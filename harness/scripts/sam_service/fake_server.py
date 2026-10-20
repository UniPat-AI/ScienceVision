"""Fake SAM FastAPI server for SAM-as-tool ablation tests."""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import io
import os

import nibabel as nib
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from scripts.sam_service.synth_mask import apply_quality_tier


BBox = list[int]
VoxelPoint = list[float]
VoxelBox = list[VoxelPoint]

_TIER_SCORES: dict[str, float] = {"perfect": 0.85, "noisy": 0.65, "wrong": 0.25}


class Sam2dRequest(BaseModel):
    """Request payload for 2D fake SAM inference."""

    image_b64: str = Field(min_length=1)
    text_prompt: str = Field(min_length=1)


class Sam2dMask(BaseModel):
    """One synthetic SAM mask result."""

    mask_b64: str
    score: float
    bbox: BBox
    area: int


class Sam2dResponse(BaseModel):
    """Response payload for 2D fake SAM inference."""

    masks: list[Sam2dMask]


class Sam3dVolumeRequest(BaseModel):
    """Request payload for 3D fake SAM inference from a NIfTI volume."""

    volume_path: str = Field(min_length=1)
    text_prompt: str = Field(min_length=1)


class Sam3dVolumeMask(BaseModel):
    """One synthetic SAM 3D mask result."""

    voxel_mask_b64: str
    score: float
    voxel_count: int
    centroid: VoxelPoint
    bbox: VoxelBox


class Sam3dVolumeResponse(BaseModel):
    """Response payload for 3D fake SAM inference."""

    masks: list[Sam3dVolumeMask]


class Sam3dMeshRequest(BaseModel):
    """Request payload for 3D fake SAM inference from a point cloud / mesh."""

    pointcloud_path: str = Field(min_length=1)
    text_prompt: str = Field(min_length=1)


class Sam3dMeshSegment(BaseModel):
    """One synthetic SAM 3D segment over a point cloud."""

    per_point_labels_b64: str
    score: float
    point_count: int
    centroid: list[float]
    bbox: list[list[float]]


class Sam3dMeshResponse(BaseModel):
    """Response payload for 3D fake SAM mesh inference."""

    segments: list[Sam3dMeshSegment]


def _decode_png_b64(image_b64: str) -> np.ndarray:
    """Decode a base64-encoded PNG into a grayscale uint8 array."""
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image payload") from exc

    try:
        image = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("image_b64 must contain a valid PNG image") from exc

    if image.format != "PNG":
        raise ValueError("image_b64 must contain a PNG image")

    return np.asarray(image.convert("L"), dtype=np.uint8)


def _encode_mask_b64(mask: np.ndarray) -> str:
    """Encode a binary mask to base64 PNG."""
    image = Image.fromarray(mask.astype(np.uint8, copy=False) * 255, mode="L")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _encode_voxel_mask_b64(mask: np.ndarray) -> str:
    """Encode a 3D binary mask as shape header plus gzipped raw bytes."""
    binary_mask = mask.astype(np.uint8, copy=False)
    shape_header = "x".join(str(size) for size in binary_mask.shape).encode("ascii") + b"\n"
    compressed = gzip.compress(binary_mask.tobytes())
    return base64.b64encode(shape_header + compressed).decode("ascii")


def _seed_for(*parts: str | np.ndarray) -> int:
    """Build a deterministic seed from strings and array content."""
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, np.ndarray):
            digest.update(part.shape.__repr__().encode("utf-8"))
            digest.update(part.dtype.str.encode("ascii"))
            digest.update(part.tobytes())
            continue
        digest.update(part.encode("utf-8"))
    return int.from_bytes(digest.digest()[:8], byteorder="big", signed=False)


def _gt_from_brightness(image: np.ndarray) -> np.ndarray:
    """Create a binary foreground mask from non-zero pixels."""
    return (image > 0).astype(np.uint8)


def _load_nifti_as_gt(path: str) -> np.ndarray:
    """Load a NIfTI file and derive a binary ground-truth mask."""
    img = nib.load(path)
    arr = np.asarray(img.dataobj)
    # Treat anything labeled (>0) as the GT mask. If the file is the
    # original volume (no label), threshold at the 90th percentile
    # to give the noisy/wrong tiers something to perturb.
    if arr.dtype.kind in ("i", "u") and (arr > 0).sum() < arr.size * 0.5:
        return (arr > 0).astype(np.uint8)
    thresh = float(np.quantile(arr, 0.9))
    return (arr > thresh).astype(np.uint8)


def _bbox_of(mask: np.ndarray) -> BBox:
    """Return [x0, y0, x1, y1] for the positive mask extent."""
    coords = np.argwhere(mask > 0)
    if coords.size == 0:
        return [0, 0, 0, 0]

    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    return [int(x0), int(y0), int(x1), int(y1)]


