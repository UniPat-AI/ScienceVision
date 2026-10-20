from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from qagen.contracts import digest, make_envelope
from qagen.batch.live import LiveBatchError, _validate_completed_live_question_coverage
from qagen.paths import WorkspacePaths
from qagen.release.staging import ReleaseStageError, _validate_candidate_question_contract_for_staging
from qagen.storage.atomic import atomic_write_json, atomic_write_yaml
from qagen.verification import (
    CuratedSemanticGTSource,
    GroundTruthRegistry,
    GroundTruthRegistryError,
    QuestionContractError,
    load_curated_semantic_gt_source,
    load_ground_truth_registry,
    load_question_text_policy,
    load_workspace_feedback_policy,
    resolve_ground_truth_record_projection,
    validate_candidate_question_contract,
    validate_question_coverage,
    validate_solver_question_text,
)
from qagen.verification.feedback_policy import parse_feedback_policy


def _facets(dataset: str = "m3d_refseg") -> dict[str, list[str]]:
    return {
        "body_region": [],
        "organ_system": [],
        "anatomy": [],
        "pathology": [],
        "modality": ["ct"],
        "dataset": [dataset],
    }


def _approved_ground_truth_registry(
    tmp_path: Path,
    *,
    records: list[dict],
    identifier: str = "test-ground-truth-registry",
) -> GroundTruthRegistry:
    """Freeze a human-approved test registry before a candidate cites it."""

    document = {
        "schema_version": 1,
        "kind": "qagen_ground_truth_registry",
        "id": identifier,
        "source_status": "approved",
        "record_count": len(records),
        "approved_snapshot": {"records": records},
    }
    path = tmp_path / f"{identifier}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_ground_truth_registry(path)


def _registered_provenance(registry: GroundTruthRegistry, record: dict) -> dict:
    """Project one immutable registry record into candidate-private provenance."""

    fields = (
        "source_id",
        "source_digest",
        "record_id",
        "record_digest",
        "answer_digest",
        "measurement_receipt_digest",
        "anonymous_scene_ref",
        "primary_asset_digest",
        "selection_slot_digest",
    )
    provenance = {field: record[field] for field in fields if field in record}
    provenance["ground_truth_registry_digest"] = registry.content_digest
    return provenance


def _medical_ground_truth_record(snapshot: dict, annotation: dict) -> dict:
    indexed = snapshot["annotations"][0]
    return {
        "source_type": "curated_source_label",
        "source_id": "m3d_refseg_doctor_annotation",
        "source_digest": snapshot["source_digest"],
        "record_id": annotation["annotation_id"],
        "record_digest": indexed["annotation_digest"],
        "answer_digest": digest({"answer": "documented body region"}),
        "anonymous_scene_ref": indexed["anonymous_scene_ref"],
        "primary_asset_digest": indexed["primary_asset_digest"],
        "selection_slot_digest": indexed["selection_slot_digest"],
    }


def _approved_medical_inputs(
    tmp_path: Path,
) -> tuple[CuratedSemanticGTSource, dict, dict, GroundTruthRegistry]:
    source, annotation, snapshot = _approved_medical_gt_source(tmp_path)
    registry = _approved_ground_truth_registry(
        tmp_path,
        records=[_medical_ground_truth_record(snapshot, annotation)],
        identifier="medical-ground-truth-registry",
    )
    return source, annotation, snapshot, registry


def _validate_medical_candidate(
    candidate: dict,
    *,
    source: CuratedSemanticGTSource,
    registry: GroundTruthRegistry,
) -> dict:
    return validate_candidate_question_contract(
        candidate,
        policy=load_question_text_policy(),
        leaf_id="medical.lesion_localization",
        medical_gt_source=source,
        ground_truth_registry=registry,
        expected_ground_truth_registry_digest=registry.content_digest,
    )


def _approved_medical_gt_source(tmp_path: Path) -> tuple[CuratedSemanticGTSource, dict, dict]:
    annotation = {
        "annotation_id": "m3d-001",
        "finding": "focal abnormality",
        "location": "documented body region",
        "impression": "requires image-based localization",
    }
    source_digest = digest({"source": "m3d-refseg-approved-snapshot"})
    anchor_digest = digest({"anchor": "primary-image-finding"})
    expert_digest = digest({"review": "medical-expert-approved"})
    scene_ref = "scene://medical-contract-test"
    primary_asset_digest = digest({"asset": "medical-primary-image"})
    selection_slot_digest = digest({"selection_slot": "medical-contract-test"})
    source = {
        "schema_version": 1,
        "kind": "qagen_curated_semantic_gt_source",
        "id": "m3d_refseg_doctor_annotation",
        "source_status": "approved",
        "annotation_count": 1,
        "solver_projection": {
            "primary_image": True,
            "excluded_artifacts": ["segmentation", "mask", "overlay"],
        },
        "gt_contract": {
            "source_type": "curated_source_label",
            "required_annotation_fields": ["finding", "location", "impression"],
            "verbatim_provenance_required": True,
            "expert_approval_required": True,
        },
        "approved_snapshot": {
            "source_digest": source_digest,
            "expert_approval_digest": expert_digest,
            "annotations": [
                {
                    "annotation_id": annotation["annotation_id"],
                    "annotation_digest": digest(annotation),
                    "verbatim_provenance_digest": digest({"verbatim": "doctor annotation 001"}),
                    "semantic_anchor_digest": anchor_digest,
                    "anonymous_scene_ref": scene_ref,
                    "primary_asset_digest": primary_asset_digest,
                    "selection_slot_digest": selection_slot_digest,
                    "question_granularity": "body_region",
                }
            ],
        },
    }
    path = tmp_path / "medical-approved-source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return load_curated_semantic_gt_source(path), annotation, source["approved_snapshot"]


