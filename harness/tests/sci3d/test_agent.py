import pytest
from sci3d_agent.agent import run_agent
from sci3d_agent.modes import get_profile
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.viewer_bridge import StubViewerBridge
from sci3d_agent.llm_client import FakeLLMClient

@pytest.mark.unit
def test_agent_finishes_immediately_when_llm_says_finish():
    llm = FakeLLMClient(responses=[
        {"tool": "finish", "args": {"answer": "42"}, "input_tokens": 10, "output_tokens": 5},
    ])
    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[{"role": "user", "content": "what is the answer?"}],
        llm=llm,
        kernel=FakeKernel(),
        bridge=StubViewerBridge(),
        task_id="t-finish-fast",
        model="fake-model",
        seed=0,
        asset_path="",
        max_iters=10,
    )
    assert traj.final_answer == "42"
    assert traj.timeout is False
    assert len(traj.steps) == 1

@pytest.mark.unit
def test_agent_executes_code_then_finishes():
    llm = FakeLLMClient(responses=[
        {"tool": "execute_code", "args": {"code": "x = 7\nprint(x)"}, "input_tokens": 10, "output_tokens": 5},
        {"tool": "finish", "args": {"answer": 7}, "input_tokens": 12, "output_tokens": 3},
    ])
    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[],
        llm=llm,
        kernel=FakeKernel(),
        bridge=StubViewerBridge(),
        task_id="t-code-then-finish",
        model="fake",
        seed=0,
        asset_path="",
        max_iters=10,
    )
    assert traj.final_answer == 7
    assert len(traj.steps) == 2

@pytest.mark.unit
def test_agent_records_violation_in_mode1_when_llm_renders():
    llm = FakeLLMClient(responses=[
        {"tool": "execute_code", "args": {"code": "plt.savefig('a.png')"}},
        {"tool": "finish", "args": {"answer": "done"}},
    ])
    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[],
        llm=llm,
        kernel=FakeKernel(emit_image_on=("plt.savefig",)),
        bridge=StubViewerBridge(),
        task_id="t-violate",
        model="fake",
        seed=0,
        asset_path="",
        max_iters=10,
    )
    assert any("plt.savefig" in v for v in traj.violations)

@pytest.mark.unit
def test_agent_times_out_at_max_iters():
    # Repeats execute_code forever; never calls finish.
    llm = FakeLLMClient(responses=[
        {"tool": "execute_code", "args": {"code": "pass"}} for _ in range(10)
    ])
    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[],
        llm=llm,
        kernel=FakeKernel(),
        bridge=StubViewerBridge(),
        task_id="t-timeout",
        model="fake",
        seed=0,
        asset_path="",
        max_iters=3,
    )
    assert traj.timeout is True
    assert len(traj.steps) == 3

@pytest.mark.unit
def test_agent_records_kernel_timeout_and_continues():
    llm = FakeLLMClient(responses=[
        {"tool": "execute_code", "args": {"code": "SLEEP"}},
        {"tool": "finish", "args": {"answer": "recovered"}},
    ])
    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[],
        llm=llm,
        kernel=FakeKernel(simulate_timeout_on=("SLEEP",)),
        bridge=StubViewerBridge(),
        task_id="t-kernel-timeout",
        model="fake",
        seed=0,
        asset_path="",
        max_iters=5,
    )
    assert traj.final_answer == "recovered"
    assert traj.steps[0].result.get("error") == "kernel_timeout"

@pytest.mark.unit
def test_agent_records_kernel_crash_and_terminates_loop():
    llm = FakeLLMClient(responses=[
        {"tool": "execute_code", "args": {"code": "CRASH"}},
        {"tool": "finish", "args": {"answer": "should_not_reach"}},
    ])
    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[],
        llm=llm,
        kernel=FakeKernel(crash_on=("CRASH",)),
        bridge=StubViewerBridge(),
        task_id="t-kernel-crash",
        model="fake",
        seed=0,
        asset_path="",
        max_iters=5,
    )
    assert traj.error == "kernel_crash"
    assert traj.final_answer is None
    assert len(traj.steps) == 1


