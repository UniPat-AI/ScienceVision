from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import qagen.provider_science_bridge as bridge_module
import qagen.viewer.batch_scene_replay as replay_module
from qagen.contracts import digest, digest_bytes, make_envelope
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json, read_json
from qagen.viewer.batch_scene_replay import (
    BatchSceneReplayError,
    load_attested_batch_replay_binding,
    persist_slot_replay_receipt,
)


def _digest(label: str) -> str:
    return digest({"v32-provider-handoff-test": label})


class _Registry:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths
        self.content_digest = _digest("registry")
        self.selection_batch_digest = _digest("selection")
        self.scene_ref = "scene://v32-provider-handoff-fixture"
        self.slot_digest = _digest("slot")
        self._scene_value = {
            "slot_digest": self.slot_digest,
            "anonymous_scene_ref": self.scene_ref,
            "asset_set_digest": _digest("asset-set"),
            "viewer_family": "molstar",
            "asset_format": "cif",
            "operation_allowlist": ["render", "rotate"],
        }
        self.envelope = {"body": {"scenes": [dict(self._scene_value)]}}

    def _scene(self, scene_ref: str) -> dict[str, Any]:
        assert scene_ref == self.scene_ref
        return dict(self._scene_value)

    def resolve(self, scene_ref: str) -> dict[str, Any]:
        assert scene_ref == self.scene_ref
        primary = b"private-v32-scene"
        return {
            **self._scene(scene_ref),
            "asset_bytes": primary,
            "asset_content_digest": digest_bytes(primary),
        }


def _bundle(root: Path) -> dict[str, Any]:
    return {
        "bundle_version": 1,
        "root": str(root),
        "package_id": "sciencevision-v1.2.1",
        "manifest_digest": _digest("bundle-manifest"),
        "viewer_provenance_digest": _digest("bundle-provenance"),
        "required_files": [{"path": "viewer/molstar.html", "content_digest": _digest("molstar-html")}],
    }


def _binding(registry: _Registry, bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "lock_set": {"path": "/private/locked.lock", "content_digest": _digest("lock-set")},
        "registry": {"path": "/private/locked.registry", "content_digest": registry.content_digest},
        "receipt": {"path": "/private/locked.receipt", "content_digest": _digest("receipt")},
        "viewer_bundle": bundle,
    }


def _patch_v32_preflight(
    monkeypatch: pytest.MonkeyPatch,
    registry: _Registry,
) -> None:
    monkeypatch.setattr(bridge_module, "_batch_host_capability", lambda **_kwargs: {"mapping_id": "fixture"})
    monkeypatch.setattr(bridge_module.BatchSceneRegistry, "load", lambda *_args, **_kwargs: registry)


def _compiled_bridge_job(*, allowed_operations: list[str]) -> dict[str, Any]:
    return {
        "job_id": "job-v32-operation-mapping",
        "target_leaf": "chemistry.steric_accessibility",
        "slot_digest": _digest("compiled-bridge-slot"),
        "lock_bindings": {
            "config": {"content_digest": _digest("compiled-bridge-config")},
            "seed": {"content_digest": _digest("compiled-bridge-seed")},
            "knowledge": {"content_digest": _digest("compiled-bridge-knowledge")},
            "viewer": {
                "content_digest": _digest("compiled-bridge-viewer"),
                "anonymous_scene_ref": "scene://v32-operation-mapping",
                "allowed_operations": allowed_operations,
                "fresh_replay": {},
            },
        },
    }


def _patch_compiled_bridge_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    required_operations: list[str],
) -> None:
    monkeypatch.setattr(
        bridge_module,
        "_batch_config_context",
        lambda **_kwargs: {"required_operations": list(required_operations)},
    )
    monkeypatch.setattr(
        bridge_module,
        "_batch_seed_context",
        lambda *_args, **_kwargs: {
            "seed_index_digest": _digest("compiled-bridge-seed-index"),
            "seed_catalog_digest": _digest("compiled-bridge-seed-catalog"),
            "verified_records": [],
        },
    )
    monkeypatch.setattr(
        bridge_module,
        "_batch_knowledge_context",
        lambda *_args, **_kwargs: {
            "knowledge_snapshot_digest": _digest("compiled-bridge-knowledge-snapshot"),
            "knowledge": {},
        },
    )
    monkeypatch.setattr(
        bridge_module,
        "_batch_viewer_context",
        lambda *_args, **_kwargs: (
            {
                "viewer_profile_digest": _digest("compiled-bridge-viewer-profile"),
                "scene_registry_digest": _digest("compiled-bridge-scene-registry"),
                "capability_mapping": {
                    "mapping_digest": _digest("compiled-bridge-capability-mapping"),
                    "profile_digest": _digest("compiled-bridge-capability-profile"),
                },
                "fresh_replay_receipt_digest": _digest("compiled-bridge-fresh-replay"),
                "viewer_bundle": {
                    "manifest_digest": _digest("compiled-bridge-bundle-manifest"),
                    "viewer_provenance_digest": _digest("compiled-bridge-bundle-provenance"),
                },
            },
            {"handoff": "fixture"},
        ),
    )


