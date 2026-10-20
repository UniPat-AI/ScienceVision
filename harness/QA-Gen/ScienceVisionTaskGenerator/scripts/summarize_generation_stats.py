#!/usr/bin/env python3
"""Summarize ScienceVision task generation token/cost and filtering stats."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TASKGEN_ROOT = Path(__file__).resolve().parents[1]
if str(TASKGEN_ROOT) not in sys.path:
    sys.path.insert(0, str(TASKGEN_ROOT))

from sciencevision_taskgen.stats import stats_to_markdown, summarize_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Run directory or task output directory to summarize.")
    parser.add_argument("--out-json", help="Path for JSON summary.")
    parser.add_argument("--out-md", help="Path for Markdown summary.")
    parser.add_argument("--input-price-per-mtok", type=float, default=None)
    parser.add_argument("--output-price-per-mtok", type=float, default=None)
    parser.add_argument("--total-price-per-mtok", type=float, default=None)
    args = parser.parse_args()

    stats = summarize_run(
        args.run_dir,
        input_price_per_mtok=args.input_price_per_mtok,
        output_price_per_mtok=args.output_price_per_mtok,
        total_price_per_mtok=args.total_price_per_mtok,
    )
    md = stats_to_markdown(stats)

    if args.out_json:
        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_md:
        out = Path(args.out_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")

    print(md)


if __name__ == "__main__":
    main()
