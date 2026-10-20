"""Append-only artifact store for expert browser-MCP trajectory submissions.

The review answer flow (``/api/expert/submit`` -> ``results``) records what an
expert concluded. A trajectory records *how* they got there: the ordered viewer
actions plus a screenshot per committed step, as produced by the browser
trajectory drawer. The two are independent on purpose — a trajectory is
evidence, not a grade, so importing one never touches ``results``.

Payloads carry base64 screenshots and routinely reach several megabytes, so the
document itself is written to a file and only its metadata and digest land in
SQLite.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .contract import (
    ContractError,
    TRAJECTORY_SCHEMA_VERSION,
    canonical_json,
    require_identifier,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectory_submissions (
  idempotency_key TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL UNIQUE,
  expert_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  qualified_task_id TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  step_count INTEGER NOT NULL,
  artifact_path TEXT NOT NULL,
  received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trajectory_submissions_task
  ON trajectory_submissions(expert_id, task_id);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"trajectory {field} must be non-empty text")
    return value.strip()


def _agreeing_field(
    payload: dict[str, Any], identity: dict[str, Any], field: str
) -> str:
    """Read a field the drawer sends twice, and refuse to guess when they differ."""

    from_identity = identity.get(field)
    from_payload = payload.get(field)
    if from_identity is not None and from_payload is not None:
        if _require_text(from_identity, field) != _require_text(from_payload, field):
            raise ContractError(
                f"trajectory {field} disagrees between identity and payload"
            )
    return _require_text(
        from_identity if from_identity is not None else from_payload, field
    )


def _step_count(payload: dict[str, Any]) -> int:
    trajectory = payload.get("trajectory")
    if trajectory is None:
        return 0
    if not isinstance(trajectory, list):
        raise ContractError("trajectory must be a list of steps")
    if any(not isinstance(step, dict) for step in trajectory):
        raise ContractError("every trajectory step must be an object")
    return len(trajectory)


def _artifact_relative_path(bundle_id: str, submission_id: str) -> str:
    return f"{bundle_id}/{submission_id}.json"


def _write_artifact(root: Path, relative: str, body: bytes) -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def import_trajectory(
    connection: sqlite3.Connection,
    *,
    expert_id: str,
    bundle_id: str,
    bundle_task_ids: Iterable[str],
    payload: Any,
    identity: Any,
    artifact_root: Path,
    received_at: str,
) -> dict[str, Any]:
    """Persist one trajectory document, idempotently by its idempotency key."""

    expert_id = require_identifier(expert_id, "expert_id")
    if not isinstance(payload, dict):
        raise ContractError("trajectory payload must be an object")
    if not isinstance(identity, dict):
        raise ContractError("trajectory identity must be an object")
    schema_version = _require_text(payload.get("schema_version"), "schema_version")
    if schema_version != TRAJECTORY_SCHEMA_VERSION:
        raise ContractError(
            f"trajectory schema_version must be {TRAJECTORY_SCHEMA_VERSION}"
        )
    idempotency_key = _agreeing_field(payload, identity, "idempotency_key")
    submission_id = _agreeing_field(payload, identity, "submission_id")
    task_id = _require_text(payload.get("task_id"), "task_id")
    if task_id not in set(bundle_task_ids):
        raise ContractError(f"task {task_id!r} is not in the task bundle for this expert")
    qualified_task_id = _require_text(
        payload.get("qualified_task_id") or task_id, "qualified_task_id"
    )
    # Counted here, with the rest of the validation, so a malformed step list is
    # rejected before any artifact is written.
    step_count = _step_count(payload)

    body = canonical_json(payload).encode("utf-8")
    payload_sha256 = hashlib.sha256(body).hexdigest()
    relative = _artifact_relative_path(bundle_id, submission_id)

    existing = connection.execute(
        "SELECT * FROM trajectory_submissions WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if existing is not None:
        # Authorization first, so a caller can never compare their body against
        # another expert's stored digest.
        if existing["expert_id"] != expert_id:
            raise ContractError("trajectory idempotency key belongs to another expert")
        # A retried submission is the same submission. A *different* body under a
        # reused key is a client bug, and silently keeping either version would
        # lose evidence, so refuse it.
        if existing["payload_sha256"] != payload_sha256:
            raise ContractError(
                "trajectory idempotency key was reused for a different payload"
            )
        return _acknowledgment(existing, replayed=True)

    # Checked before the artifact is written so a rejected submission never
    # leaves an orphan file behind.
    if connection.execute(
        "SELECT 1 FROM trajectory_submissions WHERE submission_id=?",
        (submission_id,),
    ).fetchone() is not None:
        raise ContractError(
            "trajectory submission_id was already recorded under a different"
            " idempotency key"
        )

    _write_artifact(artifact_root, relative, body)
    record = {
        "idempotency_key": idempotency_key,
        "submission_id": submission_id,
        "expert_id": expert_id,
        "bundle_id": bundle_id,
        "task_id": task_id,
        "qualified_task_id": qualified_task_id,
        "schema_version": schema_version,
        "payload_sha256": payload_sha256,
        "byte_size": len(body),
        "step_count": step_count,
        "artifact_path": relative,
        "received_at": received_at,
    }
    with connection:
        connection.execute(
            """INSERT INTO trajectory_submissions
                 (idempotency_key, submission_id, expert_id, bundle_id, task_id,
                  qualified_task_id, schema_version, payload_sha256, byte_size,
                  step_count, artifact_path, received_at)
               VALUES (:idempotency_key, :submission_id, :expert_id, :bundle_id,
                       :task_id, :qualified_task_id, :schema_version,
                       :payload_sha256, :byte_size, :step_count, :artifact_path,
                       :received_at)""",
            record,
        )
    return _acknowledgment(record, replayed=False)


def _acknowledgment(record: Any, *, replayed: bool) -> dict[str, Any]:
    return {
        "acknowledged": True,
        "replayed": replayed,
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "submission_id": record["submission_id"],
        "idempotency_key": record["idempotency_key"],
        "task_id": record["task_id"],
        "payload_sha256": record["payload_sha256"],
        "byte_size": record["byte_size"],
        "step_count": record["step_count"],
        "received_at": record["received_at"],
    }


def trajectory_task_ids(tasks_jsonl: str) -> list[str]:
    """Task IDs an expert may submit a trajectory for, read from their bundle."""

    task_ids: list[str] = []
    for line in tasks_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        task_id = row.get("task_id")
        if isinstance(task_id, str) and task_id:
            task_ids.append(task_id)
    return task_ids
