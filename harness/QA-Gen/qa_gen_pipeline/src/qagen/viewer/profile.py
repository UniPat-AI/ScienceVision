"""Local-only ViewerProfile creation and lock revalidation."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, make_envelope, require_digest, verify_envelope
from ..errors import InputError, StaleInputError
from ..paths import WorkspacePaths, require_within
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from .adapter import ViewerAdapterError
from .host_build import (
    load_chirality_host_build,
    load_chirality_session_evidence_schema,
)


class ViewerProfileError(InputError):
    """A local definition or controlled-session evidence record is invalid."""


_FORBIDDEN_CAPABILITIES = {
    "network",
    "asset_file_read",
    "raw_asset_metadata",
    "raw_asset_read",
    "arbitrary_compute",
    "unrestricted_compute",
}


def _private_0600(path: Path, field: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ViewerProfileError(f"{field} must be a regular private file")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ViewerProfileError(f"cannot inspect {field} permissions") from exc
    if mode != 0o600:
        raise ViewerProfileError(f"{field} permissions must be 0600")


def _local_path(value: str | Path, field: str) -> Path:
    raw = str(value)
    if raw.startswith(("http://", "https://", "feishu://")):
        raise ViewerProfileError(f"{field} must be a local file, not a live URL")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ViewerProfileError(f"{field} is not a local file: {path}")
    return path


def _read_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            value = read_json(path)
    except (OSError, yaml.YAMLError) as exc:
        raise ViewerProfileError(f"cannot read local viewer document {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ViewerProfileError(f"viewer document must be an object: {path}")
    return dict(value)


def _find_schema(path: Path, name: str) -> Path | None:
    for parent in (path.parent, *path.parents):
        candidate = parent / "schemas" / name
        if candidate.is_file():
            return candidate
    return None


def _validate_schema(value: Mapping[str, Any], path: Path, schema_name: str) -> None:
    schema_path = _find_schema(path, schema_name)
    if schema_path is None:
        return
    try:
        schema = read_json(schema_path)
        errors = sorted(Draft7Validator(schema).iter_errors(value), key=lambda error: list(error.absolute_path))
    except Exception as exc:  # jsonschema errors are invalid local configuration.
        raise ViewerProfileError(f"cannot validate viewer schema {schema_path}: {exc}") from exc
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ViewerProfileError(f"viewer schema validation failed at {location}: {error.message}")


def _resolve_definition(path: Path) -> tuple[Path, Path, dict[str, Any], str, str]:
    """Resolve a definition input without losing its canonical lock target.

    A source wrapper and its fixture are different lockable documents.  Keep
    both identities: otherwise a profile can record the wrapper digest beside
    the fixture path and falsely become stale on its first revalidation.
    """
    source_path = path
    source = _read_document(source_path)
    source_digest = digest(source)
    if source.get("kind") == "qagen_viewer_definition_source":
        _validate_schema(source, source_path, "source.schema.json")
        fixture_path_value = source.get("fixture_path")
        if not isinstance(fixture_path_value, str):
            raise ViewerProfileError("viewer source has no fixture_path")
        fixture_path = (source_path.parent / fixture_path_value).resolve()
        try:
            fixture_path.relative_to(source_path.parent.parent)
        except ValueError as exc:
            raise ViewerProfileError("viewer fixture path escapes its config root") from exc
        if not fixture_path.is_file():
            raise ViewerProfileError(f"viewer fixture does not exist: {fixture_path}")
        path = fixture_path
        source = _read_document(path)
    _validate_schema(source, path, "viewer.schema.json")
    if source.get("kind") != "qagen_viewer_definition_fixture":
        raise ViewerProfileError("viewer definition must be qagen_viewer_definition_fixture")
    payload = source.get("fixture_payload")
    declared_digest = source.get("fixture_payload_digest")
    if not isinstance(payload, Mapping):
        raise ViewerProfileError("viewer definition fixture_payload must be an object")
    require_digest(declared_digest, "viewer fixture_payload_digest")
    actual_digest = digest(dict(payload))
    if declared_digest != actual_digest:
        raise ViewerProfileError("viewer fixture_payload_digest does not match fixture_payload")
    fixture = source.get("fixture")
    if not isinstance(fixture, Mapping):
        raise ViewerProfileError("viewer definition fixture metadata is missing")
    return source_path, path, dict(source), source_digest, digest(source)


def _load_session_evidence(path: Path, definition_digest: str) -> tuple[dict[str, Any], str]:
    evidence = _read_document(path)
    reference = evidence.get("viewer_definition_digest", evidence.get("definition_digest"))
    require_digest(reference, "viewer session evidence definition digest")
    if reference != definition_digest:
        raise ViewerProfileError("viewer session evidence is bound to another definition")
    operations = evidence.get("allowed_operations")
    if not isinstance(operations, list) or not operations or not all(isinstance(item, str) and item for item in operations):
        raise ViewerProfileError("viewer session evidence requires non-empty allowed_operations")
    forbidden_values = evidence.get("forbidden_capabilities", [])
    if not isinstance(forbidden_values, list) or not all(isinstance(value, str) and value for value in forbidden_values):
        raise ViewerProfileError("viewer session evidence forbidden_capabilities must be a string list")
    forbidden = set(forbidden_values)
    if not _FORBIDDEN_CAPABILITIES.issubset(forbidden):
        raise ViewerProfileError("viewer session evidence must explicitly forbid network/raw asset/arbitrary compute")
    if evidence.get("network_access", False) or evidence.get("asset_file_read", False):
        raise ViewerProfileError("viewer session evidence permits a forbidden capability")
    return evidence, digest(evidence)


def _validate_controlled_session(payload: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Require the requested session to be no more capable than its definition."""
    capabilities = payload.get("capabilities", [])
    if not isinstance(capabilities, list) or not capabilities or not all(isinstance(item, str) and item for item in capabilities):
        raise ViewerProfileError("viewer definition capabilities must be a non-empty list of strings")
    capability_set = set(capabilities)
    forbidden_values = payload.get("forbidden_capabilities", [])
    if not isinstance(forbidden_values, list) or not all(isinstance(item, str) and item for item in forbidden_values):
        raise ViewerProfileError("viewer definition forbidden_capabilities must be a string list")
    defined_forbidden = set(forbidden_values)
    if not _FORBIDDEN_CAPABILITIES.issubset(defined_forbidden):
        raise ViewerProfileError("viewer definition must declare every globally forbidden capability")
    operations = evidence.get("allowed_operations")
    if not isinstance(operations, list) or not all(isinstance(item, str) and item for item in operations):
        raise ViewerProfileError("viewer session evidence allowed_operations must be a string list")
    operation_set = set(operations)
    if not operation_set.issubset(capability_set):
        raise ViewerProfileError("viewer session evidence requests operations unavailable in the viewer definition")
    if operation_set.intersection(defined_forbidden | _FORBIDDEN_CAPABILITIES):
        raise ViewerProfileError("viewer session evidence permits a forbidden capability as an operation")
    return capability_set, defined_forbidden


