# Science Vision — 数据源与格式爬取调研

> **用途**：为统一 3D 平台的数据准备阶段服务。为每个目标领域、每种核心 3D 文件格式，回答四个问题——「**去哪拿、里面是什么、用什么解析、踩什么坑**」。
>
> **配套**：与 [ProjectPlan.md](./ProjectPlan.md) §3.1（领域痛点）、§4.1（统一表示层）、§7（研究空白）联动。新增数据源应反向更新 ProjectPlan.md §10.2。
>
> **状态约定**：✅ 已核实可用 / ⚠️ 待核实 / 🔒 收费/受限 / 🩺 含 PHI 需脱敏。

---

## 0. 文档管理

| 版本 | 日期 | 主要变更 |
| --- | --- | --- |
| v0.1 | 2026-05-09 | 初稿：覆盖 ProjectPlan.md §3.1 三大领域核心格式，分级标注 |
| v0.2 | 2026-05-11 | 新增 §1.4 / §2.4 光学仿真（Ansys Optics / Lumerical 工程文件、采集策略、缺陷注入评估用法）；§3.1 与 §4 反向映射、§6 参考同步 |
| v0.3 | 2026-05-12 | 新增 §1.5 多物理仿真（COMSOL Multiphysics）：`.mph` archive-only / `.vtu` / 异构 HDF5 / 配套几何 / 官方 Gallery URL-only 边界。配合 `crawl_data/simulation/comsol/` 7 records / 80 MB 入库与 `viewer/field3d.html` 多 schema autodetect 升级（详见 CrawlDataAndViewer.md v0.7） |

---

## 1. 速查表（行动优先）

> 入门优先级：从下表挑 **优先级=高 + 法律=宽松 + 数据规模=适中** 的一行，先跑通单格式 PoC。

### 1.1 生物化学

| 格式 | 主用途 | 主源 | 抓取方式 | 法律 | 体量典型值 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| **PDBx/mmCIF** | 蛋白质/核酸大分子结构 | RCSB PDB | HTTPS 批量下载 / FTP / GraphQL API | CC0 | ~150 KB–50 MB/条 | ⭐ 高 |
| **legacy PDB** | 老格式蛋白结构 | RCSB PDB | 同上 | CC0 | 同上 | 中（建议优先 mmCIF） |
| **BinaryCIF** | 大型结构压缩二进制 | RCSB PDB / Mol\* CDN | HTTPS | CC0 | 比 mmCIF 小 5–10× | 中 |
| **AlphaFoldDB CIF** | AI 预测结构 | EBI AlphaFoldDB | HTTPS / bulk S3 | CC-BY 4.0 | ~50 KB–几 MB | ⭐ 高（覆盖广） |
| **EM Map (CCP4/MRC)** | 冷冻电镜密度图 | EMDB | HTTPS / FTP | CC0 | 几十 MB–几 GB | 低（v1 不必） |
| **SDF / MOL / MOL2** | 小分子/配体 | PubChem / ChEMBL / DrugBank | REST API / 批量 .sdf.gz | CC-BY-SA / 自定义 | ~1–10 KB/条 | ⭐ 高 |
| **PDBbind** | 蛋白-配体亲和力（structure+Ki/Kd） | PDBbind-CN | 注册下载 | 学术非商用 | ~20K 条 | 高（对接基准必备） |
| **MD 轨迹 DCD/XTC/NetCDF** | 分子动力学 | 各论文项目页 / GROMACS samples | 论文附件 | 不一 | 几十 MB–TB | 低（v1 不必） |

### 1.2 医学影像

| 格式 | 主用途 | 主源 | 抓取方式 | 法律 | 体量典型值 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| **DICOM** 🩺 | CT/MRI/PET/X-ray 临床标准 | TCIA、IDC（Imaging Data Commons）、OpenNeuro | NBIA Data Retriever / aws s3 / HTTPS | 多为公开脱敏 | 一例几百 MB 起 | ⭐ 高 |
| **NIfTI .nii / .nii.gz** | 神经影像/科研 3D 体数据 | OpenNeuro / Medical Segmentation Decathlon / BraTS / KiTS / LIDC | HTTPS / DataLad / S3 | 多 CC | 几 MB–几百 MB | ⭐ 高（最易上手） |
| **NRRD / MHA / MHD** | 医学图像处理科研 | Slicer 数据集、各论文 | HTTPS | 不一 | 类似 NIfTI | 中 |
| **DICOM-SEG / RTSTRUCT** | 分割/放疗结构 | TCIA（部分集合）、IDC | 同 DICOM | 同 DICOM | 较小 | 中（需求驱动） |
| **STL/OBJ（医学转表面）** | 器官/骨骼表面（术规、3D 打印） | 临床医院私有 / Embodi3D | HTTPS | 多自定义 | ~MB | 低 |

