"""Private scene registry and sanitized replay receipts for frozen batches.

This module deliberately stops at the host boundary.  It verifies the frozen
selection batch and its external assets, exposes verified bytes only to a
controlled local host, and persists digest-only replay receipts.  It does not
create a candidate, call a model, or integrate with the chirality-only live
executor.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from multiprocessing import get_context
import os
from pathlib import Path, PurePosixPath
from queue import Empty
import re
import stat
from typing import Any

from jsonschema import Draft7Validator

from ..contracts import digest, digest_bytes, make_envelope, require_digest, utc_now, verify_envelope
from ..errors import InputError
from ..paths import WorkspacePaths, require_within
from ..resource_paths import PackageResourceError, config_path
from ..sources.safe_fs import SafeFilesystemError, TrustedSourceRoot, lock_trusted_source_root
from ..storage.atomic import atomic_write_json, read_json
from .contracts import validate_anonymous_scene_ref


_REGISTRY_KIND = "qagen_private_batch_scene_registry"
_SLOT_RECEIPT_KIND = "qagen_batch_scene_replay_receipt"
_SUMMARY_KIND = "qagen_batch_scene_replay_summary"
_RETRY_RECORD_KIND = "qagen_batch_scene_replay_retry_record"
_LOCK_SET_KIND = "qagen_private_batch_scene_viewer_lock_set"
_SELECTION_BATCH_KIND = "vnext_selection_batch_manifest"
_MAX_SELECTION_BATCH_BYTES = 8 * 1024 * 1024
_MAX_CAPTURE_BYTES = 128 * 1024 * 1024
_REGISTRY_DIRECTORY = ("viewer", "batch-scene-registries")
_RECEIPT_DIRECTORY = ("viewer", "batch-scene-receipts")
_RETRY_DIRECTORY = ("viewer", "batch-scene-retries")
_LOCK_SET_DIRECTORY = ("viewer", "batch-scene-lock-sets")
_SCENE_REF_VERSION = "qagen.batch_scene_ref.v1"
_REPLAY_RESULT_VERSION = 1
_RETRY_RECORD_VERSION = 1
_STABLE_CAPTURE_REPAIR_REASON = "stable_capture_after_scene_ready"
_MOLSTAR_CONTRAST_REPAIR_REASON = "molstar_polymer_and_ligand_contrast_after_focus"
_ISOLATED_OPERATION_REPAIR_REASON = "fresh_host_per_operation_after_operation_failure"
_FRESH_BROWSER_REPAIR_REASON = "fresh_browser_extended_ready_wait"
_WATCHDOG_SHUTDOWN_GRACE_SECONDS = 5.0
_MAX_VIEWER_PROVENANCE_BYTES = 4 * 1024 * 1024
_TRUSTED_V32_VIEWER_BUNDLE_ROOT = Path(
    "/Users/sue/Research/ScienceVision-local-packages/scale-v3.2-bcm522-question-assets-bundle-20260826-v1.2.1"
)
_TRUSTED_V32_VIEWER_BUNDLE_PACKAGE_ID = "scale-v3.2-bcm522-question-assets-bundle-20260826-v1.2.1"
_TRUSTED_V32_VIEWER_BUNDLE_MANIFEST_DIGEST = "sha256:506485c799d27528b2c35c2d38e5dfacddf724415da603a65473d0f883739021"
_BUNDLE_MANIFEST = "MANIFEST.json"
_BUNDLE_VIEWER_PROVENANCE = "provenance/viewer-files.json"
_REQUIRED_BUNDLE_VIEWER_FILES = (
    "viewer/molstar.html",
    "viewer/niivue.html",
    "viewer/shared/viewer-mcp-api.js",
    "viewer/shared/molstar-mcp-adapter.js",
    "viewer/shared/niivue-mcp-adapter.js",
)
_REPAIR_PRIOR_BLOCKERS = {
    _STABLE_CAPTURE_REPAIR_REASON: frozenset({frozenset({"viewer_render_invalid"})}),
    _MOLSTAR_CONTRAST_REPAIR_REASON: frozenset(
        {frozenset({"viewer_render_invalid"}), frozenset({"viewer_operation_failed"})}
    ),
    _ISOLATED_OPERATION_REPAIR_REASON: frozenset({frozenset({"viewer_operation_failed"})}),
    _FRESH_BROWSER_REPAIR_REASON: frozenset({frozenset({"viewer_render_invalid"})}),
}
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HOST_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,95}$")


class BatchSceneReplayError(InputError):
    """A private batch scene cannot be prepared or replayed safely."""

    def __init__(self, code: str) -> None:
        if code not in REPLAY_BLOCKER_CODES:
            code = "host_contract_invalid"
        self.code = code
        super().__init__(code)


# These are the only codes that may cross from the private host to a receipt.
# They are intentionally categorical, so neither a filesystem path nor a
# browser/library exception can become an accidental public artifact.
REPLAY_BLOCKER_CODES = frozenset(
    {
        "asset_capture_limit_exceeded",
        "asset_integrity_mismatch",
        "asset_unavailable",
        "host_contract_invalid",
        "unsupported_measure_area",
        "unsupported_measure_dihedral",
        "unsupported_measure_distance",
        "unsupported_measure_volume",
        "viewer_host_unavailable",
        "viewer_operation_failed",
        "viewer_operation_not_allowed",
        "viewer_page_load_failed",
        "viewer_render_invalid",
        "viewer_runtime_unavailable",
    }
)

_MOLSTAR_CIF_OPERATIONS = (
    "inspect",
    "isolate",
    "rotate",
    "zoom",
    "render",
    "measure_distance",
    "measure_dihedral",
)
_MOLSTAR_XYZ_OPERATIONS = (
    "inspect",
    "rotate",
    "zoom",
    "render",
    "measure_distance",
    "measure_dihedral",
)
_NIIVUE_OPERATIONS = ("inspect", "rotate", "render", "slice", "window_level", "bbox", "overlay_compare")
_NIIVUE_UNSUPPORTED_OPERATIONS = ("measure_distance", "measure_area", "measure_volume")


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _private_directory(paths: WorkspacePaths, parts: tuple[str, ...]) -> Path:
    directory = paths.private.joinpath(*parts)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def _private_file(path: Path, label: str) -> None:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise BatchSceneReplayError("asset_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BatchSceneReplayError("asset_unavailable")


def _schema(name: str) -> dict[str, Any]:
    try:
        value = read_json(config_path("schemas", name))
    except PackageResourceError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if not isinstance(value, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    return dict(value)


def _validate_schema(value: Any, schema_name: str) -> None:
    errors = sorted(Draft7Validator(_schema(schema_name)).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        raise BatchSceneReplayError("host_contract_invalid")


def _canonical_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BatchSceneReplayError("host_contract_invalid")
    path = PurePosixPath(value)
    parts = value.split("/")
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise BatchSceneReplayError("host_contract_invalid")
    if path.as_posix() != value:
        raise BatchSceneReplayError("host_contract_invalid")
    return value


def _strict_absolute_directory(value: str | Path) -> Path:
    raw = Path(value)
    if not raw.is_absolute():
        raise BatchSceneReplayError("host_contract_invalid")
    current = Path(raw.anchor)
    try:
        for part in raw.parts[1:]:
            current = current / part
            if current.is_symlink():
                raise BatchSceneReplayError("host_contract_invalid")
        resolved = raw.resolve(strict=True)
        metadata = resolved.stat()
    except BatchSceneReplayError:
        raise
    except OSError as exc:
        raise BatchSceneReplayError("asset_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise BatchSceneReplayError("host_contract_invalid")
    return resolved


def _regular_bundle_file(root: Path, relative: str) -> Path:
    """Resolve one named bundle file without accepting symlink substitution."""

    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise BatchSceneReplayError("host_contract_invalid")
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise BatchSceneReplayError("host_contract_invalid")
    candidate = root.joinpath(*parts)
    current = root
    try:
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise BatchSceneReplayError("host_contract_invalid")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        metadata = resolved.stat()
    except BatchSceneReplayError:
        raise
    except (OSError, ValueError) as exc:
        raise BatchSceneReplayError("asset_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise BatchSceneReplayError("host_contract_invalid")
    return resolved


def _load_bundle_json(root: Path, relative: str) -> tuple[dict[str, Any], str]:
    path = _regular_bundle_file(root, relative)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BatchSceneReplayError("asset_unavailable") from exc
    if not raw or len(raw) > _MAX_VIEWER_PROVENANCE_BYTES:
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if not isinstance(value, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    return dict(value), digest_bytes(raw)


def inspect_viewer_bundle_provenance(viewer_package_root: str | Path) -> dict[str, Any]:
    """Return a replay-host binding for one shipped viewer bundle.

    This is deliberately stricter than checking for a browser HTML file.  A
    live handoff must bind the exact package root, manifest, provenance record,
    checksum file, and the two family adapters it actually loads.  It never
    searches a repository or borrows assets from another worktree.
    """

    root = _strict_absolute_directory(viewer_package_root)
    trusted_root = _strict_absolute_directory(_TRUSTED_V32_VIEWER_BUNDLE_ROOT)
    if root != trusted_root:
        raise BatchSceneReplayError("host_contract_invalid")
    manifest, manifest_digest = _load_bundle_json(root, _BUNDLE_MANIFEST)
    provenance, provenance_digest = _load_bundle_json(root, _BUNDLE_VIEWER_PROVENANCE)
    package_id = manifest.get("package_id")
    if (
        not isinstance(package_id, str)
        or package_id != _TRUSTED_V32_VIEWER_BUNDLE_PACKAGE_ID
        or package_id != root.name
        or manifest_digest != _TRUSTED_V32_VIEWER_BUNDLE_MANIFEST_DIGEST
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    if provenance.get("package_id") != package_id:
        raise BatchSceneReplayError("host_contract_invalid")
    files = provenance.get("files")
    if not isinstance(files, list):
        raise BatchSceneReplayError("host_contract_invalid")
    declared: dict[str, str] = {}
    for item in files:
        if not isinstance(item, Mapping):
            raise BatchSceneReplayError("host_contract_invalid")
        relative = item.get("relative_path")
        shipped = item.get("sha256_shipped")
        if not isinstance(relative, str) or not isinstance(shipped, str):
            raise BatchSceneReplayError("host_contract_invalid")
        if relative in declared or len(shipped) != 64 or any(char not in "0123456789abcdef" for char in shipped):
            raise BatchSceneReplayError("host_contract_invalid")
        declared[relative] = shipped
    required_files: list[dict[str, str]] = []
    for relative in _REQUIRED_BUNDLE_VIEWER_FILES:
        expected = declared.get(relative)
        if expected is None:
            raise BatchSceneReplayError("host_contract_invalid")
        path = _regular_bundle_file(root, relative)
        try:
            actual = digest_bytes(path.read_bytes()).removeprefix("sha256:")
        except OSError as exc:
            raise BatchSceneReplayError("asset_unavailable") from exc
        if actual != expected:
            raise BatchSceneReplayError("asset_integrity_mismatch")
        required_files.append({"path": relative, "content_digest": f"sha256:{actual}"})
    return {
        "bundle_version": 1,
        "root": str(root),
        "package_id": package_id,
        "manifest_digest": manifest_digest,
        "viewer_provenance_digest": provenance_digest,
        "required_files": required_files,
    }


def validate_viewer_bundle_provenance(value: Any) -> dict[str, Any]:
    """Re-open and re-attest a previously frozen viewer-bundle binding."""

    expected_fields = {
        "bundle_version",
        "root",
        "package_id",
        "manifest_digest",
        "viewer_provenance_digest",
        "required_files",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields or value.get("bundle_version") != 1:
        raise BatchSceneReplayError("host_contract_invalid")
    root = value.get("root")
    if not isinstance(root, str) or not root:
        raise BatchSceneReplayError("host_contract_invalid")
    checked = inspect_viewer_bundle_provenance(root)
    if checked != dict(value):
        raise BatchSceneReplayError("asset_integrity_mismatch")
    return checked


def _load_selection_batch(paths: WorkspacePaths, value: str | Path) -> tuple[Path, dict[str, Any], str]:
    raw = Path(value)
    if raw.is_symlink():
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        path = require_within(paths.private, raw, "selection batch")
        _private_file(path, "selection batch")
        content = path.read_bytes()
    except BatchSceneReplayError:
        raise
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if len(content) > _MAX_SELECTION_BATCH_BYTES:
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        value_json = read_json(path)
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if not isinstance(value_json, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        envelope = verify_envelope(value_json, kind=_SELECTION_BATCH_KIND)
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    _validate_schema(envelope["body"], "selection_batch_manifest.schema.json")
    return path, envelope, digest_bytes(content)


def _validate_rows(envelope: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = envelope.get("body")
    if not isinstance(body, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    rows = body.get("rows")
    if not isinstance(rows, list) or body.get("total") != len(rows):
        raise BatchSceneReplayError("host_contract_invalid")
    seen_slots: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise BatchSceneReplayError("host_contract_invalid")
        row = dict(raw)
        slot_ref = row.get("slot_ref")
        assets = row.get("assets")
        hint = row.get("viewer_hint")
        if not isinstance(slot_ref, Mapping) or not isinstance(assets, list) or not isinstance(hint, Mapping):
            raise BatchSceneReplayError("host_contract_invalid")
        slot_digest = slot_ref.get("content_digest")
        try:
            require_digest(slot_digest, "selection batch slot digest")
        except InputError as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
        if not isinstance(slot_digest, str) or slot_digest in seen_slots:
            raise BatchSceneReplayError("host_contract_invalid")
        seen_slots.add(slot_digest)
        if row.get("mapping_status") != "mapped" or not isinstance(row.get("target_leaf"), str):
            raise BatchSceneReplayError("host_contract_invalid")
        if not isinstance(row.get("generation_strategy"), str) or not isinstance(row.get("access_track"), Mapping):
            raise BatchSceneReplayError("host_contract_invalid")
        if hint.get("source_viewer") not in {"molstar_deployed", "niivue_deployed"}:
            raise BatchSceneReplayError("host_contract_invalid")
        roles: set[str] = set()
        for asset in assets:
            if not isinstance(asset, Mapping):
                raise BatchSceneReplayError("host_contract_invalid")
            role = asset.get("role")
            if role not in {"primary", "mask"} or role in roles:
                raise BatchSceneReplayError("host_contract_invalid")
            roles.add(str(role))
            _canonical_relative_path(asset.get("path"))
            if (
                not isinstance(asset.get("bytes"), int)
                or isinstance(asset.get("bytes"), bool)
                or asset["bytes"] <= 0
                or not isinstance(asset.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
            ):
                raise BatchSceneReplayError("host_contract_invalid")
        if "primary" not in roles or (hint["source_viewer"] == "niivue_deployed" and roles != {"primary", "mask"}):
            raise BatchSceneReplayError("host_contract_invalid")
        if hint["source_viewer"] == "molstar_deployed" and roles != {"primary"}:
            raise BatchSceneReplayError("host_contract_invalid")
        result.append(row)
    return sorted(result, key=lambda row: str(row["slot_ref"]["content_digest"]))


def _viewer_family(row: Mapping[str, Any]) -> str:
    source_viewer = row["viewer_hint"]["source_viewer"]
    if source_viewer == "molstar_deployed":
        return "molstar"
    if source_viewer == "niivue_deployed":
        return "niivue"
    raise BatchSceneReplayError("host_contract_invalid")


def _asset_format(path: str, viewer_family: str) -> str:
    lower = path.lower()
    if viewer_family == "molstar":
        if lower.endswith((".cif", ".mmcif")):
            return "cif"
        if lower.endswith(".xyz"):
            return "xyz"
    elif viewer_family == "niivue" and lower.endswith((".nii", ".nii.gz")):
        return "nii.gz" if lower.endswith(".nii.gz") else "nii"
    raise BatchSceneReplayError("host_contract_invalid")


def _asset_set_digest(assets: Sequence[Mapping[str, Any]]) -> str:
    facts = [
        {"role": asset["role"], "bytes": asset["bytes"], "sha256": asset["sha256"]}
        for asset in assets
    ]
    facts.sort(key=lambda item: (str(item["role"]), str(item["sha256"])))
    return digest({"algorithm": "qagen.batch_scene_asset_set.v1", "assets": facts})


def anonymous_scene_ref(*, batch_digest: str, slot_digest: str, asset_set_digest: str) -> str:
    """Return a deterministic opaque reference bound to the attested inputs."""

    require_digest(batch_digest, "selection batch digest")
    require_digest(slot_digest, "selection batch slot digest")
    require_digest(asset_set_digest, "selection batch asset set digest")
    opaque = sha256(
        digest(
            {
                "algorithm": _SCENE_REF_VERSION,
                "batch_digest": batch_digest,
                "slot_digest": slot_digest,
                "asset_set_digest": asset_set_digest,
            }
        ).encode("ascii")
    ).hexdigest()
    result = f"scene://v32-{opaque}"
    return validate_anonymous_scene_ref(result)


def _operation_policy(*, viewer_family: str, asset_format: str, target_leaf: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return host allowlist, unsupported operations, and slot blocker codes."""

    if viewer_family == "molstar" and asset_format == "cif":
        return _MOLSTAR_CIF_OPERATIONS, (), ()
    if viewer_family == "molstar" and asset_format == "xyz":
        return _MOLSTAR_XYZ_OPERATIONS, (), ()
    if viewer_family == "niivue" and asset_format in {"nii", "nii.gz"}:
        return _NIIVUE_OPERATIONS, _NIIVUE_UNSUPPORTED_OPERATIONS, ()
    raise BatchSceneReplayError("host_contract_invalid")


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True)
class _ObservedAsset:
    size: int
    sha256: str
    content: bytes | None


