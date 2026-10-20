# SOP: QA case → dataset → domain platform → 公开 URL

> **目的**: 从一道 QA case 出发，把它整理成 ModelScope 数据集条目，同步到 UniPat-Sing 上的 domain 平台，最终得到一条 `https://babyvision-v2.unipatai.com/qa_item.html?id=<task_id>` 可以对外分享的 URL。
>
> 适用范围: `tianyi3unipat/BabyVision-v2-Datasets` (demo pack) 结构下的所有 QA. 大规模生成的 QA batch 走 [`qa-to-viewer-loop.md`](qa-to-viewer-loop.md) 里的 QA-Gen loop，不走这条 SOP.
>
> 只读依赖: 对 `tianyi3unipat/BabyVision-v2-Datasets` 的 ModelScope 上传权限；SSH 到 `UniPat-Sing` (47.84.143.121, user `matianyi`)；能 push 到 `github.com/UniPat-AI/BabyVision-v2`.

---

## 全流程一张图

```
   ┌──────────────────┐
   │ 1. QA case 起草  │  一条 JSONL 记录 (task_id / question / answer_schema /
   │                  │   ground_truth / asset ref)
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 2. 整理素材       │  <domain>/qa/<family>.jsonl + <domain>/assets/*.glb|.nii.gz
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 3. 更新           │  viewer_filter.json (顶层 manifest: domains → assets[] + qa[])
   │   viewer_filter   │
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 4. 上传 ModelScope│  private repo tianyi3unipat/BabyVision-v2-Datasets
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 5. 同步 UniPat-Sing│  snapshot_download → .cache/modelscope-pull/BabyVision-v2-Datasets/
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 6. 更新本地索引   │  data/qagen_index.json (题目) + data/assets.index.json (资产)
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 7. Viewer 端连线  │  qagen.html 列表 / qa_item.html 详情页 (Caddy 直接吃磁盘)
   └────────┬─────────┘
            ▼
   ┌──────────────────┐
   │ 8. 拿到公开 URL   │  https://babyvision-v2.unipatai.com/qa_item.html?id=<task_id>
   └──────────────────┘
```

---

## 前置准备

| 环境变量 / 文件 | 用途 | 位置 |
|-----------------|------|------|
| `ModelScope_Access_Token` (`ms-…`, 39 chars) | ModelScope SDK 登录 | `~/.env` |
| `~/.modelscope/credentials/*` | 缓存的 SDK / git 凭据 | 由 `modelscope login --token "$TOK"` 生成 |
| SSH 别名 `UniPat-Sing` | ssh 到线上盒子 | 本地 Mac 的 `~/.ssh/config`；`HostName 47.84.143.121 User matianyi` |
| GitHub token `workflow` scope | 改 `.github/workflows/*.yml` 时才需要 | `gh auth refresh -s workflow` |
| 只读本地 mirror | fallback 阅读 | `~/BabyVision-v2/.cache/modelscope-pull/BabyVision-v2-Datasets/` |

Sanity check 一遍：

```bash
ssh UniPat-Sing '\
  test -s ~/.env && grep -q "^ModelScope_Access_Token=ms-" ~/.env && echo "token OK" && \
  test -f ~/.modelscope/credentials/git_token && echo "creds OK" && \
  test -d ~/BabyVision-v2/.cache/modelscope-pull/BabyVision-v2-Datasets && echo "mirror OK"'
```

看不到 `token OK` / `creds OK` 就跑一次 `modelscope login --token "$(grep '^ModelScope_Access_Token=' ~/.env | cut -d= -f2)"`。

---

## Step 1 — QA case 起草

一道 QA case 是 **一行 JSONL**，schema 对齐 `embodied/qa/genesis_vp.jsonl` 和 `medical/qa/medical_spatial.jsonl`：

```json
{
  "task_id": "<family>/<qid>",
  "domain":  "embodied | medical | ...",
  "question": "<自然语言题面>",
  "answer_schema": { "kind": "boolean | integer | numeric | numeric_range" },
  "ground_truth": {
    "type":   "deterministic_simulation | mcp_compute_volume | deterministic_topology | ...",
    "value":  <计算结果 or null>,
    "status": "computed | needs_rollout | needs_review",
    "repro":  { "<solver-specific keys>": "..." }
  },
  "asset": "<domain>/assets/<file>"
}
```

