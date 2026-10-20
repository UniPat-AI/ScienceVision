"""render_cache_dir honors $SV_RENDER_CACHE (used by the ccmcp harbor env)."""
from __future__ import annotations


def test_render_cache_dir_honors_env(monkeypatch):
    from sci3d_agent.mcp.runtime import paths
    monkeypatch.setenv("SV_RENDER_CACHE", "/app/renders")
    assert str(paths.render_cache_dir()) == "/app/renders"
    monkeypatch.delenv("SV_RENDER_CACHE", raising=False)
    assert str(paths.render_cache_dir()) == "/tmp/sv_renders"
