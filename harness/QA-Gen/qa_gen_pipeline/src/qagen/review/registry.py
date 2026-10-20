"""Digest-bound human review decisions with one revision per candidate key."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
from typing import Any, Mapping

from jsonschema import Draft7Validator

from ..contracts import canonical_json, digest, make_decision, verify_envelope
from ..evidence_validation import EvidenceValidationError, validate_persisted_evidence
from ..errors import InputError, InvalidStateError
from ..paths import WorkspacePaths, require_within
from ..pipeline import make_revision_receipt
from ..resource_paths import PackageResourceError, config_path
from ..run import (
    load_run,
    record_review_decision_locked,
    resolve_plan_work_item,
    run_directory,
    run_state_lock,
    validate_run_locks,
)
from ..storage.atomic import read_json
from ..storage.ledger import append_event
from ..verification import (
    FeedbackPolicy,
    FeedbackPolicyError,
    GroundTruthRegistry,
    GroundTruthRegistryError,
    QuestionContractError,
    load_plan_locked_ground_truth_registry,
    load_question_text_policy,
    load_workspace_feedback_policy,
    validate_candidate_question_contract,
    validate_question_coverage,
)


class ReviewError(InputError):
    """A candidate, evidence artifact, or domain-review decision is invalid."""


def _short(value: str) -> str:
    return value.split(":", 1)[-1][:16]


def _candidate_schema() -> Mapping[str, Any]:
    try:
        schema_path = config_path("schemas", "candidate.schema.json")
    except PackageResourceError as exc:
        raise ReviewError("candidate schema is unavailable") from exc
    value = read_json(schema_path)
    if not isinstance(value, Mapping):
        raise ReviewError("candidate schema is not an object")
    return value


def _load_private_json(root: Path, path: str | Path, field: str) -> dict[str, Any]:
    resolved = require_within(root, path, field)
    value = read_json(resolved)
    if not isinstance(value, Mapping):
        raise ReviewError(f"{field} must be an object")
    return dict(value)


def _plan_ground_truth_registry(paths: WorkspacePaths, plan: Mapping[str, Any]) -> GroundTruthRegistry:
    try:
        return load_plan_locked_ground_truth_registry(plan, workspace_root=paths.root)
    except GroundTruthRegistryError as exc:
        raise ReviewError("plan has no valid locked ground-truth registry") from exc


def _candidate_feedback_policy(
    paths: WorkspacePaths,
    *,
    candidate: Mapping[str, Any],
    work_item: Mapping[str, Any],
) -> FeedbackPolicy | None:
    """Resolve policy only from the snapshot already locked by this candidate."""

    provenance = candidate.get("provenance")
    leaf_id = work_item.get("leaf_id")
    if not isinstance(provenance, Mapping) or not isinstance(leaf_id, str) or not leaf_id:
        raise ReviewError("candidate feedback-policy binding is malformed")
    knowledge_digest = provenance.get("knowledge_snapshot_digest")
    if not isinstance(knowledge_digest, str):
        raise ReviewError("candidate feedback-policy knowledge lock is malformed")
    try:
        return load_workspace_feedback_policy(
            paths,
            leaf_id=leaf_id,
            expected_knowledge_snapshot_digest=knowledge_digest,
        )
    except FeedbackPolicyError as exc:
        raise ReviewError("candidate feedback-policy snapshot is invalid") from exc


def _expected_ground_truth_record(work_item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Use a preselected record when the plan carries a per-job projection."""

    value = work_item.get("ground_truth_record")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ReviewError("work item ground-truth record projection is malformed")
    return dict(value)