**约束**:
- `task_id` 全局唯一，形如 `medical_spatial/q003`；`family` 部分决定这题分到哪个 JSONL / 哪个 UI 分组
- `ground_truth.status` 三选一，值不确定时先填 `needs_rollout` 或 `needs_review`；能给答案的话填 `computed`
- `asset` 指向后面 Step 2 要放的资产路径

**准入 checklist** (Reviewer 复核)：
- [ ] 题面无歧义、无需 metadata 之外的世界知识
- [ ] `answer_schema.kind` 与 `ground_truth.value` 的类型一致（`boolean`→`true|false`，`numeric_range`→`[lo, hi]`，等）
- [ ] `ground_truth.repro` 里所有超参数固定 (`dt`, `substeps`, `rollout_steps`, `grid_density`, `deterministic: true` 等) —— **确定性**是 BV2 的核心承诺
- [ ] `asset` 对应的文件真的存在于同一次上传的资产集合里

---

## Step 2 — 整理素材 (assets + QA jsonl)

在**上传前**（本地 staging 目录 or 分支临时区）按 ModelScope 仓库结构组织文件：

```
staging/
├── embodied/
│   ├── assets/
│   │   ├── cut_dragon.glb
│   │   ├── franka_grasp.glb
│   │   ├── ipc_cloth.glb
│   │   ├── sand_wheel.glb
│   │   └── <your_new_asset>.glb              ← 新增
│   └── qa/
│       └── genesis_vp.jsonl                  ← 每 family 一份，追加或新建
├── medical/
│   ├── assets/
│   │   ├── image.nii.gz
│   │   ├── mask.nii.gz
│   │   └── <your_new_asset>.nii.gz
│   └── qa/
│       └── medical_spatial.jsonl
└── viewer_filter.json                        ← 见 Step 3
```

**资产格式约束**（当前 viewer 已支持的）：
- `glb` / `gltf` → Three.js (`gltf.html`)
- `nifti` (`.nii.gz`) → NiiVue (`niivue.html`)，可选一个 `*_mask.nii.gz` 作为 overlay 会自动叠加成红色分割
- 其它格式先加渲染器（见 [`new-viewer-page.md`](new-viewer-page.md)），再走本 SOP

**大小与预算**：单 asset 建议 < 20 MB。demo pack 目前 4×GLB + 2×NIfTI 共 ~25 MB，Caddy 前端 1h 公网 cache 打开。超过 100 MB 的重资产**不进 ModelScope**，走 `/nvme3/matianyi/sci3d-big/` 方案（见 [`../3d_assets_inventory.md`](../3d_assets_inventory.md)）。

**追加一条 QA 到已有 jsonl**：直接 `>>` 追加即可，不改老行：

```bash
python3 - <<'PY' >> staging/embodied/qa/genesis_vp.jsonl
import json
print(json.dumps({
  "task_id": "genesis_vp/q003",
  "domain":  "embodied",
  "question": "...",
  "answer_schema": {"kind": "boolean"},
  "ground_truth": {"type": "deterministic_simulation", "value": None, "status": "needs_rollout",
                   "repro": {"solver": "MPM Elastic", "dt": 0.004, "substeps": 10,
                             "rollout_steps": 200, "grid_density": 64, "deterministic": True}},
  "asset": "embodied/assets/<your_new_asset>.glb",
}, ensure_ascii=False))
PY
```

---

## Step 3 — 更新 `viewer_filter.json`

顶层 manifest 声明每个 domain 有哪些资产和哪些 QA. Schema 如下（`embodied` 段为例）：

```json
{
  "schema_version": "1.0",
  "domains": {
    "embodied": {
      "assets": [
        {
          "name":   "<人读标签>",
          "note":   "<一句话说明>",
          "path":   "embodied/assets/<file>",
          "viewer": "genesis | niivue",
          "format": "glb | nifti",
          "size":   <bytes>,
          "record": "<generator handle, 例如 genesis_sim / m3d_refseg>"
        }
      ],
      "qa": [
        {
          "task_id":     "<same as jsonl>",
          "question":    "<same as jsonl>",
          "answer_type": "boolean | numeric | numeric_range | integer",
          "gt_method":   "<same as ground_truth.type>",
          "qa_file":     "embodied/qa/<family>.jsonl",
          "asset":       "embodied/assets/<file>"
        }
      ]
    }
  }
}
```

