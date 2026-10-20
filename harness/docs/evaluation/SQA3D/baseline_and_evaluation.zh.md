# SQA3D —— Baseline & Evaluation 设计（中文版）

**日期：** 2026-05-21
**切片：** `docs/v_classify/sqa3d_v2_expanded.jsonl` —— n=73 V2 items（扩样已于 2026-05-20 跑完；1500 条样本通过 Gemini-3.1-Pro classifier + audit，PASS）
**在 agent 验证中的角色：** `docs/Sci3DAgentVerificationPlan.zh.md` 里 4×2 方法 ablation 的 **3D 室内场景侧**。
**英文版同名文件：** `baseline_and_evaluation.md`。

---

## 1. 任务定义（简要回顾）

SQA3D = Situated Question Answering on 3D Scenes。3D 场景（ScanNet）+ 一句「带情境」的自然语言问题，问题答案依赖于 agent 在场景里的位置与朝向（"如果我转身能不能看见 X"、"我面前是什么"）。

输出：一段文字答案，必须匹配论文 choice set（EM-1 协议）。如果 agent 输出自由文本没法 verbatim 命中，落到 LLM-judge fallback。

| 关心的切片 | V-class | 为何挑这个切片 |
|---|---|---|
| **V2 —— visibility / occlusion / FOV** | "如果我转身能不能看见 X"、"X 是不是被挡住" | V2 上 vision 理论上 *应该* 起决定作用。如果在这里都不必要，SQA3D 其它任何地方就更谈不上必要。 |

V1–V6 定义见 `docs/Sci3DEvaluationCriteria.md`。

## 2. 评测切片

- **来源：** `docs/v_classify/sqa3d_v2_expanded.jsonl`（73 条 V2）
- **如何产生：** `scripts/v_classify/classify_qa.py` 在 `datasets/SQA3D/sqa_task/balanced/` 上抽 1500 条样本跑分类，模型 `gemini-3.1-pro-preview` 走 Mindra proxy。Plan：`docs/superpowers/plans/2026-05-20-sqa3d-v2-expansion-run.md`。Audit：`docs/v_classify/AUDIT_sqa3d_v2.md`。
- **Audit 门槛：** ≥10 条人工抽查判定 ≥80% 是真 V2。
- **Pilot V2：** 来自 `sqa3d_pilot100.jsonl` 的 3 条 V2 已合并（无重复）。
- **统计功效：** n=73，p=0.5 处 95% CI 半宽 ±11–12pp。足以分辨 ≥10pp 的 M3-vs-M4 delta；亚 5pp 分辨不了。

## 3. 本数据集上的方法矩阵

| 方法 | 实现 | 原生 mask？ | 出文本？ |
|---|---|---|---|
| **M1 — Mesh-as-text / 3D-as-tokens** | **Chat-Scene**（NeurIPS 2024；Vicuna-7B；SQA3D EM 54.6 报告值）；github.com/ZzZZCHS/Chat-Scene | 不适用（SQA3D 是纯文本） | ✅ |
| **M2 — Multi-view render + VLM** | **LLaVA-Cube (LLaVA³)**（arXiv 2511.16454；per-scene NeRF → object decomposition → ordered multi-view tokens；**零微调**）；github.com/CEA-LIST/LLaVA-Cube。**GPT-4o-on-views** 作便宜的 API floor（arXiv 2405.18831）。*Agent3D-Zero（arXiv 2403.11835）作为引用原型 —— 未开源，不跑。* | 不适用 | ✅ |
| **M3 — Tool-use agent w/o 3D vision** | `sci3d_agent` `mode1_no_vision` | 不适用 | ✅ |
| **M4 — Tool-use agent w/ 3D vision** | `sci3d_agent` `mode3_free_visual`；**+ GPT4Scene**（arXiv 2501.01428；github.com/Qi-Zhangyang/gpt4scene）作外部 with-vision 参考（复现 4 —— 捆 fine-tuning） | 不适用 | ✅ |

`mode2_one_image`（单张 canonical image，不返回 pixel）**不在** ablation 中 —— 它会让 vision/no-vision 的切换不再干净。

### 复现 shortlist 与硬件

