from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qagen.config import build_config, load_compiled_config, revalidate_compiled_config
from qagen.contracts import digest
from qagen.errors import StaleInputError
from qagen.paths import WorkspacePaths
from qagen.viewer import (
    ViewerProfileError,
    build_viewer_profile,
    load_viewer_profile,
    revalidate_viewer_profile,
)

from conftest import (
    CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES,
    PACKAGE_ROOT,
    profile_path,
    write_controlled_viewer_evidence,
    write_json,
)


def _compile(paths: WorkspacePaths, profile_name: str) -> tuple[dict[str, object], dict[str, object]]:
    result = build_config(paths, profile_path=profile_path(profile_name))
    return result, load_compiled_config(paths, result["config_path"])


def test_fixture_compile_profile_is_stage_only_and_revalidates(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result, compiled = _compile(paths, "bio_chem_fixture_compile")

    assert result["status"] == "fixture_stage_only"
    assert result["eligible_leaves"] == [
        "biology.docking_pose_plausibility",
        "chemistry.stereochemistry_chirality",
    ]
    assert compiled["body"]["release_eligible"] is False
    assert revalidate_compiled_config(compiled)["content_digest"] == compiled["content_digest"]


def test_production_profile_with_fixture_inputs_is_blocked(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result, compiled = _compile(paths, "bio_chem_release")

    assert result["status"] == "blocked"
    assert result["eligible_leaves"] == []
    assert compiled["body"]["release_eligible"] is False
    for leaf in compiled["body"]["leaves"]:
        assert "fixture_inputs_cannot_satisfy_production_release" in leaf["blockers"]


def test_medical_profile_remains_explicitly_blocked(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result, compiled = _compile(paths, "medical_compile")

    assert result["status"] == "blocked"
    assert result["eligible_leaves"] == []
    leaf = compiled["body"]["leaves"][0]
    assert leaf["leaf_id"] == "medical.anatomical_region"
    assert "medical_or_domain_blocked" in leaf["blockers"]
    assert "leaf_not_enabled_by_profile" in leaf["blockers"]


@pytest.mark.parametrize(
    ("profile_name", "expected_count"),
    [("architecture_catalog", 11), ("mechanical_cad_catalog", 9)],
)
def test_catalog_only_profiles_compile_with_no_eligible_leaves(
    tmp_path: Path, profile_name: str, expected_count: int
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result, compiled = _compile(paths, profile_name)

    assert result["status"] == "blocked"
    assert result["eligible_leaves"] == []
    assert len(compiled["body"]["leaves"]) == expected_count
    for leaf in compiled["body"]["leaves"]:
        assert "catalog_only_profile" in leaf["blockers"]
        assert "leaf_not_enabled_by_profile" in leaf["blockers"]
        assert "leaf_status_not_compile_eligible" in leaf["blockers"]


def test_compiled_config_locks_the_model_routing_policy(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    _, compiled = _compile(paths, "bio_chem_fixture_compile")

    routing_path = PACKAGE_ROOT / "configs" / "agents" / "model_routing.yaml"
    routing = yaml.safe_load(routing_path.read_text(encoding="utf-8"))
    expected_digest = digest(routing)
    assert compiled["body"]["lock_digests"]["model_routing_digest"] == expected_digest
    routing_lock = next(lock for lock in compiled["body"]["input_locks"] if lock["label"] == "model_routing")
    assert routing_lock["path"] == str(routing_path)
    assert routing_lock["digest"] == expected_digest


def test_chirality_pilot_profile_explicitly_locks_local_provider_routing(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    result, compiled = _compile(paths, "chirality_provider_pilot")

    routing_path = PACKAGE_ROOT / "configs" / "agents" / "model_routing.pilot.yaml"
    expected_digest = digest(yaml.safe_load(routing_path.read_text(encoding="utf-8")))
    assert result["status"] == "local_provider_pilot_ready"
    assert compiled["body"]["release_eligible"] is False
    assert compiled["body"]["model_routing"] == {
        "path": str(routing_path),
        "digest": expected_digest,
        "execution_eligible": True,
    }
    runtime_path = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.pilot.yaml"
    runtime_digest = digest(yaml.safe_load(runtime_path.read_text(encoding="utf-8")))
    assert compiled["body"]["provider_runtime"] == {
        "path": str(runtime_path),
        "digest": runtime_digest,
        "production_eligible": False,
        "endpoint_env": "QAGEN_UNIV_BASE_URL",
        "endpoint_approval_path_env": "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH",
    }
    assert compiled["body"]["lock_digests"]["provider_runtime_digest"] == runtime_digest


def test_compiled_config_freezes_rubric_thresholds_and_lock(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")

    _, compiled = _compile(paths, "bio_chem_fixture_compile")

    rubric_path = PACKAGE_ROOT / "configs" / "rubric.yaml"
    rubric = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    frozen = compiled["body"]["rubric"]
    assert frozen["lock_digest"] == digest(rubric)
    assert compiled["body"]["lock_digests"]["rubric_lock_digest"] == digest(rubric)
    assert frozen["release_thresholds"] == rubric["release_thresholds"]
    assert frozen["document_digest"] == rubric["document"]["digest"]


def test_viewer_profile_requires_bound_evidence_and_detects_drift(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    _, compiled = _compile(paths, "bio_chem_fixture_compile")
    definition_digest = compiled["body"]["leaves"][0]["locks"]["viewer_definition_digest"]
    evidence_path = tmp_path / "viewer-session-evidence.json"

    write_controlled_viewer_evidence(evidence_path, definition_digest=definition_digest)
    result = build_viewer_profile(
        paths,
        definition_path=PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml",
        session_evidence_path=evidence_path,
    )
    profile = load_viewer_profile(paths, result["viewer_profile_path"])
    assert profile["body"]["definition_digest"] == definition_digest
    assert revalidate_viewer_profile(profile)["content_digest"] == profile["content_digest"]

    write_controlled_viewer_evidence(
        evidence_path,
        definition_digest=definition_digest,
        allowed_operations=["inspect", "isolate", "rotate", "zoom"],
    )
    with pytest.raises(StaleInputError, match="session evidence lock changed"):
        revalidate_viewer_profile(profile)


def test_viewer_profile_rejects_evidence_for_another_definition(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    evidence_path = tmp_path / "viewer-session-evidence.json"
    write_controlled_viewer_evidence(
        evidence_path,
        definition_digest="sha256:" + "0" * 64,
    )

    with pytest.raises(ViewerProfileError, match="bound to another definition"):
        build_viewer_profile(
            paths,
            definition_path=PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml",
            session_evidence_path=evidence_path,
        )


@pytest.mark.parametrize("missing_capability", CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES)
def test_viewer_profile_requires_every_forbidden_capability_in_session_evidence(
    tmp_path: Path,
    missing_capability: str,
) -> None:
    definition = yaml.safe_load(
        (PACKAGE_ROOT / "configs" / "fixtures" / "viewer-definition.fixture.json").read_text(encoding="utf-8")
    )
    evidence_path = tmp_path / "viewer-session-evidence.json"
    write_json(
        evidence_path,
        {
            "viewer_definition_digest": definition["fixture_payload_digest"],
            "allowed_operations": ["inspect"],
            "forbidden_capabilities": [
                capability
                for capability in CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES
                if capability != missing_capability
            ],
            "network_access": False,
            "asset_file_read": False,
        },
    )

    with pytest.raises(ViewerProfileError, match="must explicitly forbid"):
        build_viewer_profile(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            definition_path=PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml",
            session_evidence_path=evidence_path,
        )


@pytest.mark.parametrize("permitted_field", ["network_access", "asset_file_read"])
def test_viewer_profile_rejects_evidence_that_enables_forbidden_access(
    tmp_path: Path,
    permitted_field: str,
) -> None:
    definition = yaml.safe_load(
        (PACKAGE_ROOT / "configs" / "fixtures" / "viewer-definition.fixture.json").read_text(encoding="utf-8")
    )
    evidence_path = tmp_path / "viewer-session-evidence.json"
    evidence = {
        "viewer_definition_digest": definition["fixture_payload_digest"],
        "allowed_operations": ["inspect"],
        "forbidden_capabilities": CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES,
        "network_access": False,
        "asset_file_read": False,
    }
    evidence[permitted_field] = True
    write_json(evidence_path, evidence)

    with pytest.raises(ViewerProfileError, match="permits a forbidden capability"):
        build_viewer_profile(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            definition_path=PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml",
            session_evidence_path=evidence_path,
        )


def test_viewer_profile_rejects_a_forbidden_operation_even_when_declared_forbidden(tmp_path: Path) -> None:
    definition = yaml.safe_load(
        (PACKAGE_ROOT / "configs" / "fixtures" / "viewer-definition.fixture.json").read_text(encoding="utf-8")
    )
    evidence_path = tmp_path / "viewer-session-evidence.json"
    write_json(
        evidence_path,
        {
            "viewer_definition_digest": definition["fixture_payload_digest"],
            "allowed_operations": ["inspect", "raw_asset_read"],
            "forbidden_capabilities": CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES,
            "network_access": False,
            "asset_file_read": False,
        },
    )

    with pytest.raises(ViewerProfileError, match="operations unavailable"):
        build_viewer_profile(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            definition_path=PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml",
            session_evidence_path=evidence_path,
        )
