"""Explanation fields leave the rubric, but never the last one standing."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sci3d_bench.scorers.deterministic_answer import (
    DeterministicAnswerScorer,
    EXPLANATION_FIELDS,
    RUBRIC_VERSION,
    SCORER_NAME,
    select_report_only,
)

# The values below are real sentences because the rule reads the value: eight
# words is the threshold, so an elided fixture would test the wrong side of it.
PAIR = {"placement": "protein_cleft_or_pocket",
        "evidence": "The orange component is nestled in a pocket between two alpha helices."}
ONLY_PROSE = {
    "justification": "The first render contains no aerated lung tissue, whereas "
                     "the second shows a clear pleural effusion."
}
TWO_PROSE = {"located": "The ramp consists of one inclined section running between two "
                        "landing platforms.",
             "verdict": "The ramp is compliant with the code for accessible pedestrian routes."}


def test_an_explanation_field_beside_a_real_field_is_dropped():
    assert select_report_only(PAIR, []) == {"evidence"}


def test_the_last_remaining_item_is_never_dropped():
    # Dropping this would leave the task with no scorable item, turning a hard
    # question into an empty one and inflating every model's score.
    assert select_report_only(ONLY_PROSE, []) == set()


def test_dropping_would_empty_the_rubric_so_nothing_is_dropped():
    assert select_report_only(TWO_PROSE, []) == set()


def test_the_set_covers_the_fields_measured_in_v11():
    for name in ("evidence", "visual_basis", "spatial_evidence", "justification",
                 "visual_justification", "evidence_summary", "located", "verdict",
                 "cited_clause", "basis"):
        assert name in EXPLANATION_FIELDS


TRIPLE = {"placement": "protein_cleft_or_pocket",
          "notes": "The orange component is nestled in a pocket between two alpha helices.",
          "buried": False}


def test_a_contract_can_drop_a_field_the_default_set_misses():
    # A third, non-explanation field survives, so the refusal cannot fire and
    # the declared field is dropped even though no name in EXPLANATION_FIELDS
    # matches it.
    assert select_report_only(TRIPLE, ["notes"]) == {"notes"}


def test_a_short_value_on_an_explanation_name_is_not_an_explanation():
    """``verdict`` is ``"Compliant"`` in one delivered task and twenty-four
    words of reasoning in another. Deciding on the name would drop the
    one-word enum the agent has to choose between."""
    assert select_report_only({"verdict": "Compliant", "placement": "protein_cleft_or_pocket"}, []) == set()


def test_a_short_value_survives_while_the_prose_goes():
    assert select_report_only({**PAIR, "verdict": "Compliant"}, []) == {"evidence"}


CJK_PROSE = "第一个渲染图中没有含气的肺组织，第二个渲染图显示明显的胸腔积液。"


def test_a_cjk_explanation_is_prose_too():
    """CJK is not space-delimited, so the Latin word count cannot see it and
    the character count has to; without this branch every CJK explanation
    stays in the rubric and its task keeps its old ceiling."""
    assert select_report_only({"placement": "protein_cleft_or_pocket",
                               "justification": CJK_PROSE}, []) == {"justification"}


def test_a_short_cjk_value_is_not_prose():
    assert select_report_only({"verdict": "合规", "placement": "protein_cleft_or_pocket"},
                              []) == set()


def test_a_declared_field_with_a_short_value_is_not_dropped():
    """The value check covers the contract's own declaration too: naming a
    field in ``report_only`` does not make its short value an explanation."""
    assert select_report_only({"placement": "protein_cleft_or_pocket", "notes": "Compliant"},
                              ["notes"]) == set()


# --- the same rule seen from the outside, through the scorer ----------------
#
# ``select_report_only`` is a pure function, so the five tests above cannot
# fail if ``_compile_rubric`` never calls it, calls it on the wrong dict, or
# forgets to take the marked items back out. These drive the real entry point.

PLACEMENT = "protein_cleft_or_pocket"
PROSE = "The orange component is nestled in a pocket between two helices."
PARAPHRASE = "A pocket between two helices holds the orange part."


def _task(fields, ground_truth, **config):
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=fields),
        ground_truth=SimpleNamespace(value=ground_truth),
        scorer=SimpleNamespace(
            primary=SCORER_NAME,
            config={"version": RUBRIC_VERSION, **config},
        ),
    )


def _score(task, answer):
    return DeterministicAnswerScorer().score(
        task, SimpleNamespace(final_answer=answer)
    )


def _item(field, expected=None):
    return {
        "id": field,
        "pred_path": field,
        "gt_path": field,
        "comparator": (
            "boolean_equal" if isinstance(expected, bool) else "canonical_string_equal"
        ),
        "weight": 1.0,
        "required": True,
    }


# Three fields, one of them prose: the drop leaves two survivors, which is the
# smallest rubric the refusal allows to be produced.
THREE = {"placement": PLACEMENT, "evidence": PROSE, "buried": False}
ANSWER_THREE = {"placement": PLACEMENT, "evidence": PARAPHRASE, "buried": False}


def test_two_survivors_still_allow_the_drop():
    """The win, in one number: with the prose scored the ceiling was 2 of 3."""
    scored = _score(
        _task(["placement", "evidence", "buried"], THREE),
        ANSWER_THREE,
    )
    assert scored.raw["report_only"] == ["evidence"]
    assert scored.raw["total_items"] == 2
    assert scored.value == pytest.approx(1.0)


def test_a_single_survivor_refuses_the_drop():
    """Two fields, one of them prose: dropping would leave one item, and the
    spec's refusal is one survivor, not zero. A one-item task is passable on a
    single token, which is the inflation the refusal exists to prevent."""
    ground_truth = {"placement": PLACEMENT, "evidence": PROSE}
    scored = _score(
        _task(["placement", "evidence"], ground_truth),
        {"placement": PLACEMENT, "evidence": PARAPHRASE},
    )
    assert scored.raw["report_only"] == []
    assert scored.raw["total_items"] == 2
    assert scored.value == pytest.approx(0.5)


def test_an_empty_string_survivor_is_not_made_into_the_whole_task():
    """``arch-rhl-align-k011-l14-facade-bay`` carries ``cited_clause: ""``
    beside its prose. Empty is not prose, so it stays -- and as the only
    survivor it would be a one-item task any answer passes, on a three-field
    engineering-compliance question."""
    ground_truth = {"cited_clause": "", "evidence": PROSE}
    scored = _score(
        _task(["cited_clause", "evidence"], ground_truth),
        {"cited_clause": "", "evidence": PARAPHRASE},
    )
    assert scored.raw["report_only"] == []
    assert scored.raw["total_items"] == 2
    assert scored.value == pytest.approx(0.5)


def test_an_explicit_rubric_drops_the_prose_item_too():
    """This exercises the explicit branch, which the delivery does not use: 0
    of its 250 tasks carry ``scorer.rubric``, so on the delivery the implicit
    branch below is what runs. Worth keeping anyway, because F2 is the only
    rule here that reaches an explicit rubric -- the comparator derivation
    (``_default_comparator``) exists only on the implicit branch, so a
    delivered task's rubrics never pass through it."""
    scored = _score(
        _task(
            ["placement", "evidence", "buried"],
            THREE,
            rubric=[_item("placement"), _item("evidence"), _item("buried", False)],
        ),
        ANSWER_THREE,
    )
    assert scored.raw["report_only"] == ["evidence"]
    assert scored.raw["total_items"] == 2
    assert scored.value == pytest.approx(1.0)


