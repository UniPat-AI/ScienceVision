"""The EV2 matrix must not headline a number the gate refused to certify.

The six-baseline report is the artifact that gets pasted into decks, so a
footnote is not enough: the row's ``answer_score`` has to be the citable mean.
The concrete case is the medical column, where ``cc`` read the staged
ground-truth mask, rendered nothing, and scored 0.932.

``summarize_route`` is the whole aggregation, kept pure so it can be tested
without building a Harbor job tree.

Run:  python -m pytest tests/harbor_adapters/test_ev2_citability_headline.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor._citability import CITABLE_KEY  # noqa: E402
from scripts.harbor.report_v21_ev2 import (  # noqa: E402
    DOMAINS,
    summarize_route,
    trial_results,
)

_DOMAIN_OF = {"m1": "medical", "m2": "medical", "b1": "biology"}


def _trial(
    answer: float,
    *,
    citable: bool | None,
    abstained: bool = False,
    rendered: float | None = None,
    blockers: list[str] | None = None,
) -> dict:
    rewards: dict = {"answer_score": answer, "reward": answer}
    if rendered is not None:
        rewards["process_rendered"] = rendered
    if citable is not None:
        rewards[CITABLE_KEY] = 1.0 if citable else 0.0
    return {
        "clean": True,
        "exception": None,
        "rewards": rewards,
        "abstained": abstained,
        "blockers": blockers or [],
    }


def _route(clean: dict) -> dict:
    return summarize_route(clean, _DOMAIN_OF, DOMAINS)


def test_a_blocked_trial_cannot_move_the_headline():
    """0.93 by shortcut beside 0.10 by perception is 0.10, not 0.515."""
    row = _route({
        "m1": _trial(0.93, citable=False),
        "m2": _trial(0.10, citable=True),
    })
    assert row["answer_score"] == 0.10
    assert row["per_domain"]["medical"]["answer_score"] == 0.10


def test_the_unfiltered_number_survives_for_comparison():
    row = _route({
        "m1": _trial(0.93, citable=False),
        "m2": _trial(0.10, citable=True),
    })
    assert row["answer_score_all_eligible"] == 0.515
    assert row["per_domain_all_eligible"]["medical"]["answer_score"] == 0.515


def test_an_ungated_trial_is_excluded_and_counted_apart_from_blocked():
    """The EV2 matrix predates the gate, so its honest reading is "no citable
    trials" -- which must not be reported as a run where everything failed."""
    row = _route({
        "m1": _trial(0.93, citable=None),
        "m2": _trial(0.10, citable=True),
    })
    assert row["answer_score"] == 0.10
    assert row["citability"]["n_citable"] == 1
    assert row["citability"]["n_ungated"] == 1
    assert row["citability"]["n_blocked"] == 0
    assert "1 never gated" in row["citability"]["note"]


def test_a_fully_ungated_route_reports_no_headline_rather_than_the_raw_mean():
    row = _route({"m1": _trial(0.93, citable=None)})
    assert row["answer_score"] is None
    assert row["answer_score_all_eligible"] == 0.93
    assert row["scored_clean"] == 0
    assert row["scored_clean_all_eligible"] == 1


def test_the_denominator_counts_track_what_the_headline_rests_on():
    row = _route({
        "m1": _trial(0.5, citable=True),
        "m2": _trial(0.5, citable=False),
        "b1": _trial(0.5, citable=True),
    })
    assert row["scored_clean"] == 2
    assert row["scored_clean_all_eligible"] == 3
    assert row["citability"]["n_eligible"] == 3


def test_a_domain_with_only_excluded_trials_drops_out_of_the_headline():
    row = _route({
        "m1": _trial(0.9, citable=False),
        "b1": _trial(0.4, citable=True),
    })
    assert row["per_domain"]["medical"] == {"n": 0, "answer_score": None}
    assert row["per_domain"]["biology"]["answer_score"] == 0.4


