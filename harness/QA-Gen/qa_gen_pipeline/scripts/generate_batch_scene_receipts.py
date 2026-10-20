#!/usr/bin/env python3
"""Generate private, browser-attested scene receipts for a frozen batch.

This command has no model-provider dependency.  It is deliberately separate
from the chirality-only live executor: its output is local reviewer evidence,
not an authorization to generate or release a question.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qagen.paths import WorkspacePaths
from qagen.viewer.batch_scene_replay import (
    BatchSceneRegistry,
    replay_batch_scene_registry,
)


def _absolute(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=_absolute, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--registry", type=_absolute)
    source.add_argument("--selection-batch", type=_absolute)
    parser.add_argument("--asset-root", type=_absolute)
    parser.add_argument("--viewer-package-root", type=_absolute, required=True)
    parser.add_argument("--per-slot-timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    if args.selection_batch is not None and args.asset_root is None:
        parser.error("--asset-root is required with --selection-batch")
    if args.registry is not None and args.asset_root is not None:
        parser.error("--asset-root is only valid with --selection-batch")

    paths = WorkspacePaths.from_value(args.workspace)
    if args.registry is not None:
        registry = BatchSceneRegistry.load(paths, args.registry)
    else:
        registry = BatchSceneRegistry.build(
            paths,
            selection_batch=args.selection_batch,
            asset_root=args.asset_root,
        )
    result = replay_batch_scene_registry(
        paths,
        registry_path=registry.path,
        viewer_package_root=args.viewer_package_root,
        per_slot_timeout_seconds=args.per_slot_timeout_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
