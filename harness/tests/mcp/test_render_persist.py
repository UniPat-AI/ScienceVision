"""Regression test: build_image_envelope must ALWAYS persist the PNG to disk.

Root cause fixed here: small renders (well under INLINE_THRESHOLD_BYTES) used
to take an early return that inlined the PNG as base64 and never touched
disk. That broke harbor's claude-code-mcp baseline screenshot archival
(SV_RENDER_CACHE mounts the cache dir into the trial's agent/renders/, and
the collector globs agent/renders/*.png) and meant disk-cache hits
(cached_envelope_from_disk) never fired for small renders.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from sci3d_agent.mcp.tools._render import common as rs_common


@pytest.fixture(autouse=True)
def _isolated_render_cache(tmp_path, monkeypatch):
    """Redirect render cache to a hermetic tmp dir for every test in this file."""
    fresh = tmp_path / "renders"
    monkeypatch.setenv("SV_RENDER_CACHE", str(fresh))
    yield fresh


def _tiny_png_bytes() -> bytes:
    """A real, well-under-300KB PNG (PIL round-trips it cleanly)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _big_png_bytes(min_size: int) -> bytes:
    """A real PNG comfortably larger than min_size bytes."""
    from PIL import Image

    side = 1024
    arr = (np.random.default_rng(1).integers(0, 255, size=(side, side, 3))).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", optimize=False)
    raw = buf.getvalue()
    assert len(raw) >= min_size, f"synthetic png too small: {len(raw)} < {min_size}"
    return raw


def test_small_png_is_persisted_and_inlined():
    """A small (<= INLINE_THRESHOLD_BYTES) render must land on disk AND inline."""
    png_bytes = _tiny_png_bytes()
    assert len(png_bytes) <= rs_common.INLINE_THRESHOLD_BYTES

    cache_sha = "small-regression-sha"
    envelope = rs_common.build_image_envelope(png_bytes, cache_sha=cache_sha)

    out_path = rs_common.render_cache_dir() / f"{cache_sha}.png"
    assert out_path.exists(), "small render must always be written to the render cache"
    assert out_path.read_bytes() == png_bytes

    assert envelope["cache_path"] == str(out_path)
    assert "image_png_base64" in envelope
    assert "image_url" not in envelope

    import base64

    assert base64.b64decode(envelope["image_png_base64"]) == png_bytes


def test_large_png_is_persisted_and_url_only():
    """A large (> INLINE_THRESHOLD_BYTES) render must land on disk with a URL, no base64."""
    png_bytes = _big_png_bytes(rs_common.INLINE_THRESHOLD_BYTES + 1)
    assert len(png_bytes) > rs_common.INLINE_THRESHOLD_BYTES

    cache_sha = "large-regression-sha"
    envelope = rs_common.build_image_envelope(png_bytes, cache_sha=cache_sha)

    out_path = rs_common.render_cache_dir() / f"{cache_sha}.png"
    assert out_path.exists()
    assert out_path.read_bytes() == png_bytes

    assert envelope["cache_path"] == str(out_path)
    assert "image_url" in envelope
    assert "image_png_base64" not in envelope


def test_small_png_persisted_via_lowered_threshold(monkeypatch):
    """Same large-render contract, exercised via a monkeypatched tiny threshold.

    Belt-and-suspenders per the brief: constructing a >300KB PNG is a bit
    heavy, so also verify the branch logic with a small PNG whose size is
    artificially pushed above a tiny INLINE_THRESHOLD_BYTES.
    """
    monkeypatch.setattr(rs_common, "INLINE_THRESHOLD_BYTES", 4)

    png_bytes = _tiny_png_bytes()
    assert len(png_bytes) > rs_common.INLINE_THRESHOLD_BYTES

    cache_sha = "lowered-threshold-sha"
    envelope = rs_common.build_image_envelope(png_bytes, cache_sha=cache_sha)

    out_path = rs_common.render_cache_dir() / f"{cache_sha}.png"
    assert out_path.exists()
    assert out_path.read_bytes() == png_bytes

    assert envelope["cache_path"] == str(out_path)
    assert "image_url" in envelope
    assert "image_png_base64" not in envelope


def test_existing_cache_file_is_not_overwritten(tmp_path):
    """The `if not out_path.exists()` skip must be preserved (idempotent cache)."""
    cache_sha = "preexisting-sha"
    render_dir = rs_common.render_cache_dir()
    render_dir.mkdir(parents=True, exist_ok=True)
    out_path = render_dir / f"{cache_sha}.png"
    sentinel = b"already-here"
    out_path.write_bytes(sentinel)

    envelope = rs_common.build_image_envelope(_tiny_png_bytes(), cache_sha=cache_sha)

    assert out_path.read_bytes() == sentinel
    assert envelope["cache_path"] == str(out_path)
