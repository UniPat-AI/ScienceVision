#!/usr/bin/env python3
"""Stage the twelve unscored expert-v2 diagnostic trees.

The 15 expert-v2 records all carry ``ground_truth.status =
needs_expert_annotation`` with ``type = expert_reference``, and several have
empty values, so ``qa_gen_to_tasks`` correctly refuses them as
evaluation-ineligible. They still have evaluation value as trajectories an
expert can review and later sign, so they are staged here by calling the task
mapper and ``build_one`` directly, and every task is marked unscored:

*   ``[metadata]`` gains ``diagnostic_only = true`` and
    ``accuracy_denominator = false``.
*   ``tests/score.py`` is removed and ``tests/test.sh`` replaced with a check
    that the agent produced its answer and trajectory artifacts. It writes
    ``diagnostic_completion.json`` and deliberately never writes
    ``reward.json``, so nothing here can reach a leaderboard number.

Marking is structural rather than conventional: with no scorer present, a
diagnostic task cannot produce a reward even if a later aggregation step
forgets the distinction.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor.build_harbor_tasks import build_one  # noqa: E402
from scripts.harbor.build_v21_ev2_trees import (  # noqa: E402
    STOCK_SCORER,
    id_digest,
    rewrite_from,
    route_env,
    task_dirs,
)
from scripts.harbor.qa_gen_to_tasks import qa_record_to_task  # noqa: E402

ROUTES = ("cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12")

DIAGNOSTIC_TEST = r'''#!/usr/bin/env bash
# Unscored diagnostic check: confirm the agent produced reviewable artifacts.
# Emits no verifier reward whatsoever -- these records have no signed ground
# truth, so there is nothing a score could be computed against.
set -Eeuo pipefail
python - <<'PY'
import hashlib, json
from pathlib import Path

agent = Path("/logs/agent")
answer = agent / "sci3d_answer.json"
trajectory = agent / "sci3d_trajectory.json"
present = {}
for path in (answer, trajectory):
    ok = path.is_file() and path.stat().st_size > 0
    if ok:
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError:
            ok = False
    present[path.name] = ok

out = Path("/logs/verifier")
out.mkdir(parents=True, exist_ok=True)
payload = {
    "schema_version": "sciencevision.diagnostic_completion.v1",
    "accuracy_denominator": False,
    "artifacts_present": present,
    "status": "agent_artifacts_present" if all(present.values()) else "agent_artifacts_incomplete",
}
for path in (answer, trajectory):
    if present[path.name]:
        payload[f"{path.stem}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
(out / "diagnostic_completion.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
PY
'''


def mark_diagnostic(task_dir: Path, scorer_image: str) -> None:
    path = task_dir / "task.toml"
    text = path.read_text()
    if text.count(STOCK_SCORER) != 1:
        raise SystemExit(f"scorer reference mismatch in {path}")
    text = text.replace(STOCK_SCORER, f'docker_image = "{scorer_image}"')
    if text.count("[metadata]\n") != 1:
        raise SystemExit(f"metadata table mismatch in {path}")
    text = text.replace(
        "[metadata]\n",
        "[metadata]\ndiagnostic_only = true\naccuracy_denominator = false\n",
        1,
    )
    path.write_text(text)

    tests = task_dir / "tests"
    score = tests / "score.py"
    if score.exists():
        score.unlink()
    test_sh = tests / "test.sh"
    if test_sh.exists():
        test_sh.unlink()
    test_sh.write_text(DIAGNOSTIC_TEST)
    test_sh.chmod(0o755)


def audit(tree: Path, route: str, family: str, expected_ids: set[str], suffix: str) -> dict:
    dirs = task_dirs(tree)
    found = {p.name for p in dirs}
    if found != expected_ids:
        raise SystemExit(
            f"{tree}: task id mismatch (missing={sorted(expected_ids - found)[:5]} "
            f"extra={sorted(found - expected_ids)[:5]})"
        )
    is_2d = route.startswith("cc2d-")
    n_views = int(route.split("-", 1)[1]) if is_2d else None
    for task_dir in dirs:
        toml = (task_dir / "task.toml").read_text()
        if "diagnostic_only = true" not in toml or "accuracy_denominator = false" not in toml:
            raise SystemExit(f"diagnostic markers missing in {task_dir}")
        if (task_dir / "tests" / "score.py").exists():
            raise SystemExit(f"diagnostic task still carries a scorer: {task_dir}")
        # The invariant that matters is the artifact: a diagnostic test must not
        # name the file Harbor reads a score from, so a later edit cannot quietly
        # start emitting one. Keep that token out of the test's prose too.
        if "reward.json" in (task_dir / "tests" / "test.sh").read_text():
            raise SystemExit(f"diagnostic test names the reward artifact: {task_dir}")
        if is_2d:
            if (task_dir / "environment" / "asset").exists():
                raise SystemExit(f"raw-asset leakage: {task_dir}")
            instruction = (task_dir / "instruction.md").read_text()
            if "no_asset_access" not in instruction or "asset_path" in instruction:
                raise SystemExit(f"cc2d instruction contract violated: {task_dir}")
            pngs = sorted((task_dir / "environment" / "views").glob("*.png"))
            if len(pngs) < n_views:
                raise SystemExit(f"{task_dir}: {len(pngs)} views, expected >= {n_views}")
        else:
            asset_dir = task_dir / "environment" / "asset"
            if not asset_dir.is_dir() or not any(asset_dir.iterdir()):
                raise SystemExit(f"{task_dir}: cc/ccmcp requires a staged asset")
        if family == "claude-code":
            dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
            if ":latest" in dockerfile or "sci3d-codex" in dockerfile:
                raise SystemExit(f"unpinned or wrong-family image in {task_dir}")
            if f":{suffix}" not in dockerfile:
                raise SystemExit(f"Dockerfile not repointed: {task_dir}")
    return {
        "tree": str(tree),
        "route": route,
        "family": family,
        "tasks": len(dirs),
        "task_ids_sha256": id_digest(found),
        "scored": False,
        "status": "pass",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selection", required=True, type=Path)
    ap.add_argument("--ev2-root", required=True, type=Path)
    ap.add_argument("--out-root", required=True, type=Path)
    ap.add_argument("--scorer-image", required=True)
    ap.add_argument("--claude-tag-suffix", required=True)
    ap.add_argument("--tree-prefix", default="diag")
    args = ap.parse_args()

    records = [
        json.loads(line)
        for line in (args.selection / "expert_v2.jsonl").read_text().splitlines()
        if line.strip()
    ]
    expected_ids = {r["task_id"] for r in records}
    selection = json.loads((args.selection / "SELECTION.json").read_text())
    if id_digest(expected_ids) != selection["id_sha256"]["expert_v2"]:
        raise SystemExit("expert-v2 id digest drift")

    args.out_root.mkdir(parents=True, exist_ok=True)
    reports = []
    for route in ROUTES:
        agent_env, n_views = route_env(route, "codex")
        codex_tree = args.out_root / f"{args.tree_prefix}-codex-{route}"
        if codex_tree.exists():
            raise SystemExit(f"refusing to reuse an existing tree: {codex_tree}")
        codex_tree.mkdir(parents=True)
        for record in records:
            task = qa_record_to_task(record, asset_root=args.ev2_root)
            if n_views is not None:
                task["n_views"] = n_views
            build_one(task, codex_tree, containerized=True, agent_env=agent_env)
        for task_dir in task_dirs(codex_tree):
            mark_diagnostic(task_dir, args.scorer_image)
        reports.append(audit(codex_tree, route, "codex", expected_ids, args.claude_tag_suffix))
        print(f"  codex/{route}: {reports[-1]['tasks']} diagnostic tasks", flush=True)

        claude_tree = args.out_root / f"{args.tree_prefix}-claude-code-{route}"
        if claude_tree.exists():
            raise SystemExit(f"refusing to reuse an existing tree: {claude_tree}")
        subprocess.run(["cp", "-al", str(codex_tree), str(claude_tree)], check=True)
        for task_dir in task_dirs(claude_tree):
            rewrite_from(task_dir, args.claude_tag_suffix)
        reports.append(audit(claude_tree, route, "claude-code", expected_ids, args.claude_tag_suffix))
        print(f"  claude-code/{route}: {reports[-1]['tasks']} diagnostic tasks", flush=True)

    summary = {
        "schema_version": "sciencevision.v21_ev2_diag_tree_set.v1",
        "status": "pass",
        "scored": False,
        "reason": (
            "every expert-v2 record carries ground_truth.status="
            "needs_expert_annotation; these trees produce trajectories for expert "
            "review and cannot produce a reward"
        ),
        "tree_count": len(reports),
        "tasks_per_tree": reports[0]["tasks"] if reports else 0,
        "expected_id_sha256": selection["id_sha256"]["expert_v2"],
        "trees": reports,
    }
    out = args.out_root / f"TREE_AUDIT-{args.tree_prefix}.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"{len(reports)} diagnostic trees staged; audit -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
