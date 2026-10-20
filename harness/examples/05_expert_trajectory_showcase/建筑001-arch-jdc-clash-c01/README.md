# arch-jdc-clash-c01

> 建筑 · 综合公用管线碰撞检测 · 查看器 glTF 三维模型查看器 · 专家 architecture1 · 答案质检通过 2026-09-03T13:31:59Z

本文件夹是**一道已通过答案质检的题目**的完整宣传素材（含 3D 素材文件）。所有内容来自生产库封存的有效轨迹：只包含专家最终提交、且被质检通过的那一条操作血统，已撤销 / 重做掉的分支不在其中。

## 文件夹内容

| 文件 | 作用 |
|---|---|
| `题目与轨迹.json` | 机器可读的完整数据：题面、答案、推理、逐步 MCP 操作轨迹、每步截图引用、元数据。字段说明见下文。 |
| `assets/` | 题目引用的 3D 素材文件本身（`.cif` / `.xyz` / `.glb` / `.nii.gz`），文件名与 `task_context.assets[].name` 一致，sha256 与封存清单一致。 |
| `screenshots/操作NNN.png` | 每一步操作后的画面截图，`NNN` = `operation_seq`（轨迹里的真实步号），可直接放进 PPT。 |
| `index.html` | 双击即可看的静态翻页页（题目 / 推理 / 答案 + 逐步截图 + 每步工具调用与专家说明）。依赖同目录的 `data.js`、`lib/`、`screenshots/`。 |
| `data.js` | 与 `题目与轨迹.json` 内容相同，仅包成 `window.SV_CASE = …` 供 `index.html` 加载。 |
| `lib/` | 从平台复制来的两份前端文件：`i18n.js`（工具名 / 参数中英文词表）和 `qc-action-label.js`（把一次工具调用格式化成一句中文，与质检页展示一致）。 |

## 一眼概览

| 项 | 值 |
|---|---|
| 题号 `task_id` | `arch-jdc-clash-c01` |
| 领域 / 子领域 | 建筑 / 综合公用管线碰撞检测 |
| 查看器 | glTF 三维模型查看器（`gltf`） |
| 3D 素材（随包） | A_JDC_C_01.glb |
| 操作步数 / 截图数 | 18 / 18 |
| 专家用时（首步到末步） | 228 分 29 秒 |
| 最终答案 | `约为0.2m` |

## 题目打开时的原始信息

**第 0 帧截图：平台没有记录，已用同一引擎重建** → `screenshots/操作000-初始状态-重建.png`。重建方法：同一查看器 + 同一素材 + 同一 URL 参数在本地重新渲染，画幅取专家第一张截图的尺寸，相机按 `trajectory.initial_state.camera` 对齐（校验结果 `camera_match`），再用平台截图 API 捕获。它是重建图不是原始记录，对外使用时请标注。关键帧从第 1 步操作后才开始拍；题目刚打开时查看器的确切状态记录在 `trajectory.initial_state`，每次回放都从它 `viewer_reset` 开始。截图目录里最早的一张（步号 3）就是初始画面经过第一步操作之后的样子。

原始标题：**—** / —
视觉能力类别：—（— · —）
题目质检：adopt_question_only，题面是否被质检修改：是（以 `question.zh/en` 为定稿）

初始查看器状态（`trajectory.initial_state`）：

