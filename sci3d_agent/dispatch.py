"""Tool dispatcher: ToolCall → kernel or viewer-bridge → render-policy → normalized result."""
from __future__ import annotations
from typing import Tuple
from sci3d_agent.llm_client import ToolCall
from sci3d_agent.mcp_bridge import dispatch_mcp, is_mcp_tool
from sci3d_agent.modes import ModeProfile
from sci3d_agent.policy import apply_render_policy
from sci3d_agent.tools.execute_code import run_execute_code
from sci3d_agent.tools.finish import run_finish
from sci3d_agent.tools.render_view import run_render_view
from sci3d_agent.tools.screenshot import run_screenshot
from sci3d_agent.tools.set_camera import run_set_camera


def _safe_dispatch_mcp(call: ToolCall) -> dict:
    """Run an MCP handler, converting any uncaught exception to an error envelope.

    Handlers are expected to use the ``{"error": "..."}`` envelope themselves,
    but a programming bug could raise. Translating here keeps the agent loop
    on the happy path (the legacy 5-tool routes already do this implicitly via
    their handler conventions; we're just matching that for MCP).
    """
    try:
        return dict(dispatch_mcp(call))
    except Exception as exc:  # noqa: BLE001 — agent loop must never propagate
        return {"error": f"{type(exc).__name__}: {exc}"}


def dispatch(call: ToolCall, profile: ModeProfile, kernel, bridge, asset_path: str = "") -> Tuple[dict, list]:
    advertised = {t["name"] for t in profile.advertised_tools}
    if call.name not in advertised:
        raise ValueError(f"tool {call.name!r} not advertised in profile {profile.name}")

    if call.name == "execute_code":
        raw = run_execute_code(call.args, kernel)
        cleaned, violations = apply_render_policy(raw, profile, code=call.args.get("code", ""))
        return cleaned, violations
    if call.name == "finish":
        return run_finish(call.args), []
    if call.name == "render_view":
        return run_render_view(call.args, bridge, asset_path), []
    if call.name == "screenshot":
        return run_screenshot(call.args, bridge), []
    if call.name == "set_camera":
        return run_set_camera(call.args, bridge), []
    if is_mcp_tool(call.name):
        # MCP tools (56 sv_* / sv3d_* tools registered in sci3d_agent.mcp).
        # They carry their own error envelope and don't interact with the
        # kernel/bridge or render-policy filter — fully self-contained. We
        # convert any handler exception into the same envelope so the agent
        # loop never sees a raw exception (matches the legacy tools' behavior).
        return _safe_dispatch_mcp(call), []
    raise ValueError(f"unhandled tool {call.name!r}")
