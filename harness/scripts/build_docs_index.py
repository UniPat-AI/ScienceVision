"""Build docs/_index.json — the left-sidebar tree consumed by docs.html.

Walks docs/**/*.md, groups by top-level section, emits a stable JSON tree.

CLI:
    python scripts/build_docs_index.py             # write docs/_index.json
    python scripts/build_docs_index.py --check     # exit 1 if regenerated content would differ
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR_NAME = "docs"

# Canonical top-level ordering for the sidebar. New top-level sections are
# added here only.
TOP_LEVEL_ORDER = ["platform", "mcp", "partners", "benchmark", "research"]
SECTION_LABEL = {
    "platform":  "Platform",
    "mcp":       "MCP tools",
    "partners":  "Partners",
    "benchmark": "Benchmark",
    "research":  "Research notes",
}

# Files that look like docs but are partials or generated; exclude from the
# tree.
EXCLUDED_SUFFIXES  = (".intro.md",)  # MCP tool intros are partials, not standalone docs

# Explicit per-path exclusion. Paths are relative to docs/ with no .md suffix.
# Two reasons a doc lands here:
#   (a) Chinese content (Han characters) — viewer SPA is English-only.
#   (b) Internal SDD planning artifacts — not project documentation.
# Source files stay in the repo for git history; they're just excluded from
# the SPA index.
EXCLUDED_PATHS = frozenset({
    # Chinese research notes
    "3d_assets_inventory",
    "CrawlDataAndViewer",
    "DataSourcesAndFormats",
    "evaluation/2026-05-21-sv3d-tool-use-test-plan",
    "evaluation/M3D-RefSeg/baseline_and_evaluation.zh",
    "evaluation/M3D-RefSeg/baseline_pilot100_report",
    "evaluation/README.zh",
    "evaluation/SQA3D/baseline_and_evaluation.zh",
    "evaluation/WalkingSkeleton_LLM/verify_report",
    "evaluation/sv3d_tool_use/README",
    "evaluation/sv3d_tool_use/RESULTS",
    "evaluation/sv3d_tool_use/scenarios",
    "relatedwork/visual-interactive",
    "relatedwork/visual-multimodal-reasoning",
    "relatedwork/visual-scientific",
    "relatedwork/visual-vla",
    # Internal backlog tracker — see docs/issue-audit.md.
    "issue-audit",
})

# Whole subtrees that are never published. Prefix match (not per-path) so a new
# internal artifact can't leak into the tree just because nobody remembered to
# list it — tests/test_docs_index.py::test_superpowers_artifacts_excluded and
# ::test_every_docs_subtree_has_a_declared_home assert this invariant.
#
# harbor/ is here rather than enumerated per-path for exactly that reason: it
# used to be ten hand-listed entries, and the eleventh doc (added by #87) fell
# through _section_for_path's catch-all into Partners and broke the taxonomy
# test on main for three days. Harbor holds eval-baseline framework material —
# dev docs, not partner onboarding — kept in-repo but out of the SPA.
#
# docker-image-release/ is the same case one directory over: the 2026-09-23
# release record (SOP, image layout, asset visibility, scorer release) is
# operator material, and the five docs fell through the same catch-all into
# Partners the moment they were committed.
EXCLUDED_PREFIXES = ("superpowers/", "harbor/", "docker-image-release/")

# Buckets for the new "research" section (docs that are real notes, not
# partner onboarding material).
RESEARCH_PREFIXES = ("evaluation/", "qa-gen/")
RESEARCH_PATHS    = frozenset({"Tools"})

# Per-section pinned entries that override alphabetical sort. Each value is
# an ordered tuple of paths (relative to docs/, no suffix); listed paths
# float to the top of their section in the given order, then any remaining
# entries follow alphabetically. This lets us surface the canonical entry
# point (e.g. Platform → Quickstart) without renaming files for sort order.
PIN_TO_TOP: dict[str, tuple[str, ...]] = {
    "platform": ("platform/quickstart",),
}

H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def _git_tracked_md_paths(repo_root: Path) -> set[Path] | None:
    """Return absolute paths to git-tracked .md files under docs/.

    Returns None if git isn't usable (no repo, missing binary, timeout). In
    that case callers fall back to a plain filesystem walk so the script
    still works outside a git checkout.

    Filtering against this set prevents locally-present-but-ignored .md
    files (e.g. docs/TODO.md, docs/scratch.md) from polluting the index.
    Without this filter, a contributor's pre-commit hook produces bytes
    that differ from CI's (which only sees tracked files), and CI fails on
    push to main even though both ran the same script.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--", "docs"],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return {
        repo_root / line
        for line in result.stdout.splitlines()
        if line.endswith(".md")
    }


def extract_title(md_text: str) -> str:
    """Return the first H1 text from a markdown document.

    Strips surrounding whitespace from both the heading line and the title.
    Returns an empty string if no H1 is found.
    """
    m = H1_PATTERN.search(md_text)
    return m.group(1).strip() if m else ""


