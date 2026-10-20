"""Safety net: every doc surfaced in docs/_index.json must contain zero CJK Han
characters. Source files may stay in the repo; they're just excluded from the
docs SPA index.

Why: the BabyVision-v2 viewer is English-only. Chinese research notes still
live in docs/ for git history, but the SPA must not surface them.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
INDEX_PATH = DOCS_DIR / "_index.json"


def _has_han(text: str) -> bool:
    return any(0x4E00 <= ord(c) <= 0x9FFF for c in text)


def _iter_displayed_paths():
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    for section in data["tree"]:
        for entry in section["children"]:
            yield entry["path"]


def test_no_chinese_in_displayed_docs():
    offenders = []
    for rel in _iter_displayed_paths():
        # MCP tools route through .schema.json + .intro.md; skip the synthetic
        # entries that have kind: mcp-tool.
        md_path = DOCS_DIR / (rel + ".md")
        if not md_path.exists():
            continue
        if _has_han(md_path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert not offenders, (
        "These displayed docs contain Han characters; either translate them "
        "or add them to EXCLUDED_PATHS in scripts/build_docs_index.py: "
        + ", ".join(offenders)
    )
