"""End-to-end baseline matrix driver.

Given a task list of {task_id, viewer_link, question, ground_truth, ...}, run
all 6 baselines (cc / ccmcp / cc2d-{1,3,6,12}) over it and land a leaderboard.

Pipeline (each step reuses existing modules unchanged):
  1. resolve viewer_link rows -> QA-Gen records, written as a set jsonl
  2. build 6 task-dir trees via qa_gen_to_tasks (per-baseline agent-env/n_views)
  3. `harbor run` the 6 trees concurrently (--max-parallel), each to its own jobdir
  4. collect_qagen_results across the 6 jobs -> runs_leaderboard.{json,md}
  5. plot_leaderboard -> charts/

Usage:
  CODEX_MODEL=gpt-5.6-terra \
  python -m scripts.harbor.run_end2end \
    --tasks e2e_tasks.jsonl --set-name MyEval --asset-root "$(pwd)" \
    --out-root QA-Gen/tasks --jobs-root jobs-e2e \
    --max-parallel 2 [--seed-from-link] [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from scripts.harbor._codex_runtime import (
    MCP_CONFIG,
    CodexRuntimeError,
    baseline_exposes_mcp,
    record_codex_runtime,
    resolve_baseline_agent,
    resolve_codex_runtime,
)
from scripts.harbor._e2e_resolve import resolve_task_list

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Prefer the operator-selected Harbor on PATH. Fall back to a sibling binary
# only for legacy virtual environments that do not expose Harbor on PATH.
def _resolve_harbor_bin() -> str:
    override = os.environ.get("HARBOR", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("HARBOR must name an absolute executable file")
        return str(path)
    on_path = shutil.which("harbor")
    if on_path:
        return on_path
    # NOT .resolve(): the venv's bin/python is a symlink into the uv python
    # store; resolving it would leave the venv bin dir where `harbor` lives.
    cand = Path(sys.executable).parent / "harbor"
    return str(cand) if cand.is_file() and os.access(cand, os.X_OK) else "harbor"


_HARBOR_BIN = _resolve_harbor_bin()

# Baseline matrix, in fixed order: (label, agent_env, n_views|None). The
# label doubles as the "harness" tag consumed by collect_qagen_results.
BASELINES = [
    ("cc", "codex", None),
    ("ccmcp", "codex-mcp", None),
    ("cc2d-1", "codex-2d", 1),
    ("cc2d-3", "codex-2d", 3),
    ("cc2d-6", "codex-2d", 6),
    ("cc2d-12", "codex-2d", 12),
]

# T0 irreducibility probe. Not part of the leaderboard matrix: it answers "could
# this task have been answered without perceiving the 3D content?" rather than
# "how well does this route do?". Only the text half runs here — the file half
# reuses the matrix's own job-cc, because a separately-launched cc run with
# different timeouts would make the labels incomparable to the very numbers they
# exist to qualify. See scripts/harbor/probe_irreducibility.py.
PROBE_BASELINES = [
    ("codex-text", "codex-text", None),
]

# The tools-only twin of ccmcp (matrix label 3d-mcp): the same worker-backed
# HTTP MCP route with Codex's shell tool disabled, so every observation must
# come through MCP tools. Not part of the leaderboard matrix: the freshness
# check is scoped per matrix, so it lands beside a finished six-baseline run
# and is read against that run's own job-ccmcp (same trees, same worker).
NOSHELL_BASELINES = [
    ("3d-mcp", "codex-mcp", None),
]

# The matrix is threaded as a parameter rather than read off the module global,
# so a probe run inherits every knob the leaderboard run used — task-tree
# construction, --ae, timeout multipliers, the freshness check — instead of
# re-deriving them in a second code path that can drift.
MATRICES: dict[str, list[tuple[str, str, int | None]]] = {
    "six": BASELINES,
    "t0-probe": PROBE_BASELINES,
    "3d-mcp": NOSHELL_BASELINES,
}

_VERIFIER = "harbor_adapters.sci3d.verifier:Sci3dVerifier"
_SAFE_HARBOR_EXTRA = {
    "--timeout-multiplier",
    "--agent-timeout-multiplier",
    "--verifier-timeout-multiplier",
    "--agent-setup-timeout-multiplier",
    "--environment-build-timeout-multiplier",
}


def _validate_harbor_extra(tokens: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Allow tuning timeouts without permitting baseline-semantic overrides."""
    validated: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if "=" in token:
            flag, value = token.split("=", 1)
            consumed = (token,)
            i += 1
        else:
            flag = token
            if i + 1 >= len(tokens):
                raise ValueError(f"{flag!r} requires a numeric value")
            value = tokens[i + 1]
            consumed = (flag, value)
            i += 2
        if flag not in _SAFE_HARBOR_EXTRA:
            raise ValueError(
                f"unsafe --harbor-extra option {flag!r}; only timeout multipliers are allowed"
            )
        try:
            numeric = float(value)
        except ValueError as exc:
            raise ValueError(f"{flag} requires a positive numeric value") from exc
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{flag} requires a positive finite value")
        validated.extend(consumed)
    return tuple(validated)


