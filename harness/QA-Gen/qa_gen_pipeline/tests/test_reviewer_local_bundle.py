from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from qagen.contracts import digest, digest_bytes, make_envelope
from qagen.reviewer_bundle import (
    ReviewerBundleError,
    ReviewerBundleServerError,
    build_reviewer_bundle,
    create_reviewer_bundle_server,
    verify_reviewer_bundle,
)
from qagen.viewer.capability_mapping import load_compiled_selection_capability_mapping


def _write_json(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path.write_bytes(content)
    return content


def _rewrite_envelope(path: Path, *, body: dict, status: str | None = None) -> dict:
    previous = json.loads(path.read_text(encoding="utf-8"))
    envelope = make_envelope(
        kind=previous["kind"],
        identifier=previous["id"],
        status=previous["status"] if status is None else status,
        body=body,
        producer=previous["producer"],
        created_at=previous["created_at"],
    )
    _write_json(path, envelope)
    return envelope


def _refresh_checksums(root: Path) -> None:
    entries = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file() and path.name != "SHA256SUMS":
            entries[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    (root / "SHA256SUMS").write_text(
        "".join(f"{checksum}  {relative}\n" for relative, checksum in sorted(entries.items())),
        encoding="utf-8",
    )


def _descriptor(category: str, filename: str, value: dict, content: bytes) -> dict:
    return {
        "relative_path": f"{category}/{filename}",
        "canonical_sha256": digest(value),
        "file_sha256": digest_bytes(content),
        "size_bytes": len(content),
    }


def _runtime(root: Path) -> Path:
    runtime = root / "viewer"
    paths = {
        "shared/vendor/molstar/4.5.0/build/viewer/molstar.js": (
            "window.molstar = { Viewer: {} }; CameraControls loadStructureFromUrl "
            "StructureSelectionsDistance3D StructureSelectionsDihedral3D\n"
        ),
        "shared/vendor/molstar/4.5.0/build/viewer/molstar.css": "#viewer { display: block; }\n",
        "shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js": (
            "window.niivue = {}; renderAzimuth setScale setSliceType contrast sliceTypeRender\n"
        ),
    }
    for relative, content in paths.items():
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return runtime


def _asset(root: Path, relative: str, content: bytes) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": relative,
        "bytes": len(content),
        "sha256": sha256(content).hexdigest(),
    }


def _batch(root: Path, assets: dict[str, dict], *, include_mask_metadata: bool = True) -> Path:
    mask = {"role": "mask", **assets["mask"]}
    if include_mask_metadata:
        mask.update({"mask_selection": "label_index", "mask_id": 3})
    body = {
        "source_selection": {
            "selection_id_sha256": "a" * 64,
            "selection_file_sha256": "sha256:" + "b" * 64,
            "import_digest": "sha256:" + "c" * 64,
        },
        "taxonomy_mapping": {"id": "fixture-map", "content_digest": "sha256:" + "d" * 64, "reviewer": "reviewer", "reviewed_at": "2026-08-27T00:00:00Z"},
        "total": 2,
        "mapping_coverage": {"source_leaf_count": 2, "mapped_leaf_count": 2, "unmapped_source_leaves": []},
        "generation_eligible": False,
        "generation_blockers": ["local_review_only"],
        "rows": [
            {
                "slot_ref": {"id": "selection-slot-mol", "content_digest": "sha256:" + "1" * 64},
                "question_id": "question-mol",
                "task_id": "task-mol",
                "source_leaf": "biology.molecular_contact_geometry",
                "target_leaf": "biology.molecular_collision_check",
                "mapping_status": "mapped",
                "access_track": {"source": "viewer_measurement", "effective": "viewer_measurement", "conversion": "preserved"},
                "viewer_hint": {"source_viewer": "molstar_deployed", "allowed_operations": ["viewer_rotate", "viewer_zoom", "viewer_measure_distance"]},
                "assets": [{"role": "primary", **assets["mol"]}],
                "generation_status": "blocked_pending_viewer_knowledge_plan_and_review",
                "target_leaf_config": {"path": "configs/subdomains/biology/molecular_collision_check.yaml", "content_sha256": "f" * 64},
            },
            {
                "slot_ref": {"id": "selection-slot-med", "content_digest": "sha256:" + "2" * 64},
                "question_id": "question-med",
                "task_id": "task-med",
                "source_leaf": "medical.med_ct_geometry",
                "target_leaf": "medical.volumetric_target_geometry",
                "mapping_status": "mapped",
                "access_track": {"source": "viewer_measurement", "effective": "viewer_measurement", "conversion": "preserved"},
                "viewer_hint": {"source_viewer": "niivue_deployed", "allowed_operations": ["viewer_rotate", "viewer_slice", "viewer_window_level"]},
                "assets": [{"role": "primary", **assets["volume"]}, mask],
                "generation_status": "blocked_pending_viewer_knowledge_plan_and_review",
                "target_leaf_config": {"path": "configs/subdomains/medical/volumetric_target_geometry.yaml", "content_sha256": "e" * 64},
            },
        ],
    }
    envelope = make_envelope(
        kind="vnext_selection_batch_manifest",
        identifier="fixture-batch",
        status="compiled_pending_downstream_gates",
        body=body,
    )
    path = root / "batch.json"
    _write_json(path, envelope)
    return path


def _asset_formats(row: dict) -> list[str]:
    formats = set()
    for asset in row["assets"]:
        path = asset["path"].lower()
        formats.add("nii.gz" if path.endswith(".nii.gz") else path.rsplit(".", 1)[1])
    return sorted(formats)


def _viewer_capability_mapping(
    root: Path,
    *,
    row: dict,
    viewer_lock: dict,
    profile_id: str,
) -> dict:
    loaded = load_compiled_selection_capability_mapping()
    profile = loaded["profiles"][profile_id]
    operations = sorted(viewer_lock["allowed_operations"])
    receipt = {
        "schema_version": 1,
        "kind": "qagen_compiled_selection_viewer_capability_receipt",
        "id": f"viewer-capability-{row['task_id']}",
        "decision": "approved",
        "reviewer": "viewer-owner",
        "reviewed_at": "2026-08-27T00:00:00Z",
        "mapping_id": loaded["mapping"]["id"],
        "mapping_digest": loaded["content_digest"],
        "profile_id": profile_id,
        "profile_digest": loaded["profile_digests"][profile_id],
        "slot_digest": row["slot_ref"]["content_digest"],
        "source_access_track": row["access_track"]["source"],
        "generation_strategy": "preserve_intent",
        "target_leaf": row["target_leaf"],
        "source_viewer": row["viewer_hint"]["source_viewer"],
        "source_hint_digest": digest(row["viewer_hint"]),
        "asset_formats": _asset_formats(row),
        "viewer_family": profile["viewer_family"],
        "viewer_lock_content_digest": viewer_lock["content_digest"],
        "allowed_operations": operations,
    }
    receipt_path = root / "receipts" / f"{row['task_id']}.viewer-capability.json"
    _write_json(receipt_path, receipt)
    return {
        "mapping_id": loaded["mapping"]["id"],
        "content_digest": loaded["content_digest"],
        "profile_id": profile_id,
        "profile_digest": loaded["profile_digests"][profile_id],
        "receipt": {"path": str(receipt_path), "content_digest": digest(receipt)},
    }


def _batch_plan(root: Path, batch_path: Path) -> Path:
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    operations_by_task = {
        "task-mol": ["rotate", "zoom", "measure_distance", "render"],
        "task-med": ["rotate", "slice", "window_level", "render"],
    }
    jobs: list[dict] = []
    for ordinal, row in enumerate(batch["body"]["rows"], start=1):
        task_id = row["task_id"]
        slot_digest = row["slot_ref"]["content_digest"]
        operations = operations_by_task[task_id]
        viewer_lock = {
            "status": "approved",
            "content_digest": "sha256:" + f"{ordinal:x}" * 64,
            "approval_digest": "sha256:" + f"{ordinal + 2:x}" * 64,
            "approval_receipt": {
                "path": f"viewer-{ordinal}.approval.json",
                "content_digest": "sha256:" + f"{ordinal + 4:x}" * 64,
            },
            "anonymous_scene_ref": f"scene://fixture-{ordinal}",
            "allowed_operations": operations,
        }
        profile_id = "molstar_mcp_local_review_v1" if task_id == "task-mol" else "niivue_mcp_local_review_v1"
        viewer_lock["capability_mapping"] = _viewer_capability_mapping(
            root,
            row=row,
            viewer_lock=viewer_lock,
            profile_id=profile_id,
        )
        jobs.append(
            {
                "job_id": f"job-{ordinal:03d}",
                "ordinal": ordinal,
                "slot_digest": slot_digest,
                "source_access_track": row["access_track"]["source"],
                "target_leaf": row["target_leaf"],
                "generation_strategy": "preserve_intent",
                "source_asset_set_digest": digest(row["assets"]),
                "lock_bindings": {
                    "config": {},
                    "seed": {},
                    "knowledge": {},
                    "viewer": viewer_lock,
                },
                "agent_inputs": {},
                "agent_references": {},
                "status": "queued",
            }
        )
    body = {
        "selection_batch": {"path": "batch.json", "content_digest": batch["content_digest"]},
        "lock_bundle_digest": "sha256:" + "a" * 64,
        "mapping": {},
        "routing": {},
        "budget": {},
        "retry_policy": {},
        "job_count": len(jobs),
        "jobs": jobs,
    }
    plan = make_envelope(
        kind="compiled_selection_batch_plan",
        identifier="fixture-batch-plan",
        status="prepared_pending_human_approval",
        body=body,
    )
    path = root / "batch-plan.json"
    _write_json(path, plan)
    return path


def _solver(task_id: str, operations: list[str], *, viewer_profile_digest: str) -> dict:
    return {
        "artifact_kind": "solver_safe_export",
        "task_id": task_id,
        "prompt": f"Inspect the controlled scene for {task_id}.",
        "anonymous_scene_ref": f"old_scene_{task_id}",
        "viewer_session_request": {
            "request_id": f"request-{task_id}",
            "viewer_profile_digest": viewer_profile_digest,
            "allowed_operations": operations,
        },
    }


def _staging(
    root: Path,
    *,
    batch_path: Path,
    batch_plan_path: Path,
    task_ids: tuple[str, ...] = ("task-mol", "task-med"),
    legacy_answer_keys: bool = False,
) -> Path:
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch_plan = json.loads(batch_plan_path.read_text(encoding="utf-8"))
    rows_by_task = {row["task_id"]: row for row in batch["body"]["rows"]}
    jobs_by_slot = {job["slot_digest"]: job for job in batch_plan["body"]["jobs"]}
    viewer_profile_digest = "sha256:" + "e" * 64
    lock_digests = {"fixture": "sha256:" + "f" * 64}
    staged: list[dict] = []
    for index, task_id in enumerate(task_ids):
        row = rows_by_task[task_id]
        job = jobs_by_slot[row["slot_ref"]["content_digest"]]
        operations = job["lock_bindings"]["viewer"]["allowed_operations"]
        solver = _solver(task_id, operations, viewer_profile_digest=viewer_profile_digest)
        if legacy_answer_keys:
            solver["answer_keys"] = ["bad"]
        candidate_digest = "sha256:" + f"{index + 3:x}" * 64
        review_digest = "sha256:" + f"{index + 5:x}" * 64
        scorer = {
            "artifact_kind": "scorer_private_input",
            "candidate_digest": candidate_digest,
            "review_digest": review_digest,
            "reference_answer": {"answer": f"private-{task_id}"},
        }
        audit = {
            "artifact_kind": "release_audit_record",
            "candidate_digest": candidate_digest,
            "review_digest": review_digest,
        }
        solver_file = f"task-{index}.json"
        scorer_file = f"scorer-{index}.json"
        audit_file = f"audit-{index}.json"
        solver_content = _write_json(root / "solver" / solver_file, solver)
        scorer_content = _write_json(root / "scorer" / scorer_file, scorer)
        _write_json(root / "audit" / audit_file, audit)
        staged.append(
            {
                "candidate_digest": candidate_digest,
                "evidence_digest": "sha256:" + "4" * 64,
                "review_digest": review_digest,
                "batch_job_id": job["job_id"],
                "selection_slot_digest": row["slot_ref"]["content_digest"],
                "work_item_id": f"work-{task_id}",
                "execution_manifest_digest": "sha256:" + "6" * 64,
                "worker_result_digest": "sha256:" + "7" * 64,
                "author_output_digest": candidate_digest,
                "evidence_output_digest": "sha256:" + "8" * 64,
                "verifier_output_digest": "sha256:" + "9" * 64,
                "stage_digests_digest": "sha256:" + "a" * 64,
                "rubric_dry_run_digest": "sha256:" + "b" * 64,
                "solver_file": solver_file,
                "scorer_file": scorer_file,
                "audit_file": audit_file,
                "solver_artifact": _descriptor("solver", solver_file, solver, solver_content),
                "scorer_artifact": _descriptor("scorer", scorer_file, scorer, scorer_content),
            }
        )
    body = {
        "run_id": "real-run",
        "plan_digest": "sha256:" + "c" * 64,
        "config_digest": "sha256:" + "d" * 64,
        "viewer_profile_digest": viewer_profile_digest,
        "lock_digests": lock_digests,
        "fixture_only": False,
        "selection_binding": {
            "selection_batch_digest": batch["content_digest"],
            "batch_plan_digest": batch_plan["content_digest"],
            "batch_plan_lock_bundle_digest": batch_plan["body"]["lock_bundle_digest"],
            "run_id": "real-run",
            "plan_digest": "sha256:" + "c" * 64,
            "config_digest": "sha256:" + "d" * 64,
            "viewer_profile_digest": viewer_profile_digest,
            "lock_digests_digest": digest(lock_digests),
        },
        "staged": staged,
    }
    manifest = make_envelope(kind="release_staging_manifest", identifier="release-staging-real-run", status="staged_not_exported", body=body)
    _write_json(root / "manifest.json", manifest)
    return root


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    asset_root = tmp_path / "asset-root"
    assets = {
        "mol": _asset(asset_root, "assets/biology/source.cif", b"data_fixture\n#\n"),
        "volume": _asset(asset_root, "assets/medical/ct.nii.gz", b"fake-nifti-gzip"),
        "mask": _asset(asset_root, "assets/medical/mask.nii.gz", b"fake-mask-gzip"),
    }
    batch = _batch(tmp_path, assets)
    batch_plan = _batch_plan(tmp_path, batch)
    staging = _staging(tmp_path / "staging", batch_path=batch, batch_plan_path=batch_plan)
    runtime = _runtime(tmp_path)
    return batch, batch_plan, staging, asset_root, runtime


def test_builds_verified_bundle_with_only_anonymous_public_scene_routes(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    output = tmp_path / "reviewer-bundle"

    result = build_reviewer_bundle(
        batch_manifest_path=batch,
        batch_plan_path=batch_plan,
        staging_root=staging,
        asset_root=asset_root,
        output_dir=output,
        viewer_runtime_root=runtime,
    )

    assert result["status"] == "built_local_review_only"
    assert result["task_count"] == result["scene_count"] == 2
    verified = verify_reviewer_bundle(output)
    assert verified["task_count"] == 2
    registry = json.loads((output / "scene-registry.json").read_text(encoding="utf-8"))
    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (output / "public").rglob("*")
        if path.is_file()
    ) + (output / "scene-registry.json").read_text(encoding="utf-8")
    assert "assets/biology/source.cif" not in public_text
    assert "assets/medical/ct.nii.gz" not in public_text
    assert "private-task" not in public_text
    assert "maskBounds" not in (output / "public" / "app.js").read_text(encoding="utf-8")
    assert all(scene["scene_ref"].startswith("scene_") for scene in registry["scenes"])
    assert (output / "private" / "scorer" / "scorer-0.json").is_file()
    assert "private-task-mol" in (output / "private" / "scorer" / "scorer-0.json").read_text(encoding="utf-8")
    assert (output / "SHA256SUMS").is_file()


def test_loopback_server_exposes_only_explicit_public_routes(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    output = tmp_path / "reviewer-bundle"
    build_reviewer_bundle(
        batch_manifest_path=batch,
        batch_plan_path=batch_plan,
        staging_root=staging,
        asset_root=asset_root,
        output_dir=output,
        viewer_runtime_root=runtime,
    )
    server = create_reviewer_bundle_server(output)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    try:
        assert host == "127.0.0.1"
        with urlopen(f"{base}/api/tasks", timeout=5) as response:
            tasks = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{base}/api/scene-registry", timeout=5) as response:
            registry = json.loads(response.read().decode("utf-8"))
        scene = registry["scenes"][0]
        with urlopen(f"{base}{scene['primary_url']}", timeout=5) as response:
            assert response.status == 200
        assert tasks["tasks"][0]["anonymous_scene_ref"] == scene["scene_ref"]
        for forbidden in ("/private/scorer/scorer-0.json", "/SHA256SUMS", "/BUNDLE.json", "/assets/biology/source.cif", "/audit/audit-0.json"):
            with pytest.raises(HTTPError) as error:
                urlopen(f"{base}{forbidden}", timeout=5)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_rejects_legacy_answer_keys_before_any_public_projection(tmp_path: Path) -> None:
    batch, batch_plan, _, asset_root, runtime = _inputs(tmp_path)
    staging = _staging(
        tmp_path / "legacy-staging",
        batch_path=batch,
        batch_plan_path=batch_plan,
        legacy_answer_keys=True,
    )

    with pytest.raises(ReviewerBundleError, match="answer_keys|unsafe"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_medical_mask_requires_label_binding_in_batch_manifest(tmp_path: Path) -> None:
    asset_root = tmp_path / "asset-root"
    assets = {
        "mol": _asset(asset_root, "assets/biology/source.cif", b"data_fixture\n#\n"),
        "volume": _asset(asset_root, "assets/medical/ct.nii.gz", b"fake-nifti-gzip"),
        "mask": _asset(asset_root, "assets/medical/mask.nii.gz", b"fake-mask-gzip"),
    }
    batch = _batch(tmp_path, assets, include_mask_metadata=False)
    batch_plan = _batch_plan(tmp_path, batch)
    staging = _staging(tmp_path / "staging", batch_path=batch, batch_plan_path=batch_plan)

    with pytest.raises(ReviewerBundleError, match="explicit label_index"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=_runtime(tmp_path),
        )


def test_default_requires_complete_selection_but_partial_mode_is_explicit(tmp_path: Path) -> None:
    batch, batch_plan, _, asset_root, runtime = _inputs(tmp_path)
    staging = _staging(
        tmp_path / "partial-staging",
        batch_path=batch,
        batch_plan_path=batch_plan,
        task_ids=("task-mol",),
    )
    output = tmp_path / "reviewer-bundle"

    with pytest.raises(ReviewerBundleError, match="incomplete"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=output,
            viewer_runtime_root=runtime,
        )
    result = build_reviewer_bundle(
        batch_manifest_path=batch,
        batch_plan_path=batch_plan,
        staging_root=staging,
        asset_root=asset_root,
        output_dir=output,
        viewer_runtime_root=runtime,
        allow_partial=True,
    )
    assert result["partial"] is True
    assert result["task_count"] == 1


def test_rejects_staging_manifest_with_a_non_reviewer_status(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    manifest_path = staging / "manifest.json"
    body = json.loads(manifest_path.read_text(encoding="utf-8"))["body"]
    _rewrite_envelope(manifest_path, body=body, status="fixture_staged_no_production_export")

    with pytest.raises(ReviewerBundleError, match="staged_not_exported"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


@pytest.mark.parametrize(
    "field,replacement,match",
    [
        ("selection_batch_digest", "sha256:" + "0" * 64, "another selection batch"),
        ("batch_plan_digest", "sha256:" + "0" * 64, "another selection batch plan"),
        ("batch_plan_lock_bundle_digest", "sha256:" + "0" * 64, "another batch plan lock bundle"),
        ("run_id", "another-run", "staging selection binding"),
        ("plan_digest", "sha256:" + "0" * 64, "staging selection binding"),
        ("config_digest", "sha256:" + "0" * 64, "staging selection binding"),
        ("viewer_profile_digest", "sha256:" + "0" * 64, "staging selection binding"),
        ("lock_digests_digest", "sha256:" + "0" * 64, "staging selection binding"),
    ],
)
def test_rejects_staging_selection_binding_tampering(
    tmp_path: Path,
    field: str,
    replacement: str,
    match: str,
) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    manifest_path = staging / "manifest.json"
    body = json.loads(manifest_path.read_text(encoding="utf-8"))["body"]
    body["selection_binding"][field] = replacement
    _rewrite_envelope(manifest_path, body=body)

    with pytest.raises(ReviewerBundleError, match=match):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_rejects_staging_slot_job_misbinding_even_when_task_id_matches(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    manifest_path = staging / "manifest.json"
    body = json.loads(manifest_path.read_text(encoding="utf-8"))["body"]
    body["staged"][0]["selection_slot_digest"] = body["staged"][1]["selection_slot_digest"]
    _rewrite_envelope(manifest_path, body=body)

    with pytest.raises(ReviewerBundleError, match="mismatched selection slot or batch plan job"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_rejects_review_receipt_tampering_in_private_staging_artifacts(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    audit_path = staging / "audit" / "audit-0.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["review_digest"] = "sha256:" + "0" * 64
    _write_json(audit_path, audit)

    with pytest.raises(ReviewerBundleError, match="review receipt differs"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_rejects_molstar_dihedral_without_a_controlled_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch, batch_plan, _, asset_root, runtime = _inputs(tmp_path)
    batch_value = json.loads(batch.read_text(encoding="utf-8"))
    mol_row = batch_value["body"]["rows"][0]
    mol_row["viewer_hint"]["allowed_operations"] = [
        "viewer_rotate",
        "viewer_zoom",
        "viewer_measure_dihedral",
    ]
    rewritten_batch = _rewrite_envelope(batch, body=batch_value["body"])

    # Keep the source hint and its receipt binding valid.  The test must reach
    # the controlled-capability gate rather than relying on stale metadata.
    limited = deepcopy(load_compiled_selection_capability_mapping())
    profile_id = "molstar_mcp_local_review_v1"
    profile = limited["profiles"][profile_id]
    profile["controlled_operations"] = [
        operation for operation in profile["controlled_operations"] if operation != "measure_dihedral"
    ]
    profile["native_measurement_operations"] = [
        operation for operation in profile["native_measurement_operations"] if operation != "measure_dihedral"
    ]
    for raw_profile in limited["mapping"]["profiles"]:
        if raw_profile["id"] == profile_id:
            raw_profile["controlled_operations"] = list(profile["controlled_operations"])
            raw_profile["native_measurement_operations"] = list(profile["native_measurement_operations"])
    limited["profile_digests"][profile_id] = digest(profile)
    limited["content_digest"] = digest(limited["mapping"])
    monkeypatch.setattr("qagen.reviewer_bundle.bundle.load_compiled_selection_capability_mapping", lambda: limited)

    plan_value = json.loads(batch_plan.read_text(encoding="utf-8"))
    plan_value["body"]["selection_batch"]["content_digest"] = rewritten_batch["content_digest"]
    viewer_lock = plan_value["body"]["jobs"][0]["lock_bindings"]["viewer"]
    viewer_lock["allowed_operations"] = [
        "rotate",
        "zoom",
        "measure_dihedral",
        "render",
    ]
    capability = viewer_lock["capability_mapping"]
    capability["content_digest"] = limited["content_digest"]
    capability["profile_digest"] = limited["profile_digests"][profile_id]
    receipt_path = Path(capability["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.update(
        {
            "mapping_digest": limited["content_digest"],
            "profile_digest": limited["profile_digests"][profile_id],
            "source_hint_digest": digest(mol_row["viewer_hint"]),
            "allowed_operations": sorted(viewer_lock["allowed_operations"]),
        }
    )
    _write_json(receipt_path, receipt)
    capability["receipt"]["content_digest"] = digest(receipt)
    _rewrite_envelope(batch_plan, body=plan_value["body"])
    staging = _staging(tmp_path / "dihedral-staging", batch_path=batch, batch_plan_path=batch_plan)
    with pytest.raises(ReviewerBundleError, match="does not control operation: measure_dihedral"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_rejects_an_advertised_operation_absent_from_the_copied_viewer_runtime(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    molstar_runtime = runtime / "shared/vendor/molstar/4.5.0/build/viewer/molstar.js"
    molstar_runtime.write_text("CameraControls loadStructureFromUrl StructureSelectionsDihedral3D\n", encoding="utf-8")

    with pytest.raises(ReviewerBundleError, match="cannot attest required operation: measure_distance"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_rejects_batch_plan_operation_the_niivue_bridge_cannot_serve(tmp_path: Path) -> None:
    batch, batch_plan, _, asset_root, runtime = _inputs(tmp_path)
    batch_value = json.loads(batch.read_text(encoding="utf-8"))
    batch_value["body"]["rows"][1]["viewer_hint"]["allowed_operations"].append("viewer_measure_area")
    rewritten_batch = _rewrite_envelope(batch, body=batch_value["body"])
    plan_value = json.loads(batch_plan.read_text(encoding="utf-8"))
    plan_value["body"]["selection_batch"]["content_digest"] = rewritten_batch["content_digest"]
    plan_value["body"]["jobs"][1]["lock_bindings"]["viewer"]["allowed_operations"] = [
        "rotate",
        "slice",
        "window_level",
        "measure_area",
        "render",
    ]
    _rewrite_envelope(batch_plan, body=plan_value["body"])
    staging = _staging(tmp_path / "unsupported-capability-staging", batch_path=batch, batch_plan_path=batch_plan)

    with pytest.raises(ReviewerBundleError, match="does not control operation: measure_area"):
        build_reviewer_bundle(
            batch_manifest_path=batch,
            batch_plan_path=batch_plan,
            staging_root=staging,
            asset_root=asset_root,
            output_dir=tmp_path / "reviewer-bundle",
            viewer_runtime_root=runtime,
        )


def test_verify_detects_tampering_and_server_rejects_non_loopback(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    output = tmp_path / "reviewer-bundle"
    build_reviewer_bundle(
        batch_manifest_path=batch,
        batch_plan_path=batch_plan,
        staging_root=staging,
        asset_root=asset_root,
        output_dir=output,
        viewer_runtime_root=runtime,
    )
    with pytest.raises(ReviewerBundleServerError, match="127.0.0.1"):
        create_reviewer_bundle_server(output, host="0.0.0.0")
    scene = json.loads((output / "scene-registry.json").read_text(encoding="utf-8"))["scenes"][0]
    asset = output / "scenes" / scene["scene_ref"] / f"primary.{scene['format']}"
    asset.write_bytes(b"tampered")
    with pytest.raises(ReviewerBundleError, match="checksum mismatch"):
        verify_reviewer_bundle(output)


def test_verify_rejects_private_scorer_and_audit_receipt_misbinding(tmp_path: Path) -> None:
    batch, batch_plan, staging, asset_root, runtime = _inputs(tmp_path)
    output = tmp_path / "reviewer-bundle"
    build_reviewer_bundle(
        batch_manifest_path=batch,
        batch_plan_path=batch_plan,
        staging_root=staging,
        asset_root=asset_root,
        output_dir=output,
        viewer_runtime_root=runtime,
    )
    audit_path = output / "private" / "audit" / "audit-0.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["review_digest"] = "sha256:" + "0" * 64
    _write_json(audit_path, audit)
    _refresh_checksums(output)

    with pytest.raises(ReviewerBundleError, match="different review receipts"):
        verify_reviewer_bundle(output)
