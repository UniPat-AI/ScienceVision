from sci3d_agent.mcp import server  # noqa: F401
from sci3d_agent.mcp.registry import REGISTRY

EXPECTED_SV3D = {
    "sv3d_open_scene",
    "sv3d_close_scene",
    "sv3d_get_state",
    "sv3d_camera_orbit",
    "sv3d_camera_dolly",
    "sv3d_camera_pan",
    "sv3d_camera_set_view",
    "sv3d_camera_look_at",
    "sv3d_set_render_mode",
    "sv3d_set_opacity",
    "sv3d_toggle_mask",
    "sv3d_set_slice",
    "sv3d_set_clip_plane",
    "sv3d_set_crosshair",
    "sv3d_render",
    "sv3d_render_canonical_views",
    "sv3d_segment_2d",
    "sv3d_segment_3d_volume",
    "sv3d_segment_3d_mesh",
}


def test_all_sv3d_tools_registered():
    actual = {n for n in REGISTRY if n.startswith("sv3d_")}
    assert actual == EXPECTED_SV3D


def test_every_sv3d_schema_has_session_id_or_oneof():
    for name in EXPECTED_SV3D:
        schema = REGISTRY[name].input_schema
        if name == "sv3d_open_scene":
            assert "oneOf" in schema, f"{name} must accept qid|asset_path"
        else:
            req = schema.get("required", [])
            assert "session_id" in req, f"{name} missing required session_id"


def test_camera_orbit_accepts_three_axes():
    s = REGISTRY["sv3d_camera_orbit"].input_schema
    assert {"d_azimuth_deg", "d_elevation_deg"} <= set(s["required"])
    assert "d_roll_deg" in s["properties"]


def test_dolly_factor_bounds():
    s = REGISTRY["sv3d_camera_dolly"].input_schema["properties"]["factor"]
    assert s["minimum"] == 0.05 and s["maximum"] == 20.0


def test_pan_dx_dy_bounds():
    p = REGISTRY["sv3d_camera_pan"].input_schema["properties"]
    assert p["dx_frac"]["minimum"] == -2.0 and p["dx_frac"]["maximum"] == 2.0
    assert p["dy_frac"]["minimum"] == -2.0 and p["dy_frac"]["maximum"] == 2.0


def test_set_view_enum_complete():
    enum = REGISTRY["sv3d_camera_set_view"].input_schema["properties"]["view"]["enum"]
    assert set(enum) == {
        "front", "back", "left", "right", "top", "bottom", "iso", "reset"
    }


def test_set_opacity_layers():
    enum = REGISTRY["sv3d_set_opacity"].input_schema["properties"]["layer"]["enum"]
    assert set(enum) == {"base", "mask", "clip_cap"}


def test_render_mode_covers_mesh_and_nifti():
    enum = REGISTRY["sv3d_set_render_mode"].input_schema["properties"]["mode"]["enum"]
    assert {"surface", "points"} <= set(enum)
    assert {"volume", "mpr_axial", "mpr_sagittal", "mpr_coronal"} <= set(enum)


def test_set_slice_window_level_present():
    p = REGISTRY["sv3d_set_slice"].input_schema["properties"]
    assert {"axis", "index", "fraction", "window", "level"} <= set(p)
