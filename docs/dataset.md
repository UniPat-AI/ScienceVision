# Dataset

The release is **ScienceVision**: 250 expert-authored questions about scientific 3D data, 50 each
in architecture, biology, chemistry, mechanical CAD, and medical imaging, shipped with the source
assets, rendered views, expert answers, and recorded trajectories they were selected on.

## Download

The dataset repository is private. Authenticate with an account that can read it, then download
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
package this repository verified; read it rather than trusting a branch name.

For a metadata-only look before committing to the full download, fetch the manifest and the task
index:

```bash
hf download UnipatAI/ScienceVision MANIFEST.json data/tasks.jsonl \
  --type dataset --revision <commit> --local-dir data/sciencevision
```

## Layout

```text
data/tasks.jsonl             one row per task: question, answer fields, asset list, ids
MANIFEST.json                package name, creation date, counts, index/schema/readme hashes
CHECKSUMS.sha256             per-file checksums for the uploaded package
KNOWN_ISSUES.md              declared view and screenshot gaps
ID_MAP.csv                   task id to public id map
harbor/                      build_tasks.py, manifests.jsonl, templates: package to task tree
verifier/scoring/            per-task rubric blocks; 54 tasks declare one
oracles/<task_id>/solution/  reference solution: oracle.py, solution.sh, plan, notes
<domain>/<task_id>/
  question.md                the question sheet a human reads
  answer.json                the expert ground truth, with its answer schema
  normalized.json            the common question and answer record
  task_and_trajectory.json   question, gold and the expert's steps
  _meta.json                 domain, sub-domain, step and asset counts
  allow_mcp.json             the delivery-side tool policy
  assets/                    source geometry and volumes
  images/                    expert step screenshots
  views/ , views-aligned/    rendered 2D-capture sets
```

**Read the question and answer from `normalized.json`.** It carries `task_id`, `public_id`,
`domain`, `sub_domain`, `direction`, `question`, `answer`, and `answer_fields`. `answer_fields`
is not on every task, so consumers must not require it.

`answer.json` holds the ground truth in the form the scorer reads: a `shape` declaring the field
list and kind, and the `answer` object itself. The scorer compares the answer field by field.
See [the scoring standard](evaluation.md#scoring-standard).

`data/tasks.jsonl` is the index: one row per task with `task_id`, `public_id`, `domain`,
`sub_domain`, `direction`, `question`, `answer_fields`, `answer_json`, `asset_count`, `assets`,
`n_steps`, `task_dir`, and `oracle_dir`. **The index alone is not a runnable harness input**: it
does not carry the asset roles, docker context, or the full scoring contract. `harbor/` is what
turns the package into Harbor task directories:

```bash
python harbor/build_tasks.py --out /tmp/sciencevision-harbor --route cc-mcp --limit 3
```

That builder stages a valid Harbor tree, but it does not fold in the per-field tolerances the
reference scorer requires. Scored against the package's own ground truth, a tree built this way
gives 200 of 250 golds at 1.0: 46 tasks return `invalid_task_contract` for a missing tolerance,
and four grade partially on a contract normalization the builder does not apply.
`sandbox/build_trees.py` and `scripts.harbor.qa_gen_to_tasks` apply the derivation in
`harness/scripts/harbor/_tolerance_rules.py`; score only a tree built through them.

## What this release is

The 250 tasks are grouped into **22 sub-domains** and **30 `direction` labels**, and every answer
is one JSON object: 143 tasks ask for a single field, 107 for two or more. The five domain counts
are equal, which does not imply uniform coverage of task types within a domain.
`KNOWN_ISSUES.md` carries the declared gaps for the tasks that ship.

## Scoring

Every task's gold scores itself under its contract. Where a task declares its own rubric — lesion
regions graded by membership, decision fields graded without the prose beside them, accepted
spellings — it is `verifier/scoring/<task_id>.json`; the other tasks use the default deterministic
rubric. A scorer that implements those comparators is required: see
[containers.md](containers.md#scorer-versions) for the reference scorer and its digest.

## Known limitations

1. **Some 2D views are dark or small.** 13 tasks have low-contrast captures, 2 a small subject,
   2 expert screenshots are blank, and 1 task's record skips a screenshot. The images ship as
   delivered.
2. **Expert trajectories are records, not scripts.** Step numbers follow the expert record, so
   they may skip; the oracle solution is the reproducible path.
3. **Per-task comparisons hold within a measurement batch.** The leaderboard mixes carried-over
   and re-run cells; a row is not evidence that its neighbours were measured the same week.

## Interpretation and data terms

A delivery-side `allow_mcp.json` is a policy declaration; it does not prove that any historical
run enforced it. Reproducing a result requires the actual run configuration and a compatible task
and scoring contract.

The package declares the **MIT license** for the dataset in its card, `MANIFEST.json`, and
`LICENSE`. The MIT license on this repository covers its documentation, schemas, utility scripts,
and evaluation implementation; it does not grant rights to third-party assets inside the package.

## Version record

[`resources/dataset.json`](../resources/dataset.json) records the repository, the immutable
commit, the inventory, and the verification scope. A dataset update requires a new recorded
revision and a compatibility review; it must not silently change this release's task set.
