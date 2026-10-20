General disciplinary multimodal reasoning：MMMU, MMMU-Pro, SciVerse, OlympiadBench。
Visual math / science reasoning：MathVista, MATH-Vision, MathVerse, We-Math, DynaMath, MathSight。
Professional visual-domain exams：OmniMedVQA, KorMedMCQA-V。
核心 gap：很多 benchmark 有图，但不一定证明图是必要的；真正适合讨论 visual importance 的，是那些有 text-only / vision-only / vision-rich / 多版本对照的工作，比如 MMMU-Pro、SciVerse、MathVerse、MathSight、DynaMath。


Benchmark	年份	学科范围	视觉角色
MMMU	2023	30 个大学学科，艺术、商业、科学、医学、工程等	图表、地图、表格、乐谱、化学结构式等作为专业题目证据。
MMMU-Pro	2024	MMMU 强化版	过滤 text-only 可答样本，并加入 vision-only/screenshot 设置，更适合研究“视觉是否必要”。
SciVerse	2025	科学问题	很贴你的问题：构造 knowledge-free/lite/rich 和 vision-rich/vision-only 版本，用来拆分科学知识、视觉理解、CoT 推理各自贡献。
OlympiadBench	2024	奥赛级数学、物理，中英双语	多模态科学竞赛题，常含图像/公式/物理图，难度高，适合看视觉和学科推理的耦合。
Humanity’s Last Exam	2025	多学科专家级问题	不是专门视觉 benchmark，但包含多模态题，适合“前沿专家学科问题中视觉是否仍是瓶颈”的宏观讨论。

如果你的目标是论证“学科推理中视觉不是附属，而是必要证据”，MMMU-Pro 和 SciVerse 最关键。MMMU-Pro 控制 text shortcut；SciVerse 更细，能拆开知识和视觉贡献。
数学/几何方向
Benchmark	年份	重点
MathVista	2023	visual context 中的数学推理，包含图形、函数、表格、几何等。
MATH-Vision	2024	来自真实数学竞赛的视觉数学题，覆盖 16 个数学分支。
MathVerse	2024	直接问 “MLLM 是否真的看懂数学图”；同一题构造多种文本/图像信息版本。
We-Math	2024	把视觉数学题按知识点层级拆解，分析知识不足、泛化不足、死记硬背等问题。
DynaMath	2024	动态生成视觉数学题变体，测试模型对图中数值、函数图像变化的鲁棒性。
GeoEval	2024	几何题，强调文本和图形的联合理解。
MathSight	2025	大学数学；包含 original、hand-drawn、photo-captured、text-only 条件，专门量化视觉输入贡献。

数学里我会重点看 MathVerse、MathSight、DynaMath。它们比 MathVista 更直接地追问“模型到底有没有用图”。
医学/专业图像方向
Benchmark	年份	重点
PMC-VQA	2023	医学论文图像 VQA，大规模医学视觉问答。
OmniMedVQA	2024	73 个医学数据集，12 种医学模态，20+ 解剖区域。
KorMedMCQA-V	2026	韩国医师资格考试多模态题，含 X-ray、CT、ECG、超声、内镜等。
