# MedicalKAxisPilot — Generation Stats (v3, filtered)

Deterministic build via `build_records.py`. No LLM calls.

## Counts

| Metric | Value |
|---|---|
| Set version | v3 |
| Generated total | 4 |
| Kept total | 4 |
| Filtered total | 0 |
| Kept ratio | 100.0% |
| Token usage | 0 |
| Cost (USD) | 0.00 |

## By K-class

| K-class | Count | Anchored cases |
|---|---|---|
| K2 | 1 | s0146 |
| K3 | 1 | s0212 |
| K5 | 2 | s0212, s1349 |

## Ground-truth status (v3)

All 4 records: `auto_seed` — value derived deterministically from the
source `mask_text`. Each record's `ground_truth.value` matches the
task's `answer_schema`. Release gate stays `blocked` until expert
review confirms the auto_seed values.

## v2 → v3 delta

- Dropped 4 records: K1-0001, K1-0002, K1-0003, K4-0006.
- Filter criterion: 3D viewing must be **load-bearing** for the answer
  (not just garnish). See REVIEW.md §Filter criterion.
- All retained records (K2, K3, K5) genuinely require viewer
  interaction (window-level intensity reading, partial-volume
  topology judgement, orthogonal-slice confirmation).
- Task IDs preserved from v2 — no renumbering to 0001–0004.

## How to reproduce

```bash
python3 QA-Gen/tasks/MedicalKAxisPilot/build_records.py
python3 tools/build_qagen_index.py
```
