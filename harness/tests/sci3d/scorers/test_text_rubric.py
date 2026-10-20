"""The deterministic text rubric, pinned on the cases that make it necessary.

The module exists because similarity to a reference sentence cannot separate a
paraphrase from a contradiction. These tests are built around that specific
failure rather than around the happy path:

- ``test_negation_is_clause_scoped`` and ``test_the_negation_guard_is_what_...``
  hold the line the derived rubric would otherwise lose;
- ``test_a_derived_score_is_degraded_...`` keeps the two rubric provenances
  distinguishable, so a fallback score cannot be read as an authored one;
- ``test_scoring_is_deterministic_across_repeated_calls`` states the property the
  whole design rests on.

Run:  python -m pytest tests/sci3d/scorers/test_text_rubric.py -q
"""

from __future__ import annotations

import pytest

from sci3d_bench.scorers.text_rubric import (
    KeywordGroup,
    TextRubric,
    clause_starts,
    derive_rubric,
    group_is_satisfied,
    negated_positions,
    rubric_for,
    rubric_from_json,
    rubric_to_json,
    score_text,
    score_with_fallback,
    tokenize,
)

# The reference sentence from `sv-arch-exp-05`, verbatim.
GT = (
    "The overall deformation pattern is symmetric, with the largest deformation "
    "at the mid-span of the bridge; no twisting occurs."
)
CORRECT = (
    "The deformation is symmetric, with the largest deformation at midspan and "
    "no twisting."
)
CONTRADICTING = (
    "The deformation is symmetric, with the largest deformation at midspan and "
    "twisting occurs."
)


def test_tokenize_lowercases_and_normalises_hyphens():
    assert tokenize("Mid-Span") == ("mid", "span")
    assert tokenize("  no   twisting  ") == ("no", "twisting")


def test_tokenize_splits_contractions_into_a_negator():
    """"doesn't twist" has to look like "does not twist" or the negator is
    invisible and the phrase reads as affirmative."""
    assert tokenize("it doesn't twist") == ("it", "does", "not", "twist")


def test_clause_starts_are_indices_into_the_token_stream():
    """The boundary view must address the same stream, or every negated word
    after the first comma is mislabelled -- which is exactly the bug this
    replaced: splitting the text dropped the conjunctions and shifted the rest."""
    text = "a, b; c: d and e but f"
    assert tokenize(text) == ("a", "b", "c", "d", "and", "e", "but", "f")
    assert clause_starts(text) == frozenset({1, 2, 3})
    for index in clause_starts(text):
        assert 0 < index < len(tokenize(text))


def test_clause_starts_never_points_past_the_end():
    for text in (GT, "no twisting.", "twisting", "", ".,;"):
        assert all(i <= len(tokenize(text)) for i in clause_starts(text))
        assert 0 not in clause_starts(text)


def test_a_negator_governs_the_rest_of_its_clause():
    parts = negated_positions(GT, tokenize(GT))
    tokens = tokenize(GT)
    assert tokens.index("twisting") in parts
    # The clause-scoped rule, and the reason the earlier window rule was wrong:
    # `occurs` is inside the same negated clause and is negated too.
    assert tokens.index("occurs") in parts


def test_a_negator_does_not_reach_past_a_clause_break():
    text = "the supports are unchanged and no twisting occurs"
    parts = negated_positions(text, tokenize(text))
    tokens = tokenize(text)
    assert tokens.index("supports") not in parts
    assert tokens.index("twisting") in parts


def test_a_group_matches_any_of_its_alternatives():
    group = KeywordGroup(("mid-span", "midspan", "midpoint"))
    for answer in ("at mid-span", "at midspan", "at the midpoint"):
        assert group_is_satisfied(tokenize(answer), group, set())


def test_a_negated_group_needs_the_negator_in_the_answer():
    group = KeywordGroup(("twisting",), negated=True)
    for answer, expected in (
        ("no twisting", True),
        ("without any twisting", True),
        ("twisting occurs", False),
        ("there is torsion but no twisting", True),
        ("nothing was observed", False),
    ):
        tokens = tokenize(answer)
        got = group_is_satisfied(tokens, group, negated_positions(answer, tokens))
        assert got is expected, answer


def test_the_negation_guard_is_what_separates_the_two_answers():
    """Unguarded, the rubric does not merely fail to separate them -- it inverts.

    Both answers contain `twisting`, and the contradicting one additionally
    contains `occurs`, so a rubric that ignores polarity scores the *wrong*
    answer higher. The guard is not a refinement here; without it the metric
    points the wrong way.
    """
    rubric = derive_rubric(GT)
    correct = score_text(CORRECT, rubric)
    contradicting = score_text(CONTRADICTING, rubric)
    assert correct.value > contradicting.value

    unguarded = TextRubric(
        required=tuple(
            KeywordGroup(g.alternatives, negated=False) for g in rubric.required
        )
    )
    assert score_text(CONTRADICTING, unguarded).value > score_text(
        CORRECT, unguarded
    ).value


def test_mentioning_the_negated_word_affirmatively_earns_nothing():
    """An answer that never raises the topic must not lose to one that raises it
    and gets it backwards. Both score the same here, and that is the point: the
    affirmative mention buys exactly zero credit for the negated groups."""
    rubric = derive_rubric(GT)
    silent = score_text("The deformation is symmetric and largest at midspan.", rubric)
    affirmed = score_text(
        "The deformation is symmetric and largest at midspan and twisting occurs.",
        rubric,
    )
    assert affirmed.value <= silent.value
    negated_names = {g.alternatives[0] for g in rubric.required if g.negated}
    assert negated_names
    assert not negated_names & set(affirmed.raw["matched"])


