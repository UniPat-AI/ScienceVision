"""Stdlib-only OpenAI-compat HTTP client targeting the Mindra gateway.

Ported from scripts/v_classify/client.py; the two copies may diverge — that
is intentional.  Do NOT import from scripts/.

Notable additions vs. the scripts/ version:
- ``ChatClientError`` — single catch-all exception type for callers.
- Gemini-3.1-pro-preview quirk: empty content with completion_tokens > 0
  raises ``ChatClientError`` so ``complete_with_retry`` will retry it.
- ``complete_with_retry`` catches ``ChatClientError`` in addition to
  urllib-level errors.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class ChatClientError(RuntimeError):
    """Raised by ChatClient when a call cannot produce usable content.

    Callers in builder/challenger stages should catch this and record a
    fallback UsageRecord rather than letting it propagate.
    """


@dataclass(frozen=True)
class ChatResult:
    content: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int


class ChatClient:
    """Minimal OpenAI-compat client.  Stdlib-only; no httpx/openai/anthropic."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_sec: float = 90.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_sec

    def _post_chat_request(self, body: dict) -> ChatResult:
        """POST ``body`` to ``/chat/completions`` and parse the response.

        Shared between ``complete`` and subclasses (e.g. VisionChatClient).
        Subclasses MUST go through this rather than touching ``self._base_url``
        / ``self._api_key`` / ``self._timeout`` directly.

        Raises:
            ChatClientError: On non-JSON response, unexpected shape, or
                empty content with non-zero completion_tokens.
            urllib.error.HTTPError / URLError / TimeoutError: Propagated for
                ``complete_with_retry`` to handle.
        """
        req = urllib.request.Request(
            url=f"{self._base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            raw_bytes = resp.read()
        latency_ms = (time.monotonic() - t0) * 1000.0

        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ChatClientError(f"non-JSON response: {exc!r}") from exc

        try:
            choice = payload["choices"][0]
            content: str = choice["message"]["content"] or ""
            usage = payload.get("usage", {}) or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatClientError(f"unexpected response shape: {exc!r}") from exc

        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        completion_details = usage.get("completion_tokens_details") or {}

        if not content and completion_tokens > 0:
            raise ChatClientError(
                "empty content despite token usage "
                f"(completion_tokens={completion_tokens}); "
                "likely a reasoning-token quirk — retrying"
            )

        return ChatResult(
            content=content,
            latency_ms=latency_ms,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=completion_tokens,
            reasoning_tokens=int(
                completion_details.get("reasoning_tokens", 0) or 0
            ),
        )

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        json_mode: bool = True,
    ) -> ChatResult:
        """Send a single chat completion request.

        Raises:
            ChatClientError: If the response content is empty despite token
                usage (gemini-3.1-pro-preview reasoning-token quirk), or if
                the HTTP response cannot be parsed.
            urllib.error.HTTPError: On 4xx/5xx responses.
            urllib.error.URLError: On network-level failures.
            TimeoutError: If the server takes longer than ``timeout_sec``.
        """
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return self._post_chat_request(body)

    def complete_with_retry(
        self,
        model: str,
        system: str,
        user: str,
        *,
        max_attempts: int = 3,
        backoff_sec: float = 2.0,
        **kwargs,
    ) -> ChatResult:
        """Call ``complete`` with exponential back-off on transient failures.

        Retries on: ``urllib.error.HTTPError``, ``urllib.error.URLError``,
        ``TimeoutError``, and ``ChatClientError`` (e.g. empty-content quirk).

        Raises:
            ChatClientError: After all attempts are exhausted.
        """
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self.complete(model, system, user, **kwargs)
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                ChatClientError,
            ) as exc:
                last_err = exc
                if attempt == max_attempts:
                    break
                time.sleep(backoff_sec * attempt)
        raise ChatClientError(
            f"chat.complete failed after {max_attempts} attempts: {last_err!r}"
        )
