#!/usr/bin/env python3
"""Start the private ScienceVision HTTP MCP worker and write a health receipt.

Only standard-library dependencies are used so Docker can distinguish a live
TCP listener from a worker that failed while importing its tool registry. The
receipt remains in the worker filesystem, never in the agent's /logs mount.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = "sciencevision.mcp_worker_health.v1"
_DEFAULT_PORT = 8765
_DEFAULT_RECEIPT = "/run/sciencevision-mcp/health.json"


def _port_from_env() -> int:
    raw = os.environ.get("SV_HTTP_PORT", str(_DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"SV_HTTP_PORT must be an integer, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"SV_HTTP_PORT must be in 1..65535, got {raw!r}")
    return port


def _receipt_path_from_env() -> Path:
    value = os.environ.get("SV_MCP_HEALTH_RECEIPT_PATH", _DEFAULT_RECEIPT).strip()
    path = Path(value)
    if not value or not path.is_absolute() or "\x00" in value:
        raise ValueError("SV_MCP_HEALTH_RECEIPT_PATH must be a non-empty absolute path")
    return path


def _surface(port: int) -> str:
    fallback = f"http://127.0.0.1:{port}/mcp"
    return os.environ.get("SV_MCP_HTTP_SURFACE", fallback).strip() or fallback


def _payload(*, status: str, port: int, detail: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "status": status,
        "pid": os.getpid(),
        "port": port,
        "http_surface": _surface(port),
        "viewer_family": os.environ.get("SV_MCP_VIEWER_FAMILY", "").strip(),
        "catalog_sha256": os.environ.get("SV_MCP_CATALOG_SHA256", "").strip(),
        "schema_sha256": os.environ.get("SV_MCP_SCHEMA_SHA256", "").strip(),
    }
    if detail:
        payload["detail"] = detail
    return payload


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def main() -> int:
    try:
        port = _port_from_env()
        receipt_path = _receipt_path_from_env()
    except ValueError as exc:
        print(f"mcp worker configuration error: {exc}", file=sys.stderr, flush=True)
        return 2

    env = os.environ.copy()
    env.setdefault("SV_HTTP_PORT", str(port))
    env.setdefault("SV_HTTP_HOST", "0.0.0.0")
    _write_receipt(receipt_path, _payload(status="starting", port=port))
    worker = subprocess.Popen([sys.executable, "-m", "sci3d_agent.mcp.server"], env=env)

    def forward_signal(signum: int, _frame: object) -> None:
        if worker.poll() is None:
            worker.send_signal(signum)

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        code = worker.poll()
        if code is not None:
            _write_receipt(
                receipt_path,
                _payload(status="failed", port=port, detail=f"server exited with code {code}"),
            )
            return code or 1
        if _port_is_open(port):
            _write_receipt(receipt_path, _payload(status="ready", port=port))
            return worker.wait()
        time.sleep(0.1)

    worker.terminate()
    try:
        code = worker.wait(timeout=5)
    except subprocess.TimeoutExpired:
        worker.kill()
        code = worker.wait()
    _write_receipt(
        receipt_path,
        _payload(status="failed", port=port, detail="server did not open its HTTP port in 45 seconds"),
    )
    return code or 1


if __name__ == "__main__":
    raise SystemExit(main())
