from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
import pytest
import yaml

from qagen.cli import build_parser, main
from qagen.contracts import digest
from qagen.paths import WorkspacePaths
from qagen.providers import (
    ProviderAdapterError,
    ProviderRuntimeConfigError,
    build_provider_adapters,
    diagnose_provider_runtime,
    require_provider_multimodal_capability,
)
from qagen.providers.contracts import MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE
import qagen.providers.http as provider_http
from qagen.workers import WorkerExecutor, WorkerRequest, verify_routing_policy


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TEMPLATE = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.example.yaml"
PILOT_RUNTIME = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.pilot.yaml"
PILOT_ROUTING = PACKAGE_ROOT / "configs" / "agents" / "model_routing.pilot.yaml"
API_KEY_ENV = "QAGEN_UNIV_API_KEY"
BASE_URL_ENV = "QAGEN_UNIV_BASE_URL"
APPROVAL_PATH_ENV = "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH"
SECRET = "test-secret-that-must-not-appear"
TEST_ENDPOINT = "https://provider-runtime.test"
INPUT_SCHEMA_ID = "qagen://schemas/provider-test/input/v1"
OUTPUT_SCHEMA_ID = "qagen://schemas/provider-test/output/v1"
SCHEMAS = {
    INPUT_SCHEMA_ID: {"type": "object"},
    OUTPUT_SCHEMA_ID: {
        "type": "object",
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
        "additionalProperties": False,
    },
}

_VISUAL_ROLES = {"author", "evidence_worker", "verifier"}
_ROLE_MAX_TOKENS = {
    "brief_builder": 9500,
    "author": 20000,
    "truth_builder": 19000,
    "evidence_worker": 14000,
    "verifier": 18500,
    "rewriter": 19000,
}


def _visual_input_contract() -> dict[str, Any]:
    views = []
    for index in range(2):
        png = b"\x89PNG\r\n\x1a\n" + f"diagnostic-{index}".encode("ascii")
        views.append(
            {
                "mime_type": "image/png",
                "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                "receipt_digest": digest({"receipt": index}),
                "render_digest": digest({"render": index}),
            }
        )
    return {
        "root_input": {
            "provider_science_bridge": {
                "artifact_kind": "approved_local_chirality_science_bridge",
                "schema_version": 1,
                "leaf_id": "chemistry.stereochemistry_chirality",
                "work_item_id": "provider-test-work-item",
                "lock_bindings": {"viewer_profile_digest": digest({"viewer": "profile"})},
                "viewer_evidence": {"rendered_views": views},
            }
        }
    }


def _runtime_config(*, enabled: bool) -> dict[str, Any]:
    value = yaml.safe_load(RUNTIME_TEMPLATE.read_text(encoding="utf-8"))
    value["enabled"] = enabled
    for provider in value["providers"].values():
        provider["enabled"] = enabled
    return value


def _write(tmp_path: Path, value: dict[str, Any]) -> Path:
    path = tmp_path / "provider-runtime.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def _runtime_environ(
    tmp_path: Path,
    runtime_path: Path,
    *,
    api_key: str = SECRET,
    endpoint: str = TEST_ENDPOINT,
    approval_endpoint: str | None = None,
) -> dict[str, str]:
    """Provide a per-test external endpoint approval without committing an endpoint."""
    config = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    approval = {
        "schema_version": 1,
        "kind": "qagen_provider_endpoint_approval",
        "id": "runtime-test-endpoint-approval",
        "status": "approved",
        "decision": "allow_credentialed_https_endpoint",
        "endpoint": approval_endpoint if approval_endpoint is not None else endpoint,
        "credential_env": API_KEY_ENV,
        "runtime_config_digest": digest(config),
        "scope": "test-only local provider adapter validation",
        "decided_by": "qagen-tests",
        "decided_at": "2026-08-26",
        "production_eligible": False,
    }
    # A temp runtime config must not approve itself from its own directory.
    approval_root = tmp_path if runtime_path.parent != tmp_path else tmp_path.parents[1]
    approval_path = approval_root / f"{tmp_path.name}-endpoint-approval.yaml"
    approval_path.write_text(yaml.safe_dump(approval, sort_keys=False), encoding="utf-8")
    return {
        API_KEY_ENV: api_key,
        BASE_URL_ENV: endpoint,
        APPROVAL_PATH_ENV: str(approval_path),
    }


