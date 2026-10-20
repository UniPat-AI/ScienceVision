"""Phase 3.2 / F-camera tests for the 5 camera tools."""

from __future__ import annotations

import math

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d._store import (
    get_store,
    replace_camera,
    reset_store_for_tests,
)
from sci3d_agent.mcp.tools.vision3d._types import CameraState
from sci3d_agent.mcp.tools.vision3d.camera import (
    _dolly_handler,
    _look_at_handler,
    _orbit_handler,
    _pan_handler,
    _set_view_handler,
)
from sci3d_agent.mcp.tools.vision3d.session import _open_scene_handler

# ---------------------------------------------------------------------------
# Fixtures / helpers (mirror test_session.py patterns)
# ---------------------------------------------------------------------------


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
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )
    return orig


def _fake_matched_record(*, url_viewer: str, asset_kind: str, mask_url=None):
    rec = {"qid": "fake-qid", "asset_kind": asset_kind, "url_viewer": url_viewer}
    if mask_url is not None:
        rec["mask"] = {"mask_url_viewer": mask_url, "mask_id": "m1"}
    return {"record": rec}


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


def _fake_nifti_mask_pair_inspector_multi():
    return {
        "shape": [100, 100, 50],
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
                "voxel_count": 500,
                "bbox_voxel": [[10, 10, 5], [30, 30, 15]],
                "bbox_world": [[10.0, 10.0, 10.0], [30.0, 30.0, 30.0]],
                "centroid_voxel": [20.0, 20.0, 10.0],
                "centroid_world": [20.0, 20.0, 20.0],
                "best_slices": {"axial": 10, "coronal": 20, "sagittal": 20},
            },
            {
                "id": 2,
                "voxel_count": 800,
                "bbox_voxel": [[50, 50, 20], [80, 80, 40]],
                "bbox_world": [[50.0, 50.0, 40.0], [80.0, 80.0, 80.0]],
                "centroid_voxel": [65.0, 65.0, 30.0],
                "centroid_world": [65.0, 65.0, 60.0],
                "best_slices": {"axial": 30, "coronal": 65, "sagittal": 65},
            },
        ],
    }


def _open_mesh(asset_path="/datasets/a.ply", axis_up_hint="Z", bbox=None):
    if bbox is None:
        bbox = ((0.0, 0.0, 0.0), (10.0, 5.0, 3.0))
    _install_fake(
        "sv_inspect_mesh",
        lambda args: _fake_mesh_inspector(bbox=bbox, axis_up_hint=axis_up_hint),
    )
    out = _open_scene_handler({"asset_path": asset_path})
    assert "error" not in out, out
    return out["session_id"]


def _open_nifti_with_masks():
    _install_fake(
        "sv_get_matched_record",
        lambda args: _fake_matched_record(
            url_viewer="/datasets/M3D-RefSeg/ct.nii.gz",
            asset_kind="nifti",
            mask_url="/datasets/M3D-RefSeg/mask.nii.gz",
        ),
    )
    _install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda args: _fake_nifti_mask_pair_inspector_multi(),
    )
    out = _open_scene_handler({"qid": "m3d-multi"})
    assert "error" not in out, out
    return out["session_id"]


def _eye_target_distance(camera_dict) -> float:
    p = camera_dict["position"]
    t = camera_dict["target"]
    return math.sqrt(sum((p[i] - t[i]) ** 2 for i in range(3)))


