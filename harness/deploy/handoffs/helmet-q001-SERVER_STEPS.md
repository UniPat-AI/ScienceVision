# SERVER_STEPS — industrial/helmet_caliber/q001 上线清单

目标 URL（跑完 Part B 后 live）：
https://babyvision-v2.unipatai.com/qa_item.html?id=helmet_caliber/q001

> 现状（2026-07-07）：该 URL 仍返回 "Question not found"，因为服务器上的
> `data/qagen_index.json` 还没有这条 id、mirror 里也还没有 GLB。执行下面的步骤后才会 live。

关键事实（复制/校对用）：

| 项 | 值 |
|---|---|
| 仓库 | `UniPat-AI/BabyVision-v2` |
| PR | #63，分支 `feat/qa-helmet-caliber-q001`，squash 合并 |
| QA id | `helmet_caliber/q001`（domain=industrial，answer=integer，ground_truth.value=1） |
| asset id | `industrial/helmet_caliber/damaged_helmet` |
| ModelScope 数据集 | `tianyi3unipat/BabyVision-v2-Datasets`（私有，Visibility=3） |
| GLB — 源（mirror） | `industrial/assets/DamagedHelmet.glb` |
| GLB — 服务路径 | `viewer/datasets/industrial/DamagedHelmet.glb` |
| GLB md5 | `a3af6ad5a8329f22ba08b7f16e4a97d8` |
| GLB size | `3773916` bytes (~3.6 MiB) |

---

## Part A — 合并 PR #63 ✅ 已完成（2026-07-08）

PR #63 已 squash 合并进 `main`，merge commit `4b6b8fcebcc8d4ee830555de6bbfcc3576ee51b4`
（state=MERGED，mergedAt=2026-07-08T00:10:03Z）。只改了两个 index 文件：
`data/qagen_index.json`、`data/assets.index.json`。

**剩下只有 Part B（sing 服务器）+ Part C（验收）。**

---

## Part B — 只能在 sing 服务器做（H20 无 SSH 权限）

SSH 进 sing，进入部署 checkout（下面假设 `~/BabyVision-v2`，按实际路径改）。

### B1. 拿到合并后的 index 改动

优先直接 pull（若该 checkout 跟 main）：

```bash
cd ~/BabyVision-v2
git fetch origin
git pull --ff-only origin main
```

若该 checkout 不跟 main / 不想动其它文件，只精确取这两个文件（更稳，推荐）：

```bash
cd ~/BabyVision-v2
git fetch origin
git checkout origin/main -- data/qagen_index.json data/assets.index.json
```

校验 index 已含新条目：

```bash
grep -c "helmet_caliber/q001" data/qagen_index.json          # 期望 1
grep -c "industrial/helmet_caliber/damaged_helmet" data/assets.index.json   # 期望 1
```

### B2. 刷新 ModelScope mirror，取回新 GLB

用现有的 mirror 刷新方式（snapshot_download）把数据集拉到本地 mirror。按你们现有脚本/路径执行；通用形式：

```bash
python - <<'PY'
from modelscope import snapshot_download
p = snapshot_download('tianyi3unipat/BabyVision-v2-Datasets', repo_type='dataset')
print('mirror at:', p)
PY
```

> 私有集需已配置 MODELSCOPE token（见 ~/.modelscope 或环境变量）。
> 拉下来后 mirror 内应存在 `industrial/assets/DamagedHelmet.glb`。

### B3. 把 GLB 放到服务目录并校验 md5

```bash
mkdir -p viewer/datasets/industrial
cp "<MIRROR_PATH>/industrial/assets/DamagedHelmet.glb" viewer/datasets/industrial/DamagedHelmet.glb
md5sum viewer/datasets/industrial/DamagedHelmet.glb
# 期望：a3af6ad5a8329f22ba08b7f16e4a97d8
```

（把 `<MIRROR_PATH>` 换成 B2 打印的 mirror 路径。）

### B4. 触发 index 重载

```bash
touch data/qagen_index.json data/assets.index.json
```

（若服务用别的重载机制——重启进程 / 发信号——按现有 SOP 来；touch 是常见做法。）

---

## Part C — 验收（sing 上或任意能访问站点的机器）

```bash
BASE=https://babyvision-v2.unipatai.com

# 1) index 已含该 id
curl -s "$BASE/data/qagen_index.json" | grep -c "helmet_caliber/q001"      # 期望 1

# 2) 页面 200
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/qa_item.html?id=helmet_caliber/q001"   # 期望 200

# 3) GLB 200 且大小对得上
curl -s -o /dev/null -w "code=%{http_code} bytes=%{size_download}\n" \
  "$BASE/viewer/datasets/industrial/DamagedHelmet.glb"    # 期望 code=200 bytes=3773916
```

三项全绿后，目标 URL live：
https://babyvision-v2.unipatai.com/qa_item.html?id=helmet_caliber/q001

---

## 附：确切 index 条目

完整两段 diff 见同目录 `pr63.diff`。合并 PR 即等价于把这两条对象追加进对应数组。
