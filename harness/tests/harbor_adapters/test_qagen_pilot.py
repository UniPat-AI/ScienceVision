"""Tests for the QA-Gen pilot wiring: ExactMatchScorer + qa_gen_to_tasks mapper.

Run in the py3.12 harbor venv:
    .venv-harbor/bin/python -m pytest tests/harbor_adapters/test_qagen_pilot.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("SCI3D_USE_FAKE_LLM", "1")

from sci3d_bench.scorers.exact_match import ExactMatchScorer  # noqa: E402
from scripts.harbor.qa_gen_to_tasks import (  # noqa: E402
    _resolve_asset_path,
    qa_record_to_task,
)
from scripts.harbor import build_harbor_tasks as gen  # noqa: E402


def _task(gt_value):
    return SimpleNamespace(ground_truth=SimpleNamespace(value=gt_value))


def _traj(answer):
    return SimpleNamespace(final_answer=answer)


# ── ExactMatchScorer ─────────────────────────────────────────────────────── #
def test_exact_match_all_fields():
    gt = {"grasp_holds": True, "cube_lifted": True, "net_rise_class": "~15-20cm"}
    r = ExactMatchScorer().score(_task(gt), _traj(dict(gt)))
    assert r.value == 1.0
    assert r.raw["matched"] == 3


def test_exact_match_null_field_passes_when_the_answer_also_says_null():
    """A null ground-truth value is a value: null == null, and nothing else."""
    gt = {"lower_surface": None, "same_level": True}
    assert ExactMatchScorer().score(
        _task(gt), _traj({"lower_surface": None, "same_level": True})
    ).value == 1.0
    assert ExactMatchScorer().score(
        _task(gt), _traj({"lower_surface": "", "same_level": True})
    ).value == 0.5


def test_exact_match_partial_credit():
    gt = {"a": True, "b": False, "c": "x"}
    r = ExactMatchScorer().score(_task(gt), _traj({"a": True, "b": True, "c": "x"}))
    assert r.value == pytest.approx(2 / 3)


def test_exact_match_bool_and_whitespace_normalization():
    gt = {"held": True, "rise": "~15-20cm"}
    # "True" string vs bool, and a spaced variant of the class.
    r = ExactMatchScorer().score(_task(gt), _traj({"held": "True", "rise": "~15-20 cm"}))
    assert r.value == 1.0


def test_exact_match_json_string_answer():
    gt = {"x": "lumen"}
    r = ExactMatchScorer().score(_task(gt), _traj('{"x": "lumen"}'))
    assert r.value == 1.0
    assert r.raw["answer_kind"] == "dict"


def test_exact_match_freetext_substring_fallback():
    gt = {"compartment": "wall", "label": "atherosclerosis"}
    ans = "The focus sits in the aortic wall, consistent with atherosclerosis."
    r = ExactMatchScorer().score(_task(gt), _traj(ans))
    assert r.value == 1.0
    assert r.raw["answer_kind"] == "text"


def test_exact_match_no_gt_degrades():
    r = ExactMatchScorer().score(_task(None), _traj({"a": 1}))
    assert r.degraded is True
    assert r.value == 0.0


# ── qa_gen_to_tasks mapper ───────────────────────────────────────────────── #
_MED = {
    "task_id": "sv-medical-k2-0004",
    "domain": "medical",
    "v_class": None,
    "k_class": "K2",
    "instruction": "Localize the focus.",
    "asset": {"path": "/datasets/x/ct.nii.gz", "format": "nifti"},
    "answer_schema": {"kind": "sub_location_plus_pathology", "fields": ["a"]},
    "scorer": {"primary": "k2_sublocation_diagnosis", "secondary": []},
    "tool_policy": {
        "allowed_tools": ["sv3d_open_scene", "sv3d_render"],
        "forbidden_tools": ["direct_mask_text_lookup"],
    },
    "ground_truth": {"status": "auto_seed", "value": {"a": "wall"}},
}

_PHYS = {
    "task_id": "genesis-p2-franka-grasp-lift",
    "domain": "physics",
    "v_class": "V3",
    "instruction": "Predict the grasp.",
    "asset": {"path": "/viewer/x.glb", "format": "glb"},
    "answer_schema": {"kind": "structured", "fields": {"grasp_holds": "bool"}},
    "scorer": {"primary": "exact_match", "secondary": ["llm_judge"]},
    "tool_policy": {"forbidden_tools": ["scene.step"]},
    "ground_truth": {"type": "physics_rollout", "value": {"grasp_holds": True}},
}


def test_mapper_flattens_nested_fields():
    t = qa_record_to_task(_MED)
    assert t["asset_path"] == "/datasets/x/ct.nii.gz"
    assert t["asset_format"] == "nifti"
    assert t["allowed_tools"] == ["sv3d_open_scene", "sv3d_render"]
    assert t["forbidden_tools"] == ["direct_mask_text_lookup"]
    # v_class null -> falls back to k_class.
    assert t["v_class"] == "K2"
    assert t["scorer"]["primary"] == "k2_sublocation_diagnosis"
    assert t["ground_truth"]["value"] == {"a": "wall"}


def test_mapper_physics_keeps_empty_allowed_and_exact_match():
    t = qa_record_to_task(_PHYS)
    assert t["allowed_tools"] == []  # Genesis tasks only forbid tools
    assert t["forbidden_tools"] == ["scene.step"]
    assert t["v_class"] == "V3"
    assert t["scorer"]["primary"] == "exact_match"


def test_mapper_infers_asset_format_from_extension():
    """A record without asset.format must not be mislabeled as a mesh.

    The two expert-v2 chirality assets are MDL molfiles carrying no explicit
    format; defaulting them to "obj" routed them to the trimesh mesh branch, so
    no CC2D tree could be built for them. The extension is the fallback.
    """
    rec = dict(_PHYS, asset={"path": "/data/3672_ibuprofen.mol"})
    t = qa_record_to_task(rec)
    assert t["asset_format"] == "mol"


def test_mapper_infers_per_asset_format_from_extension():
    rec = dict(
        _PHYS,
        asset={"path": "/data/a.mol"},
        assets=[{"path": "/data/a.mol"}, {"path": "/data/b.xyz", "format": "xyz"}],
    )
    t = qa_record_to_task(rec)
    assert [a["asset_format"] for a in t["assets"]] == ["mol", "xyz"]


def test_mapper_asset_format_falls_back_to_obj_without_extension():
    rec = dict(_PHYS, asset={"path": "/data/blob"})
    t = qa_record_to_task(rec)
    assert t["asset_format"] == "obj"


def test_mapper_explicit_format_wins_over_extension():
    rec = dict(_PHYS, asset={"path": "/data/x.glb", "format": "glb"})
    t = qa_record_to_task(rec)
    assert t["asset_format"] == "glb"


def test_mapper_carries_n_views_and_seed_pose_when_present():
    rec = dict(_PHYS, n_views=3, seed_pose={"azimuth": 45.0})
    t = qa_record_to_task(rec)
    assert t["n_views"] == 3
    assert t["seed_pose"] == {"azimuth": 45.0}


def test_mapper_omits_n_views_and_seed_pose_when_absent():
    # _PHYS has neither field; the mapper must not fabricate them so
    # _stage_rendered_views falls back to its own defaults.
    t = qa_record_to_task(_PHYS)
    assert "n_views" not in t
    assert "seed_pose" not in t


def test_resolve_asset_path_prefers_repo_path():
    from pathlib import Path

    asset = {"path": "/datasets/x/ct.nii.gz", "repo_path": "datasets/x/ct.nii.gz"}
    # With a root, repo_path resolves to a host path; without, keep verbatim.
    assert _resolve_asset_path(asset, Path("/repo")) == "/repo/datasets/x/ct.nii.gz"
    assert _resolve_asset_path(asset, None) == "/datasets/x/ct.nii.gz"


def test_mapper_asset_root_resolves_medical_path():
    from pathlib import Path

    rec = dict(_MED)
    rec["asset"] = {
        "path": "/datasets/M3D-RefSeg/s0146/ct.nii.gz",
        "repo_path": "datasets/M3D-RefSeg/s0146/ct.nii.gz",
        "format": "nifti",
    }
    t = qa_record_to_task(rec, asset_root=Path("/home/x/repo"))
    assert t["asset_path"] == str(
        (Path("/home/x/repo") / "datasets/M3D-RefSeg/s0146/ct.nii.gz").resolve()
    )


def test_mapper_output_builds_valid_task(tmp_path):
    t = qa_record_to_task(_PHYS)
    task_dir = gen.build_one(t, tmp_path, containerized=False)
    toml = (task_dir / "task.toml").read_text()
    assert 'name = "sci3d/genesis-p2-franka-grasp-lift"' in toml
    assert "exact_match" in toml
    assert "grasp_holds" in toml  # ground_truth.value serialized


# ── trajectory (process) judge ───────────────────────────────────────────── #
def test_trajview_parses_steps_with_observation(tmp_path):
    from harbor_adapters.sci3d._verify_translate import answer_json_to_trajview
    import json as _json

    ans = tmp_path / "sci3d_answer.json"
    traj = tmp_path / "sci3d_trajectory.json"
    ans.write_text(_json.dumps({"answer": {"x": 1}}))
    traj.write_text(
        _json.dumps(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "message": "load the volume",
                        "tool_calls": [{"function_name": "execute_code"}],
                        "observation": {"results": [{"content": "shape (10,10,10)"}]},
                    },
                    {
                        "step_id": 2,
                        "message": "submit",
                        "tool_calls": [{"function_name": "finish"}],
                    },
                ]
            }
        )
    )
    view = answer_json_to_trajview(ans, traj, "t1")
    assert len(view.steps) == 2
    assert view.steps[0]["tools"] == ["execute_code"]
    assert "shape (10,10,10)" in view.steps[0]["observation"]
    assert view.tool_names == ["execute_code", "finish"]


def test_dual_source_answer_from_harbor_trajectory(tmp_path):
    """No sci3d_answer.json -> answer falls back to the last agent message."""
    from harbor_adapters.sci3d._verify_translate import answer_json_to_trajview
    import json as _json

    traj = tmp_path / "sci3d_trajectory.json"
    traj.write_text(
        _json.dumps(
            {
                "steps": [
                    {"step_id": 1, "source": "agent", "message": "inspecting",
                     "tool_calls": [{"function_name": "Bash"}]},
                    {"step_id": 2, "source": "agent",
                     "message": "FINAL ANSWER: 4 pieces"},
                ]
            }
        )
    )
    v = answer_json_to_trajview(tmp_path / "missing.json", traj, "t1")
    assert v.final_answer == "FINAL ANSWER: 4 pieces"
    assert v.tool_names == ["Bash"]


def test_sci3d_answer_wins_over_trajectory(tmp_path):
    from harbor_adapters.sci3d._verify_translate import answer_json_to_trajview
    import json as _json

    ans = tmp_path / "sci3d_answer.json"
    traj = tmp_path / "sci3d_trajectory.json"
    ans.write_text(_json.dumps({"answer": {"n": 4}}))
    traj.write_text(_json.dumps({"steps": [{"source": "agent", "message": "junk"}]}))
    v = answer_json_to_trajview(ans, traj, "t1")
    assert v.final_answer == {"n": 4}


def test_process_judge_built_and_scores():
    from harbor_adapters.sci3d._scorer_registry import build_scorers
    from types import SimpleNamespace as NS

    scs = build_scorers(
        "exact_match", ["shadow_llm_judge", "shadow_process_judge"], domain="physics"
    )
    assert "process_judge" in [s.name for s in scs]
    pj = next(s for s in scs if s.name == "process_judge")
    task = NS(question="Q", v_class="V3", ground_truth=NS(value={"a": True}))
    traj = NS(
        final_answer={"a": True},
        steps=(
            {"step": 1, "message": "load", "tools": ["execute_code"], "observation": "shape"},
            {"step": 2, "message": "done", "tools": ["finish"], "observation": ""},
        ),
    )
    r = pj.score(task, traj)
    assert r.metric == "process_judge"
    assert r.raw["n_steps"] == 2


def test_process_judge_degrades_on_empty_trajectory():
    from harbor_adapters.sci3d._scorer_registry import build_scorers
    from types import SimpleNamespace as NS

    pj = next(
        s
        for s in build_scorers("llm_judge", ["shadow_process_judge"], domain="")
        if s.name == "process_judge"
    )
    r = pj.score(NS(question="Q", v_class="V1", ground_truth=None), NS(final_answer=None, steps=()))
    assert r.degraded is True


# ── containerized layout for `harbor run` ────────────────────────────────── #
def test_containerized_task_has_environment_dir(tmp_path):
    """harbor run's Task.is_valid_dir requires an environment/ dir per task."""
    t = qa_record_to_task(_PHYS)
    asset = tmp_path / "x.glb"
    asset.write_bytes(b"glb")
    t["asset_path"] = str(asset)
    t["assets"][0]["asset_path"] = str(asset)
    task_dir = gen.build_one(t, tmp_path, containerized=True)
    dockerfile = task_dir / "environment" / "Dockerfile"
    assert dockerfile.exists()
    assert "FROM sci3d-host-agent:latest" in dockerfile.read_text()
    # host route stays free of the environment/ dir.
    host_dir = gen.build_one(qa_record_to_task(_MED), tmp_path / "host", containerized=False)
    assert not (host_dir / "environment").exists()


