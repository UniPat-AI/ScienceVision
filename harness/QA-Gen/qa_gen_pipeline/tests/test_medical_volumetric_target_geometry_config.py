from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft7Validator
import yaml

from qagen.config import build_config, load_compiled_config
from qagen.contracts import digest, digest_bytes
from qagen.knowledge import sync_knowledge_sources
from qagen.paths import WorkspacePaths

from conftest import PACKAGE_ROOT


LEAF_ID = "medical.volumetric_target_geometry"
SOURCE_ID = "medical-volumetric-target-geometry-expert-draft"
FORBIDDEN_SOLVER_CAPABILITIES = {
    "network",
    "asset_file_read",
    "raw_asset_metadata",
    "raw_asset_read",
    "arbitrary_compute",
    "unrestricted_compute",
}


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_volumetric_target_geometry_leaf_is_closed_and_blocked() -> None:
    leaf_path = (
        PACKAGE_ROOT
        / "configs"
        / "subdomains"
        / "medical"
        / "volumetric_target_geometry.yaml"
    )
    leaf = _yaml(leaf_path)
    schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "subdomain.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert list(Draft7Validator(schema).iter_errors(leaf)) == []
    assert leaf["id"] == LEAF_ID
    assert leaf["status"] == "blocked"
    assert leaf["production_status"] == "not_approved"
    assert leaf["readiness"] == {
        "config": "config_ready",
        "generation": "blocked",
        "release": "blocked",
    }
    assert leaf["knowledge"]["locked_refs"] == []
    assert leaf["release"]["eligible"] is False
    assert set(leaf["blocked_reasons"]) == {
        "medical_domain_release_blocked",
        "repaired_expert_reviewed_seed_missing",
        "expert_knowledge_approval_missing",
        "viewer_capability_evidence_missing",
        "selection_mapping_approval_missing",
        "m3d_refseg_semantic_annotation_source_missing",
    }
    assert leaf["question_type_policy"]["measurement_default_allowed"] is False
    assert leaf["asset_facets"] == {
        "tracked_dimensions": ["body_region", "organ_system", "anatomy", "pathology", "modality", "dataset"],
        "coverage_dedupe_key": ["dataset", "normalized_asset_facets", "question_kernel"],
        "family_diversity_required": True,
        "label_identity_policy": "preserve",
    }
    assert leaf["semantic_gt"]["solver_projection"]["excluded_artifacts"] == ["segmentation", "mask", "overlay"]


def test_medical_domain_keeps_solver_operations_without_overlay_or_bbox_shortcuts() -> None:
    domain = _yaml(PACKAGE_ROOT / "configs" / "domains" / "medical.yaml")
    leaf = _yaml(
        PACKAGE_ROOT
        / "configs"
        / "subdomains"
        / "medical"
        / "volumetric_target_geometry.yaml"
    )

    assert LEAF_ID in domain["catalog_leaves"]
    domain_schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "domain.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(Draft7Validator(domain_schema).iter_errors(domain)) == []
    assert "window_level" in set(domain["policy"]["allowed_viewer_operations"])
    assert "bbox" not in set(domain["policy"]["allowed_viewer_operations"])
    assert "overlay_compare" not in set(domain["policy"]["allowed_viewer_operations"])
    assert set(leaf["viewer"]["required_operations"]) <= set(
        domain["policy"]["allowed_viewer_operations"]
    )
    assert FORBIDDEN_SOLVER_CAPABILITIES <= set(
        domain["policy"]["forbidden_capabilities"]
    )
    serialized = json.dumps(leaf, sort_keys=True)
    assert "asset_file_read" not in serialized
    assert "raw_asset_metadata" not in serialized
    assert "raw_asset_read" not in serialized
    assert "arbitrary_compute" not in serialized


def test_expert_source_and_snapshot_are_unapproved_and_answer_free() -> None:
    source_path = (
        PACKAGE_ROOT
        / "configs"
        / "sources"
        / "knowledge"
        / "medical_volumetric_target_geometry.expert-draft.yaml"
    )
    fixture_path = (
        PACKAGE_ROOT
        / "knowledge"
        / "compiled"
        / "medical.volumetric_target_geometry.fixture.yaml"
    )
    source = _yaml(source_path)
    fixture = _yaml(fixture_path)

    assert source["id"] == SOURCE_ID
    assert source["leaf_id"] == LEAF_ID
    assert source["source_status"] == "draft_reference"
    assert source["production_approval"] is False
    assert fixture["id"] == LEAF_ID
    assert fixture["fixture"]["production_approval"] is False
    assert fixture["fixture"]["approval_receipt"] is None
    assert fixture["fixture_payload"]["state"] == "blocked"
    assert fixture["fixture_payload"]["production_approval"] is False
    assert fixture["fixture_digest"] == digest(fixture["fixture_payload"])

    serialized = json.dumps({"source": source, "fixture": fixture}, sort_keys=True)
    for forbidden_key in (
        '"reference_answer"',
        '"solving_approach"',
        '"compute_contract"',
        '"selection_hints"',
        '"approval_receipt":"sha256:',
    ):
        assert forbidden_key not in serialized

    registry = _yaml(PACKAGE_ROOT / "configs" / "sources" / "knowledge_sources.yaml")
    source_entry = next(entry for entry in registry["sources"] if entry["id"] == SOURCE_ID)
    assert source_entry["content_digest"] == digest_bytes(source_path.read_bytes())

    index = _yaml(PACKAGE_ROOT / "knowledge" / "index.yaml")
    index_entry = next(entry for entry in index["entries"] if entry["leaf_id"] == LEAF_ID)
    assert index_entry["state"] == "fixture_blocked"
    assert index_entry["local_fixture"] is True
    assert index_entry["production_eligible"] is False
    assert index_entry["approval_receipt"] is None
    assert index_entry["digest"] == fixture["fixture_digest"]


def test_expert_draft_syncs_as_a_structured_unapproved_source(tmp_path: Path) -> None:
    source_path = (
        PACKAGE_ROOT
        / "configs"
        / "sources"
        / "knowledge"
        / "medical_volumetric_target_geometry.expert-draft.yaml"
    )
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result = sync_knowledge_sources(paths, [f"expert:{SOURCE_ID}:{source_path}"])
    manifest = _yaml(Path(result["source_manifest_path"]))
    structured = manifest["body"]["sources"][f"expert:{SOURCE_ID}"][
        "structured_source"
    ]

    assert structured["leaf_id"] == LEAF_ID
    assert structured["source_status"] == "draft_reference"
    assert structured["production_approval"] is False


def test_draft_profile_compiles_to_an_explicitly_blocked_leaf(tmp_path: Path) -> None:
    profile = PACKAGE_ROOT / "configs" / "profiles" / "medical_volumetric_geometry_draft.yaml"
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result = build_config(paths, profile_path=profile)
    compiled = load_compiled_config(paths, result["config_path"])

    assert result["status"] == "blocked"
    assert result["eligible_leaves"] == []
    assert compiled["body"]["release_eligible"] is False
    assert len(compiled["body"]["leaves"]) == 1
    compiled_leaf = compiled["body"]["leaves"][0]
    assert compiled_leaf["leaf_id"] == LEAF_ID
    assert {
        "catalog_only_profile",
        "leaf_explicitly_blocked_by_profile",
        "leaf_not_enabled_by_profile",
        "leaf_status_not_compile_eligible",
        "medical_or_domain_blocked",
    } <= set(compiled_leaf["blockers"])
