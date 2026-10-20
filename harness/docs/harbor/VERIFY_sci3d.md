# Harbor verify adapter for sci3d (`Sci3dVerifier`)

Wraps the sci3d scorers as a Harbor `BaseVerifier`. Formal evaluation is
model-free: strict JSON is compared with structured GT through binary rubric
items, then aggregated into `answer_score`. Companion to the agent adapter
([ADAPTER_sci3d.md](ADAPTER_sci3d.md)).

Host-run route: no Docker. The verifier runs in-process, reads the artifacts
the agent already wrote, and never touches the container. Registration is via
import-path; no Harbor source is edited.

## Register

```bash
.venv-harbor/bin/harbor run \
    --agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent \
    --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
    --model anthropic/claude-haiku-4-5-20251001 \
    --dataset <sci3d-tasks> ...
```

Verifier kwargs (via `--verifier-kwarg key=value`):

| kwarg         | default                        | meaning                          |
|---------------|--------------------------------|----------------------------------|
| `judge_model` | `claude-haiku-4-5-20251001`    | model used only by explicit shadow judges |

> Requires the py3.12 `.venv-harbor` (Harbor needs Python >= 3.12). Formal
> scoring is offline and needs no API key. The `harbor` extra and scorer image
> both install Gemmi for space-group canonicalization.

## What it scores — the `rewards` dict

`verify()` returns one `VerifierResult(rewards=...)` carrying three kinds of
keys, all in `[0,1]`:

| key                       | type        | source                                            |
|---------------------------|-------------|---------------------------------------------------|
| `answer_score`            | continuous  | weighted mean of deterministic binary rubric items |
| `shadow_llm_judge`        | continuous  | optional non-authoritative answer diagnostic       |
| `shadow_process_judge`    | continuous  | optional non-authoritative process diagnostic      |
| `process_opened_scene`    | 0 / 1       | tool sequence: opened a 3D scene                  |
| `process_rendered`        | 0 / 1       | tool sequence: rendered                           |
| `process_tool_policy`     | 0 / 1       | no forbidden / out-of-allowlist calls             |
| `process_finished`        | 0 / 1       | called `finish`                                   |
| `process_metadata_only`   | 0 / 1       | shortcut probe (metadata-only, no scene/render)   |
| `process_compliance`      | 0 / 1       | deterministic tool-policy diagnostic             |
| `reward`                  | continuous  | exactly equal to `answer_score`                  |

Harbor's metric layer aggregates each key across tasks (mean by default). The
verifier computes each task's frozen rubric aggregation before Harbor sees it.

## Formal reward

```
item_i       = comparator(pred_path_i, gt_path_i) in {0, 1}
answer_score = sum(weight_i * item_i) / sum(weight_i)
reward       = answer_score
```

Legacy content/process weights in old task metadata are ignored. Generated
tasks freeze the formal weighting as:

```toml
[metadata]
weights = { answer = 1.0, process = 0.0 }
```

Process signals remain visible for audit but cannot change leaderboard rank.
Invalid JSON, missing required fields, non-finite numbers, invalid task
contracts, and missing GT fail deterministically without an LLM fallback.

## Optional shadow judges

Tasks may explicitly request `shadow_llm_judge` or `shadow_process_judge` for
disagreement analysis. Domain rubric text lives in
`harbor_adapters/sci3d/rubrics/<domain>.md`. These calls are never appended
implicitly, and their values are namespaced under `shadow_*`.

To add/adjust a domain rubric: edit/add `rubrics/<domain>.md`. No code change —
`load_rubric(domain)` picks it up and `DomainJudge` appends it under a
`DOMAIN RUBRIC (domain=…)` header.

## Three Harbor config layers

Generated `task.toml` fills all three layers explicitly:

```toml
[agent]
timeout_sec = 900.0
[verifier]
timeout_sec = 900.0
network_mode = "no-network"
[environment]
build_timeout_sec = 600.0    # no docker_image -> host route
```

The generator switches the verifier to `network_mode = "public"` only when the
task explicitly declares `shadow_llm_judge` or `shadow_process_judge`. The
formal path makes no network call.

## Generating tasks

```bash
.venv-harbor/bin/python -m scripts.harbor.build_harbor_tasks \
    --tasks scripts/eval/walkingskeleton/verify_tasks.json \
    --out scripts/harbor/tasks [--limit N]
```

Produces `scripts/harbor/tasks/<task_id>/` with `task.toml` (three layers +
`[metadata]`), `instruction.md`, and a placeholder `tests/test.sh`. Package
names are `sci3d/<task_id>` (Harbor requires `org/name`). The sci3d fields
(asset, scorer, answer_schema, tool policy, ground_truth, weights) live in
`[metadata]`, which `Sci3dVerifier` reads via `task.config.metadata`.

