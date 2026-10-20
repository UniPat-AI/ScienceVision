"""Solver-safe Mol* host proxy for the local chirality pilot."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import ipaddress
import math
import mimetypes
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import threading
from typing import Any, Mapping
from urllib.parse import unquote, urlencode, urlsplit
import warnings

from PIL import Image, ImageStat

from ..contracts import digest, digest_bytes, make_envelope, require_digest, utc_now, verify_envelope
from ..errors import InputError, StaleInputError
from ..paths import WorkspacePaths, require_within, safe_component
from ..storage.atomic import atomic_write_json, read_json
from .adapter import (
    AdapterHealth,
    AdapterObservation,
    AdapterProfile,
    AdapterSession,
    REQUIRED_FORBIDDEN_CAPABILITIES,
    ViewerAdapterError,
    validate_session_operation,
)
from .contracts import validate_anonymous_scene_ref
from .host_build import CHIRALITY_SESSION_EVIDENCE_SCHEMA
from .profile import load_viewer_profile, revalidate_viewer_profile
from .scene_registry import SceneRegistry


ADAPTER_ID = "molstar-chirality-host-v1"
PILOT_OPERATIONS = frozenset({"rotate", "render"})
_CAMERA_KEYS = {"position", "target", "up", "mode", "fov_radians", "distance"}
_ROTATE_KEYS = {"d_azimuth_deg", "d_elevation_deg", "d_roll_deg"}
_SESSION_KEYS = {"request_id", "viewer_profile_digest", "allowed_operations"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_MIN_RENDER_SIDE = 64
_MAX_RENDER_SIDE = 4096
_MAX_RENDER_BYTES = 16 * 1024 * 1024
_MIN_VISIBLE_PIXEL_VARIANCE = 0.5


def _browser_executable_candidates(chromium: Any) -> list[str | None]:
    expected = Path(chromium.executable_path)
    candidates: list[str | None] = [None] if expected.is_file() else []
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    cached = sorted(
        cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"),
        reverse=True,
    )
    cached.extend(
        sorted(
            cache.glob(
                "chromium-*/chrome-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            ),
            reverse=True,
        )
    )
    system_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for path in [*cached, system_chrome]:
        if path.is_file() and os.access(path, os.X_OK) and str(path) not in candidates:
            candidates.append(str(path))
    return candidates


def _write_private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def _finite_number(value: Any, label: str, *, limit: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ViewerAdapterError(f"{label} must be a finite number")
    number = float(value)
    if limit is not None and abs(number) > limit:
        raise ViewerAdapterError(f"{label} exceeds the controlled range")
    return number


def _controlled_camera(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _CAMERA_KEYS:
        raise ViewerAdapterError("Mol* returned an invalid camera contract")
    camera: dict[str, Any] = {}
    for field in ("position", "target", "up"):
        vector = value[field]
        if not isinstance(vector, list) or len(vector) != 3:
            raise ViewerAdapterError(f"Mol* camera {field} must be a three-vector")
        camera[field] = [_finite_number(item, f"Mol* camera {field}") for item in vector]
    mode = value["mode"]
    if mode not in {"perspective", "orthographic"}:
        raise ViewerAdapterError("Mol* camera mode is unsupported")
    camera["mode"] = mode
    camera["fov_radians"] = _finite_number(value["fov_radians"], "Mol* camera fov")
    camera["distance"] = _finite_number(value["distance"], "Mol* camera distance")
    return camera


def _validate_render_png(png: bytes) -> str:
    """Decode a bounded, non-blank PNG and return its visible RGB digest."""

    if not isinstance(png, bytes) or not png.startswith(_PNG_SIGNATURE) or not png.endswith(_PNG_IEND):
        raise ViewerAdapterError("Mol* render is not a complete PNG image")
    if len(png) > _MAX_RENDER_BYTES:
        raise ViewerAdapterError("Mol* render exceeds the controlled PNG byte limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(png)) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise ViewerAdapterError("Mol* render must be one static PNG frame")
                width, height = image.size
                if not (
                    _MIN_RENDER_SIDE <= width <= _MAX_RENDER_SIDE
                    and _MIN_RENDER_SIDE <= height <= _MAX_RENDER_SIDE
                ):
                    raise ViewerAdapterError("Mol* render dimensions are outside the controlled range")
                image.verify()
            with Image.open(BytesIO(png)) as image:
                image.load()
                rgba = image.convert("RGBA")
    except ViewerAdapterError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ViewerAdapterError("Mol* render failed PNG decoding and integrity validation") from exc

    alpha = rgba.getchannel("A")
    alpha_histogram = alpha.histogram()
    if sum(alpha_histogram[1:]) == 0:
        raise ViewerAdapterError("Mol* render is fully transparent")
    visible_mask = alpha.point(lambda value: 255 if value else 0)
    variance = ImageStat.Stat(rgba.convert("RGB"), mask=visible_mask).var
    if not variance or max(variance) < _MIN_VISIBLE_PIXEL_VARIANCE:
        raise ViewerAdapterError("Mol* render has no meaningful visible pixel variation")
    visible_rgb = Image.alpha_composite(Image.new("RGBA", rgba.size, (0, 0, 0, 255)), rgba).convert("RGB")
    dimensions = rgba.width.to_bytes(4, "big") + rgba.height.to_bytes(4, "big")
    return digest_bytes(dimensions + visible_rgb.tobytes())


def _regular_below(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ViewerAdapterError("loopback asset path is invalid")
    root_resolved = root.resolve(strict=True)
    candidate = root_resolved.joinpath(*parts)
    current = root_resolved
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ViewerAdapterError("loopback server refuses symlinked content")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ViewerAdapterError("loopback asset escapes its trusted root") from exc
    if not resolved.is_file():
        raise ViewerAdapterError("loopback asset is not a regular file")
    return resolved


class _LoopbackAssetServer:
    def __init__(self, package_root: Path) -> None:
        self.package_root = package_root.resolve(strict=True)
        self._assets: dict[str, bytes] = {}
        self._lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "qagen-viewer-host/1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self, head_only=False)

            def do_HEAD(self) -> None:  # noqa: N802
                owner._handle(self, head_only=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="qagen-viewer-loopback", daemon=True)
        self._thread.start()

    @property
    def origin(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def register_asset(self, content: bytes) -> str:
        if not isinstance(content, bytes) or not content:
            raise ViewerAdapterError("loopback scene asset must be non-empty immutable bytes")
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._assets[token] = content
        return token

    def unregister_asset(self, token: str) -> None:
        with self._lock:
            self._assets.pop(token, None)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _send_headers(self, handler: BaseHTTPRequestHandler, content_type: str, length: int) -> None:
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(length))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
        handler.send_header(
            "Content-Security-Policy",
            "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:",
        )
        handler.end_headers()

    def _deny(self, handler: BaseHTTPRequestHandler, status: HTTPStatus = HTTPStatus.NOT_FOUND) -> None:
        body = b"not found\n"
        handler.send_response(status)
        self._send_headers(handler, "text/plain; charset=utf-8", len(body))
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _handle(self, handler: BaseHTTPRequestHandler, *, head_only: bool) -> None:
        try:
            if not ipaddress.ip_address(handler.client_address[0]).is_loopback:
                self._deny(handler, HTTPStatus.FORBIDDEN)
                return
            expected_host = urlsplit(self.origin).netloc
            if handler.headers.get("Host") != expected_host:
                self._deny(handler, HTTPStatus.FORBIDDEN)
                return
            parsed = urlsplit(handler.path)
            path = unquote(parsed.path)
            if path.startswith("/__qagen_scene/"):
                token = path.removeprefix("/__qagen_scene/")
                if not token or "/" in token:
                    self._deny(handler)
                    return
                with self._lock:
                    content = self._assets.get(token)
                if content is None:
                    self._deny(handler)
                    return
                content_type = "chemical/x-mdl-molfile"
            elif path == "/viewer/molstar.html" or path.startswith("/viewer/shared/"):
                relative = path.removeprefix("/")
                if any(part in {"provenance", "sources", "tasks"} for part in PurePosixPath(relative).parts):
                    self._deny(handler)
                    return
                file_path = _regular_below(self.package_root, relative)
                content = file_path.read_bytes()
                content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            else:
                self._deny(handler)
                return
            handler.send_response(HTTPStatus.OK)
            self._send_headers(handler, content_type, len(content))
            if not head_only:
                handler.wfile.write(content)
        except (OSError, ViewerAdapterError):
            self._deny(handler)


@dataclass
class _HostSession:
    public: AdapterSession
    anonymous_scene_ref: str
    registry_digest: str
    attestation_digest: str
    directory: Path
    asset_token: str
    context: Any
    page: Any
    sequence: int = 0
    previous_receipt_digest: str | None = None
    previous_visible_rgb_digest: str | None = None


class MolstarChiralityHostAdapter:
    """Concrete controlled adapter exposing only rotate and render."""

    def __init__(
        self,
        paths: WorkspacePaths,
        registry: SceneRegistry,
        *,
        viewer_profile: Mapping[str, Any] | None = None,
        viewer_profile_path: str | Path | None = None,
    ) -> None:
        self.paths = paths
        self.registry = registry
        self._viewer_profile: dict[str, Any] | None = None
        self._viewer_profile_path: Path | None = None
        if viewer_profile is not None or viewer_profile_path is not None:
            if viewer_profile is None or viewer_profile_path is None:
                raise ViewerAdapterError("controlled chirality host requires both a ViewerProfile and its private path")
            self._bind_viewer_profile(viewer_profile, viewer_profile_path)
        first_binding = registry.resolve(registry.anonymous_scene_refs[0])
        self._package_root = Path(first_binding.backend_arguments["package_root"])
        self._server: _LoopbackAssetServer | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._sessions: dict[str, _HostSession] = {}

    @classmethod
    def from_registry_path(
        cls,
        paths: WorkspacePaths,
        registry_path: str | Path,
    ) -> "MolstarChiralityHostAdapter":
        return cls(paths, SceneRegistry.load(paths, registry_path))

    @classmethod
    def from_profile_path(
        cls,
        paths: WorkspacePaths,
        profile_path: str | Path,
    ) -> "MolstarChiralityHostAdapter":
        """Create the host only after loading and revalidating a real profile."""
        profile = load_viewer_profile(paths, profile_path)
        return cls(
            paths,
            SceneRegistry.load(paths, profile["body"].get("scene_registry_path")),
            viewer_profile=profile,
            viewer_profile_path=profile_path,
        )

    def _bind_viewer_profile(self, profile: Mapping[str, Any], profile_path: str | Path) -> None:
        try:
            envelope = revalidate_viewer_profile(profile)
            resolved_path = require_within(self.paths.viewer_profiles, profile_path, "viewer profile")
        except (InputError, StaleInputError) as exc:
            raise ViewerAdapterError(f"chirality ViewerProfile cannot be revalidated: {exc}") from exc
        body = envelope.get("body")
        if not isinstance(body, Mapping):
            raise ViewerAdapterError("chirality ViewerProfile body is malformed")
        expected_operations = sorted(PILOT_OPERATIONS)
        required = {
            "scene_registry_digest": self.registry.content_digest,
            "allowed_operations": expected_operations,
            "host_build_digest": body.get("host_build_digest"),
            "session_evidence_schema_digest": body.get("session_evidence_schema_digest"),
        }
        if (
            envelope.get("status") != "attested_local_only"
            or body.get("local_fixture") is not False
            or body.get("scene_registry_path") != str(self.registry.path)
            or body.get("scene_registry_digest") != required["scene_registry_digest"]
            or body.get("allowed_operations") != required["allowed_operations"]
            or not isinstance(required["host_build_digest"], str)
            or not isinstance(required["session_evidence_schema_digest"], str)
        ):
            raise ViewerAdapterError("chirality ViewerProfile does not bind this controlled host")
        require_digest(required["host_build_digest"], "chirality ViewerProfile host build digest")
        require_digest(
            required["session_evidence_schema_digest"],
            "chirality ViewerProfile session evidence schema digest",
        )
        self._viewer_profile = dict(envelope)
        self._viewer_profile_path = resolved_path

    def health(self) -> AdapterHealth:
        try:
            from playwright.sync_api import sync_playwright

            runtime = sync_playwright().start()
            try:
                candidates = _browser_executable_candidates(runtime.chromium)
            finally:
                runtime.stop()
        except Exception:
            return AdapterHealth(
                status="unavailable",
                adapter_id=ADAPTER_ID,
                detail="Playwright runtime is unavailable",
            )
        if not candidates:
            return AdapterHealth(
                status="unavailable",
                adapter_id=ADAPTER_ID,
                detail="local Chromium executable is unavailable",
            )
        return AdapterHealth(status="ready", adapter_id=ADAPTER_ID, detail="local controlled Mol* host is ready")

    def profile(self) -> AdapterProfile:
        return AdapterProfile(
            adapter_id=ADAPTER_ID,
            available_operations=PILOT_OPERATIONS,
            native_measurement_operations=frozenset(),
            forbidden_capabilities=REQUIRED_FORBIDDEN_CAPABILITIES,
            accepts_anonymous_scene_ref=True,
            accepts_solver_asset_identifiers=False,
            exposes_raw_asset_metadata=False,
            exposes_raw_asset_bytes=False,
            network_access=False,
            arbitrary_compute=False,
            attested_evidence=True,
        )

    def _ensure_runtime(self) -> None:
        if self._browser is not None:
            return
        if self.health().status != "ready":
            raise ViewerAdapterError("Playwright runtime is unavailable")
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            for executable in _browser_executable_candidates(self._playwright.chromium):
                try:
                    launch_arguments: dict[str, Any] = {
                        "headless": True,
                        "args": ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
                    }
                    if executable is not None:
                        launch_arguments["executable_path"] = executable
                    self._browser = self._playwright.chromium.launch(**launch_arguments)
                    break
                except Exception:
                    self._browser = None
            if self._browser is None:
                raise ViewerAdapterError("no local controlled Chromium executable could start")
            self._server = _LoopbackAssetServer(self._package_root)
        except Exception as exc:
            self.shutdown()
            raise ViewerAdapterError(f"controlled Mol* runtime failed to start ({type(exc).__name__})") from exc

    def open_session(
        self,
        *,
        anonymous_scene_ref: str,
        request: Mapping[str, Any],
    ) -> AdapterSession:
        if self._viewer_profile is not None and self._viewer_profile_path is not None:
            try:
                fresh_profile = load_viewer_profile(self.paths, self._viewer_profile_path)
            except InputError as exc:
                raise ViewerAdapterError(f"chirality ViewerProfile cannot be reloaded: {exc}") from exc
            self._bind_viewer_profile(fresh_profile, self._viewer_profile_path)
        if not isinstance(request, Mapping) or set(request) != _SESSION_KEYS:
            raise ViewerAdapterError("viewer session request has unsupported or missing fields")
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ViewerAdapterError("viewer session request_id is required")
        profile_digest = request.get("viewer_profile_digest")
        require_digest(profile_digest, "viewer session profile digest")
        requested = request.get("allowed_operations")
        if not isinstance(requested, list) or not requested or not all(isinstance(item, str) for item in requested):
            raise ViewerAdapterError("viewer session allowed_operations must be a non-empty string list")
        allowed = frozenset(requested)
        if len(allowed) != len(requested) or not allowed.issubset(PILOT_OPERATIONS):
            raise ViewerAdapterError("viewer session can request only rotate and render")
        if self._viewer_profile is not None:
            profile_body = self._viewer_profile["body"]
            if profile_digest != self._viewer_profile["content_digest"]:
                raise ViewerAdapterError("viewer session digest does not match the loaded ViewerProfile")
            if allowed != frozenset(profile_body["allowed_operations"]):
                raise ViewerAdapterError("viewer session operations do not match the loaded ViewerProfile")
        binding = self.registry.resolve(anonymous_scene_ref)
        self._ensure_runtime()
        assert self._server is not None and self._browser is not None
        session_id = f"vsess_{secrets.token_hex(16)}"
        asset_bytes = binding.backend_arguments.get("asset_bytes")
        if not isinstance(asset_bytes, bytes) or not asset_bytes:
            raise ViewerAdapterError("private scene binding has no immutable asset snapshot")
        asset_token = self._server.register_asset(asset_bytes)
        context = self._browser.new_context(viewport={"width": 960, "height": 720}, device_scale_factor=1)
        origin = self._server.origin

        def route_request(route: Any) -> None:
            target = urlsplit(route.request.url)
            if target.scheme in {"data", "blob"} or f"{target.scheme}://{target.netloc}" == origin:
                route.continue_()
            else:
                route.abort("blockedbyclient")

        context.route("**/*", route_request)
        page = context.new_page()
        page.set_default_timeout(45_000)
        query = urlencode({"file": f"/__qagen_scene/{asset_token}", "type": "mol"})
        try:
            page.goto(f"{origin}/viewer/molstar.html?{query}", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.ScienceVisionViewerMCP?.ready === true && "
                "window.ScienceVisionViewerScreenshot?.ready === true"
            )
        except Exception as exc:
            context.close()
            self._server.unregister_asset(asset_token)
            raise ViewerAdapterError(f"Mol* scene failed to become ready ({type(exc).__name__})") from exc

        public = AdapterSession(
            session_id=session_id,
            request_id=request_id.strip(),
            viewer_profile_digest=profile_digest,
            allowed_operations=allowed,
        )
        directory = self.paths.private / "viewer" / "sessions" / safe_component(session_id, "viewer session id")
        directory.mkdir(parents=True, exist_ok=False)
        os.chmod(directory, 0o700)
        attestation_body: dict[str, Any] = {
            "session_id": session_id,
            "request_id": public.request_id,
            "viewer_profile_digest": profile_digest,
            "anonymous_scene_ref": binding.anonymous_scene_ref,
            "registry_digest": binding.backend_arguments["registry_digest"],
            "allowed_operations": sorted(allowed),
            "adapter_id": ADAPTER_ID,
            "backend_kind": "molstar_browser_host_v1",
            "opened_at": utc_now(),
            "release_eligible": False,
            "release_blockers": ["source_spec_local_only"],
            "source_version_resolution": self.registry.resolution.public(),
        }
        if self._viewer_profile is not None and self._viewer_profile_path is not None:
            profile_body = self._viewer_profile["body"]
            attestation_body.update(
                {
                    "viewer_profile_path": str(self._viewer_profile_path),
                    "host_build_digest": profile_body["host_build_digest"],
                    "session_evidence_schema_digest": profile_body["session_evidence_schema_digest"],
                }
            )
        attestation = make_envelope(
            kind="viewer_session_attestation",
            identifier=session_id,
            status="open",
            body=attestation_body,
        )
        atomic_write_json(directory / "session-attestation.json", attestation, private=True)
        self._sessions[session_id] = _HostSession(
            public=public,
            anonymous_scene_ref=binding.anonymous_scene_ref,
            registry_digest=str(binding.backend_arguments["registry_digest"]),
            attestation_digest=attestation["content_digest"],
            directory=directory,
            asset_token=asset_token,
            context=context,
            page=page,
        )
        return public

    def _capture(self, host: _HostSession) -> tuple[bytes, dict[str, Any]]:
        result = host.page.evaluate(
            """async () => {
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              const state = await window.ScienceVisionViewerMCP.callTool('viewer_get_state', {});
              const blob = await window.ScienceVisionViewerScreenshot.captureScreenshot();
              const bytes = new Uint8Array(await blob.arrayBuffer());
              let binary = '';
              const chunk = 0x8000;
              for (let i = 0; i < bytes.length; i += chunk) {
                binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
              }
              return { png_base64: btoa(binary), camera: state.camera };
            }"""
        )
        if not isinstance(result, Mapping) or not isinstance(result.get("png_base64"), str):
            raise ViewerAdapterError("Mol* returned an invalid render contract")
        try:
            png = base64.b64decode(result["png_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ViewerAdapterError("Mol* returned an invalid PNG encoding") from exc
        _validate_render_png(png)
        return png, _controlled_camera(result.get("camera"))

    def perform(
        self,
        *,
        session: AdapterSession,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> AdapterObservation:
        host = self._sessions.get(session.session_id)
        if host is None or host.public != session:
            raise ViewerAdapterError("viewer session is unknown or no longer active")
        validate_session_operation(session, operation, self.profile())
        if not isinstance(arguments, Mapping):
            raise ViewerAdapterError("viewer operation arguments must be an object")
        controlled_arguments: dict[str, Any]
        if operation == "rotate":
            if set(arguments) - _ROTATE_KEYS or not {"d_azimuth_deg", "d_elevation_deg"}.issubset(arguments):
                raise ViewerAdapterError("rotate accepts only controlled azimuth, elevation, and roll deltas")
            controlled_arguments = {
                "d_azimuth_deg": _finite_number(arguments["d_azimuth_deg"], "rotate azimuth", limit=360),
                "d_elevation_deg": _finite_number(arguments["d_elevation_deg"], "rotate elevation", limit=360),
                "d_roll_deg": _finite_number(arguments.get("d_roll_deg", 0), "rotate roll", limit=360),
            }
            host.page.evaluate(
                "args => window.ScienceVisionViewerMCP.callTool('viewer_rotate', args)",
                controlled_arguments,
            )
        elif operation == "render":
            if arguments:
                raise ViewerAdapterError("render accepts no solver parameters")
            controlled_arguments = {}
        else:  # validate_session_operation already rejects this path.
            raise ViewerAdapterError("unsupported viewer operation")

        png, camera = self._capture(host)
        sequence = host.sequence
        render_digest = digest_bytes(png)
        visible_rgb_digest = _validate_render_png(png)
        if operation == "rotate" and host.previous_visible_rgb_digest == visible_rgb_digest:
            raise ViewerAdapterError("Mol* rotate did not produce changed rendered content")
        render_name = f"render-{sequence:06d}.png"
        _write_private_bytes(host.directory / render_name, png)
        receipt_body: dict[str, Any] = {
            "session_id": session.session_id,
            "session_attestation_digest": host.attestation_digest,
            "sequence": sequence,
            "operation": operation,
            "arguments_digest": digest(controlled_arguments),
            "render_digest": render_digest,
            "render_file": render_name,
            "camera": camera,
            "previous_receipt_digest": host.previous_receipt_digest,
            "recorded_at": utc_now(),
        }
        if self._viewer_profile is not None:
            profile_body = self._viewer_profile["body"]
            receipt_body.update(
                {
                    "viewer_profile_digest": self._viewer_profile["content_digest"],
                    "registry_digest": profile_body["scene_registry_digest"],
                    "host_build_digest": profile_body["host_build_digest"],
                    "session_evidence_schema_digest": profile_body["session_evidence_schema_digest"],
                }
            )
        receipt = make_envelope(
            kind="viewer_action_receipt",
            identifier=f"{session.session_id}:{sequence}",
            status="attested",
            body=receipt_body,
        )
        atomic_write_json(host.directory / f"receipt-{sequence:06d}.json", receipt, private=True)
        host.previous_receipt_digest = receipt["content_digest"]
        host.previous_visible_rgb_digest = visible_rgb_digest
        host.sequence += 1
        payload = {
            "render_digest": render_digest,
            "camera": camera,
            "action_receipt": {
                "sequence": sequence,
                "receipt_digest": receipt["content_digest"],
                "session_attestation_digest": host.attestation_digest,
            },
        }
        return AdapterObservation(
            operation=operation,
            sequence=sequence,
            payload=payload,
            trace_digest=receipt["content_digest"],
        )

    def close_session(self, *, session: AdapterSession) -> None:
        host = self._sessions.pop(session.session_id, None)
        if host is None or host.public != session:
            raise ViewerAdapterError("viewer session is unknown or already closed")
        try:
            atomic_write_json(
                host.directory / "closed.json",
                {
                    "session_attestation_digest": host.attestation_digest,
                    "action_count": host.sequence,
                    "final_receipt_digest": host.previous_receipt_digest,
                    "closed_at": utc_now(),
                },
                private=True,
            )
        finally:
            host.context.close()
            if self._server is not None:
                self._server.unregister_asset(host.asset_token)

    def shutdown(self) -> None:
        for host in list(self._sessions.values()):
            try:
                host.context.close()
            except Exception:
                pass
        self._sessions.clear()
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if self._server is not None:
            self._server.close()
            self._server = None


def diagnose_chirality_host(paths: WorkspacePaths, registry_path: str | Path) -> dict[str, Any]:
    registry = SceneRegistry.load(paths, registry_path)
    if not registry.resolution.resolved:
        return {
            "artifact_kind": "chirality_host_diagnostic",
            "adapter_id": ADAPTER_ID,
            "status": "blocked",
            "runtime_ready": False,
            "production_ready": False,
            "health": {"status": "unavailable", "detail": "source version resolution is blocked"},
            "available_operations": sorted(PILOT_OPERATIONS),
            "anonymous_scene_refs": list(registry.anonymous_scene_refs),
            "registry_digest": registry.content_digest,
            "version_resolution": registry.resolution.public(),
            "blockers": list(registry.resolution.blockers),
        }
    adapter = MolstarChiralityHostAdapter(paths, registry)
    health = adapter.health()
    blockers = ["source_spec_local_only"]
    if health.status != "ready":
        blockers.append("playwright_runtime_unavailable")
    return {
        "artifact_kind": "chirality_host_diagnostic",
        "adapter_id": ADAPTER_ID,
        "status": "runtime_ready_local_only" if health.status == "ready" else "blocked",
        "runtime_ready": health.status == "ready",
        "production_ready": False,
        "health": {"status": health.status, "detail": health.detail},
        "available_operations": sorted(PILOT_OPERATIONS),
        "anonymous_scene_refs": list(registry.anonymous_scene_refs),
        "registry_digest": registry.content_digest,
        "version_resolution": registry.resolution.public(),
        "blockers": sorted(blockers),
    }


def _private_0600(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ViewerAdapterError(f"{label} must be a regular private file")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ViewerAdapterError(f"cannot inspect {label} permissions") from exc
    if mode != 0o600:
        raise ViewerAdapterError(f"{label} permissions must be 0600")


def _schema_fields(section: str) -> set[str]:
    value = CHIRALITY_SESSION_EVIDENCE_SCHEMA.get(section)
    if not isinstance(value, Mapping):
        raise ViewerAdapterError("controlled session evidence schema is malformed")
    fields = value.get("required_body_fields")
    if not isinstance(fields, list) or not all(isinstance(field, str) and field for field in fields):
        raise ViewerAdapterError("controlled session evidence schema is malformed")
    return set(fields)


def _attested_profile(paths: WorkspacePaths, body: Mapping[str, Any]) -> dict[str, Any]:
    profile_path = body.get("viewer_profile_path")
    profile_digest = body.get("viewer_profile_digest")
    if not isinstance(profile_path, str) or not profile_path:
        raise ViewerAdapterError("viewer session attestation lacks a persisted ViewerProfile path")
    try:
        require_digest(profile_digest, "viewer session ViewerProfile digest")
        profile = load_viewer_profile(paths, profile_path)
        profile = revalidate_viewer_profile(profile)
    except (InputError, StaleInputError) as exc:
        raise ViewerAdapterError(f"viewer session profile cannot be revalidated: {exc}") from exc
    profile_body = profile.get("body")
    if not isinstance(profile_body, Mapping):
        raise ViewerAdapterError("viewer session ViewerProfile is malformed")
    expected = {
        "viewer_profile_digest": profile["content_digest"],
        "registry_digest": profile_body.get("scene_registry_digest"),
        "host_build_digest": profile_body.get("host_build_digest"),
        "session_evidence_schema_digest": profile_body.get("session_evidence_schema_digest"),
        "allowed_operations": profile_body.get("allowed_operations"),
    }
    if any(body.get(key) != value for key, value in expected.items()):
        raise ViewerAdapterError("viewer session attestation does not match the persisted ViewerProfile")
    if body.get("adapter_id") != ADAPTER_ID or body.get("backend_kind") != "molstar_browser_host_v1":
        raise ViewerAdapterError("viewer session attestation host identity is invalid")
    if body.get("allowed_operations") != sorted(PILOT_OPERATIONS):
        raise ViewerAdapterError("viewer session attestation operation scope is invalid")
    return profile


def verify_viewer_evidence_chain(
    paths: WorkspacePaths,
    session_id: str,
    *,
    viewer_profile_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a profile-bound private session, render, and receipt chain."""
    safe_id = safe_component(session_id, "viewer session id")
    directory = require_within(paths.private / "viewer" / "sessions", paths.private / "viewer" / "sessions" / safe_id)
    if not directory.is_dir() or directory.is_symlink():
        raise ViewerAdapterError("viewer session evidence directory is unavailable")
    attestation_path = directory / "session-attestation.json"
    _private_0600(attestation_path, "viewer session attestation")
    attestation_value = read_json(attestation_path)
    if not isinstance(attestation_value, Mapping):
        raise ViewerAdapterError("viewer session attestation is malformed")
    try:
        attestation = verify_envelope(attestation_value, kind="viewer_session_attestation")
    except InputError as exc:
        raise ViewerAdapterError(f"viewer session attestation failed verification: {exc}") from exc
    if attestation["body"].get("session_id") != safe_id:
        raise ViewerAdapterError("viewer session attestation is bound to another session")
    attestation_body = attestation["body"]
    if not isinstance(attestation_body, Mapping) or set(attestation_body) != _schema_fields("session_attestation"):
        raise ViewerAdapterError("viewer session attestation does not satisfy the controlled evidence schema")
    profile = _attested_profile(paths, attestation_body)
    if viewer_profile_path is not None:
        try:
            expected_profile_path = require_within(paths.viewer_profiles, viewer_profile_path, "viewer profile")
        except InputError as exc:
            raise ViewerAdapterError(str(exc)) from exc
        if expected_profile_path != Path(str(attestation_body["viewer_profile_path"])).resolve():
            raise ViewerAdapterError("viewer session attestation is bound to another ViewerProfile path")
    previous: str | None = None
    previous_visible_rgb_digest: str | None = None
    count = 0
    observed_operations: list[str] = []
    for receipt_path in sorted(directory.glob("receipt-*.json")):
        _private_0600(receipt_path, "viewer action receipt")
        value = read_json(receipt_path)
        if not isinstance(value, Mapping):
            raise ViewerAdapterError("viewer action receipt is malformed")
        try:
            receipt = verify_envelope(value, kind="viewer_action_receipt")
        except InputError as exc:
            raise ViewerAdapterError(f"viewer action receipt failed verification: {exc}") from exc
        body = receipt["body"]
        if not isinstance(body, Mapping) or set(body) != _schema_fields("action_receipt"):
            raise ViewerAdapterError("viewer action receipt does not satisfy the controlled evidence schema")
        if body.get("session_id") != safe_id or body.get("session_attestation_digest") != attestation["content_digest"]:
            raise ViewerAdapterError("viewer action receipt is bound to another session")
        for key in (
            "viewer_profile_digest",
            "registry_digest",
            "host_build_digest",
            "session_evidence_schema_digest",
        ):
            if body.get(key) != attestation_body.get(key):
                raise ViewerAdapterError("viewer action receipt does not match the session profile evidence")
        if body.get("sequence") != count or body.get("previous_receipt_digest") != previous:
            raise ViewerAdapterError("viewer action receipt chain is discontinuous")
        if body.get("operation") not in PILOT_OPERATIONS:
            raise ViewerAdapterError("viewer action receipt operation is outside the controlled session")
        render_file = body.get("render_file")
        if not isinstance(render_file, str) or render_file != f"render-{count:06d}.png":
            raise ViewerAdapterError("viewer action receipt render binding is malformed")
        render_path = require_within(directory, directory / render_file, "viewer render")
        if not render_path.is_file() or render_path.is_symlink():
            raise ViewerAdapterError("viewer action receipt render is unavailable")
        render_bytes = render_path.read_bytes()
        if digest_bytes(render_bytes) != body.get("render_digest"):
            raise ViewerAdapterError("viewer action receipt render digest does not match")
        visible_rgb_digest = _validate_render_png(render_bytes)
        if body.get("operation") == "rotate" and previous_visible_rgb_digest == visible_rgb_digest:
            raise ViewerAdapterError("viewer rotate receipt did not produce changed rendered content")
        _controlled_camera(body.get("camera"))
        previous = receipt["content_digest"]
        previous_visible_rgb_digest = visible_rgb_digest
        observed_operations.append(str(body["operation"]))
        count += 1
    closed_path = directory / "closed.json"
    _private_0600(closed_path, "viewer session closure")
    closed = read_json(closed_path)
    if not isinstance(closed, Mapping):
        raise ViewerAdapterError("viewer session closure record is malformed")
    closure = CHIRALITY_SESSION_EVIDENCE_SCHEMA.get("closure")
    expected_closure = closure.get("required_fields") if isinstance(closure, Mapping) else None
    if not isinstance(expected_closure, list) or set(closed) != set(expected_closure):
        raise ViewerAdapterError("viewer session closure does not satisfy the controlled evidence schema")
    if closed.get("session_attestation_digest") != attestation["content_digest"]:
        raise ViewerAdapterError("viewer closure is bound to another session")
    if closed.get("action_count") != count or closed.get("final_receipt_digest") != previous:
        raise ViewerAdapterError("viewer closure does not match the evidence chain")
    return {
        "artifact_kind": "viewer_evidence_chain_verification",
        "session_id": safe_id,
        "status": "verified",
        "action_count": count,
        "session_attestation_digest": attestation["content_digest"],
        "final_receipt_digest": previous,
        "viewer_profile_digest": profile["content_digest"],
        "registry_digest": attestation_body["registry_digest"],
        "host_build_digest": attestation_body["host_build_digest"],
        "session_evidence_schema_digest": attestation_body["session_evidence_schema_digest"],
        "operations": observed_operations,
    }


