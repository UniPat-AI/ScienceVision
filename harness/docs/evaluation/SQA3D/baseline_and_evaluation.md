# SQA3D — Baseline & Evaluation Design

**Date:** 2026-05-21
**Slice:** `docs/v_classify/sqa3d_v2_expanded.jsonl` — n=73 V2 items (expansion ran 2026-05-20; 1500-item Gemini-3.1-Pro classifier sweep + audit, PASS)
**Role in the agent verification:** the **3D-indoor-scene side** of the 4×2 method-family ablation in `docs/Sci3DAgentVerificationPlan.md`.

---

## 1. Task definition (recap)

SQA3D = Situated Question Answering on 3D Scenes. A 3D scene (ScanNet) + a "situated" natural-language question that depends on agent position and orientation in the scene ("if I turn around, can I see X?", "what is in front of me?").

Output: a textual answer that must match the paper's choice set (EM-1 protocol). For free-form agent answers that don't match verbatim, an LLM-judge fallback is used.

| Slice we care about | V-class | Why this slice |
|---|---|---|
| **V2 — visibility / occlusion / FOV** | "can I see X if I turn around", "is X blocked from here" | Vision is *hypothesized* to be load-bearing on V2. If it's not necessary here, it's unlikely necessary anywhere on SQA3D. |

V1–V6 definitions: `docs/Sci3DEvaluationCriteria.md`.

## 2. Eval slice

- **Source:** `docs/v_classify/sqa3d_v2_expanded.jsonl` (73 V2 items)
- **How produced:** `scripts/v_classify/classify_qa.py` over a 1500-item sample of `datasets/SQA3D/sqa_task/balanced/`, model `gemini-3.1-pro-preview` via Mindra proxy. Plan: `docs/superpowers/plans/2026-05-20-sqa3d-v2-expansion-run.md`. Audit: `docs/v_classify/AUDIT_sqa3d_v2.md`.
- **Audit gate:** ≥10 hand-spot-checked verdicts confirm ≥80% genuine V2.
- **Pilot V2 items:** 3 from `sqa3d_pilot100.jsonl` are merged in (not duplicated).
- **Statistical power:** n=73, 95% CI half-width ±11–12pp at p=0.5. Adequate for ≥10pp M3-vs-M4 delta; sub-5pp differences not resolvable.

## 3. Method matrix on this dataset

| Method | Implementation | Native mask head? | Text output? |
|---|---|---|---|
| **M1 — Mesh-as-text / 3D-as-tokens** | **Chat-Scene** (NeurIPS 2024; Vicuna-7B; SQA3D EM 54.6 reported); github.com/ZzZZCHS/Chat-Scene | n/a (SQA3D is text-only) | ✅ |
| **M2 — Multi-view render + VLM** | **LLaVA-Cube (LLaVA³)** (arXiv 2511.16454; per-scene NeRF → object decomposition → ordered multi-view tokens; **zero fine-tuning**); github.com/CEA-LIST/LLaVA-Cube. **GPT-4o-on-views** as cheap API floor (arXiv 2405.18831). *Agent3D-Zero (arXiv 2403.11835) cited as archetype — code NOT released; do not run.* | n/a | ✅ |
| **M3 — Tool-use agent w/o 3D vision** | `sci3d_agent` `mode1_no_vision` | n/a | ✅ |
| **M4 — Tool-use agent w/ 3D vision** | `sci3d_agent` `mode3_free_visual` ; **+ GPT4Scene** (arXiv 2501.01428; github.com/Qi-Zhangyang/gpt4scene) as external with-vision reference (repro 4 — bundles fine-tuning) | n/a | ✅ |

`mode2_one_image` (single canonical image, pixels not returned) is **not** in the ablation — it would blur the clean vision/no-vision toggle.

### Repro shortlist with hardware

