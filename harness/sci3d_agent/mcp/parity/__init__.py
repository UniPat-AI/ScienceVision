"""Viewer-family mapping used to gate the agent-side MCP surface.

The original platform snapshot is not part of this checkout.  This small,
runtime-only mapping therefore names only the viewer families the local MCP can
serve and fails closed for unknown families.  It is intentionally separate from
the historical parity report in ``docs/mcp``.
"""

from .allowlist import (  # noqa: F401
    LIVE_REGISTRY_BY_VIEWER,
    UnknownFamilyError,
    advertised_tools_for_family,
    families,
    live_registry_for_viewer,
)

__all__ = [
    "LIVE_REGISTRY_BY_VIEWER",
    "UnknownFamilyError",
    "advertised_tools_for_family",
    "families",
    "live_registry_for_viewer",
]
