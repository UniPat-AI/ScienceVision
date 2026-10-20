# BabyVision-v2 Viewer Platform — Design Spec

**Date:** 2026-06-19
**Status:** Approved (brainstorming → spec) · pending implementation plan
**Tracking issues:** #5 (Compare table), #6 (industrial vs simulation), #7 (Genesis_VP domain), #8 (drop pointcloud)

---

## 1 — Goals & Non-goals

### Goals

- BabyVision-v2 ships a **self-hosted static viewer** at `viewer/` that runs with `python -m http.server` — no build chain, no Node runtime in the served path.
- Three content modules: **3D Assets · QA Tasks · Docs** (Docs includes the MCP tool reference).
- Viewer code is **decoupled from any specific dataset** via a single `viewer/manifest.json` file. Same viewer code runs against BabyVision-v2's public demo data and against ScienceVision's full private dataset, with only `manifest.json` differing.
- Homepage tells the story: what the benchmark is (Tasks), why this methodology (Motivation), which domains are covered (Domain coverage), how it differs from existing benchmarks (Compare), where to go next (Get started).
- Two clear partner-facing entry points, both terminating in docs/leaderboard:
  1. **Co-build a new domain** — for teams onboarding a new scientific/professional domain.
  2. **Verify on the leaderboard** — for model teams submitting baseline runs.

### Non-goals

- No server, no database, no auth, no user accounts.
- No static site generator (mkdocs / docusaurus / vitepress). Markdown is rendered client-side.
- No rewrite of the existing per-modality renderers (`molstar.html`, `niivue.html`, `cornerstone.html`, `gltf.html`, `occt.html`, `urdf.html`, `citymap.html`, `saturn.html`, `bandplot.html`, `field3d.html`, `pointcloud.html`, etc.). They are mirrored from ScienceVision verbatim; only their data-path inputs are funneled through the manifest.
- No 2D-VQA, 3D-Grounded-QA, Negative-Samples, or Synthetic-Negative tabs in BabyVision-v2 — out of scope for this benchmark.

---

## 2 — Directory Layout & Code Ownership

```
BabyVision-v2/
├── docs/                                  # markdown sources (rendered client-side)
│   ├── benchmark/                         # existing
│   ├── platform/                          # existing
│   ├── partners/                          # existing
│   ├── mcp/
│   │   └── tools/                         # NEW — one pair per MCP tool
│   │       ├── <name>.schema.json         #   auto-generated from sci3d_agent
│   │       └── <name>.intro.md            #   human-written: When to use + Example
│   ├── _index.json                        # NEW — generated tree for left sidebar
│   └── _search.json                       # NEW — generated lunr.js index
│
├── viewer/                                # mirrored from ScienceVision, flat in BV2
│   ├── index.html                         # homepage (rewritten per §3)
│   ├── docs.html                          # NEW — docs SPA
│   ├── assets.html                        # NEW — extracted from SV index.html
│   ├── qagen.html                         # NEW — extracted from SV index.html
│   ├── leaderboard.html                   # NEW — submissions table
│   ├── manifest.json                      # NEW — single source of truth for data paths
│   ├── manifest.local.example.json        # NEW — for partner local datasets
│   ├── shared/
│   │   ├── manifest.js                    # NEW — loads manifest, exposes window.Manifest
│   │   ├── tabs.js                        # existing
│   │   ├── style.css                      # existing (extended for docs SPA)
│   │   ├── docs/                          # NEW — docs SPA modules
│   │   │   ├── docs-app.js                #   router + main render loop
│   │   │   ├── docs-tree.js               #   left sidebar tree
│   │   │   ├── docs-search.js             #   lunr-based search
│   │   │   ├── docs-mcp.js                #   MCP virtual-doc composer (§4.4)
│   │   │   └── docs-links.js              #   internal-link rewriter (§4.5)
│   │   └── sections/                      # existing (matched.js, qagen.js)
│   ├── vendor/                            # local third-party (no CDN at runtime)
│   │   ├── marked.min.js                  # NEW — markdown renderer
│   │   ├── prism.min.js + components/     # NEW — code highlighting
│   │   ├── lunr.min.js                    # NEW — search
│   │   └── d3-*.min.js                    # existing
│   └── <per-modality>.html                # mirrored from SV verbatim (manifest-aware)
│
├── scripts/
│   ├── build_docs_index.py                # NEW — scan docs/**/*.md → _index.json
│   ├── build_docs_search.py               # NEW — scan docs/**/*.md → _search.json
│   ├── build_mcp_docs.py                  # NEW — scan sci3d_agent → docs/mcp/tools/*.schema.json
│   └── (existing scripts unchanged)
│
└── tests/
    ├── viewer/                            # NEW — Playwright smoke tests
    └── visual/baseline/                   # NEW — 3 baseline screenshots
```

