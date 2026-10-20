"""Locked sub-domain feedback-policy validation.

The accepted feedback source is only planning input until it is compiled into
an approved knowledge snapshot.  This module turns the typed policy embedded
in that snapshot into a small immutable lock that can be carried by a work
item and checked at every candidate boundary.

It deliberately models an exact ``question_family_id`` separately from the
broader candidate-contract ``question_family_kind``.  The former is the
human-approved sub-domain family; the latter is only the common contract
shape.  Treating the two as interchangeable was what allowed a measurement
candidate to satisfy a generic kind check for a leaf whose accepted policy
forbids measurements.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..contracts import digest, require_digest, verify_envelope
from ..errors import InputError
from ..paths import WorkspacePaths
from ..storage.atomic import read_json, read_yaml


class FeedbackPolicyError(InputError):
    """A locked feedback policy or family declaration is invalid."""


FEEDBACK_FAMILY_KIND_TO_CONTRACT_KIND = {
    "property": "property_identification",
    "attribute": "attribute_classification",
    "comparison": "paired_comparison",
    "measurement": "measurement",
}
CONTRACT_FAMILY_KIND_TO_FEEDBACK_KIND = {
    contract_kind: feedback_kind
    for feedback_kind, contract_kind in FEEDBACK_FAMILY_KIND_TO_CONTRACT_KIND.items()
}
_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_FEEDBACK_KINDS = frozenset(FEEDBACK_FAMILY_KIND_TO_CONTRACT_KIND)
_GT_SOURCE_TYPES = frozenset(
    {
        "expert_annotation",
        "curated_source_label",
        "rule_evaluated_structure",
        "paired_comparison",
        "measurement_receipt",
    }
)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FeedbackPolicyError(f"{field}_invalid")
    return dict(value)


def _digest(value: Any, field: str) -> str:
    try:
        return require_digest(value, field)
    except InputError as exc:
        raise FeedbackPolicyError(f"{field}_invalid") from exc


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise FeedbackPolicyError(f"{field}_invalid")
    return value


def _percent(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 100:
        raise FeedbackPolicyError(f"{field}_invalid")
    return value


@dataclass(frozen=True)
class FeedbackPolicy:
    """Normalized typed policy compiled into one approved knowledge snapshot."""

    leaf_id: str
    document: Mapping[str, Any]
    content_digest: str

    @property
    def families(self) -> Mapping[str, str]:
        policy = self.document["question_family_policy"]
        return {
            str(item["family_id"]): str(item["family_kind"])
            for item in policy["allowed_families"]
        }

    @property
    def allowed_gt_source_types(self) -> frozenset[str]:
        return frozenset(str(item) for item in self.document["gt_policy"]["gt_source_types"])

    @property
    def measurement_max_fraction(self) -> float:
        return float(self.document["question_family_policy"]["quota"]["measurement_max"]) / 100.0

    @property
    def non_measurement_min_fraction(self) -> float:
        return float(self.document["question_family_policy"]["quota"]["non_measurement_min"]) / 100.0

    def family_contract_kind(self, family_id: str) -> str:
        try:
            family_kind = self.families[family_id]
        except KeyError as exc:
            raise FeedbackPolicyError("feedback_policy_question_family_not_allowed") from exc
        return FEEDBACK_FAMILY_KIND_TO_CONTRACT_KIND[family_kind]


@dataclass(frozen=True)
class LockedFeedbackPolicy:
    """A policy plus the exact question family authorized for one work item."""

    policy: FeedbackPolicy
    knowledge_snapshot_digest: str
    question_family_id: str

    @property
    def question_family_kind(self) -> str:
        return self.policy.family_contract_kind(self.question_family_id)

    @property
    def feedback_family_kind(self) -> str:
        return self.policy.families[self.question_family_id]


def parse_feedback_policy(value: Any, *, leaf_id: str) -> FeedbackPolicy:
    """Validate the executable subset of a knowledge snapshot feedback policy."""

    policy = _mapping(value, "feedback_policy")
    allowed_top_level = {
        "claims",
        "question_family_policy",
        "gt_policy",
        "subdomain_semantics",
        "asset_facets_schema",
        "medical_visibility_policy",
    }
    required_top_level = {"claims", "question_family_policy", "gt_policy"}
    if set(policy) - allowed_top_level or required_top_level - set(policy):
        raise FeedbackPolicyError("feedback_policy_shape_invalid")

    family_policy = _mapping(policy.get("question_family_policy"), "feedback_policy_question_family_policy")
    if set(family_policy) != {"allowed_families", "quota"}:
        raise FeedbackPolicyError("feedback_policy_question_family_policy_shape_invalid")
    raw_families = family_policy.get("allowed_families")
    if not isinstance(raw_families, list) or not raw_families:
        raise FeedbackPolicyError("feedback_policy_allowed_families_invalid")
    families: list[dict[str, str]] = []
    seen_families: set[str] = set()
    for raw in raw_families:
        family = _mapping(raw, "feedback_policy_allowed_family")
        if set(family) != {"family_id", "family_kind"}:
            raise FeedbackPolicyError("feedback_policy_allowed_family_shape_invalid")
        family_id = _identifier(family.get("family_id"), "feedback_policy_question_family_id")
        kind = family.get("family_kind")
        if family_id in seen_families or kind not in _FEEDBACK_KINDS:
            raise FeedbackPolicyError("feedback_policy_allowed_family_invalid")
        seen_families.add(family_id)
        families.append({"family_id": family_id, "family_kind": str(kind)})

    quota = _mapping(family_policy.get("quota"), "feedback_policy_quota")
    if set(quota) != {"scope", "unit", "measurement_max", "non_measurement_min"}:
        raise FeedbackPolicyError("feedback_policy_quota_shape_invalid")
    if quota.get("scope") != "portfolio" or quota.get("unit") != "percentage_points":
        raise FeedbackPolicyError("feedback_policy_quota_scope_invalid")
    measurement_max = _percent(quota.get("measurement_max"), "feedback_policy_measurement_max")
    non_measurement_min = _percent(
        quota.get("non_measurement_min"), "feedback_policy_non_measurement_min", minimum=1
    )
    if measurement_max + non_measurement_min > 100:
        raise FeedbackPolicyError("feedback_policy_quota_impossible")
    family_kinds = {item["family_kind"] for item in families}
    if "measurement" not in family_kinds and measurement_max != 0:
        raise FeedbackPolicyError("feedback_policy_measurement_quota_without_family")
    if not family_kinds - {"measurement"}:
        raise FeedbackPolicyError("feedback_policy_non_measurement_family_missing")

    gt_policy = _mapping(policy.get("gt_policy"), "feedback_policy_gt_policy")
    if set(gt_policy) - {"gt_source_types", "expert_approval_required", "curated_source_label_provenance"} or {
        "gt_source_types",
        "expert_approval_required",
    } - set(gt_policy):
        raise FeedbackPolicyError("feedback_policy_gt_policy_shape_invalid")
    source_types = gt_policy.get("gt_source_types")
    if (
        not isinstance(source_types, list)
        or not source_types
        or not all(isinstance(item, str) and item in _GT_SOURCE_TYPES for item in source_types)
        or len(set(source_types)) != len(source_types)
    ):
        raise FeedbackPolicyError("feedback_policy_gt_source_types_invalid")
    if type(gt_policy.get("expert_approval_required")) is not bool:
        raise FeedbackPolicyError("feedback_policy_gt_expert_approval_invalid")

    normalized = deepcopy(policy)
    normalized["question_family_policy"] = {
        "allowed_families": sorted(families, key=lambda item: item["family_id"]),
        "quota": {
            "scope": "portfolio",
            "unit": "percentage_points",
            "measurement_max": measurement_max,
            "non_measurement_min": non_measurement_min,
        },
    }
    normalized["gt_policy"] = {
        **{key: deepcopy(value) for key, value in gt_policy.items() if key == "curated_source_label_provenance"},
        "gt_source_types": sorted(source_types),
        "expert_approval_required": bool(gt_policy["expert_approval_required"]),
    }
    if normalized != policy:
        raise FeedbackPolicyError("feedback_policy_not_normalized")
    return FeedbackPolicy(leaf_id=leaf_id, document=normalized, content_digest=digest(normalized))


def feedback_policy_from_knowledge_snapshot(
    snapshot: Mapping[str, Any],
    *,
    leaf_id: str,
    expected_knowledge_snapshot_digest: str,
) -> FeedbackPolicy | None:
    """Extract one policy from the exact approved knowledge snapshot lock."""

    expected = _digest(expected_knowledge_snapshot_digest, "feedback_policy_knowledge_snapshot_digest")
    try:
        document = verify_envelope(dict(snapshot), kind="subdomain_knowledge_snapshot")
    except InputError as exc:
        raise FeedbackPolicyError("feedback_policy_knowledge_snapshot_invalid") from exc
    if document.get("content_digest") != expected:
        raise FeedbackPolicyError("feedback_policy_knowledge_snapshot_digest_mismatch")
    body = _mapping(document.get("body"), "feedback_policy_knowledge_snapshot_body")
    if document.get("status") != "approved" or body.get("status") != "approved" or body.get("id") != leaf_id:
        raise FeedbackPolicyError("feedback_policy_knowledge_snapshot_not_approved")
    raw_policy = body.get("feedback_policy")
    if raw_policy is None:
        return None
    policy = parse_feedback_policy(raw_policy, leaf_id=leaf_id)
    provenance = _mapping(body.get("provenance"), "feedback_policy_knowledge_provenance")
    if provenance.get("feedback_policy_digest") != policy.content_digest:
        raise FeedbackPolicyError("feedback_policy_knowledge_binding_mismatch")
    return policy


def load_workspace_feedback_policy(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    expected_knowledge_snapshot_digest: str,
) -> FeedbackPolicy | None:
    """Locate the one approved snapshot named by a work item's knowledge lock.

    A missing snapshot is retained as a legacy/non-generation state.  A found
    snapshot with the requested digest is never silently ignored: malformed or
    ambiguous feedback data is a fail-closed error.
    """

    expected = _digest(expected_knowledge_snapshot_digest, "feedback_policy_knowledge_snapshot_digest")
    root = paths.knowledge / "compiled"
    if not root.is_dir() or root.is_symlink():
        return None
    matches: list[FeedbackPolicy | None] = []
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.suffix.lower() in {".json", ".yaml", ".yml"}
        ),
        key=lambda item: item.as_posix(),
    )
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = read_yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else read_json(path)
        except (InputError, OSError):
            continue
        if not isinstance(value, Mapping) or value.get("content_digest") != expected:
            continue
        matches.append(
            feedback_policy_from_knowledge_snapshot(
                value,
                leaf_id=leaf_id,
                expected_knowledge_snapshot_digest=expected,
            )
        )
    if len(matches) > 1:
        raise FeedbackPolicyError("feedback_policy_knowledge_snapshot_ambiguous")
    return matches[0] if matches else None


def make_locked_feedback_policy(
    policy: FeedbackPolicy,
    *,
    knowledge_snapshot_digest: str,
    question_family_id: str,
) -> dict[str, Any]:
    """Create the immutable job projection for one selected exact family."""

    snapshot_digest = _digest(knowledge_snapshot_digest, "feedback_policy_knowledge_snapshot_digest")
    family_id = _identifier(question_family_id, "feedback_policy_question_family_id")
    contract_kind = policy.family_contract_kind(family_id)
    return {
        "knowledge_snapshot_digest": snapshot_digest,
        "content_digest": policy.content_digest,
        "policy": deepcopy(dict(policy.document)),
        "question_family_id": family_id,
        "question_family_kind": contract_kind,
    }


def load_locked_feedback_policy(
    value: Any,
    *,
    leaf_id: str,
    expected_knowledge_snapshot_digest: str,
) -> LockedFeedbackPolicy:
    """Revalidate the feedback-policy projection persisted in a work item."""

    lock = _mapping(value, "locked_feedback_policy")
    expected_fields = {
        "knowledge_snapshot_digest",
        "content_digest",
        "policy",
        "question_family_id",
        "question_family_kind",
    }
    if set(lock) != expected_fields:
        raise FeedbackPolicyError("locked_feedback_policy_shape_invalid")
    expected_snapshot = _digest(expected_knowledge_snapshot_digest, "feedback_policy_knowledge_snapshot_digest")
    if _digest(lock.get("knowledge_snapshot_digest"), "locked_feedback_policy_knowledge_snapshot_digest") != expected_snapshot:
        raise FeedbackPolicyError("locked_feedback_policy_knowledge_snapshot_mismatch")
    policy = parse_feedback_policy(lock.get("policy"), leaf_id=leaf_id)
    if _digest(lock.get("content_digest"), "locked_feedback_policy_digest") != policy.content_digest:
        raise FeedbackPolicyError("locked_feedback_policy_digest_mismatch")
    family_id = _identifier(lock.get("question_family_id"), "locked_feedback_policy_question_family_id")
    contract_kind = policy.family_contract_kind(family_id)
    if lock.get("question_family_kind") != contract_kind:
        raise FeedbackPolicyError("locked_feedback_policy_question_family_kind_mismatch")
    return LockedFeedbackPolicy(
        policy=policy,
        knowledge_snapshot_digest=expected_snapshot,
        question_family_id=family_id,
    )


def validate_feedback_policy_contract(
    contract: Mapping[str, Any],
    *,
    locked_policy: LockedFeedbackPolicy | FeedbackPolicy,
    expected_question_family_id: str | None = None,
) -> str:
    """Validate the exact family and GT source type of one candidate contract.

    Returns the selected canonical family identifier for callers that need to
    include it in coverage audit data.
    """

    policy = locked_policy.policy if isinstance(locked_policy, LockedFeedbackPolicy) else locked_policy
    required_family_id = (
        locked_policy.question_family_id
        if isinstance(locked_policy, LockedFeedbackPolicy)
        else expected_question_family_id
    )
    if expected_question_family_id is not None:
        required_family_id = _identifier(expected_question_family_id, "feedback_policy_expected_question_family_id")
    family_id = _identifier(contract.get("question_family_id"), "feedback_policy_question_family_id")
    if family_id not in policy.families:
        raise FeedbackPolicyError("feedback_policy_question_family_not_allowed")
    if required_family_id is not None and family_id != required_family_id:
        raise FeedbackPolicyError("feedback_policy_question_family_not_plan_locked")
    expected_kind = policy.family_contract_kind(family_id)
    if contract.get("question_family_kind") != expected_kind:
        raise FeedbackPolicyError("feedback_policy_question_family_kind_mismatch")
    gt = _mapping(contract.get("gt_answer_contract"), "feedback_policy_gt_answer_contract")
    if gt.get("source_type") not in policy.allowed_gt_source_types:
        raise FeedbackPolicyError("feedback_policy_gt_source_type_not_allowed")
    return family_id
