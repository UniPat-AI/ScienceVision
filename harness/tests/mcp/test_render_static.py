"""Tests for Category D — Static renderers."""

from __future__ import annotations

import base64
import builtins
import importlib
import io
import sys
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sci3d_agent.mcp.registry import REGISTRY
from sci3d_agent.mcp.tools import render_static as rs
from sci3d_agent.mcp.tools._render import common as rs_common


REPO_ROOT = Path("/Users/sue/Research/ScienceVision")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_registry():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _isolated_render_cache(tmp_path, monkeypatch):
    """Redirect render cache to tmp dir so tests are hermetic."""
    fresh = tmp_path / "renders"
    monkeypatch.setenv("SV_RENDER_CACHE", str(fresh))
    yield fresh


def _make_nifti(tmp_path: Path, arr: np.ndarray, name: str = "vol.nii.gz") -> Path:
    nib = pytest.importorskip("nibabel")
    affine = np.eye(4)
    img = nib.Nifti1Image(arr.astype(np.int16), affine)
    p = tmp_path / name
    nib.save(img, str(p))
    return p


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_render_static_tools_registered():
    # Stale since the 2026-08-05 "deterministic and fail closed" change added
    # sv_render_molecule_thumbnail as the eighth tool; the old name pinned a
    # count, so it could only ever redden on a legitimate addition.
    names = {t.name for t in rs.TOOLS}
    assert names == {
        "sv_render_mesh_thumbnail",
        "sv_render_mesh_view_sheet",
        "sv_render_nifti_slice",
        "sv_render_nifti_overlay",
        "sv_render_nifti_mip",
        "sv_render_pointcloud_thumbnail",
        "sv_render_molecule_thumbnail",
        "sv_render_qa_record_summary",
    }
    for t in rs.TOOLS:
        assert t.category == "D"
        assert callable(t.handler)
        assert isinstance(t.input_schema, dict)


# ---------------------------------------------------------------------------
# Envelope: inline vs URL
# ---------------------------------------------------------------------------


