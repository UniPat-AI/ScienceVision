# WalkingSkeleton_LLM — Active-3D-Vision Verify 测试报告

> 模型 `claude-sonnet-4-6` · mode `mode4_mcp` · 26 任务 · 2026-06-04 · run `sonnet_full26`
> commit `68597ce` · qnaigc endpoint · NVIDIA H20-3e + EGL

## 0. 一句话结论

在配备 ScienceVision MCP 主动 3D vision 工具集（56 tools）后，`claude-sonnet-4-6`
**26/26 任务都主动调用了 MCP 工具**，**18/26 真正渲染出像素**、**16/26 在看到像素后继续
推理**（active-vision 闭环成立）。但**没有一条 GT**（26 条全部 `needs_expert_annotation`），
所以本测试**只验证"过程能力"，不打"对错分"**。过程指标显示：模型不仅能用工具，还会
**绕过工具限制**（打不开 `.urdf` 就去开它引用的 `.obj` 子网格；`sv3d` 拒收 `.las` 就改用
静态 `sv_render_pointcloud_thumbnail`）。主要短板是 `max_iters=16` 偏低，可渲染任务
**过度探索、不主动 `finish`**（7 条 sv3d 原生可渲染任务 0/7 完成）。

> **关于"最终准确率"**：本批次 **N/A —— 26 条全无 ground truth，原理上无法测量答案正确性**。
> 唯一的答案侧代理是 `final_answer_shape_ok`（形状合规，11/26），它 **≠** 准确率。详见 §5.4。

---

## 1. 为什么需要一套"新的" verify 方案

### 1.1 致命前提：没有 ground truth

任务源 `QA-Gen/tasks/WalkingSkeleton_LLM/ScienceVision_WalkingSkeleton_LLM_final_tasks.jsonl`
的 26 条全部满足：

```json
"ground_truth": { "status": "needs_expert_annotation", "value": null }
```

现有 eval 框架 `sci3d_bench` 的 `runner.run_one()` 第一行就是
`if task.ground_truth is None: skip(skip_reason="gt_absent")`（设计 Decision #15）。
**直接喂给现有 runner → 26 条全部被 skip，零信号。**

### 1.2 决策：不改既有 runner，另起 verify runner，打"过程分"而非"对错分"

无 GT 不能判对错，但可以判**过程是否成立**：模型有没有真的打开场景、移动相机/切片、
渲染、看像素、在策略内作答。这正是"主动 3D vision 能力"本身的定义。于是本轮的评分对象
从"答案正确性"换成**可观测的过程 rubric**（见 §4）。

### 1.3 落地前先修了 agent loop 三处真实缺陷

| # | 缺陷（已读源码确认） | 修复 |
|---|---|---|
| (a) | `agent.py` 用 `messages.append({"role":"tool",...})` —— 不是合法 Anthropic message | 改为 assistant `tool_use` block + user `tool_result` block 配 `tool_use_id` |
| (b) | 工具返回的 base64 图像被 `str(result)` 转字符串 → 模型看不到像素 | 提取为 `image` content block 回传；PNG 落盘，trajectory 存路径不存 base64 |
| (c) | `StepRecord` 无 reasoning 字段，`response.text` 在工具分支被丢弃 | `StepRecord` 增 `reasoning`/`tool_use_id`/`images`，每步记录模型思考 |

> **实跑中又发现并修复了第四处**：模型一轮会发**多个 parallel tool_use blocks**，
> 旧 loop 只回一个 `tool_result` → API 直接报
> `Expected toolResult blocks ... for the following Ids`。现已对每个 tool_use 回一个
> tool_result（commit `68597ce`）。这条缺陷 FakeLLM 单测发现不了，只有真模型能暴露。

---

## 2. 环境与配置（可复现）