| 选项 | 复现 | 硬件 | 报告值（SQA3D） | 备注 |
|---|---|---|---|---|
| **Chat-Scene** | 2 | 1× A100 40G | EM 54.6 | Vicuna-7B + object-centric token；自带干净的 `evaluate` flag |
| 3D-LLaVA | 3 | 1× A100 | 论文有报告 | 点云走 Omni Superpoint Transformer |
| LEO | 2 | 1× A100 | EM ~50 | Vicuna-7B；object-centric scene token |
| LL3DA | 2 | 1× A100 | < 上 | query-token (Interactor3D) over scene point feature |
| **LLaVA-Cube** | 3 | 2× A100（每场景 NeRF 训练） | 与 zero-shot 3D-LLM 相当 | 唯一开源的、代码完整的、真正多视角 render+VLM、且不依赖 API |
| GPT-4o-on-views | 2 | 仅 API | floor | 几张 canonical 视角图直接喂 GPT-4o |
| GPT4Scene | 4 | 4× A100（fine-tuning） | 强 | 构造 BEV + 时空 object marker；165K 条训练数据 |

> §3 推荐的是每个方法族的首选。其它仅为备选，硬件或仓库问题时可替换。

## 4. 打分架构

### 4.1 单轨 —— EM-1 + judge fallback

与 M3D-RefSeg 不同，SQA3D **没有 mask 轨** —— 答案是纯文本。

| 轨道 | Metric | 跑哪些方法 | 目的 |
|---|---|---|---|
| **Text 轨（主）** | EM-1（论文协议 —— 在 choice set 上 exact-match top-1） | M1 / M2 / M3 / M4 | 论文头条数；可对照 leaderboard |
| **Text 轨（fallback）** | Claude LLM-judge → `0.0 / 0.5 / 1.0`，question-anchored rubric | 同上 | M3 / M4 经常吐自由文本，没法 verbatim 命中 choice set —— 这里兜底 |

EM-1 实现：论文协议归一化（lowercase、去 article、合并空白），与 gold answer 字符串 exact match。沿用 Tier-1 的打分链路；fallback 复用 `sci3d_bench/scorers/llm_judge.py`（不需要新模块，prompt 够通用）。

### 4.2 V2 上 LLM-judge fallback 的 rubric 长这样

| 分数 | 含义 |
|---|---|
| **1.0** | 答案正确 *且* 满足 situated 视角约束（如问题问「从我当前姿态能不能看见 X」，答 "yes, I can see X"） |
| **0.5** | 实体对了，但 方向/可见性 限定词错了；或含糊但偏向正确 |
| **0.0** | 实体错了、否定 / 非答案 |

Judge 模型：`claude-sonnet-4-6` 走 Mindra proxy。与被评分对象家族独立。

## 5. Trajectory analysis（仅 M3 / M4）

协议与 M3D-RefSeg 一致（见 `docs/Sci3DAgentVerificationPlan.zh.md` §5.2）：

1. **Tool-call rate** —— M4 至少调用过一次 render_view / screenshot / set_camera 的 item 比例。
2. **Answer-flip rate（vision-redaction 反事实）** —— 同一条 trajectory 把 image bytes 换空白后重放；统计答案改变率。
3. **Vision-attributable delta** —— 按是否真调 vision 拆 M4，分别与 M3 在同样 items 上比。

V2 题目恰恰是 agent *应该* 调 render_view 的（问题就是关于「从我当前姿态看到/看不到什么」）。**如果在 V2 上 tool-call 率接近 0，本身就是结论**（vision 即使理想情况也被忽略）。

## 6. 为什么这是受控 ablation，不是「再发现」

SQA3D 论文自己就报告了我们要测的核心悖论：

| Baseline | EM-1 |
|---|---|
| Blind / 仅语言 | 26.75% |
| 当时最好的 3D 模型 | 47.20% |
| BEV / egocentric video | **比 blind 还差** |
| 人类 | 90.06% |

论文把 BEV/egocentric 下降归因为 "vision-bias when visual input is provided"。其它先前工作（zero-shot GPT-4V on 3D VQA、VLind-Bench、VLMs-are-Biased）结论一致：LVLM 默认走语言先验。

**反向证据**（我们的研究意在 *限定* 而非反驳）：MV-ScanQA（2508.11058）和 HMR3D（2511.22961）都在相关 3D-QA 切片上展示出 multi-view render 提供了 text-only scene description 之外的互补信号。

我们在 V2 上的 ablation 站在交叉点：按题目分类，V2 *应该* 是 vision 起决定作用的地方；而且我们的 agent 在 V2 上有 rendering tool 可用。

