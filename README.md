# ScienceVision

ScienceVision studies how agents reason about scientific 3D data across architecture, biology, chemistry, mechanical CAD, and medical imaging. This repository provides the project description, dataset and container references, evaluation instructions, and performance data formats.

**Status:** private preparation repository. The selected dataset is the `review-v11` branch, with 250 tasks, 50 per domain. Evaluation source and container packages are also private. No new model benchmark results are published here.

| Resource | Location | Current state |
|---|---|---|
| Project | [Project overview](docs/project.md) | Scope and task composition |
| Dataset | [Dataset guide](docs/dataset.md) | Current dataset: `elorian-ai/ScienceVision`, fixed `review-v11` revision |
| Evaluation | [Baseline guide](docs/evaluation.md) | Source maintained in `UniPat-AI/BabyVision-v2`, fixed commit |
| Containers | [Container guide](docs/containers.md) | Existing private `ghcr.io/unipat-ai/sciencevision-*` packages |
| Performance | [Format and reporting rules](docs/performance.md) | Native harness output schemas and synthetic examples |

## Dataset version

Use the selected existing dataset at the exact revision:

```bash
hf auth login
hf download elorian-ai/ScienceVision \
  --type dataset \
  --revision 0f9e91ed597a49c205960a0f96ff3d4457db682e \
  --local-dir data/review-v11
python scripts/check_dataset.py data/review-v11
```

`review-v11` is a branch. The current `main` branch differs. The download contains about 8.22 GB of logical file contents, including assets, views, and archived trajectories. [Access, file layout, and limitations](docs/dataset.md).

The dataset is a delivery and review bundle with questions, answers, and assets. Its `index.jsonl` is a task and archived-performance index. A unified `review-v11` input export for the pinned harness is not currently supplied; the [baseline guide](docs/evaluation.md) documents the existing interfaces and outputs, not a complete reproduction recipe.

## Versioned references

Machine-readable records are in [`resources/dataset.json`](resources/dataset.json), [`resources/harness.json`](resources/harness.json), and [`resources/images.json`](resources/images.json). They record the current resource locations and scope of verification. Pull containers with their GHCR digest references.

## Repository checks

Use Python 3.10 or later.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-validation.txt
python scripts/validate_repository.py
```

These checks validate documentation links, schemas, references, and example consistency. They do not run model evaluation or prove container runtime compatibility.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md). Repository documentation, schemas, and utility scripts use [Apache-2.0](LICENSE). Dataset assets and container contents have separate terms; the selected dataset does not currently declare a license. See [NOTICE](NOTICE) and the [dataset guide](docs/dataset.md).
