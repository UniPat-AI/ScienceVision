# BabyVision-v2 Viewer — Plan 2: Docs SPA

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `viewer/docs.html` as a self-contained markdown SPA — three-column layout, client-side rendering via marked.js, full-text search via lunr.js, code highlighting via Prism, MCP-tool virtual documents composed from `*.schema.json` + `*.intro.md` pairs, and history-aware URL routing.

**Architecture:** Zero-build static SPA. The page bootstraps from `viewer/manifest.json` (Plan 1) to find `docs/_index.json` (tree) and `docs/_search.json` (lunr index), both of which are pre-built artifacts committed to git. Per-doc markdown lives at `docs/**/*.md`, fetched on demand. MCP tools are virtual docs synthesized at render time from `docs/mcp/tools/<name>.schema.json` + `<name>.intro.md`. Six JS modules under `viewer/shared/docs/` each own one concern (app/tree/links/toc/search/mcp).

**Tech Stack:**
- marked@14.1.4 (markdown → HTML)
- Prism@1.30.0 (syntax highlighting; 8 language components)
- lunr@2.3.9 (search index + query)
- Python ≥3.10 for build scripts (`build_docs_index.py`, `build_docs_search.py`)
- pytest for build-script coverage
- Vanilla browser JS, no module bundler, no transpilation

## Global Constraints

