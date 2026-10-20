"""Isolated loopback server for one verified static review bundle."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import stat
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from typing import Callable

from .review_bundles import ApprovedFile, ReviewBundle

RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
LOGIN_TOKEN_TTL_SECONDS = 60
REVIEW_SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
DEFAULT_LANDING_PATH = "viewer/sciencevision_tasks.html"
TRAJECTORY_REPLAY_LANDING_PATH = "viewer/trajectory-replay.html"
CONTENT_SECURITY_POLICY = (
    "default-src 'self' blob: data:; connect-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; object-src 'none'; "
    "base-uri 'self'; form-action 'none'"
)
FRAME_ANCESTORS_DENY = "'none'"
FRAME_ANCESTORS_SAME_ORIGIN = "'self'"
ParentSessionValidator = Callable[[str, str, str], bool]


class BundleDriftError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewLoginCapability:
    expires_at: float
    landing_path: str
    parent_session_id: str
    parent_username: str
    parent_role: str


@dataclass(frozen=True)
class ReviewSession:
    expires_at: float
    parent_session_id: str
    parent_username: str
    parent_role: str


class ReviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    bundle: ReviewBundle
    auth_lock: threading.Lock
    login_tokens: dict[str, ReviewLoginCapability]
    sessions: dict[str, ReviewSession]
    cookie_name: str
    clock: Callable[[], float]
    login_token_ttl: int
    session_ttl: int
    parent_session_validator: ParentSessionValidator
    secure_cookie: bool

    def _purge_expired(self, now: float) -> None:
        self.login_tokens = {
            token: capability
            for token, capability in self.login_tokens.items()
            if capability.expires_at > now
        }
        self.sessions = {
            token: session
            for token, session in self.sessions.items()
            if session.expires_at > now
        }

    def _parent_is_active(
        self, parent_session_id: str, parent_username: str, parent_role: str
    ) -> bool:
        if parent_role != "admin":
            return False
        try:
            return bool(
                self.parent_session_validator(
                    parent_session_id, parent_username, parent_role
                )
            )
        except Exception:
            return False

    def create_login_token(
        self,
        *,
        parent_session_id: str,
        parent_username: str,
        parent_role: str,
        landing_path: str = DEFAULT_LANDING_PATH,
    ) -> str:
        if landing_path not in self.bundle.landing_paths:
            raise ValueError("review landing path is not allowed for this bundle")
        if (
            not isinstance(parent_session_id, str)
            or not parent_session_id
            or not isinstance(parent_username, str)
            or not parent_username
            or parent_role != "admin"
        ):
            raise PermissionError("an active administrator session is required")
        if not self._parent_is_active(
            parent_session_id, parent_username, parent_role
        ):
            raise PermissionError("administrator session is no longer active")
        now = self.clock()
        with self.auth_lock:
            self._purge_expired(now)
            while True:
                token = secrets.token_urlsafe(32)
                if token not in self.login_tokens:
                    self.login_tokens[token] = ReviewLoginCapability(
                        expires_at=now + self.login_token_ttl,
                        landing_path=landing_path,
                        parent_session_id=parent_session_id,
                        parent_username=parent_username,
                        parent_role=parent_role,
                    )
                    return token

    def consume_login_token(self, token: str) -> tuple[str, str] | None:
        now = self.clock()
        with self.auth_lock:
            self._purge_expired(now)
            capability = self.login_tokens.pop(token, None)
            if capability is None:
                return None
            if (
                capability.expires_at <= now
                or capability.landing_path not in self.bundle.landing_paths
                or not self._parent_is_active(
                    capability.parent_session_id,
                    capability.parent_username,
                    capability.parent_role,
                )
            ):
                return None
            while True:
                session = secrets.token_urlsafe(32)
                if session not in self.sessions:
                    self.sessions[session] = ReviewSession(
                        expires_at=now + self.session_ttl,
                        parent_session_id=capability.parent_session_id,
                        parent_username=capability.parent_username,
                        parent_role=capability.parent_role,
                    )
                    return session, capability.landing_path

    def validate_session(self, token: str) -> bool:
        now = self.clock()
        with self.auth_lock:
            self._purge_expired(now)
            session = self.sessions.get(token)
            if session is None:
                return False
            if not self._parent_is_active(
                session.parent_session_id,
                session.parent_username,
                session.parent_role,
            ):
                self.sessions.pop(token, None)
                return False
            return True

    def revoke_parent(self, parent_session_id: str) -> None:
        """Delete every capability and review cookie bound to one manager session."""
        with self.auth_lock:
            self.login_tokens = {
                token: capability
                for token, capability in self.login_tokens.items()
                if capability.parent_session_id != parent_session_id
            }
            self.sessions = {
                token: session
                for token, session in self.sessions.items()
                if session.parent_session_id != parent_session_id
            }


def _path_has_symlink(bundle: ReviewBundle, pure: PurePosixPath) -> bool:
    current = bundle.root
    for part in pure.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return True
    return False


def _matches_approved_file(current: os.stat_result, record: ApprovedFile) -> bool:
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_dev == record.device
        and current.st_ino == record.inode
        and current.st_size == record.byte_size
        and current.st_mtime_ns == record.modified_ns
    )


def _verify_open_approved(descriptor: int, record: ApprovedFile) -> None:
    """Verify an already-open bundle file and leave it ready for transmission."""
    before = os.fstat(descriptor)
    if not _matches_approved_file(before, record):
        raise BundleDriftError("review bundle file changed after startup")

    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)

    # Re-stat the same fd after hashing so a write during the read cannot be
    # hidden by a stale path stat. The caller retains this fd through transfer.
    after = os.fstat(descriptor)
    if not _matches_approved_file(after, record):
        raise BundleDriftError("review bundle file changed after startup")
    if digest.hexdigest() != record.sha256:
        raise BundleDriftError("review bundle file digest changed after startup")
    os.lseek(descriptor, 0, os.SEEK_SET)


def _open_approved(bundle: ReviewBundle, record: ApprovedFile) -> int:
    root_stat = os.stat(bundle.root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_dev != bundle.root_device
        or root_stat.st_ino != bundle.root_inode
    ):
        raise BundleDriftError("review bundle root changed after startup")
    pure = PurePosixPath(record.path)
    if _path_has_symlink(bundle, pure):
        raise BundleDriftError("review bundle path changed after startup")
    path = bundle.root.joinpath(*pure.parts)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, OSError) as error:
        raise BundleDriftError("review bundle file changed after startup") from error
    try:
        _verify_open_approved(descriptor, record)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewHTTPServer

    def _headers(
        self,
        status_code: int,
        content_type: str,
        content_length: int,
        *,
        extra: dict[str, str] | None = None,
        frame_ancestors: str | None = None,
    ) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if frame_ancestors is not None:
            self.send_header(
                "X-Frame-Options",
                "SAMEORIGIN"
                if frame_ancestors == FRAME_ANCESTORS_SAME_ORIGIN
                else "DENY",
            )
            self.send_header(
                "Content-Security-Policy",
                f"{CONTENT_SECURITY_POLICY}; frame-ancestors {frame_ancestors}",
            )
        else:
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _empty_response(
        self,
        status_code: int,
        *,
        extra: dict[str, str] | None = None,
    ) -> None:
        self._headers(status_code, "text/plain; charset=utf-8", 0, extra=extra)

    def _authorized(self) -> bool:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(self.server.cookie_name)
        if morsel and self.server.validate_session(morsel.value):
            return True
        extra = None
        if morsel:
            expired_cookie = (
                f"{self.server.cookie_name}=; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=0"
            )
            if self.server.secure_cookie:
                expired_cookie += "; Secure"
            extra = {"Set-Cookie": expired_cookie}
        self._empty_response(HTTPStatus.UNAUTHORIZED, extra=extra)
        return False

    def _login(self, token: str) -> None:
        if not LOGIN_TOKEN_PATTERN.fullmatch(token):
            self._empty_response(HTTPStatus.UNAUTHORIZED)
            return
        login = self.server.consume_login_token(token)
        if login is None:
            self._empty_response(HTTPStatus.UNAUTHORIZED)
            return
        session, landing_path = login
        if landing_path == DEFAULT_LANDING_PATH:
            query = urllib.parse.urlencode({"manifest": "/bundle_manifest.json"})
            location = f"/{landing_path}?{query}"
        else:
            location = f"/{landing_path}"
        cookie = (
            f"{self.server.cookie_name}={session}; Path=/; HttpOnly; "
            f"SameSite=Strict; Max-Age={self.server.session_ttl}"
        )
        if self.server.secure_cookie:
            cookie += "; Secure"
        self._empty_response(
            HTTPStatus.SEE_OTHER,
            extra={"Location": location, "Set-Cookie": cookie},
        )

    def _relative_path(self) -> str | None:
        raw_path = urllib.parse.urlsplit(self.path).path
        if (
            "\\" in raw_path
            or "\x00" in raw_path
            or raw_path.startswith("//")
            or "//" in raw_path[1:]
        ):
            return None
        if re.search(r"%2f|%5c|%00", raw_path, re.IGNORECASE):
            return None
        try:
            decoded = urllib.parse.unquote(raw_path, errors="strict")
        except UnicodeDecodeError:
            return None
        if not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded:
            return None
        parts = decoded[1:].split("/")
        if not parts or any(part in {"", ".", ".."} for part in parts):
            return None
        pure = PurePosixPath(*parts)
        return pure.as_posix()

    def _frame_ancestors_for(
        self, record: ApprovedFile, content_type: str
    ) -> str | None:
        if content_type != "text/html":
            return None
        if record.path in self.server.bundle.embedded_viewer_paths:
            return FRAME_ANCESTORS_SAME_ORIGIN
        return FRAME_ANCESTORS_DENY

    def _serve(self, *, head_only: bool) -> None:
        relative = self._relative_path()
        record = self.server.bundle.files.get(relative or "")
        if record is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            descriptor = _open_approved(self.server.bundle, record)
        except BundleDriftError as error:
            self.send_error(HTTPStatus.CONFLICT, str(error))
            return
        try:
            content_type = mimetypes.guess_type(record.path)[0] or "application/octet-stream"
            frame_ancestors = self._frame_ancestors_for(record, content_type)
            start = 0
            end = record.byte_size - 1
            status_code = HTTPStatus.OK
            extra: dict[str, str] = {}
            range_header = self.headers.get("Range")
            if range_header:
                match = RANGE_PATTERN.fullmatch(range_header.strip())
                if not match:
                    self._headers(
                        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                        content_type,
                        0,
                        extra={"Content-Range": f"bytes */{record.byte_size}"},
                        frame_ancestors=frame_ancestors,
                    )
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last) if last else end, end)
                else:
                    count = min(int(last or "0"), record.byte_size)
                    start, end = record.byte_size - count, record.byte_size - 1
                if start < 0 or start >= record.byte_size or end < start:
                    self._headers(
                        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                        content_type,
                        0,
                        extra={"Content-Range": f"bytes */{record.byte_size}"},
                        frame_ancestors=frame_ancestors,
                    )
                    return
                status_code = HTTPStatus.PARTIAL_CONTENT
                extra["Content-Range"] = f"bytes {start}-{end}/{record.byte_size}"
            length = max(0, end - start + 1)
            self._headers(
                status_code,
                content_type,
                length,
                extra=extra,
                frame_ancestors=frame_ancestors,
            )
            if head_only or length == 0:
                return
            os.lseek(descriptor, start, os.SEEK_SET)
            remaining = length
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        raw_path = urllib.parse.urlsplit(self.path).path
        login_prefix = "/__review_login/"
        if raw_path.startswith(login_prefix):
            token = raw_path.removeprefix(login_prefix)
            if "/" in token or not token:
                self._empty_response(HTTPStatus.UNAUTHORIZED)
                return
            self._login(token)
            return
        if not self._authorized():
            return
        self._serve(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib API
        if urllib.parse.urlsplit(self.path).path.startswith("/__review_login/"):
            self._empty_response(
                HTTPStatus.METHOD_NOT_ALLOWED,
                extra={"Allow": "GET"},
            )
            return
        if not self._authorized():
            return
        self._serve(head_only=True)

    def _method_not_allowed(self) -> None:
        self._empty_response(
            HTTPStatus.METHOD_NOT_ALLOWED,
            extra={"Allow": "GET, HEAD"},
        )

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def log_message(self, format: str, *args: object) -> None:
        # Request paths can contain one-time login capabilities and must never be logged.
        status = args[1] if len(args) > 1 else "-"
        print(
            f"review:{self.server.bundle.review_id} "
            f"{self.client_address[0]} - {self.command} {status}"
        )


class ManagedReviewServer:
    def __init__(
        self,
        bundle: ReviewBundle,
        *,
        clock: Callable[[], float] = time.monotonic,
        login_token_ttl: int = LOGIN_TOKEN_TTL_SECONDS,
        session_ttl: int = REVIEW_SESSION_TTL_SECONDS,
        public_origin: str | None = None,
        parent_session_validator: ParentSessionValidator | None = None,
    ):
        if parent_session_validator is None:
            raise ValueError("review server requires a parent session validator")
        server = ReviewHTTPServer(("127.0.0.1", bundle.port), ReviewHandler)
        server.bundle = bundle
        server.auth_lock = threading.Lock()
        server.login_tokens = {}
        server.sessions = {}
        server.cookie_name = (
            "sciencevision_review_"
            + hashlib.sha256(bundle.review_id.encode("utf-8")).hexdigest()[:16]
        )
        server.clock = clock
        server.login_token_ttl = login_token_ttl
        server.session_ttl = session_ttl
        server.parent_session_validator = parent_session_validator
        server.secure_cookie = bool(public_origin and public_origin.startswith("https://"))
        self.server = server
        self.thread = threading.Thread(
            target=server.serve_forever,
            name=f"sciencevision-review-{bundle.review_id}",
            daemon=True,
        )
        port = server.server_address[1]
        self.bound_origin = f"http://127.0.0.1:{port}"
        self.origin = public_origin or self.bound_origin
        self._started = False

    def create_login_url(
        self,
        landing_path: str = DEFAULT_LANDING_PATH,
        *,
        parent_session_id: str,
        parent_username: str,
        parent_role: str,
    ) -> str:
        """Create a one-time capability bound to a fixed reviewed landing page."""
        token = self.server.create_login_token(
            parent_session_id=parent_session_id,
            parent_username=parent_username,
            parent_role=parent_role,
            landing_path=landing_path,
        )
        return f"{self.origin}/__review_login/{token}"

    def revoke_parent(self, parent_session_id: str) -> None:
        self.server.revoke_parent(parent_session_id)

    def start(self) -> None:
        self.thread.start()
        self._started = True

    def close(self) -> None:
        if self._started:
            self.server.shutdown()
        self.server.server_close()
        if self._started:
            self.thread.join(timeout=5)
        self._started = False
