# crawl_data 与 viewer 现状整理

> 生成日期：2026-05-11
> 数据来源：`crawl_data/`（smoke crawl 2026-05-09 17:32 产物）+ `viewer/` 各 HTML 入口 + `tools/build_manifest.py`
> 目的：给后续 benchmark / agent 评测准备一份"目前已有什么数据 + 3D 查询是怎样做出来的"基线快照。

---

## 0. 版本历史

本文档与 `viewer/` 实现同生命周期演进。每次新增 domain / viewer / 修缮已知 gap，都在此追加一行。

| 版本 | 日期 | Git ref | 主要变更 |
|---|---|---|---|
| v0.1 | 2026-05-09 | smoke crawl `_logs/smoke_20260509-173255.log` | 基线：3 domain（biology / medical / industrial）+ 4 viewer（Mol* / Niivue / Three.js / OCCT 占位）。59 MB 资产。 |
| v0.2 | 2026-05-11 | `chore: initial commit` (04e8405) | 文档首版 §1–§3。本文件创建。 |
| v0.3 | 2026-05-11 | 本 commit | 扩展到 6 domain：新增 robotics（URDF/SDF）、pointcloud（LAS）、topological_photonics（band/GDSII/MPB ctl/field 占位）。新增 6 个 viewer HTML（urdf / pointcloud / bandplot / gdsii / mpbctl / field3d，共 1,035 行）。`build_manifest.py` + `index.html` + `style.css` 同步扩展。manifest 92 assets。详见 §3.1。 |
| v0.4 | 2026-05-11 | 本 commit | 收尾兜底：把 v0.3 仍在 `viewer="raw"` 的最后 3 个资产（NRRD / USDA / USDZ）接入专用 viewer。NRRD 直接复用 `niivue.html`（Niivue 0.45 bundle 内置 `readNRRD` + `case "NRRD"`，已验证）；新增 `usda.html` (Prism + 自研 prim 树解析器) 与 `usdz.html` (JSZip 解包 + PBR 贴图预览)。manifest `viewer="raw"` 计数从 3 降到 **0**——92 资产全部路由到交互式 viewer。**USDZ 的几何 3D 渲染未解决**（依赖 USD-WASM 重 dep），现状为"包内容 + 贴图可视化但无 mesh"。详见 §3.3。 |
| v0.5 | 2026-05-11 | 本 commit | 引入 **negative samples 子集**：单独立档 [`NegativeSamples.md`](NegativeSamples.md) v0.1（分类学 13 类 / INDEX.json schema / 空清单骨架）。三层分离原则（目录 / manifest / viewer）。`crawl_data/{biology,medical,industrial}/broken/` 空骨架（INDEX.json + README.md）已搭；不影响 clean manifest（仍 92 assets）。抓取脚本与 viewer 集成留 v0.5.x。详见 §3.5。 |
| v0.6 | 2026-05-11 | 本 commit | **卡片缩略图**：`viewer/index.html` 给每张 card 加 72 px 高 SVG glyph 预览，按 `asset.viewer` 类型分发 14 套独特图形（atoms / MPR 三面 / iso-cube / 关节树 / 点云散点 / band 曲线 / 版图 rect / Scheme 代码 / USD 树 / zip 包等），颜色按 domain 编码。Tier-2 lazy fetch：text 格式（urdf/sdf/ctl/usda/mol/mol2/xyz）滚到视口后异步取首 7 行替换为真实内容预览，IntersectionObserver + 3 并发上限。`style.css` 加 `.card .thumb` 样式槽。`test_pages.sh` 增 thumbGlyph/DOMAIN_COLOR/enhanceThumbLazy/negative_assets.json 断言。**所有 92 clean assets 现在都有视觉差异化预览**；零新外部依赖。 |
| v0.7 | 2026-05-12 | 本 commit | **新增 simulation domain（COMSOL Multiphysics）**：第 7 个 domain 上线，`crawl_data/simulation/comsol/` 入库 7 records / 24 文件 / 80 MB（GitHub MIT × 4 + Zenodo CC-BY × 2 + figshare CC-BY × 1）。物理域覆盖 NEMS 继电器 / 固体力学 4 子案例 / 相场断裂 / CFD `.vtu` / 心脏 PINN HDF5 / 磁子晶体 STL / 8 对耳道 STL。每记录附 `SOURCE.json`（origin URL + license + retrieved_at + 每文件 SHA-1）；顶层 `INDEX.json` 汇总；`application_gallery_links.json` 为 COMSOL 官方 Application Gallery 的 5 个 URL-only 引用（KB1223 禁止镜像 .mph/图像/文档）。`build_manifest.py` 新增 `scan_simulation()`：`.mph` + `.pdf` → raw、`.stl` → gltf、`.vtu` + `.h5` → field3d。**field3d viewer 重写**：从静态 MPB-only info-pane 升级为多 schema 自动检测（MPB 均匀网格 / COMSOL 非结构化 / VTK XML），同目录 `SOURCE.json` 按 license/physics/SHA-1 显示元数据，VTU 文件做浏览器端 XML header 解析（points/cells/DataArray 名）。manifest 92 → 114 assets。详见 §3.6 + 新增的 §1.x simulation 子节。 |
| v0.8 | 2026-05-12 | 本 commit | **Three.js viewer 横向能力 — Cutaway 面板**：新增 `viewer/shared/cutaway.js`（ES module，~180 行，零外部依赖，运行期把 `THREE` / `renderer` / `scene` 由调用方注入避免重复 import）。三个 Three.js viewer（`gltf.html` / `urdf.html` / `pointcloud.html`）右上角加可折叠面板：(1) **Visibility 树**——遍历根 Object3D 的具名子节点（深度 ≤ 2，过滤 `AxesHelper / GridHelper / Points`）逐项给 checkbox + opacity slider，opacity 改动走 clone-on-write material 不污染 cache；(2) **3 轴 clip plane**——X/Y/Z 三个 `-100..100%` slider，按 root bbox 实时换算 `THREE.Plane` constant，`renderer.localClippingEnabled` 仅在任一 slider ≠ 0 时启用避免性能税；(3) **Reset** 一键恢复。CSS 新加 `.cutaway-panel*` 与 `body.has-neg-banner` 下 top 偏移规则（与现有 `#view-toggle` / `#controls` 同等待遇）。`pointcloud.html` 因为只渲染一个 `THREE.Points`，visibility 树退化成单条但 clip plane 仍生效（点着色器吃 `clippingPlanes`）。`tools/test_pages.sh` 加 5 条 assert。**未支持**：molstar (有内置 selection UI) / niivue (待 v0.9 体积 clip) / cornerstone (2D 不适用)。详见 §3.7。 |
| v0.9 | 2026-05-13 | 本 commit | **5 处用户反馈修复 + STEP 真渲染**：(1) 文档列出"哪些格式可看内部"全表，详见 §3.8.1；(2) 从 manifest 删除 `pydicom_samples/*.dcm` 4 条 — 它们都是 single-slice 教学切片（`NumberOfFrames=1` 或 RT 结构集），属于 2D 数据不是 3D 模型，留着会让用户在 Medical section 误点开看到一张静态图；文件保留磁盘备查，只是不再进 `viewer/assets.json`。`build_manifest.py` 删 12 行；medical 资产数 11→7。(3) `rtstruct.dcm` 同时是无 PixelData 的结构集（DICOM RTSTRUCT，向量 contour 而非像素），cornerstone 本质上无法 decode — 顺带 #2 一起清。(4) **`occt.html` 重写**：从静态占位页升级为 `occt-import-js@0.0.23`（npm，LGPL-2.1，95 KB JS + 7.3 MB WASM via jsDelivr CDN）的 STEP/IGES 浏览器内 tessellation；解析后的多个 face mesh 各成 `THREE.BufferGeometry`，挂到 `THREE.Group` 后复用 `gltf.html` 的 fit + OrbitControls + cutaway 模式；冷启动期间显示进度条（fetch → init runtime → parse → mesh build 四阶段），失败回退到原下载链接占位。`occt_linkrods.step`（1.7 MB B-rep 复合零件）实测可解析渲染。(5) **URDF 卡片缩略图修复**：tier-2 lazy 之前把"关节树 SVG"覆盖成 XML 头几行（`<robot xmlns:xacro=...` 全是噪声、3 个 URDF 卡片视觉一致），从 `TEXT_VIEWERS` 移除 `urdf` 改回纯 tier-1 glyph，`mpbctl/usda` 仍走文本预览。详见 §3.8。 |
| v1.0 | 2026-05-13 | 本 commit | **3 处用户反馈修复（astronomy + occt + cornerstone slice nav）**：(1) **Astronomy 大改造**：v0.9 之前 manifest 把 14 个底层 raw 文件（textures/elements_*/exoplanets）+ 1 个 questions.json + 2 个 saturn ephemeris = 17 张卡片摊给用户，点击 14 张只是下载 JSON/JPG 噪声；现在改成只索引 3 张 question 卡（V2/V4 → `saturn.html`，V6 → 新增 `solar_system.html`）+ V2/V4 的副 ephemeris 和所有 textures 由 viewer 内部 fetch，**底层文件不再单独成卡**（用户进一步反馈："不要显示 questions.json"，已一并清掉）。新增 `crawl_data/astronomy/ephemeris/solar_system_elements_j2000.json`（聚合 8 颗行星 J2000 osculating elements：a/e/i/Ω/ω/AD/QR）；新增 `viewer/solar_system.html` (~150 行 ES module，Three.js + OrbitControls，Curtis 4.41 perifocal→inertial 旋转矩阵把 8 个 Kepler 轨道画在 ecliptic 帧，配 top/side/iso 三个相机预设，让 V6 共面性问题可视检验)。`scan_astronomy()` 重写。`tools/test_assets.py` 加 `astronomy/simulation` 到 DOMAINS、`saturn/solar_system` 到 VALID_VIEWERS、`json/jpg/png/pdf/vtu` 到 VALID_FORMATS。astronomy 资产数 17 → 3。(2) **occt.html 加可见错误诊断**：v0.9 集成的 occt-import-js 在用户实际打开 `?file=occt_linkrods.step` 时呈现空白，根因不明（headless 测试无浏览器）；本版加入 `window.error` / `unhandledrejection` 监听 + 5 阶段进度条（含 v1.0 新加的 "Loading occt-import-js" 阶段）+ jsdelivr/unpkg 双 CDN fallback + 模块导入分阶段 try/catch；任何阶段失败现在都把 stack trace 渲染到右上角 progress 面板而不是空白。Node 端真实文件 parse 已验证（770ms / 8772 tris），下次浏览器打开如仍失败会自报错误位置。(3) **Cornerstone slice 导航**：用户明确不需要透明化但需要切片选择。重写 `cornerstone.html` 加 slice 控件：左上角 slider + position 显示（`3 / 70`），wheel 滚动 / ←→/Home/End 键 / slider 拖动三套交互；多帧 DICOM 通过 `wadouri:URL?frame=N` 切帧（cornerstone-wado-image-loader 原生支持），同目录序列通过 `?series=manifest.json` 参数（JSON 数组列出兄弟文件）；W/L 在切片间保留。单帧文件自动隐藏 slice 面板。manifest 总资产 127 → 113（astronomy 17 → 3）。详见 §3.9。 |
| v1.1 | 2026-05-27 | 本 commit | **新增 physics domain（atmospheric / climate · netCDF first-class）**：第 8 个 domain 上线，`crawl_data/physics/gcm_atmos_mean_daily/` 入库 1 个 file（GFDL FMS Cubed-Sphere C48 idealized GCM 日均切片，289 MB `.nc`，私有产物，license=null）。新增 `tools/extract_nc_meta.py`（用 `xarray` + `decode_times=False` 抽 dimensions / variables / global_attrs 到同目录 `<file>.nc.meta.json` sidecar，~10 KB，跟仓走；TDD 20 unit tests 覆盖 NETCDF3_CLASSIC、attr edge cases、SHA-1 fingerprint 失效重算、CLI exit codes）；`build_manifest.py::scan_physics()`（5 unit tests）自动调用 `ensure_nc_meta` 并按 `viewer="field3d", format="nc"` 登记，对单文件失败做 warning-not-block。`viewer/field3d.html` 加 `netcdf` schema 分支：绿色 `NetCDF (.nc)` badge、从 sidecar fetch dims / variables / global_attrs（折叠 `<details>` 区段）、xarray recipe（强调 `decode_times=False` 规避 `days since 0000-00-00` 解码失败）；`.nc` 二进制本身永不被 viewer fetch。`.gitignore` 沿用 `broken/` 的 re-include 模式（先 `!crawl_data/physics/*/`，再排除子项，最后白名单 `SOURCE.json` / `README.md` / `*.nc.meta.json`）。`viewer/index.html` 加 `#physics` section + `--domain-atmos: #3fb950`。manifest 113 → 114 assets。**不**做浏览器内真 3D 渲染（niivue 不适配气象数据；info-pane + xarray recipe 已满足 LLM agent 推理需求）。详见新增的 §1.5 physics 子节 + §3.10。 |
| v1.4 | 2026-06-03 | 本 commit | **viewer/index.html 转成 SPA：4 个 hash-routed tab + Home 介绍页**。原来一屏到底的长滚页拆成 `#assets` / `#vqa` / `#matched` / `#qagen` 四个 tab pane；没有 hash 时落在 Home view（hero + 4 模块概览卡 + 19-tool 速查表 + 6 个 featured case 卡）。状态机走 `viewer/shared/tabs.js`（公开 `activate(tabId|null)` + `setBadge(tabId, value)`，`replaceState` 永不 `pushState`，未知 hash 静默回退到 Home）。matched / qagen 两个新 pane 由 `viewer/shared/sections/matched.js` 渲染 `datasets/_matched/index.json` 卡片网格，`viewer/shared/sections/qagen.js` 渲染由新工具 `tools/build_qagen_index.py` 产出的 `viewer/qagen_index.json` (schema v1.0)；任一数据源失败时 pane 单点降级（红色 error pill + 重建命令）不影响其他 tab。Home 上 featured case 卡走 soft-fail：`assets.json` lookup 命中则用 `viewerUrl()` 包成可点链接，缺失则灰显 tooltip 不抛错。所有 4 个 tab 的 badge 与 Home 的 4 个数字都直接由 manifest 数据驱动（assets.index.json 求和 / datasets.index.json 计数 / matched.total / qagen sets.length），manifest 失败时显示 `!`。`viewer/index.html` 1274 → ~1620 行（CSS +180、HTML +150、wiring script +60）；既有的 inline asset render 与 VQA render 脚本 (L177-731 / L973-1272) 一字未动。新增 `tests/viewer/test_spa_tabs.py`（5 个 plumbing test：结构 marker + 3 ES module 可达 + qagen_index.json 解析）；`tests/viewer/test_index_fetch.py` 两个 lazy-shard test 改成访问 `#assets`，因为 IntersectionObserver 不会对 `display:none` 的子树触发。Spec / plan 落地：`docs/superpowers/specs/2026-06-03-viewer-spa-tabs-design.md` + `docs/superpowers/plans/2026-06-03-viewer-spa-tabs.md`。 |

