"""Distractor generation for categorical ScienceVision answers.

The generator currently emits free-form structured answers and no
multiple-choice distractors.  For enumerated categorical answers, well-chosen
distractors (plausible near-misses, not obviously-wrong fillers) make the task
harder to guess and give the scorer a clean option set.

Two paths:

* :func:`distractors_from_enum` — deterministic.  Given an answer schema with
  an ``enums`` block and the correct value, returns the other enum members as
  distractors (optionally ranked by lexical closeness to the correct label so
  the "nearest" wrong options come first).  No LLM, no network.

* :func:`make_llm_distractor_generator` — opt-in.  Returns a callable that asks
  a chat client for *hard* near-miss distractors grounded in the asset, for
  cases where a fixed enum is not rich enough.  Never raises; falls back to the
  deterministic path (or empty) on any failure.
"""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def distractors_from_enum(
    answer_schema: dict[str, Any],
    correct_value: Any,
    *,
    field: str | None = None,
    max_distractors: int | None = None,
    rank_by_closeness: bool = True,
) -> list[str]:
    """Return enum members other than *correct_value* as distractors.

    Args:
        answer_schema: the task's ``answer_schema`` (must contain ``enums``).
        correct_value: the correct label to exclude.  May be a scalar or, for
            multi-field answers, a dict keyed by field name.
        field: which enum field to draw from.  Defaults to the single enum
            field, or the first, when there are several.
        max_distractors: cap on how many to return (None = all).
        rank_by_closeness: put the most lexically-similar wrong options first.
    """
    enums = (answer_schema or {}).get("enums") or {}
    if not enums:
        return []
    if field is None:
        field = next(iter(enums)) if len(enums) == 1 else next(iter(enums))
    options = [str(o) for o in enums.get(field, [])]
    if not options:
        return []

    correct = correct_value.get(field) if isinstance(correct_value, dict) else correct_value
    correct_str = None if correct is None else str(correct)
    pool = [o for o in options if o != correct_str]

    if rank_by_closeness and correct_str is not None:
        pool.sort(key=lambda o: _similarity(o, correct_str), reverse=True)
    if max_distractors is not None:
        pool = pool[:max_distractors]
    return pool


_SYSTEM = (
    "You write HARD multiple-choice distractors for a 3D-QA task. Given the "
    "question, the correct answer, and asset metadata, produce plausible but "
    "incorrect options that a non-expert might pick — near-misses in the same "
    "category, NOT obviously-wrong fillers. Do not restate the correct answer.\n"
    'Return strict JSON only: {"distractors": [string, ...]}'
)


def make_llm_distractor_generator(
    chat_client: Any,
    model: str,
    *,
    fallback_schema: bool = True,
) -> Callable[[dict[str, Any], int], list[str]]:
    """Return ``generate(task, n) -> list[str]`` of hard distractors.

    Opt-in / LLM-backed.  Never raises: on any failure returns the
    deterministic enum distractors (when ``fallback_schema``) or an empty list.
    """

    def generate(task: dict[str, Any], n: int = 3) -> list[str]:
        try:
            from sciencevision_taskgen.llm import parse_json_lenient

            gt = (task.get("ground_truth") or {}).get("value")
            payload = json.dumps(
                {
                    "question": task.get("instruction"),
                    "correct_answer": gt,
                    "answer_schema": task.get("answer_schema"),
                    "asset": {"name": (task.get("asset") or {}).get("name")},
                    "n": n,
                },
                ensure_ascii=False,
            )
            result = chat_client.complete_with_retry(
                model, _SYSTEM, payload, temperature=0.4, json_mode=True
            )
            parsed = parse_json_lenient(result.content)
            items = parsed.get("distractors") if isinstance(parsed, dict) else None
            correct_str = None if gt is None else str(gt)
            out = [
                str(x)
                for x in (items or [])
                if isinstance(x, (str, int, float)) and str(x) != correct_str
            ]
            if out:
                return out[:n]
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM distractor gen failed for %s: %r", task.get("task_id"), exc)
        if fallback_schema:
            return distractors_from_enum(
                task.get("answer_schema", {}),
                (task.get("ground_truth") or {}).get("value"),
                max_distractors=n,
            )
        return []

    return generate
