"""Phase 3.2 / F-render-state tests for the 5 state mutator tools."""

from __future__ import annotations

import math

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d import state as state_mod
from sci3d_agent.mcp.tools.vision3d._store import (
    get_store,
    reset_store_for_tests,
)
from sci3d_agent.mcp.tools.vision3d.session import _open_scene_handler
from sci3d_agent.mcp.tools.vision3d.state import (
    _reset_shape_cache,
    _set_clip_plane_handler,
    _set_opacity_handler,
    _set_render_mode_handler,
    _set_slice_handler,
    _toggle_mask_handler,
)


# --- fixtures / helpers ---------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_store_for_tests()
    _reset_shape_cache()
    yield
    reset_store_for_tests()
    _reset_shape_cache()


@pytest.fixture
def _registry_snapshot():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def _install_fake(name: str, fn):
    """Replace REGISTRY[name].handler with fn for the duration of a test."""
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )
    return orig


def _fake_mesh_inspector(bbox=((0.0, 0.0, 0.0), (10.0, 5.0, 3.0)), axis_up_hint="Z"):
    return {
        "format": "ply",
        "n_vertices": 100,
        "n_faces": 50,
        "bbox": [list(bbox[0]), list(bbox[1])],
        "extent": [bbox[1][i] - bbox[0][i] for i in range(3)],
        "has_vertex_colors": False,
        "has_uv": False,
        "materials": None,
        "axis_up_hint": axis_up_hint,
    }


def _fake_nifti_inspector(shape=(100, 100, 50), p1=-500.0, p99=500.0):
    return {
        "shape": list(shape),
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "orientation": "RAS",
        "intensity": {
            "min": -1000.0,
            "max": 1000.0,
            "mean": 0.0,
            "p1": p1,
            "p99": p99,
        },
    }


def _fake_nifti_mask_pair_inspector(
    shape=(256, 256, 100), label_ids=(1, 2)
):
    labels = []
    for i, lid in enumerate(label_ids):
        labels.append(
            {
                "id": lid,
                "voxel_count": 100 + i,
                "bbox_voxel": [[10, 10, 5], [30, 30, 25]],
                "bbox_world": [[10.0, 10.0, 10.0], [30.0, 30.0, 50.0]],
                "centroid_voxel": [20.0, 20.0, 15.0],
                "centroid_world": [20.0, 20.0, 30.0],
                "best_slices": {"axial": 15, "coronal": 20, "sagittal": 20},
            }
        )
    return {
        "shape": list(shape),
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "labels": labels,
    }


def _open_mesh(_registry_snapshot) -> str:
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())
    out = _open_scene_handler({"asset_path": "/datasets/a.ply"})
    assert "error" not in out, out
    return out["session_id"]


def _open_nifti(
    _registry_snapshot, shape=(100, 100, 50), p1=-500.0, p99=500.0
) -> str:
    _install_fake(
        "sv_inspect_nifti",
        lambda args: _fake_nifti_inspector(shape=shape, p1=p1, p99=p99),
    )
    out = _open_scene_handler({"asset_path": "/datasets/a.nii.gz"})
    assert "error" not in out, out
    return out["session_id"]


def _open_nifti_with_masks(
    _registry_snapshot, shape=(256, 256, 100), label_ids=(1, 2)
) -> str:
    _install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda args: _fake_nifti_mask_pair_inspector(
            shape=shape, label_ids=label_ids
        ),
    )
    _install_fake(
        "sv_inspect_nifti",
        lambda args: _fake_nifti_inspector(shape=shape, p1=-200.0, p99=300.0),
    )
    out = _open_scene_handler(
        {
            "asset_path": "/datasets/foo/ct.nii.gz",
            "mask_path": "/datasets/foo/mask.nii.gz",
        }
    )
    assert "error" not in out, out
    return out["session_id"]


# --- 1) set_render_mode: surface on mesh ----------------------------------


