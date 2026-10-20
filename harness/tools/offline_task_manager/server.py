"""Loopback-only HTTP management UI backed by :mod:`store`."""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

from .bundle import (
    LEGACY_INTEGRITY_ALLOW_MISSING,
    LEGACY_INTEGRITY_REQUIRE_CHECKSUMS,
)
from .contract import ADMISSION_RECEIPT_PURPOSE, ContractError
from .packager import build_expert_package, build_shared_runtime
from .results_export import render_manager_results
from .store import ManagerStore, PreflightError, utc_now

STATIC_ROOT = Path(__file__).resolve().parent / "manager_app"
ONLINE_APP_ROOT = Path(__file__).resolve().parent / "online_app"
# The language layer is shared with the online app, so the console markup
# loads it from the same path the gateway publishes it under.
SHARED_STATIC_FILES = {"i18n.js": ONLINE_APP_ROOT / "i18n.js"}
MAX_BODY = 64 * 1024 * 1024


class ManagerServer(HTTPServer):
    store: ManagerStore


class Handler(BaseHTTPRequestHandler):
    server: ManagerServer

    def _headers(self, status: int, content_type: str, length: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _json(self, status: int, value: Any) -> None:
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
        self._headers(status, "application/json; charset=utf-8", len(payload))
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

    def do_GET(self) -> None:  # noqa: N802
        if not self._request_is_loopback():
            self._json(403, {"error": "loopback Host required"})
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/overview":
            self._json(200, self.server.store.overview())
            return
        if parsed.path == "/api/results/export":
            query = urllib.parse.parse_qs(parsed.query)
            format_name = query.get("format", ["json"])[0]
            if format_name not in {"json", "csv"}:
                self._json(400, {"error": "format must be json or csv"})
                return
            rows = self.server.store.result_rows()
            payload, content_type, filename = render_manager_results(
                rows, format_name=format_name, exported_at=utc_now()
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        self._serve_static(parsed.path)

    def _serve_static(self, raw_path: str) -> None:
        relative = PurePosixPath(urllib.parse.unquote(raw_path).lstrip("/") or "index.html")
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            self.send_error(404)
            return
        shared = SHARED_STATIC_FILES.get(str(relative))
        root = ONLINE_APP_ROOT if shared else STATIC_ROOT
        path = (shared or STATIC_ROOT.joinpath(*relative.parts)).resolve()
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            self.send_error(404)
            return
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._headers(200, content_type, len(payload))
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if not self._request_is_loopback():
                self._json(403, {"error": "loopback Host required"})
                return
            body = self._body()
            if self.path == "/api/experts/import":
                result = self.server.store.import_experts_csv(str(body.get("csv", "")))
            elif self.path == "/api/batches/import":
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
            elif self.path == "/api/assignments/auto":
                result = self.server.store.auto_assign(batch_id=str(body.get("batch_id", "")))
            elif self.path == "/api/assignments/explicit":
                result = self.server.store.assign_explicit(
                    batch_id=str(body.get("batch_id", "")),
                    task_id=str(body.get("task_id", "")),
                    expert_id=str(body.get("expert_id", "")),
                )
            elif self.path == "/api/packages/build":
                result = build_expert_package(
                    self.server.store,
                    batch_id=str(body.get("batch_id", "")),
                    expert_id=str(body.get("expert_id", "")),
                    output=str(body.get("output", "")),
                    mode=str(body.get("mode") or "standalone"),
                )
            elif self.path in {"/api/bundles/publish", "/api/publish-batch"}:
                result = self.server.store.publish_batch(
                    batch_id=str(body.get("batch_id", ""))
                )
            elif self.path in {
                "/api/bundles/import-legacy",
                "/api/import-legacy-package",
            }:
                activate = body.get("activate", True)
                if not isinstance(activate, bool):
                    raise ContractError("activate must be boolean")
                allow_missing = body.get("allow_historical_missing_checksums", False)
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
            elif self.path == "/api/runtime/build":
                result = build_shared_runtime(
                    viewer_root=str(body.get("viewer_root", "")),
                    output=str(body.get("output", "")),
                )
            elif self.path == "/api/results/import":
                submission = body.get("submission")
                if not isinstance(submission, dict):
                    raise ContractError("submission must be an object")
                result = self.server.store.import_submission(submission)
            else:
                self._json(404, {"error": "unknown endpoint"})
                return
            self._json(200, result)
        except (ContractError, PreflightError, FileExistsError, FileNotFoundError, json.JSONDecodeError) as error:
            value: dict[str, Any] = {"error": str(error)}
            if isinstance(error, PreflightError):
                value["errors"] = error.errors
            self._json(400, value)

    def _request_is_loopback(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return (urllib.parse.urlsplit(origin).hostname or "").lower() in {
            "127.0.0.1",
            "localhost",
            "::1",
        }

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def serve(db_path: Path, *, port: int = 4173, open_browser: bool = True) -> int:
    if not 0 <= port <= 65535:
        raise ContractError("port must be between 0 and 65535")
    store = ManagerStore(db_path)
    server = ManagerServer(("127.0.0.1", port), Handler)
    server.store = store
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"ScienceVision offline task manager: {url}", flush=True)
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
    return 0
