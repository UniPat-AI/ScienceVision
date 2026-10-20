#!/usr/bin/env python3
"""Run one bounded, no-task-content DeepSeek multimodal dialect probe.

This script is intentionally narrower than the provider diagnostic command:
it never receives a QA task, scene reference, asset, or prompt from a batch.
It sends a fixed tiny PNG and records only safe response structure or the
adapter's categorical error receipt.  The output path must be new so each
probe is an immutable local-preproduction receipt.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

import httpx

from qagen.contracts import digest, make_envelope
from qagen.providers.contracts import PROVIDER_CONTRACTS
from qagen.providers.http import _safe_provider_error_receipt
from qagen.providers.runtime import load_provider_runtime, require_secure_https_transport
from qagen.storage.atomic import atomic_write_json


_TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Jf4sAAAAASUVORK5CYII="
)
_FIXED_TEXT = 'Return exactly {"ok":true}.'


def _request_body(*, include_thinking: bool, image_url_as_string: bool) -> dict[str, Any]:
    image_url: str | dict[str, str]
    if image_url_as_string:
        image_url = _TINY_PNG_DATA_URL
    else:
        image_url = {"url": _TINY_PNG_DATA_URL}
    body: dict[str, Any] = {
        "model": PROVIDER_CONTRACTS["deepseek"].model,
        "max_tokens": 64,
        "messages": [
            {"role": "system", "content": "Return exactly one JSON object."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _FIXED_TEXT},
                    {"type": "image_url", "image_url": image_url},
                ],
            },
        ],
    }
    if include_thinking:
        body["thinking"] = {"type": "disabled"}
    return body


def _success_shape(response: httpx.Response) -> dict[str, Any]:
    """Inspect only structural facts from a successful response."""

    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        return {"json_object": False, "choices_present": False, "usage_present": False}
    if not isinstance(payload, Mapping):
        return {"json_object": False, "choices_present": False, "usage_present": False}
    return {
        "json_object": True,
        "choices_present": isinstance(payload.get("choices"), list),
        "usage_present": isinstance(payload.get("usage"), Mapping),
    }


def _probe(
    client: httpx.Client,
    *,
    url: str,
    api_key: str,
    timeout_seconds: float,
    label: str,
    include_thinking: bool,
    image_url_as_string: bool,
) -> dict[str, Any]:
    body = _request_body(include_thinking=include_thinking, image_url_as_string=image_url_as_string)
    started = perf_counter()
    result: dict[str, Any] = {
        "label": label,
        "model": body["model"],
        "max_tokens": body["max_tokens"],
        "no_task_content": True,
        "image": "fixed_tiny_png_data_url",
        "content_shape": "openai_chat_image_url_string" if image_url_as_string else "openai_chat_image_url_object",
        "thinking": "disabled" if include_thinking else "omitted",
        "request_digest": digest(body),
        "attempts": 1,
    }
    try:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        result.update(
            {
                "status": "transport_failed",
                "network_called": True,
                "transport_error_class": type(exc).__name__,
                "latency_ms": round((perf_counter() - started) * 1000.0, 3),
            }
        )
        return result
    result.update(
        {
            "network_called": True,
            "http_status": response.status_code,
            "latency_ms": round((perf_counter() - started) * 1000.0, 3),
        }
    )
    if 200 <= response.status_code < 300:
        shape = _success_shape(response)
        result.update(
            {
                "status": "passed" if shape["json_object"] and shape["choices_present"] else "invalid_success_envelope",
                "response_shape": shape,
            }
        )
    else:
        result.update({"status": "http_error", "error_receipt": _safe_provider_error_receipt(response)})
    return result


def _run_matrix(settings: Any, *, timeout_seconds: float) -> list[dict[str, Any]]:
    contract = PROVIDER_CONTRACTS["deepseek"]
    url = f"{settings.base_url.rstrip('/')}{contract.path}"
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        current = _probe(
            client,
            url=url,
            api_key=settings.api_key,
            timeout_seconds=timeout_seconds,
            label="current_author_dialect",
            include_thinking=True,
            image_url_as_string=False,
        )
        results = [current]
        if current.get("http_status") == 400:
            without_thinking = _probe(
                client,
                url=url,
                api_key=settings.api_key,
                timeout_seconds=timeout_seconds,
                label="without_thinking",
                include_thinking=False,
                image_url_as_string=False,
            )
            results.append(without_thinking)
            error = without_thinking.get("error_receipt")
            if (
                without_thinking.get("http_status") == 400
                and isinstance(error, Mapping)
                and (error.get("message") == "invalid_multimodal_content" or error.get("field") == "image_url")
            ):
                results.append(
                    _probe(
                        client,
                        url=url,
                        api_key=settings.api_key,
                        timeout_seconds=timeout_seconds,
                        label="image_url_string_fallback",
                        include_thinking=False,
                        image_url_as_string=True,
                    )
                )
    return results


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--receipt-path", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if not 0 < args.timeout_seconds <= 30:
        raise ValueError("timeout_seconds must be in (0, 30]")
    receipt_path = args.receipt_path.expanduser().resolve()
    if receipt_path.exists():
        raise FileExistsError("dialect smoke receipt path already exists")
    settings = load_provider_runtime(args.runtime_config)
    require_secure_https_transport(settings, client=None)
    results = _run_matrix(settings, timeout_seconds=float(args.timeout_seconds))
    passed = next((item for item in results if item.get("status") == "passed"), None)
    body = {
        "scope": "local_preproduction_fresh5_only",
        "runtime_config_digest": settings.config_digest,
        "endpoint_approval_digest": settings.endpoint_approval_digest,
        "provider": "deepseek",
        "model": PROVIDER_CONTRACTS["deepseek"].model,
        "no_task_content": True,
        "fixed_input": {"text": "fixed_json_instruction", "image": "fixed_tiny_png_data_url"},
        "matrix": results,
        "selected_dialect": passed["label"] if passed is not None else None,
        "author_dialect_eligible": passed is not None,
        "release_eligible": False,
    }
    receipt = make_envelope(
        kind="qagen_deepseek_multimodal_dialect_smoke",
        identifier=f"deepseek-dialect-{settings.config_digest.removeprefix('sha256:')[:16]}",
        status="passed" if passed is not None else "blocked",
        body=body,
        producer="qagen/0.1.0:deepseek_multimodal_dialect_smoke",
    )
    atomic_write_json(receipt_path, receipt, private=True)
    print(
        {
            "receipt_path": str(receipt_path),
            "receipt_digest": receipt["content_digest"],
            "status": receipt["status"],
            "selected_dialect": body["selected_dialect"],
            "network_called": any(item.get("network_called") is True for item in results),
        }
    )
    return 0 if passed is not None else 3


if __name__ == "__main__":
    raise SystemExit(main())
