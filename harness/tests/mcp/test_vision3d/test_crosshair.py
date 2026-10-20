"""Tests for sv3d_set_crosshair (linked-MPR crosshair) and its mode wiring."""

from __future__ import annotations

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d._crosshair import set_crosshair_handler
from sci3d_agent.mcp.tools.vision3d._slice_math import _world_to_voxel
from sci3d_agent.mcp.tools.vision3d._store import (
    get_store,
    reset_store_for_tests,
)
from sci3d_agent.mcp.tools.vision3d.session import _open_scene_handler
from sci3d_agent.mcp.tools.vision3d.state import _reset_shape_cache
from sci3d_agent.mcp.runtime.errors import SVToolError


# --- fixtures / helpers (mirror test_state.py) ----------------------------


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_store_for_tests()
    _reset_shape_cache()
    yield
    reset_store_for_tests()
    _reset_shape_cache()


@pytest.fixture
def _registry_snapshot():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def _install_fake(name: str, fn):
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )
    return orig


def _fake_nifti_inspector(shape=(100, 100, 50)):
    return {
        "shape": list(shape),
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [
            [2.0, 0.0, 0.0, -10.0],
            [0.0, 2.0, 0.0, -20.0],
            [0.0, 0.0, 2.0, -5.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "orientation": "RAS",
        "intensity": {
            "min": -1000.0, "max": 1000.0, "mean": 0.0,
            "p1": -500.0, "p99": 500.0,
        },
    }


def _fake_nifti_mask_pair_inspector(shape=(256, 256, 100), label_ids=(1, 2)):
    labels = []
    for i, lid in enumerate(label_ids):
        labels.append(
            {
                "id": lid,
                "voxel_count": 100 + i,
                "bbox_voxel": [[10, 10, 5], [30, 30, 25]],
                "bbox_world": [[10.0, 10.0, 10.0], [30.0, 30.0, 50.0]],
                "centroid_voxel": [20.0, 20.0, 15.0],
                "centroid_world": [20.0, 20.0, 30.0],
                "best_slices": {"axial": 15, "coronal": 22, "sagittal": 18},
            }
        )
    return {
        "shape": list(shape),
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "labels": labels,
    }


def _open_nifti(_registry_snapshot, shape=(100, 100, 50)) -> str:
    _install_fake("sv_inspect_nifti", lambda args: _fake_nifti_inspector(shape))
    out = _open_scene_handler({"asset_path": "/datasets/a.nii.gz"})
    assert "error" not in out, out
    return out["session_id"]


def _open_nifti_with_masks(_registry_snapshot, shape=(256, 256, 100)) -> str:
    _install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda args: _fake_nifti_mask_pair_inspector(shape=shape),
    )
    _install_fake("sv_inspect_nifti", lambda args: _fake_nifti_inspector(shape))
    out = _open_scene_handler(
        {"asset_path": "/datasets/foo/ct.nii.gz", "mask_path": "/datasets/foo/mask.nii.gz"}
    )
    assert "error" not in out, out
    return out["session_id"]


# --- _world_to_voxel unit -------------------------------------------------


def test_world_to_voxel_inverts_affine():
    aff = (
        (2.0, 0.0, 0.0, -10.0),
        (0.0, 2.0, 0.0, -20.0),
        (0.0, 0.0, 2.0, -5.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    # world = 2*vox + offset; voxel (5, 10, 3) -> world (0, 0, 1)
    assert _world_to_voxel(aff, (0.0, 0.0, 1.0)) == (5, 10, 3)


def test_world_to_voxel_missing_affine_raises():
    with pytest.raises(SVToolError):
        _world_to_voxel(None, (0.0, 0.0, 0.0))


# --- voxel locator --------------------------------------------------------


def test_crosshair_from_voxel_aligns_three_planes(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(100, 100, 50))
    out = set_crosshair_handler({"session_id": sid, "voxel": [10, 20, 30]})
    assert "error" not in out, out
    assert out["voxel"] == [10, 20, 30]
    planes = out["planes"]
    # numpy dim mapping: sagittal=i=10, coronal=j=20, axial=k=30
    assert planes["sagittal"]["index"] == 10
    assert planes["coronal"]["index"] == 20
    assert planes["axial"]["index"] == 30
    # active plane persisted on the session (axial by default)
    s = get_store().get(sid)
    assert s.slice.axis == "axial"
    assert s.slice.index == 30


def test_crosshair_voxel_out_of_range_clamps(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(100, 100, 50))
    # axial extent is 50 -> last index 49; k=999 must clamp to 49
    out = set_crosshair_handler({"session_id": sid, "voxel": [10, 20, 999]})
    assert "error" not in out, out
    assert out["planes"]["axial"]["index"] == 49


# --- point_world locator --------------------------------------------------


def test_crosshair_from_point_world(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(100, 100, 50))
    # affine: world = 2*vox + (-10,-20,-5). world (0,0,1) -> vox (5,10,3)
    out = set_crosshair_handler(
        {"session_id": sid, "point_world": [0.0, 0.0, 1.0]}
    )
    assert "error" not in out, out
    assert out["voxel"] == [5, 10, 3]
    assert out["planes"]["axial"]["index"] == 3


# --- mask_id locator ------------------------------------------------------


def test_crosshair_from_mask_uses_best_slices(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot)
    out = set_crosshair_handler({"session_id": sid, "mask_id": "1"})
    assert "error" not in out, out
    # best_slices on the fake: axial=15, coronal=22, sagittal=18
    assert out["planes"]["axial"]["index"] == 15
    assert out["planes"]["coronal"]["index"] == 22
    assert out["planes"]["sagittal"]["index"] == 18


def test_crosshair_unknown_mask_errors(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot)
    out = set_crosshair_handler({"session_id": sid, "mask_id": "nope"})
    assert "error" in out and "unknown mask_id" in out["error"]


# --- set_mode -------------------------------------------------------------


def test_crosshair_set_mode_switches_to_mpr_axial(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = set_crosshair_handler(
        {"session_id": sid, "voxel": [1, 2, 3], "set_mode": True}
    )
    assert out["mode"] == "mpr_axial"
    assert get_store().get(sid).mode == "mpr_axial"


def test_crosshair_default_does_not_change_mode(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    before = get_store().get(sid).mode
    out = set_crosshair_handler({"session_id": sid, "voxel": [1, 2, 3]})
    assert out["mode"] is None
    assert get_store().get(sid).mode == before


# --- error paths ----------------------------------------------------------


def test_crosshair_requires_session_id():
    out = set_crosshair_handler({"voxel": [1, 2, 3]})
    assert "error" in out


def test_crosshair_unknown_session():
    out = set_crosshair_handler({"session_id": "deadbeef", "voxel": [1, 2, 3]})
    assert "error" in out and "unknown session_id" in out["error"]


def test_crosshair_no_locator_errors(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = set_crosshair_handler({"session_id": sid})
    assert "error" in out and "exactly one of" in out["error"]


def test_crosshair_multiple_locators_error(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = set_crosshair_handler(
        {"session_id": sid, "voxel": [1, 2, 3], "mask_id": "1"}
    )
    assert "error" in out and "exactly one locator" in out["error"]


def test_crosshair_rejects_mesh_session(_registry_snapshot):
    _install_fake(
        "sv_inspect_mesh",
        lambda args: {
            "format": "ply", "n_vertices": 10, "n_faces": 5,
            "bbox": [[0, 0, 0], [1, 1, 1]], "extent": [1, 1, 1],
            "has_vertex_colors": False, "has_uv": False,
            "materials": None, "axis_up_hint": "Z",
        },
    )
    out = _open_scene_handler({"asset_path": "/datasets/a.ply"})
    sid = out["session_id"]
    res = set_crosshair_handler({"session_id": sid, "voxel": [1, 2, 3]})
    assert "error" in res and "nifti session" in res["error"]
