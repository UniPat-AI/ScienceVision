# Ops handoffs & runbooks

Salvaged operational notes for one-off deploys and asset bundles. Kept here
(not under `docs/`) on purpose:

- `deploy/**` is `@hidden` in the Caddyfile (returns 404 on the public site),
  so these notes — which reference the Sing server IP and SSH aliases — are
  never web-served.
- `deploy/**` is not scanned by the docs-index / no-Chinese CI, so Chinese
  runbooks live here without translation.

These are historical records of completed work; they are not live
configuration. Treat them as reference.

## Contents

| File | What it documents | Status |
|---|---|---|
| `helmet-q001-RUN.md` | Handoff summary for shipping `industrial/helmet_caliber/q001` (DamagedHelmet caliper trap) | Done — PR #63 merged, URL live |
| `helmet-q001-SERVER_STEPS.md` | Exact copy-paste checklist (Part A/B/C) for the helmet q001 deploy, with expected output + md5 | Done |
| `sv-annotate-phase0-SERVE.md` | How to serve the sv-annotate Phase-0 self-contained annotation bundle (root-absolute paths) | Reference |
| `lego-cad-demo-README.md` | Contents of the LEGO functional-assembly CAD demo bundle (STEP + interactive physics HTML + motion checks) | See offload note below |

## LEGO CAD demo bundle location

The LEGO demo's 3.2 GB source bundle (12 STEP files under `cad/`, GLB meshes,
interactive HTML, motion GIFs) is **not** in git — too large. It lived at
`~/lego_demo/lego_demo/` on the H20 box. The integrated web-facing subset is
already tracked under the LEGO CAD viewer (see the `bv2-lego-cad-demo` notes);
this bundle is the full source archive. Offload target: ModelScope
`tianyi3unipat/BabyVision-v2-Datasets` (pending a fresh MODELSCOPE_API_TOKEN).
