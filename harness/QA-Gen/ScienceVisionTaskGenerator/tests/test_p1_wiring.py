"""Tests for P1 wiring: computed difficulty, vision-grounded challenger,
opt-in ground-truth fill, and the text-only shortcut probe.

All opt-in features must leave the deterministic baseline unchanged and be
exercisable with fakes (no network, no real assets).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sciencevision_taskgen.llm import ChatResult, UsageLog
from sciencevision_taskgen.pipeline.builder_stage import BuilderStage
from sciencevision_taskgen.pipeline.challenger_stage import ChallengerStage


# ---------------------------------------------------------------------------
# Computed difficulty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "v_class,steps,expected",
    [
        ("V1", 8, "medium"),   # +1 steps
        ("V2", 8, "medium"),
        ("V3", 8, "hard"),     # +2 class +1 steps
        ("V4", 12, "hard"),    # +2 class +2 steps
        ("V5", 6, "medium"),   # +2 class, <8 steps
        ("V6", 6, "medium"),   # +1 class
        ("V1", 0, "easy"),     # no signal
    ],
)
def test_compute_difficulty(v_class, steps, expected):
    assert BuilderStage._compute_difficulty(v_class, steps) == expected


def _bio_asset() -> dict:
    return {
        "asset_id": "biology:4HHB",
        "name": "4HHB",
        "path": "datasets/biology/4HHB.cif",
        "repo_path": "datasets/biology/4HHB.cif",
        "viewer": "molstar",
        "format": "mmcif",
        "thumb": None,
        "exists_on_disk": True,
    }


def _plan(v_class: str = "V1") -> list[dict]:
    return [{"domain": "biology", "v_class": v_class, "replica": 0, "status": "ready"}]


def test_builder_uses_template_difficulty_by_default():
    from sciencevision_taskgen.templates import TEMPLATES

    tasks = BuilderStage({}).build_tasks(_plan("V1"), {"domains": {"biology": [_bio_asset()]}})
    assert tasks[0]["difficulty"] == TEMPLATES["biology"]["V1"].difficulty


def test_builder_computes_difficulty_when_enabled():
    cfg = {"builder": {"compute_difficulty": True}}
    tasks = BuilderStage(cfg).build_tasks(_plan("V1"), {"domains": {"biology": [_bio_asset()]}})
    expected = BuilderStage._compute_difficulty("V1", BuilderStage._default_max_steps("V1"))
    assert tasks[0]["difficulty"] == expected


# ---------------------------------------------------------------------------
# Vision-grounded challenger
# ---------------------------------------------------------------------------

class FakeVisionClient:
    def __init__(self, content: str) -> None:
        self._content = content
        self.vision_calls = 0
        self.text_calls = 0

    def _result(self) -> ChatResult:
        return ChatResult(content=self._content, latency_ms=1.0,
                          prompt_tokens=10, completion_tokens=5, reasoning_tokens=0)

    def complete_vision_with_retry(self, *a: Any, **k: Any) -> ChatResult:
        self.vision_calls += 1
        return self._result()

    def complete_with_retry(self, *a: Any, **k: Any) -> ChatResult:
        self.text_calls += 1
        return self._result()


def _llm_task() -> dict:
    return {
        "task_id": "t1",
        "domain": "biology",
        "v_class": "V2",
        "asset": {"path": "p", "viewer": "molstar", "format": "pdb",
                  "exists_on_disk": True, "thumb": "/viewer/thumbs/x.webp"},
        "instruction": "Judge pocket visibility.",
        "required_evidence": ["screenshot"],
        "answer_schema": {"kind": "value", "fields": ["visible"]},
        "ground_truth": {"status": "expert_verified", "value": "yes"},
        "scorer": {"primary": "exact", "checks": ["c"]},
        "tool_policy": {"allowed_tools": ["v"]},
        "quality_gates": {"irreducible_3d": True},
        "terminal_answer": {"kind": "value", "fields": ["visible"]},
        "provenance": {},
    }


def _llm_config() -> dict:
    return {"llm": {"enabled": True, "default_model": "claude-sonnet-4-6"}}


def test_challenger_uses_vision_when_thumb_present(tmp_path, monkeypatch):
    client = FakeVisionClient(json.dumps([{"code": "SHORTCUT_RISK", "severity": "high", "detail": "d"}]))
    log = UsageLog.for_run(tmp_path / "run", {"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}})
    stage = ChallengerStage(config=_llm_config(), llm_client=client, usage_log=log)
    # Pretend the thumbnail exists on disk.
    monkeypatch.setattr(ChallengerStage, "_resolve_asset_image", staticmethod(lambda task: ["dummy.webp"]))

    result = stage.critique_tasks([_llm_task()])[0]
    assert client.vision_calls == 1 and client.text_calls == 0
    assert any(i["code"] == "llm_SHORTCUT_RISK" for i in result["issues"])


def test_challenger_falls_back_to_text_without_thumb(tmp_path, monkeypatch):
    client = FakeVisionClient(json.dumps([]))
    log = UsageLog.for_run(tmp_path / "run", {"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}})
    stage = ChallengerStage(config=_llm_config(), llm_client=client, usage_log=log)
    monkeypatch.setattr(ChallengerStage, "_resolve_asset_image", staticmethod(lambda task: []))

    stage.critique_tasks([_llm_task()])
    assert client.vision_calls == 0 and client.text_calls == 1


# ---------------------------------------------------------------------------
# Opt-in ground-truth fill wiring
# ---------------------------------------------------------------------------

def test_ground_truth_fill_wiring(tmp_path):
    """Runner._fill_ground_truth dispatches the GT stage; an unknown GT type
    routes to awaiting_expert (deterministic, no LLM)."""
    from sciencevision_taskgen.runner import ScienceVisionPipelineRunner

    cfg = {"assets_path": "viewer/assets.json", "llm": {"enabled": False}}
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    runner = ScienceVisionPipelineRunner(str(cfg_path))

    task = {"task_id": "g1", "ground_truth": {"type": "totally_unknown_type", "status": "needs_expert_annotation"}}
    filled, report = runner._fill_ground_truth([task])
    assert report["total"] == 1
    assert report["awaiting_expert"] == 1
    assert filled[0]["ground_truth"]["status"] == "awaiting_expert"


# ---------------------------------------------------------------------------
# Text-only shortcut probe
# ---------------------------------------------------------------------------

class FakeProbeClient:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete_with_retry(self, *a: Any, **k: Any) -> ChatResult:
        return ChatResult(content=self._content, latency_ms=1.0,
                          prompt_tokens=1, completion_tokens=1, reasoning_tokens=0)


def test_probe_require_correct_matches_ground_truth():
    from sciencevision_taskgen.probes import make_text_only_solver

    task = {"instruction": "q", "answer_schema": {}, "asset": {"name": "a"},
            "ground_truth": {"value": "C2"}}
    correct = make_text_only_solver(
        FakeProbeClient(json.dumps({"answer": "c2", "answerable_text_only": True, "confidence": 0.9})),
        "m",
    )
    wrong = make_text_only_solver(
        FakeProbeClient(json.dumps({"answer": "D4", "answerable_text_only": True, "confidence": 0.9})),
        "m",
    )
    assert correct(task) is True   # matches ground truth (case-insensitive)
    assert wrong(task) is False    # confident but wrong → not a shortcut


def test_probe_flag_mode_without_ground_truth():
    from sciencevision_taskgen.probes import make_text_only_solver

    task = {"instruction": "q", "answer_schema": {}, "asset": {"name": "a"}, "ground_truth": {}}
    solver = make_text_only_solver(
        FakeProbeClient(json.dumps({"answer": "x", "answerable_text_only": True, "confidence": 0.5})),
        "m",
    )
    assert solver(task) is True


def test_probe_never_raises_on_bad_response():
    from sciencevision_taskgen.probes import make_text_only_solver

    task = {"instruction": "q", "answer_schema": {}, "asset": {"name": "a"}, "ground_truth": {"value": "z"}}
    solver = make_text_only_solver(FakeProbeClient("not json at all"), "m")
    assert solver(task) is False
