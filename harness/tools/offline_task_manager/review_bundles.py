"""Validated, read-only static bundles exposed to online administrators."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .contract import ContractError, require_identifier

REVIEW_BUNDLES_SCHEMA_VERSION = "sciencevision.online_review_bundles.v1"
STATIC_BUNDLE_KIND = "sciencevision-task-bundle"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VIEWER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DEFAULT_EMBEDDED_VIEWER_PATHS = frozenset(
    {"viewer/molstar.html", "viewer/niivue.html"}
)
_DEFAULT_LANDING_PATH = "viewer/sciencevision_tasks.html"
_TRAJECTORY_REPLAY_LANDING_PATH = "viewer/trajectory-replay.html"


@dataclass(frozen=True)
class ApprovedFile:
    path: str
    sha256: str
    byte_size: int
    modified_ns: int
    device: int
    inode: int


@dataclass(frozen=True)
class ReviewBundle:
    review_id: str
    title: str
    root: Path
    manifest: dict[str, Any]
    files: dict[str, ApprovedFile]
    embedded_viewer_paths: frozenset[str]
    landing_paths: frozenset[str]
    tasks_path: str
    task_count: int
    file_count: int
    byte_size: int
    port: int
    root_device: int
    root_inode: int


def _ignored_macos_metadata(relative: Path) -> bool:
    return "__MACOSX" in relative.parts or any(
        part == ".DS_Store" or part.startswith("._") for part in relative.parts
    )


def _safe_relative_path(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip() or "\\" in raw:
        raise ContractError(f"{field} must be a safe root-relative URL path")
    if not raw.startswith("/") or raw.startswith("//"):
        raise ContractError(f"{field} must start with exactly one slash")
    pure = PurePosixPath(raw[1:])
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ContractError(f"{field} contains an unsafe path component")
    return pure.as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_inventory(root: Path) -> tuple[dict[str, ApprovedFile], int]:
    checksum_path = root / "SHA256SUMS"
    if checksum_path.is_symlink() or not checksum_path.is_file():
        raise ContractError("review bundle SHA256SUMS must be a regular non-symlink file")

    expected: dict[str, str] = {}
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        digest, separator, raw_relative = line.partition("  ")
        pure = PurePosixPath(raw_relative)
        if (
            not separator
            or not _SHA256_PATTERN.fullmatch(digest)
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ContractError(f"invalid review bundle SHA256SUMS line {line_number}")
        relative = pure.as_posix()
        if relative == "SHA256SUMS" or relative in expected:
            raise ContractError(f"duplicate or reserved SHA256SUMS path at line {line_number}")
        expected[relative] = digest

    linked = [path for path in root.rglob("*") if path.is_symlink()]
    if linked:
        raise ContractError(
            "review bundle contains a symlink: "
            + linked[0].relative_to(root).as_posix()
        )
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "SHA256SUMS"
        and not _ignored_macos_metadata(path.relative_to(root))
    }
    if set(expected) != actual:
        missing = sorted(set(expected) - actual)
        unexpected = sorted(actual - set(expected))
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing[:5]))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected[:5]))
        raise ContractError("review bundle checksum inventory mismatch; " + "; ".join(details))

    mismatched: list[str] = []
    approved: dict[str, ApprovedFile] = {}
    for relative, digest in expected.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if _sha256(path) != digest:
            mismatched.append(relative)
            continue
        stat = path.stat()
        approved[relative] = ApprovedFile(
            path=relative,
            sha256=digest,
            byte_size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            device=stat.st_dev,
            inode=stat.st_ino,
        )
    if mismatched:
        raise ContractError(
            "review bundle checksum mismatch: " + ", ".join(mismatched[:10])
        )
    byte_size = sum(record.byte_size for record in approved.values())
    return approved, byte_size


def _embedded_viewer_paths(
    rows: list[dict[str, Any]], files: dict[str, ApprovedFile]
) -> frozenset[str]:
    """Return only task-declared, inventory-approved HTML viewers for iframes."""
    paths = set(_DEFAULT_EMBEDDED_VIEWER_PATHS)
    for row in rows:
        asset = row.get("asset")
        viewer = asset.get("viewer") if isinstance(asset, dict) else None
        if not viewer:
            viewer = "gltf"
        if not isinstance(viewer, str) or not _VIEWER_NAME_PATTERN.fullmatch(viewer):
            continue
        # SVG tasks point the task iframe directly at their asset rather than a
        # viewer HTML page, so they do not widen this document allowlist.
        if viewer == "svg":
            continue
        paths.add(f"viewer/{viewer}.html")
    return frozenset(path for path in paths if path in files)


def _landing_paths(files: dict[str, ApprovedFile]) -> frozenset[str]:
    """Fixed, inventory-approved capability targets for a review bundle."""
    paths = {_DEFAULT_LANDING_PATH}
    if _TRAJECTORY_REPLAY_LANDING_PATH in files:
        paths.add(_TRAJECTORY_REPLAY_LANDING_PATH)
    return frozenset(paths)


def _load_bundle(record: dict[str, Any]) -> ReviewBundle:
    review_id = require_identifier(record.get("review_id"), "review_id")
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ContractError(f"review bundle {review_id!r} title is required")
    port = record.get("port", 0)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ContractError(f"review bundle {review_id!r} port must be 0..65535")
    raw_root = record.get("root")
    if not isinstance(raw_root, str) or not raw_root or not Path(raw_root).is_absolute():
        raise ContractError(f"review bundle {review_id!r} root must be an absolute path")
    root_input = Path(raw_root)
    if root_input.is_symlink() or not root_input.is_dir():
        raise ContractError(
            f"review bundle {review_id!r} root must be a non-symlink directory"
        )
    root = root_input.resolve()
    files, byte_size = _verify_inventory(root)

    manifest_relative = "bundle_manifest.json"
    if manifest_relative not in files:
        raise ContractError(f"review bundle {review_id!r} omits bundle_manifest.json")
    try:
        manifest = json.loads((root / manifest_relative).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ContractError(f"review bundle {review_id!r} manifest is invalid JSON") from error
    if not isinstance(manifest, dict) or manifest.get("kind") != STATIC_BUNDLE_KIND:
        raise ContractError(f"review bundle {review_id!r} manifest kind is unsupported")
    tasks_path = _safe_relative_path(
        manifest.get("tasks_jsonl"), f"review bundle {review_id!r} tasks_jsonl"
    )
    if tasks_path not in files:
        raise ContractError(f"review bundle {review_id!r} tasks_jsonl is outside its inventory")
    entry_path = "viewer/sciencevision_tasks.html"
    if entry_path not in files:
        raise ContractError(f"review bundle {review_id!r} omits {entry_path}")
    task_count = manifest.get("task_count")
    if not isinstance(task_count, int) or isinstance(task_count, bool) or task_count < 0:
        raise ContractError(f"review bundle {review_id!r} task_count is invalid")
    try:
        rows = [
            json.loads(line)
            for line in root.joinpath(*PurePosixPath(tasks_path).parts)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise ContractError(f"review bundle {review_id!r} tasks_jsonl is invalid") from error
    if len(rows) != task_count or not all(isinstance(row, dict) for row in rows):
        raise ContractError(f"review bundle {review_id!r} task_count does not match JSONL")
    root_stat = root.stat()
    return ReviewBundle(
        review_id=review_id,
        title=title.strip(),
        root=root,
        manifest=manifest,
        files=files,
        embedded_viewer_paths=_embedded_viewer_paths(rows, files),
        landing_paths=_landing_paths(files),
        tasks_path=tasks_path,
        task_count=task_count,
        file_count=len(files),
        byte_size=byte_size,
        port=port,
        root_device=root_stat.st_dev,
        root_inode=root_stat.st_ino,
    )


def load_review_bundles(path: Path | None) -> dict[str, ReviewBundle]:
    if path is None:
        return {}
    config_input = path.expanduser()
    if config_input.is_symlink() or not config_input.is_file():
        raise ContractError("review bundles config must be a regular non-symlink file")
    if config_input.stat().st_mode & 0o077:
        raise ContractError(
            "review bundles config must not be accessible by group or other users"
        )
    try:
        config = json.loads(config_input.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ContractError("review bundles config is invalid JSON") from error
    if not isinstance(config, dict) or config.get("schema_version") != REVIEW_BUNDLES_SCHEMA_VERSION:
        raise ContractError("unsupported review bundles config schema")
    records = config.get("bundles")
    if not isinstance(records, list):
        raise ContractError("review bundles config must contain a bundles list")
    bundles: dict[str, ReviewBundle] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ContractError(f"review bundles config record {index} must be an object")
        bundle = _load_bundle(record)
        if bundle.review_id in bundles:
            raise ContractError(f"duplicate review_id {bundle.review_id!r}")
        bundles[bundle.review_id] = bundle
    return bundles
