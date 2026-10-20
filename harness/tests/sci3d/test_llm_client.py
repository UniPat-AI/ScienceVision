from __future__ import annotations

import os
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from sci3d_agent.llm_client import build_client, LLMConfigError, FakeLLMClient, AnthropicLLMClient, LLMResponse


def _make_message(content_blocks: list, in_tokens: int = 10, out_tokens: int = 5):
    """Build a stub anthropic.types.Message-like object."""
    return SimpleNamespace(
        content=content_blocks,
        usage=SimpleNamespace(input_tokens=in_tokens, output_tokens=out_tokens),
        stop_reason="end_turn",
    )


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, args: dict, tool_id: str = "toolu_01"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=tool_id)


@pytest.mark.unit
def test_anthropic_client_translates_system_message_to_top_level_kwarg() -> None:
    """System-role message must be extracted to `system=` kwarg, not passed in messages."""
    stub_sdk = MagicMock()
    stub_sdk.messages.create.return_value = _make_message([_text_block("ok")])
    client = AnthropicLLMClient(client=stub_sdk)

    client.complete(
        messages=[
            {"role": "system", "content": "you are a judge"},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        model="claude-sonnet-4-6",
    )

    kwargs = stub_sdk.messages.create.call_args.kwargs
    assert kwargs["system"] == "you are a judge"
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 1024


@pytest.mark.unit
def test_build_client_requires_api_key(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        build_client()

@pytest.mark.unit
def test_build_client_uses_base_url_when_set(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://mindra.example/v1")
    client = build_client()
    assert client._client.base_url
    assert "mindra" in str(client._client.base_url)

@pytest.mark.unit
def test_build_client_falls_back_to_official_when_url_unset(monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    client = build_client()
    assert client is not None

@pytest.mark.unit
def test_fake_llm_client_returns_canned_response():
    fake = FakeLLMClient(responses=[{"tool": "finish", "args": {"answer": "ok"}}])
    msg = fake.complete(messages=[], tools=[], model="claude-haiku-4-5", max_tokens=512)
    assert msg.tool_call.name == "finish"
    assert msg.tool_call.args == {"answer": "ok"}


@pytest.mark.unit
def test_anthropic_client_unpacks_tool_use_block() -> None:
    """A tool_use ContentBlock becomes LLMResponse.tool_call."""
    stub_sdk = MagicMock()
    stub_sdk.messages.create.return_value = _make_message(
        [_tool_use_block(name="_judge", args={"score": 0.5, "rationale": "hedged"})]
    )
    client = AnthropicLLMClient(client=stub_sdk)

    result = client.complete(
        messages=[{"role": "user", "content": "score this"}],
        tools=[{"name": "_judge", "input_schema": {}}],
        model="claude-sonnet-4-6",
    )

    assert result.tool_call is not None
    assert result.tool_call.name == "_judge"
    assert result.tool_call.args == {"score": 0.5, "rationale": "hedged"}
    assert result.text is None


@pytest.mark.unit
def test_anthropic_client_unpacks_text_block() -> None:
    """A text ContentBlock becomes LLMResponse.text."""
    stub_sdk = MagicMock()
    stub_sdk.messages.create.return_value = _make_message(
        [_text_block("the answer is 42")]
    )
    client = AnthropicLLMClient(client=stub_sdk)

    result = client.complete(
        messages=[{"role": "user", "content": "say something"}],
        tools=[],
        model="claude-sonnet-4-6",
    )

    assert result.text == "the answer is 42"
    assert result.tool_call is None


@pytest.mark.unit
def test_anthropic_client_handles_empty_content() -> None:
    """Empty content list yields tool_call=None, text=None without raising."""
    stub_sdk = MagicMock()
    stub_sdk.messages.create.return_value = _make_message([])
    client = AnthropicLLMClient(client=stub_sdk)

    result = client.complete(
        messages=[{"role": "user", "content": "x"}],
        tools=[],
        model="claude-sonnet-4-6",
    )

    assert result.tool_call is None
    assert result.text is None


@pytest.mark.unit
def test_anthropic_client_forwards_usage_tokens() -> None:
    """input_tokens / output_tokens come through from the SDK Message.usage."""
    stub_sdk = MagicMock()
    stub_sdk.messages.create.return_value = _make_message(
        [_text_block("hi")], in_tokens=137, out_tokens=42
    )
    client = AnthropicLLMClient(client=stub_sdk)

    result = client.complete(
        messages=[{"role": "user", "content": "ok"}],
        tools=[],
        model="claude-sonnet-4-6",
    )

    assert result.input_tokens == 137
    assert result.output_tokens == 42


@pytest.mark.unit
def test_build_client_returns_anthropic_llm_client(monkeypatch) -> None:
    """build_client() must return an AnthropicLLMClient (not the raw SDK)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:0")

    from sci3d_agent.llm_client import build_client
    client = build_client()

    assert isinstance(client, AnthropicLLMClient)
    # The wrapped raw client is an anthropic.Anthropic instance.
    import anthropic
    assert isinstance(client._client, anthropic.Anthropic)
