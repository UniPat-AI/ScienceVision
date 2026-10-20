#!/usr/bin/env python3
"""Re-score stored ScienceVision trials offline, under two rule sets.

The scorer entry point takes a task document and an answer file and returns a
score, so the effect of a scoring change is measurable on results that already
exist: no model is re-run, no container is built, no network is touched. This
tool reads the delivery read-only and writes its own output somewhere else.

**It replays what actually scored the run, not a reconstruction of it.** The
bundle carries the question and its ground truth, not the compiled contract, so
the rubric is read from each trial's own audit (``verifier/sci3d_scores.json``
-> ``answer_score.raw.verdicts``). That is the exact rubric that produced the
stored score: its items, their comparators, weights and required flags. Only
two things are re-derived, because the audit does not carry them:

- the per-field tolerance, re-derived from the ground truth with the rules at
  this branch point (``scripts/harbor/_tolerance_rules.py``);
- the agent's answer, recovered from ``agent/trajectory.json`` with the
  harness's own reader (``answer_json_to_trajview``) and the harness's own
  strict parser.

The band is the one derivation that is a *model of the tree* rather than a
recording of it. ``7c8a26a2`` (2026-09-28 01:04:39) replaced the decimal rule
``10 ** (1 - d)`` with a flat +/-0.005, and the band is baked in at tree-build
time, so a tree built before that commit carries the decimal band whatever this
tool derives. Most of the delivery's trees predate it -- 430 of 500 runs point
at a tree root stamped 2026-09-26, 14 more at one stamped the switch's own day,
and 56 at trees built after it -- and the two derivations agree on
every stored verdict except three items, all in two architecture tasks, which
is why the reproduction count is what it is. Re-deriving with the decimal band
instead reproduces every trial except the twelve whose ground truth was revised
after the run.

The parse *gate* -- whether the answer document's field set matched the task's
declared schema -- is taken from the audit, because the answer schema lives in
the compiled contract the delivery does not carry. The parse *stage* is
re-checked from the trajectory, so a missing or malformed answer is reported
rather than scored.

``--rules old`` must reproduce every stored score. A replay that cannot
reproduce what it is replaying is not evidence, so every row carries
``reproduced``; ``--rules new`` reports the delta the change actually makes.

``--scorer-root`` says which checkout to import the scoring path from, so the
same copy of this tool can drive the checkout before a rule change and the one
after it. It defaults to the checkout this file lives in, and the run prints
where each of the three imported trees came from. See ``load_scorer`` for why
the import is deferred rather than done at the top of the module: a switch that
cannot move the modules reports the change as null, which is the one wrong
answer this tool must not give.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

# The three trees this tool scores with are imported by ``load_scorer``, not
# here. See the "checkout the scorer is imported from" section: an import that
# happens before ``--scorer-root`` is parsed cannot be redirected afterwards,
# and the run then reports "no difference" when the truth is "no switch".

AUDIT_RELATIVE = "verifier/sci3d_scores.json"
TRAJECTORY_RELATIVE = "agent/trajectory.json"
ANSWER_RELATIVE = "agent/sci3d_answer.json"

RULES_OLD = "old"
RULES_NEW = "new"
RULES = (RULES_OLD, RULES_NEW)

#: Which of the three changes moved a verdict. Named as the plan names them.
RULE_NUMERIC_STRING = "F1"
RULE_REPORT_ONLY = "F2"
RULE_ALIASES = "F3"

#: The score formula's own keys, read from the audit rather than assumed.
SCORE_KEY = "answer_score"

SKIP_NO_RUNS = "the task directory has no runs/ directory"
SKIP_NO_AUDIT = "no audit record at verifier/sci3d_scores.json"
SKIP_NO_RUBRIC = "audit carries no rubric"
SKIP_NO_TRAJECTORY = "no agent trajectory"
SKIP_ANSWER_UNRECOVERABLE = "the answer could not be recovered from the trajectory"
SKIP_UNKNOWN_COMPARATOR = "comparator not implemented by the replay"
SKIP_RUBRIC_GT_MISMATCH = "rubric id is not a ground-truth key"


class ReplayError(RuntimeError):
    """An input the replay cannot turn into an auditable decision."""


# ── the old rules, frozen ─────────────────────────────────────────────────── #
# Three behaviours changed in the branch this tool measures. Replaying "old"
# with the current code would re-score the past with the present, which is the
# one thing a reproduction must not do, so each change is pinned here.

def _legacy_circular_tolerance(predicted: Any, expected: Any, item: dict) -> bool:
    """``_compare_circular_tolerance`` before a numeric string was readable.

    ``_number`` returns ``None`` for any ``str``, so a side stored as a string
    failed the comparison whatever the agent answered. That is the behaviour
    the stored audits were produced with.
    """
    da = _scorer().da
    left = da._number(predicted)
    right = da._number(expected)
    tolerance = da._number(item.get("tolerance"))
    if left is None or right is None or tolerance is None:
        return False
    modulus = da._number(item.get("periodic_modulus", 360.0))
    if modulus is None or modulus <= 0:
        return False
    distance = abs((left - right + modulus / 2.0) % modulus - modulus / 2.0)
    return distance <= tolerance or math.isclose(
        distance, tolerance, rel_tol=1e-12, abs_tol=1e-12
    )


def old_comparators() -> dict[str, Callable[[Any, Any, dict], bool]]:
    """The comparator set as it stood before Tasks 1-3, from the loaded root.

    Every other entry is the live one: only ``circular_tolerance`` changed in
    place, and the two new comparators (``numeric_literal_equal``,
    ``canonical_string_in_set``) cannot appear in an audit written before they
    existed.
    """
    return {
        **_scorer().da._COMPARATORS,
        "circular_tolerance": _legacy_circular_tolerance,
    }


def _legacy_tolerance_for(field: str, value: Any) -> float | list[float] | None:
    """The branch-point band derivation, before a numeric string was a number.

    Byte-for-byte behavioural copy of ``tolerance_for`` as it stood before Task
    2: a string returned no band at all. Kept as a composition rather than a
    second copy of the whole function because that one branch is the only
    difference -- the voxel guard the same commit added is only reachable
    through the string branch.

    This is the derivation at the branch point, not the one the trees mostly
    carried: the flat +/-0.005 band landed in ``7c8a26a2`` on 2026-09-28 and the
    band is fixed at tree-build time, so the trees behind most of these trials
    still hold the decimal band. See the module docstring.
    """
    if isinstance(value, (bool, str)):
        return None
    return _scorer().tolerance_rules.tolerance_for(field, value)


def _tolerance_map(ground_truth: dict, *, rules: str) -> dict[str, Any]:
    tolerance_rules = _scorer().tolerance_rules
    derive = (
        tolerance_rules.tolerance_for if rules == RULES_NEW else _legacy_tolerance_for
    )
    out: dict[str, Any] = {}
    for field, value in ground_truth.items():
        band = derive(str(field), value)
        if band is not None:
            out[str(field)] = band
    return out


# ── the checkout the scorer is imported from ──────────────────────────────── #
# The replay's claim is that two runs differ in the rules and in nothing else,
# so which checkout the rules come from has to be an argument rather than a
# property of where this file sits. The import is deferred to ``load_scorer``
# for the reason that costs a measurement its meaning: ``sys.path`` is only
# consulted for a name that is *not* already in ``sys.modules``, so inserting a
# root after the modules have been imported once returns the first root's
# modules with no error, and two byte-identical output files then read as "the
# change makes no difference" when the truth is "the switch is not connected".

#: The top-level packages the replay takes from the root -- all three, not just
#: the scorer. A run that read its comparators from one checkout and its
#: tolerance rules from another would report the difference between two
#: revisions of the tool, not the difference between two sets of rules.
SCORER_TREES = ("harbor_adapters", "sci3d_bench", "scripts")

#: The checkout this file lives in; its parent is that checkout's root.
DEFAULT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ScorerModules:
    """The three module trees the replay scores with, and where they came from.

    The module objects are held together because they have to move together:
    ``main`` prints one line per tree so a reader can see which checkout a
    number came from rather than having to trust the argument that was passed.
    """
    root: Path
    da: Any                   # sci3d_bench.scorers.deterministic_answer
    tolerance_rules: Any      # scripts.harbor._tolerance_rules
    trajectory_reader: Any    # harbor_adapters.sci3d._verify_translate


_LOADED: ScorerModules | None = None


def _purge_scorer_trees() -> None:
    """Drop both revisions' worth of the three trees from ``sys.modules``.

    Dropping the three top-level names is not enough. A re-import re-runs the
    parent package, but ``from package import submodule`` then finds the *old*
    submodule still cached, so the scorer would move and the module that
    actually computes would not. Submodules go with their package.
    """
    for name in [n for n in list(sys.modules) if n.split(".", 1)[0] in SCORER_TREES]:
        del sys.modules[name]


def _import_scorer(root: Path) -> ScorerModules:
    """Import the three trees from ``root``, refusing a mixture of two roots."""
    import harbor_adapters.sci3d._verify_translate as trajectory_reader
    import sci3d_bench.scorers.deterministic_answer as da
    import scripts.harbor._tolerance_rules as tolerance_rules
    for module in (trajectory_reader, da, tolerance_rules):
        source = Path(module.__file__).resolve()
        if root not in source.parents:
            raise ReplayError(
                f"{module.__name__} resolved to {source}, which is not under "
                f"{root}; the replay would be measuring a mixture of two "
                f"checkouts"
            )
    return ScorerModules(root=root, da=da, tolerance_rules=tolerance_rules,
                         trajectory_reader=trajectory_reader)


def load_scorer(root: Path | None = None) -> ScorerModules:
    """Import the scoring path from ``root``; ``None`` means this checkout.

    ``main`` calls this once, after the arguments are parsed and before any
    trial is read, so the checkout being measured is chosen before anything is
    imported from it. Calling it again with the same root is free; with a
    different one it re-imports.
    """
    global _LOADED
    target = DEFAULT_ROOT if root is None else Path(root).resolve()
    if _LOADED is not None and _LOADED.root == target:
        return _LOADED
    _purge_scorer_trees()
    entry = str(target)
    if not sys.path or sys.path[0] != entry:
        sys.path.insert(0, entry)
    _LOADED = _import_scorer(target)
    return _LOADED


def _scorer() -> ScorerModules:
    """The modules in force, loading this checkout's if none was chosen yet."""
    return _LOADED if _LOADED is not None else load_scorer()


