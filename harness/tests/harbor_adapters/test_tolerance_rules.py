"""The per-field tolerance derivation, and the traps it has to avoid.

Each of the three properties named in the module's docstring is a *silent*
failure in the scorer, so each gets a test that would pass if the property were
dropped and the field were simply given a number.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from sci3d_bench.scorers.deterministic_answer import (
    DeterministicAnswerScorer,
    _default_comparator,
)
from scripts.harbor._tolerance_rules import (
    derived_tolerances,
    merge_tolerances,
    tolerance_for,
)


# ── the closed-loop band ─────────────────────────────────────────────────── #


@pytest.mark.parametrize(
    ("field", "ground_truth", "expected"),
    [
        # The closed-loop wording (expert feedback, 2026-09-23): use exact
        # match, let the third decimal place differ -- implemented as +-0.005
        # for every float measurement, whatever its written precision.
        ("contact_distance_A", 2.755, 0.005),
        ("distance_A", 2.65, 0.005),
        ("minimum_absolute_dihedral_deg", 76.42, 0.005),
        ("minimum_C_C_C_bond_angle_deg", 79.5, 0.005),
        ("farthest_heavy_atom_distance_A", 6.024, 0.005),
        ("steepest_pitch_deg", 12.0, 0.005),
    ],
)
def test_the_band_is_the_closed_loop_five_thousandths(
    field: str, ground_truth: float, expected: float
) -> None:
    assert tolerance_for(field, ground_truth) == pytest.approx(expected)


# ── property 1: a count is never loosened ────────────────────────────────── #


@pytest.mark.parametrize("field", ["sp_count", "sp2_count", "sp3_count"])
def test_a_count_is_left_without_a_tolerance(field: str) -> None:
    """Declaring one would swap `integer_equal` for a float `abs_tolerance`."""
    assert tolerance_for(field, 4) is None


def test_the_slice_index_is_the_one_integer_that_takes_a_tolerance() -> None:
    assert tolerance_for("representative_axial_index", 65) == pytest.approx(1.0)


def test_a_laterality_label_and_a_midline_flag_get_nothing() -> None:
    assert tolerance_for("laterality", "right") is None
    assert tolerance_for("crosses_fov_midline", False) is None


# ── property 2: a numeric list must get one or the task fails closed ─────── #


def test_a_voxel_triple_is_graded_one_voxel_per_axis() -> None:
    assert tolerance_for("point_vox", [71, 94, 53]) == pytest.approx(1.0)
    assert tolerance_for("bbox_min_vox", [97, 152, 77]) == pytest.approx(1.0)
    assert tolerance_for("bbox_max_vox_inclusive", [114, 139, 411]) == pytest.approx(1.0)


def test_an_extent_is_graded_two_voxels_for_its_two_corners() -> None:
    assert tolerance_for("extent_vox", [37, 28, 83]) == pytest.approx(2.0)


def test_an_identity_list_is_graded_at_zero_not_left_open() -> None:
    assert tolerance_for("sp2_atom_ids", [6, 7]) == pytest.approx(0.0)


def test_a_measurement_vector_is_banded_component_by_component() -> None:
    """Not zero — `[2530, 2481]` is a pair of readings, not a pair of names."""
    assert tolerance_for("void_ladder_bottom_up_mm", [2530, 2481]) == [1.0, 1.0]
    assert tolerance_for("defect_offsets_m", [1.25, 3.5]) == pytest.approx([0.005, 0.005])


# ── property 3: what cannot be compared numerically is left alone ─────────── #


def test_a_string_list_is_left_alone_rather_than_made_quietly_unsatisfiable() -> None:
    """A tolerance would compile cleanly and then fail every comparison."""
    assert tolerance_for("screenshots", ["front", "side", "oblique"]) is None


def test_an_extent_written_as_range_strings_is_left_alone() -> None:
    assert tolerance_for("extent_vox", ["74-119", "105-157", "179-238"]) is None


def test_a_mixed_list_is_left_alone() -> None:
    assert tolerance_for("screenshots", ["front", 2]) is None


# ── a number stored as a string is still a number (carried finding R12) ──── #


@pytest.mark.parametrize(
    ("field", "ground_truth"),
    [
        ("contact_distance_A", "2.755"),
        ("steepest_pitch_deg", "12.0"),
        ("farthest_heavy_atom_distance_A", "6.024"),
    ],
)
def test_a_measurement_stored_as_a_string_takes_the_closed_loop_band(
    field: str, ground_truth: str
) -> None:
    assert tolerance_for(field, ground_truth) == pytest.approx(0.005)


def test_the_storage_form_of_a_measurement_does_not_decide_its_band() -> None:
    """`"2.755"` and `2.755` are one quantity, and the scorer grades both as
    numbers, so both must be banded the same way."""
    for field, number in [("contact_distance_A", 2.755), ("steepest_pitch_deg", 12.0)]:
        assert tolerance_for(field, number) == tolerance_for(field, str(number))


def test_a_count_stored_as_a_string_is_still_left_without_a_band() -> None:
    """Property 1 is not weakened by the string form: a band here would swap
    the numeric comparator's exactness for a float `abs_tolerance`."""
    assert tolerance_for("sp_count", "4") is None