```json
{
  "annotation_count": 0,
  "camera": {
    "azimuth_deg": 45,
    "elevation_deg": 19.471221,
    "pan_right": 0,
    "pan_up": 0,
    "position": [
      16.297058,
      10.494529,
      16.716979
    ],
    "target": [
      0,
      2.346,
      0.41992
    ],
    "up": [
      0,
      1,
      0
    ],
    "zoom": 1
  },
  "cutaway": {
    "axis": "y",
    "max_m": 7.82,
    "min_m": 0,
    "value_m": 7.82
  },
  "layers": [
    {
      "label": "B场地|Site",
      "name": "B场地|Site",
      "opacity": 1,
      "visible": true
    },
    {
      "label": "C结构|Structure",
      "name": "C结构|Structure",
      "opacity": 1,
      "visible": true
    },
    {
      "label": "D楼板墙|Floor&Wall",
      "name": "D楼板墙|Floor&Wall",
      "opacity": 1,
      "visible": true
    },
    {
      "label": "E幕墙|Façade",
      "name": "E幕墙|Façade",
      "opacity": 1,
      "visible": true
    },
    {
      "label": "G室内|Interior",
      "name": "G室内|Interior",
      "opacity": 1,
      "visible": true
    }
  ],
  "measurement_count": 0,
  "view": "iso",
  "viewer": "gltf",
  "zoom": 1
}
```

## 质检时间线（UTC）

| 时间 | 事件 | 操作者 |
|---|---|---|
| 2026-09-02T07:40:20Z | `assignment.assigned` | admin（admin） |
| 2026-09-03T08:18:59Z | `assignment.question_review_submitted` | architecture1（expert） |
| 2026-09-03T09:44:13Z | `assignment.question_review_approved` | architecture1（admin） |
| 2026-09-03T12:07:06Z | `assignment.submitted` | architecture1（expert） |
| 2026-09-03T12:07:06Z | `assignment.pending_review` | task-manager（system） |
| 2026-09-03T13:31:59Z | `assignment.approved` | architecture1（admin） |

## 操作轨迹速览

| 第几步 | 步号 `operation_seq`（= 截图编号） | 工具 | 中文名 | 参数（replay_input，截断） | 专家说明（expert_note） |
|---|---|---|---|---|---|
| 1 | 3 | `sv3d_toggle_layer` | 显示或隐藏图层 | `{"layer": "B场地|Site", "visible": false}` | （专家未写） |
| 2 | 4 | `sv3d_toggle_layer` | 显示或隐藏图层 | `{"layer": "E幕墙|Façade", "visible": false}` | （专家未写） |
| 3 | 5 | `sv3d_toggle_layer` | 显示或隐藏图层 | `{"layer": "D楼板墙|Floor&Wall", "visible": false}` | （专家未写） |
| 4 | 6 | `viewer_zoom` | 缩放 | `{"zoom": 0.917004}` | （专家未写） |
| 5 | 7 | `sv3d_orbit` | 环绕旋转相机 | `{"azimuth_deg": -90, "elevation_deg": 4}` | 调整到侧视图 |
| 6 | 8 | `sv3d_measure_distance` | 测量网格点距离 | `{"end": [-3.04825, 5.6581, -0.068036], "start": [-4.6, 5.721232, -0.035645]}` | 测量管线与梁之间的距离 |
| 7 | 10 | `sv3d_orbit` | 环绕旋转相机 | `{"azimuth_deg": -83, "elevation_deg": -27}` | （专家未写） |
| 8 | 11 | `sv3d_orbit` | 环绕旋转相机 | `{"azimuth_deg": -67, "elevation_deg": -11}` | （专家未写） |
| 9 | 12 | `viewer_zoom` | 缩放 | `{"zoom": 1.834008}` | （专家未写） |
| 10 | 13 | `sv3d_orbit` | 环绕旋转相机 | `{"azimuth_deg": -67, "elevation_deg": 8}` | （专家未写） |
| 11 | 14 | `sv3d_measure_distance` | 测量网格点距离 | `{"end": [-5.588997, 5.506059, 4.85], "start": [-4.6, 5.723079, 4.609063]}` | （专家未写） |
| 12 | 15 | `sv3d_clear_measurements` | 清除测量 | `{}` | （专家未写） |
| 13 | 16 | `sv3d_measure_distance` | 测量网格点距离 | `{"end": [-3.049751, 5.640673, 1.048263], "start": [-4.6, 5.74461, 1.794794]}` | （专家未写） |
| 14 | 17 | `sv3d_clear_measurements` | 清除测量 | `{}` | （专家未写） |
| 15 | 18 | `sv3d_measure_distance` | 测量网格点距离 | `{"end": [-3.030203, 5.684801, 0.663049], "start": [-4.6, 5.742558, 1.450452]}` | （专家未写） |
| 16 | 19 | `sv3d_clear_measurements` | 清除测量 | `{}` | （专家未写） |
| 17 | 20 | `sv3d_orbit` | 环绕旋转相机 | `{"azimuth_deg": -52, "elevation_deg": 6}` | （专家未写） |
| 18 | 21 | `sv3d_measure_distance` | 测量网格点距离 | `{"end": [-5.555161, 5.515004, 4.85], "start": [-4.6, 5.780469, 4.051277]}` | （专家未写） |

