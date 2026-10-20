# EV2 content/process eval marking + full-artifact re-run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every EV2 eval report show content, process, and reward side-by-side at both per-task and aggregate levels, and re-run the 15 EV2 tasks capturing answers, trajectories, and MCP render screenshots together.

**Architecture:** The scoring core already emits `llm_judge` (content) + `process_judge` (process) + `weights`; this is purely a reporting-layer change (Tasks 1–2) plus a screenshot-capture wiring change (Tasks 3–4) and an operational re-run (Task 5). No change to the scoring core or `sci3d_scores.json` schema.

**Tech Stack:** Python 3.12 (harbor venv), Harbor 0.20 CLI, pytest, sci3d MCP server, Docker.

## Global Constraints

- **Harbor venv only** (py3.12): `~/.venv-harbor/bin/python`, `~/.venv-harbor/bin/harbor`.
- **Judge model**: always `SCI3D_JUDGE_MODEL=claude-haiku-4-5` (undated). The dated `claude-haiku-4-5-20251001` 503s on the ALB → silent reward=0.100 floor.
- **Clear proxies for any API call**: prefix with `env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy`.
- **API creds**: shell-default `$ANTHROPIC_BASE_URL` + `$ANTHROPIC_API_KEY` (`source ~/.bashrc`). Do NOT use `qiniu_on` — that sk- key is expired.
- **Do NOT modify** `harbor_adapters/sci3d/_score_core.py` or the `sci3d_scores.json` schema.
- **Non-breaking**: keep the existing `by_harness_model_mean_reward` leaderboard key (an existing test asserts it exactly).
- Files ≤800 lines outside `vision3d/`.
- Scorer/registry tests need `SCI3D_USE_FAKE_LLM=1` in env (already set by `tests/harbor_adapters/test_qagen_pilot.py`; new tests here don't build scorers so they don't need it, but running the whole file is fine).
- Run tests with: `~/.venv-harbor/bin/python -m pytest <path> -q`.

---

### Task 1: Leaderboard aggregate — add mean content + mean process

**Files:**
- Modify: `scripts/harbor/collect_qagen_results.py` (`build_leaderboard`, `_leaderboard_md`)
- Test: `tests/harbor_adapters/test_qagen_pilot.py` (add one test)

**Interfaces:**
- Consumes: `build_leaderboard(runs: list[dict], tasks_root: Path) -> dict`; each run has `rewards` possibly containing `reward`, `llm_judge`, `process_judge`.
- Produces: board dict gains keys `by_harness_model_mean_content`, `by_harness_model_mean_process` (both `dict[str, float]`, keyed `"<harness> / <model>"`). `runs_leaderboard.md` aggregate "by harness / model" table gains `content` and `process` columns; per-task table header renames `judge`→`content`, `proc_judge`→`process`.

- [ ] **Step 1: Write the failing test**

Add to `tests/harbor_adapters/test_qagen_pilot.py`:

```python
def test_leaderboard_reports_content_and_process(tmp_path):
    from scripts.harbor.collect_qagen_results import build_leaderboard

    runs = [
        {"task_id": "a", "set": "S", "domain": "chemistry", "v_class": "",
         "harness": "cc", "model": "m", "status": "ok",
         "rewards": {"reward": 0.7, "llm_judge": 0.8, "process_judge": 0.5}},
        {"task_id": "b", "set": "S", "domain": "chemistry", "v_class": "",
         "harness": "cc", "model": "m", "status": "ok",
         "rewards": {"reward": 0.5, "llm_judge": 0.4, "process_judge": 0.9}},
    ]
    board = build_leaderboard(runs, tmp_path)
    assert board["by_harness_model_mean_content"]["cc / m"] == pytest.approx(0.6)
    assert board["by_harness_model_mean_process"]["cc / m"] == pytest.approx(0.7)
    md = (tmp_path / "runs_leaderboard.md").read_text()
    assert "| content |" in md or "content" in md.split("Per-task")[0]
    # per-task header renamed
    assert "proc_judge" not in md and "process" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_qagen_pilot.py::test_leaderboard_reports_content_and_process -q`
Expected: FAIL with `KeyError: 'by_harness_model_mean_content'`.

- [ ] **Step 3: Add content/process means in `build_leaderboard`**