def _verified_pilot_policy():
    value = yaml.safe_load(PILOT_ROUTING.read_text(encoding="utf-8"))
    for role in value["roles"].values():
        role["input_schema"] = INPUT_SCHEMA_ID
        role["output_schema"] = OUTPUT_SCHEMA_ID
    return verify_routing_policy(value, schema_registry=SCHEMAS)


def _request(provider: str, model: str, role: str, *, max_cost_usd: float = 0.4) -> WorkerRequest:
    return WorkerRequest(
        role=role,
        provider=provider,
        model=model,
        input_contract=_visual_input_contract() if role in _VISUAL_ROLES else {"task_id": "private-task-content"},
        max_tokens=_ROLE_MAX_TOKENS[role],
        max_cost_usd=max_cost_usd,
        run_id="provider-test-run",
        work_item_id=f"provider-test-{provider}",
    )


def _response(request: httpx.Request, *, cost: float | None = 0.01) -> httpx.Response:
    body = json.loads(request.content)
    usage: dict[str, Any]
    if body["model"] == "claude-opus-5":
        usage = {"input_tokens": 2, "output_tokens": 1}
        payload: dict[str, Any] = {
            "model": body["model"],
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "usage": usage,
        }
    else:
        usage = {"prompt_tokens": 2, "completion_tokens": 1}
        payload = {
            "model": body["model"],
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": usage,
        }
    if cost is not None:
        usage["cost_usd"] = cost
    return httpx.Response(200, json=payload)


def test_disabled_template_is_offline_and_fail_closed() -> None:
    result = diagnose_provider_runtime(RUNTIME_TEMPLATE, environ={})

    assert result["status"] == "blocked"
    assert result["execution_eligible"] is False
    assert result["network_called"] is False
    assert result["adapter_status"] == "implemented"
    assert {item["model"] for item in result["providers"]} == {
        "claude-opus-5",
        "deepseek-v4-pro",
        "gpt-5.6-sol",
    }
    assert all(item["enabled"] is False for item in result["providers"])


def test_smoke_without_key_is_explicitly_skipped_and_never_calls_transport(tmp_path: Path) -> None:
    def fail_if_called(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("network transport must not be called without a credential")

    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        result = diagnose_provider_runtime(
            PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME, api_key=""),
            smoke=True,
            client=client,
        )

    assert result["status"] == "blocked"
    assert result["network_called"] is False
    assert result["blocked_reasons"] == [f"credential_env_missing:{API_KEY_ENV}"]
    assert {item["status"] for item in result["smoke"]} == {"skipped"}
    assert all(item["reason"] == "runtime_not_ready" for item in result["smoke"])


