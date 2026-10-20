#!/usr/bin/env python3
"""Compare two replay files verdict by verdict, and separate the two directions.

``tools/replay_scoring.py`` writes one row per trial with the verdicts both
rule sets produced. Running it twice with one variable changed -- the checkout
the rules are imported from -- answers the question a before/after mean cannot:
*which* verdicts moved, and in which direction.

That direction is the whole point. A rule that loosens a comparison raises the
mean when it accepts an answer that was right, and raises it just as much when
it accepts one that was wrong. Only a per-verdict comparison can tell the two
apart, so:

- ``regressions`` -- passed before, fails now. The one outcome that makes a
  change wrong, and the one the CLI refuses to exit zero on.
- ``moved`` -- failed before, passes now. The intended effect, listed in full
  so each one can be read against its own ground truth rather than trusted.
- ``vanished`` / ``appeared`` -- the verdict left the rubric, or joined it. Not
  a flip: nothing compares it any more, or nothing did before. Kept visible
  because a rubric change is a real change to the denominator, and a trial that
  could only be scored under one of the two rules would otherwise disappear
  from the comparison without a word.
- ``identical`` -- the count of verdicts compared and unchanged. Reported so
  that the four lists can be read against a denominator: a comparison of
  nothing and a comparison with no differences both produce empty lists.

Rows whose ``status`` is not ``scored`` carry no verdicts, so they are not
compared at all -- a skipped trial is a missing input, not a failing one.

The key is ``(task_id, arm, verdict id)``: the same field on two arms is two
decisions, and they can move in opposite directions. It deliberately does
**not** include the item's comparator, so a routing change that leaves a
verdict passing is ``identical`` here by design: this tool measures what the
corpus's answers do, not how the scorer arrived at them. Pinning the route a
verdict took is the scorer's own tests' job.

A duplicate ``(task_id, arm)`` is an error rather than a last-write-wins merge.
Folding two rows into one dict keeps the later, so a flip recorded by the
earlier row would vanish -- and it would vanish into ``identical``, the count a
reader uses to conclude that nothing happened.

The exit code is the gate. ``0`` when every compared verdict is unchanged or
moved forwards; ``1`` when a verdict flipped backwards or the rubric's
membership changed, neither of which this kind of change is supposed to do
(``--allow-membership-change`` accepts the second); ``2`` when a document was
refused rather than written -- an input that is missing or malformed, an output
that would land inside the delivery the rows came from, two sides scored by one
checkout, or two sides read from different deliveries, which the run may not
know is a mistake (``--allow-same-root`` accepts the first of those two,
``--allow-different-runs`` the second). Exit 2 leaves no document behind, so
that nothing on disk looks like a result.

A truncated input fails closed rather than passing quietly: its verdicts are
all ``vanished``, which is exit 1, not a difference the caller reads past.

The same-checkout refusal is the one that cannot be replaced by reading the
output. When a replay ignores its ``--scorer-root``, both sides come from one
revision, every verdict is genuinely ``identical``, and the comparison reports
"the change does nothing" -- the same numbers as a change that really does
nothing. It is the reason the roots are carried on the rows at all.

The same-delivery refusal is its mirror on the corpus side, and it fails in the
opposite direction. The verdict key is ``(task_id, arm, field)``: two
deliveries that hold the same tasks under the same arms produce the same keys
with *different answers*, so a before-side from one and an after-side from the
other compare two corpora under one set of names. The rule change then takes
credit for the difference in models, the same way a null change takes credit
for an ignored switch. A delivery that swapped tasks in and out is caught by
the membership check; a delivery re-run in place -- same ids, new answers, the
shape of the two rescore files under ``~/sv-freetext/v12_rescore_{old,new}.jsonl``
-- is caught by nothing else.

The document carries its own inputs: ``scorer_roots`` and ``runs_roots`` say
which checkout each side was scored by and which delivery it was read from, so
a reader does not have to take the premise on trust from a console transcript
-- and both are checked against each other before the document is written,
rather than recorded for a reader who may not look.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def read_rows(path: Path) -> list[dict]:
    """The rows of one replay file. JSON Lines, one row per trial."""
    rows: list[dict] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: a row must be an object")
        rows.append(row)
    return rows


def _verdicts(
    rows: list[dict], *, where: str
) -> tuple[dict[tuple[str, str, str], bool], set[tuple[str, str]]]:
    """``((task, arm, id) -> passed, trials that were compared)``.

    Only ``scored`` rows contribute. A row that reached no verdict -- a missing
    audit, an unimplemented comparator -- is not a verdict that flipped, and
    counting its absence as one would invent a regression out of a missing
    input.

    A repeated key raises rather than overwriting. ``task_id`` is not scoped to
    a domain and the arms are directory names, so a duplicate is a real
    possibility; and the failure mode is silent and in the wrong direction --
    the surviving row is the later one, and the verdict the earlier row
    recorded simply disappears.
    """
    verdicts: dict[tuple[str, str, str], bool] = {}
    trials: set[tuple[str, str]] = set()
    for row in rows:
        if row.get("status") != "scored":
            continue
        task_id, arm = str(row.get("task_id", "")), str(row.get("arm", ""))
        if (task_id, arm) in trials:
            raise ValueError(
                f"{where}: duplicate trial {task_id} / {arm}; two rows cannot both "
                f"be one trial, and keeping the last would drop whatever the first "
                f"recorded"
            )
        trials.add((task_id, arm))
        for verdict in row.get("new_verdicts") or ():
            key = (task_id, arm, str(verdict["id"]))
            if key in verdicts:
                raise ValueError(
                    f"{where}: duplicate verdict {key[2]!r} in trial {task_id} / {arm}"
                )
            verdicts[key] = bool(verdict.get("passed"))
    return verdicts, trials


def _flip(key: tuple[str, str, str], old_passed: bool, new_passed: bool) -> dict:
    task_id, arm, field = key
    return {"task_id": task_id, "arm": arm, "field": field,
            "old_passed": old_passed, "new_passed": new_passed}


def _presence(key: tuple[str, str, str]) -> dict:
    task_id, arm, field = key
    return {"task_id": task_id, "arm": arm, "field": field}


def compare(old_rows: list[dict], new_rows: list[dict]) -> dict[str, Any]:
    """What moved between two replays of the same corpus.

    Returns ``{"moved", "regressions", "vanished", "appeared", "identical"}``;
    the four lists are ordered by key so that two runs of this tool over the
    same inputs produce the same document. Raises ``ValueError`` on a duplicate
    trial or verdict key rather than letting one row stand in for two.
    """
    old, _ = _verdicts(old_rows, where="old")
    new, _ = _verdicts(new_rows, where="new")
    moved: list[dict] = []
    regressions: list[dict] = []
    vanished: list[dict] = []
    appeared: list[dict] = []
    identical = 0
    for key in sorted(set(old) | set(new)):
        if key not in new:
            vanished.append(_presence(key))
        elif key not in old:
            appeared.append(_presence(key))
        elif old[key] == new[key]:
            identical += 1
        elif new[key]:
            moved.append(_flip(key, old[key], new[key]))
        else:
            regressions.append(_flip(key, old[key], new[key]))
    return {"moved": moved, "regressions": regressions, "vanished": vanished,
            "appeared": appeared, "identical": identical}


def _counts(rows: list[dict]) -> tuple[int, int]:
    """``(trials compared, verdicts compared)`` for one replay file.

    Printed so that the five counts below can be read against a denominator:
    an empty comparison and a comparison with no differences look the same
    without one.
    """
    verdicts, trials = _verdicts(rows, where="counts")
    return len(trials), len(verdicts)


def _roots(rows: list[dict], field: str) -> list[str]:
    """The distinct values one provenance field takes across a replay's rows.

    A list rather than a value: a file that mixes roots is itself worth seeing,
    and an old file that carries none yields ``[]`` rather than an error.
    """
    return sorted({str(row[field]) for row in rows if row.get(field)})


def provenance(old_rows: list[dict], new_rows: list[dict]) -> dict[str, Any]:
    """Which checkout and which delivery each side's rows name."""
    return {
        "scorer_roots": {"old": _roots(old_rows, "scorer_root"),
                         "new": _roots(new_rows, "scorer_root")},
        "runs_roots": {"old": _roots(old_rows, "runs_root"),
                       "new": _roots(new_rows, "runs_root")},
    }


