# Results: scale-v2.1 information-access ablation

> Status: **interim**. One harness of two. The six Claude Code routes below are
> complete (60/60 scored trials each, zero errored, zero missing); the six Codex
> routes produced no trials and are not reported. Numbers as of
> 2026-08-14 09:00 CST.

This is the first baseline result set on BabyVision-v2 task material. It answers
one question: **how much does an agent's score depend on the form the 3D asset
is handed to it in?** Same model, same 60 tasks, same effort — only the
information access changes.

## The six routes

| Route | What the agent gets | Closest [profile](leaderboard.md#baseline-profiles) |
|---|---|---|
| `cc` | the raw 3D file on disk, no 3D tools | `coding` |
| `ccmcp` | the raw 3D file plus the sci3d MCP tool set | `fusion` |
| `cc2d-1` | one pre-rendered view, no raw file | `vision` |
| `cc2d-3` | three pre-rendered views | `vision` |
| `cc2d-6` | six pre-rendered views | `vision` |
| `cc2d-12` | twelve pre-rendered views | `vision` |

Harness: Claude Code CLI in-container, `claude-opus-5`, `--effort xhigh`.
Tasks: 60 scale-v2.1 items, 20 each in biology, chemistry and medical, frozen by
`id_sha256 65dc939dff9df7ac3a4b27df3cb5ca20edc3621c64cc972c414db82f45327cd4`
under sampling seed `20260813`.

## Scores

`answer_score` is executable ground truth over the frozen 60-task denominator —
no judge, no process credit.

| Route | answer_score | Abstained | Biology | Chemistry | Medical |
|---|--:|--:|--:|--:|--:|
| `cc` | **0.556** | 0 | 0.340 | 0.817 | 0.512 |
| `ccmcp` | **0.558** | 0 | 0.340 | 0.783 | 0.550 |
| `cc2d-1` | 0.183 | 20 | 0.260 | 0.283 | 0.006 |
| `cc2d-3` | 0.157 | 20 | 0.320 | 0.150 | 0.000 |
| `cc2d-6` | 0.213 | 13 | 0.390 | 0.250 | 0.000 |
| `cc2d-12` | 0.249 | 13 | 0.300 | 0.404 | 0.044 |

`Abstained` counts trials returning the answer template with every field null.
Those score 0 like a wrong answer, but the behaviour differs: the agent declined
rather than invented a number. A 0.000 cell with 6 abstentions out of 20 is a
different result from a 0.000 earned by 20 wrong guesses.

## What the run does and does not show

**Access to the raw geometry is worth roughly 3x the rendered views** on this
task set — 0.556 against 0.157–0.249. That gap is the headline, and it is the
gap the benchmark is designed to expose.

**Adding the sci3d MCP tools changed nothing measurable here**: 0.556 vs 0.558
at n=60, moving in both directions per domain. The tool trace explains why this
is not yet a verdict on the tools — on `ccmcp` a scene was opened in 37% of
trials and a render call made in 17%. The route measures tools *offered*, not
tools *used*.

**More views help, but not monotonically.** 6 and 12 views recover part of the
gap (0.213, 0.249) while 3 views score below 1 view. At n=60 the view count is
not cleanly separable from sampling noise.

**Medical collapses on image-only routes** to 0.006 / 0.000 / 0.000 / 0.044,
with 6–9 abstentions out of 20. Those tasks ask for voxel indices and RAS+
millimetres; slice PNGs carry no coordinate frame to answer in. This is a
task-format wall, not a perception measurement.

**Chemistry is the strongest raw-file domain and the most suspect.** 16 of its
20 tasks ask for an explicitly ordered four-atom dihedral and 4 for a distance
between two declared atoms — both computable from the coordinate file by
arithmetic, with nothing perceived. See the leakage caveat below.

## Caveats

- **Biology and chemistry candidates are QC `review`, not `pass`.** Open
  findings are `asset_identifier_shortcut`, `package_compute_shortcut`,
  `shortcut_control_unenforced` and `template_reuse` — leakage and contract
  warnings, not incorrect ground truth. A raw-asset route may reach some of
  those answers by computing over coordinates or recognizing an identifier
  rather than by perceiving structure. All 20 medical tasks are QC `pass`.
- **The `cc2d` family passed technical render gates only** — decodable non-blank
  views, provenance, mask-pixel coverage for medical overlays. No blind reviewer
  has confirmed the answer is visible in the images, so these numbers are a
  lower bound on what the images support. The family is `cc2d-all`, never
  `cc2d-visible`.
- **Single run, no seeds.** One trial per task per route. No variance estimate,
  so treat sub-0.05 differences as unresolved.
- **Nothing here fills the reserved leaderboard slots.** Those are indexed by
  task family (V2/V3/V4/V6, Genesis_VP, Medical Spatial); this run is indexed by
  information access over scale-v2.1 domain tasks.

## Provenance

- Method, pins and failure modes: [`../harbor/V21_EV2_2MODEL_RUNBOOK.md`](../harbor/V21_EV2_2MODEL_RUNBOOK.md)
- What to install to reproduce any level of this — re-aggregating the table needs
  neither Harbor nor Docker: [`../harbor/V21_EV2_2MODEL_ENV.md`](../harbor/V21_EV2_2MODEL_ENV.md)
- Full accounting, process signals, diagnostic track:
  [`../harbor/V21_EV2_2MODEL_RESULTS.md`](../harbor/V21_EV2_2MODEL_RESULTS.md)
- Machine-readable aggregate: [`../harbor/V21_EV2_2MODEL_LEADERBOARD.json`](../harbor/V21_EV2_2MODEL_LEADERBOARD.json)
- Aggregator: [`../../scripts/harbor/report_v21_ev2.py`](../../scripts/harbor/report_v21_ev2.py)
- Harbor 0.20.0; agent images pinned `v21ev2-20260813`; scorer image
  `sci3d-scorer:harbor-0.20-v21ev2-20260813`.