In `scripts/harbor/collect_qagen_results.py`, after the existing `by_harness_model`/`by_harness_domain` computation (the `hm`/`hd` loop, ~line 168–176), add:

```python
    # content (llm_judge) and process (process_judge) means, same grouping.
    hm_c: dict[str, list[float]] = {}
    hm_p: dict[str, list[float]] = {}
    for x in ok:
        key = f"{x['harness']} / {x['model']}"
        if isinstance(x.get("llm_judge"), (int, float)):
            hm_c.setdefault(key, []).append(x["llm_judge"])
        if isinstance(x.get("process_judge"), (int, float)):
            hm_p.setdefault(key, []).append(x["process_judge"])
    by_harness_model_mean_content = {k: sum(v) / len(v) for k, v in hm_c.items()}
    by_harness_model_mean_process = {k: sum(v) / len(v) for k, v in hm_p.items()}
```

Then extend the `board` dict (keep existing keys unchanged):

```python
    board = {
        "n_runs": len(rows),
        "n_completed": len(ok),
        "by_harness_model_mean_reward": by_harness_model,
        "by_harness_model_mean_content": by_harness_model_mean_content,
        "by_harness_model_mean_process": by_harness_model_mean_process,
        "by_harness_domain_mean_reward": by_harness_domain,
        "rows": rows,
    }
```

- [ ] **Step 4: Update `_leaderboard_md` aggregate + per-task headers**

Replace the "Mean reward by harness / model" table block with a combined table (reward/content/process). Change:

```python
        "## Mean reward by harness / model",
        "",
        "| harness / model | mean reward |",
        "|-----------------|-------------|",
    ]
    for k, m in sorted(board["by_harness_model_mean_reward"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {_fmt(m)} |")
```

to:

```python
        "## Mean by harness / model (reward · content · process)",
        "",
        "| harness / model | reward | content | process |",
        "|-----------------|--------|---------|---------|",
    ]
    for k, m in sorted(board["by_harness_model_mean_reward"].items(), key=lambda kv: -kv[1]):
        c = board["by_harness_model_mean_content"].get(k)
        p = board["by_harness_model_mean_process"].get(k)
        lines.append(f"| {k} | {_fmt(m)} | {_fmt(c)} | {_fmt(p)} |")
```

And in the per-task table, rename the header cells `judge`→`content`, `proc_judge`→`process` (the row cells already use `x['llm_judge']`/`x['process_judge']` — leave those):

```python
        "| harness | model | task | domain | v_class | status | reward | exact | content | process |",
        "|---------|-------|------|--------|---------|--------|--------|-------|---------|---------|",
```

- [ ] **Step 5: Run test to verify it passes + no regression**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_qagen_pilot.py -q`
Expected: PASS (including the pre-existing `test_collect_leaderboard_groups_by_harness`).

- [ ] **Step 6: Commit**

```bash
git add scripts/harbor/collect_qagen_results.py tests/harbor_adapters/test_qagen_pilot.py
git commit -m "feat(harbor): leaderboard reports mean content + process, not just reward"
```

---

### Task 2: EV2 three-way report — per-task content/process + fold in content script

**Files:**
- Modify: `scripts/harbor/report_ev2_threeway.py` (add `rationale` to `_load_score`; extract `build_summary`/`render_md`; add content/process per-task)
- Delete: `scripts/harbor/collect_ev2_content.py` (folded in)
- Test: create `tests/harbor_adapters/test_ev2_threeway_report.py`

**Interfaces:**
- Consumes: `collect_baseline(b: str) -> dict[str, dict]` (existing; each record now also carries `rationale: str`).
- Produces: `build_summary(data: dict[str, dict]) -> dict` and `render_md(summary: dict) -> str`. `summary["per_task"][task_id][baseline]` is now `{"reward": float, "content": float, "process": float, "rationale": str}` (or `None`). `render_md` output contains `### per-task reward`, `### per-task content`, `### per-task process` sections; baseline summary columns are labeled `content`/`process`.

- [ ] **Step 1: Write the failing test**

Create `tests/harbor_adapters/test_ev2_threeway_report.py`:

