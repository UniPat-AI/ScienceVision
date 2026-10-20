"""Pick the eval dev subset: two questions per sub-domain, quota-filled.

Where ``build_smoke_subset`` asks *does this commit's pipeline run end to end*,
this asks *did the change move the number, and did it move it for the right
reason*. The second question is harder on the pick, in two ways:

**A score you cannot read is not a measurement.** So the selection prefers
``evaluation_ready`` rows with no ``warn_kinds``, and every row it emits carries
``score_is_trustworthy`` plus the reason it is outside the clean pool. An
iteration set built without that flag silently mixes "the model got worse" with
"this question's answer key was never approved".

**A change that breaks one sub-domain has to be visible.** So the spine is
``--per-sub-domain`` (default 2) drawn from *every* sub-domain present, not a
proportional sample -- ``arch_structural_deformation`` has 3 rows and
``bio_molecular_contact`` has 50, and both get the same quota. That makes the
subset deliberately un-representative of the corpus: read it as a coverage
instrument, never as a corpus estimate.

The two requirements fight, and the corpus decides who wins. Seven of the
fifteen sub-domains cannot fill a quota of two from the clean pool at all --
four mechanical-CAD sub-domains are blocked on ``answer_not_yet_qc_approved``,
``chem_molecular_conformation`` carries one ``answer_key_may_not_match_question``
on all 20 of its rows, ``arch_structural_deformation`` is unready without being
warned, and ``cad_geometry_drawings`` holds a single question in the whole
bundle. Dropping them would leave the set blind exactly where the pipeline is
newest, so they are included and marked.

The tiers are the same two as the smoke subset, with the same meaning:

``core``    Rows from the clean pool. A score that moves here is the pipeline.
``coverage`` Rows added to reach the quota when the clean pool ran out. Most are
            untrustworthy, but the flag -- not the tier -- is what says so: a
            sub-domain with one clean row and one warned row contributes the
            clean one to ``core`` and the warned one here, while a sub-domain
            with two clean rows puts both in ``core`` and nothing here.

Usage::

    python scripts/eval/build_dev_subset.py --bundle /path/to/STEM-250-review-v7
    python scripts/eval/build_dev_subset.py --bundle ... --write
    python scripts/eval/build_dev_subset.py --bundle ... --check   # CI
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = _REPO_ROOT / "scripts" / "eval" / "dev_subset.json"
DEFAULT_TSV = _REPO_ROOT / "scripts" / "eval" / "dev_subset.tsv"

# The same pick, flat, so the set can be read and diffed without a JSON parser.
# `task_id` leads because that is the column you grep for.
TSV_COLUMNS = (
    "task_id",
    "tier",
    "sub_domain",
    "domain",
    "viewer",
    "task_type",
    "answer_form",
    "evaluation_ready",
    "score_is_trustworthy",
    "folder",
)

PER_SUB_DOMAIN = 2

RULE = (
    "rows with no answer are dropped before selection; then up to two per "
    "sub_domain, preferring evaluation_ready with no warn_kinds, then fewest "
    "warn_kinds, then lowest asset_count, then task_id ascending; a sub_domain "
    "whose clean pool cannot fill the quota is topped up from the relaxed pool, "
    "preferring a viewer family the sub_domain does not already hold; finally "
    "every remaining question carrying a free-text ground-truth field is added, "
    "outside the quota, so the text comparator always has coverage; every row "
    "outside the clean pool carries the reason"
)

# A ground-truth field whose value is a sentence rather than a label, and so
# cannot be reached by any field comparator. Length is measured in characters
# and CJK is counted separately, because a Chinese sentence has no spaces and a
# whitespace word count reads one as a single word.
_FREE_TEXT_CJK = 8
_FREE_TEXT_WORDS = 4
_FREE_TEXT_CHARS = 25
_CJK = re.compile(r"[一-鿿]")


def is_free_text(value: Any) -> bool:
    """Whether a ground-truth field holds prose instead of a comparable value."""
    if not isinstance(value, str):
        return False
    if len(_CJK.findall(value)) >= _FREE_TEXT_CJK:
        return True
    return len(value.split()) >= _FREE_TEXT_WORDS and len(value) > _FREE_TEXT_CHARS


# --- the pool rules -------------------------------------------------------- #


def _warns(row: dict[str, Any]) -> list[str]:
    return list(row.get("warn_kinds") or [])


def is_clean(row: dict[str, Any]) -> bool:
    """A question with nothing known against it. The only tier whose score reads."""
    return bool(row.get("evaluation_ready")) and not _warns(row)


def has_answer(row: dict[str, Any]) -> bool:
    """Whether there is a ground truth to compare against at all.

    ``answer_form == "absent"`` is the index's own name for this, and it is exact
    rather than approximate: across the 250-row bundle it agrees with an empty
    ``ground_truth`` in every single row, both directions. A question without an
    answer cannot be scored by any comparator, so it is dropped before selection
    rather than carried and marked -- keeping it would spend a quota slot on a
    row that can only ever return nothing, and would understate every sub-domain
    it appears in.
    """
    return row.get("answer_form") != "absent"


def _pick_key(row: dict[str, Any]) -> tuple:
    """Deterministic order within a group: least-flagged, then cheapest, then id."""
    return (len(_warns(row)), row.get("asset_count") or 0, str(row["task_id"]))


def _pick(group: Iterable[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return sorted(group, key=_pick_key)[:n]


def _pick_diverse(
    group: Iterable[dict[str, Any]], n: int, seen_families: set[str]
) -> list[dict[str, Any]]:
    """Top up from the relaxed pool, spending the quota on an unexercised family.

    A viewer family a sub-domain does not already hold outranks every other
    consideration, because a family absent from the set is a family a regression
    cannot show up in -- which is the whole reason this subset exists. The cost is
    real and bounded: the unexercised family is usually the more expensive row.
    """
    ordered = sorted(
        group,
        key=lambda r: (
            str(r["viewer"]) in seen_families,
            *_pick_key(r),
        ),
    )
    return ordered[:n]


def not_clean_reasons(row: dict[str, Any]) -> list[str]:
    """Why this row is outside the clean pool, in the order a reader checks.

    Spelled out rather than left to be inferred from the two fields: a coverage
    row can carry an empty ``warn_kinds`` and ``evaluation_ready: false``, which
    reads as a contradiction until the reason says "not evaluation_ready".
    """
    reasons = list(_warns(row))
    if not row.get("evaluation_ready"):
        reasons.append("not evaluation_ready")
    return reasons


# --- the selection --------------------------------------------------------- #


def select(
    rows: Sequence[dict[str, Any]],
    per_sub_domain: int = PER_SUB_DOMAIN,
    text_fields: dict[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Fill every sub-domain to the quota, clean first, and say what fell short.

    ``text_fields`` maps a task id to the ground-truth fields that hold prose.
    Those questions are added last and outside the quota: the quota is about
    breadth across sub-domains, and a question that exercises the text
    comparator is worth having whether or not its sub-domain already spent its
    two slots.
    """
    text_fields = text_fields or {}
    sub_domains = sorted({r["sub_domain"] for r in rows})
    families = sorted({str(r["viewer"]) for r in rows})

    by_sub_domain: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by_sub_domain[r["sub_domain"]].append(r)

    core: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []

    for sd in sub_domains:
        group = by_sub_domain[sd]
        clean_picks = _pick([r for r in group if is_clean(r)], per_sub_domain)
        core += clean_picks

        # Top up from the relaxed pool, excluding what the clean pass took. The
        # pick key still orders it, so a warned row beats an unready one and a
        # cheap row beats an expensive one -- the quota says how many, not which.
        deficit = per_sub_domain - len(clean_picks)
        if deficit <= 0:
            continue
        taken = {r["task_id"] for r in clean_picks}
        held = {str(r["viewer"]) for r in clean_picks}
        coverage += _pick_diverse(
            [r for r in group if r["task_id"] not in taken], deficit, held
        )

    # Outside the quota, and deliberately so: this tier exists to guarantee the
    # text comparator is exercised, and a rule that let breadth veto it would
    # drop exactly the questions the tier was added for.
    chosen = {r["task_id"] for r in core} | {r["task_id"] for r in coverage}
    text_path = sorted(
        (r for r in rows if r["task_id"] not in chosen and text_fields.get(r["task_id"])),
        key=lambda r: str(r["task_id"]),
    )

    picked = core + coverage + text_path
    return {
        "core": core,
        "coverage": coverage,
        "text_path": text_path,
        "achieved": _coverage(picked, per_sub_domain, core + coverage),
        "gaps": _gaps(picked, sub_domains, families, per_sub_domain, core + coverage),
    }