def _medical_candidate(
    *,
    mode: str = "semantic_interpretation_no_overlay",
    source: CuratedSemanticGTSource | None = None,
    annotation: dict | None = None,
    snapshot: dict | None = None,
    ground_truth_registry: GroundTruthRegistry | None = None,
) -> dict:
    policy = load_question_text_policy()
    source_digest = snapshot["source_digest"] if snapshot is not None else digest({"source": "m3d-refseg-annotation-001"})
    indexed = snapshot["annotations"][0] if snapshot is not None else {}
    anchor_digest = (
        indexed["semantic_anchor_digest"]
        if snapshot is not None
        else digest({"anchor": "primary-image-finding"})
    )
    expert_digest = snapshot["expert_approval_digest"] if snapshot is not None else digest({"review": "medical-expert"})
    annotation = annotation or {
        "annotation_id": "m3d-001",
        "finding": "focal abnormality",
        "location": "documented body region",
        "impression": "requires image-based localization",
    }
    scene_ref = indexed.get("anonymous_scene_ref", "scene://medical-contract-test")
    primary_asset_digest = indexed.get("primary_asset_digest", digest({"asset": "medical-primary-image"}))
    selection_slot_digest = indexed.get("selection_slot_digest", digest({"selection_slot": "medical-contract-test"}))
    ground_truth_record = {
        "source_type": "curated_source_label",
        "source_id": "m3d_refseg_doctor_annotation",
        "source_digest": source_digest,
        "record_id": annotation["annotation_id"],
        "record_digest": indexed.get("annotation_digest", digest(annotation)),
        "answer_digest": digest({"answer": "documented body region"}),
        "anonymous_scene_ref": scene_ref,
        "primary_asset_digest": primary_asset_digest,
        "selection_slot_digest": selection_slot_digest,
    }
    gt_provenance = (
        _registered_provenance(ground_truth_registry, ground_truth_record)
        if ground_truth_registry is not None
        else {
            **ground_truth_record,
            "ground_truth_registry_digest": digest({"missing": "ground-truth-registry"}),
        }
    )
    contract: dict = {
        "question_family_kind": "attribute_classification",
        "question_kernel": "localize documented focal finding by body region",
        "question_text_policy_digest": policy.content_digest,
        "gt_answer_contract": {
            "source_type": "curated_source_label",
            "gt_shape": "semantic_label",
            "answer_fields": ["answer"],
            "provenance": gt_provenance,
        },
        "asset_facets": _facets(),
        "medical_question_contract": {
            "clinical_task_kind": "lesion_localization",
            "approved_mode": mode,
            "semantic_scenario_binding": {
                "approval_status": "approved",
                "scenario_digest": digest({"scenario": "ct-localization"}),
                "semantic_anchor_id": "finding-001",
                "semantic_anchor_digest": anchor_digest,
                "clinical_semantic_anchor": "documented focal imaging finding",
            },
            "semantic_gt_binding": {
                "source_id": "m3d_refseg_doctor_annotation",
                "source_digest": source_digest,
                "source_registry_digest": source.content_digest if source is not None else digest({"missing": "source"}),
                "semantic_anchor_id": "finding-001",
                "semantic_anchor_digest": anchor_digest,
                "anonymous_scene_ref": scene_ref,
                "primary_asset_digest": primary_asset_digest,
                "selection_slot_digest": selection_slot_digest,
                "annotation_granularity": "body_region",
                "question_granularity": "body_region",
                "annotation": annotation,
                "verbatim_provenance_digest": (
                    indexed["verbatim_provenance_digest"]
                    if snapshot is not None
                    else digest({"verbatim": "doctor annotation 001"})
                ),
            },
            "semantic_scoring": {
                "semantic_anchor_digest": anchor_digest,
                "required_dimensions": ["localization", "clinical_interpretation"],
                "expert_approval_digest": expert_digest,
            },
            "solver_projection": {
                "primary_image": True,
                "excluded_artifacts": ["segmentation", "mask", "overlay"],
            },
            "label_identity_policy": "preserve",
            "primary_image_necessary": True,
            "shortcut_resistance": {
                "mask_only": False,
                "overlay_only": False,
                "metadata_only": False,
                "raw_compute": False,
            },
            "human_viewer_answerability": {
                "intended_solver": "clinical_expert",
                "viewer_precision_bound": "body-region localization only",
                "tolerance": {
                    "kind": "categorical",
                    "justification": "The annotation supports a body-region category, not a numeric measurement.",
                },
            },
        },
    }
    if mode == "localize_then_measure_no_overlay":
        contract["medical_question_contract"]["localize_then_measure"] = {
            "localization_before_measurement": True,
            "hidden_segmentation_author_reference_only": True,
            "measurement_subordinate": True,
            "manual_calibration_digest": digest({"calibration": "clinical-viewer-v1"}),
        }
        contract["medical_question_contract"]["human_viewer_answerability"]["tolerance"] = {
            "kind": "numeric",
            "absolute": 2.0,
            "unit": "mm",
            "justification": "The calibrated viewer supports a manually attainable tolerance after localization.",
        }
    return {
        "artifact_kind": "author_private_candidate",
        "task_id": "medical-contract-test",
        "prompt": "Which documented body region contains the focal finding visible in the primary image?",
        "anonymous_scene_ref": scene_ref,
        "viewer_session_request": {
            "request_id": "medical-contract-request",
            "viewer_profile_digest": digest({"viewer": "medical"}),
            "allowed_operations": ["inspect", "slice", "window_level"],
        },
        "reference_answer": {"answer": "documented body region"},
        "reasoning": "private semantic rationale",
        "rubric": {
            "semantic_scoring": deepcopy(contract["medical_question_contract"]["semantic_scoring"]),
        },
        "question_contract": contract,
        "provenance": {
            "plan_digest": digest({"plan": "medical"}),
            "config_digest": digest({"config": "medical"}),
            "knowledge_snapshot_digest": digest({"knowledge": "medical"}),
        },
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "Use the viewer to rotate the model and render it.",
        "Click the sidebar button and select the mask label.",
        "Select the target overlay.",
        "Inspect the highlighted mask.",
        "请点击工具栏中的旋转按钮。",
        "请使用查看器侧边栏选择遮罩标签。",
        "选择目标叠加层。",
        "检查高亮遮罩。",
    ],
)
def test_solver_question_text_rejects_english_and_chinese_ui_language(prompt: str) -> None:
    with pytest.raises(QuestionContractError, match="solver_facing_question_text_contains_ui_meta_language"):
        validate_solver_question_text(prompt, policy=load_question_text_policy())


