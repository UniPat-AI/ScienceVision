from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

import httpx
from PIL import Image, ImageDraw
import pytest
import yaml

from qagen.chirality_pilot import build_chirality_pilot_viewer_profile
from qagen.contracts import digest, digest_bytes, make_envelope, utc_now
from qagen.errors import GatePendingError, StaleInputError
from qagen.paths import WorkspacePaths
from qagen.plan import approve_plan, load_plan
from qagen.plan.registry import _build_work_items
from qagen.providers import ProviderAdapterError
from qagen.release import ReleaseStageError, export_release, stage_release
from qagen.review import ReviewError, decide_review
from qagen.run import RunError, execute_run, load_persisted_execution, load_run, resolve_plan_work_item, run_directory, start_run
from qagen.seeds import registry as seed_registry
from qagen.storage.atomic import atomic_write_json
from qagen.viewer import build_chirality_scene_registry, load_viewer_profile
from qagen.verification import load_question_text_policy
from qagen.workers import ProviderNotConfiguredError
import qagen.provider_science_bridge as provider_science_bridge

from conftest import PACKAGE_ROOT


PILOT_LEAF = "chemistry.stereochemistry_chirality"
API_KEY_ENV = "QAGEN_UNIV_API_KEY"
BASE_URL_ENV = "QAGEN_UNIV_BASE_URL"
APPROVAL_PATH_ENV = "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH"
SOURCE_ENV = "QAGEN_EV2_SOURCE_ROOT"
TEST_ENDPOINT = "https://provider-run.test"

_SOURCE_FILE_ROLES = {
    "manifest": {"path": "MANIFEST.json", "format": "json", "parser_contract": "ev2_manifest_v1"},
    "version_note": {"path": "README.md", "format": "markdown", "parser_contract": "ev2_version_note_v1"},
    "tasks": {"path": "tasks/tasks.jsonl", "format": "jsonl", "parser_contract": "ev2_tasks_v1"},
    "expert_review": {
        "path": "provenance/expert-review.json",
        "format": "json",
        "parser_contract": "ev2_expert_review_v1",
    },
    "asset_manifest": {
        "path": "provenance/asset-manifest.jsonl",
        "format": "jsonl",
        "parser_contract": "ev2_asset_manifest_v1",
    },
}
_TAXONOMY_ALIASES = {
    "schema_version": 1,
    "kind": "qagen_subdomain_aliases",
    "id": "provider_pilot_subdomain_aliases",
    "version": 1,
    "aliases": [
        {
            "source_leaf": "chemistry.chirality",
            "target_leaf": PILOT_LEAF,
        }
    ],
}


def _provider_truth_record(work_item_id: str, *, measurement: bool = False) -> dict[str, Any]:
    """Return a pre-approved source record, never a model-invented truth claim."""

    answer = {"answer": "Controlled stereochemical observation."}
    if measurement:
        record_id = f"provider-pilot-measurement-{work_item_id}"
        return {
            "source_type": "measurement_receipt",
            "source_id": "provider-pilot-controlled-measurement",
            "source_digest": digest({"source": "provider-pilot-measurement", "work_item": work_item_id}),
            "record_id": record_id,
            "record_digest": digest({"record": record_id}),
            "answer_digest": digest(answer),
            "measurement_receipt_digest": digest({"measurement_receipt": record_id}),
        }
    record_id = f"provider-pilot-reviewed-observation-{work_item_id}"
    return {
        "source_type": "expert_annotation",
        "source_id": "provider-pilot-reviewed-observation",
        "source_digest": digest({"source": "provider-pilot-reviewed-observation", "work_item": work_item_id}),
        "record_id": record_id,
        "record_digest": digest({"record": record_id}),
        "answer_digest": digest(answer),
    }


