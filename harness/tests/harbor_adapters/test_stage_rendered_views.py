from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402


_MRI_DERIVED_ROOT = os.environ.get("SCIENCEVISION_CC2D_MRI_DERIVED_ROOT")
_MRI0000_IMAGE = (
    Path(_MRI_DERIVED_ROOT)
    / "crawl_data/medical/decathlon/extracted/Task04_Hippocampus/imagesTr/hippocampus_001.nii.gz"
    if _MRI_DERIVED_ROOT
    else None
)
_MRI0000_MASK = (
    Path(_MRI_DERIVED_ROOT)
    / "crawl_data/medical/decathlon/extracted/Task04_Hippocampus/labelsTr/hippocampus_001.nii.gz"
    if _MRI_DERIVED_ROOT
    else None
)
_CC2D_CANDIDATES = (
    Path(_MRI_DERIVED_ROOT) / "tasks" / "candidate_tasks.jsonl"
    if _MRI_DERIVED_ROOT
    else None
)


def test_lookup_render_handler_populates_registry():
    """Regression: _lookup_render_handler must import sci3d_agent.mcp.tools to
    populate REGISTRY. Importing registry alone leaves it empty, so every
    handler would resolve to None ("renderer unavailable") and every task would
    degrade to views_missing — the exact failure seen when generating real 2D
    task sets. Skipped where the mcp tools package is not importable (e.g. a
    minimal venv); everywhere it IS importable, a known F-renderer tool name
    must resolve to a callable handler."""
    pytest.importorskip("sci3d_agent.mcp.tools")
    handler = gen._lookup_render_handler("sv_render_nifti_slice")
    assert callable(handler), (
        "sv_render_nifti_slice did not resolve — REGISTRY was likely empty "
        "because tools were never imported"
    )
    # an unknown tool still resolves to None, not an error
    assert gen._lookup_render_handler("sv_nonexistent_tool_xyz") is None