def _normalize(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


# ---------------------------------------------------------------------------
# 1) Orbit: preserves eye-target distance
# ---------------------------------------------------------------------------


def test_orbit_preserves_eye_target_distance(_registry_snapshot):
    sid = _open_mesh()
    pre = get_store().get(sid).camera
    pre_dist = math.sqrt(sum((pre.position[i] - pre.target[i]) ** 2 for i in range(3)))

    out = _orbit_handler(
        {"session_id": sid, "d_azimuth_deg": 45.0, "d_elevation_deg": 0.0}
    )
    assert "error" not in out, out
    post_dist = _eye_target_distance(out["camera"])
    assert post_dist == pytest.approx(pre_dist, abs=1e-9)


# ---------------------------------------------------------------------------
# 2) Orbit: preserves target
# ---------------------------------------------------------------------------


def test_orbit_preserves_target(_registry_snapshot):
    sid = _open_mesh()
    pre_tgt = list(get_store().get(sid).camera.target)
    out = _orbit_handler(
        {
            "session_id": sid,
            "d_azimuth_deg": 33.3,
            "d_elevation_deg": -12.0,
            "d_roll_deg": 7.5,
        }
    )
    assert out["camera"]["target"] == pytest.approx(pre_tgt, abs=1e-9)


# ---------------------------------------------------------------------------
# 3) Orbit: clamps elevation
# ---------------------------------------------------------------------------


def test_orbit_clamps_elevation(_registry_snapshot):
    sid = _open_mesh()
    out = _orbit_handler(
        {"session_id": sid, "d_azimuth_deg": 0.0, "d_elevation_deg": 1000.0}
    )
    cam = out["camera"]
    dir_vec = [cam["position"][i] - cam["target"][i] for i in range(3)]
    up = cam["up"]
    dn = _normalize(dir_vec)
    un = _normalize(up)
    sin_el = dn[0] * un[0] + dn[1] * un[1] + dn[2] * un[2]
    sin_el = max(-1.0, min(1.0, sin_el))
    elev_deg = math.degrees(math.asin(sin_el))
    assert -89.0 - 1e-6 <= elev_deg <= 89.0 + 1e-6


# ---------------------------------------------------------------------------
# 4) Dolly: zero / negative factor errors
# ---------------------------------------------------------------------------


def test_dolly_zero_or_negative_factor_errors(_registry_snapshot):
    sid = _open_mesh()
    out0 = _dolly_handler({"session_id": sid, "factor": 0.0})
    assert "error" in out0
    out_neg = _dolly_handler({"session_id": sid, "factor": -1.0})
    assert "error" in out_neg


# ---------------------------------------------------------------------------
# 5) Dolly: halves distance
# ---------------------------------------------------------------------------


def test_dolly_halves_distance(_registry_snapshot):
    sid = _open_mesh()
    pre = get_store().get(sid).camera
    pre_dist = math.sqrt(sum((pre.position[i] - pre.target[i]) ** 2 for i in range(3)))

    out = _dolly_handler({"session_id": sid, "factor": 0.5})
    post_dist = _eye_target_distance(out["camera"])
    assert post_dist == pytest.approx(pre_dist * 0.5, rel=1e-9)


# ---------------------------------------------------------------------------
# 6) Dolly: factor out of range
# ---------------------------------------------------------------------------


def test_dolly_out_of_range(_registry_snapshot):
    sid = _open_mesh()
    out_big = _dolly_handler({"session_id": sid, "factor": 25.0})
    assert "error" in out_big
    out_tiny = _dolly_handler({"session_id": sid, "factor": 0.01})
    assert "error" in out_tiny


# ---------------------------------------------------------------------------
# 7) Pan: preserves eye-target distance
# ---------------------------------------------------------------------------


def test_pan_preserves_eye_target_distance(_registry_snapshot):
    sid = _open_mesh()
    pre = get_store().get(sid).camera
    pre_dist = math.sqrt(sum((pre.position[i] - pre.target[i]) ** 2 for i in range(3)))
    out = _pan_handler({"session_id": sid, "dx_frac": 0.3, "dy_frac": -0.2})
    post_dist = _eye_target_distance(out["camera"])
    assert post_dist == pytest.approx(pre_dist, rel=1e-9)


# ---------------------------------------------------------------------------
# 8) Pan: moves target and eye by same delta
# ---------------------------------------------------------------------------


def test_pan_moves_target_and_eye_by_same_delta(_registry_snapshot):
    sid = _open_mesh()
    pre = get_store().get(sid).camera
    out = _pan_handler({"session_id": sid, "dx_frac": 0.25, "dy_frac": 0.1})
    post = out["camera"]
    dpos = [post["position"][i] - pre.position[i] for i in range(3)]
    dtgt = [post["target"][i] - pre.target[i] for i in range(3)]
    for i in range(3):
        assert dpos[i] == pytest.approx(dtgt[i], abs=1e-9)


# ---------------------------------------------------------------------------
# 9) set_view front, Z-up
# ---------------------------------------------------------------------------


def test_set_view_front_z_up(_registry_snapshot):
    sid = _open_mesh(axis_up_hint="Z")
    out = _set_view_handler({"session_id": sid, "view": "front"})
    cam = out["camera"]
    assert cam["up"] == [0.0, 0.0, 1.0]
    assert cam["position"][1] < cam["target"][1]


# ---------------------------------------------------------------------------
# 10) set_view top, Z-up
# ---------------------------------------------------------------------------


def test_set_view_top_z_up(_registry_snapshot):
    sid = _open_mesh(axis_up_hint="Z")
    out = _set_view_handler({"session_id": sid, "view": "top"})
    cam = out["camera"]
    assert cam["up"] == [0.0, 1.0, 0.0]
    assert cam["position"][2] > cam["target"][2]


# ---------------------------------------------------------------------------
# 11) set_view iso, Z-up
# ---------------------------------------------------------------------------


def test_set_view_iso_z_up(_registry_snapshot):
    sid = _open_mesh(axis_up_hint="Z")
    out = _set_view_handler({"session_id": sid, "view": "iso"})
    cam = out["camera"]
    offsets = [cam["position"][i] - cam["target"][i] for i in range(3)]
    assert all(abs(o) > 1e-6 for o in offsets)


# ---------------------------------------------------------------------------
# 12) set_view front, Y-up session
# ---------------------------------------------------------------------------


def test_set_view_y_up_session(_registry_snapshot):
    sid = _open_mesh(
        asset_path="/datasets/a.glb",
        axis_up_hint="Y",
        bbox=((0.0, 0.0, 0.0), (4.0, 4.0, 4.0)),
    )
    out = _set_view_handler({"session_id": sid, "view": "front"})
    cam = out["camera"]
    assert cam["up"] == [0.0, 1.0, 0.0]
    assert cam["position"][2] > cam["target"][2]


# ---------------------------------------------------------------------------
# 13) set_view reset matches initial camera
# ---------------------------------------------------------------------------


def test_set_view_reset(_registry_snapshot):
    sid = _open_mesh()
    initial = get_store().get(sid).camera

    # Drift far via orbit + dolly.
    _orbit_handler(
        {"session_id": sid, "d_azimuth_deg": 120.0, "d_elevation_deg": 30.0}
    )
    _dolly_handler({"session_id": sid, "factor": 3.0})
    drifted = get_store().get(sid).camera
    assert drifted.position != initial.position

    out = _set_view_handler({"session_id": sid, "view": "reset"})
    cam = out["camera"]
    assert cam["position"] == pytest.approx(list(initial.position), abs=1e-9)
    assert cam["target"] == pytest.approx(list(initial.target), abs=1e-9)
    assert cam["up"] == pytest.approx(list(initial.up), abs=1e-9)
    assert cam["fov_deg"] == pytest.approx(initial.fov_deg, abs=1e-9)


# ---------------------------------------------------------------------------
# 14) set_view fit_bbox=false preserves current eye-target distance
# ---------------------------------------------------------------------------


def test_set_view_fit_bbox_false_keeps_distance(_registry_snapshot):
    # NOTE: We chose to preserve the current eye->target distance when
    # fit_bbox=false, so agents that have dollied in keep their zoom level
    # while still snapping to a canonical orientation.
    sid = _open_mesh()
    _dolly_handler({"session_id": sid, "factor": 0.5})
    pre = get_store().get(sid).camera
    pre_dist = math.sqrt(sum((pre.position[i] - pre.target[i]) ** 2 for i in range(3)))

    out = _set_view_handler(
        {"session_id": sid, "view": "front", "fit_bbox": False}
    )
    post_dist = _eye_target_distance(out["camera"])
    assert post_dist == pytest.approx(pre_dist, rel=1e-9)


# ---------------------------------------------------------------------------
# 15) look_at bbox_center
# ---------------------------------------------------------------------------


def test_look_at_bbox_center(_registry_snapshot):
    sid = _open_mesh(bbox=((0.0, 0.0, 0.0), (10.0, 5.0, 3.0)))
    # Move the camera somewhere arbitrary first.
    s = get_store().get(sid)
    replace_camera(
        sid,
        CameraState(
            position=(100.0, 100.0, 100.0),
            target=(50.0, 50.0, 50.0),
            up=s.camera.up,
            fov_deg=s.camera.fov_deg,
        ),
    )

    out = _look_at_handler({"session_id": sid, "target": "bbox_center"})
    assert out["camera"]["target"] == pytest.approx([5.0, 2.5, 1.5], abs=1e-9)


# ---------------------------------------------------------------------------
# 16) look_at mask_centroid default (first mask)
# ---------------------------------------------------------------------------


def test_look_at_mask_centroid_default(_registry_snapshot):
    sid = _open_nifti_with_masks()
    out = _look_at_handler({"session_id": sid, "target": "mask_centroid"})
    assert out["camera"]["target"] == pytest.approx([20.0, 20.0, 20.0], abs=1e-9)


# ---------------------------------------------------------------------------
# 17) look_at mask_centroid by id
# ---------------------------------------------------------------------------


def test_look_at_mask_centroid_by_id(_registry_snapshot):
    sid = _open_nifti_with_masks()
    out = _look_at_handler(
        {"session_id": sid, "target": "mask_centroid", "mask_id": "2"}
    )
    assert out["camera"]["target"] == pytest.approx([65.0, 65.0, 60.0], abs=1e-9)


# ---------------------------------------------------------------------------
# 18) look_at mask_centroid on mesh session errors
# ---------------------------------------------------------------------------


def test_look_at_mask_centroid_no_masks_errors(_registry_snapshot):
    sid = _open_mesh()
    out = _look_at_handler({"session_id": sid, "target": "mask_centroid"})
    assert "error" in out
    assert "no masks" in out["error"].lower()


# ---------------------------------------------------------------------------
# 19) look_at explicit point + distance
# ---------------------------------------------------------------------------


def test_look_at_explicit_point_and_distance(_registry_snapshot):
    sid = _open_mesh()
    out = _look_at_handler(
        {"session_id": sid, "target": [1.0, 2.0, 3.0], "distance": 10.0}
    )
    cam = out["camera"]
    assert cam["target"] == pytest.approx([1.0, 2.0, 3.0], abs=1e-9)
    dist = _eye_target_distance(cam)
    assert dist == pytest.approx(10.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 20) Every tool errors on unknown session id (parametrize)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,args",
    [
        (_orbit_handler, {"d_azimuth_deg": 0.0, "d_elevation_deg": 0.0}),
        (_dolly_handler, {"factor": 1.0}),
        (_pan_handler, {"dx_frac": 0.1, "dy_frac": 0.1}),
        (_set_view_handler, {"view": "front"}),
        (_look_at_handler, {"target": "bbox_center"}),
    ],
)
def test_unknown_session_id_errors(handler, args):
    payload = {"session_id": "nonexistent-sid", **args}
    out = handler(payload)
    assert "error" in out
    assert "unknown session_id" in out["error"]


