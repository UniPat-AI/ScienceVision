"""Ensure route eligibility changes the formal row, not the diagnostic answer."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor_adapters.sci3d import _score_core as core  # noqa: E402
from harbor_adapters.sci3d._route_contracts import (  # noqa: E402
    build_route_contract,
    canonical_catalog,
    write_health_receipt,
)


def _surface(*names: str) -> dict:
    raw = {
        "tools": [{"name": name, "schema_sha256": f"schema:{name}"} for name in names],
        "viewer_family": None,
    }
    canonical = canonical_catalog(raw)
    assert canonical is not None
    raw.update(
        {
            key: canonical[key]
            for key in ("tool_names", "tool_count", "catalog_sha256", "schema_sha256")
        }
    )
    return raw


@pytest.fixture
def deterministic_answer_score(monkeypatch):
    """Avoid coupling route tests to every science-domain scorer implementation."""
    fake_spec = SimpleNamespace(scorer=SimpleNamespace(primary="exact_match", secondary=()))
    fake_result = SimpleNamespace(
        value=1.0,
        metric="answer_score",
        degraded=False,
        raw={"eligible": True},
    )
    fake_scorer = SimpleNamespace(name="answer_score", score=lambda _task, _view: fake_result)
    monkeypatch.setattr(core, "task_metadata_to_taskspec", lambda *_args, **_kwargs: fake_spec)
    monkeypatch.setattr(core, "build_scorers", lambda *_args, **_kwargs: [fake_scorer])


def _write(agent_dir: Path, *steps: dict) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / core.ANSWER_FILE).write_text(json.dumps({"answer": "diagnostic"}))
    (agent_dir / core.TRAJECTORY_FILE).write_text(json.dumps({"steps": list(steps)}))


def _step(call_id: str, name: str, observation: dict) -> dict:
    return {
        "step_id": call_id,
        "tool_calls": [{"tool_call_id": call_id, "function_name": name, "arguments": {}}],
        "observation": {
            "results": [{"source_call_id": call_id, "content": json.dumps(observation)}]
        },
    }


def _meta(contract: dict) -> dict:
    return {
        "domain": "test",
        "answer_schema": {"kind": "freeform"},
        "ground_truth": {"value": "x"},
        "scorer": {"primary": "exact_match", "secondary": []},
        "weights": {"answer": 1.0, "process": 0.0},
        "route_contract": contract,
    }


def test_missing_health_is_infra_invalid_not_a_model_zero(
    tmp_path: Path, deterministic_answer_score
):
    surface = _surface("sv3d_open_scene")
    contract = build_route_contract("ccmcp", catalog=surface)
    _write(tmp_path, _step("one", "mcp__sciencevision__sv3d_open_scene", {"ok": True}))

    rewards, audit = core.score_rewards(
        _meta(contract), task_id="t", instruction="q", agent_dir=tmp_path
    )
    assert "answer_score" not in rewards
    assert rewards["reward"] == 0.0
    assert audit["route_evaluation_status"] == "infra_invalid"
    assert audit["formal_reward_valid"] is False
    assert audit["aggregate_excluded"] is True
    assert audit["diagnostic_answer_score"]["value"] == 1.0
    assert "missing_health_receipt" in audit["route_eligibility"]["reason_codes"]


def test_failed_required_render_is_agent_noncompliance(
    tmp_path: Path, deterministic_answer_score
):
    surface = _surface("sv3d_open_scene", "sv3d_render")
    contract = build_route_contract(
        "ccmcp",
        allowed_tools=("sv3d_open_scene", "sv3d_render"),
        required_tool_groups=(("sv3d_open_scene",),),
        min_successful_calls=1,
        render_required=True,
        catalog=surface,
    )
    write_health_receipt(
        tmp_path,
        contract,
        registered_servers=["sciencevision"],
        server_surface=surface,
        exposed_tools=surface["tool_names"],
        exposed_schemas=surface["tools"],
    )
    _write(
        tmp_path,
        _step("open", "mcp__sciencevision__sv3d_open_scene", {"ok": True}),
        _step("render", "mcp__sciencevision__sv3d_render", {"renderer_skipped": "no GL"}),
    )

    rewards, audit = core.score_rewards(
        _meta(contract), task_id="t", instruction="q", agent_dir=tmp_path
    )
    assert rewards["answer_score"] == 0.0
    assert rewards["reward"] == 0.0
    assert audit["route_evaluation_status"] == "agent_noncompliant"
    assert audit["formal_reward_valid"] is True
    assert audit["evaluation_eligible"] is True
    assert audit["aggregate_excluded"] is False
    assert audit["diagnostic_answer_score"]["value"] == 1.0
    assert "required_render_not_successful" in audit["route_eligibility"]["reason_codes"]
