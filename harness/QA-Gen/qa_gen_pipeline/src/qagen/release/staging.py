"""Separate solver, scorer, and audit staging for approved run candidates."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable, Mapping

from ..contracts import digest, digest_bytes, make_envelope, require_digest, verify_envelope
from ..evidence_validation import EvidenceValidationError, validate_persisted_evidence
from ..errors import GatePendingError, InputError
from ..paths import WorkspacePaths, require_within
from ..run import (
    load_persisted_execution,
    load_run,
    resolve_plan_work_item,
    run_directory,
    run_state_lock,
    validate_run_locks,
)
from ..storage.atomic import atomic_write_json, read_json
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
from .leakage_check import assert_solver_bundle_safe


class ReleaseStageError(InputError):
    """A candidate cannot be separated into solver/scorer/audit staging."""


def _candidate_feedback_policy(
    paths: WorkspacePaths,
    *,
    candidate: Mapping[str, Any],
    work_item: Mapping[str, Any],
) -> FeedbackPolicy | None:
    """Use only the exact snapshot digest carried by the approved candidate."""

    provenance = candidate.get("provenance")
    leaf_id = work_item.get("leaf_id")
    if not isinstance(provenance, Mapping) or not isinstance(leaf_id, str) or not leaf_id:
        raise ReleaseStageError("candidate feedback-policy binding is malformed")
    knowledge_digest = provenance.get("knowledge_snapshot_digest")
    if not isinstance(knowledge_digest, str):
        raise ReleaseStageError("candidate feedback-policy knowledge lock is malformed")
    try:
        return load_workspace_feedback_policy(
            paths,
            leaf_id=leaf_id,
            expected_knowledge_snapshot_digest=knowledge_digest,
        )
    except FeedbackPolicyError as exc:
        raise ReleaseStageError("candidate feedback-policy snapshot is invalid") from exc


def _expected_ground_truth_record(work_item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return the exact job record lock when this plan is compiled-selection aware."""

    value = work_item.get("ground_truth_record")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ReleaseStageError("work item ground-truth record projection is malformed")
    return dict(value)


def _short(value: str) -> str:
    return value.split(":", 1)[-1][:16]


