"""Versioned exchange contract helpers shared by manager and package builder."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

CONTRACT_VERSION = "sciencevision.offline_exchange.v1"
BUNDLE_CONTRACT_VERSION = "sciencevision.expert_exchange.v2"
ADMISSION_RECEIPT_SCHEMA_VERSION = "sciencevision.qa_export_admission_receipt.v1"
ADMISSION_RECEIPT_STATUS = "passed"
ADMISSION_RECEIPT_PURPOSE = "expert_review_distribution"
ADMISSION_RECEIPT_PURPOSES = frozenset({ADMISSION_RECEIPT_PURPOSE})
ADMISSION_REVISION_SCOPE = "active_source_task_revisions"
ADMISSION_REVISION_ALGORITHM = "sha256-canonical-json-lines-v1"
EXPERTS_SCHEMA_VERSION = "sciencevision.experts_csv.v1"
BATCH_SCHEMA_VERSION = "sciencevision.task_batch.v1"
ASSIGNMENT_SCHEMA_VERSION = "sciencevision.assignment.v1"
PACKAGE_SCHEMA_VERSION = "sciencevision.expert_package.v1"
TASK_SCHEMA_VERSION = "sciencevision.expert_task.v1"
SUBMISSION_SCHEMA_VERSION = "sciencevision.expert_submission.v1"
RESULT_SCHEMA_VERSION = "sciencevision.expert_result.v1"
IMPORT_REPORT_SCHEMA_VERSION = "sciencevision.result_import_report.v1"
BUNDLE_SCHEMA_VERSION = "sciencevision.expert_task_bundle.v2"
BUNDLE_TASK_SCHEMA_VERSION = "sciencevision.expert_task.v2"
BUNDLE_SUBMISSION_SCHEMA_VERSION = "sciencevision.expert_bundle_submission.v2"
BUNDLE_RESULT_SCHEMA_VERSION = "sciencevision.expert_bundle_result.v2"
LEGACY_BUNDLE_SCHEMA_VERSION = "sciencevision.legacy_expert_package_adapter.v1"
RUNTIME_SCHEMA_VERSION = "sciencevision.viewer_runtime.v1"
# Emitted by the browser trajectory drawer; see expert_app/mcp/PROVENANCE.md.
TRAJECTORY_SCHEMA_VERSION = "sciencevision.expert_trajectory.v1"
RUNTIME_LOADER_API_VERSION = "sciencevision.viewer_loader.v1"
MANAGER_RESULTS_EXPORT_SCHEMA_VERSION = "sciencevision.manager_results_export.v2"
MANAGER_RESULTS_EXCHANGE_VERSION = "sciencevision.manager_results_exchange.v2"

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HASH_ID_PATTERNS = {
    "assignment_id": re.compile(r"^asgn_[0-9a-f]{24}$"),
    "package_id": re.compile(r"^pkg_[0-9a-f]{24}$"),
    "submission_id": re.compile(r"^sub_[0-9a-f]{24}$"),
    "bundle_id": re.compile(r"^(?:bndl|pkg)_[0-9a-f]{24}$"),
    "runtime_id": re.compile(r"^vrt_[0-9a-f]{24}$"),
}
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_RESERVED_ASSET_TOP_LEVEL = frozenset(
    {"asset-source", "oracle", "qc", "quarantine", "sources", "tasks", "uploads", "viewer"}
)
_RESERVED_ASSET_FILES = frozenset(
    {
        "expert-app.js",
        "i18n.js",
        "index.html",
        "package_manifest.json",
        "readme.md",
        "serve_package.py",
        "sha256sums",
        "styles.css",
    }
)


class ContractError(ValueError):
    """Raised when an exchange object violates the offline contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def object_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError(f"{field} must be a lowercase SHA-256")
    return value


def require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise ContractError(
            f"{field} must match {IDENTIFIER_PATTERN.pattern}; display names are not IDs"
        )
    return value


def require_hash_identifier(value: Any, field: str) -> str:
    value = require_identifier(value, field)
    pattern = HASH_ID_PATTERNS[field]
    if not pattern.fullmatch(value):
        raise ContractError(f"{field} must match {pattern.pattern}")
    return value