# ---------------------------------------------------------------------------
# 21) Camera echo envelope shape — every tool returns exactly the same keys
# ---------------------------------------------------------------------------


_EXPECTED_CAMERA_KEYS = {"position", "target", "up", "fov_deg"}


def _assert_camera_echo(out):
    assert set(out.keys()) == {"camera"}, out
    cam = out["camera"]
    assert set(cam.keys()) == _EXPECTED_CAMERA_KEYS, cam
    assert isinstance(cam["position"], list) and len(cam["position"]) == 3
    assert isinstance(cam["target"], list) and len(cam["target"]) == 3
    assert isinstance(cam["up"], list) and len(cam["up"]) == 3
    assert isinstance(cam["fov_deg"], (int, float))


def test_camera_echo_world_coord_format(_registry_snapshot):
    sid = _open_mesh()
    _assert_camera_echo(
        _orbit_handler(
            {"session_id": sid, "d_azimuth_deg": 10.0, "d_elevation_deg": 5.0}
        )
    )
    _assert_camera_echo(_dolly_handler({"session_id": sid, "factor": 0.9}))
    _assert_camera_echo(
        _pan_handler({"session_id": sid, "dx_frac": 0.1, "dy_frac": 0.05})
    )
    _assert_camera_echo(_set_view_handler({"session_id": sid, "view": "iso"}))
    _assert_camera_echo(
        _look_at_handler({"session_id": sid, "target": "bbox_center"})
    )


