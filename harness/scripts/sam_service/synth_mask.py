"""Synthetic mask quality tiers for SAM-as-tool ablation."""
from __future__ import annotations

from itertools import product
from typing import Literal

import numpy as np
import scipy.ndimage as ndi

Tier = Literal["perfect", "noisy", "wrong"]
_VALID_TIERS = ("perfect", "noisy", "wrong")
_NOISY_IOU_MIN = 0.6
_NOISY_IOU_MAX = 0.9


def iou_2d(a: np.ndarray, b: np.ndarray) -> float:
    """Return 2D intersection-over-union for two masks."""
    return _iou(a, b)


def iou_3d(a: np.ndarray, b: np.ndarray) -> float:
    """Return 3D intersection-over-union for two masks."""
    return _iou(a, b)


def apply_quality_tier(
    gt: np.ndarray,
    *,
    tier: Tier,
    seed: int,
) -> np.ndarray:
    """Return a deterministic synthetic mask for the requested quality tier."""
    if tier not in _VALID_TIERS:
        raise ValueError(f"unknown tier: {tier}")

    mask = _as_bool_mask(gt)
    if tier == "perfect":
        return gt.copy()
    if tier == "noisy":
        noisy = _make_noisy_mask(mask, seed)
        return noisy.astype(gt.dtype, copy=False)

    wrong = _make_wrong_mask(mask, seed)
    return wrong.astype(gt.dtype, copy=False)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        raise ValueError("masks must have the same shape")

    a_mask = _as_bool_mask(a)
    b_mask = _as_bool_mask(b)
    intersection = np.logical_and(a_mask, b_mask).sum(dtype=np.int64)
    union = np.logical_or(a_mask, b_mask).sum(dtype=np.int64)
    if union == 0:
        return 1.0
    return float(intersection / union)


def _as_bool_mask(mask: np.ndarray) -> np.ndarray:
    array = np.asarray(mask)
    if array.ndim not in (2, 3):
        raise ValueError("gt must be a 2D or 3D mask")
    return array.astype(bool, copy=False)


def _make_noisy_mask(gt: np.ndarray, seed: int) -> np.ndarray:
    fallback_counts = _find_noisy_fallback_counts(gt)
    if fallback_counts is None:
        raise ValueError(
            "noisy tier cannot achieve IoU band [0.6, 0.9] for this mask"
        )

    rng = np.random.default_rng(seed)
    structure = ndi.generate_binary_structure(gt.ndim, 1)
    score_fn = iou_2d if gt.ndim == 2 else iou_3d

    for _ in range(64):
        shifted = _translate_mask(gt, _sample_noisy_shift(gt.ndim, rng))
        candidate = _apply_random_morphology(shifted, structure, rng)
        if not candidate.any():
            continue
        iou = score_fn(candidate, gt)
        if _NOISY_IOU_MIN <= iou <= _NOISY_IOU_MAX:
            return candidate.astype(bool, copy=False)

    fallback = _translate_mask(gt, _fallback_noisy_shift(gt.shape))
    fallback_iou = score_fn(fallback, gt)
    if _NOISY_IOU_MIN <= fallback_iou <= _NOISY_IOU_MAX:
        return fallback.astype(bool, copy=False)
    return _build_mask_from_counts(gt, *fallback_counts)


def _sample_noisy_shift(ndim: int, rng: np.random.Generator) -> tuple[int, ...]:
    if ndim == 2:
        return tuple(int(rng.choice((-4, -3, -2, 2, 3, 4))) for _ in range(2))

    axis = int(rng.integers(0, ndim))
    shift = [0] * ndim
    shift[axis] = int(rng.choice((-4, -3, -2, 2, 3, 4)))
    return tuple(shift)


