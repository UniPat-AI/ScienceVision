# `docs/evaluation/` — per-dataset baseline & evaluation specs

This folder holds the **per-dataset** designs for how we baseline + evaluate the `sci3d_agent` (3D-grounded scientific QA agent). It is the dataset-side companion to the project-level synthesis at `docs/Sci3DAgentVerificationPlan.md`.

| Dataset | Slice | Eval design | Upstream-eval notes |
|---|---|---|---|
| **M3D-RefSeg** | `docs/v_classify/m3d-refseg_pilot100.jsonl` (n=100, V3) | `M3D-RefSeg/baseline_and_evaluation.md` | `M3D-RefSeg/M3D-RefSeg_evaluation_notes.md` |
| **SQA3D** | `docs/v_classify/sqa3d_v2_expanded.jsonl` (n=73, V2) | `SQA3D/baseline_and_evaluation.md` | — (paper EM-1 protocol; no separate notes file yet) |

Each per-dataset file covers:

1. Eval slice — source, size, V-class, splits, statistical power.
2. Method matrix — concrete implementation for each of the four families (M1 mesh-as-text / M2 multi-view render+VLM / M3 agent w/o vision / M4 agent w/ vision) on this dataset.
3. Scoring architecture — metrics, tracks, judge model, formula details (e.g. M3D-RefSeg's smooth-1 Dice quirk).
4. Trajectory analysis protocol — the M3-vs-M4 effective-vs-necessary disambiguation.
5. File layout — which scorer modules and adapter modules are in scope.
6. Done criteria — gate conditions for "evaluation works on this dataset."
7. Risks specific to this dataset.
8. References — papers, repos, source-of-truth files.

For project-level context (the central scientific question, the verdict logic, layered verification roadmap), read `docs/Sci3DAgentVerificationPlan.md` first.
