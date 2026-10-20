"""Numeric atom ids through the atom-identity comparator.

`_default_comparator` routes any field whose name holds both "atom" and a
trailing "_id" to `canonical_atom_id_equal`, whose non-string branch went
through `_canonical_atom_object`. That helper answers None for anything that is
not a dict, so two *identical integers* compared False.

A `.xyz` conformer names its atoms by index -- there is no chain, residue or
atom name to carry -- so an integer is the only identity the question can ask
for, and the questions do declare ``<integer>``. The comparator is what has to
accept it.
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


def test_an_integer_atom_id_matches_itself() -> None:
    scored = _score("nearly_fully_occluded_atom_id", 21, 21)
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "canonical_atom_id_equal"


def test_a_different_integer_atom_id_fails() -> None:
    assert _score("nearly_fully_occluded_atom_id", 21, 20).value == pytest.approx(0.0)


def test_an_integer_atom_id_accepts_the_float_spelling() -> None:
    assert _score("initial_occluded_atom_id", 7, 7.0).value == pytest.approx(1.0)


def test_a_boolean_is_not_an_atom_id() -> None:
    assert _score("initial_occluded_atom_id", 1, True).value == pytest.approx(0.0)
    assert _score("initial_occluded_atom_id", True, 1).value == pytest.approx(0.0)


def test_a_string_atom_label_still_compares_verbatim() -> None:
    expected = "O of ALA 17 in chain 2X"
    assert _score("atom_id", expected, "  o OF ala   17 IN chain 2x ").value == pytest.approx(1.0)
    assert _score("atom_id", expected, "O of ALA 18 in chain 2X").value == pytest.approx(0.0)


def test_a_string_is_not_interchangeable_with_its_number() -> None:
    assert _score("nearly_fully_occluded_atom_id", 21, "21").value == pytest.approx(0.0)
    assert _score("nearly_fully_occluded_atom_id", "21", 21).value == pytest.approx(0.0)


def test_an_atom_identity_dict_still_compares_canonically() -> None:
    expected = {"chain_id": "B", "residue_name": "ASN", "residue_number": 37, "atom_name": "ND2"}
    assert _score("occluded_atom_id", expected, {**expected, "chain_id": " b "}).value == pytest.approx(1.0)
    assert _score("occluded_atom_id", expected, {**expected, "residue_number": 38}).value == pytest.approx(0.0)
