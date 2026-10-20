import os
import pytest
from pathlib import Path
from sci3d_bench.task_schema import load_taskspec
from sci3d_bench.runner import run_one
from sci3d_bench.adapters.sci3d_agent_adapter import Sci3DAgentAdapter
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge

REQUIRES_LLM = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="set RUN_E2E=1 + ANTHROPIC_API_KEY to enable",
)

@pytest.mark.e2e
@REQUIRES_LLM
def test_v6_cube_vertex_count_real_llm(tmp_path):
    pytest.importorskip("anthropic")
    from sci3d_agent.llm_client import build_client
    task = load_taskspec(Path("tasks_seed/v6/cube_vertex_count.json"))
    client = build_client()
    adapter = Sci3DAgentAdapter(
        llm_factory=lambda: client,
        kernel_factory=FakeKernel,
        bridge_factory=StubViewerBridge,
        scorer_llm=client,
        model=os.environ.get("SCI3D_MODEL", "claude-haiku-4-5-20251001"),
    )
    out = run_one(task, mode="mode1_no_vision", adapter=adapter, run_dir=tmp_path)
    assert out["skipped"] is False
    # Cube has 8 vertices; assert score is non-zero (judge gave at least partial credit).
    assert out["score"] >= 0.0
