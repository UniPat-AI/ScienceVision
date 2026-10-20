"""Mesh thumbnail + canonical-view-sheet renderers via pyrender + EGL."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from . import common as C

VIEWS_DEFAULT: tuple[str, ...] = ("front", "back", "left", "right", "top", "iso")

# Bumped whenever a change alters rendered pixels for identical args, so the
# content-addressed render cache cannot serve a pre-fix image.
_MESH_RENDER_VERSION = 3

VIEW_DIRECTIONS_Z_UP: dict[str, tuple[float, float, float]] = {
    "front":  (0.0, -1.0, 0.0),
    "back":   (0.0,  1.0, 0.0),
    "left":   (-1.0, 0.0, 0.0),
    "right":  (1.0,  0.0, 0.0),
    "top":    (0.0,  0.0, 1.0),
    "bottom": (0.0,  0.0, -1.0),
    "iso":    (1.0, -1.0, 1.0),
}

VIEW_DIRECTIONS_Y_UP: dict[str, tuple[float, float, float]] = {
    "front":  (0.0, 0.0, 1.0),
    "back":   (0.0, 0.0, -1.0),
    "left":   (-1.0, 0.0, 0.0),
    "right":  (1.0, 0.0, 0.0),
    "top":    (0.0, 1.0, 0.0),
    "bottom": (0.0, -1.0, 0.0),
    "iso":    (1.0, 1.0, 1.0),
}


def camera_for_view(
    view: str,
    center: np.ndarray,
    diag: float,
    axis_up: str,
    *,
    azimuth: float | None = None,
    elevation: float | None = None,
    viewer_frame: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    up = np.array([0.0, 1.0, 0.0]) if axis_up.upper() == "Y" else np.array([0.0, 0.0, 1.0])
    if azimuth is not None:
        if viewer_frame and axis_up.upper() != "Y":
            # Viewer-frame azimuths are measured from +Z (atan2(d.x, d.z)); the
            # Z-up orbit below measures from +X, so the same physical direction
            # needs the -90 shift. The Y-up branch already IS the viewer frame,
            # and named views resolve through per-frame direction tables and
            # need no shift.
            azimuth = float(azimuth) - 90.0
        az = math.radians(azimuth)
        el = math.radians(elevation if elevation is not None else 20.0)
        if axis_up.upper() == "Y":
            # up = +Y: orbit in XZ plane, elevate toward +Y
            direction = np.array([math.cos(el) * math.sin(az),
                                  math.sin(el),
                                  math.cos(el) * math.cos(az)])
        else:
            # up = +Z: orbit in XY plane, elevate toward +Z
            direction = np.array([math.cos(el) * math.cos(az),
                                  math.cos(el) * math.sin(az),
                                  math.sin(el)])
    else:
        dirs = VIEW_DIRECTIONS_Y_UP if axis_up.upper() == "Y" else VIEW_DIRECTIONS_Z_UP
        direction = np.array(dirs.get(view, dirs["iso"]), dtype=float)
    norm = np.linalg.norm(direction)
    if norm == 0:
        direction = np.array([1.0, 1.0, 1.0])
        norm = math.sqrt(3.0)
    direction = direction / norm
    distance = max(diag * 1.4, 1e-3)
    eye = center + direction * distance
    if abs(np.dot(direction, up)) > 0.95:
        up = np.array([0.0, 1.0, 0.0]) if axis_up.upper() != "Y" else np.array([0.0, 0.0, 1.0])
    return eye.astype(float), center.astype(float), up.astype(float), 45.0


def hex_to_rgba(bg: str | None) -> tuple[float, float, float, float]:
    if bg is None:
        return (0.06, 0.08, 0.10, 1.0)
    s = bg.strip()
    if s.lower() in {"transparent", "none"}:
        return (0.0, 0.0, 0.0, 0.0)
    if s.startswith("#"):
        s = s[1:]
    try:
        if len(s) == 6:
            r, g, b = (int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
            return (r, g, b, 1.0)
        if len(s) == 8:
            r, g, b, a = (int(s[i : i + 2], 16) / 255.0 for i in (0, 2, 4, 6))
            return (r, g, b, a)
    except ValueError:
        pass
    return (0.06, 0.08, 0.10, 1.0)


def render_mesh_image(
    mesh_path: Path,
    view: str,
    size: int,
    bg: str | None,
    *,
    azimuth: float | None = None,
    elevation: float | None = None,
    viewer_frame: bool = False,
) -> dict:
    """Render a single canonical view via pyrender. Returns dict with rgba+camera or error."""
    trimesh = C.try_import("trimesh")
    if trimesh is None:
        return C.missing_dep("trimesh")
    pyrender = C.try_import("pyrender")
    if pyrender is None:
        return C.missing_dep("pyrender")

    C.ensure_headless_init()

    try:
        mesh = trimesh.load(str(mesh_path), force="mesh")
    except Exception as exc:
        return {"error": f"trimesh load failed: {exc}"}
    if mesh is None or not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
        return {"error": "trimesh returned empty mesh"}

    bounds = np.asarray(mesh.bounds, dtype=float)
    center = bounds.mean(axis=0)
    diag = float(np.linalg.norm(bounds[1] - bounds[0]))
    axis_up = "Y" if mesh_path.suffix.lower() in {".glb", ".gltf"} else "Z"
    eye, target, up, fov_deg = camera_for_view(
        view, center, diag, axis_up, azimuth=azimuth, elevation=elevation,
        viewer_frame=viewer_frame,
    )

    bg_rgba = hex_to_rgba(bg)
    try:
        scene = pyrender.Scene(bg_color=bg_rgba, ambient_light=(0.4, 0.4, 0.4))
        py_mesh = pyrender.Mesh.from_trimesh(mesh, smooth=False)
        scene.add(py_mesh)
        forward = target - eye
        fn = np.linalg.norm(forward)
        if fn == 0:
            return {"error": "degenerate camera (eye == target)"}
        forward = forward / fn
        right = np.cross(forward, up)
        rn = np.linalg.norm(right)
        right = np.array([1.0, 0.0, 0.0]) if rn == 0 else right / rn
        true_up = np.cross(right, forward)
        cam_pose = np.eye(4)
        cam_pose[:3, 0] = right
        cam_pose[:3, 1] = true_up
        cam_pose[:3, 2] = -forward
        cam_pose[:3, 3] = eye
        camera = C.build_perspective_camera(
            pyrender, yfov_deg=fov_deg, aspect=1.0, eye=eye, target=target
        )
        scene.add(camera, pose=cam_pose)
        light = pyrender.DirectionalLight(color=np.ones(3), intensity=3.0)
        scene.add(light, pose=cam_pose)
        renderer = pyrender.OffscreenRenderer(viewport_width=size, viewport_height=size)
        try:
            color, _depth = renderer.render(scene)
        finally:
            try:
                renderer.delete()
            except Exception:
                pass
    except Exception as exc:
        return {"error": f"pyrender render failed: {exc}"}

    return {
        "rgba": np.asarray(color),
        "camera_used": {
            "eye": [float(v) for v in eye],
            "target": [float(v) for v in target],
            "up": [float(v) for v in up],
            "fov_deg": float(fov_deg),
        },
    }


def compose_sheet(
    tiles: list[tuple[str, np.ndarray]],
    size_per_tile: int,
) -> tuple[bytes | None, list[dict]]:
    pil_mod = C.try_import("PIL")
    if pil_mod is None:
        return None, []
    from PIL import Image, ImageDraw  # type: ignore

    n = len(tiles)
    cols = min(3, n) if n > 0 else 1
    rows = max(1, math.ceil(n / cols))
    sheet = Image.new("RGB", (cols * size_per_tile, rows * size_per_tile), color=(8, 10, 14))
    draw = ImageDraw.Draw(sheet)
    placement: list[dict] = []
    for i, (view, rgb) in enumerate(tiles):
        r, c = divmod(i, cols)
        x0, y0 = c * size_per_tile, r * size_per_tile
        arr = np.asarray(rgb)
        if arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[..., :3]
        tile_img = Image.fromarray(arr.astype(np.uint8), mode="RGB").resize(
            (size_per_tile, size_per_tile)
        )
        sheet.paste(tile_img, (x0, y0))
        label_y = y0 + size_per_tile - 14
        draw.text((x0 + 4, label_y), view, fill="white")
        placement.append(
            {"view": view, "bbox_px": [x0, y0, x0 + size_per_tile, y0 + size_per_tile]}
        )
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=False)
    return buf.getvalue(), placement


def handle_mesh_thumbnail(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    angle_frame = str(args.get("angle_frame") or "asset_native")
    if angle_frame not in {"asset_native", "viewer_y_up"}:
        return {"error": "angle_frame must be one of: asset_native, viewer_y_up"}
    azimuth = args.get("azimuth")
    elevation = args.get("elevation")
    view = args.get("view")
    # When azimuth is given, the orbit-pose path takes over and the named
    # view enum check no longer applies.
    if azimuth is None and view not in VIEW_DIRECTIONS_Z_UP:
        return {"error": f"view must be one of: {sorted(VIEW_DIRECTIONS_Z_UP)}"}
    if azimuth is not None:
        # Coerce early, before any file I/O or rendering is attempted: the MCP
        # dispatch path does not validate args against the JSON schema, so a
        # non-numeric azimuth/elevation must not raise across the boundary.
        try:
            azimuth = float(azimuth)
            elevation = float(elevation) if elevation is not None else None
        except (TypeError, ValueError):
            return {"error": "azimuth/elevation must be numeric"}
    size = int(args.get("size") or 512)
    bg = args.get("bg")
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}

    cache_args = {"view": view, "azimuth": azimuth, "elevation": elevation, "size": size,
                  "bg": bg, "renderer_version": _MESH_RENDER_VERSION}
    if angle_frame != "asset_native":
        cache_args["angle_frame"] = angle_frame
    sha = C.render_cache_key("sv_render_mesh_thumbnail", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    result = render_mesh_image(path, view or "iso", size, bg, azimuth=azimuth, elevation=elevation,
                               viewer_frame=(angle_frame == "viewer_y_up"))
    if "error" in result:
        return result
    png = C.rgba_to_png(result["rgba"])
    if png is None:
        return C.missing_dep("Pillow")
    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={
            "camera_used": result["camera_used"],
            "view": view,
            "azimuth": azimuth,
            "elevation": elevation,
            **({"angle_frame": angle_frame} if angle_frame != "asset_native" else {}),
        },
    )


def handle_mesh_view_sheet(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    views_arg = args.get("views") or list(VIEWS_DEFAULT)
    if not isinstance(views_arg, list) or not views_arg:
        return {"error": "views must be a non-empty list"}
    for v in views_arg:
        if v not in VIEW_DIRECTIONS_Z_UP:
            return {"error": f"unsupported view: {v!r}"}
    size_per_tile = int(args.get("size_per_tile") or 384)
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}

    cache_args = {"views": views_arg, "size_per_tile": size_per_tile,
                  "renderer_version": _MESH_RENDER_VERSION}
    sha = C.render_cache_key("sv_render_mesh_view_sheet", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    tiles: list[tuple[str, np.ndarray]] = []
    cameras: list[dict] = []
    for v in views_arg:
        result = render_mesh_image(path, v, size_per_tile, bg=None)
        if "error" in result:
            return result
        tiles.append((v, result["rgba"]))
        cameras.append({"view": v, **result["camera_used"]})

    png, placement = compose_sheet(tiles, size_per_tile)
    if png is None:
        return C.missing_dep("Pillow")
    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={"tiles": placement, "cameras": cameras},
    )
