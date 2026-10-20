# Asset Picker SOP — `viewer/selection.json` 作为 viewer 数据子集的契约

**Date**: 2026-06-22
**Status**: Design / SOP
**Target repo**: ScienceVision（BabyVision-v2 仓里只放此文档；实施在上游 ScienceVision）
**Author audience**: 实操者（在 ScienceVision 服务器上按本 SOP 配 picker + 维护 `selection.json`）

---

## § 1. Purpose & Scope

### 目的
从 viewer 当前 ~232 项 3D assets 里人工挑出 **20-30 个代表 case** 作为展示子集，让主页变成可控的精选视图；挑选工具长期保留，便于后续维护。

### 范围
- 仅适用于 `assets.json` / `assets.index.json` + `assets/{domain}.json` 体系
- 不修改 manifest 生成器
- 不引入任何后端

### 非目标
- 不覆盖 `datasets_assets.json`、`negative_assets.json`、`synthetic_negative_assets.json`、`qagen_index.json`（可后续按同模式扩展）
- 不替代首页既存的 "Featured cases" hero 区（hero 与本 SOP 的过滤独立维护）
- 不做自动选择 / 不做"smart pick"

### 挑选原则（操作者遵循）
- 每个 domain 选 **1-2 个** 代表 case
- 不同 format 至少覆盖 **1 个**
- 总数控制在 **20-30 个**

---

## § 2. `selection.json` 契约（CRITICAL）

### 位置
`viewer/selection.json` —— 与 `assets.index.json` 并排，跟 viewer 同源加载

### Schema (version 1)
```json
{
  "version": 1,
  "generated_at": "2026-06-22T20:30:00",
  "stats": {
    "total": 24,
    "domains_covered": 11,
    "formats_covered": 18
  },
  "selected": [
    "/crawl_data/biology/rcsb_pdb_mmcif/1A3N.cif",
    "/crawl_data/medical/IXI/IXI002-Guys-0828-T1.nii.gz",
    "..."
  ]
}
```

### 字段说明
| 字段 | 类型 | 含义 |
|---|---|---|
| `version` | int | 当前固定 `1`；新增向后兼容字段可不动 version，破坏性 schema 改动必须 `+1` 并在本 SOP 同步标注 |
| `generated_at` | ISO8601 string | picker 导出时刻；用于 review 时判断"是否过期" |
| `stats.total` | int | `selected.length`；写入时由 picker 自动计算 |
| `stats.domains_covered` | int | 选集覆盖到的 domain 数（去重） |
| `stats.formats_covered` | int | 选集覆盖到的 format 数（去重） |
| `selected` | array of string | 每个 item 的 `path` 字段值；path 是 assets manifest 中的唯一键 |

### 为什么用 path 而非 name
- `name` 在 manifest 内可能重复（不同 domain 同名）；`path` 全局唯一
- `path` 与 manifest 的对账逻辑天然一致：`new Set(selected).has(item.path)`
- 文件改名/移动时 path 会变 → 这正是我们想要的"显式失效"信号（见 § 7 orphan 处理）

### 文件格式
- UTF-8，`JSON.stringify(obj, null, 2)`，**保证 git diff 可读**
- 永远以 `selected` 数组排序为准（按字符串序），让 diff 稳定

---

## § 3. Viewer 加载与过滤行为

### 启动加载顺序
1. `fetch('/viewer/selection.json')`
2. 进入 fallback 决策（见下表）
3. 后续每个 domain shard `fetch` 完成后，在调用现有 `render(domain, items)` **之前**，按 selection 过滤 `items`

### Fallback 决策
| `selection.json` 状态 | 行为 | 顶栏指示 |
|---|---|---|
| HTTP 404 / 文件不存在 | 全量渲染，等同当前 viewer | `showing: full (no selection)` |
| 存在但 `selected` 为空 / 不是数组 / JSON 解析失败 | 仍全量渲染 | `showing: full · ⚠ selection invalid` |
| 存在且 `selected.length >= 1` | 过滤渲染：每个 domain 只保留 `selected` 集合内的 item | `showing: 24 selected · 11 domains · 18 formats` |

**原则：永远不渲染空页面**。哪怕配置错也回退到全量 + 顶栏告警，避免"看起来网站坏了"。

**顶栏数字的来源**：`24 selected · 11 domains · 18 formats` 直接读 `selection.json` 的 `stats`（写入时即固化的事实）；如果实际渲染时检出 orphan path（见 § 7），顶栏在选中数字后追加 `(K orphans)` 注解，但不重算 stats —— stats 是导出时刻的快照，orphan 注解是运行时的对账提示，两者职责分离。