```python
"""report_ev2_threeway: per-task content/process marking + summary shape."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.harbor.report_ev2_threeway as mod  # noqa: E402


def _write_score(root: Path, baseline: str, task_id: str, content, process, reward):
    d = root / f"jobs-ev2-{baseline}" / "2026-07-30__00-00-00" / f"{task_id}__hash" / "verifier"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sci3d_scores.json").write_text(json.dumps({
        "task_id": task_id, "domain": "chemistry",
        "llm_judge": {"value": content, "raw": {"rationale": "because"}},
        "process_judge": {"value": process},
        "reward": reward, "tool_names": [],
    }))


def test_summary_has_per_task_content_and_process(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    _write_score(tmp_path, "cc", "sv-chem-conformation-01", 0.8, 0.6, 0.74)
    data = {b: mod.collect_baseline(b) for b in mod.BASELINES}
    summary = mod.build_summary(data)
    cell = summary["per_task"]["sv-chem-conformation-01"]["cc"]
    assert cell["content"] == 0.8
    assert cell["process"] == 0.6
    assert cell["reward"] == 0.74
    assert cell["rationale"] == "because"
    md = mod.render_md(summary)
    assert "### per-task content" in md
    assert "### per-task process" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_ev2_threeway_report.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'build_summary'`.

- [ ] **Step 3: Add `rationale` to `_load_score`**

In `report_ev2_threeway.py::_load_score`, add to the returned dict (after `process_judge`):

```python
        "rationale": (lj.get("raw") or {}).get("rationale", ""),
```

- [ ] **Step 4: Extract `build_summary` and `render_md`**

Refactor `main()`: move the summary-dict construction into `build_summary(data)` and the markdown construction into `render_md(summary)`. `build_summary` keeps the existing `baselines`/`per_domain`/`tiers` blocks unchanged and changes `per_task` to carry the three scores:

```python
def build_summary(data: dict) -> dict:
    all_tasks = sorted({t for d in data.values() for t in d})
    summary = {"baselines": {}, "per_domain": {}, "per_task": {}, "tiers": {}}
    for b in BASELINES:
        recs = list(data[b].values())
        summary["baselines"][b] = {
            "label": BASELINES[b],
            "n": len(recs),
            "reward": _mean([r["reward"] for r in recs]),
            "content": _mean([r["llm_judge"] for r in recs]),
            "process": _mean([r["process_judge"] for r in recs]),
        }
        summary["tiers"][b] = {
            tier: sum(1 for r in recs if r.get("tier") == tier)
            for tier in ("rerun", "rescored", "degraded")
        }
        dd = defaultdict(list)
        for t, r in data[b].items():
            dd[domain_of(t)].append(r["reward"])
        summary["per_domain"][b] = {d: _mean(v) for d, v in sorted(dd.items())}
    for t in all_tasks:
        summary["per_task"][t] = {
            b: {
                "reward": round(data[b][t]["reward"], 3),
                "content": round(data[b][t]["llm_judge"], 3),
                "process": round(data[b][t]["process_judge"], 3),
                "rationale": data[b][t].get("rationale", ""),
            } if t in data[b] else None
            for b in BASELINES
        }
    return summary
```

`render_md(summary)` reuses the existing baseline-summary and per-domain tables (relabel the two score columns to `content`/`process`), then emits three per-task tables:

```python
def render_md(summary: dict) -> str:
    L = []
    L.append("## ev2 three-way baseline — reward / content / process\n")
    L.append("| baseline | n | reward | content | process | tiers (rerun/rescored/degraded) |")
    L.append("|---|--:|--:|--:|--:|--:|")
    for b in BASELINES:
        s = summary["baselines"][b]
        ti = summary["tiers"][b]
        L.append(f"| {s['label']} | {s['n']} | {s['reward']:.3f} | {s['content']:.3f} "
                 f"| {s['process']:.3f} | {ti['rerun']}/{ti['rescored']}/{ti['degraded']} |")
    L.append("\n### per-domain mean reward\n")
    doms = sorted({d for b in BASELINES for d in summary["per_domain"][b]})
    L.append("| domain | " + " | ".join(BASELINES) + " |")
    L.append("|---|" + "---|" * len(BASELINES))
    for d in doms:
        L.append(f"| {d} | " + " | ".join(
            f"{summary['per_domain'][b].get(d, float('nan')):.3f}" for b in BASELINES) + " |")
    for metric in ("reward", "content", "process"):
        L.append(f"\n### per-task {metric}\n")
        L.append("| task | " + " | ".join(BASELINES) + " |")
        L.append("|---|" + "---|" * len(BASELINES))
        for t in sorted(summary["per_task"]):
            cells = []
            for b in BASELINES:
                v = summary["per_task"][t][b]
                cells.append(f"{v[metric]:.3f}" if v else "—")
            L.append(f"| {t} | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"
```

