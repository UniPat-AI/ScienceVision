#!/usr/bin/env python3
"""Export launch-ready task contracts from prepared Harbor task trees.

The published dataset carries questions, assets and golds, but not the asset
roles, answer schema or scorer configuration a Harbor tree needs. This script
reads those fields from a prepared tree and writes one JSONL record per task,
with absolute host paths removed. The result is the input to
``sandbox/build_trees.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any, Iterable


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _clean_asset(asset: dict[str, Any]) -> dict[str, Any]:
    kept = {key: asset[key] for key in ("repo_path", "format", "role", "viewer") if key in asset}
    if "format" not in kept and "asset_format" in asset:
        kept["format"] = asset["asset_format"]
    return kept


def _record(task_dir: Path) -> dict[str, Any]:
    metadata = _load_toml(task_dir / "task.toml").get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"{task_dir}: task.toml has no [metadata] table")

    instruction_path = task_dir / "instruction.md"
    instruction = instruction_path.read_text(encoding="utf-8") if instruction_path.is_file() else ""

    assets = metadata.get("assets")
    if not isinstance(assets, list) or not assets:
        asset = metadata.get("asset")
        assets = [asset] if isinstance(asset, dict) else []

    return {
        "task_id": metadata.get("task_id") or task_dir.name,
        "domain": metadata.get("domain", ""),
        "instruction": instruction,
        "answer_schema": metadata.get("answer_schema"),
        "scorer": metadata.get("scorer"),
        "scorer_config": metadata.get("scorer_config") or {},
        "ground_truth": metadata.get("ground_truth"),
        "evaluation_eligible": metadata.get("evaluation_eligible") is True,
        "evaluation_ineligible_reason": metadata.get("evaluation_ineligible_reason", ""),
        "v_class": metadata.get("v_class") or "V1",
        "tool_policy": {
            "allowed_tools": metadata.get("allowed_tools") or [],
            "forbidden_tools": metadata.get("forbidden_tools") or [],
        },
        "assets": [_clean_asset(item) for item in assets if isinstance(item, dict)],
    }


def _iter_task_dirs(root: Path) -> Iterable[Path]:
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "task.toml").is_file():
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree-root", type=Path, action="append", required=True)
    parser.add_argument("--task-ids", type=Path, help="optional newline-delimited allow-list")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    allowed = None
    if args.task_ids is not None:
        allowed = {line.strip() for line in args.task_ids.read_text().splitlines() if line.strip()}

    records: dict[str, dict[str, Any]] = {}
    for root in args.tree_root:
        if not root.is_dir():
            raise SystemExit(f"tree root not found: {root}")
        for task_dir in _iter_task_dirs(root):
            record = _record(task_dir)
            task_id = record["task_id"]
            if allowed is not None and task_id not in allowed:
                continue
            if task_id in records:
                raise SystemExit(f"duplicate task_id across tree roots: {task_id}")
            records[task_id] = record

    if allowed is not None:
        missing = sorted(allowed - set(records))
        if missing:
            print(f"missing {len(missing)} task(s): {missing[:10]}", file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for task_id in sorted(records):
            handle.write(json.dumps(records[task_id], ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"status": "ok", "tasks": len(records), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
