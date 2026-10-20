"""Minimal local contracts for controlled viewer requests and evidence.

These validators do not open a browser, read an asset, or probe a live scene.
They only validate declarations emitted from an already-attested local viewer
session and keep those declarations inside the capability profile.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Mapping

from ..contracts import require_digest
from ..errors import InputError
from .adapter import ViewerAdapterError, validate_solver_observation_payload


class ViewerContractError(InputError):
    """A scene, session request, or local evidence packet is not controlled."""


_FORBIDDEN_SCENE_MARKERS = ("file://", "http://", "https://", "..", "\\", ".pdb", ".sdf", ".mol", ".nii")
_FORBIDDEN_ACCESS_FLAGS = {
    "live_browser",
    "network_access",
    "asset_file_read",
    "raw_asset_metadata",
    "raw_asset_read",
    "arbitrary_compute",
    "unrestricted_compute",
}
_SESSION_REQUEST_KEYS = {"request_id", "viewer_profile_digest", "allowed_operations"}
_EVIDENCE_PACKET_KEYS = {
    "artifact_kind",
    "candidate_digest",
    "viewer_profile_digest",
    "viewer_session_request_id",
    "viewer_operations",
    "allowed_operations",
    "observations",
    "viewer_trace",
    "projection",
    "probe",
    "work_item_id",
    "state_transition",
    *_FORBIDDEN_ACCESS_FLAGS,
}


def validate_anonymous_scene_ref(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ViewerContractError("anonymous scene reference must be a non-empty string")
    scene_ref = value.strip()
    if scene_ref.startswith("/") or any(marker in scene_ref.lower() for marker in _FORBIDDEN_SCENE_MARKERS):
        raise ViewerContractError("scene reference must be anonymous and cannot expose an asset path or URL")
    if PurePath(scene_ref).is_absolute():
        raise ViewerContractError("scene reference cannot be an absolute path")
    return scene_ref


def _require_exact_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ViewerContractError(f"{label} has unsupported field: {unexpected[0]}")


def _operation_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ViewerContractError(f"{label} must be a non-empty string list")
    if len(set(value)) != len(value):
        raise ViewerContractError(f"{label} cannot contain duplicate operations")
    return list(value)


def _assert_no_forbidden_access(value: Any, label: str) -> None:
    """Reject an access claim anywhere in a local evidence declaration."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in _FORBIDDEN_ACCESS_FLAGS and nested is not False:
                raise ViewerContractError(f"{label}.{key} must be explicitly false when present")
            _assert_no_forbidden_access(nested, f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_forbidden_access(nested, f"{label}[{index}]")


def _assert_solver_safe_payload(value: Any, label: str) -> None:
    try:
        validate_solver_observation_payload(value, label)
    except ViewerAdapterError as exc:
        raise ViewerContractError(str(exc)) from exc


def validate_controlled_session_request(
    request: Mapping[str, Any],
    *,
    viewer_profile: Mapping[str, Any],
    approved_operations: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ViewerContractError("viewer session request must be an object")
    _require_exact_keys(request, _SESSION_REQUEST_KEYS, "viewer session request")
    profile_body = viewer_profile.get("body")
    if not isinstance(profile_body, Mapping):
        raise ViewerContractError("viewer profile is malformed")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ViewerContractError("viewer session request_id is required")
    profile_digest = viewer_profile.get("content_digest")
    require_digest(profile_digest, "viewer profile digest")
    if request.get("viewer_profile_digest") != profile_digest:
        raise ViewerContractError("viewer session request is bound to another viewer profile")
    operations = _operation_list(request.get("allowed_operations"), "viewer session request allowed_operations")
    profile_operations = profile_body.get("allowed_operations")
    if not isinstance(profile_operations, list) or not set(operations).issubset(set(profile_operations)):
        raise ViewerContractError("viewer session request exceeds the controlled profile operations")
    if approved_operations is not None and not set(operations).issubset(approved_operations):
        raise ViewerContractError("viewer session request exceeds the approved plan operations")
    return {
        "request_id": request_id.strip(),
        "viewer_profile_digest": profile_digest,
        "allowed_operations": list(operations),
    }


def validate_local_evidence_packet(
    evidence: Mapping[str, Any],
    *,
    candidate_digest: str,
    viewer_profile: Mapping[str, Any],
    approved_operations: set[str],
    require_full_trace: bool,
    expected_session_request_id: str | None = None,
) -> dict[str, Any]:
    """Validate a non-live evidence declaration against one controlled session."""
    if not isinstance(evidence, Mapping):
        raise ViewerContractError("viewer evidence packet must be an object")
    _require_exact_keys(evidence, _EVIDENCE_PACKET_KEYS, "viewer evidence packet")
    require_digest(candidate_digest, "candidate digest")
    if evidence.get("candidate_digest") != candidate_digest:
        raise ViewerContractError("viewer evidence is not bound to the candidate digest")
    profile_digest = viewer_profile.get("content_digest")
    require_digest(profile_digest, "viewer profile digest")
    if evidence.get("viewer_profile_digest") != profile_digest:
        raise ViewerContractError("viewer evidence is not bound to the viewer profile")
    _assert_no_forbidden_access(evidence, "viewer evidence packet")
    profile_body = viewer_profile.get("body")
    if not isinstance(profile_body, Mapping):
        raise ViewerContractError("viewer profile is malformed")
    profile_operations = profile_body.get("allowed_operations")
    if not isinstance(profile_operations, list) or not all(isinstance(item, str) and item for item in profile_operations):
        raise ViewerContractError("viewer profile allowed_operations is malformed")
    if not approved_operations or not approved_operations.issubset(set(profile_operations)):
        raise ViewerContractError("approved operations exceed the controlled viewer profile")
    if expected_session_request_id is not None:
        if not isinstance(expected_session_request_id, str) or not expected_session_request_id:
            raise ViewerContractError("expected viewer session request_id is malformed")
        declared_request_id = evidence.get("viewer_session_request_id")
        if declared_request_id is not None and declared_request_id != expected_session_request_id:
            raise ViewerContractError("viewer evidence is bound to another viewer session request")
        if require_full_trace and declared_request_id != expected_session_request_id:
            raise ViewerContractError("production evidence must bind the exact viewer session request")
    observations = evidence.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ViewerContractError("viewer evidence requires non-empty observations")
    observation_operations: set[str] = set()
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise ViewerContractError("viewer evidence observations must be objects")
        operation = observation.get("operation")
        if not isinstance(operation, str) or operation not in approved_operations:
            raise ViewerContractError(f"viewer evidence observation {index} uses an operation outside the approved plan")
        _assert_solver_safe_payload(observation, f"viewer evidence observation {index}")
        observation_operations.add(operation)

    declared_operations: set[str] | None = None
    for field in ("viewer_operations", "allowed_operations"):
        if field not in evidence:
            continue
        operations = set(_operation_list(evidence.get(field), f"viewer evidence {field}"))
        if not operations.issubset(approved_operations):
            raise ViewerContractError(f"viewer evidence {field} exceeds the approved plan operations")
        if declared_operations is not None and operations != declared_operations:
            raise ViewerContractError("viewer evidence operation declarations disagree")
        declared_operations = operations
    if declared_operations is not None and not observation_operations.issubset(declared_operations):
        raise ViewerContractError("viewer evidence observations exceed its declared operations")

    trace = evidence.get("viewer_trace")
    projection = evidence.get("projection")
    probe = evidence.get("probe")
    if require_full_trace and (
        not isinstance(trace, list)
        or not trace
        or not isinstance(projection, Mapping)
        or not projection
        or not isinstance(probe, Mapping)
        or not probe
    ):
        raise ViewerContractError("production evidence requires local viewer_trace, projection, and probe declarations")
    trace_operations: set[str] = set()
    if trace is not None:
        if not isinstance(trace, list) or not trace:
            raise ViewerContractError("viewer_trace must be a non-empty list when present")
        for event in trace:
            if not isinstance(event, Mapping):
                raise ViewerContractError("viewer_trace entries must be objects")
            operation = event.get("operation")
            if not isinstance(operation, str) or operation not in approved_operations:
                raise ViewerContractError("viewer_trace contains an operation outside the approved plan")
            trace_operations.add(operation)
            _assert_no_forbidden_access(event, "viewer_trace")
            _assert_solver_safe_payload(event, "viewer_trace")
    if require_full_trace and not observation_operations.issubset(trace_operations):
        raise ViewerContractError("production viewer_trace does not substantiate every observation")
    for label, value in (("projection", projection), ("probe", probe)):
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise ViewerContractError(f"{label} must be an object when present")
        _assert_no_forbidden_access(value, label)
        _assert_solver_safe_payload(value, label)
    return dict(evidence)
