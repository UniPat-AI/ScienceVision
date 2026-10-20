# QA-Platform MVP — Design Spec

**Status**: Draft v0.1 · **Owner**: ScienceVision QA-Gen platform · **Audience**: viewer/platform engineering, eval team · **Date**: 2026-06-30 · **Related**: `2026-06-26-qa-gen-expert-annotation-spec.md` (internal), `2026-06-26-expert-onboarding.md` (handbook)

This is the design for the **thin-MVP做题平台 (QA-doing platform)** that wraps existing QA-Gen tasks so domain experts can solve them in-viewer and submit structured answers. It is the smallest slice that produces usable expert annotations; the full Phase-0 instrumentation (mouse trajectory, heatmap, replay) is deferred to a follow-up iteration.

---

## 1. Background & Scope

QA-Gen already produces 3D-grounded QA tasks (e.g. `QA-Gen/tasks/MedicalKAxisPilot/` with 4 final tasks; `tianyi3unipat/BabyVision-v2-Datasets` on ModelScope with a 2-task `medical/qa/medical_spatial.jsonl` demo pack). Viewers (`molstar.html`, `niivue.html`, `cornerstone.html`) ship inside `viewer/` and are deployed at `babyvision-v2.unipatai.com`.

What is missing is the **answering surface**: a place an expert can log in, pick a task, open the viewer, mark a few reasoning checkpoints, and submit a structured answer that lands on disk in a forward-compatible JSON shape.

**In scope (MVP)**

1. Task list aggregated from both local (`viewer/qagen_index.json`) and remote (ModelScope) sources.
2. Magic-link expert login with a **per-token login-count cap** that must be re-issued when exhausted.
3. Single-page answer surface: viewer iframe + checkpoint text capture + final-answer form (structured per task `answer_schema` + free-text + confidence + difficulty + optional asset-correction comment).
4. Submission JSON falls onto disk under `datasets/expert_annotations/<source_id>/<flat_task_id>__<expert_id>.json`, structurally aligned with the §4.2 trajectory schema from the internal spec (with a `task_snapshot` wrapper to separate immutable task definition from per-submission data) so trajectory fields can be added later without rewriting existing files.
5. Deployed alongside the existing viewer site under `/annot/*` (Caddy reverse-proxy to a FastAPI process).

**Out of scope (deferred to trajectory iteration)**

- Full mouse-event capture (`mouse_move` throttled 50 ms, `camera_change`, `slice_change`, `window_level_change`, `mask_toggle`, `view_mode_change`).
- IndexedDB local buffering of high-rate events; batch upload pipeline.
- Checkpoint screenshots (PNG capture + storage).
- Heatmap aggregation + rendering.
- Replay viewer tool.
- Inter-expert agreement (§6.3 of internal spec) and reviewer batch-QC tooling.
- Auto-scoring submissions against `ground_truth.value`.
- Multi-worker backend / distributed token state.

---

## 2. Architecture

### 2.1 Directory layout

New top-level directory `apps/annotation/`, peer to `viewer/`, `deploy/`, `QA-Gen/`:

```
apps/annotation/
├── backend/
│   ├── api.py                       FastAPI routes (composition only)
│   ├── tokens.py                    token registry; validate / consume / hot-reload
│   ├── sessions.py                  SQLite session table; issue / verify / expire
│   ├── catalog.py                   aggregate local + ModelScope sources → normalized tasks
│   ├── store.py                     submission writes; overwrite-with-.prev backup
│   ├── assets.py                    asset proxy + local cache for ModelScope blobs
│   ├── schemas/
│   │   └── submission-v0.1.json     jsonschema enforced at /submit
│   └── data/
│       └── (sessions.db lives here in dev; in prod under /var/lib)
├── frontend/
│   ├── index.html                   task list (magic-link landing)
│   ├── task.html                    answer surface (viewer iframe + form)
│   ├── js/                          modules: auth, catalog-render, task-page, postmessage-bridge
│   └── css/                         reuse viewer/shared/style.css
├── tools/
│   └── mint_token.py                CLI: issue token, append to tokens.json, print URL
├── tests/
│   ├── backend/                     pytest
│   └── e2e/                         playwright smoke
└── config/
    └── sources.example.json
```

