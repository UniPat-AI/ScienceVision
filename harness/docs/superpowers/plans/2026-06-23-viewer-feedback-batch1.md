# Viewer Feedback Batch 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Controller note**: per the user's saved memory `feedback_sdd_review_overhead`, skip the per-task reviewer dispatch; rely on the final whole-branch review only.

**Goal:** Resolve all Must-fix items in `docs/viewer-feedback.md` (Batch 1): docs SPA reorg + Chinese exclusion + Partners README rewrite + real demo data wiring + task-family display rename + homepage section reorder + hero copy refresh.

**Architecture:** Pure-frontend + build-script + data-file work. Zero new runtime dependencies. Static viewer pattern preserved (no JS bundler). Build scripts are extended with explicit `EXCLUDED_PATHS` + new `research` bucket; new `data/assets.index.json` + `data/qagen_index.json` files added at the paths `viewer/manifest.json` already points to.

**Tech Stack:** Python 3.11+ (build scripts + pytest), vanilla JS + HTML5 + CSS (viewer), JSON Schema 2020-12 (manifest validation), git for tracking.

## Global Constraints

- **Branch:** `fix/viewer-feedback-batch1`, branched from `main` (currently at `3987b70`).
- **No new runtime JS dependencies.** All viewer JS remains vanilla; no new `<script src>` for npm-style libraries.
- **No Chinese in displayed docs.** Every file surfaced in `docs/_index.json` / `docs/_search.json` must contain zero CJK Han characters (`U+4E00–U+9FFF`). Source files keep Chinese on disk; they're just excluded from the index.
- **Internal IDs unchanged.** `genesis_vp` and `medical_spatial` family IDs stay as-is in `data-family` attributes, manifest schemas, and any upstream QA-gen references. Only display labels + docs anchors change.
- **Stable timestamps in build artifacts.** `_index.json`, `_search.json`, and new data index files use the fixed `generated_at` value `"2026-06-23T00:00:00Z"` so the `--check` flag stays deterministic.
- **Existing tests must keep passing.** All 36 pre-existing pytest tests on main pass; new tests are additive. Run `pytest tests/ -v` after each commit.
- **Commit-message style:** Conventional commits (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`). One commit per task.
- **No homepage CSS edits.** Existing `viewer/shared/style.css` rules cover all reorder + new rendering needs.

---

## File Structure

**New files:**
- `data/assets.index.json` — flat catalog of 6 demo 3D assets (4 GLB from `viewer/datasets/genesis/`, 2 NIFTI from `examples/02_medical_spatial_gt/data/`)
- `data/qagen_index.json` — 4 demo QA items (2 per shipped family)
- `tests/test_data_indexes.py` — schema + count-consistency tests for the two new index files
- `tests/test_docs_no_chinese.py` — safety net asserting every doc surfaced in `_index.json` contains zero Han characters

**Modified files:**
- `scripts/build_docs_index.py` — add `research` bucket; new `TOP_LEVEL_ORDER`; `EXCLUDED_PATHS` set; route evaluation/qa-gen/Tools to `research`; route partners/* to `partners`; skip `superpowers/**`
- `scripts/build_docs_search.py` — mirror the `EXCLUDED_PATHS` filter (skip same files)
- `docs/_index.json` — regenerated artifact
- `docs/_search.json` — regenerated artifact
- `docs/partners/README.md` — rewrite as Partner Onboarding Guide
- `viewer/manifest.json` — update `counts.assets` to `6`, `counts.qagen` to `4`
- `viewer/index.html` — reorder sections (Hero → Motivation → Launcher → Tasks → Domain → Compare → Get started); refresh hero copy; rename last 2 task cards
- `viewer/qagen.html` — rename last 2 cards; render shipped-family QA list under static cards
- `viewer/assets.html` — render shipped-domain asset list under domain grid
- `docs/benchmark/task-families.md` — H2 rename (Genesis_VP → Interactive Physics; Medical Spatial → Spatial Vision)
- `tests/test_docs_index.py` — update fixtures for new section list + counts
- `tests/test_docs_search.py` — update fixtures for new exclusion list
- `tests/test_manifest_schema.py` — relax (or update) the counts==0 assertion if present

---

## Task Order Rationale

1. **Task 1 (build script)** unlocks the new docs tree shape so Task 2's Partners rewrite shows up in the right bucket.
2. **Task 2 (Partners README)** is content-only, depends on Task 1's tree shape.
3. **Tasks 3 + 4 (data index files)** create the JSON payloads that Task 5 needs.
4. **Task 5 (manifest counts + page rendering integration)** wires the data into the UI.
5. **Task 6 (family rename)** is independent display-only — could run anywhere, placed late to avoid colliding with Task 7's section-edit churn.
6. **Task 7 (homepage reorder + hero copy)** is the most visible change, done last so subagent reviewing the diff has all neighboring context.

---

## Task 1: Docs build script — new section order, research bucket, Chinese exclusion, superpowers exclusion

**Files:**
- Modify: `scripts/build_docs_index.py`
- Modify: `scripts/build_docs_search.py`
- Modify: `docs/_index.json` (regenerated)
- Modify: `docs/_search.json` (regenerated)
- Create: `tests/test_docs_no_chinese.py`
- Modify: `tests/test_docs_index.py` (update expected section order + entry list)
- Modify: `tests/test_docs_search.py` (update expected entry list)

**Interfaces:**
- Consumes: nothing from earlier tasks (Task 1 is the first task)
- Produces:
  - `docs/_index.json.tree[*].title` will be exactly `["Platform", "MCP tools", "Partners", "Benchmark", "Research notes"]` in that order
  - `docs/_index.json` will exclude all 15 paths in `EXCLUDED_PATHS` (see Step 3 below for the exact set)
  - Partners bucket will contain exactly: `partners/README`, `partners/examples/genesis-embodied`, `partners/examples/medical-spatial`
  - Research notes bucket will contain exactly: `Tools`, `evaluation/M3D-RefSeg/M3D-RefSeg_evaluation_notes`, `evaluation/M3D-RefSeg/baseline_and_evaluation`, `evaluation/README`, `evaluation/SQA3D/baseline_and_evaluation`, `qa-gen/medical_k_axis_seeds`, `qa-gen/pipeline-overview`

- [ ] **Step 1: Open a branch from main**

```bash
git checkout main
git pull origin main
git checkout -b fix/viewer-feedback-batch1
```

Expected: `Switched to a new branch 'fix/viewer-feedback-batch1'`.

- [ ] **Step 2: Write the failing safety-net test (no Chinese in displayed docs)**

Create `tests/test_docs_no_chinese.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_docs_no_chinese.py -v
```

Expected: FAIL — lists 13 offending paths (the 13 Chinese files currently in `_index.json`: `3d_assets_inventory`, `CrawlDataAndViewer`, `DataSourcesAndFormats`, `evaluation/2026-05-21-sv3d-tool-use-test-plan`, `evaluation/M3D-RefSeg/baseline_and_evaluation.zh`, `evaluation/M3D-RefSeg/baseline_pilot100_report`, `evaluation/README.zh`, `evaluation/SQA3D/baseline_and_evaluation.zh`, `evaluation/WalkingSkeleton_LLM/verify_report`, `evaluation/sv3d_tool_use/README`, `evaluation/sv3d_tool_use/RESULTS`, `evaluation/sv3d_tool_use/scenarios`, `superpowers/specs/2026-06-22-asset-picker-sop-design`).

- [ ] **Step 4: Update `scripts/build_docs_index.py` with new ordering, research bucket, and EXCLUDED_PATHS**

Replace the top constants and `_section_for_path()` function. The full target file is below (replace existing top of file from line 21 through end of `_section_for_path`):

```python
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
    # Internal SDD artifacts
    "superpowers/plans/2026-06-19-bv2-viewer-plan1-manifest-layer",
    "superpowers/plans/2026-06-19-bv2-viewer-plan2-docs-spa",
    "superpowers/plans/2026-06-23-bv2-viewer-plan3-homepage-and-domains",
    "superpowers/plans/2026-06-23-viewer-feedback-batch1",
    "superpowers/specs/2026-06-19-bv2-viewer-platform-design",
    "superpowers/specs/2026-06-22-asset-picker-sop-design",
})

# Buckets for the new "research" section (docs that are real notes, not
# partner onboarding material).
RESEARCH_PREFIXES = ("evaluation/", "qa-gen/")
RESEARCH_PATHS    = frozenset({"Tools"})

H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
```

And replace `_section_for_path()`:

```python
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
```

And in `_iter_markdown_files()` add the EXCLUDED_PATHS skip — modify the for-loop:

```python
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
        if rel_str in EXCLUDED_PATHS:
            continue
        yield rel_str, p
```

And update the `generated_at` stamp at the bottom of `build_tree()`:

```python
    return {
        "generated_at": "2026-06-23T00:00:00Z",  # bumped for batch 1 reorg
        "tree": tree,
    }
```

- [ ] **Step 5: Update `scripts/build_docs_search.py` to mirror EXCLUDED_PATHS + stamp**

Add the same constants (the explicit set, copy-pasted — DRY here would require a shared module which is over-engineering for two scripts).

After the existing `EXCLUDED_SUFFIXES = (".intro.md",)` line, append:

```python
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
    # Internal SDD artifacts
    "superpowers/plans/2026-06-19-bv2-viewer-plan1-manifest-layer",
    "superpowers/plans/2026-06-19-bv2-viewer-plan2-docs-spa",
    "superpowers/plans/2026-06-23-bv2-viewer-plan3-homepage-and-domains",
    "superpowers/plans/2026-06-23-viewer-feedback-batch1",
    "superpowers/specs/2026-06-19-bv2-viewer-platform-design",
    "superpowers/specs/2026-06-22-asset-picker-sop-design",
})
```

And modify `_iter_markdown_files()` to add the skip (same shape as Task 1 Step 4 change):

```python
def _iter_markdown_files(docs_dir: Path, repo_root: Path):
    tracked = _git_tracked_md_paths(repo_root)
    for p in sorted(docs_dir.rglob("*.md")):
        if any(p.name.endswith(suf) for suf in EXCLUDED_SUFFIXES):
            continue
        if tracked is not None and p not in tracked:
            continue
        rel = p.relative_to(docs_dir).with_suffix("").as_posix()
        if rel in EXCLUDED_PATHS:
            continue
        yield rel, p
```

And bump the timestamp:

```python
    return {
        "generated_at": "2026-06-23T00:00:00Z",
        "docs": docs_meta,
        "index_inputs": index_inputs,
    }
```

- [ ] **Step 6: Regenerate both artifacts**

```bash
python scripts/build_docs_index.py
python scripts/build_docs_search.py
```

Expected output line 1: `wrote .../docs/_index.json (5 sections, ~18 entries)` (4-6 Partners, 5-6 MCP, 5 Platform, 4 Benchmark, ~7 Research).
Expected output line 2: `wrote .../docs/_search.json (~17 docs indexed)`.

- [ ] **Step 7: Update `tests/test_docs_index.py` to reflect new shape**

Read the current test file first to find the fixture, then replace the section-order + entry-count assertions. Key updates to make:

- Section titles list now: `["Platform", "MCP tools", "Partners", "Benchmark", "Research notes"]`
- Partners bucket cardinality is exactly 3 (`partners/README`, `partners/examples/genesis-embodied`, `partners/examples/medical-spatial`)
- New `research` section with the 7 entries listed in the "Interfaces / Produces" block above
- No entries containing `superpowers/`, `3d_assets_inventory`, `CrawlDataAndViewer`, `DataSourcesAndFormats`, `.zh` suffix, `sv3d_tool_use`, or `WalkingSkeleton_LLM`

Add or replace a test like:

```python
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
        "qa-gen/medical_k_axis_seeds",
        "qa-gen/pipeline-overview",
    ]


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
```

Remove or update any older tests that assert the old `TOP_LEVEL_ORDER = ["benchmark", "platform", "mcp", "partners"]` or expect specific Chinese paths to exist. Read the existing test file to find these — common patterns to update: `assert titles[0] == "Benchmark"`, `assert "3d_assets_inventory" in paths`.

If a helper `_build_tree_via_script()` doesn't yet exist, add at the top of the test file:

```python
import subprocess, json, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def _build_tree_via_script():
    # Import the build function rather than re-running it via subprocess.
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import build_docs_index
    return build_docs_index.build_tree(REPO_ROOT)
```

- [ ] **Step 8: Update `tests/test_docs_search.py` to mirror exclusions**

Add a guard:

```python
def test_search_excludes_chinese_docs():
    import json
    from pathlib import Path
    payload = json.loads((Path(__file__).resolve().parent.parent
                          / "docs" / "_search.json").read_text(encoding="utf-8"))
    forbidden_substrings = (".zh", "sv3d_tool_use", "WalkingSkeleton_LLM",
                            "3d_assets_inventory", "CrawlDataAndViewer",
                            "DataSourcesAndFormats",
                            "baseline_pilot100_report")
    refs = list(payload["docs"].keys())
    leaks = [r for r in refs if any(s in r for s in forbidden_substrings)]
    assert not leaks, f"Forbidden refs in search payload: {leaks}"
```

Remove or update any prior test that expected Chinese paths to be present.

- [ ] **Step 9: Run all tests**

```bash
pytest tests/ -v
```

Expected: all tests pass including the new ones. Pre-existing tests for manifest schema, mcp pairs, domain taxonomy stay green.

- [ ] **Step 10: Verify `--check` mode still works**

```bash
python scripts/build_docs_index.py --check
python scripts/build_docs_search.py --check
```

Expected: both print `OK: ... is up-to-date.`

- [ ] **Step 11: Commit**

```bash
git add scripts/build_docs_index.py scripts/build_docs_search.py \
        docs/_index.json docs/_search.json \
        tests/test_docs_index.py tests/test_docs_search.py \
        tests/test_docs_no_chinese.py
git commit -m "refactor(docs): new section order + research bucket + Chinese/superpowers exclusion

- TOP_LEVEL_ORDER: Platform → MCP tools → Partners → Benchmark → Research notes
- EXCLUDED_PATHS set covers 13 Chinese files + 6 internal SDD artifacts
- New 'Research notes' bucket gathers English evaluation/qa-gen/Tools docs
- Partners bucket reduced to 3 entries: README + 2 examples
- Safety-net test (test_docs_no_chinese.py) locks the no-Chinese promise"
```

---

## Task 2: Rewrite `docs/partners/README.md` as Partner Onboarding Guide

**Files:**
- Modify: `docs/partners/README.md`
- Modify: `docs/_index.json` (regenerated to pick up new H1 title)
- Modify: `docs/_search.json` (regenerated to pick up new body)

**Interfaces:**
- Consumes: Task 1's new section order — Partners bucket is now position 3 with exactly 3 entries
- Produces: Partners README that cross-links to `platform/quickstart`, `platform/new-domain-template`, `mcp/MCP_SERVER_SPEC`, `mcp/tools/load_nifti` so the SPA tree's `partners/README` becomes the canonical entry point for new partners

- [ ] **Step 1: Replace `docs/partners/README.md` entirely**

```markdown
# Partner Onboarding Guide

This guide walks a new partner team through the four steps to ship a domain into BabyVision-v2: explore the viewer, add a new domain, wire up MCP tools, and submit a benchmark entry.

> Partner case studies live in `examples/` — see [Genesis AI — embodied physics integration](examples/genesis-embodied.md) and [Medical Spatial — GT-grounded reasoning](examples/medical-spatial.md).

---

## 1. Use the viewer

The viewer is a static site (`python -m http.server` from the repo root) with five pages:

- **Home** (`viewer/index.html`) — Hero, motivation, launcher tiles, task families, domain coverage.
- **Doc** (`viewer/docs.html`) — Three-pane docs SPA with tree, content, and search.
- **Domain 3D Assets** (`viewer/assets.html`) — Browse domain-grouped 3D assets indexed by `data/assets.index.json`.
- **QA Tasks** (`viewer/qagen.html`) — Browse generated QA items indexed by `data/qagen_index.json`.
- **Leaderboard** (`viewer/leaderboard.html`) — Submitted model scores.

The single source of truth is `viewer/manifest.json`. Every page reads it via `shared/manifest.js`, which exposes `window.Manifest` and `window.ManifestReady`. To preview locally:

```bash
python -m http.server 8765
# then open http://localhost:8765/viewer/index.html
```

For the full platform tour see [Platform — Quickstart](../platform/quickstart.md) and [Platform — Architecture](../platform/architecture.md).

---

## 2. Add a new domain

There are two flavors of "new domain":

**(a) Adding to an existing domain** (e.g. more medical NIFTI volumes) — only data changes:

1. Add your asset files under `data/<domain>/...` (or push them via the partner data pipeline; sensitive paths are git-ignored via `datasets/*/raw/` and `datasets/*/derived/`).
2. Append entries to `data/assets.index.json` matching the existing entry schema (`id`, `domain`, `family`, `label`, `format`, `path`).
3. Append entries to `data/qagen_index.json` for any new QA items.
4. Bump `viewer/manifest.json:counts.assets` and `counts.qagen` so the homepage launcher counts stay accurate.
5. Run `pytest tests/test_data_indexes.py` to confirm consistency.

**(b) Adding a brand-new domain** (e.g. astronomy, photonics) — viewer config + data:

1. Add the domain to `viewer/manifest.json:domains[]` with `{id, label, color, shipped: true}`. Use a unique color hex.
2. Add a renderer hint: if your modality has an in-browser viewer (Mol* for protein, Niivue for NIFTI, Cornerstone for DICOM, Three.js for GLB, OCCT for CAD), wire it into `viewer/shared/viewers/<domain>.js` following the pattern in the existing renderers.
3. Follow flow (a) above to populate data + counts.

The template that walks through (b) in detail is [Platform — New-domain template](../platform/new-domain-template.md).

---

## 3. Wire up MCP tools

The MCP (Model Context Protocol) layer exposes deterministic, executable ground-truth tools to evaluation runs. Two flavors:

**(a) Use existing tools.** The shipped tools are documented in `docs/mcp/tools/`:

- [`load_nifti`](../mcp/tools/load_nifti.md) — load a NIFTI volume into the agent's scratch space.
- [`extract_segment`](../mcp/tools/extract_segment.md) — extract a labelled segmentation region.
- [`compute_volume`](../mcp/tools/compute_volume.md) — compute volume in mm³ from a segmentation.

Each tool has a JSON Schema (`<tool>.schema.json`) and an intro (`<tool>.intro.md`); the docs SPA composes them at view time. To call a tool from your evaluation harness, see [MCP server specification](../mcp/MCP_SERVER_SPEC.md).

**(b) Add a new tool.** Two artifacts per tool, both committed to `docs/mcp/tools/`:

1. `<tool>.schema.json` — JSON Schema 2020-12, with required keys `name`, `description`, `inputSchema`, `outputSchema`, plus optional `domain` for tagging.
2. `<tool>.intro.md` — short Markdown intro (no H1; the schema's `name` becomes the title) explaining when to call the tool and what success looks like.

Rebuild the docs index after adding the pair:

```bash
python scripts/build_docs_index.py
python scripts/build_docs_search.py
```

The phased rollout plan is at [MCP Server — Phased Rollout Plan](../mcp/MCP_ROLLOUT_PLAN.md).

---

## 4. Submit a benchmark entry

Once your domain data + MCP tools are wired in, generate QA items for the appropriate task family (see [Benchmark — Task families](../benchmark/task-families.md)), run your model against them, and submit a leaderboard entry per [Benchmark — Submission guide](../benchmark/submission-guide.md).

The scoring protocol — including which metrics defend against LLM-as-judge — is documented at [Benchmark — Evaluation protocol](../benchmark/evaluation-protocol.md).

---

## Reference

| Step | Primary doc | Supporting docs |
|---|---|---|
| 1. Use the viewer | [Platform — Quickstart](../platform/quickstart.md) | [Platform — Architecture](../platform/architecture.md) |
| 2. Add a new domain | [Platform — New-domain template](../platform/new-domain-template.md) | [Platform — API reference](../platform/api-reference.md), [Platform — QA-Gen → viewer reload loop](../platform/qa-to-viewer-loop.md) |
| 3. Wire MCP tools | [MCP — Server specification](../mcp/MCP_SERVER_SPEC.md) | [MCP — Phased rollout plan](../mcp/MCP_ROLLOUT_PLAN.md), [`load_nifti`](../mcp/tools/load_nifti.md) |
| 4. Submit benchmark entry | [Benchmark — Submission guide](../benchmark/submission-guide.md) | [Benchmark — Evaluation protocol](../benchmark/evaluation-protocol.md), [Benchmark — Task families](../benchmark/task-families.md) |

Sensitive coordination (deadlines, contact people, internal roadmap) lives in private Notion pages — never in this repo. Adding a public case study? See `examples/` for the template.
```

- [ ] **Step 2: Regenerate doc index + search**

```bash
python scripts/build_docs_index.py
python scripts/build_docs_search.py
```

Expected: the Partners section's first entry now has title `"Partner Onboarding Guide"` instead of `"Partners"`.

- [ ] **Step 3: Verify in browser (manual)**

```bash
python -m http.server 8765 &
SERVER_PID=$!
sleep 1
curl -sS http://localhost:8765/viewer/docs.html?doc=partners/README -o /dev/null -w "%{http_code}\n"
kill $SERVER_PID
```

Expected: `200`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: all pass. The `test_no_chinese_in_displayed_docs` test passes (Partners README is English).

- [ ] **Step 5: Commit**

```bash
git add docs/partners/README.md docs/_index.json docs/_search.json
git commit -m "docs(partners): rewrite README as Partner Onboarding Guide

Four-step guide: use the viewer → add a new domain → wire MCP tools →
submit benchmark entry. Cross-links to platform/, mcp/, benchmark/ rather
than duplicating their content."
```

---

## Task 3: Create `data/assets.index.json` + render shipped-domain assets on `viewer/assets.html`

**Files:**
- Create: `data/assets.index.json`
- Modify: `viewer/assets.html` (add rendering for shipped-domain asset lists)
- Create: `tests/test_data_indexes.py` (schema test for assets.index.json)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `data/assets.index.json` — schema `{version: 1, generated_at: "...", assets: [{id, domain, family, label, format, path}]}`. Asset count: exactly 6. Domains used: `embodied` (4 entries via `genesis_vp` family), `medical` (2 entries via `medical_spatial` family).
  - Asset IDs (consumed by Task 4 for QA cross-reference):
    - `embodied/genesis_vp/cut_dragon`
    - `embodied/genesis_vp/franka_grasp`
    - `embodied/genesis_vp/ipc_cloth`
    - `embodied/genesis_vp/sand_wheel`
    - `medical/medical_spatial/ixi_brain_demo`
    - `medical/medical_spatial/ixi_brain_demo_mask`

- [ ] **Step 1: Compute file sizes for the schema**

```bash
ls -l viewer/datasets/genesis/*.glb examples/02_medical_spatial_gt/data/*.nii.gz | awk '{print $5, $NF}'
```

Record the byte sizes — they go into the JSON. If a file is missing, halt and surface the issue rather than guessing.

- [ ] **Step 2: Write the failing test**

Create `tests/test_data_indexes.py`:

```python
"""Schema + integrity tests for data/assets.index.json and data/qagen_index.json.

These two files are the source of truth for the launcher counts on the
homepage and for the per-domain / per-family lists on assets.html /
qagen.html. The contract:
  - JSON parses cleanly.
  - Each asset / question has every required key.
  - The file count matches viewer/manifest.json's counts block.
  - Every asset file referenced by `path` actually exists on disk.
  - Every QA item's asset_id resolves to an asset in assets.index.json.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "data" / "assets.index.json"
QAGEN  = REPO_ROOT / "data" / "qagen_index.json"
MANIFEST = REPO_ROOT / "viewer" / "manifest.json"

REQUIRED_ASSET_KEYS = {"id", "domain", "family", "label", "format", "path"}
REQUIRED_QA_KEYS    = {"id", "family", "asset_id", "question",
                       "answer_kind", "ground_truth_kind"}


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_assets_index_is_well_formed():
    data = _load(ASSETS)
    assert data["version"] == 1
    assert isinstance(data["assets"], list)
    assert len(data["assets"]) == 6, "expected exactly 6 demo assets"
    for a in data["assets"]:
        missing = REQUIRED_ASSET_KEYS - a.keys()
        assert not missing, f"asset {a.get('id')} missing keys: {missing}"
        target = REPO_ROOT / a["path"]
        assert target.exists(), f"asset file not on disk: {a['path']}"


def test_asset_domain_and_family_alignment():
    data = _load(ASSETS)
    embodied = [a for a in data["assets"] if a["domain"] == "embodied"]
    medical  = [a for a in data["assets"] if a["domain"] == "medical"]
    assert len(embodied) == 4
    assert len(medical) == 2
    assert all(a["family"] == "genesis_vp"     for a in embodied)
    assert all(a["family"] == "medical_spatial" for a in medical)


def test_manifest_counts_match_index_lengths():
    m = _load(MANIFEST)
    assets = _load(ASSETS)
    qagen  = _load(QAGEN)
    assert m["counts"]["assets"] == len(assets["assets"])
    assert m["counts"]["qagen"]  == len(qagen["questions"])


def test_qagen_is_well_formed():
    data = _load(QAGEN)
    assert data["version"] == 1
    assert isinstance(data["questions"], list)
    assert len(data["questions"]) == 4, "expected exactly 4 demo questions"
    for q in data["questions"]:
        missing = REQUIRED_QA_KEYS - q.keys()
        assert not missing, f"question {q.get('id')} missing keys: {missing}"


def test_every_qa_asset_id_resolves():
    asset_ids = {a["id"] for a in _load(ASSETS)["assets"]}
    for q in _load(QAGEN)["questions"]:
        assert q["asset_id"] in asset_ids, \
            f"QA {q['id']} references unknown asset {q['asset_id']}"
```

- [ ] **Step 3: Run test to verify failure**

```bash
pytest tests/test_data_indexes.py -v
```

Expected: FAIL with "No such file or directory: .../data/assets.index.json" — confirms the test detects the missing artifact.

- [ ] **Step 4: Create `data/assets.index.json`**

```bash
mkdir -p data
```

Then write the file at `data/assets.index.json`:

```json
{
  "version": 1,
  "generated_at": "2026-06-23T00:00:00Z",
  "assets": [
    {
      "id": "embodied/genesis_vp/cut_dragon",
      "domain": "embodied",
      "family": "genesis_vp",
      "label": "Cut Dragon — MPM fracture demo",
      "format": "glb",
      "path": "viewer/datasets/genesis/cut_dragon.glb"
    },
    {
      "id": "embodied/genesis_vp/franka_grasp",
      "domain": "embodied",
      "family": "genesis_vp",
      "label": "Franka Grasp — force control demo",
      "format": "glb",
      "path": "viewer/datasets/genesis/franka_grasp.glb"
    },
    {
      "id": "embodied/genesis_vp/ipc_cloth",
      "domain": "embodied",
      "family": "genesis_vp",
      "label": "IPC Cloth — FEM cloth demo",
      "format": "glb",
      "path": "viewer/datasets/genesis/ipc_cloth.glb"
    },
    {
      "id": "embodied/genesis_vp/sand_wheel",
      "domain": "embodied",
      "family": "genesis_vp",
      "label": "Sand Wheel — MPM granular cascade",
      "format": "glb",
      "path": "viewer/datasets/genesis/sand_wheel.glb"
    },
    {
      "id": "medical/medical_spatial/ixi_brain_demo",
      "domain": "medical",
      "family": "medical_spatial",
      "label": "IXI brain — sample volume",
      "format": "nifti",
      "path": "examples/02_medical_spatial_gt/data/image.nii.gz"
    },
    {
      "id": "medical/medical_spatial/ixi_brain_demo_mask",
      "domain": "medical",
      "family": "medical_spatial",
      "label": "IXI brain — segmentation mask",
      "format": "nifti",
      "path": "examples/02_medical_spatial_gt/data/mask.nii.gz"
    }
  ]
}
```

- [ ] **Step 5: Add rendering to `viewer/assets.html`**

Find the line `<section id="domain-grid" class="bv2-asset-domain-grid" aria-label="Domains"></section>` and add a new section immediately after it:

```html
  <section id="asset-list" aria-label="Assets" style="margin-top:32px"></section>
```

Then in the existing `<script>` block, after the `loadAssetCount` function definition, add a sibling function:

```javascript
  function renderAssetList(assets) {
    const root = $('asset-list');
    if (!root) return;
    if (!assets.length) return;
    // Group by domain for clarity.
    const byDomain = {};
    for (const a of assets) {
      (byDomain[a.domain] = byDomain[a.domain] || []).push(a);
    }
    const html = [];
    for (const domain of Object.keys(byDomain).sort()) {
      const items = byDomain[domain];
      html.push('<div class="bv2-asset-domain-group">');
      html.push('  <h2 style="font-size:14px;margin:24px 0 8px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.05em">' + domain + '</h2>');
      html.push('  <ul style="list-style:none;padding:0;margin:0">');
      for (const a of items) {
        html.push(
          '<li style="padding:8px 12px;border:1px solid var(--border);border-radius:4px;margin-bottom:6px;display:flex;justify-content:space-between;gap:12px">' +
          '<span><strong>' + a.label + '</strong> <code style="color:var(--fg-dim);font-size:11px">' + a.format + '</code></span>' +
          '<code style="color:var(--fg-dim);font-size:11px">' + a.path + '</code>' +
          '</li>'
        );
      }
      html.push('  </ul>');
      html.push('</div>');
    }
    root.innerHTML = html.join('\n');
  }
```

And modify `loadAssetCount` to also call `renderAssetList`:

Replace:
```javascript
      .then((j) => {
        const meta = $('counts');
        const n = Array.isArray(j) ? j.length : (j.count || (j.assets ? j.assets.length : 0));
        meta.textContent = n + ' assets · ' + window.Manifest.brand + ' v' + window.Manifest.version;
      })
```

With:
```javascript
      .then((j) => {
        const assets = Array.isArray(j) ? j : (j.assets || []);
        const meta = $('counts');
        meta.textContent = assets.length + ' assets · ' + window.Manifest.brand + ' v' + window.Manifest.version;
        renderAssetList(assets);
      })
```

- [ ] **Step 6: Confirm test still fails on the manifest-counts check, qagen check, and on the "qa asset_id resolves" check**

```bash
pytest tests/test_data_indexes.py -v
```

Expected: `test_assets_index_is_well_formed` PASS, `test_asset_domain_and_family_alignment` PASS, `test_qagen_is_well_formed` FAIL (file not yet present), `test_manifest_counts_match_index_lengths` FAIL (qagen not present + manifest counts still 0), `test_every_qa_asset_id_resolves` FAIL (file not present).

Those remaining failures are addressed by Tasks 4 + 5 — proceed to commit.

- [ ] **Step 7: Smoke-test the page**

```bash
python -m http.server 8765 &
SERVER_PID=$!
sleep 1
curl -sS http://localhost:8765/viewer/assets.html | grep -c 'asset-list\|bv2-asset-domain-grid'
kill $SERVER_PID
```

Expected: `2` (both the existing domain-grid section and the new asset-list section are present in HTML).

- [ ] **Step 8: Commit**

```bash
git add data/assets.index.json viewer/assets.html tests/test_data_indexes.py
git commit -m "feat(data): seed data/assets.index.json with 6 demo assets

- 4 GLB files from viewer/datasets/genesis/ (embodied/genesis_vp family)
- 2 NIFTI files from examples/02_medical_spatial_gt/data/ (medical/medical_spatial)
- assets.html renders a per-domain list under the existing domain grid
- New tests/test_data_indexes.py locks the schema; qagen + manifest checks
  intentionally still red until Tasks 4 + 5."
```

---

## Task 4: Create `data/qagen_index.json` + render shipped-family QA on `viewer/qagen.html`

**Files:**
- Create: `data/qagen_index.json`
- Modify: `viewer/qagen.html` (add rendering for shipped-family QA list)

**Interfaces:**
- Consumes: Task 3's asset IDs (must reference existing IDs from `data/assets.index.json`)
- Produces:
  - `data/qagen_index.json` — schema `{version: 1, generated_at: "...", questions: [{id, family, asset_id, question, answer_kind, ground_truth_kind}]}`. Question count: exactly 4. 2 questions per family (`genesis_vp`, `medical_spatial`).

- [ ] **Step 1: Write `data/qagen_index.json`**

```json
{
  "version": 1,
  "generated_at": "2026-06-23T00:00:00Z",
  "questions": [
    {
      "id": "genesis_vp/q001",
      "family": "genesis_vp",
      "asset_id": "embodied/genesis_vp/cut_dragon",
      "question": "Under a 5 N transverse force applied at the marked vertebra, will the dragon fracture into two disconnected pieces within 200 simulation steps?",
      "answer_kind": "boolean",
      "ground_truth_kind": "deterministic_simulation"
    },
    {
      "id": "genesis_vp/q002",
      "family": "genesis_vp",
      "asset_id": "embodied/genesis_vp/sand_wheel",
      "question": "After rotating the wheel by 90 degrees clockwise, what fraction of the granular sand pile (initially resting at the wheel base) remains within a 0.1 m radius of the rotation axis?",
      "answer_kind": "numeric_range",
      "ground_truth_kind": "deterministic_simulation"
    },
    {
      "id": "medical_spatial/q001",
      "family": "medical_spatial",
      "asset_id": "medical/medical_spatial/ixi_brain_demo_mask",
      "question": "What is the total volume in mm^3 of the labelled region in the segmentation mask?",
      "answer_kind": "numeric",
      "ground_truth_kind": "mcp_compute_volume"
    },
    {
      "id": "medical_spatial/q002",
      "family": "medical_spatial",
      "asset_id": "medical/medical_spatial/ixi_brain_demo",
      "question": "Counting connected components in the segmentation mask (6-connectivity), how many distinct regions are present?",
      "answer_kind": "integer",
      "ground_truth_kind": "deterministic_topology"
    }
  ]
}
```

- [ ] **Step 2: Add rendering to `viewer/qagen.html`**

Find the closing `</section>` after the 6 task cards and before `<p style="margin-top:32px;...">`, then add a new section immediately after that closing tag:

```html
  <section id="qagen-list" aria-label="Generated QA items" style="margin-top:40px"></section>
```

Then replace the existing trailing `<script>` block:

Old:
```html
<script src="shared/manifest.js"></script>
<script>
(function () {
  window.ManifestReady.then((m) => {
    document.getElementById('counts').textContent = m.brand + ' v' + m.version;
  });
})();
</script>
```

New:
```html
<script src="shared/manifest.js"></script>
<script>
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function renderQaList(questions) {
    const root = $('qagen-list');
    if (!root) return;
    if (!questions.length) return;
    const byFamily = {};
    for (const q of questions) {
      (byFamily[q.family] = byFamily[q.family] || []).push(q);
    }
    const html = [];
    html.push('<h2 style="font-size:16px;margin:0 0 12px">Generated QA items</h2>');
    for (const family of Object.keys(byFamily).sort()) {
      html.push('<div class="bv2-qagen-family-group" style="margin-bottom:16px">');
      html.push('  <h3 style="font-size:12px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.05em;margin:16px 0 6px">' + family + '</h3>');
      html.push('  <ol style="padding-left:24px;margin:0">');
      for (const q of byFamily[family]) {
        html.push(
          '<li style="padding:6px 0;border-bottom:1px solid var(--border)">' +
          '<div style="font-size:13px">' + q.question + '</div>' +
          '<div style="color:var(--fg-dim);font-size:11px;margin-top:4px"><code>' + q.id + '</code> · answer: ' + q.answer_kind + ' · GT: ' + q.ground_truth_kind + '</div>' +
          '</li>'
        );
      }
      html.push('  </ol>');
      html.push('</div>');
    }
    root.innerHTML = html.join('\n');
  }

  window.ManifestReady.then((m) => {
    $('counts').textContent = m.brand + ' v' + m.version;
    if (!m.urls || !m.urls.data || !m.urls.data.qagen_index) return;
    fetch(m.urls.data.qagen_index, { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then((j) => renderQaList(Array.isArray(j) ? j : (j.questions || [])))
      .catch(() => { /* leave list empty if not yet present */ });
  });
})();
</script>
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_data_indexes.py -v
```

Expected: `test_qagen_is_well_formed` PASS, `test_every_qa_asset_id_resolves` PASS, `test_manifest_counts_match_index_lengths` still FAIL (manifest counts still 0 — fixed in Task 5).

- [ ] **Step 4: Smoke-test**

```bash
python -m http.server 8765 &
SERVER_PID=$!
sleep 1
curl -sS http://localhost:8765/viewer/qagen.html | grep -c 'qagen-list'
kill $SERVER_PID
```

Expected: `1`.

- [ ] **Step 5: Commit**

```bash
git add data/qagen_index.json viewer/qagen.html
git commit -m "feat(data): seed data/qagen_index.json with 4 demo QA items

