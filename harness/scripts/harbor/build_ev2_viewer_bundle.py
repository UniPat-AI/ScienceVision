"""Build the ev2-pilot viewer integration for the #qagen tab.

Produces three artifacts so the 15 ev2 tasks show up as a #qagen set whose
per-question panel visualizes all three baselines' agent trajectories:

  1. QA-Gen/tasks/EV2_Pilot/ScienceVision_EV2_Pilot_final_tasks.jsonl
     15 viewer task-records mapped from scripts/harbor/tasks-ev2-cc/<id>/
     {task.toml[metadata], instruction.md}.
  2. QA-Gen/tasks/EV2_Pilot/generation_stats.json
     so tools/build_qagen_index.py emits an EV2_Pilot card.
  3. viewer/verify_ev2_pilot.json
     a MULTI-baseline verify bundle: per task, one trajectory block per
     baseline (cc / cc2d / ccmcp) with reward + judge rationales + steps.

Score source per (task, baseline) mirrors report_ev2_threeway.py's tiering:
rerun (non-degraded) > original rescored > original degraded.

This module also exposes the shared trial-scanning / score-loading /
trajectory-parsing helpers that scripts/harbor/failure_report_ev2.py imports —
there is exactly one implementation of "find the best trial + read its score
+ parse its trajectory".

Run with the harbor venv python (3.12; system python3 lacks tomllib):
    ~/.venv-harbor/bin/python scripts/harbor/build_ev2_viewer_bundle.py
"""
from __future__ import annotations

import glob
import json
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TASKS_SRC = ROOT / "scripts" / "harbor" / "tasks-ev2-cc"   # task defs (same ids across baselines)
SET_NAME = "EV2_Pilot"
SET_DIR = ROOT / "QA-Gen" / "tasks" / SET_NAME
BUNDLE_OUT = ROOT / "viewer" / "verify_ev2_pilot.json"

BASELINES = ["cc", "cc2d", "ccmcp"]
BASELINE_LABELS = {
    "cc": "Codex (plain, raw 3D file)",
    "cc2d": "Codex + 2D multiview (PNGs only)",
    "ccmcp": "Codex + MCP (raw 3D + sci3d tools)",
}

# format -> viewer page (mirrors the viewers wired in viewer/*.html).
_VIEWER_BY_FMT = {
    "mol": "molstar", "sdf": "molstar", "xyz": "molstar",
    "cif": "molstar", "mmcif": "molstar", "pdb": "molstar",
    "nifti": "niivue", "nii": "niivue",
    "glb": "gltf", "gltf": "gltf",
}
# roots that are web-served from the viewer working dir; strip to a /-rooted path.
_WEB_ROOTS = ("crawl_data", "out_samples", "datasets")

# truncation budgets for the bundle (keep the JSON light for the browser).
_MAX_STEPS = 40
_MAX_REASON = 1200
_MAX_ARGS = 600
_MAX_RESULT = 1200
_MAX_ANSWER = 4000


# ── task.toml -> viewer task-record ─────────────────────────────────────── #
def _web_path(host_path: str) -> str:
    """Map an absolute host asset path to a viewer web path (/crawl_data/...)."""
    p = str(host_path or "")
    for root in _WEB_ROOTS:
        marker = f"/{root}/"
        i = p.find(marker)
        if i >= 0:
            return p[i:]
    # fallback: last two segments under a leading slash
    parts = [x for x in p.split("/") if x]
    return "/" + "/".join(parts[-2:]) if parts else p


def _humanize_title(task_id: str, domain: str) -> str:
    # sv-chem-chirality-07 -> "Chirality 07"
    segs = task_id.split("-")
    body = segs[2:] if len(segs) > 2 else segs
    words = " ".join(w.capitalize() for w in body)
    return words or task_id


def load_task_meta(task_id: str) -> tuple[dict, str]:
    tdir = TASKS_SRC / task_id
    meta = tomllib.loads((tdir / "task.toml").read_text()).get("metadata", {}) or {}
    instr = ""
    ip = tdir / "instruction.md"
    if ip.exists():
        instr = ip.read_text().strip()
    return meta, instr


def task_record(task_id: str) -> dict:
    """Map one ev2 task into the viewer task-record schema."""
    meta, instr = load_task_meta(task_id)
    domain = str(meta.get("domain", ""))
    fmt = str(meta.get("asset_format", ""))
    asset_host = str(meta.get("asset_path", ""))
    web = _web_path(asset_host)
    gt = meta.get("ground_truth") or {}
    ans = meta.get("answer_schema") or {}
    sc = meta.get("scorer") or {}
    return {
        "task_id": task_id,
        "title": _humanize_title(task_id, domain),
        "instruction": instr,
        "domain": domain,
        "v_class": str(meta.get("v_class", "")),
        "difficulty": "medium",
        "asset": {
            "name": Path(asset_host).name,
            "path": web,
            "repo_path": web.lstrip("/"),
            "format": fmt,
            "viewer": _VIEWER_BY_FMT.get(fmt, "gltf"),
            "exists_on_disk": bool(asset_host) and Path(asset_host).exists(),
        },
        "answer_schema": {
            "kind": ans.get("kind", ""),
            "fields": ans.get("fields"),
        },
        "scorer": {"primary": sc.get("primary", ""), "checks": []},
        "tool_policy": {"allowed_tools": meta.get("allowed_tools", []) or []},
        "forbidden_tools": meta.get("forbidden_tools", []) or [],
        "ground_truth": {
            "status": gt.get("status"),
            "type": gt.get("type"),
            "value": gt.get("value"),
            "rationale": gt.get("rationale"),
            "notes": gt.get("notes"),
        },
    }


