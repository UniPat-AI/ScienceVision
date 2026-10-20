from __future__ import annotations

from pathlib import Path
import shutil

import pytest
import yaml

from qagen.config import build_config, load_compiled_config, revalidate_compiled_config
from qagen.config.compiler import ConfigCompilerError
from qagen.contracts import digest, make_decision, make_envelope
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json, atomic_write_yaml

from conftest import CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES, PACKAGE_ROOT, write_json


LEAF_ID = "biology.docking_pose_plausibility"


def _frozen_envelope(kind: str, identifier_prefix: str, status: str, body: dict[str, object]) -> dict[str, object]:
    provisional = make_envelope(kind=kind, identifier=identifier_prefix, status=status, body=body)
    return make_envelope(
        kind=kind,
        identifier=f"{identifier_prefix}-{provisional['content_digest'].split(':', 1)[1][:16]}",
        status=status,
        body=body,
    )


def _write_yaml(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _write_workspace_registry(paths: WorkspacePaths) -> tuple[dict[str, dict[str, str]], str]:
    paths.ensure()
    inventory_digest = digest({"synthetic": "seed-inventory"})
    catalog = make_envelope(
        kind="seed_catalog",
        identifier="synthetic-seed-catalog",
        status="eligible",
        body={
            "inventory_digest": inventory_digest,
            "local_only": False,
            "production_eligible": True,
            "cases": [],
        },
    )
    catalog_path = paths.catalog / "synthetic.catalog.json"
    atomic_write_json(catalog_path, catalog, private=True)
    cards = make_envelope(
        kind="seed_cards",
        identifier="synthetic-seed-cards",
        status="eligible",
        body={"inventory_digest": inventory_digest, "cards": []},
    )
    cards_path = paths.cards / "synthetic.cards.json"
    atomic_write_json(cards_path, cards, private=True)
    seed_index_body = {
        "mode": "workspace_registry",
        "inventory_digest": inventory_digest,
        "seed_catalog_digest": catalog["content_digest"],
        "catalog_path": str(catalog_path.relative_to(paths.root)),
        "cards_digest": cards["content_digest"],
        "cards_path": str(cards_path.relative_to(paths.root)),
        "package_name": "synthetic-production-seed",
        "source_spec_path": "synthetic/seed-source.yaml",
        "source_spec_digest": digest({"synthetic": "seed-source"}),
        "local_only": False,
        "production_eligible": True,
        "observed_versions": {"package": "1.0.0"},
        "resolution_ref": None,
        "eligible": True,
        "lesson_paths": [],
        "entries": [
            {
                "seed_ref": "seed-card-production-biology",
                "leaf_id": LEAF_ID,
                "domain": "biology",
                "subdomain": "docking_pose_plausibility",
                "status": "verified",
                "tags": ["local_spatial_relationship"],
            }
        ],
    }
    seed_index = _frozen_envelope("seed_index", "seed-index", "eligible", seed_index_body)
    seed_index_path = paths.catalog / f"{seed_index['content_digest'].split(':', 1)[1][:16]}.seed-index.json"
    atomic_write_json(seed_index_path, seed_index, private=True)
    # Compatibility aliases remain useful to the knowledge authoring workflow,
    # but no compile profile below is allowed to consume this path.
    atomic_write_json(paths.catalog / "current.json", seed_index, private=True)

    draft_digest = digest({"synthetic": "knowledge-draft"})
    input_digest = digest({"synthetic": "knowledge-input"})
    authorization_digest = digest({"synthetic": "knowledge-authorization"})
    source_digests = {
        "expert:biology-expert": digest({"synthetic": "expert"}),
        "taxonomy:biology-taxonomy": digest({"synthetic": "taxonomy"}),
    }
    review = make_decision(
        kind="knowledge_review_decision",
        identifier="synthetic-knowledge-review",
        decision="approved",
        actor_id="biology-expert",
        actor_role="domain_expert",
        bound_digests={
            "draft_digest": draft_digest,
            "input_digest": input_digest,
            "compile_authorization_ref": authorization_digest,
        },
        rationale="Synthetic immutable production registry integration fixture.",
        extra={
            "snapshot_digest": draft_digest,
            "input_digest": input_digest,
            "compile_authorization_ref": authorization_digest,
        },
    )
    review_path = paths.receipts / "knowledge" / "synthetic.approved.review.json"
    atomic_write_json(review_path, review, private=True)
    snapshot_body = {
        "id": LEAF_ID,
        "status": "approved",
        "provenance": {
            "expert_source_refs": [
                {"source_id": "biology-expert", "content_digest": source_digests["expert:biology-expert"]}
            ],
            "visual_taxonomy_refs": [
                {"source_id": "biology-taxonomy", "content_digest": source_digests["taxonomy:biology-taxonomy"]}
            ],
            "seed_lesson_ref": digest({"synthetic": "seed-lesson"}),
            "compile_authorization_ref": authorization_digest,
            "compiler_digest": digest({"synthetic": "knowledge-compiler"}),
            "input_digest": input_digest,
            "reviewed_draft_digest": draft_digest,
        },
        "scientific_scope": {"included": ["controlled viewer evidence"], "excluded": ["raw asset access"]},
        "visual_target_types": ["local-contact"],
        "scenario_cards": [{"id": "controlled-evidence"}],
        "allowed_inference": ["direct observation"],
        "forbidden_shortcuts": list(CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES),
        "evidence_requirements": {"allowed_truth_modes": ["expert_visual_judgment"]},
        "requirements": {"viewer_requirements": ["inspect"]},
        "common_failure_modes": [],
        "rubric_extensions": [],
        "review": {
            "decision": "approved",
            "reviewer": "biology-expert",
            "reviewed_at": review["body"]["decided_at"],
            "decision_digest": review["content_digest"],
        },
    }
    snapshot = make_envelope(
        kind="subdomain_knowledge_snapshot",
        identifier=LEAF_ID,
        status="approved",
        body=snapshot_body,
        producer="qagen/test:knowledge_freezer",
    )
    snapshot_path = paths.knowledge / "compiled" / "biology" / (
        f"docking_pose_plausibility-{snapshot['content_digest'].split(':', 1)[1][:16]}.snapshot.yaml"
    )
    atomic_write_yaml(snapshot_path, snapshot)
    knowledge_index_body = {
        "mode": "workspace_registry",
        "seed_index_digest": seed_index["content_digest"],
        "seed_catalog_digest": catalog["content_digest"],
        "entries": {
            LEAF_ID: {
                "state": "approved",
                "snapshot_digest": snapshot["content_digest"],
                "snapshot_path": str(snapshot_path.relative_to(paths.root)),
                "review_digest": review["content_digest"],
                "review_path": str(review_path.relative_to(paths.root)),
                "draft_digest": draft_digest,
                "input_digest": input_digest,
                "seed_catalog_digest": catalog["content_digest"],
                "source_digests": source_digests,
                "compiler_digest": snapshot_body["provenance"]["compiler_digest"],
                "local_only": False,
                "production_eligible": True,
            }
        },
    }
    knowledge_index = _frozen_envelope("knowledge_index", "knowledge-index", "ready", knowledge_index_body)
    knowledge_index_path = paths.knowledge / "indexes" / (
        f"{knowledge_index['content_digest'].split(':', 1)[1][:16]}.knowledge-index.yaml"
    )
    atomic_write_yaml(knowledge_index_path, knowledge_index)
    atomic_write_yaml(paths.knowledge / "index.yaml", knowledge_index)
    return (
        {
            "seed_index": {
                "path": str(seed_index_path.relative_to(paths.root)),
                "content_digest": seed_index["content_digest"],
            },
            "knowledge_index": {
                "path": str(knowledge_index_path.relative_to(paths.root)),
                "content_digest": knowledge_index["content_digest"],
            },
        },
        str(snapshot["content_digest"]),
    )


def _write_production_profile(
    tmp_path: Path,
    registry: dict[str, dict[str, str]],
    knowledge_snapshot_digest: str,
) -> Path:
    repo_root = tmp_path / "synthetic-config-repo"
    config_root = repo_root / "configs"
    shutil.copytree(PACKAGE_ROOT / "configs" / "schemas", config_root / "schemas")
    (config_root / "domains").mkdir(parents=True, exist_ok=True)
    (config_root / "agents").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGE_ROOT / "configs" / "base.yaml", config_root / "base.yaml")
    shutil.copy2(PACKAGE_ROOT / "configs" / "domains" / "biology.yaml", config_root / "domains" / "biology.yaml")
    shutil.copy2(
        PACKAGE_ROOT / "configs" / "agents" / "model_routing.yaml",
        config_root / "agents" / "model_routing.yaml",
    )
    rubric_document = repo_root / "QA-Gen" / "docs" / "Question-Rubric.md"
    rubric_document.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGE_ROOT.parent / "docs" / "Question-Rubric.md", rubric_document)
    shutil.copy2(PACKAGE_ROOT / "configs" / "rubric.yaml", config_root / "rubric.yaml")
    asset_payload = {
        "assets": [
            {
                "domain": "biology",
                "subdomain": "docking_pose_plausibility",
                "anonymous_scene_ref": "production-scene-biology-001",
            }
        ]
    }
    viewer_payload = {
        "viewer_id": "controlled-production-viewer",
        "capabilities": ["anonymous_scene", "controlled_session_request", "inspect", "isolate", "rotate"],
        "forbidden_capabilities": CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES,
        "session_scope_mode": "controlled",
    }
    write_json(config_root / "sources" / "assets.json", asset_payload)
    write_json(config_root / "sources" / "viewer.json", viewer_payload)
    _write_yaml(
        config_root / "sources" / "assets.yaml",
        {
            "schema_version": 1,
            "kind": "qagen_asset_manifest_source",
            "id": "production-assets",
            "source_mode": "locked_document",
            "document_path": "assets.json",
            "document_digest": digest(asset_payload),
            "local_only": False,
            "production_eligible": True,
            "reference_owners": [{"repo_path": "configs/sources/assets.json", "purpose": "synthetic_test"}],
        },
    )
    _write_yaml(
        config_root / "sources" / "viewer.yaml",
        {
            "schema_version": 1,
            "kind": "qagen_viewer_definition_source",
            "id": "production-viewer",
            "source_mode": "locked_document",
            "document_path": "viewer.json",
            "document_digest": digest(viewer_payload),
            "local_only": False,
            "production_eligible": True,
            "reference_owners": [{"repo_path": "configs/sources/viewer.json", "purpose": "synthetic_test"}],
        },
    )
    _write_yaml(
        config_root / "subdomains" / "biology" / "docking_pose_plausibility.yaml",
        {
            "schema_version": 1,
            "kind": "qagen_subdomain_config",
            "id": LEAF_ID,
            "domain": "biology",
            "status": "sample_compile_eligible",
            "production_status": "approved",
            "readiness": {
                "config": "config_ready",
                "generation": "eligible",
                "release": "eligible",
            },
            "visual_skills": ["local_spatial_relationship"],
            "seed": {
                "query": {"domain": "biology", "subdomain": "docking_pose_plausibility", "required_status": "verified"},
                "locked_ref": f"seed-catalog@{registry['seed_index']['content_digest']}",
            },
            "asset": {
                "locked_ref": f"production-assets@{digest(asset_payload)}",
                "required_conditions": ["anonymous_scene"],
            },
            "viewer": {
                "locked_ref": f"production-viewer@{digest(viewer_payload)}",
                "required_operations": ["inspect", "isolate", "rotate"],
            },
            "answer": {"allowed_truth_modes": ["expert_visual_judgment"]},
            "evidence": {"required": ["viewer_trace"]},
            "knowledge": {
                "source_refs": [
                    {
                        "source_id": "synthetic-approved-knowledge",
                        "source_revision": 1,
                        "coverage": "source_only",
                    }
                ],
                "locked_refs": [
                    f"knowledge-snapshot:{LEAF_ID}@{knowledge_snapshot_digest}"
                ]
            },
            "release": {"eligible": True, "reason": "Synthetic production approval fixture."},
        },
    )
    profile_path = config_root / "profiles" / "production.yaml"
    _write_yaml(
        profile_path,
        {
            "schema_version": 1,
            "kind": "qagen_run_profile",
            "id": "synthetic_workspace_registry_production",
            "mode": "production_release",
            "domains": ["biology"],
            "enabled_leaves": [LEAF_ID],
            "inputs": {
                "registry_mode": "workspace_registry",
                "workspace_registry": registry,
                "base": "../base.yaml",
                "asset_manifest": "../sources/assets.yaml",
                "viewer_definitions": "../sources/viewer.yaml",
                "rubric": "../rubric.yaml",
                "domain_configs": ["../domains/biology.yaml"],
                "leaf_configs": ["../subdomains/biology/docking_pose_plausibility.yaml"],
            },
            "fixture_policy": {
                "allow_local_fixture_compile": False,
                "require_local_fixture_digest_match": False,
                "require_production_approval": True,
                "production_eligible": True,
            },
            "release": {"enabled": True},
        },
    )
    return profile_path


