"""Tests for the Harbor sci3d verifier adapter.

Run in the py3.12 harbor venv (Harbor requires >=3.12):

    .venv-harbor/bin/python -m pytest tests/harbor_adapters/ -q

No network, no GL/SAM: scorers run via SCI3D_USE_FAKE_LLM=1 + a canned judge
response, so every continuous score is produced deterministically.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("SCI3D_USE_FAKE_LLM", "1")

from harbor.models.verifier.result import VerifierResult  # noqa: E402

from harbor_adapters.sci3d._scorer_registry import (  # noqa: E402
    DomainJudge,
    build_scorers,
    load_rubric,
)
from harbor_adapters.sci3d._verify_translate import (  # noqa: E402
    WEIGHTED_KEY,
    answer_json_to_trajview,
    compute_process_rewards,
    task_metadata_to_taskspec,
    weighted_total,
)


# ── metadata -> TaskSpec ────────────────────────────────────────────────── #
_META = {
    "domain": "biology",
    "v_class": "V1",
    "asset_path": "/data/1F88.cif",
    "asset_format": "mmcif",
    "answer_schema": {"kind": "camera_pose", "fields": ["azimuth"]},
    "ground_truth": {"value": {"azimuth": 30}},
    "scorer": {"primary": "exact_match", "secondary": []},
    "allowed_tools": ["sv3d_open_scene", "sv3d_render", "finish"],
    "forbidden_tools": ["sv_cheat"],
    "weights": {"answer": 1.0, "process": 0.0},
}


def test_metadata_to_taskspec_fields():
    ts = task_metadata_to_taskspec(_META, task_id="t1", instruction="Open 1F88")
    assert ts.task_id == "t1"
    assert ts.v_class == "V1"
    assert ts.question == "Open 1F88"
    assert ts.asset.path == "/data/1F88.cif"
    assert ts.asset.format == "mmcif"
    # camera_pose collapses to the schema's "structured" kind.
    assert ts.answer_schema.kind == "structured"
    assert ts.scorer.primary == "exact_match"


def test_metadata_to_taskspec_ground_truth_missing():
    ts = task_metadata_to_taskspec(
        {**_META, "ground_truth": "needs_expert_annotation"},
        task_id="t1",
        instruction="q",
    )
    assert ts.ground_truth is None


def test_metadata_to_taskspec_ground_truth_present():
    ts = task_metadata_to_taskspec(
        {**_META, "ground_truth": {"value": {"azimuth": 30}}},
        task_id="t1",
        instruction="q",
    )
    assert ts.ground_truth is not None
    assert ts.ground_truth.value == {"azimuth": 30}


# ── answer/trajectory -> _TrajView ──────────────────────────────────────── #
def test_answer_json_to_trajview(tmp_path: Path):
    (tmp_path / "sci3d_answer.json").write_text(json.dumps({"answer": {"x": 1}}))
    traj = {
        "steps": [
            {"tool_calls": [{"function_name": "sv3d_open_scene"}]},
            {"tool_calls": [{"function_name": "sv3d_render"}]},
            {"tool_calls": []},
            {"tool_calls": [{"function_name": "finish"}]},
        ]
    }
    (tmp_path / "sci3d_trajectory.json").write_text(json.dumps(traj))
    v = answer_json_to_trajview(
        tmp_path / "sci3d_answer.json", tmp_path / "sci3d_trajectory.json", "t1"
    )
    assert v.final_answer == {"x": 1}
    assert v.tool_names == ["sv3d_open_scene", "sv3d_render", "finish"]


def test_answer_json_to_trajview_missing_files(tmp_path: Path):
    v = answer_json_to_trajview(tmp_path / "no.json", tmp_path / "no2.json", "t1")
    assert v.final_answer is None
    assert v.tool_names == []


# ── process rewards ─────────────────────────────────────────────────────── #
def test_process_rewards_full_closed_loop():
    r = compute_process_rewards(
        ["sv3d_open_scene", "sv3d_render", "finish"],
        ["sv3d_open_scene", "sv3d_render", "finish"],
        [],
    )
    assert r["process_opened_scene"] == 1.0
    assert r["process_rendered"] == 1.0
    assert r["process_finished"] == 1.0
    assert r["process_tool_policy"] == 1.0
    assert r["process_metadata_only"] == 0.0


def test_process_rewards_metadata_only_shortcut():
    r = compute_process_rewards(["sv_inspect_molecule", "finish"], None, None)
    assert r["process_opened_scene"] == 0.0
    assert r["process_rendered"] == 0.0
    assert r["process_metadata_only"] == 1.0


def test_process_rewards_policy_violation():
    r = compute_process_rewards(
        ["sv3d_open_scene", "sv_cheat"],
        ["sv3d_open_scene"],
        ["sv_cheat"],
    )
    assert r["process_tool_policy"] == 0.0


# ── weighted total ──────────────────────────────────────────────────────── #
def test_weighted_total_default_and_override():
    rewards = {
        "answer_score": 0.8,
        "process_opened_scene": 1.0,
        "process_rendered": 1.0,
        "process_tool_policy": 1.0,
        "process_finished": 1.0,
        "process_metadata_only": 0.0,
    }
    default = weighted_total(rewards, {})
    override = weighted_total(rewards, {"content": 0.0, "process": 1.0})
    assert override == pytest.approx(0.8, abs=1e-6)
    assert default == pytest.approx(0.8, abs=1e-6)


def test_weighted_total_clamped():
    assert weighted_total({"answer_score": 2.0}, {}) == 1.0
    assert weighted_total({}, {}) == 0.0


# ── scorer registry + domain rubric ─────────────────────────────────────── #
def test_load_rubric_known_and_fallback():
    assert "structural biology" in load_rubric("biology")
    # unknown domain falls back to the default rubric.
    fallback = load_rubric("does_not_exist")
    assert "scientific correctness" in fallback or len(fallback) > 0


def test_build_scorers_branches():
    assert [s.name for s in build_scorers("exact_match", [], domain="medical")] == [
        "answer_score"
    ]
    assert [s.name for s in build_scorers("llm_judge", [], domain="medical")] == [
        "llm_judge"
    ]
    # Unknown primaries never trigger an implicit network fallback.
    names = sorted(s.name for s in build_scorers("v1_camera_delta", ["mask_dice"], domain="robotics"))
    assert names == ["mask_dice"]


def test_domain_judge_injects_rubric():
    dj = DomainJudge(None, model="m", domain="biology", rubric_text=load_rubric("biology"))
    sp = dj._system_prompt()
    assert "DOMAIN RUBRIC (domain=biology)" in sp
    assert "structural biology" in sp


def test_domain_judge_score_with_fake_llm():
    from sci3d_agent.llm_client import FakeLLMClient

    fake = FakeLLMClient(
        [{"tool": "_judge", "args": {"score": 0.73, "rationale": "ok"},
          "input_tokens": 2, "output_tokens": 1}]
    )
    dj = DomainJudge(fake, model="m", domain="astronomy", rubric_text="R")

    class _Task:
        question = "q"
        v_class = "V1"
        ground_truth = None

    class _Traj:
        final_answer = {"a": 1}

    r = dj.score(_Task(), _Traj())
    assert r.value == pytest.approx(0.73)
    assert r.raw["domain"] == "astronomy"


# ── Sci3dVerifier end-to-end ────────────────────────────────────────────── #
class _FakePackageInfo:
    def __init__(self, metadata):
        self.metadata = metadata


class _FakeConfig:
    def __init__(self, metadata):
        # mirror Harbor: metadata lives at config level; config.task has none.
        self.metadata = metadata
        self.task = _FakePackageInfo(metadata={})


class _FakeTask:
    def __init__(self, metadata, instruction):
        self.config = _FakeConfig(metadata)
        self.instruction = instruction
        self.name = "sci3d/t1"
        self.short_name = "t1"


class _FakeTrialPaths:
    def __init__(self, root: Path):
        self.agent_dir = root / "agent"
        self.verifier_dir = root / "verifier"
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self.verifier_dir.mkdir(parents=True, exist_ok=True)


def _make_verifier(tmp_path: Path, metadata: dict):
    from harbor_adapters.sci3d.verifier import Sci3dVerifier

    tp = _FakeTrialPaths(tmp_path)
    (tp.agent_dir / "sci3d_answer.json").write_text(json.dumps({"answer": {"azimuth": 30}}))
    traj = {"steps": [
        {"tool_calls": [{"function_name": "sv3d_open_scene"}]},
        {"tool_calls": [{"function_name": "sv3d_render"}]},
        {"tool_calls": [{"function_name": "finish"}]},
    ]}
    (tp.agent_dir / "sci3d_trajectory.json").write_text(json.dumps(traj))

    # Build without running BaseVerifier.__init__ (needs a real environment).
    v = Sci3dVerifier.__new__(Sci3dVerifier)
    v.task = _FakeTask(metadata, "Open 1F88")
    v.trial_paths = tp
    v._judge_model = None
    return v, tp


def test_verifier_end_to_end(tmp_path: Path):
    v, tp = _make_verifier(tmp_path, _META)
    result = asyncio.run(v.verify())
    assert isinstance(result, VerifierResult)
    rewards = result.rewards
    assert rewards["answer_score"] == 1.0
    assert "llm_judge" not in rewards
    assert "process_opened_scene" in rewards and rewards["process_opened_scene"] == 1.0
    assert WEIGHTED_KEY in rewards
    assert 0.0 <= rewards[WEIGHTED_KEY] <= 1.0
    # audit file written.
    audit = json.loads((tp.verifier_dir / "sci3d_scores.json").read_text())
    assert audit["domain"] == "biology"
    assert audit["tool_names"] == ["sv3d_open_scene", "sv3d_render", "finish"]
    assert audit["evaluation_eligible"] is True
    assert audit["weights"] == {"answer": 1.0, "process": 0.0}


def test_verifier_missing_answer_degrades(tmp_path: Path):
    from harbor_adapters.sci3d.verifier import Sci3dVerifier

    tp = _FakeTrialPaths(tmp_path)  # no answer file written
    v = Sci3dVerifier.__new__(Sci3dVerifier)
    v.task = _FakeTask(_META, "Open 1F88")
    v.trial_paths = tp
    v._judge_model = None
    result = asyncio.run(v.verify())
    # still returns a result with the weighted key; no exception across boundary.
    assert WEIGHTED_KEY in result.rewards


# ── generator -> Harbor-loadable task ───────────────────────────────────── #
def test_build_harbor_tasks_loadable(tmp_path: Path):
    from harbor.models.task.task import Task

    from scripts.harbor.build_harbor_tasks import build_one

    task = {
        "task_id": "sv-test-0001",
        "domain": "biology",
        "v_class": "V1",
        "asset_path": "/data/x.cif",
        "asset_format": "mmcif",
        "answer_schema": {"kind": "camera_pose", "fields": ["azimuth"]},
        "scorer": {"primary": "exact_match", "secondary": []},
        "ground_truth": {"value": {"azimuth": 30}},
        "allowed_tools": ["sv3d_open_scene", "finish"],
        "forbidden_tools": [],
        "instruction": "Open the structure.",
    }
    task_dir = build_one(task, tmp_path)
    t = Task(task_dir)
    assert t.short_name == "sv-test-0001"
    m = t.config.metadata
    assert m["domain"] == "biology"
    assert m["weights"]["answer"] == 1.0
    # three layers present.
    assert t.config.agent.timeout_sec == 900.0
    assert t.config.verifier.timeout_sec == 900.0
    assert t.config.verifier.network_mode.value == "no-network"
    assert t.config.environment.build_timeout_sec == 600.0


def test_shadow_judge_enables_verifier_network(tmp_path: Path):
    from harbor.models.task.task import Task

    from scripts.harbor.build_harbor_tasks import build_one

    task = {
        "task_id": "sv-shadow-0001",
        "domain": "biology",
        "asset_path": "/data/x.cif",
        "asset_format": "mmcif",
        "answer_schema": {"kind": "structured", "fields": ["label"]},
        "scorer": {"primary": "exact_match", "secondary": ["shadow_llm_judge"]},
        "ground_truth": {"value": {"label": "x"}},
        "instruction": "Identify the label.",
    }
    built = Task(build_one(task, tmp_path))
    assert built.config.verifier.network_mode.value == "public"
