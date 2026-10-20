# ScienceVision MCP Server — Tool Specification

> mcp-atlas style (`tool_name` + JSON-Schema `inputSchema` + structured output).
> 53 tools across 6 categories. Goal: give a headless LLM agent the same 3D-grounded
> QA reasoning surface a human has in `viewer/match3d.html` + the existing static viewers.

- **Target benchmark:** tier-1 3D-grounded QA whitelist at `/datasets/_matched/index.json` (10,551 records across `scanqa` / `sqa3d` / `m3d-refseg` / `m3d-seg-0005` / `embodiedscan-demo`).
- **Render host:** `UniPat-ScienceVision (10.0.0.144)`, EGL headless GL.
- **Reference:** mirrors the human UX of `viewer/{gltf,niivue,pointcloud,molstar,match3d}.html`.

---

## 0. Registration (`mcp_server_template.json` entry)

```json
"sciencevision": {
  "command": "uvx",
  "args": ["--from", "/Users/sue/Research/ScienceVision/sci3d_agent[mcp]", "sciencevision-mcp"],
  "env": {
    "SV_REPO_ROOT":     "/Users/sue/Research/ScienceVision",
    "SV_VIEWER_BASE":   "http://10.0.0.144",
    "SV_RENDER_CACHE":  "/tmp/sv_renders",
    "PYOPENGL_PLATFORM":"egl",
    "PYRENDER_OFFSCREEN":"1"
  },
  "category": "scientific-3d-visualization",
  "inherits_env": ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]
}
```

`nginx` on `10.0.0.144` additionally mounts `/renders/` → `/tmp/sv_renders/` so large
PNG outputs can be returned as URLs rather than base64.

---

## 1. Category F — 3D-Vision Interactive Operations (priority, 15 tools)

Mirrors the human UX (drag, zoom, slice scrub, opacity, mask toggle) with stateful
sessions. Stateless was rejected: explorative QA workloads orbit + render 5–10× per
question; shipping full state per call blows up tokens and loses GPU-side caches
(ScanNet BVH ≈ 100 MB; volume textures).

### sv3d_open_scene
**Purpose** Open scene by `qid` (auto-resolves mesh + optional mask via `_matched/index.json`) or raw `asset_path`. Returns `session_id` + scene metadata.
```json
{"type":"object","oneOf":[{"required":["qid"]},{"required":["asset_path"]}],
 "properties":{"qid":{"type":"string"},"asset_path":{"type":"string"},
  "mask_path":{"type":"string"},
  "asset_kind":{"enum":["mesh","ply","nifti","auto"]},
  "ttl_seconds":{"type":"integer","default":600}}}
```
**output** `{session_id, kind, bbox, axis_up, default_mode, masks:[{mask_id,label,voxel_count}]}`

### sv3d_close_scene
**Purpose** Free GPU/CPU memory. Mandatory in long agent rollouts.
**input** `{session_id}` **output** `{closed:true}`

### sv3d_set_render_mode
**Purpose** Switch render mode. ScanQA wants `surface`; M3D-RefSeg wants `mpr_axial`+overlay.
```json
{"required":["session_id","mode"],"properties":{
 "session_id":{"type":"string"},
 "mode":{"enum":["surface","points","volume","mpr_axial","mpr_sagittal","mpr_coronal"]},
 "point_size":{"type":"number"},
 "shading":{"enum":["flat","smooth","pbr"]}}}
```
**output** `{mode, applied:true}`

### sv3d_camera_orbit
**Purpose** Drag-orbit equivalent (azimuth/elevation deltas around current target). Primary verb for "what corner of the kitchen…".
**input** `{session_id, d_azimuth_deg, d_elevation_deg, d_roll_deg?}`
**output** `{camera:{position,target,up,fov_deg}}`

### sv3d_camera_dolly
**Purpose** Wheel-zoom equivalent.
**input** `{session_id, factor:0.05..20}` **output** `{camera}`

### sv3d_camera_pan
**Purpose** Shift-drag pan in screen-space.
**input** `{session_id, dx_frac, dy_frac}` *(viewport fraction)* **output** `{camera}`

### sv3d_camera_set_view
**Purpose** Snap to canonical view; auto-resolves Z-up vs Y-up vs LPS/RAS.
**input** `{session_id, view:"front|back|left|right|top|bottom|iso|reset", fit_bbox?}`
**output** `{camera}`

