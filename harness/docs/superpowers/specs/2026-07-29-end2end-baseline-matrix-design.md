# End2End Baseline Matrix + Multiview View-Count Ablation — Design

**Date:** 2026-07-29
**Branch:** `feat/harbor-eval-baselines`
**Status:** Design (approved for planning)

## 1. Motivation

Two extensions to the harbor eval baselines added in `ede69d3`:

1. **View-count ablation.** The `claude-code-2d` pure-vision baseline currently
   renders a modality-hardcoded number of views (NIfTI 3, point cloud 4, mesh 1
   six-tile sheet). We want to study how the *number* of rendered 2D views
   affects a pure-vision agent's accuracy, by turning `claude-code-2d` into a
   family of variants at **1 / 3 / 6 / 12** views.

2. **End-to-end eval driver.** Today a run is a sequence of manual commands
   (build image → `qa_gen_to_tasks` → `harbor run` → `collect` → `plot`) driven
   off a QA-Gen JSONL keyed on `asset.repo_path`. We want a single command that
   takes a task list of `{viewer_link, question, ground_truth, …}`, runs **all
   baselines** over it, and lands organized results — reusing the existing
   collect/plot artifacts.

## 2. Baseline matrix

One end-to-end run covers **6 baselines**:

| Label | Agent env / image | Input the agent sees |
|---|---|---|
| `cc` (`claude-code`) | `sci3d-claude-code:latest` | raw 3D file at `/data`, no MCP |
| `ccmcp` (`claude-code-mcp`) | `sci3d-claude-code-mcp:latest` | raw 3D file at `/data` + 57 MCP tools |
| `cc2d-1` | `sci3d-claude-code-2d:latest` | 1 pre-rendered PNG view, no file |
| `cc2d-3` | `sci3d-claude-code-2d:latest` | 3 PNG views, no file |
| `cc2d-6` | `sci3d-claude-code-2d:latest` | 6 PNG views, no file |
| `cc2d-12` | `sci3d-claude-code-2d:latest` | 12 PNG views, no file |

The single `claude-code-2d` baseline is **replaced** by the 4 `cc2d-N`
variants. The 4 variants share one Docker image; they differ only in how many
PNGs are staged into `environment/views/` and in their leaderboard label.

Consequences:
- Each of the 6 baselines is a **separate task-dir build** (the staged inputs
  differ: `asset/` for cc/ccmcp, N-sized `views/` for each cc2d variant).
- The baseline distinction is baked into the task dir (Dockerfile + staged
  inputs), so all 6 use the same harbor `--agent claude-code` adapter; the
  leaderboard **harness label** is what separates them at collect time.

## 3. Component 1 — View-count ablation

### 3.1 Per-modality N-view sequences (orbit-uniform, elevation 20°)

| Modality | N=1 | N=3 | N=6 | N=12 |
|---|---|---|---|---|
| mesh/CAD (obj/ply/stl/glb/gltf/off, non-physics) | az 0° | az 0/120/240 | az 0/60/…/300 | az 0/30/…/330 |
| point cloud (las/laz) | same orbit (azimuth about the up axis), elev 20° | | | |
| NIfTI (medical) | axial mid (frac 0.5) | sag/cor/axial mid | 3 axes × {0.35, 0.65} | 3 axes × {0.2, 0.4, 0.6, 0.8} |
| physics / molecular / urdf / raw | `views_missing: true` (unchanged, deferred) | | | |

- mesh & point cloud share an azimuthal orbit at fixed elevation 20°; azimuths
  are evenly spaced starting at 0°.
- Each view is written as a **separate PNG** (`view_00.png … view_{N-1}.png`).
  The 6-tile montage sheet is dropped so that "number of views" is a clean
  independent variable rather than "tiles inside one image".
- NIfTI axis remains one of the 3 canonical planes (renderer only supports
  those); the ablation varies slice count per axis.

### 3.2 Renderer extensions (Category D, `sci3d_agent/mcp/tools/_render/`)