# ── trial scanning + score loading (shared with failure_report_ev2) ─────── #
def _latest_run(pattern_dir: Path) -> Path | None:
    runs = sorted(glob.glob(str(pattern_dir / "*")))
    return Path(runs[-1]) if runs else None


def _load_score(path: Path) -> dict | None:
    try:
        j = json.loads(path.read_text())
    except Exception:
        return None
    lj = j.get("llm_judge") or {}
    pj = j.get("process_judge") or {}
    return {
        "task_id": j.get("task_id", ""),
        "reward": float(j.get("reward", 0.0)),
        "llm_judge": float(lj.get("value", 0.0)),
        "llm_degraded": bool(lj.get("degraded") or lj.get("error")),
        "llm_rationale": ((lj.get("raw") or {}).get("rationale") or ""),
        "process_judge": float(pj.get("value", 0.0)),
        "process_rationale": ((pj.get("raw") or {}).get("rationale") or ""),
        "tool_names": j.get("tool_names") or [],
        "degraded": bool(lj.get("degraded") or lj.get("error")),
        "src": path.name,
    }


def _exception_type(trial: Path) -> str | None:
    rp = trial / "result.json"
    if not rp.exists():
        return None
    try:
        d = json.loads(rp.read_text())
    except Exception:
        return None
    ei = d.get("exception_info") or {}
    return ei.get("exception_type") if isinstance(ei, dict) else None


def collect_trials(baseline: str) -> dict[str, dict]:
    """task_id -> best trial record {trial, tier, score fields, exception_type}.

    Tiering matches report_ev2_threeway.py: original (rescored>degraded) first,
    then a non-degraded rerun overrides.
    """
    by_task: dict[str, dict] = {}

    def _ingest(run: Path | None, tier_default: str, override_only_nondegraded: bool):
        if run is None:
            return
        for trial in sorted(run.glob("sv-*")):
            rec = _load_score(trial / "verifier" / "sci3d_scores_rescored.json") \
                or _load_score(trial / "verifier" / "sci3d_scores.json")
            if not rec or not rec["task_id"]:
                # errored trial with no usable score: synthesize a stub keyed by dir
                tid = trial.name.rsplit("__", 1)[0]
                rec = {"task_id": tid, "reward": 0.0, "llm_judge": 0.0,
                       "llm_degraded": True, "llm_rationale": "",
                       "process_judge": 0.0, "process_rationale": "",
                       "tool_names": [], "degraded": True, "src": "(none)"}
            if override_only_nondegraded and rec["degraded"]:
                continue
            rec = dict(rec)
            rec["trial"] = trial
            rec["tier"] = "rerun" if override_only_nondegraded else \
                ("rescored" if "rescored" in rec["src"] else "degraded")
            rec["exception_type"] = _exception_type(trial)
            by_task[rec["task_id"]] = rec

    _ingest(_latest_run(ROOT / f"jobs-ev2-codex-{baseline}"), "orig", False)
    _ingest(_latest_run(ROOT / f"jobs-ev2-codex-{baseline}-rerun"), "rerun", True)
    return by_task


# ── trajectory.json -> viewer step list + final answer ──────────────────── #
def _final_message(steps: list[dict]) -> str | None:
    for step in reversed(steps or []):
        src = str(step.get("source", "")).lower()
        if src and src not in ("agent", "assistant", "model"):
            continue
        msg = step.get("message")
        if msg and str(msg).strip():
            return str(msg).strip()
    return None


def _obs_by_call_id(step: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    obs = step.get("observation")
    if isinstance(obs, dict):
        for r in obs.get("results") or []:
            if isinstance(r, dict):
                out[r.get("source_call_id", "")] = str(r.get("content", ""))
    return out


def _truncate(s: Any, n: int) -> str:
    s = "" if s is None else (s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str))
    return s if len(s) <= n else s[:n] + "…"


