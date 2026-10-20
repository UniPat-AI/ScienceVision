# End2End Baseline Matrix + Multiview View-Count Ablation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `claude-code-2d` into a 1/3/6/12-view ablation family and add a single driver that runs all 6 baselines (`cc`, `ccmcp`, `cc2d-{1,3,6,12}`) over a `{viewer_link, question, ground_truth}` task list, reusing the existing collect+plot artifacts.

**Architecture:** Extend the Category-D static renderers (`mesh.py`, `pointcloud.py`) with an arbitrary azimuth/elevation orbit pose; parameterize `scripts/harbor/_multiview.py::plan_views` with `n_views` + per-modality view sequences; add a viewer-link resolver that produces standard QA-Gen records; add a thin orchestrator (`run_end2end.py`) that fans out to the *unchanged* `qa_gen_to_tasks` → `harbor run` → `collect_qagen_results` → `plot_leaderboard` pipeline, running the 6 harbor jobs concurrently.

**Tech Stack:** Python 3.12, pyrender+EGL / trimesh (mesh), laspy+matplotlib (point cloud), nibabel (NIfTI), pytest, harbor CLI, docker.

## Global Constraints

- **Files ≤450 lines** in `sci3d_agent/mcp/` (`vision3d/` sublimit), **≤800 lines elsewhere** (`scripts/`). Extract helpers to private `_*` modules rather than growing a file.
- **Error envelope, not exceptions:** renderer/tool handlers return `{"error": "..."}`, never raise across the boundary.
- **Immutable session state** convention does not apply here (Category-D static renderers are stateless); do not introduce mutable global state.
- **NIfTI axis convention:** `sagittal / coronal / axial` names only; slice varied via `index` (int) or `fraction` (0..1), mutually exclusive.
- **`SCI3D_JUDGE_MODEL=claude-haiku-4-5`** (undated id) is REQUIRED on every `harbor run` — the dated default 503s on the ALB gateway and silently collapses reward to ~0.100.
- **Renderer GL fallback:** on GL-less machines renderers return a `renderer_skipped`/`missing_dep` envelope; tests must not hard-require GL — test the pure math (camera vectors, rotation, plan lists), not pixels.
- **The 2D baseline must never see the raw 3D file:** only `environment/views/*.png` is staged; the instruction emits no `asset_path`.
- **The 4 `cc2d-N` variants share one image** (`sci3d-claude-code-2d:latest`) and one agent-env (`claude-code-2d`); they differ only by the number of staged PNGs (`task["n_views"]`) and the collect-time harness label.

---

### Task 1: Mesh renderer — arbitrary azimuth/elevation orbit pose

**Files:**
- Modify: `sci3d_agent/mcp/tools/_render/mesh.py:37-59` (`camera_for_view`), `:191-220` (`handle_mesh_thumbnail`)
- Modify: `sci3d_agent/mcp/schemas/sv_render_mesh_thumbnail.json`
- Test: `tests/mcp/test_mesh_orbit_camera.py` (new)

**Interfaces:**
- Produces: `camera_for_view(view, center, diag, axis_up, *, azimuth=None, elevation=None)` — when `azimuth` (degrees) is given, the view direction is computed from `(azimuth, elevation)` instead of the named table; returns the same `(eye, target, up, fov_deg)` tuple. `handle_mesh_thumbnail` accepts optional `azimuth`/`elevation` args (degrees, floats) and, when `azimuth` is present, bypasses the `view` enum check.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_mesh_orbit_camera.py
import numpy as np
from sci3d_agent.mcp.tools._render.mesh import camera_for_view

CENTER = np.zeros(3)
DIAG = 2.0

def test_azimuth0_elev20_points_from_front_ish_zup():
    eye, target, up, fov = camera_for_view("iso", CENTER, DIAG, "Z", azimuth=0.0, elevation=20.0)
    # direction = eye-center normalized; az=0 => +X-ish in the XY plane, elevated +Z
    d = (eye - CENTER); d = d / np.linalg.norm(d)
    assert d[0] > 0.9 and d[2] > 0.3 and abs(d[1]) < 1e-6
    assert np.allclose(target, CENTER)
    assert fov == 45.0

def test_azimuth_changes_eye():
    e0, *_ = camera_for_view("iso", CENTER, DIAG, "Z", azimuth=0.0, elevation=20.0)
    e90, *_ = camera_for_view("iso", CENTER, DIAG, "Z", azimuth=90.0, elevation=20.0)
    assert not np.allclose(e0, e90)

def test_named_view_unchanged_when_no_azimuth():
    eye, target, up, fov = camera_for_view("front", CENTER, DIAG, "Z")
    d = (eye - CENTER); d = d / np.linalg.norm(d)
    assert np.allclose(d, [0.0, -1.0, 0.0], atol=1e-6)  # front dir preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_mesh_orbit_camera.py -v`
Expected: FAIL — `camera_for_view() got an unexpected keyword argument 'azimuth'`.

- [ ] **Step 3: Implement the azimuth/elevation path in `camera_for_view`**

Add keyword-only params and an orbit-direction branch at the top of the function body (before the named-table lookup):