- 2 items per shipped family: genesis_vp + medical_spatial
- Cross-references asset_ids established in data/assets.index.json
- qagen.html now renders the QA list under the static family cards"
```

---

## Task 5: Update `viewer/manifest.json` counts; verify launcher

**Files:**
- Modify: `viewer/manifest.json`

**Interfaces:**
- Consumes: Task 3 produces 6 assets, Task 4 produces 4 questions
- Produces: `viewer/manifest.json:counts.assets == 6`, `counts.qagen == 4`, `counts.mcp_tools == 3` (already correct? verify). Launcher tiles on index.html show those numbers instead of `—`.

- [ ] **Step 1: Update `viewer/manifest.json`**

In `viewer/manifest.json`, change the `counts` block:

Old:
```json
  "counts": {
    "assets": 0,
    "qagen": 0,
    "mcp_tools": 0,
    "leaderboard_entries": 0
  },
```

New:
```json
  "counts": {
    "assets": 6,
    "qagen": 4,
    "mcp_tools": 3,
    "leaderboard_entries": 0
  },
```

Rationale for `mcp_tools = 3`: `docs/mcp/tools/` contains 3 schema pairs (compute_volume, extract_segment, load_nifti).

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: ALL tests pass — including `test_manifest_counts_match_index_lengths`. The `test_manifest_schema.py` `counts` assertion (if any) should not fail since the schema allows any non-negative integer; if it does, surface the failure to the controller before guessing.

- [ ] **Step 3: Smoke-test the homepage launcher**

```bash
python -m http.server 8765 &
SERVER_PID=$!
sleep 1
# Verify the manifest fetch surfaces real counts
curl -sS http://localhost:8765/viewer/manifest.json | python3 -c "import json,sys; m=json.load(sys.stdin); assert m['counts']['assets']==6 and m['counts']['qagen']==4 and m['counts']['mcp_tools']==3, m['counts']; print('OK')"
kill $SERVER_PID
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add viewer/manifest.json
git commit -m "feat(manifest): set counts.{assets,qagen,mcp_tools} to {6,4,3}

