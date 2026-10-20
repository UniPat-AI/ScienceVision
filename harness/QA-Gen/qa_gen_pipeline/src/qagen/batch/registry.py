"""Fail-closed orchestration skeleton for compiled selection batches.

The selection importer preserves historical source records for provenance, but
those records are not valid author prompts.  This module creates a separate
private batch plan in which every source slot is represented by an opaque job
identifier and a digest-bound viewer-native brief.  It intentionally has no
HTTP execution path: diagnostic dry-runs record readiness only and cannot
create a candidate, verification result, or release artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, digest_bytes, make_decision, make_envelope, require_digest, verify_envelope
from ..errors import GatePendingError, InputError, InvalidStateError, StaleInputError
from ..measurement import SceneInventoryError, load_v32_scene_inventory_batch
from ..paths import WorkspacePaths, contained_path, require_within, safe_component
from ..providers import diagnose_provider_runtime
from ..providers.contracts import ROLE_PROVIDER_MODELS
from ..resource_paths import PackageResourceError, config_path
from ..storage.atomic import atomic_write_json, hash_file, read_json
from ..verification import (
    FeedbackPolicyError,
    GroundTruthRegistry,
    GroundTruthRegistryError,
    load_ground_truth_registry,
    load_locked_feedback_policy,
    load_workspace_feedback_policy,
    make_locked_feedback_policy,
    resolve_ground_truth_record_projection,
    validate_ground_truth_record_projection,
)
from ..viewer.capability_mapping import (
    BASE_VIEWER_OPERATIONS,
    SUPPORTED_CAPABILITY_OPERATIONS,
    ViewerCapabilityMappingError,
    validate_capability_mapping_reference,
    validate_capability_receipt,
)
from ..viewer.batch_scene_replay import BatchSceneReplayError, load_attested_batch_replay_binding


class BatchError(InputError):
    """A compiled-selection batch cannot be prepared or resumed safely."""


_STAGES = (
    "brief_builder",
    "author",
    "truth_builder",
    "evidence_worker",
    "verifier",
    "rewriter",
)
_MAX_LOCK_BYTES = 2 * 1024 * 1024
_BATCH_THREAD_LOCK = threading.RLock()
_GENERATION_UNSUPPORTED_REASON = "no_live_worker_executor_is_connected_to_compiled_selection_batch"
_APPROVED_LOCK_FIELDS = frozenset({"status", "content_digest", "approval_digest", "approval_receipt"})
_APPROVAL_RECEIPT_REF_FIELDS = frozenset({"path", "content_digest"})
_APPROVAL_SCOPES = {
    "mapping": "compiled_selection_batch.mapping",
    "routing": "compiled_selection_batch.routing",
    "config": "compiled_selection_batch.config",
    "seed": "compiled_selection_batch.seed",
    "knowledge": "compiled_selection_batch.knowledge",
    "viewer": "compiled_selection_batch.viewer",
    "ground_truth_registry": "compiled_selection_batch.ground_truth_registry",
    "inventory_batch": "compiled_selection_batch.inventory_batch",
}
_SUPPORTED_VIEWER_OPERATIONS = SUPPORTED_CAPABILITY_OPERATIONS
_FORBIDDEN_PROJECTION_KEYS = frozenset(
    {
        "answer_fields",
        "answer_key",
        "answer_keys",
        "asset_path",
        "compute_packages",
        "forbidden_strategies",
        "has_compute_contract",
        "mask_asset",
        "primary_asset",
        "question_id",
        "raw_path",
        "reference_answer",
        "source_row",
        "task_id",
    }
)


def _short(value: str) -> str:
    return value.split(":", 1)[-1][:16]


def _schema(name: str) -> dict[str, Any]:
    try:
        path = config_path("schemas", name)
    except PackageResourceError as exc:
        raise BatchError(f"compiled-selection batch schema is unavailable: {name}") from exc
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise BatchError(f"compiled-selection batch schema must be an object: {name}")
    return dict(value)


def _validate_schema(value: Any, schema_name: str, label: str) -> None:
    errors = sorted(Draft7Validator(_schema(schema_name)).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise BatchError(f"{label} schema validation failed at {location}: {error.message}")


def _batch_root(paths: WorkspacePaths) -> Path:
    return paths.selection_batches / "compiled-runs"


def _plans_root(paths: WorkspacePaths) -> Path:
    return _batch_root(paths) / "plans"


def _approvals_root(paths: WorkspacePaths) -> Path:
    return _batch_root(paths) / "approvals"


def _runs_root(paths: WorkspacePaths) -> Path:
    return _batch_root(paths) / "runs"


def _ensure_batch_dirs(paths: WorkspacePaths) -> None:
    paths.ensure()
    for directory in (_batch_root(paths), _plans_root(paths), _approvals_root(paths), _runs_root(paths)):
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def _run_directory(paths: WorkspacePaths, run_id: str) -> Path:
    return _runs_root(paths) / safe_component(run_id, "compiled selection batch run id")


def _run_state_path(paths: WorkspacePaths, run_id: str) -> Path:
    return _run_directory(paths, run_id) / "state.json"


@contextmanager
def _locked_run(paths: WorkspacePaths, run_id: str):
    """Serialize one local batch-run transition across processes."""

    directory = _run_directory(paths, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".state.lock"
    with _BATCH_THREAD_LOCK:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _read_external_document(value: str | Path, *, label: str) -> tuple[Path, dict[str, Any], str]:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise BatchError(f"{label} cannot be a symlink")
    try:
        path = raw.resolve(strict=True)
    except OSError as exc:
        raise BatchError(f"cannot resolve {label}: {raw}") from exc
    if not path.is_file():
        raise BatchError(f"{label} is not a regular file: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise BatchError(f"cannot read {label}: {path}") from exc
    if len(content) > _MAX_LOCK_BYTES:
        raise BatchError(f"{label} exceeds {_MAX_LOCK_BYTES} byte limit")
    try:
        parsed = json.loads(content.decode("utf-8")) if path.suffix.lower() == ".json" else yaml.safe_load(content)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BatchError(f"cannot parse {label}: {path}: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise BatchError(f"{label} must be an object")
    return path, dict(parsed), digest_bytes(content)


def _load_workspace_envelope(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    label: str,
    kind: str,
) -> tuple[Path, dict[str, Any]]:
    path = _workspace_path(paths, value, label)
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise BatchError(f"{label} must be an object")
    try:
        return path, verify_envelope(raw, kind=kind)
    except InputError as exc:
        raise BatchError(f"{label} is invalid: {exc}") from exc


def _workspace_path(paths: WorkspacePaths, value: str | Path, label: str) -> Path:
    """Resolve either a CLI absolute path or a persisted workspace-relative ref."""

    if not isinstance(value, (str, Path)):
        raise BatchError(f"{label} must be a path")
    raw = Path(value).expanduser()
    return require_within(paths.root, raw, label) if raw.is_absolute() else contained_path(paths.root, raw)


def _require_approved_lock(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _APPROVED_LOCK_FIELDS:
        raise BatchError(f"{label} must be an approved digest lock with a local approval receipt")
    if value.get("status") != "approved":
        raise GatePendingError(f"{label} is not approved")
    receipt = value.get("approval_receipt")
    if not isinstance(receipt, Mapping) or set(receipt) != _APPROVAL_RECEIPT_REF_FIELDS:
        raise BatchError(f"{label} approval receipt reference is malformed")
    receipt_path = receipt.get("path")
    if not isinstance(receipt_path, str) or not receipt_path.strip() or "\x00" in receipt_path:
        raise BatchError(f"{label} approval receipt path is invalid")
    try:
        return {
            "status": "approved",
            "content_digest": require_digest(value.get("content_digest"), f"{label}.content_digest"),
            "approval_digest": require_digest(value.get("approval_digest"), f"{label}.approval_digest"),
            "approval_receipt": {
                "path": receipt_path,
                "content_digest": require_digest(
                    receipt.get("content_digest"), f"{label}.approval_receipt.content_digest"
                ),
            },
        }
    except InputError as exc:
        raise BatchError(str(exc)) from exc


def _approval_receipt_path(
    reference: Mapping[str, Any],
    *,
    label: str,
    receipt_root: Path | None,
) -> Path:
    raw_value = reference.get("path")
    if not isinstance(raw_value, str) or not raw_value.strip() or "\x00" in raw_value:
        raise BatchError(f"{label} path is invalid")
    raw = Path(raw_value).expanduser()
    if raw.is_absolute():
        candidate = raw
    elif receipt_root is not None:
        candidate = receipt_root / raw
    else:
        raise BatchError(f"{label} path must be absolute after a batch plan is persisted")
    if candidate.is_symlink():
        raise BatchError(f"{label} cannot be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BatchError(f"cannot resolve {label}: {candidate}") from exc
    if receipt_root is not None and not raw.is_absolute():
        try:
            resolved.relative_to(receipt_root.resolve())
        except ValueError as exc:
            raise BatchError(f"{label} escapes the lock bundle directory") from exc
    return resolved


def _require_receipt_time(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatchError(f"{label} must be a non-empty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BatchError(f"{label} must include a timezone")
    return value


def _validated_approved_lock(
    value: Any,
    *,
    label: str,
    lock_kind: str,
    receipt_root: Path | None,
) -> dict[str, Any]:
    """Require a local receipt that approves this exact lock digest and scope."""

    if lock_kind not in _APPROVAL_SCOPES:
        raise BatchError(f"unsupported compiled-selection lock kind: {lock_kind}")
    lock = _require_approved_lock(value, label)
    receipt_path = _approval_receipt_path(
        lock["approval_receipt"],
        label=f"{label} approval receipt",
        receipt_root=receipt_root,
    )
    _, receipt, _ = _read_external_document(receipt_path, label=f"{label} approval receipt")
    _validate_schema(
        receipt,
        "compiled_selection_batch_lock_approval.schema.json",
        f"{label} approval receipt",
    )
    receipt_digest = digest(receipt)
    if lock["approval_digest"] != receipt_digest or lock["approval_receipt"]["content_digest"] != receipt_digest:
        raise StaleInputError(f"{label} approval receipt digest does not match its lock")
    if receipt.get("decision") != "approved":
        raise GatePendingError(f"{label} approval receipt is not approved")
    if receipt.get("scope") != _APPROVAL_SCOPES[lock_kind]:
        raise BatchError(f"{label} approval receipt scope does not match the lock kind")
    if receipt.get("subject_digest") != lock["content_digest"]:
        raise StaleInputError(f"{label} approval receipt is bound to another subject digest")
    reviewer = receipt.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise BatchError(f"{label} approval receipt reviewer is invalid")
    _require_receipt_time(receipt.get("reviewed_at"), f"{label} approval receipt reviewed_at")
    return {
        **lock,
        "approval_receipt": {
            "path": str(receipt_path),
            "content_digest": receipt_digest,
        },
    }


def _validated_ground_truth_registry_lock(
    paths: WorkspacePaths,
    value: Any,
    *,
    receipt_root: Path | None,
) -> dict[str, Any]:
    """Attest a human-approved GT registry before importing its private copy.

    The external lock bundle is an ingestion boundary.  A prepared plan must
    never retain that external path as a runtime dependency, so this helper
    validates the source document and returns its normalized contents for a
    later workspace-local snapshot.
    """

    expected_fields = {"status", "path", "content_digest", "approval_digest", "approval_receipt"}
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise BatchError("ground-truth registry lock fields are invalid")
    base = _validated_approved_lock(
        {field: value.get(field) for field in _APPROVED_LOCK_FIELDS},
        label="ground-truth registry lock",
        lock_kind="ground_truth_registry",
        receipt_root=receipt_root,
    )
    source_path = _approval_receipt_path(
        value,
        label="ground-truth registry source",
        receipt_root=receipt_root,
    )
    _read_external_document(source_path, label="ground-truth registry source")
    try:
        registry = load_ground_truth_registry(source_path, expected_digest=base["content_digest"])
    except GroundTruthRegistryError as exc:
        raise BatchError("ground-truth registry source is invalid") from exc
    if not registry.approved or not registry.records:
        raise GatePendingError("ground-truth registry source is not approved")
    return {
        **base,
        "source_path": source_path,
        "document": deepcopy(dict(registry.document)),
    }


def _persist_ground_truth_registry(
    paths: WorkspacePaths,
    lock: Mapping[str, Any],
) -> dict[str, Any]:
    """Copy a checked registry into the private plan area and rebind its digest."""

    content_digest = lock.get("content_digest")
    try:
        require_digest(content_digest, "ground-truth registry content digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    document = lock.get("document")
    if not isinstance(document, Mapping):
        raise BatchError("ground-truth registry lock has no validated document")
    target = _plans_root(paths) / "ground-truth" / f"{_short(str(content_digest))}.registry.json"
    if target.exists():
        try:
            existing = load_ground_truth_registry(target, expected_digest=str(content_digest))
        except GroundTruthRegistryError as exc:
            raise StaleInputError("persisted ground-truth registry changed") from exc
        if dict(existing.document) != dict(document):
            raise StaleInputError("persisted ground-truth registry differs from its approved source")
    else:
        atomic_write_json(target, dict(document), private=True)
    return {
        "path": target.relative_to(paths.root).as_posix(),
        "content_digest": str(content_digest),
        "approval_digest": str(lock["approval_digest"]),
        "approval_receipt": deepcopy(dict(lock["approval_receipt"])),
    }


def _validated_plan_ground_truth_registry_lock(paths: WorkspacePaths, value: Any) -> dict[str, Any]:
    """Revalidate the imported registry and its human approval on plan load."""

    required = {"path", "content_digest", "approval_digest", "approval_receipt"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise BatchError("batch plan ground-truth registry lock is malformed")
    base = _validated_approved_lock(
        {
            "status": "approved",
            "content_digest": value.get("content_digest"),
            "approval_digest": value.get("approval_digest"),
            "approval_receipt": value.get("approval_receipt"),
        },
        label="batch plan ground-truth registry lock",
        lock_kind="ground_truth_registry",
        receipt_root=None,
    )
    path = _workspace_path(paths, value.get("path"), "batch plan ground-truth registry")
    try:
        registry = load_ground_truth_registry(path, expected_digest=base["content_digest"])
    except GroundTruthRegistryError as exc:
        raise StaleInputError("batch plan ground-truth registry changed") from exc
    if not registry.approved or not registry.records:
        raise GatePendingError("batch plan ground-truth registry is not approved")
    return {
        "path": path.relative_to(paths.root).as_posix(),
        "content_digest": base["content_digest"],
        "approval_digest": base["approval_digest"],
        "approval_receipt": deepcopy(dict(base["approval_receipt"])),
    }


def _executor_build_receipt_path(
    paths: WorkspacePaths,
    reference: Any,
    *,
    receipt_root: Path | None,
) -> tuple[Path, str]:
    """Resolve an executor-build receipt without allowing it to escape the workspace."""

    if not isinstance(reference, Mapping) or set(reference) != _APPROVAL_RECEIPT_REF_FIELDS:
        raise BatchError("executor build receipt reference is malformed")
    raw_digest = reference.get("content_digest")
    try:
        expected_digest = require_digest(raw_digest, "executor build receipt content digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    raw_path = reference.get("path")
    if receipt_root is None:
        path = _workspace_path(paths, raw_path, "executor build receipt")
    else:
        path = _approval_receipt_path(reference, label="executor build receipt", receipt_root=receipt_root)
    try:
        require_within(paths.root, path, "executor build receipt")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    return path, expected_digest


def _validated_viewer_runtime_installation(
    paths: WorkspacePaths,
    value: Any,
    *,
    selection_batch_digest: str,
    verify_runtime: bool,
) -> dict[str, Any]:
    """Validate a private, path-free browser installation receipt.

    Receipt construction records only the trusted browser source, version, and
    binary digest.  At live execution the explicitly pinned local executable
    is re-hashed, so a later cache replacement cannot silently alter a run.
    """

    if not isinstance(value, Mapping) or set(value) != _APPROVAL_RECEIPT_REF_FIELDS:
        raise BatchError("viewer runtime installation receipt reference is malformed")
    path = _workspace_path(paths, value.get("path"), "viewer runtime installation receipt")
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise BatchError("viewer runtime installation receipt is invalid")
    try:
        receipt = verify_envelope(raw, kind="qagen_viewer_runtime_installation_receipt")
        expected_digest = require_digest(value.get("content_digest"), "viewer runtime installation receipt digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    if receipt["content_digest"] != expected_digest:
        raise StaleInputError("viewer runtime installation receipt digest changed")
    if receipt.get("status") != "ready":
        raise GatePendingError("viewer runtime installation is not ready")
    body = receipt.get("body")
    if not isinstance(body, Mapping):
        raise BatchError("viewer runtime installation receipt body is invalid")
    required = {
        "scope",
        "selection_batch_digest",
        "viewer_host_id",
        "playwright_version",
        "browser_source",
        "browser_binary_digest",
        "browser_version",
        "replay_summary_digest",
    }
    if not required.issubset(body) or body.get("scope") != "local_preproduction_fresh5_only":
        raise BatchError("viewer runtime installation receipt has an invalid scope")
    if body.get("selection_batch_digest") != selection_batch_digest:
        raise StaleInputError("viewer runtime installation belongs to another selection batch")
    if body.get("viewer_host_id") != "compiled-selection-batch-browser-replay-v1":
        raise BatchError("viewer runtime installation host is invalid")
    if body.get("browser_source") not in {"playwright_cache_explicit", "system_chrome_explicit"}:
        raise BatchError("viewer runtime installation browser source is invalid")
    if not all(isinstance(body.get(field), str) and body.get(field) for field in ("playwright_version", "browser_version")):
        raise BatchError("viewer runtime installation version evidence is invalid")
    try:
        require_digest(body.get("browser_binary_digest"), "viewer runtime installation browser binary digest")
        require_digest(body.get("replay_summary_digest"), "viewer runtime installation replay summary digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    if verify_runtime:
        try:
            from importlib.metadata import version

            from ..viewer.batch_scene_host import explicit_browser_installation_identity

            actual = explicit_browser_installation_identity()
            playwright_version = version("playwright")
        except Exception as exc:
            raise GatePendingError("explicit approved viewer runtime is unavailable") from exc
        if (
            actual["browser_source"] != body["browser_source"]
            or actual["browser_binary_digest"] != body["browser_binary_digest"]
            or actual["browser_version"] != body["browser_version"]
            or playwright_version != body["playwright_version"]
        ):
            raise StaleInputError("live viewer runtime differs from the approved installation receipt")
    return {
        "path": path.relative_to(paths.root).as_posix(),
        "content_digest": expected_digest,
    }


def _validated_executor_build_lock(
    paths: WorkspacePaths,
    value: Any,
    *,
    selection_batch_digest: str,
    receipt_root: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the exact local build that is allowed to execute a live batch.

    The receipt deliberately names the parent lock bundle rather than the
    derived bundle which carries it; requiring the latter would create a
    circular content-addressing dependency.  The derived bundle and plan bind
    the receipt's digest afterwards.
    """

    if not isinstance(value, Mapping) or set(value) != {"receipt"}:
        raise BatchError("executor build lock fields are invalid")
    receipt_path, expected_digest = _executor_build_receipt_path(
        paths,
        value.get("receipt"),
        receipt_root=receipt_root,
    )
    _, raw_receipt, _ = _read_external_document(receipt_path, label="executor build receipt")
    try:
        receipt = verify_envelope(raw_receipt, kind="qagen_executor_build_receipt")
    except InputError as exc:
        raise BatchError(f"executor build receipt is invalid: {exc}") from exc
    if receipt["content_digest"] != expected_digest:
        raise StaleInputError("executor build receipt digest does not match its lock")
    if receipt.get("status") != "attested_local_preproduction":
        raise GatePendingError("executor build receipt is not attested for local preproduction")
    body = receipt.get("body")
    if not isinstance(body, Mapping):
        raise BatchError("executor build receipt body is invalid")
    required = {
        "scope",
        "git_head",
        "source_tree_digest",
        "source_tree_file_count",
        "git_status_digest",
        "git_diff_head_digest",
        "runtime_config_digest",
        "selection_batch_digest",
        "base_lock_bundle_digest",
        "test_evidence",
        "provider_execution_policy",
    }
    if not required.issubset(body):
        raise BatchError("executor build receipt is missing required identity evidence")
    if body.get("scope") != "local_preproduction_fresh5_only":
        raise BatchError("executor build receipt has an invalid execution scope")
    git_head = body.get("git_head")
    if not isinstance(git_head, str) or len(git_head) != 40 or any(char not in "0123456789abcdef" for char in git_head):
        raise BatchError("executor build receipt git head is invalid")
    file_count = body.get("source_tree_file_count")
    if not isinstance(file_count, int) or isinstance(file_count, bool) or file_count < 1:
        raise BatchError("executor build receipt source tree file count is invalid")
    try:
        for field in (
            "source_tree_digest",
            "git_status_digest",
            "git_diff_head_digest",
            "runtime_config_digest",
            "selection_batch_digest",
            "base_lock_bundle_digest",
        ):
            require_digest(body.get(field), f"executor build receipt {field}")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    if body.get("selection_batch_digest") != selection_batch_digest:
        raise StaleInputError("executor build receipt is bound to another selection batch")
    viewer_runtime = body.get("viewer_runtime_installation")
    if viewer_runtime is not None:
        _validated_viewer_runtime_installation(
            paths,
            viewer_runtime,
            selection_batch_digest=selection_batch_digest,
            verify_runtime=False,
        )
    evidence = body.get("test_evidence")
    policy = body.get("provider_execution_policy")
    if (
        not isinstance(evidence, Mapping)
        or not isinstance(evidence.get("command"), str)
        or not evidence.get("command")
        or not isinstance(evidence.get("result"), str)
        or not evidence.get("result")
        or not isinstance(policy, Mapping)
        or policy.get("live_only_after_no_network_diagnostic") is not True
        or policy.get("release_allowed") is not False
    ):
        raise BatchError("executor build receipt execution policy is invalid")
    try:
        receipt_relative = receipt_path.relative_to(paths.root).as_posix()
    except ValueError as exc:
        raise BatchError("executor build receipt escapes the private workspace") from exc
    return ({"receipt": {"path": receipt_relative, "content_digest": expected_digest}}, dict(body))


