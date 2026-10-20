# Partner Onboarding Guide

This guide walks a new partner team through the four steps to ship a domain into BabyVision-v2: explore the viewer, add a new domain, wire up MCP tools, and submit a benchmark entry.

> Partner case studies live in `examples/` — see [Genesis AI — embodied physics integration](examples/genesis-embodied.md) and [Medical Spatial — GT-grounded reasoning](examples/medical-spatial.md).

---

## 1. Use the viewer

The viewer is a static site (`python -m http.server` from the repo root) with five pages:

- **Home** (`viewer/index.html`) — Hero, motivation, launcher tiles, task families, domain coverage.
- **Doc** (`viewer/docs.html`) — Three-pane docs SPA with tree, content, and search.
- **Domain 3D Assets** (`viewer/assets.html`) — Browse domain-grouped 3D assets indexed by `data/assets.index.json`.
- **QA Tasks** (`viewer/qagen.html`) — Browse generated QA items indexed by `data/qagen_index.json`.
- **Leaderboard** (`viewer/leaderboard.html`) — Submitted model scores.

The single source of truth is `viewer/manifest.json`. Every page reads it via `shared/manifest.js`, which exposes `window.Manifest` and `window.ManifestReady`. To preview locally:

```bash
python -m http.server 8765
# then open http://localhost:8765/viewer/index.html
```

For the full platform tour see [Platform — Quickstart](../platform/quickstart.md) and [Platform — Architecture](../platform/architecture.md).

---

## 2. Add a new domain

There are two flavors of "new domain":

**(a) Adding to an existing domain** (e.g. more medical NIFTI volumes) — only data changes:

1. Add your asset files under `data/<domain>/...` (or push them via the partner data pipeline; sensitive paths are git-ignored via `datasets/*/raw/` and `datasets/*/derived/`).
2. Append entries to `data/assets.index.json` matching the existing entry schema (`id`, `domain`, `family`, `label`, `format`, `path`).
3. Append entries to `data/qagen_index.json` for any new QA items.
4. Bump `viewer/manifest.json:counts.assets` and `counts.qagen` so the homepage launcher counts stay accurate.
5. Run `pytest tests/test_data_indexes.py` to confirm consistency.

**(b) Adding a brand-new domain** (e.g. astronomy, photonics) — viewer config + data:

1. Add the domain to `viewer/manifest.json:domains[]` with `{id, label, color, shipped: true}`. Use a unique color hex.
2. Add a renderer hint: if your modality has an in-browser viewer (Mol* for protein, Niivue for NIFTI, Cornerstone for DICOM, Three.js for GLB, OCCT for CAD), wire it into `viewer/shared/viewers/<domain>.js` following the pattern in the existing renderers.
3. Follow flow (a) above to populate data + counts.

The template that walks through (b) in detail is [Platform — New-domain template](../platform/new-domain-template.md).

---

## 3. Wire up MCP tools

The MCP (Model Context Protocol) layer exposes deterministic, executable ground-truth tools to evaluation runs. Two flavors:

**(a) Use existing tools.** The shipped tools are documented in `docs/mcp/tools/`:

- [`load_nifti`](../mcp/tools/load_nifti.md) — load a NIFTI volume into the agent's scratch space.
- [`extract_segment`](../mcp/tools/extract_segment.md) — extract a labelled segmentation region.
- [`compute_volume`](../mcp/tools/compute_volume.md) — compute volume in mm³ from a segmentation.

Each tool has a JSON Schema (`<tool>.schema.json`) and an intro (`<tool>.intro.md`); the docs SPA composes them at view time. To call a tool from your evaluation harness, see [MCP server specification](../mcp/MCP_SERVER_SPEC.md).

**(b) Add a new tool.** Two artifacts per tool, both committed to `docs/mcp/tools/`:

1. `<tool>.schema.json` — JSON Schema 2020-12, with required keys `name`, `description`, `inputSchema`, `outputSchema`, plus optional `domain` for tagging.
2. `<tool>.intro.md` — short Markdown intro (no H1; the schema's `name` becomes the title) explaining when to call the tool and what success looks like.

Rebuild the docs index after adding the pair:

```bash
python scripts/build_docs_index.py
python scripts/build_docs_search.py
```

The phased rollout plan is at [MCP Server — Phased Rollout Plan](../mcp/MCP_ROLLOUT_PLAN.md).

---

## 4. Submit a benchmark entry

Once your domain data + MCP tools are wired in, generate QA items for the appropriate task family (see [Benchmark — Task families](../benchmark/task-families.md)), run your model against them, and submit a leaderboard entry per [Benchmark — Submission guide](../benchmark/submission-guide.md).

The scoring protocol — including which metrics defend against LLM-as-judge — is documented at [Benchmark — Evaluation protocol](../benchmark/evaluation-protocol.md).

---

## Reference

| Step | Primary doc | Supporting docs |
|---|---|---|
| 1. Use the viewer | [Platform — Quickstart](../platform/quickstart.md) | [Platform — Architecture](../platform/architecture.md) |
| 2. Add a new domain | [Platform — New-domain template](../platform/new-domain-template.md) | [Platform — API reference](../platform/api-reference.md), [Platform — QA-Gen → viewer reload loop](../platform/qa-to-viewer-loop.md) |
| 3. Wire MCP tools | [MCP — Server specification](../mcp/MCP_SERVER_SPEC.md) | [MCP — Phased rollout plan](../mcp/MCP_ROLLOUT_PLAN.md), [`load_nifti`](../mcp/tools/load_nifti.md) |
| 4. Submit benchmark entry | [Benchmark — Submission guide](../benchmark/submission-guide.md) | [Benchmark — Evaluation protocol](../benchmark/evaluation-protocol.md), [Benchmark — Task families](../benchmark/task-families.md) |

Sensitive coordination (deadlines, contact people, internal roadmap) lives in private Notion pages — never in this repo. Adding a public case study? See `examples/` for the template.
