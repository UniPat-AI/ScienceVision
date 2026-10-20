"""Collect a Harbor job's per-trial results back onto the QA-Gen tasks.

After `harbor run` over QA-Gen tasks, this walks the job directory and, for each
trial, writes a compact run record next to the originating QA-Gen task set:

    QA-Gen/tasks/<set>/runs/<task_id>.json
        { task_id, set, model, status, answer, ground_truth,
          rewards{...}, trajectory[ {step, tools, message, observation} ] }

and a roll-up leaderboard across all collected runs:

    QA-Gen/tasks/runs_leaderboard.json   (machine-readable)
    QA-Gen/tasks/runs_leaderboard.md     (human-readable table)

The trajectory is the same truncated per-step summary the TrajectoryJudge sees
(reasoning + tool calls + observation), so a reviewer can read *how* the agent
answered right beside the task it answered.

Usage:
    python -m scripts.harbor.collect_qagen_results \
        --job /tmp/qagen_jobs_all --tasks-root QA-Gen/tasks \
        [--model claude-sonnet-4-6]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harbor_adapters.sci3d._verify_translate import (
    _final_message_from_traj,
    _parse_atif_steps,
)
from scripts.harbor._citability import headline_rows
from scripts.harbor.eval_contracts import normalize_eval_contract

# task_id -> QA-Gen set directory name
_SETS = {
    "MedicalKAxisPilot": "ScienceVision_MedicalKAxisPilot_final_tasks.jsonl",
    "Genesis_VP_Hard": "ScienceVision_Genesis_VP_Hard_final_tasks.jsonl",
}


def _index_sets(tasks_root: Path) -> dict[str, dict[str, dict]]:
    """task_id -> {"set": name, "record": qa_gen_record}."""
    index: dict[str, dict] = {}
    # Auto-discover any <set>/*_final_tasks.jsonl first; hardcoded _SETS win on
    # collision (added last).
    discovered: list[tuple[str, Path]] = [
        (p.parent.name, p) for p in sorted(tasks_root.glob("*/*_final_tasks.jsonl"))
    ]
    hardcoded: list[tuple[str, Path]] = [
        (name, tasks_root / name / jsonl) for name, jsonl in _SETS.items()
    ]
    for set_name, path in discovered + hardcoded:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            index[rec["task_id"]] = {"set": set_name, "record": rec}
    return index


def _trial_dirs(job_dir: Path) -> list[Path]:
    """Trial directories: those containing a result.json with a task_name."""
    out = []
    for rf in sorted(job_dir.rglob("result.json")):
        # skip the job-level result.json (no per-trial agent dir beside it)
        if (rf.parent / "agent").exists() or (rf.parent / "verifier").exists():
            out.append(rf.parent)
    return out


def collect_one(trial_dir: Path, index: dict, model: str, harness: str) -> dict | None:
    result = json.loads((trial_dir / "result.json").read_text())
    task_name = result.get("task_name") or ""
    task_id = task_name.split("/")[-1]
    entry = index.get(task_id)
    rec = entry["record"] if entry else {}

    vr = result.get("verifier_result") or {}
    rewards = vr.get("rewards") if isinstance(vr, dict) else None

    scores_f = trial_dir / "verifier" / "sci3d_scores.json"
    score_audit: dict[str, Any] = {}
    if scores_f.exists():
        try:
            score_audit = json.loads(scores_f.read_text())
        except (json.JSONDecodeError, OSError):
            score_audit = {}

    # Legacy offline judge rescoring may still be present.  It is shadow-only:
    # it can enrich diagnostics but can never overwrite deterministic reward.
    rescored_f = trial_dir / "verifier" / "sci3d_scores_rescored.json"
    if rescored_f.exists():
        try:
            j = json.loads(rescored_f.read_text())
        except (json.JSONDecodeError, OSError):
            j = None
        if j is not None:
            rewards = dict(rewards or {})
            if "answer_score" in j:
                answer = float((j.get("answer_score") or {}).get("value", 0.0))
                rewards["answer_score"] = answer
                rewards["reward"] = answer
            if "llm_judge" in j:
                rewards["shadow_llm_judge"] = float((j["llm_judge"] or {}).get("value", 0.0))
            if "process_judge" in j:
                rewards["shadow_process_judge"] = float((j["process_judge"] or {}).get("value", 0.0))

    exc = result.get("exception_info")
    status = "ok" if rewards else ("error" if exc else "incomplete")

    # Answer + trajectory: Sci3dAgent writes sci3d_answer/sci3d_trajectory; the
    # Claude Code CLI agent writes only the standard trajectory.json. Try both.
    agent_dir = trial_dir / "agent"
    answer = None
    ans_path = agent_dir / "sci3d_answer.json"
    if ans_path.exists():
        try:
            answer = json.loads(ans_path.read_text()).get("answer")
        except json.JSONDecodeError:
            answer = None

    traj_path = None
    for cand in ("sci3d_trajectory.json", "trajectory.json"):
        if (agent_dir / cand).exists():
            traj_path = agent_dir / cand
            break
    steps = list(_parse_atif_steps(traj_path)) if traj_path else []
    if answer is None and traj_path is not None:
        answer = _final_message_from_traj(traj_path)

    shots = sorted(str(p) for p in (agent_dir / "renders").glob("*.png"))

    gt = rec.get("ground_truth") or {}
    evaluation_eligible = score_audit.get("evaluation_eligible")
    if not isinstance(evaluation_eligible, bool):
        evaluation_eligible = bool(
            normalize_eval_contract(rec).get("evaluation_eligible")
        ) if rec else False
    return {
        "task_id": task_id,
        "set": entry["set"] if entry else "_unknown",
        "domain": rec.get("domain", ""),
        "v_class": rec.get("v_class") or rec.get("k_class") or "",
        "harness": harness,
        "model": model,
        "status": status,
        "instruction": rec.get("instruction", ""),
        "answer": answer,
        "ground_truth": gt.get("value") if isinstance(gt, dict) else gt,
        "evaluation_eligible": evaluation_eligible,
        "rewards": rewards or {},
        # The process gate's verdict lives in the audit, not the reward channel
        # (``citable`` is a bool and ``blockers`` a list). Carried here so the
        # leaderboard can show it -- a signal only in sci3d_scores.json is a
        # signal nobody reads.
        "process_gate": score_audit.get("process_gate") or {},
        "error": (exc.get("exception_message") if isinstance(exc, dict) else None),
        "trajectory": steps,
        "screenshots_src": shots,
    }


def _run_slug(run: dict) -> str:
    """Filename-safe <harness>__<model>__<task_id> so harnesses don't clash."""
    def safe(value: Any) -> str:
        return str(value).replace("/", "-").replace(":", "-")

    return f"{safe(run['harness'])}__{safe(run['model'])}__{run['task_id']}"


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


