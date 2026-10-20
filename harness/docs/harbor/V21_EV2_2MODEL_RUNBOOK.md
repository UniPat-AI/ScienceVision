# scale-v2.1 + expert-v2, six baselines × two harnesses

Runbook for the 2026-08-13 matrix. Companion docs: `BASELINES.md` describes the
six-way information-access ablation; `CODEX_H20_RUNBOOK.md` is the single-harness
Codex procedure this extends.

## What runs

| | |
|---|---|
| baselines | `cc`, `ccmcp`, `cc2d-1`, `cc2d-3`, `cc2d-6`, `cc2d-12` |
| harnesses | Codex + `gpt-5.6-sol`, Claude Code + `claude-opus-5`, both at `xhigh` effort |
| scored tasks | 60 — scale-v2.1, 20 per domain |
| diagnostic tasks | 15 — expert-v2, unscored |
| trials | 720 scored + 180 diagnostic |
| concurrency | 2 per route, 6 routes per wave |

Task set: `QA-Gen/tasks/scale-v21-ev2-20260813/`, `id_sha256(all)` =
`6572ea8dc70470c8f498acd2ab78e8d47c6c8364e7ae34453905ef4824d664e6`.

Runner: `ScienceVision-runners/v21_ev2_2model_20260813/run.sh`
(`preflight` → `smoke-all` → `wave codex` → `wave claude-code` → `status`).

## Three things that are not free choices

**Concurrency is capped by the host, not by taste.** Harbor's docker environment
creates one compose project — and therefore one bridge network — per running
task. The host pool holds 31 and other users' multi-day jobs hold a large share,
so 6 routes × 2 is the ceiling, and `require_bridge_headroom` refuses to launch
without it. A previous scale-v1 run set `-n 24`, exhausted the pool, and lost
every trial at once. `ROUTE_CONCURRENCY=64` appears in an earlier runner's pins;
that run never reached launch, so it is not evidence the pool can take it.

**expert-v2 cannot be scored.** All 15 records carry
`ground_truth.status = needs_expert_annotation` with `type = expert_reference`,
several with empty values, so `qa_gen_to_tasks` refuses them as
evaluation-ineligible — correctly. They run as structurally unscored trees:
`diagnostic_only = true`, `accuracy_denominator = false`, no `tests/score.py`,
and a test that writes only `diagnostic_completion.json`. With no scorer in the
tree they cannot produce a leaderboard number even if a later aggregation step
forgets the distinction. Their value is 180 trajectories an expert can review
and sign; until that happens, the denominator is 60.

**Claude images are pinned, `:latest` is left alone.** The agent-env table
hardcodes `:latest`, which on this shared host is whatever another session last
built. The three Claude images are tagged `v21ev2-20260813` and every generated
Dockerfile is repointed at them; the tree audit rejects a surviving `:latest` or
`sci3d-codex` reference. The Codex images are used as `:latest` because that is
what the Codex runs before this one used, and their ids are recorded.

## Reading the results

Two caveats belong next to any table drawn from this run.

**Biology and chemistry carry leakage findings.** All 113 biology and all 105
chemistry scale-v2.1 candidates are QC `review`, not `pass`, so filtering on
`pass` would leave both domains empty; the sampler records QC decision instead
of filtering on it. The open findings are `asset_identifier_shortcut`,
`package_compute_shortcut`, `shortcut_control_unenforced` and `template_reuse` —
not incorrect ground truth. What they mean for the matrix: a raw-asset route
(`cc`, `ccmcp`) may reach the answer by computing over coordinates or by
recognizing a PDB id, rather than by perceiving the structure. All 20 medical
tasks are QC `pass`.

**cc2d is a lower bound, not a visibility claim.** The render gates are
technical: N decodable non-blank views, provenance, and mask-pixel coverage for
medical overlays. No blind reviewer has confirmed the answer is visible in the
images, so report the family as `cc2d-all`, never `cc2d-visible`.

