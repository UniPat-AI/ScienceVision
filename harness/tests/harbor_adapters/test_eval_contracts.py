from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from harbor_adapters.sci3d._verify_translate import task_metadata_to_taskspec
from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer
from scripts.harbor.build_harbor_tasks import _build_metadata
from scripts.harbor.eval_contracts import audit_jsonl, normalize_eval_contract
from scripts.harbor.qa_gen_to_tasks import qa_record_to_task


_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE50 = (
    _ROOT
    / "QA-Gen/tasks/scale-v1-sample50/ScienceVision_scale-v1-sample50_final_tasks.jsonl"
)


def _records():
    return [json.loads(line) for line in _SAMPLE50.read_text().splitlines() if line.strip()]


def test_sample50_contract_audit_has_130_deterministic_and_20_ineligible():
    audit = audit_jsonl(_SAMPLE50)
    assert audit == {
        "records": 150,
        "eligible": 130,
        "ineligible": 20,
        "findings": [],
    }


def test_every_eligible_sample50_gt_scores_as_a_perfect_answer():
    scorer = DeterministicAnswerScorer()
    scored = 0
    for raw in _records():
        task = qa_record_to_task(raw, asset_root=_ROOT)
        if not task["evaluation_eligible"]:
            continue
        metadata = _build_metadata(task)
        spec = task_metadata_to_taskspec(
            metadata,
            task_id=task["task_id"],
            instruction=task["instruction"],
        )
        result = scorer.score(
            spec,
            SimpleNamespace(final_answer=metadata["ground_truth"]["value"]),
        )
        assert result.raw["eligible"] is True, task["task_id"]
        assert result.value == 1.0, task["task_id"]
        scored += 1
    assert scored == 130


def test_known_contract_repairs_are_applied_without_inventing_gt():
    records = _records()
    laterality = normalize_eval_contract(next(r for r in records if "med_ct_lesion" in r["task_id"]))
    mri = normalize_eval_contract(next(r for r in records if "med_mri_anatomy" in r["task_id"]))
    clash = normalize_eval_contract(next(r for r in records if "bio_collision_check" in r["task_id"]))
    conformer = normalize_eval_contract(next(r for r in records if "chem_conformation" in r["task_id"]))

    assert laterality["answer_schema"]["enum"] == ["left", "right", "midline"]
    assert "midline" in laterality["instruction"]
    assert mri["answer_schema"]["fields"] == ["extent_vox"]
    assert "atom pair" not in clash["instruction"]
    assert conformer["ground_truth"]["value"] == {"conformer_class": "eclipsed"}
    assert "angle" not in conformer["instruction"]


def test_multi_asset_record_survives_qa_translation():
    raw = next(r for r in _records() if "multi_structure_compare" in r["task_id"])
    task = qa_record_to_task(raw, asset_root=_ROOT)
    assert len(task["assets"]) == 2
    assert {item["asset_id"] for item in task["assets"]} == {
        raw["assets"][0]["asset_id"], raw["assets"][1]["asset_id"]
    }


def test_full_scorer_configuration_is_preserved_in_metadata():
    raw = next(r for r in _records() if "chem_binding_geometry" in r["task_id"])
    task = qa_record_to_task(raw, asset_root=_ROOT)
    metadata = _build_metadata(task)
    # `tolerance` is the one key that is not copied through verbatim. This record
    # declares `distance_A`, a field the task does not have, so that declaration
    # is inert in the scorer and is replaced by the closed-loop band derived for
    # the field it does have -- `contact_distance_A`, a float measurement, which
    # grades at +/- 0.005. Everything else is preserved exactly.
    preserved = {key: value for key, value in raw["scorer"].items() if key != "tolerance"}
    assert {key: metadata["scorer"][key] for key in preserved} == preserved
    assert metadata["scorer"]["secondary"] == []
    assert metadata["scorer"]["checks"] == ["annotation_distance", "annotation_iou"]
    assert metadata["scorer"]["tolerance"] == {"contact_distance_A": 0.005}
    assert metadata["weights"] == {"answer": 1.0, "process": 0.0}


def test_docking_is_explicitly_ineligible_without_expert_gt():
    raw = next(r for r in _records() if "bio_docking_plausibility" in r["task_id"])
    task = qa_record_to_task(raw, asset_root=_ROOT)
    assert task["evaluation_eligible"] is False
    assert task["evaluation_ineligible_reason"] == "ground_truth_unavailable"
