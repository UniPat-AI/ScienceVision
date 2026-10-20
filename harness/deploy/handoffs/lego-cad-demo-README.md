# LEGO 功能装配 — CAD 资产 + 可交互物理仿真 + 零件拆解 Demo

用 LEGO（LDraw/OMR 真实 set）作为工业装配的受控代理，研究"组合/构造功能机构 + 用代码验证其运动性"。本包含三类产物，全部由真实零件几何 + 约束物理求解器生成。

## 1. CAD 资产（`cad/`）
真实 LEGO 机构导出的 **STEP**（ISO-10303 AP214，可在 SolidWorks/Fusion/Onshape/FreeCAD 打开）。
**`cad/step/` 含全部 12 个真实 set 的 STEP**（发动机/差速器/变速箱/凸轮轴，每个零件一个命名实体）：
- 发动机：`42107_engine`、`42056_flat6-engine`、`42083_w16-engine`、`42110_engine`、`42115_v12-engine`、`42110_camshaft`
- 变速箱：`42056_gearbox`、`42083_gearbox`、`42107_gearbox`、`42115_gearbox`、`42110_reargearbox`
- 差速器：`42115_differential`
- `cad/42107_engine.obj` / `.stl` — 发动机的网格格式样例（给 Fusion 等做 Mesh→BRep 的输入）。
- `cad/engine_step_view.png` / `engine_step_facets.png` / `engine_exploded.png` — 渲染/面片特写/爆炸预览。
- `meshes/*.glb` — 4 个模型的 glTF 网格（发动机/差速器/8835 叉车/6607），可直接拖进 Blender。
- **说明（诚实）**：LDraw 是网格几何，所以 STEP 是 **faceted（三角面）B-rep、非参数化**（无特征树、不能改尺寸），是 shell 不是封闭 solid。可测量/互通，不可参数化编辑。这些 CAD 资产**没有配套运动 demo**（运动 demo 仅发动机+差速器，见下）。

## 2. 可视化 + 可操作 物理仿真 Demo（`interactive/`）
**自包含离线 HTML**（three.js + 真实网格 + 求解器轨迹全部内联）。**双击打开即可，无需 server / 无需联网。**
- `standalone_42107_engine.html` — 真实发动机：驱动曲轴 → 4 活塞往复、连杆随动；机体固定。
- `standalone_42115_differential.html` — 真实差速器：驱动任意轴（cage/L/R）→ 看涌现联动（驱动左轴时右轴反向转）。
- 左上下拉换驱动轴、调速度、拖拽旋转；面板显示代码算出的 **运动性测量**（自由度 / 速比 / 传动连通 / 约束残差）。
- 运动是约束动力学求解器算出的，不是关键帧动画。

## 3. 零件拆解 Demo（`interactive/` + `scripts/`）
- `standalone_explode_42107_engine.html` — 自包含离线：拖滑块把整机炸开成单个零件再复原，证明它是 (零件+位姿) 的可操作集合。
- `scripts/explode_model.py` — Blender 版拆解/重上色脚本（需 Blender + ImportLDraw 插件）。
- `cad/engine_exploded.png` — 爆炸视图静态预览。

## 4. 运动检测（`motion/`）
- `engine_check.png` / `diff_check.png` — 每个自由度的位置曲线（**最直观的"能不能动"证明**：发动机曲轴 720°+ 4 活塞精确 ±10 LDU 正弦；差速器 L+R=2×cage）。
- `*_motion.gif` — 3D 运动动画。

## 5. 工具脚本（`scripts/`）
- `make_step_ocp.py` — 任意 `.ldr` → STEP（OpenCASCADE，无需 FreeCAD）。
- `ldraw_to_step.py` — FreeCAD 版（可移植）。

> 研究定位：LEGO 是受控代理，非真实工程 CAD。北极星 = 用可信的验证器/仿真判断"生成的机构能不能动、传动对不对"。
