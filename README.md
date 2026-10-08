<p align="center">
  <img src="assets/sciencevision-logo.png" alt="ScienceVision project logo" width="320" />
</p>

# ScienceVision

ScienceVision evaluates how agents reason about scientific 3D data across architecture, biology, chemistry, mechanical CAD, and medical imaging. This repository brings together the dataset, evaluation baselines, container packages, and performance formats.

[1. Quickstart](#1-quickstart) · [2. Evaluation](#2-evaluation) · [3. Benchmark](#3-benchmark)

## 1. Quickstart

The ScienceVision dataset contains **250 tasks across five domains**, with 50 tasks per domain. The bundle includes questions, answers, scientific assets, rendered views, and archived trajectories. Start by inspecting its task index, then download the assets needed for your work. See [Benchmark](#3-benchmark) for task coverage.

**Access:** this repository, the evaluation source, and GHCR packages are private. The dataset is hosted at [`elorian-ai/ScienceVision`](https://huggingface.co/datasets/elorian-ai/ScienceVision/tree/0f9e91ed597a49c205960a0f96ff3d4457db682e); it is private and gated. Use accounts authorized for each resource. Signing in alone does not grant dataset or package access.

### Set up and inspect the task index

Use Python 3.12 and an authenticated GitHub CLI (`gh`). The setup also installs the [Hugging Face CLI](https://huggingface.co/docs/huggingface_hub/en/guides/cli).

```bash
gh repo clone UniPat-AI/ScienceVision
cd ScienceVision
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-validation.txt huggingface_hub
python scripts/validate_repository.py
```

Download the small metadata files first:

```bash
hf auth login
hf download elorian-ai/ScienceVision README.md index.jsonl \
  --type dataset \
  --revision 0f9e91ed597a49c205960a0f96ff3d4457db682e \
  --local-dir data/sciencevision
python scripts/check_dataset.py data/sciencevision --index-only
```

The checks validate repository links, schemas, examples, the pinned index hash, and task counts. They do not call models or run the benchmark.

### Download the task bundle

For the complete questions, answers, assets, and archived runs, download the same fixed revision. Its logical file contents total about **8.22 GB**.

```bash
hf download elorian-ai/ScienceVision \
  --type dataset \
  --revision 0f9e91ed597a49c205960a0f96ff3d4457db682e \
  --local-dir data/sciencevision
python scripts/check_dataset.py data/sciencevision
```

Read each task's common question and answer from `<domain>/<task_id>/normalized.json`. Asset paths vary by task; the [dataset guide](docs/dataset.md) describes both layouts. The check verifies common task records, not every asset checksum or harness compatibility. Use the fixed commit above because `main` has different task IDs and contents.

## 2. Evaluation

**Current scope:** a unified task export for the selected dataset snapshot and pinned harness is not yet supplied. The dataset contains questions, answers, and assets, but neither `index.jsonl` nor an unmodified `normalized.json` is runnable harness input. The steps below provide the existing images and evaluation interfaces; complete end-to-end reproduction of this snapshot still requires validated task and scoring contracts.

### Evaluation settings

The paper defines these seven access settings:

| Setting | Intended agent access | Results in the paper |
|---|---|---|
| **3D + Scaffold** | Source artifacts and code execution | Reported |
| **3D + Scaffold + Vision MCP** | The same source access and code execution, plus Vision MCP inspection and measurement tools | Reported |
| **3D + Vision MCP** | Vision MCP tools only; source artifacts withheld | Not reported |
| **2D + Scaffold ×1** | One fixed rendered view with the agent scaffold; no source artifacts | Not reported |
| **2D + Scaffold ×3** | Three fixed rendered views with the agent scaffold; no source artifacts | Not reported |
| **2D + Scaffold ×6** | Six fixed rendered views with the agent scaffold; no source artifacts | Reported |
| **2D + Scaffold ×12** | Twelve fixed rendered views with the agent scaffold; no source artifacts | Not reported |

The paper reports results for the three indicated settings. The other settings do not yet have reported results matched to the same task versions, model configuration, and scoring procedure. Setting names describe intended access conditions; they do not attest to permissions in historical runs or current image profiles. See the [harness mapping and access limitations](docs/evaluation.md#paper-settings-and-harness-mapping) for internal route IDs and CLI options.

### Container packages

Images are hosted under **`ghcr.io/unipat-ai`**. Choose the runtime that matches the prepared task contract:

| Runtime | Codex package | Claude Code package |
|---|---|---|
| Raw task files | [sciencevision-codex](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex) | [sciencevision-claude-code](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code) |
| Rendered views | [sciencevision-codex-2d](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-2d) | [sciencevision-claude-code-2d](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-2d) |
| Bundled MCP tools | [sciencevision-codex-mcp](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp) | [sciencevision-claude-code-mcp](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp) |
| Client for an external HTTP MCP worker | [sciencevision-codex-mcp-client](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-codex-mcp-client) | [sciencevision-claude-code-mcp-client](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-claude-code-mcp-client) |

The external HTTP route also needs a compatible [sciencevision-mcp-worker](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-mcp-worker). Scoring uses [sciencevision-scorer](https://github.com/orgs/UniPat-AI/packages/container/package/sciencevision-scorer), with the version selected by the task contract. The [container guide](docs/containers.md) lists release tags, scorer versions, and the archived experimental runtime.

All listed packages are private `linux/amd64` images. Authenticate with package read access and pull immutable GHCR digests. For example, the raw-file Codex runtime and the pinned harness's default scorer:

```bash
docker login ghcr.io
docker pull ghcr.io/unipat-ai/sciencevision-codex@sha256:43b4bf787e205762368aee65588b7a24bac748f267488769152209632efba04c
docker pull ghcr.io/unipat-ai/sciencevision-scorer@sha256:d07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062
```

The pinned source also uses local development aliases. Follow the [runtime-selection instructions](docs/containers.md#runtime-selection) before launching a route: bundled MCP images and client-plus-worker layouts are distinct, and some runner preflights check exact local aliases. Pulling an image alone does not configure the harness.

### Evaluation harness

The implementation is maintained in [`UniPat-AI/BabyVision-v2`](https://github.com/UniPat-AI/BabyVision-v2/tree/b47f13ac5abb54b9e287ff3131ae7e29771ec53e), pinned to commit `b47f13ac5abb54b9e287ff3131ae7e29771ec53e` from `eval-baseline`. With access to that repository, run the following from the ScienceVision repository root to install the Harbor extra and inspect its entry points:

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

Real runs require a Docker-capable host, asset-specific rendering dependencies, approved task records, a model configuration, and valid model credentials. The [evaluation guide](docs/evaluation.md) documents the required task fields, task-builder command, one-task dry run, and result collection. A dry run still builds task directories and renders views; it does not call models or launch Harbor jobs.

## 3. Benchmark

### Task coverage

| Domain | Tasks | Example subject matter |
|---|---:|---|
| Architecture | 50 | Geometry, alignment, clearance, and dimensional checks |
| Biology | 50 | Molecular arrangement, contacts, and spatial selection |
| Chemistry | 50 | Molecular geometry, torsion, occlusion, and crystal structure |
| Mechanical CAD | 50 | Parts, assembly composition, properties, and motion |
| Medical imaging | 50 | Volumetric geometry, slice comparisons, and spatial localization |
| **Total** | **250** | |

Each task connects a question to scientific source assets and an answer or scoring contract. Equal domain counts do not imply equal difficulty. See the [project overview](docs/project.md) and [dataset limitations](docs/dataset.md#interpretation-and-data-terms).

### Scoring

The [evaluation settings](#evaluation-settings) vary the evidence and tools available to the agent. The primary reward is the deterministic `answer_score`; process compliance and optional shadow judges are reported separately. Every reported mean needs its denominator and coverage, including missing, error, incomplete, blocked, and ungated trials.

### Performance records

| Record | Purpose |
|---|---|
| [`run.json`](schemas/run-metadata.schema.json) | Dataset, harness, model, image digests, task scope, artifact hashes, and coverage |
| [`runs_leaderboard.json`](schemas/performance.schema.json) | Native per-task results and collector summaries |
| [`index.jsonl`](schemas/dataset-index.schema.json) | Historical task selection and archived outcomes shipped with the dataset, retaining their original field names |

This repository publishes **synthetic format examples only**, not new model benchmark results. Archived dataset scores have mixed run provenance, and scorer ground-truth self-tests are not model performance. See the [performance contract](docs/performance.md) and [annotated examples](performance/examples/README.md) for score semantics, eligibility, aggregation, and missing-data rules.

Exact versions and verification scope are recorded in [`resources/dataset.json`](resources/dataset.json), [`resources/harness.json`](resources/harness.json), and [`resources/images.json`](resources/images.json).

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md). Repository documentation, schemas, and utility scripts use [Apache-2.0](LICENSE). Dataset assets and container contents have separate terms; the selected dataset does not currently declare a license. See [NOTICE](NOTICE) and the [dataset guide](docs/dataset.md).
