#!/usr/bin/env python3
"""Build private, selector-only SceneInventory artifacts for frozen V32 scenes.

Run this command with the explicit ``measurement`` extra installed. It reads
only private, attested source records and a private controlled-scene registry;
the resulting 0600 artifacts contain anonymous selector IDs, asset digests,
and structural catalog data, never historic question/answer content or truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qagen.measurement import materialize_v32_scene_inventories
from qagen.paths import WorkspacePaths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument(
        "--parent-inventory-batch",
        type=Path,
        help="Optional ready parent inventory-batch receipt for Fresh5 selector-equivalence attestation.",
    )
    args = parser.parse_args(argv)

    result = materialize_v32_scene_inventories(
        WorkspacePaths.from_value(args.workspace),
        registry_path=args.registry,
        parent_inventory_batch_path=args.parent_inventory_batch,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI entry point.
    raise SystemExit(main())
