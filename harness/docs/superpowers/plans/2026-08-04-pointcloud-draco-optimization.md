# Point-Cloud Draco Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-encode the six 102 MB bridge-LiDAR `.las` point clouds to ~4.6 MB Draco `.drc` (all points kept, classification preserved) and teach `pointcloud.html` to decode them, so every architecture point-cloud viewer opens in < 2 s even on a slow (~20-30 Mbps) China → Aliyun link.

**Architecture:** An offline batch encoder (`tools/las_to_draco.py`, run under a `uv` Python 3.12 venv with DracoPy + laspy) reads each `.las`, centers positions by bbox midpoint exactly as the viewer does, packs the per-point classification into the Draco color.R channel, and writes a `<name>.drc` next to the `.las`. `pointcloud.html` gains a `.drc` branch that decodes with the already-vendored three.js `DRACOLoader` + `draco_decoder.wasm`, reconstructs the same `{positions, classification, intensities, colors}` shape the LAS parser produces, and feeds it into the unchanged color-mode / legend logic. `architecture.json` is repointed at the `.drc` files. The `.las` (+ `.zst`/`.gz` sidecars) stay on disk for download.

**Tech Stack:** Python 3.12 (uv-managed venv), DracoPy 2.0.0, laspy 2.7.0, numpy; three.js 0.160.0 (`DRACOLoader.js` + `libs/draco/gltf/draco_decoder.wasm`), Caddy working-tree file server.

## Global Constraints

