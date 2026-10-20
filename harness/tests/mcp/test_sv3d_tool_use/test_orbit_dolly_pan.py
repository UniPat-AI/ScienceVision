import math

from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


def _vec_dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def test_orbit_preserves_target_and_distance(mesh_session):
    sid = mesh_session
    s0 = _state(sid)
    cam0 = s0["camera"]
    d0 = _vec_dist(cam0["position"], cam0["target"])

    REGISTRY["sv3d_camera_orbit"].handler(
        {"session_id": sid, "d_azimuth_deg": 30.0, "d_elevation_deg": 15.0}
    )
    s1 = _state(sid)
    cam1 = s1["camera"]

    assert cam1["target"] == cam0["target"]
    d1 = _vec_dist(cam1["position"], cam1["target"])
    assert math.isclose(d0, d1, rel_tol=1e-6)
    assert cam1["position"] != cam0["position"]


def test_dolly_changes_distance_multiplicatively(mesh_session):
    sid = mesh_session
    cam0 = _state(sid)["camera"]
    d0 = _vec_dist(cam0["position"], cam0["target"])

    REGISTRY["sv3d_camera_dolly"].handler({"session_id": sid, "factor": 0.5})
    cam1 = _state(sid)["camera"]
    d1 = _vec_dist(cam1["position"], cam1["target"])
    assert math.isclose(d1, d0 * 0.5, rel_tol=1e-3)
    assert cam1["target"] == cam0["target"]


def test_pan_shifts_target_and_position_together(mesh_session):
    sid = mesh_session
    cam0 = _state(sid)["camera"]
    d0 = _vec_dist(cam0["position"], cam0["target"])

    REGISTRY["sv3d_camera_pan"].handler(
        {"session_id": sid, "dx_frac": 0.2, "dy_frac": -0.1}
    )
    cam1 = _state(sid)["camera"]
    d1 = _vec_dist(cam1["position"], cam1["target"])
    assert math.isclose(d0, d1, rel_tol=1e-6)
    assert cam1["target"] != cam0["target"]


def test_dolly_factor_out_of_range_returns_error(mesh_session):
    r = REGISTRY["sv3d_camera_dolly"].handler(
        {"session_id": mesh_session, "factor": 50.0}
    )
    assert "error" in r