| Pick | Repro | Hardware | Reported (SQA3D) | Notes |
|---|---|---|---|---|
| **Chat-Scene** | 2 | 1× A100 40G | EM 54.6 | Vicuna-7B + object-centric tokens; clean `evaluate` flag |
| 3D-LLaVA | 3 | 1× A100 | reported | point clouds via Omni Superpoint Transformer |
| LEO | 2 | 1× A100 | EM ~50 | Vicuna-7B; object-centric scene tokens |
| LL3DA | 2 | 1× A100 | < above | query-token (Interactor3D) over scene point features |
| **LLaVA-Cube** | 3 | 2× A100 (NeRF training per scene) | comparable to zero-shot 3D-LLMs | only released, code-complete true multi-view-render+VLM with no API dep |
| GPT-4o-on-views | 2 | API only | floor | a few rendered canonical views fed to GPT-4o |
| GPT4Scene | 4 | 4× A100 (fine-tuning) | strong | builds BEV + spatio-temporal object markers; 165K-item data |

> Recommended in §3 are the top picks per family. Others are documented for completeness and may be swapped if hardware or repo issues arise.

## 4. Scoring architecture

### 4.1 Single track — EM-1 with judge fallback

Unlike M3D-RefSeg, SQA3D has **no mask track** — answers are textual.

| Track | Metric | Methods scored | Purpose |
|---|---|---|---|
| **Text track (primary)** | EM-1 (paper protocol — exact-match top-1 over the choice set) | M1 / M2 / M3 / M4 | The paper's headline metric; comparable to leaderboard. |
| **Text track (fallback)** | Claude LLM-judge → `0.0 / 0.5 / 1.0` with question-anchored rubric | Same | For free-form answers that don't match the choice set verbatim — common with M3 / M4. |

EM-1 implementation: paper-protocol normalization (lowercase, strip articles, collapse whitespace), exact match against the gold answer string. Re-uses the Tier-1 scoring path; reuses `sci3d_bench/scorers/llm_judge.py` for the fallback (no new module needed; the prompt is generic enough).

### 4.2 What the LLM-judge fallback rubric looks like for V2