### Hard rules

- **No viewer code may hardcode** `assets.index.json`, `qagen_index.json`, or any other data path. Every reference goes through `window.Manifest.data.*` or `window.Manifest.docs.*`.
- Build outputs (`_index.json`, `_search.json`, every `mcp/tools/*.schema.json`) are **committed to git**. CI re-runs the build scripts and fails on diff.
- `viewer/` is **flat in BV2** (not a git submodule, not a mirror placeholder). It is part of BV2's tracked source.

---

## 3 — Homepage (`viewer/index.html`)

### Brand and global navigation

- Brand text top-left: **`BabyVision-v2`** (not "Science Vision").
- Global nav, present on every page: `Home · Doc · Domain 3D Assets · QA Tasks · Leaderboard`.
- Top-right meta line driven by manifest: `<N> assets · generated <ISO timestamp>`.

### Homepage sections (top to bottom)

1. **Hero**

   ```
   BABYVISION-V2 · BENCHMARK + PLATFORM     v<manifest.version>

   Evaluating 3D-grounded visual reasoning
   in professional domains.

   Evaluation-first · executable ground truth · partner-extensible.
   ```

2. **Module launcher** — three large tiles, replacing the in-page TOC nav. Each tile links to one of the three primary modules:

   | Tile | Subhead | Target |
   |---|---|---|
   | **Doc** | MCP tools + platform docs + benchmark protocol | `docs.html` |
   | **Domain 3D Assets** | `<shipped>` shipped · `<partner-in-progress>` partner-in-progress | `assets.html` |
   | **QA Tasks** | V2 / V3 / V4 / V6 + Genesis_VP + Medical Spatial | `qagen.html` |

   Tile counts come from `manifest.counts`. Visual style mirrors ScienceVision's existing card pattern (bg2 + accent hover border).

3. **§ Tasks** — six task-family cards with keyword-rich subtitles:

   | Family | Keyword subtitle |
   |---|---|
   | V2 — Cross-section / topology | 2D-view → 3D inference · cannot be shortcut by code · geometric reasoning |
   | V3 — Segmentation / referring | pixel-level GT · SSIM / LPIPS · defends against LLM-as-judge |
   | V4 — Manipulation / counterfactual | rotate · slice · deform · geometry-grounded GT |
   | V6 — Multi-step reasoning | multi-tool trace · scored on answer + trace fidelity |
   | Genesis_VP — Physics | force control · MPM fracture · MPM granular · FEM cloth · P1/P2 |
   | Medical Spatial — GT-grounded | M3D · IXI brain · distance / angle / topology / volume |

   Each card links to `qagen.html?family=<id>`.

4. **§ Motivation** — three short paragraphs (~60 words each):
   1. **Executable GT** — answers produced by deterministic programs (geometry, mask argmax, physics).
   2. **No LLM-as-judge** — pixel-level / numeric metrics; LLM verdict only at trace-fidelity layer.
   3. **3D-grounded** — true 3D geometry / topology as the standard answer, not caption similarity.

   Each paragraph links to `docs.html?doc=benchmark/evaluation-protocol`.

5. **§ Domain coverage** — eleven-tile mosaic following the **D2 strategy**: every supported domain is shown, with a state flag.
   - **Shipped** tiles (BV2 actually ships data): colored, clickable to `assets.html#<domain>`.
   - **Partner-in-progress** tiles: dim/desaturated, label "coming via partner".
   - Locked in Plan 3 per recommended resolutions of GH #6/#7/#8:
     ```
     biology · medical · embodied · robotics · geography · astronomy ·
     topological_photonics · photonics · simulation · climate · industrial
     ```
   - v0.1.1 ships `medical` and `embodied` as `shipped: true`; the other 9 render as dim "partner-in-progress" tiles.
   - Hover a tile → highlight its row in the Compare table (when populated).

6. **§ Compare** — placeholder. Renders a 1-line "TBD — tracked in #5" callout. To be populated once issue #5 resolves benchmark targets and column axes. See `docs/TODO.md` (2026/06/19 entry) for the candidate matrix.

