"""Minimal deterministic worker orchestration.

The orchestrator can create typed artifacts and request a single rewrite after
an externally-issued, candidate-specific revision receipt.  It has no API for
approving a plan, deciding domain review, publishing, or exporting a bundle.
Those are intentionally owned by the control-plane modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..contracts import digest, make_envelope, require_digest, verify_envelope
from ..errors import InputError, InvalidStateError
from ..workers import TypedWorker, WorkerExecutor, WorkerRequest, WorkerResult


class OrchestrationError(InputError):
    """The constrained worker sequence or its external receipt is invalid."""


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise OrchestrationError(f"{field} must be a non-empty string")
    return value.strip()


def _stage_payload(
    *,
    role: str,
    run_id: str,
    work_item_id: str,
    root_input: Mapping[str, Any],
    dependencies: Mapping[str, WorkerResult],
    revision_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make one canonical input artifact from already-bound prior outputs."""
    payload: dict[str, Any] = {
        "artifact_kind": "qagen_worker_input",
        "stage": role,
        "run_id": run_id,
        "work_item_id": work_item_id,
        "root_input": dict(root_input),
        "dependencies": {
            name: {
                "output_contract_digest": result.output_contract_digest,
                "output_contract": dict(result.output_contract),
            }
            for name, result in sorted(dependencies.items())
        },
    }
    if revision_receipt is not None:
        payload["revision_receipt"] = dict(revision_receipt)
    return payload


def make_revision_receipt(
    *,
    run_id: str,
    work_item_id: str,
    candidate_digest: str,
    verifier_output_digest: str,
    reviewer: str,
    rationale: str,
    review_decision_digest: str | None = None,
) -> dict[str, Any]:
    """Create the narrowly scoped external receipt consumed by ``rewriter``.

    This helper only makes a receipt artifact for tests/integration boundaries;
    it does not transition a run or grant approval.  Real callers should write
    it from the human review path before handing it to this runtime.
    """
    require_digest(candidate_digest, "candidate_digest")
    require_digest(verifier_output_digest, "verifier_output_digest")
    if not isinstance(rationale, str) or not rationale.strip():
        raise OrchestrationError("revision receipt requires a non-empty rationale")
    if review_decision_digest is not None:
        require_digest(review_decision_digest, "review_decision_digest")
    body = {
        "decision": "revise",
        "actor_id": _require_text(reviewer, "reviewer"),
        "actor_role": "domain_expert",
        "run_id": _require_text(run_id, "run_id"),
        "work_item_id": _require_text(work_item_id, "work_item_id"),
        "candidate_digest": candidate_digest,
        "verifier_output_digest": verifier_output_digest,
        "rationale": rationale.strip(),
        "reasons": [rationale.strip()],
    }
    if review_decision_digest is not None:
        body["review_decision_digest"] = review_decision_digest
    return make_envelope(
        kind="qagen_revision_receipt",
        identifier=f"revision-{candidate_digest.split(':', 1)[1][:16]}",
        status="revise",
        body=body,
        producer="qagen/0.1.0:external_human_review",
    )


