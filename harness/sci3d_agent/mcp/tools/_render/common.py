"""Shared infra for static renderers: dep probing, cache key, envelope build.

Axis convention (LOCKED — must match sci3d_agent/mcp/tools/inspect.py):

    sagittal = axis 0   coronal = axis 1   axial = axis 2

Picking the wrong axis is the #1 silent bug for M3D-RefSeg slice picking,
so this constant is intentionally re-exported here for the renderer
modules that consume ``sv_inspect_nifti_mask_pair.labels[*].best_slices``.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from sci3d_agent.mcp.runtime.paths import render_cache_dir, repo_root, viewer_base

logger = logging.getLogger(__name__)

INLINE_THRESHOLD_BYTES = 300 * 1024  # SPEC §5 D-conventions
AXIS_INDEX: dict[str, int] = {"sagittal": 0, "coronal": 1, "axial": 2}

_HEADLESS_INIT_DONE = False


def try_import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


_NEAR_MIN = 1e-4


def camera_near_far(distance: float) -> tuple[float, float]:
    """pyrender znear/zfar for a camera ``distance`` from its target.

    pyrender's defaults (znear=0.05, zfar=100) clip any scene whose bbox
    diagonal is below ~0.036, because both renderers place the camera at
    ``1.4 * diag``: the whole object falls inside the near plane and the render
    is black (the 11 blank CAD views Jing audited). Scale both planes to the
    scene instead.
    """
    d = max(float(distance), 1e-6)
    return max(_NEAR_MIN, d * 0.01), max(d * 100.0, 1.0)


def build_perspective_camera(pyrender_mod, *, yfov_deg: float, aspect: float, eye, target):
    """The one pyrender camera construction point, with scene-scaled planes.

    Every caller must go through here: a bare ``PerspectiveCamera(...)`` without
    znear/zfar reintroduces the near-plane clipping at small asset scales.
    """
    distance = float(
        np.linalg.norm(np.asarray(eye, dtype=float) - np.asarray(target, dtype=float))
    )
    znear, zfar = camera_near_far(distance)
    return pyrender_mod.PerspectiveCamera(
        yfov=math.radians(yfov_deg), aspectRatio=aspect, znear=znear, zfar=zfar
    )


def missing_dep(name: str) -> dict:
    return {"error": f"dependency missing: {name}"}


def resolve_path(p: str | Path) -> Path:
    raw = Path(p)
    if raw.is_absolute():
        return raw
    return repo_root() / raw


def ensure_headless_init() -> None:
    """Best-effort xvfb fallback for pyvista when EGL probe fails.

    Runs at most once per process.
    """
    global _HEADLESS_INIT_DONE
    if _HEADLESS_INIT_DONE:
        return
    _HEADLESS_INIT_DONE = True
    pv = try_import("pyvista")
    if pv is None:
        return
    try:  # pragma: no cover - GL backend dependent
        pv.start_xvfb()
    except Exception as exc:  # pragma: no cover
        logger.debug("pyvista xvfb fallback skipped: %s", exc)


def reset_headless_init_for_tests() -> None:
    """Test-only hook so the idempotency guard can be re-armed between tests."""
    global _HEADLESS_INIT_DONE
    _HEADLESS_INIT_DONE = False


def render_cache_key(tool_name: str, args: Mapping[str, Any], input_paths: Sequence[Path]) -> str:
    mtimes: list[int] = []
    for p in input_paths:
        try:
            mtimes.append(p.stat().st_mtime_ns)
        except OSError:
            mtimes.append(0)
    payload = json.dumps(
        {
            "tool": tool_name,
            "args": dict(args),
            "paths": [str(p) for p in input_paths],
            "mtimes": mtimes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def png_size(png_bytes: bytes) -> tuple[int, int]:
    if len(png_bytes) >= 24 and png_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        w = int.from_bytes(png_bytes[16:20], "big")
        h = int.from_bytes(png_bytes[20:24], "big")
        return w, h
    return 0, 0


def build_image_envelope(
    png_bytes: bytes,
    *,
    cache_sha: str,
    extras: dict | None = None,
) -> dict:
    extras = dict(extras or {})
    w, h = png_size(png_bytes)
    extras.setdefault("width", w)
    extras.setdefault("height", h)
    extras["content_type"] = "image/png"
    extras["cache_sha"] = cache_sha

    # Always persist to the render cache, regardless of size, so that
    # (a) SV_RENDER_CACHE archival (harbor's claude-code-mcp baseline mounts
    # this dir into the trial's agent/renders/) captures every screenshot,
    # not just the rare ones over INLINE_THRESHOLD_BYTES, and (b) disk-cache
    # hits (see cached_envelope_from_disk) actually work for small renders.
    out_path = render_cache_dir() / f"{cache_sha}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        tmp = out_path.with_suffix(".png.tmp")
        tmp.write_bytes(png_bytes)
        tmp.replace(out_path)
    extras["cache_path"] = str(out_path)

    if len(png_bytes) <= INLINE_THRESHOLD_BYTES:
        # Small enough: also inline as base64 so the agent sees the image
        # directly in the tool response.
        extras["image_png_base64"] = base64.b64encode(png_bytes).decode("ascii")
    else:
        extras["image_url"] = f"{viewer_base()}/renders/{cache_sha}.png"
    return extras


def cached_envelope_from_disk(cache_sha: str) -> dict | None:
    out_path = render_cache_dir() / f"{cache_sha}.png"
    if not out_path.exists():
        return None
    png_bytes = out_path.read_bytes()
    return build_image_envelope(png_bytes, cache_sha=cache_sha)


def rgba_to_png(rgba: np.ndarray) -> bytes | None:
    pil_mod = try_import("PIL")
    if pil_mod is None:
        return None
    from PIL import Image  # type: ignore

    arr = np.asarray(rgba)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        mode = "L"
    elif arr.shape[2] == 4:
        mode = "RGBA"
    else:
        mode = "RGB"
    img = Image.fromarray(arr, mode=mode)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def apply_colormap(gray: np.ndarray, name: str) -> np.ndarray:
    plt = try_import("matplotlib")
    if plt is None:
        return np.stack([gray, gray, gray], axis=-1)
    import matplotlib  # type: ignore

    try:
        cmap = matplotlib.colormaps[name]
    except (KeyError, TypeError):
        cmap = matplotlib.colormaps["gray"]
    rgba = cmap(gray.astype(np.float32) / 255.0)
    return (rgba[..., :3] * 255.0).astype(np.uint8)


def orient_for_display(img2d: np.ndarray) -> np.ndarray:
    """Flip+transpose so (row=0, col=0) renders top-left."""
    if img2d.ndim == 2:
        return np.flipud(img2d.T)
    return np.flipud(img2d.transpose(1, 0, 2))


def apply_window(arr: np.ndarray, window: Any | None) -> tuple[np.ndarray, tuple[float, float]]:
    flat = arr.astype(np.float32)
    if window is None:
        lo = float(np.nanpercentile(flat, 1.0)) if flat.size else 0.0
        hi = float(np.nanpercentile(flat, 99.0)) if flat.size else 1.0
    else:
        try:
            lo, hi = float(window[0]), float(window[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + 1.0
    clipped = np.clip(flat, lo, hi)
    norm = ((clipped - lo) / (hi - lo) * 255.0).astype(np.uint8)
    return norm, (lo, hi)


def slice_index_from_args(
    arr: np.ndarray,
    axis_name: str,
    index: Any,
    fraction: Any,
) -> tuple[int | None, str | None]:
    if index is not None and fraction is not None:
        return None, "index and fraction are mutually exclusive"
    axis = AXIS_INDEX[axis_name]
    dim = int(arr.shape[axis])
    if dim <= 0:
        return None, f"degenerate axis dim: {dim}"
    if index is not None:
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return None, "index must be an integer"
    elif fraction is not None:
        try:
            frac = float(fraction)
        except (TypeError, ValueError):
            return None, "fraction must be a number"
        if not 0.0 <= frac <= 1.0:
            return None, "fraction must be in [0, 1]"
        idx = int(round(frac * (dim - 1)))
    else:
        idx = (dim - 1) // 2
    if not 0 <= idx < dim:
        return None, f"index out of range: {idx} not in [0, {dim - 1}]"
    return idx, None


def take_slice(arr: np.ndarray, axis_name: str, idx: int) -> np.ndarray:
    axis = AXIS_INDEX[axis_name]
    if axis == 0:
        return arr[idx, :, :]
    if axis == 1:
        return arr[:, idx, :]
    return arr[:, :, idx]


def load_nifti_array(path: Path) -> tuple[np.ndarray | None, Any]:
    nib = try_import("nibabel")
    if nib is None:
        return None, missing_dep("nibabel")
    try:
        img = nib.load(str(path))
        arr = np.asarray(img.get_fdata(caching="unchanged"))
    except Exception as exc:
        return None, {"error": f"nibabel load failed: {exc}"}
    return arr, img
