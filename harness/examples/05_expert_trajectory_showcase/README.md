# ScienceVision 宣传素材 · 已通过题目集

这是一组从 ScienceVision 专家标注平台生产库导出的**已通过答案质检的题目**，用于对外展示「专家如何在 3D 查看器里一步一步做出科学判断」。每个子文件夹是一道题，文件夹名以题号 `task_id` 结尾（前面是领域和序号），内含题面、专家答案与推理、逐步的 MCP 工具调用轨迹、每一步的画面截图、以及一张重建的初始画面。

所有内容都来自专家**最终提交并被质检通过**的那一条有效操作血统。专家中途撤销、重做过的分支不在其中。3D 素材文件本身（`.cif` / `.xyz` / `.glb` / `.nii.gz`）随包，放在各题 `assets/`，sha256 与封存清单逐一校验一致。

标注专家一律只以平台账号 id 标识（如 `chemistry1` / `medical1`），不含姓名。

## 目录结构

```
ScienceVision-展示题目/
├── README.md              ← 本文件
├── index.jsonl            ← 一题一行的索引（题号 / 领域 / 步数 / 答案 / 题面前 120 字 …）
├── _tools/                ← 导出与重建脚本、模板、文本修订表；不是素材
└── <领域><序号>-<task_id>/  ← 一题一个文件夹，如 生物001-sv-biol-bio_collision_check-0012
    ├── README.md                  ← 本题概览、逐步速览、工具词典、时间线、全部字段说明
    ├── 题目与轨迹.json             ← 机器可读的全部数据
    ├── screenshots/
    │   ├── 操作000-初始状态-重建.png ← 题目刚打开时的画面（重建图，见下文）
    │   └── 操作NNN.png             ← 每一步操作后的画面（NNN = 步号，专家当时的原图）
    ├── index.html                 ← 双击即看的静态翻页页：题目 / 推理 / 答案 + 逐步截图 + 工具调用与专家说明
    ├── data.js                    ← 同 JSON，供 index.html 加载
    ├── assets/                    ← 本题的 3D 素材文件（.cif / .xyz / .glb / .nii.gz）
    └── lib/                       ← 平台前端词表（工具名中英文、参数释义、动作文案格式化）
```

## 怎么使用

1. 从 `index.jsonl` 或下面的「题目一览」选题。
2. 进入题目文件夹，先读 `README.md`：前半是这道题的速览（题面、答案、用时、逐步表、用到的工具），后半是 `题目与轨迹.json` 的逐字段说明。
3. 要图直接用 `screenshots/` 里的 PNG。文件名里的步号与 JSON 中 `operation_seq` 一一对应，`操作000-初始状态-重建.png` 是起点。
4. 要讲过程，每一步都能组成一页：工具中文名（`tool_glossary`）+ 专家说明（`keyframe.expert_note`）+ 该步截图。整体推理 `answer.reasoning` 和最终答案 `answer.answer` 做收尾。
5. 想直接看效果，打开该题的 `index.html`，无需服务器，键盘左右翻页，右上角可切中英文。
6. 对外引用时，重建的第 0 帧请标注「重建」；平台内部坐标（`object_key`、`bucket`、`assignment_id`）不要放进对外材料。

## 题目一览

