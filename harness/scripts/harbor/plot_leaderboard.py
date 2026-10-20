"""Build chart data + bar charts from the QA-Gen dual-harness leaderboard.

Two steps:
  1. Aggregate the leaderboard rows by (harness, domain) for every reward
     component (reward, answer_score, process_compliance, optional shadows) and save a
     compact ``chart_data.json``.
  2. Render bar charts:
       leaderboard_reward_by_domain.png   total reward, grouped by domain,
                                           one bar per harness
       leaderboard_components_by_domain.png  per-component reward (exact_match /
                                           llm_judge / process_judge), faceted
                                           by domain, grouped by harness

Inputs come from ``runs_leaderboard.json`` (written by collect_qagen_results).

Usage:
    python -m scripts.harbor.plot_leaderboard \
        --leaderboard QA-Gen/tasks/runs_leaderboard.json \
        --out-dir QA-Gen/tasks/charts
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

_COMPONENTS = [
    "reward",
    "answer_score",
    "process_compliance",
    "shadow_llm_judge",
    "shadow_process_judge",
]


def aggregate(rows: list[dict]) -> dict:
    """Mean of each component per (harness, domain), completed rows only."""
    # buckets[harness][domain][component] -> list of values
    buckets: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    harnesses: set[str] = set()
    domains: set[str] = set()
    excluded_uncitable = 0
    for r in rows:
        if r.get("status") != "ok":
            continue
        if r.get("evaluation_eligible") is not True:
            continue
        # Citable only, on the same rule the leaderboard's reward column uses.
        # This chart is the artifact that gets screenshotted, so a blocked run
        # re-entering here would contradict the table it was drawn from.
        if r.get("citable") is not True:
            excluded_uncitable += 1
            continue
        h, dom = r.get("harness", "?"), r.get("domain", "?")
        harnesses.add(h)
        domains.add(dom)
        for c in _COMPONENTS:
            v = r.get(c)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                buckets[h][dom][c].append(float(v))

    def mean(xs: list[float]):
        return sum(xs) / len(xs) if xs else None

    if excluded_uncitable and not harnesses:
        # Say so rather than letting the chart silently fail to appear: a missing
        # PNG is indistinguishable from a crashed step to whoever reads the
        # pipeline log. The census in runs_leaderboard.json says which state each
        # dropped row was in.
        print(
            f"warning: all {excluded_uncitable} eligible row(s) were uncitable, "
            "so there is nothing to plot; see the citability census in "
            "runs_leaderboard.json",
            file=sys.stderr,
        )

    means: dict[str, dict[str, dict[str, float | None]]] = {}
    for h in harnesses:
        means[h] = {}
        for dom in domains:
            means[h][dom] = {c: mean(buckets[h][dom][c]) for c in _COMPONENTS}

    return {
        "harnesses": sorted(harnesses),
        "domains": sorted(domains),
        "components": _COMPONENTS,
        "means": means,
    }


def plot_reward_by_domain(data: dict, out_path: Path) -> None:
    harnesses, domains = data["harnesses"], data["domains"]
    x = range(len(domains))
    width = 0.8 / max(1, len(harnesses))

    fig, ax = plt.subplots(figsize=(8, 5))
    for i, h in enumerate(harnesses):
        vals = [data["means"][h][d]["reward"] or 0.0 for d in domains]
        offs = [xi + (i - (len(harnesses) - 1) / 2) * width for xi in x]
        bars = ax.bar(offs, vals, width=width, label=h)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.2f}",
                    ha="center", va="bottom", fontsize=8)

    ax.set_xticks(list(x))
    ax.set_xticklabels(domains)
    ax.set_ylabel("mean reward")
    ax.set_ylim(0, 1.0)
    ax.set_title("QA-Gen mean reward by domain (per harness)")
    ax.legend(title="harness")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_components_by_domain(data: dict, out_path: Path) -> None:
    harnesses, domains = data["harnesses"], data["domains"]
    comps = [c for c in data["components"] if c != "reward"]  # the breakdown
    fig, axes = plt.subplots(1, len(domains), figsize=(6 * len(domains), 5),
                             sharey=True, squeeze=False)
    x = range(len(comps))
    width = 0.8 / max(1, len(harnesses))

    for col, dom in enumerate(domains):
        ax = axes[0][col]
        for i, h in enumerate(harnesses):
            raw = [data["means"][h][dom][c] for c in comps]
            # null = scorer not run (e.g. k-axis exact_match unimplemented) —
            # distinct from a real 0.0; plot null as no bar + an "n/a" label.
            vals = [v if v is not None else 0.0 for v in raw]
            offs = [xi + (i - (len(harnesses) - 1) / 2) * width for xi in x]
            bars = ax.bar(offs, vals, width=width, label=h)
            for b, v, rv in zip(bars, vals, raw):
                if rv is None:
                    ax.text(b.get_x() + b.get_width() / 2, 0.01, "n/a",
                            ha="center", va="bottom", fontsize=7, color="gray",
                            style="italic")
                else:
                    ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.2f}",
                            ha="center", va="bottom", fontsize=7)
        ax.set_xticks(list(x))
        ax.set_xticklabels(comps, rotation=20, ha="right")
        ax.set_title(f"domain: {dom}")
        ax.set_ylim(0, 1.05)
        ax.grid(axis="y", alpha=0.3)
        if col == 0:
            ax.set_ylabel("mean score")
        ax.legend(title="harness", fontsize=8)

    fig.suptitle("QA-Gen reward components by domain (per harness)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--leaderboard", type=Path, default=Path("QA-Gen/tasks/runs_leaderboard.json")
    )
    ap.add_argument("--out-dir", type=Path, default=Path("QA-Gen/tasks/charts"))
    args = ap.parse_args()

    board = json.loads(args.leaderboard.read_text())
    data = aggregate(board.get("rows", []))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "chart_data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )

    # Zero plottable domains (e.g. every baseline scored 0 "ok" rows in an
    # all-fail run) means plt.subplots(1, 0, ...) below would raise
    # ValueError: Number of columns must be a positive integer, not 0.
    # collect_qagen_results already persisted the leaderboard by this point,
    # so degrade cleanly here rather than letting the one-command end2end
    # driver die on this last, purely-cosmetic step.
    if not data["domains"]:
        print(
            "no plottable domains (0 completed rows in the leaderboard) -- "
            "skipping chart generation",
            file=sys.stderr,
        )
        return

    plot_reward_by_domain(data, args.out_dir / "leaderboard_reward_by_domain.png")
    plot_components_by_domain(data, args.out_dir / "leaderboard_components_by_domain.png")

    print(f"harnesses: {data['harnesses']}")
    print(f"domains:   {data['domains']}")
    print(f"wrote {args.out_dir}/chart_data.json + 2 PNG charts")


if __name__ == "__main__":
    main()
