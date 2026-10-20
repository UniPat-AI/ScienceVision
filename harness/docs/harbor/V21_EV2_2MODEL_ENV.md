# Environment and build for the scale-v2.1 matrix

What you actually need, per level of engagement. Most readers stop at level 1 or
2 and need neither Harbor nor Docker. Results:
[`V21_EV2_2MODEL_RESULTS.md`](V21_EV2_2MODEL_RESULTS.md). Method:
[`V21_EV2_2MODEL_RUNBOOK.md`](V21_EV2_2MODEL_RUNBOOK.md).

| Level | What you can do | Python | Repo deps | Harbor | Docker | Credentials | Raw data |
|---|---|---|---|---|---|---|---|
| 0 | read the published numbers | — | — | no | no | no | no |
| 1 | re-aggregate the table from an existing job root | ≥3.7 | none (stdlib) | no | no | no | no |
| 2 | run this repo's test suite | ≥3.10 | `.[dev]` (+`.[harbor]` for one package) | one package only | 2 tests only | no | some packages skip |
| 3 | rebuild the six agent images | — | — | no | **yes** | no | no |
| 4 | re-run the twelve-route matrix | 3.12 | `.[harbor]` | **0.20.0** | **yes** | **yes** | **yes** |

## Level 1 — re-aggregate the leaderboard

[`../../scripts/harbor/report_v21_ev2.py`](../../scripts/harbor/report_v21_ev2.py)
reads Harbor job roots off the filesystem. Standard library only: no Harbor
import, no Docker, no network, no credentials.

```bash
RUN=~/ScienceVision-evals/scale-v21-ev2-sixroute-2model-20260813
python3 scripts/harbor/report_v21_ev2.py \
  --run-root "$RUN" --selection "$RUN/selection" --out "$RUN/reports"
```

Two traps: `--selection` takes the **directory**, not `SELECTION.json` inside
it; and the script opens with `from __future__ import annotations`, so a
pre-3.7 interpreter fails at import. On H20 the system `python3` is too old and
reports `SyntaxError: future feature annotations is not defined` — use the
pinned Harbor venv (`~/ScienceVision-venvs/scale-v21-pr170-harbor-0.20.0/bin/python`)
or any modern 3.x.

## Level 2 — run the test suite

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # pytest, pytest-cov, ruff, jsonschema
pytest tests/ -q
```

`requires-python = ">=3.10"`. `pytest.ini` sets `pythonpath = .`, so no
`PYTHONPATH` juggling is needed. Measured on a clean `origin/main` checkout with
Python 3.12.13 and Harbor 0.20.0 present:

| package | result | what it needs beyond `.[dev]` |
|---|---|---|
| `tests/harbor_adapters` | 216 passed | **Harbor** — these hard-import `harbor.models.*`, they do not `importorskip`. Add `.[harbor]`. |
| `tests/mcp` | 579 skipped | raw ScienceVision data layers (`crawl_data/`, `datasets/`), pulled per `docs/platform/quickstart.md`. Not present in a plain clone. |
| `tests/sci3d` | 127 passed, 1 failed, 6 skipped | the failure is `test_llm_client` wanting the `anthropic` package (`.[mcp]`); 4 skips want `trimesh`, 2 want `RUN_DOCKER_TESTS=1`. |
| `tests/scripts` | 23 passed, 4 failed | pre-existing; see below. |
| `tests/viewer` | 2 passed, 1 skipped | — |
| docs / manifest tests | 43 passed, 4 failed | stdlib only. |

**Docker touches the test suite in exactly one place**:
`tests/sci3d/test_kernel_docker.py`, gated behind `RUN_DOCKER_TESTS=1` and
skipped otherwise. Everything else runs on a host with no Docker daemon at all.

**Nine failures are pre-existing on `main`**, unrelated to any eval work, and
they are test debt rather than environment problems — worth knowing so you do
not read them as a broken setup:

- `tests/test_data_indexes.py` (3) — hardcoded demo counts drifted: the test
  wants 6 assets and 4 questions, the repo now carries 15 and 19.
- `tests/scripts/test_qa_qc_round2.py` (4) — the QC blocker set grew a
  `molecular_file_preflight_invalid` code the expectations do not list.
- `tests/sci3d/test_llm_client.py` (1) — `anthropic` not installed.
- `tests/test_docs_no_chinese.py` (1) — `platform/qa-case-to-public-url-sop` is
  in the published index with Han characters in its body.

## Level 3 — rebuild the agent images

Docker is required here and nothing else is. Six agent images plus one scorer,
`--platform linux/amd64`:

```bash
# self-contained context (the templates directory)
scripts/harbor/templates/build_claude_code_image.sh     sci3d-claude-code:<tag>
scripts/harbor/templates/build_claude_code_2d_image.sh  sci3d-claude-code-2d:<tag>
scripts/harbor/templates/build_codex_image.sh           sci3d-codex:<tag>
scripts/harbor/templates/build_codex_2d_image.sh        sci3d-codex-2d:<tag>

