# ScienceVision MCP Server — Phased Rollout Plan

> Companion to [`MCP_SERVER_SPEC.md`](./MCP_SERVER_SPEC.md).
> 4 phases. Each phase has internal parallelism (multiple background agents).
> Phases gate on hard acceptance criteria before the next starts.

---

## 0. Dependency DAG

```
Phase 0  ── Skeleton (1 agent, sequential)
              │
              ▼
Phase 1  ── A · Catalog        ┐
            B · URL builders   │ 4 agents in parallel
            C · Inspectors     │ (no cross-deps; no render)
            E1 · Pipeline-read ┘
              │
              ▼
Phase 2  ── D · Static render  ┐ 2 agents in parallel
            E2 · Pipeline-write┘ (D needs C; E2 independent)
              │
              ▼
Phase 3  ── F-core  (1 agent: session mgr + open/close/get_state)
              │
              ▼
            F-camera           ┐ 2 agents in parallel
            F-render-state     ┘
              │
              ▼
            F-snapshot (1 agent: render + canonical_views)
```

**Why this order:** A/B/C/E1 have zero render dependencies and can ship in a day. D
needs C (uses `axis_up_hint`, `best_slices`, etc.). F is the heaviest engineering
work (sessions, GPU queue, axis-up normalization) and benefits from D's render
primitives. E2 is parallelized into Phase 2 only because its `dry_run` defaults
make it low-risk even if Phase 1's catalog isn't 100% stable yet.

---

## 1. Repo layout (agents must respect this; no overlap = no merge conflicts)

```
sci3d_agent/
└── mcp/
    ├── __init__.py
    ├── server.py              # Phase 0 — entry, registry, MCP wiring
    ├── registry.py            # Phase 0 — @register decorator, ToolSpec dataclass
    ├── runtime/               # Phase 0 — shared infra
    │   ├── paths.py           # SV_REPO_ROOT / SV_VIEWER_BASE resolution
    │   ├── errors.py
    │   └── cache.py
    ├── tools/
    │   ├── catalog.py         # Wave 1 · A   (11 tools)
    │   ├── urls.py            # Wave 1 · B   ( 4 tools)
    │   ├── inspect.py         # Wave 1 · C   ( 8 tools)
    │   ├── pipeline_read.py   # Wave 1 · E1  ( 5 tools)
    │   ├── render_static.py   # Wave 2 · D   ( 7 tools)
    │   ├── pipeline_write.py  # Wave 2 · E2  ( 3 tools)
    │   └── vision3d/
    │       ├── __init__.py
    │       ├── session.py     # Phase 3.1 · F-core
    │       ├── camera.py      # Phase 3.2 · F-camera
    │       ├── state.py       # Phase 3.2 · F-render-state
    │       └── snapshot.py    # Phase 3.3 · F-snapshot
    └── schemas/                # JSON-Schema mirror of every inputSchema
        └── *.json
tests/mcp/
    ├── test_skeleton.py
    ├── test_catalog.py
    ├── test_urls.py
    ├── test_inspect.py
    ├── test_pipeline_read.py
    ├── test_render_static.py
    ├── test_pipeline_write.py
    └── test_vision3d/
        ├── test_session.py
        ├── test_camera.py
        ├── test_state.py
        └── test_snapshot.py
```

Each Wave-1+ agent **only writes to its own files** (above). Shared infra
(`registry.py`, `runtime/`) is locked after Phase 0.

---

## 2. Coordination contract (set in Phase 0, locked forever)

```python
# registry.py
@dataclass(frozen=True)
class ToolSpec:
    name: str                          # e.g. "sv_search_matched_qa"
    description: str
    input_schema: dict                 # JSON-Schema
    handler: Callable[[dict], dict]    # pure function, immutable in/out
    category: Literal["A","B","C","D","E","F"]

REGISTRY: dict[str, ToolSpec] = {}

def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in REGISTRY:
        raise ValueError(f"duplicate tool {spec.name}")
    REGISTRY[spec.name] = spec
    return spec
```

