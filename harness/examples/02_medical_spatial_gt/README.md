# `examples/02_medical_spatial_gt/`

End-to-end **stage-1 (offline)** demo for the Medical Spatial task family:
one kidney-CT volume with a 4-class segmentation mask, one question, one
offline scorer that derives ground truth from the mask itself.

> Stage 1 deliberately skips the live MCP server + viewer loop. It exists
> so partners can reproduce the "executable ground truth" shape of a
> BabyVision-v2 task in under 10 minutes on a laptop. Stage 2 will add
> the live `sci3d_agent` MCP server and the URL-parameterized viewer.

## Contents

| File | Purpose |
|---|---|
| `qa.json` | The single QA item (question + ground-truth recipe + metric). |
| `data/fetch.sh` | Pulls `image.nii.gz` (~6 MB) and `mask.nii.gz` (~100 KB) from the sibling ScienceVision checkout. |
| `data/image.nii.gz` | CT volume — fetched by `fetch.sh`, NOT in git. |
| `data/mask.nii.gz` | 4-class mask — fetched by `fetch.sh`, NOT in git. |
| `score.py` | Reads the mask, computes the answer, compares to your prediction. |

## Running it

See [`docs/platform/quickstart.md`](../../docs/platform/quickstart.md) for
the full 6-step walkthrough. The short version:

```bash
pip install -e ".[demo]"
cd examples/02_medical_spatial_gt
bash data/fetch.sh
printf '%s\n' '\boxed{Kidney}' > my_answer.txt
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

## Why this design

- **Executable ground truth.** The answer is recomputed from `mask.nii.gz`
  at scoring time, never hardcoded. This is BabyVision v1's first line of
  defense against LLM-as-judge (see [`docs/benchmark/evaluation-protocol.md`](../../docs/benchmark/evaluation-protocol.md)).
- **Stage-1 honesty.** No MCP server, no viewer, no tool trace — those
  are stage 2. Stage 1 demonstrates *only* the offline-scoring loop.
- **Self-contained.** No external API keys, no GPU, no internet (with a
  sibling ScienceVision checkout).

## Stage 2 (not yet runnable)

- `sci3d_agent.mcp.server` — 56-tool MCP server (already mirrored at
  `sci3d_agent/`, but the partner-facing launch story isn't wired here yet).
- Viewer pages under `viewer/` rendered via the
  `sv3d_build_<kind>_url` MCP tools.
- A trace-fidelity score on top of the answer-match score.
