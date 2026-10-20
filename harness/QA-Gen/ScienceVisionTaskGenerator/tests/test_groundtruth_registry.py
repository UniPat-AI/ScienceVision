"""Tests for the groundtruth base types and registry."""
from __future__ import annotations

import pytest

from sciencevision_taskgen.groundtruth.base import FillResult


def test_fill_result_is_frozen():
    r = FillResult(
        value={"fully_visible": True},
        status="auto_seed",
        notes="ok",
        evidence_paths=("viewer/thumbs/abc.webp",),
        model="claude-sonnet-4-6",
    )
    with pytest.raises(Exception):  # FrozenInstanceError is a dataclasses.FrozenInstanceError
        r.value = {"fully_visible": False}  # type: ignore[misc]


def test_fill_result_defaults():
    r = FillResult(value=None, status="awaiting_expert")
    assert r.notes == ""
    assert r.evidence_paths == ()
    assert r.model is None


def test_fill_result_status_must_be_known_string():
    # Any string is allowed at the type level; documented set is checked by tests
    # not at construction time.
    r = FillResult(value=None, status="awaiting_expert")
    assert r.status in {"auto_seed", "awaiting_expert", "needs_expert_annotation"}


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

from sciencevision_taskgen.groundtruth import dispatch, register
from sciencevision_taskgen.groundtruth.registry import FILLERS


class _FixedFiller:
    def __init__(self, status: str) -> None:
        self._status = status

    def fill(self, task: dict) -> FillResult:
        return FillResult(value={"ok": True}, status=self._status)


def test_register_adds_to_FILLERS():
    @register("test_type_1")
    class A(_FixedFiller):
        def __init__(self) -> None:
            super().__init__("auto_seed")

    assert "test_type_1" in FILLERS
    assert isinstance(FILLERS["test_type_1"], A)
    del FILLERS["test_type_1"]


def test_register_rejects_duplicate():
    @register("test_type_2")
    class A(_FixedFiller):
        def __init__(self) -> None:
            super().__init__("auto_seed")

    with pytest.raises(ValueError, match="already registered"):
        @register("test_type_2")
        class B(_FixedFiller):
            def __init__(self) -> None:
                super().__init__("auto_seed")

    del FILLERS["test_type_2"]


def test_dispatch_returns_filler_for_known_type():
    @register("test_type_3")
    class A(_FixedFiller):
        def __init__(self) -> None:
            super().__init__("auto_seed")

    task = {"task_id": "t1", "ground_truth": {"type": "test_type_3"}}
    result = dispatch(task)
    assert result.status == "auto_seed"
    assert result.value == {"ok": True}

    del FILLERS["test_type_3"]


def test_dispatch_uses_not_implemented_for_unknown_type():
    task = {"task_id": "t1", "ground_truth": {"type": "does_not_exist"}}
    result = dispatch(task)
    assert result.status == "awaiting_expert"
    assert result.value is None
    assert "does_not_exist" in result.notes


def test_known_unsupported_types_route_to_awaiting_expert():
    for gt_type in [
        "expert_camera_pose",
        "expert_category_label",
        "rotation_invariant_label",
        "minimal_trace_or_upper_bound",
        "expert_slice_index",
    ]:
        task = {"task_id": "t", "ground_truth": {"type": gt_type}}
        r = dispatch(task)
        assert r.status == "awaiting_expert", gt_type
        assert "not yet implemented" in r.notes.lower(), gt_type