def _read_regular_json(path: Path, *, root: Path, label: str) -> tuple[Any, dict[str, Any]]:
    """Read one regular file by descriptor, without following a replaced symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ReleaseStageError(f"cannot open {label} artifact safely: {exc}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseStageError(f"{label} artifact is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ReleaseStageError(f"{label} artifact changed while it was being read")
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ReleaseStageError(f"{label} artifact changed while it was being read: {exc}") from exc
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
        raise ReleaseStageError(f"{label} artifact path changed while it was being read")
    content = b"".join(chunks)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseStageError(f"cannot parse {label} artifact as JSON: {exc}") from exc
    try:
        relative_path = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ReleaseStageError(f"{label} artifact is outside release staging") from exc
    return value, {
        "relative_path": relative_path,
        "canonical_sha256": digest(value),
        "file_sha256": digest_bytes(content),
        "size_bytes": len(content),
    }


def _require_artifact_descriptor(
    value: Any,
    *,
    category: str,
    filename: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "relative_path",
        "canonical_sha256",
        "file_sha256",
        "size_bytes",
    }:
        raise ReleaseStageError(f"staged {category} artifact descriptor is malformed")
    expected_path = f"{category}/{filename}"
    if value.get("relative_path") != expected_path:
        raise ReleaseStageError(f"staged {category} artifact descriptor path changed")
    require_digest(value.get("canonical_sha256"), f"staged {category} canonical sha256")
    require_digest(value.get("file_sha256"), f"staged {category} file sha256")
    size = value.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ReleaseStageError(f"staged {category} artifact size is invalid")
    return dict(value)


def _assert_exact_exportable_files(staging_root: Path, expected: set[str]) -> None:
    actual: set[str] = set()
    for category in ("solver", "scorer"):
        root = staging_root / category
        if root.is_symlink() or not root.is_dir():
            raise ReleaseStageError(f"release staging {category} directory is missing or unsafe")
        for path in root.rglob("*"):
            relative = path.relative_to(staging_root).as_posix()
            if path.is_symlink():
                raise ReleaseStageError(f"release staging contains a symlink artifact: {relative}")
            if not path.is_file():
                raise ReleaseStageError(f"release staging contains an unexpected directory: {relative}")
            actual.add(relative)
    if actual != expected:
        missing = sorted(expected - actual)
        added = sorted(actual - expected)
        detail = f"missing={missing}, added={added}"
        raise ReleaseStageError(f"release staging exportable file set changed: {detail}")


def _load_candidate(root: Path, path: str | Path) -> dict[str, Any]:
    resolved = require_within(root, path, "release candidate")
    value = read_json(resolved)
    if not isinstance(value, Mapping):
        raise ReleaseStageError("release candidate is not an object")
    required = {
        "artifact_kind",
        "task_id",
        "prompt",
        "anonymous_scene_ref",
        "viewer_session_request",
        "reference_answer",
        "reasoning",
        "rubric",
        "provenance",
    }
    missing = sorted(required - set(value))
    if missing or value.get("artifact_kind") != "author_private_candidate":
        raise ReleaseStageError("release candidate is not a valid private author candidate")
    return dict(value)


def _candidate_work_item(candidate: Mapping[str, Any], *, run: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    provenance = candidate.get("provenance")
    request = candidate.get("viewer_session_request")
    if not isinstance(provenance, Mapping) or not isinstance(request, Mapping):
        raise ReleaseStageError("candidate provenance or viewer request is malformed")
    if provenance.get("plan_digest") != run["body"].get("plan_digest"):
        raise ReleaseStageError("candidate is bound to another plan")
    if provenance.get("config_digest") != run["body"].get("config_digest"):
        raise ReleaseStageError("candidate is bound to another compiled config")
    if request.get("viewer_profile_digest") != run["body"].get("viewer_profile_digest"):
        raise ReleaseStageError("candidate is bound to another viewer profile")
    try:
        return resolve_plan_work_item(
            plan,
            knowledge_digest=provenance.get("knowledge_snapshot_digest"),
            requested_operations=request.get("allowed_operations"),
            work_item_id=provenance.get("work_item_id"),
        )
    except InputError as exc:
        raise ReleaseStageError(f"candidate does not match an approved plan work item: {exc}") from exc


def _approved_review(
    paths: WorkspacePaths,
    *,
    run: Mapping[str, Any],
    candidate_digest: str,
    work_item: Mapping[str, Any],
    execution: Mapping[str, Any],
) -> dict[str, Any]:
    review_map = run["body"].get("candidate_reviews")
    if not isinstance(review_map, Mapping):
        raise GatePendingError("run has no candidate review state")
    state = review_map.get(candidate_digest)
    if not isinstance(state, Mapping) or state.get("decision") != "approved":
        raise GatePendingError("candidate has not received an approved domain review")
    if state.get("work_item_id") != work_item.get("work_item_id"):
        raise GatePendingError("approved review state is bound to another work item")
    execution_bindings = _execution_bindings(execution)
    if any(state.get(key) != value for key, value in execution_bindings.items()):
        raise GatePendingError("approved review state is not bound to the persisted run execution")
    if state.get("evidence_digest") != execution_bindings["evidence_output_digest"]:
        raise GatePendingError("approved review state is not bound to the persisted evidence")
    expected_digest = state.get("decision_digest")
    for path in paths.review_receipts.glob("*.decision.json"):
        try:
            value = read_json(path)
            if not isinstance(value, Mapping):
                continue
            receipt = verify_envelope(value, kind="domain_review_decision")
        except (InputError, OSError):
            continue
        body = receipt["body"]
        if receipt["content_digest"] != expected_digest or body.get("candidate_digest") != candidate_digest:
            continue
        if body.get("decision") != "approved":
            continue
        bound = body.get("bound_digests")
        if not isinstance(bound, Mapping):
            continue
        run_locks = run["body"].get("lock_digests", {})
        if bound.get("plan_digest") != run["body"].get("plan_digest"):
            continue
        if bound.get("viewer_profile_digest") != run["body"].get("viewer_profile_digest"):
            continue
        if bound.get("work_item_dependency_digest") != work_item.get("dependency_digest"):
            continue
        if body.get("leaf_id") != work_item.get("leaf_id"):
            continue
        if body.get("work_item_id") != work_item.get("work_item_id"):
            continue
        if body.get("evidence_digest") != execution_bindings["evidence_output_digest"]:
            continue
        if any(bound.get(key) != value for key, value in execution_bindings.items()):
            continue
        if any(body.get(key) != value for key, value in execution_bindings.items()):
            continue
        if any(bound.get(key) != value for key, value in run_locks.items()):
            continue
        return receipt
    raise GatePendingError("approved review receipt is missing or no longer bound to run locks")


def _execution_bindings(execution: Mapping[str, Any]) -> dict[str, str]:
    entry = execution.get("entry")
    if not isinstance(entry, Mapping):
        raise ReleaseStageError("persisted execution entry is malformed")
    bindings = {
        "execution_manifest_digest": entry.get("execution_manifest_digest"),
        "worker_result_digest": entry.get("worker_result_digest"),
        "author_output_digest": entry.get("candidate_digest"),
        "evidence_output_digest": entry.get("evidence_digest"),
        "verifier_output_digest": entry.get("verification_digest"),
        "stage_digests_digest": digest(entry.get("stage_digests")),
    }
    for key, value in bindings.items():
        require_digest(value, f"persisted execution {key}")
    return bindings


def _load_execution_for_candidate(
    paths: WorkspacePaths,
    *,
    run: Mapping[str, Any],
    candidate_path: str | Path | None,
    candidate_digest: str,
) -> dict[str, Any]:
    if candidate_path is None:
        executions = run.get("body", {}).get("executions")
        if not isinstance(executions, Mapping):
            raise ReleaseStageError("run has no persisted execution state")
        matches = [
            entry
            for entry in executions.values()
            if isinstance(entry, Mapping) and entry.get("candidate_digest") == candidate_digest
        ]
        if len(matches) != 1:
            raise ReleaseStageError("candidate is not bound to exactly one persisted run execution")
        candidate_path = matches[0].get("candidate_path")
    try:
        return load_persisted_execution(
            paths,
            run=run,
            candidate_path=candidate_path,
            candidate_digest=candidate_digest,
        )
    except InputError as exc:
        raise ReleaseStageError(f"release requires a persisted run execution: {exc}") from exc


def _frozen_rubric(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the threshold data frozen in the compiled configuration."""
    config_body = config.get("body")
    if not isinstance(config_body, Mapping):
        raise ReleaseStageError("compiled config body is malformed")
    rubric = config_body.get("rubric")
    locks = config_body.get("lock_digests")
    if not isinstance(rubric, Mapping) or not isinstance(locks, Mapping):
        raise ReleaseStageError("compiled config lacks frozen rubric data")
    lock_digest = rubric.get("lock_digest")
    require_digest(lock_digest, "compiled rubric lock digest")
    if lock_digest != locks.get("rubric_lock_digest"):
        raise ReleaseStageError("compiled rubric data differs from its control-plane lock")
    document_digest = rubric.get("document_digest")
    require_digest(document_digest, "compiled rubric document digest")
    thresholds = rubric.get("release_thresholds")
    fixture_policy = rubric.get("fixture_policy")
    if not isinstance(thresholds, Mapping) or not isinstance(fixture_policy, Mapping):
        raise ReleaseStageError("compiled rubric thresholds or policy are malformed")
    expected_criteria = {"authenticity", "correctness", "three_d_relevance"}
    if set(thresholds) != expected_criteria:
        raise ReleaseStageError("compiled rubric has an unexpected release-threshold set")
    normalized: dict[str, dict[str, int]] = {}
    for criterion in sorted(expected_criteria):
        threshold = thresholds.get(criterion)
        if not isinstance(threshold, Mapping) or set(threshold) != {"required", "maximum"}:
            raise ReleaseStageError(f"compiled rubric threshold is malformed: {criterion}")
        required = threshold.get("required")
        maximum = threshold.get("maximum")
        if (
            not isinstance(required, int)
            or isinstance(required, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or required < 0
            or maximum < required
        ):
            raise ReleaseStageError(f"compiled rubric threshold is invalid: {criterion}")
        normalized[criterion] = {"required": required, "maximum": maximum}
    return {
        "lock_digest": lock_digest,
        "document_digest": document_digest,
        "release_thresholds": normalized,
        "fixture_policy": dict(fixture_policy),
    }


def _rubric_dry_run(
    *,
    verification: Mapping[str, Any],
    verification_digest: str,
    candidate_digest: str,
    evidence_digest: str,
    rubric: Mapping[str, Any],
) -> dict[str, Any]:
    """Use only the verifier output already bound by the execution manifest."""
    require_digest(verification_digest, "persisted verifier output digest")
    if digest(dict(verification)) != verification_digest:
        raise ReleaseStageError("persisted verifier output digest changed")
    if verification.get("candidate_digest") != candidate_digest:
        raise ReleaseStageError("persisted verifier output is bound to another candidate")
    if verification.get("evidence_digest") != evidence_digest:
        raise ReleaseStageError("persisted verifier output is bound to other evidence")
    if verification.get("outcome") != "pass":
        raise GatePendingError("production release requires a passing persisted verifier result")
    if verification.get("rubric_lock_digest") != rubric.get("lock_digest"):
        raise ReleaseStageError("persisted verifier output is bound to another rubric lock")
    scores = verification.get("rubric_scores")
    thresholds = rubric.get("release_thresholds")
    if not isinstance(scores, Mapping) or not isinstance(thresholds, Mapping) or set(scores) != set(thresholds):
        raise ReleaseStageError("persisted verifier scores do not match the frozen threshold set")
    normalized_scores: dict[str, dict[str, int]] = {}
    for criterion in sorted(thresholds):
        score = scores.get(criterion)
        threshold = thresholds[criterion]
        if not isinstance(score, Mapping) or set(score) != {"score", "maximum"}:
            raise ReleaseStageError(f"persisted verifier score is malformed: {criterion}")
        received = score.get("score")
        maximum = score.get("maximum")
        if (
            not isinstance(received, int)
            or isinstance(received, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or maximum != threshold["maximum"]
            or received < 0
            or received > maximum
        ):
            raise ReleaseStageError(f"persisted verifier score is invalid: {criterion}")
        if received < threshold["required"]:
            raise GatePendingError(f"persisted verifier did not meet release threshold: {criterion}")
        normalized_scores[criterion] = {"score": received, "maximum": maximum}
    return {"digest": verification_digest, "scores": normalized_scores}


def _solver_export(candidate: Mapping[str, Any], *, work_item: Mapping[str, Any]) -> dict[str, Any]:
    request = candidate.get("viewer_session_request")
    if not isinstance(request, Mapping):
        raise ReleaseStageError("candidate viewer session request is missing")
    approved_operations = work_item.get("approved_operations")
    requested_operations = request.get("allowed_operations")
    if (
        not isinstance(approved_operations, list)
        or not isinstance(requested_operations, list)
        or not set(requested_operations).issubset(set(approved_operations))
    ):
        raise ReleaseStageError("solver export would expose operations outside the approved plan work item")
    return {
        "artifact_kind": "solver_safe_export",
        "task_id": candidate["task_id"],
        "prompt": candidate["prompt"],
        "anonymous_scene_ref": candidate["anonymous_scene_ref"],
        "viewer_session_request": {
            "request_id": request.get("request_id"),
            "viewer_profile_digest": request.get("viewer_profile_digest"),
            "allowed_operations": request.get("allowed_operations"),
        },
    }


def _validate_candidate_question_contract_for_staging(
    candidate: Mapping[str, Any],
    *,
    work_item: Mapping[str, Any],
    paths: WorkspacePaths | None = None,
    fixture_only: bool = False,
    ground_truth_registry: GroundTruthRegistry | None = None,
    expected_ground_truth_registry_digest: str | None = None,
    feedback_policy: FeedbackPolicy | None = None,
) -> None:
    """Repeat the solver-text and semantic gate after durable execution reload."""

    leaf_id = work_item.get("leaf_id")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise ReleaseStageError("staged candidate work item lacks a leaf id")
    if not fixture_only and feedback_policy is None and paths is not None:
        feedback_policy = _candidate_feedback_policy(paths, candidate=candidate, work_item=work_item)
    try:
        policy = load_question_text_policy()
        validate_candidate_question_contract(
            candidate,
            policy=policy,
            leaf_id=leaf_id,
            require_contract=not fixture_only,
            legacy_fixture_compatibility=fixture_only,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=expected_ground_truth_registry_digest,
            expected_ground_truth_record=_expected_ground_truth_record(work_item),
            feedback_policy=feedback_policy,
        )
    except QuestionContractError as exc:
        raise ReleaseStageError("candidate question contract validation failed during release staging") from exc


def _preflight_question_contracts_and_coverage(
    *,
    paths: WorkspacePaths,
    candidate_root: Path,
    candidate_paths: Iterable[str | Path],
    run: Mapping[str, Any],
    plan: Mapping[str, Any],
    fixture_only: bool,
) -> None:
    """Reject an invalid portfolio before creating any staging artifacts."""

    if fixture_only:
        return
    work_items = plan.get("body", {}).get("work_items") if isinstance(plan.get("body"), Mapping) else None
    if not isinstance(work_items, list) or not work_items:
        raise ReleaseStageError("plan has no work items for question coverage validation")
    expected_work_item_ids: set[str] = set()
    expected_leaf_ids: list[str] = []
    for item in work_items:
        if not isinstance(item, Mapping):
            raise ReleaseStageError("plan work items are malformed for question coverage validation")
        work_item_id = item.get("work_item_id")
        leaf_id = item.get("leaf_id")
        if (
            not isinstance(work_item_id, str)
            or not work_item_id
            or work_item_id in expected_work_item_ids
            or not isinstance(leaf_id, str)
            or not leaf_id
        ):
            raise ReleaseStageError("plan work items are malformed for question coverage validation")
        expected_work_item_ids.add(work_item_id)
        expected_leaf_ids.append(leaf_id)
    policy = load_question_text_policy()
    try:
        registry = load_plan_locked_ground_truth_registry(plan, workspace_root=paths.root)
    except GroundTruthRegistryError as exc:
        raise ReleaseStageError("plan has no valid locked ground-truth registry") from exc
    candidates: list[dict[str, Any]] = []
    leaf_ids: list[str] = []
    feedback_policies: list[FeedbackPolicy | None] = []
    seen_digests: set[str] = set()
    seen_work_item_ids: set[str] = set()
    for candidate_path in candidate_paths:
        candidate = _load_candidate(candidate_root, candidate_path)
        candidate_digest = digest(candidate)
        if candidate_digest in seen_digests:
            raise ReleaseStageError("the same candidate cannot be staged twice")
        seen_digests.add(candidate_digest)
        work_item = _candidate_work_item(candidate, run=run, plan=plan)
        feedback_policy = _candidate_feedback_policy(paths, candidate=candidate, work_item=work_item)
        _validate_candidate_question_contract_for_staging(
            candidate,
            work_item=work_item,
            paths=paths,
            fixture_only=False,
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=registry.content_digest,
            feedback_policy=feedback_policy,
        )
        work_item_id = work_item.get("work_item_id")
        leaf_id = work_item.get("leaf_id")
        if (
            not isinstance(work_item_id, str)
            or work_item_id in seen_work_item_ids
            or not isinstance(leaf_id, str)
            or not leaf_id
        ):
            raise ReleaseStageError("release candidates do not cover each plan work item once")
        seen_work_item_ids.add(work_item_id)
        leaf_ids.append(leaf_id)
        candidates.append(candidate)
        feedback_policies.append(feedback_policy)
    if seen_work_item_ids != expected_work_item_ids:
        raise ReleaseStageError("release candidates do not cover the approved plan")
    try:
        validate_question_coverage(
            candidates,
            policy=policy,
            leaf_ids=leaf_ids,
            expected_leaf_ids=expected_leaf_ids,
            feedback_policies=feedback_policies,
        )
    except QuestionContractError as exc:
        raise ReleaseStageError("candidate question coverage validation failed during release staging") from exc


def stage_release(
    paths: WorkspacePaths,
    *,
    run_id: str,
    candidate_paths: Iterable[str | Path],
) -> dict[str, Any]:
    """Stage approved candidates without exporting private material to solver."""
    paths.ensure()
    run = load_run(paths, run_id)
    plan, config, viewer = validate_run_locks(paths, run)
    frozen_rubric = _frozen_rubric(config)
    fixture_only = bool(run["body"].get("fixture_only"))
    candidates = list(candidate_paths)
    registry: GroundTruthRegistry | None = None
    if not fixture_only:
        try:
            registry = load_plan_locked_ground_truth_registry(plan, workspace_root=paths.root)
        except GroundTruthRegistryError as exc:
            raise ReleaseStageError("plan has no valid locked ground-truth registry") from exc
    if not candidates:
        raise ReleaseStageError("at least one approved candidate is required for release staging")
    directory = run_directory(paths, run_id)
    candidate_root = directory / "private" / "candidates"
    _preflight_question_contracts_and_coverage(
        paths=paths,
        candidate_root=candidate_root,
        candidate_paths=candidates,
        run=run,
        plan=plan,
        fixture_only=fixture_only,
    )
    staging_root = directory / "staging"
    solver_dir = staging_root / "solver"
    scorer_dir = staging_root / "scorer"
    audit_dir = staging_root / "audit"
    solver_dir.mkdir(parents=True, exist_ok=True)
    scorer_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, Any]] = []
    seen_candidate_digests: set[str] = set()
    for candidate_path in candidates:
        candidate = _load_candidate(candidate_root, candidate_path)
        candidate_digest = digest(candidate)
        if candidate_digest in seen_candidate_digests:
            raise ReleaseStageError("the same candidate cannot be staged twice")
        seen_candidate_digests.add(candidate_digest)
        execution = _load_execution_for_candidate(
            paths,
            run=run,
            candidate_path=candidate_path,
            candidate_digest=candidate_digest,
        )
        candidate = execution["candidate"]
        evidence = execution["evidence"]
        execution_entry = execution["entry"]
        execution_bindings = _execution_bindings(execution)
        work_item = _candidate_work_item(candidate, run=run, plan=plan)
        if execution_entry.get("leaf_id") != work_item.get("leaf_id"):
            raise ReleaseStageError("persisted execution leaf does not match the candidate work item")
        if execution_entry.get("work_item_id") != work_item.get("work_item_id"):
            raise ReleaseStageError("persisted execution work item does not match the candidate work item")
        if execution_entry.get("dependency_digest") != work_item.get("dependency_digest"):
            raise ReleaseStageError("persisted execution dependency does not match the candidate work item")
        receipt = _approved_review(
            paths,
            run=run,
            candidate_digest=candidate_digest,
            work_item=work_item,
            execution=execution,
        )
        try:
            evidence_validation = validate_persisted_evidence(
                paths,
                run=run,
                viewer=viewer,
                candidate_path=candidate_path,
                candidate=candidate,
                evidence_path=execution["evidence_path"],
                evidence=evidence,
                work_item=work_item,
            )
        except EvidenceValidationError as exc:
            raise ReleaseStageError(str(exc)) from exc
        execution = evidence_validation.execution
        candidate = execution["candidate"]
        evidence = execution["evidence"]
        execution_entry = execution["entry"]
        candidate_digest = evidence_validation.candidate_digest
        evidence_digest = evidence_validation.evidence_digest
        execution_bindings = _execution_bindings(execution)
        controlled_request = evidence_validation.controlled_request
        _validate_candidate_question_contract_for_staging(
            candidate,
            work_item=work_item,
            paths=paths,
            fixture_only=fixture_only,
            ground_truth_registry=registry,
            expected_ground_truth_registry_digest=(registry.content_digest if registry is not None else None),
        )
        dry_run: dict[str, Any] | None = None
        if not fixture_only:
            dry_run = _rubric_dry_run(
                verification=execution["verification"],
                verification_digest=execution_entry["verification_digest"],
                candidate_digest=candidate_digest,
                evidence_digest=evidence_digest,
                rubric=frozen_rubric,
            )
        export = _solver_export(candidate, work_item=work_item)
        suffix = _short(candidate_digest)
        solver_path = solver_dir / f"task-{suffix}.json"
        scorer_path = scorer_dir / f"scorer-{suffix}.json"
        audit_path = audit_dir / f"audit-{suffix}.json"
        atomic_write_json(solver_path, export, private=True)
        atomic_write_json(
            scorer_path,
            {
                "artifact_kind": "scorer_private_input",
                "candidate_digest": candidate_digest,
                "reference_answer": candidate["reference_answer"],
                "rubric": candidate["rubric"],
                "review_digest": receipt["content_digest"],
                "viewer_evidence_digest": evidence_digest,
                "rubric_lock_digest": frozen_rubric["lock_digest"],
                "release_thresholds": frozen_rubric["release_thresholds"],
                "work_item_id": work_item["work_item_id"],
                "work_item_dependency_digest": work_item["dependency_digest"],
                **execution_bindings,
            },
            private=True,
        )
        atomic_write_json(
            audit_path,
            {
                "artifact_kind": "release_audit_record",
                "candidate_digest": candidate_digest,
                "author_truth_receipt": {
                    "reference_answer_digest": digest(candidate["reference_answer"]),
                    "reasoning_digest": digest({"reasoning": candidate["reasoning"]}),
                },
                "review_digest": receipt["content_digest"],
                "execution": execution_bindings,
                "viewer_evidence": {
                    "digest": evidence_digest,
                    "trace_required": not fixture_only,
                    "session_request_id": controlled_request["request_id"],
                },
                "rubric_dry_run": (
                    {"digest": dry_run["digest"], "scores": dry_run["scores"]} if dry_run is not None else None
                ),
                "lock_digests": run["body"]["lock_digests"],
                "work_item_id": work_item["work_item_id"],
                "work_item_dependency_digest": work_item["dependency_digest"],
            },
            private=True,
        )
        _, solver_artifact = _read_regular_json(solver_path, root=staging_root, label="solver")
        _, scorer_artifact = _read_regular_json(scorer_path, root=staging_root, label="scorer")
        staged.append(
            {
                "candidate_digest": candidate_digest,
                "evidence_digest": evidence_digest,
                "review_digest": receipt["content_digest"],
                "work_item_id": work_item["work_item_id"],
                **execution_bindings,
                "rubric_dry_run_digest": dry_run["digest"] if dry_run is not None else None,
                "solver_path": str(solver_path),
                "scorer_path": str(scorer_path),
                "audit_path": str(audit_path),
                "solver_artifact": solver_artifact,
                "scorer_artifact": scorer_artifact,
            }
        )
    # This inspects only the solver staging directory; audit and scorer files
    # never cross this boundary and therefore cannot become accidental exports.
    assert_solver_bundle_safe(solver_dir)
    status = "fixture_staged_no_production_export" if fixture_only else "staged_not_exported"
    staging_manifest = make_envelope(
        kind="release_staging_manifest",
        identifier=f"release-staging-{run_id}",
        status=status,
        body={
            "run_id": run_id,
            "plan_digest": run["body"]["plan_digest"],
            "config_digest": run["body"]["config_digest"],
            "viewer_profile_digest": run["body"]["viewer_profile_digest"],
            "lock_digests": dict(run["body"]["lock_digests"]),
            "fixture_only": fixture_only,
            "staged": [
                {
                    "candidate_digest": item["candidate_digest"],
                    "evidence_digest": item["evidence_digest"],
                    "review_digest": item["review_digest"],
                    "work_item_id": item["work_item_id"],
                    "execution_manifest_digest": item["execution_manifest_digest"],
                    "worker_result_digest": item["worker_result_digest"],
                    "author_output_digest": item["author_output_digest"],
                    "evidence_output_digest": item["evidence_output_digest"],
                    "verifier_output_digest": item["verifier_output_digest"],
                    "stage_digests_digest": item["stage_digests_digest"],
                    "rubric_dry_run_digest": item["rubric_dry_run_digest"],
                    "solver_file": Path(item["solver_path"]).name,
                    "scorer_file": Path(item["scorer_path"]).name,
                    "audit_file": Path(item["audit_path"]).name,
                    "solver_artifact": item["solver_artifact"],
                    "scorer_artifact": item["scorer_artifact"],
                }
                for item in staged
            ],
        },
        producer="qagen/0.1.0:release_staging",
    )
    staging_manifest_path = staging_root / "manifest.json"
    atomic_write_json(staging_manifest_path, staging_manifest, private=True)
    append_event(
        paths,
        "release_staged",
        {
            "run_id": run_id,
            "candidate_count": len(staged),
            "work_item_ids": [item["work_item_id"] for item in staged],
            "fixture_only": fixture_only,
            "status": status,
        },
    )
    return {
        "run_id": run_id,
        "status": status,
        "solver_staging": str(solver_dir),
        "scorer_staging": str(scorer_dir),
        "audit_staging": str(audit_dir),
        "staging_manifest_path": str(staging_manifest_path),
        "staging_manifest_digest": staging_manifest["content_digest"],
        "staged": staged,
    }


