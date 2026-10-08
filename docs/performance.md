# Performance data format

Performance artifacts preserve the current harness output. A small `run.json` supplies the versions and coverage that the native leaderboard does not fully identify. This repository contains synthetic examples only.

```text
performance/<run-id>/
  run.json
  runs_leaderboard.json
  runs/                         # optional sanitized per-task collector records
```

## Files and schemas

| File | Schema | Meaning |
|---|---|---|
| `run.json` | [run-metadata.schema.json](../schemas/run-metadata.schema.json) | Dataset/harness/container revisions, model, scope, artifact hashes, coverage, and result kind |
| `runs_leaderboard.json` | [performance.schema.json](../schemas/performance.schema.json) | Native output of `collect_qagen_results.build_leaderboard` |
| Dataset `index.jsonl` | [dataset-index.schema.json](../schemas/dataset-index.schema.json) | Historical selection and two-arm outcomes shipped with the dataset |

Schemas follow JSON Schema 2020-12. Unknown native-output fields are retained for forward compatibility. The dataset index is a separate legacy record type and is not a new benchmark submission.

## Native leaderboard fields

Each entry in `rows` identifies a `task_id`, `set`, `domain`, `v_class`, `harness`, and `model`. `status` is `ok`, `error`, or `incomplete`. `reward` and `answer_score` are numbers or `null`; process fields include `citable`, `blockers`, capability coverage, and MCP attempt/success signals. Preserve the upstream fields rather than renaming them.

Native `harness` values and archived dataset fields retain their internal route identifiers. Use the [paper setting names](evaluation.md#paper-settings-and-harness-mapping) in displayed tables and prose; keep the original IDs in stored results for compatibility. A route ID alone does not prove that a run enforced the paper's intended access conditions.

A numeric `0.0` is a scored zero. `null` means that no value is available. An absent row means that the task has no collected result, not that it was excluded from the declared evaluation scope. A process-gate `citable: null` means the gate has no verdict; it is different from `false`.

The collector's `n_completed` counts rows with non-null reward. `n_eligible_completed` further requires `evaluation_eligible: true`. `citability` splits that eligible completed set into `n_citable`, `n_blocked`, and `n_ungated`. Error and incomplete rows remain in `rows`; they are not part of those means. This makes a separate expected-task count essential.

The collector reports:

- `by_harness_model_mean_reward`: arithmetic mean over eligible completed rows whose `citable` value is exactly `true`.
- `by_harness_model_mean_reward_all_eligible`: diagnostic mean over all eligible completed rows.
- `by_harness_model_census`: the eligible/citable/blocked/ungated counts for each route/model group.
- `by_harness_domain_mean_reward`: analogous citable means by route and domain.
- Answer, process, capability, and MCP summaries: their native separate diagnostics; MCP success only uses runs that attempted MCP.

These dictionaries are collector output. The separate `aggregate_weighted` tool can compute a flat mean, equal-weight domain means, or declared weighted means. Record the tool and mode when using that aggregate; do not label a collector mean as a domain-equal score.

## Coverage and result interpretation

`run.json` declares the expected trial count before exclusions, the attempted row count, completed and eligible counts, missing/error/incomplete counts, and process-gate census. Expected trials count task × route × model × repeat, not unique task IDs. Record the expected task set as a hashed artifact, and keep rendering or contract exclusions with their reasons.

Every reported mean must be shown with its denominator and coverage. Missing, error, incomplete, blocked, and ungated trials are not silently discarded. Do not infer full-benchmark accuracy from a successful subset. Missing per-row scores remain `null`. Native mean dictionaries omit groups with no qualifying observations; they do not emit invented zeros. The validator rejects inconsistent counters and averages.

A `record_kind` of `benchmark` requires a nonempty scope, a numeric reward for every expected trial, at least one citable result, and no blocked or ungated eligible trial in this repository's metadata contract, as well as the harness's required evidence. The local validator checks schema, counts, arithmetic, and artifact hashes; it does not authenticate or validate the runtime attestation and cannot certify an official result. Use `diagnostic` for incomplete or uncertified results. Keep scorer ground-truth self-tests separate: they establish scorer consistency and do not measure model performance.

## Reproduction metadata

Record the exact dataset repository and 40-character commit, using that immutable commit as `revision_name`; the index hash; harness commit; model identifier/version; sampling configuration; platform and dependency versions; and each image's GHCR digest. An equivalent Docker Hub image has its own manifest digest. Include the command as an argument array without credentials, and record hashes of the task set, native leaderboard, and supporting evidence.

No tokens, authentication files, private service addresses, raw local paths, or unreviewed trajectories belong in submitted artifacts. `record_kind: example` is reserved for synthetic documentation examples and never enters a leaderboard.

## Validate

The [example directory](../performance/examples/README.md) demonstrates one citable result, one blocked result, an error, and a missing trial. It is deliberately incomplete. Run these checks from the ScienceVision repository root.

```bash
python scripts/validate_repository.py
python scripts/validate_repository.py --performance-dir performance/examples
```
