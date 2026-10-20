"""Expert review packet generator for ScienceVision tasks.

Usage:
    python scripts/create_expert_review_packet.py --run-dir data/runs/<run_name> [--out <dir>]

Reads <run_dir>/final_tasks.jsonl and writes one JSON packet per task to
<out>/<task_id>.json (default out: <run_dir>/expert_review_packet/).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Expert check definitions (spec §3.5 — ordered)
# ---------------------------------------------------------------------------

_EXPERT_CHECK_DEFINITIONS: list[tuple[str, str]] = [
    (
        "question_scientifically_valid",
        "Is the question scientifically valid for this asset/domain?",
    ),
    (
        "asset_matches_question",
        "Does the 3D asset actually contain the entity/feature the question refers to?",
    ),
    (
        "terminal_answer_correct",
        "Is the proposed terminal_answer kind/fields correct and verifiable?",
    ),
    (
        "gold_evidence_sufficient",
        "Is the gold evidence (or planned evidence) sufficient to grade the answer?",
    ),
    (
        "scorer_tolerance_reasonable",
        "Are the scorer checks and any tolerances appropriate?",
    ),
    (
        "terminology_precise",
        "Is the terminology in the instruction precise and standard for this domain?",
    ),
]


# ---------------------------------------------------------------------------
# Core builder (pure, no I/O)
# ---------------------------------------------------------------------------


def build_packet(task: dict[str, Any]) -> dict[str, Any]:
    """Build an expert review packet dict from a final task dict.

    Pure function — performs no file I/O. Returns a new dict; does not
    mutate the input.
    """
    asset_raw: dict[str, Any] = task.get("asset", {})
    asset_path: str = asset_raw.get("path", "")
    viewer: str = asset_raw.get("viewer") or ""

    # Build asset sub-dict
    asset: dict[str, Any] = {
        "name": asset_raw.get("name", ""),
        "path": asset_path,
        "repo_path": asset_raw.get("repo_path", ""),
        "viewer": viewer,
        "format": asset_raw.get("format", ""),
        "exists_on_disk": Path(asset_path).exists() if asset_path else False,
    }

    # Viewer URL — use urllib.parse.quote_plus for asset path value
    if viewer:
        encoded_path = urllib.parse.quote_plus(asset_path)
        asset["viewer_url"] = f"/viewer/{viewer}.html?asset={encoded_path}"
    else:
        asset["viewer_url_note"] = "viewer page not specified"

    # Expert review requirements
    expert_req: dict[str, Any] = task.get("expert_review_requirements", {})
    required_reviewer_roles: list[str] = expert_req.get("roles", ["domain_expert"])
    min_approvals: int = expert_req.get("min_approvals", 1)

    # Build expert checks list (fixed order)
    expert_checks: list[dict[str, Any]] = [
        {
            "key": key,
            "question": question,
            "decision": None,
            "comment": "",
        }
        for key, question in _EXPERT_CHECK_DEFINITIONS
    ]

    return {
        "task_id": task.get("task_id", ""),
        "title": task.get("title", ""),
        "domain": task.get("domain", ""),
        "v_class": task.get("v_class", ""),
        "difficulty": task.get("difficulty", ""),
        "instruction": task.get("instruction", ""),
        "asset": asset,
        "terminal_answer": copy.deepcopy(task.get("terminal_answer")),
        "scorer": copy.deepcopy(task.get("scorer")),
        "validation_record": copy.deepcopy(task.get("validation_record")),
        "expert_checks": expert_checks,
        "required_reviewer_roles": list(required_reviewer_roles),
        "min_approvals": min_approvals,
        "decision": "pending",
        "notes": "",
    }


# ---------------------------------------------------------------------------
# I/O layer
# ---------------------------------------------------------------------------


def write_packets(run_dir: Path, out_dir: Path | None = None) -> int:
    """Write expert review packets for every task in <run_dir>/final_tasks.jsonl.

    Args:
        run_dir: Path to the run directory containing final_tasks.jsonl.
        out_dir: Directory to write packets to. Defaults to
                 <run_dir>/expert_review_packet/.

    Returns:
        Number of packets written.

    Raises:
        FileNotFoundError: If run_dir does not exist or final_tasks.jsonl is
                           absent.
    """
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    jsonl_path = run_dir / "final_tasks.jsonl"
    if not jsonl_path.exists():
        raise FileNotFoundError(f"final_tasks.jsonl not found in: {run_dir}")

    if out_dir is None:
        out_dir = run_dir / "expert_review_packet"

    out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if stripped:
                try:
                    tasks.append(json.loads(stripped))
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"final_tasks.jsonl: invalid JSON on line {lineno}: {e}"
                    ) from e

    seen: set[str] = set()
    out_dir_resolved = out_dir.resolve()
    for task in tasks:
        packet = build_packet(task)
        task_id: str = packet["task_id"] or "unknown"

        # Fix 1: path-traversal hardening
        safe_name = Path(task_id).name
        if (
            not safe_name
            or safe_name.startswith(".")
            or "/" in safe_name
            or "\\" in safe_name
            or safe_name != task_id  # .name stripped path components — traversal attempt
        ):
            raise ValueError(f"unsafe task_id: {task_id!r}")
        packet_path = out_dir / f"{safe_name}.json"
        if not packet_path.resolve().is_relative_to(out_dir_resolved):
            raise ValueError(f"unsafe task_id: {task_id!r}")

        # Fix 4: duplicate detection
        if safe_name in seen:
            raise ValueError(f"duplicate task_id: {task_id!r}")

        with packet_path.open("w", encoding="utf-8") as fout:
            json.dump(packet, fout, ensure_ascii=False, indent=2)
            fout.write("\n")

        seen.add(safe_name)

    return len(tasks)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate per-task expert review packets from final_tasks.jsonl."
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="Path to the run directory containing final_tasks.jsonl.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for packets (default: <run-dir>/expert_review_packet/).",
    )
    args = parser.parse_args()

    try:
        count = write_packets(args.run_dir, args.out)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.out if args.out is not None else args.run_dir / "expert_review_packet"
    print(f"Wrote {count} packets to {out_dir}")


if __name__ == "__main__":
    main()
