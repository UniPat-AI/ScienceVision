from __future__ import annotations

import csv
import hashlib
import http.cookiejar
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import pytest

from tools.offline_task_manager.__main__ import parser
from tools.offline_task_manager.contract import ContractError
from tools.offline_task_manager.online_auth import create_credentials, load_credentials
from tools.offline_task_manager.online_server import (
    Handler,
    OnlineServer,
    _parse_public_origin,
    _review_origin_for_start,
    serve_online,
)
from tools.offline_task_manager.review_bundles import load_review_bundles
from tools.offline_task_manager.review_server import ManagedReviewServer


def _write_review_bundle(root: Path) -> Path:
    files: dict[str, bytes] = {
        "TASK_INDEX.md": b"# Review tasks\n",
        "crawl_data/model.bin": b"0123456789",
        "datasets/mask.bin": b"mask-bytes",
        "viewer/app.js": b"const viewer = `/viewer/${name}.html`;\n",
        "viewer/sciencevision_tasks.html": (
            b'<!doctype html><script src="/viewer/app.js"></script>'
            b'<iframe src="/viewer/molstar.html" title="Molstar"></iframe>'
            b"<a href='/viewer/'>Viewer</a>"
            b"<a href='?manifest=%2Fbundle_manifest.json'>Manifest</a>\n"
        ),
        "viewer/molstar.html": b"<!doctype html><canvas id=molstar></canvas>\n",
        "viewer/niivue.html": b"<!doctype html><canvas id=niivue></canvas>\n",
        "viewer/trajectory-replay.html": b"<!doctype html><main>Local trajectory replay</main>\n",
        "viewer/future-viewer.html": b"<!doctype html><canvas id=future></canvas>\n",
        "viewer/index.html": b"<!doctype html><h1>Viewer index</h1>\n",
    }
    task = {
        "task_id": "review-task-1",
        "domain": "biology",
        "title": "Review task",
        "viewer_url": "/viewer/molstar.html",
        "asset": {
            "path": "/crawl_data/model.bin",
            "repo_path": "crawl_data/model.bin",
            "mask_path": "/datasets/mask.bin",
            "mask_repo_path": "datasets/mask.bin",
            "viewer": "molstar",
            "format": "mmcif",
        },
    }
    future_task = task | {
        "task_id": "review-task-2",
        "title": "Future viewer task",
        "asset": task["asset"] | {"viewer": "future-viewer"},
    }
    files["tasks/review.jsonl"] = (
        "\n".join((json.dumps(task), json.dumps(future_task))) + "\n"
    ).encode()
    manifest = {
        "schema_version": "1.1",
        "kind": "sciencevision-task-bundle",
        "bundle_id": "fixture-review",
        "title": "Fixture review",
        "tasks_jsonl": "/tasks/review.jsonl",
        "task_index": "/TASK_INDEX.md",
        "task_count": 2,
    }
    files["bundle_manifest.json"] = (json.dumps(manifest) + "\n").encode()
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    checksums = "".join(
        f"{hashlib.sha256(payload).hexdigest()}  {relative}\n"
        for relative, payload in sorted(files.items())
    )
    (root / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    return root


def _append_sparse_approved_file(root: Path, relative: str, byte_size: int) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(byte_size)

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    with (root / "SHA256SUMS").open("a", encoding="utf-8") as checksums:
        checksums.write(f"{digest.hexdigest()}  {relative}\n")
    return path


def _write_config(path: Path, root: Path, *, port: int = 0) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "sciencevision.online_review_bundles.v1",
                "bundles": [
                    {
                        "review_id": "sample-review",
                        "title": "Sample review",
                        "root": str(root),
                        "port": port,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    body: dict | None = None,
    origin: str | None = None,
    csrf: str | None = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if data is not None:
        parsed = urllib.parse.urlsplit(url)
        exact_origin = origin if origin is not None else f"{parsed.scheme}://{parsed.netloc}"
        if exact_origin:
            headers["Origin"] = exact_origin
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
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
    assert status == 200, session
    return session


def test_review_bundle_config_fails_closed(tmp_path: Path) -> None:
    root = _write_review_bundle(tmp_path / "bundle")
    config = _write_config(tmp_path / "review-bundles.json", root)
    bundles = load_review_bundles(config)
    assert bundles["sample-review"].task_count == 2
    assert bundles["sample-review"].file_count == 12
    assert bundles["sample-review"].embedded_viewer_paths == {
        "viewer/future-viewer.html",
        "viewer/molstar.html",
        "viewer/niivue.html",
    }
    assert bundles["sample-review"].landing_paths == {
        "viewer/sciencevision_tasks.html",
        "viewer/trajectory-replay.html",
    }

    (root / "viewer/app.js").write_text("tampered", encoding="utf-8")
    with pytest.raises(ContractError, match="checksum mismatch"):
        load_review_bundles(config)


def test_review_bundle_config_must_be_private(tmp_path: Path) -> None:
    root = _write_review_bundle(tmp_path / "bundle")
    config = _write_config(tmp_path / "review-bundles.json", root)
    config.chmod(0o644)
    with pytest.raises(ContractError, match="group or other users"):
        load_review_bundles(config)


def test_review_bundle_config_rejects_symlinks_and_bad_manifest(tmp_path: Path) -> None:
    root = _write_review_bundle(tmp_path / "bundle")
    config = _write_config(tmp_path / "review-bundles.json", root)
    config_link = tmp_path / "config-link.json"
    config_link.symlink_to(config)
    with pytest.raises(ContractError, match="non-symlink"):
        load_review_bundles(config_link)

    root_link = tmp_path / "bundle-link"
    root_link.symlink_to(root, target_is_directory=True)
    root_link_config = _write_config(tmp_path / "root-link.json", root_link)
    with pytest.raises(ContractError, match="non-symlink directory"):
        load_review_bundles(root_link_config)

    manifest_path = root / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["task_count"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    checksum_lines = (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    replacement = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (root / "SHA256SUMS").write_text(
        "\n".join(
            f"{replacement}  bundle_manifest.json"
            if line.endswith("  bundle_manifest.json")
            else line
            for line in checksum_lines
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="task_count does not match"):
        load_review_bundles(config)


def test_serve_online_parser_accepts_review_bundle_config(tmp_path: Path) -> None:
    config = tmp_path / "review-bundles.json"
    args = parser().parse_args(
        [
            "serve-online",
            "--credentials",
            str(tmp_path / "credentials.json"),
            "--session-secret",
            str(tmp_path / "session.key"),
            "--review-bundles",
            str(config),
            "--public-origin",
            "http://127.0.0.1:4174",
            "--review-public-origin",
            "http://127.0.0.1:4175",
        ]
    )
    assert args.review_bundles == config
    assert args.public_origin == "http://127.0.0.1:4174"
    assert args.review_public_origin == "http://127.0.0.1:4175"


def test_review_public_origin_is_independent_and_fail_closed() -> None:
    manager = _parse_public_origin("https://babyvision-v2.unipatai.com")
    with pytest.raises(ContractError, match="review_public_origin is required"):
        _review_origin_for_start(
            manager, None, manager_port=4174, review_bundle_count=1
        )
    with pytest.raises(ContractError, match="must not be the manager"):
        _review_origin_for_start(
            manager,
            "https://babyvision-v2.unipatai.com",
            manager_port=4174,
            review_bundle_count=1,
        )
    with pytest.raises(ContractError, match="hostname distinct"):
        _review_origin_for_start(
            manager,
            "https://babyvision-v2.unipatai.com:444",
            manager_port=4174,
            review_bundle_count=1,
        )
    assert _review_origin_for_start(
        manager,
        "https://review.babyvision-v2.unipatai.com",
        manager_port=4174,
        review_bundle_count=1,
    ) == "https://review.babyvision-v2.unipatai.com"
    local_manager = _parse_public_origin("http://127.0.0.1:4174")
    assert _review_origin_for_start(
        local_manager, None, manager_port=4174, review_bundle_count=2
    ) is None
    with pytest.raises(ContractError, match="requires one bundle"):
        _review_origin_for_start(
            manager,
            "https://review.babyvision-v2.unipatai.com",
            manager_port=4174,
            review_bundle_count=2,
        )


def test_public_review_multi_bundle_startup_fails_before_binding(
    tmp_path: Path,
) -> None:
    first = _write_review_bundle(tmp_path / "first")
    second = _write_review_bundle(tmp_path / "second")
    config = tmp_path / "review-bundles.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "sciencevision.online_review_bundles.v1",
                "bundles": [
                    {
                        "review_id": "first-review",
                        "title": "First review",
                        "root": str(first),
                        "port": 0,
                    },
                    {
                        "review_id": "second-review",
                        "title": "Second review",
                        "root": str(second),
                        "port": 0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    with pytest.raises(ContractError, match="requires one bundle"):
        serve_online(
            tmp_path / "unused.db",
            credentials_path=tmp_path / "unused-credentials.json",
            session_secret_path=tmp_path / "unused-session.key",
            port=4174,
            review_bundles_path=config,
            public_origin="https://babyvision-v2.unipatai.com",
            review_public_origin="https://review.babyvision-v2.unipatai.com",
        )


def test_review_capability_uses_configured_public_origin(tmp_path: Path) -> None:
    root = _write_review_bundle(tmp_path / "bundle")
    bundle = load_review_bundles(
        _write_config(tmp_path / "review-bundles.json", root)
    )["sample-review"]
    managed = ManagedReviewServer(
        bundle,
        public_origin="https://review.babyvision-v2.unipatai.com",
        parent_session_validator=lambda session_id, username, role: (
            session_id == "admin-session" and username == "admin" and role == "admin"
        ),
    )
    try:
        url = managed.create_login_url(
            parent_session_id="admin-session",
            parent_username="admin",
            parent_role="admin",
        )
        assert url.startswith(
            "https://review.babyvision-v2.unipatai.com/__review_login/"
        )
        assert managed.bound_origin not in url
    finally:
        managed.close()


def test_admin_opens_authenticated_isolated_read_only_server(
    workflow: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _write_review_bundle(workflow["tmp"] / "review-bundle")
    config = _write_config(workflow["tmp"] / "review-bundles.json", root)
    auth_root = workflow["tmp"] / "auth-review"
    credentials = auth_root / "credentials.json"
    plaintext = auth_root / "handoff.csv"
    secret = auth_root / "session.key"
    create_credentials(
        workflow["store"],
        credentials_path=credentials,
        plaintext_path=plaintext,
        session_secret_path=secret,
    )
    passwords = {
        row["username"]: row["password"]
        for row in csv.DictReader(plaintext.read_text(encoding="utf-8").splitlines())
    }

    bundle = load_review_bundles(config)["sample-review"]
    server = OnlineServer(("127.0.0.1", 0), Handler)
    server.store = workflow["store"]
    server.store_lock = threading.RLock()
    server.base_path = "/sv-annotate"
    server.users = load_credentials(credentials)
    server.session_secret = secret.read_bytes()
    server.secure_cookie = False
    server.login_attempts = defaultdict(deque)
    server.runtime_manifests = {}
    server.review_bundles = {bundle.review_id: bundle}
    server.review_servers = {}
    server.public_origin = f"http://127.0.0.1:{server.server_port}"
    managed = ManagedReviewServer(
        bundle, parent_session_validator=server.validate_review_parent
    )
    server.review_servers = {bundle.review_id: managed}
    managed.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/sv-annotate"
    try:
        anonymous = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        status, _ = _request_json(anonymous, base + "/api/admin/review-bundles")
        assert status == 401

        expert = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        expert_session = _login(expert, base, "expert-a", passwords["expert-a"])
        status, _ = _request_json(expert, base + "/api/admin/review-bundles")
        assert status == 403
        status, _ = _request_json(
            expert,
            base + "/api/admin/review-bundles/open",
            body={"review_id": "sample-review"},
            csrf=expert_session["csrf"],
        )
        assert status == 403

        admin = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        admin_session = _login(admin, base, "admin", passwords["admin"])
        status, index = _request_json(admin, base + "/api/admin/review-bundles")
        assert status == 200
        assert index["bundles"][0]["task_count"] == 2
        assert index["bundles"][0]["central_submit"] is False
        assert index["bundles"][0]["manager_imported"] is False
        assert index["bundles"][0]["trajectory_replay"] is True
        assert str(root) not in json.dumps(index)
        assert "viewer_url" not in index["bundles"][0]

        review_base = managed.origin
        anonymous_review = urllib.request.build_opener()
        with pytest.raises(urllib.error.HTTPError) as error:
            anonymous_review.open(review_base + "/bundle_manifest.json", timeout=5)
        assert error.value.code == 401

        status, value = _request_json(
            admin,
            base + "/api/admin/review-bundles/open",
            body={"review_id": "sample-review"},
            csrf="wrong",
        )
        assert status == 403 and "CSRF" in value["error"]
        status, opened = _request_json(
            admin,
            base + "/api/admin/review-bundles/open",
            body={"review_id": "sample-review"},
            csrf=admin_session["csrf"],
        )
        assert status == 200
        assert opened["expires_in"] == 60
        viewer_url = opened["viewer_url"]
        manager_origin = urllib.parse.urlsplit(base)
        viewer_origin = urllib.parse.urlsplit(viewer_url)
        assert viewer_origin.hostname == "127.0.0.1"
        assert viewer_origin.port != manager_origin.port
        assert viewer_origin.path.startswith("/__review_login/")
        assert "/sv-annotate" not in viewer_origin.path
        capability = viewer_origin.path.rsplit("/", 1)[-1]

        status, replay_opened = _request_json(
            admin,
            base + "/api/admin/review-bundles/replay",
            body={"review_id": "sample-review"},
            csrf=admin_session["csrf"],
        )
        assert status == 200
        assert replay_opened["schema_version"] == "sciencevision.online_review_replay.v1"
        replay_url = replay_opened["replay_url"]
        replay_jar = http.cookiejar.CookieJar()
        replay = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(replay_jar)
        )
        with replay.open(replay_url, timeout=5) as response:
            assert response.geturl() == review_base + "/viewer/trajectory-replay.html"
            assert response.headers["X-Frame-Options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert b"Local trajectory replay" in response.read()

        review_jar = http.cookiejar.CookieJar()
        review = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(review_jar)
        )
        with review.open(viewer_url, timeout=5) as response:
            assert response.geturl().startswith(
                review_base + "/viewer/sciencevision_tasks.html?"
            )
            assert response.headers["X-Frame-Options"] == "DENY"
            task_csp = response.headers["Content-Security-Policy"]
            assert "frame-ancestors 'none'" in task_csp
            assert "default-src 'self' blob: data:" in task_csp
            assert "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:" in task_csp
            assert "worker-src 'self' blob:" in task_csp
            assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert 'src="/viewer/app.js"' in response.read().decode()
        cookies = list(review_jar)
        assert len(cookies) == 1
        assert cookies[0].name == managed.server.cookie_name
        assert cookies[0].path == "/"
        assert cookies[0].has_nonstandard_attr("HttpOnly")
        assert cookies[0].get_nonstandard_attr("SameSite") == "Strict"
        assert cookies[0].domain_specified is False

        with pytest.raises(urllib.error.HTTPError) as error:
            anonymous_review.open(viewer_url, timeout=5)
        assert error.value.code == 401
        assert capability not in capsys.readouterr().out

        with review.open(review_base + "/bundle_manifest.json", timeout=5) as response:
            manifest = json.load(response)
            assert response.headers.get("X-Frame-Options") is None
            assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
            assert "frame-ancestors" not in response.headers["Content-Security-Policy"]
            assert response.headers.get("Access-Control-Allow-Origin") is None
        assert manifest["tasks_jsonl"] == "/tasks/review.jsonl"

        for page in ("molstar.html", "niivue.html", "future-viewer.html"):
            with review.open(review_base + "/viewer/" + page, timeout=5) as response:
                assert response.status == 200
                assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
                assert "frame-ancestors 'self'" in response.headers[
                    "Content-Security-Policy"
                ]
                assert response.headers.get("Access-Control-Allow-Origin") is None
                assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
                assert response.headers["Referrer-Policy"] == "no-referrer"
                assert "<canvas" in response.read().decode()

        # This ordinary top-level navigation is the existing new-window path;
        # SAMEORIGIN constrains ancestor documents, not direct navigation.
        with review.open(review_base + "/viewer/molstar.html", timeout=5) as response:
            assert response.status == 200

        with review.open(review_base + "/viewer/index.html", timeout=5) as response:
            assert response.status == 200
            assert response.headers["X-Frame-Options"] == "DENY"
            assert "frame-ancestors 'none'" in response.headers[
                "Content-Security-Policy"
            ]

        with review.open(review_base + "/viewer/app.js", timeout=5) as response:
            assert response.status == 200
            assert response.headers.get("X-Frame-Options") is None
            assert "frame-ancestors" not in response.headers["Content-Security-Policy"]
            assert "blob: data:" in response.headers["Content-Security-Policy"]

        with review.open(review_base + "/tasks/review.jsonl", timeout=5) as response:
            task = json.loads(response.readline())
        assert task["asset"]["path"] == "/crawl_data/model.bin"
        assert task["asset"]["mask_path"] == "/datasets/mask.bin"
        assert task["asset"]["repo_path"] == "crawl_data/model.bin"
        assert task["viewer_url"] == "/viewer/molstar.html"

        range_request = urllib.request.Request(
            review_base + "/crawl_data/model.bin", headers={"Range": "bytes=2-5"}
        )
        with review.open(range_request, timeout=5) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.read() == b"2345"

        head = urllib.request.Request(review_base + "/crawl_data/model.bin", method="HEAD")
        with review.open(head, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Length"] == "10"
            assert response.read() == b""

        for path in (
            "/%2e%2e/credentials.json",
            "/viewer%2fapp.js",
            "/viewer//app.js",
            "/viewer/./app.js",
            "/not-in-inventory.txt",
        ):
            with pytest.raises(urllib.error.HTTPError) as error:
                review.open(review_base + path, timeout=5)
            assert error.value.code == 404

        for method in ("POST", "OPTIONS"):
            request = urllib.request.Request(
                review_base + "/bundle_manifest.json", data=b"{}", method=method
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                review.open(request, timeout=5)
            assert error.value.code == 405
            assert error.value.headers.get("Access-Control-Allow-Origin") is None

        with pytest.raises(urllib.error.HTTPError) as error:
            admin.open(base + "/admin-review/sample-review/bundle_manifest.json", timeout=5)
        assert error.value.code == 404

        with admin.open(base + "/admin/", timeout=5) as response:
            page = response.read().decode()
        assert 'id="review-bundles-section"' in page
        with admin.open(base + "/static/admin.js", timeout=5) as response:
            script = response.read().decode()
        assert "/api/admin/review-bundles" in script
        assert "Central submit unavailable" in script
        assert "/api/review-bundles/open" in script
        assert "/api/review-bundles/replay" in script
        assert "Replay trajectory" in script
        assert "popup.opener = null" in script
        assert "link.href = bundle.viewer_url" not in script

        asset = root / "crawl_data/model.bin"
        original = asset.stat()
        asset.write_bytes(b"abcdefghij")
        os.utime(asset, ns=(original.st_atime_ns, original.st_mtime_ns))
        with pytest.raises(urllib.error.HTTPError) as error:
            review.open(review_base + "/crawl_data/model.bin", timeout=5)
        assert error.value.code == 409

        status, logged_out = _request_json(
            admin,
            base + "/api/logout",
            body={},
            csrf=admin_session["csrf"],
        )
        assert status == 200 and logged_out["ok"] is True
        with pytest.raises(urllib.error.HTTPError) as error:
            review.open(review_base + "/bundle_manifest.json", timeout=5)
        assert error.value.code == 401
        assert "Max-Age=0" in error.value.headers["Set-Cookie"]
        with pytest.raises(urllib.error.HTTPError) as error:
            replay.open(review_base + "/bundle_manifest.json", timeout=5)
        assert error.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        managed.close()


def test_review_login_and_session_expire_and_capability_is_server_bound(
    tmp_path: Path,
) -> None:
    root = _write_review_bundle(tmp_path / "bundle")
    config = _write_config(tmp_path / "review-bundles.json", root)
    bundle = load_review_bundles(config)["sample-review"]
    now = [100.0]
    active_parents = {"admin-session": ("admin", "admin")}

    def parent_is_active(session_id: str, username: str, role: str) -> bool:
        return active_parents.get(session_id) == (username, role)

    def create_login_url(
        managed: ManagedReviewServer, landing_path: str = "viewer/sciencevision_tasks.html"
    ) -> str:
        return managed.create_login_url(
            landing_path,
            parent_session_id="admin-session",
            parent_username="admin",
            parent_role="admin",
        )

    first = ManagedReviewServer(
        bundle,
        clock=lambda: now[0],
        login_token_ttl=60,
        session_ttl=120,
        parent_session_validator=parent_is_active,
    )
    second = ManagedReviewServer(
        bundle,
        clock=lambda: now[0],
        login_token_ttl=60,
        session_ttl=120,
        parent_session_validator=parent_is_active,
    )
    first.start()
    second.start()
    try:
        with pytest.raises(ValueError, match="not allowed"):
            create_login_url(first, "viewer/index.html")
        with pytest.raises(ValueError, match="not allowed"):
            create_login_url(first, "viewer/trajectory-replay.html?next=/viewer/index.html")
        with pytest.raises(PermissionError, match="no longer active"):
            first.create_login_url(
                parent_session_id="wrong-parent",
                parent_username="admin",
                parent_role="admin",
            )
        expired_url = create_login_url(first)
        now[0] = 161.0
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(
                first.bound_origin + urllib.parse.urlsplit(expired_url).path,
                timeout=5,
            )
        assert error.value.code == 401

        login_url = create_login_url(first)
        login_path = urllib.parse.urlsplit(login_url).path
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(second.origin + login_path, timeout=5)
        assert error.value.code == 401

        jar = http.cookiejar.CookieJar()
        review = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with review.open(login_url, timeout=5) as response:
            assert response.status == 200
        with review.open(first.origin + "/bundle_manifest.json", timeout=5) as response:
            assert response.status == 200

        now[0] = 282.0
        with pytest.raises(urllib.error.HTTPError) as error:
            review.open(first.origin + "/bundle_manifest.json", timeout=5)
        assert error.value.code == 401

        parent_bound_url = create_login_url(first)
        active_parents.clear()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(parent_bound_url, timeout=5)
        assert error.value.code == 401
        active_parents["admin-session"] = ("admin", "admin")

        replay = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        replay_url = create_login_url(first, "viewer/trajectory-replay.html")
        with replay.open(replay_url, timeout=5) as response:
            assert response.geturl() == first.origin + "/viewer/trajectory-replay.html"
    finally:
        second.close()
        first.close()


def test_review_server_rejects_large_same_metadata_tampering(tmp_path: Path) -> None:
    root = _write_review_bundle(tmp_path / "bundle")
    large_asset = _append_sparse_approved_file(
        root, "crawl_data/large-model.bin", 17 * 1024 * 1024
    )
    config = _write_config(tmp_path / "review-bundles.json", root)
    bundle = load_review_bundles(config)["sample-review"]
    managed = ManagedReviewServer(
        bundle,
        parent_session_validator=lambda session_id, username, role: (
            session_id == "admin-session" and username == "admin" and role == "admin"
        ),
    )
    managed.start()
    try:
        original = large_asset.stat()
        with large_asset.open("r+b") as handle:
            handle.seek(8 * 1024 * 1024)
            handle.write(b"X")
        os.utime(large_asset, ns=(original.st_atime_ns, original.st_mtime_ns))
        tampered = large_asset.stat()
        assert tampered.st_ino == original.st_ino
        assert tampered.st_size == original.st_size
        assert tampered.st_mtime_ns == original.st_mtime_ns

        review = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        with review.open(
            managed.create_login_url(
                parent_session_id="admin-session",
                parent_username="admin",
                parent_role="admin",
            ),
            timeout=5,
        ):
            pass
        request = urllib.request.Request(
            managed.origin + "/crawl_data/large-model.bin",
            headers={"Range": "bytes=8-15"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            review.open(request, timeout=10)
        assert error.value.code == 409
    finally:
        managed.close()
