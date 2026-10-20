# ScienceVision 发布面整合设计：eval-baselines、复现 eval、镜像迁入 `UniPat-AI/ScienceVision`

- **读者**：审批人（Sue），以及按本文实施的人（Claude 或子代理）。
- **产出**：`UniPat-AI/ScienceVision` 成为 eval 代码、2D 方案、复现工具链和镜像引用的唯一权威仓库，并发布 `v1.0.0`。
- **判据**：只靠这个仓库（加上相应权限），读者就能按 digest 拉镜像，下载 pinned 数据和 `v1.0.0` 包，校验 checksum，并跑出 **250/250 oracle = 1.0**。整个过程不需要 BV2 或 SV 的任何分支。

日期 2026-10-10（设计对话在 2026-10-09）。状态：**已批准**（2026-10-10）。§9 的三项按推荐方案定案。

---

## 1. 目标与范围

| 你的原话 | 落点（经对话确认） |
|---|---|
| 把 eval-baselines 迁过去 | 把 BV2 `eval-baseline@b47f13ac` 的 eval 子集导入 `harness/`，baseline 定义文档重写（§5.3） |
| 把复现 eval 迁过去 | 迁复现**工具链**：导出 contract → 生成 7+4 个 setting 包 → oracle/nop 冒烟 → 发布（§5.5） |
| 把 images 迁过去；GitHub package 要最新版并重命名 | 用 GHCR 上已有的最新构建，统一加 `v1.0.0` tag，包名不动（§5.2） |
| 2D 既要 images 采样方案，也要 eval 方案，两者分开 | 两份文档、两处代码，接口只有一个：已发布的视图树 schema（§5.4） |

**不做**（以及为什么）：

- **不重建镜像。** "最新版"指的是现有的最新构建。重建会带来新行为，需要另外立项。
- **不迁移结果记录**（比如 750 trial、500 行重判）。`performance/` 仍然只放合成示例，已有的运行在附录 B 留了索引。
- **不改 GHCR 可见性。** 没有 API，只能由 org owner 在网页上手动改，列为收尾时的手工步骤。
- **不动 Docker Hub `suermars/*`。** 过渡期两边并存。
- **不删 BV2 的代码。** 只加一条"已迁移"说明（收尾时单独确认）。
- **不迁这些内容**：QA-Gen QC、平台与标注（`tools/offline_task_manager`、`deploy`、`sv-annotate`）、`examples/`、各轮运行专用的一次性脚本。

## 2. 术语

先定义，后文都按这里的含义使用：

- **setting**：论文里的访问条件，比如 3D + Scaffold、2D + Scaffold ×6。
- **route**：harness 内部的路由 ID，有 `cc`、`ccmcp`、`3d-mcp`、`cc2d-N`、`cc2d-orbit-N`。结果记录和 CLI 用 route，报告用 setting。
- **contract**：建树所需的完整任务记录，包括 instruction、资产及其 role、`answer_schema`、`ground_truth`、scorer、tolerance、eligibility。HF 上的 `normalized.json` 只有题面和答案，**不是** contract。
- **视图树**：`<domain>/<task_id>/{views-aligned,views}/cc2d-<N>/view_NN.png`，加上 `index.json`（schema `cc2d-views/3`）。
- **receipt**：对缺图或排除的书面说明文件。没有 receipt 的缺口一律当作故障。
- **平台包**：每个 setting 一个 `tar.gz`，里面是 250 个任务目录，供 UniEval 等平台导入。

## 3. 现状事实（2026-10-09/10 实测）

### 3.1 目标仓库

- private，main @ `e5571bd3`（2026-10-08）。里面只有文档、schema 和自检脚本。harness 指向 BV2 `b47f13ac`，数据 pin 在 HF `0f9e91ed`。README 自己写着"没有统一的 task export，不能端到端复现"。
- 10 个 GHCR 包已经 link 到本仓库，**全部 private**。
- **已有引用失效了**：README 和 `resources/images.json` 引用的 scorer digest `d07c59a7…`、`751eedbd…`、`85607538…`，在 10-09 用 skopeo 归一之后都成了 untagged。对应的 tag 现在指向与 Docker Hub 相同的 `b2d62b23…`、`b97ca59f…`、`7e5471e1…`。BV2 的 open PR #129 引用的 `751eedbd` 也一样失效。

### 3.2 数据

- HF `elorian-ai/ScienceVision` `review-v11` 的 tip 是 `bc45c91a`（10-09）。相对 `0f9e91ed` 有四批变更：
  - 医学 0014/0016/0042：答案和题面（`5e4ad78e`）
  - 化学 7 道：换装 10-08 的 approved 记录（`cba6f38d`）
  - 0014/0042：mask 字节隔离（`0e545aa7`、`73a3bc13`）
  - 生物 3 道 molstar：补轨迹，其中 2 道改了答案，`question.en` 换成英文（`bc45c91a`）
- `review-v11` 上还留着被否掉的 `mvd-068`（73 个文件），main 上没有（见 §9 D3）。
- 每道题都带 `views-aligned/` 和 `views/` 两套图，schema `cc2d-views/3`，来源是 live viewer 截图。
- 完整 contract 只存在于 H20 上的 v11 bundle（`platform-pkg-20261004/bundle/.../task.json`，10-05 状态）。
- **2026-10-10 已有一份刷新到最新 `review-v11` 的 bundle**，是另一轮跑批做的，位置在 `.132:/nvme4/matianyi/ScienceVision-evals/v11-250-4model-pass3-20261010/bundle/`，映射规则沿用 `build_v11_swap.py`。实测它与 10-05 bundle **正好差 11 道**：
  - 医学 0014/0016/0042：答案改值；0014/0042 删掉 `mask.nii.gz`，加 `drawings/seg_00.png` 和 `seg_close_00.png`
  - 化学 conformation 0002/0028/0037/0050/0054/0055：题面和字段改版
  - 生物 molstar ligand_occlusion-0006 和 chain_intersection-0007：答案改值
  
  另一道 molstar 题 0003 的 bundle 题面取自 `question.md`，本来就是英文，所以没有差异。

### 3.3 镜像

GHCR 上每个角色的最新构建：codex 三件套是 `0930`，claude-code 三件套是 `1002`，两个 `-mcp-client` 分别是 `release-0930` 和 `release-1002`，worker 是 `0924`，scorer 是 `1005`。另外还有 oracle 专用的 `mcpfix-20261006`。完整 digest 见附录 C。

### 3.4 eval-baselines 代码现状（数字见附录 A）

> 10-05 的平台包和 v11 3-arm codex run，都是 **H20 上的 tree-builder** 建出来的，不是 BV2 `b47f13ac`。两者在 harness 里有 103 个共有文件，其中 27 个内容不同。`build_harbor_tasks.py` 差了 +881/−231 行，涉及 instruction 渲染、`_render_task_toml`、`build_one`。`b47f13ac` 还多出 13 个文件，比如 `_membership_rules`、`view_protocol`、`step_mesh`、`route_contracts`、`security_contract`、MCP worker 模板。**所以用 `b47f13ac` 重建出来的包，不会只在数据变更的那几道题上和旧包不同。**

- **MCP 布局不一样。** `b47f13ac` 的 MCP 路由是"source-free agent + 独立 worker"，每道题生成 `docker-compose.yaml` 和 `Dockerfile.mcp-worker`。10-05 平台包里的 `cc-mcp` / `mcp-only` 用的是单容器 bundled 镜像，agent 容器里同时带着源码和 MCP server。
- **保活 `CMD` 修复**（`2f0f8627`）修在 SV lineage 上，`b47f13ac` 里没有。不修的话，平台上的容器起来就退出。
- **bundle → QA record 适配器** `build_v9_task_trees.py`（273 行）不在 `b47f13ac` 里。SV `2f0f8627` 的 `_ops` 副本与 H20 用的那份 sha256 相同（`3d36a6cc…`）。
- **BV2 的 `docs/harbor/BASELINES.md` 部分过期。** 它是 eval baselines 的定义文档：
  - 过期的部分：默认 agent 写的是 claude-code + `claude-opus-5`；task set 写的是 scale-v1/sample50；2D 写的是离线渲染 `cc2d-views/2`，而且注明"必须在 GL 环境生成"。
  - 仍然有效的部分：两条正交消融、held-constant 原则、闭环 ±0.005 容差表、三处故意不给容差、membership scorer、`--mcp-config` 警告。

