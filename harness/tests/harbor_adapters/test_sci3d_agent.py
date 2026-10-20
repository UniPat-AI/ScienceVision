"""Tests for the Harbor sci3d adapter.

Run in the py3.12 harbor venv (Harbor requires >=3.12):

    .venv-harbor/bin/python -m pytest tests/harbor_adapters/ -q

No network, no GL/SAM: the sci3d loop runs via SCI3D_USE_FAKE_LLM=1 + FakeKernel.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable (sci3d_agent + harbor_adapters).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor.models.agent.context import AgentContext  # noqa: E402

from harbor_adapters.sci3d._instruction import parse_instruction  # noqa: E402
from harbor_adapters.sci3d._translate import (  # noqa: E402
    record_to_context_fields,
    record_to_trajectory,
    step_to_harbor,
)
from harbor_adapters.sci3d.agent import Sci3dAgent  # noqa: E402


# ── instruction parsing ────────────────────────────────────────────────── #


def test_parse_asset_line():
    p = parse_instruction("Question: How many lobes?\nAsset: /data/lung.glb")
    assert p.asset_path == "/data/lung.glb"
    assert p.question == "How many lobes?"


def test_parse_sci3d_block():
    instr = 'Count the chambers.\n```sci3d\n{"asset_path": "/d/heart.glb"}\n```'
    p = parse_instruction(instr)
    assert p.asset_path == "/d/heart.glb"
    assert "chambers" in p.question
    assert "```" not in p.question


def test_parse_fallback_asset():
    p = parse_instruction("Just a plain question.", fallback_asset="/d/x.glb")
    assert p.asset_path == "/d/x.glb"
    assert p.question == "Just a plain question."


def test_parse_block_overrides_fallback():
    p = parse_instruction(
        'q\n```sci3d\n{"asset_path": "/block.glb"}\n```',
        fallback_asset="/fallback.glb",
    )
    assert p.asset_path == "/block.glb"


# ── translation (pure) ──────────────────────────────────────────────────── #


class _FakeStep:
    def __init__(self, tool, args, result, reasoning=None, iter=0):
        self.tool = tool
        self.args = args
        self.result = result
        self.reasoning = reasoning
        self.iter = iter
        self.latency_ms = 5
        self.images = []
        self.tool_use_id = None


class _FakeMeta:
    model = "claude-haiku-4-5-20251001"
    seed = 0
    cost_estimate_usd = 0.01
    token_counts = {"agent_input": 12, "agent_output": 7}


class _FakeRecord:
    def __init__(self, steps, final_answer="42"):
        self.steps = steps
        self.final_answer = final_answer
        self.timeout = False
        self.error = None
        self.violations = []
        self.mode = "mode1_no_vision"
        self.task_id = "/data/heart.glb"
        self.meta = _FakeMeta()


def test_step_to_harbor_with_tool():
    step = _FakeStep("execute_code", {"code": "print(1)"}, {"stdout": "1"}, "thinking", 3)
    hs = step_to_harbor(step, 3)
    assert hs.step_id == 4  # 1-indexed
    assert hs.source == "agent"
    assert hs.tool_calls[0].function_name == "execute_code"
    assert hs.tool_calls[0].arguments == {"code": "print(1)"}
    assert hs.observation.results[0].content  # serialized dict
    assert hs.reasoning_content == "thinking"


def test_step_to_harbor_no_tool_call():
    step = _FakeStep("<no_tool_call>", {}, {"text": "hmm"}, "just thinking")
    hs = step_to_harbor(step, 0)
    assert hs.tool_calls == []
    assert hs.observation is None
    assert hs.message == "just thinking"


def test_record_to_trajectory():
    rec = _FakeRecord([_FakeStep("finish", {"answer": "42"}, {"answer": "42"})])
    traj = record_to_trajectory(rec, session_id="sess-1", agent_version="9.9.9")
    assert traj.session_id == "sess-1"
    assert traj.agent.name == "sci3d"
    assert traj.agent.version == "9.9.9"
    assert traj.agent.model_name == "claude-haiku-4-5-20251001"
    assert len(traj.steps) == 1
    assert traj.final_metrics.total_prompt_tokens == 12
    assert traj.final_metrics.total_completion_tokens == 7
    assert traj.final_metrics.total_steps == 1
    assert traj.final_metrics.extra["final_answer"] == "42"
    # round-trips through pydantic JSON
    json.loads(traj.model_dump_json())


def test_record_to_context_fields():
    rec = _FakeRecord([])
    f = record_to_context_fields(rec)
    assert f["n_input_tokens"] == 12
    assert f["n_output_tokens"] == 7
    assert f["cost_usd"] == 0.01


# ── end-to-end run() via fake LLM ───────────────────────────────────────── #


def test_run_populates_context_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("SCI3D_USE_FAKE_LLM", "1")
    logs = tmp_path / "logs"
    agent = Sci3dAgent(
        logs_dir=logs,
        model_name="anthropic/claude-haiku-4-5-20251001",
        mode="mode1_no_vision",
        max_iters=3,
        asset_path="/data/heart.glb",
    )
    assert agent.name() == "sci3d"
    assert agent.version() is not None

    ctx = AgentContext()
    instruction = "Question: How many chambers?\nAsset: /data/heart.glb"

    asyncio.run(agent.run(instruction, environment=None, context=ctx))

    # context populated
    assert ctx.metadata is not None
    assert ctx.metadata["sci3d_final_answer"] == "fake"
    assert ctx.metadata["sci3d_timeout"] is False
    assert ctx.n_input_tokens is not None

    # artifacts written
    answer = json.loads((logs / "sci3d_answer.json").read_text())
    assert answer["answer"] == "fake"
    traj = json.loads((logs / "sci3d_trajectory.json").read_text())
    assert traj["agent"]["name"] == "sci3d"
    assert traj["final_metrics"]["extra"]["final_answer"] == "fake"


def test_setup_is_noop():
    agent = Sci3dAgent(logs_dir=Path("/tmp/sci3d-x"))
    asyncio.run(agent.setup(environment=None))  # must not raise