### sv3d_camera_look_at
**Purpose** Aim camera at point / bbox center / mask centroid.
```json
{"required":["session_id"],"properties":{
 "session_id":{"type":"string"},
 "target":{"oneOf":[{"type":"array","items":{"type":"number"},"minItems":3,"maxItems":3},
                    {"enum":["bbox_center","mask_centroid"]}]},
 "distance":{"type":"number"},
 "mask_id":{"type":"string"}}}
```
**output** `{camera}`

### sv3d_set_opacity
**Purpose** Per-layer alpha (mirrors viewer sliders).
**input** `{session_id, layer:"base|mask|clip_cap", alpha:0..1}` **output** `{layer, alpha}`

### sv3d_toggle_mask
**Purpose** Show/hide overlay; pick `mask_id` for multi-label M3D-RefSeg; set colormap.
**input** `{session_id, visible?, mask_id?, colormap?:"red|jet|viridis|glasbey|label", alpha?}`
**output** `{visible, mask_id, colormap}`

### sv3d_set_slice
**Purpose** MPR slice scrub + CT window/level. Directly answers "which axial slice shows the stoma".
**input** `{session_id, axis:"axial|sagittal|coronal", index?:int, fraction?:0..1, window?, level?}`
**output** `{axis, index, fraction, window, level}`

### sv3d_set_clip_plane
**Purpose** Cutaway plane for cluttered ScanNet rooms / interior CT structure.
**input** `{session_id, enabled, origin:[x,y,z], normal:[x,y,z], cap?:bool}`
**output** `{enabled, origin, normal}`

### sv3d_render
**Purpose** Snapshot current state → PNG + full camera echo.
**input** `{session_id, width?:768, height?:768, background?:"#101418", return?:"png_b64|url"}`
**output** `{image_b64|url, camera, mode, slice?, mask_state}`

### sv3d_render_canonical_views
**Purpose** One-shot 4–6 standard views — flip-eval V4 + ScanQA spatial reasoning without N round-trips.
**input** `{session_id, views?:["front","right","back","left","top","iso"], width?:512, height?:512, tile?:false}`
**output** `{images:[{view, image_b64, camera}], tiled_image_b64?}`

### sv3d_get_state
**Purpose** Read-only introspection — lets agent recover after context compaction.
**input** `{session_id}` **output** `{session_id, kind, camera, mode, mask_state, slice, clip_plane, ttl_remaining}`

### F · sharp edges to design in from day one

1. **Axis-up drift.** ScanNet PLY is Z-up; glTF Y-up; NIfTI LPS-or-RAS by affine. `open_scene` MUST return `axis_up` and `set_view` MUST normalize internally — otherwise "top" of a kitchen renders sideways.
2. **NIfTI orientation.** Pin internal to RAS; document in `get_state`. Sagittal L/R flip is the #1 silent bug.
3. **`mask_id` stability.** Use label int, not server UUID, so `extra.mask_id` from `_matched/*.json` matches directly.
4. **GPU contention.** Single-host `10.0.0.144` will OOM on concurrent volume renders. Render queue + max-concurrent-sessions; `open_scene` fail-fast.
5. **Canonical camera echo.** Always world-coord `{position, target, up, fov_deg}` (not Euler) so reasoning composes across calls.
6. **Point-size auto-scale** from bbox diagonal — otherwise ScanNet `.ply` in `points` mode is invisible dots or a blob.
7. **Mask centroid precompute** at scene open (one pass over label volume), cached.
8. **Determinism.** Fix RNG seed + disable temporal AA → identical state hashes to identical PNG hashes (benchmark harness caches).

---

## 2. Category A — Catalog / Discovery (11 tools, pure JSON, zero deps)

### sv_list_matched_datasets
**Purpose** List the 5 tier-1 whitelist datasets + counts.
**input** `{}`
**output** `{datasets:[{dataset, count, path_repo}], total}`

