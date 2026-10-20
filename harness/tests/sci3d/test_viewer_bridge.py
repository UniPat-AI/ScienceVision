import pytest
from sci3d_agent.viewer_bridge import StubViewerBridge, ViewerBridgeUnavailable

@pytest.mark.unit
def test_stub_health_check_returns_false():
    b = StubViewerBridge()
    assert b.health_check() is False

@pytest.mark.unit
def test_stub_render_view_returns_unavailable_error():
    b = StubViewerBridge()
    out = b.render_view(asset_path="x.obj", azimuth=0, elevation=0)
    assert out["error"] == "viewer_bridge_unavailable"

@pytest.mark.unit
def test_stub_screenshot_returns_unavailable_error():
    b = StubViewerBridge()
    out = b.screenshot()
    assert out["error"] == "viewer_bridge_unavailable"

@pytest.mark.unit
def test_stub_set_camera_returns_unavailable_error():
    b = StubViewerBridge()
    out = b.set_camera(azimuth=10)
    assert out["error"] == "viewer_bridge_unavailable"
