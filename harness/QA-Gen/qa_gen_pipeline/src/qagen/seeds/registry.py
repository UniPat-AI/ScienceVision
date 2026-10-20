"""Deterministic, local-only EV2 seed registry."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import threading
from typing import Any, Callable, Iterable, Mapping

from jsonschema import Draft7Validator

from ..contracts import canonical_json, digest, make_decision, make_envelope, require_digest, require_nonempty_string, verify_envelope
from ..errors import InputError, InvalidStateError, QuarantinedError
from ..paths import WorkspacePaths, require_within, safe_component
from ..registry_contracts import validate_knowledge_index, validate_seed_index
from ..sources import (
    SafeFilesystemError,
    TrustedSourceRoot,
    TrustedTreeSnapshot,
    lock_trusted_source_root,
    snapshot_trusted_tree,
    verify_trusted_tree_snapshot,
)
from ..storage.atomic import atomic_write_json, atomic_write_yaml, read_json, read_yaml
from ..storage.ledger import append_event


VALID_REVIEW_STATUSES = {"verified", "minor_fix", "factual_error", "unscorable"}
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])(v?\d+\.\d+\.\d+)(?![A-Za-z0-9])", re.IGNORECASE)
_RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_ROLE_CAPTURE_BYTES = 64 * 1024 * 1024
_SOURCE_DECISION_THREAD_LOCK = threading.RLock()

# File-role parser names are deliberately part of the committed SourceSpec.
# The importer supports this one EV2 contract only; accepting an arbitrary
# role name would let a source change its meaning without changing code.
_ROLE_CONTRACTS = {
    "manifest": ("json", "ev2_manifest_v1"),
    "version_note": ("markdown", "ev2_version_note_v1"),
    "tasks": ("jsonl", "ev2_tasks_v1"),
    "expert_review": ("json", "ev2_expert_review_v1"),
    "asset_manifest": ("jsonl", "ev2_asset_manifest_v1"),
}


# SourceSpec is the trust boundary for local seed imports.  It deliberately
# has no "verified" toggle: trust comes from matching concrete constraints.
_SOURCE_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "kind",
        "id",
        "importer",
        "mode",
        "root_env",
        "expected_basename",
        "file_roles",
        "taxonomy_aliases",
        "version_policy",
        "local_only",
        "production_eligible",
        "expected_inventory_digest",
        "expected_package",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "kind": {"const": "qagen_seed_source_spec"},
        "id": {"type": "string", "minLength": 1},
        "importer": {"const": "ev2_local_package_v1"},
        "mode": {"const": "controlled"},
        "root_env": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
        "expected_basename": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]*$"},
        "file_roles": {
            "type": "object",
            "required": list(_ROLE_CONTRACTS),
            "properties": {
                role: {
                    "type": "object",
                    "required": ["path", "format", "parser_contract"],
                    "properties": {
                        "path": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))(?!.*\\\\)[A-Za-z0-9][A-Za-z0-9._/-]*$",
                        },
                        "format": {"enum": ["json", "jsonl", "markdown"]},
                        "parser_contract": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                }
                for role in _ROLE_CONTRACTS
            },
            "additionalProperties": False,
        },
        "taxonomy_aliases": {
            "type": "object",
            "required": ["path", "version", "content_digest"],
            "properties": {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))(?!.*\\\\)[A-Za-z0-9][A-Za-z0-9._/-]*$",
                },
                "version": {"type": "integer", "minimum": 1},
                "content_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            },
            "additionalProperties": False,
        },
        "version_policy": {"const": "require_human_resolution_on_conflict"},
        "local_only": {"const": True},
        "production_eligible": {"const": False},
        "expected_inventory_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "expected_package": {
            "type": "object",
            "required": ["package_id", "version_labels", "case_count", "review_distribution", "provenance"],
            "properties": {
                "package_id": {"type": "string", "minLength": 1},
                "version_labels": {
                    "type": "object",
                    "required": ["directory", "manifest", "package_id", "version_note"],
                    "properties": {
                        "directory": {"type": "string", "minLength": 1},
                        "manifest": {"type": "string", "minLength": 1},
                        "package_id": {"type": "string", "minLength": 1},
                        "version_note": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
                "case_count": {"type": "integer", "minimum": 1},
                "review_distribution": {
                    "type": "object",
                    "required": sorted(VALID_REVIEW_STATUSES),
                    "properties": {
                        status: {"type": "integer", "minimum": 0} for status in sorted(VALID_REVIEW_STATUSES)
                    },
                    "additionalProperties": False,
                },
                "provenance": {
                    "type": "object",
                    "required": [
                        "required_fields",
                        "require_asset_sha256",
                        "require_asset_package_path",
                        "require_asset_byte_size",
                        "require_source_suite",
                    ],
                    "properties": {
                        "required_fields": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "require_asset_sha256": {"const": True},
                        "require_asset_package_path": {"const": True},
                        "require_asset_byte_size": {"const": True},
                        "require_source_suite": {"const": True},
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

_TAXONOMY_ALIAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "kind", "id", "version", "aliases"],
    "properties": {
        "schema_version": {"const": 1},
        "kind": {"const": "qagen_subdomain_aliases"},
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "version": {"type": "integer", "minimum": 1},
        "aliases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source_leaf", "target_leaf"],
                "properties": {
                    "source_leaf": {"type": "string", "pattern": "^[a-z]+\\.[a-z0-9_]+$"},
                    "target_leaf": {"type": "string", "pattern": "^[a-z]+\\.[a-z0-9_]+$"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _short(digest_value: str) -> str:
    return digest_value.split(":", 1)[-1][:16]


@contextmanager
def _source_decision_lock(paths: WorkspacePaths, request_digest: str):
    """Serialize one source-version request decision across local processes."""
    require_digest(request_digest, "source version request digest")
    directory = paths.receipts / "source"
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f".request-{request_digest.split(':', 1)[1]}.lock"
    with _SOURCE_DECISION_THREAD_LOCK:
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


def _create_source_resolution(path: Path, receipt: Mapping[str, Any]) -> None:
    """Create one immutable source resolution without replacing any file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(dict(receipt)) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _source_spec_path(paths: WorkspacePaths, value: str | Path) -> Path:
    raw = str(value)
    if raw.startswith(("http://", "https://", "feishu://")):
        raise InputError("seed source spec must be a local file, not a live URL")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise InputError(f"seed source spec is not a local file: {path}")
    # Runtime state may be kept in a different local workspace from the
    # checked-in source specification.  Package containment is instead bound
    # to this spec's own directory below.
    return path


