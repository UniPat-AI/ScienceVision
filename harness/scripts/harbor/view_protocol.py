"""The frozen cc2d view protocol (``cc2d-views/2``), shared by every view plan.

Chosen 2026-09-23 and already shipped in the v9 delivery's ``views-aligned/``
(146 tasks). ``cc2d-6``'s named set is exactly what ``sv3d_render_canonical_views``
samples by default, and the angles are viewer-frame (Y-up) spherical degrees:
``azimuth = atan2(d.x, d.z)``, ``elevation = asin(d.y / |d|)``.

``cc2d-12`` extends the nesting with the last named face plus five orbit fills so
"more views = more information" survives the 6 -> 12 escalation; the fills stay in
the same viewer frame as the named faces.

This module is planner-side and pure data: it must import with no sci3d_agent /
GL / CAD stack present (the harbor venv is not the render venv). The renderer-side
copy lives in ``sci3d_agent.mcp.tools._render.canonical_views``; a test holds the
two equal and equal to the viewer's own sampling directions rather than one
importing the other.
"""
from __future__ import annotations

SCHEME = "cc2d-views/2"
ANGLE_FRAME = "viewer_y_up"

VIEWER_ANGLES: dict[str, tuple[float, float]] = {
    "front": (0.0, 0.0),
    "right": (90.0, 0.0),
    "back": (180.0, 0.0),
    "left": (270.0, 0.0),
    "top": (0.0, 90.0),
    "bottom": (0.0, -90.0),
    "iso": (45.0, 35.26439),
}
NAMED_6: tuple[str, ...] = ("iso", "front", "top", "right", "back", "left")
ORBIT_FILL_AZIMUTHS: tuple[float, ...] = (30.0, 90.0, 150.0, 210.0, 270.0)
ORBIT_FILL_ELEVATION = 20.0

_NAMED_BY_N: dict[int, tuple[str, ...]] = {
    1: ("iso",),
    3: NAMED_6[:3],
    6: NAMED_6,
    12: NAMED_6 + ("bottom",),
}


def entries(n: int) -> list[tuple[str, float | str]] | None:
    """The frozen plan for ``n``, as ``("named", view)`` / ``("orbit", azimuth)``.

    ``None`` means the protocol does not define this count; callers fall back to
    the legacy orbit so other corpora keep planning.
    """
    named = _NAMED_BY_N.get(int(n))
    if named is None:
        return None
    out: list[tuple[str, float | str]] = [("named", view) for view in named]
    if int(n) == 12:
        out.extend(("orbit", azimuth) for azimuth in ORBIT_FILL_AZIMUTHS)
    return out


def angles(kind: str, value: float | str) -> tuple[float, float]:
    """Viewer-frame ``(azimuth, elevation)`` for one plan entry."""
    if kind == "named":
        return VIEWER_ANGLES[str(value)]
    return (float(value), ORBIT_FILL_ELEVATION)