### 3.5 2D

- 截图管线只存在于 SV `feat/cc2d-medical-coverage@71045a1a`：4 个文件加测试。BV2 只有离线渲染这一条路，**这就是外部复现方说"两套图"的根因**。
- viewer 子集是 5 个页面加 `viewer/shared/`，约 21 MB，内含 Mol* 4.5.0、niivue 0.45.0、three 0.160.0 和 OCCT。两轮拍摄之间 viewer 只改过 `occt.html` 的 10 行。

## 4. 已确认的决策（2026-10-09 对话）

| # | 决策 | 选择 |
|---|---|---|
| 1 | "复现 eval"指什么 | 复现工具链；不迁结果 |
| 2 | 镜像"最新版" | 用现有最新构建，不重建 |
| 3 | 重命名方式 | 只改 tag：`sciencevision-<role>:v1.0.0`，包名不动，旧 tag 保留为别名 |
| 4 | 数据 pin | 升到 HF `review-v11@bc45c91a` |
| 5 | 迁移方式 | 快照导入 + `resources/provenance.json`；布局 `harness/` + `sampling/2d/` + `reproduce/` |
| 6 | registry | GHCR 为主；复现工具链带 `--registry dockerhub` 开关 |
| 7 | 2D | 接口 = 视图树 schema；采样是可选步骤；评测只读已发布的图；2D 路由去掉离线渲染 |
| 8 | 派生包放哪 | 新建 private + gated HF 数据集 `elorian-ai/ScienceVision-Eval`，打 `v1.0.0` tag |
| 9 | 交付 | 4 个 PR，可以各自独立合并和回滚 |

## 5. 设计

### 5.1 仓库布局

```text
ScienceVision/
├── README.md  docs/  resources/  schemas/  performance/  scripts/   # existing, kept
├── harness/                 # eval code, module names unchanged
│   ├── pyproject.toml
│   ├── sci3d_bench/  harbor_adapters/  scorer_bundle/  sci3d_agent/
│   ├── scripts/harbor/      # builders, run_end2end, collect, aggregate, templates/
│   └── tests/
├── sampling/2d/             # optional: produces view trees (browser + viewer subset)
└── reproduce/               # contracts -> setting packages -> smoke -> publish
```

- **模块名不改。** 镜像里的 `COPY` 路径和 `python -m scripts.harbor.*` 都照常可用，只要先 `cd harness`。
- **依赖只朝一个方向**：`sampling/` 和 `reproduce/` 可以 import `harness/`，`harness/` 永远不 import 它们。
- `resources/provenance.json` 记录每个导入文件的来源仓库、commit 和 sha256。导入之后做的修改，各自单独一个 commit，不混进 import commit。

### 5.2 镜像：`v1.0.0` 及其引用

| 包 | `v1.0.0` 指向 |
|---|---|
| `sciencevision-codex` / `-2d` / `-mcp` | `server-20260930-h20` |
| `sciencevision-codex-mcp-client` | `release-20260930-h20` |
| `sciencevision-claude-code` / `-2d` / `-mcp` | `server-20261002-h20` |
| `sciencevision-claude-code-mcp-client` | `release-20261002-h20` |
| `sciencevision-mcp-worker` | `server-20260924-h20` |
| `sciencevision-scorer` | `server-20261005-h20` |

- **怎么打 tag**：在同一个 GHCR 仓库里执行 `skopeo copy --preserve-digests`，digest 和字节都不变。打完逐个回读，`v1.0.0` 的 digest 必须等于源 tag 的 digest。
- **不打 `v1.0.0` 的**：`mcpfix-20261006-h20`（只是 oracle 补丁）、scorer 的 `0930` / `0928b`（历史版本）、`-agent` 旧别名。3 个 untagged scorer manifest **不删**，因为 BV2 `b47f13ac` 按 digest pin 了 `d07c59a7`。不加 `latest`。
- **镜像 label 写不进 `v1.0.0`**，写进去就得重建、digest 会变。所以来源信息只放在 `resources/images.json`：每条记录写原 build tag、源 digest、`v1.0.0` digest，并保留旧 digest 方便核对历史 receipt。
- **代码里的引用**：pinned harness 里有 18 个文件约 38 处硬编码镜像名，统一改为从 image lock 读取。镜像信息只有一个事实来源，就是 `resources/images.json`：harness 通过 `scripts/harbor/image_lock.py` 读取它（可以用 `SV_IMAGE_LOCK` 覆盖路径），CI 负责检查代码里不再出现硬编码的镜像名。
  - 任务 Dockerfile 的 `FROM` 和 scorer 默认值，都按 digest 指向 `v1.0.0`。
  - preflight 改为比对镜像 digest，不再比对别名字符串。
  - `build_*_image.sh` 保留用于重建，产出的本地别名统一叫 `sciencevision-<role>:local`。
- **复现工具链默认用 GHCR**，`--registry dockerhub` 改用 `images.json` 里记录的 Docker Hub digest。这个开关是给拿不到 GHCR 凭据的平台用的，GHCR 可见性改成 public 之后就可以删掉。

### 5.3 eval-baselines：迁什么，怎么迁

**来源**：BV2 `eval-baseline@b47f13ac`，也就是目标仓库已经 pin 的那个版本。

**带走的规则**：通用入口的 import 闭包，加上镜像模板和它们 `COPY` 的输入，再加上只依赖这些模块的测试。

通用入口包括：
- `run_end2end`，含它以子进程方式调用的 `plot_leaderboard`
- `qa_gen_to_tasks`、`build_harbor_tasks`
- `build_v21_ev2_trees`：六路建树器，名字是历史遗留
- `collect_qagen_results`、`aggregate_weighted`、`rescore_rerun`、`completed_task_ids`、`leak_scan`
- `scorer_bundle/*`
- `sci3d_agent/mcp/server`：MCP server，同时也是 worker 的入口
- `harbor_adapters/**`、`sci3d_bench/**`

按这个规则实测，闭包是 132 个 `.py`，去掉排除项后剩 **125 个**（明细见附录 A）。

**不带走**：
- QA-Gen QC 链：`probe_irreducibility` → `scripts/qa_qc*` → `sciencevision_taskgen`
- 各轮运行专用的报告和启动脚本：v21_ev2、sample50、scale_v1、ev2 threeway、ModelScope 上传
- `tasks*/` 样例树、平台与标注代码、`examples/`
- viewer（2D 子集除外）、`datasets/`（`_matched/` 除外，它是 MCP 镜像的构建输入）

**`b47f13ac` 之外必须补的三件**：

1. **保活 `CMD`。** 照着 `2f0f8627` 的意图，在 `b47f13ac` 的 `_agent_envs.py` 和 7 个基座模板上重新实现，**不能直接 cherry-pick**，因为两边 lineage 不同。附带两条守卫测试。
2. **bundle → QA record 适配器**（`build_v9_task_trees.py`），放进 `reproduce/`，适配 `b47f13ac` 的 `qa_record_to_task` / `build_one` 签名。用测试钉住 5 条一旦出错就静默失败的契约：GT 必须包成 `{"value": …}`、scorer 必须在支持列表里、必须给 `repo_path`、必须给 `viewer`、cc/ccmcp 必须 `--containerized`。
3. **单容器 bundled MCP 打包 profile**（§9 D1 的推荐方案）：在 `MCPWorkerPackaging` 之外再加一个 `bundled` profile，只给平台包用。Harbor 路由默认仍然是 client + worker。

