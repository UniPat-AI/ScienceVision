"""Vision-capable extension of the OpenAI-compat ChatClient.

Adds ``complete_vision()`` which sends OpenAI-style ``image_url`` content
blocks (data URIs) alongside text.  Falls back to plain-text content when
the caller passes no images.

This lives next to ``client.py`` so it can share the same stdlib-only
transport.  Mindra's OpenAI-compat gateway accepts ``image_url`` blocks for
Claude vision models.
"""
from __future__ import annotations

import base64
import time
import urllib.error
from pathlib import Path
from typing import Iterable

from .client import ChatClient, ChatClientError, ChatResult


class VisionChatClient(ChatClient):
    """ChatClient subclass that supports an image_url content block array."""

    def complete_vision(
        self,
        model: str,
        system: str,
        user_text: str,
        image_paths: Iterable[Path],
        *,
        image_media_type: str = "image/webp",
        temperature: float = 0.0,
        json_mode: bool = True,
    ) -> ChatResult:
        """Send a chat completion with optional image content blocks.

        Args:
            image_paths: Iterable of paths to images to attach.  Empty
                iterable produces a text-only request (identical wire format
                to ``ChatClient.complete``).
            image_media_type: MIME type used in the data URI.  Caller is
                responsible for matching this to the actual file bytes.
        """
        images = list(image_paths)
        if images:
            parts: list[dict] = [{"type": "text", "text": user_text}]
            for p in images:
                data = Path(p).read_bytes()
                b64 = base64.b64encode(data).decode()
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_media_type};base64,{b64}"},
                })
            user_content: object = parts
        else:
            user_content = user_text

        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return self._post_chat_request(body)

    def complete_vision_with_retry(
        self,
        model: str,
        system: str,
        user_text: str,
        image_paths: Iterable[Path],
        *,
        image_media_type: str = "image/webp",
        temperature: float = 0.0,
        json_mode: bool = True,
        max_attempts: int = 3,
        backoff_sec: float = 2.0,
    ) -> ChatResult:
        """Call ``complete_vision`` with exponential back-off on transient failures.

        Retries on: ``urllib.error.HTTPError``, ``urllib.error.URLError``,
        ``TimeoutError``, and ``ChatClientError`` (e.g. empty-content quirk).

        Args:
            max_attempts: Maximum number of total attempts (including the first).
            backoff_sec: Base sleep duration; actual sleep is ``backoff_sec * attempt``.

        Raises:
            ChatClientError: After all attempts are exhausted.
        """
        image_list = list(image_paths)
        last_err: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self.complete_vision(
                    model=model,
                    system=system,
                    user_text=user_text,
                    image_paths=image_list,
                    image_media_type=image_media_type,
                    temperature=temperature,
                    json_mode=json_mode,
                )
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
            f"complete_vision failed after {max_attempts} attempts: {last_err!r}"
        )
