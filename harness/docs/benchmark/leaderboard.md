# Leaderboard

> Status: **draft / scaffold**. First entries land after the initial mirror + baseline runs.

The leaderboard reports model performance across the BabyVision-v2 task families. To preserve forward compatibility, every submission carries a `profile` field identifying which of three baseline classes it belongs to.

## Baseline profiles

| Profile | Description | Required submission fields |
|---|---|---|
| `coding` | Pure code agent: no vision encoder; reasons by calling MCP tools and inspecting geometry/numeric outputs. | `tool_trace` (full call sequence) |
| `vision` | Pure VLM: image inputs only; no tool calls (or read-only inspectors only). | `image_inputs` (manifest of images shown) |
| `fusion` | VLM + tools: typical setup. | both `tool_trace` and `image_inputs` |

## First baseline results

The first baseline run is an **information-access ablation**, not a task-family
sweep: one model over 60 scale-v2.1 tasks, varying only how the 3D asset reaches
it (raw file / raw file + MCP tools / 1, 3, 6, 12 pre-rendered views). Raw
geometry scores ~3x the rendered views. See
[`results-scale-v21.md`](results-scale-v21.md).

Those numbers do not populate the slots below — they are indexed by information
access, not by task family.

## Reserved slots (no entries yet)

| Task family | Coding | Vision | Fusion | Human |
|---|---|---|---|---|
| V2 (cross-section / topology) | — | — | — | — |
| V3 (segmentation / referring) | — | — | — | — |
| V4 (manipulation / counterfactual) | — | — | — | — |
| V6 (multi-step reasoning) | — | — | — | — |
| Genesis_VP (physics) | — | — | — | — |
| Medical Spatial (GT-grounded) | — | — | — | — |

## How to submit

See [`submission-guide.md`](submission-guide.md).

## Continuity with BabyVision v1

BabyVision v1 reported a single number (e.g. Gemini3-Pro-Preview 49.7% vs Human 94.1%) on 2D visual reasoning. v2 keeps the methodology — executable ground truth defends against LLM-as-judge — but reports per-task-family numbers because the question types differ in difficulty profile.

## Positioning vs other benchmarks

For how BabyVision-v2 sits relative to MMMU-Pro, BLINK, MathVerse, SciVerse, EmbodiedBench, Embodied3DBench, and EPIC-Bench — and what executable ground truth + profile separation add — see [`positioning.md`](positioning.md).
