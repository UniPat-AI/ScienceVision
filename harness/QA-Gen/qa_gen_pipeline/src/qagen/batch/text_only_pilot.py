"""One-slot text-only agent loop for the V32 native conformation pilot.

The generic compiled-selection executor deliberately requires visual model
inputs.  This module is narrower: the viewer has already executed an exact
native measurement twice, and the models receive only bounded text artifacts.
It is not a fallback for other leaves.  The loop is intentionally limited to
one child TargetSpec and stops before human review, staging, or release.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import secrets
import time
from typing import Any, Mapping

import yaml

from ..contracts import canonical_json, digest, make_envelope, verify_envelope
from ..errors import InputError
from ..measurement import MeasurementContractError, validate_target_spec
from ..paths import WorkspacePaths, require_within, safe_component
from ..providers import build_provider_adapters
from ..providers.contracts import MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE
from ..providers.runtime import load_provider_runtime
from ..resource_paths import config_path
from ..storage.atomic import atomic_write_json, atomic_write_yaml, hash_file, read_json
from ..storage.ledger import UsageBudget, append_event
from ..verification import (
    GroundTruthRegistry,
    GroundTruthRegistryError,
    QuestionContractError,
    load_ground_truth_registry,
    load_plan_locked_ground_truth_registry,
    QuestionTextPolicy,
    load_question_text_policy,
    validate_candidate_question_contract,
    validate_question_coverage,
)
from ..workers import (
    ProviderNotConfiguredError,
    TypedWorker,
    WorkerExecutor,
    WorkerPolicyError,
    WorkerRequest,
    WorkerResult,
    load_verified_routing_policy,
    validate_verification_result,
)


_CHILD_TARGET_KIND = "qagen_v32_native_conformation_child_target"
_ANCHOR_KIND = "qagen_v32_native_conformation_preauthor_anchor"
_PILOT_RESULT_KIND = "qagen_v32_native_conformation_pilot_result"
_MANIFEST_KIND = "vnext_selection_batch_manifest"
_INVENTORY_KIND = "qagen_scene_inventory"
_MEASUREMENT_RECEIPT_KIND = "qagen_measurement_receipt"
_SIDECAR_KIND = "qagen_measurement_private_sidecar"
_REPLAY_KIND = "qagen_native_measurement_replay_consistency"
_LEAK_SCAN_KIND = "qagen_native_measurement_leak_scan"
_LOCK_KIND = "qagen_v32_text_only_execution_lock"
_BUNDLE_KIND = "qagen_v32_text_only_execution_bundle"
_PLAN_KIND = "qagen_v32_text_only_execution_plan"
_APPROVAL_KIND = "qagen_v32_text_only_execution_approval"
_EXECUTOR_KIND = "qagen_v32_text_only_executor_receipt"
_PREFLIGHT_KIND = "qagen_v32_text_only_no_network_gate"
_STATE_KIND = "qagen_v32_text_only_execution_state"
_FAILURE_SUMMARY_KIND = "qagen_v32_text_only_execution_failure_summary"
_TARGET_LEAF = "chemistry.conformation_analysis"
_ANSWER_FIELD = "dihedral_angle_deg"
_ANSWER_FORMAT = "signed_numeric_degree_nearest_whole"
_ROLES = ("brief_builder", "author", "truth_builder", "verifier")
_MODEL_ROLES = frozenset({"brief_builder", "author", "verifier"})
_DETERMINISTIC_TRUTH_PROVIDER = "control_plane"
_DETERMINISTIC_TRUTH_MODEL = "locked_measurement_truth_record_v1"
_DETERMINISTIC_TRUTH_WORKER = "deterministic_locked_measurement_truth_record"
_TRUTH_RECORD_KIND = "v32_deterministic_truth_record"
_QUESTION_DRAFT_KIND = "v32_question_draft"
_MAX_INPUT_BYTES = 2 * 1024 * 1024
_TEXT_ONLY_FORBIDDEN = ("data:", "image_url", "base64", "asset_path", "backend_selector_map")
_FREEZE_REASONS = frozenset(
    {
        "author_pretransport_visual_bridge_guard",
        "operator_interruption_after_truth_builder",
        "truth_deterministic_binding_failed_before_verifier",
    }
)


class TextOnlyPilotError(InputError):
    """The one-slot text-only pilot cannot prove one of its gates."""


@dataclass(frozen=True)
class TextOnlyPilotInputs:
    """Exact local artifacts used by a single text-only execution."""

    selection_manifest: Path
    child_target: Path
    preauthor_anchor: Path
    pilot_result: Path
    inventory: Path
    first_receipt: Path
    first_sidecar: Path
    first_leak_scan: Path
    second_receipt: Path
    second_sidecar: Path
    second_leak_scan: Path
    replay_consistency: Path


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TextOnlyPilotError(f"{label}_invalid")
    return dict(value)


def _private_file(paths: WorkspacePaths, value: str | Path, label: str) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise TextOnlyPilotError(f"{label}_unavailable")
    try:
        path = require_within(paths.private, raw, label)
        metadata = path.stat()
    except (InputError, OSError) as exc:
        raise TextOnlyPilotError(f"{label}_unavailable") from exc
    if not path.is_file() or metadata.st_size > _MAX_INPUT_BYTES:
        raise TextOnlyPilotError(f"{label}_unavailable")
    return path


def _load_envelope(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    label: str,
    kind: str,
) -> tuple[Path, dict[str, Any]]:
    path = _private_file(paths, value, label)
    try:
        raw = _mapping(read_json(path), label)
        return path, verify_envelope(raw, kind=kind)
    except (InputError, OSError) as exc:
        raise TextOnlyPilotError(f"{label}_invalid") from exc


def _relative(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.resolve().relative_to(paths.root.resolve()).as_posix()
    except ValueError as exc:
        raise TextOnlyPilotError("pilot_artifact_escapes_workspace") from exc


def _artifact_ref(paths: WorkspacePaths, path: Path, envelope: Mapping[str, Any]) -> dict[str, str]:
    return {"path": _relative(paths, path), "content_digest": str(envelope["content_digest"])}


def _load_inputs(paths: WorkspacePaths, inputs: TextOnlyPilotInputs) -> dict[str, Any]:
    """Load and cross-bind the two fresh measurement sessions before any call."""

    manifest_path, manifest = _load_envelope(
        paths, inputs.selection_manifest, label="selection_manifest", kind=_MANIFEST_KIND
    )
    child_path, child = _load_envelope(
        paths, inputs.child_target, label="child_target", kind=_CHILD_TARGET_KIND
    )
    anchor_path, anchor = _load_envelope(
        paths, inputs.preauthor_anchor, label="preauthor_anchor", kind=_ANCHOR_KIND
    )
    result_path, result = _load_envelope(
        paths, inputs.pilot_result, label="pilot_result", kind=_PILOT_RESULT_KIND
    )
    inventory_path, inventory = _load_envelope(
        paths, inputs.inventory, label="scene_inventory", kind=_INVENTORY_KIND
    )
    first_receipt_path, first_receipt = _load_envelope(
        paths, inputs.first_receipt, label="first_measurement_receipt", kind=_MEASUREMENT_RECEIPT_KIND
    )
    first_sidecar_path, first_sidecar = _load_envelope(
        paths, inputs.first_sidecar, label="first_measurement_sidecar", kind=_SIDECAR_KIND
    )
    first_scan_path, first_scan = _load_envelope(
        paths, inputs.first_leak_scan, label="first_leak_scan", kind=_LEAK_SCAN_KIND
    )
    second_receipt_path, second_receipt = _load_envelope(
        paths, inputs.second_receipt, label="second_measurement_receipt", kind=_MEASUREMENT_RECEIPT_KIND
    )
    second_sidecar_path, second_sidecar = _load_envelope(
        paths, inputs.second_sidecar, label="second_measurement_sidecar", kind=_SIDECAR_KIND
    )
    second_scan_path, second_scan = _load_envelope(
        paths, inputs.second_leak_scan, label="second_leak_scan", kind=_LEAK_SCAN_KIND
    )
    replay_path, replay = _load_envelope(
        paths, inputs.replay_consistency, label="replay_consistency", kind=_REPLAY_KIND
    )

    manifest_body = _mapping(manifest.get("body"), "selection_manifest_body")
    child_body = _mapping(child.get("body"), "child_target_body")
    anchor_body = _mapping(anchor.get("body"), "preauthor_anchor_body")
    result_body = _mapping(result.get("body"), "pilot_result_body")
    inventory_body = _mapping(inventory.get("body"), "scene_inventory_body")
    first_receipt_body = _mapping(first_receipt.get("body"), "first_measurement_receipt_body")
    second_receipt_body = _mapping(second_receipt.get("body"), "second_measurement_receipt_body")
    first_sidecar_body = _mapping(first_sidecar.get("body"), "first_measurement_sidecar_body")
    second_sidecar_body = _mapping(second_sidecar.get("body"), "second_measurement_sidecar_body")
    replay_body = _mapping(replay.get("body"), "replay_consistency_body")
    first_scan_body = _mapping(first_scan.get("body"), "first_leak_scan_body")
    second_scan_body = _mapping(second_scan.get("body"), "second_leak_scan_body")
    target = _mapping(child_body.get("target_spec"), "child_target_spec")

    if (
        manifest_body.get("generation_eligible") is not False
        or manifest_body.get("total") != 1
        or manifest_body.get("rows") is None
        or child.get("status") != "preauthor_measurement_only"
        or anchor.get("status") != "preauthor_measurement_only"
        or result.get("status") != "attested"
        or result_body.get("status") != "attested"
        or result_body.get("generation_eligible") is not False
        or result_body.get("release_eligible") is not False
        or target.get("target_leaf") != _TARGET_LEAF
        or target.get("answer_fields") != [_ANSWER_FIELD]
        or target.get("native_operation") != "measure_dihedral"
        or target.get("allowed_native_operations") != ["measure_dihedral"]
        or child_body.get("historical_truth_equivalence") != "not_asserted"
        or anchor_body.get("historical_truth_equivalence") != "not_asserted"
    ):
        raise TextOnlyPilotError("pilot_scope_or_generation_gate_invalid")

    try:
        checked_target = validate_target_spec(
            target,
            inventory=inventory_body,
            inventory_digest=str(inventory["content_digest"]),
        )
    except MeasurementContractError as exc:
        raise TextOnlyPilotError("child_target_does_not_bind_inventory") from exc
    if (
        child_body.get("child_target_spec_digest") != digest(checked_target)
        or anchor_body.get("child_target_artifact_digest") != child["content_digest"]
        or anchor_body.get("child_target_spec_digest") != digest(checked_target)
        or result_body.get("anchor_digest") != anchor["content_digest"]
        or result_body.get("child_target_spec_digest") != digest(checked_target)
    ):
        raise TextOnlyPilotError("child_target_anchor_binding_invalid")

    expected_sessions = (
        ("first", first_receipt, first_receipt_body, first_sidecar, first_sidecar_body, first_scan, first_scan_body),
        ("second", second_receipt, second_receipt_body, second_sidecar, second_sidecar_body, second_scan, second_scan_body),
    )
    for label, receipt, receipt_body, sidecar, sidecar_body, scan, scan_body in expected_sessions:
        measurement = _mapping(result_body.get("measurements"), "pilot_measurements").get(label)
        if not isinstance(measurement, Mapping):
            raise TextOnlyPilotError("pilot_measurement_binding_invalid")
        result_value = _mapping(sidecar_body.get("measurement_result"), f"{label}_measurement_result")
        tolerance = _mapping(sidecar_body.get("measurement_tolerance"), f"{label}_measurement_tolerance")
        truth = _mapping(sidecar_body.get("truth"), f"{label}_measurement_truth")
        value = result_value.get("value")
        absolute = tolerance.get("absolute")
        if (
            measurement.get("measurement_receipt_digest") != receipt["content_digest"]
            or measurement.get("private_sidecar_digest") != sidecar["content_digest"]
            or receipt_body.get("private_sidecar_digest") != sidecar["content_digest"]
            or receipt_body.get("target_spec_digest") != digest(checked_target)
            or receipt_body.get("measurement_request_digest") != sidecar_body.get("measurement_request_digest")
            or scan.get("status") != "passed"
            or scan_body.get("status") != "passed"
            or scan_body.get("measurement_receipt_digest") != receipt["content_digest"]
            or scan_body.get("private_sidecar_digest") != sidecar["content_digest"]
            or result_value.get("unit") != "degree"
            or tolerance.get("unit") != "degree"
            or truth.get(_ANSWER_FIELD) != value
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or isinstance(absolute, bool)
            or not isinstance(absolute, (int, float))
            or float(absolute) <= 0
        ):
            raise TextOnlyPilotError("measurement_session_binding_invalid")

    if (
        first_receipt_body.get("measurement_request_digest") != second_receipt_body.get("measurement_request_digest")
        or first_receipt_body.get("viewer_session_digest") == second_receipt_body.get("viewer_session_digest")
        or first_sidecar_body["measurement_result"] != second_sidecar_body["measurement_result"]
        or first_sidecar_body["measurement_tolerance"] != second_sidecar_body["measurement_tolerance"]
        or replay.get("status") != "passed"
        or replay_body.get("status") != "passed"
        or replay_body.get("first_measurement_receipt_digest") != first_receipt["content_digest"]
        or replay_body.get("second_measurement_receipt_digest") != second_receipt["content_digest"]
        or replay_body.get("first_private_sidecar_digest") != first_sidecar["content_digest"]
        or replay_body.get("second_private_sidecar_digest") != second_sidecar["content_digest"]
    ):
        raise TextOnlyPilotError("fresh_session_independence_invalid")

    return {
        "manifest": (manifest_path, manifest),
        "child": (child_path, child),
        "anchor": (anchor_path, anchor),
        "result": (result_path, result),
        "inventory": (inventory_path, inventory),
        "first_receipt": (first_receipt_path, first_receipt),
        "first_sidecar": (first_sidecar_path, first_sidecar),
        "first_scan": (first_scan_path, first_scan),
        "second_receipt": (second_receipt_path, second_receipt),
        "second_sidecar": (second_sidecar_path, second_sidecar),
        "second_scan": (second_scan_path, second_scan),
        "replay": (replay_path, replay),
        "target": checked_target,
    }


def _runtime_config_digest(path: Path) -> str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TextOnlyPilotError("provider_runtime_config_unavailable") from exc
    if not isinstance(raw, Mapping):
        raise TextOnlyPilotError("provider_runtime_config_invalid")
    return digest(dict(raw))


def create_one_slot_provider_runtime(
    paths: WorkspacePaths,
    *,
    run_id: str,
    source_runtime_path: str | Path,
    endpoint: str,
) -> dict[str, str]:
    """Derive an approved one-attempt runtime without mutating checked-in config.

    The existing v32 runtime permits transport retries.  This derived local
    runtime narrows that setting to a single transport attempt so that the
    one-slot pilot's ``max_retries=0`` is true at both stage and HTTP layers.
    The endpoint remains the user-approved HTTPS endpoint; no credential is
    read, persisted, or returned here.
    """

    canonical_run_id = safe_component(run_id, "text-only pilot run id")
    source = Path(source_runtime_path).expanduser().resolve()
    try:
        runtime = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TextOnlyPilotError("source_provider_runtime_unavailable") from exc
    if not isinstance(runtime, Mapping):
        raise TextOnlyPilotError("source_provider_runtime_invalid")
    runtime = dict(runtime)
    request = runtime.get("request")
    if not isinstance(request, Mapping):
        raise TextOnlyPilotError("source_provider_runtime_invalid")
    runtime["request"] = {**dict(request), "max_attempts": 1, "initial_backoff_seconds": 0.0}
    runtime_root = paths.private / "text-only-agent-pilots" / canonical_run_id / "runtime"
    runtime_path = runtime_root / "provider-runtime.one-slot.yaml"
    atomic_write_yaml(runtime_path, runtime, private=True)
    runtime_digest = digest(runtime)

    approval_root = paths.root / "preproduction" / "text-only-agent-pilots" / canonical_run_id
    approval_path = approval_root / "provider-endpoint-approval.one-slot.yaml"
    approval = {
        "schema_version": 1,
        "kind": "qagen_provider_endpoint_approval",
        "id": f"v32-text-only-one-slot-{canonical_run_id}",
        "status": "approved",
        "decision": "allow_credentialed_https_endpoint",
        "endpoint": endpoint.rstrip("/"),
        "credential_env": "QAGEN_UNIV_API_KEY",
        "runtime_config_digest": runtime_digest,
        "scope": "user-authorized V32 one-slot text-only measurement-replay pilot; no staging, release, or production",
        "decided_by": "user_explicit_generation_authorization",
        "decided_at": "2026-08-27",
        "production_eligible": False,
    }
    atomic_write_yaml(approval_path, approval, private=True)
    return {
        "runtime_path": str(runtime_path),
        "runtime_digest": runtime_digest,
        "approval_path": str(approval_path),
        "approval_digest": digest(approval),
    }


def _pilot_root(paths: WorkspacePaths, run_id: str) -> Path:
    return paths.private / "text-only-agent-pilots" / safe_component(run_id, "text-only pilot run id")


def _write_envelope(path: Path, *, kind: str, identifier: str, status: str, body: Mapping[str, Any]) -> dict[str, Any]:
    envelope = make_envelope(kind=kind, identifier=identifier, status=status, body=body, producer="qagen/0.1.0:v32_text_only_pilot")
    atomic_write_json(path, envelope, private=True)
    return envelope


def _compact_inventory(inventory: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    body = _mapping(inventory.get("body"), "scene_inventory_body")
    catalog = _mapping(body.get("selector_catalog"), "scene_inventory_selector_catalog")
    selector = _mapping(target.get("selector"), "target_selector")
    atom_ids = selector.get("atom_ids")
    if not isinstance(atom_ids, list) or len(atom_ids) != 4 or not all(isinstance(item, str) for item in atom_ids):
        raise TextOnlyPilotError("target_selector_invalid")
    atoms = catalog.get("atoms")
    if not isinstance(atoms, list):
        raise TextOnlyPilotError("scene_inventory_selector_catalog_invalid")
    available = {item.get("atom_id") for item in atoms if isinstance(item, Mapping)}
    if not set(atom_ids).issubset(available):
        raise TextOnlyPilotError("target_selector_is_not_in_compact_inventory")
    return {
        "inventory_digest": inventory["content_digest"],
        "anonymous_scene_ref": target["anonymous_scene_ref"],
        "target_leaf": target["target_leaf"],
        "selector_type": "atom_quadruplet",
        "selected_atom_ids": list(atom_ids),
        "atom_count": len(atoms),
        "allowed_native_operation": "measure_dihedral",
    }


def _schema_registry() -> dict[str, Mapping[str, Any]]:
    try:
        target_schema = read_json(config_path("schemas", "target_spec.schema.json"))
    except (InputError, OSError) as exc:
        raise TextOnlyPilotError("target_spec_schema_unavailable") from exc
    if not isinstance(target_schema, Mapping):
        raise TextOnlyPilotError("target_spec_schema_invalid")
    truth_schema: dict[str, Any] = {
        "type": "object",
        "required": [
            "artifact_kind",
            "candidate_digest",
            "measurement_request_digest",
            "truth",
            "unit",
            "absolute_tolerance",
            "work_item_id",
            "state_transition",
        ],
        "properties": {
            "artifact_kind": {"const": "v32_text_only_truth_receipt"},
            "candidate_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "measurement_request_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "truth": {
                "type": "object",
                "required": [_ANSWER_FIELD],
                "properties": {_ANSWER_FIELD: {"type": "number"}},
                "additionalProperties": False,
            },
            "unit": {"const": "degree"},
            "absolute_tolerance": {"type": "number", "exclusiveMinimum": 0},
            "work_item_id": {"type": "string", "minLength": 1},
            "state_transition": {"type": "null"},
        },
        "additionalProperties": False,
    }
    return {
        "qagen://schemas/task-brief/output/v1": dict(target_schema),
        "qagen://schemas/author-truth-receipt/v1": truth_schema,
    }


def _usage_budget(policy: Any) -> UsageBudget:
    role_limits: dict[str, dict[str, float | int]] = {}
    total_tokens = 0
    total_cost = 0.0
    for role in ("brief_builder", "author", "truth_builder", "evidence_worker", "verifier", "rewriter"):
        route = policy.role(role)
        enabled = role in _MODEL_ROLES
        tokens = int(route["max_tokens"]) if enabled else 0
        cost = float(route["max_cost_usd"]) if enabled else 0.0
        role_limits[role] = {"max_tokens": tokens, "max_cost_usd": cost}
        total_tokens += tokens
        total_cost += cost
    return UsageBudget.from_value(
        {
            "total": {"max_tokens": total_tokens, "max_cost_usd": total_cost},
            "roles": role_limits,
        }
    )


def _safe_text_only(value: Mapping[str, Any], label: str) -> None:
    encoded = canonical_json(dict(value)).decode("utf-8").casefold()
    if any(marker in encoded for marker in _TEXT_ONLY_FORBIDDEN):
        raise TextOnlyPilotError(f"{label}_contains_non_text_or_private_transport_fields")


def _numeric_answer(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise TextOnlyPilotError(f"{label}_invalid")
    return float(value)


def _nearest_whole_degree(value: float) -> float:
    """Round a signed degree value half away from zero for the solver contract."""

    if value >= 0:
        return float(math.floor(value + 0.5))
    return float(math.ceil(value - 0.5))


def _measurement_ground_truth_record(
    *,
    work_item_id: str,
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the one deterministic source record approved for this pilot."""

    record_id = f"v32-text-only-measurement-{work_item_id}"
    receipt_digest = str(receipt["content_digest"])
    return {
        "source_type": "measurement_receipt",
        "source_id": "first_native_measurement_receipt",
        "source_digest": receipt_digest,
        "record_id": record_id,
        "record_digest": digest(
            {
                "record_id": record_id,
                "target_spec_digest": digest(dict(target)),
                "measurement_receipt_digest": receipt_digest,
                "private_sidecar_digest": sidecar["content_digest"],
            }
        ),
        "answer_digest": digest(dict(answer)),
        "measurement_receipt_digest": receipt_digest,
    }