def _validate_candidate(
    paths: WorkspacePaths, candidate: Mapping[str, Any], run: Mapping[str, Any], plan: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    errors = sorted(Draft7Validator(_candidate_schema()).iter_errors(candidate), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ReviewError(f"candidate schema validation failed at {location}: {error.message}")
    candidate_digest = digest(dict(candidate))
    body = run["body"]
    provenance = candidate["provenance"]
    if provenance.get("plan_digest") != body.get("plan_digest"):
        raise ReviewError("candidate is bound to another plan")
    if provenance.get("config_digest") != body.get("config_digest"):
        raise ReviewError("candidate is bound to another compiled config")
    knowledge_digest = provenance.get("knowledge_snapshot_digest")
    allowed_knowledge = {
        value
        for leaf in plan["body"].get("eligible_leaves", [])
        if isinstance(leaf, Mapping)
        for value in leaf.get("locks", {}).get("knowledge_digests", [])
    }
    if knowledge_digest not in allowed_knowledge:
        raise ReviewError("candidate knowledge snapshot is not locked by this plan")
    viewer_request = candidate["viewer_session_request"]
    if viewer_request.get("viewer_profile_digest") != body.get("viewer_profile_digest"):
        raise ReviewError("candidate is bound to another viewer profile")
    task_id = candidate.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ReviewError("candidate task_id is missing")
    try:
        work_item = resolve_plan_work_item(
            plan,
            knowledge_digest=knowledge_digest,
            requested_operations=viewer_request.get("allowed_operations"),
            work_item_id=provenance.get("work_item_id"),
        )
    except InputError as exc:
        raise ReviewError(f"candidate does not match one approved plan work item: {exc}") from exc
    try:
        policy = load_question_text_policy()
        leaf_id = str(work_item["leaf_id"])
        fixture_only = body.get("fixture_only") is True
        registry = None if fixture_only else _plan_ground_truth_registry(paths, plan)
        feedback_policy = None if fixture_only else _candidate_feedback_policy(
            paths,
            candidate=candidate,
            work_item=work_item,
        )
        validate_candidate_question_contract(
            candidate,
            policy=policy,
            leaf_id=leaf_id,
            require_contract=not fixture_only,
            legacy_fixture_compatibility=fixture_only,
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=(registry.content_digest if registry is not None else None),
            expected_ground_truth_record=_expected_ground_truth_record(work_item),
            feedback_policy=feedback_policy,
        )
    except QuestionContractError as exc:
        raise ReviewError("candidate question contract validation failed") from exc
    return candidate_digest, task_id, work_item


def _validate_review_portfolio_coverage(
    *,
    paths: WorkspacePaths,
    candidate_root: Path,
    run: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> None:
    """Require a complete non-fixture portfolio before approving one task.

    Family quotas are portfolio constraints, so approving a first candidate
    before the remaining plan work items exist would make the 25/75 policy
    unenforceable.  Rejections and revisions remain available before the
    portfolio is complete; this gate applies only to an approval decision.
    """

    body = run.get("body")
    if isinstance(body, Mapping) and body.get("fixture_only") is True:
        return
    work_items = plan.get("body", {}).get("work_items") if isinstance(plan.get("body"), Mapping) else None
    if not isinstance(work_items, list) or not work_items:
        raise ReviewError("plan has no work items for question coverage validation")
    expected_ids = {
        item.get("work_item_id")
        for item in work_items
        if isinstance(item, Mapping) and isinstance(item.get("work_item_id"), str)
    }
    if len(expected_ids) != len(work_items):
        raise ReviewError("plan work items are malformed for question coverage validation")
    expected_leaf_ids = [item.get("leaf_id") for item in work_items if isinstance(item, Mapping)]
    if len(expected_leaf_ids) != len(work_items) or not all(isinstance(leaf_id, str) and leaf_id for leaf_id in expected_leaf_ids):
        raise ReviewError("plan work items lack leaf ids for question coverage validation")
    if candidate_root.is_symlink() or not candidate_root.is_dir():
        raise ReviewError("candidate root is unavailable for question coverage validation")
    candidate_paths = sorted(candidate_root.glob("*.json"), key=lambda item: item.name)
    if len(candidate_paths) != len(expected_ids):
        raise ReviewError("question coverage is pending until every plan work item has a candidate")
    policy = load_question_text_policy()
    registry = _plan_ground_truth_registry(paths, plan)
    candidates: list[dict[str, Any]] = []
    leaf_ids: list[str] = []
    feedback_policies: list[FeedbackPolicy | None] = []
    seen_work_items: set[str] = set()
    for path in candidate_paths:
        if path.is_symlink() or not path.is_file():
            raise ReviewError("candidate artifact is unsafe for question coverage validation")
        candidate = _load_private_json(candidate_root, path, "coverage candidate")
        provenance = candidate.get("provenance")
        request = candidate.get("viewer_session_request")
        if not isinstance(provenance, Mapping) or not isinstance(request, Mapping):
            raise ReviewError("coverage candidate provenance or viewer request is malformed")
        try:
            work_item = resolve_plan_work_item(
                plan,
                knowledge_digest=provenance.get("knowledge_snapshot_digest"),
                requested_operations=request.get("allowed_operations"),
                work_item_id=provenance.get("work_item_id"),
            )
        except InputError as exc:
            raise ReviewError("coverage candidate does not match an approved plan work item") from exc
        work_item_id = work_item.get("work_item_id")
        if not isinstance(work_item_id, str) or work_item_id in seen_work_items:
            raise ReviewError("question coverage candidates do not cover each plan work item once")
        seen_work_items.add(work_item_id)
        try:
            feedback_policy = _candidate_feedback_policy(
                paths,
                candidate=candidate,
                work_item=work_item,
            )
            validate_candidate_question_contract(
                candidate,
                policy=policy,
                leaf_id=str(work_item.get("leaf_id", "")),
                require_contract=True,
                ground_truth_registry=registry,
                expected_ground_truth_registry_digest=registry.content_digest,
                expected_ground_truth_record=_expected_ground_truth_record(work_item),
                feedback_policy=feedback_policy,
            )
        except (QuestionContractError, ReviewError) as exc:
            raise ReviewError("coverage candidate question contract validation failed") from exc
        candidates.append(candidate)
        feedback_policies.append(feedback_policy)
        leaf_id = work_item.get("leaf_id")
        if not isinstance(leaf_id, str) or not leaf_id:
            raise ReviewError("coverage candidate work item lacks a leaf id")
        leaf_ids.append(leaf_id)
    if seen_work_items != expected_ids:
        raise ReviewError("question coverage candidates do not cover the approved plan")
    try:
        validate_question_coverage(
            candidates,
            policy=policy,
            leaf_ids=leaf_ids,
            expected_leaf_ids=expected_leaf_ids,
            feedback_policies=feedback_policies,
        )
    except QuestionContractError as exc:
        raise ReviewError("question coverage validation failed") from exc


def _review_allowed(run: Mapping[str, Any], candidate_digest: str, work_item_id: str, decision: str) -> None:
    body = run["body"]
    reviews = body.get("candidate_reviews", {})
    if isinstance(reviews, Mapping) and candidate_digest in reviews:
        raise InvalidStateError("candidate already has an immutable review decision")
    if decision == "revise":
        counts = body.get("revision_counts", {})
        if isinstance(counts, Mapping) and int(counts.get(work_item_id, 0)) >= 1:
            raise InvalidStateError("candidate already used its one allowed revision")


def _atomic_create_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create one immutable receipt without replacing any existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(dict(value)) + b"\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ReviewError(f"review receipt path already exists: {path.name}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _decision_is_referenced(paths: WorkspacePaths, run_id: str, decision_digest: str) -> bool:
    """Return true unless state proves that this decision was not committed."""
    try:
        current = load_run(paths, run_id)
    except Exception:
        return True
    reviews = current.get("body", {}).get("candidate_reviews")
    if not isinstance(reviews, Mapping):
        return False
    return any(
        isinstance(value, Mapping) and value.get("decision_digest") == decision_digest
        for value in reviews.values()
    )


def decide_review(
    paths: WorkspacePaths,
    *,
    run_id: str,
    candidate_path: str | Path,
    evidence_path: str | Path,
    reviewer: str,
    decision: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Create one immutable human review decision for one exact candidate."""
    if decision not in {"approved", "revise", "rejected", "quarantined"}:
        raise ReviewError("review decision must be approved, revise, rejected, or quarantined")
    if decision in {"revise", "rejected"} and not rationale.strip():
        raise ReviewError(f"review decision {decision} requires a non-empty rationale")
    paths.ensure()
    with run_state_lock(paths, run_id):
        run = load_run(paths, run_id)
        plan, _, viewer = validate_run_locks(paths, run)
        root = run_directory(paths, run_id) / "private"
        candidate = _load_private_json(root / "candidates", candidate_path, "candidate")
        candidate_digest, task_id, work_item = _validate_candidate(paths, candidate, run, plan)
        if decision == "approved":
            _validate_review_portfolio_coverage(
                paths=paths,
                candidate_root=root / "candidates",
                run=run,
                plan=plan,
            )
        evidence = _load_private_json(root / "evidence", evidence_path, "evidence")
        try:
            evidence_validation = validate_persisted_evidence(
                paths,
                run=run,
                viewer=viewer,
                candidate_path=candidate_path,
                candidate=candidate,
                evidence_path=evidence_path,
                evidence=evidence,
                work_item=work_item,
            )
        except EvidenceValidationError as exc:
            raise ReviewError(str(exc)) from exc
        evidence_digest = evidence_validation.evidence_digest
        execution = evidence_validation.execution
        execution_entry = execution["entry"]
        if execution_entry.get("leaf_id") != work_item["leaf_id"]:
            raise ReviewError("persisted execution leaf does not match the candidate work item")
        if execution_entry.get("work_item_id") != work_item["work_item_id"]:
            raise ReviewError("persisted execution work item does not match the candidate work item")
        if execution_entry.get("dependency_digest") != work_item["dependency_digest"]:
            raise ReviewError("persisted execution dependency does not match the candidate work item")
        _review_allowed(run, candidate_digest, work_item["work_item_id"], decision)
        execution_bindings = {
            "execution_manifest_digest": execution_entry["execution_manifest_digest"],
            "worker_result_digest": execution_entry["worker_result_digest"],
            "author_output_digest": candidate_digest,
            "evidence_output_digest": evidence_digest,
            "verifier_output_digest": execution_entry["verification_digest"],
            "stage_digests_digest": digest(execution_entry["stage_digests"]),
        }
        lock_digests = plan["body"]["lock_digests"]
        bound = {
            "candidate_digest": candidate_digest,
            "evidence_digest": evidence_digest,
            "plan_digest": run["body"]["plan_digest"],
            "viewer_profile_digest": run["body"]["viewer_profile_digest"],
            "work_item_dependency_digest": work_item["dependency_digest"],
            **execution_bindings,
            **dict(lock_digests),
        }
        decision_id = f"domain-review-{secrets.token_hex(16)}"
        receipt = make_decision(
            kind="domain_review_decision",
            identifier=decision_id,
            decision=decision,
            actor_id=reviewer,
            actor_role="domain_expert",
            bound_digests=bound,
            rationale=rationale,
            extra={
                "decision_id": decision_id,
                "candidate_digest": candidate_digest,
                "evidence_digest": evidence_digest,
                "candidate_path": execution["candidate_path"],
                "evidence_path": execution["evidence_path"],
                "config_digest": run["body"]["config_digest"],
                "viewer_profile_digest": run["body"]["viewer_profile_digest"],
                "leaf_id": work_item["leaf_id"],
                "work_item_id": work_item["work_item_id"],
                "knowledge_snapshot_digest": candidate["provenance"]["knowledge_snapshot_digest"],
                "requested_operations": candidate["viewer_session_request"]["allowed_operations"],
                "work_item_dependency_digest": work_item["dependency_digest"],
                "task_id": task_id,
                **execution_bindings,
            },
        )
        receipt_path = paths.review_receipts / (
            f"{run_id}.{_short(candidate_digest)}.{decision}.{decision_id}.decision.json"
        )
        audit_path = root / "audit" / "reviews" / receipt_path.name
        revision_receipt: dict[str, Any] | None = None
        revision_receipt_path: Path | None = None
        if decision == "revise":
            revision_receipt = make_revision_receipt(
                run_id=run_id,
                work_item_id=work_item["work_item_id"],
                candidate_digest=candidate_digest,
                verifier_output_digest=execution_entry["verification_digest"],
                reviewer=reviewer,
                rationale=rationale,
                review_decision_digest=receipt["content_digest"],
            )
            revision_receipt_path = root / "revision-receipts" / f"{decision_id}.revision.json"
        created_paths: list[Path] = []
        try:
            _atomic_create_json(receipt_path, receipt)
            created_paths.append(receipt_path)
            _atomic_create_json(audit_path, receipt)
            created_paths.append(audit_path)
            if revision_receipt is not None and revision_receipt_path is not None:
                _atomic_create_json(revision_receipt_path, revision_receipt)
                created_paths.append(revision_receipt_path)
            record_review_decision_locked(
                paths,
                run_id=run_id,
                candidate_digest=candidate_digest,
                candidate_key=work_item["work_item_id"],
                decision=decision,
                decision_digest=receipt["content_digest"],
                receipt=receipt,
                revision_receipt=revision_receipt,
                revision_receipt_path=revision_receipt_path,
            )
        except Exception:
            if not _decision_is_referenced(paths, run_id, receipt["content_digest"]):
                for created in reversed(created_paths):
                    try:
                        created.unlink(missing_ok=True)
                    except OSError:
                        pass
            raise
        append_event(
            paths,
            "domain_review_decided",
            {
                "run_id": run_id,
                "candidate_digest": candidate_digest,
                "evidence_digest": evidence_digest,
                "work_item_id": work_item["work_item_id"],
                "decision": decision,
                "decision_id": decision_id,
            },
        )
        result = {
            "review_path": str(receipt_path),
            "review_digest": receipt["content_digest"],
            "decision_id": decision_id,
            "decision": decision,
            "candidate_digest": candidate_digest,
            "work_item_id": work_item["work_item_id"],
        }
        if revision_receipt is not None and revision_receipt_path is not None:
            result.update(
                {
                    "revision_receipt_path": str(revision_receipt_path),
                    "revision_receipt_digest": revision_receipt["content_digest"],
                }
            )
        return result


def load_review_decision(paths: WorkspacePaths, review_path: str | Path) -> dict[str, Any]:
    resolved = require_within(paths.review_receipts, review_path, "review decision")
    value = read_json(resolved)
    if not isinstance(value, Mapping):
        raise ReviewError("review decision is not an object")
    return verify_envelope(value, kind="domain_review_decision")
