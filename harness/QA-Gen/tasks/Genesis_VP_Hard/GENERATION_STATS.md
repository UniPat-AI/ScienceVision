# Genesis_VP_Hard — Generation Stats (v1)

Deterministic build via `build_records.py`. No LLM calls.

## Counts

| Metric | Value |
|---|---|
| Set version | v1 |
| Generated total | 2 |
| Kept total | 2 |
| Filtered total | 0 |
| Kept ratio | 100.0% |
| Token usage | 0 |
| Cost (USD) | 0.00 |

## By physics class

| Physics class | Count | Scene | Answer kind |
|---|---|---|---|
| mpm_fracture | 1 | cut_dragon | boolean |
| mpm_granular | 1 | sand_wheel | numeric_range |

## Ground-truth status

Both records: `needs_rollout` — a genesis-specific extension of the
pipeline §6 status enum (`auto_seed` / `awaiting_expert` /
`needs_expert_annotation`). The answer is a deterministic function of a
Genesis solver rollout (see each record's `ground_truth.repro`), so no
value can be seeded by inspection: `ground_truth.value = null` and
`release_gate = blocked` until the rollout lands.

## Scope (v1)

Ships the **2** tasks present in the source whitelist
`embodied/qa/genesis_vp.jsonl`. The Genesis_VP family is described in
`docs/benchmark/task-families.md` as four P1/P2 tasks (force control,
MPM fracture, MPM granular cascade, FEM cloth); the two aspirational
tasks (force-controlled grasp, FEM cloth) are **not** authored here and
land when their source questions do.

## How to reproduce

```bash
python3 QA-Gen/tasks/Genesis_VP_Hard/build_records.py
python3 tools/build_qagen_index.py   # or hand-edit viewer/qagen_index.json
```