Each tool module exports `TOOLS: list[ToolSpec]`. `server.py` imports every
module and unions registries. **No global state in tools** (except F's session
store, which lives in `vision3d/session.py`).

---

## 3. Phase 0 — Skeleton

**Goal:** Bootable MCP server with empty tool registry. Lock all shared infra.

**Single agent (sequential).** ~30–60 min.

### Deliverables
- `sci3d_agent/mcp/{server.py, registry.py, runtime/*.py, tools/__init__.py}`
- `tools/{catalog,urls,inspect,pipeline_read,render_static,pipeline_write}.py` with `TOOLS = []`
- `tools/vision3d/__init__.py` with `TOOLS = []`
- `pyproject.toml` `[project.scripts] sciencevision-mcp = "sci3d_agent.mcp.server:main"`
- `tests/mcp/test_skeleton.py`

### Acceptance gate (all must pass)
1. `uvx --from . sciencevision-mcp --list-tools` exits 0 with `{"tools":[]}`.
2. `pytest tests/mcp/test_skeleton.py` green.
3. `curl -X POST http://localhost:1984/list-tools | jq '.tools | length'` returns `0`.

### Agent prompt
```
You are bootstrapping the `sciencevision` MCP server.

Read /Users/sue/Research/ScienceVision/docs/mcp/MCP_SERVER_SPEC.md sections
0 and 1, and MCP_ROLLOUT_PLAN.md sections 1–3.

Implement ONLY Phase 0 deliverables (no tools yet — they ship in later phases):

1. Choose MCP framework: fastmcp (preferred — minimal boilerplate) OR raw
   `mcp` SDK if fastmcp doesn't fit our registry pattern. Justify in 1 line.
2. Create the file tree above. Lock the @register decorator and ToolSpec.
3. server.py: load all `TOOLS` lists from tool modules; expose /list-tools
   and /call-tool over stdio (MCP standard) AND optionally HTTP on 1984
   (mcp-atlas style) — gate the HTTP server behind `SV_HTTP_PORT` env var.
4. Pass pytest. 80% coverage minimum for the files you create.

Hard rules:
- Immutable data: handlers receive dict, return new dict — never mutate input.
- Strict env-only secrets (no key args).
- Files <800 lines each.
- No tools registered yet — all TOOLS lists empty.
```

---

## 4. Phase 1 — Foundation (4 agents in parallel)

**Goal:** 28 read-only tools registered. Headless agents can browse manifests,
build URLs, inspect assets, run read-only evaluations.

**Run-in-background:** 4 agents, dispatched in one message.

### Agent A1 — Catalog (11 tools)
- **Owns** `tools/catalog.py`, `tests/mcp/test_catalog.py`, `schemas/sv_list_*.json` etc.
- **Reads** `viewer/{assets,datasets_assets,negative_assets,synthetic_negative_assets}.json`, `datasets/_matched/index.json + per-dataset files`, `spec/astronomy/questions.json`.
- **Schema source of truth:** SPEC §2.
- **Tests must include:** envelope shape `{records, total, offset, limit}` invariant; missing-qid returns top-level `error` (not exception); pagination cursor stability under random offsets.

### Agent B — URL builders (4 tools)
- **Owns** `tools/urls.py`, `tests/mcp/test_urls.py`.
- **Reads** `viewer/assets.json` (for `viewer` field auto-resolve).
- **Schema source of truth:** SPEC §3.
- **Tests must include:** `sv_build_viewer_url` rejects `overlay` on `viewer=gltf`; auto-resolve picks niivue for `.nii.gz`; `SV_VIEWER_BASE` env override honored; URL-encoding of paths with spaces.

### Agent C — Inspectors (8 tools)
- **Owns** `tools/inspect.py`, `tests/mcp/test_inspect.py`.
- **Deps** `trimesh`, `nibabel`, `laspy`, `gemmi`, `yourdfpy` (add to `pyproject.toml`).
- **Schema source of truth:** SPEC §4.
- **Tests must include:** golden fixture for one ScanNet PLY, one M3D-RefSeg CT+mask pair (use small synthetic if real fixture too large); `sv_inspect_nifti_mask_pair` returns `best_slices` matching a hand-computed expected slice index ±1.
- **Caching:** implement `runtime/cache.py` key scheme `sha1(abs_path+mtime_ns+tool+args)`.