- **All points kept — no 降采样.** The only allowed point-count reduction is Draco's merge of *exact-coordinate duplicate* source points (measured: 118 of 3,000,000 on Banwol, identical at q14/q16/q18 → genuine source dups, spatially lossless). Any encoder that would drop non-duplicate points is a failure.
- **Quantization = 14 bits** (`quantization_bits=14`) → 4.56 MB, 3.2 mm resolution (finer than bridge-LiDAR noise). Do not lower.
- **Centering must match the viewer exactly:** subtract per-axis bbox midpoint `(min + max) / 2` (Float64 math, cast positions to Float32 after). Any other origin breaks the viewer's camera auto-fit.
- **Classification packing:** `classification = raw & 0x1f` → `colors[:,0]` (values 0-31, exact in Draco's 8-bit color). Intensity and RGB are all-zero in these clouds and are dropped.
- **Encoder venv:** `uv` is the only working Python packaging path on this box (system Python 3.14 has no usable pip/wheels). A ready venv exists at `/tmp/dracoenv`; if absent, recreate with the command in the script header.
- **`.drc` files are NOT git-tracked** (crawl_data binaries are gitignored, like the `.las`). They are generated data artifacts served from the working tree. Only code (`tools/las_to_draco.py`, `viewer/pointcloud.html`, `viewer/assets/architecture.json`) is committed.
- **Working-tree deploy:** editing files under `~/BabyVision-v2` is the deploy — Caddy serves the working tree directly. HTML/JSON are `no-cache` (revalidate); `.drc` falls under `max-age=86400`, so append a `?v=` cache-buster if a `.drc` is ever re-encoded in place.
- **Six clouds:** `crawl_data/architecture/bridge_pcl/{Banwol,Ipbuk,Palgok,Seokdap,Ssanghak,Wonli}-Bridge.las`.

---

### Task 1: Encoder script `tools/las_to_draco.py` with synthetic self-test

**Files:**
- Create: `tools/las_to_draco.py`

**Interfaces:**
- Produces (importable, for the self-test and for reuse):
  - `las_arrays(path: str) -> tuple[np.ndarray, np.ndarray]` — returns `(pts_f32 [N,3] centered by bbox midpoint, cls_u8 [N] = raw & 0x1f)`.
  - `encode_cloud(pts_f32: np.ndarray, cls_u8: np.ndarray, *, quantization_bits: int = 14, compression_level: int = 7) -> bytes` — packs `cls_u8` into `colors[:,0]` and returns Draco bytes.
  - `decode_check(drc_bytes: bytes) -> tuple[np.ndarray, np.ndarray]` — decodes and returns `(pts [M,3] float32, cls [M] uint8 from round(color.R))`; used by the self-test and Task 2 verification.
- CLI: `python las_to_draco.py [--self-test] [FILE.las ...]`. With `--self-test`, runs the synthetic round-trip and exits 0/1. With file args (or no args → default glob of the six clouds), encodes each and prints `name, src_pts, out_pts, merged, out_MB`.

- [ ] **Step 1: Write the failing self-test**

Put this at the bottom of `tools/las_to_draco.py`, guarded so it only runs under `--self-test`. Write it *before* the functions it calls exist:

```python
def _self_test() -> int:
    import numpy as np
    # 1000 points on a deterministic lattice in a ~50-unit cube, offset far from
    # origin (mimics UTM), with 5 classes cycled + a couple of exact duplicates.
    n = 1000
    g = np.linspace(0.0, 50.0, n, dtype=np.float64)
    xs = 445000.0 + g
    ys = 3766000.0 + (g * 0.5)
    zs = 12.0 + np.sin(g) * 3.0
    raw_cls = (np.arange(n) % 5).astype(np.uint8)
    # inject 2 exact-duplicate points (same coords) — Draco will merge these
    xs[10] = xs[11]; ys[10] = ys[11]; zs[10] = zs[11]; raw_cls[10] = raw_cls[11]

    cx = (xs.min() + xs.max()) / 2
    cy = (ys.min() + ys.max()) / 2
    cz = (zs.min() + zs.max()) / 2
    pts = np.column_stack([xs - cx, ys - cy, zs - cz]).astype(np.float32)
    cls = raw_cls & 0x1f

    buf = encode_cloud(pts, cls, quantization_bits=14)
    dpts, dcls = decode_check(buf)

    # (a) point count: source minus exact dups (Draco merges the 1 coincident pair)
    assert dpts.shape[0] in (n, n - 1), f"unexpected point count {dpts.shape[0]}"
    # (b) class histogram preserved (allowing the one merged dup)
    src_hist = np.bincount(cls, minlength=32)
    out_hist = np.bincount(dcls, minlength=32)
    assert np.abs(src_hist - out_hist).sum() <= 1, f"class histogram drift {src_hist} vs {out_hist}"
    # (c) spatial fidelity: every decoded point within one quantization step of
    #     a source point. step = extent / 2^14.
    extent = float(np.max(pts.max(0) - pts.min(0)))
    step = extent / (2 ** 14)
    # nearest-source distance for a sample of decoded points
    import numpy as _np
    idx = _np.linspace(0, dpts.shape[0] - 1, 50).astype(int)
    for i in idx:
        d = _np.linalg.norm(pts - dpts[i], axis=1).min()
        assert d <= step * 2, f"point {i} off by {d} > 2*step {step}"
    print("self-test OK: pts=%d hist_drift=%d step=%.4g" %
          (dpts.shape[0], int(_np.abs(src_hist - out_hist).sum()), step))
    return 0
```

- [ ] **Step 2: Run the self-test to verify it fails**

Run: `/tmp/dracoenv/bin/python tools/las_to_draco.py --self-test`
Expected: FAIL — `NameError: name 'encode_cloud' is not defined` (functions not written yet).

- [ ] **Step 3: Write the module (functions + CLI)**

Write the rest of `tools/las_to_draco.py` above `_self_test`:

```python
#!/usr/bin/env python3
"""Re-encode bridge-LiDAR .las point clouds to Draco .drc for fast web loading.

Env (uv is the only working Python packaging path on this box):
    uv venv --python 3.12 /tmp/dracoenv
    /tmp/dracoenv/bin/uv pip install DracoPy==2.0.0 laspy==2.7.0 numpy
Run:
    /tmp/dracoenv/bin/python tools/las_to_draco.py            # encode the 6 default clouds
    /tmp/dracoenv/bin/python tools/las_to_draco.py --self-test
    /tmp/dracoenv/bin/python tools/las_to_draco.py a.las b.las # encode specific files

Classification is packed into color.R (0-31, exact in Draco 8-bit color).
Positions are centered by per-axis bbox midpoint (min+max)/2 to MATCH
viewer/pointcloud.html's parseLAS centering, so the camera auto-fit is unchanged.
Intensity and RGB are all-zero in these clouds and are intentionally dropped.
"""
import glob
import os
import sys

import numpy as np
import DracoPy

DEFAULT_GLOB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "crawl_data", "architecture", "bridge_pcl", "*-Bridge.las",
)


def las_arrays(path):
    """Load a .las → (pts_f32 [N,3] centered by bbox midpoint, cls_u8 [N])."""
    import laspy
    las = laspy.read(path)
    x = np.asarray(las.x, dtype=np.float64)
    y = np.asarray(las.y, dtype=np.float64)
    z = np.asarray(las.z, dtype=np.float64)
    cx = (x.min() + x.max()) / 2
    cy = (y.min() + y.max()) / 2
    cz = (z.min() + z.max()) / 2
    pts = np.column_stack([x - cx, y - cy, z - cz]).astype(np.float32)
    cls = (np.asarray(las.classification) & 0x1f).astype(np.uint8)
    return pts, cls


def encode_cloud(pts_f32, cls_u8, *, quantization_bits=14, compression_level=7):
    """Draco-encode points with classification packed into color.R."""
    n = pts_f32.shape[0]
    colors = np.zeros((n, 3), dtype=np.uint8)
    colors[:, 0] = cls_u8
    return DracoPy.encode(
        pts_f32, colors=colors,
        quantization_bits=quantization_bits,
        compression_level=compression_level,
    )


def decode_check(drc_bytes):
    """Decode .drc → (pts [M,3] float32, cls [M] uint8 recovered from color.R)."""
    m = DracoPy.decode(drc_bytes)
    pts = np.asarray(m.points, dtype=np.float32)
    colors = np.asarray(m.colors)
    # DracoPy returns colors as uint8 0-255; color.R holds the class id directly.
    cls = np.rint(colors[:, 0]).astype(np.uint8) if colors.size else np.zeros(pts.shape[0], np.uint8)
    return pts, cls


def encode_file(path, quantization_bits=14, compression_level=7):
    """Encode one .las → sibling .drc. Returns (out_path, src_pts, out_pts, merged, out_bytes)."""
    pts, cls = las_arrays(path)
    buf = encode_cloud(pts, cls, quantization_bits=quantization_bits,
                        compression_level=compression_level)
    out_path = os.path.splitext(path)[0] + ".drc"
    with open(out_path, "wb") as f:
        f.write(buf)
    dpts, _ = decode_check(buf)
    return out_path, pts.shape[0], dpts.shape[0], pts.shape[0] - dpts.shape[0], len(buf)


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if "--self-test" in argv:
        return _self_test()
    files = args if args else sorted(glob.glob(DEFAULT_GLOB))
    if not files:
        print("no .las files found (glob: %s)" % DEFAULT_GLOB, file=sys.stderr)
        return 1
    print("%-22s %10s %10s %7s %8s" % ("file", "src_pts", "out_pts", "merged", "out_MB"))
    for p in files:
        out, src, out_pts, merged, nbytes = encode_file(p)
        print("%-22s %10d %10d %7d %8.2f" %
              (os.path.basename(out), src, out_pts, merged, nbytes / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the self-test to verify it passes**

Run: `/tmp/dracoenv/bin/python tools/las_to_draco.py --self-test`
Expected: PASS — prints `self-test OK: pts=999 hist_drift=... step=...` and exits 0. (999 because the one injected coincident pair merges; `hist_drift` ≤ 1.)

- [ ] **Step 5: Commit**

```bash
cd ~/BabyVision-v2
git add tools/las_to_draco.py
git commit -m "feat(tools): add las_to_draco point-cloud encoder with self-test"
```

---

### Task 2: Encode the six real clouds and verify fidelity

**Files:**
- Generates (on disk, gitignored): `crawl_data/architecture/bridge_pcl/{Banwol,Ipbuk,Palgok,Seokdap,Ssanghak,Wonli}-Bridge.drc`

**Interfaces:**
- Consumes: `encode_file` / `decode_check` / `las_arrays` from Task 1.

- [ ] **Step 1: Write the fidelity-verification script (the "test")**

Create it inline (throwaway, not committed) at `/tmp/verify_drc.py`:

```python
import glob, os, sys
sys.path.insert(0, os.path.expanduser("~/BabyVision-v2/tools"))
import numpy as np
from las_to_draco import las_arrays, decode_check

base = os.path.expanduser("~/BabyVision-v2/crawl_data/architecture/bridge_pcl")
fails = 0
for las in sorted(glob.glob(os.path.join(base, "*-Bridge.las"))):
    drc = os.path.splitext(las)[0] + ".drc"
    if not os.path.exists(drc):
        print("MISSING", os.path.basename(drc)); fails += 1; continue
    spts, scls = las_arrays(las)
    with open(drc, "rb") as f:
        dpts, dcls = decode_check(f.read())
    size_mb = os.path.getsize(drc) / 1e6
    merged = spts.shape[0] - dpts.shape[0]
    # (a) point loss is only exact-duplicate merges (tiny fraction)
    ok_pts = 0 <= merged <= spts.shape[0] * 0.0001
    # (b) class set preserved and histograms match within the merged count
    sh = np.bincount(scls, minlength=32); dh = np.bincount(dcls, minlength=32)
    ok_hist = np.abs(sh - dh).sum() <= merged
    # (c) size within budget
    ok_size = 3.0 <= size_mb <= 6.0
    tag = "OK" if (ok_pts and ok_hist and ok_size) else "FAIL"
    if tag == "FAIL": fails += 1
    print("%-22s pts=%d merged=%d classes=%s %.2fMB %s" % (
        os.path.basename(drc), dpts.shape[0], merged,
        sorted(set(dcls.tolist())), size_mb, tag))
sys.exit(1 if fails else 0)
```

- [ ] **Step 2: Run the encoder on all six clouds**

Run: `cd ~/BabyVision-v2 && /tmp/dracoenv/bin/python tools/las_to_draco.py`
Expected: six rows printed, each `out_MB ≈ 4.5`, `merged` a tiny number (≈118 on Banwol), `out_pts ≈ 2,999,882`. Takes ~1-2 min total.

- [ ] **Step 3: Run the fidelity verification**

Run: `/tmp/dracoenv/bin/python /tmp/verify_drc.py`
Expected: six `OK` rows (point loss ≤ 0.01 %, class histograms match within merged count, 3-6 MB each); exit 0.

- [ ] **Step 4: Confirm the files exist and are the right size**

Run: `ls -la ~/BabyVision-v2/crawl_data/architecture/bridge_pcl/*.drc`
Expected: six `.drc` files, ~4.5 MB each (~27 MB total).

- [ ] **Step 5: No commit (data artifacts are gitignored)**

Confirm they are ignored, so they are not accidentally staged:
Run: `cd ~/BabyVision-v2 && git status --porcelain crawl_data/architecture/bridge_pcl/`
Expected: empty output (the `.drc` are ignored just like the `.las`).

---

### Task 3: Add the `.drc` decode branch to `pointcloud.html`

**Files:**
- Modify: `viewer/pointcloud.html`

**Interfaces:**
- Consumes: three.js `DRACOLoader` at `three/addons/loaders/DRACOLoader.js`; decoder at `/viewer/shared/vendor/three/0.160.0/examples/jsm/libs/draco/gltf/`.
- Produces: a `parsed` object with the SAME fields the existing `parseLAS` returns, so `chooseInitialMode` / `applyColorMode` / `renderLegend` work unchanged:
  `{ positions:Float32Array, intensities:Float32Array, colors:null, classification:Uint8Array, nPoints, pdrFormat:'drc', hasRGB:false, usableRGB:false, rgbDepth:0, rgbMax:0, versionLabel, extentLabel, zRange:[min,max] }`.

- [ ] **Step 1: Add a `parseDRC` helper (mirrors `parseLAS`'s output shape)**

In `viewer/pointcloud.html`, add the `DRACOLoader` import next to the existing imports (after the `OrbitControls` import on line 48):

```js
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
```

Then, immediately after the `parseLAS` function (after its closing `}` at line 155), add:

```js
// Build classification-only data from a decoded Draco BufferGeometry. The
// encoder (tools/las_to_draco.py) packs classification into color.R (0-31) and
// centers positions by bbox midpoint exactly as parseLAS does, so we only need
// to recover positions + class id and synthesize the remaining fields.
function geomToParsed(geom) {
  const pos = geom.attributes.position.array;          // Float32Array, already centered
  const nPoints = pos.length / 3;
  const positions = pos instanceof Float32Array ? pos : new Float32Array(pos);

  const classification = new Uint8Array(nPoints);
  const colAttr = geom.attributes.color;
  if (colAttr) {
    const col = colAttr.array;
    // DRACOLoader may hand back color as raw 0-255 or normalized 0-1; detect
    // and recover the integer class id either way.
    let cmax = 0;
    for (let i = 0; i < col.length; i++) if (col[i] > cmax) cmax = col[i];
    const scale = cmax <= 1 ? 255 : 1;
    for (let i = 0; i < nPoints; i++) {
      classification[i] = Math.round(col[i * 3] * scale) & 0x1f;
    }
  }

  // z-range for the Z-height color mode.
  let zmin = Infinity, zmax = -Infinity;
  for (let i = 0; i < nPoints; i++) {
    const z = positions[i * 3 + 2];
    if (z < zmin) zmin = z;
    if (z > zmax) zmax = z;
  }
  // full extent label from the position bounds
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  for (let i = 0; i < nPoints; i++) {
    const x = positions[i * 3], y = positions[i * 3 + 1];
    if (x < xmin) xmin = x; if (x > xmax) xmax = x;
    if (y < ymin) ymin = y; if (y > ymax) ymax = y;
  }

  return {
    positions,
    intensities: new Float32Array(nPoints),   // no intensity in .drc
    colors: null,                              // no true RGB
    classification,
    nPoints,
    pdrFormat: 'drc',
    hasRGB: false, usableRGB: false, rgbDepth: 0, rgbMax: 0,
    versionLabel: 'Draco',
    extentLabel: `${(xmax - xmin).toFixed(1)} × ${(ymax - ymin).toFixed(1)} × ${(zmax - zmin).toFixed(1)} units`,
    zRange: [zmin, zmax],
  };
}

// Decode a .drc URL → the same `parsed` shape parseLAS produces.
function loadDRC(url) {
  const loader = new DRACOLoader();
  loader.setDecoderPath('/viewer/shared/vendor/three/0.160.0/examples/jsm/libs/draco/gltf/');
  loader.setDecoderConfig({ type: 'wasm' });
  return new Promise((resolve, reject) => {
    loader.load(url, (geom) => resolve(geomToParsed(geom)), undefined, reject);
  });
}
```

- [ ] **Step 2: Branch the fetch/parse path on file extension**

Replace the whole `else { fetch(file)...` block (lines 250-286, from `} else {` through the `.catch(...)` that closes it) with a branch that shares one `buildScene(parsed)` continuation. New block:

```js
if (!file) {
  fail('No ?file= query param.');
} else {
  const isDRC = /\.drc(\?|$)/i.test(file);
  const parsePromise = isDRC
    ? loadDRC(file)
    : fetch(file)
        .then(r => r.ok ? r.arrayBuffer() : Promise.reject('HTTP ' + r.status))
        .then(buf => { window.__lasBytes = buf.byteLength; return parseLAS(buf); });

  parsePromise
    .then(p => {
      parsed = p;
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(parsed.positions, 3));
      geom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(parsed.positions.length), 3));

      const mat = new THREE.PointsMaterial({ size: 2.0, sizeAttenuation: false, vertexColors: true });
      points = new THREE.Points(geom, mat);
      points.name = 'pointcloud';
      scene.add(points);
      cutaway.setRoot(points);

      const initialMode = chooseInitialMode(parsed);
      document.getElementById('cmode').value = initialMode;
      applyColorMode(initialMode);

      geom.computeBoundingSphere();
      const r = geom.boundingSphere.radius || 1;
      camera.position.set(r * 1.2, -r * 1.2, r * 1.0);
      camera.near = r / 1000; camera.far = r * 100; camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.update();

      const sizeTag = window.__lasBytes ? ` · ${(window.__lasBytes / 1024).toFixed(1)} KB` : '';
      const rgbTag = parsed.usableRGB ? ` · RGB ${parsed.rgbDepth}-bit (max ${parsed.rgbMax})`
        : (parsed.hasRGB ? ' · RGB empty (using labels)' : '');
      document.getElementById('stats').textContent =
        `${parsed.versionLabel} · ${parsed.nPoints.toLocaleString()} pts · ${parsed.extentLabel}${sizeTag}${rgbTag}`;
    })
    .catch(err => fail('Failed to load / parse:\n' + err));
}
```

Also update the header subtitle on line 30 so it no longer claims LAS-only:

```html
  <div class="meta">LAS 1.x / Draco · three.js Points</div>
```

- [ ] **Step 3: Verify the file parses (no syntax errors) and the decoder assets resolve**

Run:
```bash
cd ~/BabyVision-v2
node --check <(sed -n '/<script type="module">/,/<\/script>/p' viewer/pointcloud.html | sed '1d;$d') 2>&1 | head || echo "note: import lines will error under node --check; ignore import-statement errors, look for bracket/paren mismatches"
curl -sI http://localhost/viewer/shared/vendor/three/0.160.0/examples/jsm/loaders/DRACOLoader.js | head -1
curl -sI http://localhost/viewer/shared/vendor/three/0.160.0/examples/jsm/libs/draco/gltf/draco_decoder.wasm | head -1
```
Expected: both `curl` calls return `HTTP/... 200`. (The `node --check` on an ES-module fragment will flag the bare `import` lines regardless — this step is only to catch bracket/paren typos in the code you added, not to fully validate the module.)

- [ ] **Step 4: Manual browser verification (no headless browser on this box)**

Open in a real browser against live (or via an SSH port-forward):
`https://babyvision-v2.unipatai.com/pointcloud.html?file=/crawl_data/architecture/bridge_pcl/Banwol-Bridge.drc&classes=Background,Superstructure,Column,Pier%20Cap,Guardrail`

Verify:
- Cloud renders, centered and framed (camera auto-fit looks identical to the `.las` version).
- Default color mode is **Classification**; the legend lists the 5 class names.
- The stats line shows `Draco · 2,999,882 pts · ... units`.
- Switching color mode to Z height / Intensity still works (intensity is uniform black — expected, no intensity data).
- Browser devtools Network tab: the `.drc` transfer is ~4.5 MB and the page is interactive in < 2 s on a normal connection.

- [ ] **Step 5: Commit**

```bash
cd ~/BabyVision-v2
git add viewer/pointcloud.html
git commit -m "feat(viewer): decode Draco .drc point clouds via DRACOLoader (classification in color.R)"
```

---

### Task 4: Repoint `architecture.json` at the `.drc` files

**Files:**
- Modify: `viewer/assets/architecture.json`

**Interfaces:**
- Consumes: the six `.drc` files from Task 2 and the `.drc` viewer branch from Task 3.
- Produces: six `pointcloud` entries whose `path` ends in `.drc` and whose `format` is `drc`; `classes` unchanged. `assets.html`'s `viewerURL` already builds `/pointcloud.html?file=<path>&classes=<...>` from these fields — no `assets.html` change needed.

- [ ] **Step 1: Rewrite the six pointcloud entries**

For each of the six `pointcloud` entries (Banwol, Ipbuk, Palgok, Seokdap, Ssanghak, Wonli), change `path` from `...-Bridge.las` to `...-Bridge.drc`, set `"format": "drc"`, and update `size` to the actual `.drc` byte size, and add a `las_download` field preserving the original for download. Example for Banwol (apply the analogous edit to all six):

```json
  {
    "name": "Banwol-Bridge",
    "path": "/crawl_data/architecture/bridge_pcl/Banwol-Bridge.drc",
    "size": 4560000,
    "viewer": "pointcloud",
    "format": "drc",
    "note": "桥梁 LiDAR 点云 — per-point 结构标签 (Background,Superstructure,Column,Pier Cap,Guardrail); 探伤几何底座",
    "classes": "Background,Superstructure,Column,Pier Cap,Guardrail",
    "las_download": "/crawl_data/architecture/bridge_pcl/Banwol-Bridge.las"
  },
```

Use the real per-file size from `ls -la ...*.drc` (Task 2 step 4) for each `size`.

- [ ] **Step 2: Verify JSON is valid**

Run: `cd ~/BabyVision-v2 && python3 -c "import json; d=json.load(open('viewer/assets/architecture.json')); pcs=[a for a in d if a['viewer']=='pointcloud']; assert len(pcs)==6, len(pcs); assert all(a['path'].endswith('.drc') and a['format']=='drc' and a.get('las_download','').endswith('.las') for a in pcs), pcs; print('OK', len(pcs), 'pointcloud entries →', pcs[0]['path'])"`
Expected: `OK 6 pointcloud entries → /crawl_data/architecture/bridge_pcl/Banwol-Bridge.drc`

- [ ] **Step 3: Verify each referenced `.drc` exists on disk**

Run: `cd ~/BabyVision-v2 && python3 -c "import json,os; d=json.load(open('viewer/assets/architecture.json')); [print('MISSING' if not os.path.exists('.'+a['path']) else 'ok', a['path']) for a in d if a['viewer']=='pointcloud']"`
Expected: six `ok ...` lines, no `MISSING`.

- [ ] **Step 4: Commit**

```bash
cd ~/BabyVision-v2
git add viewer/assets/architecture.json
git commit -m "feat(assets): point architecture point-cloud entries at .drc (keep .las for download)"
```

---

### Task 5: Deploy verification + load-time proof sweep

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything from Tasks 1-4, live-served from the working tree.

- [ ] **Step 1: Confirm the `.drc` are served with the right size and cache header**

Run:
```bash
for f in Banwol Ipbuk Palgok Seokdap Ssanghak Wonli; do
  curl -sI "https://babyvision-v2.unipatai.com/crawl_data/architecture/bridge_pcl/${f}-Bridge.drc" \
    | awk 'BEGIN{IGNORECASE=1}/^HTTP|content-length|cache-control/{printf "%s ", $0}'; echo " [$f]";
done
```
Expected: each returns `HTTP ... 200`, `content-length ≈ 4,500,000`, `cache-control: public, max-age=86400`.

- [ ] **Step 2: Time the point-cloud transfer end-to-end**

Run:
```bash
curl -s -o /dev/null -w "size=%{size_download} time_total=%{time_total}s\n" \
  "https://babyvision-v2.unipatai.com/crawl_data/architecture/bridge_pcl/Banwol-Bridge.drc"
```
Expected from this H20 box: `size ≈ 4,560,000`, `time_total < 0.5 s`. Budget math: 4.56 MB / 20 Mbps ≈ 1.8 s wire + decode → under the 2 s target on a slow China link.

- [ ] **Step 3: Run the user's per-domain sweep — 3 assets × each domain + 3 QA tasks**

Pull three asset URLs per domain from the asset JSONs and three QA-gen task URLs, then time each with `curl`. Write and run `/tmp/sweep.sh`:

```bash
#!/bin/bash
BASE=https://babyvision-v2.unipatai.com
t() { curl -s -o /dev/null -w "%{time_total}s  %{size_download}B  $1\n" "$1"; }
echo "== Architecture (point clouds now .drc) =="
for f in Banwol Ipbuk Palgok; do t "$BASE/crawl_data/architecture/bridge_pcl/${f}-Bridge.drc"; done
echo "== Biology (Mol* mmCIF/PDB) =="
python3 - "$BASE" <<'PY'
import json,urllib.request,sys
base=sys.argv[1]
for dom,src in [("biology","assets/biology.json"),("medical","assets/medical.json"),
                ("industrial","assets/industrial.json")]:
    j=json.load(urllib.request.urlopen(base+"/viewer/"+src))
    items=(j if isinstance(j,list) else j.get("assets",[]))[:3]
    print(f"-- {dom} --")
    for a in items: print("  ASSET", a["path"])
PY
```
Then time the printed asset paths and three QA-gen task URLs (open `qagen.html`, pick 3 task viewer links) with `t <url>`.
Expected: **every** asset and QA task `time_total < 2 s` from this box; the three point clouds land ~0.3-0.5 s (down from 7.35 s raw / 0.68 s zstd). Record the numbers.

- [ ] **Step 4: Manual browser spot-check of the three domains' viewers**

Open one asset per domain in a real browser (point cloud, a Mol* structure, a Niivue volume, a glTF/CAD model) and one QA-gen task; confirm each renders and is interactive within ~2 s. This catches render-time regressions that a `curl` transfer timing cannot.

- [ ] **Step 5: Remind the user of the durable Caddy sudo step**

The Phase-1 transport fix (precompressed sidecars + tiered cache) is live via the no-sudo admin reload but **not durable** — a `babyvision-viewer` service restart/reboot reverts it. Remind the user to run (needs sudo, user runs):
```bash
sudo cp ~/Caddyfile.tiered-cache /etc/caddy/Caddyfile && sudo systemctl reload babyvision-viewer
```
This does not affect the `.drc` work (which is pure working-tree content) but locks in the header/precompressed config the point clouds' `.las` fallback still relies on.

---

## Self-Review Notes

- **Spec coverage:** Encoder toolchain + format decision → Task 1/2; viewer `.drc` branch (DRACOLoader, classification from color.R, reconstruct parsed shape, z-range/legend) → Task 3; asset wiring → Task 4; verification (encoder correctness, viewer correctness, load-time proof) → Tasks 2/3/5; durable Caddy reminder → Task 5. Medical NII explicitly deferred in spec (no task) — intentional.
- **Fidelity guarantee:** "all points kept" is enforced by the Task 2 verification asserting merged-count ≤ 0.01 % and class-histogram match; the only loss is exact-duplicate merges, documented in Global Constraints.
- **Type consistency:** `geomToParsed` returns exactly the field set `parseLAS` returns (`positions, intensities, colors, classification, nPoints, pdrFormat, hasRGB, usableRGB, rgbDepth, rgbMax, versionLabel, extentLabel, zRange`), so `chooseInitialMode`/`applyColorMode`/`renderLegend` consume it unchanged. `las_arrays`/`encode_cloud`/`decode_check` signatures match between Task 1 definition and Task 2 usage.
- **Robustness:** color-channel unpack handles both normalized (0-1) and raw (0-255) DRACOLoader output via runtime max detection, so the plan doesn't depend on an unverified assumption about three.js standalone-`.drc` color normalization.
