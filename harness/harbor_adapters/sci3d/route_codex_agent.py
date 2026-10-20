"""Codex adapter that records CC/CC-MCP runtime evidence before a trial runs.

Harbor launches an agent only after its environment has started.  For a
compose-backed CC-MCP task that means the private ``sciencevision-mcp`` worker
has passed its Docker healthcheck.  This adapter uses Harbor's supported
``service_exec`` API to read the worker's private receipt and exact live
``--surface`` catalog, then writes the scorer-visible receipt under the agent
log mount before delegating to Harbor's normal Codex implementation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment

from ._route_contracts import canonicalize_route_contract
from scripts.harbor._mcp_route_runtime import (
    NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT,
    ROUTE_CONTRACT_CODEX_AGENT,
    write_route_health_receipt_from_environment,
)


def _task_route_contract(environment: BaseEnvironment) -> dict[str, Any]:
    """Load the public, task-bound route contract from the staged task TOML."""
    config_path = Path(environment.environment_dir).parent / "task.toml"
    try:
        # This uses Harbor's own parser, so the adapter reads the same public
        # metadata representation that Harbor accepted when it created the trial.
        from harbor.models.task.config import TaskConfig

        metadata = TaskConfig.model_validate_toml(config_path.read_text()).metadata
    except Exception as exc:
        raise RuntimeError(f"cannot read route contract from {config_path}: {exc}") from exc
    contract = metadata.get("route_contract") if isinstance(metadata, Mapping) else None
    if not isinstance(contract, Mapping):
        raise RuntimeError(f"task has no public route_contract: {config_path}")
    try:
        return canonicalize_route_contract(contract)
    except Exception as exc:
        raise RuntimeError(f"task route contract is not canonical: {config_path}: {exc}") from exc


class RouteContractCodex(Codex):
    """Normal Harbor Codex with an early, fail-closed route-health receipt."""

    async def setup(self, environment: BaseEnvironment) -> None:
        contract = _task_route_contract(environment)
        await write_route_health_receipt_from_environment(
            self.logs_dir,
            contract,
            environment,
            registered_servers=self.mcp_servers,
        )
        await super().setup(environment)


class NoShellRouteContractCodex(RouteContractCodex):
    """Route-contract Codex with the model's shell tool disabled.

    Harbor registers the MCP server independently of the shell feature (Codex
    spawns it from its own config), so ``--disable shell_tool`` removes the
    file-access path while leaving the MCP surface intact.
    """

    def build_cli_flags(self) -> str:
        flags = super().build_cli_flags()
        return f"{flags} --disable shell_tool".strip()


__all__ = [
    "NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT",
    "ROUTE_CONTRACT_CODEX_AGENT",
    "NoShellRouteContractCodex",
    "RouteContractCodex",
]