- **`mesh.py::camera_for_view`** — add an arbitrary-pose path: accept
  `azimuth`/`elevation` (compute the direction vector from angles) and/or an
  explicit `eye`/`target` (used by seed mode, §4.3). Internals already derive
  `eye` from a direction vector, so this is additive. Loosen the
  `sv_render_mesh_thumbnail` schema `view` enum to allow the new parameters.
- **`pointcloud.py`** — rotate the point cloud about the up axis by `azimuth`
  (and tilt by `elevation`) before the 2-axis projection, so orbit views beyond
  the 4 fixed projections are possible.
- **NIfTI** — no renderer change; `handle_nifti_slice`/`_overlay` already accept
  an arbitrary per-axis `index`/`fraction`.

### 3.3 `plan_views` parameterization (`scripts/harbor/_multiview.py`)

- `plan_views(task, *, size=512, best_slices=None, n_views=6, seed_pose=None)`.
- `n_views` selects the row from the §3.1 table per modality and emits the
  ordered `(tool_name, args)` render calls (now per-view for mesh, using the new
  azimuth/elevation args).
- `seed_pose` (optional, §4.3) shifts the mesh orbit's starting azimuth/elevation.

### 3.4 Optional render-reuse optimization (efficiency, not required for v1)

For mesh & point cloud the lower tiers' azimuths are subsets of the 12-view
orbit (120,240 ∈ {0,30,…,330}; 0/60/…/300 ⊂ same). The driver *may* render the
12-view orbit once per modality and stage a subset for the 1/3/6 variants,
avoiding 4× GL renders. NIfTI tiers as defined are not strictly nested, so
NIfTI renders per tier. This is an optimization; the correct-but-simple default
is to render each tier independently. Flagged so it is a deliberate choice, not
a silent cap.

## 4. Component 2 — End-to-end driver

### 4.1 Input task list (JSONL, one task per line)

```json
{"task_id": "...", "viewer_link": "https://…/gltf.html?file=…&cam=…",
 "question": "...", "ground_truth": "...", "scorer": "...", "domain": "..."}
```

`asset.repo_path` is **not** required in the input — it is derived from
`viewer_link`. Other fields (`scorer`, `allowed_tools`, `forbidden_tools`,
`mask_path`) are passed through to the existing task builder when present.

### 4.2 Viewer-link resolver (`scripts/harbor/_viewer_link.py`)

Parse a viewer URL into `{viewer_type, asset_path, cam?, slice?}`:
- `file` query param → asset path (mapped onto the host via `--asset-root`, same
  resolution as `qa_gen_to_tasks._resolve_asset_path`).
- `cam` (gltf viewer only) → `decode_cam`, the inverse of
  `scripts/eval/sv3d_emit_viewer_urls.py::encode_cam`
  (`base64url(JSON {position,target,up,fov_deg})`).
- `slice` (gltf viewer only) → `axis:fraction:window:level` per
  `viewer/shared/cam_url.js::decodeSlice`.
- Non-gltf viewers (niivue/pointcloud/cornerstone/molstar) carry no pose; only
  `asset_path` is extracted.

The resolver synthesizes the task dict that the existing
`qa_gen_to_tasks`/`build_one` pipeline already consumes, so downstream code is
unchanged.

### 4.3 Seed switch: `--seed-from-link` vs `--canonical` (default)

- **`--canonical` (default):** all modalities use the §3.1 canonical sequences;
  fully reproducible, `cam` ignored for rendering.
- **`--seed-from-link`:** for **mesh only**, decode the link `cam`, use it as the
  N=1 view, and start the orbit from that azimuth/elevation. Non-mesh assets, or
  mesh assets whose link carries no `cam`, **log a warning and fall back to
  canonical**. (Only gltf links carry pose; this is a known, logged limitation.)

### 4.4 Driver flow (`scripts/harbor/run_end2end.py`)

