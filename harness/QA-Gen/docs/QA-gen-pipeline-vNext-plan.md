# ScienceVision QA-Gen vNext 评审方案

> 状态：设计评审稿。本文定义 MVP 的目标、边界、主流程和文件结构。新包独立位于 `QA-Gen/qa_gen_pipeline/`；不修改 `ScienceVisionTaskGenerator`，也不复刻旧的 Builder/Challenger/Refiner 架构。

## 评审时只需确认的 5 个决定

### 1. 试点范围

**推荐值：** MVP 发布只覆盖 biology、chemistry。medical 仍完成 config/profile 编译，但保持 `blocked`；只有至少一个 medical seed 修复并重新经专家审核为 `verified`，才可纳入后续发布。每个 sub-domain 必须显式配置；没有合格 seed、asset 或 viewer 能力时不以泛化题目填充配额。

### 2. 真值与计算边界

**推荐值：** 默认不计算。solver 只使用受控 viewer，题目真值只允许 `visual_annotation`、`expert_visual_judgment`、`viewer_native_measurement`。测量题只能调用 viewer 原生距离、角度、二面角或医学长度/角度工具。

作者侧可在必要时声明 `author_compute_exception`，但它不是 solver 能力或题目真值模式。例外必须经审批，并产出 `AuthorTruthReceipt`：冻结输入和 asset digest、代码与环境 digest、输出、审批记录、独立 viewer evidence。它只能辅助建立或交叉核验参考答案，且绝不进入 solver bundle；题目仍必须可由受控 viewer 独立作答。

### 3. seed case 的作用

**推荐值：** seed 是私有的能力样例、反例和覆盖约束，不是可改写的题目模板。作者只看到脱敏 author card；原题、答案、asset 路径、真实 ID 和 reviewer 对话不得进入作者或 solver 上下文。

### 4. 人工 gate 的位置

**推荐值：** MVP 有四个常规、不可绕过的人类 gate：知识负责人的 `KnowledgeCompileAuthorization`、领域专家的 `KnowledgeReviewDecision`、`PlanApprovalDecision` 和 candidate 的 `DomainReviewDecision`；首次导入或版本变化时，额外触发条件式 `SourceVersionResolution` gate（通过 `qa-gen seed accept-version` 形成 receipt）。前两个都在步骤 1 内，不能因其未出现在主图的独立框中而省略；自动验证与 rubric dry-run 必须先通过。只有新增 rubric family、测量规则或高风险发布时，才加独立 scorer review。

### 5. 什么算发布

**推荐值：** 发布必须生成隔离的 solver、scorer、audit 三个 bundle。solver 只获得题目、匿名 scene 和稳定的受控 viewer session request；作答时服务端才按该 request 动态签发短期 scope。答案、seed、真实 asset 身份和内部知识不进入 solver bundle。

## 目标与非目标

**一句话目标：** 生成必须通过受控 3D viewer 才能可靠作答、且带有参考解、解题依据和 rubric 的科学 QA。

本方案不做以下事情：

- 不发布能由原始文件、元数据、asset ID、联网搜索或任意计算直接求解的题。
- 不把旧 pipeline 的 task schema、默认 planner 或历史题目直接迁入新系统。
- 不让 agent 决定配置、发布或自身工具；agent 只在已锁定的任务合同内工作。
- 不为覆盖率启用缺少 seed、asset、viewer 或 rubric 前提的 sub-domain。

## 六步主流程

```mermaid
flowchart LR
  A[资料同步\n知识授权+专家 gate] --> B[配置编译] --> C[计划确认\n人工 gate] --> D[产题取证] --> E[验证评审\n人工 gate] --> F[隔离发布]
  E -- revise --> D
```

1. **资料同步**：并列运行 Seed Registry 与 Knowledge Registry：前者同步、审核和索引 seed；后者先取得 `KnowledgeCompileAuthorization`，再把专家知识、3D visual taxonomy 与脱敏 seed lessons 编译为 draft，并经领域专家 `KnowledgeReviewDecision` 冻结为 sub-domain 本地知识。两者都生成固定 digest、本地快照与可用性报告。
2. **配置编译**：组合 base、domain、sub-domain、run profile、rubric lock 和 viewer profile，输出可执行配置与 coverage matrix。
3. **计划确认**：确定 work item、seed 选择、预算和禁用项；产生 `PlanApprovalDecision` 后才可调用产题、取证、验证、改写 agents。`knowledge_curator` 是唯一允许在 plan 前运行的 agent，但必须具备独立 `KnowledgeCompileAuthorization`。
4. **产题取证**：orchestrator 根据已批准 plan、匿名 scene registry 和 viewer profile，通过 `viewer/session.py` 申请作者受控 session；author、evidence worker、rewriter 只能使用该 session。每次调用记录时间、token 和成本。
5. **验证评审**：自动检查 schema、rubric、视觉必要性、viewer evidence 和测量证据；通过后生成领域 review packet，失败只回到一次 revise 回路。
6. **隔离发布**：review 批准后在私有运行目录构建 solver、scorer、audit bundle；只有通过 leakage check 的稳定 solver/scorer bundle 可显式导出。