def _open_asset(
    root: TrustedSourceRoot,
    relative_path: str,
    *,
    capture: bool,
) -> _ObservedAsset:
    """Read one selected asset via descriptor-relative, no-follow traversal."""

    parts = _canonical_relative_path(relative_path).split("/")
    current_fd = os.dup(root.fd)
    file_fd: int | None = None
    try:
        for part in parts[:-1]:
            try:
                observed = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                if not stat.S_ISDIR(observed.st_mode):
                    raise BatchSceneReplayError("asset_unavailable")
                child_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
            except BatchSceneReplayError:
                raise
            except OSError as exc:
                raise BatchSceneReplayError("asset_unavailable") from exc
            try:
                opened = os.fstat(child_fd)
                current_state = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                if not stat.S_ISDIR(opened.st_mode) or _stat_signature(opened) != _stat_signature(observed):
                    raise BatchSceneReplayError("asset_unavailable")
                if _stat_signature(opened) != _stat_signature(current_state):
                    raise BatchSceneReplayError("asset_unavailable")
            except BaseException:
                os.close(child_fd)
                raise
            os.close(current_fd)
            current_fd = child_fd

        name = parts[-1]
        try:
            observed = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISREG(observed.st_mode):
                raise BatchSceneReplayError("asset_unavailable")
            file_fd = os.open(name, _FILE_FLAGS, dir_fd=current_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or _stat_signature(before) != _stat_signature(observed):
                raise BatchSceneReplayError("asset_unavailable")
            if capture and before.st_size > _MAX_CAPTURE_BYTES:
                raise BatchSceneReplayError("asset_capture_limit_exceeded")
            hasher = sha256()
            content = bytearray() if capture else None
            total = 0
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)
                if content is not None:
                    content.extend(chunk)
            after = os.fstat(file_fd)
            current_state = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        except BatchSceneReplayError:
            raise
        except OSError as exc:
            raise BatchSceneReplayError("asset_unavailable") from exc
        if (
            _stat_signature(before) != _stat_signature(after)
            or _stat_signature(after) != _stat_signature(current_state)
            or total != after.st_size
        ):
            raise BatchSceneReplayError("asset_unavailable")
        return _ObservedAsset(size=total, sha256=hasher.hexdigest(), content=bytes(content) if content is not None else None)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(current_fd)


def _attest_asset(root: TrustedSourceRoot, asset: Mapping[str, Any], *, capture: bool) -> _ObservedAsset:
    observed = _open_asset(root, str(asset["path"]), capture=capture)
    if observed.size != asset["bytes"] or observed.sha256 != asset["sha256"]:
        raise BatchSceneReplayError("asset_integrity_mismatch")
    return observed


def _scene_from_row(
    *,
    batch_digest: str,
    row: Mapping[str, Any],
    root: TrustedSourceRoot,
) -> dict[str, Any]:
    assets = [dict(asset) for asset in row["assets"]]
    primary = next(asset for asset in assets if asset["role"] == "primary")
    mask = next((asset for asset in assets if asset["role"] == "mask"), None)
    family = _viewer_family(row)
    asset_format = _asset_format(str(primary["path"]), family)
    set_digest = _asset_set_digest(assets)
    slot_digest = str(row["slot_ref"]["content_digest"])
    scene_ref = anonymous_scene_ref(
        batch_digest=batch_digest,
        slot_digest=slot_digest,
        asset_set_digest=set_digest,
    )
    allowlist, unsupported, policy_blockers = _operation_policy(
        viewer_family=family,
        asset_format=asset_format,
        target_leaf=str(row["target_leaf"]),
    )
    blockers: tuple[str, ...] = policy_blockers
    status = "asset_attested"
    try:
        _attest_asset(root, primary, capture=False)
        if mask is not None:
            _attest_asset(root, mask, capture=False)
    except BatchSceneReplayError as exc:
        status = "blocked"
        blockers = tuple(sorted(set((*blockers, exc.code))))

    backend: dict[str, Any] = {
        "primary": {"path": primary["path"], "bytes": primary["bytes"], "sha256": primary["sha256"]}
    }
    if mask is not None:
        backend["mask"] = {"path": mask["path"], "bytes": mask["bytes"], "sha256": mask["sha256"]}
    return {
        "slot_digest": slot_digest,
        "anonymous_scene_ref": scene_ref,
        "asset_set_digest": set_digest,
        "viewer_family": family,
        "asset_format": asset_format,
        "operation_allowlist": list(allowlist),
        "unsupported_operations": list(unsupported),
        "capability_blockers": list(blockers),
        "asset_status": status,
        "backend": backend,
    }


