"""The local MCP's viewer-family gate.

Only category-F (live-scene) tools are viewer capabilities.  Categories A--E
are catalog, inspection, static-rendering, and pipeline tools; hiding those
because an expert's browser had a different camera widget would remove the
agent's evidence path without improving parity.  A family gate therefore keeps
all non-F tools and narrows only F.

This repository intentionally does not claim to contain the full upstream
platform snapshot.  The map is a conservative runtime map for the tool surface
actually shipped here.  Unknown families raise rather than silently receiving
the entire MCP catalog.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable


class UnknownFamilyError(ValueError):
    """A task requested a viewer family that this MCP build cannot identify."""


# ``molstar.v1`` remains a known fixture family for historical tasks, although
# new molstar tasks use v2.  The live-viewer mapping must never regress to it.
_FAMILY_VIEWER: dict[str, str] = {
    "architecture_inspection.v1": "architecture_inspection",
    "bim_coordination.v1": "bim_coordination",
    "gltf.v1": "gltf",
    "molstar.v1": "molstar",
    "molstar.v2": "molstar",
    "niivue.v1": "niivue",
    "occt.v1": "occt",
}

LIVE_REGISTRY_BY_VIEWER: dict[str, str] = {
    "molstar": "molstar.v2",
    "niivue": "niivue.v1",
    "gltf": "gltf.v1",
    "occt": "occt.v1",
    "bim_coordination": "bim_coordination.v1",
    "architecture_inspection": "architecture_inspection.v1",
}


# The session lifecycle and evidence-render floor is available on every
# family.  Additional entries are deliberately conservative and are intersected
# with what this build actually implements below.
_FLOOR = frozenset({
    "sv3d_open_scene",
    "sv3d_close_scene",
    "sv3d_get_state",
    "sv3d_render",
    "sv3d_render_canonical_views",
})
_CAMERA = frozenset({
    "sv3d_camera_orbit",
    "sv3d_camera_dolly",
    "sv3d_camera_pan",
    "sv3d_camera_set_view",
    "sv3d_camera_look_at",
})
_MESH = frozenset({"sv3d_set_render_mode", "sv3d_set_opacity", "sv3d_set_clip_plane"})
_NIFTI = frozenset({
    "sv3d_set_render_mode",
    "sv3d_set_opacity",
    "sv3d_toggle_mask",
    "sv3d_set_slice",
    "sv3d_set_clip_plane",
    "sv3d_set_crosshair",
    "sv3d_segment_2d",
    "sv3d_segment_3d_volume",
})

_FAMILY_F_TOOLS: dict[str, frozenset[str]] = {
    "architecture_inspection.v1": (
        _FLOOR | _CAMERA | _MESH | {"sv3d_segment_2d", "sv3d_segment_3d_mesh"}
    ),
    "bim_coordination.v1": _FLOOR | _CAMERA | _MESH | {"sv3d_segment_2d", "sv3d_segment_3d_mesh"},
    "gltf.v1": _FLOOR | _CAMERA | _MESH | {"sv3d_segment_2d", "sv3d_segment_3d_mesh"},
    "molstar.v1": _FLOOR | _CAMERA | {"sv3d_set_render_mode", "sv3d_set_opacity"},
    "molstar.v2": _FLOOR | _CAMERA | {"sv3d_set_render_mode", "sv3d_set_opacity"},
    "niivue.v1": _FLOOR | _NIFTI,
    "occt.v1": _FLOOR | _CAMERA | _MESH | {"sv3d_segment_2d", "sv3d_segment_3d_mesh"},
}


@lru_cache(maxsize=1)
def _check_live_map() -> None:
    """Make stale/mistyped mappings a build failure rather than an ungated run."""
    declared_viewers = set(_FAMILY_VIEWER.values())
    mapped_viewers = set(LIVE_REGISTRY_BY_VIEWER)
    missing = sorted(declared_viewers - mapped_viewers)
    if missing:
        raise ValueError("LIVE_REGISTRY_BY_VIEWER does not cover: " + ", ".join(missing))
    for viewer, family in LIVE_REGISTRY_BY_VIEWER.items():
        declared = _FAMILY_VIEWER.get(family)
        if declared is None:
            raise UnknownFamilyError(f"unknown viewer family in live map: {family}")
        if declared != viewer:
            raise ValueError(f"viewer family {family} does not serve that viewer: {viewer}")


def families() -> tuple[str, ...]:
    """All known family labels, including historical fixtures."""
    return tuple(sorted(_FAMILY_VIEWER))


def live_registry_for_viewer(viewer: str | None) -> str | None:
    """Return the current registry for one viewer, or ``None`` when ungated."""
    _check_live_map()
    text = str(viewer or "").strip()
    return LIVE_REGISTRY_BY_VIEWER.get(text)


def _tool_name_and_category(tool: Any) -> tuple[str, str | None]:
    if isinstance(tool, str):
        return tool, "F" if tool.startswith("sv3d_") else None
    name = str(getattr(tool, "name", ""))
    category = getattr(tool, "category", None)
    return name, str(category) if category is not None else None


def advertised_tools_for_family(family: str, tools: Iterable[Any] | None = None) -> list[Any]:
    """Return the exact MCP surface for ``family``.

    ``tools`` may be strings (useful for tests) or ``ToolSpec`` objects
    (server use).  The same object type/order is returned.  Passing no input
    lazily reads ``ALL_TOOLS`` so importing the family map itself never imports
    optional rendering dependencies.
    """
    _check_live_map()
    wanted = str(family or "").strip()
    if wanted not in _FAMILY_F_TOOLS:
        raise UnknownFamilyError(f"unknown viewer family: {family!r}")
    if tools is None:
        from sci3d_agent.mcp.tools import ALL_TOOLS

        tools = ALL_TOOLS
    allowed_f = _FAMILY_F_TOOLS[wanted]
    out: list[Any] = []
    for tool in tools:
        name, category = _tool_name_and_category(tool)
        if not name:
            continue
        # If a caller passed plain names, `sv3d_` is the best available F
        # classifier.  ToolSpec.category is authoritative when present.
        is_f = category == "F" or (category is None and name.startswith("sv3d_"))
        if not is_f or name in allowed_f:
            out.append(tool)
    return out
