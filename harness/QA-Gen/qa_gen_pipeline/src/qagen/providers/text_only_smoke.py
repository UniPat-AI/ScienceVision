"""Fixed DeepSeek author-dialect probe with no task or visual input.

The probe is deliberately independent of the worker graph.  It exercises the
same OpenAI-compatible DeepSeek request dialect used by the author route,
while accepting only one known structured response and retaining no prompt or
response text in its result.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Mapping

import httpx

from ..contracts import digest
from .contracts import PROVIDER_CONTRACTS
from .http import _safe_provider_error_receipt
from .runtime import ProviderRuntimeSettings


class TextOnlySmokeError(ValueError):
    """The fixed no-task provider probe is not safe to execute."""


_FIXED_SYSTEM = "You are the QA-Gen author protocol probe. Return exactly one JSON object and no markdown."
_FIXED_USER = 'Return exactly {"ok":true}.'
_FORBIDDEN_KEYS = frozenset({"image", "image_url", "data_url", "input_image"})


def _assert_text_only(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS:
                raise TextOnlySmokeError("text_only_smoke_request_is_not_text_only")
            _assert_text_only(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_text_only(nested)
    elif isinstance(value, str) and value.casefold().startswith("data:"):
        raise TextOnlySmokeError("text_only_smoke_request_is_not_text_only")


def build_deepseek_author_text_only_request(*, max_tokens: int) -> dict[str, Any]:
    """Build the current DeepSeek author wire dialect without task content."""

    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 16 <= max_tokens <= 128
    ):
        raise TextOnlySmokeError("text_only_smoke_max_tokens_invalid")
    contract = PROVIDER_CONTRACTS["deepseek"]
    if (
        contract.wire_protocol != "openai_chat_completions"
        or contract.thinking != "disabled"
    ):
        raise TextOnlySmokeError("text_only_smoke_author_dialect_unavailable")
    body = {
        "model": contract.model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": _FIXED_SYSTEM},
            {"role": "user", "content": _FIXED_USER},
        ],
        "thinking": {"type": "disabled"},
    }
    _assert_text_only(body)
    return body


def _usage(payload: Mapping[str, Any]) -> dict[str, int] | None:
    value = payload.get("usage")
    if not isinstance(value, Mapping):
        return None
    prompt_tokens = value.get("prompt_tokens")
    completion_tokens = value.get("completion_tokens")
    if (
        isinstance(prompt_tokens, bool)
        or not isinstance(prompt_tokens, int)
        or prompt_tokens < 0
        or isinstance(completion_tokens, bool)
        or not isinstance(completion_tokens, int)
        or completion_tokens < 0
    ):
        return None
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}


def _response_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"json_object": False, "choices_present": False, "usage_present": False}
    choices = payload.get("choices")
    return {
        "json_object": True,
        "choices_present": isinstance(choices, list),
        "usage_present": isinstance(payload.get("usage"), Mapping),
        "top_level_keys": sorted(str(key) for key in payload)[:32],
    }


def _is_exact_fixed_response(payload: Mapping[str, Any]) -> bool:
    try:
        choices = payload["choices"]
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], Mapping)
        ):
            return False
        message = choices[0]["message"]
        if not isinstance(message, Mapping) or not isinstance(
            message.get("content"), str
        ):
            return False
        return json.loads(message["content"]) == {"ok": True}
    except (KeyError, TypeError, json.JSONDecodeError):
        return False


def run_deepseek_author_text_only_smoke(
    settings: ProviderRuntimeSettings,
    *,
    timeout_seconds: float,
    max_tokens: int,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Issue one bounded text-only request and return a content-free receipt body."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < float(timeout_seconds) <= 30
    ):
        raise TextOnlySmokeError("text_only_smoke_timeout_invalid")
    if settings.enabled_providers.get("deepseek") is not True:
        raise TextOnlySmokeError("text_only_smoke_provider_disabled")
    capabilities = settings.provider_capabilities.get("deepseek")
    if (
        not isinstance(capabilities, Mapping)
        or capabilities.get("supports_image_input") is not False
        or capabilities.get("supports_data_url") is not False
    ):
        raise TextOnlySmokeError("text_only_smoke_runtime_capability_not_restricted")
    body = build_deepseek_author_text_only_request(max_tokens=max_tokens)
    contract = PROVIDER_CONTRACTS["deepseek"]
    request_digest = digest(body)
    started = perf_counter()
    owned_client = client is None
    active_client = client or httpx.Client(follow_redirects=False, trust_env=False)
    try:
        try:
            response = active_client.post(
                f"{settings.base_url.rstrip('/')}{contract.path}",
                headers={
                    "Authorization": f"Bearer {settings.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=float(timeout_seconds),
            )
        except httpx.HTTPError as exc:
            return {
                "status": "transport_failed",
                "network_called": True,
                "request_digest": request_digest,
                "max_tokens": max_tokens,
                "timeout_seconds": float(timeout_seconds),
                "attempts": 1,
                "latency_ms": round((perf_counter() - started) * 1000.0, 3),
                "transport_error_class": type(exc).__name__,
            }
        result: dict[str, Any] = {
            "network_called": True,
            "request_digest": request_digest,
            "max_tokens": max_tokens,
            "timeout_seconds": float(timeout_seconds),
            "attempts": 1,
            "http_status": response.status_code,
            "latency_ms": round((perf_counter() - started) * 1000.0, 3),
        }
        if not 200 <= response.status_code < 300:
            result.update(
                {
                    "status": "http_error",
                    "error_receipt": _safe_provider_error_receipt(response),
                }
            )
            return result
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            result.update(
                {
                    "status": "invalid_success_envelope",
                    "response_shape": {
                        "json_object": False,
                        "choices_present": False,
                        "usage_present": False,
                    },
                }
            )
            return result
        shape = _response_shape(payload)
        usage = _usage(payload) if isinstance(payload, Mapping) else None
        result.update(
            {
                "status": "passed"
                if isinstance(payload, Mapping)
                and _is_exact_fixed_response(payload)
                and usage is not None
                else "invalid_success_envelope",
                "response_shape": shape,
                "usage": usage,
                "fixed_structured_response": isinstance(payload, Mapping)
                and _is_exact_fixed_response(payload),
            }
        )
        return result
    finally:
        if owned_client:
            active_client.close()


__all__ = [
    "TextOnlySmokeError",
    "build_deepseek_author_text_only_request",
    "run_deepseek_author_text_only_smoke",
]
