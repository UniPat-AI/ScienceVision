#!/usr/bin/env python3
"""Create a safe, immutable matrix from three no-task provider probes.

The matrix deliberately records only provider/model identity, envelope digest,
request shape, HTTP status, and categorical error outcome. It cannot include
credentials, prompts, assets, task text, images, or provider response bodies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from qagen.contracts import digest, make_envelope, verify_envelope
from qagen.storage.atomic import atomic_write_json


_EXPECTED = {
    "anthropic": ("qagen_opus_native_image_smoke", "body.result"),
    "deepseek": ("qagen_deepseek_multimodal_dialect_smoke", "body.matrix"),
    "openai": ("qagen_openai_tiny_image_smoke", "body.result"),
}


def _read_receipt(path: Path, *, provider: str) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {provider} capability receipt") from exc
    if not isinstance(raw, Mapping):
        raise ValueError(f"{provider} capability receipt must be an envelope")
    expected_kind, _field = _EXPECTED[provider]
    receipt = verify_envelope(raw, kind=expected_kind)
    body = receipt.get("body")
    if not isinstance(body, Mapping) or body.get("provider") != provider:
        raise ValueError(f"{provider} capability receipt provider binding is invalid")
    return receipt


def _error_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"http_status": None, "outcome": "invalid_probe_receipt"}
    safe: dict[str, Any] = {
        "http_status": value.get("http_status") if isinstance(value.get("http_status"), int) else None,
        "outcome": value.get("status") if isinstance(value.get("status"), str) else "invalid_probe_receipt",
    }
    error = value.get("error_receipt")
    if isinstance(error, Mapping):
        safe["error_category"] = error.get("message") if isinstance(error.get("message"), str) else "unknown"
        safe["error_field"] = error.get("field") if isinstance(error.get("field"), str) else "unknown"
    return safe


def _row(receipt: Mapping[str, Any], *, provider: str) -> dict[str, Any]:
    body = receipt["body"]
    model = body.get("model")
    if not isinstance(model, str):
        raise ValueError(f"{provider} capability receipt model is invalid")
    if provider == "deepseek":
        matrix = body.get("matrix")
        if not isinstance(matrix, list) or not matrix:
            raise ValueError("deepseek capability receipt has no probe matrix")
        outcomes = [_error_summary(item) for item in matrix]
        probe_shape = "openai_chat_image_url_object_and_string"
    else:
        outcomes = [_error_summary(body.get("result"))]
        probe_shape = "native_anthropic_base64_image" if provider == "anthropic" else "openai_chat_image_url_object"
    return {
        "provider": provider,
        "model": model,
        "probe_shape": probe_shape,
        "probe_outcomes": outcomes,
        "supports_image_input": False,
        "supports_data_url": False,
        "visual_delivery_approved": False,
        "receipt": {
            "kind": receipt["kind"],
            "id": receipt["id"],
            "content_digest": receipt["content_digest"],
        },
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--anthropic-receipt", required=True, type=Path)
    parser.add_argument("--deepseek-receipt", required=True, type=Path)
    parser.add_argument("--openai-receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError("visual capability matrix output path already exists")
    try:
        runtime = yaml.safe_load(args.runtime_config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("cannot read provider runtime config") from exc
    if not isinstance(runtime, Mapping):
        raise ValueError("provider runtime config must be an object")
    receipts = {
        "anthropic": _read_receipt(args.anthropic_receipt.expanduser().resolve(), provider="anthropic"),
        "deepseek": _read_receipt(args.deepseek_receipt.expanduser().resolve(), provider="deepseek"),
        "openai": _read_receipt(args.openai_receipt.expanduser().resolve(), provider="openai"),
    }
    rows = [_row(receipts[provider], provider=provider) for provider in ("anthropic", "deepseek", "openai")]
    matrix = make_envelope(
        kind="qagen_provider_visual_capability_matrix",
        identifier=f"fresh5-visual-capabilities-{digest(rows).removeprefix('sha256:')[:16]}",
        status="blocked",
        body={
            "scope": "local_preproduction_fresh5_only",
            "runtime_config_digest": digest(dict(runtime)),
            "rows": rows,
            "visual_delivery_status": "no_approved_provider_visual_transport",
            "live_generation_authorized": False,
            "fresh_v9_authorized": False,
            "required_unblock": "explicitly approve a different controlled visual transport, then run one new compatibility smoke and bind a new runtime approval",
        },
        producer="qagen/0.1.0:provider_visual_capability_matrix",
    )
    atomic_write_json(output, matrix, private=True)
    print({"output": str(output), "content_digest": matrix["content_digest"], "status": matrix["status"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
