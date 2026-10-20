"""Typed, fail-closed contracts for viewer-native measurement work.

The contract separates a model's target choice from the host's measurement
execution.  Models can only select opaque inventory identifiers; they cannot
invent a selector, supply coordinates, or receive a measured value through a
receipt.  The host resolves those identifiers inside a private inventory.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from jsonschema import Draft7Validator

from ..contracts import digest, require_digest
from ..errors import InputError
from ..resource_paths import PackageResourceError, config_path
from ..storage.atomic import read_json


class MeasurementContractError(InputError):
    """A scene inventory, selector, or measurement binding is unsafe."""


# These are deliberately narrower than a viewer's global capability profile.
# A new leaf must be reviewed here before a BriefBuilder can select it.
_LEAF_SELECTOR_POLICY: dict[str, dict[str, frozenset[str]]] = {
    "biology.docking_pose_plausibility": {
        "inspect": frozenset({"residue_pair", "component_pair"}),
        "isolate": frozenset({"residue", "atom"}),
    },
    "biology.molecular_collision_check": {
        "inspect": frozenset({"atom_pair", "residue_pair"}),
    },
    "chemistry.conformation_analysis": {
        "measure_dihedral": frozenset({"atom_quadruplet"}),
        "measure_distance": frozenset({"atom_pair"}),
    },
    "chemistry.stereochemistry_chirality": {
        # The controlled chirality pilot exposes rotate/render.  Logical
        # inspection is bound to its rendered scene, never to raw structure
        # metadata or a computed stereochemical label.
        "inspect": frozenset({"atom", "residue"}),
    },
    "chemistry.steric_accessibility": {
        "inspect": frozenset({"atom", "residue"}),
        "measure_distance": frozenset({"atom_pair"}),
    },
    "medical.volumetric_target_geometry": {
        "bbox": frozenset({"mask_label_axis"}),
        "slice": frozenset({"mask_label_axis"}),
    },
}

_SELECTOR_ID_FIELDS = {
    "atom": "atom_id",
    "atom_pair": "atom_ids",
    "atom_quadruplet": "atom_ids",
    "residue": "residue_id",
    "residue_pair": "residue_ids",
    "component_pair": "component_ids",
}
_CATALOG_FOR_SELECTOR = {
    "atom": "atoms",
    "atom_pair": "atoms",
    "atom_quadruplet": "atoms",
    "residue": "residues",
    "residue_pair": "residues",
    "component_pair": "components",
}
_INDEXED_CATALOGS = {
    "components": ("component_id", "component-", 4),
    "residues": ("residue_id", "residue-", 6),
    "atoms": ("atom_id", "atom-", 8),
}


def _schema(name: str) -> dict[str, Any]:
    try:
        value = read_json(config_path("schemas", name))
    except PackageResourceError as exc:
        raise MeasurementContractError(f"measurement schema is unavailable: {name}") from exc
    if not isinstance(value, Mapping):
        raise MeasurementContractError(f"measurement schema must be an object: {name}")
    return dict(value)


def _validate_schema(value: Any, schema_name: str, label: str) -> None:
    errors = sorted(Draft7Validator(_schema(schema_name)).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise MeasurementContractError(f"{label} schema validation failed at {location}: {error.message}")


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MeasurementContractError(f"{label} must be an object")
    return dict(value)


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().casefold() == "default" or "\x00" in value:
        raise MeasurementContractError(f"{label} must be a non-default non-empty string")
    return value.strip()


def selector_policy(target_leaf: str) -> dict[str, tuple[str, ...]]:
    """Return the reviewed native operation -> selector-type policy for a leaf."""

    try:
        policy = _LEAF_SELECTOR_POLICY[target_leaf]
    except KeyError as exc:
        raise MeasurementContractError(f"target leaf has no reviewed selector policy: {target_leaf}") from exc
    return {operation: tuple(sorted(selector_types)) for operation, selector_types in policy.items()}


def allowed_selector_types(target_leaf: str) -> tuple[str, ...]:
    """Return every selector type that may occur in an inventory for this leaf."""

    policy = selector_policy(target_leaf)
    return tuple(sorted({item for values in policy.values() for item in values}))


def _walk_reject_default(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or not key or key.casefold() == "default":
                raise MeasurementContractError(f"{label} contains a default or invalid selector field")
            _walk_reject_default(nested, label)
    elif isinstance(value, list):
        if not value:
            raise MeasurementContractError(f"{label} cannot be empty")
        for nested in value:
            _walk_reject_default(nested, label)
    elif isinstance(value, str):
        _nonempty_text(value, label)


def _stable_ids(catalog_key: str, count: int) -> set[str]:
    try:
        _field, prefix, width = _INDEXED_CATALOGS[catalog_key]
    except KeyError as exc:
        raise MeasurementContractError("scene inventory selector index has an unknown category") from exc
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise MeasurementContractError("scene inventory selector index count is invalid")
    return {f"{prefix}{index:0{width}d}" for index in range(1, count + 1)}


def _indexed_catalog_ids(catalog: Mapping[str, Any], selector_index: Mapping[str, Any] | None, catalog_key: str) -> set[str]:
    field, _prefix, _width = _INDEXED_CATALOGS[catalog_key]
    entries = catalog[catalog_key]
    if selector_index is None:
        return {item[field] for item in entries}

    indexed = _require_mapping(selector_index.get(catalog_key), f"scene inventory selector_index.{catalog_key}")
    mode = indexed.get("mode")
    count = indexed.get("count")
    pages = indexed.get("pages")
    if mode not in {"inline", "paged"} or not isinstance(pages, list):
        raise MeasurementContractError("scene inventory selector index category is invalid")
    expected = _stable_ids(catalog_key, count)
    if mode == "inline":
        if pages or len(entries) != len(expected) or {item[field] for item in entries} != expected:
            raise MeasurementContractError("scene inventory inline selector index does not cover its catalog")
        return expected

    if entries:
        raise MeasurementContractError("scene inventory paged selector catalog must not inline a partial entity list")
    if (not expected and pages) or (expected and not pages):
        raise MeasurementContractError("scene inventory paged selector index has incomplete page coverage")
    expected_start = 1
    for page_number, raw_page in enumerate(pages, start=1):
        page = _require_mapping(raw_page, "scene inventory selector page")
        if (
            page.get("page_number") != page_number
            or not isinstance(page.get("entry_count"), int)
            or isinstance(page["entry_count"], bool)
            or page["entry_count"] < 1
        ):
            raise MeasurementContractError("scene inventory selector page metadata is invalid")
        page_count = int(page["entry_count"])
        first = f"{_prefix}{expected_start:0{_width}d}"
        last = f"{_prefix}{expected_start + page_count - 1:0{_width}d}"
        if page.get("first_id") != first or page.get("last_id") != last:
            raise MeasurementContractError("scene inventory selector page range is not stable or contiguous")
        require_digest(page.get("content_digest"), "scene inventory selector page digest")
        expected_start += page_count
    if expected_start != len(expected) + 1:
        raise MeasurementContractError("scene inventory paged selector index does not cover every entity")
    return expected


def validate_scene_inventory_body(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the private inventory and its selector catalog closure."""

    body = _require_mapping(value, "scene inventory")
    _validate_schema(body, "scene_inventory.schema.json", "scene inventory")
    target_leaf = _nonempty_text(body["target_leaf"], "scene inventory target_leaf")
    allowed = set(allowed_selector_types(target_leaf))
    listed_types = body["selector_types"]
    if not isinstance(listed_types, list) or not listed_types or set(listed_types) - allowed:
        raise MeasurementContractError("scene inventory exposes an unreviewed selector type")
    asset_sha256 = _require_mapping(body["asset_sha256"], "scene inventory asset_sha256")
    if "primary" not in asset_sha256 or set(asset_sha256) - {"primary", "mask"}:
        raise MeasurementContractError("scene inventory asset_sha256 has an invalid role set")
    for role, value in asset_sha256.items():
        require_digest(value, f"scene inventory asset_sha256.{role}")
    catalog = _require_mapping(body["selector_catalog"], "scene inventory selector_catalog")
    backend_map = _require_mapping(body["backend_selector_map"], "scene inventory backend_selector_map")
    raw_selector_index = body.get("selector_index")
    selector_index = (
        _require_mapping(raw_selector_index, "scene inventory selector_index") if raw_selector_index is not None else None
    )

    component_ids = _indexed_catalog_ids(catalog, selector_index, "components")
    residue_ids = _indexed_catalog_ids(catalog, selector_index, "residues")
    atom_ids = _indexed_catalog_ids(catalog, selector_index, "atoms")
    if (
        (selector_index is None and len(component_ids) != len(catalog["components"]))
        or (selector_index is None and len(residue_ids) != len(catalog["residues"]))
        or (selector_index is None and len(atom_ids) != len(catalog["atoms"]))
    ):
        raise MeasurementContractError("scene inventory selector identifiers must be unique")
    if any(item["component_id"] not in component_ids for item in catalog["residues"]):
        raise MeasurementContractError("scene inventory residue references an unknown component")
    if any(item["residue_id"] not in residue_ids for item in catalog["atoms"]):
        raise MeasurementContractError("scene inventory atom references an unknown residue")
    entity_ids = component_ids | residue_ids | atom_ids
    if set(backend_map) != entity_ids:
        raise MeasurementContractError("scene inventory backend map must exactly cover selectable molecular entities")
    if any(not isinstance(item, Mapping) or not item for item in backend_map.values()):
        raise MeasurementContractError("scene inventory backend map has an empty selector")
    if "mask_label_axis" in listed_types and (not catalog["mask_labels"] or not catalog["axes"]):
        raise MeasurementContractError("mask label selector requires extracted labels and axes")
    is_volume = "mask_label_axis" in listed_types
    volume_shape = catalog["volume_shape"]
    layers = catalog["layers"]
    if is_volume:
        if len(volume_shape) < 3 or not layers or "mask" not in asset_sha256:
            raise MeasurementContractError("volumetric inventory requires shape, layers, and a mask asset digest")
        if component_ids or residue_ids or atom_ids:
            raise MeasurementContractError("volumetric inventory cannot expose molecular entities")
    elif volume_shape or layers:
        raise MeasurementContractError("molecular inventory cannot expose volumetric shape or layers")
    if any(selector_type in listed_types for selector_type in ("atom", "atom_pair", "atom_quadruplet")) and not atom_ids:
        raise MeasurementContractError("atom selector type requires extracted atoms")
    if any(selector_type in listed_types for selector_type in ("residue", "residue_pair")) and not residue_ids:
        raise MeasurementContractError("residue selector type requires extracted residues")
    if "component_pair" in listed_types and not component_ids:
        raise MeasurementContractError("component selector type requires extracted components")
    return deepcopy(body)