# ── reading the trial ─────────────────────────────────────────────────────── #

def _read_audit(run_dir: Path) -> dict | None:
    """The audit document, or None when the trial has none.

    Opened read-only; nothing under the delivery is ever written.
    """
    path = run_dir / AUDIT_RELATIVE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _recover_answer(run_dir: Path, task_id: str) -> tuple[Any, str | None, bool]:
    """``(answer, parse_error, recovered)`` for one trial.

    Uses the harness's own reader and parser, so the replay sees the same bytes
    the scorer saw. ``recovered`` is False when the trajectory holds no
    parseable answer at all: that is a missing input, not a zero. An answer
    that parses but is not an object is scored zero by the harness, so it is
    reported the same way here rather than treated as missing.
    """
    scorer = _scorer()
    da = scorer.da
    traj_path = run_dir / TRAJECTORY_RELATIVE
    if not traj_path.exists():
        return None, SKIP_NO_TRAJECTORY, False
    try:
        view = scorer.trajectory_reader.answer_json_to_trajview(
            run_dir / ANSWER_RELATIVE, traj_path, str(task_id)
        )
    except Exception:  # a corrupt trajectory must be reported, not raised
        return None, SKIP_ANSWER_UNRECOVERABLE, False
    try:
        return da.parse_strict_answer(view.final_answer), None, True
    except da.AnswerParseError as exc:
        return None, exc.code, False