def build_leaderboard(runs: list[dict], tasks_root: Path) -> dict:
    rows = []
    for r in runs:
        rw = r["rewards"]
        gate = r.get("process_gate") if isinstance(r.get("process_gate"), dict) else {}
        rows.append(
            {
                "task_id": r["task_id"],
                "set": r["set"],
                "domain": r["domain"],
                "v_class": r["v_class"],
                "harness": r.get("harness", "sci3d-agent"),
                "model": r.get("model", ""),
                "status": r["status"],
                "evaluation_eligible": r.get("evaluation_eligible") is True,
                "reward": rw.get("reward"),
                "answer_score": rw.get("answer_score"),
                "process_compliance": rw.get("process_compliance"),
                "shadow_llm_judge": rw.get("shadow_llm_judge"),
                "shadow_process_judge": rw.get("shadow_process_judge"),
                # Process gate. ``None`` throughout for a run scored before the
                # gate existed: absent and 0.0 mean opposite things, and a 0.0
                # here would read as "checked, and it failed".
                "capability_policy": rw.get("process_capability_policy"),
                "capability_coverage": rw.get("process_capability_coverage"),
                "mcp_attempted": rw.get("process_mcp_attempted"),
                "mcp_success_rate": rw.get("process_mcp_success_rate"),
                "citable": gate.get("citable") if isinstance(gate.get("citable"), bool) else None,
                "blockers": list(gate.get("blockers") or []),
            }
        )
    rows.sort(
        key=lambda x: (x["harness"], x["model"], x["reward"] is None, -(x["reward"] or 0))
    )

    completed = [x for x in rows if x["reward"] is not None]
    ok = [x for x in completed if x["evaluation_eligible"]]

    # ── the headline set ─────────────────────────────────────────────────── #
    # The gate's verdict removes a run from the headline rather than annotating
    # it. Averaging a blocked run in is how a ``cc`` shortcut scored 0.932 into
    # the medical column: the answer exists, but it is not evidence of
    # perception. The unfiltered means are kept beside the filtered ones so the
    # size of the exclusion stays auditable instead of invisible.
    kept, census = headline_rows(ok, lambda x: x["citable"])

    # Per-group census, because a global one cannot tell a row resting on 12 of
    # 12 trials from one resting on 1 of 12 -- and the filtered reward column
    # looks identical in both cases.
    hm_census: dict[str, dict[str, int]] = {}
    for x in ok:
        entry = hm_census.setdefault(
            f"{x['harness']} / {x['model']}",
            {"n_eligible": 0, "n_citable": 0, "n_blocked": 0, "n_ungated": 0},
        )
        entry["n_eligible"] += 1
        if x["citable"] is True:
            entry["n_citable"] += 1
        elif x["citable"] is False:
            entry["n_blocked"] += 1
        else:
            entry["n_ungated"] += 1

    # group means by (harness, model) and by (harness, domain)
    hm: dict[str, list[float]] = {}
    hd: dict[str, list[float]] = {}
    hm_all: dict[str, list[float]] = {}
    hd_all: dict[str, list[float]] = {}
    for x in ok:
        hm_all.setdefault(f"{x['harness']} / {x['model']}", []).append(x["reward"])
        hd_all.setdefault(
            f"{x['harness']} / {x['domain'] or '_unknown'}", []
        ).append(x["reward"])
    for x in kept:
        hm.setdefault(f"{x['harness']} / {x['model']}", []).append(x["reward"])
        hd.setdefault(f"{x['harness']} / {x['domain'] or '_unknown'}", []).append(
            x["reward"]
        )
    by_harness_model = {k: sum(v) / len(v) for k, v in hm.items()}
    by_harness_domain = {k: sum(v) / len(v) for k, v in hd.items()}
    by_harness_model_all = {k: sum(v) / len(v) for k, v in hm_all.items()}
    by_harness_domain_all = {k: sum(v) / len(v) for k, v in hd_all.items()}

    # Deterministic answer and process compliance means, same grouping and the
    # same headline set -- ``reward`` equals ``answer_score``, so filtering one
    # and not the other would put two different numbers under one name.
    hm_a: dict[str, list[float]] = {}
    hm_p: dict[str, list[float]] = {}
    for x in kept:
        key = f"{x['harness']} / {x['model']}"
        if isinstance(x.get("answer_score"), (int, float)):
            hm_a.setdefault(key, []).append(x["answer_score"])
        if isinstance(x.get("process_compliance"), (int, float)):
            hm_p.setdefault(key, []).append(x["process_compliance"])
    by_harness_model_mean_answer = {k: sum(v) / len(v) for k, v in hm_a.items()}
    by_harness_model_mean_process = {k: sum(v) / len(v) for k, v in hm_p.items()}

    # ── process gate means, each over its own honest denominator ─────────── #
    # MCP success is averaged over the runs that *attempted* MCP only. Averaging
    # ``cc`` in (no MCP by construction, rate 0.0) would drag every mean toward
    # zero and make a healthy ccmcp look broken -- the mirror of crediting a
    # zero-attempt route with a perfect 1.0.
    hm_cov: dict[str, list[float]] = {}
    hm_mcp: dict[str, list[float]] = {}
    hm_cit: dict[str, list[float]] = {}
    for x in ok:
        key = f"{x['harness']} / {x['model']}"
        if isinstance(x.get("capability_coverage"), (int, float)):
            hm_cov.setdefault(key, []).append(float(x["capability_coverage"]))
        if x.get("mcp_attempted") == 1.0 and isinstance(
            x.get("mcp_success_rate"), (int, float)
        ):
            hm_mcp.setdefault(key, []).append(float(x["mcp_success_rate"]))
        # Only gated runs count; a legacy run has nothing to be citable about.
        if isinstance(x.get("citable"), bool):
            hm_cit.setdefault(key, []).append(1.0 if x["citable"] else 0.0)
    by_harness_model_mean_capability_coverage = {
        k: sum(v) / len(v) for k, v in hm_cov.items()
    }
    by_harness_model_mean_mcp_success = {k: sum(v) / len(v) for k, v in hm_mcp.items()}
    by_harness_model_citable_rate = {k: sum(v) / len(v) for k, v in hm_cit.items()}

    board = {
        "n_runs": len(rows),
        "n_completed": len(completed),
        # How the eligible set split. Read this before any mean below: it is the
        # only place that says whether a route's headline rests on all of its
        # trials, on some, or on none.
        "citability": {**census.as_dict(), "note": census.note()},
        "by_harness_model_mean_reward": by_harness_model,
        "by_harness_model_mean_reward_all_eligible": by_harness_model_all,
        "by_harness_model_census": hm_census,
        "n_eligible_completed": len(ok),
        "by_harness_model_mean_answer": by_harness_model_mean_answer,
        "by_harness_model_mean_process": by_harness_model_mean_process,
        "by_harness_model_mean_capability_coverage":
            by_harness_model_mean_capability_coverage,
        "by_harness_model_mean_mcp_success": by_harness_model_mean_mcp_success,
        "by_harness_model_citable_rate": by_harness_model_citable_rate,
        "by_harness_domain_mean_reward": by_harness_domain,
        "by_harness_domain_mean_reward_all_eligible": by_harness_domain_all,
        "rows": rows,
    }
    (tasks_root / "runs_leaderboard.json").write_text(
        json.dumps(board, ensure_ascii=False, indent=2)
    )
    (tasks_root / "runs_leaderboard.md").write_text(_leaderboard_md(board))
    return board


