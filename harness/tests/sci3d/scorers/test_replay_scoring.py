"""The replay must never invent a score for an input it cannot score.

Every fixture here is a miniature of the real delivery: a stored audit, a
harbor-shaped trajectory, and a ground-truth document. The tests that matter
most are the ones where a plausible implementation would quietly produce a
number -- a missing audit, a comparator nobody implemented, a rubric id the
ground truth no longer carries.
"""
import json
import sys
from pathlib import Path

import pytest

from tools.replay_scoring import replay_one


def write_run(root: Path, verdicts, *, gt, stored=0.5, task_id="t1", answer=None,
              required_gate=False):
    """One trial that looks exactly like a stored one, in miniature."""
    run = root / "runs" / "cc"
    (run / "verifier").mkdir(parents=True)
    (run / "agent").mkdir(parents=True)
    (run / "verifier" / "sci3d_scores.json").write_text(json.dumps({
        "task_id": task_id,
        "answer_score": {"value": stored, "raw": {
            "eligible": True, "required_gate": required_gate,
            "ungated_score": stored, "parse_error": None, "verdicts": verdicts,
        }},
    }))
    message = answer if answer is not None else json.dumps(gt)
    (run / "agent" / "trajectory.json").write_text(json.dumps({
        "schema_version": "atif.v1",
        "steps": [
            {"step_id": "0", "source": "agent", "message": "thinking out loud"},
            {"step_id": "1", "source": "agent",
             "message": f"SCI3D_FINAL_JSON: {message}"},
        ],
    }))
    return run


def verdict(item_id, comparator, *, passed=False, weight=1.0, required=True,
            reason="comparison_failed"):
    return {"id": item_id, "passed": passed, "required": required, "reason": reason,
            "comparator": comparator, "weight": weight}


# ── the three cases from the brief ───────────────────────────────────────── #

def test_a_run_without_an_audit_is_skipped_not_guessed(tmp_path):
    run = tmp_path / "runs" / "cc"
    run.mkdir(parents=True)
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1", arm="cc")
    assert result["status"] == "skipped"
    assert result["new_score"] is None
    assert "audit" in result["reason"].lower()


def test_an_unknown_comparator_skips_the_trial(tmp_path):
    # A comparator the replay does not implement must be reported, never
    # silently treated as a pass or a fail.
    run = write_run(
        tmp_path,
        [verdict("answer", "mystery_equal")],
        gt={"answer": 20},
    )
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1", arm="cc")
    assert result["status"] == "skipped"
    assert "mystery_equal" in result["reason"]


def test_the_delivery_tree_is_never_written_to(tmp_path):
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal")],
        gt={"answer": 20},
    )
    audit = run / "verifier" / "sci3d_scores.json"
    before = (audit.stat().st_mtime_ns, audit.read_bytes())
    replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1", arm="cc")
    after = (audit.stat().st_mtime_ns, audit.read_bytes())
    assert before == after


# ── reproduction is the load-bearing claim ───────────────────────────────── #

def test_a_stored_score_that_matches_the_old_rules_reproduces(tmp_path):
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal", passed=True, reason="pass"),
         verdict("other", "integer_equal")],
        gt={"answer": 20, "other": 5},
        stored=0.5,
        answer='{"answer": 20, "other": 9}',
    )
    result = replay_one(run_dir=run, ground_truth={"answer": 20, "other": 5},
                        task_id="t1", arm="cc", rules="old")
    assert result["status"] == "scored"
    assert result["new_score"] == pytest.approx(0.5)
    assert result["old_score"] == pytest.approx(0.5)
    assert result["reproduced"] is True


def test_a_stored_score_the_old_rules_do_not_produce_is_flagged(tmp_path):
    # The whole measurement rests on this flag discriminating. A trial whose
    # stored score cannot be re-derived must say so rather than report a
    # delta computed against a number it never checked.
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal", passed=True, reason="pass")],
        gt={"answer": 20},
        stored=0.25,
    )
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1",
                        arm="cc", rules="old")
    assert result["new_score"] == pytest.approx(1.0)
    assert result["old_score"] == pytest.approx(0.25)
    assert result["reproduced"] is False