> 维护约定：本表只记录**架构级**变更（新增/删除 domain、新增 viewer、修缮 §3 列出的 gap）。单文件 bugfix 走 git log，不进表。

---

## 1. crawl_data — 领域 / 数据类型 / 文件格式

### 1.1 顶层结构

```
crawl_data/
├── VIEW_ANGLES.md              # 视角敏感度速查（设计 benchmark 用）
├── _logs/
│   └── smoke_20260509-173255.log
├── biology/                    # ≈ 9.5 MB
│   ├── alphafolddb/            # ⚠ 抓取全部失败（HTTP 404），目录为空
│   ├── pubchem_sdf_3d/         # 5 个 .sdf
│   └── rcsb_pdb_mmcif/         # 20 个 .cif
├── industrial/                 # ≈ 22 MB
│   ├── khronos_gltf/           # 5 个 .glb
│   └── step_samples/           # 1 个 .step（另有 2 个抓取失败）
└── medical/                    # ≈ 27 MB
    └── decathlon/
        ├── Task04_Hippocampus.tar
        └── extracted/Task04_Hippocampus/
```

三个 domain：**biology · industrial · medical**。磁盘总占用约 59 MB。

### 1.2 三个 domain 的资产明细

#### Biology（蛋白与小分子，共 25 个资产）

| 子集 | 数量 | 格式 | 来源 | 用途 |
|---|---|---|---|---|
| `rcsb_pdb_mmcif/` | 20 | `.cif`（mmCIF） | RCSB PDB | 真实结构生物学，含蛋白 / DNA / 复合物 |
| `pubchem_sdf_3d/` | 5 | `.sdf`（SD file，含 3D 坐标） | PubChem | 经典小分子药物 |
| `alphafolddb/` | 0 | （`.cif` 期望） | AlphaFold DB | **目前为空**：URL 全部 404，需在 v0.2 修订 |

PDB 资产列表（按主题）：
- 球蛋白 / 血红素：`4HHB`（四聚体血红蛋白）、`1A3N`（脱氧 hemoglobin）、`1MBN`（肌红蛋白）
- 酶 / 药物靶点：`6LU7`（SARS-CoV-2 Mpro）、`1HVR`（HIV-1 protease）、`4EY7`（acetylcholinesterase）、`1AKI/1LYZ`（lysozyme）、`4AKE`（adenylate kinase 开放态）
- 膜蛋白 / GPCR / 离子通道：`1F88`（rhodopsin）、`2RH1`（β2-AR）、`3PQR`、`1BL8`（KcsA 钾通道）
- 抗体 / 配体载体 / 病毒：`1IGT`（IgG 抗体）、`1RBP`（视黄醇结合蛋白）、`5IRE`（Zika E 蛋白）、`1A8O`（HIV capsid）
- 核酸 / 微蛋白：`1BNA`（B-DNA 12-mer）、`1EHZ`（tRNA-Phe）、`1L2Y`（Trp-cage）

小分子：`aspirin` · `caffeine` · `ibuprofen` · `acetaminophen` · `penicillin_g`（文件名形如 `2244_aspirin.sdf`，前缀为 PubChem CID）。

#### Medical（医学影像，共 8 个 NIfTI 卷）

来源：**Medical Segmentation Decathlon — Task04 Hippocampus**。

| 子集 | 数量 | 格式 | 备注 |
|---|---|---|---|
| `imagesTr/` | 5 已被 manifest 暴露（原始包内 260 个） | `.nii.gz`（NIfTI-1 压缩） | 训练 MRI 卷 + 配对的 label overlay |
| `labelsTr/` | 与上配对（原始 260 个） | `.nii.gz` | 海马分割掩膜（红色叠加） |
| `imagesTs/` | 3 已被 manifest 暴露（原始 130 个） | `.nii.gz` | 测试集，无 label |
| `dataset.json` | 1 | JSON | Decathlon 元数据 |

注意：实际解压目录包含 **260 train + 260 label + 130 test = 650 个 NIfTI 文件**，但 `build_manifest.py` 出于性能只挑选前 5 train + 前 3 test 暴露给前端。

#### Industrial（CAD / 通用 3D，共 6 个资产）

| 子集 | 数量 | 格式 | 来源 | 备注 |
|---|---|---|---|---|
| `khronos_gltf/` | 5 | `.glb`（glTF 2.0 二进制） | Khronos glTF-Sample-Models | `Box`/`Duck`/`Avocado`/`DamagedHelmet`/`BoomBox` 经典测试件 |
| `step_samples/` | 1 | `.step`（STEP AP203/AP214 B-rep） | OCCT 仓库 | `occt_screw.step`；另有 2 个 FreeCAD/CadQuery 样本 404 失败 |

#### Simulation — COMSOL Multiphysics（v0.7 起，共 22 个资产 / 80 MB）

来源限定**许可证可二次分发**的三类公开渠道：GitHub MIT、Zenodo / figshare CC-BY-4.0、COMSOL 官方 Application Gallery（**仅外链，KB1223 禁镜像**）。每记录在 `crawl_data/simulation/comsol/{record}/SOURCE.json` 内带 origin URL、license、retrieved_at、每文件 SHA-1。顶层 `INDEX.json` 汇总；`application_gallery_links.json` 为 URL-only Tier-C 引用。

| record | 来源 | License | 文件 | 物理域 |
|---|---|---|---|---|
| `github__akashlevy__NEM-Relay-CAD/` | MIT | 1×.mph + 1×.stl | NEMS 继电器（电-机耦合 pull-in，Stanford） |
| `github__sergiolucarini__UMAT4COMSOL/` | MIT | 4×.mph + 1×.pdf | 固体力学：neo-Hookean / 晶体塑性 / 弹塑性 / 耦合 UMAT |
| `github__sana-shv__PFM-COMSOL/` | MIT | 2×.mph | 相场断裂：KGD 水力裂缝 + 混凝土重力坝 |
| `github__sersolmaz__CFD_AR_VR/` | MIT | 1×.vtu + 1×.pdf | CFD 速度场（非结构化）+ 论文补充材料 |
| `zenodo_16812462__cardiac_scar_pinn/` | CC-BY-4.0 | 2×.h5 + README | 心脏电生理 PINN-增强 FEM scar mesh（非结构化 HDF5） |
| `zenodo_18552513__magnonic_woodpile/` | CC-BY-4.0 | 1×.stl | 3D-打印磁子晶体 woodpile 几何 |
| `figshare_28545548__ear_canal_stl/` | CC-BY-4.0 | 8×.stl + Parameters.txt | HRTF/FEM 耳道 4 对（L+R）STL（取自 75 对原集的子集） |

**重要边界条件**：
- `.mph` 是 COMSOL 闭源二进制，**没有任何开源 viewer**——manifest 路由到 `viewer="raw"`，仅作为可下载语料保留。`docs/DataSourcesAndFormats.md` 把 .mph 标为 "archive-only" 类。
- COMSOL 官方 Application Gallery（https://www.comsol.com/models）的 .mph / 图像 / PDF 全部受 [KB1223](https://www.comsol.com/support/knowledgebase/1223) 约束，禁止再分发。本仓库**不下载**任何 comsol.com 二进制，只在 `application_gallery_links.json` 保留 5 条代表性 URL（structural / ray optics / RF / microfluidics / cable）。

抓取脚本：`scripts/data/sim_comsol.py`（stdlib-only，复用 `_common.download()` 的 idempotent 原子写）。重跑全部 skipped 验证通过。

### 1.3 全部出现过的文件格式速查

