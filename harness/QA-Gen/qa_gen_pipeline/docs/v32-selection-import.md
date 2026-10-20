# V32 BCM522 Frozen Selection Import

`inputs/selections/V32_BCM522_SIXROUTE_SELECTION.json` is a byte-for-byte
local development import of the historical selection created at
`7cc8754d709df31ff2c1dacb3185520221943bd5` from
`docs/harbor/V32_BCM522_SIXROUTE_SELECTION.json`.

Its authorized local SourceSpec is
`configs/sources/v32_bcm522_sixroute_selection.yaml`. The spec locks:

- the selection file SHA-256: `75ae4cbb...336463d6`;
- the selection fingerprint: `149b2960...f1846d4`;
- 60 total rows, with 20 each in biology, chemistry, and medical;
- the expected local BCM522 package basename and package ID; and
- the historical repository commit/path that supplied the input.

The input is deliberately a development fixture, not an installed package
resource and not a solver bundle. It contains source-side asset paths and
must remain outside released solver/scorer payloads.

## Stages

```text
frozen selection + local asset package
  -> selection import
  -> 60 private SelectionSourceSlot artifacts
  -> approved taxonomy mapping
  -> private vNext batch manifest
  -> later config/viewer/knowledge/plan gates
```

`selection import` validates the v32 schema, every per-domain count and ID
digest, the global `id_sha256`, and every selected primary/mask asset's byte
count and SHA-256 through a locked, no-symlink trusted-tree snapshot. It makes
no network request. Before validation it writes only an append-only start
event; source slots and the import artifact are not written until every source
check succeeds.

`selection compile` accepts only a human-approved mapping document. It reads
the persisted source slots, validates their content digests and provenance,
binds each source access track to an approved generation strategy, and writes
a private `vnext_selection_batch_manifest`. It does not create a viewer, open
an asset, call an LLM, or create a generation plan.

## Import

Run from `QA-Gen/qa_gen_pipeline` with the actual, locally mounted package:

```bash
qa-gen selection import \
  --workspace /path/to/qagen-v32-workspace \
  --selection inputs/selections/V32_BCM522_SIXROUTE_SELECTION.json \
  --source-spec configs/sources/v32_bcm522_sixroute_selection.yaml \
  --asset-root /Users/sue/Research/ScienceVision-local-packages/scale-v3.2-bcm522-question-assets-bundle-20260826-v1.1.0
```

The command returns a `selection_import_path` and writes these private files:

```text
<workspace>/.qagen/private/selections/
  imports/<selection>.<digest>.selection-import.json
  slots/<selection>/<question_id>.json
```

The import artifact contains the 60 source-slot references. Every slot keeps
the original source row, source selection/spec provenance, and the observed
asset facts. It is intentionally private because it includes local asset
locations.

## Audit Events

Import and compile append immutable event envelopes to
`<workspace>/.qagen/private/events.jsonl`:

- `selection_import_started`, `selection_import_completed`, or
  `selection_import_failed`;
- `selection_compile_started`, `selection_compile_completed`, or
  `selection_compile_blocked`.

Every start event includes a path-free `request_digest` plus the available
selection/import and source input digests. The completed import event also binds
the selection file/ID digest, SourceSpec digest, path-free asset-attestation
digest, row count, asset-reference count, and import digest. For this frozen
selection those counts are `60` and `80`. Compile events add the mapping
file/content digests and output batch-manifest digest when available. Events
never contain local source/asset paths, source rows, questions, or answers.
Failure events record only the phase and exception class, not exception text.

## Taxonomy Mapping

No mapping is checked in as approved. The source taxonomy has 28
domain-qualified leaves, while vNext leaf availability and expert approval are
separate decisions. Do not infer aliases from names alone.

An approved mapping must validate as
`qagen_selection_taxonomy_mapping` and bind both the exact selection ID and
the exact selection file digest:

```yaml
schema_version: 2
kind: qagen_selection_taxonomy_mapping
id: v32_bcm522_vnext_taxonomy_review_1
selection_id_sha256: 149b29605da6cd2862e427dacb4d1098b106d23eb95b248ffdf6dc0bdf1846d4
selection_file_sha256: sha256:75ae4cbb771d42ff68811b2e06a82ca34cb00749f888673518dac937336463d6
generation_strategy_by_source_access_track:
  viewer_measurement: preserve_intent
  raw_compute: reauthor_on_asset
review:
  decision: approved
  reviewer: domain-taxonomist-id
  reviewed_at: 2026-08-27T00:00:00Z
  rationale: Explain why each source leaf matches the vNext scientific scope.
mappings:
  - source_leaf: biology.example_source_leaf
    target_leaf: biology.docking_pose_plausibility
    rationale: Evidence-backed taxonomy decision for this source leaf.
```

Each mapping stays inside its domain. Unknown source leaves, duplicated source
leaves, cross-domain mappings, mismatched selection locks, a non-approved
review, a missing source-track strategy, or an invalid strategy pairing are
rejected. The strategy policy is part of the mapping content digest, while each
compiled batch row carries the selected `generation_strategy`; downstream batch
plans must rebind that row value and must not infer it again from the source
track. A target leaf absent from `configs/subdomains/` remains visible as
`target_config_missing`; it does not become implicitly enabled.
The committed `V32_BCM522_VNEXT_ROUTE_PROPOSAL.draft.json` is explicitly an
unapproved proposal and cannot be passed as an approved taxonomy mapping.

Compile an approved mapping with:

```bash
qa-gen selection compile \
  --workspace /path/to/qagen-v32-workspace \
  --selection-import /path/to/qagen-v32-workspace/.qagen/private/selections/imports/<selection>.selection-import.json \
  --taxonomy-mapping /path/to/approved-v32-taxonomy-map.yaml
```

It returns exit code `3` while downstream gates are pending. That is expected:
the resulting batch has `generation_eligible: false` until an appropriate
viewer profile, sub-domain configuration, knowledge snapshot, plan approval,
and release review exist.

## Access-Track, Strategy, and Medical Rules

`raw_compute` and `viewer_measurement` are historical source provenance, not
solver permissions. The importer preserves that provenance separately:

```json
{
  "source": "raw_compute",
  "effective": "raw_compute",
  "conversion": "preserved"
}
```

`access_track.conversion: preserved` only states that the historical metadata
was not mutated. It is not a generation strategy. The approved mapping must
also carry the generic, schema-validated policy shown above. The compiler
rejects a missing strategy, `raw_compute -> preserve_intent`, or
`viewer_measurement -> reauthor_on_asset`, then copies the matched strategy to
the batch row. For this frozen selection that yields 9 `preserve_intent` rows
and 51 `reauthor_on_asset` rows. `reauthor_on_asset` requires a new
viewer-answerable candidate and new truth/evidence; it never authorizes raw
compute for a solver.

Every `niivue_deployed` row must retain exactly one typed mask in both the
source slot and private batch metadata. The mask carries either:

- `mask_selection: label_index` with an integer `mask_id`; or
- `mask_selection: complete_nonzero_overlay` with `mask_id: null`.

Missing or malformed Niivue masks fail import/compile rather than allowing an
ambiguous medical scene to reach a viewer bridge.
