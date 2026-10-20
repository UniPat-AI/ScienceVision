"""Fail-closed protocol for a future solver-facing controlled viewer.

This module intentionally contains no backend implementation.  The current
ScienceVision MCP session API is a host-side rendering service: it accepts
``qid``/``asset_path`` and exposes scene metadata.  A solver adapter must put
an anonymous scene registry and an evidence boundary in front of that API
before it can implement this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
import re
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from ..contracts import require_digest
from ..errors import InputError


class ViewerAdapterError(InputError):
    """A viewer adapter declaration or solver-visible result is unsafe."""


NAVIGATION_OPERATIONS = frozenset(
    {
        "inspect",
        "isolate",
        "rotate",
        "orbit",
        "select",
        "zoom",
        "pan",
        "slice",
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
RENDER_OPERATIONS = frozenset({"render"})
SOLVER_SAFE_OPERATIONS = NAVIGATION_OPERATIONS | MEASUREMENT_OPERATIONS | RENDER_OPERATIONS
REQUIRED_FORBIDDEN_CAPABILITIES = frozenset(
    {
        "network",
        "asset_file_read",
        "raw_asset_metadata",
        "raw_asset_read",
        "arbitrary_compute",
        "unrestricted_compute",
    }
)

_FORBIDDEN_RESULT_KEYS = {
    "asset",
    "asset_id",
    "asset_path",
    "backend_scene_id",
    "file",
    "file_path",
    "mask_path",
    "metadata",
    "path",
    "qid",
    "raw_asset",
    "raw_metadata",
    "source_path",
    "url",
}
_REAL_ASSET_SUFFIX = re.compile(
    r"\.(?:pdb|cif|mmcif|sdf|mol2|mol|xyz|glb|gltf|obj|stl|fbx|nii(?:\.gz)?|mrc|dcm|dicom|vtk|ply)(?:$|[?#\s])",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _non_empty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ViewerAdapterError(f"{label} must be a non-empty string")
    return value.strip()


def _anonymous_scene_ref(value: Any) -> str:
    scene_ref = _non_empty_text(value, "anonymous scene reference")
    lower = scene_ref.lower()
    if (
        scene_ref.startswith(("/", "\\"))
        or _WINDOWS_ABSOLUTE.match(scene_ref)
        or lower.startswith(("file://", "http://", "https://"))
        or ".." in PurePath(scene_ref).parts
        or _REAL_ASSET_SUFFIX.search(scene_ref)
    ):
        raise ViewerAdapterError("anonymous scene reference exposes a path, URL, or raw asset")
    return scene_ref


def _operation_set(value: Any, label: str, *, allow_empty: bool = False) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ViewerAdapterError(f"{label} must be a string collection")
    operations = list(value)
    if not allow_empty and not operations:
        raise ViewerAdapterError(f"{label} must not be empty")
    if not all(isinstance(item, str) and item for item in operations):
        raise ViewerAdapterError(f"{label} must contain only non-empty strings")
    if len(set(operations)) != len(operations):
        raise ViewerAdapterError(f"{label} cannot contain duplicate operations")
    unsupported = sorted(set(operations) - SOLVER_SAFE_OPERATIONS)
    if unsupported:
        raise ViewerAdapterError(f"{label} contains unsupported operation: {unsupported[0]}")
    return frozenset(operations)


@dataclass(frozen=True)
class AdapterHealth:
    """Live adapter health without scene, asset, or backend identifiers."""

    status: Literal["ready", "degraded", "unavailable"]
    adapter_id: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in {"ready", "degraded", "unavailable"}:
            raise ViewerAdapterError("adapter health status is invalid")
        _non_empty_text(self.adapter_id, "adapter health adapter_id")
        _non_empty_text(self.detail, "adapter health detail")


@dataclass(frozen=True)
class AdapterProfile:
    """Security and capability facts attested by one adapter instance."""

    adapter_id: str
    available_operations: frozenset[str]
    native_measurement_operations: frozenset[str]
    forbidden_capabilities: frozenset[str]
    accepts_anonymous_scene_ref: bool
    accepts_solver_asset_identifiers: bool
    exposes_raw_asset_metadata: bool
    exposes_raw_asset_bytes: bool
    network_access: bool
    arbitrary_compute: bool
    attested_evidence: bool

    def __post_init__(self) -> None:
        _non_empty_text(self.adapter_id, "adapter profile adapter_id")
        available = _operation_set(self.available_operations, "adapter available_operations", allow_empty=True)
        native = _operation_set(
            self.native_measurement_operations,
            "adapter native_measurement_operations",
            allow_empty=True,
        )
        if not native.issubset(MEASUREMENT_OPERATIONS):
            raise ViewerAdapterError("native measurement operations must be measurement operations")
        if not (available & MEASUREMENT_OPERATIONS).issubset(native):
            raise ViewerAdapterError("every advertised measurement must be viewer-native")
        if not native.issubset(available):
            raise ViewerAdapterError("native measurements must be advertised operations")
        if not REQUIRED_FORBIDDEN_CAPABILITIES.issubset(self.forbidden_capabilities):
            raise ViewerAdapterError("adapter profile must forbid network/raw asset/arbitrary compute")


@dataclass(frozen=True)
class AdapterSession:
    """Opaque solver session handle bound to one approved request."""

    session_id: str
    request_id: str
    viewer_profile_digest: str
    allowed_operations: frozenset[str]

    def __post_init__(self) -> None:
        _non_empty_text(self.session_id, "viewer session_id")
        _non_empty_text(self.request_id, "viewer request_id")
        require_digest(self.viewer_profile_digest, "viewer session profile digest")
        _operation_set(self.allowed_operations, "viewer session allowed_operations")


@dataclass(frozen=True)
class HostSceneBinding:
    """Trusted-host result of resolving one anonymous scene reference.

    ``backend_arguments`` may contain private backend selectors.  It must stay
    behind the adapter boundary and must never be embedded in a session or an
    observation returned to a solver.
    """

    anonymous_scene_ref: str
    backend_arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        _anonymous_scene_ref(self.anonymous_scene_ref)
        if not isinstance(self.backend_arguments, Mapping) or not self.backend_arguments:
            raise ViewerAdapterError("host scene binding requires private backend arguments")


@runtime_checkable
class PrivateSceneRegistry(Protocol):
    """Host-only anonymous scene lookup used by a controlled adapter."""

    def resolve(self, anonymous_scene_ref: str) -> HostSceneBinding: ...


@dataclass(frozen=True)
class AdapterObservation:
    """One solver-visible result plus a private attestation digest."""

    operation: str
    sequence: int
    payload: Mapping[str, Any]
    trace_digest: str

    def __post_init__(self) -> None:
        if self.operation not in SOLVER_SAFE_OPERATIONS:
            raise ViewerAdapterError(f"unsupported viewer operation: {self.operation}")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ViewerAdapterError("viewer observation sequence must be a non-negative integer")
        if not isinstance(self.payload, Mapping) or not self.payload:
            raise ViewerAdapterError("viewer observation payload must be a non-empty object")
        require_digest(self.trace_digest, "viewer observation trace digest")
        validate_solver_observation_payload(self.payload)


@runtime_checkable
class ControlledViewerAdapter(Protocol):
    """The only interface a production evidence worker may receive.

    Implementations resolve ``anonymous_scene_ref`` inside a trusted host
    boundary.  They must never accept a qid, asset id, path, URL, bytes, raw
    metadata, network handle, or arbitrary code from the solver.
    """

    def health(self) -> AdapterHealth: ...

    def profile(self) -> AdapterProfile: ...

    def open_session(
        self,
        *,
        anonymous_scene_ref: str,
        request: Mapping[str, Any],
    ) -> AdapterSession: ...

    def perform(
        self,
        *,
        session: AdapterSession,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AdapterObservation: ...

    def close_session(self, *, session: AdapterSession) -> None: ...


def validate_adapter_profile(profile: AdapterProfile, health: AdapterHealth) -> AdapterProfile:
    """Require the full production boundary; there is no degraded mode."""
    if profile.adapter_id != health.adapter_id:
        raise ViewerAdapterError("adapter health and profile identify different adapters")
    if health.status != "ready":
        raise ViewerAdapterError("viewer adapter health is not ready")
    unsafe_flags = {
        "accepts_anonymous_scene_ref": not profile.accepts_anonymous_scene_ref,
        "accepts_solver_asset_identifiers": profile.accepts_solver_asset_identifiers,
        "exposes_raw_asset_metadata": profile.exposes_raw_asset_metadata,
        "exposes_raw_asset_bytes": profile.exposes_raw_asset_bytes,
        "network_access": profile.network_access,
        "arbitrary_compute": profile.arbitrary_compute,
        "attested_evidence": not profile.attested_evidence,
    }
    failed = sorted(name for name, value in unsafe_flags.items() if value)
    if failed:
        raise ViewerAdapterError(f"viewer adapter is not production controlled: {failed[0]}")
    return profile


def validate_measurement_operation(operation: str, profile: AdapterProfile) -> None:
    """Reject any measurement not implemented as a viewer-native operation."""
    if operation not in MEASUREMENT_OPERATIONS:
        return
    if operation not in profile.native_measurement_operations:
        raise ViewerAdapterError(f"measurement operation is not viewer-native: {operation}")


def validate_session_operation(
    session: AdapterSession,
    operation: str,
    profile: AdapterProfile,
) -> str:
    """Enforce the approved session scope before any backend invocation."""
    if operation not in SOLVER_SAFE_OPERATIONS:
        raise ViewerAdapterError(f"unsupported viewer operation: {operation}")
    if operation not in session.allowed_operations:
        raise ViewerAdapterError(f"operation is outside the approved viewer session: {operation}")
    if operation not in profile.available_operations:
        raise ViewerAdapterError(f"operation is unavailable from the viewer adapter: {operation}")
    validate_measurement_operation(operation, profile)
    return operation


def _validate_solver_scalar(value: str, label: str) -> None:
    lower = value.lower()
    if lower.startswith(("file://", "http://", "https://")):
        raise ViewerAdapterError(f"{label} exposes a file or network URL")
    if value.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE.match(value) or PurePath(value).is_absolute():
        raise ViewerAdapterError(f"{label} exposes an absolute path")
    if ".." in PurePath(value).parts or _REAL_ASSET_SUFFIX.search(value):
        raise ViewerAdapterError(f"{label} exposes a raw asset reference")


def validate_solver_observation_payload(value: Any, label: str = "viewer observation") -> None:
    """Recursively reject raw identifiers, paths, URLs, and metadata."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ViewerAdapterError(f"{label} has a non-string field")
            if key.lower() in _FORBIDDEN_RESULT_KEYS:
                raise ViewerAdapterError(f"{label}.{key} is forbidden solver-visible data")
            validate_solver_observation_payload(nested, f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            validate_solver_observation_payload(nested, f"{label}[{index}]")
    elif isinstance(value, str):
        _validate_solver_scalar(value, label)
