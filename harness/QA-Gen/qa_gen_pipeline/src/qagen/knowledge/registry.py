"""Deterministic local Knowledge Registry with explicit human gates."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterable, Mapping

import yaml

from ..contracts import (
    canonical_json,
    canonical_sha256,
    digest,
    digest_bytes,
    make_decision,
    make_envelope,
    require_digest,
    require_nonempty_string,
    verify_envelope,
)
from ..errors import GatePendingError, InputError, InvalidStateError, StaleInputError
from ..paths import WorkspacePaths, require_within, safe_component
from ..registry_contracts import validate_knowledge_index, validate_seed_index
from ..storage.atomic import atomic_copy_file, atomic_write_json, atomic_write_text, atomic_write_yaml, read_json, read_yaml
from ..storage.ledger import append_event, reserve_usage, settle_usage


_SOURCE_SPEC_RE = re.compile(r"^(expert|taxonomy|feedback):([^:]+):(.+)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TYPED_POLICY_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ALLOWED_TRUTH_MODES = {"visual_annotation", "expert_visual_judgment", "viewer_native_measurement"}
_REQUIRED_SHORTCUTS = {"raw_asset_read", "metadata_lookup", "network_search", "arbitrary_compute"}
_COMPILER_DIGEST = digest({"adapter": "knowledge_curator_deterministic", "version": "0.1.0"})
_KNOWLEDGE_DECISION_THREAD_LOCK = threading.RLock()
_STRUCTURED_SOURCE_KINDS = {
    "expert": "qagen_expert_knowledge_source",
    "taxonomy": "qagen_visual_taxonomy_source",
    "feedback": "qagen_expert_feedback_source",
}
_STRUCTURED_EXPERT_FIELDS = {
    "scientific_scope",
    "scenario_cards",
    "allowed_inference",
    "common_failure_modes",
    "rubric_extensions",
}
_STRUCTURED_TAXONOMY_FIELDS = {
    "visual_target_types",
    "forbidden_shortcuts",
    "evidence_requirements",
    "requirements",
}
_FEEDBACK_SOURCE_FIELDS = {
    "schema_version",
    "kind",
    "id",
    "leaf_id",
    "source_status",
    "production_approval",
    "reference_sources",
    "content",
}
_FEEDBACK_CONTENT_FIELDS = {
    "claims",
    "question_family_policy",
    "gt_policy",
    "subdomain_semantics",
    "asset_facets_schema",
    "medical_visibility_policy",
}
_FEEDBACK_CLAIM_FIELDS = {"claim_id", "rule_id"}
_ALLOWED_FEEDBACK_RULE_IDS = {
    "forbid_ui_or_operation_instruction",
    "forbid_ungrounded_ground_truth",
    "forbid_viewer_shortcuts",
    "limit_measurement_saturation",
    "require_expert_ground_truth_approval",
    "require_viewer_grounded_evidence",
}
_ALLOWED_QUESTION_FAMILY_KINDS = {"property", "attribute", "comparison", "measurement"}
_MEDICAL_STRUCTURE_FIELDS = {
    "subdomain_semantics",
    "asset_facets_schema",
    "medical_visibility_policy",
}
_CANONICAL_GT_SOURCE_TYPES = {
    "expert_annotation",
    "curated_source_label",
    "rule_evaluated_structure",
    "paired_comparison",
    "measurement_receipt",
}
_GT_SOURCE_TYPE_ALIASES = {
    "visual_annotation": "expert_annotation",
    "expert_visual_judgment": "expert_annotation",
    "viewer_native_measurement": "measurement_receipt",
}
_ALLOWED_GT_SOURCE_TYPES = _CANONICAL_GT_SOURCE_TYPES | set(_GT_SOURCE_TYPE_ALIASES)
_REVIEW_SAFE_FORBIDDEN_KEYS = {
    "answer",
    "asset_id",
    "asset_path",
    "ground_truth",
    "instruction",
    "question",
    "repo_path",
    "source_task_id",
    "task_id",
}
_REVIEW_SAFE_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bsv-(?:biol|chem|medi)-[a-z0-9-]+", re.IGNORECASE),
    re.compile(r"(?:^|\s)/(?:Users|data|crawl_data)/"),
    re.compile(r"[A-Za-z]:\\\\"),
)


def _short(value: str) -> str:
    return value.split(":", 1)[-1][:16]


@contextmanager
def _knowledge_object_lock(directory: Path, object_kind: str, object_id: str):
    """Serialize one knowledge object transition across threads and processes."""
    directory.mkdir(parents=True, exist_ok=True)
    token = digest({"kind": object_kind, "id": object_id}).split(":", 1)[1]
    lock_path = directory / f".{object_kind}-{token}.lock"
    with _KNOWLEDGE_DECISION_THREAD_LOCK:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _create_knowledge_receipt(path: Path, value: Mapping[str, Any]) -> None:
    """Create one immutable review receipt without replacing an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(dict(value)) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _restore_text_file(path: Path, previous: bytes | None, *, private: bool) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_text(path, previous.decode("utf-8"), private=private)


def _parse_source_spec(value: str) -> tuple[str, str, Path]:
    match = _SOURCE_SPEC_RE.fullmatch(value)
    if not match:
        raise InputError(
            "--source must have form expert:<id>:<local-path>, taxonomy:<id>:<local-path>, "
            "or feedback:<id>:<local-path>"
        )
    kind, source_id, raw_path = match.groups()
    safe_component(source_id, "source id")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise InputError(f"knowledge source is not a local file: {path}")
    if str(raw_path).startswith(("http://", "https://", "feishu://")):
        raise InputError("live network and Feishu sources are not supported; export a local file first")
    return kind, source_id, path