def _write_seed_only_blocked_profile(
    tmp_path: Path,
    registry: dict[str, dict[str, str]],
    knowledge_snapshot_digest: str,
) -> Path:
    profile = _write_production_profile(tmp_path, registry, knowledge_snapshot_digest)
    config_root = profile.parent.parent
    shutil.copy2(
        PACKAGE_ROOT / "configs" / "agents" / "model_routing.fixture.yaml",
        config_root / "agents" / "model_routing.fixture.yaml",
    )
    document = yaml.safe_load(profile.read_text(encoding="utf-8"))
    document["id"] = "synthetic_workspace_seed_only_blocked"
    document["mode"] = "local_fixture_compile"
    document["enabled_leaves"] = []
    document["blocked_leaves"] = [LEAF_ID]
    document["inputs"]["workspace_registry"] = {"seed_index": registry["seed_index"]}
    document["inputs"]["fixture_routing"] = "../agents/model_routing.fixture.yaml"
    document["fixture_policy"] = {
        "allow_local_fixture_compile": True,
        "require_local_fixture_digest_match": True,
        "require_production_approval": False,
        "production_eligible": False,
    }
    document["release"] = {
        "enabled": False,
        "blocked_reasons": ["knowledge_registry_not_ready"],
    }
    _write_yaml(profile, document)
    return profile