## 本题用到的 MCP 工具

| 工具名 | 中文名 | 说明 |
|---|---|---|
| `sv3d_toggle_layer` | 显示或隐藏图层 | Show or hide a named glTF scene-graph layer. |
| `viewer_zoom` | 缩放 | Set the glTF zoom to an absolute multiple of the fitted view; 1 is fitted, 2 is twice as close. |
| `sv3d_orbit` | 环绕旋转相机 | Point the glTF camera at an absolute azimuth/elevation around its current target. |
| `sv3d_measure_distance` | 测量网格点距离 | Measure the 3D distance between two world points on the glTF mesh. Reports glTF metres and Rhino millimetres. |
| `sv3d_clear_measurements` | 清除测量 | Clear glTF measurement overlays and results. |

## 随包的 3D 素材

| 文件 | 角色 | 字节 | sha256 |
|---|---|---|---|
| `assets/A_JDC_C_01.glb` | primary | 636780 | `6e7faed592fd68bf…` |

## `题目与轨迹.json` 字段说明

顶层是一个对象。前五段（`assignment_id` … `meta`）与平台管理端「已通过题目 → 导出训练语料」产出的 JSON **完全同构**（schema `sciencevision.approved_export.v1`），后三段（`task_context` / `tool_glossary` / `showcase`）是为宣传素材额外补充的。

### 顶层标识

| 字段 | 含义 |
|---|---|
| `task_id` | 题号，也是本文件夹名。形如 `sv-<领域缩写>-<子领域>-<序号>`。 |
| `assignment_id` | 这道题分派给这位专家的「作业」ID（`asgn_…`）。一题可能被多位专家做过，只有通过质检的那份被导出。 |
| `batch_id` | 题目所属导入批次。 |

### `question` — 题面

| 字段 | 含义 |
|---|---|
| `zh` / `en` | 中英文题面（Markdown，内含「答案格式」代码块）。 |
| `source` | `effective_question` = 题目质检定稿后的题面；`task_surface` = 原始导入题面（未经题目质检修改）。 |

### `answer` — 专家提交的结果

| 字段 | 含义 |
|---|---|
| `answer` | 专家最终答案，通常是一段符合题面「答案格式」的 JSON 字符串。 |
| `reasoning` | 专家在提交时写下的整体推理过程（CoT，自然语言）。 |
| `validation.status` | 专家对**题目本身**的判定：`valid` 合理 / `uncertain` 存疑 / `invalid` 有问题。 |
| `validation.feedback` | 专家对题目的文字反馈（可能是「无」）。 |

### `trajectory` — 操作轨迹（核心）

来自专家提交时**封存**的轨迹清单，服务端已校验：单链血统、步号单调、每步成功、截图哈希一致。

| 字段 | 含义 |
|---|---|
| `draft_id` / `submission_id` / `submitted_at` | 轨迹草稿 ID、提交 ID、提交时间（UTC）。 |
| `sealed_manifest_sha256` | 封存清单的哈希，用于防篡改校验。 |
| `registry_sha256` | 当时生效的 MCP 工具注册表版本哈希。 |
| `initial_state` | 专家开始操作前查看器的初始状态（相机、组分显示、结构列表等，结构随查看器类型不同）。 |
| `operation_count` / `keyframe_count` | 有效操作步数 / 关键帧（截图）数。正常情况两者相等。 |
| `operations[]` | **按顺序**的每一步操作，见下表。 |
| `final_result` | 与 `answer` 同源的提交结果副本（`answer` / `reasoning` / `task_validation`），封存在轨迹里做交叉校验。 |
| `screenshot_gaps[]` | 没有截图的步号列表。空数组表示每步都有图。 |