Rewrite `main()` to call them:

```python
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "harbor" / "EV2_THREEWAY"))
    args = ap.parse_args()
    data = {b: collect_baseline(b) for b in BASELINES}
    summary = build_summary(data)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    md = render_md(summary)
    out.with_suffix(".md").write_text(md)
    print(md)
    print(f"\nwrote {out.with_suffix('.json')} and {out.with_suffix('.md')}")
```

- [ ] **Step 5: Delete the folded-in content script**

```bash
git rm scripts/harbor/collect_ev2_content.py
```

Confirm nothing imports it: `grep -rn "collect_ev2_content" scripts/ tests/ docs/` should return no code imports (docs mentions are fine).

- [ ] **Step 6: Run test to verify it passes**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_ev2_threeway_report.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/harbor/report_ev2_threeway.py tests/harbor_adapters/test_ev2_threeway_report.py
git rm scripts/harbor/collect_ev2_content.py
git commit -m "feat(harbor): EV2 three-way report marks per-task content+process; fold in content script"
```

---

### Task 3: Wire SV_RENDER_CACHE into the ccmcp agent env so render PNGs are archived

**Files:**
- Modify: `scripts/harbor/_agent_envs.py` (`DF_CLAUDE_CODE_MCP`)
- Test: `tests/harbor_adapters/test_agent_env_table.py` (update byte-identical snapshot); `tests/mcp/test_render_static.py` (add env test) — or a new small test file if `test_render_static.py` lacks harbor-venv markers.

**Interfaces:**
- Consumes: `sci3d_agent.mcp.runtime.paths.render_cache_dir()` (reads `$SV_RENDER_CACHE`, default `/tmp/sv_renders`).
- Produces: ccmcp container Dockerfile sets `ENV SV_RENDER_CACHE=/app/renders` and pre-creates the dir, so MCP-rendered PNGs land in `/app/renders`, which harbor archives into the trial's `agent/renders/`.

- [ ] **Step 1: Write the failing env test**

Add to `tests/harbor_adapters/test_agent_env_table.py`:

```python
def test_ccmcp_dockerfile_sets_render_cache_under_app():
    df = _agent_envs.DF_CLAUDE_CODE_MCP
    assert "ENV SV_RENDER_CACHE=/app/renders" in df
    assert "mkdir -p /app/renders" in df
```

And add a unit test for the resolver (new file `tests/mcp/test_render_cache_env.py`):

```python
def test_render_cache_dir_honors_env(monkeypatch):
    from sci3d_agent.mcp.runtime import paths
    monkeypatch.setenv("SV_RENDER_CACHE", "/app/renders")
    assert str(paths.render_cache_dir()) == "/app/renders"
    monkeypatch.delenv("SV_RENDER_CACHE", raising=False)
    assert str(paths.render_cache_dir()) == "/tmp/sv_renders"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_agent_env_table.py::test_ccmcp_dockerfile_sets_render_cache_under_app tests/mcp/test_render_cache_env.py -q`
Expected: the env-cache test PASSES (paths.py already reads the env); the Dockerfile test FAILS (`ENV SV_RENDER_CACHE` not present). Also `test_claude_code_mcp_dockerfile_byte_identical_to_prior` will now FAIL — expected, fixed in Step 4.

- [ ] **Step 3: Add the ENV + mkdir to `DF_CLAUDE_CODE_MCP`**

In `scripts/harbor/_agent_envs.py`, change `DF_CLAUDE_CODE_MCP` to:

```python
DF_CLAUDE_CODE_MCP = (
    "# Agent environment for the Claude Code CLI + sci3d MCP-tools harness.\n"
    "# The base image bundles the `claude` CLI, the sciencevision MCP server\n"
    "# (registered via /app/.mcp.json), and the slim inspection deps. Each\n"
    "# task's 3D asset(s) are COPYd to /data so the MCP tools can read the\n"
    "# real file (build_harbor_tasks stages them under environment/asset/).\n"
    "# SV_RENDER_CACHE points render output into the agent workspace so harbor\n"
    "# archives every MCP-rendered PNG into the trial's agent/renders/.\n"
    "FROM sci3d-claude-code-mcp:latest\n"
    "COPY asset/ /data/\n"
    "ENV SV_RENDER_CACHE=/app/renders\n"
    "RUN mkdir -p /app/renders\n"
    "WORKDIR /app\n"
)
```

- [ ] **Step 4: Update the byte-identical snapshot test**

In `tests/harbor_adapters/test_agent_env_table.py::test_claude_code_mcp_dockerfile_byte_identical_to_prior`, replace the `expected` string with the new body (add the two comment lines + `ENV`/`RUN` lines exactly as in Step 3) so the snapshot matches the intended new Dockerfile.

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_agent_env_table.py tests/mcp/test_render_cache_env.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/harbor/_agent_envs.py tests/harbor_adapters/test_agent_env_table.py tests/mcp/test_render_cache_env.py
git commit -m "feat(harbor): ccmcp env pins SV_RENDER_CACHE=/app/renders so render PNGs are archived"
```

