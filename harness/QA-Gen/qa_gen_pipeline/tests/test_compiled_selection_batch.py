from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import qagen.batch.live as live_module
import qagen.batch.registry as batch_registry

from qagen.batch import (
    BatchError,
    approve_batch_plan,
    execute_batch,
    load_batch_plan,
    load_batch_run,
    load_live_execution,
    prepare_batch,
    resume_batch,
    start_batch,
)
from qagen.batch.registry import _target_leaf_policy
from qagen.cli import main
from qagen.contracts import digest, make_decision, make_envelope
from qagen.errors import GatePendingError, InvalidStateError, StaleInputError
from qagen.paths import WorkspacePaths
from qagen.providers.contracts import ROLE_PROVIDER_MODELS
from qagen.storage.atomic import atomic_write_json, atomic_write_yaml, hash_file
from qagen.viewer.capability_mapping import load_compiled_selection_capability_mapping

from conftest import PACKAGE_ROOT


def _raw_digest(value: str) -> str:
    return value.encode("ascii").hex().ljust(64, "0")[:64]


def _asset(index: int) -> dict[str, Any]:
    return {
        "role": "primary",
        "path": f"assets/source/{index:03d}.cif",
        "bytes": index + 1,
        "sha256": _raw_digest(f"asset-{index}"),
    }


def _policy_row(target_leaf: str) -> dict[str, Any]:
    domain, subdomain = target_leaf.split(".", 1)
    leaf_path = PACKAGE_ROOT / "configs" / "subdomains" / domain / f"{subdomain}.yaml"
    return {
        "target_leaf": target_leaf,
        "target_leaf_config": {
            "path": f"configs/subdomains/{domain}/{subdomain}.yaml",
            "content_sha256": hash_file(leaf_path),
        },
    }


def _selection_manifest(paths: WorkspacePaths, *, count: int = 60) -> tuple[Path, dict[str, Any]]:
    paths.ensure()
    taxonomy_digest = digest({"fixture": "approved-taxonomy"})
    rows: list[dict[str, Any]] = []
    for index in range(count):
        source_track = "raw_compute" if index == 0 else "viewer_measurement"
        slot_digest = digest({"fixture_slot": index})
        rows.append(
            {
                "slot_ref": {"id": f"slot-{index:03d}", "content_digest": slot_digest},
                "question_id": f"legacy-question-{index:03d}",
                "task_id": f"legacy-task-{index:03d}",
                "source_leaf": "chemistry.legacy_source",
                "target_leaf": "chemistry.steric_accessibility",
                "mapping_status": "mapped",
                "access_track": {
                    "source": source_track,
                    "effective": source_track,
                    "conversion": "preserved",
                },
                "generation_strategy": "reauthor_on_asset" if source_track == "raw_compute" else "preserve_intent",
                "viewer_hint": {"source_viewer": "molstar_deployed", "allowed_operations": ["viewer_rotate"]},
                "assets": [_asset(index)],
                "generation_status": "blocked_pending_viewer_knowledge_plan_and_review",
                "target_leaf_config": {
                    "path": "configs/subdomains/chemistry/steric_accessibility.yaml",
                    "content_sha256": hash_file(
                        PACKAGE_ROOT / "configs" / "subdomains" / "chemistry" / "steric_accessibility.yaml"
                    ),
                },
            }
        )
    body = {
        "source_selection": {
            "selection_id_sha256": "a" * 64,
            "selection_file_sha256": digest({"selection": "fixture"}),
            "import_digest": digest({"import": "fixture"}),
        },
        "taxonomy_mapping": {
            "id": "fixture_mapping",
            "content_digest": taxonomy_digest,
            "generation_strategy_policy_digest": digest(
                {"raw_compute": "reauthor_on_asset", "viewer_measurement": "preserve_intent"}
            ),
            "reviewer": "taxonomy-owner",
            "reviewed_at": "2026-08-27T00:00:00Z",
        },
        "total": count,
        "mapping_coverage": {
            "source_leaf_count": 1,
            "mapped_leaf_count": 1,
            "unmapped_source_leaves": [],
        },
        "generation_eligible": False,
        "generation_blockers": ["downstream_locks_pending"],
        "rows": rows,
    }
    manifest = make_envelope(
        kind="vnext_selection_batch_manifest",
        identifier="fixture-compiled-selection",
        status="compiled_pending_downstream_gates",
        body=body,
    )
    path = paths.selection_batches / "fixture.vnext-batch.json"
    atomic_write_json(path, manifest, private=True)
    return path, manifest


_RECEIPT_SCOPES = {
    "mapping": "compiled_selection_batch.mapping",
    "routing": "compiled_selection_batch.routing",
    "config": "compiled_selection_batch.config",
    "seed": "compiled_selection_batch.seed",
    "knowledge": "compiled_selection_batch.knowledge",
    "viewer": "compiled_selection_batch.viewer",
    "ground_truth_registry": "compiled_selection_batch.ground_truth_registry",
}


