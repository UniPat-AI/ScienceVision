# M3D-RefSeg —— Baseline & Evaluation 设计（中文版）

**日期：** 2026-05-21
**切片：** `docs/v_classify/m3d-refseg_pilot100.jsonl` —— n=100，100/100 V3=1.0，status=ok
**在 agent 验证中的角色：** `docs/Sci3DAgentVerificationPlan.zh.md` 里 4×2 方法 ablation 的 **医学 volume 侧**。
**同目录配套文档：**
- `M3D-RefSeg_evaluation_notes.md` —— M3D-LaMed 上游 eval 全流程走读（preprocess → `[SEG]` → SegVol → soft Dice）。不熟悉该任务请先读它。
- 英文版同名文件：`baseline_and_evaluation.md`。

---

## 1. 任务定义（简要回顾）

输入：一个 3D CT volume + 一句 referring expression（自然语言）。
输出：一段含 `[SEG]` 特殊 token 的自由文本答案 + 一个 `32 × 256 × 256` 分辨率的 3D binary mask。

负责产出 mask 的模型必须是 segmentation-capable VLM（M3D-LaMed 式架构：VLM 吐 `[SEG]`，其 hidden state 经 MLP 接入 SegVol promptable decoder）。

| 落盘字段 | 含义 |
|---|---|
| `Image` | CT NIfTI 路径（如 `s0139/ct.nii.gz`） |
| `Mask` | 多标签 mask NIfTI 路径（如 `s0139/mask.nii.gz`） |
| `Mask_ID` | 本题目标的 mask label（整数） |
| `Question` | referring expression |
| `Answer` | gold 文本答案（含 `[SEG]`） |
| `extra.mask_text` | 区域规范化描述 —— 我们 text-track judge 的 anchor |

## 2. 评测切片

- **来源：** `docs/v_classify/m3d-refseg_pilot100.jsonl`
- **规模：** n = 100，frozen
- **V-class：** 全部为 V3（pixel-evidence trace —— 必须出 segmentation mask）
- **Split：** 混合（77 `train` / 23 `test`）；见风险 #1
- **同一批 items 同时跑四方法** ⇒ matched-pairs analysis 合法（同 item 上做 paired t-test / Wilcoxon）
- **统计功效：** n=100，95% CI 半宽 ±10pp at p=0.5。足够分辨 ≥10pp 的 M3-vs-M4 delta；亚 5pp 分辨不了。若结果落在边界，再扩到 n=300 或 full test split（555）。

## 3. 本数据集上的方法矩阵

| 方法 | 实现 | 出 mask？ | 出文本？ |
|---|---|---|---|
| **M1 — Mesh-as-text / 3D-as-tokens** | **M3D-LaMed-Phi-3-4B**（HF `GoodBaiBai88/M3D-LaMed-Phi-3-4B`；github.com/BAAI-DCAI/M3D） | ✅ 通过 `[SEG]` + SegVol decoder | ✅ |
| **M2 — Multi-view render + VLM** | **Med-2E3**（slice render + 2D-VLM；github.com/MSIIP/Med-2E3）；**LLaVA-Cube** 作 floor（github.com/microsoft/LLaVA-Med）；**GPT-4o-on-views** 作便宜的 API floor | ❌ 无原生 mask head | ✅ |
| **M3 — Tool-use agent w/o 3D vision** | `sci3d_agent` `mode1_no_vision`，over `.npy_m3d` volume | ❌ | ✅ |
| **M4 — Tool-use agent w/ 3D vision** | `sci3d_agent` `mode3_free_visual`，slice / volume rendering | ❌ | ✅ |

**医学 volume 没有已发布的 with-vision 外部 agent baseline** —— GPT4Scene 只针对 scene。医学侧的 with-vision 完全靠 `mode3` + Med-2E3（与 SQA3D 侧不对称；已明确接受）。

## 4. 双轨打分架构

每个 item 在两条 **独立** 轨道上打分：

| 轨道 | Metric | 跑哪些方法 | 目的 |
|---|---|---|---|
| **Mask 轨** | Dice（binary，sigmoid > 0.5，smooth = 1，32×256×256 voxel 空间） | **仅 M1** | 与 M3D paper 表直接可比的 Dice 锚 |
| **Text 轨** | Claude LLM-judge → `0.0 / 0.5 / 1.0`，针对 `mask_text` anchor | **M1 / M2 / M3 / M4** | 跨方法的 ablation 信号；3D-vision 必要性的 M3-vs-M4 delta 全部走这条轨 |

