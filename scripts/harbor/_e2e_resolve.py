"""Resolve an end2end task list (viewer_link rows) into QA-Gen records."""
from __future__ import annotations
import copy

import sys
from pathlib import Path

from scripts.harbor._viewer_link import parse_viewer_link, seed_pose_from_cam

_MESH_EXT = {".obj", ".ply", ".stl", ".glb", ".gltf", ".off"}


def _rel_to_root(file_str: str, asset_root: Path) -> str:
    """Best-effort map the link's file= to a repo-relative path under asset_root."""
    p = Path(file_str)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(asset_root.resolve()))
        except ValueError:
            return file_str
    return file_str


def _resolve_one(row: dict, *, asset_root: Path, seed_from_link: bool) -> dict:
    """Resolve a single row. May raise (KeyError for a missing required
    field, or anything parse_viewer_link's callers surface) -- the caller in
    resolve_task_list is responsible for catching and skipping."""
    task_id = row["task_id"]
    link = parse_viewer_link(row["viewer_link"])
    repo_path = _rel_to_root(link["file"], asset_root)
    fmt = Path(link["file"]).suffix.lower().lstrip(".") or "obj"
    ground_truth = copy.deepcopy(row.get("ground_truth"))
    value = ground_truth.get("value") if isinstance(ground_truth, dict) else ground_truth
    answer_schema = copy.deepcopy(row.get("answer_schema"))
    if not answer_schema:
        if isinstance(value, dict):
            answer_schema = {"kind": "structured", "fields": list(value)}
        else:
            answer_schema = {"kind": "structured", "fields": ["value"]}
    rec = {
        "task_id": task_id,
        "domain": row.get("domain", ""),
        "instruction": row.get("question") or row.get("instruction") or "",
        "asset": {"repo_path": repo_path, "path": link["file"], "format": fmt},
        "answer_schema": answer_schema,
        "ground_truth": ground_truth,
        "scorer": row.get("scorer") or {"primary": "exact_match", "secondary": []},
    }
    if seed_from_link:
        is_mesh = Path(link["file"]).suffix.lower() in _MESH_EXT
        sp = seed_pose_from_cam(link["cam"]) if is_mesh else None
        if sp:
            rec["seed_pose"] = sp
        else:
            print(f"  warning: --seed-from-link but task {task_id} has no "
                  f"usable mesh camera; falling back to canonical", file=sys.stderr)
    return rec


def resolve_task_list(rows, *, asset_root: Path, seed_from_link: bool) -> list[dict]:
    """Map end2end task-list rows (viewer_link based) into QA-Gen records.

    Each row: ``{task_id, viewer_link, question, ground_truth, scorer?, domain?}``.
    Each output record: ``{task_id, domain, instruction, asset:{repo_path,
    format}, ground_truth, scorer, seed_pose?}``. ``seed_pose`` is only
    attached when ``seed_from_link`` and the link is a mesh (gltf) link with
    a decodable camera; other viewer types fall back to canonical (no seed)
    with a stderr warning.

    A single malformed row (missing ``task_id``/``viewer_link``, or any other
    per-row resolution error) is skipped rather than aborting the whole
    batch: the warning names the offending row's ``task_id`` when available,
    else its 0-based index, plus the reason. Valid rows are still returned.
    """
    recs: list[dict] = []
    for i, row in enumerate(rows):
        row_task_id = row.get("task_id") if isinstance(row, dict) else None
        label = row_task_id if row_task_id else f"<row #{i}>"
        try:
            rec = _resolve_one(row, asset_root=asset_root, seed_from_link=seed_from_link)
        except KeyError as exc:
            missing = exc.args[0] if exc.args else "?"
            print(f"  warning: skipping malformed row {label}: missing "
                  f"required field {missing!r}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
            print(f"  warning: skipping malformed row {label}: {exc}", file=sys.stderr)
            continue
        recs.append(rec)
    return recs