| 题号 | 领域 / 子领域 | 查看器 | 步数 | 专家用时 | 答案 |
|---|---|---|---|---|---|
| [`sv-biol-bio_collision_check-0012`](生物001-sv-biol-bio_collision_check-0012/README.md) | 生物学 / 分子接触几何 | `molstar` | 8 | 4 分 10 秒 | `{   "distance_A": 2.067 }` |
| [`arch-jdc-clash-c01`](建筑001-arch-jdc-clash-c01/README.md) | 建筑 / 综合公用管线碰撞检测 | `gltf` | 18 | 228 分 29 秒 | `约为0.2m` |
| [`sv-chem-chem_conformation-0004`](化学001-sv-chem-chem_conformation-0004/README.md) | 化学 / 最远重原子对距离 | `molstar` | 5 | 0 分 56 秒 | `{   "farthest_heavy_atom_distance_A": 5.279 }` |
| [`sv-medi-med_ct_geometry-0000`](医疗001-sv-medi-med_ct_geometry-0000/README.md) | 医学 / CT 几何测量 | `niivue` | 8 | 45 分 30 秒 | `{   "crosses_fov_midline": <no>,   "longest_axes": <64>,   "representative_axial_index": <109> }` |
| [`sv-medi-med_ct_geometry-0058`](医疗002-sv-medi-med_ct_geometry-0058/README.md) | 医学 / CT 几何测量 | `niivue` | 15 | 7 分 37 秒 | `{   "extent_vox": <161-194，122-145，85-114>,   "longest_axes": <52>,   "representative_axial_index": <106>, }` |
| [`sv-biol-bio_docking_plausibility-0105`](生物001-sv-biol-bio_docking_plausibility-0105/README.md) | 生物学 / 对接组分邻近性 | `molstar` | 5 | 1 分 20 秒 | `{   "contact_distance_A": 2.898 }` |

汇总：6 题，覆盖生物 / 化学 / 医学 / 建筑 4 个领域，3 种查看器；共 59 步有效操作、59 张原始截图、6 张重建的初始画面；专家操作总时长约 4.8 小时（其中建筑题一题 3.8 小时）。

每题的用时是首步到末步的时间跨度，包含专家在两步之间观察、思考的时间，不是纯操作时间。

## 平台是怎么产生这些数据的

- **专家只能通过 MCP 工具操作查看器**。记录态下鼠标直接拖拽、滚轮缩放是被禁用的（`mcp_only`），旋转、缩放、切片、测量都要点面板上的功能按钮。这样每一个改变画面的动作都有名字、有参数、可复现。
- **每一步自动截图**。工具调用成功后，平台立刻抓取查看器画布，得到一张关键帧；专家需要为这一步写一句说明（`expert_note`），解释这一步在做什么、看到了什么。
- **状态前后都记下来**。每步保存 `state_before` / `state_after`：相机角度、缩放、平移、各组分或图层是否可见、当前切片索引、测量数量等。任何一步都能从初始状态重放到位。
- **撤销不留痕于成品**。专家可以撤销、重做，但提交时只封存从头到当前的一条链（有效血统）。因此步号可能跳号（建筑题是 3–21），跳过的号就是被撤销的步骤。
- **提交即封存**。提交时生成带哈希的封存清单（`sealed_manifest_sha256`），之后任何人都改不了轨迹。
- **两道质检**。题目先过「题目质检」（题面是否合理、是否要改），专家作答后再过「答案质检」；本包里的题全部处于答案质检通过的终态，且都是一次通过（`ever_returned = false`）。每题 `timeline` 里有完整的时间线。

## 查看器与 MCP 功能介绍

下面按查看器列出这 6 题实际用到的工具。工具名是平台内部的 MCP 函数名，中文名来自平台前端词表；「用到」一栏是在本包里出现的题数和总调用次数。完整的参数 JSON Schema 在各题 `题目与轨迹.json` 的 `tool_glossary.<工具名>.input_schema`。

### Mol*（分子结构查看器，`molstar`）

用于生物大分子和小分子结构（mmCIF / XYZ）。本包 3 题：两道生物、一道化学。

| 工具 | 中文名 | 作用 | 主要参数 | 用到 |
|---|---|---|---|---|
| `sv3d_set_component_visibility` | 显示组分 | 显示或隐藏 Mol* 自动生成的组分：水、配体、聚合物、离子、糖。未提及的组分保持不变。典型用法是先关掉水和离子，让目标残基露出来。 | `water` / `ligand` / `polymer` / `ion` / `carbohydrate`（布尔） | 1 题 · 2 次 |
| `sv3d_set_representation` | 切换分子表示 | 把某一类组分（聚合物 / 配体 / 糖）或某个具体残基换成另一种画法，如 cartoon、ball-and-stick、spacefill。 | `target`、`style`、可选 `chain_id` / `seq_id` / `comp_id` 定位残基 | 1 题 · 1 次 |
| `viewer_zoom` | 缩放 | 把相机距离设为「刚好装下可见结构」那个距离的绝对倍数：1 = 刚好装下，2 = 近一倍。绝对值而非增量，所以可精确复现。 | `zoom` | 2 题 · 3 次 |
| `viewer_pan` | 移动视角 | 把观察中心从可见结构的球心沿相机右方 / 上方偏移，以可见球半径为单位。 | `pan_right`、`pan_up` | 2 题 · 2 次 |
| `sv3d_orbit` | 环绕旋转相机 | 从给定的世界坐标方位角 / 俯仰角看结构，角度为绝对值。 | `azimuth_deg`、`elevation_deg` | 3 题 · 5 次 |
| `sv3d_measure` | 测量距离 | 在指定原子之间做距离 / 角度 / 二面角测量，并把结果记入轨迹。参数里带原子身份（链 / 残基 / 原子名）和坐标，结果如「2.067 Å」。三道分子题的最终答案都来自这一步。 | `kind`、`atoms[]`、`commit`、`value` | 3 题 · 5 次 |