| 格式 | 扩展名 | 类别 | viewer 渲染管线 |
|---|---|---|---|
| mmCIF | `.cif` | 原子模型（蛋白 / 核酸 / 复合物） | Mol* 4.5 |
| SDF (V2000/V3000) | `.sdf` | 小分子 3D 构象 + 化学键 | Mol* 4.5 |
| NIfTI-1（gzip） | `.nii.gz` | 3D 体素（MRI / CT），含 RAS+ affine | Niivue 0.45 |
| glTF 2.0 binary | `.glb` | mesh + PBR 材质 | Three.js 0.160 + GLTFLoader |
| STEP（ISO 10303 AP203/214） | `.step` | B-rep CAD 实体 | **未渲染**，仅下载入口（待 OCCT.js / pythonocc-core 接入） |
| COMSOL Multiphysics 模型 | `.mph` | 闭源仿真档案 | **未渲染**（archive-only，需 COMSOL License 才能打开） |
| VTK XML UnstructuredGrid | `.vtu` | 非结构化网格 + nodal/cell 标量 | `field3d.html` 浏览器端 XML 头部解析（points/cells/DataArrays），完整 3D 渲染待 vtk.js 集成 |
| HDF5 | `.h5` / `.hdf5` | 多 schema：MPB 均匀场 / COMSOL 非结构化 mesh | `field3d.html` 按路径自动识别 schema 并给出 schema-specific 处理建议；体绘制路径仍为"服务端 → NIfTI → Niivue" |
| NetCDF Classic / 4 | `.nc` | 大气 / 气候 GCM 输出，dims (time × level × lat × lon) | `field3d.html` 从同目录 `<file>.nc.meta.json` sidecar 渲染 dims / variables / global_attrs + xarray recipe；sidecar 由 `tools/extract_nc_meta.py` 在 build 时产出 |
| TAR | `.tar` | 数据集打包 | 不直接渲染（解压后用 NIfTI 管线） |
| JSON | `.json` | 数据集元数据 / viewer manifest | 直接 fetch |
| Markdown | `.md` | 文档 | — |
| Log（纯文本） | `.log` | crawl 流水 | — |

### 1.4 抓取健康度

最近一次 smoke crawl（`_logs/smoke_20260509-173255.log`）汇总：

| 脚本 | ok | failed | 备注 |
|---|---|---|---|
| `bio_pdb_mmcif.py` | 20 | 0 | 全部成功 |
| `bio_pubchem_sdf.py` | 5 | 0 | 全部成功 |
| `bio_alphafold.py` | **0** | **5** | URL 全部 404 → `alphafolddb/` 目录为空 |
| `med_decathlon_hippocampus.py` | 1 (tar) | 0 | 27.1 MB |
| `cad_khronos_gltf.py` | 5 | 0 | 全部成功 |
| `cad_step_samples.py` | 1 | 2 | 仅 OCCT screw 拿到 |

**待修缮项**：(a) AlphaFold URL 规则（v4 后缀已变？需用 `latest`）；(b) FreeCAD/CadQuery STEP 样本换镜像。

---

## 2. viewer — 3D 查询的实现方式

### 2.1 总体架构

`viewer/` 采用**统一前端 shell + per-domain viewer 插件**模式（无打包工具、无构建链路，纯静态 HTML + CDN ESM）：

```
┌──────────────────────────────────────────────┐
│  index.html  (shell)                         │
│   ├─ fetch /viewer/assets.json               │
│   ├─ 按 domain (biology/medical/industrial)  │
│   │  渲染卡片网格                            │
│   └─ 卡片 href → viewerUrl(asset)            │
│                                              │
│        ↓ 根据 asset.viewer 字段路由          │
│                                              │
│  molstar.html    niivue.html    gltf.html    occt.html   │
│  (Mol* CDN)      (Niivue CDN)   (three CDN)  (info-only) │
└──────────────────────────────────────────────┘
```

**路由约定**（`index.html:38-51`）：

| `asset.viewer` 字段 | 跳转 URL | 查询参数 |
|---|---|---|
| `molstar` | `/viewer/molstar.html` | `file=...&type={mmcif|pdb|sdf|mol2}` |
| `niivue` | `/viewer/niivue.html` | `file=...&overlay=...`（overlay 可选） |
| `gltf` | `/viewer/gltf.html` | `file=...` |
| `occt` | `/viewer/occt.html` | `file=...`（只显示下载链接） |

### 2.2 资产清单生成（manifest pipeline）

`tools/build_manifest.py` 是**唯一的"数据→前端"桥**，离线扫描 `crawl_data/` 并写出 `viewer/assets.json`：

- `scan_biology()`：遍历 `rcsb_pdb_mmcif/*.cif`、`alphafolddb/*.cif`、`pubchem_sdf_3d/*.sdf`，按规则解析出 `name`，统一打上 `viewer="molstar"`。
- `scan_medical()`：扫描 `Task04_Hippocampus/imagesTr` 前 5 个 + `imagesTs` 前 3 个，并尝试找到 `labelsTr/` 中**同名 mask** 作为 `overlay` 字段。
- `scan_industrial()`：glTF → `viewer="gltf"`，STEP → `viewer="occt"`。

输出结构：

```json
{
  "generated_at": "2026-05-09T17:48:46",
  "biology":   [{name, path, size, viewer, format}, ...],
  "medical":   [{name, path, size, viewer, format, overlay?}, ...],
  "industrial":[{name, path, size, viewer, format}, ...]
}
```

`path` 字段是相对于项目根、以 `/` 开头的 URL（如 `/crawl_data/biology/rcsb_pdb_mmcif/1AKI.cif`），意味着**必须从项目根目录起静态托管**（例如 `python3 -m http.server` 在 root 跑），viewer 的 `?file=` 才能正确解析。

### 2.3 四种 3D 查询的具体实现

#### A. Mol* — 蛋白 / 小分子（`molstar.html`）

- **载体**：`molstar@4.5.0/build/viewer/molstar.{js,css}`（CDN）
- **核心调用**：
  ```js
  molstar.Viewer.create('viewer', {
    layoutShowControls: true,
    layoutShowSequence: true,
    pdbProvider: 'rcsb',
  }).then(viewer => viewer.loadStructureFromUrl(file, format));
  ```
- **格式适配**：URL 参数 `type` ∈ `{mmcif, pdb, sdf, mol2}`，Mol* 内部按格式 dispatch parser。
- **默认 pose**：Mol* 自动 centering + auto-zoom，cartoon representation；**没有强制对齐对称轴**——这正是 `VIEW_ANGLES.md` 反复警告的"默认视角不是中立视角"。
- **交互**：原生 Mol* UI（鼠标全自由旋转、序列面板、表示切换）。

#### B. Niivue — NIfTI 体数据（`niivue.html`）

- **载体**：`@niivue/niivue@0.45.0/dist/niivue.umd.js`（CDN）
- **核心调用**：
  ```js
  const nv = new niivue.Niivue({
    sliceType: 4,        // 4 = SLICE_TYPE.MULTIPLANAR
    isColorbar: true,
    isOrientCube: true,
  });
  nv.attachTo('gl');
  const volumes = [{ url: file, opacity: 1.0 }];
  if (overlay) volumes.push({ url: overlay, opacity: 0.5, colormap: 'red' });
  nv.loadVolumes(volumes);
  ```
- **3D 查询的本质**：默认 `sliceType=4` (Multiplanar) → 同时显示 **axial + coronal + sagittal + 一个 3D 体绘制**四视图。坐标约定为 NIfTI 强制的 **RAS+**（+X→Right、+Y→Anterior、+Z→Superior），`isOrientCube` 把 L/R/A/P/S/I 字母贴在角落小立方上。
- **Overlay 叠加**：若 manifest 提供 `overlay`，将 label mask 作为第二 volume 以 `colormap='red'` 半透明叠加 → 直接得到"图像 + 分割" 联合视图。
- **重要限制**：当前 viewer **没有把 `sliceType` 暴露为 URL 参数**，所以单视图 benchmark 必须修改 viewer（或 fork 一份 `niivue-single.html`）才能强制 `sliceType=0/1/2`。

#### C. Three.js — glTF / GLB（`gltf.html`）

- **载体**：`three@0.160.0` + `examples/jsm/{GLTFLoader, OrbitControls, RoomEnvironment}`（CDN，通过 `<script type="importmap">` 解析 bare specifier）。
- **关键步骤**：
  1. WebGLRenderer：`ACESFilmicToneMapping` + `SRGBColorSpace`；
  2. PMREMGenerator 把 `RoomEnvironment` 烘成 IBL → 作为 `scene.environment`（PBR 材质能正确出现高光、反射）；
  3. `GLTFLoader.load(file, gltf => ...)` 加载后：
     - 用 `Box3` 求 bounding box → **居中到原点 + 归一化到单位立方体**（解决不同资产尺度差几个数量级的问题）；
     - 相机锁定 `(0.6, 0.5, 1.2)` 看向原点 → 大约 **前-上-右 ~25° 仰角** 的轴测视图；
  4. `OrbitControls` 提供拖拽旋转 / 滚轮缩放 / 右键平移，`enableDamping=true`。
- **坐标约定**：glTF 规范的 **+Y up、-Z forward**，资产作者负责将"正面"对准 -Z；viewer 不做强制旋转。
- **PBR 一致性**：IBL 环境贴图保证 `DamagedHelmet` 这类资产的金属高光在所有相机角度下都基于物理着色。

#### D. OCCT (STEP) — `occt.html`

- **当前状态**：**未渲染**，本质是一个落地页：显示文件名、提供下载链接、列出后续接入方案（OCCT.js / pythonocc-core → glTF 转译 → 复用 `gltf.html`）。
- **占位逻辑**：仅读 `?file=` 然后把链接挂到 `<a download>` 上。
- **架构注脚**（来自页面正文）：CAD 高保真 = `OCCT (B-rep 求解) → tessellation → mesh viewer`，与 Mol* 处理 PDB 同构。

### 2.4 共用样式 / 横向能力

- `viewer/shared/style.css`：定义 `.topbar`、`.cards`、`.badge`、三个 domain 主题色（CSS 变量 `--domain-bio/med/ind`）以及 `.error-box`，保证四个独立 viewer 视觉一致。
- 错误处理：四个 viewer 都对 `file` 缺失和加载失败显示统一的 `.error-box` 红框。
- 性能：所有依赖走 unpkg CDN，**零本地构建** → 改一行 HTML 直接刷新；缺点是 CDN 不可用就全栈不可用。

### 2.5 端到端调用链（以海马 hippocampus_001 为例）

1. 浏览器打开 `index.html` → fetch `/viewer/assets.json`；
2. 读到一条记录：
   ```json
   { "name": "hippocampus_001",
     "path": "/crawl_data/medical/decathlon/extracted/Task04_Hippocampus/imagesTr/hippocampus_001.nii.gz",
     "viewer": "niivue", "format": "nifti",
     "overlay": "/crawl_data/medical/.../labelsTr/hippocampus_001.nii.gz" }
   ```
3. `viewerUrl()` 拼出
   `/viewer/niivue.html?file=/crawl_data/...imagesTr/hippocampus_001.nii.gz&overlay=/crawl_data/...labelsTr/hippocampus_001.nii.gz`；
4. `niivue.html` 读取两个 URL，`nv.loadVolumes([...])` → WebGL 渲染 multiplanar 四视图 + 红色 label overlay。

---

## 3. 已知缺口与改进点

### 3.0 原有 6 项（biology / medical / industrial）

