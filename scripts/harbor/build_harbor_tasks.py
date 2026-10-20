"""Generate Harbor task directories from sci3d's verify_tasks.json.

Each sci3d verify task becomes a Harbor task directory:

    <out>/<task_id>/
    ├── task.toml        # [task] + [metadata] + [agent] + [verifier] + [environment]
    ├── instruction.md   # the question / instruction
    └── tests/test.sh    # placeholder (host route: reward comes from Sci3dVerifier)

The three Harbor config layers are filled explicitly (decision 3 in the plan):
``[agent]`` / ``[verifier]`` (the verifier keeps ``network_mode = "public"``
only for explicitly requested shadow judges) /
``[environment]``. The sci3d-specific fields (asset, scorer, answer_schema,
tool policy, ground_truth, reward weights) live in ``[metadata]``, which
``Sci3dVerifier`` reads back via ``task.config.task.metadata``.

This generator is *not* on the runtime hot path — it exists so the task set is
inspectable, uploadable to a Harbor registry, and reusable by any agent
(Codex / sci3d). Host execution still happens via
``--agent-import-path`` + ``--verifier-import-path``.

Usage:
    python -m scripts.harbor.build_harbor_tasks \
        --tasks scripts/eval/walkingskeleton/verify_tasks.json \
        --out scripts/harbor/tasks \
        [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.harbor._agent_envs import (
    AgentEnv,
    MCP_WORKER_PACKAGING,
    DF_HOST,
    DF_CODEX,
    DF_CODEX_MCP,
    DF_CODEX_2D,
    DF_CODEX_TEXT,
    DF_CLAUDE_CODE,
    DF_CLAUDE_CODE_MCP,
    DF_CLAUDE_CODE_2D,
    DF_CLAUDE_CODE_TEXT,
)
from scripts.harbor import step_mesh
from scripts.harbor import view_protocol
from scripts.harbor._multiview import plan_views
from harbor_adapters.sci3d._scorer_registry import looks_versioned_scorer
from scripts.harbor.eval_contracts import is_rich_v21_contract
from scripts.harbor.security_contract import (
    SECURITY_CONTRACT_VERSION,
    assert_public_metadata_safe,
    network_contract,
    normalize_allowed_hosts,
)
from sci3d_agent.mcp.parity.allowlist import live_registry_for_viewer
from sci3d_bench.scorers.deterministic_answer import SCORER_ALIASES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TASKS = _REPO_ROOT / "scripts/eval/walkingskeleton/verify_tasks.json"
_DEFAULT_OUT = _REPO_ROOT / "scripts/harbor/tasks"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_TEST_SH = """#!/bin/bash
# Placeholder. In the host-run route the reward is produced by
# harbor_adapters.sci3d.verifier:Sci3dVerifier (registered via
# --verifier-import-path), not by this in-container script.
# A containerized route would instead call the sci3d scorers here and write
# /logs/verifier/reward.json. See docs/harbor/VERIFY_sci3d.md.
echo "sci3d host-run task: reward emitted by Sci3dVerifier" 1>&2
"""

# Containerized route: the shared scorer image built from the repo root by
# scripts/harbor/templates/build_image.sh. Every containerized task references
# it by name (Harbor cannot COPY the repo into a per-task build context).
#
# Pin the UniPat-AI GHCR copy of server-20260930-h20. Its configuration digest
# matches the previous Docker Hub default; this changes the registry location,
# not the scorer release. The GHCR manifest digest differs from Docker Hub's.
# Delivered packages may require another scorer, selected explicitly with
# SCI3D_SCORER_IMAGE. Private-package read access is required before a run.
_SCORER_IMAGE = os.environ.get(
    "SCI3D_SCORER_IMAGE",
    "ghcr.io/unipat-ai/sciencevision-scorer@sha256:"
    "d07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062",
)

_CONTAINER_ASSET_DIR = "/data"

# The worker image contains the MCP implementation and is resolved to a digest
# by official launch preflight.  The task-local Dockerfile is rendered by
# ``MCP_WORKER_PACKAGING`` so it has a single owner across build and launch.
_MCP_WORKER_IMAGE = "sci3d-mcp-worker:dev"
_MCP_AGENT_ENVS = frozenset({"codex-mcp", "claude-code-mcp"})

# Labels, target masks, and segmentation sidecars may be verifier inputs, but
# they are not model inputs by default.  Roles are the boundary; filenames are
# mutable and therefore cannot provide one.
_ASSET_VISIBILITY_POLICY_VERSION = "sciencevision.route_asset_visibility.v1"
_ASSET_VISIBILITY_INPUT_CONTRACT_VERSION = (
    "sciencevision.asset_visibility_contract.v1"
)
_PRIVATE_ASSET_ROLES = frozenset(
    {
        "mask",
        "label",
        "labels",
        "segmentation",
        "segmentation_sidecar",
        "annotation",
        "annotation_sidecar",
        "ground_truth",
        "ground_truth_sidecar",
        "answer_key",
        "target_mask",
    }
)
_PRIVATE_PATH_ROLES = {
    "mask_path": "mask",
    "label_path": "label",
    "segmentation_path": "segmentation",
    "annotation_path": "annotation",
}

DEFAULT_WEIGHTS = {"answer": 1.0, "process": 0.0}
_ORG = "sci3d"  # Harbor package names must be 'org/name'.
_RENDERER_VERSION = "3"
_DISPLAY_GEOMETRY_POLICY = "source_voxel_edge_world_fov_square_canvas_v2"
_DISPLAY_ORIENTATION_POLICY = "world_grid_ras_canonical_v2"
_TARGET_GRID_POLICY = "source_voxel_edge_ras_grid_v1"
_TARGET_GRID_SPACING_RULE = "source_affine_column_norm_by_dominant_ras_axis_v1"
_NIFTI_RENDERERS = {
    "sv_render_nifti_slice",
    "sv_render_nifti_overlay",
    "sv_render_nifti_mip",
}


def _route_name_for_agent_env(agent_env: str) -> str | None:
    """Map generated environment names onto the CC / CC-MCP route contract."""
    if agent_env in {"codex", "claude-code"}:
        return "cc"
    if agent_env in {"codex-mcp", "claude-code-mcp"}:
        return "ccmcp"
    return None


def _asset_visibility_route(agent_env: str) -> str:
    """Return the route label carried by the asset-boundary policy."""
    route = _route_name_for_agent_env(agent_env)
    if route is not None:
        return route
    if agent_env.endswith("-2d"):
        return "cc2d"
    if agent_env.endswith("-text"):
        return "text"
    return agent_env or "host"


def _normalized_asset_role(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _is_private_asset_role(value: Any) -> bool:
    """Whether an asset role is private unless a new contract opts in."""
    role = _normalized_asset_role(value)
    return role in _PRIVATE_ASSET_ROLES or role.startswith("segmentation_")


def _declared_private_asset_roles(task: Mapping[str, Any]) -> frozenset[str]:
    """Find private roles without copying a source path into public policy."""
    roles: set[str] = set()
    for path_key, role in _PRIVATE_PATH_ROLES.items():
        if str(task.get(path_key) or "").strip():
            roles.add(role)
    for raw in task.get("assets") or ():
        if isinstance(raw, Mapping):
            role = _normalized_asset_role(raw.get("role"))
            if _is_private_asset_role(role):
                roles.add(role)
    asset = task.get("asset")
    if isinstance(asset, Mapping):
        role = _normalized_asset_role(asset.get("role"))
        if _is_private_asset_role(role):
            roles.add(role)
    return frozenset(sorted(roles))


def _explicit_public_input_roles(task: Mapping[str, Any]) -> frozenset[str]:
    """Read the explicit future-task opt-in for normally private inputs.

    A legacy row cannot make a label public merely by naming it an asset.  A
    rewritten task must include the versioned ``asset_visibility_contract``
    with an explicit ``public_input_roles`` list.
    """
    contract = task.get("asset_visibility_contract")
    if not isinstance(contract, Mapping):
        return frozenset()
    if contract.get("schema_version") != _ASSET_VISIBILITY_INPUT_CONTRACT_VERSION:
        return frozenset()
    raw_roles = contract.get("public_input_roles")
    if isinstance(raw_roles, (str, bytes)) or not isinstance(raw_roles, (list, tuple)):
        return frozenset()
    return frozenset(
        role for role in (_normalized_asset_role(value) for value in raw_roles) if role
    )


@dataclass(frozen=True)
class _RouteAssetVisibility:
    """One route's non-secret visibility decision for a task build."""

    route: str
    status: str
    reason_code: str
    private_roles: tuple[str, ...]
    explicit_public_input_roles: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return self.status == "route_ineligible"

    def permits_role(self, value: Any) -> bool:
        role = _normalized_asset_role(value)
        return not _is_private_asset_role(role) or role in self.explicit_public_input_roles

    def public_metadata(self) -> dict[str, Any]:
        """Serialize policy facts only: no source path, asset ID, or hash."""
        return {
            "schema_version": _ASSET_VISIBILITY_POLICY_VERSION,
            "route": self.route,
            "status": self.status,
            "reason_code": self.reason_code,
            "private_asset_roles": list(self.private_roles),
            "explicit_public_input_roles": list(self.explicit_public_input_roles),
        }


def _route_asset_visibility(
    task: Mapping[str, Any], agent_env: str,
) -> _RouteAssetVisibility:
    """Derive the shared CC/CCMCP boundary before either raw-asset stager runs.

    CC2D stays eligible: its trusted host renderer can consume a private mask,
    while its agent receives only rendered views.  A CC/CCMCP task that needs a
    mask/label but has not been explicitly rewritten to make that input public
    is marked route-ineligible rather than scored as if it were intact.
    """
    route = _asset_visibility_route(agent_env)
    private_roles = tuple(sorted(_declared_private_asset_roles(task)))
    public_roles = tuple(sorted(_explicit_public_input_roles(task)))
    required_private_roles = {
        role for role in private_roles
        if role in {"mask", "label", "labels", "segmentation"}
    }
    # CT geometry's target is defined by the segmentation.  Treat any private
    # annotation role there as required so a future packaging rename from
    # ``mask`` to ``segmentation_sidecar`` cannot silently re-enable scoring.
    if str(task.get("task_type") or "").strip().casefold() == "med_ct_geometry":
        required_private_roles.update(private_roles)
    needs_mask_or_label = bool(required_private_roles)
    missing_required_input = any(
        role not in public_roles for role in required_private_roles
    )
    if route in {"cc", "ccmcp"} and needs_mask_or_label and missing_required_input:
        return _RouteAssetVisibility(
            route=route,
            status="route_ineligible",
            reason_code="private_mask_requires_explicit_public_input_contract",
            private_roles=private_roles,
            explicit_public_input_roles=public_roles,
        )
    return _RouteAssetVisibility(
        route=route,
        status="eligible",
        reason_code="",
        private_roles=private_roles,
        explicit_public_input_roles=public_roles,
    )


def _effective_task_for_route(
    task: Mapping[str, Any], asset_visibility: _RouteAssetVisibility,
) -> dict[str, Any]:
    """Project source metadata into one route without mutating the source task."""
    effective = dict(task)
    effective["route_asset_visibility"] = asset_visibility.public_metadata()
    if asset_visibility.blocked:
        effective["evaluation_eligible"] = False
        effective["evaluation_ineligible_reason"] = (
            "route_ineligible:" + asset_visibility.reason_code
        )
        effective["route_ineligible"] = True
    return effective


def _task_viewer_family(task: Mapping[str, Any]) -> str | None:
    """Best-effort viewer family for the route contract, never a secret path."""
    viewers: set[str] = set()
    asset = task.get("asset")
    if isinstance(asset, Mapping) and asset.get("viewer"):
        viewers.add(str(asset["viewer"]))
    for row in task.get("assets") or ():
        if isinstance(row, Mapping) and row.get("viewer"):
            viewers.add(str(row["viewer"]))
    if len(viewers) != 1:
        return None
    return live_registry_for_viewer(next(iter(viewers)))


