#!/usr/bin/env python3
"""Stage the exact, minimal Docker context for ``sci3d-scorer``.

No repository tree is passed directly to Docker. The explicit allow-list is
defined in :mod:`manifest`, and the generated package initialiser excludes
LLM/mask/MCP scorer imports by construction.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from manifest import (
    BUNDLED_SOURCE_FILES,
    sha256_file,
    source_date_epoch_from_environment,
    write_manifest,
)


MINIMAL_SCORERS_INIT = '''"""Formal deterministic scorers shipped in sci3d-scorer only."""\nfrom sci3d_bench.scorers.base import ScoreResult, Scorer\nfrom sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer\nfrom sci3d_bench.scorers.extent_voxel_tolerance import ExtentVoxelToleranceScorer\n\n__all__ = ["ScoreResult", "Scorer", "DeterministicAnswerScorer", "ExtentVoxelToleranceScorer"]\n'''
MINIMAL_PACKAGE_INIT = '"""Namespace intentionally limited to formal scoring code."""\n'


def _write_bytes(path: Path, value: bytes, epoch: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    os.utime(path, (epoch, epoch))


def _copy_file(source: Path, destination: Path, epoch: int) -> None:
    _write_bytes(destination, source.read_bytes(), epoch)


def stage(repo_root: Path, output: Path) -> dict[str, str]:
    repo_root = repo_root.resolve()
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty build context: {output}")
    output.mkdir(parents=True, exist_ok=True)
    epoch = source_date_epoch_from_environment()
    # A fixed default prevents copied source mtimes from becoming an invisible
    # image input. Callers may supply SOURCE_DATE_EPOCH for release metadata.
    epoch = 0 if epoch is None else epoch

    src = output / "src"
    for relative in BUNDLED_SOURCE_FILES:
        _copy_file(repo_root / relative, src / relative, epoch)
    _write_bytes(
        src / "sci3d_bench/__init__.py", MINIMAL_PACKAGE_INIT.encode("utf-8"), epoch
    )
    _write_bytes(
        src / "harbor_adapters/__init__.py", MINIMAL_PACKAGE_INIT.encode("utf-8"), epoch
    )
    _write_bytes(
        src / "harbor_adapters/sci3d/__init__.py",
        MINIMAL_PACKAGE_INIT.encode("utf-8"),
        epoch,
    )
    _write_bytes(
        src / "sci3d_bench/scorers/__init__.py",
        MINIMAL_SCORERS_INIT.encode("utf-8"),
        epoch,
    )

    bundle_root = Path(__file__).resolve().parent
    for name in ("scorer_cli.py", "manifest.py", "offline.py", "VERSION", "Dockerfile"):
        _copy_file(bundle_root / name, output / name, epoch)

    runtime_files = {
        path.relative_to(output).as_posix(): sha256_file(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "scorer_manifest.json"
    }
    version = (bundle_root / "VERSION").read_text(encoding="utf-8").strip()
    write_manifest(
        output / "scorer_manifest.json",
        repo_root,
        bundle_version=version,
        source_date_epoch=epoch,
        runtime_files=runtime_files,
    )
    os.utime(output / "scorer_manifest.json", (epoch, epoch))
    return runtime_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    stage(args.repo_root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
