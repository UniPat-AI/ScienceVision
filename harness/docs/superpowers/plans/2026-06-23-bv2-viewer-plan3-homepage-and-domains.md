# BabyVision-v2 Viewer — Plan 3: Homepage + Module Pages + Domain Taxonomy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the visible BabyVision-v2 frontend — a brand-new homepage at `viewer/index.html` (hero · 3-tile launcher · Tasks · Motivation · Domain coverage · Compare placeholder · Get started), extracted module pages (`viewer/assets.html`, `viewer/qagen.html`, `viewer/leaderboard.html`), and the final 11-domain taxonomy in `manifest.json`. Plus a tiny per-modality sweep for the two pages that hardcode data paths.

**Architecture:** Static-only, manifest-driven. The new homepage reads `window.Manifest.domains[]` to render the Domain coverage mosaic; shipped vs partner-in-progress comes straight from manifest. `assets.html` and `qagen.html` are extracted from ScienceVision's monolithic `index.html` panes — same renderers, same JSON inputs, but standalone pages routed via the new global nav. `leaderboard.html` ships as an empty-state shell pointing at the submission guide.

**Tech Stack:** Vanilla browser JS (no module loader, no transpile). marked / Prism / lunr stay docs-only. Plan 1's `window.Manifest` consumed everywhere; no new dependencies.

## Global Constraints

