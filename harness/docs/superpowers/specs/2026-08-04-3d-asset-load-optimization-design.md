# 3D Asset Load Optimization — Design

**Date:** 2026-08-04
**Goal:** Every 3D asset viewer and QA-gen task on babyvision-v2.unipatai.com opens in **< 2 s**, measured 国内→阿里云 (China → Aliyun Sing server). Full-fidelity assets only — no 降采样 (point dropping); format-level compression is allowed.

## Baseline (measured from the H20 box, ~100 Mbps to Aliyun)

The live server was already largely optimized (HTTPS, HTTP/2, gzip on JS/WASM, tiered immutable-vendor cache; GLBs already meshopt-compressed on the current perf branch). Vendor libs: Mol* 1.4MB gz, occt.wasm 3.3MB gz, three 0.27MB gz — all < 0.3 s and immutable-cached after first open.

One real defect and two borderline categories:

| Category | On-wire (before) | Time | Status |
|---|---|---|---|
| Architecture point cloud (`.las`) | **102 MB (raw!)** | **7.35 s** | ❌ defect |
| Medical full-brain NII (IXI) | 10.7 MB `.nii.gz` | ~0.85 s here | borderline on slow links |
| Biology/Chem/Industrial/QA assets | ≤ 2.2 MB | < 0.6 s | ✅ |

## Phase 1 — Transport fix (DONE, live)

**Defect:** the running `file_server` had no `precompressed` directive, so `encode gzip zstd` (which skips `application/octet-stream`) let the 102 MB `.las` ship raw while its 18 MB `.zst` sidecar sat unused.

**Fix:** `file_server { precompressed zstd gzip }`.
- Applied live via Caddy admin reload (zero downtime) and persisted to `~/Caddyfile.tiered-cache`; repo `Caddyfile.https.staged` patched to match (`.https.example` + `deploy/Caddyfile` already had it).
- Result: point cloud **102 MB / 7.35 s → 18.8 MB zstd / 0.68 s** (10.8×). Browser decompresses transparently; no viewer change; all points kept.
- ⚠️ **Durability (needs sudo, user runs):** `sudo cp ~/Caddyfile.tiered-cache /etc/caddy/Caddyfile && sudo systemctl reload babyvision-viewer` — else a service restart/reboot reverts this and the tiered-cache header fix.

## Phase 2 — Point-cloud Draco re-encode (this spec's implementation)

18.8 MB is fine on a fast link but ~5 s at 30 Mbps — the one category still over budget on a real China network. Re-encode to Draco.

**Encoder toolchain:** `uv venv --python 3.12` + `DracoPy 2.0.0` + `laspy 2.7.0` (uv is the only working package path on this box; system Python 3.14 has no pip/wheels and no cmake). Offline batch job, not a runtime dependency.

**Format decision (measured on Banwol-Bridge, 3,000,000 pts, 52.7-unit extent):**

| quantization | size | resolution | merged (dup) pts |
|---|---|---|---|
| **q14 (chosen)** | **4.56 MB** | 3.2 mm | 118 (0.0039%) |
| q16 | 6.80 MB | 0.8 mm | 118 |
| q18 | 9.02 MB | 0.2 mm | 118 |

- **q14 chosen:** 4.56 MB ≈ 1.2 s at 30 Mbps — under budget with margin. 3.2 mm is finer than bridge-LiDAR noise.
- **Fidelity / "all points":** the 118 removed points are **exact-coordinate duplicates in the source** (identical count at every quantization level) — spatial coverage is lossless, not 降采样. Disclosed here as the one deviation from a byte-exact point count.
- **Attributes:** these clouds have **all-zero intensity** and empty RGB; classification is the only meaningful per-point attribute (5 classes). Pack `classification → color.R` (0–31, survives Draco's 8-bit color exactly). Intensity/RGB dropped (they carry no information here).

**Encoder script** (`tools/las_to_draco.py`, committed):
1. `laspy.read` each `crawl_data/architecture/bridge_pcl/*.las`.
2. Center positions by bbox midpoint `(min+max)/2` per axis — must match the viewer's centering so camera auto-fit is unchanged.
3. `classification = raw & 0x1f` → `colors[:,0]`.
4. `DracoPy.encode(pts, colors=colors, quantization_bits=14, compression_level=7)` → write `<name>.drc` alongside the `.las`.
5. Print per-file: point count, merged count, output size (idempotent; safe to re-run).

**Viewer change** (`viewer/pointcloud.html`):
- Branch on file extension: `.drc` → decode with three.js `DRACOLoader` (already vendored: `examples/jsm/loaders/DRACOLoader.js` + `libs/draco/gltf/draco_decoder.wasm`); `.las` path retained as fallback.
- `DRACOLoader` sets `setDecoderPath(<vendored draco dir>)`; decode standalone `.drc` → `BufferGeometry` with `position` + `color`.
- Reconstruct the parser's data shape: `positions` from geometry; `classification = round(color.R * 255)` unpacked into a `Uint8Array`; `intensities` = zeros; `colors` (true RGB) = null. Feed into the existing `chooseInitialMode` / `applyColorMode` / legend logic unchanged.
- z-range and bounding sphere from the decoded positions (as today).
- `?classes=` legend labels unchanged.

**Asset wiring** (`viewer/assets/architecture.json` and any QA index referencing these paths): point the `pointcloud` viewer entries at the `.drc` file. Keep `.las` (+ `.zst`) on disk for download.

## Verification

- **Encoder correctness:** decoded point count = source − exact-dup count; per-class histogram matches source minus merged; max position error < quantization step. Asserted in the encoder's self-check output.
- **Viewer correctness:** no headless browser on box → verify decode headlessly with the vendored `draco_decoder` in node (point count + color channel), plus a manual visual check in a real browser (classification coloring + legend render, camera framing centered).
- **Load-time proof:** re-run the per-domain sweep (3 assets × 5 domains + 3 QA tasks) via `curl` transfer timing against live after deploy; point cloud must land < 2 s and, by size (4.56 MB), stay < 2 s down to ~20 Mbps.

## Out of scope / deferred

- **Medical full-brain NII (2 files, 7–10 MB `.nii.gz`):** already entropy-compressed; further reduction needs 降采样 (rejected) or a lossy volume transcode. Left as-is; borderline only on slow links. Revisit separately if needed.
- Everything else already < 2 s from measurement.

## Rollout

1. Commit encoder script + spec on the existing `snapshot/deploy-3d-perf-2026-07-31` branch.
2. Run encoder → 6 `.drc` files (~27 MB total).
3. Edit `pointcloud.html` + `architecture.json`; commit.
4. Deploy `.drc` + viewer to live (working-tree-served); verify decode, visual, and load time.
5. Remind user of the durable sudo step for the Caddy fix.
