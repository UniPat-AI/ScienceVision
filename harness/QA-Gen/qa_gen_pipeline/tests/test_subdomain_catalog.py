from __future__ import annotations

import json
from pathlib import Path
import re

from jsonschema import Draft7Validator
import yaml

from conftest import PACKAGE_ROOT


TARGET_LEAVES = {
    "biology": {
        "biology.bio_multi_structure_compare",
        "biology.docking_pose_plausibility",
        "biology.molecular_collision_check",
    },
    "chemistry": {
        "chemistry.stereochemistry_chirality",
        "chemistry.conformation_analysis",
        "chemistry.steric_accessibility",
        "chemistry.coordination_chemistry",
        "chemistry.crystallography_packing",
        "chemistry.reactive_conformation",
    },
    "medical": {
        "medical.lesion_localization",
        "medical.anatomical_region",
        "medical.imaging_feature_assessment",
        "medical.volumetric_target_geometry",
    },
    "architecture": {
        "architecture.concrete_void_localization",
        "architecture.bridge_damage_localization",
        "architecture.main_girder_deformation",
        "architecture.historic_facade_damage_repair",
        "architecture.historic_component_style_classification",
        "architecture.drawing_model_consistency",
        "architecture.utility_network_clash_detection",
        "architecture.cross_floor_alignment_validation",
        "architecture.stair_ramp_spatial_validity",
        "architecture.component_geometry_measurement",
        "architecture.section_elevation_reasoning",
    },
    "mechanical_cad": {
        "mechanical_cad.assembly_interference_clearance",
        "mechanical_cad.assembly_bom_relation",
        "mechanical_cad.manufacturability_draft_undercut",
        "mechanical_cad.drawing_section_consistency",
        "mechanical_cad.drawing_feature_to_3d_region_mapping",
        "mechanical_cad.tool_accessibility",
        "mechanical_cad.gdt_validation",
        "mechanical_cad.motion_envelope",
        "mechanical_cad.pointcloud_assembly_qc",
    },
}

FIXTURE_ENABLED = {
    "biology.docking_pose_plausibility",
    "chemistry.stereochemistry_chirality",
}

EXPECTED_STATUS = {
    "biology.docking_pose_plausibility": "sample_compile_eligible",
    "biology.molecular_collision_check": "conditional",
    "biology.bio_multi_structure_compare": "blocked",
    "chemistry.stereochemistry_chirality": "sample_compile_eligible",
    "chemistry.conformation_analysis": "conditional",
    "chemistry.steric_accessibility": "conditional",
    "chemistry.coordination_chemistry": "conditional",
    "chemistry.crystallography_packing": "blocked",
    "chemistry.reactive_conformation": "blocked",
    "medical.lesion_localization": "blocked",
    "medical.anatomical_region": "blocked",
    "medical.imaging_feature_assessment": "blocked",
    "medical.volumetric_target_geometry": "blocked",
    **{leaf_id: "conditional" for leaf_id in TARGET_LEAVES["architecture"]},
    **{leaf_id: "conditional" for leaf_id in TARGET_LEAVES["mechanical_cad"]},
    "mechanical_cad.gdt_validation": "blocked",
}

PROFILE_COVERAGE = {
    "bio_chem_fixture_compile.yaml": TARGET_LEAVES["biology"] | TARGET_LEAVES["chemistry"],
    "bio_chem_release.yaml": TARGET_LEAVES["biology"] | TARGET_LEAVES["chemistry"],
    "biology_workspace_registry_compile.yaml": TARGET_LEAVES["biology"],
    "ev2_v103_seed_ready.yaml": TARGET_LEAVES["biology"] | TARGET_LEAVES["chemistry"],
    "medical_compile.yaml": TARGET_LEAVES["medical"]
    - {"medical.volumetric_target_geometry"},
    "medical_volumetric_geometry_draft.yaml": {
        "medical.volumetric_target_geometry"
    },
    "architecture_catalog.yaml": TARGET_LEAVES["architecture"],
    "mechanical_cad_catalog.yaml": TARGET_LEAVES["mechanical_cad"],
}

EXPECTED_COUNTS = {
    "biology": 3,
    "chemistry": 6,
    "medical": 4,
    "architecture": 11,
    "mechanical_cad": 9,
}