def _pattern_png() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (16, 16), "black")
    for i in range(16):
        image.putpixel((i, i), (255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


_PNG_BYTES = _pattern_png()
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


def _asset(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"source-asset")
    return str(path)


def _fake_handler_factory(mode="b64"):
    def handler(args):
        if mode == "b64":
            return {
                "content_type": "image/png",
                "image_png_base64": _PNG_B64,
                **_nifti_renderer_contract(args),
                "mask_pixel_count": 100,
            }
        if mode == "error":
            return {"error": "renderer_skipped: no EGL"}
        raise AssertionError(mode)
    return handler


def _nifti_renderer_contract(args: dict | None = None) -> dict:
    """A valid world-grid NIfTI envelope for a 16px square fake PNG."""
    source_sha = hashlib.sha256(b"source-asset").hexdigest()
    affine = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    shape = [178, 178, 178]
    grid_identity = gen._sha256_json({
        "policy": "source_voxel_edge_ras_grid_v1",
        "spacing_rule": "source_affine_column_norm_by_dominant_ras_axis_v1",
        "source_sha256": source_sha,
        "source_shape": shape,
        "source_affine_4x4": affine,
        "target_shape": shape,
        "target_affine_4x4": affine,
    })
    axis_name = (args or {}).get("axis", "axial")
    axis_index = {"sagittal": 0, "coronal": 1, "axial": 2}[axis_name]
    plane_axes = [axis for axis in (0, 1, 2) if axis != axis_index]
    return {
        "renderer_version": "3",
        "display_geometry_policy": "source_voxel_edge_world_fov_square_canvas_v2",
        "display_orientation_policy": "world_grid_ras_canonical_v2",
        "display_orientation": "RAS",
        "source_sha256": source_sha,
        "source_shape": shape,
        "source_affine_4x4": affine,
        "source_world_bounds_mm": {"min": [-0.5, -0.5, -0.5], "max": [177.5, 177.5, 177.5]},
        "target_grid_policy": "source_voxel_edge_ras_grid_v1",
        "target_grid_spacing_rule": "source_affine_column_norm_by_dominant_ras_axis_v1",
        "target_grid_affine_4x4": affine,
        "target_grid_shape": shape,
        "target_grid_voxel_count": 178 * 178 * 178,
        "target_grid_spacing_mm": [1.0, 1.0, 1.0],
        "target_grid_fov_mm": [178.0, 178.0, 178.0],
        "target_grid_world_bounds_mm": {"min": [-0.5, -0.5, -0.5], "max": [177.5, 177.5, 177.5]},
        "target_grid_padding_mm": [0.0, 0.0, 0.0],
        "target_grid_padding_voxels": [0.0, 0.0, 0.0],
        "target_grid_identity": grid_identity,
        "canonical_slice_axis": axis_name,
        "canonical_slice_axis_index": axis_index,
        "display_width_target_axis_index": min(plane_axes),
        "display_height_target_axis_index": max(plane_axes),
        "display_width_voxel_spacing_mm": 1.0,
        "display_height_voxel_spacing_mm": 1.0,
        "base_resample_method": "identity",
        "base_resample_order": 1,
        "base_resample_applied": False,
        "base_resample_mode": "constant",
        "base_resample_cval": 0.0,
        "source_width": 178,
        "source_height": 178,
        "source_width_mm": 178.0,
        "source_height_mm": 178.0,
        "target_width": 178,
        "target_height": 178,
        "target_width_mm": 178.0,
        "target_height_mm": 178.0,
        "display_content_width": 16,
        "display_content_height": 16,
        "display_content_width_mm": 178.0,
        "display_content_height_mm": 178.0,
    }


def _medical_renderer_contract(args: dict | None = None) -> dict:
    return {
        **_nifti_renderer_contract(args),
        "mask_sha256": hashlib.sha256(b"source-asset").hexdigest(),
        "mask_shape": [178, 178, 178],
        "mask_affine_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "mask_world_bounds_mm": {"min": [-0.5, -0.5, -0.5], "max": [177.5, 177.5, 177.5]},
        "mask_resample_method": "identity",
        "mask_resample_order": 0,
        "mask_resample_applied": False,
        "mask_resample_mode": "constant",
        "mask_resample_cval": 0.0,
        "mask_grid_alignment": "resampled_to_base_world_target_grid",
        "mask_label_voxel_counts_source": {"1": 100},
        "mask_label_voxel_counts_target": {"1": 100},
        "mask_label_retention_fraction": {"1": 1.0},
        "mask_roi_world_bounds_mm": {"min": [0.5, 0.5, 0.5], "max": [10.5, 10.5, 10.5]},
        "mask_roi_outside_target_fov_mm": [0.0, 0.0, 0.0],
        "mask_roi_outside_target_fov_voxels": [0.0, 0.0, 0.0],
        "mask_roi_outside_target_fov_threshold_voxels": 1.0,
        "mask_selected_voxel_count_source": 100,
        "mask_selected_voxel_count_target": 100,
    }


def _medical_roi_plan(best_slices: dict | None, mode: str = "single_label"):
    return gen._MedicalRoiPlan(mode=mode, best_slices=best_slices)


def test_medical_world_grid_contract_rejects_legacy_or_wrong_mask_resample_mode():
    envelope = _medical_renderer_contract({"axis": "axial"})
    image_info = {"width": 16, "height": 16}
    assert gen._medical_display_contract(envelope, image_info)["mask_grid_alignment_pass"]

    legacy = dict(envelope)
    legacy["mask_grid_alignment"] = "verified_affine_and_shape"
    assert not gen._medical_display_contract(legacy, image_info)["mask_grid_alignment_pass"]

    wrong_order = dict(envelope)
    wrong_order["mask_resample_order"] = 1
    assert not gen._medical_display_contract(wrong_order, image_info)["mask_grid_alignment_pass"]


def test_nifti_world_grid_contract_rejects_self_consistent_non_source_grid():
    """A renderer cannot certify an arbitrary giant-voxel grid by self-report."""
    envelope = _nifti_renderer_contract({"axis": "axial"})
    target_shape = [1, 1, 1]
    target_affine = [
        [1000.0, 0.0, 0.0, 499.5],
        [0.0, 1000.0, 0.0, 499.5],
        [0.0, 0.0, 1000.0, 499.5],
        [0.0, 0.0, 0.0, 1.0],
    ]
    envelope.update({
        "target_grid_affine_4x4": target_affine,
        "target_grid_shape": target_shape,
        "target_grid_voxel_count": 1,
        "target_grid_spacing_mm": [1000.0, 1000.0, 1000.0],
        "target_grid_fov_mm": [1000.0, 1000.0, 1000.0],
        "target_grid_world_bounds_mm": {
            "min": [-0.5, -0.5, -0.5], "max": [999.5, 999.5, 999.5],
        },
        "target_grid_padding_mm": [822.0, 822.0, 822.0],
        "target_grid_padding_voxels": [0.822, 0.822, 0.822],
        "display_width_voxel_spacing_mm": 1000.0,
        "display_height_voxel_spacing_mm": 1000.0,
        "source_width": 1,
        "source_height": 1,
        "source_width_mm": 1000.0,
        "source_height_mm": 1000.0,
        "target_width": 1,
        "target_height": 1,
        "target_width_mm": 1000.0,
        "target_height_mm": 1000.0,
        "display_content_width_mm": 1000.0,
        "display_content_height_mm": 1000.0,
    })
    envelope["target_grid_identity"] = gen._sha256_json({
        "policy": "source_voxel_edge_ras_grid_v1",
        "spacing_rule": "source_affine_column_norm_by_dominant_ras_axis_v1",
        "source_sha256": envelope["source_sha256"],
        "source_shape": envelope["source_shape"],
        "source_affine_4x4": envelope["source_affine_4x4"],
        "target_shape": target_shape,
        "target_affine_4x4": target_affine,
    })

    contract = gen._nifti_display_contract(envelope, {"width": 16, "height": 16})
    assert not contract["target_grid_derivation_pass"]
    assert not contract["target_grid_pass"]
    assert not contract["physical_fov_pass"]


def test_nifti_world_grid_contract_accepts_canvas_rounding_and_rejects_distortion():
    envelope = _nifti_renderer_contract({"axis": "axial"})
    shape = [5, 6, 6]
    envelope.update({
        "source_shape": shape,
        "source_world_bounds_mm": {"min": [-0.5, -0.5, -0.5], "max": [4.5, 5.5, 5.5]},
        "target_grid_shape": shape,
        "target_grid_voxel_count": 5 * 6 * 6,
        "target_grid_fov_mm": [5.0, 6.0, 6.0],
        "target_grid_world_bounds_mm": {
            "min": [-0.5, -0.5, -0.5], "max": [4.5, 5.5, 5.5],
        },
        "source_width": 5,
        "source_height": 6,
        "source_width_mm": 5.0,
        "source_height_mm": 6.0,
        "target_width": 5,
        "target_height": 6,
        "target_width_mm": 5.0,
        "target_height_mm": 6.0,
        "display_content_width": 53,
        "display_content_height": 64,
        "display_content_width_mm": 5.0,
        "display_content_height_mm": 6.0,
    })
    envelope["target_grid_identity"] = gen._sha256_json({
        "policy": "source_voxel_edge_ras_grid_v1",
        "spacing_rule": "source_affine_column_norm_by_dominant_ras_axis_v1",
        "source_sha256": envelope["source_sha256"],
        "source_shape": shape,
        "source_affine_4x4": envelope["source_affine_4x4"],
        "target_shape": shape,
        "target_affine_4x4": envelope["target_grid_affine_4x4"],
    })

    valid = gen._nifti_display_contract(envelope, {"width": 64, "height": 64})
    assert valid["physical_aspect_pass"]
    assert valid["physical_fov_pass"]

    distorted = dict(envelope)
    distorted["display_content_width"] = 54
    assert not gen._nifti_display_contract(
        distorted, {"width": 64, "height": 64},
    )["physical_aspect_pass"]

    forged_reported_mm = dict(envelope)
    forged_reported_mm.update({
        "target_width_mm": 6.0,
        "source_width_mm": 6.0,
        "display_content_width_mm": 6.0,
        "display_content_width": 64,
    })
    assert not gen._nifti_display_contract(
        forged_reported_mm, {"width": 64, "height": 64},
    )["physical_aspect_pass"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_content_width", 16.9),
        ("target_grid_voxel_count", float("nan")),
        ("target_grid_voxel_count", float("inf")),
        ("display_content_height", True),
    ],
)
def test_nifti_world_grid_contract_rejects_noninteger_metrics(field, value):
    envelope = _nifti_renderer_contract({"axis": "axial"})
    envelope[field] = value
    assert gen._positive_int(value) is None
    assert not gen._nifti_display_contract(
        envelope, {"width": 16, "height": 16},
    )["physical_fov_pass"]


def test_nifti_stages_six_views_by_default(tmp_path, monkeypatch):
    # Default n_views is now 6 (Task 3): 3 axes x {0.35, 0.65} fractions.
    monkeypatch.setattr(gen, "_lookup_render_handler",
                        lambda name: _fake_handler_factory("b64"))
    monkeypatch.setattr(gen, "_resolve_best_slices", lambda task: None)
    env_dir = tmp_path / "environment"
    source = _asset(tmp_path, "ct.nii.gz")
    staged = gen._stage_rendered_views(
        {"asset_format": "nifti", "asset_path": source}, env_dir,
    )
    assert staged["modality"] == "nifti"
    assert staged["view_container_paths"] == [
        f"/data/view_{i:02d}.png" for i in range(6)]
    for i in range(6):
        p = env_dir / "views" / f"view_{i:02d}.png"
        assert p.exists() and p.read_bytes() == _PNG_BYTES
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    geometry = manifest["sources"][0]["nifti_geometry"]
    assert geometry["source_sha256"] == hashlib.sha256(b"source-asset").hexdigest()
    assert geometry["target_grid_shape"] == [178, 178, 178]
    assert geometry["base_resample_method"] == "identity"
    assert len(geometry["target_grid_identity"]) == 64
    # never stages the raw asset
    assert not (env_dir / "asset").exists()


def test_best_slice_resolver_accepts_scale_v1_nii_gz(monkeypatch):
    monkeypatch.setattr(
        gen,
        "_lookup_render_handler",
        lambda name: lambda args: {
            "shape": [40, 50, 60],
            "labels": [{
                "best_slices": {"sagittal": 10, "coronal": 20, "axial": 30},
                "bbox_voxel": [[1, 2, 3], [11, 22, 33]],
            }],
        },
    )
    best = gen._resolve_best_slices(
        {"asset_format": "nii.gz", "asset_path": "/ct.nii.gz", "mask_path": "/mask.nii.gz"}
    )
    assert best == {
        "sagittal": 10,
        "coronal": 20,
        "axial": 30,
        "__shape__": [40, 50, 60],
        "__bbox__": [[1, 2, 3], [11, 22, 33]],
    }


def test_explicit_union_multilabel_stages_and_records_selection(tmp_path, monkeypatch):
    source = _asset(tmp_path, "ct.nii.gz")
    mask = _asset(tmp_path, "mask.nii.gz")
    union_summary = {
        "nonzero_labels": [1, 2],
        "voxel_count": 46,
        "bbox_voxel": [[5, 6, 7], [31, 32, 33]],
        "centroid_voxel": [18.0, 19.0, 20.0],
        "best_slices": {"sagittal": 15, "coronal": 16, "axial": 17},
        "best_slice_voxel_counts": {"sagittal": 10, "coronal": 11, "axial": 12},
    }

    def lookup(name):
        if name == "sv_inspect_nifti_mask_pair":
            def inspect(args):
                assert args["foreground_mode"] == "union_nonzero"
                return {
                    "shape": [178, 178, 178],
                    "foreground_union": union_summary,
                }

            return inspect
        return lambda args: {
            "image_png_base64": _PNG_B64,
            **_medical_renderer_contract(args),
            "source_mask_pixel_count": 10,
            "display_mask_pixel_count": 10,
            "display_highlight_pixel_count": 64,
        }

    monkeypatch.setattr(gen, "_lookup_render_handler", lookup)
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {
            "task_id": "explicit-union",
            "asset_format": "nii.gz",
            "asset_path": source,
            "mask_path": mask,
            "asset_grounding": {
                "verified_entities": {
                    "foreground_rule": "union of all nonzero labels",
                },
            },
            "n_views": 3,
        },
        env_dir,
    )

    assert staged["visibility_status"] == "technical_pass"
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    selection = manifest["sources"][0]["roi_selection"]
    assert selection == {
        "mode": "union_nonzero",
        "nonzero_labels": [1, 2],
        "evidence": {
            "voxel_count": 46,
            "bbox_voxel": [[5, 6, 7], [31, 32, 33]],
            "centroid_voxel": [18.0, 19.0, 20.0],
            "best_slices": {"sagittal": 15, "coronal": 16, "axial": 17},
            "best_slice_voxel_counts": {"sagittal": 10, "coronal": 11, "axial": 12},
        },
    }
    assert str(source) not in json.dumps(selection)
    assert str(mask) not in json.dumps(selection)
    indices = {view["args"]["axis"]: view["args"]["index"] for view in manifest["views"]}
    assert indices == union_summary["best_slices"]
    assert all(
        view["args"]["plane_anchor_voxel"] == union_summary["centroid_voxel"]
        for view in manifest["views"]
    )


