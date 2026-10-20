"""Shared utilities for the ScienceVision task generator."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
TASKGEN_ROOT = PACKAGE_ROOT.parent
QA_GEN_ROOT = TASKGEN_ROOT.parent
REPO_ROOT = QA_GEN_ROOT.parent


def load_json(path: str | Path) -> Any:
    full_path = resolve_path(path)
    with full_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: str | Path, pretty: bool = True) -> None:
    full_path = Path(path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with full_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
        f.write("\n")


def save_jsonl(rows: list[dict[str, Any]], path: str | Path) -> None:
    full_path = Path(path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with full_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    local = TASKGEN_ROOT / candidate
    if local.exists():
        return local
    return REPO_ROOT / candidate


def repo_relative(path: str | Path) -> str:
    full_path = resolve_path(path)
    try:
        return str(full_path.relative_to(REPO_ROOT))
    except ValueError:
        return str(full_path)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def create_run_dir(base_dir: str | Path = "data/runs") -> Path:
    base = TASKGEN_ROOT / base_dir if not Path(base_dir).is_absolute() else Path(base_dir)
    run_dir = base / f"run_{timestamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def stable_slug(value: str) -> str:
    chars = []
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "item"
