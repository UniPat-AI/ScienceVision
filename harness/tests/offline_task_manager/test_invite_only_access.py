"""Contract tests for the invite-only access model.

Experts never register themselves: an administrator invites a roster row,
the gateway issues a temporary password, and the expert activates it. These
tests lock that shape so a later change cannot quietly reopen a
self-service signup path or blur the expert/administrator boundary.
"""

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

from tools.offline_task_manager.online_auth import create_credentials, load_credentials
from tools.offline_task_manager.online_server import Handler, OnlineServer

ONLINE_APP_ROOT = Path("tools/offline_task_manager/online_app")

REGISTRATION_CANDIDATES = (
    "/api/register",
    "/api/signup",
    "/api/accounts",
    "/api/accounts/register",
    "/api/accounts/signup",
    "/api/admin/accounts/create",
    "/register/",
    "/signup/",
)

ADMIN_GETS = (
    "/admin/",
    "/api/admin/accounts",
    "/api/admin/overview",
    "/api/admin/audit-events",
    "/api/admin/review-bundles",
    "/api/admin/results/export",
)

ADMIN_POSTS = (
    "/api/admin/accounts/invite",
    "/api/admin/accounts/reset",
    "/api/admin/assignments/review",
    "/api/admin/assignments/auto",
    "/api/admin/assignments/explicit",
    "/api/admin/experts/import",
    "/api/admin/batches/import",
    "/api/admin/packages/build",
    "/api/admin/runtime/build",
    "/api/admin/results/import",
    "/api/admin/review-bundles/open",
    "/api/admin/review-bundles/replay",
)

EXPERT_GETS = (
    "/api/expert/bootstrap",
    "/api/expert/manifest",
    "/api/expert/tasks",
)


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    body: dict | None = None,
    csrf: str | None = None,
) -> tuple[int, dict | None]:
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
        method="POST" if body is not None else "GET",
    )
    try:
        with opener.open(request, timeout=5) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except json.JSONDecodeError:
            return error.code, None
    try:
        return 200, json.loads(payload)
    except json.JSONDecodeError:
        return 200, None


def _client() -> urllib.request.OpenerDirector:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        NoRedirect,
    )


def _login(
    opener: urllib.request.OpenerDirector, base: str, username: str, password: str
) -> dict:
    status, value = _request(
        opener, base + "/api/login", body={"username": username, "password": password}
    )
    assert status == 200, value
    status, session = _request(opener, base + "/api/session")
    assert status == 200
    assert session is not None
    return session


def _activated_expert(
    base: str, username: str, temporary_password: str
) -> tuple[urllib.request.OpenerDirector, dict]:
    opener = _client()
    session = _login(opener, base, username, temporary_password)
    assert session["password_change_required"] is True
    password = f"{username}-invite-only-password"
    status, value = _request(
        opener,
        base + "/api/password-change",
        body={"new_password": password, "confirm_password": password},
        csrf=session["csrf"],
    )
    assert status == 200, value
    status, session = _request(opener, base + "/api/session")
    assert status == 200
    assert session is not None
    assert session["password_change_required"] is False
    return opener, session


@pytest.fixture
def invite_only_server(imported_workflow: dict) -> dict:
    root: Path = imported_workflow["tmp"] / "invite-only-server"
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
            "base": f"http://127.0.0.1:{server.server_port}/sv-annotate",
            "passwords": passwords,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_gateway_exposes_no_self_registration_endpoint(invite_only_server: dict) -> None:
    base = invite_only_server["base"]
    anonymous = _client()
    for path in REGISTRATION_CANDIDATES:
        status, _ = _request(anonymous, base + path)
        assert status != 200, f"GET {path} must not serve a signup surface"
        status, _ = _request(anonymous, base + path, body={"username": "walk-in"})
        assert status != 200, f"POST {path} must not create an account"

    expert, session = _activated_expert(
        base, "expert-a", invite_only_server["passwords"]["expert-a"]
    )
    for path in REGISTRATION_CANDIDATES:
        status, _ = _request(expert, base + path, body={}, csrf=session["csrf"])
        assert status != 200, f"POST {path} must stay closed to expert sessions"


def test_login_page_offers_no_signup_affordance() -> None:
    markup = (ONLINE_APP_ROOT / "login.html").read_text(encoding="utf-8").lower()
    script = (ONLINE_APP_ROOT / "login.js").read_text(encoding="utf-8").lower()
    for forbidden in ("register", "sign up", "signup", "create account", "new account"):
        assert forbidden not in markup, f"login page must not advertise {forbidden!r}"
        assert forbidden not in script, f"login script must not reach for {forbidden!r}"
    # The only account-shaped action on the login page is activating an
    # already-invited account.
    assert 'data-i18n="login.activatesubmit"' in markup


def test_expert_session_cannot_reach_administrator_surfaces(
    invite_only_server: dict,
) -> None:
    base = invite_only_server["base"]
    expert, session = _activated_expert(
        base, "expert-a", invite_only_server["passwords"]["expert-a"]
    )
    for path in ADMIN_GETS:
        status, value = _request(expert, base + path)
        assert status == 403, f"GET {path} leaked to an expert session"
        assert value is not None and "admin" in value["error"]
    for path in ADMIN_POSTS:
        status, value = _request(expert, base + path, body={}, csrf=session["csrf"])
        assert status == 403, f"POST {path} leaked to an expert session"
        assert value is not None and "admin" in value["error"]


def test_administrator_cannot_reach_expert_task_surfaces(
    invite_only_server: dict,
) -> None:
    base = invite_only_server["base"]
    admin = _client()
    session = _login(admin, base, "admin", invite_only_server["passwords"]["admin"])
    assert session["role"] == "admin"
    for path in EXPERT_GETS:
        status, value = _request(admin, base + path)
        assert status == 403, f"GET {path} leaked to an administrator session"
        assert value is not None and "expert" in value["error"]
    status, value = _request(
        admin,
        base + "/api/expert/submit",
        body={"submission": {"expert_id": "expert-a"}},
        csrf=session["csrf"],
    )
    assert status == 403 and value is not None and "expert" in value["error"]


def test_expert_cannot_submit_on_behalf_of_another_expert(
    invite_only_server: dict,
) -> None:
    base = invite_only_server["base"]
    expert, session = _activated_expert(
        base, "expert-a", invite_only_server["passwords"]["expert-a"]
    )
    status, value = _request(
        expert,
        base + "/api/expert/submit",
        body={"submission": {"expert_id": "expert-b", "answers": []}},
        csrf=session["csrf"],
    )
    assert status == 403
    assert value == {"error": "submission expert mismatch"}