def _write_provider_ground_truth_registry(
    paths: WorkspacePaths,
    *,
    work_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Freeze every provider-pilot truth record before plan approval."""

    records: list[dict[str, Any]] = []
    for work_item in work_items:
        work_item_id = work_item["work_item_id"]
        assert isinstance(work_item_id, str)
        records.extend(
            [
                _provider_truth_record(work_item_id),
                _provider_truth_record(work_item_id, measurement=True),
            ]
        )
    document = {
        "schema_version": 1,
        "kind": "qagen_ground_truth_registry",
        "id": "provider-run-test-ground-truth-registry",
        "source_status": "approved",
        "record_count": len(records),
        "approved_snapshot": {"records": records},
    }
    path = paths.private / "ground-truth-registries" / "provider-run-test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    content_digest = atomic_write_json(path, document, private=True)
    for work_item in work_items:
        work_item_id = work_item["work_item_id"]
        assert isinstance(work_item_id, str)
        work_item["ground_truth_provenance"] = {
            **_provider_truth_record(work_item_id),
            "ground_truth_registry_digest": content_digest,
        }
    return {
        "path": path.relative_to(paths.root).as_posix(),
        "content_digest": content_digest,
    }


def _synthetic_host_render(sequence: int) -> bytes:
    """Create a valid, visibly distinct controlled render for host-chain tests."""
    image = Image.new("RGBA", (96, 96), (19 + sequence * 12, 37, 68, 255))
    draw = ImageDraw.Draw(image)
    offset = sequence * 14
    draw.rectangle((8 + offset, 10, 52 + offset, 54), fill=(236, 127 - sequence * 18, 43, 255))
    draw.ellipse((29, 31 + offset // 2, 82, 84 + offset // 2), fill=(56, 185, 160 + sequence * 18, 255))
    draw.line((4, 92 - offset, 92, 4 + offset), fill=(245, 245, 245, 255), width=4)
    with BytesIO() as buffer:
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value, sort_keys=True) + "\n" for value in values), encoding="utf-8")


def _source_inventory_digest(package_root: Path) -> str:
    entries = [
        {
            "path": path.relative_to(package_root).as_posix(),
            "type": "file",
            "bytes": path.stat().st_size,
            "sha256": f"sha256:{sha256(path.read_bytes()).hexdigest()}",
        }
        for path in sorted(package_root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file() and path.name != ".DS_Store"
    ]
    return digest(entries)


def _prepare_provider_scene_registry(
    tmp_path: Path,
    paths: WorkspacePaths,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Create a resolved local EV2 closure for the non-fixture pilot viewer."""
    package_root = tmp_path / "ev2-provider-pilot-v1.0.3"
    # Profile construction attests the static Mol* host build.  Provider-run
    # tests use a separately attested synthetic handoff, so a tiny regular
    # static tree is enough here and avoids launching a browser.
    (package_root / "viewer" / "shared").mkdir(parents=True, exist_ok=True)
    (package_root / "viewer" / "molstar.html").write_text("<!doctype html><title>test host</title>\n", encoding="utf-8")
    (package_root / "viewer" / "shared" / "host.js").write_text("window.__qagen_test_host = true;\n", encoding="utf-8")
    tasks: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    statuses = ("verified", "minor_fix", "factual_error", "unscorable")
    for index, status in enumerate(statuses, start=1):
        asset_path = package_root / "assets" / f"asset-{index:02d}.mol"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_bytes = f"provider pilot fixture Mol {index}\nM  END\n".encode("ascii")
        asset_path.write_bytes(asset_bytes)
        asset_digest = sha256(asset_bytes).hexdigest()
        task_id = "provider-pilot-chirality-seed" if status == "verified" else f"provider-pilot-{status}-{index}"
        is_chirality = status == "verified"
        tasks.append(
            {
                "task_id": task_id,
                "domain": "chemistry" if is_chirality else "biology",
                "sub_domain": {"slug": "chirality" if is_chirality else "docking_pose_plausibility"},
                "question": f"Provider fixture question {index}",
                "answer_schema": {"kind": "short_text", "fields": ["answer"]},
                "asset": {
                    "repo_path": f"crawl_data/provider-fixture/asset-{index:02d}.mol",
                    "package_path": f"assets/asset-{index:02d}.mol",
                    "sha256": asset_digest,
                    "byte_size": len(asset_bytes),
                    "viewer": "molstar",
                    "format": "mol",
                },
                "expert_review": {"verdict_class": status},
                "provenance": {"source_suite": "EV2_Provider_Pilot"},
                "reference_answer": {"status": "expert_reference", "value": f"fixture answer {index}"},
                "tool_policy": {"allowed_tools": ["rotate", "render"]},
                "v_class": "V3",
            }
        )
        assets.append(
            {
                "byte_size": len(asset_bytes),
                "package_path": f"assets/asset-{index:02d}.mol",
                "repo_path": f"crawl_data/provider-fixture/asset-{index:02d}.mol",
                "sha256": asset_digest,
            }
        )

    _write_jsonl(package_root / _SOURCE_FILE_ROLES["tasks"]["path"], tasks)
    _write_jsonl(package_root / _SOURCE_FILE_ROLES["asset_manifest"]["path"], assets)
    _write_json(
        package_root / _SOURCE_FILE_ROLES["expert_review"]["path"],
        {"reviews": len(tasks), "anchors": [{"task_id": task["task_id"]} for task in tasks]},
    )
    _write_json(
        package_root / _SOURCE_FILE_ROLES["manifest"]["path"],
        {
            "schema_version": "sciencevision.ev2_pilot.sample_cases_by_type.v1",
            "package_id": "ev2-provider-pilot-v1.0.2",
            "version": "1.0.2",
            "task_count": len(tasks),
            "tasks_path": _SOURCE_FILE_ROLES["tasks"]["path"],
            "expert_review": {"manifest": _SOURCE_FILE_ROLES["expert_review"]["path"]},
            "asset_closure": {"manifest": _SOURCE_FILE_ROLES["asset_manifest"]["path"]},
        },
    )
    (package_root / _SOURCE_FILE_ROLES["version_note"]["path"]).write_text(
        "# Provider test EV2 package\n\nVersion: v1.0.2\n", encoding="utf-8"
    )

    source_spec_path = tmp_path / "provider-source" / "ev2-source-spec.json"
    _write_json(source_spec_path.parent / "aliases" / "provider-aliases.json", _TAXONOMY_ALIASES)
    _write_json(
        source_spec_path,
        {
            "schema_version": 1,
            "kind": "qagen_seed_source_spec",
            "id": "provider_pilot_ev2_source",
            "importer": "ev2_local_package_v1",
            "mode": "controlled",
            "root_env": SOURCE_ENV,
            "expected_basename": package_root.name,
            "file_roles": _SOURCE_FILE_ROLES,
            "taxonomy_aliases": {
                "path": "aliases/provider-aliases.json",
                "version": 1,
                "content_digest": digest(_TAXONOMY_ALIASES),
            },
            "version_policy": "require_human_resolution_on_conflict",
            "local_only": True,
            "production_eligible": False,
            "expected_inventory_digest": _source_inventory_digest(package_root),
            "expected_package": {
                "package_id": "ev2-provider-pilot-v1.0.2",
                "version_labels": {
                    "directory": "v1.0.3",
                    "manifest": "v1.0.2",
                    "package_id": "v1.0.2",
                    "version_note": "v1.0.2",
                },
                "case_count": len(tasks),
                "review_distribution": {
                    "verified": 1,
                    "minor_fix": 1,
                    "factual_error": 1,
                    "unscorable": 1,
                },
                "provenance": {
                    "required_fields": [
                        "task_id",
                        "domain",
                        "sub_domain.slug",
                        "expert_review.verdict_class",
                        "provenance.source_suite",
                        "asset.repo_path",
                        "asset.sha256",
                        "reference_answer.status",
                    ],
                    "require_asset_sha256": True,
                    "require_asset_package_path": True,
                    "require_asset_byte_size": True,
                    "require_source_suite": True,
                },
            },
        },
    )

    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    pending = seed_registry.sync_seed_package(paths, package_root, source_spec_path=source_spec_path)
    assert pending["eligible"] is False
    seed_registry.accept_source_version(
        paths,
        request_path=str(pending["request_path"]),
        reviewer="source-owner",
        resolved_version="1.0.3",
        decision="accept",
        rationale="The local EV2 v1.0.3 package directory is the accepted pilot source version.",
    )
    accepted = seed_registry.sync_seed_package(paths, package_root, source_spec_path=source_spec_path)
    assert accepted["eligible"] is True
    registry = build_chirality_scene_registry(
        paths,
        package_root=package_root,
        source_spec_path=source_spec_path,
        seed_task_id="provider-pilot-chirality-seed",
    )
    assert registry["runtime_eligible"] is True
    seed_index_path = Path(str(accepted["seed_index_path"]))
    seed_index = json.loads(seed_index_path.read_text(encoding="utf-8"))
    return {
        **registry,
        "seed_index": seed_index,
        "seed_index_path": str(seed_index_path),
    }


def _artifact_lock(label: str, path: Path, artifact: Mapping[str, Any]) -> dict[str, str]:
    return {
        "label": label,
        "path": str(path),
        "target": "bytes",
        "digest": digest_bytes(path.read_bytes()),
        "artifact_digest": str(artifact["content_digest"]),
        "artifact_kind": str(artifact["kind"]),
    }


def _configure_external_provider_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_path: Path,
) -> None:
    """Keep endpoint and human approval external to checked-in provider config."""
    runtime = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    approval = {
        "schema_version": 1,
        "kind": "qagen_provider_endpoint_approval",
        "id": "provider-run-test-endpoint-approval",
        "status": "approved",
        "decision": "allow_credentialed_https_endpoint",
        "endpoint": TEST_ENDPOINT,
        "credential_env": API_KEY_ENV,
        "runtime_config_digest": digest(runtime),
        "scope": "test-only provider pilot execution",
        "decided_by": "qagen-tests",
        "decided_at": "2026-08-26",
        "production_eligible": False,
    }
    approval_path = tmp_path / "external-provider-endpoint-approval.yaml"
    approval_path.write_text(yaml.safe_dump(approval, sort_keys=False), encoding="utf-8")
    monkeypatch.setenv(BASE_URL_ENV, TEST_ENDPOINT)
    monkeypatch.setenv(APPROVAL_PATH_ENV, str(approval_path))


