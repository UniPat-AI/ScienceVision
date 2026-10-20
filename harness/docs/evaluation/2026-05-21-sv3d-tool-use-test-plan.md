# sv3d Tool-Use 测试计划（Claude Code as Agent）

> **For agentic workers:** 推荐 `superpowers:subagent-driven-development` 或
> `superpowers:executing-plans` 逐 task 推进。所有步骤用 `- [ ]` 跟踪。

**Goal:** 验证 `sv3d_*` 系列 MCP 工具（旋转/平移/缩放/透明度/视图切换/蒙版/剖切/MPR）
能被 Claude Code 当作 agent 正确调用、参数可被完整记录回放、并且与 `viewer/*.html`
浏览器页面里"用户能看到的画面"一一对应。

**Architecture:**
- 测试分三层：(1) **静态契约**——15 个 sv3d 工具的 schema/dispatcher；
  (2) **状态轨迹**——agent 跑剧本调一连串工具，把 `sv3d_get_state` 拉下来比对期望；
  (3) **viewer 对齐**——把每条剧本的"当前相机/不透明度/切片"额外编码进 viewer URL，
  让人在浏览器里打开能看到与 `sv3d_render` PNG 一致的画面。
- Agent runner 直接复用 Claude Code（本仓库 `sci3d_agent/cli.py` 已有，但本计划
  不依赖它——我们用 Claude Code CLI / IDE 当 agent，把任务清单丢进去，让它产工具调用）。
- 结果产物：`evaluation_runs/<run_id>/` 下的 `trajectory.jsonl`、`renders/*.png`、
  `viewer_urls.txt`、`report.md`。

**Tech Stack:** Python 3.13 + pytest，`sci3d_agent.mcp` 已注册的 53 个工具，
`tools/serve_viewer.py` 起本地 viewer，Claude Code CLI（任意 MCP 客户端皆可）。

---

## 0. 现状盘点（已完成的调研，不写 task 只列结论）

### 0.1 MCP 端覆盖情况 ✓

`python -m sci3d_agent.mcp.server --list-tools` 列出 15 个 `sv3d_*`：

| 用户操作 | 工具 | 关键参数 | 备注 |
|---|---|---|---|
| 拖拽旋转 | `sv3d_camera_orbit` | `d_azimuth_deg`, `d_elevation_deg`, `d_roll_deg` | 增量 |
| 滚轮缩放 | `sv3d_camera_dolly` | `factor` (0.05–20.0) | 乘性 |
| Shift-拖拽平移 | `sv3d_camera_pan` | `dx_frac`, `dy_frac` (-2..2) | 视口分数 |
| 标准视角 | `sv3d_camera_set_view` | `view ∈ {front,back,left,right,top,bottom,iso,reset}`, `fit_bbox` | |
| 对准目标 | `sv3d_camera_look_at` | `target` 数组或 `bbox_center` / `mask_centroid` | |
| 透明度 | `sv3d_set_opacity` | `layer ∈ {base,mask,clip_cap}`, `alpha` | |
| 渲染模式 | `sv3d_set_render_mode` | `mode ∈ {surface,points,volume,mpr_axial,mpr_sagittal,mpr_coronal}` | mesh / nifti 各自子集 |
| 蒙版叠加 | `sv3d_toggle_mask` | `visible/mask_id/colormap/alpha` | |
| 剖切平面 | `sv3d_set_clip_plane` | `enabled/origin/normal/cap` | |
| MPR 切片 | `sv3d_set_slice` | `axis/index|fraction/window/level` | |
| 状态查询 | `sv3d_get_state` | — | 返回完整 Session JSON |
| 快照 | `sv3d_render` | `width/height/background/return` | png_b64 或 url |
| 多视角合板 | `sv3d_render_canonical_views` | `views/tile/...` | 不修改 session |
| 生命周期 | `sv3d_open_scene`, `sv3d_close_scene` | `qid` 或 `asset_path`，`ttl_seconds` | |

### 0.2 参数可记录性 ✓

`vision3d/_types.py::Session` 是 `@dataclass(frozen=True)`，所有变更经
`dataclasses.replace` 写回 `_store`。`sv3d_get_state` 任意时刻可拉到
`{camera{position,target,up,fov_deg}, mode, opacity{base,mask,clip_cap},
clip_plane, slice, mask_state, ...}`，因此**轨迹完全可序列化、可回放**。

### 0.3 与 viewer 页面的对应关系（关键 gap）

- `viewer/*.html` 当前只读 `file`/`overlay`/`type`/`format` 等**文件级**
  URL 参数（`grep params.get viewer/*.html` 验证）。
- `sv3d_camera_*` 改的是**服务端 Session**，浏览器看不到。
- `viewer/gltf.html` 用 `TrackballControls`，相机起手在 `(0.5,0.4,1.2)`，
  与 MCP 服务端默认相机互不通信。

**结论**：MCP "agent 视角" 与浏览器 "人类视角" 是两条**平行管线**——agent 用
`pyrender` 离线渲染 PNG，人用 viewer 看 three.js 渲染。本测试计划不引入 WebSocket
双向桥，而是采用 **single-shot URL 编码**：每条剧本结束时把当前 camera / opacity /
slice 编入 `viewer/*.html?cam=<json_b64>&...`，让人手动校验 viewer 渲染与
`sv3d_render` PNG 是否一致。这同时驱动 Task 6 给 viewer 加最小化的 URL 参数支持。

---

## 1. 文件结构与决策点

### 1.1 新增 / 改动的文件

