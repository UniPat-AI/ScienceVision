# BabyVision-v2

[English](README.md) · [简体中文](README.zh-CN.md)

> **Evaluating 3D-grounded visual reasoning in professional domains.**
> A benchmark and a self-serve platform for partners to onboard new domains and run QA pipelines.

BabyVision-v2 is the public face of the [ScienceVision](https://github.com/SueMarsR/Science-Vision) research effort.
It continues BabyVision v1 in methodology — *evaluation-first, no training, executable ground truth defends against LLM-as-judge* — and extends it along three axes: real 3D scene graphs, professional domains, and agent operation traces with pixel evidence.

---

## Two entry points

| | |
|---|---|
| **Benchmark / Leaderboard**<br/>Task families, evaluation protocol, leaderboard, submission guide.<br/>→ [`docs/benchmark/`](docs/benchmark/) | **Platform / Partner SDK**<br/>30-minute quickstart, new-domain template, architecture, API reference.<br/>→ [`docs/platform/`](docs/platform/) |

Partners (Genesis AI, Elorian AI, Meta research teams, and others) are expected to start at **Platform → Quickstart**. Benchmark contributors start at **Benchmark → Submission guide**.

---

## 3D assets & viewer

Every asset is listed in a per-domain manifest and rendered by an in-browser
viewer — no build step, no plugins. To browse them locally:

```bash
python3 _dev_serve.py     # serves repo root on http://127.0.0.1:8080/
# → open http://127.0.0.1:8080/viewer/  (start at "Domain 3D Assets")
```

- **Find assets:** `viewer/assets/<domain>.json` (+ roll-up `viewer/assets.index.json`).
  Each entry carries a repo-root-absolute `path`, a `viewer` (renderer), and a `format`.
- **Renderers:** Three.js (`gltf` — glb/gltf/obj/stl/ply), Mol\* (`molstar` — mmcif/pdb/sdf/mol…),
  NiiVue (`niivue` — nifti/nrrd), OpenCascade (`occt` — step/iges), USD (`usdz`/`usda`),
  plus domain-specialized pages (Genesis, URDF, GDSII, point cloud, …). 35 pages under `viewer/`.
- **Bytes:** git tracks only the small demo assets; `crawl_data/` and
  `viewer/datasets/*/assets/` are gitignored and pulled separately (ModelScope
  `tianyi3unipat/BabyVision-v2-Datasets`). Large CAD (STEP) is download-only with a derived GLB.

Full guide → [`docs/platform/viewer-and-assets.md`](docs/platform/viewer-and-assets.md).
Live demo → **babyvision-v2.unipatai.com**.

Local expert assignment, redacted per-expert packaging, and result reload are
documented in the [offline task management workflow](tools/offline_task_manager/README.md).

---

## What's in this repo

```
docs/benchmark/     # leaderboard, task families, evaluation protocol, submission guide
docs/platform/      # quickstart, new-domain template, architecture, API reference
docs/partners/      # public case studies (sanitized; detailed coordination lives elsewhere)

sci3d_agent/        # MCP server (56 tools), agent runner — mirrored from ScienceVision
viewer/             # URL-parameterized static viewer pages + asset manifests — mirrored
  ├─ assets/        #   per-domain 3D-asset manifests (path + renderer + format)
  └─ *.html         #   35 in-browser renderers (Three.js, Mol*, NiiVue, OCCT, USD, …)
scripts/            # match builders, qa-gen entry points, sam_service fake — mirrored
datasets/_matched/  # 3D-grounded whitelist indices (no raw assets; partners pull separately)
QA-Gen/tasks/       # generated QA candidate sets, one directory per drop — mirrored
  └─ scale-v2.3/    #   1333 subquestions + 854 physical-asset questions (see its README)

examples/           # End-to-end demos referenced by docs/platform/quickstart.md
tests/              # Core test suite
```

Source code in `sci3d_agent/`, `viewer/`, `scripts/`, `datasets/_matched/`, and the generated data under `QA-Gen/` is mirrored from [`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision) using the `.mirror-include` manifest. Documentation under `docs/benchmark/`, `docs/platform/`, `docs/partners/`, and everything under `examples/` is native to this repository.

The prompt an agent actually reads lives in the `instruction` field of the JSONL files
under `QA-Gen/tasks/<drop>/`. There is no `question` key. For the current drop, which
file to read for which purpose, and which fields are generator metadata rather than the
question, see [`QA-Gen/tasks/scale-v2.3/README.md`](QA-Gen/tasks/scale-v2.3/README.md).

---

## Status

Early scaffold. Quickstart, new-domain template, and the first mirror batch are in progress.

## Contributing

If you'll be editing `docs/**/*.md`, install the repo's git hooks once:

```bash
scripts/install-hooks.sh
```

This points `core.hooksPath` at `scripts/git-hooks/`, which auto-regenerates
`docs/_index.json` and `docs/_search.json` whenever your commit touches a
docs markdown file. Without it, CI will catch the mismatch (and on
same-repo PRs auto-push the regen back), but installing the hook locally
makes commits self-consistent and avoids round-trips.

## License

See [`LICENSE`](LICENSE).