### NiiVue（医学影像查看器，`niivue`）

用于 CT / MR 体数据（NIfTI），可叠加分割掩膜。本包 2 题医学。

| 工具 | 中文名 | 作用 | 主要参数 | 用到 |
|---|---|---|---|---|
| `sv3d_set_view_mode` | 切换 MPR / 轴位 / 3D | 在多平面重建（三视图）、单轴位切片、三维体渲染之间切换。专家通常先用 3D 找病灶，再切 MPR 量尺寸。 | `mode`：`mpr` / `axial` / `3d` | 2 题 · 7 次 |
| `sv3d_set_render_angles` | 设置三维方位 | 体渲染模式下设置观察的方位角 / 俯仰角。 | `azimuth_deg`、`elevation_deg` | 2 题 · 2 次 |
| `sv3d_set_slice` | 切换断层 | 设置十字线所在的矢状 / 冠状 / 轴位切片索引，未给的轴不动。逐层翻找病灶边界就靠它。 | `sagittal`、`coronal`、`axial` | 2 题 · 9 次 |
| `sv3d_set_mask_visibility` | 显示或隐藏掩膜 | 显示或隐藏叠加的分割掩膜（红色区域）。 | `visible` | 1 题 · 4 次 |
| `sv3d_measure` | 测量距离 | 在某个切面上用 RAS 毫米坐标量长度或角度，并记录结果。 | `kind`、`plane`、`points_ras_mm[]`、`commit`、`value` | 1 题 · 1 次 |

### glTF 三维模型查看器（`gltf`）

用于建筑 / 机械的三维网格模型（glb）。本包 1 题建筑管线碰撞检测。

| 工具 | 中文名 | 作用 | 主要参数 | 用到 |
|---|---|---|---|---|
| `sv3d_toggle_layer` | 显示或隐藏图层 | 按名字显示或隐藏场景图层，如「场地」「幕墙」「楼板墙」。先把遮挡的图层关掉，才看得到管线和梁。 | `layer`、`visible` | 1 题 · 3 次 |
| `viewer_zoom` | 缩放 | 与 Mol* 同义：相对「刚好装下模型」视野的绝对倍数。 | `zoom` | 1 题 · 2 次 |
| `sv3d_orbit` | 环绕旋转相机 | 围绕当前目标点，把相机转到绝对方位角 / 俯仰角。 | `azimuth_deg`、`elevation_deg` | 1 题 · 5 次 |
| `sv3d_measure_distance` | 测量网格点距离 | 量模型上两个世界坐标点的三维距离，同时报 glTF 米制和 Rhino 毫米制。 | `start[3]`、`end[3]` | 1 题 · 5 次 |
| `sv3d_clear_measurements` | 清除测量 | 清掉画面上的测量线和结果，准备量下一处。 | 无 | 1 题 · 3 次 |

除上述外，每个查看器还有 `viewer_reset`（回到初始状态）和 `viewer_get_state`（读取当前状态）两个内部工具，回放和校验用，不出现在专家的操作轨迹里。

## 读数据时要知道的几件事

