"""Upstream-parity Dice scorer for M3D-RefSeg.

Mirrors BinaryDice from BAAI-DCAI/M3D Bench/eval/metrics.py:
- threshold sigmoid > 0.5 (equivalently logit > 0)
- smooth=1 in denominator only
- per-sample Dice = 2*|p ∩ t| / (|p| + |t| + 1)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from sci3d_bench.scorers.base import ScoreResult
from sci3d_bench.scorers.nifti_io import load_mask_binary, preprocess_to_32x256x256


def compute_dice(
    pred_logits: NDArray[np.floating],
    gt_binary: NDArray[np.integer],
    smooth: float = 1.0,
) -> float:
    """Compute upstream-parity binary Dice on equally-shaped arrays.

    Threshold sigmoid > 0.5 (i.e. logit > 0); smooth is added once to
    the denominator (matches BAAI-DCAI/M3D Bench/eval/metrics.py).

    Args:
        pred_logits: float array; raw model logits (pre-sigmoid).
        gt_binary: integer array of same shape; values in {0, 1}.
        smooth: stabilization constant added to denominator only.

    Returns:
        Dice in [0, 1].

    Raises:
        ValueError: if pred_logits.shape != gt_binary.shape.
    """
    if pred_logits.shape != gt_binary.shape:
        raise ValueError(
            f"shape mismatch: pred {pred_logits.shape} vs gt {gt_binary.shape}"
        )
    pred = (pred_logits > 0.0).astype(np.float64)  # sigmoid > 0.5 ⇔ logit > 0
    gt = gt_binary.astype(np.float64)
    num = float((pred * gt).sum())
    den = float(pred.sum() + gt.sum() + smooth)
    return 2.0 * num / den


class MaskDiceScorer:
    """Scorer for M3D-RefSeg mask track. Dice in 32x256x256 voxel space."""

    name = "mask_dice"

    def score(self, task, traj) -> ScoreResult:
        gt_value = getattr(task.ground_truth, "value", None) if task.ground_truth else None
        if not isinstance(gt_value, dict) or "mask_path" not in gt_value or "mask_id" not in gt_value:
            return ScoreResult(
                value=0.0, metric=self.name,
                raw={"error": "ground_truth missing mask_path or mask_id"},
                degraded=True,
            )
        final = traj.final_answer or {}
        pred_path = final.get("mask_path") if isinstance(final, dict) else None
        if not pred_path:
            return ScoreResult(
                value=0.0, metric=self.name,
                raw={"error": "trajectory final_answer missing mask_path"},
                degraded=True,
            )

        try:
            gt_full = load_mask_binary(Path(gt_value["mask_path"]), int(gt_value["mask_id"]))
            # preprocess(mode="nearest") preserves input dtype (int8 from load_mask_binary)
            gt_resized = preprocess_to_32x256x256(gt_full, mode="nearest")
            pred_logits = np.load(pred_path)
            if pred_logits.shape != (32, 256, 256):
                return ScoreResult(
                    value=0.0, metric=self.name,
                    raw={"error": f"pred shape {pred_logits.shape} != (32,256,256)"},
                    degraded=True,
                )
            # upcasts to float32 if M1 ever saves float16 to save disk (no-op for float32)
            dice = compute_dice(pred_logits.astype(np.float32), gt_resized)
        except (FileNotFoundError, OSError, ValueError) as e:
            return ScoreResult(
                value=0.0, metric=self.name,
                raw={"error": f"{type(e).__name__}: {e}"},
                degraded=True,
            )
        return ScoreResult(
            value=float(dice),
            metric=self.name,
            raw={"dice": float(dice), "shape": list(pred_logits.shape)},
            degraded=False,
        )
