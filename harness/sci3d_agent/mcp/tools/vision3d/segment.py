"""F-segment tools: sv3d_segment_2d / sv3d_segment_3d_volume / sv3d_segment_3d_mesh.

Each tool looks up a Session, forwards to the SAM HTTP service via
_segment_client.SamClient, and returns a JSON envelope. M3-class tools
(3D volume / 3D mesh) return ONLY structured data (bbox, centroid,
counts) — never pixels — by schema-level construction. M4-class tool
(2D) returns the rendered image with mask overlay because M4 puts pixels
into the LLM context.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.tools.vision3d._segment_client import SamClient, SamClientError
from sci3d_agent.mcp.tools.vision3d._store import get_store, gpu_slot
from sci3d_agent.mcp.tools.vision3d._snapshot_camera import _seed_rng
from sci3d_agent.mcp.tools.vision3d.snapshot import _render_one_canonical
from sci3d_agent.mcp.tools.vision3d._types import Session

log = logging.getLogger("sciencevision.vision3d.segment")


_CANONICAL_VIEWS = ("front", "back", "left", "right", "top", "bottom", "iso")


def _get_session(sid: str) -> Session:
    try:
        return get_store().get(sid)
    except KeyError:
        raise SVToolError(f"unknown session_id: {sid}")


def _default_client() -> SamClient:
    return SamClient()


def _validate_request(
    args: Mapping[str, Any],
    *,
    tool_name: str,
    expected_kind: str | None = None,
) -> tuple[Session | None, str | None, Mapping[str, Any] | None]:
    """Validate session_id/text_prompt and (optionally) session kind.

    Returns ``(session, prompt, error)`` where exactly one of
    (session, prompt) and error is None. The error variant is the
    final {"error": ...} envelope ready to return to the caller.
    """
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid:
        return None, None, {"error": "session_id is required"}
    prompt = args.get("text_prompt")
    if not isinstance(prompt, str) or not prompt:
        return None, None, {"error": "text_prompt is required"}
    try:
        session = _get_session(sid)
    except SVToolError as exc:
        return None, None, {"error": str(exc)}
    if expected_kind is not None and session.kind != expected_kind:
        return None, None, {
            "error": f"{tool_name} requires a {expected_kind} session, got kind={session.kind!r}"
        }
    return session, prompt, None


# -- 2D -----------------------------------------------------------------


_SEGMENT_2D_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "text_prompt"],
    "properties": {
        "session_id": {"type": "string"},
        "view": {"enum": list(_CANONICAL_VIEWS), "default": "iso"},
        "text_prompt": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


def _segment_2d_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    session, prompt, err = _validate_request(args, tool_name="sv3d_segment_2d")
    if err is not None:
        return err
    view = str(args.get("view", "iso"))
    if view not in _CANONICAL_VIEWS:
        return {"error": f"unsupported view: {view!r}"}

    with gpu_slot():
        _seed_rng(session)
        rendered = _render_one_canonical(session, view, width=768, height=768, bg="#101418")
    image_b64 = rendered.get("image_png_base64")
    if image_b64 is None:
        # Renderer returned a skipped envelope — surface as error so M4
        # doesn't silently degrade into "no vision".
        details = {
            k: rendered.get(k)
            for k in ("renderer_used", "renderer_skipped")
            if rendered.get(k) is not None
        }
        return {
            "error": "renderer produced no pixels (GL deps missing?)",
            "renderer": details,
        }

    try:
        sam = _default_client().sam2d(image_b64=image_b64, text_prompt=prompt)
    except SamClientError as exc:
        return {"error": f"SAM service error: {exc}"}

    masks_out = []
    for m in sam.get("masks", []):
        masks_out.append({
            "label": prompt,
            "score": float(m.get("score", 0.0)),
            "bbox_2d": list(m.get("bbox", [0, 0, 0, 0])),
            "area_px": int(m.get("area", 0)),
            "mask_b64": m.get("mask_b64"),
        })
    return {
        "image_png_base64": image_b64,
        "masks": masks_out,
        "view": view,
    }


# -- 3D volume ---------------------------------------------------------


_SEGMENT_3D_VOLUME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "text_prompt"],
    "properties": {
        "session_id": {"type": "string"},
        "text_prompt": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


def _segment_3d_volume_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    session, prompt, err = _validate_request(
        args, tool_name="sv3d_segment_3d_volume", expected_kind="nifti"
    )
    if err is not None:
        return err

    volume_path = session.asset_path
    try:
        sam = _default_client().sam3d_volume(volume_path=volume_path, text_prompt=prompt)
    except SamClientError as exc:
        return {"error": f"SAM service error: {exc}"}

    masks_out = []
    for m in sam.get("masks", []):
        masks_out.append({
            "label": prompt,
            "score": float(m.get("score", 0.0)),
            "voxel_count": int(m.get("voxel_count", 0)),
            "centroid_world": list(m.get("centroid", [0.0, 0.0, 0.0])),
            "bbox_world": [list(p) for p in m.get("bbox", [[0, 0, 0], [0, 0, 0]])],
        })
    return {"masks": masks_out}


# -- 3D mesh -----------------------------------------------------------


_SEGMENT_3D_MESH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id", "text_prompt"],
    "properties": {
        "session_id": {"type": "string"},
        "text_prompt": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


def _segment_3d_mesh_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    session, prompt, err = _validate_request(
        args, tool_name="sv3d_segment_3d_mesh", expected_kind="mesh"
    )
    if err is not None:
        return err

    pointcloud_path = session.asset_path
    try:
        sam = _default_client().sam3d_mesh(pointcloud_path=pointcloud_path, text_prompt=prompt)
    except SamClientError as exc:
        return {"error": f"SAM service error: {exc}"}

    segs_out = []
    for s in sam.get("segments", []):
        segs_out.append({
            "label": prompt,
            "score": float(s.get("score", 0.0)),
            "point_count": int(s.get("point_count", 0)),
            "centroid_world": list(s.get("centroid", [0.0, 0.0, 0.0])),
            "bbox_world": [list(p) for p in s.get("bbox", [[0, 0, 0], [0, 0, 0]])],
        })
    return {"segments": segs_out}


# -- registration ------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv3d_segment_2d",
            description=(
                "Render the current session at the requested canonical view and ask the SAM "
                "service for a 2D mask matching `text_prompt`. Returns the rendered image "
                "(image_png_base64), the segmentation masks (each with mask_b64, score, "
                "bbox_2d, area_px), and the view used."
            ),
            input_schema=_SEGMENT_2D_SCHEMA,
            handler=_segment_2d_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_segment_3d_volume",
            description=(
                "Ask the SAM service for a 3D segmentation of the current nifti "
                "volume matching `text_prompt`. Returns only structured summary "
                "data (voxel_count, centroid_world, bbox_world); no pixels."
            ),
            input_schema=_SEGMENT_3D_VOLUME_SCHEMA,
            handler=_segment_3d_volume_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_segment_3d_mesh",
            description=(
                "Ask the SAM service for a 3D segmentation of the current mesh / "
                "point cloud matching `text_prompt`. Returns only structured "
                "summary data (point_count, centroid_world, bbox_world); no pixels."
            ),
            input_schema=_SEGMENT_3D_MESH_SCHEMA,
            handler=_segment_3d_mesh_handler,
            category="F",
        )
    ),
]