def test_the_one_integer_that_takes_a_band_takes_it_as_a_string_too() -> None:
    assert tolerance_for("representative_axial_index", "65") == pytest.approx(1.0)


def test_a_non_numeric_string_still_gets_nothing() -> None:
    assert tolerance_for("laterality", "right") is None
    assert tolerance_for("dominant_panel_size_mm", "4500 x 2100 mm") is None
    assert tolerance_for("cited_clause", "Article 4.2.10 of JGJ 100-2015") is None
    assert tolerance_for("empty", "") is None


def test_a_bool_still_gets_nothing() -> None:
    assert tolerance_for("crosses_fov_midline", False) is None
    assert tolerance_for("crosses_fov_midline", True) is None
    # The discriminating pair: `bool` is a subclass of `int`, so without the
    # early return a `True` on the one field that takes an integer band would
    # be handed that band. Both spellings above return None either way.
    assert tolerance_for("representative_axial_index", True) is None
    assert tolerance_for("representative_axial_index", False) is None


def test_a_numeric_string_measurement_reaches_the_derived_map() -> None:
    derived = derived_tolerances({"steepest_pitch_deg": "12.0", "sp_count": "4"})
    assert derived == {"steepest_pitch_deg": pytest.approx(0.005)}


def test_a_scalar_on_a_voxel_field_fails_closed_instead_of_raising() -> None:
    """`_voxel_tolerance` reads a triple, so a lone number must not reach it.

    The float case already raised before the string form existed; the string
    form reaches the same branch now that a numeric string is parsed."""
    assert tolerance_for("point_vox", 20.0) is None
    assert tolerance_for("point_vox", "20.0") is None
    assert tolerance_for("extent_vox", "20.0") is None
    assert tolerance_for("bbox_min_vox", "1e2") is None
    # A string that parses to an int takes the integer branch above and stays
    # safe there, so it is not the same case.
    assert tolerance_for("point_vox", "20") is None


# ── the map as a whole ───────────────────────────────────────────────────── #


def test_the_map_is_keyed_by_the_field_name_verbatim() -> None:
    """`_tolerance_for` looks a field up by string, so casing must survive."""
    derived = derived_tolerances({"Farthest_Heavy_Atom_Distance_A": 6.024})
    assert derived == {"Farthest_Heavy_Atom_Distance_A": pytest.approx(0.005)}


def test_a_ground_truth_that_is_not_a_mapping_derives_nothing() -> None:
    assert derived_tolerances(None) == {}
    assert derived_tolerances("prose answer") == {}


def test_a_chemistry_conformation_row_derives_only_its_measured_field() -> None:
    derived = derived_tolerances(
        {"sp_count": 4, "sp2_count": 5, "sp3_count": 4, "minimum_absolute_dihedral_deg": 76.42}
    )
    assert derived == {"minimum_absolute_dihedral_deg": pytest.approx(0.005)}


# ── merging with what the record already declared ────────────────────────── #


def test_a_declared_tolerance_on_a_real_field_wins() -> None:
    merged = merge_tolerances({"extent_vox": 3}, {"extent_vox": [37, 28, 83]})
    assert merged == {"extent_vox": pytest.approx(3.0)}


def test_a_declared_tolerance_naming_no_field_is_dropped() -> None:
    """`distance_A` is inert on a task whose field is `contact_distance_A`."""
    merged = merge_tolerances({"distance_A": 0.8}, {"contact_distance_A": 2.755})
    assert merged == {"contact_distance_A": pytest.approx(0.005)}


def test_merging_does_not_mutate_its_inputs() -> None:
    declared = {"extent_vox": 3}
    ground_truth = {"extent_vox": [37, 28, 83], "laterality": "right"}
    merge_tolerances(declared, ground_truth)
    assert declared == {"extent_vox": 3}
    assert ground_truth == {"extent_vox": [37, 28, 83], "laterality": "right"}


# ── end to end through the scorer that actually grades these tasks ───────── #


def _task(ground_truth: dict, tolerance: dict) -> SimpleNamespace:
    """A task on the derived path: `exact_match` is a legacy alias, not the
    generic-rubric name, so `_compile_rubric` derives its items from the ground
    truth and consults `config["tolerance"]` for each one."""
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=sorted(ground_truth)),
        ground_truth=SimpleNamespace(value=ground_truth),
        scorer=SimpleNamespace(
            primary="exact_match",
            config={"primary": "exact_match", "secondary": [], "tolerance": tolerance},
        ),
    )