def test_multilabel_without_selection_policy_fails_closed(tmp_path, monkeypatch):
    def lookup(name):
        if name == "sv_inspect_nifti_mask_pair":
            return lambda args: {
                "shape": [8, 8, 8],
                "labels": [
                    {"id": 1, "best_slices": {"sagittal": 1, "coronal": 1, "axial": 1}},
                    {"id": 2, "best_slices": {"sagittal": 6, "coronal": 6, "axial": 6}},
                ],
            }
        raise AssertionError("renderer must not run for ambiguous multi-label input")

    monkeypatch.setattr(gen, "_lookup_render_handler", lookup)
    with pytest.raises(gen.RenderVisibilityError, match="medical_roi_plan_unavailable"):
        gen._stage_rendered_views(
            {
                "task_id": "ambiguous-multilabel",
                "asset_format": "nii.gz",
                "asset_path": _asset(tmp_path, "ct.nii.gz"),
                "mask_path": _asset(tmp_path, "mask.nii.gz"),
                "n_views": 3,
            },
            tmp_path / "environment",
        )


@pytest.mark.skipif(
    _MRI0000_IMAGE is None
    or _MRI0000_MASK is None
    or not _MRI0000_IMAGE.is_file()
    or not _MRI0000_MASK.is_file(),
    reason="set SCIENCEVISION_CC2D_MRI_DERIVED_ROOT to run against MRI0000",
)
def test_real_derived_mri0000_union_stages(tmp_path, monkeypatch):
    from sci3d_agent.mcp.tools.inspect import handle_inspect_nifti_mask_pair

    assert _MRI0000_IMAGE is not None
    assert _MRI0000_MASK is not None
    source_sha = hashlib.sha256(_MRI0000_IMAGE.read_bytes()).hexdigest()
    mask_sha = hashlib.sha256(_MRI0000_MASK.read_bytes()).hexdigest()

    def lookup(name):
        if name == "sv_inspect_nifti_mask_pair":
            return handle_inspect_nifti_mask_pair

        def render(args):
            envelope = {
                "image_png_base64": _PNG_B64,
                **_medical_renderer_contract(args),
                "source_mask_pixel_count": 10,
                "display_mask_pixel_count": 10,
                "display_highlight_pixel_count": 64,
            }
            envelope["source_sha256"] = source_sha
            envelope["mask_sha256"] = mask_sha
            envelope["target_grid_identity"] = gen._sha256_json({
                "policy": "source_voxel_edge_ras_grid_v1",
                "spacing_rule": "source_affine_column_norm_by_dominant_ras_axis_v1",
                "source_sha256": source_sha,
                "source_shape": envelope["source_shape"],
                "source_affine_4x4": envelope["source_affine_4x4"],
                "target_shape": envelope["target_grid_shape"],
                "target_affine_4x4": envelope["target_grid_affine_4x4"],
            })
            return envelope

        return render

    monkeypatch.setattr(gen, "_lookup_render_handler", lookup)
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {
            "task_id": "sv-medi-med_mri_anatomy-0000",
            "asset_format": "nii.gz",
            "asset_path": str(_MRI0000_IMAGE),
            "mask_path": str(_MRI0000_MASK),
            "asset_grounding": {
                "verified_entities": {
                    "foreground_rule": "union of all nonzero labels",
                },
            },
            "answer_schema": {"fields": ["extent_vox"]},
            "n_views": 3,
        },
        env_dir,
    )

    assert staged["visibility_status"] == "technical_pass"
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    selection = manifest["sources"][0]["roi_selection"]
    assert selection["mode"] == "union_nonzero"
    assert selection["nonzero_labels"] == [1, 2]
    assert selection["evidence"]["bbox_voxel"] == [[8, 8, 5], [27, 44, 29]]
    assert selection["evidence"]["best_slices"] == {
        "sagittal": 14,
        "coronal": 36,
        "axial": 13,
    }


