"""Auditable QC gate for QA contracts, assets, duplicates, shortcuts, and evals.

The analyzer never rewrites source task JSONL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts._qa_qc_report import render_markdown, write_report
from scripts._qa_qc_mechanical import (
    MechanicalTrustError,
    audit_mechanical_contract,
    load_mechanical_trust,
)
from scripts._qa_qc_shortcuts import audit_shortcuts, attach_probe_results, content_reward

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}
GRADE_RANK = {"qualified": 0, "minor": 1, "needs_fix": 2, "unacceptable": 3}
GRADE_DISPLAY_ZH = {
    "qualified": "合格",
    "minor": "轻微问题",
    "needs_fix": "需要修复",
    "unacceptable": "不可接受",
}
UNVERIFIED_GT = {None, "", "seed", "unverified", "needs_expert_annotation"}
HARNESS_TO_BASELINE = {
    "claude-code": "cc",
    "claude-code-mcp": "ccmcp",
    "claude-code-2d": "cc2d",
}
DEFAULT_EVAL_BASELINES = ("cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12")
REFUSAL_RE = re.compile(
    r"usage policy|unable to respond|cannot assist|can't assist|policy refusal",
    re.IGNORECASE,
)
ASSET_BLOCK_RE = re.compile(r"\n\nAsset:\s.*\Z", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"```sci3d.*?```", re.DOTALL | re.IGNORECASE)
ORACLE_CONFIRMED_STATUSES = {
    "computed_exact_match",
    "computed_tolerance_match",
    "computed_mismatch",
}

REQUIRED_TASK_FIELDS = (
    "task_id", "instruction", "domain", "v_class", "asset", "answer_schema", "ground_truth", "scorer",
)

SUGGESTED_ACTIONS = {
    "missing_required_field": "Add the missing task contract field before evaluation.",
    "invalid_answer_schema": "Declare a non-empty, stable list of answer fields.",
    "missing_terminal_answer": "Add terminal_answer and keep its fields aligned with answer_schema.",
    "terminal_answer_mismatch": "Make terminal_answer.kind/fields match the scored answer contract.",
    "missing_scorer_contract": "Define scorer.primary and at least one concrete scorer check.",
    "ground_truth_not_verified": "Obtain expert or automatic verification before benchmark release.",
    "ground_truth_missing": "Fill every declared answer field in the verified ground truth.",
    "ground_truth_shape_mismatch": "Store structured ground truth for a multi-field answer contract.",
    "ground_truth_outside_answer_choices": "Align the ground truth with the choices allowed by the question.",
    "asked_output_not_scored": "Either remove the requested output or add its field, GT, and scorer check.",
    "conditional_answer_contract": "Declare which fields are required for each branch of the question.",
    "unbound_visual_reference": "Bind the referenced mask/annotation/candidate IDs to the task and rendered views.",
    "insufficient_bound_assets": "Bind every pose/structure that the comparison question refers to.",
    "incomplete_asset_binding": "Add path, viewer, and format to every bound asset.",
    "missing_asset_role": "Assign an explicit semantic role to every multi-asset binding.",
    "asset_alias_mismatch": "Make legacy asset alias one exact member of assets[].",
    "missing_candidate_label": "Give every candidate asset a stable display label.",
    "duplicate_candidate_label": "Use unique candidate labels within each task.",
    "incomplete_paired_asset_roles": "Bind a query role and at least one candidate role.",
    "asset_declared_present_but_missing": "Restore the required asset locally or correct the declaration before staging.",
    "multi_asset_materialization_incomplete": "Materialize every bound structure before staging the comparison task.",
    "multi_asset_staging_unsupported": "Stage every bound structure and expose all container paths to the agent.",
    "target_mask_registry_join_missing": "Bind the task to one reviewed registry record before deriving or rendering ground truth.",
    "target_mask_binding_missing": "Carry the registry mask_id through the task, renderer, and scorer contracts.",
    "target_mask_binding_mismatch": "Make the task mask_id match the reviewed registry target label.",
    "target_mask_geometry_uses_union": "Recompute ground truth from the bound target label, not the nonzero mask union.",
    "target_mask_geometry_mismatch": "Recompute and review target-label geometry before promotion.",
    "nifti_preflight_invalid": "Repair or replace the NIfTI image/mask before attempting a render.",
    "molecular_file_preflight_invalid": "Repair or replace the molecular structure file before staging.",
    "duplicate_task_id": "Keep one canonical sample and assign globally unique task IDs.",
    "duplicate_qa_sample": "Remove or intentionally version the duplicate asset-question pair.",
    "template_reuse": "Review task-family balance and vary wording only when it preserves the same contract.",
    "ranking_margin_below_tolerance": "Increase candidate separation or use a scorer that handles ties.",
    "shortcut_control_unenforced": "Attach runtime enforcement evidence or treat the shortcut as exposed.",
    "missing_eval_coverage": "Resume the missing baseline trials before interpreting aggregate scores.",
    "cross_baseline_refusal": "Quarantine the task, repair wording/evidence, then rerun every baseline.",
    "cross_baseline_failure": "Review the task and run artifacts; low scores alone do not prove a bad question.",
    "repeated_baseline_refusal": "Review policy-sensitive wording before spending more eval budget.",
}

CONTRACT_CODES = {
    "missing_required_field",
    "invalid_answer_schema",
    "missing_terminal_answer",
    "terminal_answer_mismatch",
    "ground_truth_shape_mismatch",
    "asked_output_not_scored",
    "conditional_answer_contract",
    "ground_truth_outside_answer_choices",
}
SCORER_CODES = {
    "missing_scorer_contract",
    "asked_output_not_scored",
    "ranking_margin_below_tolerance",
}
GROUND_TRUTH_CODES = {
    "ground_truth_not_verified",
    "ground_truth_missing",
    "ground_truth_shape_mismatch",
    "ground_truth_outside_answer_choices",
    "target_mask_geometry_uses_union",
    "target_mask_geometry_mismatch",
}
GROUNDING_CODES = {
    "unbound_visual_reference",
    "insufficient_bound_assets",
    "incomplete_asset_binding",
    "missing_asset_role",
    "asset_alias_mismatch",
    "missing_candidate_label",
    "duplicate_candidate_label",
    "incomplete_paired_asset_roles",
    "asset_declared_present_but_missing",
    "multi_asset_materialization_incomplete",
    "multi_asset_staging_unsupported",
    "target_mask_registry_join_missing",
    "target_mask_binding_missing",
    "target_mask_binding_mismatch",
    "target_mask_geometry_uses_union",
    "target_mask_geometry_mismatch",
    "nifti_preflight_invalid",
    "molecular_file_preflight_invalid",
}
LEAKAGE_LABELS = {
    "text_knowledge_shortcut": "leakage.knowledge",
    "asset_identifier_shortcut": "leakage.asset",
    "metadata_shortcut": "leakage.asset",
    "package_compute_shortcut": "leakage.tool",
}
EVAL_CODES = {
    "missing_eval_coverage",
    "cross_baseline_refusal",
    "cross_baseline_failure",
    "repeated_baseline_refusal",
}
UNACCEPTABLE_CODES = {
    "duplicate_task_id",
    "duplicate_qa_sample",
    "unbound_visual_reference",
    "insufficient_bound_assets",
    "incomplete_asset_binding",
    "missing_asset_role",
    "asset_alias_mismatch",
    "missing_candidate_label",
    "duplicate_candidate_label",
    "incomplete_paired_asset_roles",
    "asset_declared_present_but_missing",
    "multi_asset_materialization_incomplete",
    "multi_asset_staging_unsupported",
    "target_mask_registry_join_missing",
    "target_mask_binding_mismatch",
    "target_mask_geometry_uses_union",
    "target_mask_geometry_mismatch",
    "nifti_preflight_invalid",
    "molecular_file_preflight_invalid",
}
NEEDS_FIX_CODES = {
    *CONTRACT_CODES,
    *SCORER_CODES,
    *GROUND_TRUTH_CODES,
    "ranking_margin_below_tolerance",
    "shortcut_control_missing",
    "shortcut_control_unenforced",
    "target_mask_binding_missing",
}


def _finding(
    code: str,
    severity: str,
    message: str,
    *,
    evidence: Any = None,
    action: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
        "suggested_action": action or SUGGESTED_ACTIONS.get(code, "Review manually."),
    }
    if evidence is not None:
        result["evidence"] = evidence
    return result


def normalize_instruction(instruction: Any) -> str:
    """Canonical question text, excluding eval-only asset plumbing."""
    text = instruction if isinstance(instruction, str) else ""
    text = FENCE_RE.sub(" ", text)
    text = ASSET_BLOCK_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def _asset_objects(task: dict[str, Any]) -> list[dict[str, Any]]:
    assets = task.get("assets")
    if isinstance(assets, list) and assets:
        return [asset for asset in assets if isinstance(asset, dict)]
    asset = task.get("asset")
    return [asset] if isinstance(asset, dict) and asset else []


def _uses_role_aware_candidates(task: dict[str, Any]) -> bool:
    return task.get("asset_contract") == "role_aware_candidates_v1"


def _asset_key(asset: dict[str, Any]) -> str:
    for key in ("asset_id", "qid", "repo_path", "path", "name"):
        value = asset.get(key)
        if value not in (None, ""):
            return str(value)
    return "<unbound>"


def asset_signature(task: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({_asset_key(asset) for asset in _asset_objects(task)}))


def _has_nonempty_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and child not in (None, "", [], {}):
                return True
            if _has_nonempty_key(child, keys):
                return True
    elif isinstance(value, list):
        return any(_has_nonempty_key(item, keys) for item in value)
    return False


def _field_covers(concept: str, fields: list[str]) -> bool:
    lowered = [field.casefold() for field in fields]
    joined = " ".join(lowered)
    if concept == "atom_pair":
        return "atom_pair" in joined or sum("atom" in field for field in lowered) >= 2
    tokens = {
        "distance": ("distance", "length"),
        "slice_count": ("slice",),
        "dihedral": ("dihedral", "torsion"),
        "rationale": ("reason", "evidence", "support", "interaction", "concern", "reject", "basis"),
        "residue": ("residue", "protein_atom"),
        "space_group": ("spacegroup", "space_group"),
        "formula_units": ("formula_unit", "z"),
        "plane_normal": ("plane_normal", "normal"),
        "extent": ("extent", "bbox", "bounding"),
        "laterality": ("laterality", "side"),
    }.get(concept, (concept,))
    return any(token == field or token in field for token in tokens for field in lowered)


def _task_family(task: dict[str, Any], task_id: str) -> str:
    knowledge = task.get("knowledge")
    provenance = task.get("provenance")
    plan_item = provenance.get("plan_item") if isinstance(provenance, dict) else None
    candidates = (
        knowledge.get("direction_id") if isinstance(knowledge, dict) else None,
        plan_item.get("direction_id") if isinstance(plan_item, dict) else None,
        task.get("direction_id"),
        task.get("task_family"),
        task.get("family"),
    )
    for candidate in candidates:
        if candidate not in (None, ""):
            return str(candidate)
    return re.sub(r"[-_]\d+$", "", task_id) or task_id


def asked_output_concepts(instruction: str) -> list[str]:
    """Return explicit outputs requested in prose, not latent scientific needs."""
    patterns = (
        ("atom_pair", r"\batom pair\b"),
        ("distance", r"\bdistance\b|\bangstroms?\b|\bå\b"),
        ("slice_count", r"(?:how many|number of)[^.?!]{0,40}\bslices?\b|\bslices? (?:it |the \w+ )?spans?\b"),
        ("dihedral", r"\bdihedral\b|\btorsion angle\b"),
        ("rationale", r"\bcite\b|\bjustify\b|\bwhy\b|\bsupporting interactions?\b|\bjudg(?:e)?ment basis\b|判断依据|说明.{0,8}依据"),
        ("residue", r"\b(?:participating |which )?residue\b"),
        ("space_group", r"\bspace group\b"),
        ("formula_units", r"\bformula units?\b|\bunit cell \(z\)"),
        ("plane_normal", r"\bplane normal\b"),
        ("extent", r"\bbounding extent\b|\bbounding box\b"),
        ("laterality", r"\bleft or right\b|\bwhich side\b"),
    )
    text = instruction.casefold()
    return [concept for concept, pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def _answer_choices(
    answer_schema: dict[str, Any], instruction: str, fields: list[str]
) -> tuple[list[str], str | None]:
    if len(fields) != 1 or answer_schema.get("kind") not in {"binary", "categorical"}:
        return [], None
    for key in ("choices", "enum", "allowed_values"):
        value = answer_schema.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return [item.casefold() for item in value], f"answer_schema.{key}"
        if isinstance(value, dict) and isinstance(value.get(fields[0]), list):
            items = value[fields[0]]
            if all(isinstance(item, str) for item in items):
                return [item.casefold() for item in items], f"answer_schema.{key}.{fields[0]}"
    properties = answer_schema.get("properties")
    if isinstance(properties, dict):
        field_schema = properties.get(fields[0])
        if isinstance(field_schema, dict) and isinstance(field_schema.get("enum"), list):
            items = field_schema["enum"]
            if all(isinstance(item, str) for item in items):
                return [item.casefold() for item in items], f"answer_schema.properties.{fields[0]}.enum"
    patterns = (
        r"\banswer\s+([a-z][a-z0-9_-]*)\s+or\s+([a-z][a-z0-9_-]*)\b",
        r"\(([a-z][a-z0-9_-]*)\s+or\s+([a-z][a-z0-9_-]*)\)",
        r"\b([a-z][a-z0-9_-]*)\s+or\s+([a-z][a-z0-9_-]*)\b",
    )
    for pattern in patterns:
        matches = re.findall(pattern, instruction, re.IGNORECASE)
        if matches:
            return [value.casefold() for value in matches[-1]], "instruction"
    return [], None


def _ranking_margin(task: dict[str, Any]) -> dict[str, Any] | None:
    grounding = task.get("asset_grounding")
    verified = grounding.get("verified_entities") if isinstance(grounding, dict) else None
    distances = verified.get("contact_distances_A") if isinstance(verified, dict) else None
    scorer = task.get("scorer") if isinstance(task.get("scorer"), dict) else {}
    tolerance = scorer.get("tolerance") if isinstance(scorer.get("tolerance"), dict) else {}
    distance_tolerance = tolerance.get("distance_A")
    if not isinstance(distances, list) or len(distances) < 2:
        return None
    if not all(isinstance(value, (int, float)) for value in distances):
        return None
    if not isinstance(distance_tolerance, (int, float)):
        return None
    ordered = sorted(float(value) for value in distances)
    margin = ordered[1] - ordered[0]
    return {
        "values_A": [float(value) for value in distances],
        "ranking_margin_A": round(margin, 6),
        "scorer_tolerance_A": float(distance_tolerance),
        "below_tolerance": margin < float(distance_tolerance),
    }


def load_tasks(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            findings.append(_finding("task_file_unreadable", "error", f"Cannot read {path}: {exc}"))
            continue
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                task = json.loads(line)
            except json.JSONDecodeError as exc:
                findings.append(
                    _finding("invalid_task_json", "error", f"{path}:{line_no}: {exc.msg}")
                )
                continue
            if not isinstance(task, dict):
                findings.append(
                    _finding("invalid_task_json", "error", f"{path}:{line_no}: task must be an object")
                )
                continue
            entries.append({"task": task, "source": str(path), "line": line_no})
    return entries, findings


def load_oracle_receipts(
    paths: list[Path],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    receipts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    findings: list[dict[str, Any]] = []
    for path in paths:
        try:
            raw = path.read_bytes()
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(
                _finding(
                    "oracle_receipt_unreadable",
                    "error",
                    f"Cannot read oracle receipt {path}: {exc}",
                )
            )
            continue
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            findings.append(
                _finding(
                    "oracle_receipt_invalid",
                    "error",
                    f"Oracle receipt {path} has no results array.",
                )
            )
            continue
        source_sha256 = hashlib.sha256(raw).hexdigest()
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("task_id") in (None, ""):
                continue
            status = str(row.get("status") or "unknown")
            nested = row.get("receipt") if isinstance(row.get("receipt"), dict) else {}
            route_status = nested.get("route_status")
            unique_answer_confirmed = nested.get("unique_answer_confirmed")
            if route_status is not None:
                feasibility = (
                    "confirmed"
                    if route_status == "confirmed" and unique_answer_confirmed is True
                    else "candidate"
                )
            else:
                feasibility = "confirmed" if status in ORACLE_CONFIRMED_STATUSES else "candidate"
            route = str(row.get("route") or "")
            if not route and nested.get("route_type"):
                route = {
                    "metadata": "metadata_shortcut",
                    "package_compute": "package_compute_shortcut",
                }.get(str(nested["route_type"]), "")
            if route not in {"metadata_shortcut", "package_compute_shortcut"}:
                route = (
                    "metadata_shortcut"
                    if row.get("family") == "chem_crystallography"
                    else "package_compute_shortcut"
                )
            inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
            compact_inputs = {
                key: inputs[key]
                for key in (
                    "source_url",
                    "cache_path",
                    "sha256",
                    "availability",
                    "selection_rule",
                    "metadata_tags",
                )
                if key in inputs
            }
            receipt = {
                "source": str(path),
                "source_sha256": source_sha256,
                "source_schema_version": payload.get("schema_version"),
                "result_index": index,
                "status": status,
                "route": route,
                "feasibility": feasibility,
                "route_status": route_status,
                "unique_answer_confirmed": unique_answer_confirmed,
                "required_capabilities": nested.get("required_capabilities"),
                "library": row.get("library") or nested.get("package"),
                "inputs": compact_inputs,
                "computed_result": (
                    row.get("computed_result") or nested.get("computed")
                    if feasibility == "confirmed"
                    else None
                ),
                "comparison": row.get("comparison") or nested.get("comparison"),
                "error": row.get("error"),
            }
            receipts[str(row["task_id"])].append(receipt)
    return receipts, findings


def attach_oracle_receipts(
    task_results: list[dict[str, Any]], receipts: dict[str, list[dict[str, Any]]]
) -> None:
    for result in task_results:
        task_receipts = receipts.get(result["task_id"], [])
        result["oracle_receipts"] = task_receipts
        for receipt in task_receipts:
            code = receipt["route"]
            finding = next((item for item in result["findings"] if item["code"] == code), None)
            if finding is None:
                label = "metadata" if code == "metadata_shortcut" else "package-compute"
                finding = _finding(
                    code,
                    "warning",
                    f"An oracle receipt reports a {label} route for this task.",
                    evidence={"status": "static_candidate"},
                )
                result["findings"].append(finding)
            evidence = finding.setdefault("evidence", {})
            evidence.setdefault("oracle_receipts", []).append(receipt)


def analyze_task(
    entry: dict[str, Any],
    *,
    mechanical_trust: dict[str, Any] | None = None,
    mechanical_strict: bool = False,
) -> dict[str, Any]:
    task = entry["task"]
    task_id = task.get("task_id") or f"<missing>@{entry['source']}:{entry['line']}"
    findings: list[dict[str, Any]] = []

    for field in REQUIRED_TASK_FIELDS:
        if task.get(field) in (None, "", [], {}):
            findings.append(
                _finding("missing_required_field", "error", f"Required field {field!r} is missing or empty.")
            )

    answer_schema = task.get("answer_schema") if isinstance(task.get("answer_schema"), dict) else {}
    raw_fields = answer_schema.get("fields")
    fields = [field for field in raw_fields if isinstance(field, str) and field] if isinstance(raw_fields, list) else []
    if not answer_schema.get("kind") or not fields:
        findings.append(
            _finding("invalid_answer_schema", "error", "answer_schema requires kind and non-empty fields.")
        )

    terminal = task.get("terminal_answer")
    if not isinstance(terminal, dict) or not terminal.get("kind") or not terminal.get("fields"):
        findings.append(
            _finding("missing_terminal_answer", "error", "No complete terminal_answer contract is declared.")
        )
    elif terminal.get("fields") != fields:
        findings.append(
            _finding(
                "terminal_answer_mismatch",
                "error",
                "terminal_answer.fields differs from answer_schema.fields.",
                evidence={"answer_schema": fields, "terminal_answer": terminal.get("fields")},
            )
        )

    scorer = task.get("scorer") if isinstance(task.get("scorer"), dict) else {}
    if not scorer.get("primary") or not scorer.get("checks"):
        findings.append(
            _finding("missing_scorer_contract", "error", "scorer.primary or scorer.checks is empty.")
        )

    ground_truth = task.get("ground_truth") if isinstance(task.get("ground_truth"), dict) else {}
    gt_status = ground_truth.get("status")
    gt_value = ground_truth.get("value")
    if gt_status in UNVERIFIED_GT:
        findings.append(
            _finding(
                "ground_truth_not_verified",
                "warning",
                f"ground_truth.status={gt_status!r} is not release-ready.",
            )
        )
    elif gt_value is None:
        findings.append(_finding("ground_truth_missing", "error", "Verified ground truth has no value."))

    if gt_value is not None and fields:
        if len(fields) > 1 and not isinstance(gt_value, dict):
            findings.append(
                _finding(
                    "ground_truth_shape_mismatch",
                    "error",
                    "A multi-field answer contract has scalar/list ground truth.",
                    evidence={"fields": fields, "ground_truth_type": type(gt_value).__name__},
                )
            )
        elif isinstance(gt_value, dict):
            missing_gt = [field for field in fields if field not in gt_value]
            if missing_gt:
                findings.append(
                    _finding(
                        "ground_truth_missing",
                        "error",
                        f"Ground truth is missing declared fields: {missing_gt}.",
                        evidence={"missing_fields": missing_gt},
                    )
                )

    instruction = normalize_instruction(task.get("instruction"))
    answer_choices, answer_choices_source = _answer_choices(answer_schema, instruction, fields)
    if answer_choices and gt_value is not None and len(fields) == 1:
        candidate_value = gt_value.get(fields[0]) if isinstance(gt_value, dict) else gt_value
        if isinstance(candidate_value, str) and candidate_value.casefold() not in answer_choices:
            findings.append(
                _finding(
                    "ground_truth_outside_answer_choices",
                    "error",
                    "The ground truth is outside the answer choices allowed by the question.",
                    evidence={
                        "field": fields[0],
                        "ground_truth": candidate_value,
                        "allowed_choices": answer_choices,
                        "choices_source": answer_choices_source,
                    },
                )
            )

    ranking_margin = _ranking_margin(task)
    if ranking_margin and ranking_margin["below_tolerance"]:
        findings.append(
            _finding(
                "ranking_margin_below_tolerance",
                "warning",
                "The ranking margin is smaller than the scorer's declared distance tolerance.",
                evidence=ranking_margin,
            )
        )

    concepts = asked_output_concepts(instruction)
    for concept in concepts:
        if fields and not _field_covers(concept, fields):
            findings.append(
                _finding(
                    "asked_output_not_scored",
                    "error",
                    f"The question requests {concept!r}, but no answer field captures it.",
                    evidence={"concept": concept, "fields": fields},
                )
            )

    conditional = bool(re.search(r"\bif (?:so|yes|present|applicable)\b|如果", instruction, re.IGNORECASE))
    conditional_declared = any(key in answer_schema for key in ("conditional_fields", "required_when"))
    if conditional and len(fields) > 1 and not conditional_declared:
        findings.append(
            _finding(
                "conditional_answer_contract",
                "warning",
                "The question has conditional subquestions but the answer schema does not declare branch requirements.",
                evidence={"fields": fields},
            )
        )

    assets = _asset_objects(task)
    if _uses_role_aware_candidates(task):
        for index, asset in enumerate(assets):
            missing_binding = [key for key in ("path", "viewer", "format") if not asset.get(key)]
            if missing_binding:
                findings.append(
                    _finding(
                        "incomplete_asset_binding",
                        "error",
                        f"Asset {index} is missing binding fields: {missing_binding}.",
                    )
                )
        missing_roles = [index for index, asset in enumerate(assets) if not asset.get("role")]
        if missing_roles:
            findings.append(
                _finding(
                    "missing_asset_role",
                    "error",
                    f"Multi-asset bindings lack roles at indices {missing_roles}.",
                )
            )
        alias = task.get("asset") if isinstance(task.get("asset"), dict) else {}
        alias_key = alias.get("asset_id") or alias.get("path")
        asset_keys = {asset.get("asset_id") or asset.get("path") for asset in assets}
        if not alias_key or alias_key not in asset_keys:
            findings.append(
                _finding(
                    "asset_alias_mismatch",
                    "error",
                    "Legacy asset is not an exact member of assets[].",
                )
            )
        candidates = [
            asset for asset in assets if str(asset.get("role", "")).startswith("candidate_")
        ]
        labels = [asset.get("candidate_label") for asset in candidates]
        if candidates and any(not label for label in labels):
            findings.append(
                _finding(
                    "missing_candidate_label",
                    "error",
                    "At least one candidate asset has no candidate_label.",
                )
            )
        if labels and len(labels) != len(set(labels)):
            findings.append(
                _finding(
                    "duplicate_candidate_label",
                    "error",
                    "Candidate labels are not unique.",
                )
            )
        roles = {asset.get("role") for asset in assets}
        if not (roles & {"query_drawing", "query_model"}) or not (
            roles & {"candidate_drawing", "candidate_model"}
        ):
            findings.append(
                _finding(
                    "incomplete_paired_asset_roles",
                    "error",
                    "Mechanical CAD tasks need a query role and at least one candidate role.",
                )
            )
    multi_asset_claim = bool(
        re.search(
            r"(?:\ball\b|\bboth\b|\b2\b|\btwo\b|\bmultiple\b)[^.?!]{0,60}\b(?:poses|structures|complexes)\b"
            r"|\bamong\b[^.?!]{0,40}\b(?:poses|structures|complexes)\b"
            r"|\bsimultaneously\b|\bacross them\b",
            instruction,
            re.IGNORECASE,
        )
    )
    if multi_asset_claim and len(assets) < 2:
        findings.append(
            _finding(
                "insufficient_bound_assets",
                "error",
                f"The question refers to multiple poses/structures, but only {len(assets)} asset is bound.",
                evidence={"assets": list(asset_signature(task))},
            )
        )

    annotation_claim = bool(
        re.search(r"\bannotated\b[^.?!]{0,50}\b(?:candidate|vector|region|point)s?\b", instruction)
    )
    annotation_keys = {"annotation", "annotations", "annotation_path", "candidate_vectors", "vector_annotations", "variants"}
    if annotation_claim and not _has_nonempty_key(task, annotation_keys):
        findings.append(
            _finding(
                "unbound_visual_reference",
                "error",
                "The question refers to annotations/candidates that are not bound to the task.",
            )
        )

    highlight_claim = bool(re.search(r"\bhighlighted\b", instruction))
    highlight_keys = {"mask_path", "label_path", "segmentation_path", "annotation_path", "overlay_path"}
    if highlight_claim and not _has_nonempty_key(task, highlight_keys):
        findings.append(
            _finding(
                "unbound_visual_reference",
                "error",
                "The question refers to a highlighted target but no mask/label/overlay is bound.",
            )
        )

    for mechanical in audit_mechanical_contract(
        task, trust=mechanical_trust, strict=mechanical_strict
    ):
        findings.append(
            _finding(
                mechanical["code"],
                mechanical["severity"],
                mechanical["message"],
                evidence=mechanical.get("evidence"),
                action="Repair the mechanical input closure and rerun the registered deterministic solver.",
            )
        )

    shortcut_audit = audit_shortcuts(task, instruction)
    findings.extend(shortcut_audit.pop("findings"))
    return {
        "task_id": str(task_id),
        "family": _task_family(task, str(task_id)),
        "source": entry["source"],
        "line": entry["line"],
        "domain": task.get("domain"),
        "v_class": task.get("v_class"),
        "instruction": normalize_instruction(task.get("instruction")),
        "asset_signature": list(asset_signature(task)),
        "contract": {
            "question_mark_count": instruction.count("?"),
            "requested_output_count": len(concepts),
            "asked_output_concepts": concepts,
            "answer_kind": answer_schema.get("kind"),
            "answer_fields": fields,
            "answer_field_count": len(fields),
            "answer_choices": answer_choices,
            "answer_choices_source": answer_choices_source,
            "ground_truth_status": gt_status,
            "ranking_margin": ranking_margin,
        },
        "shortcuts": shortcut_audit,
        "findings": findings,
        "eval": {},
    }


def analyze_duplicates(
    entries: list[dict[str, Any]], task_results: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: list[dict[str, Any]] = []
    suite_findings: list[dict[str, Any]] = []
    result_by_location = {(r["source"], r["line"]): r for r in task_results}

    by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_identity: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
    by_template: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        task = entry["task"]
        task_id = str(task.get("task_id") or "<missing>")
        normalized = normalize_instruction(task.get("instruction"))
        signature = asset_signature(task)
        item = {
            "task_id": task_id,
            "source": entry["source"],
            "line": entry["line"],
            "assets": list(signature),
        }
        by_id[task_id].append(item)
        by_identity[(normalized, signature)].append(item)
        by_template[normalized].append(item)

    def add_task_finding(items: list[dict[str, Any]], finding: dict[str, Any]) -> None:
        for item in items:
            result = result_by_location.get((item["source"], item["line"]))
            if result is not None:
                result["findings"].append(dict(finding))

    for task_id, items in sorted(by_id.items()):
        if task_id != "<missing>" and len(items) > 1:
            finding = _finding(
                "duplicate_task_id", "error", f"task_id {task_id!r} occurs {len(items)} times.", evidence=items
            )
            add_task_finding(items, finding)
            suite_findings.append(finding)
            groups.append({"classification": "duplicate_task_id", "blocking": True, "items": items})

    for (instruction, signature), items in sorted(by_identity.items()):
        unique_ids = {item["task_id"] for item in items}
        if len(items) > 1 and len(unique_ids) > 1:
            finding = _finding(
                "duplicate_qa_sample",
                "error",
                f"The same normalized question and asset binding occurs under {len(unique_ids)} task IDs.",
                evidence={"task_ids": sorted(unique_ids), "assets": list(signature)},
            )
            add_task_finding(items, finding)
            suite_findings.append(finding)
            groups.append(
                {
                    "classification": "duplicate_qa_sample",
                    "blocking": True,
                    "instruction": instruction,
                    "items": items,
                }
            )

    template_groups = []
    for instruction, items in sorted(by_template.items()):
        signatures = {tuple(item["assets"]) for item in items}
        if instruction and len(items) > 1 and len(signatures) > 1:
            group = {
                "classification": "template_reuse",
                "blocking": False,
                "instruction": instruction,
                "count": len(items),
                "items": items,
            }
            groups.append(group)
            template_groups.append(group)
            add_task_finding(
                items,
                _finding(
                    "template_reuse",
                    "info",
                    f"This normalized instruction is reused across {len(items)} assets.",
                    evidence={"count": len(items), "instruction": instruction},
                ),
            )
    if template_groups:
        covered = len({item["task_id"] for group in template_groups for item in group["items"]})
        top = sorted(template_groups, key=lambda group: group["count"], reverse=True)[:10]
        suite_findings.append(
            _finding(
                "template_reuse",
                "warning",
                f"{len(template_groups)} repeated instruction templates cover {covered} tasks on different assets.",
                evidence=[
                    {"count": group["count"], "instruction": group["instruction"], "task_ids": [i["task_id"] for i in group["items"]]}
                    for group in top
                ],
            )
        )
    return groups, suite_findings


def _baseline_from_path(path: Path, root: Path) -> str:
    for part in path.relative_to(root).parts:
        if part.startswith("job-"):
            return part.removeprefix("job-")
    match = re.search(r"jobs-(?:ev2-)?(.+?)(?:-rerun)?$", root.name)
    return match.group(1) if match else "unknown"


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_content(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            if key in value:
                return _text_content(value[key])
    return ""


def _trajectory_answer(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        for key in ("final_answer", "answer"):
            text = _text_content(data.get(key))
            if text:
                return text
        messages = data.get("messages") or data.get("trajectory")
        if isinstance(messages, list):
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") in {"assistant", "agent"}:
                    text = _text_content(message.get("content") or message.get("message"))
                    if text:
                        return text
    return ""


def _reward(data: dict[str, Any]) -> float | None:
    rewards = data.get("rewards")
    if not isinstance(rewards, dict):
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
    value = rewards.get("reward") if isinstance(rewards, dict) else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def load_distilled_runs(paths: list[Path]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for root in paths:
        candidates = [root] if root.is_file() else sorted(root.rglob("*.json"))
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or not data.get("task_id") or not any(
                key in data for key in ("harness", "answer", "rewards", "status", "error")
            ):
                continue
            harness = str(data.get("harness") or data.get("baseline") or "unknown")
            attempts.append(
                {
                    "task_id": str(data["task_id"]),
                    "baseline": HARNESS_TO_BASELINE.get(harness, harness),
                    "answer": _text_content(data.get("answer")),
                    "error": _text_content(data.get("error")),
                    "reward": _reward(data),
                    "content_reward": content_reward(data),
                    "exception_type": "",
                    "source": str(path),
                }
            )
    return attempts


def load_harbor_jobs(roots: list[Path]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for root in roots:
        for path in sorted(root.rglob("result.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            task_name = str(data.get("task_name") or data.get("task_id") or "")
            task_id = task_name.split("/", 1)[-1]
            if not task_id:
                continue
            exc = data.get("exception_info") if isinstance(data.get("exception_info"), dict) else {}
            answer = _text_content(data.get("answer") or data.get("agent_result"))
            if not answer:
                answer = _trajectory_answer(path.parent / "agent" / "trajectory.json")
            attempts.append(
                {
                    "task_id": task_id,
                    "baseline": _baseline_from_path(path, root),
                    "answer": answer,
                    "error": _text_content(data.get("error")) or _text_content(exc.get("exception_message")),
                    "reward": _reward(data),
                    "content_reward": content_reward(data),
                    "exception_type": str(exc.get("exception_type") or ""),
                    "source": str(path),
                }
            )
    return attempts


def _attempt_signals(attempt: dict[str, Any], low_score: float) -> list[str]:
    text = f"{attempt.get('answer', '')}\n{attempt.get('error', '')}"
    signals: list[str] = []
    if REFUSAL_RE.search(text):
        signals.append("refusal")
    if not str(attempt.get("answer") or "").strip():
        signals.append("no_answer")
    if attempt.get("exception_type"):
        signals.append("exception")
    reward = attempt.get("reward")
    if reward is not None and reward <= low_score:
        signals.append("low_score")
    return signals


def attach_eval_results(
    task_results: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    expected_baselines: list[str],
    low_score: float,
    shortcut_success: float = 0.8,
) -> list[dict[str, Any]]:
    suite_findings: list[dict[str, Any]] = []
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        attempt = dict(attempt)
        attempt["signals"] = _attempt_signals(attempt, low_score)
        by_task[attempt["task_id"]].append(attempt)

    if not expected_baselines:
        expected_baselines = sorted({attempt["baseline"] for attempt in attempts})

    expected_task_ids = {result["task_id"] for result in task_results}
    coverage: dict[str, dict[str, int]] = {}
    for baseline in expected_baselines:
        observed = {task_id for task_id in expected_task_ids if any(a["baseline"] == baseline for a in by_task[task_id])}
        coverage[baseline] = {"observed": len(observed), "expected": len(expected_task_ids)}
        if len(observed) < len(expected_task_ids):
            suite_findings.append(
                _finding(
                    "missing_eval_coverage",
                    "warning",
                    f"Baseline {baseline!r} has results for {len(observed)}/{len(expected_task_ids)} tasks.",
                    evidence={"baseline": baseline, **coverage[baseline]},
                )
            )

    for result in task_results:
        task_attempts = by_task.get(result["task_id"], [])
        baseline_summary: dict[str, Any] = {}
        for baseline in sorted({attempt["baseline"] for attempt in task_attempts}):
            selected = [attempt for attempt in task_attempts if attempt["baseline"] == baseline]
            effective = max(
                enumerate(selected),
                key=lambda pair: (pair[1]["reward"] is not None, bool(pair[1]["answer"].strip()),
                                  not pair[1]["exception_type"], pair[0]),
            )[1]
            baseline_summary[baseline] = {
                "attempt_count": len(selected),
                "signals": effective["signals"],
                "history_signals": sorted({signal for attempt in selected for signal in attempt["signals"]}),
                "rewards": [attempt["reward"] for attempt in selected if attempt["reward"] is not None],
                "effective_reward": effective["reward"],
                "effective_content_reward": effective.get("content_reward"),
                "effective_source": effective["source"],
                "sources": [attempt["source"] for attempt in selected],
            }
        result["eval"] = {
            "expected_baselines": expected_baselines,
            "observed_baselines": sorted(baseline_summary),
            "baselines": baseline_summary,
        }
        attach_probe_results(result, baseline_summary, shortcut_success)

        refusals = {baseline for baseline, cell in baseline_summary.items() if "refusal" in cell["signals"]}
        failures = {
            baseline
            for baseline, cell in baseline_summary.items()
            if set(cell["signals"]) & {"refusal", "no_answer", "exception", "low_score"}
        }
        expected = set(expected_baselines)
        if len(expected) >= 2 and expected.issubset(refusals):
            result["findings"].append(
                _finding(
                    "cross_baseline_refusal",
                    "error",
                    f"All {len(expected)} expected baselines refused the task.",
                    evidence={"baselines": sorted(refusals)},
                )
            )
        elif len(refusals) >= 2:
            result["findings"].append(
                _finding(
                    "repeated_baseline_refusal",
                    "warning",
                    f"The task was refused by {len(refusals)} baselines.",
                    evidence={"baselines": sorted(refusals)},
                )
            )
        if len(expected) >= 2 and expected.issubset(failures) and not expected.issubset(refusals):
            result["findings"].append(
                _finding(
                    "cross_baseline_failure",
                    "warning",
                    f"All {len(expected)} expected baselines produced a failure signal.",
                    evidence={"baselines": sorted(failures), "low_score_threshold": low_score},
                )
            )

    return suite_findings


def _max_severity(findings: list[dict[str, Any]]) -> str:
    return max((finding["severity"] for finding in findings), key=SEVERITY_RANK.get, default="info")


def _finding_labels(finding: dict[str, Any]) -> list[str]:
    code = str(finding.get("code") or "")
    labels: set[str] = set()
    if code in CONTRACT_CODES:
        labels.add("contract")
    if code in SCORER_CODES:
        labels.add("scorer")
    if code in GROUND_TRUTH_CODES:
        labels.add("ground_truth")
    if code in GROUNDING_CODES:
        labels.add("grounding")
    if code in LEAKAGE_LABELS:
        labels.add(LEAKAGE_LABELS[code])
    if code in {"shortcut_control_missing", "shortcut_control_unenforced"}:
        labels.add("contract")
        evidence = finding.get("evidence")
        routes = []
        if isinstance(evidence, dict):
            routes = evidence.get("uncontrolled_routes") or evidence.get("routes") or []
        for route in routes:
            if not isinstance(route, dict):
                continue
            if route.get("kind") == "metadata":
                labels.add("leakage.asset")
            elif route.get("kind") == "package_compute":
                labels.add("leakage.tool")
    if code in {"duplicate_task_id", "duplicate_qa_sample"}:
        labels.add("uniqueness")
    if code == "template_reuse":
        labels.add("diversity")
    if code in EVAL_CODES:
        labels.add("eval")
    if code.startswith("mechanical_"):
        labels.update({"contract", "grounding"})
    return sorted(labels or {"quality"})


def _baseline_access_profiles(baselines: list[str]) -> dict[str, dict[str, str]]:
    profiles: dict[str, dict[str, str]] = {}
    for baseline in baselines:
        lowered = baseline.casefold()
        if lowered in {"text-only", "cc-text", "text"}:
            input_mode = "instruction_only"
            raw_asset = "unavailable"
            rendered_views = "unavailable"
        elif lowered.startswith("cc2d"):
            input_mode = "rendered_only"
            raw_asset = "unavailable"
            rendered_views = "exposed"
        elif lowered in {"cc", "ccmcp", "raw-file", "package-compute"}:
            input_mode = "raw_asset"
            raw_asset = "exposed"
            rendered_views = "unknown"
        else:
            input_mode = "unknown"
            raw_asset = "unknown"
            rendered_views = "unknown"
        profiles[baseline] = {
            "input_mode": input_mode,
            "instruction": "exposed" if input_mode != "unknown" else "unknown",
            "raw_asset": raw_asset,
            "rendered_views": rendered_views,
            "package_availability": "unknown",
        }
    return profiles


def _route_requirements(label: str) -> list[str]:
    if label == "leakage.knowledge":
        return ["instruction"]
    if label == "leakage.asset":
        return ["raw_asset"]
    return ["raw_asset", "package"]


def _affected_baselines(
    label: str, profiles: dict[str, dict[str, str]]
) -> list[str]:
    affected = []
    for baseline, profile in profiles.items():
        if label == "leakage.knowledge" and profile["instruction"] == "exposed":
            affected.append(baseline)
        elif label == "leakage.asset" and profile["raw_asset"] == "exposed":
            affected.append(baseline)
        elif (
            label == "leakage.tool"
            and profile["raw_asset"] == "exposed"
            and profile["package_availability"] != "unavailable"
        ):
            affected.append(baseline)
    return sorted(affected)


def _leakage_record(
    finding: dict[str, Any], label: str, profiles: dict[str, dict[str, str]]
) -> dict[str, Any]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    probes = evidence.get("probes", []) if isinstance(evidence.get("probes"), list) else []
    oracle_receipts = (
        evidence.get("oracle_receipts", [])
        if isinstance(evidence.get("oracle_receipts"), list)
        else []
    )
    observations = [
        {
            "baseline": str(probe["baseline"]),
            "metric": probe.get("score_kind"),
            "score": probe.get("score"),
            "success_threshold": probe.get("success_threshold"),
        }
        for probe in probes
        if isinstance(probe, dict) and probe.get("baseline") not in (None, "")
    ]
    confirmed_receipts = [
        receipt
        for receipt in oracle_receipts
        if isinstance(receipt, dict) and receipt.get("feasibility") == "confirmed"
    ]
    probe_confirms_route = (
        evidence.get("status") == "empirically_confirmed"
        and label == "leakage.knowledge"
    )
    feasibility_receipts = list(oracle_receipts)
    if probe_confirms_route:
        feasibility_receipts.extend(
            {"kind": "isolated_probe", **observation} for observation in observations
        )
    route_confirmed = bool(confirmed_receipts) or probe_confirms_route
    static_evidence = {
        key: value
        for key, value in evidence.items()
        if key not in {"status", "probes", "oracle_receipts"}
    }
    identities = evidence.get("identities") if isinstance(evidence.get("identities"), list) else []
    identifier_sources = sorted(
        {
            str(identity.get("identifier_source"))
            for identity in identities
            if isinstance(identity, dict) and identity.get("identifier_source")
        }
    )
    oracle_package_availability = (
        "available"
        if label == "leakage.tool" and any(receipt.get("library") for receipt in oracle_receipts)
        else "unknown"
    )
    return {
        "label": label,
        "finding_code": finding["code"],
        "route_requirements": _route_requirements(label),
        "route_feasibility": {
            "status": "confirmed" if route_confirmed else "candidate",
            "receipts": feasibility_receipts,
            "static_evidence": static_evidence,
            "executable_receipts": oracle_receipts,
        },
        "identifier_sources": identifier_sources,
        "baseline_access": profiles,
        "package_availability": {
            "oracle_environment": oracle_package_availability,
            "by_baseline": {
                baseline: profile["package_availability"]
                for baseline, profile in profiles.items()
            },
        },
        "affected_baselines": _affected_baselines(label, profiles),
        "answer_exploitation": {
            "status": "observed" if observations else "untested",
            "observations": observations,
            "causal_reward_delta": None,
        },
    }


def _finding_grade(finding: dict[str, Any]) -> str:
    code = str(finding.get("code") or "")
    if code == "missing_eval_coverage":
        return "qualified"
    if code == "template_reuse":
        return "minor"
    if code in UNACCEPTABLE_CODES:
        return "unacceptable"
    if code in NEEDS_FIX_CODES or finding.get("severity") == "error":
        return "needs_fix"
    if finding.get("severity") == "warning":
        return "minor"
    return "qualified"


def _classify_task(result: dict[str, Any], baselines: list[str]) -> dict[str, Any]:
    findings = result["findings"]
    grades = [_finding_grade(finding) for finding in findings]
    profiles = _baseline_access_profiles(baselines)
    labels = sorted({label for finding in findings for label in _finding_labels(finding)})
    reasons = [
        {
            "code": finding["code"],
            "severity": finding["severity"],
            "labels": _finding_labels(finding),
            "message": finding["message"],
        }
        for finding in findings
    ]
    if not reasons:
        reasons.append(
            {
                "code": "no_qc_findings",
                "severity": "info",
                "labels": [],
                "message": "No benchmark-relevant QC defect or shortcut candidate was detected.",
            }
        )
    evidence = [
        {
            "finding_code": finding["code"],
            "labels": _finding_labels(finding),
            "detail": finding["evidence"],
        }
        for finding in findings
        if "evidence" in finding
    ]
    actions = list(
        dict.fromkeys(
            str(finding["suggested_action"])
            for finding in findings
            if finding.get("suggested_action")
        )
    )
    leakage = [
        _leakage_record(finding, label, profiles)
        for finding in findings
        for label in _finding_labels(finding)
        if label.startswith("leakage.")
    ]
    for record in leakage:
        if record["route_feasibility"]["status"] == "confirmed" and record["affected_baselines"]:
            grades.append("unacceptable")
        elif record["affected_baselines"]:
            grades.append("needs_fix")
        else:
            grades.append("minor")
    grade = max(grades, key=GRADE_RANK.get, default="qualified")
    expected = list(result.get("eval", {}).get("expected_baselines") or baselines)
    observed = list(result.get("eval", {}).get("observed_baselines") or [])
    missing = sorted(set(expected) - set(observed))
    if expected and not missing:
        coverage_status = "complete"
        grade_scope = "static+eval"
    elif observed:
        coverage_status = "partial"
        grade_scope = "static+partial_eval"
    else:
        coverage_status = "missing"
        grade_scope = "static"
    qualification_status = grade
    quality_gate_status = (
        "requires_fix"
        if grade in {"unacceptable", "needs_fix"}
        else "review"
        if grade == "minor"
        else "ready"
    )
    if grade == "qualified" and coverage_status != "complete":
        qualification_status = "static-qualified"
    if quality_gate_status == "requires_fix":
        release_status = "requires_fix"
    elif quality_gate_status == "review":
        release_status = "review"
    elif coverage_status == "complete":
        release_status = "ready"
    else:
        release_status = "pending_eval"
    return {
        "schema_version": "1.1",
        "grade": grade,
        "grade_display_zh": GRADE_DISPLAY_ZH[grade],
        "grade_scope": grade_scope,
        "qualification_status": qualification_status,
        "quality_gate_status": quality_gate_status,
        "evaluation_status": coverage_status,
        "release_status": release_status,
        "evidence_coverage": {
            "status": coverage_status,
            "expected_baselines": expected,
            "observed_baselines": observed,
            "missing_baselines": missing,
        },
        "labels": labels,
        "reasons": reasons,
        "evidence": evidence,
        "recommended_actions": actions,
        "leakage": leakage,
        "legacy_decision": result["decision"],
    }


def _finalize_task(result: dict[str, Any], baselines: list[str] | None = None) -> None:
    severity = _max_severity(result["findings"])
    result["decision"] = "block" if severity == "error" else "review" if severity == "warning" else "pass"
    result["classification"] = _classify_task(result, baselines or list(DEFAULT_EVAL_BASELINES))


def build_report(
    task_paths: list[Path],
    *,
    distilled_runs: list[Path] | None = None,
    jobs_roots: list[Path] | None = None,
    oracle_receipts: list[Path] | None = None,
    expected_baselines: list[str] | None = None,
    low_score: float = 0.2,
    shortcut_success: float = 0.8,
    suite_name: str = "ScienceVision QA QC",
    mechanical_case_manifest: Path | None = None,
    mechanical_case_manifest_sha256: str | None = None,
    mechanical_strict: bool = False,
) -> dict[str, Any]:
    if mechanical_strict and (
        mechanical_case_manifest is None or mechanical_case_manifest_sha256 is None
    ):
        raise MechanicalTrustError(
            "strict MechanicalCADEngineering QC requires both case manifest and SHA256"
        )
    if (mechanical_case_manifest is None) != (mechanical_case_manifest_sha256 is None):
        raise MechanicalTrustError(
            "mechanical case manifest path and SHA256 must be provided together"
        )
    mechanical_trust = (
        load_mechanical_trust(
            mechanical_case_manifest, mechanical_case_manifest_sha256
        )
        if mechanical_case_manifest is not None
        and mechanical_case_manifest_sha256 is not None
        else None
    )
    entries, load_findings = load_tasks(task_paths)
    task_results = [
        analyze_task(
            entry,
            mechanical_trust=mechanical_trust,
            mechanical_strict=mechanical_strict,
        )
        for entry in entries
    ]
    duplicate_groups, duplicate_findings = analyze_duplicates(entries, task_results)
    oracle_by_task, oracle_findings = load_oracle_receipts(oracle_receipts or [])
    attach_oracle_receipts(task_results, oracle_by_task)
    attempts = load_distilled_runs(distilled_runs or []) + load_harbor_jobs(jobs_roots or [])
    report_baselines = list(expected_baselines or DEFAULT_EVAL_BASELINES)
    eval_findings = attach_eval_results(
        task_results, attempts, report_baselines, low_score, shortcut_success
    )
    for result in task_results:
        _finalize_task(result, report_baselines)

    suite_findings = load_findings + duplicate_findings + oracle_findings + eval_findings
    all_findings = suite_findings + [finding for result in task_results for finding in result["findings"]]
    severity_counts = Counter(finding["severity"] for finding in all_findings)
    suite_code_counts = Counter(finding["code"] for finding in suite_findings)
    task_code_counts = Counter(
        finding["code"] for result in task_results for finding in result["findings"]
    )
    code_counts = suite_code_counts + task_code_counts
    decision_counts = Counter(result["decision"] for result in task_results)
    grade_counts = Counter(result["classification"]["grade"] for result in task_results)
    label_counts = Counter(
        label for result in task_results for label in result["classification"]["labels"]
    )
    family_cells: dict[str, dict[str, Any]] = {}
    for result in task_results:
        family = result["family"]
        cell = family_cells.setdefault(
            family,
            {"task_count": 0, "grades": Counter(), "labels": Counter(), "decisions": Counter()},
        )
        cell["task_count"] += 1
        cell["grades"][result["classification"]["grade"]] += 1
        cell["decisions"][result["decision"]] += 1
        cell["labels"].update(result["classification"]["labels"])
    family_summary = {
        family: {
            "task_count": cell["task_count"],
            "grades": dict(sorted(cell["grades"].items())),
            "labels": dict(sorted(cell["labels"].items())),
            "decisions": dict(sorted(cell["decisions"].items())),
        }
        for family, cell in sorted(family_cells.items())
    }
    coverage = {
        baseline: {
            "observed": len(
                {
                    result["task_id"]
                    for result in task_results
                    if baseline in result.get("eval", {}).get("observed_baselines", [])
                }
            ),
            "expected": len(task_results),
        }
        for baseline in report_baselines
    }
    return {
        "schema_version": "1.4",
        "suite": suite_name,
        "inputs": {
            "tasks": [str(path) for path in task_paths],
            "distilled_runs": [str(path) for path in distilled_runs or []],
            "jobs_roots": [str(path) for path in jobs_roots or []],
            "oracle_receipts": [str(path) for path in oracle_receipts or []],
            "expected_baselines": report_baselines,
            "low_score_threshold": low_score,
            "shortcut_success_threshold": shortcut_success,
            "mechanical_strict": mechanical_strict,
            "mechanical_case_manifest": (
                str(mechanical_trust["manifest_path"])
                if mechanical_trust is not None else None
            ),
            "mechanical_case_manifest_sha256": (
                mechanical_trust["manifest_sha256"]
                if mechanical_trust is not None else None
            ),
        },
        "summary": {
            "task_count": len(task_results),
            "eval_attempt_count": len(attempts),
            "decisions": dict(sorted(decision_counts.items())),
            "grades": dict(sorted(grade_counts.items())),
            "labels": dict(sorted(label_counts.items())),
            "by_family": family_summary,
            "findings_by_severity": dict(sorted(severity_counts.items())),
            "findings_by_code": {
                "task": dict(sorted(task_code_counts.items())),
                "suite": dict(sorted(suite_code_counts.items())),
                "all": dict(sorted(code_counts.items())),
            },
            "eval_coverage": coverage,
        },
        "suite_findings": suite_findings,
        "duplicate_groups": duplicate_groups,
        "tasks": task_results,
    }


def _parse_baselines(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(part for part in value.split(",") if part)
    return list(dict.fromkeys(result))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, nargs="+", required=True, help="one or more task JSONL files")
    parser.add_argument("--distilled-runs", type=Path, action="append", default=[], help="distilled run JSON file/dir")
    parser.add_argument("--jobs-root", type=Path, action="append", default=[], help="Harbor jobs root")
    parser.add_argument(
        "--oracle-receipt",
        type=Path,
        action="append",
        default=[],
        help="optional executable-oracle receipt JSON; repeat as needed",
    )
    parser.add_argument("--expected-baselines", nargs="*", default=[], help="space- or comma-separated labels")
    parser.add_argument("--low-score-threshold", type=float, default=0.2)
    parser.add_argument("--shortcut-success-threshold", type=float, default=0.8)
    parser.add_argument("--suite-name", default="ScienceVision QA QC")
    parser.add_argument("--mechanical-case-manifest", type=Path)
    parser.add_argument("--mechanical-case-manifest-sha256")
    parser.add_argument("--mechanical-strict", action="store_true")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--fail-on", choices=("error", "warning", "never"), default="error")
    parser.add_argument(
        "--fail-on-code",
        action="append",
        default=[],
        help="finding code that must fail regardless of severity; repeat as needed",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(
            args.tasks,
            distilled_runs=args.distilled_runs,
            jobs_roots=args.jobs_root,
            oracle_receipts=args.oracle_receipt,
            expected_baselines=_parse_baselines(args.expected_baselines),
            low_score=args.low_score_threshold,
            shortcut_success=args.shortcut_success_threshold,
            suite_name=args.suite_name,
            mechanical_case_manifest=args.mechanical_case_manifest,
            mechanical_case_manifest_sha256=args.mechanical_case_manifest_sha256,
            mechanical_strict=args.mechanical_strict,
        )
    except MechanicalTrustError as exc:
        print(f"mechanical trust error: {exc}", file=sys.stderr)
        return 2
    write_report(report, args.out_dir)
    summary = report["summary"]
    print(render_markdown(report))
    print(f"wrote {args.out_dir / 'report.json'}", file=sys.stderr)
    print(f"wrote {args.out_dir / 'report.md'}", file=sys.stderr)
    print(f"wrote {args.out_dir / 'review_queue.jsonl'}", file=sys.stderr)
    print(f"wrote {args.out_dir / 'task_classifications.jsonl'}", file=sys.stderr)

    if args.fail_on == "never":
        severity_failed = False
    else:
        threshold = SEVERITY_RANK[args.fail_on]
        severities = summary["findings_by_severity"]
        severity_failed = any(
            SEVERITY_RANK[severity] >= threshold and count
            for severity, count in severities.items()
        )
    code_failed = any(
        summary["findings_by_code"]["all"].get(code, 0) for code in args.fail_on_code
    )
    return int(severity_failed or code_failed)


if __name__ == "__main__":
    raise SystemExit(main())
