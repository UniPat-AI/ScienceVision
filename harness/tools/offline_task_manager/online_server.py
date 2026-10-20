"""Authenticated online gateway for the manager and assigned expert workspaces."""

from __future__ import annotations

import hashlib
import hmac
import json
import ipaddress
import logging
import mimetypes
import re
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from dataclasses import dataclass
from html import escape as escape_attribute
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

from .bundle import (
    LEGACY_INTEGRITY_ALLOW_MISSING,
    LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
    build_runtime_manifest,
    require_runtime_compatibility,
)
from .contract import (
    ADMISSION_RECEIPT_PURPOSE,
    ContractError,
    require_identifier,
)
from .online_auth import (
    change_expert_password,
    create_session,
    expert_accounts_by_id,
    invite_expert_account,
    load_credentials,
    parse_session,
    password_change_required,
    password_version,
    project_online_users,
    require_private_file,
    reset_expert_password,
    validate_new_password,
    verify_password,
)
from .packager import build_expert_package, build_shared_runtime
from .results_export import render_manager_results
from .review_bundles import (
    ReviewBundle,
    load_review_bundles,
)
from .review_server import ManagedReviewServer, TRAJECTORY_REPLAY_LANDING_PATH
from .store import ManagerStore, PreflightError, utc_now

MODULE_ROOT = Path(__file__).resolve().parent
MANAGER_APP_ROOT = MODULE_ROOT / "manager_app"
EXPERT_APP_ROOT = MODULE_ROOT / "expert_app"
EXPERT_MCP_ROOT = EXPERT_APP_ROOT / "mcp"
ONLINE_APP_ROOT = MODULE_ROOT / "online_app"
MAX_BODY = 64 * 1024 * 1024
COOKIE_NAME = "sciencevision_session"
RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
VIEWER_ROOT_URL_PATTERN = re.compile(rb"(?<![A-Za-z0-9._~:/-])/viewer/")
RUNTIME_CONTENT_SECURITY_POLICY = (
    "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)
LOGGER = logging.getLogger(__name__)


class OnlineServer(ThreadingHTTPServer):
    daemon_threads = True
    store: ManagerStore
    store_lock: threading.RLock
    base_path: str
    users: dict[str, dict[str, Any]]
    credentials_path: Path
    session_secret: bytes
    secure_cookie: bool
    login_attempts: defaultdict[str, deque[float]]
    runtime_manifests: dict[str, dict[str, Any]]
    review_bundles: dict[str, ReviewBundle]
    review_servers: dict[str, ManagedReviewServer]
    public_origin: str
    # Set by serve_online. Left None so a hand-built server fails with an
    # explicit envelope instead of an AttributeError traceback.
    trajectory_root: Path | None = None
    session_lock: threading.RLock
    credential_lock: threading.RLock
    active_sessions: dict[str, dict[str, Any]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.session_lock = threading.RLock()
        self.credential_lock = threading.RLock()
        self.active_sessions = {}

    def _session_identity(self, token: str) -> str:
        return hmac.new(
            self.session_secret, token.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _purge_expired_sessions_locked(self, now: int) -> None:
        self.active_sessions = {
            session_id: record
            for session_id, record in self.active_sessions.items()
            if int(record.get("expires_at", 0)) > now
        }

    def register_session(self, token: str, session: dict[str, Any]) -> str:
        """Track a signed cookie server-side without exposing its identity."""
        session_id = self._session_identity(token)
        record = {
            "username": session["username"],
            "role": session["role"],
            "expert_id": session.get("expert_id"),
            "expires_at": session["expires_at"],
        }
        with self.session_lock:
            self._purge_expired_sessions_locked(int(time.time()))
            self.active_sessions[session_id] = record
        return session_id

    def session_identity_if_active(
        self, token: str, signed_session: dict[str, Any]
    ) -> str | None:
        session_id = self._session_identity(token)
        now = int(time.time())
        with self.session_lock:
            self._purge_expired_sessions_locked(now)
            record = self.active_sessions.get(session_id)
            if record is None:
                return None
            if (
                record.get("username") != signed_session.get("username")
                or record.get("role") != signed_session.get("role")
                or record.get("expert_id") != signed_session.get("expert_id")
                or int(record.get("expires_at", 0)) != signed_session.get("expires_at")
            ):
                self.active_sessions.pop(session_id, None)
                return None
        return session_id

    def validate_review_parent(
        self, parent_session_id: str, parent_username: str, parent_role: str
    ) -> bool:
        if parent_role != "admin":
            return False
        now = int(time.time())
        with self.session_lock:
            self._purge_expired_sessions_locked(now)
            record = self.active_sessions.get(parent_session_id)
            if record is None:
                return False
            if record.get("username") != parent_username or record.get("role") != "admin":
                return False
        user = self.users.get(parent_username)
        return bool(user and user.get("role") == "admin")

    def revoke_session_and_review(self, session_id: str) -> None:
        with self.session_lock:
            self.active_sessions.pop(session_id, None)
        for managed in tuple(getattr(self, "review_servers", {}).values()):
            managed.revoke_parent(session_id)

    def revoke_user_sessions_and_review(self, username: str) -> int:
        """Revoke every active session owned by one account and its children."""
        now = int(time.time())
        with self.session_lock:
            self._purge_expired_sessions_locked(now)
            session_ids = [
                session_id
                for session_id, record in self.active_sessions.items()
                if record.get("username") == username
            ]
            for session_id in session_ids:
                self.active_sessions.pop(session_id, None)
        for session_id in session_ids:
            for managed in tuple(getattr(self, "review_servers", {}).values()):
                managed.revoke_parent(session_id)
        return len(session_ids)


@dataclass(frozen=True)
class PublicOrigin:
    value: str
    scheme: str
    hostname: str
    port: int
    is_loopback: bool
    is_numeric_loopback: bool


def _parse_public_origin(value: str, field: str = "public_origin") -> PublicOrigin:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError(f"{field} must be an HTTP(S) origin without a path")
    hostname = parsed.hostname.lower()
    try:
        numeric_address = ipaddress.ip_address(hostname)
        is_loopback = numeric_address.is_loopback
        is_numeric_loopback = numeric_address.is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
        is_numeric_loopback = False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise ContractError(f"{field} has an invalid port") from error
    if parsed.scheme == "http" and not is_loopback:
        raise ContractError(f"non-loopback {field} must use HTTPS")
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = "" if port == default_port else f":{port}"
    return PublicOrigin(
        value=f"{parsed.scheme}://{rendered_host}{port_suffix}",
        scheme=parsed.scheme,
        hostname=hostname,
        port=port,
        is_loopback=is_loopback,
        is_numeric_loopback=is_numeric_loopback,
    )


def _canonical_public_origin(value: str) -> str:
    return _parse_public_origin(value).value


def _review_origin_for_start(
    manager_origin: PublicOrigin,
    review_public_origin: str | None,
    *,
    manager_port: int,
    review_bundle_count: int,
) -> str | None:
    if review_bundle_count < 1:
        return None
    if review_public_origin is None:
        if (
            manager_origin.scheme != "http"
            or not manager_origin.is_numeric_loopback
            or manager_origin.hostname != "127.0.0.1"
            or manager_origin.port != manager_port
        ):
            raise ContractError(
                "review_public_origin is required unless public_origin is the current "
                "http://127.0.0.1 loopback manager origin"
            )
        return None
    review_origin = _parse_public_origin(
        review_public_origin, "review_public_origin"
    )
    if review_bundle_count > 1:
        raise ContractError(
            "public review mode MVP requires one bundle; configure a per-bundle "
            "review origin/router before exposing multiple bundles"
        )
    if review_origin.value == manager_origin.value:
        raise ContractError("review_public_origin must not be the manager public_origin")
    if not manager_origin.is_loopback:
        if review_origin.scheme != "https" or review_origin.is_loopback:
            raise ContractError(
                "a public manager requires an HTTPS, non-loopback review_public_origin"
            )
        if review_origin.hostname == manager_origin.hostname:
            raise ContractError(
                "review_public_origin must use a hostname distinct from public_origin"
            )
    return review_origin.value


class Handler(BaseHTTPRequestHandler):
    server: OnlineServer

    def _headers(
        self,
        status: int,
        content_type: str,
        length: int | None = None,
        *,
        cache_control: str = "no-store",
        extra: dict[str, str] | None = None,
        content_security_policy: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Content-Security-Policy",
            content_security_policy
            or (
                "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; "
                "script-src 'self'; style-src 'self'; frame-src 'self'; object-src 'none'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
            ),
        )
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, status: int, value: Any, *, extra: dict[str, str] | None = None) -> None:
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
        self._headers(status, "application/json; charset=utf-8", len(payload), extra=extra)
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ContractError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ContractError("invalid Content-Length") from error
        if length < 1 or length > MAX_BODY:
            raise ContractError("request body is empty or too large")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ContractError("request body must be an object")
        return value

    def _relative_path(self) -> str | None:
        path = urllib.parse.urlsplit(self.path).path
        if path == self.server.base_path:
            return "/"
        prefix = self.server.base_path + "/"
        if not path.startswith(prefix):
            return None
        return "/" + path[len(prefix) :]

    def _session(self) -> dict[str, Any] | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        if not morsel:
            return None
        with self.server.credential_lock:
            session = parse_session(morsel.value, self.server.session_secret)
            if not session:
                return None
            user = self.server.users.get(str(session.get("username")))
            if not user:
                return None
            if user.get("role") != session.get("role") or user.get("expert_id") != session.get(
                "expert_id"
            ):
                return None
            if password_version(user) != session.get("password_version"):
                return None
            if password_change_required(user) != session.get("password_change_required"):
                return None
            session_id = self.server.session_identity_if_active(morsel.value, session)
            if session_id is None:
                return None
        session["_session_id"] = session_id
        return session

    def _require_session(self, role: str | None = None) -> dict[str, Any] | None:
        session = self._session()
        if not session:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            return None
        if role and session["role"] != role:
            self._json(HTTPStatus.FORBIDDEN, {"error": "role is not authorized"})
            return None
        return session

    def _origin_is_same_site(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return False
        try:
            return _canonical_public_origin(origin) == self.server.public_origin
        except ContractError:
            return False

    def _client_key(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        return forwarded or self.client_address[0]

    def _require_csrf(self, session: dict[str, Any]) -> bool:
        if not self._origin_is_same_site():
            self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request rejected"})
            return False
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or supplied != session.get("csrf"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "CSRF token is missing or invalid"})
            return False
        return True

    def _session_cookie(self, token: str, *, max_age: int = 43_200) -> str:
        cookie = (
            f"{COOKIE_NAME}={token}; Path={self.server.base_path}/; HttpOnly; "
            f"SameSite=Strict; Max-Age={max_age}"
        )
        if self.server.secure_cookie:
            cookie += "; Secure"
        return cookie

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _serve_bytes(
        self,
        payload: bytes,
        content_type: str,
        *,
        cache_control: str = "no-store",
        content_security_policy: str | None = None,
    ) -> None:
        self._headers(
            HTTPStatus.OK,
            content_type,
            len(payload),
            cache_control=cache_control,
            content_security_policy=content_security_policy,
        )
        self.wfile.write(payload)

    def _serve_file(self, path: Path, *, cache_control: str = "private, max-age=3600") -> None:
        size = path.stat().st_size
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        range_header = self.headers.get("Range")
        if not range_header:
            self._headers(
                HTTPStatus.OK,
                content_type,
                size,
                cache_control=cache_control,
                extra={"Accept-Ranges": "bytes"},
            )
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    self.wfile.write(chunk)
            return
        match = RANGE_PATTERN.fullmatch(range_header.strip())
        if not match:
            self._headers(
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                content_type,
                0,
                extra={"Content-Range": f"bytes */{size}"},
            )
            return
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last) if last else size - 1, size - 1)
        else:
            count = min(int(last or "0"), size)
            start, end = size - count, size - 1
        if start < 0 or start >= size or end < start:
            self._headers(
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                content_type,
                0,
                extra={"Content-Range": f"bytes */{size}"},
            )
            return
        length = end - start + 1
        self._headers(
            HTTPStatus.PARTIAL_CONTENT,
            content_type,
            length,
            cache_control=cache_control,
            extra={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{size}",
            },
        )
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _expert_context(self, expert_id: str) -> dict[str, Any]:
        with self.server.store_lock:
            return self.server.store.active_bundle_context(expert_id)

    def _runtime_manifest(self, context: dict[str, Any]) -> dict[str, Any]:
        viewer_root = context["viewer_root"]
        cache_key = str(viewer_root)
        cache = getattr(self.server, "runtime_manifests", None)
        if cache is None:
            cache = self.server.runtime_manifests = {}
        manifest = cache.get(cache_key)
        if manifest is None:
            manifest = build_runtime_manifest(
                viewer_root, expert_app_root=EXPERT_APP_ROOT
            )
            cache[cache_key] = manifest
        require_runtime_compatibility(
            context["manifest"].get("runtime_requirement"), manifest
        )
        return manifest

    def _locked_drafts(self, expert_id: str) -> dict[str, Any]:
        with self.server.store_lock:
            return self.server.store.load_drafts(expert_id=expert_id)

    def _bootstrap(self, expert_id: str) -> dict[str, Any]:
        context = self._expert_context(expert_id)
        return {
            "schema_version": "sciencevision.expert_bootstrap.v1",
            "manifest": context["manifest"],
            "binding": {
                "tasks_url": self.server.base_path + "/api/expert/tasks",
                "content_base": self.server.base_path + "/content/",
                "viewer_base": self.server.base_path + "/viewer/",
                "runtime_manifest": self._runtime_manifest(context),
            },
            "drafts": self._locked_drafts(expert_id),
        }

    def _admin_overview(self) -> dict[str, Any]:
        with self.server.store_lock:
            overview = self.server.store.overview()
        with self.server.credential_lock:
            account_states = {
                expert_id: {
                    "username": account["username"],
                    "status": (
                        "password_change_required"
                        if password_change_required(account)
                        else "active"
                    ),
                }
                for expert_id, account in expert_accounts_by_id(self.server.users).items()
            }
        overview["experts"] = [
            expert
            | {
                "online_account": account_states.get(
                    expert["expert_id"],
                    {"username": expert["expert_id"], "status": "not_invited"},
                )
            }
            for expert in overview["experts"]
        ]
        return overview

    def _serve_runtime_file(self, context: dict[str, Any], raw: str) -> None:
        pure = PurePosixPath(urllib.parse.unquote(raw))
        if pure.is_absolute() or not pure.parts or any(
            part in {"", ".", ".."} for part in pure.parts
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        normalized = pure.as_posix()
        runtime_manifest = self._runtime_manifest(context)
        allowed = {
            record.get("path")
            for record in runtime_manifest.get("files", [])
            if isinstance(record, dict)
        }
        if normalized not in allowed:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        root = context["viewer_root"]
        path = root.joinpath(*pure.parts)
        resolved = path.resolve()
        current = root
        linked = False
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                linked = True
                break
        if not resolved.is_relative_to(root) or linked or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path.suffix.lower() in {".html", ".js", ".mjs", ".css", ".json"}:
            replacement = (self.server.base_path + "/viewer/").encode("utf-8")
            payload = VIEWER_ROOT_URL_PATTERN.sub(
                lambda _match: replacement, path.read_bytes()
            )
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._serve_bytes(
                payload,
                content_type,
                cache_control="private, no-cache",
                content_security_policy=RUNTIME_CONTENT_SECURITY_POLICY,
            )
            return
        self._serve_file(path)

    def _review_metadata(self, bundle: ReviewBundle) -> dict[str, Any]:
        return {
            "review_id": bundle.review_id,
            "title": bundle.title,
            "task_count": bundle.task_count,
            "file_count": bundle.file_count,
            "byte_size": bundle.byte_size,
            "source_type": "checksummed_static_bundle",
            "read_only": True,
            "manager_imported": False,
            "central_submit": False,
            "drafts": "browser_local",
            "export": "browser_local",
            "trajectory_replay": TRAJECTORY_REPLAY_LANDING_PATH in bundle.landing_paths,
        }

    def _reset_expert_password(self, body: dict[str, Any]) -> None:
        if set(body) != {"expert_id"}:
            raise ContractError("account reset body must contain only expert_id")
        expert_id = require_identifier(body.get("expert_id"), "expert_id")
        credentials_path = getattr(self.server, "credentials_path", None)
        if not isinstance(credentials_path, Path):
            raise ContractError("online credentials are not configured")
        with self.server.credential_lock:
            target = expert_accounts_by_id(self.server.users).get(expert_id)
            if target is None:
                raise ContractError(
                    "password reset is available only for an existing expert account"
                )
            username = target["username"]
            try:
                reset = reset_expert_password(
                    credentials_path,
                    expert_id=expert_id,
                    expected_users=self.server.users,
                )
            except OSError:
                # An error before os.replace leaves the old credential file in
                # force, so its matching in-memory snapshot remains valid.
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "credential update failed"},
                )
                return
            updated_users = reset.users
            if not reset.directory_sync_confirmed:
                # os.replace already made the new file live. Reload it before
                # publishing credentials so this process cannot continue with
                # the old password if parent-directory durability is uncertain.
                try:
                    reloaded_users = load_credentials(credentials_path)
                except (ContractError, OSError, ValueError):
                    self.server.users = reset.users
                    revoked_sessions = self.server.revoke_user_sessions_and_review(username)
                    LOGGER.error(
                        "password reset committed but credential reload failed "
                        "username=%s revoked_sessions=%d",
                        username,
                        revoked_sessions,
                    )
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "credential update committed but could not be verified"},
                    )
                    return
                reloaded_target = expert_accounts_by_id(reloaded_users).get(expert_id)
                if (
                    reloaded_target is None
                    or reloaded_target.get("username") != username
                    or not verify_password(reset.password, reloaded_target)
                ):
                    self.server.users = reset.users
                    revoked_sessions = self.server.revoke_user_sessions_and_review(username)
                    LOGGER.error(
                        "password reset committed but credential verification failed "
                        "username=%s revoked_sessions=%d",
                        username,
                        revoked_sessions,
                    )
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "credential update committed but could not be verified"},
                    )
                    return
                updated_users = reloaded_users
            self.server.users = updated_users
            revoked_sessions = self.server.revoke_user_sessions_and_review(username)
            account = updated_users[username]
        LOGGER.info(
            "password reset issued for expert account expert_id=%s revoked_sessions=%d",
            expert_id,
            revoked_sessions,
        )
        response: dict[str, Any] = {
            "schema_version": "sciencevision.online_account_reset.v1",
            "account": {
                "username": username,
                "role": "expert",
                "expert_id": expert_id,
                "display_name": account.get("display_name") or username,
                "password_change_required": True,
            },
            "password": reset.password,
        }
        if not reset.directory_sync_confirmed:
            # The password is only returned after the committed file was
            # reloaded, verified, published, and the prior sessions revoked.
            response["durability"] = "directory_sync_unconfirmed"
        self._json(HTTPStatus.OK, response)

    def _invite_expert_account(self, body: dict[str, Any]) -> None:
        if set(body) != {"expert_id"}:
            raise ContractError("account invite body must contain only expert_id")
        expert_id = require_identifier(body.get("expert_id"), "expert_id")
        credentials_path = getattr(self.server, "credentials_path", None)
        if not isinstance(credentials_path, Path):
            raise ContractError("online credentials are not configured")

        with self.server.store_lock:
            expert = next(
                (
                    record
                    for record in self.server.store.overview()["experts"]
                    if record.get("expert_id") == expert_id and record.get("active")
                ),
                None,
            )
            if expert is None:
                raise ContractError("expert must exist in the active roster before invitation")
            with self.server.credential_lock:
                if expert_id in expert_accounts_by_id(self.server.users):
                    raise ContractError("expert already has an online account")
                try:
                    invite = invite_expert_account(
                        credentials_path,
                        expert_id=expert_id,
                        display_name=expert["name"],
                        expected_users=self.server.users,
                    )
                except OSError:
                    self._json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "credential update failed"},
                    )
                    return
                # A successful return means os.replace committed the complete
                # credential document. Publish the matching snapshot before
                # exposing the one-time password to the administrator.
                self.server.users = invite.users
                account = {
                    "username": expert_id,
                    "role": "expert",
                    "expert_id": expert_id,
                    "display_name": expert["name"],
                    "password_change_required": True,
                }

        LOGGER.info("online account invited for active expert expert_id=%s", expert_id)
        response: dict[str, Any] = {
            "schema_version": "sciencevision.online_account_invite.v1",
            "account": account,
            "password": invite.password,
        }
        if not invite.directory_sync_confirmed:
            response["durability"] = "directory_sync_unconfirmed"
        self._json(HTTPStatus.CREATED, response)

    def _render_app(
        self, source: Path, *, role: str, session: dict[str, Any] | None = None
    ) -> bytes:
        html = source.read_text(encoding="utf-8")
        base = self.server.base_path
        metas = [
            f'<meta name="sciencevision-online-base" content="{base}">',
            f'<meta name="sciencevision-online-role" content="{role}">',
        ]
        if role == "expert":
            # The trajectory drawer reads both of these to build its authenticated
            # submit transport; without them Submit stays disabled by design.
            metas.append(
                '<meta name="sciencevision-trajectory-submit-endpoint" '
                f'content="{base}/api/expert/trajectory">'
            )
            csrf = (session or {}).get("csrf")
            if csrf:
                metas.append(
                    '<meta name="sciencevision-csrf-token" '
                    f'content="{escape_attribute(csrf)}">'
                )
        html = html.replace("</head>", "\n".join(metas) + "\n</head>")
        html = html.replace('src="/i18n.js"', f'src="{base}/static/i18n.js"')
        if role == "expert":
            html = html.replace('href="/styles.css"', f'href="{base}/static/expert.css"')
            html = html.replace('src="/expert-app.js"', f'src="{base}/static/expert.js"')
            html = html.replace(
                'src="/viewer/shared/task-screenshot.js"',
                f'src="{base}/viewer/shared/task-screenshot.js"',
            )
            html = html.replace('href="/mcp/', f'href="{base}/static/mcp/')
            html = html.replace('src="/mcp/', f'src="{base}/static/mcp/')
        else:
            html = html.replace('href="/styles.css"', f'href="{base}/static/admin.css"')
            html = html.replace('src="/app.js"', f'src="{base}/static/admin.js"')
        return html.encode("utf-8")

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except (ContractError, PreflightError, FileNotFoundError, json.JSONDecodeError) as error:
            value: dict[str, Any] = {"error": str(error)}
            if isinstance(error, PreflightError):
                value["errors"] = error.errors
            self._json(HTTPStatus.CONFLICT, value)

    def _do_GET(self) -> None:
        relative = self._relative_path()
        if relative is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if relative == "/login/" or relative == "/login":
            self._serve_file(ONLINE_APP_ROOT / "login.html", cache_control="no-store")
            return
        if relative == "/static/i18n.js":
            # The language layer is shared by the login page and both apps, so
            # it is served before authentication like the login assets.
            self._serve_file(ONLINE_APP_ROOT / "i18n.js", cache_control="no-cache")
            return
        if relative == "/static/login.js":
            self._serve_file(ONLINE_APP_ROOT / "login.js", cache_control="no-cache")
            return
        if relative == "/static/online.css":
            self._serve_file(ONLINE_APP_ROOT / "online.css", cache_control="no-cache")
            return
        session = self._session()
        if relative == "/api/session":
            if not session:
                self._json(HTTPStatus.UNAUTHORIZED, {"authenticated": False})
            else:
                self._json(
                    HTTPStatus.OK,
                    {
                        "authenticated": True,
                        "username": session["username"],
                        "display_name": session["display_name"],
                        "role": session["role"],
                        "expert_id": session.get("expert_id"),
                        "password_change_required": session["password_change_required"],
                        "csrf": session["csrf"],
                        "base_path": self.server.base_path,
                    },
                )
            return
        if relative == "/api/admin/review-bundles":
            if not session:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            bundles = getattr(self.server, "review_bundles", {})
            self._json(
                HTTPStatus.OK,
                {
                    "schema_version": "sciencevision.online_review_bundle_index.v1",
                    "bundles": [
                        self._review_metadata(bundle)
                        for bundle in sorted(
                            bundles.values(), key=lambda item: item.review_id
                        )
                    ],
                },
            )
            return
        if relative == "/api/admin/accounts":
            if not session:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            with self.server.credential_lock:
                accounts = project_online_users(self.server.users)
            self._json(
                HTTPStatus.OK,
                {
                    "schema_version": "sciencevision.online_account_index.v1",
                    "accounts": accounts,
                },
            )
            return
        if relative == "/api/admin/audit-events":
            if not session:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query, keep_blank_values=True
            )
            allowed = {
                "limit",
                "offset",
                "batch_id",
                "assignment_id",
                "actor_id",
                "event_type",
            }
            unknown = sorted(set(query) - allowed)
            if unknown:
                raise ContractError("unsupported audit filter(s): " + ", ".join(unknown))
            if any(len(values) != 1 for values in query.values()):
                raise ContractError("audit filters may be supplied only once")
            try:
                limit = int(query.get("limit", ["50"])[0])
                offset = int(query.get("offset", ["0"])[0])
            except ValueError as error:
                raise ContractError("audit limit and offset must be integers") from error
            filters = {
                key: query[key][0]
                for key in ("batch_id", "assignment_id", "actor_id", "event_type")
                if key in query
            }
            with self.server.store_lock:
                page = self.server.store.audit_events(limit=limit, offset=offset, **filters)
            self._json(HTTPStatus.OK, page)
            return
        if not session:
            self._redirect(self.server.base_path + "/login/")
            return
        if session["password_change_required"]:
            self._json(HTTPStatus.FORBIDDEN, {"error": "password change required"})
            return
        if relative == "/":
            destination = "/admin/" if session["role"] == "admin" else "/expert/"
            self._redirect(self.server.base_path + destination)
            return
        if relative in {"/expert", "/expert/"}:
            if session["role"] != "expert":
                self._redirect(self.server.base_path + "/admin/")
                return
            self._serve_bytes(
                self._render_app(
                    EXPERT_APP_ROOT / "index.html", role="expert", session=session
                ),
                "text/html; charset=utf-8",
            )
            return
        if relative in {"/admin", "/admin/"}:
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            self._serve_bytes(
                self._render_app(MANAGER_APP_ROOT / "index.html", role="admin"),
                "text/html; charset=utf-8",
            )
            return
        static_files = {
            "/static/expert.js": EXPERT_APP_ROOT / "expert-app.js",
            "/static/expert.css": EXPERT_APP_ROOT / "styles.css",
            "/static/admin.js": MANAGER_APP_ROOT / "app.js",
            "/static/admin.css": MANAGER_APP_ROOT / "styles.css",
            "/static/mcp/task-mcp-controls.js": EXPERT_MCP_ROOT / "task-mcp-controls.js",
            "/static/mcp/task-mcp-controls.css": EXPERT_MCP_ROOT / "task-mcp-controls.css",
            "/static/mcp/task-trajectory.js": EXPERT_MCP_ROOT / "task-trajectory.js",
            "/static/mcp/task-trajectory.css": EXPERT_MCP_ROOT / "task-trajectory.css",
        }
        if relative in static_files:
            self._serve_file(static_files[relative], cache_control="no-cache")
            return
        if relative == "/api/expert/bootstrap":
            if session["role"] != "expert":
                self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                return
            with self.server.store_lock:
                payload = self._bootstrap(session["expert_id"])
            self._json(HTTPStatus.OK, payload)
            return
        if relative == "/api/expert/drafts":
            if session["role"] != "expert":
                self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                return
            with self.server.store_lock:
                drafts = self.server.store.load_drafts(expert_id=session["expert_id"])
            self._json(
                HTTPStatus.OK,
                {"schema_version": "sciencevision.expert_drafts.v1", "drafts": drafts},
            )
            return
        if relative == "/api/expert/manifest":
            if session["role"] != "expert":
                self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                return
            self._json(HTTPStatus.OK, self._expert_context(session["expert_id"])["manifest"])
            return
        if relative == "/api/expert/tasks":
            if session["role"] != "expert":
                self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                return
            context = self._expert_context(session["expert_id"])
            payload = context["tasks_jsonl"].encode("utf-8")
            self._serve_bytes(payload, "application/x-ndjson; charset=utf-8")
            return
        if relative == "/viewer" or relative.startswith("/viewer/"):
            if session["role"] != "expert":
                self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                return
            raw = "index.html" if relative == "/viewer" else relative[len("/viewer/") :]
            raw = raw or "index.html"
            self._serve_runtime_file(self._expert_context(session["expert_id"]), raw)
            return
        if relative.startswith("/content/"):
            if session["role"] != "expert":
                self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                return
            raw = urllib.parse.unquote(relative[len("/content/") :])
            pure = PurePosixPath(raw)
            if pure.is_absolute() or not pure.parts or any(
                part in {"", ".", ".."} for part in pure.parts
            ):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            normalized = pure.as_posix()
            context = self._expert_context(session["expert_id"])
            if normalized not in context["allowed_assets"]:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            path = context["asset_root"].joinpath(*pure.parts)
            resolved = path.resolve()
            current = context["asset_root"]
            linked = False
            for part in pure.parts:
                current = current / part
                if current.is_symlink():
                    linked = True
                    break
            if (
                not resolved.is_relative_to(context["asset_root"])
                or linked
                or not path.is_file()
            ):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._serve_file(path)
            return
        if relative == "/api/admin/overview":
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            self._json(HTTPStatus.OK, self._admin_overview())
            return
        if relative == "/api/admin/results/export":
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            format_name = query.get("format", ["json"])[0]
            if format_name not in {"json", "csv"}:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "format must be json or csv"})
                return
            with self.server.store_lock:
                rows = self.server.store.result_rows()
            payload, content_type, filename = render_manager_results(
                rows, format_name=format_name, exported_at=utc_now()
            )
            self._headers(
                HTTPStatus.OK,
                content_type,
                len(payload),
                extra={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            self.wfile.write(payload)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        relative = self._relative_path()
        if relative is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            if relative == "/api/login":
                if not self._origin_is_same_site():
                    self._json(HTTPStatus.FORBIDDEN, {"error": "cross-origin request rejected"})
                    return
                now = time.monotonic()
                attempts = self.server.login_attempts[self._client_key()]
                while attempts and now - attempts[0] > 300:
                    attempts.popleft()
                if len(attempts) >= 10:
                    self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "try again later"})
                    return
                body = self._body()
                if set(body) != {"username", "password"}:
                    raise ContractError("login body must contain only username and password")
                username = str(body.get("username", ""))
                password = str(body.get("password", ""))
                if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 256:
                    attempts.append(now)
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid username or password"})
                    return
                with self.server.credential_lock:
                    user = self.server.users.get(username)
                    if not user or not verify_password(password, user):
                        attempts.append(now)
                        self._json(
                            HTTPStatus.UNAUTHORIZED,
                            {"error": "invalid username or password"},
                        )
                        return
                    attempts.clear()
                    token, session = create_session(user, self.server.session_secret)
                    self.server.register_session(token, session)
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "role": session["role"],
                        "password_change_required": session["password_change_required"],
                    },
                    extra={"Set-Cookie": self._session_cookie(token)},
                )
                return
            session = self._require_session()
            if not session or not self._require_csrf(session):
                return
            if relative == "/api/logout":
                self.server.revoke_session_and_review(session["_session_id"])
                self._json(
                    HTTPStatus.OK,
                    {"ok": True},
                    extra={"Set-Cookie": self._session_cookie("", max_age=0)},
                )
                return
            if relative == "/api/password-change":
                if session["role"] != "expert" or not session["password_change_required"]:
                    self._json(
                        HTTPStatus.FORBIDDEN,
                        {"error": "password activation is not authorized"},
                    )
                    return
                body = self._body()
                if set(body) != {"new_password", "confirm_password"}:
                    raise ContractError(
                        "password change body must contain only new_password and confirm_password"
                    )
                new_password = validate_new_password(body.get("new_password"))
                if body.get("confirm_password") != new_password:
                    raise ContractError("new password and confirmation do not match")
                with self.server.credential_lock:
                    current = self.server.users.get(session["username"])
                    if (
                        not current
                        or password_version(current) != session["password_version"]
                        or not password_change_required(current)
                    ):
                        self._json(
                            HTTPStatus.UNAUTHORIZED,
                            {"error": "authentication required"},
                        )
                        return
                    try:
                        changed = change_expert_password(
                            self.server.credentials_path,
                            username=session["username"],
                            new_password=new_password,
                            expected_users=self.server.users,
                        )
                    except OSError:
                        self._json(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            {"error": "credential update failed"},
                        )
                        return
                    self.server.users = changed.users
                    self.server.revoke_user_sessions_and_review(session["username"])
                    token, replacement_session = create_session(
                        changed.users[session["username"]], self.server.session_secret
                    )
                    self.server.register_session(token, replacement_session)
                response: dict[str, Any] = {
                    "ok": True,
                    "role": replacement_session["role"],
                    "password_change_required": False,
                }
                if not changed.directory_sync_confirmed:
                    response["durability"] = "directory_sync_unconfirmed"
                self._json(
                    HTTPStatus.OK,
                    response,
                    extra={"Set-Cookie": self._session_cookie(token)},
                )
                return
            if session["password_change_required"]:
                self._json(HTTPStatus.FORBIDDEN, {"error": "password change required"})
                return
            body = self._body()
            if relative == "/api/expert/assignments/open":
                if session["role"] != "expert":
                    self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                    return
                if set(body) != {"assignment_id"}:
                    raise ContractError("assignment open body must contain assignment_id")
                with self.server.store_lock:
                    assignment = self.server.store.start_online_assignment(
                        expert_id=session["expert_id"],
                        assignment_id=body["assignment_id"],
                    )
                self._json(
                    HTTPStatus.OK,
                    {
                        "schema_version": "sciencevision.assignment_open.v1",
                        "assignment_id": assignment["assignment_id"],
                        "status": assignment["status"],
                        "unchanged": assignment["unchanged"],
                    },
                )
                return
            if relative == "/api/expert/drafts":
                if session["role"] != "expert":
                    self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                    return
                assignment_id = str(body.get("assignment_id") or "")
                with self.server.store_lock:
                    saved = self.server.store.save_draft(
                        expert_id=session["expert_id"],
                        assignment_id=assignment_id,
                        payload=body,
                    )
                self._json(
                    HTTPStatus.OK,
                    {"schema_version": "sciencevision.expert_drafts.v1", "draft": saved},
                )
                return
            if relative == "/api/expert/submit":
                if session["role"] != "expert":
                    self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                    return
                submission = body.get("submission")
                if not isinstance(submission, dict):
                    raise ContractError("submission must be an object")
                if submission.get("expert_id") != session["expert_id"]:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "submission expert mismatch"})
                    return
                with self.server.store_lock:
                    result = self.server.store.import_submission(
                        submission,
                        actor_id=session["expert_id"],
                        actor_role="expert",
                    )
                status = HTTPStatus.OK if not result["rejected"] else HTTPStatus.BAD_REQUEST
                self._json(status, result)
                return
            if relative == "/api/expert/trajectory":
                if session["role"] != "expert":
                    self._json(HTTPStatus.FORBIDDEN, {"error": "expert role required"})
                    return
                artifact_root = self.server.trajectory_root
                if artifact_root is None:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "trajectory storage is not configured"},
                    )
                    return
                supplied_key = (self.headers.get("Idempotency-Key") or "").strip()
                identity = body.get("identity")
                if not isinstance(identity, dict):
                    raise ContractError("trajectory identity must be an object")
                if supplied_key and supplied_key != str(
                    identity.get("idempotency_key") or ""
                ):
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Idempotency-Key header disagrees with the payload"},
                    )
                    return
                with self.server.store_lock:
                    acknowledgment = self.server.store.import_trajectory(
                        expert_id=session["expert_id"],
                        payload=body.get("payload"),
                        identity=identity,
                        artifact_root=artifact_root,
                        received_at=utc_now(),
                    )
                self._json(HTTPStatus.OK, acknowledgment)
                return
            if session["role"] != "admin":
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin role required"})
                return
            if relative == "/api/admin/accounts/reset":
                self._reset_expert_password(body)
                return
            if relative == "/api/admin/accounts/invite":
                self._invite_expert_account(body)
                return
            if relative == "/api/admin/assignments/review":
                allowed_fields = {"assignment_id", "status", "reason"}
                if not {"assignment_id", "status"}.issubset(body) or set(body) - allowed_fields:
                    raise ContractError(
                        "assignment review body must contain assignment_id, status, "
                        "and optional reason"
                    )
                reason = body.get("reason")
                if reason is not None and not isinstance(reason, str):
                    raise ContractError("review reason must be text")
                with self.server.store_lock:
                    assignment = self.server.store.review_assignment(
                        assignment_id=body["assignment_id"],
                        target_status=body["status"],
                        actor_id=session["username"],
                        reason=reason,
                    )
                self._json(
                    HTTPStatus.OK,
                    {
                        "schema_version": "sciencevision.assignment_review.v1",
                        "assignment_id": assignment["assignment_id"],
                        "status": assignment["status"],
                    },
                )
                return
            if relative == "/api/admin/review-bundles/open":
                review_id = require_identifier(body.get("review_id"), "review_id")
                managed = getattr(self.server, "review_servers", {}).get(review_id)
                if managed is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "review bundle not found"})
                    return
                try:
                    viewer_url = managed.create_login_url(
                        parent_session_id=session["_session_id"],
                        parent_username=session["username"],
                        parent_role=session["role"],
                    )
                except PermissionError:
                    self._json(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "administrator session is no longer active"},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "schema_version": "sciencevision.online_review_open.v1",
                        "review_id": review_id,
                        "viewer_url": viewer_url,
                        "expires_in": managed.server.login_token_ttl,
                    },
                )
                return
            if relative == "/api/admin/review-bundles/replay":
                review_id = require_identifier(body.get("review_id"), "review_id")
                managed = getattr(self.server, "review_servers", {}).get(review_id)
                if managed is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "review bundle not found"})
                    return
                if TRAJECTORY_REPLAY_LANDING_PATH not in managed.server.bundle.landing_paths:
                    self._json(
                        HTTPStatus.CONFLICT,
                        {"error": "trajectory replay is unavailable for this review bundle"},
                    )
                    return
                try:
                    replay_url = managed.create_login_url(
                        TRAJECTORY_REPLAY_LANDING_PATH,
                        parent_session_id=session["_session_id"],
                        parent_username=session["username"],
                        parent_role=session["role"],
                    )
                except PermissionError:
                    self._json(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "administrator session is no longer active"},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "schema_version": "sciencevision.online_review_replay.v1",
                        "review_id": review_id,
                        "replay_url": replay_url,
                        "expires_in": managed.server.login_token_ttl,
                    },
                )
                return
            with self.server.store_lock:
                if relative == "/api/admin/experts/import":
                    result = self.server.store.import_experts_csv(str(body.get("csv", "")))
                elif relative == "/api/admin/batches/import":
                    result = self.server.store.import_batch(
                        batch_id=str(body.get("batch_id", "")),
                        tasks_jsonl=str(body.get("tasks_jsonl", "")),
                        asset_root=str(body.get("asset_root", "")),
                        admission_receipt=body.get("admission_receipt") or None,
                        admission_purpose=str(
                            body.get("admission_purpose") or ADMISSION_RECEIPT_PURPOSE
                        ),
                        viewer_root=body.get("viewer_root") or None,
                        title=str(body.get("title", "")),
                    )
                elif relative == "/api/admin/assignments/auto":
                    result = self.server.store.auto_assign(
                        batch_id=str(body.get("batch_id", "")),
                        actor_id=session["username"],
                        actor_role="admin",
                    )
                elif relative == "/api/admin/assignments/explicit":
                    result = self.server.store.assign_explicit(
                        batch_id=str(body.get("batch_id", "")),
                        task_id=str(body.get("task_id", "")),
                        expert_id=str(body.get("expert_id", "")),
                        actor_id=session["username"],
                        actor_role="admin",
                    )
                elif relative == "/api/admin/packages/build":
                    result = build_expert_package(
                        self.server.store,
                        batch_id=str(body.get("batch_id", "")),
                        expert_id=str(body.get("expert_id", "")),
                        output=str(body.get("output", "")),
                        mode=str(body.get("mode") or "standalone"),
                    )
                elif relative in {
                    "/api/admin/bundles/publish",
                    "/api/admin/publish-batch",
                }:
                    result = self.server.store.publish_batch(
                        batch_id=str(body.get("batch_id", ""))
                    )
                elif relative in {
                    "/api/admin/bundles/import-legacy",
                    "/api/admin/import-legacy-package",
                }:
                    activate = body.get("activate", True)
                    if not isinstance(activate, bool):
                        raise ContractError("activate must be boolean")
                    allow_missing = body.get(
                        "allow_historical_missing_checksums", False
                    )
                    if not isinstance(allow_missing, bool):
                        raise ContractError(
                            "allow_historical_missing_checksums must be boolean"
                        )
                    result = self.server.store.import_legacy_package(
                        str(body.get("package_root", "")),
                        activate=activate,
                        integrity_policy=(
                            LEGACY_INTEGRITY_ALLOW_MISSING
                            if allow_missing
                            else LEGACY_INTEGRITY_REQUIRE_CHECKSUMS
                        ),
                    )
                elif relative == "/api/admin/runtime/build":
                    result = build_shared_runtime(
                        viewer_root=str(body.get("viewer_root", "")),
                        output=str(body.get("output", "")),
                    )
                elif relative == "/api/admin/results/import":
                    submission = body.get("submission")
                    if not isinstance(submission, dict):
                        raise ContractError("submission must be an object")
                    result = self.server.store.import_submission(
                        submission,
                        actor_id=session["username"],
                        actor_role="admin",
                    )
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
                    return
            self._json(HTTPStatus.OK, result)
        except (ContractError, PreflightError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as error:
            value: dict[str, Any] = {"error": str(error)}
            if isinstance(error, PreflightError):
                value["errors"] = error.errors
            self._json(HTTPStatus.BAD_REQUEST, value)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {format % args}")


def serve_online(
    db_path: Path,
    *,
    credentials_path: Path,
    session_secret_path: Path,
    batch_id: str | None = None,
    port: int = 4174,
    base_path: str = "/sv-annotate",
    secure_cookie: bool = True,
    review_bundles_path: Path | None = None,
    public_origin: str | None = None,
    review_public_origin: str | None = None,
    trajectory_root: Path | None = None,
) -> int:
    if not 1 <= port <= 65535:
        raise ContractError("online port must be between 1 and 65535")
    if not base_path.startswith("/") or base_path == "/" or base_path.endswith("/"):
        raise ContractError("base_path must look like /sv-annotate without a trailing slash")
    # Trajectory artifacts are large, so they live beside the database rather
    # than inside it. Under systemd this directory must fall within ReadWritePaths.
    artifact_root = (
        Path(trajectory_root).expanduser()
        if trajectory_root is not None
        else Path(db_path).expanduser().parent / "trajectories"
    ).resolve()
    manager_origin = _parse_public_origin(
        public_origin or f"http://127.0.0.1:{port}"
    )
    if not manager_origin.is_loopback and not secure_cookie:
        raise ContractError("a public manager requires secure cookies")
    review_bundles = load_review_bundles(review_bundles_path)
    resolved_review_origin = _review_origin_for_start(
        manager_origin,
        review_public_origin,
        manager_port=port,
        review_bundle_count=len(review_bundles),
    )
    review_servers: dict[str, ManagedReviewServer] = {}
    store: ManagerStore | None = None
    server: OnlineServer | None = None
    try:
        store = ManagerStore(db_path)
        users = load_credentials(credentials_path)
        secret = require_private_file(session_secret_path, "session secret").read_bytes()
        if len(secret) < 32:
            raise ContractError("session secret must contain at least 32 bytes")
        if batch_id is not None:
            store.publish_batch(batch_id=batch_id)
        server = OnlineServer(("127.0.0.1", port), Handler)
        server.store = store
        server.store_lock = threading.RLock()
        server.base_path = base_path
        server.users = users
        server.credentials_path = credentials_path
        server.session_secret = secret
        server.secure_cookie = secure_cookie
        server.login_attempts = defaultdict(deque)
        server.runtime_manifests = {}
        server.review_bundles = review_bundles
        server.review_servers = {}
        server.public_origin = manager_origin.value
        for review_id, bundle in review_bundles.items():
            managed = ManagedReviewServer(
                bundle,
                public_origin=resolved_review_origin,
                parent_session_validator=server.validate_review_parent,
            )
            managed.start()
            review_servers[review_id] = managed
        server.review_servers = review_servers
        artifact_root.mkdir(parents=True, exist_ok=True)
        server.trajectory_root = artifact_root
    except Exception:
        if server is not None:
            server.server_close()
        if store is not None:
            store.close()
        for managed in reversed(list(review_servers.values())):
            managed.close()
        raise
    print(f"ScienceVision online task manager: http://127.0.0.1:{port}{base_path}/", flush=True)
    try:
        assert server is not None
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        assert store is not None
        store.close()
        for managed in reversed(list(review_servers.values())):
            managed.close()
    return 0