```python
def camera_for_view(
    view: str,
    center: np.ndarray,
    diag: float,
    axis_up: str,
    *,
    azimuth: float | None = None,
    elevation: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    up = np.array([0.0, 1.0, 0.0]) if axis_up.upper() == "Y" else np.array([0.0, 0.0, 1.0])
    if azimuth is not None:
        az = math.radians(azimuth)
        el = math.radians(elevation if elevation is not None else 20.0)
        if axis_up.upper() == "Y":
            # up = +Y: orbit in XZ plane, elevate toward +Y
            direction = np.array([math.cos(el) * math.sin(az),
                                  math.sin(el),
                                  math.cos(el) * math.cos(az)])
        else:
            # up = +Z: orbit in XY plane, elevate toward +Z
            direction = np.array([math.cos(el) * math.cos(az),
                                  math.cos(el) * math.sin(az),
                                  math.sin(el)])
    else:
        dirs = VIEW_DIRECTIONS_Y_UP if axis_up.upper() == "Y" else VIEW_DIRECTIONS_Z_UP
        direction = np.array(dirs.get(view, dirs["iso"]), dtype=float)
    norm = np.linalg.norm(direction)
    if norm == 0:
        direction = np.array([1.0, 1.0, 1.0]); norm = math.sqrt(3.0)
    direction = direction / norm
    distance = max(diag * 1.4, 1e-3)
    eye = center + direction * distance
    if abs(np.dot(direction, up)) > 0.95:
        up = np.array([0.0, 1.0, 0.0]) if axis_up.upper() != "Y" else np.array([0.0, 0.0, 1.0])
    return eye.astype(float), center.astype(float), up.astype(float), 45.0
```

Then thread the params through `render_mesh_image` (add `azimuth`/`elevation` params, default `None`, pass to `camera_for_view` at `mesh.py:109`) and `handle_mesh_thumbnail`:

```python
def handle_mesh_thumbnail(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    azimuth = args.get("azimuth")
    elevation = args.get("elevation")
    view = args.get("view")
    if azimuth is None and view not in VIEW_DIRECTIONS_Z_UP:
        return {"error": f"view must be one of: {sorted(VIEW_DIRECTIONS_Z_UP)}"}
    size = int(args.get("size") or 512)
    bg = args.get("bg")
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}
    cache_args = {"view": view, "azimuth": azimuth, "elevation": elevation, "size": size, "bg": bg}
    sha = C.render_cache_key("sv_render_mesh_thumbnail", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached
    result = render_mesh_image(path, view or "iso", size, bg,
                               azimuth=azimuth, elevation=elevation)
    if "error" in result:
        return result
    png = C.rgba_to_png(result["rgba"])
    if png is None:
        return C.missing_dep("Pillow")
    return C.build_image_envelope(
        png, cache_sha=sha,
        extras={"camera_used": result["camera_used"],
                "view": view, "azimuth": azimuth, "elevation": elevation},
    )
```

(In `render_mesh_image`, change line 109 to `eye, target, up, fov_deg = camera_for_view(view, center, diag, axis_up, azimuth=azimuth, elevation=elevation)` and add the two keyword params to its signature.)

- [ ] **Step 4: Relax the schema**

