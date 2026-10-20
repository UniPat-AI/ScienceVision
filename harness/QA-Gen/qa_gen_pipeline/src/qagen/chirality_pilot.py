"""Local control-plane bindings for the concrete Mol* chirality pilot.

This module deliberately does not start a browser, resolve a scene, or contact
a provider.  It persists the exact capability declaration consumed by the
already-implemented host adapter so a plan can reject the generic fixture
viewer before any pilot work is approved.
"""

from __future__ import annotations

from pathlib import Path

from .contracts import digest
from .paths import WorkspacePaths
from .storage.atomic import atomic_write_json
from .viewer import SceneRegistry, ViewerAdapterError, build_viewer_profile
from .viewer.host_build import (
    build_chirality_host_build,
    build_chirality_session_evidence_schema,
)


CHIRALITY_PILOT_ADAPTER_ID = "molstar-chirality-host-v1"
CHIRALITY_PILOT_OPERATIONS = ("rotate", "render")
_FORBIDDEN_CAPABILITIES = (
    "network",
    "asset_file_read",
    "raw_asset_metadata",
    "raw_asset_read",
    "arbitrary_compute",
    "unrestricted_compute",
)


def chirality_pilot_viewer_payload() -> dict[str, object]:
    """Return the static, solver-safe capability contract of the real host."""
    return {
        "viewer_id": CHIRALITY_PILOT_ADAPTER_ID,
        "adapter_id": CHIRALITY_PILOT_ADAPTER_ID,
        "capabilities": [
            "anonymous_scene",
            "controlled_session_request",
            *CHIRALITY_PILOT_OPERATIONS,
        ],
        "forbidden_capabilities": list(_FORBIDDEN_CAPABILITIES),
        "session_scope_mode": "attested_local_chirality_host_only",
    }


def build_chirality_pilot_viewer_profile(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
) -> dict[str, object]:
    """Persist the non-fixture ViewerProfile required by the chirality pilot.

    The declaration is intentionally local-only and has no production claim.
    Unlike the generic fixture profile, it binds one private SceneRegistry and
    a host-build receipt and session-evidence schema that name that exact
    registry.  The subsequent smoke must load this persisted profile before it
    opens a browser session, so no caller can substitute an arbitrary digest.
    """
    paths.ensure()
    try:
        registry = SceneRegistry.load(paths, registry_path)
    except ViewerAdapterError as exc:
        raise ViewerAdapterError(f"chirality ViewerProfile cannot load SceneRegistry: {exc}") from exc
    if not registry.resolution.resolved:
        raise ViewerAdapterError("chirality ViewerProfile requires a resolved SceneRegistry")
    host_build = build_chirality_host_build(paths, registry)
    session_evidence_schema = build_chirality_session_evidence_schema(paths)
    host_build_envelope = host_build["envelope"]
    schema_envelope = session_evidence_schema["envelope"]
    if not isinstance(host_build_envelope, dict) or not isinstance(schema_envelope, dict):
        raise ViewerAdapterError("chirality ViewerProfile host evidence is malformed")
    payload = chirality_pilot_viewer_payload()
    definition_digest = digest(payload)
    suffix = definition_digest.split(":", 1)[1][:16]
    definition_path = paths.private / "viewer-definitions" / f"{CHIRALITY_PILOT_ADAPTER_ID}-{suffix}.json"
    registry_suffix = registry.content_digest.split(":", 1)[1][:16]
    evidence_path = paths.private / "viewer-evidence" / f"{CHIRALITY_PILOT_ADAPTER_ID}-{suffix}-{registry_suffix}.json"
    definition = {
        "schema_version": 1,
        "kind": "qagen_viewer_definition_fixture",
        "fixture": {"local_only": False, "production_eligible": False},
        "fixture_payload": payload,
        "fixture_payload_digest": definition_digest,
    }
    evidence = {
        "kind": "qagen_chirality_host_profile_evidence",
        "adapter_id": CHIRALITY_PILOT_ADAPTER_ID,
        "viewer_definition_digest": definition_digest,
        "scene_registry_digest": registry.content_digest,
        "anonymous_scene_ref_set_digest": digest(sorted(registry.anonymous_scene_refs)),
        "host_build_digest": host_build_envelope["content_digest"],
        "session_evidence_schema_digest": schema_envelope["content_digest"],
        "allowed_operations": list(CHIRALITY_PILOT_OPERATIONS),
        "forbidden_capabilities": list(_FORBIDDEN_CAPABILITIES),
        "network_access": False,
        "asset_file_read": False,
        "attestation_mode": "local_chirality_host_contract",
    }
    atomic_write_json(definition_path, definition, private=True)
    atomic_write_json(evidence_path, evidence, private=True)
    result = build_viewer_profile(
        paths,
        definition_path=definition_path,
        session_evidence_path=evidence_path,
        scene_registry_path=registry.path,
        host_build_path=str(host_build["path"]),
        session_evidence_schema_path=str(session_evidence_schema["path"]),
    )
    return {
        **result,
        "adapter_id": CHIRALITY_PILOT_ADAPTER_ID,
        "definition_path": str(definition_path),
        "definition_digest": definition_digest,
        "session_evidence_path": str(evidence_path),
        "scene_registry_path": str(registry.path),
        "scene_registry_digest": registry.content_digest,
        "host_build_path": str(host_build["path"]),
        "host_build_digest": host_build_envelope["content_digest"],
        "session_evidence_schema_path": str(session_evidence_schema["path"]),
        "session_evidence_schema_digest": schema_envelope["content_digest"],
    }


def chirality_pilot_profile_path(paths: WorkspacePaths, *, registry_path: str | Path) -> Path:
    """Return the deterministic persisted profile path after creating it."""
    result = build_chirality_pilot_viewer_profile(paths, registry_path=registry_path)
    return Path(str(result["viewer_profile_path"]))