Matches data/assets.index.json (6 entries) + data/qagen_index.json (4
entries) + docs/mcp/tools/*.schema.json (3 pairs). Launcher tiles on the
homepage now show real numbers instead of '—'. Test
test_manifest_counts_match_index_lengths locks this consistency."
```

---

## Task 6: Task-family display rename (Interactive Physics / Spatial Vision)

**Files:**
- Modify: `viewer/index.html` (lines around the 6 task cards)
- Modify: `viewer/qagen.html` (same 6 task cards in this page's grid)
- Modify: `docs/benchmark/task-families.md` (H2 titles)
- Modify: `docs/_index.json`, `docs/_search.json` (regenerated to pick up renamed H2 in task-families.md — but title at the top is `Task families` so no change at section level)

**Interfaces:**
- Consumes: nothing
- Produces:
  - In `viewer/index.html` and `viewer/qagen.html`: the 5th `<article class="bv2-task-card">` displays `Interactive Physics` instead of `Genesis_VP — Physics`; the 6th displays `Spatial Vision` instead of `Medical Spatial — GT-grounded`.
  - Deep-link anchors updated:
    - `#genesis_vp--physics` → `#interactive-physics`
    - `#medical-spatial--gt-grounded` → `#spatial-vision`
  - In `docs/benchmark/task-families.md`: the H2 lines become `## Interactive Physics` and `## Spatial Vision`.
  - **NOT changed:** `data-family="Genesis_VP"` and `data-family="Medical_Spatial"` (internal IDs preserved per Q4).

- [ ] **Step 1: Modify `viewer/index.html` task cards (lines 106–115)**

Replace:
```html
      <article class="bv2-task-card">
        <h2>Genesis_VP — Physics</h2>
        <p class="bv2-task-keywords">force control · MPM fracture · MPM granular · FEM cloth · P1/P2</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#genesis_vp--physics">Family details →</a></p>
      </article>
      <article class="bv2-task-card">
        <h2>Medical Spatial — GT-grounded</h2>
        <p class="bv2-task-keywords">M3D · IXI brain · distance / angle / topology / volume</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#medical-spatial--gt-grounded">Family details →</a></p>
      </article>
```

With:
```html
      <article class="bv2-task-card">
        <h2>Interactive Physics</h2>
        <p class="bv2-task-keywords">force control · MPM fracture · MPM granular · FEM cloth · P1/P2 — discover by acting on objects</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#interactive-physics">Family details →</a></p>
      </article>
      <article class="bv2-task-card">
        <h2>Spatial Vision</h2>
        <p class="bv2-task-keywords">M3D · IXI brain · distance / angle / topology / volume</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#spatial-vision">Family details →</a></p>
      </article>
```

Also update the launcher tile meta (line 72-73) — replace:
```html
        V2 / V3 / V4 / V6 · Genesis_VP · Medical Spatial
```

With:
```html
        V2 / V3 / V4 / V6 · Interactive Physics · Spatial Vision
```

- [ ] **Step 2: Modify `viewer/qagen.html` task cards**

Replace:
```html
    <article class="bv2-task-card" data-family="Genesis_VP">
      <h2>Genesis_VP — Physics</h2>
      <p class="bv2-task-keywords">force control · MPM fracture · MPM granular · FEM cloth · P1/P2</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#genesis_vp--physics">Details →</a></p>
    </article>
    <article class="bv2-task-card" data-family="Medical_Spatial">
      <h2>Medical Spatial — GT-grounded</h2>
      <p class="bv2-task-keywords">M3D · IXI brain · distance / angle / topology / volume</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#medical-spatial--gt-grounded">Details →</a></p>
    </article>
```

With:
```html
    <article class="bv2-task-card" data-family="Genesis_VP">
      <h2>Interactive Physics</h2>
      <p class="bv2-task-keywords">force control · MPM fracture · MPM granular · FEM cloth · P1/P2 — discover by acting on objects</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#interactive-physics">Details →</a></p>
    </article>
    <article class="bv2-task-card" data-family="Medical_Spatial">
      <h2>Spatial Vision</h2>
      <p class="bv2-task-keywords">M3D · IXI brain · distance / angle / topology / volume</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#spatial-vision">Details →</a></p>
    </article>
```

Note: `data-family` attribute values stay `Genesis_VP` / `Medical_Spatial` (internal IDs preserved).

- [ ] **Step 3: Modify `docs/benchmark/task-families.md`**

Replace:
```markdown
## Genesis_VP — physics

Force control, MPM fracture, MPM granular cascade, FEM cloth. P1/P2 difficulty. Genesis physics engine in the loop.

## Medical Spatial — GT-grounded
```

With:
```markdown
## Interactive Physics

Force control, MPM fracture, MPM granular cascade, FEM cloth. P1/P2 difficulty. Genesis physics engine in the loop. Models discover task answers by acting on objects in the environment, not just viewing them.

## Spatial Vision
```

The body under "Spatial Vision" stays as-is (still about Medical Spatial content).

- [ ] **Step 4: Regenerate doc index + search**

```bash
python scripts/build_docs_index.py
python scripts/build_docs_search.py
```

Expected: no entry-count change. The H1 of `task-families.md` is still `Task families`, so the sidebar title is unchanged.

- [ ] **Step 5: Run tests + smoke-check anchors**

```bash
pytest tests/ -v
# Verify the new anchors are now in HTML
grep -c 'interactive-physics\|spatial-vision' viewer/index.html viewer/qagen.html
# Expected: a line per file showing 2 (one match for each anchor)
# Verify the old anchors are gone
grep -c 'genesis_vp--physics\|medical-spatial--gt-grounded' viewer/index.html viewer/qagen.html
# Expected: a line per file showing 0
```

Expected: all tests pass; old anchors absent; new anchors present.

- [ ] **Step 6: Commit**

```bash
git add viewer/index.html viewer/qagen.html docs/benchmark/task-families.md \
        docs/_index.json docs/_search.json
git commit -m "refactor: rename task family display labels (Q4-compliant)

Display labels:
  Genesis_VP — Physics      → Interactive Physics
  Medical Spatial — GT-grounded → Spatial Vision

Deep-link anchors updated to match. Internal IDs preserved
(data-family='Genesis_VP', data-family='Medical_Spatial') so manifest
and QA-gen upstream consumers are unaffected."
```

---

## Task 7: Homepage section reorder + hero copy refresh

**Files:**
- Modify: `viewer/index.html` (sections 28–203)

**Interfaces:**
- Consumes: Task 6's renamed task labels (cards already show "Interactive Physics" / "Spatial Vision" — Task 7 doesn't re-touch them)
- Produces: index.html sections in the order Hero → Motivation → Launcher → Tasks → Domain → Compare → Get started. Hero copy revised per Q1.

- [ ] **Step 1: Reorder sections in `viewer/index.html`**

The current order inside `<main>` is:
1. `.bv2-hero`
2. `.bv2-launcher`
3. `.bv2-section#bv2-tasks-section`
4. `.bv2-section#bv2-motivation-section`
5. `.bv2-section#bv2-domain-section`
6. `.bv2-section#bv2-compare-section`
7. `.bv2-section#bv2-getstarted-section`

The target order is:
1. `.bv2-hero` (refreshed copy)
2. `.bv2-section#bv2-motivation-section`
3. `.bv2-launcher`
4. `.bv2-section#bv2-tasks-section`
5. `.bv2-section#bv2-domain-section`
6. `.bv2-section#bv2-compare-section`
7. `.bv2-section#bv2-getstarted-section`

Perform this as a manual reorder using `Edit`. Read the current `viewer/index.html` first (it's ~215 lines), then move:
- the `.bv2-section#bv2-motivation-section` block (currently lines 119–152) to immediately after `</section>` of `.bv2-hero` (currently ending around line 46) and before `<section class="bv2-launcher" ...>` (currently starting at line 48).
- The `.bv2-section#bv2-tasks-section` block (currently lines 78–117) stays after `.bv2-launcher`.

- [ ] **Step 2: Refresh hero copy**

Replace the entire `<section class="bv2-hero">` block:

Old:
```html
  <section class="bv2-hero">
    <div class="bv2-hero-eyebrow">
      BabyVision-v2 · Benchmark + Platform
      <span class="version">v0.1.1</span>
    </div>
    <h1 class="bv2-hero-title">
      Evaluating 3D-grounded visual reasoning<br>
      in professional domains.
    </h1>
    <p class="bv2-hero-tagline">
      Evaluation-first benchmark with executable ground truth — no LLM-as-judge.
      Partner-extensible: bring a new domain, plug in MCP tools, generate QA at scale.
    </p>
    <div class="bv2-hero-pillars" aria-label="Pillars">
      <span>executable GT</span>
      <span>3D-grounded</span>
      <span>partner-extensible</span>
    </div>
  </section>
```

New:
```html
  <section class="bv2-hero">
    <div class="bv2-hero-eyebrow">
      BabyVision-v2 · Benchmark + Platform
      <span class="version">v0.1.1</span>
    </div>
    <h1 class="bv2-hero-title">
      Evaluating 3D-grounded visual reasoning<br>
      across scientific and interactive domains.
    </h1>
    <p class="bv2-hero-tagline">
      Executable ground truth — no LLM-as-judge. Tasks span scientific imagery
      (medical, geographic, biological) and interactive physics, where models
      discover the world by acting on it, not just viewing it.
    </p>
    <div class="bv2-hero-pillars" aria-label="Pillars">
      <span>executable GT</span>
      <span>3D-grounded</span>
      <span>scientific + interactive</span>
    </div>
  </section>
```

- [ ] **Step 3: Smoke-test the page renders + the new order is in the DOM**

```bash
python -m http.server 8765 &
SERVER_PID=$!
sleep 1
# Verify the order: motivation section's id should appear BEFORE the launcher section in source
python3 -c "
import re
html = open('viewer/index.html').read()
order = [m.group(1) for m in re.finditer(r'<section[^>]*?(?:id=\"([^\"]+)\"|class=\"(bv2-(?:hero|launcher))\")', html)]
# Flatten (m.group returns either id or class; pick first non-None) — simpler: just check substring positions
positions = {
    'hero':        html.find('class=\"bv2-hero\"'),
    'motivation':  html.find('id=\"bv2-motivation-section\"'),
    'launcher':    html.find('class=\"bv2-launcher\"'),
    'tasks':       html.find('id=\"bv2-tasks-section\"'),
    'domain':      html.find('id=\"bv2-domain-section\"'),
}
order_check = ['hero', 'motivation', 'launcher', 'tasks', 'domain']
last = -1
for name in order_check:
    p = positions[name]
    assert p > last, f'{name} (pos {p}) is out of order; expected after previous (pos {last})'
    last = p
print('OK — section order is hero → motivation → launcher → tasks → domain')
"
# Verify new hero copy is present
grep -c "scientific and interactive domains" viewer/index.html
# Expected: 1
kill $SERVER_PID
```

Expected: `OK — section order …` line + `1`.

- [ ] **Step 4: Run the whole test suite one more time**

```bash
pytest tests/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add viewer/index.html
git commit -m "feat(home): reorder sections + refresh hero copy (Q1)

Order: Hero → Motivation → Launcher → Tasks → Domain → Compare → Get
started. Hero now frames evaluation across scientific AND interactive
domains, where models discover by acting on objects (not just viewing
them). Pillar 'partner-extensible' replaced with 'scientific +
interactive' to match."
```

---

## Final whole-branch review handoff

After Task 7 commits, before opening the PR:

- [ ] **Final Step A: Final whole-branch review on `claude-opus-4-8`**

Run this skill's `scripts/review-package` to build the diff payload:

```bash
cd /Users/sue/.claude/plugins/cache/claude-plugins-official/superpowers/6.0.3/skills/subagent-driven-development
./scripts/review-package $(git -C /Users/sue/Research/BabyVision-v2 merge-base main HEAD) HEAD
```

Then dispatch the code-reviewer subagent (template: `../requesting-code-review/code-reviewer.md`) with `model: claude-opus-4-8`, passing the printed review-package path and these global constraints:

- No new runtime JS dependencies in viewer/.
- All displayed docs (paths in `docs/_index.json`) must contain zero Han characters.
- `data-family="Genesis_VP"` and `data-family="Medical_Spatial"` MUST still exist in the HTML (internal IDs preserved).
- `viewer/manifest.json:counts` must match `data/{assets.index,qagen_index}.json` lengths and `docs/mcp/tools/*.schema.json` count.
- All 36+ pytest tests pass.

- [ ] **Final Step B: Open the PR**

```bash
git push -u origin fix/viewer-feedback-batch1
gh pr create --title "fix: viewer-feedback batch 1 (docs reorg + demo data + family rename + hero refresh)" --body "$(cat <<'EOF'
## Summary
Resolves all Must-fix items in `docs/viewer-feedback.md` Batch 1:

- **F1 / F1b**: Homepage section reorder (Hero → Motivation → Launcher → Tasks → Domain → Compare → Get started); hero copy refreshed to frame scientific + interactive domains.
- **F2**: Real demo data wired in — 6 assets (4 GLB + 2 NIFTI) in `data/assets.index.json`, 4 QA items (2 per shipped family) in `data/qagen_index.json`. Launcher tiles now show real counts instead of `—`.
- **F3a**: 13 Chinese docs + 6 internal SDD artifacts excluded from the docs SPA via `EXCLUDED_PATHS` in `scripts/build_docs_*.py`. Source files remain in repo. Safety-net test `tests/test_docs_no_chinese.py` locks the promise.
- **F3b**: `docs/partners/README.md` rewritten as a 4-step Partner Onboarding Guide (use viewer / add domain / wire MCP / submit benchmark).
- **F3c**: Docs SPA section order now `Platform → MCP tools → Partners → Benchmark → Research notes`.
- **F4**: Task family display rename (Genesis_VP — Physics → Interactive Physics; Medical Spatial — GT-grounded → Spatial Vision). Internal `data-family` IDs preserved.

## Test plan
- [ ] `pytest tests/ -v` — all green (≥41 tests)
- [ ] `python scripts/build_docs_index.py --check` — OK
- [ ] `python scripts/build_docs_search.py --check` — OK
- [ ] `python -m http.server 8765`; visit:
  - http://localhost:8765/viewer/index.html — launcher shows `3` / `6` / `4`; section order matches
  - http://localhost:8765/viewer/assets.html — lists 6 assets grouped by domain
  - http://localhost:8765/viewer/qagen.html — last 2 cards say Interactive Physics / Spatial Vision; QA list below shows 4 items
  - http://localhost:8765/viewer/docs.html — sidebar order Platform → MCP → Partners → Benchmark → Research notes; no Chinese visible
  - http://localhost:8765/viewer/docs.html?doc=partners/README — renders the new Onboarding Guide
EOF
)"
```

---

## Self-Review Checklist (controller pass before dispatching Task 1)

**1. Spec coverage:**
- F1 (section reorder) → Task 7 ✓
- F1b (hero copy) → Task 7 ✓
- F2 (data wiring) → Tasks 3 + 4 + 5 ✓
- F3a (Chinese exclusion) → Task 1 ✓
- F3b (Partners rewrite) → Task 2 ✓
- F3c (docs section order) → Task 1 ✓
- F4 (display rename) → Task 6 ✓
- F5 (selection/download) — **explicitly deferred** to Batch 2 per the user's `docs/viewer-feedback.md` execution batch table ✓

**2. Placeholder scan:** No TBD / TODO / "fill in later" patterns; every code edit includes the exact code; every command includes the exact expected output.

**3. Type consistency:**
- Asset IDs in Task 3 (`embodied/genesis_vp/cut_dragon`, etc.) match exactly the IDs referenced in Task 4's `asset_id` fields.
- `EXCLUDED_PATHS` set is the same in both `build_docs_index.py` (Task 1 Step 4) and `build_docs_search.py` (Task 1 Step 5) — copy/paste verbatim.
- The new section labels in `TOP_LEVEL_ORDER` (`["platform", "mcp", "partners", "benchmark", "research"]`) match `SECTION_LABEL` keys.
- The 6 assets count in Task 3 + 4 questions count in Task 4 match Task 5's manifest counts (6, 4) and the `==` assertions in `test_manifest_counts_match_index_lengths`.
- Hero copy in Task 7 matches the strings asserted by Step 3's `grep -c "scientific and interactive domains"` (1 occurrence).