@pytest.mark.skipif(
    _CC2D_CANDIDATES is None or not _CC2D_CANDIDATES.is_file(),
    reason="set SCIENCEVISION_CC2D_MRI_DERIVED_ROOT to the immutable candidate package",
)
def test_real_cc2d_ct_geometry_0133_and_adjacent_0132_stage_with_roi_anchor(tmp_path):
    """Protect the oblique 0133 failure and an adjacent already-visible task."""
    from scripts.harbor.qa_gen_to_tasks import qa_record_to_task

    assert _CC2D_CANDIDATES is not None
    assert _MRI_DERIVED_ROOT is not None
    records = {
        row["task_id"]: row
        for row in (
            json.loads(line)
            for line in _CC2D_CANDIDATES.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if row.get("task_id") in {
            "sv-medi-med_ct_geometry-0132",
            "sv-medi-med_ct_geometry-0133",
        }
    }
    assert set(records) == {
        "sv-medi-med_ct_geometry-0132",
        "sv-medi-med_ct_geometry-0133",
    }

    coverages = {}
    for task_id in sorted(records):
        task = qa_record_to_task(records[task_id], asset_root=Path(_MRI_DERIVED_ROOT))
        task["n_views"] = 1
        staged = gen._stage_rendered_views(task, tmp_path / task_id / "environment")
        assert staged["visibility_status"] == "technical_pass"
        manifest = json.loads(
            (tmp_path / task_id / "environment" / "render_manifest.json").read_text()
        )
        view = manifest["views"][0]
        coverages[task_id] = view["roi_coverage"]
        assert view["args"]["plane_anchor_voxel"]
        assert view["render_metadata"]["slice_plane_anchor_policy"] == (
            "roi_centroid_native_voxel_v1"
        )
        assert view["roi_coverage"]["passes"] is True
        assert view["roi_coverage"]["source_mask_pixel_count"] > 0

    assert coverages["sv-medi-med_ct_geometry-0133"]["source_mask_pixel_count"] > 0
    assert coverages["sv-medi-med_ct_geometry-0132"]["source_mask_pixel_count"] > 0


def test_small_medical_roi_uses_display_highlight_not_source_area(tmp_path, monkeypatch):
    """A real tiny source ROI can pass only through an explicit display locator."""
    source = _asset(tmp_path, "ct.nii.gz")
    mask = _asset(tmp_path, "mask.nii.gz")
    monkeypatch.setattr(
        gen,
        "_resolve_medical_roi_plan",
        lambda task: _medical_roi_plan({
            "sagittal": 10,
            "coronal": 20,
            "axial": 30,
            "__shape__": [178, 178, 117],
            "__bbox__": [[72, 96, 61], [109, 115, 66]],
        }),
    )

    def handler(args):
        return {
            "image_png_base64": _PNG_B64,
            **_medical_renderer_contract(),
            "source_mask_pixel_count": 1,
            "display_mask_pixel_count": 1,
            "display_highlight_pixel_count": 64,
            "roi_highlight_type": "mask_overlay+bbox_outline",
            "roi_highlight_outline_width_px": 2,
        }

    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: handler)
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {
            "task_id": "tiny-medical",
            "asset_format": "nii.gz",
            "asset_path": source,
            "mask_path": mask,
            "assets": [{"asset_id": "a", "asset_path": source, "asset_format": "nii.gz"}],
            "answer_schema": {"fields": ["laterality"]},
            "n_views": 1,
        },
        env_dir,
    )
    assert staged["visibility_status"] == "technical_pass"
    coverage = json.loads((env_dir / "render_manifest.json").read_text())["views"][0]["roi_coverage"]
    assert coverage["source_mask_pixel_count"] == 1
    assert coverage["display_mask_pixel_count"] == 1
    assert coverage["display_highlight_pixel_count"] == 64
    assert coverage["minimum_highlight_pixels"] == 64
    assert coverage["passes"] is True