def _approved(
    receipt_root: Path,
    *,
    label: str,
    lock_kind: str,
    subject_digest: str,
) -> dict[str, object]:
    receipt = {
        "schema_version": 1,
        "kind": "qagen_compiled_selection_batch_lock_approval",
        "id": f"approval-{label}",
        "decision": "approved",
        "scope": _RECEIPT_SCOPES[lock_kind],
        "subject_digest": subject_digest,
        "reviewer": "fixture-reviewer",
        "reviewed_at": "2026-08-27T00:00:00Z",
    }
    relative = Path("receipts") / f"{label}.json"
    path = receipt_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    return {
        "status": "approved",
        "content_digest": subject_digest,
        "approval_digest": digest(receipt),
        "approval_receipt": {"path": relative.as_posix(), "content_digest": digest(receipt)},
    }


def _ground_truth_record(row: dict[str, Any], index: int) -> dict[str, Any]:
    primary = next(asset for asset in row["assets"] if asset["role"] == "primary")
    return {
        "source_type": "expert_annotation",
        "source_id": f"fixture-approved-observation-{index:03d}",
        "source_digest": digest({"source": f"fixture-approved-observation-{index:03d}"}),
        "record_id": f"fixture-approved-record-{index:03d}",
        "record_digest": digest({"record": f"fixture-approved-record-{index:03d}"}),
        "answer_digest": digest({"answer": "fixture-approved-answer"}),
        "allowed_answer_schema": {"shape": "categorical_label", "fields": ["answer"]},
        "anonymous_scene_ref": f"scene://opaque-{index:03d}",
        "primary_asset_digest": f"sha256:{primary['sha256']}",
        "selection_slot_digest": row["slot_ref"]["content_digest"],
    }


def _ground_truth_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "source_type",
            "source_id",
            "source_digest",
            "record_id",
            "record_digest",
            "answer_digest",
            "allowed_answer_schema",
        )
    }


def _ground_truth_registry_lock(receipt_root: Path, manifest: dict[str, Any]) -> dict[str, object]:
    document = {
        "schema_version": 1,
        "kind": "qagen_ground_truth_registry",
        "id": "fixture-compiled-selection-ground-truth",
        "source_status": "approved",
        "record_count": len(manifest["body"]["rows"]),
        "approved_snapshot": {
            "records": [_ground_truth_record(row, index) for index, row in enumerate(manifest["body"]["rows"])]
        },
    }
    relative = Path("ground-truth-registry.json")
    path = receipt_root / relative
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return {
        **_approved(
            receipt_root,
            label="ground-truth-registry",
            lock_kind="ground_truth_registry",
            subject_digest=digest(document),
        ),
        "path": relative.as_posix(),
    }