def _safe_staging_file(root: Path, filename: Any, label: str) -> Path:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise ReleaseStageError(f"release staging {label} filename is invalid")
    path = root / filename
    if path.is_symlink() or not path.is_file():
        raise ReleaseStageError(f"release staging {label} artifact is missing or unsafe")
    return path


def _load_staging_manifest(directory: Path, *, run: Mapping[str, Any]) -> dict[str, Any]:
    path = directory / "staging" / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise GatePendingError("release must be staged before it can be exported")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ReleaseStageError("release staging manifest is malformed")
    manifest = verify_envelope(value, kind="release_staging_manifest")
    body = manifest["body"]
    expected = {
        "run_id": run["body"].get("run_id"),
        "plan_digest": run["body"].get("plan_digest"),
        "config_digest": run["body"].get("config_digest"),
        "viewer_profile_digest": run["body"].get("viewer_profile_digest"),
        "lock_digests": run["body"].get("lock_digests"),
    }
    if any(body.get(key) != value for key, value in expected.items()):
        raise ReleaseStageError("release staging manifest is not bound to the active run")
    if body.get("fixture_only") is not False or manifest.get("status") != "staged_not_exported":
        raise GatePendingError("fixture or non-production staging cannot be exported")
    staged = body.get("staged")
    if not isinstance(staged, list) or not staged:
        raise ReleaseStageError("release staging manifest has no staged candidates")
    return manifest


