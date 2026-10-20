"""Deterministic scorer for ordered three-axis voxel extents."""

from __future__ import annotations

import json
import math
from typing import Any

from sci3d_bench.scorers.base import ScoreResult


AXIS_ORDER = ("axis-0", "axis-1", "axis-2")
EXPECTED_FIELDS = ("extent_vox",)
EXPECTED_CHECKS = ("extent_vox_axis_order", "extent_vox_per_axis_tolerance")
AGGREGATION = "all_required_with_partial_credit"
TOLERANCE_VOX = 3.0


def _get(owner: Any, key: str, default: Any = None) -> Any:
    if isinstance(owner, dict):
        return owner.get(key, default)
    value = getattr(owner, key, default)
    # Older compact TaskSpec revisions retain rich scorer settings under
    # ``config``.  Reading that fallback keeps this deterministic scorer
    # compatible with pinned verifier images without silently weakening its
    # contract checks.
    if value is default:
        config = getattr(owner, "config", None)
        if isinstance(config, dict):
            return config.get(key, default)
    return value


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ordered_int_triplet(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    return tuple(value)


def _answer_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    nested = value.get("answer")
    return nested if isinstance(nested, dict) else value


class ExtentVoxelToleranceScorer:
    """Score ``extent_vox`` positionally in NIfTI array-axis order.

    Each of axis-0, axis-1, and axis-2 earns one third of the score when the
    submitted integer count is within the inclusive +/-3 voxel tolerance.
    Axes are never sorted or matched as a set.
    """

    name = "extent_voxel_tolerance_v1"

    def _config_errors(self, task: Any) -> list[str]:
        errors: list[str] = []
        scorer = _get(task, "scorer", {})
        checks = tuple(_get(scorer, "checks", ()) or ())
        aggregation = _get(scorer, "aggregation")
        tolerance = _object(_get(scorer, "tolerance"))
        expected_fields = tuple(_get(scorer, "expected_fields", ()) or ())
        axis_order = tuple(_get(scorer, "axis_order", ()) or ())
        if checks != EXPECTED_CHECKS:
            errors.append("invalid_checks")
        if aggregation != AGGREGATION:
            errors.append("invalid_aggregation")
        tolerance_value = tolerance.get("extent_vox")
        if (
            isinstance(tolerance_value, bool)
            or not isinstance(tolerance_value, (int, float))
            or not math.isfinite(float(tolerance_value))
            or float(tolerance_value) != TOLERANCE_VOX
        ):
            errors.append("invalid_extent_tolerance")
        if expected_fields != EXPECTED_FIELDS:
            errors.append("invalid_expected_fields")
        if axis_order != AXIS_ORDER:
            errors.append("invalid_scorer_axis_order")

        answer_schema = _get(task, "answer_schema", {})
        if tuple(_get(answer_schema, "fields", ()) or ()) != EXPECTED_FIELDS:
            errors.append("invalid_answer_fields")
        field_specs = _object(_get(answer_schema, "field_specs"))
        extent_spec = _object(field_specs.get("extent_vox"))
        if (
            extent_spec.get("type") != "integer_triplet"
            or extent_spec.get("unit") != "voxel"
            or extent_spec.get("length") != 3
            or tuple(extent_spec.get("axis_order", ())) != AXIS_ORDER
        ):
            errors.append("invalid_answer_extent_spec")

        raw_task = _object(_get(task, "raw_task"))
        terminal = _object(raw_task.get("terminal_answer"))
        raw_gt = _object(raw_task.get("ground_truth"))
        if tuple(terminal.get("axis_order", ())) != AXIS_ORDER:
            errors.append("invalid_terminal_axis_order")
        if terminal.get("format") != "json_object":
            errors.append("invalid_terminal_format")
        if tuple(raw_gt.get("axis_order", ())) != AXIS_ORDER:
            errors.append("invalid_ground_truth_axis_order")
        return errors

    def score(self, task: Any, traj: Any) -> ScoreResult:
        config_errors = self._config_errors(task)
        if config_errors:
            return ScoreResult(
                value=0.0,
                metric=self.name,
                raw={"config_errors": config_errors},
                degraded=True,
            )

        gt = _get(_get(task, "ground_truth", {}), "value")
        expected = _ordered_int_triplet(_object(gt).get("extent_vox"))
        if expected is None:
            return ScoreResult(
                value=0.0,
                metric=self.name,
                raw={"reason": "invalid_ground_truth_extent_vox"},
                degraded=True,
            )

        answer = _answer_object(_get(traj, "final_answer"))
        actual = _ordered_int_triplet(answer.get("extent_vox")) if answer is not None else None
        if actual is None:
            return ScoreResult(
                value=0.0,
                metric=self.name,
                raw={
                    "reason": "answer_must_be_json_object_with_integer_extent_vox_triplet",
                    "axis_order": list(AXIS_ORDER),
                },
                degraded=True,
            )

        deltas = tuple(abs(got - want) for got, want in zip(actual, expected))
        per_axis = tuple(delta <= TOLERANCE_VOX for delta in deltas)
        matched = sum(per_axis)
        return ScoreResult(
            value=matched / len(AXIS_ORDER),
            metric=self.name,
            raw={
                "axis_order": list(AXIS_ORDER),
                "expected": list(expected),
                "actual": list(actual),
                "absolute_error_vox": list(deltas),
                "tolerance_vox_inclusive": TOLERANCE_VOX,
                "per_axis_within_tolerance": {
                    axis: passed for axis, passed in zip(AXIS_ORDER, per_axis)
                },
                "matched_axes": matched,
                "total_axes": len(AXIS_ORDER),
                "all_axes_within_tolerance": all(per_axis),
                "aggregation": AGGREGATION,
            },
        )
