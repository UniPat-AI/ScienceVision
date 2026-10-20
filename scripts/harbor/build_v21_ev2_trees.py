#!/usr/bin/env python3
"""Stage the twelve task trees for the scale-v2.1 + expert-v2 matrix.

Six baselines (`cc`, `ccmcp`, `cc2d-{1,3,6,12}`) crossed with two agent
harnesses (Codex, Claude Code). Three things here are not obvious:

*   **Two asset roots, one tree.** The scale-v2.1 sample and the expert-v2 set
    resolve against different frozen packages, and ``qa_gen_to_tasks`` takes a
    single ``--asset-root``. Each tree is therefore staged in two passes and the
    per-task directories merged, rather than building a symlink farm whose
    provenance would be ambiguous.

*   **The Claude trees are hardlinked copies of the Codex trees.** The two
    families stage byte-identical inputs and differ only in the ``FROM`` line of
    each task's Dockerfile, so the cc2d render pass is paid once per view count
    instead of twice. The Dockerfile is rewritten by replacing the file, never
    edited in place, because an in-place write would reach through the hardlink
    and corrupt the Codex tree.

*   **Pinned image tags, untouched ``:latest``.** The agent-env table hardcodes
    ``:latest``, which on a shared host is whatever another session last built.
    The Claude ``FROM`` lines are rewritten to the tag built for this run, and
    the audit fails if any ``:latest`` reference survives.

Generation must run where EGL is available: the cc2d trees render their PNGs
offline, and the agent never sees the source geometry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROUTES = ("cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12")
# Match the default verifier reference emitted by build_harbor_tasks so that
# per-run scorer overrides replace exactly one field in each stock task tree.
STOCK_SCORER = (
    'docker_image = "ghcr.io/unipat-ai/sciencevision-scorer@sha256:'
    'd07c59a7c89cc1c8981d20bb8d5a169d577eb2d141153804b86ff866de023062"'
)


def route_env(route: str, family: str) -> tuple[str, int | None]:
    """Map a baseline label onto an agent-env name and view count."""
    if route == "cc":
        return family, None
    if route == "ccmcp":
        return f"{family}-mcp", None
    if route.startswith("cc2d-"):
        return f"{family}-2d", int(route.split("-", 1)[1])
    raise SystemExit(f"unknown route: {route}")


def task_dirs(tree: Path) -> list[Path]:
    """Per-task directories: <tree>/<task_id>/environment/Dockerfile is the marker."""
    return sorted(p.parent.parent for p in tree.glob("*/environment/Dockerfile"))


def id_digest(task_ids) -> str:
    return hashlib.sha256(("\n".join(sorted(task_ids)) + "\n").encode()).hexdigest()


def run_generation(
    python: str,
    runtime: Path,
    jsonl: Path,
    asset_root: Path,
    out: Path,
    agent_env: str,
    n_views: int | None,
    log: Path,
) -> None:
    cmd = [
        python, "-m", "scripts.harbor.qa_gen_to_tasks",
        "--jsonl", str(jsonl),
        "--out", str(out),
        "--asset-root", str(asset_root),
        "--containerized",
        "--agent-env", agent_env,
    ]
    if n_views is not None:
        cmd += ["--n-views", str(n_views)]
    env = {"PYTHONPATH": str(runtime), "PYTHONDONTWRITEBYTECODE": "1"}
    import os

    merged = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_API_KEY", "UNIAPI_KEY_SCIENCEVISION")}
    merged.update(env)
    with log.open("w") as fh:
        proc = subprocess.run(cmd, cwd=runtime, env=merged, stdout=fh, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise SystemExit(f"generation failed ({agent_env}, {jsonl.name}); see {log}")


def patch_scorer(task_dir: Path, scorer_image: str) -> None:
    path = task_dir / "task.toml"
    text = path.read_text()
    if text.count(STOCK_SCORER) != 1:
        raise SystemExit(f"scorer reference mismatch in {path}")
    path.write_text(text.replace(STOCK_SCORER, f'docker_image = "{scorer_image}"'))


def rewrite_from(task_dir: Path, family_suffix: str) -> None:
    """Repoint a hardlinked Dockerfile at the pinned image, by replacement.

    The file is unlinked first: writing in place would mutate the inode shared
    with the Codex tree this copy was hardlinked from.
    """
    path = task_dir / "environment" / "Dockerfile"
    text = path.read_text()
    out_lines = []
    changed = 0
    for line in text.splitlines(keepends=True):
        if line.startswith("FROM sci3d-codex"):
            repo = line.split()[1].split(":")[0].replace("sci3d-codex", "sci3d-claude-code")
            out_lines.append(f"FROM {repo}:{family_suffix}\n")
            changed += 1
        else:
            out_lines.append(line)
    if changed != 1:
        raise SystemExit(f"expected exactly one codex FROM line in {path}, found {changed}")
    path.unlink()
    path.write_text("".join(out_lines))


def audit(tree: Path, route: str, family: str, expected_ids: set[str], scorer_image: str, suffix: str) -> dict:
    dirs = task_dirs(tree)
    found = {p.name for p in dirs}
    # A `*-2d` tree may be short of `expected_ids` exactly by the tasks its
    # `cc2d_exclusions.jsonl` receipt names (expected, fail-closed exclusions
    # recorded by the generator). A missing id with no receipt line is still a
    # hard failure, and a receipt that names a built tree contradicts itself.
    receipt_path = tree / "cc2d_exclusions.jsonl"
    excluded_rows: list[dict] = []
    if receipt_path.is_file():
        for line in receipt_path.read_text().splitlines():
            if line.strip():
                excluded_rows.append(json.loads(line))
    excluded_ids = {str(row.get("task_id", "")) for row in excluded_rows}
    built_and_excluded = excluded_ids & found
    if built_and_excluded:
        raise SystemExit(
            f"{tree}: excluded task has a built tree: {sorted(built_and_excluded)[:5]}"
        )
    missing = expected_ids - excluded_ids - found
    extra = found - expected_ids
    if missing or extra:
        raise SystemExit(
            f"{tree}: task id mismatch (missing={sorted(missing)[:5]} "
            f"extra={sorted(extra)[:5]} excluded={len(excluded_ids)})"
        )

    is_2d = route.startswith("cc2d-")
    n_views = int(route.split("-", 1)[1]) if is_2d else None
    views_total = 0
    for task_dir in dirs:
        asset_dir = task_dir / "environment" / "asset"
        views_dir = task_dir / "environment" / "views"
        instruction = (task_dir / "instruction.md").read_text()
        dockerfile = (task_dir / "environment" / "Dockerfile").read_text()

        if is_2d:
            if asset_dir.exists():
                raise SystemExit(f"raw-asset leakage: {asset_dir}")
            if "no_asset_access" not in instruction:
                raise SystemExit(f"cc2d instruction lacks no_asset_access: {task_dir}")
            if "asset_path" in instruction:
                raise SystemExit(f"cc2d instruction exposes asset_path: {task_dir}")
            pngs = sorted(views_dir.glob("*.png"))
            if len(pngs) < n_views:
                raise SystemExit(f"{task_dir}: {len(pngs)} views, expected >= {n_views}")
            views_total += len(pngs)
            manifest = task_dir / "environment" / "render_manifest.json"
            if not manifest.is_file():
                raise SystemExit(f"missing render manifest: {task_dir}")
        else:
            if not asset_dir.is_dir() or not any(asset_dir.iterdir()):
                raise SystemExit(f"{task_dir}: cc/ccmcp requires a staged asset")

        if family == "claude-code":
            if ":latest" in dockerfile:
                raise SystemExit(f"unpinned image reference in {task_dir}")
            if f":{suffix}" not in dockerfile:
                raise SystemExit(f"Dockerfile not repointed to the pinned tag: {task_dir}")
            if "sci3d-codex" in dockerfile:
                raise SystemExit(f"codex image still referenced in {task_dir}")
        if scorer_image not in (task_dir / "task.toml").read_text():
            raise SystemExit(f"scorer image not pinned in {task_dir}")

    excluded_reasons: dict[str, int] = {}
    for row in excluded_rows:
        reason = str(row.get("failure_reason", "")) or "unknown"
        excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
    return {
        "tree": str(tree),
        "route": route,
        "family": family,
        "tasks": len(dirs),
        "task_ids_sha256": id_digest(found),
        "views_total": views_total,
        "excluded": sorted(excluded_ids),
        "excluded_reasons": excluded_reasons,
        "status": "pass",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime", required=True, type=Path)
    ap.add_argument("--selection", required=True, type=Path)
    ap.add_argument("--v21-root", required=True, type=Path)
    ap.add_argument("--ev2-root", required=True, type=Path)
    ap.add_argument("--out-root", required=True, type=Path)
    ap.add_argument("--log-root", required=True, type=Path)
    ap.add_argument("--scorer-image", required=True)
    ap.add_argument("--claude-tag-suffix", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--routes", default=",".join(ROUTES))
    ap.add_argument(
        "--suites",
        default="scale-v2.1,expert-v2",
        help=(
            "comma-separated suites to stage. expert-v2 records carry "
            "ground_truth.status=needs_expert_annotation, so the generator "
            "refuses them as evaluation-ineligible; stage them only into an "
            "explicitly unscored diagnostic tree."
        ),
    )
    ap.add_argument(
        "--tree-prefix",
        default="tasks",
        help="tree directory prefix, e.g. 'tasks' or 'diag'",
    )
    args = ap.parse_args()

    routes = [r.strip() for r in args.routes.split(",") if r.strip()]
    for route in routes:
        if route not in ROUTES:
            raise SystemExit(f"unknown route: {route}")

    selection = json.loads((args.selection / "SELECTION.json").read_text())
    ids = json.loads((args.selection / "TASK_IDS.json").read_text())
    if id_digest(set(ids["all"])) != selection["id_sha256"]["all"]:
        raise SystemExit("selection id digest does not match TASK_IDS.json")

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    known = {"scale-v2.1", "expert-v2"}
    if not set(suites) <= known:
        raise SystemExit(f"unknown suite(s): {sorted(set(suites) - known)}")

    v21_jsonl = args.selection / "scale_v21_sample.jsonl"
    ev2_jsonl = args.selection / "expert_v2.jsonl"
    passes = [
        ("v21", v21_jsonl, args.v21_root, "scale-v2.1"),
        ("ev2", ev2_jsonl, args.ev2_root, "expert-v2"),
    ]
    passes = [p for p in passes if p[3] in suites]
    expected_ids = set()
    for suite in suites:
        expected_ids |= set(ids["scale_v2.1" if suite == "scale-v2.1" else "expert_v2"])
    args.out_root.mkdir(parents=True, exist_ok=True)
    args.log_root.mkdir(parents=True, exist_ok=True)
    scratch = args.out_root / ".scratch"

    reports = []
    for route in routes:
        codex_tree = args.out_root / f"{args.tree_prefix}-codex-{route}"
        if codex_tree.exists():
            raise SystemExit(f"refusing to reuse an existing tree: {codex_tree}")
        agent_env, n_views = route_env(route, "codex")
        codex_tree.mkdir(parents=True)
        for label, jsonl, asset_root, _suite in passes:
            part = scratch / f"{route}-{label}"
            if part.exists():
                shutil.rmtree(part)
            part.parent.mkdir(parents=True, exist_ok=True)
            run_generation(
                args.python, args.runtime, jsonl, asset_root, part, agent_env, n_views,
                args.log_root / f"generate-codex-{route}-{label}.log",
            )
            for task_dir in task_dirs(part):
                target = codex_tree / task_dir.name
                if target.exists():
                    raise SystemExit(f"duplicate task across suites: {target}")
                shutil.move(str(task_dir), str(target))
            receipt = part / "cc2d_exclusions.jsonl"
            if receipt.is_file():
                # Keep the per-pass receipts with the tree so `audit` can accept
                # exactly the gaps they explain (the claude tree is a hardlink
                # copy and carries the same file).
                with (codex_tree / "cc2d_exclusions.jsonl").open("a") as fh:
                    fh.write(receipt.read_text())
            shutil.rmtree(part, ignore_errors=True)
        for task_dir in task_dirs(codex_tree):
            patch_scorer(task_dir, args.scorer_image)
        reports.append(audit(codex_tree, route, "codex", expected_ids, args.scorer_image, args.claude_tag_suffix))
        print(f"  codex/{route}: {reports[-1]['tasks']} tasks", flush=True)

        claude_tree = args.out_root / f"{args.tree_prefix}-claude-code-{route}"
        if claude_tree.exists():
            raise SystemExit(f"refusing to reuse an existing tree: {claude_tree}")
        # Hardlink the staged inputs; only the Dockerfile is then replaced.
        subprocess.run(["cp", "-al", str(codex_tree), str(claude_tree)], check=True)
        for task_dir in task_dirs(claude_tree):
            rewrite_from(task_dir, args.claude_tag_suffix)
        reports.append(
            audit(claude_tree, route, "claude-code", expected_ids, args.scorer_image, args.claude_tag_suffix)
        )
        print(f"  claude-code/{route}: {reports[-1]['tasks']} tasks", flush=True)

    shutil.rmtree(scratch, ignore_errors=True)
    summary = {
        "schema_version": "sciencevision.v21_ev2_tree_set.v1",
        "status": "pass",
        "tree_count": len(reports),
        "routes": routes,
        "families": ["codex", "claude-code"],
        "tasks_per_tree": reports[0]["tasks"] if reports else 0,
        "suites": suites,
        "tree_prefix": args.tree_prefix,
        "expected_id_sha256": id_digest(expected_ids),
        "scorer_image": args.scorer_image,
        "claude_tag_suffix": args.claude_tag_suffix,
        "trees": reports,
    }
    out = args.out_root / f"TREE_AUDIT-{args.tree_prefix}.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"{len(reports)} trees staged; audit -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