**为什么不让 M2 通过 SAM-Med3D 走 mask 轨？** 想过、否决了。让 M2 的 VLM 喊 SAM-Med3D 提交区域描述会让「M2 的 mask 质量」变成「SAM-Med3D 的 mask 质量」 —— confounded。我们关心的是 *agent*，不是「哪个方法最会驱动外部 mask service」。

**为什么不在原始分辨率算 Dice？** 想过、否决了。原始分辨率更干净，但 mask 轨存在的意义就是数字要能直接对得上 M3D paper 表 —— 换分辨率 = Dice 不可比。

### 4.1 Mask 轨 —— 上游同款

预处理（从 M3D 的 `m3d_refseg_data_prepare.py` 移植）：

1. 用 `nibabel` 读 `ct.nii.gz`。
2. resize / crop 到 `32 × 256 × 256` (D × H × W)。
3. MinMax 归一到 `[0, 1]`。
4. 读 `mask.nii.gz`，用 **nearest-neighbor** 插值 resize 到同样 shape（保留整数 label）。
5. 二值化：`gt_binary = (seg == Mask_ID).astype(np.int8)`。

预测消费（仅 M1）：

```python
pred_logits.shape == (32, 256, 256)
pred_binary = (sigmoid(pred_logits) > 0.5).astype(np.int8)
# 等价但更便宜：
pred_binary = (pred_logits > 0.0).astype(np.int8)
```

Dice 公式（逐字复刻上游 `Bench/eval/metrics.py`，`smooth = 1`）：

```python
num = (pred * target).sum()
den = pred.sum() + target.sum() + 1.0   # smooth=1 只加在分母
dice = 2.0 * num / den
```

**要复刻的 quirk（不要默默"修正"）：**
- `smooth = 1` 只在 **分母**。空预测 + 空 GT → Dice = 0.0（分子 0，分母 1，结果 0）。教科书上有些把 `smooth` 同时加在分子分母里 —— *那不是 M3D 的做法*。
- resize 把物理体素间距丢掉了。Dice 是在 32×256×256 voxel 空间，不是 mm³。

每 item：`dice ∈ [0, 1]`。汇总：n=100 上的 mean Dice，附 std 和 bootstrap 95% CI。同时报 empty-empty 率作为 sanity flag。

### 4.2 Text 轨 —— 带 mask-text anchor 的 Claude LLM-judge

每个 item 喂给 judge 的输入：

- `question` —— referring expression
- `gold_answer` —— 上游 gold（如 *"Sure, a postoperative stoma can be seen in the [SEG]."*）
- `mask_text` —— `extra.mask_text` 给出的区域规范化描述（无歧义 anchor —— 如 *"postoperative stoma, upper abdomen"*）
- `candidate` —— M1 / M2 / M3 / M4 输出的自由文本

评分 rubric（返回 0 / 0.5 / 1）：

| 分数 | 含义 | 例子 |
|---|---|---|
| **1.0** | 命名了正确的解剖 referent（与 `mask_text` 一致），自信、无歧义 | "Yes, I see a postoperative stoma in the upper abdomen" |
| **0.5** | 部分对：对的器官但错的亚型；或对的区域但没有具体命名；或措辞含糊 | "There appears to be something in the upper abdomen"; "Yes, I see a stoma"（没定位） |
| **0.0** | 错的 referent，否定，或非答案 | "No abnormality"; "I cannot determine"; 命名了别的解剖 |

Judge 模型：`claude-sonnet-4-6` 走 Mindra proxy（`ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`）。与被评分对象家族独立：
- 与 V-classifier（gemini-3.1-pro）独立
- 与 baseline（M1 = Phi-3 / Llama；M2 = LLaVA / GPT-4o；M3 / M4 可配置）独立

实现：`sci3d_bench/scorers/refseg_text_judge.py` —— 继承 `base.py` 的 `Scorer` Protocol，走现成的 `llm_client` 调 Claude，parse 一个返回 `{score, rationale}` 的 tool-call。模式镜像 `LLMJudgeScorer`；不复用而是新建文件，是因为 prompt 显著不同（医学 referring expression，不是通用 V-轴）。