### Agent E1 — Pipeline-read (5 tools)
- **Owns** `tools/pipeline_read.py`, `tests/mcp/test_pipeline_read.py`.
- **Tools:** `sv_score_astronomy`, `sv_classify_v`, `sv_get_v_classify_pilot_summary`, `sv_validate_matched_index`, `sv_run_v_validation`.
- **Schema source of truth:** SPEC §6.
- **Tests must include:** astronomy scoring against canned GT; `sv_classify_v` mocked (real LLM call gated by `SV_E2E=1`); `sv_validate_matched_index` over a small fixture index with deliberate missing path.

### Phase 1 acceptance gate
1. `sciencevision-mcp --list-tools | jq '.tools | length'` ≥ 28.
2. All 4 test suites green; combined coverage ≥80%.
3. Smoke call: `sv_search_matched_qa(dataset="m3d-refseg", has_mask=true, limit=1)` returns a record whose `extra.mask_url_viewer` resolves with `sv_build_viewer_url` to a valid niivue URL.

---

## 5. Phase 2 — Static render + safe writes (2 agents in parallel)

**Goal:** 38 tools. Agents can see assets via PNG and run write actions with safety.

### Agent D — Static renderers (7 tools)
- **Owns** `tools/render_static.py`, `tests/mcp/test_render_static.py`.
- **Deps** `pyrender` (EGL), `pyvista`, `vtk`, `nilearn`, `matplotlib`.
- **Calls into** Agent C's inspectors (`sv_inspect_nifti_mask_pair` for `sv_render_qa_record_summary` slice picking, `sv_inspect_mesh` for `axis_up`).
- **Schema source of truth:** SPEC §5.
- **Infra:** implement `/tmp/sv_renders/<sha>.png` cache + URL-or-base64 envelope decision (≤300 KB inline, else URL).
- **Tests must include:** golden hash PNG for one mesh thumbnail; `sv_render_nifti_overlay` on a synthetic CT+mask returns non-empty `mask_pixel_count`; headless GL probe at import time.

### Agent E2 — Pipeline-write (3 tools, dry_run default)
- **Owns** `tools/pipeline_write.py`, `tests/mcp/test_pipeline_write.py`.
- **Tools:** `sv_classify_v_batch`, `sv_rebuild_matched_index`, `sv_run_m3d_pipeline`.
- **Schema source of truth:** SPEC §6.
- **Hard rule:** every tool defaults `dry_run=true`; only writes when caller passes `false` AND emits a confirmation log line.
- **Tests must include:** dry-run returns plan/diff but does NOT touch filesystem; with `dry_run=false` writes to a tmp output_dir; re-run idempotency for `sv_rebuild_matched_index`.

### Phase 2 acceptance gate
1. Tool count ≥ 38.
2. Golden PNG hash for one ScanNet mesh thumbnail matches across two cold runs (proves determinism).
3. `sv_rebuild_matched_index(dry_run=true)` on canonical state returns empty diff.
4. End-to-end: `sv_render_qa_record_summary(qid=<some m3d-refseg qid>)` returns a PNG with non-zero mask pixels.

---

## 6. Phase 3 — Interactive 3D-Vision (3 sub-waves)

**Goal:** 53 tools. Headless agent can drag-orbit / zoom / scrub MPR / toggle mask
just like a human in `viewer/match3d.html`.

### Phase 3.1 — F-core (1 agent, sequential)
- **Owns** `tools/vision3d/session.py`, `tools/vision3d/__init__.py`, `tests/mcp/test_vision3d/test_session.py`.
- **Tools:** `sv3d_open_scene`, `sv3d_close_scene`, `sv3d_get_state`.
- **Infra (hardest engineering work):**
  - Session store (in-process dict + TTL eviction + LRU cap).
  - GPU render queue (asyncio semaphore; max-concurrent-sessions from env).
  - Axis-up normalization (Z-up ScanNet, Y-up glTF, LPS/RAS NIfTI → internal RAS).
  - Mask centroid precompute at open time (one pass per label, cached on session).
  - Canonical camera state dataclass `{position, target, up, fov_deg}` in world coords.
