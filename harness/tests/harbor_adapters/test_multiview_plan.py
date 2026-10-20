from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor._multiview import plan_views, mesh_orbit_azimuths  # noqa: E402
from scripts.harbor import view_protocol as VP  # noqa: E402


def test_nifti_no_mask_is_triplanar_slices():
    # Default n_views=6 -> 3 axes x {0.35, 0.65} fractions, 6 slices total.
    plan = plan_views({"asset_format": "nifti", "asset_path": "/h/ct.nii.gz"})
    assert len(plan) == 6
    assert [t for t, _ in plan] == ["sv_render_nifti_slice"] * 6
    axes = [a["axis"] for _, a in plan]
    assert axes == [
        "sagittal", "sagittal", "coronal", "coronal", "axial", "axial",
    ]
    assert [a["fraction"] for _, a in plan] == [0.35, 0.65] * 3


def test_nifti_with_mask_is_triplanar_overlay():
    # Default n_views=6 -> 3 axes x {0.35, 0.65} fractions, 6 slices total.
    plan = plan_views(
        {"asset_format": "nifti", "asset_path": "/h/ct.nii.gz", "mask_path": "/h/m.nii.gz"}
    )
    assert [t for t, _ in plan] == ["sv_render_nifti_overlay"] * 6
    assert all(a["nifti_path"] == "/h/ct.nii.gz" and a["mask_path"] == "/h/m.nii.gz"
               for _, a in plan)
    assert all(a["roi_highlight"] is True for _, a in plan)


def test_nifti_best_slices_use_index_not_fraction():
    plan = plan_views(
        {"asset_format": "nifti", "asset_path": "/h/ct.nii.gz", "mask_path": "/h/m.nii.gz"},
        best_slices={
            "sagittal": 10,
            "coronal": 20,
            "axial": 30,
            "__roi_plane_anchor_voxel__": [11.5, 21.5, 31.5],
        },
        n_views=3,
    )
    idx = {a["axis"]: a.get("index") for _, a in plan}
    assert idx == {"sagittal": 10, "coronal": 20, "axial": 30}
    assert all("fraction" not in a for _, a in plan)
    assert all(a["plane_anchor_voxel"] == [11.5, 21.5, 31.5] for _, a in plan)


@pytest.mark.parametrize("n", [1, 3, 6, 12])
def test_nifti_roi_plan_always_contains_best_slice(n):
    best = {
        "sagittal": 10,
        "coronal": 20,
        "axial": 30,
        "__shape__": [40, 50, 60],
    }
    plan = plan_views(
        {"asset_format": "nii.gz", "asset_path": "/h/ct.nii.gz", "mask_path": "/h/m.nii.gz"},
        best_slices=best,
        n_views=n,
    )
    indices_by_axis = {}
    for _, args in plan:
        indices_by_axis.setdefault(args["axis"], []).append(args["index"])
    for axis, values in indices_by_axis.items():
        assert best[axis] in values


def test_nifti_extent_six_view_plan_covers_bbox_endpoints():
    best = {
        "sagittal": 10,
        "coronal": 20,
        "axial": 30,
        "__shape__": [40, 50, 60],
        "__bbox__": [[2, 3, 4], [32, 43, 54]],
    }
    plan = plan_views(
        {
            "asset_format": "nii.gz",
            "asset_path": "/h/ct.nii.gz",
            "mask_path": "/h/m.nii.gz",
            "answer_schema": {"fields": ["extent_vox"]},
        },
        best_slices=best,
        n_views=6,
    )
    by_axis = {}
    for _, args in plan:
        by_axis.setdefault(args["axis"], []).append(args["index"])
    assert by_axis == {"sagittal": [2, 32], "coronal": [3, 43], "axial": [4, 54]}


@pytest.mark.parametrize("n", [1, 3, 6, 12])
def test_mesh_n_views_follow_the_frozen_protocol(n):
    plan = plan_views({"asset_format": "obj", "asset_path": "/h/m.obj"}, n_views=n)
    assert len(plan) == n
    assert all(t == "sv_render_mesh_thumbnail" for t, _ in plan)
    named = [a["view"] for _, a in plan if "view" in a]
    assert named == [v for k, v in VP.entries(n) if k == "named"]
    fills = [(a["azimuth"], a["elevation"]) for _, a in plan if "azimuth" in a]
    assert fills == [(az, 20.0) for az in VP.ORBIT_FILL_AZIMUTHS] if n == 12 else fills == []
    # Every mesh entry declares the frame the angles are in; the renderer
    # converts the fills for Z-up assets and leaves the named faces alone.
    assert all(a.get("angle_frame") == "viewer_y_up" for _, a in plan)


def test_mesh_orbit_azimuths_evenly_spaced():
    assert mesh_orbit_azimuths(1) == [0.0]
    assert mesh_orbit_azimuths(3) == [0.0, 120.0, 240.0]
    assert mesh_orbit_azimuths(6) == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
    assert mesh_orbit_azimuths(12) == [i * 30.0 for i in range(12)]


