# Compiled Selection Batch Runtime

`qagen.batch` is the bridge between the private, attested v32 selection import
and a future real QA-generation runtime.  It is deliberately separate from
`plan create`: a compiled selection can contain 60 slots, while the historic
leaf-plan count limits remain unchanged.

The batch registry remains a control plane. It can prepare, approve, start,
resume, and run a no-network diagnostic by default. An explicit `--live`
branch adds local-pilot execution in a separate private sidecar. It uses the
existing approved HTTPS provider runtime and typed worker contracts; it never
turns a missing route into fixture output. A successful dry-run remains a
pending gate rather than a generated-task success.

## Lifecycle

```text
attested selection import
  -> compiled selection manifest (still generation_eligible: false)
  -> approved lock bundle
  -> private batch plan (one opaque job per source slot)
  -> human batch-plan approval
  -> resumable batch run
  -> no-network provider diagnostic (default)
     or explicit local-pilot live sidecar
  -> typed worker outputs + verifier result
  -> human domain review
  -> existing release/staging flow
```

The compiled selection manifest is intentionally not sufficient to run. Its
taxonomy mapping and per-row `generation_strategy` are content-digest bound,
but it does not retain all approval receipts or per-slot seed, knowledge,
viewer, routing, budget, retry, and ground-truth locks. The batch lock bundle
supplies those missing first-class inputs and is content-digest bound into the
private plan. Before live execution, every candidate reference answer must be
traceable to the plan-locked, approved ground-truth registry.

## Required Lock Bundle

The input is a human-approved `qagen_compiled_selection_batch_locks` YAML or
JSON document validated by
`configs/schemas/compiled_selection_batch_locks.schema.json`.

Every manifest row needs exactly one `job_bindings` entry with these approved
locks:

- `config`, `seed`, and `knowledge`: `status: approved`, content digest, and
  approval receipt digest.
- `viewer`: the same lock fields plus an opaque `scene://` reference and a
  controlled operation allowlist containing `render`.
- `mapping`: approved and equal to the taxonomy mapping digest embedded in the
  compiled selection manifest.
- `ground_truth_registry`: an approved private answer-membership registry and
  its approval receipt. Preparation validates the source, imports an immutable
  private snapshot into the workspace, and binds the snapshot digest into both
  the batch plan and the plan-owner approval.
- `routing`: approved routes for Opus 5 brief building, DeepSeek V4 Pro
  author/truth/evidence/rewrite work, and GPT-5.6-sol verification.  The
  provider/model pairs are checked against the existing provider contracts.

The bundle also declares an aggregate token/cost budget and bounded retry
policy.  Neither field causes a model call in this skeleton; they are frozen
now so a later execution adapter cannot silently relax them.

The lock-bundle strategy must exactly equal the `generation_strategy` already
bound into its manifest row; the batch runtime must not infer it again from an
access track. For source slots whose historical access track is `raw_compute`,
the only accepted strategy is `reauthor_on_asset`; for `viewer_measurement`, it
is `preserve_intent`. The resulting job always requires a new viewer-native
candidate. `raw_compute` never becomes a solver capability or an author prompt
fallback.

## Viewer Capability Bridge

Each approved `viewer` lock additionally binds a static local-review profile
and one per-slot capability receipt:

```json
{
  "capability_mapping": {
    "mapping_id": "compiled_selection_v32_local_viewer_capabilities",
    "content_digest": "sha256:...",
    "profile_id": "molstar_mcp_local_review_v1",
    "profile_digest": "sha256:...",
    "receipt": {"path": ".../viewer-capability.json", "content_digest": "sha256:..."}
  }
}
```

The receipt binds the mapping/profile digests, source slot and access track,
strategy, target leaf, source viewer-hint digest, asset formats, anonymous
viewer lock digest, and the exact least-privilege operation list.  It is not
scene evidence and cannot make an unavailable viewer feature valid.

- `preserve_intent` must retain every compatible source viewer hint through
  the controlled profile.
- `reauthor_on_asset` intentionally ignores historical operation subsets. Its
  operations are derived afresh from asset format, profile, target leaf, and
  domain policy.
- `render` is the base visual operation. Logical leaf `inspect` maps to it;
  it is not a separate historical source capability.
- Review packaging rechecks the static profile and receipt binding. It does
  not reapply the old-source subset rule to `reauthor_on_asset` jobs.

Current local profiles are deliberately narrow: Mol* exposes `render`,
`rotate`, `zoom`, and native `measure_distance`; NiiVue exposes `render`,
`rotate`, `zoom`, `slice`, and `window_level`. Dihedral, area, volume, and any
other operation not backed by the controlled adapter are blocked rather than
advertised. See `configs/viewer-capabilities/compiled_selection_v32.yaml`.

