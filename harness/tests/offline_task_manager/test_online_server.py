from __future__ import annotations

import csv
import http.cookiejar
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque

import pytest

from tools.offline_task_manager.contract import object_sha256, submission_payload_sha256
from tools.offline_task_manager import online_auth as online_auth_module
from tools.offline_task_manager import online_server as online_server_module
from tools.offline_task_manager.online_auth import (
    create_credentials,
    load_credentials,
    verify_password,
)
from tools.offline_task_manager.online_server import Handler, OnlineServer


def _origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    body: dict | None = None,
    csrf: str | None = None,
    origin: str | bool | None = None,
) -> tuple[int, dict]:
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        # The gateway rejects state-changing requests without a trusted Origin.
        # Pass origin=False or "" to omit the header, or a string to forge one.
        if origin is None:
            headers["Origin"] = _origin_of(url)
        elif origin:
            headers["Origin"] = origin
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def login(opener: urllib.request.OpenerDirector, base: str, username: str, password: str) -> dict:
    status, value = request_json(
        opener,
        base + "/api/login",
        body={"username": username, "password": password},
    )
    assert status == 200, value
    status, session = request_json(opener, base + "/api/session")
    assert status == 200
    return session


def activate(
    opener: urllib.request.OpenerDirector, base: str, session: dict, password: str
) -> None:
    """Replace an invited expert's temporary password and retire the session."""
    status, value = request_json(
        opener,
        base + "/api/password-change",
        body={"new_password": password, "confirm_password": password},
        csrf=session["csrf"],
    )
    assert status == 200, value
    # Activation rotates the cookie, so the caller must not keep the resulting
    # session around: tests that count live sessions expect a clean slate.
    status, rotated = request_json(opener, base + "/api/session")
    assert status == 200
    assert rotated["password_change_required"] is False
    status, value = request_json(
        opener, base + "/api/logout", body={}, csrf=rotated["csrf"]
    )
    assert status == 200, value


