# Expert Platform Nav + expert-v2 Content — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the expert annotation platform reachable/returnable from the homepage, wire the three expert-v2 domains (Chemistry/Biology/Medical) into both the QA display and the annotation platform, and organize mechanical/architecture example questions in the QA demo grid.

**Architecture:** Static site served by Caddy from `~/BabyVision-v2/` on the Sing server (`47.84.143.121`); work happens in the working tree here and is rsynced to Sing (no git deploy). All Phase-1 changes are HTML/JS/JSON edits — no backend. The annotation platform (`/sv-annotate/`) reuses a shared capture module (`annotate-shell.js` + `viewer-adapters.js`); QA pages resolve questions from `data/qagen_index.json` + an assets index and route each asset to an in-browser viewer.

**Tech Stack:** Static HTML + vanilla ES modules; Mol* 4.5.0 (molecules), NiiVue 0.45.0 UMD (NIfTI), Three.js viewers (`gltf.html`, `occt.html`, `arch-model.html`, `pointcloud.html`); Caddy static server.

## Global Constraints

- **No backend in Phase 1.** Annotation submit continues to **download** `<task_id>.bundle.json`. Token auth + SQLite persistence is Phase 2 (separate spec).
- **Question text must not leak sim-repro params** (step counts, dt, solver settings). Such detail goes only in `ground_truth.repro`.
- **Deploy is working-tree-only.** The repo has ~24 intentional uncommitted changes. Each task commits **only the exact files it touches** (`git add <explicit paths>`), never `git add -A`.
- **Same-origin root-absolute paths** work everywhere: `/QA-Gen/tasks/...`, `/crawl_data/...`, `/sv-annotate/...`, `/viewer/...` all resolve under the docroot.
- **No unit-test harness exists** for this static site. "Tests" here are `grep`/`curl`/browser checks. Follow the same cadence: assert current state → change → re-assert → commit.
- **Verification base URL:** live site `https://babyvision-v2.unipatai.com`; local file checks run in `~/BabyVision-v2`.

## File Structure

**Navigation (Group A)**
- Modify: the `bv2-globalnav` block in every page that carries it — confirm the set with `grep -rl 'bv2-globalnav' viewer/*.html`. Known: `viewer/index.html`, `viewer/assets.html`, `viewer/qagen.html`, `viewer/qa_item.html`, `viewer/expert_case.html`, `viewer/leaderboard.html`; also check `viewer/docs.html`, `viewer/sciencevision_tasks.html`.

**Content sync (Group B)**
- Modify: `.mirror-include`
- Data refreshed (not hand-edited): `QA-Gen/tasks/{Chemistry,Biology,Medical}KAxis-expert-v2/*.jsonl`, any new `crawl_data/...` assets.

**Annotation platform (Group C)**
- Modify: `sv-annotate/viewer/shared/capture/annotate-shell.js` (generalize task loading)
- Rewrite: `sv-annotate/index.html` (data-driven shell + home link)
- Create: `sv-annotate/viewer/niivue.html` (NIfTI viewer + capture)
- Modify: `sv-annotate/viewer/shared/capture/viewer-adapters.js` (add `niivueAdapter`)

**QA examples (Group D)**
- Modify: `viewer/qa_item.html` (`assetViewerURL` routing branches)
- Modify: `data/assets.index.json` (add mechanical/architecture assets)
- Modify: `data/qagen_index.json` (author example questions)
- Modify: `viewer/qagen.html` (demo-grid cards)

---

## Task Group A — Homepage ↔ Annotation platform navigation

### Task A1: Add "Expert" to the global nav on every page

**Files:**
- Modify: every `viewer/*.html` containing `bv2-globalnav` (enumerate at runtime).

**Interfaces:**
- Produces: a nav entry `<a href="/sv-annotate/">Expert</a>` present on all global-nav pages. Group C's shell relies on `/sv-annotate/` resolving; that already works.

- [ ] **Step 1: Enumerate the pages and assert Expert is absent (current state)**

```bash
cd ~/BabyVision-v2
grep -rl 'bv2-globalnav' viewer/*.html
grep -rc 'href="/sv-annotate/">Expert' viewer/*.html | grep -v ':0' || echo "NONE HAVE IT (expected)"
```
Expected: a list of files; the second command prints `NONE HAVE IT (expected)`.

- [ ] **Step 2: Add the Expert link after the Leaderboard link in each enumerated file**

The nav block ends with the Leaderboard link, e.g. in `viewer/index.html`:

```html
      <a href="qagen.html">QA Tasks</a>
      <a href="leaderboard.html">Leaderboard</a>
    </nav>
```

For **each** file, insert one line so it reads:

```html
      <a href="qagen.html">QA Tasks</a>
      <a href="leaderboard.html">Leaderboard</a>
      <a href="/sv-annotate/">Expert</a>
    </nav>
```

Notes:
- Match each file's existing indentation.
- Do **not** add `is-active`/`aria-current` (no page here *is* the Expert page).
- If a file's nav omits some links, still add the Expert link as the last `<a>` before `</nav>`.

- [ ] **Step 3: Verify every global-nav page now has the link**

