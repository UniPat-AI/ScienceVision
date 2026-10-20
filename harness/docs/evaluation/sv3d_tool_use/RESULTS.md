# sv3d Tool-Use 评测 — 首次运行结果

> **范围**: Tasks 1–8 自动化测试 + S1–S8 8 条剧本的 driver 自洽实跑
> （`scripts/eval/run_all_scenarios.py`，synthetic agent）。真人 Claude
> Code 实跑（§3.3）独立排程，server/spec 已由 driver 锁紧，不阻塞合并。
> **执行人**: `worktree-test+sv3d-tool-use-eval` 会话
> **日期**: 2026-05-21

---

## 1. 工具盘点（contract 复核）

```
$ python -m sci3d_agent.mcp.server --list-tools | jq '.tools | length'
53

$ python - <<'EOF'
from sci3d_agent.mcp import server
from sci3d_agent.mcp.registry import REGISTRY
print(len([n for n in REGISTRY if n.startswith("sv3d_")]))
EOF
15
```

15 个 `sv3d_*` 工具全部注册到位（contract 测试 `test_all_15_sv3d_tools_registered` 守住）。

| 工具 | 类别 | 参数关键字 |
|---|---|---|
| `sv3d_open_scene` | session | `qid` \| `asset_path` |
| `sv3d_close_scene` | session | `session_id` |
| `sv3d_get_state` | session | `session_id` |
| `sv3d_camera_orbit` | camera | `d_azimuth_deg`, `d_elevation_deg`, `d_roll_deg?` |
| `sv3d_camera_dolly` | camera | `factor` ∈ [0.05, 20] |
| `sv3d_camera_pan` | camera | `dx_frac`, `dy_frac` ∈ [-2, 2] |
| `sv3d_camera_set_view` | camera | `view` ∈ {front,back,left,right,top,bottom,iso,reset} |
| `sv3d_camera_look_at` | camera | `target` (xyz \| "bbox_center") |
| `sv3d_set_render_mode` | state | `mode`, `point_size?` |
| `sv3d_set_opacity` | state | `layer` ∈ {base,mask,clip_cap}, `alpha` |
| `sv3d_toggle_mask` | state | `visible?`, `mask_id?`, `colormap?`, `alpha?` |
| `sv3d_set_slice` | state | `axis`, `index` \| `fraction`, `window`, `level` |
| `sv3d_set_clip_plane` | state | `enabled`, `origin?`, `normal?`, `cap?` |
| `sv3d_render` | render | `width`, `height`, `format?` |
| `sv3d_render_canonical_views` | render | `width`, `height`, `views?` |

---

## 2. 自动化测试结果

### 2.1 sv3d 测试套（Step 9.1）

```
$ pytest tests/mcp/test_sv3d_tool_use/ tests/mcp/test_urls.py -v
…
============================== 82 passed in 0.08s ==============================
```

| 文件 | 用例数 | 状态 |
|---|---|---|
| `test_contract.py` | 9 | ✅ |
| `test_orbit_dolly_pan.py` | 4 | ✅ |
| `test_set_view_look_at.py` | 10 | ✅ |
| `test_opacity_clip.py` | 6 | ✅ |
| `test_mode_slice_mask.py` | 5 | ✅ |
| `test_trajectory_replay.py` | 6 | ✅ |
| `test_urls.py`（含 Task 6 新增 2 例） | 42 | ✅ |
| **合计** | **82** | **0 失败** |

预期值 30+，实际 82，达标。

### 2.2 MCP 主套回归（Step 9.2）

```
$ pytest tests/mcp/ -q
…
504 passed, 1 skipped in 2.41s
```

| 阶段 | pass | skipped |
|---|---|---|
| Task 0 基线（CLAUDE.md 记录） | 461 | 2 |
| Task 6 完成后 | 489 | 1 |
| Task 7 完成后 | 498 | 1 |
| **Task 8 完成后（本次）** | **504** | **1** |

净增 **43 个用例**（Tasks 2–8 累计 + Task 6 的 URL 覆盖），无回归、无新增 skipped。
1 个 skipped 是 Task 0 起就存在的、与本计划无关的 EGL 渲染条件跳过。

### 2.3 自动化覆盖的能力维度

