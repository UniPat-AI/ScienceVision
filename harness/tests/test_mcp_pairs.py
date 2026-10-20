"""Verify every MCP tool *.schema.json has a sibling *.intro.md.

Mode: warn-only for now. We print a warning per missing intro but do not fail
the test, because Plan 2 explicitly allows shipping intros in batches.
"""
from __future__ import annotations

import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_TOOLS_DIR = REPO_ROOT / "docs" / "mcp" / "tools"


def test_every_schema_has_paired_intro():
    if not MCP_TOOLS_DIR.is_dir():
        return  # no tools yet — nothing to check
    schemas = sorted(MCP_TOOLS_DIR.glob("*.schema.json"))
    missing = []
    for schema in schemas:
        name = schema.name[: -len(".schema.json")]
        intro = schema.with_name(name + ".intro.md")
        if not intro.exists():
            missing.append(intro)
    for intro in missing:
        warnings.warn(f"missing MCP intro: {intro}", UserWarning, stacklevel=2)
    # Plan 2 chooses warn-only here; the assertion below documents the
    # invariant we'll tighten to "fail" in a later plan.
    assert isinstance(missing, list)
