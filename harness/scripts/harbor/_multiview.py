"""Modality -> ordered list of F-renderer calls for the 2D multi-view baseline.

``plan_views(task, n_views=...)`` returns a list of ``(tool_name, args)`` render
calls whose PNG outputs become the views staged into the (1) container, or
``None`` when the task's modality is not covered in v1 (physics animation,
urdf, raw data). For mesh/pointcloud/molecular modalities the plan is an
``n_views``-step orbit (evenly spaced azimuths at a fixed elevation) rendered
via their respective thumbnail tools. For NIfTI the plan is a set of per-axis
slices whose count/fractions scale with ``n_views`` (see ``_nifti_fractions``).
``seed_pose`` only shifts legacy orbits; frozen-protocol counts ignore it
(their named faces are what ccmcp renders, and the 2026-09-23 ruling froze
them). It remains accepted for callers that pass it.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from scripts.harbor import view_protocol

_MESH_FORMATS = {"obj", "ply", "stl", "glb", "gltf", "off"}
# CAD interchange formats. A `.step`/`.stp` is a B-rep, not a mesh, so the
# stager tessellates it first (scripts/harbor/step_mesh.py) and hands the mesh
# on; planning these as mesh is what puts a routed CAD task on the covered
# branch. `.iges`/`.igs` plan here too and fail closed at the stager — this
# repo has no IGES reader.
_STEP_FORMATS = {"step", "stp", "iges", "igs"}
_POINTCLOUD_FORMATS = {"las", "laz"}
# Molecular structures (biology mmcif; chemistry cif/xyz/pdb) use one distinct
# offline thumbnail for each requested orbit angle.  The historic view-sheet
# renderer is no longer a registered MCP tool, and a single sheet cannot meet
# the cc2d-N contract without falsely claiming N independent observations.
_MOLECULE_FORMATS = {"mmcif", "cif", "pdb", "xyz", "sdf", "mol", "mol2"}
# Formats that all map to the NIfTI slice plan (scale-v1 medical carries
# asset_format="nii.gz"); normalized before dispatch so they don't fall
# through to the uncovered (None) branch.
_NIFTI_FORMATS = {"nifti", "nii", "nii.gz", "nifti.gz"}
_NIFTI_AXES = ("sagittal", "coronal", "axial")
_ELEV = 20.0  # fixed orbit elevation (degrees) for mesh/pointcloud/molecule sequences


def mesh_orbit_azimuths(n: int) -> list[float]:
    """Evenly-spaced orbit azimuths (degrees) starting at 0.0, length n."""
    n = max(1, n)
    return [round(i * 360.0 / n, 6) for i in range(n)]


def _nifti_fractions(n: int) -> list[tuple[str, float]]:
    """(axis, fraction) pairs for an n-view NIfTI slice plan."""
    if n == 1:
        return [("axial", 0.5)]
    if n == 3:
        return [(a, 0.5) for a in _NIFTI_AXES]
    if n == 6:
        return [(a, f) for a in _NIFTI_AXES for f in (0.35, 0.65)]
    # n == 12 (and any other count): 3 axes x 4 fractions, truncated to n.
    fracs = (0.2, 0.4, 0.6, 0.8)
    return [(a, f) for a in _NIFTI_AXES for f in fracs][:n]


def plan_views(
    task: dict,
    *,
    size: int = 512,
    best_slices: Optional[dict[str, Any]] = None,
    n_views: int = 6,
    seed_pose: Optional[dict[str, float]] = None,
) -> Optional[list[tuple[str, dict[str, Any]]]]:
    fmt = (task.get("asset_format") or "").lower()
    domain = (task.get("domain") or "").lower()
    asset_path = task.get("asset_path") or ""
    if not asset_path:
        return None

    if fmt in _NIFTI_FORMATS:
        return _nifti_views(task, size, best_slices, n_views)
    if fmt in _POINTCLOUD_FORMATS:
        return _orbit_views(
            "sv_render_pointcloud_thumbnail", "path", asset_path, size, n_views, seed_pose
        )
    if (fmt in _MESH_FORMATS or fmt in _STEP_FORMATS) and domain != "physics":
        return _mesh_views(asset_path, size, n_views)
    if fmt in _MOLECULE_FORMATS:
        return _molecule_views(asset_path, fmt, size, n_views)
    # physics animation, urdf, raw data (dat/json): deferred.
    return None


def _mesh_views(asset_path: str, size: int, n_views: int) -> list[tuple[str, dict[str, Any]]]:
    """Frozen named-face plan (viewer frame), legacy orbit for other counts.

    ``seed_pose`` is deliberately not consumed: the protocol's named faces are
    what ccmcp renders, and shifting them would re-open the attribution hole the
    protocol closed (2026-09-23 ruling).
    """
    plan = view_protocol.entries(n_views)
    if plan is None:
        return _orbit_views(
            "sv_render_mesh_thumbnail", "path", asset_path, size, n_views, None
        )
    calls: list[tuple[str, dict[str, Any]]] = []
    for kind, value in plan:
        # angle_frame is declared on every entry: the named faces resolve through
        # the renderer's per-frame tables (already viewer-aligned), while the
        # orbit fills are viewer-frame azimuths the renderer shifts by -90 for
        # Z-up assets (obj/ply/stl/off and the tessellated CAD .ply).
        args: dict[str, Any] = {
            "path": asset_path, "angle_frame": "viewer_y_up", "size": size,
        }
        if kind == "named":
            args["view"] = str(value)
        else:
            azimuth, elevation = view_protocol.angles(kind, value)
            args.update(azimuth=azimuth, elevation=elevation)
        calls.append(("sv_render_mesh_thumbnail", args))
    return calls


def _orbit_views(
    tool: str,
    path_key: str,
    asset_path: str,
    size: int,
    n_views: int,
    seed_pose: Optional[dict[str, float]],
) -> list[tuple[str, dict[str, Any]]]:
    base_az = float((seed_pose or {}).get("azimuth", 0.0))
    base_el = float((seed_pose or {}).get("elevation", _ELEV))
    return [
        (tool, {path_key: asset_path, "azimuth": round(base_az + a, 6),
                "elevation": base_el, "size": size})
        for a in mesh_orbit_azimuths(n_views)
    ]


def _molecule_views(
    asset_path: str, fmt: str, size: int, n_views: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Frozen protocol in the *viewer* frame; the renderer rotates on read.

    ``angle_frame="viewer_y_up"`` tells sv_render_molecule_thumbnail to apply the
    proper Y-up -> Z-up rotation and the -90 degree azimuth shift itself, so the
    planner and the sheet-verification script cannot disagree about frames.
    """
    plan = view_protocol.entries(n_views)
    if plan is None:
        return _molecule_orbit_views(asset_path, fmt, size, n_views, None)
    calls: list[tuple[str, dict[str, Any]]] = []
    for kind, value in plan:
        args: dict[str, Any] = {
            "path": asset_path, "format": fmt,
            "angle_frame": "viewer_y_up", "size": size,
        }
        if kind == "named":
            args["view"] = str(value)
        else:
            azimuth, elevation = view_protocol.angles(kind, value)
            args.update(azimuth=azimuth, elevation=elevation)
        calls.append(("sv_render_molecule_thumbnail", args))
    return calls


