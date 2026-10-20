#!/usr/bin/env python3
"""Aggregate the scale-v2.1 + expert-v2 twelve-run matrix into a leaderboard.

Reads Harbor trial results straight from the job roots and reports:

*   ``answer_score`` per family/route over the frozen 60-task denominator, and
    per domain, which is the only number that belongs on a leaderboard.
*   ``process_*`` signals in a separate table. They are diagnostics about how the
    agent worked and must never be folded into reward.
*   Completion accounting: trials that scored cleanly, trials whose agent or
    environment raised, and tasks with no trial at all. A degraded 0.0 from a
    dead agent is not a result, so it is counted as an error rather than a score.
*   The unscored expert-v2 diagnostic track as artifact counts only.

The denominator is the frozen selection, not whatever happened to run: a route
missing trials is reported as missing rather than silently averaged over fewer
tasks.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from scripts.harbor._citability import CITABLE_KEY, citability, headline_rows

ROUTES = ("cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12")
FAMILIES = ("codex", "claude-code")
DOMAINS = ("biology", "chemistry", "medical")
PROCESS_KEYS = (
    "process_opened_scene",
    "process_rendered",
    "process_tool_policy",
    "process_finished",
    "process_metadata_only",
    "process_compliance",
)


_FINAL_MARKER = "SCI3D_FINAL_JSON:"


def abstained(trial_dir: Path) -> bool:
    """True when the agent returned the answer template with every field null.

    Worth separating from a wrong answer. On the cc2d routes the medical tasks ask
    for voxel indices and RAS+ millimetres, which cannot be recovered from slice
    PNGs, and the agent submits all nulls rather than inventing numbers -- it does
    not even guess the binary laterality field. Both outcomes score 0, so a
    leaderboard that cannot tell them apart reports good calibration as failure.
    """
    for log in trial_dir.glob("agent/*.txt"):
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        index = text.rfind(_FINAL_MARKER)
        if index < 0:
            continue
        tail = text[index + len(_FINAL_MARKER):].lstrip()
        # The marker is embedded in a JSON-encoded log line, so quotes arrive escaped.
        tail = tail.replace('\\"', '"')
        end = tail.find("}")
        if end < 0:
            continue
        try:
            payload = json.loads(tail[: end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload and all(v is None for v in payload.values()):
            return True
    return False


def _gate_from_audit(trial_dir: Path) -> dict[str, Any]:
    """``process_gate`` recovered from ``verifier/sci3d_scores.json``.

    The gate has written its verdict into the audit since 2026-08-26, but only
    into the reward channel as of this change. A trial scored in between -- which
    is the whole window this report is about -- therefore has a verdict that is
    absent from ``rewards`` and present in the audit. Without this fallback such
    a trial reads as *never gated* rather than *blocked*, and the entire matrix
    loses its headline for a reason that is an artifact of where the verdict
    travelled rather than of what it says.
    """
    try:
        audit = json.loads((trial_dir / "verifier" / "sci3d_scores.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    gate = audit.get("process_gate")
    return gate if isinstance(gate, dict) else {}


def trial_results(job_root: Path):
    """Yield (task_id, exception|None, rewards, abstained, blockers) per trial."""
    for path in sorted(job_root.glob("*/*/result.json")):
        try:
            result = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        name = result.get("task_name") or ""
        if not name:
            continue
        task_id = name.split("/")[-1]
        exc = (result.get("exception_info") or {}).get("exception_type")
        rewards = dict((result.get("verifier_result") or {}).get("rewards") or {})
        gate = _gate_from_audit(path.parent)
        if CITABLE_KEY not in rewards:
            # Copy-on-write: the audit is a fallback for transport, not a second
            # opinion, and the caller's means must not see an extra key appear.
            verdict = gate.get("citable")
            if verdict is True:
                rewards[CITABLE_KEY] = 1.0
            elif verdict is False:
                rewards[CITABLE_KEY] = 0.0
        # Only inspect the log when the trial scored zero; that is the only case
        # where abstention and a wrong answer are indistinguishable from rewards.
        blank = False
        if exc is None and rewards.get("answer_score") == 0.0:
            blank = abstained(path.parent)
        blockers = gate.get("blockers")
        yield task_id, exc, rewards, blank, list(blockers or [])


def collect(jobs_root: Path, label: str) -> dict[str, dict[str, Any]]:
    """Best result per task across all attempts: a clean score wins over an error."""
    best: dict[str, dict[str, Any]] = {}
    for job_root in sorted(jobs_root.glob(f"{label}-attempt*")):
        for task_id, exc, rewards, blank, blockers in trial_results(job_root):
            clean = exc is None and "reward" in rewards
            prior = best.get(task_id)
            if prior is None or (clean and not prior["clean"]):
                best[task_id] = {
                    "clean": clean,
                    "exception": exc,
                    "rewards": rewards,
                    "abstained": blank,
                    "blockers": blockers,
                }
    return best


def summarize_route(
    clean: dict[str, dict[str, Any]],
    domain_of: dict[str, str],
    domains: tuple[str, ...],
) -> dict[str, Any]:
    """Aggregate one route's clean trials into the row the leaderboard prints.

    The headline numbers -- ``answer_score``, ``per_domain``, ``scored_clean``
    and ``process`` -- average *citable* trials only. This is the difference
    between reporting the gate and enforcing it: the EV2 matrix put ``cc`` at
    0.932 on medical off a route that read the staged ground-truth mask and
    never rendered an image, and a footnote cannot undo a number that has
    already been pasted into a report. Every excluded trial is disclosed through
    the ``*_all_eligible`` siblings and the ``citability`` census, so the size of
    the exclusion stays auditable instead of invisible.

    A trial the gate never scored is excluded too, and counted as *ungated*
    rather than blocked: the EV2 matrix predates the gate entirely, so its
    honest reading is "no citable trials", not "all trials failed".
    """
    kept, census = headline_rows(
        sorted(clean.items()), lambda kv: citability(kv[1].get("rewards"))
    )
    kept_map = dict(kept)

    def _scores(source: dict[str, dict[str, Any]]) -> dict[str, float]:
        return {
            task_id: trial["rewards"].get("answer_score", 0.0)
            for task_id, trial in source.items()
        }

    def _per_domain(scores: dict[str, float]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for domain in domains:
            vals = [s for t, s in scores.items() if domain_of.get(t) == domain]
            out[domain] = {
                "n": len(vals),
                "answer_score": round(mean(vals), 4) if vals else None,
            }
        return out

    scores = _scores(kept_map)
    scores_all = _scores(clean)
    return {
        # ``scored_clean`` is the denominator of ``answer_score`` and therefore
        # counts citable trials; the all-eligible count sits beside it.
        "scored_clean": len(kept_map),
        "scored_clean_all_eligible": len(clean),
        "answer_score": round(mean(scores.values()), 4) if scores else None,
        "answer_score_all_eligible": (
            round(mean(scores_all.values()), 4) if scores_all else None
        ),
        "abstained": sum(1 for v in kept_map.values() if v.get("abstained")),
        "abstained_by_domain": {
            domain: sum(
                1
                for t, v in kept_map.items()
                if v.get("abstained") and domain_of.get(t) == domain
            )
            for domain in domains
        },
        "per_domain": _per_domain(scores),
        "per_domain_all_eligible": _per_domain(scores_all),
        "citability": {**census.as_dict(), "note": census.note()},
        # Deliberately over the *all-eligible* set, not the headline set. These
        # are the diagnostics that explain a verdict: ``cc``'s
        # ``process_rendered = 0.00`` -- it never looked at an image -- is the
        # evidence that got it excluded, and filtering the diagnostics to the
        # surviving runs would delete the reason for the exclusion. The section
        # is headed "diagnostic, never part of reward"; it stays that.
        "process": {
            key: round(mean([v["rewards"].get(key, 0.0) for v in clean.values()]), 4)
            if clean
            else None
            for key in PROCESS_KEYS
        },
        # Names, not just counts: a reader who sees ``blocked 1`` needs to know
        # which task and which blocker without opening the audit.
        "blocked": [
            {
                "task_id": task_id,
                "blockers": sorted(trial.get("blockers") or []),
            }
            for task_id, trial in sorted(clean.items())
            if citability(trial.get("rewards")) is False
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-root", required=True, type=Path)
    ap.add_argument("--selection", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    selection = json.loads((args.selection / "SELECTION.json").read_text())
    records = [
        json.loads(line)
        for line in (args.selection / "scale_v21_sample.jsonl").read_text().splitlines()
        if line.strip()
    ]
    domain_of = {r["task_id"]: r.get("domain", "") for r in records}
    scored_ids = set(domain_of)
    diag_ids = {
        json.loads(line)["task_id"]
        for line in (args.selection / "expert_v2.jsonl").read_text().splitlines()
        if line.strip()
    }

    jobs_root = args.run_root / "jobs"
    rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        for route in ROUTES:
            best = collect(jobs_root, f"tasks-{family}-{route}")
            clean = {t: v for t, v in best.items() if v["clean"] and t in scored_ids}
            errored = sorted(t for t, v in best.items() if not v["clean"] and t in scored_ids)
            missing = sorted(scored_ids - set(best))
            rows.append(
                {
                    "family": family,
                    "route": route,
                    "denominator": len(scored_ids),
                    "errored": len(errored),
                    "missing": len(missing),
                    "errored_ids": errored,
                    "missing_ids": missing,
                    # Coverage, not citability: whether every scored task
                    # produced a clean trial at all.
                    "complete": len(clean) == len(scored_ids),
                    **summarize_route(clean, domain_of, DOMAINS),
                }
            )

    diag_rows = []
    for family in FAMILIES:
        for route in ROUTES:
            present = 0
            statuses: dict[str, int] = defaultdict(int)
            for job_root in sorted(jobs_root.glob(f"diag-{family}-{route}-attempt*")):
                for path in sorted(job_root.glob("*/*/verifier/diagnostic_completion.json")):
                    try:
                        payload = json.loads(path.read_text())
                    except (OSError, json.JSONDecodeError):
                        continue
                    present += 1
                    statuses[payload.get("status", "unknown")] += 1
            diag_rows.append(
                {
                    "family": family,
                    "route": route,
                    "denominator": len(diag_ids),
                    "artifacts": present,
                    "statuses": dict(sorted(statuses.items())),
                    "scored": False,
                }
            )

    # The caveat about the filter is computed, not asserted. Whether a matrix has
    # verdicts at all depends on when its trials were scored -- the gate reached
    # the audit on 2026-08-26 and the reward channel later still -- so a
    # hardcoded claim would be wrong for half the archives it is printed on.
    census = {
        key: sum(row["citability"][key] for row in rows)
        for key in ("n_eligible", "n_citable", "n_blocked", "n_ungated")
    }
    if census["n_eligible"] and census["n_citable"] == census["n_eligible"]:
        citability_caveat = (
            f"Every one of the {census['n_eligible']} clean trials across the "
            "matrix is citable, so answer_score and 'all eligible' agree "
            "throughout and the filter removed nothing."
        )
    elif census["n_citable"] == 0:
        citability_caveat = (
            f"No trial in this matrix carries a process-gate verdict "
            f"({census['n_ungated']} ungated, {census['n_blocked']} blocked out "
            f"of {census['n_eligible']}), so every route shows '-' in the "
            "headline and the unfiltered mean survives in the 'all eligible' "
            "column for reference. That is the honest reading of a run whose "
            "trajectories were never scanned for a forbidden capability -- not a "
            "claim that the routes failed."
        )
    else:
        citability_caveat = (
            f"{census['n_citable']} of {census['n_eligible']} clean trials are "
            f"citable; blocked {census['n_blocked']}, ungated "
            f"{census['n_ungated']}. answer_score averages the citable subset "
            "only, 'all eligible' is the same mean over every clean trial, and "
            "the per-route census table below names what was dropped and why."
        )

    report = {
        "schema_version": "sciencevision.v21_ev2_leaderboard.v1",
        "selection_id_sha256": selection["id_sha256"],
        "scored": {"denominator": len(scored_ids), "rows": rows, "citability": census},
        "diagnostic": {
            "denominator": len(diag_ids),
            "scored": False,
            "reason": "expert-v2 ground truth is needs_expert_annotation; no reward exists",
            "rows": diag_rows,
        },
        "caveats": [
            citability_caveat,
            "Biology and chemistry scale-v2.1 candidates are QC 'review', with open "
            "leakage findings (asset_identifier_shortcut, package_compute_shortcut, "
            "shortcut_control_unenforced). A raw-asset route may reach those answers "
            "by computing over coordinates or recognizing an identifier rather than "
            "by perceiving the structure. All 20 medical tasks are QC 'pass'.",
            "The cc2d family passed technical render gates only; no blind reviewer "
            "has confirmed answer visibility. Report as cc2d-all, never cc2d-visible.",
            "xhigh effort means model_reasoning_effort=xhigh for Codex and --effort "
            "xhigh for Claude Code: comparable intent, not identical semantics.",
            "A trial whose agent raised is counted as errored, not as a 0.0 score: "
            "the verifier still scores such trials degraded, and averaging them in "
            "would report infrastructure failure as model capability.",
        ],
    }

    lines = ["# scale-v2.1 + expert-v2 twelve-run matrix", ""]
    lines.append(f"Scored denominator: {len(scored_ids)} tasks (20 per domain).")
    lines.append("")
    lines.append(
        "| family | route | answer_score | all eligible | clean | abstained "
        "| errored | missing | biology | chemistry | medical |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        def fmt(value):
            return "-" if value is None else f"{value:.3f}"
        lines.append(
            f"| {row['family']} | {row['route']} | {fmt(row['answer_score'])} | "
            f"{fmt(row['answer_score_all_eligible'])} | "
            f"{row['scored_clean']}/{row['denominator']} | {row['abstained']} | "
            f"{row['errored']} | {row['missing']} | "
            + " | ".join(fmt(row["per_domain"][d]["answer_score"]) for d in DOMAINS)
            + " |"
        )
    lines += [
        "",
        "`answer_score` averages **citable trials only**; `all eligible` is the "
        "same mean over every clean trial and is kept beside it so the size of "
        "the exclusion is auditable. A route showing `-` in the first column has "
        "no citable trial at all -- which is the answer to \"may I cite this?\", "
        "not a missing measurement. The per-domain columns are filtered the same "
        "way.",
        "",
        "`abstained` counts trials that returned the answer template with every "
        "field null. Those score 0 like a wrong answer, but they are a different "
        "behaviour: the agent declined rather than invented. Read a route's 0.000 "
        "with its abstention count beside it.",
        "",
        "| family | route | citable | eligible | blocked | never gated |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        cit = row["citability"]
        lines.append(
            f"| {row['family']} | {row['route']} | {cit['n_citable']} | "
            f"{cit['n_eligible']} | {cit['n_blocked']} | {cit['n_ungated']} |"
        )
    lines += [
        "",
        "*blocked* fired a process-gate blocker (a forbidden capability in the "
        "agent's own command, or a forbidden tool call). *never gated* means the "
        "trial carries no verdict -- a run produced before the gate existed. Both "
        "are excluded from the headline, and they are counted apart because "
        "\"we looked and it failed\" and \"we did not look\" are different facts.",
    ]
    blocked_lines = [
        f"| {row['family']} | {row['route']} | {entry['task_id']} | "
        f"{', '.join(entry['blockers']) or '-'} |"
        for row in rows
        for entry in row["blocked"]
    ]
    if blocked_lines:
        lines += [
            "",
            "### Blocked trials",
            "",
            "| family | route | task | blocker |",
            "|---|---|---|---|",
            *blocked_lines,
        ]
    lines += ["", "## Process signals (diagnostic, never part of reward)", ""]
    lines.append("| family | route | " + " | ".join(k.replace("process_", "") for k in PROCESS_KEYS) + " |")
    lines.append("|---" * (2 + len(PROCESS_KEYS)) + "|")
    for row in rows:
        lines.append(
            f"| {row['family']} | {row['route']} | "
            + " | ".join(
                "-" if row["process"][k] is None else f"{row['process'][k]:.2f}" for k in PROCESS_KEYS
            )
            + " |"
        )
    lines += ["", f"## expert-v2 diagnostic track (unscored, {len(diag_ids)} tasks)", ""]
    lines.append("| family | route | artifacts | statuses |")
    lines.append("|---|---|---|---|")
    for row in diag_rows:
        lines.append(
            f"| {row['family']} | {row['route']} | {row['artifacts']}/{row['denominator']} | "
            f"{json.dumps(row['statuses'])} |"
        )
    lines += ["", "## Caveats", ""] + [f"- {c}" for c in report["caveats"]]

    out = args.out or (args.run_root / "reports")
    out.mkdir(parents=True, exist_ok=True)
    (out / "LEADERBOARD.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (out / "LEADERBOARD.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
