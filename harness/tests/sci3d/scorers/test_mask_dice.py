"""Unit tests for compute_dice — upstream-parity Dice on 32x256x256 binary masks."""
from __future__ import annotations

import nibabel as nib
import numpy as np
import pytest

from sci3d_agent.trajectory import MetaBundle, ScoreBundle, TrajectoryRecord
from sci3d_bench.scorers.mask_dice import MaskDiceScorer, compute_dice
from sci3d_bench.scorers.nifti_io import load_mask_binary, preprocess_to_32x256x256
from sci3d_bench.task_schema import (
    AnswerSchema, AssetRef, GroundTruth, Provenance, ScorerSpec, TaskSpec,
)


@pytest.mark.unit
def test_dice_perfect_overlap_is_one():
    gt = np.ones((4, 8, 8), dtype=np.int8)
    pred_logits = np.full((4, 8, 8), 10.0, dtype=np.float32)  # sigmoid(10) ≈ 1
    assert compute_dice(pred_logits, gt) == pytest.approx(1.0, abs=3e-3)


@pytest.mark.unit
def test_dice_disjoint_pred_gt_is_near_zero():
    gt = np.zeros((4, 8, 8), dtype=np.int8)
    gt[:, :4, :] = 1
    pred_logits = np.full((4, 8, 8), -10.0, dtype=np.float32)
    pred_logits[:, 4:, :] = 10.0  # pred is the disjoint half
    dice = compute_dice(pred_logits, gt)
    # smooth=1 in denom; numerator is 0; result is 1/(N+M+1) ≈ 1/(128+128+1)
    assert dice < 0.01


@pytest.mark.unit
def test_dice_half_overlap_is_about_half():
    gt = np.ones((4, 8, 8), dtype=np.int8)
    pred_logits = np.full((4, 8, 8), -10.0, dtype=np.float32)
    pred_logits[:2, :, :] = 10.0  # predict only first half of D
    dice = compute_dice(pred_logits, gt)
    # 2 * 128 / (128 + 256 + 1) ≈ 0.665
    assert 0.6 < dice < 0.7


@pytest.mark.unit
def test_dice_empty_pred_and_gt_is_one_per_upstream_smooth():
    """Upstream M3D Dice with smooth=1 in denom only:
    empty pred + empty GT → 0 / 1 → 0 → 2*0/1 = 0. Verify we match upstream."""
    gt = np.zeros((4, 8, 8), dtype=np.int8)
    pred_logits = np.full((4, 8, 8), -10.0, dtype=np.float32)
    dice = compute_dice(pred_logits, gt)
    assert dice == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_dice_threshold_at_half_sigmoid():
    """sigmoid > 0.5 ⇔ logit > 0. Verify threshold semantics."""
    gt = np.ones((2, 4, 4), dtype=np.int8)
    pred_logits = np.full((2, 4, 4), 0.1, dtype=np.float32)  # just above threshold
    assert compute_dice(pred_logits, gt) == pytest.approx(1.0, abs=0.02)
    pred_logits[:] = -0.1
    assert compute_dice(pred_logits, gt) < 0.01


# --- MaskDiceScorer (Scorer Protocol wrapper) ---


def _build_refseg_task(mask_path: str, mask_id: int) -> TaskSpec:
    return TaskSpec(
        schema_version="1.0",
        task_id="m3d-refseg-test-001",
        v_class="V3",
        question="segment the stoma",
        asset=AssetRef(path="ct.nii.gz", format="nii.gz"),
        prerendered_image=None,
        answer_schema=AnswerSchema(kind="freeform"),
        ground_truth=GroundTruth(value={
            "mask_path": mask_path,
            "mask_id": mask_id,
            "mask_text": "postoperative stoma",
            "answer_text": "Sure, a postoperative stoma can be seen in the [SEG].",
        }),
        scorer=ScorerSpec(primary="mask_dice"),
        provenance=Provenance(
            source_dataset="m3d-refseg", source_qid=None,
            lifted_at="2026-05-21", lifter_version="manual",
        ),
    )


def _build_traj(final_answer):
    return TrajectoryRecord(
        schema_version="1.0", task_id="m3d-refseg-test-001",
        mode="mode1_no_vision", timestamp="2026-05-21T00:00:00+00:00",
        steps=[], final_answer=final_answer, timeout=False, error=None,
        violations=[], scores=ScoreBundle(),
        meta=MetaBundle(model="m3d-lamed", seed=0),
    )


def _write_synth_mask_nifti(tmp_path, label_value: int):
    arr = np.zeros((16, 64, 64), dtype=np.int16)
    arr[8:, :32, :] = label_value
    p = tmp_path / "mask.nii.gz"
    nib.save(nib.Nifti1Image(arr, affine=np.eye(4)), str(p))
    return p


@pytest.mark.unit
def test_mask_dice_scorer_perfect_overlap(tmp_path):
    mask_path = _write_synth_mask_nifti(tmp_path, label_value=2)
    pred_path = tmp_path / "pred.npy"
    # Build pred logits matching the GT after the same preprocess.
    gt_full = load_mask_binary(mask_path, mask_id=2)
    gt_resized = preprocess_to_32x256x256(gt_full, mode="nearest")
    pred_logits = np.where(gt_resized == 1, 10.0, -10.0).astype(np.float32)
    np.save(pred_path, pred_logits)

    task = _build_refseg_task(str(mask_path), mask_id=2)
    traj = _build_traj({"text": "stoma here", "mask_path": str(pred_path)})

    scorer = MaskDiceScorer()
    result = scorer.score(task, traj)
    assert result.metric == "mask_dice"
    assert result.value == pytest.approx(1.0, abs=3e-3)
    assert result.degraded is False


@pytest.mark.unit
def test_mask_dice_scorer_degraded_when_no_mask_path(tmp_path):
    mask_path = _write_synth_mask_nifti(tmp_path, label_value=2)
    task = _build_refseg_task(str(mask_path), mask_id=2)
    traj = _build_traj({"text": "no mask"})  # missing mask_path

    scorer = MaskDiceScorer()
    result = scorer.score(task, traj)
    assert result.degraded is True
    assert result.value == 0.0
    assert "mask_path" in result.raw.get("error", "")


@pytest.mark.unit
def test_mask_dice_scorer_degraded_when_ground_truth_missing(tmp_path):
    pred_path = tmp_path / "pred.npy"
    np.save(pred_path, np.zeros((32, 256, 256), dtype=np.float32))
    task = TaskSpec(
        schema_version="1.0", task_id="t", v_class="V3", question="?",
        asset=AssetRef(path="x.nii.gz", format="nii.gz"),
        prerendered_image=None,
        answer_schema=AnswerSchema(kind="freeform"),
        ground_truth=None,  # missing
        scorer=ScorerSpec(primary="mask_dice"),
        provenance=Provenance(
            source_dataset="m3d-refseg", source_qid=None,
            lifted_at="2026-05-21", lifter_version="manual",
        ),
    )
    traj = _build_traj({"text": "x", "mask_path": str(pred_path)})
    result = MaskDiceScorer().score(task, traj)
    assert result.degraded is True
    assert result.value == 0.0