The receipt path may be relative to the external lock-bundle directory; the
persisted plan normalizes it to an absolute local path. The receipt approves a
static capability binding, not a runtime handoff. Live execution independently
opens the controlled viewer and creates/revalidates its private evidence
handoff. Static mapping coverage alone never grants model execution: the live
bridge also requires a matching revalidated `viewer_profile`, resolved
`SceneRegistry` entry, and source code that implements a host handoff verifier
for the same leaf and exact operation set. Unsupported combinations fail before
any provider request.

## Agent References And Isolation

Each job records six stage references: `brief_builder`, `author`,
`truth_builder`, `evidence_worker`, `verifier`, and `rewriter`.  Each reference
contains:

```json
{
  "role": "author",
  "provider": "deepseek",
  "model": "deepseek-v4-pro",
  "prompt_digest": "sha256:...",
  "input_digest": "sha256:...",
  "output_digest": null,
  "tokens": {"input": 0, "output": 0},
  "cost_usd": 0.0,
  "latency_ms": 0,
  "status": "planned",
  "retry_count": 0
}
```

The job stores sanitized, typed input projections.  They contain only opaque
slot and scene references, locked scientific inputs, and approved viewer
operations.  They reject historical task IDs, question IDs, answer keys,
reference answers, raw asset paths, compute package declarations, and source
rows.  The verifier projection receives the new candidate/evidence dependency
slots only; it never receives a historical truth or historical answer field.

## CLI

```bash
qa-gen selection batch prepare \
  --workspace /path/to/workspace \
  --selection-batch /path/to/workspace/.qagen/private/selections/batches/...vnext-batch.json \
  --lock-bundle /path/to/approved-batch-locks.yaml

qa-gen selection batch approve \
  --workspace /path/to/workspace \
  --plan /path/to/workspace/.qagen/private/selections/batches/compiled-runs/plans/...plan.json \
  --reviewer domain-owner --decision approved

qa-gen selection batch start \
  --workspace /path/to/workspace \
  --plan /path/to/...plan.json --approval /path/to/...approval.json

qa-gen selection batch execute \
  --workspace /path/to/workspace --run-id batch-... --dry-run \
  --provider-runtime /path/to/provider_runtime.pilot.yaml

# --dry-run is optional: it is the default mode.
qa-gen selection batch execute \
  --workspace /path/to/workspace --run-id batch-...

# The only path that may invoke a provider. It requires a runtime config,
# credential, approved HTTPS endpoint receipt, valid resource locks, and a
# controlled viewer handoff for every selected job.
qa-gen selection batch execute \
  --workspace /path/to/workspace --run-id batch-... --live \
  --provider-runtime /path/to/provider_runtime.pilot.yaml \
  --limit 5

qa-gen selection batch resume --workspace /path/to/workspace --run-id batch-...
```

Without `--live`, `selection batch execute` calls the existing provider runtime
diagnostic with `smoke=false`, records `network_called: false`, and updates
each selected agent reference to `dry_run_ready` or `blocked_runtime`. It does
not reserve usage, retry a provider request, create a candidate directory, or
write any output digest. A missing credential, endpoint approval, or runtime
config is persisted as a blocking diagnostic and keeps the run safely
resumable.

With `--live`, the original diagnostic run is never rewritten into a generated
run. The executor writes only:

```text
.qagen/private/selections/batches/compiled-runs/runs/<run-id>/
  private/live-execution/state.json
  private/live-execution/jobs/<opaque-job-id>/outputs/<role>-<attempt>.json
```

The stage order is fixed:

```text
brief_builder -> author -> (truth_builder and evidence_worker) -> verifier
```

`rewriter` is available only when an external human `qagen_revision_receipt`
binds the same run, work item, candidate, and verifier digest. A rewritten
candidate is `revision_generated_pending_reverification`; it never replaces
the verified candidate or becomes stage-eligible without a new controlled
evidence and verifier pass.

The live executor validates every provider result against the routing schema,
exact input digest, candidate/evidence/verifier cross-bindings, viewer handoff,
and reserved usage before it persists the stage output. If runtime setup is
missing or invalid, it writes `live_blocked_runtime` with
`network_called: false`. A preflight failure also writes no candidate. A
verifier pass is only `verification_complete_pending_human_review`; release
continues through the existing human review and release-run binding, and the
live sidecar reports `stage_eligible_candidate_count: 0` by design.