def test_solver_question_text_does_not_reject_plain_scientific_axis_language() -> None:
    validate_solver_question_text(
        "Which molecular axis best explains the observed conformation?",
        policy=load_question_text_policy(),
    )


def test_nonfixture_candidate_cannot_bypass_a_required_contract() -> None:
    candidate = {
        "prompt": "Click the viewer button to reveal the overlay.",
        "reference_answer": {"answer": "private"},
    }
    with pytest.raises(QuestionContractError, match="solver_facing_question_text_contains_ui_meta_language"):
        validate_candidate_question_contract(
            candidate,
            policy=load_question_text_policy(),
            leaf_id="chemistry.conformation_analysis",
            require_contract=True,
        )


def test_missing_medical_semantic_gt_source_blocks_candidate() -> None:
    candidate = _medical_candidate()
    with pytest.raises(QuestionContractError, match="medical_semantic_gt_source_not_approved"):
        validate_candidate_question_contract(
            candidate,
            policy=load_question_text_policy(),
            leaf_id="medical.lesion_localization",
        )


def test_medical_semantic_interpretation_contract_is_accepted(tmp_path: Path) -> None:
    source, annotation, snapshot, registry = _approved_medical_inputs(tmp_path)
    candidate = _medical_candidate(
        source=source,
        annotation=annotation,
        snapshot=snapshot,
        ground_truth_registry=registry,
    )
    contract = _validate_medical_candidate(candidate, source=source, registry=registry)
    assert contract["medical_question_contract"]["approved_mode"] == "semantic_interpretation_no_overlay"


def test_medical_localize_then_measure_requires_hidden_mask_and_calibration(tmp_path: Path) -> None:
    source, annotation, snapshot, registry = _approved_medical_inputs(tmp_path)
    candidate = _medical_candidate(
        mode="localize_then_measure_no_overlay",
        source=source,
        annotation=annotation,
        snapshot=snapshot,
        ground_truth_registry=registry,
    )
    _validate_medical_candidate(candidate, source=source, registry=registry)

    candidate["question_contract"]["medical_question_contract"]["localize_then_measure"]["measurement_subordinate"] = False
    with pytest.raises(QuestionContractError, match="question_contract_schema_invalid"):
        _validate_medical_candidate(candidate, source=source, registry=registry)