def test_a_forbidden_group_zeroes_the_answer():
    rubric = TextRubric(
        required=(KeywordGroup(("symmetric",)),),
        forbidden=(KeywordGroup(("asymmetric",)),),
    )
    assert score_text("the deformation is asymmetric", rubric).value == 0.0
    assert score_text("the deformation is symmetric", rubric).value == 1.0
    assert score_text("asymmetric", rubric).raw["forbidden_hit"] == ["asymmetric"]


def test_the_score_is_the_fraction_of_required_groups_matched():
    rubric = TextRubric(
        required=(
            KeywordGroup(("alpha",)),
            KeywordGroup(("beta",)),
            KeywordGroup(("gamma",)),
        )
    )
    assert score_text("alpha beta gamma", rubric).value == 1.0
    assert score_text("alpha gamma", rubric).value == pytest.approx(2 / 3)
    assert score_text("nothing relevant", rubric).value == 0.0


def test_an_authored_rubric_scores_undegraded():
    result = score_text("the deformation is symmetric", TextRubric(
        required=(KeywordGroup(("symmetric",)),)
    ))
    assert result.degraded is False
    assert result.raw["source"] == "authored"


def test_a_derived_score_is_degraded_and_says_so():
    """The whole point of option C: a fallback score must not pass for an
    authored one, or the transition quietly becomes permanent."""
    result = score_text(CORRECT, derive_rubric(GT))
    assert result.degraded is True
    assert result.raw["source"] == "derived"


def test_an_authored_rubric_wins_over_derivation():
    authored = TextRubric(required=(KeywordGroup(("symmetric",)),))
    assert rubric_for(GT, authored) is authored
    assert rubric_for(GT, None).source == "derived"


def test_score_with_fallback_prefers_the_authored_rubric():
    authored = TextRubric(required=(KeywordGroup(("symmetric",)),))
    assert score_with_fallback(CORRECT, GT, authored).raw["required"] == 1
    assert score_with_fallback(CORRECT, GT, None).raw["source"] == "derived"


def test_an_empty_rubric_scores_zero_rather_than_one():
    """A rubric that asks nothing must not award full marks -- an answerless
    rubric is a bug in the rubric, and it has to look like one."""
    result = score_text("anything at all", TextRubric(required=()))
    assert result.value == 0.0
    assert result.degraded is True
    assert result.raw["reason"] == "rubric has no required groups"


def test_a_group_needs_at_least_one_non_blank_alternative():
    with pytest.raises(ValueError):
        KeywordGroup(())
    with pytest.raises(ValueError):
        KeywordGroup(("  ",))


def test_an_unknown_rubric_source_is_rejected():
    with pytest.raises(ValueError):
        TextRubric(required=(KeywordGroup(("a",)),), source="vibes")


def test_rubrics_round_trip_through_json():
    rubric = TextRubric(
        required=(
            KeywordGroup(("symmetric", "symmetry")),
            KeywordGroup(("twisting", "twist"), negated=True),
        ),
        forbidden=(KeywordGroup(("asymmetric",)),),
        notes="authored for sv-arch-exp-05",
    )
    assert rubric_from_json(rubric_to_json(rubric)) == rubric


def test_a_bare_string_and_a_bare_list_are_both_read_as_groups():
    rubric = rubric_from_json({"required": ["symmetric", ["mid-span", "midspan"]]})
    assert rubric.required[0] == KeywordGroup(("symmetric",))
    assert rubric.required[1] == KeywordGroup(("mid-span", "midspan"))


def test_scoring_is_deterministic_across_repeated_calls():
    rubric = derive_rubric(GT)
    first = score_text(CORRECT, rubric)
    for _ in range(5):
        assert score_text(CORRECT, rubric) == first


def test_scoring_does_not_derive_the_rubric_more_than_once():
    """`derive_rubric` is pure, so two calls must produce equal rubrics -- a
    rubric that drifted between calls would make the score unreproducible."""
    assert derive_rubric(GT) == derive_rubric(GT)


def test_a_non_string_answer_scores_zero_instead_of_raising():
    for answer in (None, 42, {"a": 1}, []):
        assert score_text(answer, derive_rubric(GT)).value == 0.0


def test_deriving_from_a_structured_answer_refuses_rather_than_guessing():
    """A structured ground truth belongs to the field comparator.

    Tokenising its repr yields a rubric built from field names -- `true`,
    `none`, `region` -- and the score that comes out looks plausible while
    measuring nothing. Refusing is the only safe behaviour, and it matters
    because the review bundle is actively converting prose answers to
    structured ones, so this path gets hit rather than being hypothetical.
    """
    for ground_truth in ({"a": True}, {"x": "none"}, [1, 2], 42, None):
        with pytest.raises(ValueError, match="prose ground truth"):
            derive_rubric(ground_truth)


def test_rubric_for_uses_the_authored_rubric_without_touching_the_ground_truth():
    """The authored path must keep working for a structured answer -- the
    refusal above is about derivation, not about the module as a whole."""
    authored = TextRubric(required=(KeywordGroup(("symmetric",)),))
    assert rubric_for({"a": True}, authored) is authored
