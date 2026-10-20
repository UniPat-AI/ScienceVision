# Positioning — measuring the impact of 3D vision on reasoning

> Where BabyVision-v2 sits relative to other visual-reasoning benchmarks, and
> what it adds.

Most multimodal reasoning benchmarks bundle two questions together: *does the
model reason well?* and *is vision actually being used?* When a benchmark only
reports an aggregate accuracy, a high score can come from text shortcuts,
language priors over image captions, or memorized solutions — none of which
require the model to look at the image at all.

The benchmarks below were chosen because they each address some part of
**"how do we measure the contribution of vision (especially 3D vision) to
reasoning?"** They cluster into three groups plus the direct predecessor:

1. **Static 2D visual necessity** — control for text-only shortcuts on static
   images.
2. **3D scene understanding** — input is real or simulated 3D geometry, not
   just images of it.
3. **Embodied / interactive 3D** — vision must drive grounding, navigation, or
   manipulation, not just answer questions.
4. **BabyVision v1** — same lab; the predecessor that established the
   executable-GT / no-LLM-judge methodology on 2D visual reasoning, which v2
   inherits and extends.

BabyVision-v2 sits across (2) and (3) with one extra commitment the others do
not make: **executable ground truth** (geometry, mask argmax, physics solver
output), so the score is defended without an LLM-as-judge.

## Comparison