def _chirality_scene_binding(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    host_build_path: str | Path,
    session_evidence_schema_path: str | Path,
    payload: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, str]:
    """Validate the private scene/evidence closure for the concrete host.

    A generic ViewerProfile may describe a fixture session.  The chirality
    profile is different: it is only usable when the controlled host has one
    private SceneRegistry and the local capability evidence names that exact
    registry.  The registry path and all derived values remain in the private
    workspace profile and are never solver-facing data.
    """
    from .scene_registry import SceneRegistry

    adapter_id = payload.get("adapter_id")
    if adapter_id != "molstar-chirality-host-v1" or evidence.get("adapter_id") != adapter_id:
        raise ViewerProfileError("chirality host evidence is bound to another adapter")
    if evidence.get("attestation_mode") != "local_chirality_host_contract":
        raise ViewerProfileError("chirality host evidence has an invalid attestation mode")
    if set(evidence.get("allowed_operations", [])) != {"rotate", "render"}:
        raise ViewerProfileError("chirality host evidence must allow exactly rotate and render")
    try:
        registry_file = require_within(paths.private, registry_path, "chirality scene registry")
        registry = SceneRegistry.load(paths, registry_file)
    except InputError as exc:
        raise ViewerProfileError(f"chirality scene registry is invalid: {exc}") from exc
    if not registry.resolution.resolved:
        raise ViewerProfileError("chirality scene registry source version resolution is not ready")
    registry_digest = registry.content_digest
    if evidence.get("scene_registry_digest") != registry_digest:
        raise ViewerProfileError("chirality host evidence is bound to another scene registry")
    refs_digest = digest(sorted(registry.anonymous_scene_refs))
    if evidence.get("anonymous_scene_ref_set_digest") != refs_digest:
        raise ViewerProfileError("chirality host evidence scene-reference lock changed")
    try:
        host_build = load_chirality_host_build(paths, host_build_path, registry)
        session_evidence_schema = load_chirality_session_evidence_schema(paths, session_evidence_schema_path)
    except ViewerAdapterError as exc:
        raise ViewerProfileError(f"chirality host profile evidence is invalid: {exc}") from exc
    if evidence.get("host_build_digest") != host_build["content_digest"]:
        raise ViewerProfileError("chirality host evidence is bound to another host build")
    if evidence.get("session_evidence_schema_digest") != session_evidence_schema["content_digest"]:
        raise ViewerProfileError("chirality host evidence is bound to another session evidence schema")
    host_body = host_build.get("body")
    if not isinstance(host_body, Mapping):
        raise ViewerProfileError("chirality host build body is malformed")
    host_manifest_digest = host_body.get("build_manifest_digest")
    require_digest(host_manifest_digest, "chirality host build manifest digest")
    return {
        "scene_registry_path": str(registry.path),
        "scene_registry_digest": registry_digest,
        "anonymous_scene_ref_set_digest": refs_digest,
        "host_build_path": str(Path(host_build_path).expanduser().resolve()),
        "host_build_digest": str(host_build["content_digest"]),
        "host_build_manifest_digest": str(host_manifest_digest),
        "session_evidence_schema_path": str(Path(session_evidence_schema_path).expanduser().resolve()),
        "session_evidence_schema_digest": str(session_evidence_schema["content_digest"]),
    }


