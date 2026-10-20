"""Fail-closed allowlist validation for solver-visible artifact bundles."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping


class SolverBundleLeakageError(ValueError):
    """Solver-visible output includes author-only or otherwise unsafe data."""


_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_REAL_ASSET_EXTENSION = re.compile(
    r"\.(?:pdb|cif|mmcif|sdf|mol2|mol|xyz|glb|gltf|obj|stl|fbx|nii(?:\.gz)?|mrc|dcm|dicom|vtk|ply)(?:$|[?#\s])",
    re.IGNORECASE,
)
_FORBIDDEN_NAME_PARTS = {
    "answer",
    "ground",
    "truth",
    "reasoning",
    "asset",
    "seed",
    "knowledge",
    "token",
    "credential",
    "secret",
    "private",
    "author",
    "audit",
    "receipt",
    "metadata",
    "raw",
    "review",
}
_SAFE_COMPOUND_KEYS = {"answer_schema"}
_SAFE_VIEWER_OPERATIONS = {
    "inspect",
    "isolate",
    "render",
    "rotate",
    "orbit",
    "select",
    "zoom",
    "pan",
    "slice",
    "measure_distance",
    "measure_angle",
    "measure_dihedral",
    "measure_area",
    "measure_volume",
}
_ALLOWED_COMPONENT_NAMES = {
    "task.json",
    "scene-ref.json",
    "viewer-session-request.json",
    "capability-profile.json",
}


def _raise(location: str, reason: str) -> None:
    raise SolverBundleLeakageError(f"solver bundle leak at {location}: {reason}")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _name_is_forbidden(value: str, *, allow_answer_schema: bool = False) -> bool:
    if allow_answer_schema and value == "answer_schema":
        return False
    normalized = _normalize_name(value)
    return any(part in normalized for part in _FORBIDDEN_NAME_PARTS)


def _check_scalar(value: str, location: str) -> None:
    lower = value.lower()
    if value.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE.match(value):
        _raise(location, "absolute path is not solver-safe")
    if ".." in Path(value).parts or value.startswith("../") or "/../" in value or "\\..\\" in value:
        _raise(location, "path traversal is not solver-safe")
    if lower.startswith(("file://", "http://", "https://", "feishu://")):
        _raise(location, "external or file URL is not solver-safe")
    if _REAL_ASSET_EXTENSION.search(value):
        _raise(location, "real asset filename extension is not solver-safe")
    if any(marker in lower for marker in ("real-private-", "author-only", "authortruthreceipt", "private://")):
        _raise(location, "private value marker")
    if "asset" in lower and ("id" in lower or "/" in value or "real" in lower):
        _raise(location, "asset identifier is not solver-safe")


def _check_no_sensitive_data(value: Any, location: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            if not isinstance(raw_key, str):
                _raise(location, "non-string object key")
            if _name_is_forbidden(raw_key, allow_answer_schema=True):
                _raise(f"{location}.{raw_key}", f"forbidden field {raw_key}")
            _check_no_sensitive_data(nested, f"{location}.{raw_key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _check_no_sensitive_data(nested, f"{location}[{index}]")
    elif isinstance(value, str):
        _check_scalar(value, location)


def _require_exact_keys(value: Mapping[str, Any], allowed: set[str], required: set[str], location: str) -> None:
    unexpected = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unexpected:
        _raise(location, f"unknown solver field {unexpected[0]}")
    if missing:
        _raise(location, f"missing required solver field {missing[0]}")


def _require_text(value: Any, location: str) -> None:
    if not isinstance(value, str) or not value.strip():
        _raise(location, "must be a non-empty string")
    _check_scalar(value, location)


def _validate_viewer_request(value: Any, location: str, *, legacy: bool) -> None:
    if not isinstance(value, Mapping):
        _raise(location, "must be an object")
    if legacy:
        _require_exact_keys(
            value,
            {"session_request_id", "allowed_operations", "expires_in_seconds"},
            {"session_request_id", "allowed_operations", "expires_in_seconds"},
            location,
        )
        _require_text(value["session_request_id"], f"{location}.session_request_id")
    else:
        _require_exact_keys(
            value,
            {"request_id", "viewer_profile_digest", "allowed_operations"},
            {"request_id", "viewer_profile_digest", "allowed_operations"},
            location,
        )
        _require_text(value["request_id"], f"{location}.request_id")
        digest_value = value["viewer_profile_digest"]
        if not isinstance(digest_value, str) or not _SHA256_REF.fullmatch(digest_value):
            _raise(f"{location}.viewer_profile_digest", "must be a sha256 reference")
    operations = value["allowed_operations"]
    if not isinstance(operations, list) or not operations:
        _raise(f"{location}.allowed_operations", "must be a non-empty list")
    for index, operation in enumerate(operations):
        _require_text(operation, f"{location}.allowed_operations[{index}]")
        if operation not in _SAFE_VIEWER_OPERATIONS:
            _raise(f"{location}.allowed_operations[{index}]", "operation is not in the controlled solver allowlist")
    if legacy and (not isinstance(value["expires_in_seconds"], int) or isinstance(value["expires_in_seconds"], bool) or value["expires_in_seconds"] <= 0):
        _raise(f"{location}.expires_in_seconds", "must be a positive integer")


def _validate_export_task(value: Any, location: str) -> None:
    if not isinstance(value, Mapping):
        _raise(location, "solver export must be an object")
    _require_exact_keys(
        value,
        {"artifact_kind", "task_id", "prompt", "anonymous_scene_ref", "viewer_session_request"},
        {"artifact_kind", "task_id", "prompt", "anonymous_scene_ref", "viewer_session_request"},
        location,
    )
    if value["artifact_kind"] != "solver_safe_export":
        _raise(f"{location}.artifact_kind", "must be solver_safe_export")
    for key in ("task_id", "prompt", "anonymous_scene_ref"):
        _require_text(value[key], f"{location}.{key}")
    _validate_viewer_request(value["viewer_session_request"], f"{location}.viewer_session_request", legacy=False)


def _validate_legacy_task(value: Any, location: str) -> None:
    if not isinstance(value, Mapping):
        _raise(location, "task must be an object")
    _require_exact_keys(value, {"task_id", "instruction", "answer_schema"}, {"task_id", "instruction", "answer_schema"}, location)
    _require_text(value["task_id"], f"{location}.task_id")
    _require_text(value["instruction"], f"{location}.instruction")
    schema = value["answer_schema"]
    if not isinstance(schema, Mapping):
        _raise(f"{location}.answer_schema", "must be an object")
    _require_exact_keys(schema, {"kind"}, {"kind"}, f"{location}.answer_schema")
    _require_text(schema["kind"], f"{location}.answer_schema.kind")


def _validate_scene_ref(value: Any, location: str) -> None:
    if not isinstance(value, Mapping):
        _raise(location, "scene ref must be an object")
    _require_exact_keys(value, {"scene_ref"}, {"scene_ref"}, location)
    _require_text(value["scene_ref"], f"{location}.scene_ref")


def _validate_capability_profile(value: Any, location: str) -> None:
    if not isinstance(value, Mapping):
        _raise(location, "capability profile must be an object")
    _require_exact_keys(value, {"measurement"}, {"measurement"}, location)
    if not isinstance(value["measurement"], bool):
        _raise(f"{location}.measurement", "must be boolean")


def _validate_file(relative: str, value: Any) -> None:
    _check_no_sensitive_data(value, relative)
    name = Path(relative).name
    if name == "task.json":
        _validate_legacy_task(value, relative)
    elif name == "scene-ref.json":
        _validate_scene_ref(value, relative)
    elif name == "viewer-session-request.json":
        _validate_viewer_request(value, relative, legacy=True)
    elif name == "capability-profile.json":
        _validate_capability_profile(value, relative)
    elif name.startswith("task-") and name.endswith(".json"):
        _validate_export_task(value, relative)
    else:
        _raise(relative, "unknown solver artifact filename")


def assert_solver_bundle_safe(bundle_root: str | Path) -> None:
    """Assert that a non-empty solver bundle matches a narrow code allowlist.

    This intentionally does not trust an external mutable schema at release
    time. The built-in allowlist mirrors the solver-safe export contract and
    supports only the legacy component files needed by local compatibility
    tests. All other files, paths, fields, and value forms fail closed.
    """
    root = Path(bundle_root).expanduser()
    if not root.exists() or not root.is_dir():
        raise SolverBundleLeakageError(f"solver bundle is not a directory: {root}")
    if root.is_symlink():
        raise SolverBundleLeakageError(f"solver bundle root cannot be a symlink: {root}")
    files = sorted((path for path in root.rglob("*") if path.is_file() or path.is_symlink()), key=lambda path: path.as_posix())
    if not files:
        raise SolverBundleLeakageError("solver bundle must contain at least one solver artifact")
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            _raise(relative, "symlink")
        if path.parent != root:
            _raise(relative, "nested solver artifact path is not allowed")
        if path.suffix.lower() != ".json":
            _raise(relative, "non-JSON solver artifact")
        if _name_is_forbidden(path.stem):
            _raise(relative, "private filename")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SolverBundleLeakageError(f"cannot validate solver artifact {relative}: {exc}") from exc
        _validate_file(relative, value)