def delivery_refusal(provenance_document: dict, out: Path) -> str | None:
    """The refusal message if ``out`` would land inside a delivery, else None.

    The same rule ``replay_scoring`` applies to its own ``--out``, taken from
    the rows instead of from an argument: the delivery those rows were read
    from is read-only evidence, and this tool is not the place to find out
    otherwise. A row file written before the field existed names no delivery,
    so there is nothing to refuse.

    The row's root is resolved before it is compared. ``replay_corpus`` stamps
    a resolved path, so an unresolved one is not reachable through this tool's
    own output -- but a guard that only holds for the spelling its own writer
    happens to use is one symlink away from not holding at all.
    """
    out = Path(out).resolve()
    for side in ("old", "new"):
        for root in provenance_document["runs_roots"][side]:
            delivery = Path(root).resolve()
            if delivery == out or delivery in out.parents:
                return (f"refusing to write inside the delivery these rows were "
                        f"read from: {out}")
    return None


def same_root_refusal(provenance_document: dict, *, allow_same_root: bool) -> str | None:
    """The refusal if both sides were scored by one checkout, else None.

    A comparison exists to measure what a change did, so two runs from the same
    revision are vacuous rather than reassuring: every verdict is ``identical``
    and the document reads as "this change does nothing" when what actually
    happened is that the switch never moved. That failure is invisible
    everywhere else -- the verdicts really are identical, because they came
    from one scorer -- so the check belongs beside the numbers it would
    otherwise misrepresent, not in a shell step next to them.

    Files that name no root at all are allowed through: nothing is known about
    them, and refusing would make every pre-provenance replay unreadable.
    """
    if allow_same_root:
        return None
    old = provenance_document["scorer_roots"]["old"]
    new = provenance_document["scorer_roots"]["new"]
    if old and old == new:
        return (
            f"both sides were scored by the same checkout ({', '.join(old)}); a "
            f"comparison of one revision with itself cannot show what a change "
            f"did -- pass --allow-same-root if comparing two runs of one "
            f"revision is what you meant"
        )
    return None