**Module boundaries** (every backend module < 250 lines, single responsibility, independently testable):

| Module | Reads | Writes | Depends on |
|---|---|---|---|
| `tokens` | `BV2_ANNOT_TOKENS` JSON | same file (uses+=1) | stdlib |
| `sessions` | sqlite | sqlite | stdlib |
| `catalog` | `viewer/qagen_index.json`, ModelScope API | nothing | `modelscope` SDK |
| `assets` | local fs / ModelScope | asset cache dir | `modelscope` SDK |
| `store` | submissions dir | submissions dir | stdlib |
| `api` | all of the above | nothing direct | FastAPI |

### 2.2 Relationship to existing code

- `viewer/` HTML is **not modified** except for accepting a few query-string params (`?asset=`, `?mask=`, `?task_id=`) and emitting a minimal `postMessage` bridge (see §3.4). If `viewer/niivue.html` etc. already accept these, no change at all.
- `viewer/qagen_index.json` is read but not modified.
- `datasets/expert_annotations/` is a new directory, gitignored.
- `deploy/Caddyfile` gets one new `handle_path /annot/* { … }` block.

---

## 3. Components

### 3.1 Tokens & sessions

**Token file** (`BV2_ANNOT_TOKENS`, default `/var/lib/bv2-annot/tokens.json`, gitignored):

```json
{
  "tok_8a3f1c…": {
    "expert_id": "alice",
    "display_name": "Alice Chen",
    "domain_focus": ["medical"],
    "max_uses": 5,
    "uses": 0,
    "issued_at": "2026-06-30T12:00:00Z",
    "revoked": false
  }
}
```

- `max_uses` semantics: incremented on each successful `POST /api/login`, *not* on every API call. Page refresh inside an active session does **not** consume `uses`.
- When `uses >= max_uses` or `revoked: true`, `/api/login` returns `401 token_exhausted` (or `token_revoked`) with a message telling the expert to request a new link.
- Hot reload: backend re-reads the file every 30 s and on each `/api/login`; ops can edit / revoke / extend `max_uses` without restart.
- After incrementing `uses`, the backend writes the full file (small, < 10 KB) atomically (`os.replace`).
- Default `max_uses = 5`, overridable via `mint_token --max-uses N`.

**Sessions** (SQLite, single table):

| column | type | note |
|---|---|---|
| `session_id` | TEXT PK | 32-byte random hex |
| `token_id` | TEXT | the tokens.json key |
| `expert_id` | TEXT | redundant, avoids relookup |
| `created_at` | TIMESTAMP | |
| `expires_at` | TIMESTAMP | created_at + 24 h |
| `revoked` | INTEGER | 0/1 |

Session cookie: `HttpOnly`, `Secure` (prod), `SameSite=Lax`. Frontend never holds the token after the initial `/api/login`.

**CLI `tools/mint_token.py`**:

```
python -m apps.annotation.tools.mint_token \
    --name "Alice Chen" --expert-id alice --domain medical --max-uses 5
# → prints https://babyvision-v2.unipatai.com/annot/?t=tok_8a3f1c…
```

### 3.2 Task catalog

**Sources config** (`apps/annotation/config/sources.json`):

```json
{
  "sources": [
    {
      "type": "local",
      "id": "MedicalKAxisPilot",
      "index": "viewer/qagen_index.json"
    },
    {
      "type": "modelscope",
      "id": "MS-medical-demo",
      "dataset": "tianyi3unipat/BabyVision-v2-Datasets",
      "revision": "master",
      "qa_glob": "medical/qa/*.jsonl",
      "asset_root": "medical/assets/"
    }
  ]
}
```

**Two raw task shapes** the catalog must accept:

