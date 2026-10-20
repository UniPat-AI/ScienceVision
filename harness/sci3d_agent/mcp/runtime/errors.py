class SVToolError(Exception):
    """Tool-level error; should be returned as {error: str} envelope, not raised across MCP boundary."""
