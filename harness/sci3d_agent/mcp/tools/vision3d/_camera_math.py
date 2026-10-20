"""Pure vector-math helpers for the F-camera tools (Phase 3.2).

These functions have no side effects — they operate on plain
``tuple[float, float, float]`` vectors and frozen ``CameraState`` /
``Session`` instances, returning fresh values. They are factored out of
``camera.py`` so the handler module stays focused on tool wiring.

This module MUST NOT import from ``camera`` (that would create a cycle).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools.vision3d._types import AxisUp, CameraState, Session

Vec3 = tuple[float, float, float]

_ELEV_LIMIT_DEG = 89.0


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalize(v: Vec3) -> Vec3:
    n = _length(v) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _rotate_around_axis(v: Vec3, axis: Vec3, angle_rad: float) -> Vec3:
    """Rodrigues rotation of ``v`` around unit-axis ``axis`` by ``angle_rad``."""
    k = _normalize(axis)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    term1 = _scale(v, c)
    term2 = _scale(_cross(k, v), s)
    term3 = _scale(k, _dot(k, v) * (1.0 - c))
    return _add(_add(term1, term2), term3)


def _bbox_center(
    bbox: tuple[Vec3, Vec3],
) -> Vec3:
    (x0, y0, z0), (x1, y1, z1) = bbox
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0)


def _bbox_diag(bbox: tuple[Vec3, Vec3]) -> float:
    (x0, y0, z0), (x1, y1, z1) = bbox
    return math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) or 1.0


# ---------------------------------------------------------------------------
# Orbit: spherical coords centered on ``target``
# ---------------------------------------------------------------------------


def _orbit_camera(
    cam: CameraState,
    d_az_deg: float,
    d_el_deg: float,
    d_roll_deg: float,
) -> CameraState:
    """Orbit the eye around ``target`` keeping ``target`` fixed.

    Elevation is clamped to ``[-_ELEV_LIMIT_DEG, +_ELEV_LIMIT_DEG]`` to
    avoid gimbal lock when the eye-vector becomes parallel to ``up``
    (the azimuth axis would collapse). Roll rotates ``up`` around the
    eye→target axis afterwards.
    """
    eye = cam.position
    tgt = cam.target
    up = _normalize(cam.up)

    dir_vec = _sub(eye, tgt)
    radius = _length(dir_vec) or 1.0
    dir_unit = _scale(dir_vec, 1.0 / radius)

    cur_el_rad = math.asin(max(-1.0, min(1.0, _dot(dir_unit, up))))

    horiz = _sub(dir_unit, _scale(up, math.sin(cur_el_rad)))
    horiz_len = _length(horiz)
    if horiz_len < 1e-9:
        # Eye is (nearly) along the up axis; build an arbitrary horizontal basis.
        ref = (1.0, 0.0, 0.0) if abs(up[0]) < 0.9 else (0.0, 1.0, 0.0)
        horiz_unit = _normalize(_cross(up, ref))
    else:
        horiz_unit = _scale(horiz, 1.0 / horiz_len)

    new_az_rad = math.radians(d_az_deg)
    new_el_rad = cur_el_rad + math.radians(d_el_deg)
    limit = math.radians(_ELEV_LIMIT_DEG)
    new_el_rad = max(-limit, min(limit, new_el_rad))

    # Rotate horizontal basis around ``up`` by azimuth delta.
    rotated_horiz = _rotate_around_axis(horiz_unit, up, new_az_rad)

    new_dir = _add(
        _scale(rotated_horiz, math.cos(new_el_rad)),
        _scale(up, math.sin(new_el_rad)),
    )
    new_dir = _normalize(new_dir)
    new_eye = _add(tgt, _scale(new_dir, radius))

    new_up = up
    if d_roll_deg:
        axis = _normalize(_sub(tgt, new_eye))
        new_up = _normalize(_rotate_around_axis(up, axis, math.radians(d_roll_deg)))

    return CameraState(
        position=new_eye, target=tgt, up=new_up, fov_deg=cam.fov_deg
    )


def _canonical_offsets(
    axis_up: AxisUp, view: str, diag: float
) -> tuple[Vec3, Vec3]:
    """Return ``(eye_offset_from_target, up_vector)`` for a canonical view.

    Z-up / RAS uses the convention that "front" looks down -Y with up=+Z.
    Y-up rotates axes: "front" looks down -Z (i.e. ``eye = target + (0,0,+diag)``
    so the camera looks toward -Z) and "top" looks down -Y. The iso vector
    is the unit diagonal that includes both horizontal axes plus the up
    axis, scaled to ``diag``.
    """
    iso_scale = diag / math.sqrt(3.0)
    if axis_up in ("Z", "RAS"):
        if view == "front":
            return (0.0, -diag, 0.0), (0.0, 0.0, 1.0)
        if view == "back":
            return (0.0, +diag, 0.0), (0.0, 0.0, 1.0)
        if view == "left":
            return (-diag, 0.0, 0.0), (0.0, 0.0, 1.0)
        if view == "right":
            return (+diag, 0.0, 0.0), (0.0, 0.0, 1.0)
        if view == "top":
            return (0.0, 0.0, +diag), (0.0, 1.0, 0.0)
        if view == "bottom":
            return (0.0, 0.0, -diag), (0.0, 1.0, 0.0)
        if view == "iso":
            return (
                (+iso_scale, -iso_scale, +iso_scale),
                (0.0, 0.0, 1.0),
            )
    elif axis_up == "Y":
        if view == "front":
            return (0.0, 0.0, +diag), (0.0, 1.0, 0.0)
        if view == "back":
            return (0.0, 0.0, -diag), (0.0, 1.0, 0.0)
        if view == "left":
            return (-diag, 0.0, 0.0), (0.0, 1.0, 0.0)
        if view == "right":
            return (+diag, 0.0, 0.0), (0.0, 1.0, 0.0)
        if view == "top":
            return (0.0, +diag, 0.0), (0.0, 0.0, -1.0)
        if view == "bottom":
            return (0.0, -diag, 0.0), (0.0, 0.0, 1.0)
        if view == "iso":
            return (
                (+iso_scale, +iso_scale, +iso_scale),
                (0.0, 1.0, 0.0),
            )
    elif axis_up == "X":
        # Symmetric to Y/Z with the primary axis swapped to X.
        if view == "front":
            return (0.0, -diag, 0.0), (1.0, 0.0, 0.0)
        if view == "back":
            return (0.0, +diag, 0.0), (1.0, 0.0, 0.0)
        if view == "left":
            return (0.0, 0.0, -diag), (1.0, 0.0, 0.0)
        if view == "right":
            return (0.0, 0.0, +diag), (1.0, 0.0, 0.0)
        if view == "top":
            return (+diag, 0.0, 0.0), (0.0, 1.0, 0.0)
        if view == "bottom":
            return (-diag, 0.0, 0.0), (0.0, 1.0, 0.0)
        if view == "iso":
            return (
                (+iso_scale, +iso_scale, +iso_scale),
                (1.0, 0.0, 0.0),
            )
    raise SVToolError(f"unsupported axis_up/view combo: {axis_up}/{view}")


def _resolve_look_at_target(s: Session, args: Mapping[str, Any]) -> Vec3:
    raw = args.get("target", "bbox_center")
    if isinstance(raw, list):
        if len(raw) != 3:
            raise SVToolError("target list must have exactly 3 numbers")
        return (float(raw[0]), float(raw[1]), float(raw[2]))
    if raw == "bbox_center":
        return _bbox_center(s.bbox)
    if raw == "mask_centroid":
        if not s.masks_info:
            raise SVToolError("session has no masks; cannot resolve mask_centroid")
        mask_id = args.get("mask_id")
        if mask_id is not None:
            for m in s.masks_info:
                if m.mask_id == str(mask_id):
                    return m.centroid_world
            raise SVToolError(f"mask_id={mask_id!r} not found in session masks")
        return s.masks_info[0].centroid_world
    raise SVToolError(f"invalid target value: {raw!r}")
