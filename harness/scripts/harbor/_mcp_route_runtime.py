"""Runtime MCP-health evidence for the isolated CC-family routes.

The task-local ScienceVision worker deliberately keeps its readiness receipt
inside the sidecar filesystem.  The agent container cannot read it (and must
not receive a Docker socket), but Harbor's compose-aware environment API can
execute a read-only command in that sidecar after ``docker compose up --wait``
has completed.  This module turns those live observations into the canonical
``/logs/agent/mcp_health.json`` record consumed by the scorer.

It does not import Harbor.  Keeping the small probe protocol duck-typed makes
it usable from the installed Codex adapter while allowing unit tests to model a
sidecar without starting Docker.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from harbor_adapters.sci3d._route_contracts import (
    MCP_HEALTH_FILENAME,
    MCP_HTTP_HEALTH_PATH,
    MCP_SERVICE_NAME,
    write_health_receipt,
)


# Keep the launcher import path in a Harbor-free module.  The implementation
# itself subclasses Harbor's installed Codex agent, so importing it during a
# scorer-only test would otherwise unnecessarily require Harbor.
ROUTE_CONTRACT_CODEX_AGENT = "harbor_adapters.sci3d.route_codex_agent:RouteContractCodex"

# The tools-only twin of the same adapter: identical route contract, with
# Codex's shell tool disabled so observations can only come from MCP tools.
NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT = (
    "harbor_adapters.sci3d.route_codex_agent:NoShellRouteContractCodex"
)


class _ServiceEnvironment(Protocol):
    """The small Harbor compose-service surface needed by the probe."""

    async def service_exec(
        self,
        command: str,
        *,
        service: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> Any: ...


_HEALTH_COMMAND = f"cat {MCP_HTTP_HEALTH_PATH}"
_SURFACE_COMMAND = "sciencevision-mcp --surface"
_PROBE_TIMEOUT_SEC = 20


def _route(contract: Mapping[str, Any]) -> str:
    return str(contract.get("route") or "").strip().lower().replace("-", "")


def _bounded_detail(value: object, *, limit: int = 800) -> str:
    """Keep failure diagnostics useful without storing arbitrary log blobs."""
    text = str(value or "").strip().replace("\x00", "")
    return text[:limit]


def _json_object(raw: object, *, label: str) -> dict[str, Any]:
    """Parse the one JSON object emitted by the worker diagnostics command."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"{label} probe returned no JSON")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # A dependency warning occasionally precedes the one-shot JSON payload.
        # The CLI's final non-empty line is still authoritative; do not accept a
        # non-JSON log line as a successful probe.
        parsed = None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            break
        if parsed is None:
            raise ValueError(f"{label} probe returned malformed JSON") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} probe JSON must be an object")
    return dict(parsed)


async def _probe_service_json(
    environment: _ServiceEnvironment,
    *,
    command: str,
    label: str,
) -> dict[str, Any]:
    result = await environment.service_exec(
        command,
        service=MCP_SERVICE_NAME,
        timeout_sec=_PROBE_TIMEOUT_SEC,
    )
    code = getattr(result, "return_code", 0)
    if code not in (0, None):
        stdout = _bounded_detail(getattr(result, "stdout", ""))
        stderr = _bounded_detail(getattr(result, "stderr", ""))
        raise RuntimeError(
            f"{label} probe exited {code}: {stdout or stderr or 'no diagnostic'}"
        )
    return _json_object(getattr(result, "stdout", ""), label=label)


def _server_names(values: Iterable[object] | None) -> list[str]:
    names: list[str] = []
    for value in values or ():
        name = str(getattr(value, "name", value) or "").strip()
        if name and name not in names:
            names.append(name)
    return names


async def write_route_health_receipt_from_environment(
    agent_dir: Path | str,
    contract: Mapping[str, Any],
    environment: _ServiceEnvironment,
    *,
    registered_servers: Iterable[object] | None = None,
) -> dict[str, Any]:
    """Probe the live worker and atomically write ``mcp_health.json``.

    This runs only after Harbor has started the environment; for compose
    environments its ``up --wait`` guarantees the worker healthcheck has had a
    chance to report readiness.  Probe failures intentionally still write a
    receipt with the error evidence.  The scorer will then classify the trial
    as ``infra_invalid`` rather than silently treating a missing receipt as a
    model failure.
    """
    route = _route(contract)
    names = _server_names(registered_servers)
    runtime: dict[str, Any] = {
        "receipt_source": "harbor_route_codex_adapter",
        "configured_mcp_servers": names,
    }

    if route == "cc":
        runtime["probe"] = "cc_negative_guarantee"
        return write_health_receipt(
            agent_dir,
            contract,
            registered_servers=names,
            exposed_tools=(),
            runtime=runtime,
        )

    if route != "ccmcp":
        raise ValueError(f"unsupported route for health receipt: {route!r}")

    runtime["probe"] = {
        "kind": "harbor_service_exec",
        "service": MCP_SERVICE_NAME,
        "health_command": _HEALTH_COMMAND,
        "surface_command": _SURFACE_COMMAND,
    }
    worker_health: dict[str, Any] | None = None
    server_surface: dict[str, Any] | None = None
    probe_errors: dict[str, str] = {}

    try:
        worker_health = await _probe_service_json(
            environment,
            command=_HEALTH_COMMAND,
            label="worker health",
        )
    except Exception as exc:  # receipt must exist even when the sidecar is broken
        probe_errors["worker_health"] = _bounded_detail(exc)

    try:
        # This command runs *inside the already-running worker image*.  It is
        # therefore the exact gated registry/schema surface, not a host-side
        # import of a potentially different checkout.
        server_surface = await _probe_service_json(
            environment,
            command=_SURFACE_COMMAND,
            label="worker surface",
        )
    except Exception as exc:  # see rationale above
        probe_errors["server_surface"] = _bounded_detail(exc)

    if probe_errors:
        runtime["probe_errors"] = probe_errors
    # The verifier already knows the fixed filename.  Do not copy a host trial
    # path into an agent-visible log merely to describe where it was written.
    runtime["receipt_file"] = MCP_HEALTH_FILENAME

    return write_health_receipt(
        agent_dir,
        contract,
        registered_servers=names,
        server_surface=server_surface,
        worker_health=worker_health,
        runtime=runtime,
    )


__all__ = [
    "NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT",
    "ROUTE_CONTRACT_CODEX_AGENT",
    "write_route_health_receipt_from_environment",
]