1. **AlphaFold 抓取全失败** → `biology/alphafolddb/` 为空，需修 URL 规则。
2. **STEP 渲染未实现** → 当前 `occt.html` 只能下载；CAD domain 实际上只有 glTF 一条腿能跑。
3. **Niivue `sliceType` 不可配置** → 无法用 URL 切到单视图（axial-only / coronal-only），影响"固定视角" benchmark。
4. **Mol* 默认 pose 不对齐对称轴** → 不同 PDB 默认视图无可比性，需要 viewer 支持"提供对齐矩阵 / canonical orientation" 参数。
5. **medical 暴露的数据量被人为截短**（manifest 只暴露 5 train + 3 test，原始 650 个）→ 真正大规模评测需要重写 manifest 或换 server-side 分页。
6. **无构建链路 / 全 CDN 依赖** → 离线环境下 viewer 完全不能用，做正式 benchmark 应当 vendor 一份本地副本。

### 3.1 新增 3 个 domain（2026-05-11 实施）

为支撑机器人、点云、拓扑光子三大 domain，平台从"3 domain 4 viewer"扩展到 **"6 domain 10 viewer"**：

#### Robotics · URDF / SDF
- **数据来源**：`crawl_data/robotics/urdf_sdf_samples/`，含 `kuka_iiwa.urdf`、`r2d2.urdf`、`gazebo_shapes.sdf`（共 ~29 KB）。
- **Viewer**：`viewer/urdf.html`（three.js 0.160 + OrbitControls + 内联 XML parser）。
  - 自动识别根元素 `<robot>` (URDF) vs `<sdf>` (SDF)，分别走两套解析路径；
  - URDF：递归构建 link/joint 树，`<origin xyz rpy>` → three.js `Group.position/rotation`；每个 joint 加一个 `AxesHelper` 显示 +X/+Y/+Z；
  - SDF：解析 `<pose>` 单字符串成 `xyz+rpy`，对每个 `<link>` 内的 `<visual><geometry>` primitive 渲染；
  - 三种 primitive 直接映射：`<box size>` / `<cylinder radius length>` / `<sphere radius>`；
  - **mesh 引用 fallback**：KUKA URDF 引用 `meshes/link_0.obj` 等外部文件，数据集中不存在 → 渲染一个小灰色 wireframe box 占位，且在右下角显示 mesh 占位计数（"8 mesh refs (placeholder boxes)"）。**保留拓扑信息但丢失精确几何**。
- **已验证（HTTP smoke + Python parsing 验证）**：
  - r2d2（16 links / 15 joints / 12 primitives + 4 mesh refs）：完整渲染；
  - kuka_iiwa（8 links / 7 joints / 0 primitives + 8 mesh refs）：仅渲染骨架，所有 link 显示为占位 box；
  - gazebo_shapes（7 SDF links / box+cylinder+sphere）：正常渲染。
- **未尽事项**：mesh 引用真实加载需要附加 `meshes/` 子目录抓取或服务端 STL→glTF 转换；joint 当前不可动（应加滑条驱动 revolute/prismatic 轴）。

#### Point Cloud · LAS / LiDAR
- **数据来源**：`crawl_data/pointcloud/las_samples/`，`laspy_simple.las` (1,065 点, 36 KB) + `pdal_autzen_trim.las` (110,000 点, 3.7 MB)。
- **Viewer**：`viewer/pointcloud.html`（three.js 0.160 + 内联 LAS 1.x DataView parser + `Points`）。
  - **LAS header**：读取 magic `LASF`、版本、`pdrOffset@96`、`pdrFormat@104`、`pdrLength@105`、`nPoints@107`、XYZ scale+offset@131/155、bbox@179；
  - **Point Data Record**（fmt 3, 34 byte）：X/Y/Z (int32 scaled) + Intensity (uint16) + RGB (3×uint16) @ offset 28；
  - **数值精度问题**：LAS 坐标常为 UTM（百万级数值），float32 会丢精度 → viewer 把所有点减去 bbox 中心后再写进 `BufferAttribute`；
  - **三种着色模式**（右上下拉框可切换）：(a) RGB（资产自带颜色），(b) Z 高度 rainbow（HSL，蓝色低/红色高），(c) Intensity 灰度；
  - 相机 `up=+Z`（LAS 标准约定）；网格 + 自适应远近裁剪面。
- **已验证**：两个文件首/末点的 X/Y/Z/RGB 在 Python 端用同一公式得到一致结果。
- **未尽事项**：>1M 点的 LAS 需 chunked streaming / LOD（Potree），目前是一次性 ArrayBuffer + 单 BufferGeometry，~10M 点会触发浏览器 OOM。

#### Topological Photonics · band / GDSII / MPB ctl / field
四种子格式各自有一个 viewer。

##### (a) Band 数据 `.dat` → `viewer/bandplot.html`
- **数据来源**：Zenodo 15591239（"Photonic band structure of a topological 1D nanobeam"）共 5 个 TSV：
  - `nanobeam_edge_vs_noedge.dat`（4096 行 × 4 列）— edge state vs no-edge baseline；
  - `nanobeam_measured_spectrum.dat`（1024 × 6）— PL + 不同器件的实测 transmission；
  - `nanobeam_gap_dependence_a300_w07.dat`（4096 × 13）— 12 个 gap distance 参数扫；
  - `nanobeam_band_d1_0p0.dat` / `..._1p0.dat`（~1.3K × 50 列）— 完整 band map（k-space × frequency）；
- **解析逻辑**：自动检测 leading header 行数（首列非 numeric 即为 header），最后一行 header 作为列标签 → Plotly `scattergl` 多 trace 折线；空 trail 列（如 50 列中的 padding）自动过滤；
- **已验证**：5 个文件 header 检测 1/3/3/1/3，分别得到 3/12/3/48/5 条 trace。
- **库**：Plotly 2.35.2 (CDN)。

##### (b) GDSII `.gds` → `viewer/gdsii.html`
- **数据来源**：`klayout_t10.gds`（KLayout testdata，2.7 KB，29 polygons，8 layers）。
- **Viewer**：内联 GDSII 二进制 parser + Canvas2D 渲染。
  - **记录格式**：`[uint16 len][uint8 rec_type][uint8 data_type][payload...]`，全部 big-endian；
  - 解析 `STRNAME`(0x06) / `BOUNDARY`(0x08) / `PATH`(0x09) / `LAYER`(0x0D) / `XY`(0x10) / `ENDEL`(0x11)；
  - **图层着色**：8 个调色板循环（与 viewer/shared 主题色对齐）；右上角图例列出每层的 polygon 数量；
  - 交互：左键拖拽平移、滚轮缩放（缩放以鼠标位置为中心）。
- **已验证**：29 polygons + 8 layers (1..8) Python 复算与 viewer parser 一致。
- **未尽事项**：未处理 `SREF`/`AREF`（结构引用）展开，意味着含 cell hierarchy 的复杂版图只显示顶层 boundary；未解析 GDSII 自定义 8-byte float (`UNITS`)，画面单位用 raw db-units（KLayout 默认 1 nm/db-unit）。

##### (c) MPB ctl `.ctl` → `viewer/mpbctl.html`
- **数据来源**：`mpb_diamond_lattice.ctl`（NanoComp/mpb 示例，3D diamond fcc 光子晶体）。
- **Viewer**：Prism.js 1.29 (CDN) 语法高亮 + 轻量正则提取关键参数。
  - 自动从 Scheme 源码抽取：`resolution`、`num-bands`、`mesh-size`、`eps`、`r`，以及 `vector3` / `make sphere` / `make cylinder` / `make block` 数量；
  - 渲染为顶部"关键参数"面板 + 下方语法高亮全文。
- **已验证**：从 1510-byte ctl 提取出 `resolution=16, num-bands=5, eps=11.56, r=0.25, vector3=8, spheres=2`，与文件内容一致。
- **本质**：不是 3D viewer，而是"研究人员第一眼想看的参数 + 完整源代码"——MPB 的真实 3D 渲染需要在本地运行 `mpb diamond.ctl` 生成 HDF5 field（见下条）。

##### (d) MPB/MEEP field `.h5` → `viewer/field3d.html`（占位）
- **数据来源**：**当前数据集中无 HDF5 field 样本**。理由：MPB 的 field output 不是直接下载的——它由 `mpb-mpi diamond.ctl` 本地运行产出，依赖 MPI + WASM 复杂性。
- **Viewer**：format-route 占位页，给出"HDF5 → NIfTI → 复用 niivue.html"的服务端转换 recipe（Python：`h5py` + `nibabel`）。
- **架构意义**：把"电磁场 3D scalar volume"和"医学 MRI 3D volume"归为同一个渲染问题 → 不重复造 viewer，复用 Niivue 的 multiplanar + 体绘制管线。
- **激活路径**：在 `crawl_data/topological_photonics/fields/*.h5` 放入文件后，`build_manifest.py` 会自动登记为 `viewer="field3d"`；下一步把 HDF5→NIfTI 转换器加进 `tools/` 即可端到端打通。

#### 三个 domain 的入口聚合

更新后的 viewer routing 表：

| 域 | viewer 字段 | HTML 入口 | 处理格式 | 关键依赖（CDN） |
|---|---|---|---|---|
| biology | `molstar` | `molstar.html` | mmcif / pdb / sdf / mol2 / bcif | molstar 4.5 |
| medical | `niivue` / `cornerstone` / `raw` | `niivue.html` / 后续 | nii.gz / dicom / nrrd | niivue 0.45 |
| industrial | `gltf` / `occt` / `raw` | `gltf.html` / `occt.html` | glb / step / iges / stl / usdz | three 0.160 |
| **robotics** | **`urdf`** | **`urdf.html`** | **urdf / sdf** | **three 0.160** |
| **pointcloud** | **`pointcloud`** | **`pointcloud.html`** | **las** | **three 0.160** |
| **topological_photonics** | **`bandplot` / `gdsii` / `mpbctl` / `field3d`** | **4 个 HTML** | **dat / gds / ctl / h5** | **plotly 2.35 / prism 1.29 / three 0.160** |

`viewer/index.html` 的 `viewerUrl()` 已扩展，新增 5 条 case（`urdf` / `pointcloud` / `bandplot` / `gdsii` / `mpbctl` / `field3d`），并把三个新 domain 的 section 渲染颜色调整为 `--domain-rob/pcl/topo`（`#ffa657 / #79c0ff / #ff7b72`）。

### 3.2 当前 manifest 全景（v0.3 regen 后）

```
biology               51 assets
medical               15 assets
industrial            14 assets
robotics               3 assets   ← new
pointcloud             2 assets   ← new
topological_photonics  7 assets   ← new
─────────────────────────────────
total                 92 assets   （raw=3, 其余 89 已接 viewer）
```

### 3.3 v0.4 — 兜底 viewer 路由（NRRD / USDA / USDZ）

v0.3 收尾时还剩 3 个资产挂在 `viewer="raw"`：`mr_head.nrrd`、`pixar_hello_world.usda`、`apple_teapot.usdz`。
v0.4 把这 3 个全部接入专用 viewer。

#### (a) NRRD `.nrrd` → 复用 `niivue.html`

- **结论**：**已完整解决**，零新代码，仅 manifest 路由变更。
- **依据**：Niivue 0.45 minified bundle 内验证（grep 命中）：
  - 函数 `readNRRD`（NRRD 头部 ASCII 键值对 + raw/gzipped 体数据解码）；
  - extension dispatch 含 `case "NRRD"` 与 `case "NHDR"`；
  - 同时观察到 `case "MGZ" / "MHA" / "MHD" / "MIF" / "MIH"`——意味着 Niivue 一并覆盖 FreeSurfer、MetaIO、MRtrix 等格式，后续这些格式新增样本可直接复用 `niivue.html`。