| Field | Local (`MedicalKAxisPilot`) | Remote (`medical_spatial`) |
|---|---|---|
| `task_id` | `sv-medical-k2-0004` | `medical_spatial/q001` |
| `domain` | `medical` | `medical` |
| `v_class` / `k_class` | `null` / `K2` | `null` / `null` |
| question | `instruction` field | `question` field |
| `answer_schema` | `{kind, fields, enums}` | `{kind}` |
| `asset` | `{path, viewer, repo_path, mask_repo_path, …}` | `"medical/assets/mask.nii.gz"` (string) |

**Normalized task view** returned by `GET /annot/api/tasks` and stored in `task_snapshot` on submission:

```json
{
  "task_id": "MS-medical-demo:medical_spatial/q001",
  "source_id": "MS-medical-demo",
  "domain": "medical",
  "subdomain": null,
  "v_class": null,
  "k_class": null,
  "question_text": "What is the total volume in mm^3 of the labelled region in the segmentation mask?",
  "answer_schema": { "kind": "numeric", "fields": null, "enums": null },
  "asset": {
    "viewer": "niivue",
    "primary_url": "/annot/api/asset/MS-medical-demo/medical/assets/mask.nii.gz",
    "mask_url": null,
    "format": "nifti"
  },
  "raw": { "...": "..." }
}
```

- `task_id` gains a `<source_id>:` prefix to avoid cross-source id collisions.
- `asset.viewer` is inferred when missing: `.nii.gz/.nii/.nrrd → niivue`, `.dcm → cornerstone`, `.pdb/.cif/.mol/.sdf/.mol2/.xyz → molstar`.
- `asset.primary_url` always points at the backend asset proxy — the browser never learns local disk paths or ModelScope URLs.
- `raw` preserves the original task object verbatim for audit / future migrations.

Catalog is loaded once at startup and refreshed on a 10-minute timer (also via an admin endpoint `POST /annot/api/_admin/reload` gated by a separate admin token).

### 3.3 Asset proxy

`GET /annot/api/asset/<source_id>/<repo_path...>`:

- **Local source**: `FileResponse` from `<repo_root>/<repo_path>`. Stream, do not buffer.
- **ModelScope source**: cache at `apps/annotation/cache/assets/<sha256(dataset|revision|repo_path)>/<basename>`. On miss, call `dataset_snapshot_download(dataset_id=…, revision=…, allow_patterns=[repo_path], cache_dir=…)` with the server-side `MODELSCOPE_API_TOKEN`, then serve.
- Response headers: `Cache-Control: public, max-age=86400`, weak `ETag` based on mtime. Volumes are 1–50 MB; browser + disk cache both effective.
- **Optional warm-up**: at boot, kick a background task that pre-fetches every ModelScope asset listed in the catalog. MVP can ship without this; first-time cold load is ~5 s on a fast link, acceptable.

### 3.4 Viewer iframe + postMessage bridge

Answer page embeds the right viewer by `asset.viewer`:

```html
<iframe src="/viewer/niivue.html?asset=/annot/api/asset/MS-medical-demo/medical/assets/image.nii.gz
                                 &mask=/annot/api/asset/MS-medical-demo/medical/assets/mask.nii.gz
                                 &task_id=MS-medical-demo:medical_spatial/q001"></iframe>
```

Minimal postMessage protocol (MVP):

| Direction | Message | When |
|---|---|---|
| iframe → parent | `{type:"viewer_ready"}` | viewer finished initial render; parent unlocks the answer form |
| parent → iframe | `{type:"snapshot_request"}` | optional, on "Mark key step"; viewer replies with `{type:"snapshot", viewer_state}` for future trajectory hook |

For MVP, the snapshot reply is **stored but not used** (the checkpoint only persists `reasoning_text`); the channel exists so the trajectory iteration can light it up without parent/iframe refactor.

### 3.5 Frontend answer surface (`task.html`)

Three-pane layout:

