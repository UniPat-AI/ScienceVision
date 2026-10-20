"""Stratified sampler for the scale-v1 eval set.

Draws N tasks per domain (default 50) from the three scale-v1
`*_final_tasks.jsonl` sets, stratified by *task family* — the task_id with its
trailing index stripped (e.g. `sv-medi-med_ct_lesion-0444` -> family
`sv-medi-med_ct_lesion`). Without stratification the medical domain (579
ct_lesion vs 260 mri_anatomy) would be dominated by one family and biology's
65-record multi_structure_compare could vanish entirely.

Allocation is proportional to family size with largest-remainder rounding, so
per-family shares match the full set's composition and always sum to exactly N.
Every family with at least one record gets at least one slot.

The sample is reproducible: selection is driven by a seeded `random.Random`,
and the seed plus the realized per-family counts are recorded in the manifest
next to the emitted set jsonl.

Usage:
  python -m scripts.harbor.sample_scale_v1 \
    --tasks-root /home/matianyi/Science-Vision/QA-Gen/tasks \
    --domain medical --domain biology --domain chemistry \
    --per-domain 50 --seed 20260804 \
    --set-name scale-v1-sample50 --out-root QA-Gen/tasks \
    --verify-tree jobs-scale-v1/tasks-cc
"""
from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path


def task_family(task_id: str) -> str:
    """Family = task_id minus its trailing `-<index>` segment.

    Only strips the last segment when it is all digits, so an unindexed id
    (e.g. `sv-biol-bio_multi_structure_compare`) is its own family rather than
    silently losing a meaningful suffix.
    """
    head, _, tail = task_id.rpartition("-")
    return head if head and tail.isdigit() else task_id


def allocate(sizes: dict[str, int], total: int) -> dict[str, int]:
    """Largest-remainder proportional allocation of `total` across families.

    Guarantees: every family gets >=1 (as long as total >= number of families),
    no family gets more than it has, and the allocation sums to exactly
    min(total, sum(sizes)).
    """
    pool = sum(sizes.values())
    total = min(total, pool)
    fams = sorted(sizes)
    if not fams:
        return {}

    exact = {f: sizes[f] * total / pool for f in fams}
    alloc = {f: min(int(exact[f]), sizes[f]) for f in fams}

    # Floor of a small family's exact share can be 0; lift those to 1 first so
    # no family is dropped from the sample outright.
    for f in fams:
        if alloc[f] == 0 and sizes[f] > 0 and sum(alloc.values()) < total:
            alloc[f] = 1

    # Distribute the remainder by descending fractional part, then by
    # descending family size, then by name — fully deterministic.
    def rank(f: str) -> tuple:
        return (-(exact[f] - int(exact[f])), -sizes[f], f)

    while sum(alloc.values()) < total:
        progressed = False
        for f in sorted(fams, key=rank):
            if sum(alloc.values()) >= total:
                break
            if alloc[f] < sizes[f]:
                alloc[f] += 1
                progressed = True
        if not progressed:  # every family saturated
            break

    # Over-allocation can only come from the lift-to-1 step; claw back from the
    # largest allocations that are still above their exact share.
    while sum(alloc.values()) > total:
        f = max(fams, key=lambda f: (alloc[f] - exact[f], sizes[f], f))
        if alloc[f] <= 1:
            break
        alloc[f] -= 1

    return alloc


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def sample_domain(recs: list[dict], per_domain: int, rng: random.Random) -> tuple[list[dict], dict]:
    """Return (sampled_records, stats) for one domain's records."""
    by_fam: dict[str, list[dict]] = collections.defaultdict(list)
    for r in recs:
        by_fam[task_family(r.get("task_id", ""))].append(r)

    sizes = {f: len(v) for f, v in by_fam.items()}
    alloc = allocate(sizes, per_domain)

    picked: list[dict] = []
    for fam in sorted(alloc):
        # Sort by task_id first: dict/file order must not leak into the draw,
        # otherwise the same seed gives different samples across runs.
        pool = sorted(by_fam[fam], key=lambda r: r.get("task_id", ""))
        picked.extend(rng.sample(pool, alloc[fam]))

    picked.sort(key=lambda r: r.get("task_id", ""))
    stats = {
        "population": len(recs),
        "sampled": len(picked),
        "families": {f: {"population": sizes[f], "sampled": alloc.get(f, 0)} for f in sorted(sizes)},
        "v_class": dict(collections.Counter(r.get("v_class", "?") for r in picked)),
        "answer_kind": dict(
            collections.Counter((r.get("answer_schema") or {}).get("kind", "?") for r in picked)
        ),
    }
    return picked, stats


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks-root", type=Path, required=True,
                    help="dir holding scale-v1-<domain>/ScienceVision_scale-v1-<domain>_final_tasks.jsonl")
    ap.add_argument("--domain", action="append", required=True, help="domain name; repeatable")
    ap.add_argument("--per-domain", type=int, default=50)
    ap.add_argument("--seed", type=int, default=20260804)
    ap.add_argument("--set-name", default="scale-v1-sample50")
    ap.add_argument("--out-root", type=Path, default=Path("QA-Gen/tasks"))
    ap.add_argument("--verify-tree", type=Path, action="append", default=[],
                    help="staged task tree(s) that must contain a dir for every "
                         "sampled task_id; repeatable. Non-fatal warning if absent.")
    args = ap.parse_args(argv)

    manifest = {
        "set_name": args.set_name,
        "seed": args.seed,
        "per_domain": args.per_domain,
        "stratify_by": "task_family",
        "domains": {},
    }
    all_recs: list[dict] = []
    for domain in args.domain:
        src = args.tasks_root / f"scale-v1-{domain}" / f"ScienceVision_scale-v1-{domain}_final_tasks.jsonl"
        recs = _load_jsonl(src)
        # One Random per domain, seeded from the base seed + domain name, so
        # adding or reordering domains does not reshuffle the others.
        rng = random.Random(f"{args.seed}:{domain}")
        picked, stats = sample_domain(recs, args.per_domain, rng)
        stats["source"] = str(src)
        manifest["domains"][domain] = stats
        all_recs.extend(picked)
        print(f"{domain}: {stats['sampled']}/{stats['population']}")
        for f, s in stats["families"].items():
            print(f"    {s['sampled']:3d}/{s['population']:<4d}  {f}")

    set_dir = args.out_root / args.set_name
    set_dir.mkdir(parents=True, exist_ok=True)
    set_jsonl = set_dir / f"ScienceVision_{args.set_name}_final_tasks.jsonl"
    set_jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_recs) + "\n")

    ids = [r["task_id"] for r in all_recs]
    manifest["total"] = len(ids)
    manifest["task_ids"] = ids
    dupes = [k for k, v in collections.Counter(ids).items() if v > 1]
    if dupes:
        raise SystemExit(f"duplicate task_ids in sample: {dupes[:5]}")

    for tree in args.verify_tree:
        if not tree.is_dir():
            print(f"WARN verify-tree missing, skipped: {tree}")
            continue
        missing = [t for t in ids if not (tree / t).is_dir()]
        manifest.setdefault("verified_trees", {})[str(tree)] = {
            "missing": missing, "ok": not missing,
        }
        print(f"verify {tree}: {len(ids) - len(missing)}/{len(ids)} present"
              + (f"  MISSING {missing[:5]}" if missing else ""))

    (set_dir / "sample_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"\nwrote {set_jsonl}  ({len(ids)} tasks)")
    print(f"wrote {set_dir / 'sample_manifest.json'}")


if __name__ == "__main__":
    main()
