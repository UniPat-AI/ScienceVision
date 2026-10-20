from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from qagen.contracts import digest
from qagen.paths import WorkspacePaths
from qagen.storage.ledger import reserve_usage, settle_usage
from qagen.workers import (
    ProviderNotConfiguredError,
    WorkerPolicyError,
    WorkerRequest,
    invoke_typed_worker,
    routing_suggestion,
)

from conftest import PACKAGE_ROOT


def _fixture_policy() -> dict:
    fixture_role = {
        "enabled": True,
        "provider": "fixture",
        "allowed_models": ["deterministic-fixture"],
        "max_tokens": 16,
        "max_cost_usd": 0.25,
        "max_latency_seconds": 10,
        "input_schema": "qagen://schemas/author/input/v1",
        "output_schema": "qagen://schemas/test-fixture-output/v1",
    }
    return {
        "schema_version": 1,
        "kind": "qagen_model_routing",
        "enabled": True,
        "routing_status": "local_test_verified",
        "run_eligibility": "local_test",
        "providers": {
            "fixture": {"configured": True, "local_only": True},
            "verifier-fixture": {"configured": True, "local_only": True},
        },
        "roles": {
            "brief_builder": dict(fixture_role),
            "author": dict(fixture_role),
            "truth_builder": dict(fixture_role),
            "evidence_worker": dict(fixture_role),
            "rewriter": dict(fixture_role),
            "verifier": {
                "enabled": True,
                "provider": "verifier-fixture",
                "allowed_models": ["deterministic-verifier"],
                "max_tokens": 16,
                "max_cost_usd": 0.25,
                "max_latency_seconds": 10,
                "input_schema": "qagen://schemas/verifier/input/v1",
                "output_schema": "qagen://schemas/verification-result/v1",
            },
        },
        "fixture_adapter": {
            "enabled": True,
            "local_only": True,
            "production_eligible": False,
            "provider": "fixture",
            "model": "deterministic-fixture",
        },
    }


def _fixture_request() -> WorkerRequest:
    return WorkerRequest(
        role="author",
        provider="fixture",
        model="deterministic-fixture",
        input_contract={"task_id": "fixture-task-1", "prompt_digest": digest({"prompt": "controlled"})},
        max_tokens=8,
        max_cost_usd=0.10,
        run_id="fixture-run-1",
        work_item_id="fixture-work-item-1",
    )


TEST_SCHEMA_REGISTRY = {"qagen://schemas/test-fixture-output/v1": {"type": "object"}}


def test_checked_in_routing_uses_the_required_distinct_role_models() -> None:
    policy = yaml.safe_load((PACKAGE_ROOT / "configs" / "agents" / "model_routing.yaml").read_text(encoding="utf-8"))

    assert {
        role: (entry["provider"], entry["allowed_models"])
        for role, entry in policy["roles"].items()
    } == {
        "brief_builder": ("anthropic", ["opus-5"]),
        "author": ("deepseek", ["dpsk-v4-pro"]),
        "truth_builder": ("deepseek", ["dpsk-v4-pro"]),
        "evidence_worker": ("deepseek", ["dpsk-v4-pro"]),
        "verifier": ("openai", ["gpt-5.6-sol"]),
        "rewriter": ("deepseek", ["dpsk-v4-pro"]),
    }
    assert all(provider["configured"] is False for provider in policy["providers"].values())
    for role in policy["roles"].values():
        assert role["enabled"] is False
        assert role["max_latency_seconds"] > 0
        assert role["input_schema"].startswith("qagen://schemas/")
        assert role["output_schema"].startswith("qagen://schemas/")


def test_missing_policy_mappings_fail_closed(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    policy = _fixture_policy()
    policy["providers"] = {}

    with pytest.raises(ProviderNotConfiguredError, match="not configured"):
        invoke_typed_worker(
            paths,
            policy=policy,
            request=_fixture_request(),
            allow_fixture_adapter=True,
            schema_registry=TEST_SCHEMA_REGISTRY,
        )


def test_checked_in_disabled_provider_policy_cannot_execute(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    policy = yaml.safe_load((PACKAGE_ROOT / "configs" / "agents" / "model_routing.yaml").read_text(encoding="utf-8"))
    request = WorkerRequest(
        role="author",
        provider="deepseek",
        model="dpsk-v4-pro",
        input_contract={"task_id": "fixture-task-1"},
        max_tokens=1,
        max_cost_usd=0.01,
    )

    with pytest.raises(ProviderNotConfiguredError, match="disabled"):
        invoke_typed_worker(paths, policy=policy, request=request)


def test_fixture_worker_requires_explicit_matching_fixture_policy_and_settles_usage(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    request = _fixture_request()

    result = invoke_typed_worker(
        paths,
        policy=_fixture_policy(),
        request=request,
        allow_fixture_adapter=True,
        schema_registry=TEST_SCHEMA_REGISTRY,
    )

    assert result.fixture_only is True
    assert result.input_contract_digest == request.input_contract_digest
    assert result.output_contract["state_transition"] is None
    records = [json.loads(line) for line in paths.usage.read_text(encoding="utf-8").splitlines()]
    reservation, settlement = records
    assert reservation["kind"] == "usage_reservation"
    assert settlement["kind"] == "usage_settlement"
    assert settlement["body"]["reservation_digest"] == reservation["content_digest"]


def test_fixture_worker_rejects_a_model_outside_its_explicit_allowlist(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    policy = _fixture_policy()
    request = WorkerRequest(
        role="author",
        provider="fixture",
        model="different-model",
        input_contract={"task_id": "fixture-task-1"},
        max_tokens=1,
        max_cost_usd=0.01,
        run_id="fixture-run-1",
        work_item_id="fixture-work-item-1",
    )

    with pytest.raises(WorkerPolicyError, match="not in the role allowlist"):
        invoke_typed_worker(
            paths,
            policy=policy,
            request=request,
            allow_fixture_adapter=True,
            schema_registry=TEST_SCHEMA_REGISTRY,
        )


def test_usage_settlement_cannot_exceed_its_reservation(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    reservation = reserve_usage(
        paths,
        worker="author",
        model="fixture:deterministic-fixture",
        input_contract_digest=digest({"task_id": "fixture-task-1"}),
        max_tokens=3,
        max_cost_usd=0.02,
    )

    with pytest.raises(ValueError, match="exceeds reservation"):
        settle_usage(
            paths,
            reservation=reservation,
            token_in=2,
            token_out=2,
            settled_cost_usd=0.01,
            latency_ms=1.0,
            outcome="completed",
        )


def test_routing_suggestions_distinguish_author_and_verifier() -> None:
    assert routing_suggestion("author") == "dpsk-v4-pro"
    assert routing_suggestion("verifier") == "gpt-5.6-sol"
    assert routing_suggestion("author") != routing_suggestion("verifier")


def test_worker_policy_rejects_an_over_budget_fixture_request(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    request = _fixture_request()
    request = WorkerRequest(
        role=request.role,
        provider=request.provider,
        model=request.model,
        input_contract=request.input_contract,
        max_tokens=17,
        max_cost_usd=request.max_cost_usd,
        run_id=request.run_id,
        work_item_id=request.work_item_id,
    )

    with pytest.raises(WorkerPolicyError, match="exceeds role token budget"):
        invoke_typed_worker(
            paths,
            policy=_fixture_policy(),
            request=request,
            allow_fixture_adapter=True,
            schema_registry=TEST_SCHEMA_REGISTRY,
        )
