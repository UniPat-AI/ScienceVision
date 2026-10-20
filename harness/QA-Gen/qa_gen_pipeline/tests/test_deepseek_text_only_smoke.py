from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from qagen.contracts import digest
from qagen.providers.contracts import (
    MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
    PROVIDER_CONTRACTS,
)
from qagen.providers.http import HTTPProviderAdapter, ProviderAdapterError
from qagen.providers.runtime import ProviderRuntimeSettings
from qagen.providers.text_only_smoke import (
    TextOnlySmokeError,
    build_deepseek_author_text_only_request,
    run_deepseek_author_text_only_smoke,
)
from qagen.workers import WorkerRequest


def _settings() -> ProviderRuntimeSettings:
    return ProviderRuntimeSettings(
        config_path=Path("/private/qagen-test-runtime.yaml"),
        config_digest=digest({"runtime": "text-only-smoke"}),
        base_url="https://provider.test",
        api_key="not-for-output",
        enabled_providers={"deepseek": True},
        provider_capabilities={
            "deepseek": {"supports_image_input": False, "supports_data_url": False}
        },
        endpoint_approval_digest=digest({"approval": "text-only-smoke"}),
    )


def test_text_only_smoke_uses_current_author_wire_dialect_without_visual_fields() -> (
    None
):
    body = build_deepseek_author_text_only_request(max_tokens=64)

    serialized = json.dumps(body, sort_keys=True)
    assert body["model"] == "deepseek-v4-pro"
    assert body["max_tokens"] == 64
    assert body["thinking"] == {"type": "disabled"}
    assert all(isinstance(message["content"], str) for message in body["messages"])
    assert "image" not in serialized
    assert "data:" not in serialized


@pytest.mark.parametrize("value", (0, 15, 129, True))
def test_text_only_smoke_rejects_an_unbounded_or_invalid_output_limit(
    value: int,
) -> None:
    with pytest.raises(TextOnlySmokeError, match="max_tokens_invalid"):
        build_deepseek_author_text_only_request(max_tokens=value)


def test_text_only_smoke_keeps_fixed_response_content_out_of_its_result() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_deepseek_author_text_only_smoke(
            _settings(),
            timeout_seconds=5.0,
            max_tokens=64,
            client=client,
        )

    assert result["status"] == "passed"
    assert result["attempts"] == 1
    assert result["usage"] == {"prompt_tokens": 12, "completion_tokens": 4}
    assert result["fixed_structured_response"] is True
    assert '{"ok":true}' not in json.dumps(result, sort_keys=True)
    assert len(seen) == 1
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert "image" not in json.dumps(seen[0], sort_keys=True)
    assert "data:" not in json.dumps(seen[0], sort_keys=True)


def test_text_only_smoke_retains_only_a_safe_provider_error_receipt() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "internal-secret-error",
                    "type": "invalid_request_error",
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_deepseek_author_text_only_smoke(
            _settings(),
            timeout_seconds=5.0,
            max_tokens=64,
            client=client,
        )

    assert result["status"] == "http_error"
    assert result["error_receipt"]["message"] == "provider_rejected_request"
    assert "internal-secret-error" not in json.dumps(result, sort_keys=True)


def test_text_only_measurement_replay_is_an_explicit_narrow_adapter_override() -> None:
    request = WorkerRequest(
        role="author",
        provider="deepseek",
        model="deepseek-v4-pro",
        input_contract={"execution_mode": "measurement_replay_text_only"},
        max_tokens=5_000,
        max_cost_usd=1.0,
        run_id="text-only-test",
        work_item_id="one-slot",
    )
    common = {
        "contract": PROVIDER_CONTRACTS["deepseek"],
        "base_url": "https://provider.test",
        "api_key": "not-for-output",
        "role_schemas": {"author": {"type": "object"}},
        "supports_image_input": False,
        "supports_data_url": False,
        "timeout_seconds": 5.0,
        "max_attempts": 1,
        "initial_backoff_seconds": 0.0,
    }
    default = HTTPProviderAdapter(**common)
    with pytest.raises(ProviderAdapterError, match="visual provider worker"):
        default.prompt_budget(request)

    enabled = HTTPProviderAdapter(
        **common,
        execution_profile=MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
    )
    assert enabled.prompt_budget(request).api_max_tokens > 0


def test_text_only_measurement_replay_requires_the_explicit_mode_for_verifier() -> None:
    common = {
        "contract": PROVIDER_CONTRACTS["openai"],
        "base_url": "https://provider.test",
        "api_key": "not-for-output",
        "role_schemas": {"verifier": {"type": "object"}},
        "supports_image_input": False,
        "supports_data_url": False,
        "timeout_seconds": 5.0,
        "max_attempts": 1,
        "initial_backoff_seconds": 0.0,
        "execution_profile": MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
    }
    adapter = HTTPProviderAdapter(**common)
    missing_mode = WorkerRequest(
        role="verifier",
        provider="openai",
        model="gpt-5.6-sol",
        input_contract={"verification_mode": "measurement_replay_text_only"},
        max_tokens=5_000,
        max_cost_usd=1.0,
        run_id="text-only-test",
        work_item_id="one-slot",
    )
    with pytest.raises(ProviderAdapterError, match="profile input is invalid"):
        adapter.prompt_budget(missing_mode)

    enabled = WorkerRequest(
        role="verifier",
        provider="openai",
        model="gpt-5.6-sol",
        input_contract={
            "execution_mode": "measurement_replay_text_only",
            "verification_mode": "measurement_replay_text_only",
        },
        max_tokens=5_000,
        max_cost_usd=1.0,
        run_id="text-only-test",
        work_item_id="one-slot",
    )
    assert adapter.prompt_budget(enabled).api_max_tokens > 0
