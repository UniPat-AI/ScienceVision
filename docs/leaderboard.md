# Model comparison

Twelve models were run over the same **250 questions** under three access settings. This page
records what the numbers are, where each segment came from, and what the comparison does not
establish. Read it before quoting any figure.

## Metric

| Field | Value |
|---|---|
| Score | Mean `answer_score` on `[0, 1]` |
| Ineligible trials | Counted zero |
| Denominator | 50 tasks per domain, 250 overall |
| `cc-3d`, `cc-mcp` | `mean@3`: three runs per question, averaged per task, then over tasks |
| `cc-2d-6` | `mean@3`: three runs, same metric |

The two 3D settings share a metric and a scale, so they are charted together and answer whether
the inspection tools help. The six-view setting is drawn on its own axis, so compare its order
with the 3D charts, not its bar lengths.

`answer_score` comes from the deterministic scorer described in
[evaluation.md](evaluation.md#scoring-standard). It is not a judge model's rating.

## The comparison

![Mean answer score under cc-3d and cc-mcp](../assets/leaderboard-cc3d-vs-ccmcp.png)

Under both 3D settings the five closed-source models place ahead of every open-source model.
The best overall mean is 0.53 and the highest single discipline cell is 0.65, so the ceiling
sits well out of reach and nothing is saturated. Five of the eleven adjacent pairs are cleanly
ordered; the other six have overlapping ranges, the widest of them Gemini 4 Argon against Claude
Opus 5.5, whose two-run band reaches 0.6218.

![Mean answer score under cc-2d-6](../assets/leaderboard-cc2d6.png)

The six-view setting orders differently: Claude Fable 5.1 leads it, ahead of GPT-6 Astra, which
leads both 3D settings.

**Read the position and the order, not the bar length.** This column is drawn on its own axis,
over a narrower range than the two 3D charts.

![Per-domain mean@3 under cc-3d](../assets/leaderboard-per-domain.png)

Each column carries its own scale, whose maximum sits under the discipline name, so bar length
compares models within a discipline and not disciplines with each other. Mechanical CAD,
medical imaging and architecture use most of their own range, 0.87, 0.80 and 0.79 of each
column's maximum, and their weakest models fall to 0.07, 0.08 and 0.10. Chemistry and biology
are flatter, spanning 0.60 and 0.53 of the column maximum with no model below 0.24.

## Where the numbers come from

The comparison is a **splice**. Every model's row joins a carried-over segment with a re-run
segment, and the segments were not produced by one harness:

| Segment | Source |
|---|---|
| GPT-6 Astra, GPT-6.1 Sol | Codex harness, all 250 questions, all three settings |
| The other ten models | Claude Code |
| Re-run segment: Claude Fable 5.1, Claude Opus 5.5, Gemini 4 Argon, Qwen3.8 Max 0902, Qwen3.8 Flash, Kimi K3 | A separate evaluation service |
| Re-run segment: GPT-6 Astra, GPT-6.1 Sol, GLM-5.3 Flash, DeepSeek V4 Flash, Doubao Seed Evolving, Hunyuan-4 Preview | The same harness family as their carried-over segment |

Gemini 4 Argon's carried-over segment covers **two runs rather than three** in both 3D settings,
and three of Hunyuan-4's six-view questions have two runs; every other cell covers three.

Scores are reported as evaluated. They do not show that every historical run has been
independently replayed, and rebuilding these aggregates reproduces the summary figures here,
not a replay of the original evaluation.

## Question set

This comparison uses the 250-question package published as
[`UnipatAI/ScienceVision`](https://huggingface.co/datasets/UnipatAI/ScienceVision). All 250
questions are covered; no task is missing from a model's row.

## Version drift

Runs were measured on different days, and model aliases carry no version. A difference between
two rows therefore can mix a change of run with a change of model version.

**Equal coverage does not establish a controlled comparison.**

## Reproducing a row

The collector writes per-task records and a leaderboard; the aggregator supports `flat`,
`domain_equal`, and explicit `weighted` modes. The official path requires positive process-gate
and runtime-attestation evidence, and diagnostic aggregation must be labelled as such. See
[performance.md](performance.md) for the native fields and the coverage rules, and
[evaluation.md](evaluation.md) for the run interfaces.
