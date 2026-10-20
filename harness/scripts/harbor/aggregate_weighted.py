"""Weighted cross-task aggregation of Harbor sci3d verify results.

Harbor's built-in metric layer (mean/sum/min/max) aggregates each reward key
across tasks with *equal* per-task weight and has no access to task metadata —
so it cannot, e.g., give every domain equal voice when domains have unequal
task counts (robotics 6 vs biology 2), or weight V-classes by difficulty.

This is a post-processing pass that runs *after* a Harbor job: it reads the
per-trial ``result.json`` files (or a single results array), recovers
``domain`` and ``v_class`` from the task name (``sv-<domain>-<vclass>-NNNN``),
and aggregates the ``reward`` (and any other numeric key) with configurable
weighting.

Weighting modes (``--mode``):
  flat            plain mean over all trials (matches Harbor's Mean; sanity ref)
  domain_equal    mean within each domain, then mean across domains (default) —
                  cancels domain task-count imbalance
  weighted        domain weights and/or v_class weights from a JSON config

Config JSON (``--weights weights.json``), all keys optional:
  {
    "domain":  {"robotics": 1.0, "biology": 2.0, ...},   # per-domain multiplier
    "v_class": {"V1": 1.0, "V5": 1.5, ...},              # per-v_class multiplier
    "default": 1.0
  }
Effective per-trial weight = domain_w * v_class_w. Unlisted keys use "default".

Usage:
    python -m scripts.harbor.aggregate_weighted \
        --results <dir-of-result.json | results.json> \
        [--mode domain_equal] [--weights weights.json] \
        [--key reward] [--out summary.json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from scripts.harbor._citability import CITABLE_KEY, rows_by_rewards
from scripts.harbor.security_contract import (
    SecurityContractError,
    validate_security_attestation,
)

# Best-effort name parsing, used only as a fallback when a trial carries no
# domain/v_class metadata. Two families:
#   sv-<domain>-<class>-<id>   class in {v\d, k\d}, id numeric
#       e.g. sv-biology-v1-0001, sv-medical-k5-0008
#   <prefix>-<class>-<slug>    class in {p\d, v\d}, slug free text (Genesis)
#       e.g. genesis-p2-cut-dragon-fragments  (domain NOT in the name)
_SV_NAME_RE = re.compile(r"sv-(?P<domain>.+)-(?P<cls>[vk]\d)-\d+$", re.IGNORECASE)
_GENESIS_NAME_RE = re.compile(r"^genesis-(?P<cls>[pv]\d)-", re.IGNORECASE)


def parse_task_name(task_name: str) -> tuple[str, str]:
    """Recover (domain, v_class) from a task name; ('', '') if unparseable.

    Prefer ``metadata`` (see ``load_results``); this name-based parse is the
    fallback. The Genesis names encode no domain, so we tag them ``physics``
    (the only Genesis domain in this benchmark).
    """
    leaf = task_name.split("/")[-1]
    m = _SV_NAME_RE.match(leaf)
    if m:
        return m.group("domain").lower(), m.group("cls").upper()
    g = _GENESIS_NAME_RE.match(leaf)
    if g:
        return "physics", g.group("cls").upper()
    return "", ""


def domain_v_class(trial: dict) -> tuple[str, str]:
    """Domain + v_class for a trial: metadata first, then name parsing.

    ``trial`` may carry an explicit ``metadata`` dict (projected by
    ``load_results`` from the task's ``[metadata]`` table). When present it is
    authoritative; otherwise fall back to parsing the task name.
    """
    meta = trial.get("metadata") or {}
    domain = str(meta.get("domain", "")).lower()
    v_class = str(meta.get("v_class", "")).upper()
    if domain and v_class:
        return domain, v_class
    pd, pv = parse_task_name(trial.get("task_name", ""))
    return (domain or pd), (v_class or pv)


def load_results(path: Path) -> list[dict]:
    """Load trial results from a results.json array or a directory tree.

    Each returned dict is ``{"task_name": str, "rewards": dict}``.
    """
    p = Path(path)
    raw: list[dict] = []
    if p.is_file():
        data = json.loads(p.read_text())
        raw = data if isinstance(data, list) else data.get("results", [data])
    else:
        for rf in sorted(p.rglob("result.json")):
            try:
                result = json.loads(rf.read_text())
                if isinstance(result, dict):
                    # Kept private to this loader: the formal scorer audit is
                    # a sibling of result.json, not a Harbor reward channel.
                    result["_sciencevision_result_path"] = str(rf)
                raw.append(result)
            except json.JSONDecodeError:
                continue

    out: list[dict] = []
    for r in raw:
        name = r.get("task_name") or r.get("trial_name") or ""
        vr = r.get("verifier_result") or {}
        rewards = vr.get("rewards") if isinstance(vr, dict) else None
        if rewards is None:
            rewards = r.get("rewards")
        # Keep a failed/unscored result as an empty reward row.  Dropping it
        # here would make the formal aggregation gate unable to distinguish a
        # fully certified run from one whose failures silently vanished before
        # its citability census was computed.
        score_audit = _trial_score_audit(r)
        out.append(
            {
                "task_name": name,
                "rewards": rewards if isinstance(rewards, dict) else {},
                "metadata": _trial_metadata(r),
                "evaluation_eligible": score_audit.get("evaluation_eligible"),
                "aggregate_excluded": score_audit.get("aggregate_excluded") is True,
            }
        )
    return out


def _trial_score_audit(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the scorer's exclusion verdict when a Harbor result has one.

    An infra-invalid route cannot be identified from task.toml alone: its
    metadata may be otherwise eligible. The verifier audit is the authoritative
    same-trial receipt for that distinction.
    """
    inline = result.get("sci3d_scores") or result.get("score_audit")
    if isinstance(inline, Mapping):
        return inline
    path = result.get("_sciencevision_result_path")
    if not path:
        return {}
    audit_path = Path(str(path)).parent / "verifier" / "sci3d_scores.json"
    try:
        audit = json.loads(audit_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return audit if isinstance(audit, Mapping) else {}


def _trial_metadata(result: dict) -> dict:
    """Best-effort domain/v_class for a trial.

    A Harbor result.json points at the task dir via ``config.task.path`` but
    does not inline its [metadata]; read that task.toml when available. An
    inline ``metadata`` dict (e.g. from a hand-built results array) wins.
    """
    inline = result.get("metadata")
    if isinstance(inline, dict) and inline:
        return inline
    try:
        cfg = result.get("config") or {}
        task_path = (cfg.get("task") or {}).get("path")
        if not task_path:
            return {}
        toml_path = Path(task_path) / "task.toml"
        if not toml_path.exists():
            return {}
        import tomllib

        return tomllib.loads(toml_path.read_text()).get("metadata", {}) or {}
    except Exception:
        return {}


def _numeric_keys(trials: list[dict]) -> list[str]:
    """Every numeric reward key except the citability verdict.

    ``process_citable`` is a flag that selects the aggregation set, not a
    measurement to average -- once the filter has run it is constant 1.0, so
    reporting its mean adds a row that says nothing.
    """
    keys: set[str] = set()
    for t in trials:
        for k, v in t["rewards"].items():
            if k == CITABLE_KEY:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                keys.add(k)
    return sorted(keys)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _trial_task_names(trials: list[dict]) -> list[str]:
    """Return task leaves in the result set, rejecting unscoped rows.

    Harbor often prefixes task names with a suite path; an attestation records
    the generated task directory leaf.  The security validator normalizes that
    same convention, while an empty name remains a hard error because no
    receipt can prove which task produced it.
    """
    names: list[str] = []
    for index, trial in enumerate(trials):
        raw = str(trial.get("task_name") or "").strip().rstrip("/")
        leaf = Path(raw).name if raw else ""
        if leaf in {"", ".", ".."}:
            raise SecurityContractError(f"result row {index} has no scoped task_name")
        names.append(leaf)
    return names


def _formally_eligible(trial: Mapping[str, Any]) -> bool:
    """Whether a row may enter a formal benchmark denominator.

    A missing marker is preserved for historical result files.  An explicit
    ``false`` is different: the builder has established that this route cannot
    receive a required private input (for example a segmentation mask), so a
    high or low answer is not comparable and must not become a leaderboard
    datum merely because its process gate happened to be clean.
    """
    if trial.get("aggregate_excluded") is True:
        return False
    if trial.get("evaluation_eligible") is False:
        return False
    metadata = trial.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("evaluation_eligible") is False:
        return False
    return trial.get("evaluation_eligible") is not False


def _formal_trials(trials: list[dict]) -> tuple[list[dict], int]:
    kept = [trial for trial in trials if _formally_eligible(trial)]
    return kept, len(trials) - len(kept)


def validate_official_aggregation(
    trials: list[dict],
    security_attestation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed before publishing an aggregate.

    Arithmetic remains available through :func:`aggregate` for local analysis.
    The command-line publication path calls this stricter function: every row
    must carry a positive process-gate result and must be covered by a trusted
    same-runtime egress receipt.  A caller that deliberately wants an
    exploratory/pre-gate number has to choose ``--diagnostic`` at the CLI.
    """
    formal_trials, excluded_ineligible = _formal_trials(trials)
    names = _trial_task_names(formal_trials)
    receipt = validate_security_attestation(
        security_attestation,
        expected_task_names=names,
    )
    _, census = rows_by_rewards(formal_trials, lambda trial: trial.get("rewards"))
    if census.blocked or census.ungated:
        raise SecurityContractError(
            "official aggregation refuses uncertified trials: " + census.note()
        )
    return {
        "security_attestation": receipt,
        "citability": {
            **census.as_dict(),
            "n_evaluation_ineligible_excluded": excluded_ineligible,
        },
    }


def aggregate(
    trials: list[dict],
    *,
    mode: str = "domain_equal",
    weights: dict[str, Any] | None = None,
    keys: list[str] | None = None,
    citable_only: bool = True,
) -> dict[str, Any]:
    """Aggregate reward keys across trials under the chosen weighting mode.

    ``citable_only`` (the default) drops every trial the process gate refused to
    certify, and drops those it never scored. This is the cross-task view of the
    same rule the leaderboards apply: a trial whose answer came off the staged
    ground-truth mask is not a data point about 3D perception, however well it
    scored. Pass ``citable_only=False`` to reproduce a pre-gate number, and read
    ``citability`` either way -- it is what says how much of the run the mean
    actually rests on.
    """
    weights = weights or {}
    default_w = float(weights.get("default", 1.0))
    domain_w = weights.get("domain", {}) or {}
    vclass_w = weights.get("v_class", {}) or {}

    formal_trials, excluded_ineligible = _formal_trials(trials)
    kept, census = rows_by_rewards(formal_trials, lambda t: t.get("rewards"))
    source = kept if citable_only else list(formal_trials)
    keys = keys or _numeric_keys(source)

    # annotate each trial with domain / v_class (metadata first, name fallback)
    annotated = []
    for t in source:
        dom, vc = domain_v_class(t)
        annotated.append({**t, "domain": dom, "v_class": vc})

    summary: dict[str, Any] = {
        "mode": mode,
        "n_trials": len(annotated),
        "n_evaluation_ineligible_excluded": excluded_ineligible,
        "citable_only": citable_only,
        "citability": {**census.as_dict(), "note": census.note()},
        "keys": {},
    }

    for key in keys:
        pairs = [
            (t, float(t["rewards"][key]))
            for t in annotated
            if isinstance(t["rewards"].get(key), (int, float))
            and not isinstance(t["rewards"].get(key), bool)
        ]
        if not pairs:
            continue

        if mode == "flat":
            value = _mean([v for _, v in pairs])

        elif mode == "domain_equal":
            by_dom: dict[str, list[float]] = defaultdict(list)
            for t, v in pairs:
                by_dom[t["domain"] or "_unknown"].append(v)
            value = _mean([_mean(vs) for vs in by_dom.values()])

        elif mode == "weighted":
            num = den = 0.0
            for t, v in pairs:
                w = (
                    float(domain_w.get(t["domain"], default_w))
                    * float(vclass_w.get(t["v_class"], default_w))
                )
                num += w * v
                den += w
            value = num / den if den else 0.0
        else:
            raise ValueError(f"unknown mode: {mode!r}")

        summary["keys"][key] = round(value, 6)

    # always include a per-domain and per-v_class breakdown for the primary key
    primary = "reward" if "reward" in summary["keys"] else (keys[0] if keys else None)
    if primary:
        summary["breakdown"] = _breakdown(annotated, primary)
    return summary


def _breakdown(annotated: list[dict], key: str) -> dict[str, Any]:
    by_dom: dict[str, list[float]] = defaultdict(list)
    by_vc: dict[str, list[float]] = defaultdict(list)
    for t in annotated:
        v = t["rewards"].get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            by_dom[t["domain"] or "_unknown"].append(float(v))
            by_vc[t["v_class"] or "_unknown"].append(float(v))
    return {
        "by_domain": {
            d: {"n": len(vs), "mean": round(_mean(vs), 6)} for d, vs in sorted(by_dom.items())
        },
        "by_v_class": {
            c: {"n": len(vs), "mean": round(_mean(vs), 6)} for c, vs in sorted(by_vc.items())
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, required=True,
                    help="directory of result.json files, or a results.json array")
    ap.add_argument("--mode", choices=["flat", "domain_equal", "weighted"],
                    default="domain_equal")
    ap.add_argument("--weights", type=Path, default=None, help="weights config JSON")
    ap.add_argument("--key", action="append", dest="keys", default=None,
                    help="reward key(s) to aggregate (default: all numeric keys)")
    ap.add_argument("--out", type=Path, default=None, help="write summary JSON here")
    ap.add_argument(
        "--security-attestation",
        type=Path,
        default=None,
        help=(
            "trusted host-side runtime receipt. Required for an official aggregate; "
            "a static preflight manifest is not sufficient."
        ),
    )
    ap.add_argument(
        "--diagnostic",
        action="store_true",
        help=(
            "mark this output exploratory rather than publishable; permits a missing "
            "runtime attestation and --include-uncitable"
        ),
    )
    ap.add_argument("--include-uncitable", action="store_true",
                    help="aggregate every trial, including the ones the process "
                         "gate refused to certify or never scored. Reproduces a "
                         "pre-gate number; requires --diagnostic; the citability "
                         "census is still reported.")
    args = ap.parse_args()

    trials = load_results(args.results)
    weights = json.loads(args.weights.read_text()) if args.weights else None
    if args.include_uncitable and not args.diagnostic:
        ap.error("--include-uncitable is diagnostic-only; add --diagnostic explicitly")

    publication: dict[str, Any]
    if args.diagnostic:
        # Diagnostic output is useful when investigating an incident, but must
        # never be mistaken for a certified benchmark number.  If a receipt is
        # supplied, validate it and surface the result; do not make it required.
        publication = {"mode": "diagnostic", "security_attestation": "not-required"}
        if args.security_attestation is not None:
            try:
                supplied = json.loads(args.security_attestation.read_text())
                publication["security_attestation"] = validate_security_attestation(
                    supplied,
                    expected_task_names=_trial_task_names(trials),
                )
            except (OSError, json.JSONDecodeError, SecurityContractError) as exc:
                publication["security_attestation"] = {
                    "status": "invalid-or-unverified",
                    "reason": str(exc),
                }
    else:
        if args.security_attestation is None:
            ap.error("official aggregation requires --security-attestation (or use --diagnostic)")
        try:
            supplied = json.loads(args.security_attestation.read_text())
            publication = {"mode": "official", **validate_official_aggregation(trials, supplied)}
        except (OSError, json.JSONDecodeError, SecurityContractError) as exc:
            ap.error(str(exc))

    summary = aggregate(
        trials,
        mode=args.mode,
        weights=weights,
        keys=args.keys,
        citable_only=not args.include_uncitable,
    )
    summary["publication"] = publication

    text = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
