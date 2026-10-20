# 3D 资产 × VQA 状态总览

整理日期：2026-05-21（增补 H20 `_dl_pool` 拓展数据集）
范围：
- 本地工作目录：`/Users/sue/Research/ScienceVision/datasets/`
- HF dataset `SueMintony/ScienceVision-datasets`（本地 `_matched/` 的备份镜像）
- HF dataset `SueMintony/ScienceVision`（领域素材库）
- **H20 暂存池：`UniPat-ScienceVision-H20:/nvme3/matianyi/sci3d-big/_dl_pool/`**（已下载但未配对入 `_matched/` 的 VQA 数据集）

本文档区分三类：
1. **A. 直接带 3D mesh / volume 的 VQA 数据集（已配对）** — 已被 `scripts/match/build_matched_3d_qa.py` 写入 `_matched/index.json`：3D 文件 + QA + scene_id 配对全部成立。
2. **A'. 直接带 3D mesh / volume 的 VQA 数据集（H20 已下载，待接入 builder）** — 在 H20 `_dl_pool` 已经下载完整的 3D 资产 + QA，但 `build_matched_3d_qa.py` 还没有对应的 `iter_*` 子模块；只要补 builder + 镜像 3D 文件到运行环境即可升入 A 类。
3. **B. 带 3D mesh / volume 但不是 VQA** — 只有 3D 资产，没有 QA 对（或 QA 对存在但与本地 3D 资产匹配率为 0）。这是后续自动造题的源数据池。

> 本文档不重复列举无 3D 字节的纯 2D benchmark（如 3DSRBench、AstroLLaVA、CharXiv、DesignQA 等），它们已在 `datasets/THREE_D_INVENTORY.json` 的 `_meta.non_3d_excluded` 段落中标注。

---

## A. 直接带 3D mesh / volume 的 VQA 数据集

权威来源：`datasets/_matched/index.json` 是构建器（`scripts/match/build_matched_3d_qa.py`）在 disk 模式下实际配对成功的清单。

| Dataset | 配对记录数 | 3D 资产格式 | 资产位置 | 备注 |
|---|---:|---|---|---|
| **ScanQA** | 7,173 | ScanNet 三角网格 `.ply` | `datasets/_shared_3d/scannet/scans_test/<scene_id>/<scene_id>_vh_clean_2.ply` | 全部来自 test_wo_obj split（6149 题）+ 训练范围 8 个 TOS-authorized scene 覆盖的 val 题（1024 题）。test_w_obj、剩余 val、train 在 disk 模式下不可见，需用 manifest 模式 + H20 上的 705 训练 scene 才能补齐至 41,338 题（见 `datasets/_shared_3d/scannet/_tools/available_train_scenes.txt`）。|
| **SQA3D** | 578 | ScanNet 三角网格 `.ply` | 同上 | 仅 test split 的子集；val/train 与 ScanQA 同理需 manifest 模式扩展到 33,311 题。 |
| **M3D-RefSeg** | 2,777 | NIfTI CT volume `ct.nii.gz` + 分割 mask `mask.nii.gz` | `datasets/M3D-RefSeg/M3D_RefSeg/s0000…s1999/` | 208 cases、每条记录附带一个 referring expression、答案 + mask_id；本地 NiiVue 直接渲染。无 DMCA 风险。 |
| **M3D-Seg / 0005 (KiPA22)** | 20 | NIfTI volume `image.nii.gz` + mask `mask.nii.gz`（来自 `_derived/m3d-seg-0005/` 的预转换） | `datasets/_derived/m3d-seg-0005/{0..19}/` 渲染用；canonical `.npy` 仍在 `datasets/M3D-Seg/M3D_Seg/0005/` | 严格意义上**没有现成 QA 对**（split 文件只是 segmentation gold），但 `iter_m3d_seg_0005` 在 builder 里发出**合成 Q/A**：从 `0005.json` 类目录派生 `'Which class is highlighted?'` 类的合成题。20 条记录因此被纳入 _matched。|
| **EmbodiedScan demo** | 3 | `mesh.ply`（3 个公开样例 scene）| `datasets/EmbodiedScan/demo_data/openscan/{office,restroom,restroom2}/` | 同样无 QA 对，构建器作为 asset-only 记录发出（`extra.no_qa = true`）。完整 EmbodiedScan 标注需 Google Form 审批。 |