7. **§ Get started** — two large side-by-side cards:

   | Card | Links inside |
   |---|---|
   | **Co-build a new domain** | Quickstart · New-domain template · MCP tool reference · Partner SDK API |
   | **Verify on the leaderboard** | Submission guide · Baseline profiles · Reproduce a leaderboard entry |

   Each link is a deep link into `docs.html?doc=<path>` so the URL is shareable.

### Sticky elements

- Global top nav is sticky.
- The Module launcher is **not** sticky; it scrolls away.

---

## 4 — Docs SPA (`viewer/docs.html`)

### 4.1 — Three-column layout

```
┌─ Global nav ──────────────────────────────────────────────────────┐
├──────────────┬─────────────────────────────────────┬──────────────┤
│ Search       │ Doc body (markdown rendered)        │ On this page │
│              │                                     │ (H2/H3 anchors)
│ Tree         │ max-width: 720px                    │ scrollspy     │
│ (collapsible)│                                     │              │
│              │                                     │ hidden if <2 │
│              │                                     │   H2 sections│
└──────────────┴─────────────────────────────────────┴──────────────┘
```

- Left tree column is collapsible; per-section folded state persisted in `localStorage`.
- Mobile: left and right columns hidden by default; hamburger toggles left tree; right TOC becomes a `<details>` block at the top of the doc body.

### 4.2 — URL routing

| URL | Behavior |
|---|---|
| `docs.html` | Defaults to `?doc=platform/quickstart` |
| `docs.html?doc=<path-without-md>` | Loads that markdown file |
| `docs.html?doc=<path>#<anchor>` | Loads doc + scrolls to anchor |
| `docs.html?doc=mcp/tools/<name>` | Renders virtual MCP-tool doc (§4.4) |
| `docs.html?q=<query>` | Renders search results page |

- All navigation uses `history.pushState`; no full-page reloads.
- Back / Forward keys work correctly (routing handler listens to `popstate`).
- Direct opens to `?doc=...#anchor` work as deep links and are shareable.

### 4.3 — Index build (`scripts/build_docs_index.py` and `build_docs_search.py`)

Both scripts scan `docs/**/*.md`, skipping any `*.intro.md` (those are MCP partials, not standalone docs).

**`docs/_index.json`** — left sidebar tree:

```json
{
  "generated_at": "2026-06-19T00:00:00Z",
  "tree": [
    {
      "title": "Benchmark",
      "children": [
        { "title": "Task families", "path": "benchmark/task-families" },
        { "title": "Evaluation protocol", "path": "benchmark/evaluation-protocol" },
        { "title": "Leaderboard", "path": "benchmark/leaderboard" },
        { "title": "Submission guide", "path": "benchmark/submission-guide" }
      ]
    },
    { "title": "Platform", "children": [ "..." ] },
    {
      "title": "MCP tools",
      "kind": "mcp-tools",
      "children": [
        {
          "title": "compute_volume",
          "path": "mcp/tools/compute_volume",
          "domain": "medical",
          "kind": "mcp-tool"
        }
      ]
    },
    { "title": "Partners", "children": [ "..." ] }
  ]
}
```

**`docs/_search.json`** — pre-built lunr index:

```json
{
  "version": "lunr-2.x",
  "docs":  { "<path>": { "title": "...", "snippet": "..." } },
  "index": "<serialized-lunr-index>"
}
```

Both files are **committed to git**. CI runs the build scripts in dry-run mode and fails on diff.

### 4.4 — MCP tool virtual docs

For each MCP tool `<name>`, two real files exist in the repo:

```
docs/mcp/tools/<name>.schema.json     # auto-generated by build_mcp_docs.py
docs/mcp/tools/<name>.intro.md        # human-written; required sections below
```

`intro.md` **must contain** these two top-level sections (verbatim header text):

```markdown
## When to use

<one or two paragraphs on context / when this tool is the right choice>

## Example

<one or more code blocks showing call shape and expected response>
```

Additional sections after these two are passed through verbatim.

When the SPA navigates to `?doc=mcp/tools/<name>`, it composes a virtual doc by stacking:

