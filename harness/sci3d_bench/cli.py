"""sci3d-bench CLI: load a directory of TaskSpec JSONs, run suite, write trajectories."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from sci3d_bench.task_schema import load_taskspec
from sci3d_bench.runner import run_suite
from sci3d_bench.adapters.sci3d_agent_adapter import Sci3DAgentAdapter
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge
from sci3d_agent.llm_client import build_client, FakeLLMClient


def _load_dir(d: Path) -> list:
    return [load_taskspec(p) for p in sorted(Path(d).glob("*.json"))]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sci3d-bench")
    p.add_argument("--suite", required=True, help="Directory of TaskSpec JSONs")
    p.add_argument("--modes", required=True, help="Comma-separated mode names")
    p.add_argument("--run-dir", required=True, help="Output directory for trajectories")
    p.add_argument("--isolation", default="kernel", choices=("kernel", "container", "both"))
    p.add_argument("--model", default=os.environ.get("SCI3D_MODEL", "claude-haiku-4-5-20251001"))
    args = p.parse_args(argv)

    tasks = _load_dir(Path(args.suite))
    modes = args.modes.split(",")

    use_fake = os.environ.get("SCI3D_USE_FAKE_LLM") == "1"

    def adapter_factory():
        if use_fake:
            from sci3d_agent.llm_client import FakeLLMClient
            # Provide enough canned responses for all task×mode pairs (kernel isolation reuses one adapter).
            n = len(tasks) * len(modes)
            llm_factory = lambda: FakeLLMClient([
                {"tool": "execute_code", "args": {"code": "print(1)"}},
                {"tool": "finish", "args": {"answer": 8}},
            ])
            scorer_llm = FakeLLMClient([
                {"tool": "_judge", "args": {"score": 1.0, "rationale": "ok"}}
                for _ in range(max(n, 1))
            ])
        else:
            client = build_client()
            llm_factory = lambda: client
            scorer_llm = client
        return Sci3DAgentAdapter(
            llm_factory=llm_factory,
            kernel_factory=FakeKernel,
            bridge_factory=StubViewerBridge,
            scorer_llm=scorer_llm,
            model=args.model,
        )

    Path(args.run_dir).mkdir(parents=True, exist_ok=True)
    n = 0
    for result in run_suite(tasks, modes, adapter_factory=adapter_factory, isolation=args.isolation, run_dir=Path(args.run_dir)):
        n += 1
        print(result)
    print(f"completed {n} runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
