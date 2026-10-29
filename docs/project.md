# Project overview

ScienceVision evaluates questions grounded in scientific 3D assets. A task combines a question,
one or more source assets, and an answer or scoring contract. The paper defines seven access
settings: **3D + Scaffold**, **3D + Scaffold + Vision MCP**, **3D + Vision MCP**, and
**2D + Scaffold ×1/×3/×6/×12**. It reports results for three of them. See
[evaluation.md](evaluation.md#evaluation-settings).

## The release

The dataset is **ScienceVision**: 250 tasks, 50 in each of five domains.

| Domain identifier | Tasks | Example subject matter |
|---|---:|---|
| `architecture` | 50 | Geometry, alignment, clearance, and dimensional checks |
| `biology` | 50 | Molecular arrangement, contacts, and spatial selection |
| `chemistry` | 50 | Molecular geometry, torsion, occlusion, and crystal structure |
| `mechanical-cad` | 50 | Parts, assembly composition, properties, and motion |
| `medical` | 50 | Volumetric geometry, slice comparisons, and spatial localization |

Equal task counts do not imply equal difficulty or identical scoring contracts. The source card
records the task-selection history, the measurement provenance, and the known limitations;
[dataset.md](dataset.md#known-limitations) reproduces the ones that change what a score means.

## Repository responsibilities

| Artefact | Home |
|---|---|
| Release references, documentation, schemas | This repository |
| Dataset, assets, archived trajectories | Hugging Face |
| Evaluation harness and scorer source | This repository |
| Container images | GHCR, under `ghcr.io/unipat-ai` |

Large data and evaluation histories are not duplicated in Git.

## Release status

This repository is private. The dataset repository is private. The GHCR packages are private.

Before public release, the following must be settled. Each is a link that a reader outside the
organisation cannot follow today:

1. **The dataset must be reachable** at its canonical repository. The package card,
   `MANIFEST.json`, and `LICENSE` declare MIT.
2. **The harness is vendored under `harness/`.** The whole `eval-baseline` branch is present
   at source commit `6fd95252e81fb153432add0f2400d9ee1eea6697`; the reported results were
   produced on `b47f13ac5abb54b9e287ff3131ae7e29771ec53e`. Both revisions are recorded in
   [`resources/harness.json`](../resources/harness.json).
3. **The container packages must be made public**, or the reader told how to request access.
4. **A task export must be published.** Neither `index.jsonl` nor `normalized.json` is launchable
   harness input on its own, so the [task contract](evaluation.md#task-contract) needs a
   conforming export alongside the dataset. `sandbox/export_contracts.py` produces that export
   from a prepared tree; the remaining step is to publish the resulting JSONL with the release.

The dataset's declared limitations and the provenance notes in
[leaderboard.md](leaderboard.md) are part of the release. Without them a reader cannot tell what
a given score covers.

The project has a paper. Its citation, author list, and published figures come from that paper;
this repository records the release artifacts and the comparison those figures were drawn from.
