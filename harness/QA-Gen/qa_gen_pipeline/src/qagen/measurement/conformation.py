"""Fail-closed native-measurement adapter for the V32 conformation pilot.

This module is intentionally narrow.  It exists because the selected V32
conformation asset is an XYZ scene whose private inventory maps opaque atom
IDs to host-only atom indices.  It does not accept arbitrary scripts,
coordinates, asset paths, or selector formats.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import stat
from typing import Any

from ..contracts import digest, make_envelope, require_digest, verify_envelope
from ..errors import InputError
from ..paths import WorkspacePaths, require_within
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from ..viewer.batch_scene_host import (
    HOST_ID,
    BatchReplayScene,
    BatchSceneReplayError,
    ControlledBatchViewerReplayHost,
)
from ..viewer.batch_scene_replay import BatchSceneRegistry
from .contracts import (
    MeasurementContractError,
    validate_measurement_receipt_body,
    validate_measurement_request,
    validate_target_spec,
)
from .host import (
    MeasurementHostError,
    execute_measurement_request,
    load_measurement_receipt,
)
from .inventory import SceneInventoryError, load_scene_inventory


class NativeConformationMeasurementError(MeasurementHostError):
    """The V32 native conformation adapter cannot prove an exact execution."""


_VIEWER_RECEIPT_KIND = "qagen_native_conformation_viewer_receipt"
_REPLAY_KIND = "qagen_native_measurement_replay_consistency"
_LEAK_SCAN_KIND = "qagen_native_measurement_leak_scan"
_TARGET_LEAF = "chemistry.conformation_analysis"
_ANSWER_FIELD = "dihedral_angle_deg"


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


def _regular_private_file(paths: WorkspacePaths, value: str | Path, label: str) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise NativeConformationMeasurementError(f"{label}_unavailable")
    try:
        path = require_within(paths.private, raw, label)
        metadata = path.stat()
    except (InputError, OSError) as exc:
        raise NativeConformationMeasurementError(f"{label}_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise NativeConformationMeasurementError(f"{label}_unavailable")
    return path


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeConformationMeasurementError(f"{label}_invalid")
    return dict(value)


def _checked_target(
    paths: WorkspacePaths,
    *,
    target_spec: Mapping[str, Any],
    request: Mapping[str, Any],
    inventory_path: str | Path,
    registry_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], BatchSceneRegistry, BatchReplayScene]:
    """Bind all local artifacts before a browser receives any scene bytes."""

    try:
        inventory = load_scene_inventory(paths, inventory_path)
        target = validate_target_spec(
            target_spec,
            inventory=inventory["body"],
            inventory_digest=str(inventory["content_digest"]),
        )
        checked_request = validate_measurement_request(request, target_spec=target)
        registry = BatchSceneRegistry.load(paths, registry_path)
        descriptor = registry.public_scene_descriptor(target["anonymous_scene_ref"])
        binding = registry.resolve(target["anonymous_scene_ref"])
    except (
        SceneInventoryError,
        MeasurementContractError,
        BatchSceneReplayError,
    ) as exc:
        raise NativeConformationMeasurementError(
            "native_conformation_binding_failed"
        ) from exc

    if (
        target.get("target_leaf") != _TARGET_LEAF
        or target.get("native_operation") != "measure_dihedral"
        or target.get("allowed_native_operations") != ["measure_dihedral"]
        or target.get("answer_fields") != [_ANSWER_FIELD]
        or checked_request.get("request_mode")
        not in {"candidate", "preauthor_native_measurement_pilot"}
        or inventory["body"].get("scene_registry_digest") != registry.content_digest
        or descriptor.get("anonymous_scene_ref") != target["anonymous_scene_ref"]
        or descriptor.get("viewer_family") != "molstar"
        or descriptor.get("asset_format") != "xyz"
        or "measure_dihedral" not in descriptor.get("operation_allowlist", [])
        or binding.get("asset_format") != "xyz"
        or binding.get("anonymous_scene_ref") != target["anonymous_scene_ref"]
    ):
        raise NativeConformationMeasurementError("native_conformation_binding_failed")
    primary = binding.get("asset_bytes")
    primary_digest = binding.get("asset_content_digest")
    if not isinstance(primary, bytes) or not isinstance(primary_digest, str):
        raise NativeConformationMeasurementError("native_conformation_binding_failed")
    try:
        scene = BatchReplayScene(
            anonymous_scene_ref=str(binding["anonymous_scene_ref"]),
            viewer_family="molstar",
            primary_bytes=primary,
            primary_digest=primary_digest,
            primary_format="xyz",
        )
    except InputError as exc:
        raise NativeConformationMeasurementError(
            "native_conformation_binding_failed"
        ) from exc
    return target, checked_request, registry, scene


def _persist_viewer_receipt(
    paths: WorkspacePaths,
    *,
    request_digest: str,
    target_spec_digest: str,
    source_record_digest: str,
    anonymous_scene_ref: str,
    native: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one private, content-addressed viewer execution receipt."""

    native_value = _mapping(native, "native viewer result")
    expected = {
        "native_operation",
        "operation_args",
        "measurement_result",
        "viewer_session_digest",
        "executor_identity",
        "render_digest",
    }
    if (
        set(native_value) != expected
        or native_value.get("native_operation") != "measure_dihedral"
    ):
        raise NativeConformationMeasurementError(
            "native_conformation_viewer_result_invalid"
        )
    operation_args = _mapping(
        native_value.get("operation_args"), "native viewer operation args"
    )
    result = _mapping(
        native_value.get("measurement_result"), "native viewer measurement result"
    )
    executor_identity = _mapping(
        native_value.get("executor_identity"), "native viewer executor identity"
    )
    if (
        set(result) != {"value", "unit", "absolute_tolerance"}
        or result.get("unit") != "degree"
    ):
        raise NativeConformationMeasurementError(
            "native_conformation_viewer_result_invalid"
        )
    value = result.get("value")
    tolerance = result.get("absolute_tolerance")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or float(tolerance) <= 0
    ):
        raise NativeConformationMeasurementError(
            "native_conformation_viewer_result_invalid"
        )
    try:
        viewer_session_digest = require_digest(
            native_value.get("viewer_session_digest"), "viewer session digest"
        )
        render_digest = require_digest(
            native_value.get("render_digest"), "viewer render digest"
        )
        require_digest(
            executor_identity.get("runtime_identity_digest"),
            "viewer executor runtime identity digest",
        )
    except InputError as exc:
        raise NativeConformationMeasurementError(
            "native_conformation_viewer_result_invalid"
        ) from exc
    if set(executor_identity) != {
        "host_id",
        "implementation",
        "runtime_identity_digest",
    }:
        raise NativeConformationMeasurementError(
            "native_conformation_viewer_result_invalid"
        )
    body = {
        "receipt_version": 1,
        "measurement_request_digest": request_digest,
        "target_spec_digest": target_spec_digest,
        "source_record_digest": source_record_digest,
        "anonymous_scene_ref": anonymous_scene_ref,
        "native_operation": "measure_dihedral",
        "operation_args": operation_args,
        "measurement_result": {"value": float(value), "unit": "degree"},
        "measurement_tolerance": {"absolute": float(tolerance), "unit": "degree"},
        "viewer_session_digest": viewer_session_digest,
        "executor_identity": executor_identity,
        "render_digest": render_digest,
        "local_only": True,
        "release_eligible": False,
    }
    envelope = make_envelope(
        kind=_VIEWER_RECEIPT_KIND,
        identifier=f"native-conformation-viewer-{_short(request_digest)}-{_short(viewer_session_digest)}",
        status="completed",
        body=body,
        producer="qagen/0.1.0:native_conformation_measurement",
    )
    output = (
        _private_directory(paths, "measurement", "viewer-receipts")
        / f"viewer-{_short(envelope['content_digest'])}.json"
    )
    atomic_write_json(output, envelope, private=True)
    return {
        "path": str(output),
        "content_digest": envelope["content_digest"],
        "evidence_digest": digest(
            {
                "viewer_receipt_digest": envelope["content_digest"],
                "render_digest": render_digest,
                "native_operation": "measure_dihedral",
            }
        ),
        "measurement_result": body["measurement_result"],
        "measurement_tolerance": body["measurement_tolerance"],
        "operation_args": body["operation_args"],
        "viewer_session_digest": body["viewer_session_digest"],
        "executor_identity": body["executor_identity"],
    }


