"""Tests for scripts/build_docs_search.py — pre-built lunr search index."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.build_docs_search import build_search_payload, main, extract_snippet


def test_extract_snippet_uses_first_paragraph_after_h1():
    md = "# Title\n\nFirst paragraph with enough words to land in the snippet.\n\nSecond paragraph."
    snippet = extract_snippet(md, max_chars=80)
    assert snippet.startswith("First paragraph")
    assert len(snippet) <= 80


def test_extract_snippet_truncates_long_paragraphs():
    md = "# T\n\n" + ("word " * 200)
    snippet = extract_snippet(md, max_chars=60)
    assert len(snippet) <= 60
    assert snippet.endswith("…")


def test_extract_snippet_falls_back_to_filename_when_empty(tmp_path):
    snippet = extract_snippet("", max_chars=40)
    assert snippet == ""


def test_build_payload_indexes_every_doc(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "platform"
    docs.mkdir(parents=True)
    (docs / "quickstart.md").write_text("# Quickstart\n\nRun the 30-minute demo.\n")
    (docs / "architecture.md").write_text("# Architecture\n\nSystem components.\n")
    monkeypatch.chdir(tmp_path)
    payload = build_search_payload(tmp_path)
    assert "docs" in payload and "index_inputs" in payload
    paths = sorted(payload["docs"].keys())
    assert paths == ["platform/architecture", "platform/quickstart"]
    assert payload["docs"]["platform/quickstart"]["title"] == "Quickstart"


def test_build_payload_excludes_intro_and_generated(tmp_path, monkeypatch):
    (tmp_path / "docs" / "mcp" / "tools").mkdir(parents=True)
    (tmp_path / "docs" / "mcp" / "tools" / "x.intro.md").write_text("## When to use\nfoo\n")
    (tmp_path / "docs" / "_index.json").write_text("{}")
    (tmp_path / "docs" / "_search.json").write_text("{}")
    (tmp_path / "docs" / "platform").mkdir()
    (tmp_path / "docs" / "platform" / "real.md").write_text("# Real\nbody\n")
    monkeypatch.chdir(tmp_path)
    payload = build_search_payload(tmp_path)
    paths = list(payload["docs"].keys())
    assert paths == ["platform/real"]


def test_main_check_flag_drift(tmp_path, monkeypatch):
    (tmp_path / "docs" / "platform").mkdir(parents=True)
    (tmp_path / "docs" / "platform" / "x.md").write_text("# X\nbody\n")
    monkeypatch.chdir(tmp_path)
    # Stale committed search file.
    (tmp_path / "docs" / "_search.json").write_text('{"docs": {}, "index": ""}')
    rc = main(["--check"])
    assert rc == 1


def test_main_check_flag_in_sync(tmp_path, monkeypatch):
    (tmp_path / "docs" / "platform").mkdir(parents=True)
    (tmp_path / "docs" / "platform" / "x.md").write_text("# X\nbody\n")
    monkeypatch.chdir(tmp_path)
    main([])
    rc = main(["--check"])
    assert rc == 0


def test_search_excludes_chinese_docs():
    payload = json.loads((Path(__file__).resolve().parent.parent
                          / "docs" / "_search.json").read_text(encoding="utf-8"))
    forbidden_substrings = (".zh", "sv3d_tool_use", "WalkingSkeleton_LLM",
                            "3d_assets_inventory", "CrawlDataAndViewer",
                            "DataSourcesAndFormats",
                            "baseline_pilot100_report")
    refs = list(payload["docs"].keys())
    leaks = [r for r in refs if any(s in r for s in forbidden_substrings)]
    assert not leaks, f"Forbidden refs in search payload: {leaks}"


def test_exclusion_policy_matches_the_index_builder():
    """Search and sidebar must exclude the same docs, or the exclusion is void.

    The two builders each carry their own copy of the policy, and they drifted:
    harbor/ was kept out of the sidebar while all eleven of its docs stayed in
    the search payload, so an internal framework doc the taxonomy deliberately
    hid was still one query away in the SPA. Keeping the copies equal is the
    cheapest way to stop that happening again without merging the two scripts.
    """
    from scripts import build_docs_index, build_docs_search

    assert build_docs_search.EXCLUDED_PREFIXES == build_docs_index.EXCLUDED_PREFIXES
    assert build_docs_search.EXCLUDED_SUFFIXES == build_docs_index.EXCLUDED_SUFFIXES
    assert build_docs_search.EXCLUDED_PATHS == build_docs_index.EXCLUDED_PATHS, (
        "sorted(index - search)="
        f"{sorted(build_docs_index.EXCLUDED_PATHS - build_docs_search.EXCLUDED_PATHS)} "
        "sorted(search - index)="
        f"{sorted(build_docs_search.EXCLUDED_PATHS - build_docs_index.EXCLUDED_PATHS)}"
    )