`sv_render_mesh_thumbnail.json` — make `view` optional and add the orbit params:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["path"],
  "properties": {
    "path": {"type": "string"},
    "view": {"enum": ["iso", "front", "back", "left", "right", "top", "bottom"]},
    "azimuth": {"type": "number"},
    "elevation": {"type": "number"},
    "size": {"type": "integer", "minimum": 64, "maximum": 4096, "default": 512},
    "bg": {"type": "string"},
    "with_axes": {"type": "boolean", "default": false}
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/mcp/test_mesh_orbit_camera.py -v`
Expected: PASS (pure-math test, no GL needed).

- [ ] **Step 6: Commit**

```bash
git add sci3d_agent/mcp/tools/_render/mesh.py sci3d_agent/mcp/schemas/sv_render_mesh_thumbnail.json tests/mcp/test_mesh_orbit_camera.py
git commit -m "feat(render): mesh thumbnail arbitrary azimuth/elevation orbit pose"
```

---

### Task 2: Point cloud renderer — orbit rotation before projection

**Files:**
- Modify: `sci3d_agent/mcp/tools/_render/pointcloud.py:22-30` (add `orbit_rotate`), `:100-104` (projection branch)
- Modify: `sci3d_agent/mcp/schemas/sv_render_pointcloud_thumbnail.json`
- Test: `tests/mcp/test_pointcloud_orbit.py` (new)

**Interfaces:**
- Produces: `orbit_rotate(xyz, azimuth, elevation)` — rotates points about the +Z (up) axis by `azimuth` degrees then tilts by `elevation`, returning rotated `xyz` (N×3). `handle_pointcloud_thumbnail` accepts optional `azimuth`/`elevation`; when `azimuth` is present it rotates then applies the `iso`-style top-down 2-axis projection.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_pointcloud_orbit.py
import numpy as np
from sci3d_agent.mcp.tools._render.pointcloud import orbit_rotate

def test_azimuth90_rotates_x_to_y():
    pts = np.array([[1.0, 0.0, 0.0]])
    out = orbit_rotate(pts, azimuth=90.0, elevation=0.0)
    assert np.allclose(out[0], [0.0, 1.0, 0.0], atol=1e-6)

def test_azimuth0_elevation0_is_identity():
    pts = np.array([[1.0, 2.0, 3.0]])
    assert np.allclose(orbit_rotate(pts, 0.0, 0.0), pts, atol=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_pointcloud_orbit.py -v`
Expected: FAIL — `cannot import name 'orbit_rotate'`.

- [ ] **Step 3: Implement `orbit_rotate` + wire into the handler**

```python
def orbit_rotate(xyz: np.ndarray, azimuth: float, elevation: float) -> np.ndarray:
    az = math.radians(azimuth)
    el = math.radians(elevation)
    ca, sa = math.cos(az), math.sin(az)
    rz = np.array([[ca, -sa, 0.0], [sa, ca, 0.0], [0.0, 0.0, 1.0]])
    ce, se = math.cos(el), math.sin(el)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, ce, -se], [0.0, se, ce]])
    return xyz @ rz.T @ rx.T
```

In `handle_pointcloud_thumbnail`, read `azimuth = args.get("azimuth")`, `elevation = args.get("elevation")`, add them to `cache_args`, relax the `view` check to allow `view is None` when `azimuth is not None`, and replace the projection branch (`pointcloud.py:100-104`):

```python
if azimuth is not None:
    rotated = orbit_rotate(xyz, float(azimuth), float(elevation if elevation is not None else 20.0))
    pts2d = rotated[:, [0, 2]]  # look along +Y after orbit
elif view == "iso":
    pts2d = project_iso(xyz)
else:
    ix, iy = POINTCLOUD_PROJECTIONS[view]
    pts2d = xyz[:, [ix, iy]]
```

- [ ] **Step 4: Relax the schema** (`sv_render_pointcloud_thumbnail.json`): make `view` optional, add `azimuth`/`elevation` numbers, keep `required: ["path"]`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/mcp/test_pointcloud_orbit.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add sci3d_agent/mcp/tools/_render/pointcloud.py sci3d_agent/mcp/schemas/sv_render_pointcloud_thumbnail.json tests/mcp/test_pointcloud_orbit.py
git commit -m "feat(render): point cloud orbit azimuth/elevation views"
```

---

### Task 3: Parameterize `plan_views` with `n_views` + per-modality sequences

**Files:**
- Modify: `scripts/harbor/_multiview.py` (whole `plan_views` + new helpers)
- Modify: `tests/harbor_adapters/test_multiview_plan.py` (existing assertions encode the OLD fixed-count behavior — update them)
- Test: add cases to `tests/harbor_adapters/test_multiview_plan.py`

**Interfaces:**
- Consumes: `sv_render_mesh_thumbnail` / `sv_render_pointcloud_thumbnail` now accept `azimuth`/`elevation` (Tasks 1-2).
- Produces: `plan_views(task, *, size=512, best_slices=None, n_views=6, seed_pose=None)` → ordered `(tool_name, args)` list of length `n_views` for mesh/pointcloud/nifti, or `None` for uncovered modalities. `seed_pose` is an optional `{"azimuth": float, "elevation": float}`; when present (mesh only) the mesh orbit starts at that azimuth/elevation. Also `mesh_orbit_azimuths(n)` / `nifti_slice_plan(n)` helpers.

- [ ] **Step 1: Write the failing tests** (append to `test_multiview_plan.py`)

```python
import pytest
from scripts.harbor._multiview import plan_views, mesh_orbit_azimuths

@pytest.mark.parametrize("n", [1, 3, 6, 12])
def test_mesh_n_views_are_orbit_thumbnails(n):
    plan = plan_views({"asset_format": "obj", "asset_path": "/h/m.obj"}, n_views=n)
    assert len(plan) == n
    assert all(t == "sv_render_mesh_thumbnail" for t, _ in plan)
    assert [a["azimuth"] for _, a in plan] == mesh_orbit_azimuths(n)
    assert all(a["elevation"] == 20.0 for _, a in plan)

def test_mesh_orbit_azimuths_evenly_spaced():
    assert mesh_orbit_azimuths(1) == [0.0]
    assert mesh_orbit_azimuths(3) == [0.0, 120.0, 240.0]
    assert mesh_orbit_azimuths(6) == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
    assert mesh_orbit_azimuths(12) == [i * 30.0 for i in range(12)]

def test_mesh_seed_pose_shifts_start():
    plan = plan_views({"asset_format": "obj", "asset_path": "/h/m.obj"},
                      n_views=3, seed_pose={"azimuth": 45.0, "elevation": 10.0})
    az = [a["azimuth"] for _, a in plan]
    assert az == [45.0, 165.0, 285.0]
    assert plan[0][1]["elevation"] == 10.0

@pytest.mark.parametrize("n,count", [(1, 4), (3, 4), (6, 4), (12, 4)])
def test_pointcloud_n_views(n, count):
    plan = plan_views({"asset_format": "las", "asset_path": "/h/c.las"}, n_views=n)
    assert len(plan) == n
    assert all(t == "sv_render_pointcloud_thumbnail" for t, _ in plan)

def test_nifti_slice_counts():
    p1 = plan_views({"asset_format": "nifti", "asset_path": "/h/a.nii"}, n_views=1)
    assert [a["axis"] for _, a in p1] == ["axial"]
    p6 = plan_views({"asset_format": "nifti", "asset_path": "/h/a.nii"}, n_views=6)
    assert len(p6) == 6 and sorted({a["axis"] for _, a in p6}) == ["axial", "coronal", "sagittal"]
    p12 = plan_views({"asset_format": "nifti", "asset_path": "/h/a.nii"}, n_views=12)
    assert len(p12) == 12
```

Also UPDATE the existing OLD-behavior tests: delete/replace `test_pointcloud_four_views` and `test_mesh_single_view_sheet` (they assert the pre-ablation fixed plan). Replace `test_mesh_single_view_sheet` with the parametrized mesh test above; keep the NIfTI mask/best-slice tests but re-assert against the new default `n_views=6` layout (3 axes × {0.35,0.65}).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/harbor_adapters/test_multiview_plan.py -v`
Expected: FAIL — `cannot import name 'mesh_orbit_azimuths'` / count mismatches.

- [ ] **Step 3: Rewrite `plan_views`**

```python
_MESH_FORMATS = {"obj", "ply", "stl", "glb", "gltf", "off"}
_POINTCLOUD_FORMATS = {"las", "laz"}
_NIFTI_AXES = ("sagittal", "coronal", "axial")
_ELEV = 20.0

def mesh_orbit_azimuths(n: int) -> list[float]:
    return [round(i * 360.0 / n, 6) for i in range(max(1, n))]

def _nifti_fractions(n: int) -> list[tuple[str, float]]:
    # (axis, fraction) pairs; nested-ish tiers.
    if n == 1:
        return [("axial", 0.5)]
    if n == 3:
        return [(a, 0.5) for a in _NIFTI_AXES]
    if n == 6:
        return [(a, f) for a in _NIFTI_AXES for f in (0.35, 0.65)]
    # n == 12 (and any other): 3 axes x 4 fractions
    fracs = (0.2, 0.4, 0.6, 0.8)
    return [(a, f) for a in _NIFTI_AXES for f in fracs][:n]

def plan_views(task, *, size=512, best_slices=None, n_views=6, seed_pose=None):
    fmt = (task.get("asset_format") or "").lower()
    domain = (task.get("domain") or "").lower()
    asset_path = task.get("asset_path") or ""
    if not asset_path:
        return None
    if fmt == "nifti":
        return _nifti_views(task, size, best_slices, n_views)
    if fmt in _POINTCLOUD_FORMATS:
        base_az = float((seed_pose or {}).get("azimuth", 0.0))
        base_el = float((seed_pose or {}).get("elevation", _ELEV))
        azs = mesh_orbit_azimuths(n_views)
        return [("sv_render_pointcloud_thumbnail",
                 {"path": asset_path, "azimuth": round(base_az + a, 6),
                  "elevation": base_el, "size": size})
                for a in azs]
    if fmt in _MESH_FORMATS and domain != "physics":
        base_az = float((seed_pose or {}).get("azimuth", 0.0))
        base_el = float((seed_pose or {}).get("elevation", _ELEV))
        azs = mesh_orbit_azimuths(n_views)
        return [("sv_render_mesh_thumbnail",
                 {"path": asset_path, "azimuth": round(base_az + a, 6),
                  "elevation": base_el, "size": size})
                for a in azs]
    return None

def _nifti_views(task, size, best_slices, n_views):
    asset_path = task["asset_path"]
    mask_path = task.get("mask_path")
    calls = []
    for axis, frac in _nifti_fractions(n_views):
        if mask_path:
            args = {"nifti_path": asset_path, "mask_path": mask_path, "axis": axis, "size": size}
            tool = "sv_render_nifti_overlay"
        else:
            args = {"path": asset_path, "axis": axis, "size": size}
            tool = "sv_render_nifti_slice"
        if best_slices and axis in best_slices and frac == 0.5:
            args["index"] = int(best_slices[axis])
        else:
            args["fraction"] = frac
        calls.append((tool, args))
    return calls
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/harbor_adapters/test_multiview_plan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/harbor/_multiview.py tests/harbor_adapters/test_multiview_plan.py
git commit -m "feat(harbor): parameterize plan_views with n_views orbit sequences + seed_pose"
```

---

### Task 4: Thread `n_views` / `seed_pose` through staging + `qa_gen_to_tasks`

**Files:**
- Modify: `scripts/harbor/build_harbor_tasks.py:284-336` (`_stage_rendered_views`)
- Modify: `scripts/harbor/qa_gen_to_tasks.py` (`qa_record_to_task`, `main` argparse)
- Test: `tests/harbor_adapters/test_stage_rendered_views.py` (extend), `tests/harbor_adapters/test_claude_code_2d_tasks.py` (extend)

**Interfaces:**
- Consumes: `plan_views(..., n_views, seed_pose)` (Task 3).
- Produces: `_stage_rendered_views(task, env_dir)` reads `task.get("n_views", 6)` and `task.get("seed_pose")` and passes them to `plan_views`, staging exactly `n_views` PNGs (subject to renderer availability). `qa_record_to_task(rec, *, asset_root=None)` copies `rec.get("n_views")`/`rec.get("seed_pose")` when present; `qa_gen_to_tasks` gains `--n-views {1,3,6,12}` (default 6) that stamps `task["n_views"]`.

- [ ] **Step 1: Write the failing test** (extend `test_stage_rendered_views.py`)

```python
def test_stage_respects_n_views(monkeypatch, tmp_path):
    # Fake the renderer lookup so no GL is needed: each call returns a 1x1 PNG envelope.
    import scripts.harbor.build_harbor_tasks as B
    PNG = b"\x89PNG\r\n\x1a\n"  # header is enough for _extract_png_bytes via base64 path
    import base64
    monkeypatch.setattr(B, "_lookup_render_handler",
                        lambda name: (lambda args: {"image_png_base64": base64.b64encode(PNG).decode()}))
    task = {"task_id": "t1", "asset_format": "obj", "asset_path": "/h/m.obj", "n_views": 3}
    staged = B._stage_rendered_views(task, tmp_path)
    assert len(staged["view_container_paths"]) == 3
    assert sorted(p.name for p in (tmp_path / "views").glob("*.png")) == \
        ["view_00.png", "view_01.png", "view_02.png"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/harbor_adapters/test_stage_rendered_views.py::test_stage_respects_n_views -v`
Expected: FAIL — stages the default 6, not 3.

- [ ] **Step 3: Implement**

In `_stage_rendered_views` (`build_harbor_tasks.py:294-296`) change the plan call:

```python
    fmt = (task.get("asset_format") or "").lower()
    best = _resolve_best_slices(task)
    plan = plan_views(
        task,
        best_slices=best,
        n_views=int(task.get("n_views", 6)),
        seed_pose=task.get("seed_pose"),
    )
```

In `qa_gen_to_tasks.py::qa_record_to_task`, after building `task`, carry the two optional fields:

```python
    if rec.get("n_views") is not None:
        task["n_views"] = int(rec["n_views"])
    if rec.get("seed_pose") is not None:
        task["seed_pose"] = rec["seed_pose"]
```

In `qa_gen_to_tasks.py::main`, add the argument and stamp it (overrides the record when passed):

```python
    ap.add_argument("--n-views", type=int, choices=[1, 3, 6, 12], default=None,
                    help="for --agent-env claude-code-2d: number of rendered "
                         "views to stage (overrides per-record n_views)")
    ...
    for rec in records:
        task = qa_record_to_task(rec, asset_root=asset_root)
        if args.n_views is not None:
            task["n_views"] = args.n_views
        built.append(build_one(task, args.out, containerized=args.containerized, agent_env=args.agent_env))
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/harbor_adapters/test_stage_rendered_views.py tests/harbor_adapters/test_claude_code_2d_tasks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/harbor/build_harbor_tasks.py scripts/harbor/qa_gen_to_tasks.py tests/harbor_adapters/test_stage_rendered_views.py tests/harbor_adapters/test_claude_code_2d_tasks.py
git commit -m "feat(harbor): thread n_views/seed_pose into 2D view staging + qa_gen_to_tasks --n-views"
```

---

### Task 5: Viewer-link resolver (`_viewer_link.py`)

**Files:**
- Create: `scripts/harbor/_viewer_link.py`
- Test: `tests/harbor_adapters/test_viewer_link.py` (new)

**Interfaces:**
- Produces:
  - `decode_cam(s: str) -> dict | None` — inverse of `scripts/eval/sv3d_emit_viewer_urls.py::encode_cam`; returns `{"position", "target", "up"?, "fov_deg"?}` or `None`.
  - `parse_viewer_link(url: str) -> dict` — returns `{"viewer_type": str, "file": str, "cam": dict|None, "slice": dict|None}`. `viewer_type` is the html page stem (e.g. `gltf`, `niivue`, `pointcloud`). `file` is the raw `file` query param. `cam`/`slice` populated only for `gltf`.
  - `seed_pose_from_cam(cam: dict) -> dict | None` — `{"azimuth", "elevation"}` (degrees) from the eye→center direction `normalize(position - target)`, using the Z-up convention of `mesh_orbit_azimuths`; `None` if `cam` is falsy.

- [ ] **Step 1: Write the failing test**

```python
# tests/harbor_adapters/test_viewer_link.py
import base64, json, math
from scripts.harbor._viewer_link import decode_cam, parse_viewer_link, seed_pose_from_cam

def _enc(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

def test_decode_cam_roundtrips_encode():
    cam = {"position": [1.0, 0.0, 0.0], "target": [0.0, 0.0, 0.0], "up": [0, 0, 1], "fov_deg": 45}
    assert decode_cam(_enc(cam)) == cam

def test_parse_gltf_link_extracts_file_and_cam():
    cam = {"position": [2, 0, 0], "target": [0, 0, 0], "up": [0, 0, 1], "fov_deg": 45}
    url = f"https://x/viewer/gltf.html?file=assets/m.glb&cam={_enc(cam)}"
    got = parse_viewer_link(url)
    assert got["viewer_type"] == "gltf"
    assert got["file"] == "assets/m.glb"
    assert got["cam"] == cam

def test_parse_niivue_link_is_asset_only():
    got = parse_viewer_link("https://x/viewer/niivue.html?file=vol.nii.gz")
    assert got["viewer_type"] == "niivue" and got["file"] == "vol.nii.gz"
    assert got["cam"] is None

def test_malformed_cam_is_none_not_raise():
    got = parse_viewer_link("https://x/viewer/gltf.html?file=m.glb&cam=%%%bad")
    assert got["cam"] is None and got["file"] == "m.glb"

def test_seed_pose_from_cam_plus_x_is_azimuth0():
    sp = seed_pose_from_cam({"position": [5, 0, 2], "target": [0, 0, 0]})
    assert abs(sp["azimuth"] - 0.0) < 1e-6
    assert sp["elevation"] > 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/harbor_adapters/test_viewer_link.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `_viewer_link.py`**

```python
"""Parse a ScienceVision viewer URL into asset + optional camera pose.

Only the gltf viewer carries a camera (``?cam=base64url(JSON …)``, the inverse
of scripts/eval/sv3d_emit_viewer_urls.py::encode_cam); niivue/pointcloud/
cornerstone/molstar links carry only ``file``.
"""
from __future__ import annotations

import base64
import json
import math
from urllib.parse import urlparse, parse_qs


def decode_cam(s: str | None) -> dict | None:
    if not s:
        return None
    try:
        pad = s + "=" * (-len(s) % 4)
        obj = json.loads(base64.urlsafe_b64decode(pad.encode()).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not (isinstance(obj.get("position"), list) and isinstance(obj.get("target"), list)):
        return None
    if len(obj["position"]) != 3 or len(obj["target"]) != 3:
        return None
    return obj


def _decode_slice(s: str | None) -> dict | None:
    if not s:
        return None
    parts = s.split(":")
    if parts[0] not in ("axial", "sagittal", "coronal"):
        return None
    out: dict = {"axis": parts[0]}
    if len(parts) > 1 and parts[1]:
        out["fraction"] = float(parts[1])
    return out


def parse_viewer_link(url: str) -> dict:
    parsed = urlparse(url)
    stem = parsed.path.rsplit("/", 1)[-1].removesuffix(".html")
    q = parse_qs(parsed.query)
    file = (q.get("file") or [""])[0]
    cam = decode_cam((q.get("cam") or [None])[0]) if stem == "gltf" else None
    sl = _decode_slice((q.get("slice") or [None])[0]) if stem == "gltf" else None
    return {"viewer_type": stem, "file": file, "cam": cam, "slice": sl}


def seed_pose_from_cam(cam: dict | None) -> dict | None:
    if not cam:
        return None
    p = cam["position"]; t = cam["target"]
    d = [p[i] - t[i] for i in range(3)]
    n = math.sqrt(sum(c * c for c in d)) or 1.0
    dx, dy, dz = (c / n for c in d)
    azimuth = math.degrees(math.atan2(dy, dx))
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, dz))))
    return {"azimuth": round(azimuth, 6), "elevation": round(elevation, 6)}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/harbor_adapters/test_viewer_link.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/harbor/_viewer_link.py tests/harbor_adapters/test_viewer_link.py