def test_online_gateway_isolates_roles_tasks_and_assets(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    molstar_path = imported_workflow["viewer"] / "molstar.html"
    molstar_path.write_text(
        molstar_path.read_text(encoding="utf-8")
        + '<script src="/viewer/shared/vendor/example/build/viewer/runtime.js"></script>\n',
        encoding="utf-8",
    )
    assert store.connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0] == 0
    auth_root = imported_workflow["tmp"] / "auth"
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
    server.review_servers = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/sv-annotate"
    try:
        anonymous = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        with anonymous.open(base + "/login/", timeout=5) as response:
            login_page = response.read().decode("utf-8")
        # The login page is Chinese-first; the English wording lives in the
        # language table, so assert on the translation key instead.
        assert 'data-i18n="login.facet.viewer.desc"' in login_page
        assert "Mol* and NiiVue" not in login_page
        status, value = request_json(anonymous, base + "/api/session")
        assert status == 401 and not value["authenticated"]
        status, value = request_json(
            anonymous,
            base + "/api/login",
            body={"username": "admin", "password": passwords["admin"]},
            origin="",
        )
        assert status == 403 and "cross-origin" in value["error"]

        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = login(admin, base, "admin", passwords["admin"])
        assert admin_session["role"] == "admin"
        # A valid CSRF token is not enough: the Origin must be present and trusted.
        for forged in (False, "https://attacker.example", "http://127.0.0.1:1"):
            status, rejected = request_json(
                admin,
                base + "/api/admin/bundles/publish",
                body={"batch_id": "batch-r1"},
                csrf=admin_session["csrf"],
                origin=forged,
            )
            assert status == 403, (forged, rejected)
            assert rejected["error"] == "cross-origin request rejected"
        status, publish = request_json(
            admin,
            base + "/api/admin/bundles/publish",
            body={"batch_id": "batch-r1"},
            csrf=admin_session["csrf"],
        )
        assert status == 200 and publish["published"] == 2
        status, value = request_json(
            admin,
            base + "/api/admin/bundles/publish",
            body={"batch_id": "batch-r1"},
            csrf=admin_session["csrf"],
            origin=f"http://127.0.0.1:{server.server_port + 1}",
        )
        assert status == 403 and "cross-origin" in value["error"]
        status, overview = request_json(admin, base + "/api/admin/overview")
        assert status == 200 and len(overview["assignments"]) == 4
        assert len(overview["active_bundles"]) == 2
        status, _ = request_json(admin, base + "/api/expert/tasks")
        assert status == 403

        expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        expert_session = login(expert, base, "expert-a", passwords["expert-a"])
        assert expert_session["password_change_required"] is True
        status, changed = request_json(
            expert,
            base + "/api/password-change",
            body={
                "new_password": "expert-a-private-password",
                "confirm_password": "expert-a-private-password",
            },
            csrf=expert_session["csrf"],
        )
        assert status == 200 and changed["password_change_required"] is False
        status, expert_session = request_json(expert, base + "/api/session")
        assert status == 200
        status, bootstrap = request_json(expert, base + "/api/expert/bootstrap")
        assert status == 200
        assert bootstrap["binding"]["tasks_url"] == base.removeprefix(
            f"http://127.0.0.1:{server.server_port}"
        ) + "/api/expert/tasks"
        assert bootstrap["binding"]["viewer_base"] == "/sv-annotate/viewer/"
        assert bootstrap["binding"]["runtime_manifest"]["runtime_id"].startswith("vrt_")
        status, manifest = request_json(expert, base + "/api/expert/manifest")
        assert status == 200 and manifest["expert_id"] == "expert-a"
        assert manifest["bundle_id"].startswith("bndl_")
        with expert.open(base + "/api/expert/tasks", timeout=5) as response:
            tasks = [json.loads(line) for line in response.read().decode().splitlines()]
        assert tasks and {row["expert_id"] for row in tasks} == {"expert-a"}
        assert "GROUND_TRUTH_SECRET" not in json.dumps(tasks)
        with expert.open(base + "/expert/", timeout=5) as response:
            assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"]
        with expert.open(base + "/viewer/molstar.html", timeout=5) as response:
            assert "'unsafe-inline'" in response.headers["Content-Security-Policy"]
            page = response.read()
            assert b"/sv-annotate/viewer/shared/screenshot-api.js" in page
            assert (
                b"/sv-annotate/viewer/shared/vendor/example/build/viewer/runtime.js"
                in page
            )
            assert b"build/sv-annotate/viewer" not in page
        with expert.open(base + "/content/assets/model.cif", timeout=5) as response:
            assert response.read() == imported_workflow["asset"].read_bytes()

        task = tasks[0]
        result = {
            "schema_version": "sciencevision.expert_bundle_result.v2",
            "bundle_id": manifest["bundle_id"],
            "batch_id": task["batch_id"],
            "expert_id": task["expert_id"],
            "assignment_id": task["assignment_id"],
            "task_id": task["task_id"],
            "task_revision_sha256": task["task_revision_sha256"],
            "attempt": 1,
            "task_validation": {"status": "valid", "feedback": "Reviewed online."},
            "answer": "Online answer",
            "reasoning": "Inspected the assigned model.",
            "trajectory": {
                "schema_version": "sciencevision.trajectory_ref.v1",
                "status": "not_collected",
                "reason": "collector_not_implemented",
            },
            "evidence": [],
            "saved_at": "2026-08-15T00:00:00Z",
        }
        submission_body = {
            "schema_version": "sciencevision.expert_bundle_submission.v2",
            "contract_version": "sciencevision.expert_exchange.v2",
            "submission_id": "sub_" + object_sha256(result)[:24],
            "bundle_id": manifest["bundle_id"],
            "bundle_manifest_sha256": manifest["bundle_manifest_sha256"],
            "batch_id": task["batch_id"],
            "expert_id": task["expert_id"],
            "results": [result],
        }
        submission = submission_body | {
            "payload_sha256": submission_payload_sha256(submission_body)
        }
        status, submitted = request_json(
            expert,
            base + "/api/expert/submit",
            body={"submission": submission},
            csrf=expert_session["csrf"],
        )
        assert status == 200
        assert submitted["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
        assert store.result_rows()[0]["bundle_id"] == manifest["bundle_id"]
        with admin.open(base + "/api/admin/results/export?format=json", timeout=5) as response:
            exported = json.load(response)
        assert exported["schema_version"] == "sciencevision.manager_results_export.v2"
        assert exported["contract_version"] == "sciencevision.manager_results_exchange.v2"
        assert exported["results"][0]["bundle_id"] == manifest["bundle_id"]
        traversal = urllib.request.Request(base + "/content/%2e%2e/manager.db")
        with pytest.raises(urllib.error.HTTPError) as error:
            expert.open(traversal, timeout=5)
        assert error.value.code == 404
        status, _ = request_json(expert, base + "/api/admin/overview")
        assert status == 403
        status, value = request_json(
            expert,
            base + "/api/logout",
            body={},
            csrf="wrong",
        )
        assert status == 403 and "CSRF" in value["error"]
        status, _ = request_json(
            expert,
            base + "/api/logout",
            body={},
            csrf=expert_session["csrf"],
        )
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_admin_resets_only_expert_passwords_and_revokes_target_sessions(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_root = imported_workflow["tmp"] / "reset-server-auth"
    credentials = auth_root / "credentials.json"
    plaintext = auth_root / "handoff.csv"
    secret = auth_root / "session.key"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    passwords = {
        row["username"]: row["password"]
        for row in csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines())
    }

    class ReviewParentRecorder:
        def __init__(self) -> None:
            self.revoked: list[str] = []

        def revoke_parent(self, session_id: str) -> None:
            self.revoked.append(session_id)

    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = imported_workflow["store"]
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    server.credentials_path = credentials
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {}
    review_recorder = ReviewParentRecorder()
    server.review_servers = {"recording": review_recorder}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/sv-annotate"
    try:
        anonymous = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, _ = request_json(anonymous, base + "/api/admin/accounts")
        assert status == 401

        activated: dict[str, str] = {}
        for expert_name in ("expert-a", "expert-b"):
            activation_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )
            activation_session = login(
                activation_opener, base, expert_name, passwords[expert_name]
            )
            assert activation_session["password_change_required"] is True
            activated[expert_name] = f"{expert_name}-activated-password"
            activate(
                activation_opener, base, activation_session, activated[expert_name]
            )
        # Activation revocations are setup noise for the reset assertions below.
        review_recorder.revoked.clear()

        expert_a_first = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        expert_a_first_session = login(
            expert_a_first, base, "expert-a", activated["expert-a"]
        )
        expert_a_second = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        login(expert_a_second, base, "expert-a", activated["expert-a"])
        expert_b = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        login(expert_b, base, "expert-b", activated["expert-b"])
        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = login(admin, base, "admin", passwords["admin"])

        status, accounts = request_json(admin, base + "/api/admin/accounts")
        assert status == 200
        assert {account["username"] for account in accounts["accounts"]} == {
            "admin",
            "expert-a",
            "expert-b",
        }
        assert all(
            set(account) == {"username", "role", "expert_id", "display_name"}
            for account in accounts["accounts"]
        )
        serialized_accounts = json.dumps(accounts)
        for forbidden in ("password_hash", "salt", "iterations", passwords["expert-a"]):
            assert forbidden not in serialized_accounts

        status, _ = request_json(expert_a_first, base + "/api/admin/accounts")
        assert status == 403
        status, value = request_json(
            expert_a_first,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a"},
            csrf=expert_a_first_session["csrf"],
        )
        assert status == 403 and "admin" in value["error"]
        status, value = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a"},
        )
        assert status == 403 and "CSRF" in value["error"]
        status, value = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a"},
            csrf=admin_session["csrf"],
            origin=f"http://127.0.0.1:{server.server_port + 1}",
        )
        assert status == 403 and "cross-origin" in value["error"]
        status, _ = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "admin"},
            csrf=admin_session["csrf"],
        )
        assert status == 400
        status, _ = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a", "role": "expert"},
            csrf=admin_session["csrf"],
        )
        assert status == 400
        # An administrator may not choose the expert's password: the reset only
        # issues a temporary one that the expert must replace on first login.
        status, _ = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a", "password": "a-password-long-enough"},
            csrf=admin_session["csrf"],
        )
        assert status == 400

        users_before_failure = server.users

        def fail_reset(*_args: object, **_kwargs: object) -> tuple[str, dict]:
            raise OSError("simulated durable write failure")

        with monkeypatch.context() as patch:
            patch.setattr(online_server_module, "reset_expert_password", fail_reset)
            status, value = request_json(
                admin,
                base + "/api/admin/accounts/reset",
                body={"expert_id": "expert-a"},
                csrf=admin_session["csrf"],
            )
        assert status == 500 and value == {"error": "credential update failed"}
        assert server.users is users_before_failure
        status, value = request_json(expert_a_first, base + "/api/session")
        assert status == 200 and value["username"] == "expert-a"

        target_session_ids = {
            session_id
            for session_id, record in server.active_sessions.items()
            if record["username"] == "expert-a"
        }
        assert len(target_session_ids) == 2
        status, reset = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a"},
            csrf=admin_session["csrf"],
        )
        assert status == 200
        generated_password = reset["password"]
        assert generated_password not in (passwords["expert-a"], activated["expert-a"])
        assert reset["account"] == {
            "username": "expert-a",
            "role": "expert",
            "expert_id": "expert-a",
            "display_name": "<b>Alice</b>",
            "password_change_required": True,
        }
        assert set(reset) == {"schema_version", "account", "password"}
        assert "password_hash" not in json.dumps(reset)
        assert set(review_recorder.revoked) == target_session_ids
        assert not any(
            record["username"] == "expert-a" for record in server.active_sessions.values()
        )

        for target_session in (expert_a_first, expert_a_second):
            status, value = request_json(target_session, base + "/api/session")
            assert status == 401 and value["authenticated"] is False
        status, preserved = request_json(expert_b, base + "/api/session")
        assert status == 200 and preserved["username"] == "expert-b"
        status, preserved = request_json(admin, base + "/api/session")
        assert status == 200 and preserved["username"] == "admin"

        stale_login = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        for retired in (passwords["expert-a"], activated["expert-a"]):
            status, _ = request_json(
                stale_login,
                base + "/api/login",
                body={"username": "expert-a", "password": retired},
            )
            assert status == 401
        reset_expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        reset_expert_session = login(reset_expert, base, "expert-a", generated_password)
        assert reset_expert_session["password_change_required"] is True

        status, second_reset = request_json(
            admin,
            base + "/api/admin/accounts/reset",
            body={"expert_id": "expert-a"},
            csrf=admin_session["csrf"],
        )
        assert status == 200
        reissued_password = second_reset["password"]
        assert reissued_password != generated_password
        status, value = request_json(reset_expert, base + "/api/session")
        assert status == 401 and value["authenticated"] is False
        status, value = request_json(expert_b, base + "/api/session")
        assert status == 200 and value["username"] == "expert-b"
        reissued_expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        login(reissued_expert, base, "expert-a", reissued_password)
        assert reset_expert_session["username"] == "expert-a"
        assert not verify_password(generated_password, load_credentials(credentials)["expert-a"])
        assert verify_password(reissued_password, load_credentials(credentials)["expert-a"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_post_rename_directory_sync_failure_reconciles_live_password_reset(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    auth_root = imported_workflow["tmp"] / "post-rename-sync-auth"
    credentials = auth_root / "credentials.json"
    plaintext = auth_root / "handoff.csv"
    secret = auth_root / "session.key"
    create_credentials(
        imported_workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    passwords = {
        row["username"]: row["password"]
        for row in csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines())
    }

    class ReviewParentRecorder:
        def __init__(self) -> None:
            self.revoked: list[str] = []

        def revoke_parent(self, session_id: str) -> None:
            self.revoked.append(session_id)

    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = imported_workflow["store"]
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    server.credentials_path = credentials
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {}
    review_recorder = ReviewParentRecorder()
    server.review_servers = {"recording": review_recorder}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/sv-annotate"
    try:
        expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        login(expert, base, "expert-a", passwords["expert-a"])
        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = login(admin, base, "admin", passwords["admin"])
        target_session_ids = {
            session_id
            for session_id, record in server.active_sessions.items()
            if record["username"] == "expert-a"
        }
        assert len(target_session_ids) == 1

        directory_sync_calls: list[object] = []

        def fail_parent_directory_sync(path: object) -> None:
            directory_sync_calls.append(path)
            raise OSError("simulated post-rename directory sync failure")

        caplog.set_level(logging.INFO, logger=online_server_module.__name__)
        with monkeypatch.context() as patch:
            patch.setattr(
                online_auth_module, "_fsync_parent_directory", fail_parent_directory_sync
            )
            status, reset = request_json(
                admin,
                base + "/api/admin/accounts/reset",
                body={"expert_id": "expert-a"},
                csrf=admin_session["csrf"],
            )

        assert status == 200
        assert directory_sync_calls == [credentials, credentials]
        assert set(reset) == {"schema_version", "account", "password", "durability"}
        assert reset["durability"] == "directory_sync_unconfirmed"
        new_password = reset["password"]
        assert new_password not in caplog.text
        assert "password_hash" not in json.dumps(reset)

        persisted_users = load_credentials(credentials)
        assert verify_password(new_password, persisted_users["expert-a"])
        assert not verify_password(passwords["expert-a"], persisted_users["expert-a"])
        assert server.users == persisted_users
        assert verify_password(new_password, server.users["expert-a"])
        assert not verify_password(passwords["expert-a"], server.users["expert-a"])
        assert set(review_recorder.revoked) == target_session_ids
        assert not any(
            record["username"] == "expert-a" for record in server.active_sessions.values()
        )

        status, value = request_json(expert, base + "/api/session")
        assert status == 401 and value["authenticated"] is False
        old_login = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, _ = request_json(
            old_login,
            base + "/api/login",
            body={"username": "expert-a", "password": passwords["expert-a"]},
        )
        assert status == 401
        new_login = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        new_session = login(new_login, base, "expert-a", new_password)
        assert new_session["username"] == "expert-a"
        status, value = request_json(admin, base + "/api/session")
        assert status == 200 and value["username"] == "admin"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
