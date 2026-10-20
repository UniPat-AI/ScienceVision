"""Text-only shortcut probe for measuring 3D-groundedness of questions.

A genuinely 3D-grounded question should NOT be answerable from the prompt text
alone (no viewer, no render, no interaction).  This module builds a solver
callable that attempts exactly that — answer from the instruction + answer
schema + asset *metadata* only — so it can be fed to
``metrics.compute_quality_metrics(tasks, solver=...)``.  A HIGH text-only solve
rate is bad: it means the task can be shortcut without touching the 3D content.

The probe is opt-in and LLM-backed (it needs a chat client + credentials); it is
never invoked by the deterministic pipeline.  ``make_text_only_solver`` returns
a callable that is safe to map over a task list — it never raises.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are given ONLY the text of a 3D-QA task: its instruction, its answer "
    "schema, and shallow asset metadata (name/format). You CANNOT open the 3D "
    "viewer, render, rotate, slice, or interact. Attempt the answer from text "
    "alone. If the correct answer genuinely cannot be determined without "
    "inspecting the 3D content, say so.\n\n"
    "Return strict JSON only: "
    '{"answer": <your best answer or null>, "answerable_text_only": <bool>, '
    '"confidence": <0..1>}'
)


def _extract_ground_truth(task: dict[str, Any]) -> Any:
    gt = task.get("ground_truth", {}) or {}
    return gt.get("value")


def _build_user(task: dict[str, Any]) -> str:
    payload = {
        "instruction": task.get("instruction"),
        "answer_schema": task.get("answer_schema"),
        "asset_metadata": {
            "name": (task.get("asset") or {}).get("name"),
            "format": (task.get("asset") or {}).get("format"),
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def make_text_only_solver(
    chat_client: Any,
    model: str,
    *,
    require_correct: bool = True,
    temperature: float = 0.0,
) -> Callable[[dict[str, Any]], bool]:
    """Return ``solver(task) -> bool``: True if the task was solved text-only.

    Args:
        chat_client: an object exposing ``complete_with_retry(model, system,
            user, temperature=..., json_mode=True)`` (e.g. ChatClient).
        model: model id to route the probe through.
        require_correct: when True (default) a task counts as "solved text-only"
            only if the model's answer matches ``ground_truth.value`` (so the
            probe measures genuine shortcut-solvability, not mere confidence).
            When ground truth is absent, falls back to the model's own
            ``answerable_text_only`` flag.  When False, uses the flag directly.
    """

    def solver(task: dict[str, Any]) -> bool:
        try:
            from sciencevision_taskgen.llm import parse_json_lenient

            result = chat_client.complete_with_retry(
                model,
                _SYSTEM,
                _build_user(task),
                temperature=temperature,
                json_mode=True,
            )
            parsed = parse_json_lenient(result.content)
            if not isinstance(parsed, dict):
                return False
            gt = _extract_ground_truth(task)
            if require_correct and gt is not None:
                return _answers_match(parsed.get("answer"), gt)
            return bool(parsed.get("answerable_text_only"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("text-only probe failed for %s: %r", task.get("task_id"), exc)
            return False

    return solver


def _answers_match(pred: Any, gt: Any) -> bool:
    """Loose equality for probe answers vs ground truth."""
    if pred is None:
        return False
    if isinstance(gt, dict) and isinstance(pred, dict):
        return all(
            str(pred.get(k)).strip().lower() == str(v).strip().lower()
            for k, v in gt.items()
        )
    return str(pred).strip().lower() == str(gt).strip().lower()
