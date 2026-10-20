"""LLM client wrapper. Reads ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL (Mindra proxy
convention). FakeLLMClient for tests."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any, Optional

class LLMConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict
    # Anthropic returns a stable `id` on each tool_use block which must be
    # echoed back in the matching tool_result. Lost → LLM can't pair the
    # result to the call and behaves as if no tool ran (see Bug-2 trace).
    # Optional / defaults to None so FakeLLMClient construction in tests
    # stays backwards-compatible.
    tool_use_id: Optional[str] = None


@dataclass(frozen=True)
class LLMResponse:
    tool_call: Optional[ToolCall]
    text: Optional[str] = None
    raw: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    # Anthropic tool_use id for `tool_call` (None for FakeLLM / no tool call).
    # The agent loop keys the matching tool_result block to this id so the
    # multi-turn conversation is a valid Anthropic message sequence.
    tool_use_id: Optional[str] = None
    # The assistant message's raw content blocks (list of dicts) to be echoed
    # back verbatim as the assistant turn. Preserving the original blocks
    # (incl. the tool_use block) is required for a legal multi-turn exchange.
    assistant_content: Optional[list] = None
    # ALL tool_use blocks in this turn as (ToolCall, tool_use_id) pairs. The
    # model may emit several in parallel; the Anthropic contract requires a
    # tool_result for EVERY one. `tool_call`/`tool_use_id` mirror the first.
    tool_calls: tuple = ()


def build_client() -> "AnthropicLLMClient":
    """Construct the production LLM client. Reads ANTHROPIC_API_KEY +
    ANTHROPIC_BASE_URL from env. Returns an AnthropicLLMClient that
    satisfies the .complete() contract used across sci3d_agent + sci3d_bench."""
    return AnthropicLLMClient(client=_build_raw_anthropic_client())


class FakeLLMClient:
    """Returns a scripted sequence of responses."""

    def __init__(self, responses: list[dict]):
        self._queue = list(responses)

    def complete(self, messages, tools, model: str, max_tokens: int = 1024) -> LLMResponse:
        if not self._queue:
            raise RuntimeError("FakeLLMClient out of canned responses")
        item = self._queue.pop(0)
        return LLMResponse(
            tool_call=ToolCall(name=item["tool"], args=item.get("args", {})),
            text=item.get("text"),
            raw=item,
            input_tokens=item.get("input_tokens", 100),
            output_tokens=item.get("output_tokens", 50),
        )


class AnthropicLLMClient:
    """Production LLM client. Translates the in-repo .complete() contract
    to anthropic.Anthropic().messages.create.

    Construction:
        client = AnthropicLLMClient()              # uses build_raw_client()
        client = AnthropicLLMClient(client=mock)   # for tests

    The injected `client` must satisfy `client.messages.create(...)`.
    """

    def __init__(self, client: Any = None):
        if client is None:
            client = _build_raw_anthropic_client()
        self._client = client

    def complete(
        self,
        messages: list,
        tools: list,
        model: str,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        # Extract system message (anthropic API takes `system` as top-level kwarg).
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        system = "\n".join(system_parts) if system_parts else None

        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": non_system,
        }
        if system is not None:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        resp = self._client.messages.create(**kwargs)
        return _unpack_response(resp)


def _build_raw_anthropic_client():
    """Construct the underlying anthropic.Anthropic from env vars.

    Accepts the project's three credential conventions, in priority order:
    ``ANTHROPIC_*`` → ``QINIUYUN_*`` (qnaigc proxy) → ``MINDRA_*``. This
    mirrors the fallback chain already used by
    ``sci3d_agent.mcp.tools.pipeline_{read,write}`` and lets the agent run
    against the qnaigc Anthropic-compatible endpoint configured in ``.env``.
    """
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("QINIUYUN_API")
        or os.environ.get("MINDRA_API")
    )
    if not api_key:
        raise LLMConfigError(
            "no API key in env — set ANTHROPIC_API_KEY (or QINIUYUN_API / MINDRA_API). "
            "see memory/anthropic_api_via_mindra_proxy.md"
        )
    base_url = (
        os.environ.get("ANTHROPIC_BASE_URL")
        or os.environ.get("QINIUYUN_BASE_URL")
        or os.environ.get("MINDRA_BASE_URL")
        or None
    )
    import anthropic
    return anthropic.Anthropic(api_key=api_key, base_url=base_url)


def _unpack_response(resp: Any) -> LLMResponse:
    """Translate an anthropic Message into LLMResponse."""
    tool_call = None
    tool_use_id = None
    text = None
    assistant_content: list = []
    tool_calls: list = []
    for block in getattr(resp, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "tool_use":
            block_id = getattr(block, "id", None)
            block_name = getattr(block, "name", "")
            block_input = dict(getattr(block, "input", {}) or {})
            # Carry the id on the ToolCall too (upstream contract: call.tool_use_id).
            call = ToolCall(name=block_name, args=block_input, tool_use_id=block_id)
            tool_calls.append((call, block_id))
            if tool_call is None:
                tool_call = call
                tool_use_id = block_id
            assistant_content.append(
                {"type": "tool_use", "id": block_id, "name": block_name, "input": block_input}
            )
        elif btype == "text":
            block_text = getattr(block, "text", "")
            if text is None:
                text = block_text
            assistant_content.append({"type": "text", "text": block_text})
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        tool_call=tool_call,
        text=text,
        raw=resp,
        input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
        tool_use_id=tool_use_id,
        assistant_content=assistant_content or None,
        tool_calls=tuple(tool_calls),
    )

