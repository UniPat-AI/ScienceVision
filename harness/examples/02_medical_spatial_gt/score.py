"""Stage-1 offline scorer for the medical-spatial demo.

Reads the predicted answer from --prediction (a text file containing a
`\\boxed{<class name>}` token), computes the ground truth from
data/mask.nii.gz (label with the largest voxel count, mapped to a class
name), and emits a JSON verdict. Exit 0 on match, 1 on mismatch, 2 on a
setup error (missing prediction file or missing mask).

Usage:
    python score.py --prediction my_answer.txt
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import nibabel as nib
import numpy as np

LABEL_NAMES: dict[int, str] = {
    1: "Renal vein",
    2: "Kidney",
    3: "Renal artery",
    4: "Tumor",
}

_BOXED_RE = re.compile(r"\\boxed\{([^}]+)\}")


def extract_answer(text: str) -> str | None:
    """Return the contents of the first `\\boxed{...}` token, stripped."""
    match = _BOXED_RE.search(text)
    return match.group(1).strip() if match else None


def ground_truth(mask_path: pathlib.Path) -> str:
    """Return the LABEL_NAMES entry with the largest voxel count in the mask."""
    arr = np.asarray(nib.load(str(mask_path)).dataobj, dtype=np.int64)
    counts = {label: int((arr == label).sum()) for label in LABEL_NAMES}
    largest_label = max(counts, key=lambda lbl: counts[lbl])
    return LABEL_NAMES[largest_label]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage-1 offline scorer for the medical-spatial demo.")
    ap.add_argument("--prediction", type=pathlib.Path, required=True,
                    help="Text file containing the model's answer.")
    ap.add_argument("--mask", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parent / "data" / "mask.nii.gz",
                    help="NIfTI mask used to compute ground truth.")
    args = ap.parse_args(argv)

    if not args.prediction.exists():
        print(f"error: prediction file not found: {args.prediction}", file=sys.stderr)
        return 2
    if not args.mask.exists():
        print(
            f"error: mask not found: {args.mask}\n"
            "       Did you run `bash data/fetch.sh`?",
            file=sys.stderr,
        )
        return 2

    try:
        expected = ground_truth(args.mask)
    except Exception as exc:  # noqa: BLE001 — surface any mask-load failure as a setup error
        print(f"error: failed to load mask {args.mask}: {exc}", file=sys.stderr)
        return 2
    try:
        predicted = extract_answer(args.prediction.read_text())
    except (UnicodeDecodeError, OSError) as exc:
        print(f"error: failed to read prediction {args.prediction}: {exc}", file=sys.stderr)
        return 2

    verdict = {
        "expected": expected,
        "predicted": predicted,
        "match": predicted == expected,
    }
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
