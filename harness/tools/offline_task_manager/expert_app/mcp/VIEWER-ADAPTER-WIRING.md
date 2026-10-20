# Viewer MCP adapter wiring

Expert `mcp_only` tools talk to a third-party viewer (Mol* 4.5, NiiVue 0.45, BIM)
that is **not vendored in this repo**. The adapter can look correct in tests and
still be a no-op on the expert page. This file is the checklist for the next
tool, not a catalogue of Mol* APIs.

## Iron rule

The Playwright harness implements the **vendor** signature, not the adapter's
convenience shape.

If the mock accepts an argument Mol* would throw on, the test stays green and
the expert page reports 执行成功 with no visual change.

How to pin one call:

1. Open the vendor source (for Mol* 4.5,
   `src/mol-plugin-state/manager/structure/*.ts`).
2. Copy the TypeScript signature into a comment on **both** the harness mock
   and the adapter call. Name the file.
3. Make the mock `TypeError` on the wrong shape (non-array where an array is
   required, object where a registry string is required).
4. Watch the adapter test fail on that TypeError, then write the adapter.
5. `await` any Promise the vendor returns. An un-awaited rejection is a silent
   success.
6. Do not `try/catch` a mutation and ignore it. Fail closed; the panel already
   has a red path.

Reading state is the same rule: walk the live tree the vendor actually hangs
nodes on (`structures[i].components`, not `hierarchy.current.components`).

## Paid-for misses

| Expert symptom | Adapter guessed | Vendor / contract actually is |
| --- | --- | --- |
| 组分面板空、「没有 polymer 组件」 | `hierarchy.current.components` | `hierarchy.current.structures[i].components` (Mol* 4.5 auto-preset) |
| 「聚合物球棍」闪一下、画面仍是 cartoon、高亮弹回 cartoon | `addRepresentation(ref, {type:{name}})` plus swallowed `removeRepresentations(ref)` | `removeRepresentations(ComponentRef[])` then `addRepresentation(ComponentRef[], 'ball-and-stick')` |
| 新按钮本地成功、上传 400 `not in the effective tool policy` | 面板读 live registry，draft 冻第一次 initialize 的名单 | 进行中 draft 在 registry hash 变新且已记录工具名仍在时 adopt live 名单 |
| 切对称后相机跳、或 `applyPreset` 成功但画面没变 | 信 `applyPreset` 的 resolve / `camera.setState` | `applyPreset` 的 transaction 失败会吞掉；事后读 `derivedSymmetryPreset()`，取景走 `requestCameraReset` 等 commit |

NiiVue 侧已经按 0.45 的真实方法名写了注释（`setClipPlane` 吃 `[depth, azimuth, elevation]`，`scene.clipPlane` 是平面方程）。新工具照抄这个密度，不要另发明一层。

## After a mutation

Read the viewer back. `getState()` / `derivedSymmetryPreset()` / component
`representation` must come from the tree that just changed, not from an adapter
ledger (`watersHidden`, `currentStyle`). Replay compares `state_after` with
`sameJSON`; a lie there is a later trajectory bug, not a cosmetic one.

`holdSemanticCamera` is required when visible content changed (preset, hide
structure, hide component, swap representation, add a measurement). It frames
against the **visible** sphere, so hiding a huge water shell will re-fit the
remaining polymer. That is intended.

## Expert clicks

Record-mode user-facing buttons go through `previewQuickAction` or `runTool`
(1 s cooldown, panel disabled). `previewTool` is for replay, explore-mode live
sliders, and tests. Skipping the cooldown on a new record-mode button floods OSS
with keyframes.

Explore mode does **not** get that 1 s gap.

## Registry

The draft freezes `registry_sha256` + `effective_tool_policy` so submit/replay
know which schemas the steps were recorded against. Shipping **new public
tools** is a compatible expansion and must be adopted onto in-progress drafts
(see `adopt_live_registry`). Changing an existing tool's `inputSchema` is not;
name-subset adopt will still fire, so prefer additive tools over in-place
schema breaks.

The panel lists live public tools. If the frozen draft does not, the expert
gets a local success and a 400 on upload.
