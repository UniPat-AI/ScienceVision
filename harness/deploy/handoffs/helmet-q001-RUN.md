# babyvision-helmet-handoff — industrial/helmet_caliber/q001

把 `industrial/helmet_caliber/q001`（DamagedHelmet 口径透视陷阱题）上线到
https://babyvision-v2.unipatai.com/qa_item.html?id=helmet_caliber/q001 的交接包。

## 已完成（H20 侧）
- ModelScope 数据集 `tianyi3unipat/BabyVision-v2-Datasets` 已推送 GLB + qa jsonl + viewer_filter.json industrial 段。
- GitHub PR #63（`feat/qa-helmet-caliber-q001`）**已 squash 合并进 main**，merge commit `4b6b8fce`（2026-07-08）。仅改 `data/qagen_index.json` + `data/assets.index.json`。

## ✅ 全部完成（2026-07-09）URL 已 LIVE
- index 已在 sing 上生效（`data/qagen_index.json` / `data/assets.index.json` 含该 id，均=1）。
- GLB 已就位：`~/BabyVision-v2/viewer/datasets/industrial/DamagedHelmet.glb`
  （源直接用 checkout 内已有的 `crawl_data/industrial/khronos_gltf/DamagedHelmet.glb`，md5 `a3af6ad5…` / 3773916 bytes 对齐，未走 snapshot_download）。
- 验收 4 项全绿：qagen_index=1、assets_index=1、qa_item.html=200、GLB=200/3773916。

目标 URL 已 live：https://babyvision-v2.unipatai.com/qa_item.html?id=helmet_caliber/q001

> 备注：sing = `47.84.143.121`（User matianyi），其 hostname 与本 H20 相同。
> 本机现已配置到 sing 的免密 SSH（`~/.ssh/config` alias `UniPat-Sing`/`sing`，key `~/.ssh/id_ed25519_sing`）。
> Caddy docroot = sing 上的 `~/BabyVision-v2/`（`viewer/...` 即静态服务路径）。

## 文件
- `SERVER_STEPS.md` — 精确可复制清单（Part A/B/C，含期望输出与 md5）。
- `pr63.diff` — PR #63 完整 diff，即两条 index 条目的确切内容。

## 关键值
- GLB md5 `a3af6ad5a8329f22ba08b7f16e4a97d8`，size `3773916` bytes。
- 服务路径 `viewer/datasets/industrial/DamagedHelmet.glb`；mirror 源 `industrial/assets/DamagedHelmet.glb`。
