# `docs/evaluation/` —— 各数据集 baseline & evaluation 设计（中文版）

英文索引：`docs/evaluation/README.md`。

本目录存放 **每个数据集** 的 baseline + 评测设计，是 `sci3d_agent`（3D-grounded scientific QA agent）验证的「数据集侧」配套文档。它的项目级综合在 `docs/Sci3DAgentVerificationPlan.zh.md`（中文版）/ `docs/Sci3DAgentVerificationPlan.md`（英文版）。

| 数据集 | 切片 | 评测设计（中文） | 上游 eval 走读 |
|---|---|---|---|
| **M3D-RefSeg** | `docs/v_classify/m3d-refseg_pilot100.jsonl`（n=100，V3） | `M3D-RefSeg/baseline_and_evaluation.zh.md` | `M3D-RefSeg/M3D-RefSeg_evaluation_notes.md` |
| **SQA3D** | `docs/v_classify/sqa3d_v2_expanded.jsonl`（n=73，V2） | `SQA3D/baseline_and_evaluation.zh.md` | —（论文 EM-1 协议；目前没单独的 notes 文件） |

每份数据集文档涵盖：

1. **评测切片** —— 来源、规模、V-class、split、统计功效。
2. **方法矩阵** —— 四方法（M1 mesh-as-text / M2 multi-view render+VLM / M3 agent w/o vision / M4 agent w/ vision）在该数据集上的具体实现。
3. **打分架构** —— metrics、轨道、judge 模型、公式细节（例如 M3D-RefSeg 的 smooth-1 Dice quirk）。
4. **Trajectory analysis 协议** —— M3-vs-M4 「必要 vs 有效」的拆解。
5. **文件布局** —— 涉及哪些 scorer 模块和 adapter 模块。
6. **Done criteria** —— 「这个数据集上的 evaluation 跑通」的判定条件。
7. **数据集特有的风险**。
8. **Reference** —— 论文、仓库、source-of-truth 文件。

要先看项目级的中心问题、判定逻辑、分层验证 checklist，请先读 `docs/Sci3DAgentVerificationPlan.zh.md`。
