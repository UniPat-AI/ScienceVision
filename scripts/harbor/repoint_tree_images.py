#!/usr/bin/env python3
"""Repoint a staged task tree's agent image at a pinned tag.

The agent-env table bakes ``:latest`` into every generated Dockerfile. On a
shared host that is whatever another session last built, and rebuilding
``:latest`` under a live matrix would swap the agent binary mid-run. So images
are built under a run-scoped tag and the trees are repointed here instead.

Each Dockerfile is replaced rather than edited: trees are hardlinked to each
other to share staged assets and rendered views, and an in-place write would
reach through the link into a sibling tree.

    python -m scripts.harbor.repoint_tree_images \
        --tree <tree> --repo sci3d-codex --tag v21ev2-20260813
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def task_dirs(tree: Path) -> list[Path]:
    return sorted(p.parent.parent for p in tree.glob("*/environment/Dockerfile"))


def repoint(task_dir: Path, repo: str, tag: str) -> str:
    path = task_dir / "environment" / "Dockerfile"
    lines = path.read_text().splitlines(keepends=True)
    out: list[str] = []
    seen: list[str] = []
    for line in lines:
        if line.startswith("FROM "):
            image = line.split()[1]
            name = image.split(":")[0]
            if name == repo:
                out.append(f"FROM {repo}:{tag}\n")
                seen.append(image)
                continue
        out.append(line)
    if len(seen) != 1:
        raise SystemExit(f"expected exactly one FROM {repo} line in {path}, found {len(seen)}")
    path.unlink()
    path.write_text("".join(out))
    return seen[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True, type=Path, action="append")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    report = []
    for tree in args.tree:
        dirs = task_dirs(tree)
        if not dirs:
            raise SystemExit(f"no tasks under {tree}")
        previous = set()
        for task_dir in dirs:
            previous.add(repoint(task_dir, args.repo, args.tag))
        # Verify no task kept an unpinned reference.
        for task_dir in dirs:
            text = (task_dir / "environment" / "Dockerfile").read_text()
            if f"{args.repo}:latest" in text:
                raise SystemExit(f"unpinned reference survived in {task_dir}")
            if f"{args.repo}:{args.tag}" not in text:
                raise SystemExit(f"repoint did not take in {task_dir}")
        report.append(
            {
                "tree": str(tree),
                "tasks": len(dirs),
                "from_previous": sorted(previous),
                "to": f"{args.repo}:{args.tag}",
            }
        )
        print(f"  {tree.name}: {len(dirs)} tasks -> {args.repo}:{args.tag}")
    print(json.dumps({"status": "pass", "trees": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
