"""Task 5: per-layer opacity + clip-plane state tests.

Covers:
- sv3d_set_opacity: per-layer alpha (base / mask / clip_cap)
- sv3d_set_opacity: schema enforcement rejects alpha > 1.0
- sv3d_set_clip_plane: enable + disable round-trip, origin/cap preserved
- sv3d_set_clip_plane: non-unit normal is automatically normalized
"""

import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


@pytest.mark.parametrize("layer", ["base", "mask", "clip_cap"])
def test_set_opacity_per_layer(mesh_session, layer):
    sid = mesh_session
    REGISTRY["sv3d_set_opacity"].handler(
        {"session_id": sid, "layer": layer, "alpha": 0.42}
    )
    op = _state(sid)["opacity"]
    assert op[layer] == pytest.approx(0.42)


def test_opacity_alpha_clamped_by_schema(mesh_session):
    r = REGISTRY["sv3d_set_opacity"].handler(
        {"session_id": mesh_session, "layer": "base", "alpha": 1.5}
    )
    assert "error" in r


def test_clip_plane_enable_disable(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_set_clip_plane"].handler(
        {
            "session_id": sid,
            "enabled": True,
            "origin": [0.0, 0.0, 1.0],
            "normal": [0.0, 0.0, 1.0],
            "cap": True,
        }
    )
    cp = _state(sid)["clip_plane"]
    assert cp["enabled"] is True
    assert cp["origin"] == [0.0, 0.0, 1.0]
    assert cp["cap"] is True

    REGISTRY["sv3d_set_clip_plane"].handler(
        {"session_id": sid, "enabled": False}
    )
    assert _state(sid)["clip_plane"]["enabled"] is False


def test_clip_plane_normal_must_be_unit(mesh_session):
    REGISTRY["sv3d_set_clip_plane"].handler(
        {
            "session_id": mesh_session,
            "enabled": True,
            "origin": [0.0, 0.0, 0.0],
            "normal": [3.0, 0.0, 0.0],
            "cap": False,
        }
    )
    n = _state(mesh_session)["clip_plane"]["normal"]
    norm = sum(x * x for x in n) ** 0.5
    assert norm == pytest.approx(1.0, rel=1e-3)