def _write_approved_knowledge_registry(
    paths: WorkspacePaths,
    *,
    seed_index: Mapping[str, Any],
) -> tuple[dict[str, Any], Path, dict[str, Any], Path]:
    """Create frozen test artifacts equivalent to an approved workspace registry."""
    seed_body = seed_index["body"]
    seed_catalog_digest = str(seed_body["seed_catalog_digest"])
    review = make_envelope(
        kind="knowledge_review_decision",
        identifier="provider-pilot-knowledge-review",
        status="approved",
        body={"decision": "approved", "scope": "test-only frozen chirality knowledge"},
        producer="qagen/tests:provider-run",
    )
    review_path = paths.receipts / "knowledge" / "provider-pilot.approved.review.json"
    atomic_write_json(review_path, review, private=True)
    snapshot_body = {
        "id": PILOT_LEAF,
        "status": "approved",
        "scientific_scope": {
            "included": ["tetrahedral stereocenter orientation from controlled renders"],
            "excluded": ["raw molecular file inspection", "metadata lookup"],
        },
        "visual_target_types": ["tetrahedral_stereocenter"],
        "scenario_cards": [
            {
                "id": "chirality-controlled-view",
                "observable_targets": ["substituent ordering around one stereocenter"],
                "valid_question_patterns": ["identify the visible stereochemical relation"],
                "invalid_question_patterns": ["infer a hidden identifier"],
            }
        ],
        "allowed_inference": ["compare the two host-attested controlled views"],
        "forbidden_shortcuts": ["asset path", "task identifier", "source metadata", "network"],
        "evidence_requirements": {
            "required_viewer_operations": ["render", "rotate"],
            "required_evidence": ["host_attested_receipt_chain"],
        },
        "requirements": {
            "viewer_requirements": ["render", "rotate"],
            "answer_modes": ["structured short answer"],
        },
        "common_failure_modes": ["answering from an unobserved identifier"],
        "rubric_extensions": ["must cite host-attested visual target"],
        "review": {
            "decision": "approved",
            "decision_digest": review["content_digest"],
        },
    }
    snapshot = make_envelope(
        kind="subdomain_knowledge_snapshot",
        identifier=PILOT_LEAF,
        status="approved",
        body=snapshot_body,
        producer="qagen/tests:provider-run",
    )
    snapshot_path = paths.knowledge / "compiled" / "chemistry" / "chirality.provider-pilot.snapshot.json"
    atomic_write_json(snapshot_path, snapshot)
    index_body = {
        "mode": "workspace_registry",
        "seed_index_digest": seed_index["content_digest"],
        "seed_catalog_digest": seed_catalog_digest,
        "entries": {
            PILOT_LEAF: {
                "state": "approved",
                "snapshot_digest": snapshot["content_digest"],
                "snapshot_path": snapshot_path.relative_to(paths.root).as_posix(),
                "review_digest": review["content_digest"],
                "review_path": review_path.relative_to(paths.root).as_posix(),
                "draft_digest": digest({"draft": "provider-pilot"}),
                "input_digest": digest({"input": "provider-pilot"}),
                "seed_catalog_digest": seed_catalog_digest,
                "source_digests": {"expert:provider-pilot": digest({"expert": "provider-pilot"})},
                "compiler_digest": digest({"compiler": "provider-pilot"}),
                "local_only": True,
                "production_eligible": False,
            }
        },
    }
    knowledge_index = make_envelope(
        kind="knowledge_index",
        identifier="provider-pilot-knowledge-index",
        status="ready",
        body=index_body,
        producer="qagen/tests:provider-run",
    )
    knowledge_index_path = paths.knowledge / "indexes" / "provider-pilot.knowledge-index.json"
    atomic_write_json(knowledge_index_path, knowledge_index)
    return snapshot, snapshot_path, knowledge_index, knowledge_index_path


