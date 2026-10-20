# Dataset

The release is **ScienceVision**: 250 expert-authored questions about scientific 3D data, 50 each
in architecture, biology, chemistry, mechanical CAD, and medical imaging, shipped with the source
assets, rendered views, expert answers, and recorded trajectories they were selected on.

## Download

The dataset is gated. Authenticate with an account that has been granted access, then download
a fixed revision rather than a branch. Branches move; the numbers elsewhere in this repository
are tied to one revision.

```bash
hf auth login
hf download UnipatAI/ScienceVision \
  --type dataset \
  --revision <commit> \
  --local-dir data/sciencevision
```

`<commit>` is the revision recorded in [`resources/dataset.json`](../resources/dataset.json),
which also carries the file count, total logical bytes, and the index hash. That file pins the
snapshot this repository verified; read it rather than trusting a branch name.

For a metadata-only look before committing to the full download, fetch the index and this
release's own change ledger:

```bash
hf download UnipatAI/ScienceVision README.md index.jsonl CHANGES.md \
  --type dataset --revision <commit> --local-dir data/sciencevision
```

## Layout

```text
index.jsonl                  one row per task: selection metadata and archived outcomes
CHANGES.md                   the change ledger for this release
task_ids.txt                 all 250 task ids, one per line
<domain>/<task_id>/
  question.md                the question sheet a human reads
  answer.json                the expert ground truth, with its answer schema
  normalized.json            the common question and answer record
  _meta.json
  allow_mcp.json             the delivery-side tool policy
  assets/                    source geometry and volumes
  images/                    expert imagery
  views/ , views-aligned/    rendered 2D-capture sets
  trajectory.jsonl           the recorded expert session
```

The published package names the change ledger `CHANGES.md` and the id list `task_ids.txt`. A
historical snapshot may carry older filenames with the same content.

**Read the question and answer from `normalized.json`.** Task-specific files vary across the
corpus, and only `normalized.json` is present for every task. It carries `task_id`, `set`,
`domain`, `sub_domain`, `direction`, `question`, `answer`, `answer_fields`, `answer_match`,
`answer_source`, and `repairs`. `answer_fields` is not on every task, so consumers must not
require it.

`answer.json` holds the ground truth in the form the scorer reads: a `shape` declaring the
field list and kind, and the `answer` object itself. The scorer compares the answer field by
field. See [the scoring standard](evaluation.md#scoring-standard).

`index.jsonl` records how each task was selected and how it scored in the archived runs that
selected it. Its identifiers `cc` and `ccmcp` label the two 3D access settings; retain them in
file paths and field names. Its schema is
[`dataset-index.schema.json`](../schemas/dataset-index.schema.json). **The index is not a
runnable harness input**: it does not carry the asset manifest or the scoring contract the
harness needs.

## What this release is

This revision replaces **13 tasks** of the previous 250-question set: eight architecture and five
medical. Every swap keeps the same `sub_domain`, so all 22 buckets and all five domain counts are
unchanged. The other 237 tasks are byte-identical to the previous set. The reasons and the full
change ledger are in `CHANGES.md` in the dataset itself.

Each `index.jsonl` row records its own provenance in `source`: a task either carries over from
an earlier measurement or was swapped in at a later one. A row naming a `swapped_in_for` value
carries the id of the task it replaced. The `set` and `source` values are historical
measurement-batch labels; they are not release names.

## Known limitations

These are the dataset's own declared gaps. They are reproduced here because they change what a
reader may conclude from a score.

1. **No per-task run telemetry** ships in the package. Agent trajectories stay with the run
   roots.
2. **The medical swap-ins are single-rep.** Their three-rep run had not completed when this
   snapshot was assembled.
3. **Architecture mixes two batches.** The 13 outgoing tasks carry earlier measurements, so
   per-task comparisons hold within a batch, not across batches.
4. **Some golds are prose or identifier strings graded verbatim.** The scorer-side widening
   that addresses two of those families is implemented but not deployed in this snapshot.

## Interpretation and data terms

The dataset card documents mixed source runs, revised scoring contracts, and tasks whose answers
depend on viewer camera state. A delivery-side `allow_mcp.json` is a policy declaration; it does
not prove that any historical run enforced it. Reproducing a result requires the actual run
configuration and a compatible task and scoring contract.

The source declares **no license**. The MIT license on this repository covers its documentation,
schemas, utility scripts, and evaluation implementation; it does not grant rights to the dataset
or to third-party assets inside it.

## Version record

[`resources/dataset.json`](../resources/dataset.json) records the repository, the immutable
commit, the inventory, the verification scope, and the canonical home the release is expected to
move to. A dataset update requires a new recorded revision and a compatibility review; it must
not silently change this release's task set.
