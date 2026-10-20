# Expert Annotation Platform — Navigation + expert-v2 Content (Phase 1)

**Date:** 2026-07-27
**Status:** Design approved, pending spec review
**Scope:** Phase 1 only. Token auth + local database is **Phase 2** (separate spec).

## Context

`babyvision-v2.unipatai.com` is a fully static site served by Caddy on the Sing
server (`47.84.143.121`), docroot `~/BabyVision-v2/`. There is **no backend
today**. Work is done in the working tree on the aliyun box and rsynced to Sing
("working-tree-only" deploy).

Two subsystems are in play:

- **QA display** — `viewer/qagen.html#expert-seed` → `viewer/sciencevision_tasks.html`
  already fetches the three expert-v2 sets at runtime from
  `/QA-Gen/tasks/<set>/ScienceVision_<set>_final_tasks.jsonl` and renders them
  (tier chip + Expert Review panel).
- **Annotation platform** — `sv-annotate/` (subpath `/sv-annotate/`). A static
  Phase-0 shell hardcoded to four caffeine tasks. Its viewers
  (`molstar.html`, `replay.html`) load a shared capture module
  `sv-annotate/viewer/shared/capture/annotate-shell.js` and feed it via
  `window.__sv_viewer`. Submit currently **downloads** a `<task_id>.bundle.json`.

The three expert-v2 sets already exist locally under `QA-Gen/tasks/`:

| Domain    | Set dir                      | Count | Asset formats           |
|-----------|------------------------------|-------|-------------------------|
| Chemistry | `ChemistryKAxis-expert-v2`   | 8     | xyz, mol, cif, mmcif    |
| Biology   | `BiologyKAxis-expert-v2`     | 4     | mmcif                   |
| Medical   | `MedicalKAxis-expert-v2`     | 3     | nifti (`.nii.gz`)       |

All 15 referenced assets exist on disk and are served at `/crawl_data/...`.

**H20 constraint:** the latest expert-v2 lives in a `ScienceVision` checkout on
`UniPat-ScienceVision-H20` (`10.0.0.132`, a private IP). The aliyun box cannot
route to it; the SSH alias exists only on the user's Mac.

## Requirements (Phase 1)

