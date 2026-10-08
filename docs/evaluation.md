# Evaluation baselines

## Implementation and access

The implementation remains in [`UniPat-AI/BabyVision-v2`](https://github.com/UniPat-AI/BabyVision-v2/tree/b47f13ac5abb54b9e287ff3131ae7e29771ec53e), pinned to `b47f13ac5abb54b9e287ff3131ae7e29771ec53e` from `eval-baseline`. It is private; access to this documentation repository alone does not grant access to that dependency.

Start from the ScienceVision repository root. These commands enter its `eval-baseline` subdirectory; subsequent harness commands run there.

```bash
gh repo clone UniPat-AI/BabyVision-v2 eval-baseline
cd eval-baseline
git checkout --detach b47f13ac5abb54b9e287ff3131ae7e29771ec53e
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[harbor]"
python -m scripts.harbor.run_end2end --help
python -m scripts.harbor.qa_gen_to_tasks --help
```

The Python project declares Python 3.10 or later. Use Python 3.12 or later for the Harbor extra: [Harbor 0.13.0 package metadata](https://pypi.org/project/harbor/0.13.0/) specifies that minimum. The upstream dependency is `harbor>=0.13`, so record the installed version and dependency lock for every real run. Run the harness on a Docker-capable host with the rendering dependencies required by the selected assets. The pinned container inventory is `linux/amd64`. This repository has not executed the complete benchmark.

The default scorer is the GHCR copy of `server-20260930-h20`, pinned at `ghcr.io/unipat-ai/sciencevision-scorer@sha256:d07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062`. [Upstream PR #128](https://github.com/UniPat-AI/BabyVision-v2/pull/128) changed the registry reference while preserving that scorer release. Set `SCI3D_SCORER_IMAGE` explicitly when a prepared task package requires a different scorer, and record the selected digest.

## Paper settings and harness mapping

Use the paper's setting names in reports. **3D + Scaffold** provides source artifacts and code execution. **3D + Scaffold + Vision MCP** retains that source access and adds the Vision MCP interface. **3D + Vision MCP** is the MCP-only setting: source artifacts are withheld and observations come through tools. **2D + Scaffold ×1/×3/×6/×12** supplies the corresponding number of fixed rendered views with the agent scaffold, without source artifacts.

The [driver source](https://github.com/UniPat-AI/BabyVision-v2/blob/b47f13ac5abb54b9e287ff3131ae7e29771ec53e/scripts/harbor/run_end2end.py) uses the internal identifiers below. Keep those identifiers in CLI arguments and native result records.

| Paper setting | Internal route ID | Driver option | Agent environment |
|---|---|---|---|
| **3D + Scaffold** | `cc` | `--matrix six` | `codex` |
| **3D + Scaffold + Vision MCP** | `ccmcp` | `--matrix six` | `codex-mcp` |
| **3D + Vision MCP** | `3d-mcp` | `--matrix 3d-mcp` | `codex-mcp` |
| **2D + Scaffold ×1** | `cc2d-1` | `--matrix six` | `codex-2d` |
| **2D + Scaffold ×3** | `cc2d-3` | `--matrix six` | `codex-2d` |
| **2D + Scaffold ×6** | `cc2d-6` | `--matrix six` | `codex-2d` |
| **2D + Scaffold ×12** | `cc2d-12` | `--matrix six` | `codex-2d` |

`--matrix six` is the driver's default and launches the six listed non-MCP-only routes together. **3D + Vision MCP** uses a separate `--matrix 3d-mcp` invocation with the shell tool disabled. The paper reports results for **3D + Scaffold**, **3D + Scaffold + Vision MCP**, and **2D + Scaffold ×6**; matched results for the remaining settings are not reported.

The mapping is not a permissions attestation. The pinned external HTTP MCP profile builds a source-free agent container and keeps raw assets in the separate worker for both MCP route IDs. Its shell-enabled route alone therefore does not establish the direct source access required by **3D + Scaffold + Vision MCP**. Reproducing a paper setting requires a task contract and runtime profile that enforce its intended access, with evidence recorded in the run receipt. This repository does not supply or validate that complete reconstruction.

`--matrix t0-probe` runs the harness's text-only diagnostic, with internal route ID `codex-text`. It is not one of the seven paper settings. Legacy Claude Code environment names also exist; they are not the current driver's default matrix.

## Dataset-to-harness boundary

The review bundle and the harness use different formats. `normalized.json` provides question and answer content. The task builder requires QA records containing `task_id`, `instruction`, asset references, `answer_schema`, `ground_truth`, a scorer declaration, and the applicable tool/evaluation contract. The code derives formal eligibility from those contracts.

Neither `index.jsonl` nor an unmodified `normalized.json` is a launch-ready task record. A unified input export for the selected dataset snapshot and pinned harness is not currently supplied. This guide records the existing interfaces and output format; it is not a complete reproduction recipe. In particular, asset selection, multi-asset roles, camera state, tolerances, and eligibility must be preserved from an approved task contract; guessing them changes the benchmark. The fixed harness includes the recent comparator fixes, but end-to-end compatibility with the selected snapshot has not been established here.

For an already prepared and validated QA-record file at `prepared/tasks.jsonl` inside the harness checkout, the task builder is shown below. The `../data/sciencevision` path refers to the dataset downloaded in the [quickstart](../README.md#1-quickstart).

```bash
python -m scripts.harbor.qa_gen_to_tasks \
  --jsonl prepared/tasks.jsonl \
  --asset-root ../data/sciencevision \
  --out work/tasks-3d-scaffold \
  --containerized --agent-env codex
```

The driver accepts that format with `--pre-resolved`. `CODEX_MODEL` is required, as is a valid model credential configured through the runner's supported environment or authentication file. `--dry-run` still builds task directories and renders views; it does not call models or launch Harbor jobs.

```bash
python -m scripts.harbor.run_end2end \
  --tasks prepared/tasks.jsonl --pre-resolved \
  --matrix six \
  --set-name ScienceVision \
  --asset-root ../data/sciencevision \
  --out-root work/qa --jobs-root work/smoke \
  --limit 1 --dry-run
```

For **3D + Vision MCP**, use `--matrix 3d-mcp` in the same command and a separate `--jobs-root work/smoke-mcp-only`. This selects the tools-only invocation; it does not replace the contract and access checks above.

Use a fresh jobs directory for each run. Preserve each route's task IDs and its explicit rendering-exclusion receipt. Resolve the local agent and worker aliases against the selected release in the [container guide](containers.md), verify their image identities, and retain that mapping in the run receipt. Some pinned shell runners check the exact local `FROM` alias; replacing it with a registry address alone can fail preflight. A bundled MCP image and a client-only wrapper plus worker are distinct runtimes.

## Scoring and outputs

The primary reward is the deterministic `answer_score`. Process compliance and optional shadow judges are separate diagnostics. A task without a valid supported scoring contract is ineligible; it must not silently become a model error or a regular scored zero.

The collector writes per-task run records and `runs_leaderboard.json`. The aggregator supports `flat`, `domain_equal`, and explicit `weighted` modes. Its official path requires positive process-gate and runtime-attestation evidence. Diagnostic aggregation must be labelled accordingly. See [performance.md](performance.md) for the native fields, coverage, and missing-data rules.

[Upstream PR #127](https://github.com/UniPat-AI/BabyVision-v2/pull/127) reports 250 ground-truth self-consistency checks and scorer tests. Those are implementation checks, not model results, and they have not been independently rerun for this repository.
