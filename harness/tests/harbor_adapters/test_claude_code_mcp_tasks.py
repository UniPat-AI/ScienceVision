"""Tests for private-worker Claude Code MCP task generation.

The model-facing Claude Code image is source- and asset-free. A separate
ScienceVision MCP worker gets the task asset at /data, while the instruction
still tells the client which path to supply to the HTTP MCP server. These tests
pin that generation end-to-end with fake asset files — no Docker, no Harbor.

Run in the py3.12 harbor venv:
    .venv-harbor/bin/python -m pytest tests/harbor_adapters/test_claude_code_mcp_tasks.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402
from scripts.harbor.qa_gen_to_tasks import qa_record_to_task  # noqa: E402


def _write_asset(dirpath: Path, name: str, content: bytes = b"\x00fake-nifti") -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / name
    p.write_bytes(content)
    return p


def _task(asset: Path, *, mask: Path | None = None) -> dict:
    t = {
        "task_id": "sv-medical-k3-0001",
        "domain": "medical",
        "v_class": "K3",
        "instruction": "Localize the focus mask within the aorta.",
        "asset_path": str(asset),
        "asset_format": "nifti",
        "answer_schema": {"kind": "freeform"},
        "scorer": {"primary": "llm_judge", "secondary": []},
        "allowed_tools": [],
        "forbidden_tools": [],
        "ground_truth": None,
    }
    if mask is not None:
        t["mask_path"] = str(mask)
    return t


# ── Agent/worker split + asset COPY ──────────────────────────────────────── #
def test_dockerfile_references_agent_image_and_worker_owns_asset(tmp_path):
    asset = _write_asset(tmp_path / "src", "ct.nii.gz")
    task_dir = gen.build_one(
        _task(asset), tmp_path / "out",
        containerized=True, agent_env="claude-code-mcp",
    )
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    assert "FROM sci3d-claude-code-mcp:latest" in dockerfile
    assert "COPY asset/ /data/" not in dockerfile
    worker_dockerfile = (task_dir / "environment" / "Dockerfile.mcp-worker").read_text()
    assert "FROM sci3d-mcp-worker:dev" in worker_dockerfile
    assert "COPY asset/ /data/" in worker_dockerfile
    compose = (task_dir / "environment" / "docker-compose.yaml").read_text()
    assert "sciencevision-mcp:" in compose
    assert "condition: service_healthy" in compose
    # asset file staged under environment/asset/
    assert (task_dir / "environment" / "asset" / "ct.nii.gz").exists()


def test_instruction_rewritten_with_container_path(tmp_path):
    asset = _write_asset(tmp_path / "src", "ct.nii.gz")
    task_dir = gen.build_one(
        _task(asset), tmp_path / "out",
        containerized=True, agent_env="claude-code-mcp",
    )
    instr = (task_dir / "instruction.md").read_text()
    # the original question survives
    assert "Localize the focus mask" in instr
    # one carrier for the container path: a parseable sci3d JSON block. The
    # ``Asset: /data/ct.nii.gz`` prose line that used to precede it restated the
    # same string and is gone; the adapter reads the block in preference anyway.
    assert "Asset:" not in instr
    block = re.search(r"```sci3d\s*\n(.*?)```", instr, re.DOTALL)
    assert block, "missing fenced sci3d block"
    payload = json.loads(block.group(1).strip())
    assert payload["asset_path"] == "/data/ct.nii.gz"


def test_mask_sidecar_is_private_and_marks_legacy_route_ineligible(tmp_path):
    asset = _write_asset(tmp_path / "src", "ct.nii.gz")
    mask = _write_asset(tmp_path / "src", "mask.nii.gz")
    task_dir = gen.build_one(
        _task(asset, mask=mask), tmp_path / "out",
        containerized=True, agent_env="claude-code-mcp",
    )
    # The worker follows the same filter as CC: it must not receive a label
    # merely because its route is behind MCP.
    assert not (task_dir / "environment" / "asset" / "mask.nii.gz").exists()
    manifest = json.loads(
        (task_dir / "environment" / "mcp-task-assets.json").read_text()
    )
    assert [row["asset_path"] for row in manifest["assets"]] == ["/data/ct.nii.gz"]
    assert all(row["role"] != "mask" for row in manifest["assets"])
    instr = (task_dir / "instruction.md").read_text()
    block = re.search(r"```sci3d\s*\n(.*?)```", instr, re.DOTALL)
    payload = json.loads(block.group(1).strip())
    assert "mask_path" not in payload
    assert "mask_id" not in payload
    assert "/data/mask.nii.gz" not in instr
    toml = (task_dir / "task.toml").read_text()
    assert "evaluation_eligible = false" in toml
    assert "private_mask_requires_explicit_public_input_contract" in toml
    assert str(mask) not in toml
    assert "mask_path" not in toml


def test_missing_asset_warns_but_does_not_raise(tmp_path, capsys):
    missing = tmp_path / "src" / "nope.nii.gz"  # never created
    task_dir = gen.build_one(
        _task(missing), tmp_path / "out",
        containerized=True, agent_env="claude-code-mcp",
    )
    # generation still produces a task dir + Dockerfile (degraded, not failed)
    assert (task_dir / "environment" / "Dockerfile").exists()
    assert not (task_dir / "environment" / "asset" / "nope.nii.gz").exists()
    # warned on stderr
    assert "not found on host" in capsys.readouterr().err
    # no asset path announced when nothing was staged
    instr = (task_dir / "instruction.md").read_text()
    assert "/data/" not in instr


def test_eligible_eval_task_fails_closed_when_asset_is_missing(tmp_path):
    task = _task(tmp_path)
    task["asset_path"] = str(tmp_path / "missing.cif")
    task["evaluation_eligible"] = True
    with pytest.raises(FileNotFoundError, match="missing source asset"):
        gen.build_one(
            task,
            tmp_path / "out",
            containerized=True,
            agent_env="claude-code-mcp",
        )


# ── bare claude-code also stages the asset (baseline ②: coding agent reads the
#    file directly, no MCP tools) — the only difference from claude-code-mcp is
#    the base image / whether the MCP server is registered. ─────────────────── #
def test_claude_code_env_stages_asset_from_its_own_image(tmp_path):
    asset = _write_asset(tmp_path / "src", "ct.nii.gz")
    task_dir = gen.build_one(
        _task(asset), tmp_path / "out",
        containerized=True, agent_env="claude-code",
    )
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    # its OWN image (no MCP server), but the asset is COPYd in like mcp does
    assert "FROM sci3d-claude-code:latest" in dockerfile
    assert "FROM sci3d-claude-code-mcp:latest" not in dockerfile
    assert "COPY asset/ /data/" in dockerfile
    # asset staged + instruction points at the in-container path
    assert (task_dir / "environment" / "asset" / "ct.nii.gz").exists()
    instr = (task_dir / "instruction.md").read_text()
    assert '"asset_path": "/data/ct.nii.gz"' in instr


def test_claude_code_image_bakes_in_inspection_deps():
    """The bare coding-agent image must ship the slim inspection libs so an
    offline agent can nibabel.load / trimesh.load the staged asset without a
    runtime pip install — keeping it a clean ablation against claude-code-mcp."""
    dockerfile = (gen._TEMPLATES_DIR / "Dockerfile.claude-code").read_text()
    for lib in ("nibabel", "trimesh", "numpy"):
        assert lib in dockerfile, f"{lib} missing from Dockerfile.claude-code"


# ── qa_gen_to_tasks mask resolution ──────────────────────────────────────── #
def test_qa_record_resolves_mask_repo_path():
    rec = {
        "task_id": "sv-medical-k3-0002",
        "domain": "medical",
        "instruction": "q",
        "asset": {
            "format": "nifti",
            "repo_path": "datasets/M3D-RefSeg/s0146/ct.nii.gz",
            "mask_repo_path": "datasets/M3D-RefSeg/s0146/mask.nii.gz",
        },
    }
    task = qa_record_to_task(rec, asset_root=Path("/repo"))
    assert task["asset_path"] == "/repo/datasets/M3D-RefSeg/s0146/ct.nii.gz"
    assert task["mask_path"] == "/repo/datasets/M3D-RefSeg/s0146/mask.nii.gz"


def test_qa_record_without_mask_has_no_mask_key():
    rec = {
        "task_id": "sv-physics-p1-0001",
        "domain": "physics",
        "instruction": "q",
        "asset": {"format": "glb", "repo_path": "datasets/x/scene.glb"},
    }
    task = qa_record_to_task(rec, asset_root=Path("/repo"))
    assert "mask_path" not in task