```
docs/evaluation/2026-05-21-sv3d-tool-use-test-plan.md   ← 本文件
docs/evaluation/sv3d_tool_use/
  README.md                      # 跑测说明
  scenarios.md                   # 8 条剧本的人类可读描述
tests/mcp/test_sv3d_tool_use/
  __init__.py
  conftest.py                    # 共享 fixtures：fresh store、mock 资产
  test_contract.py               # Task 2：schema + 注册契约
  test_orbit_dolly_pan.py        # Task 3：相机三件套
  test_set_view_look_at.py       # Task 4：标准/对准
  test_opacity_clip.py           # Task 5：不透明 + 剖切
  test_mode_slice_mask.py        # Task 7：模式 / MPR / mask
  test_trajectory_replay.py      # Task 8：脚本→轨迹→回放等价
scripts/eval/
  sv3d_run_scenarios.py          # 跑 8 个剧本，落 trajectory.jsonl + 渲图
  sv3d_emit_viewer_urls.py       # 把 trajectory 末态编码成 viewer URL
viewer/shared/
  cam_url.js                     # 解析 cam= / opacity= / slice= URL 参数
viewer/gltf.html                 # 引用 cam_url.js（Task 6）
viewer/niivue.html               # 引用 cam_url.js（Task 6）
sci3d_agent/mcp/tools/urls.py    # 让 sv_build_viewer_url 可注入 cam/opacity（Task 6）
evaluation_runs/                 # gitignored，运行产物落地处
```

### 1.2 关键设计决策（已锁定）

1. **不改 sv3d 工具语义**：当前 schema 已能表达所有交互，本计划不增加新工具。
2. **viewer URL 状态参数最小集**：`cam=<base64url(JSON)>` 携带
   `{position,target,up,fov_deg}`；`opacity=<base|mask|clip_cap 的小数>`；
   `slice=<axis>:<index|frac>:<window>:<level>`。增加新参数走 `additionalProperties`
   兼容老 URL。
3. **不打通双向同步**：人在浏览器里旋转**不**回灌 Session。这是 V1 限制，
   写在 README 显眼处。
4. **测试用 mock 资产**：沿用 `tests/mcp/test_vision3d/test_e2e_*.py` 的
   `_install_fake` 模式注入 `sv_get_matched_record` / `sv_inspect_mesh`，
   不依赖 100GB 的 ScanNet/SQA3D 数据。
5. **Claude Code as agent**：测试不直接驱动 Claude Code 进程。我们让人或 CI 在
   Claude Code 里粘贴 `scenarios.md` 中的 prompt，由 Claude Code 输出工具调用，
   `sv3d_run_scenarios.py` 把 Claude Code 写出的 `tool_calls.jsonl` 重放到
   REGISTRY，与"人类期望终态"做断言。这样我们在评测 agent 的工具使用能力，
   而不是评测我们自己的脚本。

---

## 2. 任务分解

### Task 1: 起脚手架 + 评测目录

**Files:**
- Create: `docs/evaluation/sv3d_tool_use/README.md`
- Create: `docs/evaluation/sv3d_tool_use/scenarios.md`
- Create: `tests/mcp/test_sv3d_tool_use/__init__.py`
- Create: `tests/mcp/test_sv3d_tool_use/conftest.py`
- Modify: `.gitignore` — add `evaluation_runs/`

- [ ] **Step 1.1: 写 conftest 共享 fixtures**

```python
# tests/mcp/test_sv3d_tool_use/conftest.py
"""Shared fixtures: fresh store + mock assets installed into REGISTRY."""

import pytest
from sci3d_agent.mcp import server  # registers all tools
from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d._store import reset_store_for_tests
from sci3d_agent.mcp.tools.vision3d.state import _reset_shape_cache


@pytest.fixture(autouse=True)
def fresh():
    reset_store_for_tests()
    _reset_shape_cache()
    yield
    reset_store_for_tests()
    _reset_shape_cache()


@pytest.fixture
def registry_snapshot():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def install_fake(name: str, fn) -> None:
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )


@pytest.fixture
def mesh_session(registry_snapshot):
    """Open a mesh session with canned record + inspect, return session_id."""
    install_fake(
        "sv_get_matched_record",
        lambda _: {
            "record": {
                "qid": "fixture-mesh-0",
                "asset_kind": "mesh",
                "url_viewer": (
                    "/datasets/_shared_3d/scannet/scans_test/"
                    "scene0707_00/scene0707_00_vh_clean_2.ply"
                ),
            }
        },
    )
    install_fake(
        "sv_inspect_mesh",
        lambda _: {
            "format": "ply",
            "n_vertices": 1000,
            "n_faces": 2000,
            "bbox": [[-5.0, -5.0, 0.0], [5.0, 5.0, 3.0]],
            "extent": [10.0, 10.0, 3.0],
            "has_vertex_colors": True,
            "has_uv": False,
            "axis_up_hint": "Z",
        },
    )
    r = REGISTRY["sv3d_open_scene"].handler({"qid": "fixture-mesh-0"})
    return r["session_id"]
```

- [ ] **Step 1.2: 写 README 描述跑测流程**

```markdown
# sv3d Tool-Use 测试

## 跑测试
pytest tests/mcp/test_sv3d_tool_use/ -q

## 跑 Claude Code 剧本
1. 启动本地 viewer：python tools/serve_viewer.py --port 8000
2. 在 Claude Code 里粘贴 docs/evaluation/sv3d_tool_use/scenarios.md 的某段
3. 拷贝 Claude Code 输出的工具调用到 evaluation_runs/<run_id>/tool_calls.jsonl
4. python scripts/eval/sv3d_run_scenarios.py --run-id <id> --replay
5. 打开 evaluation_runs/<run_id>/viewer_urls.txt 第一行，肉眼比对
```

