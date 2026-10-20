"""Refiner stage for accepting, revising, or rejecting generated tasks.

This module implements an iterative validation loop that:
  1. Critiques each candidate via the ChallengerStage.
  2. Applies deterministic repairs for fixable issues.
  3. Re-critiques after repairs to confirm they cleared the issue.
  4. Stops when stable, hard-rejected, or max_iterations reached.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Protocol

from sciencevision_taskgen.pipeline.challenger_stage import (
    V3_MISSING_IOU_CHECK,
    V6_MISSING_TOOL_AUDIT,
)

# Issue codes that can be repaired deterministically.
# Maps code -> repair string (what gets added).
# Keys reference imported constants so a rename can't silently break the map.
_REPAIRABLE: dict[str, str] = {
    V3_MISSING_IOU_CHECK: "Added annotation_iou scorer check.",
    V6_MISSING_TOOL_AUDIT: "Added forbidden_tool_audit scorer check.",
}

# ground_truth.status values that count as "verified for release"
_VERIFIED_GT_STATUSES: frozenset[str] = frozenset({"expert_verified", "released"})


class _Critiquer(Protocol):
    """Minimal interface required of the challenger argument."""

    def critique_tasks(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


def _apply_repair(task: dict[str, Any], code: str) -> None:
    """Apply the deterministic repair for *code* in-place on *task* (a deepcopy)."""
    if code == V3_MISSING_IOU_CHECK:
        checks: list[str] = task.setdefault("scorer", {}).setdefault("checks", [])
        if "annotation_iou" not in checks:
            checks.append("annotation_iou")
    elif code == V6_MISSING_TOOL_AUDIT:
        checks = task.setdefault("scorer", {}).setdefault("checks", [])
        if "forbidden_tool_audit" not in checks:
            checks.append("forbidden_tool_audit")


def _is_already_repaired(task: dict[str, Any], code: str) -> bool:
    """Return True if the repair for *code* has already been applied to *task*."""
    if code == V3_MISSING_IOU_CHECK:
        return "annotation_iou" in task.get("scorer", {}).get("checks", [])
    if code == V6_MISSING_TOOL_AUDIT:
        return "forbidden_tool_audit" in task.get("scorer", {}).get("checks", [])
    return False


def _classify_issue(
    issue: dict[str, str],
    task: dict[str, Any],
    reject_off_disk: bool = True,
) -> Literal["hard_reject", "repairable", "needs_revision"]:
    """Classify a single issue as 'hard_reject', 'repairable', or 'needs_revision'.

    Returns one of: 'hard_reject', 'repairable', 'needs_revision'.

    ``reject_off_disk`` controls how ``asset_not_on_disk`` is treated.  In the
    mirror/clone distribution model, raw assets are pulled separately, so an
    asset that is whitelisted but not yet on local disk should not fail
    generation — it yields an accepted seed spec with ``release_gate=blocked``
    until the asset is fetched.  Set ``reject_off_disk=True`` (strict mode) to
    restore the previous behaviour of rejecting such tasks outright.
    """
    code = issue.get("code", "")
    severity = issue.get("severity", "low")

    # asset_not_on_disk: hard reject only in strict mode; otherwise it blocks
    # release (needs_revision) but does not drop the task.
    if code == "asset_not_on_disk":
        return "hard_reject" if reject_off_disk else "needs_revision"

    # Repairable codes: not yet applied → repairable; already applied → needs_revision
    # (if the challenger still reports it after repair, treat as non-blocking)
    if code in _REPAIRABLE:
        if not _is_already_repaired(task, code):
            return "repairable"
        return "needs_revision"

    # Critical issues that are not repairable → hard reject
    if severity == "critical":
        return "hard_reject"

    # Non-critical, non-repairable → needs_revision (blocks release but not accept)
    return "needs_revision"


class RefinerStage:
    """Applies deterministic repairs for fixable issues and decides final status.

    The iterative loop runs up to *max_iterations* times per candidate:
      - Repairs are applied when a repairable issue is detected.
      - The loop re-critiques after a repair round to confirm the fix cleared it.
      - Hard-reject issues terminate immediately with decision='reject'.
      - 'needs_revision' issues (e.g. ground_truth_not_verified) do not reject but
        mark the task as not release-ready.
      - Reaching max_iterations while still applying repairs → 'max_iterations_exceeded'
        (treated as reject).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        # Default: do NOT reject whitelisted-but-not-yet-on-disk assets (mirror
        # distribution model).  Set validation.reject_off_disk_assets=true for
        # strict local-only runs.
        self._reject_off_disk = bool(
            cfg.get("validation", {}).get("reject_off_disk_assets", False)
        )

    def refine_tasks(
        self,
        candidates: list[dict[str, Any]],
        challenger: _Critiquer,
        max_iterations: int = 4,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Refine *candidates* using an iterative critique-repair loop.

        Args:
            candidates: List of candidate task dicts (never mutated).
            challenger: Object with a ``critique_tasks(list) -> list`` method.
            max_iterations: Maximum critique-repair iterations per candidate.

        Raises:
            ValueError: If *max_iterations* is less than 1.

        Returns:
            (final_tasks, decisions, final_critiques) — all lists aligned to
            *candidates* (final_tasks may be shorter due to rejects; decisions
            and final_critiques always have one entry per candidate).
        """
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")

        final_tasks: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        final_critiques: list[dict[str, Any]] = []

        for candidate in candidates:
            task, decision, last_critique = self._process_candidate(
                candidate, challenger, max_iterations
            )
            decisions.append(decision)
            final_critiques.append(last_critique)
            if decision["decision"] == "accept":
                final_tasks.append(task)

        return final_tasks, decisions, final_critiques

    def _run_validation_loop(
        self,
        revised: dict[str, Any],
        challenger: _Critiquer,
        max_iterations: int,
    ) -> tuple[str, int, list[dict[str, Any]], list[str], dict[str, Any]]:
        """Run the critique-repair iteration loop for a single (deepcopy) task.

        Returns:
            (validation_status, iterations_run, history, all_repairs, last_critique)
        """
        history: list[dict[str, Any]] = []
        all_repairs: list[str] = []
        last_critique: dict[str, Any] = {}
        validation_status = "pending"
        iterations_run = 0

        for i in range(1, max_iterations + 1):
            iterations_run = i
            critique = challenger.critique_tasks([revised])[0]
            last_critique = critique

            issues = critique.get("issues", [])
            iteration_repairs: list[str] = []
            iteration_failures: list[str] = []
            hard_reject = False

            for issue in issues:
                classification = _classify_issue(issue, revised, self._reject_off_disk)
                code = issue.get("code", "")

                if classification == "hard_reject":
                    hard_reject = True
                    iteration_failures.append(code)
                elif classification == "repairable":
                    _apply_repair(revised, code)
                    repair_msg = _REPAIRABLE[code]
                    iteration_repairs.append(repair_msg)
                    all_repairs.append(repair_msg)
                    iteration_failures.append(code)  # record as a failure this round
                else:
                    # needs_revision
                    iteration_failures.append(code)

            history.append({
                "iteration": i,
                "failures": iteration_failures,
                "repairs": iteration_repairs,
            })

            if hard_reject:
                validation_status = "rejected"
                break

            if not iteration_repairs:
                # Stable: no new repairs this round
                still_needs_revision = any(
                    _classify_issue(issue, revised, self._reject_off_disk) == "needs_revision"
                    for issue in issues
                )
                validation_status = "needs_revision" if still_needs_revision else "pass"
                break

            # Repairs were applied this round; continue to next iteration to confirm
            if i == max_iterations:
                validation_status = "max_iterations_exceeded"

        return validation_status, iterations_run, history, all_repairs, last_critique

    def _compute_release_flags(
        self,
        revised: dict[str, Any],
        validation_status: str,
    ) -> tuple[bool, bool, bool]:
        """Compute derived release readiness flags from the refined task.

        Returns:
            (ground_truth_verified, terminal_answer_present, release_ready)
        """
        gt_status = revised.get("ground_truth", {}).get("status")
        ground_truth_verified = gt_status in _VERIFIED_GT_STATUSES
        terminal_answer_present = bool(
            isinstance(revised.get("terminal_answer"), dict)
            and revised.get("terminal_answer", {}).get("fields")
        )
        release_ready = (validation_status == "pass") and ground_truth_verified
        return ground_truth_verified, terminal_answer_present, release_ready

    def _build_reject_decision(
        self,
        candidate: dict[str, Any],
        revised: dict[str, Any],
        last_critique: dict[str, Any],
        validation_status: str,
        iterations_run: int,
        all_repairs: list[str],
    ) -> dict[str, Any]:
        """Assemble the decision dict for a rejected candidate."""
        reject_reasons = [
            issue.get("detail", issue.get("code", "unknown"))
            for issue in last_critique.get("issues", [])
            if _classify_issue(issue, revised, self._reject_off_disk) == "hard_reject"
        ]
        if not reject_reasons:
            reject_reasons = [
                f"Validation loop did not converge after {iterations_run} iterations."
            ]
        return {
            "task_id": candidate.get("task_id"),
            "decision": "reject",
            "reasoning": (
                "Rejected due to blocking contract issues."
                if validation_status == "rejected"
                else f"Validation loop did not converge after {iterations_run} iterations."
            ),
            "repairs": all_repairs,
            "reject_reasons": reject_reasons,
            "validation_status": validation_status,
            "iterations": iterations_run,
            "release_ready": False,
        }

    def _build_accept_decision(
        self,
        candidate: dict[str, Any],
        validation_status: str,
        iterations_run: int,
        all_repairs: list[str],
        release_ready: bool,
    ) -> dict[str, Any]:
        """Assemble the decision dict for an accepted candidate."""
        return {
            "task_id": candidate.get("task_id"),
            "decision": "accept",
            "reasoning": "Accepted after iterative contract review.",
            "repairs": all_repairs,
            "validation_status": validation_status,
            "iterations": iterations_run,
            "release_ready": release_ready,
        }

    def _process_candidate(
        self,
        candidate: dict[str, Any],
        challenger: _Critiquer,
        max_iterations: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Run the iterative loop for a single candidate.

        Returns (deepcopy_of_candidate_with_updates, decision, last_critique).
        The returned task is only meaningful when decision['decision'] == 'accept'.
        """
        revised = deepcopy(candidate)

        validation_status, iterations_run, history, all_repairs, last_critique = (
            self._run_validation_loop(revised, challenger, max_iterations)
        )

        if validation_status in {"rejected", "max_iterations_exceeded"}:
            decision = self._build_reject_decision(
                candidate, revised, last_critique,
                validation_status, iterations_run, all_repairs,
            )
            return revised, decision, last_critique

        # Accepted (pass or needs_revision)
        ground_truth_verified, terminal_answer_present, release_ready = (
            self._compute_release_flags(revised, validation_status)
        )

        revised["validation_record"] = {
            "auto_checks": {
                "no_blocking_failures": True,
                "terminal_answer_present": terminal_answer_present,
                "ground_truth_verified": ground_truth_verified,
            },
            "expert_checks": candidate.get("validation_record", {}).get("expert_checks", {}),
            "loop": {
                "iteration": iterations_run,
                "max_iterations": max_iterations,
                "status": validation_status,
                "history": history,
            },
            "release_gate": "pass" if release_ready else "blocked",
        }

        last_critique_severity = last_critique.get("severity", "low")
        revised["acceptance_info"] = {
            "decision": "accept",
            "critique_severity": last_critique_severity,
            "repairs": all_repairs,
        }

        decision = self._build_accept_decision(
            candidate, validation_status, iterations_run, all_repairs, release_ready
        )
        return revised, decision, last_critique
