"""One mixed v1/v2 manager results export contract for every interface."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .contract import (
    BUNDLE_RESULT_SCHEMA_VERSION,
    MANAGER_RESULTS_EXCHANGE_VERSION,
    MANAGER_RESULTS_EXPORT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    ContractError,
)

RESULT_EXPORT_FIELDS = (
    "schema_version",
    "batch_id",
    "package_id",
    "bundle_id",
    "expert_id",
    "expert_name",
    "assignment_id",
    "task_id",
    "task_revision_sha256",
    "attempt",
    "validation_status",
    "validation_feedback",
    "answer",
    "reasoning",
    "trajectory_status",
    "trajectory_step_count",
    "evidence_count",
    "imported_at",
)
SUPPORTED_RESULT_SCHEMAS = (
    RESULT_SCHEMA_VERSION,
    BUNDLE_RESULT_SCHEMA_VERSION,
)


def csv_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" \t\r\n")
    return "'" + value if candidate.startswith(("=", "+", "-", "@")) else value


def validate_export_rows(rows: list[dict[str, Any]]) -> None:
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != set(RESULT_EXPORT_FIELDS):
            raise ContractError(f"manager result row {index + 1} fields are invalid")
        schema_version = row.get("schema_version")
        if schema_version == RESULT_SCHEMA_VERSION:
            if not row.get("package_id") or row.get("bundle_id") is not None:
                raise ContractError(
                    f"manager result row {index + 1} has an invalid v1 container binding"
                )
        elif schema_version == BUNDLE_RESULT_SCHEMA_VERSION:
            if not row.get("bundle_id") or row.get("package_id") is not None:
                raise ContractError(
                    f"manager result row {index + 1} has an invalid v2 container binding"
                )
        else:
            raise ContractError(
                f"manager result row {index + 1} has an unsupported schema_version"
            )


def manager_results_export(
    rows: list[dict[str, Any]], *, exported_at: str
) -> dict[str, Any]:
    validate_export_rows(rows)
    return {
        "schema_version": MANAGER_RESULTS_EXPORT_SCHEMA_VERSION,
        "contract_version": MANAGER_RESULTS_EXCHANGE_VERSION,
        "exchange_mode": "mixed-v1-v2",
        "result_schema_versions": list(SUPPORTED_RESULT_SCHEMAS),
        "container_id_fields": {
            RESULT_SCHEMA_VERSION: "package_id",
            BUNDLE_RESULT_SCHEMA_VERSION: "bundle_id",
        },
        "exported_at": exported_at,
        "results": rows,
    }


def render_manager_results(
    rows: list[dict[str, Any]], *, format_name: str, exported_at: str
) -> tuple[bytes, str, str]:
    if format_name == "json":
        value = manager_results_export(rows, exported_at=exported_at)
        payload = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        return payload, "application/json; charset=utf-8", "sciencevision-results.json"
    if format_name == "csv":
        validate_export_rows(rows)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=RESULT_EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(
            {key: csv_safe(value) for key, value in row.items()} for row in rows
        )
        return (
            output.getvalue().encode("utf-8"),
            "text/csv; charset=utf-8",
            "sciencevision-results.csv",
        )
    raise ContractError("export format must be json or csv")
