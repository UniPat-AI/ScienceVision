"""Fail-closed worker execution for the QA-Gen runtime.

This module deliberately knows nothing about run-state transitions, approval,
or release.  It validates a locked routing policy, invokes one stateless
adapter, and writes a bounded usage reservation/settlement pair.  The control
plane remains responsible for all decisions and persistence outside the audit
ledger.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import json
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from jsonschema import Draft7Validator
import yaml

from .contracts import canonical_json, digest, require_digest
from .errors import GatePendingError, InputError
from .paths import WorkspacePaths
from .resource_paths import PackageResourceError, config_path
from .storage.ledger import UsageBudget, mark_usage_unsettled, reserve_usage, settle_usage


ROLE_ROUTING_SUGGESTIONS = {
    "brief_builder": "opus-5",
    "planner": "opus-5",
    "author": "dpsk-v4-pro",
    "truth_builder": "dpsk-v4-pro",
    "evidence_worker": "dpsk-v4-pro",
    "rewriter": "dpsk-v4-pro",
    "verifier": "gpt-5.6-sol",
    "adversarial_check": "gpt-5.6-sol",
}


class WorkerPolicyError(InputError):
    """A worker invocation does not satisfy the verified typed policy."""


class ProviderNotConfiguredError(GatePendingError):
    """A route is not backed by an approved, configured execution provider."""


class DeadlineExceededError(WorkerPolicyError):
    """A request began or completed after its immutable deadline."""


class WorkerContractError(WorkerPolicyError):
    """An adapter returned a request/result contract that cannot be trusted."""


class ProviderUsageError(WorkerPolicyError):
    """A provider failed after reporting bounded, trustworthy usage.

    Provider adapters may raise this when the remote service returned an error
    together with its authoritative usage counters.  Generic exceptions are
    deliberately not introspected for similarly named attributes: an adapter
    must opt into this explicit typed boundary before the ledger can settle for
    less than the amount reserved before the call.
    """

    def __init__(
        self,
        message: str,
        *,
        token_in: int | None = None,
        token_out: int | None = None,
        actual_cost_usd: float | None = None,
        network_called: bool = False,
    ) -> None:
        super().__init__(message)
        self.token_in = token_in
        self.token_out = token_out
        self.actual_cost_usd = actual_cost_usd
        self.network_called = bool(network_called)
        self.has_trustworthy_usage = token_in is not None and token_out is not None and actual_cost_usd is not None


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise WorkerPolicyError(f"{field} must be a non-empty string")
    return value.strip()


def _nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorkerPolicyError(f"{field} must be a non-negative integer")
    return value


def _nonnegative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise WorkerPolicyError(f"{field} must be a non-negative number")
    if float(value) == float("inf") or float(value) != float(value):
        raise WorkerPolicyError(f"{field} must be finite")
    return float(value)


@dataclass(frozen=True)
class WorkerRequest:
    """One bounded, stateless worker request.

    ``run_id`` and ``work_item_id`` intentionally default to ``None`` rather
    than an implicit scope.  A real executor rejects unbound requests; defaults
    only preserve construction compatibility for callers that validate later.
    """

    role: str
    provider: str
    model: str
    input_contract: Mapping[str, Any]
    max_tokens: int
    max_cost_usd: float
    run_id: str | None = None
    work_item_id: str | None = None
    deadline_monotonic: float | None = None

    @property
    def input_contract_digest(self) -> str:
        if not isinstance(self.input_contract, Mapping):
            raise WorkerPolicyError("worker input_contract must be an object")
        return digest(dict(self.input_contract))


@dataclass(frozen=True)
class WorkerResult:
    """Adapter output, including the actual provider-reported usage."""

    role: str
    provider: str
    model: str
    input_contract_digest: str
    output_contract: Mapping[str, Any]
    output_contract_digest: str
    fixture_only: bool = False
    token_in: int = 0
    token_out: int = 0
    actual_cost_usd: float = 0.0
    cost_accounting: str = "automatic"


class TypedWorker(Protocol):
    """A stateless provider adapter with no authority over the run lifecycle."""

    provider: str
    model: str
    fixture_only: bool

    def execute(self, request: WorkerRequest) -> WorkerResult:
        """Return a typed result only; control-plane persistence is forbidden."""


def routing_suggestion(role: str) -> str:
    try:
        return ROLE_ROUTING_SUGGESTIONS[role]
    except KeyError as exc:
        raise WorkerPolicyError(f"unknown worker role: {role}") from exc


def _generic_object_schema() -> dict[str, Any]:
    return {"type": "object"}


def _candidate_schema() -> dict[str, Any]:
    try:
        path = config_path("schemas", "candidate.schema.json")
    except PackageResourceError as exc:
        raise WorkerPolicyError("worker candidate schema is unavailable") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerPolicyError(f"cannot load worker candidate schema: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerPolicyError("worker candidate schema must be an object")
    return value


def _question_draft_schema() -> dict[str, Any]:
    try:
        path = config_path("schemas", "question_draft.schema.json")
    except PackageResourceError as exc:
        raise WorkerPolicyError("worker question-draft schema is unavailable") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerPolicyError(f"cannot load worker question-draft schema: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerPolicyError("worker question-draft schema must be an object")
    return value


def _target_spec_schema() -> dict[str, Any]:
    try:
        path = config_path("schemas", "target_spec.schema.json")
    except PackageResourceError as exc:
        raise WorkerPolicyError("worker TargetSpec schema is unavailable") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerPolicyError(f"cannot load worker TargetSpec schema: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerPolicyError("worker TargetSpec schema must be an object")
    return value


def _verification_result_schema() -> dict[str, Any]:
    try:
        path = config_path("schemas", "verification_result.schema.json")
    except PackageResourceError as exc:
        raise WorkerPolicyError("worker verification-result schema is unavailable") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerPolicyError(f"cannot load worker verification-result schema: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerPolicyError("worker verification-result schema must be an object")
    return value


def _provider_viewer_evidence_schema() -> dict[str, Any]:
    try:
        path = config_path("schemas", "viewer_evidence_packet.schema.json")
    except PackageResourceError as exc:
        raise WorkerPolicyError("worker provider viewer-evidence schema is unavailable") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerPolicyError(f"cannot load worker provider viewer-evidence schema: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerPolicyError("worker provider viewer-evidence schema must be an object")
    return value


def validate_verification_result(value: Mapping[str, Any], *, require_pass: bool = False) -> dict[str, Any]:
    """Validate the persisted verifier contract and optionally require a pass."""
    if not isinstance(value, Mapping):
        raise WorkerContractError("verification result must be an object")
    result = dict(value)
    _validate_json_schema(result, _verification_result_schema(), "verification result")
    checks = result["checks"]
    check_outcomes = {check["outcome"] for check in checks}
    if result["outcome"] == "pass" and check_outcomes != {"pass"}:
        raise WorkerContractError("passing verification result contains a failing check")
    if result["outcome"] == "fail" and "fail" not in check_outcomes:
        raise WorkerContractError("failed verification result contains no failing check")
    for criterion, score in result["rubric_scores"].items():
        if score["score"] > score["maximum"]:
            raise WorkerContractError(f"verification result score exceeds maximum: {criterion}")
    if require_pass and result["outcome"] != "pass":
        raise WorkerContractError("verification result outcome must be pass")
    return result


def _builtin_schema_registry() -> dict[str, Mapping[str, Any]]:
    generic = _generic_object_schema()
    return {
        "qagen://schemas/task-brief/input/v1": generic,
        "qagen://schemas/task-brief/output/v1": generic,
        "qagen://schemas/target-spec/v1": _target_spec_schema(),
        "qagen://schemas/author/input/v1": generic,
        "qagen://schemas/question-draft/v1": _question_draft_schema(),
        "qagen://schemas/candidate/v1": _candidate_schema(),
        "qagen://schemas/truth-builder/input/v1": generic,
        "qagen://schemas/author-truth-receipt/v1": generic,
        "qagen://schemas/evidence-worker/input/v1": generic,
        "qagen://schemas/viewer-evidence-packet/v1": _provider_viewer_evidence_schema(),
        "qagen://schemas/verifier/input/v1": generic,
        "qagen://schemas/verification-result/v1": _verification_result_schema(),
        "qagen://schemas/rewriter/input/v1": generic,
    }


def _schema_registry(extra: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    registry = {key: deepcopy(dict(value)) for key, value in _builtin_schema_registry().items()}
    if extra is None:
        return registry
    if not isinstance(extra, Mapping):
        raise WorkerPolicyError("worker schema registry must be a mapping")
    for raw_identifier, raw_schema in extra.items():
        identifier = _require_text(raw_identifier, "schema identifier")
        if not identifier.startswith("qagen://schemas/"):
            raise WorkerPolicyError(f"worker schema identifier is not controlled: {identifier}")
        if not isinstance(raw_schema, Mapping):
            raise WorkerPolicyError(f"worker schema {identifier} must be an object")
        try:
            Draft7Validator.check_schema(dict(raw_schema))
        except Exception as exc:
            raise WorkerPolicyError(f"worker schema {identifier} is invalid: {exc}") from exc
        if identifier == "qagen://schemas/verification-result/v1" and dict(raw_schema) != registry[identifier]:
            raise WorkerPolicyError("the built-in verification-result schema cannot be overridden")
        registry[identifier] = deepcopy(dict(raw_schema))
    return registry


def _is_local_fixture_policy(policy: Mapping[str, Any]) -> bool:
    fixture = policy.get("fixture_adapter")
    if not isinstance(fixture, Mapping):
        return False
    return bool(
        fixture.get("enabled") is True
        and fixture.get("local_only") is True
        and fixture.get("production_eligible") is False
        and policy.get("run_eligibility") in {"local_test", "fixture_test"}
    )


def _is_local_pilot_policy(policy: Mapping[str, Any]) -> bool:
    return bool(
        policy.get("enabled") is True
        and policy.get("routing_status") == "approved_local_pilot"
        and policy.get("run_eligibility") == "approved_local_pilot"
        and policy.get("production_eligible") is False
    )


@dataclass(frozen=True)
class VerifiedRoutingPolicy:
    """An immutable-by-digest routing policy accepted by :class:`WorkerExecutor`."""

    _policy: Mapping[str, Any]
    content_digest: str
    _schemas: Mapping[str, Mapping[str, Any]]
    local_fixture_only: bool
    local_pilot_only: bool

    def assert_intact(self) -> None:
        if digest(dict(self._policy)) != self.content_digest:
            raise WorkerPolicyError("verified routing policy was modified after validation")

    @property
    def policy(self) -> dict[str, Any]:
        self.assert_intact()
        return deepcopy(dict(self._policy))

    def role(self, role: str) -> dict[str, Any]:
        self.assert_intact()
        roles = self._policy.get("roles")
        if not isinstance(roles, Mapping) or not isinstance(roles.get(role), Mapping):
            raise WorkerPolicyError(f"worker role is not present in verified routing policy: {role}")
        return deepcopy(dict(roles[role]))

    def provider(self, provider: str) -> dict[str, Any]:
        self.assert_intact()
        providers = self._policy.get("providers")
        if not isinstance(providers, Mapping) or not isinstance(providers.get(provider), Mapping):
            raise ProviderNotConfiguredError(f"worker provider is not configured: {provider}")
        return deepcopy(dict(providers[provider]))

    def schema(self, identifier: str) -> Mapping[str, Any]:
        self.assert_intact()
        schema = self._schemas.get(identifier)
        if not isinstance(schema, Mapping):
            raise WorkerPolicyError(f"worker schema is not registered: {identifier}")
        return deepcopy(dict(schema))


def _validate_role_policy(
    role: str,
    role_policy: Mapping[str, Any],
    providers: Mapping[str, Any],
    schemas: Mapping[str, Mapping[str, Any]],
    *,
    local_fixture_only: bool,
    local_pilot_only: bool,
) -> None:
    _require_text(role, "worker role")
    if role_policy.get("enabled") is not True:
        raise ProviderNotConfiguredError(f"worker role is disabled: {role}")
    provider_name = _require_text(role_policy.get("provider"), f"roles.{role}.provider")
    provider = providers.get(provider_name)
    if not isinstance(provider, Mapping) or provider.get("configured") is not True:
        raise ProviderNotConfiguredError(f"worker provider is not configured: {provider_name}")
    if provider.get("local_only") is True and not (local_fixture_only or local_pilot_only):
        raise ProviderNotConfiguredError(f"local-only worker provider cannot run outside a local fixture: {provider_name}")
    if local_pilot_only and provider.get("production_eligible") is not False:
        raise WorkerPolicyError(f"local-pilot provider must never be production eligible: {provider_name}")
    allowed_models = role_policy.get("allowed_models")
    if not isinstance(allowed_models, list) or not allowed_models:
        raise WorkerPolicyError(f"roles.{role}.allowed_models must be a non-empty list")
    for model in allowed_models:
        _require_text(model, f"roles.{role}.allowed_models")
    _nonnegative_int(role_policy.get("max_tokens"), f"roles.{role}.max_tokens")
    _nonnegative_number(role_policy.get("max_cost_usd"), f"roles.{role}.max_cost_usd")
    latency = role_policy.get("max_latency_seconds")
    if not isinstance(latency, (int, float)) or isinstance(latency, bool) or float(latency) <= 0:
        raise WorkerPolicyError(f"roles.{role}.max_latency_seconds must be positive")
    for key in ("input_schema", "output_schema"):
        identifier = _require_text(role_policy.get(key), f"roles.{role}.{key}")
        if identifier not in schemas:
            raise WorkerPolicyError(f"roles.{role}.{key} is not a registered schema: {identifier}")


def verify_routing_policy(
    policy: Mapping[str, Any],
    *,
    expected_digest: str | None = None,
    schema_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> VerifiedRoutingPolicy:
    """Validate and freeze a routing policy before it can execute any worker.

    A checked-in policy with ``enabled: false`` is intentionally only a logical
    recommendation.  Execution requires an enabled, approved policy and each
    enabled provider/model route.  ``expected_digest`` binds this policy to a
    config/run lock when one is available.
    """
    if not isinstance(policy, Mapping):
        raise WorkerPolicyError("routing policy must be an object")
    try:
        frozen_policy = json.loads(canonical_json(dict(policy)).decode("utf-8"))
    except Exception as exc:
        raise WorkerPolicyError(f"routing policy cannot be canonically frozen: {exc}") from exc
    if not isinstance(frozen_policy, dict):
        raise WorkerPolicyError("routing policy must freeze to an object")
    content_digest = digest(frozen_policy)
    if expected_digest is not None:
        require_digest(expected_digest, "routing policy expected digest")
        if content_digest != expected_digest:
            raise WorkerPolicyError("routing policy digest does not match its config lock")
    if frozen_policy.get("schema_version") != 1 or frozen_policy.get("kind") != "qagen_model_routing":
        raise WorkerPolicyError("routing policy kind or schema_version is invalid")
    if frozen_policy.get("enabled") is not True:
        raise ProviderNotConfiguredError("routing policy is disabled")
    local_fixture_only = _is_local_fixture_policy(frozen_policy)
    local_pilot_only = _is_local_pilot_policy(frozen_policy)
    if not (local_fixture_only or local_pilot_only) and frozen_policy.get("run_eligibility") not in {"eligible", "approved"}:
        raise ProviderNotConfiguredError("routing policy is not eligible for execution")
    routing_status = frozen_policy.get("routing_status")
    if routing_status not in {"verified", "approved", "local_test_verified", "approved_local_pilot"}:
        raise ProviderNotConfiguredError("routing policy availability has not been verified")
    providers = frozen_policy.get("providers")
    roles = frozen_policy.get("roles")
    if not isinstance(providers, Mapping) or not isinstance(roles, Mapping):
        raise WorkerPolicyError("routing policy needs providers and roles mappings")
    schemas = _schema_registry(schema_registry)
    for role, role_policy in roles.items():
        if not isinstance(role, str) or role not in ROLE_ROUTING_SUGGESTIONS:
            raise WorkerPolicyError(f"routing policy contains an unsupported worker role: {role!r}")
        if not isinstance(role_policy, Mapping):
            raise WorkerPolicyError(f"routing policy role must be an object: {role}")
        _validate_role_policy(
            role,
            role_policy,
            providers,
            schemas,
            local_fixture_only=local_fixture_only,
            local_pilot_only=local_pilot_only,
        )
    for required_role in ("brief_builder", "author", "truth_builder", "evidence_worker", "verifier", "rewriter"):
        if required_role not in roles:
            raise WorkerPolicyError(f"routing policy is missing required role: {required_role}")
    author = roles["author"]
    verifier = roles["verifier"]
    if author.get("provider") == verifier.get("provider"):
        raise WorkerPolicyError("author and verifier must use different providers")
    author_models = set(author.get("allowed_models", []))
    verifier_models = set(verifier.get("allowed_models", []))
    if author_models.intersection(verifier_models):
        raise WorkerPolicyError("author and verifier must use different models")
    fixture = frozen_policy.get("fixture_adapter")
    if isinstance(fixture, Mapping):
        if fixture.get("local_only") is not True or fixture.get("production_eligible") is not False:
            raise WorkerPolicyError("fixture adapter may only be local and never production eligible")
        if fixture.get("enabled") is True and not local_fixture_only:
            raise WorkerPolicyError("fixture adapter may only be enabled in an explicit local test policy")
    return VerifiedRoutingPolicy(
        _policy=frozen_policy,
        content_digest=content_digest,
        _schemas=schemas,
        local_fixture_only=local_fixture_only,
        local_pilot_only=local_pilot_only,
    )


def load_verified_routing_policy(
    path: str | Path,
    *,
    expected_digest: str | None = None,
    schema_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> VerifiedRoutingPolicy:
    """Load a local YAML policy and require the same validation as in-memory input."""
    policy_path = Path(path).expanduser().resolve()
    try:
        value = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise WorkerPolicyError(f"cannot load routing policy {policy_path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise WorkerPolicyError("routing policy YAML must contain an object")
    return verify_routing_policy(value, expected_digest=expected_digest, schema_registry=schema_registry)


def _configured_policy(policy: Mapping[str, Any], request: WorkerRequest) -> Mapping[str, Any]:
    """Compatibility helper retained for callers that only need route checks."""
    verified = verify_routing_policy(policy)
    return _validate_request_against_policy(verified, request)


def _validate_json_schema(value: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    try:
        errors = sorted(Draft7Validator(dict(schema)).iter_errors(dict(value)), key=lambda error: list(error.absolute_path))
    except Exception as exc:
        raise WorkerContractError(f"cannot validate {label} schema: {exc}") from exc
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise WorkerContractError(f"{label} schema validation failed at {location}: {error.message}")


def _validate_request_against_policy(policy: VerifiedRoutingPolicy, request: WorkerRequest) -> dict[str, Any]:
    if not isinstance(request, WorkerRequest):
        raise WorkerPolicyError("worker request must be a WorkerRequest")
    role = _require_text(request.role, "worker role")
    if role not in ROLE_ROUTING_SUGGESTIONS:
        raise WorkerPolicyError(f"unknown worker role: {role}")
    provider = _require_text(request.provider, "worker provider")
    model = _require_text(request.model, "worker model")
    _require_text(request.run_id, "run_id")
    _require_text(request.work_item_id, "work_item_id")
    if not isinstance(request.input_contract, Mapping):
        raise WorkerPolicyError("worker input_contract must be an object")
    max_tokens = _nonnegative_int(request.max_tokens, "worker max_tokens")
    max_cost = _nonnegative_number(request.max_cost_usd, "worker max_cost_usd")
    role_policy = policy.role(role)
    if role_policy.get("provider") != provider:
        raise WorkerPolicyError("worker provider is not allowed for this role")
    allowed_models = role_policy.get("allowed_models")
    if not isinstance(allowed_models, list) or model not in allowed_models:
        raise WorkerPolicyError("worker model is not in the role allowlist")
    if max_tokens > _nonnegative_int(role_policy.get("max_tokens"), "role policy max_tokens"):
        raise WorkerPolicyError("worker request exceeds role token budget")
    if max_cost > _nonnegative_number(role_policy.get("max_cost_usd"), "role policy max_cost_usd"):
        raise WorkerPolicyError("worker request exceeds role cost budget")
    provider_policy = policy.provider(provider)
    if provider_policy.get("configured") is not True:
        raise ProviderNotConfiguredError(f"worker provider is not configured: {provider}")
    provider_models = provider_policy.get("allowed_models")
    if provider_models is not None:
        if not isinstance(provider_models, list) or model not in provider_models:
            raise WorkerPolicyError("worker model is not in the provider allowlist")
    _validate_json_schema(request.input_contract, policy.schema(str(role_policy["input_schema"])), "worker input")
    return role_policy


def _approved_work_item_count(value: Any) -> int:
    count = _nonnegative_int(value, "approved_work_item_count")
    if count < 1:
        raise WorkerPolicyError("approved_work_item_count must be positive")
    return count


def _default_usage_budget(
    policy: VerifiedRoutingPolicy,
    *,
    approved_work_item_count: int = 1,
) -> UsageBudget:
    """Build the run-level cap from immutable per-call routing limits.

    Each approved work item can invoke each configured role at most once in
    the base graph.  The routing policy still limits every individual request;
    this function only expands the aggregate ledger cap for an approved batch.
    An explicit policy ``usage_budget`` remains an exact run-level cap and is
    deliberately not expanded here.
    """
    count = _approved_work_item_count(approved_work_item_count)
    raw = policy.policy
    explicit = raw.get("usage_budget")
    if isinstance(explicit, Mapping):
        return UsageBudget.from_value(explicit)
    roles = raw.get("roles")
    if not isinstance(roles, Mapping):  # Already checked by verification; preserve fail-closed behavior.
        raise WorkerPolicyError("verified routing policy has no roles")
    role_limits = {
        role: {"max_tokens": value["max_tokens"], "max_cost_usd": value["max_cost_usd"]}
        for role, value in roles.items()
        if isinstance(role, str) and isinstance(value, Mapping)
    }
    return UsageBudget.from_value(
        {
            "total": {
                "max_tokens": count * sum(int(limit["max_tokens"]) for limit in role_limits.values()),
                "max_cost_usd": count * sum(float(limit["max_cost_usd"]) for limit in role_limits.values()),
            },
            "roles": {
                role: {
                    "max_tokens": count * int(limit["max_tokens"]),
                    "max_cost_usd": count * float(limit["max_cost_usd"]),
                }
                for role, limit in role_limits.items()
            },
        }
    )


def _validate_usage_budget(
    policy: VerifiedRoutingPolicy,
    budget: UsageBudget,
    *,
    max_invocations_per_role: int = 1,
) -> UsageBudget:
    """Do not let a caller relax immutable per-call policy limits.

    ``max_invocations_per_role`` is supplied only by the run control plane
    from the approved concrete work-item map.  It permits the ledger to hold
    a batch-wide aggregate cap while retaining the routing limit for every
    individual WorkerRequest.
    """
    multiplier = _approved_work_item_count(max_invocations_per_role)
    roles = policy.policy.get("roles")
    if not isinstance(roles, Mapping):
        raise WorkerPolicyError("verified routing policy has no roles")
    policy_roles = {role for role, value in roles.items() if isinstance(role, str) and isinstance(value, Mapping)}
    if set(budget.role_max_tokens) != policy_roles:
        raise WorkerPolicyError("usage budget must bind every and only verified worker role")
    total_tokens = 0
    total_cost = 0.0
    for role in sorted(policy_roles):
        route = roles[role]
        route_tokens = _nonnegative_int(route.get("max_tokens"), f"roles.{role}.max_tokens")
        route_cost = _nonnegative_number(route.get("max_cost_usd"), f"roles.{role}.max_cost_usd")
        if (
            budget.role_max_tokens[role] > multiplier * route_tokens
            or budget.role_max_cost_usd[role] > multiplier * route_cost
        ):
            raise WorkerPolicyError(f"usage budget relaxes verified role cap for {role}")
        total_tokens += route_tokens
        total_cost += route_cost
    if (
        budget.total_max_tokens > multiplier * total_tokens
        or budget.total_max_cost_usd > multiplier * total_cost
    ):
        raise WorkerPolicyError("usage budget relaxes verified total policy cap")
    return budget


def _canonical_request(request: WorkerRequest) -> WorkerRequest:
    """Freeze the input once before reservation so adapters cannot race it."""
    if not isinstance(request, WorkerRequest) or not isinstance(request.input_contract, Mapping):
        raise WorkerPolicyError("worker request input_contract must be an object")
    try:
        snapshot = json.loads(canonical_json(dict(request.input_contract)).decode("utf-8"))
    except Exception as exc:
        raise WorkerPolicyError(f"worker input_contract cannot be canonically frozen: {exc}") from exc
    if not isinstance(snapshot, dict):
        raise WorkerPolicyError("worker input_contract must freeze to an object")
    return WorkerRequest(
        role=request.role,
        provider=request.provider,
        model=request.model,
        input_contract=snapshot,
        max_tokens=request.max_tokens,
        max_cost_usd=request.max_cost_usd,
        run_id=request.run_id,
        work_item_id=request.work_item_id,
        deadline_monotonic=request.deadline_monotonic,
    )


def _failure_usage(
    error: Exception,
    request: WorkerRequest,
    *,
    completed_result: WorkerResult | None = None,
) -> tuple[int, int, float, str] | None:
    """Choose a conservative settlement for an invocation that did not succeed.

    Reservations prevent concurrent callers from overspending before a worker
    starts.  Settling an unknown failure at zero would immediately give that
    capacity back despite a provider potentially having consumed it.  Use a
    validated result or an explicit ``ProviderUsageError`` only; every other
    failure consumes the request's full reservation.
    """
    if completed_result is not None:
        return (
            _nonnegative_int(completed_result.token_in, "completed worker token_in"),
            _nonnegative_int(completed_result.token_out, "completed worker token_out"),
            _nonnegative_number(completed_result.actual_cost_usd, "completed worker actual_cost_usd"),
            _require_text(completed_result.cost_accounting, "completed worker cost_accounting"),
        )
    if isinstance(error, ProviderUsageError):
        if error.has_trustworthy_usage:
            try:
                token_in = _nonnegative_int(error.token_in, "provider failure token_in")
                token_out = _nonnegative_int(error.token_out, "provider failure token_out")
                cost = _nonnegative_number(error.actual_cost_usd, "provider failure actual_cost_usd")
                if token_in + token_out <= request.max_tokens and cost <= request.max_cost_usd:
                    return token_in, token_out, cost, "provider_reported"
            except WorkerPolicyError:
                # An invalid claimed usage report is not trustworthy. Preserve
                # the original provider failure and hold the reservation below.
                pass
        if error.network_called:
            return None
    return request.max_tokens, 0, request.max_cost_usd, "reservation_upper_bound"


class DeterministicFixtureAdapter:
    """A zero-cost local adapter for tests, never an external-provider fallback."""

    fixture_only = True

    def __init__(self, *, provider: str = "fixture", model: str = "deterministic-fixture") -> None:
        self.provider = provider
        self.model = model

    def execute(self, request: WorkerRequest) -> WorkerResult:
        if request.provider != self.provider or request.model != self.model:
            raise ProviderNotConfiguredError("deterministic fixture adapter cannot impersonate another route")
        output = {
            "artifact_kind": "deterministic_fixture_worker_output",
            "role": request.role,
            "input_contract_digest": request.input_contract_digest,
            "state_transition": None,
        }
        return WorkerResult(
            role=request.role,
            provider=request.provider,
            model=request.model,
            input_contract_digest=request.input_contract_digest,
            output_contract=output,
            output_contract_digest=digest(output),
            fixture_only=True,
            token_in=0,
            token_out=0,
            actual_cost_usd=0.0,
            cost_accounting="zero_cost_fixture",
        )


class WorkerExecutor:
    """Execute one typed worker call against a verified policy and usage budget."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        policy: VerifiedRoutingPolicy,
        usage_budget: UsageBudget | Mapping[str, Any] | None = None,
        approved_work_item_count: int = 1,
        allow_fixture_adapter: bool = False,
        provider_adapters: Mapping[tuple[str, str], TypedWorker] | None = None,
    ) -> None:
        if not isinstance(policy, VerifiedRoutingPolicy):
            raise WorkerPolicyError("WorkerExecutor requires a verified routing policy")
        policy.assert_intact()
        if allow_fixture_adapter and not policy.local_fixture_only:
            raise WorkerPolicyError("fixture adapters require an explicit local fixture policy")
        self.paths = paths
        self.policy = policy
        work_item_count = _approved_work_item_count(approved_work_item_count)
        candidate_budget = (
            UsageBudget.from_value(usage_budget)
            if usage_budget is not None
            else _default_usage_budget(policy, approved_work_item_count=work_item_count)
        )
        # Both default and caller-provided budgets are run-level aggregates.
        # The approved work-item count bounds how far their per-role caps may
        # extend beyond one request; individual requests remain checked
        # against the route's immutable per-call limit below.
        self.usage_budget = _validate_usage_budget(
            policy,
            candidate_budget,
            max_invocations_per_role=work_item_count,
        )
        self.approved_work_item_count = work_item_count
        self.allow_fixture_adapter = allow_fixture_adapter
        adapters: dict[tuple[str, str], TypedWorker] = {}
        if provider_adapters is not None:
            if not isinstance(provider_adapters, Mapping):
                raise WorkerPolicyError("provider_adapters must be a mapping")
            for key, adapter in provider_adapters.items():
                if (
                    not isinstance(key, tuple)
                    or len(key) != 2
                    or not all(isinstance(part, str) and part.strip() for part in key)
                ):
                    raise WorkerPolicyError("provider adapter keys must be (provider, model) string pairs")
                provider, model = key
                if getattr(adapter, "provider", None) != provider or getattr(adapter, "model", None) != model:
                    raise WorkerContractError("registered provider adapter does not match its provider/model key")
                if getattr(adapter, "fixture_only", None) is not False:
                    raise WorkerPolicyError("registered external provider adapters must not be fixtures")
                adapters[(provider, model)] = adapter
        self.provider_adapters = MappingProxyType(adapters)

    def _deadline(self, request: WorkerRequest, role_policy: Mapping[str, Any], started: float) -> float | None:
        deadline = request.deadline_monotonic
        if deadline is not None:
            if not isinstance(deadline, (int, float)) or isinstance(deadline, bool):
                raise WorkerPolicyError("deadline_monotonic must be a numeric monotonic-clock value")
            if float(deadline) <= started:
                raise DeadlineExceededError("worker deadline elapsed before invocation")
        max_latency = float(role_policy["max_latency_seconds"])
        policy_deadline = started + max_latency
        return min(float(deadline), policy_deadline) if deadline is not None else policy_deadline

    def _select_adapter(self, request: WorkerRequest, worker: TypedWorker | None) -> TypedWorker:
        adapter: TypedWorker
        if worker is not None:
            adapter = worker
        elif (request.provider, request.model) in self.provider_adapters:
            adapter = self.provider_adapters[(request.provider, request.model)]
        elif self.allow_fixture_adapter:
            adapter = DeterministicFixtureAdapter(provider=request.provider, model=request.model)
        else:
            raise ProviderNotConfiguredError(
                "no execution adapter is configured for this provider/model; external providers are intentionally not invoked"
            )
        if getattr(adapter, "provider", None) != request.provider or getattr(adapter, "model", None) != request.model:
            raise WorkerContractError("selected worker adapter provider/model does not match the request")
        fixture_only = getattr(adapter, "fixture_only", False)
        if not isinstance(fixture_only, bool):
            raise WorkerContractError("worker adapter fixture_only flag must be boolean")
        if fixture_only and (not self.allow_fixture_adapter or not self.policy.local_fixture_only):
            raise WorkerPolicyError("fixture adapter execution is limited to explicit local tests")
        return adapter

    def _validate_result(
        self,
        request: WorkerRequest,
        result: Any,
        role_policy: Mapping[str, Any],
    ) -> WorkerResult:
        if not isinstance(result, WorkerResult):
            raise WorkerContractError("worker adapter must return WorkerResult")
        expected_input_digest = request.input_contract_digest
        if result.role != request.role or result.provider != request.provider or result.model != request.model:
            raise WorkerContractError("worker result role/provider/model does not match request")
        if result.input_contract_digest != expected_input_digest:
            raise WorkerContractError("worker result is not bound to the requested input contract")
        if not isinstance(result.output_contract, Mapping):
            raise WorkerContractError("worker output_contract must be an object")
        output = dict(result.output_contract)
        actual_output_digest = digest(output)
        if result.output_contract_digest != actual_output_digest:
            raise WorkerContractError("worker output_contract_digest is not canonical")
        output_schema = str(role_policy["output_schema"])
        _validate_json_schema(output, self.policy.schema(output_schema), "worker output")
        if output_schema == "qagen://schemas/verification-result/v1":
            validate_verification_result(output)
        if not isinstance(result.fixture_only, bool):
            raise WorkerContractError("worker result fixture_only must be boolean")
        if result.fixture_only and (not self.allow_fixture_adapter or not self.policy.local_fixture_only):
            raise WorkerContractError("fixture result escaped a local fixture policy")
        # A worker can propose artifacts but cannot smuggle a lifecycle action
        # into a generic schema.  Approval, publish, and state changes remain
        # in the human/control-plane modules.
        for field in ("state_transition", "approval", "publish", "release"):
            if field in output and output[field] is not None and output[field] is not False:
                raise WorkerContractError(f"worker output may not request control-plane action: {field}")
        token_in = _nonnegative_int(result.token_in, "worker result token_in")
        token_out = _nonnegative_int(result.token_out, "worker result token_out")
        actual_cost = _nonnegative_number(result.actual_cost_usd, "worker result actual_cost_usd")
        cost_accounting = _require_text(result.cost_accounting, "worker result cost_accounting")
        if cost_accounting == "automatic":
            cost_accounting = "zero_cost_fixture" if result.fixture_only else "provider_reported"
            result = replace(result, cost_accounting=cost_accounting)
        if cost_accounting not in {"provider_reported", "reservation_upper_bound", "zero_cost_fixture"}:
            raise WorkerContractError("worker result cost_accounting is not recognized")
        if result.fixture_only and cost_accounting != "zero_cost_fixture":
            raise WorkerContractError("fixture worker result must use zero-cost fixture accounting")
        if not result.fixture_only and cost_accounting == "zero_cost_fixture":
            raise WorkerContractError("external worker result cannot claim fixture cost accounting")
        if token_in + token_out > request.max_tokens or actual_cost > request.max_cost_usd:
            raise WorkerContractError("worker result actual usage exceeds its reservation")
        return result

    def invoke(self, request: WorkerRequest, *, worker: TypedWorker | None = None) -> WorkerResult:
        """Validate, reserve, execute, validate output, and settle exactly once."""
        self.policy.assert_intact()
        bound_request = _canonical_request(request)
        role_policy = _validate_request_against_policy(self.policy, bound_request)
        started = perf_counter()
        deadline = self._deadline(bound_request, role_policy, started)
        adapter = self._select_adapter(bound_request, worker)
        reservation = reserve_usage(
            self.paths,
            run_id=str(bound_request.run_id),
            work_item_id=str(bound_request.work_item_id),
            worker=bound_request.role,
            provider=bound_request.provider,
            model=bound_request.model,
            input_contract_digest=bound_request.input_contract_digest,
            max_tokens=bound_request.max_tokens,
            max_cost_usd=bound_request.max_cost_usd,
            budget=self.usage_budget,
        )
        failure_output_digest = digest({"outcome": "worker_failed"})
        completed_result: WorkerResult | None = None
        try:
            # Give adapters a copy.  A malicious adapter can mutate its copy,
            # but it cannot alter the canonical request bound to the ledger.
            adapter_request = WorkerRequest(
                role=bound_request.role,
                provider=bound_request.provider,
                model=bound_request.model,
                input_contract=deepcopy(dict(bound_request.input_contract)),
                max_tokens=bound_request.max_tokens,
                max_cost_usd=bound_request.max_cost_usd,
                run_id=bound_request.run_id,
                work_item_id=bound_request.work_item_id,
                deadline_monotonic=bound_request.deadline_monotonic,
            )
            result = self._validate_result(adapter_request, adapter.execute(adapter_request), role_policy)
            completed_result = result
            elapsed_ms = (perf_counter() - started) * 1000
            if deadline is not None and perf_counter() > deadline:
                raise DeadlineExceededError("worker exceeded its request or policy deadline")
        except Exception as execution_error:
            failure_usage = _failure_usage(
                execution_error,
                bound_request,
                completed_result=completed_result,
            )
            try:
                if failure_usage is None:
                    mark_usage_unsettled(
                        self.paths,
                        reservation=reservation,
                        reason=type(execution_error).__name__,
                    )
                else:
                    token_in, token_out, settled_cost, cost_accounting = failure_usage
                    settle_usage(
                        self.paths,
                        reservation=reservation,
                        token_in=token_in,
                        token_out=token_out,
                        settled_cost_usd=settled_cost,
                        latency_ms=(perf_counter() - started) * 1000,
                        outcome="failed",
                        output_contract_digest=failure_output_digest,
                        cost_accounting=cost_accounting,
                    )
            except Exception as settlement_error:
                raise WorkerContractError("worker failed and its required failure settlement could not be recorded") from settlement_error
            raise execution_error
        settle_usage(
            self.paths,
            reservation=reservation,
            token_in=result.token_in,
            token_out=result.token_out,
            settled_cost_usd=result.actual_cost_usd,
            latency_ms=elapsed_ms,
            outcome="fixture_completed" if result.fixture_only else "completed",
            output_contract_digest=result.output_contract_digest,
            cost_accounting=result.cost_accounting,
        )
        return result