def test_medical_roi_requires_real_source_mask(tmp_path, monkeypatch):
    source = _asset(tmp_path, "ct.nii.gz")
    mask = _asset(tmp_path, "mask.nii.gz")
    monkeypatch.setattr(
        gen,
        "_resolve_medical_roi_plan",
        lambda task: _medical_roi_plan({
            "sagittal": 10,
            "coronal": 20,
            "axial": 30,
            "__shape__": [178, 178, 117],
        }),
    )
    monkeypatch.setattr(
        gen,
        "_lookup_render_handler",
        lambda name: lambda args: {
            "image_png_base64": _PNG_B64,
            **_medical_renderer_contract(),
            "source_mask_pixel_count": 0,
            "display_mask_pixel_count": 0,
            "display_highlight_pixel_count": 64,
            "roi_highlight_type": "mask_overlay+bbox_outline",
        },
    )
    with pytest.raises(gen.RenderVisibilityError, match="medical_roi_not_visible"):
        gen._stage_rendered_views(
            {
                "task_id": "empty-medical",
                "asset_format": "nii.gz",
                "asset_path": source,
                "mask_path": mask,
                "assets": [{"asset_id": "a", "asset_path": source, "asset_format": "nii.gz"}],
                "answer_schema": {"fields": ["laterality"]},
                "n_views": 1,
            },
            tmp_path / "environment",
        )


