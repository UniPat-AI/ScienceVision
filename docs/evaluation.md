# Evaluation

A ScienceVision run answers the same 250 questions under one **access setting**. The setting
decides what evidence the agent may obtain; the scorer decides what counts as correct. Both are
fixed, and a result is only comparable to another result at the same setting and scorer contract.

## Evaluation settings

| Setting | Intended agent access | Reported in the paper |
|---|---|---|
| **3D + Scaffold** | Source artifacts and code execution | Yes |
| **3D + Scaffold + Vision MCP** | The same source access and code execution, plus Vision MCP inspection and measurement tools | Yes |
| **3D + Vision MCP** | Vision MCP tools only; source artifacts withheld | No |
| **2D + Scaffold ×1** | One fixed rendered view with the agent scaffold; no source artifacts | No |
| **2D + Scaffold ×3** | Three fixed rendered views with the agent scaffold; no source artifacts | No |
| **2D + Scaffold ×6** | Six fixed rendered views with the agent scaffold; no source artifacts | Yes |
| **2D + Scaffold ×12** | Twelve fixed rendered views with the agent scaffold; no source artifacts | No |

Use these names in reports. The driver uses shorter internal identifiers; keep those in CLI
arguments and in stored result records, because they are what the native output fields carry.

| Setting | Internal route ID | Driver option | Agent environment |
|---|---|---|---|
| **3D + Scaffold** | `cc` | `--matrix six` | `codex` |
| **3D + Scaffold + Vision MCP** | `ccmcp` | `--matrix six` | `codex-mcp` |
| **3D + Vision MCP** | `3d-mcp` | `--matrix 3d-mcp` | `codex-mcp` |
| **2D + Scaffold ×1** | `cc2d-1` | `--matrix six` | `codex-2d` |
| **2D + Scaffold ×3** | `cc2d-3` | `--matrix six` | `codex-2d` |
| **2D + Scaffold ×6** | `cc2d-6` | `--matrix six` | `codex-2d` |
| **2D + Scaffold ×12** | `cc2d-12` | `--matrix six` | `codex-2d` |

`--matrix six` is the driver's default and launches the six non-MCP-only routes together.
**3D + Vision MCP** is a separate `--matrix 3d-mcp` invocation with the shell tool disabled.
`--matrix t0-probe` runs a text-only diagnostic; it is not one of the seven settings.

A route ID is a label, not a permissions attestation. Reproducing a setting requires a task
contract and a runtime profile that actually enforce its intended access, with the evidence
recorded in the run receipt.

## Running the harness

The harness is vendored in this repository: `scripts/harbor/`, `harbor_adapters/`,
`sci3d_bench/`, `sci3d_agent/` and `scorer_bundle/`. It installs as an extra of this
distribution. It needs **Python 3.12 or later**, because Harbor requires it, and a
Docker-capable host with the rendering dependencies the selected assets need.
[`resources/harness.json`](../resources/harness.json) records the upstream revision the
reported results were produced on. The `harbor` extra pins Harbor `v0.20.0`, the tag those
runs used; Harbor is installed from its Git repository because it is not published on PyPI.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[harbor]"
python -m scripts.harbor.run_end2end --help
python -m scripts.harbor.qa_gen_to_tasks --help
```

Run a real matrix only with approved task records, a model configuration, and valid model
credentials. `--dry-run` still builds task directories and renders views; it does not call
models or launch Harbor jobs.

## Task contract

The dataset and the harness use different formats. `normalized.json` carries a question and an
answer; the harness needs a task record that additionally declares what the answer must look
like and how it is graded. A task record carries:

| Field | Meaning |
|---|---|
| `task_id` | Matches the dataset's task id |
| `instruction` | The question as the agent receives it |
| `assets` | Asset references, with role and path |
| `answer_schema` | The field list the answer must populate |
| `ground_truth` | The expected value per field |
| `scorer` | The scoring contract and its configuration |
| tool and evaluation contract | Which tools the route may use, and whether the task is eligible |

**`index.jsonl` and an unmodified `normalized.json` are not launch-ready task records.** Asset
selection, multi-asset roles, camera state, tolerances, and eligibility must come from an
approved contract; guessing them changes the benchmark rather than reproducing it.

For a prepared record file at `prepared/tasks.jsonl`, the builder and a one-task dry run are:

```bash
python -m scripts.harbor.qa_gen_to_tasks \
  --jsonl prepared/tasks.jsonl \
  --asset-root ../data/sciencevision \
  --out work/tasks-3d-scaffold \
  --containerized --agent-env codex