- **代码改动**：`tools/build_manifest.py` 中 `slicer_nrrd/*.nrrd` 的 `viewer="raw"` → `viewer="niivue"`，删除告警 `note`。
- **样本表现**：`mr_head.nrrd`（256×256×130 short volume，~6.3 MB；Slicer 经典 MRHead 脑部 T1 样本）经 `python3 -m http.server` + `niivue.html` 加载，自动走与 NIfTI 完全相同的 multiplanar + 3D 体绘制管线，并复用 RAS+ 朝向小立方。

#### (b) USDA `.usda` → 新建 `viewer/usda.html`

- **结论**：**已完整解决**。
- **实现**：
  - Prism.js 1.29（`language-python` 高亮做近似，USD 没有官方 Prism lexer）；
  - 自研 USD prim 解析器（300 行 JS，纯正则 + 平衡 `{}` 扫描）：
    - 抽取 `#usda 1.0` 版本号；
    - 抽取顶层 `( ... )` stage metadata；
    - 递归扫描 `def [Type] "Name" { ... }` 形成 prim 树（处理嵌套花括号与字符串）；
    - 每个 prim 内的属性（`type? name = value` / `custom`/`uniform` 限定符）抓出列在折叠节点下；
  - 渲染为：顶部摘要（USD 版本 / 字节数 / prim 类型分布）+ 中部 stage metadata 面板 + 可折叠 prim 树 + 底部完整源码（语法高亮）。
- **样本表现**：`pixar_hello_world.usda`（339 B，1 Xform + 1 Sphere），输出 `USD 1.0 · 0.33 KB · 2 prims (Xform×1, Sphere×1)`，正确展开树结构并显示 `xformOp:translate = (4, 5, 6)` 等属性。

#### (c) USDZ `.usdz` → 新建 `viewer/usdz.html`（**部分解决**）

- **结论**：**几何 3D 渲染未解决，但包内容可视化已解决**。
- **可解决的部分**：
  - JSZip 3.10 在浏览器端解 USDZ（验证文件头 `PK\\x03\\x04`，标准 ZIP）；
  - 列出包内所有文件（`.usdc` 二进制 USD + PBR 贴图 PNG/JPG）+ 大小；
  - PBR 贴图通过 `URL.createObjectURL(blob)` 渲染为 `<img>` 缩略图；
  - 按 Apple Quick Look 命名约定（`_bc/_n/_r/_m/_ao`）自动标注贴图角色（baseColor / normal / roughness / metallic / ambientOcclusion）。
  - 样本表现：`apple_teapot.usdz`（8.6 MB）解出 1 个 `teapot.usdc` (4.1 MB) + 5 个 PBR 贴图 (`bc/ao/r/n/m`)，全部缩略图正确显示。
- **解决不了的部分（**重要诚实说明**）**：
  - `.usdc` 是 OpenUSD 的**二进制压缩格式**，规范开放但解析器只有：
    - 官方 C++ OpenUSD（不能直接跑在浏览器里）；
    - **USD-WASM**：把 OpenUSD 通过 Emscripten 编译成 WASM。可行，但产物 ~10 MB+，必须额外维护构建。
    - `three-usdz-loader` (npm)：实质包了一份 USD-WASM，依赖同样的大。
  - `<model-viewer>` web component（Google AR）：
    - 实测 macOS **Safari** 打开 USDZ → 触发系统级 Quick Look，能看到几何；
    - 但 **Chrome / Firefox / Windows / Linux** 上 `<model-viewer>` 不会真渲染 USDZ，只在 iOS Safari AR 模式 + macOS Safari Quick Look 起作用——也就是说**桌面 viewer 内无法跨浏览器渲染 USDZ 几何**。
  - 所以本 viewer 的明示折中（页面顶部 notice 直接告诉用户）：
    > USDZ 是 `.usdc` + 贴图打成的 zip。本 viewer **只解包、展示贴图，不渲染 mesh**。如需几何渲染：
    > 1. macOS Safari 直接点 USDZ 文件 → 系统 Quick Look；
    > 2. 服务端 `usdzconvert` / `usd_to_gltf` 转 glTF 后用 `/viewer/gltf.html`。
- **架构启示**：USDZ 与 STEP 是平行问题——「专业格式不能在浏览器原生渲染」**不是 viewer 设计缺陷，而是格式本身需要重 dep**。两者都通过"占位/部分可视化 + 服务端转换 recipe"兜底，而不是硬塞 WASM 进 viewer。

#### v0.4 后的 viewer 路由全景

| viewer 字段 | HTML | 处理格式 | 备注 |
|---|---|---|---|
| `molstar` | `molstar.html` | mmcif / pdb / bcif / sdf / mol / mol2 / xyz | 51 资产 |
| `niivue` | `niivue.html` | nii.gz / **nrrd** | 11 资产 (+1 NRRD) |
| `gltf` | `gltf.html` | glb / stl / obj / ply | 9 资产 |
| `cornerstone` | `cornerstone.html` | dicom | 4 资产 |
| `occt` | `occt.html` | step / iges | 3（仅下载入口） |
| `urdf` | `urdf.html` | urdf / sdf | 3 资产 |
| `pointcloud` | `pointcloud.html` | las | 2 资产 |
| `bandplot` | `bandplot.html` | dat | 5 资产 |
| `gdsii` | `gdsii.html` | gds | 1 资产 |
| `mpbctl` | `mpbctl.html` | ctl | 1 资产 |
| `field3d` | `field3d.html` | h5 (占位) | 0 资产 |
| **`usda`** | **`usda.html`** | **usda** | **1 资产（v0.4 新增）** |
| **`usdz`** | **`usdz.html`** | **usdz** | **1 资产（v0.4 新增；不渲染几何）** |
| `raw` | — | — | **0 资产**（v0.4 之后已清空） |

`test_pages.sh` 同步加上 `usda.html` / `usdz.html` 的 HTTP 断言（PASS=29 / FAIL=0），`test_assets.py` 把 `usda` / `usdz` 加入 viewer whitelist。

### 3.4 v0.4 之后真正未解决的事

为避免后续读者再踩坑，这里把"看起来可以解决但实际成本很高"的几个 gap 单独列出：

| 议题 | 现状 | 想真做需要付的代价 |
|---|---|---|
| **USDZ 几何 3D 渲染** | viewer 只展示贴图与文件清单 | 引入 **USD-WASM**（~10 MB 产物 + 构建维护），或服务端 `usdzconvert` 转 glTF；前者污染 viewer 层，后者污染 crawl_data pipeline |
| **`.usdc` 二进制内容解析** | 文件大小 + 文件名 | 同上：必须 USD-WASM |
| **STEP B-rep 实际几何** | `occt.html` 仍是下载入口 | 引入 **OCCT.js**（>5 MB Emscripten 构建）或服务端 tessellation；与 USDZ 同性质问题 |
| **MPB/MEEP HDF5 field** | `field3d.html` 占位 + 转换 recipe | 需本地跑 `mpb-mpi diamond.ctl` 生成 HDF5，**没有公开 dataset 现成可下载**——这是数据侧 gap，不是 viewer 侧 |
| **URDF mesh refs** | 显示为灰色 wireframe box 占位 | crawler 把 `meshes/*.obj` 子目录一并抓下（KUKA / Franka 仓库均提供），或服务端把外部 mesh 转 inline glTF |
| **Niivue `sliceType` 不可配** | URL 只能进 multiplanar 四视图 | viewer/niivue.html 加 `?sliceType=0..4` 参数即可（30 行 JS） |

前三条是**同一类问题**——专业格式（USDZ / USDC / STEP）在浏览器里要么走重 WASM、要么走服务端转译。
后三条则是**纯工程量**，没有架构难度，列入下一轮 roadmap 即可。

### 3.5 v0.5 — Negative samples 子集（错误建模 / problematic models）

**单独立档**：分类学、INDEX.json schema、入库 pipeline、viewer 集成全部在 [`NegativeSamples.md`](NegativeSamples.md) 里维护，本文档不重复。

为什么单独立档：
- 错误样本的"被看到"语义跟 clean 数据根本不同（clean = 渲染即可用，negative = 默认渲染就是错误本身），不能混入 `assets.json` / clean section；
- 三层分离（目录 / manifest / viewer）：`crawl_data/{domain}/broken/` · `viewer/negative_assets.json` · viewer 独立 section + `[NEG]` badge；
- 生命周期跟随 benchmark **错误分类学** 演进，节奏与本文档不同步。

**v0.5 当前进度**（2026-05-11，对应 NegativeSamples.md v0.1.1）：
- ✅ Spec doc `NegativeSamples.md` v0.1（分类学 13 类 / INDEX.json schema / 空清单骨架）
- ✅ 三个 domain 的空骨架目录：`crawl_data/{biology,medical,industrial}/broken/` + 各自 `INDEX.json` + `README.md`
- ✅ `tools/build_negative_manifest.py`（80 行，schema/path/error_type 校验）→ `viewer/negative_assets.json`（empty state）
- ✅ `viewer/index.html` Negative Samples section + `viewer/shared/style.css` 增加 `.card.neg / .neg-banner / .neg-divider` 等类（红色 NEG badge / severity 颜色 / expected behavior 行 / 容错 fetch）
- ⏳ 抓取脚本 `scripts/data/{bio,med,ind}_broken.py` — 待 NegativeSamples v0.2
- ⏳ 真实样本入库（每 domain 5–10 个） — 待 NegativeSamples v0.2

> 注：`v0.5` 是本文档（CrawlDataAndViewer）的项目版本；负样本子集本身用独立版本序列 `NegativeSamples v0.1 / v0.1.1 / v0.2`，不强制 1:1 对齐。
> v0.1.1 把 viewer 基础设施提前完成（用户不必等样本入库就能看到 UI 框架），v0.2 只剩"真实入库"一件事。

**与本文档（CrawlDataAndViewer）的边界**：
| 内容 | 归属 |
|---|---|
| clean 资产清单 / viewer 路由 / 抓取健康度 | 本文档 |
| 负样本错误类型分类学 / `error_type` code 表 | NegativeSamples.md §2 |
| INDEX.json schema | NegativeSamples.md §3 |
| 负样本 pipeline 与 viewer 集成 | NegativeSamples.md §5 |

### 3.6 v0.6 — 卡片缩略图（thumbnail glyph + tier-2 lazy 预览）

`http://127.0.0.1:8765/viewer/` 的每张 asset card 之前只有"标题 + badge + 大小"三件套，跨格式视觉无差异。v0.6 在 card 顶部加 **72 px 高 SVG 预览槽**：

#### Tier 1 — format-coded SVG glyph（同步、无 fetch）

`thumbGlyph(viewer, color)` 按 `asset.viewer` 字段分发 14 套独特图形，颜色绑 domain：

