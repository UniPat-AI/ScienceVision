#!/usr/bin/env python3
"""Copy the referenced 3D asset files into the package, verified by sha256.

`export_showcase.py` records asset metadata only (`included: false`) because the
production DB stores platform paths, not the files. This script resolves each
declared asset against one or more local source trees, checks its sha256 against
the sealed manifest, copies it to `<task>/assets/<name>`, and flips the metadata
to `included: true` with an `asset_file` pointer.

A hash mismatch is refused, never copied: a same-named file from a different
build would silently break replay of the sealed trajectory.

    python3 _tools/collect_assets.py --search-root <repo> [--search-root <more>]
    python3 _tools/collect_assets.py --check

Run it after `export_showcase.py` and before `rebuild_initial_frame.py`, so the
frame rebuild can serve the assets from the package itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

TASK_JSON_NAME = "题目与轨迹.json"
ASSET_DIR_NAME = "assets"

# A declared path such as
#   assets/chemistry/<slug>/crawl_data/biology/qm_conformers/qm9_110589.xyz
# carries the source-tree-relative path after the last of these markers.
PATH_MARKERS = ("crawl_data/", "datasets/")

# Directories never worth walking when falling back to a basename search.
PRUNE_DIRS = frozenset({".git", ".claude", ".codex-worktrees", "node_modules", "__pycache__", ".venv"})

INCLUDED_NOTE = "3D 素材文件已随包，见各题 `assets/`；sha256 与封存清单一致。"

README_TEXT_PATCHES = (
    ("（不含 3D 素材文件）", "（含 3D 素材文件）"),
    ("| 3D 素材（未随包） |", "| 3D 素材（随包） |"),
    ("## 引用的 3D 素材（仅元数据）", "## 随包的 3D 素材"),
)
README_ASSET_ROW = (
    "| `assets/` | 题目引用的 3D 素材文件本身（`.cif` / `.xyz` / `.glb` / `.nii.gz`），"
    "文件名与 `task_context.assets[].name` 一致，sha256 与封存清单一致。 |"
)
README_ANCHOR = "| `screenshots/操作NNN.png` | 每一步操作后的画面截图"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def declared_suffix(declared_path: str) -> str | None:
    """Return the source-tree-relative path embedded in a platform asset path."""
    best = None
    for marker in PATH_MARKERS:
        index = declared_path.rfind(marker)
        if index != -1 and (best is None or index > best):
            best = index
    return declared_path[best:] if best is not None else None


def walk_for_basename(root: Path, name: str) -> list[Path]:
    """Every file called `name` under `root`, skipping heavy/irrelevant trees."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        if name in filenames:
            found.append(Path(dirpath) / name)
    return found


def resolve_asset(asset: dict, search_roots: list[Path]) -> tuple[Path | None, str]:
    """Find a local file whose sha256 matches the asset. Returns (path, reason)."""
    want = asset.get("sha256")
    if not want:
        return None, "asset has no sha256 in the sealed manifest"

    # Cheap first: the declared path replayed under each source tree. Only fall
    # back to walking a tree when that misses -- these trees are large.
    suffix = declared_suffix(asset.get("path") or "")
    # Tier 1 replays the declared path under each root -- one stat() each.
    # Then one tier per root, walking it for the basename. Tiers run in order
    # and stop at the first sha256 match, so a narrow root listed first spares
    # the walk of a large one.
    tiers: list[list[Path] | Path] = []
    if suffix:
        tiers.append([root / suffix for root in search_roots])
    tiers.extend(search_roots)

    seen: set[Path] = set()
    mismatched = 0
    for tier in tiers:
        candidates = tier if isinstance(tier, list) else walk_for_basename(tier, asset["name"])
        for candidate in candidates:
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            if sha256_of(candidate) == want:
                return candidate, "sha256 match"
            mismatched += 1
    if mismatched:
        return None, f"found {mismatched} same-named file(s) but none matched sha256"
    return None, "not found under any --search-root"


