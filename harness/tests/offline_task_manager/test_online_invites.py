from __future__ import annotations

import csv
import errno
import http.cookiejar
import json
import os
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import pytest

from tools.offline_task_manager import online_auth as online_auth_module
from tools.offline_task_manager import online_server as online_server_module
from tools.offline_task_manager.contract import ContractError
from tools.offline_task_manager.online_auth import (
    create_credentials,
    invite_expert_account,
    load_credentials,
    verify_password,
)
from tools.offline_task_manager.online_server import Handler, OnlineServer


def _request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    body: dict | None = None,
    csrf: str | None = None,
) -> tuple[int, dict]:
    headers: dict[str, str] = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def _login(
    opener: urllib.request.OpenerDirector, base: str, username: str, password: str
) -> dict:
    status, value = _request_json(
        opener,
        base + "/api/login",
        body={"username": username, "password": password},
    )
    assert status == 200, value
    status, session = _request_json(opener, base + "/api/session")
    assert status == 200
    return session


def _import_invite_targets(store: object) -> None:
    store.import_experts_csv(
        "schema_version,expert_id,name,domains\n"
        "sciencevision.experts_csv.v1,expert-new,New Expert,biology\n"
        "sciencevision.experts_csv.v1,expert-inactive,Inactive Expert,biology\n"
    )
    store.connection.execute(
        "UPDATE experts SET active=0 WHERE expert_id=?", ("expert-inactive",)
    )
    store.connection.commit()


def test_invite_expert_account_is_atomic_private_and_server_generated(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    root: Path = imported_workflow["tmp"] / "invite-auth"
    credentials = root / "credentials.json"
    create_credentials(
        store,
        credentials_path=credentials,
        plaintext_path=root / "handoff.csv",
        session_secret_path=root / "session.key",
    )
    _import_invite_targets(store)
    before = load_credentials(credentials)

    invite = invite_expert_account(
        credentials,
        expert_id="expert-new",
        display_name="New Expert",
        expected_users=before,
    )
    persisted = load_credentials(credentials)
    assert credentials.stat().st_mode & 0o077 == 0
    assert invite.directory_sync_confirmed
    assert invite.users == persisted
    assert persisted["expert-new"]["username"] == "expert-new"
    assert persisted["expert-new"]["expert_id"] == "expert-new"
    assert persisted["expert-new"]["role"] == "expert"
    assert verify_password(invite.password, persisted["expert-new"])
    assert invite.password not in credentials.read_text(encoding="utf-8")

    after_success = credentials.read_bytes()
    with pytest.raises(ContractError, match="already has"):
        invite_expert_account(
            credentials,
            expert_id="expert-new",
            display_name="New Expert",
            expected_users=persisted,
        )
    assert credentials.read_bytes() == after_success

    store.import_experts_csv(
        "schema_version,expert_id,name,domains\n"
        "sciencevision.experts_csv.v1,expert-write-fail,Write Fail,biology\n"
    )

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(online_auth_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        invite_expert_account(
            credentials,
            expert_id="expert-write-fail",
            display_name="Write Fail",
            expected_users=persisted,
        )
    assert credentials.read_bytes() == after_success
    assert "expert-write-fail" not in load_credentials(credentials)


def test_admin_invites_only_active_roster_experts_and_requires_activation(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    root: Path = imported_workflow["tmp"] / "invite-server"
    credentials = root / "credentials.json"
    plaintext = root / "handoff.csv"
    secret = root / "session.key"
    create_credentials(
        store,
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    _import_invite_targets(store)
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
        anonymous = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, _ = _request_json(
            anonymous,
            base + "/api/admin/accounts/invite",
            body={"expert_id": "expert-new"},
        )
        assert status == 401

        expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        expert_session = _login(expert, base, "expert-a", passwords["expert-a"])
        status, _ = _request_json(
            expert,
            base + "/api/admin/accounts/invite",
            body={"expert_id": "expert-new"},
            csrf=expert_session["csrf"],
        )
        assert status == 403

        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = _login(admin, base, "admin", passwords["admin"])
        status, _ = _request_json(
            admin,
            base + "/api/admin/accounts/invite",
            body={"expert_id": "expert-new"},
        )
        assert status == 403

        unchanged = credentials.read_bytes()
        for body in (
            {"expert_id": "expert-new", "username": "chosen-name"},
            {"expert_id": "expert-new", "role": "admin"},
            {"expert_id": "expert-new", "password": "client-chosen-password"},
            {"expert_id": "missing-expert"},
            {"expert_id": "expert-inactive"},
            {"expert_id": "expert-a"},
        ):
            status, _ = _request_json(
                admin,
                base + "/api/admin/accounts/invite",
                body=body,
                csrf=admin_session["csrf"],
            )
            assert status == 400
            assert credentials.read_bytes() == unchanged

        users_before_failure = server.users

        def fail_invite(*_args: object, **_kwargs: object) -> object:
            raise OSError("simulated credential write failure")

        with monkeypatch.context() as patch:
            patch.setattr(online_server_module, "invite_expert_account", fail_invite)
            status, value = _request_json(
                admin,
                base + "/api/admin/accounts/invite",
                body={"expert_id": "expert-new"},
                csrf=admin_session["csrf"],
            )
        assert status == 500
        assert value == {"error": "credential update failed"}
        assert server.users is users_before_failure
        assert credentials.read_bytes() == unchanged

        real_fsync = os.fsync

        def reject_directory_fsync(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(errno.EINVAL, "directory fsync unsupported")
            real_fsync(descriptor)

        with monkeypatch.context() as patch:
            patch.setattr(online_auth_module.os, "fsync", reject_directory_fsync)
            status, invited = _request_json(
                admin,
                base + "/api/admin/accounts/invite",
                body={"expert_id": "expert-new"},
                csrf=admin_session["csrf"],
            )
        assert status == 201
        assert set(invited) == {"schema_version", "account", "password", "durability"}
        assert invited["schema_version"] == "sciencevision.online_account_invite.v1"
        assert invited["durability"] == "directory_sync_unconfirmed"
        assert invited["account"] == {
            "username": "expert-new",
            "role": "expert",
            "expert_id": "expert-new",
            "display_name": "New Expert",
            "password_change_required": True,
        }
        serialized = json.dumps(invited)
        assert "password_hash" not in serialized
        assert "salt" not in serialized
        assert "iterations" not in serialized

        persisted = load_credentials(credentials)
        assert server.users == persisted
        assert credentials.stat().st_mode & 0o077 == 0
        assert verify_password(invited["password"], persisted["expert-new"])
        assert invited["password"] not in credentials.read_text(encoding="utf-8")

        invited_expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        session = _login(invited_expert, base, "expert-new", invited["password"])
        assert session["expert_id"] == "expert-new"
        assert session["password_change_required"] is True
        status, value = _request_json(
            invited_expert,
            base + "/api/expert/bootstrap",
        )
        assert status == 403
        assert value == {"error": "password change required"}

        status, _ = _request_json(
            admin,
            base + "/api/admin/accounts/invite",
            body={"expert_id": "expert-new"},
            csrf=admin_session["csrf"],
        )
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
