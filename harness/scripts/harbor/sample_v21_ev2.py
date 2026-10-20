#!/usr/bin/env python3
"""Freeze the scale-v2.1 + expert-v2 evaluation sample.

Builds a deterministic 20-per-domain sample of the scale-v2.1 candidates and
joins it with the full 15-record expert-v2 (EV2_Pilot) set, then writes the
selected task ids and a provenance manifest.

Two properties the six-baseline matrix depends on:

*   **Every selected record must be runnable on all six routes.** cc/ccmcp read
    the raw asset, so it must exist on disk; the cc2d family renders it offline,
    so its modality must reach a renderer. A record that only some routes can
    stage would silently change the denominator per route.
*   **The instruction must not carry route plumbing.** expert-v2 records bake an
    ``Asset: /data/<file>`` line and a fenced ``sci3d`` block into their prose.
    The runtime injects both per route, so the baked copy duplicates them for
    cc/ccmcp and leaks the raw filename to cc2d, which is supposed to see only
    rendered views. They are stripped with the same two patterns the runtime's
    own text route applies, and only ``instruction`` is touched.

Sampling is stratified by ``scientific_action`` with largest-remainder
allocation, and capped at one task per source asset so a domain's 20 questions
do not collapse onto a handful of structures.

    python -m scripts.harbor.sample_v21_ev2 \
        --v21-root /path/to/scale-v2.1-expert-review-package \
        --ev2-jsonl QA-Gen/tasks/EV2_Pilot/ScienceVision_EV2_Pilot_final_tasks.jsonl \
        --ev2-root /path/to/expert-v2-sample165-package \
        --out /path/to/run-root/selection
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

DOMAINS = ("biology", "chemistry", "medical")

# Modalities the cc2d render dispatch can plan views for. A record outside this
# set is not runnable on four of the six routes, so it is not sampled.
RENDERABLE_FORMATS = {
    "mmcif", "cif", "pdb", "xyz", "sdf", "mol", "mol2",
    "nii", "nii.gz", "nifti", "nifti.gz",
    "obj", "ply", "stl", "glb", "gltf", "off", "las", "laz",
}

# The runtime's own instruction-normalizing patterns, applied to expert-v2 prose.
SCRUB_PATTERNS = (r"```sci3d.*?```", r"(?im)^\s*Asset:\s*.*$")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def id_digest(task_ids: Iterable[str]) -> str:
    """Order-independent digest of a task-id set, for cross-run comparison."""
    return sha256_text("\n".join(sorted(task_ids)) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def asset_format(asset: dict[str, Any]) -> str:
    """Mirror qa_gen_to_tasks._infer_asset_format so eligibility matches staging."""
    declared = asset.get("format") or asset.get("asset_format")
    if declared:
        return str(declared)
    name = Path(str(asset.get("repo_path") or asset.get("path") or "")).name.lower()
    if name.endswith(".nii.gz"):
        return "nii.gz"
    return Path(name).suffix.lstrip(".") or "obj"


def scrub_instruction(text: str) -> str:
    out = text
    for pattern in SCRUB_PATTERNS:
        out = re.sub(pattern, "", out, flags=re.DOTALL)
    # Collapse the blank lines the removals leave behind.
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def eligibility(rec: dict[str, Any], asset_root: Path) -> str | None:
    """Return a rejection reason, or None when the record is runnable everywhere."""
    if not rec.get("ground_truth"):
        return "no_ground_truth"
    asset = rec.get("asset") or {}
    repo_path = asset.get("repo_path")
    if not repo_path:
        return "no_asset_repo_path"
    if not (asset_root / repo_path).is_file():
        return "asset_missing_on_disk"
    fmt = asset_format(asset)
    if fmt not in RENDERABLE_FORMATS:
        return f"unrenderable_format:{fmt}"
    mask_repo_path = asset.get("mask_repo_path")
    if mask_repo_path and not (asset_root / mask_repo_path).is_file():
        return "mask_missing_on_disk"
    for extra in rec.get("assets") or []:
        extra_path = (extra or {}).get("repo_path")
        if extra_path and not (asset_root / extra_path).is_file():
            return "bound_asset_missing_on_disk"
    return None


def allocate(strata: dict[str, list], total: int) -> dict[str, int]:
    """Largest-remainder proportional allocation of *total* across strata."""
    pool = sum(len(v) for v in strata.values())
    if pool <= total:
        return {k: len(v) for k, v in strata.items()}
    exact = {k: len(v) * total / pool for k, v in strata.items()}
    floors = {k: int(v) for k, v in exact.items()}
    short = total - sum(floors.values())
    order = sorted(strata, key=lambda k: (-(exact[k] - floors[k]), k))
    for key in order[:short]:
        floors[key] += 1
    # A stratum can never be allocated more members than it has.
    for key in sorted(strata):
        floors[key] = min(floors[key], len(strata[key]))
    while sum(floors.values()) < total:
        for key in sorted(strata, key=lambda k: (-len(strata[k]), k)):
            if floors[key] < len(strata[key]):
                floors[key] += 1
                if sum(floors.values()) == total:
                    break
        else:
            break
    return floors


def sample_domain(
    records: list[dict[str, Any]], count: int, seed: int, domain: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stratified, asset-deduplicated sample of *count* records."""
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in sorted(records, key=lambda r: r["task_id"]):
        strata[rec.get("scientific_action") or "unspecified"].append(rec)
    quota = allocate(strata, count)

    rng = random.Random(f"{seed}:{domain}")
    picked: list[dict[str, Any]] = []
    used_assets: set[str] = set()
    for action in sorted(strata):
        want = quota.get(action, 0)
        pool = list(strata[action])
        rng.shuffle(pool)
        for rec in pool:
            if len(picked) >= count or want <= 0:
                break
            key = (rec.get("asset") or {}).get("repo_path") or rec["task_id"]
            if key in used_assets:
                continue
            used_assets.add(key)
            picked.append(rec)
            want -= 1
    # Backfill if an asset cap starved a stratum, still honouring dedup.
    if len(picked) < count:
        chosen = {r["task_id"] for r in picked}
        backfill = [r for r in sorted(records, key=lambda r: r["task_id"]) if r["task_id"] not in chosen]
        rng.shuffle(backfill)
        for rec in backfill:
            if len(picked) >= count:
                break
            key = (rec.get("asset") or {}).get("repo_path") or rec["task_id"]
            if key in used_assets:
                continue
            used_assets.add(key)
            picked.append(rec)
    picked.sort(key=lambda r: r["task_id"])
    stats = {
        "requested": count,
        "selected": len(picked),
        "pool": len(records),
        "strata_pool": {k: len(v) for k, v in sorted(strata.items())},
        "strata_quota": dict(sorted(quota.items())),
        "strata_selected": dict(
            sorted(Counter(r.get("scientific_action") or "unspecified" for r in picked).items())
        ),
        "distinct_assets": len({(r.get("asset") or {}).get("repo_path") for r in picked}),
    }
    return picked, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v21-root", required=True, type=Path)
    ap.add_argument("--ev2-jsonl", required=True, type=Path)
    ap.add_argument("--ev2-root", required=True, type=Path)
    ap.add_argument("--per-domain", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260813)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument(
        "--overlay-jsonl",
        type=Path,
        help=(
            "derived candidate records to substitute for the selected ids after "
            "sampling. Medical MRI tasks with multi-label masks and no mask_id "
            "carry no resolvable ROI plan, so the cc2d renderer fails closed on "
            "them; the overlay supplies the foreground policy. Substitution is "
            "gated on the answer-bearing fields being byte-identical, and is "
            "applied for every route so the whole matrix consumes one input."
        ),
    )
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    v21_src = args.v21_root / "sources" / "scale-v2.1"
    qc_path = args.v21_root / "qc" / "scale-v2.1-round2-final-20260810" / "task_classifications.jsonl"
    qc = {r["task_id"]: r for r in read_jsonl(qc_path)}

    # Quarantined records are not candidates; exclude by id as a belt-and-braces
    # check even though the candidate files should already omit them.
    quarantined: set[str] = set()
    for path in sorted((v21_src / "quarantine").glob("*.jsonl")):
        quarantined.update(r["task_id"] for r in read_jsonl(path))

    v21_selected: list[dict[str, Any]] = []
    per_domain_stats: dict[str, Any] = {}
    rejections: dict[str, Counter] = {}
    for domain in DOMAINS:
        path = v21_src / f"ScienceVision_scale-v2.1-{domain}_candidate_tasks.jsonl"
        records = read_jsonl(path)
        reject = Counter()
        eligible = []
        for rec in records:
            if rec["task_id"] in quarantined:
                reject["quarantined"] += 1
                continue
            if rec.get("disposition") not in (None, "candidate"):
                reject[f"disposition:{rec.get('disposition')}"] += 1
                continue
            entry = qc.get(rec["task_id"]) or {}
            if entry.get("materialization_status") not in (None, "preflight_pass"):
                reject[f"materialization:{entry.get('materialization_status')}"] += 1
                continue
            reason = eligibility(rec, args.v21_root)
            if reason:
                reject[reason] += 1
                continue
            eligible.append(rec)
        picked, stats = sample_domain(eligible, args.per_domain, args.seed, domain)
        if len(picked) < args.per_domain:
            raise SystemExit(
                f"{domain}: only {len(picked)} of {args.per_domain} selectable "
                f"from {len(eligible)} eligible ({len(records)} candidates)"
            )
        stats["eligible"] = len(eligible)
        stats["rejected"] = dict(sorted(reject.items()))
        # QC decision is reported, never used as a filter: for scale-v2.1 the
        # whole of biology and chemistry is 'review', so filtering on it would
        # leave those two domains empty.
        stats["qc_decision"] = dict(
            sorted(Counter((qc.get(r["task_id"]) or {}).get("decision") for r in picked).items())
        )
        per_domain_stats[domain] = stats
        rejections[domain] = reject
        v21_selected.extend(picked)

    overlay_report: dict[str, Any] = {"applied": False}
    if args.overlay_jsonl:
        derived = {r["task_id"]: r for r in read_jsonl(args.overlay_jsonl)}
        answer_fields = (
            "instruction", "ground_truth", "answer_schema",
            "scorer", "scorer_config", "quality_gates",
        )
        substituted = []
        gained_policy = []
        for index, rec in enumerate(v21_selected):
            replacement = derived.get(rec["task_id"])
            if replacement is None:
                raise SystemExit(f"overlay lacks selected id {rec['task_id']}")
            for field in answer_fields:
                if json.dumps(rec.get(field), sort_keys=True) != json.dumps(
                    replacement.get(field), sort_keys=True
                ):
                    raise SystemExit(
                        f"overlay changes {field} for {rec['task_id']}; refusing to substitute"
                    )
            if (replacement.get("render_contract") or {}).get("foreground_policy"):
                gained_policy.append(rec["task_id"])
            v21_selected[index] = replacement
            substituted.append(rec["task_id"])
        overlay_report = {
            "applied": True,
            "path": str(args.overlay_jsonl),
            "sha256": sha256_file(args.overlay_jsonl),
            "substituted": len(substituted),
            "gained_foreground_policy": sorted(gained_policy),
            "verified_identical_fields": list(answer_fields),
            "applied_to_routes": "all",
        }

    ev2_records = read_jsonl(args.ev2_jsonl)
    ev2_scrubbed: list[dict[str, Any]] = []
    scrub_report: list[dict[str, Any]] = []
    for rec in ev2_records:
        reason = eligibility(rec, args.ev2_root)
        if reason:
            raise SystemExit(f"expert-v2 {rec['task_id']} is not runnable: {reason}")
        before = rec.get("instruction") or ""
        after = scrub_instruction(before)
        updated = dict(rec)
        updated["instruction"] = after
        ev2_scrubbed.append(updated)
        if after != before:
            scrub_report.append(
                {
                    "task_id": rec["task_id"],
                    "instruction_sha256_before": sha256_text(before),
                    "instruction_sha256_after": sha256_text(after),
                }
            )
    ev2_scrubbed.sort(key=lambda r: r["task_id"])

    # Residual identifier mentions survive scrubbing when the prose itself names
    # the structure. Reported, not repaired: rewriting expert prose would change
    # the question.
    residual: list[dict[str, Any]] = []
    for rec in ev2_scrubbed:
        stem = Path(str((rec.get("asset") or {}).get("repo_path") or "")).stem
        if stem and stem.lower() in (rec["instruction"] or "").lower():
            residual.append({"task_id": rec["task_id"], "identifier": stem})

    v21_path = out / "scale_v21_sample.jsonl"
    ev2_path = out / "expert_v2.jsonl"
    all_path = out / "all_tasks.jsonl"
    for path, rows in ((v21_path, v21_selected), (ev2_path, ev2_scrubbed)):
        with path.open("w") as fh:
            for rec in rows:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
    combined = v21_selected + ev2_scrubbed
    with all_path.open("w") as fh:
        for rec in combined:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    v21_ids = [r["task_id"] for r in v21_selected]
    ev2_ids = [r["task_id"] for r in ev2_scrubbed]
    all_ids = v21_ids + ev2_ids
    if len(set(all_ids)) != len(all_ids):
        raise SystemExit("duplicate task ids across the two suites")

    (out / "TASK_IDS.json").write_text(
        json.dumps(
            {
                "all": sorted(all_ids),
                "scale_v2.1": sorted(v21_ids),
                "expert_v2": sorted(ev2_ids),
                "by_domain": {
                    d: sorted(r["task_id"] for r in v21_selected if r.get("domain") == d)
                    for d in DOMAINS
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with (out / "TASK_IDS.tsv").open("w") as fh:
        fh.write("suite\tdomain\ttask_id\n")
        for rec in v21_selected:
            fh.write(f"scale-v2.1\t{rec.get('domain','')}\t{rec['task_id']}\n")
        for rec in ev2_scrubbed:
            fh.write(f"expert-v2\t{rec.get('domain','')}\t{rec['task_id']}\n")

    manifest = {
        "schema_version": "sciencevision.v21_ev2_selection.v1",
        "seed": args.seed,
        "per_domain": args.per_domain,
        "counts": {
            "all": len(all_ids),
            "scale_v2.1": len(v21_ids),
            "expert_v2": len(ev2_ids),
        },
        "id_sha256": {
            "all": id_digest(all_ids),
            "scale_v2.1": id_digest(v21_ids),
            "expert_v2": id_digest(ev2_ids),
        },
        "sources": {
            "v21_root": str(args.v21_root),
            "v21_candidates": {
                domain: {
                    "path": str(v21_src / f"ScienceVision_scale-v2.1-{domain}_candidate_tasks.jsonl"),
                    "sha256": sha256_file(
                        v21_src / f"ScienceVision_scale-v2.1-{domain}_candidate_tasks.jsonl"
                    ),
                }
                for domain in DOMAINS
            },
            "qc_classifications": {"path": str(qc_path), "sha256": sha256_file(qc_path)},
            "ev2_jsonl": {"path": str(args.ev2_jsonl), "sha256": sha256_file(args.ev2_jsonl)},
            "ev2_root": str(args.ev2_root),
        },
        "outputs": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in (
                ("scale_v21_sample.jsonl", v21_path),
                ("expert_v2.jsonl", ev2_path),
                ("all_tasks.jsonl", all_path),
            )
        },
        "per_domain": per_domain_stats,
        "instruction_scrub": {
            "patterns": list(SCRUB_PATTERNS),
            "only_field_changed": "instruction",
            "count": len(scrub_report),
            "records": scrub_report,
            "residual_identifier_mentions": residual,
        },
        "mri_foreground_overlay": overlay_report,
        "asset_roots": {
            "scale-v2.1": str(args.v21_root),
            "expert-v2": str(args.ev2_root),
        },
        "notes": [
            "QC decision is recorded, not filtered on: all 113 biology and all "
            "105 chemistry scale-v2.1 candidates are 'review'/needs_fix, so a "
            "pass-only filter would leave both domains empty. The open findings "
            "are leakage/contract warnings (asset_identifier_shortcut, "
            "package_compute_shortcut, shortcut_control_unenforced) and "
            "template_reuse, not incorrect ground truth.",
            "The two asset roots differ, so task trees are staged in two passes "
            "and merged per route.",
        ],
    }
    (out / "SELECTION.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"selection written to {out}")
    print(f"  scale-v2.1 : {len(v21_ids)} ({args.per_domain} per domain)")
    print(f"  expert-v2  : {len(ev2_ids)}")
    print(f"  total      : {len(all_ids)}  id_sha256={manifest['id_sha256']['all'][:16]}")
    for domain in DOMAINS:
        stats = per_domain_stats[domain]
        print(
            f"  {domain:10} eligible={stats['eligible']:4}/{stats['pool']:4} "
            f"strata={stats['strata_selected']} qc={stats['qc_decision']}"
        )
    if residual:
        print(f"  residual identifier mentions: {len(residual)} (reported, not repaired)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
