"""Drive the 8 QA-Gen pilot tasks end to end: real LLM agent -> verifier score.

For each generated Harbor task dir under --tasks:
  1. Sci3dAgent.run() with a real LLM produces sci3d_answer.json + trajectory.
     The host-run route's FakeKernel actually exec()s the agent's Python, so the
     LLM CAN read the asset bytes (nibabel.load, trimesh.load) — but there is no
     interactive 3D renderer / camera, so this measures code-driven inspection,
     not active-vision. The resolved asset path MUST reach the agent (see
     --asset-root in qa_gen_to_tasks and the asset_path kwarg below); without it
     the LLM only gets "Asset: None" and cannot find the file.
  2. score_rewards() scores the answer (exact_match where GT is structured,
     domain LLM-judge, process rewards, weighted total).

Writes per-task reward.json + a results.json array (Harbor TrialResult-shaped:
task_name + verifier_result.rewards) so aggregate_weighted.py can consume it.

Run (real LLM via Mindra proxy):
    source ~/.bashrc; mindra_on; export ANTHROPIC_API_KEY="$ANTHROPIC_AUTH_TOKEN"
    env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY \
        -u https_proxy .venv-harbor/bin/python -m scripts.harbor.run_qagen_pilot \
        --tasks scripts/harbor/tasks-qagen --out /tmp/qagen_results \
        --model claude-sonnet-4-6
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tomllib
from pathlib import Path

from harbor.models.agent.context import AgentContext

from harbor_adapters.sci3d._score_core import score_rewards
from harbor_adapters.sci3d.agent import Sci3dAgent


def _run_agent(
    task_dir: Path, logs: Path, model: str, max_iters: int, asset_path: str
) -> None:
    inst = (task_dir / "instruction.md").read_text()
    logs.mkdir(parents=True, exist_ok=True)
    # Pass the resolved asset path as the agent kwarg so parse_instruction can
    # tell the LLM where the file is (instruction.md is bare prose with no
    # Asset: line). Without this the agent gets "Asset: None" and cannot read
    # the volume/mesh — every read-dependent task then times out at null.
    agent = Sci3dAgent(
        logs_dir=logs,
        model_name=model,
        mode="mode1_no_vision",
        max_iters=max_iters,
        asset_path=asset_path,
    )
    asyncio.run(agent.run(instruction=inst, environment=None, context=AgentContext()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--max-iters", type=int, default=10)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    task_dirs = sorted(d for d in args.tasks.iterdir() if (d / "task.toml").exists())
    print(f"running {len(task_dirs)} QA-Gen task(s) with model={args.model}", flush=True)

    for d in task_dirs:
        toml = tomllib.loads((d / "task.toml").read_text())
        meta = toml["metadata"]
        name = toml["task"]["name"]
        inst = (d / "instruction.md").read_text()
        logs = args.out / d.name / "agent"
        asset_path = str(meta.get("asset_path", ""))
        try:
            _run_agent(d, logs, args.model, args.max_iters, asset_path)
            rewards, audit = score_rewards(
                meta, task_id=name, instruction=inst,
                agent_dir=logs, judge_model=args.model,
            )
            (args.out / d.name / "reward.json").write_text(
                json.dumps(rewards, ensure_ascii=False, indent=2)
            )
            (args.out / d.name / "sci3d_scores.json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2, default=str)
            )
            results.append({"task_name": name, "verifier_result": {"rewards": rewards}})
            print(
                f"DONE {d.name}: reward={rewards.get('reward'):.3f} "
                f"exact={rewards.get('exact_match')} judge={rewards.get('llm_judge')}",
                flush=True,
            )
        except Exception as exc:  # keep going; record the failure
            results.append({"task_name": name, "error": repr(exc)})
            print(f"FAIL {d.name}: {exc!r}", flush=True)

    (args.out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"wrote {args.out / 'results.json'} ({len(results)} entries)", flush=True)


if __name__ == "__main__":
    main()