## 5. Trajectory analysis（仅 M3 / M4）

每条 M4 trajectory 后处理：

1. **Tool-call rate** —— agent 至少调用过一次 render_view / screenshot / set_camera 的 item 比例。
2. **Answer-flip rate（vision-redaction 反事实）** —— 用同一条 trajectory 重放，把返回的 image bytes 换成空白；统计最终答案改变的比例。
3. **Vision-attributable delta** —— 按是否真调用 vision 把 M4 拆成两个子集，分别与 M3 在同样 items 上比，分离出「真正看了之后」的提升。

本数据集上的判定逻辑（事先锁定）：
- `Δ_necessary ≈ 0` ⇒ V3 segmentation referring on CT 上 vision **非必要**。
- `Δ_necessary > 0` **且** tool-call 率高 + flip 率高 ⇒ vision **既必要也有效**。
- `Δ_necessary > 0` **但** tool-call 率低 ⇒ 提升不来自 vision（混杂因素 —— 检查 scaffold/prompt）。

## 6. 文件分布（目标态）

```
sci3d_bench/scorers/
├── mask_dice.py                     [PLAN]   compute_dice + MaskDiceScorer
├── refseg_text_judge.py             [PLAN]   带 mask-text anchor 的 RefSegLLMJudge
└── nifti_io.py                      [PLAN]   load_ct, load_mask_binary, preprocess_to_32x256x256

sci3d_bench/adapters/
└── (M1 / M2 medical adapters)       [PLAN]   每个方法族一个

scripts/m3d_refseg_verify/
├── run_smoke.py                     [PLAN]   3 条 synthetic 烟雾测试；不打 live API
└── README.md                        [PLAN]
```

实施 plan：`docs/superpowers/plans/2026-05-21-m3d-refseg-verify-pipeline.md`。7 个 TDD-ed task，覆盖 Dice、NIfTI I/O、scorer wrapper、Claude judge、smoke、README。Done criteria：`pytest -m unit tests/sci3d/scorers/` → 13 PASSED；`python3 scripts/m3d_refseg_verify/run_smoke.py` → exit 0 + `SMOKE OK`。

## 7. Done criteria —— "本数据集上的 evaluation 跑通"意味着什么

- [ ] Layer B 通过：13 个 unit test + smoke 全绿。
- [ ] M1 adapter（M3D-LaMed-Phi-3-4B）在 **3 条人工挑选的 case** 上 spot-check Dice，与 paper 的对应 per-class 数字差 ≤ 2pp —— 用来证明 mask 轨接线无误，再进 full slice。
- [ ] 四方法都在 n=100 切片上跑完 trajectory，无致命错误。
- [ ] text-track judge 在 ≥95% 的 item 上返回非 degraded 分数（每方法）。
- [ ] M1 的 mask 轨 Dice 带 bootstrap 95% CI 和 empty-empty 率一起公开。
- [ ] M4 的 trajectory 指标（tool-call 率、flip 率、vision-attributable delta）都算出。
- [ ] 出一份「方法 × item」的分数表（用于 matched-pairs 分析）。

## 8. 本数据集特有的风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | **切片 split 混合**（77 train / 23 test）—— paper Dice 是 test only。 | 本切片上的 M1 Dice 不能直接对照 paper 头条；分开汇报；M3-vs-M4 ablation 不受影响（同切片 matched-pairs）。 |
| 2 | **`mask_text` 质量** —— 若其中嵌入了 gold answer 措辞，judge 就循环了。 | impl 阶段人工抽 10 条 `mask_text`；若含噪，把 anchor 退化为「label only」（如 `"label 2 = stoma"`）。 |
| 3 | **Smooth-1 quirk** 偏向「正确拒绝」的样本。 | 严格复刻（追求 parity）；汇报 Dice 时同时报 empty-empty 率。 |
| 4 | **医学侧没有 volumetric M2 参考** —— Med-2E3 + LLaVA-Med 都是 slice-render 风格；with-vision agent 外部 baseline 不存在。 | 接受；明确标注，与 SQA3D 的 GPT4Scene 不对称。 |
| 5 | **M1 的 GPU 资源** —— M3D-LaMed-Phi-3-4B 需要 1× A100 40G 或等价。 | 估算 n=100 在 1× A100 上 ~4 h；跑前先确认。 |
| 6 | **目前没有 M1 复现门槛** —— verify pipeline 只检查 scorer，不检查 M1 本身。 | 在 adapter plan 里加 spot-check gate。 |