def _tiny_png_bytes() -> bytes:
    """Build a real 1x1 PNG via PIL so width/height parsing is exercised."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _padded_png_bytes(min_size: int) -> bytes:
    """Build a PNG larger than INLINE_THRESHOLD by writing many pixels.

    We can't pad ANY bytes onto the end (PIL won't read it back), but a real
    high-res image easily exceeds 300 KB.
    """
    from PIL import Image

    side = 1024
    arr = (np.random.default_rng(0).integers(0, 255, size=(side, side, 3))).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", optimize=False)
    raw = buf.getvalue()
    assert len(raw) >= min_size, f"padded png too small: {len(raw)} < {min_size}"
    return raw


def test_envelope_inline_vs_url(tmp_path):
    small = _tiny_png_bytes()
    env_small = rs._build_image_envelope(small, cache_sha="sha-small", extras={})
    assert "image_png_base64" in env_small
    assert "image_url" not in env_small
    assert env_small["width"] == 1 and env_small["height"] == 1
    assert env_small["content_type"] == "image/png"
    # Round-trip the base64
    decoded = base64.b64decode(env_small["image_png_base64"])
    assert decoded == small

    big = _padded_png_bytes(rs.INLINE_THRESHOLD_BYTES + 1)
    env_big = rs._build_image_envelope(big, cache_sha="sha-big", extras={})
    assert "image_url" in env_big
    assert "image_png_base64" not in env_big
    cache_file = Path(env_big["cache_path"])
    assert cache_file.exists()
    assert cache_file.read_bytes() == big


# ---------------------------------------------------------------------------
# Cache hit: second call must NOT re-render
# ---------------------------------------------------------------------------


def test_render_cache_hit(tmp_path):
    arr = np.zeros((8, 8, 8), dtype=np.int16)
    arr[2:5, 2:5, 3:6] = 200
    nifti_path = _make_nifti(tmp_path, arr, "slice_cache.nii.gz")

    args = {"path": str(nifti_path), "axis": "axial", "index": 4, "colormap": "gray"}
    out1 = rs._handle_render_nifti_slice(args)
    assert "error" not in out1
    sha = out1["cache_sha"]

    # First call may have inlined; ensure cache file exists for hit test
    cache_file = rs.render_cache_dir() / f"{sha}.png"
    if not cache_file.exists():
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(base64.b64decode(out1["image_png_base64"]))

    # Second call: monkeypatch nibabel loader to raise — if rendering reruns we crash.
    with patch.object(rs_common, "load_nifti_array") as fake_load:
        fake_load.side_effect = AssertionError("should not be called on cache hit")
        out2 = rs._handle_render_nifti_slice(args)
    assert "error" not in out2
    assert out2["cache_sha"] == sha


# ---------------------------------------------------------------------------
# Axis convention contract (M3D-RefSeg load-bearing)
# ---------------------------------------------------------------------------


def test_nifti_overlay_axis_convention(tmp_path):
    """16^3 volume with labeled cube at indices [4:8, 4:8, 4:8].

    For axis='axial' (axis=2) at index=5, arr[:, :, 5] contains a 4x4 region
    where mask==label, so mask_pixel_count must equal 16.
    """
    base = np.zeros((16, 16, 16), dtype=np.int16)
    base[4:8, 4:8, 4:8] = 100
    mask = np.zeros((16, 16, 16), dtype=np.int16)
    mask[4:8, 4:8, 4:8] = 2

    nifti_path = _make_nifti(tmp_path, base, "ct.nii.gz")
    mask_path = _make_nifti(tmp_path, mask, "mask.nii.gz")

    out = rs._handle_render_nifti_overlay(
        {
            "nifti_path": str(nifti_path),
            "mask_path": str(mask_path),
            "axis": "axial",
            "index": 5,
            "mask_id": 2,
            "alpha": 0.5,
        }
    )
    assert "error" not in out, out
    assert out["mask_pixel_count"] == 16
    assert out["mask_ids_present"] == [2]
    assert out["slice_index"] == 5


def test_nifti_overlay_rejects_mutually_exclusive_index_and_fraction(tmp_path):
    base = np.zeros((4, 4, 4), dtype=np.int16)
    mask = np.zeros((4, 4, 4), dtype=np.int16)
    base_path = _make_nifti(tmp_path, base, "base.nii.gz")
    mask_path = _make_nifti(tmp_path, mask, "mask.nii.gz")
    out = rs._handle_render_nifti_overlay(
        {
            "nifti_path": str(base_path),
            "mask_path": str(mask_path),
            "axis": "axial",
            "index": 1,
            "fraction": 0.5,
        }
    )
    assert "error" in out
    assert "mutually exclusive" in out["error"]


def test_nifti_overlay_shape_mismatch_returns_error(tmp_path):
    base = np.zeros((8, 8, 8), dtype=np.int16)
    mask = np.zeros((4, 4, 4), dtype=np.int16)
    base_path = _make_nifti(tmp_path, base, "base.nii.gz")
    mask_path = _make_nifti(tmp_path, mask, "mask.nii.gz")
    out = rs._handle_render_nifti_overlay(
        {
            "nifti_path": str(base_path),
            "mask_path": str(mask_path),
            "axis": "axial",
            "fraction": 0.5,
        }
    )
    assert "error" in out
    assert "shape mismatch" in out["error"]


# ---------------------------------------------------------------------------
# NIfTI slice / MIP basic sanity
# ---------------------------------------------------------------------------


def test_nifti_slice_basic(tmp_path):
    arr = np.zeros((6, 8, 10), dtype=np.int16)
    arr[..., 3] = 1000
    path = _make_nifti(tmp_path, arr, "slice_basic.nii.gz")
    out = rs._handle_render_nifti_slice(
        {"path": str(path), "axis": "axial", "index": 3, "colormap": "gray"}
    )
    assert "error" not in out
    assert out["slice_index"] == 3
    assert out["axis"] == "axial"
    assert "image_png_base64" in out or "image_url" in out


def test_nifti_slice_fraction_default(tmp_path):
    arr = np.zeros((4, 4, 10), dtype=np.int16)
    path = _make_nifti(tmp_path, arr, "frac.nii.gz")
    out = rs._handle_render_nifti_slice(
        {"path": str(path), "axis": "axial", "fraction": 0.5}
    )
    assert "error" not in out
    # 0.5 * (10-1) = 4.5 -> rounds to 4 (round-half-to-even on .5)
    assert out["slice_index"] in {4, 5}


def test_nifti_slice_rejects_bad_axis(tmp_path):
    arr = np.zeros((4, 4, 4), dtype=np.int16)
    path = _make_nifti(tmp_path, arr, "ax.nii.gz")
    out = rs._handle_render_nifti_slice({"path": str(path), "axis": "z"})
    assert "error" in out


def test_nifti_slice_rejects_missing_path():
    out = rs._handle_render_nifti_slice({"axis": "axial"})
    assert "error" in out


def test_nifti_mip_basic(tmp_path):
    arr = np.zeros((4, 4, 4), dtype=np.int16)
    arr[1, 1, 1] = 500
    path = _make_nifti(tmp_path, arr, "mip.nii.gz")
    out = rs._handle_render_nifti_mip(
        {"path": str(path), "axis": "axial", "projection": "max"}
    )
    assert "error" not in out
    assert out["projection"] == "max"


def test_nifti_mip_rejects_bad_projection(tmp_path):
    arr = np.zeros((4, 4, 4), dtype=np.int16)
    path = _make_nifti(tmp_path, arr, "mip2.nii.gz")
    out = rs._handle_render_nifti_mip(
        {"path": str(path), "axis": "axial", "projection": "median"}
    )
    assert "error" in out


# ---------------------------------------------------------------------------
# Mesh rendering — mocked pyrender + trimesh
# ---------------------------------------------------------------------------


def _install_mesh_mocks(monkeypatch, tmp_path: Path):
    """Patch trimesh + pyrender in render_static so no real GL is needed."""
    fake_mesh = MagicMock()
    fake_mesh.vertices = np.array([[0, 0, 0], [1, 1, 1]], dtype=float)
    fake_mesh.bounds = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])

    fake_trimesh = MagicMock()
    fake_trimesh.load = MagicMock(return_value=fake_mesh)

    fake_pyrender = MagicMock()
    fake_scene = MagicMock()
    fake_pyrender.Scene = MagicMock(return_value=fake_scene)
    fake_pyrender.Mesh.from_trimesh = MagicMock(return_value=MagicMock())
    fake_pyrender.PerspectiveCamera = MagicMock(return_value=MagicMock())
    fake_pyrender.DirectionalLight = MagicMock(return_value=MagicMock())
    fake_renderer = MagicMock()
    fake_renderer.render = MagicMock(
        return_value=(
            np.full((32, 32, 3), fill_value=80, dtype=np.uint8),
            np.zeros((32, 32), dtype=np.float32),
        )
    )
    fake_renderer.delete = MagicMock()
    fake_pyrender.OffscreenRenderer = MagicMock(return_value=fake_renderer)

    import_map = {"trimesh": fake_trimesh, "pyrender": fake_pyrender}
    real_import = rs_common.try_import

    def fake_import(name: str):
        if name in import_map:
            return import_map[name]
        return real_import(name)

    monkeypatch.setattr(rs_common, "try_import", fake_import)
    mesh_path = tmp_path / "fake_mesh.ply"
    mesh_path.write_bytes(b"PLY-fake")
    return mesh_path, fake_pyrender


def test_mesh_thumbnail_with_mocked_gl(tmp_path, monkeypatch):
    mesh_path, _ = _install_mesh_mocks(monkeypatch, tmp_path)
    out = rs._handle_render_mesh_thumbnail({"path": str(mesh_path), "view": "iso"})
    assert "error" not in out, out
    assert "image_png_base64" in out or "image_url" in out
    assert out["camera_used"]["fov_deg"] == 45.0
    assert out["view"] == "iso"


def test_mesh_thumbnail_rejects_bad_view(tmp_path):
    p = tmp_path / "x.ply"
    p.write_bytes(b"x")
    out = rs._handle_render_mesh_thumbnail({"path": str(p), "view": "weird"})
    assert "error" in out


def test_mesh_view_sheet_tile_count_and_labels(tmp_path, monkeypatch):
    mesh_path, _ = _install_mesh_mocks(monkeypatch, tmp_path)
    out = rs._handle_render_mesh_view_sheet(
        {
            "path": str(mesh_path),
            "views": ["front", "back", "left", "right", "top", "iso"],
            "size_per_tile": 64,
        }
    )
    assert "error" not in out, out
    assert len(out["tiles"]) == 6
    for tile in out["tiles"]:
        assert "view" in tile and "bbox_px" in tile
        x0, y0, x1, y1 = tile["bbox_px"]
        assert x1 - x0 == 64 and y1 - y0 == 64
    assert {t["view"] for t in out["tiles"]} == {"front", "back", "left", "right", "top", "iso"}


def test_mesh_view_sheet_rejects_bad_view(tmp_path):
    p = tmp_path / "x.ply"
    p.write_bytes(b"x")
    out = rs._handle_render_mesh_view_sheet({"path": str(p), "views": ["bogus"]})
    assert "error" in out


# ---------------------------------------------------------------------------
# Dependency-missing envelopes
# ---------------------------------------------------------------------------


def _patch_missing(monkeypatch, *missing: str):
    real = rs_common.try_import

    def fake(name: str):
        if name in missing:
            return None
        return real(name)

    monkeypatch.setattr(rs_common, "try_import", fake)


def test_missing_dependency_returns_envelope(tmp_path, monkeypatch):
    p = tmp_path / "mesh.ply"
    p.write_bytes(b"PLY")
    _patch_missing(monkeypatch, "pyrender")
    out = rs._handle_render_mesh_thumbnail({"path": str(p), "view": "iso"})
    assert out == {"error": "dependency missing: pyrender"}


def test_missing_trimesh_returns_envelope(tmp_path, monkeypatch):
    p = tmp_path / "mesh.ply"
    p.write_bytes(b"PLY")
    _patch_missing(monkeypatch, "trimesh")
    out = rs._handle_render_mesh_thumbnail({"path": str(p), "view": "iso"})
    assert out == {"error": "dependency missing: trimesh"}


def test_missing_nibabel_returns_envelope(tmp_path, monkeypatch):
    p = tmp_path / "fake.nii.gz"
    p.write_bytes(b"NIFTI")
    _patch_missing(monkeypatch, "nibabel")
    out = rs._handle_render_nifti_slice({"path": str(p), "axis": "axial", "fraction": 0.5})
    assert out == {"error": "dependency missing: nibabel"}


def test_missing_laspy_returns_envelope(tmp_path, monkeypatch):
    p = tmp_path / "cloud.las"
    p.write_bytes(b"LAS")
    _patch_missing(monkeypatch, "laspy")
    out = rs._handle_render_pointcloud_thumbnail({"path": str(p), "view": "top"})
    assert out == {"error": "dependency missing: laspy"}


# ---------------------------------------------------------------------------
# QA record summary dispatcher
# ---------------------------------------------------------------------------


def _register_fake_get_matched_record(record: dict):
    from sci3d_agent.mcp.registry import ToolSpec

    def handler(args: Mapping[str, Any]) -> dict:
        qid = args.get("qid")
        if qid != record.get("qid"):
            return {"error": f"qid not found: {qid}"}
        return {"record": record}

    REGISTRY["sv_get_matched_record"] = ToolSpec(
        name="sv_get_matched_record",
        description="fake",
        input_schema={"type": "object"},
        handler=handler,
        category="A",
    )


def test_render_qa_record_summary_dispatches_mesh(tmp_path, monkeypatch):
    """ScanQA mesh -> renderer_used == sv_render_mesh_view_sheet."""
    mesh_path, _ = _install_mesh_mocks(monkeypatch, tmp_path)
    rec = {
        "qid": "scanqa-test-0",
        "asset_kind": "mesh",
        "path_repo": str(mesh_path),
        "scene_or_case_id": "scene0707_00",
        "question": "What corner has the refrigerators?",
        "mask": None,
    }
    _register_fake_get_matched_record(rec)
    out = rs._handle_render_qa_record_summary({"qid": "scanqa-test-0", "size": 128})
    assert "error" not in out, out
    assert out["renderer_used"] == "sv_render_mesh_view_sheet"
    assert out["asset_kind"] == "mesh"
    assert "caption" in out
    assert "scene0707_00" in out["caption"]


def test_render_qa_record_summary_dispatches_nifti_overlay(tmp_path):
    """M3D-RefSeg nifti with mask -> renderer_used == sv_render_nifti_overlay."""
    base = np.zeros((16, 16, 16), dtype=np.int16)
    base[4:8, 4:8, 4:8] = 100
    mask = np.zeros((16, 16, 16), dtype=np.int16)
    mask[4:8, 4:8, 4:8] = 2
    nifti_path = _make_nifti(tmp_path, base, "ct.nii.gz")
    mask_path = _make_nifti(tmp_path, mask, "mask.nii.gz")

    rec = {
        "qid": "m3d-refseg-test-0",
        "asset_kind": "nifti",
        "path_repo": str(nifti_path),
        "scene_or_case_id": "s0212",
        "question": "Identify the stoma.",
        "mask": {
            "mask_path_repo": str(mask_path),
            "mask_id": "2",
            "mask_text": "stoma",
        },
    }
    _register_fake_get_matched_record(rec)

    # Register a fake inspect handler that returns a known best_slices.axial
    from sci3d_agent.mcp.registry import ToolSpec

    def inspect_handler(args):
        return {
            "labels": [
                {
                    "id": 2,
                    "voxel_count": 64,
                    "best_slices": {"axial": 5, "coronal": 5, "sagittal": 5},
                }
            ]
        }

    REGISTRY["sv_inspect_nifti_mask_pair"] = ToolSpec(
        name="sv_inspect_nifti_mask_pair",
        description="fake",
        input_schema={"type": "object"},
        handler=inspect_handler,
        category="C",
    )

    out = rs._handle_render_qa_record_summary({"qid": "m3d-refseg-test-0", "size": 128})
    assert "error" not in out, out
    assert out["renderer_used"] == "sv_render_nifti_overlay"
    assert out["asset_kind"] == "nifti"
    assert out["mask_pixel_count"] > 0
    assert out["slice_index"] == 5


def test_render_qa_record_summary_dispatches_nifti_no_mask(tmp_path):
    arr = np.zeros((8, 8, 8), dtype=np.int16)
    nifti_path = _make_nifti(tmp_path, arr, "nomask.nii.gz")
    rec = {
        "qid": "fake-no-mask",
        "asset_kind": "nifti",
        "path_repo": str(nifti_path),
        "scene_or_case_id": "case-1",
        "question": "Whole-slice question.",
        "mask": None,
    }
    _register_fake_get_matched_record(rec)
    out = rs._handle_render_qa_record_summary({"qid": "fake-no-mask"})
    assert "error" not in out, out
    assert out["renderer_used"] == "sv_render_nifti_slice"


def test_render_qa_record_summary_unsupported_kind(tmp_path):
    rec = {
        "qid": "exotic-1",
        "asset_kind": "molecule",
        "path_repo": "x.cif",
        "question": "Q",
        "scene_or_case_id": "",
        "mask": None,
    }
    _register_fake_get_matched_record(rec)
    out = rs._handle_render_qa_record_summary({"qid": "exotic-1"})
    assert "error" in out
    assert "asset_kind not yet supported" in out["error"]


def test_render_qa_record_summary_missing_qid():
    out = rs._handle_render_qa_record_summary({})
    assert "error" in out


def test_render_qa_record_summary_no_catalog_registered():
    REGISTRY.pop("sv_get_matched_record", None)
    out = rs._handle_render_qa_record_summary({"qid": "x"})
    assert "error" in out


# ---------------------------------------------------------------------------
# Misc helpers / coverage glue
# ---------------------------------------------------------------------------


def test_hex_to_rgba_paths():
    assert rs._hex_to_rgba(None)[3] == 1.0
    assert rs._hex_to_rgba("transparent") == (0.0, 0.0, 0.0, 0.0)
    rgba = rs._hex_to_rgba("#ff8800")
    assert pytest.approx(rgba[0], abs=1e-3) == 1.0
    assert rgba[3] == 1.0
    rgba8 = rs._hex_to_rgba("#11223344")
    assert len(rgba8) == 4
    # Garbage input falls back to default dark grey
    rs._hex_to_rgba("#zzzzzz")


def test_caption_truncation():
    rec = {"question": "x" * 500, "scene_or_case_id": "scene-A"}
    cap = rs._caption_for_record(rec)
    assert cap.startswith("[scene-A]")
    assert len(cap) <= 240
    assert cap.endswith("...")


def test_coerce_mask_id():
    assert rs._coerce_mask_id(None) is None
    assert rs._coerce_mask_id("2") == 2
    assert rs._coerce_mask_id(3) == 3
    assert rs._coerce_mask_id("bogus") is None


def test_png_size_invalid():
    assert rs._png_size(b"not-a-png") == (0, 0)


def test_apply_window_with_explicit_range():
    arr = np.array([[0, 100, 200], [50, 150, 250]], dtype=np.float32)
    norm, used = rs._apply_window(arr, (0.0, 200.0))
    assert used == (0.0, 200.0)
    assert norm.dtype == np.uint8
    assert norm.min() == 0


def test_apply_window_collapses_when_lo_eq_hi():
    arr = np.array([[5.0, 5.0]], dtype=np.float32)
    _, used = rs._apply_window(arr, (5.0, 5.0))
    assert used[1] > used[0]


def test_slice_index_validation():
    arr = np.zeros((4, 4, 4))
    idx, err = rs._slice_index_from_args(arr, "axial", 100, None)
    assert idx is None and "out of range" in err
    idx, err = rs._slice_index_from_args(arr, "axial", None, 1.5)
    assert idx is None and "[0, 1]" in err
    idx, err = rs._slice_index_from_args(arr, "axial", "bad", None)
    assert idx is None and "integer" in err
    idx, err = rs._slice_index_from_args(arr, "axial", None, "bad")
    assert idx is None and "number" in err


def test_pointcloud_thumbnail_missing_path():
    out = rs._handle_render_pointcloud_thumbnail({"view": "top"})
    assert "error" in out


def test_pointcloud_thumbnail_bad_view(tmp_path):
    p = tmp_path / "x.las"
    p.write_bytes(b"x")
    out = rs._handle_render_pointcloud_thumbnail({"path": str(p), "view": "weird"})
    assert "error" in out


def _install_fake_laspy(monkeypatch, n_points: int = 100, with_rgb: bool = True):
    """Stand up a minimal fake laspy module to exercise pointcloud render."""
    rng = np.random.default_rng(0)
    fake_las = MagicMock()
    fake_las.x = rng.standard_normal(n_points)
    fake_las.y = rng.standard_normal(n_points)
    fake_las.z = rng.standard_normal(n_points)
    fake_las.intensity = rng.integers(0, 10000, size=n_points)
    fake_las.red = rng.integers(0, 65535, size=n_points)
    fake_las.green = rng.integers(0, 65535, size=n_points)
    fake_las.blue = rng.integers(0, 65535, size=n_points)
    dim_names = ["x", "y", "z", "intensity"]
    if with_rgb:
        dim_names += ["red", "green", "blue"]
    fake_las.point_format.dimension_names = dim_names

    fake_laspy = MagicMock()
    fake_laspy.read = MagicMock(return_value=fake_las)

    real = rs_common.try_import

    def fake_import(name: str):
        if name == "laspy":
            return fake_laspy
        return real(name)

    monkeypatch.setattr(rs_common, "try_import", fake_import)


@pytest.mark.parametrize("view,color_by", [("top", "height"), ("iso", "rgb"), ("front", "intensity"), ("side", "height")])
def test_pointcloud_thumbnail_views_with_fake_laspy(tmp_path, monkeypatch, view, color_by):
    pytest.importorskip("matplotlib")
    _install_fake_laspy(monkeypatch, n_points=200, with_rgb=True)
    p = tmp_path / "pc.las"
    p.write_bytes(b"fake-las")
    out = rs._handle_render_pointcloud_thumbnail(
        {"path": str(p), "view": view, "color_by": color_by, "max_points": 50, "size": 128}
    )
    assert "error" not in out, out
    assert out["points_total"] == 200
    assert out["points_rendered"] <= 50
    assert out["view"] == view


def test_pointcloud_thumbnail_empty(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    _install_fake_laspy(monkeypatch, n_points=0, with_rgb=False)
    p = tmp_path / "pc.las"
    p.write_bytes(b"x")
    out = rs._handle_render_pointcloud_thumbnail({"path": str(p), "view": "top"})
    assert "error" in out
    assert "empty" in out["error"]


def test_pointcloud_thumbnail_load_failure(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    fake_laspy = MagicMock()
    fake_laspy.read = MagicMock(side_effect=RuntimeError("boom"))
    real = rs_common.try_import

    def fake_import(name: str):
        if name == "laspy":
            return fake_laspy
        return real(name)

    monkeypatch.setattr(rs_common, "try_import", fake_import)
    p = tmp_path / "bad.las"
    p.write_bytes(b"x")
    out = rs._handle_render_pointcloud_thumbnail({"path": str(p), "view": "top"})
    assert "error" in out
    assert "laspy load failed" in out["error"]


def test_iso_projection_shape():
    xyz = np.random.default_rng(0).standard_normal((20, 3))
    out = rs._project_iso(xyz)
    assert out.shape == (20, 2)


def test_orient_for_display_2d_and_3d():
    a2 = np.zeros((3, 4), dtype=np.uint8)
    assert rs._orient_for_display(a2).shape == (4, 3)
    a3 = np.zeros((3, 4, 3), dtype=np.uint8)
    assert rs._orient_for_display(a3).shape == (4, 3, 3)


def test_apply_colormap_falls_back_when_matplotlib_missing(monkeypatch):
    _patch_missing(monkeypatch, "matplotlib")
    gray = np.array([[0, 128, 255]], dtype=np.uint8)
    out = rs._apply_colormap(gray, "viridis")
    # Falls back to grayscale triple
    assert out.shape == (1, 3, 3)
    assert (out[..., 0] == out[..., 1]).all()


def test_cache_key_is_deterministic(tmp_path):
    p = tmp_path / "file.bin"
    p.write_bytes(b"abc")
    k1 = rs._render_cache_key("tool", {"a": 1, "b": 2}, [p])
    k2 = rs._render_cache_key("tool", {"b": 2, "a": 1}, [p])
    assert k1 == k2


def test_cache_key_changes_on_mtime(tmp_path):
    p = tmp_path / "file.bin"
    p.write_bytes(b"abc")
    k1 = rs._render_cache_key("tool", {}, [p])
    p.write_bytes(b"abcd")
    # bump mtime explicitly to ensure non-equal
    import os, time

    new_t = p.stat().st_mtime + 1
    os.utime(p, (new_t, new_t))
    k2 = rs._render_cache_key("tool", {}, [p])
    assert k1 != k2


def test_resolve_path_relative():
    p = rs._resolve_path("viewer/assets.json")
    assert p.is_absolute()


def test_render_static_module_sets_egl_env():
    import os

    assert os.environ.get("PYOPENGL_PLATFORM") == "egl"


def test_ensure_headless_init_idempotent():
    rs_common._HEADLESS_INIT_DONE = False
    rs._ensure_headless_init()
    rs._ensure_headless_init()  # second call must be a no-op
    assert rs_common._HEADLESS_INIT_DONE is True