def test_medical_rejects_decorative_labels_geometry_label_merges_and_overlay_ops(tmp_path: Path) -> None:
    source, annotation, snapshot, registry = _approved_medical_inputs(tmp_path)
    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    candidate["rubric"]["semantic_scoring"]["semantic_anchor_digest"] = digest({"other": "anchor"})
    with pytest.raises(QuestionContractError, match="decorative_medical_label"):
        _validate_medical_candidate(candidate, source=source, registry=registry)

    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    candidate["prompt"] = "What is the centroid of the visible target?"
    with pytest.raises(QuestionContractError, match="medical_shell_geometry_core_rejected"):
        _validate_medical_candidate(candidate, source=source, registry=registry)

    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    candidate["question_contract"]["gt_answer_contract"]["provenance"]["label_selector"] = "all>0"
    with pytest.raises(QuestionContractError, match="medical_label_identity_merge_rejected"):
        _validate_medical_candidate(candidate, source=source, registry=registry)

    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    candidate["viewer_session_request"]["allowed_operations"].append("overlay_compare")
    with pytest.raises(QuestionContractError, match="medical_solver_visible_overlay_or_mask_operation_rejected"):
        _validate_medical_candidate(candidate, source=source, registry=registry)


def test_medical_source_index_rejects_forged_annotation_or_source_digest(tmp_path: Path) -> None:
    source, annotation, snapshot, registry = _approved_medical_inputs(tmp_path)
    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    candidate["question_contract"]["medical_question_contract"]["semantic_gt_binding"]["source_digest"] = digest({"forged": "source"})
    with pytest.raises(QuestionContractError, match="medical_semantic_gt_source_digest_mismatch"):
        _validate_medical_candidate(candidate, source=source, registry=registry)

    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    candidate["question_contract"]["medical_question_contract"]["semantic_gt_binding"]["annotation"]["finding"] = "forged finding"
    with pytest.raises(QuestionContractError, match="medical_semantic_gt_annotation_binding_mismatch"):
        _validate_medical_candidate(candidate, source=source, registry=registry)


@pytest.mark.parametrize(
    "binding_field",
    ["anonymous_scene_ref", "primary_asset_digest", "selection_slot_digest"],
)
def test_medical_semantic_gt_is_bound_to_the_selected_scene(
    tmp_path: Path,
    binding_field: str,
) -> None:
    source, annotation, snapshot, registry = _approved_medical_inputs(tmp_path)
    candidate = _medical_candidate(
        source=source, annotation=annotation, snapshot=snapshot, ground_truth_registry=registry
    )
    replacement = (
        "scene://other-medical-contract-test"
        if binding_field == "anonymous_scene_ref"
        else digest({"forged": binding_field})
    )
    candidate["question_contract"]["medical_question_contract"]["semantic_gt_binding"][binding_field] = replacement
    if binding_field == "anonymous_scene_ref":
        candidate["anonymous_scene_ref"] = replacement
    with pytest.raises(QuestionContractError, match="medical_semantic_gt_annotation_binding_mismatch"):
        _validate_medical_candidate(candidate, source=source, registry=registry)


def _coverage_candidate(
    family: str,
    shape: str,
    dataset: str = "dataset-a",
    *,
    question_kernel: str | None = None,
    facets: dict[str, list[str]] | None = None,
) -> dict:
    return {
        "question_contract": {
            "question_family_kind": family,
            "question_kernel": question_kernel or f"{family} visible relationship",
            "gt_answer_contract": {"gt_shape": shape},
            "asset_facets": facets or _facets(dataset),
        }
    }


def _nonmedical_ground_truth_record() -> dict:
    return {
        "source_type": "rule_evaluated_structure",
        "source_id": "reviewed-structure-rule",
        "source_digest": digest({"structure": "reviewed"}),
        "record_id": "reviewed-structure-rule-001",
        "record_digest": digest({"record": "reviewed-structure-rule-001"}),
        "answer_digest": digest({"answer": "steric relationship"}),
    }


def _job_bound_nonmedical_ground_truth_record(index: int) -> dict:
    """Return one registry record with the context and answer form a job locks."""

    answer = {"answer": "steric relationship"}
    return {
        "source_type": "rule_evaluated_structure",
        "source_id": f"reviewed-structure-rule-{index}",
        "source_digest": digest({"structure": f"reviewed-{index}"}),
        "record_id": f"reviewed-structure-rule-{index:03d}",
        "record_digest": digest({"record": f"reviewed-structure-rule-{index:03d}"}),
        "answer_digest": digest(answer),
        "allowed_answer_schema": {"shape": "categorical_label", "fields": ["answer"]},
        "anonymous_scene_ref": "scene://nonmedical-contract-test",
        "primary_asset_digest": digest({"asset": f"chemistry-primary-{index}"}),
        "selection_slot_digest": digest({"slot": f"chemistry-{index}"}),
    }


def _ground_truth_identity(record: dict) -> dict:
    return {
        key: record[key]
        for key in (
            "source_type",
            "source_id",
            "source_digest",
            "record_id",
            "record_digest",
            "answer_digest",
            "allowed_answer_schema",
            "measurement_receipt_digest",
        )
        if key in record
    }


def _chirality_feedback_policy():
    """The accepted chirality policy has no measurement question family."""

    return parse_feedback_policy(
        {
            "claims": [],
            "question_family_policy": {
                "allowed_families": [
                    {"family_id": "configuration-under-controlled-view", "family_kind": "comparison"},
                    {"family_id": "stereocenter-configuration", "family_kind": "attribute"},
                ],
                "quota": {
                    "scope": "portfolio",
                    "unit": "percentage_points",
                    "measurement_max": 0,
                    "non_measurement_min": 100,
                },
            },
            "gt_policy": {
                "gt_source_types": ["expert_annotation", "rule_evaluated_structure"],
                "expert_approval_required": True,
            },
        },
        leaf_id="chemistry.stereochemistry_chirality",
    )


