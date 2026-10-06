# Evaluation baselines

## Implementation and access

The implementation remains in [`UniPat-AI/BabyVision-v2`](https://github.com/UniPat-AI/BabyVision-v2/tree/c7280352fa626bd74712b95f804e59cf4bded816), pinned to `c7280352fa626bd74712b95f804e59cf4bded816` from `eval-baseline`. It is private; access to this documentation repository alone does not grant access to that dependency.

```bash
gh repo clone UniPat-AI/BabyVision-v2 eval-baseline
cd eval-baseline
git checkout --detach c7280352fa626bd74712b95f804e59cf4bded816
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[harbor]"
python -m scripts.harbor.run_end2end --help
python -m scripts.harbor.qa_gen_to_tasks --help
```

The Python project declares Python 3.10 or later. Use Python 3.12 or later for the Harbor extra: [Harbor 0.13.0 package metadata](https://pypi.org/project/harbor/0.13.0/) specifies that minimum. The upstream dependency is `harbor>=0.13`, so record the installed version and dependency lock for every real run. Run the harness on a Docker-capable host with the rendering dependencies required by the selected assets. The pinned container inventory is `linux/amd64`. This repository has not executed the complete six-route benchmark.

## Routes in the current driver

The [driver source](https://github.com/UniPat-AI/BabyVision-v2/blob/c7280352fa626bd74712b95f804e59cf4bded816/scripts/harbor/run_end2end.py) defines these default Codex routes:

| Label | Access | Agent environment |
|---|---|---|
| `cc` | Raw task files and shell/Python tools | `codex` |
| `ccmcp` | Task access through the configured shell and MCP route | `codex-mcp` |
| `cc2d-1` | One rendered view | `codex-2d` |
| `cc2d-3` | Three rendered views | `codex-2d` |
| `cc2d-6` | Six rendered views | `codex-2d` |
| `cc2d-12` | Twelve rendered views | `codex-2d` |

`--matrix 3d-mcp` runs a separate MCP-only route with the shell tool disabled. `--matrix t0-probe` runs a text-only diagnostic. These are separate matrices. Legacy Claude Code environment names also exist; they are not the current driver's default six-route matrix.

## Dataset-to-harness boundary

The review bundle and the harness use different formats. `normalized.json` provides question and answer content. The task builder requires QA records containing `task_id`, `instruction`, asset references, `answer_schema`, `ground_truth`, a scorer declaration, and the applicable tool/evaluation contract. The code derives formal eligibility from those contracts.

Neither `index.jsonl` nor an unmodified `normalized.json` is a launch-ready task record. A unified `review-v11` input export for this pinned harness is not currently supplied. This guide records the existing interfaces and output format; it is not a complete reproduction recipe. In particular, asset selection, multi-asset roles, camera state, tolerances, and eligibility must be preserved from an approved task contract; guessing them changes the benchmark. The fixed harness includes the recent comparator fixes, but end-to-end compatibility with the selected `review-v11` snapshot has not been established here.

For an already prepared and validated QA-record file, the task builder is:

```bash
python -m scripts.harbor.qa_gen_to_tasks \
  --jsonl prepared/tasks.jsonl \
  --asset-root data/review-v11 \
  --out work/tasks-cc \
  --containerized --agent-env codex
```

The six-route driver accepts that format with `--pre-resolved`. `CODEX_MODEL` is required, as is a valid model credential configured through the runner's supported environment or authentication file. `--dry-run` still builds task directories and renders views; it does not call models or launch Harbor jobs.

```bash
python -m scripts.harbor.run_end2end \
  --tasks prepared/tasks.jsonl --pre-resolved \
  --set-name ScienceVision-review-v11 \
  --asset-root data/review-v11 \
  --out-root work/qa --jobs-root work/smoke \
  --limit 1 --dry-run
```

Use a fresh jobs directory for each run. Preserve each route's task IDs and its explicit rendering-exclusion receipt. Before launching, replace generated unqualified image references with the compatible digest-pinned agent, worker, and scorer references in the [container guide](containers.md). A bundled MCP image and an agent-only wrapper plus worker are distinct runtimes.

## Scoring and outputs

The primary reward is the deterministic `answer_score`. Process compliance and optional shadow judges are separate diagnostics. A task without a valid supported scoring contract is ineligible; it must not silently become a model error or a regular scored zero.

The collector writes per-task run records and `runs_leaderboard.json`. The aggregator supports `flat`, `domain_equal`, and explicit `weighted` modes. Its official path requires positive process-gate and runtime-attestation evidence. Diagnostic aggregation must be labelled accordingly. See [performance.md](performance.md) for the native fields, coverage, and missing-data rules.

[Upstream PR #127](https://github.com/UniPat-AI/BabyVision-v2/pull/127) reports 250 ground-truth self-consistency checks and scorer tests. Those are implementation checks, not model results, and they have not been independently rerun for this repository.
