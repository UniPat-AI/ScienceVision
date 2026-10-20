from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest
import yaml

from qagen.contracts import digest
from qagen.viewer.capability_mapping import (
    ViewerCapabilityMappingError,
    effective_target_operations,
    load_compiled_selection_capability_mapping,
    validate_capability_receipt,
)

from conftest import PACKAGE_ROOT


def _row(
    *,
    source_track: str,
    target_leaf: str,
    source_viewer: str,
    source_operations: list[str],
    asset_path: str,
) -> dict[str, Any]:
    return {
        "slot_ref": {"id": "slot-fixture", "content_digest": digest({"slot": target_leaf, "track": source_track})},
        "access_track": {"source": source_track, "effective": source_track, "conversion": "preserved"},
        "target_leaf": target_leaf,
        "viewer_hint": {"source_viewer": source_viewer, "allowed_operations": source_operations},
        "assets": [
            {
                "role": "primary",
                "path": asset_path,
                "bytes": 1,
                "sha256": "a" * 64,
            }
        ],
    }


def _mapping_reference(profile_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
    loaded = load_compiled_selection_capability_mapping()
    return {
        "mapping_id": loaded["mapping"]["id"],
        "content_digest": loaded["content_digest"],
        "profile_id": profile_id,
        "profile_digest": loaded["profile_digests"][profile_id],
        "receipt": {"path": "receipts/viewer-capability.json", "content_digest": digest(receipt)},
    }


def _receipt(
    *,
    row: dict[str, Any],
    profile_id: str,
    strategy: str,
    viewer_family: str,
    asset_formats: list[str],
    viewer_digest: str,
    operations: list[str],
) -> dict[str, Any]:
    loaded = load_compiled_selection_capability_mapping()
    return {
        "schema_version": 1,
        "kind": "qagen_compiled_selection_viewer_capability_receipt",
        "id": "viewer-capability-fixture",
        "decision": "approved",
        "reviewer": "viewer-owner",
        "reviewed_at": "2026-08-27T00:00:00Z",
        "mapping_id": loaded["mapping"]["id"],
        "mapping_digest": loaded["content_digest"],
        "profile_id": profile_id,
        "profile_digest": loaded["profile_digests"][profile_id],
        "slot_digest": row["slot_ref"]["content_digest"],
        "source_access_track": row["access_track"]["source"],
        "generation_strategy": strategy,
        "target_leaf": row["target_leaf"],
        "source_viewer": row["viewer_hint"]["source_viewer"],
        "source_hint_digest": digest(row["viewer_hint"]),
        "asset_formats": asset_formats,
        "viewer_family": viewer_family,
        "viewer_lock_content_digest": viewer_digest,
        "allowed_operations": operations,
    }


def _validate(
    *,
    row: dict[str, Any],
    strategy: str,
    profile_id: str,
    viewer_family: str,
    asset_formats: list[str],
    target_required: set[str],
    domain_allowed: set[str],
    operations: list[str],
) -> dict[str, Any]:
    viewer_digest = digest({"viewer": row["slot_ref"]["content_digest"]})
    receipt = _receipt(
        row=row,
        profile_id=profile_id,
        strategy=strategy,
        viewer_family=viewer_family,
        asset_formats=asset_formats,
        viewer_digest=viewer_digest,
        operations=operations,
    )
    return validate_capability_receipt(
        receipt,
        reference=_mapping_reference(profile_id, receipt),
        receipt_digest=digest(receipt),
        row=row,
        generation_strategy=strategy,
        target_leaf=row["target_leaf"],
        target_required_operations=target_required,
        domain_allowed_operations=domain_allowed,
        viewer_lock_content_digest=viewer_digest,
        lock_allowed_operations=set(operations),
    )


def test_render_is_a_base_visual_operation_not_a_leaf_blocker() -> None:
    required, allowed = effective_target_operations({"inspect", "rotate"}, {"inspect", "rotate"})

    assert required == {"render", "rotate"}
    assert allowed == {"render", "rotate"}


def test_reauthor_on_asset_ignores_an_unsupported_legacy_hint() -> None:
    row = _row(
        source_track="raw_compute",
        target_leaf="chemistry.steric_accessibility",
        source_viewer="molstar_deployed",
        source_operations=["viewer_measure_dihedral"],
        asset_path="assets/chemistry/fixture.cif",
    )

    result = _validate(
        row=row,
        strategy="reauthor_on_asset",
        profile_id="molstar_mcp_local_review_v1",
        viewer_family="molstar",
        asset_formats=["cif"],
        target_required={"inspect", "rotate"},
        domain_allowed={"inspect", "rotate", "render", "zoom", "measure_distance"},
        operations=["render", "rotate"],
    )

    assert result["source_hint_mode"] == "not_used_for_reauthor"
    assert result["allowed_operations"] == ["render", "rotate"]
    assert "assets/chemistry/fixture.cif" not in json.dumps(result, sort_keys=True)


def test_preserve_intent_requires_every_source_hint_to_be_controlled() -> None:
    row = _row(
        source_track="viewer_measurement",
        target_leaf="chemistry.steric_accessibility",
        source_viewer="molstar_deployed",
        source_operations=["viewer_measure_angle"],
        asset_path="assets/chemistry/fixture.cif",
    )

    with pytest.raises(ViewerCapabilityMappingError, match="preserve_intent source hint has no controlled operation mapping"):
        _validate(
            row=row,
            strategy="preserve_intent",
            profile_id="molstar_mcp_local_review_v1",
            viewer_family="molstar",
            asset_formats=["cif"],
            target_required={"inspect", "rotate"},
            domain_allowed={"inspect", "rotate", "render", "zoom", "measure_distance"},
            operations=["render", "rotate"],
        )


def test_distance_dihedral_and_volumetric_modes_are_supported() -> None:
    conformation_reauthor = _row(
        source_track="raw_compute",
        target_leaf="chemistry.conformation_analysis",
        source_viewer="molstar_deployed",
        source_operations=["viewer_rotate"],
        asset_path="assets/chemistry/fixture.xyz",
    )
    reauthor = _validate(
        row=conformation_reauthor,
        strategy="reauthor_on_asset",
        profile_id="molstar_mcp_local_review_v1",
        viewer_family="molstar",
        asset_formats=["xyz"],
        target_required={"inspect", "rotate", "measure_distance"},
        domain_allowed={"inspect", "rotate", "render", "measure_distance", "measure_dihedral"},
        operations=["measure_distance", "render", "rotate"],
    )
    assert reauthor["allowed_operations"] == ["measure_distance", "render", "rotate"]
    assert reauthor["source_hint_mode"] == "not_used_for_reauthor"

    conformation_preserve = _row(
        source_track="viewer_measurement",
        target_leaf="chemistry.conformation_analysis",
        source_viewer="molstar_deployed",
        source_operations=["viewer_rotate", "viewer_zoom"],
        asset_path="assets/chemistry/fixture.xyz",
    )
    preserve = _validate(
        row=conformation_preserve,
        strategy="preserve_intent",
        profile_id="molstar_mcp_local_review_v1",
        viewer_family="molstar",
        asset_formats=["xyz"],
        target_required={"inspect", "rotate", "measure_dihedral"},
        domain_allowed={"inspect", "rotate", "render", "zoom", "measure_distance", "measure_dihedral"},
        operations=["measure_dihedral", "render", "rotate", "zoom"],
    )
    assert preserve["allowed_operations"] == ["measure_dihedral", "render", "rotate", "zoom"]
    assert preserve["source_hint_mode"] == "required"

    medical = _row(
        source_track="raw_compute",
        target_leaf="medical.volumetric_target_geometry",
        source_viewer="niivue_deployed",
        source_operations=["viewer_bbox", "viewer_slice"],
        asset_path="assets/medical/fixture.nii.gz",
    )
    volumetric = _validate(
        row=medical,
        strategy="reauthor_on_asset",
        profile_id="niivue_mcp_local_review_v1",
        viewer_family="niivue",
        asset_formats=["nii.gz"],
        target_required={"inspect", "slice", "window_level", "bbox", "overlay_compare"},
        domain_allowed={"inspect", "render", "slice", "window_level", "bbox", "overlay_compare"},
        operations=["bbox", "overlay_compare", "render", "slice", "window_level"],
    )
    assert volumetric["allowed_operations"] == ["bbox", "overlay_compare", "render", "slice", "window_level"]

    profiles = load_compiled_selection_capability_mapping()["profiles"]
    molstar = profiles["molstar_mcp_local_review_v1"]
    niivue = profiles["niivue_mcp_local_review_v1"]
    assert "measure_dihedral" not in molstar["unsupported_operations"]
    assert molstar["native_measurement_operations"] == ["measure_distance", "measure_dihedral"]
    assert "zoom" in molstar["controlled_operations"]
    assert "measure_dihedral" in molstar["controlled_operations"]
    assert "measure_area" in niivue["unsupported_operations"]
    assert "measure_volume" in niivue["unsupported_operations"]
    assert niivue["native_measurement_operations"] == []


def test_profile_claims_are_anchored_to_existing_mcp_implementations() -> None:
    mapping_path = PACKAGE_ROOT / "configs" / "viewer-capabilities" / "compiled_selection_v32.yaml"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    assert isinstance(mapping, dict)

    repository_root = PACKAGE_ROOT.parents[1]
    molstar_source = (repository_root / "viewer" / "shared" / "molstar-mcp-adapter.js").read_text(encoding="utf-8")
    niivue_source = (repository_root / "viewer" / "shared" / "niivue-mcp-adapter.js").read_text(encoding="utf-8")
    host_source = (PACKAGE_ROOT / "src" / "qagen" / "viewer" / "batch_scene_host.py").read_text(encoding="utf-8")
    assert "viewer_measure_distance" in molstar_source
    assert "viewer_measure_dihedral" not in molstar_source
    assert "manager?.addDihedral" in host_source
    assert "manager.addDihedral" in host_source
    assert "_molstar_zoom" in host_source
    assert "camera.setState" in host_source
    assert "viewer_bbox" in niivue_source
    assert "viewer_measure_area" not in niivue_source
    assert "viewer_measure_volume" not in niivue_source

    profiles = {profile["id"]: profile for profile in mapping["profiles"]}
    assert profiles["molstar_mcp_local_review_v1"]["native_measurement_operations"] == [
        "measure_distance",
        "measure_dihedral",
    ]
    assert "zoom" in profiles["molstar_mcp_local_review_v1"]["controlled_operations"]
    assert "measure_dihedral" in profiles["molstar_mcp_local_review_v1"]["controlled_operations"]
    assert "bbox" in profiles["niivue_mcp_local_review_v1"]["controlled_operations"]


def test_receipt_cannot_be_replayed_against_another_viewer_lock() -> None:
    row = _row(
        source_track="raw_compute",
        target_leaf="chemistry.steric_accessibility",
        source_viewer="molstar_deployed",
        source_operations=["viewer_rotate"],
        asset_path="assets/chemistry/fixture.cif",
    )
    viewer_digest = digest({"viewer": "original"})
    receipt = _receipt(
        row=row,
        profile_id="molstar_mcp_local_review_v1",
        strategy="reauthor_on_asset",
        viewer_family="molstar",
        asset_formats=["cif"],
        viewer_digest=viewer_digest,
        operations=["render", "rotate"],
    )
    tampered = deepcopy(receipt)
    reference = _mapping_reference("molstar_mcp_local_review_v1", tampered)

    with pytest.raises(ViewerCapabilityMappingError, match="viewer_lock_content_digest"):
        validate_capability_receipt(
            tampered,
            reference=reference,
            receipt_digest=digest(tampered),
            row=row,
            generation_strategy="reauthor_on_asset",
            target_leaf=row["target_leaf"],
            target_required_operations={"inspect", "rotate"},
            domain_allowed_operations={"inspect", "rotate", "render"},
            viewer_lock_content_digest=digest({"viewer": "another"}),
            lock_allowed_operations={"render", "rotate"},
        )
