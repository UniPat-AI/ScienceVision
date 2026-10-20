from __future__ import annotations

import csv
import io
import json
from copy import deepcopy

import pytest

from tools.offline_task_manager.__main__ import export_results
from tools.offline_task_manager.contract import (
    ContractError,
    object_sha256,
    submission_payload_sha256,
)
from tools.offline_task_manager.packager import build_expert_package
from tools.offline_task_manager.results_export import render_manager_results


def make_submission(manifest: dict, assignment: dict, **overrides: object) -> dict:
    result = {
        "schema_version": "sciencevision.expert_result.v1",
        "package_id": manifest["package_id"],
        "batch_id": manifest["batch_id"],
        "expert_id": manifest["expert_id"],
        "assignment_id": assignment["assignment_id"],
        "task_id": assignment["task_id"],
        "task_revision_sha256": assignment["task_revision_sha256"],
        "attempt": 1,
        "task_validation": {"status": "valid", "feedback": "The task is clear."},
        "answer": "answer text",
        "reasoning": "reasoning text",
        "trajectory": {
            "schema_version": "sciencevision.trajectory_ref.v1",
            "status": "not_collected",
            "reason": "collector_not_implemented",
        },
        "evidence": [],
        "saved_at": "2026-08-13T00:00:00Z",
    }
    result.update(overrides)
    submission_id = "sub_" + object_sha256(result)[:24]
    body = {
        "schema_version": "sciencevision.expert_submission.v1",
        "contract_version": "sciencevision.offline_exchange.v1",
        "submission_id": submission_id,
        "package_id": manifest["package_id"],
        "package_manifest_sha256": manifest["manifest_sha256"],
        "batch_id": manifest["batch_id"],
        "expert_id": manifest["expert_id"],
        "results": [result],
    }
    return body | {"payload_sha256": submission_payload_sha256(body)}


def prepared(imported_workflow: dict) -> tuple[dict, dict]:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    manifest = build_expert_package(
        store,
        batch_id="batch-r1",
        expert_id="expert-a",
        output=imported_workflow["tmp"] / "result-package",
    )
    return manifest, manifest["assignments"][0]