- **Schema source of truth:** SPEC §1 (tools 1, 2, 15) + §1 sharp-edges 1–8.
- **Acceptance:** `sv3d_open_scene(qid=<m3d-refseg>)` returns session_id + `axis_up=RAS` + non-empty `masks[]`; `sv3d_get_state` echoes back state immutably; `sv3d_close_scene` releases GPU mem (verified by `nvidia-smi` delta).

### Phase 3.2 — F-camera + F-render-state (2 agents in parallel)
Both depend on Phase 3.1's session store + camera dataclass.

#### Agent F-camera
- **Owns** `tools/vision3d/camera.py`, `tests/mcp/test_vision3d/test_camera.py`.
- **Tools:** `sv3d_camera_orbit`, `sv3d_camera_dolly`, `sv3d_camera_pan`, `sv3d_camera_set_view`, `sv3d_camera_look_at`.
- **Schema source of truth:** SPEC §1 (tools 4–8).
- **Hard rule:** all camera ops return canonical world-coord `{position, target, up, fov_deg}` echo; orbit accumulates around current target; `set_view` consults session `axis_up`.

#### Agent F-render-state
- **Owns** `tools/vision3d/state.py`, `tests/mcp/test_vision3d/test_state.py`.
- **Tools:** `sv3d_set_render_mode`, `sv3d_set_opacity`, `sv3d_toggle_mask`, `sv3d_set_slice`, `sv3d_set_clip_plane`.
- **Schema source of truth:** SPEC §1 (tools 3, 9–12).
- **Hard rule:** `set_slice` accepts `index` OR `fraction` (not both); `mask_id` must be label-int matching `extra.mask_id`; window/level only valid for NIfTI base.

### Phase 3.3 — F-snapshot (1 agent, sequential)
- **Owns** `tools/vision3d/snapshot.py`, `tests/mcp/test_vision3d/test_snapshot.py`.
- **Tools:** `sv3d_render`, `sv3d_render_canonical_views`.
- **Schema source of truth:** SPEC §1 (tools 13–14).
- **Hard rule:** every render echoes full state (camera + mode + slice + mask_state) so the agent can reason about what it just saw; deterministic seed + no temporal AA.

### Phase 3 acceptance gate (end-to-end)
1. Tool count = 53.
2. **M3D-RefSeg trajectory test** (`tests/mcp/test_vision3d/test_e2e_m3d_refseg.py`):
   - open_scene(qid) → set_render_mode(mpr_axial) → set_slice(axial, fraction=0.5) → toggle_mask(visible=true, alpha=0.5) → render → PNG with mask_pixel_count > 0.
3. **ScanQA trajectory test:** open_scene(scanqa qid) → set_render_mode(surface) → render_canonical_views(tile=true) → PNG with 6 tiles + axes labels.
4. **GPU OOM behavior:** opening N+1 sessions (where N=max-concurrent) returns clean error, not crash.
5. **Recovery:** close + reopen session reproduces identical PNG hash given identical state.

---

## 7. Parallel launch table

| Phase | Wave | Agents | Total | Blocking dep |
|---|---|---|---|---|
| 0 | — | Skeleton | 1 | — |
| 1 | 1 | A1, B, C, E1 | 4 | Phase 0 |
| 2 | 2 | D, E2 | 2 | Phase 1 (D needs C) |
| 3 | 3.1 | F-core | 1 | Phase 2 |
| 3 | 3.2 | F-camera, F-render-state | 2 | Phase 3.1 |
| 3 | 3.3 | F-snapshot | 1 | Phase 3.2 |

Wall-clock estimate (with parallelism): Phase 0 ≈ 1h, Phase 1 ≈ 3h (4-way), Phase 2 ≈ 4h, Phase 3 ≈ 6h. End-to-end ~14h vs ~40h serial.