# ---------------------------------------------------------------------------
# Bonus: input validation envelopes (helps coverage on error paths)
# ---------------------------------------------------------------------------


def test_orbit_missing_session_id():
    out = _orbit_handler({"d_azimuth_deg": 0.0, "d_elevation_deg": 0.0})
    assert "error" in out


def test_dolly_missing_factor(_registry_snapshot):
    sid = _open_mesh()
    out = _dolly_handler({"session_id": sid})
    assert "error" in out


def test_pan_excessive_fraction(_registry_snapshot):
    sid = _open_mesh()
    out = _pan_handler({"session_id": sid, "dx_frac": 3.0, "dy_frac": 0.0})
    assert "error" in out


def test_set_view_invalid_view(_registry_snapshot):
    sid = _open_mesh()
    out = _set_view_handler({"session_id": sid, "view": "diagonal"})
    assert "error" in out


def test_look_at_invalid_target_list(_registry_snapshot):
    sid = _open_mesh()
    out = _look_at_handler({"session_id": sid, "target": [1.0, 2.0]})
    assert "error" in out


def test_look_at_invalid_distance(_registry_snapshot):
    sid = _open_mesh()
    out = _look_at_handler(
        {"session_id": sid, "target": "bbox_center", "distance": -5.0}
    )
    assert "error" in out


