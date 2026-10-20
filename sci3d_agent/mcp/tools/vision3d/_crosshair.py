"""``sv3d_set_crosshair`` handler (linked-MPR crosshair, private module).

Extracted from ``state.py`` to keep that file under the 450-line cap. The
handler aligns all three MPR planes (axial / coronal / sagittal) to a single
focus point in one call — the "look at three linked positions" capability —
and echoes each plane's resolved ``index``/``fraction`` so the agent gets the
three correlated slices without three separate ``sv3d_set_slice`` calls.

Focus point is given as exactly one of:

* ``point_world: [x, y, z]`` — world coordinates (mapped via the session
  affine inverse to a voxel);
* ``mask_id``                — uses that mask's precomputed ``best_slices``
  (per-axis indices already computed at open_scene), falling back to its
  ``centroid_world`` when ``best_slices`` is unavailable;
* ``voxel: [i, j, k]``       — a raw voxel index.

Errors surface as ``{"error": str}`` envelopes; never raises across the MCP
boundary. The session store is read once and updated via the immutable
``replace_*`` helpers, consistent with the rest of F-render-state.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools.vision3d._slice_math import (
    _AXIS_TO_NP,
    _get_nifti_shape,
    _resolve_index_for_axis,
    _world_to_voxel,
)
from sci3d_agent.mcp.tools.vision3d._store import (
    get_store,
    replace_mode,
    replace_slice,
)
from sci3d_agent.mcp.tools.vision3d._types import MaskInfo, Session, SliceState

# numpy-array dim -> axis name (inverse of _AXIS_TO_NP).
_NP_TO_AXIS = {v: k for k, v in _AXIS_TO_NP.items()}
# The plane we leave "active" on the Session after aligning all three.
_ACTIVE_AXIS = "axial"
_ACTIVE_MODE = "mpr_axial"


def _get_session(sid: str) -> Session:
    try:
        return get_store().get(sid)
    except KeyError:
        raise SVToolError(f"unknown session_id: {sid}")


def _find_mask(s: Session, mask_id: str) -> MaskInfo:
    for m in s.masks_info:
        if m.mask_id == mask_id:
            return m
    known = [m.mask_id for m in s.masks_info]
    raise SVToolError(f"unknown mask_id {mask_id!r}; available: {known}")


def _voxel_from_args(s: Session, args: Mapping[str, Any]) -> tuple[int, int, int]:
    """Resolve the focus voxel (i, j, k) from exactly one locator input."""
    point_world = args.get("point_world")
    mask_id = args.get("mask_id")
    voxel = args.get("voxel")

    provided = [k for k in ("point_world", "mask_id", "voxel")
                if args.get(k) is not None]
    if len(provided) == 0:
        raise SVToolError(
            "provide exactly one of point_world | mask_id | voxel"
        )
    if len(provided) > 1:
        raise SVToolError(
            f"provide exactly one locator, not {provided}"
        )

    if voxel is not None:
        if (not isinstance(voxel, (list, tuple)) or len(voxel) != 3
                or not all(isinstance(v, int) for v in voxel)):
            raise SVToolError("voxel must be a length-3 array of integers")
        return int(voxel[0]), int(voxel[1]), int(voxel[2])

    if mask_id is not None:
        m = _find_mask(s, str(mask_id))
        # best_slices is per-axis index already; assemble (i, j, k).
        if m.best_slices and all(a in m.best_slices for a in _AXIS_TO_NP):
            ijk = [0, 0, 0]
            for axis, np_dim in _AXIS_TO_NP.items():
                ijk[np_dim] = int(m.best_slices[axis])
            return ijk[0], ijk[1], ijk[2]
        # Fall back to the mask centroid in world space.
        return _world_to_voxel(s.affine_4x4, m.centroid_world)

    # point_world
    if (not isinstance(point_world, (list, tuple)) or len(point_world) != 3
            or not all(isinstance(v, (int, float)) for v in point_world)):
        raise SVToolError("point_world must be a length-3 array of numbers")
    pt = (float(point_world[0]), float(point_world[1]), float(point_world[2]))
    return _world_to_voxel(s.affine_4x4, pt)


def set_crosshair_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}

        s = _get_session(sid)
        if s.kind != "nifti":
            return {
                "error": (
                    f"sv3d_set_crosshair requires nifti session; "
                    f"got kind={s.kind!r}"
                )
            }

        set_mode = args.get("set_mode", False)
        if not isinstance(set_mode, bool):
            return {"error": "set_mode must be a boolean"}

        i, j, k = _voxel_from_args(s, args)
        shape = _get_nifti_shape(s.asset_path)

        # Clamp each voxel component into range, then resolve per-axis
        # index/fraction so the three planes are all aligned to this point.
        raw = {"sagittal": i, "coronal": j, "axial": k}
        planes: dict[str, dict[str, float]] = {}
        for axis, np_dim in _AXIS_TO_NP.items():
            extent = shape[np_dim]
            idx = max(0, min(raw[axis], extent - 1))
            r_index, r_fraction = _resolve_index_for_axis(
                s.asset_path, axis, idx, None
            )
            planes[axis] = {"index": r_index, "fraction": r_fraction}

        # Persist the active plane on the Session (default axial), preserving
        # the current window/level. The other planes are echoed in the result.
        active = planes[_ACTIVE_AXIS]
        new_slice = SliceState(
            axis=_ACTIVE_AXIS,  # type: ignore[arg-type]
            index=int(active["index"]),
            fraction=float(active["fraction"]),
            window=s.slice.window,
            level=s.slice.level,
        )
        replace_slice(sid, new_slice)

        applied_mode: Optional[str] = None
        if set_mode:
            replace_mode(sid, _ACTIVE_MODE)  # type: ignore[arg-type]
            applied_mode = _ACTIVE_MODE

        return {
            "voxel": [i, j, k],
            "active_axis": _ACTIVE_AXIS,
            "planes": planes,
            "mode": applied_mode,
        }
    except SVToolError as exc:
        return {"error": str(exc)}
