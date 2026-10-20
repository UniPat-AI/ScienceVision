"""The process gate must be readable off the leaderboard, not only the audit.

A signal that lives solely in ``verifier/sci3d_scores.json`` is a signal nobody
consults: the leaderboard is what gets read, pasted into reports, and cited. So
the four ``process_*`` keys and the citability verdict surface as columns here.

What the columns must get right, and why each has a test:

  * ``mcp_success_rate`` is averaged over the routes that *attempted* MCP only.
    Including ``cc`` (no MCP by construction, rate 0.0) in the denominator would
    drag every mean toward zero and make a healthy ``ccmcp`` look broken -- the
    same class of mistake as the 0-attempt route being credited with 1.0.
  * A run scored before this change has no such keys. It must render as "—",
    never as 0.0: absent and zero mean opposite things, and a zero would read as
    "checked, and it failed".
  * ``citable`` is a tri-state -- True, False, or unknown for an unscored run.

Run:  python -m pytest tests/harbor_adapters/test_leaderboard_process_columns.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor.collect_qagen_results import (  # noqa: E402
    _leaderboard_md,
    build_leaderboard,
)


def _run(
    harness: str,
    *,
    task_id: str = "t1",
    rewards: dict | None = None,
    gate: dict | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "set": "s1",
        "domain": "medical",
        "v_class": "V1",
        "harness": harness,
        "model": "m",
        "status": "ok",
        "evaluation_eligible": True,
        "rewards": rewards if rewards is not None else {"reward": 1.0, "answer_score": 1.0},
        "process_gate": gate or {},
    }


def _rewards(**over) -> dict:
    base = {
        "reward": 1.0,
        "answer_score": 1.0,
        "process_compliance": 1.0,
        "process_capability_policy": 1.0,
        "process_capability_coverage": 1.0,
        "process_mcp_attempted": 1.0,
        "process_mcp_success_rate": 1.0,
    }
    return {**base, **over}


def _board(tmp_path: Path, runs: list[dict]) -> dict:
    return build_leaderboard(runs, tmp_path)


# -- 1. the columns exist and carry the audit's verdict -------------------- #
def test_the_process_gate_columns_are_on_every_row(tmp_path: Path):
    board = _board(tmp_path, [_run("ccmcp", rewards=_rewards())])
    row = board["rows"][0]
    for key in ("capability_policy", "capability_coverage",
                "mcp_attempted", "mcp_success_rate", "citable"):
        assert key in row, key


def test_a_capability_violation_shows_as_not_citable_with_its_blocker(tmp_path: Path):
    board = _board(tmp_path, [_run(
        "cc",
        rewards=_rewards(process_capability_policy=0.0),
        gate={"citable": False, "blockers": ["capability_violation"], "notes": []},
    )])
    row = board["rows"][0]
    assert row["citable"] is False
    assert row["blockers"] == ["capability_violation"]
    assert row["capability_policy"] == 0.0


def test_an_unscored_legacy_run_renders_as_unknown_not_as_a_failure(tmp_path: Path):
    """Runs archived before this change carry none of the keys. Absent must not
    become 0.0 -- that would read as "we checked and it failed"."""
    board = _board(tmp_path, [_run("cc", rewards={"reward": 0.9, "answer_score": 0.9})])
    row = board["rows"][0]
    assert row["capability_policy"] is None
    assert row["mcp_success_rate"] is None
    assert row["citable"] is None
    assert "—" in _leaderboard_md(board)


# -- 2. aggregation: the denominators that make the means honest ----------- #
def test_the_mcp_success_mean_covers_only_routes_that_attempted_mcp(tmp_path: Path):
    """``cc`` never calls MCP, so its 0.0 rate is not a failure to average in."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1",
             rewards=_rewards(process_mcp_attempted=0.0, process_mcp_success_rate=0.0)),
        _run("ccmcp", task_id="t2",
             rewards=_rewards(process_mcp_attempted=1.0, process_mcp_success_rate=1.0)),
    ])
    means = board["by_harness_model_mean_mcp_success"]
    assert "ccmcp / m" in means
    assert means["ccmcp / m"] == 1.0
    assert "cc / m" not in means, "a route that never called MCP has no rate to report"


def test_the_citable_rate_is_a_fraction_of_the_runs_actually_gated(tmp_path: Path):
    board = _board(tmp_path, [
        _run("cc", task_id="t1", rewards=_rewards(process_capability_policy=0.0),
             gate={"citable": False, "blockers": ["capability_violation"]}),
        _run("cc", task_id="t2", rewards=_rewards(), gate={"citable": True, "blockers": []}),
        # scored before the gate existed -> not part of either count
        _run("cc", task_id="t3", rewards={"reward": 1.0, "answer_score": 1.0}),
    ])
    rates = board["by_harness_model_citable_rate"]
    assert rates["cc / m"] == 0.5


def test_capability_coverage_is_averaged_so_an_unenforceable_policy_is_visible(tmp_path: Path):
    """The number that stops "citable" being over-read: if coverage is low, the
    gate checked little, however clean the verdict looks."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1", rewards=_rewards(process_capability_coverage=0.0),
             gate={"citable": True, "blockers": []}),
        _run("cc", task_id="t2", rewards=_rewards(process_capability_coverage=1.0),
             gate={"citable": True, "blockers": []}),
    ])
    assert board["by_harness_model_mean_capability_coverage"]["cc / m"] == 0.5


# -- 3. the rendered markdown ---------------------------------------------- #
def test_the_markdown_names_the_new_columns(tmp_path: Path):
    board = _board(tmp_path, [_run("ccmcp", rewards=_rewards(),
                                   gate={"citable": True, "blockers": []})])
    md = _leaderboard_md(board)
    assert "citable" in md.lower()
    assert "mcp" in md.lower()
    assert "coverage" in md.lower()


def test_the_markdown_row_count_still_matches_the_board(tmp_path: Path):
    """Guard against a header/row column-count mismatch silently shifting every
    value one cell left in the rendered table."""
    board = _board(tmp_path, [
        _run("cc", task_id=f"t{i}", rewards=_rewards(), gate={"citable": True, "blockers": []})
        for i in range(3)
    ])
    md = _leaderboard_md(board)
    per_task = md.split("## Per-task", 1)[1]
    rows = [ln for ln in per_task.splitlines() if ln.startswith("|")]
    header, sep = rows[0], rows[1]
    assert header.count("|") == sep.count("|")
    for line in rows[2:]:
        assert line.count("|") == header.count("|"), line


def test_the_board_is_written_to_disk_as_both_json_and_md(tmp_path: Path):
    _board(tmp_path, [_run("ccmcp", rewards=_rewards(), gate={"citable": True})])
    board = json.loads((tmp_path / "runs_leaderboard.json").read_text())
    assert board["rows"][0]["citable"] is True
    assert (tmp_path / "runs_leaderboard.md").exists()


def test_building_the_board_does_not_mutate_the_runs(tmp_path: Path):
    runs = [_run("ccmcp", rewards=_rewards(), gate={"citable": True, "blockers": []})]
    snapshot = json.loads(json.dumps(runs))
    _board(tmp_path, runs)
    assert runs == snapshot