One further asymmetry to state rather than smooth over: `xhigh` means
`model_reasoning_effort=xhigh` on the Codex side and `--effort xhigh` on the
Claude Code side. Comparable intent, not identical semantics.

## Inputs and why there are two asset roots

The two suites resolve against different frozen packages, so each tree is staged
in two passes and merged:

- scale-v2.1 → `ScienceVision-eval-inputs/scale-v2.1-expert-review-candidate823-20260810-c2660d`
- expert-v2 → `ScienceVision-eval-inputs/expert-v2-scale-v1-sample165-20260811`

Nine of the twenty medical picks are `med_mri_anatomy` tasks binding a
multi-label mask with no `mask_id`. The ROI planner has nothing to aim at, so
`_stage_rendered_views` raises `medical_roi_plan_unavailable` — the whole
"bounding extent by scrolling" stratum. The
`scale-v2.1-candidate823-mri-union-v2` overlay supplies the missing
`foreground_policy`, and the sampler substitutes its derived records for the
selected ids, gated on `instruction`, `ground_truth`, `answer_schema`, `scorer`,
`scorer_config` and `quality_gates` being byte-identical. Verified across all
60: the overlay adds rendering policy and provenance, records which label values
the already-present `extent_vox` refers to, and prefixes the
container-absolute `asset.path` with `/` (`repo_path`, which `--asset-root`
resolves against, is unchanged). Because nothing answer-bearing moves, the
overlay is applied to every route — one input across the matrix is a cleaner
control than the earlier split where only cc2d saw the derived records.

## expert-v2 instruction normalization

All 15 expert-v2 records bake an `Asset: /data/<file>` line and a fenced `sci3d`
block into their prose. The runtime injects both per route, so the baked copy
duplicates them for `cc`/`ccmcp` and leaks the raw filename to the cc2d family.
Both are stripped with the runtime's own two patterns; only `instruction`
changes. One residual identifier survives because the prose itself names the
structure — `6LU7` in `sv-chem-binding-pose-05` — reported rather than repaired,
since rewriting expert prose would change the question.

## Upstream fix this run depends on

Earlier six-route runs scored 13 of the 15 expert-v2 records. Two independent
defects were stacked: `qa_gen_to_tasks` defaulted a record with no
`asset.format` to `obj`, sending the two `.mol` chirality assets down the
trimesh mesh branch, and gemmi reports `Unknown format` for MDL V2000. Fixed by
extension-based format inference plus a dependency-free connection-table reader;
both assets now render ball-and-stick at 33 and 41 atoms. That makes them
renderable, not scoreable — see above.

## The blank tool-namespace description, and the 502 it causes

Every Codex trial returned 502 Bad Gateway from the UniAPI provider on
`/v1/responses` — five reconnects, `turn.failed`, then a scored-but-degraded 0.0
with `parse_error=missing_answer`. It reproduced on every attempt, across two
concurrent sessions, and matched the previous day's failures, so it was never
flakiness.

**Mechanism.** codex 0.147.0 sends an `additional_tools` input item declaring its
tool namespaces, and the `functions` namespace carries `description: ""`. The
provider's upstream answers 502 to any request containing a namespace group whose
description is blank, deterministically. Deleting the key still 502s — the
description must be present and non-blank.

