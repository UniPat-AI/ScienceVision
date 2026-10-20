"""Three-way baseline comparison report for the ev2 pilot.

Baselines (same 15 3D-grounded QA tasks each):
  cc     Codex, plain          image sci3d-codex      raw 3D file, no MCP
  cc2d   Codex + 2D views      image sci3d-codex-2d   pre-rendered PNGs only
  ccmcp  Codex + MCP (3D)      image sci3d-codex-mcp  raw 3D file + sci3d MCP tools

Score source per task, in priority order:
  1. a completed *rerun* trial (jobs-ev2-codex-<b>-rerun/*), preferred because it
     replaces an original errored / setup-timeout trial.
  2. a legacy offline-rescored score (verifier/sci3d_scores_rescored.json).
  3. the original degraded score (verifier/sci3d_scores.json) — reward=0.100
     floor; only used if nothing better exists.

Emits <out>.json (machine) and <out>.md (human).
"""
from __future__ import annotations

import glob
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

BASELINES = {
    "cc": "Codex (plain, raw 3D file, no MCP)",
    "cc2d": "Codex + 2D multiview (pre-rendered PNGs only)",
    "ccmcp": "Codex + MCP (raw 3D file + sci3d 3D tools)",
}
ROOT = Path(__file__).resolve().parents[2]


def _mean(xs):
    return round(st.mean(xs), 4) if xs else 0.0


def _load_score(path: Path):
    """Return (reward, llm_judge, process_judge, tool_names, degraded) or None."""
    try:
        j = json.loads(path.read_text())
    except Exception:
        return None
    lj = j.get("llm_judge") or {}
    pj = j.get("process_judge") or {}
    degraded = bool(lj.get("degraded") or lj.get("error"))
    # Tri-state, carried alongside the score because it is the gate's verdict on
    # the *trajectory*, not on which scoring source produced the number. A
    # legacy rescore file has no gate section, so its trials read as ungated --
    # correct, since nothing certified them.
    gate = j.get("process_gate")
    citable = gate.get("citable") if isinstance(gate, dict) else None
    return {
        "task_id": j.get("task_id", ""),
        "reward": float(j.get("reward", 0.0)),
        "llm_judge": float(lj.get("value", 0.0)),
        "process_judge": float(pj.get("value", 0.0)),
        "rationale": (lj.get("raw") or {}).get("rationale", ""),
        "tool_names": j.get("tool_names") or [],
        "degraded": degraded,
        "citable": citable if isinstance(citable, bool) else None,
        "src": path.name,
    }


def _task_id_from_dir(d: Path) -> str:
    # trial dir: sv-chem-chirality-07__hCNdraM  (id truncated at 32 chars by harbor)
    return d.name.rsplit("__", 1)[0]


def collect_baseline(b: str) -> dict:
    """task_id -> best score record for baseline b."""
    main_dir = sorted(glob.glob(str(ROOT / f"jobs-ev2-codex-{b}" / "*")))
    rerun_dir = sorted(glob.glob(str(ROOT / f"jobs-ev2-codex-{b}-rerun" / "*")))
    by_task: dict[str, dict] = {}

    # tier 2/3: original job (rescored preferred, then degraded).
    # Key on the full task_id from the score file — harbor truncates the trial
    # DIR name to 32 chars, which would collapse e.g. docking-pose-01/-02.
    if main_dir:
        for trial in sorted(Path(main_dir[-1]).glob("sv-*")):
            rec = _load_score(trial / "verifier" / "sci3d_scores_rescored.json") \
                or _load_score(trial / "verifier" / "sci3d_scores.json")
            if rec and rec["task_id"]:
                rec["tier"] = "rescored" if "rescored" in rec["src"] else "degraded"
                by_task[rec["task_id"]] = rec

    # tier 1: rerun overrides, if it produced a real (non-degraded) score.
    # Prefer the offline-rescored rerun file (working judge); the rerun's own
    # sci3d_scores.json is still degraded (the separate verifier container never
    # got SCI3D_JUDGE_MODEL). Only override when non-degraded — a rerun that
    # itself degraded/errored must not replace a good original rescored score.
    if rerun_dir:
        for trial in sorted(Path(rerun_dir[-1]).glob("sv-*")):
            rec = _load_score(trial / "verifier" / "sci3d_scores_rescored.json") \
                or _load_score(trial / "verifier" / "sci3d_scores.json")
            if rec and rec["task_id"] and not rec["degraded"]:
                rec["tier"] = "rerun"
                by_task[rec["task_id"]] = rec
    return by_task


def domain_of(task_id: str) -> str:
    # sv-<domain3>-...  biol/chem/medi -> biology/chemistry/medical
    seg = task_id.split("-")[1] if "-" in task_id else ""
    return {"biol": "biology", "chem": "chemistry", "medi": "medical"}.get(seg, seg)


def build_summary(data: dict) -> dict:
    all_tasks = sorted({t for d in data.values() for t in d})
    summary = {"baselines": {}, "per_domain": {}, "per_task": {}, "tiers": {}}
    for b in BASELINES:
        recs = list(data[b].values())
        # The means average citable trials only -- same rule as the leaderboards.
        # Which scoring source produced a number is orthogonal to whether the
        # trajectory behind it was certified, so the tier counts below stay on
        # the full set: they describe the recovery ladder, not the headline.
        head = [r for r in recs if r.get("citable") is True]
        summary["baselines"][b] = {
            "label": BASELINES[b],
            "n": len(head),
            "n_all": len(recs),
            "blocked": sum(1 for r in recs if r.get("citable") is False),
            "ungated": sum(1 for r in recs if r.get("citable") is None),
            "reward": _mean([r["reward"] for r in head]),
            "content": _mean([r["llm_judge"] for r in head]),
            "process": _mean([r["process_judge"] for r in head]),
            "reward_all": _mean([r["reward"] for r in recs]),
        }
        summary["tiers"][b] = {
            tier: sum(1 for r in recs if r.get("tier") == tier)
            for tier in ("rerun", "rescored", "degraded")
        }
        dd = defaultdict(list)
        for t, r in data[b].items():
            if r.get("citable") is True:
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


def render_md(summary: dict) -> str:
    L = []
    L.append("## ev2 three-way baseline — reward / content / process\n")
    L.append("| baseline | n (citable/all) | blocked | never gated | reward | reward (all) "
             "| content | process | tiers (rerun/rescored/degraded) |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for b in BASELINES:
        s = summary["baselines"][b]
        ti = summary["tiers"][b]
        L.append(f"| {s['label']} | {s['n']}/{s['n_all']} | {s['blocked']} | {s['ungated']} "
                 f"| {s['reward']:.3f} | {s['reward_all']:.3f} | {s['content']:.3f} "
                 f"| {s['process']:.3f} | {ti['rerun']}/{ti['rescored']}/{ti['degraded']} |")
    L.append("")
    L.append("`reward` averages citable trials only; `reward (all)` is the same mean over "
             "every trial collected. *blocked* means the process gate found positive "
             "evidence of a forbidden capability in the agent's own command, *never gated* "
             "that no verdict was recorded -- a legacy rescore file carries no gate "
             "section. They are counted apart because \"we looked and it failed\" and "
             "\"we did not look\" are different facts.")
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


if __name__ == "__main__":
    main()
