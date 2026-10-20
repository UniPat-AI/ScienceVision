#!/usr/bin/env python3
"""Compare a tree rebuilt from the dataset with a reference task tree.

The hardened builder keeps ground truth and the scorer out of the
agent-visible ``task.toml``; it passes them through ``SCI3D_METADATA`` in
``tests/test.sh``. This script reads either form and checks the verifier-facing
contract, plus the asset identity, against the reference tree.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

_FIELDS = ("answer_schema", "scorer", "ground_truth", "evaluation_eligible")


def _metadata(task_dir: Path) -> dict[str, Any]:
    with (task_dir / "task.toml").open("rb") as handle:
        metadata = tomllib.load(handle).get("metadata") or {}
    if metadata.get("ground_truth") is not None and metadata.get("scorer"):
        return metadata
    test_sh = task_dir / "tests" / "test.sh"
    if not test_sh.is_file():
        return metadata
    match = re.search(
        r"^export SCI3D_METADATA='(.*)'$", test_sh.read_text(encoding="utf-8"), re.MULTILINE
    )
    return json.loads(match.group(1)) if match else metadata


def _assets(metadata: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    rows = metadata.get("assets") or ([metadata["asset"]] if metadata.get("asset") else [])
    out = []
    for row in rows:
        repo_path = str(row.get("repo_path") or row.get("path") or "")
        out.append(
            (
                PurePosixPath(repo_path).name,
                str(row.get("role") or ""),
                str(row.get("viewer") or ""),
                str(row.get("format") or row.get("asset_format") or ""),
            )
        )
    return sorted(out)


def _tasks(root: Path) -> dict[str, Path]:
    if (root / "task.toml").is_file():
        return {root.name: root}
    candidates = [root / "tasks-cc", root / "tasks-ccmcp"]
    found: dict[str, Path] = {}
    for base in candidates:
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if (path / "task.toml").is_file():
                found[path.name] = path
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuilt", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    rebuilt = _tasks(args.rebuilt)
    reference = _tasks(args.reference)
    missing = sorted(set(reference) - set(rebuilt))
    extra = sorted(set(rebuilt) - set(reference))

    failures: list[dict[str, Any]] = []
    for task_id in sorted(set(rebuilt) & set(reference)):
        new = _metadata(rebuilt[task_id])
        old = _metadata(reference[task_id])
        for field in _FIELDS:
            if new.get(field) != old.get(field):
                failures.append({"task_id": task_id, "field": field})
        if _assets(new) != _assets(old):
            failures.append({"task_id": task_id, "field": "assets"})

    report = {
        "rebuilt_tasks": len(rebuilt),
        "reference_tasks": len(reference),
        "compared": len(set(rebuilt) & set(reference)),
        "missing": missing,
        "extra": extra,
        "failures": failures,
        "passed": not missing and not extra and not failures,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("compared", "missing", "failures", "passed")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
