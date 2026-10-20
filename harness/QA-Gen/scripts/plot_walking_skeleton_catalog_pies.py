#!/usr/bin/env python3
"""Create pie-chart summaries for the WalkingSkeleton catalog."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping


# Keep matplotlib and fontconfig caches out of the user's home directory in
# sandboxed runs.
cache_root = Path(tempfile.gettempdir()) / "sciencevision_plot_cache"
mpl_cache = cache_root / "matplotlib"
cache_root.mkdir(parents=True, exist_ok=True)
mpl_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
os.environ.setdefault("MPLCONFIGDIR", str(mpl_cache))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


SCRIPT_DIR = Path(__file__).resolve().parent
QA_GEN_ROOT = SCRIPT_DIR.parent
DEFAULT_INPUT = (
    QA_GEN_ROOT
    / "tasks"
    / "WalkingSkeleton"
    / "ScienceVision_WalkingSkeleton_catalog_summary.json"
)
DEFAULT_OUTPUT_DIR = QA_GEN_ROOT / "figures"
DEFAULT_OUTPUT_NAME = "ScienceVision_WalkingSkeleton_catalog_pie_summary.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot pie-chart statistics from the WalkingSkeleton catalog summary JSON."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Catalog summary JSON path. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated figures. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--output-name",
        default=DEFAULT_OUTPUT_NAME,
        help=f"Output PNG filename. Default: {DEFAULT_OUTPUT_NAME}",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Maximum categories to show before folding the rest into Other.",
    )
    return parser.parse_args()


def load_summary(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def top_items_with_other(values: Mapping[str, int], top_n: int) -> dict[str, int]:
    """Return top categories by count, folding the remaining positive values into Other."""
    sorted_items = sorted(values.items(), key=lambda item: item[1], reverse=True)
    if top_n <= 0 or len(sorted_items) <= top_n:
        return dict(sorted_items)

    head = sorted_items[:top_n]
    tail_total = sum(count for _, count in sorted_items[top_n:])
    compacted = dict(head)
    if tail_total > 0:
        compacted["Other"] = tail_total
    return compacted


def autopct_factory(total: int):
    def format_pct(pct: float) -> str:
        count = int(round(pct * total / 100.0))
        return f"{pct:.1f}%\n({count})" if pct >= 3 else ""

    return format_pct


def plot_pie(ax: plt.Axes, title: str, values: Mapping[str, int]) -> None:
    labels = list(values.keys())
    counts = list(values.values())
    total = sum(counts)
    colors = plt.get_cmap("tab20").colors[: len(counts)]

    wedges, _, autotexts = ax.pie(
        counts,
        labels=None,
        colors=colors,
        startangle=90,
        counterclock=False,
        autopct=autopct_factory(total),
        pctdistance=0.72,
        wedgeprops={"linewidth": 1, "edgecolor": "white"},
        textprops={"fontsize": 9, "color": "#1f2933"},
    )
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
    ax.axis("equal")
    ax.legend(
        wedges,
        [f"{label}: {count}" for label, count in zip(labels, counts)],
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        fontsize=8,
    )
    for text in autotexts:
        text.set_fontweight("bold")


def build_availability(summary: Mapping[str, int]) -> dict[str, int]:
    asset_count = int(summary["asset_count"])
    missing = int(summary["missing_on_disk"])
    available = max(asset_count - missing, 0)
    return {"Available on disk": available, "Missing on disk": missing}


def main() -> None:
    args = parse_args()
    summary = load_summary(args.input)

    charts = [
        ("Assets by Domain", top_items_with_other(summary["assets_by_domain"], args.top_n)),
        ("Assets by Viewer", top_items_with_other(summary["assets_by_viewer"], args.top_n)),
        ("Assets by Format", top_items_with_other(summary["assets_by_format"], args.top_n)),
        ("Disk Availability", build_availability(summary)),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(16, 11))
    fig.suptitle(
        "ScienceVision WalkingSkeleton Catalog Summary",
        fontsize=18,
        fontweight="bold",
        y=0.98,
    )

    for ax, (title, values) in zip(axes.ravel(), charts):
        plot_pie(ax, title, values)

    fig.text(
        0.5,
        0.02,
        (
            f"Total assets: {summary['asset_count']} | "
            f"Domains: {summary['domain_count']} | "
            f"Matched QA total: {summary['matched_qa_total']} | "
            f"Top-N categories shown before Other: {args.top_n}"
        ),
        ha="center",
        fontsize=10,
        color="#4b5563",
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / args.output_name
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(output_path)


if __name__ == "__main__":
    main()
