"""Build and verify a local-only reviewer bundle for an attested vNext batch.

The release pipeline deliberately separates solver, scorer, and audit files.
This module preserves that boundary: only a rebased solver projection and
anonymous scene URLs enter the HTTP surface.  Original asset paths are used
only while copying or hard-linking selected bytes into the local bundle and
are never serialized into a public artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping

from ..contracts import digest, digest_bytes, require_digest, verify_envelope
from ..errors import InputError
from ..viewer.capability_mapping import (
    MEASUREMENT_OPERATIONS as CAPABILITY_MEASUREMENT_OPERATIONS,
    ViewerCapabilityMappingError,
    load_compiled_selection_capability_mapping,
    validate_capability_mapping_reference,
)


class ReviewerBundleError(InputError):
    """The local reviewer package is incomplete, unsafe, or tampered."""


_BUNDLE_NAME = "BUNDLE.json"
_REGISTRY_NAME = "scene-registry.json"
_CHECKSUM_NAME = "SHA256SUMS"
_TASKS_NAME = "tasks.json"
_SCENE_REF = re.compile(r"^scene_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_VIEWER_BY_SOURCE = {
    "molstar_deployed": "molstar",
    "niivue_deployed": "niivue",
}
_MOLSTAR_FORMATS = frozenset({"mmcif", "cif", "pdb", "bcif", "sdf", "mol", "mol2", "xyz"})
_NIIVUE_FORMATS = frozenset({"nii", "nii.gz"})
_PUBLIC_OPERATIONS = {
    "molstar": frozenset({"rotate", "zoom", "measure_distance", "render"}),
    "niivue": frozenset({"rotate", "slice", "window_level", "zoom", "render"}),
}
_SELECTION_OPERATION_ALIASES = {
    "viewer_rotate": "rotate",
    "viewer_zoom": "zoom",
    "viewer_measure_distance": "measure_distance",
    "viewer_measure_dihedral": "measure_dihedral",
    "viewer_measure_area": "measure_area",
    "viewer_measure_volume": "measure_volume",
    "viewer_overlay_compare": "overlay_compare",
    "viewer_inspect": "inspect",
    "viewer_select": "select",
    "viewer_slice": "slice",
    "viewer_window_level": "window_level",
}
_RUNTIME_CAPABILITY_MARKERS = {
    "molstar": {
        "rotate": "CameraControls",
        "zoom": "CameraControls",
        "measure_distance": "StructureSelectionsDistance3D",
        "render": "loadStructureFromUrl",
    },
    "niivue": {
        "rotate": "renderAzimuth",
        "zoom": "setScale",
        "slice": "setSliceType",
        "window_level": "contrast",
        "render": "sliceTypeRender",
    },
}
_RUNTIME_SCRIPT_PATHS = {
    "molstar": "shared/vendor/molstar/4.5.0/build/viewer/molstar.js",
    "niivue": "shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js",
}
_FORBIDDEN_PUBLIC_KEYS = {
    "answer",
    "answer_key",
    "answer_keys",
    "asset_path",
    "asset_id",
    "backend_scene_id",
    "file_path",
    "ground_truth",
    "metadata",
    "mask",
    "mask_id",
    "mask_selection",
    "mask_url",
    "overlay",
    "original_path",
    "private_path",
    "raw_path",
    "reference_answer",
    "reasoning",
    "source_path",
}
_RAW_ASSET_REFERENCE = re.compile(
    r"\.(?:pdb|cif|mmcif|sdf|mol2|mol|xyz|glb|gltf|obj|stl|fbx|nii(?:\.gz)?|mrc|dcm|dicom|vtk|ply)(?:$|[?#\s])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Asset:
    role: str
    relative_path: str
    size: int
    sha256: str
    format: str
    mask_selection: str | None = None
    mask_id: int | None = None


@dataclass(frozen=True)
class _BatchRow:
    task_id: str
    question_id: str
    slot_digest: str
    target_leaf: str
    viewer: str
    source_viewer: str
    source_access_track: str
    source_hint_operations: tuple[str, ...]
    source_hint_digest: str
    allowed_operations: tuple[str, ...]
    primary: _Asset
    mask: _Asset | None


@dataclass(frozen=True)
class _BatchPlanJob:
    job_id: str
    slot_digest: str
    target_leaf: str
    generation_strategy: str
    allowed_operations: tuple[str, ...]


@dataclass(frozen=True)
class _BatchPlan:
    digest: str
    lock_bundle_digest: str
    jobs: tuple[_BatchPlanJob, ...]


@dataclass(frozen=True)
class _StagingSelectionBinding:
    selection_batch_digest: str
    batch_plan_digest: str
    batch_plan_lock_bundle_digest: str
    run_id: str
    plan_digest: str
    config_digest: str
    viewer_profile_digest: str
    lock_digests_digest: str


@dataclass(frozen=True)
class _StagedTask:
    task_id: str
    solver: dict[str, Any]
    scorer: dict[str, Any]
    audit: dict[str, Any]
    scorer_filename: str
    audit_filename: str
    candidate_digest: str
    review_digest: str
    batch_job_id: str
    selection_slot_digest: str


def _raw_sha256(content: bytes) -> str:
    return sha256(content).hexdigest()


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    """Read one stable regular file without following a final symlink."""
    try:
        before = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReviewerBundleError(f"cannot inspect {label}") from exc
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise ReviewerBundleError(f"{label} must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ReviewerBundleError(f"cannot open {label} safely") from exc
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ReviewerBundleError(f"{label} changed while it was read")
    return b"".join(chunks)


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    content = _read_regular_bytes(path, label=label)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewerBundleError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ReviewerBundleError(f"{label} must be a JSON object")
    return dict(value), content


def _local_file(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ReviewerBundleError(f"{label} cannot be a symlink")
    try:
        path = raw.resolve(strict=True)
    except OSError as exc:
        raise ReviewerBundleError(f"cannot resolve {label}") from exc
    if not path.is_file() or path.is_symlink():
        raise ReviewerBundleError(f"{label} must be a regular local file")
    return path


def _local_directory(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ReviewerBundleError(f"{label} cannot be a symlink")
    try:
        path = raw.resolve(strict=True)
    except OSError as exc:
        raise ReviewerBundleError(f"cannot resolve {label}") from exc
    if not path.is_dir() or path.is_symlink():
        raise ReviewerBundleError(f"{label} must be a regular local directory")
    return path


def _safe_relative_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReviewerBundleError(f"{label} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ReviewerBundleError(f"{label} must be a canonical relative path")
    return path.as_posix()


def _safe_filename(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_FILENAME.fullmatch(value):
        raise ReviewerBundleError(f"{label} must be a simple safe filename")
    return value


def _forbid_legacy_answer_keys(value: Any, *, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ReviewerBundleError(f"{label} has a non-string object key")
            normalized = re.sub(r"[^a-z0-9]+", "", key.lower())
            if normalized == "answerkeys":
                raise ReviewerBundleError(f"{label} contains deprecated answer_keys")
            _forbid_legacy_answer_keys(nested, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _forbid_legacy_answer_keys(nested, label=f"{label}[{index}]")


def _assert_public_keys_safe(value: Any, *, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ReviewerBundleError(f"{label} has a non-string object key")
            if key.lower() in _FORBIDDEN_PUBLIC_KEYS:
                raise ReviewerBundleError(f"{label}.{key} is not allowed in a public reviewer artifact")
            _assert_public_keys_safe(nested, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_public_keys_safe(nested, label=f"{label}[{index}]")
    elif isinstance(value, str):
        lower = value.lower()
        if value.startswith(("/", "\\", "file://", "http://", "https://")) or ".." in PurePosixPath(value).parts:
            raise ReviewerBundleError(f"{label} exposes a path or URL")
        if "/crawl_data/" in lower or "/datasets/" in lower or "assets/" in lower:
            raise ReviewerBundleError(f"{label} appears to expose an original asset path")
        if _RAW_ASSET_REFERENCE.search(value):
            raise ReviewerBundleError(f"{label} appears to expose a raw asset filename")


def _parse_digest(value: Any, *, label: str, raw: bool = False) -> str:
    if raw:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ReviewerBundleError(f"{label} must be an unprefixed SHA-256")
        return value
    try:
        return require_digest(value, label)
    except InputError as exc:
        raise ReviewerBundleError(str(exc)) from exc


def _infer_format(relative_path: str) -> str:
    lower = relative_path.lower()
    if lower.endswith(".nii.gz"):
        return "nii.gz"
    suffix = PurePosixPath(lower).suffix.lstrip(".")
    if not suffix:
        raise ReviewerBundleError("selected asset has no supported filename extension")
    return suffix


def _parse_asset(value: Any, *, role: str, viewer: str) -> _Asset:
    if not isinstance(value, Mapping):
        raise ReviewerBundleError(f"{role} asset must be an object")
    allowed = {"role", "path", "bytes", "sha256", "mask_selection", "mask_id"}
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ReviewerBundleError(f"{role} asset has unsupported field: {unexpected[0]}")
    required = {"role", "path", "bytes", "sha256"}
    if set(value) - allowed or not required.issubset(value):
        raise ReviewerBundleError(f"{role} asset is incomplete")
    if value.get("role") != role:
        raise ReviewerBundleError(f"{role} asset role is inconsistent")
    relative = _safe_relative_path(value.get("path"), label=f"{role} asset path")
    size = value.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ReviewerBundleError(f"{role} asset bytes must be a positive integer")
    checksum = _parse_digest(value.get("sha256"), label=f"{role} asset sha256", raw=True)
    asset_format = _infer_format(relative)
    if viewer == "molstar" and asset_format not in _MOLSTAR_FORMATS:
        raise ReviewerBundleError(f"Mol* cannot load selected format: {asset_format}")
    if viewer == "niivue" and asset_format not in _NIIVUE_FORMATS:
        raise ReviewerBundleError(f"NiiVue cannot load selected format: {asset_format}")
    selection: str | None = None
    mask_id: int | None = None
    if role == "mask":
        if viewer != "niivue":
            raise ReviewerBundleError("a selected mask requires the NiiVue bridge")
        selection_value = value.get("mask_selection")
        if selection_value != "label_index":
            raise ReviewerBundleError("mask asset must use explicit label_index; merged non-zero overlays are forbidden")
        selection = "label_index"
        mask_id_value = value.get("mask_id")
        if not isinstance(mask_id_value, int) or isinstance(mask_id_value, bool) or mask_id_value < 0:
            raise ReviewerBundleError("label_index mask requires a non-negative integer mask_id")
        mask_id = mask_id_value
    elif "mask_selection" in value or "mask_id" in value:
        raise ReviewerBundleError("primary asset cannot carry mask-only metadata")
    return _Asset(
        role=role,
        relative_path=relative,
        size=size,
        sha256=checksum,
        format=asset_format,
        mask_selection=selection,
        mask_id=mask_id,
    )


def _parse_batch_row(value: Any) -> _BatchRow:
    if not isinstance(value, Mapping):
        raise ReviewerBundleError("batch row must be an object")
    required = {
        "slot_ref",
        "question_id",
        "task_id",
        "source_leaf",
        "target_leaf",
        "mapping_status",
        "access_track",
        "viewer_hint",
        "assets",
        "generation_status",
    }
    allowed = required | {"target_leaf_config"}
    unexpected = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unexpected or missing:
        detail = unexpected[0] if unexpected else missing[0]
        raise ReviewerBundleError(f"batch row has an invalid field: {detail}")
    if value.get("mapping_status") != "mapped" or not isinstance(value.get("target_leaf"), str):
        raise ReviewerBundleError("reviewer bundle accepts only mapped vNext batch rows")
    task_id = value.get("task_id")
    question_id = value.get("question_id")
    if not isinstance(task_id, str) or not task_id or not isinstance(question_id, str) or not question_id:
        raise ReviewerBundleError("batch row task_id and question_id are required")
    target_leaf = value.get("target_leaf")
    if not isinstance(target_leaf, str) or not target_leaf:
        raise ReviewerBundleError("batch row target_leaf is required")
    slot_ref = value.get("slot_ref")
    if not isinstance(slot_ref, Mapping) or set(slot_ref) != {"id", "content_digest"}:
        raise ReviewerBundleError("batch row slot_ref is malformed")
    if not isinstance(slot_ref.get("id"), str) or not slot_ref["id"]:
        raise ReviewerBundleError("batch row slot_ref id is invalid")
    slot_digest = _parse_digest(slot_ref.get("content_digest"), label="batch row slot digest")
    viewer_hint = value.get("viewer_hint")
    if not isinstance(viewer_hint, Mapping) or set(viewer_hint) != {"source_viewer", "allowed_operations"}:
        raise ReviewerBundleError("batch row viewer_hint is malformed")
    source_viewer = viewer_hint.get("source_viewer")
    if not isinstance(source_viewer, str) or not source_viewer:
        raise ReviewerBundleError("batch row source viewer is invalid")
    viewer = _VIEWER_BY_SOURCE.get(source_viewer)
    if viewer is None:
        raise ReviewerBundleError("batch row names an unsupported source viewer")
    allowed_operations = viewer_hint.get("allowed_operations")
    if (
        not isinstance(allowed_operations, list)
        or not allowed_operations
        or not all(isinstance(item, str) and item in _SELECTION_OPERATION_ALIASES for item in allowed_operations)
        or len(set(allowed_operations)) != len(allowed_operations)
    ):
        raise ReviewerBundleError("batch row allowed_operations are malformed or unsupported")
    access_track = value.get("access_track")
    if (
        not isinstance(access_track, Mapping)
        or set(access_track) != {"source", "effective", "conversion"}
        or access_track.get("source") not in {"raw_compute", "viewer_measurement"}
        or access_track.get("effective") != access_track.get("source")
        or access_track.get("conversion") != "preserved"
    ):
        raise ReviewerBundleError("batch row access_track is malformed or changes its source route")
    assets = value.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ReviewerBundleError("batch row assets must be a non-empty list")
    by_role: dict[str, Any] = {}
    for item in assets:
        if not isinstance(item, Mapping) or not isinstance(item.get("role"), str):
            raise ReviewerBundleError("batch row asset is malformed")
        role = item["role"]
        if role not in {"primary", "mask"} or role in by_role:
            raise ReviewerBundleError("batch row has duplicate or unsupported asset roles")
        by_role[role] = item
    if "primary" not in by_role:
        raise ReviewerBundleError("batch row has no primary asset")
    primary = _parse_asset(by_role["primary"], role="primary", viewer=viewer)
    mask = _parse_asset(by_role["mask"], role="mask", viewer=viewer) if "mask" in by_role else None
    if viewer == "niivue" and mask is None:
        raise ReviewerBundleError("NiiVue selection row requires its attested mask asset")
    if viewer == "molstar" and mask is not None:
        raise ReviewerBundleError("Mol* selection row cannot include a NiiVue mask asset")
    return _BatchRow(
        task_id=task_id,
        question_id=question_id,
        slot_digest=slot_digest,
        target_leaf=target_leaf,
        viewer=viewer,
        source_viewer=str(source_viewer),
        source_access_track=str(access_track["source"]),
        source_hint_operations=tuple(allowed_operations),
        source_hint_digest=digest(
            {"source_viewer": source_viewer, "allowed_operations": list(allowed_operations)}
        ),
        allowed_operations=tuple(_SELECTION_OPERATION_ALIASES[item] for item in allowed_operations),
        primary=primary,
        mask=mask,
    )


def _load_batch(path_value: str | Path) -> tuple[dict[str, Any], list[_BatchRow]]:
    path = _local_file(path_value, label="selection batch manifest")
    value, _ = _read_json(path, label="selection batch manifest")
    try:
        envelope = verify_envelope(value, kind="vnext_selection_batch_manifest")
    except InputError as exc:
        raise ReviewerBundleError(f"selection batch manifest failed attestation: {exc}") from exc
    body = envelope.get("body")
    if not isinstance(body, Mapping):
        raise ReviewerBundleError("selection batch manifest body is malformed")
    required = {
        "source_selection",
        "taxonomy_mapping",
        "total",
        "mapping_coverage",
        "generation_eligible",
        "generation_blockers",
        "rows",
    }
    if set(body) != required:
        raise ReviewerBundleError("selection batch manifest body has unexpected fields")
    if body.get("generation_eligible") is not False:
        raise ReviewerBundleError("selection batch manifest must remain local-only and non-production")
    source_selection = body.get("source_selection")
    if not isinstance(source_selection, Mapping) or set(source_selection) != {
        "selection_id_sha256",
        "selection_file_sha256",
        "import_digest",
    }:
        raise ReviewerBundleError("selection batch manifest source_selection is malformed")
    _parse_digest(source_selection.get("selection_id_sha256"), label="selection id sha256", raw=True)
    _parse_digest(source_selection.get("selection_file_sha256"), label="selection file sha256")
    _parse_digest(source_selection.get("import_digest"), label="selection import digest")
    rows_value = body.get("rows")
    if not isinstance(rows_value, list) or body.get("total") != len(rows_value) or not rows_value:
        raise ReviewerBundleError("selection batch manifest has an invalid row count")
    rows = [_parse_batch_row(item) for item in rows_value]
    task_ids = [row.task_id for row in rows]
    if len(set(task_ids)) != len(task_ids):
        raise ReviewerBundleError("selection batch manifest repeats a task_id")
    slot_digests = [row.slot_digest for row in rows]
    if len(set(slot_digests)) != len(slot_digests):
        raise ReviewerBundleError("selection batch manifest repeats a source slot digest")
    return envelope, rows


def _capability_receipt_path(value: Any, *, plan_path: Path) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReviewerBundleError("viewer capability receipt path is invalid")
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        if any(part == ".." for part in PurePosixPath(value).parts):
            raise ReviewerBundleError("relative viewer capability receipt path escapes the batch plan")
        raw = plan_path.parent / raw
    return _local_file(raw, label="viewer capability receipt")


def _profile_operation_set(profile: Mapping[str, Any], field: str, *, allow_empty: bool = False) -> set[str]:
    value = profile.get(field)
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ReviewerBundleError(f"viewer capability profile {field} is malformed")
    return set(value)


def _batch_row_asset_formats(row: _BatchRow) -> list[str]:
    formats = {row.primary.format}
    if row.mask is not None:
        formats.add(row.mask.format)
    return sorted(formats)


def _validate_viewer_capability_mapping(
    *,
    viewer_lock: Mapping[str, Any],
    row: _BatchRow,
    generation_strategy: str,
    operations: set[str],
    plan_path: Path,
) -> None:
    """Recheck the viewer bridge without reapplying source policy to reauthored work.

    Batch preparation is the authority for leaf policy.  The reviewer bundle
    instead verifies that the already-attested capability receipt still binds
    this exact source slot, static profile, viewer lock, and public operation
    scope.  Historical source hints remain mandatory only for
    ``preserve_intent``.
    """
    expected_strategy = "reauthor_on_asset" if row.source_access_track == "raw_compute" else "preserve_intent"
    if generation_strategy != expected_strategy:
        raise ReviewerBundleError("batch plan generation strategy does not match its source access track")
    try:
        reference = validate_capability_mapping_reference(viewer_lock.get("capability_mapping"))
        loaded = load_compiled_selection_capability_mapping()
    except ViewerCapabilityMappingError as exc:
        raise ReviewerBundleError(f"batch plan viewer capability mapping is invalid: {exc}") from exc
    profile = loaded["profiles"].get(reference["profile_id"])
    if not isinstance(profile, Mapping):
        raise ReviewerBundleError("batch plan viewer capability profile is unavailable")
    if profile.get("viewer_family") != row.viewer:
        raise ReviewerBundleError("batch plan viewer capability profile has the wrong viewer family")
    if row.source_viewer not in set(profile.get("source_viewers", [])):
        raise ReviewerBundleError("batch plan viewer capability profile is incompatible with the source viewer")
    if row.target_leaf not in set(profile.get("leaf_coverage", [])):
        raise ReviewerBundleError("batch plan viewer capability profile does not cover the target leaf")
    supported_formats = _profile_operation_set(profile, "asset_formats")
    unsupported_formats = sorted(set(_batch_row_asset_formats(row)) - supported_formats)
    if unsupported_formats:
        raise ReviewerBundleError(
            f"batch plan viewer capability profile does not support asset format: {unsupported_formats[0]}"
        )
    controlled_operations = _profile_operation_set(profile, "controlled_operations")
    native_measurements = _profile_operation_set(profile, "native_measurement_operations", allow_empty=True)
    unsupported_operations = sorted(operations - controlled_operations)
    if unsupported_operations:
        raise ReviewerBundleError(
            f"batch plan viewer capability profile does not control operation: {unsupported_operations[0]}"
        )
    non_native_measurements = sorted(
        (operations & CAPABILITY_MEASUREMENT_OPERATIONS) - native_measurements
    )
    if non_native_measurements:
        raise ReviewerBundleError(
            f"batch plan viewer capability profile has no native measurement: {non_native_measurements[0]}"
        )
    if generation_strategy == "preserve_intent":
        aliases = profile.get("source_operation_aliases")
        if not isinstance(aliases, Mapping):
            raise ReviewerBundleError("batch plan viewer capability profile source aliases are malformed")
        preserved: set[str] = set()
        for source_operation in row.source_hint_operations:
            mapped = aliases.get(source_operation)
            if not isinstance(mapped, str) or mapped not in controlled_operations:
                raise ReviewerBundleError(
                    f"preserve_intent source hint is unavailable from the controlled profile: {source_operation}"
                )
            preserved.add(mapped)
        missing_preserved = sorted((preserved | {"render"}) - operations)
        if missing_preserved:
            raise ReviewerBundleError(
                f"preserve_intent viewer lock does not preserve source operation: {missing_preserved[0]}"
            )

    receipt_reference = reference["receipt"]
    receipt_path = _capability_receipt_path(receipt_reference["path"], plan_path=plan_path)
    receipt, _ = _read_json(receipt_path, label="viewer capability receipt")
    if digest(receipt) != receipt_reference["content_digest"]:
        raise ReviewerBundleError("viewer capability receipt digest does not match its plan reference")
    expected_receipt = {
        "schema_version": 1,
        "kind": "qagen_compiled_selection_viewer_capability_receipt",
        "decision": "approved",
        "mapping_id": reference["mapping_id"],
        "mapping_digest": reference["content_digest"],
        "profile_id": reference["profile_id"],
        "profile_digest": reference["profile_digest"],
        "slot_digest": row.slot_digest,
        "source_access_track": row.source_access_track,
        "generation_strategy": generation_strategy,
        "target_leaf": row.target_leaf,
        "source_viewer": row.source_viewer,
        "source_hint_digest": row.source_hint_digest,
        "asset_formats": _batch_row_asset_formats(row),
        "viewer_family": row.viewer,
        "viewer_lock_content_digest": viewer_lock.get("content_digest"),
        "allowed_operations": sorted(operations),
    }
    for field, expected in expected_receipt.items():
        if receipt.get(field) != expected:
            raise ReviewerBundleError(f"viewer capability receipt does not bind the expected {field}")
    for field in ("id", "reviewer", "reviewed_at"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            raise ReviewerBundleError(f"viewer capability receipt {field} is invalid")


def _load_batch_plan(
    path_value: str | Path,
    *,
    batch_digest: str,
    batch_rows: list[_BatchRow],
) -> _BatchPlan:
    """Load the plan that gives each frozen source slot its reviewer policy."""
    path = _local_file(path_value, label="compiled selection batch plan")
    value, _ = _read_json(path, label="compiled selection batch plan")
    try:
        plan = verify_envelope(value, kind="compiled_selection_batch_plan")
    except InputError as exc:
        raise ReviewerBundleError(f"compiled selection batch plan failed attestation: {exc}") from exc
    body = plan.get("body")
    if not isinstance(body, Mapping):
        raise ReviewerBundleError("compiled selection batch plan body is malformed")
    required = {
        "selection_batch",
        "lock_bundle_digest",
        "mapping",
        "routing",
        "budget",
        "retry_policy",
        "job_count",
        "jobs",
    }
    if set(body) != required:
        raise ReviewerBundleError("compiled selection batch plan body has an invalid contract")
    selection_batch = body.get("selection_batch")
    if not isinstance(selection_batch, Mapping) or set(selection_batch) != {"path", "content_digest"}:
        raise ReviewerBundleError("compiled selection batch plan selection binding is malformed")
    if _parse_digest(selection_batch.get("content_digest"), label="batch plan selection digest") != batch_digest:
        raise ReviewerBundleError("compiled selection batch plan is bound to another selection batch")
    lock_bundle_digest = _parse_digest(body.get("lock_bundle_digest"), label="batch plan lock bundle digest")
    jobs = body.get("jobs")
    if not isinstance(jobs, list) or body.get("job_count") != len(jobs) or not jobs:
        raise ReviewerBundleError("compiled selection batch plan has an invalid job count")
    rows_by_slot = {row.slot_digest: row for row in batch_rows}
    loaded: list[_BatchPlanJob] = []
    seen_jobs: set[str] = set()
    seen_slots: set[str] = set()
    for index, item in enumerate(jobs):
        if not isinstance(item, Mapping):
            raise ReviewerBundleError(f"compiled selection batch plan job {index} is malformed")
        required_job = {
            "job_id",
            "ordinal",
            "slot_digest",
            "source_access_track",
            "target_leaf",
            "generation_strategy",
            "source_asset_set_digest",
            "lock_bindings",
            "agent_inputs",
            "agent_references",
            "status",
        }
        if set(item) != required_job:
            raise ReviewerBundleError(f"compiled selection batch plan job {index} has an invalid contract")
        job_id = _safe_filename(item.get("job_id"), label="batch plan job id")
        slot_digest = _parse_digest(item.get("slot_digest"), label="batch plan job slot digest")
        row = rows_by_slot.get(slot_digest)
        if row is None or job_id in seen_jobs or slot_digest in seen_slots:
            raise ReviewerBundleError("compiled selection batch plan does not bind each selection slot exactly once")
        if item.get("target_leaf") != row.target_leaf:
            raise ReviewerBundleError("compiled selection batch plan job is bound to another target leaf")
        if item.get("source_access_track") != row.source_access_track:
            raise ReviewerBundleError("compiled selection batch plan job has another source access track")
        generation_strategy = item.get("generation_strategy")
        if generation_strategy not in {"preserve_intent", "reauthor_on_asset"}:
            raise ReviewerBundleError("compiled selection batch plan generation strategy is invalid")
        locks = item.get("lock_bindings")
        if not isinstance(locks, Mapping) or set(locks) != {"config", "seed", "knowledge", "viewer"}:
            raise ReviewerBundleError("compiled selection batch plan job locks are malformed")
        viewer_lock = locks.get("viewer")
        expected_viewer_lock = {
            "status",
            "content_digest",
            "approval_digest",
            "approval_receipt",
            "anonymous_scene_ref",
            "allowed_operations",
            "capability_mapping",
        }
        if (
            not isinstance(viewer_lock, Mapping)
            or (set(viewer_lock) != expected_viewer_lock and set(viewer_lock) != expected_viewer_lock | {"fresh_replay"})
        ):
            raise ReviewerBundleError("compiled selection batch plan viewer lock is malformed")
        if viewer_lock.get("status") != "approved":
            raise ReviewerBundleError("compiled selection batch plan viewer lock is not approved")
        for field in ("content_digest", "approval_digest"):
            _parse_digest(viewer_lock.get(field), label=f"batch plan viewer {field}")
        operations = viewer_lock.get("allowed_operations")
        if (
            not isinstance(operations, list)
            or not operations
            or not all(isinstance(operation, str) and operation for operation in operations)
            or len(set(operations)) != len(operations)
        ):
            raise ReviewerBundleError("compiled selection batch plan viewer operations are malformed")
        if "fresh_replay" in viewer_lock:
            fresh_replay = viewer_lock["fresh_replay"]
            if not isinstance(fresh_replay, Mapping) or set(fresh_replay) != {"lock_set", "registry", "receipt"}:
                raise ReviewerBundleError("compiled selection batch plan viewer fresh replay binding is malformed")
            for name, reference in fresh_replay.items():
                if not isinstance(reference, Mapping) or set(reference) != {"path", "content_digest"}:
                    raise ReviewerBundleError(f"compiled selection batch plan viewer {name} replay reference is malformed")
                if not isinstance(reference.get("path"), str) or not reference["path"]:
                    raise ReviewerBundleError(f"compiled selection batch plan viewer {name} replay path is invalid")
                _parse_digest(reference.get("content_digest"), label=f"batch plan viewer {name} replay digest")
        _validate_viewer_capability_mapping(
            viewer_lock=viewer_lock,
            row=row,
            generation_strategy=str(generation_strategy),
            operations=set(operations),
            plan_path=path,
        )
        seen_jobs.add(job_id)
        seen_slots.add(slot_digest)
        loaded.append(
            _BatchPlanJob(
                job_id=job_id,
                slot_digest=slot_digest,
                target_leaf=row.target_leaf,
                generation_strategy=str(generation_strategy),
                allowed_operations=tuple(operations),
            )
        )
    if seen_slots != set(rows_by_slot):
        raise ReviewerBundleError("compiled selection batch plan does not cover the frozen selection batch")
    return _BatchPlan(
        digest=_parse_digest(plan.get("content_digest"), label="compiled selection batch plan digest"),
        lock_bundle_digest=lock_bundle_digest,
        jobs=tuple(loaded),
    )


def _descriptor(value: Any, *, category: str, filename: str, content: bytes, parsed: Mapping[str, Any]) -> None:
    expected_keys = {"relative_path", "canonical_sha256", "file_sha256", "size_bytes"}
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ReviewerBundleError(f"staged {category} descriptor is malformed")
    expected_path = f"{category}/{filename}"
    if value.get("relative_path") != expected_path:
        raise ReviewerBundleError(f"staged {category} descriptor path changed")
    if value.get("canonical_sha256") != digest(dict(parsed)):
        raise ReviewerBundleError(f"staged {category} canonical digest changed")
    if value.get("file_sha256") != digest_bytes(content):
        raise ReviewerBundleError(f"staged {category} file digest changed")
    if value.get("size_bytes") != len(content):
        raise ReviewerBundleError(f"staged {category} size changed")


def _stage_file(root: Path, category: str, filename: Any) -> Path:
    safe = _safe_filename(filename, label=f"staged {category} filename")
    directory = root / category
    if directory.is_symlink() or not directory.is_dir():
        raise ReviewerBundleError(f"staging {category} directory is missing or unsafe")
    path = directory / safe
    if path.is_symlink() or not path.is_file():
        raise ReviewerBundleError(f"staged {category} artifact is missing or unsafe")
    return path


def _validate_solver_projection(value: Mapping[str, Any]) -> None:
    """Validate the reviewer-local solver projection without widening release.

    The formal release allowlist intentionally does not cover every local
    viewer action. This validator keeps a separate, evidence-backed bridge
    vocabulary instead of widening solver-safe release behavior.
    """
    required = {"artifact_kind", "task_id", "prompt", "anonymous_scene_ref", "viewer_session_request"}
    if set(value) != required or value.get("artifact_kind") != "solver_safe_export":
        raise ReviewerBundleError("staged solver projection has an invalid contract")
    for field in ("task_id", "prompt", "anonymous_scene_ref"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise ReviewerBundleError(f"staged solver projection {field} is invalid")
    request = value.get("viewer_session_request")
    if not isinstance(request, Mapping) or set(request) != {"request_id", "viewer_profile_digest", "allowed_operations"}:
        raise ReviewerBundleError("staged solver projection viewer request is invalid")
    if not isinstance(request.get("request_id"), str) or not request["request_id"]:
        raise ReviewerBundleError("staged solver projection request_id is invalid")
    _parse_digest(request.get("viewer_profile_digest"), label="staged solver viewer profile digest")
    operations = request.get("allowed_operations")
    allowed = set().union(*_PUBLIC_OPERATIONS.values())
    if (
        not isinstance(operations, list)
        or not operations
        or not all(isinstance(item, str) and item in allowed for item in operations)
        or len(set(operations)) != len(operations)
    ):
        raise ReviewerBundleError("staged solver projection has unsupported viewer operations")
    _forbid_legacy_answer_keys(value, label="staged solver projection")
    _assert_public_keys_safe(value, label="staged solver projection")


def _parse_staging_selection_binding(
    value: Any,
    *,
    body: Mapping[str, Any],
) -> _StagingSelectionBinding:
    expected = {
        "selection_batch_digest",
        "batch_plan_digest",
        "batch_plan_lock_bundle_digest",
        "run_id",
        "plan_digest",
        "config_digest",
        "viewer_profile_digest",
        "lock_digests_digest",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ReviewerBundleError("staging selection binding is malformed")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ReviewerBundleError("staging selection binding run_id is invalid")
    if run_id != body.get("run_id"):
        raise ReviewerBundleError("staging selection binding is bound to another run")
    digests = {
        field: _parse_digest(value.get(field), label=f"staging selection binding {field}")
        for field in expected - {"run_id"}
    }
    for field in ("plan_digest", "config_digest", "viewer_profile_digest"):
        if digests[field] != body.get(field):
            raise ReviewerBundleError(f"staging selection binding is bound to another {field}")
    lock_digests = body.get("lock_digests")
    if not isinstance(lock_digests, Mapping) or not lock_digests:
        raise ReviewerBundleError("staging lock digests are malformed")
    for name, lock_digest in lock_digests.items():
        if not isinstance(name, str) or not name:
            raise ReviewerBundleError("staging lock digests have an invalid name")
        _parse_digest(lock_digest, label=f"staging lock digest {name}")
    if digests["lock_digests_digest"] != digest(dict(lock_digests)):
        raise ReviewerBundleError("staging selection binding lock digests changed")
    return _StagingSelectionBinding(run_id=run_id, **digests)


def _load_staging(path_value: str | Path) -> tuple[dict[str, Any], list[_StagedTask], _StagingSelectionBinding]:
    root = _local_directory(path_value, label="staging root")
    manifest_path = root / "manifest.json"
    manifest_value, _ = _read_json(manifest_path, label="staging manifest")
    try:
        manifest = verify_envelope(manifest_value, kind="release_staging_manifest")
    except InputError as exc:
        raise ReviewerBundleError(f"staging manifest failed attestation: {exc}") from exc
    if manifest.get("status") != "staged_not_exported":
        raise ReviewerBundleError("reviewer bundle requires staging status staged_not_exported")
    body = manifest.get("body")
    if not isinstance(body, Mapping):
        raise ReviewerBundleError("staging manifest body is malformed")
    required = {
        "run_id",
        "plan_digest",
        "config_digest",
        "viewer_profile_digest",
        "lock_digests",
        "fixture_only",
        "selection_binding",
        "staged",
    }
    if set(body) != required or body.get("fixture_only") is not False:
        raise ReviewerBundleError("reviewer bundle requires a non-fixture release staging manifest")
    if not isinstance(body.get("run_id"), str) or not body["run_id"]:
        raise ReviewerBundleError("staging manifest run_id is invalid")
    for field in ("plan_digest", "config_digest", "viewer_profile_digest"):
        _parse_digest(body.get(field), label=f"staging {field}")
    binding = _parse_staging_selection_binding(body.get("selection_binding"), body=body)
    staged = body.get("staged")
    if not isinstance(staged, list) or not staged:
        raise ReviewerBundleError("staging manifest has no staged tasks")
    loaded: list[_StagedTask] = []
    seen_task_ids: set[str] = set()
    seen_solver_files: set[str] = set()
    seen_scorer_files: set[str] = set()
    seen_audit_files: set[str] = set()
    seen_candidate_digests: set[str] = set()
    for index, entry in enumerate(staged):
        if not isinstance(entry, Mapping):
            raise ReviewerBundleError(f"staging manifest entry {index} is malformed")
        needed = {
            "candidate_digest",
            "review_digest",
            "batch_job_id",
            "selection_slot_digest",
            "solver_file",
            "scorer_file",
            "audit_file",
            "solver_artifact",
            "scorer_artifact",
        }
        if not needed.issubset(entry):
            raise ReviewerBundleError(f"staging manifest entry {index} is incomplete")
        candidate_digest = _parse_digest(entry.get("candidate_digest"), label="staged candidate digest")
        if candidate_digest in seen_candidate_digests:
            raise ReviewerBundleError("staging manifest repeats a candidate digest")
        seen_candidate_digests.add(candidate_digest)
        review_digest = _parse_digest(entry.get("review_digest"), label="staged review digest")
        batch_job_id = _safe_filename(entry.get("batch_job_id"), label="staged batch job id")
        selection_slot_digest = _parse_digest(entry.get("selection_slot_digest"), label="staged selection slot digest")
        solver_path = _stage_file(root, "solver", entry.get("solver_file"))
        scorer_path = _stage_file(root, "scorer", entry.get("scorer_file"))
        audit_path = _stage_file(root, "audit", entry.get("audit_file"))
        if (
            solver_path.name in seen_solver_files
            or scorer_path.name in seen_scorer_files
            or audit_path.name in seen_audit_files
        ):
            raise ReviewerBundleError("staging manifest repeats an artifact filename")
        seen_solver_files.add(solver_path.name)
        seen_scorer_files.add(scorer_path.name)
        seen_audit_files.add(audit_path.name)
        solver, solver_content = _read_json(solver_path, label="staged solver artifact")
        scorer, scorer_content = _read_json(scorer_path, label="staged scorer artifact")
        audit, _ = _read_json(audit_path, label="staged audit artifact")
        _descriptor(
            entry.get("solver_artifact"),
            category="solver",
            filename=solver_path.name,
            content=solver_content,
            parsed=solver,
        )
        _descriptor(
            entry.get("scorer_artifact"),
            category="scorer",
            filename=scorer_path.name,
            content=scorer_content,
            parsed=scorer,
        )
        _forbid_legacy_answer_keys(solver, label="staged solver artifact")
        _forbid_legacy_answer_keys(scorer, label="staged scorer artifact")
        _forbid_legacy_answer_keys(audit, label="staged audit artifact")
        _validate_solver_projection(solver)
        if solver.get("artifact_kind") != "solver_safe_export":
            raise ReviewerBundleError("staged solver artifact has the wrong kind")
        task_id = solver.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ReviewerBundleError("staged solver artifact has no task_id")
        if task_id in seen_task_ids:
            raise ReviewerBundleError("staging manifest repeats a solver task_id")
        seen_task_ids.add(task_id)
        request = solver.get("viewer_session_request")
        if not isinstance(request, Mapping) or request.get("viewer_profile_digest") != body.get("viewer_profile_digest"):
            raise ReviewerBundleError("staged solver projection is bound to another viewer profile")
        if scorer.get("artifact_kind") != "scorer_private_input" or scorer.get("candidate_digest") != candidate_digest:
            raise ReviewerBundleError("staged scorer artifact is not bound to its candidate")
        if audit.get("artifact_kind") != "release_audit_record" or audit.get("candidate_digest") != candidate_digest:
            raise ReviewerBundleError("staged audit artifact is not bound to its candidate")
        scorer_review_digest = _parse_digest(scorer.get("review_digest"), label="staged scorer review receipt")
        audit_review_digest = _parse_digest(audit.get("review_digest"), label="staged audit review receipt")
        if review_digest != scorer_review_digest or review_digest != audit_review_digest:
            raise ReviewerBundleError("staged review receipt differs between manifest, scorer, and audit")
        loaded.append(
            _StagedTask(
                task_id=task_id,
                solver=solver,
                scorer=scorer,
                audit=audit,
                scorer_filename=scorer_path.name,
                audit_filename=audit_path.name,
                candidate_digest=candidate_digest,
                review_digest=review_digest,
                batch_job_id=batch_job_id,
                selection_slot_digest=selection_slot_digest,
            )
        )
    return manifest, loaded, binding


def _validate_staging_batch_binding(
    *,
    batch_digest: str,
    batch_rows: list[_BatchRow],
    batch_plan: _BatchPlan,
    staging_binding: _StagingSelectionBinding,
    staged: list[_StagedTask],
) -> dict[str, set[str]]:
    """Require an attested batch-slot path before exposing any reviewer task."""
    if staging_binding.selection_batch_digest != batch_digest:
        raise ReviewerBundleError("staging manifest is bound to another selection batch")
    if staging_binding.batch_plan_digest != batch_plan.digest:
        raise ReviewerBundleError("staging manifest is bound to another selection batch plan")
    if staging_binding.batch_plan_lock_bundle_digest != batch_plan.lock_bundle_digest:
        raise ReviewerBundleError("staging manifest is bound to another batch plan lock bundle")
    rows_by_slot = {row.slot_digest: row for row in batch_rows}
    jobs_by_id = {job.job_id: job for job in batch_plan.jobs}
    seen_slots: set[str] = set()
    seen_jobs: set[str] = set()
    capabilities: dict[str, set[str]] = {}
    for item in staged:
        row = rows_by_slot.get(item.selection_slot_digest)
        job = jobs_by_id.get(item.batch_job_id)
        if row is None or job is None:
            raise ReviewerBundleError("staged reviewer task is not bound to a frozen selection slot and batch plan job")
        if item.selection_slot_digest in seen_slots or item.batch_job_id in seen_jobs:
            raise ReviewerBundleError("staging manifest repeats a selection slot or batch plan job")
        if job.slot_digest != row.slot_digest or job.target_leaf != row.target_leaf:
            raise ReviewerBundleError("staged reviewer task has a mismatched selection slot or batch plan job")
        if item.task_id != row.task_id:
            raise ReviewerBundleError("staged reviewer task task_id does not match its frozen selection slot")
        request = item.solver.get("viewer_session_request")
        solver_operations = request.get("allowed_operations") if isinstance(request, Mapping) else None
        if not isinstance(solver_operations, list) or set(solver_operations) != set(job.allowed_operations):
            raise ReviewerBundleError("staged solver operations are not exactly bound to the batch plan viewer lock")
        if job.generation_strategy == "preserve_intent" and not (
            set(row.allowed_operations) | {"render"}
        ).issubset(set(job.allowed_operations)):
            raise ReviewerBundleError("preserve_intent viewer lock does not retain the frozen source viewer policy")
        unsupported = sorted(set(job.allowed_operations) - _PUBLIC_OPERATIONS[row.viewer])
        if unsupported:
            raise ReviewerBundleError(
                f"reviewer viewer bridge lacks required {row.viewer} operation: {unsupported[0]}"
            )
        seen_slots.add(item.selection_slot_digest)
        seen_jobs.add(item.batch_job_id)
        capabilities.setdefault(row.viewer, set()).update(job.allowed_operations)
    return capabilities


def _regular_asset_path(asset_root: Path, relative: str) -> Path:
    if asset_root.is_symlink() or not asset_root.is_dir():
        raise ReviewerBundleError("asset root must be a regular local directory")
    current = asset_root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            status = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReviewerBundleError("selected asset is missing") from exc
        if current.is_symlink():
            raise ReviewerBundleError("selected asset path contains a symlink")
        if part != PurePosixPath(relative).parts[-1] and not stat.S_ISDIR(status.st_mode):
            raise ReviewerBundleError("selected asset path has a non-directory component")
    if not stat.S_ISREG(current.stat(follow_symlinks=False).st_mode):
        raise ReviewerBundleError("selected asset is not a regular file")
    try:
        current.resolve(strict=True).relative_to(asset_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ReviewerBundleError("selected asset escapes its asset root") from exc
    return current


def _verify_asset_source(asset_root: Path, asset: _Asset) -> Path:
    source = _regular_asset_path(asset_root, asset.relative_path)
    content = _read_regular_bytes(source, label="selected asset")
    if len(content) != asset.size:
        raise ReviewerBundleError("selected asset byte size does not match its attestation")
    if _raw_sha256(content) != asset.sha256:
        raise ReviewerBundleError("selected asset sha256 does not match its attestation")
    return source


def _copy_or_link(source: Path, destination: Path, *, hardlink: bool, private: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700 if private else 0o755)
    if destination.exists() or destination.is_symlink():
        raise ReviewerBundleError("refusing to overwrite a reviewer bundle artifact")
    # A private reviewer artifact must own a 0600 inode.  A hard link would
    # share its source file mode and can accidentally make it public-readable.
    if hardlink and not private:
        try:
            os.link(source, destination, follow_symlinks=False)
            return
        except OSError:
            pass
    content = _read_regular_bytes(source, label="bundle source artifact")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(destination, flags, 0o600 if private else 0o644)
    except OSError as exc:
        raise ReviewerBundleError("cannot write reviewer bundle artifact") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _write_json(path: Path, value: Mapping[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700 if private else 0o755)
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600 if private else 0o644)
    except OSError as exc:
        raise ReviewerBundleError(f"cannot write {path.name}") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _copy_static_file(resource_name: str, destination: Path) -> None:
    try:
        content = resources.files("qagen.reviewer_bundle").joinpath("static", resource_name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ReviewerBundleError(f"reviewer static resource is unavailable: {resource_name}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(content)
    os.chmod(destination, 0o644)


def _runtime_file(runtime_root: Path, relative: str) -> Path:
    path = runtime_root / relative
    if path.is_symlink() or not path.is_file():
        raise ReviewerBundleError(f"local viewer runtime is missing {relative}")
    return path


def _validate_runtime_capabilities(runtime_root: Path, capabilities: Mapping[str, set[str]]) -> None:
    """Verify the copied runtime contains each operation the bundle will advertise."""
    for viewer, operations in capabilities.items():
        markers = _RUNTIME_CAPABILITY_MARKERS.get(viewer)
        runtime_relative = _RUNTIME_SCRIPT_PATHS.get(viewer)
        if markers is None or runtime_relative is None:
            raise ReviewerBundleError("reviewer bundle has an unknown viewer runtime")
        runtime = _read_regular_bytes(_runtime_file(runtime_root, runtime_relative), label=f"{viewer} viewer runtime")
        for operation in sorted(operations):
            marker = markers.get(operation)
            if marker is None:
                raise ReviewerBundleError(f"reviewer viewer bridge lacks required {viewer} operation: {operation}")
            if marker.encode("ascii") not in runtime:
                raise ReviewerBundleError(f"local {viewer} runtime cannot attest required operation: {operation}")


def _copy_viewer_runtime(runtime_root: Path, public: Path) -> None:
    files = {
        "shared/vendor/molstar/4.5.0/build/viewer/molstar.js": "vendor/molstar/molstar.js",
        "shared/vendor/molstar/4.5.0/build/viewer/molstar.css": "vendor/molstar/molstar.css",
        "shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js": "vendor/niivue/niivue.umd.js",
    }
    for source_relative, destination_relative in files.items():
        source = _runtime_file(runtime_root, source_relative)
        destination = public / destination_relative
        _copy_or_link(source, destination, hardlink=False, private=False)


def _default_runtime_root() -> Path:
    # Source checkout: QA-Gen/qa_gen_pipeline/src/qagen/reviewer_bundle/bundle.py.
    candidate = Path(__file__).resolve().parents[5] / "viewer"
    if candidate.is_dir():
        return candidate
    raise ReviewerBundleError("viewer runtime root is required outside a ScienceVision checkout")


def _scene_ref(batch_digest: str, candidate_digest: str) -> str:
    material = f"{batch_digest}:{candidate_digest}".encode("ascii")
    return f"scene_{sha256(material).hexdigest()[:32]}"


def _asset_filename(role: str, asset: _Asset) -> str:
    return f"{role}.{asset.format}"


def _stage_private_artifact(destination: Path, value: Mapping[str, Any]) -> None:
    _write_json(destination, dict(value), private=True)


def _write_checksums(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_dir():
            if path.is_symlink():
                raise ReviewerBundleError("reviewer bundle contains a symlink directory")
            continue
        if path.is_symlink() or not path.is_file():
            raise ReviewerBundleError("reviewer bundle contains an unsafe artifact")
        relative = path.relative_to(root).as_posix()
        if relative == _CHECKSUM_NAME:
            continue
        entries[relative] = _raw_sha256(_read_regular_bytes(path, label="reviewer bundle artifact"))
    checksum_path = root / _CHECKSUM_NAME
    lines = [f"{checksum}  {relative}" for relative, checksum in sorted(entries.items())]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(checksum_path, 0o644)
    return entries


def _read_checksums(root: Path) -> dict[str, str]:
    path = root / _CHECKSUM_NAME
    content = _read_regular_bytes(path, label="SHA256SUMS")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReviewerBundleError("SHA256SUMS is not UTF-8") from exc
    if not lines:
        raise ReviewerBundleError("SHA256SUMS is empty")
    result: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if match is None:
            raise ReviewerBundleError("SHA256SUMS has an invalid line")
        checksum, relative = match.groups()
        relative = _safe_relative_path(relative, label="SHA256SUMS path")
        if relative == _CHECKSUM_NAME or relative in result:
            raise ReviewerBundleError("SHA256SUMS repeats or includes itself")
        result[relative] = checksum
    return result


def _validate_registry(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "schema_version", "scenes"}:
        raise ReviewerBundleError("scene registry has an invalid top-level contract")
    if value.get("kind") != "qagen_reviewer_scene_registry" or value.get("schema_version") != 1:
        raise ReviewerBundleError("scene registry kind or schema version is invalid")
    scenes = value.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ReviewerBundleError("scene registry must contain scenes")
    result: dict[str, dict[str, Any]] = {}
    for item in scenes:
        if not isinstance(item, Mapping):
            raise ReviewerBundleError("scene registry entry must be an object")
        allowed = {"scene_ref", "viewer", "format", "primary_url", "allowed_operations"}
        if set(item) != allowed:
            raise ReviewerBundleError("scene registry entry has an invalid contract")
        scene_ref = item.get("scene_ref")
        viewer = item.get("viewer")
        asset_format = item.get("format")
        if not isinstance(scene_ref, str) or not _SCENE_REF.fullmatch(scene_ref):
            raise ReviewerBundleError("scene registry has an invalid anonymous scene ref")
        if scene_ref in result or viewer not in _PUBLIC_OPERATIONS:
            raise ReviewerBundleError("scene registry repeats a scene ref or has an unsupported viewer")
        valid_formats = _MOLSTAR_FORMATS if viewer == "molstar" else _NIIVUE_FORMATS
        if asset_format not in valid_formats:
            raise ReviewerBundleError("scene registry has an invalid viewer format pairing")
        expected_primary = f"/scene/{scene_ref}/primary.{asset_format}"
        if item.get("primary_url") != expected_primary:
            raise ReviewerBundleError("scene registry primary URL is not anonymous and canonical")
        operations = item.get("allowed_operations")
        if (
            not isinstance(operations, list)
            or not operations
            or not all(isinstance(operation, str) and operation in _PUBLIC_OPERATIONS[viewer] for operation in operations)
            or len(set(operations)) != len(operations)
        ):
            raise ReviewerBundleError("scene registry allowed_operations are invalid")
        result[scene_ref] = dict(item)
    return result


def _validate_tasks(value: Any, registry: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "schema_version", "tasks"}:
        raise ReviewerBundleError("public tasks have an invalid top-level contract")
    if value.get("kind") != "qagen_reviewer_tasks" or value.get("schema_version") != 1:
        raise ReviewerBundleError("public task kind or schema version is invalid")
    tasks_value = value.get("tasks")
    if not isinstance(tasks_value, list) or not tasks_value:
        raise ReviewerBundleError("public tasks must be a non-empty list")
    tasks: list[dict[str, Any]] = []
    ids: set[str] = set()
    for value_item in tasks_value:
        if not isinstance(value_item, Mapping):
            raise ReviewerBundleError("public task must be an object")
        task = dict(value_item)
        _validate_solver_projection(task)
        task_id = task.get("task_id")
        scene_ref = task.get("anonymous_scene_ref")
        request = task.get("viewer_session_request")
        if not isinstance(task_id, str) or not task_id or task_id in ids:
            raise ReviewerBundleError("public task has a duplicate or invalid task_id")
        if not isinstance(scene_ref, str) or scene_ref not in registry:
            raise ReviewerBundleError("public task references an unavailable scene")
        if not isinstance(request, Mapping) or request.get("allowed_operations") != registry[scene_ref]["allowed_operations"]:
            raise ReviewerBundleError("public task operations do not bind to the scene registry")
        ids.add(task_id)
        tasks.append(task)
    return tasks


def _bundle_public_files(root: Path) -> set[str]:
    return {
        _BUNDLE_NAME,
        _REGISTRY_NAME,
        "public/index.html",
        "public/app.js",
        "public/style.css",
        "public/tasks.json",
        "public/vendor/molstar/molstar.js",
        "public/vendor/molstar/molstar.css",
        "public/vendor/niivue/niivue.umd.js",
        *(
            path.relative_to(root).as_posix()
            for path in (root / "scenes").rglob("*")
            if path.is_file() or path.is_symlink()
        ),
        *(
            path.relative_to(root).as_posix()
            for path in (root / "private").rglob("*")
            if path.is_file() or path.is_symlink()
        ),
    }


def _assert_no_public_mask_artifact(root: Path) -> None:
    """A solver surface may never carry a mask, overlay, or mask metadata file."""

    for base in (root / "public", root / "scenes"):
        for path in base.rglob("*"):
            if path.is_symlink():
                raise ReviewerBundleError("reviewer public surface contains a symlink")
            if path.is_file() and re.search(r"(?:^|[._-])(mask|overlay)(?:[._-]|$)", path.name, re.IGNORECASE):
                raise ReviewerBundleError("reviewer public surface contains a forbidden mask or overlay artifact")


def verify_reviewer_bundle(bundle_root: str | Path) -> dict[str, Any]:
    """Verify all local bytes and the narrow public/HTTP-safe manifest surface."""
    root = _local_directory(bundle_root, label="reviewer bundle root")
    required_directories = (
        root / "public",
        root / "scenes",
        root / "private" / "scorer",
        root / "private" / "audit",
        root / "private" / "reviewer-evidence",
    )
    if any(path.is_symlink() or not path.is_dir() for path in required_directories):
        raise ReviewerBundleError("reviewer bundle directory layout is incomplete or unsafe")
    checksums = _read_checksums(root)
    _assert_no_public_mask_artifact(root)
    actual = _bundle_public_files(root)
    if set(checksums) != actual:
        raise ReviewerBundleError("SHA256SUMS file set does not match the reviewer bundle")
    for relative, expected in checksums.items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ReviewerBundleError(f"reviewer bundle checksum target is unsafe: {relative}")
        if _raw_sha256(_read_regular_bytes(path, label="reviewer bundle checksum target")) != expected:
            raise ReviewerBundleError(f"reviewer bundle checksum mismatch: {relative}")
    bundle, _ = _read_json(root / _BUNDLE_NAME, label="reviewer bundle manifest")
    required_bundle = {
        "kind",
        "schema_version",
        "status",
        "bundle_id",
        "source",
        "counts",
        "viewer_runtimes",
        "loopback_host",
        "release_eligible",
    }
    if set(bundle) != required_bundle or bundle.get("kind") != "qagen_reviewer_local_bundle" or bundle.get("schema_version") != 1:
        raise ReviewerBundleError("reviewer bundle manifest contract is invalid")
    if bundle.get("status") != "local_review_only" or bundle.get("loopback_host") != "127.0.0.1" or bundle.get("release_eligible") is not False:
        raise ReviewerBundleError("reviewer bundle manifest has an invalid publication status")
    if not isinstance(bundle.get("bundle_id"), str) or not _SAFE_FILENAME.fullmatch(bundle["bundle_id"]):
        raise ReviewerBundleError("reviewer bundle id is invalid")
    source = bundle.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "selection_id_sha256",
        "selection_file_sha256",
        "batch_manifest_digest",
        "batch_plan_digest",
        "batch_plan_lock_bundle_digest",
        "staging_manifest_digest",
    }:
        raise ReviewerBundleError("reviewer bundle source locks are malformed")
    _parse_digest(source.get("selection_id_sha256"), label="reviewer selection id sha256", raw=True)
    for field in (
        "selection_file_sha256",
        "batch_manifest_digest",
        "batch_plan_digest",
        "batch_plan_lock_bundle_digest",
        "staging_manifest_digest",
    ):
        _parse_digest(source.get(field), label=f"reviewer {field}")
    _assert_public_keys_safe(bundle, label="reviewer bundle manifest")
    registry_value, _ = _read_json(root / _REGISTRY_NAME, label="scene registry")
    _forbid_legacy_answer_keys(registry_value, label="scene registry")
    registry = _validate_registry(registry_value)
    tasks_value, _ = _read_json(root / "public" / _TASKS_NAME, label="public tasks")
    tasks = _validate_tasks(tasks_value, registry)
    counts = bundle.get("counts")
    expected_count_keys = {
        "selected_count",
        "task_count",
        "scene_count",
        "private_scorer_count",
        "private_audit_count",
        "private_reviewer_evidence_count",
        "partial",
    }
    if (
        not isinstance(counts, Mapping)
        or set(counts) != expected_count_keys
        or not all(
            isinstance(counts.get(field), int) and not isinstance(counts.get(field), bool) and counts[field] >= 0
            for field in expected_count_keys - {"partial"}
        )
        or not isinstance(counts.get("partial"), bool)
        or counts.get("task_count") != len(tasks)
        or counts.get("scene_count") != len(registry)
        or counts.get("selected_count", 0) < len(tasks)
        or counts.get("partial") != (counts.get("selected_count") != len(tasks))
    ):
        raise ReviewerBundleError("reviewer bundle manifest counts do not match public artifacts")
    if bundle.get("viewer_runtimes") != ["molstar-4.5.0", "niivue-0.45.0"]:
        raise ReviewerBundleError("reviewer bundle has an unexpected viewer runtime declaration")
    scorer_paths = sorted((root / "private" / "scorer").iterdir(), key=lambda path: path.name)
    audit_paths = sorted((root / "private" / "audit").iterdir(), key=lambda path: path.name)
    if (
        counts.get("private_scorer_count") != len(scorer_paths)
        or counts.get("private_audit_count") != len(audit_paths)
        or len(scorer_paths) != len(tasks)
        or len(audit_paths) != len(tasks)
    ):
        raise ReviewerBundleError("reviewer bundle private artifact counts do not match staged tasks")
    for scene_ref, scene in registry.items():
        primary = root / scene["primary_url"].lstrip("/")
        if not primary.is_file() or primary.is_symlink():
            # URLs map to the controlled route, while bundle files live below scenes.
            primary = root / "scenes" / scene_ref / f"primary.{scene['format']}"
        if not primary.is_file() or primary.is_symlink():
            raise ReviewerBundleError("scene registry primary asset is unavailable")
    # Every copied private artifact remains present but never becomes an HTTP route.
    private_receipts: dict[str, dict[str, str]] = {"scorer": {}, "audit": {}}
    for category, directory, kind in (
        ("scorer", root / "private" / "scorer", "scorer_private_input"),
        ("audit", root / "private" / "audit", "release_audit_record"),
    ):
        try:
            if stat.S_IMODE(directory.stat(follow_symlinks=False).st_mode) != 0o700:
                raise ReviewerBundleError("reviewer private directory permissions must be 0700")
        except OSError as exc:
            raise ReviewerBundleError("cannot inspect reviewer private directory permissions") from exc
        for path in directory.iterdir():
            try:
                if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o600:
                    raise ReviewerBundleError("reviewer private artifact permissions must be 0600")
            except OSError as exc:
                raise ReviewerBundleError("cannot inspect reviewer private artifact permissions") from exc
            value, _ = _read_json(path, label="private reviewer artifact")
            _forbid_legacy_answer_keys(value, label="private reviewer artifact")
            if value.get("artifact_kind") != kind:
                raise ReviewerBundleError("private reviewer artifact kind is invalid")
            candidate_digest = _parse_digest(value.get("candidate_digest"), label="private reviewer candidate digest")
            review_digest = _parse_digest(value.get("review_digest"), label="private reviewer review receipt")
            if candidate_digest in private_receipts[category]:
                raise ReviewerBundleError("reviewer private artifacts repeat a candidate digest")
            private_receipts[category][candidate_digest] = review_digest
    if set(private_receipts["scorer"]) != set(private_receipts["audit"]):
        raise ReviewerBundleError("reviewer scorer and audit artifacts bind different candidates")
    if any(
        private_receipts["scorer"][candidate_digest] != private_receipts["audit"][candidate_digest]
        for candidate_digest in private_receipts["scorer"]
    ):
        raise ReviewerBundleError("reviewer scorer and audit artifacts bind different review receipts")
    evidence_root = root / "private" / "reviewer-evidence"
    try:
        if stat.S_IMODE(evidence_root.stat(follow_symlinks=False).st_mode) != 0o700:
            raise ReviewerBundleError("reviewer private directory permissions must be 0700")
    except OSError as exc:
        raise ReviewerBundleError("cannot inspect reviewer private directory permissions") from exc
    evidence_dirs = sorted(evidence_root.iterdir(), key=lambda path: path.name)
    if counts.get("private_reviewer_evidence_count") != len(evidence_dirs):
        raise ReviewerBundleError("reviewer private evidence count does not match staged masks")
    for directory in evidence_dirs:
        if directory.is_symlink() or not directory.is_dir() or directory.name not in registry:
            raise ReviewerBundleError("reviewer private mask evidence directory is invalid")
        try:
            if stat.S_IMODE(directory.stat(follow_symlinks=False).st_mode) != 0o700:
                raise ReviewerBundleError("reviewer private mask evidence permissions must be 0700")
        except OSError as exc:
            raise ReviewerBundleError("cannot inspect reviewer private mask evidence permissions") from exc
        files = sorted(directory.iterdir(), key=lambda path: path.name)
        if len(files) != 2 or any(path.is_symlink() or not path.is_file() for path in files):
            raise ReviewerBundleError("reviewer private mask evidence files are invalid")
        reference_path = directory / "reference.json"
        if not reference_path.is_file():
            raise ReviewerBundleError("reviewer private mask evidence has no reference record")
        reference, _ = _read_json(reference_path, label="private reviewer mask reference")
        if (
            set(reference) != {"kind", "scene_ref", "mask_filename", "mask_sha256", "mask_selection", "mask_id"}
            or reference.get("kind") != "qagen_private_reviewer_mask_reference"
            or reference.get("scene_ref") != directory.name
            or not isinstance(reference.get("mask_filename"), str)
            or not _SAFE_FILENAME.fullmatch(reference["mask_filename"])
            or not reference["mask_filename"].endswith(".nii.gz")
            or not isinstance(reference.get("mask_sha256"), str)
            or not _SHA256.fullmatch(reference["mask_sha256"])
            or reference.get("mask_selection") != "label_index"
            or not isinstance(reference.get("mask_id"), int)
            or isinstance(reference.get("mask_id"), bool)
            or reference["mask_id"] < 0
        ):
            raise ReviewerBundleError("reviewer private mask reference is invalid")
        mask_path = directory / reference["mask_filename"]
        if not mask_path.is_file() or mask_path.is_symlink():
            raise ReviewerBundleError("reviewer private mask evidence is missing")
        if _raw_sha256(_read_regular_bytes(mask_path, label="private reviewer mask evidence")) != reference["mask_sha256"]:
            raise ReviewerBundleError("reviewer private mask evidence checksum mismatch")
        for path in files:
            try:
                if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o600:
                    raise ReviewerBundleError("reviewer private mask evidence permissions must be 0600")
            except OSError as exc:
                raise ReviewerBundleError("cannot inspect reviewer private mask evidence permissions") from exc
    return {
        "bundle_root": str(root),
        "bundle_id": bundle["bundle_id"],
        "task_count": len(tasks),
        "scene_count": len(registry),
        "checksum_count": len(checksums),
        "registry": registry,
        "checksums": checksums,
    }


def build_reviewer_bundle(
    *,
    batch_manifest_path: str | Path,
    batch_plan_path: str | Path,
    staging_root: str | Path,
    asset_root: str | Path,
    output_dir: str | Path,
    viewer_runtime_root: str | Path | None = None,
    hardlink_assets: bool = True,
    allow_partial: bool = False,
) -> dict[str, Any]:
    """Materialize a checksum-locked reviewer package from attested inputs.

    ``allow_partial`` is deliberately opt-in.  The default requires one staged
    generated task for every selected batch row, which is the only mode that
    can claim to represent the frozen 60-question selection.
    """
    batch, batch_rows = _load_batch(batch_manifest_path)
    batch_digest = _parse_digest(batch.get("content_digest"), label="selection batch manifest digest")
    batch_plan = _load_batch_plan(batch_plan_path, batch_digest=batch_digest, batch_rows=batch_rows)
    staging, staged, staging_binding = _load_staging(staging_root)
    runtime_capabilities = _validate_staging_batch_binding(
        batch_digest=batch_digest,
        batch_rows=batch_rows,
        batch_plan=batch_plan,
        staging_binding=staging_binding,
        staged=staged,
    )
    rows_by_task = {row.task_id: row for row in batch_rows}
    rows_by_slot = {row.slot_digest: row for row in batch_rows}
    staged_by_task = {item.task_id: item for item in staged}
    unknown = sorted(set(staged_by_task) - set(rows_by_task))
    if unknown:
        raise ReviewerBundleError("staged solver task is not part of the frozen selection batch")
    missing = sorted(set(rows_by_task) - set(staged_by_task))
    if missing and not allow_partial:
        raise ReviewerBundleError("frozen selection batch is incomplete; use an explicit partial-review mode")
    if not staged:
        raise ReviewerBundleError("reviewer bundle needs at least one staged task")
    output = Path(output_dir).expanduser()
    if output.exists() or output.is_symlink():
        raise ReviewerBundleError("refusing to overwrite an existing reviewer bundle")
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    source_root = _local_directory(asset_root, label="asset root")
    runtime_root = (
        _local_directory(viewer_runtime_root, label="viewer runtime root")
        if viewer_runtime_root is not None
        else _default_runtime_root()
    )
    stage_digest = _parse_digest(staging.get("content_digest"), label="staging manifest digest")
    _validate_runtime_capabilities(runtime_root, runtime_capabilities)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output_parent))
    try:
        public = stage / "public"
        scenes_root = stage / "scenes"
        private_scorer = stage / "private" / "scorer"
        private_audit = stage / "private" / "audit"
        private_reviewer_evidence = stage / "private" / "reviewer-evidence"
        public.mkdir(parents=True)
        scenes_root.mkdir(parents=True)
        private_scorer.mkdir(parents=True)
        private_audit.mkdir(parents=True)
        private_reviewer_evidence.mkdir(parents=True)
        os.chmod(public, 0o755)
        os.chmod(scenes_root, 0o755)
        os.chmod(stage / "private", 0o700)
        os.chmod(private_scorer, 0o700)
        os.chmod(private_audit, 0o700)
        os.chmod(private_reviewer_evidence, 0o700)
        for name in ("index.html", "app.js", "style.css"):
            _copy_static_file(name, public / name)
        _copy_viewer_runtime(runtime_root, public)

        public_tasks: list[dict[str, Any]] = []
        scenes: list[dict[str, Any]] = []
        for staged_task in sorted(staged, key=lambda item: item.task_id):
            row = rows_by_slot[staged_task.selection_slot_digest]
            request = staged_task.solver.get("viewer_session_request")
            if not isinstance(request, Mapping) or not isinstance(request.get("allowed_operations"), list):
                raise ReviewerBundleError("staged solver task has malformed viewer operations")
            operations = request["allowed_operations"]
            if (
                not operations
                or not all(isinstance(item, str) and item in _PUBLIC_OPERATIONS[row.viewer] for item in operations)
                or len(set(operations)) != len(operations)
            ):
                raise ReviewerBundleError("staged solver task requests unsupported reviewer viewer operations")
            scene_ref = _scene_ref(batch_digest, staged_task.candidate_digest)
            scene_dir = scenes_root / scene_ref
            scene_dir.mkdir(mode=0o755)
            primary_source = _verify_asset_source(source_root, row.primary)
            primary_name = _asset_filename("primary", row.primary)
            _copy_or_link(primary_source, scene_dir / primary_name, hardlink=hardlink_assets, private=False)
            if row.mask is not None:
                mask_source = _verify_asset_source(source_root, row.mask)
                private_scene = private_reviewer_evidence / scene_ref
                private_scene.mkdir(mode=0o700)
                os.chmod(private_scene, 0o700)
                private_mask_name = _asset_filename("mask", row.mask)
                _copy_or_link(mask_source, private_scene / private_mask_name, hardlink=False, private=True)
                _stage_private_artifact(
                    private_scene / "reference.json",
                    {
                        "kind": "qagen_private_reviewer_mask_reference",
                        "scene_ref": scene_ref,
                        "mask_filename": private_mask_name,
                        "mask_sha256": row.mask.sha256,
                        "mask_selection": row.mask.mask_selection,
                        "mask_id": row.mask.mask_id,
                    },
                )
            rebased_solver = dict(staged_task.solver)
            rebased_solver["anonymous_scene_ref"] = scene_ref
            _forbid_legacy_answer_keys(rebased_solver, label="rebased public task")
            _assert_public_keys_safe(rebased_solver, label="rebased public task")
            public_tasks.append(rebased_solver)
            scenes.append(
                {
                    "scene_ref": scene_ref,
                    "viewer": row.viewer,
                    "format": row.primary.format,
                    "primary_url": f"/scene/{scene_ref}/{primary_name}",
                    "allowed_operations": list(operations),
                }
            )
            _stage_private_artifact(private_scorer / staged_task.scorer_filename, staged_task.scorer)
            _stage_private_artifact(private_audit / staged_task.audit_filename, staged_task.audit)

        tasks_artifact = {"kind": "qagen_reviewer_tasks", "schema_version": 1, "tasks": public_tasks}
        registry_artifact = {"kind": "qagen_reviewer_scene_registry", "schema_version": 1, "scenes": scenes}
        _write_json(public / _TASKS_NAME, tasks_artifact)
        _write_json(stage / _REGISTRY_NAME, registry_artifact)
        source = batch["body"]["source_selection"]
        bundle_id = f"reviewer-{source['selection_id_sha256'][:16]}-{batch_digest.split(':', 1)[1][:12]}"
        bundle_artifact = {
            "kind": "qagen_reviewer_local_bundle",
            "schema_version": 1,
            "status": "local_review_only",
            "bundle_id": bundle_id,
            "source": {
                "selection_id_sha256": source["selection_id_sha256"],
                "selection_file_sha256": source["selection_file_sha256"],
                "batch_manifest_digest": batch_digest,
                "batch_plan_digest": batch_plan.digest,
                "batch_plan_lock_bundle_digest": batch_plan.lock_bundle_digest,
                "staging_manifest_digest": stage_digest,
            },
            "counts": {
                "selected_count": len(batch_rows),
                "task_count": len(public_tasks),
                "scene_count": len(scenes),
                "private_scorer_count": len(public_tasks),
                "private_audit_count": len(public_tasks),
                "private_reviewer_evidence_count": sum(
                    1 for staged_task in staged if rows_by_slot[staged_task.selection_slot_digest].mask is not None
                ),
                "partial": bool(missing),
            },
            "viewer_runtimes": ["molstar-4.5.0", "niivue-0.45.0"],
            "loopback_host": "127.0.0.1",
            "release_eligible": False,
        }
        _write_json(stage / _BUNDLE_NAME, bundle_artifact)
        _write_checksums(stage)
        verification = verify_reviewer_bundle(stage)
        os.replace(stage, output)
        return {
            "status": "built_local_review_only",
            "bundle_dir": str(output),
            "bundle_id": bundle_id,
            "task_count": len(public_tasks),
            "selected_count": len(batch_rows),
            "partial": bool(missing),
            "scene_count": len(scenes),
            "checksum_count": verification["checksum_count"],
            "loopback_host": "127.0.0.1",
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