# context = repository root: these COPY sci3d_agent/, harbor_adapters/,
# sci3d_bench/, pyproject.toml and templates/claude_code_mcp.mcp.json
scripts/harbor/templates/build_claude_code_mcp_image.sh sci3d-claude-code-mcp:<tag>
scripts/harbor/templates/build_codex_mcp_image.sh       sci3d-codex-mcp:<tag>
```

The run under report used tag `v21ev2-20260813` and scorer image
`sci3d-scorer:harbor-0.20-v21ev2-20260813`.

Three build-time pins that are load-bearing:

- **`ARG CODEX_CLI_VERSION=0.147.0`.** Pinned, not floating, because the failure
  mode of the wrong CLI is silent: the trials still run, still get scored, and
  the score is always 0.0 with `parse_error=missing_answer`. 0.146.0 images are
  kept under `-cli0146` tags as a labelled fallback — a *different* agent, not a
  fix.
- **Never rebuild `:latest` on a shared host.** The agent-env table bakes
  `:latest` into every generated Dockerfile, which on H20 is whatever another
  session built last. Build under a run-scoped tag and repoint the staged trees
  with [`../../scripts/harbor/repoint_tree_images.py`](../../scripts/harbor/repoint_tree_images.py).
  It rewrites each Dockerfile rather than editing in place, because trees are
  hardlinked to share staged assets and an in-place write would reach through
  the link into a sibling tree.
- **No GL/EGL is needed for this task set.** It is mmCIF / xyz / mol / nii.gz
  only, all of which render through matplotlib. `trimesh` is absent from the
  pinned Harbor venv, so the mesh renderer cannot run there — which is why
  `tests/mcp/test_render_static.py::test_missing_dependency_returns_envelope`
  fails in that venv, before and after this run's changes. A task set with
  meshes would need `trimesh` and a GL path.

## Level 4 — re-run the matrix

Requires Harbor **0.20.0** in its own venv (`ScienceVision-venvs/scale-v21-pr170-harbor-0.20.0`
for the reported run), Docker, live credentials, and the two frozen input
packages. Three of those are host state rather than repository content:

- **Task trees and eval inputs are not in this repo.** The trees are staged from
  `ScienceVision-eval-inputs/scale-v2.1-expert-review-candidate823-20260810-c2660d`
  and `ScienceVision-eval-inputs/expert-v2-scale-v1-sample165-20260811`, and the
  samplers and tree builders (`sample_v21_ev2.py`, `build_v21_ev2_trees.py`,
  `build_v21_ev2_diag_trees.py`) reference absolute host paths. They live on the
  Science-Vision branch `eval/v21-ev2-2model-20260813`.
- **The runner is per-run, not a repo artifact**:
  `ScienceVision-runners/v21_ev2_2model_20260813/{run.sh,pins.env}`, with the
  Codex request-repair shim (`agents/uniapi_shim.js`) staged beside it.
- **Docker concurrency is capped by the host bridge pool, not by taste.** Harbor
  creates one compose project — and therefore one bridge network — per running
  task. The pool holds 31 and other users' multi-day jobs hold a large share, so
  the run uses 6 routes × 2 = 12 slots and `require_bridge_headroom` refuses to
  launch without them. A previous scale-v1 run set `-n 24`, exhausted the pool,
  and lost every trial at once. Do not enlarge the pool by restarting Docker on
  a shared host.

### Credentials, and the trap that emptied the Codex half

| family | needs in the environment |
|---|---|
| `claude-code` | `ANTHROPIC_BASE_URL` plus `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` |
| `codex` | `UNIAPI_KEY_SCIENCEVISION`, and `NO_PROXY=127.0.0.1,localhost` so the loopback shim is reachable |

Values are never placed in process arguments: the runner passes the literal
string `KEY=${KEY}` to Harbor's `--ae` and the value expands inside the
container, keeping it out of `argv` and out of every log.

**Passing preflight does not prove the wave has credentials.** `preflight`
checks credential presence in *your interactive shell*; `wave` only checks that
`state/preflight.ok` exists, then launches each route as
`tmux new-window "bash run.sh full …"`. That window is a **non-interactive**
bash, which does not read `~/.bashrc`, so its environment is the tmux *server's*
environment — fixed when the server started, not yours now.

On H20 the tmux server carries the Anthropic credentials globally but not the
UniAPI key. Probed directly:

```
$ tmux new-session -d -s envprobe 'bash -c "... > /tmp/envprobe.txt"'
uniapi=  anthropic=SET  base_url=SET
```

That is the whole reason the Codex half of the reported run has zero trials:
every Codex route raised `RuntimeError: Required environment variable
UNIAPI_KEY_SCIENCEVISION is absent` at agent construction while the Claude
routes, launched the same way, had what they needed. Before `./run.sh wave codex`:

```bash
tmux setenv -g UNIAPI_KEY_SCIENCEVISION "$UNIAPI_KEY_SCIENCEVISION"
```

or start the tmux server from a shell that exports it. The durable fix is for
preflight to probe the credential inside a shell of the same shape the wave
uses, rather than in the shell the operator happens to be typing in.
