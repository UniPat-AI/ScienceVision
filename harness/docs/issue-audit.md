# Repo Issue Audit — 2026-06-25 (refreshed 2026-06-26)

Walked all 16 open issues against `main` and checked, file-by-file, whether
what the issue asks for already lives in the repo. The result is a single
triage page that doubles as the work backlog.

**Refresh log**

- 2026-06-25: initial audit at HEAD `ffaf5e0`.
- 2026-06-26: refreshed at HEAD `58c4594` after closing #5, #7, #9 and
  merging PR #51 (#10). Status column updated; sections retained for history.

**Status legend**

- ✅ **resolved** — current code/docs satisfy the issue's acceptance criteria; the GitHub issue can be closed.
- 🟡 **partial** — some pieces shipped; explicit gaps remain. Close requires the work listed in the TODO.
- ❌ **open** — none of the asks shipped yet; TODO maps the path to closure.
- ⚖️ **decision** — design question still needs a call; TODO lists the call to make and its blast radius.

---

## Summary table

| # | Title | Status | TODO |
|---|---|---|---|
| 50 | Converge ModelScope data-fetch (PR #39 vs PR #46) | ⚖️ decision | [→](#50--converge-modelscope-data-fetch) |
| 34 | Write `docs/benchmark/harbor.md` | ❌ open | [→](#34--write-docsbenchmarkharbormd) |
| 33 | Harbor as leaderboard infrastructure (design) | ⚖️ decision | [→](#33--harbor-as-leaderboard-infrastructure) |
| 32 | Mirror Harbor source (`harbor_adapters/`, `scripts/harbor/`, tests) | ❌ open | [→](#32--mirror-harbor-source) |
| 21 | Homepage Timeline section — content & placement | ⚖️ decision | [→](#21--homepage-timeline-section) |
| 20 | Baseline framework + per-(domain×profile) coverage matrix | ❌ open | [→](#20--baseline-design-framework) |
| 19 | Expert-annotation workflow for `needs_expert_annotation` tasks | ❌ open | [→](#19--expert-annotation-workflow) |
| 18 | Mirror Harbor verify route + batch-verify docs | ❌ open | [→](#18--mirror-harbor-verify-route) |
| 16 | End-to-end evaluation walkthrough | 🟡 partial | [→](#16--end-to-end-evaluation-walkthrough) |
| 15 | Guide for designing and registering a new MCP tool | ❌ open | [→](#15--mcp-new-tool-guide) |
| ~~10~~ | ~~`tests/mcp/*` need a `conftest.py` skip~~ | ✅ closed (PR #51, merged `58c4594`) | — |
| ~~9~~ | ~~Stage 1.5 — publish demo NIfTI + mask to a public dataset~~ | ✅ closed via ModelScope (PR #46) | — |
| 8 | Drop "pointcloud" as a top-level domain | 🟡 partial — couples to #50 | [→](#8--drop-pointcloud-as-top-level-domain) |
| ~~7~~ | ~~Where does Genesis_VP belong — "embodied" domain?~~ | ✅ closed (path 1 in manifest) | — |
| 6 | Domain taxonomy — industrial vs simulation | ⚖️ decision | [→](#6--industrial-vs-simulation) |
| ~~5~~ | ~~Homepage Compare table — benchmarks & axes~~ | ✅ closed (PR #44 + #47) | — |

Counts after refresh: ✅ 4 closed · 🟡 2 partial · ❌ 6 open · ⚖️ 4 decision. Net change since 2026-06-25: −4 closed, −1 decision (#5 was already closed).

---

## #50 — Converge ModelScope data-fetch

**Status:** ⚖️ decision required (PR #39 still open, MERGEABLE on the API but
substantively conflicts with shipped #46).

**Current state**

- PR #46 (merged `9b8081b`) — `examples/02_medical_spatial_gt/data/fetch.sh`
  uses `modelscope.hub.snapshot_download`; 6 references to "modelscope" in the
  script. Medical-only scope.
- PR #39 (still OPEN, branch `feat/viewer-feedback-batch2-b`) — would add
  `scripts/sync_data.py` (does not exist on `main`), upgrade
  `assets.index.json` with `remote_url`/`viewer_url` fields, and `git rm --cached`
  the 4 Genesis_VP GLBs.
- The label drift the issue describes is real: `assets.index.json` on `main`
  already says "Abdominal CT — M3D-RefSeg s0132…" (PR #46), so PR #39's
  rebase has to keep that wording.

**TODO**

- [ ] Pick (a) / (b) / (c) from the issue (recommendation in issue body is (a)).
- [ ] If (a): on PR #39 branch, drop `scripts/sync_data.py` + GLB untrack work, keep only the F6 viewer_url wiring. Rebase onto `main`, force-push.
- [ ] If (c): split PR #39 into (c1) viewer_url wiring + (c2) sync-tool design PR.
- [ ] Once converged: add/update `docs/platform/data-sync.md` (does not yet exist) to document the chosen `remote_url` / `viewer_url` schema.

---

## #34 — Write `docs/benchmark/harbor.md`

**Status:** ❌ open. Blocked by #32 (code mirror) and #33 (design).

**Current state**

- `docs/benchmark/harbor.md` does not exist.
- `harbor_adapters/`, `scripts/harbor/` not in the repo (see #32).
- No mention of Harbor in `docs/benchmark/leaderboard.md`,
  `submission-guide.md`, or `evaluation-protocol.md`.

**TODO**

- [ ] Close #32 first (need the verifier surface to cite).
- [ ] Close #33 (need the 5 design answers — schema, gate, thresholds, weights, rubric ownership).
- [ ] Author `docs/benchmark/harbor.md` per the deliverable list in the issue body.
- [ ] Patch cross-doc references in `leaderboard.md`, `submission-guide.md`, `evaluation-protocol.md`, `new-domain-template.md`.

---

## #33 — Harbor as leaderboard infrastructure

**Status:** ⚖️ decision required. No spec file exists; the 5 open questions
have no recorded answers.

**Current state**

- `docs/superpowers/specs/` contains only the platform-design spec and the
  asset-picker SOP — no Harbor integration spec.

**TODO**

- [ ] Write `docs/superpowers/specs/2026-06-2X-harbor-leaderboard-integration.md` answering:
  - Q1 submission JSON schema (`harbor_verify_result` block vs sibling artifact)
  - Q2 hard CI gate vs maintainer-run
  - Q3 publish per-profile `process_*` thresholds?
  - Q4 reweight per profile?
  - Q5 partner ships rubric in new-domain template?
- [ ] Per-doc patches (`leaderboard.md` / `submission-guide.md` / `evaluation-protocol.md`) listed in the spec.

---

## #32 — Mirror Harbor source

**Status:** ❌ open. None of the three directories exist on `main`.

**Current state**

- `harbor_adapters/`, `scripts/harbor/`, `tests/harbor_adapters/` all MISSING.

**TODO**

- [ ] Add three include patterns to `.mirror-include` (`harbor_adapters/**`, `scripts/harbor/**`, `tests/harbor_adapters/**`).
- [ ] Decide A vs B for `scripts/harbor/tasks/` (mirror vs gitignore + builder); issue recommends B.
- [ ] Add `harbor` to `pyproject.toml` (runtime dep or `[harbor]` extra).
- [ ] Run mirror; verify `pytest tests/harbor_adapters/ -v` green.
- [ ] Confirm no internal-only paths leak through (grep for `scripts/eval/walkingskeleton/`).

---

## #21 — Homepage Timeline section

**Status:** ⚖️ decision required. Neither a Timeline section in
`viewer/index.html` nor a Timeline spec in
`docs/superpowers/specs/2026-06-19-bv2-viewer-platform-design.md` (0 hits).

**TODO**

- [ ] Pick content axes (release history vs mirror milestones vs partner onboarding).
- [ ] Pick source of truth (git tags vs hand-curated JSON vs CHANGELOG).
- [ ] Pick placement (between Domain coverage and Compare, above Get started, or its own `viewer/timeline.html`).
- [ ] Add §3.8 Timeline to the platform-design spec.
- [ ] Implement against the spec.

---

## #20 — Baseline design framework

**Status:** ❌ open.

**Current state**

- `docs/evaluation/` has per-dataset baselines (`M3D-RefSeg/`, `SQA3D/`,
  `WalkingSkeleton_LLM/`, `sv3d_tool_use/`) but no top-level framework doc.
- `docs/benchmark/baseline-design.md` does not exist.
- `docs/benchmark/leaderboard.md` shows the 3×6 grid but all cells `—` with
  no coverage tracker.

**TODO**

- [ ] Author `docs/benchmark/baseline-design.md` — distilled M1–M4 method matrix, scoring arch, trajectory analysis, file layout, done criteria, risks.
- [ ] Add a coverage table or generated index of which `(domain × profile)` cells have a baseline doc / baseline number / TBD.
- [ ] Add a contributor checklist with the link to `docs/evaluation/<dataset>/baseline_and_evaluation.md`.

---

## #19 — Expert-annotation workflow

**Status:** ❌ open.

**Current state**

- `docs/qa-gen/` has `medical_k_axis_seeds.md` + `pipeline-overview.md` only.
- `docs/qa-gen/expert-annotation.md` MISSING.
- `WalkingSkeleton_LLM/verify_report.md` confirms 26/26 tasks still `needs_expert_annotation`.

**TODO**

- [ ] Design the workflow (interface, recorded ops, landing location, gate-to-pass).
- [ ] Lock the operation-trace schema (mirror `tool_trace` from `submission-guide.md`).
- [ ] Decide tooling — viewer page vs CLI ingestor; pick one and scope it.
- [ ] Author `docs/qa-gen/expert-annotation.md`.

---

## #18 — Mirror Harbor verify route

**Status:** ❌ open. Same root cause as #32.

**Current state**

- No Harbor code (see #32). No `docs/benchmark/harbor.md` (see #34).

**TODO**

This issue is the parent of #32 and #34. It closes when those two close.

- [ ] Close #32 (mirror).
- [ ] Close #33 (design).
- [ ] Close #34 (public docs).

---

## #16 — End-to-end evaluation walkthrough

**Status:** 🟡 partial — directory exists but README is a 5-line placeholder
(`examples/04_run_evaluation/README.md`, line count = 5).

**Current state**

- `examples/04_run_evaluation/` EXISTS; only contains a stub README.
- `docs/benchmark/run-evaluation.md` MISSING.
- `docs/benchmark/{evaluation-protocol,submission-guide,leaderboard}.md` exist.

**TODO**

- [ ] Fill `examples/04_run_evaluation/` with a runnable demo: install → infer → score → produce submission JSON.
- [ ] Add a submission-JSON validator the example calls for self-check.
- [ ] Author `docs/benchmark/run-evaluation.md` as the entry point, pointing at `examples/04` for runnable form and at per-dataset `docs/evaluation/<dataset>/` for depth.

---

## #15 — MCP new-tool guide

**Status:** ❌ open.

**Current state**

- `docs/mcp/` has `MCP_SERVER_SPEC.md` + `MCP_ROLLOUT_PLAN.md` + `tools/`.
- `docs/mcp/new-tool-guide.md` MISSING.

**TODO**

- [ ] Author `docs/mcp/new-tool-guide.md` covering categories A–F decision tree, ToolSpec authoring, registration, test scaffold, schema mirror, cross-cutting rules, end-to-end example.

---

## #10 — `tests/mcp` conftest.py skip

**Status:** ❌ open.

**Current state**

- `tests/mcp/` has 8 test files + `__init__.py`.
- `tests/mcp/conftest.py` MISSING.
- 17 failures listed in the issue still trip on a fresh checkout per PR #39's test plan ("584 passed, 17 pre-existing failures in tests/mcp/, unchanged from main").

**TODO**

- [ ] Add `tests/mcp/conftest.py` with a session-scoped fixture that detects whether the required raw data dirs (astronomy spec JSON, ScanNet PLY, VQA JSON) exist and `pytest.skip(...)`s the whole package when absent. Skip reason should point at the partner-pull instructions.
- [ ] Verify `pytest tests/` reports the 17 as `SKIPPED`, not `FAILED`.

---

## #9 — Stage 1.5 — publish demo NIfTI + mask

**Status:** ✅ resolved (via ModelScope instead of HuggingFace).

**Current state**

- `examples/02_medical_spatial_gt/data/fetch.sh` pulls from public ModelScope
  dataset `tianyi3unipat/BabyVision-v2-Datasets` via `snapshot_download` as
  the preferred path; the sibling-checkout path remains as offline fallback.
- The "5-minute partner onboarding" promise holds on any clone with internet.

**TODO**

- [ ] Close issue #9 on GitHub with a comment noting "delivered via PR #46 against ModelScope, not HuggingFace".
- [ ] (Optional) checksum-pin the fetched files in `fetch.sh` so future demo runs match the snapshot.

---

## #8 — Drop pointcloud as top-level domain

**Status:** 🟡 partial.

**Current state**

- `viewer/manifest.json` `domains[]` does NOT include `pointcloud` — the
  intent of #8 is already reflected in the manifest. The current 11 domains
  in the manifest are: biology, medical, embodied, robotics, geography,
  astronomy, topological_photonics, … (the list ends past my grep cut, but
  no `pointcloud` row exists).
- `data/assets.index.json` `domain` values today are only `embodied` +
  `medical` (the 2 shipped today), so no inventory drift.
- `viewer/pointcloud.html` still exists — kept as a renderer, which matches
  the proposal.

**TODO**

- [ ] Verify the full 11-domain list in `viewer/manifest.json` matches the proposed taxonomy (no `pointcloud`, includes `embodied` + `climate`).
- [ ] Confirm `viewer/pointcloud.html` is reachable via `?viewer=pointcloud` from any domain card — wire the convention if not.
- [ ] Update `docs/3d_assets_inventory.md` if it still lists `pointcloud` as a top-level (will surface at next mirror sync).
- [ ] Close issue #8 once verified.

---

## #7 — Genesis_VP → embodied domain

**Status:** ✅ resolved.

**Current state**

- `data/assets.index.json` Genesis_VP assets carry `"domain": "embodied"` (4
  entries).
- `viewer/manifest.json` has `{"id": "embodied", "label": "Embodied", "shipped": true}`.

**TODO**

- [ ] Verify `physics` is renamed to `climate` in the manifest (path 1 in the issue).
- [ ] Close issue #7 once that rename is confirmed.

---

## #6 — Industrial vs simulation

**Status:** ⚖️ decision required. Both still exist as candidate domains in
the manifest (neither shipped today, so the call has no live blast radius).

**TODO**

- [ ] Pick A (keep separate, UI differentiates) or B (merge with tag).
- [ ] If A: spec the `assets.html` simulation card template difference (field overlay toggle).
- [ ] Record the decision in `docs/superpowers/specs/2026-06-19-bv2-viewer-platform-design.md`.

---

## #5 — Homepage Compare table

**Status:** ✅ resolved.

**Current state**

- `viewer/index.html` Compare section uses `bv2-compare-matrix` (PR #44, merged).
- 8 benchmark columns: MMMU-Pro, BLINK, MathVerse, SciVerse, EmbodiedBench, Embodied3DBench, EPIC-Bench, BV1, BV2.
- 8 capability rows covering all 7 axes from the issue (3D-grounded, executable, agent/tool, profile separation, multi-domain, interactive viewer, partner SDK) plus pixel-level GT and tool-trace replay.
- 3-state ✓/◐/— glyphs used as the issue proposed.
- 5 of the 8 column headers link to arxiv (PR #47, merged); only BV1/BV2/Embodied3DBench/EPIC-Bench unlinked (no public arxiv yet).
- `docs/benchmark/positioning.md` carries the long-form comparison and source-paper links.

**TODO**

- [ ] Close issue #5 with reference to PR #44 + #47.

---

## Recommended order to clear the backlog

1. **Cheap wins to ship now** (small, atomic):
   - Close #5, #7, #9 (already resolved).
   - Land #10 — one `conftest.py` file, hours not days.

2. **Single-call decisions** (unblock everything downstream):
   - #50 (pick a/b/c so PR #39 can land or close).
   - #6, #21, #33 — write the 1–3-line decision into the platform-design spec.

3. **Harbor track** (largest single chunk):
   - #32 mirror → #33 design → #34 docs → #18 closes when those close.

4. **Long-form docs** (each is ~1–2 days of focused writing):
   - #15 (MCP new-tool guide)
   - #16 (end-to-end eval walkthrough + `examples/04` runnable)
   - #19 (expert-annotation workflow)
   - #20 (baseline framework + coverage matrix)

5. **Manifest verification** (cheap but blocks closures):
   - #8 — verify `pointcloud` is fully out of the manifest and inventory doc.
