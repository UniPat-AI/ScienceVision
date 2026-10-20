"""NotImplementedFiller: status=awaiting_expert for all known-but-unsupported types.

Registered eagerly for every ground_truth_type known to appear in
WalkingSkeleton_LLM so dispatch() returns a stable, descriptive note
("not yet implemented for type=X") instead of the generic "no filler
registered" fallback.  Unknown types still hit the generic fallback inside
dispatch().
"""
from __future__ import annotations

from typing import Any

from .base import FillResult
from .registry import FILLERS

_UNIMPLEMENTED_TYPES: tuple[str, ...] = (
    "expert_camera_pose",
    "expert_category_label",
    "rotation_invariant_label",
    "mirror_invariant_label",
    "view_invariant_label",
    "observer_transform_invariant_label",
    "kinematic_invariant_label",
    "paired_variant_relation",
    "minimal_trace_or_upper_bound",
    "expert_slice_index",
    "slice_plus_image_annotation",
    "expert_viewport_range",
    "viewport_plus_image_annotation",
    "frame_sequence_annotation",
)


class _NotImplementedFiller:
    """Returns awaiting_expert with a per-type note."""

    def __init__(self, gt_type: str) -> None:
        self._gt_type = gt_type

    def fill(self, task: dict[str, Any]) -> FillResult:
        return FillResult(
            value=None,
            status="awaiting_expert",
            notes=(
                f"Filler not yet implemented for type={self._gt_type!r}; "
                "task remains gated for expert annotation."
            ),
        )


def _register_all() -> None:
    for t in _UNIMPLEMENTED_TYPES:
        if t in FILLERS:
            continue
        FILLERS[t] = _NotImplementedFiller(t)


_register_all()
