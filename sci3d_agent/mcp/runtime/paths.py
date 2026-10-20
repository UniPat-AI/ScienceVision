import os
from pathlib import Path

# Walk up from this file (sci3d_agent/mcp/runtime/paths.py) to the repo root.
# This means the package detects whichever checkout it's loaded from — main
# checkout or any worktree under .claude/worktrees/ — instead of always
# pointing at one hard-coded path. SV_REPO_ROOT still overrides for tests
# and one-off scripts.
_AUTODETECTED_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_VIEWER_BASE = "http://10.0.0.144"
_DEFAULT_RENDER_CACHE = "/tmp/sv_renders"


def repo_root() -> Path:
    override = os.environ.get("SV_REPO_ROOT")
    return Path(override) if override else _AUTODETECTED_REPO_ROOT


def viewer_base() -> str:
    return os.environ.get("SV_VIEWER_BASE", _DEFAULT_VIEWER_BASE).rstrip("/")


def render_cache_dir() -> Path:
    return Path(os.environ.get("SV_RENDER_CACHE", _DEFAULT_RENDER_CACHE))
