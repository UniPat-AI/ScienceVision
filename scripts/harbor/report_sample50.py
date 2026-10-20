"""Build the sample50 eval report from the per-baseline harbor job dirs.

Reads every finished trial under ``jobs-<set>/job-<baseline>/*/<trial>/`` and
emits a markdown report plus its machine-readable twin:

  * coverage — completed / expected trials per baseline, so a partial run is
    never mistaken for a finished one
  * mean reward per baseline x domain, and overall
  * deterministic answer + process compliance per baseline; optional judges are shadow columns
  * reliability — exception types, agent-timeout rate, and how many trials left
    no parseable final answer
  * a case index — the highest and lowest scoring trials per baseline, with the
    on-disk paths to their trajectory and score files

Timed-out trials are INCLUDED in the means: hitting the agent timeout is a real
outcome for that baseline, and dropping those trials would flatter whichever
baseline times out most. The timeout rate is reported alongside so the reader can
see how much of a baseline's mean is timeout floor.

Usage:
  python -m scripts.harbor.report_sample50 --jobs-root jobs-sample50 \
    --expected 130 --out docs/harbor/SAMPLE50_REPORT.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

BASELINES = ["cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12", "host"]
DOMAINS = ["medical", "biology", "chemistry"]
COMPONENTS = [
    "answer_score",
    "process_compliance",
    "shadow_llm_judge",
    "shadow_process_judge",
]


def _domain_of(task_id: str) -> str:
    for prefix, domain in (("sv-medi", "medical"), ("sv-biol", "biology"), ("sv-chem", "chemistry")):
        if task_id.startswith(prefix):
            return domain
    return "?"


def load_trials(job_root: Path) -> list[dict]:
    """One flat record per finished trial across all timestamped runs."""
    trials: dict[str, dict] = {}
    for result_path in sorted(job_root.glob("*/*/result.json")):
        try:
            data = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("task_name") or ""
        task_id = name.split("/", 1)[1] if "/" in name else name
        if not task_id:
            continue
        trial_dir = result_path.parent
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
        exc = data.get("exception_info") or {}

        # A scorer's `degraded` flag means the agent left no final answer
        # (_scorer_registry: `degraded = traj.final_answer is None`), not that
        # the judge itself failed. Named accordingly so it is not misread as the
        # judge-degradation failure mode that silently ties rewards at ~0.100.
        no_answer = False
        # Formal reports fail closed: old/malformed trials without an explicit
        # deterministic eligibility audit cannot enter the denominator.
        evaluation_eligible = False
        # Tri-state, same rule as the leaderboards: True counts, False is a
        # blocker, None means the gate never scored this trial. Absent must not
        # become False -- it is read as "not certified" either way, but the
        # report distinguishes them so a blocked run is not reported as an
        # unexamined one.
        citable: bool | None = None
        scores_path = trial_dir / "verifier" / "sci3d_scores.json"
        if scores_path.exists():
            try:
                scores = json.loads(scores_path.read_text())
                evaluation_eligible = scores.get("evaluation_eligible") is True
                answer_audit = scores.get("answer_score") or {}
                no_answer = bool(answer_audit.get("raw", {}).get("parse_error") == "missing_answer")
                gate = scores.get("process_gate")
                if isinstance(gate, dict) and isinstance(gate.get("citable"), bool):
                    citable = gate["citable"]
            except (OSError, json.JSONDecodeError):
                pass

        rec = {
            "task_id": task_id,
            "domain": _domain_of(task_id),
            "trial_dir": str(trial_dir),
            "reward": rewards.get("reward"),
            "evaluation_eligible": evaluation_eligible,
            "citable": citable,
            "components": {c: rewards[c] for c in COMPONENTS if c in rewards},
            "exception_type": exc.get("exception_type") or "",
            "no_final_answer": no_answer,
        }
        # A resumed run can hold two trials for one task (e.g. a kill mid-flight
        # left a partial). Keep the one that actually scored.
        prev = trials.get(task_id)
        if prev is None or (prev["reward"] is None and rec["reward"] is not None):
            trials[task_id] = rec
    return sorted(trials.values(), key=lambda r: r["task_id"])


def _headline(trials: list[dict]) -> list[dict]:
    """The set the reported means rest on: eligible *and* certified.

    Excluding uncertified trials is the difference between reporting the process
    gate and enforcing it. ``citable is None`` -- a trial scored before the gate
    existed -- is excluded too, but the coverage table counts it apart from a
    blocker so the two are never merged into one number.
    """
    return [t for t in trials if t["evaluation_eligible"] and t["citable"] is True]


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def _fmt(x: float | None, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def build_report(jobs_root: Path, expected: int) -> tuple[str, dict]:
    per_baseline: dict[str, list[dict]] = {}
    for label in BASELINES:
        job_root = jobs_root / f"job-{label}"
        if job_root.is_dir():
            trials = load_trials(job_root)
            if trials:
                per_baseline[label] = trials
    if not per_baseline:
        raise SystemExit(f"no finished trials under {jobs_root}/job-*")

    data: dict = {"jobs_root": str(jobs_root), "expected_per_baseline": expected, "baselines": {}}
    lines: list[str] = ["# scale-v1 sample50 — baseline report", ""]

    # ── coverage ───────────────────────────────────────────────────────── #
    lines += ["## Coverage", "",
              "| baseline | scored eligible | expected eligible | excluded ineligible "
              "| citable | blocked | never gated | complete |",
              "|---|---|---|---|---|---|---|---|"]
    for label, trials in per_baseline.items():
        eligible = [t for t in trials if t["evaluation_eligible"]]
        scored = sum(1 for t in eligible if t["reward"] is not None)
        excluded = len(trials) - len(eligible)
        blocked = sum(1 for t in eligible if t["citable"] is False)
        ungated = sum(1 for t in eligible if t["citable"] is None)
        citable = sum(1 for t in eligible if t["citable"] is True)
        lines.append(f"| `{label}` | {scored} | {expected} | {excluded} | "
                     f"{citable} | {blocked} | {ungated} | "
                     f"{'yes' if scored >= expected else 'NO — partial'} |")
        data["baselines"][label] = {
            "scored": scored, "expected": expected, "ineligible_excluded": excluded,
            "citable": citable, "blocked": blocked, "ungated": ungated,
        }
    lines += [
        "",
        "*citable* / *blocked* / *never gated* split the eligible trials by the"
        " process gate's verdict. The means below rest on the citable set only:"
        " a run with positive evidence of a forbidden capability is not a data"
        " point about perception, and a run the gate never scored is not one"
        " either. *blocked* and *never gated* are counted apart because \"we"
        " looked and it failed\" and \"we did not look\" are different facts.",
        "",
    ]

    # ── reward by domain ───────────────────────────────────────────────── #
    lines += ["## Mean reward by domain", "",
              "Timed-out trials included (see reliability below).", "",
              "| baseline | " + " | ".join(DOMAINS) + " | overall |",
              "|---|" + "---|" * (len(DOMAINS) + 1)]
    for label, trials in per_baseline.items():
        trials = _headline(trials)
        row = []
        for domain in DOMAINS:
            row.append(_mean([t["reward"] for t in trials if t["domain"] == domain]))
        overall = _mean([t["reward"] for t in trials])
        lines.append(f"| `{label}` | " + " | ".join(_fmt(v) for v in row) + f" | **{_fmt(overall)}** |")
        data["baselines"][label]["by_domain"] = dict(zip(DOMAINS, row))
        data["baselines"][label]["overall"] = overall
    lines.append("")

    # ── components ─────────────────────────────────────────────────────── #
    lines += ["## Score components (overall)", "",
              "| baseline | " + " | ".join(f"`{c}`" for c in COMPONENTS) + " | `reward` |",
              "|---|" + "---|" * (len(COMPONENTS) + 1)]
    for label, trials in per_baseline.items():
        trials = _headline(trials)
        cells = []
        comps = {}
        for comp in COMPONENTS:
            vals = [t["components"].get(comp) for t in trials if comp in t["components"]]
            comps[comp] = _mean(vals)
            n = len([v for v in vals if v is not None])
            cells.append(f"{_fmt(comps[comp])} <sub>n={n}</sub>" if n else "—")
        lines.append(f"| `{label}` | " + " | ".join(cells)
                     + f" | **{_fmt(_mean([t['reward'] for t in trials]))}** |")
        data["baselines"][label]["components"] = comps
    lines.append("")

    # ── reliability ────────────────────────────────────────────────────── #
    lines += ["## Reliability", "",
              "| baseline | trials | agent timeout | other exception | no final answer |",
              "|---|---|---|---|---|"]
    for label, trials in per_baseline.items():
        n = len(trials)
        timeouts = sum(1 for t in trials if t["exception_type"] == "AgentTimeoutError")
        others = sorted({t["exception_type"] for t in trials
                         if t["exception_type"] and t["exception_type"] != "AgentTimeoutError"})
        n_other = sum(1 for t in trials if t["exception_type"] and t["exception_type"] != "AgentTimeoutError")
        no_answer_n = sum(1 for t in trials if t["no_final_answer"])
        lines.append(f"| `{label}` | {n} | {timeouts} ({100*timeouts/n:.0f}%) | "
                     f"{n_other}{' ' + ', '.join(others) if others else ''} | {no_answer_n} |")
        data["baselines"][label]["reliability"] = {
            "trials": n, "agent_timeout": timeouts,
            "other_exceptions": n_other, "other_exception_types": others,
            "no_final_answer": no_answer_n,
        }
    lines.append("")

    # ── case index ─────────────────────────────────────────────────────── #
    lines += ["## Case index", "",
              "Highest / lowest scoring citable trials per baseline -- a shortcut that "
              "scored well is not the exemplar a reader should study. `trajectory.json` "
              "is the step-by-step tool log; `sci3d_scores.json` holds item verdicts and "
              "diagnostics.", ""]
    for label, trials in per_baseline.items():
        scored = sorted(
            (t for t in _headline(trials) if t["reward"] is not None),
            key=lambda t: t["reward"],
        )
        if not scored:
            continue
        lines.append(f"### `{label}`")
        lines.append("")
        for tag, cases in (("best", list(reversed(scored[-3:]))), ("worst", scored[:3])):
            for t in cases:
                lines.append(f"- **{tag}** `{t['task_id']}` reward={t['reward']:.3f}"
                             + (f" _({t['exception_type']})_" if t["exception_type"] else ""))
                lines.append(f"  - `{t['trial_dir']}/agent/trajectory.json`")
                lines.append(f"  - `{t['trial_dir']}/verifier/sci3d_scores.json`")
        lines.append("")
        data["baselines"][label]["cases"] = {
            "best": [{"task_id": t["task_id"], "reward": t["reward"], "trial_dir": t["trial_dir"]}
                     for t in reversed(scored[-3:])],
            "worst": [{"task_id": t["task_id"], "reward": t["reward"], "trial_dir": t["trial_dir"]}
                      for t in scored[:3]],
        }

    data["trials"] = {label: trials for label, trials in per_baseline.items()}
    return "\n".join(lines) + "\n", data


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jobs-root", type=Path, default=Path("jobs-sample50"))
    ap.add_argument("--expected", type=int, default=130, help="eligible trials expected per baseline")
    ap.add_argument("--out", type=Path, default=Path("docs/harbor/SAMPLE50_REPORT.md"))
    args = ap.parse_args(argv)

    md, data = build_report(args.jobs_root, args.expected)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md)
    json_out = args.out.with_suffix(".json")
    json_out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(md)
    print(f"wrote {args.out}\nwrote {json_out}")


if __name__ == "__main__":
    main()
