"""Tests for GroundTruthStage."""
from __future__ import annotations

import copy
from pathlib import Path

from sciencevision_taskgen.groundtruth import FILLERS, register
from sciencevision_taskgen.groundtruth.base import FillResult
from sciencevision_taskgen.pipeline.groundtruth_stage import (
    GroundTruthStage,
    StageReport,
)


def _make_task(task_id: str, gt_type: str) -> dict:
    return {
        "task_id": task_id,
        "ground_truth": {
            "type": gt_type,
            "value": None,
            "status": "needs_expert_annotation",
            "notes": "original",
        },
    }


def test_stage_fills_known_type_and_keeps_others(monkeypatch):
    @register("fake_type")
    class _F:
        def fill(self, task):
            return FillResult(
                value={"answer": 42},
                status="auto_seed",
                notes="filled by fake",
                evidence_paths=("a.webp",),
                model="claude-sonnet-4-6",
            )

    tasks_in = [
        _make_task("t1", "fake_type"),
        _make_task("t2", "expert_camera_pose"),
    ]
    stage = GroundTruthStage()
    tasks_out, report = stage.fill_tasks(tasks_in)

    assert tasks_in[0]["ground_truth"]["value"] is None
    assert tasks_in[0]["ground_truth"]["status"] == "needs_expert_annotation"

    assert tasks_out[0]["ground_truth"]["value"] == {"answer": 42}
    assert tasks_out[0]["ground_truth"]["status"] == "auto_seed"
    assert tasks_out[0]["ground_truth"]["notes"] == "filled by fake"
    assert tasks_out[0]["ground_truth"]["evidence_paths"] == ["a.webp"]
    assert tasks_out[0]["ground_truth"]["model"] == "claude-sonnet-4-6"

    assert tasks_out[1]["ground_truth"]["value"] is None
    assert tasks_out[1]["ground_truth"]["status"] == "awaiting_expert"
    assert "not yet implemented" in tasks_out[1]["ground_truth"]["notes"].lower()

    assert isinstance(report, StageReport)
    assert report.total == 2
    assert report.auto_seed == 1
    assert report.awaiting_expert == 1

    del FILLERS["fake_type"]


def test_stage_returns_empty_report_for_empty_input():
    tasks_out, report = GroundTruthStage().fill_tasks([])
    assert tasks_out == []
    assert report.total == 0
    assert report.auto_seed == 0
    assert report.awaiting_expert == 0


def test_stage_clears_validation_release_gate_when_auto_seed(monkeypatch):
    @register("fake_type_2")
    class _F:
        def fill(self, task):
            return FillResult(value={"x": 1}, status="auto_seed")

    task = _make_task("t1", "fake_type_2")
    task["validation_record"] = {
        "auto_checks": {"ground_truth_verified": False},
        "release_gate": "blocked",
        "loop": {"history": [], "iteration": 1, "status": "needs_revision"},
    }
    tasks_out, _ = GroundTruthStage().fill_tasks([task])
    assert tasks_out[0]["validation_record"]["auto_checks"]["ground_truth_verified"] is True

    del FILLERS["fake_type_2"]


def test_stage_normalizes_absolute_evidence_paths_under_repo_root(tmp_path: Path):
    @register("fake_evidence_type")
    class _F:
        def fill(self, task: dict) -> FillResult:
            # Absolute paths inside and outside repo_root
            return FillResult(
                value={"ok": True},
                status="auto_seed",
                evidence_paths=(
                    str(tmp_path / "viewer" / "thumbs" / "a.webp"),
                    str(tmp_path / "viewer" / "thumbs" / "sub" / "b.webp"),
                    "/etc/passwd",  # outside repo_root -> preserved
                    "viewer/thumbs/already_relative.webp",  # relative -> preserved
                ),
            )

    task = {
        "task_id": "t1",
        "ground_truth": {"type": "fake_evidence_type"},
    }
    tasks_out, _ = GroundTruthStage(repo_root=tmp_path).fill_tasks([task])
    ev = tasks_out[0]["ground_truth"]["evidence_paths"]
    assert ev[0] == "viewer/thumbs/a.webp"
    assert ev[1] == "viewer/thumbs/sub/b.webp"
    assert ev[2] == "/etc/passwd"
    assert ev[3] == "viewer/thumbs/already_relative.webp"

    del FILLERS["fake_evidence_type"]


def test_stage_without_repo_root_leaves_evidence_unchanged(tmp_path: Path):
    @register("fake_evidence_type_2")
    class _F:
        def fill(self, task: dict) -> FillResult:
            return FillResult(
                value={"ok": True},
                status="auto_seed",
                evidence_paths=(str(tmp_path / "viewer" / "thumbs" / "a.webp"),),
            )

    task = {"task_id": "t1", "ground_truth": {"type": "fake_evidence_type_2"}}
    tasks_out, _ = GroundTruthStage().fill_tasks([task])  # no repo_root
    assert tasks_out[0]["ground_truth"]["evidence_paths"] == [
        str(tmp_path / "viewer" / "thumbs" / "a.webp"),
    ]
    del FILLERS["fake_evidence_type_2"]