**总计：disk 模式 10,551 条，manifest 模式（H20 705 个训练 scene 投影）77,449 条。**

格式分布：`mesh` (ply) 7,754 / `nifti` 2,797 / `ply` 3。

### 关于 manifest 模式

`build_matched_3d_qa.py --scannet-availability=manifest --scannet-availability-manifest=…` 接受一份 scene-id 列表，把远端权威主机（H20 / 部署机）已下载的训练 scene 视作"可见"。本地不需要握有 1.2 TB 的训练 tarball，就能产出与服务端一致的 41,338 ScanQA + 33,311 SQA3D 记录。该模式由 PR #22 落地于 main，详见 [PR #22 描述](https://github.com/SueMarsR/Science-Vision/pull/22)。

### 已剔除的不完整 ScanNet 训练 scene

`datasets/_shared_3d/scannet/_tools/incomplete_train_scenes.json` 收录 H20 下载失败的 21 个训练 scene：
- 2 个 (`scene0066_00`, `scene0237_00`) `_vh_clean_2.ply` 缺失 → 整体剔除
- 19 个仅缺辅助文件（`.labels.ply`/`.aggregation.json`/`.0.010000.segs.json`/`.txt`）→ 仍发出 QA 记录，但 `extra.scannet_missing_files` 注解被消费方下游使用

---

## A'. 直接带 3D mesh / volume 的 VQA 数据集（H20 已下载，待接入 builder）

H20 暂存路径：`UniPat-ScienceVision-H20:/nvme3/matianyi/sci3d-big/_dl_pool/`（rsync / hf 下载落点）。这部分数据集**满足 A 类的全部硬条件**（3D 字节 + 现成 QA pairs + 配对 ID 都在），只是 `build_matched_3d_qa.py` 还没写对应的 `iter_<name>()` 函数。把它们接入 builder 并把 3D 字节同步到运行环境（H20 / 部署机）后，立刻升入 A 类。