def test_set_mode_surface_on_mesh(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_render_mode_handler({"session_id": sid, "mode": "surface"})
    assert out == {"mode": "surface", "applied": True}
    assert get_store().get(sid).mode == "surface"


# --- 2) set_render_mode: volume on mesh -> error --------------------------


def test_set_mode_volume_on_mesh_errors(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_render_mode_handler({"session_id": sid, "mode": "volume"})
    assert "error" in out
    assert "mesh session" in out["error"]


# --- 3) set_render_mode: mpr_axial on nifti -------------------------------


def test_set_mode_mpr_axial_on_nifti(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = _set_render_mode_handler({"session_id": sid, "mode": "mpr_axial"})
    assert out == {"mode": "mpr_axial", "applied": True}
    assert get_store().get(sid).mode == "mpr_axial"


# --- 4) set_render_mode: surface on nifti -> error ------------------------


def test_set_mode_surface_on_nifti_errors(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = _set_render_mode_handler({"session_id": sid, "mode": "surface"})
    assert "error" in out
    assert "nifti session" in out["error"]


def test_set_mode_unknown_session():
    out = _set_render_mode_handler({"session_id": "no-such", "mode": "surface"})
    assert "error" in out
    assert "unknown session_id" in out["error"]


def test_set_mode_invalid_enum(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_render_mode_handler({"session_id": sid, "mode": "spaghetti"})
    assert "error" in out


def test_set_mode_ignores_point_size_and_shading(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_render_mode_handler(
        {
            "session_id": sid,
            "mode": "points",
            "point_size": 3.0,
            "shading": "smooth",
        }
    )
    assert out["mode"] == "points"
    assert out["applied"] is True


# --- 5) set_opacity: base layer -------------------------------------------


def test_set_opacity_layer_base(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_opacity_handler(
        {"session_id": sid, "layer": "base", "alpha": 0.42}
    )
    assert out == {"layer": "base", "alpha": 0.42}
    op = get_store().get(sid).opacity
    assert op.base == 0.42
    # other layers untouched
    assert op.mask == 0.5
    assert op.clip_cap == 1.0


# --- 6) set_opacity: mask layer -------------------------------------------


def test_set_opacity_layer_mask(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_opacity_handler(
        {"session_id": sid, "layer": "mask", "alpha": 0.3}
    )
    assert out == {"layer": "mask", "alpha": 0.3}
    op = get_store().get(sid).opacity
    assert op.mask == 0.3
    assert op.base == 1.0


# --- 7) set_opacity: out-of-range alpha -----------------------------------


@pytest.mark.parametrize("alpha", [-0.1, 1.1, 2.0])
def test_set_opacity_alpha_out_of_range(_registry_snapshot, alpha):
    sid = _open_mesh(_registry_snapshot)
    out = _set_opacity_handler(
        {"session_id": sid, "layer": "base", "alpha": alpha}
    )
    assert "error" in out
    assert "alpha" in out["error"]


# --- 8) set_opacity: boundary 0.0 and 1.0 ---------------------------------


def test_set_opacity_boundary_zero_and_one(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    for boundary in (0.0, 1.0):
        out = _set_opacity_handler(
            {"session_id": sid, "layer": "clip_cap", "alpha": boundary}
        )
        assert out == {"layer": "clip_cap", "alpha": boundary}


def test_set_opacity_invalid_layer(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_opacity_handler(
        {"session_id": sid, "layer": "ghost", "alpha": 0.5}
    )
    assert "error" in out


# --- 9) toggle_mask: visibility only --------------------------------------


def test_toggle_mask_visibility_only(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot)
    out = _toggle_mask_handler({"session_id": sid, "visible": False})
    assert out["visible"] is False
    # other fields preserved at session defaults
    assert out["colormap"] == "red"
    assert out["alpha"] == 0.5
    assert out["mask_id"] is None


# --- 10) toggle_mask: change id and colormap ------------------------------


def test_toggle_mask_change_id_and_colormap(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot, label_ids=(1, 2))
    out = _toggle_mask_handler(
        {
            "session_id": sid,
            "mask_id": "2",
            "colormap": "viridis",
            "alpha": 0.7,
        }
    )
    assert out["mask_id"] == "2"
    assert out["colormap"] == "viridis"
    assert out["alpha"] == 0.7
    assert out["visible"] is True  # untouched -> default for nifti+mask is True


# --- 11) toggle_mask: unknown mask_id -> error ----------------------------


def test_toggle_mask_unknown_id_errors(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot, label_ids=(1, 2))
    out = _toggle_mask_handler({"session_id": sid, "mask_id": "99"})
    assert "error" in out
    assert "99" in out["error"]
    # Available IDs are echoed back for agent self-correction.
    assert "['1', '2']" in out["error"] or "available" in out["error"]


# --- 12) toggle_mask: no masks on session -> error ------------------------


def test_toggle_mask_no_masks_errors(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = _toggle_mask_handler({"session_id": sid, "mask_id": "1"})
    assert "error" in out
    assert "no masks" in out["error"]


def test_toggle_mask_invalid_colormap(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot)
    out = _toggle_mask_handler(
        {"session_id": sid, "colormap": "rainbow"}
    )
    assert "error" in out


def test_toggle_mask_no_args_is_noop(_registry_snapshot):
    sid = _open_nifti_with_masks(_registry_snapshot)
    out = _toggle_mask_handler({"session_id": sid})
    assert out["visible"] is True
    assert out["mask_id"] is None
    assert out["colormap"] == "red"


# --- 13) set_slice by index -----------------------------------------------


def test_set_slice_by_index(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(100, 100, 50))
    out = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "index": 25}
    )
    assert "error" not in out, out
    assert out["axis"] == "axial"
    assert out["index"] == 25
    # axial last = 49 -> fraction = 25/49
    assert out["fraction"] == pytest.approx(25.0 / 49.0)


# --- 14) set_slice by fraction (rounding contract) ------------------------


def test_set_slice_by_fraction_rounding(_registry_snapshot):
    # shape[axial=numpy axis 2] = 100 -> last = 99 -> int(round(0.5 * 99)) = 50.
    # NOTE: spec example said 49 was acceptable, but the LOCKED formula is
    # int(round(fraction * (shape[axis] - 1))), which yields 50 here because
    # round(49.5) -> 50 under Python's banker's rounding on this Python build.
    # In practice: round(0.5 * 99) = round(49.5). Verify whichever the formula
    # picks (50 under banker's rounding to nearest even when last is even;
    # 49 when banker's picks even for round(49.5)=50). The contract is the
    # FORMULA, not a specific tie-breaking; assert the formula directly.
    sid = _open_nifti(_registry_snapshot, shape=(100, 100, 100))
    out = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "fraction": 0.5}
    )
    assert "error" not in out, out
    assert out["index"] == int(round(0.5 * 99))
    assert out["fraction"] == 0.5


def test_set_slice_fraction_bounds(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(10, 10, 10))
    # fraction=0 -> index 0
    out0 = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "fraction": 0.0}
    )
    assert out0["index"] == 0
    # fraction=1 -> last index (9)
    out1 = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "fraction": 1.0}
    )
    assert out1["index"] == 9


def test_set_slice_axis_mapping_sagittal_coronal(_registry_snapshot):
    # shape = (200, 100, 50): sagittal->200, coronal->100, axial->50.
    sid = _open_nifti(_registry_snapshot, shape=(200, 100, 50))
    out_s = _set_slice_handler(
        {"session_id": sid, "axis": "sagittal", "fraction": 1.0}
    )
    assert out_s["index"] == 199
    out_c = _set_slice_handler(
        {"session_id": sid, "axis": "coronal", "fraction": 1.0}
    )
    assert out_c["index"] == 99


# --- 15) set_slice both index+fraction -> error ---------------------------


def test_set_slice_both_index_and_fraction_errors(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = _set_slice_handler(
        {
            "session_id": sid,
            "axis": "axial",
            "index": 10,
            "fraction": 0.5,
        }
    )
    assert "error" in out
    assert "exactly one" in out["error"]


# --- 16) set_slice on mesh -> error ---------------------------------------


def test_set_slice_on_mesh_errors(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "fraction": 0.5}
    )
    assert "error" in out
    assert "nifti" in out["error"]


def test_set_slice_invalid_axis(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = _set_slice_handler(
        {"session_id": sid, "axis": "diagonal", "fraction": 0.5}
    )
    assert "error" in out


def test_set_slice_fraction_out_of_range(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot)
    out = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "fraction": 1.5}
    )
    assert "error" in out


def test_set_slice_index_out_of_range(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(10, 10, 10))
    out = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "index": 99}
    )
    assert "error" in out


