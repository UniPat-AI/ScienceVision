# scale-v2.1 six-route results — Claude Code half

Results for the matrix described in
[`V21_EV2_2MODEL_RUNBOOK.md`](V21_EV2_2MODEL_RUNBOOK.md). What you need installed
to re-aggregate, re-test, rebuild or re-run any of it — and which levels need
neither Harbor nor Docker: [`V21_EV2_2MODEL_ENV.md`](V21_EV2_2MODEL_ENV.md).
Reader-facing summary:
[`../benchmark/results-scale-v21.md`](../benchmark/results-scale-v21.md). Raw
aggregate: [`V21_EV2_2MODEL_LEADERBOARD.json`](V21_EV2_2MODEL_LEADERBOARD.json),
produced by [`../../scripts/harbor/report_v21_ev2.py`](../../scripts/harbor/report_v21_ev2.py).

**Status, as of 2026-08-14 09:00 CST: half the matrix.** The six Claude Code
routes are complete — 60/60 scored trials each, zero errored, zero missing. The
six Codex routes have **no trials at all**; see [Why the Codex half is
empty](#why-the-codex-half-is-empty). Nothing in this document compares the two
harnesses, because there is nothing on the Codex side to compare.

## What ran

| | |
|---|---|
| harness | Claude Code CLI in-container, `claude-opus-5`, `--effort xhigh` |
| routes | `cc`, `ccmcp`, `cc2d-1`, `cc2d-3`, `cc2d-6`, `cc2d-12` |
| scored tasks | 60 — scale-v2.1, 20 each biology / chemistry / medical |
| diagnostic tasks | 15 — expert-v2, structurally unscored |
| trials on disk | 360 scored + 90 diagnostic |
| harbor | 0.20.0 |
| images | `sci3d-claude-code{,-mcp,-2d}:v21ev2-20260813`, scorer `sci3d-scorer:harbor-0.20-v21ev2-20260813` |
| run root | `ScienceVision-evals/scale-v21-ev2-sixroute-2model-20260813` |

Frozen selection, seed `20260813`:

| set | n | `id_sha256` |
|---|--:|---|
| all | 75 | `6572ea8dc70470c8f498acd2ab78e8d47c6c8364e7ae34453905ef4824d664e6` |
| scale-v2.1 (scored) | 60 | `65dc939dff9df7ac3a4b27df3cb5ca20edc3621c64cc972c414db82f45327cd4` |
| expert-v2 (diagnostic) | 15 | `a83d7439de963eda658427ea1433072bda8c31c5848689c0889e0f670057bfca` |

## Leaderboard

`answer_score` over the frozen 60-task denominator. Process signals are
diagnostics and never enter it.

| route | answer_score | clean | abstained | errored | missing | biology | chemistry | medical |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| `cc` | 0.556 | 60/60 | 0 | 0 | 0 | 0.340 | 0.817 | 0.512 |
| `ccmcp` | 0.558 | 60/60 | 0 | 0 | 0 | 0.340 | 0.783 | 0.550 |
| `cc2d-1` | 0.183 | 60/60 | 20 | 0 | 0 | 0.260 | 0.283 | 0.006 |
| `cc2d-3` | 0.157 | 60/60 | 20 | 0 | 0 | 0.320 | 0.150 | 0.000 |
| `cc2d-6` | 0.213 | 60/60 | 13 | 0 | 0 | 0.390 | 0.250 | 0.000 |
| `cc2d-12` | 0.249 | 60/60 | 13 | 0 | 0 | 0.300 | 0.404 | 0.044 |

`abstained` counts trials that returned the answer template with every field
null. Those score 0 exactly like a wrong answer, but they are a different
behaviour — the agent declined instead of inventing a number — so a 0.000 cell
should be read with its abstention count beside it. Where the abstentions fall:

| route | biology | chemistry | medical | total |
|---|--:|--:|--:|--:|
| `cc2d-1` | 5 | 6 | 9 | 20 |
| `cc2d-3` | 1 | 10 | 9 | 20 |
| `cc2d-6` | 1 | 6 | 6 | 13 |
| `cc2d-12` | 1 | 6 | 6 | 13 |

`cc` and `ccmcp` abstained zero times in 120 trials.

## Reading the numbers

**Raw file versus raw file + MCP tools is a tie.** 0.556 vs 0.558 on n=60 is
not a separation, and the per-domain split moves in both directions
(chemistry 0.817 → 0.783, medical 0.512 → 0.550). The process signals say why
that is not yet a verdict on the tools: on `ccmcp` a scene was opened in 37% of
trials and a render call made in 17%. For most of the route's trials the tools
were available and unused, so this run measures "tools offered", not "tools
used".

**The image-only family lands far below both**, 0.157–0.249 against 0.556. More
views recover some of it at 6 and 12 (0.213, 0.249), but the ordering is not
monotone — `cc2d-3` scores below `cc2d-1` — so at n=60 the view count is not
cleanly separable from the sampling.

**Medical is where the image-only routes collapse**: 0.006, 0.000, 0.000, 0.044.
The 20 medical tasks ask for voxel indices and RAS+ millimetres; slice PNGs
carry no such coordinate frame, and the agent abstained on 6–9 of 20 rather
than guess. Read that as a task-format wall, not as a perception score.

**Chemistry is the strongest raw-file domain (0.817) and the most suspect.**
Its two strata are "measure an explicitly ordered four-atom dihedral" (16 of 20)
and "select two declared atom instances and measure their 3D distance" (4 of
20). Both are computable from the coordinate file by arithmetic, with nothing
perceived — which is precisely the open `package_compute_shortcut` finding
below.

## Process signals (diagnostic, never part of reward)

| route | opened_scene | rendered | tool_policy | finished | metadata_only | compliance |
|---|--:|--:|--:|--:|--:|--:|
| `cc` | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 |
| `ccmcp` | 0.37 | 0.17 | 0.35 | 0.00 | 0.00 | 0.35 |
| `cc2d-1` | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 |
| `cc2d-3` | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 |
| `cc2d-6` | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 |
| `cc2d-12` | 0.00 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 |

Three of these columns are vacuous outside `ccmcp`, and saying so is cheaper
than letting a reader infer a finding from them:

- `opened_scene` / `rendered` are computed from the observed sci3d tool trace.
  The `cc` and `cc2d-*` routes are given no sci3d tools, so 0.00 is the
  route definition, not agent behaviour.
- `tool_policy` is 1.0 when nothing forbidden and nothing outside the
  allow-list was called. With no sci3d tools in the trace it passes trivially.
  On `ccmcp` it is 0.35, i.e. 65% of trials called something the task's
  `allowed_tools` does not enumerate. That is either genuine out-of-policy tool
  use or an under-specified allow-list; this run does not distinguish them.
- `compliance` is an alias of `tool_policy` (`_score_core.py` copies it), not an
  independent signal.
- `finished` is 0.00 everywhere, including `ccmcp`: it fires on a sci3d
  `finish`-family tool call, and the in-container Claude Code routes end by
  emitting `SCI3D_FINAL_JSON` instead. It measures a calling convention this
  harness does not use.

## expert-v2 diagnostic track

15 records, structurally unscored: every one carries
`ground_truth.status = needs_expert_annotation`, the trees are built with
`diagnostic_only = true` and `accuracy_denominator = false`, and no reward
exists to average. 90 trajectories (15 × 6 routes) are on disk for expert
review.

**Open accounting defect.** The aggregator counts diagnostic completion as
`verifier/diagnostic_completion.json`, which is what each tree's `tests/test.sh`
writes to `/logs/verifier`. No file of that name exists anywhere in the run
root; the trials carry `verifier/sci3d_scores.json` instead (with
`evaluation_eligible: false`, `eligibility_error: unsupported_deterministic_scorer`).
So the JSON's diagnostic rows read `artifacts: 0/15` for all twelve routes.
That zero means "the file the aggregator looks for was never written", not "no
trajectories were produced" — the 90 Claude Code trial directories with their
`agent/` trees and `result.json` are present. Resolve which artifact is
canonical before anyone signs the expert review off these counts.

## Why the Codex half is empty

Every Codex route aborted at agent construction, before any container work:

```
RuntimeError: Required environment variable UNIAPI_KEY_SCIENCEVISION is absent
  agents/sciencevision_codex.py:73
```

Six scored routes at 2026-08-14T00:29Z and six diagnostic routes at
2026-08-14T00:30Z, three attempts each, all aborting within ~11 s of launch. The
adapter refuses to construct rather than fall back to `OPENAI_API_KEY` /
`CODEX_API_KEY`, which is the intended behaviour. The runner then wrote each
route's marker honestly — `scored=0 expected=60 attempts=3` — so no stale
success was recorded.

The credential was not missing from the operator's shell; it was missing from
the shell the trials ran in. `wave` launches each route as
`tmux new-window "bash run.sh full …"`, a non-interactive bash that never reads
`~/.bashrc`, so its environment is the tmux server's — which on this host
carries the Anthropic credentials globally but not `UNIAPI_KEY_SCIENCEVISION`.
`preflight` checks credentials in the interactive shell and therefore passed.
Probe and fix in
[`V21_EV2_2MODEL_ENV.md`](V21_EV2_2MODEL_ENV.md#credentials-and-the-trap-that-emptied-the-codex-half).

Two consequences for anyone re-launching:

- The Codex rows in the JSON are `missing: 60`, **not** `errored: 60`. A route
  that never produced a trial is missing; errored is reserved for a trial whose
  agent raised. Neither is a 0.0 score.
- This is a different failure from the 502 documented in the runbook. That one
  was reached inside the container and fixed by the request-repair shim; this
  one never got that far, so the shim is untested by this attempt.

## Reproducing the table

```bash
RUN=~/ScienceVision-evals/scale-v21-ev2-sixroute-2model-20260813
~/ScienceVision-venvs/scale-v21-pr170-harbor-0.20.0/bin/python \
  scripts/harbor/report_v21_ev2.py \
  --run-root "$RUN" --selection "$RUN/selection" --out "$RUN/reports"
```

`--selection` takes the directory, not `SELECTION.json` itself. The script is
stdlib-only but uses `from __future__ import annotations`, so a system
`python3` older than 3.7 fails at import; the pinned Harbor venv above works.
No Harbor import, no Docker, no credentials at this level —
[`V21_EV2_2MODEL_ENV.md`](V21_EV2_2MODEL_ENV.md) has the full matrix of what
each level needs, including why the Codex half had no credentials.

## Caveats carried from the run

- **Biology and chemistry candidates are QC `review`, not `pass`.** All 113
  biology and all 105 chemistry scale-v2.1 candidates are `review`, so the
  sampler records the QC decision instead of filtering on it — filtering would
  empty both domains. Open findings: `asset_identifier_shortcut`,
  `package_compute_shortcut`, `shortcut_control_unenforced`, `template_reuse`.
  These are leakage and contract warnings, not incorrect ground truth. All 20
  medical tasks are QC `pass`.
- **`cc2d` is a lower bound, not a visibility claim.** The render gates are
  technical — N decodable non-blank views, provenance, mask-pixel coverage for
  medical overlays. No blind reviewer has confirmed the answer is visible in the
  images. Report the family as `cc2d-all`, never `cc2d-visible`.
- **A trial whose agent raised is counted as errored, not as a 0.0 score.** The
  verifier still scores such trials degraded; averaging them in would report
  infrastructure failure as model capability.
- **Effort semantics differ across harnesses.** `xhigh` is
  `model_reasoning_effort=xhigh` for Codex and `--effort xhigh` for Claude Code:
  comparable intent, not identical semantics. Relevant once the Codex half runs.
