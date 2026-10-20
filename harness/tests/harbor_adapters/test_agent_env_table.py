"""The AgentEnv table is the single source of truth for per-env generation.
Guards that the refactor kept host/claude-code/claude-code-mcp Dockerfiles
byte-identical and that the table wires the right stager per env.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402
from scripts.harbor import _agent_envs  # noqa: E402


def test_table_has_the_three_shipping_envs():
    for name in ("host", "claude-code", "claude-code-mcp"):
        assert name in gen.AGENT_ENVS
        assert gen.AGENT_ENVS[name].name == name


def test_host_reports_host_paths_without_copying():
    """The host route's agent runs in-process and reads the asset off the host
    filesystem, so its stager copies nothing — it only surfaces the per-task
    host path for the instruction rewriter (harbor's `--ak asset_path=` is one
    value for the whole run, so the path cannot be passed that way)."""
    env = gen.AGENT_ENVS["host"]
    assert env.stager is gen._stage_host_paths
    assert env.instruction_rewriter is gen._instruction_with_container_assets
    assert "FROM sci3d-host-agent:latest" in env.dockerfile
    assert "COPY asset/" not in env.dockerfile


def test_stage_host_paths_copies_nothing_and_resolves(tmp_path):
    asset = tmp_path / "vol.nii.gz"
    asset.write_bytes(b"x")
    env_dir = tmp_path / "environment"
    staged = gen._stage_host_paths(
        {"asset_path": str(asset), "mask_path": str(tmp_path / "absent.nii.gz")}, env_dir
    )
    assert staged == {
        "asset_path": str(asset.resolve()),
        "assets": [{
            "asset_id": "",
            "asset_path": str(asset.resolve()),
            "asset_format": "obj",
        }],
    }  # missing mask skipped
    assert not (env_dir / "asset").exists()  # nothing staged into the image context


def test_host_instruction_carries_the_asset_path(tmp_path):
    asset = tmp_path / "mesh.glb"
    asset.write_bytes(b"x")
    task = {
        "task_id": "t-0001",
        "instruction": "How many chambers?",
        "asset_path": str(asset),
        "answer_schema": {"fields": ["n"], "kind": "count"},
    }
    gen.build_one(task, tmp_path / "out", containerized=True, agent_env="host")
    text = (tmp_path / "out" / "t-0001" / "instruction.md").read_text()
    # Without this the agent gets a null asset_path and every read-dependent task
    # returns null (the failure the pilot runner worked around by hand). The path
    # rides in the fenced ``sci3d`` block, which is what the adapter parses; the
    # ``Asset:`` prose line that used to restate it was dropped as duplication.
    assert f'"asset_path": "{asset.resolve()}"' in text
    assert "None" not in text


def test_both_claude_code_envs_stage_assets():
    for name in ("claude-code", "claude-code-mcp"):
        env = gen.AGENT_ENVS[name]
        assert env.stager is gen._stage_assets
        assert env.instruction_rewriter is gen._instruction_with_container_assets
    # The bare agent reads /data itself; the MCP agent stages the same task
    # input for the separate worker but must not COPY it into its own image.
    assert "COPY asset/ /data/" in gen.AGENT_ENVS["claude-code"].dockerfile
    assert "COPY asset/ /data/" not in gen.AGENT_ENVS["claude-code-mcp"].dockerfile


def test_claude_code_mcp_dockerfile_is_source_and_asset_free():
    expected = _agent_envs.DF_CLAUDE_CODE_MCP
    assert gen.AGENT_ENVS["claude-code-mcp"].dockerfile == expected
    assert "FROM sci3d-claude-code-mcp:latest" in expected
    assert "COPY asset/ /data/" not in expected
    assert "COPY sci3d_agent" not in expected
    assert "COPY harbor_adapters" not in expected


def test_ccmcp_dockerfile_points_at_private_loopback_worker():
    df = _agent_envs.DF_CLAUDE_CODE_MCP
    assert "ENV SV_MCP_HTTP_SURFACE=http://127.0.0.1:8765/mcp" in df
    assert "SV_RENDER_CACHE" not in df