CHEMISTRY_SOURCE_SECTIONS = {
    "chemistry.stereochemistry_chirality": "doxcnY22ADLaoeX9NEJCyvI7whf",
    "chemistry.conformation_analysis": "doxcngAYfO2FedAzlCvkASCyxDV",
    "chemistry.steric_accessibility": "doxcnD7VkTzM9r1y5tpCTTLmCve",
    "chemistry.coordination_chemistry": "doxcn9KvGi11qTEUyNKROTgfKlb",
    "chemistry.crystallography_packing": "doxcntupYILYu3jGkUiodgAxfxh",
    "chemistry.reactive_conformation": "doxcnjal0eX2710gBrtpPLx2DQd",
}


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _leaf_documents() -> dict[str, tuple[Path, dict]]:
    result: dict[str, tuple[Path, dict]] = {}
    for path in sorted((PACKAGE_ROOT / "configs" / "subdomains").glob("*/*.yaml")):
        value = _yaml(path)
        leaf_id = value["id"]
        assert leaf_id not in result
        result[leaf_id] = (path, value)
    return result


def test_target_catalog_has_one_file_per_leaf_with_consistent_identity() -> None:
    documents = _leaf_documents()
    expected = set().union(*TARGET_LEAVES.values())

    assert set(documents) == expected
    for leaf_id, (path, value) in documents.items():
        domain, subdomain = leaf_id.split(".", 1)
        assert path.parent.name == domain
        assert path.stem == subdomain
        assert value["domain"] == domain
        assert value["seed"]["query"]["domain"] == domain
        assert value["seed"]["query"]["subdomain"] == subdomain
        assert value["status"] == EXPECTED_STATUS[leaf_id]


def test_domain_leaf_counts_are_exact() -> None:
    documents = _leaf_documents()
    assert len(documents) == 33
    assert {
        domain: sum(leaf_id.startswith(f"{domain}.") for leaf_id in documents)
        for domain in TARGET_LEAVES
    } == EXPECTED_COUNTS


def test_every_leaf_validates_against_the_closed_schema() -> None:
    schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "subdomain.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for leaf_id, (_, leaf) in _leaf_documents().items():
        errors = list(Draft7Validator(schema).iter_errors(leaf))
        assert errors == [], (leaf_id, [error.message for error in errors])


def test_readiness_phases_do_not_conflate_config_generation_and_release() -> None:
    for leaf_id, (_, leaf) in _leaf_documents().items():
        assert leaf["readiness"]["config"] == "config_ready"
        expected_generation = "fixture_only" if leaf_id in FIXTURE_ENABLED else "blocked"
        assert leaf["readiness"]["generation"] == expected_generation
        assert leaf["readiness"]["release"] == "blocked"
        assert leaf["release"]["eligible"] is False


def test_domain_catalogs_map_every_target_leaf_once() -> None:
    seen: set[str] = set()
    for domain, expected in TARGET_LEAVES.items():
        config = _yaml(PACKAGE_ROOT / "configs" / "domains" / f"{domain}.yaml")
        catalog = config["catalog_leaves"]
        assert len(catalog) == len(set(catalog))
        assert set(catalog) == expected
        assert all(leaf_id.startswith(f"{domain}.") for leaf_id in catalog)
        assert seen.isdisjoint(catalog)
        seen.update(catalog)

    assert seen == set(EXPECTED_STATUS)


def test_non_runnable_catalog_entries_have_machine_readable_gaps_without_fake_locks() -> None:
    documents = _leaf_documents()
    code_pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    input_pattern = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

    for leaf_id, (_, leaf) in documents.items():
        if leaf_id in FIXTURE_ENABLED:
            continue
        assert leaf["production_status"] == "not_approved"
        assert leaf["release"]["eligible"] is False
        assert leaf["blocked_reasons"]
        assert all(code_pattern.fullmatch(reason) for reason in leaf["blocked_reasons"])
        required_inputs = leaf["required_inputs"]
        assert required_inputs
        assert len({item["id"] for item in required_inputs}) == len(required_inputs)
        assert all(input_pattern.fullmatch(item["id"]) for item in required_inputs)

        if leaf_id == "medical.anatomical_region":
            continue
        assert "locked_ref" not in leaf["seed"]
        assert "locked_ref" not in leaf["asset"]
        assert "locked_ref" not in leaf["viewer"]
        assert leaf["knowledge"]["locked_refs"] == []


