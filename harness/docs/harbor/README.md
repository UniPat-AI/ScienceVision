# The eval flow — from a task bundle to scored routes

**Coordinates.** You are running (or reviewing) an evaluation with the Harbor
harness in this repo. You produce **per-route scores over one task set**, plus
the artifacts that make every number auditable. The run only counts as a result
when the pre-launch gates (§5) pass **and** every route scored the **same** task
set (§8).

This file is the entry point for `docs/harbor/`. The sibling runbooks go deep on
one stage each (§9); this one is the map: what the stages are, in which order
they run, and which failures are silent.

---

## 1. Vocabulary

| term | meaning |
|---|---|
| **route** (also "baseline", "arm") | one information-access configuration for the agent: `cc`, `ccmcp`, `cc2d-N`. Routes differ *only* in what the container may see — never in the scorer (§3). |
| **task tree** | the per-route on-disk task set a `harbor run` consumes: one directory per task holding `task.toml`, `instruction.md`, `environment/`, `tests/` (§4). |
| **gate** | a check that must pass *before* a launch. Every gate here exists because its failure mode is silent — it produces a plausible-looking run with wrong numbers. |
| **receipt** | a machine-readable record written by the harness: a route-health receipt (per task), a run receipt (model, images, concurrency), or an exclusion receipt (a task absent from a tree **for a written reason**). Receipts are what make denominators auditable. |
| **run root** | the deployment directory holding one evaluation's inputs and outputs: task bundle, trees, job dirs, receipts, collected scores. Run roots are site-side; the contracts below are repo-side. |

## 2. The flow at a glance

| # | stage | input | mechanism | output | gate before the next stage |
|---|---|---|---|---|---|
| 1 | build trees | QA records (one row per task) | `scripts/harbor/qa_gen_to_tasks.py` + `build_harbor_tasks.py` (`build_one`; per-route container from `_agent_envs.py`) | `tasks-<route>/` per route | — |
| 2 | prepare views (cc2d only) | raw assets | view protocol in `_multiview.py` (offline render) or the delivery bundle's pre-rendered aligned views | `environment/views/*.png` + render manifest | a view with no visible pixels fails the blank-view floor; tasks that cannot render carry an exclusion receipt (§8) |
| 3 | verify | trees | tree verifier over the §5 contract list | pass/fail naming offending tasks | `ALL CONTRACTS OK` |
| 4 | smoke | trees | host-side scoring path, **every task against its own ground truth** | pass/fail per task | `tasks that cannot reach 1.0 against their own ground truth: 0` |
| 5 | run | trees | `harbor run` per route × model (`run_end2end.py`, or a site staged runner for one-arm / one-wave runs) | job dirs + receipts | trials judged > 0; run receipts complete |
| 6 | collect | job dirs | `collect_qagen_results.py`, `aggregate_weighted.py` | per-trial records + leaderboard | per-route denominators identical (§8) |
| 7 | recheck / backfill | collected gaps | `completed_task_ids.py` (resume without re-paying for finished trials), `rescore_rerun.py` (offline re-score) | patched trees / re-collected table | re-run stages 3–4 on patched trees, then merge by task id |

Stages 3 and 4 are what separate an auditable number from a plausible one. Run
them on **every tree you are about to launch**, including rebuilt or patched ones.

## 3. Routes are information-access levels

| route | what the agent sees | container |
|---|---|---|
| `cc` | the raw 3D asset at `/data`, inspectable with Python 3D libraries | codex CLI + slim inspection deps, **no MCP** |
| `ccmcp` | the same raw asset **plus** the `sciencevision` MCP server | codex CLI + deps + MCP |
| `cc2d-N` | **N pre-rendered PNG views only** — no raw asset, no MCP, no 3D libraries | codex CLI only; a leaked 3D file would still be unreadable |

