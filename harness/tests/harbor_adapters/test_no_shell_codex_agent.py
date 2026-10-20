"""Pins for the tools-only Codex route (matrix label ``3d-mcp``).

The route is ccmcp's worker-backed HTTP MCP surface with Codex's shell tool
disabled: Harbor registers the MCP server independently of the shell feature,
so ``--disable shell_tool`` removes the file-access path without touching MCP.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor._mcp_route_runtime import (  # noqa: E402
    NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT,
    ROUTE_CONTRACT_CODEX_AGENT,
)


def test_no_shell_agent_import_path_is_its_own_adapter():
    assert NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT == (
        "harbor_adapters.sci3d.route_codex_agent:NoShellRouteContractCodex"
    )
    assert NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT != ROUTE_CONTRACT_CODEX_AGENT


def test_no_shell_adapter_appends_the_disable_flag_to_the_parents_flags(tmp_path):
    pytest.importorskip("harbor")
    from harbor_adapters.sci3d.route_codex_agent import (
        NoShellRouteContractCodex,
        RouteContractCodex,
    )

    parent_flags = RouteContractCodex(logs_dir=tmp_path).build_cli_flags()
    flags = NoShellRouteContractCodex(logs_dir=tmp_path).build_cli_flags()
    assert flags == f"{parent_flags} --disable shell_tool".strip()
    assert flags.split()[-2:] == ["--disable", "shell_tool"]