def test_a_mismatch_line_prints_the_column_the_flag_compared(tmp_path):
    """The reported difference has to be the one ``reproduced`` decided.

    ``reproduced`` compares the audit's number against the old-rule
    recomputation, so a MISMATCH line that prints the active rule set's score
    shows a gap between two *different* rule sets on every row the F1/F2/F3
    retarget moves. The two columns coincide on the delivery this was measured
    on, which is why nothing noticed; this row is the one where they do not.
    """
    from tools.replay_scoring import _summarize

    gt = {"damage_depth_mm": "20"}
    run = write_run(
        tmp_path,
        [verdict("damage_depth_mm", "canonical_string_equal")],
        gt=gt,
        stored=0.5,
        answer='{"damage_depth_mm": 20}',
    )
    row = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert row["old_rule_score"] == 0.0
    assert row["new_score"] == pytest.approx(1.0)
    assert row["reproduced"] is False
    text = _summarize([row], "new")
    assert "stored=0.5 computed=0.0" in text
    assert "computed=1.0" not in text


def test_the_required_gate_is_applied_exactly_as_the_audit_records_it(tmp_path):
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal", passed=True, reason="pass"),
         verdict("other", "integer_equal")],
        gt={"answer": 20, "other": 5},
        stored=0.0,
        required_gate=True,
        answer='{"answer": 20, "other": 9}',
    )
    result = replay_one(run_dir=run, ground_truth={"answer": 20, "other": 5},
                        task_id="t1", arm="cc", rules="old")
    assert result["new_score"] == 0.0
    assert result["reproduced"] is True


# ── the answer comes from the trajectory, read by the harness's own reader ── #

def test_the_answer_is_recovered_from_the_last_agent_step(tmp_path):
    run = write_run(
        tmp_path,
        [verdict("total_panel_count", "integer_equal", passed=True, reason="pass")],
        gt={"total_panel_count": 33},
        stored=1.0,
        answer='{"total_panel_count": 33}',
    )
    result = replay_one(run_dir=run, ground_truth={"total_panel_count": 33},
                        task_id="t1", arm="cc", rules="old")
    assert result["status"] == "scored"
    assert result["new_score"] == pytest.approx(1.0)


def test_an_answer_file_wins_over_the_trajectory(tmp_path):
    # ``sci3d_answer.json`` is the sci3d harness's own file; when it exists the
    # trajectory's final message is not the answer. (No such file exists in the
    # delivery -- every stored trial answered through the trajectory -- so this
    # pins the precedence the harness's reader defines.)
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal", passed=True, reason="pass")],
        gt={"answer": 20},
        stored=1.0,
        answer='{"answer": 1}',
    )
    (run / "agent" / "sci3d_answer.json").write_text(
        json.dumps({"answer": {"answer": 20}})
    )
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1",
                        arm="cc", rules="old")
    assert result["status"] == "scored"
    assert result["new_score"] == pytest.approx(1.0)


def test_a_trajectory_with_no_answer_skips_rather_than_scores_zero(tmp_path):
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal")],
        gt={"answer": 20},
        stored=0.0,
        answer="",
    )
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1",
                        arm="cc", rules="old")
    assert result["status"] == "skipped"
    assert "recover" in result["reason"].lower()


def test_an_unreadable_trajectory_skips_rather_than_raising(tmp_path):
    run = write_run(
        tmp_path,
        [verdict("answer", "integer_equal")],
        gt={"answer": 20},
    )
    (run / "agent" / "trajectory.json").write_text("{not json")
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1",
                        arm="cc", rules="old")
    assert result["status"] == "skipped"


# ── the two inputs a trial can be missing ────────────────────────────────── #

def test_an_audit_with_no_rubric_is_skipped_with_its_reason_named(tmp_path):
    run = tmp_path / "runs" / "cc"
    (run / "verifier").mkdir(parents=True)
    (run / "agent").mkdir(parents=True)
    (run / "verifier" / "sci3d_scores.json").write_text(json.dumps({
        "task_id": "t1",
        "answer_score": {"value": 0.0, "raw": {
            "eligible": False, "reason": "invalid_task_contract",
            "errors": ["missing_tolerance:point_vox"]}},
    }))
    (run / "agent" / "trajectory.json").write_text(json.dumps({"steps": []}))
    result = replay_one(run_dir=run, ground_truth={"answer": 20}, task_id="t1", arm="cc")
    assert result["status"] == "skipped"
    assert "invalid_task_contract" in result["reason"]