def different_runs_refusal(provenance_document: dict, *,
                           allow_different_runs: bool) -> str | None:
    """The refusal if the two sides do not name one delivery, else None.

    The key under which verdicts are compared is ``(task_id, arm, field)``, and
    a delivery can be re-run over its own tasks: the same keys then carry
    different *answers*. Both sides are genuine replays and every verdict is a
    real comparison, so nothing downstream is malformed -- but the difference
    between them is the model's run-to-run variance, not the change, and the
    document hands it back with the same authority as a measured flip. The
    rubric-membership check catches only the tasks that entered or left the
    corpus, so a delivery re-run in place is caught by nothing else.

    Unlike the checkout half, a *missing* root is not a pass here: the pair
    this guards against often has one side written before the rows carried
    provenance, so requiring the two sets to match exactly is what makes the
    guard reach the case it was written for. Both sides naming none is a
    match -- files written before the field existed stay readable against each
    other -- and the opt-out flag is one word for the rest.
    """
    if allow_different_runs:
        return None
    old_roots = provenance_document["runs_roots"]["old"]
    new_roots = provenance_document["runs_roots"]["new"]
    if old_roots != new_roots:
        return (
            f"the two sides do not name the same delivery (old: "
            f"{_join(old_roots)}; new: {_join(new_roots)}); a verdict key names "
            f"answers as well as fields, so a comparison needs both sides read "
            f"from one corpus -- pass --allow-different-runs if comparing "
            f"across deliveries is what you meant"
        )
    return None


