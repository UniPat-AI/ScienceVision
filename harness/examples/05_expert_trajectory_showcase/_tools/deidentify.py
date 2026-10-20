#!/usr/bin/env python3
"""De-identify the showcase package: keep the expert account id, drop the name.

The production DB carries both `meta.expert_id` (a platform account slug such as
`chemistry1`) and `meta.expert_name` (the annotator's real name). Only the slug
may leave the platform, so this module removes the name field everywhere it can
reach: the per-task JSON, the `data.js` copy, the task README header line and the
`index.html` chip that renders it.

Two entry points:

    python3 _tools/deidentify.py --rewrite [<package root>]   # scrub in place
    python3 _tools/deidentify.py --check   [<package root>]    # exit 1 if dirty

`--check` is the pre-commit guard. It never prints a name: it reports which file
still carries a name-bearing field, not the value.

`export_showcase.py` imports `scrub_document` so a fresh export is already clean;
this script exists for packages exported before that fix and as the guard.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Fields inside `meta` that carry a person's name rather than an account slug.
NAME_FIELDS = frozenset({"expert_name"})

# `> 化学 · … · 专家 <姓名>（chemistry1） · 答案质检通过 …`  ->  `… · 专家 chemistry1 · …`
README_HEADER_RE = re.compile(r"专家 [^（(\n]*[（(]([A-Za-z0-9_]+)[）)]")

# `[`专家 ${m.expert_name}`, 1]` -> `[`专家 ${m.expert_id}`, 1]`
INDEX_HTML_RE = re.compile(r"\$\{m\.expert_name\}")

TASK_JSON_NAME = "题目与轨迹.json"


def scrub_document(document: dict) -> dict:
    """Return a copy of an export document with name-bearing meta fields removed.

    Raises ValueError if the document has no account slug to fall back on --
    silently emitting a task with no attribution at all would be worse than
    failing the export.
    """
    meta = document.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("document has no `meta` object")
    if not meta.get("expert_id"):
        raise ValueError("document `meta` has no `expert_id` to keep")
    return {**document, "meta": {k: v for k, v in meta.items() if k not in NAME_FIELDS}}


def find_name_fields(node: object, path: str = "") -> list[str]:
    """Recursively collect JSON paths of every name-bearing field still present."""
    if isinstance(node, dict):
        hits = [f"{path}/{k}" for k in node if k in NAME_FIELDS]
        for key, value in node.items():
            hits.extend(find_name_fields(value, f"{path}/{key}"))
        return hits
    if isinstance(node, list):
        return [h for i, v in enumerate(node) for h in find_name_fields(v, f"{path}[{i}]")]
    return []


def task_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / TASK_JSON_NAME).is_file())
    if not dirs:
        raise SystemExit(f"no task folders (containing {TASK_JSON_NAME}) under {root}")
    return dirs


def rewrite_task(task_dir: Path) -> list[str]:
    """Scrub one task folder in place. Returns the relative paths it changed."""
    changed: list[str] = []
    json_path = task_dir / TASK_JSON_NAME
    document = json.loads(json_path.read_text(encoding="utf-8"))
    scrubbed = scrub_document(document)

    if scrubbed != document:
        # Byte-for-byte the same serialisation the exporter uses, so the diff
        # shows only the removed field.
        json_path.write_text(json.dumps(scrubbed, ensure_ascii=False, indent=2), encoding="utf-8")
        changed.append(json_path.name)
        (task_dir / "data.js").write_text(
            "window.SV_CASE = " + json.dumps(scrubbed, ensure_ascii=False) + ";\n", encoding="utf-8"
        )
        changed.append("data.js")

    for name, pattern, repl in (
        ("README.md", README_HEADER_RE, r"专家 \1"),
        ("index.html", INDEX_HTML_RE, "${m.expert_id}"),
    ):
        path = task_dir / name
        if not path.is_file():
            continue
        before = path.read_text(encoding="utf-8")
        after = pattern.sub(repl, before)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed.append(name)
    return changed


def check_task(task_dir: Path) -> list[str]:
    """Return human-readable problems for one task folder (empty = clean)."""
    problems: list[str] = []
    document = json.loads((task_dir / TASK_JSON_NAME).read_text(encoding="utf-8"))
    for hit in find_name_fields(document):
        problems.append(f"{TASK_JSON_NAME}: name-bearing field at {hit}")
    if not (document.get("meta") or {}).get("expert_id"):
        problems.append(f"{TASK_JSON_NAME}: meta.expert_id missing")

    for name in ("data.js", "index.html", "README.md"):
        path = task_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for field in sorted(NAME_FIELDS):
            if field in text:
                problems.append(f"{name}: mentions `{field}`")
        if name == "README.md" and README_HEADER_RE.search(text):
            problems.append("README.md: header still renders a name before the account slug")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rewrite", action="store_true", help="scrub the package in place")
    mode.add_argument("--check", action="store_true", help="exit non-zero if any name field remains")
    parser.add_argument("root", nargs="?", default=None, help="package root (default: parent of _tools/)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    dirty = 0
    for task_dir in task_dirs(root):
        if args.rewrite:
            changed = rewrite_task(task_dir)
            print(f"{task_dir.name}: {'scrubbed ' + ', '.join(changed) if changed else 'already clean'}")
        else:
            problems = check_task(task_dir)
            dirty += len(problems)
            for problem in problems:
                print(f"{task_dir.name}: {problem}", file=sys.stderr)

    if args.check:
        print(f"checked {len(task_dirs(root))} tasks: {dirty} problem(s)", file=sys.stderr)
        return 1 if dirty else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