主图不展开终态：review 可为 `approved`、`revise`、`rejected` 或 `quarantined`。`rejected` 对该 candidate 终止；`quarantined` 表示来源、asset、viewer 或策略前提未满足。`qa-gen run --resume` 必须重验 source、seed、knowledge、rubric、config、asset、viewer、plan 全部 digest；任一 lock `superseded` 或 digest 不匹配即使旧 plan 失效，必须重新 plan/approve。

## 新包文件结构

```text
QA-Gen/qa_gen_pipeline/                                      # [MVP] 新 pipeline 独立根目录
|-- README.md                                                 # [MVP] 安装、运行、术语和试点边界
|-- pyproject.toml                                            # [MVP] Python 包、CLI 与依赖定义
|-- .gitignore                                                # [MVP] 忽略 `.qagen/runs/`、私有快照、缓存和回执
|-- configs/                                                  # [MVP] 版本化的声明式配置根目录
|   |-- base.yaml                                             # [MVP] 共用安全、预算、真值和质量下限
|   |-- profiles/bio_chem_release.yaml                        # [MVP] biology/chemistry 发布试点的配额、模型路由和阈值
|   |-- profiles/medical_compile.yaml                         # [MVP] medical 仅编译和预检，禁止发布的 profile
|   |-- sources/ev2_seed_source_spec.yaml                     # [MVP] EV2 SourceSpec：root_env、file-role map、inventory 与 version resolution 引用
|   |-- sources/knowledge_sources.yaml                        # [MVP] 专家知识和视觉 taxonomy 来源清单
|   |-- sources/revision_intake.yaml                          # [MVP] 既有题 revision 的只读来源和 digest 清单
|   |-- sources/asset_manifest.yaml                           # [MVP] asset manifest 与闭包检查入口
|   |-- sources/viewer_definitions.yaml                       # [MVP] viewer build、服务端和能力定义入口
|   |-- domains/biology.yaml                                  # [MVP] 生物学共享规则和默认 viewer 能力
|   |-- domains/chemistry.yaml                                # [MVP] 化学共享规则和默认 viewer 能力
|   |-- domains/medical.yaml                                  # [MVP] 医学共享规则和默认 viewer 能力
|   |-- subdomains/biology/docking_pose_plausibility.yaml     # [MVP] docking pose 的题型、证据和禁用条件
|   |-- subdomains/biology/molecular_collision_check.yaml     # [MVP] 局部 collision 的题型、证据和禁用条件
|   |-- subdomains/chemistry/stereochemistry_chirality.yaml   # [MVP] 手性视觉判断 leaf
|   |-- subdomains/chemistry/conformation_analysis.yaml       # [MVP] 构象分析和原生二面角测量 leaf
|   |-- subdomains/chemistry/steric_accessibility.yaml        # [MVP] 空间位阻与可接近性 leaf
|   |-- subdomains/chemistry/coordination_chemistry.yaml      # [MVP] 配位几何 leaf
|   |-- subdomains/chemistry/crystallography_packing.yaml     # [MVP] 晶体堆积 leaf，可初始标为 blocked
|   |-- subdomains/chemistry/reactive_conformation.yaml       # [MVP] 反应构象 leaf，可初始标为 blocked
|   |-- subdomains/medical/lesion_localization.yaml           # [MVP] 病灶定位和范围 leaf
|   |-- subdomains/medical/anatomical_region.yaml             # [MVP] 解剖区域识别 leaf
|   |-- subdomains/medical/imaging_feature_assessment.yaml    # [MVP] 可见影像特征判断 leaf
|   |-- rubric.yaml                                           # [MVP] 固定 Question-Rubric 摘要、阈值和版本
|   |-- schemas/base.schema.json                              # [MVP] 基础安全、预算和真值配置校验
|   |-- schemas/domain.schema.json                            # [MVP] domain 配置校验
|   |-- schemas/profile.schema.json                           # [MVP] run profile 配置校验
|   |-- schemas/source.schema.json                            # [MVP] source 和版本解决记录校验
|   |-- schemas/subdomain_knowledge.schema.json               # [MVP] 知识快照、来源和审核绑定校验
|   |-- schemas/viewer.schema.json                            # [MVP] viewer definition 与 capability 校验
|   |-- schemas/subdomain.schema.json                         # [MVP] leaf fail-closed 校验规则
|   |-- schemas/rubric.schema.json                            # [MVP] rubric digest 与阈值校验
|   |-- schemas/task.schema.json                              # [MVP] candidate 和 solver task 校验
|   `-- schemas/review_decision.schema.json                   # [MVP] plan/review 决定和状态校验
|-- knowledge/                                                 # [MVP] 可提交、可评审的本地知识 source of truth
|   |-- README.md                                              # [MVP] 知识分层、审核和更新约定
|   |-- source-manifest.yaml                                   # [MVP] 本地镜像与私有原始快照的来源/digest 映射
|   |-- sources/                                               # [MVP] 归一化且可审阅的专家/taxonomy 本地镜像
|   |   |-- expert/biology.md                                  # [MVP] 生物专家知识镜像
|   |   |-- expert/chemistry.md                                # [MVP] 化学专家知识镜像
|   |   |-- expert/medical.md                                  # [MVP] 医学专家知识镜像
|   |   `-- taxonomy/visual-taxonomy.md                        # [MVP] 3D visual taxonomy 镜像
|   |-- seed-lessons/                                          # [MVP] 按 leaf 脱敏沉淀的 seed 经验
|   |-- compiled/                                              # [MVP] 专家批准后的 SubdomainKnowledgeSnapshot
|   `-- index.yaml                                             # [MVP] 唯一可被 config build 解析的 approved 索引
|-- scripts/qa-gen                                             # [MVP] 只转发到 Python CLI 的薄 shell wrapper
|-- scripts/run-pilot                                          # [MVP] 只串联试点 CLI 命令，不含业务逻辑
|-- scripts/check                                              # [MVP] 只运行配置、bundle 和测试检查，不含业务逻辑
|-- docs/reference-map.md                                      # [MVP] 指向既有文档、旧实现和 task 数据的所有权映射
|-- src/qagen/                                                 # [MVP] 可测试的业务实现包
|   |-- cli.py                                                  # [MVP] 用户级命令的入口、参数和退出码
|   |-- contracts.py                                            # [MVP] Source、SeedCard、Plan、TaskContract 等数据合同
|   |-- paths.py                                                # [MVP] 工作区、私有缓存和发布路径解析
|   |-- sources/inventory.py                                   # [MVP] 扫描所有来源并生成 immutable inventory
|   |-- sources/ev2_importer.py                                # [MVP] 读取 EV2 manifest、tasks 和 provenance
|   |-- sources/knowledge_importer.py                          # [MVP] 只获取并冻结知识原始导出，不解释内容
|   |-- sources/revision_importer.py                           # [MVP] 将既有题只读转换为新 CandidateContract
|   |-- sources/asset_manifest.py                              # [MVP] 校验 asset 文件闭包和 digest
|   |-- sources/viewer_definition.py                           # [MVP] 冻结 viewer build、服务与能力定义
|   |-- sources/validator.py                                   # [MVP] 校验版本、专家状态、缺口和来源完整性
|   |-- sources/snapshot.py                                    # [MVP] 写入来源快照和 SourceVersionResolutionReceipt
|   |-- knowledge/contracts.py                                 # [MVP] compile authorization、snapshot、review 和 freeze 合同
|   |-- knowledge/normalizer.py                                # [MVP] 将原始文档转换为稳定章节/术语引用
|   |-- knowledge/seed_lessons.py                              # [MVP] 从私有 seed catalog 生成脱敏 lesson set
|   |-- knowledge/compiler.py                                  # [MVP] 编译每个 leaf 的知识 draft 与 scenario cards
|   |-- knowledge/validator.py                                 # [MVP] 校验 schema、引用、范围和输入 digest
|   |-- knowledge/review.py                                    # [MVP] 生成并校验 KnowledgeReviewDecision
|   |-- knowledge/freezer.py                                   # [MVP] 冻结已批准快照并生成 receipt
|   |-- knowledge/index.py                                     # [MVP] 写入和解析 approved 知识索引
|   |-- seeds/normalizer.py                                    # [MVP] 将原始 seed 统一为 private catalog
|   |-- seeds/author_cards.py                                  # [MVP] 生成正向、repair 和 anti-pattern 脱敏卡片
|   |-- seeds/selector.py                                      # [MVP] 按 leaf、状态和配额稳定选择正向 seed
|   |-- config/loader.py                                       # [MVP] 加载层级 YAML 与本地知识引用
|   |-- config/compiler.py                                     # [MVP] 编译配置、输出 lock 和 fail-closed 错误
|   |-- config/rubric_lock.py                                  # [MVP] 固定 Question-Rubric.md digest 与发布阈值
|   |-- config/coverage.py                                     # [MVP] 输出 enabled、blocked 及原因的覆盖矩阵
|   |-- viewer/profile.py                                      # [MVP] 探测 viewer 版本、场景和原生能力
|   |-- viewer/scene_registry.py                               # [MVP] 建立真实 asset 到匿名 scene ref 的服务端映射
|   |-- viewer/session.py                                      # [MVP] 生成稳定 session request，服务端动态发放短期 scope
|   |-- viewer/projection.py                                   # [MVP] 强制操作白名单、匿名化和元数据过滤
|   |-- viewer/evidence.py                                     # [MVP] 记录截图、trace、标注和原生测量证据
|   |-- viewer/probes.py                                       # [MVP] 检查 ID、文件名、网络和元数据捷径
|   |-- planning/planner.py                                    # [MVP] 从 compiled config 创建可审核 work plan
|   |-- planning/approval.py                                   # [MVP] 绑定 plan/config/profile 的 PlanApprovalDecision
|   |-- planning/task_contracts.py                             # [MVP] 固化每个题目的证据、rubric 和权限边界
|   |-- pipeline/orchestrator.py                               # [MVP] 调度产题、取证、验证、review 和 resume
|   |-- pipeline/workers.py                                    # [MVP] knowledge_curator、author、evidence、verifier、rewriter 的受限接口
|   |-- pipeline/prompts.py                                    # [MVP] 仅装配 TaskContract、author card 和 viewer session
|   |-- pipeline/usage.py                                      # [MVP] 记录模型、token、成本、耗时和失败原因
|   |-- truth/author_compute.py                                # [MVP] 执行已审批的 author_compute_exception
|   |-- truth/receipt.py                                       # [MVP] 生成和校验 AuthorTruthReceipt
|   |-- verification/verifier.py                               # [MVP] 汇总自动验证并返回状态与 revise 原因
|   |-- verification/rubric.py                                 # [MVP] 检查参考解、reasoning、字段和 rubric dry-run
|   |-- verification/native_measurement.py                     # [MVP] 校验单位、容差、trace 和测量截图
|   |-- review/packet.py                                       # [MVP] 生成可读的领域 review packet
|   |-- review/decision.py                                     # [MVP] 读取 approved、revise、rejected、quarantined 决定
|   |-- release/bundles.py                                     # [MVP] 分别构建 solver、scorer、audit bundle
|   |-- release/leakage_check.py                               # [MVP] 扫描 solver bundle 中的泄漏内容
|   `-- storage/run_store.py                                   # [MVP] 在私有 `.qagen/runs/` 写入 manifest、events 和 usage
|-- tests/                                                     # [MVP] 面向关键风险的回归测试目录
|   |-- test_seed_import.py                                    # [MVP] EV2 分布、脱敏和版本解决测试
|   |-- test_knowledge_compile.py                              # [MVP] 授权、知识编译、专家 gate、冻结和过期测试
|   |-- test_knowledge_lock.py                                 # [MVP] approved index 与 leaf lock 的 fail-closed 测试
|   |-- test_config_compile.py                                 # [MVP] 继承、rubric lock、缺字段和 blocked leaf 测试
|   |-- test_viewer_policy.py                                  # [MVP] 匿名 scene、受控 session 和捷径阻断测试
|   |-- test_author_truth.py                                   # [MVP] author_compute_exception 和 receipt 隔离测试
|   |-- test_plan_review.py                                    # [MVP] approve、review 状态和 resume 测试
|   |-- test_pipeline_smoke.py                                 # [MVP] 小规模端到端产题和 revise 测试
|   `-- test_release_leakage.py                                # [MVP] 三 bundle 隔离测试
`-- .qagen/                                                    # [运行时] 包内且由 `.gitignore` 覆盖的私有工作区
    |-- runs/                                                  # [运行时] 每次运行的私有 candidates、evidence、review 和 staging bundle
    `-- private/                                               # [运行时] 仅本机可读的私有 registry 工作区
        |-- snapshots/                                         # [运行时] seed、asset、viewer 及原始导出冻结快照
        |   `-- knowledge/raw/                                 # [运行时] 不可提交的飞书原始导出和附件
        |-- catalog/                                           # [运行时] 原始 private seed catalog 与状态报告
        |-- cards/                                             # [运行时] 脱敏 author cards 与选择索引
        |-- knowledge-drafts/                                  # [运行时] 未经专家批准的知识草稿和 review packet
        |-- profiles/                                          # [运行时] viewer capability profile 与匿名 scene registry
        `-- receipts/                                          # [运行时] source、knowledge、plan、truth、review 和验证回执