1. **Resolve** each input line via §4.2 → task dicts.
2. **Build × 6** task-dir trees (reusing `build_one` + the `AGENT_ENVS` table):
   `cc`, `ccmcp`, `cc2d-1`, `cc2d-3`, `cc2d-6`, `cc2d-12` (cc2d variants pass the
   matching `n_views` + optional `seed_pose`). 2D renders require the GL/EGL
   env; the build step runs there.
3. **Run × 6, concurrently** — schedule the 6 `harbor run` invocations with a
   `--max-parallel N` cap (default 2). Each writes to its own timestamped
   jobdir, so there is no cross-run collision. Each run uses the shared
   `--verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier` and the
   required agent-env flags (incl. `SCI3D_JUDGE_MODEL=claude-haiku-4-5` — the
   undated id, per the runbook: the dated default 503s and silently collapses
   reward to ~0.100).
4. **Collect + plot** after all runs finish: `collect_qagen_results` with the 6
   harness labels → `QA-Gen/tasks/runs_leaderboard.json/.md`; `plot_leaderboard`
   → `QA-Gen/tasks/charts/`.

### 4.5 Output

Fully reuses existing artifacts — no new result schema:
- `QA-Gen/tasks/runs_leaderboard.json` + `.md` (6 baseline rows)
- `QA-Gen/tasks/<set>/runs/<harness>__<model>__<task_id>.json` per trial
- `QA-Gen/tasks/charts/*.png` + `chart_data.json`

## 5. File-level change map

**New:**
- `scripts/harbor/_viewer_link.py` — viewer-URL parser + `decode_cam`/`decode_slice`.
- `scripts/harbor/run_end2end.py` — the orchestrator (resolve → build×6 → run×6 concurrent → collect → plot).

**Modified:**
- `sci3d_agent/mcp/tools/_render/mesh.py` — arbitrary azimuth/elevation (+ eye/target) pose.
- `sci3d_agent/mcp/tools/_render/pointcloud.py` — orbit rotation before projection.
- `sci3d_agent/mcp/schemas/sv_render_mesh_thumbnail.json` (+ pointcloud schema) — allow new params.
- `scripts/harbor/_multiview.py` — `plan_views(..., n_views, seed_pose)` + per-view mesh calls + N-sequence table.
- `scripts/harbor/build_harbor_tasks.py` — thread `n_views`/`seed_pose` through `_stage_rendered_views`; register `cc2d-1/3/6/12` labels.
- `scripts/harbor/qa_gen_to_tasks.py` — accept resolver-produced task dicts (viewer-link route).
- `docs/harbor/PILOT_2D_RUNBOOK.md` — document the end2end command + the 6-baseline matrix.

## 6. Testing

- **Renderer:** unit tests that mesh/pointcloud honor an arbitrary azimuth
  (distinct output from a canonical preset; N-view orbit produces N distinct PNGs).
- **`plan_views`:** parametrized over `n_views ∈ {1,3,6,12}` × modality →
  correct call count and args; unsupported modality → `None`.
- **Viewer-link resolver:** `decode_cam` round-trips `encode_cam`; gltf link with
  `cam`/`slice` parsed; non-gltf link yields asset-only; malformed link errors cleanly.
- **Seed switch:** `--seed-from-link` on a non-mesh / cam-less link falls back to
  canonical and logs.
- **Driver (dry-run):** with a fake harbor runner, a 1-task list fans out to 6
  build dirs with the expected staged inputs and 6 run specs; concurrency cap respected.
- Follow existing `tests/harbor_adapters/` patterns; renderer GL tests skip via
  the `renderer_skipped` envelope on GL-less machines.

## 7. Out of scope (v1)

- Pose-carrying viewer links for non-gltf viewers (niivue/pointcloud/etc.) — they
  don't encode a camera; seed mode stays mesh-only.
- Rendering physics/molecular/urdf/raw modalities (still `views_missing`).
- A new results/leaderboard schema or viewer `#qagen` tab (reuse existing collect+plot).
- New MCP session camera-pose setter (`sv3d_camera_set_pose`) — the ablation uses
  the Category-D static renderers directly, not a live session.