1. **Nav (Req #1):** enter the annotation platform from the homepage AND return
   to the homepage from it.
2. **Content (Req #3):** update the three expert-v2 sets from H20 into both the
   QA display and the annotation platform, organized into 3 domains.

3. **Mechanical/architecture examples (supplement):** organize the qagen demo
   example questions for the mechanical (机械) and architecture (建筑) domains,
   grounded in existing assets. → **Part D**.

Requirement #2 (token login + local DB) is **deferred to Phase 2**.

## Design

### Part A — Homepage ↔ Annotation platform navigation

- **Entry (already live):** the homepage "Expert Platform" launcher tile →
  `/sv-annotate/` works. No change required beyond B/C below.
- **Global nav:** add an **`Expert`** item to the per-page global nav
  (`bv2-globalnav`: `Home · Doc · Domain 3D Assets · QA Tasks · Leaderboard ·
  Expert`, `href="/sv-annotate/"`). The nav is copy-pasted HTML, so update it in
  every page that carries it: `index.html`, `docs.html`, `assets.html`,
  `qagen.html`, `qa_item.html`, `expert_case.html`, `leaderboard.html`,
  `sciencevision_tasks.html` (update whichever of these actually render the nav
  block — verify by grep at implementation time; keep them identical).
- **Return (the real gap):** add a top bar to the `sv-annotate` shell
  (`sv-annotate/index.html`) with a **`← BabyVision-v2 Home`** link (`href="/"`).
  Sub-pages already carry `← Shell`, so Shell→Home closes the loop. Style to
  match the existing `sv-annotate` dark theme.

### Part B — Refresh expert-v2 from H20

- **Fetch path:** the aliyun box cannot reach `10.0.0.132`. At implementation:
  1. Try adding the host to this box's `~/.ssh/config` and connecting (expected
     to fail — private IP with no route).
  2. On failure, deliver **exact copy-paste commands** the user runs from their
     Mac (which holds the `UniPat-ScienceVision-H20` alias) to `rsync`/`scp` the
     three sets' latest `*.jsonl` and any newly-referenced `crawl_data` assets
     into this repo at their existing relative paths
     (`QA-Gen/tasks/*-expert-v2/...`, `crawl_data/...`).
- **Manifest:** add `QA-Gen/tasks/ChemistryKAxis-expert-v2/**`,
  `QA-Gen/tasks/BiologyKAxis-expert-v2/**`,
  `QA-Gen/tasks/MedicalKAxis-expert-v2/**` to `.mirror-include` so future
  `tools/sync_from_sciencevision.py` runs keep them in sync (currently missing).
- **QA display:** already reads the three sets at runtime → **no code change**;
  once files refresh, the display updates. Verify rendering after refresh
  (including `/data/qa_tasks_index.json` if it drives the set list — check and
  update if the set names/counts changed).

### Part C — expert-v2 into the annotation platform, 3 domains

- **Data-driven shell:** rewrite `sv-annotate/index.html` to **fetch the three
  jsonl at runtime** (same pattern as `sciencevision_tasks.html`) and render
  cards **grouped into Chemistry / Biology / Medical**. Content refreshes
  (Part B) then flow in with **zero rebuild**. The existing caffeine Phase-0
  tasks remain in their own clearly-labeled section; the Replay and pure-viewer
  links stay.
  - Root-absolute fetch (`/QA-Gen/tasks/...`) works from the `/sv-annotate/`
    subpath (same origin), as does loading assets from `/crawl_data/...`.
- **Viewer routing by `asset.format`:** each card builds its annotation URL from
  the task's `asset`:
  - **Chemistry (8) + Biology (4)** → `xyz|mol|cif|mmcif` →
    `sv-annotate/viewer/molstar.html?file=/crawl_data/…&type=<fmt>&annotate=<task_id>`.
    Fully annotatable with the existing capture shell. Verify `molstar.html`
    accepts root-absolute `file=` paths outside `/sv-annotate/` and handles the
    `xyz` type (it already sniffs COD small-molecule CIF → cifCore).
  - **Medical (3)** → `nifti` → **port NiiVue into the annotation platform**:
    - Add `sv-annotate/viewer/niivue.html`, loading NiiVue vendor + the shared
      `annotate-shell.js`.
    - Write a **NiiVue→capture adapter** exposing the same `window.__sv_viewer`
      interface the shell consumes from Mol* (camera/view state getters and any
      interaction hooks `annotate-shell.js` requires). Confirm the exact
      interface by reading `annotate-shell.js` and the Mol* wiring; keep the
      adapter thin and single-purpose.
    - Card URL:
      `sv-annotate/viewer/niivue.html?file=/crawl_data/…&type=nifti&annotate=<task_id>`.
- **Submit:** unchanged — downloads `<task_id>.bundle.json`. Server-side
  persistence to SQLite is Phase 2.

### Part D — Organize mechanical & architecture example questions

**Problem:** the qagen demo-cases grid is lopsided. Mechanical has one case
(`helmet_caliber/q001`), architecture has none (Manhattan sits under
"geography"). Meanwhile `assets.html` already groups the assets cleanly into
**Industrial · CAD & Mesh** (`sec-ind`, `assets/industrial.json`, 34 assets) and
**Architecture · BIM & Infrastructure** (`sec-arch`, `assets/architecture.json`,
15 assets).

**Goal:** curate example questions for both domains so the demo grid matches the
asset organization.

- **Demo grid (`qagen.html`):**
  - Relabel the `industrial` card to **机械 · Mechanical / CAD** (keep
    `data-domain`, keep `helmet_caliber` as its demo case; optionally point at a
    cleaner OCCT B-rep part).
  - Add a new **建筑 · Architecture** card (`data-domain="architecture"`)
    pointing at a new architecture demo case.
- **Author example questions** grounded in existing assets, added to
  `data/qagen_index.json` (and any resolver `qa_item.html` reads):
  - **Mechanical:** keep `helmet_caliber/q001`; add ≥1 grounded in an OCCT B-rep
    part (`/crawl_data/industrial/step_iges_samples/occt_bearing.iges` or
    `occt_screw.step`) — a spatial/feature-count style question.
  - **Architecture:** add ≥1 BIM case grounded in
    `/crawl_data/architecture/bim_ifc/Building-Structural.glb` (via `arch-model`)
    and ≥1 bridge-LiDAR case grounded in
    `/crawl_data/architecture/bridge_pcl/Banwol-Bridge.las` (via `pointcloud`,
    per-point structural classification).
- **Viewer routing gap:** `qa_item.html`'s `assetViewerURL` currently handles
  molstar / niivue / citymap / saturn only. Add routing branches so demo cases
  can open **OCCT** (`step`/`iges` → `occt.html`), **glTF/GLB**
  (`gltf.html`/`arch-model.html`), and **pointcloud** (`las` → `pointcloud.html`,
  passing `classes` when present). Reuse the exact URL-building logic already in
  `assets.html`'s `viewerURL()` so the two stay consistent — factor it to a
  shared helper if that avoids drift, otherwise mirror it carefully.
- Question text must not leak sim-repro params (existing house rule); keep any
  such detail in `ground_truth.repro`.
- This is a **static qagen change** — no backend, fits Phase 1.

## Components (units and boundaries)

- `sv-annotate/index.html` (shell) — fetches 3 jsonl, renders domain-grouped
  cards + Phase-0 section + home link. Depends on: served jsonl, asset paths.
- `sv-annotate/viewer/molstar.html` — existing; verify format/path handling.
- `sv-annotate/viewer/niivue.html` (new) — NiiVue viewer + capture shell.
- `sv-annotate/viewer/shared/capture/niivue-adapter.js` (new) — adapts NiiVue to
  the `window.__sv_viewer` contract. Isolated so the shell stays viewer-agnostic.
- Global nav edits — mechanical, per page, kept identical.
- `.mirror-include` + H20 pull commands — content sync.
- `qagen.html` demo grid + `data/qagen_index.json` — mechanical/architecture
  cards and authored example questions (Part D).
- `qa_item.html` `assetViewerURL` — add OCCT / glTF / arch-model / pointcloud
  routing branches, consistent with `assets.html` `viewerURL()`.

## Error handling / edge cases

- jsonl fetch fails or a set is empty → shell shows a per-domain "not available"
  note (mirror `sciencevision_tasks.html`'s missing-set message), does not break
  other domains.
- Asset 404 → viewer shows a clear error rather than a blank canvas.
- Large medical `.nii.gz` (e.g. 7 MB IXI T1) → acceptable to load directly;
  no streaming needed for Phase 1.
- Unknown `asset.format` → card renders but is marked non-annotatable rather
  than linking to a broken viewer.

## Testing / verification

- Visual: homepage nav shows Expert on all pages; sv-annotate shell shows 3
  domain groups with correct counts (8/4/3) + caffeine Phase-0; Home link works.
- Each domain: open one card → viewer loads the asset → capture (mark
  checkpoint, submit) produces a `bundle.json`. Medical card loads in NiiVue and
  capture works via the adapter.
- QA display: `qagen.html#expert-seed` still opens each expert-v2 case in
  `sciencevision_tasks.html` after refresh.
- Part D: qagen demo grid shows 机械 · Mechanical/CAD and 建筑 · Architecture
  cards; each demo case opens in the correct viewer (OCCT B-rep part; BIM GLB via
  arch-model; bridge LiDAR via pointcloud with classification). No sim-repro
  params leak into question text.
- Deploy: rsync working tree to Sing; re-verify live via curl/browser.

## Out of scope (Phase 2, separate spec)

- Pre-issued per-expert token auth gating `/sv-annotate/`.
- Backend service (lightweight Python + SQLite) on Sing, reverse-proxied by
  Caddy, that validates tokens, identifies the expert, and persists annotation
  bundles to a local database instead of downloading them.
