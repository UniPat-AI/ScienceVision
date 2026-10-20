"""Comparators for label/object lists and for the positional extent vector.

`_default_comparator` used to send *every* list to `vector_abs_tolerance`,
which wants a numeric tolerance; a list of labels or objects then failed
`_validate_tolerance` and turned the whole task into `invalid_task_contract`,
whatever the agent answered. Two explicit allow-lists replace that dead end:
one for fields whose elements name themselves (any order is the same answer),
one for positional vectors. Every other list keeps its previous routing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer


def _task(field: str, expected, **scorer_extra) -> SimpleNamespace:
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=[field]),
        ground_truth=SimpleNamespace(value={field: expected}),
        scorer=SimpleNamespace(
            primary="exact_match",
            config={"primary": "exact_match", "secondary": [], **scorer_extra},
        ),
    )


def _score(field: str, expected, answer, **scorer_extra):
    return DeterministicAnswerScorer().score(
        _task(field, expected, **scorer_extra),
        SimpleNamespace(final_answer={field: answer}),
    )


def test_a_label_list_scores_order_insensitively() -> None:
    expected = ["front", "side", "oblique"]
    scored = _score("screenshots", expected, ["oblique", "front", "side"])
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "canonical_set_equal"
    assert _score("screenshots", expected, ["front", "side", "top"]).value == pytest.approx(0.0)


def test_a_label_list_still_counts_multiplicity() -> None:
    assert _score("interaction_types", ["a", "a", "b"], ["a", "b", "b"]).value == pytest.approx(0.0)
    assert _score("interaction_types", ["a", "a", "b"], ["b", "a", "a"]).value == pytest.approx(1.0)


def test_label_identity_ignores_case_and_spacing() -> None:
    scored = _score("cited_clause", ["Left Floor"], ["  left   floor "])
    assert scored.value == pytest.approx(1.0)


def test_object_lists_compare_on_content_not_key_order() -> None:
    expected = [{"panel": "A", "moves": "up"}]
    scored = _score("panels_showing_the_movement", expected, [{"moves": "up", "panel": "A"}])
    assert scored.value == pytest.approx(1.0)
    assert _score(
        "panels_showing_the_movement", expected, [{"moves": "down", "panel": "A"}]
    ).value == pytest.approx(0.0)


def test_extent_vox_is_positional_and_a_permutation_fails() -> None:
    """`["97-106", "110-123", "54-61"]` is x/y/z; swapping two is a wrong answer."""
    expected = ["97-106", "110-123", "54-61"]
    assert _score("extent_vox", expected, expected).value == pytest.approx(1.0)
    assert _score("extent_vox", expected, ["110-123", "97-106", "54-61"]).value == pytest.approx(0.0)


def test_a_numeric_identity_list_is_still_a_set() -> None:
    assert _score("residue_numbers", [43, 44], [44, 43]).value == pytest.approx(1.0)
    assert _score("residue_numbers", [43, 44], [43, 45]).value == pytest.approx(0.0)


def test_a_list_outside_the_allow_lists_still_fails_closed() -> None:
    """The loud signal stays: an unknown label list is a contract error, not a guess."""
    scored = _score("something_else", ["a", "b"], ["a", "b"])
    assert scored.value == pytest.approx(0.0)
    assert scored.raw["eligible"] is False
    assert scored.raw["errors"] == ["missing_tolerance:something_else"]


def test_a_nested_numeric_vector_grades_per_leaf() -> None:
    expected = [[1.0, 2.0], [3.0, 4.0]]
    scored = _score("bbox_extents_A", expected, [[1.05, 2.05], [3.05, 4.05]], tolerance={"bbox_extents_A": 0.1})
    assert scored.value == pytest.approx(1.0)
    assert _score(
        "bbox_extents_A", expected, [[1.2, 2.0], [3.0, 4.0]], tolerance={"bbox_extents_A": 0.1}
    ).value == pytest.approx(0.0)
    assert _score(
        "bbox_extents_A", expected, [[1.05, 2.05]], tolerance={"bbox_extents_A": 0.1}
    ).value == pytest.approx(0.0)


def test_an_atom_identity_accepts_the_crystal_scheme() -> None:
    """A small-molecule crystal has no chain/residue; element + label is its scheme."""
    expected = {"element": "O", "label": "O1"}
    assert _score("contact_atom_id", expected, {"label": "O1", "element": "O"}).value == pytest.approx(1.0)
    assert _score("contact_atom_id", expected, {"element": "O", "label": "O2"}).value == pytest.approx(0.0)


# --- the biology hard-case fields -------------------------------------------
#
# `residues` / `measurements` / `waters` / `coordinating_atoms` first appeared in
# the 2026-09-29 biology package. Three of the four routed to
# `vector_abs_tolerance` before the lists above named them, so the whole task
# came out `invalid_task_contract` -- a silent zero whatever the model answered.
# The ordering each one gets is the ordering its own question states.

_RESIDUE = {"chain_id": "B", "residue_name": "ASN", "residue_number": 37}
_RESIDUE_2 = {"chain_id": "C", "residue_name": "PRO", "residue_number": 5}
_ATOM = {"chain_id": "A", "residue_name": "CYS", "residue_number": 137,
         "atom_name": "SG", "element": "S"}
_ATOM_2 = {"chain_id": "A", "residue_name": "CYS", "residue_number": 140,
           "atom_name": "SG", "element": "S"}


def test_the_question_ordered_residue_list_is_positional() -> None:
    """"按链编号的字母顺序排列" -- B before C, so the swap is a wrong answer."""
    expected = [_RESIDUE, _RESIDUE_2]
    scored = _score("residues", expected, expected)
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "ordered_string_vector_equal"
    assert _score("residues", expected, [_RESIDUE_2, _RESIDUE]).value == pytest.approx(0.0)


def test_waters_are_positional_and_identity_is_over_the_whole_object() -> None:
    """"按链编号、残基编号升序排列"; the O is named by chain+residue, not by order."""
    expected = [{"chain_id": "C", "residue_name": "HOH", "residue_number": 515,
                 "atom_name": "O", "element": "O"},
                {"chain_id": "C", "residue_name": "HOH", "residue_number": 521,
                 "atom_name": "O", "element": "O"}]
    assert _score("waters", expected, expected).value == pytest.approx(1.0)
    assert _score("waters", expected, list(reversed(expected))).value == pytest.approx(0.0)
    wrong = [dict(expected[0], residue_number=516), expected[1]]
    assert _score("waters", expected, wrong).value == pytest.approx(0.0)


def test_coordinating_atoms_is_a_set_with_multiplicity() -> None:
    """The question asks for "all coordinating atoms" and never orders them."""
    expected = [_ATOM, _ATOM_2]
    scored = _score("coordinating_atoms", expected, list(reversed(expected)))
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "canonical_set_equal"
    assert _score("coordinating_atoms", expected, expected[:1]).value == pytest.approx(0.0)
    assert _score("coordinating_atoms", expected, expected + [_ATOM]).value == pytest.approx(0.0)


def test_an_ordered_object_list_keeps_the_order_of_its_measurements() -> None:
    """`measurements` is one entry per non-magenta chain, ordered by that chain."""
    first = {"minimum_distance_A": 6.421, "magenta_atom": _ATOM}
    second = {"minimum_distance_A": 21.915, "magenta_atom": _ATOM_2}
    scored = _score("measurements", [first, second], [second, first])
    assert scored.value == pytest.approx(0.0)
    assert scored.raw["eligible"] is True