def test_mock_smoke_uses_all_three_approved_paths_without_returning_secrets(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"model": json.loads(request.content)["model"], "usage": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = diagnose_provider_runtime(
            PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            smoke=True,
            client=client,
            sleeper=lambda _seconds: None,
        )

    assert result["status"] == "approved_local_pilot_ready"
    assert result["execution_eligible"] is True
    assert result["network_called"] is True
    assert [request.url.path for request in seen] == ["/v1/messages", "/v1/chat/completions", "/v1/chat/completions"]
    assert all(request.headers["Authorization"] == f"Bearer {SECRET}" for request in seen)
    assert SECRET not in repr(result)


def test_missing_multimodal_capability_blocks_before_transport(tmp_path: Path) -> None:
    config = _runtime_config(enabled=True)
    config["providers"]["deepseek"]["multimodal"].pop("supports_data_url")
    runtime_path = _write(tmp_path, config)
    calls: list[httpx.Request] = []

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("a provider transport must not run without an explicit data-url capability")

    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        with pytest.raises(ProviderRuntimeConfigError, match="must declare image and data-url support explicitly"):
            build_provider_adapters(
                _verified_pilot_policy(),
                runtime_config_path=runtime_path,
                environ=_runtime_environ(tmp_path, runtime_path),
                client=client,
            )

    assert calls == []


def test_v32_visual_capabilities_are_fail_closed_before_any_transport() -> None:
    v32_runtime = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml"
    with pytest.raises(ProviderRuntimeConfigError, match="does not explicitly support image/data-url input"):
        require_provider_multimodal_capability(
            v32_runtime,
            expected_config_digest=digest(yaml.safe_load(v32_runtime.read_text(encoding="utf-8"))),
            providers=("anthropic", "deepseek", "openai"),
        )


def test_standard_visual_profile_rejects_false_runtime_capabilities_before_transport(tmp_path: Path) -> None:
    v32_runtime = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml"
    calls: list[httpx.Request] = []

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("a visual profile must fail before transport when image delivery is unavailable")

    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        with pytest.raises(ProviderRuntimeConfigError, match="visual execution profile"):
            build_provider_adapters(
                _verified_pilot_policy(),
                runtime_config_path=v32_runtime,
                environ=_runtime_environ(tmp_path, v32_runtime),
                client=client,
            )

    assert calls == []


def test_text_only_measurement_profile_accepts_false_runtime_capabilities(tmp_path: Path) -> None:
    v32_runtime = PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml"
    with httpx.Client(transport=httpx.MockTransport(lambda _request: pytest.fail("no transport expected"))) as client:
        adapters = build_provider_adapters(
            _verified_pilot_policy(),
            runtime_config_path=v32_runtime,
            environ=_runtime_environ(tmp_path, v32_runtime),
            client=client,
            execution_profile=MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
        )

    assert set(adapters) == {
        ("anthropic", "claude-opus-5"),
        ("deepseek", "deepseek-v4-pro"),
        ("openai", "gpt-5.6-sol"),
    }


def test_http_endpoint_is_rejected_before_credentialed_transport(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    class RecordingTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise AssertionError("the non-HTTPS provider transport must not be called")

    with httpx.Client(transport=RecordingTransport()) as client:
        with pytest.raises(ProviderRuntimeConfigError, match="absolute HTTPS origin"):
            build_provider_adapters(
                _verified_pilot_policy(),
                runtime_config_path=PILOT_RUNTIME,
                environ=_runtime_environ(tmp_path, PILOT_RUNTIME, endpoint="http://insecure-provider.test"),
                client=client,
            )

    assert calls == []


def test_external_approval_must_bind_the_environment_endpoint_before_transport(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("an unapproved HTTPS origin must not receive the provider credential")

    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        with pytest.raises(ProviderRuntimeConfigError, match="does not bind this endpoint and runtime contract"):
            build_provider_adapters(
                _verified_pilot_policy(),
                runtime_config_path=PILOT_RUNTIME,
                environ=_runtime_environ(
                    tmp_path,
                    PILOT_RUNTIME,
                    endpoint="https://unapproved-provider.test",
                    approval_endpoint=TEST_ENDPOINT,
                ),
                client=client,
            )

    assert calls == []


def test_mock_transport_uses_environment_bound_https_endpoint_offline(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _response(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = build_provider_adapters(
            _verified_pilot_policy(),
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
        )[("deepseek", "deepseek-v4-pro")]
        assert adapter.execute(_request("deepseek", "deepseek-v4-pro", "author")).output_contract == {"ok": True}

    assert len(calls) == 1


def test_adapter_payloads_match_paths_auth_and_deepseek_thinking_contract(tmp_path: Path) -> None:
    seen: dict[str, tuple[httpx.Request, dict[str, Any]]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen[body["model"]] = (request, body)
        return _response(request)

    policy = _verified_pilot_policy()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapters = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            sleeper=lambda _seconds: None,
        )
        routes = [
            ("anthropic", "claude-opus-5", "brief_builder"),
            ("deepseek", "deepseek-v4-pro", "author"),
            ("openai", "gpt-5.6-sol", "verifier"),
        ]
        for provider, model, role in routes:
            result = adapters[(provider, model)].execute(_request(provider, model, role))
            assert result.output_contract == {"ok": True}
            assert result.cost_accounting == "provider_reported"

    assert seen["claude-opus-5"][0].url.path == "/v1/messages"
    assert "system" in seen["claude-opus-5"][1]
    assert seen["deepseek-v4-pro"][0].url.path == "/v1/chat/completions"
    assert seen["deepseek-v4-pro"][1]["thinking"] == {"type": "disabled"}
    assert "thinking" not in seen["gpt-5.6-sol"][1]
    assert all(request.headers["Authorization"] == f"Bearer {SECRET}" for request, _body in seen.values())


def test_worker_executor_auto_selects_registered_real_adapter_and_uses_cost_upper_bound(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, cost=None)

    policy = _verified_pilot_policy()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapters = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            sleeper=lambda _seconds: None,
        )
        executor = WorkerExecutor(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            policy=policy,
            provider_adapters=adapters,
        )
        result = executor.invoke(_request("deepseek", "deepseek-v4-pro", "author"))

    assert result.fixture_only is False
    assert result.actual_cost_usd == 0.4
    assert result.cost_accounting == "reservation_upper_bound"
    records = [json.loads(line) for line in executor.paths.usage.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["body"]["cost_accounting"] == "reservation_upper_bound"


def test_transient_status_retries_but_nonretryable_4xx_does_not(tmp_path: Path) -> None:
    attempts = 0
    delays: list[float] = []

    def transient_then_success(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"private": SECRET})
        return _response(request)

    policy = _verified_pilot_policy()
    with httpx.Client(transport=httpx.MockTransport(transient_then_success)) as client:
        adapter = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            sleeper=delays.append,
        )[("deepseek", "deepseek-v4-pro")]
        assert adapter.execute(_request("deepseek", "deepseek-v4-pro", "author")).output_contract == {"ok": True}

    assert attempts == 2
    assert delays == [0.25]

    attempts = 0

    def reject(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"secret": SECRET, "task": "private-task-content"})

    with httpx.Client(transport=httpx.MockTransport(reject)) as client:
        adapter = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            sleeper=lambda _seconds: None,
        )[("deepseek", "deepseek-v4-pro")]
        with pytest.raises(ProviderAdapterError, match="non-retryable HTTP status 400") as error:
            adapter.execute(_request("deepseek", "deepseek-v4-pro", "author"))

    assert attempts == 1
    assert SECRET not in str(error.value)
    assert "private-task-content" not in str(error.value)


def test_http_400_emits_only_a_sanitized_error_receipt(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []

    def reject(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "unsupported_parameter",
                    "param": "thinking",
                    "message": f"thinking must be disabled, private value={SECRET}",
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(reject)) as client:
        adapter = build_provider_adapters(
            _verified_pilot_policy(),
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            transport_observer=observed.append,
        )[("deepseek", "deepseek-v4-pro")]
        with pytest.raises(ProviderAdapterError, match="non-retryable HTTP status 400") as error:
            adapter.execute(_request("deepseek", "deepseek-v4-pro", "author"))

    expected = {
        "http_status": 400,
        "provider_error_type": "invalid_request",
        "provider_error_code": "unsupported_parameter",
        "field": "thinking",
        "message": "unsupported_thinking_option",
    }
    assert error.value.error_receipt == expected
    assert observed[-1]["event"] == "response_error"
    assert observed[-1]["error_receipt"] == expected
    assert SECRET not in repr(error.value.error_receipt)
    assert SECRET not in repr(observed)


@pytest.mark.parametrize(
    "content, expected",
    [
        ("not-json", "not exactly one JSON object"),
        ('{"unexpected":true}', "schema validation"),
    ],
)
def test_adapter_rejects_invalid_json_or_output_schema(tmp_path: Path, content: str, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-pro",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0},
            },
        )

    policy = _verified_pilot_policy()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            sleeper=lambda _seconds: None,
        )[("deepseek", "deepseek-v4-pro")]
        with pytest.raises(ProviderAdapterError, match=expected):
            adapter.execute(_request("deepseek", "deepseek-v4-pro", "author"))