def test_agent_env_claude_code_dockerfile(tmp_path):
    t = qa_record_to_task(_PHYS)
    asset = tmp_path / "x.glb"
    asset.write_bytes(b"glb")
    t["asset_path"] = str(asset)
    t["assets"][0]["asset_path"] = str(asset)
    task_dir = gen.build_one(t, tmp_path, containerized=True, agent_env="claude-code")
    df = (task_dir / "environment" / "Dockerfile").read_text()
    assert "FROM sci3d-claude-code:latest" in df


# ── results collector + leaderboard ──────────────────────────────────────── #
def test_collect_leaderboard_groups_by_harness(tmp_path):
    from scripts.harbor.collect_qagen_results import build_leaderboard

    # Every eligible run carries the gate's verdict: the headline averages
    # citable runs only, and a run with no verdict is excluded as ungated (see
    # ``_citability``). This test is about grouping, so all of them are citable.
    runs = [
        {"task_id": "a", "set": "S", "domain": "physics", "v_class": "V6",
         "harness": "sci3d-agent", "model": "claude-sonnet-4-6",
         "status": "ok", "evaluation_eligible": True,
         "rewards": {"reward": 0.7, "exact_match": 0.5},
         "process_gate": {"citable": True, "blockers": []}},
        {"task_id": "b", "set": "S", "domain": "physics", "v_class": "V3",
         "harness": "sci3d-agent", "model": "claude-sonnet-4-6",
         "status": "ok", "evaluation_eligible": True, "rewards": {"reward": 0.5},
         "process_gate": {"citable": True, "blockers": []}},
        {"task_id": "a", "set": "S", "domain": "physics", "v_class": "V6",
         "harness": "claude-code", "model": "claude-opus-4-8",
         "status": "ok", "evaluation_eligible": True, "rewards": {"reward": 0.9},
         "process_gate": {"citable": True, "blockers": []}},
        {"task_id": "c", "set": "S", "domain": "medical", "v_class": "K3",
         "harness": "claude-code", "model": "claude-opus-4-8",
         "status": "error", "evaluation_eligible": False, "rewards": {}},
    ]
    board = build_leaderboard(runs, tmp_path)
    assert board["n_runs"] == 4
    assert board["n_completed"] == 3
    # per (harness, model): sci3d-agent (0.7+0.5)/2=0.6, claude-code 0.9
    hm = board["by_harness_model_mean_reward"]
    assert hm["sci3d-agent / claude-sonnet-4-6"] == pytest.approx(0.6)
    assert hm["claude-code / claude-opus-4-8"] == pytest.approx(0.9)
    # rows grouped by harness then ranked; harness/model present on each row
    assert all("harness" in r and "model" in r for r in board["rows"])
    assert (tmp_path / "runs_leaderboard.md").exists()
    assert (tmp_path / "runs_leaderboard.json").exists()