def test_stage_respects_n_views(monkeypatch, tmp_path):
    # Fake the renderer lookup so no GL is needed: each call returns a 1x1 PNG envelope.
    monkeypatch.setattr(gen, "_lookup_render_handler",
                        lambda name: (lambda args: {"image_png_base64": _PNG_B64}))
    task = {
        "task_id": "t1",
        "asset_format": "obj",
        "asset_path": _asset(tmp_path, "m.obj"),
        "n_views": 3,
    }
    staged = gen._stage_rendered_views(task, tmp_path)
    assert len(staged["view_container_paths"]) == 3
    assert sorted(p.name for p in (tmp_path / "views").glob("*.png")) == \
        ["view_00.png", "view_01.png", "view_02.png"]


def test_renderer_error_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "_lookup_render_handler",
                        lambda name: _fake_handler_factory("error"))
    monkeypatch.setattr(gen, "_resolve_best_slices", lambda task: None)
    env_dir = tmp_path / "environment"
    with pytest.raises(gen.RenderVisibilityError, match="renderer_produced_no_png"):
        gen._stage_rendered_views(
            {"asset_format": "nifti", "asset_path": _asset(tmp_path, "ct.nii.gz")}, env_dir,
        )
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    assert manifest["visibility_status"] == "failed"
    assert manifest["failure_reason"] == "renderer_produced_no_png"
    assert not list((env_dir / "views").glob("*.png")) if (env_dir / "views").exists() else True


def test_uncovered_modality_fails_closed_without_calling_renderer(tmp_path, monkeypatch):
    def boom(name):
        raise AssertionError("renderer must not be called for uncovered modality")
    monkeypatch.setattr(gen, "_lookup_render_handler", boom)
    with pytest.raises(gen.RenderVisibilityError, match="view_plan_incomplete"):
        gen._stage_rendered_views(
            {
                "asset_format": "glb",
                "asset_path": _asset(tmp_path, "s.glb"),
                "domain": "physics",
            },
            tmp_path / "environment",
        )


def test_failed_gate_keeps_manifest_and_no_raw_asset(tmp_path, monkeypatch):
    """Uncovered modality must still create environment/views/ (empty) so that
    the Dockerfile's ``COPY views/ /data/`` succeeds at docker build time."""
    def boom(name):
        raise AssertionError("renderer must not be called for uncovered modality")
    monkeypatch.setattr(gen, "_lookup_render_handler", boom)
    env_dir = tmp_path / "environment"
    with pytest.raises(gen.RenderVisibilityError):
        gen._stage_rendered_views(
            {
                "asset_format": "glb",
                "asset_path": _asset(tmp_path, "s.glb"),
                "domain": "physics",
            },
            env_dir,
        )
    views_dir = env_dir / "views"
    assert views_dir.exists()
    assert (env_dir / "render_manifest.json").exists()
    assert not (env_dir / "asset").exists()


def test_raising_handler_fails_closed(tmp_path, monkeypatch):
    def raising_handler(args):
        raise RuntimeError("GL init failed: no EGL device found")
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: raising_handler)
    monkeypatch.setattr(gen, "_resolve_best_slices", lambda task: None)
    env_dir = tmp_path / "environment"
    with pytest.raises(gen.RenderVisibilityError, match="renderer_exception"):
        gen._stage_rendered_views(
            {"asset_format": "nifti", "asset_path": _asset(tmp_path, "ct.nii.gz")}, env_dir,
        )


def test_cache_path_branch_copies_file(tmp_path, monkeypatch):
    src = tmp_path / "cached.png"
    src.write_bytes(_PNG_BYTES)

    def handler(args):
        return {"content_type": "image/png", "cache_path": str(src)}
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: handler)
    monkeypatch.setattr(gen, "_resolve_best_slices", lambda task: None)
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {"asset_format": "las", "asset_path": _asset(tmp_path, "c.las")}, env_dir,
    )
    # default n_views is now 6 (Task 3's new default orbit length).
    assert len(staged["view_container_paths"]) == 6
    assert (env_dir / "views" / "view_00.png").read_bytes() == _PNG_BYTES


def test_manifest_records_hashes_provenance_and_gate_status(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: _fake_handler_factory("b64"))
    source = _asset(tmp_path, "mesh.obj")
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {"task_id": "t-manifest", "asset_format": "obj", "asset_path": source, "n_views": 3},
        env_dir,
    )
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    assert staged["visibility_status"] == "technical_pass"
    assert manifest["visibility_status"] == "technical_pass"
    assert manifest["answer_visibility_status"] == "pending_blind_audit"
    assert manifest["blind_audit_status"] == "required"
    assert manifest["renderer_version"] == "3"
    assert manifest["display_geometry_policy"] == "source_voxel_edge_world_fov_square_canvas_v2"
    assert manifest["display_orientation_policy"] == "world_grid_ras_canonical_v2"
    assert len(manifest["render_plan_identity"]) == 64
    assert len(manifest["render_artifact_identity"]) == 64
    assert len(manifest["sources"][0]["sha256"]) == 64
    assert len(manifest["views"]) == 3
    assert all(len(view["png_sha256"]) == 64 and view["args"] for view in manifest["views"])
    assert all(manifest["gates"].values())
    assert manifest["scheme"] == "cc2d-views/2"
    assert manifest["angle_frame"] == "viewer_y_up"
    assert manifest["views"][0]["view"] == "iso"          # obj asset, n_views=3 -> named
    assert manifest["views"][0]["angle_frame"] == "viewer_y_up"  # plan declares the frame


