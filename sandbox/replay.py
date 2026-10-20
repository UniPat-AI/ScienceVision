#!/usr/bin/env python3
"""Deterministically replay archived agent answers through the pinned scorer.

Each archived Harbor trial stores the agent's final message and the score the
run recorded. This script re-runs only the scoring stage: it finds the trial's
task contract, passes the final message to the pinned scorer image, and
compares the result with the archived ``sci3d_scores.json``. It never calls a
model, so a mismatch is a scorer, contract or extraction difference rather
than sampling noise.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_DEFAULT_MOUNTS = (
    "--network=none",
    "--read-only",
    "--cap-drop=ALL",
    "--tmpfs",
    "/tmp:rw,noexec,nosuid,size=16m",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _final_message(trajectory: Path) -> str:
    payload = _read_json(trajectory)
    steps = payload.get("steps") or payload.get("messages") or []
    messages = [
        str(step.get("message"))
        for step in steps
        if step.get("source") == "agent" and str(step.get("message") or "").strip()
    ]
    if not messages:
        raise ValueError("trajectory has no non-empty agent message")
    return messages[-1]


def _archived_score(trial: Path) -> float | None:
    path = trial / "verifier" / "sci3d_scores.json"
    if not path.is_file():
        return None
    answer_score = _read_json(path).get("answer_score")
    if not isinstance(answer_score, dict):
        return None
    value = answer_score.get("value")
    return float(value) if isinstance(value, (int, float)) else None


def _task_contract(task_roots: list[Path], task_id: str, route: str) -> Path:
    other = "ccmcp" if route == "cc" else "cc"
    for root in task_roots:
        for candidate in (
            root / f"tasks-{route}" / task_id / "task.toml",
            root / task_id / "task.toml",
            root / f"tasks-{other}" / task_id / "task.toml",
        ):
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(f"{task_id}: task.toml not found under {[str(r) for r in task_roots]}")


def _route(job: str) -> str:
    return "ccmcp" if "-ccmcp-" in job else "cc"


def _task_id(trial: Path) -> str:
    result = trial / "result.json"
    if result.is_file():
        payload = _read_json(result)
        task_name = payload.get("task_name")
        if isinstance(task_name, str) and task_name:
            return task_name.rsplit("/", 1)[-1]
    config = trial / "config.json"
    if config.is_file():
        path = (_read_json(config).get("task") or {}).get("path")
        if path:
            return Path(path).name
    # Harbor truncates long trial names before the "__" suffix, so this is a
    # last resort rather than the normal lookup.
    return trial.name.split("__", 1)[0]


def _score_trial(
    trial: Path,
    task_roots: list[Path],
    scorer_image: str,
    timeout: int,
    route: str,
) -> dict[str, Any]:
    task_id = _task_id(trial)
    archived = _archived_score(trial)
    try:
        contract = _task_contract(task_roots, task_id, route)
    except FileNotFoundError as exc:
        return {
            "trial": str(trial),
            "task_id": task_id,
            "status": "missing_contract",
            "error": str(exc),
            "archived": archived,
            "matched": False,
        }
    try:
        message = _final_message(trial / "agent" / "trajectory.json")
    except (OSError, ValueError) as exc:
        return {
            "trial": str(trial),
            "task_id": task_id,
            "status": "missing_answer",
            "error": str(exc),
            "archived": archived,
            "matched": False,
        }

    with tempfile.TemporaryDirectory() as directory:
        answer = Path(directory) / "answer.txt"
        answer.write_text(message, encoding="utf-8")
        command = [
            "docker",
            "run",
            "--rm",
            *_DEFAULT_MOUNTS,
            "-v",
            f"{contract}:/work/task.toml:ro",
            "-v",
            f"{answer}:/work/answer.txt:ro",
            scorer_image,
            "--task",
            "/work/task.toml",
            "--answer",
            "/work/answer.txt",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)

    if completed.returncode != 0:
        return {
            "trial": str(trial),
            "task_id": task_id,
            "status": "scorer_error",
            "error": completed.stderr[-500:],
            "archived": archived,
            "matched": False,
        }

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "trial": str(trial),
            "task_id": task_id,
            "status": "invalid_scorer_output",
            "error": completed.stdout[-500:],
            "archived": archived,
            "matched": False,
        }

    value = (result.get("score") or {}).get("value")
    matched = (
        archived is not None
        and result.get("status") == "scored"
        and isinstance(value, (int, float))
        and math.isclose(float(value), archived, rel_tol=1e-9, abs_tol=1e-9)
    )
    return {
        "trial": str(trial),
        "task_id": task_id,
        "status": result.get("status"),
        "score": value,
        "archived": archived,
        "matched": matched,
    }


def _trials(job: Path) -> list[Path]:
    return sorted(
        path
        for path in job.glob("*/*")
        if (path / "agent" / "trajectory.json").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, action="append", required=True)
    parser.add_argument("--task-root", type=Path, action="append", required=True)
    parser.add_argument("--scorer-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jsonl", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    if "@sha256:" not in args.scorer_image:
        print("warning: scorer image is not digest-pinned", file=sys.stderr)

    pending: list[tuple[str, Path]] = []
    for job in args.job:
        trials = _trials(job)
        if args.limit is not None:
            trials = trials[: args.limit]
        pending.extend((job.name, trial) for trial in trials)

    def run(item: tuple[str, Path]) -> dict[str, Any]:
        label, trial = item
        record = _score_trial(
            trial, args.task_root, args.scorer_image, args.timeout, _route(label)
        )
        record["job"] = label
        return record

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, pending))

    matched = sum(record["matched"] for record in results)
    summary = {
        "scorer_image": args.scorer_image,
        "trials": len(results),
        "matched": matched,
        "mismatched": len(results) - matched,
        "by_job": {},
        "mismatches": [record for record in results if not record["matched"]][:50],
    }
    for record in results:
        arm = summary["by_job"].setdefault(record["job"], {"trials": 0, "matched": 0})
        arm["trials"] += 1
        arm["matched"] += int(record["matched"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.jsonl is not None:
        with args.jsonl.open("w", encoding="utf-8") as handle:
            for record in results:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    print(json.dumps({key: summary[key] for key in ("trials", "matched", "mismatched")}))
    return 0 if matched == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