```
┌─────────────────────────────┬─────────────────────────────┐
│  viewer iframe              │  Question                   │
│                             │  Answer schema hint         │
│                             │  ─────────────────────────  │
│                             │  Checkpoints                │
│                             │    [+] Mark key step        │
│                             │    1. "Switched to coronal…"│
│                             │    2. "Volume looks ~1.3e6…"│
│                             │  ─────────────────────────  │
│                             │  Final answer               │
│                             │    structured form (per     │
│                             │      answer_schema.kind)    │
│                             │    free-text fallback       │
│                             │    confidence (1–5 slider)  │
│                             │    difficulty (1–5 slider)  │
│                             │    correction comment       │
│                             │  [ Submit ]                 │
└─────────────────────────────┴─────────────────────────────┘
```

Structured form rendering by `answer_schema.kind`:

| kind | UI |
|---|---|
| `numeric` / `integer` | number input + unit input (free) |
| `categorical` (`enums` present) | radio group / dropdown |
| `sub_location_plus_pathology` (Medical K-pilot) | one dropdown per `fields[i]` driven by `enums[fields[i]]` |
| `freeform` / unknown | textarea only |

Local browser state (sessionStorage, per task): in-progress checkpoints + draft answer, so accidental refresh doesn't lose work.

### 3.6 Submission shape (v0.1-mvp)

Disk path: `datasets/expert_annotations/<source_id>/<flat_task_id>__<expert_id>.json` (where `flat_task_id` replaces `/` with `__`). Re-submission renames the prior file to `*.prev-<submitted_at>.json` and overwrites the canonical name.

```json
{
  "schema_version": "0.1-mvp",
  "task_id": "MS-medical-demo:medical_spatial/q001",
  "source_id": "MS-medical-demo",
  "expert": {
    "expert_id": "alice",
    "session_id": "s_8f3a…"
  },
  "task_snapshot": {
    "domain": "medical",
    "v_class": null,
    "k_class": null,
    "question_text": "…",
    "answer_schema": { "kind": "numeric", "fields": null, "enums": null },
    "asset": {
      "viewer": "niivue",
      "primary": "medical/assets/image.nii.gz",
      "mask": "medical/assets/mask.nii.gz",
      "format": "nifti"
    }
  },
  "timing": {
    "started_at": "2026-06-30T10:00:00Z",
    "submitted_at": "2026-06-30T10:08:34Z",
    "active_ms": null
  },
  "checkpoints": [
    {
      "ordinal": 1,
      "client_ts": "2026-06-30T10:02:14Z",
      "reasoning_text": "Switched to coronal at slice 87; lateral ventricles look enlarged."
    }
  ],
  "answer": {
    "structured": { "value": 1295000, "unit": "mm^3" },
    "text": "Approximately 1.30 × 10^6 mm^3.",
    "confidence": 4,
    "difficulty_rating": 3,
    "asset_correction_comment": null
  },
  "client": {
    "ua": "…",
    "viewport": "1920x1080",
    "viewer_app_version": "bv2-viewer@<git-sha>"
  },
  "events": [],
  "derived": { "heatmap_uri": null, "n_checkpoints": 1, "n_camera_changes": 0, "n_slice_changes": 0 }
}
```

**Forward compatibility**: the trajectory iteration only needs to populate `events`, `derived.heatmap_uri` + remaining counters, `checkpoints[].viewer_state` + `screenshot_uri`, and `timing.active_ms`, then bump `schema_version` to `1.0`. Existing v0.1 files remain readable; consumers branch on `schema_version`. The `task_snapshot.asset.primary` / `task_snapshot.asset.mask` fields store **repo-relative paths** (not URLs) to stay stable across host / proxy changes; the asset URL is reconstructed at read time.

`backend/schemas/submission-v0.1.json` is the authoritative jsonschema and is enforced at `POST /submit` (422 on invalid). No additional properties allowed except inside `task_snapshot.raw` (if present) and `client`.

---

## 4. HTTP API

All endpoints are mounted under `/annot/api/`. Errors share this shape:

