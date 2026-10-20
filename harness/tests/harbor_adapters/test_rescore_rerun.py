"""rescore_rerun: judge model is configurable + covers arbitrary baselines/layout.

The offline rescore is the only lever that actually changes the judge model
(the in-run verifier runs in a separate container that --ae can't reach). These
lock that (a) the default judge stays the working undated haiku alias and the
default baseline set stays the three-way, and (b) both the judge and the
job/tasks layout can be overridden so a six-baseline end2end run
(jobs-e2e/job-<label>) can be rescored with e.g. claude-opus-4-8.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.harbor.rescore_rerun as mod  # noqa: E402


def _make_trial(root: Path, jobs_rel: str, tasks_rel: str, task_id: str) -> Path:
    """Create a fake trial dir (+ task meta) under a tmp repo root."""
    trial = root / jobs_rel / "2026-07-30__00-00-00" / f"{task_id}__hash"
    (trial / "verifier").mkdir(parents=True)
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier" / "sci3d_scores.json").write_text(json.dumps({"task_id": task_id}))
    tdir = root / tasks_rel / task_id
    tdir.mkdir(parents=True)
    (tdir / "task.toml").write_text('[metadata]\nv_class = "V1"\n')
    (tdir / "instruction.md").write_text("do the thing")
    return trial


def test_default_judge_and_three_way_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    seen: dict[str, str] = {}

    def fake_score(meta, *, task_id, instruction, agent_dir, judge_model):
        seen["judge"] = judge_model
        return {}, {"reward": 0.5, "llm_judge": {"value": 0.5, "degraded": False}}

    monkeypatch.setattr(mod, "score_rewards", fake_score)

    _make_trial(tmp_path, "jobs-ev2-cc", "scripts/harbor/tasks-ev2-cc", "sv-chem-x-01")
    mod.main([])  # defaults: three-way, undated haiku alias
    assert seen["judge"] == "claude-haiku-4-5"


def test_judge_override_and_six_baseline_e2e_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_score(meta, *, task_id, instruction, agent_dir, judge_model):
        calls.append((task_id, judge_model))
        return {}, {"reward": 1.0, "llm_judge": {"value": 1.0, "degraded": False}}

    monkeypatch.setattr(mod, "score_rewards", fake_score)

    for b in ("cc", "cc2d-6"):
        _make_trial(tmp_path, f"jobs-e2e/job-{b}", f"jobs-e2e/tasks-{b}", "sv-medi-y-02")

    mod.main([
        "--judge", "claude-opus-4-8",
        "--baselines", "cc,cc2d-6",
        "--jobs-tmpl", "jobs-e2e/job-{b}/*",
        "--rerun-tmpl", "",
        "--tasks-tmpl", "jobs-e2e/tasks-{b}",
    ])
    assert len(calls) == 2
    assert all(j == "claude-opus-4-8" for _, j in calls)
    assert (tmp_path / "jobs-e2e/job-cc2d-6/2026-07-30__00-00-00/sv-medi-y-02__hash"
            / "verifier" / "sci3d_scores_rescored.json").exists()
