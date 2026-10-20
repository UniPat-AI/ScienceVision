"""Fail-closed viewer capability mapping for compiled selection batches.

The frozen selection records historical viewer hints.  Those hints are useful
for a viewer-native (``preserve_intent``) route, but are not an authority to
grant capabilities to a reauthored task.  This module is deliberately a
prepare-time bridge only: it reads no scene bytes, opens no browser, and does
not implement any scientific measurement algorithm.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, require_digest
from ..errors import InputError
from ..resource_paths import PackageResourceError, config_path
from ..storage.atomic import read_json


class ViewerCapabilityMappingError(InputError):
    """A selection row cannot be bound to a real controlled viewer capability."""


BASE_VIEWER_OPERATIONS = frozenset({"render"})
SUPPORTED_CAPABILITY_OPERATIONS = frozenset(
    {
        "inspect",
        "isolate",
        "rotate",
        "render",
        "orbit",
        "select",
        "zoom",
        "pan",
        "slice",
        "window_level",
        "bbox",
        "measure_distance",
        "measure_angle",
        "measure_dihedral",
        "measure_area",
        "measure_volume",
        "explode",
        "section_cut",
        "overlay_compare",
    }
)
MEASUREMENT_OPERATIONS = frozenset(
    {
        "measure_distance",
        "measure_angle",
        "measure_dihedral",
        "measure_area",
        "measure_volume",
    }
)

_MAPPING_CONFIG = "compiled_selection_v32.yaml"
_MAPPING_SCHEMA = "compiled_selection_viewer_capability_mapping.schema.json"
_RECEIPT_SCHEMA = "compiled_selection_viewer_capability_receipt.schema.json"
_MAPPING_REFERENCE_FIELDS = frozenset(
    {
        "mapping_id",
        "content_digest",
        "profile_id",
        "profile_digest",
        "receipt",
    }
)
_RECEIPT_REFERENCE_FIELDS = frozenset({"path", "content_digest"})
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "id",
        "decision",
        "reviewer",
        "reviewed_at",
        "mapping_id",
        "mapping_digest",
        "profile_id",
        "profile_digest",
        "slot_digest",
        "source_access_track",
        "generation_strategy",
        "target_leaf",
        "source_viewer",
        "source_hint_digest",
        "asset_formats",
        "viewer_family",
        "viewer_lock_content_digest",
        "allowed_operations",
    }
)


def _schema(name: str) -> dict[str, Any]:
    try:
        value = read_json(config_path("schemas", name))
    except PackageResourceError as exc:
        raise ViewerCapabilityMappingError(f"viewer capability schema is unavailable: {name}") from exc
    if not isinstance(value, Mapping):
        raise ViewerCapabilityMappingError(f"viewer capability schema must be an object: {name}")
    return dict(value)


def _validate_schema(value: Any, name: str, label: str) -> None:
    errors = sorted(Draft7Validator(_schema(name)).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ViewerCapabilityMappingError(f"{label} schema validation failed at {location}: {error.message}")


def _read_mapping_resource() -> dict[str, Any]:
    try:
        path = config_path("viewer-capabilities", _MAPPING_CONFIG)
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, PackageResourceError, yaml.YAMLError) as exc:
        raise ViewerCapabilityMappingError("compiled-selection viewer capability mapping is unavailable") from exc
    if not isinstance(value, Mapping):
        raise ViewerCapabilityMappingError("compiled-selection viewer capability mapping must be an object")
    return dict(value)


def load_compiled_selection_capability_mapping() -> dict[str, Any]:
    """Load the packaged, reviewable mapping registry and profile digests."""
    mapping = _read_mapping_resource()
    _validate_schema(mapping, _MAPPING_SCHEMA, "compiled-selection viewer capability mapping")
    profiles = mapping.get("profiles")
    if not isinstance(profiles, list):  # Defended by schema; keeps type narrowing local.
        raise ViewerCapabilityMappingError("compiled-selection viewer capability profiles are malformed")
    by_id: dict[str, dict[str, Any]] = {}
    for raw in profiles:
        if not isinstance(raw, Mapping):
            raise ViewerCapabilityMappingError("compiled-selection viewer capability profile is malformed")
        profile = dict(raw)
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in by_id:
            raise ViewerCapabilityMappingError("compiled-selection viewer capability profile id is invalid")
        by_id[profile_id] = profile
    return {
        "mapping": mapping,
        "content_digest": digest(mapping),
        "profiles": by_id,
        "profile_digests": {profile_id: digest(profile) for profile_id, profile in by_id.items()},
    }


def validate_capability_mapping_reference(value: Any) -> dict[str, Any]:
    """Validate a lock's static-registry and receipt reference, not a scene."""
    if not isinstance(value, Mapping) or set(value) != _MAPPING_REFERENCE_FIELDS:
        raise ViewerCapabilityMappingError("viewer capability mapping reference fields are invalid")
    mapping_id = value.get("mapping_id")
    profile_id = value.get("profile_id")
    if not isinstance(mapping_id, str) or not mapping_id:
        raise ViewerCapabilityMappingError("viewer capability mapping id is invalid")
    if not isinstance(profile_id, str) or not profile_id:
        raise ViewerCapabilityMappingError("viewer capability profile id is invalid")
    receipt = value.get("receipt")
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_REFERENCE_FIELDS:
        raise ViewerCapabilityMappingError("viewer capability receipt reference is malformed")
    receipt_path = receipt.get("path")
    if not isinstance(receipt_path, str) or not receipt_path.strip() or "\x00" in receipt_path:
        raise ViewerCapabilityMappingError("viewer capability receipt path is invalid")
    try:
        return {
            "mapping_id": mapping_id,
            "content_digest": require_digest(value.get("content_digest"), "viewer capability mapping content_digest"),
            "profile_id": profile_id,
            "profile_digest": require_digest(value.get("profile_digest"), "viewer capability profile digest"),
            "receipt": {
                "path": receipt_path,
                "content_digest": require_digest(
                    receipt.get("content_digest"), "viewer capability receipt content_digest"
                ),
            },
        }
    except InputError as exc:
        raise ViewerCapabilityMappingError(str(exc)) from exc


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ViewerCapabilityMappingError(f"{label} must be a non-empty string")
    return value.strip()


