# tests/mcp/test_modes_segment.py
"""Tests for the three new sci3d_agent modes wired to segment tools."""
from __future__ import annotations

from sci3d_agent.modes import MODE_NAMES, ModeProfile, get_profile


def _tool_names(profile: ModeProfile) -> set[str]:
    return {t["name"] for t in profile.advertised_tools}


def test_mode_names_include_three_new():
    expected_new = {"mode1_seg3d_volume", "mode1_seg3d_mesh", "mode3_seg2d"}
    assert expected_new.issubset(set(MODE_NAMES))


def test_mode1_seg3d_volume_advertises_only_volume_seg():
    p = get_profile("mode1_seg3d_volume")
    names = _tool_names(p)
    assert names == {"execute_code", "finish", "sv3d_segment_3d_volume"}
    assert p.render_policy == "blocked"


def test_mode1_seg3d_mesh_advertises_only_mesh_seg():
    p = get_profile("mode1_seg3d_mesh")
    names = _tool_names(p)
    assert names == {"execute_code", "finish", "sv3d_segment_3d_mesh"}
    assert p.render_policy == "blocked"


def test_mode3_seg2d_advertises_render_plus_seg2d():
    p = get_profile("mode3_seg2d")
    names = _tool_names(p)
    expected = {"execute_code", "finish", "render_view", "screenshot", "set_camera", "sv3d_segment_2d"}
    assert names == expected
    assert p.render_policy == "allowed"


def test_unchanged_modes_still_advertise_no_segment_tools():
    """Regression: M3-zero == mode1_no_vision must remain pixel-free and seg-free."""
    p = get_profile("mode1_no_vision")
    names = _tool_names(p)
    assert "sv3d_segment_2d" not in names
    assert "sv3d_segment_3d_volume" not in names
    assert "sv3d_segment_3d_mesh" not in names


# --- mode4_medical_mpr ----------------------------------------------------


def test_mode4_medical_mpr_in_mode_names():
    assert "mode4_medical_mpr" in set(MODE_NAMES)


def test_mode4_medical_mpr_advertises_mpr_toolkit():
    p = get_profile("mode4_medical_mpr")
    names = _tool_names(p)
    # core navigation + linked crosshair + 3D volume seg are present
    for t in (
        "execute_code", "finish", "sv3d_open_scene", "sv3d_set_render_mode",
        "sv3d_set_slice", "sv3d_set_crosshair", "sv3d_segment_3d_volume",
        "sv3d_render", "sv3d_render_canonical_views",
    ):
        assert t in names, f"missing {t}"
    assert p.render_policy == "allowed"


def test_mode4_medical_mpr_excludes_mesh_only_tools():
    """Medical volume mode should not advertise mesh-only segmentation."""
    names = _tool_names(get_profile("mode4_medical_mpr"))
    assert "sv3d_segment_3d_mesh" not in names


def test_mode1_seg3d_volume_unchanged_by_medical_mpr_addition():
    """Regression: the SegVol/Dice baseline mode must keep its exact toolset."""
    names = _tool_names(get_profile("mode1_seg3d_volume"))
    assert names == {"execute_code", "finish", "sv3d_segment_3d_volume"}