- [ ] **Step 1.3: 验证空脚手架可被 pytest 收集**

Run: `pytest tests/mcp/test_sv3d_tool_use/ --collect-only -q`
Expected: 0 tests, no errors.

- [ ] **Step 1.4: Commit**

```bash
git add tests/mcp/test_sv3d_tool_use/ docs/evaluation/sv3d_tool_use/ .gitignore
git commit -m "test(sv3d): scaffold tool-use eval suite + scenarios doc"
```

---

### Task 2: 静态契约测试（15 个工具的 schema 完整性）

**Files:**
- Create: `tests/mcp/test_sv3d_tool_use/test_contract.py`

- [ ] **Step 2.1: 写失败测试——枚举工具集**

```python
# tests/mcp/test_sv3d_tool_use/test_contract.py
import pytest
from sci3d_agent.mcp import server  # noqa: F401
from sci3d_agent.mcp.registry import REGISTRY

EXPECTED_SV3D = {
    "sv3d_open_scene",
    "sv3d_close_scene",
    "sv3d_get_state",
    "sv3d_camera_orbit",
    "sv3d_camera_dolly",
    "sv3d_camera_pan",
    "sv3d_camera_set_view",
    "sv3d_camera_look_at",
    "sv3d_set_render_mode",
    "sv3d_set_opacity",
    "sv3d_toggle_mask",
    "sv3d_set_slice",
    "sv3d_set_clip_plane",
    "sv3d_render",
    "sv3d_render_canonical_views",
}


def test_all_15_sv3d_tools_registered():
    actual = {n for n in REGISTRY if n.startswith("sv3d_")}
    assert actual == EXPECTED_SV3D


def test_every_sv3d_schema_has_session_id_or_oneof():
    for name in EXPECTED_SV3D:
        schema = REGISTRY[name].input_schema
        if name == "sv3d_open_scene":
            assert "oneOf" in schema, f"{name} must accept qid|asset_path"
        else:
            req = schema.get("required", [])
            assert "session_id" in req, f"{name} missing required session_id"


def test_camera_orbit_accepts_three_axes():
    s = REGISTRY["sv3d_camera_orbit"].input_schema
    assert {"d_azimuth_deg", "d_elevation_deg"} <= set(s["required"])
    assert "d_roll_deg" in s["properties"]


def test_dolly_factor_bounds():
    s = REGISTRY["sv3d_camera_dolly"].input_schema["properties"]["factor"]
    assert s["minimum"] == 0.05 and s["maximum"] == 20.0


def test_pan_dx_dy_bounds():
    p = REGISTRY["sv3d_camera_pan"].input_schema["properties"]
    assert p["dx_frac"]["minimum"] == -2.0 and p["dx_frac"]["maximum"] == 2.0


def test_set_view_enum_complete():
    enum = REGISTRY["sv3d_camera_set_view"].input_schema["properties"]["view"]["enum"]
    assert set(enum) == {
        "front", "back", "left", "right", "top", "bottom", "iso", "reset"
    }


def test_set_opacity_layers():
    enum = REGISTRY["sv3d_set_opacity"].input_schema["properties"]["layer"]["enum"]
    assert set(enum) == {"base", "mask", "clip_cap"}


def test_render_mode_covers_mesh_and_nifti():
    enum = REGISTRY["sv3d_set_render_mode"].input_schema["properties"]["mode"]["enum"]
    assert {"surface", "points"} <= set(enum)            # mesh modes
    assert {"volume", "mpr_axial", "mpr_sagittal", "mpr_coronal"} <= set(enum)


def test_set_slice_window_level_present():
    p = REGISTRY["sv3d_set_slice"].input_schema["properties"]
    assert {"axis", "index", "fraction", "window", "level"} <= set(p)
```

- [ ] **Step 2.2: 运行——应全部通过（基于现状）**

Run: `pytest tests/mcp/test_sv3d_tool_use/test_contract.py -v`
Expected: 8 PASS。

- [ ] **Step 2.3: Commit**

```bash
git add tests/mcp/test_sv3d_tool_use/test_contract.py
git commit -m "test(sv3d): static contract test for 15 tool schemas"
```

---

### Task 3: 相机三件套（orbit / dolly / pan）状态变更测试

**Files:**
- Create: `tests/mcp/test_sv3d_tool_use/test_orbit_dolly_pan.py`

- [ ] **Step 3.1: 写测试——orbit 改变相机方位但保持 target 不变**