```

## 本地 source of truth、内容沉淀与所有权

本地资料分三层：`knowledge/sources/`、`knowledge/seed-lessons/`、`knowledge/compiled/` 与 `knowledge/index.yaml` 是人类可读、可提交、可 review 的知识沉淀；`.qagen/private/snapshots/knowledge/raw/`、`knowledge-drafts/`、`receipts/` 和 `.qagen/runs/` 是私密或不可提交的原始导出、草稿、回执和运行材料；仅通过 release 显式导出的稳定 bundle 位于 `QA-Gen/tasks/qa-gen-vnext/<release-id>/`。`config build` 只读取 `knowledge/compiled/` 和 `knowledge/index.yaml` 中的 fresh approved 内容，不读取 raw、draft 或外部页面。

既有内容均原地保留为 canonical/reference owner。新包只通过 `docs/reference-map.md`、`knowledge/source-manifest.yaml` 或 `configs/sources/*.yaml` 记录路径、用途和 digest；不复制、不覆盖，也不把原有任务数据迁入新包。

| 既有 owner | 在 vNext 中的定位与引用方式 |
|---|---|
| External PR #221: `QA-Gen/docs/QA-gen-package-pipeline.md` | 外部 package/pipeline 设计提案 owner，非当前提交；不假定该文件已在 `main` 存在，不迁入新架构。 |
| `QA-Gen/docs/QA-gen-pipeline-target.md` | 原始需求 canonical owner；由 vNext plan 和 reference map 引用。 |
| `QA-Gen/docs/QA-gen-pipeline-vNext-plan.md` | 新 pipeline 的设计 canonical owner；只定义目标架构，不承载运行状态。 |
| `QA-Gen/docs/Question-Rubric.md` | rubric canonical owner；由 `configs/rubric.yaml` 以 digest 锁定。 |
| `QA-Gen/docs/PIPELINE_V2.md` | 旧流程 reference owner；只提炼可验证经验。 |
| `QA-Gen/docs/LLM_ROUTING_PROGRESS.md` | 既有模型路由记录 reference owner；新 profile 只引用已确认结论。 |
| `QA-Gen/docs/ScienceVision_QA_Generation_Blueprint.md` | QA 生成蓝图 reference owner；不复制为新的 prompt 或 config。 |
| `QA-Gen/docs/ScienceVision_QA_Generation_Modification_Plan.md` | 历史修改计划 reference owner；不作为执行状态机。 |
| `QA-Gen/ScienceVisionTaskGenerator/{README.md,configs/**,schemas/**,docs/mechanical_cad/drawing_feature_region_mapping.md,scripts/EXPERT_REVIEW_PACKET.md}` | 原实现、配置/schema、机械 CAD mapping 和专家 review packet 的 reference owner；`ScienceVisionTaskGenerator` 仅供实现经验参考，绝不是新架构基座。 |
| `QA-Gen/TaskGenerator/` | 旧 TaskGenerator 的 canonical owner；保留、只读参考、不改写。 |
| `QA-Gen/tasks/**/{assets,_inputs,_internal_assets,_gt,qc}/` | 既有 asset、输入、私有材料、ground truth 和 QC 的 canonical owner；新 source manifest 仅引用经允许的来源和 digest。 |

## 每次运行的可审查产物

```text
QA-Gen/qa_gen_pipeline/.qagen/runs/<run-id>/                    # [运行时] 不可提交的一次可复查产题运行
|-- run-manifest.json                                           # [运行时] 全部锁定输入、代码版本、预算和运行摘要
|-- plan/                                                       # [运行时] coverage、work items、approval 和 digest 重验记录
|-- candidates/                                                 # [运行时] author 或 rewriter 的候选版本
|-- evidence/                                                   # [运行时] viewer trace、截图、标注和测量回执
|-- author-truth/                                               # [运行时] 私有 AuthorTruthReceipt，永不导出
|-- verification/                                               # [运行时] 自动验证、rubric dry-run 和 revise 原因
|-- review/                                                     # [运行时] 领域 review packet 与 decision
|-- release-staging/                                            # [运行时] leakage check 前后的私有 solver/scorer/audit bundle
|-- events.jsonl                                                # [运行时] 阶段、输入输出和错误事件
`-- usage.jsonl                                                 # [运行时] token、成本、耗时和模型路由记录
```