| Dataset | 待配对 QA 数 | 3D 字节 | QA 文件 | H20 路径 | 接入难点 |
|---|---:|---|---|---|---|
| **3D-RAD**（NeurIPS 2025 Med-VQA，CT 6 任务） | 170,105（test 33,910 + train 136,195） | `.npy` × 5,910 volumes（test 1,304 个 scene，每 scene 多个 npy；train 还需另传） | 12 个 task CSV，分 6 大类：Image_Observation / Anomaly_Detection / Medical_Computation / Existence_Detection / Static_Temporal_Diagnosis / Longitudinal_Temporal_Diagnosis | `_dl_pool/3D-RAD-data/{train,test}/Task[1-6]_*/*.csv` + `_dl_pool/3D-RAD-data/Images/test.zip` (20 GB) | (1) test.zip 待解压（5,910 npy）；(2) train 的 image npy 还没下到 H20，只有 QA CSV；(3) 写 `iter_3drad()`，按 `VolumeName` 字段匹配 npy；(4) `.npy` 渲染：viewer 需补 `.npy → NIfTI / PNG slice` 转换。 |
| **DeepTumorVQA**（AbdomenAtlas-1.1，CC-BY-NC-4.0） | 395,612 QA（4 类：Recognition / Measurement / Visual Reasoning / Medical Reasoning） | 3D CT volumes × 9,262（来自 17 个公开 abdominal CT 集合的统一映射） | `Tumor_VQA_dataset_V3.csv`（395,612 行）+ `AbdomenAtlas_ID_mapping.csv`（9,262 行 BDMAP→source） | `_dl_pool/DeepTumorVQA-QA/`（QA 已下，103 MB）；**3D volumes 还没拉**——需要走 AbdomenAtlas 渠道（hf `AbdomenAtlas/AbdomenAtlas1.1`，~1 TB） | (1) 需要先下 AbdomenAtlas volumes 到 H20；(2) 写 `iter_deeptumorvqa()`，按 `Image ID` (BDMAP_xxxxxxxx) 匹配；(3) 9,262 个 volume × 平均 (512×512×~300) 体积，HF 镜像策略需要分批。 |
| **QueryCAD**（IROS-paper CAD 工业件 VQA） | 37 cases，每 case 多个 query（`keywords.txt` 每行 = 一道 query，`solution.txt` 同行对应 ground truth） | `.step` / `.stp` × 37（abc + industrial 两个子集合） | `keywords.txt` + `solution.txt` per case，外加 `prompts/prompt-query-matching-part.xml`（system prompt 模板） | `_dl_pool/QueryCAD/resources/cad-models/extracted/{abc,industrial}/processed/<id>/` | (1) 写 `iter_querycad()`，逐行解析 `keywords.txt`；(2) `.step` → `.glb` 转换（OCCT），让 viewer 能渲染。 |
| **SuperCLEVR-3D**（部件遮挡 / 姿态 / 部件识别 3 子任务） | 模板，需要按场景生成 → 期望 ~10K 量级 | `.blend` × 31 场景源 + `.png` × 39 渲染图 + `metadata*.json`（属性表） | `question_generation/{super_clevr_pose,super_clevr_object_occlusion,super_clevr_occlusion,super_clevr_templates}/`（CLEVR-style functional program 模板） | `_dl_pool/SuperCLEVR-3D/` | (1) 跑 `generate_questions.py` 生 QA JSON（依赖 scene properties，需要 Blender 配合）；(2) `.blend` → `.glb` 导出供 viewer 显示；(3) 写 `iter_superclevr3d()`。 |
| **M3D-Seg full**（`0000`–`0024` 共 25 子集） | ~5,771（每 case 一道合成 "Which class is highlighted?"），是当前 A 类 `m3d-seg-0005` (20 条) 的 288× 扩展 | 5,771 cases，每 case `image.npy` + `mask_*.npz` | 25 个 `<subset>.json` split + `term_dictionary.json` 类目录 | `/nvme3/matianyi/sci3d-big/M3D-Seg/M3D_Seg/` (1.3 TB) | 已有 `iter_m3d_seg_0005`，**逻辑直接泛化为 `iter_m3d_seg_all` 即可**：循环 25 个子集，按 `<subset>.json` 内的 `train + test` 列表枚举 case；唯一额外工作是为每个 case 跑 `npy → nifti` 转换（参照现有 `_derived/m3d-seg-0005/` 流水线，扩到全部 5,771 case）。 |

**A' 类合计可接入 QA 量级：~570K（3D-RAD 170K + DeepTumorVQA 395K + QueryCAD ~37×N + SuperCLEVR 待生成 + M3D-Seg full ~5.7K）**

> ⚠️ 落地建议优先级：
> 1. **M3D-Seg full**（最快，逻辑已存在，只需扩 builder + 批量 npy→nifti）
> 2. **3D-RAD**（test split 已完整，先接入 33,910 条 test 题；train 等 image 落到 H20 后再加）
> 3. **QueryCAD**（量小但都是 CAD 工业件，可作为 step→glb 流水线的试点）
> 4. **SuperCLEVR-3D**（需要先跑 generate_questions.py，以及 Blender 渲染）
> 5. **DeepTumorVQA**（量最大、影像也最大，等 AbdomenAtlas 落到 H20 再做）

---

## B. 带 3D mesh / volume 但不是 VQA（领域素材池）

这部分是 `SueMintony/ScienceVision`（HF dataset）和 `datasets/EmbodiedScan` 残余的内容。它们具备真实 3D bytes，但没有现成的 question-answer 对，定位是**自动造题的源数据池**，将来由 `scripts/v_classify/` 或专门的合成 QA pipeline 处理。

