"""Filler registry and dispatch.

Fillers register themselves by decorating their class with ``@register(type)``
at module import time.  ``dispatch(task)`` looks up the filler by
``task["ground_truth"]["type"]`` and returns the FillResult; unknown types
route to a built-in NotImplemented filler that returns ``awaiting_expert``.
"""
from __future__ import annotations

from typing import Any

from .base import FillResult, GroundTruthFiller

FILLERS: dict[str, GroundTruthFiller] = {}


def register(gt_type: str):
    """Class decorator: instantiate and register the filler under gt_type.

    Raises:
        ValueError: if gt_type is already registered.  Tests can ``del
        FILLERS[gt_type]`` to clean up.
    """

    def _wrap(cls: type) -> type:
        if gt_type in FILLERS:
            raise ValueError(
                f"ground_truth type {gt_type!r} already registered "
                f"with {type(FILLERS[gt_type]).__name__!r}"
            )
        FILLERS[gt_type] = cls()
        return cls

    return _wrap


def dispatch(task: dict[str, Any]) -> FillResult:
    """Look up the filler for ``task['ground_truth']['type']`` and run it."""
    gt_type = task.get("ground_truth", {}).get("type", "")
    filler = FILLERS.get(gt_type)
    if filler is None:
        return FillResult(
            value=None,
            status="awaiting_expert",
            notes=f"No filler registered for ground_truth.type={gt_type!r}",
        )
    return filler.fill(task)
