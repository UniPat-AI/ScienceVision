# Submission guide

> Status: **draft / scaffold**. Final form will include a PR template, JSON schema, and CI checks.

## Step 1 — pick a profile

See [`leaderboard.md`](leaderboard.md) for the three baseline profiles (`coding`, `vision`, `fusion`).

## Step 2 — run your model

Use the unmodified BabyVision-v2 task set. Do **not** filter, augment, or paraphrase the questions. Modifications invalidate the submission.

## Step 3 — package the result

```json
{
  "submission": {
    "model": "your-model-name",
    "model_version": "2026-06-15",
    "profile": "coding | vision | fusion",
    "task_family": "V2 | V3 | V4 | V6 | Genesis_VP | Medical_Spatial",
    "score": { "accuracy": 0.0, "...": "..." },
    "sampling": { "temperature": 0.0, "top_p": 1.0, "seed": 0 },
    "env": { "python": "3.11", "gpu": "..." },
    "tool_trace_path": "traces/your-model.jsonl",
    "image_inputs_manifest": "inputs/your-model.json"
  }
}
```

## Step 4 — open a PR

Open a PR against this repo with:
1. The result JSON under `leaderboard/submissions/<your-model>/<task-family>.json`
2. The supporting trace and input manifests
3. A short writeup in the PR description

## Step 5 — CI validation

A GitHub Action will:
1. Validate the JSON against the schema
2. Replay a sample of `tool_trace` against the MCP server (for `coding` / `fusion`)
3. Spot-check `image_inputs_manifest` for completeness (for `vision` / `fusion`)
4. Re-score against the held-out evaluation key

CI checks must pass before a maintainer reviews and merges.