## 9. References

- M3D / M3D-LaMed —— arXiv 2404.00578（Bai et al. 2024）；github.com/BAAI-DCAI/M3D；HF `GoodBaiBai88/M3D-LaMed-Phi-3-4B`
- Med-2E3 —— arXiv 2411.12783（BIBM 2025）；github.com/MSIIP/Med-2E3
- LLaVA-Med —— github.com/microsoft/LLaVA-Med
- 上游 eval 源码（M3D 仓库）：`Bench/eval/eval_seg.py`、`Bench/eval/metrics.py`、`Bench/dataset/multi_dataset.py`、`m3d_refseg_data_prepare.py`
- 配套：`docs/evaluation/M3D-RefSeg/M3D-RefSeg_evaluation_notes.md`（上游 eval 走读）
- Spec：`docs/superpowers/specs/2026-05-21-m3d-refseg-verify-pipeline-design.md`
- Plan：`docs/superpowers/plans/2026-05-21-m3d-refseg-verify-pipeline.md`
- **Pilot100 API baseline 实测**：[`baseline_pilot100_report.md`](./baseline_pilot100_report.md)（2026-05-21）

---

## 10. 两阶段 baseline：VLM-parser + segmentation-decoder（V2 重设计）

**这一节是 §10 的整体重写**。之前的 R0–R4 路线（不同的"VLM 直出 mask vs. 替换 VLM"分叉）合并到一个干净框架里：**VLM 永远只做 target parsing，mask 永远由专门的 segmentation model 产出**。Bridge（VLM → decoder 的输入翻译）是变量。

### 10.1 动机：pilot100 实测说明 VLM 不能直接产 mask

pilot100（[`baseline_pilot100_report.md`](./baseline_pilot100_report.md)）实测：

| baseline 路线 | mean soft Dice | median Dice | n_ok / n | 说明 |
|---|---|---|---|---|
| Claude-Sonnet-4-6 + box 反投 | **0.016** | 0.000 | 100/100 | API only，box-mask 上限 |
| Gemini-3.1-Pro-Preview + box 反投 | **0.020** | 0.000 | 92/100 | API only，box-mask 上限 |
| M3D-LaMed-Phi-3-4B（paper, 全 test） | **≈ 0.40+** | n/a | n/a | end-to-end segmentation-capable，目标 reference |

差 20× 的根因不是模型差、是**结构差**——API VLM 没有 mask decoder 头，强行让它"在 2D PNG 上画 bbox"等于让一个内科医生用蜡笔画 mask。两种干净修复方向：
- **A. 给 VLM 配一个外部 decoder（本节，V0–V3）**——VLM 做语言理解，decoder 做 voxel-precise mask。
- **B. 换成 end-to-end segmentation-capable VLM（§3 M1，V4）**——两件事一个模型做完。

A 和 B **可以同时跑**，互为对照（"parser+decoder 的 plug-in 组合 vs. joint-trained 模型"）。

### 10.2 Pipeline（两阶段）

```
[CT volume + question]
         │
         ▼
┌─────────────────────────────────────────────┐
│ Stage 1: VLM parser (Claude / Gemini)       │
│                                             │
│  Input: question + K axial slice PNGs       │
│         (K = 7 in pilot100, 16 in plan)     │
│  Output (JSON):                             │
│    { "target":        { organ, lesion,      │
│                         sub_region,         │
│                         modifiers,          │
│                         free_text },        │
│      "spatial_hints": { bbox_per_slice,     │
│                         z_range_hint },     │
│      "answer":        "...[SEG]..." }       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ Stage 2: Translation layer (deterministic)  │
│                                             │
│  VLM JSON → prompt for chosen decoder       │
│   ├─ SegVol     ← target.free_text          │
│   ├─ MedSAM 2   ← spatial_hints.bbox_*      │
│   ├─ SAM 3      ← bbox or text              │
│   └─ nnU-Net    ← target.organ → checkpoint │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ Stage 3: Segmentation decoder               │
│   Predicted 3D mask  (D, H, W)              │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ Stage 4: Eval (UNCHANGED from pilot100)     │
│   Soft Dice vs (mask == Mask_ID) + 3-panel  │
│   viz                                       │
└─────────────────────────────────────────────┘
```