def _molecule_orbit_views(
    asset_path: str,
    fmt: str,
    size: int,
    n_views: int,
    seed_pose: Optional[dict[str, float]],
) -> list[tuple[str, dict[str, Any]]]:
    """Render a distinct, format-explicit molecular thumbnail per orbit view."""
    base_az = float((seed_pose or {}).get("azimuth", 0.0))
    base_el = float((seed_pose or {}).get("elevation", _ELEV))
    return [
        (
            "sv_render_molecule_thumbnail",
            {
                "path": asset_path,
                "format": fmt,
                "azimuth": round(base_az + azimuth, 6),
                "elevation": base_el,
                "size": size,
            },
        )
        for azimuth in mesh_orbit_azimuths(n_views)
    ]


def _nifti_views(
    task: dict, size: int, best_slices: Optional[dict[str, Any]], n_views: int,
) -> list[tuple[str, dict[str, Any]]]:
    asset_path = task["asset_path"]
    mask_path = task.get("mask_path")
    plane_anchor = _roi_plane_anchor(best_slices)
    calls: list[tuple[str, dict[str, Any]]] = []
    slice_specs = _nifti_slice_specs(task, best_slices, n_views)
    for axis, location_kind, location in slice_specs:
        if mask_path:
            args: dict[str, Any] = {
                "nifti_path": asset_path, "mask_path": mask_path,
                "axis": axis, "size": size, "roi_highlight": True,
            }
            if task.get("mask_id") is not None:
                args["mask_id"] = task["mask_id"]
            if plane_anchor is not None:
                args["plane_anchor_voxel"] = plane_anchor
            tool = "sv_render_nifti_overlay"
        else:
            args = {"path": asset_path, "axis": axis, "size": size}
            tool = "sv_render_nifti_slice"
        if location_kind == "index":
            args["index"] = int(location)
        else:
            args["fraction"] = float(location)
        calls.append((tool, args))
    return calls


