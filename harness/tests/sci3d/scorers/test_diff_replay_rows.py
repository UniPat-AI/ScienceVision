"""Two replays of one corpus, compared verdict by verdict.

The tool exists for the one question a loosening rule has to answer: does
anything that used to pass now fail? That flip is the failure mode a score
mean cannot show -- a mean can rise while a correct answer becomes wrong --
so ``regressions`` is separated from ``moved`` rather than folded into a delta,
and the CLI's exit code is derived from it so the check can be a gate.

A verdict that left the rubric is deliberately **not** a regression: it cannot
fail, because nothing compares it any more. It is reported under ``vanished``
so that it is still visible rather than traded away silently.
"""
import json
from pathlib import Path

import pytest

from tools.diff_replay_rows import compare


def _row(task_id, arm, verdicts, status="scored"):
    return {"task_id": task_id, "arm": arm, "status": status,
            "new_verdicts": [dict(v) for v in verdicts]}


def _v(fid, passed):
    return {"id": fid, "comparator": "canonical_string_equal", "weight": 1.0,
            "required": False, "passed": passed,
            "reason": "pass" if passed else "comparison_failed", "rule": None}


# ── the flips, and the one that must never be quiet ──────────────────────── #

def test_regression_is_reported_not_swallowed():
    old = [_row("t", "a", [_v("f", True)])]
    new = [_row("t", "a", [_v("f", False)])]
    diff = compare(old, new)
    assert diff["regressions"], "a pass -> fail flip must never be silent"
    assert diff["moved"] == []


def test_a_loosening_flip_lands_in_moved():
    old = [_row("t", "a", [_v("f", False)])]
    new = [_row("t", "a", [_v("f", True)])]
    diff = compare(old, new)
    assert diff["regressions"] == []
    assert diff["moved"] == [
        {"task_id": "t", "arm": "a", "field": "f", "old_passed": False,
         "new_passed": True}
    ]


def test_a_verdict_that_left_the_rubric_is_not_a_regression():
    old = [_row("t", "a", [_v("f", True)])]
    new = [_row("t", "a", [])]
    diff = compare(old, new)
    assert diff["regressions"] == []
    assert diff["vanished"] == [{"task_id": "t", "arm": "a", "field": "f"}]


def test_skipped_rows_are_not_compared():
    old = [{"task_id": "t", "arm": "a", "status": "skipped", "new_verdicts": []}]
    new = [{"task_id": "t", "arm": "a", "status": "skipped", "new_verdicts": []}]
    assert compare(old, new)["identical"] == 0


# ── the other three quadrants ────────────────────────────────────────────── #

def test_an_unchanged_verdict_is_counted_not_listed():
    old = [_row("t", "a", [_v("f", True), _v("g", False)])]
    new = [_row("t", "a", [_v("f", True), _v("g", False)])]
    diff = compare(old, new)
    assert (diff["moved"], diff["regressions"], diff["vanished"],
            diff["appeared"]) == ([], [], [], [])
    assert diff["identical"] == 2


def test_a_verdict_that_entered_the_rubric_is_reported_as_appeared():
    old = [_row("t", "a", [])]
    new = [_row("t", "a", [_v("f", True)])]
    diff = compare(old, new)
    assert diff["appeared"] == [{"task_id": "t", "arm": "a", "field": "f"}]
    assert diff["identical"] == 0
    assert diff["moved"] == [], "an appearance is not a flip: nothing moved"


def test_a_trial_that_only_one_run_scored_is_reported_field_by_field():
    # A trial that scored in the old run and could not be scored in the new one
    # has no verdict to compare. Its fields vanish, and that is the honest
    # report: the trial dropped out of the denominator, which is not the same
    # as its answers having got worse.
    old = [_row("t", "a", [_v("f", True)])]
    new = [{"task_id": "t", "arm": "a", "status": "skipped", "new_verdicts": []}]
    diff = compare(old, new)
    assert diff["regressions"] == []
    assert diff["vanished"] == [{"task_id": "t", "arm": "a", "field": "f"}]


def test_a_trial_only_one_run_has_at_all_is_not_a_crash():
    diff = compare([_row("t", "a", [_v("f", True)])],
                   [_row("t", "b", [_v("f", True)])])
    assert diff["vanished"] == [{"task_id": "t", "arm": "a", "field": "f"}]
    assert diff["appeared"] == [{"task_id": "t", "arm": "b", "field": "f"}]
    assert diff["identical"] == 0


