# Designing a new viewer page

> When the 19 viewer pages already shipped in `viewer/` (see
> [`../Tools.md`](../Tools.md) §A) don't fit your data format, you need
> a new one. This guide tells you when you actually need one, what the
> contract is, and how to wire it in.

---

## 1. Do you actually need a new viewer page?

Most partner domains do **not** need a new viewer page. Check the
existing 19 first:

| Your data | Reuse this | Set `asset_kind=` |
|---|---|---|
| `.glb`, `.gltf` mesh | `gltf.html` | `mesh` |
| `.ply` mesh | `gltf.html` | `ply` |
| `.nii`, `.nii.gz`, `.nrrd` volume | `niivue.html` | `nifti` |
| DICOM stack | `cornerstone.html` | (custom — see below) |
| Protein / molecule | `molstar.html` | (custom) |
| URDF / SDF robot | `urdf.html` | (custom) |
| Point cloud `.las` / `.laz` | `pointcloud.html` | (custom) |
| GDSII layout | `gdsii.html` | (custom) |
| ... | see full matrix in [`../Tools.md`](../Tools.md) §A | |

If one of these works for your data — even if it isn't a perfect fit —
**reuse it.** A new viewer page is a real maintenance liability (WebGL
context budget, lazy loading lifecycle, error handling, vendor JS
versioning). Don't add one to be tidy; add one because the existing
pages physically cannot render your format.

You need a new page when:

- The browser library you'd need isn't already vendored under
  `viewer/shared/vendor/`.
- The interaction primitives the existing page supports (slice scrub,
  camera orbit, opacity) are wrong for your data (e.g. a streamlines
  visualization where "rotate the volume" is meaningless).
- The asset format requires a parser the existing pages don't link
  (e.g. STEP / IGES needs `cad-native.html`'s OCCT bridge, which isn't
  in `gltf.html`).

---

## 2. The contract

Every viewer page in `viewer/` follows the same shape. Open
`viewer/niivue.html` (78 lines — the smallest fully-worked example) and
read it top-to-bottom; that's the contract:

```
viewer/<your-page>.html
├── <head>
│   ├── <title>... — Science Vision</title>
│   ├── <link rel="stylesheet" href="/viewer/shared/style.css">
│   ├── <script defer src="/viewer/shared/neg-banner.js">       ← negative-banner overlay
│   └── <link rel="preload" as="script" href=".../your-vendor.js">
├── <header class="topbar">
│   ├── ← Shell link back to /viewer/
│   ├── <strong>Your viewer name</strong>
│   └── <span id="filename" class="meta"></span>                ← filename echo
├── <body>
│   ├── <canvas id="gl"> or other render surface
│   ├── optional: control buttons (#view-toggle pattern)
│   └── <script>
│       1. read URLSearchParams (file=, overlay=, anything else)
│       2. show error if file= missing
│       3. load + render
│       4. attach interaction handlers
└── (vendor JS comes via <script src="/viewer/shared/vendor/...">)
```

The hard contract is exactly two things:

1. **Accept `?file=<repo-relative-asset-url>` as a query param.** The
   shell (`match3d.html`) mounts your page as an iframe with this URL.
2. **Stay inside the shared style + WebGL context budget.** Use
   `/viewer/shared/style.css`, render into one main element. The shell
   will mount and unmount your iframe; if you hold extra WebGL contexts
   beyond what's reasonable, the global budget gets crowded.

Everything else (controls, gesture hints, telemetry) is convention —
follow the existing pages, don't reinvent.

### Quickstart template

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Your viewer — Science Vision</title>
<link rel="stylesheet" href="/viewer/shared/style.css">
<script src="/viewer/shared/neg-banner.js" defer></script>
<link rel="preload" as="script" href="/viewer/shared/vendor/your-lib/your-lib.js">
<style>
  #stage { width: 100vw; height: calc(100vh - 49px); display: block;
           position: fixed; top: 49px; left: 0; }