@pytest.mark.unit
def test_agent_appends_anthropic_tool_use_and_tool_result_messages():
    """Regression for the silent multi-step bug: agent.py used to append
    a custom {"role":"tool",...} shape that Anthropic ignored, so the
    LLM never saw prior tool returns and multi-step loops degenerated.
    Verify the loop now appends the correct assistant tool_use + user
    tool_result pair, paired by tool_use_id.
    """
    captured_messages: list = []

    class CapturingLLM:
        def __init__(self):
            self.turn = 0

        def complete(self, messages, tools, model: str, max_tokens: int = 1024):
            from sci3d_agent.llm_client import LLMResponse, ToolCall
            # Snapshot the messages list passed in on each turn so the
            # test can inspect what got appended between turns.
            captured_messages.append([dict(m) for m in messages])
            if self.turn == 0:
                self.turn += 1
                return LLMResponse(
                    tool_call=ToolCall(name="execute_code", args={"code": "x=1"},
                                       tool_use_id="toolu_abc123"),
                    text=None,
                    input_tokens=1, output_tokens=1,
                    assistant_content=[
                        {"type": "tool_use", "id": "toolu_abc123",
                         "name": "execute_code", "input": {"code": "x=1"}},
                    ],
                )
            return LLMResponse(
                tool_call=ToolCall(name="finish", args={"answer": "ok"}),
                text=None, input_tokens=1, output_tokens=1,
            )

    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[{"role": "user", "content": "go"}],
        llm=CapturingLLM(),
        kernel=FakeKernel(),
        bridge=StubViewerBridge(),
        task_id="t-protocol",
        model="fake", seed=0, asset_path="", max_iters=3,
    )
    assert traj.final_answer == "ok"
    # Turn 1 sees: system, user. Turn 2 sees: system, user, assistant(tool_use),
    # user(tool_result). Check the appended pair after the first tool call.
    assert len(captured_messages) == 2
    turn2 = captured_messages[1]
    assistant_msg = next(m for m in turn2 if m.get("role") == "assistant")
    assert assistant_msg["content"][0]["type"] == "tool_use"
    assert assistant_msg["content"][0]["id"] == "toolu_abc123"
    # Last message must be the user tool_result, id-paired to the call.
    tr_msg = turn2[-1]
    assert tr_msg["role"] == "user"
    assert tr_msg["content"][0]["type"] == "tool_result"
    assert tr_msg["content"][0]["tool_use_id"] == "toolu_abc123"
    # Old broken shape must NOT be present.
    assert not any(m.get("role") == "tool" for m in turn2)


@pytest.mark.unit
def test_agent_synthesizes_tool_use_id_for_fake_llm_without_one():
    """FakeLLMClient (and other shims) emit ToolCall with tool_use_id=None.
    Agent must still emit a paired assistant tool_use + user tool_result
    so Anthropic-shaped clients downstream remain happy."""
    captured_messages: list = []

    class CapturingFake:
        def __init__(self):
            self.turn = 0
        def complete(self, messages, tools, model: str, max_tokens: int = 1024):
            from sci3d_agent.llm_client import LLMResponse, ToolCall
            captured_messages.append([dict(m) for m in messages])
            if self.turn == 0:
                self.turn += 1
                # No tool_use_id, no assistant_content (FakeLLMClient shape).
                return LLMResponse(tool_call=ToolCall(name="execute_code",
                                                     args={"code": "y=2"}),
                                   input_tokens=1, output_tokens=1)
            return LLMResponse(tool_call=ToolCall(name="finish", args={"answer": 2}),
                               input_tokens=1, output_tokens=1)

    traj = run_agent(
        profile=get_profile("mode1_no_vision"),
        initial_payload=[{"role": "user", "content": "go"}],
        llm=CapturingFake(),
        kernel=FakeKernel(), bridge=StubViewerBridge(),
        task_id="t-fake-tool-id", model="fake", seed=0, asset_path="", max_iters=3,
    )
    assert traj.final_answer == 2
    turn2 = captured_messages[1]
    assistant_msg = next(m for m in turn2 if m.get("role") == "assistant")
    synth_id = assistant_msg["content"][0]["id"]
    assert synth_id.startswith("synth-"), synth_id
    # Pairing invariant — assistant.tool_use.id must equal user.tool_result.tool_use_id.
    tr_msg = turn2[-1]
    assert tr_msg["content"][0]["tool_use_id"] == synth_id
