"""F-camera tools (Phase 3.2).

Five tools that manipulate ``Session.camera`` via the locked Phase 3.1
contract. All math is performed on plain ``tuple[float, float, float]``
vectors so the frozen ``CameraState`` is never mutated — each handler
constructs a fresh ``CameraState`` and atomically swaps it in via
``replace_camera``.

The camera echo envelope ``{"camera": {position, target, up, fov_deg}}``
is identical to the one emitted by ``sv3d_get_state`` so agent reasoning
composes cleanly across tool calls (SPEC §1 sharp-edge #5).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools.vision3d._camera_math import (
    _add,
    _bbox_center,
    _bbox_diag,
    _canonical_offsets,
    _cross,
    _dot,
    _length,
    _normalize,
    _orbit_camera,
    _resolve_look_at_target,
    _scale,
    _sub,
)
from sci3d_agent.mcp.tools.vision3d._store import get_store, replace_camera
from sci3d_agent.mcp.tools.vision3d._types import CameraState, Session
from sci3d_agent.mcp.tools.vision3d.session import _initial_camera

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_ELEV_LIMIT_DEG = 89.0
_FIT_BBOX_FACTOR = 1.4  # matches Phase 3.1 ``_initial_camera`` for fov=50
_DOLLY_MIN = 0.05
_DOLLY_MAX = 20.0
_PAN_LIMIT = 2.0


def _camera_to_dict(cam: CameraState) -> dict[str, Any]:
    return {
        "position": list(cam.position),
        "target": list(cam.target),
        "up": list(cam.up),
        "fov_deg": cam.fov_deg,
    }


def _camera_echo(session: Session) -> dict[str, Any]:
    return {"camera": _camera_to_dict(session.camera)}


def _get_session(sid: str) -> Session:
    try:
        return get_store().get(sid)
    except KeyError:
        raise SVToolError(f"unknown session_id: {sid}")


def _validate_sid(args: Mapping[str, Any]) -> str:
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid:
        raise SVToolError("session_id is required")
    return sid


# ---------------------------------------------------------------------------
# sv3d_camera_orbit
# ---------------------------------------------------------------------------


_ORBIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "d_azimuth_deg", "d_elevation_deg"],
    "properties": {
        "session_id": {"type": "string"},
        "d_azimuth_deg": {"type": "number"},
        "d_elevation_deg": {"type": "number"},
        "d_roll_deg": {"type": "number", "default": 0.0},
    },
    "additionalProperties": False,
}


def _orbit_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = _validate_sid(args)
        d_az = float(args.get("d_azimuth_deg", 0.0))
        d_el = float(args.get("d_elevation_deg", 0.0))
        d_roll = float(args.get("d_roll_deg", 0.0))
        s = _get_session(sid)
        new_cam = _orbit_camera(s.camera, d_az, d_el, d_roll)
        new_s = replace_camera(sid, new_cam)
        return _camera_echo(new_s)
    except SVToolError as exc:
        return {"error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"error": f"invalid orbit input: {exc}"}


# ---------------------------------------------------------------------------
# sv3d_camera_dolly
# ---------------------------------------------------------------------------


_DOLLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "factor"],
    "properties": {
        "session_id": {"type": "string"},
        "factor": {"type": "number", "minimum": _DOLLY_MIN, "maximum": _DOLLY_MAX},
    },
    "additionalProperties": False,
}


def _dolly_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = _validate_sid(args)
        if "factor" not in args:
            raise SVToolError("factor is required")
        factor = float(args["factor"])
        if not math.isfinite(factor) or factor < _DOLLY_MIN or factor > _DOLLY_MAX:
            raise SVToolError(
                f"factor must be in [{_DOLLY_MIN}, {_DOLLY_MAX}]; got {factor}"
            )
        s = _get_session(sid)
        cam = s.camera
        offset = _scale(_sub(cam.position, cam.target), factor)
        new_eye = _add(cam.target, offset)
        new_cam = CameraState(
            position=new_eye, target=cam.target, up=cam.up, fov_deg=cam.fov_deg
        )
        new_s = replace_camera(sid, new_cam)
        return _camera_echo(new_s)
    except SVToolError as exc:
        return {"error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"error": f"invalid dolly input: {exc}"}


# ---------------------------------------------------------------------------
# sv3d_camera_pan
# ---------------------------------------------------------------------------


_PAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "dx_frac", "dy_frac"],
    "properties": {
        "session_id": {"type": "string"},
        "dx_frac": {"type": "number", "minimum": -_PAN_LIMIT, "maximum": _PAN_LIMIT},
        "dy_frac": {"type": "number", "minimum": -_PAN_LIMIT, "maximum": _PAN_LIMIT},
    },
    "additionalProperties": False,
}


def _pan_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = _validate_sid(args)
        dx = float(args.get("dx_frac", 0.0))
        dy = float(args.get("dy_frac", 0.0))
        if not (math.isfinite(dx) and math.isfinite(dy)):
            raise SVToolError("dx_frac/dy_frac must be finite numbers")
        if abs(dx) > _PAN_LIMIT or abs(dy) > _PAN_LIMIT:
            raise SVToolError(
                f"pan fractions must be within [-{_PAN_LIMIT}, {_PAN_LIMIT}]"
            )
        s = _get_session(sid)
        cam = s.camera
        forward = _sub(cam.target, cam.position)
        flen = _length(forward) or 1.0
        right = _normalize(_cross(forward, cam.up))
        screen_up = _normalize(_cross(right, forward))
        delta = _add(_scale(right, dx * flen), _scale(screen_up, dy * flen))
        new_cam = CameraState(
            position=_add(cam.position, delta),
            target=_add(cam.target, delta),
            up=cam.up,
            fov_deg=cam.fov_deg,
        )
        new_s = replace_camera(sid, new_cam)
        return _camera_echo(new_s)
    except SVToolError as exc:
        return {"error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"error": f"invalid pan input: {exc}"}


# ---------------------------------------------------------------------------
# sv3d_camera_set_view
# ---------------------------------------------------------------------------


_VIEWS = ("front", "back", "left", "right", "top", "bottom", "iso", "reset")

_SET_VIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "view"],
    "properties": {
        "session_id": {"type": "string"},
        "view": {"enum": list(_VIEWS)},
        "fit_bbox": {"type": "boolean", "default": True},
    },
    "additionalProperties": False,
}


def _set_view_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = _validate_sid(args)
        view = args.get("view")
        if view not in _VIEWS:
            raise SVToolError(f"view must be one of {_VIEWS}; got {view!r}")
        fit_bbox = bool(args.get("fit_bbox", True))
        s = _get_session(sid)

        if view == "reset":
            new_cam = _initial_camera(s.bbox, s.axis_up)
            new_s = replace_camera(sid, new_cam)
            return _camera_echo(new_s)

        center = _bbox_center(s.bbox)
        diag = _bbox_diag(s.bbox)

        if fit_bbox:
            distance = diag * _FIT_BBOX_FACTOR
        else:
            # Preserve the current eye→target distance so agents that have
            # already dollied in can swap canonical views without re-zooming.
            cur_dist = _length(_sub(s.camera.position, s.camera.target)) or diag
            distance = cur_dist

        offset_unit, up = _canonical_offsets(s.axis_up, str(view), 1.0)
        eye_offset = _scale(_normalize(offset_unit), distance)
        new_cam = CameraState(
            position=_add(center, eye_offset),
            target=center,
            up=up,
            fov_deg=s.camera.fov_deg,
        )
        new_s = replace_camera(sid, new_cam)
        return _camera_echo(new_s)
    except SVToolError as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# sv3d_camera_look_at
# ---------------------------------------------------------------------------


_LOOK_AT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {
        "session_id": {"type": "string"},
        "target": {
            "oneOf": [
                {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                {"enum": ["bbox_center", "mask_centroid"]},
            ]
        },
        "distance": {"type": "number", "exclusiveMinimum": 0.0},
        "mask_id": {"type": "string"},
    },
    "additionalProperties": False,
}


def _look_at_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = _validate_sid(args)
        s = _get_session(sid)
        new_target = _resolve_look_at_target(s, args)
        cam = s.camera
        cur_offset = _sub(cam.position, cam.target)
        cur_dist = _length(cur_offset) or _bbox_diag(s.bbox) * _FIT_BBOX_FACTOR
        if "distance" in args and args["distance"] is not None:
            d = float(args["distance"])
            if not math.isfinite(d) or d <= 0:
                raise SVToolError("distance must be a positive number")
            distance = d
        else:
            distance = cur_dist
        direction = _normalize(cur_offset) if _length(cur_offset) > 1e-9 else (1.0, 0.0, 0.0)

        # Recompute up if forward is parallel to current up.
        new_eye = _add(new_target, _scale(direction, distance))
        forward = _sub(new_target, new_eye)
        up = cam.up
        if abs(_dot(_normalize(forward), _normalize(up))) > 0.999:
            ref = (0.0, 0.0, 1.0) if abs(up[2]) < 0.9 else (0.0, 1.0, 0.0)
            up = _normalize(_cross(_cross(forward, ref), forward))

        new_cam = CameraState(
            position=new_eye, target=new_target, up=up, fov_deg=cam.fov_deg
        )
        new_s = replace_camera(sid, new_cam)
        return _camera_echo(new_s)
    except SVToolError as exc:
        return {"error": str(exc)}
    except (TypeError, ValueError) as exc:
        return {"error": f"invalid look_at input: {exc}"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv3d_camera_orbit",
            description=(
                "Drag-orbit equivalent: azimuth/elevation/roll deltas applied "
                "around the current camera.target."
            ),
            input_schema=_ORBIT_SCHEMA,
            handler=_orbit_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_camera_dolly",
            description=(
                "Wheel-zoom equivalent: multiplicative distance change toward "
                "or away from the current target."
            ),
            input_schema=_DOLLY_SCHEMA,
            handler=_dolly_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_camera_pan",
            description=(
                "Shift-drag pan equivalent: shift eye and target together in "
                "screen-space by viewport fractions."
            ),
            input_schema=_PAN_SCHEMA,
            handler=_pan_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_camera_set_view",
            description=(
                "Snap to a canonical view (front/back/left/right/top/bottom/"
                "iso/reset), resolved against session.axis_up."
            ),
            input_schema=_SET_VIEW_SCHEMA,
            handler=_set_view_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_camera_look_at",
            description=(
                "Aim the camera at an explicit point, bbox_center, or a mask "
                "centroid; optional distance override."
            ),
            input_schema=_LOOK_AT_SCHEMA,
            handler=_look_at_handler,
            category="F",
        )
    ),
]
