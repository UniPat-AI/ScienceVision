#!/usr/bin/env python3
"""Score each task's published gold against its own contract.

This is the self-consistency check for a repaired dataset: if a gold no longer
scores 1.0 under the contract that ships with it, the package is not gradable.
It is not a model result and must not be reported as one.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.replay import _DEFAULT_MOUNTS, _scorer_input  # noqa: E402


def _gold(dataset_root: Path, domain: str, task_id: str) -> Any:
    path = dataset_root / domain / task_id / "answer.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("answer")


def _score(
    task_dir: Path,
    dataset_root: Path,
    scorer_image: str,
    timeout: int,
) -> dict[str, Any]:
    import tomllib

    with (task_dir / "task.toml").open("rb") as handle:
        metadata = tomllib.load(handle).get("metadata") or {}
    task_id = task_dir.name
    domain = str(metadata.get("domain") or "")
    try:
        gold = _gold(dataset_root, domain, task_id)
    except (OSError, json.JSONDecodeError) as exc:
        return {"task_id": task_id, "status": "missing_gold", "error": str(exc)}

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        answer = work / "answer.json"
        answer.write_text(json.dumps({"answer": gold}, ensure_ascii=False), encoding="utf-8")
        task_input = _scorer_input(task_dir / "task.toml", task_id, work)
        container_task = f"/work/metadata{task_input.suffix}"
        command = [
            "docker",
            "run",
            "--rm",
            *_DEFAULT_MOUNTS,
            "-v",
            f"{task_input}:{container_task}:ro",
            "-v",
            f"{answer}:/work/answer.json:ro",
            scorer_image,
            "--task",
            container_task,
            "--answer",
            "/work/answer.json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)

    if completed.returncode != 0:
        return {"task_id": task_id, "status": "scorer_error", "error": completed.stderr[-500:]}
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"task_id": task_id, "status": "invalid_output", "error": completed.stdout[-500:]}
    value = (result.get("score") or {}).get("value")
    return {
        "task_id": task_id,
        "status": result.get("status"),
        "score": value,
        "passed": result.get("status") == "scored" and value == 1.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree-root", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--scorer-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    task_dirs = sorted(
        path for path in args.tree_root.iterdir() if (path / "task.toml").is_file()
    )

    def run(task_dir: Path) -> dict[str, Any]:
        return _score(task_dir, args.dataset_root, args.scorer_image, args.timeout)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run, task_dirs))

    passed = sum(record.get("passed") is True for record in results)
    report = {
        "tasks": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "failures": [record for record in results if record.get("passed") is not True],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("tasks", "passed", "failed")}))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
