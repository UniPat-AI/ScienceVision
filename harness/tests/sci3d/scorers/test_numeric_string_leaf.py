"""A measurement stored as a string must still compare as a measurement."""
import pytest

from sci3d_bench.scorers.deterministic_answer import (
    _compare_numeric_literal,
    _default_comparator,
    _numeric_literal,
)


def test_numeric_literal_accepts_a_bare_number():
    assert _numeric_literal("20") == 20
    assert _numeric_literal(" 7.257756 ") is not None


def test_numeric_literal_rejects_a_partial_parse():
    assert _numeric_literal("4500 x 2100 mm") is None
    assert _numeric_literal("Article 4.2.10") is None
    assert _numeric_literal("") is None
    assert _numeric_literal("nan") is None
    assert _numeric_literal("inf") is None


@pytest.mark.parametrize(
    "field,expected",
    [
        ("damage_depth_mm", "20"),
        ("total_panel_count", "4"),
        ("longest_continuous_extent_mm", "12200"),
    ],
)
def test_a_numeric_string_routes_to_a_numeric_comparator(field, expected):
    comparator, _ = _default_comparator(field, expected, {})
    assert comparator == "numeric_literal_equal"


def test_a_spec_string_still_routes_to_string_equality():
    comparator, _ = _default_comparator(
        "dominant_panel_size_mm", "4500 x 2100 mm, 3000 x 2100 mm", {}
    )
    assert comparator == "canonical_string_equal"


def test_a_named_entity_field_keeps_its_own_comparator():
    # The numeric branch must not steal a field whose name already selects a
    # canonicaliser that knows more than "this is a number".
    comparator, _ = _default_comparator("ligand_atom_id", "20", {})
    assert comparator == "canonical_atom_id_equal"


def test_a_count_gets_no_band_and_a_measurement_keeps_the_one_declared():
    _, no_band = _default_comparator("damage_depth_mm", "20", {})
    assert no_band is None
    _, declared = _default_comparator("damage_depth_mm", "20", {"tolerance": 0.005})
    assert declared == 0.005


def test_a_string_ground_truth_matches_a_numeric_answer_and_vice_versa():
    # The motivating case, in both directions.
    assert _compare_numeric_literal("20", "20", {})
    assert _compare_numeric_literal(20, "20", {})
    assert _compare_numeric_literal("20", 20, {})
    assert not _compare_numeric_literal(21, "20", {})
    assert not _compare_numeric_literal("4500 x 2100 mm", 4500, {})


# Added beyond the brief's list: the routing tests above only assert the
# comparator *name* and its payload, which the pre-change code also produced
# (``abs_tolerance`` carried the same tolerance). These exercise the comparison
# itself, so they fail if the band stops being honoured.
def test_the_declared_band_is_honoured_on_a_string_ground_truth():
    assert _compare_numeric_literal("20", 20.004, {"tolerance": 0.005})
    assert _compare_numeric_literal(20.004, "20", {"tolerance": 0.005})
    assert not _compare_numeric_literal("20", 20.02, {"tolerance": 0.005})
    assert not _compare_numeric_literal("20", 20.004, {})  # no band means exact
