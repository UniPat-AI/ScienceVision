#!/usr/bin/env python3
"""Re-attest V32 parent evidence after a config-only batch manifest rebuild.

This utility deliberately does *not* replay scenes, execute measurements, or
call a provider.  It proves that a newly compiled batch differs from its
parent only in the exact target-leaf configuration snapshots, then binds the
parent inventory and viewer receipts as historical evidence.  A fresh
controlled-scene handoff is still required before the new batch can execute.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

import yaml

from qagen.contracts import make_envelope, require_digest, verify_envelope
from qagen.measurement import SceneInventoryError, load_scene_inventory
from qagen.paths import WorkspacePaths, require_within
from qagen.storage.atomic import atomic_write_json, hash_file, read_json
from qagen.viewer.capability_mapping import (
    ViewerCapabilityMappingError,
    effective_target_operations,
    load_compiled_selection_capability_mapping,
)


_BATCH_KIND = "vnext_selection_batch_manifest"
_INVENTORY_BATCH_KIND = "qagen_scene_inventory_batch"
_VIEWER_LOCK_SET_KIND = "qagen_private_batch_scene_viewer_lock_set"
_SOURCE_HANDOFF_KIND = "v32_private_source_agent_handoff"
_REBIND_KIND = "qagen_v32_config_only_selection_rebind"
_LEAF_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_SAFE_OUTPUT_KEY_PARTS = frozenset(
    {
        "answer",
        "asset",
        "path",
        "question",
        "task_id",
        "truth",
    }
)


class RebindError(RuntimeError):
    """Inputs cannot prove a configuration-only V32 rebind."""


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RebindError(f"{label}_invalid")
    return dict(value)


def _private_regular(paths: WorkspacePaths, value: Path, label: str) -> Path:
    try:
        path = require_within(paths.private, value, label)
        metadata = path.stat()
    except OSError as exc:
        raise RebindError(f"{label}_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RebindError(f"{label}_unavailable")
    return path


def _private_envelope(paths: WorkspacePaths, value: Path, *, kind: str, label: str) -> dict[str, Any]:
    path = _private_regular(paths, value, label)
    try:
        raw = read_json(path)
        document = _mapping(raw, label)
        return verify_envelope(document, kind=kind)
    except Exception as exc:
        raise RebindError(f"{label}_invalid") from exc


def _body(envelope: Mapping[str, Any], label: str) -> dict[str, Any]:
    return _mapping(envelope.get("body"), f"{label}_body")


def _slot_digest(row: Mapping[str, Any], label: str) -> str:
    slot = _mapping(row.get("slot_ref"), f"{label}_slot")
    try:
        return require_digest(slot.get("content_digest"), f"{label}_slot_digest")
    except Exception as exc:
        raise RebindError(f"{label}_slot_invalid") from exc


def _batch_rows(envelope: Mapping[str, Any], label: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    body = _body(envelope, label)
    rows = body.get("rows")
    if (
        body.get("total") != 60
        or body.get("generation_eligible") is not False
        or not isinstance(rows, list)
        or len(rows) != 60
    ):
        raise RebindError(f"{label}_not_a_frozen_v32_batch")
    by_slot: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _mapping(raw, f"{label}_row")
        slot_digest = _slot_digest(row, f"{label}_row_{index}")
        if slot_digest in by_slot:
            raise RebindError(f"{label}_duplicate_slot")
        leaf = row.get("target_leaf")
        if not isinstance(leaf, str) or _LEAF_RE.fullmatch(leaf) is None:
            raise RebindError(f"{label}_leaf_invalid")
        if row.get("generation_strategy") not in {"preserve_intent", "reauthor_on_asset"}:
            raise RebindError(f"{label}_strategy_invalid")
        config = _mapping(row.get("target_leaf_config"), f"{label}_target_leaf_config")
        if set(config) != {"path", "content_sha256"}:
            raise RebindError(f"{label}_target_leaf_config_invalid")
        try:
            require_digest(config["content_sha256"], f"{label}_target_leaf_config_digest")
        except Exception as exc:
            raise RebindError(f"{label}_target_leaf_config_invalid") from exc
        by_slot[slot_digest] = row
    return body, by_slot


def _without_config_digest(row: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(row)
    config = _mapping(copied.get("target_leaf_config"), "target_leaf_config")
    config.pop("content_sha256", None)
    copied["target_leaf_config"] = config
    return copied


def _rebind_diff(
    parent_body: Mapping[str, Any],
    current_body: Mapping[str, Any],
    parent_rows: Mapping[str, Mapping[str, Any]],
    current_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str]]:
    parent_metadata = {key: value for key, value in parent_body.items() if key != "rows"}
    current_metadata = {key: value for key, value in current_body.items() if key != "rows"}
    if parent_metadata != current_metadata:
        raise RebindError("batch_metadata_changed")
    if set(parent_rows) != set(current_rows):
        raise RebindError("batch_slot_set_changed")

    changed: dict[str, dict[str, str]] = {}
    for slot_digest in sorted(parent_rows):
        parent_row = parent_rows[slot_digest]
        current_row = current_rows[slot_digest]
        if _without_config_digest(parent_row) != _without_config_digest(current_row):
            raise RebindError("batch_row_changed_outside_target_leaf_config_digest")
        leaf = parent_row["target_leaf"]
        parent_config = _mapping(parent_row["target_leaf_config"], "parent_target_leaf_config")
        current_config = _mapping(current_row["target_leaf_config"], "current_target_leaf_config")
        if parent_config["path"] != current_config["path"]:
            raise RebindError("target_leaf_config_path_changed")
        parent_digest = str(parent_config["content_sha256"])
        current_digest = str(current_config["content_sha256"])
        prior = changed.setdefault(leaf, {"parent": parent_digest, "current": current_digest})
        if prior != {"parent": parent_digest, "current": current_digest}:
            raise RebindError("leaf_config_digest_is_not_consistent_across_slots")
    return {leaf: changed[leaf] for leaf in sorted(changed)}


def _source_and_mapping_invariants(body: Mapping[str, Any]) -> dict[str, str]:
    source = _mapping(body.get("source_selection"), "source_selection")
    mapping = _mapping(body.get("taxonomy_mapping"), "taxonomy_mapping")
    required_source = {"selection_id_sha256", "selection_file_sha256", "import_digest"}
    if set(source) != required_source:
        raise RebindError("source_selection_invalid")
    for field in ("selection_file_sha256", "import_digest"):
        try:
            require_digest(source[field], f"source_selection_{field}")
        except Exception as exc:
            raise RebindError("source_selection_invalid") from exc
    selection_id = source["selection_id_sha256"]
    if not isinstance(selection_id, str) or re.fullmatch(r"[0-9a-f]{64}", selection_id) is None:
        raise RebindError("source_selection_invalid")
    try:
        mapping_digest = require_digest(mapping.get("content_digest"), "taxonomy_mapping_content_digest")
    except Exception as exc:
        raise RebindError("taxonomy_mapping_invalid") from exc
    return {
        "selection_id_sha256": selection_id,
        "selection_file_digest": source["selection_file_sha256"],
        "selection_import_digest": source["import_digest"],
        "taxonomy_mapping_digest": mapping_digest,
    }


def _asset_formats(row: Mapping[str, Any]) -> set[str]:
    assets = row.get("assets")
    if not isinstance(assets, list) or not assets:
        raise RebindError("compiled_row_assets_invalid")
    formats: set[str] = set()
    for raw in assets:
        asset = _mapping(raw, "compiled_row_asset")
        path = asset.get("path")
        if not isinstance(path, str) or not path or "\\" in path:
            raise RebindError("compiled_row_assets_invalid")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise RebindError("compiled_row_assets_invalid")
        lower = pure.name.lower()
        if lower.endswith(".nii.gz"):
            formats.add("nii.gz")
        else:
            suffix = pure.suffix.lower().lstrip(".")
            if not suffix:
                raise RebindError("compiled_row_assets_invalid")
            formats.add(suffix)
    return formats


def _required_operations(pipeline_root: Path, row: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    leaf = row.get("target_leaf")
    strategy = row.get("generation_strategy")
    if not isinstance(leaf, str) or _LEAF_RE.fullmatch(leaf) is None or not isinstance(strategy, str):
        raise RebindError("compiled_row_policy_invalid")
    domain, subdomain = leaf.split(".", 1)
    expected_relative = Path("configs") / "subdomains" / domain / f"{subdomain}.yaml"
    config = pipeline_root / expected_relative
    try:
        metadata = config.stat()
        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RebindError("current_leaf_config_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or config.is_symlink() or not isinstance(loaded, Mapping):
        raise RebindError("current_leaf_config_unavailable")
    compiled_config = _mapping(row.get("target_leaf_config"), "compiled_target_leaf_config")
    if compiled_config.get("path") != expected_relative.as_posix() or hash_file(config) != compiled_config.get("content_sha256"):
        raise RebindError("current_leaf_config_digest_mismatch")
    if loaded.get("id") != leaf:
        raise RebindError("current_leaf_config_invalid")
    viewer = _mapping(loaded.get("viewer"), "current_leaf_viewer")
    required = viewer.get("required_operations")
    if not isinstance(required, list) or not required or not all(isinstance(item, str) and item for item in required):
        raise RebindError("current_leaf_viewer_invalid")
    required_operations = set(required)
    modes = viewer.get("capability_modes", [])
    if not isinstance(modes, list):
        raise RebindError("current_leaf_viewer_invalid")
    for raw_mode in modes:
        mode = _mapping(raw_mode, "current_leaf_viewer_mode")
        strategies = mode.get("generation_strategies")
        mode_operations = mode.get("required_operations")
        if (
            not isinstance(strategies, list)
            or not all(isinstance(item, str) and item for item in strategies)
            or not isinstance(mode_operations, list)
            or not mode_operations
            or not all(isinstance(item, str) and item for item in mode_operations)
        ):
            raise RebindError("current_leaf_viewer_invalid")
        if strategy in strategies:
            required_operations.update(mode_operations)
    domain_config = pipeline_root / "configs" / "domains" / f"{domain}.yaml"
    try:
        domain_loaded = yaml.safe_load(domain_config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RebindError("current_domain_config_unavailable") from exc
    domain_value = _mapping(domain_loaded, "current_domain_config")
    policy = _mapping(domain_value.get("policy"), "current_domain_policy")
    allowed = policy.get("allowed_viewer_operations")
    if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) and item for item in allowed):
        raise RebindError("current_domain_policy_invalid")
    try:
        return effective_target_operations(required_operations, set(allowed))
    except ViewerCapabilityMappingError as exc:
        raise RebindError("current_leaf_operations_not_permitted") from exc


def _viewer_lock_slots(
    lock_set: Mapping[str, Any],
    *,
    parent_batch_digest: str,
    expected_slots: set[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    body = _body(lock_set, "viewer_lock_set")
    if (
        body.get("selection_batch_digest") != parent_batch_digest
        or body.get("total") != 60
        or body.get("prepare_eligible") is not False
        or body.get("release_eligible") is not False
    ):
        raise RebindError("viewer_lock_set_not_parent_pending")
    try:
        registry_digest = require_digest(body.get("registry_digest"), "viewer_lock_set_registry_digest")
        replay_digest = require_digest(body.get("replay_summary_digest"), "viewer_lock_set_replay_summary_digest")
    except Exception as exc:
        raise RebindError("viewer_lock_set_invalid") from exc
    raw_slots = body.get("slots")
    if not isinstance(raw_slots, list) or len(raw_slots) != 60:
        raise RebindError("viewer_lock_set_invalid")
    slots: dict[str, dict[str, Any]] = {}
    actual_counts = {"attested": 0, "blocked": 0, "failed": 0}
    actual_blockers: dict[str, int] = {}
    for raw in raw_slots:
        slot = _mapping(raw, "viewer_lock_slot")
        try:
            slot_digest = require_digest(slot.get("slot_digest"), "viewer_lock_slot_digest")
        except Exception as exc:
            raise RebindError("viewer_lock_slot_invalid") from exc
        if slot_digest in slots:
            raise RebindError("viewer_lock_set_duplicate_slot")
        status = slot.get("receipt_status")
        if status not in actual_counts or slot.get("handoff_capable") is not (status == "attested"):
            raise RebindError("viewer_lock_slot_handoff_state_invalid")
        family = slot.get("viewer_family")
        allowed = slot.get("allowed_operations")
        blockers = slot.get("blockers")
        if (
            family not in {"molstar", "niivue"}
            or not isinstance(allowed, list)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or not all(isinstance(operation, str) and operation for operation in allowed)
            or not isinstance(blockers, list)
            or not all(isinstance(blocker, str) and blocker for blocker in blockers)
        ):
            raise RebindError("viewer_lock_slot_invalid")
        actual_counts[str(status)] += 1
        for blocker in blockers:
            actual_blockers[blocker] = actual_blockers.get(blocker, 0) + 1
        slots[slot_digest] = slot
    if set(slots) != expected_slots:
        raise RebindError("viewer_lock_set_slot_set_changed")
    if body.get("status_counts") != actual_counts or body.get("blocker_counts") != {
        key: actual_blockers[key] for key in sorted(actual_blockers)
    }:
        raise RebindError("viewer_lock_set_counts_invalid")
    return slots, {
        "registry_digest": registry_digest,
        "replay_summary_digest": replay_digest,
        "status_counts": actual_counts,
        "blocker_counts": {key: actual_blockers[key] for key in sorted(actual_blockers)},
    }


def _validate_static_capabilities(
    pipeline_root: Path,
    rows: Mapping[str, Mapping[str, Any]],
    viewer_slots: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        mapping = load_compiled_selection_capability_mapping()
    except ViewerCapabilityMappingError as exc:
        raise RebindError("static_viewer_capability_mapping_unavailable") from exc
    profiles = mapping["profiles"]
    covered_leaves: set[str] = set()
    by_profile: dict[str, int] = {}
    for slot_digest, row in rows.items():
        lock = viewer_slots[slot_digest]
        required, _ = _required_operations(pipeline_root, row)
        hint = _mapping(row.get("viewer_hint"), "compiled_row_viewer_hint")
        source_viewer = hint.get("source_viewer")
        if not isinstance(source_viewer, str) or not source_viewer:
            raise RebindError("compiled_row_viewer_hint_invalid")
        formats = _asset_formats(row)
        family = lock.get("viewer_family")
        candidates: list[tuple[str, Mapping[str, Any]]] = []
        for profile_id, raw_profile in profiles.items():
            profile = _mapping(raw_profile, "static_viewer_capability_profile")
            if (
                profile.get("viewer_family") == family
                and row["target_leaf"] in set(profile.get("leaf_coverage", []))
                and source_viewer in set(profile.get("source_viewers", []))
                and formats.issubset(set(profile.get("asset_formats", [])))
            ):
                candidates.append((profile_id, profile))
        if len(candidates) != 1:
            raise RebindError("static_viewer_capability_coverage_invalid")
        profile_id, profile = candidates[0]
        profile_operations = set(profile.get("controlled_operations", []))
        lock_operations = set(lock["allowed_operations"])
        if not required.issubset(lock_operations):
            raise RebindError("viewer_lock_missing_current_leaf_operation")
        if not lock_operations.issubset(profile_operations):
            raise RebindError("viewer_lock_operation_outside_static_capability_profile")
        covered_leaves.add(str(row["target_leaf"]))
        by_profile[profile_id] = by_profile.get(profile_id, 0) + 1
    return {
        "mapping_id": str(mapping["mapping"]["id"]),
        "mapping_digest": str(mapping["content_digest"]),
        "covered_target_leaf_count": len(covered_leaves),
        "profile_slot_counts": {profile: by_profile[profile] for profile in sorted(by_profile)},
    }


def _inventories_by_digest(paths: WorkspacePaths, requested: set[str]) -> dict[str, dict[str, Any]]:
    """Load every requested private inventory once instead of N x full scans."""

    root = paths.private / "measurement" / "inventories"
    if not root.is_dir():
        raise RebindError("parent_inventory_entry_unavailable")
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            loaded = load_scene_inventory(paths, path)
        except SceneInventoryError:
            continue
        inventory_digest = str(loaded["content_digest"])
        if inventory_digest not in requested:
            continue
        if inventory_digest in found:
            raise RebindError("parent_inventory_entry_ambiguous")
        found[inventory_digest] = loaded
    if set(found) != requested:
        raise RebindError("parent_inventory_entry_unavailable")
    return found


def _inventory_bindings(
    paths: WorkspacePaths,
    inventory_batch: Mapping[str, Any],
    *,
    parent_batch_digest: str,
    registry_digest: str,
    parent_rows: Mapping[str, Mapping[str, Any]],
    current_rows: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    body = _body(inventory_batch, "inventory_batch")
    leak_scan = _mapping(body.get("leak_scan"), "inventory_batch_leak_scan")
    if (
        inventory_batch.get("status") != "ready"
        or body.get("selection_batch_digest") != parent_batch_digest
        or body.get("scene_registry_digest") != registry_digest
        or body.get("total") != 60
        or body.get("ready_count") != 60
        or body.get("blocked_count") != 0
        or body.get("mapping_complete") is not True
        or leak_scan.get("status") != "passed"
    ):
        raise RebindError("parent_inventory_not_complete")
    results = body.get("results")
    if not isinstance(results, list) or len(results) != 60:
        raise RebindError("parent_inventory_invalid")
    pending: list[tuple[str, str, dict[str, Any]]] = []
    source_records: set[str] = set()
    inventory_digests: set[str] = set()
    for raw in results:
        result = _mapping(raw, "parent_inventory_result")
        if result.get("status") != "ready":
            raise RebindError("parent_inventory_not_complete")
        try:
            source_record_digest = require_digest(result.get("source_record_digest"), "parent_inventory_source_record")
            inventory_digest = require_digest(result.get("inventory_digest"), "parent_inventory_digest")
        except Exception as exc:
            raise RebindError("parent_inventory_invalid") from exc
        if source_record_digest in source_records:
            raise RebindError("parent_inventory_duplicate_source_record")
        if inventory_digest in inventory_digests:
            raise RebindError("parent_inventory_duplicate_entry")
        source_records.add(source_record_digest)
        inventory_digests.add(inventory_digest)
        pending.append((source_record_digest, inventory_digest, result))

    inventories = _inventories_by_digest(paths, inventory_digests)
    slots: set[str] = set()
    for source_record_digest, inventory_digest, result in pending:
        loaded = inventories[inventory_digest]
        item = _mapping(loaded.get("body"), "parent_inventory_entry")
        if (
            loaded.get("content_digest") != inventory_digest
            or item.get("source_record_digest") != source_record_digest
            or item.get("scene_registry_digest") != registry_digest
            or item.get("anonymous_scene_ref") != result.get("anonymous_scene_ref")
        ):
            raise RebindError("parent_inventory_entry_binding_invalid")
        try:
            slot_digest = require_digest(item.get("selection_slot_digest"), "parent_inventory_slot_digest")
        except Exception as exc:
            raise RebindError("parent_inventory_entry_binding_invalid") from exc
        parent = parent_rows.get(slot_digest)
        current = current_rows.get(slot_digest)
        if parent is None or current is None or item.get("target_leaf") != parent.get("target_leaf"):
            raise RebindError("parent_inventory_slot_leaf_mismatch")
        if parent.get("target_leaf") != current.get("target_leaf"):
            raise RebindError("current_inventory_slot_leaf_mismatch")
        slots.add(slot_digest)
    if slots != set(parent_rows):
        raise RebindError("parent_inventory_slot_set_changed")
    return source_records


def _validate_source_handoff(handoff: Mapping[str, Any], *, inventory_source_records: set[str]) -> str:
    body = _body(handoff, "source_handoff")
    records = body.get("records")
    if not isinstance(records, list) or len(records) != 60:
        raise RebindError("source_handoff_invalid")
    handoff_records: set[str] = set()
    for raw in records:
        record = _mapping(raw, "source_handoff_record")
        try:
            record_digest = require_digest(record.get("record_digest"), "source_handoff_record_digest")
        except Exception as exc:
            raise RebindError("source_handoff_invalid") from exc
        if record_digest in handoff_records:
            raise RebindError("source_handoff_duplicate_record")
        handoff_records.add(record_digest)
    if handoff_records != inventory_source_records:
        raise RebindError("source_handoff_inventory_record_set_mismatch")
    try:
        return require_digest(body.get("source_import_digest"), "source_handoff_import_digest")
    except Exception as exc:
        raise RebindError("source_handoff_invalid") from exc


def _assert_sanitized_output(value: Any, location: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RebindError("rebind_output_invalid")
            if key.casefold() in _SAFE_OUTPUT_KEY_PARTS:
                raise RebindError(f"rebind_output_forbidden_{location}")
            _assert_sanitized_output(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_sanitized_output(child, f"{location}[{index}]")


def build_rebind(
    *,
    workspace: Path,
    pipeline_root: Path,
    parent_batch_manifest: Path,
    current_batch_manifest: Path,
    inventory_batch: Path,
    viewer_lock_set: Path,
    source_handoff: Path,
) -> dict[str, Any]:
    """Create one immutable, parent-evidence-only config rebind artifact."""

    paths = WorkspacePaths.from_value(workspace)
    pipeline_root = pipeline_root.expanduser().resolve()
    if not pipeline_root.is_dir():
        raise RebindError("pipeline_root_unavailable")
    parent = _private_envelope(paths, parent_batch_manifest, kind=_BATCH_KIND, label="parent_batch")
    current = _private_envelope(paths, current_batch_manifest, kind=_BATCH_KIND, label="current_batch")
    if parent["content_digest"] == current["content_digest"]:
        raise RebindError("current_batch_is_not_a_new_manifest")
    parent_body, parent_rows = _batch_rows(parent, "parent_batch")
    current_body, current_rows = _batch_rows(current, "current_batch")
    config_rebinds = _rebind_diff(parent_body, current_body, parent_rows, current_rows)
    invariants = _source_and_mapping_invariants(parent_body)
    if invariants != _source_and_mapping_invariants(current_body):
        raise RebindError("source_or_taxonomy_mapping_changed")

    lock_set = _private_envelope(paths, viewer_lock_set, kind=_VIEWER_LOCK_SET_KIND, label="viewer_lock_set")
    viewer_slots, viewer_summary = _viewer_lock_slots(
        lock_set,
        parent_batch_digest=str(parent["content_digest"]),
        expected_slots=set(parent_rows),
    )
    capability_summary = _validate_static_capabilities(pipeline_root, current_rows, viewer_slots)

    inventory = _private_envelope(paths, inventory_batch, kind=_INVENTORY_BATCH_KIND, label="inventory_batch")
    inventory_source_records = _inventory_bindings(
        paths,
        inventory,
        parent_batch_digest=str(parent["content_digest"]),
        registry_digest=viewer_summary["registry_digest"],
        parent_rows=parent_rows,
        current_rows=current_rows,
    )
    handoff = _private_envelope(paths, source_handoff, kind=_SOURCE_HANDOFF_KIND, label="source_handoff")
    source_import_digest = _validate_source_handoff(handoff, inventory_source_records=inventory_source_records)

    config_changes = {
        leaf: values
        for leaf, values in config_rebinds.items()
        if values["parent"] != values["current"]
    }
    body = {
        "rebind_version": 1,
        "scope": "configuration_only_parent_evidence_rebind",
        "parent_selection_batch_digest": parent["content_digest"],
        "current_selection_batch_digest": current["content_digest"],
        "selection_invariants": invariants,
        "source_handoff_import_digest": source_import_digest,
        "slot_count": len(parent_rows),
        "target_leaf_config_digests": config_rebinds,
        "changed_target_leaf_config_digests": config_changes,
        "parent_evidence": {
            "scene_registry_digest": viewer_summary["registry_digest"],
            "viewer_replay_summary_digest": viewer_summary["replay_summary_digest"],
            "viewer_lock_set_digest": lock_set["content_digest"],
            "inventory_batch_digest": inventory["content_digest"],
            "source_handoff_digest": handoff["content_digest"],
        },
        "parent_inventory": {
            "ready_count": 60,
            "blocked_count": 0,
            "mapping_complete": True,
            "leak_scan_status": "passed",
            "source_record_count": len(inventory_source_records),
        },
        "parent_viewer": {
            "status_counts": viewer_summary["status_counts"],
            "blocker_counts": viewer_summary["blocker_counts"],
            "candidate_handoff_capable_count": viewer_summary["status_counts"]["attested"],
            "blocked_handoff_count": viewer_summary["status_counts"]["blocked"]
            + viewer_summary["status_counts"]["failed"],
        },
        "current_static_viewer_capability_mapping": capability_summary,
        "new_batch_scene_handoff_required": True,
        "fresh_controlled_viewer_evidence_required": True,
        "provider_execution_eligible": False,
        "prepare_eligible": False,
        "release_eligible": False,
        "requirements_remaining": [
            "fresh_current_batch_controlled_scene_handoff",
            "fresh_current_batch_controlled_viewer_evidence",
            "all_current_batch_slots_attested",
            "provider_execution_authorization",
            "independent_verification_and_human_review",
        ],
    }
    _assert_sanitized_output(body)
    identifier = f"v32-config-rebind-{_short(str(parent['content_digest']))}-{_short(str(current['content_digest']))}"
    envelope = make_envelope(
        kind=_REBIND_KIND,
        identifier=identifier,
        status="parent_evidence_rebound_new_scene_handoff_pending",
        body=body,
        producer="qagen/0.1.0:v32_config_only_rebind",
    )
    output = paths.private / "selection-rebinds" / f"{_short(envelope['content_digest'])}.rebind.json"
    if output.exists():
        raise RebindError("rebind_artifact_already_exists")
    atomic_write_json(output, envelope, private=True)
    return {
        "status": envelope["status"],
        "content_digest": envelope["content_digest"],
        "parent_selection_batch_digest": parent["content_digest"],
        "current_selection_batch_digest": current["content_digest"],
        "slot_count": len(parent_rows),
        "changed_target_leaf_config_count": len(config_changes),
        "candidate_handoff_capable_count": viewer_summary["status_counts"]["attested"],
        "blocked_handoff_count": viewer_summary["status_counts"]["blocked"] + viewer_summary["status_counts"]["failed"],
        "prepare_eligible": False,
        "provider_execution_eligible": False,
        "release_eligible": False,
    }


def _absolute(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=_absolute)
    parser.add_argument("--pipeline-root", required=True, type=_absolute)
    parser.add_argument("--parent-batch-manifest", required=True, type=_absolute)
    parser.add_argument("--current-batch-manifest", required=True, type=_absolute)
    parser.add_argument("--inventory-batch", required=True, type=_absolute)
    parser.add_argument("--viewer-lock-set", required=True, type=_absolute)
    parser.add_argument("--source-handoff", required=True, type=_absolute)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_rebind(
        workspace=args.workspace,
        pipeline_root=args.pipeline_root,
        parent_batch_manifest=args.parent_batch_manifest,
        current_batch_manifest=args.current_batch_manifest,
        inventory_batch=args.inventory_batch,
        viewer_lock_set=args.viewer_lock_set,
        source_handoff=args.source_handoff,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a script.
    try:
        raise SystemExit(main())
    except RebindError as exc:
        print(json.dumps({"status": "failed", "error_class": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2) from exc
