# `examples/03_add_new_domain/` — guided tour: how `m3d-refseg` got wired in

> **Read this as a guided tour of how the `medical / m3d-refseg` domain was
> wired into BabyVision-v2.** Use it as the reference shape for your own
> domain — every step below maps to a real file in this repo that you can
> open, read, and copy.

> Lite version: rather than ship a synthetic toy domain, this example
> reads the existing `m3d-refseg` integration as living documentation.
> When [#32](https://github.com/UniPat-AI/BabyVision-v2/issues/32)
> (Harbor mirror) lands, this example will be upgraded to drive a
> verifier run end-to-end.

---

## TL;DR — the 4 touchpoints, each pinned to a real file

| # | Touchpoint | Where in this repo | Real artifact for `m3d-refseg` |
|---|---|---|---|
| 1 | Drop the raw data | `crawl_data/<domain>/<L2>/` | `datasets/M3D-RefSeg/` (lives outside crawl_data because it's partner-mirrored) |
| 2 | Write the match-builder | `scripts/match/build_matched_3d_qa.py` | `iter_m3d_refseg` (`build_matched_3d_qa.py:247`) + entry in `DATASET_ITERATORS` (line 535) |
| 3 | Tell the viewer what kind it is | `viewer/match3d.html` | `asset_kind="nifti"` → `ASSET_VIEWERS.nifti` → `niivue.html` (line 412) |
| 4 | Ship a QA-Gen batch | `QA-Gen/tasks/<name>/` | `QA-Gen/tasks/MedicalKAxisPilot/` (4 records, K-axis seeds on M3D-RefSeg) |

Onboarding your domain = redoing these four edits with your name in place
of `m3d-refseg`. Read on for what each one actually contains.

---

## Step 1 — drop the raw data

For `m3d-refseg`, the raw layout looks like:

```
datasets/M3D-RefSeg/
├── M3D_RefSeg/
│   ├── s0146/
│   │   ├── ct.nii.gz          ← the CT volume
│   │   ├── mask.nii.gz        ← per-label segmentation mask
│   │   └── text.json          ← human-authored mask label texts
│   ├── s0212/...
│   └── ...
├── train.csv                  ← (Image, Mask, Question, Answer, Question_Type, Mask_ID)
└── validation.csv
```

Each row in `train.csv` references one `(ct.nii.gz, mask.nii.gz, mask_id)`
triple inside one case directory, plus the QA pair the original M3D-RefSeg
dataset shipped.

**Convention for partner domains.** `docs/CrawlDataAndViewer.md` (mirrored
from ScienceVision) defines the canonical `crawl_data/<domain>/<L2>/`
layout for raw partner drops. The `m3d-refseg` files live under
`datasets/M3D-RefSeg/` rather than `crawl_data/medical/` because they
arrived through a different pipeline historically; the layout principle
is what matters, not the parent directory.

What `_paths()` in `build_matched_3d_qa.py` needs from you:

```python
# build_matched_3d_qa.py — extract from _paths()
"m3d_refseg_root": repo_root / "datasets" / "M3D-RefSeg",
```

So Step 1 is: **decide a stable on-disk root for your domain's raw data,
and remember the path — Step 2 needs it.**

---

## Step 2 — write the match-builder

The builder enumerates your domain's QA records and yields one
`MatchRecord` per record. Open `scripts/match/build_matched_3d_qa.py:247`
and read `iter_m3d_refseg` end-to-end — it's 35 lines and is the smallest
fully-worked iterator in the file.

The shape your iterator must produce:

```python
@dataclass(frozen=True)
class MatchRecord:
    qid: str                 # globally unique, e.g. "m3d-refseg-train-s0212-00000"
    dataset: str             # your domain's short name
    split: str               # "train" | "val" | "test"
    scene_or_case_id: str    # case id from your source
    question: str
    answer: str
    asset_kind: str          # "mesh" | "nifti" | "npy_volume" | "ply" — see Step 3
    path_repo: str           # repo-relative path to the main asset
    url_viewer: str          # viewer URL for the main asset
    extra: dict | None       # anything your domain needs (mask path, mask_id, ...)
```

`iter_m3d_refseg` reads `train.csv` + `validation.csv` row by row, skips
records whose `.nii.gz` or `mask.nii.gz` is not present on disk
(`asset_present()` check), and yields one `MatchRecord` per row.

### Hook it into the dispatch table

Right below the iterator (line ~535), one line registers it:

```python
DATASET_ITERATORS: dict[str, _Iter] = {
    "scanqa":            iter_scanqa,
    "sqa3d":             iter_sqa3d,
    "m3d-refseg":        iter_m3d_refseg,      # ← your new line goes here
    "m3d-seg-0005":      iter_m3d_seg_0005,
    "embodiedscan-demo": iter_embodiedscan_demo,
}
```

`collect()` walks `DATASET_ITERATORS.items()` and calls each iterator.
That's the entire dispatch — there is no separate `_MATCHED_DATASETS`
table to update (despite what older documentation said).

### Special case: ScanNet-aware iterators

If your domain depends on ScanNet scene availability (most don't), also
add your name to `SCANNET_AWARE_ITERATORS` so `collect()` passes the
extra `ScannetAvailability` argument. M3D-RefSeg does **not** need this;
the indoor-scene iterators (`scanqa`, `sqa3d`) do.

### Run and verify

```bash
python -m scripts.match.build_matched_3d_qa --dry-run
# prints record counts per dataset; your new name should appear

python -m scripts.match.build_matched_3d_qa
# writes datasets/_matched/<your-name>.json + updates index.json
```

`datasets/_matched/m3d-refseg.json` is the real output (2,777 records as
of 2026-05-19). Open it to see what your shard will look like.

---

## Step 3 — tell the viewer what kind it is

The viewer mounts one iframe per `MatchRecord`, picked by `asset_kind`.
Open `viewer/match3d.html:409`:

```javascript
const ASSET_VIEWERS = {
  mesh:  url => `/viewer/gltf.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
  ply:   url => `/viewer/gltf.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
  nifti: url => `/viewer/niivue.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
};
```

So `iter_m3d_refseg` yielding `asset_kind="nifti"` routes the asset
through `viewer/niivue.html`, which renders NIfTI volumes via the niivue
library. NIfTI records with a `mask_url_viewer` in `extra` get the
dual-pane layout (original + overlay) handled separately in
`slotInnerHTML()`.

### Three cases for your domain

| Your data format | What to do |
|---|---|
| It's a `mesh` (`.glb`, `.gltf`) or `.ply` — already maps to `gltf.html` | Set `asset_kind="mesh"` or `"ply"`. **No viewer edit.** |
| It's a NIfTI / NRRD volume | Set `asset_kind="nifti"`. **No viewer edit.** |
| It's something else (URDF, point cloud LAS, photonics field, …) | Pick or write a viewer page, then add the entry to `ASSET_VIEWERS`. See [`../platform/new-viewer-page.md`](../../docs/platform/new-viewer-page.md). |

The viewer infra (lazy iframe mounting, WebGL context budget eviction)
is shared — your new viewer page only needs to render one asset given a
`?file=` query param. The shell takes care of the rest.

---

## Step 4 — ship a QA-Gen batch

A domain isn't useful in the benchmark until it has a QA-Gen batch.
`m3d-refseg` plus K-axis design produced `QA-Gen/tasks/MedicalKAxisPilot/`
(4 records). Open that directory:

```
QA-Gen/tasks/MedicalKAxisPilot/
├── build_records.py                                ← deterministic builder
├── ScienceVision_MedicalKAxisPilot_final_tasks.jsonl     ← 4 records, each a TaskSpec
├── ScienceVision_MedicalKAxisPilot_seed_tasks.jsonl
├── generation_stats.json                           ← drives the viewer card (see qa-to-viewer-loop.md)
├── GENERATION_STATS.md
└── REVIEW.md
```

`build_records.py` is the simplest possible batch builder: hand-authored
records derived from M3D-RefSeg matched whitelist entries, zero LLM,
zero cost. Run `python3 QA-Gen/tasks/MedicalKAxisPilot/build_records.py`
and the JSONL is regenerated deterministically.

The **what** to put in `final_tasks.jsonl` is governed by:

- The schema in [`../../docs/qa-gen/pipeline-overview.md`](../../docs/qa-gen/pipeline-overview.md) §5.
- For domains that integrate with the upstream `sciencevision_taskgen`
  package: the `TEMPLATES["<domain>"]` registry (overview §7.1). M3D-RefSeg
  uses K-axis templates (`medical_k_axis_seeds.md`) layered on top of
  `iter_m3d_refseg`.

### Make it appear in the viewer

Once `generation_stats.json` exists in your batch directory:

```bash
python3 tools/build_qagen_index.py     # rebuilds viewer/qagen_index.json
```

After reload, your batch's card appears under the QA Tasks page. Full
contract in [`../../docs/platform/qa-to-viewer-loop.md`](../../docs/platform/qa-to-viewer-loop.md).

---

## What's left after the 4 steps

A domain whose 4 touchpoints land cleanly can:

1. Show its records in `datasets/_matched/<name>.json` (and the
   `_matched/index.json` count goes up).
2. Render its assets in the viewer at `/viewer/match3d.html` without
   404s, with the correct viewer iframe per `asset_kind`.
3. Have at least one QA batch that the deterministic pipeline (or its
   `build_records.py` analogue) can produce.
4. Show that batch as a card in the `QA Tasks` view via
   `qagen_index.json`.

It is **not yet** scoreable on the leaderboard — that needs a Harbor
verifier route + rubric, tracked in
[#33](https://github.com/UniPat-AI/BabyVision-v2/issues/33) (design)
and [#34](https://github.com/UniPat-AI/BabyVision-v2/issues/34) (docs).
This example will be revisited when those land.

---

## See also

- Template version (checklist form): [`../../docs/platform/new-domain-template.md`](../../docs/platform/new-domain-template.md)
- New viewer page guide: [`../../docs/platform/new-viewer-page.md`](../../docs/platform/new-viewer-page.md)
- QA → viewer reload loop: [`../../docs/platform/qa-to-viewer-loop.md`](../../docs/platform/qa-to-viewer-loop.md)
- QA-gen pipeline architecture: [`../../docs/qa-gen/pipeline-overview.md`](../../docs/qa-gen/pipeline-overview.md)
- 19-viewer matrix (which asset kind maps where): [`../../docs/Tools.md`](../../docs/Tools.md) §A
