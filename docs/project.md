# Project overview

ScienceVision evaluates questions grounded in scientific 3D assets. A task combines a question, one or more source assets, and an answer or scoring contract. The baseline comparison changes how the agent can access the asset: raw files, files with MCP tools, or rendered images.

## Selected release

The `review-v11` snapshot contains 250 tasks:

| Domain identifier | Tasks | Example subject matter |
|---|---:|---|
| `architecture` | 50 | Geometry, alignment, clearance, and dimensional checks |
| `biology` | 50 | Molecular arrangement, contacts, and spatial selection |
| `chemistry` | 50 | Molecular geometry, torsion, occlusion, and crystal structure |
| `mechanical-cad` | 50 | Parts, assembly composition, properties, and motion |
| `medical` | 50 | Volumetric geometry, slice comparisons, and spatial localization |

Equal task counts do not imply equal difficulty or identical scoring contracts. The source dataset card records task selection history, mixed evaluation provenance, and known limitations.

## Repository responsibilities

This repository holds the release references and documentation. [BabyVision-v2](https://github.com/UniPat-AI/BabyVision-v2/tree/b47f13ac5abb54b9e287ff3131ae7e29771ec53e) holds the evaluation implementation. Hugging Face holds data and archived trajectories; GHCR holds container packages. Large data and evaluation histories are not duplicated in Git.

The implementation is pinned to `b47f13ac5abb54b9e287ff3131ae7e29771ec53e` on the `eval-baseline` line. Future updates should change the reference and record compatibility evidence together.

## Release status

The GitHub repository and GHCR packages are private. The dataset remains in `elorian-ai/ScienceVision` at the selected `review-v11` snapshot. Before public release, linked private dependencies must be made accessible through the intended release mechanism, data terms must be established, and the dataset's documented content and evaluation limitations must be resolved or stated in the release.

No paper citation, author list, benchmark claim, or new performance result is asserted by this repository.
