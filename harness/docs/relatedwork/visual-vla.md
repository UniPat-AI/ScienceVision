在 2023 年到 2026 年间，随着 RT-2、π0 等基础模型的爆发，学界的研究重点已经从“如何把模型做大”转向了“这些模型到底学到了什么”。
针对“VLA（视觉-语言-动作）模型中的 vision 是否真的有效”这一问题，最近两年（特别是 2025-2026 年）涌现了大量非常深入的诊断性（Diagnostic）和消融性（Ablation）工作。
核心结论
Vision（视觉）在 VLA 中绝对是起作用的，但它目前被利用的方式非常低效且脆弱。 目前的共识是：现有的自回归 VLA 模型并没有像我们期望的那样真正建立起“视觉语义-逻辑推理-物理动作”的深层因果关系，而是把视觉当成了一种**“视觉接地的轨迹模仿（Visually grounded trajectory imitation）”的捷径（Shortcut）**。模型能“看到”物体在哪里，但往往“忽视”物体的精细语义或物理状态。
以下是近三年内几篇最具代表性的工作及其探究结论：
1. 视觉利用的瓶颈：特征质量 vs. 注意力机制
代表工作：FocusVLA: Focused Visual Utilization for Vision-Language-Action Models (ArXiv, 2026)
•	探究重点： VLA 表现不佳，是因为视觉表征（Representation）本身不够好，还是策略网络对视觉信息的利用（Utilization）出了问题？
•	结论： 结论明确指向利用效率低下。现有的架构偏差导致模型在面对海量视觉 token 时，注意力极易被与任务无关的背景噪声分散，从而忽略了对于灵巧操作至关重要的精细视觉细节。VLA 的性能上限更多受限于“如何有效聚焦任务相关的视觉区域”，而不是单纯更换更强的视觉 Backbone。
2. 语义理解与视觉接地的断层
代表工作：VLA-Trace: Diagnosing Vision-Language-Action Models through Representation and Behavior Tracing (ArXiv, 2026)
•	探究重点： 视觉特征在 VLA 中到底是如何演变并控制动作的？
•	结论： 通过注意力敲除（Attention knockout）和行为探测，研究发现 VLA 存在严重的“知其然不知其所以然”的现象。模型的视觉注意力确实能准确定位（Localize）到与操作相关的物体区域，但如果对画面进行细粒度的语义修改，模型往往会完全无视这些变化。这证明模型高度依赖简单的轨迹匹配，缺乏真正的组合式视觉语义控制（Compositional semantic control）。
3. VLM 预训练与具身控制的领域鸿沟
代表工作：VLM4VLA: Revisiting Vision-Language-Models in... (OpenReview, 2025/2026)
•	探究重点： 一个在通用视觉任务（如 VQA、深度估计）上表现极好的 VLM，作为 VLA 的视觉底座时，动作输出就一定好吗？
•	结论： 并不一定。 提升 VLM 在特定辅助视觉任务上的能力，并不能保证下游控制性能的提升。当前的视觉编码器预训练目标与具身动作规划之间存在固有的领域鸿沟（Domain gap）。只有将明确的“控制相关监督信号（Control-relevant supervision）”直接注入到视觉编码器中，才能带来稳定的动作增益。
4. 走向视觉链式思考（Visual Chain-of-Thought）
代表工作：CoT-VLA: Visual Chain-of-Thought Reasoning for Vision-Language-Action Models (CVPR, 2025/2026)
•	探究重点： 既然直接从“视觉观测 -> 动作”的映射存在问题，能否通过显式推理来增强视觉的有效性？
•	结论： 研究表明，如果在生成动作之前，强制 VLA 自回归地预测未来的“子目标图像（Subgoal images）”，模型的表现会有显著跃升（真实机器人任务提升 17%）。这反向证明了，如果不加干预，传统 VLA 很难在隐空间内完成多模态的生成推理；让模型在行动前先“在视觉上想清楚（Think visually）”是极其有效的。