def _route_contract_for_task(
    task: Mapping[str, Any],
    agent_env: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the shared route contract without copying its policy logic.

    ``route_contracts`` is intentionally the owner of the contract semantics.
    This generator only supplies route-local facts and serializes its result.
    """
    route = _route_name_for_agent_env(agent_env)
    if route is None:
        if agent_env.endswith("-mcp"):
            raise ValueError(f"unsupported CC-MCP agent environment: {agent_env!r}")
        return None
    # Imported here rather than at module load time so historical non-route
    # task generation remains importable while route-contract deployments roll
    # out.  A route that is actually generated must have the shared contract.
    from scripts.harbor.route_contracts import build_route_contract, canonical_catalog

    viewer_family = _task_viewer_family(task)
    # A missing/unsupported viewer is not a benign ungated fallback on a
    # CCMCP route.  Preserve it as an explicitly infra-invalid contract (no
    # catalog) so the scorer refuses comparison rather than relabelling the
    # full server surface as task-specific evidence.
    if route == "ccmcp" and viewer_family is None:
        return build_route_contract(
            route,
            viewer_family=None,
            allowed_tools=(),
            required_tool_groups=(),
            min_successful_calls=1,
            render_required=False,
            catalog=None,
        )

    canonical = canonical_catalog(catalog)
    if canonical is not None and canonical.get("viewer_family") != viewer_family:
        raise ValueError(
            "MCP surface viewer_family does not match the task route: "
            f"catalog={canonical.get('viewer_family')!r}, task={viewer_family!r}"
        )
    catalog_names = tuple(canonical.get("tool_names") or ()) if canonical else ()
    # ``task.allowed_tools`` predates the HTTP MCP contract and sometimes names
    # generic viewer actions rather than server tools.  Never turn those labels
    # into a fake mandatory MCP call.  The task-specific evidence surface is
    # the intersection with the actual advertised catalog.
    declared = tuple(str(x) for x in (task.get("allowed_tools") or ()) if str(x))
    declared_mcp_tools = tuple(
        name for name in declared if name != "finish" and name in set(catalog_names)
    )
    if route == "ccmcp":
        # New CCMCP rows always require one successful *real* server call.
        # Legacy `allowed_tools` often contains generic viewer labels or is
        # empty, so use the portable open-scene probe instead of silently
        # making the route optional.  This is an evidence-of-tool-use floor,
        # not a claim that a generic label maps to a server method.
        if declared_mcp_tools:
            allowed = declared_mcp_tools
        elif "sv3d_open_scene" in catalog_names:
            allowed = ("sv3d_open_scene",)
        elif catalog_names:
            allowed = (catalog_names[0],)
        else:
            # No catalog is itself fail-closed in route-health validation. Keep
            # required=True so it cannot be mistaken for a legacy optional row.
            allowed = ()
        required_groups = (allowed,) if allowed else ()
        minimum = 1
        render_required = any("render" in name for name in declared_mcp_tools)
    else:
        allowed = ()
        required_groups = ()
        minimum = 0
        render_required = False
    return build_route_contract(
        route,
        viewer_family=viewer_family,
        allowed_tools=allowed,
        required_tool_groups=required_groups,
        min_successful_calls=minimum,
        render_required=render_required,
        catalog=catalog,
    )


def _is_deterministic_task(task: dict) -> bool:
    scorer = task.get("scorer") if isinstance(task.get("scorer"), dict) else {}
    primary = str(scorer.get("primary", ""))
    return primary in SCORER_ALIASES or looks_versioned_scorer(primary)


def _eligible(task: dict) -> bool:
    return task.get("evaluation_eligible") is True


def _strict_eval_task(task: dict) -> bool:
    return task.get("dataset_version") == "scale-v2.1" or is_rich_v21_contract(task)


# ── minimal TOML serialization (stdlib has no writer) ──────────────────── #
#: TOML has no null, but ground truths can carry one. The renderer writes this
#: sentinel wherever the metadata holds ``None``, and
#: ``harbor_adapters.sci3d._verify_translate`` restores it to ``None`` on the
#: way back in. An empty string would be a silent lie -- it is a different
#: value -- and is exactly the bug this sentinel replaced.
_NULL_SENTINEL = "__sv_null__"


def _toml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if v is None:
        return f'"{_NULL_SENTINEL}"'
    return _toml_str(str(v))


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _toml_value(v: Any) -> str:
    """Serialize a value as a TOML inline value (scalar / array / inline table)."""
    if isinstance(v, dict):
        inner = ", ".join(f"{_toml_key(k)} = {_toml_value(x)}" for k, x in v.items())
        return "{ " + inner + " }" if inner else "{}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return _toml_scalar(v)


def _toml_key(k: str) -> str:
    return k if k.replace("_", "").isalnum() else _toml_str(k)


def _render_task_toml(
    task: dict,
    *,
    containerized: bool = False,
    metadata: dict[str, Any] | None = None,
    agent_allowed_hosts: Iterable[str] | None = None,
) -> str:
    """Render a task config.

    Containerized tasks are the hardened evaluation route: their public TOML
    holds no verifier-only material and each phase has an explicit network
    policy.  The legacy host route keeps its existing metadata surface so old
    local debugging workflows do not silently change semantics.
    """
    if metadata is None:
        metadata = (
            _build_public_metadata(task, agent_allowed_hosts=agent_allowed_hosts)
            if containerized
            else _build_metadata(task)
        )
    scorer = task.get("scorer") if isinstance(task.get("scorer"), dict) else {}
    scorer_names = [
        str(scorer.get("primary") or "answer_score"),
        *(str(name) for name in (scorer.get("secondary") or ())),
    ]
    uses_shadow_judge = any(
        str(name) in {"shadow_llm_judge", "shadow_process_judge"}
        for name in scorer_names
    )
    # A separate formal verifier never needs public egress.  Shadow judges are
    # intentionally not part of the hardened containerized route; callers that
    # need them must run an explicitly diagnostic legacy job instead.
    if containerized and uses_shadow_judge:
        raise ValueError("shadow judges are incompatible with hardened containerized tasks")
    verifier_network = "public" if uses_shadow_judge else "no-network"
    contract = network_contract(agent_allowed_hosts)
    lines: list[str] = ['version = "1.0"', ""]
    lines.append("[task]")
    # Harbor requires 'org/name' format for the package name.
    lines.append(f'name = {_toml_str(_ORG + "/" + task["task_id"])}')
    lines.append("")
    lines.append("[metadata]")
    for k, v in metadata.items():
        lines.append(f"{_toml_key(k)} = {_toml_value(v)}")
    lines.append("")
    lines.append("[agent]")
    lines.append("timeout_sec = 900.0")
    if containerized:
        lines.append(f'network_mode = {_toml_str(contract["agent_network_mode"])}')
        if contract["agent_allowed_hosts"]:
            lines.append(f"allowed_hosts = {_toml_value(contract['agent_allowed_hosts'])}")
    lines.append("")
    lines.append("[verifier]")
    lines.append("timeout_sec = 900.0")
    # Formal scoring is offline. Public mode only serves optional shadows.
    lines.append(f'network_mode = "{verifier_network}"')
    if containerized:
        # Containerized route: the verifier runs in its own scorer container
        # (the shared sci3d-scorer image) and tests/test.sh writes reward.json.
        lines.append('environment_mode = "separate"')
        lines.append("")
        lines.append("[verifier.environment]")
        lines.append(f"docker_image = {_toml_str(_SCORER_IMAGE)}")
        # Harbor 0.20 uploads tests/ into the verifier image workdir when the
        # separate verifier uses a prebuilt image, then invokes /tests/test.sh.
        lines.append('workdir = "/tests"')
        lines.append(f'network_mode = "{verifier_network}"')
    lines.append("")
    lines.append("[environment]")
    lines.append("build_timeout_sec = 600.0")
    if containerized:
        lines.append('network_mode = "no-network"')
    # No agent docker_image: the agent's environment is route-independent. The
    # containerized route only changes the *verifier* environment (above).
    lines.append("")
    return "\n".join(lines)


def _build_metadata(task: dict) -> dict:
    """Build verifier-private metadata from a sci3d verification task.

    This is deliberately the full scorer contract.  Hardened task generation
    writes it only into the separate verifier's ``tests/test.sh``; it must not
    be handed to the public task TOML or the agent image build context.
    """
    scorer = task.get("scorer") or {}
    gt = task.get("ground_truth")
    meta: dict[str, Any] = {
        "domain": task.get("domain", ""),
        "v_class": task.get("v_class", "V1"),
        "asset_path": task.get("asset_path", ""),
        "asset_format": task.get("asset_format") or "obj",
        "answer_schema": task.get("answer_schema") or {"kind": "freeform"},
        "scorer": {
            **dict(scorer),
            "primary": scorer.get("primary", "answer_score"),
            "secondary": list(scorer.get("secondary", []) or []),
            "checks": list(scorer.get("checks", []) or []),
            "aggregation": scorer.get("aggregation") or "",
            "tolerance": dict(scorer.get("tolerance", {}) or {}),
            "expected_fields": list(scorer.get("expected_fields", []) or []),
            "axis_order": list(scorer.get("axis_order", []) or []),
        },
        "allowed_tools": list(task.get("allowed_tools", []) or []),
        "forbidden_tools": list(task.get("forbidden_tools", []) or []),
        "weights": dict(DEFAULT_WEIGHTS),
    }
    for key in (
        "mechanical_contract",
        "task_type",
        "asset_contract",
        "asset",
        "assets",
        "input_closure",
        "required_evidence",
        "viewer_requirements",
        "active_vision_evidence",
        "terminal_answer",
        "trace_requirements",
        "challenge_controls",
        "recompute_receipt",
        "provenance",
        "scorer_config",
        "asset_grounding",
        "grounding_receipt",
        "grounding_receipts",
        "target_selection",
        "entity_selection",
        "render_contract",
        "mask_id",
        "mask_path",
        "asset_hashes",
        "evaluation_eligible",
        "evaluation_ineligible_reason",
        "route_ineligible",
        "route_asset_visibility",
        "asset_visibility_contract",
        "dataset_version",
        "schema_version",
    ):
        if task.get(key) is not None:
            meta[key] = task[key]
    # ground_truth: omit when the task has none (needs_expert_annotation).
    if gt is not None and gt != "needs_expert_annotation":
        meta["ground_truth"] = gt
    return meta


def _public_asset_rows(container_paths: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return only staged, in-container asset facts safe for public metadata."""
    rows: list[dict[str, Any]] = []
    for item in (container_paths or {}).get("assets", ()):
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("asset_path") or item.get("path") or "")
        if not path.startswith(f"{_CONTAINER_ASSET_DIR}/"):
            continue
        row: dict[str, Any] = {
            "path": path,
            "asset_path": path,
            "format": str(item.get("asset_format") or item.get("format") or "obj"),
        }
        for key in ("role", "viewer", "asset_id", "name"):
            if item.get(key) is not None:
                row[key] = item[key]
        rows.append(row)
    return rows


