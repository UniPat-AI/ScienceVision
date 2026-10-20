#!/usr/bin/env python3
"""Install a QA-Gen wheel into a fresh venv and exercise packaged resources.

This must run outside the source checkout.  It catches wheels that appear to
pass source-tree tests while omitting the schemas, profiles, or fixture inputs
needed by installed CLI and configuration-compiler paths.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap


_PROBE = """
import json
from pathlib import Path
import site

import qagen
from qagen.config import build_config, load_compiled_config, revalidate_compiled_config
from qagen.paths import WorkspacePaths
from qagen.resource_paths import config_path, config_root, knowledge_path, knowledge_root, repository_reference_path
from qagen.workers import _candidate_schema, _provider_viewer_evidence_schema, _verification_result_schema

package_file = Path(qagen.__file__).resolve()
site_roots = [Path(value).resolve() for value in site.getsitepackages()]
assert any(package_file.is_relative_to(root) for root in site_roots), package_file
assert "_resources" in config_root().parts, config_root()
assert "_resources" in knowledge_root().parts, knowledge_root()
assert config_path("schemas", "candidate.schema.json").is_file()
assert config_path("schemas", "compiled_selection_batch_locks.schema.json").is_file()
assert config_path("schemas", "compiled_selection_batch_plan.schema.json").is_file()
assert config_path("schemas", "compiled_selection_batch_run.schema.json").is_file()
assert config_path("schemas", "selection.schema.json").is_file()
assert config_path("schemas", "selection_taxonomy_map.schema.json").is_file()
assert config_path("schemas", "compiled_selection_viewer_capability_mapping.schema.json").is_file()
assert config_path("schemas", "compiled_selection_viewer_capability_receipt.schema.json").is_file()
assert config_path("schemas", "scene_inventory.schema.json").is_file()
assert config_path("schemas", "target_spec.schema.json").is_file()
assert config_path("schemas", "measurement_request.schema.json").is_file()
assert config_path("schemas", "measurement_receipt.schema.json").is_file()
assert config_path("schemas", "measurement_private_sidecar.schema.json").is_file()
assert config_path("schemas", "measurement_verifier_approval.schema.json").is_file()
assert config_path("viewer-capabilities", "compiled_selection_v32.yaml").is_file()
assert config_path("profiles", "bio_chem_fixture_compile.yaml").is_file()
assert config_path("profiles", "medical_volumetric_geometry_draft.yaml").is_file()
assert config_path("sources", "v32_bcm522_sixroute_selection.yaml").is_file()
assert knowledge_path("index.yaml").is_file()
assert repository_reference_path("QA-Gen/docs/Question-Rubric.md").is_file()
assert not (config_root() / "profiles" / "ev2_v103_seed_ready.yaml").exists()
assert not (config_root() / "profiles" / "chirality_workspace_knowledge_pilot.yaml").exists()
assert _candidate_schema()["$schema"] == "http://json-schema.org/draft-07/schema#"
assert _verification_result_schema()["$schema"] == "http://json-schema.org/draft-07/schema#"
assert _provider_viewer_evidence_schema()["$schema"] == "http://json-schema.org/draft-07/schema#"

profile_expectations = {
    "architecture_catalog": ("blocked", 11),
    "bio_chem_fixture_compile": ("fixture_stage_only", 2),
    "chirality_provider_pilot": ("local_provider_pilot_ready", 1),
    "mechanical_cad_catalog": ("blocked", 9),
    "medical_compile": ("blocked", 1),
    "medical_volumetric_geometry_draft": ("blocked", 1),
}
profile_results = {}
for profile_id, (expected_status, expected_leaf_count) in profile_expectations.items():
    profile = config_path("profiles", f"{profile_id}.yaml")
    workspace = Path.cwd() / f"wheel-runtime-workspace-{profile_id}"
    result = build_config(WorkspacePaths.from_value(workspace), profile_path=profile)
    assert result["status"] == expected_status, result
    compiled_path = Path(result["config_path"])
    assert compiled_path.is_file(), result
    compiled = load_compiled_config(WorkspacePaths.from_value(workspace), compiled_path)
    assert len(compiled["body"]["leaves"]) == expected_leaf_count, compiled
    assert revalidate_compiled_config(compiled)["content_digest"] == compiled["content_digest"]
    for lock in compiled["body"]["input_locks"]:
        assert Path(lock["path"]).is_file(), lock
    profile_results[profile_id] = result["status"]

print(json.dumps({"config_root": str(config_root()), "profiles": profile_results}, sort_keys=True))
"""


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(key, None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args(argv)
    wheel = args.wheel.resolve()
    if wheel.suffix != ".whl" or not wheel.is_file():
        parser.error("wheel must name an existing .whl archive")

    environment = _clean_environment()
    with tempfile.TemporaryDirectory(prefix="qagen-wheel-smoke-") as temporary:
        root = Path(temporary)
        runtime_cwd = root / "runtime-cwd"
        runtime_cwd.mkdir()
        venv = root / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=runtime_cwd, environment=environment)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        qa_gen = venv / ("Scripts/qa-gen.exe" if os.name == "nt" else "bin/qa-gen")
        _run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            cwd=runtime_cwd,
            environment=environment,
        )
        _run([str(qa_gen), "--help"], cwd=runtime_cwd, environment=environment)
        _run(
            [str(python), "-c", textwrap.dedent(_PROBE)],
            cwd=runtime_cwd,
            environment=environment,
        )
    print("installed-wheel QA-Gen smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
