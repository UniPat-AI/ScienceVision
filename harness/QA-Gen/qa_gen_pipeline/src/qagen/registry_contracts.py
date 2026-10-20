"""Canonical contracts for immutable workspace registry indexes.

The seed synchronizer and knowledge approval flow publish a small set of
content-addressed artifacts.  This module is deliberately shared by their
writers and by :mod:`qagen.config.compiler` so a profile cannot reinterpret a
registry output with a weaker, parallel schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import require_digest, verify_envelope
from .errors import InputError


WORKSPACE_REGISTRY_MODE = "workspace_registry"
_SEED_STATUSES = {"verified", "minor_fix", "factual_error", "unscorable", "fixture_verified"}
_KNOWLEDGE_STATES = {"approved", "superseded"}


def require_workspace_relative_path(value: Any, field: str) -> str:
    """Require one canonical, workspace-relative POSIX path.

    Registry paths are identities inside a workspace, not host paths or URLs.
    Keeping that distinction here prevents a frozen index from smuggling a
    later live lookup into compilation.
    """

    if not isinstance(value, str) or not value or value.startswith(("/", "\\", "http://", "https://", "feishu://")):
        raise InputError(f"{field} must be a non-empty workspace-relative path")
    if "\\" in value:
        raise InputError(f"{field} must use POSIX path separators")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InputError(f"{field} must be a canonical workspace-relative path")
    return value


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InputError(f"{field} must be an object")
    return dict(value)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputError(f"{field} must be a non-empty string")
    return value


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise InputError(f"{field} must be boolean")
    return value


def _no_unknown_fields(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise InputError(f"{field} has unknown field(s): {', '.join(unknown)}")


def validate_seed_index(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the canonical immutable ``seed_index`` envelope."""

    envelope = verify_envelope(value, kind="seed_index")
    if envelope["id"] == "current":
        raise InputError("seed index id must identify a frozen artifact, not current")
    body = _mapping(envelope["body"], "seed index body")
    required = {
        "mode",
        "inventory_digest",
        "seed_catalog_digest",
        "catalog_path",
        "cards_digest",
        "cards_path",
        "package_name",
        "source_spec_path",
        "source_spec_digest",
        "local_only",
        "production_eligible",
        "observed_versions",
        "resolution_ref",
        "eligible",
        "lesson_paths",
        "entries",
    }
    missing = sorted(required - set(body))
    if missing:
        raise InputError(f"seed index body missing fields: {', '.join(missing)}")
    _no_unknown_fields(body, required, "seed index body")
    if body["mode"] != WORKSPACE_REGISTRY_MODE:
        raise InputError("seed index has the wrong registry mode")
    for field in ("inventory_digest", "seed_catalog_digest", "cards_digest", "source_spec_digest"):
        require_digest(body[field], f"seed index {field}")
    require_workspace_relative_path(body["catalog_path"], "seed index catalog_path")
    require_workspace_relative_path(body["cards_path"], "seed index cards_path")
    _string(body["package_name"], "seed index package_name")
    _string(body["source_spec_path"], "seed index source_spec_path")
    local_only = _bool(body["local_only"], "seed index local_only")
    production_eligible = _bool(body["production_eligible"], "seed index production_eligible")
    if local_only and production_eligible:
        raise InputError("seed index cannot be both local-only and production-eligible")
    eligible = _bool(body["eligible"], "seed index eligible")
    expected_status = "eligible" if eligible else "version_resolution_pending"
    if envelope["status"] != expected_status:
        raise InputError("seed index status does not match its eligibility")
    if not isinstance(body["observed_versions"], Mapping):
        raise InputError("seed index observed_versions must be an object")
    resolution_ref = body["resolution_ref"]
    if resolution_ref is not None:
        require_digest(resolution_ref, "seed index resolution_ref")
    lesson_paths = body["lesson_paths"]
    if not isinstance(lesson_paths, list) or not all(isinstance(path, str) for path in lesson_paths):
        raise InputError("seed index lesson_paths must be a list of paths")
    for index, path in enumerate(lesson_paths):
        require_workspace_relative_path(path, f"seed index lesson_paths[{index}]")
    entries = body["entries"]
    if not isinstance(entries, list):
        raise InputError("seed index entries must be a list")
    seen_refs: set[str] = set()
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, f"seed index entries[{index}]")
        required_entry = {"seed_ref", "leaf_id", "domain", "subdomain", "status", "tags"}
        missing_entry = sorted(required_entry - set(entry))
        if missing_entry:
            raise InputError(f"seed index entry missing fields: {', '.join(missing_entry)}")
        _no_unknown_fields(entry, required_entry, f"seed index entries[{index}]")
        seed_ref = _string(entry["seed_ref"], "seed index entry seed_ref")
        if seed_ref in seen_refs:
            raise InputError("seed index has duplicate seed_ref values")
        seen_refs.add(seed_ref)
        domain = _string(entry["domain"], "seed index entry domain")
        subdomain = _string(entry["subdomain"], "seed index entry subdomain")
        if _string(entry["leaf_id"], "seed index entry leaf_id") != f"{domain}.{subdomain}":
            raise InputError("seed index entry leaf_id does not match domain/subdomain")
        if entry["status"] not in _SEED_STATUSES:
            raise InputError("seed index entry has an unsupported status")
        tags = entry["tags"]
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
            raise InputError("seed index entry tags must be non-empty strings")
    return envelope