**Fix: the in-container request-repair shim** (`agents/uniapi_shim.js`, staged by
the run's Codex adapter). `config.toml` points codex at `127.0.0.1:8317`; the
shim fills the blank description with a single space and forwards everything else
unchanged. It perturbs exactly one model-visible string, one codex left empty.

Elimination path, for the next person who sees a 502 here:

| test | result |
|---|---|
| host curl `/v1/responses`, full-length prompt, high effort | 200, four for four |
| host `codex exec` (host CLI was 0.146.0) | `turn.completed` |
| container, proxy passthrough removed | still seven 502s |
| container, `--network host` — same namespace as the working host run | still seven 502s |
| container with codex-cli 0.146.0 | `turn.completed`, zero 502s |
| container with 0.147.0, re-tested later the same day | still seven 502s |

Note what that table does and does not establish. It rules out the network, the
proxy, the credentials and the request *shape in general*, and it shows the CLI
version is decisive. It does **not** by itself identify the blank description —
that came from the shim's own notes in the concurrent run. A version A/B can
localize a defect to a version without explaining it, and stopping there would
have left a downgrade as the apparent fix.

**Why not just downgrade.** Prior runs pin `codex-cli 0.147.0` as the evaluated
CLI, so downgrading silently swaps the agent under test for an older one. The
Dockerfiles therefore pin `ARG CODEX_CLI_VERSION=0.147.0` and the shim handles
the defect. 0.146.0 images are kept under `-cli0146` tags as a fallback, clearly
labelled as a different agent.

Two consequences worth carrying forward:

- **An unpinned agent CLI is a silent-zero hazard.** The trials still ran, still
  got scored, and still produced a leaderboard — of zeros. Nothing in the runner
  exit codes said otherwise. That is why the aggregator counts a trial whose
  agent raised as *errored* rather than as a 0.0 score, and why preflight asserts
  the CLI version from inside each image rather than trusting the tag.
- **Loopback must be exempt from the proxy.** With a proxy set and no exemption,
  codex sends its loopback base_url to the proxy and never reaches the shim, so
  the runner passes `NO_PROXY=127.0.0.1,localhost` for the Codex family and the
  adapter refuses to launch without it.

## Four ways the verification logic lied, and what fixed them

Every one of these made a bad result look like a good one, which is the only
class of bug that can quietly invalidate a whole matrix. They are listed because
each is easy to reintroduce.

**A reused smoke job root turns a stale success into a pass.** `smoke()` wrote to
`smoke-<family>-<route>-attempt1`, so after a configuration change the detector
found the *previous* configuration's successful trial sitting in the same
directory and reported OK — while the new trial had actually raised
`NonZeroAgentExitCodeError`. Re-smoking a changed configuration is exactly when
this bug fires. Each smoke now gets a timestamped job root.

**`reward.json` present is not success.** A trial whose agent dies upstream still
gets scored: 0.0, degraded, `parse_error=missing_answer`. Counting "scored" as
"done" retires tasks that never ran and skips their retries. Completion is now
`result.json` with rewards **and** no `exception_info`.

**Printing an empty result reads as one anonymous id.** `print("\n".join(ids))`
on an empty set emits a blank line, so `[[ -n "$(...)" ]]` was always true and
*every* smoke would have passed. Print nothing when empty.

**A diagnostic track has no reward by construction**, so the scored detector
finds nothing there and burns all three attempts on completed work. Completion
for that track is `diagnostic_completion.json` with
`status=agent_artifacts_present`.

`test_detector.sh` pins the first two against four real job roots — two clean
successes and two agent deaths that were scored 0.0 — and all four must classify
correctly. Run it after touching the detector.

Two smaller traps in the same family: `grep` exits 1 when it finds nothing, which
under `pipefail` kills a preflight check whose pass condition *is* finding
nothing; and `python - <<'PY'` consumes stdin for the program text, so anything
piped in is silently dropped — pass a file path instead.

## Operational notes

- `trimesh` is absent from the pinned Harbor venv, so the mesh renderer cannot
  run there. This task set is mmCIF / xyz / mol / nii.gz only, all of which
  render through matplotlib, so it does not block. It does mean
  `tests/mcp/test_render_static.py::test_missing_dependency_returns_envelope`
  fails in that venv, before and after this run's changes.
- Two `tests/harbor_adapters/test_codex_runtime.py` cases need
  `PATH="$HOME/miniconda3/bin:$PATH"`; the system `python3` is too old for
  `from __future__ import annotations`. With it, 290 pass.
- A transient upstream 502 from the shared gateway killed a previous smoke.
  Preflight now probes both endpoints, and smoke retries up to three times on
  infrastructure failure. A secret-scan failure is a security gate and is never
  retried.
