"""Renderer adapters for the groundtruth stage.

A Renderer turns a task into a RenderResult (image paths + metadata).  The
ThumbnailRenderer uses the existing per-asset WebP thumbnails that the
viewer already generated; no GL / EGL required.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class RenderResult:
    """Result returned by Renderer.render.

    Attributes:
        image_paths: Tuple of absolute Paths to rendered images.  Empty when
            no render could be produced.
        view_names: Tuple of human-readable labels, parallel to image_paths.
            Empty tuple means unlabelled / single-view (the common case).
            Reserved for future multi-view rendering; no filler currently
            inspects this field.
        media_type: MIME type of all images in image_paths.
        skip_reason: Human-readable reason image_paths is empty; "" when
            render succeeded.
    """

    image_paths: tuple[Path, ...] = ()
    view_names: tuple[str, ...] = ()
    media_type: str = ""
    skip_reason: str = ""


class Renderer(Protocol):
    def render(self, task: dict[str, Any]) -> RenderResult: ...


_VIEWER_THUMB_PREFIX = "/viewer/thumbs/"


class ThumbnailRenderer:
    """Reads ``task.asset.thumb`` (viewer-absolute URL) from the on-disk
    ``viewer/thumbs/`` directory under ``repo_root``.

    Security (defense-in-depth): only paths that both start with
    ``/viewer/thumbs/`` *and* resolve to a canonical path inside
    ``<repo_root>/viewer/thumbs/`` are accepted.  Path-traversal sequences
    such as ``/viewer/thumbs/../../etc/passwd`` are rejected even though
    they pass the prefix check.  ``_thumbs_root`` is resolved once in
    ``__init__`` so ``is_relative_to`` is a fast in-memory check.
    """

    def __init__(self, repo_root: Path) -> None:
        self._root = Path(repo_root)
        self._thumbs_root: Path = (self._root / "viewer" / "thumbs").resolve()

    def render(self, task: dict[str, Any]) -> RenderResult:
        thumb = task.get("asset", {}).get("thumb")
        if not thumb:
            return RenderResult(skip_reason="no thumb declared on asset")

        if not thumb.startswith(_VIEWER_THUMB_PREFIX):
            return RenderResult(
                skip_reason=f"rejected: thumb path outside viewer/thumbs/ ({thumb!r})"
            )

        rel = thumb.lstrip("/")
        path = (self._root / rel).resolve(strict=False)

        if not path.is_relative_to(self._thumbs_root):
            return RenderResult(
                skip_reason=f"rejected: resolved path escapes viewer/thumbs/ ({thumb!r})"
            )

        if not path.exists():
            return RenderResult(
                skip_reason=f"not on disk: {path}"
            )

        return RenderResult(
            image_paths=(path,),
            media_type="image/webp",
        )
