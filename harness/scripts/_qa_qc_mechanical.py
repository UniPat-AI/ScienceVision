"""Mechanical CAD engineering-specific QA/QC checks."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TASKGEN_ROOT = ROOT / "QA-Gen/ScienceVisionTaskGenerator"
try:
    from sciencevision_taskgen.mechanical_cad_contracts import (
        MechanicalTrustError,
        engineering_family_signals,
        load_trusted_case_manifest,
        validate_engineering_task,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(TASKGEN_ROOT))
    from sciencevision_taskgen.mechanical_cad_contracts import (
        MechanicalTrustError,
        engineering_family_signals,
        load_trusted_case_manifest,
        validate_engineering_task,
    )


CODE_ALIASES = {
    "asset_binding_content_mismatch": "mechanical_asset_binding_content_mismatch",
    "engineering_generator_invalid": "mechanical_generator_invalid",
    "engineering_scorer_invalid": "mechanical_scorer_invalid",
    "engineering_task_id_invalid": "mechanical_task_id_invalid",
    "engineering_v_class_invalid": "mechanical_v_class_invalid",
    "gt_artifact_answer_mismatch": "mechanical_gt_artifact_answer_mismatch",
    "mechanical_contract_missing_or_invalid": "mechanical_contract_missing_or_invalid",
    "receipt_actual_answer_forged": "mechanical_recomputed_answer_mismatch",
    "receipt_actual_artifact_forged": "mechanical_recomputed_artifact_mismatch",
}


def _row(code: str, message: str, evidence: Any = None) -> dict[str, Any]:
    result = {"code": code, "severity": "error", "message": message}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def load_mechanical_trust(
    manifest_path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Load an externally anchored case manifest or fail closed."""
    return load_trusted_case_manifest(manifest_path, expected_sha256)


def audit_mechanical_contract(
    task: dict[str, Any],
    *,
    trust: dict[str, Any] | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    if not strict and not engineering_family_signals(task):
        return []
    provenance = task.get("provenance") if isinstance(task.get("provenance"), dict) else {}
    trusted_case = None
    manifest_path = None
    if trust is not None:
        trusted_case = trust["cases"].get(provenance.get("case_id"))
        manifest_path = trust["manifest_path"]
    reasons = validate_engineering_task(
        task,
        trusted_case=trusted_case,
        manifest_path=manifest_path,
        strict=strict or trust is not None,
    )
    return [
        _row(
            CODE_ALIASES.get(reason["code"], f"mechanical_{reason['code']}"),
            reason["detail"],
            reason.get("evidence"),
        )
        for reason in reasons
    ]


__all__ = [
    "MechanicalTrustError",
    "audit_mechanical_contract",
    "load_mechanical_trust",
]
