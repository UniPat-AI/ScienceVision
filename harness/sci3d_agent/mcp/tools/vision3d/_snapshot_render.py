"""Renderer-dispatch helpers for the F-snapshot tools.

These functions turn immutable ``Session`` state into PNG bytes (or a
graceful ``renderer_skipped`` envelope when GL deps are missing). They may
import camera helpers from ``_snapshot_camera`` (one-way dependency); the
camera module never imports back from here.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Mapping, Optional

from sci3d_agent.mcp.tools._render import common as _common
from sci3d_agent.mcp.tools._render import mesh as _mesh
from sci3d_agent.mcp.tools._render import nifti as _nifti
from sci3d_agent.mcp.tools.vision3d._snapshot_camera import (
    _base_echo,
    _camera_dict,
)
from sci3d_agent.mcp.tools.vision3d._types import (
    CameraState,
    Session,
)

log = logging.getLogger("sciencevision.vision3d.snapshot")


# ---------------------------------------------------------------------------
# Dependency probes + skipped envelope
# ---------------------------------------------------------------------------


def _check_gl_deps() -> Optional[str]:
    """Return None if GL deps are importable; else a missing-dep message."""
    if _common.try_import("PIL") is None:
        return "dependency missing: Pillow"
    return None


def _check_mesh_deps() -> Optional[str]:
    if _common.try_import("trimesh") is None:
        return "dependency missing: trimesh"
    if _common.try_import("pyrender") is None:
        return "dependency missing: pyrender"
    return _check_gl_deps()


def _check_nifti_deps() -> Optional[str]:
    if _common.try_import("nibabel") is None:
        return "dependency missing: nibabel"
    return _check_gl_deps()


def _would_render_text(session: Session, renderer: str) -> str:
    bits = [f"renderer={renderer}", f"kind={session.kind}", f"mode={session.mode}"]
    if session.kind == "nifti":
        bits.append(f"slice={session.slice.axis}:{session.slice.index}")
        if session.mask_state.visible:
            bits.append(f"mask={session.mask_state.mask_id}")
    return " ".join(bits)


def _skipped_envelope(
    session: Session,
    *,
    width: int,
    height: int,
    renderer: str,
    reason: str,
) -> dict[str, Any]:
    out = _base_echo(session, width=width, height=height)
    out["renderer_skipped"] = reason
    out["would_render"] = _would_render_text(session, renderer)
    out["content_type"] = "image/png"
    return out


# --- mesh dispatch -------------------------------------------------------


def _render_mesh(
    session: Session,
    *,
    width: int,
    height: int,
    bg: str,
    camera_override: Optional[CameraState] = None,
) -> dict[str, Any]:
    missing = _check_mesh_deps()
    if missing is not None:
        return _skipped_envelope(
            session,
            width=width,
            height=height,
            renderer="mesh_surface",
            reason=missing,
        )
    cam = camera_override or session.camera
    out = _render_mesh_with_camera(session, cam, width=width, height=height, bg=bg)
    if "error" in out:
        return _skipped_envelope(
            session,
            width=width,
            height=height,
            renderer="mesh_surface",
            reason=f"render failed: {out['error']}",
        )
    return out


def _render_mesh_with_camera(  # pragma: no cover - requires GL
    session: Session,
    cam: CameraState,
    *,
    width: int,
    height: int,
    bg: str,
) -> dict[str, Any]:
    """Real pyrender call. Excluded from coverage on GL-less dev boxes."""
    trimesh = _common.try_import("trimesh")
    pyrender = _common.try_import("pyrender")
    if trimesh is None or pyrender is None:
        return {"error": "deps missing"}

    import numpy as np  # type: ignore

    _common.ensure_headless_init()
    try:
        mesh = trimesh.load(session.asset_path, force="mesh")
    except Exception as exc:
        return {"error": f"trimesh load failed: {exc}"}
    if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
        return {"error": "trimesh returned empty mesh"}

    bg_rgba = _mesh.hex_to_rgba(bg)
    try:
        scene = pyrender.Scene(bg_color=bg_rgba, ambient_light=(0.4, 0.4, 0.4))
        py_mesh = pyrender.Mesh.from_trimesh(mesh, smooth=False)
        scene.add(py_mesh)
        eye = np.asarray(cam.position, dtype=float)
        target = np.asarray(cam.target, dtype=float)
        up_v = np.asarray(cam.up, dtype=float)
        forward = target - eye
        fn = float(np.linalg.norm(forward))
        if fn == 0.0:
            return {"error": "degenerate camera (eye == target)"}
        forward = forward / fn
        right = np.cross(forward, up_v)
        rn = float(np.linalg.norm(right))
        right = np.array([1.0, 0.0, 0.0]) if rn == 0.0 else right / rn
        true_up = np.cross(right, forward)
        cam_pose = np.eye(4)
        cam_pose[:3, 0] = right
        cam_pose[:3, 1] = true_up
        cam_pose[:3, 2] = -forward
        cam_pose[:3, 3] = eye
        camera = _common.build_perspective_camera(
            pyrender, yfov_deg=cam.fov_deg,
            aspect=float(width) / float(height), eye=eye, target=target,
        )
        scene.add(camera, pose=cam_pose)
        light = pyrender.DirectionalLight(color=np.ones(3), intensity=3.0)
        scene.add(light, pose=cam_pose)
        renderer = pyrender.OffscreenRenderer(viewport_width=width, viewport_height=height)
        try:
            color, _ = renderer.render(scene)
        finally:
            try:
                renderer.delete()
            except Exception:
                pass
    except Exception as exc:
        return {"error": f"pyrender render failed: {exc}"}

    png = _common.rgba_to_png(color)
    if png is None:
        return {"error": "Pillow missing"}
    sha = _common.render_cache_key(
        "sv3d_render",
        {
            "sid": session.session_id,
            "cam": _camera_dict(cam),
            "mode": session.mode,
            "w": width,
            "h": height,
            "bg": bg,
            "rng": session.rng_seed,
            "near_far_policy": "distance_scaled_v1",
        },
        [],
    )
    envelope = _common.build_image_envelope(png, cache_sha=sha)
    envelope.update(_base_echo(session, width=width, height=height))
    envelope["camera"] = _camera_dict(cam)
    envelope["renderer_used"] = "mesh_surface" if session.mode == "surface" else "mesh_points"
    return envelope


# --- nifti dispatch ------------------------------------------------------


def _render_nifti(
    session: Session,
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    missing = _check_nifti_deps()
    if missing is not None:
        renderer = "nifti_overlay" if (session.mask_state.visible and session.mask_path) else "nifti_slice"
        return _skipped_envelope(
            session,
            width=width,
            height=height,
            renderer=renderer,
            reason=missing,
        )

    axis = _axis_for_mode(session)
    if session.mask_state.visible and session.mask_path:
        primitive_args: dict[str, Any] = {
            "nifti_path": session.asset_path,
            "mask_path": session.mask_path,
            "axis": axis,
            "alpha": float(session.mask_state.alpha),
            "colormap": session.mask_state.colormap if session.mask_state.colormap != "red" else "jet",
            "size": max(width, height),
        }
        if session.mask_state.mask_id is not None:
            primitive_args["mask_id"] = session.mask_state.mask_id
        _attach_slice_locator(primitive_args, session)
        if session.slice.window != (0.0, 0.0):
            primitive_args["window"] = [session.slice.window[0], session.slice.window[1]]
        out = _nifti.handle_nifti_overlay(primitive_args)
        renderer_used = "nifti_overlay"
    else:
        primitive_args = {
            "path": session.asset_path,
            "axis": axis,
            "size": max(width, height),
        }
        if session.slice.window != (0.0, 0.0):
            primitive_args["window"] = [session.slice.window[0], session.slice.window[1]]
        _attach_slice_locator(primitive_args, session)
        out = _nifti.handle_nifti_slice(primitive_args)
        renderer_used = "nifti_slice"

    if "error" in out:
        return _skipped_envelope(
            session,
            width=width,
            height=height,
            renderer=renderer_used,
            reason=f"render failed: {out['error']}",
        )

    result = dict(out)
    result.update(_base_echo(session, width=width, height=height))
    result["renderer_used"] = renderer_used
    return result


def _axis_for_mode(session: Session) -> str:
    """Map the session mode (and slice.axis fallback) to a primitive axis."""
    mode = session.mode
    if mode == "mpr_axial":
        return "axial"
    if mode == "mpr_sagittal":
        return "sagittal"
    if mode == "mpr_coronal":
        return "coronal"
    return session.slice.axis


def _attach_slice_locator(args: dict[str, Any], session: Session) -> None:
    """Add either ``index`` or ``fraction`` to a primitive-args dict."""
    if session.slice.index is not None:
        args["index"] = int(session.slice.index)
    elif session.slice.fraction is not None:
        args["fraction"] = float(session.slice.fraction)


def _render_nifti_fallback_volume(
    session: Session, *, width: int, height: int
) -> dict[str, Any]:
    """Volume rendering not implemented in _render/nifti.py: fall back to axial MPR."""
    from dataclasses import replace

    fallback = replace(session, mode="mpr_axial")
    return _render_nifti(fallback, width=width, height=height)


# --- canonical-view rendering --------------------------------------------


def _compose_tile_sheet(
    tiles: list[tuple[str, bytes]],
    *,
    width: int,
    height: int,
) -> Optional[bytes]:
    """Composite per-view PNGs into a single labeled sheet via PIL."""
    pil_mod = _common.try_import("PIL")
    if pil_mod is None:
        return None
    from PIL import Image, ImageDraw  # type: ignore
    import io

    n = len(tiles)
    if n == 0:
        return None
    cols = min(3, n)
    rows = max(1, math.ceil(n / cols))
    sheet = Image.new("RGB", (cols * width, rows * height), color=(8, 10, 14))
    draw = ImageDraw.Draw(sheet)
    for i, (view, png_bytes) in enumerate(tiles):
        r, c = divmod(i, cols)
        x0, y0 = c * width, r * height
        try:
            tile_img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            if tile_img.size != (width, height):
                tile_img = tile_img.resize((width, height))
            sheet.paste(tile_img, (x0, y0))
        except Exception as exc:
            log.debug("tile %s decode failed: %s", view, exc)
        label_y = y0 + height - 14
        draw.text((x0 + 4, label_y), view, fill="white")
    out_buf = io.BytesIO()
    sheet.save(out_buf, format="PNG", optimize=False)
    return out_buf.getvalue()


def _decode_tile_png_b64(item: Mapping[str, Any]) -> Optional[bytes]:
    """Pull raw PNG bytes from a per-view result envelope when possible."""
    b64 = item.get("image_png_base64")
    if isinstance(b64, str):
        import base64

        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    return None