def _roi_plane_anchor(best_slices: Optional[dict[str, Any]]) -> list[float] | None:
    """Return validated native-voxel ROI evidence for oblique slice mapping."""
    if not isinstance(best_slices, dict):
        return None
    raw = best_slices.get("__roi_plane_anchor_voxel__")
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    anchor: list[float] = []
    for coordinate in raw:
        if isinstance(coordinate, bool):
            return None
        try:
            value = float(coordinate)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        anchor.append(value)
    return anchor


def _nifti_slice_specs(
    task: dict,
    best_slices: Optional[dict[str, Any]],
    n_views: int,
) -> list[tuple[str, str, float | int]]:
    """Choose label-blind ROI-aware slice positions.

    When mask inspection is available, every axis includes its maximum-area
    slice.  Extent tasks use the mask bounding-box endpoints for 6/12 views.
    Otherwise the historical deterministic fraction plan is retained.
    """
    if not best_slices:
        return [(axis, "fraction", frac) for axis, frac in _nifti_fractions(n_views)]

    shape = best_slices.get("__shape__")
    bbox = best_slices.get("__bbox__")
    fields = (task.get("answer_schema") or {}).get("fields", [])
    extent_task = "extent_vox" in fields
    axis_index = {"sagittal": 0, "coronal": 1, "axial": 2}
    allocations = {
        1: {"axial": 1},
        3: {axis: 1 for axis in _NIFTI_AXES},
        6: {axis: 2 for axis in _NIFTI_AXES},
        12: {axis: 4 for axis in _NIFTI_AXES},
    }.get(n_views)
    if allocations is None:
        return [(axis, "fraction", frac) for axis, frac in _nifti_fractions(n_views)]

    out: list[tuple[str, str, float | int]] = []
    for axis in _NIFTI_AXES:
        count = allocations.get(axis, 0)
        if not count:
            continue
        best = int(best_slices[axis])
        dim = int(shape[axis_index[axis]]) if isinstance(shape, list) and len(shape) >= 3 else None
        if extent_task and count >= 2 and isinstance(bbox, list) and len(bbox) == 2:
            lo = int(bbox[0][axis_index[axis]])
            hi = int(bbox[1][axis_index[axis]])
            candidates = [lo, hi] if count == 2 else [lo, best, hi, best + 1]
        else:
            candidates = [best, best - 1, best + 1, best + 2]
        selected: list[int] = []
        for candidate in candidates:
            if dim is not None:
                candidate = max(0, min(dim - 1, candidate))
            else:
                candidate = max(0, candidate)
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) == count:
                break
        while len(selected) < count:
            # Degenerate one-slice dimensions still need a stable N-view plan.
            selected.append(selected[-1])
        out.extend((axis, "index", index) for index in selected)
    return out[:n_views]