def _build_public_metadata(
    task: Mapping[str, Any],
    *,
    container_paths: Mapping[str, Any] | None = None,
    agent_allowed_hosts: Iterable[str] | None = None,
    route_contract: Mapping[str, Any] | None = None,
    route_asset_visibility: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project only agent-safe metadata into a hardened task TOML.

    In particular, no ground truth, scorer implementation/configuration, host
    filesystem path, asset hash, or source repository path crosses this
    boundary.  Agent-visible input paths are carried by ``instruction.md`` and
    are always rooted under ``/data``.
    """
    paths = container_paths or {}
    primary = str(paths.get("asset_path") or "")
    if primary and not primary.startswith(f"{_CONTAINER_ASSET_DIR}/"):
        raise ValueError(f"public primary asset is not staged under {_CONTAINER_ASSET_DIR}: {primary}")
    public_assets = _public_asset_rows(paths)
    meta: dict[str, Any] = {
        "security_contract": network_contract(agent_allowed_hosts),
        "domain": str(task.get("domain") or ""),
        "v_class": str(task.get("v_class") or "V1"),
        "answer_schema": dict(task.get("answer_schema") or {"kind": "freeform"}),
        "allowed_tools": [str(x) for x in (task.get("allowed_tools") or ())],
        "forbidden_tools": [str(x) for x in (task.get("forbidden_tools") or ())],
        "evaluation_eligible": bool(task.get("evaluation_eligible", False)),
    }
    # CC/CCMCP receive staged raw input under /data.  CC2D deliberately has
    # only rendered views in instruction.md, so do not manufacture an empty
    # ``asset_path`` / asset row in its public TOML: even a harmless-looking
    # field suggests raw-asset access that the route is designed to deny.
    if primary:
        meta["asset_path"] = primary
        meta["asset_format"] = str(task.get("asset_format") or "obj")
    if public_assets:
        meta["assets"] = public_assets
    if task.get("dataset_version") is not None:
        meta["dataset_version"] = task["dataset_version"]
    if task.get("schema_version") is not None:
        meta["schema_version"] = task["schema_version"]
    if route_contract is not None:
        meta["route_contract"] = dict(route_contract)
    if route_asset_visibility is not None:
        meta["route_asset_visibility"] = dict(route_asset_visibility)
    assert_public_metadata_safe(meta)
    return meta


def _render_containerized_test_sh(
    task: dict,
    *,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Fill the templates/test.sh placeholders with this task's data.

    The metadata table is inlined as JSON so the in-container scorer needs no
    extra mounts. Single-quotes in the JSON are escaped for the bash single
    quoted export (close-quote + literal + reopen).
    """
    template = (_TEMPLATES_DIR / "test.sh").read_text()
    if metadata is None:
        metadata = _build_metadata(task)
    meta_json = json.dumps(metadata, ensure_ascii=False)
    instruction = task.get("instruction", "").strip()
    return (
        template
        .replace("{{TASK_ID}}", _bash_sq(_ORG + "/" + task["task_id"]))
        .replace("{{METADATA_JSON}}", _bash_sq(meta_json))
        .replace("{{INSTRUCTION}}", instruction)
    )


def _bash_sq(s: str) -> str:
    """Escape a value for embedding inside a bash single-quoted string."""
    return s.replace("'", "'\\''")


def _task_assets(task: dict) -> list[dict[str, Any]]:
    """Return the primary asset followed by distinct companion assets."""
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in task.get("assets") or []:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("asset_path") or raw.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        fmt = str(raw.get("format") or raw.get("asset_format") or "obj")
        row: dict[str, Any] = {
            "asset_path": path,
            "asset_format": fmt,
            "asset_id": str(raw.get("asset_id") or ""),
        }
        for key in ("role", "viewer", "repo_path", "sha256", "name", "byte_size"):
            if raw.get(key) is not None:
                row[key] = raw[key]
        assets.append(row)
    primary_path = str(task.get("asset_path") or "")
    if primary_path and primary_path not in seen:
        primary = task.get("asset") if isinstance(task.get("asset"), dict) else {}
        row = {
            "asset_id": str(primary.get("asset_id") or task.get("asset_id") or ""),
            "asset_path": primary_path,
            "asset_format": str(primary.get("format") or task.get("asset_format") or "obj"),
        }
        if primary.get("role") is not None:
            row["role"] = primary["role"]
        assets.insert(0, row)
    return assets


def _task_viewers(task: dict) -> set[str]:
    """The viewers this task's assets declare.

    Read from the task rather than through :func:`_task_assets`, which copies
    ``role`` off the primary asset but not ``viewer`` -- and the primary is the
    only place the viewer appears for 206 of the corpus's ~20k tasks.
    """
    viewers: set[str] = set()
    primary = task.get("asset")
    if isinstance(primary, dict) and primary.get("viewer"):
        viewers.add(str(primary["viewer"]))
    for raw in task.get("assets") or []:
        if isinstance(raw, dict) and raw.get("viewer"):
            viewers.add(str(raw["viewer"]))
    return viewers


def _viewer_family_env(task: dict) -> dict[str, str]:
    """``SV_MCP_VIEWER_FAMILY`` for this task, when it can be determined.

    The gate has to be per task -- a question authored on ``niivue.v1`` and one
    on ``molstar.v2`` want different tool surfaces -- while harbor's
    ``--mcp-config`` is fixed for the whole run. The task's own image is the only
    place a per-task value can travel, which is the route ``SV_RENDER_CACHE``
    already takes.

    Returns ``{}``, i.e. ungated, when the viewer is ambiguous, absent, or one
    the platform has no registry for. That is a recorded outcome, not a silent
    one: a run whose tasks could not be gated is not comparable with one whose
    tasks were, and this warning is what makes that visible in the build log.
    """
    viewers = _task_viewers(task)
    task_id = task.get("task_id", "<no task_id>")
    if len(viewers) != 1:
        reason = (
            "declares no viewer"
            if not viewers
            else f"declares {len(viewers)} viewers: {', '.join(sorted(viewers))}"
        )
        print(f"  warning: {task_id} {reason}; running ungated", file=sys.stderr)
        return {}
    viewer = next(iter(viewers))
    family = live_registry_for_viewer(viewer)
    if family is None:
        print(
            f"  warning: {task_id} declares viewer {viewer!r}, which the platform "
            f"has no registry for; running ungated",
            file=sys.stderr,
        )
        return {}
    return {"SV_MCP_VIEWER_FAMILY": family}


def _with_env(dockerfile: str, env: Mapping[str, str]) -> str:
    """Append ``ENV`` lines for ``env`` to a Dockerfile body."""
    if not env:
        return dockerfile
    lines = [dockerfile.rstrip("\n"), ""]
    lines.extend(f"ENV {key}={env[key]}" for key in sorted(env))
    return "\n".join(lines) + "\n"


def _is_mcp_agent_env(name: str) -> bool:
    return name in _MCP_AGENT_ENVS


def _route_mcp(route_contract: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return the contract MCP section without trusting arbitrary metadata."""
    if not isinstance(route_contract, Mapping):
        return {}
    mcp = route_contract.get("mcp")
    return mcp if isinstance(mcp, Mapping) else {}


def _declared_asset_rows(task: dict) -> list[dict[str, Any]]:
    """Expose rich canonical rows for QC materialization receipts."""
    return [
        {
            **item,
            "path": item["asset_path"],
            "format": item["asset_format"],
        }
        for item in _task_assets(task)
    ]


def _assert_eligible_assets(task: dict) -> None:
    """Fail before creating a task directory when eligible inputs are absent."""
    if not _eligible(task) or not _strict_eval_task(task):
        return
    assets = _task_assets(task)
    if not assets:
        raise FileNotFoundError(
            f"eligible task {task.get('task_id')} declares no primary asset"
        )
    missing = [
        item["asset_path"]
        for item in assets
        if not Path(item["asset_path"]).is_file()
    ]
    mask = task.get("mask_path")
    if mask and not Path(mask).is_file():
        missing.append(str(mask))
    if missing:
        raise FileNotFoundError(
            f"eligible task {task.get('task_id')} has missing asset(s): "
            + ", ".join(missing)
        )


def _stage_host_paths(task: dict, env_dir: Path) -> dict[str, Any]:
    """Report the asset's *host* absolute path(s) without copying anything.

    The host-run route's agent (``Sci3dAgent``) executes in the orchestrator
    process and reads the asset straight off the host filesystem, so there is
    nothing to COPY into the per-task image. The path still has to reach the
    agent, though: harbor's ``--ak asset_path=`` is a single value for the whole
    run (not per task) and this route's ``instruction.md`` is bare prose, so
    without a rewriter every task hands the agent ``Asset: None`` and every
    read-dependent task returns null. Pairing this with
    ``_instruction_with_container_assets`` puts the real path in the
    instruction, one per task.

    Eligible eval tasks fail closed when a declared file is missing, matching
    ``_stage_assets``.
    """
    env_dir.mkdir(parents=True, exist_ok=True)
    host_paths: dict[str, Any] = {"assets": []}
    assets = _task_assets(task)
    for item in assets:
        host = item["asset_path"]
        if Path(host).exists():
            staged_item = {
                **item,
                "asset_path": str(Path(host).resolve()),
            }
            if len(assets) > 1:
                staged_item["path"] = staged_item["asset_path"]
            host_paths["assets"].append(staged_item)
        elif task.get("evaluation_eligible") is True:
            raise FileNotFoundError(
                f"eligible eval task is missing source asset: {host}"
            )
    if host_paths["assets"]:
        host_paths["asset_path"] = host_paths["assets"][0]["asset_path"]
    mask = task.get("mask_path")
    if mask and Path(mask).exists():
        host_paths["mask_path"] = str(Path(mask).resolve())
    elif mask and task.get("evaluation_eligible") is True:
        raise FileNotFoundError(f"eligible eval task is missing mask asset: {mask}")
    if task.get("mask_id") is not None:
        host_paths["mask_id"] = task["mask_id"]
    return host_paths


def _stage_assets(
    task: dict,
    env_dir: Path,
    *,
    asset_visibility: _RouteAssetVisibility | None = None,
) -> dict[str, Any]:
    """Stage only agent-visible raw inputs into ``env_dir/asset/``.

    The exact same function serves CC (the agent image) and CCMCP (the private
    worker image), so a label cannot disappear from one route while remaining
    reachable through the other.  A direct caller without a route receives the
    conservative CC policy: annotation roles are private by default.
    """
    policy = asset_visibility or _route_asset_visibility(task, "codex")
    asset_subdir = env_dir / "asset"
    asset_subdir.mkdir(parents=True, exist_ok=True)
    container_paths: dict[str, Any] = {"assets": []}
    assets = _task_assets(task)
    used_names: set[str] = set()
    staged_by_source: dict[str, str] = {}
    for index, item in enumerate(assets):
        if not policy.permits_role(item.get("role")):
            continue
        host = item["asset_path"]
        src = Path(host)
        if not src.exists():
            if task.get("evaluation_eligible") is True:
                raise FileNotFoundError(
                    f"eligible eval task is missing source asset: {host}"
                )
            print(
                f"  warning: asset_path={host} not found on host; "
                f"MCP tools will not be able to read it",
                file=sys.stderr,
            )
            continue
        name = src.name
        if name in used_names:
            name = f"asset_{index:02d}_{name}"
        used_names.add(name)
        dst = asset_subdir / name
        shutil.copy2(src, dst)
        container_path = f"{_CONTAINER_ASSET_DIR}/{name}"
        container_paths["assets"].append({
            **item,
            "path": container_path,
            "asset_path": container_path,
        })
        staged_by_source[str(src)] = container_path
    if container_paths["assets"]:
        container_paths["asset_path"] = container_paths["assets"][0]["asset_path"]

    # A top-level mask is a common legacy encoding rather than an entry in
    # ``assets``.  Only an explicit future contract may turn it into an input;
    # otherwise it remains verifier-/renderer-private and is not copied.
    mask = str(task.get("mask_path") or "")
    if mask and policy.permits_role("mask"):
        src = Path(mask)
        if src.exists():
            existing = staged_by_source.get(str(src))
            if existing is None:
                name = src.name if src.name not in used_names else f"mask_{src.name}"
                used_names.add(name)
                shutil.copy2(src, asset_subdir / name)
                container_path = f"{_CONTAINER_ASSET_DIR}/{name}"
                container_paths["assets"].append({
                    "asset_id": str(task.get("mask_asset_id") or ""),
                    "asset_path": container_path,
                    "path": container_path,
                    "asset_format": str(task.get("mask_format") or "nifti"),
                    "role": "mask",
                })
        elif task.get("evaluation_eligible") is True:
            raise FileNotFoundError(
                f"eligible eval task is missing mask asset: {mask}"
            )
    container_paths["route_asset_visibility"] = policy.public_metadata()
    return container_paths


def _lookup_render_handler(tool_name: str):
    """Resolve an F-renderer tool handler via the live MCP REGISTRY.

    Lazy import so build_harbor_tasks stays importable in the harbor venv (where
    the mcp package/GL stack may be absent) for the host/codex(-mcp) paths;
    only the codex-2d generation path touches this.
    """
    try:
        # Importing the tools package runs each module's register() call, which
        # is what actually populates REGISTRY. Importing registry alone leaves
        # it empty, so every handler would look "unavailable".
        import sci3d_agent.mcp.tools  # noqa: F401 - side-effect: fills REGISTRY
        from sci3d_agent.mcp.registry import REGISTRY
    except Exception:  # noqa: BLE001 - mcp package not importable in this venv
        return None
    spec = REGISTRY.get(tool_name)
    return spec.handler if spec is not None else None


@dataclass(frozen=True)
class _MedicalRoiPlan:
    """Explicitly distinguish a valid ROI selection from an unavailable one."""

    mode: str
    best_slices: dict[str, Any] | None = None
    selection: dict[str, Any] | None = None


def _foreground_policy(task: dict) -> str:
    grounding = task.get("asset_grounding") if isinstance(task.get("asset_grounding"), dict) else {}
    verified = grounding.get("verified_entities") if isinstance(grounding.get("verified_entities"), dict) else {}
    target = task.get("target_selection") if isinstance(task.get("target_selection"), dict) else {}
    render = task.get("render_contract") if isinstance(task.get("render_contract"), dict) else {}
    for source in (target, render, verified):
        for key in ("foreground_policy", "foreground_rule"):
            value = source.get(key)
            if value:
                return str(value).casefold()
    return ""


def _uses_union_foreground(task: dict) -> bool:
    policy = _foreground_policy(task)
    return "union" in policy or "all nonzero" in policy


def _best_slices_from_inspection(info: dict, selected: dict) -> dict[str, Any] | None:
    raw_slices = selected.get("best_slices") if isinstance(selected, dict) else None
    if not isinstance(raw_slices, dict):
        return None
    out: dict[str, Any] = {}
    for axis in ("sagittal", "coronal", "axial"):
        if axis not in raw_slices:
            continue
        try:
            out[axis] = int(raw_slices[axis])
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(info.get("shape"), list):
        try:
            out["__shape__"] = [int(value) for value in info["shape"]]
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(selected.get("bbox_voxel"), list):
        out["__bbox__"] = selected["bbox_voxel"]
    anchor = selected.get("centroid_voxel")
    if isinstance(anchor, list) and len(anchor) == 3:
        try:
            normalized_anchor = [float(value) for value in anchor]
        except (TypeError, ValueError, OverflowError):
            normalized_anchor = []
        if len(normalized_anchor) == 3 and all(
            math.isfinite(value) for value in normalized_anchor
        ):
            out["__roi_plane_anchor_voxel__"] = normalized_anchor
    return out or None


def _roi_selection_record(mode: str, selected: dict) -> dict[str, Any] | None:
    """Serialize planner evidence only; source paths must never enter manifests."""
    if not isinstance(selected, dict):
        return None
    evidence = {
        key: selected[key]
        for key in (
            "voxel_count",
            "bbox_voxel",
            "centroid_voxel",
            "best_slices",
            "best_slice_voxel_counts",
        )
        if key in selected
    }
    if mode == "union_nonzero":
        labels = selected.get("nonzero_labels")
        if not isinstance(labels, list) or not labels:
            return None
        try:
            nonzero_labels = [int(label) for label in labels]
        except (TypeError, ValueError, OverflowError):
            return None
        return {
            "mode": mode,
            "nonzero_labels": nonzero_labels,
            "evidence": evidence,
        }
    selection = {"mode": mode, "evidence": evidence}
    if mode == "mask_id" and selected.get("id") is not None:
        selection["mask_id"] = selected["id"]
    elif mode == "single_label" and selected.get("id") is not None:
        selection["label_id"] = selected["id"]
    return selection


def _resolve_medical_roi_plan(task: dict) -> _MedicalRoiPlan:
    """Resolve the selected medical ROI without conflating union and failure.

    ``sv_inspect_nifti_mask_pair`` computes ``foreground_union`` directly from
    ``mask > 0``. Per-label maxima cannot be combined to recover that result.
    """
    if (task.get("asset_format") or "").lower() not in {"nifti", "nii", "nii.gz", "nifti.gz"}:
        return _MedicalRoiPlan("unavailable")
    mask = task.get("mask_path")
    if not mask:
        return _MedicalRoiPlan("unavailable")
    handler = _lookup_render_handler("sv_inspect_nifti_mask_pair")
    if handler is None:
        return _MedicalRoiPlan("unavailable")
    requested = task.get("mask_id")
    union_requested = requested is None and _uses_union_foreground(task)
    inspect_args = {"nifti_path": task["asset_path"], "mask_path": mask}
    if union_requested:
        inspect_args["foreground_mode"] = "union_nonzero"
    try:
        info = handler(inspect_args)
    except Exception as exc:  # noqa: BLE001
        print(
            f"  warning: sv_inspect_nifti_mask_pair raised {exc!r}; "
            f"falling back to mid-slice",
            file=sys.stderr,
        )
        return _MedicalRoiPlan("unavailable")
    if not isinstance(info, dict):
        return _MedicalRoiPlan("unavailable")
    if union_requested:
        selected = info.get("foreground_union")
        if not isinstance(selected, dict):
            print(
                f"  warning: task {task.get('task_id')} requests union foreground "
                "but inspection did not return a union summary",
                file=sys.stderr,
            )
            return _MedicalRoiPlan("unavailable")
        return _MedicalRoiPlan(
            "union_nonzero",
            _best_slices_from_inspection(info, selected),
            _roi_selection_record("union_nonzero", selected),
        )
    if not isinstance(info.get("labels"), list) or not info["labels"]:
        return _MedicalRoiPlan("unavailable")
    labels = info["labels"]
    if requested is not None:
        selected = next(
            (label for label in labels if str(label.get("id")) == str(requested)),
            None,
        )
        if selected is None:
            message = f"mask_id={requested!r} is absent for task {task.get('task_id')}"
            if _eligible(task):
                raise ValueError(message)
            print(f"  warning: {message}; falling back to fractional slices", file=sys.stderr)
            return _MedicalRoiPlan("unavailable")
        return _MedicalRoiPlan(
            "mask_id",
            _best_slices_from_inspection(info, selected),
            _roi_selection_record("mask_id", selected),
        )
    if len(labels) == 1:
        selected = labels[0]
        return _MedicalRoiPlan(
            "single_label",
            _best_slices_from_inspection(info, selected),
            _roi_selection_record("single_label", selected),
        )
    print(
        f"  warning: task {task.get('task_id')} has multiple mask labels "
        "without mask_id/foreground_policy; using fractional slices",
        file=sys.stderr,
    )
    return _MedicalRoiPlan("ambiguous_multilabel")


def _resolve_best_slices(task: dict) -> dict[str, Any] | None:
    """Backward-compatible best-slice projection for legacy callers/tests."""
    return _resolve_medical_roi_plan(task).best_slices


def _extract_png_bytes(envelope: dict) -> bytes | None:
    """Pull PNG bytes out of an F-renderer envelope, handling both the inlined
    (<=300KiB -> image_png_base64) and on-disk (cache_path) shapes."""
    import base64

    if not isinstance(envelope, dict) or "error" in envelope:
        return None
    b64 = envelope.get("image_png_base64")
    if b64:
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError):
            return None
    cache_path = envelope.get("cache_path")
    if cache_path:
        p = Path(cache_path)
        if p.exists():
            return p.read_bytes()
    return None


class RenderVisibilityError(RuntimeError):
    """A cc2d task failed a hard, deterministic render/visibility gate."""


# Failure codes that mean "this task legitimately cannot be a cc2d arm": no
# renderer covers its modality, the private mask/ROI cannot be shown to be
# visible, or a CAD B-rep could not be tessellated on this host. The batch
# runner records these as exclusions (with the receipt) instead of aborting.
# Everything else -- a renderer that raised, produced no PNG, or leaked -- is a
# real fault and must keep aborting the batch.
CC2D_EXPECTED_EXCLUSION_CODES = frozenset({
    "view_plan_incomplete",
    "medical_roi_plan_unavailable",
    "medical_roi_not_visible",
    "medical_extent_bbox_unavailable",
    "ocp_unavailable",
    "step_read_failed",
    "step_tessellation_failed",
    "step_tessellation_empty",
    "step_plan_unroutable",
    "step_format_unsupported",
})


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _inspect_png(png: bytes) -> dict[str, Any]:
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(png)) as image:
            image.load()
            if image.format != "PNG":
                raise RenderVisibilityError("render_output_not_png")
            rgba = image.convert("RGBA")
            width, height = rgba.size
            extrema = rgba.getextrema()
            alpha_nonempty = extrema[3][1] > 0
            varied = any(lo != hi for lo, hi in extrema[:3])
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise RenderVisibilityError("render_output_not_decodable") from exc
    if width <= 1 or height <= 1 or not alpha_nonempty or not varied:
        raise RenderVisibilityError("render_output_blank")
    return {"width": width, "height": height, "nonblank": True}


def _mask_pixel_count(args: dict, envelope: dict) -> int | None:
    value = envelope.get("mask_pixel_count")
    if isinstance(value, int):
        return value
    try:
        import nibabel as nib
        import numpy as np

        mask = np.asarray(nib.load(str(args["mask_path"])).get_fdata(caching="unchanged"))
        axis_index = {"sagittal": 0, "coronal": 1, "axial": 2}[str(args["axis"])]
        index = args.get("index")
        if index is None:
            index = round(float(args.get("fraction", 0.5)) * (mask.shape[axis_index] - 1))
        sl = np.take(mask, int(index), axis=axis_index)
        return int((sl > 0).sum())
    except Exception:  # noqa: BLE001 - gate reports missing coverage below
        return None


def _positive_int(value: Any) -> int | None:
    """Return a non-negative JSON integer metric without coercing it."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _affine_column_norm(affine: Any, axis: int) -> float | None:
    if not isinstance(affine, list) or len(affine) != 4 or axis not in (0, 1, 2):
        return None
    try:
        values = [float(affine[row][axis]) for row in range(3)]
    except (IndexError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    norm = math.sqrt(sum(value * value for value in values))
    return norm if norm > 0 else None


def _approximately_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return math.isclose(left, right, rel_tol=1e-4, abs_tol=1e-4)


def _finite_matrix4(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        matrix = [[float(item) for item in row] for row in value]
    except (TypeError, ValueError):
        return None
    if any(len(row) != 4 for row in matrix):
        return None
    return matrix if all(math.isfinite(item) for row in matrix for item in row) else None


def _shape3(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    result = [_positive_int(item) for item in value]
    return [int(item) for item in result] if all(item and item > 0 for item in result) else None


def _world_edge_bounds(
    shape: list[int], affine: list[list[float]],
) -> tuple[list[float], list[float]]:
    corners = [
        (x, y, z, 1.0)
        for x in (-0.5, shape[0] - 0.5)
        for y in (-0.5, shape[1] - 0.5)
        for z in (-0.5, shape[2] - 0.5)
    ]
    world = [
        [sum(affine[row][column] * corner[column] for column in range(4)) for row in range(3)]
        for corner in corners
    ]
    return (
        [min(point[axis] for point in world) for axis in range(3)],
        [max(point[axis] for point in world) for axis in range(3)],
    )


def _source_derived_target_grid(
    shape: list[int], affine: list[list[float]],
) -> dict[str, Any] | None:
    """Rebuild the renderer's base-only RAS target grid from source geometry."""
    try:
        import nibabel as nib
        import numpy as np

        matrix = np.asarray(affine, dtype=float)
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            return None
        determinant = float(np.linalg.det(matrix[:3, :3]))
        if not math.isfinite(determinant) or abs(determinant) <= 1e-12:
            return None
        orientation = nib.orientations.io_orientation(matrix)
        if orientation.shape != (3, 2) or np.isnan(orientation[:, 0]).any():
            return None
        world_axes = [int(value) for value in orientation[:, 0]]
        if sorted(world_axes) != [0, 1, 2]:
            return None
        native_for_world = [0, 0, 0]
        for native_axis, world_axis in enumerate(world_axes):
            native_for_world[world_axis] = native_axis
        column_norms = np.linalg.norm(matrix[:3, :3], axis=0)
        if np.any(~np.isfinite(column_norms)) or np.any(column_norms <= 0):
            return None
        spacing = [float(column_norms[native_for_world[axis]]) for axis in range(3)]
        corners = np.array([
            (x, y, z, 1.0)
            for x in (-0.5, shape[0] - 0.5)
            for y in (-0.5, shape[1] - 0.5)
            for z in (-0.5, shape[2] - 0.5)
        ], dtype=float)
        world = (matrix @ corners.T).T[:, :3]
        source_lower = world.min(axis=0)
        source_upper = world.max(axis=0)
        extent = source_upper - source_lower
        if np.any(~np.isfinite(extent)) or np.any(extent <= 0):
            return None
        target_shape = [
            max(1, int(math.ceil(float(extent[axis] / spacing[axis]))))
            for axis in range(3)
        ]
        target_affine = np.eye(4, dtype=float)
        target_affine[0, 0], target_affine[1, 1], target_affine[2, 2] = spacing
        target_affine[:3, 3] = source_lower + 0.5 * np.asarray(spacing, dtype=float)
        target_upper = source_lower + np.asarray(target_shape, dtype=float) * spacing
        return {
            "shape": target_shape,
            "affine": target_affine.tolist(),
            "spacing": spacing,
            "source_bounds": (source_lower.tolist(), source_upper.tolist()),
            "target_bounds": (source_lower.tolist(), target_upper.tolist()),
        }
    except Exception:  # noqa: BLE001 - malformed renderer geometry must fail closed
        return None


def _matrix_matches(left: list[list[float]] | None, right: list[list[float]] | None) -> bool:
    return bool(
        left is not None
        and right is not None
        and len(left) == len(right) == 4
        and all(
            _approximately_equal(left[row][column], right[row][column])
            for row in range(4)
            for column in range(4)
        )
    )


def _bounds_payload_matches(value: Any, bounds: tuple[list[float], list[float]]) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        reported_min = [float(item) for item in value["min"]]
        reported_max = [float(item) for item in value["max"]]
    except (KeyError, TypeError, ValueError):
        return False
    return (
        len(reported_min) == len(reported_max) == 3
        and all(math.isfinite(item) for item in [*reported_min, *reported_max])
        and all(_approximately_equal(left, right) for left, right in zip(reported_min, bounds[0]))
        and all(_approximately_equal(left, right) for left, right in zip(reported_max, bounds[1]))
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _finite_vector(value: Any, *, length: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != length:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _nifti_display_contract(envelope: dict, image_info: dict[str, Any]) -> dict[str, Any]:
    """Recompute a source-derived canonical-world-grid display contract."""
    renderer_pass = envelope.get("renderer_version") == _RENDERER_VERSION
    orientation_pass = (
        envelope.get("display_orientation_policy") == _DISPLAY_ORIENTATION_POLICY
        and envelope.get("display_orientation") == "RAS"
    )
    source_shape = _shape3(envelope.get("source_shape"))
    target_shape = _shape3(envelope.get("target_grid_shape"))
    target_voxel_count = _positive_int(envelope.get("target_grid_voxel_count"))
    source_affine = _finite_matrix4(envelope.get("source_affine_4x4"))
    target_affine = _finite_matrix4(envelope.get("target_grid_affine_4x4"))
    # The target reported by a renderer can be internally self-consistent while
    # still choosing an arbitrary spacing/FOV. Rebuild the policy grid from the
    # source affine so this gate does not trust target metadata as its own proof.
    expected_grid = (
        _source_derived_target_grid(source_shape, source_affine)
        if source_shape is not None and source_affine is not None
        else None
    )
    target_grid_derivation_pass = bool(
        expected_grid is not None
        and target_shape == expected_grid["shape"]
        and _matrix_matches(target_affine, expected_grid["affine"])
    )
    width = _positive_int(envelope.get("target_width"))
    height = _positive_int(envelope.get("target_height"))
    source_width = _positive_int(envelope.get("source_width"))
    source_height = _positive_int(envelope.get("source_height"))
    content_width = _positive_int(envelope.get("display_content_width"))
    content_height = _positive_int(envelope.get("display_content_height"))
    width_axis = _positive_int(envelope.get("display_width_target_axis_index"))
    height_axis = _positive_int(envelope.get("display_height_target_axis_index"))
    if width_axis not in (0, 1, 2):
        width_axis = None
    if height_axis not in (0, 1, 2):
        height_axis = None
    target_ras_pass = bool(
        target_affine is not None
        and all(target_affine[axis][axis] > 0 for axis in range(3))
        and all(
            abs(target_affine[row][column]) <= 1e-6
            for row in range(3)
            for column in range(3)
            if row != column
        )
        and all(abs(target_affine[3][column] - (1.0 if column == 3 else 0.0)) <= 1e-6 for column in range(4))
    )
    target_spacing = (
        [target_affine[axis][axis] for axis in range(3)] if target_ras_pass else None
    )
    affine_width_spacing = (
        target_spacing[width_axis]
        if target_spacing is not None and width_axis is not None
        else None
    )
    affine_height_spacing = (
        target_spacing[height_axis]
        if target_spacing is not None and height_axis is not None
        else None
    )
    target_width_mm = _positive_float(envelope.get("target_width_mm"))
    target_height_mm = _positive_float(envelope.get("target_height_mm"))
    source_width_mm = _positive_float(envelope.get("source_width_mm"))
    source_height_mm = _positive_float(envelope.get("source_height_mm"))
    display_width_mm = _positive_float(envelope.get("display_content_width_mm"))
    display_height_mm = _positive_float(envelope.get("display_content_height_mm"))
    expected_width_mm = width * affine_width_spacing if width is not None and affine_width_spacing is not None else None
    expected_height_mm = height * affine_height_spacing if height is not None and affine_height_spacing is not None else None
    affine_spacing_pass = (
        _approximately_equal(
            _positive_float(envelope.get("display_width_voxel_spacing_mm")),
            affine_width_spacing,
        )
        and _approximately_equal(
            _positive_float(envelope.get("display_height_voxel_spacing_mm")),
            affine_height_spacing,
        )
    )
    physical_dimensions_pass = (
        _approximately_equal(target_width_mm, expected_width_mm)
        and _approximately_equal(target_height_mm, expected_height_mm)
        and _approximately_equal(source_width_mm, target_width_mm)
        and _approximately_equal(source_height_mm, target_height_mm)
        and _approximately_equal(display_width_mm, target_width_mm)
        and _approximately_equal(display_height_mm, target_height_mm)
    )
    displayed_dimensions_pass = (
        content_width is not None
        and content_height is not None
        and content_width <= image_info["width"]
        and content_height <= image_info["height"]
        and content_width > 0
        and content_height > 0
    )
    canvas_size = image_info["width"] if image_info["width"] == image_info["height"] else None
    expected_content_width = None
    expected_content_height = None
    if canvas_size is not None and expected_width_mm is not None and expected_height_mm is not None:
        physical_scale = min(canvas_size / expected_width_mm, canvas_size / expected_height_mm)
        # Match _fit_to_square exactly. Comparing the post-rounding scales is
        # too strict: a 5 mm by 6 mm plane at 64 px correctly becomes 53x64.
        expected_content_width = max(1, int(round(expected_width_mm * physical_scale)))
        expected_content_height = max(1, int(round(expected_height_mm * physical_scale)))
    aspect_pass = bool(
        content_width == expected_content_width
        and content_height == expected_content_height
    )
    target_dimensions_pass = bool(
        target_shape is not None
        and width_axis is not None
        and height_axis is not None
        and width == target_shape[width_axis]
        and height == target_shape[height_axis]
        and source_width == width
        and source_height == height
    )
    source_bounds = (
        _world_edge_bounds(source_shape, source_affine)
        if source_shape is not None and source_affine is not None
        else None
    )
    target_bounds = None
    if target_shape is not None and target_affine is not None and target_spacing is not None:
        lower = [target_affine[axis][3] - 0.5 * target_spacing[axis] for axis in range(3)]
        target_bounds = (
            lower,
            [lower[axis] + target_shape[axis] * target_spacing[axis] for axis in range(3)],
        )
    reported_spacing = _finite_vector(envelope.get("target_grid_spacing_mm"), length=3)
    reported_fov = _finite_vector(envelope.get("target_grid_fov_mm"), length=3)
    reported_padding = _finite_vector(envelope.get("target_grid_padding_mm"), length=3)
    reported_padding_voxels = _finite_vector(
        envelope.get("target_grid_padding_voxels"), length=3,
    )
    source_fov_coverage_pass = bool(
        source_bounds is not None
        and target_bounds is not None
        and reported_spacing is not None
        and reported_fov is not None
        and reported_padding is not None
        and reported_padding_voxels is not None
        and target_voxel_count == math.prod(target_shape)
        and all(_approximately_equal(reported_spacing[axis], target_spacing[axis]) for axis in range(3))
        and all(
            _approximately_equal(reported_fov[axis], target_shape[axis] * target_spacing[axis])
            for axis in range(3)
        )
        and all(_approximately_equal(source_bounds[0][axis], target_bounds[0][axis]) for axis in range(3))
        and all(source_bounds[1][axis] <= target_bounds[1][axis] + 1e-4 for axis in range(3))
        and all(
            -1e-4 <= (target_bounds[1][axis] - source_bounds[1][axis]) / target_spacing[axis] < 1.0001
            for axis in range(3)
        )
        and all(
            _approximately_equal(
                reported_padding[axis], target_bounds[1][axis] - source_bounds[1][axis],
            )
            and _approximately_equal(
                reported_padding_voxels[axis],
                (target_bounds[1][axis] - source_bounds[1][axis]) / target_spacing[axis],
            )
            for axis in range(3)
        )
        and _bounds_payload_matches(envelope.get("source_world_bounds_mm"), source_bounds)
        and _bounds_payload_matches(envelope.get("target_grid_world_bounds_mm"), target_bounds)
    )
    expected_identity = None
    if source_shape is not None and target_shape is not None and source_affine is not None and target_affine is not None:
        expected_identity = _sha256_json({
            "policy": _TARGET_GRID_POLICY,
            "spacing_rule": _TARGET_GRID_SPACING_RULE,
            "source_sha256": envelope.get("source_sha256"),
            "source_shape": source_shape,
            "source_affine_4x4": source_affine,
            "target_shape": target_shape,
            "target_affine_4x4": target_affine,
        })
    target_grid_identity_pass = bool(
        _is_sha256(envelope.get("source_sha256"))
        and _is_sha256(envelope.get("target_grid_identity"))
        and envelope.get("target_grid_identity") == expected_identity
    )
    base_resample_pass = (
        envelope.get("base_resample_method") in {"identity", "scipy_ndimage_affine_transform"}
        and envelope.get("base_resample_order") == 1
        and isinstance(envelope.get("base_resample_applied"), bool)
        and envelope.get("base_resample_mode") == "constant"
        and envelope.get("base_resample_cval") == 0.0
    )
    geometry_pass = (
        envelope.get("display_geometry_policy") == _DISPLAY_GEOMETRY_POLICY
        and envelope.get("target_grid_policy") == _TARGET_GRID_POLICY
        and envelope.get("target_grid_spacing_rule") == _TARGET_GRID_SPACING_RULE
        and target_ras_pass
        and target_dimensions_pass
        and target_grid_derivation_pass
        and target_grid_identity_pass
        and source_fov_coverage_pass
        and base_resample_pass
        and affine_spacing_pass
        and physical_dimensions_pass
        and displayed_dimensions_pass
        and aspect_pass
    )
    return {
        "renderer_identity_pass": renderer_pass,
        "orientation_pass": orientation_pass,
        "physical_fov_pass": geometry_pass,
        "affine_spacing_pass": affine_spacing_pass,
        "physical_dimensions_pass": physical_dimensions_pass,
        "displayed_dimensions_pass": displayed_dimensions_pass,
        "physical_aspect_pass": aspect_pass,
        "target_grid_pass": (
            target_ras_pass and target_dimensions_pass and target_grid_derivation_pass
        ),
        "target_grid_derivation_pass": target_grid_derivation_pass,
        "target_grid_identity_pass": target_grid_identity_pass,
        "source_fov_coverage_pass": source_fov_coverage_pass,
        "base_resample_pass": base_resample_pass,
        "source_affine_4x4": source_affine,
        "target_grid_affine_4x4": target_affine,
    }


def _medical_display_contract(envelope: dict, image_info: dict[str, Any]) -> dict[str, Any]:
    """Extend the world-grid contract with mask resampling provenance checks."""
    contract = _nifti_display_contract(envelope, image_info)
    mask_shape = _shape3(envelope.get("mask_shape"))
    mask_affine = _finite_matrix4(envelope.get("mask_affine_4x4"))
    mask_bounds = (
        _world_edge_bounds(mask_shape, mask_affine)
        if mask_shape is not None and mask_affine is not None
        else None
    )
    source_counts = envelope.get("mask_label_voxel_counts_source")
    target_counts = envelope.get("mask_label_voxel_counts_target")
    retention = envelope.get("mask_label_retention_fraction")
    label_counts_pass = bool(
        isinstance(source_counts, dict)
        and isinstance(target_counts, dict)
        and isinstance(retention, dict)
        and all(
            isinstance(label, str)
            and _positive_int(count) is not None
            and _positive_int(target_counts.get(label)) is not None
            and _approximately_equal(
                _positive_float(retention.get(label)),
                float(target_counts[label]) / float(count) if count else None,
            )
            for label, count in source_counts.items()
        )
    )
    selected_source_count = _positive_int(envelope.get("mask_selected_voxel_count_source"))
    selected_target_count = _positive_int(envelope.get("mask_selected_voxel_count_target"))
    roi_retention_pass = bool(
        selected_source_count is not None
        and selected_target_count is not None
        and (selected_source_count == 0 or selected_target_count > 0)
    )
    outside_voxels = envelope.get("mask_roi_outside_target_fov_voxels")
    outside_pass = bool(
        envelope.get("mask_roi_outside_target_fov_threshold_voxels") == 1.0
        and isinstance(outside_voxels, list)
        and len(outside_voxels) == 3
        and all(
            isinstance(value, (int, float))
            and math.isfinite(float(value))
            and 0.0 <= float(value) < 1.0
            for value in outside_voxels
        )
    )
    mask_resample_pass = (
        envelope.get("mask_resample_method") in {"identity", "scipy_ndimage_affine_transform"}
        and envelope.get("mask_resample_order") == 0
        and isinstance(envelope.get("mask_resample_applied"), bool)
        and envelope.get("mask_resample_mode") == "constant"
        and envelope.get("mask_resample_cval") == 0.0
    )
    mask_provenance_pass = bool(
        _is_sha256(envelope.get("mask_sha256"))
        and mask_bounds is not None
        and _bounds_payload_matches(envelope.get("mask_world_bounds_mm"), mask_bounds)
        and label_counts_pass
        and roi_retention_pass
        and outside_pass
    )
    return {
        **contract,
        "mask_grid_alignment_pass": (
            envelope.get("mask_grid_alignment") == "resampled_to_base_world_target_grid"
            and mask_resample_pass
            and mask_provenance_pass
        ),
        "mask_resample_pass": mask_resample_pass,
        "mask_provenance_pass": mask_provenance_pass,
    }


def _roi_coverage(args: dict, envelope: dict, image_info: dict[str, Any]) -> dict[str, Any]:
    """Keep source geometry distinct from the rendered visibility locator.

    ``source_mask_pixel_count`` proves the selected source slice contains a
    real ROI. ``display_highlight_pixel_count`` measures the colored mask plus
    any explicitly-recorded outline used only to make a tiny ROI findable.
    """
    source_count = _positive_int(envelope.get("source_mask_pixel_count"))
    if source_count is None:
        source_count = _mask_pixel_count(args, envelope)
    display_mask_count = _positive_int(envelope.get("display_mask_pixel_count"))
    if display_mask_count is None:
        display_mask_count = source_count
    display_highlight_count = _positive_int(envelope.get("display_highlight_pixel_count"))
    if display_highlight_count is None:
        display_highlight_count = display_mask_count

    source_width = _positive_int(envelope.get("source_width"))
    source_height = _positive_int(envelope.get("source_height"))
    display_width = _positive_int(envelope.get("display_content_width"))
    display_height = _positive_int(envelope.get("display_content_height"))
    if source_width is not None and source_height is not None:
        source_area = source_width * source_height
        display_area = (
            display_width * display_height
            if display_width is not None and display_height is not None
            else image_info["width"] * image_info["height"]
        )
        reference_area = min(source_area, display_area)
    else:
        reference_area = image_info["width"] * image_info["height"]
    threshold = max(64, int(math.ceil(0.001 * reference_area)))
    contract = _medical_display_contract(envelope, image_info)
    return {
        # Retain the former key as a source-geometry alias for downstream readers.
        "mask_pixel_count": source_count,
        "source_mask_pixel_count": source_count,
        "display_mask_pixel_count": display_mask_count,
        "display_highlight_pixel_count": display_highlight_count,
        "minimum_highlight_pixels": threshold,
        "reference_area_pixels": reference_area,
        "passes": source_count is not None and source_count > 0
        and display_highlight_count is not None and display_highlight_count >= threshold
        and contract["renderer_identity_pass"]
        and contract["orientation_pass"]
        and contract["mask_grid_alignment_pass"]
        and contract["physical_fov_pass"],
        "clipped": bool(envelope.get("roi_highlight_clipped", False)),
        "highlight_type": envelope.get("roi_highlight_type", "mask_overlay"),
        "highlight_outline_width_px": _positive_int(
            envelope.get("roi_highlight_outline_width_px")
        ) or 0,
        "highlight_outline_bbox_xyxy": envelope.get("roi_highlight_outline_bbox_xyxy"),
        "renderer_identity_pass": contract["renderer_identity_pass"],
        "orientation_pass": contract["orientation_pass"],
        "mask_grid_alignment_pass": contract["mask_grid_alignment_pass"],
        "physical_fov_pass": contract["physical_fov_pass"],
        "affine_spacing_pass": contract["affine_spacing_pass"],
        "physical_dimensions_pass": contract["physical_dimensions_pass"],
        "displayed_dimensions_pass": contract["displayed_dimensions_pass"],
        "physical_aspect_pass": contract["physical_aspect_pass"],
        "target_grid_pass": contract["target_grid_pass"],
        "target_grid_derivation_pass": contract["target_grid_derivation_pass"],
        "target_grid_identity_pass": contract["target_grid_identity_pass"],
        "source_fov_coverage_pass": contract["source_fov_coverage_pass"],
        "base_resample_pass": contract["base_resample_pass"],
        "mask_resample_pass": contract["mask_resample_pass"],
        "mask_provenance_pass": contract["mask_provenance_pass"],
    }


def _manifest_args(args: dict, source_id: str) -> dict:
    clean = dict(args)
    for key in ("path", "nifti_path"):
        if key in clean:
            clean[key] = f"source:{source_id}"
    if "mask_path" in clean:
        clean["mask_path"] = "source:mask"
    return clean


def _stage_rendered_views(task: dict, env_dir: Path) -> dict:
    """Offline-render this task's 3D asset to per-modality PNG views and stage
    them under ``env_dir/views/`` for the codex-2d Dockerfile's
    ``COPY views/ /data/``. The raw 3D file is NEVER staged.

    The function is fail-closed: incomplete counts, invalid/blank PNGs, missing
    provenance, multi-asset loss, or insufficient medical mask pixels raise
    :class:`RenderVisibilityError` and prevent task generation.
    """
    requested = int(task.get("n_views", 6))
    assets = _task_assets(task)
    views_dir = env_dir / "views"
    views_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = env_dir / "render_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "task_id": str(task.get("task_id", "")),
        "baseline": f"cc2d-{requested}",
        "scheme": view_protocol.SCHEME,
        "angle_frame": view_protocol.ANGLE_FRAME,
        "renderer": "sciencevision-f-renderer",
        "renderer_version": _RENDERER_VERSION,
        "display_geometry_policy": _DISPLAY_GEOMETRY_POLICY,
        "display_orientation_policy": _DISPLAY_ORIENTATION_POLICY,
        "requested_views_per_asset": requested,
        "expected_asset_count": len(assets),
        "staged_asset_count": 0,
        "sources": [],
        "views": [],
        "gates": {},
        "visibility_status": "failed",
    }

    def fail(code: str) -> None:
        manifest["failure_reason"] = code
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        raise RenderVisibilityError(code)

    if not assets:
        fail("source_asset_missing")

    container_paths: list[str] = []
    asset_views: list[dict[str, Any]] = []
    medical_roi_pass = True
    medical_renderer_identity_pass = True
    medical_orientation_pass = True
    medical_physical_fov_pass = True
    medical_mask_grid_alignment_pass = True
    nifti_renderer_identity_pass = True
    nifti_orientation_pass = True
    nifti_physical_fov_pass = True
    nifti_source_provenance_pass = True
    for asset_index, source in enumerate(assets):
        source_path = Path(source["asset_path"])
        if not source_path.is_file():
            fail("source_asset_missing")
        source_id = source["asset_id"] or f"asset-{asset_index:02d}"
        source_entry = {
            "asset_id": source_id,
            "format": source["asset_format"],
            "sha256": _sha256_path(source_path),
        }
        if asset_index == 0 and task.get("mask_path"):
            mask_path = Path(task["mask_path"])
            if not mask_path.is_file():
                fail("mask_asset_missing")
            source_entry["mask_sha256"] = _sha256_path(mask_path)
        manifest["sources"].append(source_entry)

        render_task = {
            **task,
            "asset_path": str(source_path),
            "asset_format": source["asset_format"],
        }
        if asset_index > 0:
            render_task.pop("mask_path", None)
        roi_plan = _resolve_medical_roi_plan(render_task)
        best = roi_plan.best_slices
        if render_task.get("mask_path"):
            if roi_plan.selection is not None:
                source_entry["roi_selection"] = roi_plan.selection
            required = {"sagittal", "coronal", "axial"}
            shape = best.get("__shape__") if isinstance(best, dict) else None
            if (
                not isinstance(best, dict)
                or not required.issubset(best)
                or not isinstance(shape, list)
                or len(shape) < 3
            ):
                fail("medical_roi_plan_unavailable")
            fields = (render_task.get("answer_schema") or {}).get("fields", [])
            if (
                "extent_vox" in fields
                and requested >= 6
                and not isinstance(best.get("__bbox__"), list)
            ):
                fail("medical_extent_bbox_unavailable")
        plan = plan_views(
            render_task,
            best_slices=best,
            n_views=requested,
            seed_pose=task.get("seed_pose"),
        )
        if not plan or len(plan) != requested:
            fail("view_plan_incomplete")

        this_asset_paths: list[str] = []
        roi_passes: list[bool] = []
        renderer_identity_passes: list[bool] = []
        orientation_passes: list[bool] = []
        physical_fov_passes: list[bool] = []
        mask_grid_alignment_passes: list[bool] = []
        nifti_renderer_identity_passes: list[bool] = []
        nifti_orientation_passes: list[bool] = []
        nifti_physical_fov_passes: list[bool] = []
        with step_mesh.scratch_dir() as scratch:
            try:
                step_plan = step_mesh.rewrite_plan(plan, scratch=scratch)
            except step_mesh.StepMeshError as exc:
                fail(exc.code)
            if step_plan.provenance is not None:
                manifest["step_mesh"] = step_plan.provenance
            for view_index, (tool_name, args) in enumerate(step_plan.calls):
                handler = _lookup_render_handler(tool_name)
                if handler is None:
                    fail("renderer_unavailable")
                try:
                    envelope = handler(args)
                except Exception as exc:  # noqa: BLE001
                    manifest["renderer_error"] = repr(exc)
                    fail("renderer_exception")
                png = _extract_png_bytes(envelope)
                if png is None:
                    fail("renderer_produced_no_png")
                try:
                    image_info = _inspect_png(png)
                except RenderVisibilityError as exc:
                    fail(str(exc))

                filename = (
                    f"view_{view_index:02d}.png"
                    if len(assets) == 1
                    else f"asset_{asset_index:02d}_view_{view_index:02d}.png"
                )
                output = views_dir / filename
                output.write_bytes(png)
                container_path = f"{_CONTAINER_ASSET_DIR}/{filename}"
                this_asset_paths.append(container_path)
                container_paths.append(container_path)
                entry = {
                    "asset_id": source_id,
                    "view_index": view_index,
                    "file": filename,
                    "container_path": container_path,
                    "tool": tool_name,
                    "view": args.get("view"),
                    "angle_frame": args.get("angle_frame"),
                    "args": _manifest_args(args, source_id),
                    "png_sha256": hashlib.sha256(png).hexdigest(),
                    **image_info,
                }
                render_metadata = {
                    key: envelope[key]
                    for key in (
                        "axis",
                        "slice_index",
                        "voxel_spacing_mm",
                        "window_used",
                        "renderer_used",
                        "cache_sha",
                        "renderer_version",
                        "display_geometry_policy",
                        "display_orientation_policy",
                        "display_orientation",
                        "source_orientation",
                        "source_native_axis_index",
                        "source_slice_index",
                        "source_slice_world_coordinate_mm",
                        "plane_anchor_voxel",
                        "slice_plane_anchor_policy",
                        "canonical_slice_axis",
                        "canonical_slice_axis_index",
                        "source_shape",
                        "source_affine_4x4",
                        "source_sha256",
                        "source_world_bounds_mm",
                        "target_grid_policy",
                        "target_grid_spacing_rule",
                        "target_grid_affine_4x4",
                        "target_grid_shape",
                        "target_grid_voxel_count",
                        "target_grid_spacing_mm",
                        "target_grid_fov_mm",
                        "target_grid_world_bounds_mm",
                        "target_grid_padding_mm",
                        "target_grid_padding_voxels",
                        "target_grid_identity",
                        "base_resample_method",
                        "base_resample_order",
                        "base_resample_applied",
                        "base_resample_mode",
                        "base_resample_cval",
                        "mask_shape",
                        "mask_affine_4x4",
                        "mask_sha256",
                        "mask_world_bounds_mm",
                        "mask_resample_method",
                        "mask_resample_order",
                        "mask_resample_applied",
                        "mask_resample_mode",
                        "mask_resample_cval",
                        "mask_grid_alignment",
                        "mask_label_voxel_counts_source",
                        "mask_label_voxel_counts_target",
                        "mask_label_retention_fraction",
                        "mask_roi_world_bounds_mm",
                        "mask_roi_outside_target_fov_mm",
                        "mask_roi_outside_target_fov_voxels",
                        "mask_roi_outside_target_fov_threshold_voxels",
                        "mask_selected_voxel_count_source",
                        "mask_selected_voxel_count_target",
                        "source_width_mm",
                        "source_height_mm",
                        "target_width_mm",
                        "target_height_mm",
                        "display_content_width_mm",
                        "display_content_height_mm",
                        "display_width_voxel_spacing_mm",
                        "display_height_voxel_spacing_mm",
                    )
                    if key in envelope
                }
                if render_metadata:
                    entry["render_metadata"] = render_metadata
                if tool_name in _NIFTI_RENDERERS:
                    display_contract = _nifti_display_contract(envelope, image_info)
                    entry["display_contract"] = {
                        key: value
                        for key, value in display_contract.items()
                        if key != "source_affine_4x4"
                    }
                    nifti_renderer_identity_passes.append(
                        bool(display_contract["renderer_identity_pass"])
                    )
                    nifti_orientation_passes.append(bool(display_contract["orientation_pass"]))
                    nifti_physical_fov_passes.append(
                        bool(display_contract["physical_fov_pass"])
                    )
                    source_geometry = {
                        key: envelope[key]
                        for key in (
                            "source_sha256",
                            "source_shape",
                            "source_affine_4x4",
                            "source_world_bounds_mm",
                            "target_grid_policy",
                            "target_grid_spacing_rule",
                            "target_grid_affine_4x4",
                            "target_grid_shape",
                            "target_grid_voxel_count",
                            "target_grid_spacing_mm",
                            "target_grid_fov_mm",
                            "target_grid_world_bounds_mm",
                            "target_grid_padding_mm",
                            "target_grid_padding_voxels",
                            "target_grid_identity",
                            "base_resample_method",
                            "base_resample_order",
                            "base_resample_applied",
                            "base_resample_mode",
                            "base_resample_cval",
                        )
                        if key in envelope
                    }
                    source_provenance_ok = (
                        envelope.get("source_sha256") == source_entry["sha256"]
                        and bool(source_geometry)
                        and display_contract["target_grid_identity_pass"]
                        and display_contract["source_fov_coverage_pass"]
                    )
                    existing_geometry = source_entry.get("nifti_geometry")
                    if existing_geometry is None:
                        source_entry["nifti_geometry"] = source_geometry
                    elif existing_geometry != source_geometry:
                        source_provenance_ok = False
                    nifti_source_provenance_pass = (
                        nifti_source_provenance_pass and source_provenance_ok
                    )
                    if not source_provenance_ok:
                        fail("nifti_source_geometry_unverified")
                if tool_name == "sv_render_nifti_overlay":
                    coverage = _roi_coverage(args, envelope, image_info)
                    entry["roi_coverage"] = coverage
                    roi_passes.append(bool(coverage["passes"]))
                    renderer_identity_passes.append(bool(coverage["renderer_identity_pass"]))
                    orientation_passes.append(bool(coverage["orientation_pass"]))
                    physical_fov_passes.append(bool(coverage["physical_fov_pass"]))
                    mask_grid_alignment_passes.append(
                        bool(coverage["mask_grid_alignment_pass"])
                    )
                    mask_geometry = {
                        key: envelope[key]
                        for key in (
                            "mask_sha256",
                            "mask_shape",
                            "mask_affine_4x4",
                            "mask_world_bounds_mm",
                            "mask_resample_method",
                            "mask_resample_order",
                            "mask_resample_applied",
                            "mask_resample_mode",
                            "mask_resample_cval",
                            "mask_grid_alignment",
                            "mask_label_voxel_counts_source",
                            "mask_label_voxel_counts_target",
                            "mask_label_retention_fraction",
                            "mask_roi_world_bounds_mm",
                            "mask_roi_outside_target_fov_mm",
                            "mask_roi_outside_target_fov_voxels",
                            "mask_roi_outside_target_fov_threshold_voxels",
                            "mask_selected_voxel_count_source",
                            "mask_selected_voxel_count_target",
                        )
                        if key in envelope
                    }
                    mask_provenance_ok = (
                        envelope.get("mask_sha256") == source_entry.get("mask_sha256")
                        and bool(mask_geometry)
                        and coverage["mask_grid_alignment_pass"]
                    )
                    existing_mask_geometry = source_entry.get("mask_nifti_geometry")
                    if existing_mask_geometry is None:
                        source_entry["mask_nifti_geometry"] = mask_geometry
                    elif existing_mask_geometry != mask_geometry:
                        mask_provenance_ok = False
                    nifti_source_provenance_pass = (
                        nifti_source_provenance_pass and mask_provenance_ok
                    )
                    if not mask_provenance_ok:
                        fail("nifti_mask_geometry_unverified")
                manifest["views"].append(entry)

        if len(this_asset_paths) != requested:
            fail("view_count_mismatch")
        if not render_task.get("mask_path") and nifti_renderer_identity_passes:
            nifti_renderer_identity_pass = bool(
                all(nifti_renderer_identity_passes)
            )
            if not nifti_renderer_identity_pass:
                fail("nifti_renderer_identity_invalid")
            nifti_orientation_pass = bool(all(nifti_orientation_passes))
            if not nifti_orientation_pass:
                fail("nifti_orientation_unverified")
            nifti_physical_fov_pass = bool(all(nifti_physical_fov_passes))
            if not nifti_physical_fov_pass:
                fail("nifti_physical_fov_unverified")
        if render_task.get("mask_path"):
            medical_renderer_identity_pass = bool(
                renderer_identity_passes and all(renderer_identity_passes)
            )
            if not medical_renderer_identity_pass:
                fail("medical_renderer_identity_invalid")
            medical_orientation_pass = bool(orientation_passes and all(orientation_passes))
            if not medical_orientation_pass:
                fail("medical_orientation_unverified")
            medical_physical_fov_pass = bool(physical_fov_passes and all(physical_fov_passes))
            if not medical_physical_fov_pass:
                fail("medical_physical_fov_unverified")
            medical_mask_grid_alignment_pass = bool(
                mask_grid_alignment_passes and all(mask_grid_alignment_passes)
            )
            if not medical_mask_grid_alignment_pass:
                fail("medical_mask_grid_alignment_unverified")
            medical_roi_pass = bool(roi_passes and any(roi_passes))
            if not medical_roi_pass:
                fail("medical_roi_not_visible")
        manifest["staged_asset_count"] += 1
        asset_views.append({"asset_id": source_id, "views": this_asset_paths})

    no_raw_leak = not (env_dir / "asset").exists() and all(
        path.suffix == ".png" for path in views_dir.iterdir()
    )
    manifest["gates"] = {
        "view_count": len(container_paths) == requested * len(assets),
        "decode": True,
        "nonblank": True,
        "no_raw_asset_leakage": no_raw_leak,
        "provenance": len(manifest["views"]) == len(container_paths),
        "medical_roi": medical_roi_pass,
        "medical_renderer_identity": medical_renderer_identity_pass,
        "medical_orientation": medical_orientation_pass,
        "medical_physical_fov": medical_physical_fov_pass,
        "medical_mask_grid_alignment": medical_mask_grid_alignment_pass,
        "nifti_renderer_identity": nifti_renderer_identity_pass,
        "nifti_orientation": nifti_orientation_pass,
        "nifti_physical_fov": nifti_physical_fov_pass,
        "nifti_source_geometry": nifti_source_provenance_pass,
        "multi_asset_count": manifest["staged_asset_count"] == len(assets),
    }
    if not all(manifest["gates"].values()):
        fail("visibility_gate_failed")
    manifest["visibility_status"] = "technical_pass"
    manifest["answer_visibility_status"] = "pending_blind_audit"
    manifest["blind_audit_status"] = "required"
    plan_identity_payload = {
        "task_id": manifest["task_id"],
        "baseline": manifest["baseline"],
        "renderer": manifest["renderer"],
        "renderer_version": manifest["renderer_version"],
        "display_geometry_policy": manifest["display_geometry_policy"],
        "display_orientation_policy": manifest["display_orientation_policy"],
        "sources": manifest["sources"],
        "views": [
            {
                "asset_id": view["asset_id"],
                "view_index": view["view_index"],
                "tool": view["tool"],
                "args": view["args"],
            }
            for view in manifest["views"]
        ],
    }
    manifest["render_plan_identity"] = _sha256_json(plan_identity_payload)
    manifest["render_artifact_identity"] = _sha256_json({
        "render_plan_identity": manifest["render_plan_identity"],
        "sources": manifest["sources"],
        "views": [
            {
                "asset_id": view["asset_id"],
                "view_index": view["view_index"],
                "png_sha256": view["png_sha256"],
            }
            for view in manifest["views"]
        ],
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    staged = {
        "view_container_paths": container_paths,
        "asset_views": asset_views,
        "modality": assets[0]["asset_format"].lower(),
        "render_manifest": str(manifest_path),
        "visibility_status": "technical_pass",
    }
    return staged


def _cc2d_sensitive_path_tokens(task: dict) -> set[str]:
    """Return normalized path forms that must not reach CC2D artifacts."""
    raw_paths: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    isinstance(item, str)
                    and (str(key) == "path" or str(key).endswith("_path"))
                    and item
                ):
                    raw_paths.add(item)
                elif isinstance(item, (dict, list, tuple)):
                    collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    collect(task)
    tokens: set[str] = set()
    for raw_path in raw_paths:
        normalized = raw_path.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in {"", "."}]
        if not parts:
            continue
        tokens.add(raw_path.casefold())
        tokens.add(normalized.casefold())
        tokens.add(parts[-1].casefold())
        tokens.add(f"/data/{parts[-1]}".casefold())
        for index in range(0, len(parts) - 1):
            tokens.add("/".join(parts[index:]).casefold())
    return {token for token in tokens if token}


def _cc2d_validate_no_sensitive_strings(value: Any, sensitive_tokens: set[str]) -> None:
    """Fail when any nested string key/value contains a raw-path token."""
    if isinstance(value, str):
        normalized = value.replace("\\", "/").casefold()
        if any(token in normalized for token in sensitive_tokens):
            raise RenderVisibilityError("cc2d_sensitive_path_leakage")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _cc2d_validate_no_sensitive_strings(key, sensitive_tokens)
            _cc2d_validate_no_sensitive_strings(item, sensitive_tokens)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _cc2d_validate_no_sensitive_strings(item, sensitive_tokens)


def _cc2d_serialized_metadata(
    task: dict, staged: dict,
) -> tuple[dict[str, Any], set[str]]:
    """Build and validate path-free metadata for both CC2D serializers."""
    manifest_value = staged.get("render_manifest") if isinstance(staged, dict) else None
    manifest_path = Path(manifest_value) if isinstance(manifest_value, str) else None
    if manifest_path is None or not manifest_path.is_file():
        raise RenderVisibilityError("cc2d_render_manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise RenderVisibilityError("cc2d_render_manifest_invalid") from exc

    sources = manifest.get("sources")
    views = manifest.get("views")
    if not isinstance(sources, list) or not isinstance(views, list):
        raise RenderVisibilityError("cc2d_render_manifest_invalid")
    declared_assets = _task_assets(task)
    if len(sources) != len(declared_assets):
        raise RenderVisibilityError("cc2d_safe_metadata_incomplete")
    staged_paths = staged.get("view_container_paths")
    staged_asset_views = staged.get("asset_views")
    if not isinstance(staged_paths, list) or not isinstance(staged_asset_views, list):
        raise RenderVisibilityError("cc2d_safe_metadata_incomplete")

    safe_assets: list[dict[str, Any]] = []
    manifest_container_paths: list[str] = []
    expected_staged_asset_views: list[dict[str, Any]] = []
    for asset_index, (source, declared) in enumerate(zip(sources, declared_assets)):
        if not isinstance(source, dict):
            raise RenderVisibilityError("cc2d_render_manifest_invalid")
        asset_id = source.get("asset_id")
        asset_format = source.get("format")
        source_sha256 = source.get("sha256")
        expected_asset_id = declared["asset_id"] or f"asset-{asset_index:02d}"
        source_path = Path(declared["asset_path"])
        if (
            asset_id != expected_asset_id
            or asset_format != declared["asset_format"]
            or not source_path.is_file()
            or not _is_sha256(source_sha256)
            or source_sha256 != _sha256_path(source_path)
        ):
            raise RenderVisibilityError("cc2d_safe_metadata_incomplete")

        asset_manifest_views = [
            view for view in views
            if isinstance(view, dict) and view.get("asset_id") == asset_id
        ]
        rendered_view_files: list[str] = []
        asset_container_paths: list[str] = []
        for expected_index, view in enumerate(asset_manifest_views):
            view_index = view.get("view_index")
            filename = view.get("file")
            container_path = view.get("container_path")
            expected_filename = (
                f"view_{expected_index:02d}.png"
                if len(sources) == 1
                else f"asset_{asset_index:02d}_view_{expected_index:02d}.png"
            )
            expected_container_path = f"{_CONTAINER_ASSET_DIR}/{expected_filename}"
            if (
                _positive_int(view_index) != expected_index
                or filename != expected_filename
                or container_path != expected_container_path
            ):
                raise RenderVisibilityError("cc2d_metadata_view_ref_invalid")
            rendered_view_files.append(f"views/{expected_filename}")
            asset_container_paths.append(expected_container_path)
            manifest_container_paths.append(expected_container_path)
        if not rendered_view_files:
            raise RenderVisibilityError("cc2d_safe_metadata_incomplete")
        expected_staged_asset_views.append({
            "asset_id": asset_id,
            "views": asset_container_paths,
        })

        safe_asset: dict[str, Any] = {
            "asset_id": asset_id,
            "asset_format": asset_format,
            "sha256": source_sha256,
            "rendered_view_files": rendered_view_files,
        }
        mask_sha256 = source.get("mask_sha256")
        expected_mask = task.get("mask_path") if asset_index == 0 else None
        if expected_mask:
            mask_path = Path(str(expected_mask))
            if (
                not mask_path.is_file()
                or not _is_sha256(mask_sha256)
                or mask_sha256 != _sha256_path(mask_path)
            ):
                raise RenderVisibilityError("cc2d_safe_metadata_incomplete")
            safe_asset["mask_sha256"] = mask_sha256
        elif mask_sha256 is not None:
            raise RenderVisibilityError("cc2d_safe_metadata_incomplete")
        safe_assets.append(safe_asset)

    if (
        staged_paths != manifest_container_paths
        or staged_asset_views != expected_staged_asset_views
        or len(views) != len(manifest_container_paths)
    ):
        raise RenderVisibilityError("cc2d_metadata_view_ref_invalid")

    safe_task = dict(task)
    safe_task.pop("asset_path", None)
    safe_task.pop("asset", None)
    safe_task["assets"] = safe_assets
    safe_task.pop("mask_path", None)
    safe_task.pop("asset_hashes", None)
    metadata = _build_metadata(safe_task)
    metadata.pop("asset_path", None)
    sensitive_tokens = _cc2d_sensitive_path_tokens(task)
    _cc2d_validate_no_sensitive_strings(metadata, sensitive_tokens)
    _cc2d_validate_no_sensitive_strings(manifest, sensitive_tokens)
    return metadata, sensitive_tokens


def _instruction_with_container_assets(instruction: str, container_paths: dict[str, Any]) -> str:
    """Append a fenced ``sci3d`` JSON block telling the agent the in-container
    asset path(s), so it can hand them to the sciencevision MCP tools.

    The block is the single carrier of the paths. It used to be preceded by
    ``Asset: <path>`` prose lines restating the same strings, which is how a
    one-asset task came out as::

        Asset: /data/6LU7.cif

        ```sci3d
        {"asset_path": "/data/6LU7.cif"}
        ```

    — the path twice and no question in between. The block is also strictly more
    informative (it is the only place public ``assets`` appear) and
    ``_instruction.py`` already reads it in preference to the prose
    line, so the prose was pure duplication. That parser still accepts an
    ``Asset:`` line, because ``sci3d_agent/cli.py`` and the sci3d adapter build
    their own ``Question:``/``Asset:`` payloads; it is only this builder that
    stops emitting one.
    """
    body = instruction.strip()
    if not container_paths:
        return body + "\n"
    payload = {"asset_path": container_paths.get("asset_path", "")}
    assets = container_paths.get("assets") or []
    if assets:
        payload["assets"] = assets
    lines = [body, "", "```sci3d", json.dumps(payload), "```"]
    return "\n".join(lines) + "\n"


def _instruction_with_rendered_views(instruction: str, staged: dict) -> str:
    """Append the list of pre-rendered view PNGs + a fenced ``sci3d`` JSON block
    for the 2D multi-view baseline (①). Deliberately emits NO ``asset_path``:
    this harness sees only the rendered images, never the raw 3D file.
    """
    body = instruction.strip()
    views = staged.get("view_container_paths") or []
    if not views:
        return body + "\n"
    payload = {
        "views": list(views),
        "asset_views": staged.get("asset_views") or [],
        "modality": staged.get("modality", ""),
        "no_asset_access": True,
        "visibility_status": staged.get("visibility_status", ""),
    }
    lines = [
        body,
        "",
        "Views (pre-rendered; the original 3D file is intentionally unavailable):",
    ]
    lines.extend(f"- {v}" for v in views)
    lines.extend(["", "```sci3d", json.dumps(payload), "```"])
    return "\n".join(lines) + "\n"


def _stage_no_assets(task: dict, env_dir: Path) -> dict:
    """Create only the build context for the isolated text-only QC probe."""
    env_dir.mkdir(parents=True, exist_ok=True)
    return {"text_only": True}


def _instruction_text_only(instruction: str, staged: dict) -> str:
    """Remove eval asset plumbing while retaining natural question prose."""
    import re

    body = re.sub(r"```sci3d.*?```", "", instruction, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"(?im)^\s*Asset:\s*.*$", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body + "\n"


def _instruction_with_terminal_contract(task: dict, instruction: str) -> str:
    """Append the machine-parseable answer protocol for deterministic tasks."""
    schema = task.get("answer_schema") if isinstance(task.get("answer_schema"), dict) else {}
    fields = schema.get("fields")
    if isinstance(fields, dict):
        field_names = list(fields)
    elif isinstance(fields, (list, tuple)):
        field_names = [str(field) for field in fields]
    else:
        field_names = []
    if not _is_deterministic_task(task):
        shape = "a JSON object"
        if field_names:
            shape += " with exactly these required keys: " + ", ".join(field_names)
        return (
            instruction.rstrip()
            + "\n\nReturn the terminal answer as strict JSON using this exact marker:\n"
            + f"SCI3D_FINAL_JSON: <{shape}>\n"
        )
    if task.get("evaluation_eligible") is False:
        return instruction
    if not field_names:
        raise ValueError(
            f"deterministic task {task.get('task_id')} has no answer fields"
        )
    shape = {field: None for field in field_names}
    return (
        instruction.rstrip()
        + "\n\nFinal answer protocol: return exactly one JSON object with exactly "
        + "the declared fields. End with this marker and put no prose after it:\n"
        + "SCI3D_FINAL_JSON: "
        + json.dumps(shape, separators=(",", ":"))
        + "\n"
    )


AGENT_ENVS: dict[str, AgentEnv] = {
    # The stager copies nothing (the host agent reads the asset in-process);
    # it exists so the rewriter can put this task's host asset path into the
    # instruction. Only reached on the containerized route, so the historical
    # non-containerized host build is byte-identical.
    "host": AgentEnv("host", DF_HOST, _stage_host_paths, _instruction_with_container_assets),
    "codex": AgentEnv(
        "codex", DF_CODEX,
        _stage_assets, _instruction_with_container_assets,
    ),
    "codex-mcp": AgentEnv(
        "codex-mcp", DF_CODEX_MCP,
        _stage_assets, _instruction_with_container_assets,
        env_provider=_viewer_family_env,
    ),
    "codex-2d": AgentEnv(
        "codex-2d", DF_CODEX_2D,
        _stage_rendered_views, _instruction_with_rendered_views,
    ),
    "codex-text": AgentEnv(
        "codex-text", DF_CODEX_TEXT,
        _stage_no_assets, _instruction_text_only,
    ),
    # Explicit legacy routes for reproducing historical task trees. The six
    # current baseline defaults above never select these entries.
    "claude-code": AgentEnv(
        "claude-code", DF_CLAUDE_CODE,
        _stage_assets, _instruction_with_container_assets,
    ),
    "claude-code-mcp": AgentEnv(
        "claude-code-mcp", DF_CLAUDE_CODE_MCP,
        _stage_assets, _instruction_with_container_assets,
        env_provider=_viewer_family_env,
    ),
    "claude-code-2d": AgentEnv(
        "claude-code-2d", DF_CLAUDE_CODE_2D,
        _stage_rendered_views, _instruction_with_rendered_views,
    ),
    "claude-code-text": AgentEnv(
        "claude-code-text", DF_CLAUDE_CODE_TEXT,
        _stage_no_assets, _instruction_text_only,
    ),
}


def build_one(
    task: dict,
    out_dir: Path,
    *,
    containerized: bool = False,
    agent_env: str = "host",
    agent_allowed_hosts: Iterable[str] | None = None,
    mcp_catalog: Mapping[str, Any] | None = None,
    mcp_worker_image: str = _MCP_WORKER_IMAGE,
) -> Path:
    """Build one task directory.

    ``mcp_catalog`` must come from the exact worker image's
    ``sciencevision-mcp --surface`` output.  A missing catalog is retained as
    an explicit diagnostic state so legacy tree generation remains inspectable,
    but the route-health and static-preflight gates reject it for official use.
    """
    task_id = str(task["task_id"])
    if Path(task_id).name != task_id or task_id in {".", ".."}:
        raise ValueError("task_id must be one path component")
    if mcp_catalog is not None and not isinstance(mcp_catalog, Mapping):
        raise ValueError("mcp_catalog must be the JSON object emitted by sciencevision-mcp --surface")
    normalized_hosts = normalize_allowed_hosts(agent_allowed_hosts)
    env = AGENT_ENVS.get(agent_env, AGENT_ENVS["host"])
    asset_visibility = _route_asset_visibility(task, env.name)
    effective_task = _effective_task_for_route(task, asset_visibility)
    _assert_eligible_assets(effective_task)
    task_dir = out_dir / task_id
    # build_one owns this generated directory. Recreate it so changing N or
    # asset count cannot leak stale PNGs/assets into a later baseline build.
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)

    instruction_text = effective_task.get("instruction", "").strip() + "\n"
    verifier_metadata: dict[str, Any] | None = None
    cc2d_sensitive_tokens: set[str] | None = None
    staged: dict[str, Any] = {}
    if containerized and env.stager is not None:
        env_dir = task_dir / "environment"
        try:
            if env.stager is _stage_assets:
                staged = _stage_assets(
                    effective_task, env_dir, asset_visibility=asset_visibility,
                )
            else:
                staged = env.stager(effective_task, env_dir)
            if env.instruction_rewriter is not None:
                instruction_text = env.instruction_rewriter(
                    effective_task.get("instruction", ""), staged,
                )
            if env.name in {"codex-2d", "claude-code-2d"}:
                verifier_metadata, cc2d_sensitive_tokens = _cc2d_serialized_metadata(
                    effective_task, staged,
                )
        except Exception as exc:
            if env.name in {"codex-2d", "claude-code-2d"} and task_dir.exists():
                # The tree is fail-closed removed below; the reason must survive
                # it so a batch runner can record an exclusion receipt instead
                # of treating the missing directory as an unexplained gap.
                manifest_file = task_dir / "environment" / "render_manifest.json"
                reason = ""
                if manifest_file.is_file():
                    try:
                        reason = str(
                            json.loads(manifest_file.read_text()).get("failure_reason") or ""
                        )
                    except (OSError, ValueError):
                        reason = ""
                if not reason:
                    reason = str(exc)
                (out_dir / f"{task_id}.cc2d_excluded.json").write_text(
                    json.dumps({"task_id": task_id, "agent_env": env.name,
                                "n_views": effective_task.get("n_views"),
                                "failure_reason": reason}, sort_keys=True) + "\n"
                )
                shutil.rmtree(task_dir)
            raise

    viewer_family = _task_viewer_family(effective_task)
    effective_mcp_catalog = mcp_catalog if viewer_family is not None else None
    if (
        containerized
        and _is_mcp_agent_env(env.name)
        and viewer_family is not None
        and effective_mcp_catalog is None
    ):
        # ``--surface`` is intentionally deterministic and starts no server.
        # This source-side fallback keeps old callers from producing a route
        # contract with no catalog at all; official launchers should still pass
        # the JSON emitted by the *pinned worker image* via ``--mcp-surface``.
        from sci3d_agent.mcp.surface import surface_payload

        effective_mcp_catalog = surface_payload(viewer_family)
    route_contract = _route_contract_for_task(
        effective_task,
        env.name,
        catalog=effective_mcp_catalog,
    )
    if containerized:
        # Keep the complete scorer contract in verifier-only tests/test.sh.
        # ``_cc2d_serialized_metadata`` is still used for its raw-source-path
        # redaction, but it is never rendered into task.toml.
        verifier_metadata = verifier_metadata or _build_metadata(effective_task)
        if env.name == "host":
            # Kept solely for the historical host-container test/diagnostic
            # route. It has no meaningful agent isolation, so it does not
            # claim the hardened public/private metadata contract.
            task_toml_text = _render_task_toml(
                effective_task,
                containerized=True,
                metadata=verifier_metadata,
                agent_allowed_hosts=normalized_hosts,
            )
        else:
            public_metadata = _build_public_metadata(
                effective_task,
                container_paths=staged,
                agent_allowed_hosts=normalized_hosts,
                route_contract=route_contract,
                route_asset_visibility=asset_visibility.public_metadata(),
            )
            task_toml_text = _render_task_toml(
                effective_task,
                containerized=True,
                metadata=public_metadata,
                agent_allowed_hosts=normalized_hosts,
            )
    else:
        task_toml_text = _render_task_toml(effective_task, containerized=False)

    # The route module owns this wording.  Only CCMCP receives it: a CC task's
    # negative guarantee is recorded in public metadata/health validation, not
    # injected into the natural-language question.
    if route_contract is not None and route_contract.get("route") == "ccmcp":
        from scripts.harbor.route_contracts import route_system_context

        instruction_text = instruction_text.rstrip() + "\n" + route_system_context(route_contract)
    if route_contract is not None and verifier_metadata is not None:
        # The separate scorer sees verifier-only metadata, not public
        # task.toml. Carry the contract across that trust boundary so route
        # health/eligibility is actually evaluated during container scoring.
        verifier_metadata = {**verifier_metadata, "route_contract": dict(route_contract)}
    instruction_text = _instruction_with_terminal_contract(effective_task, instruction_text)

    test_sh = task_dir / "tests" / "test.sh"
    if containerized:
        test_sh_text = _render_containerized_test_sh(effective_task, metadata=verifier_metadata)
        score_py_text = (_TEMPLATES_DIR / "score.py").read_text()
        if cc2d_sensitive_tokens is not None:
            try:
                for text in (
                    task_toml_text,
                    instruction_text,
                    score_py_text,
                ):
                    _cc2d_validate_no_sensitive_strings(text, cc2d_sensitive_tokens)
            except RenderVisibilityError:
                shutil.rmtree(task_dir)
                raise
        test_sh.write_text(test_sh_text)
        (task_dir / "tests" / "score.py").write_text(score_py_text)
        env_dir = task_dir / "environment"
        env_dir.mkdir(parents=True, exist_ok=True)
        if _is_mcp_agent_env(env.name):
            if route_contract is None:
                raise AssertionError("CC-MCP environment requires a route contract")
            mcp = _route_mcp(route_contract)
            catalog = mcp.get("catalog") if isinstance(mcp.get("catalog"), Mapping) else {}
            # The shared renderer owns service names, loopback URL, receipt
            # path and Harbor-compatible compose shape. Do not reproduce those
            # details here: Harbor appends its egress overlay after this task
            # compose file.
            # Viewer family is safe agent-visible route context and retaining
            # it here preserves the existing per-task diagnostic surface; the
            # worker receives the same value from the immutable contract below.
            per_task = env.env_provider(effective_task) if env.env_provider is not None else {}
            (env_dir / "Dockerfile").write_text(_with_env(env.dockerfile, per_task))
            (env_dir / MCP_WORKER_PACKAGING.task_asset_manifest_filename).write_text(
                MCP_WORKER_PACKAGING.render_task_asset_manifest(staged)
            )
            (env_dir / "Dockerfile.mcp-worker").write_text(
                MCP_WORKER_PACKAGING.render_dockerfile(image=mcp_worker_image)
            )
            (env_dir / "docker-compose.yaml").write_text(
                MCP_WORKER_PACKAGING.render_compose(
                    viewer_family=str(mcp.get("viewer_family") or "") or None,
                    catalog_sha256=str(catalog.get("catalog_sha256") or "") or None,
                    schema_sha256=str(catalog.get("schema_sha256") or "") or None,
                    health_receipt_path=MCP_WORKER_PACKAGING.health_receipt_path,
                    http_surface=MCP_WORKER_PACKAGING.http_surface,
                )
            )
        else:
            per_task = env.env_provider(effective_task) if env.env_provider is not None else {}
            (env_dir / "Dockerfile").write_text(_with_env(env.dockerfile, per_task))
    else:
        test_sh.write_text(_TEST_SH)
    (task_dir / "task.toml").write_text(task_toml_text)
    (task_dir / "instruction.md").write_text(instruction_text)
    test_sh.chmod(0o755)
    return task_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", type=Path, default=_DEFAULT_TASKS)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=None, help="only generate the first N tasks")
    ap.add_argument(
        "--containerized",
        action="store_true",
        help="emit the containerized route (separate verifier env using the "
        "shared sci3d-scorer image + tests/score.py) instead of the host route",
    )
    ap.add_argument(
        "--agent-env",
        choices=sorted(AGENT_ENVS),
        default="codex",
        help="agent environment for the containerized route (default: codex)",
    )
    ap.add_argument(
        "--agent-allowed-host",
        action="append",
        default=[],
        help="exact model-gateway hostname; repeat for each explicitly allowed host",
    )
    ap.add_argument(
        "--mcp-surface",
        type=Path,
        default=None,
        help="JSON emitted by the exact sciencevision-mcp worker image's --surface command",
    )
    ap.add_argument(
        "--mcp-worker-image",
        default=_MCP_WORKER_IMAGE,
        help="immutable-or-versioned worker image reference; official preflight requires a digest",
    )
    args = ap.parse_args()

    mcp_catalog: Mapping[str, Any] | None = None
    if args.mcp_surface is not None:
        loaded = json.loads(args.mcp_surface.read_text())
        if not isinstance(loaded, Mapping):
            ap.error("--mcp-surface must contain one JSON object")
        mcp_catalog = loaded

    data = json.loads(Path(args.tasks).read_text())
    tasks = data["tasks"] if isinstance(data, dict) and "tasks" in data else data
    if args.limit is not None:
        tasks = tasks[: args.limit]

    args.out.mkdir(parents=True, exist_ok=True)
    built = [
        build_one(
            t,
            args.out,
            containerized=args.containerized,
            agent_env=args.agent_env,
            agent_allowed_hosts=args.agent_allowed_host,
            mcp_catalog=mcp_catalog,
            mcp_worker_image=args.mcp_worker_image,
        )
        for t in tasks
    ]
    route = "containerized" if args.containerized else "host-run"
    print(f"generated {len(built)} Harbor task(s) [{route}] under {args.out}")
    for d in built:
        print(f"  {d.relative_to(_REPO_ROOT) if _REPO_ROOT in d.parents else d}")


if __name__ == "__main__":
    main()
