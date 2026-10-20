"""Compute per-dataset stats for datasets/_matched/*.json (Tier-1 3D-grounded QA)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATCHED = ROOT / "datasets" / "_matched"


def load(name: str) -> list[dict]:
    return json.loads((MATCHED / f"{name}.json").read_text())["records"]


def summarize(name: str, recs: list[dict]) -> dict:
    n = len(recs)
    return {
        "name": name,
        "total_records": n,
        "splits": dict(Counter(r["split"] for r in recs)),
        "asset_kinds": dict(Counter(r["asset_kind"] for r in recs)),
        "unique_scenes_or_cases": len(set(r["scene_or_case_id"] for r in recs)),
        "with_question_text": sum(1 for r in recs if (r.get("question") or "").strip()),
        "with_answer_text": sum(
            1 for r in recs if r.get("answer") not in (None, "") and str(r["answer"]).strip()
        ),
        "no_qa_flag": sum(1 for r in recs if r.get("extra", {}).get("no_qa")),
    }


def main() -> None:
    names = ["scanqa", "sqa3d", "m3d-refseg", "m3d-seg-0005", "embodiedscan-demo"]
    summaries = [summarize(n, load(n)) for n in names]
    print(json.dumps({"per_dataset": summaries}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
