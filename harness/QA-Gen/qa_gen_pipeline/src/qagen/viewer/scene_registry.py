"""Private provenance-bound scene registry for the chirality viewer pilot."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping

from ..contracts import digest, make_envelope, require_digest, verify_envelope
from ..errors import InputError, QuarantinedError
from ..paths import WorkspacePaths, require_within
from ..registry_contracts import validate_seed_index
from ..seeds.registry import (
    _asset_capture_paths,
    _authorized_package_root,
    _inventory,
    _load_role_inputs,
    _read_source_spec,
    _validate_source_constraints,
    _version_labels,
)
from ..sources import (
    SafeFilesystemError,
    lock_trusted_source_root,
    snapshot_trusted_tree,
    verify_trusted_tree_snapshot,
)
from ..storage.atomic import atomic_write_json, read_json
from .adapter import HostSceneBinding, ViewerAdapterError
from .contracts import validate_anonymous_scene_ref


_REGISTRY_KIND = "qagen_private_scene_registry"
_MAX_CAPTURE_BYTES = 64 * 1024 * 1024
_REQUIRED_RESOLVED_VERSION = "1.0.3"


def _field(value: Mapping[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _private_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ViewerAdapterError("private scene registry permissions are too broad")


def _registry_path(paths: WorkspacePaths, identifier: str) -> Path:
    return paths.private / "viewer" / "scene-registries" / f"{identifier}.json"


@dataclass(frozen=True)
class SourceVersionResolution:
    resolved: bool
    blockers: tuple[str, ...]
    seed_index_digest: str | None = None
    receipt_digest: str | None = None
    resolved_version: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "status": "resolved" if self.resolved else "blocked",
            "resolved_version": self.resolved_version,
            "seed_index_digest": self.seed_index_digest,
            "receipt_digest": self.receipt_digest,
            "blockers": list(self.blockers),
        }


def _blocked(reason: str, *, seed_index_digest: str | None = None) -> SourceVersionResolution:
    return SourceVersionResolution(False, (reason,), seed_index_digest=seed_index_digest)


def _load_private_envelope(path: Path, *, kind: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ViewerAdapterError(f"private {kind} artifact is unavailable")
    _private_mode(path)
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise ViewerAdapterError(f"private {kind} artifact is malformed")
    try:
        return verify_envelope(value, kind=kind)
    except InputError as exc:
        raise ViewerAdapterError(f"private {kind} artifact failed attestation") from exc


def validate_source_version_resolution(
    paths: WorkspacePaths,
    *,
    inventory_digest: str,
    source_spec_digest: str,
    source_spec_path: Path,
    observed_versions: Mapping[str, str],
    package_name: str,
) -> SourceVersionResolution:
    """Close the current SeedIndex through its immutable request and receipt."""
    require_digest(inventory_digest, "viewer source inventory digest")
    require_digest(source_spec_digest, "viewer source spec digest")
    current_path = paths.catalog / "current.json"
    if not current_path.is_file() or current_path.is_symlink():
        return _blocked("source_version_seed_index_missing")
    try:
        _private_mode(current_path)
        current_value = read_json(current_path)
        if not isinstance(current_value, Mapping):
            return _blocked("source_version_seed_index_invalid")
        seed_index = validate_seed_index(current_value)
    except (InputError, OSError, ViewerAdapterError):
        return _blocked("source_version_seed_index_invalid")
    index_digest = str(seed_index["content_digest"])
    body = seed_index["body"]
    frozen_path = paths.catalog / f"{index_digest.split(':', 1)[1][:16]}.seed-index.json"
    try:
        frozen = _load_private_envelope(frozen_path, kind="seed_index")
        validate_seed_index(frozen)
    except (InputError, OSError, ViewerAdapterError):
        return _blocked("source_version_seed_index_not_canonical", seed_index_digest=index_digest)
    if frozen != seed_index:
        return _blocked("source_version_seed_index_not_canonical", seed_index_digest=index_digest)
    expected_spec_path = source_spec_path.expanduser().resolve()
    try:
        indexed_spec_path = Path(str(body.get("source_spec_path"))).expanduser().resolve()
    except (OSError, RuntimeError):
        return _blocked("source_version_seed_index_source_spec_mismatch", seed_index_digest=index_digest)
    expected_locks = (
        body.get("eligible") is True,
        seed_index.get("status") == "eligible",
        body.get("inventory_digest") == inventory_digest,
        body.get("source_spec_digest") == source_spec_digest,
        indexed_spec_path == expected_spec_path,
        body.get("observed_versions") == dict(observed_versions),
        body.get("package_name") == package_name,
    )
    if not all(expected_locks):
        return _blocked("source_version_seed_index_lock_mismatch", seed_index_digest=index_digest)
    resolution_ref = body.get("resolution_ref")
    try:
        require_digest(resolution_ref, "seed index resolution_ref")
    except InputError:
        return _blocked("source_version_resolution_missing", seed_index_digest=index_digest)

    candidates: list[Path] = []
    for path in (paths.receipts / "source").glob("*.resolution.json"):
        try:
            value = read_json(path)
        except (InputError, OSError):
            continue
        if isinstance(value, Mapping) and value.get("content_digest") == resolution_ref:
            candidates.append(path)
    if len(candidates) != 1:
        reason = "source_version_resolution_ambiguous" if len(candidates) > 1 else "source_version_resolution_missing"
        return _blocked(reason, seed_index_digest=index_digest)
    try:
        receipt = _load_private_envelope(candidates[0], kind="source_version_resolution_receipt")
    except (OSError, ViewerAdapterError):
        return _blocked("source_version_resolution_invalid", seed_index_digest=index_digest)
    if receipt["content_digest"] != resolution_ref:
        return _blocked("source_version_resolution_ref_mismatch", seed_index_digest=index_digest)
    receipt_body = receipt["body"]
    request_digest = receipt_body.get("request_digest")
    bound_digests = receipt_body.get("bound_digests")
    receipt_locks = (
        receipt.get("status") == "accept",
        receipt_body.get("decision") == "accept",
        receipt_body.get("actor_role") == "source_version_reviewer",
        isinstance(receipt_body.get("rationale"), str) and bool(receipt_body["rationale"].strip()),
        receipt_body.get("inventory_digest") == inventory_digest,
        receipt_body.get("source_spec_digest") == source_spec_digest,
        receipt_body.get("observed_versions") == dict(observed_versions),
        receipt_body.get("resolved_version") == _REQUIRED_RESOLVED_VERSION,
        isinstance(bound_digests, Mapping),
        isinstance(request_digest, str),
    )
    if not all(receipt_locks):
        return _blocked("source_version_resolution_lock_mismatch", seed_index_digest=index_digest)
    try:
        require_digest(request_digest, "source version request digest")
    except InputError:
        return _blocked("source_version_resolution_lock_mismatch", seed_index_digest=index_digest)
    if dict(bound_digests) != {
        "request_digest": request_digest,
        "inventory_digest": inventory_digest,
        "source_spec_digest": source_spec_digest,
    }:
        return _blocked("source_version_resolution_lock_mismatch", seed_index_digest=index_digest)

    request_candidates: list[Path] = []
    for path in (paths.receipts / "source").glob("*.request.json"):
        try:
            value = read_json(path)
        except (InputError, OSError):
            continue
        if isinstance(value, Mapping) and value.get("content_digest") == request_digest:
            request_candidates.append(path)
    if len(request_candidates) != 1:
        reason = "source_version_request_ambiguous" if len(request_candidates) > 1 else "source_version_request_missing"
        return _blocked(reason, seed_index_digest=index_digest)
    try:
        request = _load_private_envelope(request_candidates[0], kind="source_version_resolution_request")
    except (OSError, ViewerAdapterError):
        return _blocked("source_version_request_invalid", seed_index_digest=index_digest)
    request_body = request["body"]
    acceptable = request_body.get("acceptable_versions")
    request_locks = (
        request.get("status") == "pending",
        request.get("content_digest") == request_digest,
        request_body.get("inventory_digest") == inventory_digest,
        request_body.get("expected_inventory_digest") == inventory_digest,
        request_body.get("source_spec_digest") == source_spec_digest,
        request_body.get("observed_versions") == dict(observed_versions),
        isinstance(acceptable, list) and _REQUIRED_RESOLVED_VERSION in acceptable,
    )
    if not all(request_locks):
        return _blocked("source_version_request_lock_mismatch", seed_index_digest=index_digest)
    return SourceVersionResolution(
        True,
        (),
        seed_index_digest=index_digest,
        receipt_digest=str(receipt["content_digest"]),
        resolved_version=_REQUIRED_RESOLVED_VERSION,
    )


@dataclass(frozen=True)
class SceneRegistry:
    """Host-only mapping whose backend arguments never cross the adapter API."""

    path: Path
    envelope: Mapping[str, Any]
    resolution: SourceVersionResolution

    @classmethod
    def load(cls, paths: WorkspacePaths, registry_path: str | Path) -> "SceneRegistry":
        paths.ensure()
        path = require_within(paths.private, registry_path, "scene registry")
        if not path.is_file() or path.is_symlink():
            raise ViewerAdapterError("scene registry must be a regular private file")
        _private_mode(path)
        value = read_json(path)
        if not isinstance(value, Mapping):
            raise ViewerAdapterError("private scene registry must be an object")
        try:
            envelope = verify_envelope(value, kind=_REGISTRY_KIND)
        except InputError as exc:
            raise ViewerAdapterError(f"private scene registry failed attestation: {exc}") from exc
        body = envelope["body"]
        if body.get("release_eligible") is not False or body.get("local_only") is not True:
            raise ViewerAdapterError("chirality pilot registry must remain local-only and release-ineligible")
        scenes = body.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != 1 or not isinstance(scenes[0], Mapping):
            raise ViewerAdapterError("chirality pilot registry must contain exactly one private scene")
        source_versions = body.get("source_versions")
        if not isinstance(source_versions, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in source_versions.items()
        ):
            raise ViewerAdapterError("chirality pilot registry source version locks are malformed")
        try:
            source_spec_digest = require_digest(body.get("source_spec_digest"), "scene registry source spec digest")
            inventory_digest = require_digest(
                body.get("source_inventory_digest"), "scene registry source inventory digest"
            )
        except InputError as exc:
            raise ViewerAdapterError(str(exc)) from exc
        source_spec_path = body.get("source_spec_path")
        package_name = body.get("package_name")
        if not isinstance(source_spec_path, str) or not source_spec_path or not isinstance(package_name, str):
            raise ViewerAdapterError("chirality pilot registry source identity is malformed")
        resolution = validate_source_version_resolution(
            paths,
            inventory_digest=inventory_digest,
            source_spec_digest=source_spec_digest,
            source_spec_path=Path(source_spec_path),
            observed_versions={str(key): str(value) for key, value in source_versions.items()},
            package_name=package_name,
        )
        return cls(path=path, envelope=envelope, resolution=resolution)

    @property
    def content_digest(self) -> str:
        return str(self.envelope["content_digest"])

    @property
    def anonymous_scene_refs(self) -> tuple[str, ...]:
        scenes = self.envelope["body"]["scenes"]
        return tuple(validate_anonymous_scene_ref(scene["anonymous_scene_ref"]) for scene in scenes)

    def resolve(self, anonymous_scene_ref: str) -> HostSceneBinding:
        if not self.resolution.resolved:
            raise ViewerAdapterError("scene registry source version resolution is blocked")
        scene_ref = validate_anonymous_scene_ref(anonymous_scene_ref)
        scenes = self.envelope["body"]["scenes"]
        selected = next((scene for scene in scenes if scene.get("anonymous_scene_ref") == scene_ref), None)
        if not isinstance(selected, Mapping):
            raise ViewerAdapterError("anonymous scene reference is not registered")
        backend = selected.get("backend")
        if not isinstance(backend, Mapping):
            raise ViewerAdapterError("private scene binding is malformed")
        required = {"package_root", "package_path", "format", "asset_sha256", "asset_size"}
        if set(backend) != required:
            raise ViewerAdapterError("private scene binding has an invalid backend contract")
        package_root = Path(str(backend["package_root"]))
        package_path = str(backend["package_path"])
        if backend["format"] != "mol":
            raise ViewerAdapterError("chirality pilot backend asset must use Mol format")
        try:
            with lock_trusted_source_root(package_root) as locked:
                snapshot = snapshot_trusted_tree(
                    locked,
                    capture_paths=(package_path,),
                    max_capture_bytes=_MAX_CAPTURE_BYTES,
                )
                selected_file = snapshot.file(package_path)
                asset_bytes = snapshot.captured(package_path)
                verify_trusted_tree_snapshot(locked, snapshot)
        except SafeFilesystemError as exc:
            raise ViewerAdapterError(f"private scene asset is unavailable or changed: {exc}") from exc
        if f"sha256:{selected_file.sha256}" != backend["asset_sha256"] or selected_file.size != backend["asset_size"]:
            raise ViewerAdapterError("private scene asset no longer matches its provenance binding")
        return HostSceneBinding(
            anonymous_scene_ref=scene_ref,
            backend_arguments={
                "package_root": package_root,
                "package_path": package_path,
                "format": "mol",
                "asset_sha256": backend["asset_sha256"],
                "asset_size": backend["asset_size"],
                "asset_bytes": asset_bytes,
                "registry_digest": self.content_digest,
            },
        )


def build_chirality_scene_registry(
    paths: WorkspacePaths,
    *,
    package_root: str | Path,
    source_spec_path: str | Path,
    seed_task_id: str,
) -> dict[str, Any]:
    """Build one local-only mapping from an immutable verified EV2 seed closure."""
    if not isinstance(seed_task_id, str) or not seed_task_id.strip():
        raise ViewerAdapterError("seed task id must be a non-empty host-only value")
    paths.ensure()
    spec_path, spec, spec_digest, _ = _read_source_spec(paths, source_spec_path)
    authorized_root = _authorized_package_root(spec, package_root)
    role_specs = spec["file_roles"]
    role_paths = [str(role_specs[role]["path"]) for role in role_specs]

    try:
        with lock_trusted_source_root(authorized_root) as locked:
            bootstrap = snapshot_trusted_tree(
                locked,
                capture_paths=role_paths,
                max_capture_bytes=_MAX_CAPTURE_BYTES,
            )
            if _inventory(bootstrap)["inventory_digest"] != spec["expected_inventory_digest"]:
                raise QuarantinedError("seed package inventory does not match the authorized SourceSpec")
            _, manifest, version_note, tasks, expert_review, asset_manifest = _load_role_inputs(bootstrap, spec)
            asset_paths = _asset_capture_paths(bootstrap, asset_manifest)
            snapshot = snapshot_trusted_tree(
                locked,
                capture_paths=[*role_paths, *asset_paths],
                max_capture_bytes=_MAX_CAPTURE_BYTES,
            )
            if snapshot.stability_signature() != bootstrap.stability_signature():
                raise QuarantinedError("seed package changed while resolving the scene closure")
            _, manifest, version_note, tasks, expert_review, asset_manifest = _load_role_inputs(snapshot, spec)
            _validate_source_constraints(
                package_root=locked.path,
                manifest=manifest,
                version_note=version_note,
                tasks=tasks,
                expert_review=expert_review,
                asset_manifest=asset_manifest,
                snapshot=snapshot,
                spec=spec,
            )
            matches = [task for task in tasks if task.get("task_id") == seed_task_id]
            if len(matches) != 1:
                raise QuarantinedError("requested pilot seed is absent or duplicated")
            task = matches[0]
            if _field(task, "expert_review.verdict_class") != "verified":
                raise QuarantinedError("chirality pilot seed must have a verified expert verdict")
            if task.get("domain") != "chemistry" or _field(task, "sub_domain.slug") != "chirality":
                raise QuarantinedError("pilot seed is not an authorized chemistry chirality case")
            if _field(task, "asset.viewer") != "molstar" or _field(task, "asset.format") != "mol":
                raise QuarantinedError("chirality pilot seed does not declare a Mol* Mol asset")
            repo_path = _field(task, "asset.repo_path")
            raw_sha256 = _field(task, "asset.sha256")
            task_size = _field(task, "asset.byte_size")
            asset_matches = [
                asset
                for asset in asset_manifest
                if asset.get("repo_path") == repo_path and asset.get("sha256") == raw_sha256
            ]
            if len(asset_matches) != 1:
                raise QuarantinedError("pilot seed asset does not close uniquely against asset provenance")
            asset = asset_matches[0]
            package_path = str(asset["package_path"])
            captured = snapshot.file(package_path)
            if captured.sha256 != raw_sha256 or captured.size != asset.get("byte_size") or captured.size != task_size:
                raise QuarantinedError("pilot seed asset bytes do not match task and manifest provenance")
            versions = _version_labels(locked.path, manifest, version_note)
            if len(set(versions.values())) == 1:
                raise QuarantinedError("pilot registry expected an explicitly unresolved source version conflict")
            verify_trusted_tree_snapshot(locked, snapshot)
    except SafeFilesystemError as exc:
        raise QuarantinedError(f"pilot scene provenance could not be snapshotted safely: {exc}") from exc

    anonymous_scene_ref = f"scene_{secrets.token_hex(16)}"
    identifier = f"registry_{secrets.token_hex(12)}"
    resolution = validate_source_version_resolution(
        paths,
        inventory_digest=spec["expected_inventory_digest"],
        source_spec_digest=spec_digest,
        source_spec_path=spec_path,
        observed_versions=versions,
        package_name=authorized_root.name,
    )
    body = {
        "adapter_kind": "molstar_chirality_host_v1",
        "source_spec_path": str(spec_path),
        "source_spec_digest": spec_digest,
        "source_inventory_digest": spec["expected_inventory_digest"],
        "source_versions": versions,
        "package_name": authorized_root.name,
        "local_only": True,
        "release_eligible": False,
        "scenes": [
            {
                "anonymous_scene_ref": anonymous_scene_ref,
                "seed_attestation_digest": digest(task),
                "backend": {
                    "package_root": str(authorized_root),
                    "package_path": package_path,
                    "format": "mol",
                    "asset_sha256": f"sha256:{captured.sha256}",
                    "asset_size": captured.size,
                },
            }
        ],
    }
    envelope = make_envelope(
        kind=_REGISTRY_KIND,
        identifier=identifier,
        status="pilot_local",
        body=body,
    )
    output = _registry_path(paths, identifier)
    atomic_write_json(output, envelope, private=True)
    try:
        os.chmod(output.parent, 0o700)
    except OSError:
        pass
    _private_mode(output)
    return {
        "artifact_kind": "chirality_scene_registry_preparation",
        "status": "pilot_ready_local_only" if resolution.resolved else "blocked",
        "anonymous_scene_ref": anonymous_scene_ref,
        "registry_digest": envelope["content_digest"],
        "registry_path": str(output),
        "release_eligible": False,
        "runtime_eligible": resolution.resolved,
        "version_resolution": resolution.public(),
        "blockers": list(resolution.blockers) if not resolution.resolved else ["source_spec_local_only"],
    }
