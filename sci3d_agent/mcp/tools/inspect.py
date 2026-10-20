"""Asset inspectors (Category C).

Eight read-only header/stat inspectors used by Phase 2 D (static renderers) and
Phase 3 F (3D-Vision session manager) to make rendering decisions WITHOUT
loading voxel/point payloads into the agent context. Every handler returns a
JSON-serializable dict <2KB; errors flow back as {"error": "..."} envelopes,
never as raised exceptions.

Axis convention for ``sv_inspect_nifti_mask_pair`` (load-bearing for the
M3D-RefSeg trajectory and Phase 2 D's ``sv_render_nifti_overlay``):

    In numpy/nibabel array order ``arr[i, j, k]``, the canonical mapping used
    here is::

        sagittal = axis 0   (varies L<->R; YZ plane)
        coronal  = axis 1   (varies P<->A; XZ plane)
        axial    = axis 2   (varies I<->S; XY plane)

    A slice at index ``s`` along ``axial`` is therefore ``arr[:, :, s]``. This
    matches how nibabel reports zooms and how every downstream renderer
    consumes index arguments. Picking the wrong axis is the #1 silent bug for
    M3D-RefSeg slice picking, so this convention is intentionally explicit.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from sci3d_agent.mcp.registry import ToolSpec, register
from sci3d_agent.mcp.runtime.cache import cache_key, get_or_compute
from sci3d_agent.mcp.runtime.paths import repo_root

INSPECT_CACHE_DIR = Path.home() / ".cache" / "sciencevision" / "inspect"

_STANDARD_AMINO_ACIDS = frozenset(
    {
        "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
        "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
        "MSE", "SEC", "PYL",
    }
)


def _resolve_path(p: str | Path) -> Path:
    raw = Path(p)
    if raw.is_absolute():
        return raw
    return repo_root() / raw


def _try_import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _missing_dep(name: str) -> dict:
    return {"error": f"dependency missing: {name}"}


def _missing_file(path: Path) -> dict:
    return {"error": f"file not found: {path}"}


def _cached(tool: str, path: Path, args: dict, compute):
    key = cache_key(path, tool, args)
    return get_or_compute(key, compute, INSPECT_CACHE_DIR)


def _mesh_axis_up_hint(suffix: str) -> str:
    s = suffix.lower()
    if s in {".glb", ".gltf"}:
        return "Y"
    return "Z"


def _inspect_mesh_impl(path: Path) -> dict:
    trimesh = _try_import("trimesh")
    if trimesh is None:
        return _missing_dep("trimesh")
    try:
        mesh = trimesh.load(str(path), force="mesh")
    except Exception as exc:
        return {"error": f"trimesh load failed: {exc}"}

    if mesh is None or not hasattr(mesh, "vertices"):
        return {"error": "trimesh returned non-mesh object"}

    n_vertices = int(len(mesh.vertices))
    faces = getattr(mesh, "faces", None)
    n_faces = int(len(faces)) if faces is not None else 0
    bounds = np.asarray(mesh.bounds, dtype=float) if mesh.bounds is not None else None
    extents = np.asarray(mesh.extents, dtype=float) if mesh.extents is not None else None
    bbox = [bounds[0].tolist(), bounds[1].tolist()] if bounds is not None else None
    extent = extents.tolist() if extents is not None else None

    has_vertex_colors = False
    has_uv = False
    materials = None
    visual = getattr(mesh, "visual", None)
    if visual is not None:
        try:
            kind = getattr(visual, "kind", None)
            if kind == "vertex":
                has_vertex_colors = True
            elif getattr(visual, "vertex_colors", None) is not None:
                vc = np.asarray(visual.vertex_colors)
                has_vertex_colors = vc.size > 0
        except Exception:
            has_vertex_colors = False
        try:
            uv = getattr(visual, "uv", None)
            has_uv = uv is not None and len(uv) > 0
        except Exception:
            has_uv = False
        mat = getattr(visual, "material", None)
        if mat is not None:
            materials = type(mat).__name__

    return {
        "format": path.suffix.lstrip("."),
        "n_vertices": n_vertices,
        "n_faces": n_faces,
        "bbox": bbox,
        "extent": extent,
        "has_vertex_colors": bool(has_vertex_colors),
        "has_uv": bool(has_uv),
        "materials": materials,
        "axis_up_hint": _mesh_axis_up_hint(path.suffix),
    }


def handle_inspect_mesh(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    path = _resolve_path(raw)
    if not path.exists():
        return _missing_file(path)
    return _cached("sv_inspect_mesh", path, {}, lambda: _inspect_mesh_impl(path))


def _orientation_string(affine: np.ndarray) -> str:
    nib = _try_import("nibabel")
    if nib is None:
        return ""
    try:
        codes = nib.aff2axcodes(affine)
        return "".join(codes)
    except Exception:
        return ""


def _intensity_stats(arr: np.ndarray) -> dict:
    flat = np.asarray(arr, dtype=np.float64).reshape(-1)
    if flat.size == 0:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "p1": 0.0, "p99": 0.0}
    p1, p99 = np.percentile(flat, [1.0, 99.0])
    return {
        "min": float(flat.min()),
        "max": float(flat.max()),
        "mean": float(flat.mean()),
        "p1": float(p1),
        "p99": float(p99),
    }


def _label_histogram(arr: np.ndarray, top_k: int = 20) -> list[list[int]]:
    """Return list of [label, count] for nonzero labels, top_k by count."""
    int_arr = arr.astype(np.int64)
    vals, counts = np.unique(int_arr, return_counts=True)
    pairs = [(int(v), int(c)) for v, c in zip(vals, counts) if int(v) != 0]
    pairs.sort(key=lambda p: -p[1])
    return [[v, c] for v, c in pairs[:top_k]]


def _largest_label_slice_axial(arr: np.ndarray, hist: list[list[int]]) -> int | None:
    if not hist:
        return None
    label = hist[0][0]
    mask = (arr.astype(np.int64) == label)
    if not mask.any():
        return None
    per_slice = mask.sum(axis=(0, 1))  # axial = axis 2
    return int(np.argmax(per_slice))


def _inspect_nifti_impl(path: Path, include_label_hist: bool) -> dict:
    nib = _try_import("nibabel")
    if nib is None:
        return _missing_dep("nibabel")
    try:
        img = nib.load(str(path))
    except Exception as exc:
        return {"error": f"nibabel load failed: {exc}"}
    shape = tuple(int(s) for s in img.shape)
    try:
        spacing = tuple(float(z) for z in img.header.get_zooms()[: len(shape)])
    except Exception:
        spacing = tuple([1.0] * len(shape))
    affine = np.asarray(img.affine, dtype=float)
    affine_4x4 = affine.tolist()
    orientation = _orientation_string(affine)

    try:
        arr = np.asarray(img.get_fdata(caching="unchanged"))
    except Exception as exc:
        return {"error": f"nifti read failed: {exc}"}

    intensity = _intensity_stats(arr)
    out: dict = {
        "shape": list(shape),
        "spacing": list(spacing),
        "affine_4x4": affine_4x4,
        "orientation": orientation,
        "intensity": intensity,
    }

    if include_label_hist:
        if arr.size and float(arr.max()) < 1024.0:
            hist = _label_histogram(arr)
            out["n_nonzero_labels"] = len(hist)
            out["label_hist"] = hist
            slice_idx = _largest_label_slice_axial(arr, hist)
            if slice_idx is not None:
                out["suggested_axial_slice_for_largest_label"] = slice_idx
        else:
            out["n_nonzero_labels"] = 0
            out["label_hist"] = []
    return out


def handle_inspect_nifti(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    path = _resolve_path(raw)
    if not path.exists():
        return _missing_file(path)
    include_label_hist = bool(args.get("include_label_hist", False))
    return _cached(
        "sv_inspect_nifti",
        path,
        {"include_label_hist": include_label_hist},
        lambda: _inspect_nifti_impl(path, include_label_hist),
    )


def _voxel_corners(bbox_voxel: list[list[int]]) -> np.ndarray:
    (i0, j0, k0), (i1, j1, k1) = bbox_voxel
    corners = np.array(
        [
            [i0, j0, k0, 1.0],
            [i1, j0, k0, 1.0],
            [i0, j1, k0, 1.0],
            [i0, j0, k1, 1.0],
            [i1, j1, k0, 1.0],
            [i1, j0, k1, 1.0],
            [i0, j1, k1, 1.0],
            [i1, j1, k1, 1.0],
        ],
        dtype=float,
    )
    return corners


def _r4(x: float) -> float:
    return round(float(x), 4)


def _label_record(mask: np.ndarray, label: int, affine: np.ndarray) -> dict:
    indices = np.argwhere(mask == label)
    voxel_count = int(indices.shape[0])
    if voxel_count == 0:
        return {}
    bbox_voxel_lo = indices.min(axis=0).astype(int).tolist()
    bbox_voxel_hi = indices.max(axis=0).astype(int).tolist()
    bbox_voxel = [bbox_voxel_lo, bbox_voxel_hi]
    corners = _voxel_corners(bbox_voxel)
    world = (affine @ corners.T).T[:, :3]
    bbox_world = [
        [_r4(v) for v in world.min(axis=0).tolist()],
        [_r4(v) for v in world.max(axis=0).tolist()],
    ]
    centroid_voxel = indices.mean(axis=0)
    centroid_world = (
        affine @ np.array([*centroid_voxel.tolist(), 1.0])
    )[:3]
    # Axis convention: sagittal=axis 0, coronal=axis 1, axial=axis 2.
    label_mask = mask == label
    sagittal_idx = int(np.argmax(label_mask.sum(axis=(1, 2))))
    coronal_idx = int(np.argmax(label_mask.sum(axis=(0, 2))))
    axial_idx = int(np.argmax(label_mask.sum(axis=(0, 1))))
    return {
        "id": int(label),
        "voxel_count": voxel_count,
        "bbox_voxel": bbox_voxel,
        "bbox_world": bbox_world,
        "centroid_voxel": [_r4(v) for v in centroid_voxel.tolist()],
        "centroid_world": [_r4(v) for v in centroid_world.tolist()],
        "best_slices": {
            "axial": axial_idx,
            "coronal": coronal_idx,
            "sagittal": sagittal_idx,
        },
    }


def _inspect_nifti_mask_pair_impl(nifti_path: Path, mask_path: Path) -> dict:
    nib = _try_import("nibabel")
    if nib is None:
        return _missing_dep("nibabel")
    try:
        base = nib.load(str(nifti_path))
        mask_img = nib.load(str(mask_path))
    except Exception as exc:
        return {"error": f"nibabel load failed: {exc}"}
    base_shape = tuple(int(s) for s in base.shape)
    mask_shape = tuple(int(s) for s in mask_img.shape)
    if base_shape != mask_shape:
        return {"error": f"shape mismatch base={base_shape} mask={mask_shape}"}
    try:
        spacing = tuple(float(z) for z in base.header.get_zooms()[: len(base_shape)])
    except Exception:
        spacing = tuple([1.0] * len(base_shape))
    affine = np.asarray(base.affine, dtype=float)
    try:
        mask_arr = np.asarray(mask_img.get_fdata(caching="unchanged")).astype(np.int64)
    except Exception as exc:
        return {"error": f"mask read failed: {exc}"}

    uniq = np.unique(mask_arr)
    labels: list[dict] = []
    for raw_label in uniq:
        lbl = int(raw_label)
        if lbl == 0:
            continue
        rec = _label_record(mask_arr, lbl, affine)
        if rec:
            labels.append(rec)

    labels.sort(key=lambda r: -r["voxel_count"])
    return {
        "shape": list(base_shape),
        "spacing": [_r4(v) for v in spacing],
        "affine_4x4": [[_r4(v) for v in row] for row in affine.tolist()],
        "labels": labels,
    }


def handle_inspect_nifti_mask_pair(args: Mapping[str, Any]) -> Mapping[str, Any]:
    nifti_raw = args.get("nifti_path")
    mask_raw = args.get("mask_path")
    if not nifti_raw or not mask_raw:
        return {"error": "missing required args: nifti_path, mask_path"}
    nifti_path = _resolve_path(nifti_raw)
    mask_path = _resolve_path(mask_raw)
    if not nifti_path.exists():
        return _missing_file(nifti_path)
    if not mask_path.exists():
        return _missing_file(mask_path)
    return _cached(
        "sv_inspect_nifti_mask_pair",
        mask_path,
        {"nifti_path": str(nifti_path), "mask_mtime": mask_path.stat().st_mtime_ns},
        lambda: _inspect_nifti_mask_pair_impl(nifti_path, mask_path),
    )


def _inspect_pointcloud_impl(path: Path) -> dict:
    laspy = _try_import("laspy")
    if laspy is None:
        return _missing_dep("laspy")
    try:
        las = laspy.read(str(path))
    except Exception as exc:
        return {"error": f"laspy load failed: {exc}"}
    n_points = int(len(las.x))
    try:
        header = las.header
        mins = [float(header.mins[i]) for i in range(3)]
        maxs = [float(header.maxs[i]) for i in range(3)]
        bbox = [mins, maxs]
    except Exception:
        bbox = None
    try:
        fields = list(las.point_format.dimension_names)
    except Exception:
        fields = []
    has_rgb = any(f in fields for f in ("red", "green", "blue"))
    has_intensity = False
    if "intensity" in fields:
        try:
            inten = np.asarray(las.intensity)
            has_intensity = bool(inten.size > 0 and inten.max() > 0)
        except Exception:
            has_intensity = True
    return {
        "n_points": n_points,
        "bbox": bbox,
        "fields": fields,
        "has_rgb": has_rgb,
        "has_intensity": has_intensity,
    }


def handle_inspect_pointcloud(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    path = _resolve_path(raw)
    if not path.exists():
        return _missing_file(path)
    return _cached("sv_inspect_pointcloud", path, {}, lambda: _inspect_pointcloud_impl(path))


def _inspect_sdf_impl(path: Path) -> dict:
    try:
        text = path.read_text(errors="ignore")
    except Exception as exc:
        return {"error": f"sdf read failed: {exc}"}
    n_models = max(1, text.count("$$$$"))
    n_atoms = 0
    lines = text.splitlines()
    # Counts line is the 4th line of each block per V2000 spec
    if len(lines) >= 4:
        try:
            counts = lines[3]
            n_atoms = int(counts[:3].strip())
        except Exception:
            n_atoms = 0
    return {
        "n_atoms": n_atoms,
        "n_residues": 0,
        "chains": [],
        "ligands": [],
        "has_water": False,
        "n_models": n_models,
    }


def _inspect_molecule_impl(path: Path, kind: str) -> dict:
    if kind == "sdf":
        return _inspect_sdf_impl(path)
    gemmi = _try_import("gemmi")
    if gemmi is None:
        return _missing_dep("gemmi")
    try:
        structure = gemmi.read_structure(str(path))
    except Exception as exc:
        return {"error": f"gemmi load failed: {exc}"}
    chains_set: set[str] = set()
    ligands_set: set[str] = set()
    n_residues = 0
    n_atoms = 0
    has_water = False
    for model in structure:
        for chain in model:
            chains_set.add(chain.name)
            for residue in chain:
                n_residues += 1
                rname = residue.name.upper()
                if rname == "HOH":
                    has_water = True
                elif rname not in _STANDARD_AMINO_ACIDS:
                    ligands_set.add(rname)
                for _atom in residue:
                    n_atoms += 1
    resolution = None
    try:
        if structure.resolution and structure.resolution > 0:
            resolution = float(structure.resolution)
    except Exception:
        resolution = None
    return {
        "n_atoms": int(n_atoms),
        "n_residues": int(n_residues),
        "chains": sorted(chains_set),
        "ligands": sorted(ligands_set),
        "has_water": bool(has_water),
        "resolution": resolution,
        "n_models": int(len(structure)),
    }


def handle_inspect_molecule(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("path")
    kind = args.get("type")
    if not raw:
        return {"error": "missing required arg: path"}
    if kind not in {"mmcif", "pdb", "sdf"}:
        return {"error": "type must be one of: mmcif, pdb, sdf"}
    path = _resolve_path(raw)
    if not path.exists():
        return _missing_file(path)
    return _cached(
        "sv_inspect_molecule",
        path,
        {"type": kind},
        lambda: _inspect_molecule_impl(path, kind),
    )


def _walk_mesh_refs(urdf, urdf_path: Path) -> list[dict]:
    refs: list[dict] = []
    try:
        links = urdf.link_map.items() if hasattr(urdf, "link_map") else []
    except Exception:
        links = []
    for link_name, link in links:
        for vis in list(getattr(link, "visuals", []) or []):
            geom = getattr(vis, "geometry", None)
            mesh = getattr(geom, "mesh", None) if geom is not None else None
            if mesh is None:
                continue
            filename = getattr(mesh, "filename", None)
            if not filename:
                continue
            target = Path(filename)
            if not target.is_absolute():
                target = (urdf_path.parent / filename).resolve()
            refs.append(
                {
                    "link": link_name,
                    "path": str(target),
                    "exists": target.exists(),
                }
            )
    return refs


def _inspect_urdf_impl(path: Path, fmt: str) -> dict:
    yourdfpy = _try_import("yourdfpy")
    if yourdfpy is None:
        return _missing_dep("yourdfpy")
    try:
        urdf = yourdfpy.URDF.load(str(path))
    except Exception as exc:
        return {"error": f"yourdfpy load failed: {exc}"}
    try:
        links = list(urdf.link_map.keys()) if hasattr(urdf, "link_map") else []
    except Exception:
        links = []
    try:
        joints = list(urdf.joint_map.values()) if hasattr(urdf, "joint_map") else []
    except Exception:
        joints = []
    joint_types: dict[str, int] = {}
    dof = 0
    for j in joints:
        jt = getattr(j, "type", None) or "unknown"
        joint_types[jt] = joint_types.get(jt, 0) + 1
        if jt != "fixed":
            dof += 1
    root_link = None
    try:
        root_link = urdf.base_link
    except Exception:
        try:
            root_link = urdf.scene.graph.base_frame  # type: ignore[attr-defined]
        except Exception:
            root_link = None
    mesh_refs = _walk_mesh_refs(urdf, path)
    return {
        "n_links": len(links),
        "n_joints": len(joints),
        "joint_types_hist": joint_types,
        "mesh_refs": mesh_refs[:50],
        "root_link": root_link,
        "dof": int(dof),
    }


def handle_inspect_urdf(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("path")
    fmt = args.get("format")
    if not raw:
        return {"error": "missing required arg: path"}
    if fmt not in {"urdf", "sdf"}:
        return {"error": "format must be one of: urdf, sdf"}
    path = _resolve_path(raw)
    if not path.exists():
        return _missing_file(path)
    return _cached(
        "sv_inspect_urdf",
        path,
        {"format": fmt},
        lambda: _inspect_urdf_impl(path, fmt),
    )


def _inspect_volume_npy_impl(path: Path, affine_path: Path | None) -> dict:
    try:
        arr = np.load(str(path), mmap_mode="r", allow_pickle=False)
    except Exception as exc:
        return {"error": f"numpy load failed: {exc}"}
    try:
        vmin = float(np.asarray(arr).min())
        vmax = float(np.asarray(arr).max())
        vmean = float(np.asarray(arr).mean())
    except Exception as exc:
        return {"error": f"numpy stats failed: {exc}"}
    out: dict = {
        "shape": list(int(s) for s in arr.shape),
        "dtype": str(arr.dtype.name),
        "value_range": [vmin, vmax],
        "mean": vmean,
    }
    if affine_path is not None:
        if not affine_path.exists():
            return _missing_file(affine_path)
        try:
            affine = np.load(str(affine_path), allow_pickle=False)
        except Exception as exc:
            return {"error": f"affine load failed: {exc}"}
        if affine.shape != (4, 4):
            return {"error": f"affine must be 4x4, got {tuple(affine.shape)}"}
        out["affine_4x4"] = affine.astype(float).tolist()
        out["spacing"] = [
            float(np.linalg.norm(affine[:3, 0])),
            float(np.linalg.norm(affine[:3, 1])),
            float(np.linalg.norm(affine[:3, 2])),
        ]
    return out


def handle_inspect_volume_npy(args: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    path = _resolve_path(raw)
    if not path.exists():
        return _missing_file(path)
    affine_raw = args.get("affine_path")
    affine_path = _resolve_path(affine_raw) if affine_raw else None
    cache_args = {"affine_path": str(affine_path) if affine_path else ""}
    return _cached(
        "sv_inspect_volume_npy",
        path,
        cache_args,
        lambda: _inspect_volume_npy_impl(path, affine_path),
    )


_SCANNET_PATTERNS = (
    "scans_test/{scene_id}/{scene_id}_vh_clean_2.ply",
    "scans/{scene_id}/{scene_id}_vh_clean_2.ply",
    "scans_test/{scene_id}/{scene_id}.ply",
    "scans/{scene_id}/{scene_id}.ply",
)


def _find_scannet_ply(scene_id: str, scannet_root: Path) -> Path | None:
    for pat in _SCANNET_PATTERNS:
        candidate = scannet_root / pat.format(scene_id=scene_id)
        if candidate.exists():
            return candidate
    return None


def _matched_datasets_for_scene_lookup() -> list[str]:
    return ["scanqa", "sqa3d", "embodiedscan-demo"]


def _qa_refs_for_scene(scene_id: str, sample_limit: int = 3) -> list[dict]:
    refs: list[dict] = []
    matched_dir = repo_root() / "datasets" / "_matched"
    for dataset in _matched_datasets_for_scene_lookup():
        path = matched_dir / f"{dataset}.json"
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        records: Iterable[Mapping[str, Any]]
        if isinstance(payload, dict) and "records" in payload:
            records = payload["records"]
        elif isinstance(payload, list):
            records = payload
        else:
            continue
        matched = [r for r in records if r.get("scene_or_case_id") == scene_id]
        if not matched:
            continue
        sample_ids = [r.get("qid") for r in matched[:sample_limit] if r.get("qid")]
        refs.append(
            {
                "dataset": dataset,
                "count": len(matched),
                "sample_ids": sample_ids,
            }
        )
    return refs


def _inspect_scannet_scene_impl(scene_id: str, ply_path: Path) -> dict:
    base = _inspect_mesh_impl(ply_path)
    if "error" in base:
        return base
    return {
        "scene_id": scene_id,
        "ply_path": str(ply_path),
        "bbox": base.get("bbox"),
        "extent": base.get("extent"),
        "n_vertices": base.get("n_vertices"),
        "qa_refs": _qa_refs_for_scene(scene_id),
    }


def handle_inspect_scannet_scene(args: Mapping[str, Any]) -> Mapping[str, Any]:
    scene_id = args.get("scene_id")
    if not scene_id:
        return {"error": "missing required arg: scene_id"}
    scannet_root = repo_root() / "datasets" / "_shared_3d" / "scannet"
    ply_path = _find_scannet_ply(scene_id, scannet_root)
    if ply_path is None:
        return {"error": f"scannet ply not found for scene_id={scene_id}"}
    return _cached(
        "sv_inspect_scannet_scene",
        ply_path,
        {"scene_id": scene_id},
        lambda: _inspect_scannet_scene_impl(scene_id, ply_path),
    )


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
            category="C",
        )
    )


TOOLS: list[ToolSpec] = [
    _spec(
        "sv_inspect_mesh",
        "Mesh stats (vertices, faces, bbox, axis-up hint). No render.",
        handle_inspect_mesh,
    ),
    _spec(
        "sv_inspect_nifti",
        "NIfTI header summary + intensity stats; optional label histogram.",
        handle_inspect_nifti,
    ),
    _spec(
        "sv_inspect_nifti_mask_pair",
        "Per-label voxel/world bbox + centroid + best_slices. M3D-RefSeg load-bearing.",
        handle_inspect_nifti_mask_pair,
    ),
    _spec(
        "sv_inspect_pointcloud",
        "LAS/LAZ point cloud stats (point count, bbox, fields, rgb/intensity).",
        handle_inspect_pointcloud,
    ),
    _spec(
        "sv_inspect_molecule",
        "Molecule structure summary (atoms, residues, chains, ligands, water).",
        handle_inspect_molecule,
    ),
    _spec(
        "sv_inspect_urdf",
        "URDF/SDF robot description summary (links, joints, mesh refs, dof).",
        handle_inspect_urdf,
    ),
    _spec(
        "sv_inspect_volume_npy",
        "NumPy .npy volume summary (shape, dtype, value range, optional affine).",
        handle_inspect_volume_npy,
    ),
    _spec(
        "sv_inspect_scannet_scene",
        "Joiner: ScanNet scene_id -> ply stats + linked tier-1 QA records.",
        handle_inspect_scannet_scene,
    ),
]
