"""Phase 3.1 / F-core tests for the session store + 3 tools."""

from __future__ import annotations

import math

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d import session as session_mod
from sci3d_agent.mcp.tools.vision3d._store import (
    CapacityError,
    gpu_slot,
    get_store,
    monotonic_now,
    new_session_id,
    replace_camera,
    replace_clip_plane,
    replace_mask_state,
    replace_mode,
    replace_opacity,
    replace_slice,
    reset_store_for_tests,
)
from sci3d_agent.mcp.tools.vision3d._types import (
    CameraState,
    ClipPlaneState,
    MaskInfo,
    MaskState,
    Opacity,
    Session,
    SliceState,
)
from sci3d_agent.mcp.tools.vision3d.session import (
    _close_scene_handler,
    _get_state_handler,
    _open_scene_handler,
)


# --- fixtures / helpers ---------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture
def _registry_snapshot():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def _install_fake(name: str, fn):
    """Replace REGISTRY[name].handler with fn for the duration of a test."""
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )
    return orig


def _fake_matched_record(
    *,
    url_viewer: str,
    asset_kind: str,
    mask_url: str | None = None,
):
    rec = {
        "qid": "fake-qid",
        "asset_kind": asset_kind,
        "url_viewer": url_viewer,
    }
    out = {"record": rec}
    if mask_url is not None:
        rec["mask"] = {
            "mask_url_viewer": mask_url,
            "mask_id": "m1",
        }
    return out


def _fake_mesh_inspector(bbox=((0.0, 0.0, 0.0), (10.0, 5.0, 3.0)), axis_up_hint="Z"):
    return {
        "format": "ply",
        "n_vertices": 100,
        "n_faces": 50,
        "bbox": [list(bbox[0]), list(bbox[1])],
        "extent": [bbox[1][i] - bbox[0][i] for i in range(3)],
        "has_vertex_colors": False,
        "has_uv": False,
        "materials": None,
        "axis_up_hint": axis_up_hint,
    }


def _fake_nifti_mask_pair_inspector():
    return {
        "shape": [200, 200, 100],
        "spacing": [1.0, 1.0, 2.0],
        "affine_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 2.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "labels": [
            {
                "id": 1,
                "voxel_count": 1234,
                "bbox_voxel": [[10, 10, 5], [30, 30, 25]],
                "bbox_world": [[10.0, 10.0, 10.0], [30.0, 30.0, 50.0]],
                "centroid_voxel": [20.0, 20.0, 15.0],
                "centroid_world": [20.0, 20.0, 30.0],
                "best_slices": {"axial": 15, "coronal": 20, "sagittal": 20},
            }
        ],
    }


def _fake_nifti_inspector():
    return {
        "shape": [100, 100, 50],
        "spacing": [1.0, 1.0, 2.0],
        "affine_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 2.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "orientation": "RAS",
        "intensity": {
            "min": -1000.0,
            "max": 1000.0,
            "mean": 0.0,
            "p1": -500.0,
            "p99": 500.0,
        },
    }


# --- 1) open by qid (mesh) -----------------------------------------------


def test_open_scene_by_qid_mesh(_registry_snapshot):
    _install_fake(
        "sv_get_matched_record",
        lambda args: _fake_matched_record(
            url_viewer="/datasets/_shared_3d/scannet/scans/s1/s1.ply",
            asset_kind="mesh",
        ),
    )
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())

    out = _open_scene_handler({"qid": "scanqa-001"})
    assert "error" not in out, out
    assert out["kind"] == "mesh"
    assert out["axis_up"] == "Z"
    assert out["default_mode"] == "surface"
    assert out["bbox"] == [[0.0, 0.0, 0.0], [10.0, 5.0, 3.0]]
    assert out["masks"] == []
    assert isinstance(out["session_id"], str) and len(out["session_id"]) == 16


# --- 2) open by qid (nifti + mask) ---------------------------------------


def test_open_scene_by_qid_nifti_with_mask(_registry_snapshot):
    _install_fake(
        "sv_get_matched_record",
        lambda args: _fake_matched_record(
            url_viewer="/datasets/M3D-RefSeg/M3D_RefSeg/s0212/ct.nii.gz",
            asset_kind="nifti",
            mask_url="/datasets/M3D-RefSeg/M3D_RefSeg/s0212/mask.nii.gz",
        ),
    )
    _install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda args: _fake_nifti_mask_pair_inspector(),
    )

    out = _open_scene_handler({"qid": "m3d-001"})
    assert "error" not in out, out
    assert out["kind"] == "nifti"
    assert out["axis_up"] == "RAS"
    assert out["default_mode"] == "mpr_axial"
    assert out["bbox"] == [[0.0, 0.0, 0.0], [200.0, 200.0, 200.0]]
    assert out["masks"] == [{"mask_id": "1", "voxel_count": 1234}]

    state = _get_state_handler({"session_id": out["session_id"]})
    assert state["mask_state"]["visible"] is True
    assert state["masks_info"][0]["mask_id"] == "1"
    assert state["masks_info"][0]["best_slices"]["axial"] == 15


