"""Private, digest-bound ground-truth membership registry validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Mapping

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, require_digest
from ..errors import InputError
from ..paths import require_within
from ..resource_paths import PackageResourceError, config_path


class GroundTruthRegistryError(InputError):
    """A ground-truth registry or candidate provenance is unsafe."""


_SOURCE_TYPES = frozenset(
    {
        "expert_annotation",
        "curated_source_label",
        "rule_evaluated_structure",
        "paired_comparison",
        "measurement_receipt",
    }
)
_OPTIONAL_BINDINGS = ("anonymous_scene_ref", "primary_asset_digest", "selection_slot_digest")
_IDENTITY_FIELDS = ("source_id", "source_digest", "record_id", "record_digest", "answer_digest")


@dataclass(frozen=True)
class GroundTruthRegistry:
    """A validated, immutable registry of source records an author may cite."""

    document: Mapping[str, Any]
    content_digest: str
    records: Mapping[tuple[str, str, str, str], Mapping[str, Any]]

    @property
    def identifier(self) -> str:
        return str(self.document["id"])

    @property
    def approved(self) -> bool:
        return self.document.get("source_status") == "approved"


def _resource_json(name: str) -> dict[str, Any]:
    try:
        path = config_path("schemas", name)
        value = json.loads(path.read_text(encoding="utf-8"))
    except (PackageResourceError, OSError, json.JSONDecodeError) as exc:
        raise GroundTruthRegistryError("ground_truth_registry_schema_unavailable") from exc
    if not isinstance(value, Mapping):
        raise GroundTruthRegistryError("ground_truth_registry_schema_invalid")
    return dict(value)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GroundTruthRegistryError(f"{label}_invalid")
    return dict(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GroundTruthRegistryError(f"{label}_invalid")
    return value.strip()


def _allowed_answer_schema(value: Any, label: str) -> dict[str, Any]:
    """Validate the answer form without exposing a record's answer value."""

    schema = _mapping(value, label)
    if set(schema) != {"shape", "fields"}:
        raise GroundTruthRegistryError(f"{label}_shape_invalid")
    shape = _text(schema.get("shape"), f"{label}_shape")
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields or not all(isinstance(item, str) and item.strip() for item in fields):
        raise GroundTruthRegistryError(f"{label}_fields_invalid")
    normalized_fields = [item.strip() for item in fields]
    if len(set(normalized_fields)) != len(normalized_fields):
        raise GroundTruthRegistryError(f"{label}_fields_duplicate")
    return {"shape": shape, "fields": normalized_fields}