| 项 | 值 |
|---|---|
| 模型 | `claude-sonnet-4-6`（qnaigc / `QINIUYUN_API` + `QINIUYUN_BASE_URL`） |
| mode | `mode4_mcp`（advertise `finish` + 全部 56 个 MCP 工具） |
| max_iters | 16（**事后判定偏低，见 §6**） |
| max_tokens/turn | 2048 |
| seed | 0 |
| 渲染 | EGL，`PYOPENGL_PLATFORM=egl`，H20-3e GPU，真实 GL（`renderer_used: nifti_slice` / `mesh_surface`） |
| 工具数 | `--list-tools` = 56 |
| 关键 env | `SV_REPO_ROOT=$(pwd)`（否则 MCP catalog 工具读作者硬编码的 `/Users/sue/...` 而 404） |

> 两个**非显然的环境坑**（均已在 runner 内自动处理）：
> 1. 仓库没有 `load_dotenv()`，必须自己读 `.env`；认证变量是 `QINIUYUN_*`/`MINDRA_*` 而非 `ANTHROPIC_*`。
> 2. `sv3d_open_scene` 对相对路径会解析到作者硬编码 root → 必须传**绝对路径**。task builder 已自动转绝对路径。

配置文件：`scripts/eval/walkingskeleton/verify_config.json`。

---

## 3. 测试管线（5 个脚本）

```
build_verify_tasks.py   读 26 条 JSONL → 解析绝对+在盘 asset 路径 → 轻量 VerifyTask（不伪造 GT）
        ↓  verify_tasks.json（26/26 emit，0 skip）
run_verify.py           每条跑修好的 mode4_mcp loop（真模型）→ trajectory + 过程 rubric
        ↓  runs/<ts>/<task_id>/{trajectory.json, verify_metrics.json, final_state.json, images/*.png}
write_manifest.py       run_manifest.json（model/seed/git/deps/per-task 索引）
replay.py               重放 trajectory 里 sv3d 调用序列，校验确定性步骤可复现
view_trajectory.py      把 trajectory + images + metrics 渲染成自包含 HTML（图 inline，浏览器直接看）
```

复现命令：
```bash
python -m scripts.eval.walkingskeleton.build_verify_tasks
SV_REPO_ROOT=$(pwd) PYOPENGL_PLATFORM=egl \
  python -m scripts.eval.walkingskeleton.run_verify --run-id <id>
python -m scripts.eval.walkingskeleton.write_manifest runs/<id>
python -m scripts.eval.walkingskeleton.replay runs/<id>/<task_id>/trajectory.json
# 预览（注意：本机系统 python 是 3.6，必须用 python3.11；脚本纯标准库、按文件路径跑，不要用 -m）
# 方式A：本地有仓库时 → index.html + 每条 trajectory.html
python3.11 scripts/eval/walkingskeleton/view_trajectory.py runs/<id> --index
# 方式B（SSH 远端推荐）：全 run 合成 1 个自包含 HTML，scp 回本地双击即看
python3.11 scripts/eval/walkingskeleton/view_trajectory.py runs/<id> --single
#   → scp <host>:runs/<id>/<id>_all.html . && open <id>_all.html
# 方式C（8080 viewer，最集成）：导出静态 JSON 挑进 viewer/，与 QA Task 页面关联
python3.11 scripts/eval/walkingskeleton/export_verify_json.py runs/<id>
#   → 浏览 http://<host>:8080/viewer/sciencevision_tasks.html?set=WalkingSkeleton_LLM
#     每条 task 卡片带 verify 徽章，详情区展开"操作+每步思考+渲染图"轨迹
```

---

## 4. Verify Rubric（过程指标定义）