# --- 17) set_slice window/level round trip --------------------------------


def test_set_slice_window_level_round_trip(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(10, 10, 10))

    # Both provided: window=400 (width), level=40 -> (-160, 240) center 40.
    out_full = _set_slice_handler(
        {
            "session_id": sid,
            "axis": "axial",
            "fraction": 0.5,
            "window": 400.0,
            "level": 40.0,
        }
    )
    assert out_full["window"] == [-160.0, 240.0]
    assert out_full["level"] == 40.0

    # Only window=200 -> keep level=40 (from previous call), recompute range.
    out_w = _set_slice_handler(
        {
            "session_id": sid,
            "axis": "axial",
            "fraction": 0.5,
            "window": 200.0,
        }
    )
    assert out_w["level"] == 40.0
    assert out_w["window"] == [-60.0, 140.0]

    # Only level=100 -> keep width=200, recenter at 100.
    out_l = _set_slice_handler(
        {
            "session_id": sid,
            "axis": "axial",
            "fraction": 0.5,
            "level": 100.0,
        }
    )
    assert out_l["level"] == 100.0
    assert out_l["window"] == [0.0, 200.0]


def test_set_slice_keep_current_when_no_index_or_fraction(_registry_snapshot):
    sid = _open_nifti(_registry_snapshot, shape=(10, 10, 10))
    # First, set an explicit index. Then call again without index/fraction;
    # the slice should keep its previous (index, fraction).
    first = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "index": 4}
    )
    assert first["index"] == 4
    second = _set_slice_handler(
        {"session_id": sid, "axis": "coronal"}
    )
    # axis changed, index/fraction preserved from previous slice state.
    assert second["axis"] == "coronal"
    assert second["index"] == 4