def _schema_validate(value: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft7Validator(_resource_json("ground_truth_registry.schema.json")).iter_errors(dict(value)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise GroundTruthRegistryError(f"ground_truth_registry_schema_invalid:{location}:{error.message}")


def _approved_records(document: Mapping[str, Any]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    snapshot = _mapping(document.get("approved_snapshot"), "ground_truth_registry_snapshot")
    values = snapshot.get("records")
    count = document.get("record_count")
    if not isinstance(values, list) or not values or not isinstance(count, int) or isinstance(count, bool) or count != len(values):
        raise GroundTruthRegistryError("ground_truth_registry_records_invalid")
    records: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for raw in values:
        entry = _mapping(raw, "ground_truth_registry_record")
        source_type = entry.get("source_type")
        if source_type not in _SOURCE_TYPES:
            raise GroundTruthRegistryError("ground_truth_registry_record_source_type_invalid")
        try:
            normalized: dict[str, Any] = {
                "source_type": str(source_type),
                "source_id": _text(entry.get("source_id"), "ground_truth_registry_source_id"),
                "source_digest": require_digest(entry.get("source_digest"), "ground truth source digest"),
                "record_id": _text(entry.get("record_id"), "ground_truth_registry_record_id"),
                "record_digest": require_digest(entry.get("record_digest"), "ground truth record digest"),
                "answer_digest": require_digest(entry.get("answer_digest"), "ground truth answer digest"),
            }
            if "allowed_answer_schema" in entry:
                normalized["allowed_answer_schema"] = _allowed_answer_schema(
                    entry["allowed_answer_schema"], "ground_truth_registry_allowed_answer_schema"
                )
            for field in _OPTIONAL_BINDINGS:
                if field in entry:
                    value = entry[field]
                    normalized[field] = (
                        _text(value, f"ground_truth_registry_{field}")
                        if field == "anonymous_scene_ref"
                        else require_digest(value, f"ground truth {field}")
                    )
            if source_type == "measurement_receipt":
                normalized["measurement_receipt_digest"] = require_digest(
                    entry.get("measurement_receipt_digest"), "ground truth measurement receipt digest"
                )
        except InputError as exc:
            raise GroundTruthRegistryError("ground_truth_registry_record_digest_invalid") from exc
        key = (
            normalized["source_id"],
            normalized["source_digest"],
            normalized["record_id"],
            normalized["record_digest"],
        )
        if key in records:
            raise GroundTruthRegistryError("ground_truth_registry_record_duplicate")
        records[key] = normalized
    return records


def _approved_registry(
    registry: GroundTruthRegistry | None,
    expected_registry_digest: str | None,
) -> tuple[GroundTruthRegistry, str]:
    if registry is None or expected_registry_digest is None:
        raise GroundTruthRegistryError("ground_truth_registry_not_plan_locked")
    try:
        expected = require_digest(expected_registry_digest, "ground truth registry plan lock")
    except InputError as exc:
        raise GroundTruthRegistryError("ground_truth_registry_plan_lock_invalid") from exc
    if not registry.approved:
        raise GroundTruthRegistryError("ground_truth_registry_not_approved")
    if registry.content_digest != expected:
        raise GroundTruthRegistryError("ground_truth_registry_plan_lock_mismatch")
    return registry, expected


def ground_truth_record_projection(
    record: Mapping[str, Any],
    *,
    registry_digest: str,
) -> dict[str, Any]:
    """Build the exact answer-free record projection allowed for one job."""

    try:
        expected_registry = require_digest(registry_digest, "ground truth registry projection digest")
    except InputError as exc:
        raise GroundTruthRegistryError("ground_truth_record_projection_registry_invalid") from exc
    source_type = record.get("source_type")
    if source_type not in _SOURCE_TYPES:
        raise GroundTruthRegistryError("ground_truth_record_projection_source_type_invalid")
    try:
        provenance = {
            "source_id": _text(record.get("source_id"), "ground_truth_record_projection_source_id"),
            "source_digest": require_digest(record.get("source_digest"), "ground truth projection source digest"),
            "record_id": _text(record.get("record_id"), "ground_truth_record_projection_record_id"),
            "record_digest": require_digest(record.get("record_digest"), "ground truth projection record digest"),
            "answer_digest": require_digest(record.get("answer_digest"), "ground truth projection answer digest"),
            "ground_truth_registry_digest": expected_registry,
        }
        for field in _OPTIONAL_BINDINGS:
            if field in record:
                provenance[field] = (
                    _text(record[field], f"ground_truth_record_projection_{field}")
                    if field == "anonymous_scene_ref"
                    else require_digest(record[field], f"ground truth projection {field}")
                )
        if source_type == "measurement_receipt":
            provenance["measurement_receipt_digest"] = require_digest(
                record.get("measurement_receipt_digest"), "ground truth projection measurement receipt digest"
            )
        schema = _allowed_answer_schema(
            record.get("allowed_answer_schema"), "ground_truth_record_projection_allowed_answer_schema"
        )
    except InputError as exc:
        raise GroundTruthRegistryError("ground_truth_record_projection_invalid") from exc
    return {
        "source_type": str(source_type),
        "provenance": provenance,
        "allowed_answer_schema": schema,
    }


def resolve_ground_truth_record_projection(
    *,
    registry: GroundTruthRegistry | None,
    expected_registry_digest: str | None,
    identity: Mapping[str, Any],
    expected_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one approved registry record and bind it to one source slot.

    Lock bundles may identify a record but cannot choose a record from the
    global registry at authoring time.  The resulting projection has no answer
    value and is safe to provide to typed workers.
    """

    approved_registry, expected = _approved_registry(registry, expected_registry_digest)
    raw = _mapping(identity, "ground_truth_record_identity")
    expected_identity_fields = {"source_type", *_IDENTITY_FIELDS, "allowed_answer_schema"}
    optional_measurement = "measurement_receipt_digest"
    fields = set(raw)
    if fields - expected_identity_fields - {optional_measurement} or expected_identity_fields - fields:
        raise GroundTruthRegistryError("ground_truth_record_identity_fields_invalid")
    source_type = raw.get("source_type")
    if source_type not in _SOURCE_TYPES:
        raise GroundTruthRegistryError("ground_truth_record_identity_source_type_invalid")
    try:
        source_id = _text(raw.get("source_id"), "ground_truth_record_identity_source_id")
        source_digest = require_digest(raw.get("source_digest"), "ground truth identity source digest")
        record_id = _text(raw.get("record_id"), "ground_truth_record_identity_record_id")
        record_digest = require_digest(raw.get("record_digest"), "ground truth identity record digest")
        answer_digest = require_digest(raw.get("answer_digest"), "ground truth identity answer digest")
        answer_schema = _allowed_answer_schema(
            raw.get("allowed_answer_schema"), "ground_truth_record_identity_allowed_answer_schema"
        )
    except InputError as exc:
        raise GroundTruthRegistryError("ground_truth_record_identity_invalid") from exc
    if source_type == "measurement_receipt":
        try:
            measurement_receipt = require_digest(
                raw.get("measurement_receipt_digest"), "ground truth identity measurement receipt digest"
            )
        except InputError as exc:
            raise GroundTruthRegistryError("ground_truth_record_identity_measurement_receipt_invalid") from exc
    elif optional_measurement in raw:
        raise GroundTruthRegistryError("ground_truth_record_identity_measurement_receipt_unexpected")
    record = approved_registry.records.get((source_id, source_digest, record_id, record_digest))
    if record is None:
        raise GroundTruthRegistryError("ground_truth_record_not_registered")
    projection = ground_truth_record_projection(record, registry_digest=expected)
    if (
        projection["source_type"] != source_type
        or projection["provenance"]["answer_digest"] != answer_digest
        or projection["allowed_answer_schema"] != answer_schema
        or (
            source_type == "measurement_receipt"
            and projection["provenance"].get("measurement_receipt_digest") != measurement_receipt
        )
    ):
        raise GroundTruthRegistryError("ground_truth_record_binding_mismatch")
    if expected_context is not None:
        context = _mapping(expected_context, "ground_truth_record_expected_context")
        for field in _OPTIONAL_BINDINGS:
            if field not in context:
                raise GroundTruthRegistryError("ground_truth_record_expected_context_incomplete")
            if projection["provenance"].get(field) != context[field]:
                raise GroundTruthRegistryError("ground_truth_record_context_binding_mismatch")
    return projection


def validate_ground_truth_record_projection(
    *,
    projection: Mapping[str, Any],
    registry: GroundTruthRegistry | None,
    expected_registry_digest: str | None,
) -> dict[str, Any]:
    """Re-resolve a persisted job projection against its locked registry."""

    value = _mapping(projection, "ground_truth_record_projection")
    if set(value) != {"source_type", "provenance", "allowed_answer_schema"}:
        raise GroundTruthRegistryError("ground_truth_record_projection_fields_invalid")
    provenance = _mapping(value.get("provenance"), "ground_truth_record_projection_provenance")
    identity = {
        "source_type": value.get("source_type"),
        **{field: provenance.get(field) for field in _IDENTITY_FIELDS},
        "allowed_answer_schema": value.get("allowed_answer_schema"),
    }
    if value.get("source_type") == "measurement_receipt":
        identity["measurement_receipt_digest"] = provenance.get("measurement_receipt_digest")
    resolved = resolve_ground_truth_record_projection(
        registry=registry,
        expected_registry_digest=expected_registry_digest,
        identity=identity,
    )
    if value != resolved:
        raise GroundTruthRegistryError("ground_truth_record_projection_mismatch")
    return resolved


def load_ground_truth_registry(
    path: str | Path,
    *,
    expected_digest: str | None = None,
) -> GroundTruthRegistry:
    """Load a private registry and optionally bind it to an approved plan lock."""

    registry_path = Path(path).expanduser().resolve()
    try:
        value = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GroundTruthRegistryError("ground_truth_registry_unavailable") from exc
    if not isinstance(value, Mapping):
        raise GroundTruthRegistryError("ground_truth_registry_invalid")
    document = dict(value)
    _schema_validate(document)
    content_digest = digest(document)
    if expected_digest is not None:
        try:
            require_digest(expected_digest, "ground truth registry expected digest")
        except InputError as exc:
            raise GroundTruthRegistryError("ground_truth_registry_expected_digest_invalid") from exc
        if content_digest != expected_digest:
            raise GroundTruthRegistryError("ground_truth_registry_digest_mismatch")
    records = _approved_records(document) if document.get("source_status") == "approved" else {}
    return GroundTruthRegistry(document=document, content_digest=content_digest, records=records)


def load_plan_locked_ground_truth_registry(
    plan: Mapping[str, Any],
    *,
    workspace_root: str | Path,
) -> GroundTruthRegistry:
    """Resolve the sole registry reference carried by an immutable plan body."""

    body = _mapping(plan.get("body"), "ground_truth_plan_body")
    reference = _mapping(body.get("ground_truth_registry"), "ground_truth_plan_lock")
    basic_fields = {"path", "content_digest"}
    approved_lock_fields = {"path", "content_digest", "approval_digest", "approval_receipt"}
    if set(reference) != basic_fields and set(reference) != approved_lock_fields:
        raise GroundTruthRegistryError("ground_truth_registry_plan_lock_invalid")
    if set(reference) == approved_lock_fields:
        try:
            require_digest(reference.get("approval_digest"), "ground truth registry approval digest")
            receipt = _mapping(reference.get("approval_receipt"), "ground_truth_registry_approval_receipt")
            if set(receipt) != {"path", "content_digest"}:
                raise GroundTruthRegistryError("ground_truth_registry_plan_lock_invalid")
            _text(receipt.get("path"), "ground_truth_registry_approval_receipt_path")
            require_digest(receipt.get("content_digest"), "ground truth registry approval receipt digest")
        except InputError as exc:
            raise GroundTruthRegistryError("ground_truth_registry_plan_lock_invalid") from exc
    path_value = reference.get("path")
    if not isinstance(path_value, (str, Path)):
        raise GroundTruthRegistryError("ground_truth_registry_plan_path_invalid")
    try:
        root = Path(workspace_root).expanduser().resolve()
        raw = Path(path_value).expanduser()
        path = require_within(root, raw if raw.is_absolute() else root / raw, "ground truth registry plan lock")
    except InputError as exc:
        raise GroundTruthRegistryError("ground_truth_registry_plan_path_invalid") from exc
    return load_ground_truth_registry(path, expected_digest=reference.get("content_digest"))


def validate_ground_truth_membership(
    *,
    source_type: Any,
    provenance: Mapping[str, Any],
    reference_answer: Mapping[str, Any],
    registry: GroundTruthRegistry | None,
    expected_registry_digest: str | None,
    expected_record_projection: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Prove a candidate answer came from a plan-locked source record.

    The author supplies only identifiers and the registry's precomputed answer
    digest.  Recomputing that digest here stops a model from swapping in an
    arbitrary answer while retaining syntactically plausible provenance.
    """

    if source_type not in _SOURCE_TYPES:
        raise GroundTruthRegistryError("ground_truth_source_type_forbidden")
    registry, expected = _approved_registry(registry, expected_registry_digest)
    try:
        source_id = _text(provenance.get("source_id"), "gt_answer_contract_provenance_source_id")
        source_digest = require_digest(provenance.get("source_digest"), "gt answer provenance source digest")
        record_id = _text(provenance.get("record_id"), "gt_answer_contract_provenance_record_id")
        record_digest = require_digest(provenance.get("record_digest"), "gt answer provenance record digest")
        answer_digest = require_digest(provenance.get("answer_digest"), "gt answer provenance answer digest")
        registry_digest = require_digest(
            provenance.get("ground_truth_registry_digest"), "gt answer provenance registry digest"
        )
    except InputError as exc:
        raise GroundTruthRegistryError("ground_truth_provenance_digest_invalid") from exc
    if registry_digest != expected:
        raise GroundTruthRegistryError("ground_truth_registry_provenance_mismatch")
    actual_answer_digest = digest(dict(reference_answer))
    if answer_digest != actual_answer_digest:
        raise GroundTruthRegistryError("ground_truth_answer_digest_mismatch")
    record = registry.records.get((source_id, source_digest, record_id, record_digest))
    if record is None:
        raise GroundTruthRegistryError("ground_truth_record_not_registered")
    if record["source_type"] != source_type or record["answer_digest"] != answer_digest:
        raise GroundTruthRegistryError("ground_truth_record_binding_mismatch")
    for field in _OPTIONAL_BINDINGS:
        if field in record and provenance.get(field) != record[field]:
            raise GroundTruthRegistryError("ground_truth_record_context_binding_mismatch")
    if source_type == "measurement_receipt":
        try:
            receipt = require_digest(
                provenance.get("measurement_receipt_digest"), "gt answer provenance measurement receipt digest"
            )
        except InputError as exc:
            raise GroundTruthRegistryError("ground_truth_measurement_receipt_invalid") from exc
        if receipt != record.get("measurement_receipt_digest"):
            raise GroundTruthRegistryError("ground_truth_measurement_receipt_mismatch")
    elif "measurement_receipt_digest" in provenance:
        raise GroundTruthRegistryError("ground_truth_measurement_receipt_unexpected")
    if expected_record_projection is not None:
        expected_projection = validate_ground_truth_record_projection(
            projection=expected_record_projection,
            registry=registry,
            expected_registry_digest=expected,
        )
        candidate_projection = {
            "source_type": source_type,
            "provenance": dict(provenance),
            "allowed_answer_schema": expected_projection["allowed_answer_schema"],
        }
        if candidate_projection["source_type"] != expected_projection["source_type"]:
            raise GroundTruthRegistryError("ground_truth_job_record_source_type_mismatch")
        if candidate_projection["provenance"] != expected_projection["provenance"]:
            raise GroundTruthRegistryError("ground_truth_job_record_provenance_mismatch")
    return record
