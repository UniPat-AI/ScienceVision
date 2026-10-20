from __future__ import annotations

from typing import Any

import pytest

from qagen.contracts import digest
from qagen.errors import InvalidStateError
from qagen.paths import WorkspacePaths
from qagen.pipeline import OrchestrationError, WorkerOrchestrator, make_revision_receipt
from qagen.workers import WorkerExecutor, WorkerRequest, WorkerResult, verify_routing_policy


INPUT_SCHEMA = "qagen://schemas/orchestrator-test/input/v1"
OUTPUT_SCHEMA = "qagen://schemas/orchestrator-test/output/v1"
SCHEMAS = {INPUT_SCHEMA: {"type": "object"}, OUTPUT_SCHEMA: {"type": "object", "required": ["artifact_kind"]}}


def _policy() -> dict[str, Any]:
    routes = {
        "brief_builder": ("fixture-brief", "brief-model"),
        "author": ("fixture-author", "author-model"),
        "truth_builder": ("fixture-author", "truth-model"),
        "evidence_worker": ("fixture-author", "evidence-model"),
        "verifier": ("fixture-verifier", "verifier-model"),
        "rewriter": ("fixture-author", "rewriter-model"),
    }
    return {
        "schema_version": 1,
        "kind": "qagen_model_routing",
        "enabled": True,
        "routing_status": "local_test_verified",
        "run_eligibility": "local_test",
        "providers": {provider: {"configured": True, "local_only": True} for provider, _ in routes.values()},
        "roles": {
            role: {
                "enabled": True,
                "provider": provider,
                "allowed_models": [model],
                "max_tokens": 20,
                "max_cost_usd": 1.0,
                "max_latency_seconds": 5,
                "input_schema": INPUT_SCHEMA,
                "output_schema": OUTPUT_SCHEMA,
            }
            for role, (provider, model) in routes.items()
        },
        "fixture_adapter": {"enabled": True, "local_only": True, "production_eligible": False},
    }


class _RecordingAdapter:
    fixture_only = True

    def __init__(self, role: str, provider: str, model: str, order: list[str], *, revision_signal: bool = False) -> None:
        self.role = role
        self.provider = provider
        self.model = model
        self.order = order
        self.revision_signal = revision_signal

    def execute(self, request: WorkerRequest) -> WorkerResult:
        self.order.append(request.role)
        output: dict[str, Any] = {
            "artifact_kind": "orchestrator-test-output",
            "role": request.role,
            "input_contract_digest": request.input_contract_digest,
            "state_transition": None,
        }
        if self.revision_signal:
            output["revision_requested"] = True
        return WorkerResult(
            role=request.role,
            provider=request.provider,
            model=request.model,
            input_contract_digest=request.input_contract_digest,
            output_contract=output,
            output_contract_digest=digest(output),
            fixture_only=True,
        )


def _orchestrator(tmp_path):
    policy = verify_routing_policy(_policy(), schema_registry=SCHEMAS)
    executor = WorkerExecutor(WorkspacePaths.from_value(tmp_path / "workspace"), policy=policy, allow_fixture_adapter=True)
    orchestrator = WorkerOrchestrator(executor)
    order: list[str] = []
    adapters = {
        role: _RecordingAdapter(role, route["provider"], route["allowed_models"][0], order, revision_signal=role == "verifier")
        for role, route in policy.policy["roles"].items()
    }
    return orchestrator, adapters, order


def test_orchestrator_runs_fixed_graph_and_never_infers_rewrite_from_verifier(tmp_path) -> None:
    orchestrator, adapters, order = _orchestrator(tmp_path)

    result = orchestrator.run(run_id="run-a", work_item_id="item-a", input_contract={"seed": "x"}, adapters=adapters)

    assert order == ["brief_builder", "author", "truth_builder", "evidence_worker", "verifier"]
    assert "rewriter" not in result.stages
    assert result.status == "verification_complete_pending_human_review"
    assert result.stages["verifier"].output_contract["revision_requested"] is True
    assert not hasattr(orchestrator, "approve")
    assert not hasattr(orchestrator, "publish")


def test_orchestrator_requires_exact_human_receipt_for_one_rewrite(tmp_path) -> None:
    orchestrator, adapters, order = _orchestrator(tmp_path)
    initial = orchestrator.run(run_id="run-a", work_item_id="item-a", input_contract={"seed": "x"}, adapters=adapters)
    receipt = make_revision_receipt(
        run_id="run-a",
        work_item_id="item-a",
        candidate_digest=initial.stages["author"].output_contract_digest,
        verifier_output_digest=initial.stages["verifier"].output_contract_digest,
        reviewer="expert-1",
        rationale="The observation needs a more specific visible target.",
    )
    order.clear()

    revised = orchestrator.rewrite(
        initial,
        input_contract={"seed": "x"},
        adapters=adapters,
        revision_receipt=receipt,
    )

    assert order == ["rewriter"]
    assert revised.revision_count == 1
    assert revised.revision_receipt_digest == receipt["content_digest"]
    assert revised.status == "revision_generated_pending_verification_and_human_review"
    with pytest.raises(InvalidStateError, match="one allowed revision"):
        orchestrator.rewrite(
            initial,
            input_contract={"seed": "x"},
            adapters=adapters,
            revision_receipt=receipt,
        )


def test_orchestrator_rejects_receipt_bound_to_another_candidate(tmp_path) -> None:
    orchestrator, adapters, order = _orchestrator(tmp_path)
    initial = orchestrator.run(run_id="run-a", work_item_id="item-a", input_contract={"seed": "x"}, adapters=adapters)
    wrong_receipt = make_revision_receipt(
        run_id="run-a",
        work_item_id="item-a",
        candidate_digest=digest({"wrong": "candidate"}),
        verifier_output_digest=initial.stages["verifier"].output_contract_digest,
        reviewer="expert-1",
        rationale="Specific but wrong binding.",
    )
    order.clear()

    with pytest.raises(OrchestrationError, match="authored candidate"):
        orchestrator.rewrite(
            initial,
            input_contract={"seed": "x"},
            adapters=adapters,
            revision_receipt=wrong_receipt,
        )
    assert "rewriter" not in order
