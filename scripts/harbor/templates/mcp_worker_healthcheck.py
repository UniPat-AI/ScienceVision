#!/usr/bin/env python3
"""Docker healthcheck for the private ScienceVision MCP worker."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def _ready_receipt() -> bool:
    path = Path(os.environ.get("SV_MCP_HEALTH_RECEIPT_PATH", "/run/sciencevision-mcp/health.json"))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ready"


def _listener_is_open() -> bool:
    try:
        port = int(os.environ.get("SV_HTTP_PORT", "8765"))
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if _ready_receipt() and _listener_is_open() else 1)