### B.1 工业 / CAD

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **industrial/** | `.glb` × 8, `.gltf` × 2, `.obj` × 2, `.step` × 2 | 23 | `SueMintony/ScienceVision:industrial/{broken,khronos,…}/` | 几何形状问答、零件分类、是否封闭体等。 |

> QueryCAD 和 SuperCLEVR-3D 已经在 H20 拿到了 prompts / question_generation 模板，请见 **A' 类**，不再列在 B.1。

### B.2 医学影像（CT / MR volumes）

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **medical/** | `.gz` × 654 (NIfTI), `.dcm` × 4 | 667 | `SueMintony/ScienceVision:medical/` | volume 维度大、模态分布广。可生成器官存在性、长径测量、模态识别类问题。 |

> 3D-RAD 已确认是真 VQA（170,105 题），已转入 **A'.3D-RAD**，B.2 不再列。DeepTumorVQA 同理（A'.DeepTumorVQA，等 AbdomenAtlas 落地）。

### B.3 生命科学 / 分子结构

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **biology/** | `.cif` × 34, `.sdf` × 5, `.mol` × 5, `.mol2` × 5, `.xyz` × 5, `.bcif` × 3 | 62 | `SueMintony/ScienceVision:biology/{alphafolddb,…}/` | 蛋白结构（来自 AlphaFold DB，例 `AF-P00533-egfr_human.cif`）+ 小分子（SDF/MOL）。可生成构象、活性位点、原子计数类问题。 |

### B.4 光子学 / 电磁仿真

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **photonics/** | `.lsf` × 12, `.fsp` × 8, `.lms` × 4, `.jpg` × 12, `.json` × 11, `.md` × 9 | 68 | `SueMintony/ScienceVision:photonics/ansys_optics/…` | Lumerical FDTD 工程文件 + 渲染图 + 文档。可生成 metasurface 几何参数、光谱峰位、目标属性类问题。**注意 .fsp / .lsf / .lms 是闭源 Ansys/Lumerical 格式 — web platform 无法直接渲染**，需要预先转 mesh / 提取场图像。 |
| **topological_photonics/** | `.dat` × 5, `.gds` × 1, `.ctl` × 1 | 7 | `SueMintony/ScienceVision:topological_photonics/{specs_and_layouts,zenodo_15591239_*}/` | 拓扑光子结构 + KLayout GDS。带 cap 计算结果（`.dat`），可生成带隙问答、缺陷态识别。同样需要预渲染。 |
| **Nanophotonic（H20）** | NeurIPS 2023 nanophotonic structures repo（meep 仿真生成器） | 12 个 .py + scripts + spectra/CIE_illum_*.csv | `_dl_pool/Nanophotonic/`（708 KB，含 README、scripts、`nanophotonic_structures/{nanocones,doublenanocones,combinatorial}_{2d,3d}.py`） | 仅是 dataset **生成器**仓库，本地无 3D 字节；3D 数据要么用 meep 跑、要么去 HF `jungtaekkim/datasets-nanophotonic-structures` 拉。仿真到 mesh 后可造表面结构、几何参数、谱线峰位类问题。 |

### B.5 通用 3D 几何 / 点云 / 机器人

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **pointcloud/** | `.las` × 2 | 2 | `SueMintony/ScienceVision:pointcloud/las_samples/` | LAS 激光雷达样本（laspy/PDAL 测试）。规模太小，仅作格式 demo。 |
| **robotics/** | `.obj` × 8, `.urdf` × 2, `.stl` × 2, `.sdf` × 1 | 13 | `SueMintony/ScienceVision:robotics/urdf_sdf_samples/` | URDF/SDF 机器人描述 + KUKA iiwa / 手指 STL。可生成关节数、自由度、工作范围类问题。 |

### B.6 多物理场仿真

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **simulation/** | `.stl` × 10, `.mph` × 7, `.json` × 9, `.h5` × 2, `.pdf` × 2 | 33 | `SueMintony/ScienceVision:simulation/comsol/figshare_28545548__ear_canal_stl/` | COMSOL 仿真包 + 几何 STL。**`.mph` 是 COMSOL 闭源格式 — web 不可直接渲染**，但 STL 可。可生成场最大值位置、边界条件类问题。 |
| **PDEBench_3D（H20）** | `3D_CFD_Turb.hdf5` × 1 | 1 | `_dl_pool/PDEBench_3D/`（62 GB） | NeurIPS 2022 PDEBench 3D 湍流 CFD 仿真数组。可造速度场极值位置、湍动能、再生长率等定量问题；hdf5 → npy/nifti slice 后才能进 viewer。 |

### B.7 天文（含极小规模 manifest）

| 名称 | 文件类型 | 文件数 | 路径 | 自动造题方向 |
|---|---|---:|---|---|
| **astronomy/** | `.json` × 14, `.jpg` × 2, `.png` × 2 | 19 | `SueMintony/ScienceVision:astronomy/ephemeris/` | **不是 3D mesh** — 是星历 JSON + 2D 图像。严格说应归入 2D，但因 SueMintony/ScienceVision 已经把它放在领域素材库里（用于 VerifyBaseline 的天文 QA 种子），这里保留登记。 |

---

## C. 不在以上两类的边缘情况

| 名称 | 状态 | 说明 |
|---|---|---|
| **VSI-Bench** | 视频，不是 3D bytes | 5.4 GB MP4 walkthrough，引用 ScanNet/ScanNet++/ARKitScenes scene_id 但不携带 mesh。可在已 mirror 的 ScanNet test 集上恢复部分 mesh 配对，但本身不算 3D 数据集。 |
| **EmbodiedScan full release** | TOS-gated | 需要 Google Form 审批；本地只有 demo 3 个 scene。 |
| **M3D-VQA** | DMCA-disabled | M3D-Cap 上游被 DMCA，VQA CSV 里的 image 引用是 dangling 的。可能镜像（ModelScope / OpenDataLab / BAAI）未验证。 |

---

## D. 总结表

| 类别 | 数据集数 | 可立即用于 VQA 评测 | QA 总量级 | 备注 |
|---|---:|---:|---:|---|
| **A. 已配对到 `_matched/`** | 5（ScanQA, SQA3D, M3D-RefSeg, M3D-Seg/0005, EmbodiedScan demo） | ✓ | 10,551 disk / 77,449 manifest | 全部已落 main，builder 已支持 |
| **A'. H20 已下载、待接入 builder** | 5（3D-RAD, DeepTumorVQA, QueryCAD, SuperCLEVR-3D, M3D-Seg full 25 subsets） | 待 builder + 3D 字节同步 | ≥ ~570K | 见 A' 优先级 |
| **B. 仅 3D 资产，无 QA** | 10（B.1×1 + B.2×1 + B.3×1 + B.4×3 含 Nanophotonic + B.5×2 + B.6×2 含 PDEBench_3D + B.7×1） | — | — | ~1,100 个 3D / 仿真 / 结构文件 |

A 类总记录：**10,551（disk）/ 77,449（manifest）**
A' 类可接入 QA：**~570K**（最大头是 DeepTumorVQA 395K + 3D-RAD 170K）
B 类总文件：**~1,100** 个 3D / 仿真 / 结构文件

---

## E. 下一步建议

### E.1 短期（1–2 周）— 把 A' 类升入 A

1. **M3D-Seg full**（最划算）
   - 扩 `iter_m3d_seg_0005` → `iter_m3d_seg_all`：循环 `M3D_Seg/00{00..24}/`，按各 `<subset>.json` 的 `train + test` 列表枚举 case。
   - 批量 `npy → nifti` 流水线扩到所有 5,771 case（参照 `_derived/m3d-seg-0005/` 现有脚本）。
   - 预期：disk 模式 ~5,771 条新 QA。
2. **3D-RAD test split 接入**
   - 解压 `_dl_pool/3D-RAD-data/Images/test.zip`（20 GB → ~25 GB）到 H20 工作目录。
   - 写 `iter_3drad_test()`，按 `VolumeName` (CSV 字段) ↔ `test/test_<id>/test_<id>_<x>_<n>.npy` 匹配。
   - viewer 补 `.npy → NIfTI / PNG slice` 转换（已有 npy 读取代码，复用 NiiVue 路径）。
   - 预期：33,910 条新 QA。
3. **QueryCAD**（试点 step→glb 流水线）
   - 写 `iter_querycad()`：逐 case 解析 `keywords.txt` (query) ↔ `solution.txt` (answer)。
   - `.step` → `.glb`（OCCT）转换脚本，落到 `datasets/_derived/querycad/`。
   - 预期：~37 case × N query 的小规模评测集。

### E.2 中期 — 解决渲染瓶颈

B 类需做 web 渲染兼容性预筛：Lumerical (.fsp/.lsf/.lms)、COMSOL (.mph)、KLayout (.gds)、Blender (.blend) 都是闭源/工程格式 — 浏览器三件套（Three.js / NiiVue / 3Dmol.js）无法直接显示。需要预转换流程：

- `.step/.stp` → `.glb` (OCCT)
- `.fsp/.mph` → 预渲染图像 + 元数据 JSON
- `.blend` → glb 导出
- `.cif/.bcif/.mol/.sdf` → 3Dmol.js 直读
- `.hdf5`（PDEBench）→ npy slice 序列 + NiiVue 序列查看

### E.3 长期 — A' 剩余两个

4. **SuperCLEVR-3D**：跑 `question_generation/generate_questions.py`（需要 Blender + scene properties），把生成结果落盘后写 `iter_superclevr3d()`。
5. **DeepTumorVQA**：等 AbdomenAtlas-1.1（~1 TB）落到 H20 后，写 `iter_deeptumorvqa()`；可分批镜像（4,711 trauma + 4,100 FLARE'23 是两个最大头）。

---

## F. 相关文件路径速查

### F.1 本地 / 仓库内

| 对象 | 路径 |
|---|---|
| 配对清单（A 类权威） | `datasets/_matched/{scanqa,sqa3d,m3d-refseg,m3d-seg-0005,embodiedscan-demo,index}.json` |
| ScanNet 训练 scene 可用性清单 | `datasets/_shared_3d/scannet/_tools/available_train_scenes.txt` |
| ScanNet 训练 scene 残缺清单 | `datasets/_shared_3d/scannet/_tools/incomplete_train_scenes.json` |
| 总 3D 资产 audit | `datasets/THREE_D_INVENTORY.json` |
| 配对器源码 | `scripts/match/build_matched_3d_qa.py` |
| 数据下载计划 | `docs/dataset_download_plan.md` |
| HF 数据集（_matched 镜像） | `SueMintony/ScienceVision-datasets` |
| HF 数据集（领域素材） | `SueMintony/ScienceVision` |

### F.2 H20 `_dl_pool`（A' 类待接入数据）

主机：`UniPat-ScienceVision-H20`（10.0.0.132），根目录 `/nvme3/matianyi/sci3d-big/`

| 数据集 | 大小 | 路径 | 关键文件 |
|---|---:|---|---|
| **3D-RAD** | 201 MB（paper repo） + 20 GB（test images） | `_dl_pool/3D-RAD/`（README + RadFM/M3D 评测代码）`_dl_pool/3D-RAD-data/`（QA + Images） | `train/Task[1-6]_*/*.csv`, `test/Task[1-6]_*/*.csv`, `Images/test.zip` |
| **DeepTumorVQA** | 103 MB | `_dl_pool/DeepTumorVQA-QA/` | `Tumor_VQA_dataset_V3.csv` (395,613 行), `AbdomenAtlas_ID_mapping.csv` (9,262 行) |
| **QueryCAD** | 48 MB | `_dl_pool/QueryCAD/` | `resources/cad-models/extracted/{abc,industrial}/processed/<id>/{cad.step,keywords.txt,solution.txt}` |
| **SuperCLEVR-3D** | 291 MB | `_dl_pool/SuperCLEVR-3D/` | `question_generation/{generate_questions.py, super_clevr_*/...}`, `image_generation/*.blend` |
| **Nanophotonic** | 708 KB（生成器代码） | `_dl_pool/Nanophotonic/` | `scripts/`, `nanophotonic_structures/*.py`, `spectra/CIE_*.csv` |
| **PDEBench_3D** | 62 GB | `_dl_pool/PDEBench_3D/` | `3D_CFD_Turb.hdf5` |
| **M3D-Seg full（25 子集）** | 1.3 TB | `M3D-Seg/M3D_Seg/{0000..0024}/` | 每子集 `<subset>.json` + `case_*/{image.npy, mask_*.npz}`，共 5,771 case |
| **ScanNet 训练 scene 全集（705 个）** | 9.8 GB（仅 mesh）| `_shared_3d/scannet/scans/scene*/` | 用于 manifest 模式扩展到 41,338 ScanQA + 33,311 SQA3D |
| **_dllogs** | 1.1 MB | `_dl_pool/_dllogs/` | `3drad.log`, `hf_upload_*.log`, `pdebench_*.log`, `rsync.log` |

### F.3 HF 远端镜像状态（2026-05-21 上传）

两个 HF dataset repo（账户 `SueMintony`）按 `.env` 配置切成 QA / 3D 两路：

- **`SueMintony/ScienceVision-datasets`**（QA / 题面，**public** since 2026-05-21，累计 23.5 GB）
- **`SueMintony/ScienceVision`**（3D 素材，**public** since 2026-05-21，累计 87.3 GB）

> **配额状态**：两个 repo 已转 **public**（HF Free 对 public repo 不限容量），110 GB
> 全部从 Free tier 私仓 100 GB 上限里出账，配额风险解除。下次大文件上传（如
> DeepTumorVQA 用的 AbdomenAtlas-1.1 ~1 TB）也不会再撞临时翻倍检查。 license 已在
> 上传前 vetted（CC-BY-NC-SA / CC-BY-NC-4.0 / Apache-2.0 / ABC CC-BY-4.0），
> 与公开发行兼容。

| 数据集 | HF repo | 仓库内路径 | 大小 | 内容 |
|---|---|---|---:|---|
| **3D-RAD QA** | `ScienceVision-datasets` | `3D-RAD/{train,test}/` | ~200 MB | 12 个 Task CSV（train + test），94 files |
| **3D-RAD CT volumes** | `ScienceVision` | `3D-RAD/Images/test.zip` | 20.64 GB | 5,910 个 `.npy` volume（test split） |
| **DeepTumorVQA** | `ScienceVision-datasets` | `DeepTumorVQA/` | 103 MB | `Tumor_VQA_dataset_V3.csv` + `AbdomenAtlas_ID_mapping.csv` + README |
| **QueryCAD** | `ScienceVision` | `QueryCAD/` | 48 MB | 109 files（abc + industrial STEP/STP + keywords/solution） |
| **SuperCLEVR-3D** | `ScienceVision` | `SuperCLEVR-3D/` | 291 MB | 111 files（`.blend` + scene graphs + `question_generation/`） |
| **PDEBench 3D CFD** | `ScienceVision` | `3D_CFD_Turb.hdf5` ⚠️ at repo root | 62 GB | 单个 HDF5（`hf upload-large-folder` 不支持 `PATH_IN_REPO`，server-side rename 因当时私仓 132 GB 临时翻倍超限失败；现已转 public，可重新尝试移到 `PDEBench_3D/` 子目录） |

### F.4 本批次上传后的 A' / B 配对状态汇总

| 数据集 | A' / B 类 | H20 ✓ | HF ✓ | builder ✓ | viewer adapter ✓ | 阻塞项 |
|---|---|:---:|:---:|:---:|:---:|---|
| **3D-RAD** | A' | ✓ | ✓ | ✗ | ✗ | 写 `iter_3drad()` + `.npy → NIfTI/PNG` viewer adapter |
| **DeepTumorVQA** | A'（QA only） | ✓（QA） | ✓（QA） | ✗ | ✗ | 先要镜像 ~1 TB AbdomenAtlas-1.1 到 H20 |
| **QueryCAD** | A' | ✓ | ✓ | ✗ | ✗ | 写 `iter_querycad()` + `.step/.stp` viewer adapter（OCCT） |
| **SuperCLEVR-3D** | A' | ✓ | ✓ | ✗ | ✗ | 跑 `generate_questions.py` 生 QA + `.blend → .glb` 导出 |
| **PDEBench 3D CFD** | B.6 | ✓ | ✓（at root） | — | ✗ | 无 QA；先写 `.hdf5` slice viewer adapter |
