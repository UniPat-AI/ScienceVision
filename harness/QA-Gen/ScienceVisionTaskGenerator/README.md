# ScienceVisionTaskGenerator

ScienceVision-specific task generation pipeline for strong 3D QA.

This is intentionally separate from `QA-Gen/TaskGenerator`. The old generator is built around web-app clusters and professional workflows. This generator is built around:

- local 3D asset catalogs from `viewer/assets.json`
- matched 3D-grounded QA inventory from `datasets/_matched/index.json`
- ScienceVision domains and V1-V6 capability classes
- viewer/MCP tool policies
- scorer contracts and evidence bundles

## Pipeline

```text
CatalogStage -> PlannerStage -> BuilderStage -> ChallengerStage -> RefinerStage -> ExportStage
```

The default implementation is deterministic and does not call an external LLM. This makes the first pass reproducible and safe to run locally. Later, an LLM builder/refiner can replace `BuilderStage` or `RefinerStage` without changing the task schema.

## Run

```bash
cd QA-Gen/ScienceVisionTaskGenerator
./scripts/run_pipeline.sh
```

Useful configs:

```bash
./scripts/run_pipeline.sh --config configs/walking_skeleton.json
./scripts/run_pipeline.sh --config configs/pilot_v1_v3_v4.json
./scripts/run_pipeline.sh --config configs/default_pipeline.json
```

Summarize generation statistics:

```bash
./scripts/summarize_generation_stats.py data/runs/<run_id>
./scripts/summarize_generation_stats.py ../../QA-Gen/tasks/WalkingSkeleton \
  --out-json ../../QA-Gen/tasks/WalkingSkeleton/generation_stats.json \
  --out-md ../../QA-Gen/tasks/WalkingSkeleton/GENERATION_STATS.md
```

The stats script reports generated/kept/filtered task counts and retention ratios.
It also scans run artifacts for common token/cost fields (`usage`,
`token_usage`, `input_tokens`, `output_tokens`, `cost_usd`, etc.). The current
deterministic generator does not call an LLM, so token and cost totals are `0`
unless a future LLM-backed stage records usage metadata.

Outputs are written to `data/runs/run_<timestamp>/`:

- `catalog_summary.json`
- `seed_plan.json`
- `candidate_tasks.json`
- `critiques.json`
- `decisions.json`
- `final_tasks.json`
- `final_tasks.jsonl`
- `REPORT.md`
- `generation_stats.json`
- `GENERATION_STATS.md`

Accepted JSONL is also copied to `data/final_tasks/`.

## Task Status

Generated tasks are seed specs. Most ground-truth values intentionally have:

```json
{"status": "needs_expert_annotation"}
```

Before benchmark release, those slots must be filled by rendered labels, expert camera/slice choices, screenshot annotations, or variant-agreement labels.

## Design Contract

Every generated task must include:

- `domain` and `v_class`
- a real asset binding with `path`, `viewer`, and `format`
- required evidence such as trace, screenshot, annotation, or variant screenshots
- answer schema
- ground-truth type and status
- scorer contract
- allowed and forbidden tools
- irreducible-3D quality gate

V6 tasks must declare forbidden analytic tools. V3 tasks must require screenshot and annotation evidence. V4 tasks must require variant screenshots and agreement scoring.
