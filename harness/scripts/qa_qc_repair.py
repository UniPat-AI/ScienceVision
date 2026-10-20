"""Apply audited QC repairs and publish a fail-closed scale-v2 task set.

The source task JSONL files are immutable inputs.  Repairs are deliberately
limited to contracts whose intended answer is already present and verified;
anything that would require inventing ground truth, entity binding, or runtime
enforcement is quarantined with its QC evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft7Validator


POLICY_VERSION = "sciencevision.qa_qc.repair.v2"
MANIFEST_VERSION = "sciencevision.qa_qc.repair_manifest.v2"
BUILD_VERSION = "sciencevision.qa_qc.repair_build.v2"
SOURCE_LOCK_VERSION = "sciencevision.qa_qc.source_lock.v1"
QC_REPORT_SCHEMA_VERSION = "1.4"
DEFAULT_GENERATED_AT = "1970-01-01T00:00:00Z"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = (
    ROOT
    / "QA-Gen"
    / "ScienceVisionTaskGenerator"
    / "schemas"
    / "sciencevision_task.schema.json"
)
DEFAULT_SOURCE_LOCK = ROOT / "docs" / "qa-gen" / "SCALE_V1_REVIEWED_20260808_LOCK.json"

KNOWN_FAMILIES = {
    "med_ct_lesion",
    "med_mri_anatomy",
    "bio_collision_check",
    "bio_docking_plausibility",
    "bio_multi_structure_compare",
    "chem_binding_geometry",
    "chem_conformation",
    "chem_crystallography",
}
KNOWN_FINDING_CODES = {
    "asked_output_not_scored",
    "asset_identifier_shortcut",
    "ground_truth_missing",
    "ground_truth_not_verified",
    "ground_truth_outside_answer_choices",
    "metadata_shortcut",
    "package_compute_shortcut",
    "ranking_margin_below_tolerance",
    "shortcut_control_unenforced",
    "template_reuse",
    "confirmed_shortcut",
}
UNVERIFIED_GT = {None, "", "seed", "unverified", "needs_expert_annotation"}
DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
GENERATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RepairError(ValueError):
    """Raised before publication when an input cannot be repaired safely."""


@dataclass(frozen=True)
class TaskRecord:
    task: dict[str, Any]
    source_path: Path
    source_line: int
    source_task_sha256: str


@dataclass(frozen=True)
class QCRecord:
    classification: dict[str, Any]
    source_path: Path
    source_line: int


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _json_line(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepairError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RepairError(f"expected a JSON object in {path}")
    return value


def _read_jsonl(path: Path) -> list[tuple[dict[str, Any], int, str]]:
    records: list[tuple[dict[str, Any], int, str]] = []
    try:
        lines = path.read_bytes().splitlines()
    except OSError as exc:
        raise RepairError(f"cannot read JSONL {path}: {exc}") from exc
    for line_number, raw_line in enumerate(lines, 1):
        if not raw_line.strip():
            raise RepairError(f"blank JSONL record at {path}:{line_number}")
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RepairError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise RepairError(f"expected object at {path}:{line_number}")
        records.append((value, line_number, _sha256_bytes(raw_line)))
    return records


def _task_family(task: dict[str, Any]) -> str:
    knowledge = task.get("knowledge")
    family = knowledge.get("direction_id") if isinstance(knowledge, dict) else None
    if not isinstance(family, str) or not family:
        raise RepairError(f"task {task.get('task_id')!r} has no knowledge.direction_id")
    if family not in KNOWN_FAMILIES:
        raise RepairError(f"task {task.get('task_id')!r} has unknown family {family!r}")
    return family


def _load_tasks(source_dir: Path) -> tuple[list[TaskRecord], list[dict[str, Any]]]:
    paths = sorted(source_dir.glob("*scale-v1*_final_tasks.jsonl"))
    if not paths:
        raise RepairError(f"no scale-v1 final task JSONL files found in {source_dir}")
    records: list[TaskRecord] = []
    files: list[dict[str, Any]] = []
    seen: dict[str, tuple[Path, int]] = {}
    for path in paths:
        loaded = _read_jsonl(path)
        file_domains = {task.get("domain") for task, _, _ in loaded}
        if len(file_domains) != 1:
            raise RepairError(f"source file {path} must contain exactly one domain")
        file_domain = next(iter(file_domains))
        for task, line_number, task_hash in loaded:
            task_id = task.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise RepairError(f"missing task_id at {path}:{line_number}")
            if task_id in seen:
                prior_path, prior_line = seen[task_id]
                raise RepairError(
                    f"duplicate task_id {task_id!r} at {path}:{line_number}; "
                    f"first seen at {prior_path}:{prior_line}"
                )
            seen[task_id] = (path, line_number)
            _task_family(task)
            domain = task.get("domain")
            if not isinstance(domain, str) or not DOMAIN_RE.fullmatch(domain):
                raise RepairError(f"task {task_id!r} has invalid domain {domain!r}")
            records.append(TaskRecord(task, path, line_number, task_hash))
        files.append(
            {
                "domain": file_domain,
                "path": _stable_path(path),
                "sha256": _sha256_path(path),
                "task_count": len(loaded),
            }
        )
    return records, files


def _load_qc(qc_dir: Path) -> tuple[dict[str, QCRecord], list[dict[str, Any]]]:
    paths = sorted(qc_dir.glob("*/task_classifications.jsonl"))
    if not paths:
        raise RepairError(f"no task_classifications.jsonl files found in {qc_dir}")
    records: dict[str, QCRecord] = {}
    files: list[dict[str, Any]] = []
    for path in paths:
        report_path = path.with_name("report.json")
        report = _read_json(report_path)
        if report.get("schema_version") != QC_REPORT_SCHEMA_VERSION:
            raise RepairError(
                f"{report_path} uses QC schema {report.get('schema_version')!r}; "
                f"required {QC_REPORT_SCHEMA_VERSION!r}"
            )
        loaded = _read_jsonl(path)
        report_count = report.get("summary", {}).get("task_count")
        if report_count != len(loaded):
            raise RepairError(
                f"QC count mismatch for {path}: report={report_count!r}, "
                f"classifications={len(loaded)}"
            )
        files.append(
            {
                "domain": path.parent.name,
                "classification_path": _stable_path(path),
                "classification_sha256": _sha256_path(path),
                "report_path": _stable_path(report_path),
                "report_sha256": _sha256_path(report_path),
                "qc_schema_version": report["schema_version"],
                "task_count": len(loaded),
            }
        )
        for classification, line_number, _ in loaded:
            task_id = classification.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                raise RepairError(f"missing task_id at {path}:{line_number}")
            if task_id in records:
                prior = records[task_id]
                raise RepairError(
                    f"duplicate QC task_id {task_id!r} at {path}:{line_number}; "
                    f"first seen at {prior.source_path}:{prior.source_line}"
                )
            family = classification.get("family")
            if family not in KNOWN_FAMILIES:
                raise RepairError(
                    f"QC task {task_id!r} has unknown family {family!r} at "
                    f"{path}:{line_number}"
                )
            if classification.get("grade") not in {
                "qualified",
                "minor",
                "needs_fix",
                "unacceptable",
            }:
                raise RepairError(f"QC task {task_id!r} has invalid grade")
            for field in ("labels", "reasons", "evidence", "leakage"):
                if not isinstance(classification.get(field), list):
                    raise RepairError(f"QC task {task_id!r} has invalid {field}")
            if classification.get("evaluation_status") not in {
                "complete",
                "partial",
                "missing",
            }:
                raise RepairError(f"QC task {task_id!r} has invalid evaluation_status")
            reasons = classification["reasons"]
            reason_codes: set[str] = set()
            for reason in reasons:
                code = reason.get("code") if isinstance(reason, dict) else None
                if not isinstance(code, str) or not code:
                    raise RepairError(
                        f"QC task {task_id!r} has a reason without a code"
                    )
                reason_codes.add(code)
            unknown_codes = sorted(
                code for code in reason_codes if code not in KNOWN_FINDING_CODES
            )
            if unknown_codes:
                raise RepairError(
                    f"QC task {task_id!r} has no repair policy for findings {unknown_codes}"
                )
            records[task_id] = QCRecord(classification, path, line_number)
    return records, files


def _validate_source_lock(
    source_lock_path: Path,
    source_files: list[dict[str, Any]],
    qc_files: list[dict[str, Any]],
) -> dict[str, Any]:
    if not source_lock_path.is_file():
        raise RepairError(f"source lock is missing: {source_lock_path}")
    source_lock = _read_json(source_lock_path)
    if source_lock.get("schema_version") != SOURCE_LOCK_VERSION:
        raise RepairError(
            f"source lock uses schema {source_lock.get('schema_version')!r}; "
            f"required {SOURCE_LOCK_VERSION!r}"
        )
    if source_lock.get("source_dataset") != "scale-v1":
        raise RepairError("source lock must bind source_dataset 'scale-v1'")
    if source_lock.get("qc_schema_version") != QC_REPORT_SCHEMA_VERSION:
        raise RepairError(f"source lock QC schema must be {QC_REPORT_SCHEMA_VERSION!r}")
    reviewed_at = source_lock.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", reviewed_at
    ):
        raise RepairError("source lock reviewed_at must be an ISO date")
    if source_lock.get("source_files") != source_files:
        raise RepairError("source lock mismatch: source files changed after QC review")
    if source_lock.get("qc_files") != qc_files:
        raise RepairError("source lock mismatch: QC files changed after review")
    return {
        "path": _stable_path(source_lock_path),
        "sha256": _sha256_path(source_lock_path),
        "schema_version": SOURCE_LOCK_VERSION,
        "reviewed_batch": source_lock.get("reviewed_batch"),
        "reviewed_at": reviewed_at,
    }


def _strict_join(tasks: list[TaskRecord], qc: dict[str, QCRecord]) -> None:
    task_ids = {record.task["task_id"] for record in tasks}
    qc_ids = set(qc)
    missing = sorted(task_ids - qc_ids)
    extra = sorted(qc_ids - task_ids)
    if missing or extra:
        raise RepairError(
            "task/QC join is not 1:1: "
            f"tasks={len(task_ids)}, qc={len(qc_ids)}, "
            f"missing_qc={missing[:10]}, extra_qc={extra[:10]}"
        )
    for record in tasks:
        task_id = record.task["task_id"]
        source_family = _task_family(record.task)
        qc_family = qc[task_id].classification.get("family")
        if source_family != qc_family:
            raise RepairError(
                f"family mismatch for {task_id!r}: task={source_family!r}, qc={qc_family!r}"
            )


def _expect_fields(task: dict[str, Any], expected: list[str]) -> None:
    task_id = task["task_id"]
    for contract_name in ("answer_schema", "terminal_answer"):
        contract = task.get(contract_name)
        fields = contract.get("fields") if isinstance(contract, dict) else None
        if fields != expected:
            raise RepairError(
                f"task {task_id!r} has unsupported {contract_name}.fields {fields!r}"
            )


def _repair_ct(task: dict[str, Any]) -> list[str]:
    _expect_fields(task, ["laterality"])
    value = task.get("ground_truth", {}).get("value")
    laterality = value.get("laterality") if isinstance(value, dict) else None
    allowed = ["left", "midline", "right"]
    if laterality not in allowed:
        raise RepairError(
            f"task {task['task_id']!r} has unsupported laterality GT {laterality!r}"
        )
    task["instruction"] = (
        "Working in the displayed CT volume, where relative to the body midline does "
        "the highlighted lesion lie? Answer left, midline, or right."
    )
    task["answer_schema"]["choices"] = allowed
    task["terminal_answer"]["choices"] = allowed
    checks = task.get("scorer", {}).get("checks")
    if not isinstance(checks, list) or "laterality_match" not in checks:
        raise RepairError(f"task {task['task_id']!r} lacks laterality_match scorer")
    return ["medical_ct_allow_midline_v1"]


def _repair_mri(task: dict[str, Any]) -> list[str]:
    _expect_fields(task, ["extent_vox", "principal_axis"])
    value = task.get("ground_truth", {}).get("value")
    extent = value.get("extent_vox") if isinstance(value, dict) else None
    if not (
        isinstance(extent, list)
        and len(extent) == 3
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in extent
        )
    ):
        raise RepairError(f"task {task['task_id']!r} has no verified extent_vox GT")
    task["instruction"] = (
        "Estimate the three-dimensional bounding extent of the highlighted anatomical "
        "structure. Return exactly one JSON object: "
        '{"extent_vox":[e0,e1,e2]}. The entries are voxel counts in fixed NIfTI '
        "array order [axis-0, axis-1, axis-2]; do not sort or permute them."
    )
    axis_order = ["axis-0", "axis-1", "axis-2"]
    task["answer_schema"].update(
        {
            "fields": ["extent_vox"],
            "field_specs": {
                "extent_vox": {
                    "type": "integer_triplet",
                    "unit": "voxel",
                    "length": 3,
                    "axis_order": axis_order,
                }
            },
        }
    )
    task["terminal_answer"]["fields"] = ["extent_vox"]
    task["terminal_answer"]["format"] = "json_object"
    task["terminal_answer"]["axis_order"] = axis_order
    task["ground_truth"]["axis_order"] = axis_order
    grounding = task.get("asset_grounding")
    verified = (
        grounding.get("verified_entities") if isinstance(grounding, dict) else None
    )
    if isinstance(verified, dict):
        verified["extent_vox_axis_order"] = axis_order
    checks = task.get("scorer", {}).get("checks")
    tolerance = task.get("scorer", {}).get("tolerance")
    if not isinstance(checks, list) or "extent_tolerance" not in checks:
        raise RepairError(f"task {task['task_id']!r} lacks extent_vox scorer support")
    if (
        not isinstance(tolerance, dict)
        or isinstance(tolerance.get("extent_vox"), bool)
        or tolerance.get("extent_vox") != 3
    ):
        raise RepairError(
            f"task {task['task_id']!r} must use a 3 voxel extent tolerance"
        )
    task["scorer"] = {
        "primary": "extent_voxel_tolerance_v1",
        "secondary": [],
        "checks": ["extent_vox_axis_order", "extent_vox_per_axis_tolerance"],
        "aggregation": "all_required_with_partial_credit",
        "tolerance": {"extent_vox": 3},
        "expected_fields": ["extent_vox"],
        "axis_order": axis_order,
    }
    return ["medical_mri_extent_only_v1"]


def _repair_collision(task: dict[str, Any]) -> list[str]:
    _expect_fields(task, ["has_severe_clash"])
    value = task.get("ground_truth", {}).get("value")
    clash = value.get("has_severe_clash") if isinstance(value, dict) else None
    if clash not in {"yes", "no"}:
        raise RepairError(
            f"task {task['task_id']!r} has unsupported clash GT {clash!r}"
        )
    expected = (
        "Does the displayed complex contain a severe non-bonded clash between the ligand "
        "and the receptor? Answer yes or no; if yes, name the atom pair and the distance."
    )
    if task.get("instruction") != expected:
        raise RepairError(f"task {task['task_id']!r} has unsupported collision wording")
    task["instruction"] = (
        "Does the displayed complex contain a severe non-bonded clash between the ligand "
        "and the receptor? Answer yes or no."
    )
    scorer = task.get("scorer")
    if not isinstance(scorer, dict) or "clash_binary_match" not in scorer.get(
        "checks", []
    ):
        raise RepairError(f"task {task['task_id']!r} lacks clash_binary_match scorer")
    scorer.pop("tolerance", None)
    return ["biology_collision_binary_only_v1"]


def _repair_comparison(task: dict[str, Any], qc_codes: set[str]) -> list[str]:
    _expect_fields(task, ["structure_with_shortest_contact"])
    if "asked_output_not_scored" not in qc_codes:
        return []
    suffix = "请给出该结构的标识，并说明你的判断依据。"
    replacement = "请只给出该结构的标识。"
    instruction = task.get("instruction")
    if not isinstance(instruction, str) or suffix not in instruction:
        raise RepairError(
            f"task {task['task_id']!r} has an unknown comparison rationale repair"
        )
    task["instruction"] = instruction.replace(suffix, replacement)
    return ["biology_comparison_remove_unscored_rationale_v1"]


def _apply_repairs(
    source_task: dict[str, Any], classification: dict[str, Any]
) -> tuple[dict[str, Any], list[str], set[str]]:
    task = copy.deepcopy(source_task)
    family = _task_family(task)
    qc_codes = {
        reason["code"]
        for reason in classification.get("reasons", [])
        if isinstance(reason, dict) and isinstance(reason.get("code"), str)
    }
    repairs: list[str]
    repaired_findings: set[str]
    if family == "med_ct_lesion":
        repairs = _repair_ct(task)
        repaired_findings = {"ground_truth_outside_answer_choices"}
    elif family == "med_mri_anatomy":
        repairs = _repair_mri(task)
        repaired_findings = {"asked_output_not_scored", "ground_truth_missing"}
    elif family == "bio_collision_check":
        repairs = _repair_collision(task)
        repaired_findings = {"asked_output_not_scored"}
    elif family == "bio_multi_structure_compare":
        repairs = _repair_comparison(task, qc_codes)
        repaired_findings = {"asked_output_not_scored"} if repairs else set()
    elif family in {
        "bio_docking_plausibility",
        "chem_binding_geometry",
        "chem_conformation",
        "chem_crystallography",
    }:
        repairs = []
        repaired_findings = set()
    else:  # pragma: no cover - _task_family already guards this branch.
        raise RepairError(f"no repair handler for family {family!r}")
    return task, repairs, repaired_findings


def _leakage_is_hard_blocker(item: dict[str, Any]) -> bool:
    label = item.get("label")
    feasibility = item.get("route_feasibility")
    exploitation = item.get("answer_exploitation")
    return bool(
        label in {"leakage.asset", "leakage.tool", "leakage.knowledge"}
        and isinstance(feasibility, dict)
        and feasibility.get("status") == "confirmed"
        and isinstance(exploitation, dict)
        and exploitation.get("status") == "observed"
    )


def _receipt_values(classification: dict[str, Any], key: str) -> Iterable[Any]:
    for item in classification.get("leakage", []):
        if not isinstance(item, dict):
            continue
        feasibility = item.get("route_feasibility")
        if not isinstance(feasibility, dict):
            continue
        for receipt in feasibility.get("receipts", []):
            if isinstance(receipt, dict) and key in receipt:
                yield receipt[key]


def _quarantine_reasons(
    task: dict[str, Any],
    classification: dict[str, Any],
    repaired_findings: set[str],
) -> list[str]:
    reasons: list[str] = []
    family = _task_family(task)
    qc_codes = {
        reason["code"]
        for reason in classification.get("reasons", [])
        if isinstance(reason, dict) and isinstance(reason.get("code"), str)
    }
    unresolved = qc_codes - repaired_findings - {"template_reuse"}

    gt = task.get("ground_truth")
    gt_status = gt.get("status") if isinstance(gt, dict) else None
    gt_value = gt.get("value") if isinstance(gt, dict) else None
    if gt_status in UNVERIFIED_GT or gt_value is None:
        reasons.append("ground_truth_unverified_or_null")
    if (
        "ground_truth_not_verified" in unresolved
        or "ground_truth_missing" in unresolved
    ):
        reasons.append("ground_truth_qc_blocker")
    if "ranking_margin_below_tolerance" in unresolved:
        reasons.append("comparison_margin_below_tolerance")

    leakage = [
        item for item in classification.get("leakage", []) if isinstance(item, dict)
    ]
    has_confirmed_route = any(
        isinstance(item.get("route_feasibility"), dict)
        and item["route_feasibility"].get("status") == "confirmed"
        for item in leakage
    )
    if (
        "confirmed_shortcut" in qc_codes
        or any(_leakage_is_hard_blocker(item) for item in leakage)
        or (classification.get("grade") == "unacceptable" and has_confirmed_route)
    ):
        reasons.append("confirmed_shortcut")

    if family == "chem_conformation":
        verified = task.get("asset_grounding", {}).get("verified_entities", {})
        atom_binding_keys = {"atom_ids", "dihedral_atom_ids", "central_bond_atom_ids"}
        if not isinstance(verified, dict) or not atom_binding_keys.intersection(
            verified
        ):
            reasons.append("conformation_atom_binding_missing")
    if family == "chem_binding_geometry" and any(
        value in {"candidate", "unresolved"}
        for value in _receipt_values(classification, "route_status")
    ):
        reasons.append("binding_entity_selection_ambiguous")

    if classification.get("grade") == "unacceptable":
        reasons.append("qc_grade_unacceptable")
    remaining_contract_codes = unresolved & {
        "asked_output_not_scored",
        "ground_truth_outside_answer_choices",
    }
    if remaining_contract_codes:
        reasons.extend(
            f"unrepaired_qc_finding:{code}" for code in sorted(remaining_contract_codes)
        )
    return list(dict.fromkeys(reasons))


def _receipt_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: receipt.get(key)
        for key in (
            "source",
            "source_sha256",
            "source_schema_version",
            "result_index",
            "status",
            "route",
            "feasibility",
            "route_status",
            "unique_answer_confirmed",
        )
        if key in receipt
    }
    inputs = receipt.get("inputs")
    selection = inputs.get("selection_rule") if isinstance(inputs, dict) else None
    if isinstance(selection, dict) and "status" in selection:
        summary["selection_rule_status"] = selection["status"]
    comparison = receipt.get("comparison")
    if isinstance(comparison, dict):
        summary["comparison"] = {
            key: comparison.get(key)
            for key in (
                "selection_rule_proven",
                "unique_answer_computable",
                "answer_confirmed",
                "status_reason",
            )
            if key in comparison
        }
    return summary


def _evidence_summary(classification: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    scalar_keys = (
        "status",
        "concept",
        "field",
        "ground_truth",
        "allowed_choices",
        "choices_source",
        "ranking_margin_A",
        "scorer_tolerance_A",
        "below_tolerance",
        "count",
    )
    for evidence in classification.get("evidence", []):
        if not isinstance(evidence, dict):
            continue
        summary: dict[str, Any] = {
            "finding_code": evidence.get("finding_code"),
            "labels": evidence.get("labels", []),
        }
        detail = evidence.get("detail")
        if isinstance(detail, dict):
            selected = {key: detail[key] for key in scalar_keys if key in detail}
            identities = detail.get("identities")
            if isinstance(identities, list):
                selected["identities"] = [
                    {
                        key: identity[key]
                        for key in (
                            "kind",
                            "value",
                            "identifier_source",
                            "source_field",
                        )
                        if isinstance(identity, dict) and key in identity
                    }
                    for identity in identities
                    if isinstance(identity, dict)
                ]
            routes = detail.get("routes")
            if isinstance(routes, list):
                selected["routes"] = [
                    {
                        "route_id": route.get("route_id"),
                        "property": route.get("property"),
                        "matched_declared_controls": route.get(
                            "matched_declared_controls", []
                        ),
                        "matched_enforced_controls": route.get(
                            "matched_enforced_controls", []
                        ),
                        "receipt_statuses": [
                            _receipt_summary(receipt)
                            for receipt in route.get("oracle_receipts", [])
                            if isinstance(receipt, dict)
                        ],
                    }
                    for route in routes
                    if isinstance(route, dict)
                ]
            receipts = detail.get("oracle_receipts")
            if isinstance(receipts, list):
                selected["oracle_receipts"] = [
                    _receipt_summary(receipt)
                    for receipt in receipts
                    if isinstance(receipt, dict)
                ]
            controls = detail.get("controls")
            if isinstance(controls, dict):
                selected["controls"] = {
                    key: controls.get(key)
                    for key in (
                        "must_be_derived_from_3d_interaction",
                        "declared_shortcut_risk",
                        "declared_controls",
                        "enforced_controls",
                    )
                    if key in controls
                }
            summary["detail"] = selected
        summaries.append(summary)
    return summaries


def _leakage_summary(classification: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in classification.get("leakage", []):
        if not isinstance(item, dict):
            continue
        feasibility = item.get("route_feasibility")
        feasibility = feasibility if isinstance(feasibility, dict) else {}
        static = feasibility.get("static_evidence")
        static = static if isinstance(static, dict) else {}
        controls = static.get("controls")
        summaries.append(
            {
                "label": item.get("label"),
                "finding_code": item.get("finding_code"),
                "route_requirements": item.get("route_requirements", []),
                "route_feasibility": {
                    "status": feasibility.get("status"),
                    "receipts": [
                        _receipt_summary(receipt)
                        for receipt in feasibility.get("receipts", [])
                        if isinstance(receipt, dict)
                    ],
                },
                "identifier_sources": item.get("identifier_sources", []),
                "affected_baselines": item.get("affected_baselines", []),
                "baseline_access": item.get("baseline_access", {}),
                "package_availability": item.get("package_availability", {}),
                "answer_exploitation": item.get("answer_exploitation"),
                "declared_controls": (
                    controls.get("declared_controls", {})
                    if isinstance(controls, dict)
                    else {}
                ),
                "enforced_controls": (
                    controls.get("enforced_controls", [])
                    if isinstance(controls, dict)
                    else []
                ),
            }
        )
    return summaries


def _validate_contract(task: dict[str, Any]) -> None:
    task_id = task["task_id"]
    answer = task.get("answer_schema")
    terminal = task.get("terminal_answer")
    if not isinstance(answer, dict) or not isinstance(terminal, dict):
        raise RepairError(f"task {task_id!r} lacks answer/terminal contract")
    if answer.get("kind") != terminal.get("kind") or answer.get(
        "fields"
    ) != terminal.get("fields"):
        raise RepairError(f"task {task_id!r} has mismatched answer/terminal contract")
    fields = answer.get("fields")
    value = task.get("ground_truth", {}).get("value")
    if not isinstance(fields, list) or not fields:
        raise RepairError(f"task {task_id!r} has empty answer fields")
    if isinstance(value, dict):
        missing = [field for field in fields if field not in value]
        if missing:
            raise RepairError(f"task {task_id!r} is missing GT fields {missing}")
    scorer = task.get("scorer")
    if (
        not isinstance(scorer, dict)
        or not scorer.get("primary")
        or not scorer.get("checks")
    ):
        raise RepairError(f"task {task_id!r} lacks scorer contract")


def _validate_schema(tasks: Iterable[dict[str, Any]], schema_path: Path) -> None:
    schema = _read_json(schema_path)
    validator = Draft7Validator(schema)
    for task in tasks:
        errors = sorted(validator.iter_errors(task), key=lambda item: list(item.path))
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.path) or "<root>"
            raise RepairError(
                f"schema validation failed for {task.get('task_id')!r} at {location}: "
                f"{error.message}"
            )


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(_json_line(value))


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(
        candidate for candidate in path.rglob("*") if candidate.is_file()
    ):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _publish(staging: Path, output_dir: Path) -> bool:
    if output_dir.exists() and _tree_digest(staging) == _tree_digest(output_dir):
        shutil.rmtree(staging)
        return False
    if not output_dir.exists():
        os.replace(staging, output_dir)
        return True
    backup = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.backup-", dir=output_dir.parent)
    )
    backup.rmdir()
    os.replace(output_dir, backup)
    try:
        os.replace(staging, output_dir)
    except BaseException:
        os.replace(backup, output_dir)
        raise
    shutil.rmtree(backup)
    return True


def _assert_disjoint(source_dir: Path, qc_dir: Path, output_dir: Path) -> None:
    source = source_dir.resolve()
    qc = qc_dir.resolve()
    output = output_dir.resolve()
    for input_path, name in ((source, "source"), (qc, "QC")):
        if (
            output == input_path
            or output in input_path.parents
            or input_path in output.parents
        ):
            raise RepairError(f"output directory overlaps {name} input: {output_dir}")


def build_scale_v2(
    source_dir: Path,
    qc_dir: Path,
    output_dir: Path,
    *,
    generated_at: str = DEFAULT_GENERATED_AT,
    schema_path: Path = DEFAULT_SCHEMA,
    source_lock_path: Path = DEFAULT_SOURCE_LOCK,
    generation_command: str | None = None,
) -> dict[str, Any]:
    """Repair, validate, and atomically publish scale-v2 candidates."""
    if not GENERATED_AT_RE.fullmatch(generated_at):
        raise RepairError(
            "generated_at must be a stable UTC timestamp like 2026-08-08T00:00:00Z"
        )
    if not source_dir.is_dir() or not qc_dir.is_dir():
        raise RepairError("source_dir and qc_dir must both exist")
    _assert_disjoint(source_dir, qc_dir, output_dir)
    tasks, source_files = _load_tasks(source_dir)
    qc, qc_files = _load_qc(qc_dir)
    source_lock = _validate_source_lock(source_lock_path, source_files, qc_files)
    _strict_join(tasks, qc)

    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quarantined: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decisions: list[dict[str, Any]] = []
    all_repaired: list[dict[str, Any]] = []
    repair_counts: Counter[str] = Counter()
    quarantine_counts: Counter[str] = Counter()

    for record in tasks:
        source_task = record.task
        task_id = source_task["task_id"]
        classification = qc[task_id].classification
        repaired, repair_codes, repaired_findings = _apply_repairs(
            source_task, classification
        )
        if repaired["task_id"] != task_id or source_task != record.task:
            raise RepairError(f"repair mutated source identity for {task_id!r}")
        _validate_contract(repaired)
        family = _task_family(repaired)
        domain = repaired["domain"]
        quarantine_reasons = _quarantine_reasons(
            repaired, classification, repaired_findings
        )
        disposition = "quarantine" if quarantine_reasons else "candidate"
        if disposition == "candidate":
            candidates[domain].append(repaired)
        else:
            quarantined[domain].append(repaired)
            quarantine_counts.update(quarantine_reasons)
        repair_counts.update(repair_codes)
        all_repaired.append(repaired)
        decisions.append(
            {
                "schema_version": MANIFEST_VERSION,
                "source": {
                    "task_id": task_id,
                    "task_sha256": record.source_task_sha256,
                    "path": _stable_path(record.source_path),
                    "line": record.source_line,
                },
                "domain": domain,
                "family": family,
                "direction_revision": {
                    "direction_id": family,
                    "source_dataset": "scale-v1",
                    "candidate_dataset": "scale-v2",
                    "repair_policy_version": POLICY_VERSION,
                    "repair_codes": repair_codes,
                },
                "qc": {
                    "grade": classification.get("grade"),
                    "labels": classification.get("labels", []),
                    "reason_codes": [
                        reason.get("code")
                        for reason in classification.get("reasons", [])
                        if isinstance(reason, dict)
                    ],
                    "evidence_summary": _evidence_summary(classification),
                    "leakage_summary": _leakage_summary(classification),
                    "evaluation_status": classification.get("evaluation_status"),
                    "evidence_coverage": classification.get("evidence_coverage"),
                    "grade_scope": classification.get("grade_scope"),
                },
                "repair_codes": repair_codes,
                "repaired_qc_findings": sorted(
                    repaired_findings
                    & {
                        reason.get("code")
                        for reason in classification.get("reasons", [])
                        if isinstance(reason, dict)
                    }
                ),
                "disposition": disposition,
                "status": (
                    "repaired_candidate"
                    if disposition == "candidate"
                    else "quarantined"
                ),
                "disposition_reasons": quarantine_reasons
                or [
                    "deterministic_repairs_complete",
                    "post_repair_qc_and_baseline_promotion_pending",
                ],
            }
        )

    _validate_schema(all_repaired, schema_path)
    domains = sorted({record.task["domain"] for record in tasks})
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)
    )
    try:
        quarantine_dir = staging / "quarantine"
        quarantine_dir.mkdir()
        output_files: list[dict[str, Any]] = []
        for domain in domains:
            candidate_path = (
                staging / f"ScienceVision_scale-v2-{domain}_candidate_tasks.jsonl"
            )
            quarantine_path = (
                quarantine_dir
                / f"ScienceVision_scale-v2-{domain}_quarantine_tasks.jsonl"
            )
            _write_jsonl(candidate_path, candidates[domain])
            _write_jsonl(quarantine_path, quarantined[domain])
            output_files.extend(
                [
                    {
                        "path": candidate_path.relative_to(staging).as_posix(),
                        "sha256": _sha256_path(candidate_path),
                        "task_count": len(candidates[domain]),
                        "disposition": "candidate",
                        "domain": domain,
                    },
                    {
                        "path": quarantine_path.relative_to(staging).as_posix(),
                        "sha256": _sha256_path(quarantine_path),
                        "task_count": len(quarantined[domain]),
                        "disposition": "quarantine",
                        "domain": domain,
                    },
                ]
            )
        decisions_path = staging / "scale-v2-decisions.jsonl"
        _write_jsonl(decisions_path, decisions)
        output_files.append(
            {
                "path": decisions_path.relative_to(staging).as_posix(),
                "sha256": _sha256_path(decisions_path),
                "task_count": len(decisions),
                "disposition": "all",
            }
        )
        command = generation_command or (
            "python -m scripts.qa_qc_repair "
            f"--source-dir {source_dir} --qc-dir {qc_dir} --out-dir {output_dir} "
            f"--source-lock {_stable_path(source_lock_path)} --generated-at {generated_at}"
        )
        build = {
            "schema_version": BUILD_VERSION,
            "repair_policy_version": POLICY_VERSION,
            "source_dataset": "scale-v1",
            "output_dataset": "scale-v2",
            "publication_status": "repaired_candidate",
            "promotion_gate": {
                "post_repair_static_qc": "required",
                "baseline_evaluation": "required",
                "formal_release": "not_granted",
            },
            "generated_at": generated_at,
            "generation_command": command,
            "inputs": {
                "task_files": source_files,
                "qc_files": qc_files,
                "task_count": len(tasks),
                "qc_count": len(qc),
                "task_id_join": "strict_1_to_1",
                "source_lock": source_lock,
                "task_schema_path": _stable_path(schema_path),
                "task_schema_sha256": _sha256_path(schema_path),
            },
            "counts": {
                "total": len(tasks),
                "candidate": sum(len(items) for items in candidates.values()),
                "quarantine": sum(len(items) for items in quarantined.values()),
                "candidate_by_domain": {
                    domain: len(candidates[domain]) for domain in domains
                },
                "quarantine_by_domain": {
                    domain: len(quarantined[domain]) for domain in domains
                },
                "repairs": dict(sorted(repair_counts.items())),
                "quarantine_reasons": dict(sorted(quarantine_counts.items())),
            },
            "outputs": output_files,
        }
        build_path = staging / "scale-v2-build.json"
        build_path.write_text(
            json.dumps(build, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        changed = _publish(staging, output_dir)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {**build, "published": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--qc-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--generated-at", default=DEFAULT_GENERATED_AT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    args = parser.parse_args(argv)
    command = " ".join(
        [
            "python -m scripts.qa_qc_repair",
            f"--source-dir {args.source_dir}",
            f"--qc-dir {args.qc_dir}",
            f"--out-dir {args.out_dir}",
            f"--source-lock {_stable_path(args.source_lock)}",
            f"--generated-at {args.generated_at}",
        ]
    )
    if args.schema != DEFAULT_SCHEMA:
        command += f" --schema {args.schema}"
    try:
        result = build_scale_v2(
            args.source_dir,
            args.qc_dir,
            args.out_dir,
            generated_at=args.generated_at,
            schema_path=args.schema,
            source_lock_path=args.source_lock,
            generation_command=command,
        )
    except RepairError as exc:
        print(f"repair failed closed: {exc}", file=sys.stderr)
        return 2
    counts = result["counts"]
    action = "published" if result["published"] else "unchanged"
    print(
        f"{action} {args.out_dir}: total={counts['total']} "
        f"candidate={counts['candidate']} quarantine={counts['quarantine']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
