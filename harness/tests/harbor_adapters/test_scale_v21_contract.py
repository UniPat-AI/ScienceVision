from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from harbor_adapters.sci3d._score_core import score_rewards
from harbor_adapters.sci3d._scorer_registry import (
    UnknownVersionedScorerError,
    build_scorers,
)
from harbor_adapters.sci3d._verify_translate import task_metadata_to_taskspec
from scripts.harbor import build_harbor_tasks as builder
from scripts.harbor._multiview import plan_views
from scripts.harbor.qa_gen_to_tasks import qa_record_to_task
from sci3d_bench.scorers.deterministic_answer import (
    GENERIC_RUBRIC_VERSION,
    GENERIC_SCORER_NAME,
)


def _record(asset: Path) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "sv-v21-test",
        "domain": "medical",
        "v_class": "V1",
        "instruction": "Measure the target.",
        "asset": {
            "asset_id": "ct-1",
            "name": "ct",
            "path": str(asset),
            "repo_path": str(asset),
            "format": "nii.gz",
            "viewer": "niivue",
        },
        "answer_schema": {"kind": "structured", "fields": ["extent_vox"]},
        "terminal_answer": {"kind": "structured", "fields": ["extent_vox"]},
        "ground_truth": {
            "status": "auto_verified",
            "type": "auto_measured",
            "value": {"extent_vox": [10, 20, 30]},
        },
        "scorer": {
            "primary": GENERIC_SCORER_NAME,
            "checks": ["extent_vox"],
            "aggregation": "all_required_with_partial_credit",
        },
        "scorer_config": {
            "schema_version": GENERIC_RUBRIC_VERSION,
            "strict_json_object": True,
            "fields": [
                {
                    "field": "extent_vox",
                    "comparison": "absolute_tolerance",
                    "tolerance": 1.0,
                    "unit": "voxel",
                }
            ],
        },
        "asset_grounding": {"status": "grounded_from_asset"},
        "asset_hashes": [
            {"repo_path": str(asset), "sha256": "a" * 64, "byte_size": 1}
        ],
        "tool_policy": {"allowed_tools": ["viewer_slice"], "forbidden_tools": []},
    }


def test_qagen_metadata_taskspec_round_trip_preserves_rich_contract(tmp_path: Path):
    asset = tmp_path / "ct.nii.gz"
    asset.write_bytes(b"x")
    task = qa_record_to_task(_record(asset), asset_root=None)
    assert task["evaluation_eligible"] is True
    meta = builder._build_metadata(task)
    assert meta["scorer_config"]["schema_version"] == GENERIC_RUBRIC_VERSION
    assert meta["asset_grounding"]["status"] == "grounded_from_asset"
    spec = task_metadata_to_taskspec(meta, task_id=task["task_id"], instruction="q")
    assert spec.scorer.config["fields"][0]["field"] == "extent_vox"
    assert spec.evaluation_eligible is True
    assert spec.asset_hashes[0]["sha256"] == "a" * 64


def test_deterministic_toml_and_instruction_are_offline_and_strict(tmp_path: Path):
    asset = tmp_path / "ct.nii.gz"
    asset.write_bytes(b"x")
    task = qa_record_to_task(_record(asset), asset_root=None)
    toml = builder._render_task_toml(task, containerized=True)
    parsed = tomllib.loads(toml)
    # Security metadata now also describes the policy, so text counting would
    # confuse metadata field names with executable Harbor section settings.
    assert parsed["agent"]["network_mode"] == "no-network"
    assert parsed["verifier"]["network_mode"] == "no-network"
    assert parsed["verifier"]["environment"]["network_mode"] == "no-network"
    assert parsed["environment"]["network_mode"] == "no-network"
    assert parsed["metadata"]["security_contract"]["agent_network_mode"] == "no-network"
    out = builder._instruction_with_terminal_contract(task, task["instruction"])
    assert "SCI3D_FINAL_JSON:" in out
    assert '"extent_vox":null' in out


def test_registry_has_no_implicit_judges_and_unknown_strict_fails(monkeypatch):
    monkeypatch.setenv("SCI3D_USE_FAKE_LLM", "1")
    assert [scorer.name for scorer in build_scorers(GENERIC_SCORER_NAME, [])] == [
        "answer_score"
    ]
    with pytest.raises(UnknownVersionedScorerError):
        build_scorers("future_deterministic_rubric_v9_0", [], fail_on_unknown=True)
    assert build_scorers("v1_camera_delta", []) == []


def test_formal_reward_equals_answer_score_process_is_diagnostic(tmp_path: Path):
    asset = tmp_path / "ct.nii.gz"
    asset.write_bytes(b"x")
    task = qa_record_to_task(_record(asset), asset_root=None)
    meta = builder._build_metadata(task)
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "sci3d_answer.json").write_text(
        json.dumps({"answer": {"extent_vox": [11, 20, 29]}})
    )
    rewards, audit = score_rewards(
        meta, task_id=task["task_id"], instruction=task["instruction"], agent_dir=agent
    )
    assert rewards["answer_score"] == 1.0
    assert rewards["reward"] == rewards["answer_score"]
    assert audit["process_rewards_diagnostic_only"] is True
    assert "llm_judge" not in rewards and "process_judge" not in rewards


