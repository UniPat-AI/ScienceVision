from __future__ import annotations

import csv
import http.cookiejar
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import pytest

from tools.offline_task_manager.online_auth import (
    create_credentials,
    load_credentials,
    verify_password,
)
from tools.offline_task_manager.online_server import COOKIE_NAME, Handler, OnlineServer


def _request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    body: dict | None = None,
    csrf: str | None = None,
    origin: str | None = None,
    cookie: str | None = None,
) -> tuple[int, dict]:
    headers: dict[str, str] = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        parsed = urllib.parse.urlsplit(url)
        headers["Origin"] = origin or f"{parsed.scheme}://{parsed.netloc}"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if cookie:
        headers["Cookie"] = f"{COOKIE_NAME}={cookie}"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def _new_client() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return opener, jar


def _session_cookie(jar: http.cookiejar.CookieJar) -> str:
    return next(cookie.value for cookie in jar if cookie.name == COOKIE_NAME)


def _login(
    opener: urllib.request.OpenerDirector,
    base: str,
    username: str,
    password: str,
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


@pytest.fixture
def activation_server(imported_workflow: dict) -> dict:
    root: Path = imported_workflow["tmp"] / "activation-server"
    credentials = root / "credentials.json"
    plaintext = root / "handoff.csv"
    secret = root / "session.key"
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
    server.review_servers = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "server": server,
            "base": f"http://127.0.0.1:{server.server_port}/sv-annotate",
            "credentials": credentials,
            "passwords": passwords,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_restricted_session_can_only_activate_or_logout(activation_server: dict) -> None:
    base = activation_server["base"]
    temporary_password = activation_server["passwords"]["expert-a"]
    expert, jar = _new_client()
    session = _login(expert, base, "expert-a", temporary_password)
    assert session["password_change_required"] is True
    old_cookie = _session_cookie(jar)

    blocked_gets = (
        "/",
        "/expert/",
        "/static/expert.js",
        "/api/expert/bootstrap",
        "/api/expert/manifest",
        "/api/expert/tasks",
        "/api/expert/open",
        "/viewer/molstar.html",
        "/content/assets/model.cif",
    )
    for path in blocked_gets:
        status, value = _request_json(expert, base + path)
        assert status == 403, path
        assert value == {"error": "password change required"}
    status, value = _request_json(
        expert,
        base + "/api/expert/submit",
        body={"submission": {}},
        csrf=session["csrf"],
    )
    assert status == 403
    assert value == {"error": "password change required"}

    password_body = {
        "new_password": "expert-a-private-password",
        "confirm_password": "expert-a-private-password",
    }
    status, _ = _request_json(expert, base + "/api/password-change", body=password_body)
    assert status == 403
    status, _ = _request_json(
        expert,
        base + "/api/password-change",
        body=password_body,
        csrf=session["csrf"],
        origin="https://attacker.invalid",
    )
    assert status == 403
    for body in (
        {"new_password": "too-short", "confirm_password": "too-short"},
        {"new_password": "a" * 257, "confirm_password": "a" * 257},
        {"new_password": "one-private-password", "confirm_password": "different"},
        {
            "new_password": temporary_password,
            "confirm_password": temporary_password,
        },
        password_body | {"current_password": temporary_password},
    ):
        status, _ = _request_json(
            expert,
            base + "/api/password-change",
            body=body,
            csrf=session["csrf"],
        )
        assert status == 400

    status, changed = _request_json(
        expert,
        base + "/api/password-change",
        body=password_body,
        csrf=session["csrf"],
    )
    assert status == 200
    assert changed["password_change_required"] is False
    assert _session_cookie(jar) != old_cookie
    status, replacement_session = _request_json(expert, base + "/api/session")
    assert status == 200
    assert replacement_session["password_change_required"] is False
    with expert.open(base + "/expert/", timeout=5) as response:
        assert response.status == 200

    no_cookies = urllib.request.build_opener()
    status, value = _request_json(
        no_cookies,
        base + "/api/session",
        cookie=old_cookie,
    )
    assert status == 401
    assert value == {"authenticated": False}
    status, _ = _request_json(
        no_cookies,
        base + "/api/login",
        body={"username": "expert-a", "password": temporary_password},
    )
    assert status == 401
    assert "expert-a-private-password" not in activation_server[
        "credentials"
    ].read_text(encoding="utf-8")


def test_restricted_session_may_logout(activation_server: dict) -> None:
    base = activation_server["base"]
    expert, _ = _new_client()
    session = _login(
        expert,
        base,
        "expert-a",
        activation_server["passwords"]["expert-a"],
    )
    status, value = _request_json(
        expert,
        base + "/api/logout",
        body={},
        csrf=session["csrf"],
    )
    assert status == 200 and value == {"ok": True}
    status, _ = _request_json(expert, base + "/api/session")
    assert status == 401


def test_admin_reset_revokes_active_session_and_reissues_temporary_password(
    activation_server: dict,
) -> None:
    base = activation_server["base"]
    expert, _ = _new_client()
    expert_session = _login(
        expert,
        base,
        "expert-a",
        activation_server["passwords"]["expert-a"],
    )
    private_password = "expert-a-private-password"
    status, _ = _request_json(
        expert,
        base + "/api/password-change",
        body={
            "new_password": private_password,
            "confirm_password": private_password,
        },
        csrf=expert_session["csrf"],
    )
    assert status == 200

    admin, _ = _new_client()
    admin_session = _login(
        admin,
        base,
        "admin",
        activation_server["passwords"]["admin"],
    )
    status, overview = _request_json(admin, base + "/api/admin/overview")
    assert status == 200
    account = next(
        row["online_account"]
        for row in overview["experts"]
        if row["expert_id"] == "expert-a"
    )
    assert account == {"username": "expert-a", "status": "active"}

    reset_url = base + "/api/admin/accounts/reset"
    status, _ = _request_json(admin, reset_url, body={"username": "expert-a"})
    assert status == 403
    status, _ = _request_json(
        admin,
        reset_url,
        body={"expert_id": "expert-a"},
        csrf=admin_session["csrf"],
        origin="https://attacker.invalid",
    )
    assert status == 403
    status, _ = _request_json(
        expert,
        reset_url,
        body={"expert_id": "expert-a"},
        csrf=_request_json(expert, base + "/api/session")[1]["csrf"],
    )
    assert status == 403
    unchanged = activation_server["credentials"].read_bytes()
    for body in (
        {"expert_id": "admin"},
        {"expert_id": "expert-a", "password": "client-value"},
        {"expert_id": "expert-a", "role": "admin"},
        {"username": "expert-a"},
    ):
        status, _ = _request_json(
            admin,
            reset_url,
            body=body,
            csrf=admin_session["csrf"],
        )
        assert status == 400
        assert activation_server["credentials"].read_bytes() == unchanged

    status, reset = _request_json(
        admin,
        reset_url,
        body={"expert_id": "expert-a"},
        csrf=admin_session["csrf"],
    )
    assert status == 200
    assert set(reset) == {"schema_version", "account", "password"}
    assert reset["account"]["password_change_required"] is True
    persisted = load_credentials(activation_server["credentials"])
    assert persisted["expert-a"]["password_version"] == 3
    assert persisted["expert-a"]["must_change_password"] is True
    assert verify_password(reset["password"], persisted["expert-a"])
    assert not verify_password(private_password, persisted["expert-a"])
    assert reset["password"] not in activation_server["credentials"].read_text(
        encoding="utf-8"
    )

    status, _ = _request_json(expert, base + "/api/session")
    assert status == 401
    reissued, _ = _new_client()
    reissued_session = _login(reissued, base, "expert-a", reset["password"])
    assert reissued_session["password_change_required"] is True
    status, overview = _request_json(admin, base + "/api/admin/overview")
    assert status == 200
    account = next(
        row["online_account"]
        for row in overview["experts"]
        if row["expert_id"] == "expert-a"
    )
    assert account == {
        "username": "expert-a",
        "status": "password_change_required",
    }


def test_legacy_custom_username_is_joined_and_reset_by_expert_id(
    activation_server: dict,
) -> None:
    credentials = activation_server["credentials"]
    document = json.loads(credentials.read_text(encoding="utf-8"))
    account = next(
        record
        for record in document["users"]
        if record.get("expert_id") == "expert-a"
    )
    account["username"] = "legacy-expert-login"
    account.pop("must_change_password")
    account.pop("password_version")
    credentials.write_text(json.dumps(document), encoding="utf-8")
    with activation_server["server"].credential_lock:
        activation_server["server"].users = load_credentials(credentials)

    base = activation_server["base"]
    expert, _ = _new_client()
    legacy_session = _login(
        expert,
        base,
        "legacy-expert-login",
        activation_server["passwords"]["expert-a"],
    )
    assert legacy_session["password_change_required"] is False
    admin, _ = _new_client()
    admin_session = _login(
        admin,
        base,
        "admin",
        activation_server["passwords"]["admin"],
    )

    status, overview = _request_json(admin, base + "/api/admin/overview")
    assert status == 200
    roster_account = next(
        row["online_account"]
        for row in overview["experts"]
        if row["expert_id"] == "expert-a"
    )
    assert roster_account == {
        "username": "legacy-expert-login",
        "status": "active",
    }

    status, _ = _request_json(
        admin,
        base + "/api/admin/accounts/invite",
        body={"expert_id": "expert-a"},
        csrf=admin_session["csrf"],
    )
    assert status == 400

    status, reset = _request_json(
        admin,
        base + "/api/admin/accounts/reset",
        body={"expert_id": "expert-a"},
        csrf=admin_session["csrf"],
    )
    assert status == 200
    assert reset["account"]["username"] == "legacy-expert-login"
    assert reset["account"]["expert_id"] == "expert-a"
    persisted = load_credentials(credentials)
    assert "expert-a" not in persisted
    assert persisted["legacy-expert-login"]["password_version"] == 2
    assert verify_password(reset["password"], persisted["legacy-expert-login"])

    status, _ = _request_json(expert, base + "/api/session")
    assert status == 401