def test_abstentions_are_counted_on_the_same_set_as_the_headline():
    """An abstention in an excluded trial would otherwise sit beside a score it
    did not contribute to, reading as a route that declined when it did not."""
    row = _route({
        "m1": _trial(0.9, citable=False, abstained=True),
        "m2": _trial(0.2, citable=True),
    })
    assert row["abstained"] == 0
    assert row["abstained_by_domain"]["medical"] == 0


def test_process_diagnostics_keep_the_excluded_runs_that_explain_them():
    """``cc``'s ``process_rendered = 0.00`` is *why* it was excluded. Filtering
    the diagnostics to the surviving runs would delete the evidence."""
    row = _route({"m1": _trial(0.9, citable=False, rendered=0.0)})
    assert row["answer_score"] is None, "the run is out of the headline"
    assert row["process"]["process_rendered"] == 0.0, "its evidence is not"


def test_the_blocked_trials_are_named_with_their_blocker():
    row = _route({
        "m1": _trial(0.9, citable=False, blockers=["capability_violation"]),
        "m2": _trial(0.4, citable=True),
    })
    assert row["blocked"] == [
        {"task_id": "m1", "blockers": ["capability_violation"]}
    ]


def test_an_empty_route_does_not_raise():
    row = _route({})
    assert row["answer_score"] is None
    assert row["scored_clean"] == 0
    assert row["citability"]["n_eligible"] == 0
    assert row["citability"]["note"] == "all 0 eligible trials are citable"


# -- 5. the verdict has two carriers, and the older one still counts ------- #
def _trial_dir(tmp_path: Path, task_id: str, *, rewards: dict, gate: dict | None):
    trial = tmp_path / "d1" / task_id
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": f"sci3d/{task_id}",
                "verifier_result": {"rewards": rewards},
            }
        )
    )
    if gate is not None:
        (trial / "verifier" / "sci3d_scores.json").write_text(
            json.dumps({"process_gate": gate})
        )
    return tmp_path


def test_a_blocked_trial_scored_before_this_change_is_recovered_from_the_audit(
    tmp_path: Path,
):
    """The gate wrote its verdict into the audit from 2026-08-26 and into the
    reward channel only now. Reading rewards alone would call every trial from
    that window *ungated*, which is a different -- and false -- statement."""
    root = _trial_dir(
        tmp_path,
        "t1",
        rewards={"answer_score": 1.0, "reward": 1.0},
        gate={"citable": False, "blockers": ["capability_violation"]},
    )
    (task_id, _exc, rewards, _blank, blockers) = next(trial_results(root))
    assert task_id == "t1"
    assert rewards[CITABLE_KEY] == 0.0
    assert blockers == ["capability_violation"]


def test_the_reward_channel_wins_when_both_carriers_are_present(tmp_path: Path):
    """The audit is a fallback for a missing key, never an override."""
    root = _trial_dir(
        tmp_path,
        "t1",
        rewards={"answer_score": 1.0, "reward": 1.0, CITABLE_KEY: 1.0},
        gate={"citable": False, "blockers": ["capability_violation"]},
    )
    (_task_id, _exc, rewards, _blank, _blockers) = next(trial_results(root))
    assert rewards[CITABLE_KEY] == 1.0


def test_a_trial_with_neither_carrier_stays_ungated(tmp_path: Path):
    root = _trial_dir(
        tmp_path, "t1", rewards={"answer_score": 1.0, "reward": 1.0}, gate=None
    )
    (_task_id, _exc, rewards, _blank, blockers) = next(trial_results(root))
    assert CITABLE_KEY not in rewards
    assert blockers == []


def test_the_audit_fallback_does_not_mutate_the_result_file(tmp_path: Path):
    """``rewards`` is copied: an injected verdict must not be written back."""
    root = _trial_dir(
        tmp_path,
        "t1",
        rewards={"answer_score": 1.0, "reward": 1.0},
        gate={"citable": True, "blockers": []},
    )
    next(trial_results(root))
    on_disk = json.loads(
        (root / "d1" / "t1" / "result.json").read_text()
    )["verifier_result"]["rewards"]
    assert CITABLE_KEY not in on_disk
