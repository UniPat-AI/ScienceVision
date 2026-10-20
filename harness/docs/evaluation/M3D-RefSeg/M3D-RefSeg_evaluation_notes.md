# M3D-RefSeg Evaluation Notes

## 1. Task definition

**M3D-RefSeg** is a 3D medical referring expression segmentation task.

The model receives:

```text
3D medical image + natural language question / referring expression
```

The expected model behavior is:

```text
1. Understand which anatomical region or lesion the question refers to.
2. Generate a textual answer containing the special token [SEG].
3. Generate a 3D predicted segmentation mask corresponding to [SEG].
```

In simple terms:

```text
Input:  CT image + “Please segment the abnormal lesion in the uterine cavity.”
Output: Text answer + 3D mask of the referred lesion
```

---

## 2. Dataset fields

A typical M3D-RefSeg sample contains fields like:

| Field | Meaning |
|---|---|
| `Image` | Path to the 3D medical image, for example `s0139/ct.nii.gz` |
| `Mask` | Path to the corresponding segmentation annotation, for example `s0139/mask.nii.gz` |
| `Mask_ID` | The label ID inside the mask that corresponds to the current question |
| `Question_Type` | Type/category of the question |
| `Question` | Natural language referring expression |
| `Answer` | Ground-truth textual answer, usually containing `[SEG]` |

Example:

```text
Image:    s0139/ct.nii.gz
Mask:     s0139/mask.nii.gz
Mask_ID:  1
Question: Where do the calcification foci appear in the image?
Answer:   The calcification foci are present within the aortic wall, specifically at [SEG].
```

---

## 3. What is `[SEG]`?

`[SEG]` is a special segmentation token.

It is **not** the mask itself. It is a signal that tells the model:

```text
Generate a segmentation mask for the region referred to here.
```

For example, the textual answer may be:

```text
The calcification foci are present within the aortic wall, specifically at [SEG].
```

The role of `[SEG]` is:

```text
[SEG] token hidden state
        ↓
segmentation decoder / mask decoder
        ↓
predicted 3D mask
```

So the Dice score is not computed on the text token `[SEG]`. Dice is computed between:

```text
predicted mask generated through [SEG]
vs
binary ground-truth mask extracted using Mask_ID
```

---

## 4. Input during evaluation

For each evaluation sample, the model receives two main inputs.

### 4.1 Image input

The image is usually a preprocessed 3D volume.

Typical shape after preprocessing:

```text
[1, 32, 256, 256]
```

Meaning:

```text
1 channel × 32 slices × 256 height × 256 width
```

### 4.2 Text input

The text input is the question, usually prefixed with image patch tokens.

Conceptually:

```text
<im_patch><im_patch>...<im_patch> Where do the calcification foci appear in the image?
```

The image patch tokens represent visual features and connect the image encoder to the language model.

---

## 5. Model output during evaluation

A segmentation-capable VLM such as M3D-LaMed produces two types of output.

### 5.1 Text output

Example:

```text
The calcification foci are present within the aortic wall, specifically at [SEG].
```

This text tells us the model understood the referred region and emitted the `[SEG]` token.

### 5.2 Mask output

The model also outputs segmentation logits:

```python
seg_logit.shape = [1, 1, 32, 256, 256]
```

This is not yet a binary mask. It is a raw score for every voxel.

To convert logits to probabilities:

```python
pred_prob = sigmoid(seg_logit)
```

Then `pred_prob` has the same spatial shape:

```python
pred_prob.shape = [1, 1, 32, 256, 256]
```

Each voxel value is between 0 and 1:

```text
0.0 means very unlikely to belong to the target region
1.0 means very likely to belong to the target region
```

For visualization, it can be thresholded:

```python
pred_mask = pred_prob > 0.5
```

This gives a binary predicted mask:

```text
1 = predicted target voxel
0 = background / non-target voxel
```

---

## 6. Ground-truth mask handling

This is one of the most important parts.

The file `s0139/mask.nii.gz` is usually not a simple binary mask. It can be a **multi-label mask**.

For example:

```text
0 = background
1 = aortic wall calcification
2 = another lesion / organ
3 = another anatomical structure
```

If the current sample has:

```text
Mask_ID = 1
```

then the evaluation target is only label `1`.

So the ground-truth binary mask is extracted as:

```python
gt_mask = (mask == Mask_ID)
```

For this sample:

```python
gt_mask = (mask == 1)
```

That means:

```text
voxels where original mask equals 1 -> 1
all other voxels -> 0
```

Small toy example:

