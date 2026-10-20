# Genesis_VP_Hard — Review (v1)

First BV2-side batch of the **Genesis_VP** embodied-physics family: tasks
whose answer is only recoverable by running a deterministic Genesis
simulation rollout. Unlike the medical batches (answer by *viewing*), a
model here must **act on** the scene — apply a force, actuate a joint,
then roll the solver forward — before it can answer. See
[`docs/benchmark/task-families.md`](../../../docs/benchmark/task-families.md)
→ Genesis_VP and the pipeline contract in
[`docs/qa-gen/pipeline-overview.md`](../../../docs/qa-gen/pipeline-overview.md).

## Qualification criterion

A task qualifies for this set only when the answer is **irreducible to a
static view or metadata** — it depends on the trajectory of an MPM/FEM
rollout. Both records pass: fracture connectivity and granular
redistribution are history-dependent solver outcomes, not properties of
the rest pose.

## Records (v1)

| task_id | physics_class | scene | question | answer_kind | GT status |
|---|---|---|---|---|---|
| sv-embodied-genesis-0001 | mpm_fracture | cut_dragon | Under a 5 N transverse force at the marked vertebra, does the dragon fracture into two disconnected pieces within 200 steps? | boolean | `needs_rollout` |
| sv-embodied-genesis-0002 | mpm_granular | sand_wheel | After a 90° clockwise wheel rotation, what fraction of the sand pile stays within 0.1 m of the rotation axis? | numeric_range | `needs_rollout` |

## `needs_rollout` — why no seed answer

The medical batches ship `auto_seed` values derived deterministically
from `mask_text`. Genesis answers have no such shortcut: the correct
value is *defined* by the solver rollout in each record's
`ground_truth.repro` block (solver, `dt`, `substeps`, `rollout_steps`,
`grid_density`, `deterministic=true`). Seeding a guess would be
fabricating a physics result. So each record ships:

- `ground_truth.value = null`
- `ground_truth.status = "needs_rollout"`
- `validation_record.release_gate = "blocked"`

Promotion path: run the Genesis rollout with the recorded `repro`
config → populate `ground_truth.value` → expert confirms → `release_gate
= pass`.

## What reviewers should check

1. **Repro fidelity.** Confirm each `repro` block matches the source
   `embodied/qa/genesis_vp.jsonl` and is sufficient to reproduce the
   rollout deterministically (seed, solver version pinning).
2. **Scene availability.** `asset.exists_on_disk` is computed at build
   time against `viewer/datasets/genesis/*.glb`. The glb is a *viewer
   proxy*; the rollout needs the full simulation scene (collider mesh,
   material params) — track whether that scene ships alongside the glb.
3. **Scorers are stubs.** `genesis_fracture_connectivity` and
   `genesis_granular_mass_fraction` are referenced but not yet
   implemented. Records stay `release_gate = blocked` until both the
   rollout and scorer land (pipeline §7.3).
4. **Tool surface.** `sv_genesis_*` tools (`load_scene`, `apply_force`,
   `actuate`, `rollout`, `measure`, ...) are the assumed action API. If
   the MCP tool names differ when they land, update `tool_policy`.

## Scope note

The Genesis_VP family targets 4 P1/P2 tasks. This v1 batch ships the 2
that exist in the source whitelist (MPM fracture, MPM granular). Force
control and FEM cloth are deferred.

## Provenance

- Generator: `QA-Gen/tasks/Genesis_VP_Hard/build_records.py` (v1;
  deterministic; 0 tokens, 0 USD).
- Source: `embodied/qa/genesis_vp.jsonl` (ModelScope
  `BabyVision-v2-Datasets`, `embodied/qa/`).
- Design contract: `docs/qa-gen/pipeline-overview.md`.
