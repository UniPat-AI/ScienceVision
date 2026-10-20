"""Stage the 7 baseline task trees for a sampled subset of an existing set.

Two routes, because re-staging from scratch is expensive:

* **reuse** (cc / ccmcp / cc2d-*) — the full-set trees under
  ``--reuse-root/tasks-<label>`` are already built (the cc2d ones required an
  offline GL render pass, ~2h for 1567 tasks, and the mcp/GL env to run it).
  For a subset we hardlink the per-task dirs across (``cp -al``): the task dirs
  are write-once, so hardlinks cost no disk and no render time. Falls back to a
  real copy if the source and destination are on different filesystems.

* **fresh** (host) — the sci3d host-run route has no full-set tree yet. It has
  no asset stager (the in-process agent reads the asset from its absolute host
  path), so building it is pure file emission and fast.

Usage:
  python -m scripts.harbor.build_sample_trees \
    --set-jsonl QA-Gen/tasks/scale-v1-sample50/ScienceVision_scale-v1-sample50_final_tasks.jsonl \
    --reuse-root jobs-scale-v1 --out-root jobs-sample50 --asset-root "$(pwd)"
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

# (label, agent_env, n_views) — same shape and order as run_end2end.BASELINES,
# extended with the host route as the 7th baseline.
REUSE_BASELINES = ["cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12"]
FRESH_BASELINES = [("host", "host", None)]


def _hardlink_tree(src: Path, dst: Path) -> None:
    """`cp -al src dst` — recursive hardlink copy, with a plain-copy fallback."""
    if dst.exists():
        shutil.rmtree(dst)
    r = subprocess.run(["cp", "-al", str(src), str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        # Cross-device or a filesystem without hardlink support.
        shutil.copytree(src, dst)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set-jsonl", type=Path, required=True, help="sampled QA-Gen records")
    ap.add_argument("--reuse-root", type=Path, required=True, help="dir holding the full-set tasks-<label> trees")
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--asset-root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)

    recs = [json.loads(l) for l in args.set_jsonl.read_text().splitlines() if l.strip()]
    ids = [r["task_id"] for r in recs]
    print(f"{len(ids)} sampled tasks")

    for label in REUSE_BASELINES:
        src_tree = args.reuse_root / f"tasks-{label}"
        dst_tree = args.out_root / f"tasks-{label}"
        if not src_tree.is_dir():
            raise SystemExit(f"reuse tree missing: {src_tree}")
        missing = [t for t in ids if not (src_tree / t).is_dir()]
        if missing:
            raise SystemExit(f"{label}: {len(missing)} sampled tasks absent from {src_tree}: {missing[:5]}")
        dst_tree.mkdir(parents=True, exist_ok=True)
        for t in ids:
            _hardlink_tree(src_tree / t, dst_tree / t)
        print(f"  {label}: hardlinked {len(ids)} task dirs -> {dst_tree}")

    if FRESH_BASELINES:
        from scripts.harbor.build_harbor_tasks import build_one
        from scripts.harbor.qa_gen_to_tasks import qa_record_to_task

        for label, agent_env, n_views in FRESH_BASELINES:
            dst_tree = args.out_root / f"tasks-{label}"
            dst_tree.mkdir(parents=True, exist_ok=True)
            for rec in recs:
                task = qa_record_to_task(rec, asset_root=args.asset_root)
                if n_views is not None:
                    task["n_views"] = n_views
                build_one(task, dst_tree, containerized=True, agent_env=agent_env)
            print(f"  {label}: built {len(ids)} task dirs (agent_env={agent_env}) -> {dst_tree}")

    # Every tree must hold exactly the sampled set — a leftover dir from an
    # earlier, larger sample would silently inflate the trial count.
    for label in REUSE_BASELINES + [l for l, _, _ in FRESH_BASELINES]:
        tree = args.out_root / f"tasks-{label}"
        got = sorted(p.name for p in tree.iterdir() if p.is_dir())
        extra = sorted(set(got) - set(ids))
        if extra:
            raise SystemExit(f"{label}: {len(extra)} unexpected task dirs in {tree}: {extra[:5]}")
        assert len(got) == len(ids), f"{label}: {len(got)} dirs != {len(ids)} sampled"
    print(f"\nall {len(REUSE_BASELINES) + len(FRESH_BASELINES)} trees hold exactly {len(ids)} tasks")


if __name__ == "__main__":
    main()