- Python floor `>=3.10` (no 3.11+ syntax); not exercised heavily this plan but build scripts touched.
- File-size cap: ≤450 lines under `viewer/` mirrored material; ≤800 lines elsewhere. The new `viewer/index.html` budget is **≤500 lines** by exemption (it's the homepage shell, larger than typical viewer pages).
- Commit message format: `feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:` / `ci:`. No `Co-Authored-By:` trailer.
- Immutable patterns: never mutate `window.Manifest` (it's `Object.freeze`d).
- No CDN at runtime: every script load is local.
- No build chain at the served path: every page works via `python3 -m http.server --directory .`.
- Branch: `feat/viewer-platform-design` (continues Plan 2's branch).
- All build outputs (`docs/_index.json`, `docs/_search.json`) regenerate cleanly via `--check` after any docs change.
- All implementers run on `sonnet` with the Hard Scope Lockdown clause (see Task 1 for canonical wording).
- **DO NOT TOUCH `docs/TODO.md`** in any task — the user maintains it directly.

## Domain Taxonomy (locked for Plan 3 — GH #6/#7/#8 recommended resolutions)

Per the design discussion + brainstorm, this plan adopts the **recommended** resolution of all three open domain issues:

- **#6** industrial vs simulation — **keep as two separate domains** (different audiences: design vs simulation).
- **#7** Genesis_VP belongs to → **`embodied`** (new domain). The previous `physics` (atmospheric/climate `.nc`) renames to **`climate`** so embodied physics doesn't collide with climate physics.
- **#8** **drop `pointcloud` as a top-level domain.** Treat as a modality (renderer). Existing pointcloud assets fold into `geography` (LiDAR is the dominant case). `viewer/pointcloud.html` stays as a renderer that any domain card can invoke via `?viewer=pointcloud&src=...`.

Final **11 domains** in canonical homepage order:

| # | id | label | color (hex) | shipped (v0.1.0) |
|---|---|---|---|---|
| 1 | `biology` | Biology | `#f78166` | false |
| 2 | `medical` | Medical | `#56d364` | true |
| 3 | `embodied` | Embodied | `#ffa657` | true |
| 4 | `robotics` | Robotics | `#ff9a47` | false |
| 5 | `geography` | Geography | `#79c0ff` | false |
| 6 | `astronomy` | Astronomy | `#d2a8ff` | false |
| 7 | `topological_photonics` | Topological Photonics | `#ff7b72` | false |
| 8 | `photonics` | Photonics | `#ffd166` | false |
| 9 | `simulation` | Simulation | `#a5d6ff` | false |
| 10 | `climate` | Climate | `#3fb950` | false |
| 11 | `industrial` | Industrial | `#c79aff` | false |

`#c79aff` for industrial (was `#d2a8ff` in the SV palette but that collides with the new astronomy color); `#ff9a47` for robotics (was `#ffa657` which conflicts with embodied). Both choices are within the same tonal family as the originals.

## Frozen Plan 1+2 Interfaces (consumed by Plan 3 pages)

```javascript
// Plan 1 + 1.5
window.Manifest.brand                  // "BabyVision-v2"
window.Manifest.version                // "0.1.0"
window.Manifest.domains                // [{id, label, color, shipped}, ...]  ← becomes 11 in Task 1
window.Manifest.urls.data.assets_index
window.Manifest.urls.data.qagen_index
window.Manifest.urls.data.leaderboard
window.Manifest.urls.docs.tree_index
window.Manifest.urls.docs.search_index
window.Manifest.urls.docs.base_url
window.ManifestReady                   // Promise resolving to the same object
window:manifest:ready                  // CustomEvent dispatched after load

// Plan 2 (docs SPA — Plan 3 uses none directly, only links to docs.html?doc=...)
```

---

## File Structure (cumulative across Plan 3)

| File | Action | Responsibility | Task |
|---|---|---|---|
| `viewer/manifest.json` | MODIFY | `domains[]` expands from 2 to 11; `version` bumps to `0.1.1` | 1 |
| `viewer/manifest.local.example.json` | MODIFY | Same 11-domain shape (partner can keep a subset) | 1 |
| `docs/superpowers/specs/2026-06-19-bv2-viewer-platform-design.md` | MODIFY | §3 Domain coverage: lock the 11 final entries (replace "TBD" astronomy color) | 1 |
| `viewer/assets.html` | CREATE | Standalone 3D-Assets page; extracted from SV `index.html` pane-assets; renders via the existing `shared/sections/...` logic | 2 |
| `viewer/qagen.html` | CREATE | Standalone QA-Gen Tasks page; extracted from SV `index.html` pane-qagen | 2 |
| `viewer/leaderboard.html` | CREATE | Empty-state shell; reads `Manifest.urls.data.leaderboard`; renders "No submissions yet" with link to submission guide | 2 |
| `viewer/index.html` | REWRITE | The new BabyVision-v2 homepage per spec §3 (replaces the SV-mirrored monolith) | 3 |
| `viewer/shared/style.css` | MODIFY | Append `.bv2-*` classes (hero, module-launcher, task-card, domain-tile, get-started-card, compare-placeholder) | 3 |
| `viewer/shared/bv2-home.js` | CREATE | Homepage JS: render Domain coverage mosaic from `Manifest.domains[]`; populate launcher tile counts | 3 |
| `viewer/vqa.html` | MODIFY | Replace 5 hardcoded `/viewer/*.json` fetches with `Manifest.urls.*` (or note as a no-op if VQA out of BV2 scope) | 4 |
| `viewer/seed3d_pair.html` | MODIFY | Same — switch hardcoded `/viewer/synthetic_negative_assets.json` etc. to `Manifest.urls.*` | 4 |
| `tests/test_manifest_domain_taxonomy.py` | CREATE | pytest: locks the 11 domain ids/order; locks the 2 shipped flags | 1 |
| `.github/workflows/manifest-check.yml` | MODIFY | Add path triggers for new HTML pages + the new domain-taxonomy test | 5 |

## Out of scope for Plan 3 (deferred to later)

- `index.html` Compare table content — still tracked in GH #5; ships as placeholder.
- Mobile drawer / hamburger for the new homepage (responsive CSS hides sidebar+TOC on narrow viewports; mobile UX polish later).
- Leaderboard data population — empty-state only; `data/leaderboard.json` is still `[]`.
- Sweeping every per-modality page to include `<script src="shared/manifest.js">` — only patches the 2 pages that actually fetch hardcoded data JSONs. The other ~23 pages take `?file=` URL params and don't need Manifest.
- Auto-extracting MCP tool schemas from `sci3d_agent/` (still future).
- Mermaid / KaTeX / Playwright headless tests (still deferred).

---

## Hard Scope Lockdown — paste this clause verbatim into every implementer dispatch

> Hard scope lockdown:
> - Repo: `/Users/sue/Research/BabyVision-v2`
> - Branch: `feat/viewer-platform-design` (already checked out; STAY HERE)
> - Files allowed to modify: ONLY the files named in the Files: block of this brief
> - You MUST NOT: switch/create/cherry-pick branches, `git push`, `git reset`, modify any other file
> - **DO NOT TOUCH `docs/TODO.md`** — user maintains it directly; may have unstaged edits
> - Untracked items in working tree (e.g. `examples/02_medical_spatial_gt/data/`) — DO NOT TOUCH
> - Stop and report BLOCKED if you find yourself wanting to do any of the above

---

## Task 1: Domain taxonomy update

**Files:**
- Modify: `viewer/manifest.json` (bump version 0.1.0 → 0.1.1; replace `domains[]` with the full 11-entry list)
- Modify: `viewer/manifest.local.example.json` (mirror the 11-domain shape; partners can override `shipped` flags)
- Modify: `docs/superpowers/specs/2026-06-19-bv2-viewer-platform-design.md` (replace the astronomy TBD color + lock the final taxonomy in §8 Visual Language)
- Create: `tests/test_manifest_domain_taxonomy.py`

**Interfaces:**
- Consumes: existing `viewer/shared/manifest.schema.json` (no schema change — the array shape is already correct)
- Produces:
  - `Manifest.domains[]` becomes a frozen array of 11 entries in canonical homepage order.
  - pytest `tests/test_manifest_domain_taxonomy.py` locks the exact id list, color list, and shipped-flag set so accidental drift fails CI.

- [ ] **Step 1: Update `viewer/manifest.json`**

Replace the existing `version` and `domains` fields. Current state:

```json
  "version": "0.1.0",
  ...
  "domains": [
    { "id": "medical",  "label": "Medical",  "color": "#56d364", "shipped": true  },
    { "id": "embodied", "label": "Embodied", "color": "#ffa657", "shipped": true  }
  ]
```

New state (full file shown for context; only these two regions change):

```json
{
  "$schema": "shared/manifest.schema.json",
  "brand": "BabyVision-v2",
  "version": "0.1.1",
  "generated_at": "2026-06-19T00:00:00Z",
  "counts": {
    "assets": 0,
    "qagen": 0,
    "mcp_tools": 0,
    "leaderboard_entries": 0
  },
  "data": {
    "assets_index": "../data/assets.index.json",
    "qagen_index":  "../data/qagen_index.json",
    "leaderboard":  "../data/leaderboard.json"
  },
  "docs": {
    "tree_index":   "../docs/_index.json",
    "search_index": "../docs/_search.json",
    "base_url":     "../docs/"
  },
  "domains": [
    { "id": "biology",               "label": "Biology",               "color": "#f78166", "shipped": false },
    { "id": "medical",               "label": "Medical",               "color": "#56d364", "shipped": true  },
    { "id": "embodied",              "label": "Embodied",              "color": "#ffa657", "shipped": true  },
    { "id": "robotics",              "label": "Robotics",              "color": "#ff9a47", "shipped": false },
    { "id": "geography",             "label": "Geography",             "color": "#79c0ff", "shipped": false },
    { "id": "astronomy",             "label": "Astronomy",             "color": "#d2a8ff", "shipped": false },
    { "id": "topological_photonics", "label": "Topological Photonics", "color": "#ff7b72", "shipped": false },
    { "id": "photonics",             "label": "Photonics",             "color": "#ffd166", "shipped": false },
    { "id": "simulation",            "label": "Simulation",            "color": "#a5d6ff", "shipped": false },
    { "id": "climate",               "label": "Climate",               "color": "#3fb950", "shipped": false },
    { "id": "industrial",            "label": "Industrial",            "color": "#c79aff", "shipped": false }
  ]
}
```

- [ ] **Step 2: Validate the new manifest against the schema**

```bash
cd /Users/sue/Research/BabyVision-v2
python tools/validate_manifest.py
```

Expected: `OK: …/viewer/manifest.json validates against …/viewer/shared/manifest.schema.json` exit 0.

If the validator complains about the version regex, double-check `0.1.1` matches `^\d+\.\d+\.\d+([-+].*)?$` (it does). If it complains about a `color` pattern, check no hex string deviates from `#xxxxxx` shape.

- [ ] **Step 3: Update `viewer/manifest.local.example.json`**

Edit the file. Bump its `version` to `0.1.1` and replace its `domains` array with the same 11 entries from Step 1, but set every `shipped` to `false` (a partner-local override defaults to nothing shipped — the partner enables tiles for the subset they ship):

```json
  "domains": [
    { "id": "biology",               "label": "Biology",               "color": "#f78166", "shipped": false },
    { "id": "medical",               "label": "Medical",               "color": "#56d364", "shipped": false },
    { "id": "embodied",              "label": "Embodied",              "color": "#ffa657", "shipped": false },
    { "id": "robotics",              "label": "Robotics",              "color": "#ff9a47", "shipped": false },
    { "id": "geography",             "label": "Geography",             "color": "#79c0ff", "shipped": false },
    { "id": "astronomy",             "label": "Astronomy",             "color": "#d2a8ff", "shipped": false },
    { "id": "topological_photonics", "label": "Topological Photonics", "color": "#ff7b72", "shipped": false },
    { "id": "photonics",             "label": "Photonics",             "color": "#ffd166", "shipped": false },
    { "id": "simulation",            "label": "Simulation",            "color": "#a5d6ff", "shipped": false },
    { "id": "climate",               "label": "Climate",               "color": "#3fb950", "shipped": false },
    { "id": "industrial",            "label": "Industrial",            "color": "#c79aff", "shipped": false }
  ]
```

Bump version on the example too:

```json
  "version": "0.1.1",
```

Then validate:

```bash
python tools/validate_manifest.py --manifest viewer/manifest.local.example.json
```

Expected: `OK:` exit 0.

- [ ] **Step 4: Write the taxonomy lock test**

Create `tests/test_manifest_domain_taxonomy.py`:

```python
"""Pin the 11-domain taxonomy to canonical order, ids, colors, and shipped flags.

Accidental reordering, renaming, or color drift fails CI. To intentionally
change the taxonomy, update this test in the same commit.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "viewer" / "manifest.json"

# Canonical 11-domain order (homepage display order).
EXPECTED_DOMAINS = [
    ("biology",               "Biology",               "#f78166", False),
    ("medical",               "Medical",               "#56d364", True ),
    ("embodied",              "Embodied",              "#ffa657", True ),
    ("robotics",              "Robotics",              "#ff9a47", False),
    ("geography",             "Geography",             "#79c0ff", False),
    ("astronomy",             "Astronomy",             "#d2a8ff", False),
    ("topological_photonics", "Topological Photonics", "#ff7b72", False),
    ("photonics",             "Photonics",             "#ffd166", False),
    ("simulation",            "Simulation",            "#a5d6ff", False),
    ("climate",               "Climate",               "#3fb950", False),
    ("industrial",            "Industrial",            "#c79aff", False),
]


def _load_domains() -> list[dict]:
    with MANIFEST_PATH.open() as fh:
        return json.load(fh)["domains"]


def test_domain_count():
    assert len(_load_domains()) == 11


def test_domain_order_and_ids():
    actual = [(d["id"], d["label"], d["color"], d["shipped"]) for d in _load_domains()]
    assert actual == EXPECTED_DOMAINS


def test_only_medical_and_embodied_shipped_in_v0_1_1():
    shipped = [d["id"] for d in _load_domains() if d["shipped"]]
    assert sorted(shipped) == ["embodied", "medical"]


def test_no_duplicate_colors():
    colors = [d["color"] for d in _load_domains()]
    assert len(colors) == len(set(colors)), \
        f"duplicate colors in domains[]: {colors}"


def test_all_ids_are_snake_case():
    import re
    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for d in _load_domains():
        assert pattern.match(d["id"]), f"bad id: {d['id']}"
```

- [ ] **Step 5: Run the test (must pass after Steps 1-3)**

```bash
pytest tests/test_manifest_domain_taxonomy.py -v
```

Expected: 5/5 PASS, pristine output.

- [ ] **Step 6: Update the design spec**

Edit `docs/superpowers/specs/2026-06-19-bv2-viewer-platform-design.md`. Two regions to update:

(a) In §3 § Domain coverage, the "pending those issues" comment becomes resolved. Replace:

```markdown
- The exact list and per-tile state depend on issues #6, #7, #8 outcomes. Spec target list (pending those issues):
    ```
    biology · medical · embodied · robotics · geography · astronomy ·
    topological_photonics · photonics · simulation · climate · industrial
    ```
```

with:

```markdown
- Locked in Plan 3 per recommended resolutions of GH #6/#7/#8:
    ```
    biology · medical · embodied · robotics · geography · astronomy ·
    topological_photonics · photonics · simulation · climate · industrial
    ```
- v0.1.1 ships `medical` and `embodied` as `shipped: true`; the other 9 render as dim "partner-in-progress" tiles.
```

(b) In §8 Visual Language → Per-domain accents, replace the existing table fragment. Find the lines:

```
biology               #f78166
medical               #56d364
embodied              #ffa657
robotics              #ffa657   (shares with embodied — verify in implementation)
industrial            #d2a8ff
geography             #79c0ff
astronomy             TBD
topological_photonics #ff7b72
photonics             #ffd166
simulation            #a5d6ff
climate               #3fb950   (formerly --domain-atmos)
```

…and replace with:

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

(c) In §9 Decision Log, update rows #15, #16, #17 status from `open` to `resolved (Plan 3)`. Find the rows and edit the Status column only.

- [ ] **Step 7: Commit**

```bash
cd /Users/sue/Research/BabyVision-v2
git add viewer/manifest.json viewer/manifest.local.example.json \
        docs/superpowers/specs/2026-06-19-bv2-viewer-platform-design.md \
        tests/test_manifest_domain_taxonomy.py
git status
git commit -m "feat(viewer): expand domains to canonical 11 + lock taxonomy test"
```

Verify `git show HEAD --stat` shows exactly 4 files modified/added (no `docs/TODO.md`).

---

## Task 2: Module destination pages (assets / qagen / leaderboard)

**Files:**
- Create: `viewer/assets.html`
- Create: `viewer/qagen.html`
- Create: `viewer/leaderboard.html`

**Interfaces:**
- Consumes: `window.Manifest.urls.data.assets_index`, `qagen_index`, `leaderboard`; `window.Manifest.domains[]` for the assets-page domain headings.
- Produces: three standalone pages reachable from the new homepage's top nav.

**Context note**: These pages exist as **destination shells** for the new homepage's navigation. They are NOT full reimplementations of the SV pane-assets / pane-qagen logic. The SV-mirrored renderers (`molstar.html`, `niivue.html`, etc.) stay intact and are reached from `assets.html` via the existing data-flow. For v0.1.1, these pages render a **minimum viable empty-state** that proves the routing works and provides the right messaging until real data lands.

- [ ] **Step 1: Create `viewer/assets.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>3D Assets — BabyVision-v2</title>
<link rel="stylesheet" href="shared/style.css">
</head>
<body>
<header class="topbar">
  <div>
    <a href="index.html" class="home-link" style="color:inherit;text-decoration:none">
      <strong id="brand">BabyVision-v2</strong>
    </a>
    <span style="color:var(--fg-dim);margin-left:8px;font-size:12px">/ 3D Assets</span>
  </div>
  <div class="topbar-right">
    <nav class="bv2-globalnav" aria-label="Global">
      <a href="index.html">Home</a>
      <a href="docs.html">Doc</a>
      <a href="assets.html" aria-current="page" class="is-active">Domain 3D Assets</a>
      <a href="qagen.html">QA Tasks</a>
      <a href="leaderboard.html">Leaderboard</a>
    </nav>
    <div class="meta" id="counts">loading…</div>
  </div>
</header>
<main style="padding:24px;max-width:1400px;margin:0 auto">
  <h1 style="font-size:24px;margin:0 0 8px">3D Assets</h1>
  <p style="color:var(--fg-dim);margin:0 0 24px;max-width:760px">
    Browse the 3D assets indexed by BabyVision-v2 across professional domains.
    Each asset opens in a domain-specific in-browser renderer
    (Mol* · Niivue · Cornerstone · Three.js · OCCT best-effort)
    or as a raw download where in-browser rendering isn't viable.
  </p>
  <section id="domain-grid" class="bv2-asset-domain-grid" aria-label="Domains"></section>
  <p id="asset-empty" class="empty" hidden>
    No asset index data available yet. The first asset batches land with the
    medical and embodied demo data.
  </p>
</main>
<script src="shared/manifest.js"></script>
<script>
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function renderDomainGrid(domains) {
    const grid = $('domain-grid');
    grid.innerHTML = '';
    for (const d of domains) {
      const card = document.createElement('a');
      card.className = 'bv2-asset-domain' + (d.shipped ? '' : ' is-pending');
      card.style.setProperty('--domain-accent', d.color);
      card.href = d.shipped
        ? ('index.html#' + encodeURIComponent(d.id))   // placeholder until pane wiring lands
        : 'javascript:void(0)';
      card.innerHTML =
        '<div class="bv2-asset-domain-label">' + d.label + '</div>' +
        '<div class="bv2-asset-domain-meta">' +
        (d.shipped ? 'shipped' : 'partner-in-progress') +
        '</div>';
      grid.appendChild(card);
    }
  }

  function loadAssetCount() {
    if (!window.Manifest || !window.Manifest.urls) return;
    fetch(window.Manifest.urls.data.assets_index, { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then((j) => {
        const meta = $('counts');
        const n = Array.isArray(j) ? j.length : (j.count || (j.assets ? j.assets.length : 0));
        meta.textContent = n + ' assets · ' + window.Manifest.brand + ' v' + window.Manifest.version;
      })
      .catch(() => {
        $('counts').textContent = window.Manifest.brand + ' v' + window.Manifest.version;
        $('asset-empty').hidden = false;
      });
  }

  window.ManifestReady.then((m) => {
    renderDomainGrid(m.domains);
    loadAssetCount();
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 2: Create `viewer/qagen.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>QA Tasks — BabyVision-v2</title>
<link rel="stylesheet" href="shared/style.css">
</head>
<body>
<header class="topbar">
  <div>
    <a href="index.html" class="home-link" style="color:inherit;text-decoration:none">
      <strong id="brand">BabyVision-v2</strong>
    </a>
    <span style="color:var(--fg-dim);margin-left:8px;font-size:12px">/ QA Tasks</span>
  </div>
  <div class="topbar-right">
    <nav class="bv2-globalnav" aria-label="Global">
      <a href="index.html">Home</a>
      <a href="docs.html">Doc</a>
      <a href="assets.html">Domain 3D Assets</a>
      <a href="qagen.html" aria-current="page" class="is-active">QA Tasks</a>
      <a href="leaderboard.html">Leaderboard</a>
    </nav>
    <div class="meta" id="counts">loading…</div>
  </div>
</header>
<main style="padding:24px;max-width:1400px;margin:0 auto">
  <h1 style="font-size:24px;margin:0 0 8px">QA Tasks</h1>
  <p style="color:var(--fg-dim);margin:0 0 24px;max-width:760px">
    Question-answering task families generated by the BabyVision-v2 deterministic
    pipeline. Each family targets a distinct reasoning skill; every task ships
    with executable ground truth (no LLM-as-judge).
  </p>
  <section class="bv2-task-grid" aria-label="Task families">
    <article class="bv2-task-card" data-family="V2">
      <h2>V2 — Cross-section / topology</h2>
      <p class="bv2-task-keywords">2D-view → 3D inference · cannot be shortcut by code · geometric</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v2-cross-section--topology">Details →</a></p>
    </article>
    <article class="bv2-task-card" data-family="V3">
      <h2>V3 — Segmentation / referring</h2>
      <p class="bv2-task-keywords">pixel-level GT · SSIM / LPIPS · defends against LLM-as-judge</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v3-segmentation--referring">Details →</a></p>
    </article>
    <article class="bv2-task-card" data-family="V4">
      <h2>V4 — Manipulation / counterfactual</h2>
      <p class="bv2-task-keywords">rotate · slice · deform · geometry-grounded GT</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v4-manipulation--counterfactual">Details →</a></p>
    </article>
    <article class="bv2-task-card" data-family="V6">
      <h2>V6 — Multi-step reasoning</h2>
      <p class="bv2-task-keywords">multi-tool trace · scored on answer + trace fidelity</p>
      <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v6-multi-step-reasoning">Details →</a></p>
    </article>
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
  </section>
  <p style="margin-top:32px;color:var(--fg-dim);font-size:12px">
    Live task-instance data lands when QA-Gen artifacts begin shipping.
    See <a href="docs.html?doc=benchmark/task-families">task-families.md</a> for the canonical definitions.
  </p>
</main>
<script src="shared/manifest.js"></script>
<script>
(function () {
  window.ManifestReady.then((m) => {
    document.getElementById('counts').textContent = m.brand + ' v' + m.version;
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 3: Create `viewer/leaderboard.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Leaderboard — BabyVision-v2</title>
<link rel="stylesheet" href="shared/style.css">
</head>
<body>
<header class="topbar">
  <div>
    <a href="index.html" class="home-link" style="color:inherit;text-decoration:none">
      <strong id="brand">BabyVision-v2</strong>
    </a>
    <span style="color:var(--fg-dim);margin-left:8px;font-size:12px">/ Leaderboard</span>
  </div>
  <div class="topbar-right">
    <nav class="bv2-globalnav" aria-label="Global">
      <a href="index.html">Home</a>
      <a href="docs.html">Doc</a>
      <a href="assets.html">Domain 3D Assets</a>
      <a href="qagen.html">QA Tasks</a>
      <a href="leaderboard.html" aria-current="page" class="is-active">Leaderboard</a>
    </nav>
    <div class="meta" id="counts">loading…</div>
  </div>
</header>
<main style="padding:24px;max-width:1400px;margin:0 auto">
  <h1 style="font-size:24px;margin:0 0 8px">Leaderboard</h1>
  <p style="color:var(--fg-dim);margin:0 0 24px;max-width:760px">
    Submissions are evaluated per task family and per baseline profile (coding ·
    vision · fusion). All entries link back to their reproduce-recipe and tool-trace
    so any third party can re-run them.
  </p>
  <div class="bv2-filters" style="display:flex;gap:12px;margin-bottom:16px">
    <select id="filter-profile" disabled aria-label="Profile filter">
      <option>All profiles</option>
      <option>coding</option>
      <option>vision</option>
      <option>fusion</option>
    </select>
    <select id="filter-family" disabled aria-label="Family filter">
      <option>All families</option>
      <option>V2</option><option>V3</option><option>V4</option><option>V6</option>
      <option>Genesis_VP</option><option>Medical_Spatial</option>
    </select>
  </div>
  <table id="lb-table" class="bv2-lb-table" aria-label="Leaderboard">
    <thead>
      <tr>
        <th>Submission</th><th>Profile</th><th>Family</th>
        <th>Score</th><th>Date</th><th>Trace</th>
      </tr>
    </thead>
    <tbody id="lb-rows"></tbody>
  </table>
  <div id="lb-empty" class="bv2-lb-empty" hidden>
    <p><strong>No submissions yet.</strong></p>
    <p style="color:var(--fg-dim)">
      The 6 task families above are open for baselines. Submit a run by following
      the <a href="docs.html?doc=benchmark/submission-guide">submission guide</a>
      — your entry lands here within 24 hours of evaluation.
    </p>
  </div>
</main>
<script src="shared/manifest.js"></script>
<script>
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function renderRows(rows) {
    const tbody = $('lb-rows');
    tbody.innerHTML = '';
    if (!rows || rows.length === 0) {
      $('lb-table').style.display = 'none';
      $('lb-empty').hidden = false;
      return;
    }
    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + (r.submission || '—') + '</td>' +
        '<td>' + (r.profile    || '—') + '</td>' +
        '<td>' + (r.family     || '—') + '</td>' +
        '<td>' + (r.score      != null ? r.score : '—') + '</td>' +
        '<td>' + (r.date       || '—') + '</td>' +
        '<td>' + (r.trace ? ('<a href="' + r.trace + '">view</a>') : '—') + '</td>';
      tbody.appendChild(tr);
    }
  }

  window.ManifestReady.then((m) => {
    $('counts').textContent = m.brand + ' v' + m.version;
    fetch(m.urls.data.leaderboard, { cache: 'no-cache' })
      .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(renderRows)
      .catch(() => renderRows([]));   // file may not exist yet → empty state
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 4: Syntax check + smoke**

```bash
cd /Users/sue/Research/BabyVision-v2
python3 -c "from html.parser import HTMLParser; [HTMLParser().feed(open(p).read()) for p in ['viewer/assets.html','viewer/qagen.html','viewer/leaderboard.html']]; print('3 HTML OK')"

# Kill any leftover server then restart
pkill -f "http.server 8765" 2>/dev/null
sleep 1
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
for u in /viewer/assets.html /viewer/qagen.html /viewer/leaderboard.html; do
  s=$(curl -sI -o /dev/null -w '%{http_code}' "http://localhost:8765$u")
  echo "$s $u"
done
kill $SERVER_PID 2>/dev/null
```

Expected: 3 HTML OK, 3 × 200 OK.

- [ ] **Step 5: Commit**

```bash
git add viewer/assets.html viewer/qagen.html viewer/leaderboard.html
git status
git commit -m "feat(viewer): module destination pages — assets, qagen, leaderboard"
```

`git show HEAD --stat` should show exactly 3 new files.

---

## Task 3: New homepage `viewer/index.html`

**Files:**
- Modify: `viewer/index.html` (full rewrite — replaces the 1914-line SV-mirrored monolith)
- Modify: `viewer/shared/style.css` (append ~250 lines of `.bv2-*` classes)
- Create: `viewer/shared/bv2-home.js` (homepage JS — render Domain coverage from `Manifest.domains[]`)

**Interfaces:**
- Consumes: `window.Manifest.brand`, `version`, `domains[]`, `urls.docs.base_url`
- Produces: the BabyVision-v2 landing page with the layout from spec §3. Module-launcher tiles include live counts when available (currently 0 across the board, displayed as `—`).

**Context note**: This is the visible deliverable. The page must look clean even when every count is 0 (which is the v0.1.1 state) — i.e. `0` renders as `—` so the page doesn't shout "this thing is empty" when it's actually still early.

- [ ] **Step 1: Write `viewer/shared/bv2-home.js`**

```javascript
// viewer/shared/bv2-home.js — homepage Domain coverage + launcher count rendering.
//
// Run after window.ManifestReady resolves. Populates:
//   #bv2-launcher-doc-count    (no count — doc count is shipped/visible state)
//   #bv2-launcher-assets-count
//   #bv2-launcher-qagen-count
//   #bv2-domain-mosaic         (11 tiles from Manifest.domains[])
//   #counts                    (top-right meta line)

(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }

  function setCount(elId, n) {
    const el = $(elId);
    if (!el) return;
    el.textContent = (n && n > 0) ? n : '—';
  }

  function setShippedCount(elId, domains) {
    const el = $(elId);
    if (!el) return;
    const shipped = domains.filter((d) => d.shipped).length;
    const pending = domains.length - shipped;
    el.textContent = shipped + ' shipped · ' + pending + ' partner-in-progress';
  }

  function renderDomainMosaic(domains) {
    const root = $('bv2-domain-mosaic');
    if (!root) return;
    root.innerHTML = '';
    for (const d of domains) {
      const tile = document.createElement('a');
      tile.className = 'bv2-domain-tile' + (d.shipped ? '' : ' is-pending');
      tile.style.setProperty('--domain-accent', d.color);
      tile.href = d.shipped
        ? ('assets.html#' + encodeURIComponent(d.id))
        : 'javascript:void(0)';
      tile.setAttribute('aria-disabled', d.shipped ? 'false' : 'true');
      tile.innerHTML =
        '<div class="bv2-domain-tile-label">' + d.label + '</div>' +
        '<div class="bv2-domain-tile-state">' +
        (d.shipped ? 'shipped' : 'coming via partner') +
        '</div>';
      root.appendChild(tile);
    }
  }

  window.ManifestReady.then((m) => {
    // Top-right meta.
    const counts = $('counts');
    if (counts) counts.textContent = m.brand + ' v' + m.version;

    // Launcher tile counts.
    setCount('bv2-launcher-assets-count', m.counts.assets);
    setCount('bv2-launcher-qagen-count',  m.counts.qagen);

    // Doc launcher: show MCP tool count (more meaningful than "doc count").
    setCount('bv2-launcher-doc-count', m.counts.mcp_tools);

    // Domain shipped/pending summary in the launcher.
    setShippedCount('bv2-launcher-domain-state', m.domains);

    // Domain mosaic.
    renderDomainMosaic(m.domains);
  });
})();
```

- [ ] **Step 2: Append homepage CSS to `viewer/shared/style.css`**

Append (do NOT replace) at the end of `viewer/shared/style.css`:

```css

/* ── BabyVision-v2 homepage (Plan 3) ────────────────────────────────── */

body.bv2-home main {
  padding: 0;
  max-width: 1200px;
  margin: 0 auto;
}

.bv2-globalnav {
  display: inline-flex;
  gap: 18px;
  margin-right: 16px;
  font-size: 13px;
}
.bv2-globalnav a {
  color: var(--fg-dim);
  text-decoration: none;
  padding: 4px 0;
  border-bottom: 2px solid transparent;
}
.bv2-globalnav a:hover { color: var(--fg); }
.bv2-globalnav a.is-active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.bv2-hero {
  padding: 56px 24px 32px;
  border-bottom: 1px solid var(--border);
}
.bv2-hero-eyebrow {
  color: var(--accent);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
  margin-bottom: 8px;
}
.bv2-hero-eyebrow .version {
  color: var(--fg-dim);
  margin-left: 8px;
  font-weight: 400;
}
.bv2-hero-title {
  font-size: 36px;
  line-height: 1.2;
  margin: 0 0 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.bv2-hero-tagline {
  font-size: 14px;
  color: var(--fg-dim);
  max-width: 680px;
  margin: 0;
}
.bv2-hero-pillars {
  display: flex;
  gap: 24px;
  margin-top: 18px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--fg-dim);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.bv2-hero-pillars > span::before { content: '· '; opacity: 0.4; }
.bv2-hero-pillars > span:first-child::before { content: ''; }

.bv2-launcher {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  padding: 32px 24px;
}
.bv2-launcher-tile {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 18px 20px;
  color: var(--fg);
  text-decoration: none;
  transition: border-color 0.15s, transform 0.15s;
  display: flex; flex-direction: column;
  min-height: 120px;
}
.bv2-launcher-tile:hover {
  border-color: var(--accent);
  text-decoration: none;
  transform: translateY(-1px);
}
.bv2-launcher-tile-head {
  display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 8px;
}
.bv2-launcher-tile-title { font-size: 18px; font-weight: 600; }
.bv2-launcher-tile-count {
  font-size: 22px; font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--accent);
}
.bv2-launcher-tile-meta { font-size: 12px; color: var(--fg-dim); line-height: 1.5; }

.bv2-section {
  padding: 32px 24px;
  border-top: 1px solid var(--border);
}
.bv2-section-label {
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 11px;
  font-weight: 600;
  margin-bottom: 16px;
}
.bv2-section h2 {
  font-size: 22px;
  margin: 0 0 6px;
  font-weight: 600;
}
.bv2-section-intro {
  color: var(--fg-dim);
  max-width: 720px;
  margin: 0 0 24px;
  font-size: 14px;
}

.bv2-task-grid,
.bv2-motivation-grid,
.bv2-getstarted-grid {
  display: grid;
  gap: 16px;
}
.bv2-task-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.bv2-motivation-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.bv2-getstarted-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }

.bv2-task-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px 18px;
}
.bv2-task-card h2 { font-size: 15px; margin: 0 0 6px; font-weight: 600; }
.bv2-task-keywords {
  color: var(--fg-dim);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  margin: 0 0 10px;
}
.bv2-task-cta { font-size: 12px; margin: 0; }

.bv2-motivation-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px 18px;
}
.bv2-motivation-card h3 {
  font-size: 14px;
  margin: 0 0 8px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.bv2-motivation-card p { font-size: 13px; line-height: 1.55; margin: 0 0 8px; color: var(--fg-dim); }
.bv2-motivation-card p:last-child { margin-bottom: 0; }
.bv2-motivation-card a { font-size: 12px; }

.bv2-domain-mosaic {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.bv2-domain-tile {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--domain-accent, var(--accent));
  border-radius: 4px;
  padding: 14px 14px 12px;
  display: block;
  color: var(--fg);
  text-decoration: none;
  transition: border-color 0.15s, transform 0.15s;
}
.bv2-domain-tile:hover:not(.is-pending) {
  border-color: var(--accent);
  transform: translateY(-1px);
  text-decoration: none;
}
.bv2-domain-tile.is-pending {
  opacity: 0.5;
  cursor: default;
}
.bv2-domain-tile-label { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.bv2-domain-tile-state {
  font-size: 10px;
  color: var(--fg-dim);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.bv2-asset-domain-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.bv2-asset-domain {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--domain-accent, var(--accent));
  border-radius: 4px;
  padding: 14px 16px;
  display: block;
  color: var(--fg);
  text-decoration: none;
}
.bv2-asset-domain:hover:not(.is-pending) {
  border-color: var(--accent);
  text-decoration: none;
}
.bv2-asset-domain.is-pending { opacity: 0.5; cursor: default; }
.bv2-asset-domain-label { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.bv2-asset-domain-meta {
  font-size: 11px;
  color: var(--fg-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.bv2-compare-placeholder {
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 32px 24px;
  text-align: center;
  color: var(--fg-dim);
  font-size: 13px;
}
.bv2-compare-placeholder a { color: var(--accent); }

.bv2-getstarted-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 20px 22px;
}
.bv2-getstarted-card h3 {
  font-size: 16px;
  margin: 0 0 4px;
  font-weight: 600;
}
.bv2-getstarted-card .bv2-getstarted-sub {
  color: var(--fg-dim);
  font-size: 12px;
  margin: 0 0 12px;
}
.bv2-getstarted-card ul {
  list-style: none;
  padding: 0;
  margin: 0 0 16px;
  font-size: 13px;
}
.bv2-getstarted-card li { padding: 3px 0; }
.bv2-getstarted-card li a {
  color: var(--fg);
  text-decoration: none;
}
.bv2-getstarted-card li a:hover { color: var(--accent); text-decoration: underline; }
.bv2-getstarted-card li::before { content: '→ '; color: var(--accent); }
.bv2-getstarted-card .bv2-getstarted-cta {
  display: inline-block;
  background: var(--accent);
  color: var(--bg);
  padding: 6px 12px;
  border-radius: 4px;
  font-weight: 600;
  font-size: 13px;
  text-decoration: none;
}

.bv2-lb-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
}
.bv2-lb-table th, .bv2-lb-table td {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
  font-size: 13px;
}
.bv2-lb-table th {
  background: var(--bg2);
  color: var(--fg-dim);
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.bv2-lb-empty {
  margin-top: 18px;
  padding: 24px;
  background: var(--bg2);
  border: 1px dashed var(--border);
  border-radius: 6px;
  text-align: center;
  font-size: 13px;
}
.bv2-filters select {
  background: var(--bg2);
  color: var(--fg-dim);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
}
.bv2-filters select:disabled { opacity: 0.6; cursor: not-allowed; }

@media (max-width: 900px) {
  .bv2-launcher,
  .bv2-task-grid,
  .bv2-motivation-grid,
  .bv2-getstarted-grid { grid-template-columns: minmax(0, 1fr); }
}
```

- [ ] **Step 3: Rewrite `viewer/index.html`**

Replace the ENTIRE file content with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>BabyVision-v2 — Agentic 3D Vision Benchmark in Professional Domains</title>
<link rel="stylesheet" href="shared/style.css">
</head>
<body class="bv2-home">
<header class="topbar">
  <div>
    <a href="index.html" class="home-link" style="color:inherit;text-decoration:none">
      <strong id="brand">BabyVision-v2</strong>
    </a>
  </div>
  <div class="topbar-right">
    <nav class="bv2-globalnav" aria-label="Global">
      <a href="index.html" aria-current="page" class="is-active">Home</a>
      <a href="docs.html">Doc</a>
      <a href="assets.html">Domain 3D Assets</a>
      <a href="qagen.html">QA Tasks</a>
      <a href="leaderboard.html">Leaderboard</a>
    </nav>
    <div class="meta" id="counts">loading…</div>
  </div>
</header>

<main>
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

  <section class="bv2-launcher" aria-label="Modules">
    <a class="bv2-launcher-tile" href="docs.html">
      <div class="bv2-launcher-tile-head">
        <span class="bv2-launcher-tile-title">Doc</span>
        <span class="bv2-launcher-tile-count" id="bv2-launcher-doc-count">—</span>
      </div>
      <div class="bv2-launcher-tile-meta">
        Platform docs · benchmark protocol · MCP tool reference
      </div>
    </a>
    <a class="bv2-launcher-tile" href="assets.html">
      <div class="bv2-launcher-tile-head">
        <span class="bv2-launcher-tile-title">Domain 3D Assets</span>
        <span class="bv2-launcher-tile-count" id="bv2-launcher-assets-count">—</span>
      </div>
      <div class="bv2-launcher-tile-meta">
        <span id="bv2-launcher-domain-state">loading…</span>
      </div>
    </a>
    <a class="bv2-launcher-tile" href="qagen.html">
      <div class="bv2-launcher-tile-head">
        <span class="bv2-launcher-tile-title">QA Tasks</span>
        <span class="bv2-launcher-tile-count" id="bv2-launcher-qagen-count">—</span>
      </div>
      <div class="bv2-launcher-tile-meta">
        V2 / V3 / V4 / V6 · Genesis_VP · Medical Spatial
      </div>
    </a>
  </section>

  <section class="bv2-section" id="bv2-tasks-section">
    <div class="bv2-section-label">Tasks</div>
    <h2>Six task families across two demos</h2>
    <p class="bv2-section-intro">
      Each family stresses a distinct reasoning skill. Every task ships with a
      deterministic ground-truth recipe; metrics are computed by code, not by an LLM judge.
    </p>
    <div class="bv2-task-grid">
      <article class="bv2-task-card">
        <h2>V2 — Cross-section / topology</h2>
        <p class="bv2-task-keywords">2D-view → 3D inference · cannot be shortcut by code · geometric</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v2-cross-section--topology">Family details →</a></p>
      </article>
      <article class="bv2-task-card">
        <h2>V3 — Segmentation / referring</h2>
        <p class="bv2-task-keywords">pixel-level GT · SSIM / LPIPS · defends against LLM-as-judge</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v3-segmentation--referring">Family details →</a></p>
      </article>
      <article class="bv2-task-card">
        <h2>V4 — Manipulation / counterfactual</h2>
        <p class="bv2-task-keywords">rotate · slice · deform · geometry-grounded GT</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v4-manipulation--counterfactual">Family details →</a></p>
      </article>
      <article class="bv2-task-card">
        <h2>V6 — Multi-step reasoning</h2>
        <p class="bv2-task-keywords">multi-tool trace · scored on answer + trace fidelity</p>
        <p class="bv2-task-cta"><a href="docs.html?doc=benchmark/task-families#v6-multi-step-reasoning">Family details →</a></p>
      </article>
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
    </div>
  </section>

  <section class="bv2-section" id="bv2-motivation-section">
    <div class="bv2-section-label">Motivation</div>
    <h2>Why this benchmark exists</h2>
    <p class="bv2-section-intro">
      Three commitments separate BabyVision-v2 from caption-similarity VQA leaderboards.
    </p>
    <div class="bv2-motivation-grid">
      <article class="bv2-motivation-card">
        <h3>Executable GT</h3>
        <p>
          Every answer is produced by a deterministic program — geometry, mask argmax,
          physics solver. We compare model output to that program's output, not to a
          natural-language gold caption.
        </p>
        <a href="docs.html?doc=benchmark/evaluation-protocol">Protocol →</a>
      </article>
      <article class="bv2-motivation-card">
        <h3>No LLM-as-judge</h3>
        <p>
          Pixel-level and numeric metrics defend the score. An LLM may evaluate trace
          fidelity (step ordering) but never the terminal answer.
        </p>
        <a href="docs.html?doc=benchmark/evaluation-protocol">Protocol →</a>
      </article>
      <article class="bv2-motivation-card">
        <h3>3D-grounded</h3>
        <p>
          True 3D geometry — meshes, point clouds, volumes — is the source of truth.
          Caption matching against a single 2D view is rejected.
        </p>
        <a href="docs.html?doc=benchmark/evaluation-protocol">Protocol →</a>
      </article>
    </div>
  </section>

  <section class="bv2-section" id="bv2-domain-section">
    <div class="bv2-section-label">Domain coverage</div>
    <h2>Eleven domains · two shipped today</h2>
    <p class="bv2-section-intro">
      The platform supports eleven professional domains. v0.1.1 ships data for two
      (medical + embodied via the Genesis_VP demo); the other nine arrive as partner
      teams onboard with the partner SDK.
    </p>
    <div class="bv2-domain-mosaic" id="bv2-domain-mosaic" aria-label="Domains"></div>
  </section>

  <section class="bv2-section" id="bv2-compare-section">
    <div class="bv2-section-label">Compare</div>
    <h2>How BabyVision-v2 differs</h2>
    <div class="bv2-compare-placeholder">
      Comparison table coming soon — content tracked in
      <a href="https://github.com/UniPat-AI/BabyVision-v2/issues/5" target="_blank" rel="noopener">#5</a>.
    </div>
  </section>

  <section class="bv2-section" id="bv2-getstarted-section">
    <div class="bv2-section-label">Get started</div>
    <h2>Two entry points</h2>
    <p class="bv2-section-intro">
      Pick the path that matches what you're doing.
    </p>
    <div class="bv2-getstarted-grid">
      <article class="bv2-getstarted-card">
        <h3>Co-build a new domain</h3>
        <p class="bv2-getstarted-sub">For partners onboarding a new professional domain.</p>
        <ul>
          <li><a href="docs.html?doc=platform/quickstart">Quickstart</a></li>
          <li><a href="docs.html?doc=platform/new-domain-template">New-domain template</a></li>
          <li><a href="docs.html?doc=mcp/tools/load_nifti">MCP tool reference</a></li>
          <li><a href="docs.html?doc=platform/api-reference">Partner SDK API</a></li>
        </ul>
        <a class="bv2-getstarted-cta" href="docs.html?doc=platform/quickstart">Start in docs →</a>
      </article>
      <article class="bv2-getstarted-card">
        <h3>Verify on the leaderboard</h3>
        <p class="bv2-getstarted-sub">For model teams submitting baseline runs.</p>
        <ul>
          <li><a href="docs.html?doc=benchmark/submission-guide">Submission guide</a></li>
          <li><a href="docs.html?doc=benchmark/leaderboard">Baseline profiles</a></li>
          <li><a href="docs.html?doc=benchmark/evaluation-protocol">Reproduce an entry</a></li>
        </ul>
        <a class="bv2-getstarted-cta" href="leaderboard.html">Open leaderboard →</a>
      </article>
    </div>
  </section>

  <footer style="padding:32px 24px;color:var(--fg-dim);font-size:11px;border-top:1px solid var(--border);text-align:center">
    <span id="footer-brand">BabyVision-v2</span> ·
    <a href="https://github.com/UniPat-AI/BabyVision-v2" target="_blank" rel="noopener">github</a>
  </footer>
</main>

<script src="shared/manifest.js"></script>
<script src="shared/bv2-home.js"></script>
</body>
</html>
```

- [ ] **Step 4: Syntax check + smoke**

```bash
cd /Users/sue/Research/BabyVision-v2
node --check viewer/shared/bv2-home.js
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('viewer/index.html').read()); print('HTML OK')"

pkill -f "http.server 8765" 2>/dev/null
sleep 1
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
for u in /viewer/index.html /viewer/shared/bv2-home.js /viewer/manifest.json /viewer/shared/style.css; do
  s=$(curl -sI -o /dev/null -w '%{http_code}' "http://localhost:8765$u")
  echo "$s $u"
done
echo
# Verify the new homepage renders the 11 domains in the right order from the manifest:
curl -s http://localhost:8765/viewer/manifest.json | python3 -c "
import sys, json
m = json.load(sys.stdin)
assert len(m['domains']) == 11, f'expected 11 domains, got {len(m[\"domains\"])}'
ids = [d['id'] for d in m['domains']]
assert ids[0] == 'biology' and ids[-1] == 'industrial', f'first/last wrong: {ids[0]} / {ids[-1]}'
print('manifest 11 domains OK')
"
kill $SERVER_PID 2>/dev/null
```

Expected: `node --check` clean; HTML OK; 4 × 200; manifest 11 domains OK.

- [ ] **Step 5: Manual eyeball (record in report)**

The implementer cannot drive a browser. Document this checklist for the controller:

- Open http://localhost:8765/viewer/index.html
- Hero shows "BabyVision-v2 · Benchmark + Platform v0.1.1", title "Evaluating 3D-grounded visual reasoning in professional domains."
- 3 launcher tiles render (Doc / Domain 3D Assets / QA Tasks) with `—` counts (no real data yet) and a "2 shipped · 9 partner-in-progress" subline under "Domain 3D Assets"
- 6 task cards (V2 / V3 / V4 / V6 / Genesis_VP / Medical Spatial)
- 3 motivation cards
- 11 domain tiles: medical + embodied colored & clickable; other 9 dimmed with "coming via partner"
- Compare section shows the dashed-border placeholder
- Get started: 2 large cards with 4 + 3 deep links into docs.html

- [ ] **Step 6: Commit**

```bash
git add viewer/index.html viewer/shared/style.css viewer/shared/bv2-home.js
git status
git commit -m "feat(viewer): new BabyVision-v2 homepage with module launcher + 11-domain mosaic"
```

Verify `git show HEAD --stat` shows exactly 3 files (1 rewritten + 1 modified + 1 new).

---

## Task 4: Per-modality manifest sweep

**Files:**
- Modify: `viewer/vqa.html`
- Modify: `viewer/seed3d_pair.html`

**Interfaces:**
- Consumes: existing `window.Manifest.urls.*` (or just `window.Manifest`); both pages need `shared/manifest.js` loaded first.
- Produces: zero hardcoded `/viewer/<x>.json` fetches across the BV2-active viewer pages. The other ~23 per-modality pages take `?file=` URL params and don't need patching.

**Context note**: VQA and the 2D↔3D Seed3D-pair page are not in BV2's primary value path (they belong to ScienceVision's broader catalogue). We patch them anyway because they cause visible 404s in the served viewer when the user clicks the wrong link. If the data files don't exist, the patched code falls through to a friendly "no data" message rather than a console error.

- [ ] **Step 1: Patch `viewer/vqa.html`**

Find every line that fetches a hardcoded `/viewer/...json` and replace with the manifest-driven equivalent.

First inspect:

```bash
grep -n "fetch.*['\"]\(/viewer/\|\.\./viewer/\)" viewer/vqa.html
```

The pattern to replace (each occurrence):

```javascript
// BEFORE:
fetch('/viewer/datasets.index.json').then(...)

// AFTER:
fetch(window.Manifest.urls.data.assets_index.replace(/[^/]+$/, '') + 'datasets.index.json').then(...)
```

…but a cleaner pattern that fits Plan 1.5's contract is to bootstrap the page off `window.ManifestReady`:

For `viewer/vqa.html`, add a `<script src="shared/manifest.js"></script>` line BEFORE the existing inline script that does the fetches. Then wrap the existing init code in `window.ManifestReady.then((m) => { ... })`, and replace each hardcoded URL with a `Manifest.urls.data.<key>` if it exists, or a sibling-path derivation if the key isn't in the schema.

For the VQA-specific files (`vqa_index.json`, `datasets.index.json`) that aren't first-class entries in the manifest schema, derive the path as a sibling of `assets_index`:

```javascript
function siblingDataUrl(filename) {
  // Manifest.urls.data.assets_index is an absolute URL like
  // "http://host/data/assets.index.json". Replace the trailing path component
  // with the requested filename to get a sibling under the same data dir.
  const base = window.Manifest.urls.data.assets_index;
  return base.replace(/[^/]+$/, filename);
}
```

Add `siblingDataUrl` as a helper at the top of the inline script, then replace each `'/viewer/<filename>'` with `siblingDataUrl('<filename>')`.

The exact edit set for `viewer/vqa.html`:

1. Add `<script src="shared/manifest.js"></script>` in `<head>` before the first inline `<script>` block.
2. In the inline script, wrap the existing init logic so it runs after `window.ManifestReady` resolves:
   ```javascript
   window.ManifestReady.then(() => {
     // existing init code
   });
   ```
3. Add the `siblingDataUrl` helper at the top of the wrapped block.
4. Replace every `'/viewer/<name>.json'` with `siblingDataUrl('<name>.json')`.

**If the implementer finds vqa.html is too complex to safely refactor in one pass** (e.g. it has multiple disconnected fetch sites), they should:
- Make the change AND
- Run the page in a browser server and curl-test that all the fetch URLs now produce sensible URLs (sibling under `/data/`)

If the file has structural issues that prevent a clean wrap (e.g. fetches happen at top-level outside any function), defer detailed manifest wiring to a future plan and instead apply a minimal patch: add a top-of-script guard that warns once if Manifest is absent, leaving the existing /viewer/... fetches intact. Report this as DONE_WITH_CONCERNS.

- [ ] **Step 2: Patch `viewer/seed3d_pair.html`**

Same approach as Step 1. The file fetches `/viewer/synthetic_negative_assets.json` and possibly a sibling. Apply the same edit set: add manifest script tag, wrap init in `window.ManifestReady.then`, use `siblingDataUrl` for any non-first-class data file.

- [ ] **Step 3: Syntax check + smoke**

```bash
cd /Users/sue/Research/BabyVision-v2
python3 -c "from html.parser import HTMLParser; [HTMLParser().feed(open(p).read()) for p in ['viewer/vqa.html','viewer/seed3d_pair.html']]; print('2 HTML OK')"

pkill -f "http.server 8765" 2>/dev/null
sleep 1
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
for u in /viewer/vqa.html /viewer/seed3d_pair.html; do
  s=$(curl -sI -o /dev/null -w '%{http_code}' "http://localhost:8765$u")
  echo "$s $u"
done
echo
# No remaining hardcoded /viewer/ JSON fetches in the patched files:
echo "Remaining hardcoded /viewer/*.json fetches:"
grep -E "fetch\(['\"]/viewer/.*\.json" viewer/vqa.html viewer/seed3d_pair.html | head
kill $SERVER_PID 2>/dev/null
```

Expected: 2 HTML OK, 2 × 200, the grep prints nothing (zero hardcoded fetches remain).

- [ ] **Step 4: Commit**

```bash
git add viewer/vqa.html viewer/seed3d_pair.html
git status
git commit -m "fix(viewer): vqa + seed3d_pair fetch via Manifest.urls (no hardcoded paths)"
```

---

## Task 5: CI extension + acceptance

**Files:**
- Modify: `.github/workflows/manifest-check.yml`

**Interfaces:**
- Consumes: existing pytest + manifest validator
- Produces: CI fails when domain taxonomy drifts (Task 1's `test_manifest_domain_taxonomy.py` runs), when any of the new HTML pages goes missing, or when the manifest's domain count drops below 11.

- [ ] **Step 1: Edit `.github/workflows/manifest-check.yml`**

Find the `on.pull_request.paths:` list and add:

```yaml
      - 'viewer/index.html'
      - 'viewer/assets.html'
      - 'viewer/qagen.html'
      - 'viewer/leaderboard.html'
      - 'viewer/vqa.html'
      - 'viewer/seed3d_pair.html'
      - 'viewer/shared/bv2-home.js'
      - 'viewer/shared/style.css'
      - 'tests/test_manifest_domain_taxonomy.py'
```

Find the `steps:` list and add ONE new step right after the existing "Run MCP pair tests" step:

```yaml
      - name: Run domain-taxonomy test
        run: pytest tests/test_manifest_domain_taxonomy.py -v
```

- [ ] **Step 2: Lint YAML + verify the test still passes**

```bash
cd /Users/sue/Research/BabyVision-v2
python -c "import yaml; yaml.safe_load(open('.github/workflows/manifest-check.yml'))" && echo "YAML OK"
pytest tests/test_manifest_domain_taxonomy.py -v 2>&1 | tail -5
```

Expected: YAML OK; 5/5 pass.

- [ ] **Step 3: Full local acceptance sweep**

```bash
cd /Users/sue/Research/BabyVision-v2

echo "=== Plan 1+2+3 manifests + validators ==="
python tools/validate_manifest.py
python tools/validate_manifest.py --manifest viewer/manifest.local.example.json
pytest tests/test_manifest_schema.py tests/test_manifest_domain_taxonomy.py -v 2>&1 | tail -5

echo "=== Plan 2 build outputs not stale ==="
python scripts/build_docs_index.py --check
python scripts/build_docs_search.py --check

echo "=== All Plan 2 + 3 pytest ==="
pytest tests/test_docs_index.py tests/test_docs_search.py tests/test_mcp_pairs.py tests/test_manifest_domain_taxonomy.py -v 2>&1 | tail -10

echo "=== JS syntax sweep ==="
for f in viewer/shared/manifest.js \
         viewer/shared/bv2-home.js \
         viewer/shared/docs/docs-app.js \
         viewer/shared/docs/docs-tree.js \
         viewer/shared/docs/docs-links.js \
         viewer/shared/docs/docs-toc.js \
         viewer/shared/docs/docs-search.js \
         viewer/shared/docs/docs-mcp.js; do
  node --check "$f" && echo "OK: $f"
done

echo "=== HTML parses ==="
for f in viewer/index.html viewer/docs.html viewer/assets.html viewer/qagen.html viewer/leaderboard.html viewer/_manifest_check.html viewer/_vendor_check.html; do
  python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('$f').read()); print('OK: $f')"
done

echo "=== End-to-end server smoke ==="
pkill -f "http.server 8765" 2>/dev/null
sleep 1
python3 -m http.server 8765 --directory . > /dev/null 2>&1 &
SERVER_PID=$!
sleep 1
for u in \
  /viewer/index.html \
  /viewer/docs.html \
  /viewer/assets.html \
  /viewer/qagen.html \
  /viewer/leaderboard.html \
  /viewer/_manifest_check.html \
  /viewer/_vendor_check.html \
  /viewer/manifest.json \
  /viewer/manifest.local.example.json \
  /viewer/shared/manifest.js \
  /viewer/shared/bv2-home.js \
  /docs/_index.json \
  /docs/_search.json; do
  s=$(curl -sI -o /dev/null -w '%{http_code}' "http://localhost:8765$u")
  echo "$s $u"
done
kill $SERVER_PID 2>/dev/null
```

Expected:
- Both validators OK
- All `--check` runs OK
- Combined pytest tally ≥ 23 passing (manifest 13 + docs-index 10 + docs-search 7 + mcp-pairs 1 + domain-taxonomy 5 = exactly 36)
- All node --check OK
- All HTML OK
- All curls 200

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/manifest-check.yml
git status
git commit -m "ci(viewer): trigger on Plan 3 pages + lock domain-taxonomy test"
```

---

## Task 6: Plan-3 acceptance

**Files:** none modified — read-only verification.

**Interfaces:** none — terminal verification task.

- [ ] **Step 1: Confirm branch + log**

```bash
cd /Users/sue/Research/BabyVision-v2
git status -sb
git log --oneline c6b2404..HEAD
```

Expected: 5 new commits from Plan 3 tasks 1–5, in order.

- [ ] **Step 2: Re-run the Task-5 Step-3 sweep**

(Run the entire acceptance sweep block from Task 5 Step 3 again.)

Expected: every check still passes.

- [ ] **Step 3: Browser eyeball checklist (controller does the eyeballing)**

Document this in the report so the controller can walk it:

- http://localhost:8765/viewer/index.html — new BV2 homepage renders with hero / launcher / Tasks / Motivation / Domain mosaic / Compare placeholder / Get started
- http://localhost:8765/viewer/assets.html — global nav with "Domain 3D Assets" highlighted; 11 domain cards
- http://localhost:8765/viewer/qagen.html — 6 task family cards
- http://localhost:8765/viewer/leaderboard.html — empty-state message with link to submission guide
- http://localhost:8765/viewer/docs.html — Plan 2's docs SPA still works
- http://localhost:8765/viewer/_manifest_check.html — green OK; counts include 11 domains
- http://localhost:8765/viewer/_vendor_check.html — vendor lib smoke

- [ ] **Step 4: Status report**

For each Plan-3 acceptance item, mark Pass/Fail:

- [ ] Manifest contains 11 domains in canonical order
- [ ] medical + embodied are the only `shipped: true` entries
- [ ] `viewer/index.html` rewritten to BV2 layout (hero + launcher + 6 sections)
- [ ] `viewer/assets.html`, `viewer/qagen.html`, `viewer/leaderboard.html` reachable from the new global nav
- [ ] `vqa.html` + `seed3d_pair.html` patched to use Manifest.urls
- [ ] Domain taxonomy locked by pytest
- [ ] CI updated to trigger on new files
- [ ] All pytest passing (36 total)
- [ ] All builds `--check` clean
- [ ] All HTML pages parse

- [ ] **Step 5: No commit (verification-only)**

The ledger entry is updated by the controller.

---

## What this plan explicitly does NOT do (deferred)

- Compare-table content (GH #5).
- Real leaderboard data — `data/leaderboard.json` stays `[]`.
- Per-modality pages other than vqa + seed3d_pair (the rest take `?file=` and don't need patching).
- Auto-extracting MCP tool schemas from `sci3d_agent/`.
- Mobile drawer / hamburger for the new homepage.
- Compare visualization (radar chart, scatter, etc.) — placeholder only.
- New homepage interactive features (filtering domain tiles, sorting tasks, etc.).
- Mermaid / KaTeX / Playwright headless tests.

---

## Self-review against the spec

| Spec section | Plan 3 coverage |
|---|---|
| §3 Hero with "professional domains" wording | Task 3 |
| §3 Module launcher (3 tiles: Doc · Domain 3D Assets · QA Tasks) | Task 3 |
| §3 Tasks section (6 family cards with keyword subtitles) | Task 3 |
| §3 Motivation section (3 paragraphs: Executable GT · No LLM-as-judge · 3D-grounded) | Task 3 |
| §3 Domain coverage (11-tile mosaic, shipped vs partner-in-progress) | Tasks 1 + 3 |
| §3 Compare section (placeholder pointing at GH #5) | Task 3 |
| §3 Get started (2 cards: Co-build · Verify) | Task 3 |
| §5 `leaderboard.html` empty-state shell | Task 2 |
| §5 `assets.html`, `qagen.html` extracted from SV monolith | Task 2 |
| §5 Per-modality pages funneled through manifest | Task 4 (only the 2 with hardcoded fetches) |
| §6 manifest schema + `domains[]` with 11 entries | Task 1 |
| §7.1 CI staleness checks | Task 5 (additive) |
| §7.4 v1 acceptance checklist | Task 6 |
| §8 Visual language (palette, typography, layout patterns) | Task 3 CSS |
| §9 Decision log rows #15/#16/#17 → resolved | Task 1 Step 6 |
| §10 Out-of-scope | "What this plan does NOT do" section above |

No gaps. No placeholders. All steps contain executable code or shell commands. Type / signature consistency verified:
- `Manifest.domains[]` shape — locked by Task 1's schema (no change to manifest.schema.json needed; the array shape is already conformant).
- `Manifest.urls.data.*` — Plan 1.5 frozen interface; Task 4 consumes via `siblingDataUrl` helper.
- `Manifest.urls.docs.base_url` — consumed via `docs.html?doc=...` links throughout Task 3 HTML; no JS-side use needed in Plan 3.
- Global nav HTML pattern — identical across Tasks 2 and 3; 5 anchors with `aria-current="page" class="is-active"` on the active tab.