**清洗闸**（PR 2 的合并条件；扫描范围是带走的代码、测试和文档）：
- 私网 IP、主机路径、人名、网关名、密钥形态：全部为 0。
- 中文只允许出现在显式 allowlist 里，比如 scorer 的归一化表，每条都写明理由。
- `H20` 和 `suermars/` 只允许出现在 `images.json` 的来源字段里。
- `sci3d_agent/mcp/runtime/paths.py` 里的 `_DEFAULT_VIEWER_BASE` 现在是一个内网 IP，改成必须由环境变量提供。

**测试和 CI**：
- 可以带走约 120 个测试文件，准确数字以导入检查和实跑结果为准。
- CI 只跑不依赖 Docker、GL 和 `crawl_data` 的部分。
- `tests/mcp` 的数据闸保持原样：没有数据就 skip。CI 要输出 skip 计数，避免出现"全绿但其实没跑"。

**文档**：`docs/evaluation.md` 重写为 eval baselines 的定义文档，包含：
- matrix：7 个论文 setting，加 orbit 作为补充对照
- held constant：任务、由镜像 pin 住的 agent CLI 版本、verifier、reward、judge、timeouts
- 容差表：取自 `BASELINES.md` 仍然有效的部分
- 镜像管线和两种 MCP 布局
- `--mcp-config` 警告
- 指标定义：`pass@1` / `best@1` / `pass@3` / `best@3`，含公式、分母和不跨轮合并的规则（见下）

`BASELINES.md` 的内容按附录 D 三分，**不是整段丢**。

**指标定义（2026-10-13 定案）**

一轮 = 一次完整作答：agent 在一次 run 里回答全部小问，scorer 对这**一个** answer 对象算出**一个** `answer_score`。记 task `t` 在第 `r` 轮的分数为 `s(t,r)`，`full(t,r) = 1[s(t,r) = 1.0]`。

| 指标 | 公式 |
|---|---|
| `pass@1` | `mean_t full(t,1)` |
| `best@1` | `mean_t s(t,1)` |
| `pass@3` | `mean_t max_r full(t,r)` |
| `best@3` | `mean_t max_r s(t,r)` |

**四个指标共用同一个 task set：一张表一个集合。** 分母不一致会让不变式在报表面失效：9 道题里 1–8 三轮齐全且全错、第 9 道 r1 全对而 r2/r3 挂掉时，`pass@1 = 1/9`（分母 9）、`pass@3 = 0/8`（分母 8），于是 `pass@3 < pass@1`。这不是实现错，是分母定义错，所以它是一条硬约束，不是约定。

**@1 和 @3 必须取自同一次 run。** 拿另一次 run 的单轮结果去填 @1，会让消融表两列来自不同抽样，同一行的四个指标也不再可比。

**一轮有效**：`status == "ok"` 且 `evaluation_eligible` 为真；引用的刊数字再加 `citable is True`。register（`completed` / `eligible` / `citable`）在每一档各自生效，产物里写明是哪一档，发表面用 `citable`。`error` / `incomplete` 的轮**算缺测，不算 0**——否则基础设施故障会被当成模型答错，把 `best@3` 压低。缺测单独报数，不插补（沿用 `run-metadata.schema.json` 已有的 `missing_trial_policy` 常量）。

**不许跨轮合并小问。** `best@3` 是三轮**整次作答**分数的最大值，不是"每个 field 取三轮里的最好值"再拼成一个 answer。

具体例子：某题 3 个小问，r1 只对 A，r2 只对 B，r3 只对 C。

- 按本定义：每轮 1/3，`best@3 = 1/3`，`pass@3 = 0`（没有一轮全对）。
- 按 field 合并：3/3，`pass@3 = 1`。这衡量的是"三次抽一个能过的"，即 test-time selection，衡量预算而不是模型。

**不变式**（作为 property test 钉住，任何一条挂掉就是实现错了）：

- `pass@1 ≤ pass@3`，`best@1 ≤ best@3`
- `pass@k ≤ best@k`：全对必然是最高分。**这条同时是跨轮合并的探测器**——合并实现会出现 `pass@3 = 1` 而 `best@3 < 1`，直接违反。
- `best@k` 只可能等于某一轮的实际分数，不会落在两轮分数之间。
- **同一张表里四个指标的 task set 完全相等**，按 `task_id` 逐个比对而不只是比计数——这条是分母口径的守卫，`pass@3 < pass@1` 那样的表会被它拦住。
- `best@1` 在 `repeats == 1` 时必须等于 `by_harness_model_mean_answer`（同 register、同 task set），这条把新指标钉回历史口径。

**发表面只报 paired 一张表。** task set = 三轮全部通过该 register 闸的题，四个指标都在这一个集合上算，附 `n`、`citability` 计数，和一份洞的清单（哪些题、哪些轮、什么 reason code）。

洞分两种，只有一种会产生"三轮只跑了两轮"的题：

- **run hole**（`status` 是 `error` / `incomplete`）：可重跑。
- **data hole**（`blocked` / `evaluation_eligible: false`）：永久的，而且是**题的属性不是轮次的属性**——契约坏了、GT 泄漏了，三轮都坏。所以它只让任务集整体变小，不会造出部分轮次。

也就是说部分轮次**只可能来自 run hole，而 run hole 能补**：`completed_task_ids` 找缺口，`rescore_rerun` 定点重跑。`record_kind: "benchmark"` 本来就强制 `error` / `incomplete` / `missing` / `blocked` / `ungated` 全为 0 且 `len(completed) == expected`，所以**发表面上 paired 恒等于 wide，分母问题不存在**。

**只有 `record_kind: diagnostic`（中途看进度）才报第二张表**：主表仍是 paired；wide 单列，集合取 `{t : 第 1 轮可用}`（这样 @1 仍有定义），标 `not_for_citation`。理由是洞不随机——丢轮的题偏向更难的题——所以 paired 会高估、wide 会低估，两个数只是把偏差摆开，不是区间；正确动作是补洞或整题丢弃，不是发表一对带偏差的指标。

**`best@1` 在单轮 run 上等于现有的 `by_harness_model_mean_answer`**（都是 `answer_score` 的均值），所以新列能直接对着历史数字读，附录 B 的已有 run 仍然可比，不存在口径断点。

**可选诊断**（不是模型指标，报告里必须原样带这个标签）：`best_at_3_selection_upper_bound` = 跨轮按 field 取最好值再判分的分数，即"允许 test-time selection 时能拿到多少"，用来量化 @3 与选择上限的差。不得单独引用。

**落在哪里**：新增 `harness/scripts/harbor/round_metrics.py`（纯函数，输入 rows 输出指标）；`collect_qagen_results` 给每行写 `repeat`（1 起）；board 加 `by_harness_model_{pass_at_1,best_at_1,pass_at_3,best_at_3}` 两档（`citable` 与 `_all_eligible`）加一张分母表；`plot_leaderboard` 的 chart data 同步。

**必须改 schema，这是硬约束**：`performance.schema.json` 的 `rows` 现在没有轮次字段，`run-metadata.schema.json` 的 `scope.repeats` 虽然是 ≥1 的整数、`coverage.expected` 也已经按 repeats 算，但**行无法归属到第几轮**——所以在现有 schema 上算不出 `pass@3`。同时 `run-metadata.schema.json` 的 `aggregation` 用 `additionalProperties: false` 把 `mode` 锁成 `eligible_citable_mean` 一个常量，要放开成两个 mode。新增的 `repeat_k_metrics` 带四个字段，把上面三条规则**写进产物本身**，光靠文档约束不住：

| 字段 | 值 | 钉住什么 |
|---|---|---|
| `k` | 整数 ≥1 | 轮次预算 |
| `denominator` | 常量 `paired` | 四个指标共用一个 task set |
| `round_union` | 常量 `prohibited` | 不许跨轮合并小问 |
| `sensitivity` | 常量 `diagnostic_only`，可选 | wide 表只准出现在 diagnostic 记录里 |