def _install_synthetic_host_handoff(
    monkeypatch: pytest.MonkeyPatch,
    *,
    paths: WorkspacePaths,
    viewer_profile_path: str,
) -> None:
    """Patch browser capture with a fully revalidatable private receipt chain."""
    profile = load_viewer_profile(paths, viewer_profile_path)
    profile_body = profile["body"]

    def build_handoff(
        received_paths: WorkspacePaths,
        *,
        registry_path: str | Path,
        viewer_profile_digest: str,
        anonymous_scene_ref: str,
        request_id: str,
    ) -> dict[str, Any]:
        assert received_paths == paths
        assert viewer_profile_digest == profile["content_digest"]
        suffix = digest({"request_id": request_id})[7:23]
        session_id = f"vsess_test_{suffix}"
        directory = paths.private / "viewer" / "sessions" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        attestation = make_envelope(
            kind="viewer_session_attestation",
            identifier=session_id,
            status="open",
            body={
                "session_id": session_id,
                "request_id": request_id,
                "viewer_profile_path": str(Path(viewer_profile_path).resolve()),
                "viewer_profile_digest": viewer_profile_digest,
                "anonymous_scene_ref": anonymous_scene_ref,
                "registry_digest": profile_body["scene_registry_digest"],
                "host_build_digest": profile_body["host_build_digest"],
                "session_evidence_schema_digest": profile_body["session_evidence_schema_digest"],
                "allowed_operations": ["render", "rotate"],
                "adapter_id": "molstar-chirality-host-v1",
                "backend_kind": "molstar_browser_host_v1",
                "opened_at": utc_now(),
                "release_eligible": False,
                "release_blockers": ["source_spec_local_only"],
                "source_version_resolution": {
                    "status": "resolved",
                    "resolved_version": "1.0.3",
                    "seed_index_digest": digest({"test": "seed-index"}),
                    "receipt_digest": digest({"test": "source-receipt"}),
                    "blockers": [],
                },
            },
            producer="qagen/tests:synthetic-host",
        )
        atomic_write_json(directory / "session-attestation.json", attestation, private=True)
        receipt_digests: list[str] = []
        render_digests: list[str] = []
        rendered_views: list[dict[str, str]] = []
        observations: list[dict[str, Any]] = []
        previous: str | None = None
        for sequence, operation in enumerate(("render", "rotate")):
            png = _synthetic_host_render(sequence)
            render_digest = digest_bytes(png)
            render_name = f"render-{sequence:06d}.png"
            render_path = directory / render_name
            render_path.write_bytes(png)
            os.chmod(render_path, 0o600)
            receipt = make_envelope(
                kind="viewer_action_receipt",
                identifier=f"{session_id}:{sequence}",
                status="attested",
                body={
                    "session_id": session_id,
                    "session_attestation_digest": attestation["content_digest"],
                    "viewer_profile_digest": viewer_profile_digest,
                    "registry_digest": profile_body["scene_registry_digest"],
                    "host_build_digest": profile_body["host_build_digest"],
                    "session_evidence_schema_digest": profile_body["session_evidence_schema_digest"],
                    "sequence": sequence,
                    "operation": operation,
                    "arguments_digest": digest({} if operation == "render" else {"d_azimuth_deg": 35}),
                    "render_digest": render_digest,
                    "render_file": render_name,
                    "camera": {
                        "position": [1.0 + sequence, 2.0, 3.0],
                        "target": [0.0, 0.0, 0.0],
                        "up": [0.0, 1.0, 0.0],
                        "mode": "perspective",
                        "fov_radians": 0.7,
                        "distance": 5.0 + sequence,
                    },
                    "previous_receipt_digest": previous,
                    "recorded_at": utc_now(),
                },
                producer="qagen/tests:synthetic-host",
            )
            atomic_write_json(directory / f"receipt-{sequence:06d}.json", receipt, private=True)
            previous = receipt["content_digest"]
            receipt_digests.append(previous)
            render_digests.append(render_digest)
            observations.append(
                {
                    "operation": operation,
                    "sequence": sequence,
                    "receipt_digest": previous,
                    "render_digest": render_digest,
                }
            )
            rendered_views.append(
                {
                    "receipt_digest": previous,
                    "render_digest": render_digest,
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                }
            )
        atomic_write_json(
            directory / "closed.json",
            {
                "session_attestation_digest": attestation["content_digest"],
                "action_count": len(receipt_digests),
                "final_receipt_digest": receipt_digests[-1],
                "closed_at": utc_now(),
            },
            private=True,
        )
        handoff_body = {
            "artifact_kind": "attested_chirality_provider_handoff",
            "viewer_profile_digest": viewer_profile_digest,
            "scene_registry_digest": profile_body["scene_registry_digest"],
            "anonymous_scene_ref": anonymous_scene_ref,
            "session_id": session_id,
            "session_attestation_digest": attestation["content_digest"],
            "receipt_digests": receipt_digests,
            "receipt_chain_digest": digest(receipt_digests),
            "solver_safe_observations": observations,
            "render_digests": render_digests,
            "release_eligible": False,
        }
        handoff = make_envelope(
            kind="provider_viewer_evidence_handoff",
            identifier=f"provider-handoff-{session_id}",
            status="attested",
            body=handoff_body,
            producer="qagen/tests:synthetic-host",
        )
        handoff_path = directory / "provider-handoff.json"
        atomic_write_json(handoff_path, handoff, private=True)
        return {
            "handoff_path": str(handoff_path),
            "handoff_digest": handoff["content_digest"],
            "viewer_profile_digest": viewer_profile_digest,
            "scene_registry_digest": profile_body["scene_registry_digest"],
            "anonymous_scene_ref": anonymous_scene_ref,
            "session_attestation_digest": attestation["content_digest"],
            "receipt_digests": receipt_digests,
            "receipt_chain_digest": handoff_body["receipt_chain_digest"],
            "solver_safe_observations": observations,
            "rendered_views": rendered_views,
        }

    monkeypatch.setattr(provider_science_bridge, "build_attested_provider_handoff", build_handoff)


