# EV2 eval pipeline: explicit content/process scoring + full-artifact re-run

Date: 2026-07-30
Branch: `feat/harbor-eval-baselines`

## Goal

Two deliverables driven by the EV2 pilot (15 3D-grounded expert QA tasks, three
baselines cc / cc2d / ccmcp):

1. **Task 1 — explicit content/process marking.** Every eval report must show
   the **content** score and the **process** score side-by-side with the
   composite **reward**, at both the per-task and aggregate levels, with
   consistent terminology across the pipeline.
2. **Task 2 — re-run with full artifact capture.** Re-run the 15 EV2 tasks on
   the updated pipeline, persisting per-task **answers**, **trajectories**, and
   **render screenshots** together.

## Background (verified findings)

- **Scoring core already emits both scores.** `harbor_adapters/sci3d/_score_core.py::score_rewards`
  writes `sci3d_scores.json` containing `llm_judge` (= content), `process_judge`
  (= process), and `weights {content: 0.7, process: 0.3}`, with
  `reward = 0.7·content + 0.3·process`. **Task 1 is a reporting-layer change; the
  scoring core is not touched.**
- **Reporting is inconsistent (the Task 1 gap):**
  - `collect_qagen_results.py` → `runs_leaderboard`: aggregate has only mean
    **reward**; per-task has `llm_judge`/`process_judge` columns.
  - `report_ev2_threeway.py` → `EV2_THREEWAY`: baseline summary has
    `llm_judge`/`process_judge`; per-task has **only reward**.
  - `collect_ev2_content.py` → `EV2_CONTENT`: only **content**.
- **Render screenshots are ephemeral (the Task 2 new work).** MCP render tools
  (`sci3d_agent/mcp/tools/render_static.py`, `.../vision3d/snapshot.py`) write
  PNGs to `render_cache_dir()` = `$SV_RENDER_CACHE` (default `/tmp/sv_renders`,
  inside the container, **not archived by harbor**). Hence 0 PNGs in job trees.
- **Existing ccmcp runs barely called render tools.** Of 15 tasks, only 4
  invoked a render tool (`sv_render_nifti_mip`×2, `sv_render_nifti_slice`×1,
  `sv3d_render_canonical_views`×1), all in the medical/nifti domain; chemistry
  tasks mostly called no MCP tool. Screenshot volume on re-run is
  agent-behavior-dependent — we capture whatever is rendered, not more.
- Judge model must be **`claude-haiku-4-5`** (undated); the dated
  `claude-haiku-4-5-20251001` 503s on the ALB and silently degrades reward to
  the 0.100 floor. Harbor runs on venv py3.12; API calls need proxies cleared
  (`env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy`).

## Task 1 design — content/process marking

Principle: content, process, reward are all first-class and always shown
together, labeled "content"/"process" (not "llm_judge"/"proc_judge").

### `collect_qagen_results.py`
- Aggregate: alongside `by_harness_model_mean_reward`, add mean **content** and
  mean **process** (new keys, e.g. `by_harness_model` → `{mean_reward,
  mean_content, mean_process}`; keep the old reward key or migrate — pick
  non-breaking). Same for the by-domain grouping.
- `runs_leaderboard.md`: aggregate tables gain **content** and **process**
  columns; per-task table relabels `judge`/`proc_judge` → **content**/**process**
  and keeps reward.

### `report_ev2_threeway.py`
- Per-task: currently reward only. Add content and process as **three stacked
  per-task sections** (reward / content / process), reusing the existing table
  layout (the `EV2_CONTENT` per-task-content table proves the format).
- Baseline summary: relabel existing `llm_judge`/`process_judge` →
  **content**/**process**.
- JSON output (`EV2_THREEWAY.json`): add per-task `content` and `process` blocks
  next to the existing per-task reward.

### `collect_ev2_content.py`
- **Fold into `report_ev2_threeway.py`** and remove the standalone script:
  threeway becomes the single canonical content+process+reward report,
  eliminating口径 drift. (The tier-selection logic rerun > rescored > degraded
  is already identical in both, so the merge is mechanical.)

### Not changed
- `sci3d_scores.json` schema stays as-is (fields already present; renaming would
  ripple through both scoring routes). Explicit labels live in the reports.

## Task 2 design — re-run with full artifact capture

### Screenshot capture mechanism (the real new work)
- Set `ENV SV_RENDER_CACHE=/app/renders` in the ccmcp and cc agent-env
  Dockerfiles (`_agent_envs.py`), so MCP-rendered PNGs land in the agent
  workspace that harbor archives into the trial's `agent/` tree.
- Verify with a single-task smoke run that PNGs actually appear in the collected
  trial before doing the full matrix.
- cc2d screenshots are the pre-rendered input views (`tasks-ev2-cc2d/<task>/views/*.png`),
  already materialized by `qa_gen_to_tasks`; they are collected as-is.

### Re-run scope
- All three baselines (cc / cc2d / ccmcp) over the 15 EV2 tasks, to refresh the
  full three-way comparison on the updated pipeline.
- Judge = `claude-haiku-4-5`; proxies cleared; harbor venv py3.12. Reuse the
  existing `scripts/harbor/tasks-ev2-*` trees + `harbor run` (or `run_end2end.py`);
  the rerun/rescore path (`rerun_ev2_errored.sh`, `rescore_rerun.py`) remains the
  fallback for degraded trials.

### Artifact collocation
- Extend `collect_qagen_results.py` (which already writes per-task run records
  with answer + trajectory) to also copy the trial's render PNGs into
  `runs/<slug>/screenshots/`, so answer + trajectory + screenshots live together
  per task.

### Report regeneration
- Regenerate `EV2_THREEWAY` (now content+process+reward) and the viewer bundle
  (`build_ev2_viewer_bundle.py`) from the fresh runs.

## Execution order & cost

1. Implement Task 1 report changes + Task 2 screenshot-capture wiring.
2. Single-task smoke run (ccmcp, a medical task known to render) to verify PNGs
   are captured and the updated reports render content/process.
3. Full re-run of 15 tasks × 3 baselines — the expensive live-environment step
   (~45 container runs + judge API), requires H20 images
   `sci3d-claude-code{,-2d,-mcp}:latest` and API creds.
4. Regenerate reports + viewer bundle; sanity-check numbers vs the prior
   `EV2_THREEWAY`.

## Out of scope
- Changing the 0.7/0.3 content/process weighting.
- Changing the scoring core or `sci3d_scores.json` schema.
- Forcing the agent to call render tools it wouldn't otherwise call.