def _operation_set(value: Any, label: str, *, allow_empty: bool = False) -> set[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item for item in value)
    ):
        requirement = "a string list" if allow_empty else "a non-empty string list"
        raise ViewerCapabilityMappingError(f"{label} must be {requirement}")
    operations = set(value)
    if len(operations) != len(value):
        raise ViewerCapabilityMappingError(f"{label} cannot contain duplicate operations")
    unsupported = sorted(operations - SUPPORTED_CAPABILITY_OPERATIONS)
    if unsupported:
        raise ViewerCapabilityMappingError(f"{label} has unsupported operation: {unsupported[0]}")
    return operations


def _normalize_target_operation(operation: str) -> str:
    # ``inspect`` is a visual intent, not a separate MCP command. A controlled
    # render is the least-privilege operation that supplies that visual state.
    return "render" if operation == "inspect" else operation


def effective_target_operations(required_operations: set[str], domain_allowed_operations: set[str]) -> tuple[set[str], set[str]]:
    """Return concrete operations after applying the base-render rule."""
    required = {_normalize_target_operation(item) for item in required_operations} | set(BASE_VIEWER_OPERATIONS)
    allowed = {_normalize_target_operation(item) for item in domain_allowed_operations} | set(BASE_VIEWER_OPERATIONS)
    if not required.issubset(allowed):
        missing = sorted(required - allowed)
        raise ViewerCapabilityMappingError(
            f"target leaf viewer operations are outside its effective domain policy: {missing[0]}"
        )
    return required, allowed


def _asset_formats(assets: Any) -> list[str]:
    if not isinstance(assets, list) or not assets:
        raise ViewerCapabilityMappingError("compiled selection row assets are malformed")
    formats: set[str] = set()
    has_primary = False
    for index, raw in enumerate(assets):
        if not isinstance(raw, Mapping):
            raise ViewerCapabilityMappingError(f"compiled selection asset {index} is malformed")
        if raw.get("role") == "primary":
            has_primary = True
        path = raw.get("path")
        if not isinstance(path, str) or not path or "\\" in path:
            raise ViewerCapabilityMappingError("compiled selection asset path is malformed")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise ViewerCapabilityMappingError("compiled selection asset path is not canonical")
        lower = pure.name.lower()
        if lower.endswith(".nii.gz"):
            formats.add("nii.gz")
        else:
            suffix = pure.suffix.lower().lstrip(".")
            if not suffix:
                raise ViewerCapabilityMappingError("compiled selection asset format is unknown")
            formats.add(suffix)
    if not has_primary:
        raise ViewerCapabilityMappingError("compiled selection row has no primary asset")
    return sorted(formats)