def _workspace_for_private_scene_registry(value: Any) -> WorkspacePaths:
    """Recover the owning workspace only from the canonical private layout."""
    if not isinstance(value, str) or not value:
        raise ViewerProfileError("viewer profile scene-registry path is invalid")
    try:
        path = Path(value).expanduser().resolve()
        registry_dir = path.parent
        viewer_dir = registry_dir.parent
        private_dir = viewer_dir.parent
        qagen_dir = private_dir.parent
    except (OSError, RuntimeError) as exc:
        raise ViewerProfileError("viewer profile scene-registry path is invalid") from exc
    if (
        registry_dir.name != "scene-registries"
        or viewer_dir.name != "viewer"
        or private_dir.name != "private"
        or qagen_dir.name != ".qagen"
    ):
        raise ViewerProfileError("viewer profile scene-registry path is outside the private registry layout")
    return WorkspacePaths.from_value(qagen_dir.parent)


def build_viewer_profile(
    paths: WorkspacePaths,
    *,
    definition_path: str | Path,
    session_evidence_path: str | Path,
    scene_registry_path: str | Path | None = None,
    host_build_path: str | Path | None = None,
    session_evidence_schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a digest-bound local ViewerProfile without opening a browser."""
    paths.ensure()
    definition_source_file, definition_file, definition, source_digest, definition_document_digest = _resolve_definition(
        _local_path(definition_path, "viewer definition")
    )
    payload = definition["fixture_payload"]
    definition_digest = definition["fixture_payload_digest"]
    evidence_file = _local_path(session_evidence_path, "viewer session evidence")
    evidence, evidence_digest = _load_session_evidence(evidence_file, definition_digest)
    capability_set, defined_forbidden = _validate_controlled_session(payload, evidence)
    fixture = definition.get("fixture", {})
    local_fixture = bool(isinstance(fixture, Mapping) and fixture.get("local_only"))
    chirality_binding: dict[str, str] | None = None
    chirality_inputs = (scene_registry_path, host_build_path, session_evidence_schema_path)
    if scene_registry_path is not None:
        if host_build_path is None or session_evidence_schema_path is None:
            raise ViewerProfileError("chirality host ViewerProfile requires host-build and session-evidence-schema locks")
        try:
            require_within(paths.private, definition_source_file, "chirality viewer definition")
            require_within(paths.private, definition_file, "chirality resolved viewer definition")
            require_within(paths.private, evidence_file, "chirality viewer evidence")
        except InputError as exc:
            raise ViewerProfileError("chirality ViewerProfile inputs must remain private") from exc
        _private_0600(definition_source_file, "chirality viewer definition")
        _private_0600(definition_file, "chirality resolved viewer definition")
        _private_0600(evidence_file, "chirality viewer evidence")
        chirality_binding = _chirality_scene_binding(
            paths,
            registry_path=scene_registry_path,
            host_build_path=host_build_path,
            session_evidence_schema_path=session_evidence_schema_path,
            payload=payload,
            evidence=evidence,
        )
    elif any(value is not None for value in chirality_inputs):
        raise ViewerProfileError("host-build and session-evidence-schema locks require a private scene registry")
    elif payload.get("adapter_id") == "molstar-chirality-host-v1" or evidence.get("attestation_mode") == "local_chirality_host_contract":
        raise ViewerProfileError("chirality host ViewerProfile requires a private scene registry")
    profile_body = {
        # definition_path and definition_source_digest always describe the
        # same canonical input document.  The resolved fixture is locked
        # separately because a source wrapper may point to it.
        "definition_path": str(definition_source_file),
        "definition_digest": definition_digest,
        "definition_source_digest": source_digest,
        "resolved_definition_path": str(definition_file),
        "resolved_definition_document_digest": definition_document_digest,
        "session_evidence_path": str(evidence_file),
        "session_evidence_digest": evidence_digest,
        "allowed_operations": sorted(set(evidence["allowed_operations"])),
        "capabilities": sorted(capability_set),
        "forbidden_capabilities": sorted(defined_forbidden | _FORBIDDEN_CAPABILITIES),
        "local_fixture": local_fixture,
        "production_eligible": bool(not local_fixture and fixture.get("production_eligible", False)),
        "attestation_mode": evidence.get("attestation_mode", "local_evidence_only"),
    }
    if chirality_binding is not None:
        profile_body.update(chirality_binding)
    profile = make_envelope(
        kind="viewer_profile",
        identifier=f"viewer-profile-{definition_digest.split(':', 1)[1][:12]}-{evidence_digest.split(':', 1)[1][:12]}",
        status="fixture_only" if local_fixture else "attested_local_only",
        body=profile_body,
        producer="qagen/0.1.0:viewer_profile_local",
    )
    profile_path = paths.viewer_profiles / f"{profile['id']}-{profile['content_digest'].split(':', 1)[1][:16]}.json"
    atomic_write_json(profile_path, profile, private=True)
    append_event(
        paths,
        "viewer_profile_created",
        {"viewer_profile_digest": profile["content_digest"], "local_fixture": local_fixture},
    )
    return {"viewer_profile_path": str(profile_path), "viewer_profile_digest": profile["content_digest"], "status": profile["status"]}


def load_viewer_profile(paths: WorkspacePaths, profile_path: str | Path) -> dict[str, Any]:
    path = require_within(paths.viewer_profiles, profile_path, "viewer profile")
    _private_0600(path, "viewer profile")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ViewerProfileError("viewer profile is not an object")
    envelope = verify_envelope(value, kind="viewer_profile")
    body = envelope.get("body")
    if not isinstance(body, Mapping) or not isinstance(body.get("local_fixture"), bool):
        raise ViewerProfileError("viewer profile fixture status is malformed")
    expected_status = "fixture_only" if body["local_fixture"] else "attested_local_only"
    if envelope.get("status") != expected_status:
        raise ViewerProfileError("viewer profile status does not match its body")
    return envelope


def revalidate_viewer_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read the local files and reject a profile whose locks drifted."""
    envelope = verify_envelope(profile, kind="viewer_profile")
    body = envelope["body"]
    if not isinstance(body.get("local_fixture"), bool):
        raise StaleInputError("viewer profile fixture status is malformed")
    expected_status = "fixture_only" if body["local_fixture"] else "attested_local_only"
    if envelope.get("status") != expected_status:
        raise StaleInputError("viewer profile status does not match its body")
    definition_source_file, definition_file, definition, source_digest, definition_document_digest = _resolve_definition(
        _local_path(body.get("definition_path"), "viewer definition")
    )
    definition_digest = definition["fixture_payload_digest"]
    if definition_digest != body.get("definition_digest") or source_digest != body.get("definition_source_digest"):
        raise StaleInputError("viewer profile definition lock changed")
    if str(definition_source_file) != body.get("definition_path"):
        raise StaleInputError("viewer profile definition source path changed")
    if str(definition_file) != body.get("resolved_definition_path"):
        raise StaleInputError("viewer profile resolved definition path changed")
    if definition_document_digest != body.get("resolved_definition_document_digest"):
        raise StaleInputError("viewer profile resolved definition document lock changed")
    evidence_file = _local_path(body.get("session_evidence_path"), "viewer session evidence")
    evidence, evidence_digest = _load_session_evidence(evidence_file, definition_digest)
    if evidence_digest != body.get("session_evidence_digest"):
        raise StaleInputError("viewer profile session evidence lock changed")
    capability_set, defined_forbidden = _validate_controlled_session(definition["fixture_payload"], evidence)
    if sorted(set(evidence["allowed_operations"])) != body.get("allowed_operations"):
        raise StaleInputError("viewer profile allowed operation lock changed")
    if sorted(capability_set) != body.get("capabilities") or sorted(defined_forbidden | _FORBIDDEN_CAPABILITIES) != body.get(
        "forbidden_capabilities"
    ):
        raise StaleInputError("viewer profile capability lock changed")
    registry_keys = {
        "scene_registry_path",
        "scene_registry_digest",
        "anonymous_scene_ref_set_digest",
        "host_build_path",
        "host_build_digest",
        "host_build_manifest_digest",
        "session_evidence_schema_path",
        "session_evidence_schema_digest",
    }
    present_registry_keys = registry_keys.intersection(body)
    is_chirality_host = (
        definition["fixture_payload"].get("adapter_id") == "molstar-chirality-host-v1"
        or evidence.get("attestation_mode") == "local_chirality_host_contract"
    )
    if is_chirality_host and present_registry_keys != registry_keys:
        raise StaleInputError("chirality ViewerProfile scene-registry lock is missing")
    if present_registry_keys:
        if present_registry_keys != registry_keys:
            raise StaleInputError("viewer profile scene-registry lock is incomplete")
        try:
            chirality_paths = _workspace_for_private_scene_registry(body["scene_registry_path"])
            require_within(chirality_paths.private, definition_source_file, "chirality viewer definition")
            require_within(chirality_paths.private, definition_file, "chirality resolved viewer definition")
            require_within(chirality_paths.private, evidence_file, "chirality viewer evidence")
            _private_0600(definition_source_file, "chirality viewer definition")
            _private_0600(definition_file, "chirality resolved viewer definition")
            _private_0600(evidence_file, "chirality viewer evidence")
            binding = _chirality_scene_binding(
                chirality_paths,
                registry_path=body["scene_registry_path"],
                host_build_path=body["host_build_path"],
                session_evidence_schema_path=body["session_evidence_schema_path"],
                payload=definition["fixture_payload"],
                evidence=evidence,
            )
        except (InputError, ViewerProfileError) as exc:
            raise StaleInputError(f"viewer profile scene-registry lock changed: {exc}") from exc
        if any(body.get(key) != value for key, value in binding.items()):
            raise StaleInputError("viewer profile scene-registry lock changed")
    del definition_source_file, definition_file, evidence_file
    return envelope
