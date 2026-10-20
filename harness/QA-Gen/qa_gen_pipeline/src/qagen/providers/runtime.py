"""Fail-closed runtime loading, diagnostics, and adapter construction."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx
import yaml

from ..contracts import digest, require_digest
from ..errors import InputError
from ..workers import ProviderNotConfiguredError, TypedWorker, VerifiedRoutingPolicy
from .contracts import (
    MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
    PROVIDER_CONTRACTS,
    ROLE_PROVIDER_MODELS,
    UNIV_BASE_URL_ENV,
    UNIV_CREDENTIAL_ENV,
    UNIV_ENDPOINT_APPROVAL_PATH_ENV,
)
from .http import HTTPProviderAdapter, smoke_provider_contracts


_TOP_LEVEL_FIELDS = {
    "schema_version",
    "kind",
    "enabled",
    "endpoint_env",
    "endpoint_approval_path_env",
    "credential_env",
    "production_eligible",
    "request",
    "decision",
    "providers",
}
_REQUEST_FIELDS = {"timeout_seconds", "max_attempts", "initial_backoff_seconds"}
_DECISION_FIELDS = {"id", "decision", "decided_by", "decided_at", "scope"}
_PROVIDER_FIELDS = {
    "enabled",
    "model",
    "path",
    "wire_protocol",
    "thinking",
    "output_protocol",
    "usage_accounting",
    "multimodal",
}
_MULTIMODAL_FIELDS = {"supports_image_input", "supports_data_url"}
_VISUAL_TRANSPORT_ROLES = frozenset({"author", "evidence_worker", "verifier"})
_ENDPOINT_APPROVAL_FIELDS = {
    "schema_version",
    "kind",
    "id",
    "status",
    "decision",
    "endpoint",
    "credential_env",
    "runtime_config_digest",
    "scope",
    "decided_by",
    "decided_at",
    "production_eligible",
}


class ProviderRuntimeConfigError(InputError):
    """A provider runtime configuration is malformed or unauthorized."""


@dataclass(frozen=True)
class ProviderRuntimeSettings:
    config_path: Path
    config_digest: str
    base_url: str
    api_key: str = field(repr=False)
    enabled_providers: Mapping[str, bool] = field(default_factory=dict)
    provider_capabilities: Mapping[str, Mapping[str, bool]] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.25
    endpoint_approval_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled_providers", MappingProxyType(dict(self.enabled_providers)))
        object.__setattr__(
            self,
            "provider_capabilities",
            MappingProxyType({provider: MappingProxyType(dict(value)) for provider, value in self.provider_capabilities.items()}),
        )


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderRuntimeConfigError(f"{field_name} must be an object")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ProviderRuntimeConfigError(f"{field_name} must be a non-empty string")
    return value.strip()


def _load_config(path: str | Path) -> tuple[dict[str, Any], Path]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ProviderRuntimeConfigError(f"provider runtime config is not a local file: {config_path}")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProviderRuntimeConfigError("cannot read provider runtime config") from exc
    except yaml.YAMLError as exc:
        raise ProviderRuntimeConfigError("cannot parse provider runtime config") from exc
    return dict(_mapping(value, "provider runtime config")), config_path


def _validate_request(value: Any) -> dict[str, Any]:
    request = _mapping(value, "request")
    if set(request) != _REQUEST_FIELDS:
        raise ProviderRuntimeConfigError("request must declare timeout_seconds, max_attempts, and initial_backoff_seconds")
    timeout = request.get("timeout_seconds")
    attempts = request.get("max_attempts")
    backoff = request.get("initial_backoff_seconds")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < float(timeout) <= 120:
        raise ProviderRuntimeConfigError("request.timeout_seconds must be in (0, 120]")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts not in {1, 2, 3}:
        raise ProviderRuntimeConfigError("request.max_attempts must be 1, 2, or 3")
    if not isinstance(backoff, (int, float)) or isinstance(backoff, bool) or not 0 <= float(backoff) <= 2:
        raise ProviderRuntimeConfigError("request.initial_backoff_seconds must be in [0, 2]")
    return {"timeout_seconds": float(timeout), "max_attempts": attempts, "initial_backoff_seconds": float(backoff)}


def _validate_decision(value: Any) -> dict[str, str]:
    decision = _mapping(value, "decision")
    if set(decision) != _DECISION_FIELDS:
        raise ProviderRuntimeConfigError("decision metadata is incomplete")
    normalized = {field_name: _text(decision.get(field_name), f"decision.{field_name}") for field_name in _DECISION_FIELDS}
    if normalized["decision"] != "approved_for_local_pilot":
        raise ProviderRuntimeConfigError("provider runtime decision is not approved for the local pilot")
    return normalized


def _validate_config(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _TOP_LEVEL_FIELDS:
        raise ProviderRuntimeConfigError("provider runtime config fields do not match the approved contract")
    if value.get("schema_version") != 1 or value.get("kind") != "qagen_provider_runtime":
        raise ProviderRuntimeConfigError("provider runtime config kind or schema_version is invalid")
    if not isinstance(value.get("enabled"), bool):
        raise ProviderRuntimeConfigError("provider runtime enabled must be boolean")
    if value.get("endpoint_env") != UNIV_BASE_URL_ENV:
        raise ProviderRuntimeConfigError("provider runtime endpoint env differs from the approved name")
    if value.get("endpoint_approval_path_env") != UNIV_ENDPOINT_APPROVAL_PATH_ENV:
        raise ProviderRuntimeConfigError("provider runtime endpoint approval env differs from the approved name")
    if value.get("credential_env") != UNIV_CREDENTIAL_ENV:
        raise ProviderRuntimeConfigError("provider runtime credential env differs from the approved name")
    if value.get("production_eligible") is not False:
        raise ProviderRuntimeConfigError("the provider runtime can never be production eligible")
    request = _validate_request(value.get("request"))
    decision = _validate_decision(value.get("decision"))
    providers = _mapping(value.get("providers"), "providers")
    if set(providers) != set(PROVIDER_CONTRACTS):
        raise ProviderRuntimeConfigError("providers must declare exactly anthropic, deepseek, and openai")
    normalized_providers: dict[str, dict[str, Any]] = {}
    for provider, contract in PROVIDER_CONTRACTS.items():
        raw = _mapping(providers.get(provider), f"providers.{provider}")
        if set(raw) != _PROVIDER_FIELDS:
            raise ProviderRuntimeConfigError(f"providers.{provider} fields do not match the approved contract")
        if not isinstance(raw.get("enabled"), bool):
            raise ProviderRuntimeConfigError(f"providers.{provider}.enabled must be boolean")
        expected = {
            "model": contract.model,
            "path": contract.path,
            "wire_protocol": contract.wire_protocol,
            "thinking": contract.thinking,
            "output_protocol": "json_schema_object",
            "usage_accounting": "provider_cost_or_reservation_upper_bound",
        }
        for field_name, expected_value in expected.items():
            if raw.get(field_name) != expected_value:
                raise ProviderRuntimeConfigError(f"providers.{provider}.{field_name} differs from the approved contract")
        multimodal = _mapping(raw.get("multimodal"), f"providers.{provider}.multimodal")
        if set(multimodal) != _MULTIMODAL_FIELDS:
            raise ProviderRuntimeConfigError(
                f"providers.{provider}.multimodal must declare image and data-url support explicitly"
            )
        # A provider contract describes the widest protocol surface known to
        # the adapter. A pinned runtime may deliberately disable a capability
        # after a compatibility probe; it must never enable a capability the
        # adapter contract does not support.
        expected_multimodal = {
            "supports_image_input": contract.supports_image_input,
            "supports_data_url": contract.supports_data_url,
        }
        for capability, supported in expected_multimodal.items():
            if multimodal.get(capability) is True and supported is not True:
                raise ProviderRuntimeConfigError(
                    f"providers.{provider}.multimodal enables an unsupported adapter capability"
                )
            if not isinstance(multimodal.get(capability), bool):
                raise ProviderRuntimeConfigError(f"providers.{provider}.multimodal capability must be boolean")
        normalized_providers[provider] = {
            **expected,
            "enabled": raw["enabled"],
            "multimodal": dict(multimodal),
        }
    return {
        "schema_version": 1,
        "kind": "qagen_provider_runtime",
        "enabled": value["enabled"],
        "endpoint_env": UNIV_BASE_URL_ENV,
        "endpoint_approval_path_env": UNIV_ENDPOINT_APPROVAL_PATH_ENV,
        "credential_env": UNIV_CREDENTIAL_ENV,
        "production_eligible": False,
        "request": request,
        "decision": decision,
        "providers": normalized_providers,
    }


def _validate_base_url(value: str) -> str:
    base_url = _text(value, "provider base URL").rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ProviderRuntimeConfigError("provider base URL must be an absolute HTTPS origin")
    try:
        parsed.port
    except ValueError as exc:
        raise ProviderRuntimeConfigError("provider base URL has an invalid port") from exc
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ProviderRuntimeConfigError("provider base URL must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ProviderRuntimeConfigError("provider base URL must be an origin without an API path")
    return base_url


def _external_approval_path(config_path: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ProviderRuntimeConfigError("provider endpoint approval path must be absolute")
    if path.is_symlink() or not path.is_file():
        raise ProviderRuntimeConfigError("provider endpoint approval receipt is unavailable")
    resolved = path.resolve()
    config_root = config_path.parents[1].resolve()
    try:
        resolved.relative_to(config_root)
    except ValueError:
        return resolved
    raise ProviderRuntimeConfigError("provider endpoint approval receipt must be external to checked-in config")


def _locked_endpoint_approval(
    *,
    config_path: Path,
    config_digest: str,
    endpoint: str,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    raw_path = environ.get(UNIV_ENDPOINT_APPROVAL_PATH_ENV, "").strip()
    if not raw_path:
        raise ProviderNotConfiguredError(
            f"provider endpoint approval env is missing: {UNIV_ENDPOINT_APPROVAL_PATH_ENV}"
        )
    receipt_path = _external_approval_path(config_path, raw_path)
    try:
        receipt = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProviderRuntimeConfigError("cannot read provider endpoint approval receipt") from exc
    if not isinstance(receipt, Mapping) or set(receipt) != _ENDPOINT_APPROVAL_FIELDS:
        raise ProviderRuntimeConfigError("provider endpoint approval receipt fields do not match the approved contract")
    if receipt.get("schema_version") != 1 or receipt.get("kind") != "qagen_provider_endpoint_approval":
        raise ProviderRuntimeConfigError("provider endpoint approval receipt kind or schema_version is invalid")
    if receipt.get("status") != "approved" or receipt.get("decision") != "allow_credentialed_https_endpoint":
        raise ProviderRuntimeConfigError("provider endpoint approval receipt is not approved for credentialed HTTPS transport")
    if receipt.get("credential_env") != UNIV_CREDENTIAL_ENV or receipt.get("production_eligible") is not False:
        raise ProviderRuntimeConfigError("provider endpoint approval receipt is not authorized for this local pilot")
    try:
        approval_endpoint = _validate_base_url(_text(receipt.get("endpoint"), "provider endpoint approval receipt.endpoint"))
        require_digest(receipt.get("runtime_config_digest"), "provider endpoint approval receipt.runtime_config_digest")
    except InputError as exc:
        raise ProviderRuntimeConfigError(str(exc)) from exc
    if approval_endpoint != endpoint or receipt.get("runtime_config_digest") != config_digest:
        raise ProviderRuntimeConfigError("provider endpoint approval receipt does not bind this endpoint and runtime contract")
    for field_name in ("id", "scope", "decided_by", "decided_at"):
        _text(receipt.get(field_name), f"provider endpoint approval receipt.{field_name}")
    return dict(receipt)


def _environment(environ: Mapping[str, str]) -> tuple[str, str]:
    api_key = environ.get(UNIV_CREDENTIAL_ENV, "").strip()
    endpoint = environ.get(UNIV_BASE_URL_ENV, "").strip()
    if not endpoint:
        raise ProviderNotConfiguredError(f"provider endpoint env is missing: {UNIV_BASE_URL_ENV}")
    return api_key, _validate_base_url(endpoint)


def load_provider_runtime(
    config_path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    expected_config_digest: str | None = None,
) -> ProviderRuntimeSettings:
    raw, path = _load_config(config_path)
    if expected_config_digest is not None:
        require_digest(expected_config_digest, "provider runtime expected config digest")
        if digest(raw) != expected_config_digest:
            raise ProviderRuntimeConfigError("provider runtime config digest does not match its approved lock")
    config = _validate_config(raw)
    active_environment = os.environ if environ is None else environ
    if config["enabled"] is not True:
        raise ProviderNotConfiguredError("provider runtime is disabled")
    disabled = [provider for provider, details in config["providers"].items() if details["enabled"] is not True]
    if disabled:
        raise ProviderNotConfiguredError("one or more approved provider contracts are disabled")
    api_key = active_environment.get(UNIV_CREDENTIAL_ENV, "").strip()
    if not api_key:
        raise ProviderNotConfiguredError(f"provider credential env is missing: {UNIV_CREDENTIAL_ENV}")
    _, base_url = _environment(active_environment)
    approval = _locked_endpoint_approval(
        config_path=path,
        config_digest=digest(raw),
        endpoint=base_url,
        environ=active_environment,
    )
    request = config["request"]
    return ProviderRuntimeSettings(
        config_path=path,
        config_digest=digest(raw),
        base_url=base_url,
        api_key=api_key,
        enabled_providers={provider: details["enabled"] for provider, details in config["providers"].items()},
        provider_capabilities={
            provider: dict(details["multimodal"])
            for provider, details in config["providers"].items()
        },
        timeout_seconds=request["timeout_seconds"],
        max_attempts=request["max_attempts"],
        initial_backoff_seconds=request["initial_backoff_seconds"],
        endpoint_approval_digest=digest(approval),
    )


def _mock_transport(client: httpx.Client | None) -> bool:
    """Keep injected test transports deterministic without weakening real HTTP."""
    return bool(client is not None and isinstance(getattr(client, "_transport", None), httpx.MockTransport))


def require_secure_https_transport(
    settings: ProviderRuntimeSettings,
    *,
    client: httpx.Client | None,
) -> None:
    """Defend real credentialed calls against a non-HTTPS settings object."""
    if _mock_transport(client):
        return
    if urlsplit(settings.base_url).scheme != "https":
        raise ProviderRuntimeConfigError("credentialed real provider transport requires HTTPS")


def _validate_execution_profile_capabilities(
    policy: VerifiedRoutingPolicy,
    settings: ProviderRuntimeSettings,
    *,
    execution_profile: str | None,
) -> None:
    """Bind capability declarations to the active execution profile.

    Runtime capability is an availability claim, not a universal requirement.
    Standard visual execution cannot bind a role that lacks image/data-url
    delivery, while the text-only measurement-replay profile deliberately
    requires no image transport for any of its provider stages.
    """

    if execution_profile == MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE:
        return
    for role in _VISUAL_TRANSPORT_ROLES:
        route = policy.role(role)
        provider = route.get("provider")
        if not isinstance(provider, str):
            raise ProviderRuntimeConfigError(f"routing role {role} has no provider capability binding")
        capabilities = settings.provider_capabilities.get(provider)
        if not isinstance(capabilities, Mapping) or (
            capabilities.get("supports_image_input") is not True
            or capabilities.get("supports_data_url") is not True
        ):
            raise ProviderRuntimeConfigError(
                f"provider {provider} does not explicitly support image/data-url input for a visual execution profile"
            )


def require_provider_multimodal_capability(
    config_path: str | Path,
    *,
    expected_config_digest: str | None,
    providers: tuple[str, ...] = ("deepseek", "openai"),
) -> None:
    """Fail before any network call unless visual delivery is explicitly locked.

    The provider pilot intentionally has no metadata-only fallback.  This
    helper validates only the checked-in runtime contract, so it can run
    before credentials, browser work, and HTTP adapter construction.
    """
    raw, _path = _load_config(config_path)
    if expected_config_digest is not None:
        require_digest(expected_config_digest, "provider runtime expected config digest")
        if digest(raw) != expected_config_digest:
            raise ProviderRuntimeConfigError("provider runtime config digest does not match its approved lock")
    config = _validate_config(raw)
    for provider in providers:
        details = config["providers"].get(provider)
        if not isinstance(details, Mapping):
            raise ProviderRuntimeConfigError(f"provider runtime has no visual capability declaration for {provider}")
        multimodal = details.get("multimodal")
        if not isinstance(multimodal, Mapping) or (
            multimodal.get("supports_image_input") is not True or multimodal.get("supports_data_url") is not True
        ):
            raise ProviderRuntimeConfigError(
                f"provider {provider} does not explicitly support image/data-url input for the chirality pilot"
            )


def diagnose_provider_runtime(
    config_path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    smoke: bool = False,
    client: httpx.Client | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Inspect readiness and optionally run fixed probes without exposing content."""
    raw, path = _load_config(config_path)
    config = _validate_config(raw)
    active_environment = os.environ if environ is None else environ
    blockers: list[str] = []
    if config["enabled"] is not True:
        blockers.append("provider_runtime_disabled")
    for provider, details in config["providers"].items():
        if details["enabled"] is not True:
            blockers.append(f"provider_disabled:{provider}")
    api_key = active_environment.get(UNIV_CREDENTIAL_ENV, "").strip()
    if not api_key:
        blockers.append(f"credential_env_missing:{UNIV_CREDENTIAL_ENV}")
    endpoint_raw = active_environment.get(UNIV_BASE_URL_ENV, "").strip()
    approval_path = active_environment.get(UNIV_ENDPOINT_APPROVAL_PATH_ENV, "").strip()
    base_url: str | None = None
    approval: dict[str, Any] | None = None
    if not endpoint_raw:
        blockers.append(f"endpoint_env_missing:{UNIV_BASE_URL_ENV}")
    else:
        try:
            base_url = _validate_base_url(endpoint_raw)
        except ProviderRuntimeConfigError:
            blockers.append("endpoint_env_invalid")
    if not approval_path:
        blockers.append(f"endpoint_approval_env_missing:{UNIV_ENDPOINT_APPROVAL_PATH_ENV}")
    elif base_url is not None:
        try:
            approval = _locked_endpoint_approval(
                config_path=path,
                config_digest=digest(raw),
                endpoint=base_url,
                environ=active_environment,
            )
        except (ProviderNotConfiguredError, ProviderRuntimeConfigError):
            blockers.append("endpoint_approval_invalid")
    ready_before_smoke = not blockers
    smoke_results: list[dict[str, Any]] = []
    if smoke and ready_before_smoke:
        if base_url is None or approval is None:
            raise ProviderRuntimeConfigError("provider runtime diagnostic lost its validated endpoint binding")
        settings = ProviderRuntimeSettings(
            config_path=path,
            config_digest=digest(raw),
            base_url=base_url,
            api_key=api_key,
            enabled_providers={provider: details["enabled"] for provider, details in config["providers"].items()},
            provider_capabilities={
                provider: dict(details["multimodal"])
                for provider, details in config["providers"].items()
            },
            timeout_seconds=config["request"]["timeout_seconds"],
            max_attempts=config["request"]["max_attempts"],
            initial_backoff_seconds=config["request"]["initial_backoff_seconds"],
            endpoint_approval_digest=digest(approval),
        )
        require_secure_https_transport(settings, client=client)
    if smoke and ready_before_smoke:
        kwargs: dict[str, Any] = {}
        if sleeper is not None:
            kwargs["sleeper"] = sleeper
        smoke_results = smoke_provider_contracts(
            base_url=base_url,
            api_key=api_key,
            enabled_providers={provider: details["enabled"] for provider, details in config["providers"].items()},
            timeout_seconds=config["request"]["timeout_seconds"],
            max_attempts=config["request"]["max_attempts"],
            initial_backoff_seconds=config["request"]["initial_backoff_seconds"],
            client=client,
            **kwargs,
        )
        if any(item["status"] != "passed" for item in smoke_results):
            blockers.append("one_or_more_provider_smokes_failed")
    elif smoke:
        smoke_results = [
            {
                "provider": provider,
                "model": contract.model,
                "status": "skipped",
                "network_called": False,
                "reason": "runtime_not_ready",
            }
            for provider, contract in PROVIDER_CONTRACTS.items()
        ]
    eligible = not blockers
    return {
        "kind": "qagen_provider_runtime_diagnostic",
        "schema_version": 1,
        "status": "approved_local_pilot_ready" if eligible else "blocked",
        "execution_eligible": eligible,
        "production_eligible": False,
        "adapter_status": "implemented",
        "runtime_config_path": str(path),
        "runtime_config_digest": digest(raw),
        "credential": {"env": UNIV_CREDENTIAL_ENV, "present": bool(api_key)},
        "base_url": {
            "env": UNIV_BASE_URL_ENV,
            "present": bool(endpoint_raw),
            "source": "environment",
            "https_required": True,
        },
        "endpoint_approval": {
            "path_env": UNIV_ENDPOINT_APPROVAL_PATH_ENV,
            "present": bool(approval_path),
            "validated": approval is not None,
        },
        "providers": [
            {
                "provider": provider,
                "enabled": details["enabled"],
                "model": details["model"],
                "path": details["path"],
                "wire_protocol": details["wire_protocol"],
                "thinking": details["thinking"],
            }
            for provider, details in config["providers"].items()
        ],
        "smoke_requested": smoke,
        "network_called": any(item.get("network_called") is True for item in smoke_results),
        "smoke": smoke_results,
        "blocked_reasons": blockers,
    }