def _fmt(v: Any) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "—"


def _bool(v: Any) -> str:
    """Tri-state: a run scored before the process gate existed is neither."""
    return "yes" if v is True else ("no" if v is False else "—")


def _mcp(row: dict) -> str:
    """The success rate, or "n/a" when the route never called MCP.

    0.000 and "never tried" are different facts and the rate alone cannot tell
    them apart, so the qualifier is rendered rather than the bare number.
    """
    if row.get("mcp_attempted") == 0.0:
        return "n/a"
    return _fmt(row.get("mcp_success_rate"))


def _leaderboard_md(board: dict) -> str:
    headline = board["by_harness_model_mean_reward"]
    all_eligible = board.get("by_harness_model_mean_reward_all_eligible", {})
    # Rows come from the union, not from the headline alone. A route whose every
    # trial was excluded has no headline value, and dropping it from the table
    # would read as "not run" rather than "not citable" -- the opposite of what
    # the filter is for.
    keys = sorted(
        set(headline) | set(all_eligible),
        key=lambda k: (-(headline.get(k) if headline.get(k) is not None else -1.0), k),
    )
    census = board.get("citability") or {}
    lines = [
        "# QA-Gen Harbor run leaderboard",
        "",
        f"- runs: {board['n_runs']}  (completed: {board['n_completed']})",
        f"- citability: {census.get('note', 'not recorded')}",
        "",
        "## Mean by harness / model (deterministic answer · process gate)",
        "",
        "`reward` is the headline and averages citable runs only; `reward (all"
        " eligible)` is the same mean over every eligible run, kept beside it so"
        " the size of the exclusion is auditable. A route showing a dash in the"
        " first column and a number in the second has no citable trial at all."
        " `n` is the headline denominator -- citable over eligible -- so a row"
        " resting on one trial of twelve reads as thin; `blocked` and `ungated`"
        " are counted apart because \"we looked and it failed\" and \"we did not"
        " look\" are different facts. `citable` is the rate over runs the gate"
        " actually scored, `coverage` is the fraction of declared capabilities a"
        " signature can check -- a high citable rate at low coverage means little"
        " was verified. `mcp ok` averages only the runs that attempted MCP.",
        "",
        "| harness / model | reward | reward (all eligible) | n | blocked | ungated "
        "| answer | process compliance | citable | coverage | mcp ok |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    census_by_group = board.get("by_harness_model_census", {})
    for k in keys:
        c = board["by_harness_model_mean_answer"].get(k)
        p = board["by_harness_model_mean_process"].get(k)
        cit = board.get("by_harness_model_citable_rate", {}).get(k)
        cov = board.get("by_harness_model_mean_capability_coverage", {}).get(k)
        mcp = board.get("by_harness_model_mean_mcp_success", {}).get(k)
        g = census_by_group.get(k, {})
        # "n" is the headline denominator -- citable over eligible -- so a row
        # resting on one trial out of twelve is visibly thin rather than merely
        # different.
        n = f"{g.get('n_citable', 0)}/{g.get('n_eligible', 0)}"
        lines.append(
            f"| {k} | {_fmt(headline.get(k))} | {_fmt(all_eligible.get(k))} | "
            f"{n} | {g.get('n_blocked', 0)} | {g.get('n_ungated', 0)} | "
            f"{_fmt(c)} | {_fmt(p)} | "
            f"{_fmt(cit)} | {_fmt(cov)} | {_fmt(mcp)} |"
        )
    lines += [
        "",
        "## Mean reward by harness / domain",
        "",
        "| harness / domain | reward | reward (all eligible) |",
        "|------------------|--------|-----------------------|",
    ]
    domain_all = board.get("by_harness_domain_mean_reward_all_eligible", {})
    domain_keys = sorted(
        set(board["by_harness_domain_mean_reward"]) | set(domain_all)
    )
    for k in domain_keys:
        lines.append(
            f"| {k} | {_fmt(board['by_harness_domain_mean_reward'].get(k))} | "
            f"{_fmt(domain_all.get(k))} |"
        )
    lines += [
        "",
        "## Per-task",
        "",
        "| harness | model | task | domain | v_class | eligible | status | reward | answer | process | shadow answer | shadow process | citable | capability | coverage | mcp ok | blockers |",
        "|---------|-------|------|--------|---------|----------|--------|--------|--------|---------|---------------|----------------|---------|------------|----------|--------|----------|",
    ]
    for x in board["rows"]:
        lines.append(
            f"| {x['harness']} | {x['model']} | {x['task_id']} | {x['domain']} | "
            f"{x['v_class']} | {x['evaluation_eligible']} | {x['status']} | {_fmt(x['reward'])} | "
            f"{_fmt(x['answer_score'])} | {_fmt(x['process_compliance'])} | "
            f"{_fmt(x['shadow_llm_judge'])} | {_fmt(x['shadow_process_judge'])} | "
            f"{_bool(x.get('citable'))} | {_fmt(x.get('capability_policy'))} | "
            f"{_fmt(x.get('capability_coverage'))} | {_mcp(x)} | "
            f"{', '.join(x.get('blockers') or []) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="HARNESS:MODEL:JOBDIR",
        help="a harbor job to collect, tagged with its harness + model. "
        "Repeatable to build one leaderboard across harnesses, e.g. "
        "--run sci3d-agent:claude-sonnet-4-6:/tmp/job1 "
        "--run claude-code:claude-opus-4-8:/tmp/job2",
    )
    ap.add_argument("--job", type=Path, help="single job dir (shorthand)")
    ap.add_argument("--harness", default="sci3d-agent", help="harness for --job")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="model for --job")
    ap.add_argument("--tasks-root", type=Path, default=Path("QA-Gen/tasks"))
    args = ap.parse_args()

    # Normalize into a list of (harness, model, jobdir).
    jobs: list[tuple[str, str, Path]] = []
    for spec in args.run:
        harness, model, jobdir = spec.split(":", 2)
        jobs.append((harness, model, Path(jobdir)))
    if args.job:
        jobs.append((args.harness, args.model, args.job))
    if not jobs:
        ap.error("provide at least one --run HARNESS:MODEL:JOBDIR or --job")

    index = _index_sets(args.tasks_root)
    runs = []
    for harness, model, jobdir in jobs:
        for td in _trial_dirs(jobdir):
            run = collect_one(td, index, model, harness)
            if run:
                runs.append(run)

    write_runs(runs, args.tasks_root)
    board = build_leaderboard(runs, args.tasks_root)
    print(f"collected {len(runs)} run(s); {board['n_completed']} completed")
    for k, m in sorted(board["by_harness_model_mean_reward"].items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {_fmt(m)}")
    print(f"leaderboard -> {args.tasks_root / 'runs_leaderboard.md'}")


if __name__ == "__main__":
    main()
