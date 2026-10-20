# M3D-RefSeg — Baseline & Evaluation Design

**Date:** 2026-05-21
**Slice:** `docs/v_classify/m3d-refseg_pilot100.jsonl` — n=100, 100/100 V3=1.0, status=ok
**Role in the agent verification:** the **medical-volume side** of the 4×2 method-family ablation in `docs/Sci3DAgentVerificationPlan.md`.
**Companion docs in this folder:**
- `M3D-RefSeg_evaluation_notes.md` — upstream M3D-LaMed eval walkthrough (preprocess → `[SEG]` → SegVol → soft Dice). Read first if unfamiliar with the task.
- Chinese version: `baseline_and_evaluation.zh.md`.

---

## 1. Task definition (recap)

Input: a 3D CT volume + a natural-language referring expression.
Output: a free-text answer containing a special `[SEG]` token + a 3D binary mask at `32 × 256 × 256` resolution.

The model that produces the mask is necessarily a segmentation-capable VLM (M3D-LaMed-style architecture: VLM emits `[SEG]`; its hidden state is fed through an MLP into a SegVol promptable decoder).

| Field on disk | Meaning |
|---|---|
| `Image` | path to CT NIfTI (e.g. `s0139/ct.nii.gz`) |
| `Mask` | path to multi-label mask NIfTI (e.g. `s0139/mask.nii.gz`) |
| `Mask_ID` | integer label in the mask that this question targets |
| `Question` | referring expression |
| `Answer` | gold textual answer (contains `[SEG]`) |
| `extra.mask_text` | canonical region description used as our text-track judge anchor |

## 2. Eval slice

- **Source:** `docs/v_classify/m3d-refseg_pilot100.jsonl`
- **Size:** n = 100, frozen
- **V-class:** all V3 (pixel-evidence trace — segmentation mask required)
- **Splits:** mixed (77 `train` / 23 `test`); see Risk #1
- **Same items used by all four method families** ⇒ matched-pairs analysis valid (paired t-test / Wilcoxon on per-item scores).
- **Statistical power:** at n=100, 95% CI half-width ±10pp at p=0.5. Adequate for ≥10pp M3-vs-M4 delta; sub-5pp differences not resolvable. Expansion to n=300 or full test split (555) is a follow-up only if results are borderline.

## 3. Method matrix on this dataset

| Method | Implementation | Mask output? | Text output? |
|---|---|---|---|
| **M1 — Mesh-as-text / 3D-as-tokens** | **M3D-LaMed-Phi-3-4B** (HF `GoodBaiBai88/M3D-LaMed-Phi-3-4B`; github.com/BAAI-DCAI/M3D) | ✅ via `[SEG]` + SegVol decoder | ✅ |
| **M2 — Multi-view render + VLM** | **Med-2E3** (slice render + 2D-VLM; github.com/MSIIP/Med-2E3) ; **LLaVA-Med** as the floor (github.com/microsoft/LLaVA-Med) ; **GPT-4o-on-views** as cheap API floor | ❌ no native mask head | ✅ |
| **M3 — Tool-use agent w/o 3D vision** | `sci3d_agent` `mode1_no_vision` over the `.npy_m3d` volume | ❌ | ✅ |
| **M4 — Tool-use agent w/ 3D vision** | `sci3d_agent` `mode3_free_visual`, slice/volume rendering | ❌ | ✅ |

There is **no released agentic with-vision external baseline for medical volumes** — GPT4Scene exists for scenes only. The medical with-vision side rests on our `mode3` + Med-2E3 (asymmetry vs the SQA3D side; explicitly accepted).

## 4. Two-track scoring architecture

Every item is scored on two **independent** tracks:

| Track | Metric | Methods scored | Purpose |
|---|---|---|---|
| **Mask track** | Dice (binary, sigmoid > 0.5, smooth = 1) at 32×256×256 voxel resolution | **M1 only** | Literature-comparable Dice anchor (M3D paper Table). |
| **Text track** | Claude LLM-judge → `0.0 / 0.5 / 1.0` against the `mask_text` anchor | **M1 / M2 / M3 / M4** | Cross-method ablation signal. The 3D-vision-necessity claim (M3 vs M4 delta) lives entirely here. |