def _nonmedical_candidate(ground_truth_registry: GroundTruthRegistry | None = None) -> dict:
    policy = load_question_text_policy()
    record = _nonmedical_ground_truth_record()
    gt_provenance = (
        _registered_provenance(ground_truth_registry, record)
        if ground_truth_registry is not None
        else {
            **record,
            "ground_truth_registry_digest": digest({"missing": "ground-truth-registry"}),
        }
    )
    return {
        "artifact_kind": "author_private_candidate",
        "task_id": "nonmedical-contract-test",
        "prompt": "Which visible molecular relationship best accounts for the observed conformation?",
        "anonymous_scene_ref": "scene://nonmedical-contract-test",
        "viewer_session_request": {
            "request_id": "nonmedical-contract-request",
            "viewer_profile_digest": digest({"viewer": "chemistry"}),
            "allowed_operations": ["inspect", "render"],
        },
        "reference_answer": {"answer": "steric relationship"},
        "reasoning": "private rationale",
        "rubric": {"viewer_necessity": "controlled visual evidence"},
        "question_contract": {
            "question_family_kind": "property_identification",
            "question_kernel": "identify visible steric relationship",
            "question_text_policy_digest": policy.content_digest,
            "gt_answer_contract": {
                "source_type": "rule_evaluated_structure",
                "gt_shape": "categorical_label",
                "answer_fields": ["answer"],
                "provenance": gt_provenance,
            },
            "asset_facets": _facets("chemistry-fixture"),
        },
        "provenance": {
            "plan_digest": digest({"plan": "chemistry"}),
            "config_digest": digest({"config": "chemistry"}),
            "knowledge_snapshot_digest": digest({"knowledge": "chemistry"}),
        },
    }


def _validate_nonmedical_candidate(candidate: dict, *, registry: GroundTruthRegistry) -> dict:
    return validate_candidate_question_contract(
        candidate,
        policy=load_question_text_policy(),
        leaf_id="chemistry.conformation_analysis",
        ground_truth_registry=registry,
        expected_ground_truth_registry_digest=registry.content_digest,
    )


def _measurement_candidate(registry: GroundTruthRegistry) -> tuple[dict, dict]:
    answer = {"answer": "12.0 mm"}
    receipt_digest = digest({"receipt": "controlled-measurement-001"})
    record = {
        "source_type": "measurement_receipt",
        "source_id": "controlled-measurement-receipt",
        "source_digest": digest({"source": "controlled-measurement"}),
        "record_id": "controlled-measurement-001",
        "record_digest": digest({"record": "controlled-measurement-001"}),
        "answer_digest": digest(answer),
        "measurement_receipt_digest": receipt_digest,
    }
    candidate = _nonmedical_candidate()
    candidate["reference_answer"] = answer
    contract = candidate["question_contract"]
    contract["question_family_kind"] = "measurement"
    contract["question_kernel"] = "measure controlled molecular distance"
    contract["gt_answer_contract"] = {
        "source_type": "measurement_receipt",
        "gt_shape": "scalar_numeric",
        "answer_fields": ["answer"],
        "provenance": _registered_provenance(registry, record),
    }
    return candidate, record


def test_chirality_feedback_policy_rejects_measurement_question_family(tmp_path: Path) -> None:
    """A generic contract kind cannot bypass an accepted zero-measurement leaf policy."""

    registry = _approved_ground_truth_registry(tmp_path, records=[_nonmedical_ground_truth_record()])
    candidate = _nonmedical_candidate(registry)
    candidate["question_contract"]["question_family_id"] = "stereocenter-configuration"
    candidate["question_contract"]["question_family_kind"] = "measurement"

    with pytest.raises(QuestionContractError, match="feedback_policy_question_family_kind_mismatch"):
        validate_candidate_question_contract(
            candidate,
            policy=load_question_text_policy(),
            leaf_id="chemistry.stereochemistry_chirality",
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
            feedback_policy=_chirality_feedback_policy(),
        )