| viewer | 图形语义 |
|---|---|
| `molstar` | 5 atom + bonds（中心实心 + 四周环原子 + 化学键线） |
| `niivue` | MPR 正交三平面 + 体素 cube |
| `cornerstone` | radial gradient CT slice + 虚线 ROI 圈 |
| `gltf` | isometric wireframe cube |
| `occt` | 带 2 孔的 CAD 矩形零件 |
| `urdf` | 6 节点 4-link 关节树 |
| `pointcloud` | 36 个伪随机散点（半径/透明度抖动） |
| `bandplot` | 3 条 Q 形 dispersion 曲线 |
| `gdsii` | 6 个不同 layer 的矩形块（透明度差异化） |
| `mpbctl` | 5 行 Scheme 代码 + 注释 |
| `field3d` | 3 条 sin-wave |
| `usda` | 嵌套 `def Xform { def Sphere { … } }` 文本 |
| `usdz` | 包+`.usdc`+`+pbr` 标注 + 2 个 texture 块 |
| `raw` | 经典文件图标（折角 + 横线） |

颜色映射 `DOMAIN_COLOR`：biology=`#f78166` / medical=`#56d364` / industrial=`#d2a8ff` / robotics=`#ffa657` / pointcloud=`#79c0ff` / topological_photonics=`#ff7b72` / negative=`#f85149`。

#### Tier 2 — lazy text preview（异步、按需 fetch）

对 7 种文本格式（`urdf` / `sdf` / `ctl` / `usda` / `mol` / `mol2` / `xyz`），`enhanceThumbLazy()` 通过 **IntersectionObserver** 监听 card 进入视口，触发后排进队列：

- `_tier2Queue` + `_tier2Drain()`：**最多 3 并发** fetch，避免 92 个 card 同屏时把服务器/浏览器打死；
- 取首 ~7 行非空内容（每行截 22 字符），用 `thumbFromLines()` 渲成 monospace SVG `<text>` 替换 Tier-1 glyph；
- 任何 error 静默回退到 glyph，不影响主流程。

#### 代码 footprint

| 文件 | 改动 |
|---|---|
| `viewer/index.html` | +180 行（`THUMB_GLYPHS` + `thumbGlyph()` + `thumbFromLines()` + `enhanceThumbLazy()` + 队列 + `render()` / `renderNegative()` 插入 `.thumb` 节点） |
| `viewer/shared/style.css` | +14 行（`.card .thumb` + `.card .thumb svg` + `.card .thumb .thumb-text` 样式） |
| `tools/test_pages.sh` | +7 行（`thumbGlyph` / `DOMAIN_COLOR` / `enhanceThumbLazy` / `negative-biology` / 3×`negative_assets.json` 断言）→ 33 PASS → 40 PASS |

#### 设计取舍

- **没用 Three.js / Niivue 在线渲染缩略图**：92 个 card 全部触发 WebGL 上下文 = 浏览器死亡；
- **没做 Python pre-render PNG 流水线**：会引入 `nibabel` / `trimesh` / `rdkit` / `matplotlib` 等重 dep，违反"stdlib-only crawler"原则；
- **没做 mesh/molecular tier-2**：mesh 投影需要 3D→2D 矩阵运算，molecular 解析 mmCIF/PDB 体量大；增量价值低于实现成本，留 v0.6.x。

#### 已知 gap（v0.6.x roadmap）

- molecular 文件的 tier-2（PDB ATOM 记录前 N 行 → SVG dots）；
- mesh 文件的 tier-2（STL/OBJ ASCII 前 30 vertex 正交投影）；
- LAS 文件的 tier-2（首 379 byte 解析 bbox + npoints → bbox + 散点）；
- `.dat` band 文件的 tier-2（首 4 KB 解析首列 → mini line plot）；
- IndexedDB 缓存 tier-2 结果，二次访问免 fetch。

---

### 3.7 v0.8 — Cutaway 面板（visibility / opacity / clip plane，Three.js viewer 共用）

#### 起因

加了 negative samples（v0.5/v0.2）之后，看 4FE1 / NegativeScaleTest / kuka_iiwa 这些"内部有问题的"模型时，OrbitControls 只能绕外壳转，**看不到内部**：链节的内部碰撞、负缩放面的背面 winding、低分辨率结构的内核位移，全部被表面挡住。Mol* 自带 selection / representation UI 解决了 biology；niivue 自带 MPR + clip plane 解决了 medical；唯独**三个 Three.js viewer**（`gltf.html` / `urdf.html` / `pointcloud.html`）只有"加载即不可改"的 mesh，没有任何内透手段。

#### 设计原则

| 原则 | 取舍 |
|---|---|
| 最小依赖 | **零新外部依赖**，沿用已 import 的 `three@0.160.0`；面板 DOM 用 vanilla JS 直接写。 |
| 共用，不复制 | 面板逻辑落 `viewer/shared/cutaway.js`（ES module），三个 viewer 各 `import { attachCutaway } from '/viewer/shared/cutaway.js'` 一行接入。 |
| 不重复 import three | `attachCutaway` 接受调用方注入的 `THREE` / `renderer` / `scene` 实例（避免两份 three.js 共存导致 `instanceof` 失效）。 |
| 不污染原 material | opacity 改动走 **clone-on-write**：第一次拖 slider 时 `mesh.material = mesh.material.clone()`，原 material 不动；reset 时把指针复位。 |
| 性能税最小化 | `renderer.localClippingEnabled` 默认 `false`，仅在任一 clip slider ≠ 0 时翻 `true`；clip plane 法向固定 ±X/±Y/±Z，不做任意斜切（90% 用户够用）。 |

#### 面板组成（按从上到下顺序）

```
┌─ Cutaway ▾ ────────────┐  ← 头部可折叠（默认展开），与 #view-toggle/#controls 同位
│ Visibility & opacity   │
│ ▢ link_0   [opacity ▭] │  ← 遍历根 Object3D 的具名子节点，深度 ≤ 2
│ ▢ link_1   [opacity ▭] │     过滤 AxesHelper / GridHelper / 没有 .name 的匿名节点
│ ▢ Mesh_001 [opacity ▭] │     最多展示 12 行，超出折叠到 "+ N more"
│ Clip plane             │
│ X  [── slider ──]      │  ← 三个 -100..100% slider，按 root bbox 实时换算
│ Y  [── slider ──]      │     THREE.Plane(normal, constant) 的 constant
│ Z  [── slider ──]      │     翻向用 ± 切换按钮
│ [Reset]                │  ← 一键还原 visibility/opacity/clip
└────────────────────────┘
```

#### 实现核心

```js
// viewer/shared/cutaway.js (摘录)
export function attachCutaway({ THREE, renderer, scene }) {
  // 1) 渲染面板 DOM；
  // 2) 维护一组 THREE.Plane（X/Y/Z）+ 一个根 Object3D 引用；
  // 3) setRoot(obj) 时：
  //    - 计算 bbox → 存 min/max/center 用于 slider→constant 换算；
  //    - 遍历 obj.children（且每个 child 的 children）收集 "命名 + isObject3D" 节点；
  //    - 对收集到的每个 mesh 设置 mesh.material.clippingPlanes = activePlanes()；
  // 4) Slider input 事件：更新 plane.constant、active 集合、renderer.localClippingEnabled；
  // 5) Visibility checkbox：obj.visible 切换；opacity slider：clone-on-write material + transparent=true；
  // 6) refresh() 给 URDF 这种异步加载场景（mesh 后到）用，重跑 setRoot 即可。
  return { setRoot, refresh, dispose };
}
```

`urdf.html` 是唯一异步加载场景：`built.ready.then(() => { fitCamera(); cutaway.refresh(); })` 在所有 mesh promise 解析后再扫描具名节点。`gltf.html` / `pointcloud.html` 同步加载，`fitAndAdd(obj)` 末尾直接 `cutaway.setRoot(obj)`。

#### 文件改动

| 文件 | 改动 |
|---|---|
| `viewer/shared/cutaway.js` | **新建**，~180 行 ES module |
| `viewer/shared/style.css` | +30 行（`.cutaway-panel` 主体、`.cutaway-panel__head/sec/row`、`body.has-neg-banner .cutaway-panel { top: calc(60px + var(--neg-banner-h)) }`） |
| `viewer/gltf.html` | +5 行：import + `attachCutaway()` + `setRoot()` 调用 |
| `viewer/urdf.html` | +6 行：同上 + `built.ready` 后 `refresh()` |
| `viewer/pointcloud.html` | +5 行：同上（`THREE.Points` 单根，visibility 树退化但 clip 仍生效） |
| `tools/test_pages.sh` | +5 条 assert（脚本 200 + `attachCutaway` export + 3 viewer 引用） |

#### 设计取舍 / 不做什么

- **不做 molstar / cornerstone**：molstar 自带成熟的 selection + representation UI，重做 = 浪费；cornerstone 是 2D 切片，"看内部"靠 wheel 翻 slice，不是透明化。
- **不做 niivue 体积 clip**：niivue 原生 `setClipPlane(depth, az, el)` 已经够用，挂面板需要单独适配 azimuth/elevation 换算，留 v0.9。
- **不做任意斜切平面**：6DOF clip 需要 quaternion + 实时 gizmo，UI 复杂度跳一个量级；±X/±Y/±Z 三轴对 99% 的"看内部"诉求够用。
- **不做 per-mesh 单独 clip**：所有 clip plane 全局生效。如果用户只想切一个 link，先把其他 link 透明度调 0 即可。
- **不持久化**：reload 后状态归零。持久化需要把 root 节点的稳定 ID 入 URL，对动态加载（urdf 异步 mesh）容易脏。

#### 已知 gap（v0.8.x 候选）

- 面板内"reset to default"目前只清 visibility/opacity/clip 三组状态，不重置 OrbitControls 视角；
- visibility 树深度 > 2 的资产（COMSOL `.h5` 多组 dataset 包成的 group）会折叠成单条 + "+N more"；
- pointcloud 的 visibility tree 退化成单条 "Points (N pts)" 后没法逐 chunk 切，等到入库 LAS tile 集合后再分；
- 与 negative banner 的视觉关系：banner 在顶部 + 面板在右侧，面板自动用 `body.has-neg-banner` override 下移，不重叠。

---

### 3.8 v0.9 — 5 处用户反馈修复

#### 3.8.1 透明化 / 看内部 — 全格式能力总表

v0.8 cutaway 面板上线后，本节是 **"哪些 viewer 能/不能让用户看模型内部"** 的权威速查表，作为 §2.3（四种 3D 查询）和 §3.7 cutaway 设计的横向补充。