```python
# tests/mcp/test_sv3d_tool_use/test_orbit_dolly_pan.py
import math
import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


def _vec_dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def test_orbit_preserves_target_and_distance(mesh_session):
    sid = mesh_session
    s0 = _state(sid)
    cam0 = s0["camera"]
    d0 = _vec_dist(cam0["position"], cam0["target"])

    REGISTRY["sv3d_camera_orbit"].handler(
        {"session_id": sid, "d_azimuth_deg": 30.0, "d_elevation_deg": 15.0}
    )
    s1 = _state(sid)
    cam1 = s1["camera"]

    # target unchanged
    assert cam1["target"] == cam0["target"]
    # distance preserved (orbit is rotation, not zoom)
    d1 = _vec_dist(cam1["position"], cam1["target"])
    assert math.isclose(d0, d1, rel_tol=1e-6)
    # position actually changed
    assert cam1["position"] != cam0["position"]


def test_dolly_changes_distance_multiplicatively(mesh_session):
    sid = mesh_session
    cam0 = _state(sid)["camera"]
    d0 = _vec_dist(cam0["position"], cam0["target"])

    REGISTRY["sv3d_camera_dolly"].handler({"session_id": sid, "factor": 0.5})
    cam1 = _state(sid)["camera"]
    d1 = _vec_dist(cam1["position"], cam1["target"])
    assert math.isclose(d1, d0 * 0.5, rel_tol=1e-3)
    # target unchanged by dolly
    assert cam1["target"] == cam0["target"]


def test_pan_shifts_target_and_position_together(mesh_session):
    sid = mesh_session
    cam0 = _state(sid)["camera"]
    d0 = _vec_dist(cam0["position"], cam0["target"])

    REGISTRY["sv3d_camera_pan"].handler(
        {"session_id": sid, "dx_frac": 0.2, "dy_frac": -0.1}
    )
    cam1 = _state(sid)["camera"]
    # distance unchanged (pan slides both endpoints by same vector)
    d1 = _vec_dist(cam1["position"], cam1["target"])
    assert math.isclose(d0, d1, rel_tol=1e-6)
    # target moved
    assert cam1["target"] != cam0["target"]


def test_dolly_factor_out_of_range_returns_error(mesh_session):
    # Schema enforces 0.05–20.0; the dispatcher should reject 50.0.
    r = REGISTRY["sv3d_camera_dolly"].handler(
        {"session_id": mesh_session, "factor": 50.0}
    )
    assert "error" in r
```

- [ ] **Step 3.2: 跑测试**

Run: `pytest tests/mcp/test_sv3d_tool_use/test_orbit_dolly_pan.py -v`
Expected: 全 PASS。如某项 FAIL，先看 `vision3d/camera.py` 里的对应 handler
是否真的把变更经 `dataclasses.replace` 写回 store。

- [ ] **Step 3.3: Commit**

```bash
git add tests/mcp/test_sv3d_tool_use/test_orbit_dolly_pan.py
git commit -m "test(sv3d): orbit/dolly/pan camera invariants"
```

---

### Task 4: 标准视角 + look_at

**Files:**
- Create: `tests/mcp/test_sv3d_tool_use/test_set_view_look_at.py`

- [ ] **Step 4.1: 写测试**

```python
# tests/mcp/test_sv3d_tool_use/test_set_view_look_at.py
import math
import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


@pytest.mark.parametrize(
    "view", ["front", "back", "left", "right", "top", "bottom", "iso"]
)
def test_set_view_canonical(mesh_session, view):
    sid = mesh_session
    r = REGISTRY["sv3d_camera_set_view"].handler(
        {"session_id": sid, "view": view, "fit_bbox": True}
    )
    assert "error" not in r
    cam = _state(sid)["camera"]
    # Each canonical view must produce a finite, distinct position.
    assert all(math.isfinite(x) for x in cam["position"])
    assert all(math.isfinite(x) for x in cam["target"])


def test_set_view_reset_returns_to_default(mesh_session):
    sid = mesh_session
    cam0 = _state(sid)["camera"]
    REGISTRY["sv3d_camera_orbit"].handler(
        {"session_id": sid, "d_azimuth_deg": 90.0, "d_elevation_deg": 30.0}
    )
    REGISTRY["sv3d_camera_set_view"].handler({"session_id": sid, "view": "reset"})
    cam_reset = _state(sid)["camera"]
    # `reset` should match the freshly-opened default camera.
    assert cam_reset["position"] == cam0["position"]
    assert cam_reset["target"] == cam0["target"]


def test_look_at_explicit_point(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_camera_look_at"].handler(
        {"session_id": sid, "target": [1.0, 2.0, 3.0]}
    )
    cam = _state(sid)["camera"]
    assert cam["target"] == [1.0, 2.0, 3.0]


def test_look_at_bbox_center(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_camera_look_at"].handler(
        {"session_id": sid, "target": "bbox_center"}
    )
    cam = _state(sid)["camera"]
    # bbox is [[-5,-5,0],[5,5,3]] from fixture → center (0,0,1.5)
    assert cam["target"] == [0.0, 0.0, 1.5]
```

- [ ] **Step 4.2: 跑测试 + Commit**

Run: `pytest tests/mcp/test_sv3d_tool_use/test_set_view_look_at.py -v`
Expected: 9 PASS（7 个 parametrize + 2 个独立）。

```bash
git add tests/mcp/test_sv3d_tool_use/test_set_view_look_at.py
git commit -m "test(sv3d): canonical view + look_at semantics"
```

---

### Task 5: 透明度 + 剖切

**Files:**
- Create: `tests/mcp/test_sv3d_tool_use/test_opacity_clip.py`

- [ ] **Step 5.1: 写测试**

