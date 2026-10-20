# ScienceVision formal scorer bundle

This directory produces the isolated release image
`suermars/sci3d-scorer:server-20260923-h20`. It is a scorer artifact, not an
agent image: its staged context contains only the deterministic scorer,
`TaskSpec` schema, and the Harbor metadata-to-schema adapter needed to execute
those formal contracts. It contains no `sci3d_agent`, MCP server/client, model
client, raw dataset, viewer, or private mask.

## Build and run

Build is local only and never pushes a registry tag:

```sh
./scorer_bundle/build_image.sh suermars/sci3d-scorer:server-20260923-h20
```

The script stages an explicit allow-list, writes `scorer_manifest.json`, builds
for `linux/amd64`, then prints the local image ID, any local repo digest, and
the manifest hash. Release publishing is intentionally a separate action.

Run the fixed offline entry point with immutable task metadata and an answer:

```sh
docker run --rm --network=none --read-only --cap-drop=ALL \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -v "$PWD/task.toml:/work/task.toml:ro" \
  -v "$PWD/answer.json:/work/answer.json:ro" \
  suermars/sci3d-scorer:server-20260923-h20 \
  --task /work/task.toml --answer /work/answer.json
```

The entry point emits one canonical JSON result to stdout. It also installs an
in-process socket deny guard; Docker `--network=none` remains the authoritative
runtime isolation boundary.

## Formal eligibility and score semantics

Scoring is fail-closed. The task must set `evaluation_eligible = true` exactly;
missing or false is a `blocked` result, not a zero that can be mixed into a
formal aggregate. The bundle supports only:

- `deterministic_structured_v2_1` and its explicit deterministic aliases;
- `structured_deterministic_rubric_v1`; and
- `extent_voxel_tolerance_v1`.

The deterministic parser accepts one JSON object (or one
`SCI3D_FINAL_JSON:` terminal marker), preserving type-sensitive
`integer_equal` and ordered `vector_abs_tolerance`. `extent_voxel_tolerance_v1`
requires its signed three-axis NIfTI contract. LLM/prose judges, mask-Dice,
unknown contracts, and malformed contracts stay `blocked`; this release never
silently auto-grades them.

## Reproducibility receipt

`scorer_manifest.json` is generated from the exact staged sources. It records:

- bundle version and Git revision;
- source/bundle tree hashes;
- `TaskSpec` schema hash;
- a per-comparator implementation registry and registry hash; and
- the manifest self-hash checked by the CLI before scoring.

`SOURCE_DATE_EPOCH` can be supplied to make stage file mtimes explicit; absent
it, staging uses epoch zero. The source hashes, rather than repository-wide Git
cleanliness, are authoritative so unrelated LFS cache changes cannot alter the
receipt.

## Coverage audit

Use `audit_v9_coverage.py` only with the original v9 task index:

```sh
python3 scorer_bundle/audit_v9_coverage.py \
  --index /path/to/ScienceVision-STEM-250-review-v9/index.jsonl \
  --expected-total 250 \
  --output scorer_bundle/reports/v9_coverage.json
```

It writes source hash, task IDs, domains, `evaluation_ready`, structured/prose/
malformed categories, and conservative formal eligibility. It also computes
the historical “med63 / 28 mask” quantities only from that exact source. A
mask is counted public solely when `public_input_roles`, `allowed_input_roles`,
or `public_inputs` explicitly says so; a `mask_id`, filename, or sidecar is
not public evidence. A v7/dev-subset input is marked
`blocked_non_v9_or_unknown_source`, never relabeled as v9.
