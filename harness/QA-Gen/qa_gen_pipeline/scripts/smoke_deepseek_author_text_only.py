#!/usr/bin/env python3
"""Run one fixed, no-task DeepSeek author-dialect text-only smoke.

The probe sends no task, asset, scene reference, image, or data URL.  It uses
one bounded OpenAI-compatible request with the current DeepSeek author dialect
and records only content-free response structure, usage counters, and digests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qagen.contracts import make_envelope
from qagen.providers.runtime import (
    load_provider_runtime,
    require_secure_https_transport,
)
from qagen.providers.text_only_smoke import run_deepseek_author_text_only_smoke
from qagen.storage.atomic import atomic_write_json


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--receipt-path", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--max-tokens", type=int, default=64)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    receipt_path = args.receipt_path.expanduser().resolve()
    if receipt_path.exists():
        raise FileExistsError("text-only smoke receipt path already exists")
    settings = load_provider_runtime(args.runtime_config)
    require_secure_https_transport(settings, client=None)
    result = run_deepseek_author_text_only_smoke(
        settings,
        timeout_seconds=float(args.timeout_seconds),
        max_tokens=int(args.max_tokens),
    )
    body = {
        "scope": "local_preproduction_fixed_no_task_deepseek_author_dialect",
        "runtime_config_digest": settings.config_digest,
        "endpoint_approval_digest": settings.endpoint_approval_digest,
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "no_task_content": True,
        "text_only": True,
        "image_or_data_url_present": False,
        "result": result,
        "author_dialect_eligible": result["status"] == "passed",
        "release_eligible": False,
    }
    receipt = make_envelope(
        kind="qagen_deepseek_author_text_only_smoke",
        identifier=f"deepseek-author-text-only-{settings.config_digest.removeprefix('sha256:')[:16]}",
        status="passed" if result["status"] == "passed" else "blocked",
        body=body,
        producer="qagen/0.1.0:deepseek_author_text_only_smoke",
    )
    atomic_write_json(receipt_path, receipt, private=True)
    print(
        json.dumps(
            {
                "receipt_path": str(receipt_path),
                "receipt_digest": receipt["content_digest"],
                "status": receipt["status"],
                "network_called": result["network_called"],
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