@dataclass(frozen=True)
class BatchSceneRegistry:
    """Private mapping from opaque scene refs to independently rechecked bytes."""

    paths: WorkspacePaths
    path: Path
    envelope: Mapping[str, Any]

    @classmethod
    def build(
        cls,
        paths: WorkspacePaths,
        *,
        selection_batch: str | Path,
        asset_root: str | Path,
    ) -> "BatchSceneRegistry":
        paths.ensure()
        _, batch, batch_file_digest = _load_selection_batch(paths, selection_batch)
        rows = _validate_rows(batch)
        viewer_bundle = inspect_viewer_bundle_provenance(asset_root)
        trusted_root = Path(viewer_bundle["root"])
        try:
            with lock_trusted_source_root(trusted_root) as locked:
                scenes = [_scene_from_row(batch_digest=batch["content_digest"], row=row, root=locked) for row in rows]
        except SafeFilesystemError as exc:
            raise BatchSceneReplayError("asset_unavailable") from exc

        scenes.sort(key=lambda scene: str(scene["slot_digest"]))
        body = {
            "registry_version": 2,
            "selection_batch_digest": batch["content_digest"],
            "selection_batch_file_digest": batch_file_digest,
            "asset_root": str(trusted_root),
            "viewer_bundle": viewer_bundle,
            "total": len(scenes),
            "local_only": True,
            "release_eligible": False,
            "scenes": scenes,
        }
        status = "prepared" if all(scene["asset_status"] == "asset_attested" for scene in scenes) else "prepared_with_blocked_slots"
        envelope = make_envelope(
            kind=_REGISTRY_KIND,
            identifier=f"batch-scene-registry-{_short(batch['content_digest'])}",
            status=status,
            body=body,
        )
        directory = _private_directory(paths, _REGISTRY_DIRECTORY)
        output = directory / f"{_short(batch['content_digest'])}.{_short(envelope['content_digest'])}.registry.json"
        atomic_write_json(output, envelope, private=True)
        return cls(paths=paths, path=output, envelope=envelope)

    @classmethod
    def load(cls, paths: WorkspacePaths, value: str | Path) -> "BatchSceneRegistry":
        paths.ensure()
        raw = Path(value)
        if raw.is_symlink():
            raise BatchSceneReplayError("host_contract_invalid")
        try:
            path = require_within(paths.private, raw, "batch scene registry")
            _private_file(path, "batch scene registry")
            value_json = read_json(path)
        except BatchSceneReplayError:
            raise
        except (InputError, OSError) as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
        if not isinstance(value_json, Mapping):
            raise BatchSceneReplayError("host_contract_invalid")
        try:
            envelope = verify_envelope(value_json, kind=_REGISTRY_KIND)
        except InputError as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
        cls._validate_body(envelope["body"])
        return cls(paths=paths, path=path, envelope=envelope)

    @staticmethod
    def _validate_body(value: Any) -> None:
        if not isinstance(value, Mapping):
            raise BatchSceneReplayError("host_contract_invalid")
        required = {
            "registry_version",
            "selection_batch_digest",
            "selection_batch_file_digest",
            "asset_root",
            "viewer_bundle",
            "total",
            "local_only",
            "release_eligible",
            "scenes",
        }
        if set(value) != required or value.get("registry_version") != 2:
            raise BatchSceneReplayError("host_contract_invalid")
        if value.get("local_only") is not True or value.get("release_eligible") is not False:
            raise BatchSceneReplayError("host_contract_invalid")
        try:
            require_digest(value.get("selection_batch_digest"), "batch scene selection digest")
            require_digest(value.get("selection_batch_file_digest"), "batch scene selection file digest")
        except InputError as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
        bundle = validate_viewer_bundle_provenance(value.get("viewer_bundle"))
        if str(value.get("asset_root", "")) != bundle["root"]:
            raise BatchSceneReplayError("host_contract_invalid")
        scenes = value.get("scenes")
        if not isinstance(scenes, list) or value.get("total") != len(scenes) or not scenes:
            raise BatchSceneReplayError("host_contract_invalid")
        seen_refs: set[str] = set()
        seen_slots: set[str] = set()
        for scene in scenes:
            if not isinstance(scene, Mapping):
                raise BatchSceneReplayError("host_contract_invalid")
            expected = {
                "slot_digest",
                "anonymous_scene_ref",
                "asset_set_digest",
                "viewer_family",
                "asset_format",
                "operation_allowlist",
                "unsupported_operations",
                "capability_blockers",
                "asset_status",
                "backend",
            }
            if set(scene) != expected:
                raise BatchSceneReplayError("host_contract_invalid")
            try:
                slot = require_digest(scene.get("slot_digest"), "batch scene slot digest")
                asset_set = require_digest(scene.get("asset_set_digest"), "batch scene asset set digest")
                scene_ref = validate_anonymous_scene_ref(scene.get("anonymous_scene_ref"))
            except InputError as exc:
                raise BatchSceneReplayError("host_contract_invalid") from exc
            if slot in seen_slots or scene_ref in seen_refs:
                raise BatchSceneReplayError("host_contract_invalid")
            seen_slots.add(slot)
            seen_refs.add(scene_ref)
            if scene.get("viewer_family") not in {"molstar", "niivue"} or scene.get("asset_format") not in {
                "cif",
                "xyz",
                "nii",
                "nii.gz",
            }:
                raise BatchSceneReplayError("host_contract_invalid")
            for key in ("operation_allowlist", "unsupported_operations", "capability_blockers"):
                entries = scene.get(key)
                if not isinstance(entries, list) or len(set(entries)) != len(entries):
                    raise BatchSceneReplayError("host_contract_invalid")
                if key == "capability_blockers":
                    if any(item not in REPLAY_BLOCKER_CODES for item in entries):
                        raise BatchSceneReplayError("host_contract_invalid")
                elif any(not isinstance(item, str) or not _OPERATION_RE.fullmatch(item) for item in entries):
                    raise BatchSceneReplayError("host_contract_invalid")
            if scene.get("asset_status") not in {"asset_attested", "blocked"}:
                raise BatchSceneReplayError("host_contract_invalid")
            backend = scene.get("backend")
            if not isinstance(backend, Mapping) or set(backend) not in ({"primary"}, {"primary", "mask"}):
                raise BatchSceneReplayError("host_contract_invalid")
            for asset in backend.values():
                if not isinstance(asset, Mapping) or set(asset) != {"path", "bytes", "sha256"}:
                    raise BatchSceneReplayError("host_contract_invalid")
                _canonical_relative_path(asset.get("path"))
                if (
                    not isinstance(asset.get("bytes"), int)
                    or isinstance(asset.get("bytes"), bool)
                    or asset["bytes"] <= 0
                    or not isinstance(asset.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
                ):
                    raise BatchSceneReplayError("host_contract_invalid")
            if scene["viewer_family"] == "niivue" and set(backend) != {"primary", "mask"}:
                raise BatchSceneReplayError("host_contract_invalid")
            if scene["viewer_family"] == "molstar" and set(backend) != {"primary"}:
                raise BatchSceneReplayError("host_contract_invalid")
            if anonymous_scene_ref(
                batch_digest=str(value["selection_batch_digest"]),
                slot_digest=slot,
                asset_set_digest=asset_set,
            ) != scene_ref:
                raise BatchSceneReplayError("host_contract_invalid")

    @property
    def content_digest(self) -> str:
        return str(self.envelope["content_digest"])

    @property
    def selection_batch_digest(self) -> str:
        return str(self.envelope["body"]["selection_batch_digest"])

    @property
    def viewer_bundle(self) -> dict[str, Any]:
        """Return the revalidated external bundle binding for this registry."""

        return validate_viewer_bundle_provenance(self.envelope["body"].get("viewer_bundle"))

    @property
    def anonymous_scene_refs(self) -> tuple[str, ...]:
        return tuple(str(scene["anonymous_scene_ref"]) for scene in self.envelope["body"]["scenes"])

    def _scene(self, anonymous_scene_ref: str) -> dict[str, Any]:
        scene_ref = validate_anonymous_scene_ref(anonymous_scene_ref)
        scene = next(
            (item for item in self.envelope["body"]["scenes"] if item["anonymous_scene_ref"] == scene_ref),
            None,
        )
        if not isinstance(scene, Mapping):
            raise BatchSceneReplayError("asset_unavailable")
        return dict(scene)

    def public_scene_descriptor(self, anonymous_scene_ref: str) -> dict[str, Any]:
        """Return a path-free descriptor used to decide a controlled replay."""

        scene = self._scene(anonymous_scene_ref)
        return {
            "registry_digest": self.content_digest,
            "selection_batch_digest": self.selection_batch_digest,
            "slot_digest": scene["slot_digest"],
            "anonymous_scene_ref": scene["anonymous_scene_ref"],
            "asset_set_digest": scene["asset_set_digest"],
            "viewer_family": scene["viewer_family"],
            "asset_format": scene["asset_format"],
            "operation_allowlist": list(scene["operation_allowlist"]),
            "unsupported_operations": list(scene["unsupported_operations"]),
            "capability_blockers": list(scene["capability_blockers"]),
            "asset_status": scene["asset_status"],
            "release_eligible": False,
        }

    def resolve(self, anonymous_scene_ref: str) -> dict[str, Any]:
        """Return verified private bytes for a controlled local host only."""

        scene = self._scene(anonymous_scene_ref)
        if scene["asset_status"] != "asset_attested":
            blockers = scene["capability_blockers"]
            raise BatchSceneReplayError(str(blockers[0]) if blockers else "asset_unavailable")
        root = _strict_absolute_directory(str(self.envelope["body"]["asset_root"]))
        backend = scene["backend"]
        try:
            with lock_trusted_source_root(root) as locked:
                primary = _attest_asset(locked, backend["primary"], capture=True)
                mask = _attest_asset(locked, backend["mask"], capture=True) if "mask" in backend else None
        except SafeFilesystemError as exc:
            raise BatchSceneReplayError("asset_unavailable") from exc
        if primary.content is None or (mask is not None and mask.content is None):
            raise BatchSceneReplayError("asset_unavailable")
        result: dict[str, Any] = {
            "anonymous_scene_ref": scene["anonymous_scene_ref"],
            "slot_digest": scene["slot_digest"],
            "asset_set_digest": scene["asset_set_digest"],
            "viewer_family": scene["viewer_family"],
            "asset_format": scene["asset_format"],
            "asset_bytes": primary.content,
            "asset_content_digest": f"sha256:{primary.sha256}",
            "operation_allowlist": list(scene["operation_allowlist"]),
            "unsupported_operations": list(scene["unsupported_operations"]),
            "capability_blockers": list(scene["capability_blockers"]),
        }
        if mask is not None:
            result["mask_bytes"] = mask.content
            result["mask_content_digest"] = f"sha256:{mask.sha256}"
        return result


def build_batch_scene_registry(
    paths: WorkspacePaths,
    *,
    selection_batch: str | Path,
    asset_root: str | Path,
) -> BatchSceneRegistry:
    """Build a local-only 60-scene registry from an immutable batch envelope."""

    return BatchSceneRegistry.build(paths, selection_batch=selection_batch, asset_root=asset_root)


def _replay_result(scene: Mapping[str, Any], value: Mapping[str, Any]) -> dict[str, Any]:
    required = {"status", "host_id", "replayed_operations", "evidence", "blockers"}
    if set(value) != required:
        raise BatchSceneReplayError("host_contract_invalid")
    status = value.get("status")
    host_id = value.get("host_id")
    replayed = value.get("replayed_operations")
    evidence = value.get("evidence")
    blockers = value.get("blockers")
    if status not in {"attested", "blocked", "failed"} or not isinstance(host_id, str) or not _HOST_ID_RE.fullmatch(host_id):
        raise BatchSceneReplayError("host_contract_invalid")
    if not isinstance(replayed, list) or len(set(replayed)) != len(replayed):
        raise BatchSceneReplayError("host_contract_invalid")
    allowed = set(scene["operation_allowlist"])
    if any(not isinstance(item, str) or item not in allowed for item in replayed):
        raise BatchSceneReplayError("host_contract_invalid")
    if not isinstance(evidence, list) or len(evidence) != len(replayed):
        raise BatchSceneReplayError("host_contract_invalid")
    canonical_evidence: dict[str, dict[str, str]] = {}
    for entry in evidence:
        if not isinstance(entry, Mapping) or set(entry) != {"operation", "result_digest", "render_digest"}:
            raise BatchSceneReplayError("host_contract_invalid")
        operation = entry.get("operation")
        if not isinstance(operation, str) or operation not in allowed or operation in canonical_evidence:
            raise BatchSceneReplayError("host_contract_invalid")
        try:
            canonical_evidence[operation] = {
                "operation": operation,
                "result_digest": require_digest(entry.get("result_digest"), "viewer result digest"),
                "render_digest": require_digest(entry.get("render_digest"), "viewer render digest"),
            }
        except InputError as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
    if set(canonical_evidence) != set(replayed):
        raise BatchSceneReplayError("host_contract_invalid")
    if not isinstance(blockers, list) or len(set(blockers)) != len(blockers) or any(
        item not in REPLAY_BLOCKER_CODES for item in blockers
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    if status == "attested" and (not replayed or not canonical_evidence or blockers):
        raise BatchSceneReplayError("host_contract_invalid")
    if status in {"blocked", "failed"} and not blockers:
        raise BatchSceneReplayError("host_contract_invalid")
    return {
        "status": status,
        "host_id": host_id,
        "replayed_operations": sorted(replayed),
        "evidence": [canonical_evidence[key] for key in sorted(canonical_evidence)],
        "blockers": sorted(blockers),
    }


def persist_slot_replay_receipt(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    anonymous_scene_ref: str,
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one digest-only replay receipt after a controlled host call."""

    if registry.paths.root != paths.root:
        raise BatchSceneReplayError("host_contract_invalid")
    scene = registry._scene(anonymous_scene_ref)
    result = _replay_result(scene, replay)
    body = {
        "receipt_version": _REPLAY_RESULT_VERSION,
        "registry_digest": registry.content_digest,
        "selection_batch_digest": registry.selection_batch_digest,
        "slot_digest": scene["slot_digest"],
        "anonymous_scene_ref": scene["anonymous_scene_ref"],
        "asset_set_digest": scene["asset_set_digest"],
        "viewer_family": scene["viewer_family"],
        "host_id": result["host_id"],
        "allowed_operations": list(scene["operation_allowlist"]),
        "replayed_operations": result["replayed_operations"],
        "evidence": result["evidence"],
        "status": result["status"],
        "blockers": result["blockers"],
        "release_eligible": False,
    }
    envelope = make_envelope(
        kind=_SLOT_RECEIPT_KIND,
        identifier=f"batch-scene-replay-{_short(scene['slot_digest'])}",
        status=result["status"],
        body=body,
    )
    directory = _private_directory(paths, (*_RECEIPT_DIRECTORY, _short(registry.content_digest)))
    output = directory / f"{_short(scene['slot_digest'])}.{_short(envelope['content_digest'])}.receipt.json"
    atomic_write_json(output, envelope, private=True)
    return {
        "path": str(output),
        "content_digest": envelope["content_digest"],
        "slot_digest": scene["slot_digest"],
        "anonymous_scene_ref": scene["anonymous_scene_ref"],
        "status": result["status"],
    }


def _receipt_reference(value: Any) -> dict[str, str]:
    """Normalize one complete receipt reference without leaking its body."""

    expected = {"path", "content_digest", "slot_digest", "anonymous_scene_ref", "status"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise BatchSceneReplayError("host_contract_invalid")
    path = value.get("path")
    slot_digest = value.get("slot_digest")
    scene_ref = value.get("anonymous_scene_ref")
    status = value.get("status")
    if not isinstance(path, str) or not path or not isinstance(scene_ref, str) or status not in {"attested", "blocked", "failed"}:
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        validate_anonymous_scene_ref(scene_ref)
        return {
            "path": path,
            "content_digest": require_digest(value.get("content_digest"), "batch scene replay receipt digest"),
            "slot_digest": require_digest(slot_digest, "batch scene replay slot digest"),
            "anonymous_scene_ref": scene_ref,
            "status": status,
        }
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc


def persist_slot_replay_retry_record(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    retry_of: Mapping[str, Any],
    retry_receipt: Mapping[str, Any],
    repair_reason: str = _STABLE_CAPTURE_REPAIR_REASON,
) -> dict[str, Any]:
    """Record a stable-capture repair without changing a closed slot receipt.

    The original receipt remains immutable.  The record carries complete old
    and replacement receipt references only, never scene bytes, selectors, or
    operation results.
    """

    if registry.paths.root != paths.root or repair_reason not in _REPAIR_PRIOR_BLOCKERS:
        raise BatchSceneReplayError("host_contract_invalid")
    old_ref = _receipt_reference(retry_of)
    new_ref = _receipt_reference(retry_receipt)
    old = _load_slot_receipt(paths, old_ref, registry)
    new = _load_slot_receipt(paths, new_ref, registry)
    old_body = old["body"]
    new_body = new["body"]
    if (
        old_body.get("status") != "blocked"
        or frozenset(old_body.get("blockers", [])) not in _REPAIR_PRIOR_BLOCKERS[repair_reason]
        or any(
            old_body.get(key) != new_body.get(key)
            for key in ("registry_digest", "selection_batch_digest", "slot_digest", "anonymous_scene_ref", "asset_set_digest")
        )
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    body = {
        "record_version": _RETRY_RECORD_VERSION,
        "registry_digest": registry.content_digest,
        "selection_batch_digest": registry.selection_batch_digest,
        "slot_digest": old_body["slot_digest"],
        "anonymous_scene_ref": old_body["anonymous_scene_ref"],
        "retry_of": old_ref,
        "repair_reason": repair_reason,
        "retry_receipt": new_ref,
        "release_eligible": False,
    }
    created_at = utc_now()
    envelope = make_envelope(
        kind=_RETRY_RECORD_KIND,
        identifier=f"batch-scene-retry-{_short(str(old_body['slot_digest']))}",
        status="repaired" if new_body.get("status") == "attested" else "retry_blocked",
        body=body,
        created_at=created_at,
    )
    directory = _private_directory(paths, (*_RETRY_DIRECTORY, _short(registry.content_digest)))
    output = directory / f"{_short(str(old_body['slot_digest']))}.{_short(envelope['content_digest'])}.retry.json"
    if output.exists():
        # Equivalent blocked retries retain one semantic digest, but each
        # attempt needs its own immutable timestamped envelope for audit.
        attempt = 2
        while output.exists():
            output = directory / (
                f"{_short(str(old_body['slot_digest']))}.{_short(envelope['content_digest'])}.attempt-{attempt}.retry.json"
            )
            attempt += 1
    atomic_write_json(output, envelope, private=True)
    return {
        "path": str(output),
        "content_digest": envelope["content_digest"],
        "slot_digest": old_body["slot_digest"],
        "anonymous_scene_ref": old_body["anonymous_scene_ref"],
        "status": envelope["status"],
    }


def load_slot_replay_retry_record(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    retry_path: str | Path,
) -> dict[str, Any]:
    """Load one retry record and prove that it is bound to ``registry``.

    A retry record is deliberately separate from an immutable slot receipt.
    This loader permits a no-change retry (the replacement can reference the
    same receipt) while still requiring a categorical render-invalid prior
    result and a valid replacement receipt for exactly the same scene.
    """

    raw_path = Path(retry_path)
    if raw_path.is_symlink():
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        path = require_within(paths.private, raw_path, "batch scene replay retry record")
        _private_file(path, "batch scene replay retry record")
        raw = read_json(path)
    except BatchSceneReplayError:
        raise
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if not isinstance(raw, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        record = verify_envelope(raw, kind=_RETRY_RECORD_KIND)
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    body = record.get("body")
    expected_fields = {
        "record_version",
        "registry_digest",
        "selection_batch_digest",
        "slot_digest",
        "anonymous_scene_ref",
        "retry_of",
        "repair_reason",
        "retry_receipt",
        "release_eligible",
    }
    if (
        not isinstance(body, Mapping)
        or set(body) != expected_fields
        or body.get("record_version") != _RETRY_RECORD_VERSION
        or body.get("registry_digest") != registry.content_digest
        or body.get("selection_batch_digest") != registry.selection_batch_digest
        or body.get("repair_reason") not in _REPAIR_PRIOR_BLOCKERS
        or body.get("release_eligible") is not False
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    old_ref = _receipt_reference(body.get("retry_of"))
    new_ref = _receipt_reference(body.get("retry_receipt"))
    old = _load_slot_receipt(paths, old_ref, registry)
    new = _load_slot_receipt(paths, new_ref, registry)
    old_body = old["body"]
    new_body = new["body"]
    if (
        old_body.get("status") != "blocked"
        or frozenset(old_body.get("blockers", [])) not in _REPAIR_PRIOR_BLOCKERS[str(body.get("repair_reason"))]
        or body.get("slot_digest") != old_body.get("slot_digest")
        or body.get("anonymous_scene_ref") != old_body.get("anonymous_scene_ref")
        or any(
            old_body.get(key) != new_body.get(key)
            for key in ("registry_digest", "selection_batch_digest", "slot_digest", "anonymous_scene_ref", "asset_set_digest")
        )
        or record.get("status") != ("repaired" if new_body.get("status") == "attested" else "retry_blocked")
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    return {
        "path": str(path),
        "content_digest": record["content_digest"],
        "slot_digest": old_ref["slot_digest"],
        "anonymous_scene_ref": old_ref["anonymous_scene_ref"],
        "status": record["status"],
        "retry_of": old_ref,
        "retry_receipt": new_ref,
    }


def _load_slot_receipt(paths: WorkspacePaths, value: Mapping[str, Any], registry: BatchSceneRegistry) -> dict[str, Any]:
    expected_ref = {"path", "content_digest", "slot_digest", "anonymous_scene_ref", "status"}
    if set(value) != expected_ref:
        raise BatchSceneReplayError("host_contract_invalid")
    raw = Path(str(value["path"]))
    if raw.is_symlink():
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        path = require_within(paths.private, raw, "batch scene replay receipt")
        _private_file(path, "batch scene replay receipt")
        raw_receipt = read_json(path)
    except BatchSceneReplayError:
        raise
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if not isinstance(raw_receipt, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        receipt = verify_envelope(raw_receipt, kind=_SLOT_RECEIPT_KIND)
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    body = receipt.get("body")
    if not isinstance(body, Mapping) or receipt["content_digest"] != value["content_digest"]:
        raise BatchSceneReplayError("host_contract_invalid")
    required = {
        "receipt_version",
        "registry_digest",
        "selection_batch_digest",
        "slot_digest",
        "anonymous_scene_ref",
        "asset_set_digest",
        "viewer_family",
        "host_id",
        "allowed_operations",
        "replayed_operations",
        "evidence",
        "status",
        "blockers",
        "release_eligible",
    }
    if set(body) != required or body.get("registry_digest") != registry.content_digest:
        raise BatchSceneReplayError("host_contract_invalid")
    scene = registry._scene(str(body.get("anonymous_scene_ref")))
    if (
        body.get("selection_batch_digest") != registry.selection_batch_digest
        or body.get("slot_digest") != scene["slot_digest"]
        or body.get("asset_set_digest") != scene["asset_set_digest"]
        or body.get("viewer_family") != scene["viewer_family"]
        or body.get("release_eligible") is not False
        or body.get("slot_digest") != value["slot_digest"]
        or body.get("anonymous_scene_ref") != value["anonymous_scene_ref"]
        or body.get("status") != value["status"]
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    _replay_result(
        scene,
        {
            "status": body["status"],
            "host_id": body["host_id"],
            "replayed_operations": body["replayed_operations"],
            "evidence": body["evidence"],
            "blockers": body["blockers"],
        },
    )
    return dict(receipt)


def load_batch_replay_summary_slot_receipts(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    summary_path: str | Path,
) -> list[dict[str, str]]:
    """Resolve the full receipt references sealed by one canonical summary.

    Summaries intentionally store only slot and content digests.  Receipt file
    names are deterministic, so this loader reconstructs their private paths
    from those two facts and revalidates every receipt before exposing a full
    reference to a retry operation.
    """

    raw_path = Path(summary_path)
    if raw_path.is_symlink():
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        path = require_within(paths.private, raw_path, "batch scene replay summary")
        _private_file(path, "batch scene replay summary")
        raw = read_json(path)
    except BatchSceneReplayError:
        raise
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if not isinstance(raw, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        summary = verify_envelope(raw, kind=_SUMMARY_KIND)
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    body = summary.get("body")
    expected_fields = {
        "summary_version",
        "registry_digest",
        "selection_batch_digest",
        "total",
        "status_counts",
        "blocker_counts",
        "slot_receipts",
        "release_eligible",
    }
    if (
        not isinstance(body, Mapping)
        or set(body) != expected_fields
        or body.get("summary_version") != 1
        or body.get("registry_digest") != registry.content_digest
        or body.get("selection_batch_digest") != registry.selection_batch_digest
        or body.get("release_eligible") is not False
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    raw_refs = body.get("slot_receipts")
    expected_slots = {str(scene["slot_digest"]) for scene in registry.envelope["body"]["scenes"]}
    if not isinstance(raw_refs, list) or body.get("total") != len(raw_refs):
        raise BatchSceneReplayError("host_contract_invalid")
    receipt_directory = paths.private.joinpath(*_RECEIPT_DIRECTORY, _short(registry.content_digest))
    references: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, Mapping) or set(raw_ref) != {"slot_digest", "content_digest"}:
            raise BatchSceneReplayError("host_contract_invalid")
        try:
            slot_digest = require_digest(raw_ref.get("slot_digest"), "batch scene replay summary slot digest")
            content_digest = require_digest(raw_ref.get("content_digest"), "batch scene replay summary receipt digest")
        except InputError as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
        if slot_digest in seen:
            raise BatchSceneReplayError("host_contract_invalid")
        seen.add(slot_digest)
        receipt_path = receipt_directory / f"{_short(slot_digest)}.{_short(content_digest)}.receipt.json"
        try:
            _private_file(receipt_path, "batch scene replay receipt")
            raw_receipt = read_json(receipt_path)
            if not isinstance(raw_receipt, Mapping):
                raise BatchSceneReplayError("host_contract_invalid")
            receipt = verify_envelope(raw_receipt, kind=_SLOT_RECEIPT_KIND)
            receipt_body = receipt.get("body")
            if not isinstance(receipt_body, Mapping):
                raise BatchSceneReplayError("host_contract_invalid")
            reference = _receipt_reference(
                {
                    "path": str(receipt_path),
                    "content_digest": content_digest,
                    "slot_digest": slot_digest,
                    "anonymous_scene_ref": receipt_body.get("anonymous_scene_ref"),
                    "status": receipt_body.get("status"),
                }
            )
            _load_slot_receipt(paths, reference, registry)
        except BatchSceneReplayError:
            raise
        except (InputError, OSError) as exc:
            raise BatchSceneReplayError("host_contract_invalid") from exc
        references.append(reference)
    if set(seen) != expected_slots:
        raise BatchSceneReplayError("host_contract_invalid")
    # Re-seal through the same validator to make counts/status tampering fail
    # before a retry can use any older receipt reference.
    receipts = [_load_slot_receipt(paths, ref, registry) for ref in references]
    status_counts = {status: 0 for status in ("attested", "blocked", "failed")}
    blocker_counts: dict[str, int] = {}
    for receipt in receipts:
        receipt_body = receipt["body"]
        status_counts[str(receipt_body["status"])] += 1
        for blocker in receipt_body["blockers"]:
            blocker_counts[str(blocker)] = blocker_counts.get(str(blocker), 0) + 1
    summary_status = "replayed_pending_review" if not status_counts["blocked"] and not status_counts["failed"] else "replayed_with_blocked_slots"
    if (
        summary.get("status") != summary_status
        or body.get("status_counts") != status_counts
        or body.get("blocker_counts") != {key: blocker_counts[key] for key in sorted(blocker_counts)}
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    return sorted(references, key=lambda item: item["slot_digest"])


def persist_batch_replay_summary(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    slot_receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Seal exactly one receipt for every registered scene into a private summary."""

    if registry.paths.root != paths.root:
        raise BatchSceneReplayError("host_contract_invalid")
    receipts = [_load_slot_receipt(paths, item, registry) for item in slot_receipts]
    expected_slots = {str(scene["slot_digest"]) for scene in registry.envelope["body"]["scenes"]}
    by_slot: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        body = receipt["body"]
        slot_digest = str(body["slot_digest"])
        if slot_digest in by_slot:
            raise BatchSceneReplayError("host_contract_invalid")
        by_slot[slot_digest] = receipt
    if set(by_slot) != expected_slots:
        raise BatchSceneReplayError("host_contract_invalid")
    status_counts = {status: 0 for status in ("attested", "blocked", "failed")}
    blocker_counts: dict[str, int] = {}
    receipt_refs: list[dict[str, str]] = []
    for slot_digest, receipt in sorted(by_slot.items()):
        body = receipt["body"]
        status = str(body["status"])
        status_counts[status] += 1
        for blocker in body["blockers"]:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        receipt_refs.append({"slot_digest": slot_digest, "content_digest": receipt["content_digest"]})
    summary_status = "replayed_pending_review" if not status_counts["blocked"] and not status_counts["failed"] else "replayed_with_blocked_slots"
    body = {
        "summary_version": 1,
        "registry_digest": registry.content_digest,
        "selection_batch_digest": registry.selection_batch_digest,
        "total": len(receipt_refs),
        "status_counts": status_counts,
        "blocker_counts": {key: blocker_counts[key] for key in sorted(blocker_counts)},
        "slot_receipts": receipt_refs,
        "release_eligible": False,
    }
    envelope = make_envelope(
        kind=_SUMMARY_KIND,
        identifier=f"batch-scene-replay-summary-{_short(registry.content_digest)}",
        status=summary_status,
        body=body,
    )
    directory = _private_directory(paths, _RECEIPT_DIRECTORY)
    output = directory / f"{_short(registry.content_digest)}.{_short(envelope['content_digest'])}.summary.json"
    atomic_write_json(output, envelope, private=True)
    return {"path": str(output), "content_digest": envelope["content_digest"], "status": summary_status}


def _replay_host_code_digest() -> str:
    """Digest the local replay-host implementation without exporting its path."""

    try:
        return digest_bytes(Path(__file__).with_name("batch_scene_host.py").read_bytes())
    except OSError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc


def persist_batch_scene_viewer_lock_set(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    replay_summary_path: str | Path,
    viewer_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the viewer-only eligibility state for every batch slot.

    This is intentionally a private, anonymized lock artifact.  It records
    exactly enough for a later planner to prove which viewer receipt controls
    a slot, but it has no asset paths or bytes, source identifiers, task IDs,
    answers, or author handoff capability.
    """

    # Revalidate at persistence time, rather than copying a caller's claimed
    # digest into a fresh lock.  The binding must also be the exact one that
    # produced the private scene registry; this prevents a new lock from
    # pairing source bytes with an unrelated viewer checkout.
    bundle_binding = validate_viewer_bundle_provenance(viewer_bundle)
    if bundle_binding != registry.viewer_bundle:
        raise BatchSceneReplayError("host_contract_invalid")
    references = load_batch_replay_summary_slot_receipts(
        paths,
        registry=registry,
        summary_path=replay_summary_path,
    )
    raw_summary = read_json(Path(replay_summary_path))
    if not isinstance(raw_summary, Mapping):
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        summary = verify_envelope(raw_summary, kind=_SUMMARY_KIND)
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    host_code_digest = _replay_host_code_digest()
    status_counts = {status: 0 for status in ("attested", "blocked", "failed")}
    blocker_counts: dict[str, int] = {}
    slots: list[dict[str, Any]] = []
    for reference in references:
        receipt = _load_slot_receipt(paths, reference, registry)
        body = receipt["body"]
        status = str(body["status"])
        status_counts[status] += 1
        blockers = sorted(str(item) for item in body["blockers"])
        for blocker in blockers:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        slots.append(
            {
                "slot_digest": str(body["slot_digest"]),
                "anonymous_scene_ref": str(body["anonymous_scene_ref"]),
                "viewer_family": str(body["viewer_family"]),
                "allowed_operations": list(body["allowed_operations"]),
                "receipt_digest": str(receipt["content_digest"]),
                "receipt_status": status,
                "host_id": str(body["host_id"]),
                "host_code_digest": host_code_digest,
                # A slot that has a complete, attested replay receipt may be
                # considered by a later bounded pilot.  This does not make
                # the batch prepare/release-eligible: those aggregate gates
                # remain false until every independent control-plane check
                # has completed.
                "handoff_capable": status == "attested",
                "blockers": blockers,
            }
        )
    status = "viewer_pending" if status_counts["blocked"] or status_counts["failed"] else "viewer_attested_pending_review"
    body: dict[str, Any] = {
        "lock_set_version": 2,
        "registry_digest": registry.content_digest,
        "selection_batch_digest": registry.selection_batch_digest,
        "replay_summary_digest": str(summary["content_digest"]),
        "total": len(slots),
        "status_counts": status_counts,
        "blocker_counts": {key: blocker_counts[key] for key in sorted(blocker_counts)},
        "slots": sorted(slots, key=lambda item: str(item["slot_digest"])),
        "viewer_bundle": bundle_binding,
        "prepare_eligible": False,
        "release_eligible": False,
    }
    envelope = make_envelope(
        kind=_LOCK_SET_KIND,
        identifier=f"batch-scene-viewer-lock-set-{_short(registry.content_digest)}",
        status=status,
        body=body,
    )
    directory = _private_directory(paths, _LOCK_SET_DIRECTORY)
    output = directory / f"{_short(registry.content_digest)}.{_short(envelope['content_digest'])}.lock-set.json"
    atomic_write_json(output, envelope, private=True)
    return {
        "path": str(output),
        "content_digest": envelope["content_digest"],
        "status": status,
        "total": len(slots),
        "prepare_eligible": False,
        "release_eligible": False,
    }


def _private_reference_path(paths: WorkspacePaths, value: Any, *, label: str) -> tuple[Path, str]:
    """Resolve one digest-pinned private artifact reference.

    A lock bundle may live outside the workspace for review, but its fresh
    replay evidence may not.  Requiring the lock-set, registry, and receipt to
    reside below ``.qagen/private`` prevents a plan from silently switching to
    an unrelated checkout's browser evidence.
    """

    if not isinstance(value, Mapping) or set(value) != {"path", "content_digest"}:
        raise BatchSceneReplayError("host_contract_invalid")
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        expected_digest = require_digest(value.get("content_digest"), f"{label} digest")
        raw = Path(raw_path).expanduser()
        candidate = raw if raw.is_absolute() else paths.private / raw
        path = require_within(paths.private, candidate, label)
        _private_file(path, label)
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    return path, expected_digest


def load_attested_batch_replay_binding(
    paths: WorkspacePaths,
    *,
    fresh_replay: Any,
    selection_batch_digest: str,
    slot_digest: str,
    anonymous_scene_ref: str,
    allowed_operations: Sequence[str],
) -> dict[str, Any]:
    """Load one current, bundle-bound replay receipt for a live handoff.

    This is purposefully an exact binding, not a directory scan.  The lock
    names one lock set, registry, and slot receipt; each is re-attested before
    the caller is allowed to hand a transient render to a provider.
    """

    if not isinstance(fresh_replay, Mapping) or set(fresh_replay) != {"lock_set", "registry", "receipt"}:
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        expected_selection = require_digest(selection_batch_digest, "fresh replay selection batch digest")
        expected_slot = require_digest(slot_digest, "fresh replay slot digest")
        validate_anonymous_scene_ref(anonymous_scene_ref)
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    expected_operations = frozenset(str(item) for item in allowed_operations)
    if not expected_operations or len(expected_operations) != len(tuple(allowed_operations)) or "render" not in expected_operations:
        raise BatchSceneReplayError("host_contract_invalid")

    lock_path, expected_lock_digest = _private_reference_path(paths, fresh_replay["lock_set"], label="fresh replay lock set")
    registry_path, expected_registry_digest = _private_reference_path(paths, fresh_replay["registry"], label="fresh replay registry")
    receipt_path, expected_receipt_digest = _private_reference_path(paths, fresh_replay["receipt"], label="fresh replay receipt")
    try:
        raw_lock = read_json(lock_path)
        lock_set = verify_envelope(raw_lock, kind=_LOCK_SET_KIND) if isinstance(raw_lock, Mapping) else None
    except (InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if lock_set is None or lock_set.get("content_digest") != expected_lock_digest:
        raise BatchSceneReplayError("host_contract_invalid")
    body = lock_set.get("body")
    expected_lock_fields = {
        "lock_set_version",
        "registry_digest",
        "selection_batch_digest",
        "replay_summary_digest",
        "total",
        "status_counts",
        "blocker_counts",
        "slots",
        "prepare_eligible",
        "release_eligible",
        "viewer_bundle",
    }
    if (
        not isinstance(body, Mapping)
        or set(body) != expected_lock_fields
        or body.get("lock_set_version") != 2
        or body.get("selection_batch_digest") != expected_selection
        or body.get("prepare_eligible") is not False
        or body.get("release_eligible") is not False
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    try:
        lock_registry_digest = require_digest(body.get("registry_digest"), "fresh replay lock registry digest")
    except InputError as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if lock_registry_digest != expected_registry_digest:
        raise BatchSceneReplayError("host_contract_invalid")
    bundle = validate_viewer_bundle_provenance(body.get("viewer_bundle"))

    try:
        registry = BatchSceneRegistry.load(paths, registry_path)
    except (BatchSceneReplayError, InputError, OSError) as exc:
        raise BatchSceneReplayError("host_contract_invalid") from exc
    if registry.content_digest != expected_registry_digest or registry.selection_batch_digest != expected_selection:
        raise BatchSceneReplayError("host_contract_invalid")
    if registry.viewer_bundle != bundle:
        raise BatchSceneReplayError("host_contract_invalid")
    scene = registry._scene(anonymous_scene_ref)
    scene_operations = scene.get("operation_allowlist")
    if (
        scene.get("slot_digest") != expected_slot
        or not isinstance(scene_operations, list)
        or not all(isinstance(item, str) and item for item in scene_operations)
        or len(set(scene_operations)) != len(scene_operations)
        or not expected_operations.issubset(scene_operations)
    ):
        raise BatchSceneReplayError("host_contract_invalid")

    slots = body.get("slots")
    if not isinstance(slots, list):
        raise BatchSceneReplayError("host_contract_invalid")
    matches = [item for item in slots if isinstance(item, Mapping) and item.get("slot_digest") == expected_slot]
    if len(matches) != 1:
        raise BatchSceneReplayError("host_contract_invalid")
    slot = dict(matches[0])
    expected_slot_fields = {
        "slot_digest",
        "anonymous_scene_ref",
        "viewer_family",
        "allowed_operations",
        "receipt_digest",
        "receipt_status",
        "host_id",
        "host_code_digest",
        "handoff_capable",
        "blockers",
    }
    slot_operations = slot.get("allowed_operations")
    if (
        set(slot) != expected_slot_fields
        or slot.get("anonymous_scene_ref") != anonymous_scene_ref
        or not isinstance(slot_operations, list)
        or not all(isinstance(item, str) and item for item in slot_operations)
        or len(set(slot_operations)) != len(slot_operations)
        or not expected_operations.issubset(slot_operations)
        or slot.get("receipt_digest") != expected_receipt_digest
        or slot.get("receipt_status") != "attested"
        or slot.get("handoff_capable") is not True
        or slot.get("blockers") != []
    ):
        raise BatchSceneReplayError("host_contract_invalid")

    reference = {
        "path": str(receipt_path),
        "content_digest": expected_receipt_digest,
        "slot_digest": expected_slot,
        "anonymous_scene_ref": anonymous_scene_ref,
        "status": "attested",
    }
    receipt = _load_slot_receipt(paths, reference, registry)
    receipt_body = receipt.get("body")
    receipt_operations = receipt_body.get("allowed_operations") if isinstance(receipt_body, Mapping) else None
    if (
        not isinstance(receipt_body, Mapping)
        or receipt_body.get("registry_digest") != registry.content_digest
        or receipt_body.get("selection_batch_digest") != expected_selection
        or receipt_body.get("host_id") != slot.get("host_id")
        or not isinstance(receipt_operations, list)
        or not all(isinstance(item, str) and item for item in receipt_operations)
        or len(set(receipt_operations)) != len(receipt_operations)
        or not expected_operations.issubset(receipt_operations)
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    return {
        "lock_set": {"path": str(lock_path), "content_digest": expected_lock_digest},
        "registry": {"path": str(registry_path), "content_digest": registry.content_digest},
        "receipt": {"path": str(receipt_path), "content_digest": receipt["content_digest"]},
        "viewer_bundle": bundle,
        "slot": slot,
        "receipt_body": dict(receipt_body),
    }


def seal_batch_replay_summary_after_retries(
    paths: WorkspacePaths,
    *,
    registry: BatchSceneRegistry,
    prior_summary_path: str | Path,
    retry_record_paths: Sequence[str | Path],
) -> dict[str, Any]:
    """Seal a canonical summary that explicitly applies a set of retry records.

    The prior summary is the immutable base state.  Every retry must reference
    the exact old receipt sealed there, which prevents a later file from being
    substituted merely because it carries the same anonymous scene reference.
    """

    if not isinstance(retry_record_paths, Sequence) or isinstance(retry_record_paths, (str, bytes)) or not retry_record_paths:
        raise BatchSceneReplayError("host_contract_invalid")
    previous_refs = load_batch_replay_summary_slot_receipts(
        paths,
        registry=registry,
        summary_path=prior_summary_path,
    )
    previous_by_slot = {item["slot_digest"]: item for item in previous_refs}
    replacements: dict[str, dict[str, str]] = {}
    records: list[dict[str, Any]] = []
    for retry_path in retry_record_paths:
        record = load_slot_replay_retry_record(paths, registry=registry, retry_path=retry_path)
        old_ref = record["retry_of"]
        replacement = record["retry_receipt"]
        slot_digest = str(record["slot_digest"])
        if slot_digest in replacements or previous_by_slot.get(slot_digest) != old_ref:
            raise BatchSceneReplayError("host_contract_invalid")
        replacements[slot_digest] = replacement
        records.append(record)
    combined = [replacements.get(item["slot_digest"], item) for item in previous_refs]
    summary = persist_batch_replay_summary(paths, registry=registry, slot_receipts=combined)
    return {
        "registry_digest": registry.content_digest,
        "prior_summary_path": str(Path(prior_summary_path)),
        "retry_count": len(records),
        "retry_record_digests": sorted(str(record["content_digest"]) for record in records),
        "summary": summary,
        "release_eligible": False,
    }


def _replay_scene_with_host(
    registry: BatchSceneRegistry,
    *,
    anonymous_scene_ref: str,
    host: Any,
    timeout_seconds: float,
    operations: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Replay one registered scene and reduce any host error to a fixed code."""

    # Import here to keep scene-registry validation usable without Playwright.
    from .batch_scene_host import (  # pylint: disable=import-outside-toplevel
        HOST_ID,
        BatchReplayScene,
        BatchSceneReplayError as HostReplayError,
    )

    try:
        binding = registry.resolve(anonymous_scene_ref)
        scene = BatchReplayScene(
            anonymous_scene_ref=str(binding["anonymous_scene_ref"]),
            viewer_family=str(binding["viewer_family"]),
            primary_bytes=binding["asset_bytes"],
            primary_digest=str(binding["asset_content_digest"]),
            primary_format=str(binding["asset_format"]),
            overlay_bytes=binding.get("mask_bytes"),
            overlay_digest=binding.get("mask_content_digest"),
        )
        outcome = host.replay(
            scene,
            binding["operation_allowlist"] if operations is None else operations,
            timeout_seconds=timeout_seconds,
        )
        return {
            "status": outcome.status,
            "host_id": HOST_ID,
            "replayed_operations": list(outcome.replayed_operations),
            "evidence": [dict(item) for item in outcome.evidence],
            "blockers": list(outcome.blocker_codes),
        }
    except BatchSceneReplayError as exc:
        return {
            "status": "blocked",
            "host_id": HOST_ID,
            "replayed_operations": [],
            "evidence": [],
            "blockers": [exc.code],
        }
    except HostReplayError as exc:
        return {
            "status": "blocked",
            "host_id": HOST_ID,
            "replayed_operations": [],
            "evidence": [],
            "blockers": [exc.code],
        }


def retry_batch_scene_slots(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    prior_summary_path: str | Path,
    viewer_package_root: str | Path,
    anonymous_scene_refs: Sequence[str],
    per_slot_timeout_seconds: float = 45.0,
    repair_reason: str = _STABLE_CAPTURE_REPAIR_REASON,
) -> dict[str, Any]:
    """Retry only render-invalid slots after the stable-capture repair.

    A retry is intentionally narrow: it replaces no receipt silently, permits
    only the historical ``viewer_render_invalid`` blocker, records a separate
    repair envelope for every slot, and seals a new canonical summary using
    complete receipt references.
    """

    if (
        isinstance(per_slot_timeout_seconds, bool)
        or not isinstance(per_slot_timeout_seconds, (int, float))
        or not 5 <= float(per_slot_timeout_seconds) <= 90
        or not isinstance(anonymous_scene_refs, Sequence)
        or isinstance(anonymous_scene_refs, (str, bytes))
        or not anonymous_scene_refs
        or repair_reason not in _REPAIR_PRIOR_BLOCKERS
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    if not all(isinstance(item, str) for item in anonymous_scene_refs) or len(set(anonymous_scene_refs)) != len(anonymous_scene_refs):
        raise BatchSceneReplayError("host_contract_invalid")
    registry = BatchSceneRegistry.load(paths, registry_path)
    viewer_bundle = inspect_viewer_bundle_provenance(viewer_package_root)
    if viewer_bundle != registry.viewer_bundle:
        raise BatchSceneReplayError("host_contract_invalid")
    previous_refs = load_batch_replay_summary_slot_receipts(
        paths,
        registry=registry,
        summary_path=prior_summary_path,
    )
    previous_by_scene = {item["anonymous_scene_ref"]: item for item in previous_refs}
    requested = set(anonymous_scene_refs)
    if not requested.issubset(set(registry.anonymous_scene_refs)):
        raise BatchSceneReplayError("host_contract_invalid")
    from .batch_scene_host import ControlledBatchViewerReplayHost  # pylint: disable=import-outside-toplevel

    retry_records: list[dict[str, Any]] = []
    host = ControlledBatchViewerReplayHost(
        viewer_bundle["root"],
        molstar_representation_fallback=repair_reason == _MOLSTAR_CONTRAST_REPAIR_REASON,
    )
    try:
        for scene_ref in registry.anonymous_scene_refs:
            if scene_ref not in requested:
                continue
            prior_ref = previous_by_scene.get(scene_ref)
            if prior_ref is None:
                raise BatchSceneReplayError("host_contract_invalid")
            prior = _load_slot_receipt(paths, prior_ref, registry)
            prior_body = prior["body"]
            if (
                prior_body.get("status") != "blocked"
                or frozenset(prior_body.get("blockers", [])) not in _REPAIR_PRIOR_BLOCKERS[repair_reason]
            ):
                raise BatchSceneReplayError("host_contract_invalid")
            replay = _replay_scene_with_host(
                registry,
                anonymous_scene_ref=scene_ref,
                host=host,
                timeout_seconds=float(per_slot_timeout_seconds),
            )
            # A failed repair is an audit event, not a new canonical receipt.
            # Keeping the original reference prevents an unsuccessful browser
            # attempt from silently changing the frozen replay state.
            replacement = prior_ref
            if replay["status"] == "attested":
                replacement = persist_slot_replay_receipt(
                    paths,
                    registry=registry,
                    anonymous_scene_ref=scene_ref,
                    replay=replay,
                )
            retry_records.append(
                persist_slot_replay_retry_record(
                    paths,
                    registry=registry,
                    retry_of=prior_ref,
                    retry_receipt=replacement,
                    repair_reason=repair_reason,
                )
            )
    finally:
        host.shutdown()
    sealed = seal_batch_replay_summary_after_retries(
        paths,
        registry=registry,
        prior_summary_path=prior_summary_path,
        retry_record_paths=[record["path"] for record in retry_records],
    )
    summary_receipts = load_batch_replay_summary_slot_receipts(
        paths,
        registry=registry,
        summary_path=sealed["summary"]["path"],
    )
    status_counts: dict[str, int] = {}
    for receipt_ref in summary_receipts:
        status = str(receipt_ref["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "registry_path": str(registry.path),
        "registry_digest": registry.content_digest,
        "retried": len(retry_records),
        "retry_records": retry_records,
        "total": len(summary_receipts),
        "status_counts": {key: status_counts[key] for key in sorted(status_counts)},
        "summary": sealed["summary"],
        "release_eligible": False,
    }


def _retry_single_batch_scene_slot(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    prior_summary_path: str | Path,
    anonymous_scene_ref: str,
    per_slot_timeout_seconds: float,
    repair_reason: str,
    replay_scene: Callable[[BatchSceneRegistry], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run one named repair path without replacing a failed receipt.

    This is deliberately a one-slot operation.  It guarantees a retry record
    for every attempted repair, but commits a new immutable slot receipt only
    when the repair is fully attested.  A blocked retry therefore remains
    bound to the exact receipt sealed by ``prior_summary_path``.
    """

    if (
        isinstance(per_slot_timeout_seconds, bool)
        or not isinstance(per_slot_timeout_seconds, (int, float))
        or not 5 <= float(per_slot_timeout_seconds) <= 90
        or not isinstance(anonymous_scene_ref, str)
        or repair_reason not in _REPAIR_PRIOR_BLOCKERS
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    registry = BatchSceneRegistry.load(paths, registry_path)
    if anonymous_scene_ref not in registry.anonymous_scene_refs:
        raise BatchSceneReplayError("host_contract_invalid")
    previous_refs = load_batch_replay_summary_slot_receipts(
        paths,
        registry=registry,
        summary_path=prior_summary_path,
    )
    prior_ref = next((item for item in previous_refs if item["anonymous_scene_ref"] == anonymous_scene_ref), None)
    if prior_ref is None:
        raise BatchSceneReplayError("host_contract_invalid")
    prior = _load_slot_receipt(paths, prior_ref, registry)
    prior_body = prior["body"]
    if (
        prior_body.get("status") != "blocked"
        or frozenset(prior_body.get("blockers", [])) not in _REPAIR_PRIOR_BLOCKERS[repair_reason]
    ):
        raise BatchSceneReplayError("host_contract_invalid")

    replay = _replay_result(registry._scene(anonymous_scene_ref), replay_scene(registry))
    replacement = prior_ref
    if replay["status"] == "attested":
        replacement = persist_slot_replay_receipt(
            paths,
            registry=registry,
            anonymous_scene_ref=anonymous_scene_ref,
            replay=replay,
        )
    retry = persist_slot_replay_retry_record(
        paths,
        registry=registry,
        retry_of=prior_ref,
        retry_receipt=replacement,
        repair_reason=repair_reason,
    )
    sealed = seal_batch_replay_summary_after_retries(
        paths,
        registry=registry,
        prior_summary_path=prior_summary_path,
        retry_record_paths=[retry["path"]],
    )
    return {
        "registry_path": str(registry.path),
        "registry_digest": registry.content_digest,
        "anonymous_scene_ref": anonymous_scene_ref,
        "repair_reason": repair_reason,
        "retry": retry,
        "summary": sealed["summary"],
        "release_eligible": False,
    }


def _replay_scene_with_fresh_host_per_operation(
    registry: BatchSceneRegistry,
    *,
    anonymous_scene_ref: str,
    viewer_package_root: str | Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Replay every allowlisted operation in an isolated browser host.

    This path is for a slot where an operation may poison a shared Mol* state.
    It attests only when every operation completes in its own fresh host.  A
    partial result is kept only as a blocked retry event and cannot replace
    the canonical slot receipt.
    """

    from .batch_scene_host import HOST_ID  # pylint: disable=import-outside-toplevel

    scene = registry._scene(anonymous_scene_ref)
    allowed_operations = tuple(str(item) for item in scene["operation_allowlist"])
    evidence_by_operation: dict[str, dict[str, str]] = {}
    blockers: set[str] = set()
    for operation in allowed_operations:
        outcome = _replay_scene_with_fresh_browser(
            registry,
            anonymous_scene_ref=anonymous_scene_ref,
            viewer_package_root=viewer_package_root,
            timeout_seconds=timeout_seconds,
            operations=(operation,),
            timeout_blocker="viewer_operation_failed",
        )
        normalized = _replay_result(scene, outcome)
        for item in normalized["evidence"]:
            evidence_by_operation[item["operation"]] = item
        if normalized["status"] != "attested" or normalized["replayed_operations"] != [operation]:
            blockers.update(normalized["blockers"] or ["viewer_operation_failed"])
            # A replacement requires every allowlisted operation.  Once one
            # isolated host fails, later operations cannot change that result
            # and would only consume browser budget on a blocked slot.
            replayed = sorted(evidence_by_operation)
            return {
                "status": "blocked",
                "host_id": HOST_ID,
                "replayed_operations": replayed,
                "evidence": [evidence_by_operation[item] for item in replayed],
                "blockers": sorted(blockers),
            }

    replayed = sorted(evidence_by_operation)
    if not blockers and replayed == sorted(allowed_operations):
        return {
            "status": "attested",
            "host_id": HOST_ID,
            "replayed_operations": replayed,
            "evidence": [evidence_by_operation[operation] for operation in replayed],
            "blockers": [],
        }
    return {
        "status": "blocked",
        "host_id": HOST_ID,
        "replayed_operations": replayed,
        "evidence": [evidence_by_operation[operation] for operation in replayed],
        "blockers": sorted(blockers or {"viewer_operation_failed"}),
    }


def retry_batch_scene_slot_with_fresh_host_per_operation(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    prior_summary_path: str | Path,
    viewer_package_root: str | Path,
    anonymous_scene_ref: str,
    per_operation_timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Retry a ``viewer_operation_failed`` slot with one host per operation."""

    viewer_bundle = inspect_viewer_bundle_provenance(viewer_package_root)

    def replay_scene(registry: BatchSceneRegistry) -> Mapping[str, Any]:
        if registry.viewer_bundle != viewer_bundle:
            raise BatchSceneReplayError("host_contract_invalid")
        return _replay_scene_with_fresh_host_per_operation(
            registry,
            anonymous_scene_ref=anonymous_scene_ref,
            viewer_package_root=viewer_bundle["root"],
            timeout_seconds=float(per_operation_timeout_seconds),
        )

    return _retry_single_batch_scene_slot(
        paths,
        registry_path=registry_path,
        prior_summary_path=prior_summary_path,
        anonymous_scene_ref=anonymous_scene_ref,
        per_slot_timeout_seconds=per_operation_timeout_seconds,
        repair_reason=_ISOLATED_OPERATION_REPAIR_REASON,
        replay_scene=replay_scene,
    )


def _replay_scene_with_fresh_browser_direct(
    registry: BatchSceneRegistry,
    *,
    anonymous_scene_ref: str,
    viewer_package_root: str | Path,
    timeout_seconds: float,
    operations: Sequence[str] | None = None,
    molstar_representation_fallback: bool = False,
) -> dict[str, Any]:
    """Replay one slot through one browser, inside the watchdog child only."""

    from .batch_scene_host import ControlledBatchViewerReplayHost  # pylint: disable=import-outside-toplevel

    host = ControlledBatchViewerReplayHost(
        viewer_package_root,
        molstar_representation_fallback=molstar_representation_fallback,
    )
    try:
        return _replay_scene_with_host(
            registry,
            anonymous_scene_ref=anonymous_scene_ref,
            host=host,
            timeout_seconds=timeout_seconds,
            operations=operations,
        )
    finally:
        host.shutdown()


def _fresh_browser_replay_worker(
    result_queue: Any,
    registry: BatchSceneRegistry,
    anonymous_scene_ref: str,
    viewer_package_root: str,
    timeout_seconds: float,
    operations: tuple[str, ...] | None,
    molstar_representation_fallback: bool,
    timeout_blocker: str,
) -> None:
    """Return a sanitized replay result from an isolated, killable process."""

    try:
        result = _replay_scene_with_fresh_browser_direct(
            registry,
            anonymous_scene_ref=anonymous_scene_ref,
            viewer_package_root=viewer_package_root,
            timeout_seconds=timeout_seconds,
            operations=operations,
            molstar_representation_fallback=molstar_representation_fallback,
        )
    except Exception:
        # The parent must receive only an allowlisted blocker, never a browser
        # exception, raw path, or asset-derived value.
        result = {
            "status": "blocked",
            "host_id": "compiled-selection-batch-browser-replay-v1",
            "replayed_operations": [],
            "evidence": [],
            "blockers": [timeout_blocker],
        }
    try:
        result_queue.put(result)
    except Exception:
        # A closed parent queue is an expected consequence of a hard timeout.
        pass


def _replay_scene_with_fresh_browser(
    registry: BatchSceneRegistry,
    *,
    anonymous_scene_ref: str,
    viewer_package_root: str | Path,
    timeout_seconds: float,
    operations: Sequence[str] | None = None,
    molstar_representation_fallback: bool = False,
    timeout_blocker: str = "viewer_render_invalid",
) -> dict[str, Any]:
    """Replay in an OS-isolated browser process with a hard wall-clock cap.

    A static viewer can stop servicing both ``requestAnimationFrame`` and
    JavaScript timers.  In that failure mode a page-level timeout is not
    enough: the private browser must be disposable from outside its event
    loop.  The result queue carries only the already-sanitized replay mapping.
    """

    from .batch_scene_host import HOST_ID  # pylint: disable=import-outside-toplevel

    if timeout_blocker not in REPLAY_BLOCKER_CODES:
        raise BatchSceneReplayError("host_contract_invalid")
    requested_operations = None if operations is None else tuple(str(item) for item in operations)
    # ``fork`` keeps raw scene bytes in private process memory and avoids
    # serializing them through a queue or command line.  This host is created
    # only in the child, before any Playwright runtime exists in that process.
    context = get_context("fork")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_fresh_browser_replay_worker,
        args=(
            result_queue,
            registry,
            anonymous_scene_ref,
            str(viewer_package_root),
            float(timeout_seconds),
            requested_operations,
            molstar_representation_fallback,
            timeout_blocker,
        ),
        daemon=True,
    )
    process.start()
    try:
        process.join(float(timeout_seconds))
        if process.is_alive():
            process.terminate()
            process.join(_WATCHDOG_SHUTDOWN_GRACE_SECONDS)
            return {
                "status": "blocked",
                "host_id": HOST_ID,
                "replayed_operations": [],
                "evidence": [],
                "blockers": [timeout_blocker],
            }
        if process.exitcode != 0:
            return {
                "status": "blocked",
                "host_id": HOST_ID,
                "replayed_operations": [],
                "evidence": [],
                "blockers": [timeout_blocker],
            }
        try:
            result = result_queue.get(timeout=1.0)
        except Empty:
            return {
                "status": "blocked",
                "host_id": HOST_ID,
                "replayed_operations": [],
                "evidence": [],
                "blockers": [timeout_blocker],
            }
        return dict(result) if isinstance(result, Mapping) else {
            "status": "blocked",
            "host_id": HOST_ID,
            "replayed_operations": [],
            "evidence": [],
            "blockers": [timeout_blocker],
        }
    finally:
        if process.is_alive():
            process.terminate()
            process.join(_WATCHDOG_SHUTDOWN_GRACE_SECONDS)
        result_queue.close()
        result_queue.join_thread()


def retry_batch_scene_slot_with_fresh_browser(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    prior_summary_path: str | Path,
    viewer_package_root: str | Path,
    anonymous_scene_ref: str,
    per_slot_timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Retry a render-invalid slot in a fresh browser with a 90-second cap."""

    viewer_bundle = inspect_viewer_bundle_provenance(viewer_package_root)

    def replay_scene(registry: BatchSceneRegistry) -> Mapping[str, Any]:
        if registry.viewer_bundle != viewer_bundle:
            raise BatchSceneReplayError("host_contract_invalid")
        return _replay_scene_with_fresh_browser(
            registry,
            anonymous_scene_ref=anonymous_scene_ref,
            viewer_package_root=viewer_bundle["root"],
            timeout_seconds=float(per_slot_timeout_seconds),
        )

    return _retry_single_batch_scene_slot(
        paths,
        registry_path=registry_path,
        prior_summary_path=prior_summary_path,
        anonymous_scene_ref=anonymous_scene_ref,
        per_slot_timeout_seconds=per_slot_timeout_seconds,
        repair_reason=_FRESH_BROWSER_REPAIR_REASON,
        replay_scene=replay_scene,
    )


def retry_batch_scene_slot_with_molstar_contrast_focus(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    prior_summary_path: str | Path,
    viewer_package_root: str | Path,
    anonymous_scene_ref: str,
    per_slot_timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Retry a Mol* slot with its native polymer/ligand contrast preset."""

    viewer_bundle = inspect_viewer_bundle_provenance(viewer_package_root)

    def replay_scene(registry: BatchSceneRegistry) -> Mapping[str, Any]:
        if registry.viewer_bundle != viewer_bundle:
            raise BatchSceneReplayError("host_contract_invalid")
        return _replay_scene_with_fresh_browser(
            registry,
            anonymous_scene_ref=anonymous_scene_ref,
            viewer_package_root=viewer_bundle["root"],
            timeout_seconds=float(per_slot_timeout_seconds),
            molstar_representation_fallback=True,
        )

    return _retry_single_batch_scene_slot(
        paths,
        registry_path=registry_path,
        prior_summary_path=prior_summary_path,
        anonymous_scene_ref=anonymous_scene_ref,
        per_slot_timeout_seconds=per_slot_timeout_seconds,
        repair_reason=_MOLSTAR_CONTRAST_REPAIR_REASON,
        replay_scene=replay_scene,
    )


def replay_batch_scene_registry(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    viewer_package_root: str | Path,
    per_slot_timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Replay every frozen scene once and seal private, digest-only receipts.

    This is intentionally a local reviewer-preparation operation.  It does
    not invoke an LLM, construct a candidate, or provide a handoff to the
    chirality-only live executor.  A failed scene is sealed as a blocked
    receipt and never retries, so one browser/asset failure cannot abort the
    frozen batch or be silently hidden.
    """

    if (
        isinstance(per_slot_timeout_seconds, bool)
        or not isinstance(per_slot_timeout_seconds, (int, float))
        or not 5 <= float(per_slot_timeout_seconds) <= 90
    ):
        raise BatchSceneReplayError("host_contract_invalid")
    registry = BatchSceneRegistry.load(paths, registry_path)
    viewer_bundle = inspect_viewer_bundle_provenance(viewer_package_root)
    if viewer_bundle != registry.viewer_bundle:
        raise BatchSceneReplayError("host_contract_invalid")
    # Import here to keep the private registry usable without Playwright.
    from .batch_scene_host import (  # pylint: disable=import-outside-toplevel
        HOST_ID,
        BatchReplayScene,
        BatchSceneReplayError as HostReplayError,
        ControlledBatchViewerReplayHost,
    )

    host = ControlledBatchViewerReplayHost(viewer_bundle["root"])
    receipts: list[dict[str, Any]] = []
    try:
        for scene_ref in registry.anonymous_scene_refs:
            try:
                binding = registry.resolve(scene_ref)
                scene = BatchReplayScene(
                    anonymous_scene_ref=str(binding["anonymous_scene_ref"]),
                    viewer_family=str(binding["viewer_family"]),
                    primary_bytes=binding["asset_bytes"],
                    primary_digest=str(binding["asset_content_digest"]),
                    primary_format=str(binding["asset_format"]),
                    overlay_bytes=binding.get("mask_bytes"),
                    overlay_digest=binding.get("mask_content_digest"),
                )
                outcome = host.replay(
                    scene,
                    binding["operation_allowlist"],
                    timeout_seconds=float(per_slot_timeout_seconds),
                )
                replay = {
                    "status": outcome.status,
                    "host_id": HOST_ID,
                    "replayed_operations": list(outcome.replayed_operations),
                    "evidence": [dict(item) for item in outcome.evidence],
                    "blockers": list(outcome.blocker_codes),
                }
            except BatchSceneReplayError as exc:
                replay = {
                    "status": "blocked",
                    "host_id": HOST_ID,
                    "replayed_operations": [],
                    "evidence": [],
                    "blockers": [exc.code],
                }
            except HostReplayError as exc:
                replay = {
                    "status": "blocked",
                    "host_id": HOST_ID,
                    "replayed_operations": [],
                    "evidence": [],
                    "blockers": [exc.code],
                }
            receipts.append(
                persist_slot_replay_receipt(
                    paths,
                    registry=registry,
                    anonymous_scene_ref=scene_ref,
                    replay=replay,
                )
            )
    finally:
        host.shutdown()
    summary = persist_batch_replay_summary(paths, registry=registry, slot_receipts=receipts)
    status_counts: dict[str, int] = {}
    for receipt in receipts:
        status = str(receipt["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "registry_path": str(registry.path),
        "registry_digest": registry.content_digest,
        "total": len(receipts),
        "status_counts": {key: status_counts[key] for key in sorted(status_counts)},
        "summary": summary,
        "release_eligible": False,
    }
