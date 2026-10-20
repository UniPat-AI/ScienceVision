"""End-to-end metadata and registry wiring for the MRI extent scorer."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from harbor_adapters.sci3d._score_core import score_rewards
from harbor_adapters.sci3d._scorer_registry import build_scorers
from harbor_adapters.sci3d._verify_translate import task_metadata_to_taskspec
from scripts.harbor import build_harbor_tasks as harbor_builder
from scripts.harbor.qa_gen_to_tasks import qa_record_to_task


AXES = ["axis-0", "axis-1", "axis-2"]
SCORER = {
    "primary": "extent_voxel_tolerance_v1",
    "secondary": [],
    "checks": ["extent_vox_axis_order", "extent_vox_per_axis_tolerance"],
    "aggregation": "all_required_with_partial_credit",
    "tolerance": {"extent_vox": 3},
    "expected_fields": ["extent_vox"],
    "axis_order": AXES,
}
ANSWER_SCHEMA = {
    "kind": "structured",
    "fields": ["extent_vox"],
    "field_specs": {
        "extent_vox": {
            "type": "integer_triplet",
            "unit": "voxel",
            "length": 3,
            "axis_order": AXES,
        }
    },
}
TERMINAL = {
    "kind": "structured",
    "fields": ["extent_vox"],
    "format": "json_object",
    "axis_order": AXES,
}
GT = {
    "status": "auto_verified",
    "type": "auto_measured",
    "value": {"extent_vox": [20, 37, 25]},
    "axis_order": AXES,
}


def _record() -> dict:
    return {
        "task_id": "sv-medi-med_mri_anatomy-test",
        "domain": "medical",
        "v_class": "V6",
        "instruction": "Return ordered extent_vox JSON.",
        "asset": {
            "asset_id": "mri:test",
            "name": "test",
            "path": "/data/test.nii.gz",
            "format": "nii.gz",
            "viewer": "niivue",
        },
        "answer_schema": ANSWER_SCHEMA,
        "terminal_answer": TERMINAL,
        "ground_truth": GT,
        "scorer": SCORER,
        "tool_policy": {"allowed_tools": [], "forbidden_tools": []},
    }


def test_qagen_harbor_metadata_preserves_complete_scorer_contract() -> None:
    task = qa_record_to_task(_record())
    metadata = harbor_builder._build_metadata(task)

    assert task["terminal_answer"] == TERMINAL
    assert metadata["scorer"] == SCORER
    assert metadata["answer_schema"] == ANSWER_SCHEMA
    assert metadata["terminal_answer"] == TERMINAL
    assert metadata["ground_truth"] == GT

    toml_data = tomllib.loads(harbor_builder._render_task_toml(task))
    assert toml_data["metadata"]["scorer"] == SCORER


def test_harbor_metadata_to_taskspec_preserves_numeric_config() -> None:
    metadata = harbor_builder._build_metadata(qa_record_to_task(_record()))
    task = task_metadata_to_taskspec(
        metadata,
        task_id="sv-medi-med_mri_anatomy-test",
        instruction="Return ordered extent_vox JSON.",
    )

    assert task.scorer.primary == "extent_voxel_tolerance_v1"
    assert task.scorer.checks == tuple(SCORER["checks"])
    assert task.scorer.aggregation == SCORER["aggregation"]
    assert task.scorer.tolerance == {"extent_vox": 3}
    assert task.scorer.expected_fields == ("extent_vox",)
    assert task.scorer.axis_order == tuple(AXES)


def test_registry_builds_extent_scorer_instead_of_skipping_it() -> None:
    scorers = build_scorers(
        "extent_voxel_tolerance_v1",
        [],
        llm=object(),
        domain="medical",
        rubric_text="test",
    )

    assert "extent_voxel_tolerance_v1" in {scorer.name for scorer in scorers}


def test_score_rewards_executes_extent_scorer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SCI3D_USE_FAKE_LLM", "1")
    metadata = harbor_builder._build_metadata(qa_record_to_task(_record()))
    (tmp_path / "sci3d_answer.json").write_text(
        json.dumps({"answer": {"extent_vox": [23, 34, 28]}}),
        encoding="utf-8",
    )

    rewards, audit = score_rewards(
        metadata,
        task_id="sv-medi-med_mri_anatomy-test",
        instruction="Return ordered extent_vox JSON.",
        agent_dir=tmp_path,
    )

    assert rewards["extent_voxel_tolerance_v1"] == 1.0
    assert audit["extent_voxel_tolerance_v1"]["degraded"] is False
    assert audit["extent_voxel_tolerance_v1"]["raw"]["axis_order"] == AXES