git commit -m "feat(harbor): viewer-link resolver (decode_cam + parse_viewer_link + seed_pose)"
```

---

### Task 6: Auto-discover task sets in `collect_qagen_results`

**Files:**
- Modify: `scripts/harbor/collect_qagen_results.py:43-56` (`_index_sets`)
- Test: `tests/harbor_adapters/test_collect_autodiscover.py` (new)

**Interfaces:**
- Produces: `_index_sets(tasks_root)` also globs `tasks_root/*/*_final_tasks.jsonl` (in addition to the hardcoded `_SETS`), so an end2end set placed under `tasks_root/<set>/<anything>_final_tasks.jsonl` is indexed and its trials map to a real `set` (not `_unknown`). Hardcoded `_SETS` entries win on key collision (back-compat).

- [ ] **Step 1: Write the failing test**

```python
# tests/harbor_adapters/test_collect_autodiscover.py
import json
from scripts.harbor.collect_qagen_results import _index_sets

def test_autodiscovers_final_tasks_jsonl(tmp_path):
    d = tmp_path / "MyEndToEndSet"
    d.mkdir()
    (d / "MyEndToEndSet_final_tasks.jsonl").write_text(
        json.dumps({"task_id": "abc", "domain": "medical"}) + "\n")
    idx = _index_sets(tmp_path)
    assert idx["abc"]["set"] == "MyEndToEndSet"
    assert idx["abc"]["record"]["domain"] == "medical"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/harbor_adapters/test_collect_autodiscover.py -v`
Expected: FAIL — `KeyError: 'abc'` (only hardcoded sets indexed).

- [ ] **Step 3: Implement glob discovery**

```python
def _index_sets(tasks_root: Path) -> dict[str, dict[str, dict]]:
    """task_id -> {"set": name, "record": qa_gen_record}."""
    index: dict[str, dict] = {}
    # Auto-discover any <set>/*_final_tasks.jsonl first; hardcoded _SETS win on
    # collision (added last).
    discovered: list[tuple[str, Path]] = [
        (p.parent.name, p) for p in sorted(tasks_root.glob("*/*_final_tasks.jsonl"))
    ]
    hardcoded: list[tuple[str, Path]] = [
        (name, tasks_root / name / jsonl) for name, jsonl in _SETS.items()
    ]
    for set_name, path in discovered + hardcoded:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["task_id"]] = {"set": set_name, "record": rec}
    return index
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/harbor_adapters/test_collect_autodiscover.py tests/harbor_adapters/ -v`
Expected: PASS (existing collect tests still green).

- [ ] **Step 5: Commit**

```bash
git add scripts/harbor/collect_qagen_results.py tests/harbor_adapters/test_collect_autodiscover.py
git commit -m "feat(harbor): auto-discover *_final_tasks.jsonl sets in collect"
```

---

### Task 7: End-to-end driver (`run_end2end.py`)

**Files:**
- Create: `scripts/harbor/run_end2end.py`
- Create: `scripts/harbor/_e2e_resolve.py` (viewer-link JSONL → QA-Gen JSONL; keeps the driver ≤800 lines and unit-testable)
- Test: `tests/harbor_adapters/test_end2end_driver.py` (new)

**Interfaces:**
- Consumes: `parse_viewer_link`, `seed_pose_from_cam` (Task 5); `qa_gen_to_tasks` main / `build_one`; `collect_qagen_results`; `plot_leaderboard`.
- Produces:
  - `_e2e_resolve.resolve_task_list(rows, *, asset_root, seed_from_link) -> list[dict]` — each input row `{task_id, viewer_link, question, ground_truth, scorer?, domain?}` → a QA-Gen record `{task_id, domain, instruction, asset:{repo_path, format}, ground_truth, scorer, seed_pose?}`. `asset.format` is inferred from the resolved file extension. When `seed_from_link` and the link is a gltf link with a `cam`, `seed_pose` is attached (mesh only — non-gltf rows get no seed and log a warning).
  - `run_end2end.BASELINES` — ordered list of `(label, agent_env, n_views|None)`:
    `[("cc","claude-code",None), ("ccmcp","claude-code-mcp",None),
      ("cc2d-1","claude-code-2d",1), ("cc2d-3","claude-code-2d",3),
      ("cc2d-6","claude-code-2d",6), ("cc2d-12","claude-code-2d",12)]`.
  - `run_end2end.build_run_specs(jobroot, model) -> list[str]` — the `harness:model:jobdir` specs for collect (harness = the baseline label).
  - `run_end2end.main()` — CLI (see below).

- [ ] **Step 1: Write the failing test** (driver is orchestration; test the pure pieces + a dry-run fan-out with a fake runner)

```python
# tests/harbor_adapters/test_end2end_driver.py
import base64, json
from scripts.harbor._e2e_resolve import resolve_task_list
from scripts.harbor import run_end2end as E

