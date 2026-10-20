"""Mirror selected files from a sibling ScienceVision checkout into this repo.

Reads `.mirror-include` (one glob per line, `#` for comments). Each glob is
evaluated relative to the source repo root. Matching files are copied to
this repo at the same relative path. Files already in this repo that fall
under any mirrored path-prefix but are no longer matched are deleted.

CLI:
    python tools/sync_from_sciencevision.py --source ../ScienceVision --dry-run
    python tools/sync_from_sciencevision.py --source ../ScienceVision
"""
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import os
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = (REPO_ROOT.parent / "ScienceVision").resolve()
DEFAULT_MANIFEST = REPO_ROOT / ".mirror-include"

_PRUNE_DIRS = frozenset(
    {".git", ".qagen", "__pycache__", ".venv", ".pytest_cache",
     ".ruff_cache", ".mypy_cache", "build", "dist", "node_modules"}
)
_PRUNE_DIR_SUFFIXES = (".egg-info",)
_PRUNE_FILES = frozenset({".coverage"})
_PRUNE_FILE_SUFFIXES = (".pyc", ".pyo")
_FULL_GIT_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


class SourceCommitError(ValueError):
    """The requested source commit cannot be verified against the checkout."""


def _is_pruned_dir(name: str) -> bool:
    return name in _PRUNE_DIRS or name.endswith(_PRUNE_DIR_SUFFIXES)


def _is_pruned_file(name: str) -> bool:
    return name in _PRUNE_FILES or name.endswith(_PRUNE_FILE_SUFFIXES)


def _clean_git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        environment.pop(variable, None)
    return environment


@dataclasses.dataclass(frozen=True)
class Plan:
    copies: list[pathlib.Path]
    deletes: list[pathlib.Path]