| Viewer | Format | 能否透明 / 看内部 | 实现路径 | 备注 |
|---|---|---|---|---|
| `gltf.html` | glTF · GLB · STL · OBJ · PLY | ✅ **可** | v0.8 cutaway 面板（visibility 树 + 3 轴 clip + opacity） | 多 mesh 模型粒度最细；URDF 单 link 也能单独切 |
| `urdf.html` | URDF · SDF | ✅ **可** | 同上；`built.ready` 后 `cutaway.refresh()` 让异步 mesh 也进树 | 关掉外壳 link 看内部接头 |
| `pointcloud.html` | LAS | ✅ **可（仅 clip）** | 单 `THREE.Points` 单条 visibility 行，但 clip plane 在点着色器中生效 | 滑 Z slider 看下层地形 |
| `occt.html` | STEP · IGES | ✅ **可（v0.9 新）** | occt-import-js WASM 解析后产生 `THREE.Group` of meshes → 复用 cutaway | linkrods 这类多零件 STEP 每个零件独立可切 |
| `molstar.html` | mmCIF · PDB · SDF · MOL2 · BCIF · XYZ | ✅ **可（lib 自带）** | Mol* 内置 representation / selection UI（`Components` 面板） | 不接 cutaway 避免与 Mol* 自带 UI 重叠 |
| `niivue.html` | NIfTI · NRRD | 🟡 **部分（lib 原生）** | niivue `setClipPlane(depth, az, el)` API 可用；面板 UI 待 v0.10 | 体积渲染 clip ≠ surface clip，需要单独适配 |
| `cornerstone.html` | DICOM 单切片 | ❌ **不适用** | 2D 切片，"看内部"靠 wheel 翻 slice 不是透明 | 体素 dataset 走 niivue |
| `bandplot.html` | DAT band 文件 | ❌ **不适用** | 2D 数据可视化（dispersion 曲线），无 3D 体 | — |
| `gdsii.html` | GDS · OAS | ❌ **不适用** | 2D 版图（layer 显示已有 toggle，不算 3D 透明） | — |
| `mpbctl.html` | CTL（Scheme spec） | ❌ **不适用** | 文本规约文件，无几何 | — |
| `field3d.html` | HDF5 · VTU | 🟡 **部分（pending）** | 当前是 metadata info-pane 不渲染体；future 接 niivue 风格体绘制后可加 clip | — |
| `usda.html` | USDA | ❌ **不渲染** | Prism 语法高亮 + prim 树文本浏览，无 3D | USD-WASM 集成 = 后续大项 |
| `usdz.html` | USDZ | ❌ **不渲染** | JSZip 包内容浏览 + PBR 贴图预览，无 mesh | 同上 |

**结论**：8 个能产生 3D mesh 的 viewer 中，**5 个** 已支持看内部（gltf / urdf / pointcloud / occt / molstar），**1 个** 部分支持（niivue），**2 个**（field3d / usd*) 留作后续。其余 5 个本质 2D / 文本，"看内部"的需求不存在。

#### 3.8.2 删除 `pydicom_samples` 入库（修复 #2 + #3）

`crawl_data/medical/pydicom_samples/*.dcm` 4 个文件以前作为 cornerstone 的演示 asset 进 manifest，但实际意图与现实不符：

| 文件 | DICOM modality | PixelData | 实际类型 |
|---|---|---|---|
| `CT_small.dcm` | CT | ✓ | **单切片**（NumberOfFrames=1），不是 3D 体 |
| `MR_small.dcm` | MR | ✓ | 同上 |
| `SC_rgb_dcmtk_*.dcm` | OT (Other) | ✓ | RGB 截图，pydicom 自带的"测试图像" |
| `rtstruct.dcm` | RTSTRUCT | ✗ | **结构集**：向量 contour，连 PixelData 都没有，文件无 `DICM` magic（pydicom 内部测试 fixture，不符 DICOM Part 10 文件格式） |

把这 4 个塞进 Medical section 会让用户期待 3D 但点开看到一张静态图（前 3 个）或加载错误页（rtstruct）。**真正的 3D medical 数据**走 niivue（NIfTI/NRRD），已有 11 条覆盖（8 Decathlon hippocampus + 2 nibabel + 1 NRRD）。

修复：`build_manifest.py` 删除 pydicom_samples 入库块（约 12 行），文件留在磁盘备查。manifest medical 15 → 11 条；总资产 131 → 127。

#### 3.8.3 occt.html 真渲染 STEP / IGES（修复 #4）

之前 occt.html 只是占位页 — 显示文件名、提供下载、给 OCCT.js 集成 roadmap，实际**不渲染几何**。用户报告的 `occt_linkrods.step`（1.7 MB 多零件 B-rep）"无法加载"的根因不是文件坏，而是 viewer 根本没有解析能力。

