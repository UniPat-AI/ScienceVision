"""Shared local server fixture for viewer browser tests."""

from __future__ import annotations

import http.server
import socket
import socketserver
import threading
from functools import partial
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass


@pytest.fixture(scope="session")
def viewer_server():
    port = _free_port()
    handler = partial(_Handler, directory=str(REPO_ROOT))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