def _item_from_verdict(verdict: dict, tolerances: dict) -> dict:
    """Rebuild one rubric item from the verdict the trial recorded.

    The verdict holds the item's identity, comparator, weight and required flag
    -- everything the scoring loop reads except the tolerance payload, which is
    re-derived from the ground truth.
    """
    da = _scorer().da
    item_id = str(verdict["id"])
    item: dict[str, Any] = {
        "id": item_id,
        "pred_path": item_id,
        "gt_path": item_id,
        "comparator": str(verdict.get("comparator", "")),
        "weight": float(verdict.get("weight", 1.0)),
        "required": bool(verdict.get("required", True)),
    }
    band = da._tolerance_for(item_id, {"tolerance": tolerances})
    if band is not None:
        if isinstance(band, list):
            item["component_tolerances"] = band
        else:
            item["tolerance"] = band
    return item


def _retarget_for_new_rules(
    item: dict, expected: Any, predicted: Any
) -> tuple[dict, str | None]:
    """Apply the Tasks 1-3 routing to one item, naming the rule that moved it.

    Only the three deltas are applied, keyed on the comparator the trial
    recorded. Re-running the compiler wholesale would re-decide items the
    changes never touched -- and would invent decisions for the audits whose
    contract has since been revised -- so the new pass moves an item only where
    a task in this branch says it may.
    """
    da = _scorer().da
    comparator = item["comparator"]
    if comparator == "canonical_string_equal" and isinstance(expected, str):
        if da._numeric_literal(expected) is not None:
            # F1: the string is a number, so it is graded as one.
            return {**item, "comparator": "numeric_literal_equal"}, RULE_NUMERIC_STRING
        aliases = da.derive_aliases(expected)
        if aliases:
            # F3: the question fixed the value set; the long form is formatting.
            return {**item, "comparator": "canonical_string_in_set", "accepted": aliases}, RULE_ALIASES
    if comparator == "circular_tolerance" and (
        isinstance(expected, str) or isinstance(predicted, str)
    ):
        # F1's carried finding: this comparator owns the angle fields, so a
        # string ground truth reached it and could never pass.
        return dict(item), RULE_NUMERIC_STRING
    return dict(item), None


