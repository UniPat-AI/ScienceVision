"""Tests for sci3d_agent.mcp_bridge — exposing the MCP REGISTRY to the agent."""
from __future__ import annotations

import pytest

from sci3d_agent import mcp_bridge
from sci3d_agent.llm_client import ToolCall
from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.tools import ALL_TOOLS
from sci3d_agent.mcp_bridge import (
    dispatch_mcp,
    is_mcp_tool,
    list_mcp_tool_schemas,
)


@pytest.fixture
def index_snapshot():
    """Snapshot/restore the bridge's tool index around tests that install fakes."""
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


def test_list_mcp_tool_schemas_returns_all():
    schemas = list_mcp_tool_schemas()
    assert len(schemas) == len(ALL_TOOLS)


def test_list_mcp_tool_schemas_anthropic_shape():
    schemas = list_mcp_tool_schemas()
    for s in schemas:
        assert set(s) == {"name", "description", "input_schema"}
        assert isinstance(s["name"], str)
        assert isinstance(s["description"], str)
        assert isinstance(s["input_schema"], dict)
        assert s["input_schema"].get("type") == "object"


def test_schemas_cover_all_six_categories():
    """One entry from each category prefix should be present."""
    names = {s["name"] for s in list_mcp_tool_schemas()}
    expected_anchors = {
        "sv_list_matched_datasets",     # A
        "sv_build_viewer_url",          # B
        "sv_inspect_nifti_mask_pair",   # C
        "sv_render_qa_record_summary",  # D
        "sv_score_astronomy",           # E
        "sv3d_open_scene",              # F
    }
    assert expected_anchors <= names


def test_is_mcp_tool_known_and_unknown():
    assert is_mcp_tool("sv_list_matched_datasets")
    assert is_mcp_tool("sv3d_render")
    assert is_mcp_tool("sv_score_astronomy")  # category E (was missing pre-fix)
    assert not is_mcp_tool("execute_code")
    assert not is_mcp_tool("definitely-not-a-tool")


def test_dispatch_mcp_routes_to_registered_handler(index_snapshot):
    captured: dict = {}

    def fake(args):
        captured["args"] = dict(args)
        return {"ok": True, "echo": dict(args)}

    _install_fake("sv_list_matched_datasets", fake)

    out = dispatch_mcp(ToolCall(name="sv_list_matched_datasets", args={"x": 1}))
    assert out == {"ok": True, "echo": {"x": 1}}
    assert captured["args"] == {"x": 1}


def test_dispatch_mcp_unknown_tool_raises():
    with pytest.raises(ValueError, match="not registered"):
        dispatch_mcp(ToolCall(name="sv_definitely_not_a_tool", args={}))


def test_dispatch_mcp_passes_through_error_envelope(index_snapshot):
    _install_fake(
        "sv_get_matched_record",
        lambda args: {"error": "boom"},
    )
    out = dispatch_mcp(ToolCall(name="sv_get_matched_record", args={"qid": "x"}))
    assert out == {"error": "boom"}