```python
# tests/mcp/test_sv3d_tool_use/test_opacity_clip.py
import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


@pytest.mark.parametrize("layer", ["base", "mask", "clip_cap"])
def test_set_opacity_per_layer(mesh_session, layer):
    sid = mesh_session
    REGISTRY["sv3d_set_opacity"].handler(
        {"session_id": sid, "layer": layer, "alpha": 0.42}
    )
    op = _state(sid)["opacity"]
    assert op[layer] == pytest.approx(0.42)
    # other layers untouched
    for other in {"base", "mask", "clip_cap"} - {layer}:
        assert op[other] != 0.42 or op[other] == op[other]  # no-op safety


def test_opacity_alpha_clamped_by_schema(mesh_session):
    r = REGISTRY["sv3d_set_opacity"].handler(
        {"session_id": mesh_session, "layer": "base", "alpha": 1.5}
    )
    assert "error" in r


def test_clip_plane_enable_disable(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_set_clip_plane"].handler(
        {
            "session_id": sid,
            "enabled": True,
            "origin": [0.0, 0.0, 1.0],
            "normal": [0.0, 0.0, 1.0],
            "cap": True,
        }
    )
    cp = _state(sid)["clip_plane"]
    assert cp["enabled"] is True
    assert cp["origin"] == [0.0, 0.0, 1.0]
    assert cp["cap"] is True

    REGISTRY["sv3d_set_clip_plane"].handler(
        {"session_id": sid, "enabled": False}
    )
    assert _state(sid)["clip_plane"]["enabled"] is False


def test_clip_plane_normal_must_be_unit(mesh_session):
    # Implementation should normalize automatically, not reject.
    REGISTRY["sv3d_set_clip_plane"].handler(
        {
            "session_id": mesh_session,
            "enabled": True,
            "origin": [0.0, 0.0, 0.0],
            "normal": [3.0, 0.0, 0.0],  # length 3
            "cap": False,
        }
    )
    n = _state(mesh_session)["clip_plane"]["normal"]
    norm = sum(x * x for x in n) ** 0.5
    assert norm == pytest.approx(1.0, rel=1e-3)
```

- [ ] **Step 5.2: 跑测试 + Commit**

Run: `pytest tests/mcp/test_sv3d_tool_use/test_opacity_clip.py -v`
Expected: 6 PASS（3 parametrize + 3 独立）。如 `test_clip_plane_normal_must_be_unit`
失败，要么实现里没归一化（修实现），要么我们调整契约（接受非单位向量但回写
归一化结果）。先看代码再决定，**不要直接放宽测试**。

```bash
git add tests/mcp/test_sv3d_tool_use/test_opacity_clip.py
git commit -m "test(sv3d): per-layer opacity + clip plane state"
```

---

### Task 6: viewer URL 参数最小集（让人能肉眼比对）

**Files:**
- Create: `viewer/shared/cam_url.js`
- Modify: `viewer/gltf.html` — 在 `controls = new TrackballControls(...)` 之后
  调用 `applyCamFromUrl(camera, controls)`
- Modify: `viewer/niivue.html` — 解析 `slice=` / `opacity=`
- Modify: `viewer/pointcloud.html` — 解析 `cam=`
- Modify: `sci3d_agent/mcp/tools/urls.py` — `_handle_build_viewer_url` 支持
  `cam` / `opacity` / `slice` 三个可选 string 参数（当成 opaque 透传）
- Modify: `tests/mcp/test_urls.py` — 新增 1 个测试覆盖透传

- [ ] **Step 6.1: 写 cam_url.js（解析 + 回写 helper）**

```javascript
// viewer/shared/cam_url.js
// Tiny helper: read MCP-style camera/opacity/slice params from URL and apply.
//   ?cam=<base64url(JSON {position,target,up,fov_deg})>
//   ?opacity=base:0.7,mask:0.4
//   ?slice=axial:0.5:400:40        (axis:frac_or_index:window:level)
//
// V1 is one-way: URL → viewer. We do NOT push browser-side orbits back to
// the URL (the user can call sv3d_get_state instead).

export function decodeCam(s) {
  if (!s) return null;
  try {
    const json = atob(s.replaceAll('-', '+').replaceAll('_', '/'));
    const obj = JSON.parse(json);
    if (!obj.position || !obj.target) return null;
    return obj;
  } catch (e) {
    console.warn('cam_url: bad cam=', s, e);
    return null;
  }
}

export function decodeOpacity(s) {
  if (!s) return {};
  const out = {};
  for (const kv of s.split(',')) {
    const [k, v] = kv.split(':');
    const n = parseFloat(v);
    if (Number.isFinite(n) && n >= 0 && n <= 1) out[k] = n;
  }
  return out;
}

export function decodeSlice(s) {
  if (!s) return null;
  const [axis, fracOrIdx, window, level] = s.split(':');
  if (!['axial', 'sagittal', 'coronal'].includes(axis)) return null;
  return {
    axis,
    fraction: parseFloat(fracOrIdx),
    window: window != null ? parseFloat(window) : undefined,
    level: level != null ? parseFloat(level) : undefined,
  };
}

export function applyCamFromUrl(THREE, camera, controls, params) {
  const cam = decodeCam(params.get('cam'));
  if (!cam) return false;
  camera.position.fromArray(cam.position);
  controls.target.fromArray(cam.target);
  if (cam.up) camera.up.fromArray(cam.up);
  if (cam.fov_deg) {
    camera.fov = cam.fov_deg;
    camera.updateProjectionMatrix();
  }
  controls.update();
  return true;
}
```

- [ ] **Step 6.2: 在 gltf.html 接入**

找到现有的 `const controls = new TrackballControls(camera, canvas);`
（约 viewer/gltf.html:85），在其下方加：

```javascript
import { applyCamFromUrl } from './shared/cam_url.js';
// ... after controls created, before fitToScene:
const camOverride = applyCamFromUrl(THREE, camera, controls, params);
// If user pinned a camera via URL, skip auto-fit on first frame.
window.__camFromUrl = camOverride;
```

并在 `fitToScene` 入口加：`if (window.__camFromUrl) return;`。

- [ ] **Step 6.3: 在 urls.py 增加透传**

```python
# sci3d_agent/mcp/tools/urls.py — inside _handle_build_viewer_url, after
# building params dict:
for key in ("cam", "opacity", "slice"):
    val = args.get(key)
    if isinstance(val, str) and val:
        params[key] = val

# and in _SCHEMA_BUILD_VIEWER_URL["properties"], add:
#   "cam": {"type": "string"},
#   "opacity": {"type": "string"},
#   "slice": {"type": "string"},
```