def _write_provider_run_inputs(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    count: int = 1,
    profile_id: str = "chirality_provider_pilot",
) -> tuple[WorkspacePaths, str]:
    """Build a minimal, fully lock-bound provider-pilot run without fixtures in repo config."""
    if count not in {1, *range(5, 11)}:
        raise ValueError("provider test helper requires one item or a batch of five through ten")
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()

    registry = _prepare_provider_scene_registry(tmp_path, paths, monkeypatch)
    viewer_result = build_chirality_pilot_viewer_profile(paths, registry_path=str(registry["registry_path"]))
    viewer = load_viewer_profile(paths, viewer_result["viewer_profile_path"])
    _install_synthetic_host_handoff(
        monkeypatch,
        paths=paths,
        viewer_profile_path=str(viewer_result["viewer_profile_path"]),
    )
    seed_index = registry["seed_index"]
    seed_index_path = Path(str(registry["seed_index_path"]))
    assert isinstance(seed_index, Mapping)
    snapshot, snapshot_path, knowledge_index, knowledge_index_path = _write_approved_knowledge_registry(
        paths,
        seed_index=seed_index,
    )

    routing_path = PACKAGE_ROOT / "configs" / "agents" / "model_routing.pilot.yaml"
    runtime_path = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.pilot.yaml"
    profile_path = PACKAGE_ROOT / "configs" / "profiles" / "chirality_provider_pilot.yaml"
    routing_digest = digest(yaml.safe_load(routing_path.read_text(encoding="utf-8")))
    runtime_digest = digest(yaml.safe_load(runtime_path.read_text(encoding="utf-8")))
    profile_digest = digest(yaml.safe_load(profile_path.read_text(encoding="utf-8")))
    rubric_path = PACKAGE_ROOT / "configs" / "rubric.yaml"
    rubric = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    assert isinstance(rubric, Mapping)
    rubric_lock_digest = digest(rubric)
    dependency = {
        "leaf_id": PILOT_LEAF,
        "seed_digest": seed_index["body"]["seed_catalog_digest"],
        "asset_digest": digest({"asset": "provider-pilot"}),
        "viewer_definition_digest": viewer["body"]["definition_digest"],
        "knowledge_digests": [snapshot["content_digest"]],
        "rubric_digest": rubric_lock_digest,
    }
    dependency_locks = [dependency]
    planning = {
        "default_count": 1,
        "quota": 10,
        "batch": {
            "min_count": 5,
            "max_count": 10,
            "seed_selection": {
                "policy": "locked_seed_digest",
                "dedupe_policy": "seed_variation_pair",
            },
            "variation": {"policy": "deterministic_unique_slot"},
        },
        "per_work_item_budget": {"max_candidates": 1, "max_revisions": 1},
    }
    viewer_requirement = {
        "adapter_id": "molstar-chirality-host-v1",
        "definition_digest": viewer["body"]["definition_digest"],
        "required_operations": ["rotate", "render"],
        "require_non_fixture": True,
        "require_scene_registry": True,
    }
    leaf_locks = {key: value for key, value in dependency.items() if key != "leaf_id"}
    eligible_leaves = [
        {
            "leaf_id": PILOT_LEAF,
            "required_operations": ["rotate", "render"],
            "locks": leaf_locks,
        }
    ]
    work_items = _build_work_items(
        eligible_leaves=eligible_leaves,
        dependency_locks=dependency_locks,
        count_per_leaf=count,
        planning=planning,
    )
    ground_truth_registry = _write_provider_ground_truth_registry(paths, work_items=work_items)
    config_body = {
        "profile_id": profile_id,
        "profile_mode": "local_provider_pilot",
        "profile_path": str(profile_path),
        "status": "local_provider_pilot_ready",
        "release_eligible": False,
        "input_locks": [
            {"label": "profile", "path": str(profile_path), "target": "document", "digest": profile_digest},
            {"label": "model_routing", "path": str(routing_path), "target": "document", "digest": routing_digest},
            {"label": "provider_runtime", "path": str(runtime_path), "target": "document", "digest": runtime_digest},
            {"label": "rubric", "path": str(rubric_path), "target": "document", "digest": rubric_lock_digest},
            _artifact_lock("seed_index", seed_index_path, seed_index),
            _artifact_lock("knowledge_index", knowledge_index_path, knowledge_index),
            _artifact_lock(f"knowledge_snapshot:{PILOT_LEAF}", snapshot_path, snapshot),
        ],
        "lock_digests": {
            "dependency_lock_map_digest": digest(dependency_locks),
            "model_routing_digest": routing_digest,
            "provider_runtime_digest": runtime_digest,
            "rubric_lock_digest": dependency["rubric_digest"],
            "planning_policy_digest": digest(planning),
            "viewer_profile_requirement_digest": digest(viewer_requirement),
        },
        "planning": planning,
        "viewer_profile_requirement": viewer_requirement,
        "model_routing": {"path": str(routing_path), "digest": routing_digest, "execution_eligible": True},
        "provider_runtime": {
            "path": str(runtime_path),
            "digest": runtime_digest,
            "production_eligible": False,
            "endpoint_env": BASE_URL_ENV,
            "endpoint_approval_path_env": APPROVAL_PATH_ENV,
        },
        "rubric": {
            "lock_digest": rubric_lock_digest,
            "document_digest": rubric["document"]["digest"],
            "release_thresholds": rubric["release_thresholds"],
            "fixture_policy": rubric["fixture_policy"],
        },
        "dependency_locks": dependency_locks,
        "leaves": [
            {
                "leaf_id": PILOT_LEAF,
                "status": "eligible",
                "blockers": [],
                "fixture_only": False,
                "effective": {"viewer": {"required_operations": ["rotate", "render"]}},
                "locks": leaf_locks,
            }
        ],
    }
    config = make_envelope(
        kind="compiled_config",
        identifier="provider-run-test-config",
        status="local_provider_pilot_ready",
        body=config_body,
        producer="qagen/tests:provider-run",
    )
    config_path = paths.compiled_configs / "provider-run-test-config.json"
    atomic_write_json(config_path, config, private=True)

    plan_body = {
        "config_path": str(config_path),
        "viewer_profile_path": str(viewer_result["viewer_profile_path"]),
        "profile_id": "chirality_provider_pilot",
        "profile_mode": "local_provider_pilot",
        "lock_digests": {
            "config_digest": config["content_digest"],
            "viewer_digest": viewer["content_digest"],
            "viewer_definition_digest": viewer["body"]["definition_digest"],
            "dependency_lock_map_digest": digest(dependency_locks),
            "model_routing_digest": routing_digest,
            "provider_runtime_digest": runtime_digest,
            "rubric_lock_digest": dependency["rubric_digest"],
            "planning_policy_digest": digest(planning),
            "viewer_profile_requirement_digest": digest(viewer_requirement),
            "scene_registry_digest": viewer["body"]["scene_registry_digest"],
            "work_item_map_digest": digest(work_items),
        },
        "dependency_locks": dependency_locks,
        "eligible_leaves": eligible_leaves,
        "work_items": work_items,
        "count_per_leaf": count,
        "planning": planning,
        "ground_truth_registry": ground_truth_registry,
        "fixture_only": False,
        "provider_pilot": True,
    }
    plan = make_envelope(
        kind="generation_plan",
        identifier="provider-run-test-plan",
        status="pending_approval",
        body=plan_body,
        producer="qagen/tests:provider-run",
    )
    plan_path = paths.plans / "provider-run-test-plan.json"
    atomic_write_json(plan_path, plan, private=True)
    approval = approve_plan(paths, plan_path=plan_path, reviewer="plan-owner", decision="approved")
    started = start_run(paths, plan_path=plan_path, approval_path=approval["approval_path"])
    _configure_external_provider_endpoint(tmp_path, monkeypatch, runtime_path)
    return paths, str(started["run_id"])


def _role_and_input(request: httpx.Request) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    body = json.loads(request.content)
    system = body.get("system")
    messages = body["messages"]
    if system is None:
        system = messages[0]["content"]
        user = messages[1]["content"]
    else:
        user = messages[0]["content"]
    match = re.search(r"worker for role ([a-z_]+)\.", system)
    assert match is not None
    if isinstance(user, list):
        text_blocks = [item.get("text") for item in user if isinstance(item, Mapping) and item.get("type") == "text"]
        assert len(text_blocks) == 1
        assert isinstance(text_blocks[0], str)
        user = text_blocks[0]
    stage_input = json.loads(user)
    return match.group(1), stage_input, body


