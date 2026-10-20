"""Produce ``leak_scan.json`` for a set of Harbor runs.

The companion driver for ``_leak_probe`` (which holds all the detection logic
and its rationale). This module only walks runs and writes the artifact.

    python -m scripts.harbor.leak_scan \
        --run cc:/data/jobs/job-cc \
        --run ccmcp:/data/jobs/job-ccmcp \
        --run cc2d-1:/data/jobs/job-cc2d-1 \
        --tasks-root QA-Gen/tasks \
        --out reports/leak_scan.json

Output schema is byte-compatible with the artifact the 2026-08-14 EV2 report
already reads, plus additive keys:

    per_task[route][task_id] = {curl, fetch_attempt, fetch_url,
                                local_asset_file, steps, scanned, evidence}
    summary[route][domain]   = {curl_or_wget, external_fetch,
                                local_asset_file, unscanned, n}

The per-task/summary key-name mismatch (``curl`` vs ``curl_or_wget``) is
inherited, not a typo — see ``_leak_probe.summarize``.

Read the result as an escalation, not a single verdict: ``curl_or_wget`` is
intent, ``external_fetch`` is a named target, and only ``local_asset_file`` is
evidence the agent actually obtained an out-of-sandbox asset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.harbor._leak_probe import (
    LEAK_SCAN_SCHEMA_VERSION,
    scan_trial,
    summarize,
    unscannable,
)
from scripts.harbor.collect_qagen_results import _index_sets, _trial_dirs


def parse_run_spec(spec: str) -> tuple[str, Path]:
    """``ROUTE:JOBDIR`` or ``ROUTE:MODEL:JOBDIR`` -> (route, job_dir).

    The three-field form is accepted so a caller can paste the same specs it
    passes to ``collect_qagen_results --run HARNESS:MODEL:JOBDIR``; the model is
    not part of the leak-scan key space and is dropped.
    """
    parts = spec.split(":")
    bad = ValueError(
        f"bad --run spec {spec!r}; expected ROUTE:JOBDIR or ROUTE:MODEL:JOBDIR"
    )
    if len(parts) < 2:
        raise bad
    route, rest = parts[0].strip(), parts[1:]
    # Optional MODEL field. A model tag never looks like a path, so that test
    # keeps a job dir containing a colon from being truncated to its last chunk.
    if len(rest) >= 2 and "/" not in rest[0] and not rest[0].startswith("."):
        rest = rest[1:]
    job = ":".join(rest).strip()
    if not route or not job:
        raise bad
    return route, Path(job)


def _task_id_for(trial_dir: Path) -> str:
    """Task id from the trial's result.json, matching collect_one's rule."""
    try:
        result = json.loads((trial_dir / "result.json").read_text())
    except (OSError, json.JSONDecodeError):
        return trial_dir.name
    return str(result.get("task_name") or trial_dir.name).split("/")[-1]


def scan_job(job_dir: Path) -> dict[str, dict[str, Any]]:
    """Scan every trial under one Harbor job. Returns {task_id: row}."""
    job_dir = Path(job_dir)
    if not job_dir.is_dir():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for trial_dir in _trial_dirs(job_dir):
        task_id = _task_id_for(trial_dir)
        row = scan_trial(trial_dir)
        # Repeat trials of one task: keep the worst (a single leak contaminates
        # the task) so the roll-up can never be diluted by clean retries.
        prior = rows.get(task_id)
        if prior is None or _severity(row) > _severity(prior):
            rows[task_id] = {**row, "trial_dir": str(trial_dir)}
    return rows


def _severity(row: dict[str, Any]) -> int:
    if not row.get("scanned"):
        return 0
    return (
        1
        + int(bool(row.get("curl")))
        + int(bool(row.get("fetch_attempt")))
        + 2 * int(bool(row.get("local_asset_file")))
    )


def build_domain_index(tasks_root: Path | None) -> dict[str, str]:
    """task_id -> domain, from the QA-Gen sets. Empty when tasks_root is absent.

    An empty index is not fatal: ``summarize`` files those tasks under
    ``unknown`` rather than dropping them, so a scan still reports every trial.
    """
    if tasks_root is None or not Path(tasks_root).is_dir():
        return {}
    index = _index_sets(Path(tasks_root))
    return {
        task_id: str((entry.get("record") or {}).get("domain") or "unknown")
        for task_id, entry in index.items()
    }


def _render_text(summary: dict[str, dict[str, dict[str, int]]]) -> str:
    lines = [
        "route         domain       curl  fetch  got-file  unscanned  n",
        "-" * 66,
    ]
    for route in sorted(summary):
        for domain in sorted(summary[route]):
            c = summary[route][domain]
            lines.append(
                f"{route:<13} {domain:<12} {c['curl_or_wget']:>4}  "
                f"{c['external_fetch']:>5}  {c['local_asset_file']:>8}  "
                f"{c['unscanned']:>9}  {c['n']:>2}"
            )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="ROUTE:JOBDIR",
        help="a Harbor job to scan, tagged with its baseline route "
        "(cc / ccmcp / cc2d-1 / ...). Repeatable. ROUTE:MODEL:JOBDIR is also "
        "accepted so specs can be shared with collect_qagen_results.",
    )
    ap.add_argument(
        "--tasks-root",
        type=Path,
        default=Path("QA-Gen/tasks"),
        help="QA-Gen tasks root, used only to map task_id -> domain",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if not args.run:
        ap.error("provide at least one --run ROUTE:JOBDIR")

    specs = [parse_run_spec(s) for s in args.run]
    missing = [str(job) for _, job in specs if not job.is_dir()]
    if missing:
        # Fail loudly: a silently-skipped job renders as a clean route.
        ap.error("job dir(s) not found: " + ", ".join(missing))

    per_task: dict[str, dict[str, Any]] = {}
    for route, job_dir in specs:
        rows = scan_job(job_dir)
        if not rows:
            rows = {"_no_trials": unscannable(f"no trials under {job_dir}")}
        per_task[route] = rows

    domains = build_domain_index(args.tasks_root)
    summary = summarize(per_task, domains)

    payload = {
        "schema_version": LEAK_SCAN_SCHEMA_VERSION,
        "runs": [{"route": route, "job_dir": str(job)} for route, job in specs],
        "n_tasks_with_domain": len(domains),
        "per_task": per_task,
        "summary": summary,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    print(_render_text(summary))
    print(f"\nleak scan -> {args.out}")


if __name__ == "__main__":
    main()
