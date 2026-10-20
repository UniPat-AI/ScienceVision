#!/usr/bin/env python3
"""Review WalkingSkeleton task outputs for visualization and semantic risks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote, urlencode


REPO_ROOT = Path(__file__).resolve().parents[3]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def viewer_url(task: dict) -> str:
    asset = task["asset"]
    viewer = asset["viewer"]
    params = {}
    if viewer == "cornerstone":
        params["series"] = asset["path"]
    elif viewer == "photonics":
        params["path"] = asset["path"]
    else:
        params["file"] = asset["path"]
    if viewer == "molstar":
        params["type"] = asset["format"]
    if viewer == "urdf":
        params["format"] = asset["format"]
    return f"/viewer/{viewer}.html?{urlencode(params, quote_via=quote, safe='/')}"


def semantic_flags(task: dict) -> list[str]:
    title = task["title"].lower()
    instruction = task["instruction"].lower()
    asset_name = task["asset"]["name"].lower()
    flags = []

    if "damage" in title + " " + instruction and "damage" not in asset_name:
        flags.append("Title/instruction mentions damage, but selected asset name does not.")
    if "heme" in title + " " + instruction and task["asset"]["name"] not in {"4HHB"}:
        flags.append("Heme-oriented biology prompt should be manually checked against selected molecule.")
    if task["ground_truth"]["status"] == "needs_expert_annotation":
        flags.append("Ground truth still requires expert/rendered annotation.")
    return flags


def main() -> None:
    task_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "QA-Gen/tasks/WalkingSkeleton"
    tasks_path = task_dir / "ScienceVision_WalkingSkeleton_final_tasks.jsonl"
    rows = load_jsonl(tasks_path)

    viewer_page = REPO_ROOT / "viewer/sciencevision_tasks.html"
    index_page = (REPO_ROOT / "viewer/index.html").read_text(encoding="utf-8")
    viewer_html = viewer_page.read_text(encoding="utf-8") if viewer_page.exists() else ""

    lines = [
        "# WalkingSkeleton Review",
        "",
        "## Visualization Smoke Check",
        "",
        f"- Task viewer page exists: {viewer_page.exists()}",
        f"- Task viewer reads `/QA-Gen/tasks/<set>/ScienceVision_<set>_final_tasks.jsonl`: {'/QA-Gen/tasks/' in viewer_html}",
        f"- Data-viewer index links to WalkingSkeleton task viewer: {'sciencevision_tasks.html?set=WalkingSkeleton' in index_page}",
        f"- Task rows parsed: {len(rows)}",
        "",
        "## Task Review Table",
        "",
        "| task_id | domain | asset | viewer URL | review flags |",
        "| --- | --- | --- | --- | --- |",
    ]

    for row in rows:
        asset = row["asset"]
        page = REPO_ROOT / "viewer" / f"{asset['viewer']}.html"
        asset_path = REPO_ROOT / asset["repo_path"]
        flags = semantic_flags(row)
        if not page.exists():
            flags.append(f"Viewer page missing: {page.relative_to(REPO_ROOT)}")
        if not asset_path.exists():
            flags.append(f"Asset missing on disk: {asset['repo_path']}")
        flag_text = "<br>".join(flags) if flags else "none"
        lines.append(
            "| {task_id} | {domain} | {asset} | `{url}` | {flags} |".format(
                task_id=row["task_id"],
                domain=row["domain"],
                asset=asset["name"],
                url=viewer_url(row),
                flags=flag_text,
            )
        )

    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `100% kept` means all generated candidates passed structural contract checks.",
        "- It does not mean these tasks are benchmark-ready.",
        "- Every row still has `ground_truth.status = needs_expert_annotation`, so the set is a visualization/review skeleton, not a release-quality benchmark set.",
    ])

    out = task_dir / "REVIEW.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