### Hook 点
- 唯一改动落在 viewer 的 fetch-then-render 链路：在 `assets.index.json` 加载之前 fetch 一次 `selection.json`，把结果以 `Set<path>` 挂到全局；每个 shard 的 render 入口加一行 filter
- **不动** card 模板、不动 domain section 结构、不动既有 viewer 路由

### Featured cases hero
- 不受 selection 影响；保持现状
- 后续如果想让 hero 也跟 selection 联动，**单独**改造，不在本 SOP 范围

---

## § 4. Picker UI（`?pick=1`）

### 触发
访问 `…/viewer/index.html?pick=1` 进入 picker 模式；不带参数时 picker 代码完全不挂载，主页 DOM/CSS 零变化。

### Pick 模式下的 UI 变化
1. **每张 asset card 右上角**：复选框；勾选 ↔ path 加入/移出当前选集
2. **底部 sticky toolbar**（pick 模式专属，固定在 viewport 底部）：

```
┌──────────────────────────────────────────────────────────────────┐
│ 24 / 30 selected   Domains: 11/11 ✓   Formats: 18/33             │
│ uncovered formats: step, nrrd, iges, ply, ...                    │
│ [Save to disk]  [Download .json]  [Clear all]  [Exit pick mode]  │
└──────────────────────────────────────────────────────────────────┘
```

3. **计数染色**（一眼看出是否在目标区间）：
   - `< 20` → 黄（还没够）
   - `20–30` → 绿（合格）
   - `> 30` → 橙（超了）

4. **覆盖度 chips**：
   - `Domains: N/M ✓` — 每个 domain ≥ 1 个就算覆盖
   - `Formats: N/M` — 列出未覆盖的 format（点开可展开完整列表）

5. **本地状态持久化**：勾选状态写入 `localStorage`（key: `viewer.selection.draft`），刷新不丢；只有"导出"成功 + 用户主动 `Clear all` 才会清

---

## § 5. 持久化：Download + File System Access API

### 主路径 — File System Access API（一键覆盖）
- 第一次点 `Save to disk`：浏览器弹窗，用户选 `viewer/selection.json` 作为目标文件
- handle 存入 IndexedDB（key: `selection.handle`），后续点击直接覆盖、不再弹窗
- 浏览器关闭后 handle 依然在；只有用户清浏览器存储 / 换设备时丢失，丢了重新选一次即可
- **限制**：仅 Chrome / Edge 等 Chromium 系；必须在 `http(s)://`（含 `localhost`），`file://` 下不可用
- 不支持时 `Save to disk` 按钮 disable + tooltip 提示原因

### 兜底 — Browser Download（跨浏览器）
- 点 `Download .json` → 浏览器下载 `selection.json` 到默认下载目录
- 用户手动把文件移到 `viewer/selection.json`（覆盖）
- 任何浏览器都能用（Safari、Firefox、`file://`、远程服务器）

### 导出前校验
- `selected.length >= 1`（空选集不允许导出）
- `version`、`generated_at`、`stats` 三个字段自动填充
- `selected` 数组在写出前按字符串序排序

---

## § 6. 操作流程（SOP 本体）

### Step 1 — 起 viewer 本地服务
确保你能从浏览器访问 viewer（推荐 `http://localhost:<port>/viewer/index.html`，不要用 `file://` 否则 FSA 不可用）。

### Step 2 — 进入 pick mode
访问 `…/viewer/index.html?pick=1`。确认页面右上角进入 picker 状态、底部出现 sticky toolbar。

### Step 3 — 挑选
- 按 domain 逐一浏览，每个 domain 勾 **1-2 个**有代表性的 case
- 时不时瞥底部 toolbar：
  - 总数：盯着染色，瞄准 **20-30**
  - Domains：必须 **11/11 ✓**
  - Formats：尽量补到 **关键 viewer 都覆盖**（molstar / niivue / gltf / occt / urdf / pointcloud / field3d / bandplot / saturn 等）
- 不必死磕"所有 format 都覆盖"（有些 format 在某 domain 内只有 1 例，硬选可能不是好代表）；按"viewer 模板"覆盖更实用

### Step 4 — 导出
- 优先点 `Save to disk`（FSA 一键覆盖 `viewer/selection.json`）
- 若 FSA 不可用，点 `Download .json` → 手动 `mv ~/Downloads/selection.json viewer/selection.json`

