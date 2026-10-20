#!/usr/bin/env python3
"""Verify and serve this expert package on loopback only."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import re
import threading
import urllib.parse
import webbrowser
from functools import partial
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 4180
BLOCKED_TOP_LEVEL = frozenset(
    {"sources", "qc", "quarantine", "oracle", "asset-source", "uploads"}
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify() -> tuple[int, int]:
    expected: dict[str, str] = {}
    for line_number, line in enumerate((ROOT / "SHA256SUMS").read_text().splitlines(), 1):
        digest, separator, relative = line.partition("  ")
        path = PurePosixPath(relative)
        if (
            not separator
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() in expected
        ):
            raise RuntimeError(f"invalid SHA256SUMS line {line_number}")
        expected[path.as_posix()] = digest
    linked = [path for path in ROOT.rglob("*") if path.is_symlink()]
    if linked:
        raise RuntimeError("package contains a symlink: " + str(linked[0].relative_to(ROOT)))
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise RuntimeError("package file inventory changed")
    mismatched = [name for name, digest in expected.items() if sha256(ROOT / name) != digest]
    if mismatched:
        raise RuntimeError("package checksum mismatch: " + ", ".join(mismatched[:10]))
    return len(expected), sum((ROOT / name).stat().st_size for name in expected)


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        relative = PurePosixPath(urllib.parse.unquote(urllib.parse.urlsplit(path).path).lstrip("/"))
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            return str(ROOT / "__blocked__")
        if relative.parts and relative.parts[0].lower() in BLOCKED_TOP_LEVEL:
            return str(ROOT / "__blocked__")
        candidate = ROOT.joinpath(*relative.parts).resolve()
        return str(candidate if candidate.is_relative_to(ROOT) else ROOT / "__blocked__")

    def list_directory(self, _path: str):
        self.send_error(403, "Directory listing disabled")
        return None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
        )
        super().end_headers()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"loopback port (default: {DEFAULT_PORT}; use 0 for a temporary random port)",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    count, size = verify()
    if args.verify_only:
        print(f"OK: {count} files, {size} bytes verified")
        return 0
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", args.port), partial(Handler, directory=str(ROOT))
    )
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"ScienceVision expert package: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