def test_frozen_workspace_registry_inputs_compile_to_production_config(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry, knowledge_snapshot_digest = _write_workspace_registry(paths)
    profile = _write_production_profile(tmp_path, registry, knowledge_snapshot_digest)

    result = build_config(paths, profile_path=profile)
    compiled = load_compiled_config(paths, result["config_path"])

    assert result["status"] == "production_eligible"
    assert result["eligible_leaves"] == [LEAF_ID]
    assert compiled["body"]["release_eligible"] is True
    locks = {lock["label"]: lock for lock in compiled["body"]["input_locks"]}
    assert locks["seed_index"]["artifact_digest"] == registry["seed_index"]["content_digest"]
    assert locks["knowledge_index"]["artifact_digest"] == registry["knowledge_index"]["content_digest"]
    assert revalidate_compiled_config(compiled)["content_digest"] == compiled["content_digest"]


def test_seed_only_blocked_profile_still_validates_and_locks_frozen_seed_registry(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry, knowledge_snapshot_digest = _write_workspace_registry(paths)
    profile = _write_seed_only_blocked_profile(tmp_path, registry, knowledge_snapshot_digest)

    result = build_config(paths, profile_path=profile)
    compiled = load_compiled_config(paths, result["config_path"])

    assert result["status"] == "blocked"
    assert result["eligible_leaves"] == []
    locks = {lock["label"]: lock for lock in compiled["body"]["input_locks"]}
    assert locks["seed_index"]["artifact_digest"] == registry["seed_index"]["content_digest"]
    assert "seed_catalog" in locks
    assert "knowledge_index" not in locks
    blockers = compiled["body"]["leaves"][0]["blockers"]
    assert "workspace_knowledge_index_not_explicit" in blockers
    assert "workspace_seed_index_not_explicit" not in blockers
    assert "seed_query_has_no_verified_positive_locked_record" not in blockers


def test_workspace_registry_maps_fixture_verified_query_and_reports_only_missing_knowledge_gate(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry, knowledge_snapshot_digest = _write_workspace_registry(paths)
    profile = _write_seed_only_blocked_profile(tmp_path, registry, knowledge_snapshot_digest)
    profile_document = yaml.safe_load(profile.read_text(encoding="utf-8"))
    profile_document["enabled_leaves"] = [LEAF_ID]
    profile_document["blocked_leaves"] = []
    _write_yaml(profile, profile_document)
    leaf_path = profile.parent.parent / "subdomains" / "biology" / "docking_pose_plausibility.yaml"
    leaf_document = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
    leaf_document["seed"]["query"]["required_status"] = "fixture_verified"
    _write_yaml(leaf_path, leaf_document)

    result = build_config(paths, profile_path=profile)

    assert result["blocked_leaves"][LEAF_ID] == ["workspace_knowledge_index_not_explicit"]


def test_seed_only_blocked_profile_rejects_drifted_seed_digest(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry, knowledge_snapshot_digest = _write_workspace_registry(paths)
    profile = _write_seed_only_blocked_profile(tmp_path, registry, knowledge_snapshot_digest)
    document = yaml.safe_load(profile.read_text(encoding="utf-8"))
    document["inputs"]["workspace_registry"]["seed_index"]["content_digest"] = "sha256:" + "0" * 64
    _write_yaml(profile, document)

    with pytest.raises(ConfigCompilerError, match="does not match the profile-pinned content_digest"):
        build_config(paths, profile_path=profile)


def test_workspace_registry_profile_rejects_mutable_current_alias(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    registry, knowledge_snapshot_digest = _write_workspace_registry(paths)
    profile = _write_production_profile(tmp_path, registry, knowledge_snapshot_digest)
    document = yaml.safe_load(profile.read_text(encoding="utf-8"))
    document["inputs"]["workspace_registry"]["seed_index"]["path"] = ".qagen/private/catalog/current.json"
    _write_yaml(profile, document)

    with pytest.raises(ConfigCompilerError, match="mutable current alias"):
        build_config(paths, profile_path=profile)