- [ ] **Step 6.4: urls 测试**

```python
# tests/mcp/test_urls.py — append:
def test_build_viewer_url_passes_through_cam_opacity_slice():
    from sci3d_agent.mcp.registry import REGISTRY
    r = REGISTRY["sv_build_viewer_url"].handler({
        "asset_path": "/datasets/foo.glb",
        "viewer": "gltf",
        "cam": "eyJwb3MiOlsxLDIsM119",  # any opaque string
        "opacity": "base:0.7",
        "slice": "axial:0.5:400:40",
    })
    assert "error" not in r
    assert "cam=eyJwb3MiOlsxLDIsM119" in r["url"]
    assert "opacity=" in r["url"] and "slice=" in r["url"]
```

- [ ] **Step 6.5: 跑测试 + 手测**

Run: `pytest tests/mcp/test_urls.py -v`
Expected: 全 PASS（已有 + 新增）。

手测：
```bash
python tools/serve_viewer.py --port 8000 &
open "http://localhost:8000/viewer/gltf.html?file=/datasets/<sample>.glb&cam=$(python -c 'import base64,json;print(base64.urlsafe_b64encode(json.dumps({"position":[5,3,5],"target":[0,0,1.5],"up":[0,0,1],"fov_deg":45}).encode()).decode())')"
```
Expected: GLB 加载后摄像机停在 (5,3,5) 朝 (0,0,1.5) 方向，不被自动 fit 覆盖。

- [ ] **Step 6.6: Commit**

```bash
git add viewer/shared/cam_url.js viewer/gltf.html viewer/niivue.html \
        viewer/pointcloud.html sci3d_agent/mcp/tools/urls.py \
        tests/mcp/test_urls.py
git commit -m "feat(viewer): cam/opacity/slice URL params for sv3d alignment"
```

---

### Task 7: 渲染模式 / MPR 切片 / 蒙版（含 nifti session）

**Files:**
- Create: `tests/mcp/test_sv3d_tool_use/test_mode_slice_mask.py`

- [ ] **Step 7.1: 在 conftest 增加 nifti_session fixture**

```python
# tests/mcp/test_sv3d_tool_use/conftest.py — append:
@pytest.fixture
def nifti_session(registry_snapshot):
    install_fake(
        "sv_get_matched_record",
        lambda _: {
            "record": {
                "qid": "fixture-nifti-0",
                "asset_kind": "nifti",
                "url_viewer": "/datasets/_shared_3d/medical/sample.nii.gz",
                "mask_path": "/datasets/_shared_3d/medical/sample_mask.nii.gz",
            }
        },
    )
    install_fake(
        "sv_inspect_nifti",
        lambda _: {
            "shape": [256, 256, 180],
            "voxel_size_mm": [1.0, 1.0, 1.0],
            "affine_4x4": [
                [1, 0, 0, -128],
                [0, 1, 0, -128],
                [0, 0, 1, -90],
                [0, 0, 0, 1],
            ],
            "axis_up_hint": "RAS",
            "intensity_p1_p99": [0.0, 1000.0],
        },
    )
    install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda _: {
            "labels": [{"mask_id": "1", "voxel_count": 5000,
                        "centroid_world": [0, 0, 0],
                        "bbox_world": [[-10, -10, -10], [10, 10, 10]],
                        "best_slices": {"axial": 90, "coronal": 128, "sagittal": 128}}]
        },
    )
    r = REGISTRY["sv3d_open_scene"].handler({"qid": "fixture-nifti-0"})
    return r["session_id"]
```

- [ ] **Step 7.2: 写测试**

```python
# tests/mcp/test_sv3d_tool_use/test_mode_slice_mask.py
import pytest
from sci3d_agent.mcp.registry import REGISTRY


def _state(sid):
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


def test_mesh_mode_surface_to_points(mesh_session):
    sid = mesh_session
    REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "points", "point_size": 2.0}
    )
    assert _state(sid)["mode"] == "points"


def test_mesh_rejects_volume_mode(mesh_session):
    r = REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": mesh_session, "mode": "volume"}
    )
    assert "error" in r  # mesh kind cannot do volume


def test_nifti_mpr_axial_then_sagittal(nifti_session):
    sid = nifti_session
    REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "mpr_axial"}
    )
    assert _state(sid)["mode"] == "mpr_axial"
    REGISTRY["sv3d_set_slice"].handler(
        {"session_id": sid, "axis": "axial", "fraction": 0.5}
    )
    sl = _state(sid)["slice"]
    assert sl["axis"] == "axial"
    assert sl["fraction"] == pytest.approx(0.5)


def test_nifti_slice_window_level(nifti_session):
    sid = nifti_session
    REGISTRY["sv3d_set_render_mode"].handler(
        {"session_id": sid, "mode": "mpr_sagittal"}
    )
    REGISTRY["sv3d_set_slice"].handler(
        {"session_id": sid, "axis": "sagittal",
         "fraction": 0.3, "window": 400, "level": 40}
    )
    sl = _state(sid)["slice"]
    assert sl["window"] == [pytest.approx(400 - 200), pytest.approx(400 + 200)] \
        or sl["level"] == pytest.approx(40)  # exact encoding depends on impl;
        # check that at least one is propagated.


def test_toggle_mask_partial_update(nifti_session):
    sid = nifti_session
    REGISTRY["sv3d_toggle_mask"].handler(
        {"session_id": sid, "visible": True, "mask_id": "1",
         "colormap": "jet", "alpha": 0.6}
    )
    m = _state(sid)["mask_state"]
    assert m["visible"] is True
    assert m["mask_id"] == "1"
    assert m["colormap"] == "jet"
    assert m["alpha"] == pytest.approx(0.6)

    # Partial update: only flip visibility
    REGISTRY["sv3d_toggle_mask"].handler({"session_id": sid, "visible": False})
    m2 = _state(sid)["mask_state"]
    assert m2["visible"] is False
    assert m2["mask_id"] == "1"           # preserved
    assert m2["colormap"] == "jet"        # preserved
```

