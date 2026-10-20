# Sandbox: deterministic evaluation replay

This directory proves one thing: a machine with only the repository, a dataset
package and the pinned scorer image can reproduce the **recorded scores** of an
archived run, question by question.

It does not call a model. The archived trials already contain each agent's final
message, so the sandbox re-runs the scoring stage and compares the result with
`verifier/sci3d_scores.json`. A mismatch therefore means a scorer, contract or
extraction difference, not sampling noise.

## Inputs

| Input | What it must contain |
|---|---|
| Repository | this checkout, installed with `pip install -e ".[harbor]"` (Python 3.12) |
| Dataset | a `v13-250` package: `<domain>/<task_id>/assets/...` and `normalized.json` |
| Contracts | one JSONL record per task, from `sandbox/export_contracts.py` |
| Archived jobs | Harbor job directories holding `*/<task>/agent/trajectory.json` and `verifier/sci3d_scores.json` |
| Scorer | a digest-pinned image, e.g. `ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b...` |

`CONTRACTS` is the missing launch layer: the dataset carries the question,
assets and gold, while a Harbor tree additionally needs asset roles, the answer
schema and the scorer configuration. `export_contracts.py` reads those from a
prepared tree; `build_trees.py` joins them back to the dataset.

## Run

```bash
export WORK=/nvme1n1p3/$USER/sv-sandbox-$(date +%Y%m%d)
export DATASET=/path/to/v13-250
export CONTRACTS=/path/to/contracts.jsonl
export SCORER_IMAGE=ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3

./sandbox/run_from_scratch.sh \
  /path/to/run/jobs/job-gpt-6.1-sol-cc-v11 \
  /path/to/run/jobs/job-deepseek-v4-flash-cc-r1
```

The script creates a virtual environment, installs the harness, builds the
`cc` and `ccmcp` trees for both harnesses, pulls the scorer, and writes
`$WORK/replay-summary.json` plus `$WORK/replay-trials.jsonl`.

To export contracts from prepared trees:

```bash
python -m sandbox.export_contracts \
  --tree-root /path/to/v11-tree/tasks-cc \
  --tree-root /path/to/v12-tree/tasks-cc \
  --tree-root /path/to/v13-tree/tasks-cc \
  --task-ids /path/to/v13_250_task_ids.txt \
  --output contracts.jsonl
```

## Measured result

Measured on 2026-10-20 on a fresh H20 with the scoped archives, filtered to the
v13-250 task set:

| Arm | Exact trials | Mean delta |
|---|---|---:|
| Codex, `gpt-6.1-sol`, cc + ccmcp | 189/189 each | 0.0000 |
| Claude Code, `deepseek-v4-flash`, cc + ccmcp, 3 reps | 194-197/205 each | +0.015 to +0.025 |

The Claude Code difference is not distributed: 54 trials across the six jobs
come from 12 tasks. On those tasks the archived run recorded
`invalid_task_contract` (a missing tolerance) and scored 0; the current scorer
derives the tolerance and grades the answer. The task trees themselves are
intact: all 1,998 replayed trials carry a `task_checksum` that matches the
current tree directory hash.

**The old leaderboard is therefore not exactly reproducible.** The archived
runs used a local scorer tag, `sci3d-scorer:harbor-0.20-v1-sv20260930` (image id
`e7295...`), which is no longer present on either H20; the published digest
`ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b...` is a different build,
and the run-time source in the v11 `tree-builder` is a third. To make the
release reproducible, pick one digest-pinned scorer, rescore the archived
answers once, and publish that per-trial reference together with the scorer
manifest hash. Future replays then have an exact target.

## Acceptance

A trial passes when the scorer returns `status = scored` and its `score.value`
equals the archived `answer_score.value` (the comparison uses
`math.isclose(..., rel_tol=1e-9, abs_tol=1e-9)`). The command exits `0` only
when every replayed trial passes.

Report coverage with the result. The archived runs are a splice, and the
accessible archives do not cover all 250 tasks for every arm:

| Arm | Accessible archived segment |
|---|---|
| Codex, `gpt-6.1-sol` | 205 v11-native tasks, one rep per route |
| Claude Code, `deepseek-v4-flash` / `glm-5.3-flash` | 205 v11-native tasks, three reps per route |
| Either harness, 32 v12 swap-ins | GPT: claude-code archive; deepseek/glm: scores only |
| Either harness, 13 v13 swap-ins | GPT: archived separately; deepseek/glm: scores only |

The 45 swap-ins are not a harness failure. GPT's published row already crosses
harnesses on those tasks, and the deepseek/glm job directories for them live on
hosts that were not reachable when this sandbox was built.

## What the sandbox deliberately does not do

- It does not re-run the agent. Model calls are stochastic; a re-run cannot
  reproduce a per-question score by construction.
- It does not use `index.jsonl` or `normalized.json` as launch input. They do
  not carry asset roles, tolerances or eligibility.
- It does not silently absorb missing tasks. `export_contracts.py` reports
  missing IDs, and `replay.py` reports every mismatched trial.

## Freedom and exceptions

You may point `--dataset-root` at either the pinned HF snapshot or a newer
local package. With a newer package, pass `--instruction-source package
--gold-source package` to `build_trees.py`: the 39 tasks repaired after the
stored runs will then grade against the repaired contract and are expected to
differ from the archived score. That difference is the repair, not a replay
failure.