def _require_production_export_eligibility(
    *,
    run: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
) -> dict[str, Any]:
    """Check the immutable policy state before any file leaves private staging."""
    if run["body"].get("fixture_only") is not False:
        raise GatePendingError("fixture runs are never eligible for production export")
    config_body = config.get("body")
    viewer_body = viewer.get("body")
    if not isinstance(config_body, Mapping) or not isinstance(viewer_body, Mapping):
        raise ReleaseStageError("compiled config or viewer profile is malformed")
    if config_body.get("profile_mode") != "production_release" or config_body.get("release_eligible") is not True:
        raise GatePendingError("compiled config is not production-release eligible")
    release = config_body.get("release")
    if not isinstance(release, Mapping) or release.get("enabled") is not True:
        raise GatePendingError("production release is disabled by the frozen run profile")
    if viewer_body.get("local_fixture") is not False or viewer_body.get("production_eligible") is not True:
        raise GatePendingError("controlled viewer profile is not production eligible")
    routing = config_body.get("model_routing")
    if not isinstance(routing, Mapping) or routing.get("execution_eligible") is not True:
        raise GatePendingError("model routing is not execution eligible for production")
    if config_body.get("fixture_routing") is not None:
        raise GatePendingError("production export cannot retain a fixture routing policy")
    return _frozen_rubric(config)