### Step 5 — 验证
1. 去掉 `?pick=1` 刷新主页
2. 确认顶栏显示 `showing: N selected · …`
3. 滚一遍每个 domain section，验证只剩选中那几个
4. `git diff viewer/selection.json` 看文件 sane（行数、stats、selected 路径无异常）

### Step 6 — 提交
- `git add viewer/selection.json`
- 提交信息建议格式：`chore(viewer): refresh selection.json (N items, M domains, K formats)`

---

## § 7. 验收清单 & 边界情况

### 验收清单
- [ ] `selection.json` 不存在时 viewer = 旧版全量行为，无报错
- [ ] `selection.json` 为 `selected: []` 或解析失败时仍 fallback 渲染全量，顶栏告警
- [ ] `?pick=1` 离开后无任何残留 UI / DOM / CSS / event listener
- [ ] FSA 路径在 Chrome 上能一键覆盖；Download 路径在 Safari/Firefox 能下载
- [ ] `selection.json` 写出后 `git diff` 行数稳定（受惠于 path 排序 + 缩进）
- [ ] 顶栏状态指示文案准确（数字与实际过滤后渲染条数一致）

### 边界情况
| 情况 | 处理 |
|---|---|
| **Orphan path**：`selection.json` 里的 path 在当前 manifest 找不到 | 静默丢弃 + `console.warn` 列出 orphan path；顶栏数字后加 `(K orphans)` |
| **Manifest drift**：重新爬数据后 path 改名 | 不自动 fix；orphan 检测会暴露问题；按需重跑 SOP |
| **localStorage 与 selection.json 不一致**（已 export 过但又手动改了 JSON） | pick 模式启动时若 localStorage draft 非空但与远端 selection.json 不等，顶栏给一个 `Load from selection.json` 链接，由用户决定要不要覆盖本地 draft |
| **FSA 权限被撤销** | 重新弹窗选文件；若用户拒绝则回退到 Download |
| **空选集尝试导出** | 导出按钮 disabled；toolbar 提示 "select at least 1 item" |

---

## § 8. 实施提示（非必须）

把改动尽量收敛到 viewer 现有的几个 hook 点，不要扩散：

1. **加载入口**：在 `fetch('/viewer/assets.index.json')` 之前加一次 `fetch('/viewer/selection.json')`，结果挂到一个模块级 `let selectionSet = null` 或 `new Set()`
2. **过滤切入**：现有 `fetchAndRenderDomain` 调 `render(domain, items)` 之前，如果 `selectionSet` 非空则 `items = items.filter(it => selectionSet.has(it.path))`
3. **Picker 模式**：顶层判断 `URLSearchParams(location.search).get('pick') === '1'`，是则动态注入 picker JS + CSS（checkbox 插入到每个 card、底部 toolbar），否则什么都不做
4. **FSA handle 持久化**：浏览器原生 `FileSystemFileHandle` 可以直接存进 IndexedDB；调用前用 `queryPermission` / `requestPermission` 走一遍权限确认

具体怎么写代码由实施者决定，本 SOP 不做硬性规定。

---

## Appendix A — 名词对照

| 名词 | 含义 |
|---|---|
| picker | 进入 `?pick=1` 后挂载的选择工具（复选框 + 底部 toolbar） |
| selection | 当前选集，运行时挂 `localStorage` + 全局 `Set` |
| `selection.json` | 持久化的选集文件，本 SOP 的核心契约 |
| filter mode | 主页（不带 `?pick=1`）根据 `selection.json` 过滤后的渲染状态 |
| fallback | `selection.json` 缺失或无效时回退到全量渲染 |
| FSA | File System Access API（浏览器原生文件读写） |
| orphan path | `selection.json` 中存在但当前 manifest 找不到的 path |

## Appendix B — 后续可扩展点（YAGNI，不在本 SOP 范围）

- 把 picker 推广到 `datasets_assets.json` 等其他 manifest（每个 manifest 自己一份 `selection.json`）
- selection 内 per-item `notes` 字段（给每个 case 加一句说明）
- selection 内 per-item `priority` 字段，让 viewer 按重要性排序
- 多份 selection 切换（`selection.demo.json`、`selection.paper.json` 等，URL 参数 `?selection=demo` 切换）
- 自动覆盖度报告：CI 任务在 selection.json 改动时输出 domain / format / viewer 覆盖度对比
