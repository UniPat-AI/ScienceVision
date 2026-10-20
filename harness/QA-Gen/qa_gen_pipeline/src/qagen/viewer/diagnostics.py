"""Static health/profile diagnostics for a controlled viewer adapter config."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest
from ..storage.atomic import read_json
from .adapter import MEASUREMENT_OPERATIONS, SOLVER_SAFE_OPERATIONS, ViewerAdapterError


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ViewerAdapterError(f"cannot read viewer adapter config {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ViewerAdapterError("viewer adapter config must be an object")
    return dict(value)


def _find_schema(config_path: Path) -> Path:
    for parent in (config_path.parent, *config_path.parents):
        candidate = parent / "schemas" / "viewer_adapter.schema.json"
        if candidate.is_file():
            return candidate
    raise ViewerAdapterError("viewer adapter schema was not found beside the config tree")


def load_adapter_config(config_path: str | Path) -> tuple[Path, dict[str, Any]]:
    raw = str(config_path)
    if raw.startswith(("http://", "https://", "file://")):
        raise ViewerAdapterError("viewer adapter config must be a local file")
    path = Path(config_path).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise ViewerAdapterError(f"viewer adapter config is not a regular local file: {path}")
    config = _read_mapping(path)
    schema_path = _find_schema(path)
    schema = read_json(schema_path)
    errors = sorted(Draft7Validator(schema).iter_errors(config), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ViewerAdapterError(f"viewer adapter config failed schema validation at {location}: {error.message}")
    return path, config


def _resolve_probe_file(config_path: Path, value: Any, label: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.startswith(("http://", "https://", "file://")):
        raise ViewerAdapterError(f"{label} must be a local path")
    return (config_path.parent / value).resolve()


def _backend_contract_probe(config_path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    backend = config["backend_contract"]
    source_path = _resolve_probe_file(config_path, backend.get("session_source_path"), "session_source_path")
    schema_path = _resolve_probe_file(config_path, backend.get("open_schema_path"), "open_schema_path")
    source_present = bool(source_path is not None and source_path.is_file() and not source_path.is_symlink())
    schema_present = bool(schema_path is not None and schema_path.is_file() and not schema_path.is_symlink())
    properties: set[str] = set()
    schema_error: str | None = None
    if schema_present and schema_path is not None:
        try:
            schema = read_json(schema_path)
            raw_properties = schema.get("properties", {})
            if isinstance(raw_properties, Mapping):
                properties = {str(key) for key in raw_properties}
        except Exception as exc:  # A diagnostic reports malformed backend state.
            schema_error = f"{type(exc).__name__}: {exc}"
    return {
        "kind": backend["kind"],
        "session_source_present": source_present,
        "open_schema_present": schema_present,
        "open_schema_fields": sorted(properties),
        "accepts_anonymous_scene_ref": "anonymous_scene_ref" in properties,
        "accepts_raw_scene_selector": bool(properties.intersection({"asset_path", "qid", "mask_path"})),
        "schema_error": schema_error,
        "live_probe_performed": False,
    }


def diagnose_viewer_adapter(
    config_path: str | Path,
    *,
    viewer_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report readiness without importing or invoking an untrusted backend.

    A static source/schema probe can establish that the known MCP backend is
    present and can show its unsafe public fields.  It cannot attest runtime
    isolation or evidence, so diagnostics never turn an unimplemented config
    into a production-ready adapter.
    """
    path, config = load_adapter_config(config_path)
    probe = _backend_contract_probe(path, config)
    implementation = config["implementation"]
    scene_registry = config["scene_registry"]
    security = config["security"]
    operations = config["operations"]
    allowed_operations = set(operations["allowed"])
    native_measurements = set(operations["native_measurements"])
    blockers: list[str] = []

    if not config["enabled"]:
        blockers.append("adapter_disabled")
    if not implementation.get("module") or not implementation.get("factory"):
        blockers.append("controlled_adapter_implementation_missing")
    if not scene_registry.get("path"):
        blockers.append("anonymous_scene_registry_missing")
    else:
        registry_path = _resolve_probe_file(path, scene_registry.get("path"), "scene_registry.path")
        if registry_path is None or not registry_path.is_file() or registry_path.is_symlink():
            blockers.append("anonymous_scene_registry_unavailable")
    if not scene_registry.get("anonymous_lookup"):
        blockers.append("anonymous_scene_lookup_missing")
    if scene_registry.get("solver_visible"):
        blockers.append("scene_registry_must_be_host_private")
    if security.get("accepts_solver_asset_identifiers"):
        blockers.append("solver_asset_identifiers_accepted")
    if security.get("exposes_raw_asset_metadata"):
        blockers.append("raw_asset_metadata_exposed")
    if security.get("exposes_raw_asset_bytes"):
        blockers.append("raw_asset_bytes_exposed")
    if security.get("network_access"):
        blockers.append("network_access_enabled")
    if security.get("arbitrary_compute"):
        blockers.append("arbitrary_compute_enabled")
    if not security.get("attested_evidence"):
        blockers.append("attested_evidence_missing")
    if not probe["session_source_present"]:
        blockers.append("backend_session_source_missing")
    if not probe["open_schema_present"] or probe["schema_error"]:
        blockers.append("backend_open_schema_unavailable")
    if probe["accepts_raw_scene_selector"]:
        blockers.append("backend_open_contract_accepts_raw_scene_selector")
    if not probe["accepts_anonymous_scene_ref"]:
        blockers.append("backend_open_contract_lacks_anonymous_scene_ref")
    unsupported = sorted(allowed_operations - SOLVER_SAFE_OPERATIONS)
    if unsupported:
        blockers.append(f"unsupported_operation:{unsupported[0]}")
    non_native_measurements = sorted((allowed_operations & MEASUREMENT_OPERATIONS) - native_measurements)
    if non_native_measurements:
        blockers.append(f"measurement_not_viewer_native:{non_native_measurements[0]}")
    unadvertised_native = sorted(native_measurements - allowed_operations)
    if unadvertised_native:
        blockers.append(f"native_measurement_not_advertised:{unadvertised_native[0]}")

    profile_report: dict[str, Any] = {"provided": viewer_profile is not None}
    if viewer_profile is not None:
        body = viewer_profile.get("body")
        if not isinstance(body, Mapping):
            raise ViewerAdapterError("viewer profile is malformed")
        required = body.get("allowed_operations")
        if not isinstance(required, list) or not all(isinstance(item, str) and item for item in required):
            raise ViewerAdapterError("viewer profile allowed_operations is malformed")
        missing = sorted(set(required) - allowed_operations)
        measurement_missing = sorted((set(required) & MEASUREMENT_OPERATIONS) - native_measurements)
        if missing:
            blockers.append(f"profile_operation_unavailable:{missing[0]}")
        if measurement_missing:
            blockers.append(f"profile_measurement_not_viewer_native:{measurement_missing[0]}")
        if body.get("production_eligible") is not True:
            blockers.append("viewer_profile_not_production_eligible")
        profile_report = {
            "provided": True,
            "status": viewer_profile.get("status"),
            "viewer_profile_digest": viewer_profile.get("content_digest"),
            "required_operations": sorted(set(required)),
            "missing_operations": missing,
            "non_native_measurements": measurement_missing,
        }

    unique_blockers = sorted(set(blockers))
    return {
        "artifact_kind": "viewer_adapter_diagnostic",
        "adapter_id": config["adapter_id"],
        "adapter_config_digest": digest(config),
        "status": "blocked" if unique_blockers else "configuration_ready_unprobed",
        "production_ready": False,
        "health": {
            "status": "unavailable" if unique_blockers else "unprobed",
            "backend_contract": probe,
        },
        "profile": profile_report,
        "allowed_operations": sorted(allowed_operations),
        "native_measurement_operations": sorted(native_measurements),
        "blockers": unique_blockers,
    }
