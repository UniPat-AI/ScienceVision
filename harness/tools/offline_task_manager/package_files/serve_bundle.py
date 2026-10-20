#!/usr/bin/env python3
"""Verify a thin expert bundle and serve it with a shared viewer runtime."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import re
import threading
import urllib.parse
import webbrowser
from functools import partial
from pathlib import Path, PurePosixPath

BUNDLE_ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 4180


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def object_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("manifest path is not text")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise RuntimeError(f"unsafe manifest path: {value!r}")
    return path.as_posix()


def verify_checksums() -> tuple[int, int]:
    expected: dict[str, str] = {}
    checksum_path = BUNDLE_ROOT / "SHA256SUMS"
    for line_number, line in enumerate(checksum_path.read_text().splitlines(), 1):
        digest, separator, relative = line.partition("  ")
        name = safe_path(relative)
        if (
            not separator
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or name in expected
        ):
            raise RuntimeError(f"invalid SHA256SUMS line {line_number}")
        expected[name] = digest
    if any(path.is_symlink() for path in BUNDLE_ROOT.rglob("*")):
        raise RuntimeError("thin bundle contains a symlink")
    actual = {
        path.relative_to(BUNDLE_ROOT).as_posix()
        for path in BUNDLE_ROOT.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise RuntimeError("thin bundle file inventory changed")
    mismatched = [
        name for name, digest in expected.items()
        if file_sha256(BUNDLE_ROOT / name) != digest
    ]
    if mismatched:
        raise RuntimeError("thin bundle checksum mismatch: " + ", ".join(mismatched[:10]))
    return len(expected), sum((BUNDLE_ROOT / name).stat().st_size for name in expected)


def verify_bundle_manifest() -> tuple[dict, set[str]]:
    manifest = json.loads((BUNDLE_ROOT / "bundle_manifest.json").read_text())
    if manifest.get("schema_version") != "sciencevision.expert_task_bundle.v2":
        raise RuntimeError("unsupported expert bundle manifest")
    body = {key: value for key, value in manifest.items() if key != "bundle_manifest_sha256"}
    if object_sha256(body) != manifest.get("bundle_manifest_sha256"):
        raise RuntimeError("expert bundle manifest digest mismatch")
    task_file = manifest.get("task_file") or {}
    task_path = safe_path(task_file.get("path"))
    task_bytes = (BUNDLE_ROOT / task_path).read_bytes()
    if hashlib.sha256(task_bytes).hexdigest() != task_file.get("sha256"):
        raise RuntimeError("expert task JSONL digest mismatch")
    if len([line for line in task_bytes.splitlines() if line.strip()]) != task_file.get("count"):
        raise RuntimeError("expert task JSONL count mismatch")
    allowed = {"bundle_manifest.json", task_path}
    for record in manifest.get("assets", []):
        name = safe_path(record.get("path"))
        path = BUNDLE_ROOT / name
        if not path.is_file() or file_sha256(path) != record.get("sha256"):
            raise RuntimeError(f"expert bundle asset mismatch: {name}")
        if record.get("byte_size") is not None and path.stat().st_size != record["byte_size"]:
            raise RuntimeError(f"expert bundle asset size mismatch: {name}")
        allowed.add(name)
    return manifest, allowed


def verify_runtime(runtime: Path) -> tuple[dict, Path, set[str]]:
    if runtime.is_symlink():
        raise RuntimeError("runtime root cannot be a symlink")
    root = runtime.expanduser().resolve()
    manifest = json.loads((root / "runtime_manifest.json").read_text())
    if manifest.get("schema_version") != "sciencevision.viewer_runtime.v1":
        raise RuntimeError("unsupported viewer runtime manifest")
    body = {key: value for key, value in manifest.items() if key != "runtime_id"}
    if "vrt_" + object_sha256(body)[:24] != manifest.get("runtime_id"):
        raise RuntimeError("viewer runtime identity mismatch")
    records = manifest.get("files")
    if not isinstance(records, list) or object_sha256(records) != manifest.get("files_sha256"):
        raise RuntimeError("viewer runtime file closure mismatch")
    expected: dict[str, dict] = {}
    for record in records:
        name = safe_path(record.get("path"))
        if name in expected:
            raise RuntimeError(f"duplicate viewer runtime path: {name}")
        expected[name] = record
    if any(path.is_symlink() for path in root.rglob("*")):
        raise RuntimeError("viewer runtime contains a symlink")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "runtime_manifest.json"
    }
    if set(expected) != actual:
        raise RuntimeError("viewer runtime file inventory changed")
    for name, record in expected.items():
        path = root / name
        if file_sha256(path) != record.get("sha256") or path.stat().st_size != record.get(
            "byte_size"
        ):
            raise RuntimeError(f"viewer runtime file mismatch: {name}")
    actual_capabilities = []
    if {"index.html", "expert-app.js", "styles.css"}.issubset(expected):
        actual_capabilities.append("expert-review")
    if {
        "viewer/shared/screenshot-api.js",
        "viewer/shared/task-screenshot.js",
    }.issubset(expected):
        actual_capabilities.append("task-screenshot")
    if sorted(manifest.get("capabilities", [])) != sorted(actual_capabilities):
        raise RuntimeError("viewer runtime capability inventory mismatch")
    return manifest, root, set(expected)


def verify_runtime_compatibility(bundle_manifest: dict, runtime_manifest: dict) -> None:
    requirement = bundle_manifest.get("runtime_requirement")
    if not isinstance(requirement, dict):
        raise RuntimeError("bundle runtime_requirement must be an object")
    loader_api = requirement.get("loader_api")
    if not isinstance(loader_api, str) or not loader_api:
        raise RuntimeError("bundle runtime_requirement.loader_api must be text")
    if loader_api != runtime_manifest.get("loader_api"):
        raise RuntimeError("viewer runtime loader API does not satisfy bundle requirements")
    for field in ("renderers", "capabilities"):
        required = requirement.get(field)
        available = runtime_manifest.get(field)
        if not isinstance(required, list) or not all(
            isinstance(item, str) and item for item in required
        ):
            raise RuntimeError(f"bundle runtime_requirement.{field} must be text values")
        if not isinstance(available, list) or not all(
            isinstance(item, str) and item for item in available
        ):
            raise RuntimeError(f"viewer runtime {field} inventory is invalid")
        missing = sorted(set(required) - set(available))
        if missing:
            raise RuntimeError(
                f"viewer runtime is missing required {field}: {', '.join(missing)}"
            )


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: object,
        runtime_root: Path,
        runtime_allowed: set[str],
        bundle_allowed: set[str],
        **kwargs: object,
    ) -> None:
        self.runtime_root = runtime_root
        self.runtime_allowed = runtime_allowed
        self.bundle_allowed = bundle_allowed
        super().__init__(*args, directory=str(BUNDLE_ROOT), **kwargs)

    def translate_path(self, path: str) -> str:
        raw = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        raw_name = raw.lstrip("/") or "index.html"
        relative = PurePosixPath(raw_name)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            return str(BUNDLE_ROOT / "__blocked__")
        name = relative.as_posix()
        if name in self.runtime_allowed:
            return str(self.runtime_root.joinpath(*relative.parts))
        if name in self.bundle_allowed:
            return str(BUNDLE_ROOT.joinpath(*relative.parts))
        return str(BUNDLE_ROOT / "__blocked__")

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
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bundle_count, bundle_size = verify_checksums()
    bundle_manifest, allowed = verify_bundle_manifest()
    runtime_manifest, runtime_root, runtime_allowed = verify_runtime(args.runtime)
    verify_runtime_compatibility(bundle_manifest, runtime_manifest)
    if args.verify_only:
        print(
            f"OK: {bundle_count} bundle files, {bundle_size} bytes; "
            f"runtime {runtime_manifest['runtime_id']} verified"
        )
        return 0
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        partial(
            Handler,
            runtime_root=runtime_root,
            runtime_allowed=runtime_allowed,
            bundle_allowed=allowed,
        ),
    )
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"ScienceVision thin expert bundle: {url}", flush=True)
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