`performance/examples/` 同步更新。

### 5.4 2D：采样方案和评测方案分开

**接口**：`schemas/cc2d-views.schema.json`，形式化 `cc2d-views/3`，加上视图树的文件布局，放在 pinned 的 HF commit 上。结构校验器放在 `harness/`，`sampling/` 反过来 import 它。

**采样方案**：`docs/sampling-2d.md` + `sampling/2d/`。

- **产出什么**：每道题两套图 × 4 个档位。
  - aligned：命名面，按前缀嵌套
  - orbit：方位角均匀分布，仰角 20°，按子集嵌套
  - 医学题：切片按"目标覆盖 + 体积铺开"选取，并避开答案坐标；三窗格合成图和体渲染成对出现；mask 以红色叠加；点答题只覆盖轴向
- **闸门**：空帧、瞄准 ±1.5°、泄漏（切片索引不能落在答案坐标或专家访问位置上）、坐标读数已隐藏、结构完整。拍不出来的题写 receipt。
- **代码**：从 `71045a1a` 取 4 个文件和测试，viewer 子集从拍摄所用的 commit 逐字节 vendor 进来。依赖 playwright + Chrome。**这部分是可选的，只跑评测的人不需要装。**
- **可复现性分两层承诺**：
  - **计划**（选哪些角度、哪些切片）是确定性的。验收方式：用 `--plan-only` 跑全部 250 题，输出与已发布的 `index.json` 逐字节一致。如果截图驱动还没有这个模式就补一个：只跑 planner，不启动浏览器。
  - **像素**取决于 Chrome 和 swiftshader 的版本，所以只做抽样，报告相似度，不作为闸门。

**评测方案**：`docs/eval-2d.md` + harness 的 2D 路由。

- **Setting**：`cc2d-1/3/6/12` 用 aligned，对应论文里的 ×1/×3/×6/×12；`cc2d-orbit-1/3/6/12` 是取角方式的补充对照，不算论文 setting。
- **agent 拿到什么**：除了 `/data/view_NN.png` 什么都没有，没有原始资产，也没有 MCP，用 `-2d` 的 `v1.0.0` 镜像。题面除了资产块之外和 3D 逐字相同，scorer 和 GT 也和 3D 相同。
- **评测时绝不渲染**：只读已发布的图。缺图就直接失败，除非有 receipt。harness 2D 路由里的离线渲染去掉；渲染器代码本身保留，MCP 工具还在用。
- **可比性规则**（写进文档）：
  - 医学题 aligned 和 orbit 的合成图字节相同，只有一半信号
  - 医学切片的档位嵌套情况在 pinned 数据上重新测量，并公布不嵌套的题号
  - 极值面按设计不进图
  - 只渲染主资产
  - 旧 3-arm 基线的医学 2D 分数用的是旧切片，不能直接比
- **每个 setting 都报告分母和覆盖率。**

### 5.5 复现工具链：`reproduce/` + `docs/reproduce.md`

| # | 步骤 | 输入 → 输出 | 闸门 |
|---|---|---|---|
| 1 | `export_contracts` | 刷新后的 v11 bundle（§3.2）加 HF `bc45c91a` → 250 个 contract | 题面和 GT 必须与 HF 逐字一致。与 10-05 contract 相比，变化的题必须**恰好是** §3.2 那 11 道，多一道少一道都中止。这一步产出 changelog |
| 2 | `build_trees` | contract + HF 资产 + image lock → 任务树（3D 路线 cc / ccmcp，mcp-only 复用 ccmcp；2D 是 4 档 aligned + 4 档 orbit） | 结构闸，以及医学题的图数必须是 2N |
| 3 | `make_platform` | 树 → 平台任务目录 | 可见侧不带 GT，hidden 侧放 `task.hidden.toml`，用 CLI 版 `test.sh`。生成器直接产出保活 `CMD`；mask 隔离由 contract 的资产清单驱动；`schema_version` 做成参数（批量通道 2.0，单题通道 1.1） |
| 4 | `smoke` | scorer `v1.0.0` | 每个 setting **250/250 oracle = 1.0**，nop = 0.0，再加 marker 回退。泄漏闸：可见侧无 GT、无内网路径，2D 包里没有任何 3D 文件 |
| 4b | `contract_diff` | 新包对比 10-05 包 | 每处差异都归入"数据变更"或"代码 lineage 变更"两类，**无法归类的差异 = 0**（§9 D2） |
| 5 | `pack` | 11 个 `<setting>.tar.gz` + SHA256SUMS + manifest | 确定性打包：成员排序、固定 mtime、gzip 头不写时间。打两次 sha 必须相同 |
| 6 | `publish` | 推到 HF `ScienceVision-Eval@v1.0.0` | 以分支 ref、文件数、内容哈希为准，**不看 `hf` 的退出码** |

- 第 2–5 步在 H20 上用 `tmux-run` 跑，那里有 amd64 原生 Docker。脚本全部参数化，不写死路径和解释器。
- 第 6 步从 Mac 发起，隧道不稳定时用 `upload-large-folder` 续传。
- 平台包默认 claude-code harness，`--harness codex` 用来对齐论文里的 codex 结果。
- **已知限制**写进文档：
  - 化学 0002 在 delivery 里自相矛盾：`shortest_bond_path_atom_ids` 是 `[8,9,3]`，但 `internal_atom_count` 仍是 3，而旧路径 `[21,8,9,3,2]` 本身是自洽的
  - 0042 的 `signed_lr_distance_mm` 末位是 `7.257756` 还是 `7.257755` 待定
  - 0014 的 `trajectory.final_result` 与 `answer.answer` 不一致

## 6. 交付顺序与验收

| PR | 内容 | 对外操作 | 验收 |
|---|---|---|---|
| **1 · pins** | GHCR 打 `v1.0.0`；修正失效的 digest；数据 pin 改到 `bc45c91a`，重算 index sha、文件数、字节数 | GHCR 加 tag | 每个 `v1.0.0` 的 digest 等于源 tag；仓库里出现的每个 GHCR digest 都有据可查（是某条记录的现役 digest，或被显式登记进某条记录的 `superseded_manifest_digests`）；`validate_repository.py` 通过 |
| **2 · harness** | 导入 eval 子集；接入 image lock；补上保活 `CMD` 和 bundled profile；实现 `pass@k` / `best@k` 并放开两个 schema；`evaluation.md` 重写；补 CI | — | CI 全绿且报告 skip 计数；入口 `--help` 能跑；清洗闸为 0；五条指标不变式测试通过（含跨轮合并探测器和同表 task set 相等） |
| **3 · 2D** | 加入 `sampling/2d/`、schema、`sampling-2d.md`、`eval-2d.md`；2D 路由只读已发布的图 | — | `--plan-only` 跑 250 题与已发布 `index.json` 逐字节一致；2D 树里的 PNG 与已发布的字节相同 |
| **4 · reproduce** | 加入 `reproduce/` 和 `reproduce.md`；在 H20 上导出 contract、建包、冒烟；发布 `v1.0.0`；README 的 Evaluation 段改写 | 新建 HF 仓库并上传 | 250/250 oracle；contract diff 无法归类项为 0；两次打包 sha 相同；changelog 完整 |

**收尾**（对外操作，到时候再确认）：给 BV2 `eval-baseline` 加一个"已迁移"说明的 docs PR；以 superseded 为由关闭 #129；请 org owner 决定 GHCR 可见性；在目标仓库打 git tag `v1.0.0`。

## 7. 自由度与例外

下面这些可以不做，但必须给出相应证明：

