"""Multi-turn agent-loop tests for the three verify-pipeline fixes:

1. Valid Anthropic multi-turn shape — assistant ``tool_use`` block echoed,
   then a user ``tool_result`` block keyed to the same ``tool_use_id``.
2. Images in a tool result become real ``image`` blocks the model sees, are
   persisted to disk, and the trajectory stores the PNG path (not base64).
3. The model's per-step reasoning (``response.text``) is recorded on each step.
"""
from __future__ import annotations

import base64
import io
import json

import pytest

from sci3d_agent import mcp_bridge
from sci3d_agent.agent import run_agent
from sci3d_agent.kernel import FakeKernel
from sci3d_agent.llm_client import LLMResponse, ToolCall
from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.modes import get_profile
from sci3d_agent.viewer_bridge import StubViewerBridge


# --- a 1x1 PNG, base64 -------------------------------------------------------
def _tiny_png_b64() -> str:
    # Minimal valid PNG (1x1 transparent) so extract_images decodes it.
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAen63NgAAAAASUVORK5CYII="
    )
    return base64.b64encode(raw).decode("ascii")


class _ScriptedLLM:
    """FakeLLM that surfaces text + a real tool_use_id, like the prod client.

    Each scripted item: {"tool", "args", "text", "tool_use_id"} for a single
    call, OR {"calls": [(name, args, id), ...], "text"} for a parallel turn.
    """

    def __init__(self, items):
        self._q = list(items)

    def complete(self, messages, tools, model, max_tokens=1024):
        item = self._q.pop(0)
        text = item.get("text")
        if "calls" in item:
            calls = []
            assistant_content = []
            if text:
                assistant_content.append({"type": "text", "text": text})
            for (name, args, tid) in item["calls"]:
                calls.append((ToolCall(name=name, args=args), tid))
                assistant_content.append(
                    {"type": "tool_use", "id": tid, "name": name, "input": args}
                )
            first = calls[0]
            return LLMResponse(
                tool_call=first[0], text=text, input_tokens=10, output_tokens=5,
                tool_use_id=first[1], assistant_content=assistant_content,
                tool_calls=tuple(calls),
            )
        name = item["tool"]
        args = item.get("args", {})
        tid = item.get("tool_use_id")
        assistant_content = None
        tool_calls = ()
        if tid is not None:
            assistant_content = []
            if text:
                assistant_content.append({"type": "text", "text": text})
            assistant_content.append(
                {"type": "tool_use", "id": tid, "name": name, "input": args}
            )
            tool_calls = ((ToolCall(name=name, args=args), tid),)
        return LLMResponse(
            tool_call=ToolCall(name=name, args=args),
            text=text,
            input_tokens=10,
            output_tokens=5,
            tool_use_id=tid,
            assistant_content=assistant_content,
            tool_calls=tool_calls,
        )


@pytest.fixture
def index_snapshot():
    snapshot = dict(mcp_bridge._INDEX)
    yield
    mcp_bridge._INDEX.clear()
    mcp_bridge._INDEX.update(snapshot)


def _install_fake(name, handler):
    spec = mcp_bridge._INDEX[name]
    mcp_bridge._INDEX[name] = ToolSpec(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema,
        handler=handler,
        category=spec.category,
    )


def _run(llm, image_out_dir=None):
    return run_agent(
        profile=get_profile("mode4_mcp"),
        initial_payload=[{"role": "user", "content": "go"}],
        llm=llm,
        kernel=FakeKernel(),
        bridge=StubViewerBridge(),
        task_id="t-multiturn",
        model="fake",
        seed=0,
        asset_path="",
        max_iters=6,
        image_out_dir=image_out_dir,
    )


@pytest.mark.unit
def test_reasoning_recorded_per_step(index_snapshot):
    _install_fake("sv_list_matched_datasets", lambda a: {"total": 0, "records": []})
    llm = _ScriptedLLM(
        [
            {"tool": "sv_list_matched_datasets", "args": {}, "text": "Let me list datasets.",
             "tool_use_id": "toolu_a"},
            {"tool": "finish", "args": {"answer": "done"}, "text": "I have my answer.",
             "tool_use_id": "toolu_b"},
        ]
    )
    traj = _run(llm)
    assert traj.final_answer == "done"
    assert traj.steps[0].reasoning == "Let me list datasets."
    assert traj.steps[0].tool_use_id == "toolu_a"
    assert traj.steps[1].reasoning == "I have my answer."


