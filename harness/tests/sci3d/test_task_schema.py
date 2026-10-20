import json
import pytest
from pathlib import Path
from sci3d_bench.task_schema import TaskSpec, AssetRef, GroundTruth, AnswerSchema, ScorerSpec, Provenance, load_taskspec

VALID_TASK = {
    "schema_version": "1.0",
    "task_id": "seed-v6-cube-001",
    "v_class": "V6",
    "question": "How many vertices does this mesh have?",
    "asset": {"path": "tests/sci3d/fixtures/cube.obj", "format": "obj", "sha256": None},
    "prerendered_image": None,
    "answer_schema": {"kind": "structured", "fields": {"vertex_count": "int"}},
    "ground_truth": {"value": {"vertex_count": 8}, "notes": None},
    "scorer": {"primary": "llm_judge", "secondary": []},
    "provenance": {"source_dataset": "seed", "source_qid": None, "lifted_at": "2026-05-18", "lifter_version": "manual-v1"},
    "mode_overrides": None,
}

@pytest.mark.unit
def test_taskspec_loads_valid_json(tmp_path):
    p = tmp_path / "task.json"
    p.write_text(json.dumps(VALID_TASK))
    task = load_taskspec(p)
    assert task.task_id == "seed-v6-cube-001"
    assert task.v_class == "V6"
    assert task.ground_truth.value == {"vertex_count": 8}

@pytest.mark.unit
def test_taskspec_gt_absent_yields_none(tmp_path):
    data = dict(VALID_TASK)
    data["ground_truth"] = None
    p = tmp_path / "task.json"
    p.write_text(json.dumps(data))
    task = load_taskspec(p)
    assert task.ground_truth is None

@pytest.mark.unit
def test_taskspec_rejects_bad_v_class(tmp_path):
    data = dict(VALID_TASK)
    data["v_class"] = "V99"
    p = tmp_path / "task.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="v_class"):
        load_taskspec(p)

@pytest.mark.unit
def test_taskspec_is_frozen(tmp_path):
    p = tmp_path / "task.json"
    p.write_text(json.dumps(VALID_TASK))
    task = load_taskspec(p)
    with pytest.raises((AttributeError, TypeError)):
        task.task_id = "mutated"

@pytest.mark.unit
def test_taskspec_rejects_empty_task_id(tmp_path):
    data = dict(VALID_TASK)
    data["task_id"] = ""
    p = tmp_path / "task.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="task_id"):
        load_taskspec(p)

@pytest.mark.unit
def test_taskspec_rejects_empty_question(tmp_path):
    data = dict(VALID_TASK)
    data["question"] = ""
    p = tmp_path / "task.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="question"):
        load_taskspec(p)

@pytest.mark.unit
def test_taskspec_invalid_json_raises_with_path(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_taskspec(p)
