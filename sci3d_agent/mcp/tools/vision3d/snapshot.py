"""F-snapshot tools (Phase 3.3): sv3d_render + sv3d_render_canonical_views.

Two tools that read the immutable Phase 3.1 ``Session`` and turn it into a
PNG (or a graceful "skipped" envelope when GL deps are unavailable). All
rendering goes through the Phase 2/D primitives under ``_render/`` — this
module composes session state into the primitive arguments and wraps the
PNG with the same image envelope helper used by the static renderers.

Determinism (SPEC §1 sharp-edge #8): identical session state -> identical
PNG bytes -> identical SHA. The primitives are themselves deterministic;
this module's only stochastic surface is point-cloud decimation (not yet
exercised), for which ``session.rng_seed`` is wired through.

Graceful degradation: when ``pyrender``/``pyvista``/``nibabel`` are not
importable, every handler returns a ``renderer_skipped`` envelope that
still echoes the full reasoning state (camera, mode, slice, mask_state).
This lets end-to-end agent tests pass on dev boxes without GL while real
rendering remains exercised under ``SV_E2E=1``.

Implementation note: the pure camera/state-echo helpers live in
``_snapshot_camera`` and the renderer-dispatch helpers live in
``_snapshot_render``; this module retains only session lookup, the two
public handlers, and tool registration.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools._render import common as _common  # noqa: F401 (test monkeypatch target)
from sci3d_agent.mcp.tools._render import nifti as _nifti  # noqa: F401 (test monkeypatch target)
from sci3d_agent.mcp.tools.vision3d._snapshot_camera import (
    _base_echo,  # noqa: F401 (test helper accessor)
    _camera_dict,
    _canonical_camera,
    _seed_rng,
)
from sci3d_agent.mcp.tools.vision3d._snapshot_render import (
    _compose_tile_sheet,
    _decode_tile_png_b64,
    _render_mesh,
    _render_nifti,
    _render_nifti_fallback_volume,
)
from sci3d_agent.mcp.tools.vision3d._store import get_store, gpu_slot
from sci3d_agent.mcp.tools.vision3d._types import Session

log = logging.getLogger("sciencevision.vision3d.snapshot")

_CANONICAL_VIEWS_DEFAULT: tuple[str, ...] = (
    "front",
    "right",
    "back",
    "left",
    "top",
    "iso",
)
_CANONICAL_VIEWS_ALL: tuple[str, ...] = (
    "front",
    "back",
    "left",
    "right",
    "top",
    "bottom",
    "iso",
)


# ---------------------------------------------------------------------------
# Session lookup
# ---------------------------------------------------------------------------


def _get_session(sid: str) -> Session:
    try:
        return get_store().get(sid)
    except KeyError:
        raise SVToolError(f"unknown session_id: {sid}")


# ---------------------------------------------------------------------------
# Dispatch: session state -> primitive call -> PNG bytes
#
# These thin wrappers resolve ``_render_mesh``/``_render_nifti`` from this
# module's globals so tests can monkeypatch the dispatch target directly;
# the heavy lifting lives in ``_snapshot_render``.
# ---------------------------------------------------------------------------


def _dispatch(
    session: Session,
    *,
    width: int,
    height: int,
    bg: str,
) -> dict[str, Any]:
    if session.kind == "mesh":
        return _render_mesh(session, width=width, height=height, bg=bg)
    if session.mode == "volume":
        result = _render_nifti_fallback_volume(session, width=width, height=height)
        result["renderer_used"] = "mpr_axial_fallback"
        return result
    return _render_nifti(session, width=width, height=height)


def _render_one_canonical(
    session: Session,
    view: str,
    *,
    width: int,
    height: int,
    bg: str,
) -> dict[str, Any]:
    """Render a single canonical view *without* mutating the session."""
    cam = _canonical_camera(session, view)
    if session.kind == "mesh":
        result = _render_mesh(session, width=width, height=height, bg=bg, camera_override=cam)
    else:
        # Non-mesh canonical views currently don't change MPR slice selection;
        # we still echo the canonical camera so the agent can reason about it.
        result = _render_nifti(session, width=width, height=height)
    # Always echo the per-view canonical camera so the agent can reason about
    # the framing, even when the renderer fell back to a skipped envelope.
    result["camera"] = _camera_dict(cam)
    return result


# ---------------------------------------------------------------------------
# sv3d_render
# ---------------------------------------------------------------------------


_RENDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {
        "session_id": {"type": "string"},
        "width": {"type": "integer", "default": 768, "minimum": 64, "maximum": 2048},
        "height": {"type": "integer", "default": 768, "minimum": 64, "maximum": 2048},
        "background": {"type": "string", "default": "#101418"},
        "return": {"enum": ["png_b64", "url"], "default": "png_b64"},
    },
    "additionalProperties": False,
}


def _render_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        width = int(args.get("width", 768))
        height = int(args.get("height", 768))
        bg = str(args.get("background", "#101418"))
        if width < 64 or width > 2048 or height < 64 or height > 2048:
            return {"error": "width/height must be in [64, 2048]"}

        session = _get_session(sid)
        with gpu_slot():
            _seed_rng(session)
            return _dispatch(session, width=width, height=height, bg=bg)
    except SVToolError as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# sv3d_render_canonical_views
# ---------------------------------------------------------------------------


_RENDER_VIEWS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {
        "session_id": {"type": "string"},
        "views": {
            "type": "array",
            "items": {"enum": list(_CANONICAL_VIEWS_ALL)},
            "default": list(_CANONICAL_VIEWS_DEFAULT),
            "minItems": 1,
            "maxItems": 7,
        },
        "width": {"type": "integer", "default": 512, "minimum": 64, "maximum": 1024},
        "height": {"type": "integer", "default": 512, "minimum": 64, "maximum": 1024},
        "tile": {"type": "boolean", "default": False},
    },
    "additionalProperties": False,
}


def _render_canonical_views_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        sid = args.get("session_id")
        if not isinstance(sid, str) or not sid:
            return {"error": "session_id is required"}
        views_in = args.get("views") or list(_CANONICAL_VIEWS_DEFAULT)
        if not isinstance(views_in, list) or not views_in:
            return {"error": "views must be a non-empty list"}
        for v in views_in:
            if v not in _CANONICAL_VIEWS_ALL:
                return {"error": f"unsupported view: {v!r}"}
        width = int(args.get("width", 512))
        height = int(args.get("height", 512))
        if width < 64 or width > 1024 or height < 64 or height > 1024:
            return {"error": "width/height must be in [64, 1024]"}
        tile = bool(args.get("tile", False))
        bg = "#101418"

        session = _get_session(sid)
        images: list[dict[str, Any]] = []
        with gpu_slot():
            _seed_rng(session)
            for v in views_in:
                result = _render_one_canonical(
                    session, v, width=width, height=height, bg=bg
                )
                entry: dict[str, Any] = {
                    "view": v,
                    "camera": result.get("camera", _camera_dict(_canonical_camera(session, v))),
                }
                if "image_png_base64" in result:
                    entry["image_b64"] = result["image_png_base64"]
                if "image_url" in result:
                    entry["image_url"] = result["image_url"]
                if "renderer_skipped" in result:
                    entry["renderer_skipped"] = result["renderer_skipped"]
                if "renderer_used" in result:
                    entry["renderer_used"] = result["renderer_used"]
                images.append(entry)

        out: dict[str, Any] = {"images": images}
        if tile:
            png_tiles: list[tuple[str, bytes]] = []
            for img in images:
                raw = _decode_tile_png_b64({"image_png_base64": img.get("image_b64")})
                if raw is not None:
                    png_tiles.append((str(img["view"]), raw))
            if png_tiles:
                sheet_bytes = _compose_tile_sheet(
                    png_tiles, width=width, height=height
                )
                if sheet_bytes is not None:
                    import base64

                    out["tiled_image_b64"] = base64.b64encode(sheet_bytes).decode("ascii")
        return out
    except SVToolError as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv3d_render",
            description=(
                "Snapshot the current session state to a PNG + camera echo. "
                "Dispatches by Session.kind+mode to the Phase-D primitives; "
                "falls back to a renderer_skipped envelope when GL deps are missing."
            ),
            input_schema=_RENDER_SCHEMA,
            handler=_render_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_render_canonical_views",
            description=(
                "Render 4-7 canonical views in one call without mutating "
                "session state; optional tile=true composites into a single sheet."
            ),
            input_schema=_RENDER_VIEWS_SCHEMA,
            handler=_render_canonical_views_handler,
            category="F",
        )
    ),
]