# --- 3) open by asset_path (mesh, no qid) --------------------------------


def test_open_scene_by_asset_path_mesh(_registry_snapshot):
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())

    out = _open_scene_handler(
        {"asset_path": "/datasets/foo/bar.ply", "ttl_seconds": 120}
    )
    assert "error" not in out, out
    assert out["kind"] == "mesh"
    assert out["axis_up"] == "Z"


# --- 4) unsupported suffix -----------------------------------------------


def test_open_scene_unsupported_suffix():
    out = _open_scene_handler({"asset_path": "/datasets/foo/bar.xyz"})
    assert "error" in out
    assert "unsupported asset suffix" in out["error"]


# --- 5) close idempotent --------------------------------------------------


def test_close_scene_idempotent(_registry_snapshot):
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())
    opened = _open_scene_handler({"asset_path": "/datasets/foo/bar.ply"})
    sid = opened["session_id"]

    first = _close_scene_handler({"session_id": sid})
    assert first == {"closed": True}

    second = _close_scene_handler({"session_id": sid})
    assert second == {"closed": False, "reason": "not found"}


# --- 6) get_state unknown id ---------------------------------------------


def test_get_state_unknown_id():
    out = _get_state_handler({"session_id": "nonexistent"})
    assert "error" in out
    assert "unknown session_id" in out["error"]


# --- 7) capacity limit ----------------------------------------------------


def test_capacity_limit(monkeypatch, _registry_snapshot):
    monkeypatch.setenv("SV_MAX_SESSIONS", "2")
    reset_store_for_tests()
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())

    r1 = _open_scene_handler({"asset_path": "/datasets/a.ply"})
    r2 = _open_scene_handler({"asset_path": "/datasets/b.ply"})
    r3 = _open_scene_handler({"asset_path": "/datasets/c.ply"})

    assert "session_id" in r1
    assert "session_id" in r2
    assert "error" in r3
    assert "capacity reached" in r3["error"]


# --- 8) initial camera geometry (Z-up) -----------------------------------


def test_initial_camera_z_up_geometry(_registry_snapshot):
    _install_fake(
        "sv_inspect_mesh",
        lambda args: _fake_mesh_inspector(
            bbox=((0.0, 0.0, 0.0), (10.0, 5.0, 3.0)), axis_up_hint="Z"
        ),
    )
    out = _open_scene_handler({"asset_path": "/datasets/a.ply"})
    state = _get_state_handler({"session_id": out["session_id"]})
    pos = state["camera"]["position"]
    tgt = state["camera"]["target"]
    up = state["camera"]["up"]

    # bbox [[0,0,0],[10,5,3]] -> center (5, 2.5, 1.5), diag ~ 11.576
    diag = math.sqrt(100 + 25 + 9)
    assert tgt == [5.0, 2.5, 1.5]
    assert up == [0.0, 0.0, 1.0]
    assert pos[0] == pytest.approx(5.0 + diag * 1.0)
    assert pos[1] == pytest.approx(2.5 - diag * 1.0)
    assert pos[2] == pytest.approx(1.5 + diag * 0.4)


# --- 9) initial camera (Y-up for GLB) ------------------------------------


def test_initial_camera_y_up_for_glb(_registry_snapshot):
    _install_fake(
        "sv_inspect_mesh",
        lambda args: _fake_mesh_inspector(
            bbox=((0.0, 0.0, 0.0), (4.0, 4.0, 4.0)), axis_up_hint="Y"
        ),
    )
    out = _open_scene_handler({"asset_path": "/datasets/a.glb"})
    state = _get_state_handler({"session_id": out["session_id"]})
    assert state["axis_up"] == "Y"
    assert state["camera"]["up"] == [0.0, 1.0, 0.0]


# --- 10) default mode per kind -------------------------------------------


def test_default_mode_per_kind(_registry_snapshot):
    # mesh -> surface
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())
    mesh_out = _open_scene_handler({"asset_path": "/datasets/a.ply"})
    assert mesh_out["default_mode"] == "surface"

    # bare nifti (no mask) -> mpr_axial
    _install_fake("sv_inspect_nifti", lambda args: _fake_nifti_inspector())
    nifti_out = _open_scene_handler({"asset_path": "/datasets/a.nii.gz"})
    assert nifti_out["default_mode"] == "mpr_axial"


# --- 11) replace_* helpers atomic ----------------------------------------


