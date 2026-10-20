"""Translate sci3d_agent's TrajectoryRecord into Harbor's trajectory model.

Kept as pure functions with no side effects so the mapping is unit-testable
without spinning up an environment or an LLM. This module is the *single*
place the adapter couples to ``harbor.models.trajectories`` — when Harbor's
trajectory schema shifts, only this file should need to change.

sci3d_agent.trajectory.TrajectoryRecord (schema 1.1):
    steps:        list[StepRecord(iter, tool, args, result, latency_ms,
                                  reasoning, tool_use_id, images)]
    final_answer: Any | None
    timeout:      bool
    error:        str | None
    meta:         MetaBundle(model, seed, cost_estimate_usd, token_counts)
    token_counts keys produced by the loop: "agent_input", "agent_output".
"""
from __future__ import annotations

import json
from typing import Any

from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)


def _content_to_str(value: Any) -> str:
    """Serialize a tool result (dict/list/scalar) to a string for content."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def step_to_harbor(step: Any, index: int) -> Step:
    """Map one sci3d StepRecord to a Harbor Step.

    ``step`` is a sci3d StepRecord (frozen dataclass) or any object exposing
    the same attributes. We read via getattr so a plain dict-like or a fake
    in tests also works.
    """

    def g(name: str, default: Any = None) -> Any:
        if isinstance(step, dict):
            return step.get(name, default)
        return getattr(step, name, default)

    tool_name = g("tool", "<unknown>")
    args = g("args", {}) or {}
    result = g("result", {})
    reasoning = g("reasoning")
    tool_use_id = g("tool_use_id") or f"sci3d-call-{index}"

    tool_calls = []
    observation = None
    # ``<no_tool_call>`` is the loop's marker for a prose-only turn; it carries
    # no real tool call, only reasoning, so we leave tool_calls/observation empty.
    if tool_name and tool_name != "<no_tool_call>":
        tool_calls.append(
            ToolCall(
                tool_call_id=tool_use_id,
                function_name=tool_name,
                arguments=args if isinstance(args, dict) else {"value": args},
            )
        )
        observation = Observation(
            results=[
                ObservationResult(
                    source_call_id=tool_use_id,
                    content=_content_to_str(result),
                )
            ]
        )

    # Harbor requires a non-null ``message``; sci3d steps don't carry an
    # assistant message separate from reasoning, so we surface the reasoning
    # (or a compact tool summary) as the step message.
    message = reasoning or (f"call {tool_name}" if tool_calls else "")

    return Step(
        step_id=index + 1,  # Harbor step_id is 1-indexed (>= 1)
        source="agent",
        message=message,
        tool_calls=tool_calls,
        observation=observation,
        reasoning_content=reasoning,
        extra={
            "sci3d_iter": g("iter", index),
            "latency_ms": g("latency_ms"),
            "images": list(g("images", []) or []),
        },
    )


def record_to_trajectory(
    record: Any, *, session_id: str, agent_version: str = "unknown"
) -> Trajectory:
    """Map a sci3d TrajectoryRecord to a Harbor Trajectory.

    ``record`` is read via getattr so tests can pass a lightweight stand-in.
    """

    def g(name: str, default: Any = None) -> Any:
        return getattr(record, name, default)

    meta = g("meta")
    model = getattr(meta, "model", None) if meta else None
    token_counts = (getattr(meta, "token_counts", {}) if meta else {}) or {}
    cost = getattr(meta, "cost_estimate_usd", None) if meta else None

    steps = [step_to_harbor(s, i) for i, s in enumerate(g("steps", []) or [])]

    final_metrics = FinalMetrics(
        total_prompt_tokens=token_counts.get("agent_input"),
        total_completion_tokens=token_counts.get("agent_output"),
        total_cost_usd=cost,
        total_steps=len(steps),
        extra={
            "timeout": bool(g("timeout", False)),
            "error": g("error"),
            "violations": list(g("violations", []) or []),
            "final_answer": g("final_answer"),
        },
    )

    return Trajectory(
        session_id=session_id,
        agent=Agent(name="sci3d", version=agent_version, model_name=model),
        steps=steps,
        final_metrics=final_metrics,
        notes=g("mode"),
    )


def record_to_context_fields(record: Any) -> dict:
    """Extract the token/cost fields Harbor's AgentContext expects.

    Returns a dict suitable for ``AgentContext(**fields)``. ``final_answer``
    and the trajectory go into ``metadata`` so a downstream verifier can read
    the answer without parsing the full trajectory.
    """
    meta = getattr(record, "meta", None)
    token_counts = (getattr(meta, "token_counts", {}) if meta else {}) or {}
    cost = getattr(meta, "cost_estimate_usd", None) if meta else None
    return {
        "n_input_tokens": token_counts.get("agent_input"),
        "n_output_tokens": token_counts.get("agent_output"),
        "cost_usd": cost,
    }
