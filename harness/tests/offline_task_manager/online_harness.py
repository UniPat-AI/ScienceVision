"""Bring up an authenticated gateway in-process for tests.

``test_online_server.py`` predates this module and still configures its own
server inline; new tests should use ``serve`` so the wiring lives in one place.
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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from tools.offline_task_manager.online_auth import create_credentials, load_credentials
from tools.offline_task_manager.online_server import Handler, OnlineServer
from tools.offline_task_manager.store import ManagerStore

Opener = urllib.request.OpenerDirector


def opener() -> Opener:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def request_json(
    client: Opener,
    url: str,
    *,
    body: dict | None = None,
    csrf: str | None = None,
    origin: str | bool | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    """POST ``body`` as JSON (or GET when it is None) and decode the envelope."""

    sent = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json"
        # State-changing requests need a trusted Origin. Pass origin=False to
        # omit the header, or a string to forge one.
        if origin is not False:
            parts = urllib.parse.urlsplit(url)
            sent["Origin"] = (
                origin
                if isinstance(origin, str)
                else f"{parts.scheme}://{parts.netloc}"
            )
    if csrf:
        sent["X-CSRF-Token"] = csrf
    request = urllib.request.Request(
        url, data=data, headers=sent, method="POST" if data else "GET"
    )
    try:
        with client.open(request, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def activated_password(username: str) -> str:
    """Password an invited account answers to once its handoff password is spent.

    The gateway forces an invited account to replace the handoff password before
    any other route answers, so the harness rotates it to a deterministic value
    instead of threading a mutable password book through every test.
    """

    return f"{username}-activated-password"


@dataclass(frozen=True)
class Gateway:
    """A running gateway plus the credentials needed to sign into it."""

    base: str
    passwords: dict[str, str]
    trajectory_root: Path | None

    def login(self, username: str) -> tuple[Opener, dict]:
        client = opener()
        status, value = request_json(
            client,
            self.base + "/api/login",
            body={"username": username, "password": self.passwords[username]},
        )
        if status != 200:
            # An earlier login in this gateway already spent the handoff
            # password, so the account answers to its activated one instead.
            status, value = request_json(
                client,
                self.base + "/api/login",
                body={"username": username, "password": activated_password(username)},
            )
        assert status == 200, value
        status, session = request_json(client, self.base + "/api/session")
        assert status == 200, session
        if session["password_change_required"]:
            session = self._activate(client, session, activated_password(username))
        return client, session

    def _activate(self, client: Opener, session: dict, password: str) -> dict:
        """Clear the invited-account password gate that fronts every other route."""

        status, value = request_json(
            client,
            self.base + "/api/password-change",
            body={"new_password": password, "confirm_password": password},
            csrf=session["csrf"],
        )
        assert status == 200, value
        # Activation rotates the session cookie, so the caller needs the
        # refreshed envelope rather than the one it signed in with.
        status, rotated = request_json(client, self.base + "/api/session")
        assert status == 200, rotated
        assert rotated["password_change_required"] is False, rotated
        return rotated

    def read(self, client: Opener, path: str) -> tuple[bytes, dict[str, str]]:
        with client.open(self.base + path, timeout=5) as response:
            return response.read(), dict(response.headers)


@contextmanager
def serve(
    store: ManagerStore, state_root: Path, *, trajectories: bool = True
) -> Iterator[Gateway]:
    """Serve ``store`` on an ephemeral loopback port for the duration of a with.

    Pass ``trajectories=False`` to leave trajectory storage unconfigured, which is
    how a host that never provisioned the writable directory behaves.
    """

    credentials = state_root / "credentials.json"
    plaintext = state_root / "handoff.csv"
    secret = state_root / "session.key"
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
    trajectory_root: Path | None = None
    if trajectories:
        trajectory_root = state_root / "trajectories"
        trajectory_root.mkdir(parents=True, exist_ok=True)

    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = store
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    # Activation rewrites the credentials file, so the route needs its path.
    server.credentials_path = credentials
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {}
    server.review_servers = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    server.trajectory_root = trajectory_root
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Gateway(
            base=f"http://127.0.0.1:{server.server_port}/sv-annotate",
            passwords=passwords,
            trajectory_root=trajectory_root,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