def _iter_markdown_files(docs_dir: Path, repo_root: Path):
    """Yield (relative_path_no_suffix, absolute_path) for every doc-like .md
    file under docs_dir, skipping partials, generated artifacts, files not
    tracked by git, and explicit EXCLUDED_PATHS."""
    tracked = _git_tracked_md_paths(repo_root)
    for p in sorted(docs_dir.rglob("*.md")):
        if any(p.name.endswith(suf) for suf in EXCLUDED_SUFFIXES):
            continue
        if tracked is not None and p not in tracked:
            continue
        rel = p.relative_to(docs_dir).with_suffix("")
        rel_str = rel.as_posix()
        if rel_str in EXCLUDED_PATHS or rel_str.startswith(EXCLUDED_PREFIXES):
            continue
        yield rel_str, p


def _iter_mcp_tools(docs_dir: Path):
    """Yield (name, schema_path) for every MCP tool *.schema.json file."""
    mcp_tools_dir = docs_dir / "mcp" / "tools"
    if not mcp_tools_dir.is_dir():
        return
    for p in sorted(mcp_tools_dir.glob("*.schema.json")):
        name = p.name[:-len(".schema.json")]
        yield name, p


def _section_for_path(rel_path: str) -> str:
    """Return the section key for a doc path.

    Paths under docs/mcp/tools/ are skipped here (handled by _iter_mcp_tools).
    Top-level dirs platform/, benchmark/, mcp/, partners/ map to themselves.
    evaluation/* and qa-gen/* (plus the standalone Tools.md) go to research.
    Anything else falls through to partners (catch-all, but EXCLUDED_PATHS
    filters out anything we don't want surfaced).
    """
    head = rel_path.split("/", 1)[0]
    if head == "mcp":
        return "mcp"
    if head in ("platform", "benchmark", "partners"):
        return head
    if rel_path.startswith(RESEARCH_PREFIXES) or rel_path in RESEARCH_PATHS:
        return "research"
    return "partners"


def build_tree(repo_root: Path) -> dict:
    """Produce the {generated_at, tree: [...]} structure.

    Top-level section order follows TOP_LEVEL_ORDER. Empty sections are still
    emitted (so the UI keeps the heading visible even before content lands).
    Doc entries within each section are alphabetically ordered by path.
    """
    docs_dir = repo_root / DOCS_DIR_NAME
    sections: dict[str, list[dict]] = {key: [] for key in TOP_LEVEL_ORDER}

    if docs_dir.is_dir():
        for rel, abspath in _iter_markdown_files(docs_dir, repo_root):
            # Skip MCP tool *.intro.md (already excluded by suffix), and the
            # generated _index/_search files.
            if rel.startswith("mcp/tools/"):
                continue
            section = _section_for_path(rel)
            title = extract_title(abspath.read_text(encoding="utf-8")) or abspath.stem
            sections[section].append({"title": title, "path": rel})

        for name, schema_path in _iter_mcp_tools(docs_dir):
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            entry = {
                "title": name,
                "path":  f"mcp/tools/{name}",
                "kind":  "mcp-tool",
            }
            if "domain" in schema:
                entry["domain"] = schema["domain"]
            sections["mcp"].append(entry)

    # Per-section ordering: pinned paths first (in declared order), then
    # remaining entries alphabetically by path. Pinned-but-absent paths are
    # silently ignored so partial config doesn't break the build.
    for key, entries in sections.items():
        pinned = PIN_TO_TOP.get(key, ())
        pinned_set = set(pinned)
        pinned_entries = [e for path in pinned for e in entries if e["path"] == path]
        rest = sorted(
            (e for e in entries if e["path"] not in pinned_set),
            key=lambda e: e["path"],
        )
        sections[key] = pinned_entries + rest

    tree = [
        {"title": SECTION_LABEL[key], "children": sections[key]}
        for key in TOP_LEVEL_ORDER
    ]
    return {
        "generated_at": "2026-06-23T00:00:00Z",  # bumped for batch 1 reorg
        "tree": tree,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if rebuilt index differs from committed file.")
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    fresh = build_tree(repo_root)
    out_path = repo_root / DOCS_DIR_NAME / "_index.json"

    payload = json.dumps(fresh, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not out_path.exists():
            print(f"FAIL: {out_path} does not exist; run without --check first.", file=sys.stderr)
            return 1
        committed = out_path.read_text(encoding="utf-8")
        if committed != payload:
            print(f"FAIL: {out_path} is stale. Re-run `python scripts/build_docs_index.py`.",
                  file=sys.stderr)
            return 1
        print(f"OK: {out_path} is up-to-date.")
        return 0

    out_path.write_text(payload, encoding="utf-8")
    print(f"wrote {out_path} ({len(fresh['tree'])} sections, "
          f"{sum(len(s['children']) for s in fresh['tree'])} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
