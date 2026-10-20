from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Mapping

import pytest

from qagen.contracts import digest
from qagen.paths import WorkspacePaths
from qagen.storage.ledger import UsageBudget
from qagen.workers import (
    DeadlineExceededError,
    ProviderUsageError,
    WorkerContractError,
    WorkerExecutor,
    WorkerPolicyError,
    WorkerRequest,
    WorkerResult,
    validate_verification_result,
    verify_routing_policy,
)


INPUT_SCHEMA = "qagen://schemas/runtime-test/input/v1"
OUTPUT_SCHEMA = "qagen://schemas/runtime-test/output/v1"
SCHEMAS = {
    INPUT_SCHEMA: {"type": "object", "required": ["task_id"], "additionalProperties": True},
    OUTPUT_SCHEMA: {"type": "object", "required": ["artifact_kind"], "additionalProperties": True},
}


def _verification_result(*, outcome: str = "pass") -> dict[str, Any]:
    return {
        "artifact_kind": "verification_result",
        "candidate_digest": digest({"candidate": 1}),
        "evidence_digest": digest({"evidence": 1}),
        "outcome": outcome,
        "checks": [{"name": "candidate_evidence_binding", "outcome": outcome}],
        "rubric_scores": {
            "authenticity": {"score": 2, "maximum": 2},
            "correctness": {"score": 1, "maximum": 1},
            "three_d_relevance": {"score": 1, "maximum": 1},
        },
        "rubric_lock_digest": digest({"rubric": 1}),
        "work_item_id": "item-1",
        "state_transition": None,
    }


def test_verification_result_schema_and_pass_gate_are_fail_closed() -> None:
    assert validate_verification_result(_verification_result(), require_pass=True)["outcome"] == "pass"

    malformed = _verification_result()
    malformed.pop("checks")
    with pytest.raises(WorkerContractError, match="schema validation failed"):
        validate_verification_result(malformed)

    inconsistent = _verification_result()
    inconsistent["checks"] = [{"name": "candidate_evidence_binding", "outcome": "fail"}]
    with pytest.raises(WorkerContractError, match="passing verification result"):
        validate_verification_result(inconsistent)

    with pytest.raises(WorkerContractError, match="outcome must be pass"):
        validate_verification_result(_verification_result(outcome="fail"), require_pass=True)


def _policy() -> dict[str, Any]:
    routes = {
        "brief_builder": ("fixture-brief", "brief-model"),
        "author": ("fixture-author", "author-model"),
        "truth_builder": ("fixture-author", "truth-model"),
        "evidence_worker": ("fixture-author", "evidence-model"),
        "verifier": ("fixture-verifier", "verifier-model"),
        "rewriter": ("fixture-author", "rewriter-model"),
    }
    providers = {
        provider: {"configured": True, "local_only": True}
        for provider, _ in routes.values()
    }
    return {
        "schema_version": 1,
        "kind": "qagen_model_routing",
        "enabled": True,
        "routing_status": "local_test_verified",
        "run_eligibility": "local_test",
        "providers": providers,
        "roles": {
            role: {
                "enabled": True,
                "provider": provider,
                "allowed_models": [model],
                "max_tokens": 10,
                "max_cost_usd": 1.0,
                "max_latency_seconds": 5,
                "input_schema": INPUT_SCHEMA,
                "output_schema": OUTPUT_SCHEMA,
            }
            for role, (provider, model) in routes.items()
        },
        "fixture_adapter": {
            "enabled": True,
            "local_only": True,
            "production_eligible": False,
        },
    }


def _request(**changes: Any) -> WorkerRequest:
    values: dict[str, Any] = {
        "role": "author",
        "provider": "fixture-author",
        "model": "author-model",
        "input_contract": {"task_id": "task-1"},
        "max_tokens": 4,
        "max_cost_usd": 0.4,
        "run_id": "run-1",
        "work_item_id": "item-1",
    }
    values.update(changes)
    return WorkerRequest(**values)


def _executor(tmp_path, *, policy: Mapping[str, Any] | None = None, budget: UsageBudget | None = None) -> WorkerExecutor:
    verified = verify_routing_policy(policy or _policy(), schema_registry=SCHEMAS)
    return WorkerExecutor(
        WorkspacePaths.from_value(tmp_path / "workspace"),
        policy=verified,
        usage_budget=budget,
        allow_fixture_adapter=True,
    )


class _Adapter:
    fixture_only = True

    def __init__(self, *, provider: str = "fixture-author", model: str = "author-model", tokens: int = 0) -> None:
        self.provider = provider
        self.model = model
        self.tokens = tokens

    def execute(self, request: WorkerRequest) -> WorkerResult:
        output = {"artifact_kind": "runtime-test-output", "state_transition": None}
        return WorkerResult(
            role=request.role,
            provider=request.provider,
            model=request.model,
            input_contract_digest=request.input_contract_digest,
            output_contract=output,
            output_contract_digest=digest(output),
            fixture_only=True,
            token_in=self.tokens,
            token_out=0,
            actual_cost_usd=0.1 if self.tokens else 0.0,
        )


def test_verified_policy_rejects_shared_author_verifier_route() -> None:
    policy = _policy()
    policy["roles"]["verifier"] = {
        **policy["roles"]["author"],
        "allowed_models": ["author-model"],
    }

    with pytest.raises(WorkerPolicyError, match="different providers"):
        verify_routing_policy(policy, schema_registry=SCHEMAS)