### 1.3 工业 CAD / CAE

| 格式 | 主用途 | 主源 | 抓取方式 | 法律 | 体量典型值 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| **STEP (.step/.stp, AP203/AP214/AP242)** | CAD 几何/装配/PMI 交换 | ABC Dataset、Fusion 360 Gallery、CAD-Net、DeepCAD、Thingi10K（部分） | HTTPS / S3 | 多学术研究用 | KB–几十 MB | ⭐ 高 |
| **IGES (.iges/.igs)** | 老 CAD 交换 | 同上 / CAD 原厂样例 | HTTPS | 同上 | KB–几十 MB | 低 |
| **Parasolid (.x_t/.x_b)** 🔒 | NX/SolidWorks 内核 | 多为商业，开源样例稀少 | — | 受 Parasolid 许可 | KB–MB | 低（v1 不碰） |
| **ACIS SAT (.sat)** 🔒 | ACIS 内核 | 商业为主 | — | 受 ACIS 许可 | 同上 | 低 |
| **STL (.stl)** | 三角面片，3D 打印事实标准 | Thingi10K、GrabCAD、ShapeNet（部分）、Thingiverse | HTTPS / 抓取需注意 robots.txt | CC 居多 | KB–几十 MB | 高（适合可视化层） |
| **OBJ + MTL** | 通用 mesh + 材质 | ShapeNet / ModelNet / Objaverse | HTTPS / S3 | 多学术 | 同上 | 高 |
| **glTF/GLB (.gltf/.glb)** | Web/实时渲染 | Khronos Sample Models、Sketchfab（部分免费） | HTTPS / API | CC0–CC-BY | 同上 | ⭐ 高（前端预览天选） |
| **USD/USDZ (.usd/.usda/.usdc/.usdz)** | 大规模场景容器 | Pixar 样例、NVIDIA SimReady、Apple AR Quick Look 样例 | HTTPS | CC0–自定义 | 几 MB–GB | 中（架构方案依赖度高） |

### 1.4 光学仿真（拓扑光学 / 电磁学）

| 格式 | 主用途 | 主源 | 抓取方式 | 法律 | 体量典型值 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| **Lumerical `.fsp`** 🔒 | FDTD 工程（几何+材料+网格+监视器+结果） | Ansys Optics Application Gallery | HTTPS（需 Ansys 账号） | © Ansys，工程文件需 license 重跑 | KB–几百 MB | ⭐ 高（含 GT） |
| **Lumerical `.lms`** 🔒 | MODE / EME 波导本征模 | 同上 | 同上 | 同上 | KB–几十 MB | 高 |
| **Lumerical `.lsf`** | Lumerical 脚本（参数化建模 / 后处理） | 同上、GitHub | HTTPS | 文本，可独立阅读 | KB | 高（即代码即 GT） |
| **Lumerical `.ldf`** | 仿真结果导出（HDF5 容器） | 同上 | 同上 | 同 `.fsp` | MB–GB | 中 |
| **MEEP / Tidy3D Python script + `.h5`** | 开源 FDTD 工程 + 结果 | MEEP 官方 examples、Tidy3D notebook gallery、Lumos | git / HTTPS | MIT / Apache | KB–MB | ⭐ 高（开源对照组） |
| **GDSII `.gds`** | 光子集成电路 layout | Lumerical Foundry 公开样例、IMEC SiPh demo、SiEPIC PDK | HTTPS / git | 多 MIT，PDK 受限 | KB–MB | 中 |
| **Zemax `.zmx` / `.zos`** 🔒 | 几何光学 / 序列光线追迹 | Zemax Knowledgebase | HTTPS（需账号） | © Ansys，license 重跑 | KB–MB | 中（v1 选配） |

### 1.5 多物理仿真（COMSOL Multiphysics — v0.7 起）

