"""Tests for the offline medical-spatial scorer."""
from __future__ import annotations

import pathlib
import sys

import nibabel as nib
import numpy as np
import pytest

# The scorer is a script next to the demo data, not a package — import via path.
EXAMPLE_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "examples" / "02_medical_spatial_gt"
)
sys.path.insert(0, str(EXAMPLE_DIR))
import score  # noqa: E402


def test_extract_answer_finds_boxed_token() -> None:
    assert score.extract_answer(r"my answer is \boxed{Kidney}.") == "Kidney"
    assert score.extract_answer(r"  \boxed{Renal vein}  ") == "Renal vein"


def test_extract_answer_returns_none_when_missing() -> None:
    assert score.extract_answer("Kidney") is None
    assert score.extract_answer("") is None


def test_ground_truth_picks_largest_label(tmp_path: pathlib.Path) -> None:
    # 10x10x10 volume: label 2 occupies a 6x6x6 block (216 voxels),
    # labels 1, 3, 4 each get a single voxel.
    arr = np.zeros((10, 10, 10), dtype=np.int16)
    arr[:6, :6, :6] = 2
    arr[0, 0, 9] = 1
    arr[0, 9, 0] = 3
    arr[9, 0, 0] = 4
    nii = nib.Nifti1Image(arr, affine=np.eye(4))
    mask_path = tmp_path / "mask.nii.gz"
    nib.save(nii, str(mask_path))

    assert score.ground_truth(mask_path) == "Kidney"


def test_main_exit_2_when_prediction_missing(tmp_path: pathlib.Path) -> None:
    rc = score.main(["--prediction", str(tmp_path / "nope.txt")])
    assert rc == 2


def test_main_exit_2_when_mask_missing(tmp_path: pathlib.Path) -> None:
    pred = tmp_path / "ans.txt"
    pred.write_text(r"\boxed{Kidney}")
    rc = score.main(["--prediction", str(pred), "--mask", str(tmp_path / "no-mask.nii.gz")])
    assert rc == 2


def test_main_exit_1_on_mismatch_and_0_on_match(tmp_path: pathlib.Path) -> None:
    # Build a small mask where label 4 (Tumor) wins.
    arr = np.zeros((6, 6, 6), dtype=np.int16)
    arr[:5, :5, :5] = 4
    arr[0, 0, 0] = 1
    mask = tmp_path / "mask.nii.gz"
    nib.save(nib.Nifti1Image(arr, affine=np.eye(4)), str(mask))

    pred_match = tmp_path / "match.txt"
    pred_match.write_text(r"\boxed{Tumor}")
    assert score.main(["--prediction", str(pred_match), "--mask", str(mask)]) == 0

    pred_miss = tmp_path / "miss.txt"
    pred_miss.write_text(r"\boxed{Kidney}")
    assert score.main(["--prediction", str(pred_miss), "--mask", str(mask)]) == 1