def test_replace_helpers_atomic(_registry_snapshot):
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())
    out = _open_scene_handler({"asset_path": "/datasets/a.ply"})
    sid = out["session_id"]

    new_sess = replace_mode(sid, "points")
    assert new_sess.mode == "points"

    state = _get_state_handler({"session_id": sid})
    assert state["mode"] == "points"

    # And ensure other replace_* round-trip too
    cam = CameraState(
        position=(1.0, 2.0, 3.0), target=(0.0, 0.0, 0.0), up=(0.0, 0.0, 1.0), fov_deg=42.0
    )
    replace_camera(sid, cam)
    assert _get_state_handler({"session_id": sid})["camera"]["fov_deg"] == 42.0

    sl = SliceState(
        axis="coronal", index=10, fraction=None, window=(0.0, 100.0), level=50.0
    )
    replace_slice(sid, sl)
    assert _get_state_handler({"session_id": sid})["slice"]["axis"] == "coronal"

    ms = MaskState(visible=True, mask_id="m1", colormap="viridis", alpha=0.3)
    replace_mask_state(sid, ms)
    assert _get_state_handler({"session_id": sid})["mask_state"]["mask_id"] == "m1"

    cp = ClipPlaneState(
        enabled=True, origin=(0.0, 0.0, 1.0), normal=(0.0, 0.0, 1.0), cap=False
    )
    replace_clip_plane(sid, cp)
    assert _get_state_handler({"session_id": sid})["clip_plane"]["enabled"] is True

    op = Opacity(base=0.8, mask=0.6, clip_cap=0.9)
    replace_opacity(sid, op)
    assert _get_state_handler({"session_id": sid})["opacity"]["base"] == 0.8


# --- 12) Session dataclass is frozen --------------------------------------


def test_session_dataclass_is_frozen():
    s = Session(
        session_id="abc",
        kind="mesh",
        asset_path="/tmp/x.ply",
        mask_path=None,
        bbox=((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
        axis_up="Z",
        affine_4x4=None,
        masks_info=(),
        camera=CameraState(
            position=(1.0, 1.0, 1.0),
            target=(0.0, 0.0, 0.0),
            up=(0.0, 0.0, 1.0),
            fov_deg=50.0,
        ),
        mode="surface",
        slice=SliceState(
            axis="axial", index=None, fraction=0.5, window=(0.0, 1.0), level=0.5
        ),
        mask_state=MaskState(visible=False, mask_id=None, colormap="red", alpha=0.5),
        clip_plane=ClipPlaneState(
            enabled=False, origin=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0), cap=True
        ),
        opacity=Opacity(),
        created_at=0.0,
        ttl_seconds=600,
    )
    with pytest.raises(Exception):
        s.mode = "points"  # type: ignore[misc]


# --- 13) public API contract for Phase 3.2 -------------------------------


def test_phase32_import_contract():
    """Phase 3.2 will rely on these exact names; smoke-import them all."""
    # dataclasses from _types
    from sci3d_agent.mcp.tools.vision3d._types import (
        CameraState as C,
        ClipPlaneState as CP,
        MaskInfo as MI,
        MaskState as MS,
        Opacity as O,
        Session as S,
        SliceState as SL,
    )

    for cls in (C, CP, MI, MS, O, S, SL):
        assert isinstance(cls, type)

    # store helpers (8: get_store, gpu_slot, new_session_id, monotonic_now,
    # and 6 replace_*)
    from sci3d_agent.mcp.tools.vision3d._store import (
        get_store as gs,
        gpu_slot as gp,
        monotonic_now as mn,
        new_session_id as nsi,
        replace_camera as rc,
        replace_clip_plane as rcp,
        replace_mask_state as rms,
        replace_mode as rmd,
        replace_opacity as rop,
        replace_slice as rsl,
    )

    for fn in (gs, gp, mn, nsi, rc, rcp, rms, rmd, rop, rsl):
        assert callable(fn)


# --- bonus: gpu_slot context manager smoke -------------------------------


def test_gpu_slot_acquire_release(monkeypatch):
    monkeypatch.setenv("SV_GPU_CONCURRENCY", "2")
    reset_store_for_tests()
    with gpu_slot():
        with gpu_slot():
            pass  # both slots in use; exiting should release cleanly
    # If the semaphore counted correctly, acquiring again must not block.
    with gpu_slot():
        pass


# --- bonus: missing args envelopes ---------------------------------------


def test_open_scene_missing_args():
    out = _open_scene_handler({})
    assert "error" in out


def test_close_missing_session_id():
    out = _close_scene_handler({})
    assert "error" in out


def test_get_state_missing_session_id():
    out = _get_state_handler({})
    assert "error" in out