def test_versioned_expert_sources_resolve_and_remain_source_only() -> None:
    registry = _yaml(PACKAGE_ROOT / "configs" / "sources" / "knowledge_sources.yaml")
    sources = {source["id"]: source for source in registry["sources"]}
    assert sources["expert-knowledge-master"]["source_revision"] == 298
    assert sources["expert-architecture-mechanical-synthesis"]["source_revision"] == 9
    assert sources["expert-knowledge-master"]["approval_state"] == "unapproved"
    assert sources["expert-architecture-mechanical-synthesis"]["approval_state"] == "unapproved"

    for leaf_id, (_, leaf) in _leaf_documents().items():
        refs = leaf["knowledge"]["source_refs"]
        assert refs, leaf_id
        for ref in refs:
            source = sources[ref["source_id"]]
            assert ref["source_revision"] == source["source_revision"]
            assert ref["coverage"] == "source_only"


def test_all_chemistry_leaves_pin_their_master_document_sections() -> None:
    documents = _leaf_documents()
    for leaf_id, section_id in CHEMISTRY_SOURCE_SECTIONS.items():
        refs = documents[leaf_id][1]["knowledge"]["source_refs"]
        assert refs == [
            {
                "source_id": "expert-knowledge-master",
                "source_revision": 298,
                "section_id": section_id,
                "coverage": "source_only",
            }
        ]


def test_mechanical_cad_has_zero_approved_local_knowledge_snapshots() -> None:
    documents = _leaf_documents()
    for leaf_id in TARGET_LEAVES["mechanical_cad"]:
        leaf = documents[leaf_id][1]
        assert leaf["knowledge"]["locked_refs"] == []
        assert all(ref["coverage"] == "source_only" for ref in leaf["knowledge"]["source_refs"])
        assert leaf["readiness"]["generation"] == "blocked"
        assert leaf["readiness"]["release"] == "blocked"


def test_legacy_aliases_do_not_create_duplicate_catalog_leaves() -> None:
    documents = _leaf_documents()
    aliases = {
        alias: leaf_id
        for leaf_id, (_, leaf) in documents.items()
        for alias in leaf.get("legacy_aliases", [])
    }
    assert len(aliases) == sum(
        len(leaf.get("legacy_aliases", [])) for _, leaf in documents.values()
    )
    assert set(aliases).isdisjoint(documents)
    assert aliases["A1_drawing_model_consistency"] == "architecture.drawing_model_consistency"
    assert aliases["A4_section_elevation_reasoning"] == "architecture.section_elevation_reasoning"
    assert aliases["A5_clash_overlap"] == "architecture.utility_network_clash_detection"


def test_source_only_reference_cannot_make_generation_eligible() -> None:
    schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "subdomain.schema.json").read_text(
            encoding="utf-8"
        )
    )
    leaf = _yaml(
        PACKAGE_ROOT / "configs" / "subdomains" / "chemistry" / "conformation_analysis.yaml"
    )
    leaf["status"] = "sample_compile_eligible"
    leaf["readiness"]["generation"] = "eligible"
    leaf["seed"]["locked_ref"] = "seed-catalog@sha256:" + "1" * 64
    leaf["asset"]["locked_ref"] = "asset-manifest@sha256:" + "2" * 64
    leaf["viewer"]["locked_ref"] = "viewer-profile@sha256:" + "3" * 64
    leaf["knowledge"]["locked_refs"] = [
        "fixture-subdomain-knowledge:chemistry.conformation_analysis@sha256:" + "4" * 64
    ]

    errors = list(Draft7Validator(schema).iter_errors(leaf))
    assert any(list(error.absolute_path) == ["knowledge", "locked_refs"] for error in errors)


def test_profiles_cover_catalog_but_never_enable_conditional_or_blocked_leaves() -> None:
    documents = _leaf_documents()
    all_blocked: set[str] = set()

    for filename, expected in PROFILE_COVERAGE.items():
        profile = _yaml(PACKAGE_ROOT / "configs" / "profiles" / filename)
        enabled = set(profile.get("enabled_leaves", []))
        blocked = set(profile.get("blocked_leaves", []))
        assert enabled.isdisjoint(blocked)
        assert enabled | blocked == expected
        assert enabled <= FIXTURE_ENABLED
        assert all(documents[leaf_id][1]["status"] == "sample_compile_eligible" for leaf_id in enabled)
        all_blocked.update(blocked)

    assert set(EXPECTED_STATUS) - FIXTURE_ENABLED <= all_blocked


