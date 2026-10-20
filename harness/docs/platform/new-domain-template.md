# New-domain template

> **Goal: a checklist that a partner engineer can follow to add a new professional domain (CAD, microscopy, geophysics, …) without UniPat in the loop.**

> Reading order: skim this template for the four touchpoints; for a **worked example** that ties each step to a real file in this repo, see [`../../examples/03_add_new_domain/README.md`](../../examples/03_add_new_domain/README.md). It walks through how `m3d-refseg` was wired in — onboarding a new domain = redoing the same four edits with your name in place.

Adding a new domain touches **four** places. Each section below maps to one of them. All four must be done; skipping any one will leave a half-wired domain that compiles but fails at viewer / QA-gen time.

---

## 1. `crawl_data/` directory convention

Each new domain lives under `crawl_data/<domain>/<L2-category>/`. Large raw downloads are staged in the sibling `_dl_pool/` prefix. See `docs/CrawlDataAndViewer.md` (mirrored from ScienceVision) for the full layout and naming rules.

- [ ] Pick `<domain>` short name (lowercase, kebab-case)
- [ ] Pick L2 categories
- [ ] Drop raw files (or symlinks) under `crawl_data/<domain>/<L2>/`
- [ ] If files are large, stage via `_dl_pool/`
- [ ] Add the on-disk root to `_paths()` in `scripts/match/build_matched_3d_qa.py` (e.g. `"<your_domain>_root": repo_root / "datasets" / "<YourDir>"`)

## 2. `_matched/` whitelist registration

The 3D-grounded whitelist `datasets/_matched/index.json` is built by `scripts/match/build_matched_3d_qa.py`. Two edits in that file, one possible edit in the viewer shell:

- [ ] Add an `iter_<your_domain>(paths) -> Iterable[MatchRecord]` function — see `iter_m3d_refseg` (line 247) for the smallest fully-worked example
- [ ] Register it in `DATASET_ITERATORS` (line ~535): `"<your-domain>": iter_<your_domain>,`
- [ ] If your data depends on ScanNet scene availability, also add the name to `SCANNET_AWARE_ITERATORS` (most domains don't)
- [ ] Each `MatchRecord` you yield must set `asset_kind` to one of the kinds registered in `viewer/match3d.html`'s `ASSET_VIEWERS` (currently `mesh`, `ply`, `nifti`). If your data needs a different renderer, see §3 below
- [ ] Run: `python -m scripts.match.build_matched_3d_qa --dry-run` then without `--dry-run`. Verify your domain shows up in `datasets/_matched/index.json` and that `datasets/_matched/<your-domain>.json` is non-empty

## 3. Viewer wiring

How your records get rendered depends on whether `asset_kind` matches an existing viewer:

- [ ] **Existing kind (`mesh` / `ply` / `nifti`)** — no viewer-side edit. The shell at `viewer/match3d.html` already routes through `gltf.html` / `niivue.html`. Confirm by opening `/viewer/match3d.html` locally and verifying your assets render (no 404, correct iframe per kind).
- [ ] **New kind** — write a new viewer page and add it to `ASSET_VIEWERS` in `viewer/match3d.html:409`. See [`new-viewer-page.md`](new-viewer-page.md) for the page contract (≤80 lines for niivue.html), vendor-JS layout, and shell wiring.
- [ ] If your domain ships its own VQA-style dataset manifest (analogous to `viewer/datasets/m3dvqa.json`, `scanqa.json`, etc.), drop the JSON there so the assets pane picks it up.

## 4. QA pipeline invocation

The QA-gen pipeline is **deterministic** at runtime (no LLM cost during a run). The only LLM cost is build-time during task design. See [`../qa-gen/pipeline-overview.md`](../qa-gen/pipeline-overview.md) for the stage architecture, the `TEMPLATES` contract, and the `final_tasks.jsonl` schema your batch must produce.

- [ ] Add `TEMPLATES["<your_domain>"] = {...}` covering the V-classes you want supported (overview §7.1)
- [ ] Define your task batch (P0/P1/P2) under `QA-Gen/tasks/<your_batch_name>/`
- [ ] Ship `generation_stats.json` alongside `*_final_tasks.jsonl` — required by the index builder (see [`qa-to-viewer-loop.md`](qa-to-viewer-loop.md) §3)
- [ ] Run the pipeline against a small sample first
- [ ] Verify the outputs land with the expected schema (`pipeline-overview.md` §5)
- [ ] Run `python3 tools/build_qagen_index.py` to register the batch in the viewer — see [`qa-to-viewer-loop.md`](qa-to-viewer-loop.md)

---

## Acceptance

A domain is "done" when, on a clean clone of BabyVision-v2 plus a fresh checkout of your raw data:
1. `_matched/index.json` count goes up by N (the number of assets you registered)
2. Viewer shows your assets without 404
3. A small QA batch generates without errors
4. The resulting QA appears in the relevant leaderboard task family

For an end-to-end leaderboard score, see Harbor integration (issue [#18](https://github.com/UniPat-AI/BabyVision-v2/issues/18) + [#34](https://github.com/UniPat-AI/BabyVision-v2/issues/34)).