def test_result_import_accepts_then_reports_same_submission_unchanged(
    imported_workflow: dict,
) -> None:
    manifest, assignment = prepared(imported_workflow)
    submission = make_submission(manifest, assignment)
    first = imported_workflow["store"].import_submission(submission)
    assert first["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    assert first["submission_status"] == "imported"
    second = imported_workflow["store"].import_submission(submission)
    assert second["submission_status"] == "unchanged"
    assert second["counts"] == {"accepted": 0, "duplicates": 1, "rejected": 0}
    row = imported_workflow["store"].result_rows()[0]
    assert row["validation_feedback"] == "The task is clear."
    assert row["answer"] == "answer text"
    assert row["reasoning"] == "reasoning text"
    assert row["trajectory_status"] == "not_collected"


def test_append_only_conflicts_and_identity_drift_are_rejected(
    imported_workflow: dict,
) -> None:
    manifest, assignment = prepared(imported_workflow)
    original = make_submission(manifest, assignment)
    assert imported_workflow["store"].import_submission(original)["counts"]["accepted"] == 1

    same_id_changed = deepcopy(original)
    same_id_changed["results"][0]["answer"] = "changed"
    same_id_changed["payload_sha256"] = submission_payload_sha256(same_id_changed)
    conflict = imported_workflow["store"].import_submission(same_id_changed)
    assert "submission_id already exists" in conflict["rejected"][0]["reason"]

    new_submission = make_submission(manifest, assignment, answer="changed")
    attempt_conflict = imported_workflow["store"].import_submission(new_submission)
    assert "assignment_id + attempt" in attempt_conflict["rejected"][0]["reason"]

    drifted = make_submission(manifest, assignment, task_revision_sha256="0" * 64, attempt=2)
    drift = imported_workflow["store"].import_submission(drifted)
    assert "task_revision_sha256" in drift["rejected"][0]["reason"]

    wrong_expert = make_submission(manifest, assignment)
    wrong_expert["expert_id"] = "expert-b"
    wrong_expert["payload_sha256"] = submission_payload_sha256(wrong_expert)
    wrong = imported_workflow["store"].import_submission(wrong_expert)
    assert "expert_id does not match package" in wrong["rejected"][0]["reason"]


def test_hash_and_trajectory_schema_are_enforced(imported_workflow: dict) -> None:
    manifest, assignment = prepared(imported_workflow)
    bad_hash = make_submission(manifest, assignment)
    bad_hash["payload_sha256"] = "f" * 64
    assert "payload_sha256" in imported_workflow["store"].import_submission(bad_hash)["rejected"][0]["reason"]

    bad_trajectory = make_submission(
        manifest, assignment, trajectory={"capture_status": "not_collected"}
    )
    report = imported_workflow["store"].import_submission(bad_trajectory)
    assert "collector_not_implemented" in report["rejected"][0]["reason"]


def test_fully_rejected_submission_id_is_not_reserved(imported_workflow: dict) -> None:
    manifest, assignment = prepared(imported_workflow)
    rejected = make_submission(
        manifest, assignment, trajectory={"capture_status": "not_collected"}
    )
    rejected_id = rejected["submission_id"]
    report = imported_workflow["store"].import_submission(rejected)
    assert report["submission_status"] == "not_imported"
    assert report["counts"] == {"accepted": 0, "duplicates": 0, "rejected": 1}
    assert imported_workflow["store"].connection.execute(
        "SELECT COUNT(*) FROM submissions WHERE submission_id=?", (rejected_id,)
    ).fetchone()[0] == 0

    corrected = make_submission(manifest, assignment)
    corrected["submission_id"] = rejected_id
    corrected["payload_sha256"] = submission_payload_sha256(corrected)
    retried = imported_workflow["store"].import_submission(corrected)
    assert retried["submission_status"] == "imported"
    assert retried["counts"]["accepted"] == 1


def test_duplicate_only_submission_id_is_not_reserved(imported_workflow: dict) -> None:
    manifest, assignment = prepared(imported_workflow)
    original = make_submission(manifest, assignment)
    assert imported_workflow["store"].import_submission(original)["counts"]["accepted"] == 1

    duplicate_only = deepcopy(original)
    duplicate_only["submission_id"] = "sub_" + "f" * 24
    duplicate_only["payload_sha256"] = submission_payload_sha256(duplicate_only)
    duplicate_report = imported_workflow["store"].import_submission(duplicate_only)
    assert duplicate_report["submission_status"] == "not_imported"
    assert duplicate_report["counts"] == {"accepted": 0, "duplicates": 1, "rejected": 0}
    assert imported_workflow["store"].connection.execute(
        "SELECT COUNT(*) FROM submissions WHERE submission_id=?",
        (duplicate_only["submission_id"],),
    ).fetchone()[0] == 0

    next_attempt = make_submission(manifest, assignment, attempt=2)
    next_attempt["submission_id"] = duplicate_only["submission_id"]
    next_attempt["payload_sha256"] = submission_payload_sha256(next_attempt)
    assert imported_workflow["store"].import_submission(next_attempt)["counts"]["accepted"] == 1


def test_unknown_contract_fields_are_rejected(imported_workflow: dict) -> None:
    manifest, assignment = prepared(imported_workflow)
    submission = make_submission(manifest, assignment)
    submission["results"][0]["unreviewed_payload"] = "must not be silently accepted"
    submission["payload_sha256"] = submission_payload_sha256(submission)
    report = imported_workflow["store"].import_submission(submission)
    assert report["submission_status"] == "not_imported"
    assert "unexpected unreviewed_payload" in report["rejected"][0]["reason"]


def test_manager_results_export_v2_defines_mixed_v1_v2_exchange() -> None:
    base = {
        "schema_version": "sciencevision.expert_result.v1",
        "batch_id": "batch-r1",
        "package_id": "pkg_" + "1" * 24,
        "bundle_id": None,
        "expert_id": "expert-a",
        "expert_name": "Expert A",
        "assignment_id": "asgn_" + "2" * 24,
        "task_id": "task-a",
        "task_revision_sha256": "3" * 64,
        "attempt": 1,
        "validation_status": "valid",
        "validation_feedback": "Reviewed",
        "answer": "Answer",
        "reasoning": "Reasoning",
        "trajectory_status": "not_collected",
        "trajectory_step_count": 0,
        "evidence_count": 0,
        "imported_at": "2026-08-15T00:00:00Z",
    }
    v2 = base | {
        "schema_version": "sciencevision.expert_bundle_result.v2",
        "package_id": None,
        "bundle_id": "bndl_" + "4" * 24,
        "assignment_id": "asgn_" + "5" * 24,
        "task_id": "task-b",
    }
    exported_at = "2026-08-15T12:00:00Z"
    payload, _content_type, _filename = render_manager_results(
        [base, v2], format_name="json", exported_at=exported_at
    )
    value = json.loads(payload)
    assert value["schema_version"] == "sciencevision.manager_results_export.v2"
    assert value["contract_version"] == "sciencevision.manager_results_exchange.v2"
    assert value["exchange_mode"] == "mixed-v1-v2"
    assert value["result_schema_versions"] == [
        "sciencevision.expert_result.v1",
        "sciencevision.expert_bundle_result.v2",
    ]
    assert value["results"] == [base, v2]

    csv_payload, _content_type, _filename = render_manager_results(
        [base, v2], format_name="csv", exported_at=exported_at
    )
    rows = list(csv.DictReader(io.StringIO(csv_payload.decode("utf-8"))))
    assert rows[0]["package_id"] == base["package_id"]
    assert rows[1]["bundle_id"] == v2["bundle_id"]

    with pytest.raises(ContractError, match="invalid v2 container binding"):
        render_manager_results(
            [v2 | {"package_id": base["package_id"]}],
            format_name="json",
            exported_at=exported_at,
        )


def test_cli_export_uses_manager_results_export_v2(imported_workflow: dict) -> None:
    manifest, assignment = prepared(imported_workflow)
    imported_workflow["store"].import_submission(make_submission(manifest, assignment))
    output = imported_workflow["tmp"] / "manager-results.json"
    report = export_results(imported_workflow["store"], output, "json")
    assert report["result_count"] == 1
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["schema_version"] == "sciencevision.manager_results_export.v2"
    assert value["results"][0]["package_id"] == manifest["package_id"]
