# Expert Review Packet Generator

## Purpose

`create_expert_review_packet.py` turns each accepted task in `final_tasks.jsonl` into a
per-task **expert review packet** — a structured JSON form that domain, asset, and scorer
experts fill in to validate task quality before benchmark release.

## Usage

```bash
python scripts/create_expert_review_packet.py --run-dir data/runs/<run_name> [--out <dir>]
```

- `--run-dir` (required): path to a run directory that contains `final_tasks.jsonl`.
- `--out` (optional): output directory for packets. Default: `<run-dir>/expert_review_packet/`.

Writes one file per task: `<out>/<task_id>.json`.  
Prints `Wrote N packets to <out>` on success; exits with status 1 if the run dir or
`final_tasks.jsonl` is missing.

## Packet schema (top-level keys)

| Key | Description |
|-----|-------------|
| `task_id` | Task identifier |
| `title` | Task title |
| `domain` | Scientific domain |
| `v_class` | Vision-class tag (V1–V6) |
| `difficulty` | easy / medium / hard |
| `instruction` | Task instruction text |
| `asset` | Asset metadata + `exists_on_disk` + `viewer_url` (if viewer is set); `viewer_url_note` instead of `viewer_url` when viewer is missing/empty/null (mutually exclusive) |
| `terminal_answer` | Proposed answer spec (verbatim from task) |
| `scorer` | Scorer config (verbatim from task) |
| `validation_record` | Auto-check results + release_gate (verbatim from task) |
| `expert_checks` | **Six fixed checks** — experts fill `decision` + `comment` per check |
| `required_reviewer_roles` | Roles that must sign off |
| `min_approvals` | Minimum approvals required |
| `decision` | Top-level verdict slot (`"pending"` initially) |
| `notes` | Free-text reviewer notes (empty initially) |

## How experts fill a packet

For each entry in `expert_checks`, set:
- `decision`: one of `"approve"`, `"revise"`, or `"reject"`
- `comment`: brief justification (optional but encouraged)

Then set the top-level:
- `decision`: `"approve"` / `"revise"` / `"reject"` (overall verdict)
- `notes`: any free-text observations

The six check keys (in order):
1. `question_scientifically_valid`
2. `asset_matches_question`
3. `terminal_answer_correct`
4. `gold_evidence_sufficient`
5. `scorer_tolerance_reasonable`
6. `terminology_precise`

## Phase note

This is **Phase 1** of expert validation — packet generation only.
Tooling to ingest filled packets back into `validation_record` and update
`ground_truth.status` is a later task.
