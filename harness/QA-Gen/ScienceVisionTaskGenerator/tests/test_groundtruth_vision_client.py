"""Tests for VisionChatClient — image_url content blocks via OpenAI-compat.

We patch urllib at module level so no real network call is made.
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sciencevision_taskgen.llm._vision import VisionChatClient
from sciencevision_taskgen.llm.client import ChatClientError


def _fake_response(content: str = '{"ok": true}') -> MagicMock:
    payload = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    }
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload).encode()
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    return handle


def test_vision_client_sends_image_url_content_block(tmp_path: Path):
    img = tmp_path / "a.webp"
    img.write_bytes(b"\x00fake")
    client = VisionChatClient(base_url="https://x", api_key="k")

    with patch(
        "sciencevision_taskgen.llm.client.urllib.request.urlopen",
        return_value=_fake_response(),
    ) as urlopen:
        result = client.complete_vision(
            model="claude-sonnet-4-6",
            system="sys",
            user_text="describe this image",
            image_paths=[img],
            image_media_type="image/webp",
            json_mode=True,
        )

    req = urlopen.call_args.args[0]
    body = json.loads(req.data.decode())
    assert body["model"] == "claude-sonnet-4-6"
    msgs = body["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    user = msgs[1]
    assert user["role"] == "user"
    parts = user["content"]
    assert isinstance(parts, list)
    assert parts[0] == {"type": "text", "text": "describe this image"}
    assert parts[1]["type"] == "image_url"
    expected_b64 = base64.b64encode(b"\x00fake").decode()
    assert parts[1]["image_url"]["url"] == f"data:image/webp;base64,{expected_b64}"
    assert body["response_format"] == {"type": "json_object"}

    assert result.content == '{"ok": true}'
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50


def test_vision_client_text_only_when_no_images(tmp_path: Path):
    client = VisionChatClient(base_url="https://x", api_key="k")
    with patch(
        "sciencevision_taskgen.llm.client.urllib.request.urlopen",
        return_value=_fake_response(),
    ) as urlopen:
        client.complete_vision(
            model="m",
            system="s",
            user_text="hi",
            image_paths=[],
            image_media_type="image/webp",
        )
    body = json.loads(urlopen.call_args.args[0].data.decode())
    assert body["messages"][1]["content"] == "hi"


def _fake_response_for_retry(content: str = '{"ok": true}') -> MagicMock:
    payload = {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    handle = MagicMock()
    handle.read.return_value = json.dumps(payload).encode()
    handle.__enter__.return_value = handle
    handle.__exit__.return_value = False
    return handle


def test_complete_vision_with_retry_succeeds_on_second_attempt(tmp_path: Path):
    img = tmp_path / "t.webp"
    img.write_bytes(b"\x00")
    client = VisionChatClient(base_url="https://x", api_key="k")

    call_count = 0

    def _urlopen_side_effect(req, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.URLError("simulated network failure")
        return _fake_response_for_retry()

    with patch(
        "sciencevision_taskgen.llm.client.urllib.request.urlopen",
        side_effect=_urlopen_side_effect,
    ):
        with patch("time.sleep"):  # avoid real delay
            result = client.complete_vision_with_retry(
                model="claude-sonnet-4-6",
                system="sys",
                user_text="go",
                image_paths=[img],
                max_attempts=3,
                backoff_sec=0.01,
            )

    assert result.content == '{"ok": true}'
    assert call_count == 2


def test_complete_vision_with_retry_succeeds_on_third_attempt(tmp_path: Path):
    img = tmp_path / "t.webp"
    img.write_bytes(b"\x00")
    client = VisionChatClient(base_url="https://x", api_key="k")

    call_count = 0

    def _urlopen_side_effect(req, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ChatClientError("transient")
        return _fake_response_for_retry()

    with patch(
        "sciencevision_taskgen.llm.client.urllib.request.urlopen",
        side_effect=_urlopen_side_effect,
    ):
        with patch("time.sleep"):
            result = client.complete_vision_with_retry(
                model="m",
                system="s",
                user_text="go",
                image_paths=[img],
                max_attempts=3,
                backoff_sec=0.01,
            )

    assert result.content == '{"ok": true}'
    assert call_count == 3


def test_complete_vision_with_retry_exhausts_and_raises(tmp_path: Path):
    img = tmp_path / "t.webp"
    img.write_bytes(b"\x00")
    client = VisionChatClient(base_url="https://x", api_key="k")

    with patch(
        "sciencevision_taskgen.llm.client.urllib.request.urlopen",
        side_effect=urllib.error.URLError("always fails"),
    ):
        with patch("time.sleep"):
            with pytest.raises(ChatClientError, match="3 attempts"):
                client.complete_vision_with_retry(
                    model="m",
                    system="s",
                    user_text="go",
                    image_paths=[img],
                    max_attempts=3,
                    backoff_sec=0.01,
                )
