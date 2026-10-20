# 2D Multi-View Baseline (①) Pilot Runbook

Baseline ① (`claude-code-2d`) — a **pure-vision** agent that sees only
pre-rendered PNG views and has **no access to the raw 3D file**. This
runbook covers the full pipeline: build image → generate tasks → run →
collect → plot.

---

## Prerequisites

- Run on a machine with **EGL available** for offline PNG render, and an
  **Anthropic-compatible LLM endpoint** reachable.
- Two separate Python environments are needed:
  - **mcp/GL env** — has `sci3d_agent.mcp` + pyrender/EGL. Used for
    **generation only** (offline PNG render). Activate before Step 2.
  - **harbor venv** — has the `harbor` CLI. Used for **run, collect, and
    plot** (Steps 3–5). Does NOT need 3D libs or EGL.
- Docker daemon running (needed by `harbor run --env docker`).
- LLM endpoint reachable. Set `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`
  for your gateway. Verify before running:

  ```bash
  export ANTHROPIC_BASE_URL=...   # your Anthropic-compatible gateway
  export ANTHROPIC_API_KEY=...
  curl -sS -m 30 "$ANTHROPIC_BASE_URL/v1/messages" \
      -H "content-type: application/json" -H "anthropic-version: 2023-06-01" \
      -H "x-api-key: $ANTHROPIC_API_KEY" \
      -d '{"model":"claude-opus-4-8","max_tokens":16,
           "messages":[{"role":"user","content":"ping"}]}'
  # expect HTTP 200 with a short assistant reply.
  # If your shell sets a SOCKS proxy, unset the proxy env vars for this call
  # (ALL_PROXY/HTTP_PROXY/... break httpx's Anthropic transport).
  ```

---

## 1. Build the ① agent image (once)

```bash
bash scripts/harbor/templates/build_claude_code_2d_image.sh
```

Produces `sci3d-claude-code-2d:latest` — node:20-slim + `claude` CLI +
`apk` stub routing installs through `npm`. Deliberately **no nibabel /
trimesh / sciencevision-mcp**: this image has no 3D inspection capability
by design.

---

## 2. Generate ① tasks  — MUST run in the mcp/GL env

> **CRITICAL: This step renders PNG views offline using pyrender + EGL.
> It MUST run in the mcp/GL env** (which has `sci3d_agent.mcp` +
> pyrender/EGL), NOT the harbor venv. Activate that env before running.**

```bash
# in the mcp/GL env (NOT the harbor venv):
python -m scripts.harbor.qa_gen_to_tasks \
    --jsonl QA-Gen/tasks/MedicalKAxisPilot/ScienceVision_MedicalKAxisPilot_final_tasks.jsonl \
    --out scripts/harbor/tasks-qagen-2d \
    --asset-root "$(pwd)" \
    --containerized \
    --agent-env claude-code-2d
```

`--asset-root "$(pwd)"` resolves `asset.repo_path` from the JSONL record
onto the host filesystem so the renderer can read the 3D file. The
rendered PNGs — not the raw asset — are what gets staged.

**Security invariant (enforced by the stager):** each generated task dir
contains `environment/views/*.png` and **no** `environment/asset/`
directory. The raw 3D file is never copied into the container. This is
the fundamental constraint that makes ① a pure-vision baseline.

**After generation, verify:**

```bash
# Confirm views present, no asset dir:
ls scripts/harbor/tasks-qagen-2d/*/environment/views/*.png | head -5
ls scripts/harbor/tasks-qagen-2d/*/environment/asset 2>/dev/null \
    && echo "ERROR: asset dir found" || echo "OK: no asset dir"
```

**Expected warnings:** tasks where rendering fails (physics GLB rollouts,
molecular) are recorded with `views_missing` status and still build — they
will produce a degraded task that the agent sees without any view images.
Physics and molecular tasks are **deferred in v1**; `views_missing` on
those is expected, not a bug.

---

## 3. Run the harness (harbor venv)

