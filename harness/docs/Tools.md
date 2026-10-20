# ScienceVision Tools

Two parallel surfaces for working with the ScienceVision dataset:

| Surface | What | Who calls it | Count |
|---|---|---|---|
| **A. 3D vision tools** | Static HTML viewers under `/viewer/*.html`, opened by the browser with URL params | Humans, headless screenshot drivers | **19** |
| **B. Format tools (MCP)** | Programmatic tools served by `sciencevision-mcp` over MCP | Agents (Claude / Codex), CLI scripts | **56** |

Surface A is what the **3D-Grounded QA viewer** uses for previews. Surface B is the read/inspect/render/segment fabric the **agent runner** uses to answer questions about 3D scenes without loading them into the LLM context.

---

## A. 3D vision tools (19)

URL-parameterized static pages. Each accepts the asset path via `?path=…` or `?id=…` (see source for exact schema). They render in the browser, so the agent reaches them by **building a URL with `sv_build_viewer_url`** (see B.URL builders below), then opening or screenshotting it.

| Viewer | Domain | Opens | Underlying lib |
|---|---|---|---|
| `gltf.html` | generic | `.glb` / `.gltf` 3D models | Three.js (+ Draco/Meshopt/KTX2 decoders, v1.4) |
| `molstar.html` | biology | proteins, PDB, ligands | Mol* |
| `niivue.html` | medical | NIfTI / NRRD volumes | NiiVue |
| `cornerstone.html` | medical | DICOM stack | Cornerstone3D |
| `occt.html` | industrial | STEP / IGES / BREP | OpenCascade.js (OCCT) |
| `cad-edrawings.html` | industrial | SOLIDWORKS native (SLDPRT/SLDASM) | eDrawings web |
| `cad-native.html` | industrial | CAD multi-format browser | dispatcher |
| `urdf.html` | robotics | URDF / SDF robots | urdf-loaders |
| `pointcloud.html` | point cloud | LAS / LiDAR | potree-like |
| `field3d.html` | photonics | volumetric scalar fields | Three.js volume shader |
| `bandplot.html` | bandplot | electronic band structures | Plotly |
| `gdsii.html` | photonics | GDSII layouts | klayout-web |
| `mpbctl.html` | photonics | MPB control files | text-formatter |
| `photonics.html` | photonics | Ansys Lumerical / Zemax | format-aware viewer |
| `saturn.html` | astronomy | planetary / SED data | Plotly + image stack |
| `seed3d_pair.html` | synthetic | Seed3D before/after pairs | Three.js dual-pane |
| `usda.html` | industrial | USD ASCII | text + Three.js |
| `usdz.html` | industrial | USD compressed | three.js USDZLoader |
| `video.html` | generic | MP4 / WebM video assets | HTML5 video |

---

## B. Format tools — MCP server (56)

Entry: `sciencevision-mcp` (CLI, from `pyproject.toml`). Implementation: `sci3d_agent/mcp/`.
Spec: [docs/mcp/MCP_SERVER_SPEC.md](mcp/MCP_SERVER_SPEC.md). Rollout: [docs/mcp/MCP_ROLLOUT_PLAN.md](mcp/MCP_ROLLOUT_PLAN.md).

All handlers return JSON; errors flow back as `{"error": "..."}` envelopes (never raised exceptions). State is held in immutable `Session` dataclasses (`vision3d/_types.py`); cross-category calls go through `REGISTRY[name].handler(args)`.

### B.1 Catalog (11) — list / get / search

Read-only browse over the on-disk dataset (3D assets, VQA datasets, matched QA whitelist).

| Tool | Purpose |
|---|---|
| `sv_list_domains` | List all asset domains from `viewer/assets.json` with counts. |
| `sv_list_assets` | List assets within a single domain, optionally filtered by format and viewer. |
| `sv_get_asset` | Fetch a single asset record by path; reports on-disk existence and overlay siblings. |
| `sv_list_matched_datasets` | List the 5 tier-1 matched 3D-grounded QA whitelist datasets + per-dataset counts. |
| `sv_search_matched_qa` | Free-text + filter search over matched QA records. |
| `sv_get_matched_record` | Fetch a full matched QA record by qid, searching all 5 datasets; joins mask metadata when present. |
| `sv_list_scenes` | List distinct `scene_or_case_id` values + per-scene record counts for a tier-1 dataset. |
| `sv_list_vqa_datasets` | List all VQA datasets registered in `viewer/datasets_assets.json`. |
| `sv_get_vqa_sample` | Fetch a single VQA sample (image + Q + A) by dataset+sample_id. |
| `sv_get_astronomy_questions` | List astronomy questions tied to a planetary/SED asset. |
| `sv_list_negative_samples` | List V-classify negative samples (no-3D-grounding ScanQA / SQA3D items). |

