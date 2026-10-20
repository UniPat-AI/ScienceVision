"""Tests for sciencevision_taskgen.llm.client.

Network calls are intercepted by monkeypatching urllib.request.urlopen.
No real HTTP calls are made.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest

from sciencevision_taskgen.llm.client import ChatClient, ChatClientError, ChatResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(payload: dict) -> MagicMock:
    """Return a mock context-manager that yields a file-like object."""
    raw = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = raw
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _normal_payload(
    content: str = '{"result": 42}',
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    reasoning_tokens: int = 0,
) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


def _empty_content_payload(completion_tokens: int = 50) -> dict:
    """Simulate gemini-3.1-pro-preview quirk: empty content + token usage."""
    return _normal_payload(content="", completion_tokens=completion_tokens)


def _client() -> ChatClient:
    return ChatClient("https://gw.example.com/v1", "test-key", timeout_sec=5.0)


# ---------------------------------------------------------------------------
# ChatClient.complete — happy path
# ---------------------------------------------------------------------------


class TestComplete:
    def test_returns_chat_result_on_success(self) -> None:
        mock_resp = _make_response(_normal_payload())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _client().complete("model-x", "sys", "user")
        assert isinstance(result, ChatResult)
        assert result.content == '{"result": 42}'
        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50

    def test_latency_ms_is_positive(self) -> None:
        mock_resp = _make_response(_normal_payload())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _client().complete("model-x", "sys", "user")
        assert result.latency_ms >= 0.0

    def test_reasoning_tokens_parsed(self) -> None:
        payload = _normal_payload(reasoning_tokens=25)
        mock_resp = _make_response(payload)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _client().complete("model-x", "sys", "user")
        assert result.reasoning_tokens == 25

    def test_json_mode_adds_response_format(self) -> None:
        """Verify that the request body includes response_format when json_mode=True."""
        captured: list[dict] = []

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            captured.append(body)
            return _make_response(_normal_payload())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _client().complete("model-x", "sys", "user", json_mode=True)
        assert captured[0].get("response_format") == {"type": "json_object"}

    def test_no_json_mode_omits_response_format(self) -> None:
        captured: list[dict] = []

        def fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            captured.append(body)
            return _make_response(_normal_payload())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _client().complete("model-x", "sys", "user", json_mode=False)
        assert "response_format" not in captured[0]


# ---------------------------------------------------------------------------
# Gemini empty-content quirk
# ---------------------------------------------------------------------------


class TestEmptyContentQuirk:
    def test_empty_content_with_tokens_raises(self) -> None:
        mock_resp = _make_response(_empty_content_payload(completion_tokens=50))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ChatClientError, match="empty content"):
                _client().complete("gemini-3.1-pro-preview", "sys", "user")

    def test_empty_content_zero_tokens_does_not_raise(self) -> None:
        """Zero completion tokens + empty content is not the quirk."""
        mock_resp = _make_response(_empty_content_payload(completion_tokens=0))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _client().complete("some-model", "sys", "user")
        assert result.content == ""
        assert result.completion_tokens == 0


# ---------------------------------------------------------------------------
# ChatClient.complete_with_retry
# ---------------------------------------------------------------------------


class TestCompleteWithRetry:
    def test_success_on_first_attempt(self) -> None:
        mock_resp = _make_response(_normal_payload())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _client().complete_with_retry(
                "model-x", "sys", "user", max_attempts=3, backoff_sec=0.0
            )
        assert result.content == '{"result": 42}'

    def test_retries_on_url_error_then_succeeds(self) -> None:
        attempts: list[int] = []

        def fake_urlopen(req, timeout=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise urllib.error.URLError("temporary")
            return _make_response(_normal_payload())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):  # skip actual sleeping
                result = _client().complete_with_retry(
                    "model-x", "sys", "user", max_attempts=3, backoff_sec=0.01
                )
        assert len(attempts) == 3
        assert result.content == '{"result": 42}'

    def test_all_failures_raise_chat_client_error(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("always fails"),
        ):
            with patch("time.sleep"):
                with pytest.raises(ChatClientError, match="failed after"):
                    _client().complete_with_retry(
                        "model-x", "sys", "user", max_attempts=2, backoff_sec=0.0
                    )

    def test_retries_on_chat_client_error(self) -> None:
        """ChatClientError (e.g. empty-content quirk) should also trigger retry."""
        attempts: list[int] = []

        def fake_urlopen(req, timeout=None):
            attempts.append(1)
            if len(attempts) < 3:
                # Return the quirk payload
                return _make_response(_empty_content_payload(completion_tokens=10))
            return _make_response(_normal_payload())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):
                result = _client().complete_with_retry(
                    "gemini-3.1-pro-preview",
                    "sys",
                    "user",
                    max_attempts=3,
                    backoff_sec=0.0,
                )
        assert len(attempts) == 3
        assert result.content == '{"result": 42}'

    def test_http_error_retried(self) -> None:
        http_err = urllib.error.HTTPError(
            url="http://x", code=500, msg="Internal Server Error",
            hdrs=None, fp=None  # type: ignore[arg-type]
        )
        calls: list[int] = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) < 2:
                raise http_err
            return _make_response(_normal_payload())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("time.sleep"):
                result = _client().complete_with_retry(
                    "model-x", "sys", "user", max_attempts=3, backoff_sec=0.0
                )
        assert result.content == '{"result": 42}'


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class TestChatClientError:
    def test_is_runtime_error_subclass(self) -> None:
        err = ChatClientError("boom")
        assert isinstance(err, RuntimeError)

    def test_message_preserved(self) -> None:
        err = ChatClientError("something went wrong")
        assert "something went wrong" in str(err)


# ---------------------------------------------------------------------------
# Response shape / JSON error handling
# ---------------------------------------------------------------------------


class TestResponseShapeErrors:
    def _make_raw_response(self, body: bytes) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_missing_choices_raises_chatclienterror(self) -> None:
        payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        mock_resp = self._make_raw_response(json.dumps(payload).encode("utf-8"))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ChatClientError, match="unexpected response shape"):
                _client().complete("model-x", "sys", "user")

    def test_non_json_response_raises_chatclienterror(self) -> None:
        mock_resp = self._make_raw_response(b"<html>error</html>")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with pytest.raises(ChatClientError, match="non-JSON response"):
                _client().complete("model-x", "sys", "user")