def _lock_bundle(
    manifest: dict[str, Any],
    *,
    receipt_root: Path,
    raw_strategy: str = "reauthor_on_asset",
    executor_build: dict[str, object] | None = None,
) -> dict[str, Any]:
    roles = [
        {
            "role": role,
            "provider": provider,
            "model": model,
            "prompt_digest": digest({"prompt": role}),
        }
        for role, (provider, model) in ROLE_PROVIDER_MODELS.items()
    ]
    bindings: list[dict[str, Any]] = []
    capabilities = load_compiled_selection_capability_mapping()
    mapping = capabilities["mapping"]
    profile = "molstar_mcp_local_review_v1"
    for index, row in enumerate(manifest["body"]["rows"]):
        source_track = row["access_track"]["source"]
        config_digest = row["target_leaf_config"]["content_sha256"]
        seed_digest = digest({"content": f"seed-{index}"})
        knowledge_digest = digest({"content": f"knowledge-{index}"})
        viewer_digest = digest({"content": f"viewer-{index}"})
        strategy = raw_strategy if source_track == "raw_compute" else "preserve_intent"
        allowed_operations = ["render", "rotate"]
        capability_receipt = {
            "schema_version": 1,
            "kind": "qagen_compiled_selection_viewer_capability_receipt",
            "id": f"capability-{index}",
            "decision": "approved",
            "reviewer": "fixture-viewer-owner",
            "reviewed_at": "2026-08-27T00:00:00Z",
            "mapping_id": mapping["id"],
            "mapping_digest": capabilities["content_digest"],
            "profile_id": profile,
            "profile_digest": capabilities["profile_digests"][profile],
            "slot_digest": row["slot_ref"]["content_digest"],
            "source_access_track": source_track,
            "generation_strategy": strategy,
            "target_leaf": row["target_leaf"],
            "source_viewer": row["viewer_hint"]["source_viewer"],
            "source_hint_digest": digest(row["viewer_hint"]),
            "asset_formats": ["cif"],
            "viewer_family": "molstar",
            "viewer_lock_content_digest": viewer_digest,
            "allowed_operations": allowed_operations,
        }
        capability_relative = Path("receipts") / f"capability-{index}.json"
        capability_path = receipt_root / capability_relative
        capability_path.parent.mkdir(parents=True, exist_ok=True)
        capability_path.write_text(json.dumps(capability_receipt, sort_keys=True), encoding="utf-8")
        bindings.append(
            {
                "slot_digest": row["slot_ref"]["content_digest"],
                "target_leaf": row["target_leaf"],
                "strategy": strategy,
                "source_asset_set_digest": digest(row["assets"]),
                "ground_truth_record": _ground_truth_record_identity(_ground_truth_record(row, index)),
                "config": _approved(
                    receipt_root,
                    label=f"config-{index}",
                    lock_kind="config",
                    subject_digest=config_digest,
                ),
                "seed": _approved(
                    receipt_root,
                    label=f"seed-{index}",
                    lock_kind="seed",
                    subject_digest=seed_digest,
                ),
                "knowledge": _approved(
                    receipt_root,
                    label=f"knowledge-{index}",
                    lock_kind="knowledge",
                    subject_digest=knowledge_digest,
                ),
                "viewer": {
                    **_approved(
                        receipt_root,
                        label=f"viewer-{index}",
                        lock_kind="viewer",
                        subject_digest=viewer_digest,
                    ),
                    "anonymous_scene_ref": f"scene://opaque-{index:03d}",
                    "allowed_operations": allowed_operations,
                    "capability_mapping": {
                        "mapping_id": mapping["id"],
                        "content_digest": capabilities["content_digest"],
                        "profile_id": profile,
                        "profile_digest": capabilities["profile_digests"][profile],
                        "receipt": {
                            "path": capability_relative.as_posix(),
                            "content_digest": digest(capability_receipt),
                        },
                    },
                },
            }
        )
    mapping_digest = manifest["body"]["taxonomy_mapping"]["content_digest"]
    routing_digest = digest({"content": "routing"})
    bundle = {
        "schema_version": 1,
        "kind": "qagen_compiled_selection_batch_locks",
        "selection_batch_digest": manifest["content_digest"],
        "ground_truth_registry": _ground_truth_registry_lock(receipt_root, manifest),
        "mapping": _approved(
            receipt_root,
            label="mapping",
            lock_kind="mapping",
            subject_digest=mapping_digest,
        ),
        "routing": {
            **_approved(
                receipt_root,
                label="routing",
                lock_kind="routing",
                subject_digest=routing_digest,
            ),
            "roles": roles,
        },
        "budget": {"total_max_tokens": 600_000, "total_max_cost_usd": 120.0},
        "retry_policy": {"max_retries_per_stage": 2, "retry_backoff_seconds": 1.0},
        "job_bindings": bindings,
    }
    if executor_build is not None:
        bundle["executor_build"] = executor_build
    return bundle


def _executor_build_lock(paths: WorkspacePaths, manifest: dict[str, Any]) -> dict[str, object]:
    body = {
        "scope": "local_preproduction_fresh5_only",
        "git_head": "a" * 40,
        "source_tree_digest": digest({"fixture": "source-tree"}),
        "source_tree_file_count": 7,
        "git_status_digest": digest({"fixture": "status"}),
        "git_diff_head_digest": digest({"fixture": "diff"}),
        "runtime_config_digest": digest({"fixture": "runtime"}),
        "selection_batch_digest": manifest["content_digest"],
        "base_lock_bundle_digest": digest({"fixture": "base-lock-bundle"}),
        "test_evidence": {"command": "pytest -q", "result": "1 passed"},
        "provider_execution_policy": {
            "live_only_after_no_network_diagnostic": True,
            "release_allowed": False,
        },
    }
    receipt = make_envelope(
        kind="qagen_executor_build_receipt",
        identifier="fixture-executor-build",
        status="attested_local_preproduction",
        body=body,
    )
    path = paths.private / "executor-build-receipts" / "fixture.json"
    atomic_write_json(path, receipt, private=True)
    return {"receipt": {"path": str(path), "content_digest": receipt["content_digest"]}}


def _write_bundle(tmp_path: Path, bundle: dict[str, Any]) -> Path:
    path = tmp_path / "batch-locks.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def _rewrite_envelope(path: Path, value: dict[str, Any]) -> None:
    rewritten = make_envelope(
        kind=value["kind"],
        identifier=value["id"],
        status=value["status"],
        body=value["body"],
        producer=value["producer"],
        created_at=value["created_at"],
    )
    atomic_write_json(path, rewritten, private=True)