def _load_taxonomy_aliases(spec_path: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    reference = spec.get("taxonomy_aliases")
    if not isinstance(reference, Mapping):
        raise InputError("seed source spec taxonomy_aliases must be an object")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise InputError("seed source spec taxonomy alias path must be non-empty")
    alias_root = spec_path.parent.resolve()
    unresolved = alias_root / raw_path
    if unresolved.is_symlink():
        raise InputError("seed taxonomy alias document cannot be a symlink")
    alias_path = unresolved.resolve()
    try:
        alias_path.relative_to(alias_root)
    except ValueError as exc:
        raise InputError("seed taxonomy alias document escapes the SourceSpec directory") from exc
    if not alias_path.is_file():
        raise InputError(f"seed taxonomy alias document is not a local file: {alias_path}")
    try:
        document = read_yaml(alias_path)
    except (OSError, InputError) as exc:
        raise InputError(f"cannot read seed taxonomy alias document {alias_path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise InputError("seed taxonomy alias document must be an object")
    errors = sorted(
        Draft7Validator(_TAXONOMY_ALIAS_SCHEMA).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise InputError(f"seed taxonomy alias schema validation failed at {location}: {error.message}")
    expected_digest = require_digest(reference.get("content_digest"), "seed taxonomy alias content_digest")
    actual_digest = digest(dict(document))
    if actual_digest != expected_digest:
        raise InputError("seed taxonomy alias document does not match its content_digest")
    if document["version"] != reference.get("version"):
        raise InputError("seed taxonomy alias document does not match its pinned version")

    aliases: dict[str, str] = {}
    for entry in document["aliases"]:
        source_leaf = str(entry["source_leaf"])
        target_leaf = str(entry["target_leaf"])
        if source_leaf in aliases:
            raise InputError(f"duplicate seed taxonomy alias: {source_leaf}")
        if source_leaf == target_leaf:
            raise InputError(f"seed taxonomy alias cannot map a leaf to itself: {source_leaf}")
        if source_leaf.split(".", 1)[0] != target_leaf.split(".", 1)[0]:
            raise InputError(f"seed taxonomy alias cannot cross domains: {source_leaf}")
        aliases[source_leaf] = target_leaf
    return {
        "id": document["id"],
        "version": document["version"],
        "content_digest": actual_digest,
        "aliases": aliases,
    }


def _read_source_spec(
    paths: WorkspacePaths, value: str | Path
) -> tuple[Path, dict[str, Any], str, dict[str, Any]]:
    path = _source_spec_path(paths, value)
    try:
        spec = read_yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else read_json(path)
    except (OSError, InputError) as exc:
        raise InputError(f"cannot read seed source spec {path}: {exc}") from exc
    if not isinstance(spec, Mapping):
        raise InputError("seed source spec must be an object")
    errors = sorted(Draft7Validator(_SOURCE_SPEC_SCHEMA).iter_errors(spec), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise InputError(f"seed source spec schema validation failed at {location}: {error.message}")
    spec_dict = dict(spec)
    require_digest(spec_dict["expected_inventory_digest"], "seed source spec expected_inventory_digest")
    for role, (expected_format, expected_contract) in _ROLE_CONTRACTS.items():
        role_spec = spec_dict["file_roles"][role]
        if role_spec["format"] != expected_format or role_spec["parser_contract"] != expected_contract:
            raise InputError(f"seed source spec role {role} must use {expected_contract}/{expected_format}")
    aliases = _load_taxonomy_aliases(path, spec_dict)
    return path, spec_dict, digest(spec_dict), aliases


def _local_directory(value: str | Path, field: str) -> Path:
    raw = str(value)
    if not raw or "\x00" in raw or raw.startswith(("http://", "https://", "feishu://")):
        raise InputError(f"{field} must be a local directory")
    untrusted = Path(value).expanduser()
    if untrusted.is_symlink():
        raise QuarantinedError(f"{field} cannot be a symlink")
    path = untrusted.resolve()
    if not path.is_dir():
        raise QuarantinedError(f"{field} is not a directory: {path}")
    return path


def _authorized_package_root(spec: Mapping[str, Any], package_root: str | Path) -> Path:
    root_env = spec["root_env"]
    environment_value = os.environ.get(root_env)
    if not environment_value:
        raise InputError(f"seed source root environment variable is not set: {root_env}")
    expected = _local_directory(environment_value, f"seed source environment {root_env}")
    actual = _local_directory(package_root, "seed package root")
    if actual != expected:
        raise QuarantinedError("seed --package-root does not match the SourceSpec root_env directory")
    if actual.name != spec["expected_basename"]:
        raise QuarantinedError("seed package basename does not match the authorized SourceSpec")
    return actual


def _field_value(value: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _nonempty_provenance_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping) or isinstance(value, list):
        return bool(value)
    return value is not None


def _validate_source_constraints(
    *,
    package_root: Path,
    manifest: Mapping[str, Any],
    version_note: str,
    tasks: list[Mapping[str, Any]],
    expert_review: Mapping[str, Any],
    asset_manifest: list[Mapping[str, Any]],
    snapshot: TrustedTreeSnapshot,
    spec: Mapping[str, Any],
) -> None:
    """Validate the semantic closure declared by the EV2 SourceSpec.

    File roles determine where each input comes from.  This function proves
    that those role files still agree about task identity, review coverage,
    and asset provenance before any raw case becomes a private catalog entry.
    """
    expected = spec["expected_package"]
    if manifest.get("package_id") != expected["package_id"]:
        raise QuarantinedError("seed package_id does not match the authorized source spec")
    if len(tasks) != expected["case_count"]:
        raise QuarantinedError("seed case count does not match the authorized source spec")
    manifest_count = manifest.get("task_count")
    if not isinstance(manifest_count, int) or manifest_count != len(tasks):
        raise QuarantinedError("seed manifest task_count does not match the declared task role")

    roles = spec["file_roles"]
    for manifest_field, role in (
        ("tasks_path", "tasks"),
        ("expert_review.manifest", "expert_review"),
        ("asset_closure.manifest", "asset_manifest"),
    ):
        declared = _field_value(manifest, manifest_field)
        if declared != roles[role]["path"]:
            raise QuarantinedError(f"seed manifest {manifest_field} does not match SourceSpec role {role}")

    observed_versions = _version_labels(package_root, manifest, version_note)
    for label, expected_value in expected["version_labels"].items():
        if _semantic_version(observed_versions.get(label)) != _semantic_version(expected_value):
            raise QuarantinedError(f"seed version label does not match SourceSpec: {label}")

    observed_distribution: dict[str, int] = defaultdict(int)
    for task in tasks:
        observed_distribution[_task_status(task)] += 1
    expected_distribution = dict(expected["review_distribution"])
    if dict(sorted(observed_distribution.items())) != dict(sorted(expected_distribution.items())):
        raise QuarantinedError("seed expert-review distribution does not match the authorized source spec")

    provenance = expected["provenance"]
    fields = list(provenance["required_fields"])
    task_ids: set[str] = set()
    task_assets: dict[str, str] = {}
    for task in tasks:
        task_id = _field_value(task, "task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in task_ids:
            raise QuarantinedError("seed task ids must be non-empty and unique")
        task_ids.add(task_id)
        for field in fields:
            field_value = _field_value(task, field)
            if not _nonempty_provenance_value(field_value):
                raise QuarantinedError(f"seed task {task_id} lacks required provenance field: {field}")
        asset_reference = _field_value(task, "asset.repo_path")
        if not isinstance(asset_reference, str) or not asset_reference.strip():
            raise QuarantinedError(f"seed task {task_id} asset provenance must be a non-empty string")
        asset_digest = _field_value(task, "asset.sha256")
        if provenance["require_asset_sha256"] and (
            not isinstance(asset_digest, str) or not _RAW_SHA256_RE.fullmatch(asset_digest)
        ):
            raise QuarantinedError(f"seed task {task_id} asset.sha256 is not a raw SHA-256 digest")
        if provenance["require_source_suite"]:
            source_suite = _field_value(task, "provenance.source_suite")
            if not isinstance(source_suite, str) or not source_suite.strip():
                raise QuarantinedError(f"seed task {task_id} has no provenance.source_suite")
        prior_task_digest = task_assets.setdefault(asset_reference, str(asset_digest))
        if prior_task_digest != str(asset_digest):
            raise QuarantinedError("seed task records disagree about one asset provenance digest")

    # ``repo_path`` is provenance text.  It is intentionally never used to
    # open a local file: the declared ``package_path`` names the captured
    # package member whose bytes close the asset record.
    assets_by_provenance: dict[str, str] = {}
    asset_package_paths: set[str] = set()
    for asset in asset_manifest:
        asset_path = asset.get("repo_path")
        asset_digest = asset.get("sha256")
        package_path = asset.get("package_path")
        byte_size = asset.get("byte_size")
        if (
            not isinstance(asset_path, str)
            or not asset_path
            or not isinstance(asset_digest, str)
            or not _RAW_SHA256_RE.fullmatch(asset_digest)
        ):
            raise QuarantinedError("asset-manifest role has an invalid repo_path/SHA-256 record")
        if provenance["require_asset_package_path"] and (
            not isinstance(package_path, str) or not package_path
        ):
            raise QuarantinedError("asset-manifest role has an invalid package_path")
        if provenance["require_asset_byte_size"] and (
            not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0
        ):
            raise QuarantinedError("asset-manifest role has an invalid byte_size")
        try:
            packaged_bytes = snapshot.captured(str(package_path))
        except SafeFilesystemError as exc:
            raise QuarantinedError(f"asset-manifest package_path is unsafe or missing: {package_path}") from exc
        try:
            packaged_asset = snapshot.file(str(package_path))
        except SafeFilesystemError as exc:
            raise QuarantinedError(f"asset-manifest package_path is unsafe or missing: {package_path}") from exc
        if packaged_asset.relative_path in asset_package_paths:
            raise QuarantinedError("asset-manifest role has a duplicate package_path")
        asset_package_paths.add(packaged_asset.relative_path)
        if len(packaged_bytes) != byte_size:
            raise QuarantinedError(f"asset-manifest byte_size does not match package asset: {package_path}")
        if sha256(packaged_bytes).hexdigest() != asset_digest:
            raise QuarantinedError(f"asset-manifest SHA-256 does not match package asset: {package_path}")
        prior = assets_by_provenance.setdefault(asset_path, asset_digest)
        if prior != asset_digest:
            raise QuarantinedError("asset-manifest role has conflicting digests for one repo_path")
    for asset_path, asset_digest in task_assets.items():
        if assets_by_provenance.get(asset_path) != asset_digest:
            raise QuarantinedError("seed task asset provenance does not close against asset-manifest role")

    reviews = expert_review.get("reviews")
    anchors = expert_review.get("anchors")
    if not isinstance(reviews, int) or reviews != len(tasks) or not isinstance(anchors, list):
        raise QuarantinedError("expert-review role does not cover the declared task count")
    anchored_task_ids: set[str] = set()
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            raise QuarantinedError("expert-review role has a malformed anchor")
        task_id = anchor.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in anchored_task_ids:
            raise QuarantinedError("expert-review role has duplicate or missing task anchors")
        anchored_task_ids.add(task_id)
    if anchored_task_ids != task_ids:
        raise QuarantinedError("expert-review role task anchors do not close over the task role")


def _write_quarantine(
    paths: WorkspacePaths,
    *,
    package_path: str | Path,
    reason: str,
    source_spec_path: Path | None = None,
    source_spec_digest: str | None = None,
    inventory: Mapping[str, Any] | None = None,
) -> None:
    """Retain a minimal local audit record without emitting usable seed data."""
    package = Path(package_path).expanduser().resolve()
    inventory_digest = inventory.get("inventory_digest") if isinstance(inventory, Mapping) else None
    identifier_seed = inventory_digest if isinstance(inventory_digest, str) else digest({"package": str(package), "reason": reason})
    receipt = make_envelope(
        kind="seed_source_quarantine",
        identifier=f"seed-quarantine-{_short(identifier_seed)}",
        status="quarantined",
        body={
            "package_path": str(package),
            "inventory_digest": inventory_digest,
            "source_spec_path": str(source_spec_path) if source_spec_path else None,
            "source_spec_digest": source_spec_digest,
            "reason": reason,
        },
    )
    output = paths.snapshots / "seed" / f"{_short(receipt['content_digest'])}.quarantine.json"
    atomic_write_json(output, receipt, private=True)
    append_event(
        paths,
        "seed_sync_quarantined",
        {"inventory_digest": inventory_digest, "source_spec_digest": source_spec_digest, "reason": reason},
    )


def _decode_role_text(value: bytes, label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InputError(f"seed {label} role is not valid UTF-8: {exc}") from exc


def _read_json_role(value: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(_decode_role_text(value, label))
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in seed {label} role: {exc}") from exc
    if not isinstance(parsed, dict):
        raise QuarantinedError(f"seed {label} role must contain a JSON object")
    return parsed


def _read_json_lines(value: bytes, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = _decode_role_text(value, label)
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError(f"invalid JSONL in seed {label} role at line {line_number}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InputError(f"JSONL row in seed {label} role at line {line_number} is not an object")
        parsed["_source_line"] = line_number
        rows.append(parsed)
    return rows


def _load_role_inputs(snapshot: TrustedTreeSnapshot, spec: Mapping[str, Any]) -> tuple[
    dict[str, str], dict[str, Any], str, list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]
]:
    """Parse all EV2 roles from the bytes used to build the inventory."""
    role_specs = spec.get("file_roles")
    if not isinstance(role_specs, Mapping):
        raise QuarantinedError("seed source spec has no file_roles mapping")
    try:
        paths = {role: str(role_specs[role]["path"]) for role in _ROLE_CONTRACTS}
        role_bytes = {role: snapshot.captured(relative) for role, relative in paths.items()}
    except (KeyError, TypeError, SafeFilesystemError) as exc:
        raise QuarantinedError(f"seed role snapshot is incomplete or unsafe: {exc}") from exc
    manifest = _read_json_role(role_bytes["manifest"], "manifest")
    expert_review = _read_json_role(role_bytes["expert_review"], "expert_review")
    version_note = _decode_role_text(role_bytes["version_note"], "version_note")
    return (
        paths,
        manifest,
        version_note,
        _read_json_lines(role_bytes["tasks"], "tasks"),
        expert_review,
        _read_json_lines(role_bytes["asset_manifest"], "asset_manifest"),
    )


def _asset_capture_paths(
    snapshot: TrustedTreeSnapshot,
    asset_manifest: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Resolve all closure members before the final byte-retaining snapshot.

    The bootstrap snapshot only captures small role files.  We use its captured
    asset-manifest bytes to discover package members, then take a second
    snapshot whose stability signature must match before treating it as the
    immutable source of both role and asset bytes.
    """

    paths: list[str] = []
    for asset in asset_manifest:
        package_path = asset.get("package_path") if isinstance(asset, Mapping) else None
        if not isinstance(package_path, str) or not package_path:
            raise QuarantinedError("asset-manifest role has an invalid package_path")
        try:
            snapshot.file(package_path)
        except SafeFilesystemError as exc:
            raise QuarantinedError(
                f"asset-manifest package_path is unsafe or missing: {package_path}"
            ) from exc
        paths.append(package_path)
    return paths


def _inventory(snapshot: TrustedTreeSnapshot) -> dict[str, Any]:
    entries = snapshot.inventory_entries()
    return {
        "algorithm": "ev2_local_package_v1:file_inventory_v1",
        "entries": entries,
        # The digest intentionally covers exactly the canonical regular-file
        # list above.  It remains compatible with the checked-in EV2 spec.
        "inventory_digest": digest(entries),
    }


def _semantic_version(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.search(value)
    return match.group(1).lower().removeprefix("v") if match else None


def _version_label(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise QuarantinedError(f"seed package has no {label} version label")
    match = _VERSION_RE.search(value)
    if not match:
        raise QuarantinedError(f"seed package has no {label} version label")
    return match.group(1)


def _version_labels(package_root: Path, manifest: Mapping[str, Any], version_note_text: str) -> dict[str, str]:
    return {
        "directory": _version_label(package_root.name, "directory"),
        "manifest": _version_label(manifest.get("version"), "manifest"),
        "package_id": _version_label(manifest.get("package_id"), "package_id"),
        "version_note": _version_label(version_note_text, "version_note"),
    }


def _safe_leaf(domain: str, subdomain: str) -> tuple[str, str, str]:
    domain_value = require_nonempty_string(domain, "task domain").lower().replace("-", "_")
    subdomain_value = require_nonempty_string(subdomain, "task subdomain").lower().replace("-", "_")
    safe_component(domain_value, "domain")
    safe_component(subdomain_value, "subdomain")
    return domain_value, subdomain_value, f"{domain_value}.{subdomain_value}"


def _task_status(task: Mapping[str, Any]) -> str:
    expert_review = task.get("expert_review")
    status = expert_review.get("verdict_class") if isinstance(expert_review, Mapping) else None
    if status not in VALID_REVIEW_STATUSES:
        raise InputError(f"task {task.get('task_id', '<unknown>')} has unsupported expert review status {status!r}")
    return str(status)


def _normalized_case(
    task: Mapping[str, Any],
    inventory_digest: str,
    taxonomy_aliases: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    task_id = require_nonempty_string(task.get("task_id"), "task_id")
    domain = task.get("domain")
    subdomain_data = task.get("sub_domain")
    if not isinstance(subdomain_data, Mapping):
        raise InputError(f"task {task_id} has no sub_domain mapping")
    subdomain = subdomain_data.get("slug")
    source_domain, source_subdomain, source_leaf_id = _safe_leaf(str(domain), str(subdomain))
    leaf_id = taxonomy_aliases.get(source_leaf_id, source_leaf_id)
    domain_value, subdomain_value, canonical_leaf_id = _safe_leaf(*leaf_id.split(".", 1))
    if canonical_leaf_id != leaf_id:
        raise InputError(f"seed taxonomy alias target is not canonical: {leaf_id}")
    status = _task_status(task)
    answer_schema = task.get("answer_schema") if isinstance(task.get("answer_schema"), Mapping) else {}
    asset = task.get("asset") if isinstance(task.get("asset"), Mapping) else {}
    tool_policy = task.get("tool_policy") if isinstance(task.get("tool_policy"), Mapping) else {}
    declared_gap = task.get("declared_gap") if isinstance(task.get("declared_gap"), Mapping) else {}
    line_digest = digest({key: value for key, value in task.items() if key != "_source_line"})
    opaque_id = _short(digest({"inventory": inventory_digest, "task_id": task_id}))

    private_case = {
        "task_id": task_id,
        "source_line": task.get("_source_line"),
        "source_line_digest": line_digest,
        "domain": domain_value,
        "subdomain": subdomain_value,
        "leaf_id": leaf_id,
        "source_domain": source_domain,
        "source_subdomain": source_subdomain,
        "source_leaf_id": source_leaf_id,
        "review_status": status,
        "raw_task": {key: value for key, value in task.items() if key != "_source_line"},
    }
    public_card = {
        "card_id": f"seed-card-{opaque_id}",
        "leaf_id": leaf_id,
        "source_status": status,
        "scenario_type": leaf_id,
        "visual_skills": [task.get("v_class")] if isinstance(task.get("v_class"), str) else [],
        "answer_form": answer_schema.get("kind"),
        "answer_fields": list(answer_schema.get("fields", [])) if isinstance(answer_schema.get("fields"), list) else [],
        "viewer_affordances": [asset.get("viewer")] if isinstance(asset.get("viewer"), str) else [],
        "allowed_viewer_tools": list(tool_policy.get("allowed_tools", [])) if isinstance(tool_policy.get("allowed_tools"), list) else [],
        "anti_patterns": [],
    }
    if status != "verified":
        gap_kind = declared_gap.get("kind") if declared_gap else None
        public_card["anti_patterns"] = [f"source_status:{status}"] + ([f"gap:{gap_kind}"] if gap_kind else [])

    lesson = {
        "lesson_id": f"seed-lesson-{opaque_id}",
        "origin_status": status,
        "visual_skills": public_card["visual_skills"],
        "answer_form": public_card["answer_form"],
        "viewer_affordances": public_card["viewer_affordances"],
        "rule": (
            "Use controlled viewer evidence for the declared answer form."
            if status == "verified"
            else "Do not release until the seed's premise, asset, and review blockers are resolved."
        ),
    }
    if status != "verified":
        lesson["anti_pattern_classes"] = public_card["anti_patterns"]
    return private_case, public_card, lesson


def _load_existing_resolution(
    paths: WorkspacePaths,
    inventory_digest: str,
    request_digest: str,
    source_spec_digest: str,
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for path in (paths.receipts / "source").glob("*.resolution.json"):
        try:
            value = read_json(path)
            if not isinstance(value, Mapping):
                continue
            receipt = verify_envelope(value, kind="source_version_resolution_receipt")
        except (InputError, OSError):
            continue
        body = receipt["body"]
        if (
            body.get("inventory_digest") == inventory_digest
            and body.get("request_digest") == request_digest
            and body.get("source_spec_digest") == source_spec_digest
        ):
            matches.append(receipt)
    if len(matches) > 1:
        raise InvalidStateError("source version request has multiple immutable resolution receipts")
    return matches[0] if matches else None


def _write_seed_artifacts(
    paths: WorkspacePaths,
    *,
    package_root: Path,
    inventory: Mapping[str, Any],
    versions: Mapping[str, str],
    tasks: Iterable[Mapping[str, Any]],
    eligible: bool,
    resolution_ref: str | None,
    source_spec_path: Path,
    source_spec_digest: str,
    taxonomy_alias_contract: Mapping[str, Any],
    local_only: bool,
    before_commit: Callable[[], None] | None = None,
) -> dict[str, Any]:
    inventory_digest = str(inventory["inventory_digest"])
    status = "eligible" if eligible else "version_resolution_pending"
    private_cases: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    lessons: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"positive": [], "anti_pattern": []}
    )
    seen_task_ids: set[str] = set()
    for task in tasks:
        private_case, card, lesson = _normalized_case(
            task,
            inventory_digest,
            taxonomy_alias_contract["aliases"],
        )
        task_id = private_case["task_id"]
        if task_id in seen_task_ids:
            raise InputError(f"duplicate task_id in seed package: {task_id}")
        seen_task_ids.add(task_id)
        private_cases.append(private_case)
        cards.append(card)
        domain, subdomain, leaf = _safe_leaf(private_case["domain"], private_case["subdomain"])
        if private_case["review_status"] == "verified":
            lessons[(domain, subdomain, leaf)]["positive"].append(lesson)
        else:
            lessons[(domain, subdomain, leaf)]["anti_pattern"].append(lesson)

    catalog = make_envelope(
        kind="seed_catalog",
        identifier=f"seed-catalog-{_short(inventory_digest)}",
        status=status,
        body={
            "inventory_digest": inventory_digest,
            "package_name": package_root.name,
            "source_spec_path": str(source_spec_path),
            "source_spec_digest": source_spec_digest,
            "taxonomy_alias_lock": {
                "id": taxonomy_alias_contract["id"],
                "version": taxonomy_alias_contract["version"],
                "content_digest": taxonomy_alias_contract["content_digest"],
            },
            "local_only": local_only,
            "production_eligible": False,
            "observed_versions": dict(versions),
            "case_count": len(private_cases),
            "cases": private_cases,
            "resolution_ref": resolution_ref,
        },
    )
    if before_commit is not None:
        before_commit()
    catalog_path = paths.catalog / f"{_short(inventory_digest)}.catalog.json"
    atomic_write_json(catalog_path, catalog, private=True)

    cards_artifact = make_envelope(
        kind="seed_cards",
        identifier=f"seed-cards-{_short(inventory_digest)}",
        status=status,
        body={
            "seed_catalog_digest": catalog["content_digest"],
            "inventory_digest": inventory_digest,
            "source_spec_digest": source_spec_digest,
            "taxonomy_alias_lock": {
                "id": taxonomy_alias_contract["id"],
                "version": taxonomy_alias_contract["version"],
                "content_digest": taxonomy_alias_contract["content_digest"],
            },
            "local_only": local_only,
            "cards": sorted(cards, key=lambda item: item["card_id"]),
        },
    )
    cards_path = paths.cards / f"{_short(inventory_digest)}.cards.json"
    atomic_write_json(cards_path, cards_artifact, private=True)

    lesson_paths: list[str] = []
    lesson_index_paths: list[str] = []
    for (domain, subdomain, leaf), grouped in sorted(lessons.items()):
        lesson_artifact = make_envelope(
            kind="seed_lesson_set",
            identifier=leaf,
            status=status,
            body={
                "leaf_id": leaf,
                "domain": domain,
                "subdomain": subdomain,
                "seed_catalog_digest": catalog["content_digest"],
                "inventory_digest": inventory_digest,
                "source_spec_digest": source_spec_digest,
                "taxonomy_alias_lock": {
                    "id": taxonomy_alias_contract["id"],
                    "version": taxonomy_alias_contract["version"],
                    "content_digest": taxonomy_alias_contract["content_digest"],
                },
                "local_only": local_only,
                "production_eligible": False,
                "positive_lessons": sorted(grouped["positive"], key=lambda item: item["lesson_id"]),
                "anti_pattern_lessons": sorted(grouped["anti_pattern"], key=lambda item: item["lesson_id"]),
            },
        )
        path = paths.knowledge / "seed-lessons" / domain / f"{subdomain}.yaml"
        atomic_write_yaml(path, lesson_artifact, private=False)
        lesson_paths.append(str(path))
        lesson_index_paths.append(str(path.relative_to(paths.root)))

    seed_entries = [
        {
            "seed_ref": card["card_id"],
            "leaf_id": card["leaf_id"],
            "domain": card["leaf_id"].split(".", 1)[0],
            "subdomain": card["leaf_id"].split(".", 1)[1],
            "status": card["source_status"],
            "tags": list(card["visual_skills"]),
        }
        for card in sorted(cards, key=lambda item: item["card_id"])
    ]
    seed_index_body = {
        "mode": "workspace_registry",
        "inventory_digest": inventory_digest,
        "seed_catalog_digest": catalog["content_digest"],
        "catalog_path": str(catalog_path.relative_to(paths.root)),
        "cards_digest": cards_artifact["content_digest"],
        "cards_path": str(cards_path.relative_to(paths.root)),
        "package_name": package_root.name,
        "source_spec_path": str(source_spec_path),
        "source_spec_digest": source_spec_digest,
        "local_only": local_only,
        "production_eligible": False,
        "observed_versions": dict(versions),
        "resolution_ref": resolution_ref,
        "eligible": eligible,
        "lesson_paths": lesson_index_paths,
        "entries": seed_entries,
    }
    provisional_seed_index = make_envelope(
        kind="seed_index",
        identifier="seed-index",
        status=status,
        body=seed_index_body,
    )
    seed_index = make_envelope(
        kind="seed_index",
        identifier=f"seed-index-{_short(provisional_seed_index['content_digest'])}",
        status=status,
        body=seed_index_body,
    )
    validate_seed_index(seed_index)
    seed_index_path = paths.catalog / f"{_short(seed_index['content_digest'])}.seed-index.json"
    atomic_write_json(seed_index_path, seed_index, private=True)
    atomic_write_json(paths.catalog / "current.json", seed_index, private=True)
    return {
        "catalog": catalog,
        "cards": cards_artifact,
        "index": seed_index,
        "catalog_path": str(catalog_path),
        "cards_path": str(cards_path),
        "seed_index_path": str(seed_index_path),
        "lesson_paths": lesson_paths,
    }


def _invalidate_knowledge_index_for_seed(paths: WorkspacePaths, seed_catalog_digest: str) -> None:
    index_path = paths.knowledge / "index.yaml"
    if not index_path.exists():
        return
    try:
        index = validate_knowledge_index(read_yaml(index_path))
    except InputError:
        return
    entries = deepcopy(index["body"].get("entries", {}))
    changed = False
    for entry in entries.values():
        if entry.get("seed_catalog_digest") != seed_catalog_digest and entry.get("state") == "approved":
            entry["state"] = "superseded"
            entry["superseded_reason"] = "seed_catalog_digest_changed"
            changed = True
    if changed:
        index["body"]["entries"] = entries
        replacement_body = index["body"]
        provisional = make_envelope(
            kind="knowledge_index",
            identifier="knowledge-index",
            status="contains_superseded_entries",
            body=replacement_body,
        )
        replacement = make_envelope(
            kind="knowledge_index",
            identifier=f"knowledge-index-{_short(provisional['content_digest'])}",
            status="contains_superseded_entries",
            body=replacement_body,
        )
        validate_knowledge_index(replacement)
        frozen_index_path = paths.knowledge / "indexes" / (
            f"{_short(replacement['content_digest'])}.knowledge-index.yaml"
        )
        atomic_write_yaml(frozen_index_path, replacement, private=False)
        atomic_write_yaml(index_path, replacement, private=False)


def _verify_source_unchanged(source_root: TrustedSourceRoot, snapshot: TrustedTreeSnapshot) -> None:
    try:
        verify_trusted_tree_snapshot(source_root, snapshot)
    except SafeFilesystemError as exc:
        raise QuarantinedError(f"seed source changed during sync: {exc}") from exc


def _sync_seed_package_locked(
    paths: WorkspacePaths,
    *,
    source_root: TrustedSourceRoot,
    spec_path: Path,
    spec: Mapping[str, Any],
    spec_digest: str,
    taxonomy_alias_contract: Mapping[str, Any],
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role_specs = spec.get("file_roles")
    if not isinstance(role_specs, Mapping):
        raise QuarantinedError("seed source spec has no file_roles mapping")
    capture_paths = [str(role_specs[role]["path"]) for role in _ROLE_CONTRACTS]
    try:
        snapshot = snapshot_trusted_tree(
            source_root,
            capture_paths=capture_paths,
            max_capture_bytes=_MAX_ROLE_CAPTURE_BYTES,
        )
    except SafeFilesystemError as exc:
        raise QuarantinedError(f"seed package could not be snapshotted safely: {exc}") from exc
    inventory = _inventory(snapshot)
    inventory_digest = inventory["inventory_digest"]
    if audit_context is not None:
        audit_context["inventory_digest"] = inventory_digest
    if inventory_digest != spec["expected_inventory_digest"]:
        raise QuarantinedError("seed package inventory digest does not match the authorized SourceSpec")
    _, manifest, version_note, tasks, expert_review, asset_manifest = _load_role_inputs(snapshot, spec)
    asset_capture_paths = _asset_capture_paths(snapshot, asset_manifest)
    if asset_capture_paths:
        try:
            byte_snapshot = snapshot_trusted_tree(
                source_root,
                capture_paths=[*capture_paths, *asset_capture_paths],
                max_capture_bytes=_MAX_ROLE_CAPTURE_BYTES,
            )
        except SafeFilesystemError as exc:
            raise QuarantinedError(f"seed package assets could not be snapshotted safely: {exc}") from exc
        if byte_snapshot.stability_signature() != snapshot.stability_signature():
            raise QuarantinedError("seed source changed during sync while capturing asset closure bytes")
        # Reparse role bytes from the same immutable snapshot that supplied the
        # contained asset bytes below.  Nothing from the bootstrap capture is
        # used as semantic input after this point.
        snapshot = byte_snapshot
        _, manifest, version_note, tasks, expert_review, asset_manifest = _load_role_inputs(snapshot, spec)
    versions = _version_labels(source_root.path, manifest, version_note)
    _validate_source_constraints(
        package_root=source_root.path,
        manifest=manifest,
        version_note=version_note,
        tasks=tasks,
        expert_review=expert_review,
        asset_manifest=asset_manifest,
        snapshot=snapshot,
        spec=spec,
    )
    # No source-derived output is emitted until a second full-tree hash proves
    # the immutable role snapshot is still the current package state.
    _verify_source_unchanged(source_root, snapshot)
    local_only = bool(spec["local_only"])

    inventory_artifact = make_envelope(
        kind="source_inventory",
        identifier=f"seed-package-{_short(inventory_digest)}",
        status="authorized_local",
        body={
            "inventory_digest": inventory_digest,
            "inventory_algorithm": inventory["algorithm"],
            "package_name": source_root.path.name,
            "source_spec_path": str(spec_path),
            "source_spec_digest": spec_digest,
            "taxonomy_alias_lock": {
                "id": taxonomy_alias_contract["id"],
                "version": taxonomy_alias_contract["version"],
                "content_digest": taxonomy_alias_contract["content_digest"],
            },
            "file_roles": spec["file_roles"],
            "local_only": local_only,
            "production_eligible": False,
            "entries": inventory["entries"],
            "observed_versions": versions,
        },
    )
    atomic_write_json(paths.snapshots / "seed" / f"{_short(inventory_digest)}.inventory.json", inventory_artifact, private=True)

    distinct_versions = sorted({value for value in (_semantic_version(label) for label in versions.values()) if value})
    request: dict[str, Any] | None = None
    request_path: Path | None = None
    resolution: dict[str, Any] | None = None
    eligible = len(distinct_versions) <= 1
    if not eligible:
        request = make_envelope(
            kind="source_version_resolution_request",
            identifier=f"source-version-{_short(inventory_digest)}",
            status="pending",
            body={
                "inventory_digest": inventory_digest,
                "package_name": source_root.path.name,
                "source_spec_digest": spec_digest,
                "expected_inventory_digest": spec["expected_inventory_digest"],
                "observed_versions": versions,
                "acceptable_versions": distinct_versions,
            },
        )
        request_path = paths.receipts / "source" / (
            f"{_short(inventory_digest)}.{_short(request['content_digest'])}.request.json"
        )
        atomic_write_json(request_path, request, private=True)
        resolution = _load_existing_resolution(paths, inventory_digest, request["content_digest"], spec_digest)
        if resolution is not None:
            decision = resolution["body"].get("decision")
            if decision == "accept":
                eligible = True
            elif decision == "reject":
                artifacts = _write_seed_artifacts(
                    paths,
                    package_root=source_root.path,
                    inventory=inventory,
                    versions=versions,
                    tasks=tasks,
                    eligible=False,
                    resolution_ref=resolution["content_digest"],
                    source_spec_path=spec_path,
                    source_spec_digest=spec_digest,
                    taxonomy_alias_contract=taxonomy_alias_contract,
                    local_only=local_only,
                    before_commit=lambda: _verify_source_unchanged(source_root, snapshot),
                )
                append_event(paths, "seed_sync_quarantined", {"inventory_digest": inventory_digest})
                raise QuarantinedError(
                    f"source version resolution rejected; request retained at {request_path}; catalog={artifacts['catalog_path']}"
                )

    artifacts = _write_seed_artifacts(
        paths,
        package_root=source_root.path,
        inventory=inventory,
        versions=versions,
        tasks=tasks,
        eligible=eligible,
        resolution_ref=resolution["content_digest"] if resolution else None,
        source_spec_path=spec_path,
        source_spec_digest=spec_digest,
        taxonomy_alias_contract=taxonomy_alias_contract,
        local_only=local_only,
        before_commit=lambda: _verify_source_unchanged(source_root, snapshot),
    )
    _invalidate_knowledge_index_for_seed(paths, artifacts["catalog"]["content_digest"])
    append_event(
        paths,
        "seed_sync",
        {
            "inventory_digest": inventory_digest,
            "task_count": len(tasks),
            "eligible": eligible,
            "source_spec_digest": spec_digest,
            "local_only": local_only,
            "seed_catalog_digest": artifacts["catalog"]["content_digest"],
        },
    )
    result = {
        "inventory_digest": inventory_digest,
        "observed_versions": versions,
        "task_count": len(tasks),
        "eligible": eligible,
        "source_spec_digest": spec_digest,
        "taxonomy_alias_digest": taxonomy_alias_contract["content_digest"],
        "local_only": local_only,
        "production_eligible": False,
        "catalog_path": artifacts["catalog_path"],
        "cards_path": artifacts["cards_path"],
        "seed_index_path": artifacts["seed_index_path"],
        "seed_index_digest": artifacts["index"]["content_digest"],
        "lesson_paths": artifacts["lesson_paths"],
    }
    if request is not None and not eligible and request_path is not None:
        result["request_path"] = str(request_path)
    return result


def sync_seed_package(
    paths: WorkspacePaths,
    package_root: str | Path,
    *,
    source_spec_path: str | Path | None = None,
) -> dict[str, Any]:
    """Import one stable EV2 snapshot under a source-root process lock."""
    paths.ensure()
    spec_path: Path | None = None
    spec_digest: str | None = None
    audit_context: dict[str, Any] = {}
    try:
        if source_spec_path is None:
            raise QuarantinedError("seed sync requires an explicit authorized --source-spec")
        spec_path, spec, spec_digest, taxonomy_alias_contract = _read_source_spec(paths, source_spec_path)
        resolved_root = _authorized_package_root(spec, package_root)
        with lock_trusted_source_root(resolved_root) as source_root:
            result = _sync_seed_package_locked(
                paths,
                source_root=source_root,
                spec_path=spec_path,
                spec=spec,
                spec_digest=spec_digest,
                taxonomy_alias_contract=taxonomy_alias_contract,
                audit_context=audit_context,
            )
            return result
    except (InputError, QuarantinedError, SafeFilesystemError, FileNotFoundError) as exc:
        _write_quarantine(
            paths,
            package_path=package_root,
            reason=str(exc),
            source_spec_path=spec_path,
            source_spec_digest=spec_digest,
            inventory=audit_context,
        )
        if isinstance(exc, QuarantinedError):
            raise
        raise QuarantinedError(f"seed package/source spec failed closed: {exc}") from exc


def accept_source_version(
    paths: WorkspacePaths,
    *,
    request_path: str | Path,
    reviewer: str,
    resolved_version: str,
    decision: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Record the explicit resolution required before a conflicting package is eligible."""
    paths.ensure()
    if decision not in {"accept", "reject"}:
        raise InputError("decision must be accept or reject")
    if not rationale.strip():
        raise InputError("source version resolution requires a non-empty rationale")
    resolved_request_path = require_within(paths.receipts / "source", request_path, "source version request")
    initial = verify_envelope(read_json(resolved_request_path), kind="source_version_resolution_request")
    request_digest = initial["content_digest"]
    with _source_decision_lock(paths, request_digest):
        request = verify_envelope(read_json(resolved_request_path), kind="source_version_resolution_request")
        if request["content_digest"] != request_digest:
            raise InvalidStateError("source version request changed before its resolution")
        if request["status"] != "pending":
            raise InvalidStateError("source version request is not pending")
        body = request["body"]
        inventory_digest = body.get("inventory_digest")
        source_spec_digest = body.get("source_spec_digest")
        versions = body.get("acceptable_versions")
        if not isinstance(inventory_digest, str) or not isinstance(versions, list):
            raise InputError("source version request body is malformed")
        require_digest(source_spec_digest, "source version request source_spec_digest")
        canonical_version = _semantic_version(resolved_version)
        if canonical_version is None or canonical_version not in versions:
            raise InputError("resolved_version must be one of the observed versions")
        existing = _load_existing_resolution(paths, inventory_digest, request_digest, source_spec_digest)
        if existing is not None:
            raise InvalidStateError(
                "source version request already has an immutable resolution; provide a new inventory/request to decide again"
            )
        receipt = make_decision(
            kind="source_version_resolution_receipt",
            identifier=f"source-resolution-{_short(inventory_digest)}",
            decision=decision,
            actor_id=reviewer,
            actor_role="source_version_reviewer",
            bound_digests={
                "request_digest": request_digest,
                "inventory_digest": inventory_digest,
                "source_spec_digest": source_spec_digest,
            },
            rationale=rationale,
            extra={
                "request_digest": request_digest,
                "inventory_digest": inventory_digest,
                "source_spec_digest": source_spec_digest,
                "observed_versions": body.get("observed_versions", {}),
                "resolved_version": canonical_version,
            },
        )
        receipt_path = paths.receipts / "source" / (
            f"{_short(inventory_digest)}.{decision}.{_short(receipt['content_digest'])}.resolution.json"
        )
        created = False
        try:
            _create_source_resolution(receipt_path, receipt)
            created = True
            append_event(
                paths,
                "source_version_resolution",
                {"inventory_digest": inventory_digest, "decision": decision, "receipt_digest": receipt["content_digest"]},
            )
        except FileExistsError as exc:
            raise InvalidStateError("source version resolution receipt already exists") from exc
        except Exception:
            if created:
                receipt_path.unlink(missing_ok=True)
            raise
        return {"receipt_path": str(receipt_path), "receipt_digest": receipt["content_digest"], "decision": decision}