**关键设计原则：**

1. **同一份 VLM 输出可喂多个 decoder。** 每个 case 只调一次 API（pilot100 实测 ≈ 0.05–0.10 USD / case），但能算 N 个 decoder 的 Dice。**API 调用与 decoder 评测解耦**。
2. **Translation layer 是确定性代码——不调 API、不调 ML。** 把 JSON 翻成各 decoder 期望的 prompt。`tests/baseline/test_translate.py` 覆盖每条翻译规则。
3. **Decoder 是 plug-in。** 通过 `Protocol` 接口（`predict(volume, prompt) -> (D,H,W) prob`）实现，可独立换 SegVol / MedSAM 2 / SAM 3 / nnU-Net。
4. **Stage 4 不动。** [`baseline_pilot100_report.md`](./baseline_pilot100_report.md) §6.1 的 soft Dice、§6.3 反投（仅 V0 用）、§10 三联 viz 全部复用。

### 10.3 Stage 1: VLM 作为 target parser

**新 prompt schema：**

```
System: You are an expert radiologist analyzing axial CT slices.
        You will receive K slices ordered superior→inferior at fixed depth
        percentiles. Identify the target referred to by the question and
        produce a structured description that a downstream segmentation
        model can consume. Coordinates are pixels of the 256x256 image,
        origin top-left.

User:   [K PNG image content blocks]
        Question: <referring expression>

        Return JSON only:
        {
          "target": {
            "organ":       "<canonical, e.g. 'liver', 'left kidney'>",
            "lesion_type": "<pathology | null, e.g. 'cyst', 'tumor'>",
            "sub_region":  "<refiner | null, e.g. 'segment IV'>",
            "modifiers":   ["<visual cues, e.g. 'hypodense'>"],
            "free_text":   "<ready-to-use text prompt, e.g. 'small hepatic cyst'>"
          },
          "spatial_hints": {
            "bbox_per_slice": [ [x1,y1,x2,y2] | null, ...  // K entries ],
            "z_range_hint":   [z_min_frac, z_max_frac] | null
          },
          "answer": "<one-sentence answer containing literal [SEG]>"
        }
```

**为什么用结构化 JSON：**

- 下游不同 decoder 期望的 prompt 类型不同（text / bbox / organ name）。结构化 JSON **让 translation layer 可写、可测、可调参**。
- `bbox_per_slice` 保留 pilot100 / V0 完全兼容——V0 只用这一个字段。
- `free_text` 字段让 text-promptable decoder（SegVol、SAM 3）有现成 prompt，不必由 translation layer 临时拼字符串。
- `target.organ` 单字段让 organ-specific decoder（nnU-Net / TotalSegmentator）直接 lookup checkpoint。

**Token cost：**

- 7-slice（pilot100 实测）：Claude ≈ 1.2K prompt tokens / case；Gemini ≈ 8K / case。
- 16-slice（计划）：约线性翻倍（≈ 2.7K / 18K）。Claude 仍在 128K context 预算 1% 以内；Gemini 需要测 timeout。

### 10.4 Stage 2 / Stage 3: Decoder backends

