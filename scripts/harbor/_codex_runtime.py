"""Shared fail-closed runtime configuration for Harbor's Codex agent."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from harbor_adapters.sci3d._route_contracts import contract_sha256
from scripts.harbor._mcp_route_runtime import (
    NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT,
    ROUTE_CONTRACT_CODEX_AGENT,
)


_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
MCP_CONFIG = Path(__file__).resolve().parent / "templates" / "claude_code_mcp.mcp.json"


class CodexRuntimeError(ValueError):
    """Raised when an evaluation would otherwise start with ambiguous runtime state."""


@dataclass(frozen=True)
class CodexRuntime:
    model: str
    auth_mode: str
    base_url_mode: str
    agent_env: tuple[str, ...] = field(repr=False)


def _auth_path(raw: str, home: Path) -> Path:
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home / raw[2:]
    return Path(raw)


def _has_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_auth_json(path: Path) -> None:
    """Require one of the credential shapes written by the Codex CLI."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CodexRuntimeError(f"Codex auth JSON is unreadable or malformed: {path}") from exc
    if not isinstance(payload, dict) or not payload:
        raise CodexRuntimeError(f"Codex auth JSON must be a non-empty object: {path}")

    if _has_nonempty_string(payload.get("OPENAI_API_KEY")):
        return
    tokens = payload.get("tokens")
    if isinstance(tokens, dict) and any(
        _has_nonempty_string(tokens.get(name))
        for name in ("access_token", "refresh_token", "id_token")
    ):
        return
    raise CodexRuntimeError(
        "Codex auth JSON has no recognized credential field "
        f"(OPENAI_API_KEY or tokens): {path}"
    )


def resolve_codex_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> CodexRuntime:
    """Resolve an explicit model and one usable auth source without leaking it."""
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    model = env.get("CODEX_MODEL", "").strip()
    if not model:
        raise CodexRuntimeError(
            "CODEX_MODEL is required; repository runners do not infer it from local config"
        )
    if _MODEL_RE.fullmatch(model) is None:
        raise CodexRuntimeError("CODEX_MODEL contains unsupported characters")

    agent_env: list[str] = []
    api_key = env.get("OPENAI_API_KEY", "")
    explicit_auth = env.get("CODEX_AUTH_JSON_PATH", "").strip()
    default_auth = home / ".codex" / "auth.json"
    if api_key.strip():
        auth_mode = "openai_api_key"
        # Harbor's Codex adapter reads OPENAI_API_KEY directly from the parent
        # process environment. Do not duplicate a secret into process argv.
    elif explicit_auth:
        path = _auth_path(explicit_auth, home)
        if not path.is_file():
            raise CodexRuntimeError("CODEX_AUTH_JSON_PATH does not name a file")
        _validate_auth_json(path)
        auth_mode = "auth_json"
        agent_env.append(f"CODEX_AUTH_JSON_PATH={path}")
    elif default_auth.is_file():
        _validate_auth_json(default_auth)
        auth_mode = "auth_json"
        agent_env.append("CODEX_FORCE_AUTH_JSON=1")
    else:
        raise CodexRuntimeError(
            "Codex auth is required: set OPENAI_API_KEY or provide a non-empty Codex auth.json"
        )

    base_url = env.get("OPENAI_BASE_URL", "")
    base_url_mode = "custom" if base_url else "default"

    return CodexRuntime(
        model=model,
        auth_mode=auth_mode,
        base_url_mode=base_url_mode,
        agent_env=tuple(agent_env),
    )


def _route_contract_mcp_exposed(route_contract: Mapping[str, Any]) -> bool:
    """Read the one route fact the launcher must enforce before starting Codex."""
    route = str(route_contract.get("route") or "").strip().lower()
    mcp = route_contract.get("mcp")
    if not isinstance(mcp, Mapping):
        raise CodexRuntimeError("route contract has no mcp mapping")
    exposed = mcp.get("exposed")
    if not isinstance(exposed, bool):
        raise CodexRuntimeError("route contract mcp.exposed must be boolean")
    aliases = {"cc": False, "codex": False, "ccmcp": True, "cc-mcp": True, "codex-mcp": True}
    if route not in aliases:
        raise CodexRuntimeError("route contract has an unknown route")
    if exposed is not aliases[route]:
        raise CodexRuntimeError("route contract route/MCP exposure conflict")
    return exposed


def resolve_baseline_agent(baseline: str) -> str:
    """Harbor agent import path for a matrix baseline label.

    ``3d-mcp`` is the tools-only twin of ``ccmcp``: the same worker-backed MCP
    route with Codex's shell tool disabled by the adapter.
    """
    if baseline == "3d-mcp":
        return NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT
    if baseline in {"cc", "ccmcp"}:
        return ROUTE_CONTRACT_CODEX_AGENT
    return "codex"


def baseline_exposes_mcp(baseline: str) -> bool:
    return baseline in {"ccmcp", "3d-mcp"}


def harbor_agent_args(
    runtime: CodexRuntime,
    baseline: str,
    *,
    route_contract: Mapping[str, Any] | None = None,
) -> list[str]:
    """Build the common Codex argv, adding MCP only to the MCP baselines.

    When a route contract is supplied it is a preflight assertion, not a hint:
    a CC run whose argv has MCP (or an MCP baseline whose argv does not) stops
    before an incomparable trial is launched.
    """
    args = [
        "--agent", resolve_baseline_agent(baseline), "--model", runtime.model,
    ]
    for item in runtime.agent_env:
        args.extend(("--ae", item))
    baseline_mcp = baseline_exposes_mcp(baseline)
    if route_contract is not None and _route_contract_mcp_exposed(route_contract) != baseline_mcp:
        raise CodexRuntimeError(
            f"baseline {baseline!r} conflicts with its route contract MCP exposure"
        )
    if baseline_mcp:
        if not MCP_CONFIG.is_file():
            raise CodexRuntimeError(f"MCP config is missing: {MCP_CONFIG}")
        args.extend(("--mcp-config", str(MCP_CONFIG)))
    return args


def record_codex_runtime(
    root: Path,
    runtime: CodexRuntime,
    *,
    route_contract: Mapping[str, Any] | None = None,
) -> Path:
    """Record reproducibility metadata, deliberately excluding credential material."""
    root.mkdir(parents=True, exist_ok=True)
    output = root / "codex-runtime.json"
    payload = {
        "agent": "codex",
        "model": runtime.model,
        "auth": runtime.auth_mode,
        "openai_base_url": runtime.base_url_mode,
        "mcp_baseline": "ccmcp",
    }
    if route_contract is not None:
        exposed = _route_contract_mcp_exposed(route_contract)
        payload["route"] = str(route_contract.get("route"))
        payload["route_contract_sha256"] = contract_sha256(route_contract)
        payload["mcp_expected"] = exposed
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return output


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        runtime = resolve_codex_runtime()
    except CodexRuntimeError as exc:
        parser.error(str(exc))
    record = record_codex_runtime(args.record_dir, runtime)
    print(
        f"Codex runtime: model={runtime.model} auth={runtime.auth_mode} "
        f"base_url={runtime.base_url_mode} record={record}",
        flush=True,
    )


if __name__ == "__main__":
    main()
