"""Launcher-facing re-export of the container-safe route contract API.

Keep the implementation in ``harbor_adapters.sci3d`` because the scorer image
contains that package but deliberately omits ``scripts/``.  Generators and
launchers should import from this module; scorers import the implementation
directly.
"""
from harbor_adapters.sci3d._route_contracts import (  # noqa: F401
    MCP_HEALTH_FILENAME,
    MCP_HEALTH_SCHEMA_VERSION,
    MCP_HTTP_HEALTH_PATH,
    MCP_HTTP_URL,
    MCP_SERVER_NAME,
    MCP_SERVICE_NAME,
    ROUTE_CONTRACT_SCHEMA_VERSION,
    HealthVerdict,
    RouteContractError,
    bare_tool_name,
    build_route_contract,
    canonicalize_route_contract,
    canonical_catalog,
    contract_sha256,
    evaluate_health_receipt,
    evaluate_route_eligibility,
    load_health_receipt,
    qualified_tool_name,
    route_system_context,
    validate_health_receipt,
    write_health_receipt,
)

__all__ = [
    "MCP_HEALTH_FILENAME",
    "MCP_HEALTH_SCHEMA_VERSION",
    "MCP_HTTP_HEALTH_PATH",
    "MCP_HTTP_URL",
    "MCP_SERVER_NAME",
    "MCP_SERVICE_NAME",
    "ROUTE_CONTRACT_SCHEMA_VERSION",
    "HealthVerdict",
    "RouteContractError",
    "bare_tool_name",
    "build_route_contract",
    "canonicalize_route_contract",
    "canonical_catalog",
    "contract_sha256",
    "evaluate_health_receipt",
    "evaluate_route_eligibility",
    "load_health_receipt",
    "qualified_tool_name",
    "route_system_context",
    "validate_health_receipt",
    "write_health_receipt",
]
