from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tools.offline_task_manager.contract import object_sha256, submission_payload_sha256

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cli(db: Path, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "tools.offline_task_manager", "--db", str(db), *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_cli_lists_online_user_metadata_without_secrets(tmp_path: Path) -> None:
    db = tmp_path / "manager.db"
    credentials = tmp_path / "credentials.json"
    handoff = tmp_path / "handoff.csv"
    session_secret = tmp_path / "session.key"
    run_cli(
        db,
        "create-online-credentials",
        "--credentials",
        str(credentials),
        "--plaintext",
        str(handoff),
        "--session-secret",
        str(session_secret),
    )

    result = run_cli(
        db,
        "list-online-users",
        "--credentials",
        str(credentials),
    )

    assert result == {
        "users": [
            {
                "username": "admin",
                "role": "admin",
                "expert_id": None,
                "display_name": "ScienceVision administrator",
            }
        ]
    }
    serialized = json.dumps(result)
    assert all(
        field not in serialized
        for field in ("password", "password_hash", "salt", "iterations")
    )


def test_cli_import_assign_package_result_import_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    asset = source / "assets/model.cif"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"e2e fixture")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    viewer = source / "viewer"
    (viewer / "shared").mkdir(parents=True)
    (viewer / "shared/screenshot-api.js").write_text("window.provider = {};\n")
    (viewer / "shared/task-screenshot.js").write_text("window.screenshot = {};\n")
    (viewer / "index.html").write_text("<!doctype html><title>Viewer</title>\n")
    for page in ("molstar.html", "niivue.html"):
        options = (
            "\nconst fixtureOptions = {\n      layoutShowSequence: true,\n};"
            if page == "molstar.html"
            else ""
        )
        (viewer / page).write_text(
            '<script src="/viewer/shared/screenshot-api.js"></script>'
            f'<script>const params = new URLSearchParams(location.search);{options}</script>\n',
            encoding="utf-8",
        )
    task = {
        "task_id": "e2e-task",
        "domain": "biology",
        "title": "E2E task",
        "instruction": "Inspect the model.",
        "asset": {
            "path": "/assets/model.cif",
            "repo_path": "assets/model.cif",
            "sha256": digest,
            "byte_size": asset.stat().st_size,
            "viewer": "molstar",
            "format": "mmcif",
        },
        "asset_hashes": [
            {
                "repo_path": "assets/model.cif",
                "sha256": digest,
                "byte_size": asset.stat().st_size,
            }
        ],
        "ground_truth": {"answer": "must not ship"},
        "source_revision_sha256": hashlib.sha256(
            b'{"task_id":"e2e-task"}\n'
        ).hexdigest(),
    }
    tasks = source / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    revision_row = {
        "source_line_sha256": task["source_revision_sha256"],
        "task_id": task["task_id"],
    }
    revision_payload = json.dumps(
        revision_row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    receipt_body = {
        "schema_version": "sciencevision.qa_export_admission_receipt.v1",
        "status": "passed",
        "purpose": "expert_review_distribution",
        "tasks": {
            "path": "tasks/tasks.jsonl",
            "count": 1,
            "sha256": hashlib.sha256(tasks.read_bytes()).hexdigest(),
        },
        "task_revision_set": {
            "scope": "active_source_task_revisions",
            "algorithm": "sha256-canonical-json-lines-v1",
            "count": 1,
            "digest": hashlib.sha256(revision_payload.encode()).hexdigest(),
        },
        "source_admission": {},
        "policy": {"name": "annotation_only", "path": "policy.json", "sha256": "1" * 64},
        "qc": {},
        "admission_lock": {"path": "admission-lock.json", "sha256": "2" * 64},
    }
    receipt = receipt_body | {"receipt_digest": object_sha256(receipt_body)}
    receipt_path = source / "qa_export_admission_receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    experts = tmp_path / "experts.csv"
    experts.write_text("expert_id,name,domains\nexpert-e2e,E2E Expert,biology\n")
    db = tmp_path / "manager.db"
    package = tmp_path / "expert-package"

    assert run_cli(db, "import-experts", "--csv", str(experts))["imported"] == 1
    assert run_cli(
        db,
        "import-batch",
        "--batch-id",
        "batch-e2e",
        "--tasks-jsonl",
        str(tasks),
        "--asset-root",
        str(source),
        "--admission-receipt",
        str(receipt_path),
        "--admission-purpose",
        "expert_review_distribution",
    )["imported"] == 1
    assert run_cli(db, "auto-assign", "--batch-id", "batch-e2e")["assigned"] == 1
    manifest = run_cli(
        db,
        "build-package",
        "--batch-id",
        "batch-e2e",
        "--expert-id",
        "expert-e2e",
        "--output",
        str(package),
    )
    subprocess.run(
        [sys.executable, "serve_package.py", "--verify-only"],
        cwd=package,
        check=True,
        capture_output=True,
        text=True,
    )
    assignment = manifest["assignments"][0]
    assert manifest["admission"]["receipt_digest"] == receipt["receipt_digest"]
    result = {
        "schema_version": "sciencevision.expert_result.v1",
        "package_id": manifest["package_id"],
        "batch_id": manifest["batch_id"],
        "expert_id": manifest["expert_id"],
        "assignment_id": assignment["assignment_id"],
        "task_id": assignment["task_id"],
        "task_revision_sha256": assignment["task_revision_sha256"],
        "attempt": 1,
        "task_validation": {"status": "valid", "feedback": "Valid fixture"},
        "answer": "fixture answer",
        "reasoning": "fixture reasoning",
        "trajectory": {
            "schema_version": "sciencevision.trajectory_ref.v1",
            "status": "not_collected",
            "reason": "collector_not_implemented",
        },
        "evidence": [],
        "saved_at": "2026-08-13T00:00:00Z",
    }
    body = {
        "schema_version": "sciencevision.expert_submission.v1",
        "contract_version": "sciencevision.offline_exchange.v1",
        "submission_id": "sub_" + object_sha256(result)[:24],
        "package_id": manifest["package_id"],
        "package_manifest_sha256": manifest["manifest_sha256"],
        "batch_id": manifest["batch_id"],
        "expert_id": manifest["expert_id"],
        "results": [result],
    }
    submission = body | {"payload_sha256": submission_payload_sha256(body)}
    result_file = tmp_path / "submission.json"
    result_file.write_text(json.dumps(submission), encoding="utf-8")
    report = run_cli(db, "import-results", "--input", str(result_file))
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    status = run_cli(db, "status")
    assert status["results"][0]["answer"] == "fixture answer"
    assert status["results"][0]["trajectory_status"] == "not_collected"