- **闭包里的某个文件可以不带**：要证明没有任何入口或测试依赖它，也就是导入检查和测试都通过。
- **像素可以不逐字节复现**：前提是计划级逐字节一致，并附上抽样相似度报告。
- **某道题可以没有 2D 图**：必须有 receipt，并在分母里体现出来。
- **新包可以与 10-05 包不同**：每一处差异都要在 contract diff 里归类。
- **某个测试可以 skip**：必须写明缺的是什么依赖（Docker、GL 或数据），并计入 CI 的 skip 计数。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| lineage 差异比预想的大，新包和 10-05 包的 instruction 普遍不同 | step 4b 量化差异；结果交给你决定是否接受，或者为平台包保留旧 builder（§9 D1、D2） |
| 化学 0002 在 delivery 里自相矛盾 | 照最新 `review-v11` 安装，作为已知限制写进文档，引用这道题的分数时加注 |
| viewer vendor 涉及第三方许可证 | Mol*、niivue、three.js、OCCT 在 vendor 前逐个核对，补写 `NOTICE` |
| H20 上 `/nvme4` 已用 96%（约 137 GB 可用），完整构建一轮约 22 GB | 每轮构建前检查容量，构建完清理中间产物 |
| 隧道上传会卡死，`hf` 退出码不可信 | 用续传，以 ref + 文件数 + 哈希作为判据 |
| 别的会话同时在改 BV2 / SV | 只读这两个仓库，所有写操作都在目标仓库的独立 clone 里 |

## 9. 已定案的三项（2026-10-10，均按推荐方案）

- **D1 · 平台包的 MCP 布局：单容器 bundled。** 和 UniEval 已经导入、验证过的布局一致。代价是 agent 容器里能看到 eval 源码（不包括 GT），作为已知限制写进文档。Harbor 路由默认仍然是 client + worker。
- **D2 · 和 10-05 包的差异：用 contract diff 报告验收。** 每处差异都要归类，无法归类的必须为 0。数据侧实测是 11 道（§3.2），其余差异都来自代码 lineage。报告交审之后再发布。
- **D3 · `mvd-068`：从 `review-v11` 上删掉。** 这是 HF 写操作，执行前再确认一次。删完后数据 pin 改到新的 commit。

---

## 附录 A：eval-baselines 信息清单（2026-10-10 实测）

**BV2 `b47f13ac` 的规模**：2131 个条目，208 MB，大头是 `examples/` 88 MB、`QA-Gen/` 54 MB、`viewer/` 16 MB。eval 相关的部分：`scripts/harbor/` 150 个文件（含 78 个 `tasks/` 样例和 22 个模板）、`sci3d_agent/` 113 个、`harbor_adapters/` 26 个、`sci3d_bench/` 19 个、`scorer_bundle/` 10 个、`tests/` 166 个。

**import 闭包**（通用入口）：132 个 `.py`，排除下面 7 个后剩 **125 个**，目标路径就是在 `harness/` 下保持原相对路径。

排除的 7 个：`scripts/harbor/probe_irreducibility.py`、`scripts/harbor/_irreducibility.py`、`scripts/qa_qc.py`、`scripts/_qa_qc_mechanical.py`、`scripts/_qa_qc_report.py`、`scripts/_qa_qc_shortcuts.py`、`tools/__init__.py`（空文件）。

| 分组 | 数 | 文件 |
|---|---:|---|
| `harbor_adapters/` | 1 | `__init__.py` |
| `harbor_adapters/sci3d/` | 16 | `__init__.py`、`agent.py`、`verifier.py`、`route_codex_agent.py`、`_capability_policy.py`、`_capability_signatures.py`、`_instruction.py`、`_integrity_policy.py`、`_process_gate.py`、`_route_contracts.py`、`_score_core.py`、`_scorer_registry.py`、`_tool_names.py`、`_tool_outcomes.py`、`_translate.py`、`_verify_translate.py` |
| `sci3d_agent/` | 12 | `__init__.py`、`agent.py`、`config.py`、`dispatch.py`、`kernel.py`、`llm_client.py`、`mcp_bridge.py`、`modes.py`、`policy.py`、`trajectory.py`、`viewer_bridge.py`、`_tool_result.py` |
| `sci3d_agent/mcp/` 与子包 | 39 | `__init__.py`、`registry.py`、`server.py`、`surface.py`；`parity/{__init__,allowlist}.py`；`runtime/{__init__,cache,errors,paths}.py`；`tools/{__init__,catalog,inspect,pipeline_read,pipeline_write,render_static,urls}.py`；`tools/_render/{__init__,canonical_views,common,mesh,molecule,nifti,pointcloud}.py`；`tools/vision3d/{__init__,camera,segment,session,snapshot,state,_camera_math,_crosshair,_schemas,_segment_client,_slice_math,_snapshot_camera,_snapshot_render,_store,_types}.py` |
| `sci3d_agent/tools/` | 6 | `__init__.py`、`execute_code.py`、`finish.py`、`render_view.py`、`screenshot.py`、`set_camera.py` |
| `sci3d_bench/` | 5 | `__init__.py`、`ablation.py`、`cli.py`、`runner.py`、`task_schema.py` |
| `sci3d_bench/adapters/` | 3 | `__init__.py`、`base.py`、`sci3d_agent_adapter.py` |
| `sci3d_bench/scorers/` | 10 | `__init__.py`、`base.py`、`deterministic_answer.py`、`exact_match.py`、`extent_voxel_tolerance.py`、`llm_judge.py`、`mask_dice.py`、`nifti_io.py`、`refseg_text_judge.py`、`text_rubric.py` |
| `sci3d_bench/tasks/` | 1 | `__init__.py` |
| `scorer_bundle/` | 4 | `build_context.py`、`manifest.py`、`offline.py`、`scorer_cli.py` |
| `scripts/harbor/` | 25 | `_agent_envs.py`、`_citability.py`、`_codex_runtime.py`、`_e2e_resolve.py`、`_leak_probe.py`、`_mcp_route_runtime.py`、`_membership_rules.py`、`_multiview.py`、`_tolerance_rules.py`、`_viewer_link.py`、`aggregate_weighted.py`、`build_harbor_tasks.py`、`build_v21_ev2_trees.py`、`collect_qagen_results.py`、`completed_task_ids.py`、`eval_contracts.py`、`leak_scan.py`、`plot_leaderboard.py`、`qa_gen_to_tasks.py`、`rescore_rerun.py`、`route_contracts.py`、`run_end2end.py`、`security_contract.py`、`step_mesh.py`、`view_protocol.py` |
| `scripts/match/` | 2 | `__init__.py`、`build_matched_3d_qa.py`（只被 `pipeline_write` 用 importlib 引用） |
| `scripts/` | 1 | `__init__.py` |

**另外要带的非模块文件**：

| 来源 | 内容 |
|---|---|
| `scripts/harbor/templates/` | 8 个 Dockerfile：`Dockerfile`、`Dockerfile.codex`、`Dockerfile.codex-2d`、`Dockerfile.codex-mcp`、`Dockerfile.claude-code`、`Dockerfile.claude-code-2d`、`Dockerfile.claude-code-mcp`、`Dockerfile.sciencevision-mcp-worker`（最后这个**不进**保活 `CMD` 的追加和 glob 守卫，它以 `ENTRYPOINT` + `HEALTHCHECK` 结尾） |
| `scripts/harbor/templates/` | 9 个 `build_*_image.sh` + `build_image.sh`、`score.py`、`test.sh`、`mcp_worker_entrypoint.py`、`mcp_worker_healthcheck.py`、`claude_code_mcp.mcp.json` |
| `scripts/harbor/` | `codex_runtime.sh`（另外 7 个 `.sh` 是 scale_v1 / ev2 / sample50 的跑批脚本，不带） |
| `scorer_bundle/` | `Dockerfile`、`README.md`、`VERSION`、`build_image.sh`；`reports/v9_coverage.json` 含主机路径，**不带**；`audit_v9_coverage.py` 不在闭包里，**不带** |
| 镜像 `COPY` 的输入 | `datasets/_matched/`（`index.json`、`scanqa.json`、`embodiedscan-demo.json`、`sqa3d.json`、`m3d-refseg.json`、`m3d-seg-0005.json`，6.7 MB）、`viewer/assets.json` |
| 测试支撑 | `scripts/sam_service/`：`__init__.py`、`fake_server.py`、`synth_mask.py`（带上后 `tests/mcp` 从 34/35 变 35/35） |