def _walk(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [*value.keys(), *[item for nested in value.values() for item in _walk(nested)]]
    if isinstance(value, list):
        return [item for nested in value for item in _walk(nested)]
    if isinstance(value, str):
        return [value]
    return []


def _prepared(tmp_path: Path, *, count: int = 60) -> tuple[WorkspacePaths, dict[str, Any], dict[str, Any]]:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=count)
    bundle_path = _write_bundle(tmp_path, _lock_bundle(manifest, receipt_root=tmp_path))
    result = prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)
    return paths, result, manifest


def _approved_feedback_snapshot(paths: WorkspacePaths, *, leaf_id: str) -> dict[str, Any]:
    policy = {
        "claims": [],
        "question_family_policy": {
            "allowed_families": [{"family_id": "steric-environment", "family_kind": "attribute"}],
            "quota": {
                "scope": "portfolio",
                "unit": "percentage_points",
                "measurement_max": 0,
                "non_measurement_min": 100,
            },
        },
        "gt_policy": {
            "gt_source_types": ["expert_annotation"],
            "expert_approval_required": True,
        },
    }
    snapshot = make_envelope(
        kind="subdomain_knowledge_snapshot",
        identifier=leaf_id,
        status="approved",
        body={
            "id": leaf_id,
            "status": "approved",
            "feedback_policy": policy,
            "provenance": {"feedback_policy_digest": digest(policy)},
        },
    )
    domain, subdomain = leaf_id.split(".", 1)
    atomic_write_yaml(
        paths.knowledge / "compiled" / domain / f"{subdomain}-approved.snapshot.yaml",
        snapshot,
        private=True,
    )
    return snapshot


def test_prepare_creates_a_60_slot_plan_with_isolated_agent_inputs(tmp_path: Path) -> None:
    paths, result, manifest = _prepared(tmp_path)
    plan = load_batch_plan(paths, result["batch_plan_path"])

    assert result["job_count"] == 60
    assert plan["body"]["job_count"] == 60
    ground_truth_registry = plan["body"]["ground_truth_registry"]
    assert ground_truth_registry["content_digest"] == digest(
        json.loads((tmp_path / "ground-truth-registry.json").read_text(encoding="utf-8"))
    )
    assert (paths.root / str(ground_truth_registry["path"])).is_relative_to(paths.root)
    raw_slot = manifest["body"]["rows"][0]["slot_ref"]["content_digest"]
    raw_job = next(job for job in plan["body"]["jobs"] if job["slot_digest"] == raw_slot)
    assert raw_job["generation_strategy"] == "reauthor_on_asset"

    author_values = _walk(raw_job["agent_inputs"]["author"])
    verifier_values = _walk(raw_job["agent_inputs"]["verifier"])
    assert "legacy-task-000" not in author_values
    assert "assets/source/000.nii.gz" not in author_values
    assert "answer_keys" not in author_values
    assert "truth_builder" not in verifier_values
    assert "reference_answer" not in verifier_values
    assert "assets/source/000.nii.gz" not in verifier_values

    references = raw_job["agent_references"]
    assert set(references) == set(ROLE_PROVIDER_MODELS)
    for role, reference in references.items():
        assert reference["role"] == role
        assert reference["output_digest"] is None
        assert reference["tokens"] == {"input": 0, "output": 0}
        assert reference["status"] == "planned"
        assert reference["retry_count"] == 0


def test_prepare_locks_exact_feedback_family_from_approved_snapshot(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    leaf_id = "chemistry.steric_accessibility"
    snapshot = _approved_feedback_snapshot(paths, leaf_id=leaf_id)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    binding = bundle["job_bindings"][0]
    binding["knowledge"] = _approved(
        tmp_path,
        label="knowledge-feedback-0",
        lock_kind="knowledge",
        subject_digest=snapshot["content_digest"],
    )
    bundle_path = _write_bundle(tmp_path, bundle)
    with pytest.raises(GatePendingError, match="question_family_id"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)

    binding["question_family_id"] = "steric-environment"

    prepared = prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=_write_bundle(tmp_path, bundle))
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    job = plan["body"]["jobs"][0]

    assert job["question_family"] == {
        "id": "steric-environment",
        "kind": "attribute_classification",
        "feedback_policy_digest": digest(snapshot["body"]["feedback_policy"]),
    }
    for projection in job["agent_inputs"].values():
        assert projection["question_family"] == {
            **job["question_family"],
            "allowed_gt_source_types": ["expert_annotation"],
        }

    job["question_family"]["id"] = "tampered-family"
    _rewrite_envelope(plan_path, plan)
    with pytest.raises(StaleInputError, match="question family"):
        load_batch_plan(paths, plan_path)