def test_an_all_prose_rubric_still_scores_instead_of_blocking():
    ground_truth = {"justification": PROSE}
    scored = _score(
        _task(["justification"], ground_truth),
        {"justification": PARAPHRASE},
    )
    assert scored.raw["eligible"] is True
    assert scored.raw["report_only"] == []
    assert scored.raw["total_items"] == 1
    assert scored.value == pytest.approx(0.0)


def test_an_explicit_rubric_of_one_prose_item_is_not_emptied():
    """An explicit rubric can be a strict subset of the ground truth, so the
    survivor count is what refuses here: one item in, one dropped, zero left.
    Without it the task would come back ``empty_rubric`` and ``eligible:
    False`` -- not scored on less, but blocked outright."""
    ground_truth = {"placement": PLACEMENT, "evidence": PROSE}
    scored = _score(
        _task(
            ["placement", "evidence"],
            ground_truth,
            rubric=[_item("evidence")],
        ),
        {"placement": PLACEMENT, "evidence": PARAPHRASE},
    )
    assert scored.raw["eligible"] is True
    assert scored.raw["report_only"] == []
    assert scored.raw["total_items"] == 1
    assert scored.value == pytest.approx(0.0)


def test_a_prose_field_the_rubric_never_held_is_not_reported_as_dropped():
    """An explicit rubric may omit the prose field entirely. The change then
    touched nothing, and the replay reads ``report_only`` as the footprint of
    the change, so naming that field would overstate it.

    The rubric holds three real fields rather than one, because the refusal
    would otherwise swallow the overstatement: with fewer than two survivors
    the drop is refused and ``report_only`` comes back empty for the wrong
    reason. Three items and an unintersected set give two survivors, so this
    is the smallest shape in which the intersection is what is being tested.
    """
    ground_truth = {**THREE, "region": "cleft"}
    scored = _score(
        _task(
            ["placement", "evidence", "buried", "region"],
            ground_truth,
            rubric=[_item("placement"), _item("buried", False), _item("region")],
        ),
        {**ANSWER_THREE, "region": "cleft"},
    )
    assert scored.raw["report_only"] == []
    assert scored.raw["total_items"] == 3
    assert scored.value == pytest.approx(1.0)


