"""Camera + state-echo helpers for the F-snapshot tools.

Pure functions extracted from ``snapshot.py``: they read the immutable
``Session`` and derive cameras / state-echo dicts without mutation. This
module must NOT import from ``_snapshot_render`` (one-way dependency).
"""

from __future__ import annotations

import math
import random
from typing import Any

from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools._render import common as _common
from sci3d_agent.mcp.tools.vision3d._types import (
    AxisUp,
    CameraState,
    Session,
)

Vec3 = tuple[float, float, float]

_FIT_BBOX_FACTOR = 1.4


# ---------------------------------------------------------------------------
# State-echo helpers
# ---------------------------------------------------------------------------


def _camera_dict(cam: CameraState) -> dict[str, Any]:
    return {
        "position": list(cam.position),
        "target": list(cam.target),
        "up": list(cam.up),
        "fov_deg": cam.fov_deg,
    }


def _slice_dict(s: Session) -> dict[str, Any]:
    return {
        "axis": s.slice.axis,
        "index": s.slice.index,
        "fraction": s.slice.fraction,
        "window": [s.slice.window[0], s.slice.window[1]],
        "level": s.slice.level,
    }


def _mask_state_dict(s: Session) -> dict[str, Any]:
    return {
        "visible": s.mask_state.visible,
        "mask_id": s.mask_state.mask_id,
        "colormap": s.mask_state.colormap,
        "alpha": s.mask_state.alpha,
    }


def _base_echo(s: Session, *, width: int, height: int) -> dict[str, Any]:
    """Common reasoning-state echo emitted with every render result."""
    echo: dict[str, Any] = {
        "session_id": s.session_id,
        "kind": s.kind,
        "mode": s.mode,
        "camera": _camera_dict(s.camera),
        "mask_state": _mask_state_dict(s),
        "width": width,
        "height": height,
    }
    if s.kind == "nifti":
        echo["slice"] = _slice_dict(s)
    return echo


# ---------------------------------------------------------------------------
# Canonical view math (pure: derives a one-off camera, no session mutation)
# ---------------------------------------------------------------------------


def _bbox_center(s: Session) -> Vec3:
    (x0, y0, z0), (x1, y1, z1) = s.bbox
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0)


def _bbox_diag(s: Session) -> float:
    (x0, y0, z0), (x1, y1, z1) = s.bbox
    d = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
    return d or 1.0


def _canonical_offset(axis_up: AxisUp, view: str) -> tuple[Vec3, Vec3]:
    """Unit-offset + up vector for a canonical view. Mirrors camera.py."""
    iso_unit = 1.0 / math.sqrt(3.0)
    if axis_up in ("Z", "RAS"):
        table: dict[str, tuple[Vec3, Vec3]] = {
            "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
            "back": ((0.0, +1.0, 0.0), (0.0, 0.0, 1.0)),
            "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            "right": ((+1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            "top": ((0.0, 0.0, +1.0), (0.0, 1.0, 0.0)),
            "bottom": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
            "iso": ((+iso_unit, -iso_unit, +iso_unit), (0.0, 0.0, 1.0)),
        }
    elif axis_up == "Y":
        table = {
            "front": ((0.0, 0.0, +1.0), (0.0, 1.0, 0.0)),
            "back": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
            "left": ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            "right": ((+1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            "top": ((0.0, +1.0, 0.0), (0.0, 0.0, -1.0)),
            "bottom": ((0.0, -1.0, 0.0), (0.0, 0.0, +1.0)),
            "iso": ((+iso_unit, +iso_unit, +iso_unit), (0.0, 1.0, 0.0)),
        }
    else:  # axis_up == "X"
        table = {
            "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
            "back": ((0.0, +1.0, 0.0), (1.0, 0.0, 0.0)),
            "left": ((0.0, 0.0, -1.0), (1.0, 0.0, 0.0)),
            "right": ((0.0, 0.0, +1.0), (1.0, 0.0, 0.0)),
            "top": ((+1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            "bottom": ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            "iso": ((+iso_unit, +iso_unit, +iso_unit), (1.0, 0.0, 0.0)),
        }
    if view not in table:
        raise SVToolError(f"unsupported view: {view!r}")
    return table[view]


def _canonical_camera(session: Session, view: str) -> CameraState:
    """Pure function: compute camera for a canonical view (no mutation)."""
    center = _bbox_center(session)
    diag = _bbox_diag(session)
    offset_unit, up = _canonical_offset(session.axis_up, view)
    distance = diag * _FIT_BBOX_FACTOR
    pos = (
        center[0] + offset_unit[0] * distance,
        center[1] + offset_unit[1] * distance,
        center[2] + offset_unit[2] * distance,
    )
    return CameraState(position=pos, target=center, up=up, fov_deg=session.camera.fov_deg)


def _seed_rng(session: Session) -> None:
    """Pin the global RNG so any stochastic primitive step is deterministic."""
    random.seed(session.rng_seed)
    np = _common.try_import("numpy")
    if np is not None:
        try:
            np.random.seed(session.rng_seed)
        except Exception:  # pragma: no cover - numpy is a hard dep elsewhere
            pass
