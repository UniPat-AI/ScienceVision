更合适的筛选标准应该是：
agent 处在物理、3D、机器人、第一人称或模拟环境中；
视觉观察会影响状态估计、空间理解、affordance、导航或操作；
benchmark 不是单纯 VQA，而是和行动、探索、交互或具身任务链相关；
最好能揭示 language prior / metadata / text-only shortcut 不够用。
按这个标准，我会把候选换成下面这些。
Benchmark	年份	环境/任务	为什么更合适
EmbodiedBench	2025	多环境 embodied tasks	明确叫 vision-driven embodied agents，覆盖 household、navigation、manipulation、spatial awareness、visual perception、long-term planning。比 OSWorld 更贴近 embodied agent。
EPIC-Bench	2026	真实 embodied 图像，定位/导航/操作	很像 embodied 版 BabyVision：反对 QA/MCQ 里的语言捷径，要求 fine-grained visual grounding，包含 target localization、navigation、manipulation 三个 interaction pipeline 阶段。
Embodied3DBench	2026	3D embodied spatial intelligence	测 grounding、spatial relation、multi-view correspondence、affordance、grasp point、trajectory prediction，直接问 VLM 是否具备交互所需的 3D 视觉先验。
ManipBench	2025	低层机器人操作	关注 VLM 是否能理解 object-object interaction、deformable object manipulation、精细机器人动作决策。比一般视觉推理更接近“视觉如何影响操作”。
CHAIN	2026	交互式 3D 物理环境	标题就是 “From Perception to Action”。测试模型能否把几何、接触、支撑关系转化为动作序列，适合论证视觉结构理解对交互决策的重要性。
EgoBench	2026	第一人称视频 + 工具 + 用户交互	egocentric-video-grounded tasks，强制结合视觉感知、工具调用、多跳推理和动态用户反馈。适合 wearable / embodied assistant 方向。
CityEQA	2025	城市场景 embodied QA	agent 需要主动探索城市 3D simulator 回答 open-vocabulary questions，强调环境、行动、感知三者耦合。
EfficientEQA	2024	家庭机器人 embodied QA	不是纯 benchmark，但问题设置很 relevant：agent 必须主动探索未知环境、收集视觉证据，再回答开放词表问题。
VLN-MME	2025	Vision-and-Language Navigation	诊断 MLLM 作为 language-guided visual navigation agent 的能力，关注多轮空间推理和 sequential action prediction。
WAGIBench	2025	可穿戴第一人称多模态 goal inference	有 modality ablation，能直接看视觉、音频、数字上下文等模态对 goal inference 的贡献；但它偏 goal inference，不是完整 action benchmark。