def test_a_dropped_item_is_still_validated():
    """The drop runs after validation, so removing an item cannot be how a
    malformed item gets repaired: the task fails loudly, as it did before the
    drop existed."""
    rubric = [
        _item("placement"),
        _item("buried", False),
        {**_item("evidence"), "comparator": "no_such_comparator"},
    ]
    scored = _score(
        _task(["placement", "evidence", "buried"], THREE, rubric=rubric),
        ANSWER_THREE,
    )
    assert scored.raw["eligible"] is False
    assert scored.raw["reason"] == "invalid_task_contract"
    assert "invalid_rubric_item:evidence" in scored.raw["errors"]


def test_scoring_twice_reports_the_same_drop():
    """The marker is written onto a copy of the rubric item. If it reached the
    task's own config instead, the second pass would score a rubric the first
    pass had already edited."""
    rubric = [_item("placement"), _item("buried", False), _item("evidence")]
    task = _task(["placement", "evidence", "buried"], THREE, rubric=rubric)
    first = _score(task, ANSWER_THREE)
    second = _score(task, ANSWER_THREE)
    assert first.raw["report_only"] == second.raw["report_only"] == ["evidence"]
    assert first.value == second.value == pytest.approx(1.0)
    assert rubric == [_item("placement"), _item("buried", False), _item("evidence")]


def test_a_declared_field_the_name_set_misses_is_dropped_end_to_end():
    ground_truth = {"placement": PLACEMENT, "notes": PROSE, "buried": False}
    scored = _score(
        _task(["placement", "notes", "buried"], ground_truth, report_only=["notes"]),
        {"placement": PLACEMENT, "notes": PARAPHRASE, "buried": False},
    )
    assert scored.raw["report_only"] == ["notes"]
    assert scored.raw["total_items"] == 2
    assert scored.value == pytest.approx(1.0)