def _preproduction_lock(
    paths: WorkspacePaths,
    *,
    artifact_directory: str,
    artifact_kind: str,
    approval_scope: str,
    leaf_id: str,
    selection_batch_digest: str,
    body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    domain, subdomain = leaf_id.split(".", 1)
    artifact = make_envelope(
        kind=artifact_kind,
        identifier=leaf_id,
        status="prepared",
        body=body,
    )
    bundle_root = paths.root / "preproduction" / "fixture-bundle"
    artifact_path = bundle_root / artifact_directory / domain / f"{subdomain}.json"
    atomic_write_json(artifact_path, artifact, private=True)
    receipt = {
        "schema_version": 1,
        "kind": "qagen_compiled_selection_batch_lock_approval",
        "id": f"fixture-{artifact_directory}-{subdomain}",
        "decision": "approved",
        "scope": approval_scope,
        "subject_digest": artifact["content_digest"],
        "reviewer": "fixture-reviewer",
        "reviewed_at": "2026-08-27T00:00:00Z",
    }
    receipt_path = bundle_root / "receipts" / artifact_directory / domain / f"{subdomain}.json"
    atomic_write_json(receipt_path, receipt, private=True)
    receipt_digest = digest(receipt)
    return (
        {
            "status": "approved",
            "content_digest": artifact["content_digest"],
            "approval_digest": receipt_digest,
            "approval_receipt": {"path": str(receipt_path), "content_digest": receipt_digest},
        },
        artifact,
    )


def test_preproduction_guidance_resolvers_require_approved_sibling_artifacts(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    leaf_id = "chemistry.steric_accessibility"
    selection_batch_digest = _digest("preproduction-selection")
    seed_body = {
        "scope": "local_preproduction_only",
        "selection_batch_digest": selection_batch_digest,
        "target_leaf": leaf_id,
        "slot_count": 1,
        "derivation": "fixture guidance",
        "visual_capabilities": ["steric_accessibility_assessment"],
        "response_shapes": ["expert_visual_judgment"],
        "positive_rules": ["Use the controlled viewer."],
        "anti_patterns": ["Do not reproduce historical content."],
        "production_eligible": False,
        "release_eligible": False,
    }
    seed_lock, seed_artifact = _preproduction_lock(
        paths,
        artifact_directory="seed",
        artifact_kind="qagen_local_preproduction_seed_guidance",
        approval_scope="compiled_selection_batch.seed",
        leaf_id=leaf_id,
        selection_batch_digest=selection_batch_digest,
        body=seed_body,
    )
    seed_context = bridge_module._batch_seed_context(
        paths,
        leaf_id=leaf_id,
        expected_digest=seed_artifact["content_digest"],
        lock=seed_lock,
        selection_batch_digest=selection_batch_digest,
    )
    assert seed_context["seed_guidance_digest"] == seed_artifact["content_digest"]
    assert seed_context["guidance"]["visual_capabilities"] == ["steric_accessibility_assessment"]
    assert str(paths.root) not in json.dumps(seed_context, sort_keys=True)

    knowledge_body = {
        "scope": "local_preproduction_only",
        "selection_batch_digest": selection_batch_digest,
        "target_leaf": leaf_id,
        "knowledge_class": "policy_only_no_domain_expert_claims",
        "verified_source_digests": [{"source_class": "exact_leaf_policy", "content_digest": _digest("leaf-policy")}],
        "permitted_use": ["Constrain generation to the leaf policy."],
        "prohibited_use": ["Do not treat this as domain-expert approval."],
        "production_eligible": False,
        "release_eligible": False,
        "domain_expert_approval": False,
    }
    knowledge_lock, knowledge_artifact = _preproduction_lock(
        paths,
        artifact_directory="knowledge",
        artifact_kind="qagen_local_preproduction_knowledge_boundary",
        approval_scope="compiled_selection_batch.knowledge",
        leaf_id=leaf_id,
        selection_batch_digest=selection_batch_digest,
        body=knowledge_body,
    )
    knowledge_context = bridge_module._batch_knowledge_context(
        paths,
        leaf_id=leaf_id,
        expected_digest=knowledge_artifact["content_digest"],
        lock=knowledge_lock,
        selection_batch_digest=selection_batch_digest,
    )
    assert knowledge_context["knowledge_boundary_digest"] == knowledge_artifact["content_digest"]
    assert knowledge_context["knowledge_boundary"]["domain_expert_approval"] is False

    stale_lock = dict(seed_lock)
    stale_lock["approval_digest"] = _digest("wrong-receipt")
    with pytest.raises(bridge_module.ProviderScienceBridgeError, match="approval receipt does not bind"):
        bridge_module._batch_seed_context(
            paths,
            leaf_id=leaf_id,
            expected_digest=seed_artifact["content_digest"],
            lock=stale_lock,
            selection_batch_digest=selection_batch_digest,
        )


def test_compiled_bridge_maps_only_inspect_to_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    job = _compiled_bridge_job(allowed_operations=["render", "rotate"])
    _patch_compiled_bridge_dependencies(monkeypatch, required_operations=["inspect", "rotate"])

    bridge, _handoff = bridge_module.prepare_compiled_selection_science_bridge(
        paths,
        batch_plan_digest=_digest("compiled-bridge-plan"),
        selection_batch_digest=_digest("compiled-bridge-selection"),
        job=job,
    )

    assert bridge["config_context"]["required_operations"] == ["inspect", "rotate"]

    monkeypatch.setattr(
        bridge_module,
        "_batch_config_context",
        lambda **_kwargs: {"required_operations": ["isolate"]},
    )
    with pytest.raises(bridge_module.ProviderScienceBridgeError, match="operations differ"):
        bridge_module.prepare_compiled_selection_science_bridge(
            paths,
            batch_plan_digest=_digest("compiled-bridge-plan"),
            selection_batch_digest=_digest("compiled-bridge-selection"),
            job=job,
        )


def test_conformation_batch_host_capability_covers_approved_native_operations() -> None:
    capability = bridge_module._BATCH_VIEWER_CAPABILITIES["chemistry.conformation_analysis"]
    assert {"render", "rotate", "zoom", "measure_dihedral"}.issubset(capability)


def test_missing_fresh_replay_blocks_before_any_controlled_host_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry = _Registry(paths)
    _patch_v32_preflight(monkeypatch, registry)
    host_calls: list[str] = []

    class NeverHost:
        def __init__(self, _root: str) -> None:
            host_calls.append("constructed")
            raise AssertionError("the controlled host must not start before fresh replay validation")

    def missing_binding(**_kwargs: Any) -> dict[str, Any]:
        raise BatchSceneReplayError("host_contract_invalid")

    monkeypatch.setattr(bridge_module, "ControlledBatchViewerReplayHost", NeverHost)
    monkeypatch.setattr(bridge_module, "load_attested_batch_replay_binding", missing_binding)

    with pytest.raises(bridge_module.ProviderScienceBridgeError, match="fresh viewer replay"):
        bridge_module._batch_viewer_context(
            paths,
            leaf_id="chemistry.conformation_analysis",
            work_item_id="job-v32-fixture",
            slot_digest=registry.slot_digest,
            viewer_lock_digest=_digest("viewer-lock"),
            selection_batch_digest=registry.selection_batch_digest,
            anonymous_scene_ref=registry.scene_ref,
            allowed_operations=["render", "rotate"],
            capability_mapping={},
            fresh_replay={"registry": {"path": "/private/locked.registry"}},
        )

    assert host_calls == []


def test_unverified_bundle_root_blocks_before_any_controlled_host_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry = _Registry(paths)
    _patch_v32_preflight(monkeypatch, registry)
    host_calls: list[str] = []

    class NeverHost:
        def __init__(self, _root: str) -> None:
            host_calls.append("constructed")
            raise AssertionError("the controlled host must not start from an unverified bundle root")

    monkeypatch.setattr(bridge_module, "ControlledBatchViewerReplayHost", NeverHost)
    monkeypatch.setattr(
        bridge_module,
        "load_attested_batch_replay_binding",
        lambda **_kwargs: _binding(registry, _bundle(tmp_path / "unverified-root")),
    )
    monkeypatch.setattr(
        bridge_module,
        "validate_viewer_bundle_provenance",
        lambda _bundle_value: (_ for _ in ()).throw(BatchSceneReplayError("asset_integrity_mismatch")),
    )

    with pytest.raises(bridge_module.ProviderScienceBridgeError, match="bundle provenance"):
        bridge_module._batch_viewer_context(
            paths,
            leaf_id="chemistry.conformation_analysis",
            work_item_id="job-v32-fixture",
            slot_digest=registry.slot_digest,
            viewer_lock_digest=_digest("viewer-lock"),
            selection_batch_digest=registry.selection_batch_digest,
            anonymous_scene_ref=registry.scene_ref,
            allowed_operations=["render", "rotate"],
            capability_mapping={},
            fresh_replay={"registry": {"path": "/private/locked.registry"}},
        )

    assert host_calls == []


def test_v32_handoff_uses_the_locked_bundle_root_and_persists_no_png_or_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry = _Registry(paths)
    _patch_v32_preflight(monkeypatch, registry)
    bundle = _bundle(tmp_path / "locked-v1.2.1")
    roots: list[str] = []
    first_png = b"private-first-render"
    second_png = b"private-second-render"

    class Host:
        def __init__(self, root: str) -> None:
            roots.append(root)

        def capture_provider_views(self, _scene: Any, _operations: list[str]) -> dict[str, Any]:
            return {
                "host_id": "compiled-selection-batch-browser-replay-v1",
                "views": [
                    {
                        "operation": "render",
                        "result_digest": _digest("render-result"),
                        "render_digest": digest_bytes(first_png),
                        "png": first_png,
                    },
                    {
                        "operation": "rotate",
                        "result_digest": _digest("rotate-result"),
                        "render_digest": digest_bytes(second_png),
                        "png": second_png,
                    },
                ],
            }

        def shutdown(self) -> None:
            return

    monkeypatch.setattr(bridge_module, "ControlledBatchViewerReplayHost", Host)
    monkeypatch.setattr(bridge_module, "validate_viewer_bundle_provenance", lambda value: dict(value))
    monkeypatch.setattr(
        bridge_module,
        "load_attested_batch_replay_binding",
        lambda **_kwargs: _binding(registry, bundle),
    )

    viewer_context, handoff = bridge_module._batch_viewer_context(
        paths,
        leaf_id="chemistry.conformation_analysis",
        work_item_id="job-v32-fixture",
        slot_digest=registry.slot_digest,
        viewer_lock_digest=_digest("viewer-lock"),
        selection_batch_digest=registry.selection_batch_digest,
        anonymous_scene_ref=registry.scene_ref,
        allowed_operations=["render", "rotate"],
        capability_mapping={},
        fresh_replay={"registry": {"path": "/private/locked.registry"}},
    )

    assert roots == [bundle["root"]]
    assert all("data_url" in view for view in viewer_context["rendered_views"])
    durable = read_json(Path(handoff["handoff_path"]))
    rendered = json.dumps(durable, sort_keys=True)
    for forbidden in ("data_url", "private-first-render", "private-second-render", '"png"', '"truth"', "sidecar"):
        assert forbidden not in rendered


def test_fresh_replay_loader_rejects_a_selection_digest_mismatch_before_registry_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry = _Registry(paths)
    receipt = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref=registry.scene_ref,
        replay={
            "status": "attested",
            "host_id": "fixture-host",
            "replayed_operations": ["render", "rotate"],
            "evidence": [
                {"operation": "render", "result_digest": _digest("render"), "render_digest": _digest("frame-render")},
                {"operation": "rotate", "result_digest": _digest("rotate"), "render_digest": _digest("frame-rotate")},
            ],
            "blockers": [],
        },
    )
    registry_path = paths.private / "viewer" / "fixture.registry.json"
    atomic_write_json(registry_path, {"fixture": "registry"}, private=True)
    lock_body = {
        "lock_set_version": 2,
        "registry_digest": registry.content_digest,
        "selection_batch_digest": registry.selection_batch_digest,
        "replay_summary_digest": _digest("summary"),
        "total": 1,
        "status_counts": {"attested": 1, "blocked": 0, "failed": 0},
        "blocker_counts": {},
        "slots": [
            {
                "slot_digest": registry.slot_digest,
                "anonymous_scene_ref": registry.scene_ref,
                "viewer_family": "molstar",
                "allowed_operations": ["render", "rotate"],
                "receipt_digest": receipt["content_digest"],
                "receipt_status": "attested",
                "host_id": "fixture-host",
                "host_code_digest": _digest("host"),
                "handoff_capable": True,
                "blockers": [],
            }
        ],
        "prepare_eligible": False,
        "release_eligible": False,
        "viewer_bundle": _bundle(tmp_path / "locked-v1.2.1"),
    }
    lock = make_envelope(
        kind="qagen_private_batch_scene_viewer_lock_set",
        identifier="v32-fixture-lock-set",
        status="viewer_attested_pending_review",
        body=lock_body,
    )
    lock_path = paths.private / "viewer" / "fixture.lock-set.json"
    atomic_write_json(lock_path, lock, private=True)
    registry_loads: list[Path] = []

    def load_registry(_paths: WorkspacePaths, value: str | Path) -> _Registry:
        registry_loads.append(Path(value))
        return registry

    monkeypatch.setattr(replay_module.BatchSceneRegistry, "load", load_registry)
    with pytest.raises(BatchSceneReplayError, match="host_contract_invalid"):
        load_attested_batch_replay_binding(
            paths,
            fresh_replay={
                "lock_set": {"path": str(lock_path), "content_digest": lock["content_digest"]},
                "registry": {"path": str(registry_path), "content_digest": registry.content_digest},
                "receipt": {"path": receipt["path"], "content_digest": receipt["content_digest"]},
            },
            selection_batch_digest=_digest("different-selection"),
            slot_digest=registry.slot_digest,
            anonymous_scene_ref=registry.scene_ref,
            allowed_operations=["render", "rotate"],
        )

    assert registry_loads == []


def test_fresh_replay_loader_accepts_only_an_attested_least_privilege_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry = _Registry(paths)
    receipt = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref=registry.scene_ref,
        replay={
            "status": "attested",
            "host_id": "fixture-host",
            "replayed_operations": ["render", "rotate"],
            "evidence": [
                {"operation": "render", "result_digest": _digest("render"), "render_digest": _digest("frame-render")},
                {"operation": "rotate", "result_digest": _digest("rotate"), "render_digest": _digest("frame-rotate")},
            ],
            "blockers": [],
        },
    )
    registry_path = paths.private / "viewer" / "fixture.registry.json"
    atomic_write_json(registry_path, {"fixture": "registry"}, private=True)
    bundle = _bundle(tmp_path / "locked-v1.2.1")
    lock = make_envelope(
        kind="qagen_private_batch_scene_viewer_lock_set",
        identifier="v32-fixture-subset-lock-set",
        status="viewer_attested_pending_review",
        body={
            "lock_set_version": 2,
            "registry_digest": registry.content_digest,
            "selection_batch_digest": registry.selection_batch_digest,
            "replay_summary_digest": _digest("summary"),
            "total": 1,
            "status_counts": {"attested": 1, "blocked": 0, "failed": 0},
            "blocker_counts": {},
            "slots": [
                {
                    "slot_digest": registry.slot_digest,
                    "anonymous_scene_ref": registry.scene_ref,
                    "viewer_family": "molstar",
                    "allowed_operations": ["render", "rotate"],
                    "receipt_digest": receipt["content_digest"],
                    "receipt_status": "attested",
                    "host_id": "fixture-host",
                    "host_code_digest": _digest("host"),
                    "handoff_capable": True,
                    "blockers": [],
                }
            ],
            "prepare_eligible": False,
            "release_eligible": False,
            "viewer_bundle": bundle,
        },
    )
    lock_path = paths.private / "viewer" / "fixture-subset.lock-set.json"
    atomic_write_json(lock_path, lock, private=True)
    monkeypatch.setattr(replay_module, "validate_viewer_bundle_provenance", lambda value: dict(value))
    monkeypatch.setattr(replay_module.BatchSceneRegistry, "load", lambda *_args, **_kwargs: registry)
    monkeypatch.setattr(_Registry, "viewer_bundle", property(lambda _self: dict(bundle)), raising=False)
    fresh_replay = {
        "lock_set": {"path": str(lock_path), "content_digest": lock["content_digest"]},
        "registry": {"path": str(registry_path), "content_digest": registry.content_digest},
        "receipt": {"path": receipt["path"], "content_digest": receipt["content_digest"]},
    }

    binding = load_attested_batch_replay_binding(
        paths,
        fresh_replay=fresh_replay,
        selection_batch_digest=registry.selection_batch_digest,
        slot_digest=registry.slot_digest,
        anonymous_scene_ref=registry.scene_ref,
        allowed_operations=["render"],
    )

    assert binding["slot"]["allowed_operations"] == ["render", "rotate"]
    with pytest.raises(BatchSceneReplayError, match="host_contract_invalid"):
        load_attested_batch_replay_binding(
            paths,
            fresh_replay=fresh_replay,
            selection_batch_digest=registry.selection_batch_digest,
            slot_digest=registry.slot_digest,
            anonymous_scene_ref=registry.scene_ref,
            allowed_operations=["render", "zoom"],
        )
