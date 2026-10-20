"""Private host execution for a validated viewer-native MeasurementRequest.

The host receives a validated inventory and resolves its opaque selector IDs
in-process.  It seals the returned value in a 0600 sidecar.  The accompanying
receipt contains only immutable digests, so it can be cited by evidence and
verification workers without disclosing the measured value.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import os
from pathlib import Path
from typing import Any

from ..contracts import digest, make_envelope, require_digest, verify_envelope
from ..errors import InputError
from ..paths import WorkspacePaths, require_within
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from .contracts import (
    MeasurementContractError,
    build_measurement_request,
    selector_backend,
    validate_measurement_private_sidecar_body,
    validate_measurement_receipt_body,
    validate_measurement_request,
    validate_measurement_verifier_approval_body,
    validate_target_spec,
)
from .inventory import SceneInventoryError, load_scene_inventory


class MeasurementHostError(MeasurementContractError):
    """A host cannot execute a request under the private contract."""


NativeMeasurementExecutor = Callable[[str, Mapping[str, Any], str], Mapping[str, Any]]
_SIDECAR_KIND = "qagen_measurement_private_sidecar"
_RECEIPT_KIND = "qagen_measurement_receipt"
_APPROVAL_KIND = "qagen_measurement_verifier_approval"


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _private_directory(paths: WorkspacePaths, *parts: str) -> Path:
    directory = paths.private.joinpath(*parts)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MeasurementHostError(f"{label} must be an object")
    return dict(value)


def _executor_result(value: Any, *, answer_fields: list[str]) -> dict[str, Any]:
    result = _require_mapping(value, "native measurement result")
    if set(result) != {
        "truth",
        "viewer_receipt_digest",
        "evidence_digest",
        "operation_args",
        "measurement_result",
        "measurement_tolerance",
        "viewer_session_digest",
        "executor_identity",
    }:
        raise MeasurementHostError("native measurement result has invalid fields")
    truth = _require_mapping(result.get("truth"), "native measurement truth")
    if set(truth) != set(answer_fields):
        raise MeasurementHostError("native measurement truth does not match TargetSpec answer fields")
    try:
        viewer_receipt = require_digest(result.get("viewer_receipt_digest"), "native viewer receipt digest")
        evidence_digest = require_digest(result.get("evidence_digest"), "native evidence digest")
        viewer_session_digest = require_digest(result.get("viewer_session_digest"), "native viewer session digest")
    except InputError as exc:
        raise MeasurementHostError(str(exc)) from exc
    operation_args = _require_mapping(result.get("operation_args"), "native measurement operation args")
    if not operation_args:
        raise MeasurementHostError("native measurement operation args are empty")
    measurement_result = _require_mapping(result.get("measurement_result"), "native measurement numeric result")
    if set(measurement_result) != {"value", "unit"}:
        raise MeasurementHostError("native measurement numeric result has invalid fields")
    measured_value = measurement_result.get("value")
    measured_unit = measurement_result.get("unit")
    if (
        isinstance(measured_value, bool)
        or not isinstance(measured_value, (int, float))
        or not math.isfinite(float(measured_value))
        or not isinstance(measured_unit, str)
        or not measured_unit.strip()
    ):
        raise MeasurementHostError("native measurement numeric result is invalid")
    measurement_tolerance = _require_mapping(result.get("measurement_tolerance"), "native measurement tolerance")
    if set(measurement_tolerance) != {"absolute", "unit"}:
        raise MeasurementHostError("native measurement tolerance has invalid fields")
    absolute_tolerance = measurement_tolerance.get("absolute")
    tolerance_unit = measurement_tolerance.get("unit")
    if (
        isinstance(absolute_tolerance, bool)
        or not isinstance(absolute_tolerance, (int, float))
        or not math.isfinite(float(absolute_tolerance))
        or float(absolute_tolerance) <= 0
        or tolerance_unit != measured_unit
    ):
        raise MeasurementHostError("native measurement tolerance is invalid")
    executor_identity = _require_mapping(result.get("executor_identity"), "native measurement executor identity")
    if set(executor_identity) != {"host_id", "implementation", "runtime_identity_digest"}:
        raise MeasurementHostError("native measurement executor identity has invalid fields")
    if (
        not isinstance(executor_identity.get("host_id"), str)
        or not executor_identity["host_id"].strip()
        or not isinstance(executor_identity.get("implementation"), str)
        or not executor_identity["implementation"].strip()
    ):
        raise MeasurementHostError("native measurement executor identity is invalid")
    try:
        require_digest(executor_identity.get("runtime_identity_digest"), "native executor runtime identity digest")
    except InputError as exc:
        raise MeasurementHostError(str(exc)) from exc
    return {
        "truth": truth,
        "viewer_receipt_digest": viewer_receipt,
        "evidence_digest": evidence_digest,
        "operation_args": operation_args,
        "measurement_result": {"value": float(measured_value), "unit": measured_unit},
        "measurement_tolerance": {"absolute": float(absolute_tolerance), "unit": measured_unit},
        "viewer_session_digest": viewer_session_digest,
        "executor_identity": executor_identity,
    }


def make_measurement_request(
    target_spec: Mapping[str, Any],
    *,
    candidate_digest: str,
    request_id: str,
    request_mode: str = "candidate",
) -> dict[str, Any]:
    """Public helper for a DAG to bind a candidate to a brief TargetSpec."""

    return build_measurement_request(
        target_spec,
        candidate_digest=candidate_digest,
        request_id=request_id,
        request_mode=request_mode,
    )


def execute_measurement_request(
    paths: WorkspacePaths,
    *,
    request: Mapping[str, Any],
    target_spec: Mapping[str, Any],
    inventory_path: str | Path,
    executor: NativeMeasurementExecutor,
) -> dict[str, Any]:
    """Execute one exact request and persist a private value plus safe receipt.

    ``executor`` is deliberately a narrow host callback.  It receives only
    an operation, a validated in-process backend selector, and the anonymous
    scene reference; it is never given a source record, question, answer key,
    filesystem path, or arbitrary model text.
    """

    if not callable(executor):
        raise MeasurementHostError("native measurement executor is required")
    paths.ensure()
    try:
        inventory = load_scene_inventory(paths, inventory_path)
        validated_target = validate_target_spec(
            target_spec,
            inventory=inventory["body"],
            inventory_digest=str(inventory["content_digest"]),
        )
        validated_request = validate_measurement_request(request, target_spec=validated_target)
    except (SceneInventoryError, MeasurementContractError) as exc:
        raise MeasurementHostError(str(exc)) from exc
    backend = selector_backend(inventory["body"], validated_request["selector"])
    try:
        native = _executor_result(
            executor(
                str(validated_request["native_operation"]),
                backend,
                str(validated_request["anonymous_scene_ref"]),
            ),
            answer_fields=list(validated_request["answer_fields"]),
        )
    except MeasurementHostError:
        raise
    except Exception as exc:
        raise MeasurementHostError("controlled_measurement_host_failed") from exc

    request_digest = digest(validated_request)
    sidecar_body = validate_measurement_private_sidecar_body(
        {
            "sidecar_version": 2,
            "measurement_request_digest": request_digest,
            "viewer_receipt_digest": native["viewer_receipt_digest"],
            "evidence_digest": native["evidence_digest"],
            "operation_args": native["operation_args"],
            "measurement_result": native["measurement_result"],
            "measurement_tolerance": native["measurement_tolerance"],
            "viewer_session_digest": native["viewer_session_digest"],
            "executor_identity": native["executor_identity"],
            "truth": native["truth"],
        }
    )
    sidecar = make_envelope(
        kind=_SIDECAR_KIND,
        identifier=f"measurement-sidecar-{_short(request_digest)}",
        status="completed",
        body=sidecar_body,
        producer="qagen/0.1.0:measurement_host",
    )
    sidecar_path = _private_directory(paths, "measurement", "sidecars") / f"sidecar-{_short(sidecar['content_digest'])}.json"
    atomic_write_json(sidecar_path, sidecar, private=True)

    receipt_body = validate_measurement_receipt_body(
        {
            "receipt_version": 2,
            "measurement_request_digest": request_digest,
            "target_spec_digest": validated_request["target_spec_digest"],
            "source_record_digest": validated_request["source_record_digest"],
            "anonymous_scene_ref": validated_request["anonymous_scene_ref"],
            "native_operation": validated_request["native_operation"],
            "viewer_receipt_digest": native["viewer_receipt_digest"],
            "evidence_digest": native["evidence_digest"],
            "operation_args_digest": digest(native["operation_args"]),
            "measurement_result_digest": digest(native["measurement_result"]),
            "measurement_unit": native["measurement_result"]["unit"],
            "measurement_tolerance": native["measurement_tolerance"],
            "viewer_session_digest": native["viewer_session_digest"],
            "executor_identity_digest": digest(native["executor_identity"]),
            "private_sidecar_digest": sidecar["content_digest"],
            "status": "completed",
        }
    )
    receipt = make_envelope(
        kind=_RECEIPT_KIND,
        identifier=f"measurement-receipt-{_short(request_digest)}",
        status="completed",
        body=receipt_body,
        producer="qagen/0.1.0:measurement_host",
    )
    receipt_path = _private_directory(paths, "measurement", "receipts") / f"receipt-{_short(receipt['content_digest'])}.json"
    atomic_write_json(receipt_path, receipt, private=True)
    append_event(
        paths,
        "measurement_host_completed",
        {
            "measurement_request_digest": request_digest,
            "target_spec_digest": validated_request["target_spec_digest"],
            "measurement_receipt_digest": receipt["content_digest"],
            "private_sidecar_digest": sidecar["content_digest"],
        },
    )
    return {
        "measurement_request_digest": request_digest,
        "measurement_receipt_path": str(receipt_path),
        "measurement_receipt_digest": receipt["content_digest"],
        "private_sidecar_path": str(sidecar_path),
        "private_sidecar_digest": sidecar["content_digest"],
        "status": "completed",
    }


def load_measurement_receipt(paths: WorkspacePaths, value: str | Path) -> dict[str, Any]:
    """Load a safe receipt without loading or returning the private sidecar."""

    raw = Path(value)
    try:
        path = require_within(paths.private, raw, "measurement receipt")
        document = read_json(path)
    except (InputError, OSError) as exc:
        raise MeasurementHostError("measurement_receipt_unavailable") from exc
    if not isinstance(document, Mapping):
        raise MeasurementHostError("measurement_receipt_unavailable")
    try:
        receipt = verify_envelope(document, kind=_RECEIPT_KIND)
        body = validate_measurement_receipt_body(_require_mapping(receipt.get("body"), "measurement receipt body"))
    except (InputError, MeasurementContractError) as exc:
        raise MeasurementHostError("measurement_receipt_unavailable") from exc
    return {"path": path, "content_digest": receipt["content_digest"], "body": body}


def approve_measurement_verifier(
    paths: WorkspacePaths,
    *,
    candidate_digest: str,
    target_spec_digest: str,
    measurement_receipt_digest: str,
    verifier_output_digest: str,
    rationale: str,
) -> dict[str, Any]:
    """Persist the independent approval gate after the verifier has passed.

    This receipt is intentionally separate from the author, truth builder,
    evidence worker, and model verifier output.  It can only approve exact
    digest bindings and never carries the sidecar's measurement value.
    """

    body = validate_measurement_verifier_approval_body(
        {
            "approval_version": 1,
            "decision": "approved",
            "actor_role": "independent_verifier",
            "candidate_digest": candidate_digest,
            "target_spec_digest": target_spec_digest,
            "measurement_receipt_digest": measurement_receipt_digest,
            "verifier_output_digest": verifier_output_digest,
            "rationale": rationale,
        }
    )
    paths.ensure()
    receipt = make_envelope(
        kind=_APPROVAL_KIND,
        identifier=f"measurement-verifier-approval-{_short(candidate_digest)}-{_short(verifier_output_digest)}",
        status="approved",
        body=body,
        producer="qagen/0.1.0:measurement_verifier_gate",
    )
    path = _private_directory(paths, "measurement", "approvals") / f"approval-{_short(receipt['content_digest'])}.json"
    atomic_write_json(path, receipt, private=True)
    append_event(
        paths,
        "measurement_verifier_approved",
        {
            "candidate_digest": candidate_digest,
            "target_spec_digest": target_spec_digest,
            "measurement_receipt_digest": measurement_receipt_digest,
            "verifier_output_digest": verifier_output_digest,
            "approval_digest": receipt["content_digest"],
        },
    )
    return {"approval_path": str(path), "approval_digest": receipt["content_digest"], "status": "approved"}


def load_measurement_verifier_approval(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    candidate_digest: str,
    target_spec_digest: str,
    measurement_receipt_digest: str,
    verifier_output_digest: str,
) -> dict[str, Any]:
    """Require an exact independent approval before a candidate advances."""

    raw = Path(value)
    try:
        path = require_within(paths.private, raw, "measurement verifier approval")
        document = read_json(path)
    except (InputError, OSError) as exc:
        raise MeasurementHostError("measurement_verifier_approval_unavailable") from exc
    if not isinstance(document, Mapping):
        raise MeasurementHostError("measurement_verifier_approval_unavailable")
    try:
        approval = verify_envelope(document, kind=_APPROVAL_KIND)
        body = validate_measurement_verifier_approval_body(_require_mapping(approval.get("body"), "measurement verifier approval body"))
    except (InputError, MeasurementContractError) as exc:
        raise MeasurementHostError("measurement_verifier_approval_unavailable") from exc
    expected = {
        "candidate_digest": candidate_digest,
        "target_spec_digest": target_spec_digest,
        "measurement_receipt_digest": measurement_receipt_digest,
        "verifier_output_digest": verifier_output_digest,
    }
    if approval.get("status") != "approved" or any(body.get(key) != value for key, value in expected.items()):
        raise MeasurementHostError("measurement_verifier_approval_binding_mismatch")
    return {"path": path, "content_digest": approval["content_digest"], "body": body}


__all__ = [
    "MeasurementHostError",
    "NativeMeasurementExecutor",
    "approve_measurement_verifier",
    "execute_measurement_request",
    "load_measurement_receipt",
    "load_measurement_verifier_approval",
    "make_measurement_request",
]