```text
Original mask:
[0, 0, 1, 1, 0, 2, 2, 0]

Mask_ID = 1

Binary GT mask:
[0, 0, 1, 1, 0, 0, 0, 0]
```

If `Mask_ID = 2`, then:

```text
Original mask:
[0, 0, 1, 1, 0, 2, 2, 0]

Mask_ID = 2

Binary GT mask:
[0, 0, 0, 0, 0, 1, 1, 0]
```

So `Mask_ID` tells the evaluator which region inside the multi-label mask corresponds to the current question.

---

## 7. Dice metric

Dice measures how much the predicted mask overlaps with the ground-truth mask.

The intuitive binary Dice formula is:

```text
Dice = 2 × intersection(prediction, ground truth) / (prediction size + ground truth size)
```

Equivalently:

```text
Dice = 2TP / (2TP + FP + FN)
```

Where:

| Term | Meaning |
|---|---|
| TP | Predicted as target and also target in GT |
| FP | Predicted as target but background in GT |
| FN | Missed target voxel |

Dice ranges from 0 to 1:

```text
1 = perfect overlap
0 = no overlap
```

---

## 8. Soft Dice used during M3D evaluation

In the official-style evaluation, Dice is often computed using the probability mask instead of a thresholded binary mask.

That is:

```python
pred_prob = sigmoid(seg_logit)
```

Then:

```python
dice = 2 * sum(pred_prob * gt_mask) / (sum(pred_prob) + sum(gt_mask) + smooth)
```

Here:

```text
pred_prob = model predicted probability mask
gt_mask   = binary ground-truth mask extracted by Mask_ID
smooth    = small stabilizing constant, often 1
```

Important distinction:

```text
Thresholded pred_mask is mainly useful for visualization.
Soft pred_prob is commonly used for Dice computation.
```

---

## 9. Does Dice computation need the decoder model?

No.

There are two separate stages.

### Stage A: Generate predicted mask

This stage needs the model and segmentation decoder.

```text
image + question
      ↓
VLM + segmentation decoder
      ↓
seg_logit / predicted mask
```

Without a trained segmentation-capable model, a normal VLM can usually only produce text, not a voxel-level 3D mask.

### Stage B: Compute Dice

This stage does not need the model anymore.

It only needs:

```text
1. predicted mask or predicted probability mask
2. ground-truth binary mask
```

Then Dice is a direct mathematical calculation.

```text
predicted mask + GT mask -> Dice score
```

### Stage C: Visualization

Visualization also does not need the decoder model.

It only needs the already-generated mask:

```python
pred_mask = pred_prob > 0.5
```

Then save or display it as:

```text
pred.nii.gz
```

---

## 10. Complete evaluation flow

For one M3D-RefSeg sample:

```text
Input:
- 3D CT image
- natural language question

Ground truth:
- multi-label mask file
- Mask_ID

Model output:
- text answer containing [SEG]
- seg_logit, a 3D mask logit tensor

Evaluation:
- convert seg_logit to pred_prob using sigmoid
- extract GT binary mask using mask == Mask_ID
- compute Dice between pred_prob and GT binary mask
```

Expanded pipeline:

```text
1. Load image
2. Load multi-label mask
3. Use Mask_ID to extract binary GT mask
4. Feed image + question into model
5. Model generates text answer and seg_logit
6. Convert seg_logit to pred_prob with sigmoid
7. Compute Dice(pred_prob, gt_mask)
8. Optionally threshold pred_prob > 0.5 for visualization
9. Save image, GT mask, predicted mask, and text output
```

---

## 11. Example case: aortic wall calcification

Sample:

```text
Image:    s0139/ct.nii.gz
Mask:     s0139/mask.nii.gz
Mask_ID:  1
Question: Where do the calcification foci appear in the image?
Answer:   The calcification foci are present within the aortic wall, specifically at [SEG].
```

### Step 1: Load image

```python
image = load_image("s0139/ct.nii.gz")
```

After preprocessing:

```python
image.shape = [1, 32, 256, 256]
```

### Step 2: Load mask and extract GT

```python
mask = load_mask("s0139/mask.nii.gz")
gt_mask = (mask == 1)
```

Because:

```text
Mask_ID = 1
```

### Step 3: Run model

```python
generation, seg_logit = model.generate(
    image=image,
    input_ids=input_ids,
    seg_enable=True,
    max_new_tokens=256,
    do_sample=False,
)
```

### Step 4: Decode text

```python
pred_text = tokenizer.decode(generation[0], skip_special_tokens=True)
```

Possible output:

```text
The calcification foci are present within the aortic wall, specifically at [SEG].
```

### Step 5: Convert mask logits to probabilities