Switch to the harbor venv. If your shell sets a SOCKS proxy, unset the proxy
vars that block the LLM endpoint (they break httpx's Anthropic transport):

```bash
export ANTHROPIC_BASE_URL=... ANTHROPIC_API_KEY=...   # your gateway

env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
    -u HTTPS_PROXY -u https_proxy \
    harbor run \
    --path scripts/harbor/tasks-qagen-2d \
    --agent claude-code \
    --model claude-opus-4-8 \
    --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
    --ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \
    --ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" \
    --ae "SCI3D_JUDGE_MODEL=claude-haiku-4-5" \
    --env docker \
    -o jobs-cc2d
```

> **`SCI3D_JUDGE_MODEL` — set it, or every reward silently ties at ~0.100.**
> The verifier's LLM-judge default is the *dated* id
> `claude-haiku-4-5-20251001`, which some gateways do **not** serve
> (HTTP 503 `no available accounts supporting model …`). When the judge
> 503s, `llm_judge` **and** `process_judge` both degrade to `0.0` and reward
> collapses to the process floor — a uniform `0.100` across ALL harnesses
> that looks like a real "everyone ties" result but is a scoring outage.
> Forward `SCI3D_JUDGE_MODEL=claude-haiku-4-5` (undated) so the judge
> actually scores. The agent uses `--model claude-opus-4-8` and is
> unaffected either way.

> **`--ae` needs `KEY=VALUE`.** The current harbor (0.13.2+) rejects the
> bare-`KEY` forward form (`--ae ANTHROPIC_BASE_URL`)
> with `ValueError: Invalid env var format … Expected KEY=VALUE`. Always pass
> the value explicitly as shown.

- `--agent claude-code` — harbor's built-in `claude` CLI agent.
  The task's `environment/Dockerfile` is `FROM sci3d-claude-code-2d:latest`
  so the container has the `claude` CLI but no 3D libs.
- `--ae ANTHROPIC_BASE_URL=... --ae ANTHROPIC_API_KEY=...` — forwards those
  env vars from the host shell into the agent container.
- `-o jobs-cc2d` — job output directory; note the path for Step 4.
- The verifier reads the answer from `sci3d_answer.json` if present, else
  falls back to the last agent message in `trajectory.json`. Both paths
  are scored by the same `Sci3dVerifier` unchanged.

**Expected scoring note — `process_judge ≈ 0` is correct:**
Baseline ① runs the `claude` CLI with PNG views only. It never calls the
sci3d tool loop (no MCP, no 3D inspection tools), so `process_judge` is
expected to be near 0 across all trials. This is the fair, documented
outcome for a pure-vision baseline — it is not a bug or a scoring error.
The content score (`llm_judge` + `exact_match` where applicable) still
reflects whether the agent answered correctly from visual evidence alone.

---

## 4. Collect results

```bash
~/.venv-harbor/bin/python -m scripts.harbor.collect_qagen_results \
    --run sci3d-agent:claude-sonnet-4-6:<host_job_dir> \
    --run claude-code:claude-opus-4-8:<cc_job_dir> \
    --run claude-code-mcp:claude-opus-4-8:<ccmcp_job_dir> \
    --run claude-code-2d:claude-opus-4-8:<jobs-cc2d> \
    --tasks-root QA-Gen/tasks
```

Replace `<host_job_dir>`, `<cc_job_dir>`, `<ccmcp_job_dir>`, `<jobs-cc2d>`
with the actual job directories from previous runs. Omit any row whose job
directory you do not have (the collector handles missing/partial sets).

Output (written to `--tasks-root`, default `QA-Gen/tasks`):
- `QA-Gen/tasks/runs_leaderboard.json` — machine-readable four-harness board.
- `QA-Gen/tasks/runs_leaderboard.md` — human-readable ranked table.
- `QA-Gen/tasks/<set>/runs/<harness>__<model>__<task_id>.json` — per-trial
  records (answer + ground_truth + rewards + trajectory).

`collect_qagen_results` groups dynamically by the harness string in
`--run HARNESS:MODEL:JOBDIR`, so `claude-code-2d` appears as a new row
with no code change.

---

## 5. Plot leaderboard (four rows: sci3d-agent + ①②③)

```bash
~/.venv-harbor/bin/python -m scripts.harbor.plot_leaderboard \
    --leaderboard QA-Gen/tasks/runs_leaderboard.json \
    --out-dir QA-Gen/tasks/charts
```

Writes:
- `QA-Gen/tasks/charts/chart_data.json` — aggregated means per
  (harness, domain, component).
- `QA-Gen/tasks/charts/leaderboard_reward_by_domain.png`
- `QA-Gen/tasks/charts/leaderboard_components_by_domain.png`

Null components (scorer not run, or `process_judge ≈ 0` for ①) are
rendered as `n/a` bars, not zeros — distinguishable from a true 0 score.

---

## End-to-end baseline matrix

The pipeline above (Steps 1–5) runs a single baseline (`claude-code-2d`,
one PNG count) by hand. `scripts.harbor.run_end2end` automates the same
build → run → collect → plot pipeline across a **6-baseline matrix** in
one invocation, and replaces the single `claude-code-2d` label with four
`cc2d-N` variants that ablate the number of views the agent sees.

### Input task-list schema

The driver takes a flat JSONL task list — one task per line, independent
of any QA-Gen `_final_tasks.jsonl` layout:

```json
{"task_id": "...", "viewer_link": "https://…/gltf.html?file=…&cam=…", "question": "...", "ground_truth": "...", "scorer": "...", "domain": "..."}
```

- `scorer` and `domain` are optional.
- There is **no** `asset.repo_path` field. The 3D asset path is derived
  from the `file` query param inside `viewer_link`, resolved onto the
  host filesystem via `--asset-root` (same resolution rule as
  `--asset-root "$(pwd)"` in Step 2 above).
- Only `gltf.html` links carry a `cam` param (an encoded camera pose).
  Links into other viewers carry the asset reference only — no camera
  pose to seed from.

### The 6-baseline matrix

`claude-code-2d` is no longer a single row. It is replaced by four
`cc2d-N` variants that share one Docker image and differ only in how
many pre-rendered PNGs are staged:

| Label | Agent env / image | Input the agent sees |
|---|---|---|
| `cc` | `sci3d-claude-code:latest` | raw 3D file at `/data`, no MCP |
| `ccmcp` | `sci3d-claude-code-mcp:latest` | raw 3D file + 57 MCP tools |
| `cc2d-1` | `sci3d-claude-code-2d:latest` | 1 pre-rendered PNG, no file |
| `cc2d-3` | `sci3d-claude-code-2d:latest` | 3 PNGs |
| `cc2d-6` | `sci3d-claude-code-2d:latest` | 6 PNGs |
| `cc2d-12` | `sci3d-claude-code-2d:latest` | 12 PNGs |

The four `cc2d-N` rows **share one image** (`sci3d-claude-code-2d:latest`,
built once in Step 1) — nothing about the image changes per `N`. What
differs is the task-dir build: each of the 6 labels gets its own
task-dir tree with its own `environment/views/*.png` staged (or the raw
file, for `cc`/`ccmcp`), so the driver performs 6 separate builds even
though only one Docker image is involved.

### The one command

```bash
# in the mcp/GL env (build phase needs pyrender/EGL) — see env split below
python -m scripts.harbor.run_end2end \
    --tasks end2end_tasks.jsonl \
    --set-name End2EndPilot \
    --asset-root "$(pwd)" \
    --model claude-opus-4-8 \
    --out-root QA-Gen/tasks \
    --jobs-root jobs-e2e \
    --max-parallel 2 \
    --ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \
    --ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
```

Flags:

- `--tasks <path>` (required) — the end2end task-list JSONL described
  above.
- `--set-name <name>` (required) — QA-Gen set name; used as both the
  output dir name and the jsonl stem under `--out-root`.
- `--asset-root <path>` (default: repo root) — resolves each
  `viewer_link`'s `file` param onto the host filesystem.
- `--model <name>` (default `claude-opus-4-8`) — agent model for all 6
  runs.
- `--out-root <path>` (default `QA-Gen/tasks`) — where the task-dir
  trees, leaderboard, and charts are written.
- `--jobs-root <path>` (default `jobs-e2e`) — parent dir for the 6
  `harbor run` job output directories.
- `--max-parallel <N>` (default `2`) — number of concurrent `harbor run`
  jobs (one per baseline label, up to 6).
- `--seed-from-link` (flag, default off) — mesh-only: seed the render
  orbit from the `cam` param decoded out of the `viewer_link`, instead of
  the canonical view sequence (see the ablation table below). Non-mesh
  assets and links with no `cam` fall back to the canonical sequence with
  a stderr warning. **Default (flag absent):** always use the canonical
  sequences; `cam` is ignored for rendering.
- `--ae KEY=VALUE` (repeatable) — extra env forwarded into each
  `harbor run`, same `KEY=VALUE` requirement as Step 3 above.
- `--dry-run` (flag) — builds all 6 task-dir trees on disk and prints
  the 6 run specs, but skips the `harbor run` / collect / plot subprocess
  steps entirely — no Docker, no `harbor` invocation. Use this to sanity
  check the build phase (PNG counts, staged files) before spending
  compute on the run phase.

**`SCI3D_JUDGE_MODEL` is auto-appended — this is load-bearing, not
cosmetic.** If the caller does not pass `--ae SCI3D_JUDGE_MODEL=...`,
the driver appends `SCI3D_JUDGE_MODEL=claude-haiku-4-5` to every
`harbor run` invocation itself. This exists because of the same failure
mode documented in Step 3 and Troubleshooting below: the verifier's
default judge id (`claude-haiku-4-5-20251001`, dated) 503s on the ALB
gateway, and when it does, `llm_judge` **and** `process_judge` both
degrade to `0.0`, so reward silently collapses to a uniform `~0.100`
across **all six** baselines — a scoring outage that looks like a real
"everyone ties" result. Across a 6-way matrix this failure is easy to
miss because there is no single-baseline sanity check to catch it, so
the driver forces the working undated id by default. A caller may still
pass their own `--ae SCI3D_JUDGE_MODEL=...` to override it.

### Env split

Same two-environment split as the rest of this runbook (see
Prerequisites above): the **build phase** (offline PNG rendering for
`cc2d-*`, asset resolution for `cc`/`ccmcp`) needs the **mcp/GL env**
(pyrender/EGL). The **run / collect / plot phases** need the **harbor
venv** (`~/.venv-harbor`). `run_end2end` does not switch environments
for you — run the whole command in the mcp/GL env, since it drives both
phases sequentially and the build phase's pyrender/EGL dependency is the
binding constraint.

### Outputs

No new output schema — the driver reuses the existing collect/plot
artifacts from Steps 4–5, now populated with 6 rows instead of 1:

- `QA-Gen/tasks/<set>/runs_leaderboard.json` and `.md` — 6-baseline
  leaderboard (`cc`, `ccmcp`, `cc2d-1`, `cc2d-3`, `cc2d-6`, `cc2d-12`).
- `QA-Gen/tasks/<set>/runs/<harness>__<model>__<task_id>.json` — per-trial
  records, one file per (baseline, task) pair.
- `QA-Gen/tasks/<set>/charts/*.png` and `chart_data.json` — same chart
  outputs as Step 5, now with 6 bars/lines per domain instead of 1.

### View-count ablation semantics

Each PNG count `N` corresponds to a fixed, deterministic view sequence
per modality — not a random sample. Views are staged as separate files
(`view_00.png … view_{N-1}.png`); there is no montage/tile sheet, so `N`
is a clean, independently-varied axis. Mesh and point-cloud sequences
orbit at a fixed elevation of 20°:

| Modality | N=1 | N=3 | N=6 | N=12 |
|---|---|---|---|---|
| mesh/CAD (obj/ply/stl/glb/gltf/off, non-physics) | az 0° | az 0/120/240 | az 0/60/…/300 | az 0/30/…/330 |
| point cloud (las/laz) | same azimuthal orbit about the up-axis, elev 20°, as mesh/CAD above | | | |
| NIfTI (medical) | axial mid (frac 0.5) | sag/cor/axial mid | 3 axes × {0.35, 0.65} | 3 axes × {0.2, 0.4, 0.6, 0.8} |
| physics / molecular / urdf / raw | `views_missing: true` (deferred, unchanged from Step 2 above) | | | |

`--seed-from-link` only changes the *starting* azimuth of the mesh/CAD
orbit (seeded from the link's decoded `cam`); it does not change which
`N` maps to which count, or the elevation.

---

## Summary of env-split

| step | env | why |
|------|-----|-----|
| Step 1 — build image | any shell with `docker` | standard `docker build` |
| **Step 2 — generate tasks** | **mcp/GL env** | **needs pyrender + EGL for offline PNG render** |
| Step 3 — `harbor run` | harbor venv (`~/.venv-harbor`) | runs `harbor` CLI, Docker orchestration |
| Steps 4–5 — collect + plot | harbor venv | `matplotlib` + collector (no 3D libs needed) |

---

## Troubleshooting

**All views are `views_missing`:**
Check that the mcp/GL env has `pyrender` installed and `PYOPENGL_PLATFORM=egl`
is set. Run `python -c "import pyrender; import OpenGL"` in that env.

**`harbor run` fails with HTTP 401 `Invalid API Key` on every trial:**
The forwarded key is expired or wrong-typed for the endpoint. Verify your
`ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` with the curl ping in
Prerequisites before launching, and make sure the key type matches what the
gateway expects. Also ensure the proxy vars are unset for the LLM call (the
`env -u ALL_PROXY ...` wrapper in Step 3).

**Every trial scores a uniform `reward ≈ 0.100`, `judge = 0`, across all
harnesses:** The LLM-judge model 503'd — check
`<trial>/verifier/sci3d_scores.json` for
`"error": "...503...no available accounts supporting model..."` with
`"degraded": true`. The verifier judge default (`claude-haiku-4-5-20251001`,
dated) is not served on every gateway. Set
`--ae "SCI3D_JUDGE_MODEL=claude-haiku-4-5"` (undated). Because the agent
rollouts are already on disk, you do **not** need to re-run the agent loop —
re-score offline by calling `harbor_adapters.sci3d._score_core.score_rewards`
against each trial's `agent/` dir with the working `judge_model`.

**`collect_qagen_results` shows no runs for a harness:**
Confirm trial dirs contain `result.json` with a `task_name` field. The
collector looks for `result.json` files with a sibling `agent/` or
`verifier/` directory.

**`views_missing` tasks score 0:**
Expected for physics/molecular in v1. The agent receives no view images;
`llm_judge` may still fire from the instruction text alone, but the score
reflects the absence of visual evidence.
