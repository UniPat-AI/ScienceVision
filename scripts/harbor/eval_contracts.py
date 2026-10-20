"""Normalize legacy QA-Gen evaluation contracts without weakening v2.1 tasks."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from sci3d_bench.scorers.deterministic_answer import (
    GENERIC_RUBRIC_VERSION,
    GENERIC_SCORER_NAME,
    SCORER_ALIASES,
)


_SUPPORTED_DETERMINISTIC_SCORERS = {
    *SCORER_ALIASES,
    "exact_match",
    "tolerance_match",
    "expert_rubric",
    "extent_voxel_tolerance_v1",
    "mechanical_structured_v1",
}


def _gt_value(record: dict) -> Any:
    ground_truth = record.get("ground_truth")
    return ground_truth.get("value") if isinstance(ground_truth, dict) else ground_truth


def is_rich_v21_contract(record: dict) -> bool:
    """Return whether a record declares a versioned structured rubric."""
    scorer = record.get("scorer") if isinstance(record.get("scorer"), dict) else {}
    scorer_config = (
        record.get("scorer_config")
        if isinstance(record.get("scorer_config"), dict)
        else {}
    )
    primary = str(scorer.get("primary", ""))
    version = scorer.get(
        "rubric_version", scorer.get("contract_version", scorer.get("version"))
    )
    return bool(
        primary in {"deterministic_structured_v2_1", "deterministic_answer_v2_1"}
        or primary == GENERIC_SCORER_NAME
        or isinstance(scorer.get("rubric"), list)
        or version in {"sciencevision.deterministic_structured.v2.1", "2.1", "v2.1"}
        or scorer_config.get("schema_version") == GENERIC_RUBRIC_VERSION
    )


def normalize_eval_contract(record: dict) -> dict:
    """Return a deep copy with legacy-only contract repairs applied.

    A v2.1 rubric is authoritative. Its instruction, fields, ground truth, and
    scorer are never collapsed into the historical single-field templates.
    """
    rec = copy.deepcopy(record)
    schema = rec.setdefault("answer_schema", {"kind": "freeform"})
    raw_fields = schema.get("fields")
    fields = list(raw_fields) if isinstance(raw_fields, list) else []
    ground_truth = rec.get("ground_truth")
    gt_dict = ground_truth if isinstance(ground_truth, dict) else None
    value = _gt_value(rec)
    rich = is_rich_v21_contract(rec)

    if not rich and fields == ["laterality"]:
        schema["enum"] = ["left", "right", "midline"]
        rec["instruction"] = (
            "Working in the displayed CT volume, where is the highlighted lesion "
            "relative to the body's left-right axis? Answer left, right, or midline."
        )

    if not rich and fields == ["has_severe_clash"]:
        rec["instruction"] = (
            "Does the displayed complex contain a severe non-bonded clash between "
            "the ligand and the receptor? Answer yes or no."
        )

    if not rich and fields == ["conformer_class"] and isinstance(value, str):
        if gt_dict is not None:
            gt_dict["value"] = {"conformer_class": value}
        rec["instruction"] = (
            "For the two largest substituents across the central rotatable bond in "
            "the displayed conformer, is their arrangement anti, gauche, or eclipsed?"
        )

    if not rich and fields and isinstance(value, dict):
        schema["fields"] = [field for field in fields if field in value]

    scorer = rec.get("scorer") if isinstance(rec.get("scorer"), dict) else {}
    primary = str(scorer.get("primary", ""))
    status = gt_dict.get("status") if gt_dict is not None else None
    unavailable = status in {
        "needs_expert_annotation", "needs_regeneration", "unverified", "seed"
    }
    eligible = (
        value is not None
        and not unavailable
        and primary in _SUPPORTED_DETERMINISTIC_SCORERS
    )
    rec["evaluation_eligible"] = bool(eligible)
    if not eligible:
        rec["evaluation_ineligible_reason"] = (
            "unsupported_deterministic_scorer"
            if primary not in _SUPPORTED_DETERMINISTIC_SCORERS
            else "ground_truth_unavailable"
        )
    else:
        rec.pop("evaluation_ineligible_reason", None)
    return rec


def audit_eval_contract(record: dict) -> list[str]:
    """Return stable errors for a normalized evaluation contract."""
    errors: list[str] = []
    schema = record.get("answer_schema") if isinstance(record.get("answer_schema"), dict) else {}
    fields = schema.get("fields")
    fields = list(fields) if isinstance(fields, (list, tuple)) else []
    value = _gt_value(record)
    if not fields:
        errors.append("answer_schema_fields_empty")
    if record.get("evaluation_eligible"):
        if isinstance(value, dict) and set(fields) != set(value):
            errors.append("answer_schema_ground_truth_fields_mismatch")
        elif not isinstance(value, dict) and len(fields) != 1:
            errors.append("scalar_ground_truth_requires_one_answer_field")
    elif not record.get("evaluation_ineligible_reason"):
        errors.append("ineligible_reason_missing")

    scorer = record.get("scorer") if isinstance(record.get("scorer"), dict) else {}
    primary = str(scorer.get("primary", ""))
    if primary not in _SUPPORTED_DETERMINISTIC_SCORERS:
        errors.append("unsupported_deterministic_scorer")
    if primary == GENERIC_SCORER_NAME:
        config = (
            record.get("scorer_config")
            if isinstance(record.get("scorer_config"), dict)
            else {}
        )
        if config.get("schema_version") != GENERIC_RUBRIC_VERSION:
            errors.append("versioned_rubric_version_invalid")
        if config.get("strict_json_object") is not True:
            errors.append("strict_json_object_required")
        if not isinstance(config.get("fields"), list) or not config["fields"]:
            errors.append("versioned_rubric_missing")
    elif is_rich_v21_contract(record):
        if not isinstance(scorer.get("rubric"), list) or not scorer["rubric"]:
            errors.append("versioned_rubric_missing")
        if scorer.get("rubric_version", scorer.get("contract_version", scorer.get("version"))) not in {
            "sciencevision.deterministic_structured.v2.1",
            "2.1",
            "v2.1",
        }:
            errors.append("versioned_rubric_version_invalid")
    return errors


def audit_jsonl(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    findings = []
    eligible = 0
    for raw in rows:
        record = normalize_eval_contract(raw)
        errors = audit_eval_contract(record)
        eligible += int(bool(record.get("evaluation_eligible")))
        if errors:
            findings.append({"task_id": record.get("task_id"), "errors": errors})
    return {
        "records": len(rows),
        "eligible": eligible,
        "ineligible": len(rows) - eligible,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", type=Path, required=True)
    parser.add_argument("--require-all-eligible", action="store_true")
    args = parser.parse_args(argv)
    report = audit_jsonl(args.jsonl)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["findings"] or (args.require_all_eligible and report["ineligible"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