def test_rebuild_removes_stale_views_from_a_larger_view_count(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: _fake_handler_factory("b64"))
    source = _asset(tmp_path, "mesh.obj")
    out = tmp_path / "tasks"
    task = {
        "task_id": "rebuild",
        "asset_format": "obj",
        "asset_path": source,
        "answer_schema": {"fields": ["label"]},
        "scorer": {"primary": "exact_match"},
        "ground_truth": {"value": {"label": "x"}},
        "n_views": 6,
    }
    gen.build_one(task, out, containerized=True, agent_env="claude-code-2d")
    task["n_views"] = 3
    task_dir = gen.build_one(
        task, out, containerized=True, agent_env="claude-code-2d"
    )
    assert sorted(p.name for p in (task_dir / "environment" / "views").iterdir()) == [
        "view_00.png",
        "view_01.png",
        "view_02.png",
    ]


def test_step_assets_are_tessellated_before_render(tmp_path, monkeypatch):
    from scripts.harbor import step_mesh

    seen: dict = {}

    def fake_handler(args):
        seen.setdefault("paths", []).append(args.get("path"))
        return {"image_png_base64": _PNG_B64}

    def fake_rewrite(calls, *, scratch):
        seen["tools"] = [t for t, _ in calls]
        return step_mesh.StepPlan(
            calls=tuple((t, {**a, "path": "/scratch/tessellated_00_00.ply"})
                        for t, a in calls),
            provenance={"kernel": "OCP", "meshes": [{"face_count": 1}]},
        )

    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: fake_handler)
    monkeypatch.setattr(step_mesh, "rewrite_plan", fake_rewrite)
    src = _asset(tmp_path, "part.step")
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {"task_id": "t-step", "asset_format": "step", "asset_path": src, "n_views": 3},
        env_dir,
    )
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    assert seen["tools"] == ["sv_render_mesh_thumbnail"] * 3
    assert all(p == "/scratch/tessellated_00_00.ply" for p in seen["paths"])
    assert manifest["step_mesh"]["kernel"] == "OCP"
    assert staged["view_container_paths"]


def test_step_tessellation_failure_fails_closed_with_its_code(tmp_path, monkeypatch):
    from scripts.harbor import step_mesh

    def boom(calls, *, scratch):
        raise step_mesh.StepMeshError("ocp_unavailable", "OCP")

    def no_renderer(name):
        raise AssertionError("renderer must not be called when tessellation fails")

    monkeypatch.setattr(step_mesh, "rewrite_plan", boom)
    monkeypatch.setattr(gen, "_lookup_render_handler", no_renderer)
    env_dir = tmp_path / "environment"
    with pytest.raises(gen.RenderVisibilityError, match="ocp_unavailable"):
        gen._stage_rendered_views(
            {"task_id": "t-step-fail", "asset_format": "step",
             "asset_path": _asset(tmp_path, "p.step"), "n_views": 3},
            env_dir,
        )
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    assert manifest["failure_reason"] == "ocp_unavailable"
    assert not list((env_dir / "views").glob("*.png"))


def test_multi_asset_stages_requested_views_for_every_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: _fake_handler_factory("b64"))
    assets = [
        {"asset_id": "biology:10MN", "asset_path": _asset(tmp_path, "10MN.cif"), "asset_format": "mmcif"},
        {"asset_id": "biology:10OR", "asset_path": _asset(tmp_path, "10OR.cif"), "asset_format": "mmcif"},
    ]
    env_dir = tmp_path / "environment"
    staged = gen._stage_rendered_views(
        {"task_id": "compare", "assets": assets, "n_views": 3}, env_dir
    )
    assert len(staged["view_container_paths"]) == 6
    assert [item["asset_id"] for item in staged["asset_views"]] == [
        "biology:10MN", "biology:10OR"
    ]
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    assert manifest["staged_asset_count"] == manifest["expected_asset_count"] == 2
    assert manifest["gates"]["multi_asset_count"] is True


def test_medical_mask_pixel_gate_fails_below_threshold(tmp_path, monkeypatch):
    def lookup(name):
        if name == "sv_inspect_nifti_mask_pair":
            return lambda args: {
                "labels": [{
                    "best_slices": {"sagittal": 1, "coronal": 1, "axial": 1},
                    "bbox_voxel": [[0, 0, 0], [2, 2, 2]],
                }],
                "shape": [3, 3, 3],
            }
        return lambda args: {
            "image_png_base64": _PNG_B64,
            **_medical_renderer_contract(),
            "mask_pixel_count": 1,
        }

    monkeypatch.setattr(gen, "_lookup_render_handler", lookup)
    with pytest.raises(gen.RenderVisibilityError, match="medical_roi_not_visible"):
        gen._stage_rendered_views(
            {
                "task_id": "medical",
                "asset_path": _asset(tmp_path, "ct.nii.gz"),
                "asset_format": "nii.gz",
                "mask_path": _asset(tmp_path, "mask.nii.gz"),
                "n_views": 3,
            },
            tmp_path / "environment",
        )


