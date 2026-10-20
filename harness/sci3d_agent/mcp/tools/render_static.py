"""Static one-shot renderers (Category D, 7 tools).

These handlers are pure stateless functions: input args + on-disk asset
(by mtime) -> deterministic PNG envelope. They cache rendered PNGs under
``render_cache_dir()`` keyed by sha256 of (tool, sorted-json args, input
mtime_ns); a cache hit returns the existing PNG without re-rendering.

Axis convention (LOCKED — inherited from Category C / inspect.py):

    In numpy/nibabel array order ``arr[i, j, k]``::

        sagittal = axis 0   (varies L<->R)
        coronal  = axis 1   (varies P<->A)
        axial    = axis 2   (varies I<->S)

    A slice at index ``s`` along ``axial`` is ``arr[:, :, s]``. This
    matches ``sv_inspect_nifti_mask_pair.labels[*].best_slices.{axis}``
    so the M3D-RefSeg trajectory's slice indices flow straight through.

Implementation is split across ``_render/{common,mesh,nifti,pointcloud}.py``
to keep each module focused. This file owns: module-level GL env setup,
the cross-tool QA-record-summary dispatcher, and tool registration.

All heavy dependencies (pyrender, nibabel, matplotlib, laspy, PIL) are
imported lazily inside handlers. Missing deps return
``{"error": "dependency missing: <lib>"}`` rather than raising.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec, register

from ._render import common as _common
from ._render import mesh as _mesh
from ._render import molecule as _molecule
from ._render import nifti as _nifti
from ._render import pointcloud as _pointcloud

# Headless GL env must be set BEFORE pyrender / PyOpenGL imports anywhere.
os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
os.environ.setdefault("PYRENDER_OFFSCREEN", "1")

logger = logging.getLogger(__name__)

# Re-export the SPEC §5 D-conventions threshold for callers/tests.
INLINE_THRESHOLD_BYTES = _common.INLINE_THRESHOLD_BYTES


# ---------------------------------------------------------------------------
# Backward-compatible test surface (private helpers re-exported under their
# pre-split names). Tests import these from ``render_static`` directly.
# ---------------------------------------------------------------------------


def _try_import(name: str):  # noqa: D401 - thin shim
    return _common.try_import(name)


def _missing_dep(name: str) -> dict:
    return _common.missing_dep(name)


def _resolve_path(p: str | Path) -> Path:
    return _common.resolve_path(p)


def _render_cache_key(tool_name: str, args: Mapping[str, Any], input_paths):
    return _common.render_cache_key(tool_name, args, input_paths)


def _png_size(png_bytes: bytes):
    return _common.png_size(png_bytes)


def _build_image_envelope(png_bytes: bytes, *, cache_sha: str, extras: dict | None = None) -> dict:
    return _common.build_image_envelope(png_bytes, cache_sha=cache_sha, extras=extras)


def _cached_envelope_from_disk(cache_sha: str) -> dict | None:
    return _common.cached_envelope_from_disk(cache_sha)


def _rgba_to_png(rgba):
    return _common.rgba_to_png(rgba)


def _apply_colormap(gray, name: str):
    return _common.apply_colormap(gray, name)


def _apply_window(arr, window):
    return _common.apply_window(arr, window)


def _slice_index_from_args(arr, axis_name: str, index, fraction):
    return _common.slice_index_from_args(arr, axis_name, index, fraction)


def _orient_for_display(img2d):
    return _common.orient_for_display(img2d)


def _hex_to_rgba(bg: str | None):
    return _mesh.hex_to_rgba(bg)


def _project_iso(xyz):
    return _pointcloud.project_iso(xyz)


def _ensure_headless_init() -> None:
    _common.ensure_headless_init()


# Test hook to reset the one-shot init guard
def _reset_headless_init() -> None:
    _common.reset_headless_init_for_tests()


# Render cache dir accessor (proxied so tests that set SV_RENDER_CACHE work)
def render_cache_dir() -> Path:
    from sci3d_agent.mcp.runtime.paths import render_cache_dir as _real

    return _real()


# Properties that tests poke directly
class _ModuleState:
    """Adapter so test code can flip _HEADLESS_INIT_DONE on the module."""


def __getattr__(name: str):  # PEP 562
    if name == "_HEADLESS_INIT_DONE":
        return _common._HEADLESS_INIT_DONE
    raise AttributeError(name)


def _set_headless_init_done(value: bool) -> None:
    _common._HEADLESS_INIT_DONE = value


# ---------------------------------------------------------------------------
# Tool wrappers: thin delegates so we can replace the impl per-call (for
# mocking) without touching the registered ToolSpec.handler reference.
# ---------------------------------------------------------------------------


def _handle_render_mesh_thumbnail(args: Mapping[str, Any]) -> dict:
    return _mesh.handle_mesh_thumbnail(args)


def _handle_render_mesh_view_sheet(args: Mapping[str, Any]) -> dict:
    return _mesh.handle_mesh_view_sheet(args)


def _handle_render_nifti_slice(args: Mapping[str, Any]) -> dict:
    return _nifti.handle_nifti_slice(args)


def _handle_render_nifti_overlay(args: Mapping[str, Any]) -> dict:
    return _nifti.handle_nifti_overlay(args)


def _handle_render_nifti_mip(args: Mapping[str, Any]) -> dict:
    return _nifti.handle_nifti_mip(args)


def _handle_render_pointcloud_thumbnail(args: Mapping[str, Any]) -> dict:
    return _pointcloud.handle_pointcloud_thumbnail(args)


def _handle_render_molecule_thumbnail(args: Mapping[str, Any]) -> dict:
    return _molecule.handle_molecule_thumbnail(args)


# ---------------------------------------------------------------------------
# QA record summary dispatcher (cross-tool: uses the live REGISTRY for both
# catalog.sv_get_matched_record and inspect.sv_inspect_nifti_mask_pair so
# tests can swap fakes in without monkeypatching imports).
# ---------------------------------------------------------------------------


def _lookup_handler(tool_name: str):
    spec = REGISTRY.get(tool_name)
    return spec.handler if spec is not None else None


def _record_for_qid(qid: str) -> dict:
    handler = _lookup_handler("sv_get_matched_record")
    if handler is None:
        return {"error": "sv_get_matched_record handler not registered"}
    out = handler({"qid": qid})
    if not isinstance(out, dict):
        return {"error": "sv_get_matched_record returned non-dict"}
    return dict(out)


def _caption_for_record(rec: Mapping[str, Any]) -> str:
    q = rec.get("question") or ""
    scene = rec.get("scene_or_case_id") or ""
    base = q.strip()
    if scene:
        base = f"[{scene}] {base}" if base else f"[{scene}]"
    if len(base) > 240:
        base = base[:237] + "..."
    return base


def _coerce_mask_id(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _dispatch_nifti_summary(rec: dict, size: int) -> dict:
    nifti_repo = rec.get("path_repo")
    if not nifti_repo:
        return {"error": "record missing path_repo"}
    nifti_path = _resolve_path(nifti_repo)
    mask_block = rec.get("mask")
    if isinstance(mask_block, dict) and mask_block.get("mask_path_repo"):
        mask_repo = mask_block["mask_path_repo"]
        mask_id = _coerce_mask_id(mask_block.get("mask_id"))
        inspect_handler = _lookup_handler("sv_inspect_nifti_mask_pair")
        axial_idx: int | None = None
        if inspect_handler is not None:
            info = inspect_handler(
                {"nifti_path": str(nifti_path), "mask_path": str(_resolve_path(mask_repo))}
            )
            if isinstance(info, dict) and "labels" in info and info["labels"]:
                labels = info["labels"]
                if mask_id is not None:
                    for lbl in labels:
                        if int(lbl.get("id", -1)) == mask_id:
                            axial_idx = int(lbl["best_slices"]["axial"])
                            break
                if axial_idx is None:
                    axial_idx = int(labels[0]["best_slices"]["axial"])
        overlay_args: dict = {
            "nifti_path": str(nifti_path),
            "mask_path": str(_resolve_path(mask_repo)),
            "axis": "axial",
            "size": size,
        }
        if axial_idx is not None:
            overlay_args["index"] = axial_idx
        else:
            overlay_args["fraction"] = 0.5
        if mask_id is not None:
            overlay_args["mask_id"] = mask_id
        result = dict(_handle_render_nifti_overlay(overlay_args))
        result["renderer_used"] = "sv_render_nifti_overlay"
        result["source_paths"] = {
            "nifti": str(nifti_path),
            "mask": str(_resolve_path(mask_repo)),
        }
        return result

    slice_args = {
        "path": str(nifti_path),
        "axis": "axial",
        "fraction": 0.5,
        "size": size,
    }
    result = dict(_handle_render_nifti_slice(slice_args))
    result["renderer_used"] = "sv_render_nifti_slice"
    result["source_paths"] = {"nifti": str(nifti_path)}
    return result


def _dispatch_mesh_summary(rec: dict, size: int) -> dict:
    repo_rel = rec.get("path_repo")
    if not repo_rel:
        return {"error": "record missing path_repo"}
    path = _resolve_path(repo_rel)
    args = {"path": str(path), "size_per_tile": max(64, size // 2)}
    result = dict(_handle_render_mesh_view_sheet(args))
    result["renderer_used"] = "sv_render_mesh_view_sheet"
    result["source_paths"] = {"mesh": str(path)}
    return result


def _handle_render_qa_record_summary(args: Mapping[str, Any]) -> dict:
    qid = args.get("qid")
    if not isinstance(qid, str) or not qid:
        return {"error": "qid is required"}
    size = int(args.get("size") or 512)

    rec_envelope = _record_for_qid(qid)
    if "error" in rec_envelope:
        return rec_envelope
    record = rec_envelope.get("record")
    if not isinstance(record, dict):
        return {"error": "matched record missing"}

    asset_kind = record.get("asset_kind")
    if asset_kind in {"mesh", "ply"}:
        result = _dispatch_mesh_summary(record, size)
    elif asset_kind == "nifti":
        result = _dispatch_nifti_summary(record, size)
    else:
        return {"error": f"asset_kind not yet supported: {asset_kind!r}"}

    if "error" in result:
        return result
    result["asset_kind"] = asset_kind
    result["caption"] = _caption_for_record(record)
    result["qid"] = qid
    return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _load_schema(name: str) -> dict:
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / f"{name}.json"
    try:
        with schema_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"type": "object"}


def _spec(name: str, description: str, handler) -> ToolSpec:
    return register(
        ToolSpec(
            name=name,
            description=description,
            input_schema=_load_schema(name),
            handler=handler,
            category="D",
        )
    )


TOOLS: list[ToolSpec] = [
    _spec(
        "sv_render_mesh_thumbnail",
        "Single-view mesh thumbnail (PNG) via pyrender + EGL.",
        _handle_render_mesh_thumbnail,
    ),
    _spec(
        "sv_render_mesh_view_sheet",
        "2x3 canonical-view sheet for a mesh; one call answers most ScanQA spatial questions.",
        _handle_render_mesh_view_sheet,
    ),
    _spec(
        "sv_render_nifti_slice",
        "Single NIfTI slice as PNG (axis = axial/coronal/sagittal; index or fraction).",
        _handle_render_nifti_slice,
    ),
    _spec(
        "sv_render_nifti_overlay",
        "NIfTI slice + per-label mask overlay (M3D-RefSeg core).",
        _handle_render_nifti_overlay,
    ),
    _spec(
        "sv_render_nifti_mip",
        "NIfTI maximum/minimum/mean intensity projection along an axis.",
        _handle_render_nifti_mip,
    ),
    _spec(
        "sv_render_pointcloud_thumbnail",
        "LAS/LAZ point-cloud thumbnail (top/front/side/iso) colored by height/intensity/rgb.",
        _handle_render_pointcloud_thumbnail,
    ),
    _spec(
        "sv_render_molecule_thumbnail",
        "Molecular structure (mmcif/cif/pdb/xyz) orbit thumbnail; adaptive "
        "ball-and-stick (small) or CPK spheres (large), element-colored.",
        _handle_render_molecule_thumbnail,
    ),
    _spec(
        "sv_render_qa_record_summary",
        "Dispatch by asset_kind: look up qid then render the right kind of summary PNG.",
        _handle_render_qa_record_summary,
    ),
]
