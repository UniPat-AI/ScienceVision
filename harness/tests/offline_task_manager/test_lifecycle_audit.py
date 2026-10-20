from __future__ import annotations

import csv
import http.cookiejar
import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import pytest

from tools.offline_task_manager.contract import (
    ContractError,
    object_sha256,
    submission_payload_sha256,
)
from tools.offline_task_manager.online_auth import create_credentials, load_credentials
from tools.offline_task_manager.online_server import Handler, OnlineServer
from tools.offline_task_manager.store import ManagerStore


def _bundle_submission(context: dict, task: dict, *, attempt: int = 1) -> dict:
    result = {
        "schema_version": "sciencevision.expert_bundle_result.v2",
        "bundle_id": context["bundle_id"],
        "batch_id": task["batch_id"],
        "expert_id": task["expert_id"],
        "assignment_id": task["assignment_id"],
        "task_id": task["task_id"],
        "task_revision_sha256": task["task_revision_sha256"],
        "attempt": attempt,
        "task_validation": {"status": "valid", "feedback": "Checked online."},
        "answer": "Online answer",
        "reasoning": "Inspected the assigned model.",
        "trajectory": {
            "schema_version": "sciencevision.trajectory_ref.v1",
            "status": "not_collected",
            "reason": "collector_not_implemented",
        },
        "evidence": [],
        "saved_at": "2026-08-18T00:00:00Z",
    }
    body = {
        "schema_version": "sciencevision.expert_bundle_submission.v2",
        "contract_version": "sciencevision.expert_exchange.v2",
        "submission_id": "sub_" + object_sha256(result)[:24],
        "bundle_id": context["bundle_id"],
        "bundle_manifest_sha256": context["manifest"]["bundle_manifest_sha256"],
        "batch_id": task["batch_id"],
        "expert_id": task["expert_id"],
        "results": [result],
    }
    return body | {"payload_sha256": submission_payload_sha256(body)}


def _request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    body: dict | None = None,
    csrf: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict]:
    headers: dict[str, str] = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = origin or f"{parsed.scheme}://{parsed.netloc}"
    if csrf is not None:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def _login(opener: urllib.request.OpenerDirector, base: str, username: str, password: str) -> dict:
    status, value = _request_json(
        opener,
        base + "/api/login",
        body={"username": username, "password": password},
    )
    assert status == 200, value
    status, session = _request_json(opener, base + "/api/session")
    assert status == 200
    if not session["password_change_required"]:
        return session
    # An invited expert account starts on a temporary password and may only
    # activate itself, so lifecycle tests activate before exercising the APIs.
    activated = f"{username}-activated-password"
    status, value = _request_json(
        opener,
        base + "/api/password-change",
        body={"new_password": activated, "confirm_password": activated},
        csrf=session["csrf"],
    )
    assert status == 200, value
    status, session = _request_json(opener, base + "/api/session")
    assert status == 200
    assert session["password_change_required"] is False
    return session


