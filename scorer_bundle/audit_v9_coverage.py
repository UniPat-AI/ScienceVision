#!/usr/bin/env python3
"""Produce a source-hashed, fail-closed coverage report for a v9 task index.

The report classifies task contracts; it never tries to turn prose or malformed
records into an automatic score. A non-v9/unknown source is reported as such,
so a dev-subset or historical index cannot be mistaken for v9 evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


REPORT_SCHEMA = "sciencevision.scorer_coverage.v1"
FORMAL_SCORERS = frozenset(
    {
        "deterministic_structured_v2_1",
        "structured_deterministic_rubric_v1",
        "deterministic_answer_v2_1",
        "answer_score",
        "exact_match",
        "tolerance_match",
        "expert_rubric",
        "extent_voxel_tolerance_v1",
    }
)
PROSE_FORMS = frozenset({"freeform", "prose", "text", "natural_language"})
STRUCTURED_FORMS = frozenset(
    {
        "structured",
        "structured_matching_schema",
        "mechanical_structured",
        "categorical",
        "json_object",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [_as_mapping(row) for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        raise ValueError("index payload must be an object or list")
    for key in ("rows", "tasks", "items", "questions", "data"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [_as_mapping(row) for row in candidate if isinstance(row, Mapping)]
    # dev-subset exports three explicitly selected tiers. Do not recurse into
    # pool_excluded: those records have no answer and are not its coverage set.
    tiered: list[dict[str, Any]] = []
    for key in ("core", "coverage", "text_path"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            tiered.extend(_as_mapping(row) for row in candidate if isinstance(row, Mapping))
    if tiered:
        return tiered
    if "task_id" in payload or "metadata" in payload:
        return [dict(payload)]
    raise ValueError("no recognized task row collection")


def load_index(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    if path.is_dir():
        task_files = sorted(path.rglob("task.json"))
        source_files: dict[str, str] = {}
        rows: list[dict[str, Any]] = []
        input_errors: list[dict[str, str]] = []
        for task_file in task_files:
            relative = task_file.relative_to(path).as_posix()
            raw = task_file.read_bytes()
            source_files[relative] = _sha256(raw)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                rows.append({"_load_error": "invalid_task_json", "folder": relative})
                input_errors.append({"path": relative, "reason": type(exc).__name__})
                continue
            if not isinstance(payload, Mapping):
                rows.append({"_load_error": "task_json_not_object", "folder": relative})
                input_errors.append({"path": relative, "reason": "task_json_not_object"})
                continue
            row = dict(payload)
            row["_source_relative_path"] = relative
            rows.append(row)
        if not task_files:
            rows = [{"_load_error": "no_task_json_files", "folder": str(path)}]
            input_errors.append({"path": str(path), "reason": "no_task_json_files"})
        return (
            rows,
            {
                "source_kind": "task_directory",
                "task_file_count": len(task_files),
                "input_errors": input_errors,
                "source_version": path.name,
            },
            _sha256(_canonical_json(source_files)),
        )
    raw = path.read_bytes()
    source_sha256 = _sha256(raw)
    if path.suffix.casefold() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL line {line_number}") from exc
            if not isinstance(value, Mapping):
                raise ValueError(f"JSONL line {line_number} is not an object")
            rows.append(dict(value))
        return rows, {}, source_sha256
    payload = json.loads(raw.decode("utf-8"))
    metadata = _as_mapping(payload)
    return _rows_from_payload(payload), metadata, source_sha256


def _field(row: Mapping[str, Any], name: str, default: Any = None) -> Any:
    if name in row:
        return row.get(name, default)
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata.get(name, default)
    return default


def _answer_form(row: Mapping[str, Any]) -> str:
    explicit = _field(row, "answer_form")
    if explicit is not None:
        return str(explicit).strip().casefold()
    schema = _field(row, "answer_schema")
    if isinstance(schema, Mapping):
        return str(schema.get("kind") or "").strip().casefold()
    return ""


def _primary_scorer(row: Mapping[str, Any]) -> str:
    scorer = _field(row, "scorer")
    return str(scorer.get("primary") or "") if isinstance(scorer, Mapping) else ""


def _is_prose(row: Mapping[str, Any], answer_form: str, primary: str) -> bool:
    if answer_form in PROSE_FORMS or "judge" in primary.casefold():
        return True
    free_text = _field(row, "free_text_fields")
    return isinstance(free_text, list) and bool(free_text)


def _is_structured(answer_form: str) -> bool:
    return answer_form in STRUCTURED_FORMS or "structured" in answer_form


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _mask_evidence(row: Mapping[str, Any]) -> dict[str, bool]:
    """Identify mask evidence without inferring that it is public input."""
    mask_referenced = False
    segmentation_sidecar = False
    explicitly_public = False
    for key, value in _walk(row):
        normalized = key.casefold()
        if "mask" in normalized:
            mask_referenced = True
        if normalized in {"public_input_roles", "allowed_input_roles"}:
            roles = value if isinstance(value, list) else []
            if any(str(role).casefold() in {"mask", "segmentation_mask"} for role in roles):
                explicitly_public = True
        if normalized == "public_inputs" and isinstance(value, Mapping):
            if any("mask" in str(item).casefold() for item in value):
                explicitly_public = True
    assets = _field(row, "assets")
    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, Mapping):
                continue
            role = str(asset.get("role") or "").casefold()
            name = str(asset.get("name") or asset.get("path") or "").casefold()
            if "mask" in role or "mask" in name:
                mask_referenced = True
            if role in {"segmentation_sidecar", "segmentation-mask-sidecar"}:
                segmentation_sidecar = True
    return {
        "mask_referenced": mask_referenced,
        "segmentation_sidecar_referenced": segmentation_sidecar,
        "mask_or_segmentation_sidecar_referenced": mask_referenced or segmentation_sidecar,
        "mask_explicitly_public": explicitly_public,
    }


def classify_row(row: Mapping[str, Any]) -> dict[str, Any]:
    task_id = str(_field(row, "task_id") or _field(row, "id") or "")
    domain = str(_field(row, "domain") or "unclassified")
    answer_form = _answer_form(row)
    primary = _primary_scorer(row)
    evaluation_ready = _field(row, "evaluation_ready") is True
    evaluation_eligible = _field(row, "evaluation_eligible") is True
    free_text = _field(row, "free_text_fields")

    malformed_reasons: list[str] = []
    if not task_id:
        malformed_reasons.append("missing_task_id")
    if not answer_form:
        malformed_reasons.append("missing_answer_form_or_schema")
    if malformed_reasons:
        category = "malformed_blocked"
    elif _is_prose(row, answer_form, primary):
        category = "prose_blocked"
    elif evaluation_ready:
        category = "evaluation_ready"
    elif _is_structured(answer_form):
        category = "structured_blocked"
    else:
        category = "malformed_blocked"
        malformed_reasons.append("unrecognized_answer_form")

    formal_reasons: list[str] = []
    if category != "evaluation_ready":
        formal_reasons.append(f"coverage_category:{category}")
    if not evaluation_eligible:
        formal_reasons.append("missing_explicit_evaluation_eligible")
    if primary not in FORMAL_SCORERS:
        formal_reasons.append("unsupported_or_missing_formal_scorer")
    if _field(row, "score_is_trustworthy") is False:
        formal_reasons.append("score_not_trustworthy")
    mask_evidence = _mask_evidence(row)
    task_type = str(_field(row, "task_type") or "")
    return {
        "task_id": task_id or None,
        "domain": domain,
        "task_type": task_type or None,
        "answer_form": answer_form or None,
        "primary_scorer": primary or None,
        "evaluation_ready": evaluation_ready,
        "evaluation_eligible": evaluation_eligible,
        "coverage_category": category,
        "formal_contract_status": "eligible" if not formal_reasons else "blocked",
        "formal_block_reasons": formal_reasons,
        "malformed_reasons": malformed_reasons,
        "free_text_fields": free_text if isinstance(free_text, list) else [],
        **mask_evidence,
    }


def _source_version(payload: Mapping[str, Any], supplied: str | None) -> str | None:
    if supplied:
        return supplied
    bundle = payload.get("bundle")
    if isinstance(bundle, Mapping) and bundle.get("path_name"):
        return str(bundle["path_name"])
    for key in ("source_version", "dataset_version", "version", "bundle_version"):
        if payload.get(key):
            return str(payload[key])
    return None


def _asset_role_mask_task_ids(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        assets = _field(row, "assets")
        if not isinstance(assets, list):
            continue
        if any(
            isinstance(asset, Mapping)
            and str(asset.get("role") or "").casefold() == "mask"
            for asset in assets
        ):
            task_id = str(_field(row, "task_id") or _field(row, "id") or "")
            if task_id:
                ids.append(task_id)
    return sorted(set(ids))


def _readme_claim_lines(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    lines = raw.decode("utf-8").splitlines()
    patterns = ("**v", "Domain totals:", "`mask` 28", "role: mask")
    selected = [
        {"line": index, "text": line}
        for index, line in enumerate(lines, 1)
        if any(pattern in line for pattern in patterns)
    ]
    return {"path": str(path), "sha256": _sha256(raw), "matching_lines": selected}


def historical_claim_evidence(
    historical_rows: list[dict[str, Any]],
    *,
    source_path: Path,
    source_sha256: str,
    source_metadata: Mapping[str, Any],
    source_version: str | None,
    readme: Path | None,
) -> dict[str, Any]:
    medical_rows = [
        row for row in historical_rows if str(_field(row, "domain") or "").casefold() == "medical"
    ]
    geometry_rows = [
        row for row in medical_rows
        if str(_field(row, "sub_domain") or "") == "med_imaging_geometry"
    ]
    mask_ids = _asset_role_mask_task_ids(historical_rows)
    evidence: dict[str, Any] = {
        "status": "verified_historical_source",
        "source": {
            "path": str(source_path),
            "sha256": source_sha256,
            "declared_version": source_version,
            "source_kind": source_metadata.get("source_kind", "index"),
        },
        "medical_task_count": len(medical_rows),
        "med_imaging_geometry_task_count": len(geometry_rows),
        "mask_asset_role_task_count": len(mask_ids),
        "mask_asset_role_task_ids": mask_ids,
        "conclusion": (
            "'med63' is the historical bundle's medical-domain total; '28 mask' "
            "is its asset role=mask task count (also its med_imaging_geometry "
            "sub-domain count), not a v9 coverage or public-input assertion."
        ),
    }
    if readme is not None:
        evidence["readme_evidence"] = _readme_claim_lines(readme)
    return evidence


def build_report(
    rows: list[dict[str, Any]],
    *,
    source_path: Path,
    source_sha256: str,
    source_metadata: Mapping[str, Any],
    source_version: str | None,
    expected_total: int | None,
) -> dict[str, Any]:
    records = [classify_row(row) for row in rows]
    categories = Counter(record["coverage_category"] for record in records)
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        domains[record["domain"]][record["coverage_category"]] += 1
    medical = [record for record in records if record["domain"].casefold() == "medical"]
    med_ct_geometry = [record for record in records if record["task_type"] == "med_ct_geometry"]
    masks = [record for record in records if record["mask_referenced"]]
    segmentation_sidecars = [
        record for record in records if record["segmentation_sidecar_referenced"]
    ]
    mask_or_sidecar = [
        record for record in records if record["mask_or_segmentation_sidecar_referenced"]
    ]
    public_masks = [record for record in records if record["mask_explicitly_public"]]

    is_v9 = bool(source_version and re.search(r"(?:^|[-_])v9(?:$|[-_])", source_version, re.I))
    return {
        "schema_version": REPORT_SCHEMA,
        "source": {
            "path": str(source_path),
            "sha256": source_sha256,
            "declared_version": source_version,
            "v9_verified_source": is_v9,
            "coverage_status": "verified_v9" if is_v9 else "blocked_non_v9_or_unknown_source",
            "declared_total": source_metadata.get("bundle", {}).get("rows")
            if isinstance(source_metadata.get("bundle"), Mapping)
            else None,
        },
        "summary": {
            "total_rows": len(records),
            "expected_total": expected_total,
            "expected_total_matches": expected_total is None or len(records) == expected_total,
            "coverage_categories": dict(sorted(categories.items())),
            "declared_evaluation_ready": sum(record["evaluation_ready"] for record in records),
            "formal_contract_eligible": sum(
                record["formal_contract_status"] == "eligible" for record in records
            ),
            "formal_contract_blocked": sum(
                record["formal_contract_status"] == "blocked" for record in records
            ),
            "domains": {
                domain: dict(sorted(counts.items()))
                for domain, counts in sorted(domains.items())
            },
        },
        "historical_med63_28_mask_claim": {
            "claim": "Historical notes mention 'med63' and '28 tasks with mask', but do not define the denominator or public/private mask semantics.",
            "status": "computed_from_verified_v9_source" if is_v9 else "unverified_non_v9_or_unknown_source",
            "observed_medical_task_count": len(medical),
            "observed_med_ct_geometry_count": len(med_ct_geometry),
            "observed_mask_referenced_count": len(masks),
            "observed_segmentation_sidecar_count": len(segmentation_sidecars),
            "observed_mask_or_segmentation_sidecar_count": len(mask_or_sidecar),
            "observed_explicitly_public_mask_count": len(public_masks),
            "med_ct_geometry_task_ids": [record["task_id"] for record in med_ct_geometry],
            "mask_referenced_task_ids": [record["task_id"] for record in masks],
            "segmentation_sidecar_task_ids": [
                record["task_id"] for record in segmentation_sidecars
            ],
            "mask_or_segmentation_sidecar_task_ids": [
                record["task_id"] for record in mask_or_sidecar
            ],
            "explicitly_public_mask_task_ids": [record["task_id"] for record in public_masks],
            "rule": "A mask is public only with explicit public_input_roles/allowed_input_roles/public_inputs evidence; a filename, mask_id, or generic segmentation sidecar is not public evidence.",
            "historical_source_evidence": {
                "status": "not_supplied",
                "conclusion": "No historical source was supplied; v9 observations above must not be used to infer the historical med63/28 statement.",
            },
        },
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True, help="v9 index JSON or JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-version", help="override/declare source version")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument(
        "--historical-index", type=Path,
        help="optional historical index used to resolve the med63/28 statement",
    )
    parser.add_argument("--historical-source-version")
    parser.add_argument("--historical-readme", type=Path)
    args = parser.parse_args(argv)
    try:
        rows, source_metadata, source_sha256 = load_index(args.index)
        report = build_report(
            rows,
            source_path=args.index,
            source_sha256=source_sha256,
            source_metadata=source_metadata,
            source_version=_source_version(source_metadata, args.source_version),
            expected_total=args.expected_total,
        )
        if source_metadata.get("input_errors"):
            report["source"]["coverage_status"] = "blocked_input_schema_errors"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": REPORT_SCHEMA,
            "source": {
                "path": str(args.index),
                "coverage_status": "blocked_input_schema_error",
            },
            "summary": None,
            "input_error": {"type": type(exc).__name__, "message": str(exc)},
        }
    if args.historical_index is not None and report.get("summary") is not None:
        try:
            historical_rows, historical_metadata, historical_sha256 = load_index(
                args.historical_index
            )
            report["historical_med63_28_mask_claim"]["historical_source_evidence"] = (
                historical_claim_evidence(
                    historical_rows,
                    source_path=args.historical_index,
                    source_sha256=historical_sha256,
                    source_metadata=historical_metadata,
                    source_version=_source_version(
                        historical_metadata, args.historical_source_version
                    ),
                    readme=args.historical_readme,
                )
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report["historical_med63_28_mask_claim"]["historical_source_evidence"] = {
                "status": "blocked_historical_input_error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical_json(report) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
