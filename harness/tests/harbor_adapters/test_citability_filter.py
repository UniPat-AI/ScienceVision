"""The process gate's verdict must remove runs from the headline, not annotate them.

``citable`` was reported as a rate beside an unfiltered mean, which is a column,
not a gate: a ``cc`` run that read the staged ground-truth mask scored 0.932 and
entered the leaderboard anyway. These tests pin the three states apart and pin
the headline to the certified set.

The direction that matters most is the last one: a run the gate never scored
(archived before the gate existed) must not be reported as *blocked*. It is
excluded either way, but a report that merges "we looked and it failed" with
"we never looked" cannot tell a hardened baseline from an unexamined one.

Run:  python -m pytest tests/harbor_adapters/test_citability_filter.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor._citability import (  # noqa: E402
    CITABLE_KEY,
    Census,
    citability,
    headline_rows,
)
from scripts.harbor.collect_qagen_results import (  # noqa: E402
    _leaderboard_md,
    build_leaderboard,
)


# -- 1. the tri-state, and the direction of the default ------------------- #
def test_absent_verdict_is_ungated_not_blocked():
    assert citability({}) is None
    assert citability({"answer_score": 0.9}) is None
    assert citability(None) is None


def test_a_fired_blocker_is_false_and_a_clean_run_is_true():
    assert citability({CITABLE_KEY: 0.0}) is False
    assert citability({CITABLE_KEY: 1.0}) is True


def test_the_verdict_survives_a_truthy_non_bool_payload():
    """Reward dicts are JSON round-tripped; never rely on ``is True``."""
    assert citability({CITABLE_KEY: 1}) is True
    assert citability({CITABLE_KEY: 0}) is False


# -- 2. the split, and the census that has to travel with it -------------- #
def test_headline_rows_keeps_only_certified_rows():
    rows = [{"v": True}, {"v": False}, {"v": None}, {"v": True}]
    kept, census = headline_rows(rows, lambda r: r["v"])
    assert [r["v"] for r in kept] == [True, True]
    assert census == Census(citable=2, blocked=1, ungated=1)


def test_the_census_separates_never_gated_from_blocked():
    """The whole point of the third bucket: these are different facts."""
    _, census = headline_rows([{"v": False}, {"v": None}], lambda r: r["v"])
    assert census.blocked == 1
    assert census.ungated == 1
    assert census.eligible == 2
    assert census.citable == 0


def test_the_census_note_names_what_was_dropped_and_why():
    blocked_note = Census(citable=3, blocked=2, ungated=0).note()
    assert "3/5" in blocked_note and "2 blocked" in blocked_note

    ungated_note = Census(citable=3, blocked=0, ungated=2).note()
    assert "2 never gated" in ungated_note

    assert Census(citable=4, blocked=0, ungated=0).note() == (
        "all 4 eligible trials are citable"
    )


def test_an_empty_eligible_set_reports_a_zero_census_not_an_error():
    kept, census = headline_rows([], lambda r: True)
    assert kept == []
    assert census.as_dict() == {
        "n_eligible": 0,
        "n_citable": 0,
        "n_blocked": 0,
        "n_ungated": 0,
    }


# -- 3. the leaderboard actually uses it ---------------------------------- #
def _run(harness: str, *, task_id: str, domain: str = "medical", answer: float,
         citable: bool | None, eligible: bool = True) -> dict:
    rewards: dict = {"reward": answer, "answer_score": answer}
    if citable is not None:
        rewards[CITABLE_KEY] = 1.0 if citable else 0.0
    return {
        "task_id": task_id,
        "set": "s1",
        "domain": domain,
        "v_class": "V1",
        "harness": harness,
        "model": "m",
        "status": "ok",
        "evaluation_eligible": eligible,
        "rewards": rewards,
        "process_gate": (
            {} if citable is None else {"citable": citable, "blockers": [] if citable else ["capability_violation"]}
        ),
    }


def _board(tmp_path: Path, runs: list[dict]) -> dict:
    return build_leaderboard(runs, tmp_path)


def test_a_blocked_run_cannot_move_the_headline(tmp_path: Path):
    """The regression this change exists for: 1.0 by shortcut beside 0.0 by
    perception must not average to 0.5."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=1.0, citable=False),
        _run("cc", task_id="t2", answer=0.0, citable=True),
    ])
    assert board["by_harness_model_mean_reward"]["cc / m"] == 0.0