`qa-gen release` 只把通过 leakage check 的稳定 solver/scorer bundle 显式导出到 `QA-Gen/tasks/qa-gen-vnext/<release-id>/`；真实 asset mapping、`AuthorTruthReceipt` 和 audit bundle 永远留在上述私有目录或外部受控位置，不进入 git 导出。solver bundle 不包含 token；作答时 viewer 服务根据稳定 session request 动态签发短期、最小权限 scope token，且 token 不携带真实 asset ID、路径、答案或可扩权信息。

## Seed Registry：source sync 与 seed 加载

`qa-gen seed sync` 只读取提交的 `configs/sources/ev2_seed_source_spec.yaml` SourceSpec 与显式本地包；它不再隐式承担知识编译。SourceSpec 不提交绝对路径，只声明 `root_env: QAGEN_EV2_SOURCE_ROOT`；CLI 的 `--package-root` 必须与该环境变量解析出的目录完全相同。内部执行 inventory、validate、snapshot、normalize、index。

1. SourceSpec 锁定 `expected_basename`、`package_id`、显式 `file_roles`、case 数、review 分布、task/asset provenance 与 `expected_inventory_digest`。`file_roles` 映射 package manifest、version note、task JSONL、expert-review、asset manifest 等角色到相对路径和格式，importer 只经该映射读取，不再硬编码 `MANIFEST.json`、`README.md` 或 task 字段位置；所有角色路径必须在包内且不得为 symlink。
2. `ev2_local_package_v1` 以其 canonical inventory 算法计算目录的稳定 digest；该值必须等于 SourceSpec 的 `expected_inventory_digest`。因此 SourceSpec 同时锁住“解释这些文件的方式”和“这批文件本身”，换包、改包或改 role layout 都会 fail-closed。
3. 当前真实包的 SourceSpec 预期 basename 为 `ev2-pilot-sample-cases-by-type-20260820-v1.0.3-dsstore-fix`，manifest package ID/version 为 `...v1.0.2` / `1.0.2`，并锁定 15 个 case 和 `3 verified / 2 minor_fix / 6 factual_error / 4 unscorable`。只有 verified 可直接生成正向 author card；minor_fix 必须修复并复审后才可升级；其余只进入 repair、anti-pattern 或 blocked 记录。
4. 首次 sync 即使 SourceSpec 和 inventory 均匹配，仍会因目录标签 `v1.0.3` 与 manifest/version-note 标签 `v1.0.2` 写入 pending `SourceVersionResolutionRequest` 并停止，不生成 release-eligible index。`qa-gen seed accept-version` 的 receipt 必须同时绑定 SourceSpec digest、observed labels 和 observed inventory digest；它只解决这一次版本命名冲突，不能授权不同目录、不同 inventory 或更新后的 SourceSpec。
5. receipt 匹配后，normalize 确定性写入 `knowledge/seed-lessons/<domain>/<subdomain>.yaml` 并绑定 seed catalog digest：正向 lesson 只来自 `verified` seed；`minor_fix`、`factual_error`、`unscorable` 只产生脱敏 anti-pattern lesson。无 verified 正向 lesson 的发布 leaf 必须保持 `blocked`；`author_cards.py` 同时删除原题、答案、真实路径、hash、ID 和 reviewer 对话。

