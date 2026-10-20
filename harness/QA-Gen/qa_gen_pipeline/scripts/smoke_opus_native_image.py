#!/usr/bin/env python3
"""Run one bounded native-Anthropic tiny-image compatibility probe.

No QA task, scene identifier, asset, viewer receipt, or batch prompt is sent.
The persisted receipt contains only structural response facts or a sanitized
HTTP-error category. Its output path must not exist, preserving probe history.
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


_TINY_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Jf4sAAAAASUVORK5CYII="


def _request_body() -> dict[str, Any]:
    return {
        "model": PROVIDER_CONTRACTS["anthropic"].model,
        "max_tokens": 64,
        "system": "Return exactly one JSON object.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": 'Return exactly {"ok":true}.'},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": _TINY_PNG_BASE64,
                        },
                    },
                ],
            }
        ],
    }


def _success_shape(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        return {"json_object": False, "content_present": False, "usage_present": False}
    if not isinstance(payload, Mapping):
        return {"json_object": False, "content_present": False, "usage_present": False}
    return {
        "json_object": True,
        "content_present": isinstance(payload.get("content"), (list, str)),
        "usage_present": isinstance(payload.get("usage"), Mapping),
    }


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
        raise FileExistsError("Opus native-image smoke receipt path already exists")
    settings = load_provider_runtime(args.runtime_config)
    require_secure_https_transport(settings, client=None)
    contract = PROVIDER_CONTRACTS["anthropic"]
    body = _request_body()
    result: dict[str, Any] = {
        "label": "native_anthropic_base64_image",
        "model": contract.model,
        "max_tokens": body["max_tokens"],
        "no_task_content": True,
        "image": "fixed_tiny_png_base64",
        "request_digest": digest(body),
        "attempts": 1,
    }
    started = perf_counter()
    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"{settings.base_url.rstrip('/')}{contract.path}",
                headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=float(args.timeout_seconds),
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
    else:
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
                    "status": "passed" if shape["json_object"] and shape["content_present"] else "invalid_success_envelope",
                    "response_shape": shape,
                }
            )
        else:
            result.update({"status": "http_error", "error_receipt": _safe_provider_error_receipt(response)})
    status = "passed" if result.get("status") == "passed" else "blocked"
    receipt = make_envelope(
        kind="qagen_opus_native_image_smoke",
        identifier=f"opus-native-image-{settings.config_digest.removeprefix('sha256:')[:16]}",
        status=status,
        body={
            "scope": "local_preproduction_fresh5_only",
            "runtime_config_digest": settings.config_digest,
            "endpoint_approval_digest": settings.endpoint_approval_digest,
            "provider": "anthropic",
            "model": contract.model,
            "no_task_content": True,
            "result": result,
            "brief_builder_visual_eligible": status == "passed",
            "release_eligible": False,
        },
        producer="qagen/0.1.0:opus_native_image_smoke",
    )
    atomic_write_json(receipt_path, receipt, private=True)
    print(
        {
            "receipt_path": str(receipt_path),
            "receipt_digest": receipt["content_digest"],
            "status": status,
            "network_called": result["network_called"],
        }
    )
    return 0 if status == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
