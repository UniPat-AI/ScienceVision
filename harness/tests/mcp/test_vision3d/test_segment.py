"""Unit tests for sv3d_segment_{2d,3d_volume,3d_mesh} MCP handlers."""
from __future__ import annotations

import base64
from typing import Any

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d import segment as seg_mod
from sci3d_agent.mcp.tools.vision3d._segment_client import SamClientError
from sci3d_agent.mcp.tools.vision3d._store import reset_store_for_tests
from sci3d_agent.mcp.tools.vision3d.session import _open_scene_handler


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture
def _registry_snapshot():
    snap = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snap)


def _install_fake_inspector(name: str, fn):
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )


def _fake_mesh_inspector(args):
    return {
        "format": "ply",
        "n_vertices": 100,
        "n_faces": 50,
        "bbox": [[0.0, 0.0, 0.0], [10.0, 5.0, 3.0]],
        "extent": [10.0, 5.0, 3.0],
        "has_vertex_colors": False,
        "has_uv": False,
        "materials": None,
        "axis_up_hint": "Z",
    }


def _fake_nifti_inspector(args):
    return {
        "shape": [64, 64, 64],
        "spacing": [1.0, 1.0, 1.0],
        "affine_4x4": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
        "orientation": "RAS",
        "intensity": {"min": -1000, "max": 1000, "mean": 0.0, "p1": -400, "p99": 400},
    }


def _open_mesh_session(_registry_snapshot) -> str:
    _install_fake_inspector("sv_inspect_mesh", _fake_mesh_inspector)
    out = _open_scene_handler({"asset_path": "/datasets/fake.ply"})
    assert "error" not in out, out
    return out["session_id"]


def _open_nifti_session(_registry_snapshot) -> str:
    _install_fake_inspector("sv_inspect_nifti", _fake_nifti_inspector)
    out = _open_scene_handler({"asset_path": "/datasets/fake.nii.gz"})
    assert "error" not in out, out
    return out["session_id"]


# --- 2D ---


def test_segment_2d_unknown_session():
    out = seg_mod._segment_2d_handler({"session_id": "nope", "text_prompt": "x"})
    assert "error" in out and "unknown session" in out["error"].lower()


def test_segment_2d_missing_prompt(_registry_snapshot):
    sid = _open_mesh_session(_registry_snapshot)
    out = seg_mod._segment_2d_handler({"session_id": sid})
    assert "error" in out
    assert "text_prompt" in out["error"]


def test_segment_2d_happy_path(_registry_snapshot, monkeypatch):
    sid = _open_mesh_session(_registry_snapshot)

    def fake_render(session, view, *, width, height, bg):
        return {"image_png_base64": base64.b64encode(b"\x89PNG_fake").decode("ascii"), "renderer_used": "fake"}

    monkeypatch.setattr(seg_mod, "_render_one_canonical", fake_render)

    fake_client_resp = {
        "masks": [
            {"mask_b64": base64.b64encode(b"\x89PNG_mask").decode("ascii"), "score": 0.9, "bbox": [0, 0, 10, 10], "area": 100}
        ]
    }
    monkeypatch.setattr(seg_mod, "_default_client", lambda: _StubClient(sam2d_resp=fake_client_resp))

    out = seg_mod._segment_2d_handler({"session_id": sid, "text_prompt": "the chair"})
    assert "error" not in out, out
    assert "image_png_base64" in out
    assert "masks" in out and out["masks"][0]["label"] == "the chair"
    assert out["masks"][0]["score"] == 0.9


def test_segment_2d_client_error_returns_envelope(_registry_snapshot, monkeypatch):
    sid = _open_mesh_session(_registry_snapshot)

    def fake_render(session, view, *, width, height, bg):
        return {"image_png_base64": base64.b64encode(b"x").decode("ascii"), "renderer_used": "fake"}

    monkeypatch.setattr(seg_mod, "_render_one_canonical", fake_render)
    monkeypatch.setattr(seg_mod, "_default_client", lambda: _StubClient(raise_=SamClientError("boom")))

    out = seg_mod._segment_2d_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" in out
    assert "boom" in out["error"]


def test_segment_2d_invalid_view_returns_error(_registry_snapshot):
    sid = _open_mesh_session(_registry_snapshot)
    out = seg_mod._segment_2d_handler({
        "session_id": sid,
        "view": "frunt",  # typo
        "text_prompt": "ribcage",
    })
    assert "error" in out
    assert "unsupported view" in out["error"]


