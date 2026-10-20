import math

import numpy as np
import pytest

from sci3d_agent.mcp.tools._render.mesh import camera_for_view, handle_mesh_thumbnail


@pytest.fixture(autouse=True)
def _fresh_render_cache(tmp_path, monkeypatch):
    """Point SV_RENDER_CACHE at a per-test dir (see test_render_molecule.py)."""
    monkeypatch.setenv("SV_RENDER_CACHE", str(tmp_path / "render-cache"))

CENTER = np.zeros(3)
DIAG = 2.0


def test_azimuth0_elev20_points_from_front_ish_zup():
    eye, target, up, fov = camera_for_view("iso", CENTER, DIAG, "Z", azimuth=0.0, elevation=20.0)
    # direction = eye-center normalized; az=0 => +X-ish in the XY plane, elevated +Z
    d = (eye - CENTER); d = d / np.linalg.norm(d)
    assert d[0] > 0.9 and d[2] > 0.3 and abs(d[1]) < 1e-6
    assert np.allclose(target, CENTER)
    assert fov == 45.0


def test_azimuth_changes_eye():
    e0, *_ = camera_for_view("iso", CENTER, DIAG, "Z", azimuth=0.0, elevation=20.0)
    e90, *_ = camera_for_view("iso", CENTER, DIAG, "Z", azimuth=90.0, elevation=20.0)
    assert not np.allclose(e0, e90)


def test_named_view_unchanged_when_no_azimuth():
    eye, target, up, fov = camera_for_view("front", CENTER, DIAG, "Z")
    d = (eye - CENTER); d = d / np.linalg.norm(d)
    assert np.allclose(d, [0.0, -1.0, 0.0], atol=1e-6)  # front dir preserved


def test_non_numeric_azimuth_returns_error_envelope_not_raise():
    # The MCP dispatch path does not validate args against the JSON schema
    # before calling the handler, so a non-numeric azimuth/elevation must
    # return an {"error": ...} envelope rather than raise ValueError/TypeError
    # across the boundary. The path need not exist: the numeric guard must
    # fire before any file I/O or rendering (trimesh/pyrender) is attempted.
    result = handle_mesh_thumbnail(
        {"path": "/nonexistent/does-not-matter.glb", "azimuth": "not-a-number"}
    )
    assert "error" in result


def test_non_numeric_elevation_returns_error_envelope_not_raise():
    result = handle_mesh_thumbnail(
        {
            "path": "/nonexistent/does-not-matter.glb",
            "azimuth": 10.0,
            "elevation": "oops",
        }
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Scale-adaptive near plane (the 11 blank CAD views)
# ---------------------------------------------------------------------------

def test_viewer_frame_fill_converts_only_for_zup_assets():
    # A viewer-frame fill (azimuth measured from +Z, i.e. atan2(d.x, d.z)) is the
    # formula the Y-up branch already implements; the Z-up branch measures from
    # +X and must shift by -90 degrees, or every cc2d-12 mesh fill on a Z-up
    # asset (obj/ply/stl/off and the tessellated CAD .ply) lands ~90 degrees off.
    az, el = math.radians(30.0), math.radians(20.0)
    zup_expected = np.array([math.cos(el) * math.sin(az),
                             -math.cos(el) * math.cos(az), math.sin(el)])
    yup_expected = np.array([math.cos(el) * math.sin(az),
                             math.sin(el), math.cos(el) * math.cos(az)])

    eye, target, _, _ = camera_for_view(
        "iso", CENTER, DIAG, "Z", azimuth=30.0, elevation=20.0, viewer_frame=True)
    d = (eye - target) / np.linalg.norm(eye - target)
    assert np.allclose(d, zup_expected, atol=1e-6)

    eye, target, _, _ = camera_for_view(
        "iso", CENTER, DIAG, "Y", azimuth=30.0, elevation=20.0, viewer_frame=True)
    d = (eye - target) / np.linalg.norm(eye - target)
    assert np.allclose(d, yup_expected, atol=1e-6)

    # Legacy callers without the flag keep the native (+X-measured) frame.
    eye, target, _, _ = camera_for_view(
        "iso", CENTER, DIAG, "Z", azimuth=30.0, elevation=20.0)
    d = (eye - target) / np.linalg.norm(eye - target)
    assert not np.allclose(d, zup_expected, atol=1e-3)


def test_handler_validates_angle_frame_before_io():
    env = handle_mesh_thumbnail({"path": "/nonexistent/x.obj", "angle_frame": "mars"})
    assert "error" in env and "angle_frame" in env["error"]
    env2 = handle_mesh_thumbnail(
        {"path": "/nonexistent/x.obj", "azimuth": 30.0, "angle_frame": "viewer_y_up"})
    assert "error" in env2 and "not found" in env2["error"]  # frame accepted; IO failed


def test_declared_surface_advertises_angle_frame():
    import sci3d_agent.mcp.tools  # noqa: F401  (registers the tools)
    from sci3d_agent.mcp.registry import REGISTRY

    mesh = REGISTRY["sv_render_mesh_thumbnail"].input_schema
    assert mesh["additionalProperties"] is False
    assert mesh["properties"]["angle_frame"]["enum"] == ["asset_native", "viewer_y_up"]


def test_camera_near_far_scales_with_distance():
    from sci3d_agent.mcp.tools._render import common as C

    znear, zfar = C.camera_near_far(0.04)  # CAD small part: diag * 1.4 ~= 0.04
    assert 0.0 < znear < 0.04              # the object sits IN FRONT of the near plane
    assert zfar > 0.04
    assert C.camera_near_far(1400.0) == (14.0, 140000.0)


def test_build_perspective_camera_passes_near_far():
    from sci3d_agent.mcp.tools._render import common as C

    class FakeCamera:
        def __init__(self, **kw):
            self.kw = kw

    class FakePyrender:
        PerspectiveCamera = FakeCamera

    cam = C.build_perspective_camera(
        FakePyrender, yfov_deg=45.0, aspect=1.0,
        eye=(0.0, 0.0, 0.04), target=(0.0, 0.0, 0.0))
    assert cam.kw["znear"] == C.camera_near_far(0.04)[0]
    assert cam.kw["zfar"] == C.camera_near_far(0.04)[1]
    assert cam.kw["aspectRatio"] == 1.0
    assert cam.kw["yfov"] == pytest.approx(math.radians(45.0))


def test_tiny_mesh_renders_nonblack(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    from PIL import Image

    box = trimesh.creation.box(extents=(0.02, 0.02, 0.02))
    src = tmp_path / "tiny.obj"
    box.export(src)
    env = handle_mesh_thumbnail({"path": str(src), "view": "iso", "size": 64})
    if "error" in env:
        pytest.skip(f"no GL renderer on this host: {env.get('error')}")
    import base64
    import io

    img = np.asarray(
        Image.open(io.BytesIO(base64.b64decode(env["image_png_base64"]))).convert("RGB")
    )
    bg = img[0, 0, :3]
    foreground = (np.abs(img.astype(int) - bg.astype(int)).sum(axis=-1) > 30).mean()
    assert foreground > 0.01, f"tiny mesh rendered {foreground:.4f} foreground fraction"
