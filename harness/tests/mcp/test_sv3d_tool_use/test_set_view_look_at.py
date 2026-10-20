import math

import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


@pytest.mark.parametrize(
    "view", ["front", "back", "left", "right", "top", "bottom", "iso"]
)
def test_set_view_canonical(mesh_session, view):
    sid = mesh_session
    r = REGISTRY["sv3d_camera_set_view"].handler(
        {"session_id": sid, "view": view, "fit_bbox": True}
    )
    assert "error" not in r
    cam = _state(sid)["camera"]
    assert all(math.isfinite(x) for x in cam["position"])
    assert all(math.isfinite(x) for x in cam["target"])


def test_set_view_reset_returns_to_default(mesh_session):
    sid = mesh_session
    cam0 = _state(sid)["camera"]
    REGISTRY["sv3d_camera_orbit"].handler(
        {"session_id": sid, "d_azimuth_deg": 90.0, "d_elevation_deg": 30.0}
    )
    REGISTRY["sv3d_camera_set_view"].handler({"session_id": sid, "view": "reset"})
    cam_reset = _state(sid)["camera"]
    assert cam_reset["position"] == cam0["position"]
    assert cam_reset["target"] == cam0["target"]


def test_look_at_explicit_point(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_camera_look_at"].handler(
        {"session_id": sid, "target": [1.0, 2.0, 3.0]}
    )
    cam = _state(sid)["camera"]
    assert cam["target"] == [1.0, 2.0, 3.0]


def test_look_at_bbox_center(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_camera_look_at"].handler(
        {"session_id": sid, "target": "bbox_center"}
    )
    cam = _state(sid)["camera"]
    assert cam["target"] == [0.0, 0.0, 1.5]