```bash
cd ~/BabyVision-v2
for f in $(grep -rl 'bv2-globalnav' viewer/*.html); do
  grep -q 'href="/sv-annotate/">Expert' "$f" && echo "OK  $f" || echo "MISSING  $f"
done
```
Expected: every line starts with `OK`.

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add $(grep -rl 'bv2-globalnav' viewer/*.html)
git commit -m "feat(nav): add Expert item to global nav across pages"
```

---

## Task Group B — Refresh expert-v2 from H20

### Task B1: Add expert-v2 sets to `.mirror-include`

**Files:**
- Modify: `.mirror-include`

- [ ] **Step 1: Assert the sets are not yet mirrored**

```bash
cd ~/BabyVision-v2
grep -n 'expert-v2' .mirror-include || echo "NOT PRESENT (expected)"
```
Expected: `NOT PRESENT (expected)`.

- [ ] **Step 2: Add the three globs under the QA-gen section**

Find the block:

```
QA-Gen/tasks/MedicalKAxisPilot/**
QA-Gen/tasks/HelmetNutCaliber/**
```

Add immediately after it:

```
QA-Gen/tasks/ChemistryKAxis-expert-v2/**
QA-Gen/tasks/BiologyKAxis-expert-v2/**
QA-Gen/tasks/MedicalKAxis-expert-v2/**
```

- [ ] **Step 3: Verify**

```bash
cd ~/BabyVision-v2
grep -c 'expert-v2' .mirror-include   # expect 3
```
Expected: `3`.

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add .mirror-include
git commit -m "chore(mirror): track expert-v2 task sets for sciencevision sync"
```

### Task B2: Pull latest expert-v2 from H20 and verify the QA display

**Files:**
- Refreshed on disk: `QA-Gen/tasks/*-expert-v2/*.jsonl` (+ any newly referenced `crawl_data` assets).

**Interfaces:**
- Produces: up-to-date jsonl that both `sciencevision_tasks.html` (QA display) and the Group C shell read at runtime.

- [ ] **Step 1: Try a direct pull from this box (expected to fail — private IP)**

```bash
cd ~/BabyVision-v2
timeout 8 ssh -o ConnectTimeout=5 -o BatchMode=yes matianyi@10.0.0.132 'echo REACHABLE' 2>&1 || echo "UNREACHABLE — use Step 2 commands on the Mac"
```
If it prints `REACHABLE`, run the rsync from here (Step 2 command bodies, but from this box). Otherwise proceed to Step 2.

- [ ] **Step 2: Deliver copy-paste commands for the user's Mac**

Give the user this block to run **on their Mac** (which has the `UniPat-ScienceVision-H20` alias). It pulls the three sets and any assets they reference into this repo on the aliyun box (`UniPat-Sing` = the deploy box; adjust the destination host/path if the ScienceVision checkout path differs — confirm with the user):

```bash
# On the Mac. Source = ScienceVision checkout on H20; dest = BabyVision-v2 on the aliyun/Sing box.
SETS="ChemistryKAxis-expert-v2 BiologyKAxis-expert-v2 MedicalKAxis-expert-v2"
for s in $SETS; do
  rsync -avz \
    "UniPat-ScienceVision-H20:~/ScienceVision/QA-Gen/tasks/$s/" \
    "UniPat-Sing:~/BabyVision-v2/QA-Gen/tasks/$s/"
done
```
If newly-referenced `crawl_data` assets are missing after refresh (Step 4 flags them), pull those too, e.g.:
```bash
rsync -avz "UniPat-ScienceVision-H20:~/ScienceVision/crawl_data/" "UniPat-Sing:~/BabyVision-v2/crawl_data/" --ignore-existing
```

- [ ] **Step 3: After refresh, re-count records per set**

```bash
cd ~/BabyVision-v2
for f in QA-Gen/tasks/*-expert-v2/*.jsonl; do echo "$(wc -l < "$f")  $f"; done
```
Expected: three files with line counts (baseline was Chem 8 / Bio 4 / Med 3 — counts may grow).

- [ ] **Step 4: Verify every referenced asset exists on disk**

```bash
cd ~/BabyVision-v2
python3 - <<'PY'
import json, glob, os
missing=[]
for f in glob.glob('QA-Gen/tasks/*-expert-v2/*.jsonl'):
    for line in open(f):
        d=json.loads(line); p=d.get('asset',{}).get('repo_path')
        if p and not os.path.exists(p): missing.append(p)
print("MISSING:" , missing or "none")
PY
```
Expected: `MISSING: none`. If not, pull the assets (Step 2 second command) and re-run.

- [ ] **Step 5: Verify the live QA display still renders each set**

```bash
for s in ChemistryKAxis-expert-v2 BiologyKAxis-expert-v2 MedicalKAxis-expert-v2; do
  echo "== $s =="
  curl -s "https://babyvision-v2.unipatai.com/QA-Gen/tasks/$s/ScienceVision_${s}_final_tasks.jsonl" | head -c 120; echo
done
```
Expected: each returns JSONL (after the working tree is deployed in Task E1). Before deploy this reflects the currently-live copy; re-run after Task E1.

- [ ] **Step 6: Commit refreshed data (only if files changed)**

```bash
cd ~/BabyVision-v2
git add QA-Gen/tasks/ChemistryKAxis-expert-v2 QA-Gen/tasks/BiologyKAxis-expert-v2 QA-Gen/tasks/MedicalKAxis-expert-v2
git commit -m "data: refresh expert-v2 task sets from ScienceVision (H20)" || echo "no changes to commit"
```

---

## Task Group C — expert-v2 into the annotation platform (3 domains)

### Task C1: Generalize the capture shell to load any task set

**Files:**
- Modify: `sv-annotate/viewer/shared/capture/annotate-shell.js:15-21` (the `loadTask` function)

**Interfaces:**
- Consumes: `?annotate=<task_id>` (existing) plus a new optional `?set=<setName>` query param.
- Produces: `loadTask(id)` that, when `set` is present, fetches `/QA-Gen/tasks/<set>/ScienceVision_<set>_final_tasks.jsonl`; otherwise falls back to the caffeine Phase-0 file. Task records must expose `task_id`, `v_class`, `instruction`, `asset.viewer`, and `answer_schema.fields` — verified true for both caffeine and expert-v2.

- [ ] **Step 1: Read the current loadTask to confirm the anchor**

```bash
cd ~/BabyVision-v2
sed -n '15,21p' sv-annotate/viewer/shared/capture/annotate-shell.js
```
Expected: the caffeine-hardcoded `loadTask` shown in the spec.

- [ ] **Step 2: Replace loadTask with a set-aware version**

Replace:

```js
async function loadTask(id) {
  const res = await fetch('/sv-annotate/tasks/expert_annotation/caffeine_phase0.jsonl');
  const records = (await res.text()).trim().split('\n').filter(Boolean).map(JSON.parse);
  const t = records.find(r => r.task_id === id);
  if (!t) throw new Error(`task not found: ${id}`);
  return t;
}
```

with:

```js
async function loadTask(id) {
  const set = new URLSearchParams(location.search).get('set');
  const url = set
    ? `/QA-Gen/tasks/${encodeURIComponent(set)}/ScienceVision_${set.replace(/[^A-Za-z0-9_-]/g, '')}_final_tasks.jsonl`
    : '/sv-annotate/tasks/expert_annotation/caffeine_phase0.jsonl';
  const res = await fetch(url);
  if (!res.ok) throw new Error(`task set fetch failed: HTTP ${res.status} (${url})`);
  const records = (await res.text()).trim().split('\n').filter(Boolean).map(JSON.parse);
  const t = records.find(r => r.task_id === id);
  if (!t) throw new Error(`task not found: ${id} in ${url}`);
  return t;
}
```

- [ ] **Step 3: Verify the file parses (no syntax error)**

```bash
cd ~/BabyVision-v2
node --check sv-annotate/viewer/shared/capture/annotate-shell.js && echo "SYNTAX OK"
```
Expected: `SYNTAX OK`. (If `node --check` rejects ESM `import`, instead run `node --input-type=module -e "$(cat sv-annotate/viewer/shared/capture/annotate-shell.js | sed '1,3d')" 2>&1 | head` is NOT reliable — prefer opening the page in Step 4 of C2 to confirm at runtime.)

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add sv-annotate/viewer/shared/capture/annotate-shell.js
git commit -m "feat(annotate): load task record from ?set= jsonl, caffeine fallback"
```

### Task C2: Rewrite the sv-annotate shell — home link + 3 expert-v2 domains

**Files:**
- Rewrite: `sv-annotate/index.html`

**Interfaces:**
- Consumes: `/QA-Gen/tasks/<set>/ScienceVision_<set>_final_tasks.jsonl` (fetched at runtime); each task's `asset.format` and `asset.repo_path`/`path`.
- Produces: cards linking to `viewer/molstar.html?file=/crawl_data/…&type=<fmt>&annotate=<task_id>&set=<set>` for `xyz|mol|cif|mmcif`, and to `viewer/niivue.html?...&set=<set>` for `nifti` (Task C3). A `← BabyVision-v2 Home` link (`href="/"`).

- [ ] **Step 1: Assert current shell has no home link and no expert-v2**

```bash
cd ~/BabyVision-v2
grep -c 'href="/"' sv-annotate/index.html || true       # expect 0
grep -c 'expert-v2\|QA-Gen/tasks' sv-annotate/index.html || true  # expect 0
```
Expected: both `0`.

- [ ] **Step 2: Replace `sv-annotate/index.html` with a data-driven shell**

Write the file with: (a) a topbar carrying `← BabyVision-v2 Home` (`href="/"`), (b) the existing caffeine Phase-0 section kept as static cards, (c) a script that fetches the three expert-v2 sets and renders domain-grouped cards, routing by `asset.format`. Use this content:

```html
<!doctype html>
<meta charset="utf-8">
<title>Expert Annotation — BabyVision-v2</title>
<style>
  body { font: 15px/1.55 -apple-system, Segoe UI, Roboto, sans-serif; max-width: 760px;
         margin: 0 auto; padding: 0 20px 60px; color: #e6edf3; background: #0d1117; }
  .topbar { display:flex; align-items:center; gap:14px; padding:12px 0; margin-bottom:8px;
            border-bottom:1px solid #21262d; position:sticky; top:0; background:#0d1117; z-index:5; }
  .topbar a.home { color:#58a6ff; text-decoration:none; font-weight:600; }
  .topbar a.home:hover { text-decoration:underline; }
  h1 { font-size: 20px; margin: 18px 0 4px; } h2 { font-size: 15px; color: #8b949e; margin-top: 28px; }
  .domain-label { color:#58a6ff; }
  a.card { display: block; padding: 12px 14px; margin: 8px 0; border: 1px solid #30363d;
           border-radius: 8px; text-decoration: none; color: #e6edf3; background: #161b22; }
  a.card:hover { border-color: #58a6ff; }
  .vc { display: inline-block; min-width: 58px; font-weight: 600; color: #58a6ff; }
  .q { color: #8b949e; font-size: 13px; }
  code { background: #161b22; padding: 1px 5px; border-radius: 4px; }
  .empty { color:#6e7681; font-size:13px; }
</style>
<header class="topbar">
  <a class="home" href="/">← BabyVision-v2 Home</a>
  <strong>Expert Annotation</strong>
</header>

<h1>Expert Annotation Platform</h1>
<p class="q">点开任一任务进入标注模式：交互 → <b>Mark key step</b> 记 checkpoint → <b>Submit &amp; download</b> 下载 <code>&lt;task_id&gt;.bundle.json</code>。QC 门：交互≥60s、≥2 checkpoints、答案非空、有相机变化。</p>

<div id="expert-v2"><p class="empty">Loading expert-v2 tasks…</p></div>

<h2>Phase-0 · caffeine → Mol*</h2>
<a class="card" href="/sv-annotate/viewer/molstar.html?file=/sv-annotate/crawl_data/biology/small_molecules_derived/2519_caffeine.mol&type=mol&annotate=bio_caffeine_v2_topology">
  <span class="vc">V2</span> topology<div class="q">嘌呤双环骨架：14 个重原子是否严格共面？</div></a>
<a class="card" href="/sv-annotate/viewer/molstar.html?file=/sv-annotate/crawl_data/biology/small_molecules_derived/2519_caffeine.mol&type=mol&annotate=bio_caffeine_spatial_distance">
  <span class="vc">Spatial</span> distance<div class="q">两个羰基氧的 3D 直线距离及比值估计</div></a>
<a class="card" href="/sv-annotate/viewer/molstar.html?file=/sv-annotate/crawl_data/biology/small_molecules_derived/2519_caffeine.mol&type=mol&annotate=bio_caffeine_v3_occlusion">
  <span class="vc">V3</span> occlusion<div class="q">当前视角下被部分遮挡的 H / C 原子分割</div></a>
<a class="card" href="/sv-annotate/viewer/molstar.html?file=/sv-annotate/crawl_data/biology/small_molecules_derived/2519_caffeine.mol&type=mol&annotate=bio_caffeine_v4_rotation_occlusion">
  <span class="vc">V4</span> rotation occlusion<div class="q">绕 X 轴后翻 90° 后的遮挡推理</div></a>

<h2>回放器</h2>
<a class="card" href="/sv-annotate/viewer/replay.html"><span class="vc">▶</span> Replay<div class="q">加载下载的 <code>*.bundle.json</code></div></a>

<script type="module">
const SETS = [
  { key:'chemistry', label:'Chemistry · 化学', set:'ChemistryKAxis-expert-v2' },
  { key:'biology',   label:'Biology · 生物',   set:'BiologyKAxis-expert-v2' },
  { key:'medical',   label:'Medical · 医学',   set:'MedicalKAxis-expert-v2' },
];
const MOLSTAR_FMT = new Set(['xyz','mol','cif','mmcif','sdf','mol2','pdb']);

function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function viewerHref(task, set){
  const a = task.asset || {};
  const fmt = (a.format || '').toLowerCase();
  const file = a.path || (a.repo_path ? '/' + a.repo_path : '');
  const qs = 'file=' + encodeURIComponent(file) + '&type=' + encodeURIComponent(fmt)
           + '&annotate=' + encodeURIComponent(task.task_id) + '&set=' + encodeURIComponent(set);
  if (fmt === 'nifti' || /\.nii(\.gz)?$/.test(file)) return '/sv-annotate/viewer/niivue.html?' + qs;
  if (MOLSTAR_FMT.has(fmt)) return '/sv-annotate/viewer/molstar.html?' + qs;
  return null;   // unknown format → non-annotatable
}

async function loadSet(s){
  const url = `/QA-Gen/tasks/${s.set}/ScienceVision_${s.set}_final_tasks.jsonl`;
  const res = await fetch(url, { cache:'no-cache' });
  if (!res.ok) return { ...s, tasks: [] };
  const tasks = (await res.text()).trim().split('\n').filter(Boolean).map(JSON.parse);
  return { ...s, tasks };
}

(async () => {
  const root = document.getElementById('expert-v2');
  const groups = await Promise.all(SETS.map(loadSet));
  const html = ['<h1 style="font-size:16px;margin-top:26px">expert-v2 · 三域标注任务</h1>'];
  for (const g of groups) {
    html.push(`<h2><span class="domain-label">${esc(g.label)}</span> · ${g.tasks.length} tasks</h2>`);
    if (!g.tasks.length) { html.push('<p class="empty">（该域暂不可用 / not mirrored）</p>'); continue; }
    for (const t of g.tasks) {
      const href = viewerHref(t, g.set);
      const title = esc(t.title || t.task_id);
      const instr = esc(t.instruction || '');
      if (href) {
        html.push(`<a class="card" href="${href}"><span class="vc">${esc(t.v_class||'')}</span> ${title}<div class="q">${instr}</div></a>`);
      } else {
        html.push(`<div class="card" style="opacity:.6"><span class="vc">${esc(t.v_class||'')}</span> ${title}<div class="q">${instr}<br><em>no viewer for format ${esc((t.asset||{}).format)}</em></div></div>`);
      }
    }
  }
  root.innerHTML = html.join('\n');
})();
</script>
```

- [ ] **Step 3: Verify home link + fetch wiring present**

```bash
cd ~/BabyVision-v2
grep -c 'class="home" href="/"' sv-annotate/index.html   # expect 1
grep -c 'QA-Gen/tasks' sv-annotate/index.html            # expect >=1
grep -c 'niivue.html' sv-annotate/index.html             # expect >=1
```
Expected: `1`, `>=1`, `>=1`.

- [ ] **Step 4: Browser check (after local serve or Task E1 deploy)**

Open `/sv-annotate/`. Expected: `← BabyVision-v2 Home` at top; three domain sections with correct counts; caffeine Phase-0 section; clicking a Chemistry card opens Mol* with the molecule and the annotation panel. (Medical cards depend on Task C3.)

- [ ] **Step 5: Commit**

```bash
cd ~/BabyVision-v2
git add sv-annotate/index.html
git commit -m "feat(sv-annotate): data-driven 3-domain shell + home link"
```

### Task C3: Port NiiVue into the annotation platform

**Files:**
- Create: `sv-annotate/viewer/niivue.html`

**Interfaces:**
- Consumes: `?file=`, optional `?overlay=`, and `?annotate=` (triggers capture). Reuses main-site NiiVue vendor at `/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js`.
- Produces: `window.__sv_viewer` set to the NiiVue instance (only when `?annotate=` present), and loads `annotate-shell.js`. Canvas id is `gl`. The adapter (Task C4) reads `nv.scene`, `nv.opts.sliceType`, and the `#gl` canvas.

- [ ] **Step 1: Create the file**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Niivue — Expert Annotation</title>
<link rel="stylesheet" href="/sv-annotate/viewer/shared/style.css">
<script src="/sv-annotate/viewer/shared/neg-banner.js" defer></script>
<script src="/sv-annotate/viewer/shared/capture/annotate-shell.js" type="module"></script>
<link rel="preload" as="script" href="/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js">
<style>
  #gl { width: 100vw; height: calc(100vh - 49px); display: block; position: fixed; top: 49px; left: 0; }
  #view-toggle { position: fixed; top: 60px; right: 12px; background: rgba(13,17,23,0.9); padding: 6px; border-radius: 4px; border: 1px solid #30363d; display: flex; gap: 4px; z-index: 10; }
  #view-toggle button { background: #161b22; color: #c9d1d9; border: 1px solid #30363d; padding: 4px 10px; border-radius: 3px; font: 11px ui-monospace, monospace; cursor: pointer; }
  #view-toggle button.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }
</style>
</head>
<body>
<header class="topbar">
  <div><a class="back" href="/sv-annotate/">← Shell</a><strong>Niivue</strong> <span id="filename" class="meta"></span></div>
  <div class="meta">NIfTI · MPR / 3D volume render</div>
</header>
<canvas id="gl"></canvas>
<div id="view-toggle">
  <button data-mode="render" class="active">3D</button>
  <button data-mode="multiplanar">MPR</button>
  <button data-mode="axial">Axial</button>
</div>
<script src="/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js"></script>
<script>
  const params = new URLSearchParams(location.search);
  const file = params.get('file');
  const overlay = params.get('overlay');
  document.getElementById('filename').textContent = file ? file.split('/').pop() + (overlay ? ' + label' : '') : '(no file)';
  if (!file) {
    document.body.insertAdjacentHTML('beforeend', '<div class="error-box">No ?file= query param.</div>');
  } else {
    const SLICE = { axial: 0, coronal: 1, sagittal: 2, multiplanar: 3, render: 4 };
    const nv = new niivue.Niivue({
      backColor: [0.05, 0.06, 0.09, 1],
      sliceType: SLICE.render,
      isColorbar: true,
      isOrientCube: true,
      isNearestInterpolation: false,
    });
    nv.attachTo('gl');
    // Expose for the capture shell BEFORE volumes load so the 30s poll resolves.
    if (params.get('annotate')) window.__sv_viewer = nv;
    const volumes = [{ url: file, opacity: 1.0 }];
    if (overlay) volumes.push({ url: overlay, opacity: 0.5, colormap: 'red' });
    nv.loadVolumes(volumes).catch(err => {
      document.body.insertAdjacentHTML('beforeend', '<div class="error-box">Failed to load:\n' + err + '</div>');
    });
    const toggle = document.getElementById('view-toggle');
    toggle.addEventListener('click', e => {
      const btn = e.target.closest('button[data-mode]'); if (!btn) return;
      nv.setSliceType(SLICE[btn.dataset.mode]);
      for (const b of toggle.querySelectorAll('button')) b.classList.toggle('active', b === btn);
    });
  }
</script>
</body>
</html>
```

- [ ] **Step 2: Verify the vendor path is reachable**

```bash
cd ~/BabyVision-v2
ls viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js && echo "VENDOR OK"
```
Expected: `VENDOR OK`. (If absent locally, it is served on the live site; confirm via `curl -sI https://babyvision-v2.unipatai.com/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js | head -1`.)

- [ ] **Step 3: Commit**

```bash
cd ~/BabyVision-v2
git add sv-annotate/viewer/niivue.html
git commit -m "feat(sv-annotate): NiiVue viewer for NIfTI annotation"
```

### Task C4: Implement the NiiVue capture adapter

**Files:**
- Modify: `sv-annotate/viewer/shared/capture/viewer-adapters.js` (replace the `niivue` case)

**Interfaces:**
- Consumes: `window.__sv_viewer` = NiiVue instance (from Task C3), canvas `#gl`.
- Produces: `makeAdapter('niivue')` returning an object with `name`, `getCanvas()`, `onCameraChange(cb)`, `getCameraSnapshot()`, `getViewMode()`, `screenshot()` — the same interface `capture-core.js` and `annotate-shell.js` already consume from the Mol* adapter.

- [ ] **Step 1: Add the `niivueAdapter` function above `makeAdapter`**

Insert before `function notImplemented(name)`:

```js
function niivueAdapter() {
  const nv = window.__sv_viewer;
  if (!nv) throw new Error('niivue viewer not exposed on window.__sv_viewer');
  const canvas = document.getElementById('gl');
  const SLICE_NAME = { 0:'axial', 1:'coronal', 2:'sagittal', 3:'multiplanar', 4:'render' };
  let lastEmit = 0;

  return {
    name: 'niivue',
    getCanvas() { return canvas; },
    getCameraSnapshot() {
      const sc = nv.scene || {};
      return {
        sliceType: nv.opts ? nv.opts.sliceType : null,
        azimuth: sc.renderAzimuth, elevation: sc.renderElevation,
        crosshairPos: sc.crosshairPos, clipPlane: sc.clipPlane,
        pan2Dxyzmm: sc.pan2Dxyzmm,
      };
    },
    getViewMode() { return SLICE_NAME[nv.opts ? nv.opts.sliceType : 4] || 'render'; },
    onCameraChange(cb) {
      const emit = () => {
        const now = performance.now();
        if (now - lastEmit < 100) return;
        lastEmit = now; cb(this.getCameraSnapshot());
      };
      // NiiVue 0.45 fires these on interaction; wrap without dropping any existing handler.
      const prevLoc = nv.onLocationChange; nv.onLocationChange = (d) => { if (prevLoc) prevLoc(d); emit(); };
      const prevAz  = nv.onAzimuthElevationChange; nv.onAzimuthElevationChange = (a,e) => { if (prevAz) prevAz(a,e); emit(); };
      const prevZ   = nv.onZoom3DChange; nv.onZoom3DChange = (z) => { if (prevZ) prevZ(z); emit(); };
    },
    async screenshot() {
      // Force a fresh draw so the WebGL backbuffer is populated before capture.
      if (typeof nv.drawScene === 'function') nv.drawScene();
      return canvas.toDataURL('image/png');
    },
  };
}
```

- [ ] **Step 2: Wire it into `makeAdapter`**

Change:

```js
    case 'niivue': return notImplemented('niivue')();
```

to:

```js
    case 'niivue': return niivueAdapter();
```

- [ ] **Step 3: Verify the case is wired and file has no obvious syntax break**

```bash
cd ~/BabyVision-v2
grep -n "case 'niivue': return niivueAdapter" sv-annotate/viewer/shared/capture/viewer-adapters.js
node --check sv-annotate/viewer/shared/capture/viewer-adapters.js 2>/dev/null && echo "SYNTAX OK" || echo "check in browser (ESM)"
```
Expected: the grep matches.

- [ ] **Step 4: Browser check (after Task E1 deploy or local serve)**

Open a Medical card from `/sv-annotate/`. Expected: NiiVue loads the volume; `Mark key step` captures a checkpoint (screenshot non-blank); `Submit & download` produces a `bundle.json` (QC may require ≥60s + ≥2 checkpoints + camera change — rotate/scroll first).

- [ ] **Step 5: Commit**

```bash
cd ~/BabyVision-v2
git add sv-annotate/viewer/shared/capture/viewer-adapters.js
git commit -m "feat(annotate): NiiVue capture adapter for medical NIfTI tasks"
```

---

## Task Group D — Mechanical & architecture example questions

### Task D1: Add OCCT / pointcloud / arch-model routing to `qa_item.html`

**Files:**
- Modify: `viewer/qa_item.html` inside `assetViewerURL` (currently handles molstar/gltf/citymap/nifti/saturn).

**Interfaces:**
- Consumes: `asset.format` (`step`/`iges`/`las`), `asset.viewer` (`arch-model`/`occt`/`pointcloud`), optional `asset.classes`, `asset.name`.
- Produces: viewer URLs `occt.html?file=`, `pointcloud.html?file=[&classes=]`, `arch-model.html?file=&name=`.

- [ ] **Step 1: Assert these formats are currently unrouted**

```bash
cd ~/BabyVision-v2
grep -nE "occt|arch-model|pointcloud|'las'|'step'|'iges'" viewer/qa_item.html || echo "UNROUTED (expected)"
```
Expected: `UNROUTED (expected)`.

- [ ] **Step 2: Add branches after the existing `glb/gltf` branch**

Find:

```js
    if (fmt === 'glb' || fmt === 'gltf') {
      return 'gltf.html?file=' + encodeURIComponent(file);
    }
```

Insert immediately after it:

```js
    // Architecture BIM (split ortho drawings + interactive 3D).
    if (asset.viewer === 'arch-model') {
      return 'arch-model.html?file=' + encodeURIComponent(file) +
             '&name=' + encodeURIComponent(asset.name || asset.label || '');
    }
    // Mechanical CAD B-rep (STEP / IGES) -> OCCT.
    if (fmt === 'step' || fmt === 'stp' || fmt === 'iges' || fmt === 'igs' || asset.viewer === 'occt') {
      return 'occt.html?file=' + encodeURIComponent(file);
    }
    // Infrastructure LiDAR point clouds (LAS) -> point cloud viewer.
    if (fmt === 'las' || fmt === 'laz' || asset.viewer === 'pointcloud') {
      let pu = 'pointcloud.html?file=' + encodeURIComponent(file);
      if (asset.classes) pu += '&classes=' + encodeURIComponent(asset.classes);
      return pu;
    }
```

- [ ] **Step 3: Verify the branches are present**

```bash
cd ~/BabyVision-v2
grep -cE "occt.html\?file=|arch-model.html\?file=|pointcloud.html\?file=" viewer/qa_item.html   # expect 3
```
Expected: `3`.

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add viewer/qa_item.html
git commit -m "feat(qa): route STEP/IGES, LAS, and BIM assets to their viewers"
```

### Task D2: Add mechanical & architecture assets to the assets index

**Files:**
- Modify: `data/assets.index.json` (schema: `{id, domain, family, label, format, path, [viewer], [classes], [name]}`)

**Interfaces:**
- Produces: asset entries whose `id` values Task D3's questions reference via `asset_id`.

- [ ] **Step 1: Confirm current ids (avoid collisions)**

```bash
cd ~/BabyVision-v2
python3 -c "import json;print([a['id'] for a in json.load(open('data/assets.index.json'))['assets']])"
```
Expected: the 12 known ids; none of the four below present.

- [ ] **Step 2: Append four assets to the `assets` array**

Add these objects (mind JSON commas — insert before the closing `]` of `assets`):

```json
{ "id": "industrial/cad/occt_bearing", "domain": "industrial", "family": "cad",
  "label": "OCCT bearing (IGES B-rep)", "format": "iges", "viewer": "occt",
  "path": "/crawl_data/industrial/step_iges_samples/occt_bearing.iges" },
{ "id": "architecture/bim/building_structural", "domain": "architecture", "family": "bim",
  "label": "BIM — structural frame (glTF)", "format": "glb", "viewer": "arch-model",
  "name": "Building-Structural",
  "path": "/crawl_data/architecture/bim_ifc/Building-Structural.glb" },
{ "id": "architecture/infra/banwol_bridge", "domain": "architecture", "family": "infra",
  "label": "Banwol Bridge — LiDAR point cloud", "format": "las", "viewer": "pointcloud",
  "classes": "ground,deck,pier,girder,other",
  "path": "/crawl_data/architecture/bridge_pcl/Banwol-Bridge.las" }
```

(Confirm the LAS class names against `pointcloud.html`'s expected `?classes=` list at implementation time; if the cloud has no per-point classification, omit `classes`.)

- [ ] **Step 3: Validate JSON + assets on disk**

```bash
cd ~/BabyVision-v2
python3 -c "import json;d=json.load(open('data/assets.index.json'));print('assets:',len(d['assets']))"
for p in crawl_data/industrial/step_iges_samples/occt_bearing.iges \
         crawl_data/architecture/bim_ifc/Building-Structural.glb \
         crawl_data/architecture/bridge_pcl/Banwol-Bridge.las; do
  [ -f "$p" ] && echo "OK $p" || echo "MISS $p"
done
```
Expected: JSON parses (count grew by 3); all three assets `OK`. If any `MISS`, they need pulling (they exist in `viewer/assets/{industrial,architecture}.json` manifests — confirm the on-disk `crawl_data` path).

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add data/assets.index.json
git commit -m "data: add mechanical/architecture assets to qa assets index"
```

### Task D3: Author mechanical & architecture example questions

**Files:**
- Modify: `data/qagen_index.json` (append to `questions`)

**Interfaces:**
- Consumes: `asset_id`s created in Task D2.
- Produces: question ids `industrial_cad/q001`, `architecture/q001`, `architecture/q002` referenced by Task D4's demo cards.

- [ ] **Step 1: Append three question objects to `questions`**

Match the existing field shape (`id, family, asset_id, question, answer_kind, ground_truth_kind, domain, answer_schema, ground_truth, asset_file`). No sim-repro params in `question`.

```json
{ "id": "industrial_cad/q001", "family": "industrial_cad",
  "asset_id": "industrial/cad/occt_bearing",
  "question": "In this CAD bearing model, how many rolling elements (balls or rollers) are arranged between the inner and outer races?",
  "answer_kind": "integer", "ground_truth_kind": "human_verified", "domain": "industrial",
  "answer_schema": { "kind": "integer" },
  "ground_truth": { "type": "human_verified", "value": null, "status": "needs_review" },
  "asset_file": "crawl_data/industrial/step_iges_samples/occt_bearing.iges" },
{ "id": "architecture/q001", "family": "architecture",
  "asset_id": "architecture/bim/building_structural",
  "question": "In this structural BIM model, how many above-ground storeys does the primary column grid span?",
  "answer_kind": "integer", "ground_truth_kind": "human_verified", "domain": "architecture",
  "answer_schema": { "kind": "integer" },
  "ground_truth": { "type": "human_verified", "value": null, "status": "needs_review" },
  "asset_file": "crawl_data/architecture/bim_ifc/Building-Structural.glb" },
{ "id": "architecture/q002", "family": "architecture",
  "asset_id": "architecture/infra/banwol_bridge",
  "question": "In this bridge LiDAR point cloud, are the deck points spatially continuous across the full span, or is there a visible gap between deck segments?",
  "answer_kind": "categorical", "ground_truth_kind": "human_verified", "domain": "architecture",
  "answer_schema": { "kind": "categorical", "enums": { "continuity": ["continuous", "gap_present"] }, "fields": ["continuity"] },
  "ground_truth": { "type": "human_verified", "value": null, "status": "needs_review" },
  "asset_file": "crawl_data/architecture/bridge_pcl/Banwol-Bridge.las" }
```

- [ ] **Step 2: Validate JSON + no sim-repro leak in question text**

```bash
cd ~/BabyVision-v2
python3 -c "import json;d=json.load(open('data/qagen_index.json'));print('questions:',len(d['questions']))"
python3 -c "import json;[print('LEAK',q['id']) for q in json.load(open('data/qagen_index.json'))['questions'] if any(k in q['question'].lower() for k in ['dt=','step','substep','solver','rollout'])]" || true
```
Expected: count grew by 3; no `LEAK` lines.

- [ ] **Step 3: Commit**

```bash
cd ~/BabyVision-v2
git add data/qagen_index.json
git commit -m "data: author mechanical + architecture example questions"
```

### Task D4: Relabel the mechanical card and add the architecture card

**Files:**
- Modify: `viewer/qagen.html` demo-cases grid (the hardcoded `<article class="bv2-task-card">` block).

**Interfaces:**
- Consumes: question ids from Task D3.

- [ ] **Step 1: Relabel the Industrial card**

Find:

```html
    <article class="bv2-task-card" data-domain="industrial">
      <h2>Industrial CAD</h2>
      <p class="bv2-task-keywords">glTF · STEP / IGES B-rep · STL / OBJ · USD · SOLIDWORKS · marble warehouse</p>
      <p class="bv2-task-cta"><a href="qa_item.html?id=helmet_caliber/q001">View demo case →</a></p>
    </article>
```

Replace its `<h2>` with `<h2>机械 · Mechanical / CAD</h2>` (keep everything else, including the `helmet_caliber/q001` demo link).

- [ ] **Step 2: Add an Architecture card**

Immediately after the (now relabeled) mechanical card's `</article>`, insert:

```html
    <article class="bv2-task-card" data-domain="architecture">
      <h2>建筑 · Architecture</h2>
      <p class="bv2-task-keywords">IFC/BIM glTF · structural frame · bridge LiDAR point cloud · per-point structural classes</p>
      <p class="bv2-task-cta"><a href="qa_item.html?id=architecture/q001">View demo case →</a></p>
    </article>
```

- [ ] **Step 3: Verify both cards present**

```bash
cd ~/BabyVision-v2
grep -c '机械 · Mechanical' viewer/qagen.html       # expect 1
grep -c '建筑 · Architecture' viewer/qagen.html      # expect 1
grep -c 'id=architecture/q001' viewer/qagen.html     # expect 1
```
Expected: `1`, `1`, `1`.

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add viewer/qagen.html
git commit -m "feat(qa): mechanical/architecture demo cards in qagen grid"
```

---

## Task E1: Deploy to Sing and verify live

**Files:** none (deploy step).

- [ ] **Step 1: Rsync the working tree to Sing (mirror the existing deploy method)**

Confirm the exact deploy command with the repo's `scripts/viewer` / `MIRROR.md` / prior practice before running. Typical:

```bash
cd ~/BabyVision-v2
rsync -avz --delete \
  --include-from=.mirror-include \
  viewer sv-annotate QA-Gen/tasks data crawl_data \
  UniPat-Sing:~/BabyVision-v2/
```
(Use the project's established rsync/deploy invocation; do not invent flags that diverge from it.)

- [ ] **Step 2: Live smoke checks**

```bash
BASE=https://babyvision-v2.unipatai.com
echo "== global nav Expert =="; curl -s $BASE/qagen.html | grep -c 'href="/sv-annotate/">Expert'
echo "== sv-annotate home link =="; curl -s $BASE/sv-annotate/ | grep -c 'class="home" href="/"'
echo "== niivue viewer present =="; curl -sI $BASE/sv-annotate/viewer/niivue.html | head -1
echo "== arch demo question =="; curl -s $BASE/data/qagen_index.json | grep -c 'architecture/q001'
echo "== qagen arch card =="; curl -s $BASE/qagen.html | grep -c '建筑 · Architecture'
```
Expected: `1`, `1`, `HTTP/.. 200`, `>=1`, `1`.

- [ ] **Step 3: Manual browser pass**

- Homepage → every page shows **Expert** in the nav; click it → lands on `/sv-annotate/`.
- `/sv-annotate/` → `← BabyVision-v2 Home` returns to homepage; three domain sections + caffeine; open one Chemistry, one Biology, one Medical card → viewer loads + capture/submit works.
- `qagen.html` → **机械 · Mechanical / CAD** and **建筑 · Architecture** cards; each demo case opens the correct viewer (OCCT bearing; BIM split-view; bridge LiDAR).
- `qagen.html#expert-seed` → each expert-v2 case still opens in `sciencevision_tasks.html`.

---

## Self-Review

**Spec coverage:**
- Part A (nav) → Task A1 + shell home link in Task C2. ✓
- Part B (H20 refresh + mirror-include + QA display) → Tasks B1, B2. ✓
- Part C (data-driven shell, 3 domains, Mol* for chem/bio, NiiVue+adapter for medical) → Tasks C1–C4. ✓
- Part D (relabel mechanical, add architecture, author questions, viewer routing) → Tasks D1–D4. ✓
- Deploy/verify → Task E1. ✓

**Placeholder scan:** No TBD/TODO; all edits show concrete code/JSON and exact grep/curl checks. `ground_truth.value: null` + `status: "needs_review"` is intentional (expert fills later), not a plan placeholder.

**Type/name consistency:** adapter interface (`getCanvas/onCameraChange/getCameraSnapshot/getViewMode/screenshot`) matches `capture-core.js`/`annotate-shell.js` usage; `window.__sv_viewer` set in both viewers; `?set=` produced by the shell (C2) and consumed by the loader (C1); `asset_id`s in D3 match ids created in D2; question ids in D3 match demo-card links in D4.

**Open verification items (flagged inline, resolve during execution):**
- Exact ScienceVision checkout path on H20 (Task B2) — confirm with user.
- NiiVue `screenshot()` non-blank capture and the `onLocationChange/onAzimuthElevationChange/onZoom3DChange` callback names for 0.45.0 (Task C4) — verify in browser.
- `pointcloud.html` `?classes=` expected list (Task D2) — confirm before setting `classes`.
- Project's canonical deploy command (Task E1) — use existing practice, don't invent.
