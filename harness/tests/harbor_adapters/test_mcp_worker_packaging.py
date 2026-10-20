"""Static guards for the agent-only / private-MCP-worker packaging split.

These tests deliberately do not start Docker. They make the image boundary
reviewable on every Python CI run, while an image build remains the final
runtime check.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test environment
    import tomli as tomllib  # type: ignore[no-redef]

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor._agent_envs import (  # noqa: E402
    DF_CLAUDE_CODE_MCP,
    DF_CODEX_MCP,
    MCP_WORKER_PACKAGING,
)


_TEMPLATES = _ROOT / "scripts" / "harbor" / "templates"


def test_agent_only_templates_cannot_copy_worker_code_or_assets():
    for filename, env_dockerfile in (
        ("Dockerfile.codex-mcp", DF_CODEX_MCP),
        ("Dockerfile.claude-code-mcp", DF_CLAUDE_CODE_MCP),
    ):
        template = (_TEMPLATES / filename).read_text(encoding="utf-8")
        assert not re.search(r"^\s*COPY\s+asset/", template, flags=re.MULTILINE)
        assert not re.search(r"^\s*COPY\s+sci3d_agent", template, flags=re.MULTILINE)
        assert not re.search(r"^\s*COPY\s+harbor_adapters", template, flags=re.MULTILINE)
        assert not re.search(r"^\s*COPY\s+sci3d_bench", template, flags=re.MULTILINE)
        assert "viewer/assets.json" not in template
        assert "datasets/_matched" not in template
        assert "scanqa.json" not in template
        assert not re.search(
            r"^\s*(?:RUN|CMD|ENTRYPOINT)\b.*\bsciencevision-mcp\b",
            template,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        assert not re.search(r"^\s*COPY\s+asset/", env_dockerfile, flags=re.MULTILINE)
        assert "sci3d_agent" not in env_dockerfile
        assert "SV_MCP_HTTP_SURFACE=http://127.0.0.1:8765/mcp" in env_dockerfile


def test_worker_base_image_is_private_and_has_a_runtime_healthcheck():
    dockerfile = (_TEMPLATES / "Dockerfile.sciencevision-mcp-worker").read_text(
        encoding="utf-8"
    )
    for expected in (
        "io.sciencevision.image.role=\"mcp-worker\"",
        "COPY sci3d_agent/ ./sci3d_agent/",
        "COPY harbor_adapters/ ./harbor_adapters/",
        "COPY sci3d_bench/ ./sci3d_bench/",
        "SV_REPO_ROOT=/opt/sciencevision-src",
        "COPY viewer/assets.json ./viewer/assets.json",
        "COPY datasets/_matched/ ./datasets/_matched/",
        "PYTHONPATH=/opt/sciencevision-src",
        "USER sciencevision:sciencevision",
        "chown -R sciencevision:sciencevision /opt/sciencevision-src /opt/sciencevision/bin",
        "HEALTHCHECK",
        "mcp_worker_healthcheck.py",
        "ENTRYPOINT [\"python3\", \"/opt/sciencevision/bin/mcp_worker_entrypoint.py\"]",
    ):
        assert expected in dockerfile


def test_worker_source_path_is_explicit_while_setuptools_lacks_nested_discovery():
    """Avoid regressing to a worker that installs but cannot import its MCP module."""
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = project["tool"]["setuptools"]["packages"]
    assert "sci3d_agent" in packages
    assert "sci3d_agent.mcp" not in packages
    dockerfile = (_TEMPLATES / "Dockerfile.sciencevision-mcp-worker").read_text(
        encoding="utf-8"
    )
    assert "PYTHONPATH=/opt/sciencevision-src" in dockerfile
    assert "SV_REPO_ROOT=/opt/sciencevision-src" in dockerfile


def test_worker_compatibility_catalog_is_limited_to_the_private_registry_and_qa_index():
    """Legacy global lookup data belongs only in the private worker layer."""
    dockerfile = (_TEMPLATES / "Dockerfile.sciencevision-mcp-worker").read_text(
        encoding="utf-8"
    )
    assert "COPY viewer/assets.json ./viewer/assets.json" in dockerfile
    assert "COPY datasets/_matched/ ./datasets/_matched/" in dockerfile
    assert "COPY viewer/ ./viewer/" not in dockerfile
    assert "COPY datasets/ ./datasets/" not in dockerfile

    for filename in ("Dockerfile.codex-mcp", "Dockerfile.claude-code-mcp"):
        agent = (_TEMPLATES / filename).read_text(encoding="utf-8")
        assert "assets.json" not in agent
        assert "_matched" not in agent
        assert "scanqa" not in agent


def test_worker_overlay_has_one_asset_owner_and_harbor_owned_networking():
    rendered = MCP_WORKER_PACKAGING.render_dockerfile(
        image="registry.example/sciencevision-mcp-worker@sha256:" + "a" * 64
    )
    assert rendered.count("COPY asset/ /data/") == 1
    assert "COPY --chown=sciencevision:sciencevision mcp-task-assets.json" in rendered
    assert "SV_MCP_TASK_ISOLATED=1" in rendered
    assert "SV_MCP_TASK_ASSET_MANIFEST=/etc/sciencevision-mcp/task-assets.json" in rendered

    compose = MCP_WORKER_PACKAGING.render_compose(
        viewer_family="gltf.v1",
        catalog_sha256="b" * 64,
        schema_sha256="c" * 64,
    )
    assert "  main:" in compose
    assert "  sciencevision-mcp:" in compose
    assert "condition: service_healthy" in compose
    assert "SV_MCP_VIEWER_FAMILY: \"gltf.v1\"" in compose
    assert f"SV_MCP_CATALOG_SHA256: {json.dumps('b' * 64)}" in compose
    assert f"SV_MCP_SCHEMA_SHA256: {json.dumps('c' * 64)}" in compose
    assert "SV_MCP_HEALTH_RECEIPT_PATH" in compose
    assert "SV_MCP_HTTP_SURFACE" in compose
    assert "SV_MCP_TASK_ISOLATED" in compose
    assert "SV_MCP_TASK_ASSET_MANIFEST" in compose
    assert "network_mode:" not in compose
    assert "networks:" not in compose


def test_worker_entrypoint_preserves_contract_metadata_in_its_private_receipt():
    entrypoint = (_TEMPLATES / "mcp_worker_entrypoint.py").read_text(encoding="utf-8")
    for expected in (
        "SV_HTTP_PORT",
        "SV_HTTP_HOST",
        "SV_MCP_VIEWER_FAMILY",
        "SV_MCP_CATALOG_SHA256",
        "SV_MCP_SCHEMA_SHA256",
        "SV_MCP_HEALTH_RECEIPT_PATH",
        "SV_MCP_HTTP_SURFACE",
        '_payload(status="ready", port=port)',
    ):
        assert expected in entrypoint


def test_build_script_stages_an_allowlisted_context_and_never_pushes():
    script = (_TEMPLATES / "build_sciencevision_mcp_worker_image.sh").read_text(
        encoding="utf-8"
    )
    for expected in (
        "mktemp -d",
        '"${REPO_ROOT}/sci3d_agent"',
        '"${REPO_ROOT}/harbor_adapters"',
        '"${REPO_ROOT}/sci3d_bench"',
        '"${REPO_ROOT}/viewer/assets.json"',
        '"${REPO_ROOT}/datasets/_matched"',
        "--iidfile",
        "Built locally only; no registry push was attempted.",
    ):
        assert expected in script
    assert 'cp -R "${REPO_ROOT}/viewer"' not in script
    assert 'cp -R "${REPO_ROOT}/datasets"' not in script
    assert "docker push" not in script


def test_task_asset_manifest_is_task_local_and_redacts_source_provenance():
    rendered = MCP_WORKER_PACKAGING.render_task_asset_manifest(
        {
            "assets": [
                {
                    "asset_id": "medical:case-7",
                    "asset_path": "/data/ct.nii.gz",
                    "asset_format": "nifti",
                    "role": "primary",
                    "viewer": "niivue",
                    "repo_path": "datasets/private/ct.nii.gz",
                    "sha256": "a" * 64,
                }
            ]
        }
    )
    manifest = json.loads(rendered)
    assert manifest == {
        "schema_version": "sciencevision.mcp_task_assets.v1",
        "assets": [{
            "asset_id": "medical:case-7",
            "asset_path": "/data/ct.nii.gz",
            "format": "nifti",
            "role": "primary",
            "viewer": "niivue",
        }],
    }
    assert "datasets/private" not in rendered
    assert '"sha256"' not in rendered