def parse_trajectory(traj_path: Path) -> tuple[list[dict], str | None, list[str]]:
    """Return (viewer_steps, final_answer, distinct_tool_names)."""
    try:
        traj = json.loads(traj_path.read_text())
    except (OSError, json.JSONDecodeError):
        return [], None, []
    steps_in = traj.get("steps", []) or []
    final_answer = _final_message(steps_in)

    view_steps: list[dict] = []
    tool_names: list[str] = []
    it = 0
    for step in steps_in:
        if str(step.get("source", "")).lower() not in ("agent", "assistant", "model"):
            continue
        calls = step.get("tool_calls") or []
        msg = str(step.get("message") or "")
        obs = _obs_by_call_id(step)
        if calls:
            for k, c in enumerate(calls):
                fn = c.get("function_name") or "?"
                tool_names.append(fn)
                view_steps.append({
                    "iter": it,
                    "tool": fn,
                    "reasoning": _truncate(msg, _MAX_REASON) if k == 0 else "",
                    "args": _truncate(c.get("arguments"), _MAX_ARGS),
                    "result": _truncate(obs.get(c.get("tool_call_id", ""), ""), _MAX_RESULT),
                    "images": [],
                })
                it += 1
        elif msg.strip():
            view_steps.append({
                "iter": it, "tool": "<no_tool_call>",
                "reasoning": "", "args": "",
                "result": _truncate(msg, _MAX_RESULT), "images": [],
            })
            it += 1
        if len(view_steps) >= _MAX_STEPS:
            break
    # distinct, order-preserving
    seen: dict[str, None] = {}
    for t in tool_names:
        seen.setdefault(t, None)
    return view_steps, final_answer, list(seen.keys())


# ── status classification (shared with failure_report_ev2) ──────────────── #
_REFUSAL_MARKERS = ("usage policy", "model_refusal", "unable to respond",
                    "i can't help", "i cannot help", "i can't assist")


def classify_status(rec: dict, final_answer: str | None) -> str:
    # Refusal is checked before the exception: a Usage-Policy refusal makes the
    # agent CLI exit non-zero (NonZeroAgentExitCodeError), so the exception is a
    # symptom of the refusal, not a harness fault — label it "refused".
    fa = (final_answer or "")
    low = fa.lower()
    if any(m in low for m in _REFUSAL_MARKERS):
        return "refused"
    if rec.get("exception_type"):
        return "errored"
    if rec.get("llm_degraded") or not fa.strip():
        return "degraded"
    if rec.get("llm_judge", 0.0) < 0.5:
        return "wrong"
    return "ok"


def baseline_block(rec: dict) -> dict:
    """Build one baseline's trajectory block for the bundle (or a stub)."""
    trial: Path = rec["trial"]
    steps, final_answer, tools = parse_trajectory(trial / "agent" / "trajectory.json")
    status = classify_status(rec, final_answer)
    return {
        "reward": round(rec["reward"], 3),
        "llm_judge": round(rec["llm_judge"], 3),
        "process_judge": round(rec["process_judge"], 3),
        "n_steps": len(steps),
        "n_tool_calls": sum(1 for s in steps if s["tool"] not in ("<no_tool_call>",)),
        "tools": tools,
        "final_answer": _truncate(final_answer, _MAX_ANSWER),
        "llm_rationale": rec.get("llm_rationale", ""),
        "process_rationale": rec.get("process_rationale", ""),
        "status": status,
        "source_tier": rec.get("tier", ""),
        "steps": steps,
    }


# ── main ────────────────────────────────────────────────────────────────── #
def main() -> int:
    task_ids = sorted(p.name for p in TASKS_SRC.iterdir() if p.is_dir())

    # 1. viewer task-record JSONL + generation_stats.json
    SET_DIR.mkdir(parents=True, exist_ok=True)
    records = [task_record(tid) for tid in task_ids]
    jsonl = SET_DIR / f"ScienceVision_{SET_NAME}_final_tasks.jsonl"
    jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n")

    stats = {
        "set": SET_NAME,
        "task_counts": {
            "kept_total": len(records),
            "generated_total": len(records),
            "kept_ratio": 1.0,
        },
        "token_usage": {"total_tokens_total": 0},
        "cost": {"cost_usd_total": 0.0},
        "note": "ev2 pilot: 15 3D-grounded QA tasks run across 3 harbor baselines "
                "(cc/cc2d/ccmcp). Per-question trajectories in verify_ev2_pilot.json.",
    }
    (SET_DIR / "generation_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))

    # 2. multi-baseline verify bundle
    collected = {b: collect_trials(b) for b in BASELINES}
    tasks_out: dict[str, dict] = {}
    for tid in task_ids:
        per: dict[str, Any] = {}
        for b in BASELINES:
            rec = collected[b].get(tid)
            if rec is None:
                continue
            per[b] = baseline_block(rec)
        tasks_out[tid] = per

    bundle = {
        "schema_version": "1.0",
        "set": SET_NAME,
        "kind": "multi",
        "baselines": BASELINES,
        "baseline_labels": BASELINE_LABELS,
        "tasks": tasks_out,
    }
    BUNDLE_OUT.write_text(json.dumps(bundle, ensure_ascii=False, indent=1))

    # summary
    print(f"wrote {jsonl.relative_to(ROOT)} ({len(records)} tasks)")
    print(f"wrote {(SET_DIR / 'generation_stats.json').relative_to(ROOT)}")
    print(f"wrote {BUNDLE_OUT.relative_to(ROOT)}")
    for tid in task_ids:
        cells = []
        for b in BASELINES:
            blk = tasks_out[tid].get(b)
            if blk:
                cells.append(f"{b}={blk['reward']:.3f}/{blk['status']}[{blk['source_tier']}]")
            else:
                cells.append(f"{b}=—")
        print(f"  {tid:42} " + "  ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
