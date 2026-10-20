import pytest
from sci3d_agent.dispatch import dispatch
from sci3d_agent.modes import get_profile
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge
from sci3d_agent.llm_client import ToolCall

@pytest.mark.unit
def test_execute_code_in_mode1_returns_result():
    profile = get_profile("mode1_no_vision")
    kernel = FakeKernel()
    bridge = StubViewerBridge()
    call = ToolCall(name="execute_code", args={"code": "print(2+2)"})
    result, violations = dispatch(call, profile, kernel, bridge)
    assert "4" in result["stdout"]
    assert violations == []

@pytest.mark.unit
def test_execute_code_in_mode1_strips_images():
    profile = get_profile("mode1_no_vision")
    kernel = FakeKernel(emit_image_on=("plt.savefig",))
    bridge = StubViewerBridge()
    call = ToolCall(name="execute_code", args={"code": "plt.savefig('a.png')"})
    result, violations = dispatch(call, profile, kernel, bridge)
    assert result["images"] == []
    assert any("plt.savefig" in v for v in violations)

@pytest.mark.unit
def test_execute_code_in_mode3_passes_images_through():
    profile = get_profile("mode3_free_visual")
    kernel = FakeKernel(emit_image_on=("plt.savefig",))
    bridge = StubViewerBridge()
    call = ToolCall(name="execute_code", args={"code": "plt.savefig('a.png')"})
    result, violations = dispatch(call, profile, kernel, bridge)
    assert len(result["images"]) == 1
    assert violations == []

@pytest.mark.unit
def test_render_view_in_mode3_calls_bridge():
    profile = get_profile("mode3_free_visual")
    kernel = FakeKernel()
    bridge = StubViewerBridge()
    call = ToolCall(name="render_view", args={"azimuth": 30, "elevation": 20})
    result, violations = dispatch(call, profile, kernel, bridge, asset_path="x.obj")
    assert result["error"] == "viewer_bridge_unavailable"

@pytest.mark.unit
def test_render_view_in_mode1_is_unknown_tool():
    profile = get_profile("mode1_no_vision")
    kernel = FakeKernel()
    bridge = StubViewerBridge()
    call = ToolCall(name="render_view", args={})
    with pytest.raises(ValueError, match="not advertised"):
        dispatch(call, profile, kernel, bridge)