| 指标 | 含义 | 判定 |
|---|---|---|
| `used_mcp_tool` | 是否调用 ≥1 个 MCP 工具 | 工具名命中注册表 |
| `opened_scene` | 是否**成功**打开 sv3d 会话 | `sv3d_open_scene` 返回里有 `session_id`（非仅"调用过"） |
| `rendered` | 是否产出像素 | 调用渲染类工具 **或** 该步落了 PNG（ground truth） |
| `inspected_pixels` | 看到像素后是否继续推理 | render 之后存在带 `reasoning` 的步 |
| `tool_policy_compliant` | 是否落在 `allowed_tools`、不碰 `forbidden_tools` | 全部调用 ∈ allowed 且 ∉ forbidden |
| `metadata_only` | 是否仅靠 metadata/catalog 抄近路 | 只用 `sv_inspect/search/list/get/build_*` 且从未 open/render |
| `finished` / `timeout` | 是否主动 `finish` / 撞 max_iters | — |
| `final_answer_shape_ok` | 答案形状是否合 `answer_schema.kind` | 如 `camera_pose` 须含 azimuth/elevation/... |

> 不打"对错分"是有意为之：无 GT 时任何"正确性"分数都是伪造。本 rubric 全部可从落盘的
> trajectory 重算（`compute_metrics` 不需要再调模型）。

---

## 5. 结果

### 5.1 总体（n=26）

| 指标 | 命中 | 备注 |
|---|---|---|
| used_mcp_tool | **26/26** | 全部主动用工具，无人空手作答 |
| opened_scene（成功） | **12/26** | medical 5 + industrial 2 + robotics 5（经 `.obj` 子网格） |
| rendered | **18/26** | 加 pointcloud 3 + photonics 2（经**静态** `sv_render_*`） |
| inspected_pixels | **16/26** | 看到像素后继续推理 |
| tool_policy_compliant | 6/26 | 多数越界一两个工具（如 `sv3d_set_render_mode` 不在 allowed） |
| metadata_only（抄近路） | **0/26** | 没有任何任务靠纯 metadata 作答 |
| finished | 11/26 | — |
| timeout（撞 max_iters） | 12/26 | 主要是可渲染任务过度探索 |
| final_answer_shape_ok | 11/26 | 与 finished 一致 |

### 5.2 按 domain

| domain | n | opened | rendered | pixels | finished |
|---|---|---|---|---|---|
| medical | 5 | 5 | 5 | 5 | 0 |
| industrial | 2 | 2 | 2 | 2 | 0 |
| robotics | 6 | 5 | 6 | 6 | 2 |
| pointcloud | 3 | 0 | 3 | 3 | 3 |
| topological_photonics | 3 | 0 | 2 | 0 | 2 |
| biology | 2 | 0 | 0 | 0 | 1 |
| astronomy | 5 | 0 | 0 | 0 | 3 |

### 5.3 逐条结果表

