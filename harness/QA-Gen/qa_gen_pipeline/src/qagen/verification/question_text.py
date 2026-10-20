"""Fail-closed solver-facing question contracts and text policy checks.

The solver prompt is scientific task text, not a viewer user manual.  Viewer
operations belong in the controlled session request, while truth provenance
and the author-side answer contract remain private.  This module owns the
small deterministic boundary shared by author validation, review, and release
staging.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import re
import unicodedata
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, require_digest
from ..errors import InputError
from ..resource_paths import PackageResourceError, config_path
from .ground_truth import (
    GroundTruthRegistry,
    GroundTruthRegistryError,
    validate_ground_truth_membership,
)
from .feedback_policy import (
    FeedbackPolicy,
    FeedbackPolicyError,
    LockedFeedbackPolicy,
    validate_feedback_policy_contract,
)


class QuestionContractError(InputError):
    """A solver-facing question or its private answer contract is unsafe."""


QUESTION_FAMILY_KINDS = frozenset(
    {
        "property_identification",
        "attribute_classification",
        "paired_comparison",
        "measurement",
    }
)
GT_SOURCE_TYPES = frozenset(
    {
        "expert_annotation",
        "curated_source_label",
        "rule_evaluated_structure",
        "paired_comparison",
        "measurement_receipt",
    }
)
MEDICAL_CLINICAL_TASK_KINDS = frozenset(
    {
        "anatomical_localization",
        "lesion_localization",
        "morphology",
        "imaging_feature_characterization",
        "spatial_relation",
    }
)
_REASONING_FAMILIES = frozenset(
    {
        "property_identification",
        "attribute_classification",
        "paired_comparison",
    }
)


@dataclass(frozen=True)
class QuestionTextPolicy:
    """A canonically frozen, schema-checked policy document."""

    document: Mapping[str, Any]
    content_digest: str

    @property
    def identifier(self) -> str:
        return str(self.document["id"])

    @property
    def require_question_contract(self) -> bool:
        return bool(self.document["enforcement"]["require_question_contract"])


@dataclass(frozen=True)
class CuratedSemanticGTSource:
    """A reviewed index of private Medical semantic ground-truth records."""

    document: Mapping[str, Any]
    content_digest: str

    @property
    def identifier(self) -> str:
        return str(self.document["id"])


def _resource_json(name: str) -> dict[str, Any]:
    try:
        path = config_path("schemas", name)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (PackageResourceError, OSError, json.JSONDecodeError) as exc:
        raise QuestionContractError(f"question policy schema is unavailable: {name}") from exc
    if not isinstance(value, Mapping):
        raise QuestionContractError(f"question policy schema is invalid: {name}")
    return dict(value)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise QuestionContractError(f"{field}_invalid")
    return dict(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuestionContractError(f"{field}_invalid")
    return value.strip()


def _schema_validate(value: Mapping[str, Any], schema: Mapping[str, Any], *, label: str) -> None:
    errors = sorted(Draft7Validator(dict(schema)).iter_errors(dict(value)), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise QuestionContractError(f"{label}_schema_invalid:{location}:{error.message}")


def load_question_text_policy(
    path: str | Path | None = None,
    *,
    expected_digest: str | None = None,
) -> QuestionTextPolicy:
    """Load the sole local policy and optionally bind it to a pre-author lock."""

    if path is None:
        try:
            policy_path = config_path("policies", "solver_facing_question_text.yaml")
        except PackageResourceError as exc:
            raise QuestionContractError("question_text_policy_unavailable") from exc
    else:
        policy_path = Path(path).expanduser().resolve()
    try:
        value = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QuestionContractError("question_text_policy_unavailable") from exc
    if not isinstance(value, Mapping):
        raise QuestionContractError("question_text_policy_invalid")
    document = dict(value)
    _schema_validate(document, _resource_json("question_text_policy.schema.json"), label="question_text_policy")
    content_digest = digest(document)
    if expected_digest is not None:
        try:
            require_digest(expected_digest, "question text policy expected digest")
        except InputError as exc:
            raise QuestionContractError("question_text_policy_digest_invalid") from exc
        if content_digest != expected_digest:
            raise QuestionContractError("question_text_policy_digest_mismatch")
    return QuestionTextPolicy(document=document, content_digest=content_digest)


def _approved_semantic_gt_snapshot(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the digest-only lookup index used by an approved Medical source."""

    snapshot = _mapping(document.get("approved_snapshot"), "medical_semantic_gt_approved_snapshot")
    try:
        source_digest = require_digest(snapshot.get("source_digest"), "medical semantic GT approved source digest")
        expert_approval_digest = require_digest(
            snapshot.get("expert_approval_digest"), "medical semantic GT expert approval digest"
        )
    except InputError as exc:
        raise QuestionContractError("medical_semantic_gt_source_invalid") from exc
    annotations = snapshot.get("annotations")
    count = document.get("annotation_count")
    if (
        not isinstance(annotations, list)
        or not annotations
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count != len(annotations)
    ):
        raise QuestionContractError("medical_semantic_gt_source_invalid")
    by_id: dict[str, dict[str, Any]] = {}
    for raw in annotations:
        entry = _mapping(raw, "medical_semantic_gt_annotation_index")
        annotation_id = _text(entry.get("annotation_id"), "medical_semantic_gt_annotation_id")
        try:
            normalized = {
                "annotation_id": annotation_id,
                "annotation_digest": require_digest(
                    entry.get("annotation_digest"), "medical semantic GT annotation digest"
                ),
                "verbatim_provenance_digest": require_digest(
                    entry.get("verbatim_provenance_digest"), "medical semantic GT verbatim digest"
                ),
                "semantic_anchor_digest": require_digest(
                    entry.get("semantic_anchor_digest"), "medical semantic GT semantic anchor digest"
                ),
                "anonymous_scene_ref": _text(
                    entry.get("anonymous_scene_ref"), "medical semantic GT anonymous scene ref"
                ),
                "primary_asset_digest": require_digest(
                    entry.get("primary_asset_digest"), "medical semantic GT primary asset digest"
                ),
                "selection_slot_digest": require_digest(
                    entry.get("selection_slot_digest"), "medical semantic GT selection slot digest"
                ),
                "question_granularity": _text(
                    entry.get("question_granularity"), "medical semantic GT question granularity"
                ),
            }
        except InputError as exc:
            raise QuestionContractError("medical_semantic_gt_source_invalid") from exc
        if normalized["question_granularity"] not in {
            "body_region",
            "organ_system",
            "anatomy",
            "pathology",
            "imaging_feature",
        } or annotation_id in by_id:
            raise QuestionContractError("medical_semantic_gt_source_invalid")
        by_id[annotation_id] = normalized
    return {
        "source_digest": source_digest,
        "expert_approval_digest": expert_approval_digest,
        "annotations": by_id,
    }


