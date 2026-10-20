# Claude Code 剧本 (S1–S8)

本文件汇总 8 条提示词。把整段（中文标题 + 提示词块）粘到 Claude Code 里，
让 Claude Code 调用 `sv3d_*` 系列 MCP 工具，并把 stdout 里的工具调用按
一行一个 JSON 的方式存为 `tool_calls.jsonl`。然后用
`scripts/eval/sv3d_run_scenarios.py` 把这个 JSONL 重放到 REGISTRY，
得到 `final_state.json`，对照各剧本的 **验收准则** 比对终态。

## 前置条件

- MCP 服务正在运行（`python -m sci3d_agent.mcp.server`），并且 Claude Code
  已经把 `sv3d_*` 工具组连接进来。
- 测试库里 `tests/mcp/test_sv3d_tool_use/conftest.py` 描述的 mock 数据可用，
  或者真实数据集（ScanNet test 场景 / 一份脑 MRI）已经按 `viewer/assets.json`
  注册好。剧本里提到的 `qid` 仅作 placeholder，未在仓库里时可替换成本地真实
  qid，或者改走 `asset_path=...` 形式。
- 工具调用的 JSONL 每行形如
  `{"name": "sv3d_camera_orbit", "arguments": {...}}`（Anthropic tool-use 格式）。
  脚本同时兼容 `{"name": ..., "input": ...}`。

## 如何使用

```bash
# 1. 让 Claude Code 跑剧本，把它发出的工具调用记到 JSONL：
#    （手动复制 Claude Code 的 tool-use 块到 evaluation_runs/<run_id>/tool_calls.jsonl）

# 2. 重放，落 trajectory + final_state.json
python scripts/eval/sv3d_run_scenarios.py \
    --tool-calls evaluation_runs/<run_id>/tool_calls.jsonl \
    --out evaluation_runs/<run_id>/

# 3. 生成可视化 URL（把终态相机/不透明度/切片编进 viewer URL）
python scripts/eval/sv3d_emit_viewer_urls.py \
    --final-state evaluation_runs/<run_id>/final_state.json \
    --asset-path  /datasets/<your-asset-path> \
    --viewer gltf
```

打开打印出来的 URL，可以在浏览器里用 `viewer/gltf.html` 等页面肉眼对照
agent 的 `sv3d_render` PNG。

---

## S1 — 从正前方看 ScanQA 场景

**任务**: 打开 ScanQA scene0707，把摄像机切到正前方视角。

**提示词** (粘进 Claude Code):