---

## 8. Cross-cutting rules (apply to every agent prompt)

1. **Do NOT explore the repo.** All context is in `MCP_SERVER_SPEC.md` + this plan.
2. **Read SPEC sections relevant to your category, then implement.** Do not invent tool names or fields not in SPEC.
3. **Immutability:** handlers are pure functions; never mutate input dicts; return new dicts.
4. **File ownership:** only write to files listed in your "Owns" section. Do not edit `registry.py` or another agent's files.
5. **Tests are required.** pytest, ≥80% coverage on owned files. No `time.sleep` polling in tests.
6. **No secrets in args.** Env-only (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).
7. **Errors:** raise `SVToolError` from `runtime/errors.py`; return `{error: str}` envelope rather than throwing across the MCP boundary.
8. **Coverage report at end:** print one line `category=X tools=N coverage=P%` so the orchestrator can verify the gate.

---

## 9. Phase-launch prompt templates

### Phase 1 launch (paste into 4 parallel Agent calls)

```
[Agent A1 prompt]
You are implementing Wave 1 / Agent A1 (Catalog) of the ScienceVision MCP server.

READ FIRST (no other exploration):
- /Users/sue/Research/ScienceVision/docs/mcp/MCP_SERVER_SPEC.md §2
- /Users/sue/Research/ScienceVision/docs/mcp/MCP_ROLLOUT_PLAN.md §4 "Agent A1" + §8

YOU OWN (write only these):
- sci3d_agent/mcp/tools/catalog.py
- tests/mcp/test_catalog.py
- sci3d_agent/mcp/schemas/sv_list_*.json etc. (one file per tool)

DELIVER: 11 tools per SPEC §2. Pure functions. Envelope {records,total,offset,limit}.
GATE: pytest tests/mcp/test_catalog.py green; ≥80% coverage on catalog.py.

Report at end: "category=A tools=11 coverage=P%"
```

(Mirror prompts for B / C / E1 — change category, section refs, owned files, tool count.)

### Phase 3.1 launch (single agent)
```
You are implementing Phase 3.1 / F-core of the ScienceVision MCP server. This is
the load-bearing 3D Vision session manager. All of Phase 3.2 and 3.3 build on you.

READ FIRST:
- docs/mcp/MCP_SERVER_SPEC.md §1 (entire section — every sharp-edge applies)
- docs/mcp/MCP_ROLLOUT_PLAN.md §6 / Phase 3.1 + §8

YOU OWN:
- sci3d_agent/mcp/tools/vision3d/{__init__.py, session.py}
- tests/mcp/test_vision3d/test_session.py
- Session store + GPU queue + axis-up normalization (these are infra, not tools)

DELIVER: 3 tools (sv3d_open_scene, sv3d_close_scene, sv3d_get_state) + the
session infrastructure that Phase 3.2/3.3 agents will import.

CRITICAL: Axis-up normalization. ScanNet PLY Z-up; glTF Y-up; NIfTI LPS/RAS by
affine. Internal canonical = RAS for medical, Z-up for ScanNet-style indoor
scenes. open_scene must detect and report `axis_up`. Mismatch here is the #1
silent bug.

GATE: tests green; opens an M3D-RefSeg qid and a ScanQA qid; reports
non-empty masks[] for the former; close releases GPU mem.

Report at end: "phase=3.1 tools=3 coverage=P% gpu_freed_mb=N"
```

---

## 10. Orchestration cheatsheet

After each phase completes, run the gate verification (a single agent or human):

```bash
# Phase gate verification (run from repo root)
uv run pytest tests/mcp/ -x --tb=short
uv run sciencevision-mcp --list-tools | jq '.tools | length'
# Phase 1: expect 28
# Phase 2: expect 38
# Phase 3: expect 53
```

If the gate fails, fix in the owning module before launching the next phase.
Phases are hard barriers — do not start Phase 2 until Phase 1 is green, etc.
The DAG exists to prevent agent A1's not-yet-stable manifest reader from
poisoning agent D's render fixtures.