def test_a_rubric_id_the_ground_truth_does_not_carry_skips_named(tmp_path):
    # sv-chem-chem_conformation-0028 and gear-Model07-QA03 in the real delivery:
    # the audit was written against a contract revision the bundle no longer
    # holds. Scoring them against a different ground truth would be invention.
    run = write_run(
        tmp_path,
        [verdict("farthest_heavy_atom_distance_A", "abs_tolerance", passed=True,
                 reason="pass")],
        gt={"maximum_heavy_atom_distance_A": 4.068},
        stored=1.0,
    )
    result = replay_one(run_dir=run,
                        ground_truth={"maximum_heavy_atom_distance_A": 4.068},
                        task_id="t1", arm="cc", rules="old")
    assert result["status"] == "skipped"
    assert "farthest_heavy_atom_distance_A" in result["reason"]


# ── the rules the task is measuring ──────────────────────────────────────── #

def test_f1_a_numeric_string_ground_truth_takes_the_numeric_path(tmp_path):
    # sv-arch-exp-03 stores damage_depth_mm as the string "20"; an agent that
    # answers the number 20 scored zero under the old rules.
    gt = {"damage_depth_mm": "20"}
    run = write_run(
        tmp_path,
        [verdict("damage_depth_mm", "canonical_string_equal")],
        gt=gt,
        stored=0.0,
        answer='{"damage_depth_mm": 20}',
    )
    old = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="old")
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert old["new_score"] == 0.0
    assert new["new_score"] == pytest.approx(1.0)
    assert new["old_score"] == 0.0 and new["reproduced"] is True
    assert new["new_verdicts"][0]["rule"] == "F1"


