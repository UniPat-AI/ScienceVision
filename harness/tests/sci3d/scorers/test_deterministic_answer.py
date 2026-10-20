from __future__ import annotations

from types import SimpleNamespace as NS
import json

import pytest

from sci3d_bench.scorers.deterministic_answer import (
    AnswerParseError,
    DeterministicAnswerScorer,
    parse_strict_answer,
)


def _task(value, fields, *, primary="exact_match", **config):
    scorer_config = {"primary": primary, **config}
    return NS(
        ground_truth=None if value is None else NS(value=value),
        answer_schema=NS(kind="structured", fields=fields),
        scorer=NS(primary=primary, secondary=(), config=scorer_config),
    )


def _score(task, answer):
    return DeterministicAnswerScorer().score(task, NS(final_answer=answer))


def test_strict_parser_accepts_exact_json_and_unique_marker():
    assert parse_strict_answer('{"laterality":"left"}') == {"laterality": "left"}
    assert parse_strict_answer('analysis\nSCI3D_FINAL_JSON: {"laterality":"left"}') == {
        "laterality": "left"
    }


@pytest.mark.parametrize(
    "answer,code",
    [
        ("the answer is left", "invalid_json"),
        ("```json\n{\"x\": 1}\n```", "invalid_json"),
        ("SCI3D_FINAL_JSON: {}\nSCI3D_FINAL_JSON: {}", "multiple_final_json_markers"),
        ('{"x": NaN}', "non_finite_number"),
    ],
)
def test_strict_parser_rejects_prose_fences_duplicates_and_nan(answer, code):
    with pytest.raises(AnswerParseError) as exc:
        parse_strict_answer(answer)
    assert exc.value.code == code


def test_exact_fields_are_binary_and_weighted():
    result = _score(
        _task({"laterality": "left", "z": 4}, ["laterality", "z"]),
        {"laterality": "LEFT", "z": 3},
    )
    assert result.value == pytest.approx(0.5)
    assert [item["passed"] for item in result.raw["verdicts"]] == [True, False]


def test_free_text_substring_never_gets_credit():
    result = _score(
        _task({"laterality": "left"}, ["laterality"]),
        "The lesion is on the left.",
    )
    assert result.value == 0.0
    assert result.raw["parse_error"] == "invalid_json"


def test_answer_schema_rejects_scalar_and_extra_fields():
    task = _task({"laterality": "left"}, ["laterality"])
    scalar = _score(task, "\"left\"")
    extra = _score(task, {"laterality": "left", "explanation": "seen in image"})
    assert scalar.value == extra.value == 0.0
    assert scalar.raw["parse_error"] == "answer_not_object"
    assert extra.raw["parse_error"] == "unexpected_answer_fields"


def test_numeric_and_vector_tolerance_boundaries():
    binding = _task(
        {"contact_residue": "A/GLY43", "contact_distance_A": 2.7},
        ["contact_residue", "contact_distance_A"],
        primary="tolerance_match",
        tolerance={"distance_A": 0.5},
    )
    assert _score(binding, {"contact_residue": "GLY 43 chain A", "contact_distance_A": 3.2}).value == 1.0
    assert _score(binding, {"contact_residue": "A/GLY43", "contact_distance_A": 3.2001}).value == 0.5

    extent = _task(
        {"extent_vox": [20, 37, 25]},
        ["extent_vox"],
        primary="tolerance_match",
        tolerance={"extent_vox": 3},
    )
    assert _score(extent, {"extent_vox": [17, 40, 28]}).value == 1.0
    assert _score(extent, {"extent_vox": [17, 40, 29]}).value == 0.0


def test_circular_angle_wraparound():
    task = _task(
        {"dihedral_deg": 355.0},
        ["dihedral_deg"],
        primary="tolerance_match",
        tolerance={"dihedral_deg": 15},
    )
    assert _score(task, {"dihedral_deg": 5.0}).value == 1.0
    assert _score(task, {"dihedral_deg": 20.1}).value == 0.0


def test_canonical_asset_and_space_group_comparators():
    task = _task(
        {"structure_with_shortest_contact": "biology:10OR", "spacegroup_hm": "P n a 21"},
        ["structure_with_shortest_contact", "spacegroup_hm"],
    )
    result = _score(
        task,
        {"structure_with_shortest_contact": "10or.cif", "spacegroup_hm": "PNA21"},
    )
    assert result.value == 1.0


