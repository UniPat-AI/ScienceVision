"""Fields whose *name* looks like an identity but whose value is a measurement.

`_default_comparator` picks a comparator from the field name before it looks at
the value, and three of those name rules return identity comparators: a name
containing "spacegroup", "residue", or "asset"/"structure" routes to a
canonicaliser that maps the value to a key in a known table. No number produces
such a key, so the canonicaliser answered None and two *identical* integers
compared False.

`structure_interference_count` and `n_residues_within` are measurements that
merely contain those words -- the first is a count of structures, not a
structure, and the second counts residues rather than naming one. Both could
never score, so the tasks they belong to could not reach 1.0 against their own
ground truth.

A numeric value is therefore never sent to an identity comparator. The guard is
monotone: every path it moves previously returned False, so no answer that
scored 1.0 can fail now.
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


def test_a_count_of_structures_matches_itself() -> None:
    scored = _score("structure_interference_count", 1, 1)
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "integer_equal"


def test_a_count_of_residues_matches_itself() -> None:
    scored = _score("n_residues_within", 4, 4)
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "integer_equal"


def test_the_count_comparator_requires_an_integer_on_both_sides() -> None:
    # The guard hands counts to ``integer_equal``, which is deliberately strict:
    # a count is a whole number, so ``1.0`` is not the answer ``1``. This is
    # unchanged from before the guard -- the field could not score at all then,
    # whatever the agent answered -- and loosening it here would widen what
    # counts as equal, which is a separate decision from routing.
    assert _score("structure_interference_count", 1, 1.0).value == pytest.approx(0.0)


def test_a_different_count_still_fails() -> None:
    assert _score("structure_interference_count", 1, 2).value == pytest.approx(0.0)
    assert _score("n_residues_within", 4, 5).value == pytest.approx(0.0)


def test_a_real_structure_name_keeps_the_asset_comparator() -> None:
    scored = _score("reference_structure_id", "2X", "2X")
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "canonical_asset_id_equal"


def test_a_real_residue_keeps_the_residue_comparator() -> None:
    scored = _score("terminus_nearer_noncanonical_residue", "ASN 37", "ASN 37")
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "canonical_residue_equal"


def test_a_real_residue_list_keeps_the_set_comparator() -> None:
    scored = _score("residue_numbers", [37, 5], [5, 37])
    assert scored.value == pytest.approx(1.0)
    assert scored.raw["verdicts"][0]["comparator"] == "canonical_set_equal"