def test_anthropic_gateway_choice_envelope_is_accepted_and_transport_receipts_are_sanitized(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "claude-opus-5",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"input_tokens": 2, "output_tokens": 1, "cost_usd": 0.01},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = build_provider_adapters(
            _verified_pilot_policy(),
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            transport_observer=observed.append,
        )[("anthropic", "claude-opus-5")]
        assert adapter.execute(_request("anthropic", "claude-opus-5", "brief_builder")).output_contract == {"ok": True}

    assert [event["event"] for event in observed] == ["request_sent", "response_received", "response_envelope_observed"]
    assert observed[-1]["response_shape"] == {
        "top_level_keys": ["choices", "model", "usage"],
        "choices": {"kind": "list", "count": 1},
    }
    assert SECRET not in repr(observed)


def test_sent_provider_failure_without_usage_remains_unknown_unsettled(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "deepseek-v4-pro", "choices": [{"message": {"content": "not-json"}}]})

    policy = _verified_pilot_policy()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapters = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            transport_observer=observed.append,
        )
        executor = WorkerExecutor(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            policy=policy,
            provider_adapters=adapters,
        )
        with pytest.raises(ProviderAdapterError, match="not exactly one JSON object") as error:
            executor.invoke(_request("deepseek", "deepseek-v4-pro", "author"))

    assert error.value.network_called is True
    assert [event["event"] for event in observed] == ["request_sent", "response_received", "response_envelope_observed"]
    records = [json.loads(line) for line in executor.paths.usage.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in records] == ["usage_reservation", "usage_unsettled"]
    assert records[-1]["status"] == "unknown_unsettled"
    assert records[-1]["body"]["reservation_digest"] == records[0]["content_digest"]