```python
pred_prob = sigmoid(seg_logit)
```

Shape:

```python
pred_prob.shape = [1, 1, 32, 256, 256]
```

### Step 6: Compute Dice

```python
dice = 2 * sum(pred_prob * gt_mask) / (sum(pred_prob) + sum(gt_mask) + 1)
```

This Dice score measures how much the predicted aortic wall calcification mask overlaps with the GT region where `mask == 1`.

---

## 12. Toy Dice example

Suppose there are only 8 voxels.

Ground truth:

```text
gt_mask = [0, 1, 1, 1, 0, 0, 0, 1]
```

Model probability output:

```text
pred_prob = [0.05, 0.90, 0.80, 0.20, 0.10, 0.05, 0.60, 0.70]
```

Overlap term:

```text
pred_prob * gt_mask
= [0, 0.90, 0.80, 0.20, 0, 0, 0, 0.70]
```

So:

```text
sum(pred_prob * gt_mask) = 2.60
sum(pred_prob) = 3.40
sum(gt_mask) = 4
```

Without smooth, Dice is:

```text
Dice = 2 * 2.60 / (3.40 + 4)
     = 5.20 / 7.40
     = 0.703
```

For visualization:

```python
pred_mask = pred_prob > 0.5
```

Then:

```text
pred_mask = [0, 1, 1, 0, 0, 0, 1, 1]
```

But the soft Dice calculation uses `pred_prob`, not necessarily the thresholded `pred_mask`.

---

## 13. What model is needed to produce predicted mask?

A normal text-only LLM or ordinary VLM cannot reliably produce a 3D voxel-level mask by itself.

To produce `predicted mask`, one needs a segmentation-capable model, for example:

| Model type | Can output mask? | Notes |
|---|---:|---|
| Text-only LLM | No | Can only generate text |
| Ordinary VLM | Usually no | Can describe image but usually cannot output voxel masks |
| SAM / SAM2 / SAM3 | Yes for general image/video segmentation | Mainly general 2D/image/video setting |
| MedSAM / MedSAM2 | Yes | Medical image/video segmentation setting |
| SegVol | Yes | 3D medical segmentation foundation model style |
| M3D-LaMed | Yes | VLM plus segmentation module; can use `[SEG]` to generate mask |

For M3D-RefSeg, the closest setup is:

```text
VLM understands the question
[SEG] token identifies the referred target
segmentation decoder generates the 3D mask
```

---

## 14. Minimal pseudo-code

```python
# 1. Load sample
image = load_image(sample["Image"])
mask = load_mask(sample["Mask"])
mask_id = sample["Mask_ID"]
question = sample["Question"]

# 2. Build binary ground truth mask
gt_mask = (mask == mask_id).astype("float32")

# 3. Tokenize question and run model
input_ids = tokenizer(question, return_tensors="pt")["input_ids"]

generation, seg_logit = model.generate(
    image=image,
    input_ids=input_ids,
    seg_enable=True,
    max_new_tokens=256,
    do_sample=False,
)

# 4. Decode text output
pred_text = tokenizer.decode(generation[0], skip_special_tokens=True)

# 5. Convert logits to probability mask
pred_prob = torch.sigmoid(seg_logit)

# 6. Compute Dice
smooth = 1.0
dice = 2 * torch.sum(pred_prob * gt_mask) / (
    torch.sum(pred_prob) + torch.sum(gt_mask) + smooth
)

# 7. Optional visualization mask
pred_mask = (pred_prob > 0.5).float()
```

---

## 15. One-sentence summary

M3D-RefSeg evaluation feeds a 3D image and natural language referring expression into a segmentation-capable VLM, obtains a text answer plus a `[SEG]`-triggered predicted 3D mask, extracts the binary ground-truth mask using `Mask_ID`, and computes Dice between the predicted probability mask and the extracted GT mask.

---

## 16. Two-stage VLM-parser + segmentation-decoder baseline (this work)

Sections §1–§15 describe the **upstream M3D-LaMed end-to-end design**: one model owns both the language side (emits `[SEG]`) and the mask side (the `[SEG]` hidden state drives a SegVol decoder inside the same forward pass).

In this repo's pilot100 baseline we **do not** start from an end-to-end segmentation-capable VLM. We start from off-the-shelf API VLMs (Claude / Gemini) that **have no mask-decoder head**. Running them with the upstream evaluation protocol is what produced the 0.016 / 0.020 mean Dice in [`baseline_pilot100_report.md`](./baseline_pilot100_report.md) — a 20× gap vs. the paper that is structural, not model-quality.