def parse_manifest(manifest: pathlib.Path) -> list[str]:
    patterns: list[str] = []
    for raw in manifest.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def verify_source_commit(source: pathlib.Path, expected: str) -> str:
    """Require a Git worktree rooted at source whose HEAD is expected."""
    if not _FULL_GIT_COMMIT.fullmatch(expected):
        raise SourceCommitError("expected source commit must be a full 40-character SHA")
    git_env = _clean_git_environment()
    try:
        root_result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=git_env,
        )
        head_result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=git_env,
        )
        status_result = subprocess.run(
            [
                "git", "-C", str(source), "status", "--porcelain=v1",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            env=git_env,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise SourceCommitError(f"source is not a readable Git worktree: {source}") from exc

    worktree_root = pathlib.Path(root_result.stdout.strip()).resolve()
    if worktree_root != source.resolve():
        raise SourceCommitError(
            f"source must be the Git worktree root: {source} (found {worktree_root})"
        )
    actual = head_result.stdout.strip().lower()
    if actual != expected.lower():
        raise SourceCommitError(
            f"source HEAD {actual} does not match expected commit {expected.lower()}"
        )
    dirty_entries = [line for line in status_result.stdout.splitlines() if line]
    if dirty_entries:
        preview = "; ".join(dirty_entries[:5])
        if len(dirty_entries) > 5:
            preview += f"; ... and {len(dirty_entries) - 5} more"
        raise SourceCommitError(f"source worktree is not clean: {preview}")
    return actual


def tracked_files_at_commit(source: pathlib.Path, commit: str) -> set[pathlib.Path]:
    """Return the exact file paths tracked by commit in source's repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "ls-tree", "-r", "-z", "--name-only", commit],
            capture_output=True,
            check=True,
            timeout=30,
            env=_clean_git_environment(),
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise SourceCommitError(f"cannot read tracked files for source commit {commit}") from exc
    return {
        pathlib.Path(os.fsdecode(raw_path))
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    }


def _pattern_prefix(pattern: str) -> str:
    """Longest fixed-string directory prefix of a glob."""
    cut = len(pattern)
    for i, ch in enumerate(pattern):
        if ch in "*?[":
            cut = i
            break
    head = pattern[:cut]
    return head[: head.rfind("/") + 1]


def matches_any(rel: pathlib.Path, patterns: list[str]) -> bool:
    rel_str = rel.as_posix()
    for pat in patterns:
        if pat.endswith("/**"):
            prefix = pat[:-3] + "/"
            if rel_str.startswith(prefix):
                return True
            continue
        # Enforce depth: single '*' must not cross a path separator.
        if rel_str.count("/") != pat.count("/"):
            continue
        if fnmatch.fnmatchcase(rel_str, pat):
            return True
    return False


def _iter_source(source: pathlib.Path, patterns: list[str]) -> Iterable[pathlib.Path]:
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not _is_pruned_dir(d)]
        root_path = pathlib.Path(root)
        for fn in files:
            if _is_pruned_file(fn):
                continue
            rel = (root_path / fn).relative_to(source)
            if matches_any(rel, patterns):
                yield rel


def _iter_dest_under_prefixes(
    dest: pathlib.Path, prefixes: Iterable[str]
) -> Iterable[pathlib.Path]:
    seen: set[pathlib.Path] = set()
    for prefix in prefixes:
        if not prefix:
            continue
        base = dest / prefix
        if not base.exists():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not _is_pruned_dir(d)]
            for fn in files:
                if _is_pruned_file(fn):
                    continue
                rel = (pathlib.Path(root) / fn).relative_to(dest)
                if rel not in seen:
                    seen.add(rel)
                    yield rel


def compute_plan(
    source: pathlib.Path, dest: pathlib.Path, patterns: list[str]
) -> Plan:
    source_files = sorted(_iter_source(source, patterns))
    keep = set(source_files)
    prefixes = {_pattern_prefix(p) for p in patterns}
    dest_existing = set(_iter_dest_under_prefixes(dest, prefixes))
    # Only consider deletes that the patterns would have included if they
    # existed in source — i.e. things under a mirrored prefix that match
    # some pattern.
    delete_candidates = {p for p in dest_existing if matches_any(p, patterns)}
    deletes = sorted(delete_candidates - keep)
    return Plan(copies=source_files, deletes=deletes)


def apply_plan(plan: Plan, source: pathlib.Path, dest: pathlib.Path) -> None:
    for rel in plan.copies:
        src = source / rel
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel in plan.deletes:
        target = dest / rel
        if target.exists():
            target.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE,
                    help="Path to ScienceVision checkout (default: ../ScienceVision)")
    ap.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--dest", type=pathlib.Path, default=REPO_ROOT)
    ap.add_argument(
        "--expected-source-commit",
        metavar="FULL_SHA",
        help="Fail unless --source is a Git worktree rooted at this exact HEAD.",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Print actions without touching disk.")
    args = ap.parse_args(argv)

    if not args.source.exists():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2
    if not args.manifest.exists():
        print(f"error: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    tracked_source_files: set[pathlib.Path] | None = None
    if args.expected_source_commit:
        try:
            verified_commit = verify_source_commit(
                args.source.resolve(), args.expected_source_commit
            )
            tracked_source_files = tracked_files_at_commit(
                args.source.resolve(), verified_commit
            )
        except SourceCommitError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"verified source commit: {verified_commit}")

    patterns = parse_manifest(args.manifest)
    plan = compute_plan(args.source.resolve(), args.dest.resolve(), patterns)
    if tracked_source_files is not None:
        untracked_copies = sorted(set(plan.copies) - tracked_source_files)
        if untracked_copies:
            preview = "; ".join(path.as_posix() for path in untracked_copies[:5])
            if len(untracked_copies) > 5:
                preview += f"; ... and {len(untracked_copies) - 5} more"
            print(
                "error: source contains manifest-matched files not tracked by "
                f"the expected commit: {preview}",
                file=sys.stderr,
            )
            return 2
    print(f"plan: {len(plan.copies)} copies, {len(plan.deletes)} deletes")
    if args.dry_run:
        for rel in plan.copies[:50]:
            print(f"  + {rel}")
        if len(plan.copies) > 50:
            print(f"  ... and {len(plan.copies) - 50} more copies")
        for rel in plan.deletes:
            print(f"  - {rel}")
        return 0

    apply_plan(plan, args.source.resolve(), args.dest.resolve())
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