def invoke_typed_worker(
    paths: WorkspacePaths,
    *,
    policy: Mapping[str, Any] | VerifiedRoutingPolicy,
    request: WorkerRequest,
    worker: TypedWorker | None = None,
    usage_budget: UsageBudget | Mapping[str, Any] | None = None,
    allow_fixture_adapter: bool = False,
    provider_adapters: Mapping[tuple[str, str], TypedWorker] | None = None,
    expected_policy_digest: str | None = None,
    schema_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> WorkerResult:
    """Compatibility wrapper around :class:`WorkerExecutor`.

    Passing a raw mapping is permitted only because this wrapper verifies and
    freezes it immediately.  ``WorkerExecutor`` itself accepts only an already
    verified policy object.
    """
    verified = (
        policy
        if isinstance(policy, VerifiedRoutingPolicy)
        else verify_routing_policy(policy, expected_digest=expected_policy_digest, schema_registry=schema_registry)
    )
    if expected_policy_digest is not None and verified.content_digest != expected_policy_digest:
        raise WorkerPolicyError("verified routing policy digest does not match expected lock")
    return WorkerExecutor(
        paths,
        policy=verified,
        usage_budget=usage_budget,
        allow_fixture_adapter=allow_fixture_adapter,
        provider_adapters=provider_adapters,
    ).invoke(request, worker=worker)