def _report(diff: dict, provenance_document: dict, old: Path, new: Path,
            old_rows: list[dict], new_rows: list[dict]) -> str:
    """One screen: the inputs, the counts, then every flip with its key.

    Every ``moved`` and ``regressions`` entry is printed rather than counted,
    because the question this tool answers is *which* answer changed its
    verdict, and a bare count of a signature is what let the last one through.
    """
    trials_old, verdicts_old = _counts(old_rows)
    trials_new, verdicts_new = _counts(new_rows)
    lines = [
        f"old: {old} ({trials_old} trials, {verdicts_old} verdicts) "
        f"scored by {_join(provenance_document['scorer_roots']['old'])}",
        f"new: {new} ({trials_new} trials, {verdicts_new} verdicts) "
        f"scored by {_join(provenance_document['scorer_roots']['new'])}",
        "moved={moved} regressions={regressions} vanished={vanished} "
        "appeared={appeared} identical={identical}".format(**diff),
    ]
    for name in ("regressions", "moved"):
        for entry in diff[name]:
            lines.append(
                f"  {name[:-1].upper()} {entry['task_id']} {entry['arm']} "
                f"{entry['field']}: {entry['old_passed']} -> {entry['new_passed']}"
            )
    return "\n".join(lines)


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "(no root recorded)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", required=True, type=Path,
                        help="the replay before the change (JSONL)")
    parser.add_argument("--new", required=True, type=Path,
                        help="the replay after the change (JSONL)")
    parser.add_argument("--out", required=True, type=Path,
                        help="where the comparison document goes (JSON)")
    parser.add_argument("--allow-membership-change", action="store_true",
                        help="do not fail the gate when a verdict left or joined "
                             "the rubric; for callers that expect it")
    parser.add_argument("--allow-same-root", action="store_true",
                        help="do not refuse when both sides name one checkout; "
                             "for diffing two runs of the same revision")
    parser.add_argument("--allow-different-runs", action="store_true",
                        help="do not refuse when the two sides name different "
                             "deliveries; for comparing two corpora on purpose")
    return parser


def _refusals(diff: dict, *, allow_membership_change: bool) -> list[str]:
    """Why the gate should fail, in the order a reader wants them.

    A membership change fails the gate too. This kind of change claims to touch
    comparisons, not which items there are, so a verdict leaving or joining the
    rubric is itself unexpected -- and it is the shape a truncated input takes,
    where every verdict is ``vanished`` and the run otherwise reports no
    regressions at all. Failing closed on it is what makes ``&&`` mean
    something.
    """
    refusals = []
    if diff["regressions"]:
        refusals.append(
            f"{len(diff['regressions'])} verdict(s) passed before and fail now"
        )
    if not allow_membership_change and (diff["vanished"] or diff["appeared"]):
        refusals.append(
            f"the rubric changed: {len(diff['vanished'])} verdict(s) vanished, "
            f"{len(diff['appeared'])} appeared (--allow-membership-change to "
            f"accept this)"
        )
    return refusals


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    old = Path(args.old)
    new = Path(args.new)
    try:
        old_rows = read_rows(old)
        new_rows = read_rows(new)
        diff = compare(old_rows, new_rows)
    except (ValueError, OSError) as exc:
        # An input the comparison cannot be made over -- malformed, or not
        # there at all. Reported as itself, at exit 2, rather than as a
        # traceback or as a result.
        print(str(exc), file=sys.stderr)
        return 2
    provenance_document = provenance(old_rows, new_rows)
    out = Path(args.out).resolve()
    for refusal in (delivery_refusal(provenance_document, out),
                    same_root_refusal(provenance_document,
                                      allow_same_root=args.allow_same_root),
                    different_runs_refusal(
                        provenance_document,
                        allow_different_runs=args.allow_different_runs)):
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    document = {**diff, **provenance_document}
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(_report(diff, provenance_document, old, new, old_rows, new_rows))
    print(f"wrote {out}")
    refusals = _refusals(diff, allow_membership_change=args.allow_membership_change)
    for line in refusals:
        print(f"REFUSED: {line}", file=sys.stderr)
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