def _provider_response(request: httpx.Request) -> httpx.Response:
    role, stage_input, body = _role_and_input(request)
    root = stage_input["input_contract"]
    dependencies = root["dependencies"]
    work_item = root["root_input"]["work_item"]
    root_input = root["root_input"]
    work_item_id = root["work_item_id"]
    ground_truth_provenance = root_input["ground_truth_provenance"]
    bridge = root_input["provider_science_bridge"]
    visual_roles = {"author", "evidence_worker", "verifier"}
    if role in visual_roles:
        assert bridge["artifact_kind"] == "approved_local_chirality_science_bridge"
        if role in {"author", "verifier"}:
            assert bridge["knowledge_context"]["scenario_cards"]
        else:
            assert "knowledge_context" not in bridge
            assert "seed_context" not in bridge
            assert "solver_safe_observations" not in bridge["viewer_evidence"]
        rendered_views = bridge["viewer_evidence"]["rendered_views"]
        assert len(rendered_views) == 2
        assert all("data_url" not in view and "multimodal_attachment" in view for view in rendered_views)
        user_content = body["messages"][0]["content"] if body.get("system") is not None else body["messages"][1]["content"]
        assert isinstance(user_content, list)
        if body["model"] == "claude-opus-5":
            assert [block["type"] for block in user_content] == ["text", "image", "image"]
            assert all(block["source"]["data"].startswith("iVBOR") for block in user_content[1:])
        else:
            assert [block["type"] for block in user_content] == ["text", "image_url", "image_url"]
            assert all(block["image_url"]["url"].startswith("data:image/png;base64,iVBOR") for block in user_content[1:])
    else:
        assert "knowledge_context" not in bridge
    viewer_evidence = bridge.get("viewer_evidence", {})
    candidate_attested = {
        "handoff_digest": viewer_evidence.get("handoff_digest"),
        "receipt_digests": viewer_evidence.get("receipt_digests"),
        "render_digests": [view["render_digest"] for view in viewer_evidence.get("rendered_views", [])],
    }
    if role == "author":
        output: dict[str, Any] = {
            "artifact_kind": "author_private_candidate",
            "task_id": f"provider-pilot-chirality-{work_item_id}",
            "prompt": f"Inspect controlled chirality view {work_item_id} and identify the stereochemical relation.",
            "anonymous_scene_ref": viewer_evidence["anonymous_scene_ref"],
            "viewer_session_request": {
                "request_id": f"provider-pilot-request-{work_item_id}",
                "viewer_profile_digest": root_input["viewer_profile_digest"],
                "allowed_operations": work_item["approved_operations"],
            },
            "reference_answer": {"answer": "Controlled stereochemical observation."},
            "reasoning": "The answer is tied to the controlled viewer observation.",
            "rubric": {"authenticity": "controlled-viewer"},
            "visual_target": {"kind": "tetrahedral_stereocenter", "variation_slot": work_item_id},
            "question_contract": {
                "question_family_kind": "property_identification",
                "question_kernel": f"identify controlled stereochemical relation {work_item_id}",
                "question_text_policy_digest": load_question_text_policy().content_digest,
                "gt_answer_contract": {
                    "source_type": "expert_annotation",
                    "gt_shape": "categorical_label",
                    "answer_fields": ["answer"],
                    "provenance": ground_truth_provenance,
                },
                "asset_facets": {
                    "body_region": [],
                    "organ_system": [],
                    "anatomy": [],
                    "pathology": [],
                    "modality": ["molecular_3d"],
                    "dataset": ["provider-pilot-chirality"],
                },
            },
            "attested_evidence": candidate_attested,
            "provenance": {
                "plan_digest": root_input["plan_digest"],
                "config_digest": root_input["config_digest"],
                "knowledge_snapshot_digest": work_item["knowledge_snapshot_digest"],
                "work_item_id": work_item_id,
            },
        }
    elif role == "evidence_worker":
        output = {
            "artifact_kind": "provider_attested_viewer_evidence",
            "candidate_digest": dependencies["author"]["output_contract_digest"],
            "viewer_profile_digest": root_input["viewer_profile_digest"],
            "viewer_operations": work_item["approved_operations"],
            "host_evidence_digest": candidate_attested["handoff_digest"],
            "receipt_digests": candidate_attested["receipt_digests"],
            "work_item_id": work_item_id,
            "state_transition": None,
        }
    elif role == "verifier":
        output = {
            "artifact_kind": "verification_result",
            "candidate_digest": dependencies["author"]["output_contract_digest"],
            "evidence_digest": dependencies["evidence_worker"]["output_contract_digest"],
            "outcome": "pass",
            "checks": [
                {"name": "candidate_evidence_binding", "outcome": "pass"},
                {"name": "independent_verifier_route", "outcome": "pass"},
            ],
            "rubric_scores": {
                "authenticity": {"score": 2, "maximum": 2},
                "correctness": {"score": 1, "maximum": 1},
                "three_d_relevance": {"score": 1, "maximum": 1},
            },
            "rubric_lock_digest": root_input["rubric_lock_digest"],
            "work_item_id": work_item_id,
            "attested_evidence": {
                "handoff_digest": candidate_attested["handoff_digest"],
                "receipt_digests": candidate_attested["receipt_digests"],
            },
            "state_transition": None,
        }
    else:
        output = {"artifact_kind": f"provider_{role}_output", "state_transition": None}
    if body["model"] == "claude-opus-5":
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "content": [{"type": "text", "text": json.dumps(output)}],
                "usage": {"input_tokens": 2, "output_tokens": 3, "cost_usd": 0.01},
            },
        )
    return httpx.Response(
        200,
        json={
            "model": body["model"],
            "choices": [{"message": {"content": json.dumps(output)}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "cost_usd": 0.01},
        },
    )


def _rewrite_candidate_as_measurement(path: Path, *, ordinal: int) -> None:
    """Keep a valid private contract while making the family mix intentionally invalid."""

    candidate = json.loads(path.read_text(encoding="utf-8"))
    contract = candidate["question_contract"]
    work_item_id = candidate["provenance"]["work_item_id"]
    assert isinstance(work_item_id, str)
    prior_provenance = contract["gt_answer_contract"]["provenance"]
    assert isinstance(prior_provenance, Mapping)
    contract["question_family_kind"] = "measurement"
    contract["gt_answer_contract"] = {
        "source_type": "measurement_receipt",
        "gt_shape": "scalar_numeric",
        "answer_fields": ["answer"],
        "provenance": {
            **_provider_truth_record(work_item_id, measurement=True),
            "ground_truth_registry_digest": prior_provenance["ground_truth_registry_digest"],
        },
    }
    _write_json(path, candidate)


def _mutated_provider_response(
    request: httpx.Request,
    *,
    mutate: Callable[[str, dict[str, Any]], None],
) -> httpx.Response:
    """Apply a narrow model-output fault without changing the locked input."""
    role, _stage_input, body = _role_and_input(request)
    payload = _provider_response(request).json()
    if body["model"] == "claude-opus-5":
        content = payload["content"][0]["text"]
    else:
        content = payload["choices"][0]["message"]["content"]
    output = json.loads(content)
    assert isinstance(output, dict)
    mutate(role, output)
    if body["model"] == "claude-opus-5":
        payload["content"][0]["text"] = json.dumps(output)
    else:
        payload["choices"][0]["message"]["content"] = json.dumps(output)
    return httpx.Response(200, json=payload)


def test_provider_pilot_execute_uses_locked_real_adapters_and_persists_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _provider_response(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = execute_run(paths, run_id=run_id, provider_client=client)

    assert result["execution_status"] == "provider_pilot_execution_complete_pending_human_review"
    assert len(result["executed"]) == 1
    assert [json.loads(request.content)["model"] for request in seen] == [
        "claude-opus-5",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
        "gpt-5.6-sol",
    ]
    assert [request.url.path for request in seen] == [
        "/v1/messages",
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]

    entry = result["executed"][0]
    assert entry["execution_mode"] == "local_provider_pilot"
    assert entry["production_eligible"] is False
    assert entry["stage_digests"]["author"]["provider"] == "deepseek"
    assert entry["stage_digests"]["verifier"]["provider"] == "openai"
    assert entry["stage_digests"]["author"]["fixture_only"] is False
    assert entry["stage_digests"]["verifier"]["fixture_only"] is False
    assert all(usage["cost_accounting"] == "provider_reported" for usage in entry["usage"].values())

    run = load_run(paths, run_id)
    persisted = load_persisted_execution(
        paths,
        run=run,
        candidate_path=entry["candidate_path"],
        candidate_digest=entry["candidate_digest"],
        evidence_path=entry["evidence_path"],
        evidence_digest=entry["evidence_digest"],
    )
    assert persisted["verification"]["candidate_digest"] == entry["candidate_digest"]
    assert persisted["verification"]["evidence_digest"] == entry["evidence_digest"]
    usage_records = [json.loads(line) for line in paths.usage.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in usage_records].count("usage_reservation") == 5
    assert [record["kind"] for record in usage_records].count("usage_settlement") == 5


def test_provider_pilot_batch_binds_each_repeated_leaf_to_its_work_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _provider_response(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = execute_run(paths, run_id=run_id, provider_client=client)

    assert result["execution_status"] == "provider_pilot_execution_complete_pending_human_review"
    assert len(result["executed"]) == 5
    assert len(seen) == 25

    run = load_run(paths, run_id)
    plan = load_plan(paths, run["body"]["plan_path"])
    work_item_ids = {item["work_item_id"] for item in plan["body"]["work_items"]}
    entries = result["executed"]
    assert len(work_item_ids) == 5
    assert {entry["work_item_id"] for entry in entries} == work_item_ids
    assert set(run["body"]["executions"]) == work_item_ids

    candidates: dict[str, dict[str, Any]] = {}
    for entry in entries:
        work_item_id = entry["work_item_id"]
        candidate = json.loads(Path(entry["candidate_path"]).read_text(encoding="utf-8"))
        evidence = json.loads(Path(entry["evidence_path"]).read_text(encoding="utf-8"))
        verification = json.loads(Path(entry["verification_path"]).read_text(encoding="utf-8"))
        candidates[work_item_id] = candidate

        assert candidate["provenance"]["work_item_id"] == work_item_id
        assert evidence["work_item_id"] == work_item_id
        assert verification["work_item_id"] == work_item_id
        assert evidence["candidate_digest"] == entry["candidate_digest"]
        assert verification["candidate_digest"] == entry["candidate_digest"]
        assert verification["evidence_digest"] == entry["evidence_digest"]
        assert (
            load_persisted_execution(
                paths,
                run=run,
                candidate_path=entry["candidate_path"],
                candidate_digest=entry["candidate_digest"],
                evidence_path=entry["evidence_path"],
                evidence_digest=entry["evidence_digest"],
            )["entry"]["work_item_id"]
            == work_item_id
        )

    assert len({candidate["viewer_session_request"]["request_id"] for candidate in candidates.values()}) == 5
    first = next(iter(candidates.values()))
    with pytest.raises(RunError, match="must provide work_item_id"):
        resolve_plan_work_item(
            plan,
            knowledge_digest=first["provenance"]["knowledge_snapshot_digest"],
            requested_operations=first["viewer_session_request"]["allowed_operations"],
        )
    for work_item_id, candidate in candidates.items():
        resolved = resolve_plan_work_item(
            plan,
            knowledge_digest=candidate["provenance"]["knowledge_snapshot_digest"],
            requested_operations=candidate["viewer_session_request"]["allowed_operations"],
            work_item_id=candidate["provenance"]["work_item_id"],
        )
        assert resolved["work_item_id"] == work_item_id


def test_provider_pilot_batch_reviews_and_privately_stages_all_five_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-production provider path reaches private staging, never export."""
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")

    with httpx.Client(transport=httpx.MockTransport(_provider_response)) as client:
        generated = execute_run(paths, run_id=run_id, provider_client=client)

    assert len(generated["executed"]) == 5
    candidate_paths: list[str] = []
    for entry in generated["executed"]:
        candidate_paths.append(entry["candidate_path"])
        evidence = json.loads(Path(entry["evidence_path"]).read_text(encoding="utf-8"))
        assert evidence["artifact_kind"] == "provider_attested_viewer_evidence"
        assert "observations" not in evidence
        assert len(set(entry["render_digests"])) == 2
        review = decide_review(
            paths,
            run_id=run_id,
            candidate_path=entry["candidate_path"],
            evidence_path=entry["evidence_path"],
            reviewer="domain-expert",
            decision="approved",
        )
        assert review["decision"] == "approved"
        assert review["work_item_id"] == entry["work_item_id"]

    staged = stage_release(paths, run_id=run_id, candidate_paths=candidate_paths)

    assert staged["status"] == "staged_not_exported"
    assert len(staged["staged"]) == 5
    assert len({item["work_item_id"] for item in staged["staged"]}) == 5
    assert all(Path(item["solver_path"]).is_file() for item in staged["staged"])
    assert all(Path(item["scorer_path"]).is_file() for item in staged["staged"])
    assert len(load_run(paths, run_id)["body"]["candidate_reviews"]) == 5

    output_dir = tmp_path / "production-release"
    with pytest.raises(GatePendingError, match="production-release"):
        export_release(paths, run_id=run_id, output_dir=output_dir)
    assert not output_dir.exists()


def test_provider_review_approval_requires_the_complete_approved_portfolio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")

    with httpx.Client(transport=httpx.MockTransport(_provider_response)) as client:
        generated = execute_run(paths, run_id=run_id, provider_client=client)

    first = generated["executed"][0]
    Path(generated["executed"][-1]["candidate_path"]).unlink()

    with pytest.raises(ReviewError, match="question coverage is pending until every plan work item has a candidate"):
        decide_review(
            paths,
            run_id=run_id,
            candidate_path=first["candidate_path"],
            evidence_path=first["evidence_path"],
            reviewer="domain-expert",
            decision="approved",
        )

    assert load_run(paths, run_id)["body"]["candidate_reviews"] == {}
    assert list(paths.review_receipts.glob("*.decision.json")) == []


def test_provider_review_approval_rejects_a_measurement_only_portfolio_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")

    with httpx.Client(transport=httpx.MockTransport(_provider_response)) as client:
        generated = execute_run(paths, run_id=run_id, provider_client=client)

    for ordinal, entry in enumerate(generated["executed"], start=1):
        _rewrite_candidate_as_measurement(Path(entry["candidate_path"]), ordinal=ordinal)
    first = generated["executed"][0]

    with pytest.raises(ReviewError, match="question coverage validation failed"):
        decide_review(
            paths,
            run_id=run_id,
            candidate_path=first["candidate_path"],
            evidence_path=first["evidence_path"],
            reviewer="domain-expert",
            decision="approved",
        )

    assert load_run(paths, run_id)["body"]["candidate_reviews"] == {}
    assert list(paths.review_receipts.glob("*.decision.json")) == []


def test_provider_staging_rejects_a_measurement_only_portfolio_before_writing_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")

    with httpx.Client(transport=httpx.MockTransport(_provider_response)) as client:
        generated = execute_run(paths, run_id=run_id, provider_client=client)

    candidate_paths: list[str] = []
    for ordinal, entry in enumerate(generated["executed"], start=1):
        path = Path(entry["candidate_path"])
        _rewrite_candidate_as_measurement(path, ordinal=ordinal)
        candidate_paths.append(str(path))
    staging_root = run_directory(paths, run_id) / "staging"
    before = sorted(
        path.relative_to(staging_root).as_posix()
        for path in staging_root.rglob("*")
        if path.is_file()
    )

    with pytest.raises(ReleaseStageError, match="candidate question coverage validation failed"):
        stage_release(paths, run_id=run_id, candidate_paths=candidate_paths)

    after = sorted(
        path.relative_to(staging_root).as_posix()
        for path in staging_root.rglob("*")
        if path.is_file()
    )
    assert after == before
    assert not (staging_root / "manifest.json").exists()


def test_provider_review_rejects_model_reported_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    with httpx.Client(transport=httpx.MockTransport(_provider_response)) as client:
        entry = execute_run(paths, run_id=run_id, provider_client=client)["executed"][0]

    evidence_path = Path(entry["evidence_path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["observations"] = [{"source": "model"}]
    _write_json(evidence_path, evidence)

    with pytest.raises(ReviewError, match="cannot contain model-reported observations"):
        decide_review(
            paths,
            run_id=run_id,
            candidate_path=entry["candidate_path"],
            evidence_path=evidence_path,
            reviewer="domain-expert",
            decision="approved",
        )


def test_provider_pilot_rejects_forged_model_receipt_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    calls: list[httpx.Request] = []

    def mutate(role: str, output: dict[str, Any]) -> None:
        if role == "author":
            output["attested_evidence"]["receipt_digests"] = [
                digest({"forged": "first"}),
                digest({"forged": "second"}),
            ]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _mutated_provider_response(request, mutate=mutate)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RunError, match="candidate evidence references do not match the host handoff"):
            execute_run(paths, run_id=run_id, provider_client=client)

    assert len(calls) == 5
    assert load_run(paths, run_id)["body"]["executions"] == {}


def test_provider_pilot_rejects_self_reported_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    calls: list[httpx.Request] = []

    def mutate(role: str, output: dict[str, Any]) -> None:
        if role == "evidence_worker":
            output["observations"] = [{"source": "model"}]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _mutated_provider_response(request, mutate=mutate)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderAdapterError, match="provider JSON contract failed schema validation"):
            execute_run(paths, run_id=run_id, provider_client=client)

    assert len(calls) == 4
    assert load_run(paths, run_id)["body"]["executions"] == {}


def test_provider_pilot_rejects_semantic_duplicate_batch_and_records_audit_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch, count=5)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    calls: list[httpx.Request] = []

    def mutate(role: str, output: dict[str, Any]) -> None:
        if role == "author":
            output["prompt"] = "Inspect the controlled chirality view and identify the stereochemical relation."
            output["reference_answer"] = {"answer": "Controlled stereochemical observation."}
            output["visual_target"] = {"kind": "tetrahedral_stereocenter"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _mutated_provider_response(request, mutate=mutate)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RunError, match="semantic duplicate"):
            execute_run(paths, run_id=run_id, provider_client=client)

    events = [json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()]
    duplicate_events = [event for event in events if event["body"]["event_type"] == "provider_candidate_rejected_duplicate"]
    assert len(calls) == 10
    assert len(duplicate_events) == 1
    payload = duplicate_events[0]["body"]["payload"]
    assert payload["run_id"] == run_id
    assert payload["work_item_id"] != payload["duplicate_of_work_item_id"]
    assert payload["candidate_digest"].startswith("sha256:")
    assert payload["semantic_signature"].startswith("sha256:")
    assert len(load_run(paths, run_id)["body"]["executions"]) == 1


def test_provider_pilot_without_key_never_calls_the_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    calls: list[httpx.Request] = []

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider transport must not be called without QAGEN_UNIV_API_KEY")

    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        with pytest.raises(ProviderNotConfiguredError, match="credential env is missing"):
            execute_run(paths, run_id=run_id, provider_client=client)

    assert calls == []
    assert load_run(paths, run_id)["status"] == "running"


def test_provider_pilot_runtime_lock_drift_blocks_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, run_id = _write_provider_run_inputs(tmp_path, monkeypatch=monkeypatch)
    monkeypatch.setenv(API_KEY_ENV, "provider-run-test-secret")
    runtime_path = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.pilot.yaml"
    original = runtime_path.read_text(encoding="utf-8")
    calls: list[httpx.Request] = []

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("provider transport must not be called after runtime lock drift")

    try:
        runtime_path.write_text(original.replace("timeout_seconds: 30", "timeout_seconds: 31", 1), encoding="utf-8")
        with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
            with pytest.raises(StaleInputError, match="run locks are stale"):
                execute_run(paths, run_id=run_id, provider_client=client)
    finally:
        runtime_path.write_text(original, encoding="utf-8")

    assert calls == []
    assert load_run(paths, run_id)["status"] == "blocked_stale_locks"