def validate_knowledge_index(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the canonical immutable ``knowledge_index`` envelope."""

    envelope = verify_envelope(value, kind="knowledge_index")
    if envelope["id"] == "current":
        raise InputError("knowledge index id must identify a frozen artifact, not current")
    body = _mapping(envelope["body"], "knowledge index body")
    required = {"mode", "seed_index_digest", "seed_catalog_digest", "entries"}
    missing = sorted(required - set(body))
    if missing:
        raise InputError(f"knowledge index body missing fields: {', '.join(missing)}")
    _no_unknown_fields(body, required, "knowledge index body")
    if body["mode"] != WORKSPACE_REGISTRY_MODE:
        raise InputError("knowledge index has the wrong registry mode")
    require_digest(body["seed_index_digest"], "knowledge index seed_index_digest")
    require_digest(body["seed_catalog_digest"], "knowledge index seed_catalog_digest")
    if envelope["status"] not in {"ready", "contains_superseded_entries"}:
        raise InputError("knowledge index has an unsupported status")
    entries = body["entries"]
    if not isinstance(entries, Mapping):
        raise InputError("knowledge index entries must be an object")
    for leaf_id, raw_entry in entries.items():
        _string(leaf_id, "knowledge index leaf id")
        entry = _mapping(raw_entry, f"knowledge index entry {leaf_id}")
        required_entry = {
            "state",
            "snapshot_digest",
            "snapshot_path",
            "review_digest",
            "review_path",
            "draft_digest",
            "input_digest",
            "seed_catalog_digest",
            "source_digests",
            "compiler_digest",
            "local_only",
            "production_eligible",
        }
        allowed_entry = required_entry | {"superseded_reason"}
        missing_entry = sorted(required_entry - set(entry))
        if missing_entry:
            raise InputError(f"knowledge index entry {leaf_id} missing fields: {', '.join(missing_entry)}")
        _no_unknown_fields(entry, allowed_entry, f"knowledge index entry {leaf_id}")
        if entry["state"] not in _KNOWLEDGE_STATES:
            raise InputError(f"knowledge index entry {leaf_id} has an unsupported state")
        if entry["state"] == "superseded" and not isinstance(entry.get("superseded_reason"), str):
            raise InputError(f"knowledge index entry {leaf_id} has no superseded_reason")
        if entry["state"] == "approved" and "superseded_reason" in entry:
            raise InputError(f"knowledge index entry {leaf_id} cannot be approved and superseded")
        for field in (
            "snapshot_digest",
            "review_digest",
            "draft_digest",
            "input_digest",
            "seed_catalog_digest",
            "compiler_digest",
        ):
            require_digest(entry[field], f"knowledge index entry {leaf_id} {field}")
        require_workspace_relative_path(entry["snapshot_path"], f"knowledge index entry {leaf_id} snapshot_path")
        require_workspace_relative_path(entry["review_path"], f"knowledge index entry {leaf_id} review_path")
        sources = entry["source_digests"]
        if not isinstance(sources, Mapping) or not sources:
            raise InputError(f"knowledge index entry {leaf_id} source_digests must be a non-empty object")
        for source_id, source_digest in sources.items():
            _string(source_id, f"knowledge index entry {leaf_id} source id")
            require_digest(source_digest, f"knowledge index entry {leaf_id} source digest")
        local_only = _bool(entry["local_only"], f"knowledge index entry {leaf_id} local_only")
        production_eligible = _bool(
            entry["production_eligible"], f"knowledge index entry {leaf_id} production_eligible"
        )
        if local_only and production_eligible:
            raise InputError(f"knowledge index entry {leaf_id} cannot be both local-only and production-eligible")
    return envelope


__all__ = [
    "WORKSPACE_REGISTRY_MODE",
    "require_workspace_relative_path",
    "validate_knowledge_index",
    "validate_seed_index",
]