# ── the scoring loop, mirroring ``DeterministicAnswerScorer.score`` ───────── #

def _run_rubric(
    items: list[dict],
    comparators: dict[str, Callable[[Any, Any, dict], bool]],
    *,
    answer: Any,
    ground_truth: dict,
    parse_error: str | None,
    required_gate: bool,
) -> tuple[list[dict], float, float]:
    """``(verdicts, score, ungated_score)`` for one rule set."""
    da = _scorer().da
    verdicts: list[dict] = []
    passed_weight = 0.0
    total_weight = sum(float(item["weight"]) for item in items)
    required_failure = False
    for item in items:
        pred_ok, predicted = da._path_get(answer, item["pred_path"])
        gt_ok, expected = da._path_get(ground_truth, item["gt_path"])
        comparator = comparators[item["comparator"]]
        passed = bool(
            parse_error is None
            and pred_ok
            and gt_ok
            and comparator(predicted, expected, item)
        )
        weight = float(item["weight"])
        if passed:
            passed_weight += weight
        required = bool(item["required"])
        required_failure = required_failure or (required and not passed)
        reason = "pass" if passed else (
            parse_error or ("missing_pred_path" if not pred_ok else "comparison_failed")
        )
        verdicts.append(
            {
                "id": item["id"],
                "comparator": item["comparator"],
                "weight": weight,
                "required": required,
                "passed": passed,
                "reason": reason,
                "rule": item.get("rule"),
            }
        )
    ungated = passed_weight / total_weight if total_weight else 0.0
    score = 0.0 if required_gate and required_failure else ungated
    return verdicts, score, ungated


def _build_new_items(
    items: list[dict], ground_truth: dict, answer: Any
) -> tuple[list[dict], list[str]]:
    """The rubric the new rules produce, and the ids that left it.

    The decision is the scorer's own ``report_only_drop``, called rather than
    copied: a second copy of the rule drifted once already, when the refusal
    was widened from "would empty the rubric" to "would leave one item". The
    contract's own declared ``report_only`` list is not available -- the
    delivery carries no contract -- so only the default rule applies here.
    """
    da = _scorer().da
    item_ids = {item["id"] for item in items}
    dropped = da.report_only_drop(ground_truth, [], item_ids)
    out: list[dict] = []
    for item in items:
        if item["id"] in dropped:
            continue
        _, predicted = da._path_get(answer, item["pred_path"])
        _, expected = da._path_get(ground_truth, item["gt_path"])
        retargeted, rule = _retarget_for_new_rules(item, expected, predicted)
        out.append({**retargeted, "rule": rule})
    return out, sorted(dropped)


