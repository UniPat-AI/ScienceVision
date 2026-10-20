from __future__ import annotations

from pathlib import Path

import qagen.resource_paths as resource_paths
from qagen.resource_paths import (
    config_path,
    config_root,
    knowledge_path,
    knowledge_root,
    repository_reference_path,
)


def test_checked_in_resource_paths_resolve_required_runtime_documents() -> None:
    assert config_root().name == "configs"
    assert knowledge_root().name == "knowledge"
    assert config_path("schemas", "candidate.schema.json").is_file()
    assert config_path("schemas", "selection.schema.json").is_file()
    assert config_path("schemas", "selection_taxonomy_map.schema.json").is_file()
    assert config_path("schemas", "scene_inventory.schema.json").is_file()
    assert config_path("schemas", "target_spec.schema.json").is_file()
    assert config_path("schemas", "measurement_request.schema.json").is_file()
    assert config_path("schemas", "measurement_receipt.schema.json").is_file()
    assert config_path("schemas", "measurement_private_sidecar.schema.json").is_file()
    assert config_path("schemas", "measurement_verifier_approval.schema.json").is_file()
    assert config_path("profiles", "bio_chem_fixture_compile.yaml").is_file()
    assert config_path("sources", "v32_bcm522_sixroute_selection.yaml").is_file()
    assert knowledge_path("index.yaml").is_file()


def test_bundled_rubric_reference_matches_the_checked_in_source_document() -> None:
    source = Path(__file__).resolve().parents[2] / "docs" / "Question-Rubric.md"
    assert repository_reference_path("QA-Gen/docs/Question-Rubric.md").read_bytes() == source.read_bytes()


def test_installed_package_ignores_shadow_resource_tree_near_venv(
    tmp_path: Path, monkeypatch,
) -> None:
    installed_module = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.13"
        / "site-packages"
        / "qagen"
        / "resource_paths.py"
    )
    installed_module.parent.mkdir(parents=True)
    installed_module.touch()

    shadow_root = installed_module.parents[2]
    (shadow_root / "configs").mkdir()
    (shadow_root / "configs" / "shadow.yaml").write_text("shadow: true\n", encoding="utf-8")

    packaged_qagen = tmp_path / "wheel" / "qagen"
    packaged_configs = packaged_qagen / "_resources" / "configs"
    packaged_configs.mkdir(parents=True)
    (packaged_configs / "packaged.yaml").write_text("packaged: true\n", encoding="utf-8")

    monkeypatch.setattr(resource_paths, "__file__", str(installed_module))
    monkeypatch.setattr(resource_paths.resources, "files", lambda package: packaged_qagen)
    resource_paths.resource_tree.cache_clear()
    try:
        assert resource_paths.config_root() == packaged_configs.resolve()
        assert resource_paths.config_path("packaged.yaml").is_file()
        assert not (resource_paths.config_root() / "shadow.yaml").exists()
    finally:
        resource_paths.resource_tree.cache_clear()
