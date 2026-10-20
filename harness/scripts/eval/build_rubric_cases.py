"""Worked cases for the three open-ended questions in the dev subset.

A score is not reviewable. This emits, per question, the ground truth, the
proposed rubric, the derived fallback, and every test answer scored under both
-- with each keyword group marked hit or missed *and the reason*, so a reviewer
can disagree with a specific line rather than with a number.

**The proposed rubrics are drafts.** They were written to demonstrate that the
mechanism separates a paraphrase from a contradiction; they are not expert
judgements and must not be treated as ground truth until someone signs them.
The file says so in ``rubric_status`` next to the rubric itself, because a
rubric that looks authoritative in isolation is how an unreviewed draft becomes
a de-facto standard.

Usage::

    python scripts/eval/build_rubric_cases.py --bundle /path/to/STEM-250-review-v7
    python scripts/eval/build_rubric_cases.py --bundle ... --write
    python scripts/eval/build_rubric_cases.py --bundle ... --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO_ROOT / "scripts" / "eval" / "rubric_cases"

sys.path.insert(0, str(_REPO_ROOT))

from sci3d_bench.scorers.text_rubric import (  # noqa: E402
    KeywordGroup,
    TextRubric,
    derive_rubric,
    group_is_satisfied,
    negated_positions,
    rubric_to_json,
    score_text,
    tokenize,
)

RUBRIC_STATUS = "draft - written to exercise the mechanism, awaiting expert sign-off"

PROPOSED: dict[str, TextRubric] = {
    "sv-arch-exp-05": TextRubric(
        required=(
            KeywordGroup(("symmetric", "symmetry")),
            KeywordGroup(("mid-span", "midspan", "mid span", "midpoint", "centre", "center")),
            KeywordGroup(("twist", "twisting", "torsion", "torsional"), negated=True),
        ),
        notes="MG1 deformation pattern: symmetric, peak at mid-span, no torsion",
    ),
    "sv-arch-a4-1": TextRubric(
        required=(
            KeywordGroup(("support", "supports")),
            KeywordGroup(("unchanged", "unmoved", "same", "fixed")),
            KeywordGroup(("symmetric", "symmetry")),
            KeywordGroup(("midpoint", "mid-span", "midspan", "mid span")),
        ),
        notes="MG1 end supports unchanged; symmetric bending peaking at midpoint",
    ),
    "sv-arch-a1-2": TextRubric(
        required=(
            KeywordGroup(("service", "services", "pipe", "pipes", "duct", "ducts",
                          "conduit", "conduits")),
            # Affirmative on purpose. The first draft marked this group negated
            # and listed both polarities as alternatives -- `within` alongside
            # `not beyond` -- which can never work: the flag applies to the whole
            # group, so an answer saying "the pipes stay within the structure"
            # missed a group it plainly satisfies. Containment can be asserted
            # either way, so the alternatives carry the polarity instead.
            KeywordGroup(("within", "inside", "not beyond", "no penetration",
                          "do not extend", "does not extend")),
            KeywordGroup(("construction error", "construction errors", "no error",
                          "no errors"), negated=True),
        ),
        notes="A6/V4 service penetrations: none extend beyond structure, no errors",
    ),
}

CASES: dict[str, list[tuple[str, str]]] = {
    "sv-arch-exp-05": [
        ("paraphrase, correct",
         "The deformation is symmetric with the largest deflection at midspan; "
         "there is no torsion."),
        ("contradicts the reference",
         "The deformation is symmetric with the largest deflection at midspan; "
         "twisting occurs."),
        ("unrelated",
         "The bridge is made of steel and has three spans."),
    ],
    "sv-arch-a4-1": [
        ("paraphrase, correct",
         "Both end supports are unchanged and the deformation is symmetric, "
         "peaking at the middle of the span."),
        ("contradicts the reference",
         "Both end supports moved and the deformation is asymmetric."),
    ],
    "sv-arch-a1-2": [
        ("paraphrase, correct",
         "The pipes stay within the structural elements; there are no "
         "construction errors."),
        ("contradicts the reference",
         "The pipes extend beyond the structural elements and there are "
         "construction errors."),
    ],
}


def _group_verdict(answer: str, group: KeywordGroup) -> dict[str, Any]:
    """Why this group did or did not count, in the reviewer's own terms."""
    tokens = tokenize(answer)
    negated = negated_positions(answer, tokens)
    satisfied = group_is_satisfied(tokens, group, negated)
    present = [alt for alt in group.alternatives if tokenize(alt) and
               _appears(tokenize(alt), tokens)]
    if satisfied:
        reason = "negated, and the answer negates it" if group.negated else "present"
    elif present:
        reason = (
            "present but affirmative - the group requires a negator governing it"
            if group.negated
            else "not present"
        )
    else:
        reason = "no alternative appears in the answer"
    return {
        "any_of": list(group.alternatives),
        "negated": group.negated,
        "matched": satisfied,
        "reason": reason,
        "alternatives_present": present,
    }