**第三方依赖**（按引用文件数）：numpy、harbor、PIL、matplotlib、nibabel、gemmi、skimage、jupyter_client、httpx、anthropic、tomli、trimesh、OCP、fastmcp。

**测试**（按 import 是否全部落在带走的集合里判断，共 **117 个**）：

| 分组 | 带走 | 不带 | 不带的拦路依赖 |
|---|---:|---:|---|
| `tests/harbor_adapters/` | 43 | 5 | QC 链 3 个 + 跑批报告 3 个（`report_v21_ev2`、`report_sample50`、`report_ev2_threeway`） |
| `tests/mcp/` | 34 → **35** | 0 | 补上 `scripts/sam_service/fake_server.py` 后全部可带 |
| `tests/sci3d/` | 30 | 0 | |
| `tests/scorer_bundle/` | 1 | 0 | |
| `tests/scripts/` | 2 | 1 | `scripts.eval.build_dev_subset`（跑批专用） |
| `tests/` 根目录 | 5 | 5 | 带走 `test_data_indexes`、`test_docs_no_chinese`、`test_manifest_domain_taxonomy`、`test_mcp_pairs`、`test_medical_score`；不带 `test_docs_index`、`test_docs_search`、`test_manifest_schema`、`test_qagen_vnext_integration_boundary`、`test_sync_from_sciencevision` |
| `tests/__init__.py` | 1 | 0 | |
| `tests/offline_task_manager/` | 0 | 27 | 平台与标注代码整体不带 |
| `tests/viewer/` | 0 | 7 | viewer 只带 2D 采样子集，不带页面契约测试 |

`test_docs_no_chinese.py` 与 `test_manifest_domain_taxonomy.py` 按目标仓库的文档和 taxonomy 改写后再带；`test_medical_score.py` 依赖 `tests/sci3d/fixtures/`。

**清洗命中**（305 个文件，9.2 MB 文本，含带走的测试和 `docs/harbor`）：

| 类别 | 命中 | 说明 |
|---|---:|---|
| 私网 IP | 2 | 其中 `_DEFAULT_VIEWER_BASE` 是内网地址 |
| 主机路径 | 7（5 个文件） | |
| 网关名 | 42（13 个文件） | `llm_client` 的环境变量名和内部备注，以及 docs |
| `H20` | 15（9 个文件） | |
| `suermars/` | 13（5 个文件） | 由 image lock 替换 |
| 中文字符 | 862（6 个文件） | `ADAPTER_sci3d.md` 584、`viewer/assets.json` 124、`deterministic_answer.py` 52（归一化表） |
| 密钥形态 / 人名 | 0 | |
| email | 1 | 测试夹具 |

**H20 tree-builder 对比 `b47f13ac`**（`scripts/harbor` + `sci3d_bench` + `harbor_adapters`）：
- 76 个相同，27 个不同，`b47f13ac` 多 13 个，H20 多 4 个（不计样例树）。
- 两份 H20 tree-builder 之间也不一样，差在 `build_harbor_tasks.py` 和 `_agent_envs.py`，其中包括 scorer 镜像那一行。

## 附录 B：已有 v11 基线运行（只作索引，不迁移）

| 运行 | 模型 | route | 判分 | 位置 |
|---|---|---|---|---|
| HF 随数据附带的 runs | gpt-5.6-sol 215 题 / gpt-6-astra 34 / gpt-6.1-sol 1，汇自 7 个源 run | codex cc / ccmcp | 各源 run 当时自带的判分库 | HF `review-v11` 的 `<task>/runs/` |
| v11 报告页 | GPT 两模型 + claude-opus-5-5 | 3D 两路 | **两组判分构建不同**：GPT 组用仓库 verifier，opus 组用平台 scorer 镜像并按 1005 重判 | `SV-data/delivery/v11-250-20260929/v11_250_report.html` |
| UniEval 外部复现 | claude-opus-5-5 | cc / ccmcp / cc2d-6，共 750 trial | 按 1005 重判后 0.299 / 0.274 / 0.172 | `SV-data/qc-rework/unieval-repro-20261005/` |

## 附录 C：Pins

| 项 | 值 |
|---|---|
| 目标仓库 main | `e5571bd33e9a79c108cff6ee4508cff28ab05055` |
| BV2 `eval-baseline` | `b47f13ac5abb54b9e287ff3131ae7e29771ec53e` |
| SV 2D 截图管线 | `71045a1a3f77a23cf7b71d64cdd33c6f8570acd8`（首次截图 `cbcf2cd265c164b2bcc06dfd7f8a40f84ec826c4`） |
| SV 复现管线 | `2f0f86279c9a4076c3921b3a3cc00f637c7dcb38` |
| HF 数据 `review-v11` | `bc45c91aa6a0e4df0b270491f430ab17e1756e1d` |

| `v1.0.0` 目标 | GHCR manifest digest |
|---|---|
| `sciencevision-codex` | `sha256:43b4bf787e205762368aee65588b7a24bac748f267488769152209632efba04c` |
| `sciencevision-codex-2d` | `sha256:ac2043cf244040aefe71e0539dc01d32438061d5937f2c781d84b63451ee20aa` |
| `sciencevision-codex-mcp` | `sha256:97ddcbb6bb312176bae8a44f6564f65b6f83fdf381ba6136240bcf10c7e52e8e` |
| `sciencevision-codex-mcp-client` | `sha256:b4c03b16847e7f5d02a4c78e61c89e9f5fe896845663e532ee1f6ae2f8ae04fe` |
| `sciencevision-claude-code` | `sha256:e7e5ce2ce6393acc05f3165c3313b392e26501e29fff3e6fcd3f2f601f4075a5` |
| `sciencevision-claude-code-2d` | `sha256:c385efa0c471923da9a770c830602a0220d6fa72cbba1278879762a4633b358f` |
| `sciencevision-claude-code-mcp` | `sha256:ac9722518747ac0aae4ff52f8a9dacea5f03c23614d507903db83db98732cd59` |
| `sciencevision-claude-code-mcp-client` | `sha256:597cc446d579d0ccca961f0eed5b133d7925337fd50008594632117a8fec1b2a` |
| `sciencevision-mcp-worker` | `sha256:292e3770e3a7ffd3397f575b9282bd3d65db2a6e3a30ceae838666949a56eafa` |
| `sciencevision-scorer` | `sha256:b97ca59f66fb66838a9a5a5fcf7b51230df0ce0e21664523c52f1e3f02d4f1ee` |

## 附录 D：`BASELINES.md` 的内容去哪（2026-10-13 逐节读后定案）

`docs/harbor/BASELINES.md`（414 行）就是 eval baselines 的定义文档。它内容比"一半过期"复杂：**定义面**（评分和对照怎么成立的）整体仍然有效，**渲染面**（图怎么来的）也仍然有效但属于采样文档，真正过期的只是 task set、模型名和 `cc2d-views/2`。

### D.1 → `docs/evaluation.md`（定义面）