def test_mesh_seed_pose_no_longer_shifts_the_frozen_plan():
    plan = plan_views({"asset_format": "obj", "asset_path": "/h/m.obj"},
                      n_views=3, seed_pose={"azimuth": 45.0, "elevation": 10.0})
    assert [a.get("view") for _, a in plan] == ["iso", "front", "top"]
    assert all("azimuth" not in a for _, a in plan)


def test_unlisted_view_counts_keep_the_legacy_orbit():
    plan = plan_views({"asset_format": "obj", "asset_path": "/h/m.obj"}, n_views=9)
    assert [a["azimuth"] for _, a in plan] == mesh_orbit_azimuths(9)
    assert all(a["elevation"] == 20.0 for _, a in plan)


@pytest.mark.parametrize("n,count", [(1, 4), (3, 4), (6, 4), (12, 4)])
def test_pointcloud_n_views(n, count):
    plan = plan_views({"asset_format": "las", "asset_path": "/h/c.las"}, n_views=n)
    assert len(plan) == n
    assert all(t == "sv_render_pointcloud_thumbnail" for t, _ in plan)


def test_nifti_slice_counts():
    p1 = plan_views({"asset_format": "nifti", "asset_path": "/h/a.nii"}, n_views=1)
    assert [a["axis"] for _, a in p1] == ["axial"]
    p6 = plan_views({"asset_format": "nifti", "asset_path": "/h/a.nii"}, n_views=6)
    assert len(p6) == 6 and sorted({a["axis"] for _, a in p6}) == ["axial", "coronal", "sagittal"]
    p12 = plan_views({"asset_format": "nifti", "asset_path": "/h/a.nii"}, n_views=12)
    assert len(p12) == 12


def test_physics_glb_not_covered_v1():
    assert plan_views(
        {"asset_format": "glb", "asset_path": "/h/scene.glb", "domain": "physics"}
    ) is None


@pytest.mark.parametrize("fmt", ["mmcif", "cif", "xyz", "pdb"])
@pytest.mark.parametrize("n", [1, 3, 6, 12])
def test_molecular_formats_follow_the_frozen_protocol(fmt, n):
    # molecular assets (bio mmcif, chem cif/xyz) get a real 2D orbit rendered
    # OFFLINE from the 3D structure — the cc2d agent never sees the raw asset.
    plan = plan_views({"asset_format": fmt, "asset_path": f"/h/mol.{fmt}"}, n_views=n)
    assert len(plan) == n
    assert all(t == "sv_render_molecule_thumbnail" for t, _ in plan)
    named = [a["view"] for _, a in plan if "view" in a]
    assert named == [v for k, v in VP.entries(n) if k == "named"]
    assert all(a["angle_frame"] == "viewer_y_up" for _, a in plan)
    assert all(a["path"] == f"/h/mol.{fmt}" and a["format"] == fmt for _, a in plan)
    fills = [a["azimuth"] for _, a in plan if "azimuth" in a]
    assert fills == (list(VP.ORBIT_FILL_AZIMUTHS) if n == 12 else [])


def test_urdf_and_physics_still_not_covered_v1():
    assert plan_views({"asset_format": "urdf", "asset_path": "/h/r.urdf"}) is None
    assert plan_views({"asset_format": "dat", "asset_path": "/h/raw.dat"}) is None


@pytest.mark.parametrize("fmt", ["step", "stp"])
def test_step_formats_plan_as_mesh_named_views(fmt):
    plan = plan_views({"asset_format": fmt, "asset_path": f"/h/p.{fmt}"}, n_views=6)
    assert [t for t, _ in plan] == ["sv_render_mesh_thumbnail"] * 6
    assert [a.get("view") for _, a in plan] == ["iso", "front", "top", "right", "back", "left"]


@pytest.mark.parametrize("fmt", ["iges", "igs"])
def test_iges_plans_onto_the_mesh_branch_and_fails_at_the_stager(fmt):
    # The planner routes iges/igs to the mesh renderer; the stager's tessellation
    # hook is what rejects them (step_format_unsupported), so a raw B-rep never
    # reaches trimesh.load with a vaguer error.
    plan = plan_views({"asset_format": fmt, "asset_path": f"/h/p.{fmt}"}, n_views=1)
    assert plan is not None and plan[0][0] == "sv_render_mesh_thumbnail"


@pytest.mark.parametrize("fmt", ["nii.gz", "nii", "nifti.gz", "NIfTI"])
def test_nifti_format_variants_normalized(fmt):
    # scale-v1 medical assets carry asset_format="nii.gz"; normalize to the
    # nifti slice plan instead of falling through to the None (uncovered) path.
    plan = plan_views({"asset_format": fmt, "asset_path": "/h/ct.nii.gz"}, n_views=3)
    assert plan is not None
    assert [t for t, _ in plan] == ["sv_render_nifti_slice"] * 3


def test_missing_asset_path_returns_none():
    assert plan_views({"asset_format": "nifti", "asset_path": ""}) is None
