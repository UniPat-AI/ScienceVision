# Harbor 适配层：`sci3d`

把 `sci3d_agent` 的 agent loop 包成 [Harbor](https://github.com/harbor-framework/harbor)
的 `BaseAgent`，使其能被 `harbor run` 统一驱动 —— 与 Claude Code、SWE-Agent、Codex
等 Harbor 内置 agent 同一套 harness。

这是"用 Harbor 作底座统一 agent harness"迁移的**第一步**（agent 适配层）。
task 格式转换、连续分 verify 接入是后续步骤（见文末）。

## 这是什么 / 不是什么

- **是**：一个 import-path 注册的自定义 Harbor agent（`Sci3dAgent`），不改 Harbor 源码。
- **不是**：task 定义、verifier、或把渲染/SAM 服务容器化。本步只让 sci3d agent
  "能被 Harbor 调用并产出标准 trajectory + answer"。

## 环境（重要）

**Harbor 要求 Python ≥ 3.12**（本仓库本体只要求 ≥3.10）。系统默认 `python3`
可能是 3.6/3.10，装不了 Harbor。用 `uv` 建独立的 3.12 环境：

```bash
uv venv --python 3.12 .venv-harbor
uv pip install --python .venv-harbor/bin/python -e ".[harbor]"
# 联调真实 LLM 时若 shell 设了 SOCKS 代理需清掉（会破坏 httpx）
```

## 注册与运行

`Sci3dAgent` 的 import-path 是 `harbor_adapters.sci3d.agent:Sci3dAgent`。

```bash
.venv-harbor/bin/harbor run \
    --agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent \
    --model anthropic/claude-haiku-4-5-20251001 \
    --ak mode=mode1_no_vision \
    --ak max_iters=20 \
    --ak asset_path=/data/heart.glb \
    --dataset <your-3d-task-dataset@version> \
    --env docker
```

### Agent kwargs（`--ak key=value`，传入 `Sci3dAgent.__init__`）

| kwarg        | 默认               | 说明                                   |
|--------------|--------------------|----------------------------------------|
| `mode`       | `mode1_no_vision`  | sci3d `ModeProfile` 名（见 `sci3d_agent/modes.py`）|
| `max_iters`  | `20`               | agent loop 迭代上限                    |
| `seed`       | `0`                | RNG seed                               |
| `asset_path` | `None`             | 3D 资产路径（instruction 未携带时的兜底）|

## instruction → (question, asset) 映射

Harbor task 的 `instruction.md` 是自由文本，sci3d 需要 question + asset 路径。
`harbor_adapters/sci3d/_instruction.py` 按以下顺序解析 asset：

1. 围栏 JSON 块（最明确）：
   ````
   ```sci3d
   {"asset_path": "/data/heart.glb"}
   ```
   ````
2. `Asset:` 行（大小写不敏感）：`Asset: /data/heart.glb`
3. `--ak asset_path=...` 兜底。

question 取 `Question:` 行；没有则取去掉标记后的全部 prose。

## 产出

`run()` 跑完 sci3d loop 后：

- **populate Harbor `AgentContext`**：`n_input_tokens` / `n_output_tokens` / `cost_usd`，
  并把 `sci3d_final_answer` / `sci3d_timeout` / `sci3d_error` 放进 `metadata`。
- **写两个 artifact 到 `logs_dir`**（供后续 verifier 读取）：
  - `sci3d_answer.json` — `{"answer": ...}`
  - `sci3d_trajectory.json` — Harbor `Trajectory`（ATIF）格式

sci3d 的 `TrajectoryRecord → harbor.models.trajectories.Trajectory` 翻译集中在
`harbor_adapters/sci3d/_translate.py`（纯函数，单测覆盖）。**这是适配层对 Harbor
trajectory schema 的唯一耦合点** —— Harbor schema 变动时只改这一处。

## 两条容器化路线（设计取舍）

本步走 **host-run 路线**：

- sci3d_agent 是 host 端 Python 包（直接 import LLM client + 本地 kernel/viewer
  bridge），不是装进容器的 CLI。
- `Sci3dAgent.run()` 在**当前进程**内调 `sci3d_agent.agent.run_agent(...)`，不通过
  `environment.exec`。`setup()` 是 no-op（渲染/SAM 缺失时依赖 sci3d 既有的
  `renderer_skipped` 降级路径）。

后续如需 **installed/容器化路线**（agent 在 task 容器内执行）：

- 把 sci3d_agent 装进 task 的 Docker 镜像，子类化 `Sci3dAgent` 覆写 `run()` 改为
  `environment.exec("sci3d-agent ...")`，并把渲染/SAM 服务一并容器化。
- 公共契约（`name`/`version`/`setup`/`run` + context 回填 + artifact 落地）不变，
  仅 `run()` 内部实现替换。

## 测试

```bash
.venv-harbor/bin/python -m pytest tests/harbor_adapters/ -q
```

无网络、无 GL/SAM 依赖：端到端用例通过 `SCI3D_USE_FAKE_LLM=1` + `FakeKernel` 跑
sci3d loop，断言 context 被 populate、answer + trajectory 落地、翻译正确。

## 与现有 sci3d verify 框架的关系

本步**不动**现有的 `scripts/eval/`、`QA-Gen/TaskGenerator/evaluation/`、
`scripts/m3d_refseg_verify/`。它们仍是当前的 verify 路径。

**已落地**：deterministic verify 通过 `--verifier-import-path
harbor_adapters.sci3d.verifier:Sci3dVerifier` 接入。`reward` 等于 binary
rubric 聚合后的 `answer_score`；process 与可选 LLM judge 仅作为独立诊断列。详见
[VERIFY_sci3d.md](VERIFY_sci3d.md)。本适配层落地的 `sci3d_answer.json` +
`sci3d_trajectory.json` 正是 verifier 的输入。
```