def _local_executor_source_tree_identity() -> tuple[str, int]:
    """Hash the editable local package tree used by this preproduction executor."""

    source_root = Path(__file__).resolve().parents[3]
    candidates: list[Path] = []
    for relative_root in ("src", "configs"):
        directory = source_root / relative_root
        if not directory.is_dir() or directory.is_symlink():
            raise BatchError("local executor source tree is unavailable")
        for path in directory.rglob("*"):
            if "__pycache__" in path.parts or any(part.endswith(".egg-info") for part in path.parts):
                continue
            if path.is_symlink():
                raise BatchError("local executor source tree contains a symlink")
            if path.is_file():
                candidates.append(path)
    pyproject = source_root / "pyproject.toml"
    if not pyproject.is_file() or pyproject.is_symlink():
        raise BatchError("local executor project metadata is unavailable")
    candidates.append(pyproject)
    lines: list[str] = []
    for path in sorted(candidates, key=lambda item: item.relative_to(source_root).as_posix()):
        try:
            contents = path.read_bytes()
        except OSError as exc:
            raise BatchError("cannot read local executor source tree") from exc
        relative = path.relative_to(source_root).as_posix()
        lines.append(f"{sha256(contents).hexdigest()}  {relative}\n")
    return digest_bytes("".join(lines).encode("utf-8")), len(lines)


def validate_executor_build_runtime(
    paths: WorkspacePaths,
    *,
    plan: Mapping[str, Any],
    runtime_config_digest: str,
) -> dict[str, Any] | None:
    """Fail closed when a live run is not using its approved local source tree."""

    body = plan.get("body")
    if not isinstance(body, Mapping):
        raise BatchError("batch plan body is malformed while checking executor identity")
    raw_lock = body.get("executor_build")
    if raw_lock is None:
        return None
    selection = body.get("selection_batch")
    if not isinstance(selection, Mapping):
        raise BatchError("batch plan selection binding is malformed while checking executor identity")
    selection_digest = selection.get("content_digest")
    try:
        require_digest(selection_digest, "batch plan selection digest")
        require_digest(runtime_config_digest, "live runtime configuration digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    lock, receipt = _validated_executor_build_lock(
        paths,
        raw_lock,
        selection_batch_digest=str(selection_digest),
        receipt_root=None,
    )
    if receipt["runtime_config_digest"] != runtime_config_digest:
        raise StaleInputError("executor build receipt is bound to another provider runtime")
    source_tree_digest, source_tree_file_count = _local_executor_source_tree_identity()
    if receipt["source_tree_digest"] != source_tree_digest or receipt["source_tree_file_count"] != source_tree_file_count:
        raise StaleInputError("live executor source tree differs from the approved build receipt")
    viewer_runtime = receipt.get("viewer_runtime_installation")
    if viewer_runtime is not None:
        _validated_viewer_runtime_installation(
            paths,
            viewer_runtime,
            selection_batch_digest=str(selection_digest),
            verify_runtime=True,
        )
    return lock


