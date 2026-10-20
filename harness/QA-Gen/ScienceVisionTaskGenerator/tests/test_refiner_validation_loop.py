"""Tests for the iterative validation loop in RefinerStage (Task 5).

Covers:
  1. One-shot pass (clean task, expert_verified) → accept, status=="pass", release_gate=="pass"
  2. Seed accepted but not release-ready → accept, status=="needs_revision", release_gate=="blocked"
  3. Repair-then-pass (V3 missing annotation_iou) → repair applied, iterations>=2, status=="pass"
  4. Hard reject (asset_not_on_disk + missing terminal_answer) → reject, NOT in final_tasks
  5. Max-iterations guard (fake challenger always returns a repairable issue) → stops at max_iterations
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from sciencevision_taskgen.pipeline.challenger_stage import ChallengerStage
from sciencevision_taskgen.pipeline.refiner_stage import RefinerStage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_candidate() -> dict[str, Any]:
    """Return a fully-valid candidate that satisfies ALL ChallengerStage checks."""
    return {
        "task_id": "test-loop-001",
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
        "terminal_answer": {
            "kind": "value",
            "fields": ["symmetry_axis"],
        },
        "active_vision_evidence": {"screenshot": True},
        "validation_record": {
            "auto_checks": {},
            "expert_checks": {},
            "loop": {"iteration": 0, "max_iterations": 4, "status": "pending"},
            "release_gate": "pending",
        },
        "asset_grounding": {},
        "expert_review_requirements": {},
    }


def _candidate(**overrides: Any) -> dict[str, Any]:
    """Return a base candidate with the given key overrides applied (shallow merge)."""
    base = _base_candidate()
    base.update(overrides)
    return base


class _AlwaysRepairableChallenger:
    """Fake challenger that always returns a repairable issue and removes the repair
    from the task before each critique so that repairs apply every round.

    This simulates a task whose repaired state is always rolled back, testing that the
    loop terminates at max_iterations rather than running indefinitely.
    """

    def critique_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for task in tasks:
            # Remove any previously applied annotation_iou so the repair re-applies
            checks: list[str] = task.get("scorer", {}).get("checks", [])
            if "annotation_iou" in checks:
                checks.remove("annotation_iou")
            results.append({
                "task_id": task.get("task_id"),
                "critique_type": "fake",
                "issues": [
                    {
                        "code": "v3_missing_iou_check",
                        "severity": "high",
                        "detail": "V3 scorer should include annotation IoU or distance.",
                    }
                ],
                "severity": "high",
                "recommendation": "revise",
                "rationale": "Fake always-repairable challenger.",
            })
        return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def refiner() -> RefinerStage:
    return RefinerStage()


@pytest.fixture()
def strict_refiner() -> RefinerStage:
    """Refiner in strict mode: off-disk assets are hard-rejected."""
    return RefinerStage({"validation": {"reject_off_disk_assets": True}})


@pytest.fixture()
def challenger() -> ChallengerStage:
    return ChallengerStage()


# ---------------------------------------------------------------------------
# Test 1: One-shot pass
# ---------------------------------------------------------------------------

def test_oneshot_pass_expert_verified(refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """Clean task with expert_verified ground_truth → accept, pass, release_gate pass."""
    candidate = _candidate(ground_truth={"status": "expert_verified", "value": "C2"})
    final_tasks, decisions, final_critiques = refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert len(final_tasks) == 1, "Task should be accepted"
    task = final_tasks[0]
    decision = decisions[0]

    # Decision shape
    assert decision["decision"] == "accept"
    assert decision["validation_status"] == "pass"
    assert decision["release_ready"] is True
    assert decision["iterations"] == 1

    # validation_record
    vr = task["validation_record"]
    loop = vr["loop"]
    assert loop["status"] == "pass"
    assert loop["iteration"] == 1
    assert loop["max_iterations"] == 4
    assert isinstance(loop["history"], list)

    # auto_checks
    auto = vr["auto_checks"]
    assert auto["no_blocking_failures"] is True
    assert auto["terminal_answer_present"] is True
    assert auto["ground_truth_verified"] is True

    # release_gate
    assert vr["release_gate"] == "pass"

    # final_critiques aligned
    assert len(final_critiques) == 1
    assert final_critiques[0]["task_id"] == candidate["task_id"]

    # Input was not mutated
    assert "acceptance_info" not in candidate


# ---------------------------------------------------------------------------
# Test 2: Seed accepted but not release-ready
# ---------------------------------------------------------------------------

def test_seed_accepted_not_release_ready(refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """Clean task but needs_expert_annotation → accept, needs_revision, release_gate blocked."""
    candidate = _candidate(ground_truth={"status": "needs_expert_annotation"})
    final_tasks, decisions, final_critiques = refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert len(final_tasks) == 1, "Seed tasks should be accepted (not rejected)"
    task = final_tasks[0]
    decision = decisions[0]

    assert decision["decision"] == "accept"
    assert decision["validation_status"] == "needs_revision"
    assert decision["release_ready"] is False

    vr = task["validation_record"]
    assert vr["loop"]["status"] == "needs_revision"
    assert vr["release_gate"] == "blocked"
    assert vr["auto_checks"]["ground_truth_verified"] is False


# ---------------------------------------------------------------------------
# Test 3: Repair then pass
# ---------------------------------------------------------------------------

def test_repair_then_pass_v3_iou(refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """V3 task missing annotation_iou triggers repair; after repair loop confirms pass."""
    candidate = _candidate(
        task_id="test-v3-repair",
        v_class="V3",
        required_evidence=["screenshot", "annotation"],
        scorer={"primary": "iou_match", "checks": ["exact_match_check"]},  # missing annotation_iou
        ground_truth={"status": "expert_verified", "value": "region_A"},
    )
    final_tasks, decisions, final_critiques = refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert len(final_tasks) == 1, "Task should be accepted after repair"
    task = final_tasks[0]
    decision = decisions[0]

    # annotation_iou was applied
    assert "annotation_iou" in task["scorer"]["checks"]

    # Multiple iterations: at least one repair round + one confirm round
    assert decision["iterations"] >= 2

    # History shows at least one repair
    history = task["validation_record"]["loop"]["history"]
    repaired_rounds = [h for h in history if h.get("repairs")]
    assert len(repaired_rounds) >= 1, "At least one history entry should record a repair"

    # Final status
    assert decision["validation_status"] == "pass"
    assert decision["decision"] == "accept"

    # Input not mutated
    assert "annotation_iou" not in candidate["scorer"]["checks"]


# ---------------------------------------------------------------------------
# Test 4: Hard reject
# ---------------------------------------------------------------------------

def test_hard_reject_asset_not_on_disk(strict_refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """In strict mode, a task with asset not on disk → rejected, not in final_tasks."""
    candidate = _candidate(
        task_id="test-reject-001",
        asset={
            "path": "datasets/biology/MISSING.pdb",
            "viewer": "molstar",
            "format": "pdb",
            "exists_on_disk": False,
        },
    )
    final_tasks, decisions, final_critiques = strict_refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert len(final_tasks) == 0, "Rejected task must not be in final_tasks"
    decision = decisions[0]

    assert decision["decision"] == "reject"
    assert decision["release_ready"] is False
    assert len(decision["reject_reasons"]) > 0

    # final_critiques still aligned (one entry per candidate)
    assert len(final_critiques) == 1


def test_off_disk_accepted_but_blocked_by_default(
    refiner: RefinerStage, challenger: ChallengerStage
) -> None:
    """Default (mirror) mode: off-disk asset is accepted as a seed spec but
    release_gate stays blocked — it is not dropped from final_tasks."""
    candidate = _candidate(
        task_id="test-offdisk-default-001",
        ground_truth={"status": "expert_verified", "value": "C2"},
        asset={
            "path": "datasets/biology/MISSING.pdb",
            "viewer": "molstar",
            "format": "pdb",
            "exists_on_disk": False,
        },
    )
    final_tasks, decisions, _ = refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert len(final_tasks) == 1, "Off-disk task must be kept in default mode"
    decision = decisions[0]
    assert decision["decision"] == "accept"
    # Not release-ready: off-disk blocks the release gate even with verified GT.
    assert decision["release_ready"] is False
    assert final_tasks[0]["validation_record"]["release_gate"] == "blocked"


def test_hard_reject_critical_issue_missing_terminal_answer(
    refiner: RefinerStage, challenger: ChallengerStage
) -> None:
    """Task missing terminal_answer → critical blocker → reject."""
    candidate = _candidate(task_id="test-reject-002")
    del candidate["terminal_answer"]

    final_tasks, decisions, final_critiques = refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert len(final_tasks) == 0
    assert decisions[0]["decision"] == "reject"
    assert decisions[0]["release_ready"] is False


# ---------------------------------------------------------------------------
# Test 5: Max-iterations guard
# ---------------------------------------------------------------------------

def test_max_iterations_guard() -> None:
    """Fake challenger always returns a repairable issue; loop must stop at max_iterations."""
    refiner = RefinerStage()
    fake_challenger = _AlwaysRepairableChallenger()
    max_iter = 3

    candidate = _base_candidate()
    candidate["task_id"] = "test-max-iter"
    # Give it a V3 setup so the repair (annotation_iou) is applicable
    candidate["v_class"] = "V3"
    candidate["required_evidence"] = ["screenshot", "annotation"]
    candidate["scorer"] = {"primary": "iou_match", "checks": []}

    final_tasks, decisions, final_critiques = refiner.refine_tasks(
        [candidate], fake_challenger, max_iterations=max_iter
    )

    decision = decisions[0]

    # Must have stopped — status is max_iterations_exceeded, treated as reject
    assert decision["validation_status"] == "max_iterations_exceeded"
    assert decision["decision"] == "reject"
    assert len(final_tasks) == 0, "max_iterations_exceeded tasks must not be in final_tasks"

    # History length must not exceed max_iterations
    assert decision["iterations"] <= max_iter

    # Critiques still aligned
    assert len(final_critiques) == 1


# ---------------------------------------------------------------------------
# Test 6: Return signature — three-tuple
# ---------------------------------------------------------------------------

def test_refine_tasks_returns_three_tuple(refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """refine_tasks must return (final_tasks, decisions, final_critiques)."""
    result = refiner.refine_tasks([_base_candidate()], challenger, max_iterations=4)
    assert isinstance(result, tuple)
    assert len(result) == 3
    final_tasks, decisions, final_critiques = result
    assert isinstance(final_tasks, list)
    assert isinstance(decisions, list)
    assert isinstance(final_critiques, list)
    assert len(final_critiques) == 1  # aligned to input candidates


# ---------------------------------------------------------------------------
# Test 7: Multiple candidates — each processed independently
# ---------------------------------------------------------------------------

def test_multiple_candidates(strict_refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """Mix of pass and reject candidates in one call (strict mode)."""
    good = _candidate(task_id="good-001", ground_truth={"status": "expert_verified", "value": "C2"})
    bad = _candidate(
        task_id="bad-001",
        asset={
            "path": "datasets/biology/MISSING.pdb",
            "viewer": "molstar",
            "format": "pdb",
            "exists_on_disk": False,
        },
    )

    final_tasks, decisions, final_critiques = strict_refiner.refine_tasks(
        [good, bad], challenger, max_iterations=4
    )

    assert len(final_tasks) == 1
    assert final_tasks[0]["task_id"] == "good-001"
    assert len(decisions) == 2
    assert len(final_critiques) == 2

    good_dec = next(d for d in decisions if d["task_id"] == "good-001")
    bad_dec = next(d for d in decisions if d["task_id"] == "bad-001")
    assert good_dec["decision"] == "accept"
    assert bad_dec["decision"] == "reject"


# ---------------------------------------------------------------------------
# Test 8: Immutability — input candidates are never mutated
# ---------------------------------------------------------------------------

def test_input_candidates_not_mutated(refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """refine_tasks must never mutate the input candidate dicts."""
    candidate = _candidate(
        v_class="V3",
        required_evidence=["screenshot", "annotation"],
        scorer={"primary": "iou_match", "checks": []},
        ground_truth={"status": "expert_verified", "value": "region_A"},
    )
    original_checks = list(candidate["scorer"]["checks"])
    original_vr = deepcopy(candidate["validation_record"])

    refiner.refine_tasks([candidate], challenger, max_iterations=4)

    assert candidate["scorer"]["checks"] == original_checks, "scorer.checks was mutated"
    assert candidate["validation_record"] == original_vr, "validation_record was mutated"
    assert "acceptance_info" not in candidate, "acceptance_info was added to original"


# ---------------------------------------------------------------------------
# Test 9: max_iterations < 1 raises ValueError (I3)
# ---------------------------------------------------------------------------

def test_max_iterations_less_than_one_raises(refiner: RefinerStage, challenger: ChallengerStage) -> None:
    """refine_tasks must raise ValueError when max_iterations < 1."""
    with pytest.raises(ValueError, match="max_iterations must be >= 1"):
        refiner.refine_tasks([_base_candidate()], challenger, max_iterations=0)