def export_release(
    paths: WorkspacePaths,
    *,
    run_id: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Explicitly export a production-ready staged solver/scorer bundle.

    Audit material remains in the private run directory.  This function never
    creates a release from fixture inputs, and it refuses to overwrite a
    previous output directory.
    """
    paths.ensure()
    with run_state_lock(paths, run_id):
        return _export_release_locked(paths, run_id=run_id, output_dir=output_dir)


def _export_release_locked(
    paths: WorkspacePaths,
    *,
    run_id: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    run = load_run(paths, run_id)
    plan, config, viewer = validate_run_locks(paths, run)
    _require_production_export_eligibility(run=run, config=config, viewer=viewer)
    directory = run_directory(paths, run_id)
    manifest = _load_staging_manifest(directory, run=run)
    staging_body = manifest["body"]
    solver_staging = directory / "staging" / "solver"
    scorer_staging = directory / "staging" / "scorer"
    if solver_staging.is_symlink() or scorer_staging.is_symlink():
        raise ReleaseStageError("release staging directories cannot be symlinks")
    assert_solver_bundle_safe(solver_staging)

    destination = Path(output_dir).expanduser().resolve()
    try:
        destination.relative_to(paths.qagen.resolve())
    except ValueError:
        pass
    else:
        raise ReleaseStageError("production export output must remain outside the private .qagen workspace")
    if destination.exists():
        if not destination.is_dir():
            raise ReleaseStageError("production export output path is not a directory")
        if any(destination.iterdir()):
            raise ReleaseStageError("production export output directory must be new or empty")

    staged = staging_body["staged"]
    exportable_files: set[str] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any], str, str, dict[str, Any], dict[str, Any]]] = []
    for item in staged:
        if not isinstance(item, Mapping):
            raise ReleaseStageError("release staging entry is malformed")
        candidate_digest = item.get("candidate_digest")
        require_digest(candidate_digest, "staged candidate digest")
        require_digest(item.get("evidence_digest"), "staged evidence digest")
        require_digest(item.get("review_digest"), "staged review digest")
        require_digest(item.get("rubric_dry_run_digest"), "staged rubric dry-run digest")
        execution = _load_execution_for_candidate(
            paths,
            run=run,
            candidate_path=None,
            candidate_digest=candidate_digest,
        )
        execution_bindings = _execution_bindings(execution)
        if any(item.get(key) != value for key, value in execution_bindings.items()):
            raise ReleaseStageError("release staging entry is not bound to the persisted run execution")
        if item.get("evidence_digest") != execution_bindings["evidence_output_digest"]:
            raise ReleaseStageError("release staging entry is not bound to the persisted evidence")
        candidate = execution["candidate"]
        work_item = _candidate_work_item(candidate, run=run, plan=plan)
        if (
            item.get("work_item_id") != work_item.get("work_item_id")
            or execution["entry"].get("work_item_id") != work_item.get("work_item_id")
        ):
            raise ReleaseStageError("release staging entry is bound to another work item")
        receipt = _approved_review(
            paths,
            run=run,
            candidate_digest=candidate_digest,
            work_item=work_item,
            execution=execution,
        )
        if receipt["content_digest"] != item.get("review_digest"):
            raise ReleaseStageError("release staging entry is not bound to the approved review receipt")
        solver_source = _safe_staging_file(solver_staging, item.get("solver_file"), "solver")
        scorer_source = _safe_staging_file(scorer_staging, item.get("scorer_file"), "scorer")
        solver_descriptor = _require_artifact_descriptor(
            item.get("solver_artifact"),
            category="solver",
            filename=item.get("solver_file"),
        )
        scorer_descriptor = _require_artifact_descriptor(
            item.get("scorer_artifact"),
            category="scorer",
            filename=item.get("scorer_file"),
        )
        for descriptor in (solver_descriptor, scorer_descriptor):
            relative = descriptor["relative_path"]
            if relative in exportable_files:
                raise ReleaseStageError(f"release staging repeats an exportable artifact: {relative}")
            exportable_files.add(relative)
        solver, actual_solver_descriptor = _read_regular_json(
            solver_source,
            root=directory / "staging",
            label="solver",
        )
        scorer, actual_scorer_descriptor = _read_regular_json(
            scorer_source,
            root=directory / "staging",
            label="scorer",
        )
        if actual_solver_descriptor != solver_descriptor or actual_scorer_descriptor != scorer_descriptor:
            raise ReleaseStageError("staged exportable artifact content changed after staging")
        if solver != _solver_export(candidate, work_item=work_item):
            raise ReleaseStageError("staged solver artifact is not bound to the persisted candidate")
        if (
            not isinstance(scorer, Mapping)
            or scorer.get("artifact_kind") != "scorer_private_input"
            or scorer.get("candidate_digest") != candidate_digest
            or scorer.get("review_digest") != item.get("review_digest")
            or scorer.get("viewer_evidence_digest") != item.get("evidence_digest")
            or scorer.get("work_item_id") != work_item.get("work_item_id")
            or scorer.get("work_item_dependency_digest") != work_item.get("dependency_digest")
            or scorer.get("rubric_lock_digest") != config["body"]["rubric"]["lock_digest"]
            or any(scorer.get(key) != value for key, value in execution_bindings.items())
        ):
            raise ReleaseStageError("staged scorer artifact is not bound to the production release locks")
        pairs.append(
            (
                dict(solver),
                dict(scorer),
                str(item["solver_file"]),
                str(item["scorer_file"]),
                solver_descriptor,
                scorer_descriptor,
            )
        )

    _assert_exact_exportable_files(directory / "staging", exportable_files)

    destination.mkdir(parents=True, exist_ok=True)
    solver_output = destination / "solver"
    scorer_output = destination / "scorer"
    solver_output.mkdir(parents=True, exist_ok=False)
    scorer_output.mkdir(parents=True, exist_ok=False)
    for solver, scorer, solver_filename, scorer_filename, solver_descriptor, scorer_descriptor in pairs:
        solver_destination = solver_output / solver_filename
        scorer_destination = scorer_output / scorer_filename
        atomic_write_json(solver_destination, solver, private=False)
        atomic_write_json(scorer_destination, scorer, private=True)
        _, copied_solver_descriptor = _read_regular_json(
            solver_destination,
            root=destination,
            label="exported solver",
        )
        _, copied_scorer_descriptor = _read_regular_json(
            scorer_destination,
            root=destination,
            label="exported scorer",
        )
        if copied_solver_descriptor != solver_descriptor or copied_scorer_descriptor != scorer_descriptor:
            raise ReleaseStageError("exported artifact does not match its staging manifest descriptor")
    _assert_exact_exportable_files(destination, exportable_files)
    assert_solver_bundle_safe(solver_output)
    export_manifest = make_envelope(
        kind="production_release_export",
        identifier=f"release-{run_id}",
        status="exported",
        body={
            "run_id": run_id,
            "plan_digest": run["body"]["plan_digest"],
            "config_digest": run["body"]["config_digest"],
            "viewer_profile_digest": run["body"]["viewer_profile_digest"],
            "staging_manifest_digest": manifest["content_digest"],
            "solver_files": [solver_filename for _, _, solver_filename, _, _, _ in pairs],
            "scorer_files": [scorer_filename for _, _, _, scorer_filename, _, _ in pairs],
            "solver_artifacts": [solver_artifact for _, _, _, _, solver_artifact, _ in pairs],
            "scorer_artifacts": [scorer_artifact for _, _, _, _, _, scorer_artifact in pairs],
        },
        producer="qagen/0.1.0:release_export",
    )
    export_manifest_path = destination / "manifest.json"
    atomic_write_json(export_manifest_path, export_manifest, private=True)
    append_event(
        paths,
        "release_exported",
        {
            "run_id": run_id,
            "staging_manifest_digest": manifest["content_digest"],
            "candidate_count": len(pairs),
            "output_dir": str(destination),
        },
    )
    return {
        "run_id": run_id,
        "status": "exported",
        "output_dir": str(destination),
        "solver_export": str(solver_output),
        "scorer_export": str(scorer_output),
        "manifest_path": str(export_manifest_path),
        "manifest_digest": export_manifest["content_digest"],
    }