| Benchmark | Year | Visual input | How vision is tested | Ground truth | Agent / tool use |
|---|---|---|---|---|---|
| [MMMU-Pro](https://arxiv.org/abs/2409.02813) | 2024 | 2D images, multi-discipline | Filters text-only-answerable items; adds vision-only (screenshot) setting | Multiple choice | — |
| [BLINK](https://arxiv.org/abs/2404.12390) | 2024 | 2D images / image pairs | Perception primitives (depth, correspondence, multi-view) that language can't mediate | Multiple choice | — |
| [MathVerse](https://arxiv.org/abs/2403.14624) | 2024 | 2D math figures | Per-question variants moving information between text and figure | Pattern-match against canonical answer | — |
| [SciVerse](https://arxiv.org/abs/2503.10627) | 2025 | 2D scientific figures | Knowledge × vision × CoT ablation (knowledge-free/lite/rich × vision-rich/vision-only) | Pattern-match against canonical answer | — |
| [EmbodiedBench](https://arxiv.org/abs/2502.09560) | 2025 | Multi-view, simulated 3D | Vision-driven embodied multi-task: navigation, manipulation, planning, spatial awareness | Task success rate | ✓ (action) |
| Embodied3DBench | 2026 | 3D scenes, multi-view | Directly tests VLM 3D priors: grounding, spatial relation, multi-view correspondence, affordance, grasp, trajectory | Spatial labels / coordinates | partial |
| EPIC-Bench | 2026 | Real embodied images | Anti-MCQ-shortcut: fine-grained visual grounding required for target localization → navigation → manipulation | Localization + nav + manip outcomes | ✓ (action) |
| BabyVision v1 | 2026 | 2D images | Child-level visual primitives that defeat language priors (reported Gemini3-Pro-Preview 49.7% vs Human 94.1%) | Executable / structured `\boxed{}` — the anti-LLM-judge methodology v2 inherits | — |
| **BabyVision-v2** | **2026** | **3D scenes + interactive physics + viewer** | **Profile separation: every submission tagged `coding` / `vision` / `fusion`, so the contribution of vision is isolable in the leaderboard** | **Executable: numeric (distance, angle, topology, volume) + pixel (SSIM/LPIPS) + structured `\boxed{}`; LLM judge only as last-resort tiebreaker** | **✓ (MCP tools, trace replayed against server)** |

The row above BV2 is the direct predecessor: v1 established the
executable-GT / no-LLM-judge methodology on 2D visual reasoning. v2 keeps
that methodology and extends along three axes — real 3D scene grounding,
professional domains, and agent operation traces with pixel evidence (see
[`leaderboard.md`](leaderboard.md#continuity-with-babyvision-v1)).

(Years and source links for the 2026 embodied entries are recorded in
[`docs/relatedwork/visual-interactive.md`](../relatedwork/visual-interactive.md);
they may be preprints at the time of writing.)

## How BabyVision-v2 differs

The same eight benchmarks, scored against the capabilities that matter for
measuring vision's contribution to reasoning. Rows 1–2 are strengths
inherited from 2D visual-necessity work (MMMU-Pro / BLINK / MathVerse /
SciVerse); rows 3–4 are inherited from embodied / 3D work (EmbodiedBench /
Embodied3DBench / EPIC-Bench); rows 5–8 are the gaps BV2 closes that none
of the predecessors close together.

Legend: ✓ = supported · ◐ = partial · — = not supported.

| Capability | MMMU-Pro | BLINK | MathVerse | SciVerse | EmbodiedBench | Embodied3DBench | EPIC-Bench | BV1 | **BV2** |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 1. Filters text-only shortcuts | ✓ | ✓ | ✓ | ✓ | — | ◐ | ✓ | ✓ | **✓** |
| 2. Fine visual-perception primitives (depth, correspondence, multi-view) | — | ✓ | ◐ | ◐ | — | ✓ | ◐ | ✓ | **✓** |
| 3. Real 3D geometry as input (mesh / volume / point cloud, not 2D image of 3D) | — | — | — | — | ✓ | ✓ | ◐ | — | **✓** |
| 4. Multi-step agent / tool-use trace evaluated | — | — | — | — | ◐ | — | ◐ | — | **✓** |
| 5. Geometric ground truth from the scene (numeric distance / angle / topology / volume) | — | — | — | — | — | ◐ | ◐ | — | **✓** |
| 6. Pixel-level ground truth (SSIM / LPIPS for segmentation, rendering) | — | — | — | — | — | — | — | — | **✓** |
| 7. Tool-trace replayed against the server for fidelity | — | — | — | — | — | — | — | — | **✓** |
| 8. Profile separation isolates vision contribution (`coding` / `vision` / `fusion`) | — | — | — | — | — | — | — | — | **✓** |

**Reading the matrix.**
- Rows 1–4 are pre-existing strengths in the field: BV2 carries each one
  forward rather than re-inventing it. The text-shortcut filtering culture
  comes from MMMU-Pro; perception-primitive testing from BLINK; 3D-geometry
  input from the embodied cluster; agent traces from EPIC-Bench /
  EmbodiedBench.
- Rows 5–8 are where no single prior benchmark covers what BV2 covers
  together: a *layered* evaluation stack (geometric + pixel + structured,
  with LLM judge only as a tiebreaker), tool-trace replay for fidelity, and
  per-submission profile separation that turns "is vision doing work?" into
  a leaderboard-level question rather than an ablation paper.
- The BV1 column documents the inherited methodology: same anti-LLM-judge
  posture and structured-answer parsing on 2D primitives. v2 keeps that
  spine and adds the rows that depend on a real 3D scene + a tool surface.

See [`evaluation-protocol.md`](evaluation-protocol.md#layered-defense-against-llm-as-judge)
for the layered defense in detail and
[`leaderboard.md`](leaderboard.md#baseline-profiles) for the profile spec.

## What this positioning is not

- **Not a meta-leaderboard.** We do not re-run other benchmarks; numbers from
  the works above belong on their own leaderboards.
- **Not a comprehensive survey.** The four sources under
  [`docs/relatedwork/`](../relatedwork/) catalogue ~30 additional benchmarks
  (HallusionBench, MMVP, VisuLogic, CHAIN, EgoBench, OpenEQA, …) — the seven
  shown here were picked as the most informative representatives of each
  cluster for the question *"is vision doing real work?"*. The full sets are
  worth reading when designing a new task family.
- **Not a comparison with VLA-model diagnostic work.** Papers like FocusVLA,
  VLA-Trace, VLM4VLA, and CoT-VLA diagnose **models**, not benchmarks, so
  they belong in a separate discussion (see
  [`docs/relatedwork/visual-vla.md`](../relatedwork/visual-vla.md)).
