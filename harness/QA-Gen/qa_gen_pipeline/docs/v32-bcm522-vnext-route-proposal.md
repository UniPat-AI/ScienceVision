# V32 BCM522 60 题 vNext 路由草案

状态：`draft_unapproved`。本文和两份 proposal 都不是运行配置，不代表领域专家、
viewer owner 或 release owner 已批准。

## 1. 目的

冻结选择包含 60 个 source slot，但它们使用 28 个历史微标签。vNext 不应把这 28 个
标签原样变成 28 个 leaf。当前草案把它们收敛到 4 个已有 canonical leaf，并路由到 1 个
已经创建、但仍处于 `blocked` / `not_approved` 状态的医学 leaf：
`medical.volumetric_target_geometry`。

逐题机器可读映射在
`inputs/proposals/V32_BCM522_VNEXT_ROUTE_PROPOSAL.draft.json`，知识范围和
review 要求在
`inputs/proposals/V32_BCM522_VNEXT_KNOWLEDGE_PROPOSAL.draft.json`。

## 2. 输入锁

| 输入 | SHA-256 |
|---|---|
| selection identity | `149b29605da6cd2862e427dacb4d1098b106d23eb95b248ffdf6dc0bdf1846d4` |
| selection 文件 | `75ae4cbb771d42ff68811b2e06a82ca34cb00749f888673518dac937336463d6` |
| bundle `MANIFEST.json` | `656e1c2d3192c7a74477367a461a717767e90b1424e6e6a8055e77b5992fa71a` |
| `question_assets.jsonl` | `e093b1f92ebc5ecc9d9a79508db7be9712104251d69bc01859220fd1dcaefeb1` |
| 私有 `answer_keys.jsonl` | `6755d27b19fc17057094f182e3087052f0fcb98c1d291da531fd97dd6793c56b` |

三域 source corpus 和 physical-asset 索引的 digest 也固定在两份 proposal 中。
任何输入 digest 变化都必须重新生成并重新 review proposal。

## 3. 路由结果

| Target leaf | 类型 | Slot | 策略 |
|---|---:|---:|---|
| `biology.molecular_collision_check` | 已有 | 10 | 全部 `reauthor_on_asset` |
| `biology.docking_pose_plausibility` | 已有 | 10 | 全部 `reauthor_on_asset` |
| `chemistry.steric_accessibility` | 已有 | 8 | 7 reauthor + 1 preserve |
| `chemistry.conformation_analysis` | 已有 | 12 | 11 reauthor + 1 preserve |
| `medical.volumetric_target_geometry` | 已创建，`blocked` / `not_approved` | 20 | 13 reauthor + 7 preserve |

总计 60：`preserve_intent` 9，`reauthor_on_asset` 51；复用已有 leaf 40，路由到已创建但
尚未批准的医学 leaf 20。所有 slot 的 `old_truth_reusable` 都是 `false`。

### `preserve_intent`

只用于原本已经是 `viewer_measurement` 的 9 个 slot。保留科学动作和 answer shape，
但仍需重新生成题干、匿名 scene binding、viewer evidence 和 reference truth。旧答案
不能直接进入新 candidate。

### `reauthor_on_asset`

用于原本依赖 `raw_compute` 的 51 个 slot。仅保留 asset class 和宽泛科学主题，重新
设计可由受控 viewer 观察或测量的问题。不得把旧 compute recipe 换一种措辞交给
solver。

## 4. 为什么医学需要一个新 leaf

这 20 个 source slot 都围绕已声明 segmentation target 的 bbox、extent、centroid、
laterality 或代表性 slice。它们不是解剖结构识别；mask 也不保证是 lesion；它们更
不是影像征象描述。因此：

- `medical.anatomical_region` 语义不匹配；
- `medical.lesion_localization` 会引入未经证实的 lesion 假设；
- `medical.imaging_feature_assessment` 不覆盖 segmentation geometry 合同。

建议只使用一个 `medical.volumetric_target_geometry`，同时覆盖 CT 和 MRI，不再按数据集、
器官或单项测量拆 leaf。该 leaf 的配置现已创建，但它仍是 `blocked` / `not_approved`：
医学 release block、expert approval 和映射批准均未解除。

## 5. Agent 输入边界

`BriefBuilder` 和 `Author` 可以获得：匿名 scene ref、asset digest/type、target leaf、
viewer capability、知识快照、无答案的 answer shape，以及净化后的 source scientific
action。

它们不得获得：旧 `reference_answer`、旧 rubric、`solving_approach`、oracle receipt、
compute contract、selection hints、raw/upstream path、PDB accession、case/subject id。

`TruthBuilder` 必须基于新的 CandidatePlan 和受控 host asset handle 产生新的 truth 与
evidence。旧 answer key 只能在隔离的回归比较中使用。独立 `Verifier` 不应看到旧 answer
key，以免把历史答案当成正确性证据。

## 6. 仍然 fail-closed 的条件

这份草案不能直接用于 generation。至少还需要：

1. 领域专家批准每个 leaf 的知识范围和 valid/invalid question patterns；
2. 为 60 个资产建立匿名 scene registry；
3. 对五组 viewer capability 形成可验证的 session/evidence attestation；
4. 为已创建的医学 leaf 完成 schema 校验、修复后的 expert seed 和医学审批；
5. 由 planner 生成新 CandidatePlan，并由 TruthBuilder 对每题重建 truth；
6. verifier、human review 和 release gate 全部独立通过。

## 7. Review 决策点

- biology 的 deposited assemblies 是否具备足够证据被视为 docking pose；没有证据时，
  对应 slot 应回落到 collision leaf 或 blocked。
- chemistry binding-context 资产是否能支持真正的 steric accessibility 问题，而不是只做
  距离题。
- Niivue 是否能在不暴露 NIfTI bytes 的前提下原生提供 bbox、extent、centroid 和
  representative-slice evidence。
- 是否接受将这 20 个 slot 映射到已创建但仍 `blocked` / `not_approved` 的
  `medical.volumetric_target_geometry`；不接受时，它们应保持 blocked，而不是强行映射到
  不匹配 leaf。