class _StubClient:
    def __init__(self, *, sam2d_resp=None, sam3d_volume_resp=None, sam3d_mesh_resp=None, raise_=None):
        self._r2d = sam2d_resp
        self._r3v = sam3d_volume_resp
        self._r3m = sam3d_mesh_resp
        self._raise = raise_

    def sam2d(self, *, image_b64, text_prompt):
        if self._raise:
            raise self._raise
        return self._r2d

    def sam3d_volume(self, *, volume_path, text_prompt):
        if self._raise:
            raise self._raise
        return self._r3v

    def sam3d_mesh(self, *, pointcloud_path, text_prompt):
        if self._raise:
            raise self._raise
        return self._r3m


# --- 3D volume ---


def test_segment_3d_volume_unknown_session():
    out = seg_mod._segment_3d_volume_handler({"session_id": "nope", "text_prompt": "x"})
    assert "error" in out and "unknown session" in out["error"].lower()


def test_segment_3d_volume_rejects_mesh_session(_registry_snapshot):
    sid = _open_mesh_session(_registry_snapshot)
    out = seg_mod._segment_3d_volume_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" in out
    assert "nifti" in out["error"].lower() or "volume" in out["error"].lower()


def test_segment_3d_volume_happy_path(_registry_snapshot, monkeypatch):
    sid = _open_nifti_session(_registry_snapshot)
    fake_resp = {
        "masks": [
            {
                "voxel_mask_b64": "AA",
                "score": 0.88,
                "voxel_count": 4521,
                "centroid": [10.0, 20.0, 30.0],
                "bbox": [[0.0, 0.0, 0.0], [40.0, 40.0, 40.0]],
            }
        ]
    }
    monkeypatch.setattr(seg_mod, "_default_client", lambda: _StubClient(sam3d_volume_resp=fake_resp))

    out = seg_mod._segment_3d_volume_handler({"session_id": sid, "text_prompt": "the lesion"})
    assert "error" not in out, out
    assert "image_png_base64" not in out, "M3 must NEVER return pixels"
    assert "voxel_mask_b64" not in out, "M3 must NEVER expose raw voxel mask to LLM"
    assert "masks" in out
    m = out["masks"][0]
    assert m["label"] == "the lesion"
    assert m["voxel_count"] == 4521
    assert m["centroid_world"] == [10.0, 20.0, 30.0]
    assert m["bbox_world"] == [[0.0, 0.0, 0.0], [40.0, 40.0, 40.0]]
    # Verify the raw client response leaks no mask bytes
    for k in m.keys():
        assert "b64" not in k.lower(), f"key {k!r} would leak pixels to LLM"


def test_segment_3d_volume_client_error(_registry_snapshot, monkeypatch):
    sid = _open_nifti_session(_registry_snapshot)
    monkeypatch.setattr(seg_mod, "_default_client", lambda: _StubClient(raise_=SamClientError("net")))
    out = seg_mod._segment_3d_volume_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" in out
    assert "net" in out["error"]


# --- 3D mesh ---


def test_segment_3d_mesh_unknown_session():
    out = seg_mod._segment_3d_mesh_handler({"session_id": "nope", "text_prompt": "x"})
    assert "error" in out and "unknown session" in out["error"].lower()


def test_segment_3d_mesh_rejects_nifti_session(_registry_snapshot):
    sid = _open_nifti_session(_registry_snapshot)
    out = seg_mod._segment_3d_mesh_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" in out
    assert "mesh" in out["error"].lower()


def test_segment_3d_mesh_happy_path(_registry_snapshot, monkeypatch):
    sid = _open_mesh_session(_registry_snapshot)
    fake_resp = {
        "segments": [
            {
                "per_point_labels_b64": "AA",
                "score": 0.77,
                "point_count": 832,
                "centroid": [1.0, 2.0, 3.0],
                "bbox": [[0.0, 0.0, 0.0], [4.0, 5.0, 6.0]],
            }
        ]
    }
    monkeypatch.setattr(seg_mod, "_default_client", lambda: _StubClient(sam3d_mesh_resp=fake_resp))

    out = seg_mod._segment_3d_mesh_handler({"session_id": sid, "text_prompt": "the red chair"})
    assert "error" not in out, out
    assert "image_png_base64" not in out
    assert "per_point_labels_b64" not in out
    assert "segments" in out
    s = out["segments"][0]
    assert s["label"] == "the red chair"
    assert s["point_count"] == 832
    assert s["centroid_world"] == [1.0, 2.0, 3.0]
    for k in s.keys():
        assert "b64" not in k.lower()


def test_segment_3d_mesh_client_error(_registry_snapshot, monkeypatch):
    sid = _open_mesh_session(_registry_snapshot)
    monkeypatch.setattr(seg_mod, "_default_client", lambda: _StubClient(raise_=SamClientError("boom")))
    out = seg_mod._segment_3d_mesh_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" in out
    assert "boom" in out["error"]


