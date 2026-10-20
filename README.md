<p align="center">
  <img src="assets/sciencevision-logo.png" alt="ScienceVision project logo" width="280" />
</p>

# ScienceVision

ScienceVision is a benchmark for **observing and reasoning over scientific 3D data**: 250
expert-authored questions, 50 each in architecture, biology, chemistry, mechanical CAD, and
medical imaging.

Each task hands an agent a question and a 3D source: a building model, a protein, a CT volume, a
CAD assembly, or a crystal. The agent must return a measurement, an identification, or a
comparison. What the benchmark varies is **where the evidence comes from**: the source artifact
and code execution; an inspection tool that changes views, isolates structures and measures
geometry; or a fixed set of rendered images.

Answers are graded by a **deterministic scorer, not a judge model**, field by field against a
declared contract. A task whose scoring contract is missing or invalid is reported as *blocked*,
never as a score of zero. See [the scoring standard](docs/evaluation.md#scoring-standard).

| I want to | Start here |
|---|---|
| get the questions, assets and expert answers | [1. Dataset](#1-dataset) |
| see how twelve models compare | [2. Model comparison](#2-model-comparison) |
| run my own model through the same scorer | [3. Evaluation](#3-evaluation) |
| pull an agent or scorer image | [4. Containers](#4-containers) |
| cite or reuse this | [5. Citing and license](#5-citing-and-license) |

## 1. Dataset

**`v13-250`** holds 250 tasks, 50 per domain, with source assets, rendered views, expert answers
and the recorded expert sessions they were selected on. It is gated: authenticate with an account
that has been granted access.

```bash
hf auth login
hf download UnipatAI/ScienceVision \
  --type dataset \
  --revision <commit> \
  --local-dir data/sciencevision
```

Download a **fixed revision, not a branch**. `<commit>` and the inventory that goes with it are
recorded in [`resources/dataset.json`](resources/dataset.json); the numbers in this repository
are tied to that snapshot.

Read the question and answer from `<domain>/<task_id>/normalized.json`. It is the one record
every task has. `answer.json` carries the expert ground truth in the form the scorer reads.

`v13-250` replaces 13 of `v12-250`'s tasks, keeping every sub-domain count unchanged. The
[dataset guide](docs/dataset.md) covers the layout, the records, and the four declared
limitations. Read them before quoting a score: two of them change what a per-task comparison
means.

## 2. Model comparison

Twelve models over the same 250 questions under three access settings. Mean `answer_score` on
`[0, 1]`, ineligible trials counted zero; the two 3D settings average three runs per question,
the six-view setting is a single run.

![Mean answer score under cc-3d and cc-mcp](assets/leaderboard-cc3d-vs-ccmcp.png)

*`cc-3d` gives the agent the source artifact and code execution. `cc-mcp` adds the inspection
tools: change views, isolate structures, inspect slices, measure geometry.*

| # | Model | `cc-3d` | `cc-mcp` | `cc-2d-6` |
|--:|---|---:|---:|---:|
| 1 | GPT-6 Astra | 0.4403 | 0.4396 | 0.1789 |
| 2 | GPT-6.1 Sol | 0.4074 | 0.4292 | 0.1406 |
| 3 | Claude Fable 5.1 | 0.3860 | 0.3900 | 0.2174 |
| 4 | Claude Opus 5.5 | 0.3771 | 0.3627 | 0.1783 |
| 5 | Gemini 4 Argon | 0.3565 | 0.3625 | 0.1006 |
| 6 | Qwen3.8 Max 0902 | 0.3199 | 0.3099 | 0.1947 |
| 7 | Qwen3.8 Flash | 0.2895 | 0.2968 | 0.1913 |
| 8 | Kimi K3 | 0.2834 | 0.2693 | 0.1653 |
| 9 | GLM-5.3 Flash | 0.2206 | 0.2283 | 0.1179 |
| 10 | DeepSeek V4 Flash | 0.2231 | 0.2211 | 0.1054 |
| 11 | Doubao Seed Evolving | 0.1928 | 0.1984 | 0.0887 |
| 12 | Hunyuan-4 Preview | 0.1283 | 0.1426 | 0.0673 |

Under both 3D settings the five closed-source models place ahead of every open-source model. No
model reaches 0.60, so nothing is saturated. The six-view setting orders differently: Claude
Fable 5.1 leads it.

![Mean answer score under cc-2d-6](assets/leaderboard-cc2d6.png)

*Six rendered views, a single run. Evidence has to come from the supplied images.*

![Per-domain mean@3 under cc-3d](assets/leaderboard-per-domain.png)

*Each column carries its own scale, whose maximum sits under the discipline name, so bar length
compares models within a discipline, not disciplines with each other.*

**Two things to know before quoting any of this.** The comparison is a *splice*: each model's row
joins a carried-over segment with a re-run segment, and those segments did not come from one
harness. And Gemini 4 Argon covers two runs rather than three. The
[comparison notes](docs/leaderboard.md) record the segment sources, the question-set provenance,
and the version drift in full, and state the resulting limit plainly: *equal coverage does not
establish a controlled comparison*.

## 3. Evaluation

Seven access settings, of which the paper reports three:

| Setting | What the agent may use | Reported |
|---|---|---|
| **3D + Scaffold** | Source artifacts and code execution | Yes |
| **3D + Scaffold + Vision MCP** | The same, plus Vision MCP inspection and measurement tools | Yes |
| **3D + Vision MCP** | Vision MCP tools only; source artifacts withheld | No |
| **2D + Scaffold ×1 / ×3 / ×6 / ×12** | That many fixed rendered views; no source artifacts | ×6 only |

The harness is vendored in this repository (`scripts/harbor/`, `harbor_adapters/`,
`sci3d_bench/`, `sci3d_agent/`, `scorer_bundle/`). It installs as an extra of this
distribution. It needs Python 3.12 or later, because Harbor requires it, and a
Docker-capable host. The `harbor` extra pins Harbor `v0.20.0`. A deterministic replay
sandbox, including the dataset-to-tree builder and the archived-answer scorer, lives in
[`sandbox/`](sandbox/README.md).

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[harbor]"
python -m scripts.harbor.run_end2end --help
python -m scripts.harbor.qa_gen_to_tasks --help
```

### Scoring standard

Scoring is deterministic and **fail-closed**:

- A task must set `evaluation_eligible = true`. Otherwise the result is **blocked, not zero**.
  Blocked trials are reported separately and never mixed into a mean as a model failure.
- An answer is **one JSON object**, or one `SCI3D_FINAL_JSON:` terminal marker carrying it. Prose
  is not parsed. Types survive: `3` and `"3"` are different answers.
- Every field declares a comparator. Seventeen are defined, from `exact` and `integer_equal`
  through `abs_tolerance` and `vector_abs_tolerance` to `voxel_member_of_set`, where the mask
  itself is the tolerance region.
- `answer_score` is `1.0` only when **every field matched**. Reports use `mean@3` (per-task mean
  over three runs, then averaged over tasks) and `pass@3`.

The scorer image records a manifest whose self-hash the CLI checks before scoring, so a modified
scorer cannot report as the pinned one. [The full standard](docs/evaluation.md#scoring-standard)
lists every comparator, the three supported contracts, the task contract, and what a run receipt
must record.

## 4. Containers

Images live under **`ghcr.io/unipat-ai`**. Pick the runtime that matches the route you are
running; scoring always uses the scorer the task contract names.

| Route | Codex | Claude Code |
|---|---|---|
| Raw task files | `sciencevision-codex` | `sciencevision-claude-code` |
| Rendered views | `sciencevision-codex-2d` | `sciencevision-claude-code-2d` |
| Bundled MCP tools | `sciencevision-codex-mcp` | `sciencevision-claude-code-mcp` |
| Client for an external HTTP MCP worker | `sciencevision-codex-mcp-client` | `sciencevision-claude-code-mcp-client` |

The external HTTP route also needs `sciencevision-mcp-worker`. Pull by digest, because a tag can
be repointed.

```bash
docker login ghcr.io
docker pull ghcr.io/unipat-ai/sciencevision-codex-mcp@sha256:852f66cc924105c61e5efe6f85e0b5b389e01c416f7e6c8a09fbda0a225a1c98
docker pull ghcr.io/unipat-ai/sciencevision-scorer@sha256:b2d62b237db316b4e668b146c1195bf52d77c6fd4215b915672c73849d0fbeb3
```

A package name is a **deployment layout, not an access setting**. `-mcp` does not tell you
whether source artifacts were withheld. The [container guide](docs/containers.md) has every
version with its digest, the bundled-versus-external distinction, and the scorer versions.

## 5. Citing and license

The project has a paper; its citation will be published here and on the
[project page](https://unipat.ai/blog/ScienceVision) once settled. Until then, please describe
results by the setting and scorer version you used rather than by a single headline number. The
[comparison notes](docs/leaderboard.md) explain why that matters here.

Documentation, schemas, utility scripts, and the evaluation implementation in this repository are
**MIT** ([LICENSE](LICENSE)). That license does **not** cover the dataset or the third-party
assets inside it: the dataset declares no license of its own. Container contents keep their own.
See [NOTICE](NOTICE) and the [dataset guide](docs/dataset.md#interpretation-and-data-terms).

Contributions are described in [CONTRIBUTING.md](CONTRIBUTING.md). The
[project overview](docs/project.md) records what is still required before this release can be
read outside the organisation.