### 既有题 revision intake（仍属于受控输入）

已有题只能通过 `configs/sources/revision_intake.yaml` 加 `qa-gen plan create --revision-manifest <path>` 进入；`sources/revision_importer.py` 读取锁定的只读快照，保留 `legacy_task_ref`、`legacy_task_id`（task-id）和 provenance，再转换为新的 `CandidateContract`。它必须重走当前 knowledge/viewer/rubric/anti-hack 验证，不导入、不调用旧 pipeline；既有 `tasks/**` 仍只是输入 owner/reference。

## 专家知识与 3D visual taxonomy 编译（仍属于步骤 1：资料同步）

Knowledge Registry 与 Seed Registry 对称：Seed Registry 产出可选择的私有 seed card/lesson；Knowledge Registry 产出每个 leaf 唯一可消费的已批准知识快照。它是“资料同步”的内部 lifecycle，不新增第七个主流程节点。

```text
raw expert docs + raw visual taxonomy + normalized seed lessons
  -> snapshot / normalize -> KnowledgeCompileAuthorization -> compile draft -> validate
  -> domain-expert decision -> freeze -> index -> knowledge.locked_refs
```

四类输入合同如下：

1. `ExpertSourceSnapshot`：`source_id`、来源 URL/token、导入时间、内容 digest 和稳定章节定位；运行时只能读取已冻结本地版本，不能读取 live 飞书页面。
2. `VisualTaxonomySnapshot`：taxonomy item ID、定义、适用 viewer 行为、内容 digest；它是编译器的锁定输入，而非一个未绑定的附属输出。
3. `SeedLessonSet`：由 `qa-gen seed sync` 在 normalize 后确定性生成的 `knowledge/seed-lessons/<domain>/<subdomain>.yaml`；正向 lesson 只来自 `verified`，其余状态只保留脱敏 anti-pattern。它绑定 seed catalog digest，不含原题、答案、asset ID、路径或 reviewer 对话；只保存 opaque lesson/card ID，且不单独审批，随最终 snapshot 的 `KnowledgeReviewDecision` 一并批准。
4. `KnowledgeCompileRequest`：目标 `domain/subdomain/leaf`、上述三个输入 digest、知识 schema digest 和 compiler template digest。