# ── one trial ─────────────────────────────────────────────────────────────── #

def _skipped(task_id: str, arm: str, reason: str, **extra: Any) -> dict:
    return {
        "task_id": task_id,
        "arm": arm,
        "status": "skipped",
        "reason": reason,
        "old_score": None,
        "new_score": None,
        "old_verdicts": [],
        "new_verdicts": [],
        "reproduced": None,
        **extra,
    }


def replay_one(
    *,
    run_dir: Path,
    ground_truth: dict,
    task_id: str,
    arm: str,
    rules: str = RULES_NEW,
) -> dict:
    """Re-score one stored trial and say whether the old score came back.

    ``old_score`` is the score the audit stored -- evidence, read verbatim.
    ``new_score`` is the score under ``rules``. ``reproduced`` is decided from
    the old-rule recomputation alone, so a row written by ``--rules new`` still
    states whether the number it is compared against could be re-derived.
    """
    if rules not in RULES:
        raise ReplayError(f"unknown rules: {rules}")
    run_dir = Path(run_dir)
    audit = _read_audit(run_dir)
    if audit is None:
        return _skipped(task_id, arm, SKIP_NO_AUDIT, rules=rules)

    score_block = audit.get(SCORE_KEY)
    score_block = score_block if isinstance(score_block, dict) else {}
    raw = score_block.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    stored = score_block.get("value")
    stored = float(stored) if isinstance(stored, (int, float)) else None

    verdicts = raw.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        reason = str(raw.get("reason") or SKIP_NO_RUBRIC)
        errors = raw.get("errors")
        if isinstance(errors, list) and errors:
            reason = f"{reason}: {', '.join(str(e) for e in errors)}"
        return _skipped(task_id, arm, reason, old_score=stored, rules=rules)

    da = _scorer().da
    old_rule_comparators = old_comparators()
    unknown = sorted({
        str(v.get("comparator", "")) for v in verdicts
        if not isinstance(v, dict) or str(v.get("comparator", "")) not in old_rule_comparators
    })
    if unknown:
        return _skipped(
            task_id, arm, f"{SKIP_UNKNOWN_COMPARATOR}: {', '.join(unknown)}",
            old_score=stored, rules=rules,
        )
    absent = sorted({str(v["id"]) for v in verdicts if str(v["id"]) not in ground_truth})
    if absent:
        return _skipped(
            task_id, arm, f"{SKIP_RUBRIC_GT_MISMATCH}: {', '.join(absent)}",
            old_score=stored, rules=rules,
        )

    answer, own_parse_error, recovered = _recover_answer(run_dir, task_id)
    recorded_parse_error = raw.get("parse_error")
    if not recovered and not recorded_parse_error:
        return _skipped(
            task_id, arm, f"{SKIP_ANSWER_UNRECOVERABLE}: {own_parse_error}",
            old_score=stored, rules=rules,
        )
    # The audit's own parse gate stands: whether the answer's field set matched
    # the task's declared schema is a decision only the compiled contract can
    # make, and the delivery does not carry it. The parse *stage* is recomputed
    # above, and a disagreement is reported rather than silently reconciled.
    parse_error = str(recorded_parse_error) if recorded_parse_error else (
        None if isinstance(answer, dict) else "answer_not_object"
    )
    parse_note = ""
    if recorded_parse_error and own_parse_error:
        parse_note = f"recomputed parse error: {own_parse_error}"
    elif recorded_parse_error and recovered:
        parse_note = "recorded parse error, but the answer parses: field-set gate"

    required_gate = bool(raw.get("required_gate", False))
    old_items = [
        _item_from_verdict(v, _tolerance_map(ground_truth, rules=RULES_OLD))
        for v in verdicts
    ]
    new_items, report_only = _build_new_items(
        [
            _item_from_verdict(v, _tolerance_map(ground_truth, rules=RULES_NEW))
            for v in verdicts
        ],
        ground_truth,
        answer,
    )
    old_verdicts, old_score, old_ungated = _run_rubric(
        old_items, old_rule_comparators, answer=answer, ground_truth=ground_truth,
        parse_error=parse_error, required_gate=required_gate,
    )
    new_verdicts, new_score, new_ungated = _run_rubric(
        new_items, da._COMPARATORS, answer=answer, ground_truth=ground_truth,
        parse_error=parse_error, required_gate=required_gate,
    )

    chosen = old_score if rules == RULES_OLD else new_score
    return {
        "task_id": task_id,
        "arm": arm,
        "status": "scored",
        "reason": "",
        "rules": rules,
        "old_score": stored,
        "new_score": chosen,
        # The old-rule recomputation, kept on the row whichever rule set is
        # active: it is the column that lets a mean cover all 480 scored trials
        # with both sides computed by the replay, rather than only the 465 whose
        # stored score came back.
        "old_rule_score": old_score,
        "old_ungated_score": old_ungated,
        "new_ungated_score": new_ungated,
        "required_gate": required_gate,
        "parse_error": parse_error,
        "parse_note": parse_note,
        "report_only": report_only,
        "old_verdicts": old_verdicts,
        "new_verdicts": new_verdicts,
        "reproduced": stored is not None and old_score == stored,
    }