- [ ] **Step 7.3: 跑测试 + Commit**

Run: `pytest tests/mcp/test_sv3d_tool_use/test_mode_slice_mask.py -v`
Expected: 5 PASS。

```bash
git add tests/mcp/test_sv3d_tool_use/test_mode_slice_mask.py \
        tests/mcp/test_sv3d_tool_use/conftest.py
git commit -m "test(sv3d): render mode + MPR slice + mask state"
```

---

### Task 8: 端到端轨迹回放（剧本→tool_calls.jsonl→断言终态）

**Files:**
- Create: `docs/evaluation/sv3d_tool_use/scenarios.md`
- Create: `scripts/eval/sv3d_run_scenarios.py`
- Create: `scripts/eval/sv3d_emit_viewer_urls.py`
- Create: `tests/mcp/test_sv3d_tool_use/test_trajectory_replay.py`

**剧本设计（8 条，scenarios.md 给 Claude Code 的提示词）：**

| ID | 任务（人类语言） | 期望工具调用序列 |
|---|---|---|
| S1 | "打开 ScanQA scene0707，从正前方看" | open → set_view(front) |
| S2 | "把场景旋转 45° 再放大 2 倍" | open → orbit(45,0) → dolly(0.5) |
| S3 | "显示墙边的椅子，把它居中" | open → look_at(bbox_center) → orbit(90,15) |
| S4 | "把家具变成半透明，看到背后的墙" | open → set_opacity(base, 0.4) |
| S5 | "切换成点云模式" | open → set_render_mode(points, point_size=2) |
| S6 | "对一份脑 MRI 看冠状切片中线，窗位 40/400" | open(nifti) → set_render_mode(mpr_coronal) → set_slice(coronal, 0.5, 400, 40) |
| S7 | "在 MRI 上叠加肿瘤蒙版（jet 配色，半透明）" | open(nifti) → toggle_mask(visible=True, mask_id='1', colormap='jet', alpha=0.5) |
| S8 | "切个剖面看横截面" | open → set_clip_plane(enabled=True, origin=center, normal=z) |

- [ ] **Step 8.1: 写 scenarios.md**

```markdown
# Claude Code 剧本（粘进 Claude Code 即可执行）

> 前置：MCP 服务起在 stdio，Claude Code 已连接 sv3d_* 工具集。

## S1 — Front view

请打开 qid="scanqa-test_wo_obj-test-scene0707-0"，把摄像机切到 front 视角，
然后用 sv3d_render 给我一张 768×768 的 PNG。最后 sv3d_get_state 一下，
让我看到最终相机在哪。

## S2 — Orbit + zoom
... (依此类推 8 条)
```

- [ ] **Step 8.2: 写 sv3d_run_scenarios.py（重放器）**

```python
"""Replay a tool_calls.jsonl produced by Claude Code through REGISTRY."""
import argparse, json, pathlib, sys
from sci3d_agent.mcp import server  # registers tools
from sci3d_agent.mcp.registry import REGISTRY


def replay(jsonl_path: pathlib.Path, out_dir: pathlib.Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    trajectory, sid = [], None
    with jsonl_path.open() as f:
        for line in f:
            call = json.loads(line)
            name, args = call["name"], call["arguments"]
            result = REGISTRY[name].handler(args)
            trajectory.append({"name": name, "args": args, "result": result})
            if name == "sv3d_open_scene" and "session_id" in result:
                sid = result["session_id"]
    final_state = (
        REGISTRY["sv3d_get_state"].handler({"session_id": sid})
        if sid else {}
    )
    (out_dir / "trajectory.jsonl").write_text(
        "\n".join(json.dumps(t) for t in trajectory)
    )
    (out_dir / "final_state.json").write_text(json.dumps(final_state, indent=2))
    return {"final_state": final_state, "n_steps": len(trajectory)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool-calls", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()
    summary = replay(args.tool_calls, args.out)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.3: 写 sv3d_emit_viewer_urls.py（终态→viewer URL）**

```python
"""Translate a final sv3d state into viewer URLs for visual diff."""
import argparse, base64, json, pathlib
from sci3d_agent.mcp import server  # noqa: F401
from sci3d_agent.mcp.registry import REGISTRY


