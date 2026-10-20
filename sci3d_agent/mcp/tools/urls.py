"""Category B — Viewer URL builders.

4 tools that translate `asset_path` / dataset / qid / sample inputs into deep links
to the static viewer pages on `<SV_VIEWER_BASE>/viewer/...`.

All handlers are pure: they take a Mapping, return a new dict envelope (`{url, ...}`
or `{error: ...}`). No filesystem writes, no LLM calls, no rendering.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.paths import repo_root, viewer_base

# --- viewer-page contract (mirrors SPEC §3) --------------------------------

_VIEWER_ENUM: tuple[str, ...] = (
    "gltf",
    "niivue",
    "cornerstone",
    "molstar",
    "pointcloud",
    "gdsii",
    "urdf",
    "occt",
    "usda",
    "usdz",
    "field3d",
    "bandplot",
    "mpbctl",
    "saturn",
    "photonics",
)

_MOLSTAR_TYPES: tuple[str, ...] = ("mmcif", "pdb", "sdf", "mol2")
_URDF_FORMATS: tuple[str, ...] = ("urdf", "sdf")

_MATCH3D_DATASETS: tuple[str, ...] = (
    "scanqa",
    "sqa3d",
    "m3d-refseg",
    "m3d-seg-0005",
    "embodiedscan-demo",
)

# Per-viewer page contract: which params it consumes.
# `file_param` is the name of the path-bearing query key (file vs series vs path).
# `accepts` is the set of *additional* optional params the page actually reads.
_VIEWER_CONTRACT: dict[str, dict[str, Any]] = {
    "gltf": {"file_param": "file", "accepts": {"cam", "opacity", "slice"}},
    "niivue": {"file_param": "file", "accepts": {"overlay"}},
    "cornerstone": {"file_param": "series", "accepts": set()},
    "molstar": {"file_param": "file", "accepts": {"type"}, "required": {"type"}},
    "pointcloud": {"file_param": "file", "accepts": set()},
    "gdsii": {"file_param": "file", "accepts": set()},
    "urdf": {"file_param": "file", "accepts": {"format"}, "required": {"format"}},
    "occt": {"file_param": "file", "accepts": set()},
    "usda": {"file_param": "file", "accepts": set()},
    "usdz": {"file_param": "file", "accepts": set()},
    "field3d": {"file_param": "file", "accepts": set()},
    "bandplot": {"file_param": "file", "accepts": set()},
    "mpbctl": {"file_param": "file", "accepts": set()},
    "saturn": {"file_param": "file", "accepts": set()},
    "photonics": {"file_param": "path", "accepts": set()},
}


# --- helpers ---------------------------------------------------------------


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


@lru_cache(maxsize=1)
def _assets_path_cached(repo: str) -> Path:
    return Path(repo) / "viewer" / "assets.json"


def _load_assets() -> dict[str, Any]:
    """Read viewer/assets.json fresh each call (no mutation, no cache invalidation bugs)."""
    p = _assets_path_cached(str(repo_root()))
    if not p.exists():
        raise FileNotFoundError(f"viewer/assets.json not found at {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _lookup_asset(asset_path: str) -> dict[str, Any] | None:
    """Iterate every domain in assets.json; match by `record.path` with/without leading slash."""
    target = asset_path
    target_alt = asset_path.lstrip("/") if asset_path.startswith("/") else "/" + asset_path
    try:
        manifest = _load_assets()
    except FileNotFoundError:
        return None
    for value in manifest.values():
        if not isinstance(value, list):
            continue
        for record in value:
            if not isinstance(record, dict):
                continue
            rp = record.get("path")
            if rp == target or rp == target_alt:
                return record
    return None


def _encode_path(value: str) -> str:
    """URL-encode a path-bearing value, preserving '/' for readability."""
    return quote(value, safe="/")


def _build_url(page: str, params: dict[str, str]) -> str:
    """Assemble `<base>/viewer/<page>.html?<encoded params>`."""
    base = viewer_base()
    if params:
        query = urlencode(params, quote_via=quote, safe="/")
        return f"{base}/viewer/{page}.html?{query}"
    return f"{base}/viewer/{page}.html"


# --- 1. sv_build_viewer_url ------------------------------------------------


def _handle_build_viewer_url(args: Mapping[str, Any]) -> dict[str, Any]:
    asset_path = args.get("asset_path")
    if not isinstance(asset_path, str) or not asset_path:
        return _err("asset_path is required and must be a non-empty string")

    requested_viewer = args.get("viewer")
    overlay = args.get("overlay")
    mol_type = args.get("type")
    urdf_format = args.get("format")
    series = args.get("series")
    cam = args.get("cam")
    opacity = args.get("opacity")
    slice_arg = args.get("slice")

    if requested_viewer is None:
        record = _lookup_asset(asset_path)
        if record is None:
            return _err(f"viewer not registered for asset_path: {asset_path}")
        viewer = record.get("viewer")
        if viewer not in _VIEWER_ENUM:
            return _err(
                f"asset has unsupported viewer {viewer!r} for asset_path: {asset_path}"
            )
    else:
        if requested_viewer not in _VIEWER_ENUM:
            return _err(
                f"viewer must be one of {list(_VIEWER_ENUM)}; got {requested_viewer!r}"
            )
        viewer = requested_viewer

    contract = _VIEWER_CONTRACT[viewer]
    accepts: set[str] = contract["accepts"]
    required: set[str] = contract.get("required", set())
    file_param: str = contract["file_param"]

    supplied = {
        "overlay": overlay,
        "type": mol_type,
        "format": urdf_format,
        "series": series,
        "cam": cam,
        "opacity": opacity,
        "slice": slice_arg,
    }
    for name, value in supplied.items():
        if value is None:
            continue
        # `series` is the path-bearing param for cornerstone; accept silently when it
        # matches the page's file_param.
        if name == "series" and file_param == "series":
            continue
        if name not in accepts:
            return _err(
                f"viewer={viewer!r} does not accept parameter {name!r}"
            )

    for name in required:
        if supplied.get(name) is None:
            return _err(f"viewer={viewer!r} requires parameter {name!r}")

    if mol_type is not None and mol_type not in _MOLSTAR_TYPES:
        return _err(
            f"type must be one of {list(_MOLSTAR_TYPES)}; got {mol_type!r}"
        )
    if urdf_format is not None and urdf_format not in _URDF_FORMATS:
        return _err(
            f"format must be one of {list(_URDF_FORMATS)}; got {urdf_format!r}"
        )

    params: dict[str, str] = {file_param: asset_path}
    if "overlay" in accepts and overlay is not None:
        params["overlay"] = overlay
    if "type" in accepts and mol_type is not None:
        params["type"] = mol_type
    if "format" in accepts and urdf_format is not None:
        params["format"] = urdf_format
    if "cam" in accepts and cam is not None:
        params["cam"] = cam
    if "opacity" in accepts and opacity is not None:
        params["opacity"] = opacity
    if "slice" in accepts and slice_arg is not None:
        params["slice"] = slice_arg

    url = _build_url(viewer, params)
    return {"url": url, "viewer": viewer, "params_used": dict(params)}


_SCHEMA_BUILD_VIEWER_URL: dict[str, Any] = {
    "type": "object",
    "required": ["asset_path"],
    "additionalProperties": False,
    "properties": {
        "asset_path": {"type": "string"},
        "viewer": {"type": "string", "enum": list(_VIEWER_ENUM)},
        "overlay": {"type": "string"},
        "type": {"type": "string", "enum": list(_MOLSTAR_TYPES)},
        "format": {"type": "string", "enum": list(_URDF_FORMATS)},
        "series": {"type": "string"},
        "cam": {"type": "string"},
        "opacity": {"type": "string"},
        "slice": {"type": "string"},
    },
}


# --- 2. sv_build_match3d_url ----------------------------------------------


def _handle_build_match3d_url(args: Mapping[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset")
    qid = args.get("qid")

    if dataset is not None:
        if not isinstance(dataset, str):
            return _err("dataset must be a string")
        if dataset not in _MATCH3D_DATASETS:
            return _err(
                f"dataset must be one of {list(_MATCH3D_DATASETS)}; got {dataset!r}"
            )

    if qid is not None and not isinstance(qid, str):
        return _err("qid must be a string")

    params: dict[str, str] = {}
    if dataset is not None:
        params["dataset"] = dataset
    if qid:
        params["qid"] = qid

    url = _build_url("match3d", params)
    result: dict[str, Any] = {
        "url": url,
        "viewer": "match3d",
        "params_used": dict(params),
    }
    if qid:
        result["notes"] = "qid not yet auto-scrolled; search the page for this id"
    return result


_SCHEMA_BUILD_MATCH3D_URL: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "dataset": {"type": "string", "enum": list(_MATCH3D_DATASETS)},
        "qid": {"type": "string"},
    },
}


# --- 3. sv_build_dual_pane_url --------------------------------------------


def _is_nifti_path(p: str) -> bool:
    lower = p.lower()
    return lower.endswith(".nii") or lower.endswith(".nii.gz")


def _handle_build_dual_pane_url(args: Mapping[str, Any]) -> dict[str, Any]:
    nifti_path = args.get("nifti_path")
    mask_path = args.get("mask_path")
    if not isinstance(nifti_path, str) or not nifti_path:
        return _err("nifti_path is required and must be a non-empty string")
    if not isinstance(mask_path, str) or not mask_path:
        return _err("mask_path is required and must be a non-empty string")
    if not _is_nifti_path(nifti_path):
        return _err(f"nifti_path must end in .nii or .nii.gz; got {nifti_path!r}")
    if not _is_nifti_path(mask_path):
        return _err(f"mask_path must end in .nii or .nii.gz; got {mask_path!r}")

    params = {"file": nifti_path, "overlay": mask_path, "layout": "dual"}
    url = _build_url("niivue", params)
    return {
        "url": url,
        "viewer": "niivue-dual",
        "params_used": dict(params),
    }


_SCHEMA_BUILD_DUAL_PANE_URL: dict[str, Any] = {
    "type": "object",
    "required": ["nifti_path", "mask_path"],
    "additionalProperties": False,
    "properties": {
        "nifti_path": {"type": "string"},
        "mask_path": {"type": "string"},
    },
}


# --- 4. sv_build_vqa_url --------------------------------------------------


def _handle_build_vqa_url(args: Mapping[str, Any]) -> dict[str, Any]:
    dataset = args.get("dataset")
    sample = args.get("sample")
    if not isinstance(dataset, str) or not dataset:
        return _err("dataset is required and must be a non-empty string")
    if not isinstance(sample, str) or not sample:
        return _err("sample is required and must be a non-empty string")

    params = {"dataset": dataset, "sample": sample}
    url = _build_url("vqa", params)
    return {"url": url, "viewer": "vqa", "params_used": dict(params)}


_SCHEMA_BUILD_VQA_URL: dict[str, Any] = {
    "type": "object",
    "required": ["dataset", "sample"],
    "additionalProperties": False,
    "properties": {
        "dataset": {"type": "string"},
        "sample": {"type": "string"},
    },
}


# --- registration ----------------------------------------------------------


TOOLS: list[ToolSpec] = [
    register(
        ToolSpec(
            name="sv_build_viewer_url",
            description=(
                "Build a deep-link URL to the single-asset viewer page for the given "
                "asset_path. If `viewer` is omitted, auto-resolve from viewer/assets.json. "
                "Rejects (does not silently drop) params the chosen page does not consume."
            ),
            input_schema=_SCHEMA_BUILD_VIEWER_URL,
            handler=_handle_build_viewer_url,
            category="B",
        )
    ),
    register(
        ToolSpec(
            name="sv_build_match3d_url",
            description=(
                "Build a URL to the match3d browser. Optional dataset filter and qid. "
                "Returns a `notes` field when qid is supplied because match3d.html does "
                "not yet auto-scroll by qid."
            ),
            input_schema=_SCHEMA_BUILD_MATCH3D_URL,
            handler=_handle_build_match3d_url,
            category="B",
        )
    ),
    register(
        ToolSpec(
            name="sv_build_dual_pane_url",
            description=(
                "Build a niivue dual-pane URL for a NIfTI volume + overlay mask. "
                "Validates that both paths end in .nii or .nii.gz."
            ),
            input_schema=_SCHEMA_BUILD_DUAL_PANE_URL,
            handler=_handle_build_dual_pane_url,
            category="B",
        )
    ),
    register(
        ToolSpec(
            name="sv_build_vqa_url",
            description="Build a VQA viewer URL for the given dataset+sample pair.",
            input_schema=_SCHEMA_BUILD_VQA_URL,
            handler=_handle_build_vqa_url,
            category="B",
        )
    ),
]