## Data flow

```
agent run  ->  agent_dir/sci3d_answer.json     ({"answer": ...})
               agent_dir/sci3d_trajectory.json (Harbor ATIF; tool sequence)
                         │
Sci3dVerifier.verify():  ▼
  task.config.metadata ──► task_metadata_to_taskspec() ──► sci3d TaskSpec
  sci3d_answer.json    ──► answer_json_to_trajview()   ──► _TrajView(.final_answer,.tool_names)
  build_scorers(primary, secondary, domain) ─► DeterministicAnswerScorer
     binary rubric items ─► rewards[answer_score]
  compute_process_rewards(tool_names, allowed, forbidden) ─► rewards[process_*]
  rewards["reward"] = rewards["answer_score"]
  ─► VerifierResult(rewards) + audit verifier_dir/sci3d_scores.json
```

## Files

| file                                            | role                                              |
|-------------------------------------------------|---------------------------------------------------|
| `harbor_adapters/sci3d/verifier.py`             | `Sci3dVerifier(BaseVerifier)` (host route)        |
| `harbor_adapters/sci3d/_score_core.py`          | **shared** `score_rewards()` — both routes use it |
| `harbor_adapters/sci3d/_verify_translate.py`    | metadata→TaskSpec, TrajView, process + weighting  |
| `harbor_adapters/sci3d/_scorer_registry.py`     | scorer instantiation + `DomainJudge` rubric inject|
| `harbor_adapters/sci3d/rubrics/*.md`            | per-domain judge rubrics                          |
| `scripts/harbor/build_harbor_tasks.py`          | verify_tasks.json → Harbor task dirs (`--containerized`) |
| `scripts/harbor/templates/{Dockerfile,test.sh,score.py,build_image.sh}` | containerized route templates |
| `tests/harbor_adapters/test_sci3d_verifier.py`  | unit + end-to-end (fake LLM, no net/GL/SAM)       |
| `tests/harbor_adapters/test_containerized_route.py` | container layout + host==container anti-drift |

## Relationship to existing verify frameworks

- `DeterministicAnswerScorer` is the formal scorer shared by both Harbor routes.
- The `scripts/eval/walkingskeleton/run_verify.py` process metrics are
  approximated here from the tool-name sequence (no image-disk probe), so the
  host-unit path needs no GL.

## Containerized route (done)

Same scoring, run inside a container instead of in-process — for hosts that
can't import the scorers, or when the LLM-judge needs in-container network.

**Zero duplication is the design invariant.** Both routes call the *same*
`harbor_adapters.sci3d._score_core.score_rewards(meta, task_id, instruction,
agent_dir)`. The host `Sci3dVerifier.verify()` is now a thin wrapper around it;
the container's `tests/score.py` is another thin wrapper. The host==container
equality is pinned by `test_host_and_container_rewards_identical` so the two
paths can never drift.

Generate the containerized task set:

```bash
.venv-harbor/bin/python -m scripts.harbor.build_harbor_tasks \
    --out scripts/harbor/tasks-containerized --containerized
```

Each task then carries a **separate verifier environment** in `task.toml`:

```toml
[verifier]
environment_mode = "separate"
[verifier.environment]
docker_image = "sci3d-scorer:latest"
network_mode  = "no-network"      # public only for explicit shadow judges
```

and a `tests/` with `test.sh` (inlines the task's `[metadata]` as JSON +
instruction via here-doc, then runs `score.py`) and `score.py` (reads
`/logs/agent/sci3d_answer.json` + trajectory, calls `score_rewards`, writes the
multi-key `/logs/verifier/reward.json` + `sci3d_scores.json`).

**Build context caveat.** Harbor pins each task's per-task Docker build context
to that task's `environment/` dir, which cannot `COPY` the repo. So the route
uses **one shared image** built from the repo root, referenced by name — not a
per-task Dockerfile:

```bash
bash scripts/harbor/templates/build_image.sh   # -> sci3d-scorer:latest
```

The image is lightweight: only the scorer's runtime deps (`numpy`,
`scikit-image`, `nibabel`, `anthropic`) + this repo installed `--no-deps`. The
GL/SAM rendering stack is the *agent's* job and is deliberately excluded.

## Not done (follow-up)

- **Multi-step `[[steps]]`** (`multi_step_reward_strategy = mean|final`) for
  same-task / multi-agent / multi-stage scoring.
- **V1..V6 dedicated scorers** (`v1_camera_delta`, etc.) — declared in
  verify_tasks.json but not yet implemented; the universal judge is used and
  unknown scorer names are skipped.
- **Cross-task/domain weighting** — only per-task weighting is done; the metric
  layer stays Harbor-native mean.