### sv_search_matched_qa
**Purpose** Main filterable browse over tier-1 QA records.
```json
{"properties":{
 "dataset":{"enum":["scanqa","sqa3d","m3d-refseg","m3d-seg-0005","embodiedscan-demo"]},
 "split":{"type":"string"},
 "scene_or_case_id":{"type":"string"},
 "asset_kind":{"type":"string"},
 "has_mask":{"type":"boolean"},
 "question_contains":{"type":"string"},
 "qid_prefix":{"type":"string"},
 "limit":{"type":"integer","default":50,"maximum":500},
 "offset":{"type":"integer","default":0}}}
```
**output** `{records:[{qid,dataset,split,scene_or_case_id,question,answer,asset_kind,url_viewer,has_mask}], total, offset, limit}`

### sv_get_matched_record
**input** `{qid}` (required)
**output** `{record:{...full record + joined mask:{path_repo,url_viewer,mask_id,question_type,mask_text,mask_label_count}}}`

### sv_list_scenes
**Purpose** Distinct `scene_or_case_id` values + per-scene record counts.
**input** `{dataset, limit?, offset?}` **output** `{records:[{scene_or_case_id, count}], total, offset, limit}`

### sv_list_domains
**input** `{}` **output** `{domains:[{domain, count}], total}`

### sv_list_assets
**input** `{domain, format?, viewer?, limit?, offset?}`
**output** `{records:[{name,path,size,viewer,format,overlay?}], total, offset, limit}`

### sv_get_asset
**input** `{path}` **output** `{record:{name,path,size,viewer,format,exists,overlay?,siblings?:[{kind,path}]}}`

### sv_list_vqa_datasets
**input** `{}` **output** `{datasets:[{name, count, ...meta}], total}`

### sv_get_vqa_sample
**input** `{dataset, sample}` **output** `{record:{...sample fields, url_viewer?}}`

### sv_get_astronomy_questions
**input** `{version:"V2|V4|V6"}` **output** `{version, questions:[...]}`

### sv_list_negative_samples
**input** `{domain?, kind?:"natural|synthetic", limit?, offset?}`
**output** `{records:[{name,path,domain,kind,...}], total, offset, limit}`

### A · conventions

- **Envelope** `{records:[...], total, offset, limit}` for list returns; `{record}` for single; missing → top-level `error` (no exception), so agents branch instead of retry-storm.
- `limit` default 50, max 500; `offset` default 0; `total` reflects post-filter count.

---

## 3. Category B — Viewer URL Builders (4 tools)