`KnowledgeCompileAuthorization` 由知识负责人签发并存为私有 receipt，绑定全部 input digests、允许的 worker/model、最大预算、负责人和时间；它只授权生成 draft，绝不等于领域专家批准。`qa-gen knowledge compile` 在缺少、过期或不匹配的授权时 fail-closed。`knowledge sync` 先写入 `.qagen/private/snapshots/knowledge/raw/`，再将可提交的归一化镜像写入 `knowledge/sources/`；`knowledge compile` 只能生成 `draft`。核心输出是一个 `SubdomainKnowledgeSnapshot`，其中可含 scenario cards，并固定引用 taxonomy 与 seed lessons：

```yaml
schema_version: 1
id: biology.docking_pose_plausibility
status: draft # approved | revise | rejected | quarantined | superseded
provenance:
  expert_source_refs: []       # source digest + section ID
  visual_taxonomy_ref: null    # taxonomy digest
  seed_lesson_ref: null        # 脱敏 lesson-set digest
  compile_authorization_ref: null # KnowledgeCompileAuthorization receipt digest
  compiler_digest: null
  input_digest: null
scientific_scope: {included: [], excluded: []}
visual_target_types: []
scenario_cards:
  - id: null
    observable_targets: []
    valid_question_patterns: []
    invalid_question_patterns: []
allowed_inference: []
forbidden_shortcuts: []
evidence_requirements: {required_viewer_operations: [], required_evidence: [], allowed_truth_modes: []}
requirements: {asset_requirements: [], viewer_requirements: [], answer_modes: []}
common_failure_modes: []
rubric_extensions: []
review: {decision: pending, reviewer: null, reviewed_at: null, decision_digest: null}
```

