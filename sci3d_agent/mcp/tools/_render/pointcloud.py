"""Point cloud (LAS/LAZ) thumbnail renderer."""

from __future__ import annotations

import io
import math
from typing import Any, Mapping

import numpy as np

from . import common as C

POINTCLOUD_PROJECTIONS = {
    "top": (0, 1),
    "front": (0, 2),
    "side": (1, 2),
    "iso": (0, 1),  # iso uses a dedicated transform below
}


def project_iso(xyz: np.ndarray) -> np.ndarray:
    theta = math.radians(30.0)
    rot = np.array(
        [
            [math.cos(theta), -math.cos(theta), 0.0],
            [math.sin(theta) * 0.5, math.sin(theta) * 0.5, -1.0],
        ]
    )
    return xyz @ rot.T


def orbit_rotate(xyz: np.ndarray, azimuth: float, elevation: float) -> np.ndarray:
    """Rotate points about the +Z (up) axis by ``azimuth`` degrees, then tilt
    by ``elevation`` degrees, returning the rotated N×3 points."""
    az = math.radians(azimuth)
    el = math.radians(elevation)
    ca, sa = math.cos(az), math.sin(az)
    rz = np.array([[ca, -sa, 0.0], [sa, ca, 0.0], [0.0, 0.0, 1.0]])
    ce, se = math.cos(el), math.sin(el)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, ce, -se], [0.0, se, ce]])
    return xyz @ rz.T @ rx.T


def handle_pointcloud_thumbnail(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    view = args.get("view")
    azimuth = args.get("azimuth")
    elevation = args.get("elevation")
    if not raw:
        return {"error": "missing required arg: path"}
    if azimuth is None and view not in POINTCLOUD_PROJECTIONS:
        return {"error": f"view must be one of: {sorted(POINTCLOUD_PROJECTIONS)}"}
    if azimuth is not None:
        # Coerce early, before any file I/O or rendering is attempted: the MCP
        # dispatch path does not validate args against the JSON schema, so a
        # non-numeric azimuth/elevation must not raise across the boundary.
        try:
            azimuth = float(azimuth)
            elevation = float(elevation) if elevation is not None else None
        except (TypeError, ValueError):
            return {"error": "azimuth/elevation must be numeric"}
    max_points = int(args.get("max_points") or 500000)
    point_size = float(args.get("point_size") or 1.5)
    color_by = args.get("color_by") or "height"
    size = int(args.get("size") or 512)
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}

    cache_args = {
        "view": view,
        "azimuth": azimuth,
        "elevation": elevation,
        "max_points": max_points,
        "point_size": point_size,
        "color_by": color_by,
        "size": size,
    }
    sha = C.render_cache_key("sv_render_pointcloud_thumbnail", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    laspy = C.try_import("laspy")
    if laspy is None:
        return C.missing_dep("laspy")
    matplotlib = C.try_import("matplotlib")
    if matplotlib is None:
        return C.missing_dep("matplotlib")
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt  # type: ignore

    try:
        las = laspy.read(str(path))
    except Exception as exc:
        return {"error": f"laspy load failed: {exc}"}
    xyz = np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)])
    points_total = int(xyz.shape[0])
    if points_total == 0:
        return {"error": "point cloud is empty"}
    rng = np.random.default_rng(42)
    if points_total > max_points:
        sel = rng.choice(points_total, size=max_points, replace=False)
        xyz = xyz[sel]
        sample_idx = sel
    else:
        sample_idx = np.arange(points_total)
    points_rendered = int(xyz.shape[0])

    colors: Any
    field_names = las.point_format.dimension_names
    if color_by == "rgb" and all(f in field_names for f in ("red", "green", "blue")):
        r = np.asarray(las.red)[sample_idx] / 65535.0
        g = np.asarray(las.green)[sample_idx] / 65535.0
        b = np.asarray(las.blue)[sample_idx] / 65535.0
        colors = np.clip(np.column_stack([r, g, b]), 0.0, 1.0)
    elif color_by == "intensity" and "intensity" in field_names:
        inten = np.asarray(las.intensity)[sample_idx].astype(np.float32)
        m = float(inten.max() or 1.0)
        colors = inten / m
    else:
        colors = xyz[:, 2]

    if azimuth is not None:
        rotated = orbit_rotate(xyz, azimuth, elevation if elevation is not None else 20.0)
        pts2d = rotated[:, [0, 2]]  # look along +Y after orbit
    elif view == "iso":
        pts2d = project_iso(xyz)
    else:
        ix, iy = POINTCLOUD_PROJECTIONS[view]
        pts2d = xyz[:, [ix, iy]]

    dpi = 100
    fig = plt.figure(figsize=(size / dpi, size / dpi), dpi=dpi)
    ax = fig.add_subplot(111)
    ax.set_facecolor((0.03, 0.04, 0.06))
    cmap = "viridis" if color_by == "height" else None
    if isinstance(colors, np.ndarray) and colors.ndim == 2:
        ax.scatter(pts2d[:, 0], pts2d[:, 1], c=colors, s=point_size, marker=".")
    else:
        ax.scatter(pts2d[:, 0], pts2d[:, 1], c=colors, s=point_size, marker=".", cmap=cmap)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout(pad=0.0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=(0.03, 0.04, 0.06), dpi=dpi)
    plt.close(fig)
    png = buf.getvalue()

    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={
            "view": view,
            "azimuth": azimuth,
            "elevation": elevation,
            "color_by": color_by,
            "points_total": points_total,
            "points_rendered": points_rendered,
        },
    )
