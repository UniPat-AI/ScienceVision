# QA-gen pipeline — architecture and extension points

> Partner-facing overview of the QA-gen pipeline. The Python package
> (`sciencevision_taskgen`) is mirrored into this repo at
> [`QA-Gen/ScienceVisionTaskGenerator/`](../../QA-Gen/ScienceVisionTaskGenerator/);
> this doc fixes the vocabulary and contracts so that the package, its
> templates, and the `QA-Gen/tasks/<batch>/` artifacts are interpretable
> together, and so that partner contributions land cleanly.

---

## 1. What QA-gen is, and what it is not

QA-gen is the **build-time** pipeline that turns the 3D-grounded asset
catalog (`viewer/assets.json` + `datasets/_matched/index.json`) into a
structured task batch under `QA-Gen/tasks/<batch>/`. Each task has:

- a natural-language question tied to a specific 3D asset,
- a typed `answer_schema` + `terminal_answer` contract,
- an evaluation policy (`required_evidence`, `allowed_tools`,
  `forbidden_tools`),
- a scorer reference (`scorer_primary`).

QA-gen is **not** a runtime evaluator. It produces deterministic JSONL
task lists; scoring happens later via the benchmark scorers
([`../benchmark/evaluation-protocol.md`](../benchmark/evaluation-protocol.md))
and the Harbor verify runner (issue #18, when mirrored).

The pipeline is **deterministic by default**. The optional LLM layer
(v2) augments two stages — Builder and Challenger — for richer wording
and semantic critique. With `llm.enabled = false` (the default), v2
produces byte-identical output to v1; cost is $0 and the run reproduces
from a seed plan.

---

## 2. Pipeline stages

```
                  ┌──────────┐
                  │ Catalog  │  enumerate assets per domain
                  └────┬─────┘    (viewer/assets.json + datasets/_matched/index.json)
                       ↓
                  ┌──────────┐
                  │ Planner  │  expand (domain × v_class × replica) cells
                  └────┬─────┘    from config.target_domains × config.v_classes ×
                                  config.tasks_per_cell
                       ↓
              ┌─────────────────┐
              │     Builder     │  for each cell: TEMPLATES[domain][v_class]
              │  (+ optional    │    + chosen asset → seed task dict
              │   LLM augment)  │  optional LLM: rewrite instruction +
              └────────┬────────┘    scientific_action against the actual asset
                       ↓
                ┌──────────┐    ↑   accept / repair / reject decisions, up to
                │ Refiner  ├────┤   validation.max_validation_iterations rounds
                └────┬─────┘    ↓
                     ↓    ┌───────────────┐
                     └───▶│  Challenger   │  rule-based critique (V1..V6 contract,
                          │  (+ optional  │    terminal_answer rules, asset/scorer
                          │   LLM         │    checks). Optional LLM semantic critique
                          │   semantic)   │    (cross-model when builder used a
                          └───────────────┘    different model).
                       ↓
                  ┌──────────┐
                  │  Export  │  final_tasks.jsonl + REPORT.md +
                  └──────────┘    GENERATION_STATS.md + decisions.json +
                                  critiques.json + (llm_usage.jsonl if LLM)
```

### Invariants

1. `llm.enabled = false` → byte-identical to deterministic v1.
   Test-guarded.
2. The LLM boundary never raises: HTTP / parse / validation failure
   falls back to template/rule output and records `fallback=true` in
   `llm_usage.jsonl`.
3. All stage state is immutable. `task`, `asset`, `plan`, and critique
   dicts are never mutated in place; updates build new dicts.

---

## 3. Cell iteration — what the planner does

The planner expands a config into a list of cells:

```python
# sciencevision_taskgen/pipeline/planner_stage.py (sketch)
for domain in (config.target_domains or CANONICAL_DOMAINS):
    if domain not in TEMPLATES:            # → status="pending_adapter"
        continue
    for v_class in (config.v_classes or V_CLASSES):
        if v_class not in TEMPLATES[domain]:    # → status="skipped"
            continue
        for replica in range(config.tasks_per_cell or 1):
            plan.append({"domain": domain, "v_class": v_class,
                         "replica": replica, "status": "ready"})
```

Where:

- `CANONICAL_DOMAINS` = `biology, medical, industrial, robotics,
  pointcloud, topological_photonics, astronomy`
- `EXTENSION_DOMAINS` = `photonics, simulation` (included only when
  `include_extension_domains=true`)
- `V_CLASSES` = `V1..V6`

A `skipped` or `pending_adapter` cell still appears in `decisions.json`
with a reason — partners can see what was not generated and why,
without grepping logs.

### K-axis extension

The K-axis (knowledge-grounded) family extends the cell key to
`(domain, v_class, k_class)` via a sibling `K_TEMPLATES[domain]`
registry. Pure-K cells set `v_class = null`; combined V×K cells merge
both templates' `required_evidence` and `forbidden_tools`. See
[`medical_k_axis_seeds.md`](medical_k_axis_seeds.md) §5 for the
integration sketch. Today, K-axis batches (e.g. `MedicalKAxisPilot/`)
are produced by per-batch `build_records.py` scripts that bypass the
planner; the `(domain, v_class, k_class)` planner extension is not yet
implemented in the mirrored package.

---

## 4. The `TEMPLATES` contract

`TEMPLATES: dict[str, dict[str, Template]]` is the source-of-truth for
every cell the planner can fill.

```python
@dataclass(frozen=True)
class Template:
    title: str
    instruction: str
    scientific_action: str
    answer_kind: str                      # e.g. "camera_pose", "topology_count"
    answer_fields: tuple[str, ...]        # e.g. ("azimuth","elevation","roll","zoom")
    required_evidence: tuple[str, ...]    # e.g. ("operation_trace","screenshot",
                                          #       "camera_state")
    scorer_primary: str                   # e.g. "v1_camera_delta"
    scorer_checks: tuple[str, ...]        # e.g. ("camera_delta_deg",
                                          #       "target_visibility")
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    ground_truth_type: str
    difficulty: str                       # "easy" | "med" | "hard"
    irreducibility_reason: str            # why this can't be shortcut by
                                          # code/metadata lookup
    preferred_assets: tuple[str, ...] = ()
    terminal_answer_kind: str = "categorical"
    terminal_answer_fields: tuple[str, ...] = ()
```

`NOT_ALLOWED_AS_ANSWER = ("camera_pose_only", "trace_only",
"metadata_lookup")` is enforced at the Challenger stage — a task whose
terminal answer reduces to any of these is rejected.

---

## 5. Output schema — `final_tasks.jsonl`

One JSON object per accepted task. Top-level keys:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | currently `"1.0"` |
| `task_id` | string | stable id, e.g. `sv-medical-k2-0004` |
| `domain` | string | matches a key in `TEMPLATES` (or `K_TEMPLATES`) |
| `v_class` | string \| null | one of `V1..V6`, or `null` for pure-K |
| `k_class` | string \| null | e.g. `K2`/`K3`/`K5` for medical; `null` otherwise |
| `scientific_action` | string | what the agent must do, in domain terms |
| `title` | string | one-line human-readable title |
| `instruction` | string | the question the agent sees |
| `difficulty` | string | `easy` / `med` / `hard` |
| `asset` | object | `{asset_id, format, viewer, path, repo_path, mask_path?, ...}` |
| `asset_grounding` | object | `{verified_entities, missing_or_ambiguous_entities, status, notes}` |
| `answer_schema` | object | `{kind, fields, enums?}` — answer shape |
| `terminal_answer` | object | record format + `must_be_derived_from_3d_interaction` flag + `not_allowed_as_answer` list |
| `tool_policy` | object | `{allowed_tools, forbidden_tools}` |
| `required_evidence` | array | screenshots / camera state / annotations the trace must include |
| `scorer_primary` | string | scorer family id (used by Harbor + per-dataset scorers) |
| `scorer_checks` | array | subordinate checks |
| `ground_truth` | object | `{status, value?}` — see §6 |
| `provenance` | object | builder template id; optional `llm_meta = {model, fallback, reason?}` |

Worked examples:
`QA-Gen/tasks/MedicalKAxisPilot/ScienceVision_MedicalKAxisPilot_final_tasks.jsonl`
(4 records, K-axis on M3D-RefSeg; `auto_seed`) and
`QA-Gen/tasks/Genesis_VP_Hard/ScienceVision_Genesis_VP_Hard_final_tasks.jsonl`
(2 records, embodied MPM physics; `needs_rollout`).

---

## 6. The `ground_truth.status` state

The status is an enum that records how much of the ground-truth a
record has on day one.

| Status | Meaning |
|---|---|
| `auto_seed` | Builder produced a candidate value deterministically (e.g. derived from `mask_text` or catalog facts). Expert sign-off still required before scoring. |
| `awaiting_expert` | Flagged for human review; review is queued but not yet performed. |
| `needs_expert_annotation` | No seed could be produced. Expert must annotate from scratch before any accuracy can be scored. |
| `needs_rollout` | Domain-specific (embodied/simulation): the value is a deterministic function of a solver rollout defined in `ground_truth.repro`. No value can be seeded by inspection; running the solver produces it, then an expert confirms. Used by `Genesis_VP_Hard/`. |

Promotion: any of the above → **`release_gate = pass`** once an expert
confirms the value. Until then, records can still flow through the
Harbor verify route (process-only rubric, no accuracy); see issue #18.
The expert annotation workflow is tracked in issue #19.

A batch can mix statuses freely. MedicalKAxisPilot (4 tasks) ships 4/4
as `auto_seed`; Genesis_VP_Hard (2 tasks) ships 2/2 as `needs_rollout`.

---

## 7. Extension points

### 7.1 Add a new domain

1. Drop the assets under `crawl_data/<domain>/<L2-category>/` (see
   [`../platform/new-domain-template.md`](../platform/new-domain-template.md) §1).
2. Add a builder in `scripts/match/build_matched_3d_qa.py`
   (`iter_<domain>(...)`) and register the dataset in
   `_MATCHED_DATASETS`.
3. Add `TEMPLATES["<domain>"] = {...}` covering the V-classes you want
   to support. A domain without templates appears in the plan as
   `status="pending_adapter"` — its assets count but no tasks are
   emitted.
4. Optionally narrow which assets a template draws from with
   `preferred_assets = (...)`.

### 7.2 Add a new V-axis or K-axis class

- **New V-class** — extend `V_CLASSES` and add the template to every
  domain that should support it.
- **New K-class** — add `K_TEMPLATES["<domain>"]["<K_class>"] = ...`.
  Pure-K runs set `v_class=null`; see
  [`medical_k_axis_seeds.md`](medical_k_axis_seeds.md) §5 for the
  planner extension that wires `(domain, v_class, k_class)`.

### 7.3 Add a new scorer family

1. Implement the scorer; expose its id as `scorer_primary` (e.g.
   `"k2_wall_vs_lumen"`).
2. Templates referencing the new id immediately route through it at
   scoring time.
3. Until the scorer lands, tasks that reference it ship with
   `ground_truth.status = "auto_seed"` and stay
   `release_gate = blocked` — the pipeline remains runnable end-to-end.

### 7.4 Add LLM augmentation to an existing template

The LLM layer is opt-in via config; the template itself does not
change. Enable for a run with:

```json
"llm": {
  "enabled": true,
  "default_model": "claude-sonnet-4-6",
  "model_by_v_class": {
    "V1": "claude-sonnet-4-6", "V2": "claude-sonnet-4-6",
    "V3": "claude-opus-4-7",   "V4": "claude-opus-4-7",
    "V5": "gemini-3.1-pro-preview", "V6": "gemini-3.1-pro-preview"
  },
  "challenger_cross_model": true,
  "temperature": 0.2,
  "timeout_seconds": 60
}
```

Every LLM call is recorded in `<run>/llm_usage.jsonl` with `stage`,
`task_id`, `v_class`, `model`, token counts, cost, latency, and a
`fallback` flag. Cost is computed per call from the per-model rates in
the config.

---

## 8. Running a batch

The `sciencevision_taskgen` package is mirrored into this repo at
`QA-Gen/ScienceVisionTaskGenerator/`. Run a batch with:

```bash
cd QA-Gen/ScienceVisionTaskGenerator
./scripts/run_pipeline.sh --config configs/walking_skeleton.json
# equivalently:
PYTHONPATH="$(pwd):${PYTHONPATH}" \
  python -m sciencevision_taskgen.runner --config configs/walking_skeleton.json

# LLM-enabled (v2):
./scripts/run_pipeline.sh --config configs/walking_skeleton_llm.json
```

Some `QA-Gen/tasks/<batch>/` batches additionally ship a standalone
`build_records.py` that recreates that specific batch deterministically
without invoking the full pipeline (e.g.
`QA-Gen/tasks/MedicalKAxisPilot/build_records.py`,
`QA-Gen/tasks/Genesis_VP_Hard/build_records.py`) — handy for K-axis /
domain batches that predate a planner adapter.

Output lands in `data/runs/run_<timestamp>/`:

- `final_tasks.jsonl` — accepted tasks
- `decisions.json` — per-cell `accept / repair / reject` log
- `critiques.json` — Challenger findings per task
- `REPORT.md` — coverage by `domain × v_class`
- `GENERATION_STATS.md` — per-stage × per-model breakdown (cost,
  tokens, fallback rate)
- `llm_usage.jsonl` — present only when `llm.enabled=true`; one record
  per LLM call

Once a batch is reviewed, the directory is published under
`QA-Gen/tasks/<batch>/` and registered in `viewer/qagen_index.json`
(see [`../platform/qa-to-viewer-loop.md`](../platform/qa-to-viewer-loop.md)
for the registration step and viewer rendering contract).

---

## 9. Where to dig deeper

| Topic | File |
|---|---|
| Domain-specific design example (K-axis) | [`medical_k_axis_seeds.md`](medical_k_axis_seeds.md) |
| Evaluation protocol (4-tier judge defense) | [`../benchmark/evaluation-protocol.md`](../benchmark/evaluation-protocol.md) |
| Submission format | [`../benchmark/submission-guide.md`](../benchmark/submission-guide.md) |
| New domain checklist | [`../platform/new-domain-template.md`](../platform/new-domain-template.md) |
| MCP tool surface used by tasks | [`../Tools.md`](../Tools.md) |
| QA → viewer reload loop | [`../platform/qa-to-viewer-loop.md`](../platform/qa-to-viewer-loop.md) |
| Harbor batch-verify route | issue [#18](https://github.com/UniPat-AI/BabyVision-v2/issues/18) (mirror + doc pending) |
| Expert annotation workflow | issue [#19](https://github.com/UniPat-AI/BabyVision-v2/issues/19) (doc pending) |
| New MCP tool lifecycle | issue [#15](https://github.com/UniPat-AI/BabyVision-v2/issues/15) (doc pending) |