def load_curated_semantic_gt_source(
    path: str | Path | None = None,
) -> CuratedSemanticGTSource:
    """Load the only configured Medical semantic-GT source declaration.

    A declaration may remain ``missing`` or ``pending_approval``.  Those
    states are valid configuration states but never authorize a candidate.
    An ``approved`` declaration carries a digest-only annotation index so the
    candidate's private annotation can be looked up without exposing it in a
    solver projection.
    """

    if path is None:
        try:
            source_path = config_path("sources", "medical_m3d_refseg_annotations.yaml")
        except PackageResourceError as exc:
            raise QuestionContractError("medical_semantic_gt_source_unavailable") from exc
    else:
        source_path = Path(path).expanduser().resolve()
    try:
        value = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QuestionContractError("medical_semantic_gt_source_unavailable") from exc
    if not isinstance(value, Mapping):
        raise QuestionContractError("medical_semantic_gt_source_invalid")
    document = dict(value)
    _schema_validate(document, _resource_json("curated_semantic_gt_source.schema.json"), label="medical_semantic_gt_source")
    if document.get("source_status") == "approved":
        _approved_semantic_gt_snapshot(document)
    return CuratedSemanticGTSource(document=document, content_digest=digest(document))


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _question_kernel(value: Any) -> str:
    """Normalize the private semantic kernel used to deduplicate coverage."""

    kernel = _text(value, "question_kernel")
    normalized = re.sub(r"\s+", " ", _normalized(kernel)).strip()
    if not normalized:
        raise QuestionContractError("question_kernel_invalid")
    return normalized