| 原文段落 | 搬什么 |
|---|---|
| Two orthogonal ablations | `cc → ccmcp` 是 MCP ablation（两张镜像 stage 同一份资产到同一个路径，差别**只有** MCP 是否注册）；`cc2d-1 → -3 → -6 → -12` 是 view-count ablation（同任务、同 renderer、同 seed pose，只有视角数变）。第三个对照：`cc`/`ccmcp` vs `cc2d-*` = 结构化 3D 访问 vs 纯像素 |
| Held constant | verifier 是单个 `harbor_adapters.sci3d.verifier:Sci3dVerifier`，两个 answer source：`_verify_translate.answer_json_to_trajview` 优先读 `sci3d_answer.json`，回退 harbor `trajectory.json` 的最后一条消息——**所有 route 走同一套 rails**，这是矩阵可比的前提 |
| Reward | deterministic `answer_score` = strict JSON schema validation + binary rubric + frozen weighted aggregation；process / tool-policy 信号独立上报，**永不改 leaderboard reward** |
| Judge | 默认判分路径**没有模型调用**。`llm_judge` / `process_judge` 只在任务显式声明时作为可选 `shadow_*` 诊断，**不能覆盖 `answer_score`** |
| Timeouts | `--agent-setup-timeout-multiplier 3.0`、`--agent-timeout-multiplier 2.0` |
| Measurement tolerances | 整表搬，见下 |
| Point-in-mask | `voxel_member_of_set`：区域以 bit-packed blob 随 rubric item 下发（`_membership_rules.py` 在 build 时打包，scorer 镜像保持 stdlib-only）；越界 = 答错；畸形区域 fail closed 为 `invalid_member_set`；`ground_truth.method == "radiologist_mask_membership"` 或 value 里给 `mask_path` + `mask_id` 触发 |
| Image plumbing | 三张镜像的差异表（**名字换成 `sciencevision-*`**）+ `--mcp-config` 警告 |
| Answer-visible release SOP | 冻结 label-blind 证据契约 → 技术闸 → 评审只看 instruction + PNG → 同一 deterministic rubric 判分；新家族逐条审，稳定后抽样 ≥10%；失败回滚整个家族的渲染策略；只有审过的 manifest 能进 `cc2d-visible` 表。**技术通过绝不可改写为 answer-visible**：通过时记 `visibility_status: technical_pass`、`answer_visibility_status: pending_blind_audit`、`blind_audit_status: required`，对外报 `cc2d-all`（PNG 通道下界） |
| Render exclusions are receipts | 闸失败的题 fail-closed 删树，但**留下收据**：`<task_id>.cc2d_excluded.json`（`failure_reason` = 闸的 code），跑批器把预期 code 汇成 tree-local `cc2d_exclusions.jsonl`；树审计接受"有解释的缺口"、拒绝无解释的缺口、拒绝与已建树矛盾的收据；真故障（`renderer_exception`、无 PNG、泄漏）仍然中止 |
| `3d-mcp` route | 是 `ccmcp` 的 worker-backed MCP 面 + Codex 关掉 shell tool（adapter 追加 `--disable shell_tool`），所以没有文件访问通路；要**launch 进一个已完成的六基座 jobs root**（freshness check 按 matrix 分域）；与 `job-ccmcp` 比对时任务树、worker 镜像、task set 逐项相同，所以 `ccmcp ↔ 3d-mcp` 隔离出的正是 shell tool 的贡献 |

**容差表**（`BASELINES.md` 的 §Measurement tolerances，逐行搬）：

| ground truth | band | 家族 |
|---|---|---|
| 任意书写精度的 float | `±0.005` | `contact_distance_A`、`distance_A`、`farthest_heavy_atom_distance_A`、`*_angle_deg` 等 |
| dihedral | `±0.005`，模 360 | `*dihedral*`，走 `circular_tolerance` |
| 测量向量里的 int | `±1` | 如 `void_ladder_bottom_up_mm` `[2530, 2481]` |
| `point_vox` / `bbox_min_vox` / `bbox_max_vox_inclusive` | 每轴 `±1` | |
| `extent_vox` | 每轴 `±2` | 两个 bbox 角之差，每个角各有一体素的歧义 |
| `representative_axial_index` | `±1` | |
| counts 与 identity set | **exact** | |

必须连同**理由**一起搬：容差不是测量不确定度。两边读同一个文件，没有仪器误差也没有抽样误差，唯一的不确定是选错了原子对或体素——而选错原子对会让距离移动 1–3 Å，比容差大两个数量级。容差存在的意义是吸收末位表示，不是接受近似。专家侧提过这条严格性把距离类问题分数压得很低，**这是预期行为，不是评分 bug**。

**三个故意不给容差的字段**（每个都是 *静默* 失败而不是报错）：

- **counts 和 identity set 完全不写容差条目。** 给整数字段声明容差会让 `_default_comparator` 越过 `isinstance(expected, int)` 分支，把 `integer_equal` 静默变成 float `abs_tolerance`。"容差为零"的写法是**省略这个 key**，永远不是写 `0`。真正需要这个转换的两个整数字段在 `_INT_TOLERANCE` 里逐个点名。
- **数值 list 必须有一条。** 没有容差的 list 字段不是被判严格，而是变成 `missing_tolerance:<field>` → `invalid_task_contract` → `eligible: false` → 无论 agent 答什么都 0.0。
- **不能数值比较的字段不要碰。** 字符串 list（`screenshots`）一旦挂上 band 就*变成 eligible*，然后每次都判错——把一个响亮的契约错误变成安静的 0。

### D.2 → `docs/sampling-2d.md`（只搬视角计划语义）

⚠️ **这一节容易搞错。** 旧文档的 `cc2d` 渲染面是 **offline pyrender/EGL** 那条路（`_multiview.plan_views` → `REGISTRY[tool].handler` → pyrender 出 PNG）。本设计的采样**走浏览器截图**（§5.4、§3.5），所以离线渲染器里的大量常数**不搬**——它们描述的是留在 MCP 工具里的那个渲染器，不是采样路径。

**搬（视角计划语义，与渲染器无关）：**

| 原文段落 | 搬什么 |
|---|---|
| The frozen view protocol | 命名面集合与它的嵌套：`-1` = `iso`；`-3` = `iso front top`；`-6` = `iso front top right back left`（= ccmcp 的 canonical 默认）；`-12` = 前六个 + `bottom` + 仰角 20°、方位角 30/90/150/210/270° 的五个 orbit fill。**aligned 档位的可比性就是从这条嵌套来的**，所以它必须逐字进文档 |
| 角度约定 | 角度是 **viewer-frame（Y-up）球面度**：`azimuth = atan2(d.x, d.z)`、`elevation = asin(d.y / \|d\|)`；每个 entry 声明 `angle_frame="viewer_y_up"`。Z-up 资产（`.obj/.ply/.stl/.off`、细分后的 CAD `.ply`）要把 orbit fill 的方位角 **−90°**——不位移的话 fill 偏 ~90°，这是修过的 bug。**取样必须和 viewer 自己的采样方向对齐**：`scripts/harbor/view_protocol.py`（规划器） = `_render/canonical_views.py` = viewer 自己的方向表，三者相等，有测试钉住 |
| Dispatch: modality decides the kind of view | 按 modality 决定"更多视角"是**更多相机角**还是**更多切片**——这张表在浏览器管线里等价于"打开哪个 viewer 页面"：`nii*` → 切片；`las/laz` → orbit；`obj/ply/stl/glb/gltf/off` 且 `domain != physics` → 命名面；`step/stp/iges/igs` → 先 `step_mesh.py` 细分，iges/igs fail closed 为 `step_format_unsupported`；`mmcif/cif/pdb/xyz/sdf/mol/mol2` → 分子命名面；physics 动画 / urdf / 裸 `dat`/`json` → ineligible。`domain != physics` 是故意的：physics 资产是 GLB rollout（动画），拿一帧做静态 orbit 会骗人 |
| 轴约定 | **sagittal = 0, coronal = 1, axial = 2**（numpy 数组序），repo-wide 锁定 |
| 升级结构 | 1 → 3 买的是**轴**（一个平面变三个），3 → 6 → 12 买的是**每轴的深度**。不是均匀的"更多同样的东西"，曲线两半要分开读 |
| Render exclusions are receipts | 收据机制照搬（`<task_id>.cc2d_excluded.json` 的 `failure_reason` + tree-local `cc2d_exclusions.jsonl`；树审计接受有解释的缺口、拒绝无解释的缺口、拒绝与已建树矛盾的收据） |
| Answer-visible release SOP | 冻结 label-blind 证据契约 → 技术闸 → 评审只看 instruction + PNG → 同一 rubric 判分；新家族逐条审，稳定后 ≥10%；失败回滚整个家族。**技术通过绝不可改写成 answer-visible**，对外报 `cc2d-all` |
| Reproducing / verifying a tree | 四行不变量照搬（`ls views/*.png`、`find render_manifest.json`、确认没有 `asset` 目录、`grep -L no_asset_access` 应无输出），命令换成新 CLI |
| 泄漏三性质 | `views/` 是唯一 stage 出来的东西；instruction 里**没有** `asset_path`（只给 `views` / `modality` / `no_asset_access: true`）；多资产业务题每个绑定资产都要出 N 张图 |

