"""Offline-rescore trials with a working judge model.

Why: the in-run verifier still 503s on the *dated* judge id
(`claude-haiku-4-5-20251001`) because `--ae SCI3D_JUDGE_MODEL=...` only injects
the AGENT container's env, not the (environment_mode=separate) verifier. So a
trial's own `sci3d_scores.json` is still `degraded`. Mirror the original
pipeline: re-score offline against each trial's agent/ dir with a working judge,
writing `sci3d_scores_rescored.json`. `report_ev2_threeway.py` prefers that file.

The judge model and the job/tasks layout are both configurable:
  - `--judge` (default: the undated `claude-haiku-4-5` alias; env
    `SCI3D_RESCORE_JUDGE` overrides the default). Use e.g.
    `--judge claude-opus-4-8` to rescore an entire matrix with a stronger judge
    -- but keep one judge across ALL baselines you compare (a mixed haiku/opus
    leaderboard is not comparable).
  - `--baselines`, `--jobs-tmpl`, `--rerun-tmpl`, `--tasks-tmpl` retarget the
    two shipped layouts:
      three-way (default): jobs-ev2-{b}/<ts>/sv-*  (+ jobs-ev2-{b}-rerun/*/sv-*),
        task meta under scripts/harbor/tasks-ev2-{b}/<task_id>/.
      six-way end2end:     --baselines cc,ccmcp,cc2d-1,cc2d-3,cc2d-6,cc2d-12
        --jobs-tmpl 'jobs-e2e/job-{b}/*' --rerun-tmpl '' --tasks-tmpl 'jobs-e2e/tasks-{b}'

For each baseline only the LATEST main job dir (sorted last) is rescored, plus
every rerun dir when --rerun-tmpl is non-empty.

Run with the harbor venv python (3.12) and API creds in env:
    source ~/.bashrc
    env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
        -u HTTPS_PROXY -u https_proxy \
        ~/.venv-harbor/bin/python -m scripts.harbor.rescore_rerun
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import tomllib
from pathlib import Path

from harbor_adapters.sci3d._score_core import score_rewards

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_JUDGE = "claude-haiku-4-5"
DEFAULT_BASELINES = ("cc", "cc2d", "ccmcp")
DEFAULT_JOBS_TMPL = "jobs-ev2-{b}/*"
DEFAULT_RERUN_TMPL = "jobs-ev2-{b}-rerun/*"
DEFAULT_TASKS_TMPL = "scripts/harbor/tasks-ev2-{b}"


def task_meta(tasks_dir: Path, task_id: str) -> tuple[dict, str]:
    tdir = tasks_dir / task_id
    meta = tomllib.loads((tdir / "task.toml").read_text()).get("metadata", {}) or {}
    instr = (tdir / "instruction.md").read_text() if (tdir / "instruction.md").exists() else ""
    return meta, instr


def rescore_trial(b: str, trial: Path, *, judge: str, tasks_dir: Path) -> bool:
    """Rescore one trial dir with the given judge; returns True if it wrote a file.

    Shared body for both the latest-main-dir loop and the rerun-dir loop below,
    so the two sources stay in lockstep instead of duplicating the score_rewards
    call.
    """
    score_f = trial / "verifier" / "sci3d_scores.json"
    if not score_f.exists():
        print(f"skip (no verifier score yet): {b}/{trial.name}")
        return False
    task_id = json.loads(score_f.read_text()).get("task_id", "")
    if not task_id:
        print(f"skip (no task_id): {b}/{trial.name}")
        return False
    meta, instr = task_meta(tasks_dir, task_id)
    rewards, audit = score_rewards(
        meta,
        task_id=task_id,
        instruction=instr,
        agent_dir=trial / "agent",
        judge_model=judge,
    )
    out = trial / "verifier" / "sci3d_scores_rescored.json"
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    lj = (audit.get("llm_judge") or {}).get("value")
    deg = (audit.get("llm_judge") or {}).get("degraded")
    print(f"{b:8} {task_id:38} reward={audit.get('reward'):.3f} "
          f"llm_judge={lj} degraded={deg}")
    return True


def iter_trials(baselines, *, jobs_tmpl: str, rerun_tmpl: str):
    """Yield (baseline, trial_dir): the latest main job dir + any reruns."""
    for b in baselines:
        main_dirs = sorted(glob.glob(str(ROOT / jobs_tmpl.format(b=b))))
        if main_dirs:
            for trial in sorted(Path(main_dirs[-1]).glob("sv-*")):
                yield b, trial
        if rerun_tmpl:
            for rerun in sorted(glob.glob(str(ROOT / rerun_tmpl.format(b=b)))):
                for trial in sorted(Path(rerun).glob("sv-*")):
                    yield b, trial


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline-rescore trials with a working judge.")
    ap.add_argument("--judge", default=os.environ.get("SCI3D_RESCORE_JUDGE") or DEFAULT_JUDGE,
                    help=f"judge model id (default: env SCI3D_RESCORE_JUDGE or {DEFAULT_JUDGE})")
    ap.add_argument("--baselines", default=",".join(DEFAULT_BASELINES),
                    help="comma-separated baseline labels")
    ap.add_argument("--jobs-tmpl", default=DEFAULT_JOBS_TMPL,
                    help="glob template (relative to repo root, {b}=baseline) for the main job dir")
    ap.add_argument("--rerun-tmpl", default=DEFAULT_RERUN_TMPL,
                    help="glob template for rerun dirs; empty string disables the rerun pass")
    ap.add_argument("--tasks-tmpl", default=DEFAULT_TASKS_TMPL,
                    help="dir template (relative to repo root, {b}=baseline) holding <task_id>/task.toml")
    args = ap.parse_args(argv)

    baselines = [x for x in args.baselines.split(",") if x]
    n = 0
    for b, trial in iter_trials(baselines, jobs_tmpl=args.jobs_tmpl, rerun_tmpl=args.rerun_tmpl):
        tasks_dir = ROOT / args.tasks_tmpl.format(b=b)
        if rescore_trial(b, trial, judge=args.judge, tasks_dir=tasks_dir):
            n += 1
    print(f"\nrescored {n} trials with judge={args.judge}")


if __name__ == "__main__":
    main()
