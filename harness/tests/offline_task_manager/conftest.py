from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from tools.offline_task_manager.store import ManagerStore


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_admission_receipt(
    path: Path,
    tasks_path: Path,
    rows: list[dict],
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    revisions: dict[str, str] = {}
    for row in rows:
        task_id = row["task_id"]
        source_hash = row["source_revision_sha256"]
        if task_id in revisions and revisions[task_id] != source_hash:
            raise ValueError(f"conflicting fixture revision for {task_id}")
        revisions[task_id] = source_hash
    revision_rows = [
        {"source_line_sha256": revisions[task_id], "task_id": task_id}
        for task_id in sorted(revisions)
    ]
    revision_payload = "".join(canonical_json(row) + "\n" for row in revision_rows)
    receipt: dict[str, Any] = {
        "schema_version": "sciencevision.qa_export_admission_receipt.v1",
        "status": "passed",
        "purpose": "expert_review_distribution",
        "tasks": {
            "path": f"tasks/{tasks_path.name}",
            "count": len(rows),
            "sha256": hashlib.sha256(tasks_path.read_bytes()).hexdigest(),
        },
        "task_revision_set": {
            "scope": "active_source_task_revisions",
            "algorithm": "sha256-canonical-json-lines-v1",
            "count": len(revision_rows),
            "digest": hashlib.sha256(revision_payload.encode("utf-8")).hexdigest(),
        },
        "source_admission": {
            "expert-v2": {
                "policy": "excluded",
                "task_count": 0,
                "admitted_count": 0,
                "rejected_count": 0,
                "rejection_reason_counts": {},
            },
            "scale-v2.2": {
                "policy": "annotation_only",
                "task_count": len(revision_rows),
                "admitted_count": len(revision_rows),
                "rejected_count": 0,
                "rejection_reason_counts": {},
            },
        },
        "policy": {
            "name": "annotation_only",
            "path": "policy/annotation_only.json",
            "sha256": "1" * 64,
        },
        "qc": {
            "expert-v2": {
                "classifications_path": "qc/ev2.jsonl",
                "classifications_sha256": "2" * 64,
                "report_path": "qc/ev2.json",
                "report_sha256": "3" * 64,
            },
            "scale-v2.2": {
                "classifications_path": "qc/scale.jsonl",
                "classifications_sha256": "4" * 64,
                "report_path": "qc/scale.json",
                "report_sha256": "5" * 64,
            },
        },
        "admission_lock": {"path": "admission-lock.json", "sha256": "6" * 64},
    }
    if mutate:
        mutate(receipt)
    receipt["receipt_digest"] = hashlib.sha256(
        canonical_json(receipt).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def workflow(tmp_path: Path) -> dict:
    root = tmp_path / "source"
    asset_path = root / "assets" / "model.cif"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"data_model_fixture\n")
    unused = root / "assets" / "unused.bin"
    unused.write_bytes(b"must not be copied")
    digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()

    viewer = root / "viewer"
    shared = viewer / "shared"
    vendor = shared / "vendor" / "fixture"
    vendor.mkdir(parents=True)
    (shared / "screenshot-api.js").write_text(
        "window.__ScienceVisionScreenshotProvider = {};\n", encoding="utf-8"
    )
    (shared / "task-screenshot.js").write_text(
        "window.ScienceVisionTaskScreenshot = {};\n", encoding="utf-8"
    )
    (shared / "style.css").write_text("body { background: #000; }\n", encoding="utf-8")
    (vendor / "runtime.bin").write_bytes(b"complete shared viewer runtime")
    (viewer / "index.html").write_text("<!doctype html><title>Viewer</title>\n")
    for name in ("molstar.html", "niivue.html"):
        options = (
            "\nconst fixtureOptions = {\n      layoutShowSequence: true,\n};"
            if name == "molstar.html"
            else ""
        )
        (viewer / name).write_text(
            "<!doctype html><script src=\"/viewer/shared/screenshot-api.js\"></script>"
            f"<script>const params = new URLSearchParams(location.search);{options}</script>\n",
            encoding="utf-8",
        )

    def task(task_id: str, domain: str = "biology") -> dict:
        source_line_sha256 = hashlib.sha256(
            (canonical_json({"source_task_id": task_id}) + "\n").encode("utf-8")
        ).hexdigest()
        return {
            "task_id": task_id,
            "domain": domain,
            "title": f"Task {task_id}",
            "instruction": "Inspect the complete 3D model.",
            "answer_schema": {"kind": "free_text", "fields": ["answer"]},
            "asset": {
                "asset_id": "fixture:model",
                "name": "model",
                "path": "/assets/model.cif",
                "repo_path": "assets/model.cif",
                "viewer": "molstar",
                "format": "mmcif",
                "sha256": digest,
                "byte_size": asset_path.stat().st_size,
                "internal_secret": "ASSET_SECRET",
            },
            "asset_hashes": [
                {
                    "repo_path": "assets/model.cif",
                    "sha256": digest,
                    "byte_size": asset_path.stat().st_size,
                }
            ],
            "ground_truth": {"value": "GROUND_TRUTH_SECRET"},
            "asset_grounding": {"verified_entities": "GROUNDING_SECRET"},
            "oracle_receipt_id": "ORACLE_SECRET",
            "scorer_config": {"expected": "SCORER_SECRET"},
            "validation_record": {"result": "VALIDATION_SECRET"},
            "acceptance_info": {"result": "ACCEPTANCE_SECRET"},
            "qc": {"decision": "QC_SECRET"},
            "quarantine": {"reason": "QUARANTINE_SECRET"},
            "sources": {"receipt": "SOURCE_SECRET"},
            "source_revision_sha256": source_line_sha256,
        }

    tasks = [task(f"task-{index}") for index in range(1, 5)]
    tasks_path = root / "tasks.jsonl"
    write_jsonl(tasks_path, tasks)
    admission_receipt = write_admission_receipt(
        root / "qa_export_admission_receipt.json", tasks_path, tasks
    )
    db_path = tmp_path / "manager.db"
    store = ManagerStore(db_path)
    store.import_experts_csv(
        "schema_version,expert_id,name,domains\n"
        "sciencevision.experts_csv.v1,expert-a,<b>Alice</b>,biology\n"
        "sciencevision.experts_csv.v1,expert-b,Bob,biology\n"
    )
    yield {
        "root": root,
        "viewer": viewer,
        "asset": asset_path,
        "unused": unused,
        "digest": digest,
        "task": task,
        "tasks": tasks,
        "tasks_path": tasks_path,
        "admission_receipt": admission_receipt,
        "write_admission_receipt": write_admission_receipt,
        "store": store,
        "db": db_path,
        "tmp": tmp_path,
    }
    store.close()


@pytest.fixture
def imported_workflow(workflow: dict) -> dict:
    workflow["store"].import_batch(
        batch_id="batch-r1",
        tasks_jsonl=workflow["tasks_path"],
        asset_root=workflow["root"],
        admission_receipt=workflow["admission_receipt"],
    )
    return workflow
