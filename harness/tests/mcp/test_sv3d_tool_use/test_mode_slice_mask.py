import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


def test_mesh_mode_surface_to_points(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "points", "point_size": 2.0}
    )
    assert _state(sid)["mode"] == "points"


def test_mesh_rejects_volume_mode(mesh_session):
    r = REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": mesh_session, "mode": "volume"}
    )
    assert "error" in r


def test_nifti_mpr_axial_then_sagittal(nifti_session):
    sid = nifti_session
    REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "mpr_axial"}
    )
    assert _state(sid)["mode"] == "mpr_axial"
    REGISTRY["sv3d_set_slice"].handler(
        {"session_id": sid, "axis": "axial", "fraction": 0.5}
    )
    sl = _state(sid)["slice"]
    assert sl["axis"] == "axial"
    assert sl["fraction"] == pytest.approx(0.5)


def test_nifti_slice_window_level(nifti_session):
    sid = nifti_session
    REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "mpr_sagittal"}
    )
    REGISTRY["sv3d_set_slice"].handler(
        {
            "session_id": sid,
            "axis": "sagittal",
            "fraction": 0.3,
            "window": 400,
            "level": 40,
        }
    )
    sl = _state(sid)["slice"]
    # Window/level encoding may differ across implementations. Verify
    # the level (or the window tuple's center) reflects what we set.
    if sl.get("level") is not None:
        assert sl["level"] == pytest.approx(40)
    else:
        low, high = sl["window"]
        assert (low + high) / 2 == pytest.approx(40)


def test_toggle_mask_partial_update(nifti_session):
    sid = nifti_session
    REGISTRY["sv3d_toggle_mask"].handler(
        {
            "session_id": sid,
            "visible": True,
            "mask_id": "1",
            "colormap": "jet",
            "alpha": 0.6,
        }
    )
    m = _state(sid)["mask_state"]
    assert m["visible"] is True
    assert m["mask_id"] == "1"
    assert m["colormap"] == "jet"
    assert m["alpha"] == pytest.approx(0.6)

    # Partial update: only flip visibility, others preserved.
    REGISTRY["sv3d_toggle_mask"].handler({"session_id": sid, "visible": False})
    m2 = _state(sid)["mask_state"]
    assert m2["visible"] is False
    assert m2["mask_id"] == "1"
    assert m2["colormap"] == "jet"
