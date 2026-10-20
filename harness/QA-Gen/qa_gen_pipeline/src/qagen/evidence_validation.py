"""Canonical validation for persisted fixture and provider evidence.

The worker output alone is never sufficient for provider evidence.  A
``provider_attested_viewer_evidence`` packet is only a digest-bound reference
to host-generated evidence, so this module always roots validation in the
persisted execution record.  ``load_persisted_execution`` revalidates the
on-disk ViewerProfile, private session, action receipts, and render files
before this module checks the candidate, verifier, and plan bindings that
review and release share.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from .contracts import digest, require_digest
from .errors import InputError
from .paths import WorkspacePaths
from .run import load_persisted_execution
from .viewer import (
    validate_anonymous_scene_ref,
    validate_controlled_session_request,
    validate_local_evidence_packet,
)
from .workers import validate_verification_result


class EvidenceValidationError(InputError):
    """Evidence cannot be accepted by either review or release staging."""


EvidenceKind = Literal["legacy_fixture", "provider_chirality"]

_FIXTURE_EVIDENCE_KIND = "fixture_viewer_evidence"
_PROVIDER_EVIDENCE_KIND = "provider_attested_viewer_evidence"
_PROVIDER_EXECUTION_MODE = "local_provider_pilot"
_PROVIDER_EVIDENCE_FIELDS = {
    "artifact_kind",
    "candidate_digest",
    "viewer_profile_digest",
    "viewer_operations",
    "host_evidence_digest",
    "receipt_digests",
    "work_item_id",
    "state_transition",
}
_HOST_EVIDENCE_FIELDS = {
    "host_evidence_path",
    "host_evidence_digest",
    "viewer_profile_digest",
    "scene_registry_digest",
    "anonymous_scene_ref",
    "session_attestation_digest",
    "receipt_digests",
    "receipt_chain_digest",
    "render_digests",
}


@dataclass(frozen=True)
class ValidatedEvidence:
    """The evidence kind and durable execution accepted by the shared gate."""

    kind: EvidenceKind
    candidate_digest: str
    evidence_digest: str
    execution: dict[str, Any]
    controlled_request: dict[str, Any]


def _evidence_kind(evidence: Mapping[str, Any]) -> EvidenceKind:
    artifact_kind = evidence.get("artifact_kind")
    # Older local-fixture execution artifacts predate an explicit kind.  They
    # remain admissible only through the fixture branch and its observations.
    if artifact_kind in {None, _FIXTURE_EVIDENCE_KIND}:
        return "legacy_fixture"
    if artifact_kind == _PROVIDER_EVIDENCE_KIND:
        return "provider_chirality"
    raise EvidenceValidationError("evidence artifact kind is not accepted by review or release")


def _nonempty_string_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
    ):
        raise EvidenceValidationError(f"{field} must be a non-empty unique string list")
    return list(value)


def _require_digest(value: Any, field: str) -> str:
    try:
        return require_digest(value, field)
    except InputError as exc:
        raise EvidenceValidationError(str(exc)) from exc


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceValidationError(f"{field} must be an object")
    return value


def _validate_common_declarations(
    evidence: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    candidate_digest: str,
    viewer: Mapping[str, Any],
    work_item: Mapping[str, Any],
) -> dict[str, Any]:
    if evidence.get("candidate_digest") != candidate_digest:
        raise EvidenceValidationError("evidence must bind the exact candidate digest")
    profile_digest = _require_digest(viewer.get("content_digest"), "persisted ViewerProfile digest")
    if evidence.get("viewer_profile_digest") != profile_digest:
        raise EvidenceValidationError("evidence must bind the persisted ViewerProfile")
    request = _mapping(candidate.get("viewer_session_request"), "candidate viewer session request")
    approved_operations = _nonempty_string_list(work_item.get("approved_operations"), "approved work item operations")
    try:
        validate_anonymous_scene_ref(candidate.get("anonymous_scene_ref"))
        controlled_request = validate_controlled_session_request(
            request,
            viewer_profile=viewer,
            approved_operations=set(approved_operations),
        )
    except InputError as exc:
        raise EvidenceValidationError(f"controlled viewer contract failed: {exc}") from exc

    reported_operations = evidence.get("viewer_operations", evidence.get("allowed_operations"))
    if reported_operations is not None:
        try:
            operations = _nonempty_string_list(reported_operations, "evidence viewer operations")
        except EvidenceValidationError:
            raise EvidenceValidationError("evidence reports viewer operations outside the approved plan work item") from None
        if not set(operations).issubset(set(approved_operations)):
            raise EvidenceValidationError("evidence reports viewer operations outside the approved plan work item")
    return controlled_request


def _validate_fixture_evidence(
    evidence: Mapping[str, Any],
    *,
    candidate_digest: str,
    viewer: Mapping[str, Any],
    approved_operations: set[str],
    controlled_request: Mapping[str, Any],
) -> None:
    observations = evidence.get("observations")
    if not isinstance(observations, list) or not observations:
        raise EvidenceValidationError("legacy fixture evidence requires a non-empty observations list")
    try:
        validate_local_evidence_packet(
            evidence,
            candidate_digest=candidate_digest,
            viewer_profile=viewer,
            approved_operations=approved_operations,
            require_full_trace=False,
            expected_session_request_id=controlled_request["request_id"],
        )
    except InputError as exc:
        raise EvidenceValidationError(f"legacy fixture evidence contract failed: {exc}") from exc


def _validate_provider_evidence_shape(
    evidence: Mapping[str, Any],
    *,
    candidate_digest: str,
    viewer_profile_digest: str,
    work_item_id: str,
    approved_operations: set[str],
) -> None:
    if "observations" in evidence:
        raise EvidenceValidationError("provider chirality evidence cannot contain model-reported observations")
    if set(evidence) != _PROVIDER_EVIDENCE_FIELDS:
        raise EvidenceValidationError("provider chirality evidence has an invalid field set")
    if evidence.get("artifact_kind") != _PROVIDER_EVIDENCE_KIND:
        raise EvidenceValidationError("provider chirality evidence has an invalid artifact kind")
    if evidence.get("candidate_digest") != candidate_digest:
        raise EvidenceValidationError("provider chirality evidence is bound to another candidate")
    if evidence.get("viewer_profile_digest") != viewer_profile_digest:
        raise EvidenceValidationError("provider chirality evidence is bound to another ViewerProfile")
    if evidence.get("work_item_id") != work_item_id:
        raise EvidenceValidationError("provider chirality evidence is bound to another work item")
    _require_digest(evidence.get("host_evidence_digest"), "provider host evidence digest")
    receipts = evidence.get("receipt_digests")
    if not isinstance(receipts, list) or len(receipts) < 2 or len(set(receipts)) != len(receipts):
        raise EvidenceValidationError("provider chirality evidence requires distinct host action receipts")
    for index, receipt in enumerate(receipts):
        _require_digest(receipt, f"provider action receipt {index}")
    operations = set(_nonempty_string_list(evidence.get("viewer_operations"), "provider viewer operations"))
    if operations != approved_operations:
        raise EvidenceValidationError("provider chirality evidence operations do not match the approved work item")
    if evidence.get("state_transition") is not None:
        raise EvidenceValidationError("provider chirality evidence state transition must be null")


def _validate_execution_bindings(
    execution: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    candidate_digest: str,
    evidence_digest: str,
    work_item: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    entry = _mapping(execution.get("entry"), "persisted execution entry")
    verification = _mapping(execution.get("verification"), "persisted verifier output")
    if execution.get("candidate") != dict(candidate) or execution.get("evidence") != dict(evidence):
        raise EvidenceValidationError("persisted execution artifacts differ from the review/release inputs")
    expected_work_item_id = work_item.get("work_item_id")
    expected_leaf_id = work_item.get("leaf_id")
    expected_dependency = work_item.get("dependency_digest")
    if (
        not isinstance(expected_work_item_id, str)
        or not isinstance(expected_leaf_id, str)
        or entry.get("candidate_digest") != candidate_digest
        or entry.get("evidence_digest") != evidence_digest
        or entry.get("work_item_id") != expected_work_item_id
        or entry.get("leaf_id") != expected_leaf_id
        or entry.get("dependency_digest") != expected_dependency
    ):
        raise EvidenceValidationError("persisted execution is bound to another candidate or work item")
    provenance = _mapping(candidate.get("provenance"), "candidate provenance")
    if evidence.get("work_item_id") != expected_work_item_id or provenance.get("work_item_id") != expected_work_item_id:
        raise EvidenceValidationError("candidate and evidence must bind the approved work item")
    if (
        verification.get("candidate_digest") != candidate_digest
        or verification.get("evidence_digest") != evidence_digest
        or verification.get("work_item_id") != expected_work_item_id
    ):
        raise EvidenceValidationError("persisted verifier output is not bound to the candidate, evidence, and work item")
    try:
        validate_verification_result(verification, require_pass=True)
    except InputError as exc:
        raise EvidenceValidationError(f"persisted verifier output is invalid: {exc}") from exc
    return entry, verification


def _validate_provider_chain(
    execution: Mapping[str, Any],
    *,
    entry: Mapping[str, Any],
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    verification: Mapping[str, Any],
    viewer: Mapping[str, Any],
) -> None:
    if entry.get("execution_mode") != _PROVIDER_EXECUTION_MODE or entry.get("production_eligible") is not False:
        raise EvidenceValidationError("provider chirality evidence requires a non-production persisted provider execution")
    host = _mapping(execution.get("host_evidence"), "persisted provider host evidence")
    if set(host) != _HOST_EVIDENCE_FIELDS:
        raise EvidenceValidationError("persisted provider host evidence bindings are malformed")
    profile_digest = _require_digest(viewer.get("content_digest"), "persisted ViewerProfile digest")
    viewer_body = _mapping(viewer.get("body"), "persisted ViewerProfile body")
    if viewer_body.get("local_fixture") is not False:
        raise EvidenceValidationError("provider chirality evidence requires a non-fixture ViewerProfile")
    for field, label in (
        ("host_evidence_digest", "provider host evidence digest"),
        ("viewer_profile_digest", "provider host ViewerProfile digest"),
        ("scene_registry_digest", "provider host SceneRegistry digest"),
        ("session_attestation_digest", "provider host session attestation digest"),
        ("receipt_chain_digest", "provider host receipt chain digest"),
    ):
        _require_digest(host.get(field), label)
    if (
        host.get("viewer_profile_digest") != profile_digest
        or viewer_body.get("scene_registry_digest") != host.get("scene_registry_digest")
        or entry.get("viewer_profile_digest") != profile_digest
        or entry.get("scene_registry_digest") != host.get("scene_registry_digest")
        or entry.get("host_evidence_digest") != host.get("host_evidence_digest")
        or entry.get("session_attestation_digest") != host.get("session_attestation_digest")
    ):
        raise EvidenceValidationError("provider host evidence is not bound to the persisted ViewerProfile and session")
    receipt_digests = host.get("receipt_digests")
    render_digests = host.get("render_digests")
    if (
        not isinstance(receipt_digests, list)
        or not isinstance(render_digests, list)
        or len(receipt_digests) < 2
        or len(receipt_digests) != len(render_digests)
        or len(set(receipt_digests)) != len(receipt_digests)
        or len(set(render_digests)) != len(render_digests)
    ):
        raise EvidenceValidationError("provider host evidence lacks a complete action-receipt/render chain")
    for index, value in enumerate([*receipt_digests, *render_digests]):
        _require_digest(value, f"provider host receipt/render digest {index}")
    if host.get("receipt_chain_digest") != digest(receipt_digests):
        raise EvidenceValidationError("provider host action receipt chain digest changed")
    if entry.get("receipt_digests") != receipt_digests or entry.get("render_digests") != render_digests:
        raise EvidenceValidationError("persisted provider execution lost its host receipt/render bindings")
    if evidence.get("host_evidence_digest") != host.get("host_evidence_digest") or evidence.get("receipt_digests") != receipt_digests:
        raise EvidenceValidationError("provider evidence is not bound to the persisted host evidence")
    if candidate.get("anonymous_scene_ref") != host.get("anonymous_scene_ref"):
        raise EvidenceValidationError("provider candidate scene is not bound to the persisted host evidence")
    candidate_attestation = {
        "handoff_digest": host["host_evidence_digest"],
        "receipt_digests": receipt_digests,
        "render_digests": render_digests,
    }
    if candidate.get("attested_evidence") != candidate_attestation:
        raise EvidenceValidationError("provider candidate is not bound to host receipt and render attachments")
    verifier_attestation = {
        "handoff_digest": host["host_evidence_digest"],
        "receipt_digests": receipt_digests,
    }
    if verification.get("attested_evidence") != verifier_attestation:
        raise EvidenceValidationError("provider verifier is not bound to the persisted host evidence")


def validate_persisted_evidence(
    paths: WorkspacePaths,
    *,
    run: Mapping[str, Any],
    viewer: Mapping[str, Any],
    candidate_path: str | Path,
    candidate: Mapping[str, Any],
    evidence_path: str | Path,
    evidence: Mapping[str, Any],
    work_item: Mapping[str, Any],
) -> ValidatedEvidence:
    """Validate one evidence artifact through the appropriate durable chain.

    Both review and private release staging call this exact function.  For a
    fixture it preserves the legacy observations contract.  For a provider
    chirality artifact, model-written observations are prohibited and the
    only accepted visual facts are those already revalidated from private host
    evidence by :func:`qagen.run.load_persisted_execution`.
    """
    if not isinstance(candidate, Mapping) or not isinstance(evidence, Mapping):
        raise EvidenceValidationError("candidate and evidence must be objects")
    kind = _evidence_kind(evidence)
    candidate_digest = digest(dict(candidate))
    evidence_digest = digest(dict(evidence))
    controlled_request = _validate_common_declarations(
        evidence,
        candidate=candidate,
        candidate_digest=candidate_digest,
        viewer=viewer,
        work_item=work_item,
    )
    approved_operations = set(_nonempty_string_list(work_item.get("approved_operations"), "approved work item operations"))
    if kind == "legacy_fixture":
        _validate_fixture_evidence(
            evidence,
            candidate_digest=candidate_digest,
            viewer=viewer,
            approved_operations=approved_operations,
            controlled_request=controlled_request,
        )
    else:
        work_item_id = work_item.get("work_item_id")
        if not isinstance(work_item_id, str) or not work_item_id:
            raise EvidenceValidationError("approved provider work item id is malformed")
        _validate_provider_evidence_shape(
            evidence,
            candidate_digest=candidate_digest,
            viewer_profile_digest=controlled_request["viewer_profile_digest"],
            work_item_id=work_item_id,
            approved_operations=approved_operations,
        )
    try:
        execution = load_persisted_execution(
            paths,
            run=run,
            candidate_path=candidate_path,
            candidate_digest=candidate_digest,
            evidence_path=evidence_path,
            evidence_digest=evidence_digest,
        )
    except InputError as exc:
        raise EvidenceValidationError(f"evidence is not bound to a persisted run execution: {exc}") from exc
    entry, verification = _validate_execution_bindings(
        execution,
        candidate=candidate,
        evidence=evidence,
        candidate_digest=candidate_digest,
        evidence_digest=evidence_digest,
        work_item=work_item,
    )
    if kind == "legacy_fixture":
        if entry.get("execution_mode", "local_fixture") != "local_fixture":
            raise EvidenceValidationError("legacy fixture evidence is not bound to a fixture execution")
    else:
        _validate_provider_chain(
            execution,
            entry=entry,
            candidate=candidate,
            evidence=evidence,
            verification=verification,
            viewer=viewer,
        )
    return ValidatedEvidence(
        kind=kind,
        candidate_digest=candidate_digest,
        evidence_digest=evidence_digest,
        execution=dict(execution),
        controlled_request=controlled_request,
    )
