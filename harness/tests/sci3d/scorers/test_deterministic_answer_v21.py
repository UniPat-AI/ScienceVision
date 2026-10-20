from __future__ import annotations

from types import SimpleNamespace

import pytest

from sci3d_bench.scorers.deterministic_answer import (
    DeterministicAnswerScorer,
    GENERIC_RUBRIC_VERSION,
    GENERIC_SCORER_NAME,
    RUBRIC_VERSION,
    SCORER_NAME,
)


def _task(*, fields, ground_truth, config, primary=GENERIC_SCORER_NAME):
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=fields),
        ground_truth=SimpleNamespace(value=ground_truth),
        scorer=SimpleNamespace(primary=primary, config=config),
    )


def _traj(answer):
    return SimpleNamespace(final_answer=answer)


def _generic_task():
    return _task(
        fields=["label", "distance", "vector", "dihedral_deg"],
        ground_truth={
            "label": "right",
            "distance": 2.0,
            "vector": [10.0, 20.0, 30.0],
            "dihedral_deg": 179.0,
        },
        config={
            "schema_version": GENERIC_RUBRIC_VERSION,
            "strict_json_object": True,
            "fields": [
                {"field": "label", "comparison": "exact"},
                {"field": "distance", "comparison": "absolute_tolerance", "tolerance": 0.1},
                {"field": "vector", "comparison": "absolute_tolerance", "tolerance": 1.0},
                {
                    "field": "dihedral_deg",
                    "comparison": "absolute_tolerance",
                    "tolerance": 3.0,
                    "periodic_modulus": 360.0,
                },
            ],
        },
    )


def test_generic_boundary_vector_and_dihedral_wrap_pass():
    result = DeterministicAnswerScorer().score(
        _generic_task(),
        _traj({
            "label": "right",
            "distance": 2.1,
            "vector": [11.0, 19.0, 30.5],
            "dihedral_deg": -179.0,
        }),
    )
    assert result.value == pytest.approx(1.0)
    assert result.raw["passed_items"] == 4


def test_generic_partial_credit_and_wrong_answer():
    result = DeterministicAnswerScorer().score(
        _generic_task(),
        _traj({
            "label": "left",
            "distance": 2.09,
            "vector": [50.0, 20.0, 30.0],
            "dihedral_deg": -179.0,
        }),
    )
    assert result.value == pytest.approx(0.5)


@pytest.mark.parametrize(
    "answer,parse_error",
    [
        (None, "missing_answer"),
        ('{"label":"right"}', "answer_fields_mismatch"),
        (
            '{"label":"right","distance":2,"vector":[10,20,30],'
            '"dihedral_deg":179,"extra":1}',
            "unexpected_answer_fields",
        ),
    ],
)
def test_missing_or_contract_shape_mismatch_fails_closed(answer, parse_error):
    result = DeterministicAnswerScorer().score(_generic_task(), _traj(answer))
    assert result.value == 0.0
    assert result.raw["parse_error"] == parse_error


def test_generic_canonical_atom_identity_accepts_aliases_but_not_wrong_atom():
    gt = {
        "atom_id": {
            "chain_id": "A", "comp_id": "GLY", "seq_id": 7,
            "atom_id": "CA", "icode": "", "altloc": "",
        }
    }
    task = _task(
        fields=["atom_id"],
        ground_truth=gt,
        config={
            "schema_version": GENERIC_RUBRIC_VERSION,
            "strict_json_object": True,
            "fields": [{"field": "atom_id", "comparison": "exact"}],
        },
    )
    equivalent = {
        "atom_id": {
            "chain": "a", "residue_name": "gly", "residue_number": "7",
            "atom_name": "ca", "insertion_code": "", "altloc": "",
        }
    }
    assert DeterministicAnswerScorer().score(task, _traj(equivalent)).value == 1.0
    equivalent["atom_id"]["atom_name"] = "CB"
    assert DeterministicAnswerScorer().score(task, _traj(equivalent)).value == 0.0


def test_required_gate_zeroes_otherwise_partial_canonical_rubric():
    task = _task(
        fields=["required", "optional"],
        ground_truth={"required": "yes", "optional": 5.0},
        primary=SCORER_NAME,
        config={
            "rubric_version": RUBRIC_VERSION,
            "required_gate": True,
            "rubric": [
                {
                    "id": "required", "pred_path": "required", "gt_path": "required",
                    "comparator": "canonical_string_equal", "weight": 1, "required": True,
                },
                {
                    "id": "optional", "pred_path": "optional", "gt_path": "optional",
                    "comparator": "abs_tolerance", "tolerance": 0.1,
                    "weight": 1, "required": False,
                },
            ],
        },
    )
    result = DeterministicAnswerScorer().score(
        task, _traj({"required": "no", "optional": 5.0})
    )
    assert result.raw["ungated_score"] == pytest.approx(0.5)
    assert result.value == 0.0
