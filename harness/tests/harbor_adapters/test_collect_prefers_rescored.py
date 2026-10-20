"""Legacy LLM rescoring is shadow-only and cannot replace answer_score."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor.collect_qagen_results import collect_one  # noqa: E402


def _write_result(trial_dir: Path) -> None:
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / "agent").mkdir(exist_ok=True)
    (trial_dir / "verifier").mkdir(exist_ok=True)
    result = {
        "task_name": "sv-fake-task-01",
        "verifier_result": {
            "rewards": {
                "reward": 0.6,
                "answer_score": 0.6,
                "process_compliance": 1.0,
            }
        },
    }
    (trial_dir / "result.json").write_text(json.dumps(result))


def test_llm_rescore_is_shadow_only(tmp_path):
    trial_dir = tmp_path / "sv-fake-task-01__abc"
    _write_result(trial_dir)
    rescored = {
        "reward": 0.83,
        "llm_judge": {"value": 0.9, "degraded": False},
        "process_judge": {"value": 0.7},
        "tool_names": ["render_view"],
    }
    (trial_dir / "verifier" / "sci3d_scores_rescored.json").write_text(
        json.dumps(rescored)
    )

    run = collect_one(trial_dir, index={}, model="m", harness="h")

    assert run is not None
    assert run["rewards"]["reward"] == 0.6
    assert run["rewards"]["answer_score"] == 0.6
    assert run["rewards"]["shadow_llm_judge"] == 0.9
    assert run["rewards"]["shadow_process_judge"] == 0.7
    assert run["status"] == "ok"


def test_falls_back_to_in_run_rewards_without_rescored_file(tmp_path):
    trial_dir = tmp_path / "sv-fake-task-02__def"
    _write_result(trial_dir)
    # No verifier/sci3d_scores_rescored.json written.

    run = collect_one(trial_dir, index={}, model="m", harness="h")

    assert run is not None
    assert run["rewards"]["reward"] == 0.6
    assert run["rewards"]["answer_score"] == 0.6
    assert run["status"] == "ok"


def test_deterministic_rescore_can_replace_answer_score(tmp_path):
    trial_dir = tmp_path / "sv-fake-task-03__ghi"
    _write_result(trial_dir)
    rescored = {
        "reward": 1.0,
        "answer_score": {"value": 1.0},
    }
    (trial_dir / "verifier" / "sci3d_scores_rescored.json").write_text(
        json.dumps(rescored)
    )

    run = collect_one(trial_dir, index={}, model="m", harness="h")

    assert run["rewards"]["answer_score"] == 1.0
    assert run["rewards"]["reward"] == 1.0


def test_unparseable_rescored_file_falls_back(tmp_path):
    trial_dir = tmp_path / "sv-fake-task-04__jkl"
    _write_result(trial_dir)
    (trial_dir / "verifier" / "sci3d_scores_rescored.json").write_text("{not json")

    run = collect_one(trial_dir, index={}, model="m", harness="h")

    assert run is not None
    assert run["rewards"]["reward"] == 0.6
