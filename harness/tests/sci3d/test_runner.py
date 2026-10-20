import json
import pytest
from pathlib import Path
from sci3d_bench.task_schema import (TaskSpec, AssetRef, GroundTruth, AnswerSchema, ScorerSpec, Provenance)
from sci3d_bench.runner import run_one, run_suite
from sci3d_bench.adapters.sci3d_agent_adapter import Sci3DAgentAdapter
from sci3d_agent.llm_client import FakeLLMClient
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge

def _task(task_id="t1", gt=42):
    return TaskSpec(
        schema_version="1.0", task_id=task_id, v_class="V6", question="q",
        asset=AssetRef(path="x.obj", format="obj"), prerendered_image=None,
        answer_schema=AnswerSchema(kind="structured", fields={"v": "int"}),
        ground_truth=GroundTruth(value=gt) if gt is not None else None,
        scorer=ScorerSpec(primary="llm_judge"),
        provenance=Provenance(source_dataset="seed", source_qid=None, lifted_at="2026-05-18", lifter_version="m"),
    )

@pytest.mark.unit
def test_runner_skips_gt_absent_task(tmp_path):
    task = _task(gt=None)
    adapter = Sci3DAgentAdapter(
        llm_factory=lambda: FakeLLMClient([{"tool": "finish", "args": {"answer": 1}}]),
        kernel_factory=FakeKernel,
        bridge_factory=StubViewerBridge,
        scorer_llm=FakeLLMClient([{"tool": "_judge", "args": {"score": 0, "rationale": "x"}}]),
        model="fake-model",
    )
    out = run_one(task, mode="mode1_no_vision", adapter=adapter, run_dir=tmp_path)
    assert out["skipped"] is True
    assert out["skip_reason"] == "gt_absent"

@pytest.mark.unit
def test_runner_writes_trajectory_to_run_dir(tmp_path):
    task = _task()
    adapter = Sci3DAgentAdapter(
        llm_factory=lambda: FakeLLMClient([{"tool": "finish", "args": {"answer": 42}}]),
        kernel_factory=FakeKernel,
        bridge_factory=StubViewerBridge,
        scorer_llm=FakeLLMClient([{"tool": "_judge", "args": {"score": 1.0, "rationale": "yes"}}]),
        model="fake-model",
    )
    out = run_one(task, mode="mode1_no_vision", adapter=adapter, run_dir=tmp_path)
    assert out["skipped"] is False
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["task_id"] == "t1"
    assert data["mode"] == "mode1_no_vision"

@pytest.mark.unit
def test_run_suite_iterates_tasks_and_modes(tmp_path):
    tasks = [_task(task_id="ta"), _task(task_id="tb")]
    modes = ["mode1_no_vision", "mode3_free_visual"]
    def adapter_factory():
        # kernel isolation holds one adapter across all task×mode pairs (4 total),
        # so the scorer LLM must serve 4 canned judge responses.
        return Sci3DAgentAdapter(
            llm_factory=lambda: FakeLLMClient([{"tool": "finish", "args": {"answer": 42}}]),
            kernel_factory=FakeKernel,
            bridge_factory=StubViewerBridge,
            scorer_llm=FakeLLMClient([
                {"tool": "_judge", "args": {"score": 1.0, "rationale": "ok"}},
                {"tool": "_judge", "args": {"score": 1.0, "rationale": "ok"}},
                {"tool": "_judge", "args": {"score": 1.0, "rationale": "ok"}},
                {"tool": "_judge", "args": {"score": 1.0, "rationale": "ok"}},
            ]),
            model="fake-model",
        )
    results = list(run_suite(tasks, modes, adapter_factory=adapter_factory, isolation="kernel", run_dir=tmp_path))
    assert len(results) == 4
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 4

@pytest.mark.unit
def test_runner_skips_on_asset_sha256_mismatch(tmp_path):
    asset = tmp_path / "asset.obj"
    asset.write_bytes(b"hello")
    task = TaskSpec(
        schema_version="1.0", task_id="t-sha", v_class="V6", question="q",
        asset=AssetRef(path=str(asset), format="obj", sha256="0" * 64),
        prerendered_image=None,
        answer_schema=AnswerSchema(kind="structured", fields={"v": "int"}),
        ground_truth=GroundTruth(value=1),
        scorer=ScorerSpec(primary="llm_judge"),
        provenance=Provenance(source_dataset="seed", source_qid=None, lifted_at="2026-05-18", lifter_version="m"),
    )
    adapter = Sci3DAgentAdapter(
        llm_factory=lambda: FakeLLMClient([{"tool": "finish", "args": {"answer": 1}}]),
        kernel_factory=FakeKernel, bridge_factory=StubViewerBridge,
        scorer_llm=FakeLLMClient([{"tool": "_judge", "args": {"score": 1.0, "rationale": "x"}}]),
        model="fake-model",
    )
    out = run_one(task, mode="mode1_no_vision", adapter=adapter, run_dir=tmp_path)
    assert out["skipped"] is True
    assert out["skip_reason"] == "asset_mismatch"

@pytest.mark.unit
def test_runner_skips_on_task_timeout(tmp_path):
    import time
    task = _task(task_id="t-slow")

    class SlowAdapter:
        def run(self, t, m):
            time.sleep(2)
            return None
        def score(self, t, traj):
            raise AssertionError("score should not be called when run timed out")

    out = run_one(task, mode="mode1_no_vision", adapter=SlowAdapter(),
                  run_dir=tmp_path, task_timeout_s=0.1)
    assert out["skipped"] is True
    assert out["skip_reason"] == "task_timeout"

@pytest.mark.unit
def test_runner_persists_judge_tokens_in_meta(tmp_path):
    task = _task()
    adapter = Sci3DAgentAdapter(
        llm_factory=lambda: FakeLLMClient([{"tool": "finish", "args": {"answer": 42},
                                            "input_tokens": 11, "output_tokens": 7}]),
        kernel_factory=FakeKernel, bridge_factory=StubViewerBridge,
        scorer_llm=FakeLLMClient([{"tool": "_judge", "args": {"score": 1.0, "rationale": "yes"},
                                   "input_tokens": 33, "output_tokens": 9}]),
        model="fake-model",
    )
    out = run_one(task, mode="mode1_no_vision", adapter=adapter, run_dir=tmp_path)
    assert out["skipped"] is False
    files = list(tmp_path.glob("*.json"))
    data = json.loads(files[0].read_text())
    counts = data["meta"]["token_counts"]
    assert counts["agent_input"] == 11
    assert counts["agent_output"] == 7
    assert counts["judge_input"] == 33
    assert counts["judge_output"] == 9
