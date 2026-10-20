"""Analyze SAM-as-tool ablation pilot results and emit figures + a summary.

Reads evaluation_runs/ablation_pilot/<exp>_<tier>.json (written by
run_ablation_pilot.py) and produces, under the report figs/ dir:

  ablation_accuracy.png   — per-experiment grouped bars: SEG@{perfect,noisy,
                            wrong} vs no_vision baseline; delta annotated.
  ablation_score_vs_correct.png — for SEG runs, mean SAM score split by
                            whether the agent's final answer was correct.
  case_<exp>_<tier>.png   — one worked case per experiment/tier: question,
                            gold, SEG vs baseline answers, SAM score/centroid.

Also writes results_pilot.json (aggregated) next to the figures and prints a
markdown table to stdout for pasting into the report.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "evaluation_runs/ablation_pilot"
FIGS = REPO / "docs/superpowers/reports/2026-05-28-sam-tool/figs"
FIGS.mkdir(parents=True, exist_ok=True)

TIERS = ("perfect", "noisy", "wrong")
EXPS = ("e2_volume", "e1_mesh")
EXP_TITLE = {"e2_volume": "E2 · volume (nifti)", "e1_mesh": "E1 · mesh (scene)"}
TIER_COLOR = {"perfect": "#2e7d32", "noisy": "#f9a825", "wrong": "#c62828"}


def _load():
    data = {}
    for f in sorted(RUNS.glob("*.json")):
        d = json.loads(f.read_text())
        data[(d["experiment"], d["tier"])] = d
    return data


def _acc(runs, mode):
    rs = [r for r in runs if r["mode"] == mode]
    n = len(rs)
    return (sum(1 for r in rs if r["correct"]) / n if n else 0.0), n


def fig_accuracy(data):
    fig, axes = plt.subplots(1, len(EXPS), figsize=(11, 4.2), squeeze=False)
    for ax, exp in zip(axes[0], EXPS):
        present = [(t, data[(exp, t)]) for t in TIERS if (exp, t) in data]
        if not present:
            ax.set_visible(False)
            continue
        seg_mode = present[0][1]["seg_mode"]
        base_mode = present[0][1]["baseline_mode"]
        labels, seg_accs, base_accs, colors = [], [], [], []
        for t, d in present:
            sa, _ = _acc(d["runs"], seg_mode)
            ba, _ = _acc(d["runs"], base_mode)
            labels.append(t)
            seg_accs.append(sa)
            base_accs.append(ba)
            colors.append(TIER_COLOR[t])
        x = range(len(labels))
        ax.bar([i - 0.2 for i in x], seg_accs, width=0.4, color=colors,
               label="SEG (with segmentation)")
        ax.bar([i + 0.2 for i in x], base_accs, width=0.4, color="#90a4ae",
               label="no_vision baseline")
        for i, (sa, ba) in enumerate(zip(seg_accs, base_accs)):
            ax.text(i - 0.2, sa + 0.02, f"{sa:.2f}", ha="center", fontsize=8)
            ax.text(i + 0.2, ba + 0.02, f"{ba:.2f}", ha="center", fontsize=8)
            ax.text(i, max(sa, ba) + 0.09, f"Δ{sa - ba:+.2f}", ha="center",
                    fontsize=9, fontweight="bold",
                    color="#2e7d32" if sa >= ba else "#c62828")
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{l}\n(n={_acc(data[(exp,l)]['runs'], seg_mode)[1]})"
                            for l in labels])
        ax.set_ylim(0, 1.18)
        ax.set_ylabel("answer accuracy")
        ax.set_title(EXP_TITLE[exp])
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("SAM-as-tool ablation (PILOT) — accuracy by SAM quality tier",
                 fontweight="bold")
    fig.tight_layout()
    out = FIGS / "ablation_accuracy.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def fig_score_vs_correct(data):
    # gather all SEG runs that actually invoked segmentation
    rows = []  # (tier, score, correct)
    for (exp, tier), d in data.items():
        for r in d["runs"]:
            if r["mode"] == d["seg_mode"] and r.get("seg_scores"):
                rows.append((tier, max(r["seg_scores"]), r["correct"]))
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    import numpy as np
    for tier in TIERS:
        sc_correct = [s for t, s, c in rows if t == tier and c]
        sc_wrong = [s for t, s, c in rows if t == tier and not c]
        if sc_correct:
            ax.scatter([tier] * len(sc_correct),
                       np.array(sc_correct) + np.random.uniform(-0.01, 0.01, len(sc_correct)),
                       c="#2e7d32", marker="o", s=40, label="correct" if tier == "perfect" else None)
        if sc_wrong:
            ax.scatter([tier] * len(sc_wrong),
                       np.array(sc_wrong) + np.random.uniform(-0.01, 0.01, len(sc_wrong)),
                       c="#c62828", marker="x", s=40, label="wrong" if tier == "perfect" else None)
    ax.set_ylabel("SAM score (returned to agent)")
    ax.set_xlabel("fake SAM quality tier")
    ax.set_title("SAM score vs answer correctness (SEG runs)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = FIGS / "ablation_score_vs_correct.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def _short(x, n=180):
    s = json.dumps(x, default=str) if not isinstance(x, str) else x
    return textwrap.fill(s[:n] + ("…" if len(s) > n else ""), 60)


def fig_case(data, exp, tier):
    key = (exp, tier)
    if key not in data:
        return None
    d = data[key]
    seg = next((r for r in d["runs"] if r["mode"] == d["seg_mode"]
                and r.get("seg_scores")), None)
    if seg is None:
        return None
    base = next((r for r in d["runs"]
                 if r["mode"] == d["baseline_mode"] and r["qid"] == seg["qid"]), None)
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.axis("off")
    lines = [
        f"CASE — {EXP_TITLE[exp]} · SAM tier = {tier}",
        f"qid: {seg['qid']}",
        "",
        f"Q: {_short(seg['question'], 200)}",
        f"gold: {_short(seg['gold'], 120)}",
        "",
        f"SAM returned: score={seg['seg_scores']}  (steps={seg['n_steps']})",
        "",
        f"[SEG · with segmentation]  correct={seg['correct']}",
        f"   {_short(seg['pred'], 240)}",
        "",
        f"[no_vision baseline]       correct={base['correct'] if base else 'NA'}",
        f"   {_short(base['pred'] if base else None, 200)}",
    ]
    ax.text(0.01, 0.99, "\n".join(lines), va="top", ha="left",
            family="monospace", fontsize=8.5, transform=ax.transAxes)
    edge = TIER_COLOR[tier]
    for s in ax.spines.values():
        s.set_visible(False)
    fig.patch.set_edgecolor(edge)
    fig.patch.set_linewidth(3)
    fig.tight_layout()
    out = FIGS / f"case_{exp}_{tier}.png"
    fig.savefig(out, dpi=130, edgecolor=edge)
    plt.close(fig)
    return out


def main():
    data = _load()
    if not data:
        print("no result files found in", RUNS)
        return 1
    agg = {}
    print("\n| experiment | tier | SEG acc | base acc | Δ | n |")
    print("|---|---|---|---|---|---|")
    for exp in EXPS:
        for tier in TIERS:
            if (exp, tier) not in data:
                continue
            d = data[(exp, tier)]
            sa, n = _acc(d["runs"], d["seg_mode"])
            ba, _ = _acc(d["runs"], d["baseline_mode"])
            agg[f"{exp}_{tier}"] = {"seg_acc": round(sa, 3), "base_acc": round(ba, 3),
                                    "delta": round(sa - ba, 3), "n": n}
            print(f"| {EXP_TITLE[exp]} | {tier} | {sa:.2f} | {ba:.2f} | {sa-ba:+.2f} | {n} |")
    figs = [fig_accuracy(data), fig_score_vs_correct(data)]
    for exp in EXPS:
        for tier in TIERS:
            figs.append(fig_case(data, exp, tier))
    (FIGS.parent / "results_pilot.json").write_text(json.dumps(agg, indent=2))
    print("\nfigures:")
    for f in figs:
        if f:
            print("  ", f.relative_to(REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