def _selector_type(selector: Mapping[str, Any]) -> str:
    value = selector.get("type")
    if not isinstance(value, str) or value not in set(_SELECTOR_ID_FIELDS) | {"mask_label_axis"}:
        raise MeasurementContractError("TargetSpec selector type is invalid")
    return value


def _validate_selector_against_inventory(selector: Mapping[str, Any], inventory: Mapping[str, Any]) -> None:
    target_type = _selector_type(selector)
    catalog = _require_mapping(inventory["selector_catalog"], "scene inventory selector_catalog")
    available_types = set(inventory["selector_types"])
    if target_type not in available_types:
        raise MeasurementContractError("TargetSpec selector type is not available for this scene")
    if target_type == "mask_label_axis":
        if selector.get("mask_label") not in set(catalog["mask_labels"]) or selector.get("axis") not in set(catalog["axes"]):
            raise MeasurementContractError("TargetSpec mask label or axis is absent from the scene inventory")
        return
    field = _SELECTOR_ID_FIELDS[target_type]
    identifiers = selector.get(field)
    values = [identifiers] if isinstance(identifiers, str) else identifiers
    if not isinstance(values, list) or not values or any(not isinstance(item, str) for item in values):
        raise MeasurementContractError("TargetSpec selector identifiers are invalid")
    catalog_key = _CATALOG_FOR_SELECTOR[target_type]
    selector_index = inventory.get("selector_index")
    available = _indexed_catalog_ids(
        catalog,
        _require_mapping(selector_index, "scene inventory selector_index") if selector_index is not None else None,
        catalog_key,
    )
    if any(item not in available for item in values):
        raise MeasurementContractError("TargetSpec references an entity absent from the scene inventory")


