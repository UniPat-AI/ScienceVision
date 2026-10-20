"""Structured exact-match scorer for tasks with a ground-truth answer value.

Used by Genesis physics-VQA tasks (and any task whose `scorer.primary` is
`exact_match`): the ground truth is a structured dict of field -> expected
value, and the agent's terminal answer should reproduce those fields. Score is
the fraction of fields matched (partial credit), so a 3-field answer that gets
2 right scores 0.667.

Matching is tolerant of how the agent serializes its answer:
- dict final_answer            -> compared field by field.
- JSON-string final_answer     -> parsed, then compared field by field.
- plain-string final_answer    -> each GT field value is searched for as a
                                  normalized substring (last-resort, so a free
                                  text answer that names the right values still
                                  earns credit).

Value comparison is normalized: booleans, numbers, and strings are lowercased /
stringified and stripped so "True"/true/"true" all match, and "~15-20cm" vs
"~15-20 cm" matches after whitespace removal.
"""
from __future__ import annotations

import json
from typing import Any

from sci3d_bench.scorers.base import ScoreResult


def _normalize(v: Any) -> str:
    """Normalize a scalar for tolerant comparison."""
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).strip().lower()
    # collapse internal whitespace so "~15-20 cm" == "~15-20cm"
    return "".join(s.split())


def _gt_value(task: Any) -> dict | None:
    gt = getattr(task, "ground_truth", None)
    value = getattr(gt, "value", None) if gt is not None else None
    return value if isinstance(value, dict) else None


def _answer_as_dict(answer: Any) -> dict | None:
    if isinstance(answer, dict):
        return answer
    if isinstance(answer, str):
        try:
            parsed = json.loads(answer)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


class ExactMatchScorer:
    """Field-wise exact match against the structured ground-truth value."""

    name = "exact_match"

    def score(self, task, traj) -> ScoreResult:
        gt = _gt_value(task)
        if not gt:
            # No structured GT to match against; degrade (judge still runs).
            return ScoreResult(
                value=0.0,
                metric=self.name,
                raw={"reason": "no structured ground_truth.value"},
                degraded=True,
            )

        answer = getattr(traj, "final_answer", None)
        ans_dict = _answer_as_dict(answer)

        per_field: dict[str, bool] = {}
        if ans_dict is not None:
            for key, expected in gt.items():
                got = ans_dict.get(key)
                if expected is None:
                    # A null ground-truth value is a value: only a null passes.
                    per_field[key] = got is None
                else:
                    per_field[key] = (
                        got is not None and _normalize(got) == _normalize(expected)
                    )
        else:
            # Free-text fallback: credit a field if its expected value appears
            # as a normalized substring of the (normalized) answer text. A null
            # has no textual evidence, so only the structured branch can
            # credit it.
            hay = _normalize(answer) if answer is not None else ""
            for key, expected in gt.items():
                if expected is None:
                    per_field[key] = False
                    continue
                needle = _normalize(expected)
                per_field[key] = bool(needle) and needle in hay

        matched = sum(1 for ok in per_field.values() if ok)
        total = len(gt)
        value = matched / total if total else 0.0
        return ScoreResult(
            value=value,
            metric=self.name,
            raw={
                "matched": matched,
                "total": total,
                "per_field": per_field,
                "answer_kind": "dict" if ans_dict is not None else "text",
            },
        )