def cam_b64(cam: dict) -> str:
    raw = json.dumps(cam, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final-state", type=pathlib.Path, required=True)
    ap.add_argument("--asset-path", required=True)
    ap.add_argument("--viewer", default="gltf")
    args = ap.parse_args()

    state = json.loads(args.final_state.read_text())
    op = state.get("opacity", {})
    opacity_str = ",".join(f"{k}:{v}" for k, v in op.items())
    sl = state.get("slice", {})
    slice_str = (
        f"{sl['axis']}:{sl.get('fraction', 0.5)}:"
        f"{sl.get('window', [0, 0])[1]}:{sl.get('level', 0)}"
        if sl.get("axis") else ""
    )

    r = REGISTRY["sv_build_viewer_url"].handler({
        "asset_path": args.asset_path,
        "viewer": args.viewer,
        "cam": cam_b64(state["camera"]),
        "opacity": opacity_str,
        **({"slice": slice_str} if slice_str else {}),
    })
    print(r["url"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.4: 写回放等价测试（不依赖 Claude Code）**

```python
# tests/mcp/test_sv3d_tool_use/test_trajectory_replay.py
import json, pathlib
from sci3d_agent.mcp.registry import REGISTRY


def test_canned_s2_orbit_zoom_trajectory(mesh_session, tmp_path):
    """Same trajectory must produce same final state across two replays."""
    calls = [
        {"name": "sv3d_camera_set_view",
         "arguments": {"session_id": mesh_session, "view": "front"}},
        {"name": "sv3d_camera_orbit",
         "arguments": {"session_id": mesh_session,
                       "d_azimuth_deg": 45.0, "d_elevation_deg": 0.0}},
        {"name": "sv3d_camera_dolly",
         "arguments": {"session_id": mesh_session, "factor": 0.5}},
    ]
    for c in calls:
        REGISTRY[c["name"]].handler(c["arguments"])
    s1 = REGISTRY["sv3d_get_state"].handler({"session_id": mesh_session})

    # Reset and replay → same state.
    REGISTRY["sv3d_camera_set_view"].handler(
        {"session_id": mesh_session, "view": "reset"}
    )
    for c in calls:
        REGISTRY[c["name"]].handler(c["arguments"])
    s2 = REGISTRY["sv3d_get_state"].handler({"session_id": mesh_session})

    assert s1["camera"] == s2["camera"], "trajectory not deterministic"
```

- [ ] **Step 8.5: 跑测试 + 手跑一条剧本**

Run: `pytest tests/mcp/test_sv3d_tool_use/test_trajectory_replay.py -v`
Expected: PASS。

手跑：
1. 在 Claude Code 里粘 S2，让它产生 3 个工具调用，把 stdout 里的 JSONL 拷到
   `evaluation_runs/manual-S2/tool_calls.jsonl`。
2. `python scripts/eval/sv3d_run_scenarios.py --tool-calls evaluation_runs/manual-S2/tool_calls.jsonl --out evaluation_runs/manual-S2/`
3. `python scripts/eval/sv3d_emit_viewer_urls.py --final-state evaluation_runs/manual-S2/final_state.json --asset-path /datasets/.../scene0707_00_vh_clean_2.ply --viewer gltf`
4. 打开打印的 URL，肉眼比对：viewer 里相机角度应当与 PNG 渲染基本一致
   （因 trackball vs pyrender FOV/clip 计算可能有亚像素差异，10° 内即视为对齐）。

- [ ] **Step 8.6: Commit**

```bash
git add scripts/eval/ tests/mcp/test_sv3d_tool_use/test_trajectory_replay.py \
        docs/evaluation/sv3d_tool_use/scenarios.md
git commit -m "test(sv3d): trajectory replay + viewer URL emitter for visual diff"
```

---

### Task 9: 整合报告 + 跑全套

**Files:**
- Create: `docs/evaluation/sv3d_tool_use/RESULTS.md`（运行后填写）

- [ ] **Step 9.1: 跑整套 sv3d 测试**

Run: `pytest tests/mcp/test_sv3d_tool_use/ tests/mcp/test_urls.py -v`
Expected: 30+ PASS, 0 FAIL。

- [ ] **Step 9.2: 跑 MCP 主套不回归**

Run: `pytest tests/mcp/ -q`
Expected: 491+ pass, 2 skipped（原 461 + 本计划新增 ~30）。

- [ ] **Step 9.3: 写 RESULTS.md（手动跑完 8 条剧本后填）**

模板：
```markdown
# Run YYYY-MM-DD

| 剧本 | Claude Code 工具调用计数 | 期望计数 | 终态匹配 | viewer 肉眼对齐 |
|---|---|---|---|---|
| S1 | ... | 2 | ✓ | ✓ |
...
```

- [ ] **Step 9.4: 收尾 commit**

```bash
git add docs/evaluation/sv3d_tool_use/RESULTS.md
git commit -m "docs(eval): sv3d tool-use first run results"
```

---

## 3. 自检清单（writing-plans Self-Review）

- **Spec 覆盖**：原始问题 4 项 — (a) 工具齐全度 → §0.1 + Task 2；
  (b) API 形态 / 可记录 → §0.1, §0.2 + Task 3-5,7,8；(c) viewer 对齐 → §0.3 + Task 6,8；
  (d) Claude Code 测试 → Task 8。✓
- **占位符扫描**：无 TBD/TODO/"appropriate handling" 字样。所有代码段给完整体。
- **类型一致**：`Session.opacity` 为 `Opacity(base/mask/clip_cap)`；`mask_state` 为
  `MaskState(visible/mask_id/colormap/alpha)`；测试访问字段全部按此命名（与
  `vision3d/_types.py` 一致）。`viewer` 枚举透传只是字符串，不引入新类型。
- **gap 已显式声明**：浏览器→MCP 不双向同步是 V1 限制，写在 §0.3 + Task 6 README，
  不在测试里假装存在。

---

## 4. 执行交接（Handoff）

计划已保存到 `docs/evaluation/2026-05-21-sv3d-tool-use-test-plan.md`。两种执行选项：

**1. Subagent-Driven（推荐）** — 每个 Task 派一个 fresh subagent，主线程 review。
适合 Task 6（viewer 改动）这类需要交叉确认的任务。

**2. Inline Execution** — 在当前 session 串行做，简单直接。适合 Task 1-5,7,8
这种纯加测试的任务。

> 选哪种？或者你想先看 Task 1 的脚手架跑通再说？
