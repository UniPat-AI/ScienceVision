"""Thin httpx-based client for the SAM HTTP service.

The same client targets the local Fake server (default) or the real
H20 service (via SAM_SERVICE_URL env). All three endpoints share the
same error-handling pattern; failures are surfaced as SamClientError
so MCP-tool handlers can convert to {"error": "..."} envelopes.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

import httpx

log = logging.getLogger("sciencevision.vision3d.segment_client")


class SamClientError(RuntimeError):
    """Wraps any HTTP / network / decode failure from the SAM service."""


def _default_base_url() -> str:
    from sci3d_agent.config import SAM_SERVICE_URL_DEFAULT  # local import avoids circular dep
    return os.environ.get("SAM_SERVICE_URL", SAM_SERVICE_URL_DEFAULT)


class SamClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self._base_url = (base_url or _default_base_url()).rstrip("/")
        self._timeout = timeout

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        log.debug("POST %s payload_keys=%s", url, list(payload))
        try:
            with httpx.Client(timeout=self._timeout) as cli:
                resp = cli.post(url, json=dict(payload))
        except Exception as exc:
            raise SamClientError(f"POST {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            detail: str
            try:
                detail = resp.json().get("detail") or resp.text
            except Exception:
                detail = resp.text
            raise SamClientError(f"POST {url} -> {resp.status_code}: {detail}")
        try:
            return resp.json()
        except Exception as exc:
            raise SamClientError(f"POST {url} JSON decode failed: {exc}") from exc

    def sam2d(self, *, image_b64: str, text_prompt: str) -> dict[str, Any]:
        return self._post("/sam2d", {"image_b64": image_b64, "text_prompt": text_prompt})

    def sam3d_volume(self, *, volume_path: str, text_prompt: str) -> dict[str, Any]:
        return self._post("/sam3d_volume", {"volume_path": volume_path, "text_prompt": text_prompt})

    def sam3d_mesh(self, *, pointcloud_path: str, text_prompt: str) -> dict[str, Any]:
        return self._post("/sam3d_mesh", {"pointcloud_path": pointcloud_path, "text_prompt": text_prompt})
