"""Tests for the MCP fall-through path in sci3d_agent.dispatch.dispatch."""
from __future__ import annotations

import pytest

from sci3d_agent import mcp_bridge
from sci3d_agent.dispatch import dispatch
from sci3d_agent.llm_client import ToolCall
from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.tools import ALL_TOOLS
from sci3d_agent.modes import get_profile


@pytest.fixture
def index_snapshot():
    snapshot = dict(mcp_bridge._INDEX)
    yield
    mcp_bridge._INDEX.clear()
    mcp_bridge._INDEX.update(snapshot)


def _install_fake(name: str, handler) -> None:
    spec = mcp_bridge._INDEX[name]
    mcp_bridge._INDEX[name] = ToolSpec(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema,
        handler=handler,
        category=spec.category,
    )


def test_mode4_advertises_finish_plus_all_mcp():
    profile = get_profile("mode4_mcp")
    names = [t["name"] for t in profile.advertised_tools]
    assert names[0] == "finish"
    assert len(names) == 1 + len(ALL_TOOLS)


def test_dispatch_routes_mcp_tool_in_mode4(index_snapshot):
    seen = {}

    def fake(args):
        seen["called"] = True
        return {"records": [], "total": 0, "offset": 0, "limit": 25}

    _install_fake("sv_list_matched_datasets", fake)

    profile = get_profile("mode4_mcp")
    call = ToolCall(name="sv_list_matched_datasets", args={})
    result, violations = dispatch(call, profile, kernel=None, bridge=None)
    assert seen.get("called") is True
    assert violations == []
    assert "total" in result


def test_dispatch_rejects_mcp_tool_in_mode1():
    """Mode 1 doesn't advertise MCP tools — call must be rejected before reaching the bridge."""
    profile = get_profile("mode1_no_vision")
    call = ToolCall(name="sv_list_matched_datasets", args={})
    with pytest.raises(ValueError, match="not advertised"):
        dispatch(call, profile, kernel=None, bridge=None)


def test_dispatch_legacy_finish_still_works_in_mode4():
    """Adding MCP fall-through must not break the legacy `finish` tool."""
    profile = get_profile("mode4_mcp")
    result, violations = dispatch(
        ToolCall(name="finish", args={"answer": "42"}),
        profile,
        kernel=None,
        bridge=None,
    )
    assert violations == []
    assert result.get("answer") == "42"


def test_dispatch_unknown_tool_in_mode4_raises():
    profile = get_profile("mode4_mcp")
    with pytest.raises(ValueError, match="not advertised"):
        dispatch(
            ToolCall(name="not-a-real-tool", args={}),
            profile,
            kernel=None,
            bridge=None,
        )


def test_dispatch_wraps_handler_exception_into_error_envelope(index_snapshot):
    """A buggy handler that raises must NOT propagate up the agent loop —
    it should be converted into the same {"error": ...} envelope the legacy
    tools and well-behaved MCP handlers already return."""

    def boom(_args):
        raise RuntimeError("simulated handler bug")

    _install_fake("sv_list_matched_datasets", boom)

    profile = get_profile("mode4_mcp")
    result, violations = dispatch(
        ToolCall(name="sv_list_matched_datasets", args={}),
        profile,
        kernel=None,
        bridge=None,
    )
    assert violations == []
    assert "error" in result
    assert "RuntimeError" in result["error"]
    assert "simulated handler bug" in result["error"]
