# Quickstart

> **Goal: in ≤30 minutes, run a 3D-grounded QA demo end-to-end on your
> machine — no UniPat engineer in the loop.**

> Status: **stage 1 (offline)**. The full end-to-end loop with the live
> MCP server, URL-parameterized viewer, and runtime QA generation is
> stage 2. Stage 1 exercises the offline-scoring shape so you can
> validate the "executable ground truth" pattern before wiring the rest.

---

## Pick a demo

| Demo | When to pick | Path |
|---|---|---|
| **Medical Spatial (offline)** ✓ stage 1 ready | Any partner. CT volume + 4-class mask + 1 QA + offline scorer. ~5 min on a laptop. | [`examples/02_medical_spatial_gt/`](../../examples/02_medical_spatial_gt/) |
| **Genesis_VP_Hard** (4 physics tasks) — stage 2 | Embodied / physical reasoning background; force, MPM, FEM, cloth. | [`examples/01_genesis_vp_quickstart/`](../../examples/01_genesis_vp_quickstart/) (placeholder, not yet runnable) |

The rest of this page walks the Medical Spatial demo top-to-bottom.

---

## Step 1 — install

```bash
git clone https://github.com/UniPat-AI/BabyVision-v2.git
cd BabyVision-v2
pip install -e ".[demo]"
```

Requires Python ≥3.10 and ~10 MB free disk for the sample.

## Step 2 — fetch the demo sample

If you don't already have a ScienceVision checkout next door, clone one (the default-clone name `Science-Vision` works too):

```bash
git clone https://github.com/SueMarsR/Science-Vision.git ../../../../ScienceVision
```

```bash
cd examples/02_medical_spatial_gt
bash data/fetch.sh
```

This expects a sibling [`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision)
checkout at `../../../../ScienceVision` (i.e. next to your `BabyVision-v2`
clone). Override with:

```bash
SCIENCEVISION_DIR=/path/to/ScienceVision bash data/fetch.sh
```

Expected: `data/image.nii.gz` (~6 MB) and `data/mask.nii.gz` (~100 KB).

> Stage 1.5 will publish these two files to a public HuggingFace dataset
> so the sibling-checkout step goes away. Tracked at the top of `data/fetch.sh`.

## Step 3 — inspect the volume (optional)

Open `data/image.nii.gz` in any NIfTI viewer (ITK-SNAP, 3D Slicer, FSL,
`niivue`). The mask labels are:

| Label | Structure |
|---|---|
| 1 | Renal vein |
| 2 | Kidney |
| 3 | Renal artery |
| 4 | Tumor |

This step is *recommended* but not required — the scorer doesn't depend
on it.

## Step 4 — write your answer

The single demo question is in [`qa.json`](../../examples/02_medical_spatial_gt/qa.json):

> "This kidney CT volume comes with a 4-class segmentation mask
> (1=Renal vein, 2=Kidney, 3=Renal artery, 4=Tumor). Which class occupies
> the LARGEST number of voxels? Answer in the form `\boxed{<class name>}`."

Write your answer to a text file:

```bash
printf '%s\n' '\boxed{Kidney}' > my_answer.txt
```

## Step 5 — run the offline scorer

```bash
python score.py --prediction my_answer.txt
```

Expected output:

```json
{
  "expected": "Kidney",
  "predicted": "Kidney",
  "match": true
}
```

Exit code is `0` on match, `1` on mismatch, `2` on setup errors (missing
prediction file or missing mask). This makes it CI-friendly:

```bash
python score.py --prediction my_answer.txt > /dev/null && echo PASS || echo FAIL
```

## Step 6 — what's next

- **Read the eval protocol** — [`docs/benchmark/evaluation-protocol.md`](../benchmark/evaluation-protocol.md)
  explains the layered defense against LLM-as-judge that this offline
  scorer is one example of.
- **Submit to the leaderboard** — once you've run your model against the
  Medical Spatial task family, package the result per
  [`docs/benchmark/submission-guide.md`](../benchmark/submission-guide.md)
  and open a PR.
- **Add your own domain** — follow [`new-domain-template.md`](new-domain-template.md).
- **Stage 2 (live MCP + viewer)** is in progress; track in
  [`examples/01_genesis_vp_quickstart/`](../../examples/01_genesis_vp_quickstart/).

---

## Troubleshooting

- **`fetch.sh` says source files not found** — your ScienceVision checkout
  isn't at `../../../../ScienceVision`. Either clone it there, or set
  `SCIENCEVISION_DIR=/path/to/ScienceVision`.
- **`ModuleNotFoundError: No module named 'nibabel'`** — you ran the
  scorer without the `demo` extras. `pip install -e ".[demo]"` first.
- **`predicted: null`** — your `my_answer.txt` must contain the literal
  `\boxed{...}` token, not just plain text. The scorer extracts via
  regex.
- **Match is false but the answer is right** — case-sensitive. `\boxed{kidney}` ≠ `\boxed{Kidney}`. Match the spelling in
  the `LABEL_NAMES` table above exactly.