def test_medical_manifest_records_slice_metadata(tmp_path, monkeypatch):
    def lookup(name):
        if name == "sv_inspect_nifti_mask_pair":
            return lambda args: {
                "labels": [{
                    "best_slices": {"sagittal": 1, "coronal": 1, "axial": 1},
                    "bbox_voxel": [[0, 0, 0], [2, 2, 2]],
                }],
                "shape": [3, 3, 3],
            }

        def render(args):
            return {
                "image_png_base64": _PNG_B64,
                **_medical_renderer_contract(),
                "mask_pixel_count": 100,
                "axis": args["axis"],
                "slice_index": args["index"],
                "voxel_spacing_mm": [1.0, 1.0, 2.0],
                "window_used": [-150, 250],
                "renderer_used": name,
            }

        return render

    monkeypatch.setattr(gen, "_lookup_render_handler", lookup)
    env_dir = tmp_path / "environment"
    gen._stage_rendered_views(
        {
            "task_id": "medical-metadata",
            "asset_path": _asset(tmp_path, "ct.nii.gz"),
            "asset_format": "nii.gz",
            "mask_path": _asset(tmp_path, "mask.nii.gz"),
            "n_views": 3,
        },
        env_dir,
    )
    manifest = json.loads((env_dir / "render_manifest.json").read_text())
    for view in manifest["views"]:
        metadata = view["render_metadata"]
        assert metadata["axis"] == view["args"]["axis"]
        assert metadata["slice_index"] == view["args"]["index"]
        assert metadata["voxel_spacing_mm"] == [1.0, 1.0, 2.0]
        assert metadata["window_used"] == [-150, 250]
        assert metadata["renderer_used"] == "sv_render_nifti_overlay"


def test_medical_gate_rejects_v1_renderer_identity(tmp_path, monkeypatch):
    contract = _medical_renderer_contract()
    contract["renderer_version"] = "1"
    monkeypatch.setattr(
        gen,
        "_resolve_medical_roi_plan",
        lambda task: _medical_roi_plan({
            "sagittal": 1,
            "coronal": 1,
            "axial": 1,
            "__shape__": [3, 3, 3],
        }),
    )
    monkeypatch.setattr(
        gen,
        "_lookup_render_handler",
        lambda name: lambda args: {
            "image_png_base64": _PNG_B64,
            **contract,
            "source_mask_pixel_count": 10,
            "display_mask_pixel_count": 10,
            "display_highlight_pixel_count": 64,
        },
    )
    with pytest.raises(gen.RenderVisibilityError, match="medical_renderer_identity_invalid"):
        gen._stage_rendered_views(
            {
                "task_id": "reject-v1",
                "asset_path": _asset(tmp_path, "ct.nii.gz"),
                "asset_format": "nii.gz",
                "mask_path": _asset(tmp_path, "mask.nii.gz"),
                "n_views": 1,
            },
            tmp_path / "environment",
        )


def test_medical_gate_rejects_nonphysical_fov(tmp_path, monkeypatch):
    contract = _medical_renderer_contract()
    contract["source_width_mm"] = 177.0
    monkeypatch.setattr(
        gen,
        "_resolve_medical_roi_plan",
        lambda task: _medical_roi_plan({
            "sagittal": 1,
            "coronal": 1,
            "axial": 1,
            "__shape__": [3, 3, 3],
        }),
    )
    monkeypatch.setattr(
        gen,
        "_lookup_render_handler",
        lambda name: lambda args: {
            "image_png_base64": _PNG_B64,
            **contract,
            "source_mask_pixel_count": 10,
            "display_mask_pixel_count": 10,
            "display_highlight_pixel_count": 64,
        },
    )
    with pytest.raises(gen.RenderVisibilityError, match="medical_physical_fov_unverified"):
        gen._stage_rendered_views(
            {
                "task_id": "reject-fov",
                "asset_path": _asset(tmp_path, "ct.nii.gz"),
                "asset_format": "nii.gz",
                "mask_path": _asset(tmp_path, "mask.nii.gz"),
                "n_views": 1,
            },
            tmp_path / "environment",
        )


def test_medical_mask_task_fails_when_roi_plan_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gen,
        "_resolve_medical_roi_plan",
        lambda task: _medical_roi_plan(None, "unavailable"),
    )
    monkeypatch.setattr(gen, "_lookup_render_handler", lambda name: _fake_handler_factory("b64"))
    with pytest.raises(gen.RenderVisibilityError, match="medical_roi_plan_unavailable"):
        gen._stage_rendered_views(
            {
                "task_id": "medical",
                "asset_path": _asset(tmp_path, "ct.nii.gz"),
                "asset_format": "nii.gz",
                "mask_path": _asset(tmp_path, "mask.nii.gz"),
                "n_views": 3,
            },
            tmp_path / "environment",
        )
