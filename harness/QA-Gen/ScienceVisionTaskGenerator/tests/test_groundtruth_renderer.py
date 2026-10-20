"""Tests for ThumbnailRenderer."""
from __future__ import annotations

from pathlib import Path

import pytest

from sciencevision_taskgen.groundtruth.renderer import (
    RenderResult,
    ThumbnailRenderer,
)


def _make_task(thumb: str | None) -> dict:
    return {
        "task_id": "t1",
        "asset": {
            "asset_id": "a1",
            "name": "asset1",
            "thumb": thumb,
        },
    }


def test_thumbnail_renderer_returns_resolved_path(tmp_path: Path):
    thumb_dir = tmp_path / "viewer" / "thumbs"
    thumb_dir.mkdir(parents=True)
    (thumb_dir / "abc.webp").write_bytes(b"\x00fake-webp")

    renderer = ThumbnailRenderer(repo_root=tmp_path)
    result = renderer.render(_make_task("/viewer/thumbs/abc.webp"))

    assert isinstance(result, RenderResult)
    assert result.image_paths == (thumb_dir / "abc.webp",)
    assert result.media_type == "image/webp"


def test_thumbnail_renderer_returns_empty_when_thumb_missing(tmp_path: Path):
    renderer = ThumbnailRenderer(repo_root=tmp_path)
    result = renderer.render(_make_task(None))
    assert result.image_paths == ()
    assert "no thumb" in result.skip_reason.lower()


def test_thumbnail_renderer_skips_when_file_does_not_exist(tmp_path: Path):
    renderer = ThumbnailRenderer(repo_root=tmp_path)
    result = renderer.render(_make_task("/viewer/thumbs/does_not_exist.webp"))
    assert result.image_paths == ()
    assert "not on disk" in result.skip_reason.lower()


def test_thumbnail_renderer_rejects_thumb_outside_viewer(tmp_path: Path):
    renderer = ThumbnailRenderer(repo_root=tmp_path)
    result = renderer.render(_make_task("/etc/passwd"))
    assert result.image_paths == ()
    assert "rejected" in result.skip_reason.lower()


def test_thumbnail_renderer_rejects_traversal_via_dotdot(tmp_path: Path):
    """Defense-in-depth: even if startswith passes, .. must not escape thumbs/."""
    thumb_dir = tmp_path / "viewer" / "thumbs"
    thumb_dir.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("nope")

    renderer = ThumbnailRenderer(repo_root=tmp_path)
    result = renderer.render(_make_task("/viewer/thumbs/../secret.txt"))

    assert result.image_paths == ()
    assert "rejected" in result.skip_reason.lower()


def test_thumbnail_renderer_rejects_absolute_thumb_pointing_into_thumbs(tmp_path: Path):
    """A thumb that startswith /viewer/thumbs/ but resolves outside must fail."""
    thumb_dir = tmp_path / "viewer" / "thumbs"
    thumb_dir.mkdir(parents=True)

    renderer = ThumbnailRenderer(repo_root=tmp_path)
    # Even a symlink-style escape via dotdot deep inside the path
    result = renderer.render(_make_task("/viewer/thumbs/sub/../../../etc/passwd"))

    assert result.image_paths == ()
    assert "rejected" in result.skip_reason.lower()


def test_render_result_default_view_names_is_empty_tuple():
    """RenderResult() has view_names defaulting to ()."""
    r = RenderResult()
    assert r.view_names == ()


def test_thumbnail_renderer_returns_empty_view_names(tmp_path: Path):
    """ThumbnailRenderer does not populate view_names (single unlabelled thumb)."""
    thumb_dir = tmp_path / "viewer" / "thumbs"
    thumb_dir.mkdir(parents=True)
    (thumb_dir / "xyz.webp").write_bytes(b"\x00fake")

    renderer = ThumbnailRenderer(repo_root=tmp_path)
    result = renderer.render(_make_task("/viewer/thumbs/xyz.webp"))

    assert result.view_names == ()


def test_render_result_view_names_is_preserved_and_hashable(tmp_path: Path):
    """view_names tuple is preserved and the frozen dataclass remains hashable."""
    p = tmp_path / "front.webp"
    p.write_bytes(b"\x00")
    r = RenderResult(image_paths=(p,), view_names=("front",), media_type="image/webp")
    assert r.view_names == ("front",)
    # Frozen dataclass must be hashable.
    assert isinstance(hash(r), int)
