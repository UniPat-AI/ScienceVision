import pytest
from sci3d_agent.modes import get_profile, MODE_NAMES

@pytest.mark.unit
def test_three_profiles_exist():
    for name in MODE_NAMES:
        profile = get_profile(name)
        assert profile.name == name
        assert profile.system_prompt
        assert profile.advertised_tools

@pytest.mark.unit
def test_mode1_has_no_viewer_tools():
    p = get_profile("mode1_no_vision")
    tool_names = [t["name"] for t in p.advertised_tools]
    assert "execute_code" in tool_names
    assert "finish" in tool_names
    assert "render_view" not in tool_names
    assert "screenshot" not in tool_names
    assert p.render_policy == "blocked"

@pytest.mark.unit
def test_mode2_has_no_viewer_tools_but_strips_kernel_pixels():
    p = get_profile("mode2_one_image")
    tool_names = [t["name"] for t in p.advertised_tools]
    assert "render_view" not in tool_names
    assert p.render_policy == "allowed_no_pixels_returned"

@pytest.mark.unit
def test_mode3_exposes_all_tools():
    p = get_profile("mode3_free_visual")
    tool_names = [t["name"] for t in p.advertised_tools]
    for required in ("execute_code", "finish", "render_view", "screenshot", "set_camera"):
        assert required in tool_names
    assert p.render_policy == "allowed"

@pytest.mark.unit
def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        get_profile("mode99")