- **步号会跳、不一定从 1 开始**。`operation_seq` 是平台分配的号，撤销掉的步骤号不回收。讲「第几步」用它在 `operations[]` 里的位置，引用截图用步号。
- **`replay_input` 才是权威参数**。`input` 是专家在面板上点出来的原始值，`replay_input` 是规范化后用于回放的值（例如测距会补全原子坐标）。展示、讲解以 `replay_input` 为准。
- **初始画面是重建的**。平台在题目刚打开时不截图，关键帧从第 1 步操作后开始。`操作000-初始状态-重建.png` 是用同一查看器引擎、同一素材、同一 URL 参数在本地重新渲染，画幅取专家第一张截图的尺寸，相机按封存的 `initial_state.camera` 对齐后用平台截图 API 抓取。校验结果在 `showcase.initial_frame`：四道分子 / 建筑题相机与记录一致，两道医学题状态里无相机字段但十字线、窗宽窗位、掩膜一致。它是重建图，不是原始记录。
- **题面有原始版和定稿版**。顶层 `question` 是题目质检后的定稿；`original_task.question_*_original` 是质检前的原文。本包中建筑题和医学 0058 被质检改过题面（`question_review.decision` 为采纳修改），其余四题保持原题。
- **时间全部是 UTC**。`timing.started_at` 是每步开始时刻，相邻两步之差就是专家的观察思考时间。
- **答案是专家原文**。有的题专家按题面格式写成 JSON，有的写成一句话（建筑题「约为0.2m」）；医学题里 `<64>` 这类尖括号是专家沿用了题面里的占位写法。都没有改。

## 已知瑕疵

- 建筑题 `arch-jdc-clash-c01` 18 步里只有 2 步写了专家说明，其余为空。
- `sv-biol-bio_docking_plausibility-0105` 的中文标题字段在生产库里被机翻填成了整段题面，原样保留。
- `sv-medi-med_ct_geometry-0000` 的推理原文末尾多了一个逗号，导出时已修订，原文记在 `showcase.text_overrides`；修订规则见 `_tools/overrides.json`。
- 重建的初始画面右下角坐标轴指示器比原始截图略大，是无头渲染器与浏览器像素比处理不同所致，不影响模型本身。

## 数据口径

- 「已通过」= 平台 `assignments.status == 'approved'`（答案质检终态）。
- 轨迹 = 专家提交时封存的清单，服务端校验过：单链血统、步号单调、每步成功、每张截图 sha256 一致。
- 截图从平台归档存储按哈希校验后取回，为专家当时画面的原图，未压缩。
- 3D 素材随包，在各题 `assets/`；元数据（平台内路径 / sha256 / 大小 / 角色）在 `题目与轨迹.json` 的 `task_context.assets` 里，`asset_file` 指向包内副本。每个文件的 sha256 都与封存清单核对过，不一致的不会入包。

## 重新生成（维护者）

```
# 1) 导出题目（会重建整个题目文件夹）
SV_OSS_AK=… SV_OSS_SK=… python3 _tools/export_showcase.py --repo <repo> --db <manager.db 快照> --out . <task_id> …
# 2) 把 3D 素材收进包（逐个按 sha256 校验；--search-root 可重复，指向多棵源树）
python3 _tools/collect_assets.py --search-root <repo> [--search-root <另一棵源树>]
# 3) 起本地查看器服务（另一个终端）
python3 _tools/showcase_viewer_server.py --repo <repo> --assets <素材目录> --port 4181
# 4) 重建第 0 帧（必须在 1 之后跑，1 会清掉旧的重建帧）
python3 _tools/rebuild_initial_frame.py --out . [task_id …]
# 5) 出包前两道自检，都应退出 0
python3 _tools/deidentify.py --check
python3 _tools/collect_assets.py --check
```

文本修订写在 `_tools/overrides.json`，导出时自动套用。

脱敏：`export_showcase.py` 一拿到生产库的记录就先过 `_tools/deidentify.py` 的 `scrub_document`，
专家姓名不会写进任何产物；`--check` 是出包前的守门，发现残留时非零退出且只报字段名、不打印姓名。
素材解析不到、或 sha256 与封存清单不一致时 `collect_assets.py` 拒绝入包并非零退出——
同名不同版本的素材会让轨迹回放对不上，比缺文件更难发现。