The fix (see [`baseline_and_evaluation.md`](./baseline_and_evaluation.md) §10 for the full V0–V4 method matrix) is to split the upstream end-to-end model along its natural seam:

```
  Upstream (M3D-LaMed end-to-end, §1–§15)
  ──────────────────────────────────────────────────────────────
  CT volume + question ──► [ VLM body + [SEG] token + SegVol decoder ] ──► text + mask
                                          (one forward pass)

  Two-stage baseline (this work, §16 here / §10 in baseline_and_evaluation.md)
  ──────────────────────────────────────────────────────────────
  CT volume + question
    │                                                  ┌────────────────────┐
    ▼                                                  │  Stage 3:          │
  Stage 1: API VLM parser   ──► Stage 2: deterministic │  SegVol /          │
  (Claude / Gemini)              translation layer ──► │  MedSAM 2 /        │ ──► mask
  emits JSON                     (no API, no ML)       │  TotalSegmentator  │
  { target.{organ,lesion,...},                         │  / nnU-Net         │
    spatial_hints.{bbox,z},                            └────────────────────┘
    answer:"...[SEG]..." }
```

### 16.1 How this differs from the upstream protocol

| Aspect | Upstream (§1–§15) | Two-stage (this work) |
|---|---|---|
| **`[SEG]` semantics** | Triggers a hidden-state vector that the in-model SegVol decoder consumes. | Cosmetic — kept only in `answer` for text-track judge parity. The mask never depends on `[SEG]`. |
| **Who emits the mask** | The VLM itself (jointly trained mask head). | An external decoder, conditioned on a prompt translated from the VLM's structured JSON. |
| **VLM job** | Language understanding **and** mask prediction. | Language understanding only — identify the referent and produce a target description. |
| **Mask prompt format** | `[SEG]` hidden-state vector (learned). | Hand-shaped: `text` for SegVol, `bbox_per_slice` for MedSAM 2, `organ` for TotalSegmentator. |
| **Voxel-space resolution** | 32 × 256 × 256 (locked by paper Table). | Same for soft-Dice comparability; decoder's native output is resampled. |
| **Soft Dice formula (§13)** | unchanged — `2·Σ(p·g) / (Σp + Σg + 1)`, smooth=1 in denominator only. | unchanged — used identically on the external decoder's `pred_prob`. |
| **`Mask_ID` selection (§9)** | unchanged. | unchanged. |
| **Preprocessing (§7)** | unchanged. | unchanged for the volume side; Stage 1 additionally renders K axial slice PNGs (K=7 in pilot100, K=16 planned). |

In short: **§1–§15 still describe the eval (Stage 4) verbatim**. The two-stage redesign replaces only Stages 1–3 of the *mask production*, leaving the metric, the dataset reader, and the `Mask_ID` selection identical to upstream.

### 16.2 Why this split is the right one

Three reasons:

1. **API VLMs do not have a mask head** — the structural fix is to give them one externally, not to ask the VLM to "draw" a mask in token space.
2. **The bridge between language and mask is now inspectable.** Stage 1's JSON output is human-readable; Stage 2 is deterministic Python that can be unit-tested without any GPU. If the mask is wrong, we can tell whether Stage 1 misnamed the organ, Stage 2 mistranslated, or Stage 3 segmented poorly — three independent failure modes that are entangled in the end-to-end design.
3. **It is a fair benchmark for the agent question.** §1–§15 evaluate "does this *one* model do segmentation referring well." With Stages 1–3 split apart, we evaluate the orthogonal question: "does a *general* VLM, when paired with the right segmentation tool, recover the gap to a joint-trained model?" That is the question `Sci3DAgentVerificationPlan.md` is actually asking.

### 16.3 Reading order

For anyone joining this work:

1. **Start here, §1–§15** — understand what M3D-RefSeg is and how upstream evaluates it.
2. **Then [`baseline_pilot100_report.md`](./baseline_pilot100_report.md)** — see the V0 measurements that motivated the redesign (Claude 0.016 / Gemini 0.020 mean Dice; viz artifacts; bbox-mask ceiling).
3. **Then [`baseline_and_evaluation.md`](./baseline_and_evaluation.md) §10 (or [`baseline_and_evaluation.zh.md`](./baseline_and_evaluation.zh.md) §10)** — V0–V4 method matrix, decoder backend table, translation rules, prerequisites & roadmap.
4. **Finally [`docs/Sci3DAgentVerificationPlan.md`](../../Sci3DAgentVerificationPlan.md)** — how M1 / M2 / M3 / M4 inside `sci3d_bench` map onto V0 / V1 / V2 / V4 here.
