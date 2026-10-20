#!/usr/bin/env python3
"""Run the bounded V32 conformation viewer-native measurement pilot.

This is a pre-authoring gate, not a candidate-generation command.  It
narrows one historical three-field TargetSpec to the one value that the
locked Mol* viewer can actually display and measure, then executes that
measurement twice in fresh viewer sessions.  Numerical values remain inside
private measurement sidecars; stdout contains only statuses, paths, and
content digests.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
from typing import Any

from jsonschema import Draft7Validator

from qagen.contracts import digest, make_envelope, require_digest, verify_envelope
from qagen.errors import InputError
from qagen.measurement import (
    MeasurementContractError,
    NativeConformationMeasurementError,
    SceneInventoryError,
    execute_v32_native_conformation_measurement,
    load_scene_inventory,
    make_measurement_request,
    scan_native_measurement_leaks,
    validate_target_spec,
    verify_native_measurement_replay_consistency,
)
from qagen.paths import WorkspacePaths, require_within
from qagen.resource_paths import PackageResourceError, config_path
from qagen.storage.atomic import atomic_write_json, read_json
from qagen.viewer.batch_scene_replay import BatchSceneRegistry, BatchSceneReplayError


_PARENT_BATCH_KIND = "vnext_selection_batch_manifest"
_CHILD_TARGET_KIND = "qagen_v32_native_conformation_child_target"
_ANCHOR_KIND = "qagen_v32_native_conformation_preauthor_anchor"
_RESULT_KIND = "qagen_v32_native_conformation_pilot_result"
_TARGET_LEAF = "chemistry.conformation_analysis"
_PARENT_ANSWER_FIELDS = [
    "dihedral_angle_deg",
    "torsion_sign",
    "conformation_class",
]
_CHILD_ANSWER_FIELDS = ["dihedral_angle_deg"]
_MAX_INPUT_BYTES = 2 * 1024 * 1024


class PilotError(InputError):
    """The bounded native measurement pilot cannot prove its preconditions."""


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotError(f"{label}_invalid")
    return dict(value)


def _private_regular(paths: WorkspacePaths, value: str | Path, label: str) -> Path:
    raw = Path(value)
    if raw.is_symlink():
        raise PilotError(f"{label}_unavailable")
    try:
        path = require_within(paths.private, raw, label)
        metadata = path.stat()
    except (InputError, OSError) as exc:
        raise PilotError(f"{label}_unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_size > _MAX_INPUT_BYTES
    ):
        raise PilotError(f"{label}_unavailable")
    return path


def _load_private_object(
    paths: WorkspacePaths, value: str | Path, label: str
) -> tuple[Path, dict[str, Any]]:
    path = _private_regular(paths, value, label)
    try:
        document = _mapping(read_json(path), label)
    except (InputError, OSError) as exc:
        raise PilotError(f"{label}_invalid") from exc
    return path, document


def _manifest_schema() -> dict[str, Any]:
    try:
        schema = read_json(
            config_path("schemas", "selection_batch_manifest.schema.json")
        )
    except PackageResourceError as exc:
        raise PilotError("selection_batch_schema_unavailable") from exc
    return _mapping(schema, "selection_batch_schema")


def _validate_manifest_body(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    body = _mapping(value, label)
    errors = sorted(
        Draft7Validator(_manifest_schema()).iter_errors(body),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise PilotError(f"{label}_invalid")
    return body


def _load_parent_batch(
    paths: WorkspacePaths, value: str | Path
) -> tuple[Path, dict[str, Any]]:
    path, document = _load_private_object(paths, value, "parent_selection_batch")
    try:
        envelope = verify_envelope(document, kind=_PARENT_BATCH_KIND)
    except InputError as exc:
        raise PilotError("parent_selection_batch_invalid") from exc
    body = _validate_manifest_body(
        _mapping(envelope["body"], "parent_selection_batch_body"),
        "parent_selection_batch",
    )
    return path, {"content_digest": envelope["content_digest"], "body": body}


def _load_parent_target(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    inventory: Mapping[str, Any],
    inventory_digest: str,
) -> tuple[Path, dict[str, Any]]:
    path, target = _load_private_object(paths, value, "parent_target_spec")
    try:
        checked = validate_target_spec(
            target,
            inventory=inventory,
            inventory_digest=inventory_digest,
        )
    except MeasurementContractError as exc:
        raise PilotError("parent_target_spec_invalid") from exc
    if (
        checked.get("target_leaf") != _TARGET_LEAF
        or checked.get("native_operation") != "measure_dihedral"
        or checked.get("allowed_native_operations") != ["measure_dihedral"]
        or checked.get("answer_fields") != _PARENT_ANSWER_FIELDS
    ):
        raise PilotError(
            "parent_target_spec_is_not_the_expected_historical_conformation_target"
        )
    return path, checked


def _parent_row(
    parent_batch: Mapping[str, Any], *, selection_slot_digest: str
) -> dict[str, Any]:
    try:
        require_digest(selection_slot_digest, "scene inventory selection slot digest")
    except InputError as exc:
        raise PilotError("scene_inventory_slot_binding_invalid") from exc
    rows = parent_batch.get("body", {}).get("rows")
    if not isinstance(rows, list):
        raise PilotError("parent_selection_batch_invalid")
    matches: list[dict[str, Any]] = []
    for raw in rows:
        row = _mapping(raw, "parent_selection_row")
        slot = _mapping(row.get("slot_ref"), "parent_selection_slot")
        if slot.get("content_digest") == selection_slot_digest:
            matches.append(row)
    if len(matches) != 1 or matches[0].get("target_leaf") != _TARGET_LEAF:
        raise PilotError("parent_selection_slot_is_not_the_expected_conformation_row")
    return matches[0]


def _persist_envelope(
    paths: WorkspacePaths,
    *,
    directory: Path,
    filename: str,
    envelope: Mapping[str, Any],
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    path = directory / filename
    if path.exists():
        existing_path, existing = _load_private_object(paths, path, "pilot_artifact")
        try:
            checked = verify_envelope(existing, kind=str(envelope["kind"]))
        except InputError as exc:
            raise PilotError("pilot_artifact_conflict") from exc
        if checked["content_digest"] != envelope["content_digest"]:
            raise PilotError("pilot_artifact_conflict")
        return existing_path
    atomic_write_json(path, dict(envelope), private=True)
    return path


def _child_target(
    parent_target: Mapping[str, Any], *, registry_digest: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    parent_digest = digest(dict(parent_target))
    context = {
        "pilot_version": 1,
        "parent_target_spec_digest": parent_digest,
        "scene_inventory_digest": parent_target["scene_inventory_digest"],
        "scene_registry_digest": registry_digest,
        "anonymous_scene_ref": parent_target["anonymous_scene_ref"],
        "target_leaf": _TARGET_LEAF,
        "native_operation": "measure_dihedral",
        "selector_digest": digest(parent_target["selector"]),
        "historical_truth_equivalence": "not_asserted",
        "derivation": "narrow_to_one_viewer_native_numeric_field",
    }
    target = {
        "target_spec_version": 1,
        "source_record_digest": parent_target["source_record_digest"],
        "scene_inventory_digest": parent_target["scene_inventory_digest"],
        "anonymous_scene_ref": parent_target["anonymous_scene_ref"],
        "candidate_plan_digest": digest(context),
        "target_leaf": _TARGET_LEAF,
        "allowed_native_operations": ["measure_dihedral"],
        "native_operation": "measure_dihedral",
        "selector": deepcopy(parent_target["selector"]),
        "answer_fields": list(_CHILD_ANSWER_FIELDS),
    }
    try:
        return validate_target_spec(target), context
    except MeasurementContractError as exc:
        raise PilotError("child_target_spec_invalid") from exc


def _safe_measurement_ref(value: Mapping[str, Any]) -> dict[str, str]:
    expected = {
        "measurement_request_digest",
        "measurement_receipt_path",
        "measurement_receipt_digest",
        "private_sidecar_path",
        "private_sidecar_digest",
        "status",
    }
    if set(value) != expected or value.get("status") != "completed":
        raise PilotError("native_measurement_result_invalid")
    return {
        "measurement_request_digest": str(value["measurement_request_digest"]),
        "measurement_receipt_digest": str(value["measurement_receipt_digest"]),
        "private_sidecar_digest": str(value["private_sidecar_digest"]),
    }


def _persist_result(
    paths: WorkspacePaths,
    *,
    anchor_digest: str,
    child_target_digest: str,
    status: str,
    body: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    result = make_envelope(
        kind=_RESULT_KIND,
        identifier=f"v32-native-conformation-pilot-{_short(anchor_digest)}-{status}",
        status=status,
        body={
            "pilot_version": 1,
            "anchor_digest": anchor_digest,
            "child_target_spec_digest": child_target_digest,
            "status": status,
            "generation_eligible": False,
            "release_eligible": False,
            **dict(body),
        },
        producer="qagen/0.1.0:v32_native_conformation_pilot",
    )
    path = _persist_envelope(
        paths,
        directory=paths.private / "measurement" / "pilot-results",
        filename=f"result-{_short(result['content_digest'])}.json",
        envelope=result,
    )
    return path, result


def run_pilot(
    paths: WorkspacePaths,
    *,
    parent_selection_batch: str | Path,
    parent_target_spec: str | Path,
    inventory_path: str | Path,
    registry_path: str | Path,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Run two fresh native measurements and derive a one-slot manifest on pass."""

    paths.ensure()
    try:
        inventory = load_scene_inventory(paths, inventory_path)
        registry = BatchSceneRegistry.load(paths, registry_path)
    except (SceneInventoryError, BatchSceneReplayError) as exc:
        raise PilotError("pilot_scene_binding_unavailable") from exc
    inventory_body = _mapping(inventory["body"], "scene_inventory")
    if (
        inventory_body.get("target_leaf") != _TARGET_LEAF
        or inventory_body.get("scene_registry_digest") != registry.content_digest
    ):
        raise PilotError("pilot_scene_binding_unavailable")
    parent_batch_path, parent_batch = _load_parent_batch(paths, parent_selection_batch)
    parent_target_path, parent_target = _load_parent_target(
        paths,
        parent_target_spec,
        inventory=inventory_body,
        inventory_digest=str(inventory["content_digest"]),
    )
    selection_slot_digest = inventory_body.get("selection_slot_digest")
    if not isinstance(selection_slot_digest, str):
        raise PilotError("scene_inventory_slot_binding_invalid")
    row = _parent_row(parent_batch, selection_slot_digest=selection_slot_digest)
    child_target, context = _child_target(
        parent_target, registry_digest=registry.content_digest
    )
    child_target_digest = digest(child_target)

    child_record = make_envelope(
        kind=_CHILD_TARGET_KIND,
        identifier=f"v32-native-conformation-target-{_short(child_target_digest)}",
        status="preauthor_measurement_only",
        body={
            "target_spec": child_target,
            "child_target_spec_digest": child_target_digest,
            "parent_target_spec_digest": digest(parent_target),
            "historical_truth_equivalence": "not_asserted",
            "parent_answer_fields": list(_PARENT_ANSWER_FIELDS),
            "child_answer_fields": list(_CHILD_ANSWER_FIELDS),
        },
        producer="qagen/0.1.0:v32_native_conformation_pilot",
    )
    child_record_path = _persist_envelope(
        paths,
        directory=paths.private / "measurement" / "child-targets",
        filename=f"child-target-{_short(child_record['content_digest'])}.json",
        envelope=child_record,
    )
    anchor = make_envelope(
        kind=_ANCHOR_KIND,
        identifier=f"v32-native-conformation-anchor-{_short(child_target_digest)}",
        status="preauthor_measurement_only",
        body={
            "anchor_version": 1,
            "parent_selection_batch_digest": parent_batch["content_digest"],
            "parent_target_spec_digest": digest(parent_target),
            "child_target_artifact_digest": child_record["content_digest"],
            "child_target_spec_digest": child_target_digest,
            "scene_inventory_digest": inventory["content_digest"],
            "scene_registry_digest": registry.content_digest,
            "selection_slot_digest": selection_slot_digest,
            "historical_truth_equivalence": "not_asserted",
            "parent_answer_fields": list(_PARENT_ANSWER_FIELDS),
            "child_answer_fields": list(_CHILD_ANSWER_FIELDS),
            "context": context,
            "generation_eligible": False,
            "release_eligible": False,
        },
        producer="qagen/0.1.0:v32_native_conformation_pilot",
    )
    anchor_path = _persist_envelope(
        paths,
        directory=paths.private / "measurement" / "preauthor-anchors",
        filename=f"anchor-{_short(anchor['content_digest'])}.json",
        envelope=anchor,
    )

    measurement_request = make_measurement_request(
        child_target,
        candidate_digest=anchor["content_digest"],
        request_id=f"measurement-v32-native-conformation-{_short(anchor['content_digest'])}",
        request_mode="preauthor_native_measurement_pilot",
    )
    try:
        first = execute_v32_native_conformation_measurement(
            paths,
            request=measurement_request,
            target_spec=child_target,
            inventory_path=inventory_path,
            registry_path=registry_path,
            timeout_seconds=timeout_seconds,
        )
        second = execute_v32_native_conformation_measurement(
            paths,
            request=measurement_request,
            target_spec=child_target,
            inventory_path=inventory_path,
            registry_path=registry_path,
            timeout_seconds=timeout_seconds,
        )
        first_scan = scan_native_measurement_leaks(paths, measurement=first)
        second_scan = scan_native_measurement_leaks(paths, measurement=second)
        replay = verify_native_measurement_replay_consistency(
            paths,
            first_measurement=first,
            second_measurement=second,
        )
    except (
        NativeConformationMeasurementError,
        MeasurementContractError,
        BatchSceneReplayError,
    ) as exc:
        result_path, result = _persist_result(
            paths,
            anchor_digest=anchor["content_digest"],
            child_target_digest=child_target_digest,
            status="blocked",
            body={
                "blockers": [str(exc)],
                "generation_blockers": [
                    "native_measurement_pilot_not_attested",
                    "candidate_generation_not_started",
                ],
            },
        )
        return {
            "status": "blocked",
            "blocker": str(exc),
            "anchor_path": str(anchor_path),
            "anchor_digest": anchor["content_digest"],
            "child_target_path": str(child_record_path),
            "child_target_digest": child_target_digest,
            "result_path": str(result_path),
            "result_digest": result["content_digest"],
        }

    passed = (
        first_scan["status"] == second_scan["status"] == replay["status"] == "passed"
    )
    result_path, result = _persist_result(
        paths,
        anchor_digest=anchor["content_digest"],
        child_target_digest=child_target_digest,
        status="attested" if passed else "blocked",
        body={
            "measurements": {
                "first": _safe_measurement_ref(first),
                "second": _safe_measurement_ref(second),
            },
            "leak_scans": {
                "first_digest": first_scan["content_digest"],
                "first_status": first_scan["status"],
                "second_digest": second_scan["content_digest"],
                "second_status": second_scan["status"],
            },
            "replay_consistency_digest": replay["content_digest"],
            "replay_consistency_status": replay["status"],
            "generation_blockers": [
                "preauthor_native_measurement_only",
                "candidate_generation_not_started",
                "independent_author_verifier_human_review_required",
            ],
        },
    )
    output: dict[str, Any] = {
        "status": "attested" if passed else "blocked",
        "anchor_path": str(anchor_path),
        "anchor_digest": anchor["content_digest"],
        "child_target_path": str(child_record_path),
        "child_target_digest": child_target_digest,
        "result_path": str(result_path),
        "result_digest": result["content_digest"],
        "replay_consistency_digest": replay["content_digest"],
        "replay_consistency_status": replay["status"],
    }
    if not passed:
        output["blocker"] = "native_measurement_replay_or_leak_scan_failed"
        return output

    one_slot_body = {
        "source_selection": deepcopy(parent_batch["body"]["source_selection"]),
        "taxonomy_mapping": deepcopy(parent_batch["body"]["taxonomy_mapping"]),
        "total": 1,
        "mapping_coverage": {
            "source_leaf_count": 1,
            "mapped_leaf_count": 1,
            "unmapped_source_leaves": [],
        },
        "generation_eligible": False,
        "generation_blockers": [
            "preauthor_native_measurement_only",
            "candidate_generation_not_started",
            "independent_author_verifier_human_review_required",
        ],
        "rows": [deepcopy(row)],
    }
    _validate_manifest_body(one_slot_body, "one_slot_selection_manifest")
    manifest = make_envelope(
        kind=_PARENT_BATCH_KIND,
        identifier=f"v32-native-conformation-one-slot-{_short(anchor['content_digest'])}",
        status="native_measurement_attested_pending_generation_gates",
        body=one_slot_body,
        producer="qagen/0.1.0:v32_native_conformation_pilot",
    )
    manifest_path = _persist_envelope(
        paths,
        directory=paths.selection_batches / "native-measurement-pilots",
        filename=f"one-slot-{_short(manifest['content_digest'])}.vnext-batch.json",
        envelope=manifest,
    )
    output.update(
        {
            "selection_manifest_path": str(manifest_path),
            "selection_manifest_digest": manifest["content_digest"],
            "generation_eligible": False,
        }
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--parent-selection-batch", required=True, type=Path)
    parser.add_argument("--parent-target-spec", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    try:
        result = run_pilot(
            WorkspacePaths.from_value(args.workspace),
            parent_selection_batch=args.parent_selection_batch,
            parent_target_spec=args.parent_target_spec,
            inventory_path=args.inventory,
            registry_path=args.registry,
            timeout_seconds=args.timeout_seconds,
        )
    except PilotError as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "attested" else 2


if __name__ == "__main__":
    raise SystemExit(main())