The route set is an ablation, not a leaderboard of models: it isolates *which
information access* buys accuracy. `BASELINES.md` defines the full six-way
version (`cc2d-1/3/6/12`); deployments pick a subset — current runs evaluate
`cc` / `ccmcp` / `cc2d-6` under the Codex CLI.

What must stay identical across routes: the question, the ground truth, the
scorer contract, and the tolerance band. The tree builder guarantees this by
assembling every route through the same `qa_record_to_task` + `build_one` path —
only the agent environment and the staged inputs differ.

## 4. Anatomy of a task tree

```
tasks-cc2d-6/<task_id>/
  instruction.md                  # the question, rewritten for this route's paths
  task.toml                       # everything the harness and the scorer need
  tests/score.py                  # scorer payload (also runnable in-container)
  tests/test.sh
  environment/Dockerfile          # FROM the route's agent image; COPY views|asset
  environment/views/              # cc2d: the N PNGs the agent sees
  environment/render_manifest.json
  environment/asset/...           # cc/ccmcp: the staged raw asset
```

The load-bearing fields in `task.toml`:

```toml
[metadata]
answer_schema = { fields = [...], kind = "..." }
scorer = { primary = "exact_match", tolerance = { ... } }
evaluation_eligible = true
ground_truth = { value = { ... } }        # the {value: ...} envelope is required

[agent]     timeout_sec = 900.0
[verifier]  timeout_sec = 900.0
[verifier.environment]
docker_image = "sci3d-scorer:..."          # the pin the verifier contract checks
```

One subtlety worth knowing before you touch images: the route image is named in
exactly one place — the generated `environment/Dockerfile`, templated from
`_agent_envs.py`. Whatever form you put there is what the run resolves. A
**namespace-less** name (`FROM sci3d-codex-2d:latest`) resolves against the
host's local image store, so on hosts that use that form, re-pointing the local
alias — not pushing a registry tag — is the step that takes effect.

## 5. Pre-launch gates

> [!IMPORTANT]
> Every contract below fails *silently* — the run completes, the number looks
> fine, and the score is wrong. The verifier exits non-zero and names offending
> tasks; never skip it to "save time".

The tree verifier enforces, per route:

1. `ground_truth` is wrapped as `{value: ...}` — else every trial scores 0.0.
2. `evaluation_eligible` is true — else the verifier short-circuits.
3. An asset (or view set) is staged under `environment/` — else "missing source asset".
4. The scorer tolerance is non-empty where the ground truth needs a band — else exact grading / `invalid_task_contract`.
5. The scorer image is the intended build — else a previous round's scoring rules grade this one.
6. The agent image is the intended build — else render fixes are absent from the container.
7. `ccmcp` trees declare a viewer family — else the MCP worker is ungated.

The smoke gate is the second half: feed **every tree task its own ground
truth** through the host-side scoring path (the same
`harbor_adapters.sci3d.verifier:Sci3dVerifier` the runs use, in-process — the
score never comes from the verifier container). A task that cannot score 1.0
against its own ground truth is a task no agent can pass, and the harness
reports nothing — just reward 0.0. The pass criterion is exact:
`0 tasks that cannot reach 1.0`.

## 6. Running an arm

What a runner must enforce, and why:

- **The evaluated model is a run declaration, never inferred.** The runner
  fails closed without an explicit model id and records the resolved non-secret
  runtime (model, auth mode) into `codex-runtime.json` under the job root.
- **Concurrency is capped by the host's docker address pool, not by taste.**
  Each running task creates one compose bridge network; an over-sized `-n`
  exhausts the pool and loses every trial at once (the scale-v1 incident is
  recorded in `V21_EV2_2MODEL_RUNBOOK.md`). Runners check for headroom before
  launching and refuse to start without it.
- **Receipts bind image *identity*, not the tag.** A run receipt records the
  `docker image inspect` id/digest of each image, because a tag can be
  re-pointed between two runs.
- **Credentials never touch a command line.** The runner reads the key from a
  `0600` file or the environment; receipts record presence, never values.
