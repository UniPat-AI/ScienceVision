"""Runner: skip GT-absent tasks, run agent under mode profile, score, persist trajectory."""
from __future__ import annotations
import hashlib
import threading
from dataclasses import replace
from itertools import product
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal
from sci3d_agent.trajectory import ScoreBundle, MetaBundle

DEFAULT_TASK_TIMEOUT_S = 600  # 10 minutes per spec §6


def _check_asset_sha256(task) -> bool | None:
    """Return True if sha matches, False if mismatch, None if no sha to verify."""
    expected = task.asset.sha256
    if not expected:
        return None
    p = Path(task.asset.path)
    if not p.exists():
        return False
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest() == expected


def _run_with_timeout(fn, timeout_s: float):
    """Run `fn()` in a daemon thread; return (result, timed_out: bool).

    Cooperative — Python threads can't be forcibly killed, so this gives
    the caller a wall-clock signal but the worker keeps running until it
    finishes naturally. For the kernel paths it's enough because the
    agent loop checks max_iters and the kernel has its own per-call timeout.
    """
    result_box: dict = {}
    def _worker():
        try:
            result_box["ok"] = fn()
        except BaseException as e:
            result_box["err"] = e
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        return None, True
    if "err" in result_box:
        raise result_box["err"]
    return result_box.get("ok"), False


def run_one(task, mode: str, adapter, run_dir: Path, task_timeout_s: float = DEFAULT_TASK_TIMEOUT_S) -> dict:
    if task.ground_truth is None:
        return {"task_id": task.task_id, "mode": mode, "skipped": True, "skip_reason": "gt_absent"}

    sha_ok = _check_asset_sha256(task)
    if sha_ok is False:
        return {"task_id": task.task_id, "mode": mode, "skipped": True, "skip_reason": "asset_mismatch"}

    traj, timed_out = _run_with_timeout(lambda: adapter.run(task, mode), task_timeout_s)
    if timed_out:
        return {"task_id": task.task_id, "mode": mode, "skipped": True, "skip_reason": "task_timeout"}

    score = adapter.score(task, traj)
    bundle = ScoreBundle(
        v_class_score=None,
        v_class_metric=None,
        judge_score=score.value,
        judge_rationale=score.raw.get("rationale"),
        degraded=score.degraded,
    )

    judge_in = int(score.raw.get("tokens_in", 0))
    judge_out = int(score.raw.get("tokens_out", 0))
    new_token_counts = dict(traj.meta.token_counts)
    new_token_counts["judge_input"] = new_token_counts.get("judge_input", 0) + judge_in
    new_token_counts["judge_output"] = new_token_counts.get("judge_output", 0) + judge_out
    new_meta = replace(traj.meta, token_counts=new_token_counts)
    traj_with_scores = replace(traj, scores=bundle, meta=new_meta)

    out_path = Path(run_dir) / f"{task.task_id}__{mode}.json"
    traj_with_scores.dump_json(out_path)
    return {
        "task_id": task.task_id, "mode": mode, "skipped": False,
        "score": score.value, "violations": list(traj.violations),
        "trajectory_path": str(out_path),
    }


def run_suite(
    tasks: Iterable,
    modes: Iterable[str],
    adapter_factory: Callable,
    isolation: Literal["kernel", "container", "both"] = "kernel",
    run_dir: Path = Path("out_samples/sci3d_bench/_default"),
    task_timeout_s: float = DEFAULT_TASK_TIMEOUT_S,
) -> Iterator[dict]:
    tasks = list(tasks)
    modes = list(modes)
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    if isolation == "kernel":
        adapter = adapter_factory()
        for task, mode in product(tasks, modes):
            yield run_one(task, mode, adapter, run_dir, task_timeout_s=task_timeout_s)
    elif isolation == "container":
        for task, mode in product(tasks, modes):
            adapter = adapter_factory()
            yield run_one(task, mode, adapter, run_dir, task_timeout_s=task_timeout_s)
    elif isolation == "both":
        yield from run_suite(tasks, modes, adapter_factory, isolation="kernel",
                             run_dir=Path(run_dir) / "kernel", task_timeout_s=task_timeout_s)
        yield from run_suite(tasks, modes, adapter_factory, isolation="container",
                             run_dir=Path(run_dir) / "container", task_timeout_s=task_timeout_s)
    else:
        raise ValueError(f"unknown isolation: {isolation}")
