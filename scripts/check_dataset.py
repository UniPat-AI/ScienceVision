#!/usr/bin/env python3
"""Check the pinned local review-v11 index and common task records, read-only."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def check(snapshot: Path, *, index_only: bool = False) -> dict:
    reference = json.loads((ROOT / "resources/dataset.json").read_text())
    raw = (snapshot / "index.jsonl").read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != reference["index_sha256"]:
        raise ValueError("index.jsonl hash differs from the pinned review-v11 snapshot")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != reference["task_count"]:
        raise ValueError("task count differs from the release reference")
    ids = [row["task_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task IDs")
    counts = dict(Counter(row["domain"] for row in rows))
    if counts != reference["domain_counts"]:
        raise ValueError("domain counts differ from the release reference")
    checked = 0
    if not index_only:
        for row in rows:
            task_id = row["task_id"]
            if Path(task_id).name != task_id or task_id in (".", ".."):
                raise ValueError("unsafe task ID in index")
            task = snapshot / row["domain"] / task_id
            normalized = json.loads((task / "normalized.json").read_text())
            if normalized.get("task_id") != task_id:
                raise ValueError(f"task ID mismatch: {task_id}")
            if normalized.get("domain") != row["domain"]:
                raise ValueError(f"domain mismatch: {task_id}")
            if not isinstance(normalized.get("question"), str) or not normalized["question"].strip():
                raise ValueError(f"missing question: {task_id}")
            if not isinstance(normalized.get("answer"), dict):
                raise ValueError(f"answer is not an object: {task_id}")
            for name in ("_meta.json", "allow_mcp.json"):
                json.loads((task / name).read_text())
            checked += 1
    return {
        "status": "pass",
        "task_count": len(rows),
        "domain_counts": counts,
        "index_sha256": actual,
        "normalized_records_checked": checked,
        "asset_layout_verified": False,
        "asset_payload_checksums_verified": False,
        "harness_compatibility_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--index-only", action="store_true")
    args = parser.parse_args()
    try:
        result = check(args.snapshot, index_only=args.index_only)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Dataset check failed: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