- **Timeouts are raised, not tightened.** Trees declare 900 s agent / 900 s
  verifier / 600 s build ceilings; runners apply multipliers to *raise* those
  ceilings so a slow trial finishes instead of being cut.

Registration is by import path; Harbor itself is not forked:

```bash
harbor run \
  --agent codex --model <model-id> \
  --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
  ...           # cc/ccmcp additionally delegate through
                # harbor_adapters.sci3d.route_codex_agent:RouteContractCodex,
                # which writes the per-task route-health receipt
```

## 7. Collecting

Collection walks the job directories and writes, per trial, a compact record
next to the originating task set (`runs/<task_id>.json`: status, answer,
ground truth, rewards, trajectory) plus a roll-up leaderboard. Two rules:

- **A set assembled in waves is collected by union, keyed by (task_id, route).**
  Backfills and rechecks add trials for a subset of tasks; when the same
  (task_id, route) has several finished trials, keep the newest and count the
  attempts. Never let a backfill silently change one route's denominator.
- **Degraded scores are not results.** If the judge model was unavailable at
  run time (the trial's score file says `degraded`), re-score offline with
  `rescore_rerun.py` rather than shipping the degraded number.

## 8. Parity discipline

These rules are what keep the three routes comparable; each one exists because
its absence produced a silently skewed table at least once.

1. **One task set, three routes.** A comparison is only valid over tasks every
   route scored. A task missing from a route needs an exclusion receipt with a
   code (e.g. a named view with no visible pixels; an asset that cannot
   render), and the written reason must be accepted by the verifier of every
   route that omits it — not just the one that hit the problem.
2. **Substitutions keep the information class.** When a task is replaced
   because it is broken (not because it is hard), the replacement must come
   from the same task family, and it must be run in **all** routes — replacing
   only the route that was missing changes the ablation's meaning.
3. **Rechecks rerun only what has no result** (`completed_task_ids.py` lists
   finished trials so re-launches don't re-pay), but a rechecked tree passes
   the same §5 gates as a fresh one.

**Freedom clause.** Subset and diagnostic runs are allowed — one route only,
or N tasks only — provided they are *labeled* as such when reported. They are
not leaderboard-comparable, and folding them into a full table without a label
is the one mistake these rules cannot detect.

## 9. Where it all lives, and what to read next

| stage | file |
|---|---|
| resolve + build + run + collect (one driver) | `scripts/harbor/run_end2end.py` |
| record → tree assembly, route environments | `scripts/harbor/qa_gen_to_tasks.py`, `build_harbor_tasks.py`, `_agent_envs.py` |
| Codex runtime preflight / argv | `scripts/harbor/codex_runtime.sh`, `_codex_runtime.py` |
| agent + verifier adapters | `harbor_adapters/sci3d/{agent,route_codex_agent,verifier}.py` |
| view protocol / chem view checks | `scripts/harbor/_multiview.py`, `_verify_chem_views.py` |
| collect / aggregate / plot | `scripts/harbor/collect_qagen_results.py`, `aggregate_weighted.py`, `plot_leaderboard.py` |
| resume / offline rescore | `scripts/harbor/completed_task_ids.py`, `rescore_rerun.py` |

Sibling docs, and when each is worth opening:

- `BASELINES.md` — the six-way ablation; read it before changing the route set.
- `PILOT_2D_RUNBOOK.md` — the 2D route end to end, including offline view
  generation; read it before building `cc2d` trees.
- `V21_EV2_2MODEL_RUNBOOK.md` — a complete worked matrix run; its concurrency
  and pinning sections are required reading before planning a multi-route wave.
- `ADAPTER_sci3d.md` / `VERIFY_sci3d.md` — how trials are driven and scored;
  read them before changing either adapter.
- `EV2_FAILURE_CASES.md` — catalog of observed failure shapes; read it when a
  run's numbers look wrong.
