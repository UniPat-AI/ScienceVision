"""Regression coverage for the scale-v2 round2 materialization gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from scripts import qa_qc, qa_qc_round2


def _asset(asset_id: str, repo_path: str, asset_format: str = "mmcif") -> dict:
    return {
        "asset_id": asset_id,
        "exists_on_disk": True,
        "format": asset_format,
        "name": asset_id,
        "path": f"/{repo_path}",
        "repo_path": repo_path,
        "viewer": "molstar",
    }


def test_ct_registry_target_detects_missing_binding_and_union_geometry(
    tmp_path: Path,
) -> None:
    (tmp_path / "ct.nii.gz").write_bytes(b"fixture")
    mask = np.zeros((3, 3, 3), dtype=np.uint8)
    mask[0, 0, 0] = 1
    mask[1, 1, 1] = 2
    mask[1, 1, 2] = 2
    nib.save(nib.Nifti1Image(mask, np.eye(4)), tmp_path / "mask.nii.gz")
    task = {
        "task_id": "ct-1",
        "knowledge": {"direction_id": "med_ct_lesion"},
        "asset": {
            **_asset("medical:ct-1", "ct.nii.gz", "nii.gz"),
            "qid": "m3d-refseg-train-ct-1",
            "mask_repo_path": "mask.nii.gz",
            "mask_path": "/mask.nii.gz",
        },
        "assets": [],
        "asset_grounding": {
            "verified_entities": {
                "voxel_count": 3,
                "extent_vox": [2, 2, 3],
            }
        },
    }
    task["assets"] = [task["asset"]]
    registry = {
        "m3d-refseg-train-ct-1": {
            "qid": "m3d-refseg-train-ct-1",
            "extra": {"mask_id": "2", "mask_label_count": 2},
        }
    }

    findings = qa_qc_round2.audit_task_materialization(
        task, registry, tmp_path, qa_qc_round2.MaskGeometryCache()
    )
    codes = {finding["code"] for finding in findings}

    assert "target_mask_binding_missing" in codes
    assert "target_mask_geometry_uses_union" in codes
    geometry = next(
        finding["evidence"]
        for finding in findings
        if finding["code"] == "target_mask_geometry_uses_union"
    )
    assert geometry["stored_geometry"] == geometry["union_geometry"]
    assert geometry["target_geometry"] == {
        "voxel_count": 2,
        "extent_vox": [1, 1, 2],
    }
    result = {"findings": findings, "eval": {}, "decision": "pass"}
    qa_qc._finalize_task(result, list(qa_qc.DEFAULT_EVAL_BASELINES))
    assert result["decision"] == "block"
    assert result["classification"]["grade"] == "unacceptable"
    assert {"grounding", "ground_truth"} <= set(result["classification"]["labels"])


def test_single_label_ct_missing_binding_is_review_not_block(tmp_path: Path) -> None:
    nib.save(
        nib.Nifti1Image(np.ones((2, 3, 4), dtype=np.uint8), np.eye(4)),
        tmp_path / "ct.nii.gz",
    )
    nib.save(
        nib.Nifti1Image(np.ones((2, 3, 4), dtype=np.uint8), np.eye(4)),
        tmp_path / "mask.nii.gz",
    )
    asset = {
        **_asset("medical:ct-2", "ct.nii.gz", "nii.gz"),
        "qid": "m3d-refseg-train-ct-2",
        "mask_repo_path": "mask.nii.gz",
        "mask_path": "/mask.nii.gz",
    }
    task = {
        "task_id": "ct-2",
        "knowledge": {"direction_id": "med_ct_lesion"},
        "asset": asset,
        "assets": [asset],
        "asset_grounding": {
            "verified_entities": {
                "voxel_count": 24,
                "extent_vox": [2, 3, 4],
            }
        },
    }
    registry = {
        "m3d-refseg-train-ct-2": {
            "qid": "m3d-refseg-train-ct-2",
            "extra": {"mask_id": "1", "mask_label_count": 1},
        }
    }

    findings = qa_qc_round2.audit_task_materialization(
        task, registry, tmp_path, qa_qc_round2.MaskGeometryCache()
    )

    assert [finding["code"] for finding in findings] == ["target_mask_binding_missing"]
    result = {"findings": findings, "eval": {}, "decision": "pass"}
    qa_qc._finalize_task(result, list(qa_qc.DEFAULT_EVAL_BASELINES))
    assert result["decision"] == "review"
    assert result["classification"]["grade"] == "needs_fix"


def test_mri_pair_passes_nifti_preflight_without_claiming_render(
    tmp_path: Path,
) -> None:
    image = np.arange(24, dtype=np.float32).reshape((2, 3, 4))
    mask = np.zeros((2, 3, 4), dtype=np.uint8)
    mask[1, 1, 1] = 1
    nib.save(nib.Nifti1Image(image, np.eye(4)), tmp_path / "mri.nii.gz")
    nib.save(nib.Nifti1Image(mask, np.eye(4)), tmp_path / "mask.nii.gz")
    asset = {
        **_asset("medical:mri-1", "mri.nii.gz", "nii.gz"),
        "mask_repo_path": "mask.nii.gz",
        "mask_path": "/mask.nii.gz",
    }
    task = {
        "task_id": "mri-1",
        "knowledge": {"direction_id": "med_mri_anatomy"},
        "asset": asset,
        "assets": [asset],
    }
    detail: dict = {}

    findings = qa_qc_round2.audit_task_materialization(
        task,
        {},
        tmp_path,
        qa_qc_round2.MaskGeometryCache(),
        detail=detail,
    )

    assert findings == []
    assert detail["materialization_scope"] == "static/preflight"
    assert detail["render_status"] == "not_run"
    assert detail["nifti_checks"]["image"]["valid"] is True
    assert detail["nifti_checks"]["mask"]["valid"] is True
    assert detail["nifti_checks"]["image_mask_shape_match"] is True


@pytest.mark.parametrize("contents", ["", "_entry.id fixture\n"])
def test_invalid_mmcif_is_a_materialization_blocker(
    tmp_path: Path, contents: str
) -> None:
    (tmp_path / "invalid.cif").write_text(contents, encoding="utf-8")
    asset = _asset("biology:invalid", "invalid.cif")
    task = {
        "task_id": "bio-invalid",
        "knowledge": {"direction_id": "bio_collision_check"},
        "asset": asset,
        "assets": [asset],
    }

    findings = qa_qc_round2.audit_task_materialization(
        task, {}, tmp_path, qa_qc_round2.MaskGeometryCache()
    )

    finding = next(
        finding
        for finding in findings
        if finding["code"] == "molecular_file_preflight_invalid"
    )
    assert finding["severity"] == "error"
    result = {"findings": findings, "eval": {}, "decision": "pass"}
    qa_qc._finalize_task(result, list(qa_qc.DEFAULT_EVAL_BASELINES))
    assert result["decision"] == "block"
    assert result["classification"]["grade"] == "unacceptable"


def test_missing_primary_and_incomplete_multi_asset_are_blockers(
    tmp_path: Path,
) -> None:
    present = tmp_path / "present.cif"
    present.write_text("data_fixture\n", encoding="utf-8")
    task = {
        "task_id": "multi-1",
        "knowledge": {"direction_id": "bio_multi_structure_compare"},
        "asset": _asset("biology:present", "present.cif"),
        "assets": [
            _asset("biology:present", "present.cif"),
            _asset("biology:missing", "missing.cif"),
        ],
    }

    findings = qa_qc_round2.audit_task_materialization(
        task, {}, tmp_path, qa_qc_round2.MaskGeometryCache()
    )
    codes = {finding["code"] for finding in findings}

    assert codes == {
        "asset_declared_present_but_missing",
        "multi_asset_materialization_incomplete",
    }
    missing = next(
        finding
        for finding in findings
        if finding["code"] == "asset_declared_present_but_missing"
    )
    assert missing["evidence"]["primary_missing"] is False


def test_complete_multi_asset_passes_live_flatten_and_staging_receipt(
    tmp_path: Path,
) -> None:
    for name in ("a.cif", "b.cif"):
        (tmp_path / name).write_text("data_fixture\n", encoding="utf-8")
    task = {
        "task_id": "multi-2",
        "knowledge": {"direction_id": "bio_multi_structure_compare"},
        "asset": _asset("biology:a", "a.cif"),
        "assets": [_asset("biology:a", "a.cif"), _asset("biology:b", "b.cif")],
    }

    detail: dict = {}
    findings = qa_qc_round2.audit_task_materialization(
        task, {}, tmp_path, qa_qc_round2.MaskGeometryCache(), detail=detail
    )

    assert findings == []
    receipt = detail["multi_asset_materialization_receipt"]
    assert receipt["status"] == "supported"
    assert receipt["declared_asset_count"] == 2
    assert receipt["flattened_asset_count"] == 2
    assert receipt["host_staged_asset_count"] == 2
    assert receipt["container_staged_asset_count"] == 2
    assert receipt["host_paths_exist"] is True
    assert receipt["staged_files_exist"] is True


def test_multi_asset_flatten_count_mismatch_is_blocked(tmp_path: Path) -> None:
    for name in ("a.cif", "b.cif"):
        (tmp_path / name).write_text("data_fixture\n", encoding="utf-8")
    task = {
        "task_id": "multi-flatten-mismatch",
        "knowledge": {"direction_id": "bio_multi_structure_compare"},
        "asset": _asset("biology:a", "a.cif"),
        "assets": [_asset("biology:a", "a.cif"), _asset("biology:b", "b.cif")],
    }
    receipt = {
        "schema_version": qa_qc_round2.MULTI_ASSET_RECEIPT_SCHEMA_VERSION,
        "stager_capability": qa_qc_round2.MULTI_ASSET_STAGER_CAPABILITY,
        "status": "supported",
        "declared_asset_count": 2,
        "flattened_asset_count": 1,
        "host_staged_asset_count": 1,
        "container_staged_asset_count": 1,
        "host_primary_present": True,
        "container_primary_present": True,
        "host_paths_exist": True,
        "staged_files_exist": True,
    }

    findings = qa_qc_round2.audit_task_materialization(
        task,
        {},
        tmp_path,
        qa_qc_round2.MaskGeometryCache(),
        materialization_receipt=receipt,
    )

    assert [finding["code"] for finding in findings] == [
        "multi_asset_materialization_incomplete"
    ]


def test_legacy_unsupported_multi_asset_receipt_remains_blocked(
    tmp_path: Path,
) -> None:
    for name in ("a.cif", "b.cif"):
        (tmp_path / name).write_text("data_fixture\n", encoding="utf-8")
    task = {
        "task_id": "multi-legacy-receipt",
        "knowledge": {"direction_id": "bio_multi_structure_compare"},
        "asset": _asset("biology:a", "a.cif"),
        "assets": [_asset("biology:a", "a.cif"), _asset("biology:b", "b.cif")],
    }
    receipt = {
        "schema_version": "sciencevision.qa_qc.multi_asset_materialization_receipt.v0",
        "stager_capability": "primary_asset_only",
        "status": "unsupported",
        "declared_asset_count": 2,
    }

    findings = qa_qc_round2.audit_task_materialization(
        task,
        {},
        tmp_path,
        qa_qc_round2.MaskGeometryCache(),
        materialization_receipt=receipt,
    )

    assert [finding["code"] for finding in findings] == [
        "multi_asset_staging_unsupported"
    ]


def test_round2_rejects_noncanonical_baseline_matrix() -> None:
    canonical = list(qa_qc.DEFAULT_EVAL_BASELINES)

    assert qa_qc_round2._validate_canonical_baselines(canonical) == canonical
    with pytest.raises(qa_qc_round2.Round2Error, match="exact canonical"):
        qa_qc_round2._validate_canonical_baselines(canonical[:-1])
    with pytest.raises(qa_qc_round2.Round2Error, match="exact canonical"):
        qa_qc_round2._validate_canonical_baselines([*canonical, "text-only"])


def test_round2_rejects_output_overlapping_protected_input(tmp_path: Path) -> None:
    protected = tmp_path / "source"
    protected.mkdir()

    with pytest.raises(qa_qc_round2.Round2Error, match="overlaps protected"):
        qa_qc_round2._validate_output_dir(protected / "round2", [protected])
    with pytest.raises(qa_qc_round2.Round2Error, match="overlaps protected"):
        qa_qc_round2._validate_output_dir(tmp_path, [protected])


def test_build_manifest_requires_external_sha256_pin(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(qa_qc_round2, "ROOT", tmp_path)
    build_path = tmp_path / "candidate" / "scale-v2-build.json"
    build_path.parent.mkdir()
    build_path.write_text('{"schema_version":"fixture"}\n', encoding="utf-8")
    digest = hashlib.sha256(build_path.read_bytes()).hexdigest()
    lock_path = tmp_path / "docs" / "build-lock.json"
    lock_path.parent.mkdir()
    lock_path.write_text(
        json.dumps(
            {
                "schema_version": "sciencevision.qa_qc.build_manifest_lock.v1",
                "build_manifest_path": "candidate/scale-v2-build.json",
                "build_manifest_sha256": digest,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(qa_qc_round2.Round2Error, match="external.*pin"):
        qa_qc_round2._validate_build_pin(build_path, None, None)
    pin = qa_qc_round2._validate_build_pin(build_path, lock_path, None)
    assert pin["actual_sha256"] == digest
    assert pin["trust_anchor_status"] == "pending_git_commit"
    cli_pin = qa_qc_round2._validate_build_pin(build_path, None, digest)
    assert cli_pin["trust_anchor_status"] == "cli_expected_hash"
    with pytest.raises(qa_qc_round2.Round2Error, match="CLI pin"):
        qa_qc_round2._validate_build_pin(build_path, None, "0" * 64)


def test_quarantine_reasons_are_recomputed_against_current_task() -> None:
    task = {
        "task_id": "dock-1",
        "knowledge": {"direction_id": "bio_docking_plausibility"},
        "ground_truth": {"status": "needs_expert_annotation", "value": None},
    }
    decision = {
        "source": {"task_id": "dock-1"},
        "disposition": "quarantine",
        "domain": "biology",
        "family": "bio_docking_plausibility",
        "disposition_reasons": [
            "ground_truth_unverified_or_null",
            "ground_truth_qc_blocker",
        ],
        "repaired_qc_findings": [],
        "qc": {
            "grade": "needs_fix",
            "reason_codes": ["ground_truth_not_verified"],
            "leakage_summary": [],
        },
    }

    rows, summary = qa_qc_round2.audit_quarantine({"dock-1": task}, [decision])

    assert rows[0]["status"] == "current"
    assert rows[0]["obsolete_reasons"] == []
    assert summary["status_counts"] == {"current": 1}


def test_main_returns_one_for_a_persisted_blocked_gate(
    tmp_path: Path, monkeypatch
) -> None:
    report = {
        "summary": {
            "task_count": 1,
            "decisions": {"block": 1},
            "grades": {"unacceptable": 1},
        },
        "round2": {
            "materialization_finding_counts": {"target_mask_binding_missing": 1},
            "quarantine_audit": {"task_count": 0},
            "gate": {"status": "blocked"},
        },
    }
    monkeypatch.setattr(
        qa_qc_round2,
        "build_round2_report",
        lambda **kwargs: (report, [], {"status": "valid"}),
    )
    monkeypatch.setattr(qa_qc_round2, "write_round2_outputs", lambda *args: None)

    status = qa_qc_round2.main(["--out-dir", str(tmp_path / "round2")])

    assert status == 1
