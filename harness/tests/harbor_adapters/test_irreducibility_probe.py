"""Tests for the T0 irreducibility probe (``scripts/harbor/_irreducibility.py``).

T0 asks, per task: *could this have been answered without perceiving the 3D
content?* Two probe routes answer it —

    codex-text  question prose only, no file / image / viewer / MCP
    cc (codex)  the raw 3D file plus Bash, no MCP, no renders

— and the task gets one label: ``text-leaked`` (prose alone sufficed),
``file-reducible`` (file computation sufficed), or ``discriminative`` (neither
shortcut worked). Only the third is citable.

This matters because of a measured result: on the 11 scored ``med_ct_geometry``
tasks, ``cc`` scored 0.932 with ``process_rendered = 0.00`` — it never looked at
an image, it ran ``scipy.ndimage.center_of_mass`` on the staged GT mask. The
same tasks carry the largest cc-vs-cc2d gap in the EV2 leaderboard, which is
exactly the gap a shortcut manufactures.

Run:  python -m pytest tests/harbor_adapters/test_irreducibility_probe.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts._qa_qc_shortcuts import (  # noqa: E402
    RAW_COMPUTE_BASELINES,
    TEXT_PROBE_BASELINES,
    attach_probe_results,
)
from scripts.harbor._irreducibility import (  # noqa: E402
    IRREDUCIBILITY_SCHEMA_VERSION,
    LABEL_DISCRIMINATIVE,
    LABEL_FILE_REDUCIBLE,
    LABEL_TEXT_LEAKED,
    LABEL_UNKNOWN,
    effective_score,
    label_task,
    summarize_labels,
)
from scripts.harbor.probe_irreducibility import build_per_task, main as probe_main  # noqa: E402


def _cell(reward=None, content=None, source="probe"):
    return {
        "effective_reward": reward,
        "effective_content_reward": content,
        "effective_source": source,
    }


# ── 1. score selection prefers the deterministic content reward ──────────── #
def test_content_reward_wins_over_the_weighted_total():
    """answer_score is the only number on the leaderboard; the weighted total
    folds in process signals that are explicitly diagnostic-only."""
    score, kind = effective_score(_cell(reward=0.2, content=0.9))
    assert (score, kind) == (0.9, "content_reward")


def test_weighted_reward_is_the_fallback_when_no_content_reward_exists():
    assert effective_score(_cell(reward=0.7)) == (0.7, "weighted_reward")


def test_a_missing_cell_yields_no_score():
    assert effective_score(None) == (None, "missing")
    assert effective_score(_cell()) == (None, "missing")


# ── 2. the three labels ──────────────────────────────────────────────────── #
def test_text_probe_above_threshold_is_text_leaked():
    got = label_task(text_cell=_cell(content=0.8), file_cell=_cell(content=1.0))
    assert got["irreducibility"] == LABEL_TEXT_LEAKED


def test_text_leaked_wins_even_when_every_route_succeeds():
    """Prose sufficing is the most damaging finding; it is not diluted by the
    3D routes also succeeding."""
    got = label_task(
        text_cell=_cell(content=0.9),
        file_cell=_cell(content=1.0),
        perception_cell=_cell(content=1.0),
    )
    assert got["irreducibility"] == LABEL_TEXT_LEAKED


def test_file_computation_alone_is_file_reducible():
    """The med_ct_geometry shape: text fails, raw-file compute answers."""
    got = label_task(text_cell=_cell(content=0.0), file_cell=_cell(content=0.93))
    assert got["irreducibility"] == LABEL_FILE_REDUCIBLE
    assert got["file_score"] == 0.93


def test_neither_shortcut_route_succeeding_is_discriminative():
    got = label_task(text_cell=_cell(content=0.0), file_cell=_cell(content=0.1))
    assert got["irreducibility"] == LABEL_DISCRIMINATIVE


def test_threshold_is_inclusive_and_configurable():
    assert label_task(
        text_cell=_cell(content=0.5), file_cell=_cell(content=0.0)
    )["irreducibility"] == LABEL_TEXT_LEAKED
    assert label_task(
        text_cell=_cell(content=0.5), file_cell=_cell(content=0.0), threshold=0.6
    )["irreducibility"] == LABEL_DISCRIMINATIVE


# ── 3. a missing probe is never a clean bill of health ───────────────────── #
def test_a_missing_text_probe_is_unknown_not_discriminative():
    """Without the text probe we have no evidence about prose leakage. Calling
    that 'discriminative' would assert a fact we never measured."""
    got = label_task(text_cell=None, file_cell=_cell(content=0.1))
    assert got["irreducibility"] == LABEL_UNKNOWN
    assert "text" in got["reason"]


def test_a_missing_file_probe_is_unknown_not_discriminative():
    got = label_task(text_cell=_cell(content=0.0), file_cell=None)
    assert got["irreducibility"] == LABEL_UNKNOWN
    assert "file" in got["reason"]


# ── 4. 'discriminative' is not the same as 'anyone solved it' ───────────── #
def test_perception_route_success_is_recorded_separately_from_the_label():
    """A task no route solves labels 'discriminative' under the doc's taxonomy,
    but it is not evidence of a good task — keep the two facts distinct."""
    solved = label_task(
        text_cell=_cell(content=0.0),
        file_cell=_cell(content=0.0),
        perception_cell=_cell(content=0.9),
    )
    unsolved = label_task(
        text_cell=_cell(content=0.0),
        file_cell=_cell(content=0.0),
        perception_cell=_cell(content=0.0),
    )
    assert solved["irreducibility"] == unsolved["irreducibility"] == LABEL_DISCRIMINATIVE
    assert solved["solved_by_perception_route"] is True
    assert unsolved["solved_by_perception_route"] is False


def test_no_perception_probe_leaves_the_flag_undetermined():
    got = label_task(text_cell=_cell(content=0.0), file_cell=_cell(content=0.0))
    assert got["solved_by_perception_route"] is None


# ── 5. roll-up ───────────────────────────────────────────────────────────── #
def test_summarize_counts_by_label_and_domain():
    per_task = {
        "t1": {"domain": "medical", "labels": label_task(
            text_cell=_cell(content=0.0), file_cell=_cell(content=0.95))},
        "t2": {"domain": "medical", "labels": label_task(
            text_cell=_cell(content=0.0), file_cell=_cell(content=0.0),
            perception_cell=_cell(content=0.8))},
        "t3": {"domain": "biology", "labels": label_task(
            text_cell=_cell(content=0.9), file_cell=_cell(content=0.9))},
    }
    got = summarize_labels(per_task)
    assert got["n"] == 3
    assert got["by_label"][LABEL_FILE_REDUCIBLE] == 1
    assert got["by_label"][LABEL_TEXT_LEAKED] == 1
    assert got["by_label"][LABEL_DISCRIMINATIVE] == 1
    assert got["by_domain"]["medical"][LABEL_FILE_REDUCIBLE] == 1
    assert got["by_domain"]["biology"][LABEL_TEXT_LEAKED] == 1
    assert got["n_citable"] == 1


def test_summarize_flags_discriminative_tasks_no_route_solved():
    per_task = {
        "t1": {"domain": "medical", "labels": label_task(
            text_cell=_cell(content=0.0), file_cell=_cell(content=0.0),
            perception_cell=_cell(content=0.0))},
    }
    got = summarize_labels(per_task)
    assert got["by_label"][LABEL_DISCRIMINATIVE] == 1
    assert got["n_discriminative_unsolved"] == 1
    assert got["n_citable"] == 1, "the label is still discriminative"


def test_summarize_reports_unknown_rather_than_hiding_it():
    per_task = {"t1": {"domain": "chemistry", "labels": label_task(
        text_cell=None, file_cell=_cell(content=0.0))}}
    got = summarize_labels(per_task)
    assert got["n_unknown"] == 1
    assert got["n_citable"] == 0


def test_schema_version_is_declared():
    assert IRREDUCIBILITY_SCHEMA_VERSION.startswith("sciencevision.irreducibility.")


# ── 6. the QC consumer must actually see the probe route names ───────────── #
def test_route_names_the_probe_emits_are_recognised_by_the_qc_gate():
    """The route label comes from the job dir (``job-codex-text`` ->
    ``codex-text``) via qa_qc._baseline_from_path. If that name is not in the
    alias set, attach_probe_results falls through both branches and silently
    attaches nothing — a probe that reports 'clean' having checked nothing."""
    assert "codex-text" in TEXT_PROBE_BASELINES
    assert "codex" in RAW_COMPUTE_BASELINES
    assert "cc" in RAW_COMPUTE_BASELINES


def test_attach_probe_results_fires_on_the_codex_text_route_name():
    result = {
        "task_id": "t1",
        "findings": [],
        "shortcuts": {
            "controls": {"must_be_derived_from_3d_interaction": True},
            "routes": {},
            "probe_results": {},
        },
    }
    attach_probe_results(result, {"codex-text": _cell(content=0.8)}, 0.5)
    codes = [f["code"] for f in result["findings"]]
    assert "text_knowledge_shortcut" in codes
    assert result["shortcuts"]["probe_results"]["codex-text"]["kind"] == "text_only"


# ── 7. run ingestion ─────────────────────────────────────────────────────── #
def _harbor_trial(job: Path, task_id: str, answer_score: float) -> None:
    """Minimal trial matching what qa_qc.load_harbor_jobs reads."""
    trial = job / task_id
    (trial / "agent").mkdir(parents=True, exist_ok=True)
    (trial / "result.json").write_text(
        json.dumps({
            "task_name": f"tasks/{task_id}",
            "verifier_result": {"rewards": {"reward": answer_score,
                                            "answer_score": answer_score}},
        })
    )


def test_build_per_task_pairs_the_two_probe_jobs(tmp_path: Path):
    text_job, file_job = tmp_path / "job-codex-text", tmp_path / "job-cc"
    _harbor_trial(text_job, "sv-med-0001", 0.0)
    _harbor_trial(file_job, "sv-med-0001", 0.95)
    _harbor_trial(text_job, "sv-bio-0002", 0.9)
    _harbor_trial(file_job, "sv-bio-0002", 0.9)

    per_task = build_per_task(text_job, file_job, None, {}, 0.5)
    assert per_task["sv-med-0001"]["labels"]["irreducibility"] == LABEL_FILE_REDUCIBLE
    assert per_task["sv-bio-0002"]["labels"]["irreducibility"] == LABEL_TEXT_LEAKED
    # The QC gate is fed straight from this cell, so it must carry its keys.
    cell = per_task["sv-med-0001"]["baseline_summary"]["codex-text"]
    assert set(cell) >= {"effective_reward", "effective_content_reward", "effective_source"}


def test_build_per_task_keeps_a_task_present_in_only_one_probe(tmp_path: Path):
    """The id union, not the intersection: a task dropped for want of a pair
    vanishes from the citable-set denominator without leaving a trace.

    The two halves are deliberately asymmetric. A task the file probe solved is
    *measurably* file-reducible even with the text probe missing — a measured
    shortcut condemns on its own. A task neither probe could condemn is
    'unknown', because 'discriminative' would assert something never measured.
    """
    text_job, file_job = tmp_path / "job-codex-text", tmp_path / "job-cc"
    _harbor_trial(text_job, "sv-med-0001", 0.0)   # file probe never ran
    _harbor_trial(file_job, "sv-med-0002", 0.9)   # text probe never ran
    per_task = build_per_task(text_job, file_job, None, {}, 0.5)
    assert set(per_task) == {"sv-med-0001", "sv-med-0002"}
    assert per_task["sv-med-0001"]["labels"]["irreducibility"] == LABEL_UNKNOWN
    assert per_task["sv-med-0002"]["labels"]["irreducibility"] == LABEL_FILE_REDUCIBLE
    assert "text probe missing" in per_task["sv-med-0002"]["labels"]["reason"]


def test_main_writes_the_probe_artifact(tmp_path: Path, monkeypatch):
    text_job, file_job = tmp_path / "job-codex-text", tmp_path / "job-cc"
    _harbor_trial(text_job, "sv-med-0001", 0.0)
    _harbor_trial(file_job, "sv-med-0001", 0.95)
    out = tmp_path / "reports" / "irreducibility.json"

    monkeypatch.setattr(sys, "argv", [
        "probe_irreducibility", "--text-run", str(text_job), "--file-run", str(file_job),
        "--tasks-root", str(tmp_path / "no-such-root"), "--out", str(out),
    ])
    probe_main()

    payload = json.loads(out.read_text())
    assert payload["schema_version"] == IRREDUCIBILITY_SCHEMA_VERSION
    assert payload["success_threshold"] == 0.5
    assert payload["summary"]["by_label"][LABEL_FILE_REDUCIBLE] == 1
    assert payload["per_task"]["sv-med-0001"]["domain"] == "unknown"


def test_main_refuses_a_missing_probe_job(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "probe_irreducibility", "--text-run", str(tmp_path / "nope"),
        "--file-run", str(tmp_path / "also-nope"), "--out", str(tmp_path / "o.json"),
    ])
    with pytest.raises(SystemExit):
        probe_main()