| task_id | V | domain | mcp | open | render | pixels | policy | finish | steps | imgs | shape |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sv-biology-v1-0001 | V1 | biology | ✓ | · | · | · | · | ✓ | 10 | 0 | ✓ |
| sv-biology-v4-0001 | V4 | biology | ✓ | · | · | · | · | · | 16 | 0 | · |
| sv-medical-v1-0001 | V1 | medical | ✓ | ✓ | ✓ | ✓ | · | · | 18 | 6 | · |
| sv-medical-v2-0001 | V2 | medical | ✓ | ✓ | ✓ | ✓ | · | · | 17 | 6 | · |
| sv-medical-v3-0001 | V3 | medical | ✓ | ✓ | ✓ | ✓ | ✓ | · | 17 | 14 | · |
| sv-medical-v4-0001 | V4 | medical | ✓ | ✓ | ✓ | ✓ | · | · | 17 | 6 | · |
| sv-medical-v6-0001 | V6 | medical | ✓ | ✓ | ✓ | ✓ | ✓ | · | 20 | 7 | · |
| sv-industrial-v1-0001 | V1 | industrial | ✓ | ✓ | ✓ | ✓ | ✓ | · | 17 | 7 | · |
| sv-industrial-v4-0001 | V4 | industrial | ✓ | ✓ | ✓ | ✓ | ✓ | · | 17 | 9 | · |
| sv-robotics-v1-0001 | V1 | robotics | ✓ | ✓ | ✓ | ✓ | · | · | 24 | 9 | · |
| sv-robotics-v2-0001 | V2 | robotics | ✓ | ✓ | ✓ | ✓ | · | ✓ | 20 | 10 | ✓ |
| sv-robotics-v3-0001 | V3 | robotics | ✓ | ✓ | ✓ | ✓ | · | · | 36 | 10 | · |
| sv-robotics-v4-0001 | V4 | robotics | ✓ | ✓ | ✓ | ✓ | ✓ | · | 24 | 7 | · |
| sv-robotics-v5-0001 | V5 | robotics | ✓ | ✓ | ✓ | ✓ | ✓ | · | 18 | 6 | · |
| sv-robotics-v6-0001 | V6 | robotics | ✓ | · | ✓ | ✓ | · | ✓ | 12 | 8 | ✓ |
| sv-pointcloud-v1-0001 | V1 | pointcloud | ✓ | · | ✓ | ✓ | · | ✓ | 10 | 6 | ✓ |
| sv-pointcloud-v3-0001 | V3 | pointcloud | ✓ | · | ✓ | ✓ | · | ✓ | 10 | 6 | ✓ |
| sv-pointcloud-v6-0001 | V6 | pointcloud | ✓ | · | ✓ | ✓ | · | ✓ | 9 | 6 | ✓ |
| sv-topological-photonics-v1-0001 | V1 | photonics | ✓ | · | · | · | · | ✓ | 18 | 0 | ✓ |
| sv-topological-photonics-v3-0001 | V3 | photonics | ✓ | · | ✓ | · | · | · | 21 | 0 | · |
| sv-topological-photonics-v4-0001 | V4 | photonics | ✓ | · | ✓ | · | · | ✓ | 20 | 0 | ✓ |
| sv-astronomy-v1-0001 | V1 | astronomy | ✓ | · | · | · | · | ✓ | 15 | 0 | ✓ |
| sv-astronomy-v2-0001 | V2 | astronomy | ✓ | · | · | · | · | ✓ | 23 | 0 | ✓ |
| sv-astronomy-v3-0001 | V3 | astronomy | ✓ | · | · | · | · | ✓ | 21 | 0 | ✓ |
| sv-astronomy-v4-0001 | V4 | astronomy | ✓ | · | · | · | · | · | 21 | 0 | · |
| sv-astronomy-v5-0001 | V5 | astronomy | ✓ | · | · | · | · | · | 22 | 0 | · |

### 5.4 关于"最终准确性"评估结果（直接回答）

**没有，且本批次原理上无法给出准确率。** 这不是遗漏，是数据前提决定的硬边界：

- WalkingSkeleton_LLM 的 **26 条 `ground_truth.status` 全部为 `needs_expert_annotation`、
  `value` 全部为 `null`**（已逐条核实）。没有任何参考答案可比对。
- `verify_metrics.json` 里**不含任何 correctness / accuracy / score / pass 字段**——
  全部是过程指标。任何"准确率 = X%"的数字在无 GT 下都是凭空捏造，本报告拒绝给出。

**唯一能提供的、与"答案"沾边的代理指标是 `final_answer_shape_ok`**——它只判断
最终答案的**形状**是否符合 `answer_schema.kind`（如 `camera_pose` 是否含
azimuth/elevation/roll/zoom），**不判断数值是否正确**：

| 指标 | 结果 | 这是什么 / 不是什么 |
|---|---|---|
| `finished`（主动交答案） | 11/26 | 交了答案；其余 12 撞 max_iters、未交 |
| `final_answer_shape_ok`（形状合规） | 11/26 | 交答案者形状都合规；**≠ 答案正确** |
| **准确率（答案正确性）** | **N/A** | 无 GT，无法测量 |

> 换句话说：在交了答案的 11 条里，模型给出的答案**格式都对得上**任务要求的 schema，
> 但"它给的 camera_pose / 切片选择 / 计数 是否真的正确"——本批次**回答不了**，
> 必须先有 expert/rendered GT 才能评。