def _ground_truth_provenance(record: Mapping[str, Any], registry: GroundTruthRegistry) -> dict[str, Any]:
    """Project an approved record into the private author contract."""

    fields = (
        "source_id",
        "source_digest",
        "record_id",
        "record_digest",
        "answer_digest",
        "measurement_receipt_digest",
        "anonymous_scene_ref",
        "primary_asset_digest",
        "selection_slot_digest",
    )
    return {
        **{field: record[field] for field in fields if field in record},
        "ground_truth_registry_digest": registry.content_digest,
    }


def _registered_measurement_ground_truth_record(
    *,
    registry: GroundTruthRegistry,
    work_item_id: str,
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> dict[str, Any]:
    """Require this exact deterministic measurement record in the plan lock."""

    expected = _measurement_ground_truth_record(
        work_item_id=work_item_id,
        target=target,
        receipt=receipt,
        sidecar=sidecar,
        answer=answer,
    )
    key = (
        expected["source_id"],
        expected["source_digest"],
        expected["record_id"],
        expected["record_digest"],
    )
    registered = registry.records.get(key)
    if registered != expected:
        raise TextOnlyPilotError("text_only_measurement_ground_truth_not_registered")
    return dict(registered)


def _plan_ground_truth_registry(paths: WorkspacePaths, plan: Mapping[str, Any]) -> GroundTruthRegistry:
    try:
        return load_plan_locked_ground_truth_registry(plan, workspace_root=paths.root)
    except GroundTruthRegistryError as exc:
        raise TextOnlyPilotError("text_only_plan_ground_truth_registry_invalid") from exc


def _locked_ground_truth_registry(
    paths: WorkspacePaths,
    *,
    lock: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> GroundTruthRegistry:
    """Ensure the executable lock and immutable plan name the same registry."""

    lock_body = _mapping(lock.get("body"), "execution_lock_body")
    plan_body = _mapping(plan.get("body"), "execution_plan_body")
    lock_ref = _mapping(lock_body.get("ground_truth_registry"), "execution_lock_ground_truth_registry")
    plan_ref = _mapping(plan_body.get("ground_truth_registry"), "execution_plan_ground_truth_registry")
    if lock_ref != plan_ref:
        raise TextOnlyPilotError("text_only_ground_truth_registry_lock_mismatch")
    return _plan_ground_truth_registry(paths, plan)


def _candidate_gate(
    candidate: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    run_id: str,
    work_item_id: str,
    plan_digest: str,
    config_digest: str,
    knowledge_digest: str,
    viewer_profile_digest: str,
    expected_answer: float,
    tolerance: float,
    ground_truth_registry: GroundTruthRegistry,
    question_policy: QuestionTextPolicy | None = None,
) -> None:
    expected_keys = {
        "artifact_kind",
        "task_id",
        "prompt",
        "anonymous_scene_ref",
        "target_spec_digest",
        "viewer_session_request",
        "reference_answer",
        "reasoning",
        "rubric",
        "visual_target",
        "question_contract",
        "provenance",
    }
    if set(candidate) != expected_keys or candidate.get("artifact_kind") != "author_private_candidate":
        raise TextOnlyPilotError("candidate_composer_contract_invalid")
    answer = _mapping(candidate.get("reference_answer"), "candidate_reference_answer")
    session = _mapping(candidate.get("viewer_session_request"), "candidate_viewer_session_request")
    provenance = _mapping(candidate.get("provenance"), "candidate_provenance")
    prompt = candidate.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise TextOnlyPilotError("candidate_prompt_invalid")
    rounded_expected_answer = _nearest_whole_degree(expected_answer)
    forbidden_answer_tokens = {
        str(expected_answer),
        f"{expected_answer:.1f}",
        str(int(rounded_expected_answer)),
    }
    if any(token in prompt for token in forbidden_answer_tokens):
        raise TextOnlyPilotError("candidate_prompt_leaks_measured_answer")
    selected = _mapping(target.get("selector"), "target_selector").get("atom_ids")
    if not isinstance(selected, list) or not all(isinstance(item, str) and item in prompt for item in selected):
        raise TextOnlyPilotError("candidate_prompt_does_not_name_the_controlled_measurement_selector")
    required_scientific_terms = ("dihedral", "degrees", "nearest whole degree")
    normalized_prompt = prompt.casefold()
    if any(term not in normalized_prompt for term in required_scientific_terms):
        raise TextOnlyPilotError("candidate_prompt_does_not_state_the_scientific_measurement_question")
    active_question_policy = question_policy or load_question_text_policy()
    try:
        validate_candidate_question_contract(
            candidate,
            policy=active_question_policy,
            leaf_id=_TARGET_LEAF,
            require_contract=True,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=ground_truth_registry.content_digest,
        )
    except QuestionContractError as exc:
        raise TextOnlyPilotError("candidate_question_contract_invalid") from exc
    actual = _numeric_answer(answer.get(_ANSWER_FIELD), "candidate_reference_answer")
    if (
        set(answer) != {_ANSWER_FIELD}
        or actual != rounded_expected_answer
        or candidate.get("task_id") != run_id
        or candidate.get("anonymous_scene_ref") != target["anonymous_scene_ref"]
        or candidate.get("target_spec_digest") != digest(dict(target))
        or session.get("request_id") != f"{run_id}-solver-viewer"
        or session.get("viewer_profile_digest") != viewer_profile_digest
        or session.get("allowed_operations") != ["rotate", "render", "measure_dihedral"]
        or _mapping(candidate.get("rubric"), "candidate_rubric")
        != {
            "answer_format": _ANSWER_FORMAT,
            "viewer_necessity": "controlled_viewer_measure_dihedral_required",
        }
        or provenance
        != {
            "plan_digest": plan_digest,
            "config_digest": config_digest,
            "knowledge_snapshot_digest": knowledge_digest,
            "work_item_id": work_item_id,
        }
    ):
        raise TextOnlyPilotError("candidate_is_not_bound_to_the_one_slot_execution_lock")


def _question_draft_gate(draft: Mapping[str, Any]) -> None:
    """Reject answer-bearing or control-bearing author text before composition.

    The author has no access to the measurement sidecar. This explicit gate
    retains that boundary even for fixture adapters or a compromised provider
    that returns an otherwise schema-valid object.
    """

    expected_keys = {
        "artifact_kind",
        "scientific_context",
        "object_reference",
        "question_wording",
        "reasoning_focus",
        "state_transition",
    }
    if set(draft) != expected_keys or draft.get("artifact_kind") != _QUESTION_DRAFT_KIND:
        raise TextOnlyPilotError("question_draft_contract_invalid")
    if draft.get("state_transition") is not None:
        raise TextOnlyPilotError("question_draft_attempted_control_plane_action")
    text_fields = ("scientific_context", "object_reference", "question_wording", "reasoning_focus")
    values: list[str] = []
    for field in text_fields:
        value = draft.get(field)
        if not isinstance(value, str) or not value.strip():
            raise TextOnlyPilotError("question_draft_text_invalid")
        values.append(value.strip())
    text = "\n".join(values)
    # Standalone numeric literals can carry an answer. Terms such as sp3 and
    # C4 are permitted because their digits are part of an identifier.
    if re.search(r"(?<![A-Za-z0-9_])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?![A-Za-z0-9_])", text):
        raise TextOnlyPilotError("question_draft_contains_numeric_answer_or_measurement")
    prohibited = (
        "reference answer",
        "answer key",
        "ground truth",
        "measurement result",
        "private sidecar",
        "truth record",
        "viewer_session_request",
        "provenance",
    )
    lowered = text.casefold()
    if any(marker in lowered for marker in prohibited):
        raise TextOnlyPilotError("question_draft_contains_private_or_controlled_content")


def _compose_candidate(
    draft: Mapping[str, Any],
    *,
    target: Mapping[str, Any],
    run_id: str,
    work_item_id: str,
    plan_digest: str,
    config_digest: str,
    knowledge_digest: str,
    viewer_profile_digest: str,
    expected_answer: float,
    question_policy: QuestionTextPolicy | None = None,
    gt_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach the non-bypassable viewer and truth contract to editorial text."""

    _question_draft_gate(draft)
    selector = _mapping(target.get("selector"), "target_selector")
    atom_ids = selector.get("atom_ids")
    if (
        selector.get("type") != "atom_quadruplet"
        or not isinstance(atom_ids, list)
        or len(atom_ids) != 4
        or not all(isinstance(atom_id, str) and atom_id for atom_id in atom_ids)
    ):
        raise TextOnlyPilotError("candidate_composer_selector_invalid")
    measured_answer = _nearest_whole_degree(
        _numeric_answer(expected_answer, "candidate_composer_expected_answer")
    )
    active_question_policy = question_policy or load_question_text_policy()
    selected_atoms = ", ".join(atom_ids)
    if not isinstance(gt_provenance, Mapping):
        raise TextOnlyPilotError("candidate_composer_gt_provenance_invalid")
    provenance = dict(gt_provenance)
    required_provenance = {
        "source_id",
        "source_digest",
        "record_id",
        "record_digest",
        "answer_digest",
        "measurement_receipt_digest",
        "ground_truth_registry_digest",
    }
    if not required_provenance.issubset(provenance):
        raise TextOnlyPilotError("candidate_composer_gt_provenance_invalid")
    prompt = (
        f"{draft['scientific_context'].strip()}\n\n"
        f"{draft['question_wording'].strip()}\n\n"
        f"For the atom sequence {selected_atoms}, what is the signed dihedral angle in degrees, "
        "rounded to the nearest whole degree?"
    )
    return {
        "artifact_kind": "author_private_candidate",
        "task_id": run_id,
        "prompt": prompt,
        "anonymous_scene_ref": target["anonymous_scene_ref"],
        "target_spec_digest": digest(dict(target)),
        "viewer_session_request": {
            "request_id": f"{run_id}-solver-viewer",
            "viewer_profile_digest": viewer_profile_digest,
            "allowed_operations": ["rotate", "render", "measure_dihedral"],
        },
        "reference_answer": {_ANSWER_FIELD: measured_answer},
        "reasoning": "The locked native measurement is the private answer authority; the solver must use the controlled viewer measurement operation.",
        "rubric": {
            "answer_format": _ANSWER_FORMAT,
            "viewer_necessity": "controlled_viewer_measure_dihedral_required",
        },
        "visual_target": {
            "scientific_context": draft["scientific_context"].strip(),
            "object_reference": draft["object_reference"].strip(),
            "question_wording": draft["question_wording"].strip(),
            "reasoning_focus": draft["reasoning_focus"].strip(),
            "question_draft_digest": digest(dict(draft)),
            "selector_type": "atom_quadruplet",
            "highlighted_atom_ids": list(atom_ids),
        },
        "question_contract": {
            "question_family_kind": "measurement",
            "question_kernel": "measure signed dihedral for the locked atom quadruplet",
            "question_text_policy_digest": active_question_policy.content_digest,
            "gt_answer_contract": {
                "source_type": "measurement_receipt",
                "gt_shape": "scalar_numeric",
                "answer_fields": [_ANSWER_FIELD],
                "provenance": provenance,
            },
            "asset_facets": {
                "body_region": [],
                "organ_system": [],
                "anatomy": [],
                "pathology": [],
                "modality": ["molecular_3d"],
                "dataset": ["v32_native_conformation_private"],
            },
        },
        "provenance": {
            "plan_digest": plan_digest,
            "config_digest": config_digest,
            "knowledge_snapshot_digest": knowledge_digest,
            "work_item_id": work_item_id,
        },
    }


def _truth_materialization_input(
    *,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    work_item_id: str,
    purpose: str,
) -> dict[str, Any]:
    """Describe deterministic truth construction without putting truth in a prompt."""

    receipt_body = _mapping(receipt.get("body"), "measurement_receipt_body")
    return {
        "artifact_kind": "v32_deterministic_truth_materialization_input",
        "materializer": _DETERMINISTIC_TRUTH_WORKER,
        "purpose": purpose,
        "candidate_digest": digest(dict(candidate)),
        "target_spec_digest": digest(dict(target)),
        "selector_digest": digest(_mapping(target.get("selector"), "target_selector")),
        "measurement_request_digest": receipt_body["measurement_request_digest"],
        "measurement_receipt_digest": receipt["content_digest"],
        "private_sidecar_digest": sidecar["content_digest"],
        "work_item_id": work_item_id,
    }


def _materialize_truth_record(
    *,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    work_item_id: str,
    purpose: str,
) -> dict[str, Any]:
    """Mechanically bind a private measurement to one candidate and selector.

    This is deliberately control-plane code, not a model instruction. The
    resulting private TruthRecord is the only authority for an answer value.
    """

    if purpose not in {"author_truth", "verifier_independent_evidence"}:
        raise TextOnlyPilotError("deterministic_truth_purpose_invalid")
    receipt_body = _mapping(receipt.get("body"), "measurement_receipt_body")
    sidecar_body = _mapping(sidecar.get("body"), "measurement_sidecar_body")
    selector = _mapping(target.get("selector"), "target_selector")
    result = _mapping(sidecar_body.get("measurement_result"), "measurement_result")
    tolerance = _mapping(sidecar_body.get("measurement_tolerance"), "measurement_tolerance")
    declared_truth = _mapping(sidecar_body.get("truth"), "measurement_truth")
    answer = _numeric_answer(result.get("value"), "measurement_result_value")
    absolute_tolerance = _numeric_answer(tolerance.get("absolute"), "measurement_tolerance_absolute")
    declared_answer = _numeric_answer(declared_truth.get(_ANSWER_FIELD), "measurement_truth_answer")
    if (
        set(selector) != {"type", "atom_ids"}
        or selector.get("type") != "atom_quadruplet"
        or not isinstance(selector.get("atom_ids"), list)
        or len(selector["atom_ids"]) != 4
        or not all(isinstance(value, str) and value for value in selector["atom_ids"])
        or set(declared_truth) != {_ANSWER_FIELD}
        or result.get("unit") != "degree"
        or tolerance.get("unit") != "degree"
        or abs(answer - declared_answer) > absolute_tolerance
        or receipt_body.get("measurement_request_digest") != sidecar_body.get("measurement_request_digest")
        or receipt_body.get("private_sidecar_digest") != sidecar["content_digest"]
        or receipt_body.get("anonymous_scene_ref") != target.get("anonymous_scene_ref")
    ):
        raise TextOnlyPilotError("locked_measurement_cannot_materialize_truth_record")
    return {
        "artifact_kind": _TRUTH_RECORD_KIND,
        "materializer_version": 1,
        "materializer": _DETERMINISTIC_TRUTH_WORKER,
        "purpose": purpose,
        "candidate_digest": digest(dict(candidate)),
        "target_spec_digest": digest(dict(target)),
        "selector": dict(selector),
        "selector_digest": digest(selector),
        "measurement_request_digest": receipt_body["measurement_request_digest"],
        "measurement_receipt_digest": receipt["content_digest"],
        "private_sidecar_digest": sidecar["content_digest"],
        "viewer_receipt_digest": receipt_body["viewer_receipt_digest"],
        "measurement_result_digest": digest(result),
        "measurement_tolerance_digest": digest(tolerance),
        # The native result remains bound by ``measurement_result_digest``.
        # Solver-facing truth follows the same explicit nearest-whole-degree
        # response contract as the Candidate.
        "truth": {_ANSWER_FIELD: _nearest_whole_degree(answer)},
        "unit": "degree",
        "absolute_tolerance": absolute_tolerance,
        "answer_format": _ANSWER_FORMAT,
        "work_item_id": work_item_id,
        "state_transition": None,
    }


def _truth_gate(
    truth: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    work_item_id: str,
) -> None:
    expected = _materialize_truth_record(
        candidate=candidate,
        target=target,
        receipt=receipt,
        sidecar=sidecar,
        work_item_id=work_item_id,
        purpose="author_truth",
    )
    if dict(truth) != expected:
        raise TextOnlyPilotError("deterministic_truth_record_does_not_match_locked_measurement")


def _verifier_gate(
    verification: Mapping[str, Any],
    *,
    candidate_digest: str,
    evidence_digest: str,
    rubric_lock_digest: str,
    work_item_id: str,
    handoff_digest: str,
    receipt_digests: list[str],
) -> None:
    try:
        checked = validate_verification_result(verification, require_pass=True)
    except InputError as exc:
        raise TextOnlyPilotError("verifier_output_invalid") from exc
    cited = _mapping(checked.get("attested_evidence"), "verifier_attested_evidence")
    forbidden_claims = ("i viewed", "i inspected an image", "i saw the render", "visual input was provided")
    details = " ".join(
        str(item.get("details", ""))
        for item in checked.get("checks", [])
        if isinstance(item, Mapping)
    ).casefold()
    if (
        checked.get("candidate_digest") != candidate_digest
        or checked.get("evidence_digest") != evidence_digest
        or checked.get("rubric_lock_digest") != rubric_lock_digest
        or checked.get("work_item_id") != work_item_id
        or cited != {"handoff_digest": handoff_digest, "receipt_digests": receipt_digests}
        or any(claim in details for claim in forbidden_claims)
    ):
        raise TextOnlyPilotError("verifier_output_is_not_independent_measurement_replay_text_only")


def _stage_output_path(root: Path, role: str) -> Path:
    return root / "outputs" / f"{safe_component(role, 'worker role')}.json"


def _candidate_path(root: Path) -> Path:
    """Keep a deterministic Candidate separate from the author QuestionDraft."""

    return root / "outputs" / "candidate.json"


def _stage_input_path(root: Path, role: str) -> Path:
    return root / "inputs" / f"{safe_component(role, 'worker role')}.json"


def _stage_prompt_path(root: Path, role: str) -> Path:
    return root / "prompts" / f"{safe_component(role, 'worker role')}.json"


def _persist_prompt_snapshot(
    root: Path,
    *,
    role: str,
    adapter: TypedWorker,
    request: WorkerRequest,
) -> dict[str, Any]:
    """Persist an exact private prompt for real HTTP adapters when available."""

    materialize = getattr(adapter, "_prompt_material", None)
    if callable(materialize):
        output_schema, visual_inputs, system, user = materialize(request)
        if visual_inputs:
            raise TextOnlyPilotError("text_only_worker_prompt_attempted_visual_transport")
        snapshot: dict[str, Any] = {
            "role": role,
            "provider": request.provider,
            "model": request.model,
            "system": system,
            "user": user,
            "output_schema": dict(output_schema),
            "visual_input_count": 0,
        }
        exact = True
    else:
        # Test adapters can implement the typed worker contract without
        # exposing their renderer. The actual HTTP adapters above do expose
        # it; retaining the full input still makes the fixture audit useful.
        snapshot = {
            "role": role,
            "provider": request.provider,
            "model": request.model,
            "input_contract": dict(request.input_contract),
            "renderer": "not_exposed_by_adapter",
        }
        exact = False
    path = _stage_prompt_path(root, role)
    atomic_write_json(path, snapshot, private=True)
    return {"path": path.name, "content_digest": digest(snapshot), "exact_rendered_prompt": exact}


def _usage_settlement_reference_for_binding(
    paths: WorkspacePaths,
    *,
    run_id: str,
    role: str,
    provider: str,
    model: str,
    input_digest: str,
    output_digest: str,
) -> dict[str, Any]:
    """Find one completed append-only settlement without exposing its content."""

    try:
        records = [json.loads(line) for line in paths.usage.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise TextOnlyPilotError("usage_ledger_unavailable") from exc
    matches: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping) or record.get("kind") != "usage_settlement":
            continue
        body = record.get("body")
        if not isinstance(body, Mapping):
            continue
        if (
            body.get("run_id") == run_id
            and body.get("worker") == role
            and body.get("provider") == provider
            and body.get("model") == model
            and body.get("input_contract_digest") == input_digest
            and body.get("output_contract_digest") == output_digest
            and record.get("status") == "completed"
        ):
            matches.append(dict(record))
    if len(matches) != 1:
        raise TextOnlyPilotError("usage_settlement_reference_is_missing_or_ambiguous")
    settlement = matches[0]
    body = _mapping(settlement.get("body"), "usage_settlement_body")
    return {
        "path": _relative(paths, paths.usage),
        "id": str(settlement["id"]),
        "content_digest": str(settlement["content_digest"]),
        "token_in": int(body["token_in"]),
        "token_out": int(body["token_out"]),
        "cost_usd": float(body["settled_cost_usd"]),
        "latency_ms": float(body["latency_ms"]),
        "cost_accounting": str(body["cost_accounting"]),
    }


def _usage_settlement_reference(paths: WorkspacePaths, result: WorkerResult, *, run_id: str) -> dict[str, Any]:
    """Find the exact append-only settlement created by WorkerExecutor."""

    return _usage_settlement_reference_for_binding(
        paths,
        run_id=run_id,
        role=result.role,
        provider=result.provider,
        model=result.model,
        input_digest=result.input_contract_digest,
        output_digest=result.output_contract_digest,
    )


def _locked_runtime(
    lock_body: Mapping[str, Any],
    *,
    runtime_config_path: str | Path,
    environ: Mapping[str, str] | None,
):
    """Load the credentialed runtime locally and bind its approval before HTTP."""

    try:
        settings = load_provider_runtime(
            runtime_config_path,
            environ=environ,
            expected_config_digest=str(lock_body["runtime_config_digest"]),
        )
    except InputError as exc:
        raise TextOnlyPilotError("locked_provider_runtime_invalid") from exc
    if (
        settings.endpoint_approval_digest != lock_body.get("endpoint_approval_digest")
        or settings.max_attempts != 1
        or settings.initial_backoff_seconds != 0.0
    ):
        raise TextOnlyPilotError("locked_provider_runtime_does_not_preserve_one_slot_retry_limits")
    return settings


def _write_state(root: Path, *, run_id: str, body: Mapping[str, Any], status: str) -> dict[str, Any]:
    """Persist one state whose envelope and body status cannot diverge.

    The state body is deliberately mutable within a live invocation. Updating
    it here keeps the in-memory state aligned with the atomically written
    envelope for the next stage as well as for a process restart.
    """

    if isinstance(body, dict):
        body["status"] = status
        state_body = body
    else:
        state_body = dict(body)
        state_body["status"] = status
    return _write_envelope(
        root / "state.json",
        kind=_STATE_KIND,
        identifier=f"{run_id}-state",
        status=status,
        body=state_body,
    )


def _completed_output(root: Path, state_body: Mapping[str, Any], role: str) -> dict[str, Any] | None:
    """Load one committed stage output when resuming after a local pre-call gate."""

    stages = _mapping(state_body.get("stages"), "pilot_state_stages")
    stage = _mapping(stages.get(role), f"{role}_stage")
    status = stage.get("status")
    if status == "not_started":
        return None
    deterministic_truth = role == "truth_builder"
    expected_calls = 0 if deterministic_truth else 1
    if (
        status != "completed"
        or stage.get("attempts") != 1
        or stage.get("provider_calls") != expected_calls
        or (deterministic_truth and stage.get("worker_kind") != _DETERMINISTIC_TRUTH_WORKER)
    ):
        raise TextOnlyPilotError("one_slot_run_contains_a_nonresumable_stage")
    expected_path = _stage_output_path(root, role)
    if stage.get("output_path") != expected_path.name:
        raise TextOnlyPilotError("completed_stage_output_reference_invalid")
    try:
        output = _mapping(read_json(expected_path), f"{role}_output")
    except (InputError, OSError) as exc:
        raise TextOnlyPilotError("completed_stage_output_unavailable") from exc
    if digest(output) != stage.get("output_digest"):
        raise TextOnlyPilotError("completed_stage_output_changed")
    return output


def _validate_resumable_stage_order(state_body: Mapping[str, Any]) -> None:
    stages = _mapping(state_body.get("stages"), "pilot_state_stages")
    saw_not_started = False
    for role in _ROLES:
        stage = _mapping(stages.get(role), f"{role}_stage")
        status = stage.get("status")
        if status not in {"not_started", "completed"}:
            raise TextOnlyPilotError("one_slot_run_contains_a_terminal_or_running_stage")
        if status == "not_started":
            saw_not_started = True
        elif saw_not_started:
            raise TextOnlyPilotError("one_slot_run_stage_order_is_not_prefix_complete")


def prepare_text_only_pilot(
    paths: WorkspacePaths,
    *,
    inputs: TextOnlyPilotInputs,
    routing_policy_path: str | Path,
    runtime_config_path: str | Path,
    endpoint_approval_path: str | Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Create immutable execution artifacts without contacting any provider."""

    paths.ensure()
    loaded = _load_inputs(paths, inputs)
    routing_path = Path(routing_policy_path).expanduser().resolve()
    runtime_path = Path(runtime_config_path).expanduser().resolve()
    approval_path = Path(endpoint_approval_path).expanduser().resolve()
    if not routing_path.is_file() or not runtime_path.is_file() or not approval_path.is_file():
        raise TextOnlyPilotError("routing_or_runtime_artifact_unavailable")
    try:
        policy = load_verified_routing_policy(routing_path, schema_registry=_schema_registry())
    except (InputError, WorkerPolicyError) as exc:
        raise TextOnlyPilotError("routing_policy_invalid") from exc
    if not policy.local_pilot_only:
        raise TextOnlyPilotError("routing_policy_is_not_an_approved_local_pilot")
    try:
        question_policy = load_question_text_policy()
    except QuestionContractError as exc:
        raise TextOnlyPilotError("question_text_policy_invalid") from exc
    raw_run_id = run_id or f"v32-text-only-{_short(str(loaded['child'][1]['content_digest']))}-{secrets.token_hex(4)}"
    canonical_run_id = safe_component(raw_run_id, "text-only pilot run id")
    root = _pilot_root(paths, canonical_run_id)
    # The tightened runtime is intentionally materialized first under
    # ``runtime/``.  It is the only pre-existing content a new execution may
    # inherit; any execution artifact means this run id has already been used.
    if root.exists() and any(
        (root / name).exists()
        for name in (
            "execution-lock.json",
            "execution-bundle.json",
            "execution-plan.json",
            "execution-approval.json",
            "executor-receipt.json",
            "state.json",
        )
    ):
        raise TextOnlyPilotError("text_only_pilot_run_already_exists")
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass

    target = loaded["target"]
    inventory = loaded["inventory"][1]
    compact_inventory = _compact_inventory(inventory, target)
    config_digest = hash_file(config_path("subdomains", "chemistry", "conformation_analysis.yaml"))
    knowledge_boundary = {
        "kind": "v32_text_only_measurement_boundary",
        "target_leaf": _TARGET_LEAF,
        "truth_scope": "native_dihedral_measurement_only",
        "historical_truth_equivalence": "not_asserted",
        "forbidden_inference": ["conformation_class", "torsion_sign", "product_prediction"],
        "release_eligible": False,
    }
    knowledge_digest = digest(knowledge_boundary)
    viewer_profile = {
        "viewer_family": "molstar",
        "mode": "controlled_native_measurement_text_only",
        "allowed_operations": ["rotate", "render", "measure_dihedral"],
        "release_eligible": False,
    }
    viewer_profile_digest = digest(viewer_profile)
    work_item_id = f"conformation-{_short(str(loaded['child'][1]['content_digest']))}"
    first_receipt = loaded["first_receipt"][1]
    first_sidecar = loaded["first_sidecar"][1]
    first_sidecar_body = _mapping(first_sidecar.get("body"), "first_measurement_sidecar_body")
    first_measurement = _mapping(first_sidecar_body.get("measurement_result"), "first_measurement_result")
    answer = {
        _ANSWER_FIELD: _nearest_whole_degree(
            _numeric_answer(first_measurement.get("value"), "first_measurement_value")
        )
    }
    record = _measurement_ground_truth_record(
        work_item_id=work_item_id,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        answer=answer,
    )
    registry_path = root / "ground-truth-registry.json"
    registry_document = {
        "schema_version": 1,
        "kind": "qagen_ground_truth_registry",
        "id": f"{canonical_run_id}-ground-truth-registry",
        "source_status": "approved",
        "record_count": 1,
        "approved_snapshot": {"records": [record]},
    }
    atomic_write_json(registry_path, registry_document, private=True)
    try:
        ground_truth_registry = load_ground_truth_registry(registry_path)
    except GroundTruthRegistryError as exc:
        raise TextOnlyPilotError("text_only_ground_truth_registry_invalid") from exc
    _registered_measurement_ground_truth_record(
        registry=ground_truth_registry,
        work_item_id=work_item_id,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        answer=answer,
    )
    ground_truth_registry_ref = {
        "path": _relative(paths, registry_path),
        "content_digest": ground_truth_registry.content_digest,
    }
    artifact_refs: dict[str, dict[str, str]] = {}
    for name, value in loaded.items():
        if name == "target":
            continue
        path, envelope = value
        artifact_refs[name] = _artifact_ref(paths, path, envelope)
    lock_body = {
        "lock_version": 1,
        "execution_eligible": True,
        "selection_generation_eligible": False,
        "selection_manifest_digest": loaded["manifest"][1]["content_digest"],
        "scope": "single V32 chemistry.conformation_analysis measurement-replay text-only pilot",
        "work_item_id": work_item_id,
        "target_spec_digest": digest(target),
        "stage_order": list(_ROLES),
        "stage_workers": {
            role: (
                {
                    "worker_kind": "deterministic",
                    "provider": _DETERMINISTIC_TRUTH_PROVIDER,
                    "model": _DETERMINISTIC_TRUTH_MODEL,
                    "materializer": _DETERMINISTIC_TRUTH_WORKER,
                }
                if role == "truth_builder"
                else {"worker_kind": "model"}
            )
            for role in _ROLES
        },
        "stage_retry_max": 0,
        "transport_attempt_max": 1,
        "rewriter_enabled": False,
        "staging_enabled": False,
        "release_eligible": False,
        "historical_truth_equivalence": "not_asserted",
        "artifacts": artifact_refs,
        "routing_policy_digest": policy.content_digest,
        "question_text_policy_digest": question_policy.content_digest,
        "question_text_policy_id": question_policy.identifier,
        "question_coverage_override": {
            "approval_status": "approved",
            "approval_digest": digest(
                {
                    "scope": "single V32 chemistry.conformation_analysis measurement-replay text-only pilot",
                    "policy_digest": question_policy.content_digest,
                    "reason": "explicit one-slot local pilot; not a default generation profile",
                }
            ),
            "measurement_max_fraction": 1.0,
            "reasoning_min_fraction": 0.0,
        },
        "runtime_config_digest": _runtime_config_digest(runtime_path),
        "endpoint_approval_digest": digest(yaml.safe_load(approval_path.read_text(encoding="utf-8"))),
        "config_digest": config_digest,
        "knowledge_boundary_digest": knowledge_digest,
        "viewer_profile_digest": viewer_profile_digest,
        "ground_truth_registry": ground_truth_registry_ref,
    }
    lock = _write_envelope(root / "execution-lock.json", kind=_LOCK_KIND, identifier=f"{canonical_run_id}-lock", status="approved_for_one_slot_local_pilot", body=lock_body)
    bundle_body = {
        "bundle_version": 1,
        "execution_lock_digest": lock["content_digest"],
        "public_brief_input": {
            "child_target": target,
            "compact_inventory": compact_inventory,
            "measurement_capability": {
                "native_operation": "measure_dihedral",
                "answer_fields": [_ANSWER_FIELD],
                "unit": "degree",
                "text_only": True,
            },
        },
        "author_truth_input_policy": "first_private_sidecar_to_deterministic_truth_record",
        "verifier_input_policy": "candidate_plus_second_session_deterministic_truth_record_text_only",
        "no_visual_input": True,
        "knowledge_boundary": knowledge_boundary,
        "viewer_profile": viewer_profile,
    }
    bundle = _write_envelope(root / "execution-bundle.json", kind=_BUNDLE_KIND, identifier=f"{canonical_run_id}-bundle", status="locked", body=bundle_body)
    plan_body = {
        "plan_version": 1,
        "execution_lock_digest": lock["content_digest"],
        "bundle_digest": bundle["content_digest"],
        "run_id": canonical_run_id,
        "work_item_id": work_item_id,
        "routing_policy_digest": policy.content_digest,
        "runtime_config_digest": lock_body["runtime_config_digest"],
        "endpoint_approval_digest": lock_body["endpoint_approval_digest"],
        "ground_truth_registry": ground_truth_registry_ref,
        "stages": [
            (
                {
                    "role": "truth_builder",
                    "worker_kind": "deterministic",
                    "provider": _DETERMINISTIC_TRUTH_PROVIDER,
                    "model": _DETERMINISTIC_TRUTH_MODEL,
                    "materializer": _DETERMINISTIC_TRUTH_WORKER,
                }
                if role == "truth_builder"
                else {
                    "role": role,
                    "worker_kind": "model",
                    "provider": policy.role(role)["provider"],
                    "model": policy.role(role)["allowed_models"][0],
                }
            )
            for role in _ROLES
        ],
        "retry_policy": {"max_retries": 0, "transport_max_attempts": 1},
        "stop_after": "human_review",
        "staging_enabled": False,
        "release_eligible": False,
    }
    plan = _write_envelope(root / "execution-plan.json", kind=_PLAN_KIND, identifier=f"{canonical_run_id}-plan", status="approved_for_execution", body=plan_body)
    approval_body = {
        "decision": "approved",
        "approval_basis": "user_explicit_generation_authorization",
        "plan_digest": plan["content_digest"],
        "execution_lock_digest": lock["content_digest"],
        "decision_scope": "one text-only slot only; must stop before human review; no staging or release",
        "review_required_after_execution": True,
    }
    approval = _write_envelope(root / "execution-approval.json", kind=_APPROVAL_KIND, identifier=f"{canonical_run_id}-approval", status="approved", body=approval_body)
    executor_body = {
        "executor_version": 1,
        "execution_lock_digest": lock["content_digest"],
        "source_digest": hash_file(Path(__file__)),
        "stage_retry_max": 0,
        "transport_attempt_max": 1,
        "text_only": True,
        "staging_enabled": False,
        "release_eligible": False,
    }
    executor_receipt = _write_envelope(root / "executor-receipt.json", kind=_EXECUTOR_KIND, identifier=f"{canonical_run_id}-executor", status="ready", body=executor_body)
    initial_state = {
        "run_id": canonical_run_id,
        "work_item_id": work_item_id,
        "execution_lock_digest": lock["content_digest"],
        "plan_digest": plan["content_digest"],
        "approval_digest": approval["content_digest"],
        "executor_receipt_digest": executor_receipt["content_digest"],
        "status": "prepared_pending_no_network_gate",
        "staging_enabled": False,
        "release_eligible": False,
        "stages": {
            role: {"status": "not_started", "attempts": 0, "provider_calls": 0}
            for role in _ROLES
        },
        "usage": {"token_in": 0, "token_out": 0, "cost_usd": 0.0},
    }
    state = _write_state(root, run_id=canonical_run_id, body=initial_state, status="prepared_pending_no_network_gate")
    return {
        "run_id": canonical_run_id,
        "root": str(root),
        "lock_path": str(root / "execution-lock.json"),
        "lock_digest": lock["content_digest"],
        "bundle_path": str(root / "execution-bundle.json"),
        "plan_path": str(root / "execution-plan.json"),
        "plan_digest": plan["content_digest"],
        "approval_path": str(root / "execution-approval.json"),
        "executor_receipt_path": str(root / "executor-receipt.json"),
        "ground_truth_registry_path": str(registry_path),
        "ground_truth_registry_digest": ground_truth_registry.content_digest,
        "state_path": str(root / "state.json"),
        "state_digest": state["content_digest"],
    }


def _load_prepared(paths: WorkspacePaths, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = _pilot_root(paths, run_id)
    if not root.is_dir() or root.is_symlink():
        raise TextOnlyPilotError("text_only_pilot_run_unavailable")
    def load(name: str, kind: str) -> dict[str, Any]:
        try:
            return verify_envelope(_mapping(read_json(root / name), name), kind=kind)
        except (InputError, OSError) as exc:
            raise TextOnlyPilotError("prepared_text_only_pilot_artifact_invalid") from exc
    lock = load("execution-lock.json", _LOCK_KIND)
    bundle = load("execution-bundle.json", _BUNDLE_KIND)
    plan = load("execution-plan.json", _PLAN_KIND)
    state = load("state.json", _STATE_KIND)
    return root, lock, bundle, plan, state


def _run_envelope(root: Path, name: str, kind: str) -> dict[str, Any]:
    try:
        return verify_envelope(_mapping(read_json(root / name), name), kind=kind)
    except (InputError, OSError) as exc:
        raise TextOnlyPilotError("prepared_text_only_pilot_artifact_invalid") from exc


def _assert_executor_identity(root: Path, lock: Mapping[str, Any]) -> dict[str, Any]:
    """Prevent a prepared run from crossing a control-plane source change."""

    receipt = _run_envelope(root, "executor-receipt.json", _EXECUTOR_KIND)
    body = _mapping(receipt.get("body"), "executor_receipt_body")
    if (
        body.get("execution_lock_digest") != lock.get("content_digest")
        or body.get("source_digest") != hash_file(Path(__file__))
        or body.get("stage_retry_max") != 0
        or body.get("transport_attempt_max") != 1
        or body.get("text_only") is not True
        or body.get("staging_enabled") is not False
        or body.get("release_eligible") is not False
    ):
        raise TextOnlyPilotError("executor_identity_does_not_match_the_locked_source")
    return receipt


def _json_artifact_reference(paths: WorkspacePaths, path: Path, label: str) -> dict[str, str]:
    try:
        value = _mapping(read_json(path), label)
    except (InputError, OSError) as exc:
        raise TextOnlyPilotError(f"{label}_unavailable") from exc
    return {"path": _relative(paths, path), "content_digest": digest(value)}


def _plan_route(plan: Mapping[str, Any], role: str) -> tuple[str, str]:
    plan_body = _mapping(plan.get("body"), "execution_plan_body")
    stages = plan_body.get("stages")
    if not isinstance(stages, list):
        raise TextOnlyPilotError("execution_plan_stages_invalid")
    matches = [item for item in stages if isinstance(item, Mapping) and item.get("role") == role]
    if len(matches) != 1:
        raise TextOnlyPilotError("execution_plan_role_missing")
    route = _mapping(matches[0], "execution_plan_stage")
    provider = route.get("provider")
    model = route.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        raise TextOnlyPilotError("execution_plan_route_invalid")
    return provider, model


def freeze_incomplete_text_only_pilot(
    paths: WorkspacePaths,
    *,
    run_id: str,
    reason: str = "author_pretransport_visual_bridge_guard",
) -> dict[str, Any]:
    """Freeze an interrupted historical run without replaying any stage.

    This is intentionally a one-way remediation for the pre-state-commit
    implementation defect. It proves what was durable, marks the control
    plane terminal, and keeps the private model prompt/output only as local
    references. It never reconstructs a completed stage or invokes a model.
    """

    if reason not in _FREEZE_REASONS:
        raise TextOnlyPilotError("text_only_freeze_reason_is_not_approved")
    root, lock, bundle, plan, state = _load_prepared(paths, run_id)
    state_body = _mapping(state.get("body"), "execution_state_body")
    stages = _mapping(state_body.get("stages"), "execution_state_stages")
    brief_stage = _mapping(stages.get("brief_builder"), "brief_builder_stage")
    if (
        state.get("status") not in {"live_running", "ready_for_live_execution"}
        or state_body.get("status") not in {"live_running", "ready_for_live_execution"}
        or brief_stage.get("status") != "running"
        or brief_stage.get("attempts") != 1
        or any(
            _mapping(stages.get(role), f"{role}_stage").get("status") != "not_started"
            for role in ("author", "truth_builder", "verifier")
        )
    ):
        raise TextOnlyPilotError("text_only_run_is_not_the_expected_interrupted_shape")

    approval = _run_envelope(root, "execution-approval.json", _APPROVAL_KIND)
    executor_receipt = _run_envelope(root, "executor-receipt.json", _EXECUTOR_KIND)
    gate = _run_envelope(root, "no-network-gate.json", _PREFLIGHT_KIND)
    brief_input_path = _stage_input_path(root, "brief_builder")
    brief_prompt_path = _stage_prompt_path(root, "brief_builder")
    brief_output_path = _stage_output_path(root, "brief_builder")
    brief_input = _json_artifact_reference(paths, brief_input_path, "brief_builder_input")
    brief_prompt = _json_artifact_reference(paths, brief_prompt_path, "brief_builder_prompt")
    brief_output = _json_artifact_reference(paths, brief_output_path, "brief_builder_output")
    provider, model = _plan_route(plan, "brief_builder")
    settlement = _usage_settlement_reference_for_binding(
        paths,
        run_id=run_id,
        role="brief_builder",
        provider=provider,
        model=model,
        input_digest=brief_input["content_digest"],
        output_digest=brief_output["content_digest"],
    )
    failure_body = {
        "failure_version": 1,
        "run_id": run_id,
        "status": "incomplete_frozen",
        "reason": reason,
        "execution_artifacts": {
            "lock": _artifact_ref(paths, root / "execution-lock.json", lock),
            "bundle": _artifact_ref(paths, root / "execution-bundle.json", bundle),
            "plan": _artifact_ref(paths, root / "execution-plan.json", plan),
            "approval": _artifact_ref(paths, root / "execution-approval.json", approval),
            "executor_receipt": _artifact_ref(paths, root / "executor-receipt.json", executor_receipt),
            "no_network_gate": _artifact_ref(paths, root / "no-network-gate.json", gate),
        },
        "stages": {
            "brief_builder": {
                "status": "uncommitted_completed_frozen",
                "provider_calls": 1,
                "brief_state_committed": False,
                "input": brief_input,
                "prompt": brief_prompt,
                "output": brief_output,
                "usage_settlement": settlement,
            },
            "author": {
                "status": "not_called_pretransport_blocked",
                "provider_calls": 0,
                "reason": reason,
            },
            "truth_builder": {"status": "not_called", "provider_calls": 0},
            "verifier": {"status": "not_called", "provider_calls": 0},
        },
        "staging_enabled": False,
        "release_eligible": False,
        "resume_allowed": False,
    }
    summary = _write_envelope(
        root / "failure-summary.json",
        kind=_FAILURE_SUMMARY_KIND,
        identifier=f"{run_id}-failure-summary",
        status="incomplete_frozen",
        body=failure_body,
    )
    state_body["stages"] = dict(failure_body["stages"])
    state_body["usage"] = {
        "token_in": settlement["token_in"],
        "token_out": settlement["token_out"],
        "cost_usd": settlement["cost_usd"],
    }
    state_body["failure_summary"] = _artifact_ref(paths, root / "failure-summary.json", summary)
    state_body["failed_stage"] = "author"
    state_body["staging_enabled"] = False
    state_body["release_eligible"] = False
    state_body["resume_allowed"] = False
    final = _write_state(root, run_id=run_id, body=state_body, status="incomplete_frozen")
    append_event(
        paths,
        "v32_text_only_pilot_incomplete_frozen",
        {
            "run_id": run_id,
            "reason": reason,
            "brief_usage_settlement_digest": settlement["content_digest"],
            "author_provider_calls": 0,
            "truth_builder_provider_calls": 0,
            "verifier_provider_calls": 0,
        },
    )
    return {
        "run_id": run_id,
        "status": "incomplete_frozen",
        "failure_summary_path": str(root / "failure-summary.json"),
        "failure_summary_digest": summary["content_digest"],
        "state_path": str(root / "state.json"),
        "state_digest": final["content_digest"],
        "resume_allowed": False,
    }


def freeze_committed_interrupted_text_only_pilot(
    paths: WorkspacePaths,
    *,
    run_id: str,
    reason: str = "operator_interruption_after_truth_builder",
) -> dict[str, Any]:
    """Freeze a process-interrupted run after a committed stage prefix.

    A completed prefix is durable proof that those provider calls occurred.
    It must never be relabeled as a zero-call pretransport failure, and it
    must not be resumed after an operator explicitly abandons the run.
    """

    if reason not in {
        "operator_interruption_after_truth_builder",
        "truth_deterministic_binding_failed_before_verifier",
    }:
        raise TextOnlyPilotError("committed_interruption_freeze_reason_is_not_approved")
    root, lock, bundle, plan, state = _load_prepared(paths, run_id)
    state_body = _mapping(state.get("body"), "execution_state_body")
    stages = _mapping(state_body.get("stages"), "execution_state_stages")
    completed_roles = ("brief_builder", "author", "truth_builder")
    if (
        state.get("status") != "live_running"
        or state_body.get("status") != "live_running"
        or _mapping(stages.get("verifier"), "verifier_stage").get("status") != "not_started"
        or _mapping(stages.get("verifier"), "verifier_stage").get("attempts") != 0
    ):
        raise TextOnlyPilotError("text_only_run_is_not_the_expected_committed_interruption_shape")
    for role in completed_roles:
        stage = _mapping(stages.get(role), f"{role}_stage")
        deterministic_truth = role == "truth_builder" and stage.get("worker_kind") == _DETERMINISTIC_TRUTH_WORKER
        expected_calls = 0 if deterministic_truth else 1
        if (
            stage.get("status") != "completed"
            or stage.get("attempts") != 1
            or stage.get("provider_calls") != expected_calls
        ):
            raise TextOnlyPilotError("text_only_run_is_not_the_expected_committed_interruption_shape")

    approval = _run_envelope(root, "execution-approval.json", _APPROVAL_KIND)
    executor_receipt = _run_envelope(root, "executor-receipt.json", _EXECUTOR_KIND)
    gate = _run_envelope(root, "no-network-gate.json", _PREFLIGHT_KIND)
    frozen_stages: dict[str, dict[str, Any]] = {}
    total_token_in = 0
    total_token_out = 0
    total_cost = 0.0
    for role in completed_roles:
        stage = _mapping(stages.get(role), f"{role}_stage")
        input_reference = _json_artifact_reference(paths, _stage_input_path(root, role), f"{role}_input")
        output_reference = _json_artifact_reference(paths, _stage_output_path(root, role), f"{role}_output")
        if (
            stage.get("input_digest") != input_reference["content_digest"]
            or stage.get("output_digest") != output_reference["content_digest"]
            or stage.get("output_path") != _stage_output_path(root, role).name
        ):
            raise TextOnlyPilotError("committed_stage_artifact_binding_invalid")
        deterministic_truth = role == "truth_builder" and stage.get("worker_kind") == _DETERMINISTIC_TRUTH_WORKER
        if deterministic_truth:
            if (
                stage.get("provider_calls") != 0
                or stage.get("provider") != _DETERMINISTIC_TRUTH_PROVIDER
                or stage.get("model") != _DETERMINISTIC_TRUTH_MODEL
                or stage.get("token_in") != 0
                or stage.get("token_out") != 0
                or stage.get("cost_usd") != 0.0
            ):
                raise TextOnlyPilotError("deterministic_truth_stage_accounting_invalid")
            frozen_stages[role] = {
                "status": "committed_deterministic_truth_frozen",
                "provider_calls": 0,
                "state_committed": True,
                "agent_reference": _DETERMINISTIC_TRUTH_WORKER,
                "input": input_reference,
                "output": output_reference,
            }
            continue
        prompt_reference = _json_artifact_reference(paths, _stage_prompt_path(root, role), f"{role}_prompt")
        provider, model = _plan_route(plan, role)
        settlement = _usage_settlement_reference_for_binding(
            paths,
            run_id=run_id,
            role=role,
            provider=provider,
            model=model,
            input_digest=input_reference["content_digest"],
            output_digest=output_reference["content_digest"],
        )
        frozen_stages[role] = {
            "status": "committed_completed_frozen",
            "provider_calls": 1,
            "state_committed": True,
            "input": input_reference,
            "prompt": prompt_reference,
            "output": output_reference,
            "usage_settlement": settlement,
        }
        total_token_in += settlement["token_in"]
        total_token_out += settlement["token_out"]
        total_cost += settlement["cost_usd"]
    frozen_stages["verifier"] = {
        "status": "not_called_operator_interruption",
        "provider_calls": 0,
        "reason": reason,
    }
    failure_body = {
        "failure_version": 1,
        "run_id": run_id,
        "status": "incomplete_frozen",
        "reason": reason,
        "execution_artifacts": {
            "lock": _artifact_ref(paths, root / "execution-lock.json", lock),
            "bundle": _artifact_ref(paths, root / "execution-bundle.json", bundle),
            "plan": _artifact_ref(paths, root / "execution-plan.json", plan),
            "approval": _artifact_ref(paths, root / "execution-approval.json", approval),
            "executor_receipt": _artifact_ref(paths, root / "executor-receipt.json", executor_receipt),
            "no_network_gate": _artifact_ref(paths, root / "no-network-gate.json", gate),
        },
        "stages": frozen_stages,
        "staging_enabled": False,
        "release_eligible": False,
        "resume_allowed": False,
    }
    summary = _write_envelope(
        root / "failure-summary.json",
        kind=_FAILURE_SUMMARY_KIND,
        identifier=f"{run_id}-failure-summary",
        status="incomplete_frozen",
        body=failure_body,
    )
    state_body["stages"] = frozen_stages
    state_body["usage"] = {
        "token_in": total_token_in,
        "token_out": total_token_out,
        "cost_usd": total_cost,
    }
    state_body["failure_summary"] = _artifact_ref(paths, root / "failure-summary.json", summary)
    state_body["failed_stage"] = "verifier"
    state_body["staging_enabled"] = False
    state_body["release_eligible"] = False
    state_body["resume_allowed"] = False
    final = _write_state(root, run_id=run_id, body=state_body, status="incomplete_frozen")
    append_event(
        paths,
        "v32_text_only_pilot_committed_interruption_frozen",
        {
            "run_id": run_id,
            "reason": reason,
            "completed_roles": list(completed_roles),
            "verifier_provider_calls": 0,
        },
    )
    return {
        "run_id": run_id,
        "status": "incomplete_frozen",
        "failure_summary_path": str(root / "failure-summary.json"),
        "failure_summary_digest": summary["content_digest"],
        "state_path": str(root / "state.json"),
        "state_digest": final["content_digest"],
        "resume_allowed": False,
    }


def _maybe_json_artifact_reference(paths: WorkspacePaths, path: Path, label: str) -> dict[str, str] | None:
    """Return a private JSON reference when it was durably written.

    Abort recovery must describe partial persistence without trying to repair or
    reinterpret it. A missing output therefore becomes audit evidence rather
    than a reason to re-run a model call.
    """

    if path.is_symlink() or not path.is_file():
        return None
    try:
        return _json_artifact_reference(paths, path, label)
    except TextOnlyPilotError:
        return None


def _safe_failure_code(error: BaseException | None) -> str | None:
    """Only persist stable control-plane codes, never provider text."""

    if not isinstance(error, TextOnlyPilotError):
        return None
    value = str(error)
    return value if re.fullmatch(r"[a-z0-9_]{1,160}", value) else None


def _freeze_aborted_model_stage(
    paths: WorkspacePaths,
    *,
    root: Path,
    plan: Mapping[str, Any],
    run_id: str,
    role: str,
    stage: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    """Freeze one model stage and recover a settlement only when provable."""

    status = stage.get("status")
    attempts = stage.get("attempts")
    provider_calls = stage.get("provider_calls")
    input_reference = _maybe_json_artifact_reference(paths, _stage_input_path(root, role), f"{role}_input")
    prompt_reference = _maybe_json_artifact_reference(paths, _stage_prompt_path(root, role), f"{role}_prompt")
    output_reference = _maybe_json_artifact_reference(paths, _stage_output_path(root, role), f"{role}_output")
    settlement: dict[str, Any] | None = None
    if input_reference is not None and output_reference is not None:
        try:
            provider, model = _plan_route(plan, role)
            settlement = _usage_settlement_reference_for_binding(
                paths,
                run_id=run_id,
                role=role,
                provider=provider,
                model=model,
                input_digest=input_reference["content_digest"],
                output_digest=output_reference["content_digest"],
            )
        except TextOnlyPilotError:
            settlement = None

    if status == "completed":
        frozen_status = "committed_completed_frozen"
        calls = 1
        state_committed = True
    elif status == "running":
        # State is written immediately before transport. Treat any remaining
        # running stage as an irreversible one-attempt boundary, even if a
        # response/usage receipt cannot be recovered after process loss.
        frozen_status = (
            "response_received_uncommitted_frozen"
            if output_reference is not None
            else "attempt_started_usage_unsettled_frozen"
        )
        calls = 1
        state_committed = False
    elif status == "failed_final":
        frozen_status = "failed_final_frozen"
        calls = 1 if provider_calls == 1 else 0
        state_committed = False
    elif status == "not_started":
        return {"status": "not_called", "provider_calls": 0}, None, False
    else:
        frozen_status = "unknown_stage_state_frozen"
        calls = 1 if attempts == 1 or provider_calls == 1 else 0
        state_committed = False

    frozen: dict[str, Any] = {
        "status": frozen_status,
        "provider_calls": calls,
        "attempts": 1 if calls else 0,
        "state_committed": state_committed,
    }
    if input_reference is not None:
        frozen["input"] = input_reference
    if prompt_reference is not None:
        frozen["prompt"] = prompt_reference
    if output_reference is not None:
        frozen["output"] = output_reference
    if settlement is not None:
        frozen["usage_settlement"] = settlement
    if calls and settlement is None:
        frozen["usage_accounting"] = "usage_unsettled"
    return frozen, settlement, bool(calls and settlement is None)


def freeze_aborted_text_only_pilot(
    paths: WorkspacePaths,
    *,
    run_id: str,
    failure_phase: str,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Terminally freeze an aborted run without resuming any model stage.

    This is the sole recovery path for process loss, post-response validation
    failures, and an explicit operator abort. It does not rely on the current
    source digest, because it must also freeze a run prepared by a now-stale
    executor. Provider usage is recovered from the append-only ledger when
    possible; otherwise it is explicitly marked unsettled.
    """

    if not re.fullmatch(r"[a-z0-9_]{1,96}", failure_phase):
        raise TextOnlyPilotError("text_only_abort_failure_phase_invalid")
    root, lock, bundle, plan, state = _load_prepared(paths, run_id)
    state_body = _mapping(state.get("body"), "execution_state_body")
    if state.get("status") == "incomplete_frozen" and (root / "failure-summary.json").is_file():
        summary = _run_envelope(root, "failure-summary.json", _FAILURE_SUMMARY_KIND)
        return {
            "run_id": run_id,
            "status": "incomplete_frozen",
            "failure_summary_path": str(root / "failure-summary.json"),
            "failure_summary_digest": summary["content_digest"],
            "state_path": str(root / "state.json"),
            "state_digest": state["content_digest"],
            "resume_allowed": False,
        }

    approval = _run_envelope(root, "execution-approval.json", _APPROVAL_KIND)
    executor_receipt = _run_envelope(root, "executor-receipt.json", _EXECUTOR_KIND)
    gate = _run_envelope(root, "no-network-gate.json", _PREFLIGHT_KIND)
    original_stages = _mapping(state_body.get("stages"), "execution_state_stages")
    frozen_stages: dict[str, dict[str, Any]] = {}
    total_token_in = 0
    total_token_out = 0
    total_cost = 0.0
    usage_unsettled_roles: list[str] = []
    for role in _ROLES:
        stage = _mapping(original_stages.get(role), f"{role}_stage")
        if role == "truth_builder":
            status = stage.get("status")
            if status == "completed":
                input_reference = _maybe_json_artifact_reference(paths, _stage_input_path(root, role), f"{role}_input")
                output_reference = _maybe_json_artifact_reference(paths, _stage_output_path(root, role), f"{role}_output")
                frozen: dict[str, Any] = {
                    "status": "committed_deterministic_truth_frozen",
                    "provider_calls": 0,
                    "attempts": 1,
                    "state_committed": True,
                    "agent_reference": _DETERMINISTIC_TRUTH_WORKER,
                }
                if input_reference is not None:
                    frozen["input"] = input_reference
                if output_reference is not None:
                    frozen["output"] = output_reference
                frozen_stages[role] = frozen
            elif status == "not_started":
                frozen_stages[role] = {"status": "not_called", "provider_calls": 0}
            else:
                frozen_stages[role] = {
                    "status": "deterministic_stage_incomplete_frozen",
                    "provider_calls": 0,
                    "attempts": 1 if stage.get("attempts") == 1 else 0,
                    "state_committed": False,
                }
            continue
        frozen, settlement, unsettled = _freeze_aborted_model_stage(
            paths,
            root=root,
            plan=plan,
            run_id=run_id,
            role=role,
            stage=stage,
        )
        frozen_stages[role] = frozen
        if settlement is not None:
            total_token_in += settlement["token_in"]
            total_token_out += settlement["token_out"]
            total_cost += settlement["cost_usd"]
        if unsettled:
            usage_unsettled_roles.append(role)

    derived_artifacts: dict[str, dict[str, str]] = {}
    for label, path in (
        ("candidate", _candidate_path(root)),
        ("authoritative_truth_record", _stage_output_path(root, "truth_builder")),
        ("independent_truth_record", root / "evidence" / "independent-second-session-truth-record.json"),
    ):
        reference = _maybe_json_artifact_reference(paths, path, label)
        if reference is not None:
            derived_artifacts[label] = reference

    failure_body: dict[str, Any] = {
        "failure_version": 2,
        "run_id": run_id,
        "status": "incomplete_frozen",
        "reason": "execution_aborted_fail_closed",
        "failure_context": {
            "phase": failure_phase,
            "error_class": type(error).__name__ if error is not None else "OperatorAbort",
            "error_code": _safe_failure_code(error),
        },
        "execution_artifacts": {
            "lock": _artifact_ref(paths, root / "execution-lock.json", lock),
            "bundle": _artifact_ref(paths, root / "execution-bundle.json", bundle),
            "plan": _artifact_ref(paths, root / "execution-plan.json", plan),
            "approval": _artifact_ref(paths, root / "execution-approval.json", approval),
            "executor_receipt": _artifact_ref(paths, root / "executor-receipt.json", executor_receipt),
            "no_network_gate": _artifact_ref(paths, root / "no-network-gate.json", gate),
        },
        "stages": frozen_stages,
        "derived_artifacts": derived_artifacts,
        "usage_unsettled_roles": usage_unsettled_roles,
        "staging_enabled": False,
        "release_eligible": False,
        "resume_allowed": False,
    }
    summary = _write_envelope(
        root / "failure-summary.json",
        kind=_FAILURE_SUMMARY_KIND,
        identifier=f"{run_id}-failure-summary",
        status="incomplete_frozen",
        body=failure_body,
    )
    state_body["stages"] = frozen_stages
    state_body["usage"] = {
        "token_in": total_token_in,
        "token_out": total_token_out,
        "cost_usd": total_cost,
    }
    state_body["usage_unsettled_roles"] = usage_unsettled_roles
    state_body["failure_summary"] = _artifact_ref(paths, root / "failure-summary.json", summary)
    state_body["failed_stage"] = next(
        (role for role in reversed(_ROLES) if frozen_stages[role].get("provider_calls", 0) or frozen_stages[role]["status"] != "not_called"),
        "brief_builder",
    )
    state_body["staging_enabled"] = False
    state_body["release_eligible"] = False
    state_body["resume_allowed"] = False
    final = _write_state(root, run_id=run_id, body=state_body, status="incomplete_frozen")
    try:
        append_event(
            paths,
            "v32_text_only_pilot_aborted_frozen",
            {
                "run_id": run_id,
                "failure_phase": failure_phase,
                "error_class": type(error).__name__ if error is not None else "OperatorAbort",
                "error_code": _safe_failure_code(error),
                "usage_unsettled_roles": usage_unsettled_roles,
            },
        )
    except Exception:
        # The terminal summary/state are already durable; append-only event
        # logging must never trigger a retry or overwrite that evidence.
        pass
    return {
        "run_id": run_id,
        "status": "incomplete_frozen",
        "failure_summary_path": str(root / "failure-summary.json"),
        "failure_summary_digest": summary["content_digest"],
        "state_path": str(root / "state.json"),
        "state_digest": final["content_digest"],
        "resume_allowed": False,
    }


def _stage_inputs(
    *,
    lock: Mapping[str, Any],
    bundle: Mapping[str, Any],
    plan: Mapping[str, Any],
    run_id: str,
    target: Mapping[str, Any],
    first_receipt: Mapping[str, Any],
    first_sidecar: Mapping[str, Any],
    second_receipt: Mapping[str, Any],
    second_sidecar: Mapping[str, Any],
    brief: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
    independent_truth_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    lock_body = _mapping(lock.get("body"), "execution_lock_body")
    bundle_body = _mapping(bundle.get("body"), "execution_bundle_body")
    work_item_id = str(lock_body["work_item_id"])
    if brief is None:
        return {
            "artifact_kind": "v32_text_only_brief_input",
            "execution_mode": "measurement_replay_text_only",
            "run_id": run_id,
            "work_item_id": work_item_id,
            "instruction": "Copy the supplied child TargetSpec exactly. Do not add facts, answers, paths, coordinates, or a lifecycle action.",
            "public_child_target": bundle_body["public_brief_input"]["child_target"],
            "compact_inventory": bundle_body["public_brief_input"]["compact_inventory"],
            "measurement_capability": bundle_body["public_brief_input"]["measurement_capability"],
        }
    if candidate is None:
        return {
            "artifact_kind": "v32_question_draft_input",
            "execution_mode": "measurement_replay_text_only",
            "run_id": run_id,
            "work_item_id": work_item_id,
            "instruction": (
                "Write editorial-only scientific context, object reference, question wording, and reasoning focus for a conformation question. "
                "Do not infer, state, encode, or request a numerical measurement, answer, reference answer, truth, viewer session, operation list, provenance, or lifecycle action. "
                "Do not put viewer UI, tool, click, selection, rotation, zoom, rendering, sidebar, menu, or operation instructions in solver-facing text. "
                "The control plane will attach session metadata and private truth after this draft is validated. Return only the required JSON schema object."
            ),
            "author_scope": {
                "target_leaf": brief["target_leaf"],
                "selector_type": _mapping(brief["selector"], "brief_selector")["type"],
                "question_type": "controlled_viewer_dihedral_measurement",
            },
            "target_spec_digest": digest(dict(brief)),
            "question_text_policy": {
                "id": lock_body["question_text_policy_id"],
                "content_digest": lock_body["question_text_policy_digest"],
            },
        }
    if independent_truth_record is None:
        raise TextOnlyPilotError("verifier_requires_an_independent_deterministic_truth_record")
    independent = dict(independent_truth_record)
    if independent.get("artifact_kind") != _TRUTH_RECORD_KIND:
        raise TextOnlyPilotError("verifier_independent_truth_record_invalid")
    verifier_handoff = {
        "verification_mode": "measurement_replay_text_only",
        "independent_truth_record": independent,
        "handoff_digest": digest(independent),
        "receipt_digests": [
            independent["measurement_receipt_digest"],
            independent["private_sidecar_digest"],
        ],
    }
    return {
        "artifact_kind": "v32_text_only_verifier_input",
        "execution_mode": "measurement_replay_text_only",
        "verification_mode": "measurement_replay_text_only",
        "run_id": run_id,
        "work_item_id": work_item_id,
        "instruction": (
            "You receive text only, not image, render, or interactive viewer input. Verify the candidate against the independently materialized second-session TruthRecord. "
            "Do not claim visual inspection. Return only the required verification JSON object."
        ),
        "candidate": candidate,
        "independent_measurement_handoff": verifier_handoff,
        "required_verification_bindings": {
            "candidate_digest": digest(candidate),
            "evidence_digest": second_sidecar["body"]["evidence_digest"],
            "rubric_lock_digest": lock_body["config_digest"],
            "work_item_id": work_item_id,
        },
    }


def _materialize_truth_stage(
    paths: WorkspacePaths,
    *,
    root: Path,
    state_body: dict[str, Any],
    run_id: str,
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    work_item_id: str,
) -> dict[str, Any]:
    """Persist a zero-token TruthRecord from the locked author-side measurement."""

    role = "truth_builder"
    stages = _mapping(state_body.get("stages"), "pilot_state_stages")
    stage = _mapping(stages.get(role), "truth_builder_stage")
    if (
        stage.get("status") != "not_started"
        or stage.get("attempts") != 0
        or stage.get("provider_calls") != 0
    ):
        raise TextOnlyPilotError("deterministic_truth_stage_is_not_fresh")
    started = time.perf_counter()
    try:
        materialization_input = _truth_materialization_input(
            candidate=candidate,
            target=target,
            receipt=receipt,
            sidecar=sidecar,
            work_item_id=work_item_id,
            purpose="author_truth",
        )
        truth = _materialize_truth_record(
            candidate=candidate,
            target=target,
            receipt=receipt,
            sidecar=sidecar,
            work_item_id=work_item_id,
            purpose="author_truth",
        )
        atomic_write_json(_stage_input_path(root, role), materialization_input, private=True)
        atomic_write_json(_stage_output_path(root, role), truth, private=True)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        stages[role] = {
            "status": "completed",
            "attempts": 1,
            "provider_calls": 0,
            "worker_kind": _DETERMINISTIC_TRUTH_WORKER,
            "provider": _DETERMINISTIC_TRUTH_PROVIDER,
            "model": _DETERMINISTIC_TRUTH_MODEL,
            "input_digest": digest(materialization_input),
            "output_digest": digest(truth),
            "output_path": _stage_output_path(root, role).name,
            "token_in": 0,
            "token_out": 0,
            "cost_usd": 0.0,
            "cost_accounting": "zero_cost_deterministic",
            "latency_ms": elapsed_ms,
            "agent_reference": _DETERMINISTIC_TRUTH_WORKER,
        }
        state_body["stages"] = stages
        _write_state(root, run_id=run_id, body=state_body, status="live_running")
    except Exception as exc:
        stages = _mapping(state_body.get("stages"), "pilot_state_stages")
        stages[role] = {
            "status": "failed_final",
            "attempts": 0,
            "provider_calls": 0,
            "worker_kind": _DETERMINISTIC_TRUTH_WORKER,
            "failure_category": "deterministic_materialization",
            "error_class": type(exc).__name__,
        }
        state_body["stages"] = stages
        state_body["failed_stage"] = role
        _write_state(root, run_id=run_id, body=state_body, status="failed_final")
        append_event(
            paths,
            "v32_text_only_pilot_stage_failed",
            {
                "run_id": run_id,
                "role": role,
                "attempt": 0,
                "provider_calls": 0,
                "failure_category": "deterministic_materialization",
                "error_class": type(exc).__name__,
            },
        )
        raise
    event = append_event(
        paths,
        "v32_text_only_pilot_stage_completed",
        {
            "run_id": run_id,
            "role": role,
            "input_digest": digest(materialization_input),
            "output_digest": digest(truth),
            "deterministic": True,
            "provider_calls": 0,
        },
    )
    stages = _mapping(state_body.get("stages"), "pilot_state_stages")
    completed = _mapping(stages.get(role), "completed_deterministic_truth_stage")
    completed["event_digest"] = event["content_digest"]
    stages[role] = completed
    state_body["stages"] = stages
    _write_state(root, run_id=run_id, body=state_body, status="live_running")
    return truth


def _persist_independent_truth_record(
    paths: WorkspacePaths,
    *,
    root: Path,
    truth: Mapping[str, Any],
) -> dict[str, str]:
    """Freeze verifier evidence before its one permitted provider invocation."""

    path = root / "evidence" / "independent-second-session-truth-record.json"
    expected = dict(truth)
    if path.exists():
        existing = _mapping(read_json(path), "independent_truth_record")
        if existing != expected:
            raise TextOnlyPilotError("independent_truth_record_changed_between_resume_attempts")
    else:
        atomic_write_json(path, expected, private=True)
    return {"path": _relative(paths, path), "content_digest": digest(expected)}


def _after_worker_output_persisted_before_state_commit(
    _role: str,
    _result: WorkerResult,
) -> None:
    """Narrow fault-injection seam for crash-boundary tests.

    Production execution leaves this as a no-op. Tests patch it to prove that
    the top-level abort handler freezes a post-response, pre-state-commit
    interruption without reissuing a provider call.
    """


def _invoke(
    paths: WorkspacePaths,
    *,
    root: Path,
    state_body: dict[str, Any],
    executor: WorkerExecutor,
    adapters: Mapping[tuple[str, str], TypedWorker],
    policy: Any,
    run_id: str,
    role: str,
    input_contract: Mapping[str, Any],
) -> WorkerResult:
    def record_failure(exc: Exception, *, category: str, provider_calls: int) -> None:
        """Commit a terminal failure without serializing untrusted error text."""

        stages = _mapping(state_body.get("stages"), "pilot_state_stages")
        current = _mapping(stages.get(role), f"{role}_stage")
        attempts = 1 if current.get("attempts") == 1 else 0
        stages[role] = {
            "status": "failed_final",
            "attempts": attempts,
            "provider_calls": provider_calls,
            "failure_category": category,
            "error_class": type(exc).__name__,
        }
        state_body["stages"] = stages
        state_body["failed_stage"] = role
        _write_state(root, run_id=run_id, body=state_body, status="failed_final")
        append_event(
            paths,
            "v32_text_only_pilot_stage_failed",
            {
                "run_id": run_id,
                "role": role,
                "attempt": attempts,
                "provider_calls": provider_calls,
                "failure_category": category,
                "error_class": type(exc).__name__,
            },
        )

    # Everything before ``executor.invoke`` is local. A failure there must
    # become a durable, zero-provider-call terminal state rather than leave a
    # fresh run ambiguous or replayable.
    try:
        _safe_text_only(input_contract, f"{role}_input")
        stages = _mapping(state_body.get("stages"), "pilot_state_stages")
        stage = _mapping(stages.get(role), f"{role}_stage")
        if (
            stage.get("status") != "not_started"
            or stage.get("attempts") != 0
            or stage.get("provider_calls") != 0
        ):
            raise TextOnlyPilotError("one_slot_stage_is_not_fresh")
        route = policy.role(role)
        request = WorkerRequest(
            role=role,
            provider=str(route["provider"]),
            model=str(route["allowed_models"][0]),
            input_contract=dict(input_contract),
            max_tokens=int(route["max_tokens"]),
            max_cost_usd=float(route["max_cost_usd"]),
            run_id=run_id,
            work_item_id=str(state_body["work_item_id"]),
        )
        adapter = adapters.get((request.provider, request.model))
        if adapter is None:
            raise TextOnlyPilotError("approved_provider_adapter_missing")
        prompt_budget = getattr(adapter, "prompt_budget", None)
        reserve = getattr(adapter, "with_prompt_reservation", None)
        if not callable(prompt_budget) or not callable(reserve):
            raise TextOnlyPilotError("approved_provider_adapter_has_no_prompt_reservation_contract")
        budget = prompt_budget(request)
        narrowed = reserve(request)
        if not isinstance(narrowed, WorkerRequest):
            raise TextOnlyPilotError("provider_prompt_reservation_invalid")
        atomic_write_json(_stage_input_path(root, role), dict(input_contract), private=True)
        prompt_reference = _persist_prompt_snapshot(root, role=role, adapter=adapter, request=request)
    except Exception as exc:
        record_failure(exc, category="local_preflight", provider_calls=0)
        raise

    # Commit that the one allowed provider attempt is about to begin before
    # handing control to the HTTP executor. A crash at this point is terminal,
    # never an invitation to repeat the call.
    stages[role] = {"status": "running", "attempts": 1, "provider_calls": 0}
    state_body["stages"] = stages
    _write_state(root, run_id=run_id, body=state_body, status="live_running")
    started = time.perf_counter()
    try:
        result = executor.invoke(narrowed, worker=adapter)
    except Exception as exc:
        record_failure(
            exc,
            category="provider_invocation" if bool(getattr(exc, "network_called", False)) else "local_preflight",
            provider_calls=1 if bool(getattr(exc, "network_called", False)) else 0,
        )
        raise

    # The provider has consumed an attempt. Persist its output, exact ledger
    # settlement, and aggregate usage before returning to the next stage.
    try:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        atomic_write_json(_stage_output_path(root, role), dict(result.output_contract), private=True)
        _after_worker_output_persisted_before_state_commit(role, result)
        settlement = _usage_settlement_reference(paths, result, run_id=run_id)
        stages[role] = {
            "status": "completed",
            "attempts": 1,
            "provider_calls": 1,
            "provider": result.provider,
            "model": result.model,
            "input_digest": result.input_contract_digest,
            "output_digest": result.output_contract_digest,
            "output_path": _stage_output_path(root, role).name,
            "token_in": result.token_in,
            "token_out": result.token_out,
            "cost_usd": result.actual_cost_usd,
            "cost_accounting": result.cost_accounting,
            "latency_ms": elapsed_ms,
            "prompt": prompt_reference,
            "prompt_budget": {
                "estimated_input_tokens": getattr(budget, "estimated_input_tokens", None),
                "schema_output_reservation": getattr(budget, "schema_output_reservation", None),
                "dynamic_total_reservation": getattr(budget, "dynamic_total_reservation", None),
                "api_max_tokens": getattr(budget, "api_max_tokens", None),
                "prompt_digest": getattr(budget, "prompt_digest", None),
            },
            "usage_settlement": settlement,
        }
        state_body["stages"] = stages
        usage = _mapping(state_body.get("usage"), "pilot_state_usage")
        usage["token_in"] = int(usage["token_in"]) + result.token_in
        usage["token_out"] = int(usage["token_out"]) + result.token_out
        usage["cost_usd"] = float(usage["cost_usd"]) + result.actual_cost_usd
        state_body["usage"] = usage
        _write_state(root, run_id=run_id, body=state_body, status="live_running")
    except Exception as exc:
        record_failure(exc, category="post_transport_persistence", provider_calls=1)
        raise

    # The state above is already a complete restart point. The event is a
    # secondary audit record, so a later interruption cannot cause a repeated
    # provider call merely because this append has not happened yet.
    event = append_event(
        paths,
        "v32_text_only_pilot_stage_completed",
        {
            "run_id": run_id,
            "role": role,
            "input_digest": result.input_contract_digest,
            "output_digest": result.output_contract_digest,
            "usage_settlement_digest": settlement["content_digest"],
        },
    )
    stages = _mapping(state_body.get("stages"), "pilot_state_stages")
    completed = _mapping(stages.get(role), f"{role}_completed_stage")
    completed["event_digest"] = event["content_digest"]
    stages[role] = completed
    state_body["stages"] = stages
    _write_state(root, run_id=run_id, body=state_body, status="live_running")
    return result


def run_no_network_gate(
    paths: WorkspacePaths,
    *,
    run_id: str,
    routing_policy_path: str | Path,
    runtime_config_path: str | Path,
    environ: Mapping[str, str] | None = None,
    adapters: Mapping[tuple[str, str], TypedWorker] | None = None,
) -> dict[str, Any]:
    """Validate text-only contracts and adapter prompt budgets without invoke."""

    root, lock, bundle, plan, state = _load_prepared(paths, run_id)
    _assert_executor_identity(root, lock)
    lock_body = _mapping(lock.get("body"), "execution_lock_body")
    state_body = _mapping(state.get("body"), "execution_state_body")
    ground_truth_registry = _locked_ground_truth_registry(paths, lock=lock, plan=plan)
    if state_body.get("status") not in {"prepared_pending_no_network_gate", "ready_for_live_execution"}:
        raise TextOnlyPilotError("no_network_gate_has_an_invalid_run_state")
    try:
        policy = load_verified_routing_policy(
            routing_policy_path,
            expected_digest=str(lock_body["routing_policy_digest"]),
            schema_registry=_schema_registry(),
        )
        _locked_runtime(lock_body, runtime_config_path=runtime_config_path, environ=environ)
        active_adapters = dict(adapters) if adapters is not None else build_provider_adapters(
            policy,
            runtime_config_path=runtime_config_path,
            runtime_config_digest=str(lock_body["runtime_config_digest"]),
            environ=environ,
            execution_profile=MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
        )
    except (InputError, WorkerPolicyError, ProviderNotConfiguredError) as exc:
        raise TextOnlyPilotError("no_network_gate_provider_binding_invalid") from exc
    if lock_body.get("transport_attempt_max") != 1:
        raise TextOnlyPilotError("one_slot_transport_retry_gate_invalid")
    target = _mapping(_mapping(bundle.get("body"), "execution_bundle_body")["public_brief_input"]["child_target"], "child_target")
    artifacts = _mapping(lock_body.get("artifacts"), "execution_lock_artifacts")
    first_receipt = verify_envelope(read_json(require_within(paths.root, paths.root / artifacts["first_receipt"]["path"])), kind=_MEASUREMENT_RECEIPT_KIND)
    first_sidecar = verify_envelope(read_json(require_within(paths.root, paths.root / artifacts["first_sidecar"]["path"])), kind=_SIDECAR_KIND)
    second_receipt = verify_envelope(read_json(require_within(paths.root, paths.root / artifacts["second_receipt"]["path"])), kind=_MEASUREMENT_RECEIPT_KIND)
    second_sidecar = verify_envelope(read_json(require_within(paths.root, paths.root / artifacts["second_sidecar"]["path"])), kind=_SIDECAR_KIND)
    first_measurement = _mapping(first_sidecar.get("body"), "first_measurement_sidecar_body")
    first_result = _mapping(first_measurement.get("measurement_result"), "first_measurement_result")
    _registered_measurement_ground_truth_record(
        registry=ground_truth_registry,
        work_item_id=str(lock_body["work_item_id"]),
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        answer={
            _ANSWER_FIELD: _nearest_whole_degree(
                _numeric_answer(first_result.get("value"), "first_measurement_value")
            )
        },
    )
    preflight_candidate = {
        "artifact_kind": "v32_text_only_preflight_candidate",
        "run_id": run_id,
        "target_spec_digest": digest(target),
        "anonymous_scene_ref": target["anonymous_scene_ref"],
        "purpose": "prompt_budget_only_no_network_gate",
    }
    preflight_truth = _materialize_truth_record(
        candidate=preflight_candidate,
        target=target,
        receipt=second_receipt,
        sidecar=second_sidecar,
        work_item_id=str(lock_body["work_item_id"]),
        purpose="verifier_independent_evidence",
    )
    contracts = {
        "brief_builder": _stage_inputs(
            lock=lock,
            bundle=bundle,
            plan=plan,
            run_id=run_id,
            target=target,
            first_receipt=first_receipt,
            first_sidecar=first_sidecar,
            second_receipt=second_receipt,
            second_sidecar=second_sidecar,
        ),
        "author": _stage_inputs(
            lock=lock,
            bundle=bundle,
            plan=plan,
            run_id=run_id,
            target=target,
            first_receipt=first_receipt,
            first_sidecar=first_sidecar,
            second_receipt=second_receipt,
            second_sidecar=second_sidecar,
            brief=target,
        ),
        "verifier": _stage_inputs(
            lock=lock,
            bundle=bundle,
            plan=plan,
            run_id=run_id,
            target=target,
            first_receipt=first_receipt,
            first_sidecar=first_sidecar,
            second_receipt=second_receipt,
            second_sidecar=second_sidecar,
            brief=target,
            candidate=preflight_candidate,
            independent_truth_record=preflight_truth,
        ),
    }
    # The first-session truth stage has no provider. Materializing it here
    # proves the lock can produce an authoritative record before any model is
    # allowed to run.
    _materialize_truth_record(
        candidate=preflight_candidate,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        work_item_id=str(lock_body["work_item_id"]),
        purpose="author_truth",
    )
    for role, contract in contracts.items():
        _safe_text_only(contract, f"{role}_input")
        route = policy.role(role)
        request = WorkerRequest(role=role, provider=str(route["provider"]), model=str(route["allowed_models"][0]), input_contract=contract, max_tokens=int(route["max_tokens"]), max_cost_usd=float(route["max_cost_usd"]), run_id=run_id, work_item_id=str(lock_body["work_item_id"]))
        adapter = active_adapters.get((request.provider, request.model))
        if adapter is None or not callable(getattr(adapter, "prompt_budget", None)):
            raise TextOnlyPilotError("no_network_gate_missing_prompt_budget_adapter")
        adapter.prompt_budget(request)
    gate_body = {
        "gate_version": 1,
        "execution_lock_digest": lock["content_digest"],
        "plan_digest": plan["content_digest"],
        "status": "passed",
        "network_calls": 0,
        "checked_roles": sorted(_MODEL_ROLES),
        "checked_deterministic_roles": ["truth_builder"],
        "text_only": True,
        "stage_retry_max": 0,
        "transport_attempt_max": 1,
        "staging_enabled": False,
        "release_eligible": False,
    }
    gate = _write_envelope(root / "no-network-gate.json", kind=_PREFLIGHT_KIND, identifier=f"{run_id}-no-network", status="passed", body=gate_body)
    state_body["status"] = "ready_for_live_execution"
    state_body["no_network_gate_digest"] = gate["content_digest"]
    _write_state(root, run_id=run_id, body=state_body, status="ready_for_live_execution")
    return {"gate_path": str(root / "no-network-gate.json"), "gate_digest": gate["content_digest"], "network_calls": 0}


def _execute_text_only_pilot_impl(
    paths: WorkspacePaths,
    *,
    run_id: str,
    routing_policy_path: str | Path,
    runtime_config_path: str | Path,
    environ: Mapping[str, str] | None = None,
    adapters: Mapping[tuple[str, str], TypedWorker] | None = None,
) -> dict[str, Any]:
    """Run exactly one non-retryable four-role loop and stop at human review."""

    root, lock, bundle, plan, state = _load_prepared(paths, run_id)
    _assert_executor_identity(root, lock)
    lock_body = _mapping(lock.get("body"), "execution_lock_body")
    state_body = _mapping(state.get("body"), "execution_state_body")
    if state_body.get("status") == "prepared_pending_no_network_gate":
        run_no_network_gate(paths, run_id=run_id, routing_policy_path=routing_policy_path, runtime_config_path=runtime_config_path, environ=environ, adapters=adapters)
        root, lock, bundle, plan, state = _load_prepared(paths, run_id)
        lock_body = _mapping(lock.get("body"), "execution_lock_body")
        state_body = _mapping(state.get("body"), "execution_state_body")
    if state_body.get("status") not in {"ready_for_live_execution", "live_running"}:
        raise TextOnlyPilotError("text_only_pilot_is_not_ready_for_live_execution")
    _validate_resumable_stage_order(state_body)
    ground_truth_registry = _locked_ground_truth_registry(paths, lock=lock, plan=plan)
    try:
        policy = load_verified_routing_policy(routing_policy_path, expected_digest=str(lock_body["routing_policy_digest"]), schema_registry=_schema_registry())
        question_policy = load_question_text_policy(expected_digest=str(lock_body["question_text_policy_digest"]))
        _locked_runtime(lock_body, runtime_config_path=runtime_config_path, environ=environ)
        active_adapters = dict(adapters) if adapters is not None else build_provider_adapters(
            policy,
            runtime_config_path=runtime_config_path,
            runtime_config_digest=str(lock_body["runtime_config_digest"]),
            environ=environ,
            execution_profile=MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
        )
    except (InputError, WorkerPolicyError, ProviderNotConfiguredError) as exc:
        raise TextOnlyPilotError("live_provider_binding_invalid") from exc
    executor = WorkerExecutor(paths, policy=policy, usage_budget=_usage_budget(policy), approved_work_item_count=1, provider_adapters=active_adapters)
    artifacts = _mapping(lock_body.get("artifacts"), "execution_lock_artifacts")
    def artifact(name: str, kind: str) -> dict[str, Any]:
        reference = _mapping(artifacts.get(name), f"execution_lock_artifact_{name}")
        path = require_within(paths.root, paths.root / str(reference["path"]), name)
        value = verify_envelope(_mapping(read_json(path), name), kind=kind)
        if value["content_digest"] != reference["content_digest"]:
            raise TextOnlyPilotError("locked_execution_input_changed")
        return value
    target = _mapping(_mapping(bundle.get("body"), "execution_bundle_body")["public_brief_input"]["child_target"], "child_target")
    first_receipt = artifact("first_receipt", _MEASUREMENT_RECEIPT_KIND)
    first_sidecar = artifact("first_sidecar", _SIDECAR_KIND)
    second_receipt = artifact("second_receipt", _MEASUREMENT_RECEIPT_KIND)
    second_sidecar = artifact("second_sidecar", _SIDECAR_KIND)
    first_measurement = _mapping(first_sidecar.get("body"), "first_measurement_sidecar_body")
    result = _mapping(first_measurement.get("measurement_result"), "first_measurement_result")
    tolerance = _mapping(first_measurement.get("measurement_tolerance"), "first_measurement_tolerance")
    expected_answer = _numeric_answer(result.get("value"), "first_measurement_value")
    absolute_tolerance = _numeric_answer(tolerance.get("absolute"), "first_measurement_tolerance")
    work_item_id = str(lock_body["work_item_id"])
    ground_truth_record = _registered_measurement_ground_truth_record(
        registry=ground_truth_registry,
        work_item_id=work_item_id,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        answer={_ANSWER_FIELD: _nearest_whole_degree(expected_answer)},
    )

    brief_input = _stage_inputs(lock=lock, bundle=bundle, plan=plan, run_id=run_id, target=target, first_receipt=first_receipt, first_sidecar=first_sidecar, second_receipt=second_receipt, second_sidecar=second_sidecar)
    brief = _completed_output(root, state_body, "brief_builder")
    if brief is None:
        brief_result = _invoke(paths, root=root, state_body=state_body, executor=executor, adapters=active_adapters, policy=policy, run_id=run_id, role="brief_builder", input_contract=brief_input)
        brief = dict(brief_result.output_contract)
    try:
        brief = validate_target_spec(brief)
    except MeasurementContractError as exc:
        raise TextOnlyPilotError("brief_builder_target_invalid") from exc
    if brief != target:
        raise TextOnlyPilotError("brief_builder_changed_the_locked_child_target")

    author_input = _stage_inputs(lock=lock, bundle=bundle, plan=plan, run_id=run_id, target=target, first_receipt=first_receipt, first_sidecar=first_sidecar, second_receipt=second_receipt, second_sidecar=second_sidecar, brief=brief)
    draft = _completed_output(root, state_body, "author")
    if draft is None:
        author_result = _invoke(paths, root=root, state_body=state_body, executor=executor, adapters=active_adapters, policy=policy, run_id=run_id, role="author", input_contract=author_input)
        draft = dict(author_result.output_contract)
    _question_draft_gate(draft)
    candidate = _compose_candidate(
        draft,
        target=target,
        run_id=run_id,
        work_item_id=work_item_id,
        plan_digest=plan["content_digest"],
        config_digest=str(lock_body["config_digest"]),
        knowledge_digest=str(lock_body["knowledge_boundary_digest"]),
        viewer_profile_digest=str(lock_body["viewer_profile_digest"]),
        expected_answer=expected_answer,
        question_policy=question_policy,
        gt_provenance=_ground_truth_provenance(ground_truth_record, ground_truth_registry),
    )
    candidate_path = _candidate_path(root)
    if candidate_path.exists():
        existing_candidate = _mapping(read_json(candidate_path), "composed_candidate")
        if existing_candidate != candidate:
            raise TextOnlyPilotError("deterministic_candidate_composition_changed")
    else:
        atomic_write_json(candidate_path, candidate, private=True)
    candidate_digest = digest(candidate)
    _candidate_gate(candidate, target=target, run_id=run_id, work_item_id=work_item_id, plan_digest=plan["content_digest"], config_digest=str(lock_body["config_digest"]), knowledge_digest=str(lock_body["knowledge_boundary_digest"]), viewer_profile_digest=str(lock_body["viewer_profile_digest"]), expected_answer=expected_answer, tolerance=absolute_tolerance, ground_truth_registry=ground_truth_registry, question_policy=question_policy)
    try:
        validate_question_coverage(
            [candidate],
            policy=question_policy,
            approved_profile_override=_mapping(lock_body.get("question_coverage_override"), "question_coverage_override"),
        )
    except QuestionContractError as exc:
        raise TextOnlyPilotError("candidate_question_coverage_invalid") from exc

    truth = _completed_output(root, state_body, "truth_builder")
    if truth is None:
        truth = _materialize_truth_stage(
            paths,
            root=root,
            state_body=state_body,
            run_id=run_id,
            candidate=candidate,
            target=target,
            receipt=first_receipt,
            sidecar=first_sidecar,
            work_item_id=work_item_id,
        )
    truth_digest = digest(truth)
    _truth_gate(
        truth,
        candidate=candidate,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        work_item_id=work_item_id,
    )

    independent_truth = _materialize_truth_record(
        candidate=candidate,
        target=target,
        receipt=second_receipt,
        sidecar=second_sidecar,
        work_item_id=work_item_id,
        purpose="verifier_independent_evidence",
    )
    independent_truth_reference = _persist_independent_truth_record(
        paths,
        root=root,
        truth=independent_truth,
    )

    verifier_input = _stage_inputs(
        lock=lock,
        bundle=bundle,
        plan=plan,
        run_id=run_id,
        target=target,
        first_receipt=first_receipt,
        first_sidecar=first_sidecar,
        second_receipt=second_receipt,
        second_sidecar=second_sidecar,
        brief=brief,
        candidate=candidate,
        independent_truth_record=independent_truth,
    )
    verification = _completed_output(root, state_body, "verifier")
    if verification is None:
        verifier_result = _invoke(paths, root=root, state_body=state_body, executor=executor, adapters=active_adapters, policy=policy, run_id=run_id, role="verifier", input_contract=verifier_input)
        verification = dict(verifier_result.output_contract)
    verification_digest = digest(verification)
    handoff = _mapping(verifier_input.get("independent_measurement_handoff"), "independent_measurement_handoff")
    _verifier_gate(verification, candidate_digest=candidate_digest, evidence_digest=str(second_sidecar["body"]["evidence_digest"]), rubric_lock_digest=str(lock_body["config_digest"]), work_item_id=work_item_id, handoff_digest=str(handoff["handoff_digest"]), receipt_digests=list(handoff["receipt_digests"]))

    state_body["status"] = "verification_complete_pending_human_review"
    state_body["candidate_digest"] = candidate_digest
    state_body["truth_digest"] = truth_digest
    state_body["independent_truth_record"] = independent_truth_reference
    state_body["verification_digest"] = verification_digest
    state_body["staging_enabled"] = False
    state_body["release_eligible"] = False
    final = _write_state(root, run_id=run_id, body=state_body, status="verification_complete_pending_human_review")
    append_event(paths, "v32_text_only_pilot_complete_pending_human_review", {"run_id": run_id, "candidate_digest": candidate_digest, "verification_digest": verification_digest})
    return {
        "run_id": run_id,
        "status": "verification_complete_pending_human_review",
        "state_path": str(root / "state.json"),
        "state_digest": final["content_digest"],
        "question_draft_path": str(_stage_output_path(root, "author")),
        "candidate_path": str(candidate_path),
        "verification_path": str(_stage_output_path(root, "verifier")),
        "staging_enabled": False,
        "release_eligible": False,
    }


def _execution_failure_phase(error: BaseException) -> str:
    code = _safe_failure_code(error)
    if code is None:
        return "unclassified_execution_abort"
    if code.startswith("question_draft_"):
        return "question_draft_gate"
    if code.startswith("candidate_"):
        return "candidate_composition_or_gate"
    if code.startswith("deterministic_truth_"):
        return "deterministic_truth_gate"
    if code.startswith("verifier_"):
        return "verifier_gate"
    if code.startswith("one_slot_") or code.startswith("text_only_"):
        return "control_plane_state_gate"
    return "execution_failure"


def execute_text_only_pilot(
    paths: WorkspacePaths,
    *,
    run_id: str,
    routing_policy_path: str | Path,
    runtime_config_path: str | Path,
    environ: Mapping[str, str] | None = None,
    adapters: Mapping[tuple[str, str], TypedWorker] | None = None,
) -> dict[str, Any]:
    """Execute one slot and freeze all exceptional exits before propagating.

    A process can still be killed below Python (for example SIGKILL or host
    loss), but every Python-level exception including ``SystemExit`` and
    ``KeyboardInterrupt`` writes a terminal failure summary. This removes the
    former ambiguous ``live_running`` recovery path and prohibits retries.
    """

    try:
        return _execute_text_only_pilot_impl(
            paths,
            run_id=run_id,
            routing_policy_path=routing_policy_path,
            runtime_config_path=runtime_config_path,
            environ=environ,
            adapters=adapters,
        )
    except BaseException as exc:
        try:
            freeze_aborted_text_only_pilot(
                paths,
                run_id=run_id,
                failure_phase=_execution_failure_phase(exc),
                error=exc,
            )
        except Exception as freeze_error:
            raise TextOnlyPilotError("text_only_pilot_abort_could_not_be_frozen") from freeze_error
        raise


__all__ = [
    "TextOnlyPilotError",
    "TextOnlyPilotInputs",
    "create_one_slot_provider_runtime",
    "execute_text_only_pilot",
    "freeze_aborted_text_only_pilot",
    "freeze_committed_interrupted_text_only_pilot",
    "freeze_incomplete_text_only_pilot",
    "prepare_text_only_pilot",
    "run_no_network_gate",
]