`KnowledgeReviewDecision` 必须逐项绑定 snapshot digest、input digest、`compile_authorization_ref`、reviewer、时间、`approved/revise/rejected/quarantined` 和必要修订意见。只有 `approved` 能由 `knowledge freezer` 写入 `knowledge/compiled/`、生成 `KnowledgeFreezeReceipt` 并登记到 `knowledge/index.yaml`。`revise` 返回 draft；`rejected` 不进入索引；`quarantined` 表示来源、引用或策略前提不可信。任一 expert/taxonomy/lesson/compiler digest 改变，旧快照标为 `superseded`；没有 fresh approved index 的 leaf 在 coverage 中必须为 `blocked`。

`knowledge_curator` 是可选的 typed worker：orchestrator 只会在有效 `KnowledgeCompileAuthorization` 的 worker/model/预算范围内调用它；它只能在注入的冻结章节和脱敏 `SeedLessonSet` 上输出带 section ID 的结构化 draft，并记录模型、token、成本、耗时和 draft digest。它不得访问 live 来源、原始 seed、asset、真实 ID 或 reviewer 对话；不得修改 YAML、选择来源、写 index、批准知识或发布。schema/provenance 由确定性 validator 检查，科学语义只由不可绕过的人工领域专家 `KnowledgeReviewDecision` 批准。

## sub-domain config 如何继承和编译

配置固定按以下顺序继承：

```text
base.yaml -> domains/<domain>.yaml -> subdomains/<leaf>.yaml -> profiles/<run>.yaml
```

每个 leaf 至少声明：`id`、状态、视觉技能、seed 查询条件、asset 条件、所需 viewer 操作、答案模式、允许真值模式、证据要求、rubric、配额、review 要求、唯一的 `knowledge.locked_refs`，以及可选的 `author_compute_exception` 审批要求。

```yaml
knowledge:
  locked_refs:
    - subdomain-knowledge:biology.docking_pose_plausibility@sha256:<snapshot-digest>
```

这是知识的唯一解析入口：`config build` 只从 `knowledge/index.yaml` 按完整 digest 解析该 ref，并验证 `id/domain/subdomain`、`approved` 状态、review/input digest 和全部 provenance。它不读取 `knowledge/sources/`、`.qagen/private/knowledge-drafts/`、live 飞书页面或可变的“latest”别名；科学规则不在 leaf YAML 中复制。

子层只能收紧规则，不能扩大工具权限或真值范围。例如 domain 禁止 `network`、`asset_file_read` 后，sub-domain 不能重新开启。编译器依次验证 base、domain、profile、source、viewer、sub-domain、rubric schema。

`configs/rubric.yaml` 固定引用 `QA-Gen/docs/Question-Rubric.md` 的 digest 和发布阈值：真实性 2/2、正确性 1/1、3D 相关性 1/1。rubric 文档或阈值变化会使 config lock 失效，必须重新编译并重新确认计划。

编译输出 `compiled-config.json`、`config-lock.json` 和 `coverage.md`。缺少 fresh approved knowledge index、有效 source receipt、合格 seed、asset 条件、viewer capability、rubric、测量校准或例外审批时，leaf 必须 fail-closed 并显示为 `blocked`。MVP 中 medical 即使完成编译也不得进入发布 coverage，直到有至少一个修复后重新专家审核为 verified 的 medical seed。

## 8 个用户级 CLI 命令组与模块映射

此前的 7 组命令把知识审核隐含在 `seed sync` 中，缺少不可绕过的专家 gate；新增 Knowledge Registry 命令组后为 8 组，正确性优先于维持任意的命令数量。

