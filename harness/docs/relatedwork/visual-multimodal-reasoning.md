1. 视觉在多模态推理中是否必要
Benchmark	年份	核心问题
BabyVision	2026	明确测试“语言之外”的基础视觉能力，强调 MLLM 依赖语言先验、缺少儿童级视觉 primitives。
MMStar	2024	从已有 benchmark 中筛出“vision-indispensable”样本，控制 text-only 可答和数据泄漏，适合引用来论证视觉必要性。
MMMU-Pro	2024	在 MMMU 基础上过滤 text-only 可答题，并加入 vision-only/screenshot-like setting，专门增强视觉依赖。
BLINK	2024	“Can see but not perceive”：把经典 CV perception 任务改成 VLM benchmark，测试深度、对应、多视角等语言难以中介的能力。
MMVP / Eyes Wide Shut	2024	用 CLIP-blind pairs 构造视觉模式题，说明许多 MLLM 的视觉 encoder/grounding 是瓶颈。
HallusionBench	2023	诊断语言幻觉和视觉错觉纠缠，包含成对/控制组问题，适合讨论模型是否真正看图。
VisuLogic	2025	视觉中心逻辑推理，避免靠文本描述或语言捷径答题。
BLINK-Twice	2025	从“看见”推进到“观察后推理”，强调重复/主动视觉观察对推理的价值。
NPHardEval4V	2024	比较 visual、text、visual+text prompting 对复杂推理的影响，关注视觉输入如何改变推理表现。