def _write_v1_lifecycle_database(db_path: Path) -> tuple[str, str]:
    assignment_ids = ("asgn_" + "1" * 24, "asgn_" + "2" * 24)
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE assignments (
          assignment_id TEXT PRIMARY KEY,
          schema_version TEXT NOT NULL,
          batch_id TEXT NOT NULL,
          task_id TEXT NOT NULL,
          task_revision_sha256 TEXT NOT NULL,
          expert_id TEXT NOT NULL,
          assigned_at TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'assigned',
          UNIQUE (batch_id, task_id)
        );
        CREATE TABLE results (
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
          PRIMARY KEY (assignment_id, attempt)
        );
        CREATE TABLE bundle_results (
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
          PRIMARY KEY (assignment_id, attempt)
        );
        CREATE TABLE audit_events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          occurred_at TEXT NOT NULL,
          actor_id TEXT NOT NULL,
          actor_role TEXT NOT NULL,
          event_type TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          batch_id TEXT,
          assignment_id TEXT,
          payload_json TEXT NOT NULL
        );
        CREATE TABLE schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES (1, '2026-08-17T00:00:00Z');
        """
    )
    for index, assignment_id in enumerate(assignment_ids, start=1):
        connection.execute(
            """INSERT INTO assignments VALUES
               (?, 'sciencevision.assignment.v1', 'batch-old', ?, ?, 'expert-old',
                '2026-08-01T00:00:00Z', 'assigned')""",
            (assignment_id, f"task-{index}", str(index) * 64),
        )
    connection.execute(
        """INSERT INTO results VALUES
           (?,1,?,'pkg_aaaaaaaaaaaaaaaaaaaaaaaa','expert-old','batch-old','task-1',
            ?,?,'{}','2026-08-02T00:00:00Z')""",
        (assignment_ids[0], "sub_" + "a" * 24, "1" * 64, "a" * 64),
    )
    connection.execute(
        """INSERT INTO bundle_results VALUES
           (?,1,?,'bndl_bbbbbbbbbbbbbbbbbbbbbbbb','expert-old','batch-old','task-2',
            ?,?,'{}','2026-08-02T00:00:00Z')""",
        (assignment_ids[1], "sub_" + "b" * 24, "2" * 64, "b" * 64),
    )
    connection.commit()
    connection.close()
    return assignment_ids


def test_lifecycle_migration_opens_database_without_assignment_status(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """CREATE TABLE assignments (
             assignment_id TEXT PRIMARY KEY,
             schema_version TEXT NOT NULL,
             batch_id TEXT NOT NULL,
             task_id TEXT NOT NULL,
             task_revision_sha256 TEXT NOT NULL,
             expert_id TEXT NOT NULL,
             assigned_at TEXT NOT NULL,
             UNIQUE (batch_id, task_id)
           )"""
    )
    connection.execute(
        """INSERT INTO assignments
           (assignment_id,schema_version,batch_id,task_id,task_revision_sha256,
            expert_id,assigned_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            "asgn_" + "1" * 24,
            "sciencevision.assignment.v1",
            "batch-old",
            "task-old",
            "2" * 64,
            "expert-old",
            "2026-08-01T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    with ManagerStore(db_path) as store:
        columns = {
            row["name"] for row in store.connection.execute("PRAGMA table_info(assignments)")
        }
        assignment = store.connection.execute("SELECT * FROM assignments").fetchone()
        migrations = store.connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert "status" in columns
        assert assignment["status"] == "assigned"
        assert [row["version"] for row in migrations] == [1, 2, 3]
        assert store.audit_events()["events"] == []


def test_v2_migration_backfills_legacy_result_assignments(tmp_path: Path) -> None:
    db_path = tmp_path / "v1-results.db"
    assignment_ids = _write_v1_lifecycle_database(db_path)

    with ManagerStore(db_path) as store:
        rows = store.connection.execute(
            "SELECT assignment_id,status FROM assignments ORDER BY assignment_id"
        ).fetchall()
        assert [(row["assignment_id"], row["status"]) for row in rows] == [
            (assignment_ids[0], "pending_review"),
            (assignment_ids[1], "pending_review"),
        ]
        assert [
            row["version"]
            for row in store.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ] == [1, 2, 3]


def test_migration_check_and_apply_are_concurrency_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.db"
    with ManagerStore(db_path):
        pass
    connection = sqlite3.connect(db_path)
    connection.execute("DELETE FROM schema_migrations WHERE version=2")
    connection.commit()
    connection.close()

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def open_store() -> None:
        try:
            barrier.wait(timeout=5)
            with ManagerStore(db_path):
                pass
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [threading.Thread(target=open_store) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    connection = sqlite3.connect(db_path)
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version=2"
    ).fetchone()[0] == 1
    connection.close()


def test_marked_migration_rejects_unhealthy_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "unhealthy.db"
    with ManagerStore(db_path):
        pass
    connection = sqlite3.connect(db_path)
    connection.execute("DROP TRIGGER audit_events_no_replace")
    connection.commit()
    connection.close()

    with pytest.raises(ContractError, match="triggers are unhealthy"):
        ManagerStore(db_path)


def test_assignment_state_machine_and_append_only_audit(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    assignment = store.assign_explicit(batch_id="batch-r1", task_id="task-1", expert_id="expert-a")
    assignment_id = assignment["assignment_id"]

    with pytest.raises(ContractError, match="illegal assignment transition"):
        store.transition_assignment_status(
            assignment_id,
            "approved",
            actor_id="admin",
            actor_role="admin",
        )
    assert (
        store.connection.execute(
            "SELECT status FROM assignments WHERE assignment_id=?", (assignment_id,)
        ).fetchone()["status"]
        == "assigned"
    )

    updated = store.transition_assignment_status(
        assignment_id,
        "in_progress",
        actor_id="expert-a",
        actor_role="expert",
        payload={"from_status": "approved", "to_status": "approved"},
    )
    assert updated["status"] == "in_progress"
    first_transition = store.audit_events(assignment_id=assignment_id, limit=1)["events"][0]
    assert first_transition["payload"]["from_status"] == "assigned"
    assert first_transition["payload"]["to_status"] == "in_progress"

    transitions = [
        ("submitted", "expert-a", "expert"),
        ("pending_review", "task-manager", "system"),
        ("returned", "admin", "admin"),
        ("in_progress", "expert-a", "expert"),
    ]
    for status, actor_id, role in transitions:
        updated = store.transition_assignment_status(
            assignment_id, status, actor_id=actor_id, actor_role=role
        )
        assert updated["status"] == status

    page = store.audit_events(assignment_id=assignment_id, limit=3, offset=1)
    assert page["page"] == {"limit": 3, "offset": 1, "total": 6}
    assert [event["event_id"] for event in page["events"]] == sorted(
        [event["event_id"] for event in page["events"]], reverse=True
    )
    assert all(event["assignment_id"] == assignment_id for event in page["events"])
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute(
            "UPDATE audit_events SET event_type='changed' WHERE assignment_id=?",
            (assignment_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("DELETE FROM audit_events WHERE assignment_id=?", (assignment_id,))
    with pytest.raises(ContractError, match="sensitive"):
        store.record_audit_event(
            actor_id="admin",
            actor_role="admin",
            event_type="test.sensitive",
            entity_type="assignment",
            entity_id=assignment_id,
            assignment_id=assignment_id,
            payload={"session_token": "must-not-persist"},
        )
    with pytest.raises(ContractError, match="sensitive"):
        store.record_audit_event(
            actor_id="admin",
            actor_role="admin",
            event_type="test.sensitive",
            entity_type="assignment",
            entity_id=assignment_id,
            assignment_id=assignment_id,
            payload={"sessionToken": "camel-case-must-not-persist"},
        )
    with pytest.raises(ContractError, match="unexpected credential_blob"):
        store.record_audit_event(
            actor_id="admin",
            actor_role="admin",
            event_type="assignment.status_changed",
            entity_type="assignment",
            entity_id=assignment_id,
            assignment_id=assignment_id,
            payload={
                "from_status": "returned",
                "to_status": "in_progress",
                "credential_blob": "must-not-persist",
            },
        )


def test_replace_and_null_cannot_bypass_lifecycle_guards(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    assignment = store.assign_explicit(
        batch_id="batch-r1", task_id="task-1", expert_id="expert-a"
    )
    row = store.connection.execute(
        "SELECT * FROM assignments WHERE assignment_id=?", (assignment["assignment_id"],)
    ).fetchone()
    event = store.audit_events(assignment_id=assignment["assignment_id"], limit=1)["events"][0]

    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        store.connection.execute(
            """INSERT OR REPLACE INTO assignments
               (assignment_id,schema_version,batch_id,task_id,task_revision_sha256,
                expert_id,assigned_at,status)
               VALUES (?,?,?,?,?,?,?,?)""",
            tuple(row),
        )
    conflicting_row = list(row)
    conflicting_row[0] = "asgn_" + "f" * 24
    with pytest.raises(sqlite3.IntegrityError, match="cannot be replaced"):
        store.connection.execute(
            """INSERT OR REPLACE INTO assignments
               (assignment_id,schema_version,batch_id,task_id,task_revision_sha256,
                expert_id,assigned_at,status)
               VALUES (?,?,?,?,?,?,?,?)""",
            conflicting_row,
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute(
            """INSERT OR REPLACE INTO audit_events
               (event_id,occurred_at,actor_id,actor_role,event_type,entity_type,
                entity_id,batch_id,assignment_id,payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                event["event_id"],
                event["occurred_at"],
                event["actor_id"],
                event["actor_role"],
                event["event_type"],
                event["entity_type"],
                event["entity_id"],
                event["batch_id"],
                event["assignment_id"],
                json.dumps(event["payload"]),
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="invalid assignment status|NOT NULL"):
        store.connection.execute(
            "UPDATE assignments SET status=NULL WHERE assignment_id=?",
            (assignment["assignment_id"],),
        )
    assert store.connection.execute(
        "SELECT status FROM assignments WHERE assignment_id=?", (assignment["assignment_id"],)
    ).fetchone()["status"] == "assigned"


def test_open_assignment_requires_active_bundle_and_is_idempotent(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    context = store.active_bundle_context("expert-a")
    assignment_id = context["manifest"]["assignments"][0]["assignment_id"]

    opened = store.start_online_assignment(expert_id="expert-a", assignment_id=assignment_id)
    repeated = store.start_online_assignment(expert_id="expert-a", assignment_id=assignment_id)
    assert opened["status"] == "in_progress" and not opened["unchanged"]
    assert repeated["status"] == "in_progress" and repeated["unchanged"]
    events = store.audit_events(assignment_id=assignment_id, event_type="assignment.opened")[
        "events"
    ]
    assert len(events) == 1
    with pytest.raises(ContractError, match="not assigned to this expert"):
        store.start_online_assignment(expert_id="expert-b", assignment_id=assignment_id)


def test_offline_import_advances_lifecycle_with_system_actor(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    context = store.active_bundle_context("expert-a")
    task = json.loads(context["tasks_jsonl"].splitlines()[0])

    report = store.import_submission(_bundle_submission(context, task))
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    assert store.connection.execute(
        "SELECT status FROM assignments WHERE assignment_id=?", (task["assignment_id"],)
    ).fetchone()["status"] == "pending_review"
    events = list(reversed(store.audit_events(assignment_id=task["assignment_id"])["events"]))
    imported = [event for event in events if event["event_type"] == "assignment.result_imported"]
    assert len(imported) == 1
    assert imported[0]["actor_id"] == "offline-import"
    assert imported[0]["actor_role"] == "system"
    assert imported[0]["payload"]["source"] == "offline_import"
    lifecycle = [
        event
        for event in events
        if event["event_type"]
        in {"assignment.opened", "assignment.submitted", "assignment.pending_review"}
    ]
    assert [event["event_type"] for event in lifecycle] == [
        "assignment.opened",
        "assignment.submitted",
        "assignment.pending_review",
    ]
    assert all(event["payload"]["source"] == "offline_import" for event in lifecycle)

    retry = store.import_submission(_bundle_submission(context, task, attempt=2))
    assert retry["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    assert store.connection.execute(
        "SELECT status FROM assignments WHERE assignment_id=?", (task["assignment_id"],)
    ).fetchone()["status"] == "pending_review"


def test_online_submit_updates_result_status_and_audit_atomically(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    context = store.active_bundle_context("expert-a")
    task = json.loads(context["tasks_jsonl"].splitlines()[0])
    submission = _bundle_submission(context, task)
    assignment_id = task["assignment_id"]

    original_append = store._append_audit_event

    def fail_pending_review(**kwargs):
        if kwargs["event_type"] == "assignment.pending_review":
            raise RuntimeError("injected audit failure")
        return original_append(**kwargs)

    monkeypatch.setattr(store, "_append_audit_event", fail_pending_review)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        store.import_submission(submission, actor_id="expert-a", actor_role="expert")
    assert (
        store.connection.execute(
            "SELECT status FROM assignments WHERE assignment_id=?", (assignment_id,)
        ).fetchone()["status"]
        == "assigned"
    )
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM bundle_results WHERE assignment_id=?", (assignment_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        store.audit_events(assignment_id=assignment_id, event_type="assignment.submitted")["events"]
        == []
    )

    monkeypatch.setattr(store, "_append_audit_event", original_append)
    accepted = store.import_submission(submission, actor_id="expert-a", actor_role="expert")
    assert accepted["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    assert (
        store.connection.execute(
            "SELECT status FROM assignments WHERE assignment_id=?", (assignment_id,)
        ).fetchone()["status"]
        == "pending_review"
    )
    lifecycle = [
        event["event_type"]
        for event in reversed(store.audit_events(assignment_id=assignment_id)["events"])
    ]
    assert lifecycle == [
        "assignment.assigned",
        "assignment.opened",
        "assignment.submitted",
        "assignment.pending_review",
    ]

    before_count = store.audit_events(assignment_id=assignment_id)["page"]["total"]
    duplicate = store.import_submission(submission, actor_id="expert-a", actor_role="expert")
    assert duplicate["submission_status"] == "unchanged"
    assert store.audit_events(assignment_id=assignment_id)["page"]["total"] == before_count

    second_attempt = _bundle_submission(context, task, attempt=2)
    rejected = store.import_submission(second_attempt, actor_id="expert-a", actor_role="expert")
    assert rejected["counts"] == {"accepted": 0, "duplicates": 0, "rejected": 1}
    assert "does not allow submission" in rejected["rejected"][0]["reason"]
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM bundle_results WHERE assignment_id=?", (assignment_id,)
        ).fetchone()[0]
        == 1
    )


def test_audit_and_assignment_open_api_permission_boundaries(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    context = store.active_bundle_context("expert-a")
    assignment_id = context["manifest"]["assignments"][0]["assignment_id"]
    auth_root = imported_workflow["tmp"] / "lifecycle-auth"
    credentials = auth_root / "credentials.json"
    plaintext = auth_root / "handoff.csv"
    secret = auth_root / "session.key"
    create_credentials(
        store,
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    passwords = {
        row["username"]: row["password"]
        for row in csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines())
    }
    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = store
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    server.credentials_path = credentials
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/sv-annotate"
    try:
        anonymous = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        status, _ = _request_json(anonymous, base + "/api/admin/audit-events")
        assert status == 401

        expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        expert_session = _login(expert, base, "expert-a", passwords["expert-a"])
        status, _ = _request_json(expert, base + "/api/admin/audit-events")
        assert status == 403
        status, value = _request_json(
            expert,
            base + "/api/expert/assignments/open",
            body={"assignment_id": assignment_id},
        )
        assert status == 403 and "CSRF" in value["error"]
        status, value = _request_json(
            expert,
            base + "/api/expert/assignments/open",
            body={"assignment_id": assignment_id},
            csrf=expert_session["csrf"],
            origin=f"http://127.0.0.1:{server.server_port + 1}",
        )
        assert status == 403 and "cross-origin" in value["error"]
        status, value = _request_json(
            expert,
            base + "/api/expert/assignments/open",
            body={"assignment_id": assignment_id},
            csrf=expert_session["csrf"],
        )
        assert status == 200 and value["status"] == "in_progress"
        assert value["unchanged"] is False
        status, repeated = _request_json(
            expert,
            base + "/api/expert/assignments/open",
            body={"assignment_id": assignment_id},
            csrf=expert_session["csrf"],
        )
        assert status == 200 and repeated["unchanged"] is True

        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = _login(admin, base, "admin", passwords["admin"])
        status, page = _request_json(
            admin,
            base
            + "/api/admin/audit-events?limit=1&assignment_id="
            + urllib.parse.quote(assignment_id)
            + "&event_type=assignment.opened",
        )
        assert status == 200
        assert page["page"] == {"limit": 1, "offset": 0, "total": 1}
        assert page["events"][0]["actor_id"] == "expert-a"
        status, _ = _request_json(
            admin,
            base + "/api/expert/assignments/open",
            body={"assignment_id": assignment_id},
            csrf=admin_session["csrf"],
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_admin_review_api_closes_lifecycle_and_records_session_actor(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    assignments = store.overview()["assignments"][:2]
    for assignment in assignments:
        for status in ("in_progress", "submitted", "pending_review"):
            store.transition_assignment_status(
                assignment["assignment_id"],
                status,
                actor_id="task-manager",
                actor_role="system",
            )
    auth_root = imported_workflow["tmp"] / "review-auth"
    credentials = auth_root / "credentials.json"
    plaintext = auth_root / "handoff.csv"
    secret = auth_root / "session.key"
    create_credentials(
        store,
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    passwords = {
        row["username"]: row["password"]
        for row in csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines())
    }
    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = store
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    server.credentials_path = credentials
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/sv-annotate"
    review_url = base + "/api/admin/assignments/review"
    try:
        expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        expert_session = _login(expert, base, "expert-a", passwords["expert-a"])
        status, _ = _request_json(
            expert,
            review_url,
            body={"assignment_id": assignments[0]["assignment_id"], "status": "approved"},
            csrf=expert_session["csrf"],
        )
        assert status == 403

        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = _login(admin, base, "admin", passwords["admin"])
        status, value = _request_json(
            admin,
            review_url,
            body={"assignment_id": assignments[0]["assignment_id"], "status": "returned"},
        )
        assert status == 403 and "CSRF" in value["error"]
        status, value = _request_json(
            admin,
            review_url,
            body={"assignment_id": assignments[0]["assignment_id"], "status": "returned"},
            csrf=admin_session["csrf"],
            origin=f"http://127.0.0.1:{server.server_port + 1}",
        )
        assert status == 403 and "cross-origin" in value["error"]
        status, value = _request_json(
            admin,
            review_url,
            body={"assignment_id": assignments[0]["assignment_id"], "status": "returned"},
            csrf=admin_session["csrf"],
        )
        assert status == 400 and "requires a reason" in value["error"]
        status, returned = _request_json(
            admin,
            review_url,
            body={
                "assignment_id": assignments[0]["assignment_id"],
                "status": "returned",
                "reason": "Answer does not match the visual evidence.",
            },
            csrf=admin_session["csrf"],
        )
        assert status == 200 and returned["status"] == "returned"
        status, approved = _request_json(
            admin,
            review_url,
            body={"assignment_id": assignments[1]["assignment_id"], "status": "approved"},
            csrf=admin_session["csrf"],
        )
        assert status == 200 and approved["status"] == "approved"

        status, overview = _request_json(admin, base + "/api/admin/overview")
        assert status == 200
        statuses = {row["assignment_id"]: row["status"] for row in overview["assignments"]}
        assert statuses[assignments[0]["assignment_id"]] == "returned"
        assert statuses[assignments[1]["assignment_id"]] == "approved"
        for assignment, event_type in zip(
            assignments, ("assignment.returned", "assignment.approved"), strict=True
        ):
            event = store.audit_events(
                assignment_id=assignment["assignment_id"], event_type=event_type
            )["events"][0]
            assert event["actor_id"] == "admin"
            assert event["actor_role"] == "admin"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
