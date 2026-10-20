"""F-render-state tools (Phase 3.2 / F-render-state).

Five render-state mutators on top of the locked Phase 3.1 contract:

* ``sv3d_set_render_mode`` — switch render mode (kind-aware enum gate).
* ``sv3d_set_opacity``     — per-layer alpha (base | mask | clip_cap).
* ``sv3d_toggle_mask``     — overlay visibility/id/colormap/alpha (partial update).
* ``sv3d_set_slice``       — MPR slice scrub (axis + index|fraction + window/level).
* ``sv3d_set_clip_plane``  — cutaway plane (enable + origin + unit normal).

All handlers are pure with respect to the session store: read the current
Session via ``get_store().get(sid)``, build a new frozen sub-state via
``dataclasses.replace`` (or a fresh dataclass), and call the matching
``replace_*`` helper. Errors surface as ``{"error": str}`` envelopes,
never raises across the MCP boundary.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools.vision3d._schemas import (
    _AXES,
    _COLORMAPS,
    _LAYERS,
    _MESH_MODES,
    _MODES,
    _NIFTI_MODES,
    _SET_CLIP_PLANE_SCHEMA,
    _SET_CROSSHAIR_SCHEMA,
    _SET_MODE_SCHEMA,
    _SET_OPACITY_SCHEMA,
    _SET_SLICE_SCHEMA,
    _TOGGLE_MASK_SCHEMA,
)
from sci3d_agent.mcp.tools.vision3d._crosshair import (
    set_crosshair_handler as _set_crosshair_handler,
)
from sci3d_agent.mcp.tools.vision3d._slice_math import (
    _AXIS_TO_NP,
    _as_xyz,
    _get_nifti_shape,
    _normalize,
    _reset_shape_cache,
    _resolve_index_for_axis,
    _window_level_from_args,
)
from sci3d_agent.mcp.tools.vision3d._store import (
    get_store,
    replace_clip_plane,
    replace_mask_state,
    replace_mode,
    replace_opacity,
    replace_slice,
)
from sci3d_agent.mcp.tools.vision3d._types import (
    ClipPlaneState,
    MaskState,
    Session,
    SliceState,
)

# Re-exports keep these importable from this module for tests/back-compat:
# _AXIS_TO_NP, _as_xyz, _get_nifti_shape, _normalize, _reset_shape_cache,
# _resolve_index_for_axis, _window_level_from_args (from ._slice_math);
# enum tuples + *_SCHEMA dicts (from ._schemas).


def _get_session(sid: str) -> Session:
    try:
        return get_store().get(sid)
    except KeyError:
        raise SVToolError(f"unknown session_id: {sid}")


# --- sv3d_set_render_mode -------------------------------------------------


def _set_render_mode_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        mode = args.get("mode")
        if mode not in _MODES:
            return {"error": f"mode must be one of {list(_MODES)}; got {mode!r}"}

        s = _get_session(sid)
        if s.kind == "mesh" and mode not in _MESH_MODES:
            return {
                "error": (
                    f"mode {mode!r} is not valid for mesh session; "
                    f"allowed: {sorted(_MESH_MODES)}"
                )
            }
        if s.kind == "nifti" and mode not in _NIFTI_MODES:
            return {
                "error": (
                    f"mode {mode!r} is not valid for nifti session; "
                    f"allowed: {sorted(_NIFTI_MODES)}"
                )
            }

        # point_size / shading are accepted in the input schema but the
        # frozen Session has no field for them; ignore silently.
        replace_mode(sid, mode)  # type: ignore[arg-type]
        return {"mode": mode, "applied": True}
    except SVToolError as exc:
        return {"error": str(exc)}


# --- sv3d_set_opacity -----------------------------------------------------


def _set_opacity_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        layer = args.get("layer")
        if layer not in _LAYERS:
            return {"error": f"layer must be one of {list(_LAYERS)}; got {layer!r}"}
        alpha_raw = args.get("alpha")
        if not isinstance(alpha_raw, (int, float)):
            return {"error": "alpha must be a number in [0, 1]"}
        alpha = float(alpha_raw)
        if not (0.0 <= alpha <= 1.0):
            return {"error": f"alpha must be in [0, 1]; got {alpha}"}

        s = _get_session(sid)
        new_opacity = replace(s.opacity, **{layer: alpha})
        replace_opacity(sid, new_opacity)
        return {"layer": layer, "alpha": alpha}
    except SVToolError as exc:
        return {"error": str(exc)}


# --- sv3d_toggle_mask -----------------------------------------------------


def _toggle_mask_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        s = _get_session(sid)

        visible = args.get("visible")
        mask_id = args.get("mask_id")
        colormap = args.get("colormap")
        alpha_raw = args.get("alpha")

        if mask_id is not None:
            if not s.masks_info:
                return {
                    "error": (
                        "session has no masks; cannot set mask_id "
                        f"(got {mask_id!r})"
                    )
                }
            known = [m.mask_id for m in s.masks_info]
            if mask_id not in known:
                return {
                    "error": (
                        f"unknown mask_id {mask_id!r}; available: {known}"
                    )
                }

        if colormap is not None and colormap not in _COLORMAPS:
            return {
                "error": (
                    f"colormap must be one of {list(_COLORMAPS)}; got {colormap!r}"
                )
            }

        alpha: Optional[float] = None
        if alpha_raw is not None:
            if not isinstance(alpha_raw, (int, float)):
                return {"error": "alpha must be a number in [0, 1]"}
            alpha = float(alpha_raw)
            if not (0.0 <= alpha <= 1.0):
                return {"error": f"alpha must be in [0, 1]; got {alpha}"}

        if visible is not None and not isinstance(visible, bool):
            return {"error": "visible must be a boolean"}

        updates: dict[str, Any] = {}
        if visible is not None:
            updates["visible"] = visible
        if mask_id is not None:
            updates["mask_id"] = mask_id
        if colormap is not None:
            updates["colormap"] = colormap
        if alpha is not None:
            updates["alpha"] = alpha

        new_ms: MaskState = replace(s.mask_state, **updates) if updates else s.mask_state
        replace_mask_state(sid, new_ms)
        return {
            "visible": new_ms.visible,
            "mask_id": new_ms.mask_id,
            "colormap": new_ms.colormap,
            "alpha": new_ms.alpha,
        }
    except SVToolError as exc:
        return {"error": str(exc)}


# --- sv3d_set_slice -------------------------------------------------------


def _set_slice_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        axis = args.get("axis")
        if axis not in _AXES:
            return {"error": f"axis must be one of {list(_AXES)}; got {axis!r}"}

        s = _get_session(sid)
        if s.kind != "nifti":
            return {
                "error": (
                    f"sv3d_set_slice requires nifti session; got kind={s.kind!r}"
                )
            }

        index = args.get("index")
        fraction = args.get("fraction")

        if index is not None and fraction is not None:
            return {"error": "provide exactly one of index or fraction, not both"}

        if index is not None and not isinstance(index, int):
            return {"error": "index must be an integer"}
        if fraction is not None:
            if not isinstance(fraction, (int, float)):
                return {"error": "fraction must be a number in [0, 1]"}
            fraction = float(fraction)
            if not (0.0 <= fraction <= 1.0):
                return {"error": f"fraction must be in [0, 1]; got {fraction}"}

        # Keep current index/fraction if neither supplied; else resolve.
        if index is None and fraction is None:
            new_index = s.slice.index
            new_fraction = s.slice.fraction
        else:
            new_index, new_fraction = _resolve_index_for_axis(
                s.asset_path, axis, index, fraction
            )

        window_arg = args.get("window")
        level_arg = args.get("level")
        if window_arg is not None and not isinstance(window_arg, (int, float)):
            return {"error": "window must be a number"}
        if level_arg is not None and not isinstance(level_arg, (int, float)):
            return {"error": "level must be a number"}

        new_window, new_level = _window_level_from_args(
            s.slice,
            float(window_arg) if window_arg is not None else None,
            float(level_arg) if level_arg is not None else None,
        )

        new_slice = SliceState(
            axis=axis,  # type: ignore[arg-type]
            index=new_index,
            fraction=new_fraction,
            window=new_window,
            level=new_level,
        )
        replace_slice(sid, new_slice)

        return {
            "axis": axis,
            "index": new_index,
            "fraction": new_fraction,
            "window": [new_window[0], new_window[1]],
            "level": new_level,
        }
    except SVToolError as exc:
        return {"error": str(exc)}


# --- sv3d_set_clip_plane --------------------------------------------------


def _set_clip_plane_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        enabled = args.get("enabled")
        if not isinstance(enabled, bool):
            return {"error": "enabled must be a boolean"}

        s = _get_session(sid)
        cur = s.clip_plane

        origin_arg = args.get("origin")
        normal_arg = args.get("normal")
        cap_arg = args.get("cap")

        new_origin = cur.origin
        if origin_arg is not None:
            new_origin = _as_xyz(origin_arg, "origin")

        new_normal = cur.normal
        if normal_arg is not None:
            raw_normal = _as_xyz(normal_arg, "normal")
            new_normal = _normalize(raw_normal)

        if cap_arg is not None:
            if not isinstance(cap_arg, bool):
                return {"error": "cap must be a boolean"}
            new_cap = cap_arg
        else:
            new_cap = cur.cap

        new_cp = ClipPlaneState(
            enabled=enabled,
            origin=new_origin,
            normal=new_normal,
            cap=new_cap,
        )
        replace_clip_plane(sid, new_cp)
        return {
            "enabled": new_cp.enabled,
            "origin": list(new_cp.origin),
            "normal": list(new_cp.normal),
            "cap": new_cp.cap,
        }
    except SVToolError as exc:
        return {"error": str(exc)}


# --- registration ---------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv3d_set_render_mode",
            description=(
                "Switch render mode. mesh→{surface,points}; "
                "nifti→{volume,mpr_axial,mpr_sagittal,mpr_coronal}."
            ),
            input_schema=_SET_MODE_SCHEMA,
            handler=_set_render_mode_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_set_opacity",
            description="Set per-layer alpha (layer ∈ {base, mask, clip_cap}).",
            input_schema=_SET_OPACITY_SCHEMA,
            handler=_set_opacity_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_toggle_mask",
            description=(
                "Toggle / configure overlay mask: visible, mask_id, "
                "colormap, alpha (partial update; None means leave alone)."
            ),
            input_schema=_TOGGLE_MASK_SCHEMA,
            handler=_toggle_mask_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_set_slice",
            description=(
                "Scrub MPR slice on a nifti session (exactly one of "
                "index|fraction) plus window-width / level."
            ),
            input_schema=_SET_SLICE_SCHEMA,
            handler=_set_slice_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_set_clip_plane",
            description=(
                "Configure cutaway plane: enabled + origin + unit-normalized "
                "normal vector + cap."
            ),
            input_schema=_SET_CLIP_PLANE_SCHEMA,
            handler=_set_clip_plane_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_set_crosshair",
            description=(
                "Linked-MPR crosshair: align all three planes "
                "(axial/coronal/sagittal) to one focus point given as exactly "
                "one of point_world | mask_id | voxel. Echoes each plane's "
                "index/fraction so you can inspect the three correlated "
                "locations at once. Optional set_mode switches to mpr_axial."
            ),
            input_schema=_SET_CROSSHAIR_SCHEMA,
            handler=_set_crosshair_handler,
            category="F",
        )
    ),
]