- Python floor: `>=3.10` (from `pyproject.toml` line 10). No `Self`, no 3.11+-only syntax.
- File-size cap: ≤450 lines under `viewer/` mirrored material; ≤800 lines elsewhere.
- Commit message format: `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:` / `ci:`. No `Co-Authored-By:` trailer (attribution disabled globally).
- Immutable patterns: no in-place mutation of inputs. Browser-side, do not mutate `window.Manifest` (it's `Object.freeze`d).
- No CDN at runtime: every vendored library lives under `viewer/vendor/` and is committed.
- No build chain at the served path: pages must work via `python3 -m http.server --directory .` with zero pre-processing.
- All implementer subagents run on `sonnet` model unless this plan says otherwise. Every dispatch includes a Hard Scope Lockdown clause (see Task 1 for the canonical wording) — no branch creation, no `git push`, no `git reset`, no out-of-list file edits.
- Branch: `feat/viewer-platform-design` (canonical viewer branch).
- All Plan-2 build outputs (`docs/_index.json`, `docs/_search.json`) are **committed to git**. CI re-runs the build scripts and fails on diff.

## Frozen Plan 1 Interface (consumed by every Plan 2 page)

After `<script src="shared/manifest.js"></script>` loads, the page MUST use these properties; raw `.data`/`.docs` paths are inspection-only.

```javascript
window.Manifest.urls.data.assets_index   // absolute URL, ready to fetch
window.Manifest.urls.data.qagen_index
window.Manifest.urls.data.leaderboard
window.Manifest.urls.docs.tree_index     // → docs/_index.json
window.Manifest.urls.docs.search_index   // → docs/_search.json
window.Manifest.urls.docs.base_url       // ends with '/', join doc path against it

window.Manifest.brand                    // "BabyVision-v2"
window.Manifest.version                  // "0.1.0"
window.Manifest.domains                  // [{id, label, color, shipped}, ...]
window.ManifestReady                     // Promise resolving to the same object
window:manifest:ready                    // CustomEvent dispatched on success
```

---

## File Structure (cumulative across Plan 2)

| File | Action | Responsibility | Task |
|---|---|---|---|
| `viewer/vendor/marked.min.js` | CREATE | Markdown → HTML renderer (marked 14.1.4) | 1 |
| `viewer/vendor/prism.min.js` | CREATE | Syntax highlight core (Prism 1.30.0) | 1 |
| `viewer/vendor/prism-tomorrow.css` | CREATE | Prism dark theme (matches GitHub-dark palette) | 1 |
| `viewer/vendor/prism/prism-python.min.js` | CREATE | Prism language: python | 1 |
| `viewer/vendor/prism/prism-bash.min.js` | CREATE | Prism language: bash | 1 |
| `viewer/vendor/prism/prism-json.min.js` | CREATE | Prism language: json | 1 |
| `viewer/vendor/prism/prism-javascript.min.js` | CREATE | Prism language: javascript | 1 |
| `viewer/vendor/prism/prism-typescript.min.js` | CREATE | Prism language: typescript | 1 |
| `viewer/vendor/prism/prism-yaml.min.js` | CREATE | Prism language: yaml | 1 |
| `viewer/vendor/prism/prism-markdown.min.js` | CREATE | Prism language: markdown | 1 |
| `viewer/vendor/prism/prism-docker.min.js` | CREATE | Prism language: dockerfile | 1 |
| `viewer/vendor/lunr.min.js` | CREATE | Full-text search (lunr 2.3.9) | 1 |
| `viewer/vendor/README.md` | CREATE | Vendor inventory: name, version, source URL, license, SHA-256 | 1 |
| `viewer/_vendor_check.html` | CREATE | Smoke page verifying each vendor lib loads + a minimal call | 1 |
| `scripts/build_docs_index.py` | CREATE | Walk `docs/**/*.md` → `docs/_index.json` (tree) | 2 |
| `tests/test_docs_index.py` | CREATE | pytest: tree structure, title extraction, ordering, exclusions | 2 |
| `docs/_index.json` | CREATE (generated) | Doc tree consumed by `docs-tree.js`; committed | 2 |
| `viewer/docs.html` | CREATE | Three-column SPA shell (sidebar / content / TOC) | 3 |
| `viewer/shared/docs/docs-app.js` | CREATE | Router + render orchestrator | 3 |
| `viewer/shared/docs/docs-tree.js` | CREATE | Left sidebar tree (collapsible, persisted) | 3 |
| `viewer/shared/style.css` | MODIFY | Add `.docs-layout`, `.docs-tree`, `.docs-main`, `.docs-toc`, `.broken-link` classes | 3 |
| `viewer/shared/docs/docs-links.js` | CREATE | Internal link rewriter (`.md` → SPA, `[[wiki]]`, external) | 4 |
| `viewer/shared/docs/docs-toc.js` | CREATE | Extract H2/H3 → right TOC + IntersectionObserver scrollspy | 5 |
| `scripts/build_docs_search.py` | CREATE | Build lunr serialized index → `docs/_search.json` | 6 |
| `tests/test_docs_search.py` | CREATE | pytest: search index format, doc list, smoke query | 6 |
| `docs/_search.json` | CREATE (generated) | Pre-built lunr index; committed | 6 |
| `viewer/shared/docs/docs-search.js` | CREATE | Lunr query runner + UI (input box + result list) | 6 |
| `docs/mcp/tools/compute_volume.schema.json` | CREATE | Seed MCP tool — schema (Plan 2 ships 3 hand-authored examples) | 7 |
| `docs/mcp/tools/compute_volume.intro.md` | CREATE | Seed MCP tool — When-to-use + Example | 7 |
| `docs/mcp/tools/extract_segment.schema.json` | CREATE | Seed MCP tool — schema | 7 |
| `docs/mcp/tools/extract_segment.intro.md` | CREATE | Seed MCP tool — intro | 7 |
| `docs/mcp/tools/load_nifti.schema.json` | CREATE | Seed MCP tool — schema | 7 |
| `docs/mcp/tools/load_nifti.intro.md` | CREATE | Seed MCP tool — intro | 7 |
| `viewer/shared/docs/docs-mcp.js` | CREATE | MCP virtual-doc composer (schema + intro → markdown) | 7 |
| `.github/workflows/manifest-check.yml` | MODIFY | Add jobs: `_index.json` not-stale, `_search.json` not-stale, MCP pair check | 8 |
| `tests/test_mcp_pairs.py` | CREATE | pytest: every `*.schema.json` has matching `*.intro.md` (warn-not-fail mode) | 8 |

## Out of scope for Plan 2

These are spec items deferred to **Plan 3** or beyond — implementers must not touch them:

- `viewer/index.html` rewrite (homepage redesign — Plan 3)
- `viewer/assets.html`, `viewer/qagen.html`, `viewer/leaderboard.html` (Plan 3)
- Per-modality pages (`viewer/molstar.html`, `niivue.html`, etc.) — Plan 3 sweeps them to use `Manifest.urls.*`
- Filling in the remaining 9 domains in `manifest.json.domains[]` (Plan 3)
- Compare table content (GH issue #5)
- `build_mcp_docs.py` — auto-extraction from `sci3d_agent/` source. Plan 2 ships 3 hand-authored seed tools as canonical examples; the auto-extractor lands when `sci3d_agent/` is real in this repo (future plan).
- Mermaid diagrams, KaTeX math (deferred to v2)
- Playwright headless tests (deferred to v2 — Plan 2 uses curl + `node --check` + smoke HTML pages instead)

---

## Hard Scope Lockdown — paste this clause verbatim into every implementer dispatch

> Hard scope lockdown:
> - Repo: `/Users/sue/Research/BabyVision-v2`
> - Branch: `feat/viewer-platform-design` (already checked out; STAY HERE)
> - Files allowed to modify: ONLY the files named in the Files: block of this brief
> - You MUST NOT: switch/create/cherry-pick branches, `git push`, `git reset`, modify any other file
> - Untracked items in working tree (e.g. `examples/02_medical_spatial_gt/data/`) — DO NOT TOUCH
> - Stop and report BLOCKED if you find yourself wanting to do any of the above

---

## Task 1: Vendor third-party libraries

**Files:**
- Create: `viewer/vendor/marked.min.js`
- Create: `viewer/vendor/prism.min.js`
- Create: `viewer/vendor/prism-tomorrow.css`
- Create: `viewer/vendor/prism/prism-python.min.js`
- Create: `viewer/vendor/prism/prism-bash.min.js`
- Create: `viewer/vendor/prism/prism-json.min.js`
- Create: `viewer/vendor/prism/prism-javascript.min.js`
- Create: `viewer/vendor/prism/prism-typescript.min.js`
- Create: `viewer/vendor/prism/prism-yaml.min.js`
- Create: `viewer/vendor/prism/prism-markdown.min.js`
- Create: `viewer/vendor/prism/prism-docker.min.js`
- Create: `viewer/vendor/lunr.min.js`
- Create: `viewer/vendor/README.md`
- Create: `viewer/_vendor_check.html`

**Interfaces:**
- Consumes: nothing
- Produces: global `marked` function (UMD), global `Prism` object, global `lunr` function; CSS classes `.token.*` from prism-tomorrow theme. All available via simple `<script src>` includes — no module system.

- [ ] **Step 1: Create the vendor directory**

```bash
cd /Users/sue/Research/BabyVision-v2
mkdir -p viewer/vendor/prism
```

- [ ] **Step 2: Download marked@14.1.4**

```bash
curl -fsSL -o viewer/vendor/marked.min.js \
  https://cdn.jsdelivr.net/npm/marked@14.1.4/lib/marked.umd.min.js
ls -la viewer/vendor/marked.min.js
```
Expected: file size ~35–45 KB.

- [ ] **Step 3: Download Prism core + components**

```bash
cd /Users/sue/Research/BabyVision-v2

# Core
curl -fsSL -o viewer/vendor/prism.min.js \
  https://cdn.jsdelivr.net/npm/prismjs@1.30.0/prism.min.js
curl -fsSL -o viewer/vendor/prism-tomorrow.css \
  https://cdn.jsdelivr.net/npm/prismjs@1.30.0/themes/prism-tomorrow.min.css

# Language components (8)
for lang in python bash json javascript typescript yaml markdown docker; do
  curl -fsSL -o "viewer/vendor/prism/prism-${lang}.min.js" \
    "https://cdn.jsdelivr.net/npm/prismjs@1.30.0/components/prism-${lang}.min.js"
done

ls -la viewer/vendor/prism.min.js viewer/vendor/prism-tomorrow.css viewer/vendor/prism/
```
Expected: prism.min.js ~10 KB; prism-tomorrow.css ~1.5 KB; each language file 1–5 KB.

- [ ] **Step 4: Download lunr@2.3.9**

```bash
curl -fsSL -o viewer/vendor/lunr.min.js \
  https://cdn.jsdelivr.net/npm/lunr@2.3.9/lunr.min.js
ls -la viewer/vendor/lunr.min.js
```
Expected: ~30 KB.

- [ ] **Step 5: Syntax-check every JS file**

```bash
cd /Users/sue/Research/BabyVision-v2
for f in viewer/vendor/marked.min.js viewer/vendor/prism.min.js viewer/vendor/lunr.min.js viewer/vendor/prism/*.min.js; do
  node --check "$f" && echo "OK: $f"
done
```
Expected: every line prints `OK: <path>`.

- [ ] **Step 6: Record vendor inventory in `viewer/vendor/README.md`**

```markdown
# Vendored libraries

Each file is committed verbatim; no build step is required. The viewer must
never load these from a CDN at runtime.

| File | Package | Version | Source | License | SHA-256 |
|---|---|---|---|---|---|
| `marked.min.js` | marked | 14.1.4 | https://www.npmjs.com/package/marked | MIT | (run `shasum -a 256 viewer/vendor/marked.min.js` and paste) |
| `prism.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | (run `shasum -a 256 viewer/vendor/prism.min.js` and paste) |
| `prism-tomorrow.css` | prismjs | 1.30.0 | https://prismjs.com/themes/prism-tomorrow.css | MIT | (run `shasum -a 256 viewer/vendor/prism-tomorrow.css` and paste) |
| `prism/prism-<lang>.min.js` × 8 | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | (per-file SHAs; one row each or one combined row noting "8 files") |
| `lunr.min.js` | lunr | 2.3.9 | https://www.npmjs.com/package/lunr | MIT | (run `shasum -a 256 viewer/vendor/lunr.min.js` and paste) |

## Upgrade procedure

1. Update the version in this README's table and in the `curl` commands of Plan 2 Task 1.
2. Run the curl downloads.
3. Re-run `viewer/_vendor_check.html` end-to-end (see Step 8).
4. If breaking changes: open an issue, do not silently upgrade.
```

Replace the parenthetical SHA placeholders with the actual hashes:

```bash
cd /Users/sue/Research/BabyVision-v2
shasum -a 256 viewer/vendor/marked.min.js viewer/vendor/prism.min.js \
  viewer/vendor/prism-tomorrow.css viewer/vendor/lunr.min.js \
  viewer/vendor/prism/*.min.js
```
Paste each hash into the matching row.

- [ ] **Step 7: Write the vendor smoke page**

Create `viewer/_vendor_check.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Vendor check — BabyVision-v2</title>
<link rel="stylesheet" href="vendor/prism-tomorrow.css">
<style>
  body { background: #0d1117; color: #c9d1d9; font: 14px/1.5 -apple-system, system-ui, sans-serif; padding: 24px; max-width: 760px; }
  h1 { font-size: 18px; margin: 0 0 16px; }
  h2 { font-size: 14px; margin: 24px 0 8px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; }
  .ok   { color: #56d364; }
  .fail { color: #f85149; }
  pre  { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px; overflow-x: auto; }
  code { font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
  ul.checks { list-style: none; padding: 0; }
  ul.checks li { padding: 3px 0; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
</style>
</head>
<body>
<h1>Vendor check</h1>

<h2>Status</h2>
<ul class="checks" id="checks"></ul>

<h2>Marked render demo</h2>
<div id="marked-out"></div>

<h2>Prism highlight demo</h2>
<pre><code class="language-python" id="prism-out">def hello():
    return "world"</code></pre>

<h2>Lunr search demo</h2>
<div id="lunr-out"></div>

<script src="vendor/marked.min.js"></script>
<script src="vendor/prism.min.js"></script>
<script src="vendor/prism/prism-python.min.js"></script>
<script src="vendor/prism/prism-bash.min.js"></script>
<script src="vendor/prism/prism-json.min.js"></script>
<script src="vendor/prism/prism-javascript.min.js"></script>
<script src="vendor/prism/prism-typescript.min.js"></script>
<script src="vendor/prism/prism-yaml.min.js"></script>
<script src="vendor/prism/prism-markdown.min.js"></script>
<script src="vendor/prism/prism-docker.min.js"></script>
<script src="vendor/lunr.min.js"></script>
<script>
  const list = document.getElementById('checks');
  function line(ok, label, detail) {
    const li = document.createElement('li');
    li.innerHTML = '<span class="' + (ok ? 'ok' : 'fail') + '">' + (ok ? '✓' : '✗') + '</span> ' + label + (detail ? ' — ' + detail : '');
    list.appendChild(li);
  }
  // 1. marked
  try {
    const html = marked.parse('# Hi\n\n**bold** and `code`.');
    document.getElementById('marked-out').innerHTML = html;
    line(/<h1>/.test(html) && /<strong>/.test(html), 'marked.parse', 'rendered h1 + strong');
  } catch (e) { line(false, 'marked.parse', e.message); }
  // 2. Prism + languages
  try {
    Prism.highlightAllUnder(document.body);
    const out = document.getElementById('prism-out').innerHTML;
    line(/token/.test(out), 'Prism.highlightAllUnder', 'token spans applied');
    const langs = ['python', 'bash', 'json', 'javascript', 'typescript', 'yaml', 'markdown', 'docker'];
    for (const l of langs) {
      const has = Prism.languages[l] !== undefined;
      line(has, 'Prism.languages.' + l, has ? 'loaded' : 'MISSING');
    }
  } catch (e) { line(false, 'Prism', e.message); }
  // 3. lunr
  try {
    const idx = lunr(function () {
      this.ref('id'); this.field('text');
      this.add({ id: 1, text: 'manifest layer' });
      this.add({ id: 2, text: 'docs search index' });
    });
    const hits = idx.search('manifest');
    document.getElementById('lunr-out').textContent = JSON.stringify(hits);
    line(hits.length === 1 && hits[0].ref === '1', 'lunr search', 'expected 1 hit on id=1');
  } catch (e) { line(false, 'lunr', e.message); }
</script>
</body>
</html>
```

- [ ] **Step 8: Verify the smoke page via server + curl**

```bash
cd /Users/sue/Research/BabyVision-v2
# Use port 8765 — 8000 is taken by VS Code
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
echo "vendor-check 200:"
curl -sI http://localhost:8765/viewer/_vendor_check.html | head -1
echo "marked 200:"
curl -sI http://localhost:8765/viewer/vendor/marked.min.js | head -1
echo "lunr 200:"
curl -sI http://localhost:8765/viewer/vendor/lunr.min.js | head -1
echo "prism-python 200:"
curl -sI http://localhost:8765/viewer/vendor/prism/prism-python.min.js | head -1
kill $SERVER_PID 2>/dev/null
```
Expected: all four lines show `HTTP/1.0 200 OK`.

- [ ] **Step 9: Commit**

```bash
cd /Users/sue/Research/BabyVision-v2
git add viewer/vendor/ viewer/_vendor_check.html
git status
git commit -m "feat(viewer): vendor marked@14.1.4, prismjs@1.30.0, lunr@2.3.9"
```

Verify exactly the vendor + smoke page files are staged (no other touches).

---

## Task 2: Docs tree builder + `docs/_index.json`

**Files:**
- Create: `scripts/build_docs_index.py`
- Create: `tests/test_docs_index.py`
- Create: `docs/_index.json` (committed; output of the build script)

**Interfaces:**
- Consumes: filesystem walk over `docs/**/*.md` plus the in-script `TOP_LEVEL_ORDER` constant.
- Produces:
  - Pure functions exported by `scripts/build_docs_index.py`:
    - `extract_title(md_text: str) -> str` — returns the first `# H1` text, or the path basename if missing.
    - `build_tree(repo_root: pathlib.Path) -> dict` — returns the `{generated_at, tree: [...]}` structure.
    - `main(argv: list[str] | None = None) -> int` — CLI: writes `docs/_index.json` if content changed; returns 0 on success, 1 on I/O failure. With `--check`, exits 1 if regenerated output would differ from the committed file (CI uses this).
  - `docs/_index.json` (committed): `{generated_at, tree: [Section, ...]}`. Each `Section = {title, children: [DocEntry, ...]}`. `DocEntry = {title, path}` for ordinary docs, or `{title, path, kind: "mcp-tool", domain?}` for MCP tools.

- [ ] **Step 1: Write the failing test**

Create `tests/test_docs_index.py`:

```python
"""Tests for scripts/build_docs_index.py — the docs tree builder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.build_docs_index import extract_title, build_tree, main


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
    # Benchmark, Platform, MCP tools, Partners — canonical order even though
    # the MCP tools section is empty (still emitted as a section shell).
    assert titles == ["Benchmark", "Platform", "MCP tools", "Partners"]


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
    (docs / "quickstart.md").write_text("# Quickstart\n")
    (docs / "architecture.md").write_text("# Architecture\n")
    (docs / "api-reference.md").write_text("# API reference\n")
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
```

- [ ] **Step 2: Run the failing test (RED)**

```bash
cd /Users/sue/Research/BabyVision-v2
pytest tests/test_docs_index.py -v
```
Expected: `ModuleNotFoundError: No module named 'scripts.build_docs_index'` or similar. Record the relevant lines in your report.

- [ ] **Step 3: Write the builder**

Create `scripts/build_docs_index.py`:

```python
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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR_NAME = "docs"

# Canonical top-level ordering for the sidebar. New top-level sections are
# added here only.
TOP_LEVEL_ORDER = ["benchmark", "platform", "mcp", "partners"]
SECTION_LABEL = {
    "benchmark": "Benchmark",
    "platform":  "Platform",
    "mcp":       "MCP tools",
    "partners":  "Partners",
}

# Files that look like docs but are partials or generated; exclude from the
# tree.
EXCLUDED_BASENAMES = {"_index.json", "_search.json"}
EXCLUDED_SUFFIXES  = (".intro.md",)  # MCP tool intros are partials, not standalone docs

H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def extract_title(md_text: str) -> str:
    """Return the first H1 text from a markdown document.

    Strips surrounding whitespace from both the heading line and the title.
    Returns an empty string if no H1 is found.
    """
    m = H1_PATTERN.search(md_text)
    return m.group(1).strip() if m else ""


def _iter_markdown_files(docs_dir: Path):
    """Yield (relative_path_no_suffix, absolute_path) for every doc-like .md
    file under docs_dir, skipping partials and generated artifacts."""
    for p in sorted(docs_dir.rglob("*.md")):
        if any(p.name.endswith(suf) for suf in EXCLUDED_SUFFIXES):
            continue
        rel = p.relative_to(docs_dir).with_suffix("")
        yield rel.as_posix(), p


def _iter_mcp_tools(docs_dir: Path):
    """Yield (name, schema_path) for every MCP tool *.schema.json file."""
    mcp_tools_dir = docs_dir / "mcp" / "tools"
    if not mcp_tools_dir.is_dir():
        return
    for p in sorted(mcp_tools_dir.glob("*.schema.json")):
        name = p.name[:-len(".schema.json")]
        yield name, p


def _section_for_path(rel_path: str) -> str:
    """Return the section key for a doc path. Paths under docs/mcp/tools/ are
    skipped here (handled by _iter_mcp_tools); other top-level dirs map to
    themselves; anything else falls through to the partners section."""
    head = rel_path.split("/", 1)[0]
    if head == "mcp":
        return "mcp"
    if head in TOP_LEVEL_ORDER:
        return head
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
        for rel, abspath in _iter_markdown_files(docs_dir):
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

    # Stable alphabetical sort within each section.
    for key in sections:
        sections[key].sort(key=lambda e: e["path"])

    tree = [
        {"title": SECTION_LABEL[key], "children": sections[key]}
        for key in TOP_LEVEL_ORDER
    ]
    return {
        "generated_at": "2026-06-19T00:00:00Z",  # stable timestamp; CI checks bit-for-bit equality
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
```

- [ ] **Step 4: Run the tests to verify they pass (GREEN)**

```bash
cd /Users/sue/Research/BabyVision-v2
pytest tests/test_docs_index.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 5: Generate `docs/_index.json` against the real repo**

```bash
cd /Users/sue/Research/BabyVision-v2
python scripts/build_docs_index.py
cat docs/_index.json | python -m json.tool | head -40
```
Expected: a JSON document with sections `Benchmark`, `Platform`, `MCP tools` (empty until Task 7), `Partners`. Each non-empty section lists its existing markdown files with extracted titles.

- [ ] **Step 6: Run `--check` to confirm in-sync**

```bash
python scripts/build_docs_index.py --check
```
Expected: `OK: ...docs/_index.json is up-to-date.` exit 0.

- [ ] **Step 7: Commit**

```bash
git add scripts/build_docs_index.py tests/test_docs_index.py docs/_index.json
git status
git commit -m "feat(docs): build_docs_index.py + initial _index.json"
```

---

## Task 3: Docs SPA MVP — shell + router + tree + markdown render

**Files:**
- Create: `viewer/docs.html`
- Create: `viewer/shared/docs/docs-app.js`
- Create: `viewer/shared/docs/docs-tree.js`
- Modify: `viewer/shared/style.css` (add docs SPA classes; ≤120 new lines appended)

**Interfaces:**
- Consumes:
  - `window.Manifest.urls.docs.tree_index` (Plan 1 contract — fetched as `_index.json`)
  - `window.Manifest.urls.docs.base_url` (joins to `<path>.md` for fetching docs)
  - `marked.parse(md_text)` (Task 1 — global)
  - `Prism.highlightAllUnder(rootEl)` (Task 1 — global)
- Produces:
  - `window.DocsApp` namespace (intentionally global; no module system):
    - `DocsApp.navigate(docPath: string, anchor?: string) -> void` — pushes URL and triggers render
    - `DocsApp.render(docPath: string) -> Promise<void>` — fetches, parses, injects into main column. Also emits `docs:rendered` CustomEvent with `{detail: {docPath, headings: [{level, id, text}, ...]}}` on success. (Later tasks listen for this.)
    - `DocsApp.parseUrlDoc() -> {doc: string, anchor: string | null, q: string | null}` — read `window.location` into route state.
  - `window.DocsTree` namespace:
    - `DocsTree.mount(rootEl: HTMLElement, index: object) -> void` — renders the tree.
    - `DocsTree.setActive(docPath: string) -> void` — highlights the active leaf.

- [ ] **Step 1: Write `viewer/docs.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Docs — BabyVision-v2</title>
<link rel="stylesheet" href="shared/style.css">
<link rel="stylesheet" href="vendor/prism-tomorrow.css">
</head>
<body class="docs-body">
<header class="topbar">
  <div>
    <a href="index.html" class="home-link" style="color:inherit;text-decoration:none">
      <strong id="brand">BabyVision-v2</strong>
    </a>
    <span style="color:var(--fg-dim);margin-left:8px;font-size:12px">/ Docs</span>
  </div>
  <div class="topbar-right">
    <input type="search" id="docs-search-input" placeholder="Search docs (press /)" aria-label="Search docs"
           autocomplete="off" spellcheck="false">
    <span class="meta" id="docs-meta">loading…</span>
  </div>
</header>

<main class="docs-layout">
  <aside class="docs-sidebar" aria-label="Document tree">
    <nav id="docs-tree" class="docs-tree" aria-label="Sections"></nav>
  </aside>

  <section class="docs-main" id="docs-main" aria-live="polite">
    <article id="docs-content"><p class="empty">Loading…</p></article>
  </section>

  <aside class="docs-toc-wrap" aria-label="On this page">
    <div class="docs-toc-label">On this page</div>
    <nav id="docs-toc" class="docs-toc"></nav>
  </aside>
</main>

<script src="shared/manifest.js"></script>
<script src="vendor/marked.min.js"></script>
<script src="vendor/prism.min.js"></script>
<script src="vendor/prism/prism-python.min.js"></script>
<script src="vendor/prism/prism-bash.min.js"></script>
<script src="vendor/prism/prism-json.min.js"></script>
<script src="vendor/prism/prism-javascript.min.js"></script>
<script src="vendor/prism/prism-typescript.min.js"></script>
<script src="vendor/prism/prism-yaml.min.js"></script>
<script src="vendor/prism/prism-markdown.min.js"></script>
<script src="vendor/prism/prism-docker.min.js"></script>
<script src="shared/docs/docs-tree.js"></script>
<script src="shared/docs/docs-app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `viewer/shared/docs/docs-tree.js`**

```javascript
// viewer/shared/docs/docs-tree.js — left sidebar tree component.
//
// Public API on window.DocsTree:
//   DocsTree.mount(rootEl, indexJson)   — render the tree
//   DocsTree.setActive(docPath)         — highlight the leaf matching docPath
//
// Persisted state: per-section open/closed in localStorage under key
// "docs-tree:open".

(function () {
  'use strict';

  const STORAGE_KEY = 'docs-tree:open';

  function readOpenState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? new Set(JSON.parse(raw)) : null;
    } catch (e) {
      return null;
    }
  }

  function writeOpenState(setOfTitles) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...setOfTitles]));
    } catch (e) { /* quota or privacy-mode — ignore */ }
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const k of Object.keys(attrs)) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
    if (children) for (const c of children) node.appendChild(c);
    return node;
  }

  function buildSection(section, openSet) {
    const details = el('details', { class: 'docs-tree-section' });
    if (openSet === null || openSet.has(section.title)) {
      details.open = true;
    }
    details.addEventListener('toggle', () => {
      const cur = readOpenState() || new Set();
      if (details.open) cur.add(section.title);
      else cur.delete(section.title);
      writeOpenState(cur);
    });

    const summary = el('summary', { class: 'docs-tree-section-summary' });
    summary.textContent = section.title;
    if (section.children && section.children.length) {
      const count = el('span', { class: 'docs-tree-count' });
      count.textContent = section.children.length;
      summary.appendChild(count);
    }
    details.appendChild(summary);

    const list = el('ul', { class: 'docs-tree-list' });
    for (const child of section.children || []) {
      const li = el('li', { class: 'docs-tree-leaf' });
      const a  = el('a',  { class: 'docs-tree-link', href: '?doc=' + encodeURIComponent(child.path) });
      a.dataset.docPath = child.path;
      a.textContent = child.title;
      if (child.kind === 'mcp-tool') {
        a.classList.add('is-mcp-tool');
      }
      li.appendChild(a);
      list.appendChild(li);
    }
    details.appendChild(list);
    return details;
  }

  function mount(rootEl, indexJson) {
    rootEl.innerHTML = '';
    const openSet = readOpenState();
    for (const section of indexJson.tree || []) {
      rootEl.appendChild(buildSection(section, openSet));
    }
  }

  function setActive(docPath) {
    const root = document.getElementById('docs-tree');
    if (!root) return;
    const all = root.querySelectorAll('a.docs-tree-link');
    all.forEach((a) => a.classList.remove('is-active'));
    const match = root.querySelector('a.docs-tree-link[data-doc-path="' + cssEscape(docPath) + '"]');
    if (match) {
      match.classList.add('is-active');
      // Ensure the containing <details> is open so the active leaf is visible.
      const section = match.closest('details');
      if (section && !section.open) section.open = true;
    }
  }

  // Minimal CSS.escape polyfill: we control the input format so a tight
  // allowlist replacement is enough.
  function cssEscape(s) {
    return s.replace(/[^a-zA-Z0-9_\-/]/g, '\\$&');
  }

  window.DocsTree = { mount: mount, setActive: setActive };
})();
```

- [ ] **Step 3: Write `viewer/shared/docs/docs-app.js`**

```javascript
// viewer/shared/docs/docs-app.js — router + markdown render orchestrator.
//
// Public API on window.DocsApp:
//   DocsApp.navigate(docPath, anchor)   — pushState + render
//   DocsApp.render(docPath) -> Promise  — fetch .md, render to main column,
//                                          emit docs:rendered CustomEvent
//   DocsApp.parseUrlDoc() -> {doc, anchor, q}
//
// Default doc: 'platform/quickstart' when ?doc= is absent.

(function () {
  'use strict';

  const DEFAULT_DOC = 'platform/quickstart';

  function $(id) { return document.getElementById(id); }

  function parseUrlDoc() {
    const params = new URLSearchParams(window.location.search);
    const q   = params.get('q');
    const doc = params.get('doc') || DEFAULT_DOC;
    const anchor = window.location.hash ? window.location.hash.slice(1) : null;
    return { doc: doc, anchor: anchor, q: q };
  }

  function slug(text) {
    return text.toLowerCase()
      .replace(/[^\w\s-]/g, '')
      .trim()
      .replace(/\s+/g, '-');
  }

  function configureMarked() {
    // Walker after marked.parse: ensure every h2/h3 has a stable id.
    // We rely on marked's default renderer for headings (which doesn't add
    // ids), then assign one in the DOM afterward.
  }

  function applyHeadingIds(root) {
    const headings = root.querySelectorAll('h2, h3');
    const seen = new Set();
    const list = [];
    headings.forEach((h) => {
      let base = h.id || slug(h.textContent);
      let candidate = base;
      let n = 1;
      while (seen.has(candidate)) { n++; candidate = base + '-' + n; }
      seen.add(candidate);
      h.id = candidate;
      list.push({ level: h.tagName === 'H2' ? 2 : 3, id: candidate, text: h.textContent });
    });
    return list;
  }

  function showError(message) {
    const content = $('docs-content');
    content.innerHTML =
      '<div class="docs-404"><h2>Document not found</h2>' +
      '<p>' + escapeHtml(message) + '</p>' +
      '<p><a href="?doc=platform/quickstart">Back to Quickstart</a></p></div>';
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function fetchDoc(docPath) {
    const base = window.Manifest.urls.docs.base_url;     // e.g. ".../docs/"
    const url  = new URL(docPath + '.md', base).href;
    const resp = await fetch(url, { cache: 'no-cache' });
    if (!resp.ok) {
      throw new Error('HTTP ' + resp.status + ' for ' + url);
    }
    return resp.text();
  }

  async function render(docPath) {
    const content = $('docs-content');
    content.innerHTML = '<p class="empty">Loading…</p>';
    let mdText;
    try {
      mdText = await fetchDoc(docPath);
    } catch (e) {
      showError(e.message);
      return;
    }
    // marked produces standard HTML. We inject as innerHTML and then
    // post-process via DOM walks.
    const html = marked.parse(mdText, {
      gfm: true,
      breaks: false,
    });
    content.innerHTML = html;
    const headings = applyHeadingIds(content);
    if (window.Prism && Prism.highlightAllUnder) {
      Prism.highlightAllUnder(content);
    }
    if (window.DocsTree && DocsTree.setActive) DocsTree.setActive(docPath);
    document.title = content.querySelector('h1')
      ? content.querySelector('h1').textContent + ' — Docs — ' + (window.Manifest.brand || 'BV2')
      : 'Docs — ' + (window.Manifest.brand || 'BV2');
    // Fire event so later tasks (links, toc, search) can hook in.
    window.dispatchEvent(new CustomEvent('docs:rendered', {
      detail: { docPath: docPath, headings: headings, root: content },
    }));
  }

  function navigate(docPath, anchor) {
    const url = '?doc=' + encodeURIComponent(docPath) + (anchor ? ('#' + anchor) : '');
    history.pushState({ doc: docPath, anchor: anchor || null }, '', url);
    render(docPath).then(() => {
      if (anchor) {
        const el = document.getElementById(anchor);
        if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
      } else {
        window.scrollTo(0, 0);
      }
    });
  }

  // popstate (browser back/forward) replays the route.
  window.addEventListener('popstate', () => {
    const route = parseUrlDoc();
    render(route.doc).then(() => {
      if (route.anchor) {
        const el = document.getElementById(route.anchor);
        if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
      }
    });
  });

  // Sidebar clicks → intercept and navigate via History API.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.docs-tree-link');
    if (!a) return;
    e.preventDefault();
    const docPath = a.dataset.docPath;
    if (docPath) navigate(docPath, null);
  });

  // Bootstrap.
  window.ManifestReady.then(async (m) => {
    // Update meta line + brand.
    const meta = $('docs-meta');
    if (meta) meta.textContent = m.brand + ' · v' + m.version;
    const brand = $('brand');
    if (brand) brand.textContent = m.brand;

    // Fetch tree index, mount sidebar.
    let indexJson;
    try {
      const resp = await fetch(m.urls.docs.tree_index, { cache: 'no-cache' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      indexJson = await resp.json();
    } catch (e) {
      const tree = $('docs-tree');
      if (tree) tree.innerHTML = '<p class="empty">Tree index unreachable: ' + escapeHtml(e.message) + '</p>';
      // Continue without sidebar — main render still works.
      indexJson = { tree: [] };
    }
    DocsTree.mount($('docs-tree'), indexJson);

    // Initial render.
    const route = parseUrlDoc();
    await render(route.doc);
    if (route.anchor) {
      const el = document.getElementById(route.anchor);
      if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
    }
  });

  window.DocsApp = {
    navigate: navigate,
    render:   render,
    parseUrlDoc: parseUrlDoc,
  };
})();
```

- [ ] **Step 4: Append docs-SPA styles to `viewer/shared/style.css`**

Append (do NOT replace) at the end of `viewer/shared/style.css`:

```css

/* ── Docs SPA (Plan 2) ──────────────────────────────────────────────── */

body.docs-body { display: flex; flex-direction: column; min-height: 100vh; }

.docs-layout {
  display: grid;
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr) minmax(0, 220px);
  gap: 24px;
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
  flex: 1 1 auto;
}

.docs-sidebar {
  position: sticky;
  top: 64px;
  align-self: start;
  max-height: calc(100vh - 80px);
  overflow-y: auto;
  font-size: 13px;
}

.docs-tree-section { margin-bottom: 8px; }
.docs-tree-section[open] > summary { color: var(--fg); }
.docs-tree-section-summary {
  cursor: pointer;
  list-style: none;
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-dim);
  padding: 4px 0;
  user-select: none;
}
.docs-tree-section-summary::-webkit-details-marker { display: none; }
.docs-tree-section-summary::before {
  content: '▸';
  display: inline-block;
  width: 14px;
  font-size: 10px;
  transition: transform 100ms;
}
.docs-tree-section[open] > summary::before { transform: rotate(90deg); }
.docs-tree-count {
  margin-left: 6px;
  font-size: 10px;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 0 6px;
  color: var(--fg-dim);
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
}
.docs-tree-list { list-style: none; margin: 4px 0 12px; padding: 0 0 0 18px; }
.docs-tree-leaf { padding: 2px 0; }
.docs-tree-link {
  display: block;
  padding: 3px 8px;
  border-radius: 4px;
  color: var(--fg-dim);
  text-decoration: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
.docs-tree-link:hover { color: var(--fg); background: var(--bg2); text-decoration: none; }
.docs-tree-link.is-active {
  color: var(--accent);
  background: var(--bg2);
  border-left: 2px solid var(--accent);
  padding-left: 6px;
}
.docs-tree-link.is-mcp-tool::before { content: '⚙ '; opacity: 0.6; }

.docs-main {
  min-width: 0;  /* allow grid child to shrink */
  font-size: 14px;
  line-height: 1.65;
}
.docs-main h1 { font-size: 28px; margin: 0 0 16px; }
.docs-main h2 { font-size: 20px; margin: 32px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
.docs-main h3 { font-size: 16px; margin: 24px 0 10px; }
.docs-main p, .docs-main ul, .docs-main ol { margin: 0 0 14px; }
.docs-main ul, .docs-main ol { padding-left: 24px; }
.docs-main li { margin: 4px 0; }
.docs-main blockquote {
  margin: 12px 0; padding: 8px 14px;
  border-left: 3px solid var(--fg-dim);
  background: rgba(139, 148, 158, 0.06);
  color: var(--fg-dim);
}
.docs-main code {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 1px 6px;
  font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
}
.docs-main pre {
  background: #1e1e1e;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px 14px;
  overflow-x: auto;
  font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
}
.docs-main pre code { background: transparent; border: 0; padding: 0; font-size: inherit; }
.docs-main table {
  border-collapse: collapse;
  margin: 12px 0 16px;
}
.docs-main th, .docs-main td {
  border: 1px solid var(--border);
  padding: 6px 12px;
  text-align: left;
}
.docs-main th { background: var(--bg2); color: var(--fg-dim); font-weight: 600; }
.docs-main a { color: var(--accent); }

.docs-toc-wrap {
  position: sticky;
  top: 64px;
  align-self: start;
  max-height: calc(100vh - 80px);
  overflow-y: auto;
  font-size: 12px;
}
.docs-toc-label {
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  font-size: 11px;
  font-weight: 600;
  margin-bottom: 8px;
}
.docs-toc a {
  display: block;
  padding: 3px 0 3px 8px;
  color: var(--fg-dim);
  text-decoration: none;
  border-left: 2px solid transparent;
}
.docs-toc a.toc-l3 { padding-left: 20px; font-size: 11px; }
.docs-toc a:hover { color: var(--fg); }
.docs-toc a.is-active { color: var(--accent); border-left-color: var(--accent); }

.docs-404 { padding: 16px; background: rgba(248, 81, 73, 0.06); border-left: 3px solid var(--domain-neg); border-radius: 0 4px 4px 0; }
.broken-link { color: var(--domain-neg); text-decoration: line-through; cursor: help; }
.docs-mcp-banner {
  background: rgba(255, 209, 102, 0.08);
  border-left: 3px solid var(--domain-photo);
  padding: 10px 14px; margin: 0 0 16px; border-radius: 0 4px 4px 0;
  font-size: 12px;
}

#docs-search-input {
  background: var(--bg2);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 10px;
  font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
  width: 220px;
  margin-right: 12px;
}
#docs-search-input:focus { outline: 1px solid var(--accent); border-color: var(--accent); }

.docs-search-results {
  position: absolute;
  top: 48px;
  right: 24px;
  width: 360px;
  max-height: 60vh;
  overflow-y: auto;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  padding: 8px;
  z-index: 50;
}
.docs-search-results.is-empty { color: var(--fg-dim); padding: 16px; text-align: center; font-style: italic; }
.docs-search-result {
  display: block;
  padding: 6px 8px;
  border-radius: 4px;
  color: var(--fg);
  text-decoration: none;
  font-size: 12px;
}
.docs-search-result:hover { background: var(--bg); }
.docs-search-result .path { color: var(--fg-dim); font-family: ui-monospace, Menlo, monospace; font-size: 10px; }
.docs-search-result mark { background: var(--accent); color: var(--bg); padding: 0 2px; border-radius: 2px; }

@media (max-width: 1100px) {
  .docs-layout { grid-template-columns: minmax(0, 1fr); }
  .docs-sidebar, .docs-toc-wrap { display: none; }
}
```

- [ ] **Step 5: Syntax-check JS + HTML parse + smoke-fetch**

```bash
cd /Users/sue/Research/BabyVision-v2
node --check viewer/shared/docs/docs-app.js
node --check viewer/shared/docs/docs-tree.js
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('viewer/docs.html').read()); print('HTML OK')"

# Smoke: serve + curl
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
echo "docs.html 200:"
curl -sI http://localhost:8765/viewer/docs.html | head -1
echo "docs-app.js 200:"
curl -sI http://localhost:8765/viewer/shared/docs/docs-app.js | head -1
echo "docs-tree.js 200:"
curl -sI http://localhost:8765/viewer/shared/docs/docs-tree.js | head -1
echo "_index.json fetchable:"
curl -s http://localhost:8765/docs/_index.json | python3 -m json.tool | head -10
echo "platform/quickstart.md fetchable:"
curl -sI http://localhost:8765/docs/platform/quickstart.md | head -1
kill $SERVER_PID 2>/dev/null
```
Expected: all four `200 OK`, `_index.json` parses, quickstart.md exists.

- [ ] **Step 6: Manual eyeball (record in report, controller verifies)**

Document this as a controller-side verification step in your report. The implementer cannot run a real browser. Note in the report: "Open http://localhost:8765/viewer/docs.html — expected to see three-column layout, left sidebar populated with Benchmark/Platform/MCP tools/Partners sections, main column rendering platform/quickstart.md with H1 and content."

- [ ] **Step 7: Commit**

```bash
cd /Users/sue/Research/BabyVision-v2
git add viewer/docs.html viewer/shared/docs/ viewer/shared/style.css
git status
git commit -m "feat(viewer): docs SPA MVP — shell, router, tree, markdown render"
```

---

## Task 4: Internal link rewriting

**Files:**
- Create: `viewer/shared/docs/docs-links.js`
- Modify: `viewer/docs.html` (add one `<script src>` line for docs-links.js immediately AFTER `docs-app.js`)

**Interfaces:**
- Consumes:
  - `docs:rendered` CustomEvent from `docs-app.js`
  - `window.DocsApp.navigate(docPath, anchor)`
  - `window.Manifest.urls.docs.base_url`
  - The index loaded into memory by `docs-app.js` (we'll need to expose it). Update docs-app.js minimally so the tree index is accessible at `window.DocsApp._index` (underscore signals internal).
- Produces:
  - Side effect: rewrites anchor tags inside `#docs-content` after each render. No new public API.

- [ ] **Step 1: Minimally extend docs-app.js to expose the index**

In `viewer/shared/docs/docs-app.js`, find the bootstrap block:

```javascript
    DocsTree.mount($('docs-tree'), indexJson);
```

Insert immediately ABOVE this line:

```javascript
    DocsApp._index = indexJson;
```

Also update the `}` block: `window.DocsApp = { navigate: navigate, render: render, parseUrlDoc: parseUrlDoc };` — keep the same surface; the `_index` field is set during bootstrap, after the namespace is created, so no change needed there. The above one-line insert is the only edit.

- [ ] **Step 2: Write `viewer/shared/docs/docs-links.js`**

```javascript
// viewer/shared/docs/docs-links.js — rewrite internal links after each render.
//
// Rules:
//   [t](other.md)                  -> SPA navigation, relative to current doc's dir
//   [t](../platform/quickstart.md) -> SPA navigation, cross-directory
//   [t](#anchor)                   -> leave alone (in-page anchor)
//   [t](https://...)               -> target="_blank" rel="noopener"
//   [t](/spec/...)                 -> leave alone (absolute repo path)
//   [[wiki-link]] -> looks up by title in DocsApp._index; if hit, replace with
//                    SPA <a>; if miss, render as <span class="broken-link"> with
//                    tooltip.
//
// We hook into the docs:rendered event and walk the freshly-rendered DOM.

(function () {
  'use strict';

  function isMdSpaLink(href) {
    if (!href) return false;
    if (/^[a-z]+:\/\//i.test(href)) return false; // absolute http(s)
    if (href.startsWith('/')) return false;       // absolute repo path
    if (href.startsWith('#')) return false;       // in-page
    if (href.startsWith('mailto:')) return false;
    return href.endsWith('.md') || href.includes('.md#');
  }

  function resolveRelative(currentDocPath, mdHref) {
    // currentDocPath like "platform/quickstart"; mdHref like "evaluation-protocol.md"
    // or "../platform/quickstart.md#step-1".
    const [pathPart, anchor] = mdHref.split('#');
    const currentDir = currentDocPath.includes('/')
      ? currentDocPath.slice(0, currentDocPath.lastIndexOf('/'))
      : '';
    // Normalize via URL semantics against a synthetic base.
    const base = 'docs:///' + currentDir + (currentDir ? '/' : '');
    const resolved = new URL(pathPart, base).pathname.replace(/^\/+/, '');
    const docPath = resolved.replace(/\.md$/, '');
    return { docPath: docPath, anchor: anchor || null };
  }

  function rewriteAnchor(a, currentDocPath) {
    const raw = a.getAttribute('href') || '';
    // 1. External (http/https): open in new tab.
    if (/^https?:\/\//i.test(raw)) {
      a.setAttribute('target', '_blank');
      a.setAttribute('rel', 'noopener');
      return;
    }
    // 2. In-page anchor: leave alone.
    if (raw.startsWith('#')) return;
    // 3. Absolute repo path: leave alone.
    if (raw.startsWith('/')) return;
    // 4. Cross-doc .md link: rewrite to SPA route.
    if (isMdSpaLink(raw)) {
      const target = resolveRelative(currentDocPath, raw);
      const spaHref = '?doc=' + encodeURIComponent(target.docPath)
        + (target.anchor ? '#' + target.anchor : '');
      a.setAttribute('href', spaHref);
      a.dataset.spa = 'doc';
      a.dataset.docPath = target.docPath;
      if (target.anchor) a.dataset.anchor = target.anchor;
    }
    // 5. Other relative links: leave alone (e.g. images, raw assets).
  }

  // [[wiki-link]] processing: after marked.parse the DOM contains literal
  // text "[[name]]" (marked doesn't treat double-brackets specially). Walk
  // text nodes, replace with anchor or span as appropriate.
  const WIKI_PATTERN = /\[\[([^\]]+)\]\]/g;

  function indexByTitle(indexJson) {
    const map = new Map();
    for (const section of (indexJson && indexJson.tree) || []) {
      for (const child of section.children || []) {
        if (!map.has(child.title)) map.set(child.title, child);
      }
    }
    return map;
  }

  function walkText(root, byTitle) {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) =>
        n.parentNode && /CODE|PRE|A|SCRIPT|STYLE/.test(n.parentNode.tagName)
          ? NodeFilter.FILTER_REJECT
          : NodeFilter.FILTER_ACCEPT,
    });
    const targets = [];
    let node;
    while ((node = tw.nextNode())) {
      if (WIKI_PATTERN.test(node.nodeValue)) {
        WIKI_PATTERN.lastIndex = 0;
        targets.push(node);
      }
    }
    for (const tn of targets) {
      const frag = document.createDocumentFragment();
      const text = tn.nodeValue;
      let lastIdx = 0;
      let m;
      WIKI_PATTERN.lastIndex = 0;
      while ((m = WIKI_PATTERN.exec(text)) !== null) {
        if (m.index > lastIdx) {
          frag.appendChild(document.createTextNode(text.slice(lastIdx, m.index)));
        }
        const title = m[1].trim();
        const hit = byTitle.get(title);
        if (hit) {
          const a = document.createElement('a');
          a.href = '?doc=' + encodeURIComponent(hit.path);
          a.textContent = title;
          a.dataset.spa = 'doc';
          a.dataset.docPath = hit.path;
          frag.appendChild(a);
        } else {
          const span = document.createElement('span');
          span.className = 'broken-link';
          span.textContent = title;
          span.title = 'No doc with title "' + title + '" in the index.';
          frag.appendChild(span);
        }
        lastIdx = m.index + m[0].length;
      }
      if (lastIdx < text.length) {
        frag.appendChild(document.createTextNode(text.slice(lastIdx)));
      }
      tn.parentNode.replaceChild(frag, tn);
    }
  }

  // SPA navigation hijack for rewritten cross-doc links.
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-spa="doc"]');
    if (!a) return;
    e.preventDefault();
    const docPath = a.dataset.docPath;
    const anchor  = a.dataset.anchor || null;
    if (docPath && window.DocsApp) {
      DocsApp.navigate(docPath, anchor);
    }
  });

  window.addEventListener('docs:rendered', (e) => {
    const { docPath, root } = e.detail;
    // 1. Rewrite explicit <a href> tags.
    root.querySelectorAll('a').forEach((a) => rewriteAnchor(a, docPath));
    // 2. Walk text nodes for [[wiki-link]] tokens.
    const byTitle = indexByTitle(window.DocsApp && DocsApp._index);
    if (byTitle.size > 0) walkText(root, byTitle);
  });

  window.DocsLinks = { rewriteAnchor: rewriteAnchor, _walkText: walkText };
})();
```

- [ ] **Step 3: Add the script tag to docs.html**

In `viewer/docs.html`, find this line:

```html
<script src="shared/docs/docs-app.js"></script>
```

Insert immediately AFTER it:

```html
<script src="shared/docs/docs-links.js"></script>
```

- [ ] **Step 4: Syntax check + smoke**

```bash
cd /Users/sue/Research/BabyVision-v2
node --check viewer/shared/docs/docs-links.js
node --check viewer/shared/docs/docs-app.js

# Verify rewriting end-to-end via a tiny inline test in node:
node --input-type=module -e "
const { JSDOM } = await import('jsdom').catch(() => ({ JSDOM: null }));
if (!JSDOM) { console.log('jsdom not installed — skipping inline DOM check'); process.exit(0); }
" 2>&1 || true
# (Inline DOM checks require jsdom which isn't in scope; rely on browser eyeball.)

python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
curl -sI http://localhost:8765/viewer/shared/docs/docs-links.js | head -1
curl -s http://localhost:8765/viewer/docs.html | grep -c docs-links.js
kill $SERVER_PID 2>/dev/null
```
Expected: link script returns 200; docs.html grep returns at least 1 (the script tag is present).

- [ ] **Step 5: Manual eyeball note**

Document in report: After this task, opening `http://localhost:8765/viewer/docs.html?doc=benchmark/leaderboard` should show clickable links that route via SPA navigation (no full page reload) to other docs. External links open in new tabs.

- [ ] **Step 6: Commit**

```bash
git add viewer/shared/docs/docs-links.js viewer/shared/docs/docs-app.js viewer/docs.html
git commit -m "feat(viewer): internal markdown link rewriting + [[wiki-link]] resolution"
```

---

## Task 5: Right-side TOC + scrollspy

**Files:**
- Create: `viewer/shared/docs/docs-toc.js`
- Modify: `viewer/docs.html` (add one `<script src>` line after `docs-links.js`)

**Interfaces:**
- Consumes:
  - `docs:rendered` CustomEvent (provides `{detail.headings, detail.root}`)
- Produces:
  - Renders into `#docs-toc`. No new public API.
  - IntersectionObserver-driven `is-active` class on the TOC link matching the heading nearest the viewport top.

- [ ] **Step 1: Write `viewer/shared/docs/docs-toc.js`**

```javascript
// viewer/shared/docs/docs-toc.js — right-column outline + scrollspy.
//
// Renders into #docs-toc using the headings list emitted by docs-app.js.
// Uses IntersectionObserver to mark the currently-in-view heading.

(function () {
  'use strict';

  let observer = null;

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function render(headings) {
    const wrap = document.getElementById('docs-toc-wrap') || document.querySelector('.docs-toc-wrap');
    const root = document.getElementById('docs-toc');
    if (!root) return;
    clear(root);

    // If there are fewer than 2 H2-level headings, hide the whole TOC column.
    const h2count = headings.filter((h) => h.level === 2).length;
    if (h2count < 2) {
      if (wrap) wrap.style.display = 'none';
      return;
    } else {
      if (wrap) wrap.style.display = '';
    }

    for (const h of headings) {
      const a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.text;
      a.dataset.targetId = h.id;
      a.className = 'toc-l' + h.level;
      root.appendChild(a);
    }
  }

  function attachScrollspy(root, headings) {
    if (observer) { observer.disconnect(); observer = null; }
    if (!('IntersectionObserver' in window)) return;
    const tocLinks = document.querySelectorAll('#docs-toc a');
    if (!tocLinks.length) return;
    const linkByTarget = new Map();
    tocLinks.forEach((a) => linkByTarget.set(a.dataset.targetId, a));

    const visible = new Set();
    observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      }
      // Active = first heading in document order that is currently visible.
      // Falls back to the first heading otherwise.
      let activeId = null;
      for (const h of headings) {
        if (visible.has(h.id)) { activeId = h.id; break; }
      }
      tocLinks.forEach((a) => a.classList.toggle('is-active', a.dataset.targetId === activeId));
    }, {
      // Trigger when heading enters the upper third of the viewport.
      rootMargin: '0px 0px -66% 0px',
      threshold:  [0, 1],
    });
    for (const h of headings) {
      const el = root.querySelector('#' + cssEscape(h.id));
      if (el) observer.observe(el);
    }
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_\-]/g, '\\$&');
  }

  // Smooth click → scroll (no full reload).
  document.addEventListener('click', (e) => {
    const a = e.target.closest('#docs-toc a');
    if (!a) return;
    const id = a.dataset.targetId;
    const target = document.getElementById(id);
    if (target) {
      e.preventDefault();
      history.replaceState(history.state, '', '#' + id);
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });

  window.addEventListener('docs:rendered', (e) => {
    const { headings, root } = e.detail;
    render(headings);
    attachScrollspy(root, headings);
  });

  window.DocsToc = { render: render };
})();
```

- [ ] **Step 2: Wire into `viewer/docs.html`**

In `viewer/docs.html`, after the `docs-links.js` script tag added in Task 4, append:

```html
<script src="shared/docs/docs-toc.js"></script>
```

Also, give the right column wrapper an id so the script can hide/show it. Find this line in `viewer/docs.html`:

```html
  <aside class="docs-toc-wrap" aria-label="On this page">
```

and change it to:

```html
  <aside class="docs-toc-wrap" id="docs-toc-wrap" aria-label="On this page">
```

- [ ] **Step 3: Syntax check + smoke**

```bash
cd /Users/sue/Research/BabyVision-v2
node --check viewer/shared/docs/docs-toc.js
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
curl -sI http://localhost:8765/viewer/shared/docs/docs-toc.js | head -1
curl -s http://localhost:8765/viewer/docs.html | grep -c docs-toc.js
kill $SERVER_PID 2>/dev/null
```
Expected: 200, grep ≥ 1.

- [ ] **Step 4: Manual eyeball note**

Document in report: Open `http://localhost:8765/viewer/docs.html?doc=platform/architecture` (or any doc with ≥2 H2s); the right column should list its outline; scrolling should highlight the current heading; clicking a TOC link should scroll smoothly.

- [ ] **Step 5: Commit**

```bash
git add viewer/shared/docs/docs-toc.js viewer/docs.html
git commit -m "feat(viewer): right-column TOC + scrollspy for docs SPA"
```

---

## Task 6: Search — `build_docs_search.py` + `docs-search.js`

**Files:**
- Create: `scripts/build_docs_search.py`
- Create: `tests/test_docs_search.py`
- Create: `docs/_search.json` (committed; output of the build script)
- Create: `viewer/shared/docs/docs-search.js`
- Modify: `viewer/docs.html` (add `<script src="vendor/lunr.min.js">` BEFORE `docs-app.js`, and `<script src="shared/docs/docs-search.js">` AFTER `docs-toc.js`)

**Interfaces:**
- Consumes:
  - Filesystem walk over `docs/**/*.md` (same exclusion rules as `build_docs_index.py`)
  - `lunr` global (Task 1 vendor)
  - `window.Manifest.urls.docs.search_index`
- Produces:
  - Python: `build_search_payload(repo_root: Path) -> dict` returning `{generated_at, docs: {path: {title, snippet}}, index: <lunr-serialized>}`. Same `--check` flag pattern as the index builder.
  - JS: `window.DocsSearch.query(text: string, limit?: number) -> Array<{path, title, snippet, score}>`. Side effect: keybinding `/` and `?` focus the search input; Escape blurs; Enter on a result navigates.

- [ ] **Step 1: Write the failing test**

Create `tests/test_docs_search.py`:

```python
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
    assert "docs" in payload and "index" in payload
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
```

- [ ] **Step 2: Run test (RED)**

```bash
pytest tests/test_docs_search.py -v
```
Expected: ModuleNotFoundError for `scripts.build_docs_search`.

- [ ] **Step 3: Write the search builder**

Create `scripts/build_docs_search.py`:

```python
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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR_NAME = "docs"
EXCLUDED_BASENAMES = {"_index.json", "_search.json"}
EXCLUDED_SUFFIXES  = (".intro.md",)

H1_PATTERN = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
CODE_FENCE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
INLINE_CODE = re.compile(r"`[^`]+`")
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^)]+\)")
HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)


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


