"""Produce ``irreducibility.json`` — the T0 per-task irreducibility labels.

Driver for ``_irreducibility`` (which holds the labelling rules and their
rationale). This module walks Harbor job dirs and writes the artifact.

    python -m scripts.harbor.probe_irreducibility \
        --text-run /data/jobs/job-codex-text \
        --file-run /data/jobs/job-cc \
        --perception-run /data/jobs/job-cc2d-6 \
        --tasks-root QA-Gen/tasks \
        --out reports/irreducibility.json

``--file-run`` should point at the six-baseline matrix's own ``job-cc``, not a
second ``cc`` run: reusing it costs nothing extra and guarantees the file-probe
half was executed under exactly the knobs the leaderboard was measured with. A
separately-launched ``cc`` run with a different timeout would silently make the
labels incomparable to the numbers they are supposed to qualify.

``--perception-run`` is optional and never changes a label; it only fills
``solved_by_perception_route``, which separates "irreducible and answerable"
from "irreducible and possibly unanswerable".

Output:

    per_task[task_id] = {domain, baseline_summary{route: cell}, labels{...}}
    summary           = {n, by_label, by_domain, n_citable, n_unknown,
                         n_discriminative_unsolved}

``baseline_summary`` is emitted in the exact shape
``_qa_qc_shortcuts.attach_probe_results`` consumes, so the same cells can be fed
straight into the QC gate to raise ``text_knowledge_shortcut`` /
``package_compute_shortcut`` findings on the affected tasks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts._qa_qc_shortcuts import content_reward
from scripts.harbor._irreducibility import (
    FILE_ROUTE,
    IRREDUCIBILITY_SCHEMA_VERSION,
    PERCEPTION_ROUTE,
    TEXT_ROUTE,
    DEFAULT_SUCCESS_THRESHOLD,
    label_task,
    summarize_labels,
)
from scripts.harbor.leak_scan import build_domain_index
from scripts.qa_qc import _reward


def _best(current: float | None, candidate: float | None) -> float | None:
    """Best-of across repeat trials.

    A shortcut that works once is a shortcut: if any attempt reached the answer
    without perceiving the asset, the task is reducible. Averaging would let a
    flaky retry hide a real leak.
    """
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


def collect_job(job_dir: Path, route: str) -> dict[str, dict[str, Any]]:
    """Read one Harbor job into ``{task_id: cell}`` in attach_probe_results shape.

    Returns an empty mapping for a missing dir; the caller decides whether that
    is fatal (``main`` treats it as fatal, so an unrun probe can never be read
    as a passing probe).
    """
    job_dir = Path(job_dir)
    if not job_dir.is_dir():
        return {}
    cells: dict[str, dict[str, Any]] = {}
    for path in sorted(job_dir.rglob("result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        task_name = str(data.get("task_name") or data.get("task_id") or "")
        task_id = task_name.split("/")[-1]
        if not task_id:
            continue
        prior = cells.get(task_id)
        cells[task_id] = {
            "route": route,
            "attempt_count": (prior or {}).get("attempt_count", 0) + 1,
            "effective_reward": _best((prior or {}).get("effective_reward"), _reward(data)),
            "effective_content_reward": _best(
                (prior or {}).get("effective_content_reward"), content_reward(data)
            ),
            "effective_source": str(path),
        }
    return cells


def build_per_task(
    text_job: Path,
    file_job: Path,
    perception_job: Path | None,
    domains: dict[str, str],
    threshold: float,
) -> dict[str, dict[str, Any]]:
    """Pair the probe jobs task-by-task and label each task.

    The task-id union is deliberate: a task present in one probe but not the
    other is reported as ``unknown`` rather than dropped, because a dropped task
    disappears from the citable-set denominator without leaving a trace.
    """
    text_cells = collect_job(text_job, TEXT_ROUTE)
    file_cells = collect_job(file_job, FILE_ROUTE)
    perception_cells = (
        collect_job(perception_job, PERCEPTION_ROUTE) if perception_job else {}
    )

    task_ids = sorted(set(text_cells) | set(file_cells) | set(perception_cells))
    per_task: dict[str, dict[str, Any]] = {}
    for task_id in task_ids:
        summary = {
            route: cell
            for route, cell in (
                (TEXT_ROUTE, text_cells.get(task_id)),
                (FILE_ROUTE, file_cells.get(task_id)),
                (PERCEPTION_ROUTE, perception_cells.get(task_id)),
            )
            if cell is not None
        }
        per_task[task_id] = {
            "domain": str(domains.get(task_id) or "unknown"),
            "baseline_summary": summary,
            "labels": label_task(
                text_cell=text_cells.get(task_id),
                file_cell=file_cells.get(task_id),
                perception_cell=perception_cells.get(task_id),
                threshold=threshold,
            ),
        }
    return per_task


def _render_text(summary: dict[str, Any]) -> str:
    by_label = summary["by_label"]
    lines = [
        f"tasks labelled       {summary['n']}",
        f"  text-leaked        {by_label['text-leaked']}",
        f"  file-reducible     {by_label['file-reducible']}",
        f"  discriminative     {by_label['discriminative']}  <- citable",
        f"  unknown            {by_label['unknown']}",
        "",
        f"of the citable set, {summary['n_discriminative_unsolved']} were solved by no route",
        "",
        "domain          text-leak  file-red  discrim  unknown",
        "-" * 52,
    ]
    for domain in sorted(summary["by_domain"]):
        c = summary["by_domain"][domain]
        lines.append(
            f"{domain:<15} {c['text-leaked']:>9}  {c['file-reducible']:>8}  "
            f"{c['discriminative']:>7}  {c['unknown']:>7}"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--text-run", type=Path, required=True,
                    help="job dir for the codex-text probe (prose only)")
    ap.add_argument("--file-run", type=Path, required=True,
                    help="job dir for the raw-file probe; reuse the matrix's job-cc")
    ap.add_argument("--perception-run", type=Path, default=None,
                    help="optional job dir for a rendered-views route (cc2d-*)")
    ap.add_argument("--tasks-root", type=Path, default=Path("QA-Gen/tasks"),
                    help="QA-Gen tasks root, used only to map task_id -> domain")
    ap.add_argument("--success-threshold", type=float,
                    default=DEFAULT_SUCCESS_THRESHOLD)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    runs = [("text", args.text_run), ("file", args.file_run)]
    if args.perception_run is not None:
        runs.append(("perception", args.perception_run))
    missing = [f"{role}={job}" for role, job in runs if not Path(job).is_dir()]
    if missing:
        # Fail loudly: a skipped probe would label every task 'unknown', and an
        # all-unknown report is indistinguishable from a probe that found nothing.
        ap.error("probe job dir(s) not found: " + ", ".join(missing))

    domains = build_domain_index(args.tasks_root)
    per_task = build_per_task(
        args.text_run, args.file_run, args.perception_run,
        domains, args.success_threshold,
    )
    summary = summarize_labels(per_task)

    payload = {
        "schema_version": IRREDUCIBILITY_SCHEMA_VERSION,
        "success_threshold": args.success_threshold,
        "runs": [{"role": role, "job_dir": str(job)} for role, job in runs],
        "routes": {"text": TEXT_ROUTE, "file": FILE_ROUTE,
                   "perception": PERCEPTION_ROUTE},
        "n_tasks_with_domain": len(domains),
        "per_task": per_task,
        "summary": summary,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    print(_render_text(summary))
    print(f"\nirreducibility labels -> {args.out}")


if __name__ == "__main__":
    main()
