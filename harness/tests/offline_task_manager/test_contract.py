from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.offline_task_manager.contract import (
    ContractError,
    active_source_task_revisions,
    asset_records,
    redact_task,
    stable_assignment_id,
    stable_package_id,
    source_task_revision_set_digest,
    task_revision_sha256,
)


def test_redaction_is_an_allowlist_projection(workflow: dict) -> None:
    task = dict(workflow["tasks"][0])
    task["options"] = [
        "choice A",
        {"id": "b", "text": "choice B", "correct": True, "oracle": "SECRET"},
    ]
    task["asset"]["view_hint"] = {
        "cam": "safe-camera",
        "ground_truth": "NESTED_VIEW_SECRET",
    }
    task["required_evidence"] = ["screenshot"]
    redacted, removed = redact_task(task)
    serialized = json.dumps(redacted, sort_keys=True)
    assert redacted["instruction"] == task["instruction"]
    assert redacted["asset"]["viewer"] == "molstar"
    assert "internal_secret" not in redacted["asset"]
    assert "asset.internal_secret" in removed
    assert redacted["options"] == ["choice A", {"id": "b", "text": "choice B"}]
    assert redacted["asset"]["view_hint"] == {"cam": "safe-camera"}
    assert "correct" not in serialized
    for secret in (
        "GROUND_TRUTH_SECRET",
        "GROUNDING_SECRET",
        "ORACLE_SECRET",
        "SCORER_SECRET",
        "VALIDATION_SECRET",
        "ACCEPTANCE_SECRET",
        "QC_SECRET",
        "QUARANTINE_SECRET",
        "SOURCE_SECRET",
        "ASSET_SECRET",
        "NESTED_VIEW_SECRET",
    ):
        assert secret not in serialized
    assert set(task) - set(redacted) >= {
        "ground_truth",
        "asset_grounding",
        "oracle_receipt_id",
        "scorer_config",
        "validation_record",
        "acceptance_info",
        "qc",
        "quarantine",
        "sources",
    }


def test_stable_ids_use_machine_keys_and_revision(workflow: dict) -> None:
    task = workflow["tasks"][0]
    revision = task_revision_sha256(task)
    first = stable_assignment_id("batch-r1", "expert-a", task["task_id"], revision)
    second = stable_assignment_id("batch-r1", "expert-a", task["task_id"], revision)
    assert first == second
    assert first != stable_assignment_id("batch-r1", "expert-b", task["task_id"], revision)
    rows = [
        {"assignment_id": first, "task_id": task["task_id"], "task_revision_sha256": revision}
    ]
    assert stable_package_id("batch-r1", "expert-a", rows) == stable_package_id(
        "batch-r1", "expert-a", reversed(rows)
    )
    changed = dict(task, title="Changed")
    assert task_revision_sha256(changed) != revision


def test_asset_paths_reject_traversal_and_remote_sources(workflow: dict) -> None:
    task = workflow["task"]("bad")
    task["asset"]["repo_path"] = "../secret.cif"
    with pytest.raises(ContractError, match="unsafe asset path"):
        asset_records(task)
    task = workflow["task"]("remote")
    task["asset"]["path"] = "https://example.test/model.cif"
    with pytest.raises(ContractError, match="remote asset paths"):
        asset_records(task)


def test_redaction_rejects_nested_values_in_text_only_fields(workflow: dict) -> None:
    task = workflow["task"]("nested-secret")
    task["instruction"] = {"answer": "NESTED_SECRET"}
    with pytest.raises(ContractError, match="instruction must be text"):
        redact_task(task)

    task = workflow["task"]("nested-evidence")
    task["required_evidence"] = [{"kind": "screenshot", "oracle": "NESTED_SECRET"}]
    with pytest.raises(ContractError, match="required_evidence"):
        redact_task(task)


def test_asset_closure_is_exact_and_cannot_overlap_package_code(workflow: dict) -> None:
    extra = workflow["task"]("extra")
    extra["asset_hashes"].append(
        {"repo_path": "assets/unused.bin", "sha256": "0" * 64}
    )
    with pytest.raises(ContractError, match="exactly match.*unreferenced"):
        asset_records(extra)

    mismatch = workflow["task"]("mismatch")
    mismatch["asset"]["path"] = "/assets/public.cif"
    with pytest.raises(ContractError, match="path and repo_path"):
        asset_records(mismatch)

    for unsafe in ("//example.test/model.cif", "viewer/molstar.html", "index.html"):
        task = workflow["task"]("unsafe")
        task["asset"]["path"] = unsafe
        task["asset"]["repo_path"] = unsafe
        task["asset_hashes"][0]["repo_path"] = unsafe
        with pytest.raises(ContractError):
            asset_records(task)


def test_active_source_revision_digest_is_sorted_and_deduplicated(workflow: dict) -> None:
    first = workflow["task"]("source-b")
    second = workflow["task"]("source-a")
    revisions = active_source_task_revisions([first, second])
    assert [row["task_id"] for row in revisions] == ["source-a", "source-b"]
    expected_lines = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in revisions
    )
    import hashlib

    assert source_task_revision_set_digest(revisions) == hashlib.sha256(
        expected_lines.encode("utf-8")
    ).hexdigest()


def test_active_source_revision_conflict_is_rejected(workflow: dict) -> None:
    first = workflow["task"]("duplicate")
    conflicting = dict(first, source_revision_sha256="0" * 64)
    with pytest.raises(ContractError, match="conflicting source_revision_sha256"):
        active_source_task_revisions([first, conflicting])


def test_exchange_schemas_are_valid_json_and_require_admission() -> None:
    schema_root = (
        Path(__file__).resolve().parents[2] / "tools/offline_task_manager/schemas"
    )
    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in schema_root.glob("*.json")
    }
    assert schemas["qa_export_admission_receipt.schema.json"]["$id"] == (
        "sciencevision.qa_export_admission_receipt.v1"
    )
    assert "admission" in schemas["package_manifest.schema.json"]["required"]
    assert schemas["manager_results_export.schema.json"]["$id"] == (
        "sciencevision.manager_results_export.v2"
    )
    assert "source_revision_sha256" in schemas["task_batch.schema.json"]["required"]
    assert "subquestions" not in schemas["task_batch.schema.json"]["required"]