def _coverage(
    picked: Sequence[dict[str, Any]],
    per_sub_domain: int,
    quota_picked: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    # `quota_met` is about the quota tiers only. Counting the text-path tier here
    # would drop a sub-domain that filled its quota exactly and then gained a row.
    quota = collections.Counter(r["sub_domain"] for r in quota_picked)
    return {
        "n": len(picked),
        "per_sub_domain": per_sub_domain,
        "sub_domains": sorted({r["sub_domain"] for r in picked}),
        "viewer_families": sorted({str(r["viewer"]) for r in picked}),
        "domains": sorted({r["domain"] for r in picked}),
        "mcp_allow_list_sources": sorted(
            {str(r["mcp_allow_list_source"]) for r in picked}
        ),
        # The number an iteration actually reads: how many rows can carry a
        # verdict, and how many are along for coverage.
        "trustworthy": sum(1 for r in picked if is_clean(r)),
        "untrustworthy": sum(1 for r in picked if not is_clean(r)),
        # `untrustworthy` now means "scorable, but read the reason" -- the rows
        # that could not be scored at all are gone from the pool before this
        # count is taken, so every emitted row can return a verdict.
        "by_answer_form": dict(
            sorted(collections.Counter(str(r.get("answer_form")) for r in picked).items())
        ),
        "quota_met": sorted(sd for sd in quota if quota[sd] == per_sub_domain),
        "with_asset_2d": sum(1 for r in picked if r.get("asset_2d")),
        "without_asset_2d": sum(1 for r in picked if not r.get("asset_2d")),
        "single_asset": sum(1 for r in picked if (r.get("asset_count") or 0) == 1),
        "multi_asset": sum(1 for r in picked if (r.get("asset_count") or 0) > 1),
    }


def _gaps(
    picked: Sequence[dict[str, Any]],
    sub_domains: Sequence[str],
    families: Sequence[str],
    per_sub_domain: int,
    quota_picked: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """What the subset does not reach, and where the quota could not be filled.

    ``under_quota`` counts the quota tiers only, and the shortfall is in the
    bundle rather than the selection: no re-run of the rule will close it.
    """
    per = collections.Counter(r["sub_domain"] for r in picked)
    quota = collections.Counter(r["sub_domain"] for r in quota_picked)
    got_fam = {str(r["viewer"]) for r in picked}
    return {
        "sub_domains": [s for s in sub_domains if per[s] == 0],
        "under_quota": {s: quota[s] for s in sub_domains if quota[s] < per_sub_domain},
        "viewer_families": [f for f in families if f not in got_fam],
    }


def _row_out(row: dict[str, Any], text_fields: Sequence[str] = ()) -> dict[str, Any]:
    """The fields a consumer needs to run the task and to judge its score."""
    return {
        "task_id": row["task_id"],
        "free_text_fields": list(text_fields),
        "folder": row["folder"],
        "sub_domain": row["sub_domain"],
        "domain": row["domain"],
        "viewer": str(row["viewer"]),
        "task_type": row.get("task_type"),
        "asset_count": row.get("asset_count"),
        "has_asset_2d": bool(row.get("asset_2d")),
        "mcp_allow_list_source": row.get("mcp_allow_list_source"),
        "evaluation_ready": bool(row.get("evaluation_ready")),
        "answer_form": row.get("answer_form"),
        "warn_kinds": _warns(row),
        "not_clean_reasons": not_clean_reasons(row),
        "score_is_trustworthy": is_clean(row),
    }


# --- emit and check -------------------------------------------------------- #


def free_text_fields(bundle: Path, row: dict[str, Any]) -> list[str]:
    """Ground-truth fields of one task that hold prose, read from its task.json.

    The index does not carry field-level detail, so this is the one place the
    builder reaches past ``index.jsonl`` into the bundle's layout.
    """
    task = json.loads(
        (bundle / row["folder"] / "task.json").read_text(encoding="utf-8")
    )
    ground_truth = task.get("ground_truth")
    if not isinstance(ground_truth, dict):
        return []
    return sorted(k for k, v in ground_truth.items() if is_free_text(v))


def build(bundle: Path, per_sub_domain: int = PER_SUB_DOMAIN) -> dict[str, Any]:
    index = bundle / "index.jsonl"
    raw = index.read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    scorable = [r for r in rows if has_answer(r)]
    text_fields = {r["task_id"]: free_text_fields(bundle, r) for r in scorable}
    result = select(scorable, per_sub_domain, text_fields)
    return {
        "schema_version": "sciencevision.eval_dev_subset.v1",
        "bundle": {
            "path_name": bundle.name,
            "index_sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(rows),
            "scorable_pool": len(scorable),
            "clean_pool": sum(1 for r in scorable if is_clean(r)),
        },
        "pool_excluded": [
            {
                "task_id": r["task_id"],
                "folder": r["folder"],
                "sub_domain": r["sub_domain"],
                "reason": "no answer: ground_truth is empty",
            }
            for r in rows
            if not has_answer(r)
        ],
        "per_sub_domain": per_sub_domain,
        "rule": RULE,
        "core": [
            _row_out(r, text_fields.get(r["task_id"], ())) for r in result["core"]
        ],
        "coverage": [
            _row_out(r, text_fields.get(r["task_id"], ())) for r in result["coverage"]
        ],
        "text_path": [
            _row_out(r, text_fields.get(r["task_id"], ()))
            for r in result["text_path"]
        ],
        "achieved": result["achieved"],
        "gaps": result["gaps"],
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def tsv_text(doc: dict[str, Any]) -> str:
    """The same rows as the JSON pin, flat and greppable.

    Emitted from the same in-memory document rather than re-derived, so the two
    files cannot disagree: if the TSV and the JSON ever diverge, one of them was
    edited by hand.
    """
    lines = ["\t".join(TSV_COLUMNS)]
    for tier in ("core", "coverage", "text_path"):
        for r in doc[tier]:
            row = dict(r, tier=tier)
            lines.append("\t".join(_cell(row.get(c)) for c in TSV_COLUMNS))
    return "\n".join(lines) + "\n"


def _self_check(doc: dict[str, Any]) -> list[str]:
    """Invariants that make the file trustworthy without re-deriving it."""
    problems: list[str] = []
    core, cov = doc["core"], doc["coverage"]
    text_path = doc["text_path"]
    quota = doc["per_sub_domain"]
    ids = [r["task_id"] for r in core + cov + text_path]
    if len(ids) != len(set(ids)):
        problems.append("a task appears twice across the tiers")
    blank = [r["task_id"] for r in text_path if not r.get("free_text_fields")]
    if blank:
        problems.append(f"a text_path row has no free-text field: {blank[:3]}")
    slipped = [r["task_id"] for r in core if not is_clean(r)]
    if slipped:
        problems.append(f"core holds a non-clean row: {slipped[:3]}")
    per = collections.Counter(r["sub_domain"] for r in core + cov)
    over = {sd: n for sd, n in per.items() if n > quota}
    if over:
        problems.append(f"a sub_domain exceeds the quota of {quota}: {over}")
    # The spine: a sub-domain present in the bundle and absent here is a hole in
    # the coverage instrument. `under_quota` records a shortfall against the
    # quota; this catches the case where the loop dropped one entirely.
    unreached = doc["gaps"]["sub_domains"]
    if unreached:
        problems.append(f"a sub_domain in the bundle is not represented: {unreached}")
    for r in core + cov + text_path:
        if r["folder"].startswith("/"):
            problems.append(f"{r['task_id']}: folder is an absolute path")
    if doc["achieved"]["n"] != len(core) + len(cov) + len(text_path):
        problems.append("achieved.n disagrees with the emitted rows")
    expected_trust = sum(1 for r in core + cov + text_path if r["score_is_trustworthy"])
    if doc["achieved"]["trustworthy"] != expected_trust:
        problems.append("achieved.trustworthy disagrees with the emitted rows")
    # Every emitted row has to be scorable -- that is the whole reason the pool
    # filter runs first. A row without an answer reaching the file means the
    # filter was bypassed, and the run would silently return fewer results than
    # the subset has rows.
    answerless = [r["task_id"] for r in core + cov + text_path if not has_answer(r)]
    if answerless:
        problems.append(f"an emitted row carries no answer: {answerless[:3]}")
    dropped = {r["task_id"] for r in doc.get("pool_excluded") or []}
    overlap = dropped & set(ids)
    if overlap:
        problems.append(f"a row is both emitted and listed as excluded: {overlap}")
    return problems


def _report(doc: dict[str, Any]) -> None:
    b, a, g = doc["bundle"], doc["achieved"], doc["gaps"]
    dropped = len(doc.get("pool_excluded") or [])
    print(f"bundle       {b['path_name']}  ({b['rows']} rows, "
          f"{b['scorable_pool']} scorable, {b['clean_pool']} clean)")
    print(f"index sha256 {b['index_sha256'][:16]}")
    print(f"dropped      {dropped} rows with no answer, before selection")
    print()
    print(f"core         {len(doc['core'])} rows (clean -- a move here is the pipeline)")
    print(f"coverage     {len(doc['coverage'])} rows (added to reach the quota)")
    print(f"text_path    {len(doc['text_path'])} rows (a ground-truth field is prose)")
    for r in doc["text_path"]:
        print(f"    {r['task_id']:44s} {r['sub_domain']:32s} {r['free_text_fields']}")
    print()
    print(f"achieved     {a['n']} rows | trustworthy {a['trustworthy']} | "
          f"untrustworthy {a['untrustworthy']} (scorable, read the reason)")
    print(f"             by answer_form: {a['by_answer_form']}")
    print(f"             {len(a['sub_domains'])} sub-domains | "
          f"{len(a['viewer_families'])} families | {len(a['domains'])} domains")
    print(f"             ~{a['per_sub_domain']} per sub-domain | "
          f"1-asset {a['single_asset']} multi {a['multi_asset']}")
    print()
    per = collections.Counter(r["sub_domain"] for r in doc["core"] + doc["coverage"])
    for sd in sorted(per):
        rows = [r for r in doc["core"] + doc["coverage"] if r["sub_domain"] == sd]
        ok = sum(1 for r in rows if r["score_is_trustworthy"])
        mark = "" if per[sd] == a["per_sub_domain"] else "  <-- under quota"
        print(f"    {sd:32s} {per[sd]}/{a['per_sub_domain']} "
              f"({ok} clean){mark}")
    if g["under_quota"] or g["viewer_families"]:
        print()
        print(f"GAPS         under quota {g['under_quota']}")
        print(f"             families    {g['viewer_families']}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True, help="the STEM-250 bundle")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    ap.add_argument("--per-sub-domain", type=int, default=PER_SUB_DOMAIN)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true", help="compare, exit 1 on drift")
    args = ap.parse_args(argv)

    if not (args.bundle / "index.jsonl").exists():
        print(f"{args.bundle}/index.jsonl not found", file=sys.stderr)
        return 2
    if args.per_sub_domain < 1:
        print("--per-sub-domain must be at least 1", file=sys.stderr)
        return 2

    doc = build(args.bundle, args.per_sub_domain)
    problems = _self_check(doc)
    _report(doc)
    if problems:
        print()
        for p in problems:
            print(f"SELF-CHECK FAILED: {p}", file=sys.stderr)
        return 1

    emitted = {
        args.out: json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        args.tsv: tsv_text(doc),
    }
    if args.check:
        stale = [
            p
            for p, text in emitted.items()
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        if stale:
            for p in stale:
                print(f"\n{p} is stale; rerun with --write", file=sys.stderr)
            return 1
        return 0
    if args.write:
        for p, text in emitted.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            print(f"\nwrote {p}")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