```
# <name>                                       (from schema.name)
*<schema.description>*

## When to use                                 (from intro.md)
…
## Example                                     (from intro.md)
…
<any additional intro.md sections>             (passed through)

## Parameters                                  (synthesized from schema.parameters)
| Name | Type | Required | Default | Description |

## Returns                                     (synthesized from schema.returns)
…

## Source                                      (auto-added)
Link to sci3d_agent source.
```

If `intro.md` is missing, the page renders Header + Parameters + Returns + Source with a banner: **"Intro pending — schema only"**.

### 4.5 — Internal link rewriting

The markdown renderer post-processes anchor tags inside doc body:

| Markdown source | Rewritten to | Behavior |
|---|---|---|
| `[t](other.md)` | `<a href="?doc=<dir>/other">` | SPA navigation, resolved relative to current doc's directory |
| `[t](../platform/quickstart.md)` | `<a href="?doc=platform/quickstart">` | SPA navigation, cross-directory |
| `[t](#step-1)` | `<a href="#step-1">` | In-page anchor (no rewrite) |
| `[t](https://...)` | `<a target="_blank" rel="noopener">` | External link, new tab |
| `[t](/spec/...)` | unchanged | Absolute repo paths |
| `[[wiki-link]]` | look up by title in `_index.json`; match → SPA link; no match → `<span class="broken-link">` with tooltip | Internal doc references by title |

### 4.6 — Markdown rendering pipeline

```
.md source
  ↓ marked.js (CommonMark + GFM: tables, strikethrough, task lists)
  ↓ custom walker — apply link-rewrite rules (§4.5) + tag code blocks
  ↓ DOM injection into middle column
  ↓ Prism.highlightAllUnder — languages bundled: python, bash, json, javascript,
    typescript, dockerfile
  ↓ TOC extraction — scan h2/h3, build right-column outline
  ↓ scrollspy — bind IntersectionObserver to outline entries
```

**v1 explicitly does not support:** mermaid diagrams, KaTeX math, custom admonitions beyond GFM alerts. Deferred to v2.

All vendor JS is local under `viewer/vendor/`. **No CDN requests at runtime.**

### 4.7 — Search

- `lunr.js` (~30 KB gzip) + `docs/_search.json` (~200 KB target, hard cap 500 KB) loaded on docs.html bootstrap.
- Keyboard shortcuts: `/` or `?` focuses search box, `Esc` blurs, `↑` / `↓` navigate results, `Enter` opens.
- Results show title + 60-character context snippet with query terms highlighted.
- Result URLs use `?q=<query>` so a search state is shareable.

### 4.8 — Error states

| Condition | UX |
|---|---|
| `?doc=` path not found | 404 panel + "Try Quickstart" link |
| `_index.json` fetch fails | Red banner at top + fallback to rendering the requested doc without tree |
| MCP tool `intro.md` missing | Banner "Intro pending — schema only" + render skeleton (Header + Parameters + Returns + Source) |
| Internal link target unresolved | Inline `<span class="broken-link">` with strike-through + tooltip showing the failed lookup |
| Search returns zero results | "No matches. Try …" with 3 most-recent docs as fallback |

---

## 5 — Other Pages

### `viewer/leaderboard.html`

- Data source: `manifest.data.leaderboard` → `data/leaderboard.json`. v0.1.0 ships an empty array.
- UI:
  - Table headers: `Submission · Profile · Task family · Score · Date · Trace`
  - Two filter dropdowns: `Profile (coding | vision | fusion)`, `Task family (V2 | V3 | V4 | V6 | Genesis_VP | Medical_Spatial)`.
  - Client-side sort by `(family, -score)`.
- Empty state: large "No submissions yet. **6 reserved slots →** *Submit yours*" → links to `?doc=benchmark/submission-guide`.

### `viewer/assets.html` and `viewer/qagen.html`

Both are extracted from ScienceVision's monolithic `index.html` panes into standalone pages. Behavior preserved; the only structural change is the **manifest-driven** data-path indirection.

### Per-modality pages

`molstar.html`, `niivue.html`, `cornerstone.html`, `gltf.html`, `gltf_anim.html`, `urdf.html`, `usda.html`, `usdz.html`, `occt.html`, `cad-edrawings.html`, `cad-native.html`, `pointcloud.html`, `citymap.html`, `citymap-photo.html`, `bandplot.html`, `field3d.html`, `genesis_view.html`, `gdsii.html`, `mpbctl.html`, `photonics.html`, `saturn.html`, `seed3d_pair.html`, `vqa.html`, `video.html`, `match3d.html`.