| 命令 | 主要输入 | 主要输出 | fail-closed 条件 |
|---|---|---|---|
| `qa-gen seed sync` / `accept-version` | EV2 SourceSpec、`QAGEN_EV2_SOURCE_ROOT`、同路径 `--package-root`、版本 request/decision | snapshots、catalog、cards、resolution receipt | root、file-role、inventory、版本、专家状态或闭包不合格时不得产出 eligible index |
| `qa-gen knowledge sync` / `authorize` / `compile` / `approve` | 知识 source、taxonomy、seed lessons、compile authorization、expert decision | 本地镜像、authorization receipt、draft/packet、approved snapshot/index/receipt | 来源/digest/引用不合格、无有效 authorization、无专家批准或 decision 不匹配时不得写入 approved index |
| `qa-gen viewer profile` | viewer definition、asset manifest | capability profile、匿名 scene registry | 无受控 session、能力或匿名化证明时失败 |
| `qa-gen config build` | base/domain/leaf/profile/rubric | compiled config、lock、coverage | schema、digest、seed、viewer 或 rubric 缺失时失败 |
| `qa-gen plan create` | config lock、coverage、预算、可选 `--approve` 决定 | 待签 work plan 或已绑定的 `PlanApprovalDecision` | blocked leaf、超预算、无正向 seed 或 approval digest 不匹配时失败 |
| `qa-gen run` | 已批准 plan、scene registry、viewer profile | candidates、evidence、verification、usage | 内部经 `viewer/session.py` 申请受控作者 session；无 plan approval、越权工具或证据失败时停止或 revise |
| `qa-gen review` | review packet、candidate 版本 | review decision | 决定与 candidate/config/profile 不匹配时拒绝写入 |
| `qa-gen release` | approved candidate、review、bundle 配置 | solver/scorer/audit bundle | 未批准、泄漏、缺 rubric 或缺 evidence 时失败 |

`qa-gen plan create` 首先输出待签计划；人工将批准决定写入 `<run-id>/plan/approval.json`，再以 `qa-gen plan create --approve <run-id>/plan/approval.json` 绑定 `PlanApprovalDecision`，其中固定绑定 plan、config、viewer-profile digests。未绑定的计划可供 review，但 `qa-gen run` 必须 fail-closed。

`scripts/run-pilot` 的固定顺序是：`seed sync -> (必要时 accept-version) -> knowledge sync -> 人工 knowledge authorize -> knowledge compile -> 人工 knowledge approve -> viewer profile -> config build -> plan create -> 人工 plan approve -> run -> review -> release`。它先暂停等待 `KnowledgeCompileAuthorization`，再在 `knowledge compile` 后暂停等待领域专家 `KnowledgeReviewDecision`；两者完成后才允许 viewer profile/config build。它还会在 source version resolution 和 plan approval 处停下等待人工输入；`scripts/check` 只调用配置检查、bundle 检查和测试。shell 脚本不承载业务逻辑。状态、预算、agent 调度和 resume 都在 `pipeline/orchestrator.py`；agent 不得扫描来源目录、修改 YAML、访问原始 asset 或自行发布。

## MVP 验收标准

- 可完整同步 EV2 15 个 case，并稳定得到 3 verified、2 minor_fix、6 factual_error、4 unscorable 的分类与版本解决回执。
- 可编译 biology、chemistry、medical；每个启用 leaf 都具有 fresh approved `SubdomainKnowledgeSnapshot`、合格 seed、viewer 能力、rubric digest、阈值和 blocked 原因。medical profile 在 MVP 中必须保持 blocked。
- 发布试点只覆盖 biology、chemistry；每道候选题绑定具体 asset、匿名 scene 和 viewer 操作，而不是复用模板。medical 只有在至少一个 seed 修复并重新专家审核为 verified 后才可加入发布。
- 每道进入 review 的题都具备参考解、简短 reasoning、字段级 rubric、独立 viewer evidence；测量题另具单位、容差、trace 和截图。
- `author_compute_exception` 只有在审批、冻结 digest、代码/环境 digest、输出和独立 viewer evidence 齐全时才可通过，且 receipt 不泄漏到 solver。
- 自动探测能阻止真实 asset ID、文件名、元数据、网络、原始文件读取和无 viewer 的捷径。
- `usage.jsonl` 可汇总每个 agent/stage 的模型、token、成本与耗时；resume 重验全部 lock，且不会绕过 plan 或 review gate。
- 私有 staging 中 solver、scorer、audit 三个 bundle 均存在；仅 leakage-checked solver/scorer 可导出，solver 不含答案、seed、真实 asset 映射或内部知识，audit 保持私有。

## 延后能力

- 通用、未审批的 offline compute，结构解析和坐标全局搜索。
- 内容寻址存储、SQLite 账本、跨机器恢复、复杂幂等计费和多 provider 调度。
- 默认多人阈值审批、默认双人工 review、向量检索 seed 和自动扩展新 domain。

这些能力只有在 MVP 已证明题目质量、viewer 受控性和 review 成本可接受后，才以独立设计决策加入。旧系统可参考用量记录、viewer 测量和 QC 经验，但不作为新包的实现基础。