# ── the corpus ────────────────────────────────────────────────────────────── #

def index_ground_truth(bundle: Path) -> dict[str, dict]:
    """``task_id -> ground_truth`` for every task document in the bundle."""
    out: dict[str, dict] = {}
    pattern = str(Path(bundle) / "tasks" / "*" / "*" / "*" / "task.json")
    for path in sorted(glob.glob(pattern)):
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        task_id = str(document.get("task_id") or "")
        if not task_id:
            raise ReplayError(f"task document without a task_id: {path}")
        if task_id in out:
            raise ReplayError(f"duplicate task_id in bundle: {task_id}")
        ground_truth = document.get("ground_truth")
        out[task_id] = ground_truth if isinstance(ground_truth, dict) else {}
    return out


def iter_trials(runs_root: Path) -> Iterator[tuple[str, Path, str, str]]:
    """``(task_id, run_dir, arm, domain)`` for every trial the delivery holds.

    A task directory with no ``runs`` at all still yields one entry, so that
    "this task was never scored" is reported rather than silently dropped from
    the denominator.
    """
    root = Path(runs_root)
    # A leading dot is not a domain: the delivery root carries a ``.cache``
    # beside its five domains, and treating it as one invents a task.
    for domain in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for task in sorted(p for p in domain.iterdir() if p.is_dir()):
            arms = task / "runs"
            if not arms.is_dir():
                yield task.name, task, "-", domain.name
                continue
            for arm in sorted(p for p in arms.iterdir() if p.is_dir()):
                yield task.name, arm, arm.name, domain.name


def replay_corpus(*, bundle: Path, runs_root: Path, rules: str) -> list[dict]:
    ground_truth_by_id = index_ground_truth(bundle)
    # Which checkout scored these rows, and which delivery they were read from,
    # stamped on every row. A comparison's premise is that its two inputs are
    # two revisions of one scorer; a premise that lives only in pasted console
    # text is not evidence, so the row carries it and the diff document can
    # quote it back. Rows written before this field existed simply lack it,
    # which is why the reader tolerates its absence rather than requiring it.
    scorer_root = str(_scorer().root)
    delivery = str(Path(runs_root).resolve())
    rows: list[dict] = []
    for task_id, run_dir, arm, domain in iter_trials(runs_root):
        ground_truth = ground_truth_by_id.get(task_id)
        if ground_truth is None:
            row = _skipped(task_id, arm, "task_id is not in the bundle", rules=rules)
        elif arm == "-":
            row = _skipped(task_id, arm, SKIP_NO_RUNS, rules=rules)
        else:
            row = replay_one(
                run_dir=run_dir, ground_truth=ground_truth, task_id=task_id, arm=arm,
                rules=rules,
            )
        row["domain"] = domain
        row["scorer_root"] = scorer_root
        row["runs_root"] = delivery
        rows.append(row)
    return rows


def _mean(rows: list[dict], key: str) -> float:
    return sum(float(r[key]) for r in rows) / len(rows) if rows else 0.0