</style>
</head>
<body>
<header class="topbar">
  <div><a class="back" href="/viewer/">← Shell</a>
       <strong>Your Viewer</strong>
       <span id="filename" class="meta"></span></div>
  <div class="meta">your one-line subtitle</div>
</header>
<canvas id="stage"></canvas>
<script src="/viewer/shared/vendor/your-lib/your-lib.js"></script>
<script>
  const params = new URLSearchParams(location.search);
  const file = params.get('file');
  document.getElementById('filename').textContent =
    file ? file.split('/').pop() : '(no file)';
  if (!file) {
    document.body.insertAdjacentHTML('beforeend',
      '<div class="error-box">No ?file= query param.</div>');
  } else {
    // initialize your library, load the asset, render
  }
</script>
</body>
</html>
```

---

## 3. Wire it into the shell

Once the page renders standalone, register it so `match3d.html` picks
it up. Open `viewer/match3d.html:409`:

```javascript
const ASSET_VIEWERS = {
  mesh:  url => `/viewer/gltf.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
  ply:   url => `/viewer/gltf.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
  nifti: url => `/viewer/niivue.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
  // your-kind: url => `/viewer/your-page.html?file=${encodeURIComponent(url)}&v=${BUILD_TAG}`,
};
```

Pick a short `asset_kind` token (`urdf`, `pointcloud`, `gdsii`, …). The
same token gets emitted by your match-builder iterator
(`scripts/match/build_matched_3d_qa.py`, see
[`new-domain-template.md`](new-domain-template.md) §2). Token consistency
on both ends is the only contract — there is no central registry to
update.

### Dual-pane variant

If your data has a natural overlay companion (NIfTI + mask, mesh + colored
labels), look at how `match3d.html`'s `slotInnerHTML(kind, url, maskUrl)`
special-cases `kind === 'nifti' && maskUrl` to emit two iframes side by
side. Adding a similar branch for your kind is a 10-line patch.

---

## 4. Vendor JS

Third-party libraries live under `viewer/shared/vendor/<lib>/<version>/`
so that:

- The viewer works offline (no CDN at runtime).
- A specific commit's behavior is reproducible — vendor versioning is
  pinned per directory.
- The version is auditable in PR diffs.

For a new viewer page that needs a new library:

1. Drop the library under `viewer/shared/vendor/<lib-name>/<version>/`.
2. Reference it from your page as
   `/viewer/shared/vendor/<lib-name>/<version>/<file>.js`.
3. Add a `<link rel="preload">` in `<head>` so the iframe shell can
   warm-fetch it.

`viewer/vendor/README.md` documents what's already vendored.

---

## 5. Common pitfalls

- **No error box on missing `?file=`** — every existing page does this;
  copy it. Without it, your page is silent when mounted by the shell
  before a slot is ready.
- **Hardcoded data paths** — paths come from the manifest layer
  (`window.Manifest.data.*` via `viewer/shared/manifest.js`), or via the
  `?file=` query param when invoked through `match3d.html`. Never
  hard-code an `assets.index.json` path or a dataset-specific filename;
  see `docs/platform/architecture.md` "Manifest layer".
- **Holding extra WebGL contexts** — the shell evicts iframes when its
  context budget fills. If your page holds N hidden canvases, the
  eviction math underestimates your cost and the user sees crashes.
- **No `<a class="back" href="/viewer/">← Shell</a>`** — users who
  bookmark your page directly need a way back. Match the existing
  topbar layout.
- **Drift between standalone and shell view** — test both. Open
  `/viewer/your-page.html?file=...` directly **and** through
  `/viewer/match3d.html`. The shell mounts you in an iframe with
  `referrerpolicy="no-referrer"` — anything that depends on referrer
  will fail only inside the shell.

---

## 6. When in doubt

Walk through `viewer/niivue.html` end-to-end as a template, and then
walk through `viewer/match3d.html` lines 405–450 to see where your new
page plugs in. The vocabulary, error states, and gesture hints are all
short enough to read in a single sitting.

For the broader manifest contract (how non-asset URLs like docs and
QA-Gen index are wired), see
[`architecture.md`](architecture.md) "Manifest layer".