def test_architecture_and_cad_profiles_are_structurally_catalog_only() -> None:
    for filename in ("architecture_catalog.yaml", "mechanical_cad_catalog.yaml"):
        profile = _yaml(PACKAGE_ROOT / "configs" / "profiles" / filename)
        assert profile["mode"] == "catalog_only"
        assert profile["inputs"]["registry_mode"] == "catalog_only"
        assert profile["enabled_leaves"] == []
        assert not {
            "source_registry",
            "seed_index",
            "asset_manifest",
            "viewer_definitions",
            "knowledge_index",
            "fixture_routing",
        } & set(profile["inputs"])


def test_catalog_only_leaf_cannot_claim_compile_eligibility_without_real_locks() -> None:
    schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "subdomain.schema.json").read_text(encoding="utf-8")
    )
    leaf = _yaml(
        PACKAGE_ROOT
        / "configs"
        / "subdomains"
        / "biology"
        / "molecular_collision_check.yaml"
    )
    leaf["status"] = "sample_compile_eligible"

    errors = list(Draft7Validator(schema).iter_errors(leaf))
    assert errors
    assert any("locked_ref" in error.message or "non-empty" in error.message for error in errors)


def test_ev2_seed_ready_profile_pins_only_the_frozen_seed_registry() -> None:
    schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "profile.schema.json").read_text(encoding="utf-8")
    )
    profile = _yaml(PACKAGE_ROOT / "configs" / "profiles" / "ev2_v103_seed_ready.yaml")
    workspace_registry = profile["inputs"]["workspace_registry"]

    assert list(workspace_registry) == ["seed_index"]
    assert workspace_registry["seed_index"] == {
        "path": ".qagen/private/catalog/e07bc402f604dd8f.seed-index.json",
        "content_digest": "sha256:e07bc402f604dd8fdcbf2000f07a46d8fe574551f3f423079b1c63f6260b8a57",
    }
    assert profile["enabled_leaves"] == []
    assert profile["release"]["enabled"] is False
    assert list(Draft7Validator(schema).iter_errors(profile)) == []

    profile["mode"] = "production_release"
    profile["fixture_policy"] = {
        "allow_local_fixture_compile": False,
        "require_local_fixture_digest_match": False,
        "require_production_approval": True,
        "production_eligible": True,
    }
    profile["release"] = {"enabled": True}
    errors = list(Draft7Validator(schema).iter_errors(profile))
    assert any(
        list(error.absolute_path) == ["inputs", "workspace_registry"]
        and "'knowledge_index' is a required property" in error.message
        for error in errors
    )


def test_chirality_workspace_profile_pins_verified_local_viewer_artifacts() -> None:
    schema = json.loads(
        (PACKAGE_ROOT / "configs" / "schemas" / "profile.schema.json").read_text(encoding="utf-8")
    )
    profile = _yaml(PACKAGE_ROOT / "configs" / "profiles" / "chirality_workspace_knowledge_pilot.yaml")
    workspace_registry = profile["inputs"]["workspace_registry"]

    assert workspace_registry["scene_registry"] == {
        "path": ".qagen/private/viewer/scene-registries/registry_3e4fd505bd2583ca77976d67.json",
        "content_digest": "sha256:5751d9b50b03547ea5e84f56ba88e5ec55c6f3cd7cb85441d17eeab2927cf098",
    }
    assert workspace_registry["viewer_profile"] == {
        "path": ".qagen/private/viewer-profiles/viewer-profile-b487b742c6b7-5574f13f2977-9107d8c88c9a56ad.json",
        "content_digest": "sha256:9107d8c88c9a56adfefdd1976cb6819b4540b07e7d331ea0ba409f4c89b08255",
    }
    assert workspace_registry["knowledge_approval"] == {
        "state": "pending_human_approval",
        "leaf_id": "chemistry.stereochemistry_chirality",
    }
    assert profile["fixture_policy"]["production_eligible"] is False
    assert profile["release"] == {
        "enabled": False,
        "blocked_reasons": [
            "knowledge_human_approval_pending",
            "local_provider_pilot_never_release_eligible",
        ],
    }
    assert list(Draft7Validator(schema).iter_errors(profile)) == []

    workspace_registry["viewer_profile"] = {"state": "pending"}
    errors = list(Draft7Validator(schema).iter_errors(profile))
    assert any(list(error.absolute_path) == ["inputs", "workspace_registry", "viewer_profile"] for error in errors)