def test_the_same_field_on_two_arms_is_two_decisions():
    old = [_row("t", "cc", [_v("f", False)]), _row("t", "ccmcp", [_v("f", True)])]
    new = [_row("t", "cc", [_v("f", True)]), _row("t", "ccmcp", [_v("f", False)])]
    diff = compare(old, new)
    assert diff["moved"] == [
        {"task_id": "t", "arm": "cc", "field": "f", "old_passed": False,
         "new_passed": True}
    ]
    assert diff["regressions"] == [
        {"task_id": "t", "arm": "ccmcp", "field": "f", "old_passed": True,
         "new_passed": False}
    ]


def test_the_lists_are_ordered_so_two_runs_of_this_tool_agree():
    old = [_row("t2", "cc", [_v("f", False)]), _row("t1", "cc", [_v("f", False)])]
    new = [_row("t2", "cc", [_v("f", True)]), _row("t1", "cc", [_v("f", True)])]
    assert [m["task_id"] for m in compare(old, new)["moved"]] == ["t1", "t2"]


# ── the three ways this comparison could swallow its own subject ─────────── #

def test_a_duplicate_trial_key_is_an_error_not_a_last_write_wins():
    # Two rows for one (task_id, arm) cannot both be the trial. Folding them
    # into a dict keeps the later one, so a flip recorded by the earlier row
    # disappears -- and it disappears as ``identical``, which is the count a
    # reader uses to conclude that nothing happened.
    old = [_row("t", "a", [_v("f", True)]), _row("t", "a", [_v("f", False)])]
    new = [_row("t", "a", [_v("f", False)]), _row("t", "a", [_v("f", False)])]
    with pytest.raises(ValueError, match="duplicate"):
        compare(old, new)


def test_a_duplicate_verdict_id_within_a_row_is_an_error():
    old = [_row("t", "a", [_v("f", True), _v("f", False)])]
    with pytest.raises(ValueError, match="duplicate"):
        compare(old, old)


# ── the CLI, which is the gate ───────────────────────────────────────────── #

def _write(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return path


def test_the_cli_exits_nonzero_when_a_verdict_regressed(tmp_path, capsys):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", True)])])
    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [_v("f", False)])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 1
    document = json.loads(out.read_text())
    assert document["regressions"] == [
        {"task_id": "t", "arm": "a", "field": "f", "old_passed": True,
         "new_passed": False}
    ]
    assert "t" in capsys.readouterr().out


def test_the_cli_exits_zero_and_writes_the_document_on_a_clean_loosening(tmp_path):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", False)])])
    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [_v("f", True)])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 0
    document = json.loads(out.read_text())
    assert sorted(document) == ["appeared", "identical", "moved", "regressions",
                                "runs_roots", "scorer_roots", "vanished"]
    assert document["moved"] == [
        {"task_id": "t", "arm": "a", "field": "f", "old_passed": False,
         "new_passed": True}
    ]


def test_the_cli_refuses_a_duplicate_trial_key_instead_of_reading_past_it(
    tmp_path, capsys
):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl",
                 [_row("t", "a", [_v("f", True)]), _row("t", "a", [_v("f", False)])])
    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [_v("f", False)])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists()
    assert "duplicate" in capsys.readouterr().err.lower()


# ── the gate fails closed on a membership change as well as on a flip ────── #

def test_the_cli_exits_nonzero_when_a_verdict_left_the_rubric(tmp_path, capsys):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", True)])])
    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 1
    assert "vanished" in capsys.readouterr().err.lower()


def test_the_membership_refusal_is_opt_out_able(tmp_path):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", True)])])
    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out),
                 "--allow-membership-change"]) == 0
    assert json.loads(out.read_text())["vanished"] == [
        {"task_id": "t", "arm": "a", "field": "f"}
    ]


def test_a_truncated_replay_fails_the_gate_rather_than_passing_as_a_difference(
    tmp_path,
):
    # The shape this refuses: ``--new`` cut short, so every verdict is
    # ``vanished`` and the run reports no regressions. Visible in the counts
    # and invisible to ``&&`` -- which is the one place it matters.
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", True)])])
    new = _write(tmp_path / "new.jsonl", [])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 1


# ── the document has to carry its own inputs ─────────────────────────────── #