def validate_target_spec(
    value: Mapping[str, Any],
    *,
    inventory: Mapping[str, Any] | None = None,
    inventory_digest: str | None = None,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one BriefBuilder TargetSpec and optionally bind it to a scene."""

    target = _require_mapping(value, "TargetSpec")
    _validate_schema(target, "target_spec.schema.json", "TargetSpec")
    _walk_reject_default(target["selector"], "TargetSpec selector")
    if target["allowed_native_operations"] != [target["native_operation"]]:
        raise MeasurementContractError("TargetSpec must grant exactly its selected native operation")
    policy = selector_policy(str(target["target_leaf"]))
    selector_type = _selector_type(_require_mapping(target["selector"], "TargetSpec selector"))
    if selector_type not in set(policy.get(str(target["native_operation"]), ())):
        raise MeasurementContractError("TargetSpec operation and selector type are not approved for its leaf")
    if inventory is not None:
        checked_inventory = validate_scene_inventory_body(inventory)
        for field in ("source_record_digest", "anonymous_scene_ref", "target_leaf"):
            if target[field] != checked_inventory[field]:
                raise MeasurementContractError(f"TargetSpec {field} differs from the scene inventory")
        if inventory_digest is not None:
            require_digest(inventory_digest, "scene inventory digest")
            if target["scene_inventory_digest"] != inventory_digest:
                raise MeasurementContractError("TargetSpec differs from the scene inventory digest")
        _validate_selector_against_inventory(_require_mapping(target["selector"], "TargetSpec selector"), checked_inventory)
    if expected is not None:
        bindings = _require_mapping(expected, "TargetSpec expected bindings")
        for field, expected_value in bindings.items():
            if field not in target or target[field] != expected_value:
                raise MeasurementContractError(f"TargetSpec does not match its immutable {field} binding")
    return deepcopy(target)


def validate_brief_target_spec_output(
    value: Mapping[str, Any],
    *,
    inventory: Mapping[str, Any],
    source_record_digest: str,
    scene_inventory_digest: str,
    anonymous_scene_ref: str,
    candidate_plan_digest: str,
    target_leaf: str,
) -> dict[str, Any]:
    """Validate the exact output contract for a live BriefBuilder stage.

    A live DAG supplies every expected binding from immutable job locks.  This
    avoids treating any model-written scene reference or source digest as an
    authority, while keeping the function independent of a particular DAG.
    """

    expected = {
        "source_record_digest": require_digest(source_record_digest, "brief source record digest"),
        "scene_inventory_digest": require_digest(scene_inventory_digest, "brief scene inventory digest"),
        "anonymous_scene_ref": _nonempty_text(anonymous_scene_ref, "brief anonymous scene reference"),
        "candidate_plan_digest": require_digest(candidate_plan_digest, "brief candidate plan digest"),
        "target_leaf": _nonempty_text(target_leaf, "brief target leaf"),
    }
    return validate_target_spec(
        value,
        inventory=inventory,
        inventory_digest=scene_inventory_digest,
        expected=expected,
    )


def build_measurement_request(
    target_spec: Mapping[str, Any],
    *,
    candidate_digest: str,
    request_id: str,
    request_mode: str = "candidate",
) -> dict[str, Any]:
    """Bind a candidate or pre-author pilot subject to a frozen TargetSpec."""

    target = validate_target_spec(target_spec)
    require_digest(candidate_digest, "measurement candidate digest")
    _nonempty_text(request_id, "measurement request id")
    if request_mode not in {"candidate", "preauthor_native_measurement_pilot"}:
        raise MeasurementContractError("measurement request mode is invalid")
    request = {
        "request_version": 1,
        "request_id": request_id,
        "request_mode": request_mode,
        "target_spec_digest": digest(target),
        "source_record_digest": target["source_record_digest"],
        "scene_inventory_digest": target["scene_inventory_digest"],
        "anonymous_scene_ref": target["anonymous_scene_ref"],
        "candidate_plan_digest": target["candidate_plan_digest"],
        "candidate_digest": candidate_digest,
        "native_operation": target["native_operation"],
        "selector": deepcopy(target["selector"]),
        "answer_fields": list(target["answer_fields"]),
    }
    return validate_measurement_request(request, target_spec=target)


def validate_measurement_request(value: Mapping[str, Any], *, target_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Require an exact immutable replay of a target spec, plus candidate binding."""

    request = _require_mapping(value, "MeasurementRequest")
    _validate_schema(request, "measurement_request.schema.json", "MeasurementRequest")
    target = validate_target_spec(target_spec)
    if request["target_spec_digest"] != digest(target):
        raise MeasurementContractError("MeasurementRequest does not bind the supplied TargetSpec")
    for field in (
        "source_record_digest",
        "scene_inventory_digest",
        "anonymous_scene_ref",
        "candidate_plan_digest",
        "native_operation",
        "selector",
        "answer_fields",
    ):
        if request[field] != target[field]:
            raise MeasurementContractError(f"MeasurementRequest changed TargetSpec field: {field}")
    _walk_reject_default(request["selector"], "MeasurementRequest selector")
    return deepcopy(request)


def selector_backend(inventory: Mapping[str, Any], selector: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve opaque selector IDs to an in-process host-only selector payload."""

    checked_inventory = validate_scene_inventory_body(inventory)
    checked_selector = _require_mapping(selector, "measurement selector")
    _validate_selector_against_inventory(checked_selector, checked_inventory)
    selector_type = _selector_type(checked_selector)
    backend_map = _require_mapping(checked_inventory["backend_selector_map"], "scene inventory backend_selector_map")
    if selector_type == "mask_label_axis":
        return {
            "type": selector_type,
            "mask_label": int(checked_selector["mask_label"]),
            "axis": str(checked_selector["axis"]),
        }
    field = _SELECTOR_ID_FIELDS[selector_type]
    identifiers = checked_selector[field]
    values = [identifiers] if isinstance(identifiers, str) else list(identifiers)
    plural = "atoms" if selector_type.startswith("atom") else "residues" if selector_type.startswith("residue") else "components"
    return {"type": selector_type, plural: [deepcopy(backend_map[item]) for item in values]}


def validate_measurement_receipt_body(value: Mapping[str, Any]) -> dict[str, Any]:
    receipt = _require_mapping(value, "measurement receipt")
    _validate_schema(receipt, "measurement_receipt.schema.json", "measurement receipt")
    return deepcopy(receipt)


def validate_measurement_private_sidecar_body(value: Mapping[str, Any]) -> dict[str, Any]:
    sidecar = _require_mapping(value, "measurement private sidecar")
    _validate_schema(sidecar, "measurement_private_sidecar.schema.json", "measurement private sidecar")
    return deepcopy(sidecar)


def validate_measurement_verifier_approval_body(value: Mapping[str, Any]) -> dict[str, Any]:
    approval = _require_mapping(value, "measurement verifier approval")
    _validate_schema(approval, "measurement_verifier_approval.schema.json", "measurement verifier approval")
    return deepcopy(approval)


__all__ = [
    "MeasurementContractError",
    "allowed_selector_types",
    "build_measurement_request",
    "selector_backend",
    "selector_policy",
    "validate_measurement_private_sidecar_body",
    "validate_measurement_receipt_body",
    "validate_measurement_request",
    "validate_measurement_verifier_approval_body",
    "validate_brief_target_spec_output",
    "validate_scene_inventory_body",
    "validate_target_spec",
]