# ── trajectory store / GT stripping ──────────────────────────────────────── #
def test_stage_strips_ground_truth(tmp_path):
    from scripts.harbor.upload_trajectories_modelscope import _stage

    run = {
        "task_id": "t1", "set": "S", "harness": "h", "model": "m",
        "answer": "a", "ground_truth": {"secret": 1},
        "rewards": {"reward": 0.5}, "trajectory": [],
    }
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    stage = tmp_path / "stage"
    _stage([(tmp_path / "x.json", run)], tasks_root, stage, strip_ground_truth=True)
    out = json.loads((stage / "h" / "S" / "t1.json").read_text())
    assert "ground_truth" not in out
    assert out["ground_truth_stripped"] is True
    assert out["answer"] == "a" and out["rewards"] == {"reward": 0.5}


def test_stage_keeps_ground_truth_by_default(tmp_path):
    from scripts.harbor.upload_trajectories_modelscope import _stage

    run = {"task_id": "t1", "set": "S", "harness": "h", "model": "m",
           "ground_truth": {"v": 1}, "rewards": {"reward": 0.5}}
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    stage = tmp_path / "stage"
    _stage([(tmp_path / "x.json", run)], tasks_root, stage)
    out = json.loads((stage / "h" / "S" / "t1.json").read_text())
    assert out["ground_truth"] == {"v": 1}


