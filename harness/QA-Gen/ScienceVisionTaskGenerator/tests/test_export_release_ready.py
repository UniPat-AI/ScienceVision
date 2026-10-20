"""Tests for ExportStage seed vs release-ready split (Task 6).

Covers:
  1. is_release_ready unit logic for all three task variants.
  2. export() writes _seed_tasks.jsonl (all accepted) and _release_ready_tasks.jsonl
     (only release-ready tasks).
  3. REPORT.md contains the updated summary lines.
  4. Edge case: all tasks non-release-ready → empty release_ready file + Release Gate note.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sciencevision_taskgen.pipeline.export_stage import ExportStage, is_release_ready


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str, gt_status: str, release_gate: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "domain": "biology",
        "v_class": "V2",
        "ground_truth": {"status": gt_status, "value": "C2"},
        "validation_record": {"release_gate": release_gate},
    }


def _minimal_catalog() -> dict[str, Any]:
    return {"summary": {"asset_count": 0, "matched_qa_total": 0}}


def _run_export(
    tmp_path: Path,
    final_tasks: list[dict[str, Any]],
) -> tuple[dict[str, str], Path]:
    """Create a run dir under tmp_path, call export, return (outputs, run_dir)."""
    run_dir = tmp_path / "run_20260528_120000"
    run_dir.mkdir()
    stage = ExportStage()
    outputs = stage.export(
        run_dir=run_dir,
        catalog=_minimal_catalog(),
        plan=[],
        candidates=[],
        critiques=[],
        decisions=[],
        final_tasks=final_tasks,
    )
    return outputs, run_dir


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TASK_A = _make_task("task-A", gt_status="expert_verified", release_gate="pass")
TASK_B = _make_task("task-B", gt_status="needs_expert_annotation", release_gate="blocked")
TASK_C = _make_task("task-C", gt_status="expert_verified", release_gate="blocked")

THREE_TASKS = [TASK_A, TASK_B, TASK_C]


# ---------------------------------------------------------------------------
# Unit tests for is_release_ready
# ---------------------------------------------------------------------------

def test_is_release_ready_returns_true_for_verified_pass() -> None:
    assert is_release_ready(TASK_A) is True


def test_is_release_ready_returns_false_for_unverified_blocked() -> None:
    assert is_release_ready(TASK_B) is False


def test_is_release_ready_returns_false_for_verified_but_gate_blocked() -> None:
    """gt=expert_verified but gate=blocked → NOT release-ready."""
    assert is_release_ready(TASK_C) is False


def test_is_release_ready_with_released_status() -> None:
    """gt.status='released' + gate='pass' is also release-ready."""
    task = _make_task("task-R", gt_status="released", release_gate="pass")
    assert is_release_ready(task) is True


def test_is_release_ready_missing_fields_returns_false() -> None:
    assert is_release_ready({}) is False


def test_is_release_ready_returns_false_for_released_status_but_gate_blocked() -> None:
    """gt.status='released' but gate='blocked' → NOT release-ready."""
    task = _make_task("task-blocked", gt_status="released", release_gate="blocked")
    assert is_release_ready(task) is False


# ---------------------------------------------------------------------------
# Integration: export with 3 tasks (1 release-ready)
# ---------------------------------------------------------------------------

def test_seed_jsonl_has_all_tasks(tmp_path: Path) -> None:
    outputs, _ = _run_export(tmp_path, THREE_TASKS)
    seed_path = Path(outputs["seed_jsonl"])
    assert seed_path.exists()
    rows = _read_jsonl(seed_path)
    assert len(rows) == 3


def test_release_ready_jsonl_has_one_task(tmp_path: Path) -> None:
    outputs, _ = _run_export(tmp_path, THREE_TASKS)
    rr_path = Path(outputs["release_ready_jsonl"])
    assert rr_path.exists()
    rows = _read_jsonl(rr_path)
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task-A"


def test_seed_jsonl_filename_uses_run_dir_name(tmp_path: Path) -> None:
    outputs, run_dir = _run_export(tmp_path, THREE_TASKS)
    seed_path = Path(outputs["seed_jsonl"])
    assert seed_path.name == f"{run_dir.name}_seed_tasks.jsonl"


def test_release_ready_jsonl_filename_uses_run_dir_name(tmp_path: Path) -> None:
    outputs, run_dir = _run_export(tmp_path, THREE_TASKS)
    rr_path = Path(outputs["release_ready_jsonl"])
    assert rr_path.name == f"{run_dir.name}_release_ready_tasks.jsonl"


def test_report_contains_seed_count(tmp_path: Path) -> None:
    outputs, run_dir = _run_export(tmp_path, THREE_TASKS)
    report = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Accepted seed tasks: 3" in report


def test_report_contains_release_ready_count(tmp_path: Path) -> None:
    outputs, run_dir = _run_export(tmp_path, THREE_TASKS)
    report = (run_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "Release-ready tasks: 1" in report


def test_outputs_dict_contains_new_keys(tmp_path: Path) -> None:
    outputs, _ = _run_export(tmp_path, THREE_TASKS)
    assert "seed_jsonl" in outputs
    assert "release_ready_jsonl" in outputs


# ---------------------------------------------------------------------------
# Edge case: all tasks non-release-ready
# ---------------------------------------------------------------------------

ALL_NON_READY = [TASK_B, TASK_C]


def test_release_ready_file_exists_when_empty(tmp_path: Path) -> None:
    outputs, _ = _run_export(tmp_path, ALL_NON_READY)
    rr_path = Path(outputs["release_ready_jsonl"])
    assert rr_path.exists(), "release_ready file must exist even when 0 release-ready tasks"
    rows = _read_jsonl(rr_path)
    assert len(rows) == 0


def test_report_shows_zero_release_ready(tmp_path: Path) -> None:
    outputs, _ = _run_export(tmp_path, ALL_NON_READY)
    report = (Path(outputs["report"])).read_text(encoding="utf-8")
    assert "Release-ready tasks: 0" in report


def test_report_contains_release_gate_note_when_zero(tmp_path: Path) -> None:
    outputs, _ = _run_export(tmp_path, ALL_NON_READY)
    report = (Path(outputs["report"])).read_text(encoding="utf-8")
    assert "## Release Gate" in report
    assert "0 release-ready" in report