**怎样才能拿到准确率**（见 §9 下一步）：为这批任务补 expert ground truth
（哪怕先补 5–7 条 sv3d 可渲染任务），再用 §4 之外的 v_class scorer
（`v1_camera_delta` / `target_visibility` 等，任务的 `scorer.primary` 已指定）
对 `final_answer` 打带 partial-credit 的正式分。届时才会有真正的"准确性评估结果"。

---

## 6. 关键发现（实跑得来，非静态假设）

1. **模型会"绕过"工具限制**。`sv3d_open_scene` 拒收 `.urdf`，模型遂列目录、找到 URDF 引用的
   `meshes/link_*.obj`，转而打开子网格渲染（robotics 5/6 因此 opened+rendered）。`.las` 被
   sv3d 拒收，模型改用**静态** `sv_render_pointcloud_thumbnail` 出图（pointcloud 3/3 渲染）。
   → "可渲染性"不是 asset 格式的静态属性，而取决于 agent 找到的工具路径。**纯静态格式检查会
   低估**（会算成 7，实际渲染 18）。

2. **没有任何任务抄 metadata 近路**（`metadata_only=0/26`）。即便答案能从文件名/header 推出，
   模型仍倾向真去开场景看图。这是主动 3D vision 工具确实被"用起来"的强信号。

3. **最大短板是不主动收尾**。`sv3d` 原生可渲染的 7 条（medical+industrial）**0/7 finished**，
   全部撞 `max_iters=16`——它们持续切片/转相机渲染、越看越细却不 `finish`。反而非渲染域
   （astronomy/biology）因为没图可看、很快给出形状合规答案（`finished` 集中在这些域）。
   → **`max_iters` 应提到 ~28–32**，或在 system prompt 里加"探索够了就 finish"的收敛提示。

4. **policy 越界普遍但轻微**（compliant 仅 6/26）。典型是多调了一个 `sv3d_set_render_mode`
   / `sv3d_render_canonical_views`，不在该任务 `allowed_tools` 白名单里。属"能力外溢"而非
   "违规走捷径"，rubric 如实记录在 `out_of_policy_calls`。

5. **trajectory 真实记录了"操作 + 每步思考"**（见 §8 样例）。

---

## 6.5 失败溯源（root-cause，读源码核实）

把 §5 的失败逐条溯到源头。结论：**"打不开 3D 文件"既不是参数错、也不是文件损坏或格式无法识别，而是 `sv3d_open_scene` 的 loader 用了一个硬编码格式白名单，只认 7 种后缀。** 它与 viewer 是**两套完全独立的渲染路径**——viewer 能开不代表 MCP 工具能开。

### 6.5.1 根因：MCP loader 的硬编码白名单

`sci3d_agent/mcp/tools/vision3d/session.py:63-69`：

```python
def _detect_kind(path: str) -> Kind:
    p = path.lower()
    if p.endswith(".nii") or p.endswith(".nii.gz"):
        return "nifti"
    if any(p.endswith(s) for s in (".ply", ".glb", ".gltf", ".obj", ".stl")):
        return "mesh"
    raise SVToolError(f"unsupported asset suffix: {path}")   # ← 白名单外的后缀全部在此被拒
```

`sv3d_open_scene` 只支持 **nifti**（`.nii/.nii.gz`）与 **mesh**（`.ply/.glb/.gltf/.obj/.stl`）共 7 种后缀。遇到白名单外的后缀**直接抛 `unsupported asset suffix`**——不是参数问题，是这个 loader 根本没有为这些格式写服务端解析分支。

### 6.5.2 26 条任务格式 × 白名单 × 报告结果（逐条对齐）

把每条任务的真实 asset 后缀（取自 `verify_tasks.json`）跟白名单、§5.2 的 `opened` 数交叉验证：