```json
{ "error": "token_exhausted", "message": "Your link has been used 5/5 times. Ask an admin to mint a new one.", "context": {} }
```

| Method | Path | Auth | Body / Query | 200 response | Errors |
|---|---|---|---|---|---|
| `POST` | `/login` | token (body or `?t=`) | `{token}` | sets cookie; `{expert_id, display_name, remaining_uses}` | `token_invalid`, `token_revoked`, `token_exhausted` |
| `POST` | `/logout` | session | — | `{ok: true}` | — |
| `GET`  | `/me` | session | — | `{expert_id, display_name, remaining_uses, session_expires_at}` | `session_expired` |
| `GET`  | `/tasks` | session | optional `?domain=&source_id=` | `{tasks: [...normalized], totals: {...}}` | — |
| `GET`  | `/tasks/{task_id}` | session | — | full normalized task | `task_not_found` |
| `POST` | `/submit` | session | full submission JSON | `{task_id, submitted_at, path}` | `submit_invalid` (422), `task_not_found` |
| `GET`  | `/my-submissions` | session | — | `{[task_id]: {submitted_at, path}}` | — |
| `GET`  | `/asset/{source_id}/{repo_path}` | session | — | binary, streaming | `asset_not_found`, `source_unknown` |
| `POST` | `/_admin/reload` | admin token (separate env var) | — | `{ok}` | `admin_token_invalid` |

Logging policy: query strings starting with `?t=` are scrubbed in access logs. Successful `/login` emits an audit line with `token_id` + `expert_id` + remaining uses (no token value).

---

## 5. Deployment

### 5.1 Process model

Single uvicorn worker on `127.0.0.1:7800` behind Caddy. Single worker is required because `tokens.uses` increments mutate a flat JSON file; a future move to multi-worker would need a small Redis or a switch to SQLite-backed tokens. Concurrency target for MVP is low (≤ a handful of experts), so single worker is fine.

### 5.2 Caddy changes (`deploy/Caddyfile`)

Add inside the existing site block:

```
handle_path /annot/* {
    reverse_proxy 127.0.0.1:7800
}
```

`handle_path` strips `/annot` from the upstream path so the FastAPI app sees `/api/...` and the static frontend sees `/index.html`, `/task.html`, etc.

### 5.3 systemd unit (`deploy/annotation.service`)

```
[Unit]
Description=BabyVision-v2 annotation backend
After=network.target

[Service]
Type=simple
User=bv2-annot
Group=bv2-annot
WorkingDirectory=/opt/babyvision-v2
EnvironmentFile=/etc/bv2-annot/annotation.env
ExecStart=/opt/babyvision-v2/.venv/bin/uvicorn apps.annotation.backend.api:app --host 127.0.0.1 --port 7800 --workers 1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`annotation.env`:

```
BV2_ANNOT_TOKENS=/var/lib/bv2-annot/tokens.json
BV2_ANNOT_SESSIONS_DB=/var/lib/bv2-annot/sessions.db
BV2_ANNOT_SUBMISSIONS_DIR=/var/lib/bv2-annot/expert_annotations
BV2_ANNOT_ASSET_CACHE=/var/lib/bv2-annot/asset-cache
BV2_ANNOT_ADMIN_TOKEN=<random>
MODELSCOPE_API_TOKEN=<copied from ops vault>
```

### 5.4 `deploy/install.sh` additions

Idempotent block that creates `/var/lib/bv2-annot/` (chmod 750, owned by `bv2-annot`), installs the systemd unit, drops the env file (with placeholders prompting the operator on first run), and runs `systemctl enable --now annotation`.

### 5.5 Dev / local mode

`make annot-dev` (or a script under `apps/annotation/`) runs:

```
uvicorn apps.annotation.backend.api:app --reload --port 7800 \
  --env-file apps/annotation/.env.dev