def test_space_group_aliases_use_crystallographic_canonicalization():
    pytest.importorskip("gemmi")
    task = _task({"spacegroup_hm": "P 1 21/c 1"}, ["spacegroup_hm"])
    assert _score(task, {"spacegroup_hm": "P21/c"}).value == 1.0
    # Origin choices are not presentation aliases and must remain distinct.
    setting = _task({"spacegroup_hm": "F d d d :2"}, ["spacegroup_hm"])
    assert _score(setting, {"spacegroup_hm": "F d d d :1"}).value == 0.0


def test_invalid_explicit_rubric_is_an_ineligible_task_contract():
    task = _task(
        {"laterality": "left"},
        ["laterality"],
        rubric=[{
            "id": "bad",
            "pred_path": "unknown",
            "gt_path": "missing",
            "comparator": "canonical_string_equal",
            "weight": float("nan"),
        }],
    )
    result = _score(task, {"laterality": "left"})
    assert result.raw["eligible"] is False
    assert set(result.raw["errors"]) == {
        "invalid_pred_path:bad",
        "invalid_gt_path:bad",
        "invalid_rubric_weight:bad",
    }


def test_explicit_rubric_rejects_non_object_items():
    task = _task(
        {"laterality": "left"},
        ["laterality"],
        rubric=[{
            "id": "laterality",
            "pred_path": "laterality",
            "gt_path": "laterality",
            "comparator": "canonical_enum_equal",
            "weight": 1.0,
        }, "silently ignored before this regression"],
    )
    result = _score(task, {"laterality": "left"})
    assert result.raw["eligible"] is False
    assert result.raw["errors"] == ["invalid_rubric_item_type"]


def test_missing_gt_and_schema_mismatch_are_ineligible():
    missing = _score(_task(None, ["plausible"], primary="expert_rubric"), {"plausible": True})
    mismatch = _score(_task({"extent_vox": [1, 2, 3]}, ["extent_vox", "principal_axis"]), {})
    assert missing.raw["eligible"] is False
    assert mismatch.raw["eligible"] is False
    assert mismatch.raw["errors"] == ["answer_schema_ground_truth_fields_mismatch"]


def test_score_core_default_path_never_builds_llm(tmp_path, monkeypatch):
    import harbor_adapters.sci3d._scorer_registry as registry
    from harbor_adapters.sci3d._score_core import score_rewards

    monkeypatch.setattr(
        registry,
        "_build_llm",
        lambda model=None: (_ for _ in ()).throw(AssertionError("network judge must not be built")),
    )
    (tmp_path / "sci3d_answer.json").write_text(
        json.dumps({"answer": {"laterality": "left"}})
    )
    rewards, audit = score_rewards(
        {
            "domain": "medical",
            "answer_schema": {"kind": "categorical", "fields": ["laterality"]},
            "ground_truth": {"value": {"laterality": "left"}},
            "scorer": {"primary": "exact_match", "secondary": []},
        },
        task_id="t1",
        instruction="q",
        agent_dir=tmp_path,
    )
    assert rewards["answer_score"] == rewards["reward"] == 1.0
    assert not any(key.startswith("shadow_") for key in rewards)
    assert audit["evaluation_eligible"] is True


def test_score_core_honors_explicit_ineligibility(tmp_path):
    from harbor_adapters.sci3d._score_core import score_rewards

    (tmp_path / "sci3d_answer.json").write_text(
        json.dumps({"answer": {"plausible": True}})
    )
    rewards, audit = score_rewards(
        {
            "evaluation_eligible": False,
            "evaluation_ineligible_reason": "ground_truth_unavailable",
            "answer_schema": {"kind": "structured", "fields": ["plausible"]},
            "ground_truth": {"value": {"plausible": True}},
            "scorer": {"primary": "exact_match", "secondary": []},
        },
        task_id="docking",
        instruction="q",
        agent_dir=tmp_path,
    )
    assert rewards["reward"] == 0.0
    assert "answer_score" not in rewards
    assert audit["evaluation_eligible"] is False
    assert audit["eligibility_error"] == "ground_truth_unavailable"
