"""Per-agent-env descriptors for Harbor task generation.

Single source of truth replacing the old (``_AGENT_ENV_DOCKERFILES`` dict +
``_ASSET_STAGING_ENVS`` set) pair. Each ``AgentEnv`` bundles the container
Dockerfile body with the two generation hooks: a *stager* (copies inputs into
the per-task ``environment/`` and returns a staged-paths dict) and an
*instruction_rewriter* (rewrites the prose instruction given those paths).
``host`` has neither.

Only the dataclass + Dockerfile strings live here; the stager/rewriter
callables stay in build_harbor_tasks (they touch the task dict + filesystem),
so importing this module never pulls in build_harbor_tasks (no import cycle).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Optional


@dataclass(frozen=True)
class MCPWorkerPackaging:
    """Pure templates for one private, per-task HTTP MCP worker.

    The task generator supplies already-computed route-contract fields.  This
    module deliberately does not derive viewer families, catalogue digests, or
    asset identifiers, so it cannot diverge from ``route_contracts``.
    """

    service_name: str = "sciencevision-mcp"
    port: int = 8765
    http_surface: str = "http://127.0.0.1:8765/mcp"
    health_receipt_path: str = "/run/sciencevision-mcp/health.json"
    task_asset_manifest_filename: str = "mcp-task-assets.json"
    task_asset_manifest_path: str = "/etc/sciencevision-mcp/task-assets.json"
    task_asset_manifest_schema: str = "sciencevision.mcp_task_assets.v1"

    @staticmethod
    def _image_ref(image: str) -> str:
        value = str(image).strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError("MCP worker image must be a single non-empty image reference")
        return value

    @staticmethod
    def _staged_data_path(value: object) -> str:
        """Accept only a normalized, task-local worker asset path.

        The manifest is deliberately derived *after* staging.  It must never
        contain the source checkout path, hashes, provenance, or a path which
        lets a model-selected ``asset_id`` escape the private ``/data`` tree.
        """
        path = str(value or "").strip()
        parsed = PurePosixPath(path)
        if (
            not path
            or "\x00" in path
            or not path.startswith("/data/")
            or len(parsed.parts) < 3
            or ".." in parsed.parts
        ):
            raise ValueError(
                "MCP task asset manifest accepts only normalized staged /data/<file> paths"
            )
        return path

    def render_task_asset_manifest(self, container_paths: Mapping[str, Any]) -> str:
        """Render the private per-task asset-id manifest for the MCP worker.

        ``container_paths`` is the return value of ``_stage_assets``.  The
        generator writes this exact text next to ``asset/`` in the worker-only
        Docker build context.  A missing upstream ``asset_id`` gets a stable
        task-local identifier rather than falling back to a source path or a
        global ``viewer/assets.json`` lookup; old exploratory fixtures can
        therefore remain runnable without reintroducing registry access.
        """
        raw_assets = container_paths.get("assets", ())
        if isinstance(raw_assets, (str, bytes)) or not isinstance(raw_assets, (list, tuple)):
            raise ValueError("MCP task asset manifest requires container_paths['assets'] list")

        rows: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        seen_paths: set[str] = set()
        for index, raw in enumerate(raw_assets):
            if not isinstance(raw, Mapping):
                raise ValueError("MCP task asset manifest entries must be mappings")
            asset_id = str(raw.get("asset_id") or "").strip() or f"task-asset-{index + 1}"
            if "\x00" in asset_id or asset_id in seen_ids:
                raise ValueError("MCP task asset manifest asset_id values must be unique and non-empty")
            path = self._staged_data_path(raw.get("asset_path", raw.get("path")))
            if path in seen_paths:
                raise ValueError("MCP task asset manifest asset paths must be unique")
            row = {
                "asset_id": asset_id,
                "asset_path": path,
                "format": str(raw.get("asset_format") or raw.get("format") or "obj").strip() or "obj",
                "role": str(raw.get("role") or ("primary" if index == 0 else "companion")).strip()
                or ("primary" if index == 0 else "companion"),
            }
            viewer = str(raw.get("viewer") or "").strip()
            if viewer:
                row["viewer"] = viewer
            rows.append(row)
            seen_ids.add(asset_id)
            seen_paths.add(path)

        return json.dumps(
            {"schema_version": self.task_asset_manifest_schema, "assets": rows},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"

    def render_dockerfile(self, *, image: str) -> str:
        """Render the worker-only Dockerfile that receives raw assets."""
        return (
            "# Generated task-local ScienceVision MCP worker.\n"
            "# Raw assets are private to this service, never to main.\n"
            f"FROM {self._image_ref(image)}\n"
            "COPY asset/ /data/\n"
            f"COPY --chown=sciencevision:sciencevision {self.task_asset_manifest_filename} "
            f"{self.task_asset_manifest_path}\n"
            "ENV SV_MCP_TASK_ISOLATED=1 \\\n"
            f"    SV_MCP_TASK_ASSET_MANIFEST={self.task_asset_manifest_path}\n"
        )

    def render_compose(
        self,
        *,
        viewer_family: str | None,
        catalog_sha256: str | None,
        schema_sha256: str | None = None,
        health_receipt_path: str | None = None,
        http_surface: str | None = None,
    ) -> str:
        """Render a no-network-override compose overlay for Harbor.

        Harbor's egress overlay appends after this file and gives ``main`` and
        the worker a shared namespace.  The MCP client consequently uses the
        loopback surface instead of fragile compose service DNS.
        """
        values = {
            "SV_HTTP_PORT": str(self.port),
            "SV_HTTP_HOST": "0.0.0.0",
            "SV_MCP_VIEWER_FAMILY": viewer_family or "",
            "SV_MCP_CATALOG_SHA256": catalog_sha256 or "",
            "SV_MCP_SCHEMA_SHA256": schema_sha256 or "",
            "SV_MCP_HEALTH_RECEIPT_PATH": health_receipt_path or self.health_receipt_path,
            "SV_MCP_HTTP_SURFACE": http_surface or self.http_surface,
            "SV_MCP_TASK_ISOLATED": "1",
            "SV_MCP_TASK_ASSET_MANIFEST": self.task_asset_manifest_path,
            "SV_RENDER_CACHE": "/var/lib/sciencevision-mcp/renders",
        }
        env_lines = "".join(
            f"      {key}: {json.dumps(value)}\n" for key, value in values.items()
        )
        return (
            "# Generated hardened MCP-worker overlay. Keep it free of explicit\n"
            "# networks/network_mode; Harbor owns egress enforcement.\n"
            "services:\n"
            "  main:\n"
            "    depends_on:\n"
            f"      {self.service_name}:\n"
            "        condition: service_healthy\n"
            f"  {self.service_name}:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.mcp-worker\n"
            "    init: true\n"
            "    environment:\n"
            f"{env_lines}"
            "    healthcheck:\n"
            "      test: [\"CMD\", \"python3\", \"/opt/sciencevision/bin/mcp_worker_healthcheck.py\"]\n"
            "      interval: 2s\n"
            "      timeout: 3s\n"
            "      retries: 30\n"
            "      start_period: 20s\n"
        )


MCP_WORKER_PACKAGING = MCPWorkerPackaging()

DF_HOST = (
    "# Minimal agent environment for the sci3d host-run route.\n"
    "# Sci3dAgent runs in the orchestrator process; this only satisfies\n"
    "# harbor's per-task environment requirement.\n"
    "#\n"
    "# FROM the locally-tagged sci3d-host-agent (an alias for python:3.12-slim)\n"
    "# so per-task env start never triggers a registry pull — pulling\n"
    "# python:3.12-slim per task timed out the first task on the slow H20\n"
    "# Docker network. Create the tag once:\n"
    "#   bash scripts/harbor/templates/build_host_agent_image.sh\n"
    "FROM sci3d-host-agent:latest\n"
    "WORKDIR /app\n"
)

DF_CODEX = (
    "# Agent environment for the bare Codex CLI harness (baseline ②:\n"
    "# a coding agent that reads the 3D file directly, no MCP tools). The base\n"
    "# image ships the `codex` CLI plus the slim inspection deps (nibabel /\n"
    "# trimesh / numpy / ...), and the task's 3D asset(s) are COPYd to /data\n"
    "# like the mcp env — so the only difference from codex-mcp is\n"
    "# whether the sciencevision MCP server is registered.\n"
    "FROM sci3d-codex:latest\n"
    "COPY asset/ /data/\n"
    "WORKDIR /app\n"
)

DF_CODEX_MCP = (
    "# Source-free Codex agent for the HTTP ScienceVision MCP route.\n"
    "# The separate sciencevision-mcp service alone receives repository source\n"
    "# and raw /data assets. Keep this Dockerfile free of COPY asset/.\n"
    "FROM sci3d-codex-mcp:latest\n"
    "ENV SV_MCP_HTTP_SURFACE=http://127.0.0.1:8765/mcp\n"
    "WORKDIR /app\n"
)

DF_CODEX_2D = (
    "# Agent environment for the 2D image multi-view harness (baseline ①:\n"
    "# a vision agent that sees only pre-rendered PNG views, never the raw 3D\n"
    "# file). Only the views/ dir is COPYd — no 3D asset, no MCP server. The\n"
    "# base image is node + the `codex` CLI with NO 3D-inspection libs, so a\n"
    "# leaked 3D file would still be unreadable (constraint hardening).\n"
    "FROM sci3d-codex-2d:latest\n"
    "COPY views/ /data/\n"
    "WORKDIR /app\n"
)

DF_CODEX_TEXT = (
    "# QC-only text probe: Codex receives question prose but no raw 3D\n"
    "# file, rendered image, viewer, or sciencevision MCP server. This is not\n"
    "# one of the six leaderboard baselines.\n"
    "FROM sci3d-codex-2d:latest\n"
    "WORKDIR /app\n"
)

# Legacy task-generation contracts retained for reproducing historical runs.
DF_CLAUDE_CODE = (
    "# Agent environment for the bare Claude Code CLI harness (baseline ②:\n"
    "# a coding agent that reads the 3D file directly, no MCP tools). The base\n"
    "# image ships the `claude` CLI plus the slim inspection deps (nibabel /\n"
    "# trimesh / numpy / ...), and the task's 3D asset(s) are COPYd to /data\n"
    "# like the mcp env — so the only difference from claude-code-mcp is\n"
    "# whether the sciencevision MCP server is registered.\n"
    "FROM sci3d-claude-code:latest\n"
    "COPY asset/ /data/\n"
    "WORKDIR /app\n"
)

DF_CLAUDE_CODE_MCP = (
    "# Source-free Claude Code agent for the HTTP ScienceVision MCP route.\n"
    "# The separate sciencevision-mcp service alone receives repository source\n"
    "# and raw /data assets. Keep this Dockerfile free of COPY asset/.\n"
    "FROM sci3d-claude-code-mcp:latest\n"
    "ENV SV_MCP_HTTP_SURFACE=http://127.0.0.1:8765/mcp\n"
    "WORKDIR /app\n"
)

DF_CLAUDE_CODE_2D = (
    "# Legacy 2D image multi-view harness.\n"
    "FROM sci3d-claude-code-2d:latest\n"
    "COPY views/ /data/\n"
    "WORKDIR /app\n"
)

DF_CLAUDE_CODE_TEXT = (
    "# Legacy QC-only text probe.\n"
    "FROM sci3d-claude-code-2d:latest\n"
    "WORKDIR /app\n"
)


@dataclass(frozen=True)
class AgentEnv:
    name: str
    dockerfile: str
    stager: Optional[Callable] = None
    instruction_rewriter: Optional[Callable] = None
    #: ``(task) -> {NAME: value}``, rendered as extra ``ENV`` lines appended to
    #: ``dockerfile``. harbor's ``--mcp-config`` is fixed per run, so anything
    #: that has to differ *per task* — the viewer family the MCP server narrows
    #: itself to — has to travel in the task's own image instead.
    env_provider: Optional[Callable] = None
