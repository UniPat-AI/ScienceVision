"""Read datasets/_matched/index.json and print explicit `git add` lines.

Never invokes git. Output is the list of paths the user should consider staging — meshes,
masks, NIfTI volumes, npy volumes, demo .ply files, the originating QA source files, and
the _matched/ JSONs themselves.

Usage:
  python scripts/match/print_git_add.py
  python scripts/match/print_git_add.py --dataset scanqa     # filter
  python scripts/match/print_git_add.py --repo-root /path    # for tests
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

# QA source files per dataset — these are the on-disk files the matched JSONs were derived from.
# Listed explicitly (not auto-discovered) so the staging set is auditable.
QA_SOURCE_FILES: dict[str, list[str]] = {
    "scanqa": [
        "datasets/ScanQA/data/qa/ScanQA_v1.0/ScanQA_v1.0_test_wo_obj.json",
        "datasets/ScanQA/data/qa/ScanQA_v1.0/ScanQA_v1.0_val.json",
    ],
    "sqa3d": [
        "datasets/SQA3D/sqa_task/balanced/v1_balanced_questions_test_scannetv2.json",
        "datasets/SQA3D/sqa_task/balanced/v1_balanced_sqa_annotations_test_scannetv2.json",
    ],
    "m3d-refseg": [
        "datasets/M3D-RefSeg/M3D_RefSeg_train.csv",
        "datasets/M3D-RefSeg/M3D_RefSeg_test.csv",
    ],
    "m3d-seg-0005": [
        "datasets/M3D-Seg/M3D_Seg/0005/0005_subset20.json",
    ],
    "embodiedscan-demo": [],
}


def _collect_paths(index: dict[str, Any], matched_dir: Path, dataset_filter: str | None) -> list[str]:
    paths: set[str] = {"datasets/_matched/index.json"}
    for entry in index["files"]:
        ds = entry["dataset"]
        if dataset_filter and ds != dataset_filter:
            continue
        # The per-dataset matched JSON.
        paths.add(entry["path_repo"])
        # QA source files for this dataset.
        for qa in QA_SOURCE_FILES.get(ds, []):
            paths.add(qa)
        # Per-record asset paths.
        ds_payload = json.loads((matched_dir / f"{ds}.json").read_text())
        for rec in ds_payload["records"]:
            paths.add(rec["path_repo"])
            mask = rec.get("extra", {}).get("mask_path_repo")
            if mask:
                paths.add(mask)
    return sorted(paths)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="repo root to resolve paths against (default: auto-detected from script location; override in tests)",
    )
    ap.add_argument("--dataset", help="emit lines only for this dataset (scanqa | sqa3d | m3d-refseg | m3d-seg-0005 | embodiedscan-demo)")
    args = ap.parse_args()

    matched_dir = args.repo_root / "datasets" / "_matched"
    index = json.loads((matched_dir / "index.json").read_text())
    for p in _collect_paths(index, matched_dir, args.dataset):
        print(f"git add {p}")


if __name__ == "__main__":
    main()