# These deliberately match an operation *on a UI surface*, not a scientific
# use of "axis", "render", or "select" in isolation.
_UI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "viewer_tool_instruction_en",
        re.compile(
            r"\b(?:use|open|click|select|choose|inspect)[ \t]+"
            r"(?:(?:the|a|an)[ \t]+)?"
            r"(?:[a-z0-9][a-z0-9-]*[ \t]+){0,2}"
            r"(?:viewer|tool|toolbar|button|menu|sidebar|overlay|mask(?:[ \t]+label)?|"
            r"bounding[ \t]+box|bbox|fov|field[ \t]+of[ \t]+view)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "viewer_scene_operation_en",
        re.compile(
            r"\b(?:rotate|zoom|render|pan|orbit)\s+(?:the\s+)?(?:viewer|scene|model|structure|image)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ui_control_reference_en",
        re.compile(r"\b(?:sidebar|toolbar|button|menu)\b", re.IGNORECASE),
    ),
    (
        "solver_shortcut_reference_en",
        re.compile(
            r"\b(?:mask\s+label|viewer\s+overlay|overlay\s+mask|bounding\s+box|bbox|field\s+of\s+view|fov)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "viewer_axis_reference_en",
        re.compile(r"\b(?:viewer|screen|coordinate)\s+axis\b", re.IGNORECASE),
    ),
    (
        "viewer_tool_instruction_zh",
        re.compile(
            r"(?:请|先|然后)?(?:点击|选择|使用|打开|检查|查看|检视)"
            r"[^。！？.!?\n]{0,16}(?:查看器|viewer|工具|工具栏|按钮|菜单|侧边栏|叠加层|遮罩|掩膜|遮罩标签|掩膜标签|边界框|bbox|视野|fov)"
        ),
    ),
    (
        "viewer_scene_operation_zh",
        re.compile(r"(?:旋转|缩放|渲染|平移|拖动)[^。！？.!?\n]{0,16}(?:查看器|viewer|场景|模型|结构|图像)"),
    ),
    (
        "ui_control_reference_zh",
        re.compile(r"(?:侧边栏|工具栏|按钮|菜单|遮罩标签|掩膜标签|边界框)"),
    ),
    (
        "viewer_axis_reference_zh",
        re.compile(r"(?:查看器|屏幕|坐标)\s*轴"),
    ),
)


def lint_solver_question_text(text: str, *, policy: QuestionTextPolicy) -> list[str]:
    """Return stable issue identifiers for solver-facing UI/tool meta-language."""

    _text(text, "solver_question_text")
    if policy.document["enforcement"]["reject_solver_ui_meta_language"] is not True:
        raise QuestionContractError("question_text_policy_does_not_enforce_solver_text_boundary")
    normalized = _normalized(text)
    requested = set(policy.document["enforcement"]["forbidden_categories"])
    issues = [identifier for identifier, pattern in _UI_PATTERNS if identifier in requested and pattern.search(normalized)]
    return sorted(set(issues))


def validate_solver_question_text(text: str, *, policy: QuestionTextPolicy) -> None:
    issues = lint_solver_question_text(text, policy=policy)
    if issues:
        raise QuestionContractError(
            "solver_facing_question_text_contains_ui_meta_language:" + ",".join(issues)
        )


def _contract_schema() -> dict[str, Any]:
    return _resource_json("question_contract.schema.json")


def _answer_fields(contract: Mapping[str, Any]) -> list[str]:
    gt = _mapping(contract.get("gt_answer_contract"), "gt_answer_contract")
    _text(gt.get("gt_shape"), "gt_answer_contract_gt_shape")
    fields = gt.get("answer_fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(item, str) and item for item in fields):
        raise QuestionContractError("gt_answer_contract_answer_fields_invalid")
    if len(set(fields)) != len(fields):
        raise QuestionContractError("gt_answer_contract_answer_fields_duplicate")
    return list(fields)


def _validate_gt_provenance(
    contract: Mapping[str, Any],
    reference_answer: Any,
    *,
    ground_truth_registry: GroundTruthRegistry | None,
    expected_ground_truth_registry_digest: str | None,
    expected_ground_truth_record: Mapping[str, Any] | None,
) -> None:
    gt = _mapping(contract.get("gt_answer_contract"), "gt_answer_contract")
    source_type = gt.get("source_type")
    if source_type not in GT_SOURCE_TYPES:
        raise QuestionContractError("gt_answer_contract_source_type_forbidden")
    provenance = _mapping(gt.get("provenance"), "gt_answer_contract_provenance")
    if not provenance:
        raise QuestionContractError("gt_answer_contract_provenance_empty")
    try:
        require_digest(provenance.get("source_digest"), "gt answer provenance source digest")
    except InputError as exc:
        raise QuestionContractError("gt_answer_contract_provenance_digest_invalid") from exc
    _text(provenance.get("source_id"), "gt_answer_contract_provenance_source_id")
    fields = _answer_fields(contract)
    answer = _mapping(reference_answer, "reference_answer")
    if set(answer) != set(fields):
        raise QuestionContractError("gt_answer_contract_answer_fields_do_not_match_reference_answer")
    if expected_ground_truth_record is not None:
        if not isinstance(expected_ground_truth_record, Mapping):
            raise QuestionContractError("ground_truth_job_record_projection_invalid")
        schema = expected_ground_truth_record.get("allowed_answer_schema")
        if not isinstance(schema, Mapping):
            raise QuestionContractError("ground_truth_job_record_projection_invalid")
        if gt.get("gt_shape") != schema.get("shape") or fields != schema.get("fields"):
            raise QuestionContractError("ground_truth_job_record_answer_schema_mismatch")
    try:
        validate_ground_truth_membership(
            source_type=source_type,
            provenance=provenance,
            reference_answer=answer,
            registry=ground_truth_registry,
            expected_registry_digest=expected_ground_truth_registry_digest,
            expected_record_projection=expected_ground_truth_record,
        )
    except GroundTruthRegistryError as exc:
        raise QuestionContractError(str(exc)) from exc


def _validate_asset_facets(contract: Mapping[str, Any]) -> dict[str, list[str]]:
    facets = _mapping(contract.get("asset_facets"), "asset_facets")
    expected = {"body_region", "organ_system", "anatomy", "pathology", "modality", "dataset"}
    if set(facets) != expected:
        raise QuestionContractError("asset_facets_shape_invalid")
    normalized: dict[str, list[str]] = {}
    for key in sorted(expected):
        values = facets.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise QuestionContractError("asset_facets_value_invalid")
        if len(set(values)) != len(values):
            raise QuestionContractError("asset_facets_value_duplicate")
        normalized[key] = sorted(item.strip() for item in values)
    if not normalized["dataset"]:
        raise QuestionContractError("asset_facets_dataset_missing")
    return normalized


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


def _contains_merged_label_selector(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_merged_label_selector(key) or _contains_merged_label_selector(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_merged_label_selector(item) for item in value)
    if isinstance(value, str):
        normalized = _normalized(value)
        return bool(
            re.search(r"\ball\s*>\s*0\b", normalized)
            or normalized in {"all_nonzero", "all_non_zero", "merge_labels", "merged_labels", "merged_label"}
        )
    return False


def _validate_medical_question_contract(
    contract: Mapping[str, Any],
    candidate: Mapping[str, Any],
    prompt: str,
    *,
    semantic_gt_source: CuratedSemanticGTSource,
) -> None:
    medical = _mapping(contract.get("medical_question_contract"), "medical_question_contract")
    task_kind = medical.get("clinical_task_kind")
    if task_kind not in MEDICAL_CLINICAL_TASK_KINDS:
        raise QuestionContractError("medical_question_contract_task_kind_invalid")
    mode = medical.get("approved_mode")
    if mode not in {"semantic_interpretation_no_overlay", "localize_then_measure_no_overlay"}:
        raise QuestionContractError("medical_approved_mode_invalid")
    binding = _mapping(medical.get("semantic_scenario_binding"), "medical_semantic_scenario_binding")
    if binding.get("approval_status") != "approved":
        raise QuestionContractError("medical_semantic_scenario_binding_not_approved")
    for key in ("scenario_digest", "semantic_anchor_digest"):
        try:
            require_digest(binding.get(key), f"medical semantic scenario binding {key}")
        except InputError as exc:
            raise QuestionContractError("medical_semantic_scenario_binding_digest_invalid") from exc
    semantic_anchor_id = _text(binding.get("semantic_anchor_id"), "medical_semantic_anchor_id")
    semantic_anchor_digest = str(binding["semantic_anchor_digest"])
    _text(binding.get("clinical_semantic_anchor"), "medical_clinical_semantic_anchor")

    semantic_gt = _mapping(medical.get("semantic_gt_binding"), "medical_semantic_gt_binding")
    source_document = semantic_gt_source.document
    if (
        semantic_gt_source.identifier != "m3d_refseg_doctor_annotation"
        or semantic_gt.get("source_id") != semantic_gt_source.identifier
    ):
        raise QuestionContractError("medical_semantic_gt_source_invalid")
    if source_document.get("source_status") != "approved":
        raise QuestionContractError("medical_semantic_gt_source_not_approved")
    approved_snapshot = _approved_semantic_gt_snapshot(source_document)
    for key in ("source_digest", "semantic_anchor_digest", "verbatim_provenance_digest"):
        try:
            require_digest(semantic_gt.get(key), f"medical semantic GT {key}")
        except InputError as exc:
            raise QuestionContractError("medical_semantic_gt_digest_invalid") from exc
    if semantic_gt.get("source_registry_digest") != semantic_gt_source.content_digest:
        raise QuestionContractError("medical_semantic_gt_source_registry_digest_mismatch")
    if semantic_gt.get("source_digest") != approved_snapshot["source_digest"]:
        raise QuestionContractError("medical_semantic_gt_source_digest_mismatch")
    if (
        semantic_gt.get("semantic_anchor_id") != semantic_anchor_id
        or semantic_gt.get("semantic_anchor_digest") != semantic_anchor_digest
    ):
        raise QuestionContractError("decorative_medical_label")
    if semantic_gt.get("annotation_granularity") != semantic_gt.get("question_granularity"):
        raise QuestionContractError("medical_question_exceeds_annotation_granularity")
    annotation = _mapping(semantic_gt.get("annotation"), "medical_semantic_gt_annotation")
    for key in ("annotation_id", "finding", "location", "impression"):
        _text(annotation.get(key), f"medical_semantic_gt_annotation_{key}")
    annotation_id = str(annotation["annotation_id"])
    annotation_index = approved_snapshot["annotations"].get(annotation_id)
    if annotation_index is None:
        raise QuestionContractError("medical_semantic_gt_annotation_not_found")
    if (
        digest(annotation) != annotation_index["annotation_digest"]
        or semantic_gt.get("verbatim_provenance_digest") != annotation_index["verbatim_provenance_digest"]
        or semantic_gt.get("semantic_anchor_digest") != annotation_index["semantic_anchor_digest"]
        or semantic_gt.get("question_granularity") != annotation_index["question_granularity"]
        or semantic_gt.get("anonymous_scene_ref") != annotation_index["anonymous_scene_ref"]
        or semantic_gt.get("primary_asset_digest") != annotation_index["primary_asset_digest"]
        or semantic_gt.get("selection_slot_digest") != annotation_index["selection_slot_digest"]
        or candidate.get("anonymous_scene_ref") != annotation_index["anonymous_scene_ref"]
    ):
        raise QuestionContractError("medical_semantic_gt_annotation_binding_mismatch")
    gt = _mapping(contract.get("gt_answer_contract"), "gt_answer_contract")
    gt_provenance = _mapping(gt.get("provenance"), "gt_answer_contract_provenance")
    if (
        gt.get("source_type") != "curated_source_label"
        or gt_provenance.get("source_id") != semantic_gt.get("source_id")
        or gt_provenance.get("source_digest") != semantic_gt.get("source_digest")
        or gt_provenance.get("record_id") != annotation_id
        or gt_provenance.get("record_digest") != annotation_index["annotation_digest"]
        or gt_provenance.get("anonymous_scene_ref") != semantic_gt.get("anonymous_scene_ref")
        or gt_provenance.get("primary_asset_digest") != semantic_gt.get("primary_asset_digest")
        or gt_provenance.get("selection_slot_digest") != semantic_gt.get("selection_slot_digest")
    ):
        raise QuestionContractError("decorative_medical_label")
    scoring = _mapping(medical.get("semantic_scoring"), "medical_semantic_scoring")
    if scoring.get("semantic_anchor_digest") != semantic_anchor_digest:
        raise QuestionContractError("decorative_medical_label")
    dimensions = scoring.get("required_dimensions")
    if not isinstance(dimensions, list) or not dimensions or not all(isinstance(item, str) and item for item in dimensions):
        raise QuestionContractError("medical_semantic_scoring_dimensions_invalid")
    try:
        require_digest(scoring.get("expert_approval_digest"), "medical semantic scoring expert approval")
    except InputError as exc:
        raise QuestionContractError("medical_semantic_scoring_approval_invalid") from exc
    if scoring.get("expert_approval_digest") != approved_snapshot["expert_approval_digest"]:
        raise QuestionContractError("medical_semantic_gt_expert_approval_mismatch")
    rubric = _mapping(candidate.get("rubric"), "candidate_rubric")
    rubric_semantic = _mapping(rubric.get("semantic_scoring"), "candidate_rubric_semantic_scoring")
    if (
        rubric_semantic.get("semantic_anchor_digest") != semantic_anchor_digest
        or rubric_semantic.get("expert_approval_digest") != scoring.get("expert_approval_digest")
        or rubric_semantic.get("required_dimensions") != dimensions
    ):
        raise QuestionContractError("decorative_medical_label")

    projection = _mapping(medical.get("solver_projection"), "medical_solver_projection")
    excluded = projection.get("excluded_artifacts")
    if projection.get("primary_image") is not True or not isinstance(excluded, list) or set(excluded) != {"segmentation", "mask", "overlay"}:
        raise QuestionContractError("medical_solver_projection_not_no_overlay")
    if medical.get("label_identity_policy") != "preserve" or _contains_merged_label_selector(contract) or _contains_merged_label_selector(candidate.get("reference_answer")):
        raise QuestionContractError("medical_label_identity_merge_rejected")
    if medical.get("primary_image_necessary") is not True:
        raise QuestionContractError("medical_primary_image_necessity_missing")
    session = _mapping(candidate.get("viewer_session_request"), "medical_viewer_session_request")
    requested_operations = session.get("allowed_operations")
    if (
        not isinstance(requested_operations, list)
        or not requested_operations
        or not all(isinstance(item, str) and item for item in requested_operations)
        or len(set(requested_operations)) != len(requested_operations)
    ):
        raise QuestionContractError("medical_viewer_session_operations_invalid")
    if {"bbox", "overlay_compare", "mask", "segmentation"}.intersection(requested_operations):
        raise QuestionContractError("medical_solver_visible_overlay_or_mask_operation_rejected")
    shortcuts = _mapping(medical.get("shortcut_resistance"), "medical_shortcut_resistance")
    expected_shortcuts = {"mask_only", "overlay_only", "metadata_only", "raw_compute"}
    if set(shortcuts) != expected_shortcuts or any(shortcuts[key] is not False for key in expected_shortcuts):
        raise QuestionContractError("medical_solver_visible_truth_shortcut_not_rejected")
    answerability = _mapping(medical.get("human_viewer_answerability"), "medical_human_viewer_answerability")
    if answerability.get("intended_solver") != "clinical_expert":
        raise QuestionContractError("medical_intended_solver_invalid")
    _text(answerability.get("viewer_precision_bound"), "medical_viewer_precision_bound")
    tolerance = _mapping(answerability.get("tolerance"), "medical_answer_tolerance")
    kind = tolerance.get("kind")
    if kind not in {"categorical", "ordinal", "numeric"}:
        raise QuestionContractError("medical_answer_tolerance_kind_invalid")
    _text(tolerance.get("justification"), "medical_answer_tolerance_justification")
    if kind == "numeric":
        if not _finite_positive(tolerance.get("absolute")) or not isinstance(tolerance.get("unit"), str) or not tolerance["unit"].strip():
            raise QuestionContractError("medical_answer_tolerance_numeric_invalid")
    elif "absolute" in tolerance and tolerance["absolute"] is not None:
        raise QuestionContractError("medical_answer_tolerance_non_numeric_absolute_forbidden")

    localize_then_measure = medical.get("localize_then_measure")
    if mode == "semantic_interpretation_no_overlay":
        if localize_then_measure is not None:
            raise QuestionContractError("medical_semantic_mode_cannot_attach_measurement_protocol")
    else:
        protocol = _mapping(localize_then_measure, "medical_localize_then_measure")
        if (
            protocol.get("localization_before_measurement") is not True
            or protocol.get("hidden_segmentation_author_reference_only") is not True
            or protocol.get("measurement_subordinate") is not True
        ):
            raise QuestionContractError("medical_localize_then_measure_protocol_invalid")
        try:
            require_digest(protocol.get("manual_calibration_digest"), "medical manual calibration")
        except InputError as exc:
            raise QuestionContractError("medical_localize_then_measure_calibration_invalid") from exc
        if kind != "numeric":
            raise QuestionContractError("medical_localize_then_measure_requires_numeric_tolerance")

    # Geometry-only language is rejected here only for Medical.  The matching
    # terms are deliberately narrowly scoped; a scientific anatomical axis is
    # not rejected merely for containing the word "axis".
    geometry = re.compile(
        r"\b(?:centroid|bbox|bounding\s+box|axis\s+ratio|voxel\s+count|mask\s+volume|raw\s+compute)\b"
        r"|\b(?:calculate|determine|report|what\s+is)\s+(?:the\s+)?(?:extent|volume)\b"
        r"|(?:质心|边界框|包围盒|轴比|体素计数|掩膜体积|原始计算)",
        re.IGNORECASE,
    )
    if geometry.search(_normalized(prompt)):
        raise QuestionContractError("medical_shell_geometry_core_rejected")


def validate_candidate_question_contract(
    candidate: Mapping[str, Any],
    *,
    policy: QuestionTextPolicy,
    leaf_id: str | None = None,
    require_contract: bool | None = None,
    medical_gt_source: CuratedSemanticGTSource | None = None,
    ground_truth_registry: GroundTruthRegistry | None = None,
    expected_ground_truth_registry_digest: str | None = None,
    expected_ground_truth_record: Mapping[str, Any] | None = None,
    feedback_policy: LockedFeedbackPolicy | FeedbackPolicy | None = None,
    expected_question_family_id: str | None = None,
    legacy_fixture_compatibility: bool = False,
) -> dict[str, Any]:
    """Validate candidate question text, GT provenance, and medical semantics.

    ``require_contract`` defaults to the frozen policy.  The sole compatibility
    escape hatch is for an immutable, non-exportable fixture run.  It disables
    both contract and solver-text enforcement for legacy fixture artifacts;
    normal review, staging, and live execution must never set it.
    """

    value = _mapping(candidate, "candidate")
    prompt = _text(value.get("prompt"), "candidate_prompt")
    if not legacy_fixture_compatibility:
        validate_solver_question_text(prompt, policy=policy)
    contract_raw = value.get("question_contract")
    if contract_raw is None:
        if policy.require_question_contract if require_contract is None else require_contract:
            raise QuestionContractError("question_contract_missing")
        return {}
    contract = _mapping(contract_raw, "question_contract")
    _schema_validate(contract, _contract_schema(), label="question_contract")
    expected_digest = policy.content_digest
    if contract.get("question_text_policy_digest") != expected_digest:
        raise QuestionContractError("question_contract_policy_digest_mismatch")
    family = contract.get("question_family_kind")
    if family not in QUESTION_FAMILY_KINDS:
        raise QuestionContractError("question_family_kind_invalid")
    _question_kernel(contract.get("question_kernel"))
    _validate_asset_facets(contract)
    medical_required = isinstance(leaf_id, str) and leaf_id.startswith("medical.")
    if medical_required:
        if family == "measurement":
            raise QuestionContractError("medical_measurement_only_question_family_forbidden")
        source = medical_gt_source or load_curated_semantic_gt_source()
        _validate_medical_question_contract(contract, value, prompt, semantic_gt_source=source)
    elif "medical_question_contract" in contract:
        raise QuestionContractError("medical_question_contract_on_non_medical_leaf")
    _validate_gt_provenance(
        contract,
        value.get("reference_answer"),
        ground_truth_registry=ground_truth_registry,
        expected_ground_truth_registry_digest=expected_ground_truth_registry_digest,
        expected_ground_truth_record=expected_ground_truth_record,
    )
    if feedback_policy is not None:
        try:
            validate_feedback_policy_contract(
                contract,
                locked_policy=feedback_policy,
                expected_question_family_id=expected_question_family_id,
            )
        except FeedbackPolicyError as exc:
            raise QuestionContractError(str(exc)) from exc
    return contract


def _coverage_limits(
    *,
    policy: QuestionTextPolicy,
    approved_profile_override: Mapping[str, Any] | None,
) -> tuple[float, float, int]:
    """Load the frozen portfolio limits and an explicitly approved override."""

    coverage = _mapping(policy.document.get("coverage"), "question_text_policy_coverage")
    measurement_max = coverage.get("measurement_max_fraction")
    reasoning_min = coverage.get("reasoning_min_fraction")
    minimum_expected = coverage.get("domain_minimum_expected_count")
    if not isinstance(measurement_max, (int, float)) or isinstance(measurement_max, bool):
        raise QuestionContractError("question_text_policy_measurement_fraction_invalid")
    if not isinstance(reasoning_min, (int, float)) or isinstance(reasoning_min, bool):
        raise QuestionContractError("question_text_policy_reasoning_fraction_invalid")
    if not isinstance(minimum_expected, int) or isinstance(minimum_expected, bool) or minimum_expected < 1:
        raise QuestionContractError("question_text_policy_domain_minimum_expected_count_invalid")
    if approved_profile_override is not None:
        override = _mapping(approved_profile_override, "approved_profile_coverage_override")
        if coverage.get("approved_override_required") is not True or override.get("approval_status") != "approved":
            raise QuestionContractError("question_coverage_override_not_approved")
        try:
            require_digest(override.get("approval_digest"), "question coverage override approval digest")
        except InputError as exc:
            raise QuestionContractError("question_coverage_override_digest_invalid") from exc
        measurement_max = override.get("measurement_max_fraction", measurement_max)
        reasoning_min = override.get("reasoning_min_fraction", reasoning_min)
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= float(value) <= 1
        for value in (measurement_max, reasoning_min)
    ):
        raise QuestionContractError("question_coverage_fraction_invalid")
    return float(measurement_max), float(reasoning_min), minimum_expected


def _coverage_summary(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count raw slots and deduplicated coverage buckets for one scope."""

    effective_buckets: dict[tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...], str], str] = {}
    raw_families: dict[str, int] = {}
    for candidate in candidates:
        contract = _mapping(_mapping(candidate, "candidate").get("question_contract"), "question_contract")
        family = contract.get("question_family_kind")
        if family not in QUESTION_FAMILY_KINDS:
            raise QuestionContractError("question_family_kind_invalid")
        facets = _validate_asset_facets(contract)
        normalized_facets = tuple((dimension, tuple(facets[dimension])) for dimension in sorted(facets))
        key = (tuple(facets["dataset"]), normalized_facets, _question_kernel(contract.get("question_kernel")))
        prior_family = effective_buckets.setdefault(key, family)
        if prior_family != family:
            raise QuestionContractError("question_coverage_bucket_family_conflict")
        raw_families[str(family)] = raw_families.get(str(family), 0) + 1
    total = len(effective_buckets)
    if total == 0:
        raise QuestionContractError("question_coverage_candidates_empty")
    effective_families: dict[str, int] = {}
    for family in effective_buckets.values():
        effective_families[family] = effective_families.get(family, 0) + 1
    return {
        "raw_total": len(candidates),
        "total": total,
        "measurement": effective_families.get("measurement", 0),
        "reasoning": sum(count for family, count in effective_families.items() if family in _REASONING_FAMILIES),
        "family_counts": {family: raw_families[family] for family in sorted(raw_families)},
        "effective_family_counts": {family: effective_families[family] for family in sorted(effective_families)},
    }


def _assert_coverage_limits(
    summary: Mapping[str, Any],
    *,
    measurement_max: float,
    reasoning_min: float,
    scope: str | None = None,
) -> None:
    """Apply the family ratio to deduplicated buckets in one named scope."""

    total = summary.get("total")
    measurement = summary.get("measurement")
    reasoning = summary.get("reasoning")
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (total, measurement, reasoning)) or total < 1:
        raise QuestionContractError("question_coverage_summary_invalid")
    suffix = f":{scope}" if scope else ""
    if measurement / total > measurement_max:
        raise QuestionContractError(f"question_coverage_measurement_ratio_exceeded{suffix}")
    if reasoning / total < reasoning_min:
        raise QuestionContractError(f"question_coverage_reasoning_ratio_not_met{suffix}")


def _leaf_scope(leaf_id: Any) -> tuple[str, str]:
    if not isinstance(leaf_id, str):
        raise QuestionContractError("question_coverage_leaf_id_invalid")
    domain, separator, subdomain = leaf_id.partition(".")
    if not separator or not domain or not subdomain or "." in domain:
        raise QuestionContractError("question_coverage_leaf_id_invalid")
    return domain, subdomain


def _domain_report(
    *,
    candidates: Sequence[Mapping[str, Any]],
    leaf_ids: Sequence[str],
    expected_leaf_ids: Sequence[str],
    measurement_max: float,
    reasoning_min: float,
    minimum_expected: int,
) -> dict[str, Any]:
    """Enforce every planned domain independently and return audit detail.

    The expected slot count comes from the already approved plan, not from a
    partially generated candidate list.  That prevents a tiny domain sample
    from turning a 25 percent allocation into an arbitrary rounding decision.
    """

    if len(candidates) != len(leaf_ids):
        raise QuestionContractError("question_coverage_domain_context_count_mismatch")
    expected_by_domain: dict[str, int] = {}
    for leaf_id in expected_leaf_ids:
        domain, _ = _leaf_scope(leaf_id)
        expected_by_domain[domain] = expected_by_domain.get(domain, 0) + 1
    if not expected_by_domain:
        raise QuestionContractError("question_coverage_expected_domain_slots_empty")

    scoped: dict[str, list[tuple[Mapping[str, Any], str]]] = {}
    for candidate, leaf_id in zip(candidates, leaf_ids, strict=True):
        domain, subdomain = _leaf_scope(leaf_id)
        scoped.setdefault(domain, []).append((candidate, subdomain))

    if set(scoped) != set(expected_by_domain):
        raise QuestionContractError("question_coverage_domain_portfolio_incomplete")

    report: dict[str, Any] = {}
    for domain in sorted(expected_by_domain):
        expected_count = expected_by_domain[domain]
        entries = scoped[domain]
        if expected_count < minimum_expected:
            raise QuestionContractError(f"question_coverage_domain_minimum_sample_not_met:{domain}")
        if len(entries) != expected_count:
            raise QuestionContractError(f"question_coverage_domain_portfolio_incomplete:{domain}")
        summary = _coverage_summary([candidate for candidate, _ in entries])
        _assert_coverage_limits(
            summary,
            measurement_max=measurement_max,
            reasoning_min=reasoning_min,
            scope=domain,
        )
        subdomains: dict[str, dict[str, Any]] = {}
        for candidate, subdomain in entries:
            subdomains.setdefault(subdomain, {"candidates": []})["candidates"].append(candidate)
        report[domain] = {
            "expected_count": expected_count,
            **summary,
            "subdomains": {
                subdomain: _coverage_summary(value["candidates"])
                for subdomain, value in sorted(subdomains.items())
            },
        }
    return report


def _feedback_policy_coverage_report(
    candidates: Sequence[Mapping[str, Any]],
    feedback_policies: Sequence[LockedFeedbackPolicy | FeedbackPolicy | None],
) -> dict[str, Any]:
    """Apply each locked leaf policy to its own deduplicated portfolio.

    The global text policy remains a broad product-floor.  An accepted
    sub-domain policy can be stricter (for example, chirality's zero-measurement
    cap), so it is evaluated separately for every policy digest represented in
    the batch.
    """

    if len(candidates) != len(feedback_policies):
        raise QuestionContractError("feedback_policy_coverage_context_count_mismatch")
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    policy_by_leaf: dict[str, str] = {}
    for candidate, locked_policy in zip(candidates, feedback_policies, strict=True):
        if locked_policy is None:
            continue
        policy = locked_policy.policy if isinstance(locked_policy, LockedFeedbackPolicy) else locked_policy
        expected_family = (
            locked_policy.question_family_id if isinstance(locked_policy, LockedFeedbackPolicy) else None
        )
        contract = _mapping(_mapping(candidate, "candidate").get("question_contract"), "question_contract")
        try:
            family_id = validate_feedback_policy_contract(
                contract,
                locked_policy=locked_policy,
                expected_question_family_id=expected_family,
            )
        except FeedbackPolicyError as exc:
            raise QuestionContractError(str(exc)) from exc
        previous_digest = policy_by_leaf.setdefault(policy.leaf_id, policy.content_digest)
        if previous_digest != policy.content_digest:
            raise QuestionContractError("feedback_policy_leaf_digest_conflict")
        key = (policy.leaf_id, policy.content_digest)
        group = grouped.setdefault(
            key,
            {
                "policy": policy,
                "candidates": [],
                "question_family_ids": [],
            },
        )
        group["candidates"].append(candidate)
        group["question_family_ids"].append(family_id)

    report: dict[str, Any] = {}
    for (leaf_id, policy_digest), group in sorted(grouped.items()):
        policy = group["policy"]
        summary = _coverage_summary(group["candidates"])
        _assert_coverage_limits(
            summary,
            measurement_max=policy.measurement_max_fraction,
            reasoning_min=policy.non_measurement_min_fraction,
            scope=f"feedback:{leaf_id}",
        )
        family_counts: dict[str, int] = {}
        for family_id in group["question_family_ids"]:
            family_counts[family_id] = family_counts.get(family_id, 0) + 1
        report[leaf_id] = {
            "policy_digest": policy_digest,
            "measurement_max_fraction": policy.measurement_max_fraction,
            "non_measurement_min_fraction": policy.non_measurement_min_fraction,
            "question_family_counts": {key: family_counts[key] for key in sorted(family_counts)},
            **summary,
        }
    return report


def validate_question_coverage(
    candidates: Sequence[Mapping[str, Any]],
    *,
    policy: QuestionTextPolicy,
    approved_profile_override: Mapping[str, Any] | None = None,
    leaf_ids: Sequence[str] | None = None,
    expected_leaf_ids: Sequence[str] | None = None,
    feedback_policies: Sequence[LockedFeedbackPolicy | FeedbackPolicy | None] | None = None,
) -> dict[str, Any]:
    """Enforce global and, when plan context is supplied, per-domain quotas.

    The default keeps the original global validation for isolated fixtures and
    one-off local pilots.  Live, review, and release paths must provide both
    ``leaf_ids`` and the plan's ``expected_leaf_ids`` so every represented
    domain satisfies the same measurement/reasoning allocation independently.
    """

    if not candidates:
        raise QuestionContractError("question_coverage_candidates_empty")
    if (leaf_ids is None) != (expected_leaf_ids is None):
        raise QuestionContractError("question_coverage_domain_context_incomplete")
    measurement_max, reasoning_min, minimum_expected = _coverage_limits(
        policy=policy,
        approved_profile_override=approved_profile_override,
    )
    summary = _coverage_summary(candidates)
    _assert_coverage_limits(
        summary,
        measurement_max=measurement_max,
        reasoning_min=reasoning_min,
    )
    result: dict[str, Any] = {
        key: summary[key]
        for key in ("raw_total", "total", "measurement", "reasoning")
    }
    if leaf_ids is not None and expected_leaf_ids is not None:
        result["domains"] = _domain_report(
            candidates=candidates,
            leaf_ids=leaf_ids,
            expected_leaf_ids=expected_leaf_ids,
            measurement_max=measurement_max,
            reasoning_min=reasoning_min,
            minimum_expected=minimum_expected,
        )
    if feedback_policies is not None:
        result["feedback_policies"] = _feedback_policy_coverage_report(candidates, feedback_policies)
    return result