def _summarize(rows: list[dict], rules: str) -> str:
    """Two means, because they are two different questions.

    ``all scored`` puts both columns on the same 480 rows, both computed by the
    replay, so the difference is the rule change alone -- including for the
    trials whose stored score no longer comes back, whose movement the rules do
    not cause. ``reproduced rows`` reads the old column from the audit and so
    covers only the trials whose base this tool can vouch for.

    Both are the same total gain over different denominators: the rows that do
    not reproduce contribute zero to the *rule* delta, because the input that
    changed moved the old pass and the new pass together.
    """
    scored = [r for r in rows if r["status"] == "scored"]
    skipped = [r for r in rows if r["status"] != "scored"]
    mismatched = [r for r in scored if not r["reproduced"]]
    lines = [
        f"rules={rules} trials={len(rows)} scored={len(scored)} skipped={len(skipped)}",
        f"reproduced={len(scored) - len(mismatched)}/{len(scored)} scored trials",
    ]
    if scored:
        # Both columns by the replay: old rules vs the active rules.
        lines.append(
            f"basis all scored, both columns replayed: old={_mean(scored, 'old_rule_score'):.6f} "
            f"new={_mean(scored, 'new_score'):.6f} "
            f"delta={_mean(scored, 'new_score') - _mean(scored, 'old_rule_score'):+.6f} "
            f"n={len(scored)}"
        )
    reproduced = [r for r in scored if r["reproduced"]]
    if reproduced:
        # Old column read from the audit, on the rows that reproduce.
        lines.append(
            f"basis reproduced rows, old read from the audit: "
            f"old={_mean(reproduced, 'old_score'):.6f} "
            f"new={_mean(reproduced, 'new_score'):.6f} "
            f"delta={_mean(reproduced, 'new_score') - _mean(reproduced, 'old_score'):+.6f} "
            f"n={len(reproduced)}"
        )
    for row in mismatched:
        # The column ``reproduced`` was decided against, not the active rule
        # set's score: a mismatch is a claim about the audit's number versus
        # the old-rule recomputation. Printing ``new_score`` here would show a
        # difference between two *different* rule sets on any row the F1/F2/F3
        # retarget moves, which is a number the flag never compared.
        lines.append(
            f"  MISMATCH {row['task_id']} {row['arm']}: "
            f"stored={row['old_score']!r} computed={row['old_rule_score']!r}"
        )
    for row in skipped:
        lines.append(f"  SKIPPED {row['task_id']} {row['arm']}: {row['reason']}")
    return "\n".join(lines)


def write_rows(rows: list[dict], out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", choices=RULES, default=RULES_NEW)
    parser.add_argument("--bundle", required=True, type=Path,
                        help="ground-truth bundle root (tasks/<domain>/<sub>/<task>/task.json)")
    parser.add_argument("--runs", required=True, type=Path,
                        help="delivery root; read-only, never written")
    parser.add_argument("--out", required=True, type=Path,
                        help="where the JSONL rows go (never inside --runs)")
    parser.add_argument("--scorer-root", type=Path, default=None,
                        help="checkout to import the scorer from; default is this worktree")
    return parser


def _scorer_lines(scorer: ScorerModules) -> list[str]:
    """Which checkout each imported tree came from, as the run's header.

    Printed rather than assumed. Two runs that differ only in this header and
    in one rule are the measurement, so a reader has to be able to see which
    checkout each number came from -- and two runs with the *same* header are
    the switch failing, not the change being null.
    """
    lines = [f"scorer root: {scorer.root}"]
    for module in (scorer.da, scorer.tolerance_rules, scorer.trajectory_reader):
        lines.append(f"  {module.__name__}: {Path(module.__file__).resolve()}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runs_root = Path(args.runs).resolve()
    out = Path(args.out).resolve()
    if runs_root == out or runs_root in out.parents:
        print(f"refusing to write inside the delivery: {out}", file=sys.stderr)
        return 2
    for line in _scorer_lines(load_scorer(args.scorer_root)):
        print(line)
    rows = replay_corpus(bundle=args.bundle, runs_root=args.runs, rules=args.rules)
    write_rows(rows, args.out)
    print(_summarize(rows, args.rules))
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
