"""Build data/qa_tasks_index.json — the set picker for viewer/sciencevision_tasks.html.

Scans QA-Gen/tasks/<set>/ScienceVision_<set>_final_tasks.jsonl, counts tasks,
collects distinct domains, and writes a stable JSON file.

CLI:
    python scripts/build_qa_tasks_index.py             # write data/qa_tasks_index.json
    python scripts/build_qa_tasks_index.py --check     # exit 1 if regenerated would differ
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "QA-Gen" / "tasks"
OUT_PATH = REPO_ROOT / "data" / "qa_tasks_index.json"

JSONL_PATTERN = "ScienceVision_{set}_final_tasks.jsonl"


def _iter_set_dirs() -> list[Path]:
    if not TASKS_DIR.is_dir():
        return []
    return sorted(p for p in TASKS_DIR.iterdir() if p.is_dir() and not p.name.startswith("_"))


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as err:
                raise SystemExit(f"{path}:{i}: invalid JSONL: {err}")
    return rows


def build_index() -> dict:
    sets: list[dict] = []
    for set_dir in _iter_set_dirs():
        name = set_dir.name
        jsonl = set_dir / JSONL_PATTERN.format(set=name)
        if not jsonl.is_file():
            # A set dir without the final tasks file is a partial mirror —
            # skip rather than crash, so a half-synced clone still renders
            # the sets that are present.
            continue
        rows = _read_jsonl(jsonl)
        domains = sorted({str(r.get("domain", "")) for r in rows if r.get("domain")})
        sets.append({
            "name": name,
            "task_count": len(rows),
            "domains": domains,
        })
    return {
        "version": 1,
        "generated_at": "2026-06-29T00:00:00Z",
        "sets": sets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if rebuilt index differs from committed file.")
    args = parser.parse_args(argv)

    payload = json.dumps(build_index(), indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not OUT_PATH.exists():
            print(f"FAIL: {OUT_PATH} does not exist; run without --check first.",
                  file=sys.stderr)
            return 1
        if OUT_PATH.read_text(encoding="utf-8") != payload:
            print(f"FAIL: {OUT_PATH} is stale. "
                  f"Re-run `python scripts/build_qa_tasks_index.py`.", file=sys.stderr)
            return 1
        print(f"OK: {OUT_PATH} is up-to-date.")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(payload, encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(build_index()['sets'])} sets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
