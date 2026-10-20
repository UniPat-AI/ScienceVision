"""Public base types for the groundtruth sub-package."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FillResult:
    """Immutable result returned by a GroundTruthFiller.

    Attributes:
        value: The structured ground-truth value (matching the task's
            ``answer_schema``); None when no value could be produced.
        status: One of ``auto_seed`` (LLM-derived seed; still needs expert
            sign-off), ``awaiting_expert`` (filler not applicable / not
            implemented), or ``needs_expert_annotation`` (kept for backward
            compat with builder_stage default).
        notes: Free-form audit note about how value was derived (or why it
            wasn't).  Stored under ``ground_truth.notes`` in the task dict.
        evidence_paths: Tuple of paths to artifacts that justify the value
            (e.g. the thumbnail image fed to the LLM).  Tuple, not list, so
            FillResult remains hashable.
        model: Model id that produced the value when applicable.
    """

    value: Any | None
    status: str
    notes: str = ""
    evidence_paths: tuple[str, ...] = ()
    model: str | None = None


class GroundTruthFiller(Protocol):
    """Protocol for ground-truth fillers.

    A filler takes the full task dict (read-only — do not mutate) and returns
    a FillResult.  Implementations should be deterministic in their non-LLM
    parts so failures are reproducible.
    """

    def fill(self, task: dict[str, Any]) -> FillResult: ...