def test_the_unfiltered_mean_is_kept_beside_the_headline(tmp_path: Path):
    """Dropping the old number entirely would make the filter unauditable."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=1.0, citable=False),
        _run("cc", task_id="t2", answer=0.0, citable=True),
    ])
    assert board["by_harness_model_mean_reward_all_eligible"]["cc / m"] == 0.5


def test_an_ungated_run_is_excluded_from_the_headline_and_counted(tmp_path: Path):
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=1.0, citable=None),
        _run("cc", task_id="t2", answer=0.0, citable=True),
    ])
    assert board["by_harness_model_mean_reward"]["cc / m"] == 0.0
    census = board["citability"]
    assert census["n_citable"] == 1
    assert census["n_ungated"] == 1
    assert census["n_blocked"] == 0


def test_an_ineligible_run_is_out_before_citability_is_consulted(tmp_path: Path):
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=1.0, citable=True, eligible=False),
        _run("cc", task_id="t2", answer=0.25, citable=True),
    ])
    assert board["by_harness_model_mean_reward"]["cc / m"] == 0.25
    assert board["citability"]["n_eligible"] == 1


def test_a_fully_ungated_board_yields_no_headline_rather_than_a_wrong_one(tmp_path: Path):
    """A run produced before the gate existed has no citable trials. That is the
    honest answer to "may I cite this?", and it must not fall back to the
    unfiltered mean."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=0.93, citable=None),
    ])
    assert board["by_harness_model_mean_reward"] == {}
    assert board["by_harness_model_mean_reward_all_eligible"]["cc / m"] == 0.93
    assert board["citability"]["n_ungated"] == 1


def test_the_per_domain_headline_is_filtered_too(tmp_path: Path):
    board = _board(tmp_path, [
        _run("cc", task_id="t1", domain="medical", answer=1.0, citable=False),
        _run("cc", task_id="t2", domain="medical", answer=0.0, citable=True),
        _run("cc", task_id="t3", domain="biology", answer=0.8, citable=True),
    ])
    assert board["by_harness_domain_mean_reward"]["cc / medical"] == 0.0
    assert board["by_harness_domain_mean_reward_all_eligible"]["cc / medical"] == 0.5
    assert board["by_harness_domain_mean_reward"]["cc / biology"] == 0.8


def test_the_citable_rate_still_denominates_on_the_gated_runs(tmp_path: Path):
    """Filtering the headline must not turn the rate into a constant 1.0 -- it
    is the number that says how much of the run the headline spoke for."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=1.0, citable=False),
        _run("cc", task_id="t2", answer=1.0, citable=True),
        _run("cc", task_id="t3", answer=1.0, citable=None),
    ])
    assert board["by_harness_model_citable_rate"]["cc / m"] == 0.5


# -- 4. the rendered report has to disclose the denominator --------------- #
def test_the_markdown_states_what_the_filter_dropped(tmp_path: Path):
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=1.0, citable=False),
        _run("cc", task_id="t2", answer=0.0, citable=True),
    ])
    md = _leaderboard_md(board)
    assert "citable" in md.lower()
    assert "1 blocked" in md


def test_the_markdown_warns_when_nothing_could_be_certified(tmp_path: Path):
    board = _board(tmp_path, [_run("cc", task_id="t1", answer=0.93, citable=None)])
    md = _leaderboard_md(board)
    assert "never gated" in md


def test_the_markdown_gives_every_row_its_own_denominator(tmp_path: Path):
    """A global census cannot tell a row resting on 12 of 12 trials from one
    resting on 1 of 12 -- the filtered reward looks the same in both."""
    board = _board(tmp_path, [
        _run("cc", task_id="t1", answer=0.9, citable=True),
        _run("cc", task_id="t2", answer=0.9, citable=False),
        _run("cc", task_id="t3", answer=0.9, citable=False),
        _run("cc", task_id="t4", answer=0.9, citable=None),
    ])
    md = _leaderboard_md(board)
    assert "| cc / m | 0.900 | 0.900 | 1/4 | 2 | 1 |" in md, md