### sv_build_viewer_url
**Purpose** Build deep-link to correct single-asset viewer page. If `viewer` omitted, resolve from `/viewer/assets.json`; ERROR (don't guess) if not found.
```json
{"required":["asset_path"],"additionalProperties":false,"properties":{
 "asset_path":{"type":"string"},
 "viewer":{"enum":["gltf","niivue","cornerstone","molstar","pointcloud","gdsii","urdf","occt","usda","usdz","field3d","bandplot","mpbctl","saturn","photonics"]},
 "overlay":{"type":"string"},
 "type":{"enum":["mmcif","pdb","sdf","mol2"]},
 "format":{"enum":["urdf","sdf"]},
 "series":{"type":"string"}}}
```
**output** `{url, viewer, params_used}`
**Validation** (1) viewer auto-resolve; (2) reject params not consumed by chosen page (e.g. `overlay` with `viewer=gltf` → ERROR); (3) URL-encode every value.

### sv_build_match3d_url
**input** `{dataset?, qid?}` **output** `{url, viewer:"match3d", params_used, notes?}`
**Gap** Current `match3d.html` doesn't deep-link by qid. Tool returns URL + `notes` until the viewer adds `?qid=` scroll-into-view (~10 line patch).

### sv_build_dual_pane_url
**input** `{nifti_path, mask_path}` **output** `{url, viewer:"niivue-dual", params_used}`
Validates `.nii` / `.nii.gz` suffix on both.

### sv_build_vqa_url
**input** `{dataset, sample}` **output** `{url, viewer:"vqa", params_used}`

All tools use `${SV_VIEWER_BASE}` (default `http://10.0.0.144`, no trailing slash).

---

## 4. Category C — Asset Inspectors (8 tools, no render, <2KB JSON)

### sv_inspect_mesh
**Purpose** Mesh stats — pick orbit distance, decide LOD, detect missing color/UV.
**input** `{path}` **output** `{format, n_vertices, n_faces, bbox, extent, has_vertex_colors, has_uv, materials, units?, axis_up_hint:"Z|Y"}`

### sv_inspect_nifti
**Purpose** Header-level CT/MR summary — drives MPR axis + windowing without loading voxels.
**input** `{path, include_label_hist?:bool}`
**output** `{shape, spacing, affine_4x4, orientation, intensity:{min,max,mean,p1,p99}, n_nonzero_labels?, label_hist?, suggested_axial_slice_for_largest_label?}`

### sv_inspect_nifti_mask_pair  *(load-bearing for M3D-RefSeg)*
**Purpose** Per-label voxel/world bbox + `best_slices:{axial,coronal,sagittal}` + centroid → MPR rendering jumps straight to the answer slice without voxel data ever entering agent context.
**input** `{nifti_path, mask_path}`
**output** `{shape, spacing, affine_4x4, labels:[{id, voxel_count, bbox_voxel, bbox_world, centroid_voxel, centroid_world, best_slices:{axial,coronal,sagittal}}]}`

### sv_inspect_pointcloud
**input** `{path}` **output** `{n_points, bbox, fields, crs?, units?, has_rgb, has_intensity}`

### sv_inspect_molecule
**input** `{path, type:"mmcif|pdb|sdf"}`
**output** `{n_atoms, n_residues, chains, ligands, has_water, resolution?, n_models}`

### sv_inspect_urdf
**input** `{path, format:"urdf|sdf"}`
**output** `{n_links, n_joints, joint_types_hist, mesh_refs:[{link,path,exists}], root_link, dof}`

### sv_inspect_volume_npy
**input** `{path, affine_path?}` **output** `{shape, dtype, value_range, mean, affine_4x4?, spacing?}`

### sv_inspect_scannet_scene
**Purpose** Joiner: ScanNet scene → ply + bbox + linked QA records (bridges A and C).
**input** `{scene_id}` **output** `{scene_id, ply_path, bbox, extent, n_vertices, qa_refs:[{dataset, count, sample_ids}]}`

### C · caching

Key = `sha1(abs_path + ":" + mtime_ns + ":" + tool + ":" + sorted(args))`; values stored in `~/.cache/sciencevision/inspect/` (LRU). Invalidate on mtime change; never cache by path alone.

---

## 5. Category D — Static One-Shot Renderers (7 tools, stateless, cacheable)

### sv_render_mesh_thumbnail
**input** `{path, view:"iso|front|back|left|right|top|bottom", size?:512, bg?, with_axes?}`
**output** PNG envelope + `camera_used:{eye,target,up,fov_deg}`.

### sv_render_mesh_view_sheet
**Purpose** 2×3 canonical-view sheet — one call answers most ScanQA "which corner / which wall" questions.
**input** `{path, views?:["front","back","left","right","top","iso"], size_per_tile?:384}`
**output** PNG envelope (tiled, labeled) + `tiles:[{view, bbox_px}]`.

### sv_render_nifti_slice
**input** `{path, axis, index?, fraction?, window?:[lo,hi], colormap?:"gray|bone|hot"}`
**output** PNG envelope + `slice_index, voxel_spacing_mm, window_used`.

### sv_render_nifti_overlay  *(M3D-RefSeg core)*
**input** `{nifti_path, mask_path, axis, index?, fraction?, mask_id?, alpha?:0.4, colormap?:"jet|hot|viridis"}`
**output** PNG envelope + `slice_index, mask_pixel_count, mask_ids_present`.

### sv_render_nifti_mip
**input** `{path, axis, projection:"max|min|mean", window?, size?:512}` **output** PNG envelope.

### sv_render_pointcloud_thumbnail
**input** `{path, view:"iso|top|front|side", max_points?:500000, point_size?:1.5, color_by?:"rgb|intensity|height", size?:512}`
**output** PNG envelope + `points_rendered, points_total`.

### sv_render_qa_record_summary  *(most-used in QA runs)*
**Purpose** One call, agent gets eyes — looks up matched record, dispatches to right renderer.
**input** `{qid, size?:512}` **output** PNG envelope + `caption, asset_kind, renderer_used, source_paths`.

### D · conventions

- **Image returns:** ≤300 KB inline `image_png_base64`; larger → `/tmp/sv_renders/<sha>.png` (sha over inputs+mtime → idempotent) served by nginx as `image_url`.
- **Headless GL:** `PYOPENGL_PLATFORM=egl` + `PYRENDER_OFFSCREEN=1`; `pv.start_xvfb()` fallback on EGL probe fail.
- **Why separate from F:** D = pure functions, cacheable by hash, parallelizable. F owns a live scene with evolving state and cannot share that tool surface.

---

## 6. Category E — Pipeline / Evaluation Actions (8 tools)

| Tool | R/W | Notes |
|---|---|---|
| `sv_score_astronomy({question_id, predicted_answer, ground_truth_version?})` | R | Wraps `tools/score_astronomy.py` |
| `sv_classify_v({qid\|record, with_reasoning_knowledge?, model?})` | R | LLM via Mindra→Gemini; ~1–3s, ~$0.001–0.01 |
| `sv_classify_v_batch({dataset, limit?, sample?, output_jsonl?, model?})` | W (safe) | Writes timestamped JSONL under `scripts/v_classify/out/` |
| `sv_get_v_classify_pilot_summary({dataset?})` | R | Pre-computed: ScanQA→100% none / SQA3D→97% none + 3% V2 / M3D-RefSeg→100% V3. **Call before batching** to avoid wasted spend |
| `sv_validate_matched_index({datasets?})` | R | Audits `_matched/*.json` vs on-disk; reports missing/orphan |
| `sv_rebuild_matched_index({dry_run=true, datasets?, output_dir?})` | **W** | Default dry-run returns diff; explicit `false` to write |
| `sv_run_m3d_pipeline({input, config?, dry_run=true})` | **W** | Same `dry_run` policy; artifacts under timestamped subdir |
| `sv_run_v_validation({spec_path?, v_levels?})` | R | Wraps `tools/v_validation/` |

### E · conventions

- **Auth:** server inherits `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` (Mindra) and `GEMINI_API_KEY` from process env; **never** accept keys as tool args (would leak to agent transcripts).
- **Idempotency:**
  - `sv_rebuild_matched_index` — idempotent for same inputs (content-hashed); no-op when sources unchanged.
  - `sv_run_m3d_pipeline` — **not** idempotent; per-run timestamped subdir.
  - `sv_classify_v_batch` — idempotent per `output_jsonl` (fresh timestamped file by default).

---

## 7. End-to-end 3D-Grounded QA agent trajectory

### M3D-RefSeg ("identify postoperative stoma")
```
sv_list_matched_datasets
sv_search_matched_qa(dataset=m3d-refseg, has_mask=true, limit=1)
sv_inspect_nifti_mask_pair(nifti, mask)           # → best_slices.axial
sv3d_open_scene(qid=...)
sv3d_set_render_mode(mpr_axial)
sv3d_set_slice(axial, index=best_slices.axial)
sv3d_toggle_mask(visible=true, mask_id=2, alpha=0.5)
sv3d_render() → PNG
# (optional) sv3d_set_opacity(base, 0.3); sv3d_camera_orbit(...) to disambiguate
sv_score_astronomy / submit answer
sv3d_close_scene
```

### ScanQA ("In what corner of the kitchen are there two refrigerators?")
```
sv_get_matched_record(qid=scanqa-...)
sv_inspect_scannet_scene(scene_id=scene0707_00)   # bbox, vertex count
sv3d_open_scene(qid=...)
sv3d_set_render_mode(surface)
sv3d_render_canonical_views(views=[front,back,left,right,top,iso], tile=true)
# 6-view sheet usually enough; agent can also orbit
sv3d_close_scene
```

---

## 8. Tool inventory summary

| Cat | Count | Render? | Stateful? | Dependencies |
|---|---|---|---|---|
| A · Catalog | 11 | No | No | Filesystem read |
| B · URL builders | 4 | No | No | A (assets.json reader) |
| C · Inspectors | 8 | No | No | trimesh/nibabel/laspy/gemmi/yourdfpy |
| D · Static render | 7 | Yes | No | C + pyrender/pyvista/EGL |
| E · Pipeline/eval | 8 | No | No | Existing CLIs + Mindra/Gemini |
| **F · 3D-Vision** | **15** | **Yes** | **Yes** | C + D primitives + session manager + GPU queue |
| **Total** | **53** | | | |
