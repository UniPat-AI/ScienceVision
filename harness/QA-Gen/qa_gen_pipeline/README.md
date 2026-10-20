# ScienceVision QA-Gen Pipeline

This package is the independent, fail-closed implementation of the QA-Gen
vNext source registries. It does not import or invoke either legacy task
generator.

The first implementation slice provides local-only Seed Registry and Knowledge
Registry commands. It never fetches live Feishu content and it does not call an
LLM. `knowledge_curator` is currently a deterministic compiler adapter.

## Install

```bash
python -m pip install -e .
```

## Commands

All state-changing commands require an explicit `--workspace`. Runtime state is
written below `<workspace>/.qagen/`; only reviewed knowledge snapshots are
written below `<workspace>/knowledge/`.

```bash
export QAGEN_EV2_SOURCE_ROOT=/path/to/ev2-package
qa-gen seed sync --workspace /path/to/workspace \
  --source-spec configs/sources/ev2_seed_source_spec.yaml \
  --package-root "$QAGEN_EV2_SOURCE_ROOT"
qa-gen seed accept-version --workspace /path/to/workspace \
  --request /path/to/request.json --reviewer reviewer-id \
  --resolved-version 1.0.3 --decision accept \
  --rationale "The reviewed v1.0.3 source inventory is authoritative for this workspace."

qa-gen knowledge sync --workspace /path/to/workspace \
  --source expert:biology:/path/to/biology.md \
  --source taxonomy:visual-taxonomy:/path/to/taxonomy.md
qa-gen knowledge authorize --workspace /path/to/workspace \
  --leaf biology.docking_pose_plausibility --expert-source-id biology \
  --taxonomy-source-id visual-taxonomy --owner owner-id \
  --model deterministic-local --max-tokens 0 --max-cost-usd 0
qa-gen knowledge compile --workspace /path/to/workspace \
  --authorization /path/to/authorization.json
qa-gen knowledge approve --workspace /path/to/workspace \
  --draft /path/to/draft.json --reviewer expert-id --decision approved
```

`knowledge compile` writes both a private `SubdomainKnowledgeSnapshot` draft
and a digest-bound `KnowledgeReviewPacket`. Structured local expert and visual
taxonomy YAML sources may populate the review fields, but must declare
`production_approval: false`; only the separate `knowledge approve` command can
freeze a reviewed snapshot and publish a knowledge index. The chirality pilot
and its current review artifacts are documented in
[`docs/chirality-knowledge-pilot.md`](docs/chirality-knowledge-pilot.md).

The current ScienceVision MCP session API is host-side and is not exposed to a
solver. Inspect its controlled-adapter readiness without opening an asset:

```bash
qa-gen viewer diagnose --workspace /path/to/workspace \
  --adapter-config configs/viewer-adapters/sciencevision-mcp-local.template.yaml
```

The committed template is intentionally blocked. See
[`docs/viewer-adapter-status.md`](docs/viewer-adapter-status.md) for the missing
anonymous registry, evidence attestation, and viewer-native measurement gates.

## Frozen V32 Selection Intake

### Private Scene Inventory

The optional `measurement` extra supplies the mature parsers used to create
private, selector-only scene inventories. It is intentionally separate from
the base package because no provider or normal config compile needs to parse
raw scientific assets.

```bash
uv run --extra measurement python scripts/materialize_v32_scene_inventories.py \
  --workspace /absolute/private/workspace \
  --registry /absolute/private/workspace/.qagen/private/viewer/batch-scene-registries/registry.json
```

The command writes only `0600` inventory artifacts and a digest-only private
batch receipt. A nonzero status means that at least one asset failed closed;
it never substitutes a handwritten parser or computes answer values.

The historical 60-row BCM522 selection can be imported into private vNext
source slots without conflating its source-side `raw_compute` provenance with
a viewer-only task. `qa-gen selection import` verifies the locked selection
fingerprint and every referenced asset's bytes/SHA-256; `qa-gen selection
compile` requires an approved taxonomy map and still leaves generation blocked
for viewer, knowledge, config, and human-plan gates. See
[`docs/v32-selection-import.md`](docs/v32-selection-import.md) for the exact
SourceSpec, command sequence, medical mask contract, and mapping-review shape.

Exit codes: `0` success, `2` invalid input, `3` approval/gate pending, `4`
stale input, `5` quarantined input, `6` invalid state transition, `1`
unexpected failure.

`seed sync` accepts only a committed `qagen_seed_source_spec`: its `root_env`
must resolve to exactly the supplied `--package-root`. The spec locks the
package basename and ID, an explicit file-role map, case/review/provenance
constraints, and the importer's canonical `expected_inventory_digest`. All
role paths must remain inside the package and may not be symlinks.

The current EV2 package intentionally pauses on first import: its directory,
manifest, and package ID are `v1.0.3`, while its README version note says
`v1.0.2`.
`seed sync` records the observed inventory and labels, then exits with `3`.
`accept-version` binds its decision to that exact SourceSpec and inventory;
rerun sync only succeeds if both still match. Only `verified` seeds produce
positive lessons. Other seed statuses produce de-identified anti-pattern
lessons only.

## Provider Endpoint Binding

The checked-in provider runtime contracts contain no endpoint, approval
receipt, or credential value. A credentialed provider run requires all three
external environment inputs:

```bash
export QAGEN_UNIV_BASE_URL='https://approved-provider.example'
export QAGEN_UNIV_ENDPOINT_APPROVAL_PATH='/secure/path/provider-endpoint-approval.yaml'
export QAGEN_UNIV_API_KEY='credential-value'
```

`QAGEN_UNIV_BASE_URL` must be an HTTPS origin. The approval file must be kept
outside this repository and bind that exact endpoint, `QAGEN_UNIV_API_KEY`, and
the digest of the exact `provider_runtime.pilot.yaml` document. Its required
kind is `qagen_provider_endpoint_approval`, with decision
`allow_credentialed_https_endpoint` and `production_eligible: false`.
The legacy insecure-HTTP opt-in is not supported. MockTransport-based tests
use a temporary HTTPS endpoint and temporary external approval, so they remain
fully offline.

## Programmatic Gates

`qagen.contracts.canonical_sha256` is the common raw-hex digest helper. It
hashes UTF-8 JSON serialized with sorted keys, compact separators,
`ensure_ascii=False`, and `allow_nan=False`; `qagen.contracts.digest` adds the
`sha256:` reference prefix used by workspace artifacts.

The configuration-facing APIs are deliberately narrow:

- `qagen.sources.safe_fs.inspect_trusted_file` rejects escaping or symlinked
  local sources before import.
- `qagen.seeds.sync_seed_package` is the only Seed Registry implementation. It
  requires an authorized SourceSpec and persists digest-bound version requests
  and decisions in the workspace. `qagen.seed.SeedRegistry` is a deprecated
  fail-fast migration shim; callers must use the canonical function API.
- `qagen.knowledge.registry.KnowledgeRegistry` requires a bounded compile
  authorization and a digest-bound human review decision before a snapshot can
  become approved.
- `qagen.release.leakage_check.assert_solver_bundle_safe` rejects author truth,
  raw asset, seed, knowledge, and credential leakage from solver bundles.