| domain | 文件格式 | 在白名单? | 报告 `opened` | 一致? |
|---|---|---|---|---|
| medical | `.nii.gz` | ✓ nifti | 5/5 | ✓ |
| industrial | `.glb` | ✓ mesh | 2/2 | ✓ |
| robotics | **`.urdf`** | ✗（但引用 `.obj` 子网格） | 5/6（绕道开 `.obj`） | ✓ |
| pointcloud | **`.las`** | ✗ | **0/3** | ✓ |
| photonics | **`.dat`** | ✗ | **0/3** | ✓ |
| biology | **`.cif`** | ✗ | **0/2** | ✓ |
| astronomy | **`.json`** | ✗ | **0/5** | ✓ |

每一条"开不了场景"都精确对应到一个白名单外的格式。证据链闭合：**所有 `opened=0` 的域，无一例外是格式不在 loader 白名单内**。

### 6.5.3 为什么"viewer 能可视化所有格式"却仍打不开 —— 两条路径不共享 loader

直觉没错，viewer 确实能开所有格式。但 **viewer 与 MCP `sv3d_open_scene` 是两套独立的渲染系统**：

- **viewer**：`viewer/` 下有 **26 个专用前端 HTML renderer**，一种格式一个页面——`urdf.html`(URDF)、`pointcloud.html`(.las / Potree)、`molstar.html`(.cif / Mol\*)、`photonics.html`(.dat)、`niivue.html`(nifti)、`gltf.html`(mesh)…… 每个内嵌对应的前端 JS 库（three.js / Mol\* / deck.gl 等）。
- **MCP `sv3d_open_scene`**：服务端**单个 Python loader**（`_detect_kind` + 对应 inspector），要在 EGL 离屏 GL 里真渲染像素喂给模型。它只实现了 nifti(NiBabel) + mesh(trimesh) 两条服务端解析路径。

> 一句话：**viewer 的"全格式支持"是前端 26 个独立 viewer 拼出来的；MCP 服务端 loader 只移植了其中 2 类。** `.urdf/.las/.cif/.dat/.json` 在 viewer 里各有专属页面，但没人在 `session.py` 里为它们写服务端 loader——所以 viewer 能开、`sv3d_open_scene` 开不了。这不是 bug，是 MCP 端格式覆盖尚未补齐（见 §9 下一步第 5 条）。

### 6.5.4 其余失败原因（与"格式打不开"无关的独立失败）

| 类别 | 影响任务 | 本质 | 是否"打不开文件" |
|---|---|---|---|
| **格式不在 MCP loader 白名单** | astronomy(5)、biology(2)、photonics(3)、pointcloud(3)、urdf(部分) | 服务端只支持 nifti+mesh，`.cif/.las/.dat/.json/.urdf` 无 loader（§6.5.1） | **是** |
| **`max_iters=16` 太低不收尾** | 可渲染的 12 条 timeout | 文件开得太顺，模型持续探索、不主动 `finish`（§6.3）。**与打不开相反——是开得太顺** | 否 |
| **policy 越界** | compliant 仅 6/26 | 多调白名单外渲染工具；疑为 seed 任务 `allowed_tools` 定义偏紧（§9） | 否 |
| **无 GT 测不了对错** | 全部 26 条 | `ground_truth` 全 `needs_expert_annotation`，只能打过程分（§5.4） | 否 |
| **mesh 渲染非确定性** | industrial 等 mesh | 渲染器 run-to-run jitter，限制字节级复现（§7） | 否 |

> 注意一个反差：astronomy/biology 这些**开不了文件**的域，因为没图可看反而很快交出形状合规答案——`finished` 几乎都集中在它们身上；而能开能渲染的 medical/industrial 全部撞 max_iters。**"打不开"和"不收尾"是两类相反的失败，不可混谈。**

---

## 7. 复现性

`run_manifest.json` 固化：model / seed / mode / git commit / 依赖版本 / config 快照 /
每任务产物索引。`replay.py` 重放 trajectory 里的 sv3d 调用序列并校验 render 的
`cache_sha`：

| 类别 | 重放结果 |
|---|---|
| medical（nifti）×5 | **完全确定性**：render `cache_sha` 逐张比对 0 mismatch |
| industrial（mesh）×2 | **render 非字节级确定**：同 mesh 同相机两次渲染 `cache_sha` 都不同 |