#### `trajectory.operations[i]` — 一步操作

| 字段 | 含义 |
|---|---|
| `operation_seq` | 步号。截图文件名 `操作NNN.png` 的 `NNN` 就是它。**注意步号可能不从 1 开始、也可能跳号**（例如 3, 4, 5, 6, 7, 8, 10 …）：专家撤销过的步骤不在有效血统里，但已用掉的步号不会回收。讲「第几步」请用它在 `operations[]` 里的位置（index + 1），引用截图请用步号。 |
| `operation_id` / `parent_operation_id` | 本步 ID 与上一步 ID（链式血统）。 |
| `function.name` / `function.version` | 调用的 MCP 工具名与版本。工具中文名和说明见 `tool_glossary`。 |
| `input` | 专家在面板上点出来的原始参数。 |
| `replay_input` | **回放用的规范化参数**。做展示、讲解时以它为准（例如测距会带上原子坐标与身份）。 |
| `state_before` / `state_after` | 该步执行前 / 后查看器的完整状态（相机角度、缩放、平移、各组分是否可见、测量数量等）。可以据此讲「这一步把方位角从 0° 转到 −48°」。 |
| `result` | 工具返回值。若有 `result.expert_summary`，是给人看的一句中文结论（如「距离：2.067 Å（A:357 GLY / O — A:502 ICS / S3A）」）。 |
| `timing.started_at` / `timing.completed_at` | 该步开始 / 完成时间（UTC）。相邻步之间的时间差就是专家的思考 / 观察时间。 |
| `keyframe.expert_note` | **专家为这一步写的说明**（逐步 CoT），宣传文案的主要来源。 |
| `keyframe.screenshot_file` | 本步截图在本文件夹内的相对路径，如 `screenshots/操作003.png`。 |
| `keyframe.screenshot.{width,height,byte_size,sha256,captured_at}` | 截图尺寸、字节数、哈希、拍摄时间。`object_key` / `bucket` 是平台内部存储坐标，展示时忽略。 |

### `meta` — 质检与归属

| 字段 | 含义 |
|---|---|
| `expert_id` | 标注专家账号（如 `chemistry1`）。已脱敏：不含专家姓名。 |
| `approved_at` | 答案质检通过时间（UTC）。 |
| `ever_returned` | 是否曾被质检退回过再重做（`false` = 一次通过）。 |
| `domain` | 领域英文 slug（`biology` / `chemistry` / `medical` / `architecture` / `mechanical`）。中文见 `task_context.domain_label`。 |
| `sub_domain_slug` / `sub_domain_label` / `sub_domain_source` | 子领域 slug、中文名、来源（题目质检定稿或批次原始值）。 |

### `task_context` — 展示补充：题目上下文

| 字段 | 含义 |
|---|---|
| `viewer` / `viewer_label` / `engine` | 查看器类型（`molstar` 分子 / `niivue` 医学影像 / `gltf` 三维模型 / `occt` CAD …）、中文名、底层引擎。 |
| `domain_label` | 领域中文名。 |
| `answer_shape` | 题目要求的答案结构（字段名列表）。 |
| `assets[]` | 题目引用的 3D 素材元数据：文件名、平台内路径、角色（`primary` 主模型 / `mask` 分割掩膜 …）、sha256、字节数。`included: false` 表示文件本身**没有**随包导出。 |

### `original_task` — 展示补充：题目打开时的原始信息

