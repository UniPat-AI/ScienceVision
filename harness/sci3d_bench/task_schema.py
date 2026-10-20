"""TaskSpec — single data contract crossing the A↔B layer boundary."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional
import json

VClass = Literal["V1", "V2", "V3", "V4", "V5", "V6"]
ALLOWED_V_CLASSES = {"V1", "V2", "V3", "V4", "V5", "V6"}
ALLOWED_FORMATS = {
    "obj", "ply", "stl", "glb", "gltf", "step", "iges", "urdf", "sdf",
    "nii", "nii.gz", "nrrd", "dcm_dir", "pdb", "mmcif", "sdf_mol",
    "xyz", "las", "usd", "usdz", "npy_m3d", "cif",
}

@dataclass(frozen=True)
class AssetRef:
    path: str
    format: str
    sha256: Optional[str] = None
    asset_id: Optional[str] = None
    role: Optional[str] = None
    viewer: Optional[str] = None
    repo_path: Optional[str] = None

@dataclass(frozen=True)
class ImageRef:
    path: str
    source: Literal["manual", "canonical"]
    camera: Optional[dict] = None

@dataclass(frozen=True)
class AnswerSchema:
    # Newer task contracts also use ``mechanical_structured``.  Keep this
    # schema permissive because scorer-specific validation owns the stricter
    # semantics for each answer family.
    kind: str
    fields: Optional[Any] = None
    field_specs: Optional[dict] = None

@dataclass(frozen=True)
class GroundTruth:
    value: Any
    notes: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = None
    artifact: Optional[dict] = None

@dataclass(frozen=True)
class ScorerSpec:
    primary: str
    secondary: tuple = field(default_factory=tuple)
    checks: tuple = field(default_factory=tuple)
    aggregation: Optional[str] = None
    tolerance: Optional[dict] = None
    expected_fields: tuple = field(default_factory=tuple)
    axis_order: tuple = field(default_factory=tuple)
    config: dict = field(default_factory=dict)

@dataclass(frozen=True)
class Provenance:
    source_dataset: Optional[str]
    source_qid: Optional[str]
    lifted_at: Optional[str]
    lifter_version: Optional[str]
    generator: Optional[str] = None
    case_id: Optional[str] = None
    case_manifest_sha256: Optional[str] = None
    case_sha256: Optional[str] = None
    solver: Optional[str] = None
    input_sha256: Optional[str] = None

@dataclass(frozen=True)
class TaskSpec:
    schema_version: str
    task_id: str
    v_class: VClass
    question: str
    asset: AssetRef
    prerendered_image: Optional[ImageRef]
    answer_schema: AnswerSchema
    ground_truth: Optional[GroundTruth]
    scorer: ScorerSpec
    provenance: Provenance
    mode_overrides: Optional[dict] = None
    mechanical_contract: Optional[dict] = None
    task_type: Optional[str] = None
    asset_contract: Optional[dict] = None
    assets: tuple[AssetRef, ...] = field(default_factory=tuple)
    input_closure: Optional[dict] = None
    required_evidence: tuple = field(default_factory=tuple)
    viewer_requirements: tuple = field(default_factory=tuple)
    active_vision_evidence: Optional[dict] = None
    terminal_answer: Optional[dict] = None
    trace_requirements: Optional[dict] = None
    tool_policy: Optional[dict] = None
    recompute_receipt: Optional[dict] = None
    challenge_controls: Optional[dict] = None
    asset_grounding: Optional[dict] = None
    grounding_receipt: Optional[dict] = None
    grounding_receipts: tuple = field(default_factory=tuple)
    target_selection: Optional[dict] = None
    entity_selection: Optional[dict] = None
    render_contract: Optional[dict] = None
    mask_id: Optional[Any] = None
    asset_hashes: Optional[Any] = None
    evaluation_eligible: Optional[bool] = None
    evaluation_ineligible_reason: Optional[str] = None
    raw_task: Optional[dict] = None


def _validate(d: dict) -> None:
    if not d.get("task_id"):
        raise ValueError("task_id cannot be empty")
    if not (d.get("question") or d.get("instruction")):
        raise ValueError("question/instruction cannot be empty")
    if d.get("v_class") not in ALLOWED_V_CLASSES:
        raise ValueError(f"v_class must be one of {ALLOWED_V_CLASSES}, got {d.get('v_class')!r}")
    if d.get("schema_version") != "1.0":
        raise ValueError(f"unsupported schema_version: {d.get('schema_version')!r}")
    fmt = d.get("asset", {}).get("format")
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"asset.format must be one of {ALLOWED_FORMATS}, got {fmt!r}")


def load_taskspec(path: Path) -> TaskSpec:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e
    _validate(data)

    def asset_ref(row: dict) -> AssetRef:
        return AssetRef(
            path=str(row.get("path") or row.get("asset_path") or ""),
            format=str(row.get("format") or row.get("asset_format") or "obj"),
            sha256=row.get("sha256"),
            asset_id=row.get("asset_id"),
            role=row.get("role"),
            viewer=row.get("viewer"),
            repo_path=row.get("repo_path"),
        )

    asset = asset_ref(data["asset"])
    img = ImageRef(**data["prerendered_image"]) if data.get("prerendered_image") else None
    answer_data = data["answer_schema"]
    answer_schema = AnswerSchema(
        kind=str(answer_data["kind"]),
        fields=answer_data.get("fields"),
        field_specs=answer_data.get("field_specs"),
    )
    gt_data = data.get("ground_truth")
    gt = (
        GroundTruth(
            value=gt_data.get("value"),
            notes=gt_data.get("notes"),
            status=gt_data.get("status"),
            type=gt_data.get("type"),
            artifact=gt_data.get("artifact"),
        )
        if isinstance(gt_data, dict)
        else (GroundTruth(value=gt_data) if gt_data is not None else None)
    )
    scorer_config = dict(data["scorer"])
    if isinstance(data.get("scorer_config"), dict):
        scorer_config.update(data["scorer_config"])
    scorer = ScorerSpec(
        primary=data["scorer"]["primary"],
        secondary=tuple(data["scorer"].get("secondary", [])),
        checks=tuple(data["scorer"].get("checks", [])),
        aggregation=data["scorer"].get("aggregation"),
        tolerance=data["scorer"].get("tolerance"),
        expected_fields=tuple(data["scorer"].get("expected_fields", [])),
        axis_order=tuple(data["scorer"].get("axis_order", [])),
        config=scorer_config,
    )
    provenance_data = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    prov = Provenance(
        source_dataset=provenance_data.get("source_dataset"),
        source_qid=provenance_data.get("source_qid"),
        lifted_at=provenance_data.get("lifted_at"),
        lifter_version=provenance_data.get("lifter_version"),
        generator=provenance_data.get("generator"),
        case_id=provenance_data.get("case_id"),
        case_manifest_sha256=provenance_data.get("case_manifest_sha256"),
        case_sha256=provenance_data.get("case_sha256"),
        solver=provenance_data.get("solver"),
        input_sha256=provenance_data.get("input_sha256"),
    )
    return TaskSpec(
        schema_version=data["schema_version"],
        task_id=data["task_id"],
        v_class=data["v_class"],
        question=data.get("question") or data["instruction"],
        asset=asset,
        prerendered_image=img,
        answer_schema=answer_schema,
        ground_truth=gt,
        scorer=scorer,
        provenance=prov,
        mode_overrides=data.get("mode_overrides"),
        mechanical_contract=data.get("mechanical_contract"),
        task_type=data.get("task_type"),
        asset_contract=data.get("asset_contract"),
        assets=tuple(asset_ref(row) for row in data.get("assets", []) if isinstance(row, dict)),
        input_closure=data.get("input_closure"),
        required_evidence=tuple(data.get("required_evidence", []) or []),
        viewer_requirements=tuple(data.get("viewer_requirements", []) or []),
        active_vision_evidence=data.get("active_vision_evidence"),
        terminal_answer=data.get("terminal_answer"),
        trace_requirements=data.get("trace_requirements"),
        tool_policy=data.get("tool_policy"),
        recompute_receipt=data.get("recompute_receipt"),
        challenge_controls=data.get("challenge_controls"),
        asset_grounding=data.get("asset_grounding"),
        grounding_receipt=data.get("grounding_receipt"),
        grounding_receipts=tuple(data.get("grounding_receipts", []) or []),
        target_selection=data.get("target_selection"),
        entity_selection=data.get("entity_selection"),
        render_contract=data.get("render_contract"),
        mask_id=data.get("mask_id"),
        asset_hashes=data.get("asset_hashes"),
        evaluation_eligible=data.get("evaluation_eligible"),
        evaluation_ineligible_reason=data.get("evaluation_ineligible_reason"),
        raw_task=data,
    )
