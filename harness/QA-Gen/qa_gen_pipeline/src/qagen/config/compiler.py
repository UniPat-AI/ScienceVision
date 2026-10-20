"""Compile a vNext run profile from local, schema-validated lock inputs."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, digest_bytes, make_envelope, require_digest, verify_envelope
from ..errors import InputError, StaleInputError
from ..paths import WorkspacePaths, require_within
from ..resource_paths import PackageResourceError, repository_reference_path
from ..registry_contracts import (
    require_workspace_relative_path,
    validate_knowledge_index,
    validate_seed_index,
)
from ..routing import load_model_routing
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from ..viewer.profile import load_viewer_profile, revalidate_viewer_profile
from ..viewer.scene_registry import SceneRegistry
from ..workers import verify_routing_policy


class ConfigCompilerError(InputError):
    """A profile input is malformed, untrusted, or fails its JSON Schema."""


_LAYER_METADATA = {
    "schema_version",
    "kind",
    "id",
    "domain",
    "domain_status",
    "status",
    "production_status",
    "readiness",
    "legacy_aliases",
    "blocked_reasons",
    "inputs",
    "release",
    "enabled_leaves",
    "blocked_leaves",
    "mode",
}

_GLOBAL_SOLVER_FORBIDDEN_CAPABILITIES = {
    "network",
    "asset_file_read",
    "raw_asset_metadata",
    "raw_asset_read",
    "arbitrary_compute",
    "unrestricted_compute",
}
_REQUIRED_QUALITY_FLAGS = {
    "require_reference_answer",
    "require_reasoning",
    "require_field_rubric",
    "require_independent_viewer_evidence",
}
_REQUIRED_RUBRIC_THRESHOLDS = {
    "authenticity": (2, 2),
    "correctness": (1, 1),
    "three_d_relevance": (1, 1),
}

_DEFAULT_PLANNING_POLICY = {
    "default_count": 1,
    "quota": 1,
    "batch": None,
    "per_work_item_budget": {"max_candidates": 1, "max_revisions": 1},
}

_CHIRALITY_PROVIDER_PROFILE_IDS = {
    "chirality_provider_pilot",
    "chirality_workspace_knowledge_pilot",
}
_CHIRALITY_WORKSPACE_BINDING_KIND = "chirality_scene_registry_and_viewer_profile"


def _local_file(value: str | Path, field: str) -> Path:
    raw = str(value)
    if raw.startswith(("http://", "https://", "feishu://")):
        raise ConfigCompilerError(f"{field} must be a local file, not a live URL")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ConfigCompilerError(f"{field} is not a local file: {path}")
    return path


def _read_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            value = read_json(path)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigCompilerError(f"cannot read configuration document {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ConfigCompilerError(f"configuration document is not an object: {path}")
    return dict(value)


def _find_config_root(profile_path: Path) -> Path:
    for parent in (profile_path.parent, *profile_path.parents):
        if (parent / "schemas").is_dir():
            return parent
    raise ConfigCompilerError(f"cannot find configs/schemas above profile {profile_path}")


def _validate(value: Mapping[str, Any], config_root: Path, schema_name: str, label: str) -> None:
    schema_path = config_root / "schemas" / schema_name
    if not schema_path.is_file():
        raise ConfigCompilerError(f"missing required schema {schema_path}")
    try:
        schema = read_json(schema_path)
        errors = sorted(Draft7Validator(schema).iter_errors(value), key=lambda error: list(error.absolute_path))
    except Exception as exc:
        raise ConfigCompilerError(f"cannot validate {label} against {schema_name}: {exc}") from exc
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ConfigCompilerError(f"{label} schema validation failed at {location}: {error.message}")


def _resolve_declared(base_path: Path, raw: Any, repo_root: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigCompilerError(f"{label} must be a non-empty local relative path")
    if raw.startswith(("http://", "https://", "feishu://")):
        raise ConfigCompilerError(f"{label} cannot use a live URL")
    candidate = (base_path.parent / raw).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ConfigCompilerError(f"{label} escapes repository root") from exc
    if not candidate.is_file():
        raise ConfigCompilerError(f"{label} does not exist: {candidate}")
    return candidate


def _document_lock(label: str, path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    return {"label": label, "path": str(path), "target": "document", "digest": digest(dict(value))}


def _payload_lock(label: str, path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"label": label, "path": str(path), "target": "fixture_payload", "digest": digest(dict(payload))}


def _bytes_lock(label: str, path: Path) -> dict[str, Any]:
    return {"label": label, "path": str(path), "target": "bytes", "digest": digest_bytes(path.read_bytes())}


def _fixture_alias(fixture: Mapping[str, Any]) -> str:
    kind = fixture.get("kind")
    if not isinstance(kind, str) or not kind.startswith("qagen_") or not kind.endswith("_fixture"):
        raise ConfigCompilerError("fixture kind cannot be converted into a locked reference")
    return "fixture-" + kind[len("qagen_") : -len("_fixture")].replace("_", "-")


def _load_fixture_source(
    source: Mapping[str, Any],
    source_path: Path,
    repo_root: Path,
    label: str,
    *,
    source_lock_document: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], Path, dict[str, Any], list[dict[str, Any]]]:
    fixture_path = _resolve_declared(source_path, source.get("fixture_path"), repo_root, f"{label}.fixture_path")
    fixture = _read_document(fixture_path)
    payload = fixture.get("fixture_payload")
    declared = fixture.get("fixture_payload_digest")
    source_declared = source.get("fixture_payload_digest")
    if not isinstance(payload, Mapping):
        raise ConfigCompilerError(f"{label} fixture_payload must be an object")
    require_digest(declared, f"{label} fixture payload digest")
    require_digest(source_declared, f"{label} source fixture payload digest")
    actual = digest(dict(payload))
    if actual != declared or actual != source_declared:
        raise ConfigCompilerError(f"{label} fixture payload digest does not match its lock")
    fixture_meta = fixture.get("fixture")
    if not isinstance(fixture_meta, Mapping):
        raise ConfigCompilerError(f"{label} fixture metadata is missing")
    if source.get("local_only") is not True or fixture_meta.get("local_only") is not True:
        raise ConfigCompilerError(f"{label} must explicitly be local-only in this skeleton")
    locks = [_payload_lock(f"{label}_fixture", fixture_path, payload)]
    if source_lock_document is not None:
        locks.insert(0, _document_lock(f"{label}_source", source_path, source_lock_document))
    return fixture, fixture_path, dict(payload), locks


def _load_locked_document_source(
    source: Mapping[str, Any],
    source_path: Path,
    repo_root: Path,
    label: str,
) -> tuple[dict[str, Any], Path, dict[str, Any], list[dict[str, Any]]]:
    """Load a non-fixture source whose payload is itself digest-pinned.

    This path exists for production registry integration tests and future
    reviewed inputs.  It deliberately accepts only a local, declared document;
    callers never discover a newer source by scanning a directory or URL.
    """

    document_path = _resolve_declared(
        source_path, source.get("document_path"), repo_root, f"{label}.document_path"
    )
    document = _read_document(document_path)
    expected = source.get("document_digest")
    try:
        require_digest(expected, f"{label} document digest")
    except InputError as exc:
        raise ConfigCompilerError(str(exc)) from exc
    if digest(document) != expected:
        raise ConfigCompilerError(f"{label} document digest does not match its lock")
    local_only = source.get("local_only")
    production_eligible = source.get("production_eligible")
    if not isinstance(local_only, bool) or not isinstance(production_eligible, bool):
        raise ConfigCompilerError(f"{label} local_only and production_eligible must be boolean")
    if local_only and production_eligible:
        raise ConfigCompilerError(f"{label} cannot be both local-only and production-eligible")
    return (
        dict(source),
        document_path,
        document,
        [
            _document_lock(f"{label}_source", source_path, source),
            _document_lock(f"{label}_document", document_path, document),
        ],
    )


def _load_input_source(
    source: Mapping[str, Any],
    source_path: Path,
    repo_root: Path,
    label: str,
) -> dict[str, Any]:
    """Normalize fixture and digest-locked source forms for compilation."""

    if "fixture_path" in source:
        fixture, path, payload, locks = _load_fixture_source(
            source,
            source_path,
            repo_root,
            label,
            source_lock_document=source,
        )
        return {
            "alias": _fixture_alias(fixture),
            "digest": fixture["fixture_payload_digest"],
            "payload": payload,
            "local_only": True,
            "production_eligible": False,
            "path": path,
            "locks": locks,
        }
    if source.get("source_mode") == "locked_document":
        document_source, path, payload, locks = _load_locked_document_source(
            source, source_path, repo_root, label
        )
        alias = document_source.get("id")
        if not isinstance(alias, str) or not alias:
            raise ConfigCompilerError(f"{label} source id is missing")
        return {
            "alias": alias,
            "digest": document_source["document_digest"],
            "payload": payload,
            "local_only": document_source["local_only"],
            "production_eligible": document_source["production_eligible"],
            "path": path,
            "locks": locks,
        }
    raise ConfigCompilerError(f"{label} has no supported local source mode")


def _deep_merge(left: Any, right: Any) -> Any:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        merged = {key: deepcopy(value) for key, value in left.items()}
        for key, value in right.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else deepcopy(value)
        return merged
    return deepcopy(right)


def _layer(document: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in document.items() if key not in _LAYER_METADATA}


def _reference_digest(reference: Any, field: str) -> str:
    if not isinstance(reference, str) or "@" not in reference:
        raise ConfigCompilerError(f"{field} is not a locked reference")
    digest_value = reference.rsplit("@", 1)[1]
    require_digest(digest_value, field)
    return digest_value


def _find_repo_file(profile_path: Path, repo_relative: str) -> Path:
    for parent in profile_path.parents:
        candidate = (parent / repo_relative).resolve()
        if candidate.is_file():
            return candidate
    try:
        return repository_reference_path(repo_relative)
    except PackageResourceError as exc:
        raise ConfigCompilerError(f"cannot resolve repo-relative rubric document: {repo_relative}") from exc


def _resolve_workspace_path(paths: WorkspacePaths, raw: Any, label: str) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith(("/", "http://", "https://", "feishu://")):
        raise ConfigCompilerError(f"{label} must be a non-empty workspace-relative path")
    candidate = (paths.root / raw).resolve()
    try:
        candidate.relative_to(paths.root)
    except ValueError as exc:
        raise ConfigCompilerError(f"{label} escapes workspace root") from exc
    return candidate


def _artifact_lock(label: str, path: Path, artifact: Mapping[str, Any]) -> dict[str, Any]:
    lock = _bytes_lock(label, path)
    artifact_digest = artifact.get("content_digest")
    artifact_kind = artifact.get("kind")
    require_digest(artifact_digest, f"{label} artifact digest")
    if not isinstance(artifact_kind, str) or not artifact_kind:
        raise ConfigCompilerError(f"{label} artifact kind is missing")
    lock["artifact_digest"] = artifact_digest
    lock["artifact_kind"] = artifact_kind
    return lock


def _verified_envelope(path: Path, *, kind: str, label: str) -> dict[str, Any]:
    value = _read_document(path)
    try:
        return verify_envelope(value, kind=kind)
    except InputError as exc:
        raise ConfigCompilerError(f"{label} is not a valid {kind} envelope: {exc}") from exc


def _workspace_artifact_reference(
    paths: WorkspacePaths,
    raw: Any,
    label: str,
    *,
    kind: str,
) -> tuple[Path, str]:
    """Resolve an exact frozen registry artifact declared by a profile."""

    if not isinstance(raw, Mapping):
        raise ConfigCompilerError(f"{label} must be an object with path and content_digest")
    unknown = sorted(set(raw) - {"path", "content_digest"})
    if unknown:
        raise ConfigCompilerError(f"{label} has unknown field(s): {', '.join(unknown)}")
    try:
        raw_path = require_workspace_relative_path(raw.get("path"), f"{label}.path")
        expected_digest = require_digest(raw.get("content_digest"), f"{label}.content_digest")
    except InputError as exc:
        raise ConfigCompilerError(str(exc)) from exc
    path = _resolve_workspace_path(paths, raw_path, f"{label}.path")
    mutable_aliases = {
        (paths.catalog / "current.json").resolve(),
        (paths.knowledge / "index.yaml").resolve(),
    }
    if path in mutable_aliases:
        raise ConfigCompilerError(f"{label} must reference a frozen {kind} artifact, not a mutable current alias")
    if not path.is_file():
        raise ConfigCompilerError(f"{label} does not exist: {path}")
    return path, expected_digest


def _parse_workspace_seed_index(
    paths: WorkspacePaths,
    index_reference: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    index_path, expected_index_digest = _workspace_artifact_reference(
        paths,
        index_reference,
        "profile.inputs.workspace_registry.seed_index",
        kind="seed_index",
    )
    try:
        index = validate_seed_index(_read_document(index_path))
    except InputError as exc:
        raise ConfigCompilerError(f"workspace seed index is not canonical: {exc}") from exc
    if index["content_digest"] != expected_index_digest:
        raise ConfigCompilerError("workspace seed index does not match the profile-pinned content_digest")
    body = index["body"]
    inventory_digest = body.get("inventory_digest")
    catalog_digest = body.get("seed_catalog_digest")
    require_digest(inventory_digest, "workspace seed index inventory_digest")
    require_digest(catalog_digest, "workspace seed index seed_catalog_digest")
    for field in ("eligible", "local_only", "production_eligible"):
        if not isinstance(body.get(field), bool):
            raise ConfigCompilerError(f"workspace seed index {field} must be boolean")
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ConfigCompilerError("workspace seed index entries must be a list")
    records: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise ConfigCompilerError("workspace seed index entry must be an object")
        entry = dict(raw_entry)
        required = ("seed_ref", "leaf_id", "domain", "subdomain", "status", "tags")
        if not all(key in entry for key in required):
            raise ConfigCompilerError("workspace seed index entry is incomplete")
        if not all(isinstance(entry[key], str) and entry[key] for key in required[:-1]):
            raise ConfigCompilerError("workspace seed index entry has an invalid string field")
        if not isinstance(entry["tags"], list) or not all(
            isinstance(tag, str) and tag for tag in entry["tags"]
        ):
            raise ConfigCompilerError("workspace seed index entry tags must be strings")
        if entry["seed_ref"] in seen_refs:
            raise ConfigCompilerError("workspace seed index has duplicate seed_ref values")
        seen_refs.add(entry["seed_ref"])
        records.append(entry)

    locks = [_artifact_lock("seed_index", index_path, index)]
    blockers: list[str] = []
    if index["status"] != "eligible" or body["eligible"] is not True:
        blockers.append("seed_index_not_eligible")
    catalog_path = _resolve_workspace_path(paths, body.get("catalog_path"), "seed index catalog_path")
    if not catalog_path.is_file():
        blockers.append("seed_catalog_missing")
    else:
        catalog = _verified_envelope(catalog_path, kind="seed_catalog", label="workspace seed catalog")
        if catalog["content_digest"] != catalog_digest:
            raise ConfigCompilerError("workspace seed catalog digest does not match its index")
        if catalog["body"].get("inventory_digest") != inventory_digest:
            raise ConfigCompilerError("workspace seed catalog inventory does not match its index")
        if catalog["body"].get("local_only") != body["local_only"]:
            raise ConfigCompilerError("workspace seed catalog local-only flag does not match its index")
        if catalog["body"].get("production_eligible") != body["production_eligible"]:
            raise ConfigCompilerError("workspace seed catalog production eligibility does not match its index")
        locks.append(_artifact_lock("seed_catalog", catalog_path, catalog))
    return (
        {
            "digest": catalog_digest,
            "index_digest": index["content_digest"],
            "payload": {"records": records},
            "local_fixture": body["local_only"],
            "production_eligible": body["production_eligible"],
        },
        locks,
        blockers,
    )


def _parse_static_knowledge_index(
    index: Mapping[str, Any], index_path: Path, repo_root: Path
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    entries = index.get("entries")
    if not isinstance(entries, list):
        raise ConfigCompilerError("knowledge index entries must be a list")
    result: dict[str, dict[str, Any]] = {}
    locks: list[dict[str, Any]] = [_document_lock("knowledge_index", index_path, index)]
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise ConfigCompilerError("knowledge index entry must be an object")
        entry = dict(raw_entry)
        leaf_id = entry.get("leaf_id")
        reference = entry.get("reference")
        path_value = entry.get("path")
        entry_digest = entry.get("digest")
        if not all(isinstance(value, str) and value for value in (leaf_id, reference, path_value, entry_digest)):
            raise ConfigCompilerError("knowledge index entry lacks leaf_id/reference/path/digest")
        require_digest(entry_digest, f"knowledge index digest for {leaf_id}")
        if reference.rsplit("@", 1)[-1] != entry_digest:
            raise ConfigCompilerError(f"knowledge index reference digest does not match entry for {leaf_id}")
        knowledge_path = (index_path.parent / path_value).resolve()
        try:
            knowledge_path.relative_to(repo_root)
        except ValueError as exc:
            raise ConfigCompilerError(f"knowledge path escapes repository root for {leaf_id}") from exc
        if not knowledge_path.is_file():
            raise ConfigCompilerError(f"knowledge snapshot is missing for {leaf_id}: {knowledge_path}")
        snapshot = _read_document(knowledge_path)
        payload = snapshot.get("fixture_payload")
        snapshot_digest = snapshot.get("fixture_digest", snapshot.get("content_digest"))
        if not isinstance(payload, Mapping):
            raise ConfigCompilerError(f"knowledge snapshot payload is missing for {leaf_id}")
        actual = digest(dict(payload))
        if snapshot_digest != actual or entry_digest != actual:
            raise ConfigCompilerError(f"knowledge snapshot digest lock does not match for {leaf_id}")
        if leaf_id in result:
            raise ConfigCompilerError(f"duplicate knowledge index entry for {leaf_id}")
        result[leaf_id] = {
            "reference": reference,
            "digest": entry_digest,
            "state": entry.get("state"),
            "local_fixture": bool(entry.get("local_fixture")),
            "production_eligible": bool(entry.get("production_eligible")),
            "approval_receipt": entry.get("approval_receipt"),
            "payload": dict(payload),
        }
        locks.append(_payload_lock(f"knowledge_snapshot:{leaf_id}", knowledge_path, payload))
    return result, locks


def _parse_workspace_knowledge_index(
    paths: WorkspacePaths,
    index_reference: Mapping[str, Any],
    *,
    seed_index: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str]]:
    index_path, expected_index_digest = _workspace_artifact_reference(
        paths,
        index_reference,
        "profile.inputs.workspace_registry.knowledge_index",
        kind="knowledge_index",
    )
    try:
        index = validate_knowledge_index(_read_document(index_path))
    except InputError as exc:
        raise ConfigCompilerError(f"workspace knowledge index is not canonical: {exc}") from exc
    if index["content_digest"] != expected_index_digest:
        raise ConfigCompilerError("workspace knowledge index does not match the profile-pinned content_digest")
    body = index["body"]
    seed_index_digest = body.get("seed_index_digest")
    seed_catalog_digest = body.get("seed_catalog_digest")
    require_digest(seed_index_digest, "workspace knowledge index seed_index_digest")
    require_digest(seed_catalog_digest, "workspace knowledge index seed_catalog_digest")
    blockers: list[str] = []
    if index["status"] != "ready":
        blockers.append("knowledge_index_not_ready")
    if seed_index is not None and (
        seed_index_digest != seed_index.get("index_digest")
        or seed_catalog_digest != seed_index.get("digest")
    ):
        blockers.append("knowledge_index_seed_binding_mismatch")
    raw_entries = body.get("entries")
    if not isinstance(raw_entries, Mapping):
        raise ConfigCompilerError("workspace knowledge index entries must be an object")
    locks = [_artifact_lock("knowledge_index", index_path, index)]
    result: dict[str, dict[str, Any]] = {}
    for leaf_id, raw_entry in raw_entries.items():
        if not isinstance(leaf_id, str) or not leaf_id or not isinstance(raw_entry, Mapping):
            raise ConfigCompilerError("workspace knowledge index entry is malformed")
        entry = dict(raw_entry)
        for field in (
            "snapshot_digest",
            "snapshot_path",
            "review_digest",
            "review_path",
            "draft_digest",
            "input_digest",
            "seed_catalog_digest",
            "compiler_digest",
        ):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ConfigCompilerError(f"workspace knowledge entry {leaf_id} lacks {field}")
        for field in (
            "snapshot_digest",
            "review_digest",
            "draft_digest",
            "input_digest",
            "seed_catalog_digest",
            "compiler_digest",
        ):
            require_digest(entry[field], f"workspace knowledge entry {leaf_id} {field}")
        source_digests = entry.get("source_digests")
        if not isinstance(source_digests, Mapping) or not source_digests:
            raise ConfigCompilerError(f"workspace knowledge entry {leaf_id} lacks source_digests")
        for source_id, source_digest in source_digests.items():
            if not isinstance(source_id, str) or not source_id:
                raise ConfigCompilerError(f"workspace knowledge entry {leaf_id} has an invalid source id")
            require_digest(source_digest, f"workspace knowledge entry {leaf_id} source digest")
        if entry["seed_catalog_digest"] != seed_catalog_digest:
            raise ConfigCompilerError(f"workspace knowledge entry {leaf_id} has a stale seed catalog binding")
        snapshot_path = _resolve_workspace_path(
            paths, entry["snapshot_path"], f"workspace knowledge entry {leaf_id} snapshot_path"
        )
        review_path = _resolve_workspace_path(
            paths, entry["review_path"], f"workspace knowledge entry {leaf_id} review_path"
        )
        if not snapshot_path.is_file() or not review_path.is_file():
            raise ConfigCompilerError(f"workspace knowledge entry {leaf_id} has a missing frozen artifact")
        snapshot = _verified_envelope(
            snapshot_path, kind="subdomain_knowledge_snapshot", label=f"knowledge snapshot {leaf_id}"
        )
        review = _verified_envelope(
            review_path, kind="knowledge_review_decision", label=f"knowledge review {leaf_id}"
        )
        if snapshot["content_digest"] != entry["snapshot_digest"]:
            raise ConfigCompilerError(f"workspace knowledge snapshot digest mismatch for {leaf_id}")
        if snapshot["status"] != "approved" or snapshot["body"].get("status") != "approved":
            raise ConfigCompilerError(f"workspace knowledge snapshot is not frozen and approved for {leaf_id}")
        if snapshot["body"].get("id") != leaf_id:
            raise ConfigCompilerError(f"workspace knowledge snapshot leaf mismatch for {leaf_id}")
        if review["content_digest"] != entry["review_digest"] or review["status"] != "approved":
            raise ConfigCompilerError(f"workspace knowledge review is not approved for {leaf_id}")
        bound = review["body"].get("bound_digests")
        if not isinstance(bound, Mapping) or bound.get("draft_digest") != entry["draft_digest"]:
            raise ConfigCompilerError(f"workspace knowledge review does not bind the frozen draft for {leaf_id}")
        if bound.get("input_digest") != entry["input_digest"]:
            raise ConfigCompilerError(f"workspace knowledge review input binding mismatch for {leaf_id}")
        provenance = snapshot["body"].get("provenance")
        review_binding = snapshot["body"].get("review")
        if not isinstance(provenance, Mapping) or not isinstance(review_binding, Mapping):
            raise ConfigCompilerError(f"workspace knowledge snapshot provenance is malformed for {leaf_id}")
        if provenance.get("reviewed_draft_digest") != entry["draft_digest"]:
            raise ConfigCompilerError(f"workspace knowledge snapshot draft binding mismatch for {leaf_id}")
        if provenance.get("input_digest") != entry["input_digest"]:
            raise ConfigCompilerError(f"workspace knowledge snapshot input binding mismatch for {leaf_id}")
        if provenance.get("compiler_digest") != entry["compiler_digest"]:
            raise ConfigCompilerError(f"workspace knowledge snapshot compiler binding mismatch for {leaf_id}")
        if review_binding.get("decision_digest") != review["content_digest"]:
            raise ConfigCompilerError(f"workspace knowledge snapshot review binding mismatch for {leaf_id}")
        if review["body"].get("compile_authorization_ref") != provenance.get("compile_authorization_ref"):
            raise ConfigCompilerError(f"workspace knowledge authorization binding mismatch for {leaf_id}")
        source_digests = entry["source_digests"]
        expert_sources = provenance.get("expert_source_refs", [])
        taxonomy_sources = provenance.get("visual_taxonomy_refs", [])
        feedback_sources = provenance.get("expert_feedback_refs", [])
        if (
            not isinstance(expert_sources, list)
            or not isinstance(taxonomy_sources, list)
            or not isinstance(feedback_sources, list)
        ):
            raise ConfigCompilerError(f"workspace knowledge source provenance is malformed for {leaf_id}")
        snapshot_sources = {
            **{
                f"expert:{item.get('source_id')}": item.get("content_digest")
                for item in expert_sources
                if isinstance(item, Mapping)
            },
            **{
                f"taxonomy:{item.get('source_id')}": item.get("content_digest")
                for item in taxonomy_sources
                if isinstance(item, Mapping)
            },
            **{
                f"feedback:{item.get('source_id')}": item.get("content_digest")
                for item in feedback_sources
                if isinstance(item, Mapping)
            },
        }
        if snapshot_sources != dict(source_digests):
            raise ConfigCompilerError(f"workspace knowledge source bindings mismatch for {leaf_id}")
        if leaf_id in result:
            raise ConfigCompilerError(f"duplicate workspace knowledge index entry for {leaf_id}")
        result[leaf_id] = {
            "reference": f"knowledge-snapshot:{leaf_id}@{entry['snapshot_digest']}",
            "digest": entry["snapshot_digest"],
            "state": entry.get("state"),
            "local_fixture": bool(entry.get("local_only")),
            "production_eligible": bool(entry.get("production_eligible")),
            "approval_receipt": entry["review_digest"],
            "payload": dict(snapshot["body"]),
        }
        locks.extend(
            [
                _artifact_lock(f"knowledge_snapshot:{leaf_id}", snapshot_path, snapshot),
                _artifact_lock(f"knowledge_review:{leaf_id}", review_path, review),
            ]
        )
    return result, locks, blockers


def _workspace_artifact_or_pending(
    raw: Any,
    *,
    label: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Distinguish an immutable workspace reference from an honest pending slot."""

    if raw is None:
        return None, f"{label}_not_explicit"
    if not isinstance(raw, Mapping):
        raise ConfigCompilerError(f"profile.inputs.workspace_registry.{label} must be an artifact reference or pending")
    if raw.get("state") == "pending":
        if set(raw) != {"state"}:
            raise ConfigCompilerError(f"profile.inputs.workspace_registry.{label} pending slot is malformed")
        return None, f"{label}_pending"
    return raw, None


