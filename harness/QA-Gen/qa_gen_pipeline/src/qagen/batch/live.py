"""Strict live executor for approved compiled-selection batch jobs.

The batch registry owns plan approval and diagnostic dry-runs.  This module
adds an explicit local-provider execution sidecar without widening that
registry's diagnostic-state contract.  It is intentionally fail-closed:
missing runtime approval, a missing typed resource, an unsupported viewer
capability, or an invalid provider result stops before a candidate can become
stage-eligible.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import secrets
from typing import Any, Mapping

import httpx

from ..contracts import canonical_json, digest, make_envelope, require_digest, verify_envelope
from ..errors import GatePendingError, InputError, StaleInputError
from ..measurement import (
    MeasurementContractError,
    SceneInventoryError,
    build_measurement_request,
    load_scene_inventory_by_digest,
    load_v32_scene_inventory_batch,
    selector_policy,
    validate_brief_target_spec_output,
    validate_measurement_request,
)
from ..paths import WorkspacePaths, require_within, safe_component
from ..provider_science_bridge import (
    ProviderScienceBridgeError,
    prepare_compiled_selection_science_bridge,
    verify_attested_provider_handoff,
)
from ..providers import ProviderAdapterError, build_provider_adapters, load_provider_runtime
from ..resource_paths import PackageResourceError, config_path, config_root
from ..storage.atomic import atomic_write_json, hash_file, read_json
from ..storage.ledger import UsageBudget, append_event
from ..verification import (
    FeedbackPolicyError,
    GroundTruthRegistry,
    GroundTruthRegistryError,
    LockedFeedbackPolicy,
    QuestionContractError,
    load_locked_feedback_policy,
    load_plan_locked_ground_truth_registry,
    load_question_text_policy,
    validate_candidate_question_contract,
    validate_question_coverage,
)
from ..workers import (
    ProviderNotConfiguredError,
    WorkerExecutor,
    WorkerRequest,
    WorkerResult,
    load_verified_routing_policy,
    validate_verification_result,
)
from .registry import BatchError, load_batch_plan, load_batch_run, validate_executor_build_runtime


_BASE_STAGES = ("brief_builder", "author", "truth_builder", "evidence_worker", "verifier")
_ALL_STAGES = (*_BASE_STAGES, "rewriter")
_LIVE_KIND = "compiled_selection_batch_live_execution"
_LIVE_SCHEMA_VERSION = 1
_ROUTING_ARTIFACT_KIND = "qagen_local_preproduction_routing"
_MAX_ROUTING_ARTIFACTS = 64
_MAX_ROUTING_ARTIFACT_BYTES = 1024 * 1024
_ALLOWED_STRATEGIES = frozenset({"preserve_intent", "reauthor_on_asset"})
_BRIEF_PROJECTION_VERSION = 1
_BRIEF_SELECTOR_CANDIDATE_LIMIT = 24
_BRIEF_PROJECTION_MAX_BYTES = 16 * 1024
_BRIEF_PROJECTION_FORBIDDEN_KEYS = frozenset(
    {
        "asset_path",
        "asset_sha256",
        "backend_selector_map",
        "coordinate_identity",
        "data_url",
        "question",
        "reference_answer",
        "selector_catalog",
        "selector_index",
        "task_id",
        "truth",
    }
)
_ALLOWED_LIVE_JOB_STATUSES = frozenset(
    {
        "queued",
        "retry_pending",
        "blocked_runtime",
        "failed_final",
        "verification_failed",
        "verification_complete_pending_human_review",
        "revision_generated_pending_reverification",
    }
)
_ALLOWED_LIVE_EXECUTION_STATUSES = frozenset(
    {
        "ready_for_live_execution",
        "live_running",
        "live_blocked_runtime",
        "live_execution_incomplete",
        "live_complete_pending_human_review",
    }
)
_FORBIDDEN_ROOT_KEYS = frozenset(
    {
        "answer_fields",
        "answer_key",
        "answer_keys",
        "asset_path",
        "backend_selector_map",
        "compute_packages",
        "mask_asset",
        "primary_asset",
        "question_id",
        "raw_path",
        "reference_answer",
        "scene_inventory",
        "selector_catalog",
        "selector_index",
        "source_row",
        "task_id",
    }
)


class LiveBatchError(BatchError):
    """A live batch execution cannot proceed under its immutable locks."""


def _plan_ground_truth_registry(paths: WorkspacePaths, plan: Mapping[str, Any]) -> GroundTruthRegistry:
    """Resolve the private, approved answer registry bound by this batch plan."""

    try:
        return load_plan_locked_ground_truth_registry(plan, workspace_root=paths.root)
    except GroundTruthRegistryError as exc:
        raise LiveBatchError("compiled-selection batch plan has no valid locked ground-truth registry") from exc


def _run_directory(paths: WorkspacePaths, run_id: str) -> Path:
    return paths.selection_batches / "compiled-runs" / "runs" / safe_component(run_id, "compiled selection batch run id")


def _live_root(paths: WorkspacePaths, run_id: str) -> Path:
    return _run_directory(paths, run_id) / "private" / "live-execution"


def _live_state_path(paths: WorkspacePaths, run_id: str) -> Path:
    return _live_root(paths, run_id) / "state.json"


def _relative(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.resolve().relative_to(paths.root.resolve()).as_posix()
    except ValueError as exc:
        raise LiveBatchError("live execution path escapes its workspace") from exc


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveBatchError(f"{label} must be an object")
    return dict(value)


def _require_mutable_mapping(value: Any, label: str) -> dict[str, Any]:
    """Return a state-owned mapping rather than the defensive read copy."""

    if not isinstance(value, dict):
        raise LiveBatchError(f"{label} must be a mutable object")
    return value


def _assert_projection_safe(value: Any) -> None:
    """Reject legacy identifiers and raw paths before any provider call."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise LiveBatchError("live worker projection has a non-string key")
            if key.casefold() in _FORBIDDEN_ROOT_KEYS:
                raise LiveBatchError(f"live worker projection leaks forbidden field: {key}")
            _assert_projection_safe(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_projection_safe(nested)


def _stage_template(role: str, reference: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "status": "planned",
        "attempts": 0,
        "agent_reference": deepcopy(dict(reference)),
        "output": None,
        "usage": {"token_in": 0, "token_out": 0, "actual_cost_usd": 0.0, "cost_accounting": "not_invoked"},
        "usage_digest": None,
        "event_digest": None,
    }


def _live_job(plan_job: Mapping[str, Any]) -> dict[str, Any]:
    job_id = plan_job.get("job_id")
    strategy = plan_job.get("generation_strategy")
    refs = plan_job.get("agent_references")
    if (
        not isinstance(job_id, str)
        or safe_component(job_id, "batch job id") != job_id
        or strategy not in _ALLOWED_STRATEGIES
        or not isinstance(refs, Mapping)
        or set(refs) != set(_ALL_STAGES)
    ):
        raise LiveBatchError("approved batch plan has an invalid live job contract")
    return {
        "job_id": job_id,
        "plan_job_digest": digest(dict(plan_job)),
        "target_leaf": plan_job.get("target_leaf"),
        "generation_strategy": strategy,
        "status": "queued",
        "failure": None,
        "stages": {role: _stage_template(role, _require_mapping(refs[role], f"{role} agent reference")) for role in _ALL_STAGES},
        "artifacts": {"candidate": None, "evidence": None, "verification": None, "revision": None},
    }


def _new_live_state(paths: WorkspacePaths, *, run_id: str, batch_run: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    body = batch_run.get("body")
    plan_body = plan.get("body")
    if not isinstance(body, Mapping) or not isinstance(plan_body, Mapping):
        raise LiveBatchError("batch run or batch plan body is malformed")
    jobs = plan_body.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise LiveBatchError("approved batch plan has no jobs")
    run_ref = body.get("plan")
    approval_ref = body.get("approval")
    if not isinstance(run_ref, Mapping) or not isinstance(approval_ref, Mapping):
        raise LiveBatchError("batch run is missing its immutable plan approval bindings")
    return {
        "schema_version": _LIVE_SCHEMA_VERSION,
        "batch_run": {
            "run_id": run_id,
            "plan_path": run_ref.get("path"),
            "plan_digest": run_ref.get("content_digest"),
            "approval_path": approval_ref.get("path"),
            "approval_digest": approval_ref.get("content_digest"),
        },
        "status": "ready_for_live_execution",
        "runtime": None,
        "budget": None,
        "jobs": [_live_job(_require_mapping(job, "batch plan job")) for job in jobs],
        "resume_count": 0,
    }


def _write_live_state(paths: WorkspacePaths, *, run_id: str, body: Mapping[str, Any], status: str) -> dict[str, Any]:
    root = _live_root(paths, run_id)
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    state = make_envelope(
        kind=_LIVE_KIND,
        identifier=f"{safe_component(run_id, 'compiled selection batch run id')}-live",
        status=status,
        body=dict(body),
        producer="qagen/0.1.0:compiled_selection_batch_live",
    )
    atomic_write_json(_live_state_path(paths, run_id), state, private=True)
    return state


def _plan_job_map(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    body = _require_mapping(plan.get("body"), "batch plan body")
    jobs = body.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise LiveBatchError("batch plan jobs are malformed")
    result: dict[str, dict[str, Any]] = {}
    for raw in jobs:
        job = _require_mapping(raw, "batch plan job")
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or job_id in result:
            raise LiveBatchError("batch plan has duplicate or invalid job ids")
        # The route strategy comes from the compiled selection row.  Do not
        # derive it from access_track, asset type, or a viewer hint here.
        if job.get("generation_strategy") not in _ALLOWED_STRATEGIES:
            raise LiveBatchError("batch plan generation_strategy is not the approved row strategy")
        result[job_id] = job
    return result


def _job_feedback_policy(
    job: Mapping[str, Any],
    *,
    require_for_live: bool = False,
) -> LockedFeedbackPolicy | None:
    """Load the exact family lock persisted with one approved batch job."""

    locks = _require_mapping(job.get("lock_bindings"), "batch job locks")
    target_leaf = job.get("target_leaf")
    knowledge = _require_mapping(locks.get("knowledge"), "batch job knowledge lock")
    if not isinstance(target_leaf, str) or not target_leaf:
        raise LiveBatchError("batch job has no target leaf for feedback-policy validation")
    raw_policy = locks.get("feedback_policy")
    if raw_policy is None:
        if require_for_live:
            raise LiveBatchError("live generation requires a locked feedback policy for every batch job")
        return None
    try:
        policy = load_locked_feedback_policy(
            raw_policy,
            leaf_id=target_leaf,
            expected_knowledge_snapshot_digest=str(knowledge.get("content_digest")),
        )
    except FeedbackPolicyError as exc:
        raise LiveBatchError("batch job feedback-policy lock is invalid") from exc
    expected = {
        "id": policy.question_family_id,
        "kind": policy.question_family_kind,
        "feedback_policy_digest": policy.policy.content_digest,
    }
    if job.get("question_family") != expected:
        raise LiveBatchError("batch job question family differs from its locked feedback policy")
    return policy


def _require_live_feedback_policies(plan: Mapping[str, Any]) -> None:
    """A live provider cannot choose a family after the plan approval gate."""

    for job in _plan_job_map(plan).values():
        _job_feedback_policy(job, require_for_live=True)


def _validate_live_body(
    paths: WorkspacePaths,
    *,
    run_id: str,
    body: Any,
    batch_run: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    value = _require_mapping(body, "live execution body")
    required = {"schema_version", "batch_run", "status", "runtime", "budget", "jobs", "resume_count"}
    if set(value) != required or value.get("schema_version") != _LIVE_SCHEMA_VERSION:
        raise LiveBatchError("live execution state fields are invalid")
    if value.get("status") not in _ALLOWED_LIVE_EXECUTION_STATUSES:
        raise LiveBatchError("live execution status is invalid")
    run_ref = _require_mapping(value.get("batch_run"), "live batch run reference")
    expected_run = _require_mapping(batch_run.get("body"), "batch run body")
    expected_plan = _require_mapping(expected_run.get("plan"), "batch run plan reference")
    expected_approval = _require_mapping(expected_run.get("approval"), "batch run approval reference")
    if run_ref != {
        "run_id": run_id,
        "plan_path": expected_plan.get("path"),
        "plan_digest": expected_plan.get("content_digest"),
        "approval_path": expected_approval.get("path"),
        "approval_digest": expected_approval.get("content_digest"),
    }:
        raise StaleInputError("live execution state is bound to a different batch run")
    if not isinstance(value.get("resume_count"), int) or value["resume_count"] < 0:
        raise LiveBatchError("live execution resume_count is invalid")
    jobs = value.get("jobs")
    if not isinstance(jobs, list):
        raise LiveBatchError("live execution jobs are invalid")
    plan_jobs = _plan_job_map(plan)
    if len(jobs) != len(plan_jobs):
        raise StaleInputError("live execution jobs differ from the approved plan")
    seen: set[str] = set()
    for raw in jobs:
        job = _require_mapping(raw, "live execution job")
        required_job = {"job_id", "plan_job_digest", "target_leaf", "generation_strategy", "status", "failure", "stages", "artifacts"}
        if set(job) != required_job:
            raise LiveBatchError("live execution job fields are invalid")
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or job_id in seen or job_id not in plan_jobs:
            raise LiveBatchError("live execution job id is invalid")
        seen.add(job_id)
        plan_job = plan_jobs[job_id]
        if (
            job.get("plan_job_digest") != digest(plan_job)
            or job.get("target_leaf") != plan_job.get("target_leaf")
            or job.get("generation_strategy") != plan_job.get("generation_strategy")
        ):
            raise StaleInputError("live execution job differs from its approved plan")
        if job.get("status") not in _ALLOWED_LIVE_JOB_STATUSES:
            raise LiveBatchError("live execution job status is invalid")
        if job.get("failure") is not None and not isinstance(job.get("failure"), Mapping):
            raise LiveBatchError("live execution job failure is invalid")
        artifacts = _require_mapping(job.get("artifacts"), "live execution artifacts")
        if set(artifacts) != {"candidate", "evidence", "verification", "revision"}:
            raise LiveBatchError("live execution artifacts are invalid")
        for artifact_name, artifact in artifacts.items():
            if artifact is None:
                continue
            artifact_ref = _require_mapping(artifact, f"live {artifact_name} artifact")
            if set(artifact_ref) != {"path", "content_digest"}:
                raise LiveBatchError(f"live {artifact_name} artifact reference is invalid")
            require_digest(artifact_ref.get("content_digest"), f"live {artifact_name} artifact digest")
        stages = _require_mapping(job.get("stages"), "live execution stages")
        if set(stages) != set(_ALL_STAGES):
            raise LiveBatchError("live execution job stages are incomplete")
        for role in _ALL_STAGES:
            stage = _require_mapping(stages[role], f"live {role} stage")
            stage_fields = {"role", "status", "attempts", "agent_reference", "output", "usage", "usage_digest", "event_digest"}
            if set(stage) != stage_fields or stage.get("role") != role:
                raise LiveBatchError(f"live {role} stage fields are invalid")
            if not isinstance(stage.get("attempts"), int) or stage["attempts"] < 0:
                raise LiveBatchError(f"live {role} attempt count is invalid")
            reference = _require_mapping(stage.get("agent_reference"), f"live {role} agent reference")
            if reference.get("role") != role:
                raise LiveBatchError(f"live {role} agent reference is invalid")
            if stage.get("status") not in {"planned", "running", "completed", "failed", "waiting_human_review"}:
                raise LiveBatchError(f"live {role} stage status is invalid")
            output = stage.get("output")
            if output is not None:
                output_ref = _require_mapping(output, f"live {role} output")
                if set(output_ref) != {"path", "content_digest"}:
                    raise LiveBatchError(f"live {role} output reference is invalid")
                require_digest(output_ref.get("content_digest"), f"live {role} output digest")
                output_path = require_within(paths.root, paths.root / str(output_ref.get("path")), "live output")
                if not output_path.is_file() or output_path.is_symlink() or digest(read_json(output_path)) != output_ref["content_digest"]:
                    raise StaleInputError(f"live {role} output changed")
                execution_input_digest = reference.get("execution_input_digest")
                if not isinstance(execution_input_digest, str):
                    raise LiveBatchError(f"live {role} completed stage lacks its execution input digest")
                require_digest(execution_input_digest, f"live {role} execution input digest")
    return value


def load_live_execution(paths: WorkspacePaths, *, run_id: str) -> dict[str, Any] | None:
    """Load a live sidecar and revalidate its plan/run/output bindings."""

    canonical_run_id = safe_component(run_id, "compiled selection batch run id")
    path = _live_state_path(paths, canonical_run_id)
    if not path.exists():
        return None
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise LiveBatchError("live execution state must be an object")
    try:
        state = verify_envelope(raw, kind=_LIVE_KIND)
    except InputError as exc:
        raise LiveBatchError("live execution state envelope is invalid") from exc
    if state.get("status") != _require_mapping(state.get("body"), "live execution body").get("status"):
        raise StaleInputError("live execution envelope status differs from its body")
    batch_run = load_batch_run(paths, canonical_run_id)
    plan_ref = _require_mapping(batch_run["body"].get("plan"), "batch run plan reference")
    plan = load_batch_plan(paths, str(plan_ref.get("path")))
    _validate_live_body(paths, run_id=canonical_run_id, body=state.get("body"), batch_run=batch_run, plan=plan)
    return state


def _batch_schema_resource(name: str) -> dict[str, Any]:
    try:
        value = read_json(config_path("schemas", name))
    except (PackageResourceError, OSError, InputError) as exc:
        raise LiveBatchError(f"required live batch schema is unavailable: {name}") from exc
    if not isinstance(value, Mapping):
        raise LiveBatchError(f"required live batch schema is invalid: {name}")
    return deepcopy(dict(value))


def _batch_schema_registry() -> dict[str, Mapping[str, Any]]:
    """Bind live workers to TargetSpec and MeasurementRequest dependencies."""

    target_spec = _batch_schema_resource("target_spec.schema.json")
    candidate = _batch_schema_resource("candidate.schema.json")
    required_candidate = candidate.get("required")
    properties_candidate = candidate.get("properties")
    if not isinstance(required_candidate, list) or not isinstance(properties_candidate, Mapping):
        raise LiveBatchError("candidate schema cannot be tightened for the live batch")
    if "target_spec_digest" not in required_candidate:
        required_candidate.append("target_spec_digest")
    properties_candidate["target_spec_digest"] = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}

    evidence = _batch_schema_resource("viewer_evidence_packet.schema.json")
    definitions = evidence.get("definitions")
    if not isinstance(definitions, Mapping):
        raise LiveBatchError("viewer evidence schema cannot be tightened for the live batch")
    for variant in ("provider", "fixture"):
        variant_schema = definitions.get(variant)
        if not isinstance(variant_schema, Mapping):
            raise LiveBatchError("viewer evidence schema has no expected variant")
        required = variant_schema.get("required")
        properties = variant_schema.get("properties")
        if not isinstance(required, list) or not isinstance(properties, Mapping):
            raise LiveBatchError("viewer evidence schema variant is malformed")
        if "measurement_request_digest" not in required:
            required.append("measurement_request_digest")
        properties["measurement_request_digest"] = {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}

    return {
        # The shared routing policy intentionally keeps its generic task-brief
        # identifier for legacy runs.  Only compiled-selection execution binds
        # that output position to a concrete inventory-backed TargetSpec.
        "qagen://schemas/task-brief/output/v1": target_spec,
        "qagen://schemas/target-spec/v1": target_spec,
        "qagen://schemas/candidate/v1": candidate,
        "qagen://schemas/viewer-evidence-packet/v1": evidence,
        "qagen://schemas/author-truth-receipt/v1": {
            "type": "object",
            "required": [
                "artifact_kind",
                "candidate_digest",
                "measurement_request_digest",
                "work_item_id",
                "truth",
                "state_transition",
            ],
            "properties": {
                "artifact_kind": {"const": "batch_author_truth_receipt"},
                "candidate_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                "measurement_request_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                "work_item_id": {"type": "string", "minLength": 1},
                "truth": {"type": "object", "minProperties": 1},
                "state_transition": {"type": "null"},
            },
            "additionalProperties": False,
        }
    }


def _locked_routing_artifact(paths: WorkspacePaths, *, routing: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the exact V32 routing artifact approved by the batch plan.

    The plan routing lock identifies a content-addressed local-preproduction
    artifact, not a generic model-routing file.  Looking up that artifact
    first prevents a same-shaped pilot policy from being selected merely
    because it happens to be present in the package.
    """

    expected_digest = routing.get("content_digest")
    require_digest(expected_digest, "batch routing digest")
    root = paths.root / "preproduction"
    if root.is_symlink() or not root.is_dir():
        raise LiveBatchError("approved batch routing artifact is unavailable")
    candidates = sorted(root.glob("**/routing.json"), key=lambda item: item.as_posix())
    if len(candidates) > _MAX_ROUTING_ARTIFACTS:
        raise LiveBatchError("approved batch routing artifact search is too broad")
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            require_within(paths.root, candidate, "batch routing artifact")
            if candidate.stat().st_size > _MAX_ROUTING_ARTIFACT_BYTES:
                continue
            raw = read_json(candidate)
            artifact = verify_envelope(raw, kind=_ROUTING_ARTIFACT_KIND)
        except (InputError, OSError):
            continue
        if artifact.get("content_digest") != expected_digest:
            continue
        body = _require_mapping(artifact.get("body"), "approved batch routing artifact")
        required = {
            "scope",
            "selection_batch_digest",
            "source_routing_digest",
            "source_runtime_digest",
            "agent_references",
            "credential_transport",
            "production_eligible",
            "release_eligible",
        }
        if (
            set(body) != required
            or body.get("scope") != "local_preproduction_only"
            or body.get("production_eligible") is not False
            or body.get("release_eligible") is not False
            or not isinstance(body.get("agent_references"), list)
        ):
            raise LiveBatchError("approved batch routing artifact has an invalid local-preproduction boundary")
        require_digest(body.get("source_routing_digest"), "approved batch routing source digest")
        require_digest(body.get("source_runtime_digest"), "approved batch runtime source digest")
        matches.append(body)
    if not matches:
        raise LiveBatchError("batch routing lock does not resolve an approved V32 routing artifact")
    # Local-preproduction artifacts are immutable but may be copied into a
    # later fresh-lock assembly.  The lock binds the routing body digest, so
    # identical verified bodies are equivalent; treating those copies as an
    # ambiguity would block a valid resumed local run.  A non-identical body
    # remains fail-closed even if a digest collision were somehow introduced.
    selected = matches[0]
    if any(candidate != selected for candidate in matches[1:]):
        raise LiveBatchError("batch routing lock resolves conflicting approved V32 routing artifacts")
    return selected


def _locked_routing_config_path(paths: WorkspacePaths, *, routing: Mapping[str, Any]) -> Path:
    """Find one packaged routing config by the raw digest locked in V32."""

    artifact = _locked_routing_artifact(paths, routing=routing)
    source_digest = str(artifact["source_routing_digest"])
    try:
        agents_root = (config_root() / "agents").resolve()
    except PackageResourceError as exc:
        raise LiveBatchError("approved V32 routing configuration resource is unavailable") from exc
    if not agents_root.is_dir():
        raise LiveBatchError("approved V32 routing configuration resource is unavailable")
    matches: list[Path] = []
    for candidate in sorted(agents_root.glob("model_routing.*.yaml"), key=lambda item: item.name):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(agents_root)
        except ValueError:
            continue
        try:
            if hash_file(candidate) == source_digest:
                matches.append(candidate)
        except OSError:
            continue
    if len(matches) != 1:
        raise LiveBatchError("batch routing lock does not resolve exactly one packaged V32 routing configuration")
    return matches[0]


def _routing_policy(paths: WorkspacePaths, plan: Mapping[str, Any]):
    body = _require_mapping(plan.get("body"), "batch plan body")
    routing = _require_mapping(body.get("routing"), "batch routing lock")
    try:
        policy_path = _locked_routing_config_path(paths, routing=routing)
        policy = load_verified_routing_policy(
            policy_path,
            schema_registry=_batch_schema_registry(),
        )
    except InputError as exc:
        raise LiveBatchError("batch routing policy does not match its approved lock") from exc
    plan_routes = routing.get("roles")
    if not isinstance(plan_routes, list) or len(plan_routes) != len(_ALL_STAGES):
        raise LiveBatchError("batch routing role bindings are malformed")
    observed: set[str] = set()
    for route in plan_routes:
        value = _require_mapping(route, "batch routing role")
        role = value.get("role")
        if role not in _ALL_STAGES or role in observed:
            raise LiveBatchError("batch routing roles are incomplete")
        observed.add(str(role))
        configured = policy.role(str(role))
        models = configured.get("allowed_models")
        if (
            value.get("provider") != configured.get("provider")
            or not isinstance(models, list)
            or models != [value.get("model")]
        ):
            raise LiveBatchError(f"batch route does not match approved provider policy: {role}")
    if observed != set(_ALL_STAGES):
        raise LiveBatchError("batch routing omits a required live worker")
    author = policy.role("author")
    verifier = policy.role("verifier")
    if author.get("provider") == verifier.get("provider") or author.get("allowed_models") == verifier.get("allowed_models"):
        raise LiveBatchError("batch author and verifier routes are not independent")
    return policy


def _usage_budget(plan: Mapping[str, Any], policy: Any) -> UsageBudget:
    body = _require_mapping(plan.get("body"), "batch plan body")
    budget = _require_mapping(body.get("budget"), "batch budget")
    count = body.get("job_count")
    retry_policy = _require_mapping(body.get("retry_policy"), "batch retry policy")
    retries = retry_policy.get("max_retries_per_stage")
    if not isinstance(count, int) or count < 1 or not isinstance(retries, int) or retries < 0:
        raise LiveBatchError("batch budget or retry policy is invalid")
    total_tokens = budget.get("total_max_tokens")
    total_cost = budget.get("total_max_cost_usd")
    if not isinstance(total_tokens, int) or total_tokens < 1 or not isinstance(total_cost, (int, float)) or total_cost < 0:
        raise LiveBatchError("batch total budget is invalid")
    multiplier = count * (retries + 1)
    roles: dict[str, dict[str, Any]] = {}
    for role in _ALL_STAGES:
        route = policy.role(role)
        roles[role] = {
            "max_tokens": multiplier * int(route["max_tokens"]),
            "max_cost_usd": multiplier * float(route["max_cost_usd"]),
        }
    return UsageBudget.from_value(
        {
            "total_max_tokens": total_tokens,
            "total_max_cost_usd": float(total_cost),
            "roles": roles,
        }
    )


_NATIVE_TO_VIEWER_OPERATION = {
    "inspect": "render",
    "isolate": "render",
    "measure_distance": "measure_distance",
    "measure_dihedral": "measure_dihedral",
    "bbox": "bbox",
    "slice": "slice",
}


def _live_scene_inventory(
    paths: WorkspacePaths,
    *,
    job: Mapping[str, Any],
    inventory_batch: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the exact inventory locked through an approved batch receipt.

    A live run never selects a receipt or inventory by directory contents,
    timestamp, or a best-effort scene match.  The immutable plan supplies one
    approved inventory-batch receipt; its unique ready result for the plan's
    slot and scene reference supplies the one inventory content digest.
    """

    locks = _require_mapping(job.get("lock_bindings"), "batch job locks")
    viewer = _require_mapping(locks.get("viewer"), "batch viewer lock")
    scene_ref = viewer.get("anonymous_scene_ref")
    slot_digest = job.get("slot_digest")
    target_leaf = job.get("target_leaf")
    if not isinstance(scene_ref, str) or not isinstance(target_leaf, str):
        raise LiveBatchError("batch job lacks an immutable scene inventory binding")
    require_digest(slot_digest, "batch job slot digest")
    if not isinstance(inventory_batch, Mapping):
        raise LiveBatchError("approved plan has no inventory batch lock")
    receipt = inventory_batch.get("batch_receipt")
    expected_digest = inventory_batch.get("content_digest")
    if not isinstance(receipt, Mapping):
        raise LiveBatchError("approved inventory batch receipt binding is malformed")
    receipt_path = receipt.get("path")
    receipt_digest = receipt.get("content_digest")
    if (
        not isinstance(receipt_path, str)
        or not isinstance(expected_digest, str)
        or not isinstance(receipt_digest, str)
        or receipt_digest != expected_digest
    ):
        raise LiveBatchError("approved inventory batch receipt binding is stale")
    try:
        batch = load_v32_scene_inventory_batch(
            paths,
            receipt_path,
            expected_content_digest=expected_digest,
        )
    except SceneInventoryError as exc:
        raise LiveBatchError("approved inventory batch receipt is unavailable") from exc
    results = batch["body"].get("results")
    if not isinstance(results, list):
        raise LiveBatchError("approved inventory batch results are malformed")
    matches = [
        result
        for result in results
        if isinstance(result, Mapping)
        and result.get("status") == "ready"
        and result.get("selection_slot_digest") == slot_digest
        and result.get("anonymous_scene_ref") == scene_ref
    ]
    if len(matches) != 1:
        raise LiveBatchError("approved inventory batch has no exact current slot binding")
    selected_result = matches[0]
    inventory_digest = selected_result.get("inventory_digest")
    source_record_digest = selected_result.get("source_record_digest")
    if not isinstance(inventory_digest, str) or not isinstance(source_record_digest, str):
        raise LiveBatchError("approved inventory batch slot result is malformed")
    try:
        selected = load_scene_inventory_by_digest(paths, inventory_digest)
    except SceneInventoryError as exc:
        raise LiveBatchError("approved inventory digest is unavailable or ambiguous") from exc
    body = _require_mapping(selected["body"], "scene inventory body")
    if (
        body.get("selection_slot_digest") != slot_digest
        or body.get("anonymous_scene_ref") != scene_ref
        or body.get("target_leaf") != target_leaf
        or body.get("source_record_digest") != source_record_digest
    ):
        raise LiveBatchError("approved inventory does not match the immutable job binding")
    return {
        "inventory": body,
        "inventory_digest": require_digest(selected.get("content_digest"), "scene inventory digest"),
        "source_record_digest": require_digest(source_record_digest, "scene inventory source record digest"),
        "anonymous_scene_ref": str(body["anonymous_scene_ref"]),
        "target_leaf": str(body["target_leaf"]),
    }


def _brief_projection_entry(entry: Any, *, fields: tuple[str, ...], identifier: str) -> dict[str, Any]:
    """Project one anonymous selector entry through a closed field allowlist."""

    if not isinstance(entry, Mapping) or not set(fields).issubset(entry):
        raise LiveBatchError("scene inventory selector entry is malformed")
    value = entry.get(identifier)
    if not isinstance(value, str) or not value:
        raise LiveBatchError("scene inventory selector entry lacks its stable identifier")
    return {field: entry[field] for field in fields}


def _evenly_spaced_candidates(
    entries: Any,
    *,
    fields: tuple[str, ...],
    identifier: str,
    limit: int = _BRIEF_SELECTOR_CANDIDATE_LIMIT,
) -> tuple[int, list[dict[str, Any]]]:
    """Return a deterministic bounded sample without copying the full catalog."""

    if not isinstance(entries, list) or limit < 1:
        raise LiveBatchError("scene inventory selector catalog is malformed")
    count = len(entries)
    if count == 0:
        return 0, []
    if count <= limit:
        positions = range(count)
    elif limit == 1:
        positions = (0,)
    else:
        # Integer arithmetic avoids platform-dependent floating-point rounding.
        positions = tuple((index * (count - 1)) // (limit - 1) for index in range(limit))
    sampled = [_brief_projection_entry(entries[index], fields=fields, identifier=identifier) for index in positions]
    if len({item[identifier] for item in sampled}) != len(sampled):
        raise LiveBatchError("scene inventory selector candidate sample is not unique")
    return count, sampled


def _evenly_spaced_scalar_candidates(values: Any, *, expected_type: type, label: str) -> tuple[int, list[Any]]:
    if not isinstance(values, list) or any(not isinstance(value, expected_type) for value in values):
        raise LiveBatchError(f"BriefBuilder inventory {label} values are malformed")
    count = len(values)
    if count <= _BRIEF_SELECTOR_CANDIDATE_LIMIT:
        positions = range(count)
    elif _BRIEF_SELECTOR_CANDIDATE_LIMIT == 1:
        positions = (0,)
    else:
        positions = tuple(
            (index * (count - 1)) // (_BRIEF_SELECTOR_CANDIDATE_LIMIT - 1)
            for index in range(_BRIEF_SELECTOR_CANDIDATE_LIMIT)
        )
    return count, [values[index] for index in positions]


def _assert_brief_projection_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or key.casefold() in _BRIEF_PROJECTION_FORBIDDEN_KEYS:
                raise LiveBatchError("BriefBuilder inventory projection leaks a host-only field")
            _assert_brief_projection_safe(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_brief_projection_safe(nested)


def _brief_inventory_projection(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact, deterministic selector interface for BriefBuilder.

    The full structural inventory remains private and is revalidated when a
    TargetSpec and MeasurementRequest bind selectors.  BriefBuilder receives
    only stable, anonymous selector candidates plus complete counts and the
    reviewed operation/type matrix; it never receives a full catalog, page
    index, backend selector map, coordinates, or asset identities.
    """

    inventory = _require_mapping(binding.get("inventory"), "live scene inventory")
    catalog = _require_mapping(inventory.get("selector_catalog"), "live scene selector catalog")
    target_leaf = binding.get("target_leaf")
    if not isinstance(target_leaf, str) or target_leaf != inventory.get("target_leaf"):
        raise LiveBatchError("BriefBuilder inventory target leaf is invalid")
    selector_types = inventory.get("selector_types")
    if not isinstance(selector_types, list) or not selector_types or not all(isinstance(item, str) for item in selector_types):
        raise LiveBatchError("BriefBuilder inventory selector types are invalid")
    try:
        operations = selector_policy(target_leaf)
    except MeasurementContractError as exc:
        raise LiveBatchError("BriefBuilder inventory selector policy is unavailable") from exc

    component_count, components = _evenly_spaced_candidates(
        catalog.get("components"),
        fields=("component_id", "chain_id"),
        identifier="component_id",
    )
    residue_count, residues = _evenly_spaced_candidates(
        catalog.get("residues"),
        fields=("residue_id", "component_id", "residue_number", "insertion_code"),
        identifier="residue_id",
    )
    atom_count, atoms = _evenly_spaced_candidates(
        catalog.get("atoms"),
        fields=("atom_id", "residue_id", "element"),
        identifier="atom_id",
    )
    mask_label_count, mask_labels = _evenly_spaced_scalar_candidates(
        catalog.get("mask_labels"), expected_type=int, label="mask labels"
    )
    axis_count, axes = _evenly_spaced_scalar_candidates(catalog.get("axes"), expected_type=str, label="axes")
    shape_count, volume_shape = _evenly_spaced_scalar_candidates(
        catalog.get("volume_shape"), expected_type=int, label="volume shape"
    )
    layer_count, layers = _evenly_spaced_scalar_candidates(catalog.get("layers"), expected_type=str, label="layers")
    projection = {
        "projection_version": _BRIEF_PROJECTION_VERSION,
        "scene_inventory_digest": binding.get("inventory_digest"),
        "source_record_digest": binding.get("source_record_digest"),
        "anonymous_scene_ref": binding.get("anonymous_scene_ref"),
        "target_leaf": target_leaf,
        "selector_types": sorted(selector_types),
        "allowed_native_operations": [
            {"operation": operation, "selector_types": list(types)}
            for operation, types in sorted(operations.items())
        ],
        "selector_summary": {
            "sampling": "evenly_spaced_stable_sample_v1",
            "candidate_limit_per_category": _BRIEF_SELECTOR_CANDIDATE_LIMIT,
            "counts": {
                "components": component_count,
                "residues": residue_count,
                "atoms": atom_count,
                "mask_labels": mask_label_count,
                "axes": axis_count,
                "volume_shape": shape_count,
                "layers": layer_count,
            },
            "candidates": {"components": components, "residues": residues, "atoms": atoms},
            "mask_labels": mask_labels,
            "axes": axes,
            "volume_shape": volume_shape,
            "layers": layers,
        },
    }
    _assert_brief_projection_safe(projection)
    try:
        projected_bytes = len(canonical_json(projection))
    except Exception as exc:
        raise LiveBatchError("BriefBuilder inventory projection cannot be canonicalized") from exc
    if projected_bytes > _BRIEF_PROJECTION_MAX_BYTES:
        raise LiveBatchError("BriefBuilder inventory projection exceeds its byte cap")
    return projection


def _validate_target_viewer_operations(target_spec: Mapping[str, Any], *, allowed_operations: Any) -> None:
    native_operation = target_spec.get("native_operation")
    required_viewer_operation = _NATIVE_TO_VIEWER_OPERATION.get(native_operation)
    if required_viewer_operation is None or not isinstance(allowed_operations, list):
        raise LiveBatchError("TargetSpec native operation cannot be fulfilled by the controlled viewer")
    if required_viewer_operation not in set(allowed_operations):
        raise LiveBatchError("TargetSpec native operation is not covered by the approved viewer lock")


def _approved_target_spec(
    *,
    brief: WorkerResult,
    binding: Mapping[str, Any],
    plan_digest: str,
    viewer_operations: Any,
) -> dict[str, Any]:
    try:
        checked = validate_brief_target_spec_output(
            brief.output_contract,
            inventory=_require_mapping(binding.get("inventory"), "live scene inventory"),
            source_record_digest=str(binding.get("source_record_digest")),
            scene_inventory_digest=str(binding.get("inventory_digest")),
            anonymous_scene_ref=str(binding.get("anonymous_scene_ref")),
            candidate_plan_digest=plan_digest,
            target_leaf=str(binding.get("target_leaf")),
        )
    except MeasurementContractError as exc:
        raise LiveBatchError("BriefBuilder TargetSpec is invalid or not bound to the approved inventory") from exc
    _validate_target_viewer_operations(checked, allowed_operations=viewer_operations)
    return checked


def _measurement_request_for_candidate(
    *,
    brief: WorkerResult,
    candidate: WorkerResult,
    binding: Mapping[str, Any],
    plan_digest: str,
    viewer_operations: Any,
    job_id: str,
) -> dict[str, Any]:
    target = _approved_target_spec(
        brief=brief,
        binding=binding,
        plan_digest=plan_digest,
        viewer_operations=viewer_operations,
    )
    request_id = f"measurement-{safe_component(job_id, 'batch job id')}-{candidate.output_contract_digest.removeprefix('sha256:')[:12]}"
    try:
        request = build_measurement_request(
            target,
            candidate_digest=candidate.output_contract_digest,
            request_id=request_id,
        )
        return validate_measurement_request(request, target_spec=target)
    except MeasurementContractError as exc:
        raise LiveBatchError("cannot bind a MeasurementRequest to the approved TargetSpec") from exc


def _root_input(
    *,
    run_id: str,
    plan: Mapping[str, Any],
    job: Mapping[str, Any],
    bridge: Mapping[str, Any],
) -> dict[str, Any]:
    locks = _require_mapping(job.get("lock_bindings"), "batch job locks")
    viewer = _require_mapping(locks.get("viewer"), "batch viewer lock")
    ground_truth_record = _require_mapping(job.get("ground_truth_record"), "batch job ground-truth record")
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or bridge.get("work_item_id") != job_id:
        raise LiveBatchError("provider science bridge is not bound to the selected batch job")
    root = {
        "artifact_kind": "qagen_compiled_selection_live_root_input",
        "run_id": run_id,
        "work_item_id": job_id,
        "batch_plan_digest": plan.get("content_digest"),
        "rubric_lock_digest": locks["config"].get("content_digest"),
        "job": {
            "job_id": job_id,
            "target_leaf": job.get("target_leaf"),
            "generation_strategy": job.get("generation_strategy"),
            "lock_digests": {
                "config": locks["config"].get("content_digest"),
                "seed": locks["seed"].get("content_digest"),
                "knowledge": locks["knowledge"].get("content_digest"),
                "viewer": viewer.get("content_digest"),
            },
            "viewer": {
                "anonymous_scene_ref": viewer.get("anonymous_scene_ref"),
                "allowed_operations": list(viewer.get("allowed_operations", [])),
            },
            "ground_truth_record": deepcopy(ground_truth_record),
        },
        "provider_science_bridge": deepcopy(dict(bridge)),
    }
    feedback_policy = _job_feedback_policy(job)
    if feedback_policy is not None:
        root["job"]["question_family"] = {
            "id": feedback_policy.question_family_id,
            "kind": feedback_policy.question_family_kind,
            "feedback_policy_digest": feedback_policy.policy.content_digest,
            "allowed_gt_source_types": sorted(feedback_policy.policy.allowed_gt_source_types),
        }
    _assert_projection_safe(root)
    for value, label in (
        (root["batch_plan_digest"], "batch plan digest"),
        (root["rubric_lock_digest"], "batch rubric/config lock"),
        (root["job"]["lock_digests"]["knowledge"], "batch knowledge lock"),
        (root["job"]["lock_digests"]["viewer"], "batch viewer lock"),
    ):
        require_digest(value, label)
    return root


def _author_visual_bridge(root: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project V32's transient render attachments without exposing the full bridge.

    The author needs images plus receipt bindings, while the full science
    bridge also contains seed and knowledge context intended for other typed
    stages.  This small projection is never persisted as a durable artifact;
    the HTTP adapter removes ``data_url`` values from the text prompt and uses
    them only as multimodal attachments.
    """

    bridge = root.get("provider_science_bridge")
    if not isinstance(bridge, Mapping) or bridge.get("artifact_kind") != "approved_local_provider_science_bridge":
        return None
    evidence = bridge.get("viewer_evidence")
    if not isinstance(evidence, Mapping):
        raise LiveBatchError("V32 author visual bridge lacks viewer evidence")
    rendered = evidence.get("rendered_views")
    if not isinstance(rendered, list) or len(rendered) < 2:
        raise LiveBatchError("V32 author visual bridge lacks the required render attachments")
    required_evidence = {
        "anonymous_scene_ref",
        "viewer_profile_digest",
        "handoff_digest",
        "receipt_digests",
        "rendered_views",
    }
    if not required_evidence.issubset(evidence):
        raise LiveBatchError("V32 author visual bridge has incomplete viewer bindings")
    for field in ("viewer_profile_digest", "handoff_digest"):
        require_digest(evidence.get(field), f"V32 author visual bridge {field}")
    receipts = evidence.get("receipt_digests")
    if not isinstance(receipts, list) or len(receipts) < 2:
        raise LiveBatchError("V32 author visual bridge receipt bindings are malformed")
    for receipt in receipts:
        require_digest(receipt, "V32 author visual bridge receipt digest")
    projected_views: list[dict[str, Any]] = []
    for view in rendered:
        if not isinstance(view, Mapping) or set(view) != {
            "operation",
            "receipt_digest",
            "render_digest",
            "mime_type",
            "data_url",
        }:
            raise LiveBatchError("V32 author visual bridge view is malformed")
        if view.get("mime_type") != "image/png" or not isinstance(view.get("data_url"), str):
            raise LiveBatchError("V32 author visual bridge has an invalid render attachment")
        require_digest(view.get("receipt_digest"), "V32 author visual bridge view receipt digest")
        require_digest(view.get("render_digest"), "V32 author visual bridge view render digest")
        projected_views.append(deepcopy(dict(view)))
    return {
        "artifact_kind": "qagen_author_visual_bridge",
        "schema_version": 1,
        "viewer_evidence": {
            "anonymous_scene_ref": evidence["anonymous_scene_ref"],
            "viewer_profile_digest": evidence["viewer_profile_digest"],
            "handoff_digest": evidence["handoff_digest"],
            "receipt_digests": deepcopy(receipts),
            "rendered_views": projected_views,
        },
    }


def _stage_input(
    *,
    role: str,
    run_id: str,
    job_id: str,
    root_input: Mapping[str, Any],
    dependencies: Mapping[str, WorkerResult],
    inventory_binding: Mapping[str, Any],
    revision_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = _require_mapping(root_input, "live root input")
    job = _require_mapping(root.get("job"), "live root job")
    plan_digest = str(root.get("batch_plan_digest"))
    viewer = _require_mapping(job.get("viewer"), "live root viewer")
    if role == "brief_builder":
        payload = {
            "artifact_kind": "qagen_worker_input",
            "stage": role,
            "run_id": run_id,
            "work_item_id": job_id,
            "root_input": deepcopy(dict(root_input)),
            "scene_inventory_catalog": _brief_inventory_projection(inventory_binding),
            "dependencies": {},
        }
        return payload

    brief = dependencies.get("brief_builder")
    if brief is None:
        raise LiveBatchError("live stage requires a completed TargetSpec")
    target = _approved_target_spec(
        brief=brief,
        binding=inventory_binding,
        plan_digest=plan_digest,
        viewer_operations=viewer.get("allowed_operations"),
    )
    if role == "author":
        # The author receives the reviewed target, not a full inventory or
        # any host backend selector map. It cannot choose a replacement. V32
        # render attachments are kept in a separate minimal projection so the
        # provider adapter cannot recover the full bridge from author input.
        payload: dict[str, Any] = {
            "artifact_kind": "qagen_author_target_spec_input",
            "stage": role,
            "run_id": run_id,
            "work_item_id": job_id,
            "target_spec_digest": digest(target),
            "target_spec": deepcopy(target),
            "author_context": {
                "batch_plan_digest": plan_digest,
                "rubric_lock_digest": root.get("rubric_lock_digest"),
                "config_digest": job.get("lock_digests", {}).get("config"),
                "knowledge_snapshot_digest": job.get("lock_digests", {}).get("knowledge"),
                "viewer_lock_digest": job.get("lock_digests", {}).get("viewer"),
                "anonymous_scene_ref": target["anonymous_scene_ref"],
                "allowed_operations": list(viewer.get("allowed_operations", [])),
                "ground_truth_record": deepcopy(_require_mapping(job.get("ground_truth_record"), "live root ground-truth record")),
                **(
                    {"question_family": deepcopy(dict(job["question_family"]))}
                    if isinstance(job.get("question_family"), Mapping)
                    else {}
                ),
            },
        }
        visual_bridge = _author_visual_bridge(root)
        if visual_bridge is not None:
            payload["visual_bridge"] = visual_bridge
        return payload

    payload: dict[str, Any] = {
        "artifact_kind": "qagen_worker_input",
        "stage": role,
        "run_id": run_id,
        "work_item_id": job_id,
        "root_input": deepcopy(dict(root_input)),
        "dependencies": {
            name: {
                "output_contract_digest": result.output_contract_digest,
                "output_contract": deepcopy(dict(result.output_contract)),
            }
            for name, result in sorted(dependencies.items())
        },
    }
    if role in {"truth_builder", "evidence_worker"}:
        candidate = dependencies.get("author")
        if candidate is None:
            raise LiveBatchError("measurement stage requires an authored candidate")
        request = _measurement_request_for_candidate(
            brief=brief,
            candidate=candidate,
            binding=inventory_binding,
            plan_digest=plan_digest,
            viewer_operations=viewer.get("allowed_operations"),
            job_id=job_id,
        )
        payload["measurement_request"] = request
        payload["measurement_request_digest"] = digest(request)
    if revision_receipt is not None:
        payload["revision_receipt"] = deepcopy(dict(revision_receipt))
    return payload


def _stage_request(
    policy: Any,
    *,
    role: str,
    run_id: str,
    job_id: str,
    input_contract: Mapping[str, Any],
) -> WorkerRequest:
    route = policy.role(role)
    models = route.get("allowed_models")
    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], str):
        raise LiveBatchError(f"batch policy route for {role} is malformed")
    return WorkerRequest(
        role=role,
        provider=str(route["provider"]),
        model=models[0],
        input_contract=input_contract,
        max_tokens=int(route["max_tokens"]),
        max_cost_usd=float(route["max_cost_usd"]),
        run_id=run_id,
        work_item_id=job_id,
    )


_PROMPT_BUDGET_FIELDS = {
    "estimated_input_tokens",
    "schema_output_reservation",
    "dynamic_total_reservation",
    "api_max_tokens",
    "prompt_digest",
}


def _prompt_reservation(
    adapter: Any,
    request: WorkerRequest,
) -> tuple[WorkerRequest, dict[str, Any]]:
    """Narrow a request before ledger reservation and return a safe receipt.

    Provider adapters own prompt rendering, including multimodal attachment
    accounting.  The live executor deliberately sees only the resulting
    numeric budget and prompt digest; it cannot persist prompt content.
    """

    budget_method = getattr(adapter, "prompt_budget", None)
    reserve_method = getattr(adapter, "with_prompt_reservation", None)
    if not callable(budget_method) or not callable(reserve_method):
        raise LiveBatchError("approved provider adapter has no prompt-budget contract")
    raw_budget = budget_method(request)
    values = {field: getattr(raw_budget, field, None) for field in _PROMPT_BUDGET_FIELDS}
    if set(values) != _PROMPT_BUDGET_FIELDS:
        raise LiveBatchError("provider prompt budget fields are incomplete")
    for field in (
        "estimated_input_tokens",
        "schema_output_reservation",
        "dynamic_total_reservation",
        "api_max_tokens",
    ):
        if not isinstance(values[field], int) or isinstance(values[field], bool) or values[field] < 1:
            raise LiveBatchError("provider prompt budget has an invalid numeric field")
    require_digest(values["prompt_digest"], "provider prompt budget digest")
    if (
        values["dynamic_total_reservation"] > request.max_tokens
        or values["dynamic_total_reservation"]
        != values["estimated_input_tokens"] + values["schema_output_reservation"]
        or values["api_max_tokens"] > values["schema_output_reservation"]
    ):
        raise LiveBatchError("provider prompt budget exceeds its approved route cap")
    narrowed = reserve_method(request)
    if not isinstance(narrowed, WorkerRequest):
        raise LiveBatchError("provider prompt reservation did not return a worker request")
    if (
        narrowed.role != request.role
        or narrowed.provider != request.provider
        or narrowed.model != request.model
        or narrowed.input_contract_digest != request.input_contract_digest
        or narrowed.max_cost_usd != request.max_cost_usd
        or narrowed.run_id != request.run_id
        or narrowed.work_item_id != request.work_item_id
        or narrowed.max_tokens != values["dynamic_total_reservation"]
    ):
        raise LiveBatchError("provider prompt reservation changed immutable request bindings")
    receipt = {field: values[field] for field in sorted(_PROMPT_BUDGET_FIELDS)}
    receipt.update(
        {
            "role": request.role,
            "work_item_id": request.work_item_id,
            "input_contract_digest": request.input_contract_digest,
            "output_capacity": values["api_max_tokens"],
            "max_cost_usd": request.max_cost_usd,
        }
    )
    return narrowed, receipt


def _validate_runtime_preflight(
    *,
    budget: UsageBudget,
    receipts: list[Mapping[str, Any]],
) -> None:
    """Apply global and BriefBuilder caps before any transport can begin."""

    if not receipts:
        raise LiveBatchError("live runtime preflight has no selected jobs")
    total_tokens = 0
    total_cost = 0.0
    role_tokens = 0
    role_cost = 0.0
    for receipt in receipts:
        if receipt.get("role") != "brief_builder":
            raise LiveBatchError("live runtime preflight includes a non-BriefBuilder receipt")
        tokens = receipt.get("dynamic_total_reservation")
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 1:
            raise LiveBatchError("live runtime preflight has an invalid token reservation")
        total_tokens += tokens
        role_tokens += tokens
        # The cost bound remains the locked role maximum.  Prompt-size
        # compaction is not a pricing model and must never lower the ledger's
        # conservative monetary reservation without an approved rate card.
        cost = receipt.get("max_cost_usd")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or float(cost) < 0:
            raise LiveBatchError("live runtime preflight has an invalid cost reservation")
        total_cost += float(cost)
        role_cost += float(cost)
    if role_tokens > budget.role_max_tokens["brief_builder"] or role_cost > budget.role_max_cost_usd["brief_builder"]:
        raise LiveBatchError("live runtime preflight exceeds the BriefBuilder budget")
    if total_tokens > budget.total_max_tokens or total_cost > budget.total_max_cost_usd:
        raise LiveBatchError("live runtime preflight exceeds the global batch budget")


def _prepare_initial_live_jobs(
    paths: WorkspacePaths,
    *,
    run_id: str,
    plan: Mapping[str, Any],
    plan_jobs: Mapping[str, Mapping[str, Any]],
    jobs: list[dict[str, Any]],
    adapters: Mapping[tuple[str, str], Any],
    policy: Any,
    budget: UsageBudget,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Materialize every selected BriefBuilder input before live transport.

    This is intentionally a no-network gate.  It proves that every locked
    bridge, inventory, root input, and rendered BriefBuilder prompt fits its
    route and batch budgets before even the first provider invocation.
    """

    plan_body = _require_mapping(plan.get("body"), "batch plan body")
    inventory_batch = plan_body.get("inventory_batch")
    prepared: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    for job in jobs:
        job_id = str(job["job_id"])
        plan_job = plan_jobs.get(job_id)
        if not isinstance(plan_job, Mapping):
            raise LiveBatchError("live runtime preflight cannot resolve a planned job")
        bridge, handoff = prepare_compiled_selection_science_bridge(
            paths,
            batch_plan_digest=str(plan["content_digest"]),
            selection_batch_digest=str(plan_body["selection_batch"]["content_digest"]),
            job=plan_job,
        )
        inventory_binding = _live_scene_inventory(paths, job=plan_job, inventory_batch=inventory_batch)
        root = _root_input(run_id=run_id, plan=plan, job=plan_job, bridge=bridge)
        input_contract = _stage_input(
            role="brief_builder",
            run_id=run_id,
            job_id=job_id,
            root_input=root,
            dependencies={},
            inventory_binding=inventory_binding,
        )
        request = _stage_request(
            policy,
            role="brief_builder",
            run_id=run_id,
            job_id=job_id,
            input_contract=input_contract,
        )
        adapter = adapters.get((request.provider, request.model))
        if adapter is None:
            raise LiveBatchError("live runtime preflight has no approved BriefBuilder adapter")
        narrowed, receipt = _prompt_reservation(adapter, request)
        prepared[job_id] = {
            "bridge": bridge,
            "handoff": handoff,
            "inventory_binding": inventory_binding,
            "root": root,
            "brief_request": narrowed,
        }
        receipts.append(receipt)
    _validate_runtime_preflight(budget=budget, receipts=receipts)
    return prepared, sorted(receipts, key=lambda item: str(item["work_item_id"]))


def _handoff_binding(paths: WorkspacePaths, bridge: Mapping[str, Any], handoff: Mapping[str, Any]) -> dict[str, Any]:
    try:
        verified = verify_attested_provider_handoff(paths, handoff, bridge=bridge)
    except ProviderScienceBridgeError as exc:
        raise LiveBatchError("controlled viewer handoff is no longer valid") from exc
    body = _require_mapping(verified.get("body"), "viewer handoff body")
    evidence = _require_mapping(bridge.get("viewer_evidence"), "provider bridge viewer evidence")
    if body.get("artifact_kind") == "attested_v32_batch_provider_handoff":
        views = body.get("views")
        if not isinstance(views, list) or len(views) < 2:
            raise LiveBatchError("V32 controlled viewer handoff has insufficient visual evidence")
        receipt_digests: list[str] = []
        render_digests: list[str] = []
        for view in views:
            if not isinstance(view, Mapping):
                raise LiveBatchError("V32 controlled viewer handoff visual evidence is malformed")
            receipt = view.get("receipt_digest")
            render = view.get("render_digest")
            if not isinstance(receipt, str) or not isinstance(render, str):
                raise LiveBatchError("V32 controlled viewer handoff visual evidence is malformed")
            receipt_digests.append(receipt)
            render_digests.append(render)
        expected = {
            "handoff_digest": handoff.get("handoff_digest"),
            "receipt_digests": evidence.get("receipt_digests"),
            "render_digests": [
                view.get("render_digest") for view in evidence.get("rendered_views", []) if isinstance(view, Mapping)
            ],
        }
        if (
            verified.get("content_digest") != expected["handoff_digest"]
            or receipt_digests != expected["receipt_digests"]
            or render_digests != expected["render_digests"]
        ):
            raise LiveBatchError("V32 controlled viewer handoff differs from its provider bridge")
        return {
            "handoff_digest": str(expected["handoff_digest"]),
            "receipt_digests": receipt_digests,
            "render_digests": render_digests,
            "viewer_profile_digest": str(evidence.get("viewer_profile_digest")),
            "anonymous_scene_ref": str(evidence.get("anonymous_scene_ref")),
        }
    expected = {
        "handoff_digest": handoff.get("handoff_digest"),
        "receipt_digests": evidence.get("receipt_digests"),
        "render_digests": [view.get("render_digest") for view in evidence.get("rendered_views", []) if isinstance(view, Mapping)],
    }
    if (
        verified.get("content_digest") != expected["handoff_digest"]
        or body.get("receipt_digests") != expected["receipt_digests"]
        or body.get("render_digests") != expected["render_digests"]
    ):
        raise LiveBatchError("controlled viewer handoff differs from its provider bridge")
    return {
        "handoff_digest": str(expected["handoff_digest"]),
        "receipt_digests": list(expected["receipt_digests"]),
        "render_digests": list(expected["render_digests"]),
        "viewer_profile_digest": str(evidence.get("viewer_profile_digest")),
        "anonymous_scene_ref": str(evidence.get("anonymous_scene_ref")),
    }


def _validate_candidate(
    result: WorkerResult,
    *,
    root: Mapping[str, Any],
    handoff: Mapping[str, Any],
    target_spec: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
    feedback_policy: LockedFeedbackPolicy,
) -> None:
    candidate = _require_mapping(result.output_contract, "author candidate")
    job = _require_mapping(root.get("job"), "batch root job")
    ground_truth_record = _require_mapping(job.get("ground_truth_record"), "batch root ground-truth record")
    bridge = _require_mapping(root.get("provider_science_bridge"), "batch provider science bridge")
    provenance = _require_mapping(candidate.get("provenance"), "author candidate provenance")
    request = _require_mapping(candidate.get("viewer_session_request"), "author candidate viewer request")
    evidence = _require_mapping(candidate.get("attested_evidence"), "author candidate attested evidence")
    if (
        candidate.get("anonymous_scene_ref") != handoff["anonymous_scene_ref"]
        or candidate.get("target_spec_digest") != digest(dict(target_spec))
        or request.get("viewer_profile_digest") != handoff["viewer_profile_digest"]
        or set(request.get("allowed_operations", [])) != set(job.get("viewer", {}).get("allowed_operations", []))
        or provenance.get("plan_digest") != root.get("batch_plan_digest")
        or provenance.get("config_digest") != job.get("lock_digests", {}).get("config")
        or provenance.get("knowledge_snapshot_digest") != job.get("lock_digests", {}).get("knowledge")
        or provenance.get("work_item_id") != root.get("work_item_id")
        or bridge.get("work_item_id") != root.get("work_item_id")
    ):
        raise LiveBatchError("author candidate is not bound to the selected batch locks")
    reference_answer = _require_mapping(candidate.get("reference_answer"), "author candidate reference answer")
    answer_fields = target_spec.get("answer_fields")
    if not isinstance(answer_fields, list) or set(reference_answer) != set(answer_fields):
        raise LiveBatchError("author candidate answer fields do not match its approved TargetSpec")
    if evidence != {
        "handoff_digest": handoff["handoff_digest"],
        "receipt_digests": handoff["receipt_digests"],
        "render_digests": handoff["render_digests"],
    }:
        raise LiveBatchError("author candidate does not cite exactly the controlled viewer evidence")
    if job.get("question_family") != {
        "id": feedback_policy.question_family_id,
        "kind": feedback_policy.question_family_kind,
        "feedback_policy_digest": feedback_policy.policy.content_digest,
        "allowed_gt_source_types": sorted(feedback_policy.policy.allowed_gt_source_types),
    }:
        raise LiveBatchError("author candidate root input differs from its locked feedback policy")
    leaf_id = job.get("target_leaf")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise LiveBatchError("batch root job lacks its leaf id")
    try:
        policy = load_question_text_policy()
        validate_candidate_question_contract(
            candidate,
            policy=policy,
            leaf_id=leaf_id,
            require_contract=True,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=ground_truth_registry.content_digest,
            expected_ground_truth_record=ground_truth_record,
            feedback_policy=feedback_policy,
            expected_question_family_id=feedback_policy.question_family_id,
        )
    except QuestionContractError as exc:
        raise LiveBatchError("author candidate question contract is invalid") from exc


def _validate_truth(
    result: WorkerResult,
    *,
    candidate: WorkerResult,
    measurement_request: Mapping[str, Any],
    job_id: str,
    root: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
    feedback_policy: LockedFeedbackPolicy,
) -> None:
    value = _require_mapping(result.output_contract, "truth-builder receipt")
    if (
        value.get("candidate_digest") != candidate.output_contract_digest
        or value.get("measurement_request_digest") != digest(dict(measurement_request))
        or value.get("work_item_id") != job_id
    ):
        raise LiveBatchError("truth-builder receipt is not bound to the authored candidate")
    candidate_value = _require_mapping(candidate.output_contract, "authored candidate")
    job = _require_mapping(root.get("job"), "batch root job")
    ground_truth_record = _require_mapping(job.get("ground_truth_record"), "batch root ground-truth record")
    leaf_id = job.get("target_leaf")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise LiveBatchError("truth-builder root job lacks its leaf id")
    try:
        validate_candidate_question_contract(
            candidate_value,
            policy=load_question_text_policy(),
            leaf_id=leaf_id,
            require_contract=True,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=ground_truth_registry.content_digest,
            expected_ground_truth_record=ground_truth_record,
            feedback_policy=feedback_policy,
            expected_question_family_id=feedback_policy.question_family_id,
        )
    except QuestionContractError as exc:
        raise LiveBatchError("truth-builder candidate does not match the locked ground truth") from exc
    if value.get("truth") != candidate_value.get("reference_answer"):
        raise LiveBatchError("truth-builder receipt does not repeat the locked candidate reference answer")


def _validate_evidence(
    result: WorkerResult,
    *,
    candidate: WorkerResult,
    measurement_request: Mapping[str, Any],
    root: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> None:
    value = _require_mapping(result.output_contract, "viewer evidence packet")
    job = _require_mapping(root.get("job"), "batch root job")
    if (
        value.get("artifact_kind") != "provider_attested_viewer_evidence"
        or value.get("candidate_digest") != candidate.output_contract_digest
        or value.get("measurement_request_digest") != digest(dict(measurement_request))
        or value.get("viewer_profile_digest") != handoff["viewer_profile_digest"]
        or value.get("viewer_operations") != job.get("viewer", {}).get("allowed_operations")
        or value.get("host_evidence_digest") != handoff["handoff_digest"]
        or value.get("receipt_digests") != handoff["receipt_digests"]
        or value.get("work_item_id") != root.get("work_item_id")
        or "observations" in value
    ):
        raise LiveBatchError("evidence worker output is not bound to host-attested viewer evidence")


def _validate_verification(
    result: WorkerResult,
    *,
    candidate: WorkerResult,
    evidence: WorkerResult,
    root: Mapping[str, Any],
    handoff: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
    feedback_policy: LockedFeedbackPolicy,
) -> None:
    try:
        value = validate_verification_result(result.output_contract)
    except InputError as exc:
        raise LiveBatchError("verifier result is invalid") from exc
    cited = _require_mapping(value.get("attested_evidence"), "verifier attested evidence")
    if (
        value.get("artifact_kind") != "verification_result"
        or value.get("candidate_digest") != candidate.output_contract_digest
        or value.get("evidence_digest") != evidence.output_contract_digest
        or value.get("rubric_lock_digest") != root.get("rubric_lock_digest")
        or value.get("work_item_id") != root.get("work_item_id")
        or cited != {"handoff_digest": handoff["handoff_digest"], "receipt_digests": handoff["receipt_digests"]}
    ):
        raise LiveBatchError("verifier result is not bound to the candidate, evidence, rubric, and viewer handoff")
    job = _require_mapping(root.get("job"), "batch root job")
    leaf_id = job.get("target_leaf")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise LiveBatchError("verifier root job lacks its leaf id")
    try:
        validate_candidate_question_contract(
            _require_mapping(candidate.output_contract, "verifier candidate"),
            policy=load_question_text_policy(),
            leaf_id=leaf_id,
            require_contract=True,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=ground_truth_registry.content_digest,
            expected_ground_truth_record=_require_mapping(job.get("ground_truth_record"), "verifier ground-truth record"),
            feedback_policy=feedback_policy,
            expected_question_family_id=feedback_policy.question_family_id,
        )
    except QuestionContractError as exc:
        raise LiveBatchError("verifier candidate does not match the locked ground truth") from exc


def _stage_output_path(paths: WorkspacePaths, *, run_id: str, job_id: str, role: str, attempt: int) -> Path:
    """Allocate a one-shot output path for this exact worker invocation.

    Persisting an output necessarily happens before its enclosing state file can
    atomically reference it.  A nonce makes any crash-window output explicitly
    uncommitted and prevents a resumed stage from colliding with or reusing it.
    """

    if attempt < 1:
        raise LiveBatchError("live worker output has no positive attempt number")
    filename = f"{role}-{attempt:02d}-{secrets.token_hex(12)}.json"
    path = _live_root(paths, run_id) / "jobs" / safe_component(job_id, "batch job id") / "outputs" / "attempts" / filename
    if path.exists():
        raise LiveBatchError("live worker output nonce collision")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _usage_record(result: WorkerResult) -> dict[str, Any]:
    return {
        "token_in": result.token_in,
        "token_out": result.token_out,
        "actual_cost_usd": result.actual_cost_usd,
        "cost_accounting": result.cost_accounting,
    }


def _stage_success(
    paths: WorkspacePaths,
    *,
    run_id: str,
    job: dict[str, Any],
    role: str,
    result: WorkerResult,
) -> None:
    stages = _require_mutable_mapping(job.get("stages"), "live job stages")
    stage = _require_mutable_mapping(stages.get(role), f"live {role} stage")
    attempt = int(stage["attempts"])
    output_path = _stage_output_path(paths, run_id=run_id, job_id=str(job["job_id"]), role=role, attempt=attempt)
    written = atomic_write_json(output_path, dict(result.output_contract), private=True)
    if written != result.output_contract_digest:
        raise LiveBatchError("persisted worker output digest differs from the verified provider result")
    usage = _usage_record(result)
    event = append_event(
        paths,
        "compiled_selection_batch_stage_completed",
        {
            "run_id": run_id,
            "job_id": job["job_id"],
            "role": role,
            "output_contract_digest": result.output_contract_digest,
            "usage_digest": digest(usage),
        },
    )
    reference = _require_mutable_mapping(stage.get("agent_reference"), f"live {role} agent reference")
    reference.update(
        {
            "output_digest": result.output_contract_digest,
            "execution_input_digest": result.input_contract_digest,
            "tokens": {"input": result.token_in, "output": result.token_out},
            "cost_usd": result.actual_cost_usd,
            "status": "completed",
            "retry_count": attempt - 1,
        }
    )
    stage.update(
        {
            "status": "completed",
            "agent_reference": reference,
            "output": {"path": _relative(paths, output_path), "content_digest": result.output_contract_digest},
            "usage": usage,
            "usage_digest": digest(usage),
            "event_digest": event["content_digest"],
        }
    )
    artifacts = _require_mutable_mapping(job.get("artifacts"), "live job artifacts")
    if role == "author":
        artifacts["candidate"] = deepcopy(stage["output"])
    elif role == "evidence_worker":
        artifacts["evidence"] = deepcopy(stage["output"])
    elif role == "verifier":
        artifacts["verification"] = deepcopy(stage["output"])
    elif role == "rewriter":
        artifacts["revision"] = deepcopy(stage["output"])


def _stage_failure(
    paths: WorkspacePaths,
    *,
    run_id: str,
    job: dict[str, Any],
    role: str,
    error: Exception,
    max_retries: int,
) -> None:
    stages = _require_mutable_mapping(job.get("stages"), "live job stages")
    stage = _require_mutable_mapping(stages.get(role), f"live {role} stage")
    attempts = int(stage["attempts"])
    provider_error: dict[str, Any] | None = None
    if isinstance(error, ProviderAdapterError) and isinstance(error.error_receipt, Mapping):
        candidate = dict(error.error_receipt)
        expected = {"http_status", "provider_error_type", "provider_error_code", "field", "message"}
        allowed_types = {"invalid_request", "authentication", "permission", "rate_limit", "server", "unknown"}
        allowed_codes = {
            "invalid_request_error",
            "invalid_parameter",
            "unsupported_parameter",
            "context_length_exceeded",
            "model_not_found",
            "invalid_api_key",
            "rate_limit_exceeded",
            "unknown",
        }
        allowed_fields = {"thinking", "max_tokens", "messages", "content", "image_url", "response_format", "model", "unknown"}
        allowed_messages = {
            "unsupported_thinking_option",
            "invalid_max_tokens",
            "unsupported_response_format",
            "invalid_multimodal_content",
            "invalid_messages",
            "invalid_model",
            "authentication_rejected",
            "rate_limited",
            "provider_rejected_request",
        }
        if (
            set(candidate) == expected
            and isinstance(candidate["http_status"], int)
            and 100 <= candidate["http_status"] <= 599
            and candidate["provider_error_type"] in allowed_types
            and candidate["provider_error_code"] in allowed_codes
            and candidate["field"] in allowed_fields
            and candidate["message"] in allowed_messages
        ):
            provider_error = candidate
    event = append_event(
        paths,
        "compiled_selection_batch_stage_failed",
        {
            "run_id": run_id,
            "job_id": job["job_id"],
            "role": role,
            "attempt": attempts,
            "error_class": type(error).__name__,
            **({"provider_error": provider_error} if provider_error is not None else {}),
        },
    )
    reference = _require_mutable_mapping(stage.get("agent_reference"), f"live {role} agent reference")
    reference.update({"status": "failed", "retry_count": max(0, attempts - 1)})
    stage.update({"status": "failed", "agent_reference": reference, "event_digest": event["content_digest"]})
    retryable = attempts <= max_retries
    job["status"] = "retry_pending" if retryable else "failed_final"
    job["failure"] = {
        "stage": role,
        "error_class": type(error).__name__,
        "reason": str(error)[:512],
        "event_digest": event["content_digest"],
        **({"provider_error": provider_error} if provider_error is not None else {}),
    }


def _load_completed_result(paths: WorkspacePaths, stage: Mapping[str, Any]) -> WorkerResult:
    """Reconstruct a bounded result only from a persisted verified output."""

    output_ref = stage.get("output")
    reference = _require_mapping(stage.get("agent_reference"), "completed stage agent reference")
    if not isinstance(output_ref, Mapping) or stage.get("status") != "completed":
        raise LiveBatchError("required prior stage has not completed")
    path = require_within(paths.root, paths.root / str(output_ref.get("path")), "live prior output")
    value = read_json(path)
    if not isinstance(value, Mapping) or digest(dict(value)) != output_ref.get("content_digest"):
        raise StaleInputError("persisted live stage output changed")
    return WorkerResult(
        role=str(reference.get("role")),
        provider=str(reference.get("provider")),
        model=str(reference.get("model")),
        input_contract_digest=str(reference.get("execution_input_digest")),
        output_contract=dict(value),
        output_contract_digest=str(output_ref.get("content_digest")),
        fixture_only=False,
        token_in=int(_require_mapping(stage.get("usage"), "stage usage").get("token_in", 0)),
        token_out=int(_require_mapping(stage.get("usage"), "stage usage").get("token_out", 0)),
        actual_cost_usd=float(_require_mapping(stage.get("usage"), "stage usage").get("actual_cost_usd", 0.0)),
        cost_accounting=str(_require_mapping(stage.get("usage"), "stage usage").get("cost_accounting", "provider_reported")),
    )


def _revision_receipt(
    paths: WorkspacePaths,
    *,
    receipt_path: str | Path,
    run_id: str,
    job_id: str,
    candidate_digest: str,
    verifier_digest: str,
) -> dict[str, Any]:
    path = require_within(paths.root, receipt_path, "batch revision receipt")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise LiveBatchError("batch revision receipt must be an object")
    try:
        receipt = verify_envelope(value, kind="qagen_revision_receipt")
    except InputError as exc:
        raise LiveBatchError("batch revision receipt is invalid") from exc
    body = _require_mapping(receipt.get("body"), "batch revision receipt body")
    if (
        receipt.get("status") != "revise"
        or body.get("decision") != "revise"
        or body.get("actor_role") not in {"domain_expert", "human_reviewer"}
        or body.get("run_id") != run_id
        or body.get("work_item_id") != job_id
        or body.get("candidate_digest") != candidate_digest
        or body.get("verifier_output_digest") != verifier_digest
        or not isinstance(body.get("rationale"), str)
        or not body["rationale"].strip()
    ):
        raise LiveBatchError("batch revision receipt does not bind this candidate and verifier result")
    return receipt


def _next_stage(job: Mapping[str, Any], *, revision_receipt: Mapping[str, Any] | None) -> str | None:
    stages = _require_mapping(job.get("stages"), "live job stages")
    for role in _BASE_STAGES:
        if _require_mapping(stages[role], f"live {role} stage").get("status") != "completed":
            return role
    if revision_receipt is not None and _require_mapping(stages["rewriter"], "live rewriter stage").get("status") != "completed":
        return "rewriter"
    return None


def _dependencies_for_stage(paths: WorkspacePaths, job: Mapping[str, Any], role: str) -> dict[str, WorkerResult]:
    stages = _require_mapping(job.get("stages"), "live job stages")
    if role == "brief_builder":
        return {}
    brief = _load_completed_result(paths, _require_mapping(stages["brief_builder"], "brief stage"))
    if role == "author":
        return {"brief_builder": brief}
    author = _load_completed_result(paths, _require_mapping(stages["author"], "author stage"))
    base = {"brief_builder": brief, "author": author}
    if role in {"truth_builder", "evidence_worker"}:
        return base
    truth = _load_completed_result(paths, _require_mapping(stages["truth_builder"], "truth stage"))
    evidence = _load_completed_result(paths, _require_mapping(stages["evidence_worker"], "evidence stage"))
    dependencies = {**base, "truth_builder": truth, "evidence_worker": evidence}
    if role == "rewriter":
        dependencies["verifier"] = _load_completed_result(paths, _require_mapping(stages["verifier"], "verifier stage"))
    return dependencies


def _invoke_stage(
    paths: WorkspacePaths,
    *,
    executor: WorkerExecutor,
    policy: Any,
    adapters: Mapping[tuple[str, str], Any],
    run_id: str,
    state_body: dict[str, Any],
    job: dict[str, Any],
    root: Mapping[str, Any],
    inventory_binding: Mapping[str, Any],
    bridge: Mapping[str, Any],
    handoff: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
    feedback_policy: LockedFeedbackPolicy,
    role: str,
    revision_receipt: Mapping[str, Any] | None,
    max_retries: int,
    prepared_request: WorkerRequest | None = None,
) -> bool:
    stages = _require_mutable_mapping(job.get("stages"), "live job stages")
    stage = _require_mutable_mapping(stages.get(role), f"live {role} stage")
    if stage.get("status") == "completed":
        return False
    stage["attempts"] = int(stage.get("attempts", 0)) + 1
    stage["status"] = "running"
    stage_reference = _require_mutable_mapping(stage.get("agent_reference"), f"live {role} agent reference")
    stage_reference.update({"status": "running", "retry_count": int(stage["attempts"]) - 1})
    _write_live_state(paths, run_id=run_id, body=state_body, status="live_running")
    provider_invocation_started = False
    try:
        dependencies = _dependencies_for_stage(paths, job, role)
        input_contract = _stage_input(
            role=role,
            run_id=run_id,
            job_id=str(job["job_id"]),
            root_input=root,
            dependencies=dependencies,
            inventory_binding=inventory_binding,
            revision_receipt=revision_receipt if role == "rewriter" else None,
        )
        route_request = _stage_request(policy, role=role, run_id=run_id, job_id=str(job["job_id"]), input_contract=input_contract)
        adapter = adapters.get((route_request.provider, route_request.model))
        if adapter is None:
            raise LiveBatchError(f"no approved provider adapter is bound to {role}")
        if prepared_request is None:
            request, _ = _prompt_reservation(adapter, route_request)
        else:
            if (
                prepared_request.role != route_request.role
                or prepared_request.provider != route_request.provider
                or prepared_request.model != route_request.model
                or prepared_request.input_contract_digest != route_request.input_contract_digest
                or prepared_request.run_id != route_request.run_id
                or prepared_request.work_item_id != route_request.work_item_id
                or prepared_request.max_cost_usd != route_request.max_cost_usd
                or prepared_request.max_tokens > route_request.max_tokens
            ):
                raise LiveBatchError("cached live prompt reservation no longer matches its stage input")
            request = prepared_request
        # This is intentionally recorded just before entering WorkerExecutor.
        # The adapter may still reject locally, so it is an invocation attempt,
        # not a claim that a remote service returned a response.
        provider_invocation_started = True
        result = executor.invoke(request, worker=adapter)
        binding = _handoff_binding(paths, bridge, handoff)
        job_viewer = _require_mapping(_require_mapping(root.get("job"), "batch root job").get("viewer"), "batch root viewer")
        plan_digest = str(root.get("batch_plan_digest"))
        if role == "brief_builder":
            _approved_target_spec(
                brief=result,
                binding=inventory_binding,
                plan_digest=plan_digest,
                viewer_operations=job_viewer.get("allowed_operations"),
            )
        elif role == "author":
            target = _approved_target_spec(
                brief=dependencies["brief_builder"],
                binding=inventory_binding,
                plan_digest=plan_digest,
                viewer_operations=job_viewer.get("allowed_operations"),
            )
            _validate_candidate(
                result,
                root=root,
                handoff=binding,
                target_spec=target,
                ground_truth_registry=ground_truth_registry,
                feedback_policy=feedback_policy,
            )
        elif role == "truth_builder":
            request = _measurement_request_for_candidate(
                brief=dependencies["brief_builder"],
                candidate=dependencies["author"],
                binding=inventory_binding,
                plan_digest=plan_digest,
                viewer_operations=job_viewer.get("allowed_operations"),
                job_id=str(job["job_id"]),
            )
            _validate_truth(
                result,
                candidate=dependencies["author"],
                measurement_request=request,
                job_id=str(job["job_id"]),
                root=root,
                ground_truth_registry=ground_truth_registry,
                feedback_policy=feedback_policy,
            )
        elif role == "evidence_worker":
            request = _measurement_request_for_candidate(
                brief=dependencies["brief_builder"],
                candidate=dependencies["author"],
                binding=inventory_binding,
                plan_digest=plan_digest,
                viewer_operations=job_viewer.get("allowed_operations"),
                job_id=str(job["job_id"]),
            )
            _validate_evidence(
                result,
                candidate=dependencies["author"],
                measurement_request=request,
                root=root,
                handoff=binding,
            )
        elif role == "verifier":
            _validate_verification(
                result,
                candidate=dependencies["author"],
                evidence=dependencies["evidence_worker"],
                root=root,
                handoff=binding,
                ground_truth_registry=ground_truth_registry,
                feedback_policy=feedback_policy,
            )
        elif role == "rewriter":
            target = _approved_target_spec(
                brief=dependencies["brief_builder"],
                binding=inventory_binding,
                plan_digest=plan_digest,
                viewer_operations=job_viewer.get("allowed_operations"),
            )
            _validate_candidate(
                result,
                root=root,
                handoff=binding,
                target_spec=target,
                ground_truth_registry=ground_truth_registry,
                feedback_policy=feedback_policy,
            )
        _stage_success(paths, run_id=run_id, job=job, role=role, result=result)
        job["failure"] = None
        _write_live_state(paths, run_id=run_id, body=state_body, status="live_running")
    except Exception as exc:
        _stage_failure(paths, run_id=run_id, job=job, role=role, error=exc, max_retries=max_retries)
        _write_live_state(paths, run_id=run_id, body=state_body, status="live_running")
    return provider_invocation_started


def _job_verifier_outcome(paths: WorkspacePaths, job: Mapping[str, Any]) -> str | None:
    stage = _require_mapping(_require_mapping(job.get("stages"), "live job stages").get("verifier"), "live verifier stage")
    if stage.get("status") != "completed":
        return None
    return _require_mapping(_load_completed_result(paths, stage).output_contract, "verification output").get("outcome")


def _runtime_block(
    paths: WorkspacePaths,
    *,
    run_id: str,
    body: dict[str, Any],
    reason: Exception,
) -> dict[str, Any]:
    event = append_event(
        paths,
        "compiled_selection_batch_live_blocked",
        {"run_id": run_id, "error_class": type(reason).__name__},
    )
    body["status"] = "live_blocked_runtime"
    prior_runtime = body.get("runtime")
    prior_network_called = isinstance(prior_runtime, Mapping) and prior_runtime.get("network_called") is True
    prior_preflight = prior_runtime.get("preflight") if isinstance(prior_runtime, Mapping) else None
    body["runtime"] = {
        "status": "blocked",
        "network_called": prior_network_called,
        "reason": str(reason)[:512],
        "event_digest": event["content_digest"],
        **({"preflight": deepcopy(dict(prior_preflight))} if isinstance(prior_preflight, Mapping) else {}),
    }
    for job in body["jobs"]:
        if job["status"] in {"queued", "retry_pending"}:
            job["status"] = "blocked_runtime"
    state = _write_live_state(paths, run_id=run_id, body=body, status="live_blocked_runtime")
    return {
        "run_id": run_id,
        "live": True,
        "status": state["status"],
        "network_called": prior_network_called,
        "execution_eligible": False,
        "generated_candidate_count": 0,
        "stage_eligible_candidate_count": 0,
        "blockers": [type(reason).__name__, str(reason)],
        "live_state_digest": state["content_digest"],
    }


def _select_jobs(body: Mapping[str, Any], *, limit: int | None) -> list[dict[str, Any]]:
    jobs = body.get("jobs")
    if not isinstance(jobs, list):
        raise LiveBatchError("live execution jobs are malformed")
    candidates: list[dict[str, Any]] = []
    for job in jobs:
        # Validate the persisted object without replacing it with the helper's
        # defensive copy.  Callers transition job status and must write those
        # transitions back into the live state body.
        _require_mapping(job, "live execution job")
        if not isinstance(job, dict):
            raise LiveBatchError("live execution job must be a mutable object")
        if job.get("status") in {"queued", "retry_pending", "blocked_runtime"}:
            candidates.append(job)
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise LiveBatchError("live execution limit must be a positive integer")
        candidates = candidates[:limit]
    return candidates


def _validate_completed_live_question_coverage(
    paths: WorkspacePaths,
    *,
    body: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Revalidate every completed author artifact as one batch portfolio."""

    jobs = body.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise LiveBatchError("live execution jobs are malformed for question coverage validation")
    if any(job.get("status") != "verification_complete_pending_human_review" for job in jobs if isinstance(job, Mapping)):
        return {"status": "pending_all_jobs"}
    if not all(isinstance(job, Mapping) for job in jobs):
        raise LiveBatchError("live execution job is malformed for question coverage validation")
    expected_leaf_ids: list[str] = []
    for job in jobs:
        leaf_id = job.get("target_leaf")
        if not isinstance(leaf_id, str) or not leaf_id:
            raise LiveBatchError("completed live candidate lacks a target leaf")
        expected_leaf_ids.append(leaf_id)
    policy = load_question_text_policy()
    candidates: list[dict[str, Any]] = []
    leaf_ids: list[str] = []
    feedback_policies: list[LockedFeedbackPolicy] | None = [] if plan is not None else None
    plan_jobs = _plan_job_map(plan) if plan is not None else None
    for raw_job in jobs:
        job = _require_mapping(raw_job, "live execution job")
        artifact = _require_mapping(
            _require_mapping(job.get("artifacts"), "live execution artifacts").get("candidate"),
            "completed live candidate artifact",
        )
        if set(artifact) != {"path", "content_digest"}:
            raise LiveBatchError("completed live candidate artifact is malformed")
        path = require_within(paths.root, paths.root / str(artifact.get("path")), "completed live candidate")
        candidate = read_json(path)
        if not isinstance(candidate, Mapping) or digest(dict(candidate)) != artifact.get("content_digest"):
            raise StaleInputError("completed live candidate changed before question coverage validation")
        leaf_id = job.get("target_leaf")
        if not isinstance(leaf_id, str) or not leaf_id:
            raise LiveBatchError("completed live candidate lacks a target leaf")
        feedback_policy: LockedFeedbackPolicy | None = None
        if plan_jobs is not None:
            job_id = job.get("job_id")
            if not isinstance(job_id, str) or job_id not in plan_jobs:
                raise LiveBatchError("completed live candidate does not resolve an approved plan job")
            plan_job = plan_jobs[job_id]
            if plan_job.get("target_leaf") != leaf_id:
                raise LiveBatchError("completed live candidate leaf differs from its approved plan job")
            feedback_policy = _job_feedback_policy(plan_job, require_for_live=True)
            if feedback_policy is None:
                raise LiveBatchError("completed live candidate has no locked feedback policy")
            expected_ground_truth_record = _require_mapping(
                plan_job.get("ground_truth_record"), "completed live candidate ground-truth record"
            )
        else:
            expected_ground_truth_record = None
        try:
            validate_candidate_question_contract(
                candidate,
                policy=policy,
                leaf_id=leaf_id,
                require_contract=True,
                ground_truth_registry=ground_truth_registry,
                expected_ground_truth_registry_digest=ground_truth_registry.content_digest,
                expected_ground_truth_record=expected_ground_truth_record,
                feedback_policy=feedback_policy,
                expected_question_family_id=(feedback_policy.question_family_id if feedback_policy is not None else None),
            )
        except QuestionContractError as exc:
            raise LiveBatchError("completed live candidate question contract is invalid") from exc
        candidates.append(dict(candidate))
        leaf_ids.append(leaf_id)
        if feedback_policies is not None:
            if feedback_policy is None:
                raise LiveBatchError("completed live candidate has no feedback-policy coverage lock")
            feedback_policies.append(feedback_policy)
    try:
        coverage = validate_question_coverage(
            candidates,
            policy=policy,
            leaf_ids=leaf_ids,
            expected_leaf_ids=expected_leaf_ids,
            feedback_policies=feedback_policies,
        )
    except QuestionContractError as exc:
        raise LiveBatchError("completed live candidate question coverage is invalid") from exc
    return {
        "status": "passed",
        "policy_digest": policy.content_digest,
        "coverage": coverage,
    }


def _resume_interrupted_jobs(paths: WorkspacePaths, *, run_id: str, body: dict[str, Any], max_retries: int) -> None:
    for job in body["jobs"]:
        stages = _require_mapping(job.get("stages"), "live job stages")
        for role, raw_stage in stages.items():
            stage = _require_mapping(raw_stage, f"live {role} stage")
            if stage.get("status") == "running":
                _stage_failure(
                    paths,
                    run_id=run_id,
                    job=job,
                    role=role,
                    error=LiveBatchError("previous process ended while this stage was running"),
                    max_retries=max_retries,
                )


def _record_provider_transport_event(
    paths: WorkspacePaths,
    *,
    run_id: str,
    state_body: dict[str, Any],
    payload: Mapping[str, Any],
) -> None:
    """Persist a sanitized provider-transport receipt before/after HTTP I/O."""

    event_name = payload.get("event")
    if event_name not in {
        "request_sent",
        "response_received",
        "response_error",
        "transport_failed",
        "response_envelope_observed",
    }:
        raise LiveBatchError("provider transport observer emitted an unknown event")
    if payload.get("run_id") != run_id:
        raise LiveBatchError("provider transport observer is bound to a different run")
    for field in ("provider", "model", "role", "work_item_id"):
        if not isinstance(payload.get(field), str) or not str(payload[field]).strip():
            raise LiveBatchError("provider transport observer event is malformed")
    if event_name == "response_error":
        receipt = payload.get("error_receipt")
        if not isinstance(receipt, Mapping) or set(receipt) != {
            "http_status",
            "provider_error_type",
            "provider_error_code",
            "field",
            "message",
        }:
            raise LiveBatchError("provider transport error receipt is malformed")
        if (
            not isinstance(receipt.get("http_status"), int)
            or not isinstance(receipt.get("provider_error_type"), str)
            or not isinstance(receipt.get("provider_error_code"), str)
            or not isinstance(receipt.get("field"), str)
            or not isinstance(receipt.get("message"), str)
        ):
            raise LiveBatchError("provider transport error receipt is malformed")
    runtime = _require_mutable_mapping(state_body.get("runtime"), "live runtime state")
    if event_name == "request_sent":
        runtime["network_called"] = True
        runtime["provider_requests_sent"] = int(runtime.get("provider_requests_sent", 0)) + 1
    event = append_event(
        paths,
        "compiled_selection_batch_provider_transport",
        {"run_id": run_id, "transport": deepcopy(dict(payload))},
    )
    runtime["last_provider_transport_event_digest"] = event["content_digest"]
    _write_live_state(paths, run_id=run_id, body=state_body, status="live_running")


def execute_live_batch(
    paths: WorkspacePaths,
    *,
    run_id: str,
    runtime_config_path: str | Path | None,
    limit: int | None = None,
    revision_receipt_path: str | Path | None = None,
    provider_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute approved jobs only through the real, externally-approved routes.

    No fallback adapter exists.  In particular, this function does not convert
    an unavailable provider into fixture output and does not stage a candidate
    for release.  A verifier pass remains pending human review.
    """

    canonical_run_id = safe_component(run_id, "compiled selection batch run id")
    batch_run = load_batch_run(paths, canonical_run_id)
    if batch_run.get("status") not in {"ready_for_diagnostic", "diagnostic_blocked", "dry_run_ready"}:
        raise LiveBatchError(f"compiled-selection batch cannot start live execution from {batch_run.get('status')}")
    plan_ref = _require_mapping(batch_run["body"].get("plan"), "batch run plan reference")
    plan = load_batch_plan(paths, str(plan_ref.get("path")))
    existing = load_live_execution(paths, run_id=canonical_run_id)
    if existing is None:
        body = _new_live_state(paths, run_id=canonical_run_id, batch_run=batch_run, plan=plan)
        _write_live_state(paths, run_id=canonical_run_id, body=body, status="ready_for_live_execution")
    else:
        body = deepcopy(_require_mapping(existing.get("body"), "live execution body"))
        body["resume_count"] = int(body["resume_count"]) + 1

    if runtime_config_path is None:
        return _runtime_block(
            paths,
            run_id=canonical_run_id,
            body=body,
            reason=GatePendingError("--provider-runtime is required for --live execution"),
        )
    try:
        # Old diagnostic-only plans remain readable, but execution must bind
        # every candidate answer to an approved private source before it can
        # construct a provider adapter or touch credentials.
        ground_truth_registry = _plan_ground_truth_registry(paths, plan)
        _require_live_feedback_policies(plan)
        policy = _routing_policy(paths, plan)
        settings = load_provider_runtime(runtime_config_path)
        executor_build = validate_executor_build_runtime(
            paths,
            plan=plan,
            runtime_config_digest=settings.config_digest,
        )
        # The callback has no request content, credentials, or response text.
        # It must commit request_sent before HTTP begins so runtime state remains
        # auditable even if transport or parsing subsequently fails.
        def transport_observer(payload: Mapping[str, Any]) -> None:
            _record_provider_transport_event(
                paths,
                run_id=canonical_run_id,
                state_body=body,
                payload=payload,
            )

        adapters = build_provider_adapters(
            policy,
            runtime_config_path=runtime_config_path,
            runtime_config_digest=settings.config_digest,
            client=provider_client,
            transport_observer=transport_observer,
        )
        budget = _usage_budget(plan, policy)
    except (InputError, ProviderNotConfiguredError) as exc:
        return _runtime_block(paths, run_id=canonical_run_id, body=body, reason=exc)

    retry_policy = _require_mapping(plan["body"].get("retry_policy"), "batch retry policy")
    max_retries = retry_policy.get("max_retries_per_stage")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise LiveBatchError("batch retry policy is invalid")
    _resume_interrupted_jobs(paths, run_id=canonical_run_id, body=body, max_retries=max_retries)
    body["status"] = "live_running"
    prior_runtime = body.get("runtime")
    prior_network_called = isinstance(prior_runtime, Mapping) and prior_runtime.get("network_called") is True
    prior_requests_sent = (
        int(prior_runtime.get("provider_requests_sent", 0))
        if isinstance(prior_runtime, Mapping) and isinstance(prior_runtime.get("provider_requests_sent", 0), int)
        else 0
    )
    body["runtime"] = {
        "status": "ready",
        "network_called": prior_network_called,
        "provider_requests_sent": prior_requests_sent,
        "runtime_config_digest": settings.config_digest,
        "endpoint_approval_digest": settings.endpoint_approval_digest,
        "routing_digest": policy.content_digest,
        **(
            {"executor_build_digest": executor_build["receipt"]["content_digest"]}
            if executor_build is not None
            else {}
        ),
    }
    body["budget"] = budget.as_dict()
    _write_live_state(paths, run_id=canonical_run_id, body=body, status="live_running")

    plan_jobs = _plan_job_map(plan)
    selected = _select_jobs(body, limit=limit)
    try:
        prepared_jobs, preflight_receipts = _prepare_initial_live_jobs(
            paths,
            run_id=canonical_run_id,
            plan=plan,
            plan_jobs=plan_jobs,
            jobs=selected,
            adapters=adapters,
            policy=policy,
            budget=budget,
        )
    except Exception as exc:
        runtime = _require_mutable_mapping(body.get("runtime"), "live runtime state")
        runtime["preflight"] = {
            "status": "failed",
            "job_count": len(selected),
            "reason": str(exc)[:512],
        }
        return _runtime_block(paths, run_id=canonical_run_id, body=body, reason=exc)
    runtime = _require_mutable_mapping(body.get("runtime"), "live runtime state")
    runtime["preflight"] = {
        "status": "passed",
        "job_count": len(preflight_receipts),
        "receipt_digest": digest(preflight_receipts),
        "receipts": preflight_receipts,
    }
    _write_live_state(paths, run_id=canonical_run_id, body=body, status="live_running")

    executor = WorkerExecutor(
        paths,
        policy=policy,
        usage_budget=budget,
        approved_work_item_count=int(plan["body"]["job_count"]) * (max_retries + 1),
        provider_adapters=adapters,
    )
    executed: list[str] = []
    provider_invocations_started = 0
    for job in selected:
        job_id = str(job["job_id"])
        # A blocked runtime can be retried after a valid endpoint approval is
        # supplied; no stage was invoked while it was blocked.
        if job["status"] == "blocked_runtime":
            job["status"] = "queued"
        try:
            prepared = prepared_jobs.get(job_id)
            if not isinstance(prepared, Mapping):
                raise LiveBatchError("live runtime preflight did not cache this selected job")
            bridge = _require_mapping(prepared.get("bridge"), "preflight provider bridge")
            handoff = _require_mapping(prepared.get("handoff"), "preflight provider handoff")
            inventory_binding = _require_mapping(prepared.get("inventory_binding"), "preflight inventory binding")
            root = _require_mapping(prepared.get("root"), "preflight root input")
            brief_request = prepared.get("brief_request")
            if not isinstance(brief_request, WorkerRequest):
                raise LiveBatchError("live runtime preflight did not cache a BriefBuilder request")
            plan_job = plan_jobs.get(job_id)
            if not isinstance(plan_job, Mapping):
                raise LiveBatchError("live execution job does not resolve an approved plan job")
            feedback_policy = _job_feedback_policy(plan_job, require_for_live=True)
            if feedback_policy is None:
                raise LiveBatchError("live execution job lacks a locked feedback policy")
        except Exception as exc:
            job["status"] = "failed_final"
            event = append_event(
                paths,
                "compiled_selection_batch_preflight_failed",
                {"run_id": canonical_run_id, "job_id": job_id, "error_class": type(exc).__name__},
            )
            job["failure"] = {
                "stage": "preflight",
                "error_class": type(exc).__name__,
                "reason": str(exc)[:512],
                "event_digest": event["content_digest"],
            }
            _write_live_state(paths, run_id=canonical_run_id, body=body, status="live_running")
            continue

        candidate_stage = _require_mapping(job["stages"].get("author"), "live author stage")
        verifier_stage = _require_mapping(job["stages"].get("verifier"), "live verifier stage")
        receipt: dict[str, Any] | None = None
        if revision_receipt_path is not None and candidate_stage.get("status") == "completed" and verifier_stage.get("status") == "completed":
            receipt = _revision_receipt(
                paths,
                receipt_path=revision_receipt_path,
                run_id=canonical_run_id,
                job_id=job_id,
                candidate_digest=str(candidate_stage["output"]["content_digest"]),
                verifier_digest=str(verifier_stage["output"]["content_digest"]),
            )
        role = _next_stage(job, revision_receipt=receipt)
        while role is not None and job["status"] not in {"failed_final", "retry_pending"}:
            if _invoke_stage(
                paths,
                executor=executor,
                policy=policy,
                adapters=adapters,
                run_id=canonical_run_id,
                state_body=body,
                job=job,
                root=root,
                inventory_binding=inventory_binding,
                bridge=bridge,
                handoff=handoff,
                ground_truth_registry=ground_truth_registry,
                feedback_policy=feedback_policy,
                role=role,
                revision_receipt=receipt,
                max_retries=max_retries,
                prepared_request=brief_request if role == "brief_builder" else None,
            ):
                provider_invocations_started += 1
            if job["status"] in {"failed_final", "retry_pending"}:
                break
            role = _next_stage(job, revision_receipt=receipt)

        outcome = _job_verifier_outcome(paths, job)
        if job["status"] not in {"failed_final", "retry_pending"}:
            if receipt is not None and _require_mapping(job["stages"]["rewriter"], "live rewriter stage").get("status") == "completed":
                # A rewritten candidate has no new evidence/verifier result.
                # It is never stage-eligible until a new controlled pass.
                job["status"] = "revision_generated_pending_reverification"
            elif outcome == "pass":
                job["status"] = "verification_complete_pending_human_review"
            elif outcome == "fail":
                job["status"] = "verification_failed"
            else:
                job["status"] = "queued"
        executed.append(job_id)
        _write_live_state(paths, run_id=canonical_run_id, body=body, status="live_running")

    statuses: dict[str, int] = {}
    for job in body["jobs"]:
        statuses[job["status"]] = statuses.get(job["status"], 0) + 1
    candidate_count = sum(1 for job in body["jobs"] if job["artifacts"]["candidate"] is not None)
    completed_count = statuses.get("verification_complete_pending_human_review", 0)
    runtime = _require_mutable_mapping(body.get("runtime"), "live runtime state")
    try:
        coverage = _validate_completed_live_question_coverage(
            paths,
            body=body,
            ground_truth_registry=ground_truth_registry,
            plan=plan,
        )
    except (LiveBatchError, StaleInputError) as exc:
        event = append_event(
            paths,
            "compiled_selection_batch_question_coverage_blocked",
            {"run_id": canonical_run_id, "error_class": type(exc).__name__},
        )
        runtime["question_coverage"] = {
            "status": "blocked",
            "reason": str(exc)[:512],
            "event_digest": event["content_digest"],
        }
        coverage = {"status": "blocked"}
    else:
        runtime["question_coverage"] = coverage
    coverage_passed = coverage.get("status") == "passed"
    body["status"] = (
        "live_complete_pending_human_review"
        if completed_count == len(body["jobs"]) and coverage_passed
        else "live_execution_incomplete"
    )
    state = _write_live_state(paths, run_id=canonical_run_id, body=body, status=body["status"])
    return {
        "run_id": canonical_run_id,
        "live": True,
        "status": state["status"],
        # A preflight failure is not a provider call.  Once a typed worker
        # invocation has started, transport may have been attempted even if
        # the adapter fails before a response is available.
        "network_called": runtime.get("network_called") is True,
        "provider_invocations_started": provider_invocations_started,
        "execution_eligible": True,
        "selected_job_count": len(selected),
        "executed_job_ids": executed,
        "job_statuses": {key: statuses[key] for key in sorted(statuses)},
        "generated_candidate_count": candidate_count,
        "stage_eligible_candidate_count": 0,
        "review_pending_candidate_count": completed_count if coverage_passed else 0,
        "live_state_path": str(_live_state_path(paths, canonical_run_id)),
        "live_state_digest": state["content_digest"],
        "release_blocker": (
            "human_domain_review_and_existing_release_run_binding_required"
            if coverage_passed
            else "question_coverage_requires_a_complete_compliant_portfolio"
        ),
    }
