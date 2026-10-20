"""Build docs/_search.json — pre-built lunr-compatible search payload.

We don't import lunr from Python (it's a JS library). Instead we emit
{docs: {path: {title, snippet}}, index_inputs: [{ref, title, body}]} and let
the browser construct the lunr index at boot. The "index" key holds a JSON
array of {ref, title, body} that lunr.js will index in a single pass on page
load. (Pre-building the binary lunr index in JS at build time would require
node, which we want to avoid in CI.)

CLI:
    python scripts/build_docs_search.py
    python scripts/build_docs_search.py --check
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
EXCLUDED_SUFFIXES  = (".intro.md",)
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

# Whole subtrees that are never indexed for search. Prefix match so new internal
# artifacts stay out without having to be listed one by one. This must mirror
# EXCLUDED_PREFIXES in build_docs_index.py: a doc kept out of the sidebar but
# left searchable is still reachable from the SPA, which defeats the exclusion.
# The two lists had drifted — harbor/ was excluded from the tree while all
# eleven of its docs stayed in the search payload. tests/test_docs_search.py
# ::test_exclusion_policy_matches_the_index_builder pins them together.
# docker-image-release/ joined both lists together (release-ops docs).
EXCLUDED_PREFIXES = ("superpowers/", "harbor/", "docker-image-release/")

H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
CODE_FENCE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
INLINE_CODE = re.compile(r"`[^`]+`")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]+\)")
HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+.+$", re.MULTILINE)


def _git_tracked_md_paths(repo_root: Path) -> set[Path] | None:
    """Return absolute paths to git-tracked .md files under docs/.

    Returns None if git isn't usable, so callers fall back to a plain
    filesystem walk. See scripts/build_docs_index.py for the longer
    rationale; the short version is: without this filter, locally-present
    ignored .md files (docs/TODO.md, docs/scratch.md) end up in the
    search index, the contributor's pre-commit hook produces bytes that
    differ from CI's, and CI fails on push to main.
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


def _strip_markdown(md: str) -> str:
    text = CODE_FENCE.sub(" ", md)
    text = INLINE_CODE.sub(" ", text)
    text = LINK_PATTERN.sub(r"\1", text)
    text = HEADING_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_snippet(md: str, max_chars: int = 200) -> str:
    """Return a leading-paragraph snippet, ASCII-safe ellipsis when truncated."""
    stripped = _strip_markdown(md)
    if not stripped:
        return ""
    # Take up to the first sentence-ish boundary or paragraph break.
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 1].rstrip() + "…"


def extract_title(md: str) -> str:
    m = H1_PATTERN.search(md)
    return m.group(1).strip() if m else ""


def _iter_markdown_files(docs_dir: Path, repo_root: Path):
    tracked = _git_tracked_md_paths(repo_root)
    for p in sorted(docs_dir.rglob("*.md")):
        if any(p.name.endswith(suf) for suf in EXCLUDED_SUFFIXES):
            continue
        if tracked is not None and p not in tracked:
            continue
        rel = p.relative_to(docs_dir).with_suffix("").as_posix()
        if rel in EXCLUDED_PATHS or rel.startswith(EXCLUDED_PREFIXES):
            continue
        yield rel, p


def build_search_payload(repo_root: Path) -> dict:
    docs_dir = repo_root / DOCS_DIR_NAME
    docs_meta = {}
    index_inputs = []
    if docs_dir.is_dir():
        for rel, abspath in _iter_markdown_files(docs_dir, repo_root):
            text = abspath.read_text(encoding="utf-8")
            title = extract_title(text) or abspath.stem
            snippet = extract_snippet(text, max_chars=200)
            body = _strip_markdown(text)
            docs_meta[rel] = {"title": title, "snippet": snippet}
            index_inputs.append({"ref": rel, "title": title, "body": body})
    return {
        "generated_at": "2026-06-23T00:00:00Z",
        "docs": docs_meta,
        "index_inputs": index_inputs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if rebuilt payload differs from committed file.")
    args = parser.parse_args(argv)

    repo_root = Path.cwd()
    fresh = build_search_payload(repo_root)
    out_path = repo_root / DOCS_DIR_NAME / "_search.json"
    payload = json.dumps(fresh, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not out_path.exists():
            print(f"FAIL: {out_path} does not exist.", file=sys.stderr)
            return 1
        committed = out_path.read_text(encoding="utf-8")
        if committed != payload:
            print(f"FAIL: {out_path} is stale. Re-run `python scripts/build_docs_search.py`.",
                  file=sys.stderr)
            return 1
        print(f"OK: {out_path} is up-to-date.")
        return 0

    out_path.write_text(payload, encoding="utf-8")
    print(f"wrote {out_path} ({len(fresh['docs'])} docs indexed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
