"""SWE-Vision-shaped agent loop. Mode differences are absorbed by ModeProfile.

Multi-turn tool use follows the Anthropic message contract: the assistant's
``tool_use`` block is echoed back as an ``assistant`` turn, and the tool's
output is returned as a ``user`` turn containing a ``tool_result`` block keyed
to the matching ``tool_use_id``. Images in a tool result are passed back as
real ``image`` blocks (the model sees pixels) and persisted to disk; the
trajectory records the model's per-step reasoning and the image paths.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Optional
from sci3d_agent.modes import ModeProfile
from sci3d_agent.dispatch import dispatch
from sci3d_agent.kernel import KernelCrash, KernelTimeout
from sci3d_agent.trajectory import TrajectoryRecorder, TrajectoryRecord
from sci3d_agent._tool_result import (
    extract_images,
    persist_images,
    build_tool_result_block,
    compact_result_for_trajectory,
)


def _assistant_turn(response, call, tool_use_id: str) -> dict:
    """Build the assistant message to append before the tool_result.

    Prefer the model's original content blocks (they carry the real
    ``tool_use`` id + any sibling text). Fall back to a synthesized block for
    FakeLLM / clients that don't surface raw content, using the same
    ``tool_use_id`` the caller will key the tool_result to.
    """
    if getattr(response, "assistant_content", None):
        return {"role": "assistant", "content": response.assistant_content}
    block: list[dict] = []
    if response.text:
        block.append({"type": "text", "text": response.text})
    block.append(
        {"type": "tool_use", "id": tool_use_id, "name": call.name, "input": call.args}
    )
    return {"role": "assistant", "content": block}


def _serialize_tool_result(result: Any) -> str:
    """Anthropic tool_result.content expects a string or list-of-blocks.
    Serialize dicts/lists as compact JSON so the model can parse them back."""
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, default=str)
        except (TypeError, ValueError):
            return str(result)
    return str(result)


def run_agent(
    profile: ModeProfile,
    initial_payload: list,
    llm,
    kernel,
    bridge,
    task_id: str,
    model: str,
    seed: int,
    asset_path: str,
    max_iters: int = 20,
    image_out_dir: Optional[Path] = None,
) -> TrajectoryRecord:
    rec = TrajectoryRecorder(task_id=task_id, mode=profile.name, model=model, seed=seed)
    messages = [{"role": "system", "content": profile.system_prompt}] + list(initial_payload)
    img_dir = Path(image_out_dir) if image_out_dir else None
    iter_ = 0
    while iter_ < max_iters:
        t0 = time.time()
        response = llm.complete(messages=messages, tools=list(profile.advertised_tools), model=model)
        rec.token_counts.setdefault("agent_input", 0)
        rec.token_counts.setdefault("agent_output", 0)
        rec.token_counts["agent_input"] += response.input_tokens
        rec.token_counts["agent_output"] += response.output_tokens

        if response.tool_call is None:
            # Model emitted prose without a tool call. Record it as reasoning
            # and feed it back as an assistant turn so the conversation stays
            # well-formed and the model can self-correct toward a tool call.
            rec.record_step(
                iter_=iter_, tool="<no_tool_call>", args={},
                result={"text": response.text or ""},
                latency_ms=int((time.time() - t0) * 1000),
                reasoning=response.text,
            )
            messages.append({"role": "assistant", "content": response.text or ""})
            messages.append(
                {"role": "user", "content": "Please call a tool (or `finish`) to proceed."}
            )
            iter_ += 1
            continue

        # The model may emit several tool_use blocks in one turn (parallel tool
        # use). The Anthropic contract requires a tool_result for EVERY one, so
        # we process them all and return all results in a single user turn.
        # FakeLLM / single-call clients don't populate `tool_calls`; fall back
        # to the single `tool_call`.
        calls = list(response.tool_calls) or [(response.tool_call, response.tool_use_id)]

        # A `finish` anywhere in the turn ends the task (its answer wins).
        for call, _tid in calls:
            if call.name == "finish":
                rec.record_step(
                    iter_=iter_, tool="finish", args=call.args, result={},
                    latency_ms=int((time.time() - t0) * 1000),
                    reasoning=response.text, tool_use_id=_tid,
                )
                rec.record_final(call.args.get("answer"))
                return rec.build()

        result_blocks: list[dict] = []
        crashed = False
        for idx, (call, tid) in enumerate(calls):
            tool_use_id = tid or call.tool_use_id or f"synth-{iter_}-{idx}"
            try:
                result, violations = dispatch(call, profile, kernel, bridge, asset_path=asset_path)
            except ValueError as e:
                result, violations = {"error": f"dispatch_error: {e}"}, []
            except KernelTimeout:
                result, violations = {"error": "kernel_timeout", "code": call.args.get("code", "")}, []
            except KernelCrash as e:
                result, violations = {"error": "kernel_crash", "detail": str(e)}, []
                rec.record_error("kernel_crash")
                crashed = True

            for v in violations:
                rec.record_violation(v)

            images = extract_images(result)
            image_paths: list[str] = []
            if images and img_dir is not None:
                image_paths = persist_images(images, img_dir, stem=f"step{iter_:03d}_{idx}")
            traj_result = compact_result_for_trajectory(result, image_paths)
            # Reasoning text belongs to the turn; attach it to the first call only.
            rec.record_step(
                iter_=iter_, tool=call.name, args=call.args, result=traj_result,
                latency_ms=int((time.time() - t0) * 1000),
                reasoning=response.text if idx == 0 else None,
                tool_use_id=tool_use_id, images=image_paths,
            )
            result_blocks.append(
                build_tool_result_block(tool_use_id, result, images, image_paths)
            )
            if crashed:
                return rec.build()

        # One assistant turn (all tool_use blocks) + one user turn (all results).
        first_call, first_tid = calls[0]
        first_id = first_tid or first_call.tool_use_id or f"synth-{iter_}-0"
        messages.append(_assistant_turn(response, first_call, first_id))
        messages.append({"role": "user", "content": result_blocks})
        iter_ += 1

    rec.record_timeout()
    return rec.build()