def test_the_document_records_which_checkout_each_side_was_scored_by(tmp_path):
    from tools.diff_replay_rows import main

    old_rows = [_row("t", "a", [_v("f", False)])]
    new_rows = [_row("t", "a", [_v("f", True)])]
    for row, root in ((old_rows[0], "/checkouts/before"), (new_rows[0], "/checkouts/after")):
        row["scorer_root"] = root
        row["runs_root"] = "/evidence/delivery"
    old = _write(tmp_path / "old.jsonl", old_rows)
    new = _write(tmp_path / "new.jsonl", new_rows)
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 0
    document = json.loads(out.read_text())
    assert document["scorer_roots"] == {"old": ["/checkouts/before"],
                                        "new": ["/checkouts/after"]}
    assert document["runs_roots"] == {"old": ["/evidence/delivery"],
                                      "new": ["/evidence/delivery"]}


def test_the_cli_refuses_to_write_inside_the_delivery_the_rows_came_from(
    tmp_path, capsys
):
    from tools.diff_replay_rows import main

    delivery = tmp_path / "delivery" / "v11-250"
    delivery.mkdir(parents=True)
    rows = [_row("t", "a", [_v("f", True)])]
    rows[0]["runs_root"] = str(delivery)
    old = _write(tmp_path / "old.jsonl", rows)
    new = _write(tmp_path / "new.jsonl", rows)
    out = delivery / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists()
    assert "delivery" in capsys.readouterr().err.lower()


def test_a_row_with_no_recorded_root_is_still_comparable(tmp_path):
    # Files written before the rows carried provenance must not become
    # unreadable; the guard simply has nothing to guard.
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", False)])])
    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [_v("f", True)])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 0
    document = json.loads(out.read_text())
    assert document["scorer_roots"] == {"old": [], "new": []}


# ── the switch check belongs in the tool, not in a shell step beside it ──── #

def _stamped(root: str, passed: bool = True) -> list[dict]:
    rows = [_row("t", "a", [_v("f", passed)])]
    rows[0]["scorer_root"] = root
    return rows