def test_look_at_unknown_mask_id(_registry_snapshot):
    sid = _open_nifti_with_masks()
    out = _look_at_handler(
        {"session_id": sid, "target": "mask_centroid", "mask_id": "999"}
    )
    assert "error" in out
    assert "not found" in out["error"]


def test_set_view_y_up_top_orientation(_registry_snapshot):
    sid = _open_mesh(
        asset_path="/datasets/a.glb",
        axis_up_hint="Y",
        bbox=((0.0, 0.0, 0.0), (4.0, 4.0, 4.0)),
    )
    out = _set_view_handler({"session_id": sid, "view": "top"})
    cam = out["camera"]
    # Y-up "top" looks down -Y from above; eye Y > target Y.
    assert cam["position"][1] > cam["target"][1]


@pytest.mark.parametrize(
    "view", ["front", "back", "left", "right", "top", "bottom", "iso"]
)
def test_set_view_all_canonical_views_z_up(_registry_snapshot, view):
    sid = _open_mesh(axis_up_hint="Z")
    out = _set_view_handler({"session_id": sid, "view": view})
    assert "error" not in out, out
    _assert_camera_echo(out)


@pytest.mark.parametrize(
    "view", ["front", "back", "left", "right", "top", "bottom", "iso"]
)
def test_set_view_all_canonical_views_y_up(_registry_snapshot, view):
    sid = _open_mesh(
        asset_path="/datasets/a.glb",
        axis_up_hint="Y",
        bbox=((0.0, 0.0, 0.0), (4.0, 4.0, 4.0)),
    )
    out = _set_view_handler({"session_id": sid, "view": view})
    assert "error" not in out, out
    _assert_camera_echo(out)


def test_orbit_invalid_numeric_input(_registry_snapshot):
    sid = _open_mesh()
    out = _orbit_handler(
        {"session_id": sid, "d_azimuth_deg": "not-a-number", "d_elevation_deg": 0.0}
    )
    assert "error" in out


def test_pan_non_finite_input(_registry_snapshot):
    sid = _open_mesh()
    out = _pan_handler({"session_id": sid, "dx_frac": float("nan"), "dy_frac": 0.0})
    assert "error" in out
