from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

from jsonschema import Draft7Validator
import pytest
import yaml

from qagen.config import build_config, load_compiled_config
from qagen.contracts import digest
from qagen.paths import WorkspacePaths

from conftest import PACKAGE_ROOT


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _schema(name: str) -> dict:
    value = json.loads((PACKAGE_ROOT / "configs" / "schemas" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _errors(schema_name: str, value: dict) -> list[object]:
    return list(Draft7Validator(_schema(schema_name)).iter_errors(value))


@pytest.mark.parametrize(
    ("schema_name", "config_path"),
    [
        ("base.schema.json", PACKAGE_ROOT / "configs" / "base.yaml"),
        ("rubric.schema.json", PACKAGE_ROOT / "configs" / "rubric.yaml"),
        ("domain.schema.json", PACKAGE_ROOT / "configs" / "domains" / "biology.yaml"),
        ("model_routing.schema.json", PACKAGE_ROOT / "configs" / "agents" / "model_routing.yaml"),
        ("model_routing.schema.json", PACKAGE_ROOT / "configs" / "agents" / "model_routing.pilot.yaml"),
        ("model_routing.schema.json", PACKAGE_ROOT / "configs" / "agents" / "model_routing.fixture.yaml"),
        ("provider_runtime.schema.json", PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.example.yaml"),
        ("provider_runtime.schema.json", PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.pilot.yaml"),
        ("viewer.schema.json", PACKAGE_ROOT / "configs" / "fixtures" / "viewer-definition.fixture.json"),
        (
            "viewer.schema.json",
            PACKAGE_ROOT / "configs" / "fixtures" / "molstar-chirality-pilot-viewer-definition.fixture.json",
        ),
        ("source.schema.json", PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml"),
        ("source.schema.json", PACKAGE_ROOT / "configs" / "sources" / "molstar_chirality_pilot_viewer.yaml"),
        (
            "subdomain.schema.json",
            PACKAGE_ROOT / "configs" / "pilots" / "chirality" / "stereochemistry_chirality.workspace.yaml",
        ),
    ],
)
def test_checked_in_policy_documents_satisfy_closed_schemas(schema_name: str, config_path: Path) -> None:
    assert _errors(schema_name, _yaml(config_path)) == []


def test_provider_runtime_has_no_checked_in_endpoint_or_insecure_transport_escape_hatch() -> None:
    runtime_paths = (
        PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.example.yaml",
        PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.pilot.yaml",
    )
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in runtime_paths)

    assert "http://" not in serialized
    insecure_opt_in_env = "QAGEN_ALLOW_" + "INSECURE_HTTP"
    assert insecure_opt_in_env not in serialized
    assert "QAGEN_UNIV_BASE_URL" in serialized
    assert "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH" in serialized


def test_source_distribution_policy_excludes_private_runtime_artifacts() -> None:
    pyproject = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.hatch.build.targets.sdist]" in pyproject
    for excluded in (
        "/.qagen/**",
        "/.pytest_cache/**",
        "/.ruff_cache/**",
        "/docs/**",
        "/tests/**",
        "**/__pycache__/**",
        "**/*.pyc",
    ):
        assert excluded in pyproject


@pytest.mark.parametrize(
    ("schema_name", "config_path", "object_path"),
    [
        ("base.schema.json", PACKAGE_ROOT / "configs" / "base.yaml", ("policy",)),
        ("base.schema.json", PACKAGE_ROOT / "configs" / "base.yaml", ("policy", "author_compute_exception")),
        ("base.schema.json", PACKAGE_ROOT / "configs" / "base.yaml", ("quality",)),
        ("base.schema.json", PACKAGE_ROOT / "configs" / "base.yaml", ("budget",)),
        ("base.schema.json", PACKAGE_ROOT / "configs" / "base.yaml", ("fixture_policy",)),
        ("rubric.schema.json", PACKAGE_ROOT / "configs" / "rubric.yaml", ("document",)),
        ("rubric.schema.json", PACKAGE_ROOT / "configs" / "rubric.yaml", ("release_thresholds",)),
        (
            "rubric.schema.json",
            PACKAGE_ROOT / "configs" / "rubric.yaml",
            ("release_thresholds", "authenticity"),
        ),
        ("rubric.schema.json", PACKAGE_ROOT / "configs" / "rubric.yaml", ("fixture_policy",)),
        ("domain.schema.json", PACKAGE_ROOT / "configs" / "domains" / "biology.yaml", ("policy",)),
        ("domain.schema.json", PACKAGE_ROOT / "configs" / "domains" / "biology.yaml", ("viewer_defaults",)),
    ],
)
def test_policy_schemas_reject_empty_required_objects(
    schema_name: str,
    config_path: Path,
    object_path: tuple[str, ...],
) -> None:
    value = deepcopy(_yaml(config_path))
    target = value
    for component in object_path[:-1]:
        target = target[component]
    target[object_path[-1]] = {}

    assert _errors(schema_name, value)


def test_viewer_schema_requires_the_complete_forbidden_capability_set() -> None:
    value = _yaml(PACKAGE_ROOT / "configs" / "fixtures" / "viewer-definition.fixture.json")
    value["fixture_payload"]["forbidden_capabilities"].remove("raw_asset_read")

    assert _errors("viewer.schema.json", value)


def test_viewer_fixture_digest_is_propagated_to_source_and_leaf_locks() -> None:
    fixture = _yaml(PACKAGE_ROOT / "configs" / "fixtures" / "viewer-definition.fixture.json")
    expected_digest = digest(fixture["fixture_payload"])
    assert fixture["fixture_payload_digest"] == expected_digest

    source = _yaml(PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml")
    assert source["fixture_payload_digest"] == expected_digest

    for path in sorted((PACKAGE_ROOT / "configs" / "subdomains").glob("*/*.yaml")):
        leaf = _yaml(path)
        locked_ref = leaf["viewer"].get("locked_ref")
        if locked_ref is not None:
            assert locked_ref == f"fixture-viewer-definition@{expected_digest}"
        else:
            assert leaf["status"] != "sample_compile_eligible"


def test_chirality_workspace_leaf_uses_profile_binding_without_private_selectors() -> None:
    path = PACKAGE_ROOT / "configs" / "pilots" / "chirality" / "stereochemistry_chirality.workspace.yaml"
    leaf = _yaml(path)
    serialized = json.dumps(leaf, sort_keys=True)

    assert _errors("subdomain.schema.json", leaf) == []
    assert leaf["workspace_binding"] == {"kind": "chirality_scene_registry_and_viewer_profile"}
    assert leaf["viewer"] == {"required_operations": ["rotate", "render"]}
    assert leaf["seed"]["query"]["required_status"] == "verified"
    assert leaf["knowledge"]["locked_refs"] == []
    assert leaf["blocked_reasons"] == ["knowledge_human_approval_pending"]
    assert leaf["required_inputs"] == [{"id": "knowledge.human_approval", "state": "missing"}]
    assert leaf["release"]["eligible"] is False
    assert "locked_ref" not in leaf["seed"]
    assert "locked_ref" not in leaf["asset"]
    assert "locked_ref" not in leaf["viewer"]
    for forbidden in ("seed_task_id", "asset_path", "sha256:", "/Users/"):
        assert forbidden not in serialized


def test_ev2_source_spec_locks_the_audited_local_package_without_resolving_its_version_conflict() -> None:
    spec = _yaml(PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml")

    assert spec["root_env"] == "QAGEN_EV2_SOURCE_ROOT"
    assert spec["expected_basename"] == "ev2-pilot-sample-cases-by-type-20260820-v1.0.3-dsstore-fix"
    assert spec["expected_inventory_digest"] == (
        "sha256:063b0347fe3a72704ccb23aab81ccc0414e9d12bed3b3cba9eec901ee2f73e1a"
    )
    assert spec["version_policy"] == "require_human_resolution_on_conflict"
    assert spec["expected_package"]["package_id"] == (
        "ev2-pilot-sample-cases-by-type-20260820-v1.0.3-dsstore-fix"
    )
    assert spec["expected_package"]["version_labels"] == {
        "directory": "v1.0.3",
        "manifest": "v1.0.3",
        "package_id": "v1.0.3",
        "version_note": "v1.0.2",
    }
    assert spec["taxonomy_aliases"] == {
        "path": "aliases/ev2_subdomain_aliases.yaml",
        "version": 1,
        "content_digest": "sha256:b8a180f785052e19e44e73a9df699c47970fd669a1c426bea1480a69a709c0bf",
    }
    assert spec["expected_package"]["case_count"] == 15
    assert spec["expected_package"]["review_distribution"] == {
        "verified": 3,
        "minor_fix": 2,
        "factual_error": 6,
        "unscorable": 4,
    }
    assert set(spec["file_roles"]) == {
        "manifest",
        "version_note",
        "tasks",
        "expert_review",
        "asset_manifest",
    }
    assert spec["local_only"] is True
    assert spec["production_eligible"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("root_env", "not-an-env-name"),
        lambda value: value.__setitem__("production_eligible", True),
        lambda value: value.__setitem__("expected_package", {}),
        lambda value: value["file_roles"].pop("expert_review"),
        lambda value: value["file_roles"]["tasks"].__setitem__("path", "../tasks.jsonl"),
        lambda value: value["expected_package"].__setitem__("provenance", {}),
        lambda value: value["expected_package"]["review_distribution"].pop("unscorable"),
    ],
)
def test_seed_source_spec_schema_rejects_unsafe_or_incomplete_contracts(mutate: object) -> None:
    value = deepcopy(_yaml(PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml"))
    mutate(value)

    assert _errors("source.schema.json", value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("run_eligibility", "eligible"),
        lambda value: value["providers"]["fixture-author"].__setitem__("local_only", False),
        lambda value: value["fixture_adapter"].__setitem__("production_eligible", True),
        lambda value: value["roles"]["author"].__setitem__("provider", "openai"),
        lambda value: value["roles"]["verifier"].__setitem__("allowed_models", ["deterministic-fixture"]),
    ],
)
def test_fixture_routing_schema_rejects_production_or_cross_role_relaxation(mutate: object) -> None:
    value = deepcopy(_yaml(PACKAGE_ROOT / "configs" / "agents" / "model_routing.fixture.yaml"))
    mutate(value)

    assert _errors("model_routing.schema.json", value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("production_eligible", True),
        lambda value: value["providers"]["anthropic"].__setitem__("credential_ref", "env:OTHER_KEY"),
        lambda value: value["providers"]["deepseek"].__setitem__("local_only", False),
        lambda value: value["roles"]["brief_builder"].__setitem__("allowed_models", ["opus-5"]),
        lambda value: value["roles"]["verifier"].__setitem__("provider", "deepseek"),
    ],
)
def test_provider_pilot_routing_schema_rejects_production_or_route_relaxation(mutate: object) -> None:
    value = deepcopy(_yaml(PACKAGE_ROOT / "configs" / "agents" / "model_routing.pilot.yaml"))
    mutate(value)

    assert _errors("model_routing.schema.json", value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["budget"].__setitem__("max_candidates_per_leaf", 0),
        lambda value: value["budget"].__setitem__("max_revisions", 2),
        lambda value: value["policy"]["solver_forbidden_capabilities"].pop(),
        lambda value: value["quality"].__setitem__("require_reasoning", False),
        lambda value: value["fixture_policy"].__setitem__("production_eligible", True),
    ],
)
def test_base_schema_rejects_relaxed_safety_policy(mutate: object) -> None:
    value = deepcopy(_yaml(PACKAGE_ROOT / "configs" / "base.yaml"))
    mutate(value)

    assert _errors("base.schema.json", value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["release_thresholds"]["authenticity"].__setitem__("required", 1),
        lambda value: value["release_thresholds"]["correctness"].__setitem__("maximum", 2),
        lambda value: value["release_thresholds"]["three_d_relevance"].__setitem__("required", 0),
        lambda value: value["fixture_policy"].__setitem__("fixture_compile_is_release_eligible", True),
        lambda value: value["fixture_policy"].__setitem__("local_only", False),
    ],
)
def test_rubric_schema_rejects_threshold_or_fixture_policy_relaxation(mutate: object) -> None:
    value = deepcopy(_yaml(PACKAGE_ROOT / "configs" / "rubric.yaml"))
    mutate(value)

    assert _errors("rubric.schema.json", value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["policy"].__setitem__("allowed_viewer_operations", []),
        lambda value: value["policy"]["forbidden_capabilities"].pop(),
        lambda value: value["viewer_defaults"].__setitem__("required_capabilities", []),
        lambda value: value["viewer_defaults"].__setitem__("required_capabilities", ["anonymous_scene"]),
    ],
)
def test_domain_schema_rejects_relaxed_viewer_controls(mutate: object) -> None:
    value = deepcopy(_yaml(PACKAGE_ROOT / "configs" / "domains" / "biology.yaml"))
    mutate(value)

    assert _errors("domain.schema.json", value)


def _copy_config_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "config-repository"
    shutil.copytree(PACKAGE_ROOT / "configs", repository / "configs")
    shutil.copytree(PACKAGE_ROOT / "knowledge", repository / "knowledge")
    rubric_source = PACKAGE_ROOT.parent / "docs" / "Question-Rubric.md"
    target = repository / "QA-Gen" / "docs" / "Question-Rubric.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rubric_source, target)
    return repository


def test_child_leaf_cannot_expand_its_domain_viewer_operation_allowlist(tmp_path: Path) -> None:
    repository = _copy_config_repository(tmp_path)
    chemistry_leaf_path = repository / "configs" / "subdomains" / "chemistry" / "stereochemistry_chirality.yaml"
    chemistry_leaf = _yaml(chemistry_leaf_path)
    chemistry_leaf["viewer"]["required_operations"] = ["inspect", "isolate", "rotate"]
    chemistry_leaf_path.write_text(yaml.safe_dump(chemistry_leaf, sort_keys=False), encoding="utf-8")

    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    result = build_config(paths, profile_path=repository / "configs" / "profiles" / "bio_chem_fixture_compile.yaml")
    compiled = load_compiled_config(paths, result["config_path"])
    chemistry_leaf_state = next(
        leaf for leaf in compiled["body"]["leaves"] if leaf["leaf_id"] == "chemistry.stereochemistry_chirality"
    )

    assert chemistry_leaf_state["status"] == "blocked"