def patch_readme(task_dir: Path, asset_names: list[str]) -> bool:
    """Idempotently reword the README now that the assets ship with the package."""
    readme = task_dir / "README.md"
    if not readme.is_file():
        return False
    before = readme.read_text(encoding="utf-8")
    text = before
    for old, new in README_TEXT_PATCHES:
        text = text.replace(old, new)
    # Point the asset table at the in-package copy.
    for name in asset_names:
        text = text.replace(f"| `{name}` |", f"| `{ASSET_DIR_NAME}/{name}` |")
    if README_ASSET_ROW not in text and README_ANCHOR in text:
        text = text.replace(README_ANCHOR, README_ASSET_ROW + "\n" + README_ANCHOR, 1)
    if text == before:
        return False
    readme.write_text(text, encoding="utf-8")
    return True


def collect_task(task_dir: Path, search_roots: list[Path]) -> list[str]:
    """Copy every declared asset into the task folder. Returns failure messages."""
    json_path = task_dir / TASK_JSON_NAME
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    context = doc["task_context"]
    failures: list[str] = []
    updated_assets: list[dict] = []

    for asset in context.get("assets") or []:
        target = task_dir / ASSET_DIR_NAME / asset["name"]
        if target.is_file() and sha256_of(target) == asset.get("sha256"):
            print(f"  {asset['name']}: already in package")
        else:
            source, reason = resolve_asset(asset, search_roots)
            if source is None:
                failures.append(f"{asset['name']}: {reason}")
                updated_assets.append(asset)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            print(f"  {asset['name']}: copied ({target.stat().st_size} B, {reason})")
        updated_assets.append({**asset, "included": True, "asset_file": f"{ASSET_DIR_NAME}/{asset['name']}"})

    if all(a.get("included") for a in updated_assets) and updated_assets:
        note = INCLUDED_NOTE
    else:
        note = context.get("assets_note")
    new_doc = {**doc, "task_context": {**context, "assets": updated_assets, "assets_note": note}}

    if new_doc != doc:
        json_path.write_text(json.dumps(new_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        (task_dir / "data.js").write_text(
            "window.SV_CASE = " + json.dumps(new_doc, ensure_ascii=False) + ";\n", encoding="utf-8"
        )
    if not failures:
        patch_readme(task_dir, [a["name"] for a in updated_assets])
    return failures


def check_task(task_dir: Path) -> list[str]:
    doc = json.loads((task_dir / TASK_JSON_NAME).read_text(encoding="utf-8"))
    problems: list[str] = []
    for asset in doc["task_context"].get("assets") or []:
        rel = asset.get("asset_file")
        if not asset.get("included") or not rel:
            problems.append(f"{asset['name']}: not marked as included")
            continue
        path = task_dir / rel
        if not path.is_file():
            problems.append(f"{rel}: declared but missing from the package")
        elif sha256_of(path) != asset.get("sha256"):
            problems.append(f"{rel}: sha256 differs from the sealed manifest")
    return problems


def task_dirs(root: Path) -> list[Path]:
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / TASK_JSON_NAME).is_file())
    if not dirs:
        raise SystemExit(f"no task folders (containing {TASK_JSON_NAME}) under {root}")
    return dirs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--search-root", action="append", type=Path, default=[],
                        help="local tree to resolve assets from (repeatable); required unless --check")
    parser.add_argument("--check", action="store_true", help="verify the packaged assets, exit non-zero if any is wrong")
    parser.add_argument("root", nargs="?", default=None, help="package root (default: parent of _tools/)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    if args.check:
        problems = [f"{d.name}: {p}" for d in task_dirs(root) for p in check_task(d)]
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"checked {len(task_dirs(root))} tasks: {len(problems)} problem(s)", file=sys.stderr)
        return 1 if problems else 0

    if not args.search_root:
        raise SystemExit("--search-root is required (repeat it for several source trees)")
    search_roots = []
    for candidate in args.search_root:
        resolved = candidate.expanduser().resolve()
        if not resolved.is_dir():
            raise SystemExit(f"--search-root is not a directory: {resolved}")
        search_roots.append(resolved)

    failed = 0
    for task_dir in task_dirs(root):
        print(task_dir.name)
        failures = collect_task(task_dir, search_roots)
        failed += len(failures)
        for failure in failures:
            print(f"  FAILED {failure}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