def test_f1_a_numeric_string_still_compares_exactly_not_within_a_band(tmp_path):
    gt = {"total_panel_count": "4"}
    run = write_run(
        tmp_path,
        [verdict("total_panel_count", "canonical_string_equal")],
        gt=gt,
        stored=0.0,
        answer='{"total_panel_count": 5}',
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["new_score"] == 0.0


def test_f3_a_choice_letter_is_not_derived_from_the_option_text(tmp_path):
    # A leading letter is ambiguous ("D. rotates ..." vs "E. coli O157:H7"), so
    # it is authored in ``accepted`` rather than derived; F3 must not fire.
    gt = {"answer": "D. rotates counter-clockwise by 90 degrees"}
    run = write_run(
        tmp_path,
        [verdict("answer", "canonical_string_equal")],
        gt=gt,
        stored=0.0,
        answer='{"answer": "D"}',
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["new_score"] == pytest.approx(0.0)


def test_f2_a_prose_explanation_field_leaves_the_rubric(tmp_path):
    # The field was a required item, so dropping it raises the fraction and
    # removes it from the required-gate set. Three fields, not two: the
    # refusal is one survivor, so a two-field task keeps its prose and only a
    # task left with two real items can lose one.
    gt = {"placement": "protein_cleft_or_pocket",
          "region": "cleft",
          "evidence": "The orange non-polymer component is nestled within a groove "
                      "formed by surrounding green protein helices on several sides."}
    run = write_run(
        tmp_path,
        [verdict("placement", "canonical_string_equal", passed=True, reason="pass"),
         verdict("region", "canonical_string_equal", passed=True, reason="pass"),
         verdict("evidence", "canonical_string_equal")],
        gt=gt,
        stored=2 / 3,
        answer='{"placement": "protein_cleft_or_pocket", "region": "cleft", '
               '"evidence": "no"}',
    )
    old = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="old")
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert old["new_score"] == pytest.approx(2 / 3)
    assert old["reproduced"] is True
    assert new["new_score"] == pytest.approx(1.0)
    assert new["report_only"] == ["evidence"]
    assert {v["id"] for v in new["new_verdicts"]} == {"placement", "region"}


def test_f2_a_two_field_task_keeps_its_prose_because_one_item_would_be_left(
    tmp_path,
):
    """The same shape as the test above, minus the third field. The replay
    must refuse here too, or it reports a gain the scorer does not make."""
    gt = {"placement": "protein_cleft_or_pocket",
          "evidence": "The orange non-polymer component is nestled within a groove "
                      "formed by surrounding green protein helices on several sides."}
    run = write_run(
        tmp_path,
        [verdict("placement", "canonical_string_equal", passed=True, reason="pass"),
         verdict("evidence", "canonical_string_equal")],
        gt=gt,
        stored=0.5,
        answer='{"placement": "protein_cleft_or_pocket", "evidence": "no"}',
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["new_score"] == pytest.approx(0.5)
    assert new["report_only"] == []
    assert {v["id"] for v in new["new_verdicts"]} == {"placement", "evidence"}


def test_f2_never_empties_a_rubric_covering_only_part_of_the_ground_truth():
    # The refusal has two halves. ``select_report_only`` refuses when every
    # ground-truth field is prose; the compiler-level half refuses when the
    # rubric covers a strict subset and the drop would empty *it*. An explicit
    # rubric can be that subset, so the second half is reachable even though the
    # implicit rubric the replay rebuilds makes the two coincide.
    from tools.replay_scoring import _build_new_items

    ground_truth = {
        "placement": "protein_cleft_or_pocket",
        "evidence": "The orange non-polymer component is nestled within a groove "
                    "formed by surrounding green protein helices on several sides.",
    }
    items = [{"id": "evidence", "pred_path": "evidence", "gt_path": "evidence",
              "comparator": "canonical_string_equal", "weight": 1.0, "required": True}]
    new_items, dropped = _build_new_items(items, ground_truth, {"evidence": "no"})
    assert dropped == []
    assert [item["id"] for item in new_items] == ["evidence"]


def test_f2_never_drops_the_last_item(tmp_path):
    gt = {"justification": "The first render contains no aerated lung tissue, whereas "
                           "the second render shows bilateral aerated lung parenchyma."}
    run = write_run(
        tmp_path,
        [verdict("justification", "canonical_string_equal")],
        gt=gt,
        stored=0.0,
        answer='{"justification": "anything"}',
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["report_only"] == []
    assert {v["id"] for v in new["new_verdicts"]} == {"justification"}


def test_f1_the_circular_comparator_reads_a_string_only_under_the_new_rules(tmp_path):
    # The carried finding: ``_compare_circular_tolerance`` read both sides
    # through ``_number``, which returns None for any string, so an angle the
    # agent wrote as a string could never pass. The old pass has to keep that
    # behaviour, or it would not be replaying the rules it claims to replay.
    gt = {"dihedral_deg": 53.34}
    run = write_run(
        tmp_path,
        [verdict("dihedral_deg", "circular_tolerance")],
        gt=gt,
        stored=0.0,
        answer='{"dihedral_deg": "53.34"}',
    )
    old = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="old")
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert old["new_score"] == 0.0
    assert old["reproduced"] is True
    assert new["new_score"] == pytest.approx(1.0)
    assert new["new_verdicts"][0]["rule"] == "F1"


def test_f1_a_string_ground_truth_of_an_angle_compares_under_the_new_rules(tmp_path):
    # The measured case the plan cites: a ``*_deg`` field whose ground truth is
    # the string "53.34" and whose answer is the number.
    gt = {"dihedral_deg": "53.34"}
    run = write_run(
        tmp_path,
        [verdict("dihedral_deg", "circular_tolerance")],
        gt=gt,
        stored=0.0,
        answer='{"dihedral_deg": 53.34}',
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["new_score"] == pytest.approx(1.0)


def test_the_circular_fix_does_not_reach_an_integer_string(tmp_path):
    # Reach, stated rather than assumed. The comparator needs a band, and the
    # band derivation gives none to a string that parses as an *int* -- so an
    # angle stored as "180" still fails where one stored as "53.34" now passes.
    gt = {"dihedral_deg": "180"}
    run = write_run(
        tmp_path,
        [verdict("dihedral_deg", "circular_tolerance")],
        gt=gt,
        stored=0.0,
        answer='{"dihedral_deg": 180}',
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["new_score"] == 0.0


def test_a_rule_that_did_not_move_a_verdict_is_not_labelled(tmp_path):
    gt = {"placement": "protein_cleft_or_pocket"}
    run = write_run(
        tmp_path,
        [verdict("placement", "canonical_string_equal", passed=True, reason="pass")],
        gt=gt,
        stored=1.0,
    )
    new = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="new")
    assert new["new_score"] == pytest.approx(1.0)
    assert new["new_verdicts"][0]["rule"] is None


# ── the tolerance the tree declared is the one the replay must use ────────── #

def test_a_tolerance_is_derived_from_the_ground_truth(tmp_path):
    # A measurement keeps the closed-loop band; a count gets none.
    gt = {"defect_offsets_m": [1.25, 3.5], "total_panel_count": 4}
    run = write_run(
        tmp_path,
        [verdict("defect_offsets_m", "vector_abs_tolerance", passed=True, reason="pass"),
         verdict("total_panel_count", "integer_equal", passed=True, reason="pass")],
        gt=gt,
        stored=1.0,
        answer='{"defect_offsets_m": [1.251, 3.504], "total_panel_count": 4}',
    )
    result = replay_one(run_dir=run, ground_truth=gt, task_id="t1", arm="cc", rules="old")
    assert result["new_score"] == pytest.approx(1.0)
    assert result["reproduced"] is True


# ── the corpus walk and its output ────────────────────────────────────────── #

def _corpus(tmp_path):
    """A two-domain delivery: one task that can be replayed, one that cannot."""
    delivery = tmp_path / "delivery"
    good = delivery / "biology" / "t1" / "runs" / "cc"
    (good / "verifier").mkdir(parents=True)
    (good / "agent").mkdir(parents=True)
    (good / "verifier" / "sci3d_scores.json").write_text(json.dumps({
        "task_id": "t1",
        "answer_score": {"value": 1.0, "raw": {
            "eligible": True, "required_gate": False, "parse_error": None,
            "verdicts": [verdict("answer", "integer_equal", passed=True, reason="pass")],
        }},
    }))
    (good / "agent" / "trajectory.json").write_text(json.dumps({"steps": [
        {"step_id": "0", "source": "agent", "message": 'SCI3D_FINAL_JSON: {"answer": 20}'},
    ]}))
    # A task with no runs directory at all: reported, never dropped.
    (delivery / "biology" / "t2").mkdir(parents=True)
    for folder, doc in (
        ("001-t1", {"task_id": "t1", "ground_truth": {"answer": 20}}),
        ("002-t2", {"task_id": "t2", "ground_truth": {"answer": 20}}),
    ):
        bundle = tmp_path / "bundle" / "tasks" / "biology" / "sub" / folder
        bundle.mkdir(parents=True)
        (bundle / "task.json").write_text(json.dumps(doc))
    return delivery, tmp_path / "bundle"


def _digest(root: Path) -> dict:
    return {
        str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_the_corpus_walk_reports_a_task_with_no_runs(tmp_path):
    from tools.replay_scoring import replay_corpus

    delivery, bundle = _corpus(tmp_path)
    rows = replay_corpus(bundle=bundle, runs_root=delivery, rules="old")
    by_key = {(r["task_id"], r["arm"]): r for r in rows}
    assert by_key[("t1", "cc")]["status"] == "scored"
    assert by_key[("t2", "-")]["status"] == "skipped"
    assert "runs" in by_key[("t2", "-")]["reason"].lower()
    assert by_key[("t2", "-")]["new_score"] is None


def test_main_writes_only_its_output(tmp_path, capsys):
    from tools.replay_scoring import main

    delivery, bundle = _corpus(tmp_path)
    before = _digest(delivery)
    out = tmp_path / "replay.jsonl"
    assert main(["--rules", "old", "--bundle", str(bundle), "--runs", str(delivery),
                 "--out", str(out)]) == 0
    assert _digest(delivery) == before
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(rows) == 2
    scored = [r for r in rows if r["status"] == "scored"]
    assert scored and scored[0]["reproduced"] is True
    assert "reproduced=1/1" in capsys.readouterr().out


def test_main_refuses_to_write_inside_the_delivery(tmp_path, capsys):
    from tools.replay_scoring import main

    delivery, bundle = _corpus(tmp_path)
    out = delivery / "biology" / "t1" / "runs" / "cc" / "rescore.jsonl"
    assert main(["--rules", "old", "--bundle", str(bundle), "--runs", str(delivery),
                 "--out", str(out)]) == 2
    assert not out.exists()


# ── the tolerance the tree declared is the one the replay must use ────────── #

def test_the_old_rules_do_not_band_a_numeric_string_the_new_ones_do():
    # The band derivation changed in Task 2 (carried finding R12). The replay
    # has to date the band to the rules it is replaying with, or the old pass
    # would grade a trial with a band the tree never carried.
    from tools.replay_scoring import _legacy_tolerance_for

    assert _legacy_tolerance_for("defect_offset_m", "1.25") is None
    assert _legacy_tolerance_for("defect_offset_m", 1.25) == pytest.approx(0.005)
    assert _legacy_tolerance_for("total_panel_count", "4") is None
    assert _legacy_tolerance_for("crosses_fov_midline", False) is None


# ── which checkout the scorer is imported from ────────────────────────────── #
#
# The measurement is only evidence if the two runs really import two different
# trees. The failure this guards against is silent: ``sys.path.insert`` after
# the modules are already in ``sys.modules`` returns the first import's modules
# with no error, and two byte-identical output files then read as "no
# difference" when the truth is "the switch is not connected".


def _fake_scorer_root(root: Path) -> Path:
    """A checkout-shaped tree whose three packages identify themselves.

    Three packages, not one: the replay imports ``harbor_adapters``,
    ``sci3d_bench`` and ``scripts.harbor``, and a run that takes one of them
    from each root is measuring a mixture of two revisions.
    """
    for relative, source in (
        ("harbor_adapters/__init__.py", ""),
        ("harbor_adapters/sci3d/__init__.py", ""),
        ("harbor_adapters/sci3d/_verify_translate.py",
         "MARKER = 'harbor'\ndef answer_json_to_trajview(*a, **k):\n    return None\n"),
        ("sci3d_bench/__init__.py", ""),
        ("sci3d_bench/scorers/__init__.py", ""),
        ("sci3d_bench/scorers/deterministic_answer.py",
         "MARKER = 'sci3d'\n_COMPARATORS = {}\n"),
        ("scripts/__init__.py", ""),
        ("scripts/harbor/__init__.py", ""),
        ("scripts/harbor/_tolerance_rules.py",
         "MARKER = 'scripts'\ndef tolerance_for(field, value):\n    return None\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


@pytest.fixture
def scorer_state():
    """Undo what pointing the loader at a fixture checkout did.

    ``load_scorer`` rewrites ``sys.modules`` and ``sys.path`` on purpose; a test
    that drives it at a fake tree must not leave the next test scoring with a
    fake.
    """
    from tools import replay_scoring

    saved_modules = dict(sys.modules)
    saved_path = list(sys.path)
    saved_loaded = replay_scoring._LOADED
    yield
    replay_scoring._LOADED = saved_loaded
    for name in [n for n in sys.modules if n not in saved_modules]:
        del sys.modules[name]
    sys.modules.update(saved_modules)
    sys.path[:] = saved_path


def test_the_parser_takes_a_scorer_root_that_defaults_to_none():
    from tools.replay_scoring import build_parser

    args = build_parser().parse_args(
        ["--bundle", "b", "--runs", "r", "--out", "o"]
    )
    assert args.scorer_root is None
    assert build_parser().parse_args(
        ["--bundle", "b", "--runs", "r", "--out", "o", "--scorer-root", "/elsewhere"]
    ).scorer_root == Path("/elsewhere")


def test_the_default_root_is_this_checkout(scorer_state):
    from tools import replay_scoring
    from tools.replay_scoring import load_scorer

    assert replay_scoring.DEFAULT_ROOT == Path(replay_scoring.__file__).resolve().parents[1]
    scorer = load_scorer()
    assert scorer.root == replay_scoring.DEFAULT_ROOT
    for module in (scorer.da, scorer.tolerance_rules, scorer.trajectory_reader):
        assert Path(module.__file__).resolve().is_relative_to(scorer.root)


def test_all_three_module_trees_are_imported_from_the_root_it_is_given(tmp_path, scorer_state):
    from tools.replay_scoring import load_scorer

    root = _fake_scorer_root(tmp_path / "elsewhere").resolve()
    scorer = load_scorer(root)
    assert scorer.root == root
    assert [scorer.trajectory_reader.MARKER, scorer.da.MARKER,
            scorer.tolerance_rules.MARKER] == ["harbor", "sci3d", "scripts"]
    for module in (scorer.trajectory_reader, scorer.da, scorer.tolerance_rules):
        assert Path(module.__file__).resolve().is_relative_to(root)


def test_switching_root_after_the_default_was_already_imported_still_switches(
    tmp_path, scorer_state
):
    # The exact trap. By the time a root is chosen, the modules may already be
    # in ``sys.modules`` -- this checkout's, imported by the default. A path
    # insertion alone would be a no-op, and both runs would come back
    # byte-identical.
    from tools.replay_scoring import load_scorer

    first = load_scorer()
    root = _fake_scorer_root(tmp_path / "elsewhere").resolve()
    second = load_scorer(root)
    assert second.da is not first.da
    assert second.da.MARKER == "sci3d"
    assert Path(second.da.__file__).resolve().is_relative_to(root)


def test_a_root_that_does_not_hold_the_trees_is_refused_rather_than_mixed(
    tmp_path, scorer_state
):
    # A root missing a package would otherwise fall through to whatever else is
    # on ``sys.path`` -- this checkout -- and score half of one revision with
    # half of the other. That must be an error, not a quiet mixture.
    from tools.replay_scoring import ReplayError, load_scorer

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReplayError, match="not under"):
        load_scorer(empty)


def test_main_hands_the_parsed_root_to_the_loader(tmp_path, monkeypatch):
    from tools import replay_scoring

    chosen: list[Path | None] = []
    calls: list[str] = []
    monkeypatch.setattr(replay_scoring, "load_scorer",
                        lambda root=None: chosen.append(root))
    monkeypatch.setattr(replay_scoring, "_scorer_lines", lambda scorer: [])
    monkeypatch.setattr(replay_scoring, "replay_corpus",
                        lambda **kwargs: calls.append("corpus") or [])
    monkeypatch.setattr(replay_scoring, "write_rows", lambda rows, out: None)
    assert replay_scoring.main(
        ["--rules", "new", "--bundle", "b", "--runs", "r", "--out", "o",
         "--scorer-root", "/elsewhere"]
    ) == 0
    assert chosen == [Path("/elsewhere")]
    assert calls == ["corpus"], "the loader must run before the corpus is walked"


def test_every_row_names_the_checkout_that_scored_it_and_the_delivery_it_read(tmp_path):
    # The measurement's premise is "these two runs came from two different
    # revisions". A premise that lives only in pasted console text is not
    # evidence, so each row carries both roots: the comparison document can
    # then demonstrate its own inputs.
    from tools import replay_scoring

    delivery, bundle = _corpus(tmp_path)
    rows = replay_scoring.replay_corpus(bundle=bundle, runs_root=delivery, rules="old")
    assert rows
    assert {r["scorer_root"] for r in rows} == {str(replay_scoring.DEFAULT_ROOT)}
    assert {r["runs_root"] for r in rows} == {str(delivery.resolve())}
    # A skipped row carries it too: it is a row, and it names the run it was
    # skipped in.
    assert {r["scorer_root"] for r in rows if r["status"] == "skipped"} == {
        str(replay_scoring.DEFAULT_ROOT)
    }


def test_the_row_records_the_root_in_force_not_a_constant(tmp_path, scorer_state):
    from tools import replay_scoring

    root = _fake_scorer_root(tmp_path / "elsewhere").resolve()
    replay_scoring.load_scorer(root)
    delivery = tmp_path / "delivery"
    (delivery / "biology" / "t9").mkdir(parents=True)
    task = tmp_path / "bundle" / "tasks" / "biology" / "sub" / "009-t9"
    task.mkdir(parents=True)
    (task / "task.json").write_text(json.dumps({"task_id": "t9", "ground_truth": {"a": 1}}))
    rows = replay_scoring.replay_corpus(bundle=tmp_path / "bundle", runs_root=delivery,
                                        rules="new")
    assert rows and {r["scorer_root"] for r in rows} == {str(root)}


def test_main_loads_the_default_root_when_no_root_is_given(monkeypatch):
    from tools import replay_scoring

    chosen: list[Path | None] = []
    monkeypatch.setattr(replay_scoring, "load_scorer",
                        lambda root=None: chosen.append(root))
    monkeypatch.setattr(replay_scoring, "_scorer_lines", lambda scorer: [])
    monkeypatch.setattr(replay_scoring, "replay_corpus", lambda **kwargs: [])
    monkeypatch.setattr(replay_scoring, "write_rows", lambda rows, out: None)
    assert replay_scoring.main(
        ["--rules", "new", "--bundle", "b", "--runs", "r", "--out", "o"]
    ) == 0
    assert chosen == [None]
