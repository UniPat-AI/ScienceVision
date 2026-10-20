"""Private build and evidence-schema locks for the chirality viewer host.

The Mol* host serves a small, local browser application.  A capability
declaration alone is not enough to identify that application: the profile
must also commit to the exact host code and static files that are allowed to
render the private scene.  This module stores that commitment under the
private workspace and recomputes it during profile revalidation.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any, Mapping

from ..contracts import digest, digest_bytes, make_envelope, require_digest, verify_envelope
from ..errors import InputError
from ..paths import WorkspacePaths, require_within
from ..storage.atomic import atomic_write_json, read_json
from .adapter import ViewerAdapterError


CHIRALITY_HOST_ADAPTER_ID = "molstar-chirality-host-v1"
CHIRALITY_HOST_BACKEND_KIND = "molstar_browser_host_v1"
CHIRALITY_HOST_BUILD_KIND = "viewer_host_build"
CHIRALITY_SESSION_EVIDENCE_SCHEMA_KIND = "viewer_session_evidence_schema"

_MAX_HOST_BUILD_FILES = 2_048
_MAX_HOST_BUILD_BYTES = 64 * 1024 * 1024


# This is deliberately a data contract rather than a reference to Python
# implementation details.  The host writes these fields into its private
# session evidence and the verifier uses the same contract to reject a receipt
# from a different host build or profile.
CHIRALITY_SESSION_EVIDENCE_SCHEMA: dict[str, Any] = {
    "schema_version": 1,
    "adapter_id": CHIRALITY_HOST_ADAPTER_ID,
    "allowed_operations": ["render", "rotate"],
    "session_attestation": {
        "kind": "viewer_session_attestation",
        "required_body_fields": [
            "session_id",
            "request_id",
            "viewer_profile_path",
            "viewer_profile_digest",
            "anonymous_scene_ref",
            "registry_digest",
            "host_build_digest",
            "session_evidence_schema_digest",
            "allowed_operations",
            "adapter_id",
            "backend_kind",
            "opened_at",
            "release_eligible",
            "release_blockers",
            "source_version_resolution",
        ],
    },
    "action_receipt": {
        "kind": "viewer_action_receipt",
        "required_body_fields": [
            "session_id",
            "session_attestation_digest",
            "viewer_profile_digest",
            "registry_digest",
            "host_build_digest",
            "session_evidence_schema_digest",
            "sequence",
            "operation",
            "arguments_digest",
            "render_digest",
            "render_file",
            "camera",
            "previous_receipt_digest",
            "recorded_at",
        ],
    },
    "closure": {
        "required_fields": [
            "session_attestation_digest",
            "action_count",
            "final_receipt_digest",
            "closed_at",
        ]
    },
}


def _private_0600(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ViewerAdapterError(f"{label} must be a regular private file")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ViewerAdapterError(f"cannot inspect {label} permissions") from exc
    if mode != 0o600:
        raise ViewerAdapterError(f"{label} permissions must be 0600")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _regular_file_digest(path: Path, *, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ViewerAdapterError(f"{label} must be a regular file")
    try:
        return digest_bytes(path.read_bytes())
    except OSError as exc:
        raise ViewerAdapterError(f"cannot read {label}") from exc


def _host_code_manifest() -> list[dict[str, str]]:
    source_root = Path(__file__).resolve().parent
    files = ("adapter.py", "chirality_host.py", "host_build.py", "scene_registry.py")
    return [
        {"path": f"qagen/viewer/{name}", "digest": _regular_file_digest(source_root / name, label="viewer host code")}
        for name in files
    ]


def _viewer_file_manifest(package_root: Path) -> list[dict[str, Any]]:
    root = package_root.resolve()
    viewer_root = root / "viewer"
    if viewer_root.is_symlink() or not viewer_root.is_dir():
        raise ViewerAdapterError("private package has no regular viewer root")
    candidates = [viewer_root / "molstar.html"]
    shared_root = viewer_root / "shared"
    if shared_root.is_symlink() or not shared_root.is_dir():
        raise ViewerAdapterError("private package has no regular viewer shared root")
    candidates.extend(path for path in shared_root.rglob("*") if path.is_file() or path.is_symlink())
    manifest: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or not path.is_file():
            raise ViewerAdapterError("private viewer build contains a non-regular file")
        try:
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
        except OSError as exc:
            raise ViewerAdapterError("cannot inspect private viewer build") from exc
        if size < 0:
            raise ViewerAdapterError("private viewer build has an invalid file size")
        total_bytes += size
        if len(manifest) >= _MAX_HOST_BUILD_FILES or total_bytes > _MAX_HOST_BUILD_BYTES:
            raise ViewerAdapterError("private viewer build exceeds the host-build capture limit")
        manifest.append({"path": relative, "digest": _regular_file_digest(path, label="private viewer build"), "size": size})
    if not manifest or manifest[0]["path"] != "viewer/molstar.html":
        raise ViewerAdapterError("private viewer build is missing viewer/molstar.html")
    return manifest


def _host_build_manifest(registry: Any) -> dict[str, Any]:
    """Capture only code/static build identities, never scene selectors."""
    refs = getattr(registry, "anonymous_scene_refs", ())
    if not isinstance(refs, tuple) or len(refs) != 1:
        raise ViewerAdapterError("chirality host build requires exactly one private scene")
    binding = registry.resolve(refs[0])
    backend = binding.backend_arguments
    package_root = backend.get("package_root") if isinstance(backend, Mapping) else None
    if not isinstance(package_root, Path):
        raise ViewerAdapterError("chirality host build cannot identify the private viewer package")
    registry_digest = getattr(registry, "content_digest", None)
    require_digest(registry_digest, "chirality host build registry digest")
    return {
        "adapter_id": CHIRALITY_HOST_ADAPTER_ID,
        "backend_kind": CHIRALITY_HOST_BACKEND_KIND,
        "registry_digest": registry_digest,
        "host_code": _host_code_manifest(),
        "viewer_files": _viewer_file_manifest(package_root),
    }


def _build_path(paths: WorkspacePaths, build_digest: str) -> Path:
    return paths.private / "viewer" / "host-builds" / f"chirality-{build_digest.split(':', 1)[1][:16]}.json"


def _schema_path(paths: WorkspacePaths, schema_digest: str) -> Path:
    return paths.private / "viewer" / "session-evidence-schemas" / f"chirality-{schema_digest.split(':', 1)[1][:16]}.json"


def build_chirality_host_build(paths: WorkspacePaths, registry: Any) -> dict[str, Any]:
    """Persist the exact local host build bound to a resolved SceneRegistry."""
    paths.ensure()
    manifest = _host_build_manifest(registry)
    manifest_digest = digest(manifest)
    body = {"build_manifest": manifest, "build_manifest_digest": manifest_digest}
    envelope = make_envelope(
        kind=CHIRALITY_HOST_BUILD_KIND,
        identifier=f"chirality-host-build-{manifest_digest.split(':', 1)[1][:16]}",
        status="attested_local_only",
        body=body,
        producer="qagen/0.1.0:chirality_host_build",
    )
    output = _build_path(paths, envelope["content_digest"])
    _private_directory(output.parent)
    atomic_write_json(output, envelope, private=True)
    _private_0600(output, "chirality host build")
    return {"path": str(output), "envelope": envelope}


def build_chirality_session_evidence_schema(paths: WorkspacePaths) -> dict[str, Any]:
    """Persist the immutable schema expected from a controlled host session."""
    paths.ensure()
    schema_body = dict(CHIRALITY_SESSION_EVIDENCE_SCHEMA)
    envelope = make_envelope(
        kind=CHIRALITY_SESSION_EVIDENCE_SCHEMA_KIND,
        identifier=f"chirality-session-evidence-{digest(schema_body).split(':', 1)[1][:16]}",
        status="attested_local_only",
        body=schema_body,
        producer="qagen/0.1.0:chirality_session_evidence_schema",
    )
    output = _schema_path(paths, envelope["content_digest"])
    _private_directory(output.parent)
    atomic_write_json(output, envelope, private=True)
    _private_0600(output, "chirality session evidence schema")
    return {"path": str(output), "envelope": envelope}


def load_chirality_host_build(paths: WorkspacePaths, path: str | Path, registry: Any) -> dict[str, Any]:
    """Load and recompute a persisted host-build lock."""
    try:
        artifact = require_within(paths.private / "viewer" / "host-builds", path, "chirality host build")
    except InputError as exc:
        raise ViewerAdapterError(str(exc)) from exc
    _private_0600(artifact, "chirality host build")
    value = read_json(artifact)
    if not isinstance(value, Mapping):
        raise ViewerAdapterError("chirality host build is malformed")
    try:
        envelope = verify_envelope(value, kind=CHIRALITY_HOST_BUILD_KIND)
    except InputError as exc:
        raise ViewerAdapterError(f"chirality host build failed attestation: {exc}") from exc
    if envelope.get("status") != "attested_local_only" or envelope.get("producer") != "qagen/0.1.0:chirality_host_build":
        raise ViewerAdapterError("chirality host build identity changed")
    body = envelope.get("body")
    if not isinstance(body, Mapping) or set(body) != {"build_manifest", "build_manifest_digest"}:
        raise ViewerAdapterError("chirality host build has an invalid body")
    manifest = body.get("build_manifest")
    if not isinstance(manifest, Mapping):
        raise ViewerAdapterError("chirality host build manifest is malformed")
    expected = _host_build_manifest(registry)
    if manifest != expected or body.get("build_manifest_digest") != digest(expected):
        raise ViewerAdapterError("chirality host build lock changed")
    expected_id = f"chirality-host-build-{body['build_manifest_digest'].split(':', 1)[1][:16]}"
    if envelope.get("id") != expected_id:
        raise ViewerAdapterError("chirality host build identity changed")
    return dict(envelope)


def load_chirality_session_evidence_schema(paths: WorkspacePaths, path: str | Path) -> dict[str, Any]:
    """Load the exact schema used by a profile and reject schema drift."""
    try:
        artifact = require_within(
            paths.private / "viewer" / "session-evidence-schemas",
            path,
            "chirality session evidence schema",
        )
    except InputError as exc:
        raise ViewerAdapterError(str(exc)) from exc
    _private_0600(artifact, "chirality session evidence schema")
    value = read_json(artifact)
    if not isinstance(value, Mapping):
        raise ViewerAdapterError("chirality session evidence schema is malformed")
    try:
        envelope = verify_envelope(value, kind=CHIRALITY_SESSION_EVIDENCE_SCHEMA_KIND)
    except InputError as exc:
        raise ViewerAdapterError(f"chirality session evidence schema failed attestation: {exc}") from exc
    if envelope.get("body") != CHIRALITY_SESSION_EVIDENCE_SCHEMA:
        raise ViewerAdapterError("chirality session evidence schema changed")
    schema_digest = digest(CHIRALITY_SESSION_EVIDENCE_SCHEMA)
    expected_id = f"chirality-session-evidence-{schema_digest.split(':', 1)[1][:16]}"
    if (
        envelope.get("status") != "attested_local_only"
        or envelope.get("producer") != "qagen/0.1.0:chirality_session_evidence_schema"
        or envelope.get("id") != expected_id
    ):
        raise ViewerAdapterError("chirality session evidence schema identity changed")
    return dict(envelope)


__all__ = [
    "CHIRALITY_HOST_ADAPTER_ID",
    "CHIRALITY_HOST_BACKEND_KIND",
    "CHIRALITY_HOST_BUILD_KIND",
    "CHIRALITY_SESSION_EVIDENCE_SCHEMA_KIND",
    "CHIRALITY_SESSION_EVIDENCE_SCHEMA",
    "build_chirality_host_build",
    "build_chirality_session_evidence_schema",
    "load_chirality_host_build",
    "load_chirality_session_evidence_schema",
]