def _iter_markdown_files(docs_dir: Path):
    for p in sorted(docs_dir.rglob("*.md")):
        if any(p.name.endswith(suf) for suf in EXCLUDED_SUFFIXES):
            continue
        rel = p.relative_to(docs_dir).with_suffix("").as_posix()
        yield rel, p


def build_search_payload(repo_root: Path) -> dict:
    docs_dir = repo_root / DOCS_DIR_NAME
    docs_meta = {}
    index_inputs = []
    if docs_dir.is_dir():
        for rel, abspath in _iter_markdown_files(docs_dir):
            text = abspath.read_text(encoding="utf-8")
            title = extract_title(text) or abspath.stem
            snippet = extract_snippet(text, max_chars=200)
            body = _strip_markdown(text)
            docs_meta[rel] = {"title": title, "snippet": snippet}
            index_inputs.append({"ref": rel, "title": title, "body": body})
    return {
        "generated_at": "2026-06-19T00:00:00Z",
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
```

- [ ] **Step 4: Run tests (GREEN)**

```bash
pytest tests/test_docs_search.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Generate `docs/_search.json` against real docs**

```bash
python scripts/build_docs_search.py
ls -la docs/_search.json
python -c "import json; d = json.load(open('docs/_search.json')); print('docs indexed:', len(d['docs']))"
python scripts/build_docs_search.py --check
```
Expected: file is created (~few KB), shows N docs indexed, --check passes.

- [ ] **Step 6: Write `viewer/shared/docs/docs-search.js`**

```javascript
// viewer/shared/docs/docs-search.js — client-side search using lunr.
//
// At boot, fetches docs/_search.json and constructs a lunr index from
// index_inputs[]. The constructed index is held in memory only — we don't
// serialize back. Each `index_inputs` entry is {ref, title, body}.
//
// Public API on window.DocsSearch:
//   DocsSearch.query(text, limit=10) -> [{path, title, snippet, score}, ...]

(function () {
  'use strict';

  let lunrIndex = null;
  let docsMeta = {};
  let inputEl = null;
  let resultsEl = null;

  function ensureResultsContainer() {
    if (resultsEl && document.body.contains(resultsEl)) return resultsEl;
    resultsEl = document.createElement('div');
    resultsEl.className = 'docs-search-results';
    resultsEl.id = 'docs-search-results';
    resultsEl.hidden = true;
    document.body.appendChild(resultsEl);
    return resultsEl;
  }

  function buildIndex(payload) {
    const inputs = payload.index_inputs || [];
    docsMeta = payload.docs || {};
    lunrIndex = lunr(function () {
      this.ref('ref');
      this.field('title', { boost: 5 });
      this.field('body');
      for (const doc of inputs) this.add(doc);
    });
  }

  function highlightSnippet(snippet, terms) {
    if (!snippet) return '';
    let html = escapeHtml(snippet);
    for (const t of terms) {
      if (!t) continue;
      const re = new RegExp('(' + escapeRegExp(t) + ')', 'ig');
      html = html.replace(re, '<mark>$1</mark>');
    }
    return html;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  function query(text, limit) {
    if (!lunrIndex || !text || text.trim().length < 2) return [];
    limit = limit || 10;
    try {
      const hits = lunrIndex.search(text);
      const terms = text.trim().split(/\s+/);
      return hits.slice(0, limit).map((h) => {
        const meta = docsMeta[h.ref] || {};
        return {
          path:    h.ref,
          title:   meta.title || h.ref,
          snippet: meta.snippet || '',
          score:   h.score,
          terms:   terms,
        };
      });
    } catch (e) {
      // lunr throws on malformed queries (e.g. bare punctuation). Return [].
      return [];
    }
  }

  function renderResults(text) {
    const container = ensureResultsContainer();
    container.innerHTML = '';
    if (!text || text.trim().length < 2) {
      container.hidden = true;
      return;
    }
    const hits = query(text, 10);
    if (hits.length === 0) {
      container.className = 'docs-search-results is-empty';
      container.innerHTML = '<em>No matches.</em>';
      container.hidden = false;
      return;
    }
    container.className = 'docs-search-results';
    for (const h of hits) {
      const a = document.createElement('a');
      a.href = '?doc=' + encodeURIComponent(h.path);
      a.className = 'docs-search-result';
      a.dataset.spa = 'doc';
      a.dataset.docPath = h.path;
      a.innerHTML =
        '<div><strong>' + escapeHtml(h.title) + '</strong></div>' +
        '<div class="path">' + escapeHtml(h.path) + '</div>' +
        '<div>' + highlightSnippet(h.snippet, h.terms) + '</div>';
      container.appendChild(a);
    }
    container.hidden = false;
  }

  // Keyboard: '/' or '?' focuses the search input; Escape blurs.
  document.addEventListener('keydown', (e) => {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
      if (e.key === 'Escape' && e.target === inputEl) {
        e.target.blur();
        if (resultsEl) resultsEl.hidden = true;
      }
      return;
    }
    if (e.key === '/' || e.key === '?') {
      e.preventDefault();
      if (inputEl) inputEl.focus();
    }
  });

  // Click outside results hides them.
  document.addEventListener('click', (e) => {
    if (!resultsEl || resultsEl.hidden) return;
    if (resultsEl.contains(e.target)) return;
    if (e.target === inputEl) return;
    resultsEl.hidden = true;
  });

  // Result clicks → SPA navigation via docs-links.js (data-spa="doc").
  // No additional code needed here — docs-links.js handles the click.

  function bootstrap() {
    inputEl = document.getElementById('docs-search-input');
    if (!inputEl) return;
    inputEl.addEventListener('input', () => renderResults(inputEl.value));
    inputEl.addEventListener('focus', () => {
      if (inputEl.value) renderResults(inputEl.value);
    });
  }

  window.ManifestReady.then(async (m) => {
    bootstrap();
    try {
      const resp = await fetch(m.urls.docs.search_index, { cache: 'no-cache' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const payload = await resp.json();
      buildIndex(payload);
    } catch (e) {
      console.error('[docs-search] failed to load index:', e);
      if (inputEl) inputEl.placeholder = 'Search unavailable';
    }
  });

  window.DocsSearch = { query: query };
})();
```

- [ ] **Step 7: Wire scripts into docs.html**

In `viewer/docs.html`, find the existing `<script src="shared/manifest.js"></script>` line. Immediately AFTER it (before marked.min.js), insert:

```html
<script src="vendor/lunr.min.js"></script>
```

And after the existing `docs-toc.js` script tag (added in Task 5), append:

```html
<script src="shared/docs/docs-search.js"></script>
```

- [ ] **Step 8: Syntax check + curl smoke**

```bash
cd /Users/sue/Research/BabyVision-v2
node --check viewer/shared/docs/docs-search.js
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
curl -sI http://localhost:8765/docs/_search.json | head -1
curl -sI http://localhost:8765/viewer/shared/docs/docs-search.js | head -1
curl -sI http://localhost:8765/viewer/vendor/lunr.min.js | head -1
kill $SERVER_PID 2>/dev/null
```
Expected: three `200 OK`.

- [ ] **Step 9: Manual eyeball note**

Document in report: After this task, opening docs.html, pressing `/`, typing `evaluation` should show a hit for benchmark/evaluation-protocol. Pressing Escape closes the dropdown.

- [ ] **Step 10: Commit**

```bash
git add scripts/build_docs_search.py tests/test_docs_search.py \
        docs/_search.json viewer/shared/docs/docs-search.js viewer/docs.html
git commit -m "feat(docs): build_docs_search.py + lunr-backed docs-search.js"
```

---

## Task 7: MCP virtual docs — composer + 3 seed tools

**Files:**
- Create: `docs/mcp/tools/compute_volume.schema.json`
- Create: `docs/mcp/tools/compute_volume.intro.md`
- Create: `docs/mcp/tools/extract_segment.schema.json`
- Create: `docs/mcp/tools/extract_segment.intro.md`
- Create: `docs/mcp/tools/load_nifti.schema.json`
- Create: `docs/mcp/tools/load_nifti.intro.md`
- Create: `viewer/shared/docs/docs-mcp.js`
- Modify: `viewer/docs.html` (add `<script src="shared/docs/docs-mcp.js">` AFTER docs-search.js)
- Modify: `viewer/shared/docs/docs-app.js` (add 1 hook: detect `mcp/tools/<name>` paths and route through the MCP composer)
- Run: `python scripts/build_docs_index.py` (regenerates `docs/_index.json` to include the 3 new MCP tools)
- Run: `python scripts/build_docs_search.py` (regenerates `docs/_search.json` — search will only index intro bodies since schema is JSON; this is intentional)

**Interfaces:**
- Consumes:
  - `window.Manifest.urls.docs.base_url`
  - `marked.parse(md)` (Task 1)
  - `Prism.highlightAllUnder(root)` (Task 1)
- Produces:
  - `window.DocsMcp.compose(name) -> Promise<string>` — fetches schema + intro, returns rendered markdown text ready for `marked.parse`.
  - Side effect (`docs-app.js` change): when the requested doc path starts with `mcp/tools/`, the renderer calls `DocsMcp.compose(name)` instead of fetching `<name>.md`.

- [ ] **Step 1: Seed `docs/mcp/tools/compute_volume.schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "name": "compute_volume",
  "description": "Compute the voxel volume (in mm³) of a label in a 3D mask volume.",
  "domain": "medical",
  "parameters": [
    {
      "name": "mask_path",
      "type": "string",
      "required": true,
      "description": "Filesystem path to a NIfTI (.nii or .nii.gz) integer-label mask."
    },
    {
      "name": "label",
      "type": "integer",
      "required": true,
      "description": "The voxel value identifying the target structure (1, 2, ...). Background is typically 0."
    },
    {
      "name": "spacing_override",
      "type": "array<float>",
      "required": false,
      "description": "Optional [x, y, z] voxel spacing in mm. Defaults to the spacing in the NIfTI affine."
    }
  ],
  "returns": {
    "type": "object",
    "fields": [
      {"name": "volume_mm3", "type": "float", "description": "Total volume in cubic millimetres."},
      {"name": "voxel_count", "type": "integer", "description": "Number of voxels with the matching label."},
      {"name": "spacing_mm", "type": "array<float>", "description": "Spacing used in the calculation, [x, y, z] mm."}
    ]
  },
  "source": "sci3d_agent/tools/medical/compute_volume.py"
}
```

- [ ] **Step 2: Seed `docs/mcp/tools/compute_volume.intro.md`**

```markdown
## When to use

Reach for `compute_volume` when the question is "how big is this structure?" and the structure
is delineated by a deterministic mask — typically a segmentation file paired with the source
imaging volume. Common cases:

- Comparing tumour volume before and after treatment
- Reporting organ volumes for cohort statistics
- Quantifying lesion burden in stroke imaging

Do **not** use this tool when the structure is defined only by a fuzzy / probabilistic map —
threshold it first, or pick the tool that integrates the probability mass directly.

## Example

```python
{
  "tool": "compute_volume",
  "args": {
    "mask_path": "data/kidney_ct/case-001/mask.nii.gz",
    "label": 1
  }
}
```

Expected response:

```json
{
  "volume_mm3": 142876.5,
  "voxel_count": 18241,
  "spacing_mm": [0.8, 0.8, 1.0]
}
```
```

- [ ] **Step 3: Seed `docs/mcp/tools/extract_segment.schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "name": "extract_segment",
  "description": "Crop a 3D image volume to the bounding box of a target label in a mask.",
  "domain": "medical",
  "parameters": [
    {
      "name": "image_path",
      "type": "string",
      "required": true,
      "description": "NIfTI volume to be cropped."
    },
    {
      "name": "mask_path",
      "type": "string",
      "required": true,
      "description": "NIfTI label mask in the same voxel grid as image_path."
    },
    {
      "name": "label",
      "type": "integer",
      "required": true,
      "description": "Voxel value identifying the structure of interest."
    },
    {
      "name": "padding_voxels",
      "type": "integer",
      "required": false,
      "description": "Number of voxels of padding on each side. Defaults to 4."
    }
  ],
  "returns": {
    "type": "object",
    "fields": [
      {"name": "cropped_path", "type": "string", "description": "Path to the cropped NIfTI written under the agent's scratch dir."},
      {"name": "bbox_voxels", "type": "array<integer>", "description": "Bounding box as [xmin, ymin, zmin, xmax, ymax, zmax] inclusive."}
    ]
  },
  "source": "sci3d_agent/tools/medical/extract_segment.py"
}
```

- [ ] **Step 4: Seed `docs/mcp/tools/extract_segment.intro.md`**

```markdown
## When to use

Use `extract_segment` to isolate the volume of interest before downstream analysis — e.g.
running texture statistics, shape descriptors, or feeding a small CNN. It's the right move
whenever the cost of analysing the full volume is high but only a small region is relevant.

Pair it with `compute_volume` to verify the bounding box is reasonable before committing to
the crop.

## Example

```python
{
  "tool": "extract_segment",
  "args": {
    "image_path": "data/kidney_ct/case-001/image.nii.gz",
    "mask_path":  "data/kidney_ct/case-001/mask.nii.gz",
    "label": 1,
    "padding_voxels": 6
  }
}
```

Expected response:

```json
{
  "cropped_path": "/tmp/agent-scratch/case-001-label-1-cropped.nii.gz",
  "bbox_voxels": [38, 102, 14, 224, 318, 88]
}
```
```

- [ ] **Step 5: Seed `docs/mcp/tools/load_nifti.schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "name": "load_nifti",
  "description": "Load a NIfTI volume and return its metadata (shape, affine, voxel spacing).",
  "domain": "medical",
  "parameters": [
    {
      "name": "path",
      "type": "string",
      "required": true,
      "description": "Filesystem path to a .nii or .nii.gz file."
    }
  ],
  "returns": {
    "type": "object",
    "fields": [
      {"name": "shape", "type": "array<integer>", "description": "Volume dimensions as [x, y, z]."},
      {"name": "spacing_mm", "type": "array<float>", "description": "Voxel spacing in mm."},
      {"name": "affine", "type": "array<array<float>>", "description": "4x4 NIfTI affine matrix mapping voxel index → world (mm) coordinates."},
      {"name": "dtype", "type": "string", "description": "Numpy dtype string of the volume data."}
    ]
  },
  "source": "sci3d_agent/tools/io/load_nifti.py"
}
```

- [ ] **Step 6: Seed `docs/mcp/tools/load_nifti.intro.md`**

```markdown
## When to use

`load_nifti` is the canonical first step when working with medical volumes — call it before
any other tool that needs to know dimensions or spacing. It does **not** stream voxel data
into the agent's context (that would blow the budget for typical 512×512×512 volumes); it
returns metadata only.

If you need the voxel array itself, hand the file path to a downstream tool that operates
on the data in-place (e.g. [[compute_volume]], [[extract_segment]]).

## Example

```python
{ "tool": "load_nifti", "args": { "path": "data/kidney_ct/case-001/image.nii.gz" } }
```

Expected response:

```json
{
  "shape": [512, 512, 124],
  "spacing_mm": [0.8, 0.8, 1.0],
  "affine": [[0.8, 0, 0, -204.8], [0, 0.8, 0, -204.8], [0, 0, 1.0, -62.0], [0, 0, 0, 1.0]],
  "dtype": "int16"
}
```
```

- [ ] **Step 7: Write `viewer/shared/docs/docs-mcp.js`**

```javascript
// viewer/shared/docs/docs-mcp.js — compose virtual MCP-tool docs.
//
// Input: tool name (e.g. "compute_volume"). The composer fetches:
//   <docs.base_url>/mcp/tools/<name>.schema.json
//   <docs.base_url>/mcp/tools/<name>.intro.md   (may be 404)
//
// It returns a single markdown string that, when fed through marked.parse,
// looks like:
//   # <name>
//   *<schema.description>*
//   ## When to use            (from intro.md, header copied verbatim)
//   ## Example                (from intro.md)
//   ## Parameters             (from schema.parameters, as a table)
//   ## Returns                (from schema.returns)
//   ## Source                 (link to schema.source if present)
//
// If intro.md is missing the composer prepends a banner ("Intro pending —
// schema only") and skips the When-to-use / Example sections.

(function () {
  'use strict';

  function paramTable(params) {
    if (!params || !params.length) return "_no parameters_";
    const lines = [];
    lines.push("| Name | Type | Required | Description |");
    lines.push("|---|---|---|---|");
    for (const p of params) {
      const req = p.required ? "yes" : "no";
      const desc = (p.description || "").replace(/\|/g, "\\|");
      lines.push("| `" + p.name + "` | `" + p.type + "` | " + req + " | " + desc + " |");
    }
    return lines.join("\n");
  }

  function returnsBlock(ret) {
    if (!ret) return "_unspecified_";
    if (!ret.fields || !ret.fields.length) {
      return "Type: `" + (ret.type || "any") + "`";
    }
    const lines = [];
    lines.push("Type: `" + (ret.type || "object") + "`");
    lines.push("");
    lines.push("| Field | Type | Description |");
    lines.push("|---|---|---|");
    for (const f of ret.fields) {
      const desc = (f.description || "").replace(/\|/g, "\\|");
      lines.push("| `" + f.name + "` | `" + f.type + "` | " + desc + " |");
    }
    return lines.join("\n");
  }

  function sourceLink(schema) {
    if (!schema.source) return "_(no source link)_";
    return "[`" + schema.source + "`](#)";
  }

  async function fetchText(url) {
    const resp = await fetch(url, { cache: 'no-cache' });
    if (!resp.ok) {
      const err = new Error('HTTP ' + resp.status + ' for ' + url);
      err.status = resp.status;
      throw err;
    }
    return resp.text();
  }

  async function fetchJson(url) {
    const text = await fetchText(url);
    return JSON.parse(text);
  }

  async function compose(name) {
    const base = window.Manifest.urls.docs.base_url; // ends with '/'
    const schemaUrl = new URL('mcp/tools/' + name + '.schema.json', base).href;
    const introUrl  = new URL('mcp/tools/' + name + '.intro.md',    base).href;

    let schema;
    try { schema = await fetchJson(schemaUrl); }
    catch (e) {
      throw new Error('Missing schema for MCP tool "' + name + '": ' + e.message);
    }

    let introText = '';
    let introMissing = false;
    try { introText = await fetchText(introUrl); }
    catch (e) {
      if (e.status === 404) introMissing = true;
      else throw e;
    }

    const lines = [];
    lines.push('# ' + name);
    lines.push('');
    lines.push('*' + (schema.description || '') + '*');
    lines.push('');
    if (introMissing) {
      // marked treats blockquotes as <blockquote>; we'll style this banner
      // via the prose flow.
      lines.push('> ⚠ Intro pending — schema only. Add `docs/mcp/tools/'
        + name + '.intro.md` with `## When to use` and `## Example` sections.');
      lines.push('');
    } else {
      // Pass intro through verbatim. Authors are required to start with the
      // "## When to use" heading by Plan 2 convention.
      lines.push(introText.trim());
      lines.push('');
    }
    lines.push('## Parameters');
    lines.push('');
    lines.push(paramTable(schema.parameters));
    lines.push('');
    lines.push('## Returns');
    lines.push('');
    lines.push(returnsBlock(schema.returns));
    lines.push('');
    lines.push('## Source');
    lines.push('');
    lines.push(sourceLink(schema));
    lines.push('');
    return lines.join('\n');
  }

  window.DocsMcp = { compose: compose };
})();
```

- [ ] **Step 8: Hook docs-app.js to route MCP paths through the composer**

In `viewer/shared/docs/docs-app.js`, find the `render` function. Currently it does:

```javascript
  async function render(docPath) {
    const content = $('docs-content');
    content.innerHTML = '<p class="empty">Loading…</p>';
    let mdText;
    try {
      mdText = await fetchDoc(docPath);
    } catch (e) {
      showError(e.message);
      return;
    }
```

Replace the try/catch block above with:

```javascript
  async function render(docPath) {
    const content = $('docs-content');
    content.innerHTML = '<p class="empty">Loading…</p>';
    let mdText;
    try {
      if (docPath.startsWith('mcp/tools/') && window.DocsMcp) {
        const name = docPath.slice('mcp/tools/'.length);
        mdText = await DocsMcp.compose(name);
      } else {
        mdText = await fetchDoc(docPath);
      }
    } catch (e) {
      showError(e.message);
      return;
    }
```

(Everything else in the function stays unchanged.)

- [ ] **Step 9: Add the script tag to docs.html**

In `viewer/docs.html`, after the `docs-search.js` script tag (added in Task 6), append:

```html
<script src="shared/docs/docs-mcp.js"></script>
```

Then ensure `docs-app.js` comes AFTER `docs-mcp.js` (so `window.DocsMcp` is defined when `render` is called). The script tag order in `<body>` should be:

```html
<script src="shared/manifest.js"></script>
<script src="vendor/lunr.min.js"></script>
<script src="vendor/marked.min.js"></script>
<script src="vendor/prism.min.js"></script>
<!-- prism language components ... -->
<script src="shared/docs/docs-tree.js"></script>
<script src="shared/docs/docs-mcp.js"></script>
<script src="shared/docs/docs-app.js"></script>
<script src="shared/docs/docs-links.js"></script>
<script src="shared/docs/docs-toc.js"></script>
<script src="shared/docs/docs-search.js"></script>
```

(`docs-mcp.js` moves BEFORE `docs-app.js` so the namespace is available when bootstrap runs.)

- [ ] **Step 10: Regenerate `_index.json` and `_search.json`**

```bash
cd /Users/sue/Research/BabyVision-v2
python scripts/build_docs_index.py
python scripts/build_docs_search.py
python scripts/build_docs_index.py --check
python scripts/build_docs_search.py --check
```
Expected: both rebuild without error; both `--check` runs print `OK:` exit 0.

Verify the index now includes MCP tools:

```bash
python3 -c "
import json
d = json.load(open('docs/_index.json'))
mcp = [s for s in d['tree'] if s['title'] == 'MCP tools'][0]
for c in mcp['children']:
    print(c['title'], '-', c['path'])
"
```
Expected output:
```
compute_volume - mcp/tools/compute_volume
extract_segment - mcp/tools/extract_segment
load_nifti - mcp/tools/load_nifti
```

- [ ] **Step 11: Syntax check + curl smoke + tests**

```bash
cd /Users/sue/Research/BabyVision-v2
node --check viewer/shared/docs/docs-mcp.js
node --check viewer/shared/docs/docs-app.js
pytest tests/test_docs_index.py tests/test_docs_search.py -v 2>&1 | tail -5
python scripts/build_docs_index.py --check
python scripts/build_docs_search.py --check

python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
curl -sI http://localhost:8765/docs/mcp/tools/load_nifti.schema.json | head -1
curl -sI http://localhost:8765/docs/mcp/tools/load_nifti.intro.md | head -1
curl -sI http://localhost:8765/viewer/shared/docs/docs-mcp.js | head -1
kill $SERVER_PID 2>/dev/null
```
Expected: all tests pass, both `--check` clean, three `200 OK` from curl.

- [ ] **Step 12: Manual eyeball note**

Document in report: Opening `http://localhost:8765/viewer/docs.html?doc=mcp/tools/load_nifti` should render the composed virtual doc — title "load_nifti", italic description, "When to use" + "Example" sections from intro, a Parameters table, a Returns table, a Source line. The left sidebar's "MCP tools" section should list all three.

- [ ] **Step 13: Commit**

```bash
git add docs/mcp/ docs/_index.json docs/_search.json \
        viewer/shared/docs/docs-mcp.js \
        viewer/shared/docs/docs-app.js \
        viewer/docs.html
git status
git commit -m "feat(docs): MCP virtual-doc composer + 3 seed tool docs"
```

---

## Task 8: CI extensions + acceptance verification

**Files:**
- Modify: `.github/workflows/manifest-check.yml`
- Create: `tests/test_mcp_pairs.py`

**Interfaces:**
- Consumes: build scripts + test files from earlier tasks
- Produces: CI guarantees that committed `_index.json` and `_search.json` are not stale, and that every `*.schema.json` has a matching `*.intro.md` (warn-only on the pair check; eventually upgraded to fail in a later plan).

- [ ] **Step 1: Write the MCP pair test**

Create `tests/test_mcp_pairs.py`:

```python
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
```

- [ ] **Step 2: Run the test (it must pass since Task 7 shipped all three intros)**

```bash
pytest tests/test_mcp_pairs.py -v
```
Expected: `1 passed`, no warnings.

- [ ] **Step 3: Extend `.github/workflows/manifest-check.yml`**

Edit `.github/workflows/manifest-check.yml`. Find the `paths:` list under `pull_request:` and add four more entries (keep existing entries):

```yaml
on:
  pull_request:
    paths:
      - 'viewer/manifest.json'
      - 'viewer/manifest.local.example.json'
      - 'viewer/shared/manifest.schema.json'
      - 'tools/validate_manifest.py'
      - 'tests/test_manifest_schema.py'
      - 'pyproject.toml'
      # NEW (Plan 2):
      - 'docs/**/*.md'
      - 'docs/mcp/tools/**'
      - 'scripts/build_docs_index.py'
      - 'scripts/build_docs_search.py'
      - 'tests/test_docs_index.py'
      - 'tests/test_docs_search.py'
      - 'tests/test_mcp_pairs.py'
      - 'docs/_index.json'
      - 'docs/_search.json'
  push:
    branches: [main]
```

And find the `steps:` block; add four new steps AFTER the existing "Run manifest schema tests" step (keep that step):

```yaml
      - name: Check docs/_index.json not stale
        run: python scripts/build_docs_index.py --check

      - name: Check docs/_search.json not stale
        run: python scripts/build_docs_search.py --check

      - name: Run docs index tests
        run: pytest tests/test_docs_index.py -v

      - name: Run docs search tests
        run: pytest tests/test_docs_search.py -v

      - name: Run MCP pair tests
        run: pytest tests/test_mcp_pairs.py -v
```

(Order: the staleness checks come before pytest so a stale-output PR fails fast on the cheap check.)

- [ ] **Step 4: Lint YAML**

```bash
cd /Users/sue/Research/BabyVision-v2
python -c "import yaml; yaml.safe_load(open('.github/workflows/manifest-check.yml'))" && echo OK
```
Expected: `OK`.

- [ ] **Step 5: Full local acceptance pass**

```bash
cd /Users/sue/Research/BabyVision-v2

echo "=== Plan 1 still healthy ==="
python tools/validate_manifest.py
python tools/validate_manifest.py --manifest viewer/manifest.local.example.json
pytest tests/test_manifest_schema.py -v 2>&1 | tail -3

echo "=== Plan 2 build outputs not stale ==="
python scripts/build_docs_index.py --check
python scripts/build_docs_search.py --check

echo "=== Plan 2 tests ==="
pytest tests/test_docs_index.py tests/test_docs_search.py tests/test_mcp_pairs.py -v 2>&1 | tail -10

echo "=== JS syntax sweep ==="
for f in viewer/shared/manifest.js \
         viewer/shared/docs/docs-app.js \
         viewer/shared/docs/docs-tree.js \
         viewer/shared/docs/docs-links.js \
         viewer/shared/docs/docs-toc.js \
         viewer/shared/docs/docs-search.js \
         viewer/shared/docs/docs-mcp.js \
         viewer/vendor/marked.min.js \
         viewer/vendor/prism.min.js \
         viewer/vendor/lunr.min.js; do
  node --check "$f" && echo "OK: $f"
done

echo "=== HTML parses ==="
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('viewer/docs.html').read()); print('docs.html OK')"
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('viewer/_manifest_check.html').read()); print('_manifest_check OK')"
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('viewer/_vendor_check.html').read()); print('_vendor_check OK')"

echo "=== End-to-end server smoke ==="
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
for u in \
  /viewer/docs.html \
  /viewer/_manifest_check.html \
  /viewer/_vendor_check.html \
  /viewer/manifest.json \
  /docs/_index.json \
  /docs/_search.json \
  /docs/platform/quickstart.md \
  /docs/mcp/tools/load_nifti.schema.json \
  /docs/mcp/tools/load_nifti.intro.md \
  /viewer/vendor/marked.min.js \
  /viewer/vendor/prism.min.js \
  /viewer/vendor/lunr.min.js \
  /viewer/vendor/prism/prism-python.min.js \
  /viewer/shared/manifest.js \
  /viewer/shared/docs/docs-app.js \
  /viewer/shared/docs/docs-tree.js \
  /viewer/shared/docs/docs-links.js \
  /viewer/shared/docs/docs-toc.js \
  /viewer/shared/docs/docs-search.js \
  /viewer/shared/docs/docs-mcp.js; do
  s=$(curl -sI -o /dev/null -w '%{http_code}' "http://localhost:8765$u")
  echo "$s $u"
done
kill $SERVER_PID 2>/dev/null
```
Expected:
- All Plan 1 checks pass
- Both `--check` runs print `OK:`
- All Plan 2 tests pass (10+ tests across the three files)
- Every node --check prints `OK:`
- Every HTML page parses
- Every curl returns `200`

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/manifest-check.yml tests/test_mcp_pairs.py
git commit -m "ci(docs): validate _index/_search not stale + MCP pair test"
```

---

## Task 9: Plan-2 acceptance

**Files:** none modified — read-only verification.

**Interfaces:** none — terminal verification task.

- [ ] **Step 1: Confirm branch state**

```bash
cd /Users/sue/Research/BabyVision-v2
git status -sb
git log --oneline 19bb3a5..HEAD
```
Expected: clean working tree (apart from any pre-existing untracked items); commit log shows the Plan-2 task commits in order (one per task).

- [ ] **Step 2: Run the full acceptance command set from Task 8 Step 5 again**

(Same commands as Task 8 Step 5 — this re-confirms after the final CI commit.)

- [ ] **Step 3: Open the browser-side acceptance URLs**

The implementer can't drive a browser. Document this checklist in the report so the controller (or user) can walk it:

- http://localhost:8765/viewer/_vendor_check.html → all checks green
- http://localhost:8765/viewer/_manifest_check.html → green OK; `urls.*` resolved
- http://localhost:8765/viewer/docs.html → loads platform/quickstart as default
- http://localhost:8765/viewer/docs.html?doc=benchmark/leaderboard → loads, sidebar highlights
- http://localhost:8765/viewer/docs.html?doc=mcp/tools/load_nifti → composed virtual doc
- http://localhost:8765/viewer/docs.html — press `/`, type "evaluation" → results dropdown shows hits

- [ ] **Step 4: Report the v1 acceptance checklist status**

For each item in spec §7.4, mark Pass/Fail in the report:

- [ ] Four primary pages reachable: Home (existing SV-styled index.html), Doc (new), Domain 3D Assets (Plan 3), QA Tasks (Plan 3)
  - For Plan 2 specifically: **Doc** is the only one that needs to work now. The other three land in Plan 3.
- [ ] Docs full-text search returns results
- [ ] Manifest decoupling proven: replacing `manifest.json.data` paths with a different dataset leaves the viewer functional (Plan 1 acceptance; Plan 2 inherits)
- [ ] All eleven domain tiles render — N/A for Plan 2 (Plan 3)
- [ ] § Compare renders the "TBD — tracked in #5" placeholder — N/A for Plan 2 (Plan 3)
- [ ] Leaderboard empty state renders — N/A for Plan 2 (Plan 3)

So Plan 2 acceptance items are:
- ✅ `viewer/docs.html` reachable, default doc loads
- ✅ Left sidebar populated from `_index.json`
- ✅ Internal `.md` links route via SPA (no full reload)
- ✅ `[[wiki-link]]` resolves against `_index.json`; broken ones show as `.broken-link`
- ✅ Right TOC + scrollspy work on docs with ≥2 H2 headings
- ✅ Lunr search returns results; `/` focuses input; Escape closes
- ✅ MCP virtual doc composes (schema + intro)
- ✅ All three seed MCP tools render
- ✅ `--check` flags clean
- ✅ All pytest passing

- [ ] **Step 5: No commit (verification-only task)**

Report only. Update the SDD ledger via the controller (the controller appends `Task 9: complete (no commit)` to `.superpowers/sdd/progress.md`).

---

## What this plan explicitly does NOT do (out of scope, deferred to Plan 3 or beyond)

- Rewrite `viewer/index.html` to the new BabyVision-v2 homepage (Plan 3).
- Patch per-modality pages (`molstar.html`, `niivue.html`, `cornerstone.html`, etc.) to consume `Manifest.urls.*` (Plan 3).
- Add `viewer/assets.html`, `viewer/qagen.html`, `viewer/leaderboard.html` (Plan 3).
- Fill in the remaining 9 domains in `manifest.json.domains[]` (Plan 3; pending GH #6/#7/#8).
- Compare table content (GH issue #5).
- Auto-extract MCP tool schemas from `sci3d_agent/` source code (future plan, when `sci3d_agent/` lands in BV2).
- Mermaid diagrams, KaTeX math (deferred to v2).
- Headless / Playwright browser tests (deferred to v2; Plan 2 uses curl + smoke HTML pages).
- Mobile drawer for sidebar / hamburger (deferred — CSS hides sidebar+TOC on narrow viewports; mobile UX polish is Plan 3 or later).

---

## Self-review against the spec

| Spec section | Plan 2 coverage |
|---|---|
| §4.1 three-column layout (sidebar / content / TOC) | Task 3 (shell + tree) + Task 5 (TOC) |
| §4.2 URL routing via History API | Task 3 (`docs-app.js`) |
| §4.3 `_index.json` build script + format | Task 2 |
| §4.4 MCP virtual doc composition (schema + intro) | Task 7 (composer + 3 seeds) |
| §4.4 Required `## When to use` + `## Example` headings | Task 7 (all three seeds comply; convention documented in `architecture.md` from Plan 1) |
| §4.4 "Intro pending" banner when intro.md missing | Task 7 `docs-mcp.js` `introMissing` branch |
| §4.5 Link rewriting (relative .md, in-page anchor, external, wiki) | Task 4 (`docs-links.js`) |
| §4.6 marked.js + Prism rendering pipeline | Tasks 1 + 3 |
| §4.7 lunr full-text search | Task 6 |
| §4.8 Error states (404, intro missing, broken link) | Task 3 (404 panel), Task 7 (intro-missing banner), Task 4 (broken-link span) |
| §6 manifest decoupling | Inherited from Plan 1 + 1.5; consumed via `Manifest.urls.docs.*` |
| §7.1 CI staleness checks | Task 8 |
| §7.2 smoke tests | All tasks include curl + node --check smoke; manual browser eyeball noted in reports |
| §10 explicit out-of-scope | "What this plan does NOT do" section above |

No gaps. No placeholders. All steps contain executable code or shell commands. Type / signature consistency verified across tasks:
- `extract_title`, `build_tree`, `main` — defined Task 2, referenced by tests Task 2.
- `extract_snippet`, `build_search_payload`, `main` — defined Task 6, referenced by tests Task 6.
- `DocsApp.navigate`, `DocsApp.render`, `DocsApp.parseUrlDoc`, `DocsApp._index` — defined Task 3, consumed by Tasks 4/5/6/7.
- `DocsTree.mount`, `DocsTree.setActive` — defined Task 3, consumed by Task 3 bootstrap.
- `DocsMcp.compose` — defined Task 7, called from Task 7 docs-app.js patch.
- `DocsSearch.query` — defined Task 6, no other consumers (used internally for UI rendering).
- `docs:rendered` CustomEvent — emitted Task 3, listened by Tasks 4/5.