# --- 18) set_clip_plane: enable with normal normalization -----------------


def test_set_clip_plane_enable_with_normal_normalization(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_clip_plane_handler(
        {
            "session_id": sid,
            "enabled": True,
            "origin": [1.0, 2.0, 3.0],
            "normal": [3.0, 0.0, 4.0],  # |v| = 5, unit -> (0.6, 0, 0.8)
            "cap": False,
        }
    )
    assert out["enabled"] is True
    assert out["origin"] == [1.0, 2.0, 3.0]
    assert out["normal"][0] == pytest.approx(0.6)
    assert out["normal"][1] == pytest.approx(0.0)
    assert out["normal"][2] == pytest.approx(0.8)
    # unit-length check
    nx, ny, nz = out["normal"]
    assert math.sqrt(nx * nx + ny * ny + nz * nz) == pytest.approx(1.0)
    assert out["cap"] is False


# --- 19) set_clip_plane: zero normal -> error -----------------------------


def test_set_clip_plane_zero_normal_errors(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_clip_plane_handler(
        {
            "session_id": sid,
            "enabled": True,
            "origin": [0.0, 0.0, 0.0],
            "normal": [0.0, 0.0, 0.0],
        }
    )
    assert "error" in out
    assert "zero vector" in out["error"]


# --- 20) set_clip_plane: disable keeps previous origin --------------------


def test_set_clip_plane_disable_keeps_previous_origin(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    # Step 1: enable + set a non-default origin
    _set_clip_plane_handler(
        {
            "session_id": sid,
            "enabled": True,
            "origin": [5.0, 6.0, 7.0],
            "normal": [1.0, 0.0, 0.0],
        }
    )
    # Step 2: disable without specifying origin/normal -> keep both
    out = _set_clip_plane_handler({"session_id": sid, "enabled": False})
    assert out["enabled"] is False
    assert out["origin"] == [5.0, 6.0, 7.0]
    assert out["normal"] == [1.0, 0.0, 0.0]


def test_set_clip_plane_invalid_origin_shape(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _set_clip_plane_handler(
        {"session_id": sid, "enabled": True, "origin": [1.0, 2.0]}
    )
    assert "error" in out
    assert "origin" in out["error"]


# --- 21) full M3D-RefSeg pipeline (mpr + mask + opacity) ------------------


def test_full_m3d_refseg_pipeline(_registry_snapshot):
    sid = _open_nifti_with_masks(
        _registry_snapshot, shape=(256, 256, 100), label_ids=(1, 2)
    )

    # 1) Switch to mpr_axial
    r1 = _set_render_mode_handler({"session_id": sid, "mode": "mpr_axial"})
    assert r1 == {"mode": "mpr_axial", "applied": True}

    # 2) Scrub to the middle axial slice via fraction
    r2 = _set_slice_handler(
        {"session_id": sid, "axis": "axial", "fraction": 0.5}
    )
    assert "error" not in r2
    assert r2["axis"] == "axial"
    # shape[axial=2]=100 -> last=99 -> int(round(0.5*99)) = 50
    assert r2["index"] == int(round(0.5 * 99))

    # 3) Make mask id=2 visible at alpha 0.5
    r3 = _toggle_mask_handler(
        {
            "session_id": sid,
            "visible": True,
            "mask_id": "2",
            "alpha": 0.5,
        }
    )
    assert r3["visible"] is True
    assert r3["mask_id"] == "2"
    assert r3["alpha"] == 0.5

    # 4) Read full state back through sv3d_get_state to confirm all three
    state_out = REGISTRY["sv3d_get_state"].handler({"session_id": sid})
    assert state_out["mode"] == "mpr_axial"
    assert state_out["slice"]["axis"] == "axial"
    assert state_out["slice"]["index"] == int(round(0.5 * 99))
    assert state_out["mask_state"]["visible"] is True
    assert state_out["mask_state"]["mask_id"] == "2"
    assert state_out["mask_state"]["alpha"] == 0.5


# --- 22) tools are registered + present in TOOLS list ---------------------


def test_state_tools_registered_in_module():
    names = {t.name for t in state_mod.TOOLS}
    assert names == {
        "sv3d_set_render_mode",
        "sv3d_set_opacity",
        "sv3d_toggle_mask",
        "sv3d_set_slice",
        "sv3d_set_clip_plane",
        "sv3d_set_crosshair",
    }
    for name in names:
        assert name in REGISTRY
        assert REGISTRY[name].category == "F"


# --- 23) missing session_id envelopes -------------------------------------


@pytest.mark.parametrize(
    "handler,args",
    [
        (_set_render_mode_handler, {"mode": "surface"}),
        (_set_opacity_handler, {"layer": "base", "alpha": 0.5}),
        (_toggle_mask_handler, {"visible": True}),
        (_set_slice_handler, {"axis": "axial"}),
        (_set_clip_plane_handler, {"enabled": True}),
    ],
)
def test_missing_session_id_envelope(handler, args):
    out = handler(args)
    assert "error" in out
    assert "session_id" in out["error"]
