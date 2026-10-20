"""NIfTI I/O + preprocessing to upstream M3D-RefSeg target shape (32, 256, 256).

Mirrors the resize/MinMax pipeline from BAAI-DCAI/M3D's
m3d_refseg_data_prepare.py — trilinear for CT, nearest-neighbor for masks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import nibabel as nib
import numpy as np
from skimage.transform import resize


TARGET_SHAPE = (32, 256, 256)


def load_ct(path: str | Path) -> np.ndarray:
    """Load a NIfTI CT volume as float32, raw HU values."""
    img = nib.load(str(path))
    return np.asarray(img.dataobj, dtype=np.float32)


def load_mask_binary(path: str | Path, mask_id: int) -> np.ndarray:
    """Load a NIfTI multi-label mask and binarize via (mask == mask_id).

    Note:
        mask_id=0 would return the background mask (complement of all
        foreground labels). M3D-RefSeg uses 1-indexed foreground IDs,
        so callers should pass mask_id >= 1 unless intentionally
        selecting background.
    """
    img = nib.load(str(path))
    arr = np.asarray(img.dataobj)
    return (arr == mask_id).astype(np.int8)


def preprocess_to_32x256x256(
    arr: np.ndarray,
    mode: Literal["trilinear", "nearest"],
) -> np.ndarray:
    """Resize to (32, 256, 256) and (for trilinear) MinMax normalize to [0, 1].

    - trilinear: order=1, anti_aliasing=True. For CT volumes; output normalized.
    - nearest:   order=0, anti_aliasing=False. For label masks; preserves labels.
    """
    if mode == "trilinear":
        out = resize(
            arr.astype(np.float32),
            TARGET_SHAPE,
            order=1,
            mode="constant",
            anti_aliasing=True,
            preserve_range=True,
        ).astype(np.float32)
        lo, hi = float(out.min()), float(out.max())
        if hi > lo:
            out = (out - lo) / (hi - lo)
        else:
            out = np.zeros_like(out)
        return out
    if mode == "nearest":
        out = resize(
            arr,
            TARGET_SHAPE,
            order=0,
            mode="constant",
            anti_aliasing=False,
            preserve_range=True,
        )
        return out.astype(arr.dtype)
    raise ValueError(f"mode must be 'trilinear' or 'nearest', got {mode!r}")
