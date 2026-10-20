"""Stage (and optionally publish) collected QA-Gen run trajectories.

Reorganizes the per-run records under ``QA-Gen/tasks/<set>/runs/`` into a
harness-first layout:

    <stage>/
      <harness>/<set>/<task_id>.json      # answer (+GT) + rewards + trajectory
      runs_leaderboard.json
      runs_leaderboard.md
      README.md                            # auto index of what's inside

Default behavior is **stage only** to a stable in-repo, gitignored store
(QA-Gen/tasks/_trajectory_store) — see TRAJECTORY_STORE.md. These records carry
the benchmark **ground truth**, so they are kept local and NOT published.

Publishing to a public ModelScope dataset is opt-in (--upload) and, because the
records contain answers, is **refused** unless you either:
  --strip-ground-truth   drop ground_truth before upload (recommended), or
  --include-ground-truth explicitly accept publishing the answers.

Auth: reads MODELSCOPE_API_TOKEN from the environment (or ~/.modelscope creds).

Usage:
    # stage locally (default; keeps GT, gitignored)
    python -m scripts.harbor.upload_trajectories_modelscope

    # publish a GT-free copy to ModelScope
    python -m scripts.harbor.upload_trajectories_modelscope \
        --repo-id tianyi3unipat/BabyVision-v2-Trajectory \
        --strip-ground-truth --upload
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from scripts.harbor._citability import citability


def _collect_runs(tasks_root: Path) -> list[tuple[Path, dict]]:
    runs: list[tuple[Path, dict]] = []
    for rf in sorted(tasks_root.rglob("runs/*.json")):
        try:
            runs.append((rf, json.loads(rf.read_text())))
        except (OSError, json.JSONDecodeError):
            continue
    return runs


# Fields dropped from each run when --strip-ground-truth is set, so the public
# upload never leaks the benchmark's answers.
_GT_FIELDS = ("ground_truth",)


def _stage(
    runs: list[tuple[Path, dict]],
    tasks_root: Path,
    stage_dir: Path,
    *,
    strip_ground_truth: bool = False,
) -> dict:
    """Reshape into <harness>/<set>/<task_id>.json; return a small index."""
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    index: dict[str, dict] = {}
    for _, run in runs:
        harness = run.get("harness", "unknown")
        set_name = run.get("set", "_unknown")
        task_id = run.get("task_id", "task")
        out = stage_dir / harness / set_name
        out.mkdir(parents=True, exist_ok=True)
        payload = dict(run)
        if strip_ground_truth:
            for f in _GT_FIELDS:
                payload.pop(f, None)
            payload["ground_truth_stripped"] = True
        (out / f"{task_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2)
        )
        key = f"{harness} / {run.get('model','')}"
        bucket = index.setdefault(
            key,
            {"n": 0, "rewards": [], "citable_rewards": [], "blocked": 0,
             "ungated": 0, "sets": set()},
        )
        bucket["n"] += 1
        bucket["sets"].add(set_name)
        r = (run.get("rewards") or {}).get("reward")
        if isinstance(r, (int, float)):
            bucket["rewards"].append(r)
        # The README this builds is public, so the published mean has to be the
        # citable one -- a shortcut that scored well is not a capability result.
        verdict = citability(run.get("rewards"))
        if verdict is True:
            if isinstance(r, (int, float)):
                bucket["citable_rewards"].append(r)
        elif verdict is False:
            bucket["blocked"] += 1
        else:
            bucket["ungated"] += 1

    # copy leaderboard alongside
    for name in ("runs_leaderboard.json", "runs_leaderboard.md"):
        src = tasks_root / name
        if src.exists():
            shutil.copy(src, stage_dir / name)

    return index


def _write_readme(stage_dir: Path, index: dict, repo_id: str) -> None:
    lines = [
        "# BabyVision-v2 Trajectory",
        "",
        "Agent trajectories for the QA-Gen pilot tasks, run through Harbor with "
        "the sci3d verifier (exact_match + domain LLM-judge + trajectory judge + "
        "process signals).",
        "",
        "Layout: `<harness>/<set>/<task_id>.json` — each file has the agent's "
        "answer, the task ground truth, the full reward dict, and the truncated "
        "trajectory (per-step reasoning + tool calls + observations).",
        "",
        "## Harnesses",
        "",
        "| harness / model | runs | citable | blocked | never gated | mean reward "
        "| mean reward (all) | sets |",
        "|---|--:|--:|--:|--:|--:|--:|---|",
    ]
    for key, b in sorted(index.items()):
        cit = b["citable_rewards"]
        mean = sum(cit) / len(cit) if cit else None
        mean_s = f"{mean:.3f}" if mean is not None else "—"
        all_mean = (
            sum(b["rewards"]) / len(b["rewards"]) if b["rewards"] else None
        )
        all_s = f"{all_mean:.3f}" if all_mean is not None else "—"
        lines.append(
            f"| {key} | {b['n']} | {len(cit)} | {b['blocked']} | {b['ungated']} "
            f"| {mean_s} | {all_s} | {', '.join(sorted(b['sets']))} |"
        )
    lines += [
        "",
        "`mean reward` averages citable runs only: the process gate found no "
        "positive evidence of a forbidden capability in the agent's own commands. "
        "*blocked* runs are excluded for that reason, *never gated* runs for having "
        "no verdict recorded, and the two are counted apart because \"we looked and "
        "it failed\" and \"we did not look\" are different facts. `mean reward "
        "(all)` is the unfiltered mean, shown so the exclusion is auditable.",
        "",
        "See `runs_leaderboard.md` for the per-task table.",
        "",
        f"_Uploaded to `{repo_id}` by scripts/harbor/upload_trajectories_modelscope.py._",
    ]
    (stage_dir / "README.md").write_text("\n".join(lines) + "\n")


# Stable local staging root (NOT /tmp — that is cleared on reboot). The
# ground-truth-bearing trajectories live here and are gitignored; only a
# pointer file is committed. See TRAJECTORY_STORE.md.
_DEFAULT_STAGE = Path("QA-Gen/tasks/_trajectory_store")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-id", help="org/name of the dataset (required to upload)")
    ap.add_argument("--tasks-root", type=Path, default=Path("QA-Gen/tasks"))
    ap.add_argument(
        "--stage-dir",
        type=Path,
        default=_DEFAULT_STAGE,
        help="stable local staging dir (default keeps GT trajectories in-repo, "
        "gitignored)",
    )
    ap.add_argument("--path-in-repo", default="", help="subdir within the dataset")
    ap.add_argument(
        "--strip-ground-truth",
        action="store_true",
        help="drop ground_truth from each run before staging/upload, so the "
        "public dataset never leaks benchmark answers",
    )
    ap.add_argument(
        "--include-ground-truth",
        action="store_true",
        help="explicitly allow uploading WITH ground_truth (off by default; "
        "uploads carrying GT are refused unless this is set)",
    )
    ap.add_argument(
        "--upload",
        action="store_true",
        help="actually upload to --repo-id (default: only stage locally)",
    )
    args = ap.parse_args()

    runs = _collect_runs(args.tasks_root)
    if not runs:
        ap.error(f"no run JSON found under {args.tasks_root}/**/runs/")
    index = _stage(
        runs, args.tasks_root, args.stage_dir,
        strip_ground_truth=args.strip_ground_truth,
    )
    _write_readme(args.stage_dir, index, args.repo_id or "(local only)")

    n_files = sum(1 for _ in args.stage_dir.rglob("*") if _.is_file())
    gt_note = "without ground_truth" if args.strip_ground_truth else "WITH ground_truth"
    print(f"staged {len(runs)} run(s) [{gt_note}] -> {n_files} file(s) under {args.stage_dir}")
    for key, b in sorted(index.items()):
        print(f"  {key}: {b['n']} runs")

    if not args.upload:
        print("staged locally only (pass --upload to publish).")
        return

    if not args.repo_id:
        ap.error("--upload requires --repo-id")
    # Refuse to publish ground truth unless explicitly opted in.
    if not args.strip_ground_truth and not args.include_ground_truth:
        ap.error(
            "refusing to upload trajectories that still contain ground_truth "
            "to a public dataset. Re-run with --strip-ground-truth (recommended) "
            "or --include-ground-truth to override."
        )

    token = os.environ.get("MODELSCOPE_API_TOKEN")
    from modelscope.hub.api import HubApi

    api = HubApi()
    if token:
        api.login(token)
    api.upload_folder(
        repo_id=args.repo_id,
        folder_path=str(args.stage_dir),
        path_in_repo=args.path_in_repo,
        repo_type="dataset",
        commit_message="Add QA-Gen agent trajectories (sci3d-agent + claude-code)",
    )
    print(f"uploaded {n_files} file(s) to dataset {args.repo_id}")


if __name__ == "__main__":
    main()