These are mirrored verbatim. The **only** change: each file's `<head>` is patched to include `<script src="shared/manifest.js"></script>`, and any hardcoded data paths inside are replaced with `window.Manifest.data.<key>` references. The body and renderer logic remain untouched.

`pointcloud.html` is **retained as a modality renderer** even though `pointcloud` ceases to be a top-level domain (per issue #8). Any domain card may invoke it via `?viewer=pointcloud&src=...`.

---

## 6 — Data Flow & Manifest Schema

```
                ┌──────────────────────────────────┐
                │ viewer/manifest.json             │
                │  brand · version · counts        │
                │  data{} · docs{} · domains[]     │
                └────────────────┬─────────────────┘
                                 ↓ fetch on page load
                ┌──────────────────────────────────┐
                │ viewer/shared/manifest.js        │
                │  exposes window.Manifest         │
                │  (Object.freeze, fail-fast       │
                │   on missing required keys)      │
                └────────────────┬─────────────────┘
                                 ↓
       ┌──────────┬──────────────┴──────────────┬─────────────┐
       ↓          ↓                             ↓             ↓
   index.html  assets.html                qagen.html     docs.html
   counts +    M.data.assets_index        M.data.qagen_  M.docs.tree_index
   brand                                  index           M.docs.search_index
                                                          M.docs.base_url
                                                          M.domains[]
```

### Manifest schema v1

```json
{
  "$schema": "viewer/shared/manifest.schema.json",
  "brand": "BabyVision-v2",
  "version": "0.1.0",
  "generated_at": "2026-06-19T00:00:00Z",
  "counts": {
    "assets": 0,
    "qagen": 0,
    "mcp_tools": 0,
    "leaderboard_entries": 0
  },
  "data": {
    "assets_index": "data/assets.index.json",
    "qagen_index":  "data/qagen_index.json",
    "leaderboard":  "data/leaderboard.json"
  },
  "docs": {
    "tree_index":   "docs/_index.json",
    "search_index": "docs/_search.json",
    "base_url":     "docs/"
  },
  "domains": [
    { "id": "biology",  "label": "Biology",  "color": "#f78166", "shipped": false },
    { "id": "medical",  "label": "Medical",  "color": "#56d364", "shipped": true  },
    { "id": "embodied", "label": "Embodied", "color": "#ffa657", "shipped": true  }
  ]
}
```

- `viewer/shared/manifest.schema.json` is a JSON Schema; CI validates `manifest.json` against it on every change.
- `domains[].shipped` drives § Domain coverage colored vs dimmed state.
- Exact `domains[]` entries and `shipped` flags depend on issues #6 / #7 / #8 resolutions.

### Immutability

All viewer JS treats `window.Manifest` as frozen (`Object.freeze`); page code never mutates it. Per-page state derives from manifest fields but is held in page-scope objects.

---

## 7 — Testing & Acceptance

### 7.1 — Static checks (CI must pass)

| Check | Action on fail |
|---|---|
| `manifest.json` validates against `manifest.schema.json` | block PR |
| `docs/_index.json` and `docs/_search.json` not stale (re-run scripts, diff must be empty) | block PR |
| Every `mcp/tools/*.schema.json` has a paired `*.intro.md` | warn only (allow staged backfill) |
| All internal markdown links resolvable | warn for broken links; block on absolute repo-path 404s |

### 7.2 — Smoke tests (Playwright, in `tests/viewer/`)

- `index.html`
  - Five nav items render
  - Brand text equals `BabyVision-v2`
  - Module launcher renders three tiles with non-zero counts (or "—" placeholders)
  - Six task-family cards render with their keyword subtitles
- `docs.html?doc=platform/quickstart`
  - H1 renders
  - No console errors
  - Left tree highlights the current doc
  - Right TOC contains at least one H2 entry
- `docs.html?doc=mcp/tools/<sample>`
  - Either the full virtual doc renders (`Parameters` table present), OR an "Intro pending" banner appears with the schema-only skeleton
- `docs.html` keyboard `/`
  - Search box becomes the focused element
  - Typing `executable gt` produces ≥1 result
- Tree leaf click
  - Middle column re-renders with new doc
  - URL updates via `pushState` and is shareable

### 7.3 — Visual baseline (manual diff in v1, automate in v2)

Stored under `tests/visual/baseline/`:

- `index.png`
- `docs-quickstart.png`
- `docs-mcp-sample.png`

### 7.4 — v1 acceptance checklist

- [ ] Four primary pages reachable from any other page: Home, Doc, Domain 3D Assets, QA Tasks
- [ ] Docs full-text search returns results
- [ ] Manifest decoupling proven: replacing `manifest.json.data` paths with a different dataset leaves the viewer functional
- [ ] All eleven domain tiles render with shipped vs partner-in-progress states
- [ ] § Compare renders the "TBD — tracked in #5" placeholder
- [ ] Leaderboard empty state renders with the "Reserved slots" message

---

## 8 — Visual Language

Inherited from ScienceVision's `viewer/shared/style.css`. Captured here so the design has no implicit dependency on the upstream file.

### Palette (GitHub dark)

```
--bg        #0d1117     canvas
--bg2       #161b22     card / topbar
--fg        #c9d1d9     body text
--fg-dim    #8b949e     subtle copy / meta lines
--accent    #58a6ff     link color, hover border
--border    #30363d     1px hairlines
```

### Per-domain accents (used in domain mosaic + section H2 colors)

```
biology               #f78166
medical               #56d364
embodied              #ffa657
robotics              #ff9a47   (deeper orange, distinct from embodied)
geography             #79c0ff
astronomy             #d2a8ff
topological_photonics #ff7b72
photonics             #ffd166
simulation            #a5d6ff
climate               #3fb950   (formerly --domain-atmos)
industrial            #c79aff   (lavender, distinct from astronomy)
```

### Typography

- Body: `-apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", sans-serif`, 14px / 1.5
- Monospace: `ui-monospace, SFMono-Regular, Menlo, monospace` — for code, asset names, badges

### Layout patterns

- Topbar: sticky, `bg2` background, 1px `border` border-bottom, 12px / 24px padding.
- Cards: `bg2` background, 6px border-radius, 1px `border` border. Hover: border-color shifts to `accent`, transform `translateY(-1px)`, 150ms transition.
- Section H2: 14px font-size, uppercase, 0.05em letter-spacing, colored by per-domain accent.
- Grids: `repeat(auto-fill, minmax(180px, 1fr))`, 12px gap.

---

## 9 — Decision Log (forward references)

| # | Decision | Status | Reference |
|---|---|---|---|
| 1 | Viewer code lives in BV2 (flat, not submodule) with manifest decoupling | confirmed | brainstorming Q1 → A |
| 2 | Client-side markdown SPA (no SSG) | confirmed | brainstorming Q2 → A |
| 3 | MCP docs use schema + intro hybrid (`<name>.schema.json` + `<name>.intro.md`) | confirmed | brainstorming Q2 → A3 |
| 4 | Homepage IA = global nav + in-page sections (Y option) | confirmed | brainstorming Q3 → Y |
| 5 | Brand text = `BabyVision-v2` | confirmed | follow-up |
| 6 | Hero wording = "professional domains" (not "scientific") | confirmed | follow-up |
| 7 | In-page nav = 3-tile module launcher (Doc / Domain 3D Assets / QA Tasks) | confirmed | follow-up |
| 8 | Domain coverage strategy = D2 (show all 11 with shipped flag) | confirmed | follow-up |
| 9 | Three-column docs layout with right-side TOC | confirmed | §4 review |
| 10 | MCP `intro.md` must contain `## When to use` + `## Example` | confirmed | §4 review |
| 11 | Build outputs committed to git, CI fails on diff | confirmed | §4 review |
| 12 | lunr.js full-text search in v1 | confirmed | §4 review |
| 13 | Leaderboard ships v1 as empty-state shell | confirmed | §5 review |
| 14 | Compare table populated separately (GH issue #5) | tracked | GH #5 |
| 15 | industrial vs simulation kept as two domains (recommended) | resolved (Plan 3) | GH #6 |
| 16 | Genesis_VP → new `embodied` domain; `physics` → `climate` (recommended) | resolved (Plan 3) | GH #7 |
| 17 | Drop `pointcloud` as top-level domain, retain as renderer | resolved (Plan 3) | GH #8 |

---

## 10 — Out of Scope (deferred to v2 or later)

- Mermaid diagrams in docs
- KaTeX math rendering
- Visual regression auto-diff
- Per-tool playground (run MCP tool against demo data from the browser)
- Multi-language docs (zh / en split)
- RSS / changelog feed
- Authentication (none expected; viewer stays public-static)