### B.2 Inspect (8) — read headers without payload

Eight read-only inspectors used by Static Render (B.3) and Vision3D Session (B.7) to make rendering decisions **without loading voxel/point payloads** into the agent context. Every handler returns a JSON-serializable dict <2 KB.

| Tool | Purpose |
|---|---|
| `sv_inspect_mesh` | Mesh stats (vertices, faces, bbox, axis-up hint). No render. |
| `sv_inspect_nifti` | NIfTI header summary + intensity stats; optional label histogram. |
| `sv_inspect_nifti_mask_pair` | Per-label voxel/world bbox + centroid + best_slices. M3D-RefSeg load-bearing. |
| `sv_inspect_pointcloud` | LAS/LAZ point cloud stats (point count, bbox, fields, rgb/intensity). |
| `sv_inspect_molecule` | Molecule structure summary (atoms, residues, chains, ligands, water). |
| `sv_inspect_urdf` | URDF/SDF robot description summary (links, joints, mesh refs, dof). |
| `sv_inspect_volume_npy` | NumPy .npy volume summary (shape, dtype, value range, optional affine). |
| `sv_inspect_scannet_scene` | Joiner: ScanNet scene_id → ply stats + linked tier-1 QA records. |

Axis convention (load-bearing for M3D-RefSeg): `sagittal=0, coronal=1, axial=2` in numpy array order.

### B.3 Render — static (7) — one-shot PNG

Static renders that bypass the Vision3D session. Each returns a PNG path + dimensions; ideal for QA summary thumbnails.

| Tool | Purpose |
|---|---|
| `sv_render_mesh_thumbnail` | Single-view mesh thumbnail (PNG) via pyrender + EGL. |
| `sv_render_mesh_view_sheet` | 2×3 canonical-view sheet for a mesh; one call answers most ScanQA spatial questions. |
| `sv_render_nifti_slice` | Single NIfTI slice as PNG (axis = axial/coronal/sagittal; index or fraction). |
| `sv_render_nifti_overlay` | NIfTI slice + per-label mask overlay (M3D-RefSeg core). |
| `sv_render_nifti_mip` | NIfTI maximum/minimum/mean intensity projection along an axis. |
| `sv_render_pointcloud_thumbnail` | LAS/LAZ point-cloud thumbnail (top/front/side/iso) colored by height/intensity/rgb. |
| `sv_render_qa_record_summary` | Dispatch by `asset_kind`: look up qid then render the right kind of summary PNG. |

### B.4 URL builders (4) — link into the viewer

Generate parameterized URLs into Surface A. Pure functions, no I/O.

| Tool | Purpose |
|---|---|
| `sv_build_viewer_url` | Build a `/viewer/<kind>.html?…` URL for an arbitrary asset path. |
| `sv_build_match3d_url` | Build a `/viewer/match3d.html?ds=…&qid=…` URL for a 3D-grounded QA record. |
| `sv_build_dual_pane_url` | Build a side-by-side dual-pane viewer URL (e.g. NIfTI + mask overlay). |
| `sv_build_vqa_url` | Build a VQA viewer URL for the given dataset+sample pair. |

### B.5 Pipeline · read (5) — query-side runners

Pure-read pipeline runners and validators. They write to caches/reports but never mutate the source manifests.

| Tool | Purpose |
|---|---|
| `sv_classify_v` | V-classify a single QA item (none / V1 / V2 / V3 visual-grounding tier). |
| `sv_score_astronomy` | Score astronomy questions for grounding quality. |
| `sv_run_v_validation` | Run V-validation harness over a slice of matched whitelist. |
| `sv_get_v_classify_pilot_summary` | Fetch the pilot-300 V-classify summary results. |
| `sv_validate_matched_index` | Sanity-check `datasets/_matched/index.json` (counts, schemas, on-disk existence). |