def test_leaderboard_reports_answer_and_process_separately(tmp_path):
    from scripts.harbor.collect_qagen_results import build_leaderboard

    runs = [
        {"task_id": "a", "set": "S", "domain": "chemistry", "v_class": "",
         "harness": "cc", "model": "m", "status": "ok", "evaluation_eligible": True,
         "rewards": {"reward": 0.7, "answer_score": 0.7, "process_compliance": 0.5},
         "process_gate": {"citable": True, "blockers": []}},
        {"task_id": "b", "set": "S", "domain": "chemistry", "v_class": "",
         "harness": "cc", "model": "m", "status": "ok", "evaluation_eligible": True,
         "rewards": {"reward": 0.5, "answer_score": 0.5, "process_compliance": 0.9},
         "process_gate": {"citable": True, "blockers": []}},
    ]
    board = build_leaderboard(runs, tmp_path)
    assert board["by_harness_model_mean_answer"]["cc / m"] == pytest.approx(0.6)
    assert board["by_harness_model_mean_process"]["cc / m"] == pytest.approx(0.7)
    md = (tmp_path / "runs_leaderboard.md").read_text()
    assert "| answer | process compliance |" in md
    assert "shadow answer" in md


def test_write_runs_copies_screenshots(tmp_path):
    from scripts.harbor.collect_qagen_results import write_runs, _run_slug

    src = tmp_path / "trial_agent_renders"
    src.mkdir()
    (src / "view0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    run = {
        "task_id": "t1", "set": "S", "harness": "ccmcp", "model": "opus",
        "rewards": {"reward": 0.5}, "answer": "a", "trajectory": [],
        "screenshots_src": [str(src / "view0.png")],
    }
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    write_runs([run], tasks_root)

    slug = _run_slug(run)
    assert (tasks_root / "S" / "runs" / slug / "view0.png").read_bytes().startswith(b"\x89PNG")
    rec = json.loads((tasks_root / "S" / "runs" / f"{slug}.json").read_text())
    assert rec["screenshots"] == [f"{slug}/view0.png"]
    assert "screenshots_src" not in rec