def test_unknown_versioned_scorer_has_zero_formal_reward_without_judge(tmp_path: Path):
    meta = {
        "domain": "biology",
        "v_class": "V1",
        "asset_path": "/data/x.cif",
        "asset_format": "mmcif",
        "answer_schema": {"kind": "structured", "fields": ["x"]},
        "ground_truth": {"value": {"x": 1}},
        "scorer": {"primary": "future_deterministic_rubric_v9_0"},
        "evaluation_eligible": True,
    }
    rewards, audit = score_rewards(
        meta, task_id="unknown", instruction="q", agent_dir=tmp_path
    )
    assert rewards["reward"] == 0.0
    assert rewards["answer_score"] == 0.0
    assert "llm_judge" not in rewards and "process_judge" not in rewards
    assert "unsupported scorer contract" in audit["taskspec_error"]


def test_eligible_missing_asset_fails_before_task_directory_is_created(tmp_path: Path):
    task = qa_record_to_task(_record(tmp_path / "missing.nii.gz"), asset_root=None)
    with pytest.raises(FileNotFoundError):
        builder.build_one(task, tmp_path / "out")
    assert not (tmp_path / "out" / task["task_id"]).exists()


def test_multiview_passes_requested_mask_id():
    plan = plan_views(
        {
            "asset_format": "nii.gz",
            "asset_path": "/data/ct.nii.gz",
            "mask_path": "/data/mask.nii.gz",
            "mask_id": "2",
        },
        n_views=1,
    )
    assert plan is not None
    assert plan[0][1]["mask_id"] == "2"


def test_multi_asset_staging_preserves_roles_and_instruction_payload(tmp_path: Path):
    first = tmp_path / "a.cif"
    second = tmp_path / "b.cif"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    task = {
        "task_id": "multi",
        "asset_path": str(first),
        "asset_format": "cif",
        "asset": {"path": str(first), "format": "cif", "role": "reference"},
        "assets": [
            {"path": str(first), "format": "cif", "role": "reference"},
            {"path": str(second), "format": "cif", "role": "query"},
        ],
    }
    staged = builder._stage_assets(task, tmp_path / "environment")
    assert [row["role"] for row in staged["assets"]] == ["reference", "query"]
    payload_text = builder._instruction_with_container_assets("Compare.", staged)
    payload = json.loads(payload_text.split("```sci3d\n", 1)[1].split("\n```", 1)[0])
    assert [row["path"] for row in payload["assets"]] == ["/data/a.cif", "/data/b.cif"]


def test_best_slice_uses_requested_label_and_explicit_union_uses_true_foreground(monkeypatch):
    labels = [
        {"id": 1, "best_slices": {"axial": 11}},
        {"id": 2, "best_slices": {"axial": 22}},
    ]
    union_summary = {
        "nonzero_labels": [1, 2],
        "voxel_count": 37,
        "bbox_voxel": [[1, 2, 3], [9, 10, 11]],
        "centroid_voxel": [4.5, 5.5, 6.5],
        "best_slices": {"sagittal": 4, "coronal": 5, "axial": 6},
        "best_slice_voxel_counts": {"sagittal": 8, "coronal": 9, "axial": 10},
    }
    calls = []

    def inspect(args):
        calls.append(args)
        if args.get("foreground_mode") == "union_nonzero":
            return {
                "shape": [12, 13, 14],
                "foreground_union": union_summary,
            }
        return {"labels": labels}

    monkeypatch.setattr(builder, "_lookup_render_handler", lambda _: inspect)
    base = {
        "asset_format": "nii.gz",
        "asset_path": "/data/ct.nii.gz",
        "mask_path": "/data/mask.nii.gz",
    }
    assert builder._resolve_best_slices({**base, "mask_id": 2}) == {"axial": 22}
    union = {
        **base,
        "asset_grounding": {
            "verified_entities": {"foreground_rule": "union of all nonzero labels"}
        },
    }
    plan = builder._resolve_medical_roi_plan(union)
    assert plan.mode == "union_nonzero"
    assert plan.best_slices == {
        "sagittal": 4,
        "coronal": 5,
        "axial": 6,
        "__shape__": [12, 13, 14],
        "__bbox__": [[1, 2, 3], [9, 10, 11]],
        "__roi_plane_anchor_voxel__": [4.5, 5.5, 6.5],
    }
    assert plan.selection == {
        "mode": "union_nonzero",
        "nonzero_labels": [1, 2],
        "evidence": {
            "voxel_count": 37,
            "bbox_voxel": [[1, 2, 3], [9, 10, 11]],
            "centroid_voxel": [4.5, 5.5, 6.5],
            "best_slices": {"sagittal": 4, "coronal": 5, "axial": 6},
            "best_slice_voxel_counts": {"sagittal": 8, "coronal": 9, "axial": 10},
        },
    }
    selected_over_union = builder._resolve_medical_roi_plan({**union, "mask_id": 2})
    assert selected_over_union.mode == "mask_id"
    assert selected_over_union.best_slices == {"axial": 22}
    assert "foreground_mode" not in calls[0]
    assert calls[1]["foreground_mode"] == "union_nonzero"
    assert "foreground_mode" not in calls[2]