| 字段 | 含义 |
|---|---|
| `title_en` / `title_zh` | 题目标题（原始英文 / 平台机翻中文）。 |
| `question_en_original` / `question_zh_original` | **题目质检之前**的原始题面。定稿题面在顶层 `question`；两者不同说明质检时改过题。 |
| `question_id` | 出题系统里的题目 ID（通常与 `task_id` 相同）。 |
| `vision_class` / `vision_class_code` / `vision_class_group` | 出题时标注的视觉能力类别（如「visibility and occlusion / V2 / hard moat」）。可能为空。 |
| `source_domain` / `source_sub_domain` | 出题系统原始领域 / 子领域 slug。 |
| `forbidden_strategies` | 出题时声明的禁止解法（如直接调用碰撞评分 API），说明这题必须靠观察操作完成。 |
| `trace_requirements` | 出题时对轨迹的要求（最多步数、必须截图等）。 |
| `provenance` | 数据集版本、难度、生成器。 |
| `initial_view_note` | 说明：平台未记录第 0 帧截图，初始画面以 `trajectory.initial_state` 为准。 |

### `question_review` — 展示补充：题目质检记录

`decision`（`keep_original` 保持原题 / `adopt_edit` 采纳专家修改 …）、`submitted_at` / `decided_at` / `decided_by`、专家对题目的反馈 `expert_validation_feedback`、专家建议的修改 `expert_question_edit_zh/en`、`question_changed`（定稿题面是否与原始题面不同）。无题目质检记录时为 `null`。

### `timeline[]` — 展示补充：这份作业的全过程时间线

按时间排序的审计事件：`assignment.assigned`（分派）→ `assignment.question_review_submitted` / `question_review_approved`（题目质检）→ `assignment.submitted`（专家提交）→ `assignment.pending_review`（进入答案质检队列）→ `assignment.approved`（通过）。每条含 `at`（UTC）、`event`、`actor`、`actor_role`。

### `tool_glossary` — 展示补充：本题用到的工具词典

以工具名为键：`label_zh` 中文名、`help_zh` 中文说明（平台专家页的帮助文案）、`description_en` 注册表英文描述、`input_schema` 参数 JSON Schema。

### `showcase` — 展示补充：导出信息

| 字段 | 含义 |
|---|---|
| `schema` | `sciencevision.showcase_task.v1`。 |
| `exported_at` / `source` | 导出时间与数据来源说明。 |
| `screenshots[]` | 每张截图的 `operation_seq`、相对路径、宽高、字节数、sha256。 |
| `duration_seconds` | 首步开始到末步完成的秒数，即专家实际操作用时。 |
| `tools_used` | 各工具的调用次数。 |
| `arg_glossary_zh` | 本题出现过的参数名的中文释义（如 `azimuth_deg` → 方位角）。 |
| `initial_frame` | **重建的第 0 帧**（仅当跑过重建脚本时存在）。`file` 相对路径；`kind` 固定 `reconstruction`；`viewer_url_params` 重建时用的查看器参数；`canvas` 画幅及是否与专家第一帧尺寸一致；`alignment_applied[]` 为对齐相机而执行的工具调用（通常一次 `viewer_zoom`）；`state_check.camera_match` 相机是否与记录一致、`other_differences` 其他字段差异（adapter 版本差异，非画面差异）、`verdict`。对外使用请标注「重建」。 |

## 使用建议

1. 先读 `question.zh` 和 `answer.answer` 建立「问什么 / 答什么」。
2. 用 `trajectory.operations[]` 讲过程：每步取 `function.name`（配 `tool_glossary` 的中文名）+ `keyframe.expert_note` + `keyframe.screenshot_file`，就是一页「第 N 步」的图文。
3. `answer.reasoning` 是专家的整体总结，适合做过程页的收尾或旁白。
4. 讲「为什么可信」时用 `meta.approved_at`、`trajectory.sealed_manifest_sha256`、`screenshot_gaps` 为空、每步 `state_after` 可回放。
5. 时间线数字用 `showcase.duration_seconds` 与 `timing`。
6. 不要把 `keyframe.screenshot.object_key`、`bucket`、`assignment_id` 等内部坐标放进对外材料。