> 这是一个诚实的边界：**nifti slice 渲染字节级可复现，mesh surface 渲染不可**（mesh
> 渲染含 run-to-run jitter）。因此复现保证分两级：(i) 模型的工具**选择**全程落盘、可
> 逐步重演（操作+思考）；(ii) 工具**效果**中，nifti 渲染可字节复现、mesh 渲染只能结构
> 复现。模型采样本身不可复现（即便温度 0，跨 provider 也不保证），故 trajectory 是
> "日志"而非"可重新推导"。

---

## 8. Trajectory 样例（medical-v3，操作 + 思考）

```
iter0 sv_inspect_nifti   "I'll systematically explore the hippocampus NIfTI volume to find the optimal..."
iter0 sv3d_open_scene    (并行调用，同一轮)
iter1 sv3d_set_slice     "The volume is 35×51×35 voxels, 1mm isotropic, RAS orientation..."
iter2 sv3d_render        → 落盘 step002_*.png（真实 hippocampus 冠状切片）
iter3 sv3d_set_slice     "Good, I can see the axial view. Let me systematically explore coronal..."
iter4 sv3d_render
iter5 sv3d_render_canonical_views "Let me render multiple coronal slices to find the optimal..."
iter6 sv3d_set_slice     "the canonical views confirm the characteristic hippocampal C-shape..."
...（共 17 步，14 张渲染图）
```

每步的 `reasoning`（模型思考）、`args`（操作）、`result`（含图像落盘路径）都在
`runs/sonnet_full26/sv-medical-v3-0001/trajectory.json`，图像在同目录 `images/`。

---

## 9. 已知局限 + 下一步

**局限**
- **无 GT**：本测试只证"过程能力"，不证"答案正确"。
- **样本小**：每域 1 条 seed（共 26），不足以做统计显著的域间比较。
- **单模型单次**：仅 `claude-sonnet-4-6` 跑了一遍，未做 opus 对照、未做多 seed 方差。
- **policy 白名单偏紧**：seed 任务的 `allowed_tools` 似乎没覆盖模型实际需要的渲染工具
  （如 `sv3d_render_canonical_views`），导致 compliant 偏低——需复核是 rubric 还是任务定义的问题。
- **`max_iters=16` 偏低**：直接压低了 `finished` 与可渲染任务的完成度。

**下一步**
1. 提 `max_iters` 到 ~30 重跑，看可渲染任务能否在更长预算内主动 `finish`。
2. 跑 `claude-opus-4-7` 对照（config 已验证可用），比较两模型的闭环率/越界率。
3. 补 expert GT（哪怕 5–7 条 sv3d 可渲染任务），把过程分升级为带 partial-credit 的正式打分。
4. 复核 seed 任务的 `allowed_tools`，把合理的渲染工具补进白名单后重判 policy。
5. 对 `.urdf`/`.las`/`.cif`/`.dat`/`.json` 这些 sv3d 不支持的格式（根因见 §6.5：`session.py::_detect_kind`
   硬编码白名单只认 nifti+mesh），决定是把 viewer 已有的对应 renderer 移植成服务端 loader，还是在任务里
   改指向可渲染子资产（如 URDF→`.obj`）。

---

## 附：产物清单

```
scripts/eval/walkingskeleton/
  build_verify_tasks.py  verify_tasks.json   # 26 任务（绝对路径，无伪造 GT）
  run_verify.py          verify_config.json  # runner + 过程 rubric
  replay.py  write_manifest.py               # 复现工具
runs/sonnet_full26/                          # gitignore，不入库
  run_manifest.json  verify_summary.json
  <task_id>/{trajectory.json, verify_metrics.json, final_state.json, replay_report.json, images/*.png}
docs/evaluation/WalkingSkeleton_LLM/verify_report.md   # 本报告
tests/sci3d/test_agent_loop_multiturn.py     # loop 修复回归测试（含 parallel tool use）
```