def test_feedback_policy_loader_reads_approved_yaml_snapshot(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    policy = _chirality_feedback_policy()
    snapshot = make_envelope(
        kind="subdomain_knowledge_snapshot",
        identifier="chemistry.stereochemistry_chirality",
        status="approved",
        body={
            "id": "chemistry.stereochemistry_chirality",
            "status": "approved",
            "feedback_policy": policy.document,
            "provenance": {"feedback_policy_digest": policy.content_digest},
        },
    )
    snapshot_path = (
        paths.knowledge
        / "compiled"
        / "chemistry"
        / "stereochemistry_chirality-approved.snapshot.yaml"
    )
    atomic_write_yaml(snapshot_path, snapshot, private=True)

    loaded = load_workspace_feedback_policy(
        paths,
        leaf_id="chemistry.stereochemistry_chirality",
        expected_knowledge_snapshot_digest=snapshot["content_digest"],
    )

    assert loaded is not None
    assert loaded.content_digest == policy.content_digest


def test_ground_truth_registry_rejects_answer_and_membership_tampering(tmp_path: Path) -> None:
    record = _nonmedical_ground_truth_record()
    registry = _approved_ground_truth_registry(tmp_path, records=[record])
    candidate = _nonmedical_candidate(registry)
    _validate_nonmedical_candidate(candidate, registry=registry)

    forged_answer = deepcopy(candidate)
    forged_answer["reference_answer"] = {"answer": "fabricated relationship"}
    with pytest.raises(QuestionContractError, match="ground_truth_answer_digest_mismatch"):
        _validate_nonmedical_candidate(forged_answer, registry=registry)

    unknown_record = deepcopy(candidate)
    unknown_record["question_contract"]["gt_answer_contract"]["provenance"]["record_id"] = "unregistered-record"
    with pytest.raises(QuestionContractError, match="ground_truth_record_not_registered"):
        _validate_nonmedical_candidate(unknown_record, registry=registry)

    wrong_registry = deepcopy(candidate)
    wrong_registry["question_contract"]["gt_answer_contract"]["provenance"]["ground_truth_registry_digest"] = digest(
        {"registry": "other"}
    )
    with pytest.raises(QuestionContractError, match="ground_truth_registry_provenance_mismatch"):
        _validate_nonmedical_candidate(wrong_registry, registry=registry)


def test_ground_truth_registry_rejects_a_forged_measurement_receipt(tmp_path: Path) -> None:
    seed_record = _nonmedical_ground_truth_record()
    measurement_answer = {"answer": "12.0 mm"}
    measurement_record = {
        "source_type": "measurement_receipt",
        "source_id": "controlled-measurement-receipt",
        "source_digest": digest({"source": "controlled-measurement"}),
        "record_id": "controlled-measurement-001",
        "record_digest": digest({"record": "controlled-measurement-001"}),
        "answer_digest": digest(measurement_answer),
        "measurement_receipt_digest": digest({"receipt": "controlled-measurement-001"}),
    }
    registry = _approved_ground_truth_registry(tmp_path, records=[seed_record, measurement_record])
    candidate, _ = _measurement_candidate(registry)
    _validate_nonmedical_candidate(candidate, registry=registry)

    candidate["question_contract"]["gt_answer_contract"]["provenance"]["measurement_receipt_digest"] = digest(
        {"receipt": "forged"}
    )
    with pytest.raises(QuestionContractError, match="ground_truth_measurement_receipt_mismatch"):
        _validate_nonmedical_candidate(candidate, registry=registry)


def test_job_ground_truth_projection_rejects_other_valid_record_answer_and_schema(tmp_path: Path) -> None:
    selected_record = _job_bound_nonmedical_ground_truth_record(1)
    other_record = _job_bound_nonmedical_ground_truth_record(2)
    registry = _approved_ground_truth_registry(tmp_path, records=[selected_record, other_record])
    expected_projection = resolve_ground_truth_record_projection(
        registry=registry,
        expected_registry_digest=registry.content_digest,
        identity=_ground_truth_identity(selected_record),
    )
    candidate = _nonmedical_candidate()
    candidate["question_contract"]["gt_answer_contract"]["provenance"] = _registered_provenance(
        registry,
        selected_record,
    )

    validate_candidate_question_contract(
        candidate,
        policy=load_question_text_policy(),
        leaf_id="chemistry.conformation_analysis",
        ground_truth_registry=registry,
        expected_ground_truth_registry_digest=registry.content_digest,
        expected_ground_truth_record=expected_projection,
    )

    other_valid_record = deepcopy(candidate)
    other_valid_record["question_contract"]["gt_answer_contract"]["provenance"] = _registered_provenance(
        registry,
        other_record,
    )
    with pytest.raises(QuestionContractError, match="ground_truth_job_record_provenance_mismatch"):
        validate_candidate_question_contract(
            other_valid_record,
            policy=load_question_text_policy(),
            leaf_id="chemistry.conformation_analysis",
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
            expected_ground_truth_record=expected_projection,
        )

    changed_answer = deepcopy(candidate)
    changed_answer["reference_answer"] = {"answer": "fabricated relationship"}
    with pytest.raises(QuestionContractError, match="ground_truth_answer_digest_mismatch"):
        validate_candidate_question_contract(
            changed_answer,
            policy=load_question_text_policy(),
            leaf_id="chemistry.conformation_analysis",
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
            expected_ground_truth_record=expected_projection,
        )

    changed_shape = deepcopy(candidate)
    changed_shape["question_contract"]["gt_answer_contract"]["gt_shape"] = "semantic_label"
    with pytest.raises(QuestionContractError, match="ground_truth_job_record_answer_schema_mismatch"):
        validate_candidate_question_contract(
            changed_shape,
            policy=load_question_text_policy(),
            leaf_id="chemistry.conformation_analysis",
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
            expected_ground_truth_record=expected_projection,
        )

    changed_fields = deepcopy(candidate)
    changed_fields["question_contract"]["gt_answer_contract"]["answer_fields"] = ["answer", "confidence"]
    changed_fields["reference_answer"] = {"answer": "steric relationship", "confidence": "high"}
    with pytest.raises(QuestionContractError, match="ground_truth_job_record_answer_schema_mismatch"):
        validate_candidate_question_contract(
            changed_fields,
            policy=load_question_text_policy(),
            leaf_id="chemistry.conformation_analysis",
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
            expected_ground_truth_record=expected_projection,
        )


def test_measurement_job_ground_truth_projection_requires_and_preserves_receipt_digest(tmp_path: Path) -> None:
    answer = {"answer": "12.0 mm"}
    receipt_digest = digest({"receipt": "controlled-measurement-job-001"})
    record = {
        "source_type": "measurement_receipt",
        "source_id": "controlled-measurement-job",
        "source_digest": digest({"source": "controlled-measurement-job"}),
        "record_id": "controlled-measurement-job-001",
        "record_digest": digest({"record": "controlled-measurement-job-001"}),
        "answer_digest": digest(answer),
        "allowed_answer_schema": {"shape": "scalar_numeric", "fields": ["answer"]},
        "measurement_receipt_digest": receipt_digest,
        "anonymous_scene_ref": "scene://controlled-measurement-job-001",
        "primary_asset_digest": digest({"asset": "controlled-measurement-job-001"}),
        "selection_slot_digest": digest({"slot": "controlled-measurement-job-001"}),
    }
    registry = _approved_ground_truth_registry(tmp_path, records=[record])
    identity = _ground_truth_identity(record)
    projection = resolve_ground_truth_record_projection(
        registry=registry,
        expected_registry_digest=registry.content_digest,
        identity=identity,
    )

    assert projection["provenance"]["measurement_receipt_digest"] == receipt_digest

    del identity["measurement_receipt_digest"]
    with pytest.raises(GroundTruthRegistryError, match="measurement_receipt"):
        resolve_ground_truth_record_projection(
            registry=registry,
            expected_registry_digest=registry.content_digest,
            identity=identity,
        )


def test_coverage_is_family_first_and_deduplicates_repeated_dataset_geometry() -> None:
    policy = load_question_text_policy()
    repeated_measurement = [_coverage_candidate("measurement", "scalar_numeric") for _ in range(35)]
    with pytest.raises(QuestionContractError, match="question_coverage_measurement_ratio_exceeded"):
        validate_question_coverage(repeated_measurement, policy=policy)

    approved_override = {
        "approval_status": "approved",
        "approval_digest": digest({"approval": "one-slot-pilot"}),
        "measurement_max_fraction": 1.0,
        "reasoning_min_fraction": 0.0,
    }
    coverage = validate_question_coverage(
        repeated_measurement,
        policy=policy,
        approved_profile_override=approved_override,
    )
    assert coverage == {"raw_total": 35, "total": 1, "measurement": 1, "reasoning": 0}

    coverage = validate_question_coverage(
        [
            _coverage_candidate("measurement", "scalar_numeric"),
            _coverage_candidate("property_identification", "semantic_label"),
            _coverage_candidate("attribute_classification", "categorical_label"),
            _coverage_candidate("paired_comparison", "paired_relation"),
        ],
        policy=policy,
    )
    assert coverage["measurement"] == 1
    assert coverage["reasoning"] == 3


def test_coverage_dedupe_uses_full_asset_facets_and_question_kernel() -> None:
    policy = load_question_text_policy()
    same_bucket_different_shape = [
        _coverage_candidate(
            "property_identification",
            "semantic_label",
            question_kernel="identify the visible stereochemical relation",
        ),
        _coverage_candidate(
            "property_identification",
            "categorical_label",
            question_kernel="  identify  the visible stereochemical relation ",
        ),
    ]
    coverage = validate_question_coverage(same_bucket_different_shape, policy=policy)
    assert coverage == {"raw_total": 2, "total": 1, "measurement": 0, "reasoning": 1}

    distinct_facet = _coverage_candidate(
        "property_identification",
        "semantic_label",
        question_kernel="identify the visible stereochemical relation",
        facets={**_facets("dataset-a"), "anatomy": ["tetrahedral_center"]},
    )
    distinct_kernel = _coverage_candidate(
        "property_identification",
        "semantic_label",
        question_kernel="compare substituent ordering at the visible stereocenter",
    )
    coverage = validate_question_coverage(
        [same_bucket_different_shape[0], distinct_facet, distinct_kernel],
        policy=policy,
    )
    assert coverage["total"] == 3
    assert coverage["reasoning"] == 3


def test_coverage_rejects_a_family_change_within_the_same_semantic_bucket() -> None:
    policy = load_question_text_policy()
    candidates = [
        _coverage_candidate(
            "property_identification",
            "semantic_label",
            question_kernel="identify the visible stereochemical relation",
        ),
        _coverage_candidate(
            "attribute_classification",
            "categorical_label",
            question_kernel="identify the visible stereochemical relation",
        ),
    ]
    with pytest.raises(QuestionContractError, match="question_coverage_bucket_family_conflict"):
        validate_question_coverage(candidates, policy=policy)


def test_domain_coverage_rejects_cross_domain_measurement_masking() -> None:
    policy = load_question_text_policy()
    candidates = [
        *[_coverage_candidate("measurement", "scalar_numeric", f"biology-{index}") for index in range(4)],
        *[
            _coverage_candidate("property_identification", "semantic_label", f"chemistry-{index}")
            for index in range(12)
        ],
    ]
    leaf_ids = [
        *["biology.molecular_collision_check" for _ in range(4)],
        *["chemistry.conformation_analysis" for _ in range(12)],
    ]

    with pytest.raises(QuestionContractError, match="question_coverage_measurement_ratio_exceeded:biology"):
        validate_question_coverage(
            candidates,
            policy=policy,
            leaf_ids=leaf_ids,
            expected_leaf_ids=leaf_ids,
        )


def test_domain_coverage_requires_a_minimum_approved_domain_sample() -> None:
    policy = load_question_text_policy()
    candidates = [
        _coverage_candidate("property_identification", "semantic_label", f"chemistry-{index}")
        for index in range(3)
    ]
    leaf_ids = ["chemistry.conformation_analysis" for _ in candidates]

    with pytest.raises(QuestionContractError, match="question_coverage_domain_minimum_sample_not_met:chemistry"):
        validate_question_coverage(
            candidates,
            policy=policy,
            leaf_ids=leaf_ids,
            expected_leaf_ids=leaf_ids,
        )


def test_approved_override_applies_to_each_domain_and_reports_subdomain_family_mix() -> None:
    policy = load_question_text_policy()
    candidates = [
        _coverage_candidate("measurement", "scalar_numeric", f"biology-{index}")
        for index in range(4)
    ]
    leaf_ids = ["biology.molecular_collision_check" for _ in candidates]
    approved_override = {
        "approval_status": "approved",
        "approval_digest": digest({"approval": "biology-measurement-exception"}),
        "measurement_max_fraction": 1.0,
        "reasoning_min_fraction": 0.0,
    }

    coverage = validate_question_coverage(
        candidates,
        policy=policy,
        approved_profile_override=approved_override,
        leaf_ids=leaf_ids,
        expected_leaf_ids=leaf_ids,
    )

    assert coverage["domains"]["biology"]["expected_count"] == 4
    assert coverage["domains"]["biology"]["family_counts"] == {"measurement": 4}
    assert coverage["domains"]["biology"]["subdomains"]["molecular_collision_check"]["family_counts"] == {
        "measurement": 4
    }


def test_completed_live_batch_blocks_a_measurement_only_portfolio(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    answer = {"answer": "steric relationship"}
    measurement_record = {
        "source_type": "measurement_receipt",
        "source_id": "controlled-measurement-receipt",
        "source_digest": digest({"source": "controlled-measurement"}),
        "record_id": "controlled-measurement-coverage",
        "record_digest": digest({"record": "controlled-measurement-coverage"}),
        "answer_digest": digest(answer),
        "measurement_receipt_digest": digest({"receipt": "controlled-measurement-coverage"}),
    }
    registry = _approved_ground_truth_registry(tmp_path, records=[measurement_record], identifier="coverage-ground-truth-registry")
    candidate = _nonmedical_candidate()
    candidate["question_contract"]["question_family_kind"] = "measurement"
    candidate["question_contract"]["gt_answer_contract"]["source_type"] = "measurement_receipt"
    candidate["question_contract"]["gt_answer_contract"]["gt_shape"] = "scalar_numeric"
    candidate["question_contract"]["gt_answer_contract"]["provenance"] = _registered_provenance(registry, measurement_record)
    candidate_path = paths.root / "candidate.json"
    atomic_write_json(candidate_path, candidate, private=True)
    body = {
        "jobs": [
            {
                "target_leaf": "chemistry.conformation_analysis",
                "status": "verification_complete_pending_human_review",
                "artifacts": {
                    "candidate": {
                        "path": candidate_path.relative_to(paths.root).as_posix(),
                        "content_digest": digest(candidate),
                    }
                },
            }
        ]
    }

    with pytest.raises(LiveBatchError, match="question coverage"):
        _validate_completed_live_question_coverage(paths, body=body, ground_truth_registry=registry)


def test_release_staging_rechecks_tampered_solver_prompt(tmp_path: Path) -> None:
    registry = _approved_ground_truth_registry(tmp_path, records=[_nonmedical_ground_truth_record()])
    candidate = _nonmedical_candidate(registry)
    work_item = {"leaf_id": "chemistry.conformation_analysis"}
    _validate_candidate_question_contract_for_staging(
        candidate,
        work_item=work_item,
        ground_truth_registry=registry,
        expected_ground_truth_registry_digest=registry.content_digest,
    )

    candidate["prompt"] = "Click the viewer button to reveal the overlay."
    with pytest.raises(ReleaseStageError, match="question contract validation failed"):
        _validate_candidate_question_contract_for_staging(
            candidate,
            work_item=work_item,
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
        )
