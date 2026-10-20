"""Loopback-only HTTP surface for a verified reviewer bundle."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import stat
from typing import Any
from urllib.parse import urlsplit

from .bundle import ReviewerBundleError, _raw_sha256, _read_regular_bytes, verify_reviewer_bundle


class ReviewerBundleServerError(ReviewerBundleError):
    """The local reviewer bridge cannot safely serve a requested resource."""


_MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".cif": "chemical/x-cif",
    ".xyz": "chemical/x-xyz",
    ".nii": "application/octet-stream",
    ".gz": "application/gzip",
}
_STATIC_PATH = re.compile(r"^/(?:index\.html|app\.js|style\.css|vendor/(?:molstar/(?:molstar\.(?:js|css))|niivue/niivue\.umd\.js))$")
_MASK_ROUTE = re.compile(r"^/scene/[^/]+/(?:mask|overlay)(?:[._-].*)?$")


def _content_type(path: Path) -> str:
    if path.name.endswith(".nii.gz"):
        return "application/gzip"
    return _MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


class _LoopbackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_reviewer_bundle_server(
    bundle_root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """Create, but do not start, the narrow loopback reviewer HTTP server."""
    if host != "127.0.0.1":
        raise ReviewerBundleServerError("reviewer viewer bridge must bind exactly to 127.0.0.1")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ReviewerBundleServerError("reviewer viewer bridge port is invalid")
    verified = verify_reviewer_bundle(bundle_root)
    root = Path(verified["bundle_root"])
    registry = verified["registry"]
    checksums = verified["checksums"]
    public_files: dict[str, Path] = {
        "/": root / "public" / "index.html",
        "/index.html": root / "public" / "index.html",
        "/app.js": root / "public" / "app.js",
        "/style.css": root / "public" / "style.css",
        "/vendor/molstar/molstar.js": root / "public" / "vendor" / "molstar" / "molstar.js",
        "/vendor/molstar/molstar.css": root / "public" / "vendor" / "molstar" / "molstar.css",
        "/vendor/niivue/niivue.umd.js": root / "public" / "vendor" / "niivue" / "niivue.umd.js",
        "/api/tasks": root / "public" / "tasks.json",
        "/api/scene-registry": root / "scene-registry.json",
    }
    capabilities: dict[str, bytes] = {}
    for scene_ref, scene in registry.items():
        public_files[scene["primary_url"]] = root / "scenes" / scene_ref / f"primary.{scene['format']}"
        capabilities[f"/api/scenes/{scene_ref}/capabilities"] = json.dumps(
            {
                "scene_ref": scene_ref,
                "viewer": scene["viewer"],
                "allowed_operations": scene["allowed_operations"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            # Browser requests must not put task or scene paths into terminal logs.
            return

        def _headers(self, *, content_type: str, length: int, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _not_found(self) -> None:
            payload = b'{"error":"not_found"}\n'
            self._headers(content_type="application/json; charset=utf-8", length=len(payload), status=HTTPStatus.NOT_FOUND)
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _method_not_allowed(self) -> None:
            payload = b'{"error":"method_not_allowed"}\n'
            self._headers(content_type="application/json; charset=utf-8", length=len(payload), status=HTTPStatus.METHOD_NOT_ALLOWED)
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _checked_file(self, path: Path) -> bytes:
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ReviewerBundleServerError("HTTP route escapes reviewer bundle") from exc
            expected = checksums.get(relative)
            if expected is None:
                raise ReviewerBundleServerError("HTTP route is not in the reviewer checksum manifest")
            if path.is_symlink() or not path.is_file():
                raise ReviewerBundleServerError("HTTP route points to an unsafe file")
            try:
                mode = path.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise ReviewerBundleServerError("cannot inspect HTTP artifact") from exc
            if not stat.S_ISREG(mode):
                raise ReviewerBundleServerError("HTTP route does not point to a regular file")
            content = _read_regular_bytes(path, label="HTTP reviewer artifact")
            if _raw_sha256(content) != expected:
                raise ReviewerBundleServerError("HTTP artifact no longer matches its verified checksum")
            return content

        def _dispatch(self) -> None:
            parsed = urlsplit(self.path)
            route = parsed.path
            if parsed.query and route not in {"/", "/index.html"}:
                self._not_found()
                return
            if _MASK_ROUTE.fullmatch(route):
                self._not_found()
                return
            if route == "/healthz":
                content = b'{"status":"ready","host":"127.0.0.1"}\n'
                self._headers(content_type="application/json; charset=utf-8", length=len(content))
                if self.command != "HEAD":
                    self.wfile.write(content)
                return
            if route in capabilities:
                content = capabilities[route]
                self._headers(content_type="application/json; charset=utf-8", length=len(content))
                if self.command != "HEAD":
                    self.wfile.write(content)
                return
            path = public_files.get(route)
            if path is None:
                self._not_found()
                return
            try:
                content = self._checked_file(path)
            except ReviewerBundleServerError:
                self._not_found()
                return
            self._headers(content_type=_content_type(path), length=len(content))
            if self.command != "HEAD":
                self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

    return _LoopbackServer((host, port), Handler)