## 7. 文件分布

SQA3D 这一侧比 M3D-RefSeg **简单**：

```
sci3d_bench/scorers/
├── llm_judge.py                     [已存在]   V2 自由文本 fallback 复用
└── (EM-1 scorer)                    [PLAN]     论文同款 exact-match；≤80 LoC

sci3d_bench/adapters/
└── (M1 / M2 scene adapters)         [PLAN]     Chat-Scene、LLaVA-Cube、GPT-4o-on-views、GPT4Scene

scripts/sqa3d_verify/                [PLAN]     与 scripts/m3d_refseg_verify/ 类比
├── run_smoke.py
└── README.md
```

EM-1 scorer 体量很小，挂在 `llm_judge.py` 旁边即可，不必单独建文件。Adapter 是工作量主体。

## 8. Done criteria —— "本数据集上 evaluation 跑通"意味着什么

- [ ] EM-1 scorer + tests + smoke（TDD-ed）。
- [ ] LLM-judge fallback 路径有覆盖（包含一个 EM-1 不通但 judge 接受的 smoke item）。
- [ ] Chat-Scene adapter 在 **3 条人工挑选的 dev-set item** 上复现 paper EM-1，差 ≤ 2pp。
- [ ] 四方法都在 n=73 上产出 trajectory，无致命错误。
- [ ] 每方法每 item 都同时报 EM-1 + LLM-judge。
- [ ] M4 的 trajectory 指标（tool-call、flip、vision-attributable delta）都算出。
- [ ] GPT4Scene 要么跑（复现 4 budget 获批），要么明确降级为 report 里的 "cited only"。
- [ ] 出「方法 × item」分数表（用于 matched-pairs）。

## 9. 本数据集特有的风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **n=73 vs 目标 30–50** —— 跑超了；统计功效没问题，但 LLM-judge fallback 这一路 API 成本更高。 | 接受；budget 明确算上。 |
| 2 | **with-vision 外部参考不对称** —— GPT4Scene 是唯一已发布选项；复现 4（捆 fine-tuning）。 | 决策推到 phase-2 budgeting；明确写入「scene 侧有第二个带 vision 的数据点，医学侧没有」。 |
| 3 | **EM-1 脆弱** —— agent 吐自由文本，常常 verbatim 命中不了 choice set。 | LLM-judge fallback 已规划；两个数都报。 |
| 4 | **ScanNet 场景获取 / 预处理** —— Chat-Scene 与 LLaVA-Cube 需要 73 V2 涉及场景的 ScanNet mesh。 | 我们已经拿了 8 条 ScanNet train scenes（matched availability）；确认 73 V2 都在其中，否则补取。 |
| 5 | **Vision-bias 先验过强** —— 一个 null result 可能被混淆为 mode 漂移 bug。 | 强力 mode-parity audit（M3 / M4 必须共享 backbone、scaffold、decoding）；用 trajectory analysis 排除。 |

## 10. References

- SQA3D —— arXiv 2210.07474（Ma et al. ICLR 2023）；blind baseline + vision-bias 证据
- Zero-Shot GPT-4V on 3D VQA —— arXiv 2405.18831；"blind is strong" + GPT-4o-on-views 协议
- VLind-Bench —— arXiv 2406.08702；VLMs are Biased —— arXiv 2505.23941（语言先验主导）
- HMR3D —— arXiv 2511.22961；MV-ScanQA —— arXiv 2508.11058（反向证据：render 增加信号）
- Chat-Scene —— arXiv 2312.08168；github.com/ZzZZCHS/Chat-Scene
- 3D-LLaVA —— arXiv 2501.01163；LEO —— arXiv 2311.12871；LL3DA —— github.com/Open3DA/LL3DA
- LLaVA-Cube —— arXiv 2511.16454；github.com/CEA-LIST/LLaVA-Cube
- Agent3D-Zero —— arXiv 2403.11835（引用原型，未开源）
- GPT4Scene —— arXiv 2501.01428；github.com/Qi-Zhangyang/gpt4scene
- 扩样 plan & audit：`docs/superpowers/plans/2026-05-20-sqa3d-v2-expansion-run.md`；`docs/v_classify/AUDIT_sqa3d_v2.md`；`docs/v_classify/REPORT_sqa3d_expansion1500.md`