Medical domain 多一个 `overlay_of` 字段，指该 mask 是哪张 image 的分割：

```json
{
  "path": "medical/assets/mask.nii.gz",
  "overlay_of": "medical/assets/image.nii.gz",
  ...
}
```

对每个新增题目 / 新增资产：**必须**在这里补一条 —— 上一步的 JSONL 是完整数据 (含 GT)，`viewer_filter.json` 是**面向前端**的裁剪版。

---

## Step 4 — 上传 ModelScope

```bash
# 从 staging/ 目录一次性推整个 dataset (create-or-update)
cd staging/
python3 <<'PY'
import os
from modelscope.hub.api import HubApi
api = HubApi()
api.login(open(os.path.expanduser('~/.env')).read().split('ModelScope_Access_Token=')[1].strip().split()[0])
api.push_dataset(
    dataset_id='tianyi3unipat/BabyVision-v2-Datasets',
    local_path='.',
    commit_message='<what changed>',
    revision='master',
)
PY
```

> 注意点（[`../../memory/modelscope-bv2-datasets`](../../memory) 里也有）:
> - 仓库是 **private** (`Visibility=3`)。匿名 `snapshot_download` / resolve URL 会拿到 `400 "username cannot be empty or null"`，**不是** dataset 元数据坏了，是没认证。
> - 有效 token 39 chars (`ms-<32-hex>`)。`~/.modelscope/credentials/session` 是**客户端 UUID**，不是 token，别 覆盖。
> - `push_dataset` 会保留已有文件；只想删某个文件用 `api.delete_dataset_files(...)`。

