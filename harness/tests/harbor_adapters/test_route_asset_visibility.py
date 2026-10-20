"""Regression tests for route-level label and sidecar isolation.

The important invariant is not merely that a public TOML omits a convenient
``mask_path`` field.  CC and CCMCP must stage the same visible set, and a
legacy segmentation-dependent task must be excluded from formal scoring rather
than silently evaluated after its required input was removed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test environment
    import tomli as tomllib  # type: ignore[no-redef]

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402
from scripts.harbor.security_contract import (  # noqa: E402
    SecurityContractError,
    validate_task_tree,
)


def _asset(root: Path, name: str, content: bytes = b"asset") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(content)
    return path


def _geometry_task(root: Path) -> tuple[dict, Path, Path]:
    ct = _asset(root, "ct.nii.gz", b"ct")
    mask = _asset(root, "mask.nii.gz", b"mask")
    return {
        "task_id": "sv-medi-med_ct_geometry-0016",
        "domain": "medical",
        "task_type": "med_ct_geometry",
        "v_class": "K3",
        "instruction": "Measure the target in the supplied CT volume.",
        "asset_path": str(ct),
        "asset_format": "nii.gz",
        "mask_path": str(mask),
        "asset": {
            "asset_id": "ct-public-id",
            "asset_path": str(ct),
            "asset_format": "nii.gz",
            "role": "primary",
            "viewer": "niivue",
        },
        "assets": [
            {
                "asset_id": "ct-public-id",
                "asset_path": str(ct),
                "asset_format": "nii.gz",
                "role": "primary",
                "viewer": "niivue",
            },
            {
                "asset_id": "secret-mask-id",
                "asset_path": str(mask),
                "asset_format": "nii.gz",
                "role": "mask",
                "viewer": "niivue",
                "sha256": "a" * 64,
            },
        ],
        "answer_schema": {"kind": "structured", "fields": ["extent"]},
        "scorer": {"primary": "exact_match", "secondary": []},
        "ground_truth": {"value": {"extent": 1}},
        "evaluation_eligible": True,
    }, ct, mask


@pytest.mark.parametrize("agent_env", ["codex", "codex-mcp"])
def test_geometry_mask_is_private_and_route_ineligible_for_cc_routes(tmp_path, agent_env):
    task, ct, mask = _geometry_task(tmp_path / "source")
    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env=agent_env,
    )

    staged_dir = task_dir / "environment" / "asset"
    assert (staged_dir / ct.name).is_file()
    assert not (staged_dir / mask.name).exists()
    public_toml = (task_dir / "task.toml").read_text()
    instruction = (task_dir / "instruction.md").read_text()
    assert str(mask) not in public_toml
    assert "secret-mask-id" not in public_toml
    assert "mask_path" not in public_toml
    assert "mask_id" not in public_toml
    assert "/data/mask.nii.gz" not in instruction
    assert "mask_path" not in instruction

    metadata = tomllib.loads(public_toml)["metadata"]
    assert metadata["evaluation_eligible"] is False
    policy = metadata["route_asset_visibility"]
    assert policy["status"] == "route_ineligible"
    assert policy["reason_code"] == "private_mask_requires_explicit_public_input_contract"
    assert policy["private_asset_roles"] == ["mask"]

    if agent_env == "codex-mcp":
        manifest = json.loads((task_dir / "environment" / "mcp-task-assets.json").read_text())
        assert [row["asset_path"] for row in manifest["assets"]] == ["/data/ct.nii.gz"]
        assert all(row["role"] != "mask" for row in manifest["assets"])

    # The static preflight permits an explicitly ineligible tree, while still
    # proving no model-facing label was staged. Image tags are intentionally
    # unpinned in this local fixture, so digest pinning is tested elsewhere.
    result = validate_task_tree(task_dir, require_image_digest=False)
    assert result["status"] == "pass"


@pytest.mark.parametrize("agent_env", ["codex", "codex-mcp"])
def test_segmentation_sidecar_is_private_but_cad_task_remains_eligible(tmp_path, agent_env):
    source = tmp_path / "source"
    model = _asset(source, "part.stp", b"step")
    sidecar = _asset(source, "part.seg", b"labels")
    task = {
        "task_id": "hard-061",
        "domain": "mechanical-cad",
        "task_type": "cad_feature_fillet_chamfer",
        "instruction": "Count chamfers in the model.",
        "asset_path": str(model),
        "asset_format": "stp",
        "asset": {
            "asset_id": "cad-model",
            "asset_path": str(model),
            "asset_format": "stp",
            "role": "primary_3d_model",
            "viewer": "occt",
        },
        "assets": [
            {
                "asset_id": "cad-model",
                "asset_path": str(model),
                "asset_format": "stp",
                "role": "primary_3d_model",
                "viewer": "occt",
            },
            {
                "asset_id": "cad-segmentation",
                "asset_path": str(sidecar),
                "asset_format": "seg",
                "role": "segmentation_sidecar",
                "viewer": "occt",
            },
        ],
        "answer_schema": {"kind": "freeform"},
        "scorer": {"primary": "llm_judge", "secondary": []},
        "evaluation_eligible": True,
    }
    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env=agent_env,
    )
    staged_dir = task_dir / "environment" / "asset"
    assert (staged_dir / model.name).is_file()
    assert not (staged_dir / sidecar.name).exists()
    metadata = tomllib.loads((task_dir / "task.toml").read_text())["metadata"]
    assert metadata["evaluation_eligible"] is True
    assert metadata["route_asset_visibility"]["status"] == "eligible"
    assert metadata["route_asset_visibility"]["private_asset_roles"] == [
        "segmentation_sidecar"
    ]
    if agent_env == "codex-mcp":
        manifest = json.loads((task_dir / "environment" / "mcp-task-assets.json").read_text())
        assert [row["asset_id"] for row in manifest["assets"]] == ["cad-model"]


def test_explicit_rewritten_contract_can_make_mask_a_public_input(tmp_path):
    task, _ct, mask = _geometry_task(tmp_path / "source")
    task["asset_visibility_contract"] = {
        "schema_version": "sciencevision.asset_visibility_contract.v1",
        "public_input_roles": ["mask"],
    }
    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env="codex",
    )
    metadata = tomllib.loads((task_dir / "task.toml").read_text())["metadata"]
    assert metadata["evaluation_eligible"] is True
    assert metadata["route_asset_visibility"]["status"] == "eligible"
    assert (task_dir / "environment" / "asset" / mask.name).is_file()
    assert any(row.get("role") == "mask" for row in metadata["assets"])


def test_manifest_private_role_or_unlisted_file_fails_static_preflight(tmp_path):
    task, _ct, _mask = _geometry_task(tmp_path / "source")
    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env="codex-mcp",
    )
    manifest_path = task_dir / "environment" / "mcp-task-assets.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["assets"][0]["role"] = "mask"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SecurityContractError, match="verifier-private"):
        validate_task_tree(task_dir, require_image_digest=False)


def test_ccmcp_visibility_policy_cannot_be_downgraded_by_dropping_contract(tmp_path):
    task, _ct, _mask = _geometry_task(tmp_path / "source")
    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env="codex-mcp",
    )
    toml_path = task_dir / "task.toml"
    lines = toml_path.read_text().splitlines()
    toml_path.write_text(
        "\n".join(line for line in lines if not line.startswith("route_contract = ")) + "\n"
    )
    with pytest.raises(SecurityContractError, match="must agree"):
        validate_task_tree(task_dir, require_image_digest=False)


def test_cc2d_keeps_mask_private_while_trusted_renderer_keeps_source_contract(tmp_path):
    task, _ct, _mask = _geometry_task(tmp_path / "source")
    policy = gen._route_asset_visibility(task, "codex-2d")
    effective = gen._effective_task_for_route(task, policy)
    assert policy.status == "eligible"
    assert not policy.permits_role("mask")
    # The host-side renderer receives the source task; only its generated view
    # tree crosses into the agent image.
    assert effective["mask_path"] == task["mask_path"]
    assert effective["evaluation_eligible"] is True
