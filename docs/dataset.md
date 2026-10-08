# Dataset

## Exact source

- Repository: [`elorian-ai/ScienceVision`](https://huggingface.co/datasets/elorian-ai/ScienceVision/tree/0f9e91ed597a49c205960a0f96ff3d4457db682e).
- Commit: `0f9e91ed597a49c205960a0f96ff3d4457db682e`.
- Tasks: 250, with 50 in each of five domains.
- Files: 21,653; logical contents: 8,224,311,200 bytes.
- `index.jsonl` SHA-256: `d90b72139fa74c9cbe331559d2bb617d21c92dcd57e11445f41b028703864f37`.

Verified on 2026-10-06. File totals come from repository metadata; the full asset payload was not downloaded or checksum-verified for this repository.

The source is private and records automatic gating. Authenticate with an account authorized to read it. The current dataset stays in `elorian-ai/ScienceVision`. Run the following commands from the ScienceVision repository root to download and check the pinned snapshot.

```bash
hf auth login
hf download elorian-ai/ScienceVision \
  --type dataset \
  --revision 0f9e91ed597a49c205960a0f96ff3d4457db682e \
  --local-dir data/sciencevision
python scripts/check_dataset.py data/sciencevision
```

For a small metadata-only inspection:

```bash
hf download elorian-ai/ScienceVision README.md index.jsonl \
  --type dataset \
  --revision 0f9e91ed597a49c205960a0f96ff3d4457db682e \
  --local-dir data/sciencevision
python scripts/check_dataset.py data/sciencevision --index-only
```

Do not substitute `main`: its task IDs and file contents differ from the selected snapshot.

## Layout and records

```text
index.jsonl
<domain>/<task_id>/
  _meta.json
  normalized.json
  allow_mcp.json
  assets/                    # 240 tasks; 10 architecture tasks use root-level assets
  views/
  views-aligned/
  runs/cc/
  runs/ccmcp/
```

Every task has `normalized.json` with `task_id`, `domain`, `sub_domain`, `direction`, `set`, `question`, `answer`, `answer_match`, `answer_source`, and `repairs`. All 250 questions are present and all answers are JSON objects. `answer_fields` exists on 54 tasks, so consumers must not require it unconditionally.

Task-specific source files vary: 118 tasks have `question.md` and `answer.json`; 156 have `题目与轨迹.json`; 84 have `trajectory.jsonl`. Read the common question and answer from `normalized.json`. Asset layout varies: 240 tasks have an `assets/` subtree; 10 architecture tasks store scene JSON and associated PNG files at the task root. Resolve each asset from the actual task files and retain its original path and hash in the evaluation contract.

`index.jsonl` records selection metadata and archived outcomes. Its historical identifiers `cc` and `ccmcp` label **3D + Scaffold** and **3D + Scaffold + Vision MCP**, respectively; retain those identifiers in file paths and field names. They do not verify the access restrictions enforced by each run. The index does not contain the complete question, asset manifest, or scoring contract required by the harness. Its schema is [`dataset-index.schema.json`](../schemas/dataset-index.schema.json).

## Interpretation and data terms

The source card documents mixed source runs, revised scoring contracts, and tasks whose answers depend on viewer camera state. A delivery-side `allow_mcp.json` does not prove that an archived run enforced that policy. Reproducing a result requires the actual run configuration and compatible task/scoring contracts.

Archived trajectories include runtime metadata. Keep the bundle private while its public release review is incomplete. The source card and file listing provide no dataset license. Apache-2.0 in this GitHub repository does not grant rights to the dataset or its third-party assets.

## Version record

[`resources/dataset.json`](../resources/dataset.json) records the selected existing repository, immutable commit, inventory, and verification scope. Any future dataset update must receive a new recorded revision and compatibility review; it must not silently change this release's task set.