def test_executor_validates_input_schema_before_reserving(tmp_path) -> None:
    executor = _executor(tmp_path)

    with pytest.raises(WorkerContractError, match="input schema"):
        executor.invoke(_request(input_contract={}), worker=_Adapter())
    assert not executor.paths.usage.exists()


def test_executor_rejects_malicious_result_and_records_failed_settlement(tmp_path) -> None:
    class WrongModelAdapter(_Adapter):
        def execute(self, request: WorkerRequest) -> WorkerResult:
            result = super().execute(request)
            return WorkerResult(
                role=result.role,
                provider=result.provider,
                model="impersonated-model",
                input_contract_digest=result.input_contract_digest,
                output_contract=result.output_contract,
                output_contract_digest=result.output_contract_digest,
                fixture_only=True,
            )

    executor = _executor(tmp_path)
    with pytest.raises(WorkerContractError, match="role/provider/model"):
        executor.invoke(_request(), worker=WrongModelAdapter())

    records = executor.paths.usage.read_text(encoding="utf-8").splitlines()
    assert len(records) == 2
    assert '"kind":"usage_settlement"' in records[1]
    assert '"status":"failed"' in records[1]


def test_worker_failure_consumes_the_reserved_maximum_unless_provider_usage_is_trusted(tmp_path) -> None:
    class FailingAdapter(_Adapter):
        def execute(self, request: WorkerRequest) -> WorkerResult:
            raise RuntimeError("provider connection dropped")

    executor = _executor(tmp_path)
    with pytest.raises(RuntimeError, match="connection dropped"):
        executor.invoke(_request(), worker=FailingAdapter())

    records = [json.loads(line) for line in executor.paths.usage.read_text(encoding="utf-8").splitlines()]
    settlement = records[-1]["body"]
    assert settlement["token_in"] == 4
    assert settlement["token_out"] == 0
    assert settlement["settled_cost_usd"] == 0.4


def test_worker_failure_uses_explicit_bounded_provider_usage_when_available(tmp_path) -> None:
    class ReportedUsageAdapter(_Adapter):
        def execute(self, request: WorkerRequest) -> WorkerResult:
            raise ProviderUsageError("provider rejected request", token_in=2, token_out=1, actual_cost_usd=0.3)

    executor = _executor(tmp_path)
    with pytest.raises(ProviderUsageError, match="rejected request"):
        executor.invoke(_request(), worker=ReportedUsageAdapter())

    records = [json.loads(line) for line in executor.paths.usage.read_text(encoding="utf-8").splitlines()]
    settlement = records[-1]["body"]
    assert settlement["token_in"] == 2
    assert settlement["token_out"] == 1
    assert settlement["settled_cost_usd"] == 0.3


def test_fixture_adapter_cannot_escape_explicit_local_test_mode(tmp_path) -> None:
    policy = _policy()
    policy["run_eligibility"] = "eligible"
    policy["routing_status"] = "verified"
    policy["fixture_adapter"]["enabled"] = False
    for provider in policy["providers"].values():
        provider["local_only"] = False
    verified = verify_routing_policy(policy, schema_registry=SCHEMAS)

    with pytest.raises(WorkerPolicyError, match="fixture adapters require"):
        WorkerExecutor(WorkspacePaths.from_value(tmp_path / "workspace"), policy=verified, allow_fixture_adapter=True)


def test_deadline_and_policy_bound_usage_budget_are_enforced(tmp_path) -> None:
    policy = _policy()
    role_limits = {role: 10 for role in policy["roles"]}
    budget = UsageBudget(
        total_max_tokens=4,
        total_max_cost_usd=1.0,
        role_max_tokens=role_limits,
        role_max_cost_usd={role: 1.0 for role in role_limits},
    )
    executor = _executor(tmp_path, budget=budget)

    with pytest.raises(DeadlineExceededError, match="deadline elapsed"):
        executor.invoke(_request(deadline_monotonic=perf_counter() - 1), worker=_Adapter())
    executor.invoke(_request(), worker=_Adapter(tokens=4))
    with pytest.raises(Exception, match="total run budget"):
        executor.invoke(_request(work_item_id="item-2", max_tokens=1), worker=_Adapter(tokens=1))


def test_executor_accepts_a_capped_explicit_aggregate_budget_for_multiple_items(tmp_path) -> None:
    policy = verify_routing_policy(_policy(), schema_registry=SCHEMAS)
    role_limits = set(policy.policy["roles"])
    budget = UsageBudget(
        total_max_tokens=120,
        total_max_cost_usd=12.0,
        role_max_tokens={role: 20 for role in role_limits},
        role_max_cost_usd={role: 2.0 for role in role_limits},
    )

    executor = WorkerExecutor(
        WorkspacePaths.from_value(tmp_path / "workspace"),
        policy=policy,
        usage_budget=budget,
        approved_work_item_count=2,
        allow_fixture_adapter=True,
    )

    assert executor.usage_budget == budget


def test_fixture_adapter_returns_canonical_contract_under_valid_local_policy(tmp_path) -> None:
    executor = _executor(tmp_path)
    result = executor.invoke(_request(), worker=_Adapter())

    assert result.output_contract_digest == digest(dict(result.output_contract))
    assert result.input_contract_digest == _request().input_contract_digest
    assert result.fixture_only is True