v0.9 接入 [`occt-import-js@0.0.23`](https://github.com/kovacsv/occt-import-js)（LGPL-2.1，npm 上活跃维护，95 KB JS + 7.3 MB WASM via jsDelivr CDN）：

```
fetch ?file= ──▶ ArrayBuffer ──▶ occt.ReadStepFile(buf)/ReadIgesFile(buf)
                                          │
                                          ▼
                          { meshes: [{ name, attributes:{position, normal,
                                       index}, color }, …] }
                                          │
                                          ▼
                  THREE.Group ◀── per-mesh BufferGeometry + MeshStandardMaterial
                                          │
                                          ▼
                         scene.add() ──▶ fitAndAdd ──▶ cutaway.setRoot()
```

实现要点（与设计一致 → 不重写）：
- **冷启动 7.3 MB WASM** 在网络差时较慢：viewer 上方放进度条（`fetch → init → parse → mesh build` 四阶段），用户能看到非 hang 状态；
- **解析失败**回退到 v0.8 之前的下载链接占位（IGES 复杂体 / 非标 STEP 仍可能失败）；
- **复用 cutaway**：解析后产生的 `THREE.Group` 直接 `cutaway.setRoot()`，linkrods 多零件可逐个透明 / clip；
- **importmap 复用**：和 `gltf.html` 共用 three@0.160.0 importmap，OrbitControls + RoomEnvironment 同款；
- **依赖大小取舍**：7.3 MB WASM 比 STEP 文件本身（~1.7 MB）大 4×，但只下载一次（CDN 缓存），后续加载零成本。是 viewer 集成 OCCT 的**最小重 dep 路径**。

#### 3.8.4 URDF 卡片缩略图修复（修复 #5）

v0.6 给 URDF/SDF 卡片做的"tier-2 lazy 文本预览"误伤了视觉一致性 — 把 `urdf.html` 默认的"6 节点关节树 SVG glyph"覆盖成 XML 头 7 行（`<?xml version=...`、`<robot ...>`、`<link name=...>`），三个 URDF 卡片看起来全是同样的尖括号噪声。

修复：`viewer/index.html` 的 `TEXT_VIEWERS` 集合从 `{urdf, mpbctl, usda}` 缩为 `{mpbctl, usda}`。URDF/SDF 回归 tier-1 关节树 glyph，三个卡片视觉立刻可区分。`mpbctl`（Scheme code 五行 = 高信息密度）和 `usda`（USD prim 嵌套树 = 文本即视图）保留 tier-2，因为这两类文本预览的**首 7 行**真的是模型本身。

#### 3.8.5 文件改动汇总

| 文件 | 改动 |
|---|---|
| `docs/CrawlDataAndViewer.md` | +1 版本行 + 本节（§3.8） |
| `tools/build_manifest.py` | -12 行（删除 pydicom_samples 入库块） |
| `viewer/assets.json` | regen：medical 15→11，total 131→127 |
| `viewer/occt.html` | 重写：占位页 → occt-import-js + Three.js + cutaway 集成 |
| `viewer/index.html` | `TEXT_VIEWERS` 减 `urdf` |
| `tools/test_pages.sh` | +occt-import-js 引用 + 删 pydicom 路径（如有） |

---

### 3.9 v1.0 — 3 处用户反馈（astronomy 大改造 + occt 错误可见 + cornerstone slice nav）

#### 3.9.1 Astronomy — 从 17 张 raw 卡片到 3 张可视化卡片

v0.9 之前 `scan_astronomy` 把 `crawl_data/astronomy/` 下的所有底层文件平铺成卡片：

| 子目录 | 文件数 | viewer | 用户体验 |
|---|---|---|---|
| `textures/` | 4 (.jpg/.png) | raw | 点了下载 JPG / 图片预览 |
| `ephemeris/` | 11 (.json) | 9 raw + 2 saturn | 9 张点了下载 JSON 文本，2 张能开 saturn viewer |
| `exoplanets/` | 1 (.json) | raw | 下载 NASA TAP 响应 |
| 顶层 `questions.json` | 1 | raw | 下载题目 spec JSON |

**14 张卡片只是文件下载，0 个可视化能力**。用户报告"大部分文件都有问题"指的就是这个 — 每张卡片本应是"点开 → 看到 3D" 的 affordance，结果点开是 raw JSON。

**v1.0 改造**：把数据视角从"扫底层文件"改成"扫 Sci3D 问题"。`spec/astronomy/questions.json` 已经为每个 question 声明了 `model_refs` + `ephemeris_refs` + `render_recipe`，所以正确的 manifest 形态就是**每个 question 一张卡，路由到该 question 的 viewer**：

| Question | v_class | 卡片标题 | viewer | 主 ?file= |
|---|---|---|---|---|
| `saturn_ring_occlusion_2026_conjunction` | V2 | "土星合相时土星环的可见性" | `saturn.html` | `saturn_from_earth_2026_conjunction.json` |
| `flipeval_astro_saturn_conjunction_multiviewpoint` | V4 | "土星合相事件的多视角自一致性" | `saturn.html` | 同上（多视角扩展待 v2.5） |
| `solar_system_coplanar_invariant_j2000` | V6 | "太阳系行星轨道共面性" | `solar_system.html` | `solar_system_elements_j2000.json` |

底层 textures / element_*/exoplanets / `questions.json` 这些**不再单独成卡**：textures + saturn ephemeris 由 saturn.html 内部 fetch，elements 由 solar_system.html 内部 fetch。Astronomy 资产数 **17 → 3**。

**新增 `solar_system.html`**（~150 行 ES module）：

- 输入：聚合的 J2000 elements JSON `{ planets: { mercury: { EC, IN, OM, W, A_au, … }, … } }`
- 数学：每个行星按 Kepler 元素 `(a, e, i, Ω, ω)` 用 Curtis (4.41) 把 perifocal 帧 `(r cos ν, r sin ν, 0)` 旋转到 J2000 ecliptic 帧 `(X, Y, Z)`，256-segment 闭合 polyline
- 场景：太阳（点光源 + 黄色 sphere glyph）+ 8 条带颜色的轨道 line（mercury/venus/earth/mars/jupiter/saturn/uranus/neptune 各自调色板）+ 半透明 ecliptic 参考盘（让用户看到偏离平面的程度）+ 每条轨道一个行星 marker
- 三个相机预设：top（face-on，平面看似乎所有轨道重合）/ side（edge-on，按倾角散开 ~7°）/ iso
- 信息面板：行星表（按 `a` 排序）+ 最大倾角统计

**新增 `crawl_data/astronomy/ephemeris/solar_system_elements_j2000.json`**：聚合 8 颗行星 J2000 osculating elements 一个文件（之前 V6 question 引用此文件但磁盘上不存在）。Python 一行式从 8 个 `elements_*_j2000.json` 的 `$$SOE` 块取首行解析得到。

**新增 `viewer/index.html` 的 `solar_system` glyph**（同心圆 + 中央太阳 + 散落行星点），与 `saturn` glyph（倾斜环 + 中央球）一起补完 thumbnail 体系。

**新增 `tools/test_assets.py` 三处**：DOMAINS 加 `astronomy/simulation`、VALID_VIEWERS 加 `saturn/solar_system`、VALID_FORMATS 加 `json/jpg/png/pdf/vtu`。

#### 3.9.2 occt.html — 加可见错误诊断

v0.9 集成 occt-import-js 后用户实际打开 `?file=occt_linkrods.step` 看到**空白页**。Node 端实测 parse 770ms / 8772 tris 完全正常，所以问题在浏览器侧某一步，但 headless 测试栈不在仓库内，盲修风险大。

v1.0 把 `occt.html` 改成"任何阶段失败都有可见错误"：

- `window.addEventListener('error', …)` + `unhandledrejection` 监听把 stack trace 渲染到右上角 progress 面板
- 进度条从 4 阶段 → **5 阶段**（多了 "Loading occt-import-js" — v0.9 这一步是隐式 `<script src=…>` 加载，失败会静默卡住）
- CDN 双 fallback：`jsdelivr` 失败自动尝试 `unpkg`
- 三组模块导入（occt-import-js classic script / three / cutaway.js）分别 try/catch，定位失败的具体导入

下次用户打开如果仍失败，progress 面板会显示具体卡哪一步 + stack，可以照该信息精准修。

#### 3.9.3 cornerstone.html — slice 导航

用户明确："cornerstone 不用做透明化，但切片选择需要做"。v0.9 之前 `cornerstone.html` 只能显示一张图。v1.0 重写加切片导航：

| 输入形态 | 检测 | 切换机制 |
|---|---|---|
| 多帧 DICOM (`NumberOfFrames > 1`) | 加载第 0 帧后读 metadata `x00280008` | imageId 加 `?frame=N`（cornerstone-wado-image-loader 原生支持） |
| 同目录 series（多个 .dcm 同序列） | URL 加 `?series=<manifest.json>` 参数指向 JSON 数组 | imageId 直接切 wadouri |
| 单帧文件 | `nFrames === 1` | slice 面板隐藏，仅 W/L 控件 |

UI：左上 sticky 面板，slider + 位置（`3 / 70`），三套交互：
- 鼠标滚轮（监听 `#stage` 的 `wheel`，`preventDefault` 防止页面滚）
- 方向键 ← → ↑ ↓（`Home/End` 跳到首/末），仅在不在 input 焦点时响应
- slider 拖动

W/L 在切片间通过 `cornerstone.getViewport()` 保留，不会被 `cornerstone.getDefaultViewportForImage` 覆盖。

#### 3.9.4 文件改动汇总

| 文件 | 改动 |
|---|---|
| `docs/CrawlDataAndViewer.md` | +1 版本行 + 本节（§3.9） |
| `tools/build_manifest.py` | `scan_astronomy` 重写：扫 questions 而不是扫文件；不再索引 textures/elements_*/exoplanets/questions.json |
| `tools/test_assets.py` | DOMAINS / VALID_VIEWERS / VALID_FORMATS 扩展 |
| `crawl_data/astronomy/ephemeris/solar_system_elements_j2000.json` | **新建**，聚合 8 颗行星 J2000 elements |
| `viewer/solar_system.html` | **新建**，~150 行 Kepler 轨道可视化 |
| `viewer/saturn.html` | （v2.5 已存在，不变） |
| `viewer/index.html` | viewerUrl 加 `solar_system` 路由；thumbGlyph 加 `saturn` + `solar_system` glyph |
| `viewer/occt.html` | 加 5 阶段进度条 + window.error 监听 + CDN double fallback + 分阶段 try/catch |
| `viewer/cornerstone.html` | 加 slice 控件 + 多帧 / series manifest 双路径 + wheel/key/slider 三套交互 |
| `tools/test_pages.sh` | +5 条 astronomy/cornerstone assert，+反向 contains 检查（textures / questions.json / pydicom_samples 不在 manifest）。76 → 77 PASS |
| `viewer/assets.json` | regen：astronomy 17→3，total 127→113 |

---

### 3.10 v1.1 — Verifier 工具 + 2D 资产清理

#### 3.10.1 起因

v1.0 之后 manifest 还残留 6 条**物理上就是 2D** 的资产被错误地放在"3D viewer"框架里：5 个 bandplot `.dat` 频散曲线（k–ω 图）+ 1 个 GDSII `.gds` 光刻版图。它们和"看 3D 内部"诉求毫不相干，留在 viewer 里只会让用户点开看到平面图后困惑。但靠肉眼审 manifest 不可持续；下一个 domain 入库可能再带进来一批。需要的是**自动化检查**。

#### 3.10.2 Verifier 设计

新增 [`tools/verify_assets.py`](../tools/verify_assets.py)（~480 行 stdlib only）+ [`docs/Verifier.md`](Verifier.md) 完整 spec。三合一 sanity check：

| Check | 问题 | 实现 |
|---|---|---|
| **1. 3D-only** | viewer 是不是真能产生 3D 渲染？ | 16 viewer × 4 capability tag (`3D / PARTIAL / 2D / TEXT / DOWNLOAD`)；2D → CRITICAL，TEXT/DOWNLOAD → WARN，PARTIAL → INFO |
| **2. Category** | 文件类型与所在 domain 一致？ | 每 domain 扩展名白名单；mismatch → WARN + 建议 domain |
| **3. Negative real** | 每条 negative 样本真的"坏"？ | T1 schema/file/URL 全样本必跑；T2 按 `error_type` 静态特征验证 |

T2 覆盖 13 类 error_type 中的 7 类：
- `bio.low_resolution` — mmCIF `_refine.ls_d_res_high` ≥ 3.5 Å
- `bio.alphafold_low_plddt` — ATOM B-factor < 50 比例 ≥ 30%
- `bio.superseded` — `_pdbx_database_PDB_obs_spr` 表存在
- `ind.negative_scale` — glTF node `scale` 行列式 < 0（或 mat4 上 3×3 行列式 < 0）
- `ind.sparse_accessor` — 任一 `accessors[].sparse` 字段
- `ind.no_indices` — 任一 `meshes[].primitives[]` 缺 `indices`
- `ind.multi_uv` — `TEXCOORD_1` 或 `KHR_materials_pbrSpecularGlossiness`

剩余 6 类（`bio.clash_outlier / bio.rama_outlier / med.* / ind.non_manifold`）需要 validation report 或图论分析，T1-only。

**Read-only by default**。两个显式 flag 才动文件：
- `--apply-remove-2d` — 把 Check-1 CRITICAL 从 manifest 删掉（文件留磁盘）
- `--apply-fix-category` — 把 Check-2 WARN 移到建议 domain（manifest only，文件不动）

#### 3.10.3 首跑结果

```
[verifier] CRITICAL=6 WARN=19 INFO=16   exit=1
- Check 1 (3D-only):   6 CRITICAL · 12 WARN ·  3 INFO
- Check 2 (category):  0 CRITICAL ·  0 WARN ·  0 INFO
- Check 3 (negative):  0 CRITICAL ·  7 WARN · 13 INFO
```

- **Check 1** 6 CRITICAL 全是 topological_photonics：5 bandplot .dat + 1 gdsii .gds
- **Check 2** 0 finding — 113 个资产分类全对
- **Check 3** 13 INFO 全部 T2 静态特征确认 negative 样本真坏：4 个 ind.* 全对、2 个 low-res mmCIF（4FE1=4.92 Å、3IYV=7.90 Å）、3 个 AlphaFold pLDDT<50 比例 64.6/79.8/78.0%、3 个 supersede `obs_spr` 表 OBSLTE 三连

#### 3.10.4 执行清理

```bash
$ python3 tools/verify_assets.py --apply-remove-2d
[verifier] --apply-remove-2d: removed 6 entry(ies) from manifest
```

manifest 113 → 107，topological_photonics 7 → 1（仅剩 mpb_diamond_lattice.ctl）。

**同步改 `build_manifest.py`** 的 `scan_topological_photonics()`：删掉 `.dat` (bandplot) 和 `.gds` (gdsii) 两个 glob loop，加注释引用 v1.1 verifier 决策。这样下次 `python3 tools/build_manifest.py` regen 不会把 6 条又拉回来。`viewer/bandplot.html` / `viewer/gdsii.html` 文件**保留**（未来若有人愿意把 .dat 转 NIfTI 走 niivue / .gds 转 glTF 走 mesh viewer 仍可复活）。

#### 3.10.5 验证

- **Verifier 二跑**：`exit=0`，0 CRITICAL
- **`tools/test_pages.sh`**：仍 77/77 PASS（断言只检查 viewer 页面可达 + manifest 反向不含 pydicom/textures/questions.json，不依赖 topological_photonics 资产）
- **Report 落盘**：`crawl_data/_logs/verifier_report_<UTC>.md`

#### 3.10.6 文件改动汇总

| 文件 | 改动 |
|---|---|
| `tools/verify_assets.py` | **新建**，~480 行 stdlib-only verifier |
| `docs/Verifier.md` | **新建**，~200 行 verifier spec |
| `tools/build_manifest.py` | `scan_topological_photonics()` 移除 `.dat` + `.gds` glob，加 v1.1 决策注释 |
| `viewer/assets.json` | regen：topological_photonics 7→1，total 113→107 |
| `crawl_data/_logs/verifier_report_*.md` | 报告自动落盘，git ignored |

## 4. Server-side `crawl_data/`（v1.2，2026-05-13）

The internal server `UniPat-ScienceVision` (10.0.0.144) hosts a mirror of
the viewer at `http://localhost:80` (accessible via ssh local-port forwarding
by default; see `README.md` § Server deployment).

The server cannot reach `huggingface.co` directly (verified 2026-05-13:
`Connection refused` from the Aliyun host; `hf-mirror.com` is reachable but
~3 KB/s throughput, not viable for a 253 MB dataset). Therefore the server
gets `crawl_data/` via **rsync from the developer Mac**, not from Hugging
Face — packaged in `tools/deploy.sh`:

```bash
rsync -av --delete \
    --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r \
    ./crawl_data/ \
    UniPat-ScienceVision:/srv/sciencevision/repo/crawl_data/
```

The Hugging Face dataset `SueMintony/ScienceVision` remains the public /
external source of truth for `crawl_data/`. The Mac is the deployment relay
for the internal server only.

Design notes: [`docs/superpowers/specs/2026-05-13-viewer-server-migration-design.md`](superpowers/specs/2026-05-13-viewer-server-migration-design.md).

### 3.10 Medical · M3D `.npy` 输入样例 (v1.0)

`crawl_data/medical/m3d_samples/*.npy` 是 M3D-CLIP / M3D-LaMed 训练侧期望
的输入张量 (1×32×256×256, [0,1], float32)。Niivue 不能直接读 `.npy`，所以
`tools/build_manifest.py` 在扫描时调 `tools/m3d_pipeline.py` 的
`derive_nifti_from_m3d` 把每个 `.npy` 派生成 `m3d_samples/derived/*.nii.gz`
(`scale=255`, `transpose=True`, 单位 affine)，niivue 渲染派生件。卡片是
"双入口"：主链接 → niivue (派生件)，副链接 `↓ .npy` → 原始 .npy 下载。

派生件 (`derived/`) 走 `.gitignore` 不入库；`.npy` 入库。生成新样例：

```bash
datasets/_logs/.venv/bin/python tools/m3d_pipeline_cli.py prep \
    <some>.nii.gz crawl_data/medical/m3d_samples/<name>.npy
```

`build_manifest.py` 下一次跑会自动派生 `.nii.gz` 并把新卡片加进 medical
section。manifest item 多两个字段：`npy_download` (原 .npy 相对 URL)、
`derived_from="m3d_npy"` (来源标记，向后兼容)。

E2E 验证（headless chromium + WebGL readback）：
`tools/test_e2e_niivue_m3d.py`，标记 `@pytest.mark.e2e`。

设计 spec 与默认值 empirical 复核见
`docs/superpowers/specs/2026-05-15-m3d-npy-pipeline-design.md` §7。

## 5. Viewer 性能优化（v1.3）

Viewer 的传输 / iframe 调度 / glTF 解码 / HTTP/2 等加速实现均集中在
[`docs/ViewerPerformanceOptimization.md`](./ViewerPerformanceOptimization.md)。
本文件只承载"crawl_data 现状 + viewer 路由 / 兜底"两条主线，不再
重复性能调优细节。