上传后 ModelScope 页面 (https://modelscope.cn/datasets/tianyi3unipat/BabyVision-v2-Datasets/files) 应该能看到新增文件；也可以立刻 `api.get_dataset_files(...)` 校对：

```bash
python3 <<'PY'
import os
from modelscope.hub.api import HubApi
api = HubApi(); api.login(open(os.path.expanduser('~/.env')).read().split('ModelScope_Access_Token=')[1].strip().split()[0])
for f in api.get_dataset_files('tianyi3unipat/BabyVision-v2-Datasets', revision='master'):
    if isinstance(f, dict) and 'qa/' in f.get('Path',''):
        print(f['Path'], f.get('Size'))
PY
```

---

## Step 5 — 同步到 UniPat-Sing

```bash
ssh UniPat-Sing '\
  cd ~/BabyVision-v2 && \
  python3 <<PY
import os
from modelscope.hub.snapshot_download import snapshot_download
from modelscope.hub.api import HubApi
api = HubApi()
api.login(open(os.path.expanduser("~/.env")).read().split("ModelScope_Access_Token=")[1].strip().split()[0])
p = snapshot_download(
    "tianyi3unipat/BabyVision-v2-Datasets",
    repo_type="dataset",
    revision="master",
    local_dir=os.path.expanduser("~/BabyVision-v2/.cache/modelscope-pull/BabyVision-v2-Datasets"),
)
print("synced to", p)
PY'
```

**Drift check** —— 逐文件 md5 对齐一次，确认没漏拉：

```bash
ssh UniPat-Sing '\
  cd ~/BabyVision-v2/.cache/modelscope-pull/BabyVision-v2-Datasets && \
  find . -type f ! -name ".msc" ! -name ".mv" -printf "%p %s\n" | sort'
```

预期输出和 Step 4 里 `get_dataset_files` 的字节数逐行一致。

---

## Step 6 — 更新本地索引

Caddy 直接服务磁盘上的 JSON，没有 build step，所以直接改这两个文件：

### 6a. `data/qagen_index.json` (题目)

对每条新增 QA 加一条：

```json
{
  "id": "genesis_vp/q003",
  "family": "genesis_vp",
  "asset_id": "embodied/genesis_vp/<matching_asset_id_in_assets.index.json>",
  "question": "<同 jsonl>",
  "answer_kind": "<同 answer_schema.kind>",
  "ground_truth_kind": "<同 ground_truth.type>",
  "domain": "embodied",
  "answer_schema": { "kind": "..." },
  "ground_truth": { "...原样从 jsonl 复制..." },
  "asset_file": "embodied/assets/<file>"
}
```

一键从 ModelScope 拉下的 JSONL 派生 (代替手写)：

```bash
python3 <<'PY'
import json
from pathlib import Path
repo = Path.home() / 'BabyVision-v2'
cache = repo / '.cache/modelscope-pull/BabyVision-v2-Datasets'
by_id = {}
for f in cache.rglob('qa/*.jsonl'):
    family = f.stem
    for line in f.read_text().splitlines():
        if not line.strip(): continue
        o = json.loads(line)
        by_id[o['task_id']] = {
            'id': o['task_id'],
            'family': family,
            'asset_id': None,   # ← Step 6b 会填
            'question': o['question'],
            'answer_kind': o['answer_schema']['kind'],
            'ground_truth_kind': o['ground_truth']['type'],
            'domain': o.get('domain'),
            'answer_schema': o.get('answer_schema'),
            'ground_truth': o.get('ground_truth'),
            'asset_file': o.get('asset'),
        }
# TODO: merge into data/qagen_index.json; asset_id needs manual mapping
print(json.dumps(list(by_id.values()), indent=2, ensure_ascii=False))
PY
```

### 6b. `data/assets.index.json` (资产)

对每个新增 asset 加一条 (`id` 是 viewer 内部 handle，跟 `qagen_index.json` 里的 `asset_id` 完全对应)：

```json
{
  "id": "embodied/genesis_vp/<name>",
  "domain": "embodied",
  "family": "genesis_vp",
  "label": "<display name>",
  "format": "glb",
  "path": "viewer/datasets/genesis/<file>.glb"
}
```

**注意**: `path` 字段指的是 **viewer 端拿得到该文件的路径**，而不是 ModelScope 上的 layout. 当前约定：
- Embodied GLBs 存 `viewer/datasets/genesis/`（Caddy 直服务）
- Medical NIfTI 存 `examples/02_medical_spatial_gt/data/`（也直服务）

同步 md5：

```bash
cd ~/BabyVision-v2
md5sum viewer/datasets/genesis/<file>.glb \
       .cache/modelscope-pull/BabyVision-v2-Datasets/embodied/assets/<file>.glb
# 两行 md5 必须相同
```

如果不同 → 从 mirror 拷一份到 viewer 路径 (`cp` 或做 symlink)：

```bash
cp .cache/modelscope-pull/BabyVision-v2-Datasets/embodied/assets/<file>.glb \
   viewer/datasets/genesis/<file>.glb
```

---

## Step 7 — Viewer 端连线（无需改代码）

`viewer/qagen.html` 和 `viewer/qa_item.html` 都会**动态**读上一步两个 JSON、按 `asset_id` join、然后调 `assetViewerURL()` 算出 `gltf.html?file=…` / `niivue.html?file=…&overlay=…`. 只要 Step 6 两个 index 是 consistent 的，就不需要动 HTML.

想触发 viewer 端立刻可见（Caddy 不需要 reload；浏览器可能有 1h edge cache）：

```bash
# 服务器端强制文件 mtime 更新 (让 If-Modified-Since 失效)
ssh UniPat-Sing 'touch ~/BabyVision-v2/data/qagen_index.json ~/BabyVision-v2/data/assets.index.json'
```

自查这一步的连线：

```bash
# 4/4 QA URL 都应该 HTTP 200
BASE=https://babyvision-v2.unipatai.com
for id in $(curl -sS $BASE/data/qagen_index.json | python3 -c 'import json,sys; [print(q["id"]) for q in json.load(sys.stdin)["questions"]]'); do
  code=$(curl -sSo /dev/null -w '%{http_code}' "$BASE/qa_item.html?id=$id")
  echo "$code  $BASE/qa_item.html?id=$id"
done
```

想深一层验证（浏览器实际能加载 asset + overlay）：跑 `/tmp/qatest/simulate.mjs`（Node + `fetch`，模拟 qagen.html JS 生成的 href 并 HEAD 每一个），2026-07-03 已入模板。

---

## Step 8 — 拿到公开 URL

按 SOP 走完，每条 QA 的公开 URL 是：

```
https://babyvision-v2.unipatai.com/qa_item.html?id=<task_id>
```

例：
- `genesis_vp/q001` → https://babyvision-v2.unipatai.com/qa_item.html?id=genesis_vp/q001
- `medical_spatial/q002` → https://babyvision-v2.unipatai.com/qa_item.html?id=medical_spatial/q002

QA 列表页仍在 https://babyvision-v2.unipatai.com/qagen.html — 每行点开会跳到对应 `qa_item.html`.

---

## 提交回代码库

如果 Step 6 改了 `data/qagen_index.json` / `data/assets.index.json`（或 Step 2 顺便更新了 `viewer/*` 里的资产 layout），跑标准 PR 流程：

```bash
git switch -c feat/qa-<slug> origin/main
git add data/qagen_index.json data/assets.index.json <asset files if any>
git commit -m "feat(qa): add <family>/<qid> — <one-line why>"
git push -u origin feat/qa-<slug>
gh pr create --fill
```

**关键**: **千万不要把 GLB / NIfTI 二进制丢进 git**（`.gitignore` 已排除 `viewer/datasets/genesis/*.glb` / `examples/**/*.nii.gz`）。资产的**权威**副本在 ModelScope；本地只是 mirror.

---

## 完整验收 checklist

- [ ] JSONL 一行 QA 通过 schema 校对（Step 1）
- [ ] `viewer_filter.json` 新增了对应 asset + qa 条目（Step 3）
- [ ] `api.get_dataset_files(...)` 上能看到新文件（Step 4）
- [ ] `snapshot_download` 拉下新文件到 UniPat-Sing 的 `.cache/modelscope-pull/`（Step 5）
- [ ] `data/qagen_index.json` + `data/assets.index.json` 的 `asset_id` join 通（Step 6）
- [ ] md5 校对: `viewer/datasets/**/*` 与 `.cache/modelscope-pull/**/*` 字节相同（Step 6b）
- [ ] `curl -sSI https://babyvision-v2.unipatai.com/qa_item.html?id=<task_id>` 返回 200（Step 7）
- [ ] 浏览器打开 `qa_item.html?id=<task_id>` 看到题面 + 3D viewer 里能看到 asset（Step 8）
- [ ] 相关 PR 已开 / 已 merge（可选）

---

## 常见坑

| 症状 | 排查 |
|------|------|
| `400 "username cannot be empty or null"` | Token 没设或已过期。跑 `modelscope login --token "$TOK"` 再试 |
| `HTTP 404` for `/data/qagen_index.json` | Caddy `@hidden path_regexp ^/(\.\|deploy(\$\|/))` 把点起头的路径全 404。别把 `data/` 命名成 `.data/` |
| `qa_item.html` 显示 "Question not found" | `data/qagen_index.json` 里没这条 id，或者 id 大小写 / 前后空格不一致 |
| 3D viewer 空白但没报错 | 90% 是 asset bbox 纵向 elongated（e.g. `sand_wheel.glb` 沙粒瀑布把 bbox 拉长），camera auto-fit 把主体缩小到 1/5 屏。UI 上已经给了大号 "🎯 Open interactive 3D viewer" 按钮引导用户开全屏 |
| `git push` 被 GitHub 拒 `refusing to allow an OAuth App to create or update workflow` | Token 没 `workflow` scope。要么 `gh auth refresh -s workflow`，要么把 `.github/workflows/*.yml` 从 commit 里剔掉 |
| 上传后 viewer 没变化 | Caddy 直服务磁盘，检查 (a) 你改的确是 `~/BabyVision-v2/` 下的文件 (b) `data/qagen_index.json` 里 JSON 合法 (c) 浏览器 disable cache 硬刷一次 |

---

## 参考

- [`qa-to-viewer-loop.md`](qa-to-viewer-loop.md) — QA-Gen 批量 loop（本 SOP 处理单题；批量走 QA-Gen）
- [`new-viewer-page.md`](new-viewer-page.md) — 新增一种资产格式（非 glb/nifti）时需要的 viewer 侧改动
- [`new-domain-template.md`](new-domain-template.md) — 新增 domain（非 embodied/medical）时的完整 checklist
- [`../3d_assets_inventory.md`](../3d_assets_inventory.md) — 大资产（不进 ModelScope）的存放约定
- [`../../deploy/README.md`](../../deploy/README.md) — Caddy 部署与安全模型