- 静态契约（schema、必填字段、枚举完整性）
- 相机三件套不变量（orbit 保 target+distance、dolly 距离乘法律、pan 同步移动 target+pos）
- 标准视角 + look_at 语义
- 不透明度三层 + clip plane 启停 / 法向单位化
- mesh→{surface,points} 切换、mesh 拒绝 volume 模式
- nifti MPR axial/sagittal 切换、`fraction`/`window`/`level` 写读
- mask partial-update 不擦掉未触及字段
- 工具轨迹的**确定性回放**（同序列两次执行得到相同相机）
- error envelope 不中断回放（dolly factor=50 → `{"error": …}` 进入 trajectory 而非抛异常）
- 终态 → viewer URL 编码（mesh + gltf 走 cam/opacity；nifti slice 用 width 语义；niivue 自动剥掉 gltf-only 参数）

---

## 3. 剧本实跑（S1–S8）

剧本提示词与期望工具调用见
[`scenarios.md`](./scenarios.md)。运行手册见 [`README.md`](./README.md)。

### 3.1 自动 driver 自洽测试（synthetic agent，已通过）

跑法：

```bash
python scripts/eval/run_all_scenarios.py
# 产物落到 evaluation_runs/auto-2026-05-21/<S>/，含
#   tool_calls.jsonl / trajectory.jsonl / final_state.json / viewer_url.txt
# 顶层 REPORT.md 为每条剧本一行的汇总。
```

driver 用 `install_fake` 注入 mesh / nifti mock（与 conftest 中
`sv_inspect_mesh` / `sv_inspect_nifti_mask_pair` 的契约一致），按
scenarios.md 里**写死的工具调用序列**依次调 REGISTRY 并捕获终态。它证明
**给定正确序列、MCP server + emitter 链路完整且符合 scenarios.md 的验收
准则**；但**不替代真人把 scenarios.md 粘到 Claude Code 让 agent 自己产出
工具调用**——后者考的是 Claude Code 的 tool-use 能力，driver 考的是
server / spec 一致性。

**结果**（`evaluation_runs/auto-2026-05-21/REPORT.md`，2026-05-21 实跑）：

| 剧本 | 描述 | 调用 (得/期) | error envs | 终态验收 | 备注 |
|---|---|---|---|---|---|
| S1 | 打开 ScanQA scene0707，从正前方看 | 4 / 4 | 0 | ✅ | `target=[0,0,1.5]` `mode=surface` `pos[1]=-20.24 < 0` |
| S2 | 旋转 45° + 放大 2× | 4 / 4 | 0 | ✅ | `d0=21.247 → d1=10.624`，ratio≈0.500，target 不变 |
| S3 | look_at(bbox_center) + 俯视 | 4 / 4 | 0 | ✅ | `target=[0,0,1.5]`（fixture bbox center） |
| S4 | 半透明 base=0.4 | 3 / 3 | 0 | ✅ | `opacity={'base':0.4,'mask':0.5,'clip_cap':1.0}` |
| S5 | 点云模式 | 3 / 3 | 0 | ✅ | `mode=points` |
| S6 | MRI 冠状中线 + W400/L40 | 4 / 4 | 0 | ✅ | `axis=coronal frac=0.5 window=[-160,240] level=40 mode=mpr_coronal` |
| S7 | MRI 叠加 mask (jet, α=0.5) | 3 / 3 | 0 | ✅ | `mask_state={'visible':True,'mask_id':'1','colormap':'jet','alpha':0.5}` |
| S8 | z=0 剖切横截面 | 3 / 3 | 0 | ✅ | `clip_plane={'enabled':True,'origin':[0,0,0],'normal':[0,0,1],'cap':True}` |
| **合计** | | **27 / 27** | **0** | **8 / 8** | |

viewer URL 也已逐条生成；mesh 剧本走 `gltf.html?cam=&opacity=`，nifti 走
`niivue.html?file=`（emitter fallback 自动剥掉 gltf-only 参数）。完整 URL
见 `evaluation_runs/auto-2026-05-21/REPORT.md`。

### 3.2 修复的两个 spec bug（driver 验证时发现）

driver 第一遍跑 S4 / S7 时分别命中：

1. **S4**: 原验收准则写 `final_state["opacity"]["mask"] == 1.0`，但
   `Opacity` dataclass 的默认 `mask=0.5`（参见
   `vision3d/_types.py::Opacity`），所以 `set_opacity(layer="base")` 后
   `mask` 应仍是 0.5。**已修**：`scenarios.md` S4 验收条件改为
   `mask == 0.5`。
2. **S7**: 原 `sv3d_open_scene` 仅传 `asset_path`，session 的 `masks`
   列表为空，`sv3d_toggle_mask(mask_id="1")` 直接返回
   `{"error": "session has no masks"}`。**已修**：`scenarios.md` S7
   要求同时传 `mask_path`，并在期望工具调用里把 `mask_path` 字段补齐；
   补充了 "如果只传 asset_path 不传 mask_path 会怎么样" 的注意框。