| Score | Meaning |
|---|---|
| **1.0** | Answer is correct *and* matches the situated viewpoint constraint (e.g. "yes, I can see X" when the question asked from the agent's pose) |
| **0.5** | Right entity but wrong directional / visibility qualifier; or hedged-but-correct |
| **0.0** | Wrong entity, denied / non-answer |

Judge model: `claude-sonnet-4-6` via Mindra proxy. Independent LLM family from any baseline being scored.

## 5. Trajectory analysis (M3 / M4 only)

Same protocol as M3D-RefSeg (`docs/Sci3DAgentVerificationPlan.md` §5.2):

1. **Tool-call rate** — fraction of items where M4 invoked render_view / screenshot / set_camera ≥ 1 time.
2. **Answer-flip rate (vision-redaction counterfactual)** — replay with image bytes redacted; count answer changes.
3. **Vision-attributable delta** — accuracy delta of M4 vs M3 conditioned on whether vision was actually invoked.

V2 questions are exactly the ones for which the agent *should* call render_view (the question is about visibility from the agent's pose). A near-zero tool-call rate on V2 is itself a finding (vision is available but unused even when ideal).

## 6. Why the SOTA prior art makes this a controlled ablation, not a rediscovery

The SQA3D paper itself reports the central paradox we are testing:

| Baseline | EM-1 |
|---|---|
| Blind / language-only | 26.75% |
| Best 3D model (paper time) | 47.20% |
| BEV / egocentric video | **lower than blind** |
| Human | 90.06% |

The authors attribute the BEV/egocentric drop to "vision-bias when the visual input is provided." Other prior art (zero-shot GPT-4V on 3D VQA, VLind-Bench, VLMs-are-Biased) reaches the same conclusion: LVLMs default to language priors.

**Counter-evidence** (which our study is designed to qualify, not refute): MV-ScanQA (2508.11058) and HMR3D (2511.22961) both show multi-view renders provide complementary signal over text-only descriptions on related 3D-QA splits.

Our V2 ablation lives at the intersection: V2 is precisely where vision *should* be load-bearing per the question taxonomy, and where our agent has rendering tools available.

## 7. File layout

The SQA3D verify side is **simpler** than M3D-RefSeg:

```
sci3d_bench/scorers/
├── llm_judge.py                     [exists]   reused for the V2 free-form fallback
└── (EM-1 scorer)                    [PLAN]     paper-protocol exact-match; ≤80 LoC

sci3d_bench/adapters/
└── (M1 / M2 scene adapters)         [PLAN]     Chat-Scene, LLaVA-Cube, GPT-4o-on-views, GPT4Scene

scripts/sqa3d_verify/                [PLAN]     analogous to scripts/m3d_refseg_verify/
├── run_smoke.py
└── README.md
```

The EM-1 scorer is small enough to live alongside `llm_judge.py` rather than getting its own file. Adapters are the bulk of the work.

## 8. Done criteria — what "evaluation works on this dataset" means

- [ ] EM-1 scorer + tests + smoke (TDD-ed).
- [ ] LLM-judge fallback path covered (smoke item that EM-1 misses but the judge accepts).
- [ ] Chat-Scene adapter reproduces a paper-table EM-1 spot-check on **3 hand-picked dev-set items** within ±2pp.
- [ ] All four method families produce trajectories on n=73, no fatal failures.
- [ ] EM-1 + LLM-judge both reported per method per item.
- [ ] Trajectory metrics (tool-call, flip, vision-attributable delta) computed for M4.
- [ ] GPT4Scene either runs (repro 4 budget approved) or is explicitly dropped to "cited" status in the report.
- [ ] Per-method × per-item table published (matched-pairs input).

## 9. Risks specific to this dataset

| # | Risk | Mitigation |
|---|---|---|
| 1 | **n=73 vs target 30–50** — we over-shot; statistical power is fine, but more items = more API cost for the LLM-judge fallback path. | Acceptable; budget explicitly. |
| 2 | **Asymmetric with-vision external reference** — GPT4Scene is the only released option; repro 4 (fine-tuning bundled). | Decision deferred to phase-2 budgeting; document as "scene side has a second with-vision data point, medical side does not." |
| 3 | **EM-1 brittleness** — agents emit free-form prose that misses the choice set verbatim. | LLM-judge fallback already specified; ensure both numbers are reported. |
| 4 | **ScanNet scene fetch / preproc** — Chat-Scene and LLaVA-Cube need ScanNet meshes for the 73 V2 scenes. | We already pulled 8 ScanNet train scenes for matched availability; verify the 73 V2 items are within that set or pull more. |
| 5 | **Vision-bias prior** is so well-documented that a null result might be confounded with mode-parity bugs. | Aggressive mode-parity audit (M3 / M4 must share backbone, scaffold, decoding); trajectory analysis to disambiguate. |

## 10. References

- SQA3D — arXiv 2210.07474 (Ma et al. ICLR 2023); blind-baseline + vision-bias evidence
- Zero-Shot GPT-4V on 3D VQA — arXiv 2405.18831; "blind is strong" + GPT-4o-on-views protocol
- VLind-Bench — arXiv 2406.08702 ; VLMs are Biased — arXiv 2505.23941 (language-prior dominance)
- HMR3D — arXiv 2511.22961 ; MV-ScanQA — arXiv 2508.11058 (counter-evidence: renders add signal)
- Chat-Scene — arXiv 2312.08168 ; github.com/ZzZZCHS/Chat-Scene
- 3D-LLaVA — arXiv 2501.01163 ; LEO — arXiv 2311.12871 ; LL3DA — github.com/Open3DA/LL3DA
- LLaVA-Cube — arXiv 2511.16454 ; github.com/CEA-LIST/LLaVA-Cube
- Agent3D-Zero — arXiv 2403.11835 (cited archetype, code NOT released)
- GPT4Scene — arXiv 2501.01428 ; github.com/Qi-Zhangyang/gpt4scene
- Expansion-run plan & audit: `docs/superpowers/plans/2026-05-20-sqa3d-v2-expansion-run.md` ; `docs/v_classify/AUDIT_sqa3d_v2.md` ; `docs/v_classify/REPORT_sqa3d_expansion1500.md`