python -m scripts.harbor.run_end2end \
  --tasks prepared/tasks.jsonl --pre-resolved \
  --matrix six --set-name ScienceVision \
  --asset-root ../data/sciencevision \
  --out-root work/qa --jobs-root work/smoke \
  --limit 1 --dry-run
```

`CODEX_MODEL` is required for a real run, along with a valid model credential in the runner's
supported environment or authentication file. Use a fresh jobs directory per run, and resolve
the local agent and worker aliases against the selected release in the
[container guide](containers.md). Some runners check the exact local alias and fail preflight
otherwise.

## Scoring standard

Scoring is **deterministic and fail-closed**. No model judges an answer, and no answer is
silently graded when its contract is missing.

### Eligibility comes first

A task must set `evaluation_eligible = true` exactly. Missing or `false` produces a **`blocked`
result, not a score of zero**. A blocked trial is reported separately and must never be mixed
into a mean as if the model had failed the question.

The bundle supports three contracts:

| Contract | For |
|---|---|
| `deterministic_structured_v2_1` | A structured answer with a declared field rubric |
| `structured_deterministic_rubric_v1` | The generic form of the same |
| `extent_voxel_tolerance_v1` | Volumetric answers judged by region membership |

LLM or prose judges, mask Dice, unknown contracts, and malformed contracts stay `blocked`.
This release never auto-grades them.

### The answer

The parser accepts **one JSON object**, or **one `SCI3D_FINAL_JSON:` terminal marker** followed
by that object in the agent's final message. Nothing else is parsed out of prose. Type survives:
`integer_equal` distinguishes `3` from `"3"`, and `vector_abs_tolerance` keeps its component
order.

### Comparators

Every field in `answer_schema` names a comparator, and every comparator is implementation-pinned
in the scorer's manifest so a scoring run can be identified by its comparator registry hash.
Seventeen are defined:

| Comparator | Matches when |
|---|---|
| `exact` | Same type and equal |
| `canonical_string_equal` | Canonical forms are equal, widened by the item's declared `accepted` spellings |
| `canonical_enum_equal` | As above, for enumerations |
| `cited_clause_equal` | Both sides cite the same `(standard, clause)` pair; a gold that names no sub-clause does not constrain one |
| `boolean_equal` | Both sides resolve to a boolean |
| `integer_equal` | Both are integers of equal value; a boolean is never an integer |
| `abs_tolerance` | Within the field's absolute tolerance |
| `vector_abs_tolerance` | Leaf-by-leaf at any nesting depth, per-component tolerances, shape required to match |
| `circular_tolerance` | Within tolerance on a modular scale, default modulus 360 |
| `canonical_atom_id_equal` | Canonical atom identity, whether named or numeric |
| `canonical_atom_identity_equal` | As above |
| `canonical_asset_id_equal` | Canonical asset identifiers |
| `canonical_residue_equal` | Canonical residue identifiers |
| `canonical_space_group_equal` | Canonical space-group symbols |
| `canonical_set_equal` | Equal as a multiset: order ignored, multiplicity counted |
| `ordered_string_vector_equal` | Positional equality, where the index carries meaning |
| `voxel_member_of_set` | The predicted voxel lies inside the item's packed region. The mask **is** the tolerance, so there is no numeric tolerance to tune |

Tolerances live in the scorer configuration, per field, and are part of the task contract. Two
runs at different tolerances are different measurements.

### Score

`answer_score` is computed **field by field**. A field either matches or it does not, and the
score is `1.0` only when every field matched. Reported metrics:

| Metric | Definition |
|---|---|
| `mean@3` | Each task's mean over three independent runs, then averaged over tasks |
| `pass@3` | Share of tasks with at least one full score across three runs |

For a single-run column, the metric is `mean@1`. Every reported mean needs its denominator and
its coverage: missing, error, incomplete, blocked, and ungated trials are reported, not
discarded. See [performance.md](performance.md) for the native fields and the aggregation
modes.

A scorer self-test that feeds ground truth back into the scorer establishes scorer consistency.
It is not a model result and does not belong in a leaderboard.

## Reproducibility receipts

The scorer image is built from an explicit allow-list and writes a `scorer_manifest.json`
recording the bundle version, source revision, source and bundle tree hashes, the task-spec
schema hash, and a per-comparator implementation registry with its own hash. The CLI checks the
manifest's self-hash before scoring, so a modified scorer cannot silently report as the pinned
one.

For a run receipt, record the dataset repository and its 40-character commit, the index hash,
the harness commit, the model identifier and version, the sampling configuration, platform and
dependency versions, each image's GHCR digest, the scorer digest, and the hashes of the task set
and the native leaderboard. No tokens, authentication files, private addresses, or raw local
paths belong in a submitted artifact.
