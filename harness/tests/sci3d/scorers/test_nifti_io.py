"""Unit tests for nifti_io — load + preprocess CT and GT masks to 32x256x256."""
from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from sci3d_bench.scorers.nifti_io import (
    load_ct,
    load_mask_binary,
    preprocess_to_32x256x256,
)


def _write_synth_nifti(tmp: Path, name: str, arr: np.ndarray) -> Path:
    img = nib.Nifti1Image(arr, affine=np.eye(4))
    p = tmp / name
    nib.save(img, str(p))
    return p


@pytest.mark.unit
def test_load_ct_returns_float_array(tmp_path):
    arr = np.arange(64, dtype=np.int16).reshape((4, 4, 4)) * 10
    p = _write_synth_nifti(tmp_path, "ct.nii.gz", arr)
    ct = load_ct(p)
    assert ct.shape == (4, 4, 4)
    assert ct.dtype == np.float32


@pytest.mark.unit
def test_load_mask_binary_selects_label(tmp_path):
    arr = np.zeros((4, 4, 4), dtype=np.int16)
    arr[1, :, :] = 2
    arr[2, :, :] = 5
    p = _write_synth_nifti(tmp_path, "mask.nii.gz", arr)
    binary = load_mask_binary(p, mask_id=2)
    assert binary.shape == (4, 4, 4)
    assert binary.dtype == np.int8
    assert binary[1, 0, 0] == 1
    assert binary[2, 0, 0] == 0  # label 5, not 2
    assert binary[0, 0, 0] == 0


@pytest.mark.unit
def test_preprocess_ct_to_target_shape_and_range():
    arr = (np.random.RandomState(0).rand(16, 64, 64) * 1000).astype(np.float32)
    out = preprocess_to_32x256x256(arr, mode="trilinear")
    assert out.shape == (32, 256, 256)
    assert out.dtype == np.float32
    assert 0.0 <= out.min() and out.max() <= 1.0


@pytest.mark.unit
def test_preprocess_mask_preserves_binary_values():
    arr = np.zeros((16, 64, 64), dtype=np.int8)
    arr[8:, :32, :] = 1
    out = preprocess_to_32x256x256(arr, mode="nearest")
    assert out.shape == (32, 256, 256)
    # Nearest preserves discrete labels — only 0 and 1 should appear.
    unique = set(np.unique(out).tolist())
    assert unique == {0, 1}


@pytest.mark.unit
def test_preprocess_already_target_shape_is_passthrough_in_range():
    arr = np.full((32, 256, 256), 0.5, dtype=np.float32)
    out = preprocess_to_32x256x256(arr, mode="trilinear")
    assert out.shape == (32, 256, 256)
    # Constant-input branch returns zeros (MinMax of constant is undefined → 0).
    assert out.min() == pytest.approx(0.0)
    assert out.max() == pytest.approx(0.0)


@pytest.mark.unit
def test_load_mask_binary_returns_zeros_for_absent_label(tmp_path):
    """Real production case: the requested organ isn't present in this volume."""
    arr = np.zeros((4, 4, 4), dtype=np.int16)
    arr[1, :, :] = 2
    arr[2, :, :] = 5
    p = _write_synth_nifti(tmp_path, "mask.nii.gz", arr)
    binary = load_mask_binary(p, mask_id=99)  # not present in file
    assert binary.shape == (4, 4, 4)
    assert binary.dtype == np.int8
    assert binary.sum() == 0
