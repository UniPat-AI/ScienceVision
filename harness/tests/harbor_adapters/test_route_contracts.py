"""Route isolation, live-catalog, and MCP evidence contract coverage."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor_adapters.sci3d._route_contracts import (  # noqa: E402
    MCP_HTTP_HEALTH_PATH,
    MCP_HTTP_URL,
    build_route_contract,
    canonicalize_route_contract,
    canonical_catalog,
    evaluate_route_eligibility,
    route_system_context,
    validate_health_receipt,
    write_health_receipt,
)


def _surface(*names: str, viewer_family: str | None = None, salt: str = "") -> dict:
    raw = {
        "viewer_family": viewer_family,
        "tools": [
            {"name": name, "schema_sha256": f"schema:{name}:{salt}"}
            for name in names
        ],
    }
    canon = canonical_catalog(raw)
    assert canon is not None
    raw.update(
        {
            key: canon[key]
            for key in ("tool_names", "tool_count", "catalog_sha256", "schema_sha256")
        }
    )
    return raw


def _healthy_receipt(tmp_path: Path, contract: dict, surface: dict, **kwargs) -> dict:
    return write_health_receipt(
        tmp_path,
        contract,
        registered_servers=["sciencevision"],
        server_surface=surface,
        exposed_tools=surface["tool_names"],
        exposed_schemas=surface["tools"],
        **kwargs,
    )


def test_cc_is_a_negative_guarantee_not_a_label(tmp_path: Path):
    contract = build_route_contract("cc", allowed_tools=("sv3d_render",))
    assert contract["mcp"]["exposed"] is False
    assert contract["mcp"]["allowed_tools"] == []
    assert "no-MCP control" in route_system_context(contract)

    clean = write_health_receipt(tmp_path, contract, registered_servers=[], exposed_tools=[])
    assert validate_health_receipt(clean, contract)["status"] == "eligible"

    leaked = write_health_receipt(
        tmp_path,
        contract,
        registered_servers=["sciencevision"],
        exposed_tools=["mcp__sciencevision__sv3d_render"],
        server_surface=_surface("sv3d_render"),
    )
    verdict = validate_health_receipt(leaked, contract)
    assert verdict["status"] == "infra_invalid"
    assert {"cc_mcp_registered", "cc_mcp_exposed", "cc_mcp_surface_present"} <= set(
        verdict["reason_codes"]
    )


def test_toml_empty_sentinels_are_normalized_before_receipt_hashing():
    """Public TOML turns optional ``None`` fields into empty strings."""
    original = build_route_contract("cc")
    toml_round_trip = {
        **original,
        "mcp": {
            **original["mcp"],
            "viewer_family": "",
            "catalog": "",
        },
    }
    assert canonicalize_route_contract(toml_round_trip) == original


def test_ccmcp_catalog_and_schema_match_are_required(tmp_path: Path):
    surface = _surface("sv3d_open_scene", "sv3d_render", viewer_family="gltf.v1")
    contract = build_route_contract(
        "ccmcp",
        viewer_family="gltf.v1",
        allowed_tools=("sv3d_open_scene", "sv3d_render"),
        required_tool_groups=(("sv3d_open_scene",),),
        min_successful_calls=1,
        render_required=True,
        catalog=surface,
    )
    receipt = _healthy_receipt(tmp_path, contract, surface)
    assert validate_health_receipt(receipt, contract)["status"] == "eligible"
    context = route_system_context(contract)
    assert "MUST obtain" in context
    assert "renderer_skipped" in context


def test_73_vs_53_catalog_drift_is_infra_invalid(tmp_path: Path):
    expected = _surface(*(f"sv_tool_{index}" for index in range(73)))
    actual = _surface(*(f"sv_tool_{index}" for index in range(53)))
    contract = build_route_contract("ccmcp", catalog=expected)
    receipt = _healthy_receipt(tmp_path, contract, actual)
    verdict = validate_health_receipt(receipt, contract)
    assert verdict["status"] == "infra_invalid"
    assert "catalog_mismatch" in verdict["reason_codes"]


def test_schema_drift_is_not_hidden_by_equal_tool_names(tmp_path: Path):
    expected = _surface("sv3d_open_scene", "sv3d_render", salt="old")
    actual = _surface("sv3d_open_scene", "sv3d_render", salt="new")
    contract = build_route_contract("ccmcp", catalog=expected)
    verdict = validate_health_receipt(_healthy_receipt(tmp_path, contract, actual), contract)
    assert verdict["status"] == "infra_invalid"
    assert "schema_mismatch" in verdict["reason_codes"]


def test_renderer_skipped_is_agent_noncompliance_not_success(tmp_path: Path):
    surface = _surface("sv3d_open_scene", "sv3d_render")
    contract = build_route_contract(
        "ccmcp",
        allowed_tools=("sv3d_open_scene", "sv3d_render"),
        required_tool_groups=(("sv3d_open_scene",),),
        min_successful_calls=1,
        render_required=True,
        catalog=surface,
    )
    receipt = _healthy_receipt(tmp_path, contract, surface)
    verdict = evaluate_route_eligibility(
        contract,
        receipt,
        [
            {"name": "mcp__sciencevision__sv3d_open_scene", "outcome": "ok"},
            {
                "name": "mcp__sciencevision__sv3d_render",
                "outcome": "error",
                "error_text": "renderer_skipped: no EGL device",
            },
        ],
    )
    assert verdict["status"] == "agent_noncompliant"
    assert "required_render_not_successful" in verdict["reason_codes"]


def test_http_worker_receipt_uses_the_shared_c_group_contract(tmp_path: Path):
    surface = _surface("sv3d_open_scene")
    contract = build_route_contract("ccmcp", catalog=surface)
    receipt = _healthy_receipt(
        tmp_path,
        contract,
        surface,
        worker_health={
            "schema_version": "sciencevision.mcp_worker_health.v1",
            "status": "ready",
            "http_surface": MCP_HTTP_URL,
            "viewer_family": "",
            "catalog_sha256": surface["catalog_sha256"],
            "schema_sha256": surface["schema_sha256"],
        },
    )
    assert receipt["service_health_path"] == MCP_HTTP_HEALTH_PATH
    assert validate_health_receipt(receipt, contract)["status"] == "eligible"


def test_bad_or_missing_receipt_route_is_invalid_not_an_exception(tmp_path: Path):
    contract = build_route_contract("cc")
    receipt = write_health_receipt(tmp_path, contract, registered_servers=[], exposed_tools=[])
    receipt["route"] = "not-a-route"
    verdict = validate_health_receipt(receipt, contract)
    assert verdict["status"] == "infra_invalid"
    assert "route_mismatch" in verdict["reason_codes"]