---

### Task 4: Collect render screenshots next to answer + trajectory

**Files:**
- Modify: `scripts/harbor/collect_qagen_results.py` (`collect_one` discovers PNGs; `write_runs` copies them)
- Test: `tests/harbor_adapters/test_qagen_pilot.py` (add one test)

**Interfaces:**
- Consumes: trial layout `<trial>/agent/renders/*.png` (from Task 3).
- Produces: `collect_one(...)` return dict gains `screenshots_src: list[str]` (absolute source PNG paths). `write_runs(runs, tasks_root)` copies them into `<set>/runs/<slug>/*.png`, sets `run["screenshots"]: list[str]` (paths relative to the runs dir, e.g. `"<slug>/view0.png"`), and drops `screenshots_src` from the persisted JSON.

- [ ] **Step 1: Write the failing test**

Add to `tests/harbor_adapters/test_qagen_pilot.py`:

```python
def test_write_runs_copies_screenshots(tmp_path):
    from scripts.harbor.collect_qagen_results import write_runs, _run_slug

    src = tmp_path / "trial_agent_renders"
    src.mkdir()
    (src / "view0.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    run = {
        "task_id": "t1", "set": "S", "harness": "ccmcp", "model": "opus",
        "rewards": {"reward": 0.5}, "answer": "a", "trajectory": [],
        "screenshots_src": [str(src / "view0.png")],
    }
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    write_runs([run], tasks_root)

    slug = _run_slug(run)
    assert (tasks_root / "S" / "runs" / slug / "view0.png").read_bytes().startswith(b"\x89PNG")
    rec = json.loads((tasks_root / "S" / "runs" / f"{slug}.json").read_text())
    assert rec["screenshots"] == [f"{slug}/view0.png"]
    assert "screenshots_src" not in rec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_qagen_pilot.py::test_write_runs_copies_screenshots -q`
Expected: FAIL (`KeyError: 'screenshots'` or the copied file missing).

- [ ] **Step 3: Copy screenshots in `write_runs`**

Replace `write_runs` in `scripts/harbor/collect_qagen_results.py` with:

```python
def write_runs(runs: list[dict], tasks_root: Path) -> None:
    for run in runs:
        if run["set"] == "_unknown":
            continue
        out_dir = tasks_root / run["set"] / "runs"
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = _run_slug(run)
        srcs = run.pop("screenshots_src", None) or []
        shots: list[str] = []
        if srcs:
            shot_dir = out_dir / slug
            shot_dir.mkdir(parents=True, exist_ok=True)
            for sp in srcs:
                p = Path(sp)
                if p.exists():
                    (shot_dir / p.name).write_bytes(p.read_bytes())
                    shots.append(f"{slug}/{p.name}")
        run["screenshots"] = shots
        (out_dir / f"{slug}.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2)
        )
```

- [ ] **Step 4: Discover PNGs in `collect_one`**

In `collect_one`, before the `return`, add screenshot discovery and include it in the returned dict:

```python
    shots = sorted(str(p) for p in (agent_dir / "renders").glob("*.png"))
```

and add `"screenshots_src": shots,` to the returned dict literal.

- [ ] **Step 5: Run tests to verify they pass**