| 格式 | 主用途 | 主源 | 抓取方式 | 法律 | 体量典型值 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| **COMSOL `.mph`** 🔒 | 几何 + 材料 + 物理设置 + 网格 + 解（闭源容器） | (a) GitHub MIT 教学仓库 (b) Zenodo / figshare 论文配套 | HTTPS / GitHub raw / Zenodo API / figshare API | (a)(b) MIT / CC-BY-4.0 / CC0；**不可** redistribute COMSOL 官方 Application Gallery 文件 | KB–几十 MB | 中（archive-only：无开源 viewer，需 COMSOL License 才能打开） |
| **VTK XML UnstructuredGrid `.vtu`** | 非结构化网格 + 节点/单元标量（COMSOL 导出） | 论文配套 / GitHub | HTTPS | CC-BY-4.0 / MIT | MB–几十 MB | ⭐ 高（field3d 浏览器端可解析头部） |
| **HDF5（COMSOL/PINN schema）** | 非结构化 mesh + nodal scalars（与 MPB 不同，schema 因案例而异） | Zenodo paper-companion | HTTPS | CC-BY-4.0 | MB–几百 MB | 中（schema 异构，需逐案处理） |
| **配套 STL/STEP 几何** | 仅几何（去掉物理设置后导出） | 论文配套 | HTTPS | 同上 | KB–几十 MB | ⭐ 高（与 industrial CAD 路径相通，可直接走 gltf/occt viewer） |
| **COMSOL Application Gallery 链接** 🔒 | 官方案例的元数据 / 缩略图 / 案例 PDF | comsol.com/models | URL-only **不下载** | © COMSOL（[KB1223](https://www.comsol.com/support/knowledgebase/1223) 默认禁止再分发） | — | 高（外链与教学引用） |

**关键边界条件**：
- **COMSOL 官方 Application Gallery 严禁镜像**：KB1223 明文规定 "If you want to re-publish any information created by COMSOL, including but not limited to images, models, and documentation, please contact us directly." 因此即使是缩略图、案例 PDF 也只能保留 URL 引用。
- **`.mph` 是闭源二进制**：本平台无法在浏览器或 CI 内打开/解析，仅作为可下载语料保留。要从 `.mph` 派生几何或场，必须本地有 COMSOL License + 用 `comsol batch` 或 LiveLink 导出。
- **HDF5 schema 不通用**：MPB/MEEP 是均匀网格 + 复值场（datasets `data` / `data.r` / `data.i`）；COMSOL/PINN 是节点-连接性-标量混合（如 cardiac PINN scar mesh）。`viewer/field3d.html` 按路径前缀（`/topological_photonics/` vs `/simulation/comsol/`）自动识别 schema 并给出 schema-specific 处理建议；目前仅 MPB 一路有完整"HDF5 → NIfTI → Niivue"渲染管线，COMSOL-style 仍要走桌面 ParaView / pyvista。
- **VTU 浏览器端可分析**：XML 头部（`<Piece NumberOfPoints/NumberOfCells>` + `<DataArray Name= type=>`）可在前 64 KB 内解析，为 LLM agent 提供 mesh 规模 + 标量场名作为推理上下文，无需把 ~7 MB 二进制完整传到 LLM。

---

## 2. 详细规格与解析工具

### 2.1 生物化学

#### 2.1.1 PDBx/mmCIF（RCSB PDB）

- **入口**：<https://www.rcsb.org/>，FTP `https://files.rcsb.org/download/{PDB_ID}.cif`
- **批量**：`https://files.rcsb.org/pub/pdb/data/structures/all/mmCIF/`（约 23 万条目，gz 压缩）
- **API**：GraphQL `https://data.rcsb.org/graphql`、Search API `https://search.rcsb.org/`
- **内容**：原子坐标、链/残基/配体、晶体学信息、实验方法、二级结构、组装、对称操作。
- **解析工具**（Python）：
  - **gemmi** — 速度最快、功能最全（推荐）
  - **biotite.structure.io.pdbx** — Pythonic
  - **Biopython.PDB** — 老牌、生态熟
  - **MDAnalysis** — 更适合 trajectory
- **核心注意点**：
  - 建议**不要用 legacy PDB**，字段长度限制（5 位原子序号、4 位残基号），大复合物（如核糖体）已经无法用 PDB 装下。
  - **多模型条目**（NMR）：一个文件多 MODEL，注意是否需要展开。
  - **alt-loc / occupancy / B-factor**：同一原子可能有多个备选构象。
  - **氢原子**：晶体学结构通常无氢；NMR/计算结构有氢。一致性需统一。
  - **生物组装 vs 不对称单元**：crystallographic asymmetric unit ≠ biological assembly，需通过 `_pdbx_struct_assembly` 还原。
  - **配体**：HETATM 包括水、离子、抑制剂、辅因子，要按 chemical component 字典区分。
  - 单位：**Å (Ångström)**，与医学/CAD 不同。

#### 2.1.2 SDF / MOL2 / MOL（小分子）

- **入口**：
  - **PubChem** <https://pubchem.ncbi.nlm.nih.gov/>（>1 亿化合物）— REST/PUG-REST、批量 FTP
  - **ChEMBL** <https://www.ebi.ac.uk/chembl/>（药物筛选数据，~250 万）
  - **DrugBank** <https://go.drugbank.com/>（学术免费 + 商业付费）🔒
  - **ZINC** <https://zinc.docking.org/>（虚拟筛选库，购买物可标）
- **解析工具**：**RDKit**（黄金标准）、**Open Babel**、**OEChem**🔒
- **注意点**：
  - **3D vs 2D**：SDF 可仅含 2D 拓扑，需用 RDKit/OB 做 3D embedding。
  - **质子化态/互变异构**：pH 7 下的标准形式与文件中可能不同。
  - **立体化学**：手性中心标注是否完整影响对接。

#### 2.1.3 AlphaFoldDB

- **入口**：<https://alphafold.ebi.ac.uk/>、bulk on Google Cloud Storage / S3
- **覆盖**：UniProt 下游，~2 亿预测结构
- **核心字段**：每个残基的 **pLDDT**（confidence 0–100）、**PAE matrix**（残基对距离不确定性）—— **非常重要**，用于过滤可信区域。
- **对接平台的意义**：作为实验结构稀缺时的兜底；但需向 Agent 暴露置信度，避免基于低置信结构做物理推理。

### 2.2 医学影像

#### 2.2.1 DICOM 🩺

- **入口**：
  - **TCIA**（The Cancer Imaging Archive）<https://www.cancerimagingarchive.net/> — 200+ collections
  - **IDC**（NCI Imaging Data Commons）<https://imaging.datacommons.cancer.gov/> — TCIA 数据云化版本，AWS/GCP 直读
  - **OHIF Demo data**、Radiopaedia（教学，非批量）
- **抓取**：
  - **NBIA Data Retriever**（TCIA 官方客户端，支持 manifest）
  - **idc-index** Python 包（IDC 官方）
  - 直接 `aws s3 cp s3://idc-open-data/...`
- **内容**：
  - 像素数据（uncompressed / JPEG-LS / JPEG2000 / RLE）
  - **元数据三层**：Patient → Study → Series → Instance
  - 关键 tag：`Modality (CT/MR/PT/...)`、`PixelSpacing`、`SliceThickness`、`ImagePositionPatient` (3 元组 mm)、`ImageOrientationPatient` (6 元组方向余弦)、`RescaleSlope/Intercept` (HU 变换)、`WindowCenter/Width`
- **解析工具**：
  - **pydicom** — 元数据/像素读写
  - **dcm2niix** — DICOM → NIfTI 转换神器（推荐用于科研）
  - **GDCM / dcmtk** — C++ 后端
  - **SimpleITK** — 高级体数据操作
  - **MONAI** — DL 管线集成
- **核心注意点**：
  - **PHI（受保护健康信息）**：原始 DICOM 含姓名/生日/医院；TCIA/IDC 已脱敏，但**自抓数据必须运行 de-identification**（推荐 `pydicom-anon` / DICOM PS3.15 Profile）。
  - **坐标系**：**Patient Coordinate System (LPS / RAS)** 与 NIfTI 的 RAS 不同，转换出错是巨坑。
  - **切片排序**：按 `ImagePositionPatient` 投影到 `ImageOrientationPatient` 法向量后排序，**别按文件名**。
  - **Multi-frame DICOM**：单文件含多帧（增强 MR/CT），与传统单帧 series 解析路径不同。
  - **HU 重建**：CT 必做 `pixel * RescaleSlope + RescaleIntercept`。
  - **Window/Level**：显示参数，不影响原始数据，但影响 Agent 截图证据的"看见性"。

#### 2.2.2 NIfTI

- **入口**：
  - **OpenNeuro** <https://openneuro.org/> — BIDS 标准 MRI/fMRI/EEG，~1000 数据集
  - **Medical Segmentation Decathlon** <http://medicaldecathlon.com/> — 10 任务标准基准
  - **BraTS** — 脑肿瘤分割（MICCAI 系列）
  - **KiTS / LiTS / LIDC-IDRI / NLST / AMOS / TotalSegmentator dataset**
- **解析工具**：**nibabel**（Pythonic 黄金标准）、**SimpleITK**、**MONAI**
- **核心注意点**：
  - **Affine matrix**（4×4 仿射）决定体素索引到世界坐标的映射，**比所有手动调向都重要**。
  - **方向（qform/sform）**：RAS+ 是约定；不同来源可能 LAS/LPS，必查。
  - **数据类型**：int16 / float32 / 复数（fMRI），存储与计算精度需匹配。
  - **`.nii.gz`** 默认 gzip，nibabel 透明处理；某些工具不支持需先解压。

#### 2.2.3 DICOM-SEG / RTSTRUCT

- **DICOM-SEG**：分割存为单独 DICOM 文件，引用原 series UID。工具：`pydicom-seg`、`highdicom`。
- **RTSTRUCT**：放疗轮廓（多边形序列），不是体素。转 mask 用 `dcmrtstruct2nii`、`rt-utils`。
- **注意**：分割与原始 series 必须**通过 SeriesInstanceUID + ReferencedSOPInstanceUID 关联**，乱序会错位。

### 2.3 工业 CAD / CAE

#### 2.3.1 STEP（ISO 10303）

- **入口数据集**：
  - **ABC Dataset** <https://deep-geometry.github.io/abc-dataset/> — 100 万 CAD 模型，from Onshape，含 STEP/OBJ/特征注解 ⭐
  - **Fusion 360 Gallery** <https://github.com/AutodeskAILab/Fusion360GalleryDataset> — Reconstruction / Segmentation 子集，含建模序列
  - **DeepCAD** <https://github.com/ChrisWu1997/DeepCAD> — 17.8 万参数化 CAD 序列
  - **CC3D** — 5 万 STEP，含多视角扫描配对
  - **CAD-Net 系列**（多个论文释放）
- **AP 子标准**：
  - **AP203** — 配置控制设计（基础 B-rep + 装配）
  - **AP214** — 汽车机械
  - **AP242** — 当代 model-based 工程，含 PMI/公差/材料/语义（**首选**）
- **解析工具**：
  - **OpenCASCADE / pythonocc-core** — 黄金标准 B-rep 内核（Python 绑定，开源）
  - **FreeCAD** — GUI + Python API，可作 MCP server 后端
  - **STEPcode** — 纯 STEP 解析器（C++）
  - **CadQuery / Build123d** — 参数化建模 DSL
  - **Open Cascade Cloud** / **OnShape API**
- **核心注意点**：
  - **B-rep ≠ mesh**：STEP 存的是参数曲面（NURBS）+ 拓扑，不是三角片。**渲染前需要 tessellation**（OCCT `BRepMesh`）。
  - **单位**：mm 是默认，但 STEP 头里有 `LENGTH_UNIT`，**自动检测后归一化**。
  - **装配层级**：`PRODUCT_DEFINITION_FORMATION` → `NEXT_ASSEMBLY_USAGE_OCCURRENCE`，需还原零件实例化矩阵。
  - **PMI（Product Manufacturing Information）**：AP242 才完整支持公差/标注；AP203/214 通常无。
  - **内核差异**：不同 CAD 软件导出的 STEP，OCCT 解析时容差/拓扑修复策略可能不同；**先做 `ShapeFix` 再用**。
  - **隐式特征**：参数特征（孔/倒角/圆角）在 STEP 中通常已"烤"成几何，恢复特征语义是开放研究问题（参考 DeepCAD / SolidGen）。

#### 2.3.2 STL / OBJ / glTF

- **STL**：仅三角面片 + 法向量，**无单位、无层级、无颜色、无材质**。Thingi10K（10K 模型）质量参差。工具：trimesh、open3d、numpy-stl。
- **OBJ**：mesh + 可选材质 (.mtl) + 纹理。ShapeNet / ModelNet / Objaverse 主格式。工具：trimesh、Open3D、PyTorch3D。
- **glTF/GLB**：Web 友好 mesh + 材质 + 动画 + skins。Khronos 样例 <https://github.com/KhronosGroup/glTF-Sample-Models>。工具：pygltflib、trimesh、Three.js（前端）。
- **OBJ → STL → STEP 不可逆方向**：mesh 信息少，无法恢复参数曲面，这是数据采集阶段必须留住高级格式的原因。

#### 2.3.3 USD/OpenUSD

- **入口**：
  - **Pixar OpenUSD samples** <https://openusd.org/release/dl_downloads.html>
  - **NVIDIA SimReady Assets** <https://docs.omniverse.nvidia.com/simready/>
  - **Sketchfab USDZ 导出**
- **变体**：
  - `.usda` 文本（人读）
  - `.usdc` 二进制（生产）
  - `.usdz` 压缩包（AR 分发）
- **解析**：`usd-core` (PyPI) — Pixar 官方 Python；NVIDIA Omniverse Kit (闭源 SDK)。
- **注意**：USD schema 强大但医学/分子/CAD 领域 schema 还需扩展（呼应 ProjectPlan.md §4.1 方案 C）。

### 2.4 光学仿真（Ansys Optics / Lumerical 等）

> 对应 ProjectPlan.md §11.2 T14（owner：涂增基）。本节不仅列"去哪拿、怎么解析"，还显式给出"基于此构造缺陷样本给 Agent 评估"的样本设计模式。

#### 2.4.1 Ansys Optics 案例库（前 Lumerical Application Gallery）⭐

- **入口**：<https://optics.ansys.com/hc/en-us> — Ansys Optics 官方知识库 + Application Gallery，覆盖 FDTD、MODE/EME、CHARGE/HEAT、INTERCONNECT、RCWA、FDE 等求解器的端到端示例。
- **代表案例类别**：
  - 光子晶体 / 周期结构 / 带隙（FDTD）
  - 波导 / 光纤 / EME 模式（MODE）
  - 超表面 / Metalens / 超透镜（RCWA + FDTD）
  - 表面等离激元 SPP、纳米天线（FDTD）
  - 光子集成电路 PIC（INTERCONNECT + FDTD）
  - 太阳能电池 / LED / OLED 微腔（CHARGE + FDTD）
  - 全息 / 衍射光学元件 DOE
- **覆盖文件类型**：

  | 后缀 | 来源工具 | 性质 | 解析方式 |
  | --- | --- | --- | --- |
  | `.fsp` | FDTD Solutions | 二进制工程文件（私有），含几何/材料/网格/光源/监视器 | 完整：`lumapi` 🔒；离线：版本相关，部分内嵌 HDF5 子块可探 |
  | `.lms` | MODE Solutions | 同上（波导本征模 / EME） | 同上 |
  | `.ldev` / `.icp` | DEVICE / INTERCONNECT | 多物理 / 光路网表工程 | `lumapi` 🔒 |
  | `.lsf` | 全套 | 文本脚本（类 MATLAB），参数化建模 + 批扫 + 后处理 | 任意文本工具，**最有价值的"即代码即 GT"载体** |
  | `.ldf` | 全套（结果导出） | HDF5 容器 | `h5py` 直读 |
  | `.mat` / `.h5` | 后处理导出 | 数值 | `scipy.io` / `h5py` |
  | HTML + PDF + 截图 | 教程页面 | 文档资料 | 抓取作 RAG 语料 |

- **抓取方式（按 license 友好度排序）**：
  1. **HTML 教程 / Knowledge Base 页面**：robots.txt 允许公开抓，作 RAG 语料 + 几何描述源。
  2. **`.lsf` 脚本**：Application Gallery 多数案例附带 `.lsf`，纯文本，**没有 license 也能完整阅读几何/物理设定**。优先抓这一类。
  3. **`.fsp` / `.lms` 工程文件**：需 Ansys 账号下载（免费试用账号可注册）；下载后离线**仅用于内部评测留底**，不二次分发。
  4. **几何重建（plan B，免许可证路线）**：把案例网页的 schematic、参数表、材料色散曲线、监视器位置/结果图截下，用 USD/glTF + JSON 注释**自家重建几何**；官方仿真曲线作为标答挂在样本侧。这条路对工程合规最稳。
- **法律 / License** 🔒：
  - 网页内容 © Ansys Inc.，**非 CC**，仅可"研究/评测内部参考"，不得整段二次发布。
  - `.fsp` / `.lms` 工程文件**在 Lumerical 软件中重跑需 license**；离线打开做静态读取属灰区，**v1 阶段不分发原厂工程文件**。
  - `.lsf` 脚本通常被视为"用户内容"性质相对宽松，但仍以"截屏 + 重建 + 注明出处"为安全默认。
  - **结论**：v1 主路径 = 几何重建 + 官方曲线作 GT；少量原文件仅供项目内部对照。
- **解析工具**：
  - `h5py` — 读 `.ldf`（HDF5 导出）的 raw group/dataset
  - `lumapi`（Ansys 官方 Python API）🔒 — 唯一能完整跑 `.fsp` 的官方路径
  - `meep`（开源 FDTD）/ `tidy3d`（Flexcompute SaaS） — 几何复刻 + 跨求解器交叉验证
  - `gdsfactory` / `SiEPIC-Tools` — 光子集成电路几何 / 版图 / PDK
- **核心注意点**：
  - **单位**：FDTD/MODE 默认 SI（长度 μm 或 m，频率 Hz/THz，波长 nm），但工程文件内部统一 SI；脚本里常用 `c=2.99792458e8`、`um=1e-6`，**导入到 S3D-SG 必须显式带单位**。
  - **坐标系**：Lumerical 用右手系，z 通常沿光轴或层栈方向；PML/对称面位置写入坐标，重建时**不能漏 PML 厚度**。
  - **材料色散**：内置库（Palik/Johnson-Christy/Lumerical fits）在不同波段的拟合误差差异显著，作 GT 时要锁定数据源。
  - **网格**：FDTD 结果对网格步长极其敏感（通常 < λ/20），重建评估时必须把网格设定也纳入"答案"维度。

#### 2.4.2 作"3D 视觉评估题"的样本设计模式（核心创新点）

把 Application Gallery 的"参数 → 仿真曲线/场分布"翻成可机评的样本，三种模式：

1. **Ground-truth 问答**
   - 输入：重建几何（USD/glTF + 参数 JSON） + 案例描述
   - 题目示例："该波导基模有效折射率？""该超表面 0 阶透过率峰在多少 nm？""该光子晶体带隙范围？"
   - 标答：官方仿真曲线 / 表格
   - 评分：数值容差（如相对误差 < 5%）

2. **缺陷注入诊断（核心）** —— 在重建几何上**人工引入失配**，让 Agent 通过 viewer 观察并定位：

   | 缺陷类别 | 注入示例 | Agent 期望输出 |
   | --- | --- | --- |
   | 几何错位 | 关键层错位 50 nm / 周期常数偏 1% | 指出错位位置 + 量级 |
   | 网格不足 | 关键区域网格 > λ/10 | 指出"网格不足将导致结果不收敛" |
   | PML / 对称面 | PML 厚度过薄 / 对称面用错偏振 | 指出物理不一致性 |
   | 材料 | 替换为错色散 / 漏掉吸收虚部 | 比对官方曲线找异常 + 反推材料错误 |
   | 监视器 | 位置/方向/频段错放 | 提出"应放在何处、为何" |

3. **语义缺陷模式**
   - 几何正确，但仿真设定（光源偏振、频段、对称利用）改坏
   - 让 Agent **解释为何结果不收敛或不物理**，考查物理 + 工具链联合推理

#### 2.4.3 其他光学仿真生态（旁路参考）

| 平台 | 主要格式 | 来源 | 备注 |
| --- | --- | --- | --- |
| Zemax OpticStudio 🔒 | `.zmx` / `.zos` / `.zar` | Zemax Knowledgebase <https://support.zemax.com/> | 几何光学 / 序列光线追迹；案例库受 license 限制 |
| Ansys Speos 🔒 | `.scdocx` + Speos sidecar | Ansys 同源 | 视觉 / 照明 / HUD，与 Optics 库交叉 |
| **MEEP**（开源 FDTD） | Python script + `.h5` | <https://meep.readthedocs.io/> | MIT，作为 Lumerical 案例的开源对照组 ⭐ |
| **Tidy3D**（云 FDTD） | Python notebook + JSON | <https://www.flexcompute.com/tidy3d/> | SaaS 免费额度，notebook gallery 丰富 |
| **Lumos**（Lumerical 风格的开源前端） | Python | GitHub | 用户脚本可作开源样本 |
| 光子 PIC | GDSII `.gds` | SiEPIC-Tools / IMEC SiPh demo | layout 几何标准；与 PDK 配合方能反演物理 |

---

## 3. 跨格式通用注意点

### 3.1 单位与坐标系（最高频踩坑）

| 领域 | 默认单位 | 默认坐标 | 备注 |
| --- | --- | --- | --- |
| 蛋白质/分子 | Å | 笛卡尔，无统一原点 | 晶体学结构有晶胞参数 |
| 医学 NIfTI | mm | RAS+ | affine 决定一切 |
| 医学 DICOM | mm | LPS（Patient） | 与 NIfTI 转换易出错 |
| CAD STEP | 多为 mm（看 header） | 装配根坐标 | 单位检查必做 |
| Web glTF | m | Y-up（Khronos 约定）| 与 USD（Y-up 默认，可改）/ 工程（Z-up）冲突 |
| 光学仿真（Lumerical/MEEP） | μm 或 m（频率 Hz/THz、波长 nm） | 自定义局部坐标，常 Z 沿光轴/层栈 | PML/对称面位置必须随坐标记录；网格步长是结果有效性的隐式坐标 |

> **建议在 S3D-SG 中间层强制记录** `unit / origin / axes / local-to-world transform / source-format`，所有适配器输出前必须填全。

### 3.2 法律与许可分级

- **CC0 / Public Domain** — RCSB PDB、PubChem、大部分 NIH 公开数据集 → 自由用
- **CC-BY / CC-BY-SA** — AlphaFoldDB、ChEMBL、OpenNeuro → 需署名/同条款分享
- **学术非商用** — PDBbind、部分 TCIA 子集、ABC Dataset → 论文/研究 OK，产品化要谈授权
- **私有/付费** — DrugBank（商业）、Parasolid/ACIS、CSD → v1 一律不用
- **PHI 数据** 🩺 — 自抓医学数据强制 de-identify；**TCIA/IDC 已脱敏可直接用**

### 3.3 数据规模估算（v1 PoC 建议）

| 维度 | 建议规模 |
| --- | --- |
| 蛋白结构 | 100–500 条 PDB，覆盖 enzyme / GPCR / ribosome 多尺度 |
| 蛋白-配体 | PDBbind core set ~290 条 |
| 医学体数据 | Medical Decathlon 任意 1–2 任务（如 Task01 BrainTumour、Task07 Pancreas）即够 |
| CAD | ABC dataset 子采样 1K，加 Fusion 360 Reconstruction 1K |

总磁盘 < 100 GB，单机可承载。

### 3.4 抓取礼仪与速率

- 公共 FTP / HTTPS：限并发 2–4，加 User-Agent 与 email contact
- TCIA / IDC：用官方客户端避免误用
- Sketchfab / GrabCAD：尊重 robots.txt，**不爬付费内容**
- 凡涉及大规模分布式抓取，先看仓库官方说明是否提供 S3/GCS bucket 直读（PDB / AlphaFold / IDC 都有），比 HTTP 高效一个量级

---

## 4. 与 ProjectPlan.md 的反向映射

| 数据/格式 | 触达的 ProjectPlan.md 章节 | 用途 |
| --- | --- | --- |
| PDB+SDF+PDBbind | §3.1 生化、§4.2 MCP Mark、§6 Benchmark"测量"、§7 空白5 | 训练/评测对接、口袋视角生成 |
| TCIA + Medical Decathlon | §3.1 医学、§6 Benchmark"3D 病灶定位/体积测量" | 跨切面操作、窗宽窗位调整 |
| ABC Dataset + Fusion 360 | §3.1 工业、§5.2 CAD-Assistant/AADvark | 装配干涉、PMI QA |
| OpenUSD samples | §4.1 方案 A/C、§4.3 协议层 | 验证 USD 作为统一容器的可行性 |
| AlphaFoldDB | §3.1 生化（覆盖广度） | 实验结构稀缺时兜底 |
| Ansys Optics Application Gallery（Lumerical）| §3.1 拓扑光学/电磁学、§6 Benchmark"参数→仿真曲线"、§7 空白（缺陷诊断）、§11.2 T14 | 几何重建 + 缺陷注入双轨样本（详见 §2.4） |

---

## 5. 待补 / 开放问题

- [ ] **机器人/具身相邻数据**（URDF/SDF, 相邻参考但暂不入 v1）：Open X-Embodiment、RoboCasa、Behavior-1K，是否纳入比较实验？
- [ ] **CAE/有限元**：ANSYS .rst、Abaqus .odb、OpenFOAM mesh — 物理仿真层是否需要直接接？还是只通过 MCP 工具调用 solver？
- [ ] **病理切片**：WSI（.svs/.tif/.ndpi），属于医学但不是 3D 体数据，是否纳入？
- [ ] **Cryo-EM tomogram**：MRC/CCP4 + STAR，3D 但非典型，纳入与否取决于是否做 cryo-EM 子任务。
- [ ] **抓取脚本仓库**：建议在 `scripts/data/` 下逐格式建独立脚本，单独可重放（v0.2 议题）。
- [ ] **数据卡片（Datasheet）**：每个最终入库子集应配 datasheet（来源/许可/规模/已知偏见）。

---

## 6. 参考

- DICOM 标准：<https://www.dicomstandard.org/>
- NIfTI-1：<https://nifti.nimh.nih.gov/nifti-1>
- ISO 10303 STEP：<https://www.iso.org/standard/63141.html>
- OpenUSD：<https://openusd.org/>
- glTF 2.0：<https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html>
- PDBx/mmCIF Dictionary：<https://mmcif.wwpdb.org/>
- BIDS（医学影像组织规范）：<https://bids.neuroimaging.io/>
- Ansys Optics（Lumerical）官方知识库 / Application Gallery：<https://optics.ansys.com/hc/en-us>
- MEEP（开源 FDTD）：<https://meep.readthedocs.io/>
- Tidy3D（云 FDTD）：<https://www.flexcompute.com/tidy3d/>
