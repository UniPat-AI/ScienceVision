from __future__ import annotations

import copy
import json

import pytest

from tools.offline_task_manager import bundle as bundle_module
from tools.offline_task_manager.bundle import (
    LEGACY_INTEGRITY_ALLOW_MISSING,
    build_runtime_manifest,
    prepare_expert_bundle,
    prepare_legacy_bundle,
    require_runtime_compatibility,
)
from tools.offline_task_manager.contract import (
    ContractError,
    object_sha256,
    submission_payload_sha256,
)
from tools.offline_task_manager.packager import build_expert_package
from tools.offline_task_manager.store import PreflightError


def test_bundle_id_tracks_content_not_deployment(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    inputs = store.package_inputs(batch_id="batch-r1", expert_id="expert-a")
    baseline = prepare_expert_bundle(inputs, batch_id="batch-r1", expert_id="expert-a")
    immutable_manifest = {
        key: value
        for key, value in baseline["manifest"].items()
        if key not in {"bundle_id", "bundle_manifest_sha256"}
    }
    assert baseline["bundle_id"] == "bndl_" + object_sha256(immutable_manifest)[:24]

    relocated = copy.deepcopy(inputs)
    relocated["batch"]["asset_root"] = "/different/deployment/assets"
    relocated["batch"]["viewer_root"] = "/different/shared/runtime"
    relocated["deployment"] = {"content_base": "https://example.test/content/"}
    assert (
        prepare_expert_bundle(
            relocated, batch_id="batch-r1", expert_id="expert-a"
        )["bundle_id"]
        == baseline["bundle_id"]
    )

    changed_task = copy.deepcopy(inputs)
    changed_task["assignments"][0]["payload"]["instruction"] += " Rotate once."
    assert (
        prepare_expert_bundle(
            changed_task, batch_id="batch-r1", expert_id="expert-a"
        )["bundle_id"]
        != baseline["bundle_id"]
    )

    changed_asset = copy.deepcopy(inputs)
    for assignment in changed_asset["assignments"]:
        assignment["assets"][0]["sha256"] = "f" * 64
    assert (
        prepare_expert_bundle(
            changed_asset, batch_id="batch-r1", expert_id="expert-a"
        )["bundle_id"]
        != baseline["bundle_id"]
    )

    changed_expert = copy.deepcopy(inputs)
    changed_expert["expert"]["name"] = "Renamed expert"
    assert (
        prepare_expert_bundle(
            changed_expert, batch_id="batch-r1", expert_id="expert-a"
        )["bundle_id"]
        != baseline["bundle_id"]
    )

    original_policy = bundle_module.REDACTION_POLICY
    bundle_module.REDACTION_POLICY = "expert-visible-allowlist-v2"
    try:
        assert (
            prepare_expert_bundle(
                inputs, batch_id="batch-r1", expert_id="expert-a"
            )["bundle_id"]
            != baseline["bundle_id"]
        )
    finally:
        bundle_module.REDACTION_POLICY = original_policy


def test_runtime_compatibility_checks_loader_renderers_and_capabilities() -> None:
    runtime = {
        "loader_api": "sciencevision.viewer_loader.v1",
        "renderers": ["molstar", "niivue"],
        "capabilities": ["expert-review", "task-screenshot"],
    }
    requirement = {
        "loader_api": "sciencevision.viewer_loader.v1",
        "renderers": ["molstar"],
        "capabilities": ["expert-review", "task-screenshot"],
    }
    require_runtime_compatibility(requirement, runtime)

    with pytest.raises(ContractError, match="loader API"):
        require_runtime_compatibility(requirement | {"loader_api": "other"}, runtime)
    with pytest.raises(ContractError, match="required renderers: cornerstone"):
        require_runtime_compatibility(
            requirement | {"renderers": ["cornerstone"]}, runtime
        )
    with pytest.raises(ContractError, match="required capabilities: evidence-capture"):
        require_runtime_compatibility(
            requirement | {"capabilities": ["evidence-capture"]}, runtime
        )


def test_publish_is_atomic_and_preserves_active_pointers(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    before = {
        row["expert_id"]: row["bundle_id"]
        for row in store.connection.execute("SELECT * FROM active_task_bundles")
    }

    store.import_batch(
        batch_id="batch-r2",
        tasks_jsonl=imported_workflow["tasks_path"],
        asset_root=imported_workflow["root"],
        admission_receipt=imported_workflow["admission_receipt"],
    )
    store.auto_assign(batch_id="batch-r2")
    real_insert = store._insert_task_bundle
    calls = 0

    def fail_second(prepared: dict, created_at: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated publish failure")
        real_insert(prepared, created_at)

    monkeypatch.setattr(store, "_insert_task_bundle", fail_second)
    with pytest.raises(RuntimeError, match="simulated publish failure"):
        store.publish_batch(batch_id="batch-r2")

    after = {
        row["expert_id"]: row["bundle_id"]
        for row in store.connection.execute("SELECT * FROM active_task_bundles")
    }
    assert after == before
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM task_bundles WHERE batch_id='batch-r2'"
        ).fetchone()[0]
        == 0
    )


def test_v2_bundle_submission_round_trip(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    context = store.active_bundle_context("expert-a")
    task_row = json.loads(context["tasks_jsonl"].splitlines()[0])
    result = {
        "schema_version": "sciencevision.expert_bundle_result.v2",
        "bundle_id": context["bundle_id"],
        "batch_id": task_row["batch_id"],
        "expert_id": task_row["expert_id"],
        "assignment_id": task_row["assignment_id"],
        "task_id": task_row["task_id"],
        "task_revision_sha256": task_row["task_revision_sha256"],
        "attempt": 1,
        "task_validation": {"status": "valid", "feedback": "Looks correct."},
        "answer": "Reviewed answer",
        "reasoning": "Reviewed against the model.",
        "trajectory": {
            "schema_version": "sciencevision.trajectory_ref.v1",
            "status": "not_collected",
            "reason": "collector_not_implemented",
        },
        "evidence": [],
        "saved_at": "2026-08-15T00:00:00Z",
    }
    body = {
        "schema_version": "sciencevision.expert_bundle_submission.v2",
        "contract_version": "sciencevision.expert_exchange.v2",
        "submission_id": "sub_" + object_sha256(result)[:24],
        "bundle_id": context["bundle_id"],
        "bundle_manifest_sha256": context["manifest"]["bundle_manifest_sha256"],
        "batch_id": result["batch_id"],
        "expert_id": result["expert_id"],
        "results": [result],
    }
    submission = body | {"payload_sha256": submission_payload_sha256(body)}

    report = store.import_submission(submission)
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    exported = store.result_rows()
    assert exported[0]["bundle_id"] == context["bundle_id"]
    assert exported[0]["package_id"] is None
    assert exported[0]["answer"] == "Reviewed answer"


def test_publish_rechecks_assets_and_does_not_activate_drifted_source(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    source = imported_workflow["asset"]
    source.write_bytes(b"x" * source.stat().st_size)

    with pytest.raises(PreflightError, match="asset hash mismatch"):
        store.publish_batch(batch_id="batch-r1")

    assert store.connection.execute("SELECT COUNT(*) FROM task_bundles").fetchone()[0] == 0
    assert (
        store.connection.execute("SELECT COUNT(*) FROM active_task_bundles").fetchone()[0]
        == 0
    )


@pytest.mark.parametrize(
    ("runtime_failure", "message"),
    [
        ("renderer", "missing required renderers: molstar"),
        ("capability", "missing required capabilities: task-screenshot"),
        ("loader", "loader API"),
    ],
)
def test_publish_validates_runtime_before_activation(
    imported_workflow: dict,
    monkeypatch: pytest.MonkeyPatch,
    runtime_failure: str,
    message: str,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    if runtime_failure == "renderer":
        (imported_workflow["viewer"] / "molstar.html").unlink()
    elif runtime_failure == "capability":
        (imported_workflow["viewer"] / "shared/task-screenshot.js").unlink()
    else:
        real_builder = build_runtime_manifest

        def incompatible_loader(*args, **kwargs):
            manifest = real_builder(*args, **kwargs)
            return manifest | {"loader_api": "sciencevision.viewer_loader.other"}

        monkeypatch.setattr(
            "tools.offline_task_manager.store.build_runtime_manifest",
            incompatible_loader,
        )

    with pytest.raises(ContractError, match=message):
        store.publish_batch(batch_id="batch-r1")
    assert store.connection.execute("SELECT COUNT(*) FROM task_bundles").fetchone()[0] == 0
    assert (
        store.connection.execute("SELECT COUNT(*) FROM active_task_bundles").fetchone()[0]
        == 0
    )


def test_legacy_adapter_is_explicit_and_does_not_fabricate_admission(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "legacy-package"
    build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )

    prepared = prepare_legacy_bundle(output)
    manifest = prepared["manifest"]
    assert manifest["read_only"] is True
    assert manifest["admission_status"] == "legacy_pre_admission"
    assert "admission" not in manifest
    imported = store.import_legacy_package(output)
    assert imported["admission_status"] == "legacy_pre_admission"
    assert store.active_bundle_context("expert-a")["source_kind"] == "legacy-v1"


def test_legacy_adapter_rejects_unsafe_task_paths_and_checksum_drift(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "legacy-integrity"
    manifest = build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )
    assert manifest["tasks_path"] == "tasks/tasks.jsonl"

    tasks_path = output / "tasks/tasks.jsonl"
    tasks_path.write_text(tasks_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="checksum mismatch"):
        prepare_legacy_bundle(output)

    manifest_path = output / "package_manifest.json"
    changed = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed["tasks_path"] = "../outside.jsonl"
    body = {key: value for key, value in changed.items() if key != "manifest_sha256"}
    changed["manifest_sha256"] = object_sha256(body)
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ContractError, match="unsafe legacy tasks_path"):
        prepare_legacy_bundle(output)


def test_legacy_missing_checksums_requires_explicit_historical_policy(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "legacy-pre-checksum"
    build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )
    (output / "SHA256SUMS").unlink()

    manifest_path = output / "package_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tasks_path"] = "/tasks/tasks.jsonl"
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    manifest["manifest_sha256"] = object_sha256(body)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ContractError, match="unsafe legacy tasks_path"):
        prepare_legacy_bundle(output)
    prepared = prepare_legacy_bundle(
        output, integrity_policy=LEGACY_INTEGRITY_ALLOW_MISSING
    )
    integrity = prepared["manifest"]["legacy_package"]["integrity"]
    assert integrity["policy"] == LEGACY_INTEGRITY_ALLOW_MISSING
    assert integrity["sha256sums_present"] is False
    assert integrity["tasks_path_policy"] == "historical-root-url-exact-v1"


def test_legacy_historical_policy_never_allows_symlink_escape(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "legacy-linked-task"
    build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )
    (output / "SHA256SUMS").unlink()
    tasks_path = output / "tasks/tasks.jsonl"
    outside = imported_workflow["tmp"] / "outside.jsonl"
    outside.write_text(tasks_path.read_text(encoding="utf-8"), encoding="utf-8")
    tasks_path.unlink()
    tasks_path.symlink_to(outside)

    with pytest.raises(ContractError, match="contains a symlink"):
        prepare_legacy_bundle(
            output, integrity_policy=LEGACY_INTEGRITY_ALLOW_MISSING
        )