Run: `~/.venv-harbor/bin/python -m pytest tests/harbor_adapters/test_qagen_pilot.py -q`
Expected: PASS (all, including the earlier `test_stage_*` and Task 1's test).

- [ ] **Step 6: Commit**

```bash
git add scripts/harbor/collect_qagen_results.py tests/harbor_adapters/test_qagen_pilot.py
git commit -m "feat(harbor): collect render screenshots into runs/<slug>/ beside answer+trajectory"
```

---

### Task 5: Regenerate ccmcp task tree, re-run 15 EV2 tasks × 3 baselines, regenerate reports

**This is the expensive live-environment step** (~45 container runs + judge API). Requires H20 images `sci3d-claude-code:latest`, `sci3d-claude-code-2d:latest`, `sci3d-claude-code-mcp:latest`, and shell-default API creds. No unit test — verification is by inspecting produced artifacts.

**Files:**
- Create (regenerated): `scripts/harbor/tasks-ev2-ccmcp/**` (picks up new Dockerfile ENV)
- Create (job trees): `jobs-ev2-{cc,cc2d,ccmcp}-<ts>/`
- Create/update: `QA-Gen/tasks/EV2_Pilot/runs/**`, `docs/harbor/EV2_THREEWAY.{md,json}`, `viewer/ev2_pilot/**`

- [ ] **Step 1: Regenerate the ccmcp task tree so the new SV_RENDER_CACHE Dockerfile is baked**

```bash
source ~/.bashrc
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
  ~/.venv-harbor/bin/python -m scripts.harbor.qa_gen_to_tasks \
    --jsonl QA-Gen/tasks/EV2_Pilot/ScienceVision_EV2_Pilot_final_tasks.jsonl \
    --out scripts/harbor/tasks-ev2-ccmcp \
    --asset-root "$(pwd)" --containerized --agent-env claude-code-mcp
grep -r "SV_RENDER_CACHE" scripts/harbor/tasks-ev2-ccmcp/*/environment/Dockerfile | head
```
Expected: every ccmcp task Dockerfile now contains `ENV SV_RENDER_CACHE=/app/renders`.

- [ ] **Step 2: Smoke run — one ccmcp medical task known to render, verify PNGs archived**

`sv-medi-anatomical-region-01` called `sv3d_render_canonical_views` + `sv_render_nifti_*` in the prior run, so it exercises render.

```bash
source ~/.bashrc
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
  ~/.venv-harbor/bin/harbor run --path scripts/harbor/tasks-ev2-ccmcp \
    --agent claude-code --model claude-opus-4-8 \
    --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
    --ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \
    --ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" \
    --ae "SCI3D_JUDGE_MODEL=claude-haiku-4-5" \
    --agent-setup-timeout-multiplier 3.0 --agent-timeout-multiplier 2.0 \
    --env docker -i sv-medi-anatomical-region-01 -o jobs-ev2-smoke
find jobs-ev2-smoke -path "*/agent/renders/*.png" | head
```
Expected: at least one PNG under `jobs-ev2-smoke/*/sv-medi-*/agent/renders/`. If empty, STOP — the harbor archive path for `/app/renders` needs revisiting (fallback: add a `tests/test.sh` copy of `$SV_RENDER_CACHE` into `/logs/agent/renders`) before the full run.

- [ ] **Step 3: Full re-run — 3 baselines × 15 tasks (fresh job dirs)**

Run each baseline over the full set (no `-i` filter). Use fresh timestamped `-o` dirs so prior jobs are preserved.

```bash
source ~/.bashrc
COMMON=(--agent claude-code --model claude-opus-4-8 \
  --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
  --ae "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL" \
  --ae "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" \
  --ae "SCI3D_JUDGE_MODEL=claude-haiku-4-5" \
  --agent-setup-timeout-multiplier 3.0 --agent-timeout-multiplier 2.0 --env docker)
NOPROXY=(env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy)
for b in cc cc2d ccmcp; do
  "${NOPROXY[@]}" ~/.venv-harbor/bin/harbor run \
    --path scripts/harbor/tasks-ev2-$b "${COMMON[@]}" -o jobs-ev2-$b-v2
done
```
Expected: each `jobs-ev2-$b-v2/<ts>/` has 15 trials with `verifier/sci3d_scores.json`. Watch for `degraded: true` (judge 503) — if any, run the rescore fallback (`~/.venv-harbor/bin/python -m scripts.harbor.rescore_rerun` adapted to the `-v2` dirs) as in the prior pipeline.

- [ ] **Step 4: Collect runs (answer + trajectory + screenshots) into the EV2_Pilot set**

```bash
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
  ~/.venv-harbor/bin/python -m scripts.harbor.collect_qagen_results \
    --run cc:claude-opus-4-8:jobs-ev2-cc-v2 \
    --run cc2d:claude-opus-4-8:jobs-ev2-cc2d-v2 \
    --run ccmcp:claude-opus-4-8:jobs-ev2-ccmcp-v2 \
    --tasks-root QA-Gen/tasks
ls QA-Gen/tasks/EV2_Pilot/runs/ | head
find QA-Gen/tasks/EV2_Pilot/runs -name "*.png" | head
```
Expected: per-task run JSONs with `answer`, `trajectory`, `screenshots`; ccmcp render PNGs present under `runs/<slug>/`. Leaderboard `runs_leaderboard.md` shows reward/content/process columns.

- [ ] **Step 5: Regenerate the EV2 three-way report from the fresh runs**

`report_ev2_threeway.py` reads `jobs-ev2-<b>` and `jobs-ev2-<b>-rerun`. Point it at the new dirs. Simplest without editing the script's globs: symlink or pass a `--jobs-suffix`? The script hardcodes `jobs-ev2-<b>`. Since a `--jobs-suffix` arg does not exist, either (a) move the `-v2` job dirs to `jobs-ev2-<b>` (after archiving the old ones), or (b) add a `--jobs-suffix` CLI arg to `collect_baseline`. Choose (a) for a one-shot re-run:

```bash
for b in cc cc2d ccmcp; do
  mv jobs-ev2-$b jobs-ev2-$b-2026-07-23 2>/dev/null || true
  mv jobs-ev2-$b-v2 jobs-ev2-$b
done
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
  ~/.venv-harbor/bin/python -m scripts.harbor.report_ev2_threeway
```
Expected: `docs/harbor/EV2_THREEWAY.md` shows baseline reward/content/process + three per-task tables.

- [ ] **Step 6: Rebuild the viewer bundle**

```bash
~/.venv-harbor/bin/python -m scripts.harbor.build_ev2_viewer_bundle
```
Expected: `viewer/ev2_pilot/` refreshed with the new scores.

- [ ] **Step 7: Commit artifacts + regenerated task tree**

```bash
git add scripts/harbor/tasks-ev2-ccmcp docs/harbor/EV2_THREEWAY.md docs/harbor/EV2_THREEWAY.json \
        QA-Gen/tasks/EV2_Pilot viewer/ev2_pilot
git commit -m "chore(harbor): re-run 15 EV2 tasks x3 baselines; content/process reports + render screenshots"
```

---

## Self-Review

**Spec coverage:**
- Task 1 requirement "content/process at aggregate level" → Task 1 (leaderboard) + Task 2 (EV2 baseline summary). ✓
- "content/process at per-task level" → Task 1 (leaderboard per-task rename) + Task 2 (three per-task tables). ✓
- "consistent terminology" → both reports use `content`/`process` labels. ✓
- "fold collect_ev2_content" → Task 2 Step 5. ✓
- "screenshot capture mechanism" → Task 3 (SV_RENDER_CACHE) + Task 4 (collect/copy). ✓
- "re-run 15 tasks, save answer/trajectory/screenshots" → Task 5. ✓
- "regenerate reports + viewer" → Task 5 Steps 5–6. ✓
- Not-in-scope (weights, scoring core, schema) respected. ✓

**Placeholder scan:** No TBD/TODO; all code steps have real code. Task 5 is operational with exact commands; the one branch (report jobs-dir mismatch) is resolved concretely via option (a) rename.

**Type consistency:** `build_summary`/`render_md` names match between Task 2 definition and its test. `screenshots_src`/`screenshots` keys match between Task 4 `collect_one`/`write_runs`/test. `by_harness_model_mean_content`/`_process` keys match between Task 1 impl and test. `_run_slug` reused, not redefined.

**Deviation from spec noted:** spec said "ccmcp and cc agent-env" get SV_RENDER_CACHE; narrowed to **ccmcp only** (cc has no MCP tools, cannot render) — sensible, keeps the byte-identical `cc` snapshot test untouched.