def test_segment_3d_volume_empty_masks(_registry_snapshot, monkeypatch):
    sid = _open_nifti_session(_registry_snapshot)
    monkeypatch.setattr(
        seg_mod, "_default_client", lambda: _StubClient(sam3d_volume_resp={"masks": []})
    )
    out = seg_mod._segment_3d_volume_handler({"session_id": sid, "text_prompt": "nothing"})
    assert out == {"masks": []}


def test_segment_3d_volume_missing_keys_fallback(_registry_snapshot, monkeypatch):
    sid = _open_nifti_session(_registry_snapshot)
    # SAM returns a malformed entry (no score, no bbox, etc.) — handler must
    # not raise; defaults from `.get(..., default)` should kick in.
    monkeypatch.setattr(
        seg_mod, "_default_client", lambda: _StubClient(sam3d_volume_resp={"masks": [{}]})
    )
    out = seg_mod._segment_3d_volume_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" not in out
    m = out["masks"][0]
    assert m == {
        "label": "x",
        "score": 0.0,
        "voxel_count": 0,
        "centroid_world": [0.0, 0.0, 0.0],
        "bbox_world": [[0, 0, 0], [0, 0, 0]],
    }


def test_segment_3d_mesh_empty_segments(_registry_snapshot, monkeypatch):
    sid = _open_mesh_session(_registry_snapshot)
    monkeypatch.setattr(
        seg_mod, "_default_client", lambda: _StubClient(sam3d_mesh_resp={"segments": []})
    )
    out = seg_mod._segment_3d_mesh_handler({"session_id": sid, "text_prompt": "nothing"})
    assert out == {"segments": []}


def test_segment_3d_mesh_missing_keys_fallback(_registry_snapshot, monkeypatch):
    sid = _open_mesh_session(_registry_snapshot)
    monkeypatch.setattr(
        seg_mod, "_default_client", lambda: _StubClient(sam3d_mesh_resp={"segments": [{}]})
    )
    out = seg_mod._segment_3d_mesh_handler({"session_id": sid, "text_prompt": "x"})
    assert "error" not in out
    s = out["segments"][0]
    assert s == {
        "label": "x",
        "score": 0.0,
        "point_count": 0,
        "centroid_world": [0.0, 0.0, 0.0],
        "bbox_world": [[0, 0, 0], [0, 0, 0]],
    }


# --- registration ---


def test_three_tools_registered():
    # Just importing the module should have registered the tools at server startup.
    # Use the package-level import path to mirror server.py.
    from sci3d_agent.mcp.tools.vision3d import segment  # noqa: F401
    from sci3d_agent.mcp.registry import REGISTRY

    for name in ("sv3d_segment_2d", "sv3d_segment_3d_volume", "sv3d_segment_3d_mesh"):
        assert name in REGISTRY, f"missing {name}"
        spec = REGISTRY[name]
        assert spec.category == "F"
        assert callable(spec.handler)
        assert isinstance(spec.input_schema, dict)
        assert spec.input_schema["additionalProperties"] is False


def test_total_tool_count_58():
    """Advertised-surface count: 53 base -> 57 (segment + crosshair) -> 58.

    The 58th tool is ``sv_render_qa_record_summary`` (2026-08-05, the
    deterministic-render change); this test was stale at 57 since then, hidden
    by the tests/mcp package skip on clones without the raw data layers. Bump
    the number when the advertised surface legitimately grows; CLAUDE.md pins
    the same 58 via ``sciencevision-mcp --list-tools``.

    The canonical tool count is ``ALL_TOOLS`` (what ``server.py --list-tools``
    advertises), NOT ``len(REGISTRY)``: the 8 pipeline_write tools are
    intentionally collected into ALL_TOOLS without calling ``register()``, so
    the REGISTRY dict is a strict subset. The criterion is about the advertised
    surface, so assert against ALL_TOOLS / the CLI output.
    """
    # server.py imports all tool modules on startup and exposes ALL_TOOLS.
    from sci3d_agent.mcp import server  # noqa: F401
    from sci3d_agent.mcp.tools import ALL_TOOLS

    names = [t.name for t in ALL_TOOLS]
    assert len(names) == 58, (
        f"expected 58 tools (57 + sv_render_qa_record_summary); "
        f"got {len(names)}: {sorted(names)}"
    )
    # All three segment tools must be advertised.
    for name in ("sv3d_segment_2d", "sv3d_segment_3d_volume", "sv3d_segment_3d_mesh"):
        assert name in names, f"{name} not advertised in ALL_TOOLS"
    # The linked-crosshair tool must be advertised.
    assert "sv3d_set_crosshair" in names