def build_run_specs(
    jobdirs: dict[str, str],
    model: str,
    baselines: list[tuple[str, str, int | None]] | None = None,
) -> list[str]:
    """Build the `harness:model:jobdir` specs consumed by collect --run."""
    matrix = BASELINES if baselines is None else baselines
    return [f"{label}:{model}:{jobdirs[label]}" for label, _, _ in matrix if label in jobdirs]


def _require_fresh_jobs_root(
    jobs_root: Path,
    baselines: list[tuple[str, str, int | None]] | None = None,
) -> None:
    reserved = [
        jobs_root / f"{prefix}-{label}"
        for label, _, _ in (BASELINES if baselines is None else baselines)
        for prefix in ("tasks", "job")
    ]
    existing = [path for path in reserved if path.exists()]
    if existing:
        shown = ", ".join(str(path) for path in existing[:3])
        suffix = " ..." if len(existing) > 3 else ""
        raise SystemExit(
            "jobs root contains prior baseline artifacts; use a fresh --jobs-root "
            f"(refusing to modify: {shown}{suffix})"
        )


def _require_exact_task_tree(
    tasks_dir: Path,
    expected_ids: set[str],
    *,
    excluded_ids: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Every eligible id must exist, minus exactly the receipt's exclusions.

    A `*-2d` tree may be short of `expected_ids` by the tasks its
    `cc2d_exclusions.jsonl` receipt names (expected, fail-closed exclusions).
    A missing id with no receipt is still a hard failure, and a receipt that
    names a built tree contradicts itself.
    """
    actual_ids = {path.name for path in tasks_dir.iterdir() if path.is_dir()}
    built_and_excluded = set(excluded_ids) & actual_ids
    if built_and_excluded:
        raise RuntimeError(
            f"excluded task has a built tree for {tasks_dir}: "
            f"{sorted(built_and_excluded)[:5]}"
        )
    if actual_ids == set(expected_ids) - set(excluded_ids):
        return
    missing = sorted(set(expected_ids) - set(excluded_ids) - actual_ids)
    unexpected = sorted(actual_ids - set(expected_ids))
    raise RuntimeError(
        f"generated task tree mismatch for {tasks_dir}: "
        f"missing={missing[:5]} unexpected={unexpected[:5]}"
    )


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _harbor_run(
    baseline: str, tasks_dir: Path, jobdir: Path, model: str, ae: list[str], runner,
    n_concurrent: int | None = None,
    harbor_extra: tuple = (),
) -> None:
    harbor_extra = _validate_harbor_extra(harbor_extra)
    agent = resolve_baseline_agent(baseline)
    cmd = [_HARBOR_BIN, "run", "--path", str(tasks_dir), "--agent", agent,
           "--model", model]
    if baseline_exposes_mcp(baseline):
        cmd += ["--mcp-config", str(MCP_CONFIG)]
    cmd += ["--verifier-import-path", _VERIFIER, "--env", "docker", "-o", str(jobdir)]
    # Raw passthrough for harbor flags this driver does not model explicitly
    # (e.g. --agent-setup-timeout-multiplier / --agent-timeout-multiplier).
    # Placed before -n / --ae.
    cmd += list(harbor_extra)
    if n_concurrent is not None:
        cmd += ["-n", str(n_concurrent)]
    for kv in ae:
        cmd += ["--ae", kv]
    runner(cmd)


def _run_all(
    tasks_dirs: dict[str, Path],
    jobdirs: dict[str, str],
    model: str,
    ae: list[str],
    max_workers: int,
    runner,
    n_concurrent: int | None = None,
    harbor_extra: tuple = (),
    baselines: list[tuple[str, str, int | None]] | None = None,
) -> None:
    """Fan out the matrix's harbor-run calls concurrently (bounded by max_workers).

    Every baseline's runner is invoked regardless of another baseline's
    failure: all futures are submitted up front and waited on (ALL_COMPLETED)
    before any result is inspected, so one failing run does not silently drop
    the other 5's execution. The first exception encountered (in BASELINES
    order) is then re-raised so the failure is surfaced to the caller rather
    than swallowed.

    `n_concurrent` is the per-baseline trial concurrency (harbor `-n`). The
    total containers in flight is `max_workers * n_concurrent`, so for a fixed
    trial budget run baselines sequentially (max_workers=1) with the full
    budget as n_concurrent.
    """
    harbor_extra = _validate_harbor_extra(harbor_extra)
    matrix = BASELINES if baselines is None else baselines
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {
            label: ex.submit(
                _harbor_run, label, tasks_dirs[label], Path(jobdirs[label]), model, ae, runner,
                n_concurrent, harbor_extra,
            )
            for label, _, _ in matrix
        }
        wait(futs.values(), return_when=ALL_COMPLETED)
        for label, _, _ in matrix:
            futs[label].result()  # re-raises the first exception, if any


def _build_collect_argv(run_specs: list[str], out_root: Path) -> list[str]:
    """Flags for `python -m scripts.harbor.collect_qagen_results` (verified
    against that module's argparse: --tasks-root, repeatable --run)."""
    argv = ["--tasks-root", str(out_root)]
    for s in run_specs:
        argv += ["--run", s]
    return argv


def _build_plot_argv(out_root: Path) -> list[str]:
    """Flags for `python -m scripts.harbor.plot_leaderboard` (verified
    against that module's argparse: --leaderboard, --out-dir)."""
    return [
        "--leaderboard", str(out_root / "runs_leaderboard.json"),
        "--out-dir", str(out_root / "charts"),
    ]


def _collect(run_specs: list[str], out_root: Path) -> None:
    cmd = [sys.executable, "-m", "scripts.harbor.collect_qagen_results"]
    cmd += _build_collect_argv(run_specs, out_root)
    subprocess.run(cmd, check=True)


def _plot(out_root: Path) -> None:
    cmd = [sys.executable, "-m", "scripts.harbor.plot_leaderboard"]
    cmd += _build_plot_argv(out_root)
    subprocess.run(cmd, check=True)


def _default_runner(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--tasks", type=Path, required=True, action="append",
        help="task-list jsonl; repeatable to concatenate several files "
             "(e.g. the 3 scale-v1 domain sets). Rows are viewer_link "
             "end2end rows unless --pre-resolved is given.",
    )
    ap.add_argument(
        "--pre-resolved", action="store_true",
        help="the --tasks jsonls are already QA-Gen records (nested "
             "asset.{path,format}, no viewer_link) -- load them directly and "
             "skip resolve_task_list. Use for scale-v1 *_final_tasks.jsonl.",
    )
    ap.add_argument(
        "--limit", type=int, default=None,
        help="only take the first N records (after concatenation) -- for "
             "smoke/dry-run validation of the full pipeline on a small slice.",
    )
    ap.add_argument("--set-name", required=True, help="QA-Gen set name (dir + jsonl stem)")
    ap.add_argument("--asset-root", type=Path, default=_REPO_ROOT)
    ap.add_argument("--out-root", type=Path, default=Path("QA-Gen/tasks"))
    ap.add_argument("--jobs-root", type=Path, default=Path("jobs-e2e"))
    ap.add_argument("--max-parallel", type=int, default=2, help="baselines run in parallel (ThreadPoolExecutor over the 6 harbor-run calls)")
    ap.add_argument(
        "--n-concurrent", type=int, default=None,
        help="per-baseline trial concurrency (harbor -n). Total containers in "
             "flight = max-parallel * n-concurrent; for a fixed trial budget "
             "set --max-parallel 1 and --n-concurrent <budget>.",
    )
    ap.add_argument(
        "--matrix", choices=sorted(MATRICES), default="six",
        help="which route matrix to run. 'six' is the leaderboard matrix "
             "(cc/ccmcp/cc2d-1/3/6/12). 't0-probe' runs only the codex-text "
             "irreducibility probe; pair its job dir with the six-baseline run's "
             "own job-cc when calling scripts.harbor.probe_irreducibility, and "
             "pass the identical --ae / --harbor-extra / --n-concurrent so the "
             "labels stay comparable to the numbers they qualify. '3d-mcp' runs "
             "only the tools-only twin of ccmcp (same task trees and worker; "
             "Codex's shell tool disabled): land it beside a finished "
             "six-baseline run and read it against that run's own job-ccmcp.",
    )
    ap.add_argument("--seed-from-link", action="store_true")
    ap.add_argument("--ae", action="append", default=[], help="extra --ae KEY=VALUE for harbor run")
    ap.add_argument(
        "--harbor-extra", action="append", default=[],
        help="timeout-multiplier token appended to every harbor-run argv "
             "(repeatable); agent/model/MCP/environment/output overrides are rejected, e.g. "
             "--harbor-extra=--agent-setup-timeout-multiplier --harbor-extra=3.0",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="still build the 6 task-dir trees on disk and print the run "
             "specs, but stop before the harbor-run/collect/plot subprocess "
             "steps (no docker/harbor invoked)",
    )
    args = ap.parse_args(argv)
    try:
        harbor_extra = _validate_harbor_extra(args.harbor_extra)
    except ValueError as exc:
        ap.error(str(exc))

    try:
        runtime = resolve_codex_runtime()
    except CodexRuntimeError as exc:
        ap.error(str(exc))
    # 1. load one-or-more task-list jsonls -> QA-Gen records (indexable by
    #    collect via auto-discovery). Pre-resolved sets (scale-v1) are already
    #    records; end2end viewer_link rows go through resolve_task_list.
    if args.pre_resolved:
        recs = []
        for tp in args.tasks:
            recs.extend(_load_jsonl(tp))
    else:
        rows = []
        for tp in args.tasks:
            rows.extend(_load_jsonl(tp))
        recs = resolve_task_list(rows, asset_root=args.asset_root, seed_from_link=args.seed_from_link)
    if args.limit is not None:
        recs = recs[: args.limit]

    from scripts.harbor.qa_gen_to_tasks import qa_record_to_task
    from scripts.harbor.build_harbor_tasks import (
        CC2D_EXPECTED_EXCLUSION_CODES,
        RenderVisibilityError,
        build_one,
    )

    prepared_tasks = [qa_record_to_task(rec, asset_root=args.asset_root) for rec in recs]
    task_ids = [task["task_id"] for task in prepared_tasks]
    invalid_ids = [task_id for task_id in task_ids if Path(task_id).name != task_id]
    if invalid_ids:
        raise ValueError(f"task_id must be one path component: {invalid_ids[0]!r}")
    duplicate_ids = sorted({task_id for task_id in task_ids if task_ids.count(task_id) > 1})
    if duplicate_ids:
        raise ValueError(f"duplicate task_id values are not allowed: {duplicate_ids[:5]}")
    eligible_tasks = [task for task in prepared_tasks if task.get("evaluation_eligible")]
    if not eligible_tasks:
        raise SystemExit(
            "no records have validated deterministic GT; refusing to launch an empty/formally invalid eval"
        )
    expected_ids = {task["task_id"] for task in eligible_tasks}

    matrix = MATRICES[args.matrix]
    _require_fresh_jobs_root(args.jobs_root, matrix)
    runtime_record = record_codex_runtime(args.jobs_root, runtime)
    print(
        f"Codex runtime: model={runtime.model} auth={runtime.auth_mode} "
        f"base_url={runtime.base_url_mode} record={runtime_record}",
        flush=True,
    )

    set_dir = args.out_root / args.set_name
    set_dir.mkdir(parents=True, exist_ok=True)
    set_jsonl = set_dir / f"{args.set_name}_final_tasks.jsonl"
    set_jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")

    # 2. build 6 task-dir trees
    tasks_dirs: dict[str, Path] = {}
    for label, agent_env, n_views in matrix:
        out = args.jobs_root / f"tasks-{label}"
        out.mkdir(parents=True, exist_ok=True)
        cc2d_excluded: dict[str, str] = {}
        for prepared in eligible_tasks:
            task = copy.deepcopy(prepared)
            if n_views is not None:
                task["n_views"] = n_views
            try:
                build_one(task, out, containerized=True, agent_env=agent_env)
            except RenderVisibilityError as exc:
                # Expected cc2d exclusions (unsupported modality, hidden ROI,
                # missing CAD kernel) become receipt lines; real renderer faults
                # still abort the run.
                if (not agent_env.endswith("-2d")
                        or str(exc) not in CC2D_EXPECTED_EXCLUSION_CODES):
                    raise
                cc2d_excluded[str(task["task_id"])] = str(exc)
        if cc2d_excluded:
            (out / "cc2d_exclusions.jsonl").write_text(
                "".join(json.dumps({"task_id": tid, "agent_env": agent_env,
                                    "failure_reason": reason}, sort_keys=True) + "\n"
                        for tid, reason in sorted(cc2d_excluded.items()))
            )
        _require_exact_task_tree(out, expected_ids, excluded_ids=set(cc2d_excluded))
        tasks_dirs[label] = out

    eligible_count = len(eligible_tasks)

    jobdirs = {label: str(args.jobs_root / f"job-{label}") for label, _, _ in matrix}
    ae = [*runtime.agent_env, *args.ae]
    run_specs = build_run_specs(jobdirs, runtime.model, matrix)
    if args.dry_run:
        print(f"DRY RUN — {eligible_count}/{len(recs)} records have deterministic GT; task dirs:")
        for label, d in tasks_dirs.items():
            print(f"  {label}: {d}")
        print("run specs:")
        for s in run_specs:
            print(f"  {s}")
        return

    # 3. run harbor concurrently
    _run_all(
        tasks_dirs, jobdirs, runtime.model, ae, args.max_parallel, _default_runner,
        n_concurrent=args.n_concurrent, harbor_extra=harbor_extra, baselines=matrix,
    )

    # 4. collect + 5. plot (reuse existing CLIs)
    _collect(run_specs, args.out_root)
    _plot(args.out_root)


if __name__ == "__main__":
    main()