def _inventory_batch_lock(
    paths: WorkspacePaths,
    value: Any,
    *,
    selection_batch_digest: str,
    expected_slot_digests: set[str],
    receipt_root: Path | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load one approved, content-addressed V32 inventory-batch receipt.

    The batch receipt is deliberately explicit: it is never selected by a
    directory scan or file recency.  Each compiled selection slot must occur
    exactly once as a ready result, so a live job later resolves only the
    inventory digest locked through this receipt.
    """

    if not isinstance(value, Mapping) or set(value) != _APPROVED_LOCK_FIELDS | {"batch_receipt"}:
        raise BatchError("inventory batch lock fields do not match the approved contract")
    base = _validated_approved_lock(
        {key: value.get(key) for key in _APPROVED_LOCK_FIELDS},
        label="inventory batch lock",
        lock_kind="inventory_batch",
        receipt_root=receipt_root,
    )
    reference = value.get("batch_receipt")
    if not isinstance(reference, Mapping) or set(reference) != _APPROVAL_RECEIPT_REF_FIELDS:
        raise BatchError("inventory batch receipt reference is malformed")
    raw_path = reference.get("path")
    raw_digest = reference.get("content_digest")
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        raise BatchError("inventory batch receipt path is invalid")
    try:
        receipt_digest = require_digest(raw_digest, "inventory batch receipt content digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    if receipt_digest != base["content_digest"]:
        raise StaleInputError("inventory batch receipt digest differs from its approved lock")
    try:
        loaded = load_v32_scene_inventory_batch(
            paths,
            raw_path,
            expected_content_digest=receipt_digest,
        )
    except SceneInventoryError as exc:
        raise GatePendingError("approved inventory batch receipt is unavailable or stale") from exc
    body = loaded["body"]
    if (
        loaded["status"] != "ready"
        or body.get("selection_batch_digest") != selection_batch_digest
        or body.get("mapping_complete") is not True
        or body.get("blocked_count") != 0
        or body.get("leak_scan", {}).get("status") != "passed"
    ):
        raise GatePendingError("approved inventory batch is not ready for the compiled selection")
    results = body.get("results")
    if not isinstance(results, list) or len(results) != len(expected_slot_digests):
        raise GatePendingError("approved inventory batch does not cover the compiled selection")
    by_slot: dict[str, dict[str, Any]] = {}
    for raw_result in results:
        if not isinstance(raw_result, Mapping) or raw_result.get("status") != "ready":
            raise GatePendingError("approved inventory batch contains a non-ready slot")
        slot_digest = raw_result.get("selection_slot_digest")
        if not isinstance(slot_digest, str) or slot_digest in by_slot:
            raise GatePendingError("approved inventory batch has an invalid slot binding")
        try:
            require_digest(slot_digest, "inventory batch selection slot digest")
            require_digest(raw_result.get("inventory_digest"), "inventory batch scene inventory digest")
            require_digest(raw_result.get("source_record_digest"), "inventory batch source record digest")
        except InputError as exc:
            raise BatchError(str(exc)) from exc
        scene_ref = raw_result.get("anonymous_scene_ref")
        if not isinstance(scene_ref, str) or not scene_ref.startswith("scene://"):
            raise GatePendingError("approved inventory batch has an invalid scene binding")
        by_slot[slot_digest] = dict(raw_result)
    if set(by_slot) != expected_slot_digests:
        raise GatePendingError("approved inventory batch slots differ from the compiled selection")
    try:
        private_path = loaded["path"].relative_to(paths.private).as_posix()
    except ValueError as exc:
        raise BatchError("approved inventory batch receipt escapes the private workspace") from exc
    return (
        {
            **base,
            "batch_receipt": {"path": private_path, "content_digest": receipt_digest},
        },
        by_slot,
    )


def _manifest_rows(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = manifest.get("body")
    if not isinstance(body, Mapping):
        raise BatchError("compiled selection manifest body must be an object")
    _validate_schema(dict(body), "selection_batch_manifest.schema.json", "compiled selection manifest")
    rows = body.get("rows")
    if not isinstance(rows, list) or body.get("total") != len(rows):
        raise BatchError("compiled selection manifest total does not match its rows")
    coverage = body.get("mapping_coverage")
    if not isinstance(coverage, Mapping) or coverage.get("unmapped_source_leaves"):
        raise GatePendingError("compiled selection batch has unmapped source leaves")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise BatchError("compiled selection row must be an object")
        row = dict(raw)
        slot_ref = row.get("slot_ref")
        if not isinstance(slot_ref, Mapping):
            raise BatchError("compiled selection row lacks a source-slot reference")
        slot_digest = slot_ref.get("content_digest")
        try:
            require_digest(slot_digest, "compiled selection row slot digest")
        except InputError as exc:
            raise BatchError(str(exc)) from exc
        if slot_digest in seen:
            raise BatchError("compiled selection has duplicate source-slot digests")
        seen.add(str(slot_digest))
        if row.get("mapping_status") != "mapped" or not isinstance(row.get("target_leaf"), str):
            raise GatePendingError("compiled selection has an unmapped or missing target leaf")
        if not isinstance(row.get("target_leaf_config"), Mapping):
            raise GatePendingError("compiled selection target leaf config is missing")
        access_track = row.get("access_track")
        if not isinstance(access_track, Mapping) or access_track.get("source") not in {"raw_compute", "viewer_measurement"}:
            raise BatchError("compiled selection row has an invalid source access track")
        if access_track.get("effective") != access_track.get("source") or access_track.get("conversion") != "preserved":
            raise BatchError("compiled selection row changes its source access track")
        assets = row.get("assets")
        if not isinstance(assets, list) or not assets:
            raise BatchError("compiled selection row has no attested assets")
        result.append(row)
    return sorted(result, key=lambda item: str(item["slot_ref"]["content_digest"]))


def _ground_truth_context(row: Mapping[str, Any], *, anonymous_scene_ref: Any) -> dict[str, str]:
    """Bind a selected GT record to the exact current scene and primary asset."""

    slot_ref = row.get("slot_ref")
    assets = row.get("assets")
    if not isinstance(slot_ref, Mapping) or not isinstance(assets, list):
        raise BatchError("compiled selection row is malformed for ground-truth binding")
    try:
        slot_digest = require_digest(slot_ref.get("content_digest"), "ground-truth selection slot digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    if not isinstance(anonymous_scene_ref, str) or not anonymous_scene_ref.strip():
        raise BatchError("ground-truth binding has no anonymous scene reference")
    primary = [asset for asset in assets if isinstance(asset, Mapping) and asset.get("role") == "primary"]
    if len(primary) != 1:
        raise BatchError("ground-truth binding requires exactly one primary asset")
    raw_digest = primary[0].get("sha256")
    if not isinstance(raw_digest, str):
        raise BatchError("ground-truth binding primary asset digest is invalid")
    try:
        primary_digest = require_digest(f"sha256:{raw_digest}", "ground-truth primary asset digest")
    except InputError as exc:
        raise BatchError(str(exc)) from exc
    return {
        "anonymous_scene_ref": anonymous_scene_ref.strip(),
        "primary_asset_digest": primary_digest,
        "selection_slot_digest": slot_digest,
    }


def _ground_truth_projection_for_binding(
    *,
    registry: GroundTruthRegistry,
    binding: Mapping[str, Any],
    row: Mapping[str, Any],
    anonymous_scene_ref: Any,
) -> dict[str, Any]:
    identity = binding.get("ground_truth_record")
    if not isinstance(identity, Mapping):
        raise GatePendingError("batch job binding requires a ground_truth_record identity")
    try:
        return resolve_ground_truth_record_projection(
            registry=registry,
            expected_registry_digest=registry.content_digest,
            identity=identity,
            expected_context=_ground_truth_context(row, anonymous_scene_ref=anonymous_scene_ref),
        )
    except GroundTruthRegistryError as exc:
        raise BatchError("batch job ground-truth record is invalid or belongs to another selected scene") from exc


def _routing_roles(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise BatchError("approved routing roles must be a list")
    by_role: dict[str, dict[str, str]] = {}
    for raw in value:
        if not isinstance(raw, Mapping):
            raise BatchError("approved routing role must be an object")
        role = raw.get("role")
        if role not in ROLE_PROVIDER_MODELS or role in by_role:
            raise BatchError("approved routing roles must contain each supported role exactly once")
        expected_provider, expected_model = ROLE_PROVIDER_MODELS[role]
        if raw.get("provider") != expected_provider or raw.get("model") != expected_model:
            raise BatchError(f"approved routing route differs from provider contract for {role}")
        try:
            by_role[role] = {
                "role": role,
                "provider": expected_provider,
                "model": expected_model,
                "prompt_digest": require_digest(raw.get("prompt_digest"), f"routing {role}.prompt_digest"),
            }
        except InputError as exc:
            raise BatchError(str(exc)) from exc
    if set(by_role) != set(_STAGES):
        missing = sorted(set(_STAGES) - set(by_role))
        extra = sorted(set(by_role) - set(_STAGES))
        raise BatchError(f"approved routing roles mismatch: missing={missing}, extra={extra}")
    return [by_role[role] for role in _STAGES]


def _viewer_lock(
    value: Any,
    *,
    paths: WorkspacePaths | None = None,
    selection_batch_digest: str | None = None,
    source_asset_paths: set[str],
    receipt_root: Path | None,
    source_row: Mapping[str, Any] | None = None,
    generation_strategy: str | None = None,
    required_operations: set[str] | None = None,
    domain_allowed_operations: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BatchError("viewer lock must be an object")
    expected = {
        "status",
        "content_digest",
        "approval_digest",
        "approval_receipt",
        "anonymous_scene_ref",
        "allowed_operations",
        "capability_mapping",
    }
    raw_fields = set(value)
    if raw_fields != expected and raw_fields != expected | {"fresh_replay"}:
        raise BatchError("viewer lock fields do not match the approved contract")
    base = _validated_approved_lock(
        {key: value.get(key) for key in _APPROVED_LOCK_FIELDS},
        label="viewer lock",
        lock_kind="viewer",
        receipt_root=receipt_root,
    )
    scene_ref = value.get("anonymous_scene_ref")
    operations = value.get("allowed_operations")
    if not isinstance(scene_ref, str) or not scene_ref.startswith("scene://") or len(scene_ref) <= len("scene://"):
        raise BatchError("viewer lock must contain an opaque scene:// reference")
    if any(path and path in scene_ref for path in source_asset_paths):
        raise BatchError("viewer anonymous scene reference leaks a source asset path")
    if not isinstance(operations, list) or not operations or not all(isinstance(item, str) and item for item in operations):
        raise BatchError("viewer lock allowed_operations must be a non-empty string list")
    operation_set = set(operations)
    if len(operation_set) != len(operations) or "render" not in operation_set:
        raise BatchError("viewer lock must include a unique render operation")
    unsupported = sorted(operation_set - _SUPPORTED_VIEWER_OPERATIONS)
    if unsupported:
        raise BatchError(f"viewer lock contains unsupported operations: {unsupported}")
    try:
        mapping_reference = validate_capability_mapping_reference(value.get("capability_mapping"))
        receipt_path = _approval_receipt_path(
            mapping_reference["receipt"],
            label="viewer capability receipt",
            receipt_root=receipt_root,
        )
        _, receipt, _ = _read_external_document(receipt_path, label="viewer capability receipt")
        receipt_digest = digest(receipt)
        if receipt_digest != mapping_reference["receipt"]["content_digest"]:
            raise StaleInputError("viewer capability receipt digest does not match its mapping reference")
        normalized_mapping = {
            **mapping_reference,
            "receipt": {"path": str(receipt_path), "content_digest": receipt_digest},
        }
        if (
            source_row is not None
            and generation_strategy is not None
            and required_operations is not None
            and domain_allowed_operations is not None
        ):
            validate_capability_receipt(
                receipt,
                reference=normalized_mapping,
                receipt_digest=receipt_digest,
                row=source_row,
                generation_strategy=generation_strategy,
                target_leaf=str(source_row.get("target_leaf", "")),
                target_required_operations=required_operations,
                domain_allowed_operations=domain_allowed_operations,
                viewer_lock_content_digest=base["content_digest"],
                lock_allowed_operations=operation_set,
            )
    except ViewerCapabilityMappingError as exc:
        raise GatePendingError(f"viewer capability mapping is blocked: {exc}") from exc
    result = {
        **base,
        "anonymous_scene_ref": scene_ref,
        "allowed_operations": sorted(operation_set),
        "capability_mapping": normalized_mapping,
    }
    if "fresh_replay" in value:
        if paths is None or selection_batch_digest is None:
            raise BatchError("viewer fresh replay binding requires a workspace and selection batch digest")
        try:
            bound = load_attested_batch_replay_binding(
                paths,
                fresh_replay=value["fresh_replay"],
                selection_batch_digest=selection_batch_digest,
                slot_digest=str(source_row.get("slot_ref", {}).get("content_digest")) if isinstance(source_row, Mapping) else "",
                anonymous_scene_ref=scene_ref,
                allowed_operations=sorted(operation_set),
            )
        except (BatchSceneReplayError, InputError) as exc:
            raise GatePendingError("viewer fresh replay binding is unavailable or stale") from exc
        result["fresh_replay"] = {
            "lock_set": dict(bound["lock_set"]),
            "registry": dict(bound["registry"]),
            "receipt": dict(bound["receipt"]),
        }
    return result


def _viewer_hint_operations(row: Mapping[str, Any]) -> set[str]:
    hint = row.get("viewer_hint")
    if not isinstance(hint, Mapping) or set(hint) != {"source_viewer", "allowed_operations"}:
        raise BatchError("compiled selection row viewer_hint is malformed")
    source_viewer = hint.get("source_viewer")
    if not isinstance(source_viewer, str) or not source_viewer.strip():
        raise BatchError("compiled selection row viewer_hint source_viewer is invalid")
    operations = hint.get("allowed_operations")
    if (
        not isinstance(operations, list)
        or not operations
        or not all(isinstance(item, str) and item for item in operations)
        or len(set(operations)) != len(operations)
    ):
        raise BatchError("compiled selection row viewer_hint allowed_operations are invalid")
    aliases = {
        "viewer_rotate": "rotate",
        "viewer_zoom": "zoom",
        "viewer_measure_distance": "measure_distance",
        "viewer_measure_dihedral": "measure_dihedral",
        "viewer_measure_area": "measure_area",
        "viewer_measure_volume": "measure_volume",
        "viewer_slice": "slice",
        "viewer_window_level": "window_level",
        "viewer_bbox": "bbox",
    }
    normalized = {aliases.get(item, item) for item in operations}
    unsupported = sorted(normalized - _SUPPORTED_VIEWER_OPERATIONS)
    if unsupported:
        raise BatchError(f"compiled selection row viewer_hint has unsupported operations: {unsupported}")
    # Rendering is a base controlled-viewer primitive, not a legacy source tool
    # grant. Only preserve_intent routes consume this historical hint.
    return normalized | set(BASE_VIEWER_OPERATIONS)


def _target_leaf_policy(
    row: Mapping[str, Any],
    *,
    generation_strategy: str,
) -> dict[str, Any]:
    """Resolve one leaf's baseline and route-specific viewer policy.

    A capability mode is not an all-of extension to the leaf's baseline
    operations.  The frozen batch route selects exactly one mode from the
    leaf configuration.  Requiring a unique match here keeps prepare-time and
    reload-time capability receipt validation identical and fail-closed.
    """

    target_leaf = row.get("target_leaf")
    config_lock = row.get("target_leaf_config")
    if not isinstance(target_leaf, str) or target_leaf.count(".") != 1 or not isinstance(config_lock, Mapping):
        raise BatchError("compiled selection row target leaf configuration is malformed")
    if set(config_lock) != {"path", "content_sha256"}:
        raise BatchError("compiled selection row target leaf configuration fields are invalid")
    domain, subdomain = target_leaf.split(".", 1)
    expected_path = f"configs/subdomains/{domain}/{subdomain}.yaml"
    if config_lock.get("path") != expected_path:
        raise StaleInputError("compiled selection row target leaf config path does not match its target leaf")
    try:
        expected_digest = require_digest(config_lock.get("content_sha256"), "target leaf config content_sha256")
        leaf_path = config_path("subdomains", domain, f"{subdomain}.yaml")
        domain_path = config_path("domains", f"{domain}.yaml")
    except (InputError, PackageResourceError) as exc:
        raise BatchError(f"compiled selection target leaf configuration is unavailable for {target_leaf}") from exc
    if hash_file(leaf_path) != expected_digest:
        raise StaleInputError("compiled selection row target leaf config digest changed")
    try:
        leaf = yaml.safe_load(leaf_path.read_text(encoding="utf-8"))
        domain_config = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BatchError(f"cannot read target leaf policy for {target_leaf}") from exc
    if not isinstance(leaf, Mapping) or not isinstance(domain_config, Mapping):
        raise BatchError(f"target leaf policy for {target_leaf} must be an object")
    _validate_schema(dict(leaf), "subdomain.schema.json", f"target leaf config {target_leaf}")
    _validate_schema(dict(domain_config), "domain.schema.json", f"target domain config {domain}")
    if leaf.get("id") != target_leaf or leaf.get("domain") != domain:
        raise StaleInputError("target leaf config identity does not match the compiled selection row")
    catalog = domain_config.get("catalog_leaves")
    if not isinstance(catalog, list) or target_leaf not in catalog:
        raise StaleInputError("target leaf is not present in its domain policy catalog")
    viewer = leaf.get("viewer")
    policy = domain_config.get("policy")
    if not isinstance(viewer, Mapping) or not isinstance(policy, Mapping):
        raise BatchError(f"target leaf viewer policy is malformed for {target_leaf}")
    if generation_strategy not in {"preserve_intent", "reauthor_on_asset"}:
        raise BatchError(f"target leaf generation strategy is invalid for {target_leaf}")
    required = viewer.get("required_operations")
    domain_allowed = policy.get("allowed_viewer_operations")
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(item, str) and item for item in required)
        or len(set(required)) != len(required)
    ):
        raise BatchError(f"target leaf required viewer operations are invalid for {target_leaf}")
    if (
        not isinstance(domain_allowed, list)
        or not domain_allowed
        or not all(isinstance(item, str) and item for item in domain_allowed)
        or len(set(domain_allowed)) != len(domain_allowed)
    ):
        raise BatchError(f"target domain viewer operations are invalid for {target_leaf}")
    required_set = set(required)
    capability_mode_id: str | None = None
    modes = viewer.get("capability_modes")
    if modes is not None:
        if not isinstance(modes, list) or not modes:
            raise BatchError(f"target leaf viewer capability modes are invalid for {target_leaf}")
        matched_modes: list[tuple[str, set[str]]] = []
        seen_ids: set[str] = set()
        for index, raw_mode in enumerate(modes):
            if not isinstance(raw_mode, Mapping):
                raise BatchError(f"target leaf viewer capability mode {index} is invalid for {target_leaf}")
            mode_id = raw_mode.get("id")
            strategies = raw_mode.get("generation_strategies")
            mode_operations = raw_mode.get("required_operations")
            if (
                not isinstance(mode_id, str)
                or not mode_id
                or mode_id in seen_ids
                or not isinstance(strategies, list)
                or not strategies
                or not all(
                    isinstance(item, str) and item in {"preserve_intent", "reauthor_on_asset"}
                    for item in strategies
                )
                or len(set(strategies)) != len(strategies)
                or not isinstance(mode_operations, list)
                or not mode_operations
                or not all(isinstance(item, str) and item for item in mode_operations)
                or len(set(mode_operations)) != len(mode_operations)
            ):
                raise BatchError(f"target leaf viewer capability mode {index} is invalid for {target_leaf}")
            seen_ids.add(mode_id)
            if generation_strategy in strategies:
                matched_modes.append((mode_id, set(mode_operations)))
        if len(matched_modes) != 1:
            raise GatePendingError(
                f"target leaf does not define exactly one viewer capability mode for {generation_strategy}: {target_leaf}"
            )
        capability_mode_id, mode_operations = matched_modes[0]
        required_set |= mode_operations
    domain_allowed_set = set(domain_allowed) | set(BASE_VIEWER_OPERATIONS)
    if not required_set.issubset(domain_allowed_set):
        raise BatchError(f"target leaf viewer operations exceed its domain policy for {target_leaf}")
    return {
        "config_digest": expected_digest,
        "required_operations": required_set,
        "domain_allowed_operations": domain_allowed_set,
        "capability_mode_id": capability_mode_id,
    }


def _feedback_policy_lock_for_binding(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    knowledge_digest: str,
    question_family_id: Any,
) -> dict[str, Any] | None:
    """Resolve the accepted policy from the exact approved knowledge snapshot.

    A generic digest lock may describe an older diagnostic fixture and have no
    locally resolved snapshot.  That state remains readable, but it cannot
    enter live generation.  Once an approved snapshot carries a feedback
    policy, selecting a concrete family is mandatory before any author call.
    """

    try:
        policy = load_workspace_feedback_policy(
            paths,
            leaf_id=leaf_id,
            expected_knowledge_snapshot_digest=knowledge_digest,
        )
    except FeedbackPolicyError as exc:
        raise BatchError("locked knowledge feedback policy is invalid") from exc
    if policy is None:
        if question_family_id is not None:
            raise BatchError("batch question_family_id requires a locked knowledge feedback policy")
        return None
    if not isinstance(question_family_id, str) or not question_family_id:
        raise GatePendingError("accepted knowledge feedback policy requires question_family_id before authoring")
    try:
        return make_locked_feedback_policy(
            policy,
            knowledge_snapshot_digest=knowledge_digest,
            question_family_id=question_family_id,
        )
    except FeedbackPolicyError as exc:
        raise BatchError("batch question_family_id is not allowed by the locked feedback policy") from exc


def _revalidate_job_feedback_policy(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    knowledge_digest: str,
    lock: Any,
) -> dict[str, Any] | None:
    """Prove the plan copy is still exactly the policy in its knowledge lock."""

    if lock is None:
        try:
            current = load_workspace_feedback_policy(
                paths,
                leaf_id=leaf_id,
                expected_knowledge_snapshot_digest=knowledge_digest,
            )
        except FeedbackPolicyError as exc:
            raise BatchError("locked knowledge feedback policy is invalid") from exc
        if current is not None:
            raise StaleInputError("batch plan omitted the accepted knowledge feedback policy")
        return None
    try:
        persisted = load_locked_feedback_policy(
            lock,
            leaf_id=leaf_id,
            expected_knowledge_snapshot_digest=knowledge_digest,
        )
        current = load_workspace_feedback_policy(
            paths,
            leaf_id=leaf_id,
            expected_knowledge_snapshot_digest=knowledge_digest,
        )
    except FeedbackPolicyError as exc:
        raise BatchError("batch plan feedback-policy lock is invalid") from exc
    if current is None or current.content_digest != persisted.policy.content_digest or current.document != persisted.policy.document:
        raise StaleInputError("batch plan feedback-policy lock differs from the approved knowledge snapshot")
    return make_locked_feedback_policy(
        persisted.policy,
        knowledge_snapshot_digest=knowledge_digest,
        question_family_id=persisted.question_family_id,
    )


def _load_lock_bundle(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    manifest: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    bundle_path, bundle, _ = _read_external_document(value, label="compiled-selection batch lock bundle")
    _validate_schema(bundle, "compiled_selection_batch_locks.schema.json", "compiled-selection batch lock bundle")
    if bundle.get("selection_batch_digest") != manifest.get("content_digest"):
        raise BatchError("batch lock bundle is bound to another compiled selection manifest")
    body = manifest["body"]
    ground_truth_registry = _validated_ground_truth_registry_lock(
        paths,
        bundle.get("ground_truth_registry"),
        receipt_root=bundle_path.parent,
    )
    try:
        registered_truth = load_ground_truth_registry(
            ground_truth_registry["source_path"],
            expected_digest=ground_truth_registry["content_digest"],
        )
    except GroundTruthRegistryError as exc:
        raise BatchError("ground-truth registry changed while resolving job bindings") from exc
    mapping = _validated_approved_lock(
        bundle.get("mapping"),
        label="taxonomy mapping lock",
        lock_kind="mapping",
        receipt_root=bundle_path.parent,
    )
    taxonomy = body.get("taxonomy_mapping")
    if not isinstance(taxonomy, Mapping) or mapping["content_digest"] != taxonomy.get("content_digest"):
        raise StaleInputError("taxonomy mapping lock does not match the compiled selection manifest")
    routing_raw = bundle.get("routing")
    if not isinstance(routing_raw, Mapping):
        raise BatchError("batch routing lock must be an object")
    routing_base = _validated_approved_lock(
        {key: routing_raw.get(key) for key in _APPROVED_LOCK_FIELDS},
        label="routing lock",
        lock_kind="routing",
        receipt_root=bundle_path.parent,
    )
    roles = _routing_roles(routing_raw.get("roles"))
    budget = bundle.get("budget")
    retry_policy = bundle.get("retry_policy")
    if not isinstance(budget, Mapping) or not isinstance(retry_policy, Mapping):
        raise BatchError("batch budget and retry policy must be objects")
    bindings_raw = bundle.get("job_bindings")
    if not isinstance(bindings_raw, list):
        raise BatchError("batch job_bindings must be a list")
    manifest_by_slot = {str(row["slot_ref"]["content_digest"]): row for row in rows}
    inventory_batch: dict[str, Any] | None = None
    inventory_by_slot: dict[str, dict[str, Any]] = {}
    if "inventory_batch" in bundle:
        inventory_batch, inventory_by_slot = _inventory_batch_lock(
            paths,
            bundle.get("inventory_batch"),
            selection_batch_digest=str(manifest["content_digest"]),
            expected_slot_digests=set(manifest_by_slot),
            receipt_root=bundle_path.parent,
        )
    executor_build: dict[str, Any] | None = None
    if "executor_build" in bundle:
        executor_build, _receipt_body = _validated_executor_build_lock(
            paths,
            bundle.get("executor_build"),
            selection_batch_digest=str(manifest["content_digest"]),
            receipt_root=bundle_path.parent,
        )
    bindings: dict[str, dict[str, Any]] = {}
    for raw in bindings_raw:
        if not isinstance(raw, Mapping):
            raise BatchError("batch job binding must be an object")
        binding = dict(raw)
        slot_digest = binding.get("slot_digest")
        if not isinstance(slot_digest, str) or slot_digest not in manifest_by_slot or slot_digest in bindings:
            raise BatchError("batch job binding does not name exactly one compiled source slot")
        row = manifest_by_slot[slot_digest]
        if binding.get("target_leaf") != row.get("target_leaf"):
            raise StaleInputError("batch job binding target leaf differs from compiled selection")
        source_assets = row["assets"]
        source_asset_digest = digest(source_assets)
        if binding.get("source_asset_set_digest") != source_asset_digest:
            raise StaleInputError("batch job binding asset set differs from the attested source slot")
        source_track = row["access_track"]["source"]
        strategy = binding.get("strategy")
        manifest_strategy = row.get("generation_strategy")
        expected_strategy = "reauthor_on_asset" if source_track == "raw_compute" else "preserve_intent"
        if manifest_strategy != expected_strategy:
            raise StaleInputError("compiled selection row generation strategy does not match its source access track")
        if source_track == "raw_compute" and strategy != "reauthor_on_asset":
            raise GatePendingError("raw_compute source slots require strategy=reauthor_on_asset")
        if source_track == "viewer_measurement" and strategy != "preserve_intent":
            raise BatchError("viewer_measurement source slots require strategy=preserve_intent")
        asset_paths = {str(asset.get("path")) for asset in source_assets if isinstance(asset, Mapping)}
        target_policy = _target_leaf_policy(row, generation_strategy=strategy)
        config_lock = _validated_approved_lock(
            binding.get("config"),
            label="config lock",
            lock_kind="config",
            receipt_root=bundle_path.parent,
        )
        if config_lock["content_digest"] != target_policy["config_digest"]:
            raise StaleInputError("config lock does not match the manifest row target leaf config digest")
        viewer_lock = _viewer_lock(
            binding.get("viewer"),
            paths=paths,
            selection_batch_digest=str(manifest["content_digest"]),
            source_asset_paths=asset_paths,
            receipt_root=bundle_path.parent,
            source_row=row,
            generation_strategy=strategy,
            required_operations=target_policy["required_operations"],
            domain_allowed_operations=target_policy["domain_allowed_operations"],
        )
        ground_truth_record = _ground_truth_projection_for_binding(
            registry=registered_truth,
            binding=binding,
            row=row,
            anonymous_scene_ref=viewer_lock["anonymous_scene_ref"],
        )
        if inventory_batch is not None:
            result = inventory_by_slot[slot_digest]
            if result.get("anonymous_scene_ref") != viewer_lock["anonymous_scene_ref"]:
                raise StaleInputError("inventory batch scene binding differs from the approved viewer lock")
        knowledge_lock = _validated_approved_lock(
            binding.get("knowledge"),
            label="knowledge lock",
            lock_kind="knowledge",
            receipt_root=bundle_path.parent,
        )
        feedback_policy_lock = _feedback_policy_lock_for_binding(
            paths,
            leaf_id=str(binding["target_leaf"]),
            knowledge_digest=str(knowledge_lock["content_digest"]),
            question_family_id=binding.get("question_family_id"),
        )
        normalized_binding = {
            "slot_digest": slot_digest,
            "target_leaf": str(binding["target_leaf"]),
            "strategy": str(strategy),
            "source_asset_set_digest": source_asset_digest,
            "ground_truth_record": ground_truth_record,
            "config": config_lock,
            "seed": _validated_approved_lock(
                binding.get("seed"),
                label="seed lock",
                lock_kind="seed",
                receipt_root=bundle_path.parent,
            ),
            "knowledge": knowledge_lock,
            "viewer": viewer_lock,
        }
        if feedback_policy_lock is not None:
            normalized_binding["feedback_policy"] = feedback_policy_lock
        bindings[slot_digest] = normalized_binding
    if set(bindings) != set(manifest_by_slot):
        missing = sorted(set(manifest_by_slot) - set(bindings))
        extra = sorted(set(bindings) - set(manifest_by_slot))
        raise GatePendingError(f"batch lock bundle does not bind every source slot: missing={missing}, extra={extra}")
    return (
        {
            "ground_truth_registry": ground_truth_registry,
            "mapping": mapping,
            "routing": {**routing_base, "roles": roles},
            "inventory_batch": inventory_batch,
            "executor_build": executor_build,
            "budget": dict(budget),
            "retry_policy": dict(retry_policy),
            "bindings": bindings,
        },
        digest(bundle),
    )


def _walk_projection(value: Any, *, forbidden_values: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise BatchError("worker projection has a non-string key")
            if key.casefold() in _FORBIDDEN_PROJECTION_KEYS:
                raise BatchError(f"worker projection leaks forbidden field: {key}")
            _walk_projection(nested, forbidden_values=forbidden_values)
    elif isinstance(value, list):
        for nested in value:
            _walk_projection(nested, forbidden_values=forbidden_values)
    elif isinstance(value, str) and value in forbidden_values:
        raise BatchError("worker projection leaks an original source identifier or asset path")


def _question_family_projection(
    *,
    target_leaf: str,
    locks: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project the exact pre-author family without exposing truth content."""

    raw_policy = locks.get("feedback_policy")
    if raw_policy is None:
        return None
    knowledge = locks.get("knowledge")
    if not isinstance(knowledge, Mapping):
        raise BatchError("feedback-policy lock has no knowledge lock")
    try:
        locked = load_locked_feedback_policy(
            raw_policy,
            leaf_id=target_leaf,
            expected_knowledge_snapshot_digest=str(knowledge.get("content_digest")),
        )
    except FeedbackPolicyError as exc:
        raise BatchError("feedback-policy lock is invalid") from exc
    return {
        "id": locked.question_family_id,
        "kind": locked.question_family_kind,
        "feedback_policy_digest": locked.policy.content_digest,
        "allowed_gt_source_types": sorted(locked.policy.allowed_gt_source_types),
    }


def _project_job_inputs(
    *,
    job_id: str,
    target_leaf: str,
    strategy: str,
    locks: Mapping[str, Any],
    ground_truth_record: Mapping[str, Any],
    source_values: set[str],
) -> dict[str, dict[str, Any]]:
    viewer = locks["viewer"]
    shared = {
        "job_id": job_id,
        "target_leaf": target_leaf,
        "generation_strategy": strategy,
        "viewer": {
            "profile_digest": viewer["content_digest"],
            "capability_profile_id": viewer["capability_mapping"]["profile_id"],
            "capability_profile_digest": viewer["capability_mapping"]["profile_digest"],
            "anonymous_scene_ref": viewer["anonymous_scene_ref"],
            "allowed_operations": list(viewer["allowed_operations"]),
        },
        "approved_locks": {
            "config_digest": locks["config"]["content_digest"],
            "seed_digest": locks["seed"]["content_digest"],
            "knowledge_digest": locks["knowledge"]["content_digest"],
        },
        # This is the sole record an agent may cite.  It carries a digest and
        # answer form, never the answer value or an enumerable registry.
        "ground_truth_record": deepcopy(dict(ground_truth_record)),
        "viewer_native_required": True,
    }
    question_family = _question_family_projection(target_leaf=target_leaf, locks=locks)
    if question_family is not None:
        shared["question_family"] = question_family
    result = {
        "brief_builder": {
            "artifact_kind": "qagen_batch_brief_input",
            **shared,
            "constraints": ["viewer_native", "no_legacy_task_reuse", "no_raw_compute"],
        },
        "author": {
            "artifact_kind": "qagen_batch_author_input",
            **shared,
            "brief_dependency": {"role": "brief_builder"},
            "candidate_contract": "new_viewer_native_candidate_only",
        },
        "truth_builder": {
            "artifact_kind": "qagen_batch_truth_input",
            **shared,
            "candidate_dependency": {"role": "author"},
            "truth_scope": "new_candidate_only",
        },
        "evidence_worker": {
            "artifact_kind": "qagen_batch_evidence_input",
            **shared,
            "candidate_dependency": {"role": "author"},
            "evidence_scope": "controlled_viewer_only",
        },
        "verifier": {
            "artifact_kind": "qagen_batch_verifier_input",
            **shared,
            "candidate_dependency": {"role": "author"},
            "evidence_dependency": {"role": "evidence_worker"},
            "verification_scope": "independent_candidate_and_viewer_evidence",
        },
        "rewriter": {
            "artifact_kind": "qagen_batch_rewriter_input",
            **shared,
            "candidate_dependency": {"role": "author"},
            "revision_gate": "human_receipt_required",
        },
    }
    for projection in result.values():
        _walk_projection(projection, forbidden_values=source_values)
    return result


def _agent_reference(*, role: str, route: Mapping[str, str], input_projection: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "provider": route["provider"],
        "model": route["model"],
        "prompt_digest": route["prompt_digest"],
        "input_digest": digest(dict(input_projection)),
        "output_digest": None,
        "tokens": {"input": 0, "output": 0},
        "cost_usd": 0.0,
        "latency_ms": 0,
        "status": "planned",
        "retry_count": 0,
    }


def _make_job(
    *,
    ordinal: int,
    row: Mapping[str, Any],
    binding: Mapping[str, Any],
    routes: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    slot_digest = str(binding["slot_digest"])
    job_id = f"job-{ordinal:03d}-{_short(slot_digest)}"
    source_values = {
        str(row.get("question_id", "")),
        str(row.get("task_id", "")),
        *(str(asset.get("path", "")) for asset in row.get("assets", []) if isinstance(asset, Mapping)),
    }
    source_values.discard("")
    lock_bindings = {
        "config": deepcopy(binding["config"]),
        "seed": deepcopy(binding["seed"]),
        "knowledge": deepcopy(binding["knowledge"]),
        "viewer": deepcopy(binding["viewer"]),
    }
    if binding.get("feedback_policy") is not None:
        lock_bindings["feedback_policy"] = deepcopy(binding["feedback_policy"])
    ground_truth_record = binding.get("ground_truth_record")
    if not isinstance(ground_truth_record, Mapping):
        raise BatchError("batch job ground-truth record is malformed")
    question_family = _question_family_projection(
        target_leaf=str(binding["target_leaf"]),
        locks=lock_bindings,
    )
    inputs = _project_job_inputs(
        job_id=job_id,
        target_leaf=str(binding["target_leaf"]),
        strategy=str(binding["strategy"]),
        locks=lock_bindings,
        ground_truth_record=ground_truth_record,
        source_values=source_values,
    )
    references = {
        role: _agent_reference(role=role, route=routes[role], input_projection=inputs[role]) for role in _STAGES
    }
    job = {
        "job_id": job_id,
        "ordinal": ordinal,
        "slot_digest": slot_digest,
        "source_access_track": str(row["access_track"]["source"]),
        "target_leaf": str(binding["target_leaf"]),
        "generation_strategy": str(binding["strategy"]),
        "source_asset_set_digest": str(binding["source_asset_set_digest"]),
        "ground_truth_record": deepcopy(dict(ground_truth_record)),
        "lock_bindings": lock_bindings,
        "agent_inputs": inputs,
        "agent_references": references,
        "status": "queued",
    }
    if question_family is not None:
        job["question_family"] = {
            key: question_family[key]
            for key in ("id", "kind", "feedback_policy_digest")
        }
    return job


def _plan_path(paths: WorkspacePaths, plan_digest: str) -> Path:
    return _plans_root(paths) / f"compiled-selection-{_short(plan_digest)}.plan.json"


def prepare_batch(
    paths: WorkspacePaths,
    *,
    selection_batch_path: str | Path,
    lock_bundle_path: str | Path,
) -> dict[str, Any]:
    """Bind every compiled-selection slot to a private, viewer-native job.

    A selection manifest remains non-generation-ready by design.  This stage
    is the explicit bridge: it requires complete approved mappings and one
    approved config/seed/knowledge/viewer lock per source slot.
    """

    _ensure_batch_dirs(paths)
    manifest_path, manifest = _load_workspace_envelope(
        paths,
        selection_batch_path,
        label="compiled selection manifest",
        kind="vnext_selection_batch_manifest",
    )
    rows = _manifest_rows(manifest)
    locked, lock_bundle_digest = _load_lock_bundle(paths, lock_bundle_path, manifest=manifest, rows=rows)
    ground_truth_registry = _persist_ground_truth_registry(paths, locked["ground_truth_registry"])
    routes = {route["role"]: route for route in locked["routing"]["roles"]}
    jobs = [
        _make_job(
            ordinal=ordinal,
            row=row,
            binding=locked["bindings"][str(row["slot_ref"]["content_digest"])],
            routes=routes,
        )
        for ordinal, row in enumerate(rows, start=1)
    ]
    body = {
        "selection_batch": {
            "path": manifest_path.relative_to(paths.root).as_posix(),
            "content_digest": manifest["content_digest"],
        },
        "lock_bundle_digest": lock_bundle_digest,
        "ground_truth_registry": ground_truth_registry,
        "mapping": {
            "content_digest": locked["mapping"]["content_digest"],
            "approval_digest": locked["mapping"]["approval_digest"],
            "approval_receipt": deepcopy(locked["mapping"]["approval_receipt"]),
        },
        "routing": {
            "content_digest": locked["routing"]["content_digest"],
            "approval_digest": locked["routing"]["approval_digest"],
            "approval_receipt": deepcopy(locked["routing"]["approval_receipt"]),
            "roles": locked["routing"]["roles"],
        },
        "budget": locked["budget"],
        "retry_policy": locked["retry_policy"],
        "job_count": len(jobs),
        "jobs": jobs,
    }
    if locked["inventory_batch"] is not None:
        inventory_batch = locked["inventory_batch"]
        body["inventory_batch"] = {
            key: deepcopy(value)
            for key, value in inventory_batch.items()
            if key != "status"
        }
    if locked["executor_build"] is not None:
        body["executor_build"] = deepcopy(locked["executor_build"])
    _validate_schema(body, "compiled_selection_batch_plan.schema.json", "compiled-selection batch plan")
    plan = make_envelope(
        kind="compiled_selection_batch_plan",
        identifier=f"compiled-selection-{_short(manifest['content_digest'])}-{_short(lock_bundle_digest)}",
        status="prepared_pending_human_approval",
        body=body,
        producer="qagen/0.1.0:compiled_selection_batch",
    )
    path = _plan_path(paths, plan["content_digest"])
    atomic_write_json(path, plan, private=True)
    return {
        "status": plan["status"],
        "batch_plan_path": str(path),
        "batch_plan_digest": plan["content_digest"],
        "job_count": len(jobs),
        "generation_eligible": False,
        "generation_supported": False,
        "generation_support_reason": _GENERATION_UNSUPPORTED_REASON,
        "blockers": ["human_batch_plan_approval_required", "provider_runtime_diagnostic_required"],
    }


def _validate_job(
    job: Any,
    *,
    plan_status: bool,
    paths: WorkspacePaths | None = None,
    selection_batch_digest: str | None = None,
    manifest_row: Mapping[str, Any] | None = None,
    ground_truth_registry: GroundTruthRegistry | None = None,
    expected_ground_truth_registry_digest: str | None = None,
) -> dict[str, Any]:
    if not isinstance(job, Mapping):
        raise BatchError("batch job must be an object")
    value = deepcopy(dict(job))
    required = {
        "job_id",
        "ordinal",
        "slot_digest",
        "source_access_track",
        "target_leaf",
        "generation_strategy",
        "source_asset_set_digest",
        "ground_truth_record",
        "lock_bindings",
        "agent_inputs",
        "agent_references",
        "status",
    }
    optional = {"question_family"}
    if set(value) - required - optional or required - set(value):
        raise BatchError("batch job fields do not match the private contract")
    job_id = value.get("job_id")
    if not isinstance(job_id, str) or safe_component(job_id, "batch job id") != job_id:
        raise BatchError("batch job id is unsafe")
    if not isinstance(value.get("ordinal"), int) or isinstance(value["ordinal"], bool) or value["ordinal"] < 1:
        raise BatchError("batch job ordinal is invalid")
    for field in ("slot_digest", "source_asset_set_digest"):
        try:
            require_digest(value.get(field), f"batch job {field}")
        except InputError as exc:
            raise BatchError(str(exc)) from exc
    if value.get("source_access_track") not in {"raw_compute", "viewer_measurement"}:
        raise BatchError("batch job source access track is invalid")
    if not isinstance(value.get("target_leaf"), str) or "." not in value["target_leaf"]:
        raise BatchError("batch job target_leaf is invalid")
    if value.get("generation_strategy") not in {"preserve_intent", "reauthor_on_asset"}:
        raise BatchError("batch job generation strategy is invalid")
    raw_ground_truth_record = value.get("ground_truth_record")
    if ground_truth_registry is None or expected_ground_truth_registry_digest is None:
        # Legacy diagnostic plans intentionally remain readable. They cannot
        # reach live execution because that entry point requires a registry.
        if not isinstance(raw_ground_truth_record, Mapping):
            raise BatchError("batch job ground-truth record projection is malformed")
        ground_truth_record = deepcopy(dict(raw_ground_truth_record))
    else:
        try:
            ground_truth_record = validate_ground_truth_record_projection(
                projection=raw_ground_truth_record,
                registry=ground_truth_registry,
                expected_registry_digest=expected_ground_truth_registry_digest,
            )
        except GroundTruthRegistryError as exc:
            raise StaleInputError("batch job ground-truth record projection is invalid") from exc
    locks = value.get("lock_bindings")
    base_lock_keys = {"config", "seed", "knowledge", "viewer"}
    if not isinstance(locks, Mapping) or set(locks) - base_lock_keys - {"feedback_policy"} or base_lock_keys - set(locks):
        raise BatchError("batch job lock bindings are incomplete")
    normalized_locks = {
        "config": _validated_approved_lock(
            locks["config"],
            label="planned config lock",
            lock_kind="config",
            receipt_root=None,
        ),
        "seed": _validated_approved_lock(
            locks["seed"],
            label="planned seed lock",
            lock_kind="seed",
            receipt_root=None,
        ),
        "knowledge": _validated_approved_lock(
            locks["knowledge"],
            label="planned knowledge lock",
            lock_kind="knowledge",
            receipt_root=None,
        ),
        "viewer": _viewer_lock(
            locks["viewer"],
            paths=paths,
            selection_batch_digest=selection_batch_digest,
            source_asset_paths=set(),
            receipt_root=None,
            source_row=manifest_row,
        ),
    }
    feedback_lock = _revalidate_job_feedback_policy(
        paths,
        leaf_id=str(value["target_leaf"]),
        knowledge_digest=str(normalized_locks["knowledge"]["content_digest"]),
        lock=locks.get("feedback_policy"),
    ) if paths is not None else None
    if feedback_lock is not None:
        normalized_locks["feedback_policy"] = feedback_lock
    if manifest_row is not None and ground_truth_registry is not None:
        try:
            expected_context = _ground_truth_context(
                manifest_row,
                anonymous_scene_ref=normalized_locks["viewer"]["anonymous_scene_ref"],
            )
        except BatchError:
            raise
        if any(ground_truth_record["provenance"].get(field) != expected for field, expected in expected_context.items()):
            raise StaleInputError("batch job ground-truth record belongs to another selected scene")
    expected_question_family = _question_family_projection(
        target_leaf=str(value["target_leaf"]),
        locks=normalized_locks,
    )
    planned_question_family = value.get("question_family")
    if expected_question_family is None:
        if planned_question_family is not None:
            raise BatchError("batch job declares a question family without a locked feedback policy")
    elif planned_question_family != {
        key: expected_question_family[key]
        for key in ("id", "kind", "feedback_policy_digest")
    }:
        raise StaleInputError("batch job question family differs from its locked feedback policy")
    inputs = value.get("agent_inputs")
    refs = value.get("agent_references")
    if not isinstance(inputs, Mapping) or set(inputs) != set(_STAGES):
        raise BatchError("batch job agent inputs must bind every stage")
    if not isinstance(refs, Mapping) or set(refs) != set(_STAGES):
        raise BatchError("batch job agent references must bind every stage")
    for role in _STAGES:
        projection = inputs[role]
        if not isinstance(projection, Mapping):
            raise BatchError(f"batch job {role} input is malformed")
        _walk_projection(projection, forbidden_values=set())
        if expected_question_family is None:
            if "question_family" in projection:
                raise BatchError(f"batch job {role} input has an unlocked question family")
        elif projection.get("question_family") != expected_question_family:
            raise StaleInputError(f"batch job {role} input question family differs from its locked policy")
        if projection.get("ground_truth_record") != ground_truth_record:
            raise StaleInputError(f"batch job {role} input ground-truth record differs from its locked projection")
        ref = refs[role]
        if not isinstance(ref, Mapping):
            raise BatchError(f"batch job {role} agent reference is malformed")
        required_ref = {
            "role",
            "provider",
            "model",
            "prompt_digest",
            "input_digest",
            "output_digest",
            "tokens",
            "cost_usd",
            "latency_ms",
            "status",
            "retry_count",
        }
        if set(ref) != required_ref or ref.get("role") != role:
            raise BatchError(f"batch job {role} agent reference fields are invalid")
        expected_provider, expected_model = ROLE_PROVIDER_MODELS[role]
        if ref.get("provider") != expected_provider or ref.get("model") != expected_model:
            raise BatchError(f"batch job {role} route differs from provider contract")
        for field in ("prompt_digest", "input_digest"):
            try:
                require_digest(ref.get(field), f"batch job {role} {field}")
            except InputError as exc:
                raise BatchError(str(exc)) from exc
        if ref.get("input_digest") != digest(dict(projection)):
            raise StaleInputError(f"batch job {role} input digest changed")
        output_digest = ref.get("output_digest")
        if output_digest is not None:
            try:
                require_digest(output_digest, f"batch job {role} output_digest")
            except InputError as exc:
                raise BatchError(str(exc)) from exc
        tokens = ref.get("tokens")
        if (
            not isinstance(tokens, Mapping)
            or set(tokens) != {"input", "output"}
            or not all(isinstance(tokens[key], int) and not isinstance(tokens[key], bool) and tokens[key] >= 0 for key in tokens)
        ):
            raise BatchError(f"batch job {role} token accounting is invalid")
        if (
            not isinstance(ref.get("cost_usd"), (int, float))
            or isinstance(ref["cost_usd"], bool)
            or ref["cost_usd"] < 0
            or float(ref["cost_usd"]) != float(ref["cost_usd"])
            or float(ref["cost_usd"]) == float("inf")
        ):
            raise BatchError(f"batch job {role} cost accounting is invalid")
        if not isinstance(ref.get("latency_ms"), int) or isinstance(ref["latency_ms"], bool) or ref["latency_ms"] < 0:
            raise BatchError(f"batch job {role} latency accounting is invalid")
        if not isinstance(ref.get("retry_count"), int) or isinstance(ref["retry_count"], bool) or ref["retry_count"] < 0:
            raise BatchError(f"batch job {role} retry accounting is invalid")
        if plan_status and (ref.get("status") != "planned" or output_digest is not None):
            raise BatchError("prepared batch plan cannot contain an agent output")
    if plan_status and value.get("status") != "queued":
        raise BatchError("prepared batch plan job must be queued")
    value["lock_bindings"] = normalized_locks
    value["ground_truth_record"] = ground_truth_record
    return value


def _validate_job_manifest_binding(
    paths: WorkspacePaths,
    job: dict[str, Any],
    row: Mapping[str, Any],
    *,
    selection_batch_digest: str,
) -> None:
    """Rebind a persisted job to its exact current manifest row on every load."""

    slot_ref = row.get("slot_ref")
    access_track = row.get("access_track")
    assets = row.get("assets")
    if not isinstance(slot_ref, Mapping) or not isinstance(access_track, Mapping) or not isinstance(assets, list):
        raise BatchError("compiled selection row is malformed while validating a planned job")
    source_track = access_track.get("source")
    if job["slot_digest"] != slot_ref.get("content_digest"):
        raise StaleInputError("batch plan job is bound to a different manifest source slot")
    if job["source_access_track"] != source_track:
        raise StaleInputError("batch plan job source access track differs from its manifest row")
    if job["target_leaf"] != row.get("target_leaf"):
        raise StaleInputError("batch plan job target leaf differs from its manifest row")
    if job["source_asset_set_digest"] != digest(assets):
        raise StaleInputError("batch plan job source asset set differs from its manifest row")
    expected_strategy = "reauthor_on_asset" if source_track == "raw_compute" else "preserve_intent"
    if job["generation_strategy"] != expected_strategy:
        if source_track == "raw_compute":
            raise GatePendingError("raw_compute source slots require strategy=reauthor_on_asset")
        raise StaleInputError("viewer_measurement source slots require strategy=preserve_intent")
    policy = _target_leaf_policy(row, generation_strategy=str(job["generation_strategy"]))
    if job["lock_bindings"]["config"]["content_digest"] != policy["config_digest"]:
        raise StaleInputError("batch plan config lock does not match its manifest row target leaf config digest")
    source_asset_paths = {str(asset.get("path")) for asset in assets if isinstance(asset, Mapping)}
    job["lock_bindings"]["viewer"] = _viewer_lock(
        job["lock_bindings"]["viewer"],
        paths=paths,
        selection_batch_digest=selection_batch_digest,
        source_asset_paths=source_asset_paths,
        receipt_root=None,
        source_row=row,
        generation_strategy=job["generation_strategy"],
        required_operations=policy["required_operations"],
        domain_allowed_operations=policy["domain_allowed_operations"],
    )


def _validate_plan_body(paths: WorkspacePaths, body: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(body, Mapping):
        raise BatchError("compiled-selection batch plan body must be an object")
    plan_body = deepcopy(dict(body))
    _validate_schema(plan_body, "compiled_selection_batch_plan.schema.json", "compiled-selection batch plan")
    ground_truth_registry: GroundTruthRegistry | None = None
    if "ground_truth_registry" in plan_body:
        plan_body["ground_truth_registry"] = _validated_plan_ground_truth_registry_lock(
            paths,
            plan_body["ground_truth_registry"],
        )
        try:
            ground_truth_registry = load_ground_truth_registry(
                paths.root / str(plan_body["ground_truth_registry"]["path"]),
                expected_digest=str(plan_body["ground_truth_registry"]["content_digest"]),
            )
        except GroundTruthRegistryError as exc:
            raise StaleInputError("batch plan ground-truth registry changed") from exc
    selection_ref = plan_body["selection_batch"]
    if not isinstance(selection_ref, Mapping):
        raise BatchError("batch plan selection manifest reference is malformed")
    manifest_path = _workspace_path(paths, selection_ref.get("path"), "batch plan selection manifest")
    raw_manifest = read_json(manifest_path)
    if not isinstance(raw_manifest, Mapping):
        raise BatchError("batch plan selection manifest is not an object")
    try:
        manifest = verify_envelope(raw_manifest, kind="vnext_selection_batch_manifest")
    except InputError as exc:
        raise BatchError(f"batch plan selection manifest is invalid: {exc}") from exc
    if manifest["content_digest"] != selection_ref.get("content_digest"):
        raise StaleInputError("batch plan selection manifest digest changed")
    rows = _manifest_rows(manifest)
    jobs = plan_body.get("jobs")
    if not isinstance(jobs, list) or plan_body.get("job_count") != len(jobs) or len(jobs) != len(rows):
        raise BatchError("batch plan job count does not match its compiled selection")
    rows_by_slot = {str(row["slot_ref"]["content_digest"]): row for row in rows}
    seen_jobs: set[str] = set()
    seen_slots: set[str] = set()
    normalized_jobs: list[dict[str, Any]] = []
    for index, raw_job in enumerate(jobs, start=1):
        if not isinstance(raw_job, Mapping):
            raise BatchError("batch plan job must be an object")
        raw_slot = raw_job.get("slot_digest")
        manifest_row = rows_by_slot.get(raw_slot) if isinstance(raw_slot, str) else None
        job = _validate_job(
            raw_job,
            plan_status=True,
            paths=paths,
            selection_batch_digest=str(manifest["content_digest"]),
            manifest_row=manifest_row,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=(
                ground_truth_registry.content_digest if ground_truth_registry is not None else None
            ),
        )
        if job["ordinal"] != index or job["job_id"] in seen_jobs or job["slot_digest"] in seen_slots:
            raise BatchError("batch plan jobs are not in canonical unique order")
        row = rows_by_slot.get(job["slot_digest"])
        if row is None:
            raise StaleInputError("batch plan job is bound to an unknown compiled selection slot")
        _validate_job_manifest_binding(
            paths,
            job,
            row,
            selection_batch_digest=str(manifest["content_digest"]),
        )
        seen_jobs.add(job["job_id"])
        seen_slots.add(job["slot_digest"])
        normalized_jobs.append(job)
    plan_body["jobs"] = normalized_jobs
    manifest_slots = set(rows_by_slot)
    if seen_slots != manifest_slots:
        raise StaleInputError("batch plan jobs no longer bind the compiled selection slots")
    mapping = plan_body.get("mapping")
    taxonomy = manifest["body"].get("taxonomy_mapping")
    if not isinstance(mapping, Mapping) or not isinstance(taxonomy, Mapping):
        raise BatchError("batch plan mapping lock is malformed")
    if mapping.get("content_digest") != taxonomy.get("content_digest"):
        raise StaleInputError("batch plan mapping lock differs from the compiled selection")
    _validated_approved_lock(
        {"status": "approved", **dict(mapping)},
        label="batch plan mapping lock",
        lock_kind="mapping",
        receipt_root=None,
    )
    routing = plan_body.get("routing")
    if not isinstance(routing, Mapping):
        raise BatchError("batch plan routing lock is malformed")
    _validated_approved_lock(
        {"status": "approved", **{key: routing.get(key) for key in _APPROVED_LOCK_FIELDS if key != "status"}},
        label="batch plan routing lock",
        lock_kind="routing",
        receipt_root=None,
    )
    _routing_roles(routing.get("roles"))
    inventory_batch = plan_body.get("inventory_batch")
    if inventory_batch is not None:
        if not isinstance(inventory_batch, Mapping):
            raise BatchError("batch plan inventory batch lock is malformed")
        normalized_inventory, inventory_by_slot = _inventory_batch_lock(
            paths,
            {"status": "approved", **dict(inventory_batch)},
            selection_batch_digest=str(manifest["content_digest"]),
            expected_slot_digests=set(rows_by_slot),
            receipt_root=None,
        )
        for job in normalized_jobs:
            result = inventory_by_slot[job["slot_digest"]]
            viewer = job["lock_bindings"]["viewer"]
            if result.get("anonymous_scene_ref") != viewer["anonymous_scene_ref"]:
                raise StaleInputError("batch plan inventory scene binding differs from its viewer lock")
        plan_body["inventory_batch"] = {
            key: deepcopy(value)
            for key, value in normalized_inventory.items()
            if key != "status"
        }
    executor_build = plan_body.get("executor_build")
    if executor_build is not None:
        normalized_executor_build, _receipt_body = _validated_executor_build_lock(
            paths,
            executor_build,
            selection_batch_digest=str(manifest["content_digest"]),
            receipt_root=None,
        )
        plan_body["executor_build"] = normalized_executor_build
    return plan_body, manifest


def load_batch_plan(paths: WorkspacePaths, value: str | Path) -> dict[str, Any]:
    """Load and revalidate a private prepared compiled-selection plan."""

    _ensure_batch_dirs(paths)
    _path, plan = _load_workspace_envelope(
        paths,
        value,
        label="compiled-selection batch plan",
        kind="compiled_selection_batch_plan",
    )
    if plan.get("status") != "prepared_pending_human_approval":
        raise BatchError("compiled-selection batch plan has an unsupported status")
    _validate_plan_body(paths, plan["body"])
    return plan


def _approval_path(paths: WorkspacePaths, approval_digest: str) -> Path:
    return _approvals_root(paths) / f"compiled-selection-{_short(approval_digest)}.approval.json"


@contextmanager
def _locked_batch_plan_approval(paths: WorkspacePaths, plan_digest: str):
    """Serialize the one terminal approval decision for a batch plan."""

    directory = _approvals_root(paths)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f".{_short(plan_digest)}.approval.lock"
    with _BATCH_THREAD_LOCK:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _prior_batch_approvals(paths: WorkspacePaths, plan_digest: str) -> list[dict[str, Any]]:
    approvals: list[dict[str, Any]] = []
    for path in _approvals_root(paths).glob("*.approval.json"):
        try:
            value = read_json(path)
            if not isinstance(value, Mapping):
                continue
            approval = verify_envelope(value, kind="compiled_selection_batch_approval")
        except (InputError, OSError):
            continue
        body = approval.get("body")
        bound = body.get("bound_digests") if isinstance(body, Mapping) else None
        if isinstance(bound, Mapping) and bound.get("plan_digest") == plan_digest:
            approvals.append(approval)
    return approvals


def _ground_truth_record_projections_digest(body: Mapping[str, Any]) -> str | None:
    """Return a stable digest over every job's sole permitted GT record."""

    jobs = body.get("jobs")
    if not isinstance(jobs, list):
        raise BatchError("batch plan jobs are malformed for ground-truth approval binding")
    projections: list[dict[str, Any]] = []
    missing = 0
    for job in jobs:
        if not isinstance(job, Mapping):
            raise BatchError("batch plan job is malformed for ground-truth approval binding")
        record = job.get("ground_truth_record")
        if record is None:
            missing += 1
            continue
        if not isinstance(record, Mapping) or not isinstance(job.get("job_id"), str):
            raise BatchError("batch plan job ground-truth record is malformed")
        projections.append(
            {
                "job_id": job["job_id"],
                "slot_digest": job.get("slot_digest"),
                "ground_truth_record": dict(record),
            }
        )
    if missing == len(jobs):
        return None
    if missing:
        raise BatchError("batch plan ground-truth record projections are incomplete")
    return digest(projections)


def approve_batch_plan(
    paths: WorkspacePaths,
    *,
    plan_path: str | Path,
    reviewer: str,
    decision: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Issue a human batch-plan decision; only ``approved`` can start jobs."""

    if decision not in {"approved", "revise", "rejected", "quarantined"}:
        raise BatchError("batch plan decision is invalid")
    if decision in {"revise", "rejected"} and not rationale.strip():
        raise BatchError(f"batch plan decision {decision} requires rationale")
    initial = load_batch_plan(paths, plan_path)
    plan_digest = initial["content_digest"]
    with _locked_batch_plan_approval(paths, plan_digest):
        plan = load_batch_plan(paths, plan_path)
        if plan["content_digest"] != plan_digest:
            raise InvalidStateError("compiled-selection batch plan changed before its approval decision")
        if _prior_batch_approvals(paths, plan_digest):
            raise InvalidStateError("compiled-selection batch plan already has an immutable approval decision")
        body = plan["body"]
        bound_digests = {
            "plan_digest": plan_digest,
            "selection_batch_digest": body["selection_batch"]["content_digest"],
            "mapping_digest": body["mapping"]["content_digest"],
            "lock_bundle_digest": body["lock_bundle_digest"],
        }
        ground_truth_registry = body.get("ground_truth_registry")
        if ground_truth_registry is not None:
            if not isinstance(ground_truth_registry, Mapping):
                raise BatchError("batch plan ground-truth registry lock is malformed")
            try:
                bound_digests["ground_truth_registry_digest"] = require_digest(
                    ground_truth_registry.get("content_digest"),
                    "batch plan ground-truth registry digest",
                )
            except InputError as exc:
                raise BatchError(str(exc)) from exc
        ground_truth_record_digest = _ground_truth_record_projections_digest(body)
        if ground_truth_record_digest is not None:
            bound_digests["ground_truth_record_projections_digest"] = ground_truth_record_digest
        extra: dict[str, Any] = {"job_count": body["job_count"]}
        executor_build = body.get("executor_build")
        if executor_build is not None:
            receipt_ref = executor_build.get("receipt") if isinstance(executor_build, Mapping) else None
            if not isinstance(receipt_ref, Mapping):
                raise BatchError("batch plan executor build lock is malformed")
            executor_build_digest = receipt_ref.get("content_digest")
            try:
                bound_digests["executor_build_digest"] = require_digest(
                    executor_build_digest,
                    "batch plan executor build receipt digest",
                )
            except InputError as exc:
                raise BatchError(str(exc)) from exc
            extra["executor_build_receipt"] = deepcopy(dict(receipt_ref))
        receipt = make_decision(
            kind="compiled_selection_batch_approval",
            identifier=f"compiled-selection-approval-{secrets.token_hex(12)}",
            decision=decision,
            actor_id=reviewer,
            actor_role="plan_owner",
            bound_digests=bound_digests,
            rationale=rationale,
            extra=extra,
        )
        path = _approval_path(paths, receipt["content_digest"])
        if path.exists():
            raise InvalidStateError("compiled-selection batch approval receipt already exists")
        atomic_write_json(path, receipt, private=True)
        return {
            "status": decision,
            "batch_approval_path": str(path),
            "batch_approval_digest": receipt["content_digest"],
            "batch_plan_digest": plan_digest,
        }


def _load_approval(paths: WorkspacePaths, value: str | Path, *, plan: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    path, approval = _load_workspace_envelope(
        paths,
        value,
        label="compiled-selection batch approval",
        kind="compiled_selection_batch_approval",
    )
    if approval.get("status") != "approved":
        raise GatePendingError("compiled-selection batch approval is not approved")
    body = approval.get("body")
    if not isinstance(body, Mapping) or body.get("decision") != "approved":
        raise GatePendingError("compiled-selection batch approval decision is not approved")
    bound = body.get("bound_digests")
    if not isinstance(bound, Mapping):
        raise BatchError("compiled-selection batch approval has no digest bindings")
    plan_body = plan["body"]
    expected = {
        "plan_digest": plan["content_digest"],
        "selection_batch_digest": plan_body["selection_batch"]["content_digest"],
        "mapping_digest": plan_body["mapping"]["content_digest"],
        "lock_bundle_digest": plan_body["lock_bundle_digest"],
    }
    ground_truth_registry = plan_body.get("ground_truth_registry")
    if ground_truth_registry is not None:
        if not isinstance(ground_truth_registry, Mapping):
            raise BatchError("compiled-selection batch plan ground-truth registry lock is malformed")
        expected["ground_truth_registry_digest"] = ground_truth_registry.get("content_digest")
    ground_truth_record_digest = _ground_truth_record_projections_digest(plan_body)
    if ground_truth_record_digest is not None:
        expected["ground_truth_record_projections_digest"] = ground_truth_record_digest
    executor_build = plan_body.get("executor_build")
    if executor_build is not None:
        receipt_ref = executor_build.get("receipt") if isinstance(executor_build, Mapping) else None
        if not isinstance(receipt_ref, Mapping):
            raise BatchError("compiled-selection batch plan executor build lock is malformed")
        expected["executor_build_digest"] = receipt_ref.get("content_digest")
    if any(bound.get(key) != value for key, value in expected.items()):
        raise StaleInputError("compiled-selection batch approval is bound to different inputs")
    with _locked_batch_plan_approval(paths, plan["content_digest"]):
        _current_path, current = _load_workspace_envelope(
            paths,
            path,
            label="compiled-selection batch approval",
            kind="compiled_selection_batch_approval",
        )
        decisions = _prior_batch_approvals(paths, plan["content_digest"])
        if len(decisions) != 1:
            raise InvalidStateError("compiled-selection batch plan does not have exactly one canonical approval decision")
        if decisions[0]["content_digest"] != current["content_digest"]:
            raise InvalidStateError("compiled-selection batch approval is not the canonical decision")
        return path, current


def _job_status_summary(jobs: list[Mapping[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for job in jobs:
        status = job.get("status")
        if not isinstance(status, str):
            raise BatchError("batch job status is invalid")
        summary[status] = summary.get(status, 0) + 1
    return {key: summary[key] for key in sorted(summary)}


def _write_run_state(paths: WorkspacePaths, run_id: str, body: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    _validate_schema(dict(body), "compiled_selection_batch_run.schema.json", "compiled-selection batch run")
    state = make_envelope(
        kind="compiled_selection_batch_run",
        identifier=run_id,
        status=status,
        body=dict(body),
        producer="qagen/0.1.0:compiled_selection_batch",
    )
    atomic_write_json(_run_state_path(paths, run_id), state, private=True)
    return state


def _validate_run_job(
    job: Any,
    *,
    paths: WorkspacePaths,
    selection_batch_digest: str,
    manifest_row: Mapping[str, Any] | None,
    ground_truth_registry: GroundTruthRegistry | None,
    expected_ground_truth_registry_digest: str | None,
) -> dict[str, Any]:
    normalized = _validate_job(
        job,
        plan_status=False,
        paths=paths,
        selection_batch_digest=selection_batch_digest,
        manifest_row=manifest_row,
        ground_truth_registry=ground_truth_registry,
        expected_ground_truth_registry_digest=expected_ground_truth_registry_digest,
    )
    if normalized["status"] not in {"queued", "blocked_runtime", "dry_run_ready"}:
        raise BatchError("compiled-selection batch run has an unsupported job status")
    for role, ref in normalized["agent_references"].items():
        if ref["output_digest"] is not None:
            raise BatchError("compiled-selection batch diagnostic run cannot contain a generated agent output")
        if ref["tokens"] != {"input": 0, "output": 0} or ref["cost_usd"] != 0.0 or ref["latency_ms"] != 0:
            raise BatchError("compiled-selection batch diagnostic run cannot record generated usage")
        allowed = {"planned", "blocked_runtime", "dry_run_ready"}
        if ref["status"] not in allowed:
            raise BatchError(f"compiled-selection batch {role} has an unsupported diagnostic status")
    return normalized


def _validate_run_body(paths: WorkspacePaths, body: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(body, Mapping):
        raise BatchError("compiled-selection batch run body must be an object")
    run_body = deepcopy(dict(body))
    _validate_schema(run_body, "compiled_selection_batch_run.schema.json", "compiled-selection batch run")
    plan_ref = run_body["plan"]
    approval_ref = run_body["approval"]
    if not isinstance(plan_ref, Mapping) or not isinstance(approval_ref, Mapping):
        raise BatchError("compiled-selection batch run plan or approval reference is malformed")
    plan_path = _workspace_path(paths, plan_ref.get("path"), "compiled-selection batch run plan")
    plan = load_batch_plan(paths, plan_path)
    if plan["content_digest"] != plan_ref.get("content_digest"):
        raise StaleInputError("compiled-selection batch run plan digest changed")
    plan_ground_truth_lock = plan["body"].get("ground_truth_registry")
    ground_truth_registry: GroundTruthRegistry | None = None
    if isinstance(plan_ground_truth_lock, Mapping):
        try:
            ground_truth_registry = load_ground_truth_registry(
                paths.root / str(plan_ground_truth_lock.get("path")),
                expected_digest=str(plan_ground_truth_lock.get("content_digest")),
            )
        except GroundTruthRegistryError as exc:
            raise StaleInputError("compiled-selection batch run ground-truth registry changed") from exc
    approval_path = _workspace_path(paths, approval_ref.get("path"), "compiled-selection batch run approval")
    _approval_path, approval = _load_approval(paths, approval_path, plan=plan)
    if approval["content_digest"] != approval_ref.get("content_digest"):
        raise StaleInputError("compiled-selection batch run approval digest changed")
    if run_body.get("budget") != plan["body"].get("budget") or run_body.get("retry_policy") != plan["body"].get("retry_policy"):
        raise StaleInputError("compiled-selection batch run budget or retry policy differs from its plan")
    if run_body.get("executor_build") != plan["body"].get("executor_build"):
        raise StaleInputError("compiled-selection batch run executor build differs from its approved plan")
    selection_ref = plan["body"].get("selection_batch")
    if not isinstance(selection_ref, Mapping):
        raise BatchError("compiled-selection batch plan selection manifest reference is malformed")
    selection_path = _workspace_path(paths, selection_ref.get("path"), "compiled-selection batch plan selection manifest")
    raw_selection = read_json(selection_path)
    if not isinstance(raw_selection, Mapping):
        raise BatchError("compiled-selection batch plan selection manifest is not an object")
    try:
        selection_manifest = verify_envelope(raw_selection, kind="vnext_selection_batch_manifest")
    except InputError as exc:
        raise BatchError(f"compiled-selection batch plan selection manifest is invalid: {exc}") from exc
    if selection_manifest["content_digest"] != selection_ref.get("content_digest"):
        raise StaleInputError("compiled-selection batch plan selection manifest digest changed")
    manifest_rows = _manifest_rows(selection_manifest)
    rows_by_slot = {str(row["slot_ref"]["content_digest"]): row for row in manifest_rows}
    usage = run_body.get("usage")
    if usage != {"settled_tokens": 0, "settled_cost_usd": 0.0}:
        raise BatchError("compiled-selection diagnostic run may not settle provider usage")
    jobs = run_body.get("jobs")
    plan_jobs = plan["body"].get("jobs")
    if not isinstance(jobs, list) or not isinstance(plan_jobs, list) or len(jobs) != len(plan_jobs):
        raise BatchError("compiled-selection batch run jobs do not match its plan")
    normalized_jobs: list[dict[str, Any]] = []
    for plan_job, run_job in zip(plan_jobs, jobs, strict=True):
        if not isinstance(plan_job, Mapping):
            raise BatchError("compiled-selection batch plan job is malformed")
        plan_slot = plan_job.get("slot_digest")
        manifest_row = rows_by_slot.get(plan_slot) if isinstance(plan_slot, str) else None
        normalized = _validate_run_job(
            run_job,
            paths=paths,
            selection_batch_digest=str(selection_manifest["content_digest"]),
            manifest_row=manifest_row,
            ground_truth_registry=ground_truth_registry,
            expected_ground_truth_registry_digest=(
                ground_truth_registry.content_digest if ground_truth_registry is not None else None
            ),
        )
        immutable_fields = {
            "job_id",
            "ordinal",
            "slot_digest",
            "source_access_track",
            "target_leaf",
            "generation_strategy",
            "source_asset_set_digest",
            "ground_truth_record",
            "lock_bindings",
            "agent_inputs",
        }
        if any(normalized[field] != plan_job[field] for field in immutable_fields):
            raise StaleInputError("compiled-selection batch run job differs from its approved plan")
        if manifest_row is None:
            raise StaleInputError("compiled-selection batch run job is bound to an unknown compiled selection slot")
        _validate_job_manifest_binding(
            paths,
            normalized,
            manifest_row,
            selection_batch_digest=str(selection_manifest["content_digest"]),
        )
        normalized_jobs.append(normalized)
    diagnostics = run_body.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise BatchError("compiled-selection batch run diagnostics must be a list")
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, Mapping) or set(diagnostic) != {"path", "content_digest", "network_called"}:
            raise BatchError("compiled-selection batch diagnostic reference is malformed")
        if diagnostic.get("network_called") is not False:
            raise BatchError("compiled-selection batch diagnostics must never call the network")
        try:
            require_digest(diagnostic.get("content_digest"), "compiled-selection batch diagnostic digest")
        except InputError as exc:
            raise BatchError(str(exc)) from exc
    resume_count = run_body.get("resume_count")
    if not isinstance(resume_count, int) or isinstance(resume_count, bool) or resume_count < 0:
        raise BatchError("compiled-selection batch run resume_count is invalid")
    run_body["jobs"] = normalized_jobs
    return run_body, plan


def load_batch_run(paths: WorkspacePaths, run_id: str) -> dict[str, Any]:
    """Load a batch run and revalidate every immutable planning lock."""

    _ensure_batch_dirs(paths)
    canonical_run_id = safe_component(run_id, "compiled selection batch run id")
    path = _run_state_path(paths, canonical_run_id)
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise BatchError("compiled-selection batch run state must be an object")
    try:
        run = verify_envelope(raw, kind="compiled_selection_batch_run")
    except InputError as exc:
        raise BatchError(f"compiled-selection batch run state is invalid: {exc}") from exc
    if run.get("id") != canonical_run_id:
        raise BatchError("compiled-selection batch run id differs from its state identifier")
    _validate_run_body(paths, run["body"])
    return run


def start_batch(
    paths: WorkspacePaths,
    *,
    plan_path: str | Path,
    approval_path: str | Path,
) -> dict[str, Any]:
    """Start one persistent batch run after human approval.

    Starting copies only the sanitized plan job projections.  It does not open
    a viewer or instantiate a provider adapter.
    """

    _ensure_batch_dirs(paths)
    plan = load_batch_plan(paths, plan_path)
    approval_file, approval = _load_approval(paths, approval_path, plan=plan)
    run_id = f"batch-{_short(plan['content_digest'])}-{secrets.token_hex(5)}"
    directory = _run_directory(paths, run_id)
    for child in (directory, directory / "private" / "audit" / "diagnostics"):
        child.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(child, 0o700)
        except OSError:
            pass
    plan_file = _workspace_path(paths, plan_path, "compiled-selection batch plan")
    body = {
        "plan": {
            "path": plan_file.relative_to(paths.root).as_posix(),
            "content_digest": plan["content_digest"],
        },
        "approval": {
            "path": approval_file.relative_to(paths.root).as_posix(),
            "content_digest": approval["content_digest"],
        },
        "budget": deepcopy(plan["body"]["budget"]),
        "retry_policy": deepcopy(plan["body"]["retry_policy"]),
        "usage": {"settled_tokens": 0, "settled_cost_usd": 0.0},
        "jobs": deepcopy(plan["body"]["jobs"]),
        "diagnostics": [],
        "resume_count": 0,
    }
    if plan["body"].get("executor_build") is not None:
        body["executor_build"] = deepcopy(plan["body"]["executor_build"])
    state = _write_run_state(paths, run_id, body, status="ready_for_diagnostic")
    return {
        "run_id": run_id,
        "run_path": str(directory),
        "run_state_digest": state["content_digest"],
        "status": state["status"],
        "job_count": len(body["jobs"]),
        "generation_supported": False,
        "generation_support_reason": _GENERATION_UNSUPPORTED_REASON,
    }


def _runtime_diagnostic(runtime_config_path: str | Path | None) -> dict[str, Any]:
    if runtime_config_path is None:
        return {
            "kind": "qagen_compiled_selection_batch_runtime_diagnostic",
            "status": "blocked",
            "execution_eligible": False,
            "network_called": False,
            "blocked_reasons": ["provider_runtime_config_required"],
        }
    result = diagnose_provider_runtime(runtime_config_path, smoke=False)
    if result.get("network_called") is True:
        raise BatchError("provider runtime diagnostic unexpectedly contacted the network")
    return dict(result)


def _diagnostic_record(
    paths: WorkspacePaths,
    *,
    run_id: str,
    ordinal: int,
    runtime: Mapping[str, Any],
    selected_job_ids: list[str],
) -> tuple[Path, dict[str, Any]]:
    record = make_envelope(
        kind="compiled_selection_batch_diagnostic",
        identifier=f"{run_id}-diagnostic-{ordinal:04d}",
        status="ready" if runtime.get("execution_eligible") is True else "blocked",
        body={
            "dry_run": True,
            "network_called": False,
            "runtime": dict(runtime),
            "selected_job_ids": selected_job_ids,
        },
        producer="qagen/0.1.0:compiled_selection_batch",
    )
    path = _run_directory(paths, run_id) / "private" / "audit" / "diagnostics" / f"{ordinal:04d}.json"
    atomic_write_json(path, record, private=True)
    return path, record


def _diagnostic_agent_refs(job: Mapping[str, Any], *, ready: bool) -> dict[str, Any]:
    updated = deepcopy(dict(job))
    target_status = "dry_run_ready" if ready else "blocked_runtime"
    updated["status"] = target_status
    references = updated["agent_references"]
    for role in _STAGES:
        reference = dict(references[role])
        if reference.get("output_digest") is not None:
            raise BatchError("dry-run cannot overwrite a generated agent output")
        reference.update(
            {
                "tokens": {"input": 0, "output": 0},
                "cost_usd": 0.0,
                "latency_ms": 0,
                "status": target_status,
            }
        )
        references[role] = reference
    return updated


def execute_batch(
    paths: WorkspacePaths,
    *,
    run_id: str,
    dry_run: bool = True,
    live: bool = False,
    runtime_config_path: str | Path | None = None,
    limit: int | None = None,
    revision_receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run an explicit local live sidecar, or the default no-network diagnostic.

    The historical batch-run record remains diagnostic-only.  ``live=True``
    delegates to a separate private sidecar that owns typed worker outputs,
    usage records, and review-pending state.  That branch is deliberately
    explicit, so omitting both mode flags remains a no-network dry-run.
    """

    if not isinstance(dry_run, bool) or not isinstance(live, bool):
        raise BatchError("batch execution modes must be boolean")
    if dry_run and live:
        raise BatchError("batch execution accepts exactly one of dry_run or live")
    if not dry_run and not live:
        raise BatchError("batch execution requires dry_run or explicit live mode")
    if revision_receipt_path is not None and not live:
        raise BatchError("a batch revision receipt is only valid for explicit live execution")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise BatchError("batch execution limit must be a positive integer")
    _ensure_batch_dirs(paths)
    canonical_run_id = safe_component(run_id, "compiled selection batch run id")
    with _locked_run(paths, canonical_run_id):
        if live:
            # Keep the HTTP-capable path out of this diagnostic control-plane
            # module until a caller explicitly selects --live.
            from .live import execute_live_batch

            return execute_live_batch(
                paths,
                run_id=canonical_run_id,
                runtime_config_path=runtime_config_path,
                limit=limit,
                revision_receipt_path=revision_receipt_path,
            )
        run = load_batch_run(paths, canonical_run_id)
        if run["status"] not in {"ready_for_diagnostic", "diagnostic_blocked", "dry_run_ready"}:
            raise InvalidStateError(f"compiled-selection batch cannot execute from {run['status']}")
        body, _plan = _validate_run_body(paths, run["body"])
        candidates = [job for job in body["jobs"] if job["status"] in {"queued", "blocked_runtime", "dry_run_ready"}]
        selected = candidates if limit is None else candidates[:limit]
        if not selected:
            raise InvalidStateError("compiled-selection batch has no diagnostic-eligible jobs")
        runtime = _runtime_diagnostic(runtime_config_path)
        if runtime.get("network_called") is True:
            raise BatchError("compiled-selection batch dry-run must never contact a provider")
        ready = runtime.get("execution_eligible") is True
        selected_ids = [str(job["job_id"]) for job in selected]
        diagnostic_path, diagnostic = _diagnostic_record(
            paths,
            run_id=canonical_run_id,
            ordinal=len(body["diagnostics"]) + 1,
            runtime=runtime,
            selected_job_ids=selected_ids,
        )
        selected_set = set(selected_ids)
        body["jobs"] = [
            _diagnostic_agent_refs(job, ready=ready) if job["job_id"] in selected_set else job for job in body["jobs"]
        ]
        body["diagnostics"].append(
            {
                "path": diagnostic_path.relative_to(paths.root).as_posix(),
                "content_digest": diagnostic["content_digest"],
                "network_called": False,
            }
        )
        status = "dry_run_ready" if ready else "diagnostic_blocked"
        updated = _write_run_state(paths, canonical_run_id, body, status=status)
        return {
            "run_id": canonical_run_id,
            "run_state_digest": updated["content_digest"],
            "status": status,
            "dry_run": True,
            "network_called": False,
            "selected_job_count": len(selected_ids),
            "job_statuses": _job_status_summary(body["jobs"]),
            "runtime": runtime,
            "generated_candidate_count": 0,
            "generation_supported": False,
            "generation_support_reason": _GENERATION_UNSUPPORTED_REASON,
        }


def resume_batch(paths: WorkspacePaths, *, run_id: str) -> dict[str, Any]:
    """Revalidate locks and return a resumable batch state without executing it."""

    _ensure_batch_dirs(paths)
    canonical_run_id = safe_component(run_id, "compiled selection batch run id")
    with _locked_run(paths, canonical_run_id):
        run = load_batch_run(paths, canonical_run_id)
        if run["status"] not in {"ready_for_diagnostic", "diagnostic_blocked", "dry_run_ready"}:
            raise InvalidStateError(f"compiled-selection batch cannot resume from {run['status']}")
        body, _plan = _validate_run_body(paths, run["body"])
        body["resume_count"] = int(body["resume_count"]) + 1
        resumed = _write_run_state(paths, canonical_run_id, body, status=run["status"])
        return {
            "run_id": canonical_run_id,
            "run_state_digest": resumed["content_digest"],
            "status": resumed["status"],
            "resume_count": body["resume_count"],
            "job_statuses": _job_status_summary(body["jobs"]),
            "generated_candidate_count": 0,
            "generation_supported": False,
            "generation_support_reason": _GENERATION_UNSUPPORTED_REASON,
        }