def _source_hint(row: Mapping[str, Any]) -> tuple[str, list[str], str]:
    hint = row.get("viewer_hint")
    if not isinstance(hint, Mapping) or set(hint) != {"source_viewer", "allowed_operations"}:
        raise ViewerCapabilityMappingError("compiled selection row viewer_hint is malformed")
    source_viewer = _text(hint.get("source_viewer"), "compiled selection source viewer")
    allowed = hint.get("allowed_operations")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) and item for item in allowed):
        raise ViewerCapabilityMappingError("compiled selection source viewer operations are malformed")
    if len(set(allowed)) != len(allowed):
        raise ViewerCapabilityMappingError("compiled selection source viewer operations contain duplicates")
    normalized = {"source_viewer": source_viewer, "allowed_operations": list(allowed)}
    return source_viewer, list(allowed), digest(normalized)


def _profile_from_reference(reference: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    loaded = load_compiled_selection_capability_mapping()
    mapping = loaded["mapping"]
    if reference["mapping_id"] != mapping.get("id"):
        raise ViewerCapabilityMappingError("viewer capability mapping id is not the packaged mapping")
    if reference["content_digest"] != loaded["content_digest"]:
        raise ViewerCapabilityMappingError("viewer capability mapping digest changed")
    profile = loaded["profiles"].get(reference["profile_id"])
    if profile is None:
        raise ViewerCapabilityMappingError("viewer capability profile is not present in the packaged mapping")
    if reference["profile_digest"] != loaded["profile_digests"][reference["profile_id"]]:
        raise ViewerCapabilityMappingError("viewer capability profile digest changed")
    return mapping, profile


def _receipt_time(value: Any) -> str:
    text = _text(value, "viewer capability receipt reviewed_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ViewerCapabilityMappingError("viewer capability receipt reviewed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ViewerCapabilityMappingError("viewer capability receipt reviewed_at must include a timezone")
    return text


def validate_capability_receipt(
    receipt: Any,
    *,
    reference: Mapping[str, Any],
    receipt_digest: str,
    row: Mapping[str, Any],
    generation_strategy: str,
    target_leaf: str,
    target_required_operations: set[str],
    domain_allowed_operations: set[str],
    viewer_lock_content_digest: str,
    lock_allowed_operations: set[str],
) -> dict[str, Any]:
    """Prove one viewer lock is compatible with its route and static profile.

    ``preserve_intent`` must preserve every source-hint operation through the controlled profile.
    ``reauthor_on_asset`` instead derives its operation scope solely from the
    asset format, viewer family, and target/domain policy.
    """
    reference_value = validate_capability_mapping_reference(reference)
    if not isinstance(receipt, Mapping):
        raise ViewerCapabilityMappingError("viewer capability receipt must be an object")
    receipt_value = dict(receipt)
    if set(receipt_value) != _RECEIPT_FIELDS:
        raise ViewerCapabilityMappingError("viewer capability receipt fields are invalid")
    _validate_schema(receipt_value, _RECEIPT_SCHEMA, "viewer capability receipt")
    if receipt_digest != reference_value["receipt"]["content_digest"]:
        raise ViewerCapabilityMappingError("viewer capability receipt digest does not match its reference")
    if receipt_digest != digest(receipt_value):
        raise ViewerCapabilityMappingError("viewer capability receipt canonical digest changed")

    mapping, profile = _profile_from_reference(reference_value)
    profile_id = reference_value["profile_id"]
    profile_digest = reference_value["profile_digest"]
    source_viewer, source_operations, source_hint_digest = _source_hint(row)
    asset_formats = _asset_formats(row.get("assets"))
    source_access_track = row.get("access_track")
    if not isinstance(source_access_track, Mapping):
        raise ViewerCapabilityMappingError("compiled selection row access_track is malformed")
    source_track = source_access_track.get("source")
    expected_strategy = "reauthor_on_asset" if source_track == "raw_compute" else "preserve_intent"
    if generation_strategy != expected_strategy:
        raise ViewerCapabilityMappingError("viewer capability route strategy does not match source access track")
    if target_leaf not in set(profile.get("leaf_coverage", [])):
        raise ViewerCapabilityMappingError("viewer capability profile does not cover the target leaf")
    if source_viewer not in set(profile.get("source_viewers", [])):
        raise ViewerCapabilityMappingError("viewer capability profile is incompatible with the source viewer family")
    supported_formats = set(profile.get("asset_formats", []))
    unsupported_formats = sorted(set(asset_formats) - supported_formats)
    if unsupported_formats:
        raise ViewerCapabilityMappingError(
            f"viewer capability profile does not support asset format: {unsupported_formats[0]}"
        )

    controlled_operations = _operation_set(profile.get("controlled_operations"), "viewer capability profile controlled_operations")
    native_measurements = _operation_set(
        profile.get("native_measurement_operations"),
        "viewer capability profile native_measurement_operations",
        allow_empty=True,
    )
    if not native_measurements.issubset(MEASUREMENT_OPERATIONS):
        raise ViewerCapabilityMappingError("viewer capability profile native measurements are malformed")
    if not native_measurements.issubset(controlled_operations):
        raise ViewerCapabilityMappingError("viewer capability profile native measurements are not controlled operations")
    source_aliases = profile.get("source_operation_aliases")
    if not isinstance(source_aliases, Mapping):
        raise ViewerCapabilityMappingError("viewer capability profile source operation aliases are malformed")

    required, allowed = effective_target_operations(target_required_operations, domain_allowed_operations)
    preserved_operations: set[str] = set()
    if generation_strategy == "preserve_intent":
        for source_operation in source_operations:
            mapped = source_aliases.get(source_operation)
            if not isinstance(mapped, str) or mapped not in SUPPORTED_CAPABILITY_OPERATIONS:
                raise ViewerCapabilityMappingError(
                    f"preserve_intent source hint has no controlled operation mapping: {source_operation}"
                )
            if mapped not in controlled_operations:
                raise ViewerCapabilityMappingError(
                    f"preserve_intent source hint is unavailable from the controlled profile: {mapped}"
                )
            preserved_operations.add(mapped)
    elif generation_strategy != "reauthor_on_asset":
        raise ViewerCapabilityMappingError("viewer capability generation strategy is invalid")

    expected_operations = required | preserved_operations
    unavailable = sorted(expected_operations - controlled_operations)
    if unavailable:
        raise ViewerCapabilityMappingError(
            f"target leaf requires unavailable controlled viewer operation: {unavailable[0]}"
        )
    policy_gap = sorted(expected_operations - allowed)
    if policy_gap:
        raise ViewerCapabilityMappingError(
            f"target domain policy does not allow controlled viewer operation: {policy_gap[0]}"
        )
    non_native = sorted((expected_operations & MEASUREMENT_OPERATIONS) - native_measurements)
    if non_native:
        raise ViewerCapabilityMappingError(
            f"measurement operation is not viewer-native in this profile: {non_native[0]}"
        )
    if lock_allowed_operations != expected_operations:
        missing = sorted(expected_operations - lock_allowed_operations)
        excess = sorted(lock_allowed_operations - expected_operations)
        detail = f"missing={missing[0]}" if missing else f"excess={excess[0]}"
        raise ViewerCapabilityMappingError(f"viewer lock operations differ from capability mapping: {detail}")

    expected_receipt = {
        "decision": "approved",
        "mapping_id": mapping["id"],
        "mapping_digest": reference_value["content_digest"],
        "profile_id": profile_id,
        "profile_digest": profile_digest,
        "slot_digest": row.get("slot_ref", {}).get("content_digest") if isinstance(row.get("slot_ref"), Mapping) else None,
        "source_access_track": source_track,
        "generation_strategy": generation_strategy,
        "target_leaf": target_leaf,
        "source_viewer": source_viewer,
        "source_hint_digest": source_hint_digest,
        "asset_formats": asset_formats,
        "viewer_family": profile.get("viewer_family"),
        "viewer_lock_content_digest": viewer_lock_content_digest,
        "allowed_operations": sorted(expected_operations),
    }
    for field, expected in expected_receipt.items():
        if receipt_value.get(field) != expected:
            raise ViewerCapabilityMappingError(f"viewer capability receipt does not bind the expected {field}")
    _text(receipt_value.get("id"), "viewer capability receipt id")
    _text(receipt_value.get("reviewer"), "viewer capability receipt reviewer")
    _receipt_time(receipt_value.get("reviewed_at"))
    return {
        "mapping_id": mapping["id"],
        "mapping_digest": reference_value["content_digest"],
        "profile_id": profile_id,
        "profile_digest": profile_digest,
        "viewer_family": str(profile["viewer_family"]),
        "asset_formats": asset_formats,
        "generation_strategy": generation_strategy,
        "source_hint_mode": "required" if generation_strategy == "preserve_intent" else "not_used_for_reauthor",
        "allowed_operations": sorted(expected_operations),
        "receipt_digest": receipt_digest,
    }
