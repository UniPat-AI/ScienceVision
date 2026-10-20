"""NIfTI slice / overlay / MIP renderers."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from . import common as C

_PROJECTIONS = {"max": np.max, "min": np.min, "mean": np.mean}


def handle_nifti_slice(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    axis_name = args.get("axis")
    if not raw:
        return {"error": "missing required arg: path"}
    if axis_name not in C.AXIS_INDEX:
        return {"error": f"axis must be one of: {sorted(C.AXIS_INDEX)}"}
    colormap = args.get("colormap") or "gray"
    window = args.get("window")
    size = int(args.get("size") or 512)
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}

    cache_args = {
        "axis": axis_name,
        "index": args.get("index"),
        "fraction": args.get("fraction"),
        "window": list(window) if window is not None else None,
        "colormap": colormap,
        "size": size,
    }
    sha = C.render_cache_key("sv_render_nifti_slice", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    arr, img = C.load_nifti_array(path)
    if arr is None:
        return img
    idx, err = C.slice_index_from_args(arr, axis_name, args.get("index"), args.get("fraction"))
    if err is not None:
        return {"error": err}
    sl = C.take_slice(arr, axis_name, idx)
    gray, window_used = C.apply_window(sl, window)
    rgb = C.apply_colormap(gray, colormap)
    rgb = C.orient_for_display(rgb)

    try:
        spacing = float(img.header.get_zooms()[C.AXIS_INDEX[axis_name]])
    except Exception:
        spacing = 1.0

    png = C.rgba_to_png(rgb)
    if png is None:
        return C.missing_dep("Pillow")
    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={
            "axis": axis_name,
            "slice_index": int(idx),
            "voxel_spacing_mm": spacing,
            "window_used": list(window_used),
        },
    )


def handle_nifti_overlay(args: Mapping[str, Any]) -> dict:
    nifti_raw = args.get("nifti_path")
    mask_raw = args.get("mask_path")
    axis_name = args.get("axis")
    if not nifti_raw or not mask_raw:
        return {"error": "missing required args: nifti_path, mask_path"}
    if axis_name not in C.AXIS_INDEX:
        return {"error": f"axis must be one of: {sorted(C.AXIS_INDEX)}"}
    nifti_path = C.resolve_path(nifti_raw)
    mask_path = C.resolve_path(mask_raw)
    if not nifti_path.exists():
        return {"error": f"file not found: {nifti_path}"}
    if not mask_path.exists():
        return {"error": f"file not found: {mask_path}"}

    raw_mask_id = args.get("mask_id")
    mask_id_norm: int | None = None
    if raw_mask_id is not None:
        try:
            mask_id_norm = int(raw_mask_id)
        except (TypeError, ValueError):
            return {"error": f"mask_id must be an integer, got {raw_mask_id!r}"}
    alpha = float(args.get("alpha") if args.get("alpha") is not None else 0.4)
    if not 0.0 <= alpha <= 1.0:
        return {"error": "alpha must be in [0, 1]"}
    colormap = args.get("colormap") or "jet"
    window = args.get("window")
    size = int(args.get("size") or 512)

    cache_args = {
        "axis": axis_name,
        "index": args.get("index"),
        "fraction": args.get("fraction"),
        "mask_id": mask_id_norm,
        "alpha": alpha,
        "colormap": colormap,
        "window": list(window) if window is not None else None,
        "size": size,
    }
    sha = C.render_cache_key(
        "sv_render_nifti_overlay", cache_args, [nifti_path, mask_path]
    )
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    base_arr, base_img = C.load_nifti_array(nifti_path)
    if base_arr is None:
        return base_img
    mask_arr, _mask_img = C.load_nifti_array(mask_path)
    if mask_arr is None:
        return _mask_img
    if base_arr.shape != mask_arr.shape:
        return {
            "error": f"shape mismatch base={tuple(base_arr.shape)} mask={tuple(mask_arr.shape)}"
        }

    idx, err = C.slice_index_from_args(
        base_arr, axis_name, args.get("index"), args.get("fraction")
    )
    if err is not None:
        return {"error": err}

    int_mask = mask_arr.astype(np.int64)
    ids_present = sorted(int(v) for v in np.unique(int_mask) if int(v) != 0)
    base_slice = C.take_slice(base_arr, axis_name, idx)
    mask_slice = C.take_slice(int_mask, axis_name, idx)

    if mask_id_norm is not None:
        binary = mask_slice == mask_id_norm
    else:
        binary = mask_slice > 0
    mask_pixel_count = int(binary.sum())

    gray, window_used = C.apply_window(base_slice, window)
    base_rgb = np.stack([gray, gray, gray], axis=-1).astype(np.float32)
    mask_norm = (mask_slice.astype(np.float32) / max(1, mask_slice.max())) * 255.0
    if mask_id_norm is not None:
        mask_norm = binary.astype(np.float32) * 255.0
    colored = C.apply_colormap(mask_norm.astype(np.uint8), colormap).astype(np.float32)
    alpha_map = (binary.astype(np.float32) * alpha)[..., None]
    out = base_rgb * (1.0 - alpha_map) + colored * alpha_map
    out = np.clip(out, 0, 255).astype(np.uint8)
    out = C.orient_for_display(out)

    try:
        spacing = float(base_img.header.get_zooms()[C.AXIS_INDEX[axis_name]])
    except Exception:
        spacing = 1.0

    png = C.rgba_to_png(out)
    if png is None:
        return C.missing_dep("Pillow")
    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={
            "axis": axis_name,
            "slice_index": int(idx),
            "voxel_spacing_mm": spacing,
            "window_used": list(window_used),
            "mask_pixel_count": mask_pixel_count,
            "mask_ids_present": ids_present,
            "alpha": alpha,
        },
    )


def handle_nifti_mip(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    axis_name = args.get("axis")
    projection = args.get("projection")
    if not raw:
        return {"error": "missing required arg: path"}
    if axis_name not in C.AXIS_INDEX:
        return {"error": f"axis must be one of: {sorted(C.AXIS_INDEX)}"}
    if projection not in _PROJECTIONS:
        return {"error": f"projection must be one of: {sorted(_PROJECTIONS)}"}
    colormap = args.get("colormap") or "gray"
    window = args.get("window")
    size = int(args.get("size") or 512)
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}

    cache_args = {
        "axis": axis_name,
        "projection": projection,
        "window": list(window) if window is not None else None,
        "colormap": colormap,
        "size": size,
    }
    sha = C.render_cache_key("sv_render_nifti_mip", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    arr, _img = C.load_nifti_array(path)
    if arr is None:
        return _img
    axis = C.AXIS_INDEX[axis_name]
    reduced = _PROJECTIONS[projection](arr, axis=axis)
    gray, window_used = C.apply_window(reduced, window)
    rgb = C.apply_colormap(gray, colormap)
    rgb = C.orient_for_display(rgb)

    png = C.rgba_to_png(rgb)
    if png is None:
        return C.missing_dep("Pillow")
    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={
            "axis": axis_name,
            "projection": projection,
            "window_used": list(window_used),
        },
    )
