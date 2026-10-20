"""End-to-end trajectory for M3D-RefSeg (CT + mask, MPR + overlay).

Mirrors the SPEC §7 trajectory exactly. Uses REGISTRY-installed fakes for
Phase 1/C inspectors so this runs on machines without GL. Asserts the full
pipeline state at the end via sv3d_get_state and sv3d_render echo.
"""

import os

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d._store import reset_store_for_tests
from sci3d_agent.mcp.tools.vision3d.state import _reset_shape_cache


@pytest.fixture(autouse=True)
def _fresh():
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


def _install_fake(name, fn):
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )
    return orig


def _canned_m3d_record(*_):
    return {
        "record": {
            "qid": "m3d-refseg-train-s0212-00000",
            "asset_kind": "nifti",
            "url_viewer": "/datasets/M3D-RefSeg/M3D_RefSeg/s0212/ct.nii.gz",
            "mask": {
                "mask_url_viewer": "/datasets/M3D-RefSeg/M3D_RefSeg/s0212/mask.nii.gz",
                "mask_id": "2",
            },
        }
    }


def _canned_mask_pair(*_):
    return {
        "shape": [256, 256, 100],
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ],
        "labels": [
            {
                "id": 1,
                "voxel_count": 100,
                "centroid_world": [10, 10, 10],
                "bbox_world": [[8, 8, 8], [12, 12, 12]],
                "best_slices": {"axial": 10, "coronal": 10, "sagittal": 10},
            },
            {
                "id": 2,
                "voxel_count": 200,
                "centroid_world": [50, 50, 50],
                "bbox_world": [[40, 40, 40], [60, 60, 60]],
                "best_slices": {"axial": 50, "coronal": 50, "sagittal": 50},
            },
        ],
    }


def _canned_nifti(*_):
    return {
        "shape": [256, 256, 100],
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ],
        "intensity": {
            "min": -1000,
            "max": 2000,
            "p1": -400,
            "p99": 400,
            "mean": 0,
        },
    }


def test_e2e_m3d_refseg_trajectory(_registry_snapshot):
    _install_fake("sv_get_matched_record", _canned_m3d_record)
    _install_fake("sv_inspect_nifti_mask_pair", _canned_mask_pair)
    _install_fake("sv_inspect_nifti", _canned_nifti)

    # 1. Open
    r = REGISTRY["sv3d_open_scene"].handler(
        {"qid": "m3d-refseg-train-s0212-00000"}
    )
    assert "session_id" in r, r
    sid = r["session_id"]
    assert r["kind"] == "nifti"
    assert r["axis_up"] == "RAS"
    assert r["default_mode"] == "mpr_axial"
    assert len(r["masks"]) == 2

    # 2. Set render mode (matches default but verify the call works)
    r = REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "mpr_axial"}
    )
    assert r["mode"] == "mpr_axial"

    # 3. Jump to the best-axial slice for mask_id=2 (centroid at index 50)
    r = REGISTRY["sv3d_set_slice"].handler(
        {"session_id": sid, "axis": "axial", "index": 50}
    )
    assert r["index"] == 50
    assert r["axis"] == "axial"

    # 4. Toggle mask 2 on with overlay
    r = REGISTRY["sv3d_toggle_mask"].handler(
        {
            "session_id": sid,
            "visible": True,
            "mask_id": "2",
            "colormap": "jet",
            "alpha": 0.5,
        }
    )
    assert r["mask_id"] == "2"
    assert r["visible"] is True

    # 5. Render
    r = REGISTRY["sv3d_render"].handler(
        {"session_id": sid, "width": 512, "height": 512}
    )
    # State metadata is checked unconditionally — it must be correct on every
    # box (GL-less dev or GPU host alike).
    assert "camera" in r
    assert r["mode"] == "mpr_axial"
    assert r["slice"]["index"] == 50
    assert r["mask_state"]["mask_id"] == "2"
    # On GL-equipped hosts the deploy script sets SV_E2E=1; in that case we
    # require an actual rendered image, not the renderer_skipped envelope.
    # Without this gate the test would pass even on a misconfigured GPU host
    # (smoke check would lie). On dev boxes SV_E2E is unset so we tolerate
    # renderer_skipped — but if a real image came back, that's also fine.
    if os.environ.get("SV_E2E") == "1":
        assert "renderer_skipped" not in r, (
            f"SV_E2E=1 set but render returned renderer_skipped={r.get('renderer_skipped')!r} — "
            "check pyrender/EGL deps on this host."
        )
        assert ("image_png_base64" in r) or ("image_url" in r), (
            f"SV_E2E=1 set but no image returned: keys={list(r)}"
        )

    # 6. Close
    r = REGISTRY["sv3d_close_scene"].handler({"session_id": sid})
    assert r["closed"] is True
