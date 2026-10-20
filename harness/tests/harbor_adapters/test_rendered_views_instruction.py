from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402


def test_lists_views_and_sci3d_block_without_asset_path():
    staged = {"view_container_paths": ["/data/view_00.png", "/data/view_01.png"],
              "modality": "nifti"}
    out = gen._instruction_with_rendered_views("What organ is shown?", staged)
    assert "What organ is shown?" in out
    assert "/data/view_00.png" in out and "/data/view_01.png" in out
    block = re.search(r"```sci3d\s*\n(.*?)```", out, re.DOTALL)
    payload = json.loads(block.group(1).strip())
    assert payload["views"] == ["/data/view_00.png", "/data/view_01.png"]
    assert payload["no_asset_access"] is True
    assert "asset_path" not in payload
    # the raw-file constraint: no Asset: line, no asset path anywhere
    assert "Asset:" not in out


def test_no_views_degrades_to_bare_prose():
    out = gen._instruction_with_rendered_views("Q", {"views_missing": True, "modality": "glb"})
    assert out.strip() == "Q"
    assert "/data/" not in out
    assert "```sci3d" not in out
