"""Tests for the three new ChallengerStage contract blockers (Task 4).

Blockers added:
  - missing_terminal_answer   (critical)
  - ground_truth_not_verified (high)
  - answer_is_process_only    (critical)
"""

from __future__ import annotations

import copy

import pytest

from sciencevision_taskgen.pipeline.challenger_stage import ChallengerStage


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _complete_task() -> dict:
    """Return a minimal task dict that satisfies ALL existing checks.

    Mutate a copy of this to trigger specific blockers.
    """
    return {
        "task_id": "test-001",
        "domain": "biology",
        "v_class": "V2",
        "asset": {
            "path": "datasets/biology/4HHB.pdb",
            "viewer": "molstar",
            "format": "pdb",
            "exists_on_disk": True,
        },
        "instruction": "Identify the symmetry axis of the protein.",
        "required_evidence": ["screenshot"],
        "answer_schema": {"kind": "value", "fields": ["symmetry_axis"]},
        "ground_truth": {"status": "expert_verified", "value": "C2"},
        "scorer": {
            "primary": "exact_match",
            "checks": ["exact_match_check"],
        },
        "tool_policy": {
            "allowed_tools": ["molstar_viewer"],
        },
        "quality_gates": {"irreducible_3d": True},
        # New required fields (Tasks 1-3)
        "terminal_answer": {
            "kind": "value",
            "fields": ["symmetry_axis"],
        },
        "active_vision_evidence": {"screenshot": True},
        "validation_record": {},
        "asset_grounding": {},
        "expert_review_requirements": {},
    }


@pytest.fixture()
def challenger() -> ChallengerStage:
    return ChallengerStage()


def _critique(task: dict, challenger: ChallengerStage) -> dict:
    return challenger._critique_task(task)


def _issue_codes(result: dict) -> list[str]:
    return [i["code"] for i in result["issues"]]


# ---------------------------------------------------------------------------
# Test 1 — complete valid task produces no new blocker codes
# ---------------------------------------------------------------------------


def test_complete_valid_task_no_new_blockers(challenger: ChallengerStage) -> None:
    result = _critique(_complete_task(), challenger)
    codes = _issue_codes(result)
    assert "missing_terminal_answer" not in codes
    assert "ground_truth_not_verified" not in codes
    assert "answer_is_process_only" not in codes
    assert result["severity"] == "low"
    assert result["recommendation"] == "accept"


# ---------------------------------------------------------------------------
# Test 2 — missing terminal_answer key → missing_terminal_answer critical
# ---------------------------------------------------------------------------


def test_missing_terminal_answer_key(challenger: ChallengerStage) -> None:
    task = _complete_task()
    del task["terminal_answer"]
    result = _critique(task, challenger)
    codes = _issue_codes(result)
    assert "missing_terminal_answer" in codes
    blocker = next(i for i in result["issues"] if i["code"] == "missing_terminal_answer")
    assert blocker["severity"] == "critical"
    assert result["recommendation"] == "reject"


# ---------------------------------------------------------------------------
# Test 3 — terminal_answer.fields = [] → missing_terminal_answer
# ---------------------------------------------------------------------------


def test_terminal_answer_empty_fields(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["terminal_answer"] = {"kind": "value", "fields": []}
    result = _critique(task, challenger)
    assert "missing_terminal_answer" in _issue_codes(result)


# ---------------------------------------------------------------------------
# Test 4 — ground_truth.status unverified variants → ground_truth_not_verified
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["needs_expert_annotation", "seed", "unverified", None, ""],
)
def test_ground_truth_not_verified_statuses(
    status: str | None, challenger: ChallengerStage
) -> None:
    task = _complete_task()
    task["ground_truth"] = {"status": status}
    result = _critique(task, challenger)
    codes = _issue_codes(result)
    assert "ground_truth_not_verified" in codes
    blocker = next(i for i in result["issues"] if i["code"] == "ground_truth_not_verified")
    assert blocker["severity"] == "high"


# ---------------------------------------------------------------------------
# Test 5 — ground_truth.status="expert_verified" → no ground_truth_not_verified
# ---------------------------------------------------------------------------


def test_ground_truth_expert_verified_no_blocker(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["ground_truth"] = {"status": "expert_verified", "value": "C2"}
    result = _critique(task, challenger)
    assert "ground_truth_not_verified" not in _issue_codes(result)


# ---------------------------------------------------------------------------
# Test 6 — terminal_answer.kind in NOT_ALLOWED_AS_ANSWER, v_class != V1/V5
# ---------------------------------------------------------------------------


def test_answer_is_process_only_kind_v2(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["terminal_answer"] = {"kind": "camera_pose_only", "fields": ["azimuth"]}
    result = _critique(task, challenger)
    codes = _issue_codes(result)
    assert "answer_is_process_only" in codes
    blocker = next(i for i in result["issues"] if i["code"] == "answer_is_process_only")
    assert blocker["severity"] == "critical"


# ---------------------------------------------------------------------------
# Test 7 — camera_pose_only with v_class=V1 → no answer_is_process_only
# ---------------------------------------------------------------------------


def test_answer_is_process_only_kind_v1_allowed(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["v_class"] = "V1"
    task["terminal_answer"] = {"kind": "camera_pose_only", "fields": ["azimuth"]}
    result = _critique(task, challenger)
    assert "answer_is_process_only" not in _issue_codes(result)


# ---------------------------------------------------------------------------
# Test 8 — all fields are pose/trace fields → answer_is_process_only
# ---------------------------------------------------------------------------


def test_answer_is_process_only_pure_pose_fields(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["terminal_answer"] = {
        "kind": "value",
        "fields": ["azimuth", "elevation", "roll", "zoom"],
    }
    result = _critique(task, challenger)
    codes = _issue_codes(result)
    assert "answer_is_process_only" in codes


# ---------------------------------------------------------------------------
# Test 9 — mixed fields (pose + scientific) → no answer_is_process_only
# ---------------------------------------------------------------------------


def test_answer_is_process_only_mixed_fields_allowed(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["terminal_answer"] = {
        "kind": "value",
        "fields": ["azimuth", "target_visibility_score"],
    }
    result = _critique(task, challenger)
    assert "answer_is_process_only" not in _issue_codes(result)


# ---------------------------------------------------------------------------
# Test 10 — trace_only kind with v_class=V5 → no answer_is_process_only (V5 exemption)
# ---------------------------------------------------------------------------


def test_answer_is_process_only_kind_v5_allowed(challenger: ChallengerStage) -> None:
    task = _complete_task()
    task["v_class"] = "V5"
    task["answer_schema"] = {"kind": "tool_trace"}
    task["trace_requirements"] = {"max_steps": 10}
    task["terminal_answer"] = {"kind": "trace_only", "fields": ["trace"]}
    result = _critique(task, challenger)
    assert "answer_is_process_only" not in _issue_codes(result)
