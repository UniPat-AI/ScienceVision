"""Phase 3.3 / F-snapshot unit tests for sv3d_render + sv3d_render_canonical_views."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d import snapshot as snap_mod
from sci3d_agent.mcp.tools.vision3d._store import (
    get_store,
    reset_store_for_tests,
)
from sci3d_agent.mcp.tools.vision3d.session import _open_scene_handler
from sci3d_agent.mcp.tools.vision3d.snapshot import (
    _render_canonical_views_handler,
    _render_handler,
)
from sci3d_agent.mcp.tools.vision3d.state import (
    _reset_shape_cache,
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


def _fake_nifti_mask_pair_inspector(shape=(256, 256, 100), label_ids=(1, 2)):
    labels = []
    for i, lid in enumerate(label_ids):
        labels.append(
            {
                "id": lid,
                "voxel_count": 100 * (i + 1),
                "bbox_voxel": [[8, 8, 8], [12, 12, 12]],
                "bbox_world": [[8.0, 8.0, 8.0], [12.0, 12.0, 12.0]],
                "centroid_voxel": [10.0, 10.0, 10.0],
                "centroid_world": [10.0, 10.0, 10.0],
                "best_slices": {"axial": 10, "coronal": 10, "sagittal": 10},
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


def _fake_nifti_inspector(shape=(256, 256, 100)):
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
        "intensity": {"min": -1000, "max": 1000, "mean": 0.0, "p1": -400, "p99": 400},
    }


def _open_mesh(_registry_snapshot) -> str:
    _install_fake("sv_inspect_mesh", lambda args: _fake_mesh_inspector())
    out = _open_scene_handler({"asset_path": "/datasets/test.ply"})
    assert "error" not in out, out
    return out["session_id"]


def _open_nifti_with_mask(_registry_snapshot) -> str:
    _install_fake(
        "sv_get_matched_record",
        lambda args: {
            "record": {
                "qid": "fake-nifti",
                "asset_kind": "nifti",
                "url_viewer": "/datasets/test/ct.nii.gz",
                "mask": {
                    "mask_url_viewer": "/datasets/test/mask.nii.gz",
                    "mask_id": "2",
                },
            }
        },
    )
    _install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda args: _fake_nifti_mask_pair_inspector(),
    )
    _install_fake("sv_inspect_nifti", lambda args: _fake_nifti_inspector())
    out = _open_scene_handler({"qid": "fake-nifti"})
    assert "error" not in out, out
    return out["session_id"]


# ---------------------------------------------------------------------------
# 1) Unknown session
# ---------------------------------------------------------------------------


def test_render_unknown_session_errors():
    out = _render_handler({"session_id": "does-not-exist"})
    assert "error" in out
    assert "unknown session_id" in out["error"]


# ---------------------------------------------------------------------------
# 2) Envelope keys: camera + mode always; slice for nifti
# ---------------------------------------------------------------------------


def test_render_returns_envelope_keys_mesh(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _render_handler({"session_id": sid, "width": 256, "height": 256})
    assert "camera" in out
    assert out["mode"] == "surface"
    assert "slice" not in out
    assert "renderer_used" in out or "renderer_skipped" in out


def test_render_returns_envelope_keys_nifti(_registry_snapshot):
    sid = _open_nifti_with_mask(_registry_snapshot)
    out = _render_handler({"session_id": sid, "width": 128, "height": 128})
    assert "camera" in out
    assert out["mode"] == "mpr_axial"
    assert "slice" in out
    assert "mask_state" in out
    assert "renderer_used" in out or "renderer_skipped" in out


# ---------------------------------------------------------------------------
# 3) Canonical views tile count
# ---------------------------------------------------------------------------


def test_render_canonical_views_tile_count(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _render_canonical_views_handler(
        {"session_id": sid, "views": ["front", "back", "left", "right"]}
    )
    assert "images" in out
    assert len(out["images"]) == 4


# ---------------------------------------------------------------------------
# 4) Canonical views: each image carries its own camera
# ---------------------------------------------------------------------------


def test_render_canonical_views_camera_per_image(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _render_canonical_views_handler(
        {"session_id": sid, "views": ["front", "iso", "top"]}
    )
    cams = [img["camera"] for img in out["images"]]
    assert len(cams) == 3
    # Cameras should differ from each other
    positions = [tuple(c["position"]) for c in cams]
    assert len(set(positions)) == 3


# ---------------------------------------------------------------------------
# 5) Canonical views: no session mutation
# ---------------------------------------------------------------------------


def test_render_canonical_views_no_session_mutation(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    pre = get_store().get(sid).camera
    out = _render_canonical_views_handler(
        {"session_id": sid, "views": ["front", "iso"]}
    )
    assert "images" in out
    post = get_store().get(sid).camera
    assert pre.position == post.position
    assert pre.target == post.target
    assert pre.up == post.up
    assert pre.fov_deg == post.fov_deg


# ---------------------------------------------------------------------------
# 6) No GL -> renderer_skipped envelope
# ---------------------------------------------------------------------------


def test_render_no_gl_returns_skipped_envelope(_registry_snapshot, monkeypatch):
    sid = _open_mesh(_registry_snapshot)

    def _no_module(name: str):
        return None

    monkeypatch.setattr(snap_mod._common, "try_import", _no_module)
    out = _render_handler({"session_id": sid, "width": 128, "height": 128})
    assert "renderer_skipped" in out
    assert "would_render" in out
    assert "camera" in out
    assert out["mode"] == "surface"


# ---------------------------------------------------------------------------
# 7) Determinism: identical state -> identical bytes (when renderer runs)
# ---------------------------------------------------------------------------


def test_determinism_same_state_same_output(_registry_snapshot, monkeypatch):
    """Two consecutive calls with no state change produce identical PNGs.

    Stubs the mesh dispatcher to return a fixed RGBA array; the surrounding
    envelope plumbing must hash to the same bytes both times.
    """
    sid = _open_mesh(_registry_snapshot)

    import numpy as np

    fixed_rgba = np.full((128, 128, 3), 42, dtype=np.uint8)

    def _fake_render_mesh(
        session,
        *,
        width,
        height,
        bg,
        camera_override=None,
    ):
        png = snap_mod._common.rgba_to_png(fixed_rgba)
        sha = hashlib.sha256(b"fixed").hexdigest()
        envelope = snap_mod._common.build_image_envelope(png, cache_sha=sha)
        envelope.update(snap_mod._base_echo(session, width=width, height=height))
        cam = camera_override or session.camera
        envelope["camera"] = snap_mod._camera_dict(cam)
        envelope["renderer_used"] = "mesh_surface"
        return envelope

    monkeypatch.setattr(snap_mod, "_render_mesh", _fake_render_mesh)

    a = _render_handler({"session_id": sid, "width": 128, "height": 128})
    b = _render_handler({"session_id": sid, "width": 128, "height": 128})
    assert "image_png_base64" in a or "image_url" in a
    assert a.get("image_png_base64") == b.get("image_png_base64")
    assert a.get("image_url") == b.get("image_url")


# ---------------------------------------------------------------------------
# 8) MPR overlay dispatch: visible vs hidden mask
# ---------------------------------------------------------------------------


def test_render_mpr_overlay_dispatch_visible(_registry_snapshot, monkeypatch):
    sid = _open_nifti_with_mask(_registry_snapshot)
    # Force mask visible + pick a mask_id
    out = _toggle_mask_handler(
        {"session_id": sid, "visible": True, "mask_id": "2", "alpha": 0.4}
    )
    assert out.get("visible") is True

    captured: dict[str, Any] = {}

    def _fake_overlay(args):
        captured["called"] = True
        # Return a fake successful envelope
        return {"image_png_base64": "Zg==", "content_type": "image/png"}

    def _fake_slice(args):
        captured["slice_called"] = True
        return {"image_png_base64": "Zw==", "content_type": "image/png"}

    monkeypatch.setattr(snap_mod._nifti, "handle_nifti_overlay", _fake_overlay)
    monkeypatch.setattr(snap_mod._nifti, "handle_nifti_slice", _fake_slice)

    result = _render_handler({"session_id": sid, "width": 128, "height": 128})
    assert captured.get("called") is True
    assert captured.get("slice_called") is None
    assert result["renderer_used"] == "nifti_overlay"


def test_render_mpr_overlay_dispatch_hidden(_registry_snapshot, monkeypatch):
    sid = _open_nifti_with_mask(_registry_snapshot)
    # Hide the mask
    out = _toggle_mask_handler({"session_id": sid, "visible": False})
    assert out.get("visible") is False

    captured: dict[str, Any] = {}

    def _fake_overlay(args):
        captured["overlay_called"] = True
        return {"image_png_base64": "Zg==", "content_type": "image/png"}

    def _fake_slice(args):
        captured["slice_called"] = True
        return {"image_png_base64": "Zw==", "content_type": "image/png"}

    monkeypatch.setattr(snap_mod._nifti, "handle_nifti_overlay", _fake_overlay)
    monkeypatch.setattr(snap_mod._nifti, "handle_nifti_slice", _fake_slice)

    result = _render_handler({"session_id": sid, "width": 128, "height": 128})
    assert captured.get("slice_called") is True
    assert captured.get("overlay_called") is None
    assert result["renderer_used"] == "nifti_slice"


# ---------------------------------------------------------------------------
# Bonus: input validation
# ---------------------------------------------------------------------------


def test_render_requires_session_id():
    out = _render_handler({})
    assert "error" in out


def test_render_canonical_requires_session_id():
    out = _render_canonical_views_handler({})
    assert "error" in out


def test_render_canonical_rejects_unknown_view(_registry_snapshot):
    sid = _open_mesh(_registry_snapshot)
    out = _render_canonical_views_handler({"session_id": sid, "views": ["bogus"]})
    assert "error" in out


def test_render_canonical_tile_assembles_when_renderer_runs(
    _registry_snapshot, monkeypatch
):
    """When the primitive returns real PNGs, tile=True produces tiled_image_b64."""
    sid = _open_mesh(_registry_snapshot)

    import base64
    import io

    from PIL import Image

    # Build a real 32x32 PNG for stub
    img = Image.new("RGB", (32, 32), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    fake_png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    def _fake_render_mesh(
        session, *, width, height, bg, camera_override=None,
    ):
        cam = camera_override or session.camera
        return {
            **snap_mod._base_echo(session, width=width, height=height),
            "image_png_base64": fake_png_b64,
            "camera": snap_mod._camera_dict(cam),
            "renderer_used": "mesh_surface",
        }

    monkeypatch.setattr(snap_mod, "_render_mesh", _fake_render_mesh)

    out = _render_canonical_views_handler(
        {"session_id": sid, "views": ["front", "back"], "tile": True}
    )
    assert "images" in out
    assert "tiled_image_b64" in out