**Why no SAM-Med3D bolted onto M2?** Considered and rejected. Letting M2's VLM call SAM-Med3D with a region description would make M2's "mask quality" be SAM-Med3D's mask quality — confounded. The central claim is about the *agent*, not which method best drives an external mask service.

**Why not native-resolution Dice?** Considered and rejected. Native resolution would be scientifically cleaner, but the entire purpose of the mask track is comparability to M3D paper tables. Different resolution = different Dice = not comparable.

### 4.1 Mask track — upstream parity

Preprocessing (port from M3D's `m3d_refseg_data_prepare.py`):

1. Load `ct.nii.gz` via `nibabel`.
2. Resize / crop to `32 × 256 × 256` (D × H × W).
3. MinMax normalize to `[0, 1]`.
4. Load `mask.nii.gz`, resize to same shape with **nearest-neighbor** interpolation (preserves integer labels).
5. Binarize via `gt_binary = (seg == Mask_ID).astype(np.int8)`.

Pred consumption (M1 only):

```python
pred_logits.shape == (32, 256, 256)
pred_binary = (sigmoid(pred_logits) > 0.5).astype(np.int8)
# equivalent / cheaper:
pred_binary = (pred_logits > 0.0).astype(np.int8)
```

Dice formula (verbatim from upstream `Bench/eval/metrics.py`, `smooth = 1`):

```python
num = (pred * target).sum()
den = pred.sum() + target.sum() + 1.0   # smooth=1 in den only
dice = 2.0 * num / den
```

Quirks to replicate (do not silently fix):
- `smooth = 1` is in the **denominator only** — empty-pred + empty-GT → Dice = 0.0 with this formula (numerator 0, denominator 1, result 0). Some textbook variants put `smooth` in both — *this is not what M3D does*.
- Physical voxel spacing is discarded by the resize. Dice is in 32×256×256 voxel space, not mm³.

Per-item: `dice ∈ [0, 1]`. Aggregate: mean Dice over n=100, with std and bootstrap 95% CI. Report empty-empty rate alongside Dice (sanity flag).

### 4.2 Text track — Claude LLM-judge with mask-text anchor

Inputs to the judge for each item:

- `question` — the referring expression
- `gold_answer` — upstream gold (e.g. *"Sure, a postoperative stoma can be seen in the [SEG]."*)
- `mask_text` — canonical region description from `extra.mask_text` (the unambiguous anchor — e.g. *"postoperative stoma, upper abdomen"*)
- `candidate` — free-text answer from M1 / M2 / M3 / M4

Rubric (returns 0 / 0.5 / 1):

| Score | Meaning | Examples |
|---|---|---|
| **1.0** | Names the correct anatomical referent (matches `mask_text`); confident, unambiguous | "Yes, I see a postoperative stoma in the upper abdomen" |
| **0.5** | Partial: right organ wrong subtype, or right region but no specific naming, or hedged | "There appears to be something in the upper abdomen"; "Yes, I see a stoma" (no localization) |
| **0.0** | Wrong referent, denial of presence, or non-answer | "No abnormality"; "I cannot determine"; names a different anatomy |

Judge model: `claude-sonnet-4-6` via Mindra proxy (`ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`). Independent LLM family from:
- The gemini-3.1-pro V-classifier
- Any baseline being scored (M1 = Phi-3 / Llama; M2 = LLaVA / GPT-4o; M3 / M4 = configurable).

Implementation: `sci3d_bench/scorers/refseg_text_judge.py` — extends the `Scorer` Protocol from `base.py`, calls Claude via the existing `llm_client`, parses a tool-call returning `{score, rationale}`. Mirrors the `LLMJudgeScorer` pattern; new file rather than extending the generic one because the prompt is materially different.

## 5. Trajectory analysis (M3 / M4 only)

Every M4 trajectory is post-processed for:

1. **Tool-call rate** — fraction of items where the agent invoked render_view / screenshot / set_camera at least once.
2. **Answer-flip rate (vision-redaction counterfactual)** — re-run the recorded trajectory with returned image bytes redacted (replaced by a blank); measure how often the final answer changes.
3. **Vision-attributable delta** — split M4 accuracy by whether vision was actually invoked; compare each subset against M3 on the same items.

Verdict logic on this dataset (locked up front):
- `Δ_necessary ≈ 0` ⇒ vision **not necessary** for V3 segmentation referring on CT.
- `Δ_necessary > 0` **and** high tool-call + flip rate ⇒ vision **necessary and effective**.
- `Δ_necessary > 0` **but** low tool-call rate ⇒ gain not from vision (confound — investigate scaffold/prompt).

## 6. File layout (target state)

```
sci3d_bench/scorers/
├── mask_dice.py                     [PLAN]   compute_dice + MaskDiceScorer
├── refseg_text_judge.py             [PLAN]   RefSegLLMJudge w/ mask-text anchor
└── nifti_io.py                      [PLAN]   load_ct, load_mask_binary, preprocess_to_32x256x256

sci3d_bench/adapters/
└── (M1 / M2 medical adapters)       [PLAN]   one per family

scripts/m3d_refseg_verify/
├── run_smoke.py                     [PLAN]   3-item synthetic smoke; no live APIs
└── README.md                        [PLAN]
```

Implementation plan: `docs/superpowers/plans/2026-05-21-m3d-refseg-verify-pipeline.md`. 7 TDD-ed tasks covering Dice, NIfTI I/O, scorer wrapper, Claude judge, smoke harness, README. Done criteria: `pytest -m unit tests/sci3d/scorers/` → 13 PASSED; `python3 scripts/m3d_refseg_verify/run_smoke.py` → exit 0 + `SMOKE OK`.

## 7. Done criteria — what "evaluation works on this dataset" means

- [ ] Layer B passes: 13 unit tests + smoke green.
- [ ] M1 adapter (M3D-LaMed-Phi-3-4B) reproduces a spot-check Dice on **3 hand-picked cases** within ±2pp of the paper's per-class number — establishes that the mask-track wiring is faithful before running the full slice.
- [ ] All four method families produce trajectories on the n=100 slice, no fatal failures.
- [ ] Text-track judge returns non-degraded scores on ≥95% of items per method.
- [ ] Mask-track Dice on M1 reported with bootstrap 95% CI and empty-empty rate.
- [ ] Trajectory metrics (tool-call rate, flip rate, vision-attributable delta) computed for M4.
- [ ] Per-method × per-item score table published (matched-pairs analysis input).

## 8. Risks specific to this dataset

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Mixed-split eval slice** (77 train / 23 test) — paper Dice is on test only. | M1 Dice on this slice is NOT directly comparable to the paper headline. Report it separately; the M3-vs-M4 ablation is unaffected (matched within slice). |
| 2 | **`mask_text` quality** — if it embeds gold-answer phrasing, the judge becomes circular. | Spot-check 10 random `mask_text` during impl; if noisy, switch anchor to label only (e.g. `"label 2 = stoma"`). |
| 3 | **Smooth-1 quirk** biases toward correct refusals. | Replicate exactly (parity); report empty-empty rate alongside Dice. |
| 4 | **No volumetric M2 reference for medical** — Med-2E3 + LLaVA-Med are slice-render style; no agentic with-vision external baseline exists. | Accepted; flagged as asymmetry vs SQA3D's GPT4Scene. |
| 5 | **GPU resource for M1** — M3D-LaMed-Phi-3-4B requires 1× A100 40G or equivalent. | Estimate ~4 h on 1× A100 for n=100; verify before running. |
| 6 | **No reproduction gate yet** — verify pipeline checks scorers, not M1 itself. | Adapter plan adds the spot-check gate. |

## 9. References

- M3D / M3D-LaMed — arXiv 2404.00578 (Bai et al. 2024); github.com/BAAI-DCAI/M3D; HF `GoodBaiBai88/M3D-LaMed-Phi-3-4B`
- Med-2E3 — arXiv 2411.12783 (BIBM 2025); github.com/MSIIP/Med-2E3
- LLaVA-Med — github.com/microsoft/LLaVA-Med
- Upstream eval source files (M3D repo): `Bench/eval/eval_seg.py`, `Bench/eval/metrics.py`, `Bench/dataset/multi_dataset.py`, `m3d_refseg_data_prepare.py`
- Companion: `docs/evaluation/M3D-RefSeg/M3D-RefSeg_evaluation_notes.md` (upstream-eval walkthrough)
- Spec: `docs/superpowers/specs/2026-05-21-m3d-refseg-verify-pipeline-design.md`
- Plan: `docs/superpowers/plans/2026-05-21-m3d-refseg-verify-pipeline.md`
- **Pilot100 API baseline measurement:** [`baseline_pilot100_report.md`](./baseline_pilot100_report.md) (2026-05-21)

---

## 10. Two-stage baseline: VLM-parser + segmentation-decoder (V2 redesign)

**This section is a full rewrite of §10.** Earlier R0–R4 routes (with their "VLM directly emits mask vs. swap-out-the-VLM" forks) collapse into a single clean framework: **the VLM only ever does target parsing; the mask is always produced by a dedicated segmentation model**. The bridge (translation from VLM JSON to decoder prompt) is the variable.

### 10.1 Motivation: pilot100 shows API VLMs cannot directly produce masks

Pilot100 ([`baseline_pilot100_report.md`](./baseline_pilot100_report.md)) measurements:

| Baseline route | mean soft Dice | median Dice | n_ok / n | Notes |
|---|---|---|---|---|
| Claude-Sonnet-4-6 + box-backproject | **0.016** | 0.000 | 100/100 | API only, box-mask ceiling |
| Gemini-3.1-Pro-Preview + box-backproject | **0.020** | 0.000 | 92/100 | API only, box-mask ceiling |
| M3D-LaMed-Phi-3-4B (paper, full test) | **≈ 0.40+** | n/a | n/a | end-to-end segmentation-capable; target reference |

The 20× gap is **structural, not model quality** — API VLMs have no mask-decoder head, and forcing them to "draw a bbox on a 2D PNG" is asking an internist to draw a mask with a crayon. Two clean fixes:
- **A. Bolt an external decoder onto the VLM (this section, V0–V3)** — VLM does language understanding; decoder produces voxel-precise mask.
- **B. Use an end-to-end segmentation-capable VLM (§3 M1, V4)** — both jobs in one model.

A and B **run side-by-side** as controls (parser+decoder plug-in vs. joint-trained model).

### 10.2 Pipeline (two stages)

```
[CT volume + question]
         │
         ▼
┌─────────────────────────────────────────────┐
│ Stage 1: VLM parser (Claude / Gemini)       │
│                                             │
│  Input: question + K axial slice PNGs       │
│         (K = 7 in pilot100, 16 in plan)     │
│  Output (JSON):                             │
│    { "target":        { organ, lesion,      │
│                         sub_region,         │
│                         modifiers,          │
│                         free_text },        │
│      "spatial_hints": { bbox_per_slice,     │
│                         z_range_hint },     │
│      "answer":        "...[SEG]..." }       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ Stage 2: Translation layer (deterministic)  │
│                                             │
│  VLM JSON → prompt for chosen decoder       │
│   ├─ SegVol     ← target.free_text          │
│   ├─ MedSAM 2   ← spatial_hints.bbox_*      │
│   ├─ SAM 3      ← bbox or text              │
│   └─ nnU-Net    ← target.organ → checkpoint │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ Stage 3: Segmentation decoder               │
│   Predicted 3D mask  (D, H, W)              │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ Stage 4: Eval (UNCHANGED from pilot100)     │
│   Soft Dice vs (mask == Mask_ID) + 3-panel  │
│   viz                                       │
└─────────────────────────────────────────────┘
```

**Key design principles:**

1. **One VLM call feeds many decoders.** A single API call per case (pilot100 measured ≈ 0.05–0.10 USD / case) produces a JSON that can be replayed against N decoders. **API call and decoder eval are decoupled.**
2. **Translation layer is deterministic code — no API, no ML.** It rewrites JSON into the prompt format each decoder expects. `tests/baseline/test_translate.py` covers every translation rule.
3. **Decoders are plug-ins** behind a `Protocol` interface (`predict(volume, prompt) -> (D,H,W) prob`); SegVol / MedSAM 2 / SAM 3 / nnU-Net are interchangeable.
4. **Stage 4 is unchanged.** Soft Dice (§6.1), backprojection (§6.3, V0 only), and 3-panel viz (§10) from [`baseline_pilot100_report.md`](./baseline_pilot100_report.md) are all reused.

### 10.3 Stage 1: VLM as target parser

**New prompt schema:**

```
System: You are an expert radiologist analyzing axial CT slices.
        You will receive K slices ordered superior→inferior at fixed depth
        percentiles. Identify the target referred to by the question and
        produce a structured description that a downstream segmentation
        model can consume. Coordinates are pixels of the 256x256 image,
        origin top-left.

User:   [K PNG image content blocks]
        Question: <referring expression>

        Return JSON only:
        {
          "target": {
            "organ":       "<canonical, e.g. 'liver', 'left kidney'>",
            "lesion_type": "<pathology | null, e.g. 'cyst', 'tumor'>",
            "sub_region":  "<refiner | null, e.g. 'segment IV'>",
            "modifiers":   ["<visual cues, e.g. 'hypodense'>"],
            "free_text":   "<ready-to-use text prompt, e.g. 'small hepatic cyst'>"
          },
          "spatial_hints": {
            "bbox_per_slice": [ [x1,y1,x2,y2] | null, ...  // K entries ],
            "z_range_hint":   [z_min_frac, z_max_frac] | null
          },
          "answer": "<one-sentence answer containing literal [SEG]>"
        }
```

**Why structured JSON:**

- Different decoders downstream expect different prompt types (text / bbox / organ name). Structured JSON makes the translation layer **writable, testable, and tunable**.
- `bbox_per_slice` keeps full compatibility with pilot100 / V0 — V0 only reads this field.
- `free_text` gives text-promptable decoders (SegVol, SAM 3) a ready-to-use prompt, so the translation layer doesn't have to assemble strings ad hoc.
- `target.organ` lets organ-specific decoders (nnU-Net / TotalSegmentator) look up checkpoints directly.

**Token cost:**

- 7-slice (pilot100 measured): Claude ≈ 1.2K prompt tokens / case; Gemini ≈ 8K / case.
- 16-slice (planned): roughly linear (≈ 2.7K / 18K). Claude still uses <1% of 128K context; Gemini needs a timeout retest.

### 10.4 Stage 2 / Stage 3: Decoder backends

| Decoder | Native prompt | 3D handling | Medical-trained | GPU | Reference |
|---|---|---|---|---|---|
| **SegVol** | text / point / bbox | native 3D (96³ patch + sliding window) | ✅ | 24G | Du et al. 2024, [BAAI-DCAI/SegVol](https://github.com/BAAI-DCAI/SegVol) |
| **MedSAM 2** | bbox / point | 2D + video propagator | ✅ | 12G | Ma et al. 2024, [bowang-lab/MedSAM2](https://github.com/bowang-lab/MedSAM2) |
| **SAM 3** | bbox / point / text | 2D (needs wrapper for 3D) | ❌ (general) | 12–24G | Meta 2025/2026 |
| **TotalSegmentator / nnU-Net** | organ name → checkpoint | native 3D | ✅ | 12G | Wasserthal et al. 2023, 117 classes |
| **M3D-LaMed** (reference) | end-to-end (no Stage 1) | native 3D | ✅ | 40G | §10.5 V4 |

**Why SegVol is P0:** native text-promptable + native 3D + medical-trained. Lowest-friction match for Stage 1's `free_text` field, with **one model covering most tissue + lesion combinations**.
**Why MedSAM 2 is P0:** native bbox-promptable. Reuses V0's `bbox_per_slice` directly — **one line of `reconstruct(...)` change is the full V0 → V1 path**.

### 10.5 Method matrix V0–V4

| ID | Stage 1 (VLM output field) | Bridge | Stage 2 decoder | Mask source | Expected mean Dice | Priority |
|---|---|---|---|---|---|---|
| **V0** (done) | `bbox_per_slice` | 2D bbox | none — `boxes_to_mask3d` (fill rect + z-dilate) | box-mask | **0.016 / 0.020** (measured) | done |
| **V1-MedSAM2** | `bbox_per_slice` (reuse V0 API call) | 2D bbox + slice idx | **MedSAM 2** image + video predictor | 2D mask per slice + z propagate | 0.10–0.20 | **P0** |
| **V2-SegVol** | `target.free_text` | category text | **SegVol** text-prompted | native 3D probability mask | 0.20–0.35 | **P0** |
| **V2-SegVol+bbox** | `free_text` + `bbox_per_slice` → 3D bbox | text + 3D bbox | **SegVol** dual prompt | native 3D probability mask | 0.25–0.40 | P1 |
| **V3-nnUNet** | `target.organ` | organ → checkpoint URL | **TotalSegmentator** 117-class | trained 3D mask (whole organ) | organ-level 0.7+; lesion-level limited | P1 |
| **V1-SAM3** | `bbox_per_slice` or `free_text` | 2D bbox / text | **SAM 3** local HTTP wrap | 2D mask + propagate | unknown (no medical zero-shot numbers) | P2 |
| **V4-end2end** | — | — | **M3D-LaMed-Phi-3-4B** end-to-end | `[SEG]` token + SegVol decoder | paper ≈ 0.40+ | **P0** (reference) |

**P0 cost-comparable trio:**

- **V0** (have): API-only ceiling
- **V1-MedSAM 2**: API + 2D mask decoder (local 12G GPU)
- **V2-SegVol**: API + native-3D text-promptable decoder (local 24G GPU)
- **V4-end2end**: only route directly comparable to paper numbers (local 40G GPU)

Running these together separates three contributions quantitatively:

| Comparison | Isolated factor |
|---|---|
| **V1 − V0** | Contribution of a 2D mask decoder given a VLM bbox prompt |
| **V2 − V0** | Contribution of a native-3D text-promptable decoder given a VLM language prompt |
| **V2 − V1** | Whether the bridge that wins is "text prompt" or "bbox prompt" |
| **V4 − V2** | Gap between end-to-end joint training and parser+decoder plug-in |

This control set covers the central question: *"what is the VLM's real role in segmentation."*

### 10.6 Stage 2 translation rules

**SegVol (V2):**

```python
def vlm_to_segvol(vlm: VLMOutput) -> SegVolPrompt:
    t = vlm.target
    parts = [t.organ]
    if t.lesion_type: parts = [t.lesion_type, "in", t.organ]
    if t.sub_region:  parts += [",", t.sub_region]
    return SegVolPrompt(text=" ".join(parts))   # e.g. "cyst in liver, segment IV"
# SegVol call: segvol.predict(volume=ct_dhw, text=prompt) → (D,H,W) prob mask
```

**MedSAM 2 (V1):**

```python
def vlm_to_medsam2(vlm: VLMOutput, z_indices: list[int]) -> list[Sam2BoxPrompt]:
    prompts = []
    for slice_idx, bbox in enumerate(vlm.spatial_hints.bbox_per_slice):
        if bbox is None: continue
        prompts.append(Sam2BoxPrompt(z=z_indices[slice_idx], bbox=bbox))
    return prompts
# Then: image_predictor for each prompt → mask_2d; video_predictor.propagate(...) → mask_3d
```

**TotalSegmentator / nnU-Net (V3):**

```python
def vlm_to_totalseg(vlm: VLMOutput) -> str | None:
    organ = canonicalize_organ(vlm.target.organ)   # 'liver' → 'liver'
    if organ not in TOTALSEG_CLASSES: return None  # decoder cannot help; record as 'unsupported'
    return organ
# Then: totalseg.predict(volume=ct_dhw, classes=[organ]) → (D,H,W) mask
# NOTE: covers whole organ, not lesion within it — V3 is mostly useful when answer = whole organ
```

Every rule gets a unit test in `tests/baseline/test_translate.py` (VLM-JSON example → expected prompt). **No live API, no decoder weights** required.

### 10.7 Stage 4 is unchanged (already implemented)

Soft Dice (§6.1), bbox backprojection (§6.3, V0 only — other V's consume decoder mask directly, skipping backprojection), 3-panel viz (§10) from [`baseline_pilot100_report.md`](./baseline_pilot100_report.md) are **all reused**.

Only new wiring: when the decoder emits `pred_prob ∈ [0,1]` (V2 SegVol) we **consume the probability directly** in the soft-Dice formula, parallel to the `sigmoid > 0.5` path in §4.1. The Dice formula (`2 × Σ(pred_prob · gt) / (Σpred_prob + Σgt + 1)`) is valid for both hard masks and soft probabilities.

### 10.8 Prerequisites & roadmap

```
Now        ─── V0 done (pilot100 measured)
Day 1-2    ─── V1-MedSAM 2 landed (reuse V0 API output cache)
Day 3-5    ─── V2-SegVol landed (weight download, text-prompt validation)
Day 6-8    ─── V4-M3D-LaMed adapter (shared spot-check gate with §3 M1)
Day 9-10   ─── V3-TotalSegmentator + full baseline comparison table
Day 11+    ─── V1-SAM 3 exploration / denser axial sampling ablation
```

**Prerequisites:**

- [ ] Local GPU ≥ 12G (V1-MedSAM 2), ≥ 24G (V2-SegVol), ≥ 40G (V4-M3D-LaMed) — see `memory:h20_download_tooling` and `memory:project_sciencevision_server`
- [ ] SegVol / MedSAM 2 / M3D-LaMed / TotalSegmentator weights downloadable (some require HF token)
- [ ] Re-run pilot100 with 7→16 slice density to give Stage 1 a sensible z-density baseline
- [ ] Write `tests/baseline/test_translate.py` (≥ 6 cases covering each translation rule)

### 10.9 Relationship to §3 method matrix

§3's M1–M4 matrix is **the target state for the 4-method ablation inside `sci3d_bench`**; this §10 V-series is **the implementation route for mask production**. The two matrices are orthogonal:

| §3 method | §10 V-series counterpart |
|---|---|
| **M1** (M3D-LaMed end-to-end) | **V4** — directly paper-comparable |
| **M2** (Med-2E3 / GPT-4o-on-views / Claude / Gemini) | **V0** = M2's box-only implementation; **V1 / V2** = M2 attached to an external decoder |
| **M3** (vision-less agent) | orthogonal (agent does not call segmentation tools) |
| **M4** (with-vision agent + tool-use) | **V1 / V2** are M4's "agent calls segmentation tool" implementation |

In other words: **§10 V-series defines *how* a VLM/agent calls a segmentation tool; §3 defines *how* the agent is ablated inside `sci3d_bench`.** Run together, they answer both *"is vision necessary inside the agent"* and *"which decoder works best when an agent calls a segmentation tool."*

In the next verify-pipeline plan iteration, V1-MedSAM 2 / V2-SegVol / V4-M3D-LaMed adapters fold into `docs/superpowers/plans/2026-05-21-m3d-refseg-verify-pipeline.md` as the concrete segmentation backends for §3 M1 / M2 / M4.