def _appears(phrase: Sequence[str], tokens: Sequence[str]) -> bool:
    width = len(phrase)
    if width == 0 or width > len(tokens):
        return False
    return any(
        tuple(tokens[i : i + width]) == tuple(phrase)
        for i in range(len(tokens) - width + 1)
    )


def _case(ground_truth: str, authored: TextRubric, label: str, answer: str) -> dict[str, Any]:
    derived = derive_rubric(ground_truth)
    authored_result = score_text(answer, authored)
    derived_result = score_text(answer, derived)
    return {
        "label": label,
        "answer": answer,
        "authored": {
            "value": round(authored_result.value, 4),
            "degraded": authored_result.degraded,
            "matched": authored_result.raw.get("matched"),
            "missed": authored_result.raw.get("missed"),
            "required": authored_result.raw.get("required"),
            "groups": [_group_verdict(answer, g) for g in authored.required],
        },
        "derived": {
            "value": round(derived_result.value, 4),
            "degraded": derived_result.degraded,
            "matched": derived_result.raw.get("matched"),
            "missed": derived_result.raw.get("missed"),
            "required": derived_result.raw.get("required"),
        },
    }


def build(bundle: Path) -> dict[str, dict[str, Any]]:
    index = {
        json.loads(line)["task_id"]: json.loads(line)
        for line in (bundle / "index.jsonl").read_text(encoding="utf-8").splitlines()
    }
    out: dict[str, dict[str, Any]] = {}
    for task_id, rubric in PROPOSED.items():
        row = index[task_id]
        task = json.loads((bundle / row["folder"] / "task.json").read_text(encoding="utf-8"))
        ground_truth = task["ground_truth"]
        if not isinstance(ground_truth, str):
            raise SystemExit(
                f"{task_id}: the bundle's answer is now {task.get('answer_form')}, not "
                "prose. This case was written for an open-ended answer; regenerate it "
                "against the current bundle or drop it."
            )
        derived = derive_rubric(ground_truth)
        out[task_id] = {
            "task_id": task_id,
            "sub_domain": row["sub_domain"],
            "domain": row["domain"],
            "viewer": row["viewer"],
            "folder": row["folder"],
            "question_en": (task.get("question") or {}).get("en", ""),
            "ground_truth": ground_truth,
            "ground_truth_source": task.get("ground_truth_source"),
            "rubric_status": RUBRIC_STATUS,
            "proposed_rubric": rubric_to_json(rubric),
            "derived_rubric": rubric_to_json(derived),
            "derived_group_count": len(derived.required),
            "cases": [
                _case(ground_truth, rubric, label, answer)
                for label, answer in CASES[task_id]
            ],
        }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    if not (args.bundle / "index.jsonl").exists():
        print(f"{args.bundle}/index.jsonl not found", file=sys.stderr)
        return 2

    docs = build(args.bundle)
    emitted = {
        args.out / f"{task_id}.json": json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        for task_id, doc in docs.items()
    }

    for task_id, doc in docs.items():
        print(f"{task_id}")
        for case in doc["cases"]:
            a, d = case["authored"], case["derived"]
            print(f"    {case['label']:28s} authored {a['value']:.2f}   derived {d['value']:.2f}")
        print()

    if args.check:
        stale = [
            p for p, text in emitted.items()
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        for p in stale:
            print(f"{p} is stale; rerun with --write", file=sys.stderr)
        return 1 if stale else 0
    if args.write:
        for path, text in emitted.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path}")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
