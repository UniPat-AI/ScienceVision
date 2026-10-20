import json
import pytest
from pathlib import Path
from sci3d_agent.trajectory import (
    TrajectoryRecorder, TrajectoryRecord, StepRecord, ScoreBundle, MetaBundle
)

@pytest.mark.unit
def test_recorder_starts_empty():
    rec = TrajectoryRecorder(task_id="t1", mode="mode1_no_vision", model="claude-opus-4-7", seed=42)
    traj = rec.build()
    assert traj.schema_version == "1.1"
    assert traj.task_id == "t1"
    assert traj.steps == []
    assert traj.timeout is False
    assert traj.violations == []

@pytest.mark.unit
def test_recorder_records_step():
    rec = TrajectoryRecorder(task_id="t1", mode="mode1_no_vision", model="m", seed=0)
    rec.record_step(iter_=0, tool="execute_code", args={"code": "1+1"}, result={"stdout": "2"}, latency_ms=12)
    traj = rec.build()
    assert len(traj.steps) == 1
    assert traj.steps[0].tool == "execute_code"
    assert traj.steps[0].latency_ms == 12

@pytest.mark.unit
def test_recorder_records_violation():
    rec = TrajectoryRecorder(task_id="t1", mode="mode1_no_vision", model="m", seed=0)
    rec.record_violation("mode1_render_attempt:plt.savefig")
    traj = rec.build()
    assert "mode1_render_attempt:plt.savefig" in traj.violations

@pytest.mark.unit
def test_recorder_serializes_to_json(tmp_path):
    rec = TrajectoryRecorder(task_id="t1", mode="mode3_free_visual", model="m", seed=7)
    rec.record_step(iter_=0, tool="finish", args={"answer": "ok"}, result={}, latency_ms=1)
    rec.record_final({"answer": "ok"})
    traj = rec.build()
    out = tmp_path / "t.json"
    traj.dump_json(out)
    data = json.loads(out.read_text())
    assert data["schema_version"] == "1.1"
    assert data["final_answer"] == {"answer": "ok"}
    assert data["meta"]["seed"] == 7
