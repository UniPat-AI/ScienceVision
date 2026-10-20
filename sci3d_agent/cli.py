"""sci3d-agent CLI: standalone runner. Either --task <path> or --asset + --question."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from sci3d_agent.agent import run_agent
from sci3d_agent.modes import get_profile, MODE_NAMES
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge
from sci3d_agent.llm_client import build_client, FakeLLMClient


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sci3d-agent")
    p.add_argument("--mode", required=True, choices=MODE_NAMES)
    p.add_argument("--task", help="Path to a TaskSpec JSON")
    p.add_argument("--asset", help="Path to a 3D asset (alternative to --task)")
    p.add_argument("--question", help="Free-form question (with --asset)")
    p.add_argument("--model", default=os.environ.get("SCI3D_MODEL", "claude-haiku-4-5-20251001"))
    p.add_argument("--max-iters", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", help="Trajectory output JSON path")
    args = p.parse_args(argv)

    if not args.task and not (args.asset and args.question):
        p.error("provide either --task PATH or --asset PATH --question STR")

    if args.task:
        from sci3d_bench.task_schema import load_taskspec
        task = load_taskspec(Path(args.task))
        question = task.question
        asset_path = task.asset.path
    else:
        question = args.question
        asset_path = args.asset

    if os.environ.get("SCI3D_USE_FAKE_LLM") == "1":
        llm = FakeLLMClient([{"tool": "finish", "args": {"answer": "fake"}, "input_tokens": 1, "output_tokens": 1}])
    else:
        llm = build_client()

    profile = get_profile(args.mode)
    payload = [{"role": "user", "content": f"Question: {question}\nAsset: {asset_path}"}]
    traj = run_agent(
        profile=profile, initial_payload=payload, llm=llm,
        kernel=FakeKernel(), bridge=StubViewerBridge(),
        task_id=args.task or args.asset, model=args.model, seed=args.seed,
        asset_path=asset_path, max_iters=args.max_iters,
    )
    if args.out:
        traj.dump_json(Path(args.out))
    else:
        from dataclasses import asdict
        json.dump(asdict(traj), sys.stdout, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
