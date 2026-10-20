"""Run state persistence, approval validation, and lock-aware resume."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import secrets
import threading
from typing import Any, Callable, Mapping
import unicodedata

import httpx

from ..chirality_pilot import CHIRALITY_PILOT_ADAPTER_ID, CHIRALITY_PILOT_OPERATIONS, chirality_pilot_viewer_payload
from ..config import load_compiled_config, revalidate_compiled_config
from ..contracts import digest, make_envelope, require_digest, verify_envelope
from ..errors import GatePendingError, InputError, InvalidStateError, StaleInputError
from ..paths import WorkspacePaths, require_within, safe_component
from ..plan import load_plan, load_plan_approval, validate_plan_work_items
from ..pipeline import OrchestrationResult, WorkerOrchestrator
from ..resource_paths import config_path
from ..provider_science_bridge import (
    ProviderScienceBridgeError,
    prepare_approved_chirality_science_bridge,
    verify_attested_provider_handoff,
)
from ..providers import build_provider_adapters, require_provider_multimodal_capability
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from ..viewer import load_viewer_profile, revalidate_viewer_profile
from ..verification import (
    FeedbackPolicy,
    FeedbackPolicyError,
    GroundTruthRegistry,
    GroundTruthRegistryError,
    QuestionContractError,
    load_plan_locked_ground_truth_registry,
    load_question_text_policy,
    load_workspace_feedback_policy,
    validate_candidate_question_contract,
)
from ..workers import WorkerExecutor, WorkerRequest, WorkerResult, load_verified_routing_policy, validate_verification_result


class RunError(InputError):
    """A run cannot start, resume, or accept a control-plane decision."""


_RUN_THREAD_LOCK = threading.RLock()

_PROVIDER_PILOT_PROFILE_ID = "chirality_provider_pilot"
_PROVIDER_PILOT_LEAF_ID = "chemistry.stereochemistry_chirality"
_PROVIDER_PILOT_MODE = "local_provider_pilot"


def _plan_ground_truth_registry(paths: WorkspacePaths, plan: Mapping[str, Any]) -> GroundTruthRegistry:
    """Load the sole private truth registry authorized by an immutable plan."""

    try:
        return load_plan_locked_ground_truth_registry(plan, workspace_root=paths.root)
    except GroundTruthRegistryError as exc:
        raise RunError("plan has no valid locked ground-truth registry") from exc


def _feedback_policy_for_snapshot(
    paths: WorkspacePaths,
    *,
    leaf_id: Any,
    knowledge_snapshot_digest: Any,
) -> FeedbackPolicy | None:
    """Resolve only the approved snapshot already named by a work item."""

    if not isinstance(leaf_id, str) or not leaf_id or not isinstance(knowledge_snapshot_digest, str):
        raise RunError("work item feedback-policy binding is malformed")
    try:
        return load_workspace_feedback_policy(
            paths,
            leaf_id=leaf_id,
            expected_knowledge_snapshot_digest=knowledge_snapshot_digest,
        )
    except FeedbackPolicyError as exc:
        raise RunError("work item feedback-policy snapshot is invalid") from exc


@contextmanager
def _locked_run(paths: WorkspacePaths, run_id: str):
    """Serialize state transitions for one run across local processes."""
    directory = run_directory(paths, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".state.lock"
    with _RUN_THREAD_LOCK:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


@contextmanager
def run_state_lock(paths: WorkspacePaths, run_id: str):
    """Expose the run transaction lock to sibling control-plane registries."""
    canonical_run_id = safe_component(run_id, "run id")
    with _locked_run(paths, canonical_run_id):
        yield


def _stage_dependency_digest(request: WorkerRequest, role: str) -> str:
    dependencies = request.input_contract.get("dependencies")
    if not isinstance(dependencies, Mapping):
        raise RunError("fixture worker input lacks prior-stage dependencies")
    dependency = dependencies.get(role)
    if not isinstance(dependency, Mapping):
        raise RunError(f"fixture worker input lacks required {role} dependency")
    value = dependency.get("output_contract_digest")
    require_digest(value, f"fixture {role} output digest")
    return value


class _RunFixtureAdapter:
    """Local-only typed output adapter used exclusively by a locked fixture route."""

    fixture_only = True

    def __init__(self, *, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model

    def execute(self, request: WorkerRequest) -> WorkerResult:
        root = request.input_contract.get("root_input")
        if not isinstance(root, Mapping):
            raise RunError("fixture worker input lacks control-plane root input")
        work_item = root.get("work_item")
        if not isinstance(work_item, Mapping):
            raise RunError("fixture worker input lacks an approved work item")
        leaf_id = work_item.get("leaf_id")
        work_item_id = work_item.get("work_item_id")
        viewer_profile_digest = root.get("viewer_profile_digest")
        operations = work_item.get("approved_operations")
        knowledge_digest = work_item.get("knowledge_snapshot_digest")
        if (
            not isinstance(leaf_id, str)
            or not isinstance(work_item_id, str)
            or safe_component(work_item_id, "fixture work item id") != work_item_id
            or root.get("work_item_id") != work_item_id
            or request.work_item_id != work_item_id
            or not isinstance(viewer_profile_digest, str)
            or not isinstance(operations, list)
        ):
            raise RunError("fixture worker root input is malformed")
        if request.role in {"author", "rewriter"}:
            revision_receipt = request.input_contract.get("revision_receipt")
            is_revision = request.role == "rewriter"
            output: dict[str, Any] = {
                "artifact_kind": "author_private_candidate",
                "task_id": f"fixture-generated-{request.run_id}-{work_item_id}",
                "prompt": (
                    f"Use the controlled viewer to inspect the approved fixture work item {work_item_id}"
                    f" ({'revised once' if is_revision else 'initial'})."
                ),
                "anonymous_scene_ref": f"scene://fixture/{work_item_id}",
                "viewer_session_request": {
                    "request_id": f"fixture-request-{request.run_id}-{work_item_id}{'-revision-1' if is_revision else ''}",
                    "viewer_profile_digest": viewer_profile_digest,
                    "allowed_operations": list(operations),
                },
                "reference_answer": {"fixture_answer": "controlled visual evidence required"},
                "reasoning": (
                    "Fixture-only candidate rewritten once from a persisted human revision receipt: "
                    f"{revision_receipt.get('body', {}).get('rationale', '')}"
                    if is_revision and isinstance(revision_receipt, Mapping)
                    else "Fixture-only private candidate generated from the approved work item."
                ),
                "rubric": {"fixture": True},
                "provenance": {
                    "plan_digest": root.get("plan_digest"),
                    "config_digest": root.get("config_digest"),
                    "knowledge_snapshot_digest": knowledge_digest,
                    "work_item_id": work_item_id,
                },
            }
        elif request.role == "evidence_worker":
            candidate_digest = _stage_dependency_digest(request, "author")
            output = {
                "artifact_kind": "fixture_viewer_evidence",
                "candidate_digest": candidate_digest,
                "viewer_profile_digest": viewer_profile_digest,
                "viewer_operations": list(operations),
                "observations": [
                    {
                        "operation": operation,
                        "observation": f"fixture observation for approved work item {leaf_id}",
                    }
                    for operation in operations
                ],
                "work_item_id": work_item_id,
                "state_transition": None,
            }
        elif request.role == "verifier":
            output = {
                "artifact_kind": "fixture_verification_result",
                "candidate_digest": _stage_dependency_digest(request, "author"),
                "evidence_digest": _stage_dependency_digest(request, "evidence_worker"),
                "outcome": "pass",
                "checks": [
                    {"name": "candidate_evidence_binding", "outcome": "pass"},
                    {"name": "controlled_viewer_scope", "outcome": "pass"},
                ],
                "rubric_scores": {
                    "authenticity": {"score": 2, "maximum": 2},
                    "correctness": {"score": 1, "maximum": 1},
                    "three_d_relevance": {"score": 1, "maximum": 1},
                },
                "rubric_lock_digest": root.get("rubric_lock_digest"),
                "work_item_id": work_item_id,
                "state_transition": None,
            }
        elif request.role == "truth_builder":
            output = {
                "artifact_kind": "fixture_author_truth_receipt",
                "candidate_digest": _stage_dependency_digest(request, "author"),
                "work_item_id": work_item_id,
                "state_transition": None,
            }
        else:
            output = {
                "artifact_kind": "deterministic_fixture_worker_output",
                "role": request.role,
                "work_item_id": work_item_id,
                "input_contract_digest": request.input_contract_digest,
                "state_transition": None,
            }
        return WorkerResult(
            role=request.role,
            provider=request.provider,
            model=request.model,
            input_contract_digest=request.input_contract_digest,
            output_contract=output,
            output_contract_digest=digest(output),
            fixture_only=True,
            token_in=0,
            token_out=0,
            actual_cost_usd=0.0,
        )


def _short(value: str) -> str:
    return value.split(":", 1)[-1][:12]


def run_directory(paths: WorkspacePaths, run_id: str) -> Path:
    return paths.runs / safe_component(run_id, "run id")


def _state_path(paths: WorkspacePaths, run_id: str) -> Path:
    return run_directory(paths, run_id) / "state.json"


def _write_state(paths: WorkspacePaths, run_id: str, body: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    state = make_envelope(
        kind="run_state",
        identifier=run_id,
        status=status,
        body=dict(body),
        producer="qagen/0.1.0:run_control_plane",
    )
    atomic_write_json(_state_path(paths, run_id), state, private=True)
    return state


def load_run(paths: WorkspacePaths, run_id: str) -> dict[str, Any]:
    path = _state_path(paths, run_id)
    if not path.is_file():
        raise RunError(f"run state does not exist: {run_id}")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise RunError("run state is not an object")
    return verify_envelope(value, kind="run_state")


def _validate_approval(plan: Mapping[str, Any], approval: Mapping[str, Any]) -> None:
    if approval["status"] != "approved" or approval["body"].get("decision") != "approved":
        raise GatePendingError("plan approval is not approved")
    body = plan["body"]
    locks = body.get("lock_digests")
    bound = approval["body"].get("bound_digests")
    if not isinstance(locks, Mapping) or not isinstance(bound, Mapping):
        raise RunError("plan or approval lacks lock bindings")
    if bound.get("plan_digest") != plan["content_digest"]:
        raise StaleInputError("plan approval is bound to another plan")
    for key, value in locks.items():
        if bound.get(key) != value:
            raise StaleInputError(f"plan approval lock binding changed: {key}")
    dependency_locks = body.get("dependency_locks")
    if not isinstance(dependency_locks, list) or digest(dependency_locks) != locks.get("dependency_lock_map_digest"):
        raise StaleInputError("plan approval does not bind the complete dependency lock map")


def _ordered_dependencies(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RunError(f"{label} dependency locks must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise RunError(f"{label} dependency lock is malformed")
        leaf_id = item.get("leaf_id")
        if not isinstance(leaf_id, str) or not leaf_id or leaf_id in seen:
            raise RunError(f"{label} dependency lock leaf id is invalid")
        seen.add(leaf_id)
        for key in ("seed_digest", "asset_digest", "viewer_definition_digest", "rubric_digest"):
            require_digest(item.get(key), f"{label} dependency {leaf_id}.{key}")
        knowledge = item.get("knowledge_digests")
        if not isinstance(knowledge, list) or not knowledge:
            raise RunError(f"{label} dependency {leaf_id}.knowledge_digests is malformed")
        for item_digest in knowledge:
            require_digest(item_digest, f"{label} dependency {leaf_id}.knowledge_digest")
        result.append(dict(item))
    if value != sorted(result, key=lambda item: item["leaf_id"]):
        raise RunError(f"{label} dependency lock map is not in canonical leaf order")
    return result


def resolve_plan_work_item(
    plan: Mapping[str, Any],
    *,
    knowledge_digest: str,
    requested_operations: Any,
    work_item_id: str | None = None,
) -> dict[str, Any]:
    """Resolve one candidate to exactly one approved concrete work item.

    Knowledge and viewer operations identify a leaf, but they cannot distinguish
    multiple deterministic variations of that leaf.  Such candidates must carry
    their approved ``work_item_id`` in provenance.  The legacy omission remains
    valid only while those bindings resolve to exactly one concrete work item.
    """
    require_digest(knowledge_digest, "candidate knowledge digest")
    if not isinstance(requested_operations, list) or not requested_operations or not all(
        isinstance(operation, str) and operation for operation in requested_operations
    ):
        raise RunError("candidate requested operations must be a non-empty string list")
    body = plan.get("body")
    if not isinstance(body, Mapping):
        raise RunError("generation plan is malformed")
    dependencies = {item["leaf_id"]: item for item in _ordered_dependencies(body.get("dependency_locks"), label="plan")}
    work_items = validate_plan_work_items(plan)
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for item in work_items:
        leaf_id = item.get("leaf_id")
        if not isinstance(leaf_id, str):
            raise RunError("plan work item leaf is malformed")
        dependency = dependencies.get(leaf_id)
        approved_operations = item.get("approved_operations")
        if dependency is None or not isinstance(approved_operations, list):
            raise RunError("plan work item lacks a valid dependency or operation binding")
        if knowledge_digest in dependency["knowledge_digests"] and set(requested_operations).issubset(set(approved_operations)):
            matches.append((item, dependency))
    if work_item_id is not None:
        if not isinstance(work_item_id, str) or safe_component(work_item_id, "candidate work item id") != work_item_id:
            raise RunError("candidate work item id is invalid")
        matches = [match for match in matches if match[0].get("work_item_id") == work_item_id]
        if len(matches) != 1:
            raise RunError("candidate work item id does not match one approved plan work item")
    elif len(matches) != 1:
        raise RunError("candidate must provide work_item_id when its locks match multiple approved plan work items")
    item, dependency = matches[0]
    canonical_work_item_id = item.get("work_item_id")
    leaf_id = item.get("leaf_id")
    approved_operations = item.get("approved_operations")
    if (
        not isinstance(canonical_work_item_id, str)
        or not isinstance(leaf_id, str)
        or not isinstance(approved_operations, list)
    ):
        raise RunError("plan work item is malformed")
    return {
        "work_item_id": canonical_work_item_id,
        "leaf_id": leaf_id,
        "dependency_locks": dict(dependency),
        "dependency_digest": digest(dependency),
        "approved_operations": list(approved_operations),
        "seed_selection": deepcopy(dict(item["seed_selection"])),
        "variation": deepcopy(dict(item["variation"])),
        "budget": deepcopy(dict(item["budget"])),
    }


def _validate_plan_live_locks(paths: WorkspacePaths, plan: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    body = plan["body"]
    config_path = body.get("config_path")
    viewer_path = body.get("viewer_profile_path")
    if not isinstance(config_path, str) or not isinstance(viewer_path, str):
        raise RunError("plan lacks config/viewer artifact paths")
    config = load_compiled_config(paths, config_path)
    viewer = load_viewer_profile(paths, viewer_path)
    revalidate_compiled_config(config)
    revalidate_viewer_profile(viewer)
    locks = body.get("lock_digests")
    if not isinstance(locks, Mapping):
        raise RunError("plan lacks lock digests")
    if config["content_digest"] != locks.get("config_digest"):
        raise StaleInputError("compiled config digest differs from plan lock")
    if viewer["content_digest"] != locks.get("viewer_digest"):
        raise StaleInputError("viewer profile digest differs from plan lock")
    if viewer["body"].get("definition_digest") != locks.get("viewer_definition_digest"):
        raise StaleInputError("viewer definition differs from plan lock")
    if "scene_registry_digest" in locks:
        require_digest(locks.get("scene_registry_digest"), "plan scene registry digest")
        if viewer["body"].get("scene_registry_digest") != locks.get("scene_registry_digest"):
            raise StaleInputError("viewer SceneRegistry differs from plan lock")
    try:
        validate_plan_work_items(plan)
    except InputError as exc:
        raise StaleInputError(f"plan work items are invalid: {exc}") from exc
    plan_dependencies = _ordered_dependencies(body.get("dependency_locks"), label="plan")
    if digest(plan_dependencies) != locks.get("dependency_lock_map_digest"):
        raise StaleInputError("plan dependency lock map differs from its digest")
    config_dependencies = _ordered_dependencies(config["body"].get("dependency_locks"), label="compiled config")
    config_lock_digests = config["body"].get("lock_digests")
    if not isinstance(config_lock_digests, Mapping) or digest(config_dependencies) != config_lock_digests.get(
        "dependency_lock_map_digest"
    ):
        raise StaleInputError("compiled config dependency lock map differs from its digest")
    model_routing_digest = config_lock_digests.get("model_routing_digest")
    require_digest(model_routing_digest, "compiled config model routing digest")
    if model_routing_digest != locks.get("model_routing_digest"):
        raise StaleInputError("compiled config model routing lock differs from plan lock")
    model_routing = config["body"].get("model_routing")
    if not isinstance(model_routing, Mapping) or model_routing.get("digest") != model_routing_digest:
        raise StaleInputError("compiled config model routing document differs from its lock")
    if config["body"].get("profile_mode") == "local_fixture_compile" and config_lock_digests.get(
        "fixture_routing_digest"
    ) != locks.get("fixture_routing_digest"):
        raise StaleInputError("compiled config fixture routing lock differs from plan lock")
    if config["body"].get("profile_mode") == "local_fixture_compile":
        fixture_routing = config["body"].get("fixture_routing")
        fixture_routing_digest = config_lock_digests.get("fixture_routing_digest")
        require_digest(fixture_routing_digest, "compiled fixture routing digest")
        if not isinstance(fixture_routing, Mapping) or fixture_routing.get("digest") != fixture_routing_digest:
            raise StaleInputError("compiled fixture routing document differs from its lock")
    for key in ("planning_policy_digest", "viewer_profile_requirement_digest"):
        config_value = config_lock_digests.get(key)
        if config_value is not None and config_value != locks.get(key):
            raise StaleInputError(f"compiled config {key} differs from plan lock")
    requirement = config["body"].get("viewer_profile_requirement")
    if requirement is not None:
        if not isinstance(requirement, Mapping):
            raise StaleInputError("compiled viewer profile requirement is malformed")
        required_operations = requirement.get("required_operations")
        if (
            viewer["body"].get("definition_digest") != requirement.get("definition_digest")
            or viewer["body"].get("local_fixture") is not False
            or viewer.get("status") != "attested_local_only"
            or not isinstance(required_operations, list)
            or set(viewer["body"].get("allowed_operations", [])) != set(required_operations)
            or requirement.get("require_scene_registry") is not True
            or not isinstance(viewer["body"].get("scene_registry_path"), str)
            or not isinstance(viewer["body"].get("scene_registry_digest"), str)
        ):
            raise StaleInputError("persistent chirality ViewerProfile no longer matches the approved plan")
    if config["body"].get("profile_mode") == _PROVIDER_PILOT_MODE:
        provider_runtime = config["body"].get("provider_runtime")
        provider_runtime_digest = config_lock_digests.get("provider_runtime_digest")
        require_digest(provider_runtime_digest, "compiled provider runtime digest")
        if provider_runtime_digest != locks.get("provider_runtime_digest"):
            raise StaleInputError("compiled provider runtime lock differs from plan lock")
        if not isinstance(provider_runtime, Mapping) or provider_runtime.get("digest") != provider_runtime_digest:
            raise StaleInputError("compiled provider runtime document differs from its lock")
    selected_ids = {
        leaf.get("leaf_id")
        for leaf in body.get("eligible_leaves", [])
        if isinstance(leaf, Mapping) and isinstance(leaf.get("leaf_id"), str)
    }
    expected_dependencies = [item for item in config_dependencies if item["leaf_id"] in selected_ids]
    if {item["leaf_id"] for item in expected_dependencies} != selected_ids or expected_dependencies != plan_dependencies:
        raise StaleInputError("compiled config dependency locks differ from plan locks")
    for dependency in plan_dependencies:
        if dependency["viewer_definition_digest"] != locks.get("viewer_definition_digest"):
            raise StaleInputError("selected leaf uses a viewer definition different from the plan viewer profile")
    return config, viewer


def validate_run_locks(paths: WorkspacePaths, run: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate plan approval plus every live input lock before work continues."""
    if not isinstance(run, Mapping):
        raise RunError("run state is malformed")
    body = run["body"]
    if not isinstance(body, Mapping):
        raise RunError("run state body is malformed")
    plan_path = body.get("plan_path")
    approval_path = body.get("approval_path")
    if not isinstance(plan_path, str) or not isinstance(approval_path, str):
        raise RunError("run state lacks plan/approval paths")
    plan = load_plan(paths, plan_path)
    approval = load_plan_approval(paths, approval_path)
    if plan["content_digest"] != body.get("plan_digest") or approval["content_digest"] != body.get("approval_digest"):
        raise StaleInputError("run state plan or approval digest changed")
    _validate_approval(plan, approval)
    config, viewer = _validate_plan_live_locks(paths, plan)
    plan_body = plan.get("body")
    if not isinstance(plan_body, Mapping):
        raise RunError("generation plan body is malformed")
    plan_locks = plan_body.get("lock_digests")
    if not isinstance(plan_locks, Mapping):
        raise RunError("generation plan has no lock digests")
    if body.get("config_digest") != config["content_digest"]:
        raise StaleInputError("run state config digest differs from the approved plan")
    if body.get("viewer_profile_digest") != viewer["content_digest"]:
        raise StaleInputError("run state viewer digest differs from the approved plan")
    if body.get("lock_digests") != dict(plan_locks):
        raise StaleInputError("run state lock digests differ from the approved plan")
    if body.get("eligible_leaves") != plan_body.get("eligible_leaves"):
        raise StaleInputError("run state work items differ from the approved plan")
    if body.get("work_items") != plan_body.get("work_items"):
        raise StaleInputError("run state concrete work items differ from the approved plan")
    if body.get("fixture_only") != bool(plan_body.get("fixture_only")):
        raise StaleInputError("run state fixture mode differs from the approved plan")
    if body.get("provider_pilot", False) != bool(plan_body.get("provider_pilot")):
        raise StaleInputError("run state provider-pilot mode differs from the approved plan")
    # Fixture artifacts are deliberately non-exportable and retain their
    # separate legacy compatibility path. Every other executable plan must
    # bind a human-approved source record before any worker can author a
    # candidate or claim a reference answer.
    if plan_body.get("fixture_only") is not True:
        _plan_ground_truth_registry(paths, plan)
    return plan, config, viewer


