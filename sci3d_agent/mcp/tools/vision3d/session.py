"""F-vision session tools (Phase 3.1 / F-core).

Three tools:

* ``sv3d_open_scene``  — resolve qid|asset_path, inspect, create Session.
* ``sv3d_close_scene`` — remove a Session from the store (idempotent).
* ``sv3d_get_state``   — snapshot a Session as a JSON-friendly dict.

Phase 3.2 (F-camera / F-render-state) consumes these via ``REGISTRY`` to
mutate camera/mode/slice/mask_state through the ``replace_*`` helpers in
``_store``. This module owns NO Phase-3.2 logic.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Mapping, Optional

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec, register
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.runtime.paths import repo_root
from sci3d_agent.mcp.tools.vision3d._store import (
    CapacityError,
    get_store,
    monotonic_now,
    new_session_id,
)
from sci3d_agent.mcp.tools.vision3d._types import (
    AxisUp,
    CameraState,
    ClipPlaneState,
    Kind,
    MaskInfo,
    MaskState,
    Mode,
    Opacity,
    Session,
    SliceState,
)

log = logging.getLogger("sciencevision.vision3d.session")


# --- catalog + path resolution --------------------------------------------


def _resolve_paths_from_qid(qid: str) -> tuple[str, Optional[str], str]:
    """Resolve (asset_path, mask_path_or_None, asset_kind) via REGISTRY."""
    rec = REGISTRY["sv_get_matched_record"].handler({"qid": qid})
    if "error" in rec:
        raise SVToolError(rec["error"])
    r = rec["record"]
    asset = r.get("url_viewer")
    if not asset:
        raise SVToolError(f"matched record for qid={qid} has no url_viewer")
    mask_block = r.get("mask") if isinstance(r.get("mask"), dict) else None
    mask = mask_block.get("mask_url_viewer") if mask_block else None
    return asset, mask, r.get("asset_kind", "")


def _detect_kind(path: str) -> Kind:
    p = path.lower()
    if p.endswith(".nii") or p.endswith(".nii.gz"):
        return "nifti"
    if any(p.endswith(s) for s in (".ply", ".glb", ".gltf", ".obj", ".stl")):
        return "mesh"
    raise SVToolError(f"unsupported asset suffix: {path}")


def _resolve_repo_path(url_or_path: str) -> Path:
    """Accept /datasets/... (url_viewer), absolute, or relative; return Path."""
    raw = Path(url_or_path)
    if raw.is_absolute():
        return raw
    return repo_root() / url_or_path.lstrip("/")


def _inspect_for_open(
    kind: Kind, asset_abs: Path, mask_abs: Optional[Path]
) -> Mapping[str, Any]:
    """Dispatch to the appropriate Phase 1/C inspector via REGISTRY."""
    if kind == "mesh":
        return REGISTRY["sv_inspect_mesh"].handler({"path": str(asset_abs)})
    if mask_abs is not None:
        return REGISTRY["sv_inspect_nifti_mask_pair"].handler(
            {"nifti_path": str(asset_abs), "mask_path": str(mask_abs)}
        )
    return REGISTRY["sv_inspect_nifti"].handler({"path": str(asset_abs)})


# --- axis-up + initial camera ---------------------------------------------


def _axis_up_for_mesh(asset_path: str, inspector_out: Mapping[str, Any]) -> AxisUp:
    hint = inspector_out.get("axis_up_hint")
    if hint in ("Y", "Z"):
        return hint  # type: ignore[return-value]
    return "Y" if any(asset_path.lower().endswith(s) for s in (".glb", ".gltf")) else "Z"


def _up_vector(axis_up: AxisUp) -> tuple[float, float, float]:
    return {
        "X": (1.0, 0.0, 0.0),
        "Y": (0.0, 1.0, 0.0),
        "Z": (0.0, 0.0, 1.0),
        "RAS": (0.0, 0.0, 1.0),
    }[axis_up]


def _initial_camera(
    bbox: tuple[tuple[float, float, float], tuple[float, float, float]],
    axis_up: AxisUp,
) -> CameraState:
    (x0, y0, z0), (x1, y1, z1) = bbox
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0
    diag = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    up = _up_vector(axis_up)
    # Eye offset placed so the bbox fills the view comfortably (~diag * 1.4).
    if up == (0.0, 0.0, 1.0):
        eye = (cx + diag * 1.0, cy - diag * 1.0, cz + diag * 0.4)
    elif up == (0.0, 1.0, 0.0):
        eye = (cx + diag * 1.0, cy + diag * 0.4, cz + diag * 1.0)
    else:
        eye = (cx + diag * 0.4, cy + diag * 1.0, cz + diag * 1.0)
    return CameraState(position=eye, target=(cx, cy, cz), up=up, fov_deg=50.0)


def _initial_slice_for_nifti(inspector_out: Mapping[str, Any]) -> SliceState:
    intensity = inspector_out.get("intensity", {})
    lo = float(intensity.get("p1", -400.0))
    hi = float(intensity.get("p99", 400.0))
    return SliceState(
        axis="axial",
        index=None,
        fraction=0.5,
        window=(lo, hi),
        level=(lo + hi) / 2,
    )


def _build_mask_info(inspector_out: Mapping[str, Any]) -> tuple[MaskInfo, ...]:
    labels = inspector_out.get("labels", [])
    out: list[MaskInfo] = []
    for lbl in labels:
        bbox_world = lbl["bbox_world"]
        out.append(
            MaskInfo(
                mask_id=str(lbl["id"]),
                voxel_count=int(lbl["voxel_count"]),
                centroid_world=tuple(lbl["centroid_world"]),  # type: ignore[arg-type]
                bbox_world=(
                    tuple(bbox_world[0]),  # type: ignore[arg-type]
                    tuple(bbox_world[1]),  # type: ignore[arg-type]
                ),
                best_slices={k: int(v) for k, v in lbl["best_slices"].items()},
            )
        )
    return tuple(out)


def _nifti_affine_as_tuple(
    affine_4x4: list[list[float]],
) -> tuple[tuple[float, ...], ...]:
    """Store inspector affine as a hashable tuple-of-tuples.

    Phase 1/C ``sv_inspect_nifti_mask_pair`` returns ``affine`` and reports
    centroid/bbox in **the same world frame** that affine defines. We trust
    that convention here; an explicit LPS->RAS reflection is not applied at
    Phase 3.1 (no current asset has been observed with non-RAS axcodes after
    nibabel canonicalization). Phase 3.2 may revisit if a counterexample lands.
    """
    return tuple(tuple(row) for row in affine_4x4)


def _nifti_world_bbox(
    shape: list[int], spacing: list[float]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    return (
        (0.0, 0.0, 0.0),
        (shape[0] * spacing[0], shape[1] * spacing[1], shape[2] * spacing[2]),
    )


# --- sv3d_open_scene ------------------------------------------------------


_OPEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "oneOf": [{"required": ["qid"]}, {"required": ["asset_path"]}],
    "properties": {
        "qid": {"type": "string"},
        "asset_path": {"type": "string"},
        "mask_path": {"type": "string"},
        "asset_kind": {"enum": ["mesh", "ply", "nifti", "auto"], "default": "auto"},
        "ttl_seconds": {
            "type": "integer",
            "default": 600,
            "minimum": 30,
            "maximum": 7200,
        },
    },
    "additionalProperties": False,
}


def _build_session_from_inspector(
    *,
    kind: Kind,
    asset_path: str,
    asset_abs: Path,
    mask_abs: Optional[Path],
    ins: Mapping[str, Any],
    ttl: int,
) -> Session:
    """Assemble an immutable Session from an inspector envelope."""
    if kind == "mesh":
        raw_bbox = ins.get("bbox")
        if not raw_bbox:
            raise SVToolError("inspector returned no bbox for mesh")
        bbox: tuple[tuple[float, float, float], tuple[float, float, float]] = (
            tuple(raw_bbox[0]),  # type: ignore[arg-type]
            tuple(raw_bbox[1]),  # type: ignore[arg-type]
        )
        axis_up: AxisUp = _axis_up_for_mesh(asset_path, ins)
        affine: Optional[tuple[tuple[float, ...], ...]] = None
        masks_info: tuple[MaskInfo, ...] = ()
        default_mode: Mode = "surface"
        slice_state = SliceState(
            axis="axial", index=None, fraction=0.5, window=(0.0, 1.0), level=0.5
        )
    else:  # nifti
        shape = ins["shape"]
        spacing = ins["spacing"]
        bbox = _nifti_world_bbox(shape, spacing)
        axis_up = "RAS"
        affine = _nifti_affine_as_tuple(ins["affine_4x4"])
        masks_info = _build_mask_info(ins) if "labels" in ins else ()
        default_mode = "mpr_axial"
        slice_state = _initial_slice_for_nifti(ins)

    camera = _initial_camera(bbox, axis_up)

    return Session(
        session_id=new_session_id(),
        kind=kind,
        asset_path=str(asset_abs),
        mask_path=str(mask_abs) if mask_abs else None,
        bbox=bbox,
        axis_up=axis_up,
        affine_4x4=affine,
        masks_info=masks_info,
        camera=camera,
        mode=default_mode,
        slice=slice_state,
        mask_state=MaskState(
            visible=bool(masks_info),
            mask_id=None,
            colormap="red",
            alpha=0.5,
        ),
        clip_plane=ClipPlaneState(
            enabled=False,
            origin=(0.0, 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            cap=True,
        ),
        opacity=Opacity(),
        created_at=monotonic_now(),
        ttl_seconds=ttl,
    )


def _open_scene_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        qid = args.get("qid")
        asset_path = args.get("asset_path")
        mask_path = args.get("mask_path")
        ttl = int(args.get("ttl_seconds", 600))

        if qid:
            asset_path, mask_from_qid, _ = _resolve_paths_from_qid(qid)
            if mask_path is None:
                mask_path = mask_from_qid
        if not asset_path:
            return {"error": "must provide qid or asset_path"}

        kind = _detect_kind(asset_path)
        asset_abs = _resolve_repo_path(asset_path)
        mask_abs = _resolve_repo_path(mask_path) if mask_path else None

        ins = _inspect_for_open(kind, asset_abs, mask_abs)
        if "error" in ins:
            return {"error": f"inspector failed: {ins['error']}"}

        sess = _build_session_from_inspector(
            kind=kind,
            asset_path=asset_path,
            asset_abs=asset_abs,
            mask_abs=mask_abs,
            ins=ins,
            ttl=ttl,
        )

        try:
            get_store().add(sess)
        except CapacityError as exc:
            return {"error": f"{exc}; close one or wait"}

        return {
            "session_id": sess.session_id,
            "kind": sess.kind,
            "bbox": [list(sess.bbox[0]), list(sess.bbox[1])],
            "axis_up": sess.axis_up,
            "default_mode": sess.mode,
            "masks": [
                {"mask_id": m.mask_id, "voxel_count": m.voxel_count}
                for m in sess.masks_info
            ],
        }
    except SVToolError as exc:
        return {"error": str(exc)}


# --- sv3d_close_scene -----------------------------------------------------


_CLOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {"session_id": {"type": "string"}},
    "additionalProperties": False,
}


def _close_scene_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid:
        return {"error": "session_id is required"}
    removed = get_store().remove(sid)
    if removed:
        return {"closed": True}
    return {"closed": False, "reason": "not found"}


# --- sv3d_get_state -------------------------------------------------------


_GET_STATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["session_id"],
    "properties": {"session_id": {"type": "string"}},
    "additionalProperties": False,
}


def _get_state_handler(args: Mapping[str, Any]) -> Mapping[str, Any]:
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid:
        return {"error": "session_id is required"}
    try:
        s = get_store().get(sid)
    except KeyError:
        return {"error": f"unknown session_id: {sid}"}
    ttl_left = max(0, int(s.ttl_seconds - (monotonic_now() - s.created_at)))
    return {
        "session_id": s.session_id,
        "kind": s.kind,
        "axis_up": s.axis_up,
        "camera": {
            "position": list(s.camera.position),
            "target": list(s.camera.target),
            "up": list(s.camera.up),
            "fov_deg": s.camera.fov_deg,
        },
        "mode": s.mode,
        "mask_state": {
            "visible": s.mask_state.visible,
            "mask_id": s.mask_state.mask_id,
            "colormap": s.mask_state.colormap,
            "alpha": s.mask_state.alpha,
        },
        "slice": {
            "axis": s.slice.axis,
            "index": s.slice.index,
            "fraction": s.slice.fraction,
            "window": list(s.slice.window),
            "level": s.slice.level,
        },
        "clip_plane": {
            "enabled": s.clip_plane.enabled,
            "origin": list(s.clip_plane.origin),
            "normal": list(s.clip_plane.normal),
            "cap": s.clip_plane.cap,
        },
        "opacity": {
            "base": s.opacity.base,
            "mask": s.opacity.mask,
            "clip_cap": s.opacity.clip_cap,
        },
        "ttl_remaining_s": ttl_left,
        "masks_info": [
            {
                "mask_id": m.mask_id,
                "voxel_count": m.voxel_count,
                "centroid_world": list(m.centroid_world),
                "best_slices": dict(m.best_slices),
            }
            for m in s.masks_info
        ],
    }


# --- registration ---------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv3d_open_scene",
            description=(
                "Open a 3D scene by qid or asset_path; returns session_id + "
                "scene metadata."
            ),
            input_schema=_OPEN_SCHEMA,
            handler=_open_scene_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_close_scene",
            description="Close a session and free associated state.",
            input_schema=_CLOSE_SCHEMA,
            handler=_close_scene_handler,
            category="F",
        )
    ),
    register(
        ToolSpec(
            name="sv3d_get_state",
            description="Return a JSON snapshot of session state.",
            input_schema=_GET_STATE_SCHEMA,
            handler=_get_state_handler,
            category="F",
        )
    ),
]