def _voxel_centroid_and_bbox(mask: np.ndarray) -> tuple[VoxelPoint, VoxelBox]:
    """Return centroid and bounding box for a positive 3D mask."""
    coords = np.argwhere(mask > 0)
    if coords.size == 0:
        return [0.0, 0.0, 0.0], [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]

    centroid = coords.mean(axis=0).astype(np.float64, copy=False).tolist()
    lower = coords.min(axis=0).astype(np.float64, copy=False).tolist()
    upper = coords.max(axis=0).astype(np.float64, copy=False).tolist()
    return centroid, [lower, upper]


def _load_ply_xyz_brightness(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (xyz [N,3], brightness [N]) from a PLY file.

    Fast path: a minimal ASCII parser for the layout our own test
    fixtures use (`x y z` then optional `red green blue`). If that
    fails — e.g. the file is binary PLY, as real ScanNet meshes are —
    fall back to trimesh, which handles binary/format variants and
    exposes per-vertex colors. Brightness is the mean of RGB if
    present, else uniform 1.0.
    """
    try:
        return _load_ply_ascii(path)
    except Exception:
        return _load_ply_via_trimesh(path)


def _load_ply_ascii(path: str) -> tuple[np.ndarray, np.ndarray]:
    with open(path, "r", encoding="ascii") as fh:
        lines = fh.read().splitlines()
    if not lines or not lines[0].startswith("ply"):
        raise ValueError("not a PLY file")
    header_end = next(i for i, l in enumerate(lines) if l.strip() == "end_header")
    fmt = next((l for l in lines[:header_end] if l.startswith("format")), "")
    if "ascii" not in fmt:
        raise ValueError("not ASCII PLY")
    n = int(next(l for l in lines[:header_end] if l.startswith("element vertex")).split()[-1])
    has_rgb = any("property uchar red" in l for l in lines[:header_end])
    xyz = np.zeros((n, 3), dtype=np.float32)
    bright = np.ones(n, dtype=np.float32)
    for i in range(n):
        parts = lines[header_end + 1 + i].split()
        xyz[i] = [float(parts[0]), float(parts[1]), float(parts[2])]
        if has_rgb:
            r, g, b = int(parts[3]), int(parts[4]), int(parts[5])
            bright[i] = (r + g + b) / 3.0 / 255.0
    return xyz, bright


def _load_ply_via_trimesh(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Robust fallback for binary / non-trivial PLY (e.g. ScanNet)."""
    import trimesh  # local import: only needed for real binary meshes

    mesh = trimesh.load(str(path), force="mesh")
    xyz = np.asarray(mesh.vertices, dtype=np.float32)
    if xyz.size == 0:
        raise ValueError("trimesh returned empty mesh")
    vc = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
    if vc is not None and len(vc) == len(xyz):
        rgb = np.asarray(vc, dtype=np.float32)[:, :3]
        bright = rgb.mean(axis=1) / 255.0
    else:
        bright = np.ones(len(xyz), dtype=np.float32)
    return xyz, bright


def _per_point_gt_from_brightness(bright: np.ndarray) -> np.ndarray:
    """Threshold per-point brightness into a binary GT label vector."""
    if bright.size == 0:
        return np.zeros(0, dtype=np.uint8)
    thresh = max(float(bright.mean() + bright.std()), 0.01)
    return (bright > thresh).astype(np.uint8)


def _encode_per_point_labels_b64(labels: np.ndarray) -> str:
    """Encode a 1D label vector as gzipped raw bytes, then base64."""
    raw = labels.astype(np.uint8, copy=False).tobytes()
    compressed = gzip.compress(raw)
    return base64.b64encode(compressed).decode("ascii")


def _xyz_centroid_and_bbox(
    xyz: np.ndarray, labels: np.ndarray
) -> tuple[list[float], list[list[float]]]:
    """Return centroid and bbox in XYZ space for the positive label subset."""
    sel = xyz[labels > 0]
    if sel.size == 0:
        return [0.0, 0.0, 0.0], [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    return (
        sel.mean(axis=0).tolist(),
        [sel.min(axis=0).tolist(), sel.max(axis=0).tolist()],
    )


def _apply_quality_tier_per_point(
    gt: np.ndarray, tier: str, seed: int
) -> np.ndarray:
    """Per-point degradation for unordered point-cloud labels.

    synth_mask.apply_quality_tier targets 2D/3D spatial masks; per-point
    labels are an unordered set and need a different noise model. Targets
    the same IoU bands as the 2D/3D-volume code path:
      perfect -> IoU == 1.0
      noisy   -> IoU in [0.6, 0.9]
      wrong   -> IoU < 0.2
    """
    rng = np.random.default_rng(seed)
    gt_bool = gt.astype(bool, copy=False)
    if tier == "perfect":
        return gt_bool.astype(np.uint8)
    if not gt_bool.any():
        return gt_bool.astype(np.uint8)
    if tier == "noisy":
        target_iou = float(rng.uniform(0.65, 0.85))
        K = int(gt_bool.sum())
        N = int(gt_bool.size)
        if K == N:
            q = 1.0 - target_iou
        else:
            q = max(
                0.0,
                min(0.5, (K * (1.0 - target_iou)) / (K + target_iou * (N - K))),
            )
        flips = rng.random(N) < q
        out = np.where(flips, ~gt_bool, gt_bool)
        return out.astype(np.uint8)
    if tier == "wrong":
        out = (~gt_bool).copy()
        ones_idx = np.flatnonzero(out)
        if ones_idx.size:
            drop = rng.choice(
                ones_idx, size=int(0.8 * ones_idx.size), replace=False
            )
            out[drop] = False
        return out.astype(np.uint8)
    raise ValueError(f"unknown tier: {tier}")


def build_app(quality: str = "noisy") -> FastAPI:
    """Build the fake SAM FastAPI application."""
    quality_tier = quality.lower()
    if quality_tier not in {"perfect", "noisy", "wrong"}:
        raise ValueError(f"unknown quality tier: {quality}")

    app = FastAPI(
        title="Fake SAM Service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "quality": quality_tier}

    @app.post("/sam2d", response_model=Sam2dResponse)
    def sam2d(request: Sam2dRequest) -> Sam2dResponse:
        try:
            image = _decode_png_b64(request.image_b64)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        gt = _gt_from_brightness(image)
        if gt.sum(dtype=np.int64) == 0:
            predicted = gt.copy()
        else:
            seed = _seed_for(image, request.text_prompt)
            predicted = apply_quality_tier(gt, tier=quality_tier, seed=seed)

        mask = Sam2dMask(
            mask_b64=_encode_mask_b64(predicted),
            score=_TIER_SCORES[quality_tier],
            bbox=_bbox_of(predicted),
            area=int(predicted.sum(dtype=np.int64)),
        )
        return Sam2dResponse(masks=[mask])

    @app.post("/sam3d_volume", response_model=Sam3dVolumeResponse)
    def sam3d_volume(request: Sam3dVolumeRequest) -> Sam3dVolumeResponse:
        if not os.path.exists(request.volume_path):
            raise HTTPException(status_code=400, detail=f"volume file not found: {request.volume_path}")

        try:
            gt = _load_nifti_as_gt(request.volume_path)
        except (OSError, nib.filebasedimages.ImageFileError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"error loading NIfTI volume: {exc}") from exc

        if gt.sum(dtype=np.int64) == 0:
            predicted = gt.copy()
        else:
            seed = _seed_for(request.text_prompt, request.volume_path)
            predicted = apply_quality_tier(gt, tier=quality_tier, seed=seed)
        centroid, bbox = _voxel_centroid_and_bbox(predicted)
        mask = Sam3dVolumeMask(
            voxel_mask_b64=_encode_voxel_mask_b64(predicted),
            score=_TIER_SCORES[quality_tier],
            voxel_count=int(predicted.sum(dtype=np.int64)),
            centroid=centroid,
            bbox=bbox,
        )
        return Sam3dVolumeResponse(masks=[mask])

    @app.post("/sam3d_mesh", response_model=Sam3dMeshResponse)
    def sam3d_mesh(request: Sam3dMeshRequest) -> Sam3dMeshResponse:
        if not os.path.exists(request.pointcloud_path):
            raise HTTPException(
                status_code=400,
                detail=f"pointcloud not found: {request.pointcloud_path}",
            )

        try:
            xyz, bright = _load_ply_xyz_brightness(request.pointcloud_path)
        except (OSError, ValueError, StopIteration, IndexError) as exc:
            raise HTTPException(
                status_code=400, detail=f"ply load failed: {exc}"
            ) from exc

        gt = _per_point_gt_from_brightness(bright)
        if gt.sum(dtype=np.int64) == 0:
            predicted = gt.copy()
        else:
            seed = _seed_for(request.text_prompt, request.pointcloud_path)
            predicted = _apply_quality_tier_per_point(
                gt, tier=quality_tier, seed=seed
            )
        centroid, bbox = _xyz_centroid_and_bbox(xyz, predicted)
        segment = Sam3dMeshSegment(
            per_point_labels_b64=_encode_per_point_labels_b64(predicted),
            score=_TIER_SCORES[quality_tier],
            point_count=int(predicted.sum(dtype=np.int64)),
            centroid=centroid,
            bbox=bbox,
        )
        return Sam3dMeshResponse(segments=[segment])

    return app


def main() -> None:
    """Run the fake SAM server."""
    quality = os.environ.get("FAKE_SAM_QUALITY", "noisy")
    uvicorn.run(build_app(quality=quality), host="0.0.0.0", port=8900)


if __name__ == "__main__":
    main()