def _normalized_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"knowledge source must be UTF-8 text: {path}") from exc
    except OSError as exc:
        raise InputError(f"cannot read knowledge source {path}: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "section"


def _sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _HEADING_RE.match(line)
        if match:
            sections.append(
                {
                    "section_id": f"section-{len(sections) + 1}-{_slug(match.group(2))}",
                    "heading": match.group(2),
                    "line_start": line_number,
                }
            )
    if not sections:
        sections.append({"section_id": "document", "heading": "document", "line_start": 1})
    return sections


def _typed_policy_id(value: Any, field: str) -> str:
    identifier = require_nonempty_string(value, field)
    if not _TYPED_POLICY_ID_RE.fullmatch(identifier):
        raise InputError(f"{field} must use lower-case typed policy identifier syntax")
    return identifier


def _typed_int(value: Any, field: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise InputError(f"{field} must be an integer greater than or equal to {minimum}")
    return value


def _feedback_claims(value: Any, source_id: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise InputError(f"expert feedback claims must be a non-empty list: {source_id}")
    claims: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_claim in enumerate(value):
        if not isinstance(raw_claim, Mapping):
            raise InputError(f"expert feedback claim must be an object: {source_id}[{index}]")
        unknown = sorted(set(raw_claim) - _FEEDBACK_CLAIM_FIELDS)
        missing = sorted(_FEEDBACK_CLAIM_FIELDS - set(raw_claim))
        if missing or unknown:
            detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
            raise InputError(f"expert feedback claim fields are invalid: {source_id}[{index}] ({detail})")
        claim_id = _typed_policy_id(raw_claim.get("claim_id"), f"expert feedback claim_id {source_id}[{index}]")
        if claim_id in seen:
            raise InputError(f"expert feedback claim_id must be unique: {source_id} ({claim_id})")
        seen.add(claim_id)
        rule_id = _typed_policy_id(raw_claim.get("rule_id"), f"expert feedback rule_id {source_id}[{index}]")
        if rule_id not in _ALLOWED_FEEDBACK_RULE_IDS:
            raise InputError(f"expert feedback rule_id is unsupported: {source_id} ({rule_id})")
        claims.append({"claim_id": claim_id, "rule_id": rule_id})
    return sorted(claims, key=lambda claim: claim["claim_id"])


def _question_family_policy(value: Any, source_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"expert feedback question_family_policy must be an object: {source_id}")
    required = {"allowed_families", "quota"}
    unknown = sorted(set(value) - required)
    missing = sorted(required - set(value))
    if missing or unknown:
        detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
        raise InputError(f"expert feedback question_family_policy fields are invalid: {source_id} ({detail})")
    raw_families = value.get("allowed_families")
    if not isinstance(raw_families, list) or not raw_families:
        raise InputError(f"expert feedback allowed_families must be a non-empty list: {source_id}")
    families: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw_family in enumerate(raw_families):
        if not isinstance(raw_family, Mapping):
            raise InputError(f"expert feedback question family must be an object: {source_id}[{index}]")
        fields = {"family_id", "family_kind"}
        unknown = sorted(set(raw_family) - fields)
        missing = sorted(fields - set(raw_family))
        if missing or unknown:
            detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
            raise InputError(f"expert feedback question family fields are invalid: {source_id}[{index}] ({detail})")
        family_id = _typed_policy_id(raw_family.get("family_id"), f"expert feedback family_id {source_id}[{index}]")
        if family_id in seen:
            raise InputError(f"expert feedback family_id must be unique: {source_id} ({family_id})")
        seen.add(family_id)
        family_kind = require_nonempty_string(raw_family.get("family_kind"), f"expert feedback family_kind {source_id}[{index}]")
        if family_kind not in _ALLOWED_QUESTION_FAMILY_KINDS:
            raise InputError(f"expert feedback family_kind is unsupported: {source_id} ({family_kind})")
        families.append({"family_id": family_id, "family_kind": family_kind})
    raw_quota = value.get("quota")
    if not isinstance(raw_quota, Mapping):
        raise InputError(f"expert feedback question_family_policy quota must be an object: {source_id}")
    quota_fields = {"scope", "unit", "measurement_max", "non_measurement_min"}
    unknown = sorted(set(raw_quota) - quota_fields)
    missing = sorted(quota_fields - set(raw_quota))
    if missing or unknown:
        detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
        raise InputError(f"expert feedback question family quota fields are invalid: {source_id} ({detail})")
    quota = {
        "scope": raw_quota.get("scope"),
        "unit": raw_quota.get("unit"),
        "measurement_max": _typed_int(
            raw_quota.get("measurement_max"), f"expert feedback measurement_max {source_id}", minimum=0
        ),
        "non_measurement_min": _typed_int(
            raw_quota.get("non_measurement_min"), f"expert feedback non_measurement_min {source_id}", minimum=1
        ),
    }
    if quota["scope"] != "portfolio" or quota["unit"] != "percentage_points":
        raise InputError(
            f"expert feedback question family quota must be portfolio percentage_points: {source_id}"
        )
    if quota["measurement_max"] > 100 or quota["non_measurement_min"] > 100:
        raise InputError(f"expert feedback question family quota cannot exceed 100 percentage points: {source_id}")
    if quota["measurement_max"] + quota["non_measurement_min"] > 100:
        raise InputError(f"expert feedback question family quota is impossible: {source_id}")
    kinds = {family["family_kind"] for family in families}
    if "measurement" not in kinds and quota["measurement_max"] != 0:
        raise InputError(f"expert feedback measurement_max requires a measurement family: {source_id}")
    if not (kinds - {"measurement"}):
        raise InputError(f"expert feedback requires a non-measurement question family: {source_id}")
    return {"allowed_families": sorted(families, key=lambda family: family["family_id"]), "quota": quota}


def _asset_facets_schema(value: Any, source_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"expert feedback asset_facets_schema must be an object: {source_id}")
    fields = {"facets"}
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if missing or unknown:
        detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
        raise InputError(f"expert feedback asset_facets_schema fields are invalid: {source_id} ({detail})")
    raw_facets = value.get("facets")
    if not isinstance(raw_facets, list) or not raw_facets:
        raise InputError(f"expert feedback asset_facets_schema facets must be a non-empty list: {source_id}")
    facets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_facet in enumerate(raw_facets):
        if not isinstance(raw_facet, Mapping):
            raise InputError(f"expert feedback asset facet must be an object: {source_id}[{index}]")
        facet_fields = {"facet_id", "cardinality", "required"}
        unknown = sorted(set(raw_facet) - facet_fields)
        missing = sorted(facet_fields - set(raw_facet))
        if missing or unknown:
            detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
            raise InputError(f"expert feedback asset facet fields are invalid: {source_id}[{index}] ({detail})")
        facet_id = _typed_policy_id(raw_facet.get("facet_id"), f"expert feedback asset facet_id {source_id}[{index}]")
        if facet_id in seen:
            raise InputError(f"expert feedback asset facet_id must be unique: {source_id} ({facet_id})")
        seen.add(facet_id)
        cardinality = raw_facet.get("cardinality")
        if cardinality not in {"single", "multiple"}:
            raise InputError(f"expert feedback asset facet cardinality is invalid: {source_id} ({facet_id})")
        required = raw_facet.get("required")
        if type(required) is not bool:
            raise InputError(f"expert feedback asset facet required must be boolean: {source_id} ({facet_id})")
        facets.append({"facet_id": facet_id, "cardinality": cardinality, "required": required})
    return {"facets": sorted(facets, key=lambda facet: facet["facet_id"])}


def _medical_visibility_policy(value: Any, source_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"expert feedback medical_visibility_policy must be an object: {source_id}")
    fields = {
        "solver_visible_segmentation",
        "hidden_reference_mask_allowed",
        "label_identity_policy",
        "semantic_scoring_required",
        "duplicate_coverage_key",
    }
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if missing or unknown:
        detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
        raise InputError(f"expert feedback medical_visibility_policy fields are invalid: {source_id} ({detail})")
    if value.get("solver_visible_segmentation") is not False:
        raise InputError(f"expert feedback medical solver_visible_segmentation must be false: {source_id}")
    if value.get("hidden_reference_mask_allowed") != "conditional":
        raise InputError(f"expert feedback medical hidden_reference_mask_allowed must be conditional: {source_id}")
    if value.get("label_identity_policy") != "preserve":
        raise InputError(f"expert feedback medical label_identity_policy must be preserve: {source_id}")
    if value.get("semantic_scoring_required") is not True:
        raise InputError(f"expert feedback medical semantic_scoring_required must be true: {source_id}")
    return {
        "solver_visible_segmentation": False,
        "hidden_reference_mask_allowed": "conditional",
        "label_identity_policy": "preserve",
        "semantic_scoring_required": True,
        "duplicate_coverage_key": _typed_policy_id(
            value.get("duplicate_coverage_key"), f"expert feedback duplicate_coverage_key {source_id}"
        ),
    }


def _canonical_gt_source_type(value: Any, field: str) -> str:
    source_type = require_nonempty_string(value, field)
    if source_type not in _ALLOWED_GT_SOURCE_TYPES:
        raise InputError(f"expert feedback gt_source_type is unsupported: {field} ({source_type})")
    return _GT_SOURCE_TYPE_ALIASES.get(source_type, source_type)


def _gt_policy(
    value: Any,
    source_id: str,
    *,
    question_family_policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"expert feedback gt_policy must be an object: {source_id}")
    required = {"gt_source_types", "expert_approval_required"}
    optional = {"curated_source_label_provenance"}
    unknown = sorted(set(value) - required - optional)
    missing = sorted(required - set(value))
    if missing or unknown:
        detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
        raise InputError(f"expert feedback gt_policy fields are invalid: {source_id} ({detail})")
    raw_source_types = value.get("gt_source_types")
    if not isinstance(raw_source_types, list) or not raw_source_types:
        raise InputError(f"expert feedback gt_source_types must be a non-empty list: {source_id}")
    source_types: list[str] = []
    for index, raw_source_type in enumerate(raw_source_types):
        source_types.append(
            _canonical_gt_source_type(raw_source_type, f"expert feedback gt_source_types {source_id}[{index}]")
        )
    if len(set(source_types)) != len(source_types):
        raise InputError(f"expert feedback gt_source_types must be unique: {source_id}")
    expert_approval_required = value.get("expert_approval_required")
    if type(expert_approval_required) is not bool:
        raise InputError(f"expert feedback gt_policy expert_approval_required must be boolean: {source_id}")
    non_measurement_family = question_family_policy is None or any(
        family["family_kind"] != "measurement"
        for family in question_family_policy["allowed_families"]
    )
    if non_measurement_family and not expert_approval_required:
        raise InputError(
            f"expert feedback gt_policy must require expert approval for non-measurement families: {source_id}"
        )
    if not expert_approval_required and set(source_types) != {"measurement_receipt"}:
        raise InputError(
            f"expert feedback gt_policy without expert approval is limited to measurement_receipt: {source_id}"
        )
    policy: dict[str, Any] = {
        "gt_source_types": sorted(source_types),
        "expert_approval_required": expert_approval_required,
    }
    provenance = value.get("curated_source_label_provenance")
    if provenance is not None:
        if "curated_source_label" not in source_types:
            raise InputError(
                f"expert feedback curated_source_label_provenance requires curated_source_label: {source_id}"
            )
        if not isinstance(provenance, Mapping):
            raise InputError(f"expert feedback curated_source_label_provenance must be an object: {source_id}")
        provenance_fields = {"source_snapshot_digest", "annotation_id"}
        unknown = sorted(set(provenance) - provenance_fields)
        missing = sorted(provenance_fields - set(provenance))
        if missing or unknown:
            detail = ", ".join([*(f"missing {item}" for item in missing), *(f"unknown {item}" for item in unknown)])
            raise InputError(
                f"expert feedback curated_source_label_provenance fields are invalid: {source_id} ({detail})"
            )
        policy["curated_source_label_provenance"] = {
            "source_snapshot_digest": require_digest(
                provenance.get("source_snapshot_digest"),
                f"expert feedback curated_source_label source_snapshot_digest {source_id}",
            ),
            "annotation_id": _typed_policy_id(
                provenance.get("annotation_id"), f"expert feedback curated_source_label annotation_id {source_id}"
            ),
        }
    return policy


def _feedback_content(value: Mapping[str, Any], source_id: str, *, leaf_id: str | None = None) -> dict[str, Any]:
    unknown = sorted(set(value) - _FEEDBACK_CONTENT_FIELDS)
    if unknown:
        raise InputError(f"expert feedback content has unknown field(s): {source_id} ({', '.join(unknown)})")
    if "claims" not in value:
        raise InputError(f"expert feedback content is missing claims: {source_id}")
    content: dict[str, Any] = {"claims": _feedback_claims(value["claims"], source_id)}
    question_family_policy: dict[str, Any] | None = None
    if "question_family_policy" in value:
        question_family_policy = _question_family_policy(value["question_family_policy"], source_id)
        content["question_family_policy"] = question_family_policy
    if "gt_policy" in value:
        content["gt_policy"] = _gt_policy(
            value["gt_policy"], source_id, question_family_policy=question_family_policy
        )
    medical_fields = _MEDICAL_STRUCTURE_FIELDS.intersection(value)
    if medical_fields:
        if leaf_id is None or _leaf_parts(leaf_id)[0] != "medical":
            raise InputError(f"expert feedback medical structure is only valid for a medical leaf: {source_id}")
        missing = sorted(_MEDICAL_STRUCTURE_FIELDS - set(value))
        if missing:
            raise InputError(
                f"expert feedback medical structure is incomplete: {source_id} (missing {', '.join(missing)})"
            )
        if value.get("subdomain_semantics") != "question_type_only":
            raise InputError(f"expert feedback subdomain_semantics must be question_type_only: {source_id}")
        content["subdomain_semantics"] = "question_type_only"
        content["asset_facets_schema"] = _asset_facets_schema(value["asset_facets_schema"], source_id)
        content["medical_visibility_policy"] = _medical_visibility_policy(
            value["medical_visibility_policy"], source_id
        )
    return content


def _structured_source(kind: str, source_id: str, text: str) -> dict[str, Any] | None:
    """Parse an explicit local curation source without interpreting free-form prose."""
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InputError(f"structured knowledge source is not valid YAML: {source_id}") from exc
    if not isinstance(value, Mapping) or value.get("kind") not in set(_STRUCTURED_SOURCE_KINDS.values()):
        if kind == "feedback":
            raise InputError(f"expert feedback source must be structured YAML with a recognized kind: {source_id}")
        return None
    expected_kind = _STRUCTURED_SOURCE_KINDS[kind]
    if value.get("kind") != expected_kind:
        raise InputError(f"knowledge source {source_id} kind does not match {kind}")
    if value.get("schema_version") != 1 or value.get("id") != source_id:
        raise InputError(f"structured knowledge source identity is invalid: {source_id}")
    leaf_id = require_nonempty_string(value.get("leaf_id"), f"{source_id} leaf_id")
    _leaf_parts(leaf_id)
    allowed_statuses = {"accepted"} if kind == "feedback" else {"local_reference", "draft_reference"}
    if value.get("source_status") not in allowed_statuses:
        raise InputError(f"structured knowledge source status is invalid: {source_id}")
    if value.get("production_approval") is not False:
        raise InputError(f"structured knowledge source cannot claim production approval: {source_id}")
    content = value.get("content")
    if not isinstance(content, Mapping):
        raise InputError(f"structured knowledge source content is missing: {source_id}")
    if kind == "feedback":
        unknown = sorted(set(value) - _FEEDBACK_SOURCE_FIELDS)
        if unknown:
            raise InputError(f"expert feedback source has unknown field(s): {source_id} ({', '.join(unknown)})")
        normalized_feedback_content = _feedback_content(content, source_id, leaf_id=leaf_id)
    else:
        required = _STRUCTURED_EXPERT_FIELDS if kind == "expert" else _STRUCTURED_TAXONOMY_FIELDS
        missing = sorted(required - set(content))
        if missing:
            raise InputError(f"structured knowledge source {source_id} missing content fields: {', '.join(missing)}")
    references = value.get("reference_sources", [])
    if not isinstance(references, list):
        raise InputError(f"structured knowledge source reference_sources is invalid: {source_id}")
    for reference in references:
        if not isinstance(reference, Mapping):
            raise InputError(f"structured knowledge source reference is invalid: {source_id}")
        require_nonempty_string(reference.get("repo_path"), f"{source_id} reference repo_path")
        require_nonempty_string(reference.get("use"), f"{source_id} reference use")
        require_digest(reference.get("content_digest"), f"{source_id} reference content_digest")
    fixture_reference = value.get("fixture_reference")
    if fixture_reference is not None:
        if not isinstance(fixture_reference, Mapping):
            raise InputError(f"structured knowledge fixture reference is invalid: {source_id}")
        require_nonempty_string(fixture_reference.get("id"), f"{source_id} fixture id")
        require_digest(fixture_reference.get("fixture_digest"), f"{source_id} fixture digest")
        if fixture_reference.get("production_approval") is not False:
            raise InputError(f"knowledge fixture cannot claim production approval: {source_id}")
        if fixture_reference.get("use") != "structure_only":
            raise InputError(f"knowledge fixture use must be structure_only: {source_id}")
    normalized = deepcopy(dict(value))
    if kind == "feedback":
        normalized["content"] = normalized_feedback_content
    return normalized


def _load_manifest(paths: WorkspacePaths) -> dict[str, Any]:
    path = paths.knowledge / "source-manifest.yaml"
    if not path.exists():
        raise GatePendingError("knowledge source manifest is missing; run qa-gen knowledge sync")
    value = read_yaml(path)
    if not isinstance(value, Mapping):
        raise InputError("knowledge source manifest is not an object")
    return verify_envelope(value, kind="knowledge_source_manifest")


def _load_seed_state(paths: WorkspacePaths) -> dict[str, Any]:
    path = paths.catalog / "current.json"
    if not path.exists():
        raise GatePendingError("seed sync state is missing; run qa-gen seed sync")
    return validate_seed_index(read_json(path))


def _leaf_parts(leaf: str) -> tuple[str, str]:
    parts = require_nonempty_string(leaf, "leaf").split(".", 1)
    if len(parts) != 2 or not all(parts):
        raise InputError("leaf must have form <domain>.<subdomain>")
    domain, subdomain = parts
    safe_component(domain, "leaf domain")
    safe_component(subdomain, "leaf subdomain")
    return domain, subdomain


def _load_lesson_set(paths: WorkspacePaths, leaf: str) -> dict[str, Any]:
    domain, subdomain = _leaf_parts(leaf)
    path = paths.knowledge / "seed-lessons" / domain / f"{subdomain}.yaml"
    if not path.exists():
        raise GatePendingError(f"seed lesson set is missing for {leaf}")
    value = read_yaml(path)
    if not isinstance(value, Mapping):
        raise InputError(f"seed lesson set is not an object: {path}")
    return verify_envelope(value, kind="seed_lesson_set")


def _resolve_sources(
    manifest: Mapping[str, Any],
    *,
    expert_ids: Iterable[str],
    taxonomy_ids: Iterable[str],
    feedback_ids: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_map = manifest["body"].get("sources")
    if not isinstance(source_map, Mapping):
        raise InputError("knowledge source manifest has no source map")

    def resolve(kind: str, ids: Iterable[str], *, required: bool) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_id in ids:
            source_id = require_nonempty_string(raw_id, f"{kind} source id")
            key = f"{kind}:{source_id}"
            if key in seen:
                continue
            seen.add(key)
            entry = source_map.get(key)
            if not isinstance(entry, Mapping):
                raise GatePendingError(f"missing synchronized {kind} source: {source_id}")
            if entry.get("kind") != kind:
                raise InputError(f"source kind mismatch for {source_id}")
            require_digest(entry.get("content_digest"), f"{key} content_digest")
            resolved.append(dict(entry))
        if required and not resolved:
            raise InputError(f"at least one {kind} source id is required")
        return sorted(resolved, key=lambda entry: entry["source_id"])

    return (
        resolve("expert", expert_ids, required=True),
        resolve("taxonomy", taxonomy_ids, required=True),
        resolve("feedback", feedback_ids, required=False),
    )


def _current_compile_inputs(
    paths: WorkspacePaths,
    *,
    leaf: str,
    expert_ids: Iterable[str],
    taxonomy_ids: Iterable[str],
    feedback_ids: Iterable[str] = (),
) -> dict[str, Any]:
    manifest = _load_manifest(paths)
    experts, taxonomy, feedback = _resolve_sources(
        manifest,
        expert_ids=expert_ids,
        taxonomy_ids=taxonomy_ids,
        feedback_ids=feedback_ids,
    )
    seed_state = _load_seed_state(paths)
    if seed_state["status"] != "eligible" or not seed_state["body"].get("eligible"):
        raise GatePendingError("seed registry is not eligible; resolve source version and rerun seed sync")
    lesson = _load_lesson_set(paths, leaf)
    seed_catalog_digest = seed_state["body"].get("seed_catalog_digest")
    if lesson["status"] != "eligible" or lesson["body"].get("seed_catalog_digest") != seed_catalog_digest:
        raise StaleInputError("seed lesson set does not match the current eligible seed catalog")
    input_digests = {
        "expert_sources": {entry["source_id"]: entry["content_digest"] for entry in experts},
        "taxonomy_sources": {entry["source_id"]: entry["content_digest"] for entry in taxonomy},
        "feedback_sources": {entry["source_id"]: entry["content_digest"] for entry in feedback},
        "seed_lesson_digest": lesson["content_digest"],
        "seed_catalog_digest": seed_catalog_digest,
        "source_manifest_digest": manifest["content_digest"],
        "compiler_digest": _COMPILER_DIGEST,
    }
    return {
        "manifest": manifest,
        "experts": experts,
        "taxonomy": taxonomy,
        "feedback": feedback,
        "seed_state": seed_state,
        "lesson": lesson,
        "input_digests": input_digests,
        "input_digest": digest(input_digests),
    }


def _mark_index_superseded(paths: WorkspacePaths, source_map: Mapping[str, Any]) -> None:
    index_path = paths.knowledge / "index.yaml"
    if not index_path.exists():
        return
    value = read_yaml(index_path)
    if not isinstance(value, Mapping):
        return
    try:
        index = verify_envelope(value, kind="knowledge_index")
    except InputError:
        return
    entries = deepcopy(index["body"].get("entries", {}))
    changed = False
    for entry in entries.values():
        if not isinstance(entry, Mapping) or entry.get("state") != "approved":
            continue
        for key, frozen_digest in entry.get("source_digests", {}).items():
            current = source_map.get(key)
            if not isinstance(current, Mapping) or current.get("content_digest") != frozen_digest:
                entry["state"] = "superseded"
                entry["superseded_reason"] = f"source_changed:{key}"
                changed = True
                break
    if changed:
        body = dict(index["body"])
        body["entries"] = entries
        replacement = make_envelope(
            kind="knowledge_index",
            identifier="current",
            status="contains_superseded_entries",
            body=body,
        )
        atomic_write_yaml(index_path, replacement, private=False)


def sync_knowledge_sources(paths: WorkspacePaths, source_specs: Iterable[str]) -> dict[str, Any]:
    """Synchronize local text exports. Network source strings are rejected."""
    paths.ensure()
    supplied = [_parse_source_spec(spec) for spec in source_specs]
    if not supplied:
        raise InputError("at least one --source is required")
    existing_sources: dict[str, Any] = {}
    manifest_path = paths.knowledge / "source-manifest.yaml"
    if manifest_path.exists():
        existing = _load_manifest(paths)
        raw_existing = existing["body"].get("sources", {})
        if isinstance(raw_existing, Mapping):
            existing_sources = deepcopy(dict(raw_existing))

    for kind, source_id, source_path in supplied:
        text = _normalized_text(source_path)
        content_digest = digest_bytes(text.encode("utf-8"))
        structured_source = _structured_source(kind, source_id, text)
        raw_destination = paths.snapshots / "knowledge" / "raw" / f"{kind}-{source_id}-{_short(content_digest)}{source_path.suffix or '.txt'}"
        atomic_copy_file(source_path, raw_destination, private=True)
        mirror_destination = paths.knowledge / "sources" / kind / f"{source_id}.md"
        atomic_write_text(mirror_destination, text, private=False)
        source_entry = {
            "kind": kind,
            "source_id": source_id,
            "content_digest": content_digest,
            "raw_snapshot": str(raw_destination.relative_to(paths.root)),
            "mirror_path": str(mirror_destination.relative_to(paths.root)),
            "sections": _sections(text),
        }
        if structured_source is not None:
            source_entry["structured_source"] = structured_source
        existing_sources[f"{kind}:{source_id}"] = source_entry

    body = {"sources": dict(sorted(existing_sources.items())), "sync_mode": "local_only"}
    manifest = make_envelope(kind="knowledge_source_manifest", identifier="current", status="synchronized", body=body)
    atomic_write_yaml(manifest_path, manifest, private=False)
    _mark_index_superseded(paths, existing_sources)
    append_event(paths, "knowledge_sync", {"source_manifest_digest": manifest["content_digest"], "source_count": len(existing_sources)})
    return {"source_manifest_path": str(manifest_path), "source_manifest_digest": manifest["content_digest"], "source_count": len(existing_sources)}


def authorize_knowledge_compile(
    paths: WorkspacePaths,
    *,
    leaf: str,
    expert_source_ids: Iterable[str],
    taxonomy_source_ids: Iterable[str],
    feedback_source_ids: Iterable[str] = (),
    owner: str,
    model: str,
    max_tokens: int,
    max_cost_usd: float,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Create the distinct pre-plan authorization for deterministic curation."""
    paths.ensure()
    if max_tokens < 0 or max_cost_usd < 0 or ttl_seconds <= 0:
        raise InputError("max tokens/cost must be non-negative and ttl_seconds must be positive")
    inputs = _current_compile_inputs(
        paths,
        leaf=leaf,
        expert_ids=expert_source_ids,
        taxonomy_ids=taxonomy_source_ids,
        feedback_ids=feedback_source_ids,
    )
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    authorization_seed = {
        "leaf": leaf,
        "input_digest": inputs["input_digest"],
        "owner": owner,
        "issued_at": issued_at.isoformat(),
        "model": model,
    }
    authorization_id = f"knowledge-auth-{_short(digest(authorization_seed))}"
    body = {
        "leaf_id": leaf,
        "input_digests": inputs["input_digests"],
        "input_digest": inputs["input_digest"],
        "expert_source_ids": [entry["source_id"] for entry in inputs["experts"]],
        "taxonomy_source_ids": [entry["source_id"] for entry in inputs["taxonomy"]],
        "feedback_source_ids": [entry["source_id"] for entry in inputs["feedback"]],
        "allowed_workers": ["knowledge_curator"],
        "allowed_models": [require_nonempty_string(model, "model")],
        "max_tokens": max_tokens,
        "max_cost_usd": max_cost_usd,
        "consumption_policy": "single_use",
        "owner": require_nonempty_string(owner, "owner"),
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    authorization = make_envelope(
        kind="knowledge_compile_authorization",
        identifier=authorization_id,
        status="authorized",
        body=body,
    )
    path = paths.receipts / "knowledge" / f"{authorization_id}.authorization.json"
    atomic_write_json(path, authorization, private=True)
    append_event(paths, "knowledge_authorize", {"authorization_digest": authorization["content_digest"], "leaf_id": leaf})
    return {"authorization_path": str(path), "authorization_digest": authorization["content_digest"], "expires_at": body["expires_at"]}


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise InputError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"invalid {field}: {value}") from exc
    if parsed.tzinfo is None:
        raise InputError(f"{field} must include a timezone")
    return parsed


def _validate_authorization(paths: WorkspacePaths, authorization: Mapping[str, Any], *, worker: str, model: str) -> dict[str, Any]:
    auth = verify_envelope(authorization, kind="knowledge_compile_authorization")
    if auth["status"] != "authorized":
        raise InvalidStateError("knowledge compile authorization is not authorized")
    body = auth["body"]
    if body.get("consumption_policy") != "single_use":
        raise InvalidStateError("knowledge compile authorization must declare single_use consumption")
    if datetime.now(timezone.utc) >= _parse_time(body.get("expires_at"), "authorization expires_at"):
        raise StaleInputError("knowledge compile authorization has expired")
    if worker not in body.get("allowed_workers", []):
        raise InputError(f"worker {worker!r} is not authorized")
    if model not in body.get("allowed_models", []):
        raise InputError(f"model {model!r} is not authorized")
    inputs = _current_compile_inputs(
        paths,
        leaf=body.get("leaf_id"),
        expert_ids=body.get("expert_source_ids", []),
        taxonomy_ids=body.get("taxonomy_source_ids", []),
        feedback_ids=body.get("feedback_source_ids", []),
    )
    if body.get("input_digest") != inputs["input_digest"] or body.get("input_digests") != inputs["input_digests"]:
        raise StaleInputError("knowledge compile authorization input digests are stale")
    return {"authorization": auth, "inputs": inputs}


def _authorization_consumption(paths: WorkspacePaths, authorization_digest: str) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for path in (paths.receipts / "knowledge").glob("*.compile-consumption.json"):
        try:
            value = read_json(path)
            if not isinstance(value, Mapping):
                continue
            receipt = verify_envelope(value, kind="knowledge_compile_consumption")
        except (InputError, OSError):
            continue
        if receipt["body"].get("authorization_digest") == authorization_digest:
            matches.append(receipt)
    if len(matches) > 1:
        raise InvalidStateError("knowledge authorization has multiple consumption receipts")
    return matches[0] if matches else None


def _consume_authorization(paths: WorkspacePaths, authorization: Mapping[str, Any], *, worker: str, model: str) -> dict[str, Any]:
    authorization_digest = authorization["content_digest"]
    if _authorization_consumption(paths, authorization_digest) is not None:
        raise InvalidStateError("knowledge compile authorization is single-use and has already been consumed")
    receipt = make_envelope(
        kind="knowledge_compile_consumption",
        identifier=f"knowledge-consumption-{_short(authorization_digest)}",
        status="consumed",
        body={
            "authorization_digest": authorization_digest,
            "input_digest": authorization["body"].get("input_digest"),
            "worker": worker,
            "model": model,
            "consumption_policy": "single_use",
        },
    )
    path = paths.receipts / "knowledge" / (
        f"{_short(authorization_digest)}.{_short(receipt['content_digest'])}.compile-consumption.json"
    )
    atomic_write_json(path, receipt, private=True)
    append_event(
        paths,
        "knowledge_authorization_consumed",
        {"authorization_digest": authorization_digest, "consumption_digest": receipt["content_digest"]},
    )
    return receipt


def _unique(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if isinstance(value, str) and value})


def _selected_structured_source(
    entries: Iterable[Mapping[str, Any]], *, leaf: str, source_kind: str
) -> dict[str, Any] | None:
    selected: list[dict[str, Any]] = []
    for entry in entries:
        value = entry.get("structured_source")
        if value is None:
            continue
        if not isinstance(value, Mapping) or value.get("leaf_id") != leaf:
            raise InputError(f"structured {source_kind} source does not match requested leaf {leaf}")
        selected.append(dict(value))
    if len(selected) > 1:
        raise InputError(f"multiple structured {source_kind} sources are ambiguous for {leaf}")
    return selected[0] if selected else None


def _compiled_feedback_policy(entries: Iterable[Mapping[str, Any]], *, leaf: str) -> dict[str, Any] | None:
    feedback_source = _selected_structured_source(entries, leaf=leaf, source_kind="feedback")
    if feedback_source is None:
        return None
    content = feedback_source.get("content")
    if not isinstance(content, Mapping):
        raise InputError(f"structured feedback source is missing content for {leaf}")
    # Feedback sources are normalized on sync. Revalidate here so a tampered
    # manifest cannot inject prose into an otherwise typed policy.
    normalized = _feedback_content(
        content,
        str(feedback_source.get("id", "feedback")),
        leaf_id=leaf,
    )
    if normalized != dict(content):
        raise InputError(f"structured feedback source is not normalized for {leaf}")
    return normalized


def _seed_lesson_refs(lesson_body: Mapping[str, Any]) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for disposition, field in (("positive", "positive_lessons"), ("anti_pattern", "anti_pattern_lessons")):
        lessons = lesson_body.get(field, [])
        if not isinstance(lessons, list):
            continue
        for lesson in lessons:
            if not isinstance(lesson, Mapping):
                continue
            lesson_id = lesson.get("lesson_id")
            origin_status = lesson.get("origin_status")
            if isinstance(lesson_id, str) and lesson_id and isinstance(origin_status, str) and origin_status:
                references.append(
                    {
                        "lesson_id": lesson_id,
                        "origin_status": origin_status,
                        "disposition": disposition,
                    }
                )
    return sorted(references, key=lambda item: (item["disposition"], item["lesson_id"]))


def _draft_body(inputs: Mapping[str, Any], authorization: Mapping[str, Any]) -> dict[str, Any]:
    leaf = authorization["body"]["leaf_id"]
    lesson_body = inputs["lesson"]["body"]
    feedback_policy = _compiled_feedback_policy(inputs["feedback"], leaf=leaf)
    visual_skills = _unique(
        skill
        for lesson in lesson_body.get("positive_lessons", []) + lesson_body.get("anti_pattern_lessons", [])
        for skill in lesson.get("visual_skills", [])
    )
    viewer_affordances = _unique(
        affordance
        for lesson in lesson_body.get("positive_lessons", []) + lesson_body.get("anti_pattern_lessons", [])
        for affordance in lesson.get("viewer_affordances", [])
    )
    taxonomy_targets = [
        f"{entry['source_id']}:{section['section_id']}"
        for entry in inputs["taxonomy"]
        for section in entry.get("sections", [])[:1]
    ]
    anti_patterns = _unique(
        pattern
        for lesson in lesson_body.get("anti_pattern_lessons", [])
        for pattern in lesson.get("anti_pattern_classes", [])
    )
    body = {
        "id": leaf,
        "status": "draft",
        "provenance": {
            "expert_source_refs": [
                {"source_id": entry["source_id"], "content_digest": entry["content_digest"], "sections": entry.get("sections", [])}
                for entry in inputs["experts"]
            ],
            "visual_taxonomy_refs": [
                {"source_id": entry["source_id"], "content_digest": entry["content_digest"], "sections": entry.get("sections", [])}
                for entry in inputs["taxonomy"]
            ],
            "expert_feedback_refs": [
                {"source_id": entry["source_id"], "content_digest": entry["content_digest"]}
                for entry in inputs["feedback"]
            ],
            "seed_lesson_ref": inputs["lesson"]["content_digest"],
            "seed_lesson_refs": _seed_lesson_refs(lesson_body),
            "compile_authorization_ref": authorization["content_digest"],
            "compiler_digest": _COMPILER_DIGEST,
            "input_digest": inputs["input_digest"],
        },
        "scientific_scope": {
            "included": ["Domain-expert completion required before approval."],
            "excluded": ["Live-source lookup", "raw-asset analysis", "unrestricted computation"],
        },
        "visual_target_types": taxonomy_targets or visual_skills or ["controlled-viewer-observation"],
        "scenario_cards": [
            {
                "id": f"{leaf}-controlled-viewer-evidence",
                "observable_targets": taxonomy_targets or visual_skills or ["controlled-viewer-observation"],
                "valid_question_patterns": ["Require evidence obtained through the controlled viewer."],
                "invalid_question_patterns": ["Answer from metadata, raw assets, network search, or arbitrary computation."],
            }
        ],
        "allowed_inference": ["Direct observations through the controlled viewer."],
        "forbidden_shortcuts": sorted(_REQUIRED_SHORTCUTS),
        "evidence_requirements": {
            "required_viewer_operations": ["controlled_viewer_observation"],
            "required_evidence": ["viewer_trace_or_annotation"],
            "allowed_truth_modes": ["visual_annotation", "expert_visual_judgment", "viewer_native_measurement"],
        },
        "requirements": {
            "asset_requirements": ["asset selected by compiled leaf policy"],
            "viewer_requirements": viewer_affordances or ["controlled_viewer"],
            "answer_modes": [lesson.get("answer_form") for lesson in lesson_body.get("positive_lessons", []) if lesson.get("answer_form")],
        },
        "common_failure_modes": anti_patterns,
        "rubric_extensions": [],
        "review": {"decision": "pending", "reviewer": None, "reviewed_at": None, "decision_digest": None},
    }
    expert_source = _selected_structured_source(inputs["experts"], leaf=leaf, source_kind="expert")
    taxonomy_source = _selected_structured_source(inputs["taxonomy"], leaf=leaf, source_kind="taxonomy")
    if expert_source is not None:
        expert_content = expert_source["content"]
        for field in _STRUCTURED_EXPERT_FIELDS:
            body[field] = deepcopy(expert_content[field])
        fixture_reference = expert_source.get("fixture_reference")
        if isinstance(fixture_reference, Mapping):
            body["provenance"]["fixture_reference"] = {
                "id": fixture_reference["id"],
                "fixture_digest": fixture_reference["fixture_digest"],
                "use": "structure_only",
                "production_approval": False,
            }
    if taxonomy_source is not None:
        taxonomy_content = taxonomy_source["content"]
        for field in _STRUCTURED_TAXONOMY_FIELDS:
            body[field] = deepcopy(taxonomy_content[field])
        body["forbidden_shortcuts"] = sorted(
            _REQUIRED_SHORTCUTS | set(body["forbidden_shortcuts"])
        )
    if feedback_policy is not None:
        body["feedback_policy"] = feedback_policy
        body["provenance"]["feedback_policy_digest"] = digest(feedback_policy)
    alias_lock = lesson_body.get("taxonomy_alias_lock")
    if isinstance(alias_lock, Mapping):
        body["provenance"]["taxonomy_alias_lock"] = deepcopy(dict(alias_lock))
    return body


def _assert_review_artifact_deidentified(value: Any, *, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in _REVIEW_SAFE_FORBIDDEN_KEYS:
                raise InputError(f"knowledge review artifact contains forbidden field: {path}.{key_text}")
            _assert_review_artifact_deidentified(child, path=f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_review_artifact_deidentified(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _REVIEW_SAFE_FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                raise InputError(f"knowledge review artifact contains a private identifier or path at {path}")


def _review_source_summary(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for entry in entries:
        structured = entry.get("structured_source")
        source_status = "unstructured_local_reference"
        production_approval = False
        if isinstance(structured, Mapping):
            source_status = structured.get("source_status", source_status)
            production_approval = structured.get("production_approval") is True
        summaries.append(
            {
                "source_id": entry["source_id"],
                "content_digest": entry["content_digest"],
                "source_status": source_status,
                "production_approval": production_approval,
            }
        )
    return sorted(summaries, key=lambda item: item["source_id"])


def _knowledge_review_packet(draft: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    body = draft["body"]
    review_fields = (
        "scientific_scope",
        "visual_target_types",
        "scenario_cards",
        "allowed_inference",
        "forbidden_shortcuts",
        "evidence_requirements",
        "requirements",
        "common_failure_modes",
        "rubric_extensions",
        "feedback_policy",
    )
    packet_body = {
        "artifact_type": "KnowledgeReviewPacket",
        "leaf_id": body["id"],
        "draft_digest": draft["content_digest"],
        "input_digest": body["provenance"]["input_digest"],
        "compile_authorization_ref": body["provenance"]["compile_authorization_ref"],
        "review_status": "pending_human_approval",
        "approval_gate": {
            "required": True,
            "satisfied": False,
            "decision_kind": "KnowledgeReviewDecision",
            "allowed_decisions": ["approved", "revise", "rejected", "quarantined"],
        },
        "source_boundaries": {
            "expert_sources": _review_source_summary(inputs["experts"]),
            "visual_taxonomy_sources": _review_source_summary(inputs["taxonomy"]),
            "expert_feedback_sources": _review_source_summary(inputs["feedback"]),
            "seed_lesson_ref": body["provenance"]["seed_lesson_ref"],
            "seed_lesson_refs": deepcopy(body["provenance"].get("seed_lesson_refs", [])),
            "fixture_reference": deepcopy(body["provenance"].get("fixture_reference")),
        },
        "review_sections": {field: deepcopy(body[field]) for field in review_fields if field in body},
        "review_checks": [
            {"field": field, "status": "pending_domain_expert_review"}
            for field in review_fields
            if field in body
        ],
    }
    _assert_review_artifact_deidentified(packet_body)
    return make_envelope(
        kind="knowledge_review_packet",
        identifier=f"{body['id']}-review-{_short(draft['content_digest'])}",
        status="pending_human_approval",
        body=packet_body,
        producer="qagen/0.1.0:knowledge_curator_deterministic",
    )


def compile_knowledge_draft(
    paths: WorkspacePaths,
    *,
    authorization_path: str | Path,
    worker: str = "knowledge_curator",
    model: str = "deterministic-local",
) -> dict[str, Any]:
    """Run the deterministic adapter. It cannot approve or index knowledge."""
    paths.ensure()
    started = time.perf_counter()
    authorization = read_json(require_within(paths.receipts / "knowledge", authorization_path, "knowledge authorization"))
    if not isinstance(authorization, Mapping):
        raise InputError("knowledge authorization is not an object")
    validated = _validate_authorization(paths, authorization, worker=worker, model=model)
    _consume_authorization(paths, validated["authorization"], worker=worker, model=model)
    authorization_body = validated["authorization"]["body"]
    reservation = reserve_usage(
        paths,
        worker=worker,
        model=model,
        input_contract_digest=validated["inputs"]["input_digest"],
        max_tokens=int(authorization_body["max_tokens"]),
        max_cost_usd=float(authorization_body["max_cost_usd"]),
    )
    try:
        body = _draft_body(validated["inputs"], validated["authorization"])
        _validate_snapshot_body(body)
        _assert_review_artifact_deidentified(body)
        draft = make_envelope(
            kind="subdomain_knowledge_snapshot",
            identifier=body["id"],
            status="draft",
            body=body,
            producer="qagen/0.1.0:knowledge_curator_deterministic",
        )
        path = paths.knowledge_drafts / f"{body['id'].replace('.', '__')}-{_short(draft['content_digest'])}.draft.json"
        review_packet = _knowledge_review_packet(draft, validated["inputs"])
        review_packet_path = paths.knowledge_drafts / (
            f"{body['id'].replace('.', '__')}-{_short(draft['content_digest'])}.review-packet.json"
        )
        atomic_write_json(path, draft, private=True)
        try:
            atomic_write_json(review_packet_path, review_packet, private=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    except Exception:
        settle_usage(
            paths,
            reservation=reservation,
            token_in=0,
            token_out=0,
            settled_cost_usd=0.0,
            latency_ms=(time.perf_counter() - started) * 1000,
            outcome="failed",
        )
        raise
    settle_usage(
        paths,
        reservation=reservation,
        token_in=0,
        token_out=0,
        settled_cost_usd=0.0,
        latency_ms=(time.perf_counter() - started) * 1000,
        outcome="draft_created",
    )
    append_event(
        paths,
        "knowledge_compile",
        {
            "draft_digest": draft["content_digest"],
            "review_packet_digest": review_packet["content_digest"],
            "authorization_digest": validated["authorization"]["content_digest"],
        },
    )
    return {
        "draft_path": str(path),
        "draft_digest": draft["content_digest"],
        "review_packet_path": str(review_packet_path),
        "review_packet_digest": review_packet["content_digest"],
        "leaf_id": body["id"],
    }


def _validate_snapshot_body(body: Mapping[str, Any]) -> None:
    required = {
        "id",
        "status",
        "provenance",
        "scientific_scope",
        "visual_target_types",
        "scenario_cards",
        "allowed_inference",
        "forbidden_shortcuts",
        "evidence_requirements",
        "requirements",
        "common_failure_modes",
        "rubric_extensions",
        "review",
    }
    missing = sorted(required - set(body))
    if missing:
        raise InputError(f"knowledge snapshot missing fields: {', '.join(missing)}")
    if not isinstance(body["provenance"], Mapping) or not isinstance(body["scientific_scope"], Mapping):
        raise InputError("knowledge snapshot provenance and scientific_scope must be mappings")
    if not isinstance(body["visual_target_types"], list) or not body["visual_target_types"]:
        raise InputError("knowledge snapshot visual_target_types must be non-empty")
    if not isinstance(body["scenario_cards"], list) or not body["scenario_cards"]:
        raise InputError("knowledge snapshot scenario_cards must be non-empty")
    if not _REQUIRED_SHORTCUTS.issubset(set(body["forbidden_shortcuts"])):
        raise InputError("knowledge snapshot does not forbid all required shortcuts")
    evidence = body["evidence_requirements"]
    if not isinstance(evidence, Mapping):
        raise InputError("knowledge snapshot evidence_requirements must be a mapping")
    modes = set(evidence.get("allowed_truth_modes", []))
    if not modes or not modes.issubset(_ALLOWED_TRUTH_MODES):
        raise InputError("knowledge snapshot has invalid allowed truth modes")
    provenance = body["provenance"]
    for key in ("seed_lesson_ref", "compile_authorization_ref", "compiler_digest", "input_digest"):
        require_digest(provenance.get(key), f"provenance.{key}")
    feedback_policy = body.get("feedback_policy")
    if feedback_policy is not None:
        if not isinstance(feedback_policy, Mapping):
            raise InputError("knowledge snapshot feedback_policy must be a mapping")
        normalized_feedback_policy = _feedback_content(
            feedback_policy,
            "compiled feedback policy",
            leaf_id=body["id"],
        )
        if normalized_feedback_policy != dict(feedback_policy):
            raise InputError("knowledge snapshot feedback_policy is not normalized")
        if provenance.get("feedback_policy_digest") != digest(normalized_feedback_policy):
            raise InputError("knowledge snapshot feedback policy digest is invalid")
        feedback_refs = provenance.get("expert_feedback_refs")
        if not isinstance(feedback_refs, list) or not feedback_refs:
            raise InputError("knowledge snapshot feedback policy has no feedback source provenance")
        for index, ref in enumerate(feedback_refs):
            if not isinstance(ref, Mapping):
                raise InputError(f"knowledge snapshot feedback source provenance is invalid at index {index}")
            require_nonempty_string(ref.get("source_id"), f"feedback source provenance {index} source_id")
            require_digest(ref.get("content_digest"), f"feedback source provenance {index} content_digest")
        gt_policy = normalized_feedback_policy.get("gt_policy")
        if isinstance(gt_policy, Mapping):
            gt_source_types = set(gt_policy["gt_source_types"])
            if "measurement_receipt" in gt_source_types and "viewer_native_measurement" not in modes:
                raise InputError("knowledge snapshot measurement_receipt is not supported by evidence requirements")
            if "expert_annotation" in gt_source_types and not {
                "visual_annotation",
                "expert_visual_judgment",
            }.intersection(modes):
                raise InputError("knowledge snapshot expert_annotation is not supported by evidence requirements")


def _write_index_entry(
    paths: WorkspacePaths,
    *,
    frozen: Mapping[str, Any],
    frozen_path: Path,
    draft: Mapping[str, Any],
    decision: Mapping[str, Any],
    decision_path: Path,
    inputs: Mapping[str, Any],
) -> tuple[dict[str, Any], Path]:
    index_path = paths.knowledge / "index.yaml"
    entries: dict[str, Any] = {}
    if index_path.exists():
        existing_value = read_yaml(index_path)
        if not isinstance(existing_value, Mapping):
            raise InputError("knowledge index is not an object")
        existing = validate_knowledge_index(existing_value)
        raw_entries = existing["body"].get("entries", {})
        if isinstance(raw_entries, Mapping):
            entries = deepcopy(dict(raw_entries))
    leaf = frozen["body"]["id"]
    source_digests = {
        **{f"expert:{entry['source_id']}": entry["content_digest"] for entry in inputs["experts"]},
        **{f"taxonomy:{entry['source_id']}": entry["content_digest"] for entry in inputs["taxonomy"]},
        **{f"feedback:{entry['source_id']}": entry["content_digest"] for entry in inputs["feedback"]},
    }
    entries[leaf] = {
        "state": "approved",
        "snapshot_digest": frozen["content_digest"],
        "snapshot_path": str(frozen_path.relative_to(paths.root)),
        "review_digest": decision["content_digest"],
        "review_path": str(decision_path.relative_to(paths.root)),
        "draft_digest": draft["content_digest"],
        "input_digest": frozen["body"]["provenance"]["input_digest"],
        "seed_catalog_digest": inputs["seed_state"]["body"]["seed_catalog_digest"],
        "source_digests": source_digests,
        "compiler_digest": _COMPILER_DIGEST,
        "local_only": bool(inputs["seed_state"]["body"].get("local_only")),
        "production_eligible": bool(
            inputs["seed_state"]["body"].get("production_eligible")
        ),
    }
    seed_index = inputs["seed_state"]
    body = {
        "mode": "workspace_registry",
        "seed_index_digest": seed_index["content_digest"],
        "seed_catalog_digest": seed_index["body"]["seed_catalog_digest"],
        "entries": dict(sorted(entries.items())),
    }
    provisional = make_envelope(kind="knowledge_index", identifier="knowledge-index", status="ready", body=body)
    index = make_envelope(
        kind="knowledge_index",
        identifier=f"knowledge-index-{_short(provisional['content_digest'])}",
        status="ready",
        body=body,
    )
    validate_knowledge_index(index)
    frozen_index_path = paths.knowledge / "indexes" / f"{_short(index['content_digest'])}.knowledge-index.yaml"
    atomic_write_yaml(frozen_index_path, index, private=False)
    atomic_write_yaml(index_path, index, private=False)
    return index, frozen_index_path


def _prior_draft_reviews(paths: WorkspacePaths, draft_digest: str) -> list[dict[str, Any]]:
    """Find immutable decisions already bound to one exact draft digest."""
    decisions: list[dict[str, Any]] = []
    for candidate in (paths.receipts / "knowledge").glob("*.review.json"):
        try:
            value = read_json(candidate)
            if not isinstance(value, Mapping):
                continue
            receipt = verify_envelope(value, kind="knowledge_review_decision")
        except (InputError, OSError):
            continue
        bound = receipt["body"].get("bound_digests")
        if isinstance(bound, Mapping) and bound.get("draft_digest") == draft_digest:
            decisions.append(receipt)
    return decisions


def approve_knowledge_snapshot(
    paths: WorkspacePaths,
    *,
    draft_path: str | Path,
    reviewer: str,
    decision: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Apply the human expert decision; only approved drafts are frozen/indexed."""
    paths.ensure()
    if decision not in {"approved", "revise", "rejected", "quarantined"}:
        raise InputError("knowledge decision must be approved, revise, rejected, or quarantined")
    if decision in {"revise", "rejected"} and not rationale.strip():
        raise InputError(f"knowledge decision {decision} requires a non-empty rationale")
    resolved_draft_path = require_within(paths.knowledge_drafts, draft_path, "knowledge draft")
    draft_value = read_json(resolved_draft_path)
    if not isinstance(draft_value, Mapping):
        raise InputError("knowledge draft is not an object")
    initial = verify_envelope(draft_value, kind="subdomain_knowledge_snapshot")
    draft_digest = initial["content_digest"]
    review_directory = paths.receipts / "knowledge"
    with _knowledge_object_lock(review_directory, "draft-review", draft_digest):
        current_value = read_json(resolved_draft_path)
        if not isinstance(current_value, Mapping):
            raise InputError("knowledge draft is not an object")
        draft = verify_envelope(current_value, kind="subdomain_knowledge_snapshot")
        if draft["content_digest"] != draft_digest:
            raise InvalidStateError("knowledge draft changed before its review decision")
        if draft["status"] != "draft":
            raise InvalidStateError("only a draft knowledge snapshot can be reviewed")
        prior_reviews = _prior_draft_reviews(paths, draft_digest)
        if prior_reviews:
            previous = sorted({str(receipt["body"].get("decision")) for receipt in prior_reviews})
            raise InvalidStateError(
                "knowledge draft already has immutable review decision(s): "
                + ", ".join(previous)
                + "; create a new revision draft before another review"
            )
        _validate_snapshot_body(draft["body"])
        provenance = draft["body"]["provenance"]
        matching_authorization: dict[str, Any] | None = None
        for candidate in review_directory.glob("*.authorization.json"):
            value = read_json(candidate)
            if isinstance(value, Mapping):
                try:
                    envelope = verify_envelope(value, kind="knowledge_compile_authorization")
                except InputError:
                    continue
                if envelope["content_digest"] == provenance["compile_authorization_ref"]:
                    matching_authorization = envelope
                    break
        if matching_authorization is None:
            raise StaleInputError("knowledge draft authorization receipt is missing")
        validated = _validate_authorization(
            paths,
            matching_authorization,
            worker="knowledge_curator",
            model=matching_authorization["body"]["allowed_models"][0],
        )
        if provenance["input_digest"] != validated["inputs"]["input_digest"]:
            raise StaleInputError("knowledge draft inputs are stale")
        review = make_decision(
            kind="knowledge_review_decision",
            identifier=f"knowledge-review-{_short(draft_digest)}",
            decision=decision,
            actor_id=reviewer,
            actor_role="domain_expert",
            bound_digests={
                "draft_digest": draft_digest,
                "input_digest": provenance["input_digest"],
                "compile_authorization_ref": provenance["compile_authorization_ref"],
            },
            rationale=rationale,
            extra={
                "snapshot_digest": draft_digest,
                "input_digest": provenance["input_digest"],
                "compile_authorization_ref": provenance["compile_authorization_ref"],
            },
        )
        review_path = review_directory / (
            f"{_short(draft_digest)}.{decision}.{_short(review['content_digest'])}.review.json"
        )
        result = {"review_path": str(review_path), "review_digest": review["content_digest"], "decision": decision}

        if decision != "approved":
            created = False
            try:
                _create_knowledge_receipt(review_path, review)
                created = True
                append_event(paths, "knowledge_review", {"draft_digest": draft_digest, "decision": decision})
            except FileExistsError as exc:
                raise InvalidStateError("knowledge review receipt already exists") from exc
            except Exception:
                if created:
                    review_path.unlink(missing_ok=True)
                raise
            return result

        frozen_body = deepcopy(draft["body"])
        frozen_body["status"] = "approved"
        frozen_body["provenance"]["reviewed_draft_digest"] = draft_digest
        frozen_body["review"] = {
            "decision": "approved",
            "reviewer": reviewer,
            "reviewed_at": review["body"]["decided_at"],
            "decision_digest": review["content_digest"],
        }
        frozen = make_envelope(
            kind="subdomain_knowledge_snapshot",
            identifier=frozen_body["id"],
            status="approved",
            body=frozen_body,
            producer="qagen/0.1.0:knowledge_freezer",
        )
        domain, subdomain = _leaf_parts(frozen_body["id"])
        frozen_path = paths.knowledge / "compiled" / domain / (
            f"{subdomain}-{_short(frozen['content_digest'])}.snapshot.yaml"
        )
        current_index_path = paths.knowledge / "index.yaml"
        index_directory = paths.knowledge / "indexes"
        with _knowledge_object_lock(review_directory, "knowledge-index", "current"):
            previous_frozen = frozen_path.read_bytes() if frozen_path.exists() else None
            previous_index = current_index_path.read_bytes() if current_index_path.exists() else None
            previous_frozen_indexes = {
                path: path.read_bytes() for path in index_directory.glob("*.knowledge-index.yaml")
            }
            created = False
            try:
                _create_knowledge_receipt(review_path, review)
                created = True
                atomic_write_yaml(frozen_path, frozen, private=False)
                index, index_path = _write_index_entry(
                    paths,
                    frozen=frozen,
                    frozen_path=frozen_path,
                    draft=draft,
                    decision=review,
                    decision_path=review_path,
                    inputs=validated["inputs"],
                )
                append_event(
                    paths,
                    "knowledge_freeze",
                    {"snapshot_digest": frozen["content_digest"], "review_digest": review["content_digest"]},
                )
            except FileExistsError as exc:
                raise InvalidStateError("knowledge review receipt already exists") from exc
            except Exception:
                for path in set(index_directory.glob("*.knowledge-index.yaml")) - set(previous_frozen_indexes):
                    path.unlink(missing_ok=True)
                for path, content in previous_frozen_indexes.items():
                    _restore_text_file(path, content, private=False)
                _restore_text_file(current_index_path, previous_index, private=False)
                _restore_text_file(frozen_path, previous_frozen, private=False)
                if created:
                    review_path.unlink(missing_ok=True)
                raise
        result.update(
            {
                "snapshot_path": str(frozen_path),
                "snapshot_digest": frozen["content_digest"],
                "index_path": str(index_path),
                "index_digest": index["content_digest"],
            }
        )
        return result


# Programmatic registry facade -------------------------------------------------
#
# The CLI functions above operate on a workspace layout and on already-synced
# inputs. The configuration compiler needs a compact typed contract while it
# constructs those inputs. Keep this API separate so it cannot bypass the
# workspace review gate.


class KnowledgeRegistryError(RuntimeError):
    """Base error for the programmatic knowledge registry facade."""


class KnowledgeAuthorizationError(KnowledgeRegistryError):
    """Compile authorization is expired, malformed, stale, or over budget."""


class KnowledgeReviewBindingError(KnowledgeRegistryError):
    """A human decision is not bound to the exact compiled draft."""


class KnowledgeTransitionError(KnowledgeRegistryError):
    """A caller attempted to change knowledge state outside a review decision."""


@dataclass(frozen=True)
class KnowledgeCompileAuthorization:
    """A bounded, pre-plan grant for one knowledge compilation request."""

    digest: str
    request_digest: str
    owner: str
    allowed_workers: frozenset[str]
    allowed_models: frozenset[str]
    max_budget_usd: float
    expires_at: datetime


@dataclass(frozen=True)
class KnowledgeDraft:
    """A draft snapshot; it is not eligible for index or release use."""

    identifier: str
    status: str
    snapshot_digest: str
    input_digest: str
    compile_authorization_ref: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """A review-decision result, frozen only when its status is approved."""

    identifier: str
    status: str
    snapshot_digest: str
    input_digest: str
    compile_authorization_ref: str
    review_digest: str
    payload: dict[str, Any]


_API_REQUIRED_REQUEST_FIELDS = {
    "id",
    "expert_source_refs",
    "visual_taxonomy_ref",
    "seed_lesson_ref",
    "schema_digest",
    "compiler_digest",
}
_API_REQUIRED_DRAFT_FIELDS = {
    "scientific_scope",
    "visual_target_types",
    "scenario_cards",
    "allowed_inference",
    "forbidden_shortcuts",
    "evidence_requirements",
    "requirements",
    "common_failure_modes",
    "rubric_extensions",
}
_API_REVIEW_DECISIONS = {"approved", "revise", "rejected", "quarantined"}


def _api_require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeAuthorizationError(f"{field} must be an object")
    return value


def _api_request_digest(request: Mapping[str, Any]) -> str:
    missing = sorted(_API_REQUIRED_REQUEST_FIELDS - set(request))
    if missing:
        raise KnowledgeAuthorizationError(
            f"knowledge compile request missing fields: {', '.join(missing)}"
        )
    identifier = request.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise KnowledgeAuthorizationError("knowledge compile request id is required")
    return canonical_sha256(dict(request))


def _api_validate_draft_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(_API_REQUIRED_DRAFT_FIELDS - set(payload))
    if missing:
        raise KnowledgeAuthorizationError(
            f"knowledge draft payload missing fields: {', '.join(missing)}"
        )
    if not isinstance(payload["scientific_scope"], Mapping):
        raise KnowledgeAuthorizationError("knowledge draft scientific_scope must be an object")
    for field in (
        "visual_target_types",
        "scenario_cards",
        "allowed_inference",
        "forbidden_shortcuts",
        "common_failure_modes",
        "rubric_extensions",
    ):
        if not isinstance(payload[field], list):
            raise KnowledgeAuthorizationError(f"knowledge draft {field} must be a list")
    for field in ("evidence_requirements", "requirements"):
        if not isinstance(payload[field], Mapping):
            raise KnowledgeAuthorizationError(f"knowledge draft {field} must be an object")
    return dict(payload)


class KnowledgeRegistry:
    """Typed authorization, draft, and human-review lifecycle for knowledge."""

    def __init__(self, *, private_root: str | Path, knowledge_root: str | Path) -> None:
        self.private_root = Path(private_root).expanduser().resolve()
        self.knowledge_root = Path(knowledge_root).expanduser().resolve()
        self.private_root.mkdir(parents=True, exist_ok=True)
        self.knowledge_root.mkdir(parents=True, exist_ok=True)

    def authorize(
        self,
        request: Mapping[str, Any],
        *,
        owner: str,
        allowed_workers: set[str] | frozenset[str],
        allowed_models: set[str] | frozenset[str],
        max_budget_usd: float,
        expires_at: datetime,
    ) -> KnowledgeCompileAuthorization:
        request_map = _api_require_mapping(request, "knowledge compile request")
        request_digest = _api_request_digest(request_map)
        if not isinstance(owner, str) or not owner.strip():
            raise KnowledgeAuthorizationError("knowledge authorization owner is required")
        workers = frozenset(worker for worker in allowed_workers if isinstance(worker, str) and worker)
        models = frozenset(model for model in allowed_models if isinstance(model, str) and model)
        if not workers or not models:
            raise KnowledgeAuthorizationError("knowledge authorization needs worker and model allowlists")
        if not isinstance(max_budget_usd, (int, float)) or isinstance(max_budget_usd, bool) or max_budget_usd < 0:
            raise KnowledgeAuthorizationError("knowledge authorization max_budget_usd must be non-negative")
        if not isinstance(expires_at, datetime) or expires_at.tzinfo is None:
            raise KnowledgeAuthorizationError("knowledge authorization expiry must be timezone-aware")
        body = {
            "request_digest": request_digest,
            "owner": owner.strip(),
            "allowed_workers": sorted(workers),
            "allowed_models": sorted(models),
            "max_budget_usd": float(max_budget_usd),
            "expires_at": expires_at.isoformat(),
        }
        authorization = KnowledgeCompileAuthorization(
            digest=canonical_sha256(body),
            request_digest=request_digest,
            owner=body["owner"],
            allowed_workers=workers,
            allowed_models=models,
            max_budget_usd=body["max_budget_usd"],
            expires_at=expires_at,
        )
        path = self.private_root / "knowledge-authorizations" / f"{authorization.digest}.json"
        atomic_write_json(path, {"kind": "KnowledgeCompileAuthorization", **body, "digest": authorization.digest}, private=True)
        return authorization

    def compile_draft(
        self,
        request: Mapping[str, Any],
        *,
        authorization: KnowledgeCompileAuthorization,
        worker: str,
        model: str,
        reserved_budget_usd: float,
        draft_payload: Mapping[str, Any],
    ) -> KnowledgeDraft:
        request_map = _api_require_mapping(request, "knowledge compile request")
        request_digest = _api_request_digest(request_map)
        if not isinstance(authorization, KnowledgeCompileAuthorization):
            raise KnowledgeAuthorizationError("invalid knowledge compile authorization")
        if authorization.request_digest != request_digest:
            raise KnowledgeAuthorizationError("knowledge authorization is bound to a different request")
        if datetime.now(timezone.utc) >= authorization.expires_at:
            raise KnowledgeAuthorizationError("knowledge compile authorization has expired")
        if worker not in authorization.allowed_workers:
            raise KnowledgeAuthorizationError(f"worker {worker!r} is not authorized")
        if model not in authorization.allowed_models:
            raise KnowledgeAuthorizationError(f"model {model!r} is not authorized")
        if (
            not isinstance(reserved_budget_usd, (int, float))
            or isinstance(reserved_budget_usd, bool)
            or reserved_budget_usd < 0
            or float(reserved_budget_usd) > authorization.max_budget_usd
        ):
            raise KnowledgeAuthorizationError("reserved knowledge compile budget is invalid or exceeds authorization")
        payload = _api_validate_draft_payload(_api_require_mapping(draft_payload, "knowledge draft payload"))
        identifier = str(request_map["id"])
        snapshot_digest = canonical_sha256(
            {
                "id": identifier,
                "input_digest": request_digest,
                "compile_authorization_ref": authorization.digest,
                "payload": payload,
            }
        )
        draft = KnowledgeDraft(
            identifier=identifier,
            status="draft",
            snapshot_digest=snapshot_digest,
            input_digest=request_digest,
            compile_authorization_ref=authorization.digest,
            payload=payload,
        )
        path = self.private_root / "knowledge-drafts" / f"{snapshot_digest}.json"
        atomic_write_json(
            path,
            {
                "kind": "SubdomainKnowledgeSnapshot",
                "status": draft.status,
                "id": draft.identifier,
                "snapshot_digest": draft.snapshot_digest,
                "input_digest": draft.input_digest,
                "compile_authorization_ref": draft.compile_authorization_ref,
                "payload": draft.payload,
            },
            private=True,
        )
        return draft

    def _prior_review_receipts(self, draft: KnowledgeDraft) -> list[Mapping[str, Any]]:
        receipts: list[Mapping[str, Any]] = []
        directory = self.private_root / "knowledge-reviews"
        if not directory.exists():
            return receipts
        for path in directory.glob("*.json"):
            try:
                value = read_json(path)
            except (InputError, OSError):
                continue
            if (
                isinstance(value, Mapping)
                and value.get("kind") == "KnowledgeReviewDecision"
                and value.get("snapshot_digest") == draft.snapshot_digest
                and value.get("input_digest") == draft.input_digest
                and value.get("compile_authorization_ref") == draft.compile_authorization_ref
            ):
                receipts.append(value)
        return receipts

    def approve(self, draft: KnowledgeDraft, decision: Mapping[str, Any]) -> KnowledgeSnapshot:
        if not isinstance(draft, KnowledgeDraft) or draft.status != "draft":
            raise KnowledgeTransitionError("only a draft knowledge snapshot may be reviewed")
        if not isinstance(decision, Mapping):
            raise KnowledgeReviewBindingError("knowledge review decision must be an object")
        for field, expected in (
            ("snapshot_digest", draft.snapshot_digest),
            ("input_digest", draft.input_digest),
            ("compile_authorization_ref", draft.compile_authorization_ref),
        ):
            if decision.get(field) != expected:
                raise KnowledgeReviewBindingError(f"knowledge review {field} does not bind this draft")
        reviewer = decision.get("reviewer")
        reviewed_at = decision.get("reviewed_at")
        status = decision.get("decision")
        if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reviewed_at, str):
            raise KnowledgeReviewBindingError("knowledge review requires reviewer and reviewed_at")
        if status not in _API_REVIEW_DECISIONS:
            raise KnowledgeTransitionError("unsupported knowledge review decision")
        if status in {"revise", "rejected"} and not isinstance(decision.get("rationale"), str):
            raise KnowledgeReviewBindingError(f"knowledge review {status} requires a non-empty rationale")
        if status in {"revise", "rejected"} and not decision["rationale"].strip():
            raise KnowledgeReviewBindingError(f"knowledge review {status} requires a non-empty rationale")
        review_directory = self.private_root / "knowledge-reviews"
        with _knowledge_object_lock(review_directory, "api-draft-review", draft.snapshot_digest):
            existing = self._prior_review_receipts(draft)
            if existing:
                previous = sorted({str(receipt.get("decision")) for receipt in existing})
                raise KnowledgeTransitionError(
                    "knowledge draft already has immutable review decision(s): "
                    + ", ".join(previous)
                    + "; compile a changed revision before another review"
                )
            review_body = {
                "snapshot_digest": draft.snapshot_digest,
                "input_digest": draft.input_digest,
                "compile_authorization_ref": draft.compile_authorization_ref,
                "reviewer": reviewer.strip(),
                "reviewed_at": reviewed_at,
                "decision": status,
                "rationale": decision.get("rationale", ""),
            }
            review_digest = canonical_sha256(review_body)
            receipt_path = review_directory / f"{review_digest}.json"
            receipt = {"kind": "KnowledgeReviewDecision", **review_body, "digest": review_digest}
            snapshot = KnowledgeSnapshot(
                identifier=draft.identifier,
                status=status,
                snapshot_digest=draft.snapshot_digest,
                input_digest=draft.input_digest,
                compile_authorization_ref=draft.compile_authorization_ref,
                review_digest=review_digest,
                payload=dict(draft.payload),
            )
            frozen_path = self.knowledge_root / "compiled" / f"{draft.identifier.replace('.', '__')}.json"
            previous_frozen = frozen_path.read_bytes() if frozen_path.exists() else None
            created = False
            try:
                _create_knowledge_receipt(receipt_path, receipt)
                created = True
                if status == "approved":
                    atomic_write_json(
                        frozen_path,
                        {
                            "kind": "SubdomainKnowledgeSnapshot",
                            "status": snapshot.status,
                            "id": snapshot.identifier,
                            "snapshot_digest": snapshot.snapshot_digest,
                            "input_digest": snapshot.input_digest,
                            "compile_authorization_ref": snapshot.compile_authorization_ref,
                            "review_digest": snapshot.review_digest,
                            "payload": snapshot.payload,
                        },
                    )
            except FileExistsError as exc:
                raise KnowledgeTransitionError("knowledge review receipt already exists") from exc
            except Exception:
                if status == "approved":
                    _restore_text_file(frozen_path, previous_frozen, private=False)
                if created:
                    receipt_path.unlink(missing_ok=True)
                raise
            return snapshot

    def transition(self, snapshot: KnowledgeDraft | KnowledgeSnapshot, target_status: str) -> None:
        """Reject generic state changes; only :meth:`approve` records review decisions."""
        del snapshot, target_status
        raise KnowledgeTransitionError(
            "knowledge state can change only through a bound KnowledgeReviewDecision"
        )