def persist_chirality_smoke_receipt(
    paths: WorkspacePaths,
    *,
    viewer_profile_path: str | Path,
    session_id: str,
) -> dict[str, Any]:
    """Record a completed render/rotate smoke bound to a real ViewerProfile."""
    safe_id = safe_component(session_id, "viewer session id")
    verification = verify_viewer_evidence_chain(
        paths,
        session_id,
        viewer_profile_path=viewer_profile_path,
    )
    if verification["operations"] != ["render", "rotate"]:
        raise ViewerAdapterError("chirality smoke must contain exactly render followed by rotate")
    profile = load_viewer_profile(paths, viewer_profile_path)
    profile = revalidate_viewer_profile(profile)
    profile_body = profile["body"]
    body = {
        "viewer_profile_path": str(require_within(paths.viewer_profiles, viewer_profile_path, "viewer profile")),
        "viewer_profile_digest": profile["content_digest"],
        "session_id": safe_id,
        "session_attestation_digest": verification["session_attestation_digest"],
        "final_receipt_digest": verification["final_receipt_digest"],
        "action_count": verification["action_count"],
        "operations": verification["operations"],
        "registry_digest": profile_body["scene_registry_digest"],
        "host_build_digest": profile_body["host_build_digest"],
        "session_evidence_schema_digest": profile_body["session_evidence_schema_digest"],
        "release_eligible": False,
    }
    receipt = make_envelope(
        kind="chirality_viewer_smoke_receipt",
        identifier=f"chirality-smoke-{profile['content_digest'].split(':', 1)[1][:16]}-{safe_id}",
        status="passed_local_only",
        body=body,
        producer="qagen/0.1.0:chirality_smoke",
    )
    output = paths.private / "viewer" / "smokes" / (
        f"{profile['content_digest'].split(':', 1)[1][:16]}-{safe_id}-{receipt['content_digest'].split(':', 1)[1][:16]}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(output.parent, 0o700)
    except OSError:
        pass
    atomic_write_json(output, receipt, private=True)
    _private_0600(output, "chirality smoke receipt")
    return {"smoke_receipt_path": str(output), "smoke_receipt_digest": receipt["content_digest"], "receipt": receipt}


def build_attested_provider_handoff(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    viewer_profile_digest: str,
    anonymous_scene_ref: str,
    request_id: str,
) -> dict[str, Any]:
    """Capture the one bounded visual handoff allowed to a provider pilot.

    The host, rather than a model, creates the observations and image bytes.
    The returned data URLs are intentionally transient caller input.  The
    durable handoff contains only attestation and receipt references, while
    the image bytes remain in the private viewer-session directory.
    """
    require_digest(viewer_profile_digest, "provider handoff viewer profile digest")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ViewerAdapterError("provider handoff request_id is required")
    registry = SceneRegistry.load(paths, registry_path)
    if not registry.resolution.resolved:
        raise ViewerAdapterError("provider handoff requires a resolved private SceneRegistry")
    scene_ref = validate_anonymous_scene_ref(anonymous_scene_ref)
    if scene_ref not in registry.anonymous_scene_refs:
        raise ViewerAdapterError("provider handoff scene is not registered")

    adapter = MolstarChiralityHostAdapter(paths, registry)
    session: AdapterSession | None = None
    closed = False
    try:
        session = adapter.open_session(
            anonymous_scene_ref=scene_ref,
            request={
                "request_id": request_id.strip(),
                "viewer_profile_digest": viewer_profile_digest,
                "allowed_operations": sorted(PILOT_OPERATIONS),
            },
        )
        # A fixed render followed by a bounded rotation is enough to attest
        # that the model receives actual controlled visual evidence, without
        # allowing it to choose arbitrary host actions.
        observations = [
            adapter.perform(session=session, operation="render", arguments={}),
            adapter.perform(
                session=session,
                operation="rotate",
                arguments={"d_azimuth_deg": 35, "d_elevation_deg": 15, "d_roll_deg": 0},
            ),
        ]
        adapter.close_session(session=session)
        closed = True
        chain = verify_viewer_evidence_chain(paths, session.session_id)
    finally:
        if session is not None and not closed:
            try:
                adapter.close_session(session=session)
            except Exception:
                pass
        adapter.shutdown()

    directory = paths.private / "viewer" / "sessions" / safe_component(session.session_id, "viewer session id")
    if not directory.is_dir() or directory.is_symlink():
        raise ViewerAdapterError("provider handoff session directory is unavailable")
    attestation_value = read_json(directory / "session-attestation.json")
    if not isinstance(attestation_value, Mapping):
        raise ViewerAdapterError("provider handoff session attestation is malformed")
    try:
        attestation = verify_envelope(attestation_value, kind="viewer_session_attestation")
    except InputError as exc:
        raise ViewerAdapterError(f"provider handoff session attestation failed verification: {exc}") from exc
    if (
        attestation["content_digest"] != chain["session_attestation_digest"]
        or attestation["body"].get("viewer_profile_digest") != viewer_profile_digest
        or attestation["body"].get("anonymous_scene_ref") != scene_ref
        or attestation["body"].get("registry_digest") != registry.content_digest
    ):
        raise ViewerAdapterError("provider handoff attestation binding changed")

    safe_observations: list[dict[str, Any]] = []
    rendered_views: list[dict[str, str]] = []
    receipt_digests: list[str] = []
    for expected_sequence, observation in enumerate(observations):
        payload = observation.payload
        action = payload.get("action_receipt") if isinstance(payload, Mapping) else None
        if not isinstance(action, Mapping) or action.get("sequence") != expected_sequence:
            raise ViewerAdapterError("provider handoff observation lacks its host receipt")
        receipt_digest = action.get("receipt_digest")
        require_digest(receipt_digest, "provider handoff action receipt digest")
        receipt_value = read_json(directory / f"receipt-{expected_sequence:06d}.json")
        if not isinstance(receipt_value, Mapping):
            raise ViewerAdapterError("provider handoff action receipt is malformed")
        try:
            receipt = verify_envelope(receipt_value, kind="viewer_action_receipt")
        except InputError as exc:
            raise ViewerAdapterError(f"provider handoff action receipt failed verification: {exc}") from exc
        body = receipt["body"]
        if (
            receipt["content_digest"] != receipt_digest
            or body.get("session_attestation_digest") != attestation["content_digest"]
            or body.get("sequence") != expected_sequence
            or body.get("operation") != observation.operation
            or body.get("render_digest") != payload.get("render_digest")
        ):
            raise ViewerAdapterError("provider handoff observation does not match its host receipt")
        render_name = body.get("render_file")
        if not isinstance(render_name, str) or render_name != f"render-{expected_sequence:06d}.png":
            raise ViewerAdapterError("provider handoff render binding is malformed")
        render_path = require_within(directory, directory / render_name, "provider handoff render")
        if render_path.is_symlink() or not render_path.is_file():
            raise ViewerAdapterError("provider handoff render is unavailable")
        render_bytes = render_path.read_bytes()
        if digest_bytes(render_bytes) != body.get("render_digest"):
            raise ViewerAdapterError("provider handoff render digest changed")
        receipt_digests.append(receipt_digest)
        safe_observations.append(
            {
                "operation": observation.operation,
                "sequence": expected_sequence,
                "receipt_digest": receipt_digest,
                "render_digest": body["render_digest"],
            }
        )
        rendered_views.append(
            {
                "receipt_digest": receipt_digest,
                "render_digest": body["render_digest"],
                "mime_type": "image/png",
                "data_url": "data:image/png;base64," + base64.b64encode(render_bytes).decode("ascii"),
            }
        )
    if chain["final_receipt_digest"] != receipt_digests[-1] or chain["action_count"] != len(receipt_digests):
        raise ViewerAdapterError("provider handoff receipt chain is incomplete")

    handoff_body = {
        "artifact_kind": "attested_chirality_provider_handoff",
        "viewer_profile_digest": viewer_profile_digest,
        "scene_registry_digest": registry.content_digest,
        "anonymous_scene_ref": scene_ref,
        "session_id": session.session_id,
        "session_attestation_digest": attestation["content_digest"],
        "receipt_digests": receipt_digests,
        "receipt_chain_digest": digest(receipt_digests),
        "solver_safe_observations": safe_observations,
        "render_digests": [view["render_digest"] for view in rendered_views],
        "release_eligible": False,
    }
    handoff = make_envelope(
        kind="provider_viewer_evidence_handoff",
        identifier=f"provider-handoff-{session.session_id}",
        status="attested",
        body=handoff_body,
        producer="qagen/0.1.0:molstar_chirality_host",
    )
    handoff_path = directory / "provider-handoff.json"
    atomic_write_json(handoff_path, handoff, private=True)
    return {
        "handoff_path": str(handoff_path),
        "handoff_digest": handoff["content_digest"],
        "viewer_profile_digest": viewer_profile_digest,
        "scene_registry_digest": registry.content_digest,
        "anonymous_scene_ref": scene_ref,
        "session_attestation_digest": attestation["content_digest"],
        "receipt_digests": receipt_digests,
        "receipt_chain_digest": handoff_body["receipt_chain_digest"],
        "solver_safe_observations": safe_observations,
        "rendered_views": rendered_views,
    }