def build_provider_adapters(
    policy: VerifiedRoutingPolicy,
    *,
    runtime_config_path: str | Path,
    runtime_config_digest: str | None = None,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    sleeper: Callable[[float], None] | None = None,
    transport_observer: Callable[[Mapping[str, Any]], None] | None = None,
    execution_profile: str | None = None,
) -> dict[tuple[str, str], TypedWorker]:
    """Bind approved runtime settings to the exact verified pilot routes."""
    if not isinstance(policy, VerifiedRoutingPolicy):
        raise ProviderRuntimeConfigError("provider adapter factory requires a verified routing policy")
    policy.assert_intact()
    if policy.local_fixture_only:
        raise ProviderRuntimeConfigError("real provider adapters cannot bind to a fixture routing policy")
    if not policy.local_pilot_only:
        raise ProviderRuntimeConfigError("real provider adapters require an explicit approved-local pilot routing policy")
    if execution_profile not in {None, MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE}:
        raise ProviderRuntimeConfigError("provider adapter execution profile is invalid")
    settings = load_provider_runtime(
        runtime_config_path,
        environ=environ,
        expected_config_digest=runtime_config_digest,
    )
    _validate_execution_profile_capabilities(
        policy,
        settings,
        execution_profile=execution_profile,
    )
    require_secure_https_transport(settings, client=client)
    roles_by_provider: dict[str, dict[str, Mapping[str, Any]]] = {provider: {} for provider in PROVIDER_CONTRACTS}
    latency_by_provider: dict[str, float] = {}
    for role, expected_route in ROLE_PROVIDER_MODELS.items():
        route = policy.role(role)
        models = route.get("allowed_models")
        actual_route = (route.get("provider"), models[0] if isinstance(models, list) and len(models) == 1 else None)
        if actual_route != expected_route:
            raise ProviderRuntimeConfigError(f"routing role {role} does not match the approved provider contract")
        provider, _model = expected_route
        provider_policy = policy.provider(provider)
        if provider_policy.get("credential_ref") != f"env:{UNIV_CREDENTIAL_ENV}":
            raise ProviderRuntimeConfigError(f"routing provider {provider} has an unapproved credential reference")
        if provider_policy.get("production_eligible") is not False:
            raise ProviderRuntimeConfigError(f"routing provider {provider} must remain local-pilot-only")
        roles_by_provider[provider][role] = policy.schema(str(route["output_schema"]))
        latency = float(route["max_latency_seconds"])
        latency_by_provider[provider] = min(latency_by_provider.get(provider, latency), latency)
    adapters: dict[tuple[str, str], TypedWorker] = {}
    for provider, contract in PROVIDER_CONTRACTS.items():
        kwargs: dict[str, Any] = {}
        if sleeper is not None:
            kwargs["sleeper"] = sleeper
        adapter = HTTPProviderAdapter(
            contract=contract,
            base_url=settings.base_url,
            api_key=settings.api_key,
            role_schemas=roles_by_provider[provider],
            supports_image_input=settings.provider_capabilities[provider]["supports_image_input"],
            supports_data_url=settings.provider_capabilities[provider]["supports_data_url"],
            timeout_seconds=min(settings.timeout_seconds, latency_by_provider[provider]),
            max_attempts=settings.max_attempts,
            initial_backoff_seconds=settings.initial_backoff_seconds,
            client=client,
            transport_observer=transport_observer,
            execution_profile=execution_profile,
            **kwargs,
        )
        adapters[(provider, contract.model)] = adapter
    return adapters
