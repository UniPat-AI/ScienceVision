import json
import os
import sys
from typing import Any, Mapping, Sequence

from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.surface import surface_payload
from sci3d_agent.mcp.surface import selected_tools
from sci3d_agent.mcp.tools import ALL_TOOLS

SERVER_NAME = "sciencevision"


def _safe_handler(spec: ToolSpec):
    handler = spec.handler

    def _wrapped(args: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return handler(args)
        except SVToolError as exc:
            return {"error": str(exc)}

    _wrapped.__name__ = f"{spec.name}_safe"
    return _wrapped


def list_tool_names(viewer_family: str | None = None) -> list[str]:
    """Names actually exposed by this server invocation.

    The optional family gate is applied before both ``--list-tools`` and the
    live FastMCP server are built.  A catalog receipt therefore cannot describe
    a narrower surface than the one the agent was offered.
    """
    return [spec.name for spec in selected_tools(viewer_family, tools=ALL_TOOLS)]


def _build_fastmcp_server(viewer_family: str | None = None):
    from fastmcp import FastMCP

    mcp = FastMCP(SERVER_NAME)
    for spec in selected_tools(viewer_family, tools=ALL_TOOLS):
        wrapped = _safe_handler(spec)
        mcp.tool(
            name=spec.name,
            description=spec.description,
        )(wrapped)
    return mcp


def _run_stdio(viewer_family: str | None = None) -> int:
    mcp = _build_fastmcp_server(viewer_family)
    mcp.run()
    return 0


def _run_http(
    port: int,
    viewer_family: str | None = None,
    *,
    host: str = "127.0.0.1",
) -> int:
    mcp = _build_fastmcp_server(viewer_family)
    # The HTTP worker is a separate compose service.  Its entrypoint supplies
    # 0.0.0.0 so the agent can reach it through Harbor's shared egress
    # namespace; the stdio/default route remains loopback-only.
    mcp.run(transport="http", host=host, port=port)
    return 0


def _parse_cli(args: Sequence[str]) -> tuple[str | None, bool, bool]:
    """Parse the intentionally small server CLI without importing argparse.

    Stdio MCP servers must reserve stdout for protocol traffic; diagnostics are
    JSON on stderr and the explicit one-shot modes are the only modes that
    print catalog data.  Keeping the parser here also makes ``--surface`` safe
    during image build, before FastMCP is imported.
    """
    viewer_family: str | None = None
    list_mode = False
    surface_mode = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--list-tools":
            list_mode = True
        elif arg == "--surface":
            surface_mode = True
        elif arg == "--viewer-family":
            index += 1
            if index >= len(args) or not str(args[index]).strip():
                raise ValueError("--viewer-family requires a non-empty value")
            viewer_family = str(args[index]).strip()
        elif arg.startswith("--viewer-family="):
            viewer_family = arg.split("=", 1)[1].strip()
            if not viewer_family:
                raise ValueError("--viewer-family requires a non-empty value")
        else:
            raise ValueError(f"unknown argument: {arg}")
        index += 1
    if list_mode and surface_mode:
        raise ValueError("--list-tools and --surface cannot be used together")
    if viewer_family is None:
        viewer_family = os.environ.get("SV_MCP_VIEWER_FAMILY", "").strip() or None
    return viewer_family, list_mode, surface_mode


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        viewer_family, list_mode, surface_mode = _parse_cli(args)
        # Resolve/gate before emitting either one-shot payload.  In particular,
        # an unknown family must not fall back to the full catalog.
        payload = surface_payload(viewer_family, tools=ALL_TOOLS)
    except (ValueError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

    if list_mode:
        print(json.dumps({"tools": payload["tool_names"]}))
        return 0
    if surface_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    http_port_env = os.environ.get("SV_HTTP_PORT", "").strip()
    if http_port_env:
        try:
            port = int(http_port_env)
        except ValueError:
            print(
                json.dumps({"error": f"invalid SV_HTTP_PORT: {http_port_env!r}"}),
                file=sys.stderr,
            )
            return 2
        if not 1 <= port <= 65535:
            print(
                json.dumps({"error": f"invalid SV_HTTP_PORT: {http_port_env!r}"}),
                file=sys.stderr,
            )
            return 2
        host = os.environ.get("SV_HTTP_HOST", "127.0.0.1").strip()
        if not host or "\x00" in host:
            print(json.dumps({"error": "invalid SV_HTTP_HOST"}), file=sys.stderr)
            return 2
        return _run_http(port, viewer_family, host=host)

    return _run_stdio(viewer_family)


if __name__ == "__main__":
    raise SystemExit(main())
