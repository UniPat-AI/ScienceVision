# tests/mcp/test_vision3d/test_segment_e2e.py
"""End-to-end: MCP tool -> real httpx -> in-process Fake SAM FastAPI app.

Verifies the API contract between the MCP-side client and the fake
server is stable. No mocks on the HTTP boundary.
"""
from __future__ import annotations

import base64
import io
import threading
import time

import httpx
import numpy as np
import pytest
import uvicorn
from PIL import Image

from scripts.sam_service.fake_server import build_app
from sci3d_agent.mcp.tools.vision3d._segment_client import SamClient


def _wait_for_ready(server: uvicorn.Server, *, timeout: float) -> str:
    """Poll the in-process uvicorn server until its /healthz answers 200.

    Raises RuntimeError with diagnostic info on timeout. Returns the
    bound base URL on success.
    """
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if server.started and server.servers:
            sock = next(iter(server.servers)).sockets[0]
            url = f"http://127.0.0.1:{sock.getsockname()[1]}"
            try:
                if httpx.get(f"{url}/healthz", timeout=2.0).status_code == 200:
                    return url
            except httpx.HTTPError as exc:
                last_err = exc
        time.sleep(0.1)
    raise RuntimeError(
        f"Fake SAM server never became ready in {timeout}s "
        f"(started={server.started}, last_probe_err={last_err!r})"
    )


@pytest.fixture(scope="module")
def fake_server_url():
    """Spin up a real uvicorn server in a thread bound to an ephemeral port."""
    app = build_app(quality="perfect")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        url = _wait_for_ready(server, timeout=10.0)
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_e2e_sam2d_roundtrip(fake_server_url):
    img = np.zeros((64, 64), dtype=np.uint8)
    img[10:30, 10:30] = 200
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    client = SamClient(base_url=fake_server_url)
    out = client.sam2d(image_b64=image_b64, text_prompt="the bright square")
    assert "masks" in out
    assert out["masks"][0]["area"] > 0
    assert 0.0 <= out["masks"][0]["score"] <= 1.0
