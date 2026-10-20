# 3D Assets & Viewer

How BabyVision-v2 indexes its 3D assets, where the raw bytes live, and how the
in-browser viewer renders each format. If you just want to *see* an asset, jump
to [Run the viewer locally](#run-the-viewer-locally).

There are two layers, kept deliberately separate:

1. **Asset manifests** — small JSON files that list every asset and say which
   renderer + format it uses. Always in git.
2. **Viewer pages** — URL-parameterized static HTML pages, one per renderer.
   Always in git.

The **raw asset bytes** are a third thing, and mostly *not* in git — see
[Where the bytes live](#where-the-bytes-live).

---

## Finding the assets: the manifests

The authoritative index is per-domain JSON under `viewer/assets/`, plus a
top-level roll-up:

```
viewer/assets.index.json        # { domain: {count, sha, url} } — 12 domains
viewer/assets/<domain>.json     # the actual asset list for one domain
```

Every entry has the same shape:

```json
{
  "name":   "DamagedHelmet",
  "path":   "/crawl_data/industrial/khronos_gltf/DamagedHelmet.glb",
  "size":   2284996,
  "viewer": "gltf",
  "format": "glb"
}
```

- **`path`** is **repo-root-absolute** (`/crawl_data/…` or `/viewer/datasets/…`).
  It resolves directly when the docroot is the repo root — which is exactly what
  the dev server and the deployed site do.
- **`viewer`** selects the renderer (see the table below).
- **`format`** is passed to renderers that need it (e.g. Mol* `&type=`).

To find every asset of a kind, grep the manifests rather than walking the tree:

```bash
# every OCCT/CAD asset across all domains
grep -l '"viewer": "occt"' viewer/assets/*.json

# every asset in one domain, with its renderer
python -c "import json;[print(a['viewer'],a['format'],a['path']) \
  for a in json.load(open('viewer/assets/industrial.json'))]"
```

Domains currently indexed (`assets.index.json`): biology, medical, industrial,
robotics, pointcloud, topological_photonics, photonics, astronomy, simulation,
geography. (The domain *taxonomy* — labels, colors, `shipped` flags — lives in
`viewer/manifest.json`.)

---

## Where the bytes live

Git only tracks the small demo assets that the shipped domains need. Everything
else is pulled separately. The split is enforced by `.gitignore`:

```
crawl_data/                 # ignored — partner sample packs, pulled separately
viewer/datasets/*/assets/   # ignored — per-dataset raw assets
```

So:

| Location | In git? | What |
|---|---|---|
| `viewer/datasets/genesis/*.glb` | ✅ | 4 Genesis/embodied sim outputs (sand_wheel, cut_dragon, ipc_cloth, franka_grasp) |
| `viewer/datasets/industrial/DamagedHelmet.glb` | ✅ | Khronos sample |
| `viewer/datasets/{astronomy,biology,chemistry}/…` | ✅ | small non-mesh demo data (cif/mol/json/textures) |
| `crawl_data/industrial/**` | ❌ | Khronos glTF pack, mesh samples, STEP/IGES samples, USD, LEGO CAD |
| `viewer/datasets/*/assets/**` | ❌ | per-dataset raw meshes/volumes |

Partners pull the ignored bytes separately — the canonical demo pack is the
ModelScope dataset `tianyi3unipat/BabyVision-v2-Datasets` (private). A manifest
entry whose `path` points at an un-pulled file simply won't render until the
bytes are fetched into place; the manifest itself is always complete.

> **Large CAD is download-only.** Some assets (notably the LEGO Technic STEP
> files, 27 MB–800 MB each) are far too heavy to tessellate in-browser. These
> are shipped as download links, with a derived `.glb` used for the actual 3D
> view. The STEP→GLB conversion helper is `~/lego_step_to_glb.py` on the build
> box.

---

## Run the viewer locally

The dev server sets the docroot to the repo root, so the absolute `path` values
in the manifests resolve as-is:

```bash
python3 _dev_serve.py         # serves repo root on http://127.0.0.1:8080/
```

Then open **http://127.0.0.1:8080/viewer/** — start at `assets.html`
("Domain 3D Assets") to browse the gallery, which reads the manifests and links
each card straight into the right renderer.

The public deployment is **babyvision-v2.unipatai.com**.

---

## The viewers

35 static HTML renderer pages live directly under `viewer/`. Each takes the
asset via a URL query param, so any asset is a shareable link
(`/<renderer>.html?file=<path>`). The generic asset gallery (`assets.html`)
builds these links for the common renderers:

| `viewer` | Page | Engine | Formats |
|---|---|---|---|
| `gltf` | `gltf.html?file=` | Three.js | glb, gltf, obj, stl, ply |
| `molstar` | `molstar.html?file=&type=` | Mol* | mmcif, bcif, pdb, sdf, mol, mol2, xyz |
| `niivue` | `niivue.html?file=` (`&overlay=`) | NiiVue | nifti, nrrd (medical volumes) |
| `occt` | `occt.html?file=` | OpenCascade.js | step, iges (best-effort tessellation) |
| `usdz` | `usdz.html?file=` | USD / model-viewer | usdz |
| `usda` | `usda.html?file=` | USD | usda |
| `html` | *(opens the path directly)* | — | self-contained standalone demo pages |

Domain-specialized renderers also have their own pages but are opened from QA
items and domain pages rather than the generic gallery: `genesis_view.html`,
`urdf.html`, `gdsii.html`, `pointcloud.html`, `field3d.html`, `bandplot.html`,
`citymap.html` / `citymap-photo.html`, `saturn.html`, `mpbctl.html`,
`cad-native.html`, `cad-edrawings.html`, and others.

Front-end JS libraries are vendored under `viewer/vendor/` (checked with
`_vendor_check.html`); no build step or network fetch is required to render.

---

## Add an asset or a new viewer

- **New asset in an existing renderer** → add an entry to the domain's
  `viewer/assets/<domain>.json` (set `path`/`viewer`/`format`), drop the bytes
  where `path` points, done.
- **New renderer / format** → [`new-viewer-page.md`](new-viewer-page.md).
- **New domain** → [`new-domain-template.md`](new-domain-template.md).
- **Wiring a QA item to a viewer** → [`qa-to-viewer-loop.md`](qa-to-viewer-loop.md).

## Related, deeper references

- [`../CrawlDataAndViewer.md`](../CrawlDataAndViewer.md) — full crawl-data +
  viewer status write-up (Chinese).
- [`../DataSourcesAndFormats.md`](../DataSourcesAndFormats.md) — data-source and
  format survey.
- [`../3d_assets_inventory.md`](../3d_assets_inventory.md) — 3D-asset × VQA
  pairing status. *(Dated 2026-05-21; still references the upstream ScienceVision
  layout — read for the dataset-level pairing logic, not for current file paths.)*