**不搬（离线 pyrender 专有，留在 MCP 渲染器里）：**

- Camera framing 常数（bbox 中心、`distance = max(diag × 1.4, 1e-3)`、fov 45°、`znear`/`zfar` 缩放，以及"pyrender 默认 0.05/100 把 11 个小 CAD 零件裁空白"）——浏览器管线的取景由 viewer 自己决定。
- 分子渲染器的 300 原子分界、8000 原子下采样、HETATM 保留——那是 pyrender 侧的 adaptive 规则，Mol\* 按结构完整渲染。
- Two resolution caveats（NIfTI 忽略 `size`、分子 394×394 是 matplotlib `bbox_inches="tight"` 裁的）——换成浏览器视口尺寸后不成立。
- The pipeline 图与两个 stager——采样不再经 `_stage_rendered_views`。

**已经从 `71045a1a` 的代码里读到的事实**（`_cc2d_plan.py`，写文档时直接用，不必再猜）：

- 旧命名面协议**就是**新的 aligned 计划：`CANONICAL = (iso, front, top, right, back, left, bottom)` 七个具名面在前，再接 `ORBIT_FILL = (30, 90, 150, 210, 270)` 五个方位角、`ORBIT_ELEV = 20`，凑满 12。`orbit_plan()` 是 12 个 30° 等分方位角，同样仰角 20。
- **两族的嵌套规则不同，且这个区别是故意的。** canonical 按**前缀**嵌套（`range(tier)`），因为具名面是按"能加多少信息"排序的；orbit 在**每一档都保持方位角等分**（`stride = 12 // tier`，取 `i × stride`），所以 `cc2d-3` 是 `cc2d-6` 的子集但**不是它的前三张**——orbit 用前缀规则会让 `cc2d-6` 只看模型的一半，严格更差。两族的**角度**档位都是嵌套的。
- **页面路由取代旧的 renderer 分派**：`.nii`/`.nii.gz` → `niivue.html`；`.scene.json` → `architecture_inspection.html`；分子类 → `molstar.html`；`glb/gltf/obj/ply/stl/off` → `gltf.html`；`step/stp/iges/igs` → `occt.html`。**名字里带 `mask` 的文件永远不进这条路由**——分割是叠加层，不是切片来源；任务同时给两者时，viewer 打开的不是 mask。
- **`.cif` 按内容判定，不按扩展名**：`.cif` 一名两制。生物是 mmCIF（atom 键带点，`_atom_site.label_atom_id`），化学小分子晶体是 CIF（键是平的 `_atom_site_fract_x`，另带晶胞和对称操作）。Mol\* 分别用 `mmcif` 和 `cifCore` 读，给错了**一个结构都不加载**，而且只在请求截图时才报。**24 道题（全是晶体）就是这么挂的**；viewer 本身没错，`?type=cif` 已经能选 cifCore，是驱动按扩展名断言了 mmCIF。
- **空帧闸的阈值是实测的**：`DRAWN_FLOOR_PCT = 0.1`、`MODAL_DELTA = 4`。合成纯色图 0.00%，见过的最差真实帧（从底下看的壳体零件，背面剔除把大部分遮掉了）0.66%，正常视角 11–32%。
- **任务树不统一资产位置**：有的在 `assets/`，机械 CAD 在 `assets/<task_id>/` 再嵌一层，平台导出的 architecture 直接把场景丢在任务目录里、根本没有 `assets/`。只查 `assets/` 会把最后一类报成"没有几何"。
- **切片索引来自专家实际设过的位置**，按轴记；某一档要的比专家给的多时，再按体积铺开补齐。实现里有 `expert_positions`、`answer_indices`、`answer_point`、`mask_axis_points`、`nudge`、`distinct_free` 一组函数专门做**避让**：切片索引不能落在答案坐标或专家访问位置上。
- 已有树若不是本管线写的，先 clone 到一边；备份失败的树**原样保留**，不覆盖。

**仍然要在 pinned 数据上实测才能写：**

- **切片的档位嵌套**：角度两族都嵌套，切片不是（医学切片覆盖规则在 SV 侧重做过）。§5.4 要求公布不嵌套的题号，这条只能实测。
- 医学 aligned 与 orbit 的合成图字节相同（只有一半信号）——按 pinned 数据复核。
- `render_manifest.json` 的 `scheme` 从 `"cc2d-views/2"` 改成 `"cc2d-views/3"`（代码里已经是 `SCHEME = "cc2d-views/3"`），`ANGLE_FRAME = "viewer_y_up"` 与逐视角名字保留。

### D.4 采样侧代码清单（`71045a1a`，未合并）

| 文件 | 作用 |
|---|---|
| `scripts/harbor/_cc2d_plan.py`（551 行） | 纯计划：路由、视角、档位嵌套、切片避让、空帧判定 |
| `scripts/harbor/_cc2d_browser.py` | playwright + Chrome 的取景/抓拍/恢复，`ViewerServer`、`aim`、`check_aim`、`capture` |
| `scripts/harbor/cc2d_capture_views.py` | CLI，把两棵树写进 dataset，`--workers` / `--shard` |
| `scripts/harbor/cc2d_verify_views.py` | 校验已存在的树 |
| `tests/harbor_adapters/test_cc2d_capture_views.py` | 测试 |

这 5 个文件目前**不在 main 上**，只在 `feat/cc2d-medical-coverage`。`--plan-only` 这个模式代码里**还没有**，PR 3 要加：只跑 planner、不启动浏览器，用来做逐字节闸。

### D.3 丢掉（真正过期的）

- **六基座标签本身**：`cc`/`ccmcp`/`cc2d-N` 的 route 名保留（结果记录和 CLI 用 route），但对外矩阵改成 §5.4 的 7 个论文 setting；`host` 降为补充对照。
- **`--model claude-opus-5`**：换成本次 run 实际 pin 的模型。
- **scale-v1 = 1567 / sample50 = 150 / 正式分母 130 / 780 trials**：v11 是 250 题，分母按题集重算。原文里"20 道 docking 记录 `needs_expert_annotation` 所以排除"的段落整段过时。
- **"generation MUST run in the mcp/GL env (pyrender + EGL)，不是 harbor venv"**：在新设计里采样是**可选前置步骤**，评测侧绝不渲染（§5.4）。这句话改写成采样的前置条件，不再作为评测的前提。
- **`build_sample_trees.py` 的 `cp -al` 硬链接优化**：不在闭包里，是跑批专用脚本，不提。
- **`HARNESS_COMPARISON.md` 引用**：该文件已不在 `docs/harbor/`，是悬空引用，不搬。
- **v9 特有的医学口径**：原文说"v9 50 道医学题里只有 25 道 `med_ct_geometry` 带 `mask.nii.gz`，点答题跑在没有 mask 的原始 CT 网格上"、"14 道散文 GT 是 `evaluation_ready: false`"。这两条要在 v11 数据上**重新测**再写，不能照抄。
