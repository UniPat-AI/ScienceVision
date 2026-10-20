"""ev2 pilot — failure-case compilation + per-baseline failure-mode summary.

Reuses the shared trial-scanning / score-loading / trajectory-parsing helpers
from scripts.harbor.build_ev2_viewer_bundle so there is exactly one notion of
"the deployed (tiered) score per (task, baseline)" and one status classifier.

A cell (task, baseline) is a FAILURE when status ∈ {errored, refused, degraded,
wrong}. status is computed by build_ev2_viewer_bundle.classify_status:
  refused  — final answer hits a Usage-Policy / model-refusal marker
  errored  — trial carries an exception (and did not refuse)
  degraded — judge degraded or empty final answer
  wrong    — llm_judge < 0.5
  ok       — otherwise

Emits docs/harbor/EV2_FAILURE_CASES.md.

Run with the harbor venv python (3.12):
    ~/.venv-harbor/bin/python scripts/harbor/failure_report_ev2.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.harbor.build_ev2_viewer_bundle import (
    BASELINES,
    BASELINE_LABELS,
    ROOT,
    baseline_block,
    collect_trials,
    load_task_meta,
    parse_trajectory,
)

OUT = ROOT / "docs" / "harbor" / "EV2_FAILURE_CASES.md"
FAIL_STATUSES = {"errored", "refused", "degraded", "wrong"}


def domain_of(task_id: str) -> str:
    seg = task_id.split("-")[1] if "-" in task_id else ""
    return {"biol": "biology", "chem": "chemistry", "medi": "medical"}.get(seg, seg)


def _snip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def gather() -> dict[str, dict[str, dict]]:
    """baseline -> task_id -> {block fields + exception_type + task_id}."""
    out: dict[str, dict[str, dict]] = {}
    for b in BASELINES:
        recs = collect_trials(b)
        per: dict[str, dict] = {}
        for tid, rec in recs.items():
            blk = baseline_block(rec)
            blk["task_id"] = tid
            blk["exception_type"] = rec.get("exception_type")
            per[tid] = blk
        out[b] = per
    return out


# ── per-baseline failure-mode prose (grounded, curated) ─────────────────── #
_MODE_NOTES = {
    "cc": [
        "**No 3D libraries → NIfTI is opaque.** The plain image ships the raw "
        "`.nii.gz` but no medical-imaging stack; the agent cannot even `file` it "
        "reliably (exit 127) and falls back to answering from anatomical priors "
        "rather than the voxels — weak on `sv-medi-*` (mri-arbitrary-plane, "
        "lesion-extent).",
        "**Atom-index mislabeling on molecules.** It parses `.mol` coordinates by "
        "hand and computes the CIP triple product correctly, but labels the "
        "stereocenter by file atom index (`C7`) instead of the ground-truth "
        "chemical numbering (`C2`) — method right, label wrong → `llm_judge = 0` "
        "(sv-chem-chirality-07).",
        "**\"Asset not staged\" false-negative on the docking tasks.** On "
        "sv-biol-docking-pose-02 the agent searches the sandbox, concludes the "
        "pose files are not present, and declines rather than reasoning from the "
        "staged asset — a self-inflicted miss the judge scores 0 even though the "
        "ground truth is answerable.",
        "**Structural zero on the closed-loop process signals.** With no "
        "`sv3d_open_scene` / render tools, `process_opened_scene` and "
        "`process_rendered` are 0 by construction regardless of answer quality.",
        "**Setup-timeout / non-zero-exit errors.** The in-container "
        "`npm install -g @anthropic-ai/claude-code` bootstrap hit the 360 s "
        "`AgentSetupTimeout` in the original run; re-running with a 3× multiplier "
        "recovered the trials.",
    ],
    "cc2d": [
        "**Molecular / point-cloud views are missing → blind on chemistry & "
        "biology.** The pure-vision image stages only offline-rendered PNGs, and "
        "the molecular/point-cloud renderer is deferred (`views_missing`), so the "
        "`views/` dir is empty for most `sv-chem-*` / `sv-biol-*` tasks. The agent "
        "sees nothing and emits a generic non-answer ('paste the molecule and I "
        "will assign…') → the 0.100 process floor.",
        "**Competitive only where slices actually render.** Medical NIfTI slices "
        "do render, which is the one domain where `cc2d` has real evidence and "
        "even wins a task (mri-arbitrary-plane-02). Its low chemistry/biology "
        "scores are a rendering-pipeline artifact, not intrinsic vision weakness.",
    ],
    "ccmcp": [
        "**Strongest overall, but not immune on hard tasks.** It still fails the "
        "hard biology pair (docking-pose-02, molecular-collision-04) that is hard "
        "for every baseline, and the medical lesion-extent task.",
        "**Also declines docking-pose-02 as \"data unavailable.\"** Like `cc`, it "
        "reports the pose files are missing from the sandbox and refuses the "
        "comparison, despite the ground truth documenting the specific "
        "native-vs-decoy poses — the MCP tools do not rescue a task the agent "
        "believes has no asset.",
        "**Judge variance costs it, too.** On chirality-07 the near-identical "
        "'C7 is R' answer was scored 0.60 here but 0.0 for `cc` — single-sample "
        "judging is noisy in both directions.",
        "**Usage-Policy refusal.** optimization-vector-06 (ligand substitution "
        "vectors into an acetylcholinesterase subpocket) trips a false-positive "
        "refusal on all three baselines, `ccmcp` included → no answer → floor.",
    ],
}

_CROSS_CUTTING = [
    "**Judge-503 degrade (fixed).** The verifier's LLM judge defaulted to the "
    "*dated* id `claude-haiku-4-5-20251001`, which the ALB gateway 503s "
    "(channel pricing restriction). When it 503s, both `llm_judge` and "
    "`process_judge` degrade to 0 and reward collapses to the 0.100 process "
    "floor — a scoring outage masquerading as a result. Fixed by offline-"
    "rescoring with the undated `claude-haiku-4-5`; all 45 deployed cells are "
    "non-degraded. See `docs/harbor/EV2_THREEWAY_REPORT.md` §5.",
    "**Single-judge, single-sample variance.** Absolute `llm_judge` values "
    "carry real noise (the chirality-07 0.0-vs-0.60 split on identical "
    "conclusions). Multi-sample / multi-judge scoring would tighten the "
    "per-task numbers.",
    "**Ground truth is provisional.** Every `task.toml` GT is marked "
    "`status = needs_expert_annotation`. A 'wrong' verdict here means "
    "'disagreed with the provisional expert reference under one judge', not a "
    "certified error — treat the failure set as *relative* signal between "
    "baselines, pending expert sign-off.",
    "**The 30% process weight structurally favors the tool-equipped baseline.** "
    "`process_opened_scene` / `process_rendered` can only fire for `ccmcp`, so "
    "part of every `cc` / `cc2d` reward gap is definitional, not answer quality.",
]


def main() -> int:
    data = gather()
    task_ids = sorted({t for b in BASELINES for t in data[b]})

    # failure cells
    fails = []
    for b in BASELINES:
        for tid, blk in data[b].items():
            if blk["status"] in FAIL_STATUSES:
                fails.append((b, tid, blk))

    L: list[str] = []
    L.append("# ev2 Pilot — Failure Cases & Per-Baseline Failure Modes\n")
    L.append("**Scope:** 15 3D-grounded QA tasks × 3 harbor baselines "
             "(`cc` / `cc2d` / `ccmcp`) = 45 deployed cells. Scores are the "
             "tiered best per cell (rerun non-degraded > original rescored > "
             "degraded), identical to the leaderboard in "
             "`docs/harbor/EV2_THREEWAY_REPORT.md`.\n")
    L.append("**Companion:** per-question trajectories are visualized live in the "
             "viewer `#qagen` → **EV2_Pilot** set (bundle "
             "`viewer/verify_ev2_pilot.json`).\n")

    # failure-rate overview
    L.append("## 0. Failure overview\n")
    L.append("| baseline | input to agent | failures / 15 | breakdown |")
    L.append("|---|---|--:|---|")
    for b in BASELINES:
        cnt = Counter(blk["status"] for blk in data[b].values())
        n_fail = sum(cnt[s] for s in FAIL_STATUSES)
        bd = " ".join(f"{s}:{cnt[s]}" for s in ("wrong", "refused", "errored", "degraded") if cnt[s])
        L.append(f"| `{b}` | {_snip(BASELINE_LABELS[b], 60)} | **{n_fail}** | {bd or '—'} |")
    L.append("")

    # §1 full failure table
    L.append("## 1. All failure cases\n")
    L.append("Grouped by task; one row per failing baseline. Rationale is the "
             "verbatim judge rationale (llm_judge) where present.\n")
    L.append("| task (domain) | baseline | status | reward | llm_judge | judge rationale (verbatim) | answer snippet | tier |")
    L.append("|---|---|---|--:|--:|---|---|---|")
    for tid in task_ids:
        dom = domain_of(tid)
        for b in BASELINES:
            blk = data[b].get(tid)
            if not blk or blk["status"] not in FAIL_STATUSES:
                continue
            rat = _snip(blk["llm_rationale"], 160) or "—"
            ans = _snip(blk["final_answer"], 90) or "—"
            L.append(f"| {tid} ({dom}) | `{b}` | **{blk['status']}** | "
                     f"{blk['reward']:.3f} | {blk['llm_judge']:.2f} | {rat} | {ans} | {blk['source_tier']} |")
    L.append("")

    # §2 per-baseline failure modes
    L.append("## 2. Per-baseline failure modes\n")
    for b in BASELINES:
        cnt = Counter(blk["status"] for blk in data[b].values())
        n_fail = sum(cnt[s] for s in FAIL_STATUSES)
        # tool usage across this baseline's failing cells
        tool_ctr: Counter = Counter()
        for tid, blk in data[b].items():
            if blk["status"] in FAIL_STATUSES:
                tool_ctr.update(blk["tools"])
        top_tools = ", ".join(f"`{t}`×{c}" for t, c in tool_ctr.most_common(6)) or "—"
        L.append(f"### `{b}` — {BASELINE_LABELS[b]}\n")
        L.append(f"*{n_fail}/15 failed. Tools seen across failing trajectories: {top_tools}.*\n")
        for note in _MODE_NOTES[b]:
            L.append(f"- {note}")
        L.append("")

    # §3 cross-cutting
    L.append("## 3. Cross-cutting issues\n")
    for note in _CROSS_CUTTING:
        L.append(f"- {note}")
    L.append("")

    L.append("---\n")
    L.append("*Generated by `scripts/harbor/failure_report_ev2.py` (imports the "
             "shared helpers in `scripts/harbor/build_ev2_viewer_bundle.py`).*")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(fails)} failure cells)")
    for b in BASELINES:
        cnt = Counter(blk["status"] for blk in data[b].values())
        print(f"  {b:5} " + "  ".join(f"{s}={cnt[s]}" for s in ("ok", "wrong", "refused", "errored", "degraded")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