def execute_v32_native_conformation_measurement(
    paths: WorkspacePaths,
    *,
    request: Mapping[str, Any],
    target_spec: Mapping[str, Any],
    inventory_path: str | Path,
    registry_path: str | Path,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Execute one exact V32 XYZ dihedral request through the locked viewer."""

    paths.ensure()
    target, checked_request, registry, scene = _checked_target(
        paths,
        target_spec=target_spec,
        request=request,
        inventory_path=inventory_path,
        registry_path=registry_path,
    )
    request_digest = digest(checked_request)
    viewer_bundle = registry.viewer_bundle
    host = ControlledBatchViewerReplayHost(viewer_bundle["root"])
    try:

        def executor(
            operation: str, backend: Mapping[str, Any], scene_ref: str
        ) -> Mapping[str, Any]:
            if (
                operation != "measure_dihedral"
                or scene_ref != target["anonymous_scene_ref"]
            ):
                raise NativeConformationMeasurementError(
                    "native_conformation_binding_failed"
                )
            native = host.native_measure_xyz_dihedral(
                scene, selector=backend, timeout_seconds=timeout_seconds
            )
            viewer = _persist_viewer_receipt(
                paths,
                request_digest=request_digest,
                target_spec_digest=checked_request["target_spec_digest"],
                source_record_digest=checked_request["source_record_digest"],
                anonymous_scene_ref=checked_request["anonymous_scene_ref"],
                native=native,
            )
            return {
                "truth": {_ANSWER_FIELD: viewer["measurement_result"]["value"]},
                "viewer_receipt_digest": viewer["content_digest"],
                "evidence_digest": viewer["evidence_digest"],
                "operation_args": viewer["operation_args"],
                "measurement_result": viewer["measurement_result"],
                "measurement_tolerance": viewer["measurement_tolerance"],
                "viewer_session_digest": viewer["viewer_session_digest"],
                "executor_identity": viewer["executor_identity"],
            }

        result = execute_measurement_request(
            paths,
            request=checked_request,
            target_spec=target,
            inventory_path=inventory_path,
            executor=executor,
        )
    except (
        MeasurementHostError,
        BatchSceneReplayError,
        NativeConformationMeasurementError,
    ) as exc:
        raise NativeConformationMeasurementError(
            "native_conformation_measurement_failed"
        ) from exc
    finally:
        host.shutdown()
    append_event(
        paths,
        "native_conformation_measurement_completed",
        {
            "measurement_request_digest": result["measurement_request_digest"],
            "measurement_receipt_digest": result["measurement_receipt_digest"],
            "private_sidecar_digest": result["private_sidecar_digest"],
            "registry_digest": registry.content_digest,
            "host_id": HOST_ID,
        },
    )
    return result


def _load_sidecar(
    paths: WorkspacePaths, value: str | Path
) -> tuple[Path, dict[str, Any]]:
    path = _regular_private_file(paths, value, "measurement_private_sidecar")
    try:
        raw = read_json(path)
        envelope = verify_envelope(raw, kind="qagen_measurement_private_sidecar")
    except (InputError, OSError) as exc:
        raise NativeConformationMeasurementError(
            "measurement_private_sidecar_unavailable"
        ) from exc
    body = _mapping(envelope.get("body"), "measurement private sidecar body")
    return path, {"content_digest": envelope["content_digest"], "body": body}


def verify_native_measurement_replay_consistency(
    paths: WorkspacePaths,
    *,
    first_measurement: Mapping[str, Any],
    second_measurement: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two fresh sessions without persisting their numerical values."""

    first = _mapping(first_measurement, "first native measurement")
    second = _mapping(second_measurement, "second native measurement")
    required = {
        "measurement_request_digest",
        "measurement_receipt_path",
        "measurement_receipt_digest",
        "private_sidecar_path",
        "private_sidecar_digest",
        "status",
    }
    if (
        set(first) != required
        or set(second) != required
        or first.get("status") != "completed"
        or second.get("status") != "completed"
    ):
        raise NativeConformationMeasurementError(
            "native_measurement_replay_input_invalid"
        )
    first_sidecar_path, first_sidecar = _load_sidecar(
        paths, str(first["private_sidecar_path"])
    )
    second_sidecar_path, second_sidecar = _load_sidecar(
        paths, str(second["private_sidecar_path"])
    )
    try:
        first_receipt = load_measurement_receipt(
            paths, str(first["measurement_receipt_path"])
        )
        second_receipt = load_measurement_receipt(
            paths, str(second["measurement_receipt_path"])
        )
    except MeasurementHostError as exc:
        raise NativeConformationMeasurementError(
            "native_measurement_replay_input_invalid"
        ) from exc
    if (
        first_sidecar["content_digest"] != first.get("private_sidecar_digest")
        or second_sidecar["content_digest"] != second.get("private_sidecar_digest")
        or first_receipt["content_digest"] != first.get("measurement_receipt_digest")
        or second_receipt["content_digest"] != second.get("measurement_receipt_digest")
    ):
        raise NativeConformationMeasurementError(
            "native_measurement_replay_input_invalid"
        )
    first_body = first_sidecar["body"]
    second_body = second_sidecar["body"]
    first_receipt_body = _mapping(first_receipt["body"], "first measurement receipt")
    second_receipt_body = _mapping(second_receipt["body"], "second measurement receipt")
    first_result = _mapping(
        first_body.get("measurement_result"), "first measurement result"
    )
    second_result = _mapping(
        second_body.get("measurement_result"), "second measurement result"
    )
    first_tolerance = _mapping(
        first_body.get("measurement_tolerance"), "first measurement tolerance"
    )
    second_tolerance = _mapping(
        second_body.get("measurement_tolerance"), "second measurement tolerance"
    )
    comparable = (
        first_body.get("measurement_request_digest")
        == second_body.get("measurement_request_digest")
        and first_body.get("measurement_request_digest")
        == first.get("measurement_request_digest")
        and second_body.get("measurement_request_digest")
        == second.get("measurement_request_digest")
        and first_receipt_body.get("measurement_request_digest")
        == first_body.get("measurement_request_digest")
        and second_receipt_body.get("measurement_request_digest")
        == second_body.get("measurement_request_digest")
        and first_receipt_body.get("private_sidecar_digest")
        == first_sidecar["content_digest"]
        and second_receipt_body.get("private_sidecar_digest")
        == second_sidecar["content_digest"]
        and first_receipt_body.get("viewer_session_digest")
        != second_receipt_body.get("viewer_session_digest")
        and first_result.get("unit") == "degree"
        and first_result.get("unit") == second_result.get("unit")
        and first_tolerance.get("unit") == first_result.get("unit")
        and second_tolerance.get("unit") == second_result.get("unit")
        and isinstance(first_result.get("value"), (int, float))
        and not isinstance(first_result.get("value"), bool)
        and isinstance(second_result.get("value"), (int, float))
        and not isinstance(second_result.get("value"), bool)
        and isinstance(first_tolerance.get("absolute"), (int, float))
        and not isinstance(first_tolerance.get("absolute"), bool)
        and isinstance(second_tolerance.get("absolute"), (int, float))
        and not isinstance(second_tolerance.get("absolute"), bool)
    )
    tolerance = max(
        float(first_tolerance.get("absolute", 0)),
        float(second_tolerance.get("absolute", 0)),
    )
    passed = (
        comparable
        and tolerance > 0
        and abs(float(first_result["value"]) - float(second_result["value"]))
        <= tolerance
    )
    body = {
        "consistency_version": 1,
        "first_measurement_receipt_digest": first["measurement_receipt_digest"],
        "second_measurement_receipt_digest": second["measurement_receipt_digest"],
        "first_private_sidecar_digest": first_sidecar["content_digest"],
        "second_private_sidecar_digest": second_sidecar["content_digest"],
        "measurement_request_digest": first_body.get("measurement_request_digest"),
        "unit": "degree",
        "status": "passed" if passed else "failed",
        "local_only": True,
        "release_eligible": False,
    }
    envelope = make_envelope(
        kind=_REPLAY_KIND,
        identifier=f"native-measurement-replay-{_short(str(first['measurement_receipt_digest']))}",
        status=body["status"],
        body=body,
        producer="qagen/0.1.0:native_conformation_measurement",
    )
    output = (
        _private_directory(paths, "measurement", "replay-consistency")
        / f"replay-{_short(envelope['content_digest'])}.json"
    )
    atomic_write_json(output, envelope, private=True)
    return {
        "path": str(output),
        "content_digest": envelope["content_digest"],
        "status": body["status"],
        "checked_sidecar_count": 2,
        "first_sidecar_path": str(first_sidecar_path),
        "second_sidecar_path": str(second_sidecar_path),
    }


def scan_native_measurement_leaks(
    paths: WorkspacePaths,
    *,
    measurement: Mapping[str, Any],
) -> dict[str, Any]:
    """Ensure numeric truth remains inside a 0600 sidecar, not its receipt."""

    result = _mapping(measurement, "native measurement")
    required = {
        "measurement_request_digest",
        "measurement_receipt_path",
        "measurement_receipt_digest",
        "private_sidecar_path",
        "private_sidecar_digest",
        "status",
    }
    if set(result) != required or result.get("status") != "completed":
        raise NativeConformationMeasurementError(
            "native_measurement_leak_scan_input_invalid"
        )
    sidecar_path, sidecar = _load_sidecar(
        paths, str(result.get("private_sidecar_path", ""))
    )
    receipt_path = _regular_private_file(
        paths, str(result.get("measurement_receipt_path", "")), "measurement_receipt"
    )
    try:
        receipt_envelope = verify_envelope(
            read_json(receipt_path), kind="qagen_measurement_receipt"
        )
        receipt_body = _mapping(
            receipt_envelope.get("body"), "measurement receipt body"
        )
    except (InputError, OSError) as exc:
        raise NativeConformationMeasurementError(
            "measurement_leak_scan_failed"
        ) from exc
    if receipt_envelope["content_digest"] != result.get(
        "measurement_receipt_digest"
    ) or sidecar["content_digest"] != result.get("private_sidecar_digest"):
        raise NativeConformationMeasurementError(
            "native_measurement_leak_scan_input_invalid"
        )
    violations = 0
    try:
        validate_measurement_receipt_body(receipt_body)
    except MeasurementContractError:
        # A malformed receipt cannot be treated as safe.  Continue to emit a
        # bounded failed scan so an operator can audit the exact artifact.
        violations += 1
    if {"truth", "measurement_result", "operation_args", "executor_identity"} & set(
        receipt_body
    ):
        violations += 1
    if receipt_body.get("measurement_request_digest") != result.get(
        "measurement_request_digest"
    ):
        violations += 1
    body = {
        "scan_version": 1,
        "measurement_receipt_digest": receipt_envelope["content_digest"],
        "private_sidecar_digest": sidecar["content_digest"],
        "status": "passed" if violations == 0 else "failed",
        "checked_artifact_count": 2,
        "violation_count": violations,
        "local_only": True,
        "release_eligible": False,
    }
    envelope = make_envelope(
        kind=_LEAK_SCAN_KIND,
        identifier=f"native-measurement-leak-scan-{_short(receipt_envelope['content_digest'])}",
        status=body["status"],
        body=body,
        producer="qagen/0.1.0:native_conformation_measurement",
    )
    output = (
        _private_directory(paths, "measurement", "leak-scans")
        / f"scan-{_short(envelope['content_digest'])}.json"
    )
    atomic_write_json(output, envelope, private=True)
    return {
        "path": str(output),
        "content_digest": envelope["content_digest"],
        "status": body["status"],
        "checked_artifact_count": 2,
        "violation_count": violations,
        "sidecar_path": str(sidecar_path),
    }


__all__ = [
    "NativeConformationMeasurementError",
    "execute_v32_native_conformation_measurement",
    "scan_native_measurement_leaks",
    "verify_native_measurement_replay_consistency",
]
