"""Live MCP catalog identity and schema fingerprints.

``sciencevision-mcp --surface`` is intentionally cheap: it imports the tool
registry but starts no transport or renderer.  Build/run code records its JSON
verbatim, then route-contract validation compares it to the task contract and
to the tools the client reports.  That makes a 73-vs-53 drift explicit instead
of silently treating a different tool catalog as the same MCP baseline.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence

from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.tools import ALL_TOOLS


SURFACE_SCHEMA_VERSION = "sciencevision.mcp_surface.v1"
SERVER_NAME = "sciencevision"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def selected_tools(
    viewer_family: str | None = None,
    *,
    tools: Sequence[ToolSpec] | Iterable[ToolSpec] | None = None,
) -> list[ToolSpec]:
    """Select the actual server surface; unknown families fail closed."""
    source = list(ALL_TOOLS if tools is None else tools)
    if not viewer_family:
        return source
    from sci3d_agent.mcp.parity.allowlist import advertised_tools_for_family

    return list(advertised_tools_for_family(viewer_family, source))


def tool_schema_sha256(spec: ToolSpec) -> str:
    """Fingerprint the strict schema the MCP server itself advertises."""
    return _sha256(spec.input_schema)


def surface_payload(
    viewer_family: str | None = None,
    *,
    tools: Sequence[ToolSpec] | Iterable[ToolSpec] | None = None,
) -> dict[str, Any]:
    """Build deterministic, JSON-serializable live-catalog evidence."""
    selected = selected_tools(viewer_family, tools=tools)
    entries = [
        {"name": spec.name, "schema_sha256": tool_schema_sha256(spec)}
        for spec in selected
    ]
    names = [entry["name"] for entry in entries]
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "server": SERVER_NAME,
        "viewer_family": viewer_family or None,
        "gated": bool(viewer_family),
        "tool_count": len(entries),
        "tool_names": names,
        "tools": entries,
        # Keep these payloads aligned with ``route_contracts.canonical_catalog``.
        "catalog_sha256": _sha256(names),
        "schema_sha256": _sha256(entries),
    }