def _apply_random_morphology(
    mask: np.ndarray,
    structure: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    operation = str(rng.choice(("none", "erode", "dilate")))
    if operation == "erode":
        return ndi.binary_erosion(mask, structure=structure, iterations=1)
    if operation == "dilate":
        return ndi.binary_dilation(mask, structure=structure, iterations=1)
    return mask


def _fallback_noisy_shift(shape: tuple[int, ...]) -> tuple[int, ...]:
    if len(shape) == 2:
        return (2, 2)
    return (2, 0, 0)


def _translate_mask(mask: np.ndarray, shifts: tuple[int, ...]) -> np.ndarray:
    out = np.zeros_like(mask, dtype=bool)
    source_slices: list[slice] = []
    target_slices: list[slice] = []

    for size, shift in zip(mask.shape, shifts):
        if abs(shift) >= size:
            return out
        if shift >= 0:
            source_slices.append(slice(0, size - shift))
            target_slices.append(slice(shift, size))
        else:
            source_slices.append(slice(-shift, size))
            target_slices.append(slice(0, size + shift))

    out[tuple(target_slices)] = mask[tuple(source_slices)]
    return out


def _make_wrong_mask(gt: np.ndarray, seed: int) -> np.ndarray:
    if _minimum_non_empty_iou(gt) >= 0.2:
        raise ValueError("wrong tier cannot achieve IoU < 0.2 for this mask")

    bounds = _bounding_slices(gt)
    cropped = gt[bounds]
    rng = np.random.default_rng(seed)
    score_fn = iou_2d if gt.ndim == 2 else iou_3d

    corner_starts = _corner_starts(gt.shape, cropped.shape)
    if not corner_starts:
        return _fallback_wrong_mask(gt)

    order = rng.permutation(len(corner_starts))
    for index in order:
        candidate = _place_crop(gt.shape, cropped, corner_starts[int(index)])
        if candidate.any() and score_fn(candidate, gt) < 0.2:
            return candidate.astype(bool, copy=False)

    return _fallback_wrong_mask(gt)


def _bounding_slices(mask: np.ndarray) -> tuple[slice, ...]:
    coords = np.argwhere(mask)
    if coords.size == 0:
        raise ValueError("gt must contain at least one positive element")

    starts = coords.min(axis=0)
    stops = coords.max(axis=0) + 1
    return tuple(slice(int(start), int(stop)) for start, stop in zip(starts, stops))


def _corner_starts(
    full_shape: tuple[int, ...],
    crop_shape: tuple[int, ...],
) -> list[tuple[int, ...]]:
    options_per_axis: list[tuple[int, ...]] = []
    for full, crop in zip(full_shape, crop_shape):
        max_start = full - crop
        if max_start < 0:
            return []
        starts = (0,) if max_start == 0 else (0, max_start)
        options_per_axis.append(starts)
    return [tuple(start) for start in product(*options_per_axis)]


def _place_crop(
    full_shape: tuple[int, ...],
    cropped: np.ndarray,
    starts: tuple[int, ...],
) -> np.ndarray:
    out = np.zeros(full_shape, dtype=bool)
    slices = tuple(
        slice(start, start + size)
        for start, size in zip(starts, cropped.shape)
    )
    out[slices] = cropped
    return out


def _fallback_wrong_mask(gt: np.ndarray) -> np.ndarray:
    positive_count = int(gt.sum(dtype=np.int64))
    total_count = int(gt.size)
    negative_count = total_count - positive_count
    if positive_count == 0:
        raise ValueError("gt must contain at least one positive element")
    if negative_count > 0:
        return _build_mask_from_counts(gt, intersection_count=0, false_positive_count=1)
    return _build_mask_from_counts(gt, intersection_count=1, false_positive_count=0)


def _minimum_non_empty_iou(gt: np.ndarray) -> float:
    positive_count = int(gt.sum(dtype=np.int64))
    total_count = int(gt.size)
    negative_count = total_count - positive_count
    if positive_count == 0:
        raise ValueError("gt must contain at least one positive element")

    if negative_count > 0:
        return 0.0
    return 1.0 / positive_count


def _find_noisy_fallback_counts(gt: np.ndarray) -> tuple[int, int] | None:
    positive_count = int(gt.sum(dtype=np.int64))
    total_count = int(gt.size)
    negative_count = total_count - positive_count
    if positive_count == 0:
        raise ValueError("gt must contain at least one positive element")

    max_overlap = min(positive_count, total_count - 1)
    for intersection in range(max_overlap, 0, -1):
        min_union = max(positive_count, intersection)
        max_union = min(total_count, positive_count + negative_count + intersection)
        for union in range(min_union, max_union + 1):
            iou = intersection / union
            if _NOISY_IOU_MIN <= iou <= _NOISY_IOU_MAX:
                false_positives = union - positive_count
                if false_positives < 0 or false_positives > negative_count:
                    continue
                return intersection, false_positives
    return None


def _build_mask_from_counts(
    gt: np.ndarray,
    intersection_count: int,
    false_positive_count: int,
) -> np.ndarray:
    candidate = np.zeros_like(gt, dtype=bool)
    positive_coords = np.argwhere(gt)
    negative_coords = np.argwhere(~gt)

    if intersection_count < 0:
        raise ValueError("intersection_count must be non-negative")
    if false_positive_count < 0:
        raise ValueError("false_positive_count must be non-negative")
    if intersection_count + false_positive_count <= 0:
        raise ValueError("candidate mask must be non-empty")
    if intersection_count > len(positive_coords):
        raise ValueError("intersection_count exceeds positive voxel count")
    if false_positive_count > len(negative_coords):
        raise ValueError("false_positive_count exceeds negative voxel count")

    for coord in positive_coords[:intersection_count]:
        candidate[tuple(coord)] = True
    for coord in negative_coords[:false_positive_count]:
        candidate[tuple(coord)] = True
    return candidate
