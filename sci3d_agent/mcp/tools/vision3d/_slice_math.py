"""Slice / clip-plane math helpers for F-render-state (private module).

Pure, behavior-preserving helpers extracted from ``state.py``:

* ``_get_nifti_shape``       — resolve volume shape via the Phase-1/C inspector.
* ``_resolve_index_for_axis`` — MPR index<->fraction resolution.
* ``_window_level_from_args`` — partial window-width / level resolution.
* ``_as_xyz`` / ``_normalize`` — clip-plane vector validation/normalization.

This module imports from ``._types`` and the global ``REGISTRY`` only; it must
NOT import from ``state`` (would create a circular import).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from sci3d_agent.mcp.registry import REGISTRY
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools.vision3d._types import SliceState

# Axis-to-numpy mapping (LOCKED from Phase 1/C). The volume is indexed
# `arr[sagittal_idx, coronal_idx, axial_idx]` after nibabel canonicalization,
# so axis names map to numpy dims as follows:
_AXIS_TO_NP: dict[str, int] = {"sagittal": 0, "coronal": 1, "axial": 2}

# Per-asset volume shape cache; keyed by absolute asset_path. We use the
# Phase-1/C ``sv_inspect_nifti`` inspector (already file-mtime cached) as the
# source of truth for shape, so the state module itself stores nothing on
# Session and ``_types.py`` stays untouched.
_SHAPE_CACHE: dict[str, tuple[int, int, int]] = {}


def _reset_shape_cache() -> None:
    """Test-only: clear the per-asset shape cache."""
    _SHAPE_CACHE.clear()


def _get_nifti_shape(asset_path: str) -> tuple[int, int, int]:
    """Resolve volume shape via the Phase-1/C inspector, cached per asset."""
    cached = _SHAPE_CACHE.get(asset_path)
    if cached is not None:
        return cached
    spec = REGISTRY.get("sv_inspect_nifti")
    if spec is None:
        raise SVToolError("sv_inspect_nifti tool not registered")
    out = spec.handler({"path": asset_path})
    if "error" in out:
        raise SVToolError(f"sv_inspect_nifti failed: {out['error']}")
    shape_raw = out.get("shape")
    if not isinstance(shape_raw, (list, tuple)) or len(shape_raw) != 3:
        raise SVToolError(f"unexpected nifti shape: {shape_raw!r}")
    shape: tuple[int, int, int] = (
        int(shape_raw[0]),
        int(shape_raw[1]),
        int(shape_raw[2]),
    )
    _SHAPE_CACHE[asset_path] = shape
    return shape


def _resolve_index_for_axis(
    asset_path: str, axis: str, index: Optional[int], fraction: Optional[float]
) -> tuple[int, float]:
    """Return (index, fraction) for the requested axis on the given volume.

    - If ``index`` is given, derive ``fraction = index / (extent - 1)``.
    - If ``fraction`` is given, derive ``index`` via
      ``int(round(fraction * (extent - 1)))`` so 0.0 -> 0 and 1.0 -> last.
    """
    shape = _get_nifti_shape(asset_path)
    np_axis = _AXIS_TO_NP[axis]
    extent = shape[np_axis]
    if extent < 1:
        raise SVToolError(f"volume has zero extent along {axis} axis")
    last = extent - 1
    if index is not None:
        if index < 0 or index > last:
            raise SVToolError(
                f"index {index} out of range [0, {last}] for axis {axis}"
            )
        frac = 0.0 if last == 0 else index / last
        return int(index), float(frac)
    assert fraction is not None
    idx = int(round(fraction * last))
    return idx, float(fraction)


def _window_level_from_args(
    current: SliceState,
    window_arg: Optional[float],
    level_arg: Optional[float],
) -> tuple[tuple[float, float], float]:
    """Resolve (window_lo_hi, level) given partial window/level inputs.

    ``window_arg`` is interpreted as **window width** (high - low); the
    stored ``window`` tuple is ``(low, high)`` centered on ``level``.

    - Both provided: low=level-w/2, high=level+w/2.
    - Only ``window`` (width): keep current center (level), recompute range.
    - Only ``level``: keep current width, recenter at new level.
    - Neither: return current unchanged.
    """
    if window_arg is None and level_arg is None:
        return current.window, current.level

    cur_lo, cur_hi = current.window
    cur_width = cur_hi - cur_lo
    cur_level = current.level

    new_level = float(level_arg) if level_arg is not None else cur_level
    new_width = float(window_arg) if window_arg is not None else cur_width

    if new_width < 0:
        raise SVToolError(f"window width must be >= 0; got {new_width}")

    half = new_width / 2.0
    return (new_level - half, new_level + half), new_level


def _as_xyz(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SVToolError(f"{name} must be a length-3 array of numbers")
    if not all(isinstance(v, (int, float)) for v in value):
        raise SVToolError(f"{name} must contain only numbers")
    return float(value[0]), float(value[1]), float(value[2])


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n == 0.0:
        raise SVToolError("normal must not be the zero vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def _world_to_voxel(
    affine_4x4: Optional[tuple[tuple[float, ...], ...]],
    point_world: tuple[float, float, float],
) -> tuple[int, int, int]:
    """Map a world-space point to nearest integer voxel index (i, j, k).

    ``affine_4x4`` is the voxel->world transform stored on the Session (the
    same world frame the C-inspector reports centroid/bbox in). We invert it
    and round to the nearest voxel. Raises ``SVToolError`` when the affine is
    missing or singular.
    """
    if affine_4x4 is None:
        raise SVToolError(
            "session has no affine; cannot map world point to voxel "
            "(provide voxel=[i,j,k] instead)"
        )
    import numpy as np

    aff = np.asarray(affine_4x4, dtype=float)
    if aff.shape != (4, 4):
        raise SVToolError(f"affine must be 4x4; got shape {tuple(aff.shape)}")
    try:
        inv = np.linalg.inv(aff)
    except np.linalg.LinAlgError as exc:  # pragma: no cover - defensive
        raise SVToolError(f"affine is singular, cannot invert: {exc}")
    homog = np.array(
        [point_world[0], point_world[1], point_world[2], 1.0], dtype=float
    )
    vox = inv @ homog
    return (
        int(round(float(vox[0]))),
        int(round(float(vox[1]))),
        int(round(float(vox[2]))),
    )