def _score(task: SimpleNamespace, answer: dict):
    return DeterministicAnswerScorer().score(task, SimpleNamespace(final_answer=answer))


def test_a_derived_map_compiles_a_float_and_a_list_task_without_contract_errors() -> None:
    ground_truth = {"contact_distance_A": 2.755, "point_vox": [71, 94, 53]}
    task = _task(ground_truth, derived_tolerances(ground_truth))
    result = _score(task, ground_truth)
    assert result.raw["eligible"] is True, result.raw.get("errors")
    assert result.value == pytest.approx(1.0)


def test_an_answer_inside_the_band_passes_and_one_outside_it_does_not() -> None:
    ground_truth = {"contact_distance_A": 2.755}
    task = _task(ground_truth, derived_tolerances(ground_truth))
    assert _score(task, {"contact_distance_A": 2.76}).value == pytest.approx(1.0)
    assert _score(task, {"contact_distance_A": 2.761}).value == pytest.approx(0.0)
    assert _score(task, {"contact_distance_A": 2.8}).value == pytest.approx(0.0)


def test_a_voxel_answer_may_miss_by_one_voxel_per_axis_but_not_two() -> None:
    ground_truth = {"point_vox": [71, 94, 53]}
    task = _task(ground_truth, derived_tolerances(ground_truth))
    assert _score(task, {"point_vox": [72, 93, 54]}).value == pytest.approx(1.0)
    assert _score(task, {"point_vox": [73, 94, 53]}).value == pytest.approx(0.0)


def test_a_dihedral_on_the_far_side_of_the_seam_still_scores() -> None:
    """The derived band rides `circular_tolerance`, whose modulus defaults to 360."""
    ground_truth = {"dihedral_deg": -155.26}
    task = _task(ground_truth, derived_tolerances(ground_truth))
    assert _score(task, {"dihedral_deg": 204.74}).value == pytest.approx(1.0)


def test_the_record_to_task_wiring_puts_a_band_on_a_list_field() -> None:
    """The smoke16 failure mode: a list field with no tolerance is not scored
    strictly, it is `invalid_task_contract`. This is the wiring that fixes it.

    Skipped where the Harbor harness is only partly mirrored: `qa_gen_to_tasks`
    imports `build_harbor_tasks`, which needs `sci3d_agent.mcp.parity`.
    """
    qa_gen_to_tasks = pytest.importorskip("scripts.harbor.qa_gen_to_tasks")

    task = qa_gen_to_tasks.qa_record_to_task(
        {
            "task_id": "sv-medi-probe-0001",
            "domain": "medical",
            "instruction": "Return one voxel inside the lesion.",
            "answer_schema": {"kind": "structured", "fields": ["point_vox"]},
            "ground_truth": {"value": {"point_vox": [71, 94, 53]}},
            "scorer": {"primary": "exact_match", "secondary": []},
            "asset": {"path": "/x/ct.nii.gz", "format": "nii.gz"},
            "evaluation_eligible": True,
        }
    )
    assert task["scorer"]["tolerance"] == {"point_vox": pytest.approx(1.0)}


def test_a_count_answered_exactly_still_scores_after_the_map_is_derived() -> None:
    ground_truth = {"sp_count": 4}
    task = _task(ground_truth, derived_tolerances(ground_truth))
    assert derived_tolerances(ground_truth) == {}
    assert _score(task, {"sp_count": 4}).value == pytest.approx(1.0)
    assert _score(task, {"sp_count": 5}).value == pytest.approx(0.0)


def test_an_angle_leaf_stored_as_a_string_is_graded_as_an_angle() -> None:
    """The ``*_angle_deg`` branch owns such a field, so a numeric string has to
    survive both the derivation and the comparison. Before the carried fix the
    ground truth could not be parsed at all and the task scored 0.0 whatever the
    agent answered."""
    ground_truth = {"minimum_C_C_C_bond_angle_deg": "79.5"}
    task = _task(ground_truth, derived_tolerances(ground_truth))
    assert _score(task, {"minimum_C_C_C_bond_angle_deg": 79.5}).value == pytest.approx(1.0)
    assert _score(task, {"minimum_C_C_C_bond_angle_deg": 79.503}).value == pytest.approx(1.0)
    assert _score(task, {"minimum_C_C_C_bond_angle_deg": 79.6}).value == pytest.approx(0.0)


def test_the_a03_angle_field_is_graded_numerically_not_circularly() -> None:
    """``column_alignment_angles_deg`` is plural, so it does not match the
    ``_angle_deg`` suffix and never reaches the circular comparator: it routes to
    ``numeric_literal_equal``, which Task 1 already taught to read a numeric
    string. Recorded, because the carried finding names this field as the
    circular comparator's reach."""
    comparator, _ = _default_comparator("column_alignment_angles_deg", "180", {})
    assert comparator == "numeric_literal_equal"