@pytest.mark.unit
def test_image_becomes_block_persisted_and_stripped_from_trajectory(index_snapshot, tmp_path):
    png_b64 = _tiny_png_b64()

    def fake_render(_args):
        return {"image_png_base64": png_b64, "renderer_used": "fake", "width": 1, "height": 1}

    # Borrow any sv3d tool name that exists in the index for the fake.
    target = "sv3d_render" if "sv3d_render" in mcp_bridge._INDEX else next(
        n for n in mcp_bridge._INDEX if n.startswith("sv3d_")
    )
    _install_fake(target, fake_render)

    img_dir = tmp_path / "images"
    llm = _ScriptedLLM(
        [
            {"tool": target, "args": {"session_id": "s"}, "text": "render it",
             "tool_use_id": "toolu_r"},
            {"tool": "finish", "args": {"answer": "ok"}, "text": "done",
             "tool_use_id": "toolu_f"},
        ]
    )
    traj = _run(llm, image_out_dir=img_dir)

    # (b) image persisted to disk + path recorded on the step
    step0 = traj.steps[0]
    assert step0.images, "expected at least one saved image path"
    saved = img_dir / step0.images[0].split("/")[-1]
    assert saved.exists() and saved.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # base64 stripped from the trajectory result (kept compact)
    blob = json.dumps(traj.steps[0].result)
    assert png_b64 not in blob
    assert "image saved" in blob or "<image" in blob


@pytest.mark.unit
def test_messages_form_valid_multiturn_pairs(index_snapshot):
    """The assistant tool_use id and the following tool_result id must match.

    We assert indirectly: a captured message log shows an assistant turn whose
    tool_use id equals the user tool_result's tool_use_id.
    """
    captured = {}

    def fake(_args):
        return {"total": 0}

    _install_fake("sv_list_matched_datasets", fake)

    class _CapturingLLM(_ScriptedLLM):
        def complete(self, messages, tools, model, max_tokens=1024):
            # Snapshot the conversation on the *second* call (after one tool turn).
            if len(messages) > 2 and "pairs" not in captured:
                captured["pairs"] = [dict(m) for m in messages]
            return super().complete(messages, tools, model, max_tokens)

    llm = _CapturingLLM(
        [
            {"tool": "sv_list_matched_datasets", "args": {}, "text": "list", "tool_use_id": "toolu_x"},
            {"tool": "finish", "args": {"answer": "z"}, "text": "fin", "tool_use_id": "toolu_y"},
        ]
    )
    _run(llm)

    msgs = captured["pairs"]
    # find assistant tool_use id
    asst = next(m for m in msgs if m["role"] == "assistant" and isinstance(m["content"], list))
    tu = next(b for b in asst["content"] if b.get("type") == "tool_use")
    usr = next(
        m for m in msgs
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    )
    tr = next(b for b in usr["content"] if b.get("type") == "tool_result")
    assert tr["tool_use_id"] == tu["id"] == "toolu_x"


@pytest.mark.unit
def test_parallel_tool_use_returns_a_result_for_every_tool_use(index_snapshot):
    """A turn with N tool_use blocks must yield N tool_result blocks with
    matching ids — the Anthropic API rejects a turn that omits any
    (BedrockException: 'Expected toolResult blocks ... for the following Ids').
    """
    _install_fake("sv_list_matched_datasets", lambda a: {"total": 1})
    target = "sv3d_get_state" if "sv3d_get_state" in mcp_bridge._INDEX else "sv_list_matched_datasets"
    _install_fake(target, lambda a: {"ok": True})

    captured = {}

    class _Cap(_ScriptedLLM):
        def complete(self, messages, tools, model, max_tokens=1024):
            if len(messages) > 2 and "msgs" not in captured:
                captured["msgs"] = [dict(m) for m in messages]
            return super().complete(messages, tools, model, max_tokens)

    llm = _Cap(
        [
            {"text": "do both", "calls": [
                ("sv_list_matched_datasets", {}, "toolu_p1"),
                (target, {"session_id": "s"}, "toolu_p2"),
            ]},
            {"tool": "finish", "args": {"answer": "ok"}, "text": "fin", "tool_use_id": "toolu_done"},
        ]
    )
    traj = _run(llm)

    # both calls recorded as steps in the same turn
    assert [s.tool for s in traj.steps[:2]] == ["sv_list_matched_datasets", target]
    assert traj.steps[0].iter == traj.steps[1].iter == 0

    # the user turn after the parallel assistant turn has 2 tool_result blocks
    msgs = captured["msgs"]
    usr = next(
        m for m in msgs
        if m["role"] == "user" and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    )
    ids = {b["tool_use_id"] for b in usr["content"] if b.get("type") == "tool_result"}
    assert ids == {"toolu_p1", "toolu_p2"}
