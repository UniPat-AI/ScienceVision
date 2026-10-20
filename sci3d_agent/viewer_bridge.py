"""Viewer-bridge protocol + stub. Real Playwright bridge wired in once
tools/sci3d_mcp/ lands (see 2026-05-14-vqa-3d-mcp-design.md)."""
from __future__ import annotations
from typing import Protocol


class ViewerBridgeUnavailable(RuntimeError):
    pass


class ViewerBridgeProtocol(Protocol):
    def health_check(self) -> bool: ...
    def render_view(self, asset_path: str, **camera) -> dict: ...
    def screenshot(self) -> dict: ...
    def set_camera(self, **camera) -> dict: ...


class StubViewerBridge:
    """Returns 'viewer_bridge_unavailable' for every call. Lets mode-3 work via
    in-kernel matplotlib until the real bridge is implemented."""

    def health_check(self) -> bool:
        return False

    def render_view(self, asset_path: str, **camera) -> dict:
        return {"error": "viewer_bridge_unavailable", "asset_path": asset_path, "camera": camera}

    def screenshot(self) -> dict:
        return {"error": "viewer_bridge_unavailable"}

    def set_camera(self, **camera) -> dict:
        return {"error": "viewer_bridge_unavailable", "camera": camera}
