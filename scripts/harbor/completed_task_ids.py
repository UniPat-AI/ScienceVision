"""List the task_ids that already have a finished trial in a harbor job dir.

Used by the staged runners to resume: a relaunch (after a crash, a kill, or a
concurrency change) passes these back as `--exclude-task-name` patterns so the
trials already paid for are not re-run.

The task_id is read from each trial's ``result.json`` (``task_name``, of the
form ``sci3d/<task_id>``) rather than derived from the trial directory name —
harbor truncates long task names in the directory (e.g.
``sv-biol-bio_multi_structure_comp__bVLHpx`` for task
``sv-biol-bio_multi_structure_compare-0012``), so the directory name is lossy.

A trial counts as finished when it has a ``result.json``, including one that
recorded an exception: an AgentTimeoutError is a real measurement of that
baseline, not a transient to retry. Pass --exclude-exceptions to retry those
instead.

Usage:
  python -m scripts.harbor.completed_task_ids --job-root jobs-sample50/job-cc
  python -m scripts.harbor.completed_task_ids --job-root jobs-sample50/job-cc --as-glob
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def completed_task_ids(job_root: Path, *, skip_exceptions: bool = False) -> list[str]:
    """task_ids with a finished trial under any timestamped run of `job_root`."""
    ids: set[str] = set()
    for result in sorted(job_root.glob("*/*/result.json")):
        try:
            data = json.loads(result.read_text())
        except (OSError, json.JSONDecodeError):
            continue  # a trial killed mid-write
        if skip_exceptions and data.get("exception_info"):
            continue
        name = data.get("task_name") or ""
        task_id = name.split("/", 1)[1] if "/" in name else name
        if not task_id:
            # Fall back to the staged task dir recorded in the trial config.
            path = ((data.get("task") or {}).get("path")) or ""
            task_id = Path(path).name
        if task_id:
            ids.add(task_id)
    return sorted(ids)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job-root", type=Path, required=True, help="jobs-<set>/job-<baseline>")
    ap.add_argument("--exclude-exceptions", action="store_true",
                    help="treat trials that raised as NOT completed, so they re-run")
    ap.add_argument("--as-glob", action="store_true",
                    help="print `*<task_id>`, matching whether harbor's task name "
                         "is the bare id or the `sci3d/<id>` form from task.toml")
    args = ap.parse_args(argv)

    if not args.job_root.is_dir():
        return
    for task_id in completed_task_ids(args.job_root, skip_exceptions=args.exclude_exceptions):
        print(f"*{task_id}" if args.as_glob else task_id)


if __name__ == "__main__":
    main()