```
请帮我打开 ScanQA 的 scene0707（qid="scanqa-test_wo_obj-test-scene0707-0"），
然后把摄像机切到 front 视角（fit_bbox=true），最后调用 sv3d_render 给我一张
768x768 的 PNG，并且再用 sv3d_get_state 把当前 session 状态拉回来。
请把你的工具调用一行一个 JSON 输出，便于我落 tool_calls.jsonl。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"qid": "scanqa-test_wo_obj-test-scene0707-0"}}
{"name": "sv3d_camera_set_view", "arguments": {"session_id": "<sid>", "view": "front", "fit_bbox": true}}
{"name": "sv3d_render", "arguments": {"session_id": "<sid>", "width": 768, "height": 768}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `final_state["camera"]["target"]` 等于 mesh bbox 的中心；
`final_state["mode"] == "surface"`；`final_state["camera"]["position"]` 落在
y 负半轴（front 视角约定看向 +y 方向），即 `position[1] < target[1]`。

---

## S2 — 旋转 45° 再放大 2 倍

**任务**: 在 ScanQA 场景里把视角顺时针旋 45°，再放大 2 倍。

**提示词**:

```
打开 qid="scanqa-test_wo_obj-test-scene0707-0"，先调用 sv3d_camera_orbit
让方位角 +45°、仰角 0°，再调用 sv3d_camera_dolly(factor=0.5) 把镜头拉近 2 倍
（factor<1 表示距离缩短）。最后 sv3d_get_state 一下，我要看到 camera.position
的最终位置。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"qid": "scanqa-test_wo_obj-test-scene0707-0"}}
{"name": "sv3d_camera_orbit", "arguments": {"session_id": "<sid>", "d_azimuth_deg": 45.0, "d_elevation_deg": 0.0}}
{"name": "sv3d_camera_dolly", "arguments": {"session_id": "<sid>", "factor": 0.5}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: 设打开时的相机距 `d0 = ||position - target||`，那么终态
`d1 = ||position - target||` 应满足 `0.499*d0 ≤ d1 ≤ 0.501*d0`（dolly factor=0.5）。
`target` 与打开时一致（orbit + dolly 都不动 target）。

---

## S3 — 居中椅子并俯视

**任务**: 让相机看向 mesh 的几何中心，然后绕方位 90°、仰角 +15°。

**提示词**:

```
打开 qid="scanqa-test_wo_obj-test-scene0707-0"，让摄像机看向 bbox 几何中心
（sv3d_camera_look_at(target="bbox_center")），然后旋转：方位 +90°、仰角 +15°。
最后 sv3d_get_state。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"qid": "scanqa-test_wo_obj-test-scene0707-0"}}
{"name": "sv3d_camera_look_at", "arguments": {"session_id": "<sid>", "target": "bbox_center"}}
{"name": "sv3d_camera_orbit", "arguments": {"session_id": "<sid>", "d_azimuth_deg": 90.0, "d_elevation_deg": 15.0}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `final_state["camera"]["target"]` 等于 fixture 里的 bbox 中心
`[0.0, 0.0, 1.5]`（真实数据可能不同，验证逻辑用 fixture）。

---

## S4 — 家具半透明

**任务**: 把 base 层不透明度设成 0.4。

**提示词**:

```
打开 qid="scanqa-test_wo_obj-test-scene0707-0"，调用 sv3d_set_opacity
把 layer="base" 的 alpha 设成 0.4，然后 sv3d_get_state。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"qid": "scanqa-test_wo_obj-test-scene0707-0"}}
{"name": "sv3d_set_opacity", "arguments": {"session_id": "<sid>", "layer": "base", "alpha": 0.4}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `0.39 ≤ final_state["opacity"]["base"] ≤ 0.41`；
`final_state["opacity"]["mask"] == 0.5` 且 `["clip_cap"] == 1.0`（其它层不动；
注意 `Opacity.mask` 默认就是 0.5，不是 1.0）。

---

## S5 — 点云模式

**任务**: 切换成 points 渲染模式。

**提示词**:

```
打开 qid="scanqa-test_wo_obj-test-scene0707-0"，
调用 sv3d_set_render_mode(mode="points", point_size=2.0)，然后 sv3d_get_state。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"qid": "scanqa-test_wo_obj-test-scene0707-0"}}
{"name": "sv3d_set_render_mode", "arguments": {"session_id": "<sid>", "mode": "points", "point_size": 2.0}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `final_state["mode"] == "points"`。

---

## S6 — 冠状切片中线，窗位 40/400

**任务**: 切到冠状 MPR 中线，window 宽度 400，level 40。

注意 `sv3d_set_slice` 的 `window` 参数是**宽度**（width，标量），而存回
`final_state["slice"]["window"]` 是 `[low, high]` 的两元组，以 level 为中心。
width=400 + level=40 ⇒ `window=[-160, 240]`。

**提示词**:

```
打开一份脑 MRI（asset_path="<path-to-nifti>"，或本地能用的 nifti qid），
调用 sv3d_set_render_mode(mode="mpr_coronal")，然后
sv3d_set_slice(axis="coronal", fraction=0.5, window=400, level=40)。
最后 sv3d_get_state 拉回 slice/level/window。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"asset_path": "<path-to-nifti>"}}
{"name": "sv3d_set_render_mode", "arguments": {"session_id": "<sid>", "mode": "mpr_coronal"}}
{"name": "sv3d_set_slice", "arguments": {"session_id": "<sid>", "axis": "coronal", "fraction": 0.5, "window": 400, "level": 40}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `final_state["slice"]["axis"] == "coronal"`；
`final_state["slice"]["fraction"] ≈ 0.5`；
`final_state["slice"]["window"] == [-160.0, 240.0]`；
`final_state["slice"]["level"] ≈ 40.0`；
`final_state["mode"] == "mpr_coronal"`。

---

## S7 — 叠加肿瘤蒙版

**任务**: 在 nifti session 上把 mask_id="1" 的蒙版开起来，jet 配色，半透明 0.5。

**提示词**:

```
打开 nifti（asset_path="<path-to-nifti>" 且同时传
mask_path="<path-to-mask.nii.gz>"，或者用一个已经在 viewer/assets.json
里绑好 mask 的 qid——两者只需选一），调用
sv3d_toggle_mask(visible=true, mask_id="1", colormap="jet", alpha=0.5)，
然后 sv3d_get_state。
```

> 注意：如果只传 `asset_path` 不传 `mask_path`，session 的 `masks` 列表会是
> 空的，`sv3d_toggle_mask(mask_id="1")` 将返回
> `{"error": "session has no masks"}`。

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"asset_path": "<path-to-nifti>", "mask_path": "<path-to-mask>"}}
{"name": "sv3d_toggle_mask", "arguments": {"session_id": "<sid>", "visible": true, "mask_id": "1", "colormap": "jet", "alpha": 0.5}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `final_state["mask_state"] == {"visible": True, "mask_id": "1",
"colormap": "jet", "alpha": 0.5}`（`alpha` 允许 1e-3 浮点误差）。

---

## S8 — 剖切横截面

**任务**: 启用 z=0 平面 +z 法向的剖切。

**提示词**:

```
打开 qid="scanqa-test_wo_obj-test-scene0707-0"，调用
sv3d_set_clip_plane(enabled=true, origin=[0,0,0], normal=[0,0,1], cap=true)，
然后 sv3d_get_state。
```

**期望工具调用序列**:

```jsonl
{"name": "sv3d_open_scene", "arguments": {"qid": "scanqa-test_wo_obj-test-scene0707-0"}}
{"name": "sv3d_set_clip_plane", "arguments": {"session_id": "<sid>", "enabled": true, "origin": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "cap": true}}
{"name": "sv3d_get_state", "arguments": {"session_id": "<sid>"}}
```

**验收准则**: `final_state["clip_plane"]["enabled"] is True`；
`final_state["clip_plane"]["origin"] == [0.0, 0.0, 0.0]`；
`final_state["clip_plane"]["normal"] == [0.0, 0.0, 1.0]`（已单位化）；
`final_state["clip_plane"]["cap"] is True`。