def _parse_workspace_chirality_binding(
    paths: WorkspacePaths,
    registry: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, bool, list[dict[str, Any]], list[str]]:
    """Resolve the concrete host artifacts used in place of fixture sources.

    The checked-in profile can remain a template by carrying honest pending
    slots. Once both values become frozen artifact references, they are loaded
    through their owning contracts and cross-checked before becoming leaf
    locks. No static fixture can silently stand in for either runtime artifact.
    """

    requested = "scene_registry" in registry or "viewer_profile" in registry
    if not requested:
        return None, False, [], []
    scene_raw, scene_blocker = _workspace_artifact_or_pending(
        registry.get("scene_registry"),
        label="workspace_scene_registry",
    )
    profile_raw, profile_blocker = _workspace_artifact_or_pending(
        registry.get("viewer_profile"),
        label="workspace_viewer_profile",
    )
    blockers = [blocker for blocker in (scene_blocker, profile_blocker) if blocker is not None]
    if scene_raw is None or profile_raw is None:
        return None, requested, [], blockers

    scene_path, expected_scene_digest = _workspace_artifact_reference(
        paths,
        scene_raw,
        "profile.inputs.workspace_registry.scene_registry",
        kind="qagen_private_scene_registry",
    )
    profile_path, expected_profile_digest = _workspace_artifact_reference(
        paths,
        profile_raw,
        "profile.inputs.workspace_registry.viewer_profile",
        kind="viewer_profile",
    )
    try:
        scene = SceneRegistry.load(paths, scene_path)
    except InputError as exc:
        raise ConfigCompilerError(f"workspace SceneRegistry is invalid: {exc}") from exc
    if scene.content_digest != expected_scene_digest:
        raise ConfigCompilerError("workspace SceneRegistry does not match the profile-pinned content_digest")
    if not scene.resolution.resolved:
        return (
            None,
            True,
            [_artifact_lock("scene_registry", scene_path, scene.envelope)],
            ["workspace_scene_registry_not_runtime_ready"],
        )
    try:
        viewer_profile = revalidate_viewer_profile(load_viewer_profile(paths, profile_path))
    except InputError as exc:
        raise ConfigCompilerError(f"workspace ViewerProfile is invalid: {exc}") from exc
    if viewer_profile["content_digest"] != expected_profile_digest:
        raise ConfigCompilerError("workspace ViewerProfile does not match the profile-pinned content_digest")
    body = viewer_profile.get("body")
    if not isinstance(body, Mapping):
        raise ConfigCompilerError("workspace ViewerProfile body is malformed")
    required_operations = ["render", "rotate"]
    if (
        viewer_profile.get("status") != "attested_local_only"
        or body.get("local_fixture") is not False
        or body.get("attestation_mode") != "local_chirality_host_contract"
        or body.get("scene_registry_path") != str(scene.path)
        or body.get("scene_registry_digest") != scene.content_digest
        or body.get("allowed_operations") != required_operations
    ):
        raise ConfigCompilerError("workspace ViewerProfile is not bound to the resolved chirality SceneRegistry")
    definition_digest = body.get("definition_digest")
    require_digest(definition_digest, "workspace ViewerProfile definition digest")
    for field in (
        "anonymous_scene_ref_set_digest",
        "host_build_digest",
        "session_evidence_schema_digest",
    ):
        require_digest(body.get(field), f"workspace ViewerProfile {field}")
    capabilities = body.get("capabilities")
    forbidden = body.get("forbidden_capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(value, str) and value for value in capabilities):
        raise ConfigCompilerError("workspace ViewerProfile capabilities are malformed")
    if not isinstance(forbidden, list) or not all(isinstance(value, str) and value for value in forbidden):
        raise ConfigCompilerError("workspace ViewerProfile forbidden capabilities are malformed")
    scene_refs = list(scene.anonymous_scene_refs)
    if len(scene_refs) != 1:
        raise ConfigCompilerError("workspace chirality SceneRegistry must contain exactly one anonymous scene")
    locks = [
        _artifact_lock("scene_registry", scene_path, scene.envelope),
        _artifact_lock("viewer_profile", profile_path, viewer_profile),
    ]
    return (
        {
            "asset_input": {
                "alias": "workspace-scene-registry",
                "digest": scene.content_digest,
                "payload": {
                    "assets": [
                        {
                            "domain": "chemistry",
                            "subdomain": "stereochemistry_chirality",
                            "anonymous_scene_ref": scene_refs[0],
                        }
                    ]
                },
                "local_only": True,
                "production_eligible": False,
                "path": scene_path,
                "locks": [],
            },
            "viewer_input": {
                "alias": "workspace-viewer-profile",
                "digest": definition_digest,
                "payload": {
                    "viewer_id": "molstar-chirality-host-v1",
                    "adapter_id": "molstar-chirality-host-v1",
                    "capabilities": list(capabilities),
                    "forbidden_capabilities": list(forbidden),
                    "session_scope_mode": "attested_local_chirality_host_only",
                },
                "local_only": True,
                "production_eligible": False,
                "path": profile_path,
                "locks": [],
            },
            "scene_registry_digest": scene.content_digest,
            "viewer_profile_digest": viewer_profile["content_digest"],
        },
        True,
        locks,
        [],
    )


def _string_set(value: Any, field: str) -> set[str]:
    """Read a policy list without silently accepting malformed capability data."""
    if value is None:
        return set()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigCompilerError(f"{field} must be a list of non-empty strings")
    return set(value)


def _viewer_operation_modes(viewer: Mapping[str, Any], field: str) -> list[set[str]]:
    """Return baseline-plus-one-mode operation alternatives for a leaf.

    ``required_operations`` remains the baseline shared by every route.  A
    capability mode is an explicit *alternative* chosen by its generation
    strategy, rather than a request to provision every operation across all
    possible routes.
    """

    baseline = _string_set(viewer.get("required_operations"), f"{field}.required_operations")
    if not baseline:
        raise ConfigCompilerError(f"{field}.required_operations cannot be empty")
    raw_modes = viewer.get("capability_modes")
    if raw_modes is None:
        return [baseline]
    if not isinstance(raw_modes, list) or not raw_modes:
        raise ConfigCompilerError(f"{field}.capability_modes must be a non-empty list")
    modes: list[set[str]] = []
    seen_ids: set[str] = set()
    seen_strategies: set[str] = set()
    for index, raw in enumerate(raw_modes):
        if not isinstance(raw, Mapping):
            raise ConfigCompilerError(f"{field}.capability_modes[{index}] must be an object")
        mode_id = raw.get("id")
        if not isinstance(mode_id, str) or not mode_id or mode_id in seen_ids:
            raise ConfigCompilerError(f"{field}.capability_modes has a duplicate or invalid id")
        seen_ids.add(mode_id)
        strategies = _string_set(raw.get("generation_strategies"), f"{field}.capability_modes[{index}].generation_strategies")
        if not strategies or not strategies.issubset({"preserve_intent", "reauthor_on_asset"}):
            raise ConfigCompilerError(f"{field}.capability_modes generation strategies are invalid")
        if seen_strategies.intersection(strategies):
            raise ConfigCompilerError(f"{field}.capability_modes overlap on a generation strategy")
        seen_strategies |= strategies
        operations = _string_set(raw.get("required_operations"), f"{field}.capability_modes[{index}].required_operations")
        if not operations:
            raise ConfigCompilerError(f"{field}.capability_modes required operations cannot be empty")
        modes.append(baseline | operations)
    return modes


def _require_bool(mapping: Mapping[str, Any], key: str, expected: bool, field: str) -> None:
    if mapping.get(key) is not expected:
        raise ConfigCompilerError(f"{field}.{key} must be {str(expected).lower()}")


def _validate_base_contract(base: Mapping[str, Any]) -> set[str]:
    policy = base.get("policy")
    quality = base.get("quality")
    budget = base.get("budget")
    fixture_policy = base.get("fixture_policy")
    if not all(isinstance(value, Mapping) for value in (policy, quality, budget, fixture_policy)):
        raise ConfigCompilerError("base config requires policy, quality, budget, and fixture_policy objects")
    if not _string_set(policy.get("allowed_truth_modes"), "base.policy.allowed_truth_modes"):
        raise ConfigCompilerError("base.policy.allowed_truth_modes cannot be empty")
    forbidden = _string_set(policy.get("solver_forbidden_capabilities"), "base.policy.solver_forbidden_capabilities")
    if not _GLOBAL_SOLVER_FORBIDDEN_CAPABILITIES.issubset(forbidden):
        raise ConfigCompilerError("base policy does not forbid every global solver capability")
    author_exception = policy.get("author_compute_exception")
    if not isinstance(author_exception, Mapping) or author_exception.get("enabled") is not False:
        raise ConfigCompilerError("base policy must disable author compute exceptions by default")
    for flag in _REQUIRED_QUALITY_FLAGS:
        _require_bool(quality, flag, True, "base.quality")
    if budget.get("max_candidates_per_leaf") != 1 or budget.get("max_revisions") != 1:
        raise ConfigCompilerError("base budget must set max_candidates_per_leaf=1 and max_revisions=1")
    _require_bool(fixture_policy, "allow_local_fixture_compile", False, "base.fixture_policy")
    _require_bool(fixture_policy, "never_release_fixture_inputs", True, "base.fixture_policy")
    _require_bool(fixture_policy, "require_explicit_profile_opt_in", True, "base.fixture_policy")
    _require_bool(fixture_policy, "local_only", True, "base.fixture_policy")
    _require_bool(fixture_policy, "production_eligible", False, "base.fixture_policy")
    return forbidden


def _validate_rubric_contract(rubric: Mapping[str, Any]) -> None:
    thresholds = rubric.get("release_thresholds")
    fixture_policy = rubric.get("fixture_policy")
    if not isinstance(thresholds, Mapping) or not isinstance(fixture_policy, Mapping):
        raise ConfigCompilerError("rubric requires release_thresholds and fixture_policy objects")
    for criterion, (required, maximum) in _REQUIRED_RUBRIC_THRESHOLDS.items():
        threshold = thresholds.get(criterion)
        if not isinstance(threshold, Mapping) or threshold.get("required") != required or threshold.get("maximum") != maximum:
            raise ConfigCompilerError(f"rubric threshold {criterion} must be {required}/{maximum}")
    _require_bool(fixture_policy, "fixture_compile_uses_this_lock", True, "rubric.fixture_policy")
    _require_bool(fixture_policy, "fixture_compile_is_release_eligible", False, "rubric.fixture_policy")
    _require_bool(fixture_policy, "local_only", True, "rubric.fixture_policy")
    _require_bool(fixture_policy, "production_eligible", False, "rubric.fixture_policy")


def _validate_domain_contract(domain: Mapping[str, Any], *, base_forbidden: set[str]) -> None:
    policy = domain.get("policy")
    defaults = domain.get("viewer_defaults")
    if not isinstance(policy, Mapping) or not isinstance(defaults, Mapping):
        raise ConfigCompilerError("domain config requires policy and viewer_defaults objects")
    if not _string_set(policy.get("allowed_viewer_operations"), "domain.policy.allowed_viewer_operations"):
        raise ConfigCompilerError("domain.policy.allowed_viewer_operations cannot be empty")
    forbidden = _string_set(policy.get("forbidden_capabilities"), "domain.policy.forbidden_capabilities")
    if not base_forbidden.issubset(forbidden):
        raise ConfigCompilerError("domain policy cannot remove base forbidden capabilities")
    if not _string_set(defaults.get("required_capabilities"), "domain.viewer_defaults.required_capabilities"):
        raise ConfigCompilerError("domain.viewer_defaults.required_capabilities cannot be empty")


def _validate_profile_fixture_contract(profile: Mapping[str, Any]) -> None:
    policy = profile.get("fixture_policy")
    release = profile.get("release")
    if not isinstance(policy, Mapping) or not isinstance(release, Mapping):
        raise ConfigCompilerError("run profile requires fixture_policy and release objects")
    if profile.get("mode") == "catalog_only":
        _require_bool(policy, "allow_local_fixture_compile", False, "profile.fixture_policy")
        _require_bool(policy, "require_local_fixture_digest_match", False, "profile.fixture_policy")
        _require_bool(policy, "require_production_approval", True, "profile.fixture_policy")
        _require_bool(policy, "production_eligible", False, "profile.fixture_policy")
        _require_bool(release, "enabled", False, "profile.release")
    elif profile.get("mode") == "local_fixture_compile":
        _require_bool(policy, "allow_local_fixture_compile", True, "profile.fixture_policy")
        _require_bool(policy, "require_local_fixture_digest_match", True, "profile.fixture_policy")
        _require_bool(policy, "require_production_approval", False, "profile.fixture_policy")
        _require_bool(policy, "production_eligible", False, "profile.fixture_policy")
        _require_bool(release, "enabled", False, "profile.release")
    elif profile.get("mode") == "local_provider_pilot":
        if profile.get("id") not in _CHIRALITY_PROVIDER_PROFILE_IDS:
            raise ConfigCompilerError("local provider execution is restricted to the chirality pilot profiles")
        if profile.get("domains") != ["chemistry"] or profile.get("enabled_leaves") != [
            "chemistry.stereochemistry_chirality"
        ]:
            raise ConfigCompilerError("local provider execution is restricted to the chirality pilot leaf")
        _require_bool(policy, "allow_local_fixture_compile", False, "profile.fixture_policy")
        _require_bool(policy, "require_local_fixture_digest_match", False, "profile.fixture_policy")
        _require_bool(policy, "require_production_approval", False, "profile.fixture_policy")
        _require_bool(policy, "production_eligible", False, "profile.fixture_policy")
        _require_bool(release, "enabled", False, "profile.release")


def _validate_profile_planning_contract(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the only opt-in batch policy without relaxing base budgets."""
    planning = profile.get("planning")
    if planning is None:
        return deepcopy(_DEFAULT_PLANNING_POLICY)
    if not isinstance(planning, Mapping):
        raise ConfigCompilerError("profile.planning must be an object")
    if profile.get("mode") != "local_provider_pilot" or profile.get("id") not in _CHIRALITY_PROVIDER_PROFILE_IDS:
        raise ConfigCompilerError("batch planning is restricted to the chirality local provider pilot")
    default_count = planning.get("default_count")
    quota = planning.get("quota")
    batch = planning.get("batch")
    budget = planning.get("per_work_item_budget")
    if default_count != 1:
        raise ConfigCompilerError("profile planning default_count must remain 1")
    if isinstance(quota, bool) or not isinstance(quota, int) or not 5 <= quota <= 10:
        raise ConfigCompilerError("profile planning quota must be an integer from 5 through 10")
    if not isinstance(batch, Mapping) or not isinstance(budget, Mapping):
        raise ConfigCompilerError("profile planning requires batch and per_work_item_budget objects")
    minimum = batch.get("min_count")
    maximum = batch.get("max_count")
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
        or not 5 <= minimum <= maximum <= 10
        or quota > maximum
    ):
        raise ConfigCompilerError("profile planning batch range must be 5 through 10 and include its quota")
    seed_selection = batch.get("seed_selection")
    variation = batch.get("variation")
    if not isinstance(seed_selection, Mapping) or seed_selection != {
        "policy": "locked_seed_digest",
        "dedupe_policy": "seed_variation_pair",
    }:
        raise ConfigCompilerError("profile planning requires locked seed selection with seed/variation deduplication")
    if not isinstance(variation, Mapping) or variation != {"policy": "deterministic_unique_slot"}:
        raise ConfigCompilerError("profile planning requires deterministic unique variations")
    if budget.get("max_candidates") != 1 or budget.get("max_revisions") != 1:
        raise ConfigCompilerError("profile planning must keep one candidate and one revision per work item")
    return deepcopy(dict(planning))


def _validate_pilot_viewer_profile_requirement(
    profile: Mapping[str, Any], viewer_input: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """Bind the provider pilot to the concrete host capability declaration."""
    requirement = profile.get("viewer_profile_requirement")
    if requirement is None:
        return None
    if not isinstance(requirement, Mapping):
        raise ConfigCompilerError("profile viewer_profile_requirement must be an object")
    if profile.get("mode") != "local_provider_pilot" or profile.get("id") not in _CHIRALITY_PROVIDER_PROFILE_IDS:
        raise ConfigCompilerError("a real viewer profile requirement is restricted to the chirality local provider pilot")
    adapter_id = requirement.get("adapter_id")
    definition_digest = requirement.get("definition_digest")
    operations = requirement.get("required_operations")
    if adapter_id != "molstar-chirality-host-v1":
        raise ConfigCompilerError("chirality pilot must bind the molstar-chirality-host-v1 adapter")
    require_digest(definition_digest, "profile viewer profile definition digest")
    if not isinstance(operations, list) or set(operations) != {"rotate", "render"} or len(operations) != 2:
        raise ConfigCompilerError("chirality pilot viewer operations must be exactly rotate and render")
    if requirement.get("require_non_fixture") is not True:
        raise ConfigCompilerError("chirality pilot must require a non-fixture persisted ViewerProfile")
    if requirement.get("require_scene_registry") is not True:
        raise ConfigCompilerError("chirality pilot must require a SceneRegistry-bound ViewerProfile")
    if viewer_input is not None:
        if definition_digest != viewer_input.get("digest"):
            raise ConfigCompilerError("chirality pilot viewer profile requirement does not match the locked viewer definition")
        payload = viewer_input.get("payload")
        capabilities = payload.get("capabilities") if isinstance(payload, Mapping) else None
        if not isinstance(capabilities, list) or not set(operations).issubset(set(capabilities)):
            raise ConfigCompilerError("chirality pilot viewer definition lacks rotate/render capability")
    return dict(requirement)


def _validate_viewer_payload(payload: Mapping[str, Any], label: str) -> None:
    viewer_id = payload.get("viewer_id")
    capabilities = payload.get("capabilities")
    forbidden = payload.get("forbidden_capabilities")
    if not isinstance(viewer_id, str) or not viewer_id:
        raise ConfigCompilerError(f"{label} viewer_id must be a non-empty string")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) and item for item in capabilities):
        raise ConfigCompilerError(f"{label} capabilities must be non-empty strings")
    if not isinstance(forbidden, list) or not all(isinstance(item, str) and item for item in forbidden):
        raise ConfigCompilerError(f"{label} forbidden_capabilities must be non-empty strings")
    if not _GLOBAL_SOLVER_FORBIDDEN_CAPABILITIES.issubset(set(forbidden)):
        raise ConfigCompilerError(f"{label} does not forbid every global solver capability")


def _policy_blockers(
    *,
    base: Mapping[str, Any],
    domain: Mapping[str, Any],
    leaf: Mapping[str, Any],
) -> tuple[list[str], set[str]]:
    """Enforce monotonic policy inheritance for a leaf.

    A leaf may require fewer viewer operations or truth modes, but it may not
    introduce an operation/truth mode its parents did not permit.  Deny lists
    accumulate, so downstream configuration cannot reopen network, raw asset,
    or unrestricted-compute access by omission.
    """
    base_policy = base.get("policy", {})
    domain_policy = domain.get("policy", {})
    if not isinstance(base_policy, Mapping) or not isinstance(domain_policy, Mapping):
        raise ConfigCompilerError("base and domain policy must be objects")
    base_truth_modes = _string_set(base_policy.get("allowed_truth_modes"), "base.policy.allowed_truth_modes")
    domain_truth_modes = _string_set(domain_policy.get("allowed_truth_modes"), "domain.policy.allowed_truth_modes")
    allowed_truth_modes = domain_truth_modes or base_truth_modes
    leaf_answer = leaf.get("answer", {})
    if not isinstance(leaf_answer, Mapping):
        raise ConfigCompilerError("leaf.answer must be an object")
    leaf_truth_modes = _string_set(leaf_answer.get("allowed_truth_modes"), "leaf.answer.allowed_truth_modes")

    domain_allowed_operations = _string_set(
        domain_policy.get("allowed_viewer_operations"), "domain.policy.allowed_viewer_operations"
    )
    leaf_viewer = leaf.get("viewer", {})
    if not isinstance(leaf_viewer, Mapping):
        raise ConfigCompilerError("leaf.viewer must be an object")
    leaf_operation_modes = _viewer_operation_modes(leaf_viewer, "leaf.viewer")

    inherited_forbidden = _string_set(
        base_policy.get("solver_forbidden_capabilities"), "base.policy.solver_forbidden_capabilities"
    )
    inherited_forbidden |= _string_set(domain_policy.get("forbidden_capabilities"), "domain.policy.forbidden_capabilities")
    blockers: list[str] = []
    if allowed_truth_modes and not leaf_truth_modes.issubset(allowed_truth_modes):
        blockers.append("leaf_truth_modes_exceed_parent_policy")
    if domain_allowed_operations and any(
        not operations.issubset(domain_allowed_operations) for operations in leaf_operation_modes
    ):
        blockers.append("leaf_viewer_operations_exceed_domain_policy")
    # The current leaf schema intentionally has no policy override.  Retain a
    # defensive check so schema expansion cannot accidentally let a leaf reopen
    # capabilities that are forbidden at an ancestor.
    leaf_policy = leaf.get("policy")
    if isinstance(leaf_policy, Mapping):
        for key in ("allowed_capabilities", "solver_allowed_capabilities", "allowed_viewer_operations"):
            if key in leaf_policy and inherited_forbidden.intersection(_string_set(leaf_policy.get(key), f"leaf.policy.{key}")):
                blockers.append("leaf_policy_reopens_forbidden_capability")
    return blockers, inherited_forbidden


def _ordered_leaf_dependency_locks(leaves: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Produce the canonical per-leaf dependency-lock representation.

    Lists are deliberately retained rather than collapsed into a set digest:
    each selected leaf needs an inspectable binding to its own seed, knowledge,
    asset, viewer, and rubric inputs.
    """
    result: list[dict[str, Any]] = []
    for leaf in sorted(leaves, key=lambda item: str(item.get("leaf_id", ""))):
        leaf_id = leaf.get("leaf_id")
        locks = leaf.get("locks")
        if not isinstance(leaf_id, str) or not isinstance(locks, Mapping):
            raise ConfigCompilerError("compiled leaf lacks an inspectable dependency lock map")
        knowledge = locks.get("knowledge_digests")
        if not isinstance(knowledge, list):
            raise ConfigCompilerError(f"compiled leaf {leaf_id} knowledge locks are malformed")
        result.append(
            {
                "leaf_id": leaf_id,
                "seed_digest": locks.get("seed_digest"),
                "asset_digest": locks.get("asset_digest"),
                "viewer_definition_digest": locks.get("viewer_definition_digest"),
                "knowledge_digests": list(knowledge),
                "rubric_digest": locks.get("rubric_digest"),
            }
        )
    return result


class ConfigCompiler:
    """Compile only the local files explicitly declared by a run profile."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def build(self, profile_path: str | Path) -> dict[str, Any]:
        self.paths.ensure()
        profile_file = _local_file(profile_path, "run profile")
        config_root = _find_config_root(profile_file)
        repo_root = config_root.parent
        profile = _read_document(profile_file)
        _validate(profile, config_root, "profile.schema.json", "run profile")
        mode = profile["mode"]
        inputs = profile["inputs"]
        registry_mode = inputs["registry_mode"]
        base_file = _resolve_declared(profile_file, inputs["base"], repo_root, "profile.inputs.base")
        asset_file = (
            _resolve_declared(profile_file, inputs["asset_manifest"], repo_root, "profile.inputs.asset_manifest")
            if "asset_manifest" in inputs
            else None
        )
        viewer_file = (
            _resolve_declared(profile_file, inputs["viewer_definitions"], repo_root, "profile.inputs.viewer_definitions")
            if "viewer_definitions" in inputs
            else None
        )
        rubric_file = _resolve_declared(profile_file, inputs["rubric"], repo_root, "profile.inputs.rubric")
        raw_model_routing = inputs.get("model_routing")
        routing_file = (
            _resolve_declared(profile_file, raw_model_routing, repo_root, "profile.inputs.model_routing")
            if raw_model_routing is not None
            else config_root / "agents" / "model_routing.yaml"
        )
        base = _read_document(base_file)
        asset_source = _read_document(asset_file) if asset_file is not None else None
        viewer_source = _read_document(viewer_file) if viewer_file is not None else None
        rubric = _read_document(rubric_file)
        routing = load_model_routing(routing_file, schema_path=config_root / "schemas" / "model_routing.schema.json")
        provider_runtime_file: Path | None = None
        provider_runtime_document: dict[str, Any] | None = None
        provider_runtime_digest: str | None = None
        if mode == "local_provider_pilot":
            expected_routing = (config_root / "agents" / "model_routing.pilot.yaml").resolve()
            if routing_file != expected_routing or routing["document"].get("routing_status") != "approved_local_pilot":
                raise ConfigCompilerError("local provider pilot requires the checked-in model_routing.pilot.yaml")
            if routing["execution_eligible"] is not True or routing["document"].get("production_eligible") is not False:
                raise ConfigCompilerError("local provider pilot routing is not explicitly eligible and non-production")
            raw_provider_runtime = inputs.get("provider_runtime")
            if raw_provider_runtime is None:
                raise ConfigCompilerError("local provider pilot requires inputs.provider_runtime")
            provider_runtime_file = _resolve_declared(
                profile_file,
                raw_provider_runtime,
                repo_root,
                "profile.inputs.provider_runtime",
            )
            expected_runtime = (config_root / "agents" / "provider_runtime.pilot.yaml").resolve()
            if provider_runtime_file != expected_runtime:
                raise ConfigCompilerError("local provider pilot requires the checked-in provider_runtime.pilot.yaml")
            provider_runtime_document = _read_document(provider_runtime_file)
            _validate(provider_runtime_document, config_root, "provider_runtime.schema.json", "provider runtime")
            if (
                provider_runtime_document.get("enabled") is not True
                or provider_runtime_document.get("production_eligible") is not False
                or provider_runtime_document.get("endpoint_env") != "QAGEN_UNIV_BASE_URL"
                or provider_runtime_document.get("endpoint_approval_path_env") != "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH"
            ):
                raise ConfigCompilerError("provider runtime must require an external HTTPS endpoint approval for the non-production pilot")
            provider_runtime_digest = digest(provider_runtime_document)
        fixture_routing_file: Path | None = None
        fixture_routing_document: dict[str, Any] | None = None
        fixture_routing_digest: str | None = None
        if mode == "local_fixture_compile":
            raw_fixture_routing = inputs.get("fixture_routing")
            if raw_fixture_routing is None:
                raise ConfigCompilerError("local fixture profile requires inputs.fixture_routing")
            fixture_routing_file = _resolve_declared(
                profile_file,
                raw_fixture_routing,
                repo_root,
                "profile.inputs.fixture_routing",
            )
            fixture_routing_document = _read_document(fixture_routing_file)
            fixture_routing_digest = digest(fixture_routing_document)
            verified_fixture_routing = verify_routing_policy(
                fixture_routing_document,
                expected_digest=fixture_routing_digest,
            )
            if not verified_fixture_routing.local_fixture_only:
                raise ConfigCompilerError("fixture routing must be an explicit local-only fixture policy")
        _validate(base, config_root, "base.schema.json", "base config")
        if asset_source is not None:
            _validate(asset_source, config_root, "source.schema.json", "asset source")
        if viewer_source is not None:
            _validate(viewer_source, config_root, "source.schema.json", "viewer source")
        _validate(rubric, config_root, "rubric.schema.json", "rubric lock")
        base_forbidden = _validate_base_contract(base)
        _validate_rubric_contract(rubric)
        _validate_profile_fixture_contract(profile)
        planning_policy = _validate_profile_planning_contract(profile)

        input_locks: list[dict[str, Any]] = [
            _document_lock("profile", profile_file, profile),
            _document_lock("base", base_file, base),
            _document_lock("rubric", rubric_file, rubric),
            _document_lock("model_routing", routing_file, routing["document"]),
        ]
        if fixture_routing_file is not None and fixture_routing_document is not None:
            input_locks.append(_document_lock("fixture_routing", fixture_routing_file, fixture_routing_document))
        if provider_runtime_file is not None and provider_runtime_document is not None:
            input_locks.append(_document_lock("provider_runtime", provider_runtime_file, provider_runtime_document))
        seed_fixtures: dict[str, dict[str, Any]] = {}
        workspace_seed: dict[str, Any] | None = None
        registry_blockers: list[str] = []
        knowledge_entries: dict[str, dict[str, Any]] = {}
        pending_knowledge_approval_leaves: set[str] = set()
        workspace_chirality_binding: dict[str, Any] | None = None
        workspace_chirality_binding_requested = False
        if registry_mode == "catalog_only":
            registry_blockers.append("catalog_only_profile")
        elif registry_mode == "static_fixture":
            source_file = _resolve_declared(
                profile_file,
                inputs["source_registry"],
                repo_root,
                "profile.inputs.source_registry",
            )
            source_registry = _read_document(source_file)
            _validate(source_registry, config_root, "source.schema.json", "seed source registry")
            input_locks.append(_document_lock("seed_source_registry", source_file, source_registry))
            source_entries = source_registry.get("sources")
            if not isinstance(source_entries, list):
                raise ConfigCompilerError("seed source registry sources must be a list")
            for source in source_entries:
                if not isinstance(source, Mapping):
                    raise ConfigCompilerError("seed source registry entry must be an object")
                fixture, fixture_path, payload, locks = _load_fixture_source(
                    source,
                    source_file,
                    repo_root,
                    "seed source",
                    source_lock_document=None,
                )
                alias = _fixture_alias(fixture)
                seed_fixtures[alias] = {
                    "digest": fixture["fixture_payload_digest"],
                    "payload": payload,
                    "path": fixture_path,
                    "local_fixture": True,
                    "production_eligible": False,
                }
                input_locks.extend(locks)
            knowledge_file = _resolve_declared(
                profile_file,
                inputs["knowledge_index"],
                repo_root,
                "profile.inputs.knowledge_index",
            )
            knowledge_index = _read_document(knowledge_file)
            _validate(knowledge_index, config_root, "knowledge_index.schema.json", "knowledge index")
            knowledge_entries, knowledge_locks = _parse_static_knowledge_index(
                knowledge_index, knowledge_file, repo_root
            )
            input_locks.extend(knowledge_locks)
        else:
            workspace_registry = inputs.get("workspace_registry")
            if not isinstance(workspace_registry, Mapping):
                # Older checked-in skeleton profiles intentionally remain
                # blocked.  They are never allowed to turn a mutable current
                # pointer into a production input.
                registry_blockers.append("workspace_registry_inputs_not_explicit")
            else:
                seed_reference = workspace_registry.get("seed_index")
                knowledge_reference = workspace_registry.get("knowledge_index")
                pending_knowledge_approval = workspace_registry.get("knowledge_approval")
                if not isinstance(seed_reference, Mapping):
                    registry_blockers.append("workspace_seed_index_not_explicit")
                else:
                    workspace_seed, seed_locks, seed_blockers = _parse_workspace_seed_index(
                        self.paths, seed_reference
                    )
                    input_locks.extend(seed_locks)
                    registry_blockers.extend(seed_blockers)
                if knowledge_reference is not None and pending_knowledge_approval is not None:
                    raise ConfigCompilerError(
                        "workspace knowledge index and pending knowledge approval cannot both be declared"
                    )
                if pending_knowledge_approval is not None:
                    if (
                        not isinstance(pending_knowledge_approval, Mapping)
                        or pending_knowledge_approval.get("state") != "pending_human_approval"
                        or not isinstance(pending_knowledge_approval.get("leaf_id"), str)
                    ):
                        raise ConfigCompilerError("workspace knowledge approval pending slot is malformed")
                    pending_knowledge_approval_leaves.add(str(pending_knowledge_approval["leaf_id"]))
                elif not isinstance(knowledge_reference, Mapping):
                    registry_blockers.append("workspace_knowledge_index_not_explicit")
                else:
                    knowledge_entries, knowledge_locks, knowledge_blockers = (
                        _parse_workspace_knowledge_index(
                            self.paths,
                            knowledge_reference,
                            seed_index=workspace_seed,
                        )
                    )
                    input_locks.extend(knowledge_locks)
                    registry_blockers.extend(knowledge_blockers)
                (
                    workspace_chirality_binding,
                    workspace_chirality_binding_requested,
                    chirality_locks,
                    chirality_blockers,
                ) = _parse_workspace_chirality_binding(self.paths, workspace_registry)
                input_locks.extend(chirality_locks)
                registry_blockers.extend(chirality_blockers)

        if workspace_chirality_binding is not None:
            asset_input: dict[str, Any] | None = workspace_chirality_binding["asset_input"]
            viewer_input: dict[str, Any] | None = workspace_chirality_binding["viewer_input"]
        elif workspace_chirality_binding_requested:
            asset_input = None
            viewer_input = None
        elif asset_source is not None and asset_file is not None and viewer_source is not None and viewer_file is not None:
            asset_input = _load_input_source(asset_source, asset_file, repo_root, "asset source")
            viewer_input = _load_input_source(viewer_source, viewer_file, repo_root, "viewer source")
        else:
            asset_input = None
            viewer_input = None
        if viewer_input is not None and viewer_source is not None and "fixture_path" in viewer_source:
            viewer_fixture = _read_document(viewer_input["path"])
            _validate(viewer_fixture, config_root, "viewer.schema.json", "viewer definition fixture")
        if viewer_input is not None:
            _validate_viewer_payload(viewer_input["payload"], "viewer definition")
        viewer_profile_requirement = _validate_pilot_viewer_profile_requirement(profile, viewer_input)
        asset_alias = asset_input["alias"] if asset_input is not None else None
        viewer_alias = viewer_input["alias"] if viewer_input is not None else None
        asset_payload = asset_input["payload"] if asset_input is not None else {"assets": []}
        viewer_payload = viewer_input["payload"] if viewer_input is not None else {}
        if asset_input is not None and viewer_input is not None:
            input_locks.extend(asset_input["locks"] + viewer_input["locks"])
        rubric_document = rubric.get("document")
        if not isinstance(rubric_document, Mapping):
            raise ConfigCompilerError("rubric lock document is missing")
        rubric_source_path = _find_repo_file(profile_file, str(rubric_document.get("repo_path", "")))
        rubric_digest = rubric_document.get("digest")
        require_digest(rubric_digest, "rubric document digest")
        if digest_bytes(rubric_source_path.read_bytes()) != rubric_digest:
            raise ConfigCompilerError("rubric source document digest does not match rubric lock")
        input_locks.append(_bytes_lock("rubric_document", rubric_source_path))
        # The source document is one lock, while this YAML lock also freezes
        # release thresholds and fixture policy. Keep both identities so a
        # release never has to re-read mutable configuration.
        rubric_lock_digest = digest(rubric)

        domains: dict[str, dict[str, Any]] = {}
        for raw_path in inputs["domain_configs"]:
            domain_file = _resolve_declared(profile_file, raw_path, repo_root, "profile domain config")
            domain = _read_document(domain_file)
            _validate(domain, config_root, "domain.schema.json", f"domain config {domain_file.name}")
            _validate_domain_contract(domain, base_forbidden=base_forbidden)
            domain_id = domain["id"]
            if domain_id in domains:
                raise ConfigCompilerError(f"duplicate domain config: {domain_id}")
            domains[domain_id] = {"document": domain, "path": domain_file}
            input_locks.append(_document_lock(f"domain:{domain_id}", domain_file, domain))
        missing_domains = set(profile["domains"]) - set(domains)
        if missing_domains:
            raise ConfigCompilerError(f"profile lacks declared domain config(s): {', '.join(sorted(missing_domains))}")
        leaves: list[tuple[dict[str, Any], Path]] = []
        seen_leaf_ids: set[str] = set()
        for raw_path in inputs["leaf_configs"]:
            leaf_file = _resolve_declared(profile_file, raw_path, repo_root, "profile leaf config")
            leaf = _read_document(leaf_file)
            _validate(leaf, config_root, "subdomain.schema.json", f"subdomain config {leaf_file.name}")
            leaf_id = leaf["id"]
            if leaf_id in seen_leaf_ids:
                raise ConfigCompilerError(f"duplicate subdomain config: {leaf_id}")
            seen_leaf_ids.add(leaf_id)
            leaves.append((leaf, leaf_file))
            input_locks.append(_document_lock(f"leaf:{leaf_id}", leaf_file, leaf))

        compiled_leaves: list[dict[str, Any]] = []
        enabled_leaves = set(profile.get("enabled_leaves", []))
        blocked_profile_leaves = set(profile.get("blocked_leaves", []))
        for leaf, leaf_file in leaves:
            leaf_id = leaf["id"]
            domain_id = leaf["domain"]
            blockers: list[str] = list(registry_blockers)
            domain_entry = domains.get(domain_id)
            if domain_entry is None:
                blockers.append("domain_config_missing")
                domain_document: Mapping[str, Any] = {}
            else:
                domain_document = domain_entry["document"]
            if domain_id not in profile["domains"]:
                blockers.append("domain_not_enabled_by_profile")
            if leaf_id not in enabled_leaves:
                blockers.append("leaf_not_enabled_by_profile")
            if leaf_id in blocked_profile_leaves:
                blockers.append("leaf_explicitly_blocked_by_profile")
            if domain_id == "medical" or domain_document.get("domain_status") == "blocked":
                blockers.append("medical_or_domain_blocked")
            workspace_binding_template = leaf.get("workspace_binding") == {
                "kind": _CHIRALITY_WORKSPACE_BINDING_KIND
            }
            if leaf.get("status") != "sample_compile_eligible" and not (
                registry_mode == "workspace_registry" and workspace_binding_template
            ):
                blockers.append("leaf_status_not_compile_eligible")
            if workspace_chirality_binding_requested and not workspace_binding_template:
                blockers.append("workspace_chirality_binding_not_declared_by_leaf")
            if domain_document:
                policy_blockers, inherited_forbidden = _policy_blockers(
                    base=base,
                    domain=domain_document,
                    leaf=leaf,
                )
                blockers.extend(policy_blockers)
            else:
                inherited_forbidden = set()

            seed_ref = leaf["seed"].get("locked_ref")
            if registry_mode == "static_fixture":
                if not isinstance(seed_ref, str):
                    blockers.append("seed_lock_missing_or_mismatched")
                    seed_entry = None
                    resolved_seed_digest: str | None = None
                else:
                    seed_alias = seed_ref.rsplit("@", 1)[0]
                    seed_entry = seed_fixtures.get(seed_alias)
                    resolved_seed_digest = _reference_digest(seed_ref, f"seed lock {leaf_id}")
                    if seed_entry is None or seed_entry["digest"] != resolved_seed_digest:
                        blockers.append("seed_lock_missing_or_mismatched")
            else:
                seed_entry = workspace_seed
                resolved_seed_digest = seed_entry["digest"] if seed_entry is not None else None
            if seed_entry is not None:
                expected_seed = leaf["seed"]["query"]
                leaf_subdomain = leaf_id.split(".", 1)[1]
                if expected_seed.get("domain") != domain_id or expected_seed.get("subdomain") != leaf_subdomain:
                    blockers.append("seed_query_does_not_match_leaf_contract")
                required_tags = expected_seed.get("tags", [])
                if required_tags is not None and (
                    not isinstance(required_tags, list) or not all(isinstance(tag, str) and tag for tag in required_tags)
                ):
                    blockers.append("seed_query_tags_invalid")
                required_status = expected_seed["required_status"]
                if registry_mode == "workspace_registry" and required_status == "fixture_verified":
                    required_status = "verified"
                matching_seed = [
                    record
                    for record in seed_entry["payload"].get("records", [])
                    if isinstance(record, Mapping)
                    and record.get("domain") == expected_seed["domain"]
                    and record.get("subdomain") == expected_seed["subdomain"]
                    and record.get("status") == required_status
                    and record.get("status") in {"verified", "fixture_verified"}
                    and set(required_tags or []).issubset(set(record.get("tags", [])))
                ]
                if not matching_seed:
                    blockers.append("seed_query_has_no_verified_positive_locked_record")

            asset_ref = leaf["asset"].get("locked_ref")
            if asset_input is None:
                if not workspace_binding_template:
                    blockers.append("asset_source_or_workspace_scene_registry_missing")
            elif workspace_chirality_binding is not None:
                matching_asset = [
                    record
                    for record in asset_payload.get("assets", [])
                    if isinstance(record, Mapping)
                    and record.get("domain") == domain_id
                    and record.get("subdomain") == leaf_id.split(".", 1)[1]
                    and isinstance(record.get("anonymous_scene_ref"), str)
                ]
                if not matching_asset:
                    blockers.append("asset_selector_has_no_anonymous_scene")
            elif (
                not isinstance(asset_ref, str)
                or asset_ref.rsplit("@", 1)[0] != asset_alias
                or _reference_digest(asset_ref, f"asset lock {leaf_id}") != asset_input["digest"]
            ):
                blockers.append("asset_lock_missing_or_mismatched")
            else:
                matching_asset = [
                    record
                    for record in asset_payload.get("assets", [])
                    if isinstance(record, Mapping)
                    and record.get("domain") == domain_id
                    and record.get("subdomain") == leaf_id.split(".", 1)[1]
                    and isinstance(record.get("anonymous_scene_ref"), str)
                ]
                if not matching_asset:
                    blockers.append("asset_selector_has_no_anonymous_scene")

            viewer_ref = leaf["viewer"].get("locked_ref")
            viewer_capabilities = set(viewer_payload.get("capabilities", []))
            if viewer_input is None:
                if not workspace_binding_template:
                    blockers.append("viewer_source_or_workspace_profile_missing")
            elif workspace_chirality_binding is not None:
                viewer_modes = _viewer_operation_modes(leaf["viewer"], f"{leaf_id}.viewer")
                if not any(mode.issubset(viewer_capabilities) for mode in viewer_modes):
                    blockers.append("viewer_definition_lacks_required_operations")
            elif (
                not isinstance(viewer_ref, str)
                or viewer_ref.rsplit("@", 1)[0] != viewer_alias
                or _reference_digest(viewer_ref, f"viewer lock {leaf_id}") != viewer_input["digest"]
            ):
                blockers.append("viewer_lock_missing_or_mismatched")
            elif not any(
                mode.issubset(viewer_capabilities)
                for mode in _viewer_operation_modes(leaf["viewer"], f"{leaf_id}.viewer")
            ):
                blockers.append("viewer_definition_lacks_required_operations")

            knowledge_refs = leaf["knowledge"]["locked_refs"]
            matched_knowledge: list[dict[str, Any]] = []
            resolved_knowledge_refs: list[str] = []
            if registry_mode == "workspace_registry":
                entry = knowledge_entries.get(leaf_id)
                if entry is None:
                    if leaf_id in pending_knowledge_approval_leaves:
                        blockers.append("knowledge_human_approval_pending")
                    elif "workspace_knowledge_index_not_explicit" not in registry_blockers:
                        blockers.append("knowledge_lock_missing_or_mismatched")
                else:
                    matched_knowledge.append(entry)
                    resolved_knowledge_refs.append(entry["reference"])
                    if entry["state"] != "approved":
                        blockers.append("knowledge_snapshot_not_approved")
            else:
                for knowledge_ref in knowledge_refs:
                    entry = knowledge_entries.get(leaf_id)
                    if entry is None or entry["reference"] != knowledge_ref:
                        blockers.append("knowledge_lock_missing_or_mismatched")
                        continue
                    matched_knowledge.append(entry)
                    resolved_knowledge_refs.append(knowledge_ref)
                    if entry["state"] != "fixture_compile_eligible":
                        blockers.append("knowledge_snapshot_not_compile_eligible")

            fixture_input = bool(
                (asset_input is not None and asset_input["local_only"])
                or (viewer_input is not None and viewer_input["local_only"])
                or (seed_entry is not None and seed_entry.get("local_fixture"))
                or any(entry.get("local_fixture") for entry in matched_knowledge)
            )
            if mode == "local_fixture_compile":
                policy = profile["fixture_policy"]
                if not policy.get("allow_local_fixture_compile"):
                    blockers.append("fixture_profile_opt_in_missing")
            elif mode == "local_provider_pilot":
                if leaf_id != "chemistry.stereochemistry_chirality":
                    blockers.append("provider_pilot_leaf_not_authorized")
            elif mode == "catalog_only":
                blockers.append("catalog_only_profile")
            else:
                if fixture_input:
                    blockers.append("fixture_inputs_cannot_satisfy_production_release")
                if leaf.get("production_status") != "approved":
                    blockers.append("leaf_not_production_approved")
                if seed_entry is None or not seed_entry.get("production_eligible"):
                    blockers.append("seed_not_production_approved")
                if any(entry.get("local_fixture") or not entry.get("production_eligible") for entry in matched_knowledge):
                    blockers.append("knowledge_not_production_approved")
                if asset_input is None or asset_input["local_only"] or not asset_input["production_eligible"]:
                    blockers.append("asset_not_production_approved")
                if viewer_input is None or viewer_input["local_only"] or not viewer_input["production_eligible"]:
                    blockers.append("viewer_not_production_approved")
                if not profile.get("release", {}).get("enabled"):
                    blockers.append("profile_release_disabled")

            effective = _deep_merge(_layer(base), _layer(domain_document))
            effective = _deep_merge(effective, _layer(leaf))
            if registry_mode == "workspace_registry":
                if resolved_seed_digest is not None:
                    effective["seed"]["locked_ref"] = f"seed-catalog@{resolved_seed_digest}"
                effective["knowledge"]["locked_refs"] = list(resolved_knowledge_refs)
            if workspace_chirality_binding is not None:
                effective["asset"]["locked_ref"] = (
                    f"workspace-scene-registry@{workspace_chirality_binding['scene_registry_digest']}"
                )
                effective["viewer"]["locked_ref"] = (
                    f"workspace-viewer-profile@{workspace_chirality_binding['viewer_profile_digest']}"
                )
            effective_policy = effective.get("policy")
            if isinstance(effective_policy, Mapping):
                # Preserve all inherited denials in the effective document.
                # A leaf can only add a denial, never remove one.
                policy_copy = dict(effective_policy)
                policy_copy["solver_forbidden_capabilities"] = sorted(inherited_forbidden)
                policy_copy["forbidden_capabilities"] = sorted(inherited_forbidden)
                effective["policy"] = policy_copy
            effective = _deep_merge(
                effective,
                {
                    "fixture_policy": deepcopy(profile["fixture_policy"]),
                    "run_profile": {
                        "id": profile["id"],
                        "mode": mode,
                        "release": deepcopy(profile["release"]),
                    },
                },
            )
            leaf_locks = {
                "seed_digest": resolved_seed_digest,
                "asset_digest": asset_input["digest"] if asset_input is not None else None,
                "viewer_definition_digest": viewer_input["digest"] if viewer_input is not None else None,
                "knowledge_digests": [
                    _reference_digest(reference, f"knowledge lock {leaf_id}")
                    for reference in resolved_knowledge_refs
                ],
                "rubric_digest": rubric_digest,
            }
            compiled_leaves.append(
                {
                    "leaf_id": leaf_id,
                    "config_path": str(leaf_file),
                    "status": "eligible" if not blockers else "blocked",
                    "blockers": sorted(set(blockers)),
                    "fixture_only": bool(fixture_input and mode == "local_fixture_compile"),
                    "effective": effective,
                    "locks": leaf_locks,
                }
            )

        eligible = [leaf for leaf in compiled_leaves if leaf["status"] == "eligible"]
        dependency_locks = _ordered_leaf_dependency_locks(compiled_leaves)
        # This digest is a commitment to the complete ordered lock map, not a
        # substitute for any individual dependency digest.
        lock_digests = {
            "dependency_lock_map_digest": digest(dependency_locks),
            "model_routing_digest": routing["routing_digest"],
            "rubric_lock_digest": rubric_lock_digest,
            "planning_policy_digest": digest(planning_policy),
        }
        if viewer_profile_requirement is not None:
            lock_digests["viewer_profile_requirement_digest"] = digest(viewer_profile_requirement)
        if fixture_routing_digest is not None:
            lock_digests["fixture_routing_digest"] = fixture_routing_digest
        if provider_runtime_digest is not None:
            lock_digests["provider_runtime_digest"] = provider_runtime_digest
        release_eligible = bool(
            mode == "production_release"
            and profile["release"].get("enabled")
            and profile["fixture_policy"].get("production_eligible")
            and eligible
            and len(eligible) == len(compiled_leaves)
        )
        status = (
            "production_eligible"
            if release_eligible
            else (
                "local_provider_pilot_ready"
                if mode == "local_provider_pilot" and eligible
                else ("fixture_stage_only" if eligible else "blocked")
            )
        )
        body = {
            "profile_id": profile["id"],
            "profile_mode": mode,
            "profile_path": str(profile_file),
            "config_root": str(config_root),
            "status": status,
            "release_eligible": release_eligible,
            "release": deepcopy(profile["release"]),
            "rubric": {
                "lock_digest": rubric_lock_digest,
                "document_digest": rubric_digest,
                "release_thresholds": deepcopy(rubric["release_thresholds"]),
                "fixture_policy": deepcopy(rubric["fixture_policy"]),
            },
            "input_locks": input_locks,
            "lock_digests": lock_digests,
            "planning": planning_policy,
            "viewer_profile_requirement": viewer_profile_requirement,
            "model_routing": {
                "path": routing["routing_path"],
                "digest": routing["routing_digest"],
                "execution_eligible": routing["execution_eligible"],
            },
            "fixture_routing": (
                {
                    "path": str(fixture_routing_file),
                    "digest": fixture_routing_digest,
                    "local_only": True,
                }
                if fixture_routing_file is not None and fixture_routing_digest is not None
                else None
            ),
            "provider_runtime": (
                {
                    "path": str(provider_runtime_file),
                    "digest": provider_runtime_digest,
                    "production_eligible": False,
                    "endpoint_env": "QAGEN_UNIV_BASE_URL",
                    "endpoint_approval_path_env": "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH",
                }
                if provider_runtime_file is not None and provider_runtime_digest is not None
                else None
            ),
            "dependency_locks": dependency_locks,
            "leaves": compiled_leaves,
        }
        compiled = make_envelope(
            kind="compiled_config",
            identifier=f"config-{profile['id']}",
            status=status,
            body=body,
            producer="qagen/0.1.0:config_compiler",
        )
        output_path = self.paths.compiled_configs / f"{profile['id']}-{compiled['content_digest'].split(':', 1)[1][:16]}.json"
        atomic_write_json(output_path, compiled, private=True)
        append_event(
            self.paths,
            "config_compiled",
            {
                "config_digest": compiled["content_digest"],
                "profile_id": profile["id"],
                "eligible_leaf_count": len(eligible),
                "status": status,
            },
        )
        return {
            "config_path": str(output_path),
            "config_digest": compiled["content_digest"],
            "status": status,
            "eligible_leaves": [leaf["leaf_id"] for leaf in eligible],
            "blocked_leaves": {
                leaf["leaf_id"]: leaf["blockers"] for leaf in compiled_leaves if leaf["status"] == "blocked"
            },
        }


def build_config(paths: WorkspacePaths, *, profile_path: str | Path) -> dict[str, Any]:
    return ConfigCompiler(paths).build(profile_path)


def load_compiled_config(paths: WorkspacePaths, config_path: str | Path) -> dict[str, Any]:
    path = require_within(paths.compiled_configs, config_path, "compiled config")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ConfigCompilerError("compiled config is not an object")
    return verify_envelope(value, kind="compiled_config")


def revalidate_compiled_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read every locked local input before run start or resume."""
    envelope = verify_envelope(config, kind="compiled_config")
    locks = envelope["body"].get("input_locks")
    if not isinstance(locks, list):
        raise StaleInputError("compiled config has no input locks")
    for lock in locks:
        if not isinstance(lock, Mapping):
            raise StaleInputError("compiled config input lock is malformed")
        path = _local_file(lock.get("path"), "compiled config input")
        target = lock.get("target")
        expected = lock.get("digest")
        require_digest(expected, "compiled config lock digest")
        if target == "bytes":
            actual = digest_bytes(path.read_bytes())
        else:
            document = _read_document(path)
            if target == "document":
                actual = digest(document)
            elif target == "fixture_payload":
                payload = document.get("fixture_payload")
                if not isinstance(payload, Mapping):
                    raise StaleInputError(f"compiled config fixture lock is no longer a fixture: {path}")
                actual = digest(dict(payload))
            else:
                raise StaleInputError(f"unknown compiled config lock target: {target}")
        if actual != expected:
            raise StaleInputError(f"compiled config input lock changed: {lock.get('label')}")
        artifact_digest = lock.get("artifact_digest")
        if artifact_digest is not None:
            artifact_kind = lock.get("artifact_kind")
            try:
                require_digest(artifact_digest, "compiled config artifact digest")
                if not isinstance(artifact_kind, str) or not artifact_kind:
                    raise InputError("compiled config artifact kind is missing")
                artifact = _verified_envelope(
                    path,
                    kind=artifact_kind,
                    label=f"compiled config artifact {lock.get('label')}",
                )
            except InputError as exc:
                raise StaleInputError(str(exc)) from exc
            if artifact["content_digest"] != artifact_digest:
                raise StaleInputError(f"compiled config artifact lock changed: {lock.get('label')}")
    return envelope
