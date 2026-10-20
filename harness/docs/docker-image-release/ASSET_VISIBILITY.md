# Asset visibility and annotation isolation

This document defines the generated-task boundary for the CC, CC-MCP, and
CC2D evaluation routes. It applies to task-local assets only; verifier metadata
and source datasets remain private to the evaluator.

The governing implementation is
[`scripts/harbor/build_harbor_tasks.py`](../../scripts/harbor/build_harbor_tasks.py),
with structural checks in
[`scripts/harbor/security_contract.py`](../../scripts/harbor/security_contract.py).

## Policy

An asset's declared `role`, rather than its filename, determines whether it is
agent-visible. The following roles are verifier-private by default:

- `mask`, `label`, `labels`, `segmentation`, and `segmentation_sidecar`;
- `annotation`, `annotation_sidecar`, `ground_truth`,
  `ground_truth_sidecar`, `answer_key`, and `target_mask`.

This prevents a file whose contents encode a target region or answer from being
made public simply by renaming it. Private roles are retained in verifier-side
metadata and, where applicable, may be consumed by the trusted CC2D renderer.
They are not copied into a CC agent image or a CC-MCP worker by default.

The policy is serialized as public, non-secret `route_asset_visibility`
metadata. It contains only route, status, reason code, and role names; it must
not contain a source path, asset identifier, hash, or ground truth.

### Explicit future-task opt-in

A future task may make a normally private role a public input only after its
task contract has been rewritten and carries this explicit, versioned opt-in:

```json
{
  "schema_version": "sciencevision.asset_visibility_contract.v1",
  "public_input_roles": ["mask"]
}
```

This is an authoring decision, not a compatibility fallback. Existing tasks do
not become public merely because they declare a mask asset.

## Route boundaries

| Route | Raw assets available to the model agent | Private label/sidecar handling |
| --- | --- | --- |
| CC | Only role-permitted task inputs staged under `/data`. | Private roles are not staged or announced in `task.toml` / `instruction.md`. |
| CC-MCP | The main agent image receives no raw asset directory. It calls the private MCP worker. | The worker is staged through the same role filter as CC, so it receives no default-private mask or sidecar. |
| CC2D | Only rendered PNG views are copied to `/data`; no raw source asset directory is created for the agent. | The trusted host-side renderer may read a private mask to construct views. The agent payload contains neither raw paths nor `mask_id`. |

The CC and CC-MCP routes deliberately share `_stage_assets`; their input sets
cannot drift. CC-MCP's agent image remains source- and asset-free. Its worker
is the only image that copies the filtered task-local `asset/` directory.

## Medical task semantics

### `med_ct_geometry`

The current v9 CT-geometry questions define the target from a segmentation
label. Since that mask is private by default, CC and CC-MCP cannot be treated
as comparable scored routes after it is withheld. For either route, absent the
explicit future-task opt-in above, the builder emits:

```text
route_asset_visibility.status = "route_ineligible"
evaluation_eligible = false
evaluation_ineligible_reason =
  "route_ineligible:private_mask_requires_explicit_public_input_contract"
```

The task is still inspectable and its verifier-private source contract remains
available for auditing, but its result is excluded from formal aggregation.
`aggregate_weighted.py` reports the count as
`n_evaluation_ineligible_excluded`; an ineligible row cannot enter a benchmark
denominator merely because its process gate is otherwise clean.

CC2D is different: its trusted renderer may use the private mask to produce
views, while the vision agent remains limited to those views. This does not
make the raw mask public.

### `med_lesion_localization`

The audited v9 lesion-localization tasks have only a primary CT input. They do
not have a mask/label asset to withhold, so the visibility policy leaves CC and
CC-MCP `evaluation_eligible = true` when their other eligibility conditions
are satisfied.

## CC-MCP task asset manifest

For every CC-MCP task, the builder writes:

```text
environment/mcp-task-assets.json
```

using `MCP_WORKER_PACKAGING.render_task_asset_manifest(staged)`. The manifest
uses schema `sciencevision.mcp_task_assets.v1` and maps the already-filtered,
staged `/data/...` files to task-local asset IDs, formats, roles, and viewers.
It is private to the MCP worker build context. It must not contain source
paths, repository paths, hashes, or a default-private role.

The worker Dockerfile copies both `asset/` and this manifest. The model-facing
Dockerfile may not copy either one.

## Static security preflight

`validate_task_tree()` checks, before launch, that:

1. public `route_asset_visibility` agrees with the public route contract;
2. public TOML and fenced `sci3d` instruction payloads contain no hidden
   private-role path or ID fields;
3. a CC-MCP manifest exists, is schema-valid, has no source provenance, has no
   unapproved private role, and enumerates exactly every staged worker file;
4. the CC-MCP agent Dockerfile is asset/server-free and the worker is the sole
   `COPY asset/ /data/` holder.

Example local preflight (keep `require_image_digest=True` for a release image):

```bash
python - <<'PY'
from pathlib import Path
from scripts.harbor.security_contract import validate_task_tree

task_dir = Path("/path/to/generated/task")
print(validate_task_tree(task_dir, require_image_digest=True))
PY
```

Static preflight is not a runtime egress attestation. Official aggregation also
requires the trusted host-side runtime receipt described by the aggregate
security contract.

## v9 acceptance procedure

Use a fresh output directory and a task constructed from the v9 review record
with its true local primary/annotation paths. For each target route:

1. Generate the task tree with `build_one(..., containerized=True, agent_env=...)`.
2. Inspect `environment/asset/`: CC and CC-MCP must contain only allowed
   inputs; a default-private mask or sidecar must be absent.
3. For CC-MCP, inspect `environment/mcp-task-assets.json`; it must list the
   same filtered files and no private role.
4. Check `task.toml` and `instruction.md` for absence of private source paths,
   mask asset IDs, hashes, `mask_path`, and `mask_id` fields.
5. For a mask-dependent CC/CC-MCP task, verify
   `route_asset_visibility.status = "route_ineligible"` and
   `evaluation_eligible = false`.
6. Run `validate_task_tree()` with a digest-pinned image in release mode.
7. For CC2D, require a successful renderer visibility gate before accepting a
   tree; on any renderer/ROI error, retain the fail-closed failure rather than
   substituting an unchecked tree.

## Verified v9 results

The v9 review bundle audit found 25 `med_ct_geometry` tasks and 25
`med_lesion_localization` tasks.

On the real `sv-medi-med_ct_geometry-0016` source pair, generated CC and
CC-MCP trees staged only `ct.nii.gz`; neither public TOML nor instruction
contained `mask_path`. Both trees were marked route-ineligible, and the
CC-MCP manifest listed only `/data/ct.nii.gz`. On the real
`sv-medi-med_lesion_point-0003` source, CC and CC-MCP each staged the one
primary CT and remained eligible. Static preflight passed for all four trees
with `require_image_digest=False` solely because that local canary used
development image tags; a release run must use digest pinning.

The attempted real CC2D geometry canary encountered a host sandbox
`PermissionError` in the NIfTI mask inspection path and correctly failed with
`RenderVisibilityError: medical_roi_plan_unavailable`. It did not produce a
tree and is not recorded as a successful CC2D acceptance. This is the intended
fail-closed behavior; rerun the CC2D acceptance in a renderer-capable runtime.

The implementation regressions covering these boundaries, manifests,
anti-downgrade check, aggregation exclusion, and CC2D metadata isolation pass
in the Harbor adapter test suite.