| Decoder | 原生 prompt | 3D 处理 | Medical-trained | GPU | 主要参考 |
|---|---|---|---|---|---|
| **SegVol** | text / point / bbox | native 3D（96³ patch + sliding window） | ✅ | 24G | Du et al. 2024，[BAAI-DCAI/SegVol](https://github.com/BAAI-DCAI/SegVol) |
| **MedSAM 2** | bbox / point | 2D + video propagator | ✅ | 12G | Ma et al. 2024，[bowang-lab/MedSAM2](https://github.com/bowang-lab/MedSAM2) |
| **SAM 3** | bbox / point / text | 2D（需 wrapper 做 3D） | ❌（通用） | 12-24G | Meta 2025/2026 |
| **TotalSegmentator / nnU-Net** | organ name → checkpoint | native 3D | ✅ | 12G | Wasserthal et al. 2023，117 类 |
| **M3D-LaMed**（reference） | end-to-end（无 Stage 1） | native 3D | ✅ | 40G | §10.5 V4 |

**为什么 SegVol 列 P0：** native text-promptable + native 3D + medical-trained，与 Stage 1 的 `free_text` 字段对接成本最低，**单一模型覆盖大部分组织+病灶**。
**为什么 MedSAM 2 列 P0：** native bbox-promptable，与 V0 的 `bbox_per_slice` 完全复用，**改一行 reconstruct 函数就能从 V0 升到 V1**。

### 10.5 方法矩阵 V0–V4

| ID | Stage 1（VLM 输出字段） | Bridge | Stage 2 decoder | Mask source | mean Dice 预估 | 优先级 |
|---|---|---|---|---|---|---|
| **V0**（done） | `bbox_per_slice` | 2D bbox | none — `boxes_to_mask3d`（填充矩形 + z-dilate） | box-mask | **0.016 / 0.020**（实测） | done |
| **V1-MedSAM2** | `bbox_per_slice`（复用 V0 API call） | 2D bbox + slice idx | **MedSAM 2** image+video predictor | 2D mask refined per slice + z propagate | 0.10–0.20 | **P0** |
| **V2-SegVol** | `target.free_text` | category text | **SegVol** text-prompted | native 3D probabilistic mask | 0.20–0.35 | **P0** |
| **V2-SegVol+bbox** | `free_text` + `bbox_per_slice` → 3D bbox | text + 3D bbox | **SegVol** dual prompt | native 3D probabilistic mask | 0.25–0.40 | P1 |
| **V3-nnUNet** | `target.organ` | organ → checkpoint URL | **TotalSegmentator** 117-class | trained 3D mask（仅整 organ） | organ-level 0.7+；lesion-level 受限 | P1 |
| **V1-SAM3** | `bbox_per_slice` 或 `free_text` | 2D bbox / text | **SAM 3** 本地 HTTP wrap | 2D mask + propagate | unknown（medical zero-shot 无数字） | P2 |
| **V4-end2end** | — | — | **M3D-LaMed-Phi-3-4B** end-to-end | `[SEG]` token + SegVol decoder | paper ≈ 0.40+ | **P0**（reference） |

**P0 cost-comparable 三件套：**

- **V0**（已有）：API-only 上限
- **V1-MedSAM 2**：API + 2D mask decoder（本地 12G GPU）
- **V2-SegVol**：API + native-3D text-promptable decoder（本地 24G GPU）
- **V4-end2end**：与 paper 数字对齐的唯一路线（本地 40G GPU）

三个一起跑可定量分离三个 contribution：

| 比较 | 分离的因子 |
|---|---|
| **V1 − V0** | 2D mask decoder 在 VLM bbox prompt 下的贡献 |
| **V2 − V0** | native-3D text-promptable decoder 在 VLM 语言理解下的贡献 |
| **V2 − V1** | "text prompt vs. bbox prompt" 哪条桥更准 |
| **V4 − V2** | end-to-end joint training vs. parser+decoder plug-in 的 gap |

这套对照覆盖了"VLM 在 segmentation 里的真实角色"这条核心问题。

### 10.6 Stage 2 translation 规则

**SegVol（V2）：**

```python
def vlm_to_segvol(vlm: VLMOutput) -> SegVolPrompt:
    t = vlm.target
    parts = [t.organ]
    if t.lesion_type: parts = [t.lesion_type, "in", t.organ]
    if t.sub_region:  parts += [",", t.sub_region]
    return SegVolPrompt(text=" ".join(parts))   # e.g. "cyst in liver, segment IV"
# SegVol call: segvol.predict(volume=ct_dhw, text=prompt) → (D,H,W) prob mask
```

**MedSAM 2（V1）：**

```python
def vlm_to_medsam2(vlm: VLMOutput, z_indices: list[int]) -> list[Sam2BoxPrompt]:
    prompts = []
    for slice_idx, bbox in enumerate(vlm.spatial_hints.bbox_per_slice):
        if bbox is None: continue
        prompts.append(Sam2BoxPrompt(z=z_indices[slice_idx], bbox=bbox))
    return prompts
# Then: image_predictor for each prompt → mask_2d; video_predictor.propagate(...) → mask_3d
```

**TotalSegmentator / nnU-Net（V3）：**

```python
def vlm_to_totalseg(vlm: VLMOutput) -> str | None:
    organ = canonicalize_organ(vlm.target.organ)   # 'liver' → 'liver'
    if organ not in TOTALSEG_CLASSES: return None  # decoder cannot help; record as 'unsupported'
    return organ
# Then: totalseg.predict(volume=ct_dhw, classes=[organ]) → (D,H,W) mask
# NOTE: covers whole organ, not lesion within it — V3 is mostly useful when answer = whole organ
```

每条规则在 `tests/baseline/test_translate.py` 里有 unit test（每个 VLM JSON example → expected prompt）；这些 test **不打 live API、不需要 decoder weight**。

### 10.7 Stage 4 不变（已实现）

[`baseline_pilot100_report.md`](./baseline_pilot100_report.md) §6.1 soft Dice、§6.3 反投（仅 V0 用，其他 V 直接吃 decoder mask 跳过反投）、§10 三联 viz **全部复用**。

唯一新增：decoder 输出 `pred_prob ∈ [0,1]`（V2 SegVol）时**直接吃概率值**算 soft Dice，与 §4.1 的 `sigmoid > 0.5` 路径并行。pilot100 报告里的 Dice 公式（`2 × Σ(pred_prob · gt) / (Σpred_prob + Σgt + 1)`）对硬 mask 和软概率都成立。

### 10.8 Prerequisites & roadmap

```
Now        ─── V0 done（pilot100 实测）
Day 1-2    ─── V1-MedSAM 2 落地（复用 V0 API output cache）
Day 3-5    ─── V2-SegVol 落地（权重下载、text-prompt 验证）
Day 6-8    ─── V4-M3D-LaMed adapter（与 §3 M1 共用 spot-check gate）
Day 9-10   ─── V3-TotalSegmentator + 全 baseline 对照表
Day 11+    ─── V1-SAM 3 探索 / 加密 axial 采样 ablation
```

**前置条件：**

- [ ] 本地 GPU ≥ 12G 可用（V1-MedSAM 2）、≥ 24G（V2-SegVol）、≥ 40G（V4-M3D-LaMed）—— 见 `memory:h20_download_tooling` 和 `memory:project_sciencevision_server`
- [ ] SegVol / MedSAM 2 / M3D-LaMed / TotalSegmentator 权重能下载（个别需 HF token）
- [ ] pilot100 的 7-slice 加密到 16-slice 试验跑一次，给 Stage 1 一个合理的 z-density 起点
- [ ] 写一份 `tests/baseline/test_translate.py`（≥ 6 个 case，每条 translation 规则覆盖）

### 10.9 与 §3 方法矩阵的关系

§3 的 M1–M4 矩阵是 **`sci3d_bench` 框架下** 4 方法 ablation 的目标态；本节 §10 V-系列是 **mask 产出的实现路径**。两个矩阵正交：

| §3 方法 | §10 V-系列对应 |
|---|---|
| **M1**（M3D-LaMed end-to-end） | **V4** — 与 paper 数字直接可比 |
| **M2**（Med-2E3 / GPT-4o-on-views / Claude / Gemini） | **V0** = M2 的 box-only 实现样例；**V1 / V2** = M2 接外部 decoder 的实现样例 |
| **M3**（无 vision agent） | 与本节正交（agent 不调 segmentation tool） |
| **M4**（有 vision agent + tool-use） | **V1 / V2** 是 M4 的"agent 调 segmentation tool"具体实现 |

也就是说：**§10 V-系列定义"VLM/agent 如何调 segmentation tool"，§3 定义"agent 在 sci3d_bench 里如何被 ablation"**。两个矩阵一起跑，能同时回答 *"vision 在 agent 里必要吗"* 和 *"agent 调 segmentation tool 时哪种 decoder 最有效"*。

下次 verify pipeline plan 迭代时，把 V1-MedSAM 2 / V2-SegVol / V4-M3D-LaMed 三个 adapter 实现纳入 `docs/superpowers/plans/2026-05-21-m3d-refseg-verify-pipeline.md`，作为 §3 M1 / M2 / M4 的具体 segmentation backend。