def test_prepare_requires_an_approved_ground_truth_registry_lock(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    del bundle["ground_truth_registry"]
    bundle_path = _write_bundle(tmp_path, bundle)

    with pytest.raises(BatchError, match="ground_truth_registry"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)


def test_prepare_requires_each_binding_to_select_one_ground_truth_record(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    del bundle["job_bindings"][0]["ground_truth_record"]

    with pytest.raises(BatchError, match="ground_truth_record"):
        prepare_batch(
            paths,
            selection_batch_path=manifest_path,
            lock_bundle_path=_write_bundle(tmp_path, bundle),
        )


def test_prepare_rejects_swapping_valid_ground_truth_records_between_slots(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=2)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    first, second = bundle["job_bindings"]
    first["ground_truth_record"], second["ground_truth_record"] = (
        second["ground_truth_record"],
        first["ground_truth_record"],
    )

    with pytest.raises(BatchError, match="belongs to another selected scene"):
        prepare_batch(
            paths,
            selection_batch_path=manifest_path,
            lock_bundle_path=_write_bundle(tmp_path, bundle),
        )


def test_plan_reload_rechecks_the_imported_ground_truth_registry_snapshot(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan = load_batch_plan(paths, prepared["batch_plan_path"])
    registry_path = paths.root / str(plan["body"]["ground_truth_registry"]["path"])
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["approved_snapshot"]["records"][0]["answer_digest"] = digest({"answer": "tampered"})
    registry_path.write_text(json.dumps(registry, sort_keys=True), encoding="utf-8")

    with pytest.raises(StaleInputError, match="ground-truth registry"):
        load_batch_plan(paths, prepared["batch_plan_path"])


def test_plan_reload_rejects_a_tampered_job_ground_truth_record_projection(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    plan["body"]["jobs"][0]["ground_truth_record"]["provenance"]["record_id"] = "forged-record"
    _rewrite_envelope(plan_path, plan)

    with pytest.raises(StaleInputError, match="ground-truth record projection"):
        load_batch_plan(paths, plan_path)


def test_legacy_plan_without_a_ground_truth_registry_stays_readable_until_live_execution(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    del plan["body"]["ground_truth_registry"]
    _rewrite_envelope(plan_path, plan)
    approval = approve_batch_plan(
        paths,
        plan_path=plan_path,
        reviewer="batch-owner",
        decision="approved",
    )
    started = start_batch(paths, plan_path=plan_path, approval_path=approval["batch_approval_path"])

    result = execute_batch(paths, run_id=started["run_id"], dry_run=False, live=True)

    assert result["status"] == "live_blocked_runtime"
    assert load_live_execution(paths, run_id=started["run_id"]) is not None


def test_executor_build_receipt_is_bound_through_plan_approval_and_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    executor_build = _executor_build_lock(paths, manifest)
    bundle_path = _write_bundle(
        tmp_path,
        _lock_bundle(manifest, receipt_root=tmp_path, executor_build=executor_build),
    )
    prepared = prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)
    plan = load_batch_plan(paths, prepared["batch_plan_path"])
    expected_executor_build = {
        "receipt": {
            "path": Path(executor_build["receipt"]["path"]).relative_to(paths.root).as_posix(),
            "content_digest": executor_build["receipt"]["content_digest"],
        }
    }

    assert plan["body"]["executor_build"] == expected_executor_build
    approval = approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="fixture-plan-owner",
        decision="approved",
        rationale="fixture executor build attested",
    )
    approval_document = json.loads(Path(approval["batch_approval_path"]).read_text(encoding="utf-8"))
    assert approval_document["body"]["bound_digests"]["executor_build_digest"] == executor_build["receipt"]["content_digest"]
    run = start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=approval["batch_approval_path"])
    assert load_batch_run(paths, run["run_id"])["body"]["executor_build"] == expected_executor_build

    monkeypatch.setattr(
        batch_registry,
        "_local_executor_source_tree_identity",
        lambda: (digest({"fixture": "source-tree"}), 7),
    )
    assert batch_registry.validate_executor_build_runtime(
        paths,
        plan=plan,
        runtime_config_digest=digest({"fixture": "runtime"}),
    ) == expected_executor_build

    monkeypatch.setattr(batch_registry, "_local_executor_source_tree_identity", lambda: (digest({"fixture": "other"}), 7))
    with pytest.raises(StaleInputError, match="source tree"):
        batch_registry.validate_executor_build_runtime(
            paths,
            plan=plan,
            runtime_config_digest=digest({"fixture": "runtime"}),
        )


def test_raw_compute_cannot_bypass_reauthor_on_asset(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=2)
    bundle_path = _write_bundle(
        tmp_path,
        _lock_bundle(manifest, receipt_root=tmp_path, raw_strategy="preserve_intent"),
    )

    with pytest.raises(GatePendingError, match="reauthor_on_asset"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)


def test_plan_reload_rebinds_source_track_target_and_asset_set_to_manifest_rows(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    job = plan["body"]["jobs"][0]

    job["source_access_track"] = "viewer_measurement"
    _rewrite_envelope(plan_path, plan)

    with pytest.raises(StaleInputError, match="source access track"):
        load_batch_plan(paths, plan_path)


def test_plan_reload_enforces_raw_compute_strategy_after_tampering(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    job = plan["body"]["jobs"][0]

    job["generation_strategy"] = "preserve_intent"
    for role, projection in job["agent_inputs"].items():
        projection["generation_strategy"] = "preserve_intent"
        job["agent_references"][role]["input_digest"] = digest(projection)
    _rewrite_envelope(plan_path, plan)

    with pytest.raises(GatePendingError, match="reauthor_on_asset"):
        load_batch_plan(paths, plan_path)


def test_plan_reload_rejects_a_tampered_source_asset_set_digest(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    plan["body"]["jobs"][0]["source_asset_set_digest"] = digest({"tampered": "asset-set"})
    _rewrite_envelope(plan_path, plan)

    with pytest.raises(StaleInputError, match="source asset set"):
        load_batch_plan(paths, plan_path)


def test_plan_reload_rejects_a_tampered_target_leaf(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    job = plan["body"]["jobs"][0]
    job["target_leaf"] = "chemistry.conformation_analysis"
    for role, projection in job["agent_inputs"].items():
        projection["target_leaf"] = "chemistry.conformation_analysis"
        job["agent_references"][role]["input_digest"] = digest(projection)
    _rewrite_envelope(plan_path, plan)

    with pytest.raises(StaleInputError, match="target leaf"):
        load_batch_plan(paths, plan_path)


def test_lock_bundle_rejects_a_tampered_local_approval_receipt(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    receipt_path = tmp_path / bundle["job_bindings"][0]["seed"]["approval_receipt"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewer"] = "tampered-reviewer"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    bundle_path = _write_bundle(tmp_path, bundle)

    with pytest.raises(StaleInputError, match="approval receipt digest"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)


def test_plan_reload_rechecks_the_persisted_local_approval_receipts(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan_path = Path(prepared["batch_plan_path"])
    plan = load_batch_plan(paths, plan_path)
    receipt_path = Path(plan["body"]["jobs"][0]["lock_bindings"]["knowledge"]["approval_receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["reviewed_at"] = "2026-08-28T00:00:00Z"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(StaleInputError, match="approval receipt digest"):
        load_batch_plan(paths, plan_path)


def test_lock_bundle_rejects_a_fake_receipt_with_the_wrong_scope(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    config = bundle["job_bindings"][0]["config"]
    receipt_path = tmp_path / config["approval_receipt"]["path"]
    fake = json.loads(receipt_path.read_text(encoding="utf-8"))
    fake["scope"] = _RECEIPT_SCOPES["routing"]
    fake_digest = digest(fake)
    receipt_path.write_text(json.dumps(fake), encoding="utf-8")
    config["approval_digest"] = fake_digest
    config["approval_receipt"]["content_digest"] = fake_digest
    bundle_path = _write_bundle(tmp_path, bundle)

    with pytest.raises(BatchError, match="scope"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)


def test_lock_bundle_requires_the_config_lock_to_match_the_manifest_leaf_config(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    bundle["job_bindings"][0]["config"] = _approved(
        tmp_path,
        label="wrong-config",
        lock_kind="config",
        subject_digest=digest({"wrong": "target-config"}),
    )
    bundle_path = _write_bundle(tmp_path, bundle)

    with pytest.raises(StaleInputError, match="target leaf config digest"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)


def test_viewer_lock_must_fit_the_row_hint_and_target_leaf_policy(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    manifest_path, manifest = _selection_manifest(paths, count=1)
    bundle = _lock_bundle(manifest, receipt_root=tmp_path)
    bundle["job_bindings"][0]["viewer"]["allowed_operations"] = ["render", "rotate", "inspect"]
    bundle_path = _write_bundle(tmp_path, bundle)

    with pytest.raises(GatePendingError, match="operations differ"):
        prepare_batch(paths, selection_batch_path=manifest_path, lock_bundle_path=bundle_path)


def test_target_leaf_policy_selects_one_capability_mode_per_route() -> None:
    conformation = _policy_row("chemistry.conformation_analysis")
    preserve = _target_leaf_policy(conformation, generation_strategy="preserve_intent")
    reauthor = _target_leaf_policy(conformation, generation_strategy="reauthor_on_asset")

    assert preserve["capability_mode_id"] == "native_dihedral"
    assert preserve["required_operations"] == {"inspect", "rotate", "measure_dihedral"}
    assert reauthor["capability_mode_id"] == "reauthor_visual_measurement"
    assert reauthor["required_operations"] == {"inspect", "rotate", "measure_distance"}

    medical = _target_leaf_policy(
        _policy_row("medical.volumetric_target_geometry"),
        generation_strategy="reauthor_on_asset",
    )
    assert medical["capability_mode_id"] == "primary_image_localization"
    assert medical["required_operations"] == {
        "inspect",
        "slice",
        "window_level",
    }
    # Medical prompts are primary-image semantic tasks.  The controlled
    # adapter may implement bbox/overlay for other internal workflows, but
    # the leaf policy must never grant those solver-facing shortcuts.
    assert "bbox" not in medical["required_operations"]
    assert "overlay_compare" not in medical["required_operations"]
    assert "measure_area" not in medical["required_operations"]
    assert "measure_volume" not in medical["required_operations"]


def test_batch_plan_has_one_canonical_terminal_approval_decision(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="rejected",
        rationale="The first terminal decision rejects this plan.",
    )

    with pytest.raises(InvalidStateError, match="immutable approval decision"):
        approve_batch_plan(
            paths,
            plan_path=prepared["batch_plan_path"],
            reviewer="batch-owner",
            decision="approved",
        )


def test_start_batch_rejects_a_forged_second_approval_receipt(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="rejected",
        rationale="The canonical terminal decision rejects this plan.",
    )
    plan = load_batch_plan(paths, prepared["batch_plan_path"])
    body = plan["body"]
    forged = make_decision(
        kind="compiled_selection_batch_approval",
        identifier="forged-batch-approval",
        decision="approved",
        actor_id="forged-owner",
        actor_role="plan_owner",
        bound_digests={
            "plan_digest": plan["content_digest"],
            "selection_batch_digest": body["selection_batch"]["content_digest"],
                "mapping_digest": body["mapping"]["content_digest"],
                "lock_bundle_digest": body["lock_bundle_digest"],
                "ground_truth_registry_digest": body["ground_truth_registry"]["content_digest"],
                "ground_truth_record_projections_digest": batch_registry._ground_truth_record_projections_digest(body),
            },
        extra={"job_count": body["job_count"]},
    )
    forged_path = paths.selection_batches / "compiled-runs" / "approvals" / "forged.approval.json"
    atomic_write_json(forged_path, forged, private=True)

    with pytest.raises(InvalidStateError, match="canonical approval decision"):
        start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=forged_path)


def test_batch_approval_binds_and_rechecks_job_ground_truth_record_projections(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    plan = load_batch_plan(paths, prepared["batch_plan_path"])
    expected_digest = batch_registry._ground_truth_record_projections_digest(plan["body"])
    assert expected_digest is not None

    approved = approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="approved",
    )
    receipt = json.loads(Path(approved["batch_approval_path"]).read_text(encoding="utf-8"))
    assert receipt["body"]["bound_digests"]["ground_truth_record_projections_digest"] == expected_digest

    forged_bindings = dict(receipt["body"]["bound_digests"])
    forged_bindings["ground_truth_record_projections_digest"] = digest({"forged": "ground-truth-projections"})
    forged = make_decision(
        kind="compiled_selection_batch_approval",
        identifier="forged-ground-truth-projection-approval",
        decision="approved",
        actor_id="forged-owner",
        actor_role="plan_owner",
        bound_digests=forged_bindings,
        extra={"job_count": plan["body"]["job_count"]},
    )
    forged_path = paths.selection_batches / "compiled-runs" / "approvals" / "forged-ground-truth.approval.json"
    atomic_write_json(forged_path, forged, private=True)

    with pytest.raises(StaleInputError, match="approval is bound to different inputs"):
        start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=forged_path)


def test_start_dry_run_and_resume_do_not_fabricate_candidates(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=3)
    approval = approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="approved",
    )
    started = start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=approval["batch_approval_path"])

    result = execute_batch(paths, run_id=started["run_id"], dry_run=True)
    assert result["status"] == "diagnostic_blocked"
    assert result["network_called"] is False
    assert result["generated_candidate_count"] == 0
    assert result["generation_supported"] is False
    assert result["generation_support_reason"] == "no_live_worker_executor_is_connected_to_compiled_selection_batch"
    assert result["job_statuses"] == {"blocked_runtime": 3}

    run = load_batch_run(paths, started["run_id"])
    assert run["body"]["usage"] == {"settled_tokens": 0, "settled_cost_usd": 0.0}
    assert all(job["status"] == "blocked_runtime" for job in run["body"]["jobs"])
    assert all(
        reference["output_digest"] is None
        and reference["tokens"] == {"input": 0, "output": 0}
        and reference["status"] == "blocked_runtime"
        for job in run["body"]["jobs"]
        for reference in job["agent_references"].values()
    )
    assert not (Path(started["run_path"]) / "private" / "candidates").exists()

    resumed = resume_batch(paths, run_id=started["run_id"])
    assert resumed["resume_count"] == 1
    assert resumed["generated_candidate_count"] == 0
    assert resumed["generation_supported"] is False


def test_live_execution_without_runtime_fails_closed_before_any_candidate_is_written(tmp_path: Path) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    approval = approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="approved",
    )
    started = start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=approval["batch_approval_path"])

    result = execute_batch(paths, run_id=started["run_id"], dry_run=False, live=True)
    assert result["status"] == "live_blocked_runtime"
    assert result["network_called"] is False
    assert result["generated_candidate_count"] == 0
    assert result["stage_eligible_candidate_count"] == 0
    state = load_live_execution(paths, run_id=started["run_id"])
    assert state is not None
    assert state["body"]["runtime"]["status"] == "blocked"
    assert all(job["status"] == "blocked_runtime" for job in state["body"]["jobs"])
    assert not (Path(started["run_path"]) / "private" / "candidates").exists()


def test_live_routing_policy_resolves_the_v32_config_from_the_batch_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    v32_routing = PACKAGE_ROOT / "configs" / "agents" / "model_routing.v32-local-preproduction.yaml"
    v32_runtime = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml"
    artifact = make_envelope(
        kind="qagen_local_preproduction_routing",
        identifier="fixture-v32-routing",
        status="prepared",
        body={
            "scope": "local_preproduction_only",
            "selection_batch_digest": digest({"selection": "fixture"}),
            "source_routing_digest": hash_file(v32_routing),
            "source_runtime_digest": hash_file(v32_runtime),
            "agent_references": [],
            "credential_transport": "external approved local-pilot runtime required",
            "production_eligible": False,
            "release_eligible": False,
        },
    )
    artifact_path = paths.root / "preproduction" / "v32" / "routing.json"
    atomic_write_json(artifact_path, artifact, private=True)
    plan = {
        "body": {
            "routing": {
                "content_digest": artifact["content_digest"],
                "roles": [
                    {
                        "role": role,
                        "provider": provider,
                        "model": model,
                        "prompt_digest": digest({"prompt": role}),
                    }
                    for role, (provider, model) in ROLE_PROVIDER_MODELS.items()
                ],
            }
        }
    }
    selected: list[Path] = []
    original = live_module.load_verified_routing_policy

    def tracked(path: str | Path, **kwargs: Any):
        selected.append(Path(path).resolve())
        return original(path, **kwargs)

    monkeypatch.setattr(live_module, "load_verified_routing_policy", tracked)

    policy = live_module._routing_policy(paths, plan)

    assert selected == [v32_routing.resolve()]
    assert policy.role("author")["allowed_models"] == ["deepseek-v4-pro"]


def test_live_routing_lock_allows_identical_preproduction_artifact_copies(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    body = {
        "scope": "local_preproduction_only",
        "selection_batch_digest": digest({"selection": "fixture"}),
        "source_routing_digest": digest({"routing": "fixture"}),
        "source_runtime_digest": digest({"runtime": "fixture"}),
        "agent_references": [],
        "credential_transport": "external approved local-pilot runtime required",
        "production_eligible": False,
        "release_eligible": False,
    }
    artifact = make_envelope(
        kind="qagen_local_preproduction_routing",
        identifier="fixture-v32-routing-copy",
        status="prepared",
        body=body,
    )
    for name in ("fresh-a", "fresh-b"):
        atomic_write_json(paths.root / "preproduction" / name / "routing.json", artifact, private=True)

    selected = live_module._locked_routing_artifact(
        paths,
        routing={"content_digest": artifact["content_digest"]},
    )

    assert selected == body


def test_live_selected_jobs_update_the_persisted_state_object() -> None:
    body = {"jobs": [{"job_id": "job-1", "status": "blocked_runtime"}]}

    selected = live_module._select_jobs(body, limit=1)
    selected[0]["status"] = "queued"

    assert body["jobs"][0]["status"] == "queued"


def test_cli_batch_dry_run_returns_pending_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    approval = approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="approved",
    )
    started = start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=approval["batch_approval_path"])

    assert (
        main(
            ["selection", "batch", "execute", "--workspace", str(paths.root), "--run-id", started["run_id"], "--dry-run"]
        )
        == 3
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["generated_candidate_count"] == 0
    assert payload["network_called"] is False
    assert payload["generation_supported"] is False


def test_cli_batch_execute_defaults_to_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths, prepared, _manifest = _prepared(tmp_path, count=1)
    approval = approve_batch_plan(
        paths,
        plan_path=prepared["batch_plan_path"],
        reviewer="batch-owner",
        decision="approved",
    )
    started = start_batch(paths, plan_path=prepared["batch_plan_path"], approval_path=approval["batch_approval_path"])

    assert main(["selection", "batch", "execute", "--workspace", str(paths.root), "--run-id", started["run_id"]]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["network_called"] is False
