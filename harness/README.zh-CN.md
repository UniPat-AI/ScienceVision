# BabyVision-v2

[English](README.md) · [简体中文](README.zh-CN.md)

> **面向专业领域的 3D 锚定视觉推理评测。**
> 一个评测基准，以及供合作伙伴自助接入新领域、运行 QA 流水线的平台。

BabyVision-v2 是 [ScienceVision](https://github.com/SueMarsR/Science-Vision) 研究项目的对外公开版本。
它延续了 BabyVision v1 的方法论 —— *以评测为先、无需训练、用可执行的真值抵御 LLM-as-judge 偏差* —— 并在三个方向上做了扩展：真实的 3D 场景图、专业领域、以及带像素级证据的智能体操作轨迹。

---

## 两个入口

| | |
|---|---|
| **基准 / 排行榜**<br/>任务族、评测协议、排行榜、提交指南。<br/>→ [`docs/benchmark/`](docs/benchmark/) | **平台 / 合作伙伴 SDK**<br/>30 分钟快速开始、新领域模板、架构、API 参考。<br/>→ [`docs/platform/`](docs/platform/) |

合作伙伴（Genesis AI、Elorian AI、Meta 研究团队等）建议从 **平台 → 快速开始** 入手。基准贡献者请从 **基准 → 提交指南** 入手。

---

## 3D 资产与查看器

每个资产都登记在按领域划分的清单里，并由浏览器内的查看器渲染 —— 无需构建、无需插件。本地浏览：

```bash
python3 _dev_serve.py     # 以仓库根为 docroot，服务于 http://127.0.0.1:8080/
# → 打开 http://127.0.0.1:8080/viewer/（从「Domain 3D Assets」入手）
```

- **找资产：** `viewer/assets/<domain>.json`（外加汇总 `viewer/assets.index.json`）。
  每条记录含仓库根绝对路径 `path`、渲染器 `viewer`、格式 `format`。
- **渲染器：** Three.js（`gltf` —— glb/gltf/obj/stl/ply）、Mol\*（`molstar` —— mmcif/pdb/sdf/mol…）、
  NiiVue（`niivue` —— nifti/nrrd）、OpenCascade（`occt` —— step/iges）、USD（`usdz`/`usda`），
  以及领域专属页面（Genesis、URDF、GDSII、点云 等）。`viewer/` 下共 35 个页面。
- **字节：** git 只跟踪小体量 demo 资产；`crawl_data/` 与 `viewer/datasets/*/assets/`
  已被 gitignore，需另行拉取（ModelScope `tianyi3unipat/BabyVision-v2-Datasets`）。
  大型 CAD（STEP）为下载专用，网页端用派生的 GLB 渲染。

完整指南见 [`docs/platform/viewer-and-assets.md`](docs/platform/viewer-and-assets.md)。
在线 demo：**babyvision-v2.unipatai.com**。

专家名单/题目批次导入、按领域分配、离线脱敏分包与结果回载见
[离线题目管理工作流](tools/offline_task_manager/README.md)。

---

## 仓库结构

```
docs/benchmark/             # 排行榜、任务族、评测协议、提交指南
docs/platform/              # 快速开始、新领域模板、架构、API 参考
docs/partners/              # 公开案例（已脱敏；细节协作另行管理）

sci3d_agent/                # MCP 服务（56 个工具）、智能体运行器 —— 自 ScienceVision 镜像
viewer/                     # URL 参数化的静态查看器页面 + 资产清单 —— 镜像
  ├─ assets/                #   按领域划分的 3D 资产清单（path + 渲染器 + 格式）
  └─ *.html                 #   35 个浏览器内渲染器（Three.js、Mol*、NiiVue、OCCT、USD…）
QA-Gen/                     # 各任务族的 QA 生成流水线 —— 镜像
scripts/                    # match 构造、sam_service 桩 —— 镜像
datasets/_matched/          # 3D 锚定白名单索引（不含原始资产，需另行拉取）

examples/                   # docs/platform/quickstart.md 中引用的端到端示例
  └─ 02_medical_spatial_gt/ # 离线 stage-1 示例：NIfTI mask → \boxed{label} 评分
tools/                      # 本地同步脚本（.mirror-include → 镜像路径）
tests/                      # 核心测试套件
```

`sci3d_agent/`、`viewer/`、`QA-Gen/`、`scripts/`、`datasets/_matched/` 下的源码通过 [`.mirror-include`](.mirror-include) 清单从 [`SueMarsR/Science-Vision`](https://github.com/SueMarsR/Science-Vision) 镜像而来。`docs/benchmark/`、`docs/platform/`、`docs/partners/` 下的文档，以及 `examples/` 与 `tools/` 下的全部内容，均原生于本仓库。

从同级目录下的 ScienceVision 检出重新同步：

```bash
python tools/sync_from_sciencevision.py --src ../ScienceVision --plan   # 预演（不写入）
python tools/sync_from_sciencevision.py --src ../ScienceVision --apply  # 实际写入
```

完整的同步契约见 [`MIRROR.md`](MIRROR.md) 与 [`tools/README.md`](tools/README.md)。

---

## 进度

- ✅ 镜像同步基础设施（`.mirror-include` + `tools/sync_from_sciencevision.py`）
- ✅ 首批镜像（`sci3d_agent/`、`viewer/`、`QA-Gen/`、`scripts/`、`datasets/_matched/`）
- ✅ 离线 stage-1 示例：[`examples/02_medical_spatial_gt/`](examples/02_medical_spatial_gt/) —— 以可执行真值实现 NIfTI mask → `\boxed{label}` 评分
- ✅ 可运行的 [`docs/platform/quickstart.md`](docs/platform/quickstart.md)
- 🚧 Genesis-VP 快速开始、新领域模板演练、完整 QA-Gen 流水线

## 许可

见 [`LICENSE`](LICENSE)。