def _validate_revision_receipt(
    receipt: Mapping[str, Any],
    *,
    run_id: str,
    work_item_id: str,
    candidate_digest: str,
    verifier_output_digest: str,
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise OrchestrationError("revision receipt must be an envelope")
    try:
        envelope = verify_envelope(receipt, kind="qagen_revision_receipt")
    except Exception as exc:
        raise OrchestrationError(f"revision receipt is invalid: {exc}") from exc
    if envelope.get("status") != "revise":
        raise OrchestrationError("revision receipt status must be revise")
    body = envelope["body"]
    if not isinstance(body, Mapping):
        raise OrchestrationError("revision receipt body is malformed")
    if body.get("decision") != "revise":
        raise OrchestrationError("revision receipt decision must be revise")
    if body.get("actor_role") not in {"domain_expert", "human_reviewer"}:
        raise OrchestrationError("revision receipt must be issued by a human domain reviewer")
    _require_text(body.get("actor_id"), "revision receipt actor_id")
    if body.get("run_id") != run_id or body.get("work_item_id") != work_item_id:
        raise OrchestrationError("revision receipt is bound to another run or work item")
    if body.get("candidate_digest") != candidate_digest:
        raise OrchestrationError("revision receipt is not bound to the authored candidate")
    if body.get("verifier_output_digest") != verifier_output_digest:
        raise OrchestrationError("revision receipt is not bound to this verifier result")
    if not isinstance(body.get("rationale"), str) or not body["rationale"].strip():
        raise OrchestrationError("revision receipt rationale is required")
    reasons = body.get("reasons")
    if reasons != [body["rationale"].strip()]:
        raise OrchestrationError("revision receipt reasons must bind the review rationale")
    if "review_decision_digest" in body:
        require_digest(body.get("review_decision_digest"), "revision receipt review decision digest")
    return envelope


@dataclass(frozen=True)
class OrchestrationResult:
    """Private staged outputs; its status deliberately is never approval/publish."""

    run_id: str
    work_item_id: str
    stages: Mapping[str, WorkerResult]
    revision_count: int
    status: str
    revision_receipt_digest: str | None = None
    root_input_digest: str = ""

    @property
    def candidate(self) -> WorkerResult:
        return self.stages["rewriter"] if "rewriter" in self.stages else self.stages["author"]


class WorkerOrchestrator:
    """Run the fixed worker graph with no authority to approve or publish."""

    _BASE_STAGES = ("brief_builder", "author", "truth_builder", "evidence_worker", "verifier")

    def __init__(self, executor: WorkerExecutor) -> None:
        if not isinstance(executor, WorkerExecutor):
            raise OrchestrationError("WorkerOrchestrator requires a WorkerExecutor")
        self._executor = executor
        self._revision_counts: dict[tuple[str, str], int] = {}

    def _request(
        self,
        *,
        role: str,
        run_id: str,
        work_item_id: str,
        input_contract: Mapping[str, Any],
        deadline_monotonic: float | None,
    ) -> WorkerRequest:
        route = self._executor.policy.role(role)
        models = route.get("allowed_models")
        if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], str):
            raise OrchestrationError(f"deterministic orchestrator requires exactly one model for {role}")
        return WorkerRequest(
            role=role,
            provider=str(route["provider"]),
            model=models[0],
            input_contract=input_contract,
            max_tokens=int(route["max_tokens"]),
            max_cost_usd=float(route["max_cost_usd"]),
            run_id=run_id,
            work_item_id=work_item_id,
            deadline_monotonic=deadline_monotonic,
        )

    def _invoke(
        self,
        *,
        role: str,
        run_id: str,
        work_item_id: str,
        root_input: Mapping[str, Any],
        dependencies: Mapping[str, WorkerResult],
        adapters: Mapping[str, TypedWorker] | None,
        deadline_monotonic: float | None,
        revision_receipt: Mapping[str, Any] | None = None,
    ) -> WorkerResult:
        request = self._request(
            role=role,
            run_id=run_id,
            work_item_id=work_item_id,
            input_contract=_stage_payload(
                role=role,
                run_id=run_id,
                work_item_id=work_item_id,
                root_input=root_input,
                dependencies=dependencies,
                revision_receipt=revision_receipt,
            ),
            deadline_monotonic=deadline_monotonic,
        )
        adapter = adapters.get(role) if adapters is not None else None
        return self._executor.invoke(request, worker=adapter)

    def rewrite(
        self,
        base: OrchestrationResult,
        *,
        input_contract: Mapping[str, Any],
        revision_receipt: Mapping[str, Any],
        adapters: Mapping[str, TypedWorker] | None = None,
        deadline_monotonic: float | None = None,
    ) -> OrchestrationResult:
        """Use one human receipt to rewrite an already verified base result.

        This is the runtime's resume point after human review.  It deliberately
        reuses the exact author/verifier artifacts from ``base`` instead of
        rerunning a potentially non-deterministic author and invalidating the
        receipt's candidate binding.
        """
        if not isinstance(base, OrchestrationResult):
            raise OrchestrationError("rewrite requires an OrchestrationResult")
        if not isinstance(input_contract, Mapping):
            raise OrchestrationError("orchestration input_contract must be an object")
        run_id = _require_text(base.run_id, "base run_id")
        work_item_id = _require_text(base.work_item_id, "base work_item_id")
        if base.revision_count >= 1 or "rewriter" in base.stages:
            raise InvalidStateError("work item already used its one allowed revision")
        if base.root_input_digest != digest(dict(input_contract)):
            raise OrchestrationError("rewrite input does not match the original orchestration input")
        required = {"brief_builder", "author", "truth_builder", "evidence_worker", "verifier"}
        if not required.issubset(base.stages) or not all(isinstance(base.stages[name], WorkerResult) for name in required):
            raise OrchestrationError("base orchestration result lacks the fixed verified worker graph")
        key = (run_id, work_item_id)
        if self._revision_counts.get(key, 0) >= 1:
            raise InvalidStateError("work item already used its one allowed revision")
        receipt = _validate_revision_receipt(
            revision_receipt,
            run_id=run_id,
            work_item_id=work_item_id,
            candidate_digest=base.stages["author"].output_contract_digest,
            verifier_output_digest=base.stages["verifier"].output_contract_digest,
        )
        dependencies = {name: base.stages[name] for name in sorted(required)}
        rewritten = self._invoke(
            role="rewriter",
            run_id=run_id,
            work_item_id=work_item_id,
            root_input=input_contract,
            dependencies=dependencies,
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
            revision_receipt=receipt,
        )
        self._revision_counts[key] = 1
        return OrchestrationResult(
            run_id=run_id,
            work_item_id=work_item_id,
            stages={**dict(base.stages), "rewriter": rewritten},
            revision_count=1,
            status="revision_generated_pending_verification_and_human_review",
            revision_receipt_digest=receipt["content_digest"],
            root_input_digest=base.root_input_digest,
        )

    def run(
        self,
        *,
        run_id: str,
        work_item_id: str,
        input_contract: Mapping[str, Any],
        adapters: Mapping[str, TypedWorker] | None = None,
        revision_receipt: Mapping[str, Any] | None = None,
        prior_revision_count: int = 0,
        deadline_monotonic: float | None = None,
    ) -> OrchestrationResult:
        """Run the immutable base graph and, only with receipt, one rewrite.

        The verifier's own output can never select the rewriter.  A separately
        supplied, exact-digest human receipt is required and a work item cannot
        pass through this method with more than one revision.
        """
        canonical_run_id = _require_text(run_id, "run_id")
        canonical_work_item_id = _require_text(work_item_id, "work_item_id")
        if not isinstance(input_contract, Mapping):
            raise OrchestrationError("orchestration input_contract must be an object")
        if not isinstance(prior_revision_count, int) or isinstance(prior_revision_count, bool) or prior_revision_count < 0:
            raise OrchestrationError("prior_revision_count must be a non-negative integer")
        key = (canonical_run_id, canonical_work_item_id)
        seen_revisions = self._revision_counts.get(key, 0)
        if prior_revision_count + seen_revisions > 1:
            raise InvalidStateError("work item already used its one allowed revision")
        if revision_receipt is not None and prior_revision_count + seen_revisions >= 1:
            raise InvalidStateError("work item already used its one allowed revision")

        stages: dict[str, WorkerResult] = {}
        stages["brief_builder"] = self._invoke(
            role="brief_builder",
            run_id=canonical_run_id,
            work_item_id=canonical_work_item_id,
            root_input=input_contract,
            dependencies={},
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
        )
        stages["author"] = self._invoke(
            role="author",
            run_id=canonical_run_id,
            work_item_id=canonical_work_item_id,
            root_input=input_contract,
            dependencies={"brief_builder": stages["brief_builder"]},
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
        )
        author_dependencies = {"brief_builder": stages["brief_builder"], "author": stages["author"]}
        stages["truth_builder"] = self._invoke(
            role="truth_builder",
            run_id=canonical_run_id,
            work_item_id=canonical_work_item_id,
            root_input=input_contract,
            dependencies=author_dependencies,
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
        )
        stages["evidence_worker"] = self._invoke(
            role="evidence_worker",
            run_id=canonical_run_id,
            work_item_id=canonical_work_item_id,
            root_input=input_contract,
            dependencies=author_dependencies,
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
        )
        verifier_dependencies = {
            **author_dependencies,
            "truth_builder": stages["truth_builder"],
            "evidence_worker": stages["evidence_worker"],
        }
        stages["verifier"] = self._invoke(
            role="verifier",
            run_id=canonical_run_id,
            work_item_id=canonical_work_item_id,
            root_input=input_contract,
            dependencies=verifier_dependencies,
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
        )

        base = OrchestrationResult(
            run_id=canonical_run_id,
            work_item_id=canonical_work_item_id,
            stages=stages,
            revision_count=prior_revision_count + seen_revisions,
            status="verification_complete_pending_human_review",
            root_input_digest=digest(dict(input_contract)),
        )
        if revision_receipt is None:
            return base
        return self.rewrite(
            base,
            input_contract=input_contract,
            revision_receipt=revision_receipt,
            adapters=adapters,
            deadline_monotonic=deadline_monotonic,
        )
