from __future__ import annotations

import json

from scripts.harbor.report_sample50 import load_trials


def _trial(tmp_path, audit: dict) -> None:
    trial = tmp_path / "run" / "sv-medi-example"
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({
        "task_name": "sci3d/sv-medi-example",
        "verifier_result": {"rewards": {"reward": 1.0, "answer_score": 1.0}},
    }))
    (trial / "verifier" / "sci3d_scores.json").write_text(json.dumps(audit))


def test_missing_explicit_eligibility_fails_closed(tmp_path):
    _trial(tmp_path, {"answer_score": {"raw": {"eligible": True}}})
    assert load_trials(tmp_path)[0]["evaluation_eligible"] is False


def test_explicit_eligibility_is_included(tmp_path):
    _trial(tmp_path, {"evaluation_eligible": True})
    assert load_trials(tmp_path)[0]["evaluation_eligible"] is True
