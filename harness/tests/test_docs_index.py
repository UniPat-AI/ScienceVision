"""Tests for scripts/build_docs_index.py — the docs tree builder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.build_docs_index import (  # noqa: E402
    EXCLUDED_PATHS,
    EXCLUDED_PREFIXES,
    _iter_markdown_files,
    _section_for_path,
    build_tree,
    extract_title,
    main,
)


def _build_tree_via_script():
    """Run build_tree against the real repo, returning the full payload."""
    return build_tree(REPO_ROOT)


def test_extract_title_from_h1():
    md = "# Quickstart\n\nSome intro paragraph."
    assert extract_title(md) == "Quickstart"


def test_extract_title_strips_leading_whitespace_and_hashes():
    md = "   #   Architecture overview   \n"
    assert extract_title(md) == "Architecture overview"


def test_extract_title_ignores_h2_and_lower():
    md = "## Not the title\n# Real title\n"
    # First H1 wins regardless of preceding lines.
    assert extract_title(md) == "Real title"


def test_extract_title_fallback_to_filename_stem(tmp_path):
    # extract_title takes text, not a path; document the fallback rule
    # by checking what build_tree does when no H1 is present.
    docs = tmp_path / "docs" / "platform"
    docs.mkdir(parents=True)
    (docs / "no-h1.md").write_text("This file has no H1.\n")
    tree = build_tree(tmp_path)
    platform = next(s for s in tree["tree"] if s["title"] == "Platform")
    entry = next(c for c in platform["children"] if c["path"] == "platform/no-h1")
    assert entry["title"] == "no-h1"


def test_build_tree_known_sections(tmp_path):
    """Top-level sections appear in the canonical order even when files
    arrive in a different filesystem order."""
    (tmp_path / "docs" / "platform").mkdir(parents=True)
    (tmp_path / "docs" / "benchmark").mkdir(parents=True)
    (tmp_path / "docs" / "partners").mkdir(parents=True)
    (tmp_path / "docs" / "platform" / "a.md").write_text("# A\n")
    (tmp_path / "docs" / "benchmark" / "b.md").write_text("# B\n")
    (tmp_path / "docs" / "partners" / "c.md").write_text("# C\n")
    tree = build_tree(tmp_path)["tree"]
    titles = [s["title"] for s in tree]
    # Platform, MCP tools, Partners, Benchmark, Research notes — canonical
    # order; empty sections (MCP tools, Research notes) are still emitted.
    assert titles == ["Platform", "MCP tools", "Partners", "Benchmark", "Research notes"]


def test_build_tree_skips_intro_and_index_partials(tmp_path):
    docs = tmp_path / "docs" / "mcp" / "tools"
    docs.mkdir(parents=True)
    (docs / "load_nifti.intro.md").write_text("## When to use\nfoo\n")
    (docs / "load_nifti.schema.json").write_text('{"name":"load_nifti","description":"d"}')
    (tmp_path / "docs" / "_index.json").write_text("{}")  # generated artifact
    (tmp_path / "docs" / "_search.json").write_text("{}")  # generated artifact
    tree = build_tree(tmp_path)["tree"]
    mcp = next(s for s in tree if s["title"] == "MCP tools")
    paths = [c["path"] for c in mcp["children"]]
    assert paths == ["mcp/tools/load_nifti"]
    assert all(c.get("kind") == "mcp-tool" for c in mcp["children"])
    # And the generated index/search files MUST NOT appear as docs.
    all_paths = [c["path"] for s in tree for c in s["children"]]
    assert "_index" not in all_paths
    assert "_search" not in all_paths


def test_build_tree_orders_children_alphabetically(tmp_path):
    docs = tmp_path / "docs" / "platform"
    docs.mkdir(parents=True)
    # Use names that are not pinned in PIN_TO_TOP so the assertion is purely
    # about alphabetical ordering of non-pinned entries.
    (docs / "architecture.md").write_text("# Architecture\n")
    (docs / "api-reference.md").write_text("# API reference\n")
    (docs / "new-domain-template.md").write_text("# New domain template\n")
    tree = build_tree(tmp_path)["tree"]
    platform = next(s for s in tree if s["title"] == "Platform")
    paths = [c["path"] for c in platform["children"]]
    assert paths == sorted(paths), f"expected alphabetical path order, got {paths}"


def test_main_writes_index_json(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "platform"
    docs.mkdir(parents=True)
    (docs / "quickstart.md").write_text("# Quickstart\n")
    monkeypatch.chdir(tmp_path)
    rc = main([])
    assert rc == 0
    out = tmp_path / "docs" / "_index.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert "tree" in data
    assert any(s["title"] == "Platform" for s in data["tree"])


def test_main_check_flag_exits_1_on_drift(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "platform"
    docs.mkdir(parents=True)
    (docs / "quickstart.md").write_text("# Quickstart\n")
    monkeypatch.chdir(tmp_path)
    # Write a stale committed index — main --check should detect drift.
    (tmp_path / "docs" / "_index.json").write_text('{"tree": []}')
    rc = main(["--check"])
    assert rc == 1


def test_main_check_flag_exits_0_when_in_sync(tmp_path, monkeypatch):
    docs = tmp_path / "docs" / "platform"
    docs.mkdir(parents=True)
    (docs / "quickstart.md").write_text("# Quickstart\n")
    monkeypatch.chdir(tmp_path)
    main([])             # write the index
    rc = main(["--check"])  # now check it's clean
    assert rc == 0


# --- Integration tests against the real repo ---

def test_section_order_is_platform_mcp_partners_benchmark_research():
    payload = _build_tree_via_script()
    titles = [s["title"] for s in payload["tree"]]
    assert titles == ["Platform", "MCP tools", "Partners", "Benchmark", "Research notes"]


def test_partners_bucket_only_contains_partner_onboarding_docs():
    payload = _build_tree_via_script()
    partners = next(s for s in payload["tree"] if s["title"] == "Partners")
    paths = sorted(c["path"] for c in partners["children"])
    assert paths == [
        "partners/README",
        "partners/examples/genesis-embodied",
        "partners/examples/medical-spatial",
    ]


def test_research_notes_bucket_holds_english_research():
    payload = _build_tree_via_script()
    research = next(s for s in payload["tree"] if s["title"] == "Research notes")
    paths = sorted(c["path"] for c in research["children"])
    assert paths == [
        "Tools",
        "evaluation/M3D-RefSeg/M3D-RefSeg_evaluation_notes",
        "evaluation/M3D-RefSeg/baseline_and_evaluation",
        "evaluation/README",
        "evaluation/SQA3D/baseline_and_evaluation",
        "qa-gen/SCALE_V2_1_BUILD",
        "qa-gen/SCALE_V2_1_CONTRACT",
        "qa-gen/medical_k_axis_seeds",
        "qa-gen/pipeline-overview",
        "qa-gen/vnext-integration-boundary",
    ]


def test_platform_quickstart_is_pinned_to_top():
    """Platform section must show Quickstart first, then alphabetical.

    Quickstart is the canonical entry point for new partners; pinning it
    overrides the default alphabetical sort that would otherwise bury it
    under api-reference / architecture / new-*. See PIN_TO_TOP in
    scripts/build_docs_index.py.
    """
    payload = _build_tree_via_script()
    platform = next(s for s in payload["tree"] if s["title"] == "Platform")
    paths = [c["path"] for c in platform["children"]]
    assert paths[0] == "platform/quickstart", \
        f"expected quickstart first; got {paths}"
    # Remaining entries stay alphabetical.
    assert paths[1:] == sorted(paths[1:]), \
        f"non-pinned entries should be alphabetical; got {paths[1:]}"


def test_excluded_chinese_docs_are_not_in_tree():
    payload = _build_tree_via_script()
    all_paths = {c["path"] for s in payload["tree"] for c in s["children"]}
    forbidden = {
        "3d_assets_inventory", "CrawlDataAndViewer", "DataSourcesAndFormats",
        "evaluation/M3D-RefSeg/baseline_and_evaluation.zh",
        "evaluation/M3D-RefSeg/baseline_pilot100_report",
        "evaluation/README.zh",
        "evaluation/SQA3D/baseline_and_evaluation.zh",
        "evaluation/sv3d_tool_use/README",
        "evaluation/sv3d_tool_use/RESULTS",
        "evaluation/sv3d_tool_use/scenarios",
        "evaluation/WalkingSkeleton_LLM/verify_report",
    }
    assert not (all_paths & forbidden), \
        f"Forbidden Chinese paths leaked into tree: {all_paths & forbidden}"


def test_superpowers_artifacts_excluded():
    payload = _build_tree_via_script()
    all_paths = {c["path"] for s in payload["tree"] for c in s["children"]}
    leak = {p for p in all_paths if p.startswith("superpowers/")}
    assert not leak, f"Superpowers artifacts leaked into tree: {leak}"


def test_nothing_reaches_the_partners_bucket_by_accident():
    """A doc filed under Partners must actually live in docs/partners/.

    _section_for_path ends in `return "partners"`, so every top-level directory
    the taxonomy does not recognise is published as partner onboarding material.
    That is how docs/harbor/ broke main: ten of its docs were listed in
    EXCLUDED_PATHS, the eleventh was not, and it surfaced under Partners. The
    failure was reported as an equality assertion on the Partners list, which
    names the symptom and not the cause.

    Asserting the implication directly means the next unclassified subtree fails
    here, and the message names the directory and what to do about it.
    """
    docs = REPO_ROOT / "docs"
    misfiled = sorted(
        rel for rel, _path in _iter_markdown_files(docs, REPO_ROOT)
        if _section_for_path(rel) == "partners" and not rel.startswith("partners/")
    )
    assert not misfiled, (
        "these docs reached the Partners bucket through _section_for_path's "
        "catch-all rather than by living in docs/partners/. Give each subtree a "
        "section in TOP_LEVEL_ORDER, a RESEARCH_PREFIXES entry, or an "
        f"EXCLUDED_PREFIXES entry: {misfiled}"
    )


def test_harbor_is_excluded_by_prefix_not_by_enumeration():
    """Harbor docs must stay out of the tree without being listed one by one.

    The per-path list is what failed: a doc added to an excluded directory is
    published by default. Pinning the prefix keeps the guarantee from depending
    on whoever adds the next file remembering to update a list.
    """
    assert "harbor/" in EXCLUDED_PREFIXES
    assert not [path for path in EXCLUDED_PATHS if path.startswith("harbor/")], (
        "harbor/ is excluded as a subtree; per-path entries are redundant and "
        "re-introduce the leak the moment one is missing"
    )
    payload = _build_tree_via_script()
    paths = []

    def walk(node):
        for child in node.get("children") or []:
            walk(child)
        if "path" in node:
            paths.append(node["path"])

    for section in payload["tree"]:
        walk(section)
    assert not [p for p in paths if p.startswith("harbor/")]
