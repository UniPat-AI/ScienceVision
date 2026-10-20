# Genesis AI — embodied physics integration

> Status: **partial**. `Genesis_VP_Hard` has landed as a pipeline-aligned
> QA batch with 2 of its 4 tasks (MPM fracture, MPM granular) under
> [`QA-Gen/tasks/Genesis_VP_Hard/`](../../../QA-Gen/tasks/Genesis_VP_Hard/);
> ground truth is `needs_rollout` pending the Genesis solver integration.

## Domain
Physics-driven embodied reasoning: rigid body, MPM (granular / fracture), FEM (cloth).

## Wiring
Followed [`docs/platform/new-domain-template.md`](../../platform/new-domain-template.md). Four touchpoints:
- `crawl_data/genesis/` for scene assets
- `_matched/` registration for the Genesis_VP whitelist
- Viewer manifest under `viewer/datasets/genesis/`
- QA pipeline batches under `QA-Gen/tasks/Genesis_VP_Hard/`

## Tasks unlocked
4 P1/P2 hard tasks:
1. Force-controlled grasp
2. MPM fracture
3. MPM granular cascade
4. FEM cloth

(See [`docs/benchmark/task-families.md`](../../benchmark/task-families.md) → Genesis_VP.)

## Leaderboard
Reserved slots; baselines TBD.