def normalize_domains(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[;,|]", value)
    elif isinstance(value, list):
        raw = value
    else:
        raise ContractError("domains must be a delimited string or list")
    domains = sorted({str(item).strip().lower() for item in raw if str(item).strip()})
    if not domains:
        raise ContractError("at least one domain is required")
    return domains


def task_revision_sha256(task: dict[str, Any]) -> str:
    return object_sha256(task)


def active_source_task_revisions(tasks: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract direct task revisions without unfolding nested review wrappers."""

    revisions: dict[str, str] = {}
    for row_number, task in enumerate(tasks, 1):
        task_id = require_identifier(task.get("task_id"), f"task row {row_number} task_id")
        source_line_sha256 = require_sha256(
            task.get("source_revision_sha256"),
            f"task row {row_number} source_revision_sha256",
        )
        existing = revisions.get(task_id)
        if existing is not None and existing != source_line_sha256:
            raise ContractError(
                f"source task {task_id!r} has conflicting source_revision_sha256 values"
            )
        revisions[task_id] = source_line_sha256
    return [
        {"source_line_sha256": revisions[task_id], "task_id": task_id}
        for task_id in sorted(revisions)
    ]


def source_task_revision_set_digest(revisions: Iterable[dict[str, str]]) -> str:
    payload = "".join(canonical_json(row) + "\n" for row in revisions).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{field} must be an object")
    return value


def _require_nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be non-empty text")
    return value


def validate_qa_export_admission_receipt(
    receipt: dict[str, Any],
    *,
    tasks_path: Path,
    tasks: list[dict[str, Any]],
    expected_purpose: str = ADMISSION_RECEIPT_PURPOSE,
) -> dict[str, Any]:
    """Validate a ScienceVision admission decision without reimplementing its policy."""

    if not isinstance(receipt, dict):
        raise ContractError("admission receipt must be an object")
    expected_digest = require_sha256(receipt.get("receipt_digest"), "receipt_digest")
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if object_sha256(body) != expected_digest:
        raise ContractError("admission receipt digest mismatch")
    if receipt.get("schema_version") != ADMISSION_RECEIPT_SCHEMA_VERSION:
        raise ContractError("unsupported admission receipt schema_version")
    if receipt.get("status") != ADMISSION_RECEIPT_STATUS:
        raise ContractError("admission receipt status is not passed")
    if expected_purpose not in ADMISSION_RECEIPT_PURPOSES:
        raise ContractError(f"unsupported expected admission purpose {expected_purpose!r}")
    if receipt.get("purpose") != expected_purpose:
        raise ContractError(
            f"admission receipt purpose is not expected {expected_purpose!r}"
        )

    task_binding = _require_object(receipt.get("tasks"), "admission receipt tasks")
    _require_nonempty_text(task_binding.get("path"), "admission receipt tasks.path")
    count = task_binding.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ContractError("admission receipt tasks.count must be a positive integer")
    expected_tasks_sha256 = require_sha256(
        task_binding.get("sha256"), "admission receipt tasks.sha256"
    )
    if file_sha256(tasks_path) != expected_tasks_sha256:
        raise ContractError("admission receipt tasks.sha256 does not match tasks JSONL")
    if count != len(tasks):
        raise ContractError("admission receipt tasks.count does not match tasks JSONL")

    revision_binding = _require_object(
        receipt.get("task_revision_set"), "admission receipt task_revision_set"
    )
    if revision_binding.get("scope") != ADMISSION_REVISION_SCOPE:
        raise ContractError("unsupported admission receipt task revision scope")
    if revision_binding.get("algorithm") != ADMISSION_REVISION_ALGORITHM:
        raise ContractError("unsupported admission receipt task revision algorithm")
    revision_count = revision_binding.get("count")
    if not isinstance(revision_count, int) or isinstance(revision_count, bool) or revision_count < 1:
        raise ContractError(
            "admission receipt task_revision_set.count must be a positive integer"
        )
    expected_revision_digest = require_sha256(
        revision_binding.get("digest"), "admission receipt task_revision_set.digest"
    )
    revisions = active_source_task_revisions(tasks)
    if revision_count != len(revisions):
        raise ContractError("admission receipt task revision count does not match tasks JSONL")
    if source_task_revision_set_digest(revisions) != expected_revision_digest:
        raise ContractError("admission receipt task revision digest does not match tasks JSONL")

    _require_object(receipt.get("source_admission"), "admission receipt source_admission")
    policy = _require_object(receipt.get("policy"), "admission receipt policy")
    _require_nonempty_text(policy.get("name"), "admission receipt policy.name")
    _require_nonempty_text(policy.get("path"), "admission receipt policy.path")
    require_sha256(policy.get("sha256"), "admission receipt policy.sha256")
    _require_object(receipt.get("qc"), "admission receipt qc")
    admission_lock = _require_object(
        receipt.get("admission_lock"), "admission receipt admission_lock"
    )
    _require_nonempty_text(admission_lock.get("path"), "admission receipt admission_lock.path")
    require_sha256(admission_lock.get("sha256"), "admission receipt admission_lock.sha256")
    return receipt


def admission_manifest_provenance(receipt: dict[str, Any]) -> dict[str, Any]:
    """Return the non-sensitive admission binding safe to expose to experts."""

    return {
        "schema_version": receipt["schema_version"],
        "status": receipt["status"],
        "purpose": receipt["purpose"],
        "receipt_digest": receipt["receipt_digest"],
        "tasks": {
            "path": receipt["tasks"]["path"],
            "count": receipt["tasks"]["count"],
            "sha256": receipt["tasks"]["sha256"],
        },
        "task_revision_set": dict(receipt["task_revision_set"]),
        "policy": {
            "name": receipt["policy"]["name"],
            "sha256": receipt["policy"]["sha256"],
        },
        "admission_lock_sha256": receipt["admission_lock"]["sha256"],
    }


def stable_assignment_id(
    batch_id: str, expert_id: str, task_id: str, task_revision: str
) -> str:
    payload = [
        ASSIGNMENT_SCHEMA_VERSION,
        require_identifier(batch_id, "batch_id"),
        require_identifier(expert_id, "expert_id"),
        require_identifier(task_id, "task_id"),
        task_revision,
    ]
    return "asgn_" + object_sha256(payload)[:24]


def stable_package_id(
    batch_id: str, expert_id: str, assignments: Iterable[dict[str, Any]]
) -> str:
    rows = sorted(
        (
            str(row["assignment_id"]),
            str(row["task_id"]),
            str(row["task_revision_sha256"]),
        )
        for row in assignments
    )
    return "pkg_" + object_sha256(
        [PACKAGE_SCHEMA_VERSION, batch_id, expert_id, rows]
    )[:24]


def redact_task(task: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Project a source task onto the explicit expert-visible allowlist."""

    if not isinstance(task, dict):
        raise ContractError("task must be an object")
    text_fields = {
        "task_id",
        "title",
        "instruction",
        "question",
        "prompt",
        "description",
        "domain",
        "dataset_version",
        "difficulty",
        "v_class",
        "scientific_action",
    }
    object_allowlists = {
        "answer_schema": {"kind", "fields", "type", "required"},
        "terminal_answer": {"kind", "fields", "type", "must_be_derived_from_3d_interaction"},
        "tool_policy": {"allowed_tools", "forbidden_tools"},
        "trace_requirements": {"max_steps", "must_include_final_state", "must_save_operation_trace"},
        "active_vision_evidence": {"required", "optional", "role"},
        "target_selection": {"mask_id"},
    }
    asset_text_fields = {
        "asset_id",
        "candidate_label",
        "format",
        "mask_path",
        "mask_repo_path",
        "mask_sha256",
        "name",
        "path",
        "repo_path",
        "role",
        "sha256",
        "viewer",
    }
    asset_integer_fields = {"byte_size", "mask_byte_size"}
    asset_fields = asset_text_fields | asset_integer_fields | {"mask_id", "view_hint"}
    hash_fields = {"repo_path", "path", "sha256", "byte_size"}

    def text_list(value: Any, field: str) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ContractError(f"{field} must be a list of text values")
        return list(value)

    def project_object(key: str, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ContractError(f"{key} must be an object")
        projected: dict[str, Any] = {}
        if key in {"answer_schema", "terminal_answer"}:
            for child in ("kind", "type"):
                if child in value:
                    if not isinstance(value[child], str):
                        raise ContractError(f"{key}.{child} must be text")
                    projected[child] = value[child]
            for child in ("fields", "required"):
                if child in value:
                    projected[child] = text_list(value[child], f"{key}.{child}")
            flag = "must_be_derived_from_3d_interaction"
            if flag in value:
                if not isinstance(value[flag], bool):
                    raise ContractError(f"{key}.{flag} must be boolean")
                projected[flag] = value[flag]
        elif key == "tool_policy":
            for child in ("allowed_tools", "forbidden_tools"):
                if child in value:
                    projected[child] = text_list(value[child], f"{key}.{child}")
        elif key == "trace_requirements":
            if "max_steps" in value:
                max_steps = value["max_steps"]
                if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 0:
                    raise ContractError(f"{key}.max_steps must be a non-negative integer")
                projected["max_steps"] = max_steps
            for child in ("must_include_final_state", "must_save_operation_trace"):
                if child in value:
                    if not isinstance(value[child], bool):
                        raise ContractError(f"{key}.{child} must be boolean")
                    projected[child] = value[child]
        elif key == "active_vision_evidence":
            for child in ("required", "optional"):
                if child in value:
                    projected[child] = text_list(value[child], f"{key}.{child}")
            if "role" in value:
                if not isinstance(value["role"], str):
                    raise ContractError(f"{key}.role must be text")
                projected["role"] = value["role"]
        elif key == "target_selection" and "mask_id" in value:
            mask_id = value["mask_id"]
            if not isinstance(mask_id, (str, int)) or isinstance(mask_id, bool):
                raise ContractError(f"{key}.mask_id must be text or an integer")
            projected["mask_id"] = mask_id
        return projected

    def project_asset(value: Any, field: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ContractError(f"{field} must be an object")
        projected: dict[str, Any] = {}
        for child in asset_text_fields:
            if child in value:
                if not isinstance(value[child], str):
                    raise ContractError(f"{field}.{child} must be text")
                projected[child] = value[child]
        for child in asset_integer_fields:
            if child in value:
                number = value[child]
                if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                    raise ContractError(f"{field}.{child} must be a non-negative integer")
                projected[child] = number
        if "mask_id" in value:
            mask_id = value["mask_id"]
            if not isinstance(mask_id, (str, int)) or isinstance(mask_id, bool):
                raise ContractError(f"{field}.mask_id must be text or an integer")
            projected["mask_id"] = mask_id
        if "view_hint" in value:
            view_hint = value["view_hint"]
            if not isinstance(view_hint, dict):
                raise ContractError(f"{field}.view_hint must be an object")
            if "cam" in view_hint:
                if not isinstance(view_hint["cam"], str):
                    raise ContractError(f"{field}.view_hint.cam must be text")
                projected["view_hint"] = {"cam": view_hint["cam"]}
        return projected

    redacted: dict[str, Any] = {}
    for key in text_fields:
        if key in task:
            if not isinstance(task[key], str):
                raise ContractError(f"{key} must be text")
            redacted[key] = task[key]
    if "required_evidence" in task:
        redacted["required_evidence"] = text_list(
            task["required_evidence"], "required_evidence"
        )
    for key in ("options", "answer_options"):
        value = task.get(key)
        if key in task and not isinstance(value, list):
            raise ContractError(f"{key} must be a list")
        if isinstance(value, list):
            safe_options: list[Any] = []
            for item in value:
                if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                    if isinstance(item, float) and not math.isfinite(item):
                        raise ContractError(f"{key} contains a non-finite number")
                    safe_options.append(copy.deepcopy(item))
                elif isinstance(item, dict):
                    option: dict[str, Any] = {}
                    for child in ("id", "label", "text", "value"):
                        if child not in item:
                            continue
                        child_value = item[child]
                        if not isinstance(child_value, (str, int, float)) or isinstance(
                            child_value, bool
                        ):
                            raise ContractError(f"{key}.{child} must be text or a number")
                        if isinstance(child_value, float) and not math.isfinite(child_value):
                            raise ContractError(f"{key}.{child} contains a non-finite number")
                        option[child] = copy.deepcopy(child_value)
                    safe_options.append(option)
                else:
                    raise ContractError(f"{key} contains an unsupported option")
            redacted[key] = safe_options
    for key in object_allowlists:
        if key in task:
            redacted[key] = project_object(key, task[key])
    if "asset" in task:
        redacted["asset"] = project_asset(task["asset"], "asset")
    if "assets" in task:
        if not isinstance(task["assets"], list):
            raise ContractError("assets must be a list")
        redacted["assets"] = [
            project_asset(item, f"assets[{index}]")
            for index, item in enumerate(task["assets"])
        ]
    if "asset_hashes" in task:
        if not isinstance(task["asset_hashes"], list):
            raise ContractError("asset_hashes must be a list")
        hashes: list[dict[str, Any]] = []
        for index, item in enumerate(task["asset_hashes"]):
            if not isinstance(item, dict):
                raise ContractError(f"asset_hashes[{index}] must be an object")
            projected_hash: dict[str, Any] = {}
            for child in hash_fields:
                if child not in item:
                    continue
                value = item[child]
                if child == "byte_size":
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        raise ContractError(
                            f"asset_hashes[{index}].byte_size must be a non-negative integer"
                        )
                elif not isinstance(value, str):
                    raise ContractError(f"asset_hashes[{index}].{child} must be text")
                projected_hash[child] = copy.deepcopy(value)
            hashes.append(projected_hash)
        redacted["asset_hashes"] = hashes

    kept_top_level = set(redacted)
    removed = sorted(key for key in task if key not in kept_top_level)
    # Record dropped nested fields without retaining their values.
    for key, allowed in object_allowlists.items():
        value = task.get(key)
        if isinstance(value, dict):
            removed.extend(f"{key}.{child}" for child in value if child not in allowed)
    for key in ("asset",):
        value = task.get(key)
        if isinstance(value, dict):
            removed.extend(f"{key}.{child}" for child in value if child not in asset_fields)
    for index, value in enumerate(task.get("assets", [])):
        if isinstance(value, dict):
            removed.extend(
                f"assets[{index}].{child}" for child in value if child not in asset_fields
            )
    return redacted, sorted(set(removed))


def safe_asset_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError("asset path must be a non-empty string")
    if raw != raw.strip() or "\\" in raw:
        raise ContractError(f"unsafe asset path: {raw!r}")
    text = raw
    if any(ord(character) < 32 for character in text) or any(
        character in text for character in "?#"
    ):
        raise ContractError(f"unsafe asset path: {raw!r}")
    if text.startswith("//") or "://" in text or _URI_SCHEME.match(text):
        raise ContractError(f"remote asset paths are forbidden: {raw!r}")
    relative = text[1:] if text.startswith("/") else text
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ContractError(f"unsafe asset path: {raw!r}")
    path = PurePosixPath(*parts)
    if path.parts[0].lower() in _RESERVED_ASSET_TOP_LEVEL:
        raise ContractError(f"asset path overlaps package control data: {raw!r}")
    if len(path.parts) == 1 and path.name.lower() in _RESERVED_ASSET_FILES:
        raise ContractError(f"asset path overlaps a package control file: {raw!r}")
    return path.as_posix()


def asset_records(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the declared path/hash closure for one task."""

    by_path: dict[str, dict[str, Any]] = {}

    def merge(path_value: Any, sha_value: Any, size_value: Any = None) -> None:
        if path_value in (None, ""):
            return
        path = safe_asset_path(path_value)
        sha = str(sha_value or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ContractError(f"asset {path!r} is missing a valid sha256")
        size: int | None = None
        if size_value not in (None, ""):
            if isinstance(size_value, bool):
                raise ContractError(f"asset {path!r} has an invalid byte_size")
            try:
                size = int(size_value)
            except (TypeError, ValueError) as error:
                raise ContractError(f"asset {path!r} has an invalid byte_size") from error
            if size < 0:
                raise ContractError(f"asset {path!r} has a negative byte_size")
        record = {"path": path, "sha256": sha, "byte_size": size}
        existing = by_path.get(path)
        if existing and existing != record:
            if existing["sha256"] != sha:
                raise ContractError(f"conflicting hashes declared for asset {path!r}")
            if existing["byte_size"] is None:
                existing["byte_size"] = size
            elif size is not None and existing["byte_size"] != size:
                raise ContractError(f"conflicting sizes declared for asset {path!r}")
        else:
            by_path[path] = record

    declared = task.get("asset_hashes")
    if not isinstance(declared, list) or not declared:
        raise ContractError("asset_hashes must be a non-empty list")
    for item in declared:
        if not isinstance(item, dict):
            raise ContractError("asset_hashes entries must include repo_path and sha256")
        merge(
            item.get("repo_path") or item.get("path"),
            item.get("sha256"),
            item.get("byte_size"),
        )
    declared_paths = set(by_path)

    objects: list[dict[str, Any]] = []
    if "asset" in task:
        if not isinstance(task["asset"], dict):
            raise ContractError("asset must be an object")
        objects.append(task["asset"])
    if "assets" in task:
        if not isinstance(task["assets"], list) or not all(
            isinstance(item, dict) for item in task["assets"]
        ):
            raise ContractError("assets must be a list of objects")
        objects.extend(task["assets"])
    if not objects:
        raise ContractError("task must declare at least one asset object")
    referenced_paths: set[str] = set()
    for item in objects:
        public_path = (
            safe_asset_path(item["path"]) if item.get("path") not in (None, "") else None
        )
        repo_path = (
            safe_asset_path(item["repo_path"])
            if item.get("repo_path") not in (None, "")
            else None
        )
        if public_path and repo_path and public_path != repo_path:
            raise ContractError("asset path and repo_path must identify the same local file")
        primary_path = repo_path or public_path
        if not primary_path:
            raise ContractError("asset object is missing path or repo_path")
        referenced_paths.add(primary_path)
        declared_record = by_path.get(primary_path, {})
        merge(
            primary_path,
            item.get("sha256") or declared_record.get("sha256"),
            item.get("byte_size", declared_record.get("byte_size")),
        )
        public_mask = (
            safe_asset_path(item["mask_path"])
            if item.get("mask_path") not in (None, "")
            else None
        )
        repo_mask = (
            safe_asset_path(item["mask_repo_path"])
            if item.get("mask_repo_path") not in (None, "")
            else None
        )
        if public_mask and repo_mask and public_mask != repo_mask:
            raise ContractError("mask_path and mask_repo_path must identify the same local file")
        mask_path = repo_mask or public_mask
        if mask_path:
            referenced_paths.add(mask_path)
            declared_mask = by_path.get(mask_path, {})
            merge(
                mask_path,
                item.get("mask_sha256") or declared_mask.get("sha256"),
                item.get("mask_byte_size", declared_mask.get("byte_size")),
            )

    if declared_paths != referenced_paths:
        extra = sorted(declared_paths - referenced_paths)
        missing = sorted(referenced_paths - declared_paths)
        details = []
        if extra:
            details.append("unreferenced: " + ", ".join(extra))
        if missing:
            details.append("undeclared: " + ", ".join(missing))
        raise ContractError("asset_hashes must exactly match public asset references (" + "; ".join(details) + ")")
    return [by_path[path] for path in sorted(by_path)]


def verify_manifest_hash(manifest: dict[str, Any]) -> str:
    expected = manifest.get("manifest_sha256")
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    actual = object_sha256(body)
    if expected != actual:
        raise ContractError("package manifest hash mismatch")
    return actual


def submission_payload_sha256(envelope: dict[str, Any]) -> str:
    body = {key: value for key, value in envelope.items() if key != "payload_sha256"}
    return object_sha256(body)
