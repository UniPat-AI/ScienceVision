"""SQLite persistence for the local-only task management platform."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .bundle import (
    LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
    build_runtime_manifest,
    prepare_expert_bundle,
    prepare_legacy_bundle,
    require_runtime_compatibility,
)
from .contract import (
    ADMISSION_RECEIPT_PURPOSE,
    ADMISSION_RECEIPT_SCHEMA_VERSION,
    BATCH_SCHEMA_VERSION,
    BUNDLE_CONTRACT_VERSION,
    BUNDLE_RESULT_SCHEMA_VERSION,
    BUNDLE_SCHEMA_VERSION,
    BUNDLE_SUBMISSION_SCHEMA_VERSION,
    CONTRACT_VERSION,
    EXPERTS_SCHEMA_VERSION,
    IMPORT_REPORT_SCHEMA_VERSION,
    PACKAGE_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    SUBMISSION_SCHEMA_VERSION,
    ContractError,
    admission_manifest_provenance,
    asset_records,
    canonical_json,
    file_sha256,
    normalize_domains,
    object_sha256,
    require_identifier,
    require_hash_identifier,
    stable_assignment_id,
    submission_payload_sha256,
    task_revision_sha256,
    validate_qa_export_admission_receipt,
)
from .trajectories import ensure_schema as ensure_trajectory_schema
from .trajectories import import_trajectory as _import_trajectory
from .trajectories import trajectory_task_ids

_SUBMISSION_FIELDS = {
    "schema_version",
    "contract_version",
    "submission_id",
    "package_id",
    "package_manifest_sha256",
    "batch_id",
    "expert_id",
    "results",
    "payload_sha256",
}
_RESULT_FIELDS = {
    "schema_version",
    "package_id",
    "batch_id",
    "expert_id",
    "assignment_id",
    "task_id",
    "task_revision_sha256",
    "attempt",
    "task_validation",
    "answer",
    "reasoning",
    "trajectory",
    "evidence",
    "saved_at",
}
_VALIDATION_FIELDS = {"status", "feedback"}
_TRAJECTORY_REF = {
    "schema_version": "sciencevision.trajectory_ref.v1",
    "status": "not_collected",
    "reason": "collector_not_implemented",
}
_TRAJECTORY_SCHEMA_VERSION = "sciencevision.trajectory.v1"
_TRAJECTORY_FIELDS = {"schema_version", "status", "steps"}
_TRAJECTORY_STEP_FIELDS = {"index", "at", "action", "note"}
_TRAJECTORY_MAX_STEPS = 200
_TRAJECTORY_MAX_AT = 128
_TRAJECTORY_MAX_ACTION = 64
_TRAJECTORY_MAX_NOTE = 500
_BUNDLE_SUBMISSION_FIELDS = {
    "schema_version", "contract_version", "submission_id", "bundle_id",
    "bundle_manifest_sha256", "batch_id", "expert_id", "results", "payload_sha256",
}
_BUNDLE_RESULT_FIELDS = (_RESULT_FIELDS - {"package_id"}) | {"bundle_id"}

ASSIGNMENT_STATUSES = frozenset(
    {
        "assigned",
        "in_progress",
        "submitted",
        "pending_review",
        "approved",
        "returned",
    }
)
ASSIGNMENT_TRANSITIONS = {
    "assigned": frozenset({"in_progress"}),
    "in_progress": frozenset({"submitted"}),
    "submitted": frozenset({"pending_review"}),
    "pending_review": frozenset({"approved", "returned"}),
    "approved": frozenset(),
    "returned": frozenset({"in_progress"}),
}
_AUDIT_VALUE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_AUDIT_ACTOR_ROLES = frozenset({"admin", "expert", "system"})
_AUDIT_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "csrf",
        "password",
        "secret",
        "session",
        "session_id",
        "token",
    }
)
_AUDIT_PAYLOAD_FIELDS = {
    "assignment.assigned": frozenset({"expert_id", "task_id"}),
    "assignment.opened": frozenset(
        {
            "attempt",
            "container_id",
            "from_status",
            "source",
            "submission_id",
            "to_status",
        }
    ),
    "assignment.submitted": frozenset(
        {
            "attempt",
            "container_id",
            "from_status",
            "source",
            "submission_id",
            "to_status",
        }
    ),
    "assignment.pending_review": frozenset(
        {
            "attempt",
            "container_id",
            "from_status",
            "source",
            "submission_id",
            "to_status",
        }
    ),
    "assignment.result_imported": frozenset(
        {"attempt", "container_id", "source", "submission_id"}
    ),
    "assignment.status_changed": frozenset({"from_status", "reason", "to_status"}),
    "assignment.approved": frozenset({"from_status", "reason", "to_status"}),
    "assignment.returned": frozenset({"from_status", "reason", "to_status"}),
}
_AUDIT_PAYLOAD_REQUIRED_FIELDS = {
    "assignment.assigned": frozenset({"expert_id", "task_id"}),
    "assignment.opened": frozenset({"from_status", "to_status"}),
    "assignment.submitted": frozenset({"from_status", "to_status"}),
    "assignment.pending_review": frozenset({"from_status", "to_status"}),
    "assignment.result_imported": frozenset(
        {"attempt", "container_id", "source", "submission_id"}
    ),
    "assignment.status_changed": frozenset({"from_status", "to_status"}),
    "assignment.approved": frozenset({"from_status", "to_status"}),
    "assignment.returned": frozenset({"from_status", "reason", "to_status"}),
}
_AUDIT_SOURCES = frozenset(
    {"admin_http_import", "offline_import", "online_submit", "online_task_open"}
)
_SCHEMA_MIGRATIONS = (1, 2, 3)
_DRAFT_VALIDATION_STATUSES = frozenset({"valid", "invalid", "uncertain"})


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reject_nonstandard_json_constant(constant: str) -> None:
    raise ValueError(f"non-standard JSON constant {constant!r} is not allowed")


def _require_exact_fields(value: dict[str, Any], fields: set[str], scope: str) -> None:
    missing = sorted(fields - set(value))
    extra = sorted(set(value) - fields)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise ContractError(f"{scope} fields are invalid: {'; '.join(details)}")


def _require_trajectory_text(value: Any, *, limit: int, scope: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ContractError(f"{scope} must be text of 1..{limit} characters")


def _validate_trajectory(trajectory: Any) -> None:
    """Accept the legacy "no collector yet" record and collected key operations.

    Reviewers asked for the reasoning behind the operations that moved a
    judgement, not a log of every mouse move, so every step must carry a note.
    Packages built before the collector shipped still send the legacy record,
    which keeps importing; anything else is refused rather than stored.
    """
    if trajectory == _TRAJECTORY_REF:
        return
    if (
        not isinstance(trajectory, dict)
        or trajectory.get("schema_version") != _TRAJECTORY_SCHEMA_VERSION
    ):
        raise ContractError(
            "trajectory must be a sciencevision.trajectory.v1 record"
            " or state collector_not_implemented"
        )
    _require_exact_fields(trajectory, _TRAJECTORY_FIELDS, "trajectory")
    steps = trajectory.get("steps")
    if not isinstance(steps, list):
        raise ContractError("trajectory steps must be a list")
    if len(steps) > _TRAJECTORY_MAX_STEPS:
        raise ContractError(f"trajectory carries more than {_TRAJECTORY_MAX_STEPS} steps")
    expected_status = "collected" if steps else "empty"
    if trajectory.get("status") != expected_status:
        raise ContractError(
            f"trajectory status must be {expected_status} for {len(steps)} steps"
        )
    for position, step in enumerate(steps, start=1):
        scope = f"trajectory step {position}"
        if not isinstance(step, dict):
            raise ContractError(f"{scope} must be an object")
        _require_exact_fields(step, _TRAJECTORY_STEP_FIELDS, scope)
        if step.get("index") != position:
            raise ContractError(f"{scope} index is out of order")
        _require_trajectory_text(
            step.get("at"), limit=_TRAJECTORY_MAX_AT, scope=f"{scope} at"
        )
        _require_trajectory_text(
            step.get("action"), limit=_TRAJECTORY_MAX_ACTION, scope=f"{scope} action"
        )
        _require_trajectory_text(
            step.get("note"), limit=_TRAJECTORY_MAX_NOTE, scope=f"{scope} note"
        )


def _trajectory_step_count(trajectory: Any) -> int:
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    return len(steps) if isinstance(steps, list) else 0


def _trajectory_steps(trajectory: Any) -> list[dict[str, Any]]:
    """The recorded steps themselves, for the console that has to read them.

    The export contract only counts them; whoever reviews a submission needs
    the note saying why each operation mattered.
    """
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, dict)]


def _path_has_symlink(root: Path, relative_path: str) -> bool:
    current = root
    for part in relative_path.split("/"):
        current = current / part
        if current.is_symlink():
            return True
    return False


def _audit_payload_has_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower().replace("-", "_")
            pieces = {piece for piece in normalized.split("_") if piece}
            if normalized in _AUDIT_SENSITIVE_KEYS or pieces & _AUDIT_SENSITIVE_KEYS:
                return True
            if _audit_payload_has_sensitive_key(nested):
                return True
    elif isinstance(value, list):
        return any(_audit_payload_has_sensitive_key(item) for item in value)
    return False


class PreflightError(ContractError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class ManagerStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # The loopback HTTP server is single-request-at-a-time, but tests and
        # desktop wrappers may run that server on a dedicated thread.
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ManagerStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS experts (
              expert_id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              domains_json TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batches (
              batch_id TEXT PRIMARY KEY,
              schema_version TEXT NOT NULL,
              title TEXT NOT NULL,
              tasks_source TEXT NOT NULL,
              asset_root TEXT NOT NULL,
              viewer_root TEXT NOT NULL,
              imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
              batch_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              domain TEXT NOT NULL,
              task_revision_sha256 TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              assets_json TEXT NOT NULL,
              PRIMARY KEY (batch_id, task_id),
              FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
            );
            CREATE TABLE IF NOT EXISTS batch_admissions (
              batch_id TEXT PRIMARY KEY,
              schema_version TEXT NOT NULL,
              status TEXT NOT NULL,
              purpose TEXT NOT NULL,
              receipt_digest TEXT NOT NULL,
              policy_name TEXT NOT NULL,
              policy_sha256 TEXT NOT NULL,
              tasks_path TEXT NOT NULL,
              tasks_sha256 TEXT NOT NULL,
              task_count INTEGER NOT NULL,
              revision_count INTEGER NOT NULL,
              revision_digest TEXT NOT NULL,
              receipt_json TEXT NOT NULL,
              admitted_at TEXT NOT NULL,
              FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
            );
            CREATE TABLE IF NOT EXISTS assignments (
              assignment_id TEXT PRIMARY KEY,
              schema_version TEXT NOT NULL,
              batch_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              task_revision_sha256 TEXT NOT NULL,
              expert_id TEXT NOT NULL,
              assigned_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'assigned',
              UNIQUE (batch_id, task_id),
              FOREIGN KEY (batch_id, task_id) REFERENCES tasks(batch_id, task_id),
              FOREIGN KEY (expert_id) REFERENCES experts(expert_id)
            );
            CREATE TABLE IF NOT EXISTS packages (
              package_id TEXT PRIMARY KEY,
              schema_version TEXT NOT NULL,
              batch_id TEXT NOT NULL,
              expert_id TEXT NOT NULL,
              manifest_sha256 TEXT NOT NULL,
              assignments_json TEXT NOT NULL,
              output_path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (batch_id) REFERENCES batches(batch_id),
              FOREIGN KEY (expert_id) REFERENCES experts(expert_id)
            );
            CREATE TABLE IF NOT EXISTS task_bundles (
              bundle_id TEXT PRIMARY KEY,
              schema_version TEXT NOT NULL,
              batch_id TEXT NOT NULL,
              expert_id TEXT NOT NULL,
              manifest_sha256 TEXT NOT NULL,
              manifest_json TEXT NOT NULL,
              tasks_sha256 TEXT NOT NULL,
              tasks_jsonl TEXT NOT NULL,
              asset_paths_json TEXT NOT NULL,
              asset_root TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              source_root TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (batch_id) REFERENCES batches(batch_id),
              FOREIGN KEY (expert_id) REFERENCES experts(expert_id)
            );
            CREATE TABLE IF NOT EXISTS active_task_bundles (
              expert_id TEXT PRIMARY KEY,
              bundle_id TEXT NOT NULL,
              activated_at TEXT NOT NULL,
              FOREIGN KEY (expert_id) REFERENCES experts(expert_id),
              FOREIGN KEY (bundle_id) REFERENCES task_bundles(bundle_id)
            );
            CREATE TABLE IF NOT EXISTS submissions (
              submission_id TEXT PRIMARY KEY,
              package_id TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              FOREIGN KEY (package_id) REFERENCES packages(package_id)
            );
            CREATE TABLE IF NOT EXISTS results (
              assignment_id TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              submission_id TEXT NOT NULL,
              package_id TEXT NOT NULL,
              expert_id TEXT NOT NULL,
              batch_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              task_revision_sha256 TEXT NOT NULL,
              result_sha256 TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              PRIMARY KEY (assignment_id, attempt),
              FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id),
              FOREIGN KEY (submission_id) REFERENCES submissions(submission_id),
              FOREIGN KEY (package_id) REFERENCES packages(package_id)
            );
            CREATE TABLE IF NOT EXISTS bundle_submissions (
              submission_id TEXT PRIMARY KEY,
              bundle_id TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              FOREIGN KEY (bundle_id) REFERENCES task_bundles(bundle_id)
            );
            CREATE TABLE IF NOT EXISTS bundle_results (
              assignment_id TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              submission_id TEXT NOT NULL,
              bundle_id TEXT NOT NULL,
              expert_id TEXT NOT NULL,
              batch_id TEXT NOT NULL,
              task_id TEXT NOT NULL,
              task_revision_sha256 TEXT NOT NULL,
              result_sha256 TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              imported_at TEXT NOT NULL,
              PRIMARY KEY (assignment_id, attempt),
              FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id),
              FOREIGN KEY (submission_id) REFERENCES bundle_submissions(submission_id),
              FOREIGN KEY (bundle_id) REFERENCES task_bundles(bundle_id)
            );
            CREATE INDEX IF NOT EXISTS idx_assignments_expert
              ON assignments(batch_id, expert_id);
            CREATE INDEX IF NOT EXISTS idx_results_package ON results(package_id);
            CREATE INDEX IF NOT EXISTS idx_task_bundles_expert
              ON task_bundles(batch_id, expert_id);
            CREATE INDEX IF NOT EXISTS idx_bundle_results_bundle ON bundle_results(bundle_id);
            """
        )
        ensure_trajectory_schema(self.connection)
        self.connection.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                     version INTEGER PRIMARY KEY,
                     applied_at TEXT NOT NULL
                   )"""
            )
            applied = {
                int(row["version"])
                for row in self.connection.execute("SELECT version FROM schema_migrations")
            }
            unknown = applied - set(_SCHEMA_MIGRATIONS)
            if unknown:
                raise ContractError(
                    "manager database has unsupported schema migration(s): "
                    + ", ".join(str(version) for version in sorted(unknown))
                )
            if 1 not in applied:
                self._apply_lifecycle_audit_migration()
                self.connection.execute(
                    "INSERT INTO schema_migrations (version,applied_at) VALUES (?,?)",
                    (1, utc_now()),
                )
            if 2 not in applied:
                self._apply_lifecycle_hardening_migration()
                self.connection.execute(
                    "INSERT INTO schema_migrations (version,applied_at) VALUES (?,?)",
                    (2, utc_now()),
                )
            if 3 not in applied:
                self._apply_assignment_drafts_migration()
                self.connection.execute(
                    "INSERT INTO schema_migrations (version,applied_at) VALUES (?,?)",
                    (3, utc_now()),
                )
            self._validate_lifecycle_schema()
            self.connection.commit()
            transaction_started = False
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise

    def _apply_lifecycle_audit_migration(self) -> None:
        assignment_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(assignments)")
        }
        if "status" not in assignment_columns:
            self.connection.execute(
                "ALTER TABLE assignments ADD COLUMN status TEXT NOT NULL DEFAULT 'assigned'"
            )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS audit_events (
                 event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                 occurred_at TEXT NOT NULL,
                 actor_id TEXT NOT NULL,
                 actor_role TEXT NOT NULL,
                 event_type TEXT NOT NULL,
                 entity_type TEXT NOT NULL,
                 entity_id TEXT NOT NULL,
                 batch_id TEXT,
                 assignment_id TEXT,
                 payload_json TEXT NOT NULL,
                 FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id)
               )"""
        )
        self._drop_lifecycle_triggers()
        self._create_audit_indexes()
        self._backfill_submitted_assignments()
        self._install_lifecycle_triggers()

    def _apply_assignment_drafts_migration(self) -> None:
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS assignment_drafts (
                 assignment_id TEXT PRIMARY KEY,
                 expert_id TEXT NOT NULL,
                 payload_json TEXT NOT NULL,
                 saved_at TEXT NOT NULL,
                 FOREIGN KEY (assignment_id) REFERENCES assignments(assignment_id),
                 FOREIGN KEY (expert_id) REFERENCES experts(expert_id)
               )"""
        )

    def _apply_lifecycle_hardening_migration(self) -> None:
        # Version 1 allowed REPLACE to bypass update/delete triggers and did not
        # backfill historical results. Rebuild the guards inside this write lock.
        self._drop_lifecycle_triggers()
        self._backfill_submitted_assignments()
        self._create_audit_indexes()
        self._install_lifecycle_triggers()

    def _create_audit_indexes(self) -> None:
        for name, columns in (
            ("idx_audit_events_batch", "batch_id,event_id"),
            ("idx_audit_events_assignment", "assignment_id,event_id"),
            ("idx_audit_events_actor", "actor_id,event_id"),
            ("idx_audit_events_type", "event_type,event_id"),
        ):
            self.connection.execute(
                f"CREATE INDEX IF NOT EXISTS {name} ON audit_events({columns})"
            )

    def _backfill_submitted_assignments(self) -> None:
        placeholders = ",".join("?" for _ in ASSIGNMENT_STATUSES)
        invalid_status = self.connection.execute(
            f"""SELECT status FROM assignments
                WHERE status IS NULL OR status NOT IN ({placeholders})
                LIMIT 1""",
            tuple(sorted(ASSIGNMENT_STATUSES)),
        ).fetchone()
        if invalid_status:
            raise ContractError(
                f"assignment has unsupported lifecycle status {invalid_status['status']!r}"
            )
        self.connection.execute(
            """UPDATE assignments
               SET status='pending_review'
               WHERE status IN ('assigned','in_progress','submitted')
                 AND (
                   EXISTS (
                     SELECT 1 FROM results r
                     WHERE r.assignment_id=assignments.assignment_id
                   )
                   OR EXISTS (
                     SELECT 1 FROM bundle_results r
                     WHERE r.assignment_id=assignments.assignment_id
                   )
                 )"""
        )

    def _drop_lifecycle_triggers(self) -> None:
        for name in (
            "audit_events_no_update",
            "audit_events_no_delete",
            "audit_events_no_replace",
            "assignments_no_replace",
            "assignments_status_insert_valid",
            "assignments_status_transition_valid",
        ):
            self.connection.execute(f"DROP TRIGGER IF EXISTS {name}")

    def _install_lifecycle_triggers(self) -> None:
        self._drop_lifecycle_triggers()
        self.connection.execute(
            """CREATE TRIGGER audit_events_no_update
                 BEFORE UPDATE ON audit_events
                 BEGIN
                   SELECT RAISE(ABORT, 'audit events are append-only');
                 END"""
        )
        self.connection.execute(
            """CREATE TRIGGER audit_events_no_delete
                 BEFORE DELETE ON audit_events
                 BEGIN
                   SELECT RAISE(ABORT, 'audit events are append-only');
                 END"""
        )
        self.connection.execute(
            """CREATE TRIGGER audit_events_no_replace
                 BEFORE INSERT ON audit_events
                 WHEN EXISTS (
                   SELECT 1 FROM audit_events WHERE event_id IS NEW.event_id
                 )
                 BEGIN
                   SELECT RAISE(ABORT, 'audit events are append-only');
                 END"""
        )
        self.connection.execute(
            """CREATE TRIGGER assignments_no_replace
                 BEFORE INSERT ON assignments
                 WHEN NEW.assignment_id IS NULL OR EXISTS (
                   SELECT 1 FROM assignments
                   WHERE assignment_id IS NEW.assignment_id
                 ) OR EXISTS (
                   SELECT 1 FROM assignments
                   WHERE batch_id IS NEW.batch_id AND task_id IS NEW.task_id
                 )
                 BEGIN
                   SELECT RAISE(ABORT, 'existing assignments cannot be replaced');
                 END"""
        )
        valid_statuses = ",".join(f"'{status}'" for status in sorted(ASSIGNMENT_STATUSES))
        valid_transitions = " OR ".join(
            f"(OLD.status='{source}' AND NEW.status='{target}')"
            for source, targets in ASSIGNMENT_TRANSITIONS.items()
            for target in sorted(targets)
        )
        self.connection.execute(
            f"""CREATE TRIGGER assignments_status_insert_valid
                 BEFORE INSERT ON assignments
                 WHEN NEW.status IS NULL OR NEW.status NOT IN ({valid_statuses})
                 BEGIN
                   SELECT RAISE(ABORT, 'invalid assignment status');
                 END"""
        )
        self.connection.execute(
            f"""CREATE TRIGGER assignments_status_transition_valid
                 BEFORE UPDATE OF status ON assignments
                 WHEN OLD.status IS NOT NEW.status AND NOT ({valid_transitions})
                 BEGIN
                   SELECT RAISE(ABORT, 'invalid assignment status transition');
                 END"""
        )

    def _validate_lifecycle_schema(self) -> None:
        assignment_columns = {
            row["name"]: row for row in self.connection.execute("PRAGMA table_info(assignments)")
        }
        status_column = assignment_columns.get("status")
        if status_column is None or not status_column["notnull"]:
            raise ContractError("manager database lifecycle status column is unhealthy")
        audit_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(audit_events)")
        }
        required_audit_columns = {
            "event_id",
            "occurred_at",
            "actor_id",
            "actor_role",
            "event_type",
            "entity_type",
            "entity_id",
            "batch_id",
            "assignment_id",
            "payload_json",
        }
        if not required_audit_columns.issubset(audit_columns):
            raise ContractError("manager database audit_events table is unhealthy")
        invalid_status = self.connection.execute(
            f"""SELECT status FROM assignments
                WHERE status IS NULL OR status NOT IN
                  ({",".join("?" for _ in ASSIGNMENT_STATUSES)})
                LIMIT 1""",
            tuple(sorted(ASSIGNMENT_STATUSES)),
        ).fetchone()
        if invalid_status:
            raise ContractError(
                f"assignment has unsupported lifecycle status {invalid_status['status']!r}"
            )
        stale_result = self.connection.execute(
            """SELECT assignment_id FROM assignments
               WHERE status IN ('assigned','in_progress','submitted')
                 AND (
                   EXISTS (
                     SELECT 1 FROM results r
                     WHERE r.assignment_id=assignments.assignment_id
                   )
                   OR EXISTS (
                     SELECT 1 FROM bundle_results r
                     WHERE r.assignment_id=assignments.assignment_id
                   )
                 )
               LIMIT 1"""
        ).fetchone()
        if stale_result:
            raise ContractError("submitted assignment lifecycle backfill is incomplete")
        trigger_rows = self.connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        triggers = {row["name"]: row["sql"] or "" for row in trigger_rows}
        expected_triggers = {
            "audit_events_no_update",
            "audit_events_no_delete",
            "audit_events_no_replace",
            "assignments_no_replace",
            "assignments_status_insert_valid",
            "assignments_status_transition_valid",
        }
        if not expected_triggers.issubset(triggers):
            raise ContractError("manager database lifecycle triggers are unhealthy")
        if "NEW.status IS NULL" not in triggers["assignments_status_insert_valid"]:
            raise ContractError("assignment status insert guard is unhealthy")
        if "OLD.status IS NOT NEW.status" not in triggers["assignments_status_transition_valid"]:
            raise ContractError("assignment status transition guard is unhealthy")
        if "event_id IS NEW.event_id" not in triggers["audit_events_no_replace"]:
            raise ContractError("audit event replacement guard is unhealthy")
        assignment_replace_guard = triggers["assignments_no_replace"]
        if (
            "assignment_id IS NEW.assignment_id" not in assignment_replace_guard
            or "batch_id IS NEW.batch_id" not in assignment_replace_guard
            or "task_id IS NEW.task_id" not in assignment_replace_guard
        ):
            raise ContractError("assignment replacement guard is unhealthy")

    @staticmethod
    def _require_audit_value(value: Any, field: str) -> str:
        if not isinstance(value, str) or not _AUDIT_VALUE_PATTERN.fullmatch(value):
            raise ContractError(f"{field} must match {_AUDIT_VALUE_PATTERN.pattern}")
        return value

    @staticmethod
    def _canonical_audit_payload(
        event_type: str, payload: dict[str, Any] | None
    ) -> str:
        value = {} if payload is None else payload
        if not isinstance(value, dict):
            raise ContractError("audit payload must be an object")
        if _audit_payload_has_sensitive_key(value):
            raise ContractError("audit payload contains a sensitive field")
        allowed = _AUDIT_PAYLOAD_FIELDS.get(event_type, frozenset())
        required = _AUDIT_PAYLOAD_REQUIRED_FIELDS.get(event_type, frozenset())
        unexpected = sorted(set(value) - allowed)
        missing = sorted(required - set(value))
        if unexpected or missing:
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise ContractError(
                f"audit payload for {event_type!r} is invalid: {'; '.join(details)}"
            )
        for field in ("expert_id", "task_id"):
            if field in value:
                require_identifier(value[field], field)
        if "container_id" in value:
            require_hash_identifier(value["container_id"], "bundle_id")
        if "submission_id" in value:
            require_hash_identifier(value["submission_id"], "submission_id")
        for field in ("from_status", "to_status"):
            if field in value and value[field] not in ASSIGNMENT_STATUSES:
                raise ContractError(f"audit payload {field} is not a lifecycle status")
        if "attempt" in value and (
            isinstance(value["attempt"], bool)
            or not isinstance(value["attempt"], int)
            or value["attempt"] < 1
        ):
            raise ContractError("audit payload attempt must be a positive integer")
        if "source" in value and value["source"] not in _AUDIT_SOURCES:
            raise ContractError("audit payload source is invalid")
        if "reason" in value and (
            not isinstance(value["reason"], str)
            or not value["reason"].strip()
            or len(value["reason"]) > 2000
        ):
            raise ContractError("audit payload reason must be 1 to 2000 characters")
        try:
            encoded = canonical_json(value)
        except (TypeError, ValueError) as error:
            raise ContractError("audit payload must be canonical JSON") from error
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ContractError("audit payload is too large")
        return encoded

    def _append_audit_event(
        self,
        *,
        actor_id: str,
        actor_role: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        batch_id: str | None = None,
        assignment_id: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> int:
        actor_id = require_identifier(actor_id, "actor_id")
        if actor_role not in _AUDIT_ACTOR_ROLES:
            raise ContractError("actor_role must be admin, expert, or system")
        event_type = self._require_audit_value(event_type, "event_type")
        entity_type = self._require_audit_value(entity_type, "entity_type")
        entity_id = require_identifier(entity_id, "entity_id")
        if batch_id is not None:
            batch_id = require_identifier(batch_id, "batch_id")
        if assignment_id is not None:
            assignment_id = require_hash_identifier(assignment_id, "assignment_id")
        cursor = self.connection.execute(
            """INSERT INTO audit_events
               (occurred_at,actor_id,actor_role,event_type,entity_type,entity_id,
                batch_id,assignment_id,payload_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                occurred_at or utc_now(),
                actor_id,
                actor_role,
                event_type,
                entity_type,
                entity_id,
                batch_id,
                assignment_id,
                self._canonical_audit_payload(event_type, payload),
            ),
        )
        return int(cursor.lastrowid)

    def record_audit_event(
        self,
        *,
        actor_id: str,
        actor_role: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        batch_id: str | None = None,
        assignment_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Append one validated event; callers cannot mutate it afterward."""

        with self.connection:
            return self._append_audit_event(
                actor_id=actor_id,
                actor_role=actor_role,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                batch_id=batch_id,
                assignment_id=assignment_id,
                payload=payload,
            )

    def audit_events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        batch_id: str | None = None,
        assignment_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ContractError("audit limit must be an integer between 1 and 200")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
            raise ContractError("audit offset must be an integer between 0 and 1000000")
        filters: list[str] = []
        parameters: list[Any] = []
        if batch_id is not None:
            filters.append("batch_id=?")
            parameters.append(require_identifier(batch_id, "batch_id"))
        if assignment_id is not None:
            filters.append("assignment_id=?")
            parameters.append(require_hash_identifier(assignment_id, "assignment_id"))
        if actor_id is not None:
            filters.append("actor_id=?")
            parameters.append(require_identifier(actor_id, "actor_id"))
        if event_type is not None:
            filters.append("event_type=?")
            parameters.append(self._require_audit_value(event_type, "event_type"))
        where = " WHERE " + " AND ".join(filters) if filters else ""
        total = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM audit_events" + where, parameters
            ).fetchone()[0]
        )
        rows = self.connection.execute(
            "SELECT * FROM audit_events" + where + " ORDER BY event_id DESC LIMIT ? OFFSET ?",
            [*parameters, limit, offset],
        ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event.pop("payload_json"))
            events.append(event)
        return {
            "schema_version": "sciencevision.audit_event_page.v1",
            "events": events,
            "page": {"limit": limit, "offset": offset, "total": total},
        }

    @staticmethod
    def _require_assignment_transition(current_status: str, target_status: str) -> None:
        if current_status not in ASSIGNMENT_STATUSES:
            raise ContractError(f"unsupported assignment status {current_status!r}")
        if target_status not in ASSIGNMENT_STATUSES:
            raise ContractError(f"unsupported assignment status {target_status!r}")
        if target_status not in ASSIGNMENT_TRANSITIONS[current_status]:
            raise ContractError(
                f"illegal assignment transition {current_status!r} -> {target_status!r}"
            )

    def _transition_assignment_in_transaction(
        self,
        assignment: sqlite3.Row,
        *,
        target_status: str,
        actor_id: str,
        actor_role: str,
        event_type: str = "assignment.status_changed",
        payload: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> sqlite3.Row:
        current_status = str(assignment["status"])
        self._require_assignment_transition(current_status, target_status)
        if actor_role == "expert":
            allowed = {
                ("assigned", "in_progress"),
                ("returned", "in_progress"),
                ("in_progress", "submitted"),
            }
            if (
                actor_id != assignment["expert_id"]
                or (current_status, target_status) not in allowed
            ):
                raise ContractError("expert is not authorized for this assignment transition")
        elif actor_role == "admin":
            allowed = {
                ("pending_review", "approved"),
                ("pending_review", "returned"),
                ("assigned", "in_progress"),
                ("returned", "in_progress"),
                ("in_progress", "submitted"),
                ("submitted", "pending_review"),
            }
            if (current_status, target_status) not in allowed:
                raise ContractError("admin is not authorized for this assignment transition")
        elif actor_role != "system":
            raise ContractError("actor_role must be admin, expert, or system")
        cursor = self.connection.execute(
            """UPDATE assignments SET status=?
               WHERE assignment_id=? AND status=?""",
            (target_status, assignment["assignment_id"], current_status),
        )
        if cursor.rowcount != 1:
            raise ContractError("assignment status changed concurrently")
        event_payload = dict(payload or {})
        event_payload.update(
            {
                "from_status": current_status,
                "to_status": target_status,
            }
        )
        self._append_audit_event(
            actor_id=actor_id,
            actor_role=actor_role,
            event_type=event_type,
            entity_type="assignment",
            entity_id=assignment["assignment_id"],
            batch_id=assignment["batch_id"],
            assignment_id=assignment["assignment_id"],
            payload=event_payload,
            occurred_at=occurred_at,
        )
        updated = self.connection.execute(
            "SELECT * FROM assignments WHERE assignment_id=?",
            (assignment["assignment_id"],),
        ).fetchone()
        if updated is None:
            raise ContractError("assignment disappeared during status transition")
        return updated

    def transition_assignment_status(
        self,
        assignment_id: str,
        target_status: str,
        *,
        actor_id: str,
        actor_role: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assignment_id = require_hash_identifier(assignment_id, "assignment_id")
        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            assignment = self.connection.execute(
                "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
            ).fetchone()
            if assignment is None:
                raise ContractError("unknown assignment_id")
            updated = self._transition_assignment_in_transaction(
                assignment,
                target_status=target_status,
                actor_id=actor_id,
                actor_role=actor_role,
                payload=payload,
            )
            self.connection.commit()
            transaction_started = False
            return dict(updated)
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise

    def start_online_assignment(self, *, expert_id: str, assignment_id: str) -> dict[str, Any]:
        expert_id = require_identifier(expert_id, "expert_id")
        assignment_id = require_hash_identifier(assignment_id, "assignment_id")
        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            assignment = self.connection.execute(
                "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
            ).fetchone()
            if assignment is None or assignment["expert_id"] != expert_id:
                raise ContractError("assignment is not assigned to this expert")
            active = self.connection.execute(
                """SELECT b.manifest_json FROM active_task_bundles a
                   JOIN task_bundles b ON b.bundle_id=a.bundle_id
                   WHERE a.expert_id=?""",
                (expert_id,),
            ).fetchone()
            if active is None:
                raise ContractError("expert has no active task bundle")
            manifest = json.loads(active["manifest_json"])
            active_ids = {
                item.get("assignment_id")
                for item in manifest.get("assignments", [])
                if isinstance(item, dict)
            }
            if assignment_id not in active_ids:
                raise ContractError("assignment is not part of the active task bundle")
            if assignment["status"] == "in_progress":
                self.connection.commit()
                transaction_started = False
                return dict(assignment) | {"unchanged": True}
            updated = self._transition_assignment_in_transaction(
                assignment,
                target_status="in_progress",
                actor_id=expert_id,
                actor_role="expert",
                event_type="assignment.opened",
                payload={"source": "online_task_open"},
            )
            self.connection.commit()
            transaction_started = False
            return dict(updated) | {"unchanged": False}
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise

    def save_draft(
        self,
        *,
        expert_id: str,
        assignment_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        expert_id = require_identifier(expert_id, "expert_id")
        assignment_id = require_hash_identifier(assignment_id, "assignment_id")
        if not isinstance(payload, dict):
            raise ContractError("draft payload must be an object")
        assignment = self.connection.execute(
            "SELECT assignment_id, expert_id, status FROM assignments WHERE assignment_id=?",
            (assignment_id,),
        ).fetchone()
        if assignment is None or assignment["expert_id"] != expert_id:
            raise ContractError("assignment is not assigned to this expert")
        if assignment["status"] not in {"assigned", "in_progress", "returned"}:
            raise ContractError("drafts cannot be saved after the assignment is submitted")
        status = payload.get("validation_status", "uncertain")
        if status not in _DRAFT_VALIDATION_STATUSES:
            raise ContractError("validation_status must be valid, invalid, or uncertain")
        attempt = payload.get("attempt", 1)
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ContractError("attempt must be an integer >= 1")
        steps = payload.get("steps") or []
        if not isinstance(steps, list):
            raise ContractError("steps must be a list")
        stored = {
            "validation_status": status,
            "validation_feedback": str(payload.get("validation_feedback") or ""),
            "answer": str(payload.get("answer") or ""),
            "reasoning": str(payload.get("reasoning") or ""),
            "attempt": attempt,
            "steps": steps,
        }
        saved_at = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO assignment_drafts
                   (assignment_id, expert_id, payload_json, saved_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(assignment_id) DO UPDATE SET
                     expert_id=excluded.expert_id,
                     payload_json=excluded.payload_json,
                     saved_at=excluded.saved_at""",
                (assignment_id, expert_id, canonical_json(stored), saved_at),
            )
        stored["saved_at"] = saved_at
        stored["assignment_id"] = assignment_id
        return stored

    def load_drafts(self, *, expert_id: str) -> dict[str, dict[str, Any]]:
        expert_id = require_identifier(expert_id, "expert_id")
        drafts: dict[str, dict[str, Any]] = {}
        for row in self.connection.execute(
            """SELECT assignment_id, payload_json, saved_at FROM assignment_drafts
               WHERE expert_id=?""",
            (expert_id,),
        ):
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                continue
            payload["saved_at"] = row["saved_at"]
            payload["assignment_id"] = row["assignment_id"]
            drafts[row["assignment_id"]] = payload
        return drafts

    def review_assignment(
        self,
        *,
        assignment_id: str,
        target_status: str,
        actor_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(target_status, str) or target_status not in {"approved", "returned"}:
            raise ContractError("review status must be approved or returned")
        if reason is not None:
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                raise ContractError("review reason must be 1 to 2000 characters")
            reason = reason.strip()
        if target_status == "returned" and reason is None:
            raise ContractError("returning an assignment requires a reason")
        assignment_id = require_hash_identifier(assignment_id, "assignment_id")
        actor_id = require_identifier(actor_id, "actor_id")
        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            assignment = self.connection.execute(
                "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
            ).fetchone()
            if assignment is None:
                raise ContractError("unknown assignment_id")
            payload = {"reason": reason} if reason is not None else None
            updated = self._transition_assignment_in_transaction(
                assignment,
                target_status=target_status,
                actor_id=actor_id,
                actor_role="admin",
                event_type=f"assignment.{target_status}",
                payload=payload,
            )
            self.connection.commit()
            transaction_started = False
            return dict(updated)
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise

    def import_experts_csv(self, csv_text: str) -> dict[str, Any]:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        required = {"expert_id", "name", "domains"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise PreflightError(
                ["experts CSV requires columns: schema_version,expert_id,name,domains"]
            )
        parsed: list[tuple[str, str, list[str]]] = []
        errors: list[str] = []
        seen: set[str] = set()
        for line_number, row in enumerate(reader, 2):
            try:
                if None in row:
                    raise ContractError("unexpected extra CSV column; quote values containing commas")
                version = (row.get("schema_version") or EXPERTS_SCHEMA_VERSION).strip()
                if version != EXPERTS_SCHEMA_VERSION:
                    raise ContractError(f"unsupported schema_version {version!r}")
                expert_id = require_identifier((row.get("expert_id") or "").strip(), "expert_id")
                name = (row.get("name") or "").strip()
                if not name:
                    raise ContractError("name is required")
                domains = normalize_domains(row.get("domains") or "")
                if expert_id in seen:
                    raise ContractError(f"duplicate expert_id {expert_id!r}")
                seen.add(expert_id)
                parsed.append((expert_id, name, domains))
            except ContractError as error:
                errors.append(f"line {line_number}: {error}")
        if errors:
            raise PreflightError(errors)
        if not parsed:
            raise PreflightError(["experts CSV has no data rows"])

        now = utc_now()
        existing_ids = {
            row["expert_id"]
            for row in self.connection.execute(
                f"SELECT expert_id FROM experts WHERE expert_id IN ({','.join('?' for _ in parsed)})",
                [row[0] for row in parsed],
            )
        } if parsed else set()
        with self.connection:
            self.connection.executemany(
                """INSERT INTO experts (expert_id,name,domains_json,active,updated_at)
                   VALUES (?,?,?,1,?)
                   ON CONFLICT(expert_id) DO UPDATE SET
                     name=excluded.name,domains_json=excluded.domains_json,
                     active=1,updated_at=excluded.updated_at""",
                [
                    (expert_id, name, canonical_json(domains), now)
                    for expert_id, name, domains in parsed
                ],
            )
        return {
            "schema_version": EXPERTS_SCHEMA_VERSION,
            "imported": len(parsed) - len(existing_ids),
            "updated": len(existing_ids),
            "total": len(parsed),
        }

    def import_experts_file(self, path: Path | str) -> dict[str, Any]:
        return self.import_experts_csv(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw, parse_constant=_reject_nonstandard_json_constant)
                if not isinstance(value, dict):
                    raise ValueError("row is not an object")
                rows.append(value)
            except (json.JSONDecodeError, ValueError) as error:
                errors.append(f"line {line_number}: {error}")
        if errors:
            raise PreflightError(errors)
        return rows

    def import_batch(
        self,
        *,
        batch_id: str,
        tasks_jsonl: Path | str,
        asset_root: Path | str,
        admission_receipt: Path | str | None = None,
        admission_purpose: str = ADMISSION_RECEIPT_PURPOSE,
        viewer_root: Path | str | None = None,
        title: str = "",
    ) -> dict[str, Any]:
        batch_id = require_identifier(batch_id, "batch_id")
        tasks_input = Path(tasks_jsonl).expanduser()
        assets_input = Path(asset_root).expanduser()
        admission_input = (
            Path(admission_receipt).expanduser() if admission_receipt is not None else None
        )
        viewer_input = Path(viewer_root).expanduser() if viewer_root else assets_input / "viewer"
        tasks_path = tasks_input.resolve()
        assets_path = assets_input.resolve()
        admission_path = admission_input.resolve() if admission_input is not None else None
        viewers_path = (
            viewer_input.resolve()
            if viewer_root
            else (assets_path / "viewer").resolve()
        )
        errors: list[str] = []
        if tasks_input.is_symlink():
            errors.append(f"tasks JSONL cannot be a symlink: {tasks_input}")
        if assets_input.is_symlink():
            errors.append(f"asset root cannot be a symlink: {assets_input}")
        if admission_input is None:
            errors.append("QC admission receipt is required")
        elif admission_input.is_symlink():
            errors.append(f"QC admission receipt cannot be a symlink: {admission_input}")
        if viewer_input.is_symlink():
            errors.append(f"viewer root cannot be a symlink: {viewer_input}")
        if not tasks_path.is_file():
            errors.append(f"tasks JSONL does not exist: {tasks_path}")
        if not assets_path.is_dir():
            errors.append(f"asset root does not exist: {assets_path}")
        if admission_path is not None and not admission_path.is_file():
            errors.append(f"QC admission receipt does not exist: {admission_path}")
        if not viewers_path.is_dir():
            errors.append(f"viewer root does not exist: {viewers_path}")
        if errors:
            raise PreflightError(errors)

        rows = self._read_jsonl(tasks_path)
        if not rows:
            raise PreflightError(["tasks JSONL is empty"])
        assert admission_path is not None
        try:
            receipt = json.loads(
                admission_path.read_text(encoding="utf-8"),
                parse_constant=_reject_nonstandard_json_constant,
            )
            if not isinstance(receipt, dict):
                raise ContractError("admission receipt must be an object")
            validate_qa_export_admission_receipt(
                receipt,
                tasks_path=tasks_path,
                tasks=rows,
                expected_purpose=admission_purpose,
            )
        except (json.JSONDecodeError, ValueError, ContractError) as error:
            raise PreflightError([f"QC admission receipt: {error}"]) from error
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Package construction rechecks each hash to catch post-import asset drift.
        verified_assets: dict[str, tuple[str, int]] = {}
        for index, task in enumerate(rows, 1):
            try:
                task_id = require_identifier(task.get("task_id"), "task_id")
                raw_domain = task.get("domain")
                if not isinstance(raw_domain, str) or not raw_domain.strip():
                    raise ContractError("domain is required")
                domain = raw_domain.strip().lower()
                if task_id in seen:
                    raise ContractError(f"duplicate task_id {task_id!r}")
                seen.add(task_id)
                assets = asset_records(task)
                for asset in assets:
                    verified = verified_assets.get(asset["path"])
                    if verified is not None:
                        verified_sha256, actual_size = verified
                        if verified_sha256 != asset["sha256"]:
                            raise ContractError(
                                f"conflicting hashes declared for asset {asset['path']!r}"
                            )
                        if (
                            asset["byte_size"] is not None
                            and actual_size != asset["byte_size"]
                        ):
                            raise ContractError(
                                f"asset byte_size mismatch for {asset['path']!r}"
                            )
                        continue
                    source = assets_path.joinpath(*asset["path"].split("/"))
                    resolved = source.resolve()
                    if (
                        not resolved.is_relative_to(assets_path)
                        or _path_has_symlink(assets_path, asset["path"])
                        or not source.is_file()
                    ):
                        raise ContractError(f"missing, linked, or escaped asset {asset['path']!r}")
                    actual_size = source.stat().st_size
                    if asset["byte_size"] is not None and actual_size != asset["byte_size"]:
                        raise ContractError(f"asset byte_size mismatch for {asset['path']!r}")
                    if file_sha256(source) != asset["sha256"]:
                        raise ContractError(f"asset sha256 mismatch for {asset['path']!r}")
                    verified_assets[asset["path"]] = (asset["sha256"], actual_size)
                prepared.append(
                    {
                        "task_id": task_id,
                        "domain": domain,
                        "revision": task_revision_sha256(task),
                        "payload": task,
                        "assets": assets,
                    }
                )
            except ContractError as error:
                errors.append(f"task row {index}: {error}")
        if errors:
            raise PreflightError(errors)

        active_experts = [
            json.loads(row["domains_json"])
            for row in self.connection.execute(
                "SELECT domains_json FROM experts WHERE active=1"
            )
        ]
        uncovered = sorted(
            {
                row["domain"]
                for row in prepared
                if not any(
                    row["domain"] in domains or "*" in domains or "all" in domains
                    for domains in active_experts
                )
            }
        )
        if uncovered:
            raise PreflightError(
                ["task domain(s) have no eligible active expert: " + ", ".join(uncovered)]
            )

        existing_batch = self.connection.execute(
            "SELECT * FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if existing_batch:
            existing_admission = self.connection.execute(
                "SELECT * FROM batch_admissions WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if not existing_admission:
                raise PreflightError(
                    [
                        f"batch {batch_id!r} predates QC admission; "
                        "use a new batch_id with a passed receipt"
                    ]
                )
            if existing_admission["receipt_digest"] != receipt["receipt_digest"]:
                raise PreflightError(
                    [
                        f"batch {batch_id!r} has an immutable QC admission receipt; "
                        "use a new batch_id"
                    ]
                )
            existing = {
                row["task_id"]: row["task_revision_sha256"]
                for row in self.connection.execute(
                    "SELECT task_id,task_revision_sha256 FROM tasks WHERE batch_id=?",
                    (batch_id,),
                )
            }
            incoming = {row["task_id"]: row["revision"] for row in prepared}
            if existing != incoming:
                changed = sorted(
                    task_id
                    for task_id in set(existing) | set(incoming)
                    if existing.get(task_id) != incoming.get(task_id)
                )
                raise PreflightError(
                    [
                        f"batch {batch_id!r} is immutable; use a new batch_id for changed tasks: "
                        + ", ".join(changed[:20])
                    ]
                )
            return {
                "schema_version": BATCH_SCHEMA_VERSION,
                "batch_id": batch_id,
                "imported": 0,
                "unchanged": len(prepared),
                "admission": admission_manifest_provenance(receipt),
            }

        now = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO batches
                   (batch_id,schema_version,title,tasks_source,asset_root,viewer_root,imported_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    batch_id,
                    BATCH_SCHEMA_VERSION,
                    title.strip() or batch_id,
                    str(tasks_path),
                    str(assets_path),
                    str(viewers_path),
                    now,
                ),
            )
            self.connection.executemany(
                """INSERT INTO tasks
                   (batch_id,task_id,domain,task_revision_sha256,payload_json,assets_json)
                   VALUES (?,?,?,?,?,?)""",
                [
                    (
                        batch_id,
                        row["task_id"],
                        row["domain"],
                        row["revision"],
                        canonical_json(row["payload"]),
                        canonical_json(row["assets"]),
                    )
                    for row in prepared
                ],
            )
            self.connection.execute(
                """INSERT INTO batch_admissions
                   (batch_id,schema_version,status,purpose,receipt_digest,policy_name,
                    policy_sha256,tasks_path,tasks_sha256,task_count,revision_count,
                    revision_digest,receipt_json,admitted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    batch_id,
                    ADMISSION_RECEIPT_SCHEMA_VERSION,
                    receipt["status"],
                    receipt["purpose"],
                    receipt["receipt_digest"],
                    receipt["policy"]["name"],
                    receipt["policy"]["sha256"],
                    receipt["tasks"]["path"],
                    receipt["tasks"]["sha256"],
                    receipt["tasks"]["count"],
                    receipt["task_revision_set"]["count"],
                    receipt["task_revision_set"]["digest"],
                    canonical_json(receipt),
                    now,
                ),
            )
        return {
            "schema_version": BATCH_SCHEMA_VERSION,
            "batch_id": batch_id,
            "imported": len(prepared),
            "unchanged": 0,
            "domains": dict(sorted(Counter(row["domain"] for row in prepared).items())),
            "admission": admission_manifest_provenance(receipt),
        }

    def _require_batch_admission(self, batch_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM batch_admissions WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if not row:
            raise PreflightError(
                [f"batch {batch_id!r} has no passed QC admission receipt"]
            )
        receipt = json.loads(row["receipt_json"])
        receipt_body = {
            key: value for key, value in receipt.items() if key != "receipt_digest"
        }
        if (
            receipt.get("receipt_digest") != row["receipt_digest"]
            or object_sha256(receipt_body) != row["receipt_digest"]
            or receipt.get("status") != row["status"]
            or receipt.get("purpose") != row["purpose"]
        ):
            raise PreflightError([f"batch {batch_id!r} QC admission provenance is corrupt"])
        return dict(row) | {
            "receipt": receipt,
            "manifest_provenance": admission_manifest_provenance(receipt),
        }

    def assign_explicit(
        self,
        *,
        batch_id: str,
        task_id: str,
        expert_id: str,
        actor_id: str = "task-manager",
        actor_role: str = "system",
    ) -> dict[str, Any]:
        batch_id = require_identifier(batch_id, "batch_id")
        task_id = require_identifier(task_id, "task_id")
        expert_id = require_identifier(expert_id, "expert_id")
        task = self.connection.execute(
            "SELECT * FROM tasks WHERE batch_id=? AND task_id=?", (batch_id, task_id)
        ).fetchone()
        if not task:
            raise PreflightError([f"unknown task {batch_id}/{task_id}"])
        self._require_batch_admission(batch_id)
        expert = self.connection.execute(
            "SELECT * FROM experts WHERE expert_id=? AND active=1", (expert_id,)
        ).fetchone()
        if not expert:
            raise PreflightError([f"unknown or inactive expert {expert_id!r}"])
        domains = json.loads(expert["domains_json"])
        if task["domain"] not in domains and "*" not in domains and "all" not in domains:
            raise PreflightError(
                [
                    f"expert {expert_id!r} is not eligible for task domain "
                    f"{task['domain']!r}"
                ]
            )
        existing = self.connection.execute(
            "SELECT * FROM assignments WHERE batch_id=? AND task_id=?", (batch_id, task_id)
        ).fetchone()
        if existing:
            if existing["expert_id"] == expert_id:
                return dict(existing) | {"unchanged": True}
            raise PreflightError(
                [f"task {task_id!r} is already assigned to {existing['expert_id']!r}"]
            )
        assignment_id = stable_assignment_id(
            batch_id, expert_id, task_id, task["task_revision_sha256"]
        )
        assigned_at = utc_now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO assignments
                   (assignment_id,schema_version,batch_id,task_id,task_revision_sha256,
                    expert_id,assigned_at,status)
                   VALUES (?,?,?,?,?,?,?,'assigned')""",
                (
                    assignment_id,
                    "sciencevision.assignment.v1",
                    batch_id,
                    task_id,
                    task["task_revision_sha256"],
                    expert_id,
                    assigned_at,
                ),
            )
            self._append_audit_event(
                actor_id=actor_id,
                actor_role=actor_role,
                event_type="assignment.assigned",
                entity_type="assignment",
                entity_id=assignment_id,
                batch_id=batch_id,
                assignment_id=assignment_id,
                payload={"expert_id": expert_id, "task_id": task_id},
                occurred_at=assigned_at,
            )
        return {
            "assignment_id": assignment_id,
            "batch_id": batch_id,
            "task_id": task_id,
            "expert_id": expert_id,
            "task_revision_sha256": task["task_revision_sha256"],
            "unchanged": False,
        }

    def auto_assign(
        self,
        *,
        batch_id: str,
        actor_id: str = "task-manager",
        actor_role: str = "system",
    ) -> dict[str, Any]:
        batch_id = require_identifier(batch_id, "batch_id")
        if not self.connection.execute(
            "SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone():
            raise PreflightError([f"unknown batch {batch_id!r}"])
        self._require_batch_admission(batch_id)
        experts = []
        for row in self.connection.execute(
            "SELECT * FROM experts WHERE active=1 ORDER BY expert_id"
        ):
            experts.append(dict(row) | {"domains": json.loads(row["domains_json"])})
        tasks = list(
            self.connection.execute(
                """SELECT t.* FROM tasks t
                   LEFT JOIN assignments a ON a.batch_id=t.batch_id AND a.task_id=t.task_id
                   WHERE t.batch_id=? AND a.assignment_id IS NULL
                   ORDER BY t.domain,t.task_id""",
                (batch_id,),
            )
        )
        missing = sorted(
            {
                task["domain"]
                for task in tasks
                if not any(
                    task["domain"] in expert["domains"]
                    or "*" in expert["domains"]
                    or "all" in expert["domains"]
                    for expert in experts
                )
            }
        )
        if missing:
            raise PreflightError(
                ["no active expert covers domain(s): " + ", ".join(missing)]
            )

        total_counts: Counter[str] = Counter()
        domain_counts: Counter[tuple[str, str]] = Counter()
        for row in self.connection.execute(
            """SELECT a.expert_id,t.domain,COUNT(*) count
               FROM assignments a JOIN tasks t
                 ON t.batch_id=a.batch_id AND t.task_id=a.task_id
               WHERE a.batch_id=? GROUP BY a.expert_id,t.domain""",
            (batch_id,),
        ):
            total_counts[row["expert_id"]] += row["count"]
            domain_counts[(row["expert_id"], row["domain"])] = row["count"]

        created: list[dict[str, Any]] = []
        now = utc_now()
        for task in tasks:
            eligible = [
                expert
                for expert in experts
                if task["domain"] in expert["domains"]
                or "*" in expert["domains"]
                or "all" in expert["domains"]
            ]
            chosen = min(
                eligible,
                key=lambda expert: (
                    domain_counts[(expert["expert_id"], task["domain"])],
                    total_counts[expert["expert_id"]],
                    expert["expert_id"],
                ),
            )
            assignment_id = stable_assignment_id(
                batch_id,
                chosen["expert_id"],
                task["task_id"],
                task["task_revision_sha256"],
            )
            created.append(
                {
                    "assignment_id": assignment_id,
                    "schema_version": "sciencevision.assignment.v1",
                    "batch_id": batch_id,
                    "task_id": task["task_id"],
                    "task_revision_sha256": task["task_revision_sha256"],
                    "expert_id": chosen["expert_id"],
                    "assigned_at": now,
                }
            )
            total_counts[chosen["expert_id"]] += 1
            domain_counts[(chosen["expert_id"], task["domain"])] += 1
        with self.connection:
            self.connection.executemany(
                """INSERT INTO assignments
                   (assignment_id,schema_version,batch_id,task_id,task_revision_sha256,
                    expert_id,assigned_at,status)
                   VALUES (:assignment_id,:schema_version,:batch_id,:task_id,
                           :task_revision_sha256,:expert_id,:assigned_at,'assigned')""",
                created,
            )
            for assignment in created:
                self._append_audit_event(
                    actor_id=actor_id,
                    actor_role=actor_role,
                    event_type="assignment.assigned",
                    entity_type="assignment",
                    entity_id=assignment["assignment_id"],
                    batch_id=batch_id,
                    assignment_id=assignment["assignment_id"],
                    payload={
                        "expert_id": assignment["expert_id"],
                        "task_id": assignment["task_id"],
                    },
                    occurred_at=now,
                )
        return {
            "batch_id": batch_id,
            "assigned": len(created),
            "counts": dict(sorted(Counter(row["expert_id"] for row in created).items())),
        }

    def package_inputs(self, *, batch_id: str, expert_id: str) -> dict[str, Any]:
        batch = self.connection.execute(
            "SELECT * FROM batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        expert = self.connection.execute(
            "SELECT * FROM experts WHERE expert_id=?", (expert_id,)
        ).fetchone()
        if not batch or not expert:
            raise PreflightError(["unknown batch or expert"])
        admission = self._require_batch_admission(batch_id)
        rows = self.connection.execute(
            """SELECT a.*,t.domain,t.payload_json,t.assets_json
               FROM assignments a JOIN tasks t
                 ON t.batch_id=a.batch_id AND t.task_id=a.task_id
               WHERE a.batch_id=? AND a.expert_id=? ORDER BY a.task_id""",
            (batch_id, expert_id),
        ).fetchall()
        if not rows:
            raise PreflightError([f"expert {expert_id!r} has no assignments in {batch_id!r}"])
        assignments = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item["assets"] = json.loads(item.pop("assets_json"))
            assignments.append(item)
        return {
            "batch": dict(batch),
            "expert": dict(expert) | {"domains": json.loads(expert["domains_json"])},
            "assignments": assignments,
            "admission": admission["manifest_provenance"],
        }

    def _prepare_expert_bundle(self, *, batch_id: str, expert_id: str) -> dict[str, Any]:
        inputs = self.package_inputs(batch_id=batch_id, expert_id=expert_id)
        prepared = prepare_expert_bundle(inputs, batch_id=batch_id, expert_id=expert_id)
        prepared["viewer_root"] = str(Path(inputs["batch"]["viewer_root"]).resolve())
        asset_root = Path(prepared["asset_root"])
        for record in prepared["assets"]:
            relative = record["path"]
            path = asset_root.joinpath(*relative.split("/"))
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(asset_root)
                or _path_has_symlink(asset_root, relative)
                or not path.is_file()
            ):
                raise PreflightError([f"missing, linked, or escaped asset: {relative}"])
            if file_sha256(path) != record["sha256"]:
                raise PreflightError([f"asset hash mismatch: {relative}"])
            if (
                record.get("byte_size") is not None
                and path.stat().st_size != record["byte_size"]
            ):
                raise PreflightError([f"asset byte_size mismatch: {relative}"])
        return prepared

    def prepare_bundle_export(self, *, batch_id: str, expert_id: str) -> dict[str, Any]:
        """Prepare and revalidate one immutable bundle without activating it."""

        batch_id = require_identifier(batch_id, "batch_id")
        expert_id = require_identifier(expert_id, "expert_id")
        self._require_batch_admission(batch_id)
        unassigned = self.unassigned_tasks(batch_id)
        if unassigned:
            raise PreflightError(
                [f"batch {batch_id!r} still has {len(unassigned)} unassigned task(s)"]
            )
        return self._prepare_expert_bundle(batch_id=batch_id, expert_id=expert_id)

    def _insert_task_bundle(self, prepared: dict[str, Any], created_at: str) -> None:
        manifest = prepared["manifest"]
        existing = self.connection.execute(
            "SELECT * FROM task_bundles WHERE bundle_id=?", (prepared["bundle_id"],)
        ).fetchone()
        if existing:
            if (
                existing["manifest_sha256"] != manifest["bundle_manifest_sha256"]
                or existing["tasks_sha256"] != prepared["tasks_sha256"]
                or existing["asset_paths_json"] != canonical_json(prepared["asset_paths"])
            ):
                raise ContractError("bundle_id already exists with different immutable content")
            return
        self.connection.execute(
            """INSERT INTO task_bundles
               (bundle_id,schema_version,batch_id,expert_id,manifest_sha256,manifest_json,
                tasks_sha256,tasks_jsonl,asset_paths_json,asset_root,source_kind,source_root,
                created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prepared["bundle_id"],
                manifest["schema_version"],
                manifest["batch_id"],
                manifest["expert_id"],
                manifest["bundle_manifest_sha256"],
                canonical_json(manifest),
                prepared["tasks_sha256"],
                prepared["tasks_jsonl"],
                canonical_json(prepared["asset_paths"]),
                prepared["asset_root"],
                prepared["source_kind"],
                prepared.get("source_root"),
                created_at,
            ),
        )

    def register_prepared_bundle(self, prepared: dict[str, Any]) -> dict[str, Any]:
        """Register one fully validated v2 bundle without changing active pointers."""

        manifest = prepared.get("manifest")
        if not isinstance(manifest, dict) or manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
            raise ContractError("cannot register an unsupported prepared bundle")
        now = utc_now()
        with self.connection:
            self._insert_task_bundle(prepared, now)
        return {
            "bundle_id": prepared["bundle_id"],
            "expert_id": manifest["expert_id"],
            "registered_at": now,
            "activated": False,
        }

    def publish_batch(self, *, batch_id: str) -> dict[str, Any]:
        """Materialize every expert bundle, then atomically switch active pointers."""

        batch_id = require_identifier(batch_id, "batch_id")
        self._require_batch_admission(batch_id)
        unassigned = self.unassigned_tasks(batch_id)
        if unassigned:
            raise PreflightError(
                [f"batch {batch_id!r} still has {len(unassigned)} unassigned task(s)"]
            )
        expert_ids = [
            row["expert_id"]
            for row in self.connection.execute(
                """SELECT DISTINCT expert_id FROM assignments
                   WHERE batch_id=? ORDER BY expert_id""",
                (batch_id,),
            )
        ]
        if not expert_ids:
            raise PreflightError([f"batch {batch_id!r} has no assignments"])
        # All expensive validation and redaction happens before active state changes.
        prepared = [
            self._prepare_expert_bundle(batch_id=batch_id, expert_id=expert_id)
            for expert_id in expert_ids
        ]
        runtime_manifests: dict[str, dict[str, Any]] = {}
        expert_app_root = Path(__file__).resolve().parent / "expert_app"
        for item in prepared:
            viewer_root = item["viewer_root"]
            runtime_manifest = runtime_manifests.get(viewer_root)
            if runtime_manifest is None:
                runtime_manifest = build_runtime_manifest(
                    Path(viewer_root), expert_app_root=expert_app_root
                )
                runtime_manifests[viewer_root] = runtime_manifest
            require_runtime_compatibility(
                item["manifest"].get("runtime_requirement"), runtime_manifest
            )
        now = utc_now()
        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            for item in prepared:
                self._insert_task_bundle(item, now)
            self.connection.executemany(
                """INSERT INTO active_task_bundles (expert_id,bundle_id,activated_at)
                   VALUES (?,?,?)
                   ON CONFLICT(expert_id) DO UPDATE SET
                     bundle_id=excluded.bundle_id,activated_at=excluded.activated_at""",
                [(item["manifest"]["expert_id"], item["bundle_id"], now) for item in prepared],
            )
            self.connection.commit()
            transaction_started = False
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise
        return {
            "schema_version": "sciencevision.bundle_publish_report.v1",
            "batch_id": batch_id,
            "published": len(prepared),
            "bundles": [
                {
                    "expert_id": item["manifest"]["expert_id"],
                    "bundle_id": item["bundle_id"],
                    "task_count": item["manifest"]["task_file"]["count"],
                }
                for item in prepared
            ],
            "activated_at": now,
        }

    def import_legacy_package(
        self,
        package_root: Path | str,
        *,
        activate: bool = True,
        integrity_policy: str = LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
    ) -> dict[str, Any]:
        prepared = prepare_legacy_bundle(
            Path(package_root), integrity_policy=integrity_policy
        )
        manifest = prepared["manifest"]
        if not self.connection.execute(
            "SELECT 1 FROM experts WHERE expert_id=?", (manifest["expert_id"],)
        ).fetchone():
            raise PreflightError(["legacy package expert is not present in the manager"])
        if not self.connection.execute(
            "SELECT 1 FROM batches WHERE batch_id=?", (manifest["batch_id"],)
        ).fetchone():
            raise PreflightError(["legacy package batch is not present in the manager"])
        now = utc_now()
        with self.connection:
            self._insert_task_bundle(prepared, now)
            existing_package = self.connection.execute(
                "SELECT * FROM packages WHERE package_id=?", (prepared["bundle_id"],)
            ).fetchone()
            legacy = manifest["legacy_package"]
            if existing_package:
                if (
                    existing_package["batch_id"] != manifest["batch_id"]
                    or existing_package["expert_id"] != manifest["expert_id"]
                    or existing_package["manifest_sha256"]
                    != legacy["package_manifest_sha256"]
                ):
                    raise ContractError(
                        "legacy package_id already exists with different immutable content"
                    )
            else:
                self.connection.execute(
                    """INSERT INTO packages
                       (package_id,schema_version,batch_id,expert_id,manifest_sha256,
                        assignments_json,output_path,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        prepared["bundle_id"],
                        PACKAGE_SCHEMA_VERSION,
                        manifest["batch_id"],
                        manifest["expert_id"],
                        legacy["package_manifest_sha256"],
                        canonical_json(manifest["assignments"]),
                        prepared["source_root"],
                        now,
                    ),
                )
            if activate:
                self.connection.execute(
                    """INSERT INTO active_task_bundles (expert_id,bundle_id,activated_at)
                       VALUES (?,?,?)
                       ON CONFLICT(expert_id) DO UPDATE SET
                         bundle_id=excluded.bundle_id,activated_at=excluded.activated_at""",
                    (manifest["expert_id"], prepared["bundle_id"], now),
                )
        return {
            "schema_version": manifest["schema_version"],
            "bundle_id": prepared["bundle_id"],
            "expert_id": manifest["expert_id"],
            "task_count": manifest["task_file"]["count"],
            "admission_status": "legacy_pre_admission",
            "activated": activate,
            "source_root": prepared["source_root"],
        }

    def active_bundle_context(self, expert_id: str) -> dict[str, Any]:
        expert_id = require_identifier(expert_id, "expert_id")
        row = self.connection.execute(
            """SELECT b.*,ba.viewer_root FROM active_task_bundles a
               JOIN task_bundles b ON b.bundle_id=a.bundle_id
               JOIN batches ba ON ba.batch_id=b.batch_id
               WHERE a.expert_id=?""",
            (expert_id,),
        ).fetchone()
        if not row:
            raise PreflightError([f"no active task bundle for expert {expert_id!r}"])
        tasks_jsonl = row["tasks_jsonl"]
        actual_sha256 = hashlib.sha256(tasks_jsonl.encode("utf-8")).hexdigest()
        if actual_sha256 != row["tasks_sha256"]:
            raise ContractError("stored task bundle bytes failed their digest")
        manifest = json.loads(row["manifest_json"])
        manifest_body = {
            key: value for key, value in manifest.items() if key != "bundle_manifest_sha256"
        }
        if (
            manifest.get("bundle_manifest_sha256") != row["manifest_sha256"]
            or object_sha256(manifest_body) != row["manifest_sha256"]
        ):
            raise ContractError("stored task bundle manifest digest mismatch")
        viewer_root = Path(row["viewer_root"]).resolve()
        if row["source_kind"] == "legacy-v1" and row["source_root"]:
            legacy_viewer = Path(row["source_root"]).resolve() / "viewer"
            if legacy_viewer.is_dir() and not legacy_viewer.is_symlink():
                viewer_root = legacy_viewer
        return {
            "bundle_id": row["bundle_id"],
            "manifest": manifest,
            "tasks_jsonl": tasks_jsonl,
            "allowed_assets": set(json.loads(row["asset_paths_json"])),
            "asset_root": Path(row["asset_root"]).resolve(),
            "viewer_root": viewer_root,
            "source_kind": row["source_kind"],
        }

    def import_trajectory(
        self,
        *,
        expert_id: str,
        payload: Any,
        identity: Any,
        artifact_root: Path,
        received_at: str,
    ) -> dict[str, Any]:
        """Record a browser trajectory against this expert's active bundle."""

        context = self.active_bundle_context(expert_id)
        return _import_trajectory(
            self.connection,
            expert_id=expert_id,
            bundle_id=context["bundle_id"],
            bundle_task_ids=trajectory_task_ids(context["tasks_jsonl"]),
            payload=payload,
            identity=identity,
            artifact_root=artifact_root,
            received_at=received_at,
        )

    def unassigned_tasks(self, batch_id: str) -> list[str]:
        return [
            row["task_id"]
            for row in self.connection.execute(
                """SELECT t.task_id FROM tasks t
                   LEFT JOIN assignments a
                     ON a.batch_id=t.batch_id AND a.task_id=t.task_id
                   WHERE t.batch_id=? AND a.assignment_id IS NULL
                   ORDER BY t.task_id""",
                (batch_id,),
            )
        ]

    def package_exists(self, package_id: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM packages WHERE package_id=?", (package_id,)
            ).fetchone()
        )

    def register_package(self, manifest: dict[str, Any], output_path: Path) -> None:
        if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
            raise ContractError("cannot register an unsupported package manifest")
        expected_admission = self._require_batch_admission(
            str(manifest.get("batch_id", ""))
        )["manifest_provenance"]
        if manifest.get("admission") != expected_admission:
            raise ContractError("package manifest QC admission provenance mismatch")
        with self.connection:
            self.connection.execute(
                """INSERT INTO packages
                   (package_id,schema_version,batch_id,expert_id,manifest_sha256,
                    assignments_json,output_path,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    manifest["package_id"],
                    manifest["schema_version"],
                    manifest["batch_id"],
                    manifest["expert_id"],
                    manifest["manifest_sha256"],
                    canonical_json(manifest["assignments"]),
                    str(output_path.resolve()),
                    manifest["created_at"],
                ),
            )

    @staticmethod
    def _require_submission_assignment(
        assignment: sqlite3.Row, *, actor_id: str, actor_role: str
    ) -> None:
        if actor_role == "expert" and actor_id != assignment["expert_id"]:
            raise ContractError("online submission actor does not own assignment")
        if actor_role not in _AUDIT_ACTOR_ROLES:
            raise ContractError("submission actor role is invalid")
        allowed = {"assigned", "in_progress", "returned"}
        if actor_role != "expert":
            allowed.update({"submitted", "pending_review"})
        if assignment["status"] not in allowed:
            raise ContractError(
                f"assignment status {assignment['status']!r} does not allow submission"
            )

    def _advance_assignment_for_submission(
        self,
        *,
        assignment_id: str,
        actor_id: str,
        actor_role: str,
        source: str,
        submission_id: str,
        attempt: int,
        container_id: str,
        occurred_at: str,
    ) -> None:
        assignment = self.connection.execute(
            "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
        ).fetchone()
        if assignment is None:
            raise ContractError("unknown assignment_id")
        self._require_submission_assignment(
            assignment, actor_id=actor_id, actor_role=actor_role
        )
        if assignment["status"] == "pending_review":
            return
        shared_payload = {
            "attempt": attempt,
            "container_id": container_id,
            "source": source,
            "submission_id": submission_id,
        }
        if assignment["status"] in {"assigned", "returned"}:
            assignment = self._transition_assignment_in_transaction(
                assignment,
                target_status="in_progress",
                actor_id=actor_id,
                actor_role=actor_role,
                event_type="assignment.opened",
                payload=shared_payload,
                occurred_at=occurred_at,
            )
        if assignment["status"] == "in_progress":
            assignment = self._transition_assignment_in_transaction(
                assignment,
                target_status="submitted",
                actor_id=actor_id,
                actor_role=actor_role,
                event_type="assignment.submitted",
                payload=shared_payload,
                occurred_at=occurred_at,
            )
        pending_actor_id = actor_id if actor_role == "system" else "task-manager"
        pending_actor_role = "system"
        self._transition_assignment_in_transaction(
            assignment,
            target_status="pending_review",
            actor_id=pending_actor_id,
            actor_role=pending_actor_role,
            event_type="assignment.pending_review",
            payload=shared_payload,
            occurred_at=occurred_at,
        )

    def _require_active_online_container(self, expert_id: str, container_id: str) -> None:
        active = self.connection.execute(
            "SELECT bundle_id FROM active_task_bundles WHERE expert_id=?", (expert_id,)
        ).fetchone()
        if active is None or active["bundle_id"] != container_id:
            raise ContractError("online submission does not target the active task bundle")

    def import_submission(
        self,
        envelope: dict[str, Any],
        *,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> dict[str, Any]:
        if (actor_id is None) != (actor_role is None):
            raise ContractError("submission actor_id and actor_role must be provided together")
        if actor_id is None:
            actor_id = "offline-import"
            actor_role = "system"
            source = "offline_import"
        else:
            actor_id = require_identifier(actor_id, "actor_id")
            if actor_role not in _AUDIT_ACTOR_ROLES:
                raise ContractError("submission actor_role must be admin, expert, or system")
            source = "online_submit" if actor_role == "expert" else "offline_import"
            if actor_role == "admin":
                source = "admin_http_import"
        if actor_role == "expert":
            if not isinstance(envelope, dict) or envelope.get("expert_id") != actor_id:
                raise ContractError("online submission actor does not match expert_id")
        if (
            isinstance(envelope, dict)
            and envelope.get("schema_version") == BUNDLE_SUBMISSION_SCHEMA_VERSION
        ):
            return self._import_bundle_submission(
                envelope, actor_id=actor_id, actor_role=actor_role, source=source
            )
        report: dict[str, Any] = {
            "schema_version": IMPORT_REPORT_SCHEMA_VERSION,
            "imported_at": utc_now(),
            "package_id": envelope.get("package_id") if isinstance(envelope, dict) else None,
            "accepted": [],
            "duplicates": [],
            "rejected": [],
        }

        def finish(status: str) -> dict[str, Any]:
            report["submission_status"] = status
            report["counts"] = {
                "accepted": len(report["accepted"]),
                "duplicates": len(report["duplicates"]),
                "rejected": len(report["rejected"]),
            }
            return report

        if not isinstance(envelope, dict):
            report["rejected"].append({"scope": "envelope", "reason": "submission is not an object"})
            return finish("not_imported")
        try:
            _require_exact_fields(envelope, _SUBMISSION_FIELDS, "submission")
            if envelope.get("schema_version") != SUBMISSION_SCHEMA_VERSION:
                raise ContractError("unsupported submission schema_version")
            if envelope.get("contract_version") != CONTRACT_VERSION:
                raise ContractError("unsupported contract_version")
            expected_payload_hash = envelope.get("payload_sha256")
            try:
                actual_payload_hash = submission_payload_sha256(envelope)
            except (TypeError, ValueError) as error:
                raise ContractError("submission is not canonical JSON") from error
            if expected_payload_hash != actual_payload_hash:
                raise ContractError("submission payload_sha256 mismatch")
            submission_id = require_hash_identifier(
                envelope.get("submission_id"), "submission_id"
            )
            package_id = require_hash_identifier(envelope.get("package_id"), "package_id")
            require_identifier(envelope.get("batch_id"), "batch_id")
            require_identifier(envelope.get("expert_id"), "expert_id")
            if not re.fullmatch(r"[0-9a-f]{64}", str(envelope.get("package_manifest_sha256", ""))):
                raise ContractError("package_manifest_sha256 must be a lowercase SHA-256")
            results = envelope.get("results")
            if not isinstance(results, list) or not results:
                raise ContractError("results must be a non-empty list")
        except ContractError as error:
            report["rejected"].append({"scope": "envelope", "reason": str(error)})
            return finish("not_imported")

        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            package = self.connection.execute(
                "SELECT * FROM packages WHERE package_id=?", (package_id,)
            ).fetchone()
            if not package:
                raise ContractError(f"unknown package_id {package_id!r}")
            for field in ("expert_id", "batch_id"):
                if envelope.get(field) != package[field]:
                    raise ContractError(f"submission {field} does not match package")
            if envelope.get("package_manifest_sha256") != package["manifest_sha256"]:
                raise ContractError("package manifest hash mismatch")
            if actor_role == "expert":
                self._require_active_online_container(actor_id, package_id)
            existing_submission = self.connection.execute(
                "SELECT * FROM submissions WHERE submission_id=?", (submission_id,)
            ).fetchone()
            if existing_submission:
                if existing_submission["payload_sha256"] == expected_payload_hash:
                    report["submission_id"] = submission_id
                    report["submission_status"] = "unchanged"
                    report["duplicates"].append(
                        {"scope": "submission", "submission_id": submission_id}
                    )
                    self.connection.commit()
                    transaction_started = False
                    return finish("unchanged")
                raise ContractError("submission_id already exists with different content")
            manifest_assignments = json.loads(package["assignments_json"])
            if not isinstance(manifest_assignments, list):
                raise ContractError("registered package assignment closure is invalid")
            package_assignment_ids = {
                row.get("assignment_id")
                for row in manifest_assignments
                if isinstance(row, dict)
            }

            pending: list[tuple[dict[str, Any], str]] = []
            seen_keys: set[tuple[str, int]] = set()
            pending_online_assignments: set[str] = set()
            for index, result in enumerate(results):
                identity = {"index": index}
                try:
                    if not isinstance(result, dict):
                        raise ContractError("result is not an object")
                    identity.update(
                        assignment_id=result.get("assignment_id"),
                        attempt=result.get("attempt"),
                    )
                    _require_exact_fields(result, _RESULT_FIELDS, "result")
                    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
                        raise ContractError("unsupported result schema_version")
                    if result.get("package_id") != package["package_id"]:
                        raise ContractError("result package_id mismatch")
                    for field in ("expert_id", "batch_id"):
                        if result.get(field) != package[field]:
                            raise ContractError(f"result {field} mismatch")
                    assignment_id = require_hash_identifier(
                        result.get("assignment_id"), "assignment_id"
                    )
                    attempt = result.get("attempt")
                    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
                        raise ContractError("attempt must be a positive integer")
                    key = (assignment_id, attempt)
                    if key in seen_keys:
                        raise ContractError("duplicate assignment_id + attempt in submission")
                    seen_keys.add(key)
                    if assignment_id not in package_assignment_ids:
                        raise ContractError("assignment_id is not part of this package")
                    assignment = self.connection.execute(
                        "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
                    ).fetchone()
                    if not assignment:
                        raise ContractError("unknown assignment_id")
                    for field in (
                        "expert_id",
                        "batch_id",
                        "task_id",
                        "task_revision_sha256",
                    ):
                        if result.get(field) != assignment[field]:
                            raise ContractError(f"result {field} does not match assignment")
                    validation = result.get("task_validation")
                    if not isinstance(validation, dict):
                        raise ContractError("task_validation must be an object")
                    _require_exact_fields(validation, _VALIDATION_FIELDS, "task_validation")
                    if not isinstance(validation.get("feedback"), str):
                        raise ContractError("task_validation.feedback must be text")
                    if validation.get("status") not in {"valid", "invalid", "uncertain"}:
                        raise ContractError("task_validation.status is invalid")
                    if not isinstance(result.get("answer"), str) or not isinstance(
                        result.get("reasoning"), str
                    ):
                        raise ContractError("answer and reasoning must be text")
                    _validate_trajectory(result.get("trajectory"))
                    if not isinstance(result.get("evidence"), list):
                        raise ContractError("evidence must be a list")
                    saved_at = result.get("saved_at")
                    if not isinstance(saved_at, str) or not saved_at or len(saved_at) > 128:
                        raise ContractError("saved_at must be non-empty timestamp text")
                    try:
                        result_hash = object_sha256(result)
                    except (TypeError, ValueError) as error:
                        raise ContractError("result is not canonical JSON") from error
                    existing = self.connection.execute(
                        "SELECT result_sha256 FROM results WHERE assignment_id=? AND attempt=?",
                        key,
                    ).fetchone()
                    bundle_existing = self.connection.execute(
                        """SELECT 1 FROM bundle_results
                           WHERE assignment_id=? AND attempt=?""",
                        key,
                    ).fetchone()
                    if bundle_existing:
                        raise ContractError(
                            "assignment_id + attempt already exists in the v2 exchange"
                        )
                    if existing:
                        if existing["result_sha256"] == result_hash:
                            report["duplicates"].append(identity)
                        else:
                            raise ContractError(
                                "assignment_id + attempt already exists with different content"
                            )
                    else:
                        if actor_role == "expert":
                            if assignment_id in pending_online_assignments:
                                raise ContractError(
                                    "online submission has multiple attempts for one assignment"
                                )
                            pending_online_assignments.add(assignment_id)
                        self._require_submission_assignment(
                            assignment, actor_id=actor_id, actor_role=actor_role
                        )
                        pending.append((result, result_hash))
                except ContractError as error:
                    report["rejected"].append(identity | {"reason": str(error)})

            if not pending:
                self.connection.commit()
                transaction_started = False
                report["submission_id"] = submission_id
                return finish("not_imported")

            self.connection.execute(
                """INSERT INTO submissions
                   (submission_id,package_id,payload_sha256,imported_at)
                   VALUES (?,?,?,?)""",
                (
                    submission_id,
                    package["package_id"],
                    expected_payload_hash,
                    report["imported_at"],
                ),
            )
            advanced_assignments: set[str] = set()
            for result, result_hash in pending:
                if result["assignment_id"] not in advanced_assignments:
                    self._advance_assignment_for_submission(
                        assignment_id=result["assignment_id"],
                        actor_id=actor_id,
                        actor_role=actor_role,
                        source=source,
                        submission_id=submission_id,
                        attempt=result["attempt"],
                        container_id=result["package_id"],
                        occurred_at=report["imported_at"],
                    )
                    advanced_assignments.add(result["assignment_id"])
                self.connection.execute(
                    """INSERT INTO results
                       (assignment_id,attempt,submission_id,package_id,expert_id,batch_id,task_id,
                        task_revision_sha256,result_sha256,payload_json,imported_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        result["assignment_id"],
                        result["attempt"],
                        submission_id,
                        result["package_id"],
                        result["expert_id"],
                        result["batch_id"],
                        result["task_id"],
                        result["task_revision_sha256"],
                        result_hash,
                        canonical_json(result),
                        report["imported_at"],
                    ),
                )
                if actor_role != "expert":
                    self._append_audit_event(
                        actor_id=actor_id,
                        actor_role=actor_role,
                        event_type="assignment.result_imported",
                        entity_type="assignment",
                        entity_id=result["assignment_id"],
                        batch_id=result["batch_id"],
                        assignment_id=result["assignment_id"],
                        payload={
                            "attempt": result["attempt"],
                            "container_id": result["package_id"],
                            "source": source,
                            "submission_id": submission_id,
                        },
                        occurred_at=report["imported_at"],
                    )
                report["accepted"].append(
                    {
                        "assignment_id": result["assignment_id"],
                        "attempt": result["attempt"],
                    }
                )
            self.connection.commit()
            transaction_started = False
            report["submission_id"] = submission_id
            return finish("imported")
        except ContractError as error:
            if transaction_started:
                self.connection.rollback()
            report["rejected"].append({"scope": "envelope", "reason": str(error)})
            return finish("not_imported")
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise

    def _import_bundle_submission(
        self,
        envelope: dict[str, Any],
        *,
        actor_id: str,
        actor_role: str,
        source: str,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {
            "schema_version": IMPORT_REPORT_SCHEMA_VERSION,
            "imported_at": utc_now(),
            "bundle_id": envelope.get("bundle_id"),
            "accepted": [],
            "duplicates": [],
            "rejected": [],
        }

        def finish(status: str) -> dict[str, Any]:
            report["submission_status"] = status
            report["counts"] = {
                "accepted": len(report["accepted"]),
                "duplicates": len(report["duplicates"]),
                "rejected": len(report["rejected"]),
            }
            return report

        try:
            _require_exact_fields(envelope, _BUNDLE_SUBMISSION_FIELDS, "submission")
            if envelope.get("schema_version") != BUNDLE_SUBMISSION_SCHEMA_VERSION:
                raise ContractError("unsupported submission schema_version")
            if envelope.get("contract_version") != BUNDLE_CONTRACT_VERSION:
                raise ContractError("unsupported contract_version")
            expected_payload_hash = envelope.get("payload_sha256")
            try:
                actual_payload_hash = submission_payload_sha256(envelope)
            except (TypeError, ValueError) as error:
                raise ContractError("submission is not canonical JSON") from error
            if expected_payload_hash != actual_payload_hash:
                raise ContractError("submission payload_sha256 mismatch")
            submission_id = require_hash_identifier(
                envelope.get("submission_id"), "submission_id"
            )
            bundle_id = require_hash_identifier(envelope.get("bundle_id"), "bundle_id")
            if not bundle_id.startswith("bndl_"):
                raise ContractError("v2 submission bundle_id must identify a v2 bundle")
            require_identifier(envelope.get("batch_id"), "batch_id")
            require_identifier(envelope.get("expert_id"), "expert_id")
            if not re.fullmatch(
                r"[0-9a-f]{64}", str(envelope.get("bundle_manifest_sha256", ""))
            ):
                raise ContractError("bundle_manifest_sha256 must be a lowercase SHA-256")
            results = envelope.get("results")
            if not isinstance(results, list) or not results:
                raise ContractError("results must be a non-empty list")
        except ContractError as error:
            report["rejected"].append({"scope": "envelope", "reason": str(error)})
            return finish("not_imported")

        transaction_started = False
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            transaction_started = True
            bundle = self.connection.execute(
                "SELECT * FROM task_bundles WHERE bundle_id=?", (bundle_id,)
            ).fetchone()
            if not bundle:
                raise ContractError(f"unknown bundle_id {bundle_id!r}")
            if bundle["source_kind"] != "v2":
                raise ContractError("v2 submissions require a v2 task bundle")
            for field in ("expert_id", "batch_id"):
                if envelope.get(field) != bundle[field]:
                    raise ContractError(f"submission {field} does not match bundle")
            if envelope.get("bundle_manifest_sha256") != bundle["manifest_sha256"]:
                raise ContractError("bundle manifest hash mismatch")
            if actor_role == "expert":
                self._require_active_online_container(actor_id, bundle_id)
            existing_submission = self.connection.execute(
                "SELECT * FROM bundle_submissions WHERE submission_id=?", (submission_id,)
            ).fetchone()
            if existing_submission:
                if existing_submission["payload_sha256"] == expected_payload_hash:
                    report["submission_id"] = submission_id
                    report["duplicates"].append(
                        {"scope": "submission", "submission_id": submission_id}
                    )
                    self.connection.commit()
                    transaction_started = False
                    return finish("unchanged")
                raise ContractError("submission_id already exists with different content")

            manifest = json.loads(bundle["manifest_json"])
            manifest_assignments = manifest.get("assignments")
            if not isinstance(manifest_assignments, list):
                raise ContractError("registered bundle assignment closure is invalid")
            bundle_assignment_ids = {
                row.get("assignment_id")
                for row in manifest_assignments
                if isinstance(row, dict)
            }
            pending: list[tuple[dict[str, Any], str]] = []
            seen_keys: set[tuple[str, int]] = set()
            pending_online_assignments: set[str] = set()
            for index, result in enumerate(results):
                identity = {"index": index}
                try:
                    if not isinstance(result, dict):
                        raise ContractError("result is not an object")
                    identity.update(
                        assignment_id=result.get("assignment_id"),
                        attempt=result.get("attempt"),
                    )
                    _require_exact_fields(result, _BUNDLE_RESULT_FIELDS, "result")
                    if result.get("schema_version") != BUNDLE_RESULT_SCHEMA_VERSION:
                        raise ContractError("unsupported result schema_version")
                    if result.get("bundle_id") != bundle["bundle_id"]:
                        raise ContractError("result bundle_id mismatch")
                    for field in ("expert_id", "batch_id"):
                        if result.get(field) != bundle[field]:
                            raise ContractError(f"result {field} mismatch")
                    assignment_id = require_hash_identifier(
                        result.get("assignment_id"), "assignment_id"
                    )
                    attempt = result.get("attempt")
                    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
                        raise ContractError("attempt must be a positive integer")
                    key = (assignment_id, attempt)
                    if key in seen_keys:
                        raise ContractError("duplicate assignment_id + attempt in submission")
                    seen_keys.add(key)
                    if assignment_id not in bundle_assignment_ids:
                        raise ContractError("assignment_id is not part of this bundle")
                    assignment = self.connection.execute(
                        "SELECT * FROM assignments WHERE assignment_id=?", (assignment_id,)
                    ).fetchone()
                    if not assignment:
                        raise ContractError("unknown assignment_id")
                    for field in (
                        "expert_id",
                        "batch_id",
                        "task_id",
                        "task_revision_sha256",
                    ):
                        if result.get(field) != assignment[field]:
                            raise ContractError(f"result {field} does not match assignment")
                    validation = result.get("task_validation")
                    if not isinstance(validation, dict):
                        raise ContractError("task_validation must be an object")
                    _require_exact_fields(validation, _VALIDATION_FIELDS, "task_validation")
                    if not isinstance(validation.get("feedback"), str):
                        raise ContractError("task_validation.feedback must be text")
                    if validation.get("status") not in {"valid", "invalid", "uncertain"}:
                        raise ContractError("task_validation.status is invalid")
                    if not isinstance(result.get("answer"), str) or not isinstance(
                        result.get("reasoning"), str
                    ):
                        raise ContractError("answer and reasoning must be text")
                    _validate_trajectory(result.get("trajectory"))
                    if not isinstance(result.get("evidence"), list):
                        raise ContractError("evidence must be a list")
                    saved_at = result.get("saved_at")
                    if not isinstance(saved_at, str) or not saved_at or len(saved_at) > 128:
                        raise ContractError("saved_at must be non-empty timestamp text")
                    try:
                        result_hash = object_sha256(result)
                    except (TypeError, ValueError) as error:
                        raise ContractError("result is not canonical JSON") from error
                    existing = self.connection.execute(
                        """SELECT result_sha256 FROM bundle_results
                           WHERE assignment_id=? AND attempt=?""",
                        key,
                    ).fetchone()
                    legacy_existing = self.connection.execute(
                        """SELECT 1 FROM results
                           WHERE assignment_id=? AND attempt=?""",
                        key,
                    ).fetchone()
                    if legacy_existing:
                        raise ContractError(
                            "assignment_id + attempt already exists in the v1 exchange"
                        )
                    if existing:
                        if existing["result_sha256"] == result_hash:
                            report["duplicates"].append(identity)
                        else:
                            raise ContractError(
                                "assignment_id + attempt already exists with different content"
                            )
                    else:
                        if actor_role == "expert":
                            if assignment_id in pending_online_assignments:
                                raise ContractError(
                                    "online submission has multiple attempts for one assignment"
                                )
                            pending_online_assignments.add(assignment_id)
                        self._require_submission_assignment(
                            assignment, actor_id=actor_id, actor_role=actor_role
                        )
                        pending.append((result, result_hash))
                except ContractError as error:
                    report["rejected"].append(identity | {"reason": str(error)})

            if not pending:
                self.connection.commit()
                transaction_started = False
                report["submission_id"] = submission_id
                return finish("not_imported")

            self.connection.execute(
                """INSERT INTO bundle_submissions
                   (submission_id,bundle_id,payload_sha256,imported_at)
                   VALUES (?,?,?,?)""",
                (submission_id, bundle_id, expected_payload_hash, report["imported_at"]),
            )
            advanced_assignments: set[str] = set()
            for result, result_hash in pending:
                if result["assignment_id"] not in advanced_assignments:
                    self._advance_assignment_for_submission(
                        assignment_id=result["assignment_id"],
                        actor_id=actor_id,
                        actor_role=actor_role,
                        source=source,
                        submission_id=submission_id,
                        attempt=result["attempt"],
                        container_id=result["bundle_id"],
                        occurred_at=report["imported_at"],
                    )
                    advanced_assignments.add(result["assignment_id"])
                self.connection.execute(
                    """INSERT INTO bundle_results
                       (assignment_id,attempt,submission_id,bundle_id,expert_id,batch_id,
                        task_id,task_revision_sha256,result_sha256,payload_json,imported_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        result["assignment_id"],
                        result["attempt"],
                        submission_id,
                        result["bundle_id"],
                        result["expert_id"],
                        result["batch_id"],
                        result["task_id"],
                        result["task_revision_sha256"],
                        result_hash,
                        canonical_json(result),
                        report["imported_at"],
                    ),
                )
                if actor_role != "expert":
                    self._append_audit_event(
                        actor_id=actor_id,
                        actor_role=actor_role,
                        event_type="assignment.result_imported",
                        entity_type="assignment",
                        entity_id=result["assignment_id"],
                        batch_id=result["batch_id"],
                        assignment_id=result["assignment_id"],
                        payload={
                            "attempt": result["attempt"],
                            "container_id": result["bundle_id"],
                            "source": source,
                            "submission_id": submission_id,
                        },
                        occurred_at=report["imported_at"],
                    )
                report["accepted"].append(
                    {
                        "assignment_id": result["assignment_id"],
                        "attempt": result["attempt"],
                    }
                )
            self.connection.commit()
            transaction_started = False
            report["submission_id"] = submission_id
            return finish("imported")
        except ContractError as error:
            if transaction_started:
                self.connection.rollback()
            report["rejected"].append({"scope": "envelope", "reason": str(error)})
            return finish("not_imported")
        except Exception:
            if transaction_started:
                self.connection.rollback()
            raise

    def result_rows(self) -> list[dict[str, Any]]:
        """The export payload: exactly the fields validate_export_rows pins."""
        return [
            {key: value for key, value in row.items() if key != "trajectory_steps"}
            for row in self._result_rows_with_steps()
        ]

    def _result_rows_with_steps(self) -> list[dict[str, Any]]:
        """The export rows plus the key operations behind trajectory_step_count.

        The console renders these so an admin can read why each operation
        mattered; the export keeps its narrower contract.
        """
        rows: list[dict[str, Any]] = []
        query = """SELECT r.*,e.name expert_name
                   FROM results r JOIN experts e ON e.expert_id=r.expert_id
                   ORDER BY r.batch_id,r.expert_id,r.task_id,r.attempt"""
        for row in self.connection.execute(query):
            payload = json.loads(row["payload_json"])
            rows.append(
                {
                    "schema_version": RESULT_SCHEMA_VERSION,
                    "batch_id": row["batch_id"],
                    "package_id": row["package_id"],
                    "bundle_id": None,
                    "expert_id": row["expert_id"],
                    "expert_name": row["expert_name"],
                    "assignment_id": row["assignment_id"],
                    "task_id": row["task_id"],
                    "task_revision_sha256": row["task_revision_sha256"],
                    "attempt": row["attempt"],
                    "validation_status": payload["task_validation"]["status"],
                    "validation_feedback": payload["task_validation"]["feedback"],
                    "answer": payload["answer"],
                    "reasoning": payload["reasoning"],
                    "trajectory_status": payload["trajectory"]["status"],
                    "trajectory_step_count": _trajectory_step_count(payload["trajectory"]),
                    "trajectory_steps": _trajectory_steps(payload["trajectory"]),
                    "evidence_count": len(payload.get("evidence", [])),
                    "imported_at": row["imported_at"],
                }
            )
        bundle_query = """SELECT r.*,e.name expert_name
                          FROM bundle_results r JOIN experts e ON e.expert_id=r.expert_id
                          ORDER BY r.batch_id,r.expert_id,r.task_id,r.attempt"""
        for row in self.connection.execute(bundle_query):
            payload = json.loads(row["payload_json"])
            rows.append(
                {
                    "schema_version": BUNDLE_RESULT_SCHEMA_VERSION,
                    "batch_id": row["batch_id"],
                    "package_id": None,
                    "bundle_id": row["bundle_id"],
                    "expert_id": row["expert_id"],
                    "expert_name": row["expert_name"],
                    "assignment_id": row["assignment_id"],
                    "task_id": row["task_id"],
                    "task_revision_sha256": row["task_revision_sha256"],
                    "attempt": row["attempt"],
                    "validation_status": payload["task_validation"]["status"],
                    "validation_feedback": payload["task_validation"]["feedback"],
                    "answer": payload["answer"],
                    "reasoning": payload["reasoning"],
                    "trajectory_status": payload["trajectory"]["status"],
                    "trajectory_step_count": _trajectory_step_count(payload["trajectory"]),
                    "trajectory_steps": _trajectory_steps(payload["trajectory"]),
                    "evidence_count": len(payload.get("evidence", [])),
                    "imported_at": row["imported_at"],
                }
            )
        rows.sort(
            key=lambda row: (
                row["batch_id"],
                row["expert_id"],
                row["task_id"],
                row["attempt"],
            )
        )
        return rows

    def overview(self) -> dict[str, Any]:
        experts = [
            dict(row) | {"domains": json.loads(row["domains_json"])}
            for row in self.connection.execute("SELECT * FROM experts ORDER BY expert_id")
        ]
        for expert in experts:
            expert.pop("domains_json", None)
        batches = []
        for row in self.connection.execute(
            """SELECT b.*,ba.status admission_status,ba.purpose admission_purpose,
                      ba.receipt_digest admission_receipt_digest,
                      ba.policy_name admission_policy_name,
                      COUNT(DISTINCT t.task_id) task_count,
                      COUNT(DISTINCT a.assignment_id) assignment_count
               FROM batches b
               LEFT JOIN batch_admissions ba ON ba.batch_id=b.batch_id
               LEFT JOIN tasks t ON t.batch_id=b.batch_id
               LEFT JOIN assignments a ON a.batch_id=b.batch_id
               GROUP BY b.batch_id ORDER BY b.imported_at DESC"""
        ):
            batches.append(dict(row))
        assignments = [
            dict(row)
            for row in self.connection.execute(
                """SELECT a.*,t.domain,e.name expert_name
                   FROM assignments a JOIN tasks t
                     ON t.batch_id=a.batch_id AND t.task_id=a.task_id
                   JOIN experts e ON e.expert_id=a.expert_id
                   ORDER BY a.batch_id,a.expert_id,a.task_id"""
            )
        ]
        tasks = [
            dict(row)
            for row in self.connection.execute(
                """SELECT t.batch_id,t.task_id,t.domain,t.task_revision_sha256,
                          a.assignment_id,a.expert_id
                   FROM tasks t LEFT JOIN assignments a
                     ON a.batch_id=t.batch_id AND a.task_id=t.task_id
                   ORDER BY t.batch_id,t.domain,t.task_id"""
            )
        ]
        packages = [dict(row) for row in self.connection.execute(
            "SELECT * FROM packages ORDER BY created_at DESC"
        )]
        for package in packages:
            package["assignments"] = json.loads(package.pop("assignments_json"))
        bundles = []
        for row in self.connection.execute(
            """SELECT b.bundle_id,b.schema_version,b.batch_id,b.expert_id,
                      b.source_kind,b.created_at,
                      b.manifest_json,
                      CASE WHEN a.bundle_id IS NULL THEN 0 ELSE 1 END active,
                      a.activated_at
               FROM task_bundles b
               LEFT JOIN active_task_bundles a ON a.bundle_id=b.bundle_id
               ORDER BY b.created_at DESC,b.bundle_id"""
        ):
            item = dict(row)
            manifest = json.loads(item.pop("manifest_json"))
            item["task_count"] = manifest["task_file"]["count"]
            item["admission_status"] = (
                manifest.get("admission", {}).get("status")
                or manifest.get("admission_status")
            )
            item["active"] = bool(item["active"])
            bundles.append(item)
        active_bundles = [
            dict(row)
            for row in self.connection.execute(
                """SELECT a.expert_id,a.bundle_id,b.batch_id,b.source_kind,a.activated_at
                   FROM active_task_bundles a
                   JOIN task_bundles b ON b.bundle_id=a.bundle_id
                   ORDER BY a.expert_id"""
            )
        ]
        return {
            "contract_version": CONTRACT_VERSION,
            "bundle_contract_version": BUNDLE_CONTRACT_VERSION,
            "experts": experts,
            "batches": batches,
            "tasks": tasks,
            "assignments": assignments,
            "packages": packages,
            "bundles": bundles,
            "active_bundles": active_bundles,
            "results": self._result_rows_with_steps(),
        }