### 3.3 真人 Claude Code 实跑（pending — 不阻塞合并）

真人把 `scenarios.md` 的 S1–S8 提示词粘到 Claude Code、把它发出的
tool-use 块抄进 `evaluation_runs/<run_id>/tool_calls.jsonl` 后跑：

```bash
python scripts/eval/sv3d_run_scenarios.py \
    --tool-calls evaluation_runs/<run_id>/tool_calls.jsonl \
    --out        evaluation_runs/<run_id>/

python scripts/eval/sv3d_emit_viewer_urls.py \
    --final-state evaluation_runs/<run_id>/final_state.json \
    --asset-path  <asset> --viewer gltf   # 或 niivue
```

考的是 agent **是否会自己产出** S1–S8 的工具调用序列。driver 通过保证了
"序列对了，server 一定能复现终态"；这一步专门考 Claude Code 的工具决策。

> 视觉对齐误差容忍：因 trackball vs pyrender FOV/clip 计算差异，相机角度 ≤10° 视为对齐
> （计划文档 §0.3 已说明）。

---

## 4. 已知 V1 限制

1. **不双向同步**: 浏览器里手动旋转 viewer **不会**回写 Session。本评测只覆盖
   `MCP → viewer` 单向 single-shot 对齐。
2. **URL state 参数只接进 gltf**: Task 6 仅给 `viewer/gltf.html` 加了
   `cam/opacity/slice` 支持；niivue/molstar/…等其它 viewer 调用 emitter 时会自动剥掉
   这些参数并保留基础 `file=`（emitter 通过 fallback 路径处理）。如要给 niivue 也加
   状态注入，需要扩展 `_VIEWER_CONTRACT["niivue"]["accepts"]` 并改 niivue.html。
3. **S6 真实 nifti 资产**: 剧本里 `<path-to-nifti>` 是占位符，请替换为本地能用的
   `.nii.gz`（或对应 qid）。
4. **S7 mask 注入**: 需要 nifti session 在 `open_scene` 阶段已经把 `masks_info`
   绑好（`sv_inspect_nifti_mask_pair` envelope 提供）。本评测的 fixture 模拟了这条路径；
   真跑要确保 viewer/assets.json 里目标资产带可用的 mask_url_viewer。
5. **S6 `window` 输入 vs 存储语义**: MCP 入参 `window` 是宽度（scalar），存回
   `final_state["slice"]["window"]` 是 `[low, high]`。emitter 写回 URL 时**重新取宽度**
   （`high - low`），保持与 MCP 输入一致。

---

## 5. 下一步建议

- §3.1 已完成（driver 自洽 8/8）；§3.3 真人 Claude Code 实跑独立排程，
  不阻塞合并——server / spec 已被 driver 锁紧。
- 把本测试目录写进 CI：`pytest tests/mcp/test_sv3d_tool_use/` +
  `python scripts/eval/run_all_scenarios.py`（两者都不依赖网络/EGL）。
- 双向同步（浏览器 → MCP）若有需求，单独立项；目前不属于本 PR 范围。

---

## 6. 文件清单

本计划新增 / 改动的产物：

```
docs/evaluation/sv3d_tool_use/
  README.md               # Task 1
  scenarios.md            # Task 1 → Task 8 填充 → Task 9 修 S4/S7
  RESULTS.md              # 本文件（Task 9 + 真跑回填）
tests/mcp/test_sv3d_tool_use/
  __init__.py             # Task 1
  conftest.py             # Task 1（Task 7 修 nifti envelope）
  test_contract.py        # Task 2
  test_orbit_dolly_pan.py # Task 3
  test_set_view_look_at.py# Task 4
  test_opacity_clip.py    # Task 5
  test_mode_slice_mask.py # Task 7
  test_trajectory_replay.py # Task 8
scripts/eval/
  sv3d_run_scenarios.py   # Task 8（重放外部 JSONL）
  sv3d_emit_viewer_urls.py# Task 8
  run_all_scenarios.py    # Task 9（synthetic agent 自洽 driver，落 §3.1 报告）
viewer/shared/cam_url.js  # Task 6
viewer/gltf.html          # Task 6（apply cam_url）
sci3d_agent/mcp/tools/urls.py # Task 6（接 cam/opacity/slice 到 gltf accepts）
tests/mcp/test_urls.py    # Task 6 追加 2 例
.gitignore                # Task 1（gitignore evaluation_runs/）
```