def test_expired_deadline_does_not_emit_a_false_request_sent_receipt(tmp_path: Path) -> None:
    observed: list[dict[str, Any]] = []

    def fail_if_called(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("deadline-expired request must not reach HTTP transport")

    request = _request("deepseek", "deepseek-v4-pro", "author")
    expired = WorkerRequest(
        role=request.role,
        provider=request.provider,
        model=request.model,
        input_contract=request.input_contract,
        max_tokens=request.max_tokens,
        max_cost_usd=request.max_cost_usd,
        run_id=request.run_id,
        work_item_id=request.work_item_id,
        deadline_monotonic=perf_counter() - 1,
    )
    with httpx.Client(transport=httpx.MockTransport(fail_if_called)) as client:
        adapter = build_provider_adapters(
            _verified_pilot_policy(),
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
            transport_observer=observed.append,
        )[("deepseek", "deepseek-v4-pro")]
        with pytest.raises(ProviderAdapterError, match="deadline elapsed before HTTP invocation") as error:
            adapter.execute(expired)

    assert error.value.network_called is False
    assert observed == []


def test_prompt_token_estimate_leaves_schema_output_capacity_without_byte_per_token_rejection() -> None:
    request = _request("anthropic", "claude-opus-5", "brief_builder")
    body = provider_http._request_body(
        provider_http.PROVIDER_CONTRACTS["anthropic"],
        request,
        system="s" * 12000,
        user="u" * 9000,
        visual_inputs=[],
    )
    assert 512 <= body["max_tokens"] < request.max_tokens


def test_runtime_contract_and_errors_never_render_secret_values(tmp_path: Path) -> None:
    config = _runtime_config(enabled=True)
    config["production_eligible"] = True
    path = _write(tmp_path, config)

    with pytest.raises(ProviderRuntimeConfigError, match="never be production eligible") as error:
        diagnose_provider_runtime(path, environ=_runtime_environ(tmp_path, path))
    assert SECRET not in str(error.value)

    policy = _verified_pilot_policy()
    with httpx.Client(transport=httpx.MockTransport(lambda request: _response(request))) as client:
        adapter = build_provider_adapters(
            policy,
            runtime_config_path=PILOT_RUNTIME,
            environ=_runtime_environ(tmp_path, PILOT_RUNTIME),
            client=client,
        )[("openai", "gpt-5.6-sol")]
    assert SECRET not in repr(adapter)


def test_runtime_yaml_parse_error_does_not_echo_private_source(tmp_path: Path) -> None:
    path = tmp_path / "provider-runtime.yaml"
    path.write_text(f"providers: [unterminated {SECRET}\n", encoding="utf-8")

    with pytest.raises(ProviderRuntimeConfigError, match="cannot parse provider runtime config") as error:
        diagnose_provider_runtime(path, environ={})
    assert SECRET not in str(error.value)


def test_cli_smoke_returns_gate_pending_without_key_or_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(BASE_URL_ENV, raising=False)

    exit_code = main(["config", "diagnose-providers", "--runtime-config", str(PILOT_RUNTIME), "--smoke"])

    captured = capsys.readouterr()
    assert exit_code == 3
    assert '"network_called": false' in captured.out
    assert '"status": "skipped"' in captured.out
    assert SECRET not in captured.out


def test_diagnose_does_not_add_a_top_level_cli_group() -> None:
    parser = build_parser()
    command_action = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))

    assert set(command_action.choices) == {
        "seed",
        "selection",
        "knowledge",
        "viewer",
        "config",
        "plan",
        "run",
        "review",
        "release",
    }
    config_action = next(
        action
        for action in command_action.choices["config"]._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    diagnose = config_action.choices["diagnose-providers"]
    assert any(action.dest == "smoke" for action in diagnose._actions)
