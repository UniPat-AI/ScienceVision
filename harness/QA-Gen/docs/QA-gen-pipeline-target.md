# Target

1. 真实性+相关性，通过 rubric 控制题目产出: `QA-Gen/docs/Question-Rubric.md`
2. seed case加载来提高产题质量
   1. bio/chem/med 目前有：`$QAGEN_EV2_SOURCE_ROOT`
3. 专家知识+3D视觉类别 控制 domain + sub-domain，需要结合seed case落盘到本地文档
   1. 专家知识：https://ncn595olgbv0.feishu.cn/wiki/AZlyw5VsXi2sokkPQjGcYC5WnHh
   2. 3D视觉类别：https://ncn595olgbv0.feishu.cn/wiki/FrO8wJfXIiAqaGkl6alcYEqonzf?previous_navigation_time=1787732303205
   具体来说，应该覆盖这些sub-domain（目前不全面）
   - bio: Docking Pose Plausibility, Molecular Collision Check
   - chem: ① 立体化学 / 手性；② 构象分析；③ 空间位阻与可接近性；④ 配位化学； ⑤ 晶体学；晶体对称性的视觉判定，中间构象决定产物
   - med: - (不规则病灶)病灶定位/识别,- 解剖区域划分,- 病症特征判断
4. 题目与3D asset高度相关，去模版化，表述自然、重点突出
5. 产题脚本化，agent in the loop而不是agent操控 （比如build环节，verifier环节，rewrite环节，modifier，refine环节... 可以有不同的agent来控制成本，能够自动记录token消耗和时间开销）
6. 与可视化viewer适配
7. 要求题目可作答（提供参考解+解体思路+每道题的rubric）
8. 目前存在的题目问题：
   1. 学科类设计问题：
      - 生物：https://ncn595olgbv0.feishu.cn/wiki/P15DwQ8I5ixrtvkzBsocZjpKn2e
      - 化学：https://ncn595olgbv0.feishu.cn/wiki/WIwOwIhSMic7uakFj0JcBL9hned
      - 医学：https://ncn595olgbv0.feishu.cn/wiki/RfPmwS2Ouiy1zzk1dBacr2Yinxe
      - 建筑：https://ncn595olgbv0.feishu.cn/wiki/QtnswphOXiLj3ukSksVcUG3RnRh
      - 机械：https://ncn595olgbv0.feishu.cn/wiki/LeH0wGqZDif8RnkZBYIc15Lhnjf
   2. agent hack：拿到3D asset id找到答案，联网搜到答案，直接用知识不利用3D视觉找到答案，调用计算工具/python库获得答案
9. 支持在已有题目上迭代（比如有必要的话保留task-id等信息）

# implement
- sub-domain 覆盖
- agent选择：
  - 以使用 opus-5 来承担难度高的任务/分配/计划， dpsk-v4-pro来做细致的执行，gpt-5.6-sol来检查（去bias）
  - 不是先写一套 prompt 就开始造题。更短且可靠的顺序是：先完成 config -> plan approval -> run/review/
  release 的锁定链，再配置 brief_builder / author / truth_builder / evidence_worker / verifier /
  rewriter；否则 agent 产物没有可验证的输入边界。

  你的模型分工合理，我会把它落成可配置 routing policy：

  - opus-5：高难度任务拆解、brief 和方案规划
  - dpsk-v4-pro：结构化产题、证据整理和按 receipt 修改
  - gpt-5.6-sol：独立 verification / adversarial check，避免作者自审偏差

  当前子 agent 运行环境只直接暴露 gpt-5.6-terra 和 gpt-5.6-sol，所以实现阶段我能用 gpt-5.6-sol 做独立审
  计；opus-5、dpsk-v4-pro 会作为 pipeline 的外部 provider/model 配置接入，并在没有实际 provider
  credential 时 fail-closed，不伪装成已经可调用。
