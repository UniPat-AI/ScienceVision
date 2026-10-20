"""Immutable expert task bundles and shared viewer runtime manifests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .contract import (
    BUNDLE_CONTRACT_VERSION,
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_TASK_SCHEMA_VERSION,
    LEGACY_BUNDLE_SCHEMA_VERSION,
    RUNTIME_LOADER_API_VERSION,
    RUNTIME_SCHEMA_VERSION,
    ContractError,
    canonical_json,
    file_sha256,
    object_sha256,
    redact_task,
    require_hash_identifier,
    require_identifier,
    require_sha256,
    verify_manifest_hash,
)

REDACTION_POLICY = "expert-visible-allowlist-v1"
LEGACY_INTEGRITY_REQUIRE_CHECKSUMS = "require-sha256sums-v1"
LEGACY_INTEGRITY_ALLOW_MISSING = "allow-historical-missing-sha256sums-v1"
_EXPERT_APP_FILES = ("index.html", "expert-app.js", "styles.css")
# Parent-page half of the browser MCP layer: the schema-driven Functions panel
# and the expert Trajectory capture drawer. See expert_app/mcp/PROVENANCE.md.
_MCP_CONTROL_FILES = (
    "task-mcp-controls.js",
    "task-mcp-controls.css",
    "task-trajectory.js",
    "task-trajectory.css",
)


def canonical_jsonl(rows: Iterable[dict[str, Any]]) -> str:
    return "".join(canonical_json(row) + "\n" for row in rows)


def required_renderers(assignments: Iterable[dict[str, Any]]) -> list[str]:
    renderers: set[str] = set()
    for assignment in assignments:
        task = assignment["payload"]
        assets: list[dict[str, Any]] = []
        if isinstance(task.get("asset"), dict):
            assets.append(task["asset"])
        if isinstance(task.get("assets"), list):
            assets.extend(item for item in task["assets"] if isinstance(item, dict))
        for asset in assets:
            viewer = asset.get("viewer")
            if viewer == "svg" or (not viewer and asset.get("format") == "svg"):
                continue
            renderers.add(str(viewer or "gltf"))
    return sorted(renderers)


def prepare_expert_bundle(
    inputs: dict[str, Any], *, batch_id: str, expert_id: str
) -> dict[str, Any]:
    assignments = inputs["assignments"]
    task_rows: list[dict[str, Any]] = []
    manifest_assignments: list[dict[str, Any]] = []
    closure: dict[str, dict[str, Any]] = {}
    removed_count = 0
    for assignment in assignments:
        redacted, removed = redact_task(assignment["payload"])
        removed_count += len(removed)
        task_rows.append(
            {
                "schema_version": BUNDLE_TASK_SCHEMA_VERSION,
                "contract_version": BUNDLE_CONTRACT_VERSION,
                "batch_id": batch_id,
                "expert_id": expert_id,
                "assignment_id": assignment["assignment_id"],
                "task_id": assignment["task_id"],
                "task_revision_sha256": assignment["task_revision_sha256"],
                "task": redacted,
            }
        )
        manifest_assignments.append(
            {
                "assignment_id": assignment["assignment_id"],
                "task_id": assignment["task_id"],
                "task_revision_sha256": assignment["task_revision_sha256"],
                "domain": assignment["domain"],
            }
        )
        for record in assignment["assets"]:
            existing = closure.get(record["path"])
            if existing and existing != record:
                raise ContractError(f"asset closure conflict: {record['path']}")
            closure[record["path"]] = record

    tasks_jsonl = canonical_jsonl(task_rows)
    tasks_sha256 = hashlib.sha256(tasks_jsonl.encode("utf-8")).hexdigest()
    assets = [closure[path] for path in sorted(closure)]
    asset_closure_sha256 = object_sha256(assets)
    manifest_identity = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "contract_version": BUNDLE_CONTRACT_VERSION,
        "batch_id": batch_id,
        "expert_id": expert_id,
        "expert": {
            "name": inputs["expert"]["name"],
            "domains": inputs["expert"]["domains"],
        },
        "admission": inputs["admission"],
        "task_file": {
            "path": "tasks/tasks.jsonl",
            "media_type": "application/x-ndjson",
            "count": len(task_rows),
            "sha256": tasks_sha256,
        },
        "assignments": manifest_assignments,
        "asset_count": len(assets),
        "asset_bytes": sum(int(record.get("byte_size") or 0) for record in assets),
        "asset_closure_sha256": asset_closure_sha256,
        "assets": assets,
        "runtime_requirement": {
            "loader_api": RUNTIME_LOADER_API_VERSION,
            "renderers": required_renderers(assignments),
            "capabilities": ["expert-review", "task-screenshot"],
        },
        "redaction": {"policy": REDACTION_POLICY, "removed_field_count": removed_count},
        "submission": {"schema_version": "sciencevision.expert_bundle_submission.v2"},
    }
    bundle_id = "bndl_" + object_sha256(manifest_identity)[:24]
    manifest_body = manifest_identity | {"bundle_id": bundle_id}
    manifest = manifest_body | {"bundle_manifest_sha256": object_sha256(manifest_body)}
    return {
        "bundle_id": bundle_id,
        "manifest": manifest,
        "tasks_jsonl": tasks_jsonl,
        "tasks_sha256": tasks_sha256,
        "assets": assets,
        "asset_paths": sorted(closure),
        "asset_root": str(Path(inputs["batch"]["asset_root"]).resolve()),
        "source_kind": "v2",
        "source_root": None,
    }


def _capability_record(path: Path, label: str) -> dict[str, Any]:
    return {
        "path": label,
        "sha256": file_sha256(path),
        "byte_size": path.stat().st_size,
    }


def build_runtime_manifest(
    runtime_root: Path, *, expert_app_root: Path | None = None
) -> dict[str, Any]:
    root = runtime_root.resolve()
    if runtime_root.is_symlink() or not root.is_dir():
        raise ContractError("viewer runtime root must be a non-symlink directory")
    records: list[dict[str, Any]] = []
    manifest_path = root / "runtime_manifest.json"
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ContractError(f"viewer runtime contains a symlink: {path}")
        if path.is_file() and path != manifest_path:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": file_sha256(path),
                    "byte_size": path.stat().st_size,
                }
            )
    if not records:
        raise ContractError("viewer runtime is empty")
    files_sha256 = object_sha256(records)
    viewer_root = root / "viewer" if (root / "viewer").is_dir() else root
    renderers = sorted(path.stem for path in viewer_root.glob("*.html"))
    entrypoint = root / "index.html"
    if not entrypoint.is_file() or entrypoint.is_symlink():
        raise ContractError("viewer runtime entrypoint index.html is missing or linked")

    capability_evidence: dict[str, list[dict[str, Any]]] = {}
    screenshot_files = (
        viewer_root / "shared" / "screenshot-api.js",
        viewer_root / "shared" / "task-screenshot.js",
    )
    if all(path.is_file() and not path.is_symlink() for path in screenshot_files):
        capability_evidence["task-screenshot"] = [
            _capability_record(path, path.relative_to(root).as_posix())
            for path in screenshot_files
        ]

    app_root = root
    if not all((app_root / name).is_file() for name in _EXPERT_APP_FILES):
        app_root = expert_app_root.resolve() if expert_app_root is not None else root
    app_files = tuple(app_root / name for name in _EXPERT_APP_FILES)
    if all(path.is_file() and not path.is_symlink() for path in app_files):
        capability_evidence["expert-review"] = [
            _capability_record(
                path,
                path.relative_to(root).as_posix()
                if path.is_relative_to(root)
                else f"expert-app/{path.name}",
            )
            for path in app_files
        ]

    # A task can only be driven through browser MCP when both halves are
    # present: the in-iframe command boundary that ships with the package, and
    # the parent-page control surface that the host serves.
    mcp_files = (viewer_root / "shared" / "viewer-mcp-api.js",) + tuple(
        app_root / "mcp" / name for name in _MCP_CONTROL_FILES
    )
    if all(path.is_file() and not path.is_symlink() for path in mcp_files):
        capability_evidence["viewer-mcp-functions"] = [
            _capability_record(
                path,
                path.relative_to(root).as_posix()
                if path.is_relative_to(root)
                else f"expert-app/mcp/{path.name}",
            )
            for path in mcp_files
        ]

    body = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "loader_api": RUNTIME_LOADER_API_VERSION,
        "files_sha256": files_sha256,
        "files": records,
        "file_count": len(records),
        "byte_size": sum(record["byte_size"] for record in records),
        "entrypoint": "index.html",
        "renderers": renderers,
        "capabilities": sorted(capability_evidence),
        "capability_evidence": capability_evidence,
    }
    return body | {"runtime_id": "vrt_" + object_sha256(body)[:24]}


def require_runtime_compatibility(
    requirement: Any, runtime_manifest: dict[str, Any]
) -> None:
    """Fail closed when a runtime cannot execute a task bundle."""

    if not isinstance(requirement, dict):
        raise ContractError("bundle runtime_requirement must be an object")
    loader_api = requirement.get("loader_api")
    if not isinstance(loader_api, str) or not loader_api:
        raise ContractError("bundle runtime_requirement.loader_api must be text")
    if loader_api != runtime_manifest.get("loader_api"):
        raise ContractError("viewer runtime loader API does not satisfy bundle requirements")

    for field, label in (
        ("renderers", "renderers"),
        ("capabilities", "capabilities"),
    ):
        required = requirement.get(field)
        available = runtime_manifest.get(field)
        if not isinstance(required, list) or not all(
            isinstance(item, str) and item for item in required
        ):
            raise ContractError(f"bundle runtime_requirement.{field} must be text values")
        if not isinstance(available, list) or not all(
            isinstance(item, str) and item for item in available
        ):
            raise ContractError(f"viewer runtime {field} inventory is invalid")
        missing = sorted(set(required) - set(available))
        if missing:
            raise ContractError(
                f"viewer runtime is missing required {label}: {', '.join(missing)}"
            )


def _safe_relative_posix_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractError(f"{field} must be a non-empty relative POSIX path")
    if "\\" in value or any(character in value for character in "?#"):
        raise ContractError(f"unsafe {field}: {value!r}")
    if any(ord(character) < 32 for character in value):
        raise ContractError(f"unsafe {field}: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ContractError(f"unsafe {field}: {value!r}")
    return path.as_posix()


def _verify_legacy_inventory(
    root: Path, tasks_name: str, integrity_policy: str
) -> dict[str, Any]:
    checksum_path = root / "SHA256SUMS"
    if not checksum_path.exists():
        if integrity_policy != LEGACY_INTEGRITY_ALLOW_MISSING:
            raise ContractError(
                "legacy package SHA256SUMS is required; use the explicit historical "
                "missing-checksum policy only for audited pre-checksum packages"
            )
        return {
            "policy": LEGACY_INTEGRITY_ALLOW_MISSING,
            "sha256sums_present": False,
            "verified_file_count": 0,
        }
    if checksum_path.is_symlink() or not checksum_path.is_file():
        raise ContractError("legacy package SHA256SUMS must be a regular file")

    expected: dict[str, str] = {}
    for line_number, line in enumerate(
        checksum_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        digest, separator, relative = line.partition("  ")
        try:
            name = _safe_relative_posix_path(relative, "SHA256SUMS path")
        except ContractError as error:
            raise ContractError(f"invalid SHA256SUMS line {line_number}: {error}") from error
        if (
            not separator
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or name == "SHA256SUMS"
            or name in expected
        ):
            raise ContractError(f"invalid SHA256SUMS line {line_number}")
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != checksum_path
    }
    if set(expected) != actual:
        raise ContractError("legacy package SHA256SUMS inventory mismatch")
    for required in ("package_manifest.json", tasks_name):
        if required not in expected:
            raise ContractError(f"legacy package SHA256SUMS omits {required}")
    mismatched = [
        name
        for name, digest in expected.items()
        if file_sha256(root.joinpath(*PurePosixPath(name).parts)) != digest
    ]
    if mismatched:
        raise ContractError(
            "legacy package checksum mismatch: " + ", ".join(mismatched[:10])
        )
    records = [{"path": name, "sha256": expected[name]} for name in sorted(expected)]
    return {
        "policy": LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
        "sha256sums_present": True,
        "verified_file_count": len(expected),
        "inventory_sha256": object_sha256(records),
    }


def prepare_legacy_bundle(
    package_root: Path,
    *,
    integrity_policy: str = LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
) -> dict[str, Any]:
    if integrity_policy not in {
        LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
        LEGACY_INTEGRITY_ALLOW_MISSING,
    }:
        raise ContractError(f"unsupported legacy integrity policy: {integrity_policy!r}")
    root = package_root.expanduser().resolve()
    if package_root.is_symlink() or not root.is_dir():
        raise ContractError("legacy package root must be a non-symlink directory")
    linked = [path for path in root.rglob("*") if path.is_symlink()]
    if linked:
        raise ContractError(
            "legacy package contains a symlink: " + linked[0].relative_to(root).as_posix()
        )
    manifest_path = root / "package_manifest.json"
    if not manifest_path.is_file():
        raise ContractError("legacy package manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "sciencevision.expert_package.v1":
        raise ContractError("legacy package manifest schema is unsupported")
    verify_manifest_hash(manifest)
    raw_tasks_path = manifest.get("tasks_path")
    historical_url_path = raw_tasks_path == "/tasks/tasks.jsonl"
    if historical_url_path and integrity_policy == LEGACY_INTEGRITY_ALLOW_MISSING:
        tasks_name = "tasks/tasks.jsonl"
    else:
        tasks_name = _safe_relative_posix_path(raw_tasks_path, "legacy tasks_path")
    tasks_path = root.joinpath(*PurePosixPath(tasks_name).parts)
    resolved_tasks = tasks_path.resolve()
    if not resolved_tasks.is_relative_to(root) or not tasks_path.is_file():
        raise ContractError("legacy package tasks file is missing or linked")
    integrity = _verify_legacy_inventory(root, tasks_name, integrity_policy)
    tasks_jsonl = tasks_path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in tasks_jsonl.splitlines() if line.strip()]
    if len(rows) != manifest.get("task_count"):
        raise ContractError("legacy package task count mismatch")
    package_id = str(manifest.get("package_id", ""))
    expert_id = str(manifest.get("expert_id", ""))
    require_hash_identifier(package_id, "package_id")
    require_identifier(expert_id, "expert_id")
    require_identifier(manifest.get("batch_id"), "batch_id")
    if any(row.get("package_id") != package_id or row.get("expert_id") != expert_id for row in rows):
        raise ContractError("legacy package task identity mismatch")
    assignments = manifest.get("assignments")
    if not isinstance(assignments, list):
        raise ContractError("legacy package assignment closure is missing")
    expected_assignments = {
        (
            item.get("assignment_id"),
            item.get("task_id"),
            item.get("task_revision_sha256"),
        )
        for item in assignments
        if isinstance(item, dict)
    }
    actual_assignments = {
        (
            row.get("assignment_id"),
            row.get("task_id"),
            row.get("task_revision_sha256"),
        )
        for row in rows
    }
    if len(expected_assignments) != len(assignments) or actual_assignments != expected_assignments:
        raise ContractError("legacy package task assignment closure mismatch")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise ContractError("legacy package asset closure is missing")
    for record in assets:
        if not isinstance(record, dict):
            raise ContractError("legacy package asset record must be an object")
        relative = _safe_relative_posix_path(record.get("path"), "legacy asset path")
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ContractError(f"legacy package asset is missing or linked: {relative}")
        expected_sha256 = require_sha256(
            record.get("sha256"), f"legacy asset {relative} sha256"
        )
        if file_sha256(path) != expected_sha256:
            raise ContractError(f"legacy package asset hash mismatch: {relative}")
        if record.get("byte_size") is not None and path.stat().st_size != record["byte_size"]:
            raise ContractError(f"legacy package asset size mismatch: {relative}")
    tasks_sha256 = hashlib.sha256(tasks_jsonl.encode("utf-8")).hexdigest()
    integrity["tasks_sha256"] = tasks_sha256
    integrity["tasks_path_policy"] = (
        "historical-root-url-exact-v1" if historical_url_path else "relative-posix-v1"
    )
    adapter_body = {
        "schema_version": LEGACY_BUNDLE_SCHEMA_VERSION,
        "contract_version": "sciencevision.offline_exchange.v1",
        "bundle_id": package_id,
        "batch_id": manifest["batch_id"],
        "expert_id": expert_id,
        "expert": manifest["expert"],
        "read_only": True,
        "admission_status": "legacy_pre_admission",
        "task_file": {
            "path": "tasks/tasks.jsonl",
            "media_type": "application/x-ndjson",
            "count": len(rows),
            "sha256": tasks_sha256,
        },
        "assignments": manifest["assignments"],
        "asset_count": len(assets),
        "asset_bytes": sum(int(record.get("byte_size") or 0) for record in assets),
        "asset_closure_sha256": object_sha256(assets),
        "assets": assets,
        "runtime_requirement": {
            "loader_api": RUNTIME_LOADER_API_VERSION,
            "renderers": required_renderers(
                {"payload": row["task"]} for row in rows
            ),
            "capabilities": ["expert-review", "task-screenshot"],
        },
        "legacy_package": {
            "package_id": package_id,
            "package_manifest_sha256": manifest["manifest_sha256"],
            "submission_schema_version": "sciencevision.expert_submission.v1",
            "integrity": integrity,
        },
    }
    adapter = adapter_body | {"bundle_manifest_sha256": object_sha256(adapter_body)}
    return {
        "bundle_id": package_id,
        "manifest": adapter,
        "tasks_jsonl": tasks_jsonl,
        "tasks_sha256": tasks_sha256,
        "assets": assets,
        "asset_paths": sorted(str(record["path"]) for record in assets),
        "asset_root": str(root),
        "source_kind": "legacy-v1",
        "source_root": str(root),
    }