def _cam_link(cam):
    raw = json.dumps(cam, separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"https://x/viewer/gltf.html?file=assets/m.glb&cam={b}"

def test_resolve_maps_row_to_qagen_record(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    rows = [{"task_id": "t1", "viewer_link": _cam_link({"position":[2,0,1],"target":[0,0,0]}),
             "question": "how many parts?", "ground_truth": {"value": "3"}}]
    recs = resolve_task_list(rows, asset_root=tmp_path, seed_from_link=True)
    assert recs[0]["asset"]["repo_path"] == "assets/m.glb"
    assert recs[0]["asset"]["format"] == "glb"
    assert recs[0]["instruction"] == "how many parts?"
    assert "seed_pose" in recs[0]  # gltf + cam + seed_from_link

def test_resolve_no_seed_when_canonical(tmp_path):
    (tmp_path / "assets").mkdir(); (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    rows = [{"task_id": "t1", "viewer_link": _cam_link({"position":[2,0,1],"target":[0,0,0]}),
             "question": "q", "ground_truth": {"value": "3"}}]
    recs = resolve_task_list(rows, asset_root=tmp_path, seed_from_link=False)
    assert "seed_pose" not in recs[0]

def test_six_baselines_and_run_specs():
    assert [b[0] for b in E.BASELINES] == ["cc","ccmcp","cc2d-1","cc2d-3","cc2d-6","cc2d-12"]
    specs = E.build_run_specs({b[0]: f"/jobs/{b[0]}" for b in E.BASELINES}, "claude-opus-4-8")
    assert "cc2d-6:claude-opus-4-8:/jobs/cc2d-6" in specs
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/harbor_adapters/test_end2end_driver.py -v`
Expected: FAIL — modules not present.

- [ ] **Step 3: Implement `_e2e_resolve.py`**

```python
"""Resolve an end2end task list (viewer_link rows) into QA-Gen records."""
from __future__ import annotations

import sys
from pathlib import Path

from scripts.harbor._viewer_link import parse_viewer_link, seed_pose_from_cam

_MESH_EXT = {".obj", ".ply", ".stl", ".glb", ".gltf", ".off"}


def _rel_to_root(file_str: str, asset_root: Path) -> str:
    """Best-effort map the link's file= to a repo-relative path under asset_root."""
    p = Path(file_str)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(asset_root.resolve()))
        except ValueError:
            return file_str
    return file_str


def resolve_task_list(rows, *, asset_root: Path, seed_from_link: bool) -> list[dict]:
    recs: list[dict] = []
    for row in rows:
        link = parse_viewer_link(row["viewer_link"])
        repo_path = _rel_to_root(link["file"], asset_root)
        fmt = Path(link["file"]).suffix.lower().lstrip(".") or "obj"
        rec = {
            "task_id": row["task_id"],
            "domain": row.get("domain", ""),
            "instruction": row.get("question") or row.get("instruction") or "",
            "asset": {"repo_path": repo_path, "path": link["file"], "format": fmt},
            "ground_truth": row.get("ground_truth"),
            "scorer": row.get("scorer") or {"primary": "llm_judge", "secondary": []},
        }
        if seed_from_link:
            is_mesh = Path(link["file"]).suffix.lower() in _MESH_EXT
            sp = seed_pose_from_cam(link["cam"]) if is_mesh else None
            if sp:
                rec["seed_pose"] = sp
            else:
                print(f"  warning: --seed-from-link but task {row['task_id']} has no "
                      f"usable mesh camera; falling back to canonical", file=sys.stderr)
        recs.append(rec)
    return recs
```

- [ ] **Step 4: Implement `run_end2end.py`** (orchestrator; concurrency via `concurrent.futures.ThreadPoolExecutor` over `subprocess.run(["harbor","run",...])`)

```python
"""End-to-end baseline matrix driver.

Given a task list of {task_id, viewer_link, question, ground_truth, ...}, run
all 6 baselines (cc / ccmcp / cc2d-{1,3,6,12}) over it and land a leaderboard.

Pipeline (each step reuses existing modules unchanged):
  1. resolve viewer_link rows -> QA-Gen records, written as a set jsonl
  2. build 6 task-dir trees via qa_gen_to_tasks (per-baseline agent-env/n_views)
  3. `harbor run` the 6 trees concurrently (--max-parallel), each to its own jobdir
  4. collect_qagen_results across the 6 jobs -> runs_leaderboard.{json,md}
  5. plot_leaderboard -> charts/

Usage:
  python -m scripts.harbor.run_end2end \
    --tasks e2e_tasks.jsonl --set-name MyEval --asset-root "$(pwd)" \
    --model claude-opus-4-8 --out-root QA-Gen/tasks --jobs-root jobs-e2e \
    --max-parallel 2 [--seed-from-link] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.harbor._e2e_resolve import resolve_task_list

_REPO_ROOT = Path(__file__).resolve().parents[2]

BASELINES = [
    ("cc", "claude-code", None),
    ("ccmcp", "claude-code-mcp", None),
    ("cc2d-1", "claude-code-2d", 1),
    ("cc2d-3", "claude-code-2d", 3),
    ("cc2d-6", "claude-code-2d", 6),
    ("cc2d-12", "claude-code-2d", 12),
]

_VERIFIER = "harbor_adapters.sci3d.verifier:Sci3dVerifier"


def build_run_specs(jobdirs: dict[str, str], model: str) -> list[str]:
    return [f"{label}:{model}:{jobdirs[label]}" for label, _, _ in BASELINES if label in jobdirs]


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _harbor_run(tasks_dir: Path, jobdir: Path, model: str, ae: list[str], runner) -> None:
    cmd = ["harbor", "run", "--path", str(tasks_dir), "--agent", "claude-code",
           "--model", model, "--verifier-import-path", _VERIFIER,
           "--env", "docker", "-o", str(jobdir)]
    for kv in ae:
        cmd += ["--ae", kv]
    runner(cmd)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", type=Path, required=True, help="end2end task-list jsonl")
    ap.add_argument("--set-name", required=True, help="QA-Gen set name (dir + jsonl stem)")
    ap.add_argument("--asset-root", type=Path, default=_REPO_ROOT)
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--out-root", type=Path, default=Path("QA-Gen/tasks"))
    ap.add_argument("--jobs-root", type=Path, default=Path("jobs-e2e"))
    ap.add_argument("--max-parallel", type=int, default=2)
    ap.add_argument("--seed-from-link", action="store_true")
    ap.add_argument("--ae", action="append", default=[], help="extra --ae KEY=VALUE for harbor run")
    ap.add_argument("--dry-run", action="store_true", help="build + print run specs, do not exec harbor")
    args = ap.parse_args()

    # 1. resolve -> QA-Gen set jsonl (indexable by collect via auto-discovery)
    rows = _load_jsonl(args.tasks)
    recs = resolve_task_list(rows, asset_root=args.asset_root, seed_from_link=args.seed_from_link)
    set_dir = args.out_root / args.set_name
    set_dir.mkdir(parents=True, exist_ok=True)
    set_jsonl = set_dir / f"{args.set_name}_final_tasks.jsonl"
    set_jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")

    # 2. build 6 task-dir trees
    from scripts.harbor.qa_gen_to_tasks import qa_record_to_task
    from scripts.harbor.build_harbor_tasks import build_one
    tasks_dirs: dict[str, Path] = {}
    for label, agent_env, n_views in BASELINES:
        out = args.jobs_root / f"tasks-{label}"
        out.mkdir(parents=True, exist_ok=True)
        for rec in recs:
            task = qa_record_to_task(rec, asset_root=args.asset_root)
            if n_views is not None:
                task["n_views"] = n_views
            build_one(task, out, containerized=True, agent_env=agent_env)
        tasks_dirs[label] = out

    # 3. run harbor concurrently
    jobdirs = {label: str(args.jobs_root / f"job-{label}") for label, _, _ in BASELINES}
    ae = list(args.ae)
    if not any(kv.startswith("SCI3D_JUDGE_MODEL=") for kv in ae):
        ae.append("SCI3D_JUDGE_MODEL=claude-haiku-4-5")
    run_specs = build_run_specs(jobdirs, args.model)
    if args.dry_run:
        print("DRY RUN — task dirs:")
        for label, d in tasks_dirs.items():
            print(f"  {label}: {d}")
        print("run specs:")
        for s in run_specs:
            print(f"  {s}")
        return

    def runner(cmd):
        subprocess.run(cmd, check=True)

    with ThreadPoolExecutor(max_workers=args.max_parallel) as ex:
        futs = [ex.submit(_harbor_run, tasks_dirs[label], Path(jobdirs[label]), args.model, ae, runner)
                for label, _, _ in BASELINES]
        for f in futs:
            f.result()

    # 4. collect + 5. plot (reuse existing CLIs)
    collect_cmd = [sys.executable, "-m", "scripts.harbor.collect_qagen_results",
                   "--tasks-root", str(args.out_root)]
    for s in run_specs:
        collect_cmd += ["--run", s]
    subprocess.run(collect_cmd, check=True)
    subprocess.run([sys.executable, "-m", "scripts.harbor.plot_leaderboard",
                    "--leaderboard", str(args.out_root / "runs_leaderboard.json"),
                    "--out-dir", str(args.out_root / "charts")], check=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/harbor_adapters/test_end2end_driver.py -v`
Expected: PASS.

- [ ] **Step 6: Manual dry-run smoke (no docker)**

Run:
```bash
printf '%s\n' '{"task_id":"smoke1","viewer_link":"https://x/viewer/gltf.html?file=assets/m.glb","question":"q","ground_truth":{"value":"1"}}' > /tmp/e2e_smoke.jsonl
python -m scripts.harbor.run_end2end --tasks /tmp/e2e_smoke.jsonl --set-name Smoke \
  --out-root /tmp/e2e_out --jobs-root /tmp/e2e_jobs --dry-run
```
Expected: prints 6 task dirs + 6 run specs (`cc … cc2d-12`); no harbor invoked.

- [ ] **Step 7: Commit**

```bash
git add scripts/harbor/_e2e_resolve.py scripts/harbor/run_end2end.py tests/harbor_adapters/test_end2end_driver.py
git commit -m "feat(harbor): end2end baseline-matrix driver (resolve->build x6->concurrent run->collect->plot)"
```

---

### Task 8: Runbook documentation

**Files:**
- Modify: `docs/harbor/PILOT_2D_RUNBOOK.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Add an "End-to-end baseline matrix" section**

Document: input jsonl schema (`{task_id, viewer_link, question, ground_truth, scorer?, domain?}`); the 6-baseline matrix and that `cc2d-N` share one image; the one-command invocation (with `--seed-from-link` vs default canonical, `--max-parallel`, and the required `--ae SCI3D_JUDGE_MODEL=claude-haiku-4-5`); where outputs land (`runs_leaderboard.{json,md}`, `charts/`); and that 2D task-gen must run in the mcp/GL env. Also note the view-count ablation semantics table (mesh/pointcloud orbit azimuths at elev 20°; NIfTI slice counts).

- [ ] **Step 2: Commit**

```bash
git add docs/harbor/PILOT_2D_RUNBOOK.md
git commit -m "docs(harbor): end2end baseline-matrix + view-count ablation runbook"
```

---

## Self-Review

**1. Spec coverage:**
- Spec §2 (6-baseline matrix) → Task 7 `BASELINES`. ✓
- Spec §3.1 (per-modality N sequences) → Task 3 (`mesh_orbit_azimuths`, `_nifti_fractions`). ✓
- Spec §3.2 (renderer extensions) → Tasks 1 (mesh), 2 (pointcloud); NIfTI needs none (uses existing `index`/`fraction`). ✓
- Spec §3.3 (`plan_views` params) → Task 3. ✓
- Spec §3.4 (render-reuse optimization) → intentionally deferred (optional, not implemented in v1); noted here so it isn't a silent gap. ✓
- Spec §4.1 (input schema) → Task 7 CLI + `_load_jsonl`. ✓
- Spec §4.2 (viewer-link resolver, decode_cam) → Task 5. ✓
- Spec §4.3 (seed switch, mesh-only, canonical fallback + log) → Task 5 `seed_pose_from_cam` + Task 7 `resolve_task_list` warning. ✓
- Spec §4.4 (build×6 → concurrent run×6 → collect → plot) → Task 7; concurrency via `ThreadPoolExecutor(max_workers=--max-parallel)`. ✓
- Spec §4.5 (reuse collect+plot outputs) → Task 7 steps 4-5; collect set-indexing gap closed by Task 6. ✓
- Spec §6 (testing) → each task ships pure-math/dry-run tests that skip GL. ✓

**2. Placeholder scan:** No TBD/TODO; all test/impl steps carry real code. Optimization §3.4 (render-reuse across tiers) is explicitly deferred/out-of-scope, not a placeholder.

**3. Type consistency:** `mesh_orbit_azimuths(n) -> list[float]` used identically in Tasks 3, 5 (convention reference), 7. `seed_pose` shape `{"azimuth", "elevation"}` consistent across `_multiview.plan_views`, `_viewer_link.seed_pose_from_cam`, `_e2e_resolve.resolve_task_list`. `azimuth`/`elevation` renderer args consistent across `mesh.camera_for_view`, `pointcloud.handle_pointcloud_thumbnail`, and the `plan_views` emitted args. `BASELINES` tuple shape `(label, agent_env, n_views|None)` consistent in `build_run_specs` and `main`.
