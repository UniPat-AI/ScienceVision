"""Aliases widen a controlled vocabulary by mechanical derivation only."""
from types import SimpleNamespace

import pytest

from sci3d_bench.scorers.deterministic_answer import (
    DeterministicAnswerScorer,
    _compare_circular_tolerance,
    _compare_string_in_set,
    _compile_rubric,
    derive_aliases,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("cylindrical (rotation plus translation)", ["cylindrical"]),
        ("revolute (rotation only)", ["revolute"]),
        ("M1c (peritoneal metastasis)", ["M1c"]),
        ("D. rotates counter-clockwise by 90 degrees", ["D"]),
        ("protein_cleft_or_pocket", []),
        ("Article 4.2.10 of JGJ 100-2015", []),
        # A sentence is a proposition, not a formatting variant: dropping the
        # parenthetical would accept an answer that omits what it says.
        ("O-H...O hydrogen bonds (C-H...O secondary)", []),
        ("steric clash with the aromatic wall (Trp286/Trp86/Tyr341)", []),
        ("propagates along all three axes (a, b, c)", []),
        ("only a geometry-based roughness/curvature anomaly proxy (no defect)", []),
    ],
)
def test_derivation_is_mechanical_and_conservative(value, expected):
    assert derive_aliases(value) == expected


def test_a_derived_alias_matches():
    assert _compare_string_in_set(
        "cylindrical", "cylindrical (rotation plus translation)",
        {"accepted": ["cylindrical"]},
    )


def test_case_and_whitespace_still_normalise():
    assert _compare_string_in_set("  CYLINDRICAL ", "cylindrical", {"accepted": []})


def test_a_different_answer_is_still_wrong():
    assert not _compare_string_in_set("spherical", "cylindrical", {"accepted": ["cylindrical"]})


def test_no_accepted_key_falls_back_to_plain_equality():
    assert not _compare_string_in_set("cylindrical", "cylindrical (rotation plus translation)", {})


@pytest.mark.parametrize("accepted", [None, "cylindrical", 3, {"cylindrical": True}])
def test_a_malformed_accepted_value_fails_closed_rather_than_raising(accepted):
    """An explicit rubric item reaches the comparator verbatim from the config,
    so anything but a list must be ignored, not iterated."""
    item = {"accepted": accepted}
    assert not _compare_string_in_set("c", "cylindrical", item)
    assert _compare_string_in_set("cylindrical", "cylindrical", item)


# ── carried finding R10: the circular comparator owns the angle fields ────── #


def test_a_circular_measurement_stored_as_a_string_still_compares():
    item = {"tolerance": 0.005, "periodic_modulus": 360.0}
    assert _compare_circular_tolerance("180", 180.0, item)
    assert _compare_circular_tolerance(180.0, "180", item)
    assert not _compare_circular_tolerance("180", 100.0, item)


def test_the_circular_comparator_keeps_its_seam_and_its_band():
    """Normalising the string side must not cost the comparator its own
    semantics: the modulus-360 wrap and the declared band both still apply."""
    item = {"tolerance": 0.005, "periodic_modulus": 360.0}
    assert _compare_circular_tolerance("-180", 180.0, item)
    assert _compare_circular_tolerance("359.999", 0.0, item)
    assert not _compare_circular_tolerance("180", 179.9, item)
    assert not _compare_circular_tolerance("180", 180.0, {"tolerance": None})
    assert not _compare_circular_tolerance("prose", 180.0, item)


# ── the alias reaches a real task, and only where it is allowed to ────────── #


def _task(ground_truth: dict, **config) -> SimpleNamespace:
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=sorted(ground_truth)),
        ground_truth=SimpleNamespace(value=ground_truth),
        scorer=SimpleNamespace(
            primary="exact_match",
            config={"primary": "exact_match", "secondary": [], **config},
        ),
    )


def _rubric(ground_truth: dict, **config):
    return _compile_rubric(_task(ground_truth, **config), ground_truth)


def test_a_derived_alias_is_attached_to_the_compiled_item():
    ground_truth = {"motion_type": "cylindrical (rotation plus translation)"}
    rubric, errors, _ = _rubric(ground_truth)
    assert errors == []
    assert rubric[0]["comparator"] == "canonical_string_in_set"
    assert rubric[0]["accepted"] == ["cylindrical"]


def test_a_hand_written_alias_from_the_config_is_carried_through():
    ground_truth = {"motion_type": "prismatic"}
    rubric, errors, _ = _rubric(ground_truth, accepted={"motion_type": ["sliding", "translational"]})
    assert errors == []
    assert rubric[0]["comparator"] == "canonical_string_in_set"
    assert rubric[0]["accepted"] == ["sliding", "translational"]


def test_a_field_with_no_alias_keeps_plain_string_equality():
    ground_truth = {"protein_region": "protein_cleft_or_pocket"}
    rubric, errors, _ = _rubric(ground_truth, accepted={"protein_region": []})
    assert errors == []
    assert rubric[0]["comparator"] == "canonical_string_equal"
    assert "accepted" not in rubric[0]


def test_a_numeric_string_ground_truth_is_not_pulled_back_to_string_equality():
    """The guard is load-bearing. A count whose ground truth is written as a
    string is graded as a number; the declared alias must not flip it back to a
    string comparison, which would fail every agent that answered the number."""
    ground_truth = {"damage_depth_mm": "20"}
    rubric, errors, _ = _rubric(ground_truth, accepted={"damage_depth_mm": ["20 mm"]})
    assert errors == []
    assert rubric[0]["comparator"] == "numeric_literal_equal"
    assert "accepted" not in rubric[0]


def test_an_agent_answering_the_number_still_scores_when_an_alias_is_declared():
    """The same guard, seen from the outside."""
    ground_truth = {"damage_depth_mm": "20"}
    task = _task(ground_truth, accepted={"damage_depth_mm": ["20 mm"]})
    result = DeterministicAnswerScorer().score(
        task, SimpleNamespace(final_answer={"damage_depth_mm": 20})
    )
    assert result.raw["eligible"] is True, result.raw.get("errors")
    assert result.value == pytest.approx(1.0)


def test_a_non_string_ground_truth_survives_the_alias_pass():
    """``derive_aliases`` parses a string, so it must not even be called for a
    field whose ground truth is an int, a float or a list."""
    ground_truth = {"sp_count": 4, "distance_A": 2.755, "point_vox": [71, 94, 53]}
    rubric, errors, _ = _rubric(ground_truth, tolerance={"distance_A": 0.005, "point_vox": 1.0})
    assert errors == []
    assert [item["comparator"] for item in rubric] == [
        "integer_equal",
        "abs_tolerance",
        "vector_abs_tolerance",
    ]


def test_a_malformed_accepted_map_does_not_crash_and_derives_nothing_extra():
    """The contract type is ``dict[str, list[str]]``; a value of any other type
    is ignored rather than iterated, so no single letter of a mistyped string
    can become a candidate answer."""
    ground_truth = {"motion_type": "cylindrical"}
    rubric, errors, _ = _rubric(ground_truth, accepted={"motion_type": "cylindrical"})
    assert errors == []
    assert rubric[0]["comparator"] == "canonical_string_equal"
    assert "accepted" not in rubric[0]


def test_a_name_matched_field_is_not_given_aliases_even_when_one_derives():
    """The guard again, on a field that routes to its own canonicaliser."""
    ground_truth = {"has_interference": "true (the flag is set)"}
    rubric, errors, _ = _rubric(ground_truth)
    assert errors == []
    assert rubric[0]["comparator"] == "boolean_equal"
    assert "accepted" not in rubric[0]