```

`.env.dev` points all paths under repo working tree (`datasets/expert_annotations/`, `apps/annotation/cache/assets/`, `apps/annotation/backend/data/sessions.db`). Caddy is not required in dev — the uvicorn process serves the static frontend directly via `StaticFiles`.

---

## 6. Verification

### 6.1 Automated

**pytest** (`apps/annotation/tests/backend/`):

- `test_tokens.py`: validate / consume / hot-reload / exhaust / revoke; concurrent `/login` calls don't double-credit one use.
- `test_catalog.py`: ingest both local fixture + a fake ModelScope-shaped jsonl; normalized output matches §3.2; viewer inference by extension.
- `test_assets.py`: local FileResponse path; ModelScope path uses a stubbed `dataset_snapshot_download` that returns a fixture file; cache hit on second request.
- `test_submit.py`: valid submission lands at expected path; jsonschema rejection on invalid; re-submission renames previous to `.prev-*.json`.
- `test_sessions.py`: cookie issuance, expiry, revoke.

**Playwright** (`apps/annotation/tests/e2e/`):

- `smoke.spec.ts`: mint a token via the CLI in a tmp dir, hit `/annot/?t=...`, list tasks, open `medical_spatial/q001`, wait for `viewer_ready`, click Mark Key Step + type a checkpoint, fill structured numeric answer, submit; assert the resulting JSON file is well-formed and matches the schema.

### 6.2 Manual dogfood (exit criterion)

1. Mint two tokens (`alice`, `bob`).
2. Each of us answers both ModelScope `medical_spatial` tasks and at least one local `MedicalKAxisPilot` task.
3. Inspect on-disk JSONs; confirm `task_snapshot`, `answer.structured`, `checkpoints`, and `timing` look right.
4. Verify a fresh browser refresh inside a session does **not** consume a token use, and a third login from a 5-use token after `uses=5` is rejected with `token_exhausted`.
5. Verify the `niivue` iframe actually renders the ModelScope mask volume via the asset proxy.

---

## 7. Open items / decisions log

Decisions reached during the 2026-06-30 brainstorm:

- **Scope**: thin MVP — task list + viewer + checkpoint text + structured answer. Trajectory deferred.
- **Backend**: lightweight FastAPI + JSON-on-disk for submissions; SQLite for sessions.
- **Auth**: magic-link with per-token `max_uses` cap (default 5), hot-reloadable tokens.json, re-issue via CLI when exhausted.
- **Task pool**: read `viewer/qagen_index.json` + extensible to additional sources (local + ModelScope).
- **Assignment**: all tasks public, any expert can answer any task (overlap is allowed; enables future inter-expert agreement metrics).
- **Schema**: simplified `v0.1-mvp`, forward-compatible with the full §4.2 trajectory schema (extra fields, not breaking changes).
- **Hosting**: same host as existing viewer, mounted under `/annot/*` via Caddy reverse-proxy.
- **ModelScope case**: `tianyi3unipat/BabyVision-v2-Datasets/master/medical` — 2 tasks (`medical_spatial/q001` numeric volume, `q002` integer count) on a single CT image + mask asset pair.
- **Asset access**: backend proxies + caches; ModelScope token never reaches the browser.
- **`task_id` prefixing**: `<source_id>:<original_task_id>` to prevent cross-source collisions.
- **Re-submission**: allowed; previous version preserved as `.prev-<ts>.json`.
- **Process model**: single uvicorn worker; multi-worker deferred until concurrency demands it.

Open items (none block plan-writing; they surface during implementation):

- Concrete asset list for biology / extra medical batches — backfilled per internal spec §3.2.
- Whether `confidence` and `difficulty_rating` defaults should be empty (forcing the expert to choose) or middle-value (3). Recommendation: leave empty, validate non-null at submit.
- Whether `display_name` belongs in the submission JSON (vs only `expert_id`). Current design: only `expert_id`; the name lives in tokens.json which is operator-side.
- Versioning policy when we bump to `schema_version: 1.0` — likely a small migration script that fills the new fields with `null`s on existing v0.1 records so downstream consumers can branch on presence rather than version.