def _execution_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise RunError(f"persisted execution {label} path is invalid")
    try:
        path = require_within(root, value, f"persisted execution {label}")
    except InputError as exc:
        raise RunError(str(exc)) from exc
    if path.is_symlink() or not path.is_file():
        raise RunError(f"persisted execution {label} artifact is missing or unsafe")
    return path


def _execution_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = read_json(path)
    except (InputError, OSError) as exc:
        raise RunError(f"cannot read persisted execution {label}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise RunError(f"persisted execution {label} must be an object")
    return dict(value)


def load_persisted_execution(
    paths: WorkspacePaths,
    *,
    run: Mapping[str, Any],
    candidate_path: str | Path,
    candidate_digest: str,
    evidence_path: str | Path | None = None,
    evidence_digest: str | None = None,
) -> dict[str, Any]:
    """Resolve and revalidate one exact run-execute result from durable state."""
    require_digest(candidate_digest, "candidate digest")
    if evidence_digest is not None:
        require_digest(evidence_digest, "evidence digest")
    body = run.get("body")
    if not isinstance(body, Mapping):
        raise RunError("run state body is malformed")
    run_id = body.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise RunError("run state lacks its run id")
    raw_executions = body.get("executions")
    if not isinstance(raw_executions, Mapping):
        raise RunError("run has no persisted execution state")
    matches = [
        dict(entry)
        for entry in raw_executions.values()
        if isinstance(entry, Mapping) and entry.get("candidate_digest") == candidate_digest
    ]
    if len(matches) != 1:
        raise RunError("candidate is not bound to exactly one persisted run execution")
    entry = matches[0]
    execution_mode = entry.get("execution_mode", "local_fixture")
    if execution_mode not in {"local_fixture", _PROVIDER_PILOT_MODE}:
        raise RunError("persisted execution mode is not recognized")
    provider_execution = execution_mode == _PROVIDER_PILOT_MODE
    if bool(body.get("provider_pilot", False)) != provider_execution:
        raise RunError("persisted execution mode does not match the run's approved plan")
    if provider_execution:
        require_digest(entry.get("provider_runtime_digest"), "persisted provider runtime digest")
        if entry.get("production_eligible") is not False:
            raise RunError("persisted provider execution must remain non-production")
    run_locks = body.get("lock_digests")
    if not isinstance(run_locks, Mapping):
        raise RunError("persisted execution run lock map is invalid")
    expected_routing_digest = run_locks.get("model_routing_digest" if provider_execution else "fixture_routing_digest")
    if entry.get("routing_digest") != expected_routing_digest:
        raise RunError("persisted execution routing digest does not match the run lock")
    if provider_execution and entry.get("provider_runtime_digest") != run_locks.get("provider_runtime_digest"):
        raise RunError("persisted provider execution runtime digest does not match the run lock")
    persisted_host_evidence: dict[str, Any] | None = None
    if provider_execution:
        persisted_host_evidence = _persisted_provider_handoff_binding(
            paths,
            run_body=body,
            entry=entry,
        )
    leaf_id = entry.get("leaf_id")
    execution_key = entry.get("work_item_id")
    if (
        not isinstance(leaf_id, str)
        or not isinstance(execution_key, str)
        or safe_component(execution_key, "persisted execution work item id") != execution_key
        or raw_executions.get(execution_key) != entry
    ):
        raise RunError("persisted execution leaf binding is invalid")
    raw_work_items = body.get("work_items")
    if not isinstance(raw_work_items, list):
        raise RunError("run has no approved concrete work items")
    matching_work_items = [
        item for item in raw_work_items if isinstance(item, Mapping) and item.get("work_item_id") == execution_key
    ]
    if len(matching_work_items) != 1:
        raise RunError("persisted execution is not bound to exactly one approved work item")
    approved_work_item = matching_work_items[0]
    if (
        approved_work_item.get("leaf_id") != leaf_id
        or approved_work_item.get("dependency_digest") != entry.get("dependency_digest")
    ):
        raise RunError("persisted execution work-item binding differs from the approved plan")
    for field in (
        "dependency_digest",
        "routing_digest",
        "root_input_digest",
        "worker_result_digest",
        "execution_manifest_digest",
        "candidate_digest",
        "evidence_digest",
        "verification_digest",
    ):
        require_digest(entry.get(field), f"persisted execution {field}")
    if provider_execution:
        for field in (
            "semantic_signature",
            "host_evidence_digest",
            "receipt_chain_digest",
            "session_attestation_digest",
        ):
            require_digest(entry.get(field), f"persisted provider execution {field}")
    if entry["candidate_digest"] != candidate_digest:
        raise RunError("persisted execution candidate digest changed")
    if evidence_digest is not None and entry["evidence_digest"] != evidence_digest:
        raise RunError("persisted execution evidence digest changed")

    stages = entry.get("stage_digests")
    required_stages = {"brief_builder", "author", "truth_builder", "evidence_worker", "verifier"}
    if not isinstance(stages, Mapping) or set(stages) != required_stages:
        raise RunError("persisted execution lacks stage digests")

    def stage_digest(role: str) -> str:
        stage = stages.get(role)
        if not isinstance(stage, Mapping):
            raise RunError(f"persisted execution lacks the {role} stage")
        value = stage.get("output_contract_digest")
        require_digest(value, f"persisted execution {role} stage digest")
        return value

    if stage_digest("author") != candidate_digest:
        raise RunError("persisted author stage does not bind the candidate")
    if stage_digest("evidence_worker") != entry["evidence_digest"]:
        raise RunError("persisted evidence stage digest changed")
    if stage_digest("verifier") != entry["verification_digest"]:
        raise RunError("persisted verifier stage digest changed")

    directory = run_directory(paths, run_id)
    private = directory / "private"
    stage_outputs: dict[str, dict[str, Any]] = {}
    stage_results: dict[str, WorkerResult] = {}
    stage_files: dict[str, Path] = {}
    for role in sorted(required_stages):
        stage = stages[role]
        if not isinstance(stage, Mapping):
            raise RunError(f"persisted execution lacks the {role} stage")
        for field in ("input_contract_digest", "output_contract_digest"):
            require_digest(stage.get(field), f"persisted execution {role} {field}")
        provider = stage.get("provider")
        model = stage.get("model")
        if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
            raise RunError(f"persisted execution {role} provider/model is invalid")
        fixture_only = stage.get("fixture_only")
        if not isinstance(fixture_only, bool) or fixture_only == provider_execution:
            expected = "provider" if provider_execution else "fixture"
            raise RunError(f"persisted execution {role} is not a {expected} result")
        token_in = stage.get("token_in")
        token_out = stage.get("token_out")
        actual_cost = stage.get("actual_cost_usd")
        if (
            not isinstance(token_in, int)
            or isinstance(token_in, bool)
            or token_in < 0
            or not isinstance(token_out, int)
            or isinstance(token_out, bool)
            or token_out < 0
            or not isinstance(actual_cost, (int, float))
            or isinstance(actual_cost, bool)
            or actual_cost < 0
        ):
            raise RunError(f"persisted execution {role} usage is invalid")
        cost_accounting = stage.get("cost_accounting", "zero_cost_fixture" if fixture_only else None)
        if provider_execution and cost_accounting not in {"provider_reported", "reservation_upper_bound"}:
            raise RunError(f"persisted execution {role} provider cost accounting is invalid")
        if not provider_execution and cost_accounting != "zero_cost_fixture":
            raise RunError(f"persisted execution {role} fixture cost accounting is invalid")
        stage_file = _execution_file(private, stage.get("path"), f"{role} output")
        output = _execution_json(stage_file, f"{role} output")
        if digest(output) != stage["output_contract_digest"]:
            raise RunError(f"persisted execution {role} output digest changed")
        stage_files[role] = stage_file
        stage_outputs[role] = output
        stage_results[role] = WorkerResult(
            role=role,
            provider=provider,
            model=model,
            input_contract_digest=stage["input_contract_digest"],
            output_contract=output,
            output_contract_digest=stage["output_contract_digest"],
            fixture_only=fixture_only,
            token_in=token_in,
            token_out=token_out,
            actual_cost_usd=float(actual_cost),
            cost_accounting=str(cost_accounting),
        )
    if (
        stage_results["author"].provider == stage_results["verifier"].provider
        or stage_results["author"].model == stage_results["verifier"].model
    ):
        raise RunError("persisted execution author and verifier routes are not independent")
    candidate_file = _execution_file(private / "candidates", candidate_path, "candidate")
    recorded_candidate = _execution_file(private / "candidates", entry.get("candidate_path"), "candidate")
    if candidate_file != recorded_candidate:
        raise RunError("candidate path is not the persisted run execution output")
    recorded_evidence = _execution_file(private / "evidence", entry.get("evidence_path"), "evidence")
    if evidence_path is not None:
        supplied_evidence = _execution_file(private / "evidence", evidence_path, "evidence")
        if supplied_evidence != recorded_evidence:
            raise RunError("evidence path is not the persisted run execution output")
    verification_file = _execution_file(private / "verification", entry.get("verification_path"), "verification")
    if stage_files["author"] != recorded_candidate:
        raise RunError("persisted author stage path does not match the candidate path")
    if stage_files["evidence_worker"] != recorded_evidence:
        raise RunError("persisted evidence stage path does not match the evidence path")
    if stage_files["verifier"] != verification_file:
        raise RunError("persisted verifier stage path does not match the verification path")
    worker_result_file = _execution_file(private / "worker-results", entry.get("worker_result_path"), "worker result")
    manifest_file = _execution_file(
        private / "audit" / "executions",
        entry.get("execution_manifest_path"),
        "manifest",
    )

    candidate = _execution_json(recorded_candidate, "candidate")
    evidence = _execution_json(recorded_evidence, "evidence")
    verification = _execution_json(verification_file, "verification")
    worker_result = _execution_json(worker_result_file, "worker result")
    manifest_value = _execution_json(manifest_file, "manifest")
    try:
        manifest = verify_envelope(manifest_value, kind="run_execution_manifest")
    except InputError as exc:
        raise RunError(f"persisted execution manifest is invalid: {exc}") from exc

    if digest(candidate) != candidate_digest:
        raise RunError("persisted candidate content no longer matches its execution digest")
    if digest(evidence) != entry["evidence_digest"]:
        raise RunError("persisted evidence content no longer matches its execution digest")
    if digest(verification) != entry["verification_digest"]:
        raise RunError("persisted verifier output no longer matches its execution digest")
    if digest(worker_result) != entry["worker_result_digest"]:
        raise RunError("persisted worker result no longer matches its execution digest")
    if manifest["content_digest"] != entry["execution_manifest_digest"]:
        raise RunError("persisted execution manifest digest changed")
    if evidence.get("candidate_digest") != candidate_digest:
        raise RunError("persisted evidence does not bind the executed candidate")
    if verification.get("candidate_digest") != candidate_digest:
        raise RunError("persisted verifier output does not bind the executed candidate")
    if verification.get("evidence_digest") != entry["evidence_digest"]:
        raise RunError("persisted verifier output does not bind the executed evidence")
    provenance = candidate.get("provenance")
    if (
        not isinstance(provenance, Mapping)
        or provenance.get("work_item_id") != execution_key
        or evidence.get("work_item_id") != execution_key
        or verification.get("work_item_id") != execution_key
    ):
        raise RunError("persisted execution artifacts do not bind the selected work item")
    if provider_execution:
        plan_path = body.get("plan_path")
        if not isinstance(plan_path, str) or not plan_path:
            raise RunError("provider run has no approved plan path")
        registry = _plan_ground_truth_registry(paths, load_plan(paths, plan_path))
        feedback_policy = _feedback_policy_for_snapshot(
            paths,
            leaf_id=leaf_id,
            knowledge_snapshot_digest=provenance.get("knowledge_snapshot_digest"),
        )
        try:
            validate_candidate_question_contract(
                candidate,
                policy=load_question_text_policy(),
                leaf_id=leaf_id,
                require_contract=True,
                ground_truth_registry=registry,
                expected_ground_truth_registry_digest=registry.content_digest,
                feedback_policy=feedback_policy,
            )
        except QuestionContractError as exc:
            raise RunError("persisted provider candidate question contract is invalid") from exc
    try:
        validate_verification_result(verification, require_pass=True)
    except InputError as exc:
        raise RunError(f"persisted verifier output is not an accepted pass result: {exc}") from exc

    if provider_execution:
        assert persisted_host_evidence is not None
        if entry.get("semantic_signature") != _provider_candidate_signature(candidate):
            raise RunError("persisted provider candidate semantic signature changed")
        for other_work_item_id, other in raw_executions.items():
            if other_work_item_id == execution_key or not isinstance(other, Mapping):
                continue
            if (
                other.get("execution_mode") == _PROVIDER_PILOT_MODE
                and other.get("semantic_signature") == entry["semantic_signature"]
            ):
                raise RunError("persisted provider candidates contain a semantic duplicate")
        if candidate.get("anonymous_scene_ref") != persisted_host_evidence["anonymous_scene_ref"]:
            raise RunError("persisted provider candidate scene binding changed")
        _attested_evidence_matches(
            candidate.get("attested_evidence"),
            handoff=persisted_host_evidence,
            include_renders=True,
            label="candidate",
        )
        if (
            evidence.get("artifact_kind") != "provider_attested_viewer_evidence"
            or evidence.get("host_evidence_digest") != persisted_host_evidence["host_evidence_digest"]
            or evidence.get("receipt_digests") != persisted_host_evidence["receipt_digests"]
            or "observations" in evidence
        ):
            raise RunError("persisted provider evidence does not bind the host handoff")
        _attested_evidence_matches(
            verification.get("attested_evidence"),
            handoff=persisted_host_evidence,
            include_renders=False,
            label="verifier",
        )

    manifest_body = manifest["body"]
    expected_worker_result = {
        "artifact_kind": "provider_worker_run" if provider_execution else "fixture_worker_run",
        **(
            {
                "execution_mode": _PROVIDER_PILOT_MODE,
                "production_eligible": False,
            }
            if provider_execution
            else {}
        ),
        "run_id": run_id,
        "work_item_id": execution_key,
        "leaf_id": leaf_id,
        "dependency_digest": entry["dependency_digest"],
        "routing_digest": entry["routing_digest"],
        **({"provider_runtime_digest": entry["provider_runtime_digest"]} if provider_execution else {}),
        "root_input_digest": entry["root_input_digest"],
        **(
            {
                "semantic_signature": entry["semantic_signature"],
                "host_evidence": persisted_host_evidence,
            }
            if provider_execution
            else {}
        ),
        "status": entry.get("status"),
        "stages": dict(stages),
        **({"usage": entry.get("usage")} if provider_execution else {}),
    }
    if provider_execution:
        usage = entry.get("usage")
        expected_usage = {
            role: {
                "token_in": stages[role]["token_in"],
                "token_out": stages[role]["token_out"],
                "actual_cost_usd": stages[role]["actual_cost_usd"],
                "cost_accounting": stages[role].get("cost_accounting"),
            }
            for role in sorted(required_stages)
        }
        if usage != expected_usage:
            raise RunError("persisted provider execution usage does not match its stage records")
    expected_manifest = {
        **expected_worker_result,
        "run_id": run_id,
        "leaf_id": leaf_id,
        "dependency_digest": entry["dependency_digest"],
        "routing_digest": entry["routing_digest"],
        "root_input_digest": entry["root_input_digest"],
        "status": entry.get("status"),
        "stages": dict(stages),
        "candidate": {"path": entry.get("candidate_path"), "digest": candidate_digest},
        "evidence": {"path": entry.get("evidence_path"), "digest": entry["evidence_digest"]},
        "verification": {"path": entry.get("verification_path"), "digest": entry["verification_digest"]},
        "worker_result": {"path": entry.get("worker_result_path"), "digest": entry["worker_result_digest"]},
    }
    if any(manifest_body.get(key) != value for key, value in expected_manifest.items()):
        raise RunError("persisted execution manifest bindings changed")
    if worker_result != expected_worker_result:
        raise RunError("persisted worker result bindings changed")
    return {
        "entry": entry,
        "candidate": candidate,
        "evidence": evidence,
        "verification": verification,
        "worker_result": worker_result,
        "manifest": manifest,
        "stage_results": stage_results,
        "candidate_path": str(recorded_candidate),
        "evidence_path": str(recorded_evidence),
        "verification_path": str(verification_file),
        "worker_result_path": str(worker_result_file),
        "execution_manifest_path": str(manifest_file),
        **({"host_evidence": persisted_host_evidence} if provider_execution else {}),
    }


def _fixture_runtime_policy(config: Mapping[str, Any], plan: Mapping[str, Any]):
    """Load only the fixture policy already locked by this config and plan."""
    config_body = config.get("body")
    plan_body = plan.get("body")
    if not isinstance(config_body, Mapping) or not isinstance(plan_body, Mapping):
        raise RunError("compiled config or plan is malformed")
    if config_body.get("profile_mode") != "local_fixture_compile" or config_body.get("status") != "fixture_stage_only":
        raise GatePendingError("run execute permits only a fixture_stage_only local fixture profile")
    if config_body.get("release_eligible") is not False:
        raise RunError("fixture execution cannot use a release-eligible compiled config")
    if plan_body.get("profile_mode") != "local_fixture_compile" or plan_body.get("fixture_only") is not True:
        raise GatePendingError("run execute requires a fixture-only approved plan")
    fixture = config_body.get("fixture_routing")
    locks = plan_body.get("lock_digests")
    if not isinstance(fixture, Mapping) or not isinstance(locks, Mapping):
        raise RunError("fixture run lacks a locked fixture routing policy")
    path = fixture.get("path")
    digest_value = fixture.get("digest")
    if not isinstance(path, str):
        raise RunError("fixture routing path is invalid")
    require_digest(digest_value, "fixture routing digest")
    if digest_value != locks.get("fixture_routing_digest"):
        raise StaleInputError("fixture routing digest differs from the approved plan lock")
    policy = load_verified_routing_policy(path, expected_digest=digest_value)
    if not policy.local_fixture_only:
        raise RunError("only a local-only fixture routing policy may use the fixture adapter")
    return policy


def _provider_pilot_paths() -> tuple[Path, Path]:
    """Return the only routing/runtime documents authorized for real HTTP work."""
    return (
        config_path("agents", "model_routing.pilot.yaml"),
        config_path("agents", "provider_runtime.pilot.yaml"),
    )


def _provider_pilot_profile_path() -> Path:
    return config_path("profiles", "chirality_provider_pilot.yaml")


def _provider_runtime_policy(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    viewer: Mapping[str, Any],
):
    """Validate the narrow, digest-locked real-provider pilot authorization.

    This is intentionally separate from the fixture path.  A signed plan is
    not enough: real HTTP execution is available only to the one checked-in
    chirality profile and only while both checked-in pilot documents still
    match the plan's locks.
    """
    config_body = config.get("body")
    plan_body = plan.get("body")
    if not isinstance(config_body, Mapping) or not isinstance(plan_body, Mapping):
        raise RunError("compiled config or plan is malformed")
    if (
        config_body.get("profile_mode") != _PROVIDER_PILOT_MODE
        or config_body.get("profile_id") != _PROVIDER_PILOT_PROFILE_ID
        or config_body.get("status") != "local_provider_pilot_ready"
    ):
        raise GatePendingError("real provider execution is restricted to the approved chirality pilot profile")
    if config_body.get("release_eligible") is not False:
        raise RunError("provider pilot execution can never use a release-eligible compiled config")
    requirement = config_body.get("viewer_profile_requirement")
    expected_viewer_requirement = {
        "adapter_id": CHIRALITY_PILOT_ADAPTER_ID,
        "definition_digest": digest(chirality_pilot_viewer_payload()),
        "required_operations": list(CHIRALITY_PILOT_OPERATIONS),
        "require_non_fixture": True,
        "require_scene_registry": True,
    }
    if requirement != expected_viewer_requirement:
        raise RunError("provider pilot compiled config lacks the locked chirality viewer requirement")
    viewer_body = viewer.get("body")
    if (
        not isinstance(viewer_body, Mapping)
        or viewer.get("status") != "attested_local_only"
        or viewer_body.get("local_fixture") is not False
        or viewer_body.get("definition_digest") != expected_viewer_requirement["definition_digest"]
        or set(viewer_body.get("allowed_operations", [])) != set(CHIRALITY_PILOT_OPERATIONS)
        or not set(CHIRALITY_PILOT_OPERATIONS).issubset(set(viewer_body.get("capabilities", [])))
        or not isinstance(viewer_body.get("scene_registry_path"), str)
        or not isinstance(viewer_body.get("scene_registry_digest"), str)
        or not isinstance(viewer_body.get("anonymous_scene_ref_set_digest"), str)
    ):
        raise RunError("provider pilot requires the locked non-fixture chirality viewer with only rotate/render operations")
    require_digest(viewer_body.get("scene_registry_digest"), "provider pilot ViewerProfile scene registry digest")
    require_digest(
        viewer_body.get("anonymous_scene_ref_set_digest"),
        "provider pilot ViewerProfile anonymous scene reference lock",
    )
    profile_path = _provider_pilot_profile_path()
    profile_locks = config_body.get("input_locks")
    if config_body.get("profile_path") != str(profile_path) or not isinstance(profile_locks, list):
        raise RunError("provider pilot compiled config is not locked to the checked-in chirality profile")
    matching_profile_locks = [
        lock
        for lock in profile_locks
        if isinstance(lock, Mapping)
        and lock.get("label") == "profile"
        and lock.get("path") == str(profile_path)
        and lock.get("target") == "document"
    ]
    if len(matching_profile_locks) != 1:
        raise RunError("provider pilot profile lock is missing or ambiguous")
    require_digest(matching_profile_locks[0].get("digest"), "provider pilot profile lock digest")
    if (
        plan_body.get("profile_mode") != _PROVIDER_PILOT_MODE
        or plan_body.get("profile_id") != _PROVIDER_PILOT_PROFILE_ID
        or plan_body.get("provider_pilot") is not True
        or plan_body.get("fixture_only") is not False
    ):
        raise GatePendingError("run execute requires the approved non-production chirality provider plan")
    eligible = plan_body.get("eligible_leaves")
    if (
        not isinstance(eligible, list)
        or len(eligible) != 1
        or not isinstance(eligible[0], Mapping)
        or eligible[0].get("leaf_id") != _PROVIDER_PILOT_LEAF_ID
    ):
        raise RunError("provider pilot plan must contain exactly the approved chirality work item")
    work_items = validate_plan_work_items(plan)
    if len(work_items) not in {1, *range(5, 11)}:
        raise RunError("provider pilot plan must contain one dry-run item or a batch of 5 through 10 work items")
    for item in work_items:
        if (
            item.get("leaf_id") != _PROVIDER_PILOT_LEAF_ID
            or set(item.get("approved_operations", [])) != set(CHIRALITY_PILOT_OPERATIONS)
        ):
            raise RunError("provider pilot work item is outside the approved chirality viewer scope")

    locks = plan_body.get("lock_digests")
    config_locks = config_body.get("lock_digests")
    routing = config_body.get("model_routing")
    runtime = config_body.get("provider_runtime")
    if not isinstance(locks, Mapping) or not isinstance(config_locks, Mapping):
        raise RunError("provider pilot lacks control-plane lock digests")
    if not isinstance(routing, Mapping) or not isinstance(runtime, Mapping):
        raise RunError("provider pilot lacks locked routing or provider runtime references")
    routing_path, runtime_path = _provider_pilot_paths()
    routing_digest = config_locks.get("model_routing_digest")
    runtime_digest = config_locks.get("provider_runtime_digest")
    require_digest(routing_digest, "provider pilot model routing digest")
    require_digest(runtime_digest, "provider pilot runtime digest")
    if (
        routing.get("path") != str(routing_path)
        or routing.get("digest") != routing_digest
        or routing_digest != locks.get("model_routing_digest")
    ):
        raise StaleInputError("provider pilot model routing reference differs from the approved plan lock")
    if (
        runtime.get("path") != str(runtime_path)
        or runtime.get("digest") != runtime_digest
        or runtime_digest != locks.get("provider_runtime_digest")
    ):
        raise StaleInputError("provider pilot runtime reference differs from the approved plan lock")
    if (
        runtime.get("production_eligible") is not False
        or runtime.get("endpoint_env") != "QAGEN_UNIV_BASE_URL"
        or runtime.get("endpoint_approval_path_env") != "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH"
    ):
        raise RunError("provider pilot runtime has not locked the external HTTPS endpoint approval contract")

    policy = load_verified_routing_policy(routing_path, expected_digest=routing_digest)
    if not policy.local_pilot_only or policy.local_fixture_only:
        raise RunError("provider pilot routing is not an explicit approved-local provider policy")
    policy_document = policy.policy
    if policy_document.get("production_eligible") is not False:
        raise RunError("provider pilot routing can never be production eligible")
    author = policy.role("author")
    verifier = policy.role("verifier")
    if author.get("provider") == verifier.get("provider") or author.get("allowed_models") == verifier.get("allowed_models"):
        raise RunError("provider pilot author and verifier routes must remain independent")
    return policy, runtime_path, str(runtime_digest)


def _fixture_work_item_input(
    *,
    run_id: str,
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
    leaf: Mapping[str, Any],
    dependency: Mapping[str, Any],
    work_item: Mapping[str, Any],
) -> dict[str, Any]:
    leaf_id = leaf.get("leaf_id")
    knowledge = dependency.get("knowledge_digests")
    operations = leaf.get("required_operations")
    work_item_id = work_item.get("work_item_id")
    seed_selection = work_item.get("seed_selection")
    variation = work_item.get("variation")
    budget = work_item.get("budget")
    if (
        not isinstance(leaf_id, str)
        or not isinstance(work_item_id, str)
        or safe_component(work_item_id, "fixture work item id") != work_item_id
        or not isinstance(knowledge, list)
        or len(knowledge) != 1
        or not isinstance(operations, list)
        or not operations
        or work_item.get("leaf_id") != leaf_id
        or work_item.get("dependency_digest") != digest(dependency)
        or work_item.get("approved_operations") != operations
        or not isinstance(seed_selection, Mapping)
        or not isinstance(variation, Mapping)
        or not isinstance(budget, Mapping)
    ):
        raise RunError("fixture work item must bind one approved concrete plan item")
    return {
        "run_id": run_id,
        "work_item_id": work_item_id,
        "plan_digest": plan["content_digest"],
        "config_digest": config["content_digest"],
        "viewer_profile_digest": viewer["content_digest"],
        "rubric_lock_digest": config["body"]["lock_digests"]["rubric_lock_digest"],
        "work_item": {
            "work_item_id": work_item_id,
            "work_item_digest": digest(dict(work_item)),
            "leaf_id": leaf_id,
            "dependency_digest": digest(dependency),
            "knowledge_snapshot_digest": knowledge[0],
            "approved_operations": list(operations),
            "seed_selection": deepcopy(dict(seed_selection)),
            "variation": deepcopy(dict(variation)),
            "budget": deepcopy(dict(budget)),
        },
    }


def _provider_work_item_input(
    *,
    paths: WorkspacePaths,
    run_id: str,
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
    leaf: Mapping[str, Any],
    dependency: Mapping[str, Any],
    work_item: Mapping[str, Any],
    science_bridge: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
) -> dict[str, Any]:
    """Build the one typed root contract allowed for the HTTP chirality pilot."""
    root_input = _fixture_work_item_input(
        run_id=run_id,
        plan=plan,
        config=config,
        viewer=viewer,
        leaf=leaf,
        dependency=dependency,
        work_item=work_item,
    )
    if root_input["work_item"]["leaf_id"] != _PROVIDER_PILOT_LEAF_ID:
        raise RunError("provider pilot work item is not the approved chirality leaf")
    feedback_policy = _feedback_policy_for_snapshot(
        paths,
        leaf_id=root_input["work_item"]["leaf_id"],
        knowledge_snapshot_digest=root_input["work_item"]["knowledge_snapshot_digest"],
    )
    work_item_id = work_item.get("work_item_id")
    if not isinstance(work_item_id, str) or safe_component(work_item_id, "provider pilot work item id") != work_item_id:
        raise RunError("provider pilot work item id is invalid")
    if root_input["work_item_id"] != work_item_id:
        raise RunError("provider pilot root input does not bind the approved work item")
    raw_truth = work_item.get("ground_truth_provenance")
    if not isinstance(raw_truth, Mapping):
        raise RunError("provider pilot work item has no approved ground-truth provenance")
    truth = dict(raw_truth)
    key = (
        truth.get("source_id"),
        truth.get("source_digest"),
        truth.get("record_id"),
        truth.get("record_digest"),
    )
    if not all(isinstance(value, str) and value for value in key):
        raise RunError("provider pilot ground-truth provenance is malformed")
    record = ground_truth_registry.records.get(key)
    expected_truth = (
        {**record, "ground_truth_registry_digest": ground_truth_registry.content_digest}
        if record is not None
        else None
    )
    if truth != expected_truth:
        raise RunError("provider pilot work item ground-truth provenance is not registry-bound")
    locks = science_bridge.get("lock_bindings") if isinstance(science_bridge, Mapping) else None
    evidence = science_bridge.get("viewer_evidence") if isinstance(science_bridge, Mapping) else None
    if (
        not isinstance(locks, Mapping)
        or not isinstance(evidence, Mapping)
        or science_bridge.get("artifact_kind") != "approved_local_chirality_science_bridge"
        or science_bridge.get("leaf_id") != _PROVIDER_PILOT_LEAF_ID
        or science_bridge.get("work_item_id") != work_item_id
        or locks.get("plan_digest") != root_input["plan_digest"]
        or locks.get("config_digest") != root_input["config_digest"]
        or locks.get("knowledge_snapshot_digest") != root_input["work_item"]["knowledge_snapshot_digest"]
        or locks.get("viewer_profile_digest") != root_input["viewer_profile_digest"]
        or not isinstance(evidence.get("receipt_digests"), list)
        or not evidence.get("receipt_digests")
        or not isinstance(evidence.get("rendered_views"), list)
        or not evidence.get("rendered_views")
    ):
        raise RunError("provider pilot scientific bridge is not bound to the approved work item")
    root_input["provider_science_bridge"] = deepcopy(dict(science_bridge))
    root_input["ground_truth_provenance"] = deepcopy(truth)
    if feedback_policy is not None:
        root_input["work_item"]["question_family_policy"] = {
            "feedback_policy_digest": feedback_policy.content_digest,
            "allowed_families": [
                {"id": family_id, "kind": feedback_policy.family_contract_kind(family_id)}
                for family_id in sorted(feedback_policy.families)
            ],
            "allowed_gt_source_types": sorted(feedback_policy.allowed_gt_source_types),
        }
    root_input["execution_mode"] = _PROVIDER_PILOT_MODE
    return root_input


def _provider_stage_usage(stage: WorkerResult) -> dict[str, Any]:
    if stage.fixture_only:
        raise RunError("provider pilot cannot persist a fixture worker result")
    if stage.cost_accounting not in {"provider_reported", "reservation_upper_bound"}:
        raise RunError("provider pilot worker result has invalid cost accounting")
    return {
        "token_in": stage.token_in,
        "token_out": stage.token_out,
        "actual_cost_usd": stage.actual_cost_usd,
        "cost_accounting": stage.cost_accounting,
    }


def _provider_handoff_binding(
    paths: WorkspacePaths,
    *,
    bridge: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidate the private host evidence named by one science bridge.

    The model receives a prompt-safe projection of the bridge, but only this
    function is allowed to resolve the private handoff path and receipt chain.
    Keeping that check at the control-plane boundary prevents a model-produced
    digest citation from becoming evidence on its own.
    """
    locks = bridge.get("lock_bindings") if isinstance(bridge, Mapping) else None
    evidence = bridge.get("viewer_evidence") if isinstance(bridge, Mapping) else None
    if not isinstance(locks, Mapping) or not isinstance(evidence, Mapping):
        raise RunError("provider scientific bridge lacks viewer evidence bindings")
    try:
        verified = verify_attested_provider_handoff(paths, handoff, bridge=bridge)
    except ProviderScienceBridgeError as exc:
        raise RunError(f"provider host evidence handoff is invalid: {exc}") from exc
    body = verified.get("body")
    if not isinstance(body, Mapping):
        raise RunError("provider host evidence handoff body is malformed")
    rendered_views = evidence.get("rendered_views")
    observations = evidence.get("solver_safe_observations")
    receipt_digests = evidence.get("receipt_digests")
    if (
        not isinstance(rendered_views, list)
        or len(rendered_views) < 2
        or not isinstance(observations, list)
        or len(observations) != len(rendered_views)
        or not isinstance(receipt_digests, list)
        or len(receipt_digests) != len(rendered_views)
        or len(set(receipt_digests)) != len(receipt_digests)
    ):
        raise RunError("provider scientific bridge lacks a complete host receipt/render chain")
    render_digests: list[str] = []
    for position, view in enumerate(rendered_views):
        if not isinstance(view, Mapping):
            raise RunError("provider scientific bridge render view is malformed")
        receipt_digest = view.get("receipt_digest")
        render_digest = view.get("render_digest")
        if receipt_digest != receipt_digests[position]:
            raise RunError("provider scientific bridge render receipt order changed")
        require_digest(receipt_digest, "provider scientific bridge receipt digest")
        require_digest(render_digest, "provider scientific bridge render digest")
        render_digests.append(str(render_digest))
    handoff_digest = handoff.get("handoff_digest")
    handoff_path = handoff.get("handoff_path")
    receipt_chain_digest = evidence.get("receipt_chain_digest")
    session_attestation_digest = evidence.get("session_attestation_digest")
    for value, label in (
        (handoff_digest, "provider host evidence digest"),
        (receipt_chain_digest, "provider host receipt chain digest"),
        (session_attestation_digest, "provider host session attestation digest"),
    ):
        require_digest(value, label)
    required = {
        "viewer_profile_digest": locks.get("viewer_profile_digest"),
        "scene_registry_digest": locks.get("scene_registry_digest"),
        "anonymous_scene_ref": evidence.get("anonymous_scene_ref"),
        "session_attestation_digest": session_attestation_digest,
        "receipt_digests": receipt_digests,
        "receipt_chain_digest": receipt_chain_digest,
        "solver_safe_observations": observations,
        "render_digests": render_digests,
        "release_eligible": False,
    }
    if (
        not isinstance(handoff_path, (str, Path))
        or verified.get("content_digest") != handoff_digest
        or any(body.get(key) != value for key, value in required.items())
    ):
        raise RunError("provider host evidence handoff differs from the scientific bridge")
    observed_operations = [item.get("operation") for item in observations if isinstance(item, Mapping)]
    if observed_operations != ["render", "rotate"]:
        raise RunError("provider host evidence must contain the fixed render then rotate sequence")
    return {
        "host_evidence_path": str(handoff_path),
        "host_evidence_digest": str(handoff_digest),
        "viewer_profile_digest": str(locks["viewer_profile_digest"]),
        "scene_registry_digest": str(locks["scene_registry_digest"]),
        "anonymous_scene_ref": str(evidence["anonymous_scene_ref"]),
        "session_attestation_digest": str(session_attestation_digest),
        "receipt_digests": list(receipt_digests),
        "receipt_chain_digest": str(receipt_chain_digest),
        "render_digests": render_digests,
    }


def _attested_evidence_matches(
    value: Any,
    *,
    handoff: Mapping[str, Any],
    include_renders: bool,
    label: str,
) -> None:
    if not isinstance(value, Mapping):
        raise RunError(f"provider {label} lacks host-attested evidence references")
    expected = {
        "handoff_digest": handoff["host_evidence_digest"],
        "receipt_digests": handoff["receipt_digests"],
    }
    if include_renders:
        expected["render_digests"] = handoff["render_digests"]
    if dict(value) != expected:
        raise RunError(f"provider {label} evidence references do not match the host handoff")


def _normalized_provider_text(value: str) -> str:
    """Canonicalize human text without conflating structured answer fields."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        characters.append(" " if character.isspace() or category.startswith("P") else character)
    return " ".join("".join(characters).split())


def _normalized_provider_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalized_provider_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [_normalized_provider_value(item) for item in value]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise RunError("provider structured reference answer has a non-string key")
            key = _normalized_provider_text(raw_key)
            if not key or key in normalized:
                raise RunError("provider structured reference answer has an ambiguous normalized key")
            normalized[key] = _normalized_provider_value(raw_value)
        return normalized
    raise RunError("provider duplicate signature has an unsupported structured value")


def _provider_candidate_signature(candidate: Mapping[str, Any]) -> str:
    """Bind semantic duplicate detection to question, answer, and visual target."""
    prompt = candidate.get("prompt", candidate.get("question"))
    answer = candidate.get("reference_answer")
    target = candidate.get("visual_target")
    if not isinstance(prompt, str) or not _normalized_provider_text(prompt):
        raise RunError("provider candidate has no normalizable question prompt")
    if not isinstance(answer, Mapping) or not answer:
        raise RunError("provider candidate has no structured reference answer")
    if target is None:
        raise RunError("provider candidate has no visual target for duplicate detection")
    return digest(
        {
            "question": _normalized_provider_text(prompt),
            "answer": _normalized_provider_value(answer),
            "visual_target": _normalized_provider_value(target),
        }
    )


def _persisted_provider_candidate_signatures(
    paths: WorkspacePaths,
    *,
    run_id: str,
    executions: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Read only already committed provider candidates for resume/batch dedupe."""
    root = run_directory(paths, run_id) / "private" / "candidates"
    signatures: dict[str, str] = {}
    for work_item_id, entry in executions.items():
        if entry.get("execution_mode") != _PROVIDER_PILOT_MODE:
            continue
        candidate_digest = entry.get("candidate_digest")
        require_digest(candidate_digest, "persisted provider candidate digest")
        candidate_path = _execution_file(root, entry.get("candidate_path"), "provider candidate")
        candidate = _execution_json(candidate_path, "provider candidate")
        if digest(candidate) != candidate_digest:
            raise RunError("persisted provider candidate content changed before duplicate validation")
        signature = _provider_candidate_signature(candidate)
        if signature in signatures:
            raise RunError("persisted provider candidates already contain a semantic duplicate")
        signatures[signature] = work_item_id
    return signatures


def _persisted_provider_handoff_binding(
    paths: WorkspacePaths,
    *,
    run_body: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the minimal bridge needed to revalidate durable host evidence."""
    run_locks = run_body.get("lock_digests")
    if not isinstance(run_locks, Mapping):
        raise RunError("persisted provider execution run lock map is invalid")
    viewer_profile_digest = run_body.get("viewer_profile_digest")
    scene_registry_digest = run_locks.get("scene_registry_digest")
    receipt_digests = entry.get("receipt_digests")
    render_digests = entry.get("render_digests")
    if (
        not isinstance(receipt_digests, list)
        or not isinstance(render_digests, list)
        or len(receipt_digests) < 2
        or len(receipt_digests) != len(render_digests)
    ):
        raise RunError("persisted provider execution host evidence references are malformed")
    for value, label in (
        (viewer_profile_digest, "persisted provider ViewerProfile digest"),
        (scene_registry_digest, "persisted provider SceneRegistry digest"),
        (entry.get("host_evidence_digest"), "persisted provider host evidence digest"),
        (entry.get("receipt_chain_digest"), "persisted provider receipt chain digest"),
        (entry.get("session_attestation_digest"), "persisted provider session attestation digest"),
    ):
        require_digest(value, label)
    for value in [*receipt_digests, *render_digests]:
        require_digest(value, "persisted provider host receipt/render digest")
    if (
        entry.get("viewer_profile_digest") != viewer_profile_digest
        or entry.get("scene_registry_digest") != scene_registry_digest
        or not isinstance(entry.get("anonymous_scene_ref"), str)
        or not entry.get("anonymous_scene_ref")
    ):
        raise RunError("persisted provider host evidence is not bound to the run ViewerProfile")
    observations = [
        {
            "operation": "render" if position == 0 else "rotate",
            "sequence": position,
            "receipt_digest": receipt_digest,
            "render_digest": render_digest,
        }
        for position, (receipt_digest, render_digest) in enumerate(zip(receipt_digests, render_digests, strict=True))
    ]
    bridge = {
        "artifact_kind": "approved_local_chirality_science_bridge",
        "lock_bindings": {
            "viewer_profile_digest": viewer_profile_digest,
            "scene_registry_digest": scene_registry_digest,
        },
        "viewer_evidence": {
            "anonymous_scene_ref": entry["anonymous_scene_ref"],
            "session_attestation_digest": entry["session_attestation_digest"],
            "receipt_digests": list(receipt_digests),
            "receipt_chain_digest": entry["receipt_chain_digest"],
            "solver_safe_observations": observations,
            "rendered_views": [
                {
                    "receipt_digest": receipt_digest,
                    "render_digest": render_digest,
                }
                for receipt_digest, render_digest in zip(receipt_digests, render_digests, strict=True)
            ],
        },
    }
    handoff = {
        "handoff_path": entry.get("host_evidence_path"),
        "handoff_digest": entry.get("host_evidence_digest"),
    }
    binding = _provider_handoff_binding(paths, bridge=bridge, handoff=handoff)
    if any(entry.get(key) != value for key, value in binding.items()):
        raise RunError("persisted provider host evidence bindings changed")
    return binding


def _validate_provider_execution_chain(
    paths: WorkspacePaths,
    result: OrchestrationResult,
    root_input: Mapping[str, Any],
    *,
    handoff: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
) -> dict[str, Any]:
    """Reject a typed provider graph whose cross-stage bindings do not hold."""
    required_stages = {"brief_builder", "author", "truth_builder", "evidence_worker", "verifier"}
    if set(result.stages) != required_stages:
        raise RunError("provider pilot orchestrator did not return the fixed worker graph")
    if not all(isinstance(result.stages[role], WorkerResult) for role in required_stages):
        raise RunError("provider pilot orchestrator returned an invalid worker result")
    author = result.stages["author"]
    evidence = result.stages["evidence_worker"]
    verifier = result.stages["verifier"]
    if author.provider == verifier.provider or author.model == verifier.model:
        raise RunError("provider pilot author and verifier execution routes are not independent")
    for role in required_stages:
        _provider_stage_usage(result.stages[role])

    work_item = root_input.get("work_item")
    if not isinstance(work_item, Mapping):
        raise RunError("provider pilot root input lacks the approved work item")
    work_item_id = work_item.get("work_item_id")
    if (
        not isinstance(work_item_id, str)
        or root_input.get("work_item_id") != work_item_id
        or result.work_item_id != work_item_id
    ):
        raise RunError("provider pilot root input does not bind the orchestrated work item")
    bridge = root_input.get("provider_science_bridge")
    if not isinstance(bridge, Mapping):
        raise RunError("provider pilot root input lacks the approved scientific bridge")
    host_evidence = _provider_handoff_binding(paths, bridge=bridge, handoff=handoff)
    if (
        bridge.get("leaf_id") != _PROVIDER_PILOT_LEAF_ID
        or bridge.get("work_item_id") != work_item_id
        or host_evidence["viewer_profile_digest"] != root_input.get("viewer_profile_digest")
    ):
        raise RunError("provider host evidence is not bound to the approved work item")
    candidate = dict(author.output_contract)
    provenance = candidate.get("provenance")
    request = candidate.get("viewer_session_request")
    if not isinstance(provenance, Mapping) or not isinstance(request, Mapping):
        raise RunError("provider candidate lacks required provenance or viewer bindings")
    if (
        provenance.get("plan_digest") != root_input.get("plan_digest")
        or provenance.get("config_digest") != root_input.get("config_digest")
        or provenance.get("knowledge_snapshot_digest") != work_item.get("knowledge_snapshot_digest")
        or provenance.get("work_item_id") != work_item_id
        or request.get("viewer_profile_digest") != root_input.get("viewer_profile_digest")
    ):
        raise RunError("provider candidate is not bound to the approved plan/config/knowledge/viewer locks")
    requested_operations = request.get("allowed_operations")
    approved_operations = work_item.get("approved_operations")
    if (
        not isinstance(requested_operations, list)
        or not requested_operations
        or not isinstance(approved_operations, list)
        or not set(requested_operations).issubset(set(approved_operations))
    ):
        raise RunError("provider candidate requests viewer operations outside the approved work item")
    visual_target = candidate.get("visual_target")
    if (
        visual_target is None
        or candidate.get("anonymous_scene_ref") != host_evidence["anonymous_scene_ref"]
    ):
        raise RunError("provider candidate lacks the required host-bound visual target")
    _attested_evidence_matches(
        candidate.get("attested_evidence"),
        handoff=host_evidence,
        include_renders=True,
        label="candidate",
    )
    feedback_policy = _feedback_policy_for_snapshot(
        paths,
        leaf_id=work_item.get("leaf_id"),
        knowledge_snapshot_digest=work_item.get("knowledge_snapshot_digest"),
    )
    try:
        validate_candidate_question_contract(
            candidate,
            policy=load_question_text_policy(),
            leaf_id=_PROVIDER_PILOT_LEAF_ID,
            require_contract=True,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=ground_truth_registry.content_digest,
            feedback_policy=feedback_policy,
        )
    except QuestionContractError as exc:
        raise RunError("provider candidate question contract is invalid") from exc

    evidence_output = dict(evidence.output_contract)
    if (
        evidence_output.get("artifact_kind") != "provider_attested_viewer_evidence"
        or evidence_output.get("candidate_digest") != author.output_contract_digest
        or evidence_output.get("viewer_profile_digest") != root_input.get("viewer_profile_digest")
        or evidence_output.get("work_item_id") != work_item_id
        or evidence_output.get("host_evidence_digest") != host_evidence["host_evidence_digest"]
        or evidence_output.get("receipt_digests") != host_evidence["receipt_digests"]
        or "observations" in evidence_output
    ):
        raise RunError("provider evidence is not bound to the authored candidate and host handoff")
    evidence_operations = evidence_output.get("viewer_operations")
    if (
        not isinstance(evidence_operations, list)
        or set(evidence_operations) != set(approved_operations)
    ):
        raise RunError("provider evidence reports viewer operations outside the approved host scope")

    verification = dict(verifier.output_contract)
    try:
        validate_verification_result(verification)
    except InputError as exc:
        raise RunError(f"provider verifier output is invalid: {exc}") from exc
    if (
        verification.get("artifact_kind") != "verification_result"
        or verification.get("candidate_digest") != author.output_contract_digest
        or verification.get("evidence_digest") != evidence.output_contract_digest
        or verification.get("rubric_lock_digest") != root_input.get("rubric_lock_digest")
        or verification.get("work_item_id") != result.work_item_id
    ):
        raise RunError("provider verification is not bound to the author, evidence, rubric, and work item")
    _attested_evidence_matches(
        verification.get("attested_evidence"),
        handoff=host_evidence,
        include_renders=False,
        label="verifier",
    )
    return host_evidence


def _private_output_path(directory: Path, category: str, filename: str) -> Path:
    path = directory / "private" / category / filename
    if path.exists():
        raise RunError(f"execution output already exists without a committed run state: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_worker_output(path: Path, stage: WorkerResult) -> None:
    written_digest = atomic_write_json(path, dict(stage.output_contract), private=True)
    if written_digest != stage.output_contract_digest:
        raise RunError("persisted worker output digest does not match the verified worker result")


def _persist_fixture_execution(
    *,
    directory: Path,
    run_id: str,
    work_item_id: str,
    leaf_id: str,
    dependency: Mapping[str, Any],
    routing_digest: str,
    result: OrchestrationResult,
) -> dict[str, Any]:
    """Persist every review-relevant worker output before committing run state."""
    required_stages = {"brief_builder", "author", "truth_builder", "evidence_worker", "verifier"}
    stages = result.stages
    if not required_stages.issubset(stages):
        raise RunError("orchestrator result lacks the fixed fixture worker graph")
    if not all(isinstance(stages[role], WorkerResult) for role in required_stages):
        raise RunError("orchestrator result contains an invalid worker result")
    author = stages["author"]
    evidence = stages["evidence_worker"]
    verification = stages["verifier"]
    provenance = author.output_contract.get("provenance")
    if (
        safe_component(work_item_id, "fixture work item id") != work_item_id
        or result.work_item_id != work_item_id
        or not isinstance(provenance, Mapping)
        or provenance.get("work_item_id") != work_item_id
        or evidence.output_contract.get("candidate_digest") != author.output_contract_digest
        or evidence.output_contract.get("work_item_id") != work_item_id
        or verification.output_contract.get("candidate_digest") != author.output_contract_digest
        or verification.output_contract.get("evidence_digest") != evidence.output_contract_digest
        or verification.output_contract.get("work_item_id") != work_item_id
    ):
        raise RunError("fixture execution outputs are not bound to the approved work item")
    try:
        validate_verification_result(verification.output_contract, require_pass=True)
    except InputError as exc:
        raise RunError(f"fixture verifier output is invalid: {exc}") from exc
    component = safe_component(work_item_id.replace(".", "_"), "work item id")
    candidate_path = _private_output_path(directory, "candidates", f"fixture-{component}.json")
    evidence_path = _private_output_path(directory, "evidence", f"fixture-{component}.json")
    verification_path = _private_output_path(directory, "verification", f"fixture-{component}.json")
    brief_path = _private_output_path(directory, "worker-outputs", f"{component}.brief_builder.json")
    truth_path = _private_output_path(directory, "worker-outputs", f"{component}.truth_builder.json")
    worker_result_path = _private_output_path(directory, "worker-results", f"{component}.json")
    audit_path = _private_output_path(directory, "audit/executions", f"{component}.json")
    _write_worker_output(candidate_path, author)
    _write_worker_output(evidence_path, evidence)
    _write_worker_output(verification_path, verification)
    _write_worker_output(brief_path, stages["brief_builder"])
    _write_worker_output(truth_path, stages["truth_builder"])
    stage_paths = {
        "brief_builder": brief_path,
        "author": candidate_path,
        "truth_builder": truth_path,
        "evidence_worker": evidence_path,
        "verifier": verification_path,
    }
    stage_digests = {
        role: {
            "path": str(stage_paths[role]),
            "provider": stage.provider,
            "model": stage.model,
            "input_contract_digest": stage.input_contract_digest,
            "output_contract_digest": stage.output_contract_digest,
            "fixture_only": stage.fixture_only,
            "token_in": stage.token_in,
            "token_out": stage.token_out,
            "actual_cost_usd": stage.actual_cost_usd,
        }
        for role, stage in sorted(stages.items())
    }
    worker_result = {
        "artifact_kind": "fixture_worker_run",
        "run_id": run_id,
        "work_item_id": work_item_id,
        "leaf_id": leaf_id,
        "dependency_digest": digest(dependency),
        "routing_digest": routing_digest,
        "root_input_digest": result.root_input_digest,
        "status": result.status,
        "stages": stage_digests,
    }
    worker_result_digest = atomic_write_json(worker_result_path, worker_result, private=True)
    manifest = make_envelope(
        kind="run_execution_manifest",
        identifier=f"execution-{run_id}-{component}",
        status=result.status,
        body={
            **worker_result,
            "candidate": {"path": str(candidate_path), "digest": author.output_contract_digest},
            "evidence": {"path": str(evidence_path), "digest": evidence.output_contract_digest},
            "verification": {"path": str(verification_path), "digest": verification.output_contract_digest},
            "worker_result": {"path": str(worker_result_path), "digest": worker_result_digest},
        },
        producer="qagen/0.1.0:fixture_run_execution",
    )
    atomic_write_json(audit_path, manifest, private=True)
    return {
        "work_item_id": work_item_id,
        "leaf_id": leaf_id,
        "dependency_digest": digest(dependency),
        "routing_digest": routing_digest,
        "root_input_digest": result.root_input_digest,
        "worker_result_path": str(worker_result_path),
        "worker_result_digest": worker_result_digest,
        "execution_manifest_path": str(audit_path),
        "execution_manifest_digest": manifest["content_digest"],
        "candidate_path": str(candidate_path),
        "candidate_digest": author.output_contract_digest,
        "evidence_path": str(evidence_path),
        "evidence_digest": evidence.output_contract_digest,
        "verification_path": str(verification_path),
        "verification_digest": verification.output_contract_digest,
        "status": result.status,
        "stage_digests": stage_digests,
    }


def _persist_provider_execution(
    *,
    paths: WorkspacePaths,
    directory: Path,
    run_id: str,
    work_item_id: str,
    leaf_id: str,
    dependency: Mapping[str, Any],
    routing_digest: str,
    provider_runtime_digest: str,
    result: OrchestrationResult,
    root_input: Mapping[str, Any],
    handoff: Mapping[str, Any],
    ground_truth_registry: GroundTruthRegistry,
    known_signatures: dict[str, str],
    on_duplicate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Persist the auditable outputs from the explicitly authorized HTTP pilot."""
    host_evidence = _validate_provider_execution_chain(
        paths,
        result,
        root_input,
        handoff=handoff,
        ground_truth_registry=ground_truth_registry,
    )
    if result.work_item_id != work_item_id or root_input.get("work_item_id") != work_item_id:
        raise RunError("provider execution is not bound to the selected work item")
    require_digest(provider_runtime_digest, "provider runtime digest")
    stages = result.stages
    author = stages["author"]
    evidence = stages["evidence_worker"]
    verification = stages["verifier"]
    semantic_signature = _provider_candidate_signature(author.output_contract)
    duplicate_work_item_id = known_signatures.get(semantic_signature)
    if duplicate_work_item_id is not None:
        on_duplicate(
            {
                "work_item_id": work_item_id,
                "duplicate_of_work_item_id": duplicate_work_item_id,
                "candidate_digest": author.output_contract_digest,
                "semantic_signature": semantic_signature,
            }
        )
        raise RunError("provider candidate is a semantic duplicate of an already persisted or batch candidate")
    component = safe_component(work_item_id.replace(".", "_"), "work item id")
    candidate_path = _private_output_path(directory, "candidates", f"provider-{component}.json")
    evidence_path = _private_output_path(directory, "evidence", f"provider-{component}.json")
    verification_path = _private_output_path(directory, "verification", f"provider-{component}.json")
    brief_path = _private_output_path(directory, "worker-outputs", f"{component}.provider.brief_builder.json")
    truth_path = _private_output_path(directory, "worker-outputs", f"{component}.provider.truth_builder.json")
    worker_result_path = _private_output_path(directory, "worker-results", f"{component}.provider.json")
    audit_path = _private_output_path(directory, "audit/executions", f"{component}.provider.json")
    _write_worker_output(candidate_path, author)
    _write_worker_output(evidence_path, evidence)
    _write_worker_output(verification_path, verification)
    _write_worker_output(brief_path, stages["brief_builder"])
    _write_worker_output(truth_path, stages["truth_builder"])
    stage_paths = {
        "brief_builder": brief_path,
        "author": candidate_path,
        "truth_builder": truth_path,
        "evidence_worker": evidence_path,
        "verifier": verification_path,
    }
    stage_digests = {
        role: {
            "path": str(stage_paths[role]),
            "provider": stage.provider,
            "model": stage.model,
            "input_contract_digest": stage.input_contract_digest,
            "output_contract_digest": stage.output_contract_digest,
            "fixture_only": False,
            **_provider_stage_usage(stage),
        }
        for role, stage in sorted(stages.items())
    }
    usage = {role: _provider_stage_usage(stage) for role, stage in sorted(stages.items())}
    worker_result = {
        "artifact_kind": "provider_worker_run",
        "execution_mode": _PROVIDER_PILOT_MODE,
        "production_eligible": False,
        "run_id": run_id,
        "work_item_id": work_item_id,
        "leaf_id": leaf_id,
        "dependency_digest": digest(dependency),
        "routing_digest": routing_digest,
        "provider_runtime_digest": provider_runtime_digest,
        "root_input_digest": result.root_input_digest,
        "semantic_signature": semantic_signature,
        "host_evidence": host_evidence,
        "status": result.status,
        "stages": stage_digests,
        "usage": usage,
    }
    worker_result_digest = atomic_write_json(worker_result_path, worker_result, private=True)
    manifest = make_envelope(
        kind="run_execution_manifest",
        identifier=f"provider-execution-{run_id}-{component}",
        status=result.status,
        body={
            **worker_result,
            "candidate": {"path": str(candidate_path), "digest": author.output_contract_digest},
            "evidence": {"path": str(evidence_path), "digest": evidence.output_contract_digest},
            "verification": {"path": str(verification_path), "digest": verification.output_contract_digest},
            "worker_result": {"path": str(worker_result_path), "digest": worker_result_digest},
        },
        producer="qagen/0.1.0:provider_pilot_run_execution",
    )
    atomic_write_json(audit_path, manifest, private=True)
    return {
        "execution_mode": _PROVIDER_PILOT_MODE,
        "production_eligible": False,
        "work_item_id": work_item_id,
        "leaf_id": leaf_id,
        "dependency_digest": digest(dependency),
        "routing_digest": routing_digest,
        "provider_runtime_digest": provider_runtime_digest,
        "root_input_digest": result.root_input_digest,
        "semantic_signature": semantic_signature,
        **host_evidence,
        "worker_result_path": str(worker_result_path),
        "worker_result_digest": worker_result_digest,
        "execution_manifest_path": str(audit_path),
        "execution_manifest_digest": manifest["content_digest"],
        "candidate_path": str(candidate_path),
        "candidate_digest": author.output_contract_digest,
        "evidence_path": str(evidence_path),
        "evidence_digest": evidence.output_contract_digest,
        "verification_path": str(verification_path),
        "verification_digest": verification.output_contract_digest,
        "status": result.status,
        "stage_digests": stage_digests,
        "usage": usage,
    }


def _execute_locked_fixture_runtime(
    paths: WorkspacePaths,
    *,
    run_id: str,
    directory: Path,
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
    policy: Any,
    leaves: Mapping[str, Mapping[str, Any]],
    work_items: list[Mapping[str, Any]],
    approved_work_item_count: int,
    on_completed: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """Run the fixed graph for plan-derived fixture work items only."""
    executor = WorkerExecutor(
        paths,
        policy=policy,
        approved_work_item_count=approved_work_item_count,
        allow_fixture_adapter=True,
    )
    orchestrator = WorkerOrchestrator(executor)
    dependencies = _ordered_dependencies(plan["body"].get("dependency_locks"), label="plan")
    dependency_by_leaf = {item["leaf_id"]: item for item in dependencies}
    adapters = {
        role: _RunFixtureAdapter(
            provider=str(policy.role(role)["provider"]),
            model=str(policy.role(role)["allowed_models"][0]),
        )
        for role in ("brief_builder", "author", "truth_builder", "evidence_worker", "verifier", "rewriter")
    }
    results: list[dict[str, Any]] = []
    for work_item in work_items:
        if not isinstance(work_item, Mapping):
            raise RunError("approved plan has a malformed fixture work item")
        work_item_id = work_item.get("work_item_id")
        leaf_id = work_item.get("leaf_id")
        if (
            not isinstance(work_item_id, str)
            or safe_component(work_item_id, "fixture work item id") != work_item_id
            or not isinstance(leaf_id, str)
            or leaf_id not in dependency_by_leaf
        ):
            raise RunError("approved fixture work item lacks a locked dependency map")
        leaf = leaves.get(leaf_id)
        if not isinstance(leaf, Mapping):
            raise RunError("approved fixture work item has no eligible leaf")
        dependency = dependency_by_leaf[leaf_id]
        root_input = _fixture_work_item_input(
            run_id=run_id,
            plan=plan,
            config=config,
            viewer=viewer,
            leaf=leaf,
            dependency=dependency,
            work_item=work_item,
        )
        result = orchestrator.run(
            run_id=run_id,
            work_item_id=work_item_id,
            input_contract=root_input,
            adapters=adapters,
        )
        execution = _persist_fixture_execution(
            directory=directory,
            run_id=run_id,
            work_item_id=work_item_id,
            leaf_id=leaf_id,
            dependency=dependency,
            routing_digest=policy.content_digest,
            result=result,
        )
        on_completed(execution)
        results.append(execution)
    return results


def _execute_locked_provider_runtime(
    paths: WorkspacePaths,
    *,
    run_id: str,
    directory: Path,
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
    policy: Any,
    provider_runtime_path: Path,
    provider_runtime_digest: str,
    leaves: Mapping[str, Mapping[str, Any]],
    work_items: list[Mapping[str, Any]],
    approved_work_item_count: int,
    on_completed: Callable[[dict[str, Any]], None],
    provider_client: httpx.Client | None,
    known_signatures: dict[str, str],
    on_duplicate: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """Run only the exact approved chirality graph through real provider adapters."""
    if provider_client is not None and not isinstance(provider_client, httpx.Client):
        raise RunError("provider test transport must be an httpx.Client")
    # This gate reads only the pinned runtime file.  It deliberately runs
    # before credentials, adapter construction, and any possible transport.
    try:
        require_provider_multimodal_capability(
            provider_runtime_path,
            expected_config_digest=provider_runtime_digest,
        )
    except InputError as exc:
        raise RunError(f"provider pilot visual capability gate failed: {exc}") from exc
    ground_truth_registry = _plan_ground_truth_registry(paths, plan)
    dependencies = _ordered_dependencies(plan["body"].get("dependency_locks"), label="plan")
    dependency_by_leaf = {item["leaf_id"]: item for item in dependencies}
    prepared: list[tuple[str, str, Mapping[str, Any], Mapping[str, Any], dict[str, Any], dict[str, Any]]] = []
    for work_item in work_items:
        if not isinstance(work_item, Mapping):
            raise RunError("approved provider pilot plan has a malformed work item")
        work_item_id = work_item.get("work_item_id")
        leaf_id = work_item.get("leaf_id")
        if (
            not isinstance(work_item_id, str)
            or safe_component(work_item_id, "provider pilot work item id") != work_item_id
            or leaf_id != _PROVIDER_PILOT_LEAF_ID
            or leaf_id not in dependency_by_leaf
        ):
            raise RunError("provider pilot plan leaf is not the approved chirality work item")
        leaf = leaves.get(leaf_id)
        if not isinstance(leaf, Mapping):
            raise RunError("provider pilot work item has no approved chirality leaf")
        dependency = dependency_by_leaf[leaf_id]
        try:
            science_bridge, handoff = prepare_approved_chirality_science_bridge(
                paths,
                plan=plan,
                config=config,
                viewer=viewer,
                dependency=dependency,
                work_item=work_item,
            )
            _provider_handoff_binding(paths, bridge=science_bridge, handoff=handoff)
        except InputError as exc:
            raise RunError(f"provider pilot cannot prepare approved scientific input: {exc}") from exc
        root_input = _provider_work_item_input(
            paths=paths,
            run_id=run_id,
            plan=plan,
            config=config,
            viewer=viewer,
            leaf=leaf,
            dependency=dependency,
            work_item=work_item,
            science_bridge=science_bridge,
            ground_truth_registry=ground_truth_registry,
        )
        prepared.append((work_item_id, str(leaf_id), leaf, dependency, root_input, handoff))
    # ``build_provider_adapters`` loads the credential before it creates an
    # adapter.  Thus a missing QAGEN_UNIV_API_KEY exits before any HTTP call.
    provider_adapters = build_provider_adapters(
        policy,
        runtime_config_path=provider_runtime_path,
        runtime_config_digest=provider_runtime_digest,
        client=provider_client,
    )
    executor = WorkerExecutor(
        paths,
        policy=policy,
        approved_work_item_count=approved_work_item_count,
        provider_adapters=provider_adapters,
    )
    orchestrator = WorkerOrchestrator(executor)
    adapters: dict[str, Any] = {}
    for role in ("brief_builder", "author", "truth_builder", "evidence_worker", "verifier"):
        route = policy.role(role)
        models = route.get("allowed_models")
        if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], str):
            raise RunError(f"provider pilot route is malformed for {role}")
        adapter = provider_adapters.get((str(route.get("provider")), models[0]))
        if adapter is None:
            raise RunError(f"provider pilot has no approved adapter for {role}")
        adapters[role] = adapter
    results: list[dict[str, Any]] = []
    for work_item_id, leaf_id, _leaf, dependency, root_input, handoff in prepared:
        result = orchestrator.run(
            run_id=run_id,
            work_item_id=work_item_id,
            input_contract=root_input,
            adapters=adapters,
        )
        execution = _persist_provider_execution(
            paths=paths,
            directory=directory,
            run_id=run_id,
            work_item_id=work_item_id,
            leaf_id=leaf_id,
            dependency=dependency,
            routing_digest=policy.content_digest,
            provider_runtime_digest=provider_runtime_digest,
            result=result,
            root_input=root_input,
            handoff=handoff,
            ground_truth_registry=ground_truth_registry,
            known_signatures=known_signatures,
            on_duplicate=on_duplicate,
        )
        known_signatures[execution["semantic_signature"]] = work_item_id
        on_completed(execution)
        results.append(execution)
    return results


def _load_persisted_revision_receipt(
    paths: WorkspacePaths,
    *,
    run_id: str,
    state: Mapping[str, Any],
    revision_receipt_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = run_directory(paths, run_id) / "private" / "revision-receipts"
    path = _execution_file(root, revision_receipt_path, "revision receipt")
    value = _execution_json(path, "revision receipt")
    try:
        receipt = verify_envelope(value, kind="qagen_revision_receipt")
    except InputError as exc:
        raise RunError(f"revision receipt is invalid: {exc}") from exc
    reviews = state.get("body", {}).get("candidate_reviews")
    if not isinstance(reviews, Mapping):
        raise RunError("run has no persisted review state")
    matches = [
        (candidate_digest, dict(review))
        for candidate_digest, review in reviews.items()
        if isinstance(review, Mapping)
        and review.get("decision") == "revise"
        and review.get("revision_receipt_digest") == receipt["content_digest"]
        and review.get("revision_receipt_path") == str(path)
    ]
    if len(matches) != 1:
        raise RunError("revision receipt is not bound to exactly one persisted revise decision")
    reviewed_candidate_digest, review = matches[0]
    body = receipt["body"]
    work_item_id = review.get("work_item_id")
    if not isinstance(work_item_id, str) or safe_component(work_item_id, "reviewed work item id") != work_item_id:
        raise RunError("persisted review state lacks a valid work-item binding")
    expected = {
        "decision": "revise",
        "run_id": run_id,
        "work_item_id": work_item_id,
        "candidate_digest": reviewed_candidate_digest,
        "verifier_output_digest": review.get("verifier_output_digest"),
        "review_decision_digest": review.get("decision_digest"),
    }
    if receipt.get("status") != "revise" or any(body.get(key) != expected_value for key, expected_value in expected.items()):
        raise RunError("revision receipt bindings do not match persisted review state")
    rationale = body.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or body.get("reasons") != [rationale.strip()]:
        raise RunError("revision receipt reasons are missing or changed")
    return receipt, review


def _persist_fixture_revision(
    *,
    directory: Path,
    run_id: str,
    base_execution: Mapping[str, Any],
    receipt: Mapping[str, Any],
    result: OrchestrationResult,
) -> dict[str, Any]:
    rewritten = result.stages.get("rewriter")
    if not isinstance(rewritten, WorkerResult) or result.revision_count != 1:
        raise RunError("orchestrator did not return one valid rewrite")
    if rewritten.output_contract_digest == base_execution.get("candidate_digest"):
        raise RunError("rewriter returned the unchanged base candidate")
    leaf_id = str(base_execution["leaf_id"])
    work_item_id = base_execution.get("work_item_id")
    provenance = rewritten.output_contract.get("provenance")
    if (
        not isinstance(work_item_id, str)
        or safe_component(work_item_id, "revision work item id") != work_item_id
        or not isinstance(provenance, Mapping)
        or provenance.get("work_item_id") != work_item_id
    ):
        raise RunError("rewritten candidate is not bound to the reviewed work item")
    component = safe_component(work_item_id.replace(".", "_"), "work item id")
    candidate_path = _private_output_path(directory, "candidates", f"revision-{component}.json")
    worker_result_path = _private_output_path(directory, "worker-results", f"{component}.revision.json")
    manifest_path = _private_output_path(directory, "audit/revisions", f"{component}.revision.json")
    _write_worker_output(candidate_path, rewritten)
    rewriter_stage = {
        "path": str(candidate_path),
        "provider": rewritten.provider,
        "model": rewritten.model,
        "input_contract_digest": rewritten.input_contract_digest,
        "output_contract_digest": rewritten.output_contract_digest,
        "fixture_only": rewritten.fixture_only,
        "token_in": rewritten.token_in,
        "token_out": rewritten.token_out,
        "actual_cost_usd": rewritten.actual_cost_usd,
    }
    worker_result = {
        "artifact_kind": "fixture_worker_revision",
        "run_id": run_id,
        "work_item_id": work_item_id,
        "leaf_id": leaf_id,
        "base_candidate_digest": base_execution["candidate_digest"],
        "base_execution_manifest_digest": base_execution["execution_manifest_digest"],
        "revision_receipt_digest": receipt["content_digest"],
        "status": result.status,
        "rewriter": rewriter_stage,
    }
    worker_result_digest = atomic_write_json(worker_result_path, worker_result, private=True)
    manifest = make_envelope(
        kind="run_revision_manifest",
        identifier=f"revision-{run_id}-{component}",
        status=result.status,
        body={
            **worker_result,
            "candidate": {"path": str(candidate_path), "digest": rewritten.output_contract_digest},
            "worker_result": {"path": str(worker_result_path), "digest": worker_result_digest},
        },
        producer="qagen/0.1.0:fixture_run_revision",
    )
    atomic_write_json(manifest_path, manifest, private=True)
    return {
        "work_item_id": work_item_id,
        "revision_count": 1,
        "revision_receipt_digest": receipt["content_digest"],
        "candidate_path": str(candidate_path),
        "candidate_digest": rewritten.output_contract_digest,
        "rewriter_stage": rewriter_stage,
        "worker_result_path": str(worker_result_path),
        "worker_result_digest": worker_result_digest,
        "revision_manifest_path": str(manifest_path),
        "revision_manifest_digest": manifest["content_digest"],
        "status": result.status,
    }


def _execute_fixture_revision(
    paths: WorkspacePaths,
    *,
    run_id: str,
    state: Mapping[str, Any],
    body: dict[str, Any],
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
    policy: Any,
    leaves: Mapping[str, Mapping[str, Any]],
    work_items: Mapping[str, Mapping[str, Any]],
    approved_work_item_count: int,
    executions: dict[str, dict[str, Any]],
    revision_receipt_path: str | Path,
    requested_work_item: str | None,
) -> dict[str, Any]:
    receipt, review = _load_persisted_revision_receipt(
        paths,
        run_id=run_id,
        state=state,
        revision_receipt_path=revision_receipt_path,
    )
    work_item_id = review.get("work_item_id")
    if not isinstance(work_item_id, str) or work_item_id not in work_items or work_item_id not in executions:
        raise RunError("revision receipt work item has no persisted base execution")
    if requested_work_item is not None and requested_work_item != work_item_id:
        raise RunError("revision receipt is bound to another requested work item")
    work_item = work_items[work_item_id]
    leaf_id = work_item.get("leaf_id")
    if not isinstance(leaf_id, str) or leaf_id not in leaves:
        raise RunError("revision receipt work item has no eligible leaf")
    base_entry = executions[work_item_id]
    if base_entry.get("work_item_id") != work_item_id:
        raise RunError("revision receipt base execution work-item binding changed")
    if "revision" in base_entry:
        raise InvalidStateError("work item already used its one allowed revision")
    candidate_digest = receipt["body"].get("candidate_digest")
    if candidate_digest != base_entry.get("candidate_digest"):
        raise RunError("revision receipt candidate does not match the base execution")
    base = load_persisted_execution(
        paths,
        run=state,
        candidate_path=base_entry.get("candidate_path"),
        candidate_digest=candidate_digest,
        evidence_path=base_entry.get("evidence_path"),
        evidence_digest=base_entry.get("evidence_digest"),
    )
    dependencies = _ordered_dependencies(plan["body"].get("dependency_locks"), label="plan")
    dependency_by_leaf = {item["leaf_id"]: item for item in dependencies}
    dependency = dependency_by_leaf.get(leaf_id)
    if (
        not isinstance(dependency, Mapping)
        or digest(dependency) != base_entry.get("dependency_digest")
        or work_item.get("dependency_digest") != digest(dependency)
    ):
        raise RunError("revision base dependency no longer matches the approved plan")
    root_input = _fixture_work_item_input(
        run_id=run_id,
        plan=plan,
        config=config,
        viewer=viewer,
        leaf=leaves[leaf_id],
        dependency=dependency,
        work_item=work_item,
    )
    base_result = OrchestrationResult(
        run_id=run_id,
        work_item_id=work_item_id,
        stages=base["stage_results"],
        revision_count=0,
        status=str(base_entry.get("status")),
        root_input_digest=base_entry["root_input_digest"],
    )
    executor = WorkerExecutor(
        paths,
        policy=policy,
        approved_work_item_count=approved_work_item_count,
        allow_fixture_adapter=True,
    )
    orchestrator = WorkerOrchestrator(executor)
    route = policy.role("rewriter")
    adapters = {
        "rewriter": _RunFixtureAdapter(
            provider=str(route["provider"]),
            model=str(route["allowed_models"][0]),
        )
    }
    revised = orchestrator.rewrite(
        base_result,
        input_contract=root_input,
        revision_receipt=receipt,
        adapters=adapters,
    )
    revision = _persist_fixture_revision(
        directory=run_directory(paths, run_id),
        run_id=run_id,
        base_execution=base_entry,
        receipt=receipt,
        result=revised,
    )
    base_entry["revision"] = revision
    executions[work_item_id] = base_entry
    body["executions"] = executions
    updated = _write_state(paths, run_id, body, status="running")
    append_event(
        paths,
        "run_work_item_revised",
        {
            "run_id": run_id,
            "run_digest": updated["content_digest"],
            "leaf_id": leaf_id,
            "work_item_id": work_item_id,
            "base_candidate_digest": candidate_digest,
            "revised_candidate_digest": revision["candidate_digest"],
            "revision_receipt_digest": receipt["content_digest"],
        },
    )
    return {
        "run_id": run_id,
        "run_state_digest": updated["content_digest"],
        "status": updated["status"],
        "execution_status": revised.status,
        "revision": revision,
    }


def start_run(
    paths: WorkspacePaths,
    *,
    plan_path: str | Path,
    approval_path: str | Path,
) -> dict[str, Any]:
    """Start a persistent local run only after a bound approved plan."""
    paths.ensure()
    plan = load_plan(paths, plan_path)
    approval = load_plan_approval(paths, approval_path)
    _validate_approval(plan, approval)
    config, viewer = _validate_plan_live_locks(paths, plan)
    if not plan["body"].get("eligible_leaves"):
        raise GatePendingError("approved plan has no eligible leaves")
    work_items = validate_plan_work_items(plan)
    run_id = f"run-{_short(plan['content_digest'])}-{secrets.token_hex(5)}"
    directory = run_directory(paths, run_id)
    for child in (
        directory,
        directory / "private" / "candidates",
        directory / "private" / "evidence",
        directory / "private" / "verification",
        directory / "private" / "worker-outputs",
        directory / "private" / "worker-results",
        directory / "private" / "revision-receipts",
        directory / "private" / "audit" / "executions",
        directory / "private" / "audit" / "revisions",
        directory / "staging" / "solver",
        directory / "staging" / "scorer",
        directory / "staging" / "audit",
    ):
        child.mkdir(parents=True, exist_ok=True)
    body = {
        "run_id": run_id,
        "plan_path": str(Path(plan_path).expanduser().resolve()),
        "plan_digest": plan["content_digest"],
        "approval_path": str(Path(approval_path).expanduser().resolve()),
        "approval_digest": approval["content_digest"],
        "config_digest": config["content_digest"],
        "viewer_profile_digest": viewer["content_digest"],
        "lock_digests": deepcopy(plan["body"]["lock_digests"]),
        "eligible_leaves": deepcopy(plan["body"]["eligible_leaves"]),
        "work_items": work_items,
        "fixture_only": bool(plan["body"].get("fixture_only")),
        "provider_pilot": bool(plan["body"].get("provider_pilot")),
        "resume_count": 0,
        "executions": {},
        "revision_counts": {},
        "candidate_reviews": {},
    }
    state = _write_state(paths, run_id, body, status="running")
    append_event(paths, "run_started", {"run_id": run_id, "run_digest": state["content_digest"], "plan_digest": plan["content_digest"]})
    return {"run_id": run_id, "run_path": str(directory), "run_state_digest": state["content_digest"], "status": state["status"]}


def execute_run(
    paths: WorkspacePaths,
    *,
    run_id: str,
    work_item_id: str | None = None,
    revision_receipt_path: str | Path | None = None,
    provider_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute an approved fixture or the single authorized provider pilot.

    The public entry point deliberately accepts no policy, adapter, raw plan,
    runtime config, or raw work-item payload.  ``provider_client`` only makes
    the HTTP transport injectable for local tests; it cannot change the
    locked endpoint, model routes, credentials, or profile authorization.
    """
    paths.ensure()
    canonical_run_id = safe_component(run_id, "run id")
    requested_work_item = safe_component(work_item_id, "work item id") if work_item_id is not None else None
    with _locked_run(paths, canonical_run_id):
        state = load_run(paths, canonical_run_id)
        body = deepcopy(state["body"])
        if state.get("id") != canonical_run_id or body.get("run_id") != canonical_run_id:
            raise RunError("run state identifier does not match the requested run")
        if state["status"] != "running":
            raise InvalidStateError(f"run cannot execute from {state['status']}")
        try:
            plan, config, viewer = validate_run_locks(paths, state)
        except (InputError, StaleInputError) as exc:
            blocked = _write_state(paths, canonical_run_id, body, status="blocked_stale_locks")
            append_event(
                paths,
                "run_blocked_stale",
                {"run_id": canonical_run_id, "run_digest": blocked["content_digest"], "reason": str(exc)},
            )
            raise StaleInputError(f"run locks are stale: {exc}") from exc
        fixture_execution = body.get("fixture_only") is True
        provider_execution = body.get("provider_pilot") is True
        if fixture_execution == provider_execution:
            raise GatePendingError("run execute has no authorized execution mode for this plan")
        provider_runtime_path: Path | None = None
        provider_runtime_digest: str | None = None
        if fixture_execution:
            if provider_client is not None:
                raise RunError("a provider transport may only be supplied to the approved provider pilot")
            # The fixture policy is loaded from the persisted config path and
            # checked against the approved plan lock.  No caller may inject a
            # policy or adapter through this public execution entry point.
            policy = _fixture_runtime_policy(config, plan)
        else:
            policy, provider_runtime_path, provider_runtime_digest = _provider_runtime_policy(config, plan, viewer)
        plan_body = plan["body"]
        eligible = plan_body.get("eligible_leaves")
        if not isinstance(eligible, list) or not eligible:
            raise GatePendingError("approved plan has no executable work items")
        leaves: dict[str, Mapping[str, Any]] = {}
        for leaf in eligible:
            if not isinstance(leaf, Mapping):
                raise RunError("approved plan has a malformed eligible leaf")
            leaf_id = leaf.get("leaf_id")
            if not isinstance(leaf_id, str) or not leaf_id or leaf_id in leaves:
                raise RunError("approved plan has an invalid or duplicate work item")
            leaves[leaf_id] = leaf
        work_items_by_id: dict[str, Mapping[str, Any]] = {}
        for item in validate_plan_work_items(plan):
            work_item_id = item.get("work_item_id")
            leaf_id = item.get("leaf_id")
            if (
                not isinstance(work_item_id, str)
                or safe_component(work_item_id, "approved work item id") != work_item_id
                or work_item_id in work_items_by_id
                or not isinstance(leaf_id, str)
                or leaf_id not in leaves
            ):
                raise RunError("approved plan has an invalid or duplicate concrete work item")
            work_items_by_id[work_item_id] = item
        recorded = body.get("executions", {})
        if not isinstance(recorded, Mapping):
            raise RunError("run state executions must be a mapping")
        executions = {
            execution_id: dict(entry)
            for execution_id, entry in recorded.items()
            if isinstance(execution_id, str) and isinstance(entry, Mapping)
        }
        execution_scope = work_items_by_id
        if (
            len(executions) != len(recorded)
            or any(
                execution_id not in execution_scope or entry.get("work_item_id") != execution_id
                for execution_id, entry in executions.items()
            )
        ):
            raise RunError("run state has an invalid persisted execution entry")
        if revision_receipt_path is not None:
            if provider_execution:
                raise GatePendingError("provider pilot revisions are not authorized by this execution mode")
            return _execute_fixture_revision(
                paths,
                run_id=canonical_run_id,
                state=state,
                body=body,
                plan=plan,
                config=config,
                viewer=viewer,
                policy=policy,
                leaves=leaves,
                work_items=work_items_by_id,
                approved_work_item_count=len(work_items_by_id),
                executions=executions,
                revision_receipt_path=revision_receipt_path,
                requested_work_item=requested_work_item,
            )
        if requested_work_item is not None:
            if requested_work_item not in execution_scope:
                raise RunError("requested work item is not an approved plan work item")
            if requested_work_item in executions:
                raise InvalidStateError("requested work item already has a persisted execution")
            selected = [execution_scope[requested_work_item]]
        else:
            selected = [item for item_id, item in execution_scope.items() if item_id not in executions]
            if not selected:
                raise InvalidStateError("run has no unexecuted approved work items")

        latest_state = state

        def commit_execution(execution: dict[str, Any]) -> None:
            nonlocal latest_state
            execution_id = execution.get("work_item_id")
            if not isinstance(execution_id, str) or execution_id not in execution_scope:
                raise RunError("completed execution is not bound to an approved plan work item")
            if execution_id in executions:
                raise InvalidStateError("work item already has a persisted execution")
            executions[execution_id] = dict(execution)
            body["executions"] = executions
            latest_state = _write_state(paths, canonical_run_id, body, status="running")
            append_event(
                paths,
                "run_work_item_executed",
                {
                    "run_id": canonical_run_id,
                    "run_digest": latest_state["content_digest"],
                    "leaf_id": execution["leaf_id"],
                    "work_item_id": execution_id,
                    "candidate_digest": execution["candidate_digest"],
                    "evidence_digest": execution["evidence_digest"],
                    "verification_digest": execution["verification_digest"],
                    "routing_digest": execution["routing_digest"],
                    "execution_mode": execution.get("execution_mode", "local_fixture"),
                },
            )

        if provider_execution:
            if provider_runtime_path is None or provider_runtime_digest is None:
                raise RunError("provider pilot runtime was not resolved from the approved plan")
            known_provider_signatures = _persisted_provider_candidate_signatures(
                paths,
                run_id=canonical_run_id,
                executions=executions,
            )

            def reject_duplicate(payload: dict[str, Any]) -> None:
                append_event(
                    paths,
                    "provider_candidate_rejected_duplicate",
                    {
                        "run_id": canonical_run_id,
                        "run_digest": latest_state["content_digest"],
                        **payload,
                    },
                )

            executed = _execute_locked_provider_runtime(
                paths,
                run_id=canonical_run_id,
                directory=run_directory(paths, canonical_run_id),
                plan=plan,
                config=config,
                viewer=viewer,
                policy=policy,
                provider_runtime_path=provider_runtime_path,
                provider_runtime_digest=provider_runtime_digest,
                leaves=leaves,
                work_items=selected,
                approved_work_item_count=len(work_items_by_id),
                on_completed=commit_execution,
                provider_client=provider_client,
                known_signatures=known_provider_signatures,
                on_duplicate=reject_duplicate,
            )
            execution_status = "provider_pilot_execution_complete_pending_human_review"
        else:
            executed = _execute_locked_fixture_runtime(
                paths,
                run_id=canonical_run_id,
                directory=run_directory(paths, canonical_run_id),
                plan=plan,
                config=config,
                viewer=viewer,
                policy=policy,
                leaves=leaves,
                work_items=selected,
                approved_work_item_count=len(work_items_by_id),
                on_completed=commit_execution,
            )
            execution_status = "fixture_execution_complete_pending_human_review"
        return {
            "run_id": canonical_run_id,
            "run_state_digest": latest_state["content_digest"],
            "status": latest_state["status"],
            "execution_status": execution_status,
            "executed": executed,
        }


def resume_run(paths: WorkspacePaths, *, run_id: str) -> dict[str, Any]:
    """Resume an existing run only after all current locks still validate."""
    paths.ensure()
    canonical_run_id = safe_component(run_id, "run id")
    with _locked_run(paths, canonical_run_id):
        state = load_run(paths, canonical_run_id)
        if state["status"] not in {"running", "paused", "blocked_stale_locks"}:
            raise InvalidStateError(f"run cannot be resumed from {state['status']}")
        body = deepcopy(state["body"])
        try:
            validate_run_locks(paths, state)
        except (InputError, StaleInputError) as exc:
            blocked = _write_state(paths, canonical_run_id, body, status="blocked_stale_locks")
            append_event(
                paths,
                "run_blocked_stale",
                {"run_id": canonical_run_id, "run_digest": blocked["content_digest"], "reason": str(exc)},
            )
            raise StaleInputError(f"run locks are stale: {exc}") from exc
        body["resume_count"] = int(body.get("resume_count", 0)) + 1
        resumed = _write_state(paths, canonical_run_id, body, status="running")
        append_event(
            paths,
            "run_resumed",
            {"run_id": canonical_run_id, "run_digest": resumed["content_digest"], "resume_count": body["resume_count"]},
        )
        return {
            "run_id": canonical_run_id,
            "run_state_digest": resumed["content_digest"],
            "status": resumed["status"],
            "resume_count": body["resume_count"],
        }


def record_review_decision(
    paths: WorkspacePaths,
    *,
    run_id: str,
    candidate_digest: str,
    candidate_key: str,
    decision: str,
    decision_digest: str,
    receipt: Mapping[str, Any],
    revision_receipt: Mapping[str, Any] | None = None,
    revision_receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Commit a review decision under the same per-run state lock as execution."""
    paths.ensure()
    canonical_run_id = safe_component(run_id, "run id")
    with _locked_run(paths, canonical_run_id):
        return record_review_decision_locked(
            paths,
            run_id=canonical_run_id,
            candidate_digest=candidate_digest,
            candidate_key=candidate_key,
            decision=decision,
            decision_digest=decision_digest,
            receipt=receipt,
            revision_receipt=revision_receipt,
            revision_receipt_path=revision_receipt_path,
        )


def record_review_decision_locked(
    paths: WorkspacePaths,
    *,
    run_id: str,
    candidate_digest: str,
    candidate_key: str,
    decision: str,
    decision_digest: str,
    receipt: Mapping[str, Any],
    revision_receipt: Mapping[str, Any] | None = None,
    revision_receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Commit review state while the caller holds :func:`run_state_lock`."""
    if decision not in {"approved", "revise", "rejected", "quarantined"}:
        raise RunError("review decision is not recognized")
    require_digest(candidate_digest, "candidate digest")
    require_digest(decision_digest, "review decision digest")
    if not isinstance(candidate_key, str) or not candidate_key:
        raise RunError("candidate key is required")
    try:
        review = verify_envelope(receipt, kind="domain_review_decision")
    except InputError as exc:
        raise RunError(f"review receipt is invalid: {exc}") from exc
    review_body = review["body"]
    if review["content_digest"] != decision_digest or review["status"] != decision or review_body.get("decision") != decision:
        raise RunError("review receipt decision digest or status does not match state update")
    if review_body.get("candidate_digest") != candidate_digest:
        raise RunError("review receipt does not bind the candidate digest")
    evidence_digest = review_body.get("evidence_digest")
    require_digest(evidence_digest, "review evidence digest")
    state = load_run(paths, run_id)
    if state["status"] != "running":
        raise InvalidStateError("review decisions require a running run")
    plan, _, _ = validate_run_locks(paths, state)
    body = deepcopy(state["body"])
    bound = review_body.get("bound_digests")
    if not isinstance(bound, Mapping):
        raise RunError("review receipt lacks bound digests")
    if review_body.get("config_digest") != body.get("config_digest"):
        raise RunError("review receipt config digest does not match run")
    if review_body.get("viewer_profile_digest") != body.get("viewer_profile_digest"):
        raise RunError("review receipt viewer profile digest does not match run")
    knowledge_digest = review_body.get("knowledge_snapshot_digest")
    requested_operations = review_body.get("requested_operations")
    reviewed_work_item_id = review_body.get("work_item_id")
    try:
        work_item = resolve_plan_work_item(
            plan,
            knowledge_digest=knowledge_digest,
            requested_operations=requested_operations,
            work_item_id=reviewed_work_item_id,
        )
    except InputError as exc:
        raise RunError(f"review receipt does not resolve to one approved plan work item: {exc}") from exc
    if review_body.get("leaf_id") != work_item["leaf_id"]:
        raise RunError("review receipt leaf id does not match the approved plan work item")
    if review_body.get("work_item_id") != work_item["work_item_id"]:
        raise RunError("review receipt work item id does not match the approved plan work item")
    if candidate_key != work_item["work_item_id"]:
        raise RunError("review candidate key must be the approved work item id")
    if review_body.get("work_item_dependency_digest") != work_item["dependency_digest"]:
        raise RunError("review receipt work-item dependency lock does not match plan")
    try:
        execution = load_persisted_execution(
            paths,
            run=state,
            candidate_path=review_body.get("candidate_path"),
            candidate_digest=candidate_digest,
            evidence_path=review_body.get("evidence_path"),
            evidence_digest=evidence_digest,
        )
    except InputError as exc:
        raise RunError(f"review receipt is not bound to a persisted run execution: {exc}") from exc
    execution_entry = execution["entry"]
    if execution_entry.get("leaf_id") != work_item["leaf_id"]:
        raise RunError("review receipt execution leaf does not match the approved work item")
    if execution_entry.get("work_item_id") != work_item["work_item_id"]:
        raise RunError("review receipt execution work item does not match the approved work item")
    if execution_entry.get("dependency_digest") != work_item["dependency_digest"]:
        raise RunError("review receipt execution dependency does not match the approved work item")
    execution_bindings = {
        "execution_manifest_digest": execution_entry["execution_manifest_digest"],
        "worker_result_digest": execution_entry["worker_result_digest"],
        "author_output_digest": candidate_digest,
        "evidence_output_digest": evidence_digest,
        "verifier_output_digest": execution_entry["verification_digest"],
        "stage_digests_digest": digest(execution_entry["stage_digests"]),
    }
    persisted_revision: dict[str, str] = {}
    if decision == "revise":
        if not isinstance(revision_receipt, Mapping) or revision_receipt_path is None:
            raise RunError("revise decision requires a persisted revision receipt")
        try:
            revision = verify_envelope(revision_receipt, kind="qagen_revision_receipt")
            revision_path = _execution_file(
                run_directory(paths, run_id) / "private" / "revision-receipts",
                revision_receipt_path,
                "revision receipt",
            )
            persisted_value = _execution_json(revision_path, "revision receipt")
            persisted = verify_envelope(persisted_value, kind="qagen_revision_receipt")
        except InputError as exc:
            raise RunError(f"revision receipt is invalid: {exc}") from exc
        if persisted != revision:
            raise RunError("persisted revision receipt content changed")
        revision_body = revision["body"]
        expected_revision = {
            "decision": "revise",
            "run_id": run_id,
            "work_item_id": work_item["work_item_id"],
            "candidate_digest": candidate_digest,
            "verifier_output_digest": execution_entry["verification_digest"],
            "review_decision_digest": decision_digest,
            "rationale": review_body.get("rationale"),
            "reasons": [review_body.get("rationale")],
        }
        if revision.get("status") != "revise" or any(
            revision_body.get(key) != value for key, value in expected_revision.items()
        ):
            raise RunError("revision receipt is not bound to the review decision and execution")
        persisted_revision = {
            "revision_receipt_digest": revision["content_digest"],
            "revision_receipt_path": str(revision_path),
        }
    elif revision_receipt is not None or revision_receipt_path is not None:
        raise RunError("only a revise decision may attach a revision receipt")
    expected = {
        "candidate_digest": candidate_digest,
        "evidence_digest": evidence_digest,
        "plan_digest": body.get("plan_digest"),
        "config_digest": body.get("config_digest"),
        "viewer_digest": body.get("viewer_profile_digest"),
        "viewer_profile_digest": body.get("viewer_profile_digest"),
        "work_item_dependency_digest": work_item["dependency_digest"],
        **execution_bindings,
        **dict(body.get("lock_digests", {})),
    }
    for key, value in expected.items():
        if bound.get(key) != value:
            raise RunError(f"review receipt binding does not match run lock: {key}")
        if key in execution_bindings and review_body.get(key) != value:
            raise RunError(f"review receipt execution binding changed: {key}")
    reviews = dict(body.get("candidate_reviews", {}))
    if candidate_digest in reviews:
        raise InvalidStateError("candidate already has an immutable review decision")
    revision_counts = dict(body.get("revision_counts", {}))
    if decision == "revise":
        previous = int(revision_counts.get(candidate_key, 0))
        if previous >= 1:
            raise InvalidStateError("candidate already used its one allowed revision")
        revision_counts[candidate_key] = previous + 1
    reviews[candidate_digest] = {
        "decision": decision,
        "decision_digest": decision_digest,
        "candidate_key": candidate_key,
        "evidence_digest": evidence_digest,
        **execution_bindings,
        **persisted_revision,
        "leaf_id": work_item["leaf_id"],
        "work_item_id": work_item["work_item_id"],
        "work_item_dependency_digest": work_item["dependency_digest"],
    }
    body["candidate_reviews"] = reviews
    body["revision_counts"] = revision_counts
    updated = _write_state(paths, run_id, body, status="running")
    append_event(
        paths,
        "run_review_recorded",
        {
            "run_id": run_id,
            "work_item_id": work_item["work_item_id"],
            "candidate_digest": candidate_digest,
            "decision": decision,
            "decision_digest": decision_digest,
        },
    )
    return updated