### B.6 Pipeline · write (3) — mutating pipeline runs

Mutating runs. **Each write is gated by a `--confirm` arg** at the MCP boundary.

| Tool | Purpose |
|---|---|
| `sv_classify_v_batch` | Bulk V-classify and persist to per-dataset cache. |
| `sv_rebuild_matched_index` | Rebuild `datasets/_matched/index.json` + per-dataset shards from the catalog. |
| `sv_run_m3d_pipeline` | Run the M3D-RefSeg verification pipeline end-to-end. |

### B.7 Vision3D · session (3) — agent-facing 3D session

Open a session against one asset; subsequent camera/state/snapshot calls act on it. Sessions are immutable; every mutator returns a new `Session` via `replace_*` helpers.

| Tool | Purpose |
|---|---|
| `sv3d_open_scene` | Open a session for one asset (mesh / NIfTI / pointcloud); returns session_id. |
| `sv3d_close_scene` | Close a session and free associated state. |
| `sv3d_get_state` | Return a JSON snapshot of session state. |

### B.8 Vision3D · camera (5) — pose controls

| Tool | Purpose |
|---|---|
| `sv3d_camera_orbit` | Orbit camera around target (yaw/pitch in degrees). |
| `sv3d_camera_dolly` | Move camera toward/away from target (signed distance). |
| `sv3d_camera_pan` | Pan camera + target sideways (screen-space dx/dy). |
| `sv3d_camera_set_view` | Set named canonical view (`top` / `front` / `side` / `iso`). |
| `sv3d_camera_look_at` | Re-aim camera at a world-space point. |

### B.9 Vision3D · state (5) — visual mode toggles

| Tool | Purpose |
|---|---|
| `sv3d_set_render_mode` | Switch render mode (mip / iso / surface / volumetric). |
| `sv3d_set_opacity` | Set overall or per-label opacity. |
| `sv3d_toggle_mask` | Show/hide a per-label mask. |
| `sv3d_set_slice` | Set slice index along an axis. |
| `sv3d_set_clip_plane` | Set/clear an arbitrary clip plane. |

### B.10 Vision3D · snapshot (2) — render the current session

| Tool | Purpose |
|---|---|
| `sv3d_render` | Render current session state to a PNG; returns path + dims. |
| `sv3d_render_canonical_views` | Render the 4 canonical views (top/front/side/iso) in one call. |

### B.11 Vision3D · segment (3) — SAM-backed segmentation

Calls an HTTP segmentation service at `SAM_SERVICE_URL` (default `http://localhost:8900`). For local dev: `FAKE_SAM_QUALITY=noisy python -m scripts.sam_service.fake_server`.

| Tool | Purpose |
|---|---|
| `sv3d_segment_2d` | Run SAM on a 2D slice; persist mask + return label_id. |
| `sv3d_segment_3d_volume` | Per-slice SAM, then 3D-CC merge, on a volume. |
| `sv3d_segment_3d_mesh` | Project mesh to canonical views, SAM each, then back-project to mesh labels. |

---

## Where this lives in code

| Surface | Path | Files ≤ |
|---|---|---|
| Frontend viewers | `viewer/*.html` + `viewer/shared/` | per-page |
| MCP catalog | `sci3d_agent/mcp/tools/catalog.py` | 450 |
| MCP inspect | `sci3d_agent/mcp/tools/inspect.py` | 450 |
| MCP pipeline (R/W) | `sci3d_agent/mcp/tools/pipeline_{read,write}.py` | 450 |
| MCP render-static | `sci3d_agent/mcp/tools/render_static.py` | 450 |
| MCP urls | `sci3d_agent/mcp/tools/urls.py` | 450 |
| MCP vision3d | `sci3d_agent/mcp/tools/vision3d/{session,camera,state,snapshot,segment}.py` | 450 |
| Registry / dispatch | `sci3d_agent/mcp/registry.py` | 450 |

Helpers extract to private `_*` modules rather than growing one file (see `vision3d/_segment_client.py`, `_snapshot_render.py`, etc).
