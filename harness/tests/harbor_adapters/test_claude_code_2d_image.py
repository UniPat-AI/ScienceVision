from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402


def test_dockerfile_2d_has_claude_no_3d_libs_no_mcp():
    df = (gen._TEMPLATES_DIR / "Dockerfile.claude-code-2d").read_text()
    assert "FROM node:20-slim" in df
    assert "@anthropic-ai/claude-code" in df
    # constraint: NO 3D inspection libs, NO MCP server baked in
    for banned in ("nibabel", "trimesh", "sciencevision-mcp", "pip3 install"):
        assert banned not in df, f"{banned} must not be in the 2D image"


def test_build_script_tags_2d_image():
    sh = (gen._TEMPLATES_DIR / "build_claude_code_2d_image.sh").read_text()
    assert "sci3d-claude-code-2d:latest" in sh
    assert "Dockerfile.claude-code-2d" in sh


def test_qa_gen_to_tasks_accepts_2d_env():
    out = subprocess.run(
        [sys.executable, "-m", "scripts.harbor.qa_gen_to_tasks", "--help"],
        cwd=_ROOT, capture_output=True, text=True,
    )
    assert "claude-code-2d" in out.stdout