def test_the_comparison_refuses_to_compare_one_revision_with_itself(
    tmp_path, capsys
):
    """The failure this catches measures nothing and reports a clean bill.

    A replay run whose ``--scorer-root`` was ignored stamps the *same* root on
    both sides and finds nothing moved -- which reads as "the change is null"
    when the truth is "the switch never happened". Nothing else in the pipeline
    sees it: the verdicts really are identical, because they came from one
    revision. The rows carry the roots, so the tool can refuse.
    """
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", _stamped("/checkouts/only"))
    new = _write(tmp_path / "new.jsonl", _stamped("/checkouts/only"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists(), "a vacuous comparison must not leave a document"
    assert "same checkout" in capsys.readouterr().err.lower()


def test_the_same_root_refusal_is_opt_out_able(tmp_path):
    # Diffing two runs of one revision is legitimate -- it is how a refactor is
    # shown to have changed nothing -- but it has to be said out loud.
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", _stamped("/checkouts/only"))
    new = _write(tmp_path / "new.jsonl", _stamped("/checkouts/only"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out),
                 "--allow-same-root"]) == 0
    document = json.loads(out.read_text())
    assert document["identical"] == 1 and document["moved"] == []


def test_two_different_roots_are_not_refused(tmp_path):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", _stamped("/checkouts/before", passed=False))
    new = _write(tmp_path / "new.jsonl", _stamped("/checkouts/after"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 0


# ── the corpus half of the same premise ──────────────────────────────────── #

def _read(root: str, passed: bool = True) -> list[dict]:
    row = _row("t", "a", [_v("f", passed)])
    row["runs_root"] = root
    return [row]


def test_the_comparison_refuses_two_sides_read_from_different_deliveries(
    tmp_path, capsys
):
    """The mirror of the switch check, and the one that moves the other way.

    A verdict is keyed by ``(task_id, arm, field)``, so two deliveries that
    hold the same tasks under the same arms produce the same keys and different
    *answers*. Both sides are then real replays, every verdict is a real
    comparison, and the flips the diff reports are the models' run-to-run
    variance rather than the change -- reported with the authority of a
    measurement. The membership check sees a corpus that kept its tasks, so
    nothing else catches it, and ``v12_rescore_old.jsonl`` diffed against
    ``v12_rescore_new.jsonl`` is exactly this pair.
    """
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", _read("/evidence/v11-250", passed=False))
    new = _write(tmp_path / "new.jsonl", _read("/evidence/v12-250"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists(), "a comparison across two corpora must not leave a document"
    message = capsys.readouterr().err.lower()
    assert "same delivery" in message
    assert "/evidence/v11-250" in message and "/evidence/v12-250" in message


def test_the_different_runs_refusal_is_opt_out_able(tmp_path):
    # Comparing two deliveries is a real question ("did this corpus change?"),
    # so the refusal is a speed bump rather than a wall -- but it has to be
    # said out loud, because the same flags also produce the accidental case.
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", _read("/evidence/v11-250", passed=False))
    new = _write(tmp_path / "new.jsonl", _read("/evidence/v12-250"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out),
                 "--allow-different-runs"]) == 0
    document = json.loads(out.read_text())
    assert document["runs_roots"] == {"old": ["/evidence/v11-250"],
                                      "new": ["/evidence/v12-250"]}


def test_one_delivery_on_both_sides_is_not_refused(tmp_path):
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", _read("/evidence/v11-250", passed=False))
    new = _write(tmp_path / "new.jsonl", _read("/evidence/v11-250"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["moved"] == [
        {"task_id": "t", "arm": "a", "field": "f", "old_passed": False,
         "new_passed": True}
    ]


def test_a_side_that_names_no_delivery_does_not_pass_the_check(tmp_path, capsys):
    # Stricter than the checkout half on purpose. The pair this guards against
    # often has one side written before the rows carried provenance -- an
    # un-stamped before-replay against a new after-replay is the shape -- so a
    # missing root cannot be read as agreement. Both sides naming none is still
    # a match, so such files stay readable against each other.
    from tools.diff_replay_rows import main

    old = _write(tmp_path / "old.jsonl", [_row("t", "a", [_v("f", False)])])
    new = _write(tmp_path / "new.jsonl", _read("/evidence/v11-250"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists()
    message = capsys.readouterr().err.lower()
    assert "no root recorded" in message and "/evidence/v11-250" in message
    # The both-nameless case is the existing
    # ``test_a_row_with_no_recorded_root_is_still_comparable``: equal sets,
    # nothing refused.


def test_a_file_that_mixes_deliveries_is_refused_against_a_single_one(tmp_path):
    # A concatenation of two deliveries is not one corpus either; the guard
    # compares the sets, not the first value.
    from tools.diff_replay_rows import main

    from_v11 = _read("/evidence/v11-250")
    from_v12 = _read("/evidence/v12-250")
    from_v12[0]["task_id"] = "t2"
    old = _write(tmp_path / "old.jsonl", from_v11 + from_v12)
    new = _write(tmp_path / "new.jsonl", _read("/evidence/v11-250"))
    out = tmp_path / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists()


# ── the guard has to read the row's root the way the writer wrote it ─────── #

def test_the_delivery_guard_resolves_the_root_the_rows_carry(tmp_path, capsys):
    # ``replay_corpus`` stamps a resolved root, so this is unreachable through
    # this tool's own output -- but the guard should not depend on that, and a
    # root spelled with a symlink is the spelling that defeats a raw compare.
    from tools.diff_replay_rows import main

    real = tmp_path / "real" / "delivery"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    rows = [_row("t", "a", [_v("f", True)])]
    rows[0]["runs_root"] = str(link)          # same directory, other spelling
    old = _write(tmp_path / "old.jsonl", rows)
    new = _write(tmp_path / "new.jsonl", rows)
    out = real / "delta.json"
    assert main(["--old", str(old), "--new", str(new), "--out", str(out)]) == 2
    assert not out.exists()
    assert "delivery" in capsys.readouterr().err.lower()


def test_a_missing_input_is_reported_as_an_input_error(tmp_path, capsys):
    # Documented as exit 2, so it has to be exit 2 rather than a traceback out
    # of ``read_text``. Both fail closed; a traceback does not say which input.
    from tools.diff_replay_rows import main

    new = _write(tmp_path / "new.jsonl", [_row("t", "a", [_v("f", True)])])
    out = tmp_path / "delta.json"
    assert main(["--old", str(tmp_path / "absent.jsonl"), "--new", str(new),
                 "--out", str(out)]) == 2
    assert not out.exists()
    assert "absent.jsonl" in capsys.readouterr().err
