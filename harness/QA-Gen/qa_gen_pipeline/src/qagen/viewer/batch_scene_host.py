"""Private, browser-backed replay host for compiled-selection scene receipts.

This module intentionally sits beside, rather than inside, the chirality live
adapter.  It can attest that a frozen selection asset rendered through the
real bundled Mol* or NiiVue application, but it does not create a solver
session and is not a provider-evidence handoff.  Inputs are trusted private
scene snapshots; every returned value is a digest or a fixed blocker code.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import mimetypes
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
import threading
from time import monotonic
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import unquote, urlencode, urlsplit

from ..contracts import digest, digest_bytes, require_digest
from ..errors import InputError
from .adapter import ViewerAdapterError
from .chirality_host import _browser_executable_candidates, _validate_render_png
from .contracts import validate_anonymous_scene_ref


HOST_ID = "compiled-selection-batch-browser-replay-v1"
_VIEWER_EXECUTABLE_ENV = "QAGEN_VIEWER_EXECUTABLE_PATH"
ViewerFamily = Literal["molstar", "niivue"]

# Playwright's ``page.evaluate`` does not inherit ``page.set_default_timeout``.
# Browser-side promises therefore need their own bounded completion path before
# they can be used in the fail-closed replay host.
_FRAME_SETTLE_TIMEOUT_MS = 5_000
_MOLSTAR_PRESET_TIMEOUT_MS = 15_000
_MCP_CALL_TIMEOUT_MS = 15_000

_MOLSTAR_OPERATIONS = frozenset(
    {
        "inspect",
        "isolate",
        "rotate",
        "zoom",
        "render",
        "measure_distance",
        "measure_dihedral",
    }
)
_NIIVUE_OPERATIONS = frozenset(
    {"inspect", "slice", "window_level", "bbox", "overlay_compare", "render", "rotate"}
)
_ALL_OPERATIONS = _MOLSTAR_OPERATIONS | _NIIVUE_OPERATIONS
# The host never accepts a caller-supplied camera delta.  It applies one
# conservative zoom-in step and clamps it to the loaded scene's own radius.
_MOLSTAR_ZOOM_FACTOR = 0.82
_MOLSTAR_ZOOM_MIN_RADIUS_MULTIPLIER = 0.25
_MOLSTAR_ZOOM_MAX_RADIUS_MULTIPLIER = 20.0
_NATIVE_DIHEDRAL_DISPLAY_RESOLUTION_DEG = 0.1
_BLOCKER_CODES = frozenset(
    {
        "asset_capture_limit_exceeded",
        "asset_integrity_mismatch",
        "asset_unavailable",
        "host_contract_invalid",
        "unsupported_measure_area",
        "unsupported_measure_dihedral",
        "unsupported_measure_distance",
        "unsupported_measure_volume",
        "viewer_host_unavailable",
        "viewer_operation_failed",
        "viewer_operation_not_allowed",
        "viewer_page_load_failed",
        "viewer_render_invalid",
        "viewer_runtime_unavailable",
    }
)
_STATIC_PREFIX = "/viewer/"
_FORBIDDEN_STATIC_COMPONENTS = frozenset(
    {"provenance", "sources", "tasks", ".git", ".qagen"}
)
_MAX_SCENE_BYTES = 256 * 1024 * 1024


class BatchSceneReplayError(ViewerAdapterError):
    """A replay failure whose public representation is a fixed blocker code."""

    def __init__(self, code: str) -> None:
        if code not in _BLOCKER_CODES:
            code = "viewer_operation_failed"
        self.code = code
        super().__init__(code)


def _explicit_browser_executable() -> tuple[Path, str] | None:
    """Resolve an explicitly pinned local browser without accepting arbitrary paths."""

    raw = os.environ.get(_VIEWER_EXECUTABLE_ENV, "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if (
        not candidate.is_absolute()
        or candidate.is_symlink()
        or not candidate.is_file()
        or not os.access(candidate, os.X_OK)
    ):
        raise BatchSceneReplayError("viewer_runtime_unavailable")
    resolved = candidate.resolve(strict=True)
    cache_root = (Path.home() / "Library" / "Caches" / "ms-playwright").resolve()
    system_chrome = Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    ).resolve()
    try:
        resolved.relative_to(cache_root)
        return resolved, "playwright_cache_explicit"
    except ValueError:
        pass
    if resolved == system_chrome:
        return resolved, "system_chrome_explicit"
    raise BatchSceneReplayError("viewer_runtime_unavailable")


def explicit_browser_installation_identity() -> dict[str, str]:
    """Return a path-free identity for the explicitly pinned viewer runtime."""

    resolved = _explicit_browser_executable()
    if resolved is None:
        raise BatchSceneReplayError("viewer_runtime_unavailable")
    path, source = resolved
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = completed.stdout.strip()
        if not version or len(version) > 256:
            raise ValueError("browser version is invalid")
        binary_digest = digest_bytes(path.read_bytes())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise BatchSceneReplayError("viewer_runtime_unavailable") from exc
    return {
        "environment": _VIEWER_EXECUTABLE_ENV,
        "browser_source": source,
        "browser_binary_digest": binary_digest,
        "browser_version": version,
    }


@dataclass(frozen=True)
class BatchReplayScene:
    """Private immutable asset snapshot handed to the browser replay host."""

    anonymous_scene_ref: str
    viewer_family: ViewerFamily
    primary_bytes: bytes
    primary_digest: str
    primary_format: str
    overlay_bytes: bytes | None = None
    overlay_digest: str | None = None
    mask_id: int | None = None

    def __post_init__(self) -> None:
        validate_anonymous_scene_ref(self.anonymous_scene_ref)
        if self.viewer_family not in {"molstar", "niivue"}:
            raise InputError("batch replay scene viewer family is unsupported")
        if (
            not isinstance(self.primary_bytes, bytes)
            or not self.primary_bytes
            or len(self.primary_bytes) > _MAX_SCENE_BYTES
        ):
            raise InputError("batch replay scene primary snapshot is invalid")
        require_digest(self.primary_digest, "batch replay scene primary digest")
        if digest_bytes(self.primary_bytes) != self.primary_digest:
            raise InputError(
                "batch replay scene primary digest does not match its snapshot"
            )
        if not isinstance(self.primary_format, str) or self.primary_format not in {
            "cif",
            "bcif",
            "pdb",
            "mol",
            "sdf",
            "xyz",
            "nii",
            "nii.gz",
        }:
            raise InputError("batch replay scene primary format is unsupported")
        if (self.overlay_bytes is None) != (self.overlay_digest is None):
            raise InputError(
                "batch replay scene overlay bytes and digest must be supplied together"
            )
        if self.overlay_bytes is not None:
            if not self.overlay_bytes or len(self.overlay_bytes) > _MAX_SCENE_BYTES:
                raise InputError("batch replay scene overlay snapshot is invalid")
            assert self.overlay_digest is not None
            require_digest(self.overlay_digest, "batch replay scene overlay digest")
            if digest_bytes(self.overlay_bytes) != self.overlay_digest:
                raise InputError(
                    "batch replay scene overlay digest does not match its snapshot"
                )
        if self.mask_id is not None and (
            isinstance(self.mask_id, bool)
            or not isinstance(self.mask_id, int)
            or self.mask_id < 0
        ):
            raise InputError(
                "batch replay scene mask_id must be a non-negative integer"
            )

    @property
    def asset_set_digest(self) -> str:
        assets: list[dict[str, str]] = [
            {"role": "primary", "content_digest": self.primary_digest}
        ]
        if self.overlay_digest is not None:
            assets.append({"role": "mask", "content_digest": self.overlay_digest})
        return digest(assets)


@dataclass(frozen=True)
class BatchReplayResult:
    """Sanitized per-scene replay result safe to persist in a receipt."""

    anonymous_scene_ref: str
    viewer_family: ViewerFamily
    replayed_operations: tuple[str, ...]
    evidence: tuple[Mapping[str, str], ...]
    status: Literal["attested", "blocked"]
    blocker_codes: tuple[str, ...]

    def as_receipt_body(self) -> dict[str, Any]:
        return {
            "anonymous_scene_ref": self.anonymous_scene_ref,
            "viewer_family": self.viewer_family,
            "host_id": HOST_ID,
            "replayed_operations": list(self.replayed_operations),
            "evidence": [dict(item) for item in self.evidence],
            "status": self.status,
            "blocker_codes": list(self.blocker_codes),
        }


@dataclass(frozen=True)
class _RegisteredScene:
    primary: bytes
    primary_format: str
    overlay: bytes | None
    mask_id: int | None


class _BatchLoopbackAssetServer:
    """Loopback-only static viewer server with one-time opaque scene tokens."""

    def __init__(self, package_root: Path) -> None:
        root = package_root.expanduser()
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise InputError(
                "viewer package root must be an absolute non-symlink directory"
            )
        self.package_root = root.resolve(strict=True)
        self._scenes: dict[str, _RegisteredScene] = {}
        self._lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "qagen-batch-viewer/1"

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self, head_only=False)

            def do_HEAD(self) -> None:  # noqa: N802
                owner._handle(self, head_only=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="qagen-batch-viewer-loopback",
            daemon=True,
        )
        self._thread.start()

    @property
    def origin(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def register(self, scene: BatchReplayScene) -> str:
        token = secrets.token_urlsafe(24)
        registered = _RegisteredScene(
            primary=scene.primary_bytes,
            primary_format=scene.primary_format,
            overlay=scene.overlay_bytes,
            mask_id=scene.mask_id,
        )
        with self._lock:
            self._scenes[token] = registered
        return token

    def unregister(self, token: str) -> None:
        with self._lock:
            self._scenes.pop(token, None)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _send_headers(
        self, handler: BaseHTTPRequestHandler, content_type: str, length: int
    ) -> None:
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

    def _deny(
        self, handler: BaseHTTPRequestHandler, status: HTTPStatus = HTTPStatus.NOT_FOUND
    ) -> None:
        body = b"not found\n"
        handler.send_response(status)
        self._send_headers(handler, "text/plain; charset=utf-8", len(body))
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _static_file(self, url_path: str) -> tuple[bytes, str]:
        if not url_path.startswith(_STATIC_PREFIX):
            raise BatchSceneReplayError("asset_unavailable")
        relative = url_path.removeprefix("/")
        parts = PurePosixPath(relative).parts
        if not parts or any(
            part in {"", ".", ".."} or part in _FORBIDDEN_STATIC_COMPONENTS
            for part in parts
        ):
            raise BatchSceneReplayError("asset_unavailable")
        candidate = self.package_root.joinpath(*parts)
        current = self.package_root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise BatchSceneReplayError("asset_unavailable")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self.package_root)
        except ValueError as exc:
            raise BatchSceneReplayError("asset_unavailable") from exc
        mode = resolved.stat().st_mode
        if not stat.S_ISREG(mode):
            raise BatchSceneReplayError("asset_unavailable")
        content = resolved.read_bytes()
        content_type = (
            mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        )
        return content, content_type

    def _scene_file(self, url_path: str) -> tuple[bytes, str]:
        parts = PurePosixPath(url_path).parts
        # /__qagen_scene/<opaque token>/<primary|mask>.<format>.  The opaque
        # filename suffix is required because NiiVue selects its loader from
        # the URL extension; it is never a source path or asset identifier.
        if len(parts) != 4 or parts[1] != "__qagen_scene":
            raise BatchSceneReplayError("asset_unavailable")
        token = parts[2]
        resource = parts[3]
        kind, separator, suffix = resource.partition(".")
        if kind not in {"primary", "mask"} or not separator or not suffix:
            raise BatchSceneReplayError("asset_unavailable")
        with self._lock:
            scene = self._scenes.get(token)
        if scene is None:
            raise BatchSceneReplayError("asset_unavailable")
        expected_suffix = scene.primary_format
        if suffix != expected_suffix:
            raise BatchSceneReplayError("asset_unavailable")
        if kind == "mask":
            if scene.overlay is None:
                raise BatchSceneReplayError("asset_unavailable")
            return scene.overlay, "application/octet-stream"
        if scene.primary_format in {"cif", "bcif"}:
            return scene.primary, "chemical/x-cif"
        if scene.primary_format in {"pdb", "mol", "sdf", "xyz"}:
            return scene.primary, "text/plain; charset=utf-8"
        return scene.primary, "application/octet-stream"

    def _handle(self, handler: BaseHTTPRequestHandler, *, head_only: bool) -> None:
        try:
            if not ipaddress.ip_address(handler.client_address[0]).is_loopback:
                self._deny(handler, HTTPStatus.FORBIDDEN)
                return
            if handler.headers.get("Host") != urlsplit(self.origin).netloc:
                self._deny(handler, HTTPStatus.FORBIDDEN)
                return
            path = unquote(urlsplit(handler.path).path)
            if path.startswith("/__qagen_scene/"):
                content, content_type = self._scene_file(path)
            else:
                content, content_type = self._static_file(path)
            handler.send_response(HTTPStatus.OK)
            self._send_headers(handler, content_type, len(content))
            if not head_only:
                handler.wfile.write(content)
        except (OSError, ValueError, BatchSceneReplayError):
            self._deny(handler)


def _decode_screenshot(value: Any) -> tuple[bytes, str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("png_base64"), str):
        raise BatchSceneReplayError("viewer_render_invalid")
    try:
        png = base64.b64decode(value["png_base64"], validate=True)
    except (TypeError, ValueError) as exc:
        raise BatchSceneReplayError("viewer_render_invalid") from exc
    _validate_render_png(png)
    return png, digest_bytes(png)


def _safe_result_digest(value: Any) -> str:
    """Hash private browser output without allowing it into the receipt body."""

    return digest(value)


class ControlledBatchViewerReplayHost:
    """Replay one frozen scene through a real packaged viewer with no network."""

    def __init__(
        self, package_root: str | Path, *, molstar_representation_fallback: bool = False
    ) -> None:
        if not isinstance(molstar_representation_fallback, bool):
            raise InputError("molstar representation fallback must be boolean")
        self._package_root = Path(package_root)
        self._molstar_representation_fallback = molstar_representation_fallback
        self._server: _BatchLoopbackAssetServer | None = None
        self._playwright: Any = None
        self._browser: Any = None

    def health(self) -> dict[str, str]:
        try:
            from playwright.sync_api import sync_playwright

            explicit = _explicit_browser_executable()
            if explicit is not None:
                return {
                    "status": "ready",
                    "host_id": HOST_ID,
                    "detail": "explicit_local_browser_ready",
                }
            runtime = sync_playwright().start()
            try:
                candidates = _browser_executable_candidates(runtime.chromium)
            finally:
                runtime.stop()
        except Exception:
            return {
                "status": "unavailable",
                "host_id": HOST_ID,
                "detail": "playwright_unavailable",
            }
        if not candidates:
            return {
                "status": "unavailable",
                "host_id": HOST_ID,
                "detail": "chromium_unavailable",
            }
        return {"status": "ready", "host_id": HOST_ID, "detail": "local_browser_ready"}

    def _ensure_runtime(self) -> None:
        if self._browser is not None:
            return
        if self.health()["status"] != "ready":
            raise BatchSceneReplayError("viewer_runtime_unavailable")
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            explicit = _explicit_browser_executable()
            candidates = _browser_executable_candidates(self._playwright.chromium)
            if explicit is not None:
                candidates = [
                    str(explicit[0]),
                    *[item for item in candidates if item != str(explicit[0])],
                ]
            for executable in candidates:
                try:
                    options: dict[str, Any] = {
                        "headless": True,
                        "args": [
                            "--enable-unsafe-swiftshader",
                            "--use-angle=swiftshader",
                        ],
                    }
                    if executable is not None:
                        options["executable_path"] = executable
                    self._browser = self._playwright.chromium.launch(**options)
                    break
                except Exception:
                    self._browser = None
            if self._browser is None:
                raise BatchSceneReplayError("viewer_runtime_unavailable")
            self._server = _BatchLoopbackAssetServer(self._package_root)
        except BatchSceneReplayError:
            self.shutdown()
            raise
        except Exception as exc:
            self.shutdown()
            raise BatchSceneReplayError("viewer_runtime_unavailable") from exc

    @staticmethod
    def _call(page: Any, name: str, args: Mapping[str, Any]) -> Any:
        return page.evaluate(
            """async ({ name, args, timeoutMs }) => {
              const timeout = Symbol('qagen_mcp_timeout');
              const result = await Promise.race([
                window.ScienceVisionViewerMCP.callTool(name, args),
                new Promise(resolve => setTimeout(() => resolve(timeout), timeoutMs)),
              ]);
              if (result === timeout) throw new Error('qagen_mcp_timeout');
              return result;
            }""",
            {"name": name, "args": dict(args), "timeoutMs": _MCP_CALL_TIMEOUT_MS},
        )

    @staticmethod
    def _capture(page: Any) -> tuple[bytes, str]:
        # The packaged screenshot helper routes through a WebGL ReadPixels
        # path that can stall under headless SwiftShader.  Playwright captures
        # the same visible page/canvas pixels without exposing an asset or a
        # browser-side raw scene result to the receipt boundary.
        try:
            png = page.screenshot(type="png")
            _validate_render_png(png)
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_render_invalid") from exc
        return png, digest_bytes(png)

    @classmethod
    def _await_animation_frames(cls, page: Any) -> None:
        """Allow a camera or renderer state change to commit to the canvas."""

        try:
            settled = page.evaluate(
                """async timeoutMs => {
                  const timeout = Symbol('qagen_frame_timeout');
                  const result = await Promise.race([
                    new Promise(resolve => requestAnimationFrame(
                      () => requestAnimationFrame(() => resolve('painted'))
                    )),
                    new Promise(resolve => setTimeout(() => resolve(timeout), timeoutMs)),
                  ]);
                  return result === timeout ? 'timed_out' : result;
                }""",
                _FRAME_SETTLE_TIMEOUT_MS,
            )
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_render_invalid") from exc
        if settled != "painted":
            raise BatchSceneReplayError("viewer_render_invalid")

    @classmethod
    def _await_stable_nonblank_render(cls, page: Any) -> None:
        """Wait for the initialized viewer to paint before replaying operations.

        ``ready`` proves that the browser-side MCP bridge was installed, not
        that its WebGL/DOM surface has painted a meaningful frame.  A two-frame
        barrier avoids treating a race at initial paint as a scientific viewer
        failure.  The capture is deliberately discarded: it is a host-health
        check, while each replay operation below retains its own digest-only
        render evidence.
        """

        try:
            cls._await_animation_frames(page)
            cls._capture(page)
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_render_invalid") from exc

    @classmethod
    def _molstar_focus_scene(cls, page: Any) -> None:
        """Use Mol*'s native camera manager to frame the loaded scene.

        Batch scenes expose exactly one primary molecular structure.  Calling
        ``focusSphere`` keeps capture timing and framing within Mol* instead of
        deriving a camera transform in the host.  A multi-structure scene is
        rejected rather than choosing one structure implicitly.
        """

        try:
            result = page.evaluate(
                """() => {
                  const plugin = window.__sv_viewer?.plugin;
                  const structures = plugin?.managers?.structure?.hierarchy?.current?.structures;
                  if (!Array.isArray(structures) || structures.length !== 1) return { focused: false };
                  const sphere = structures[0]?.cell?.obj?.data?.boundary?.sphere;
                  if (!sphere || typeof plugin?.managers?.camera?.focusSphere !== 'function') {
                    return { focused: false };
                  }
                  plugin.managers.camera.focusSphere(sphere, { durationMs: 0 });
                  return { focused: true };
                }"""
            )
            if not isinstance(result, Mapping) or result.get("focused") is not True:
                raise BatchSceneReplayError("viewer_operation_failed")
            cls._await_animation_frames(page)
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_operation_failed") from exc

    @classmethod
    def _molstar_zoom(cls, page: Any) -> dict[str, bool]:
        """Apply one bounded zoom step through Mol*'s camera API.

        The fixed factor and radius-relative clamp deliberately leave no
        unbounded camera parameter for a worker or a page script to control.
        """

        try:
            result = page.evaluate(
                """({ factor, minRadiusMultiplier, maxRadiusMultiplier }) => {
                  const camera = window.__sv_viewer?.plugin?.canvas3d?.camera;
                  const snapshot = camera?.getSnapshot?.();
                  const vectorsAreFinite = value => Array.isArray(value)
                    && value.length === 3
                    && value.every(item => Number.isFinite(item));
                  if (!camera || !snapshot || typeof camera.setState !== 'function'
                    || !vectorsAreFinite(snapshot.position) || !vectorsAreFinite(snapshot.target)) {
                    return { zoomed: false };
                  }
                  const radius = Number(snapshot.radius);
                  if (!Number.isFinite(radius) || radius <= 0) return { zoomed: false };
                  const distance = Math.hypot(...snapshot.position.map(
                    (value, index) => value - snapshot.target[index]
                  ));
                  const minimum = Math.max(1e-3, radius * minRadiusMultiplier);
                  const maximum = Math.max(minimum, radius * maxRadiusMultiplier);
                  if (!Number.isFinite(distance) || distance <= minimum || distance > maximum) {
                    return { zoomed: false };
                  }
                  const nextDistance = Math.max(minimum, Math.min(maximum, distance * factor));
                  if (!(nextDistance < distance)) return { zoomed: false };
                  const scale = nextDistance / distance;
                  const position = snapshot.position.map(
                    (value, index) => snapshot.target[index] + (value - snapshot.target[index]) * scale
                  );
                  if (!vectorsAreFinite(position)) return { zoomed: false };
                  camera.setState({ position, target: [...snapshot.target] }, 0);
                  const after = camera.getSnapshot?.();
                  const afterDistance = after && vectorsAreFinite(after.position) && vectorsAreFinite(after.target)
                    ? Math.hypot(...after.position.map((value, index) => value - after.target[index]))
                    : Number.NaN;
                  return {
                    zoomed: Number.isFinite(afterDistance)
                      && afterDistance >= minimum
                      && afterDistance <= maximum
                      && afterDistance < distance,
                  };
                }""",
                {
                    "factor": _MOLSTAR_ZOOM_FACTOR,
                    "minRadiusMultiplier": _MOLSTAR_ZOOM_MIN_RADIUS_MULTIPLIER,
                    "maxRadiusMultiplier": _MOLSTAR_ZOOM_MAX_RADIUS_MULTIPLIER,
                },
            )
            if not isinstance(result, Mapping) or result.get("zoomed") is not True:
                raise BatchSceneReplayError("viewer_operation_failed")
            cls._await_animation_frames(page)
            return {"zoomed": True}
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_operation_failed") from exc

    @classmethod
    def _molstar_apply_polymer_and_ligand_preset(cls, page: Any) -> None:
        """Apply Mol*'s native high-contrast polymer/ligand representation.

        This is an explicit repair-only viewer path.  It delegates styling to
        Mol*'s registered preset, which uses a cartoon for polymer and a
        ball-and-stick representation for ligand detail.  The host does not
        synthesize geometry, colors, or camera projections.
        """

        try:
            result = page.evaluate(
                """async timeoutMs => {
                  const plugin = window.__sv_viewer?.plugin;
                  const entry = plugin?.managers?.structure?.hierarchy?.current?.structures?.[0];
                  if (!entry?.cell || typeof plugin?.builders?.structure?.representation?.applyPreset !== 'function') {
                    return { applied: false };
                  }
                  const timeout = Symbol('qagen_preset_timeout');
                  const outcome = await Promise.race([
                    plugin.builders.structure.representation.applyPreset(
                      entry.cell,
                      'preset-structure-representation-polymer-and-ligand'
                    ),
                    new Promise(resolve => setTimeout(() => resolve(timeout), timeoutMs)),
                  ]);
                  if (outcome === timeout) return { applied: false };
                  // The hierarchy list is updated asynchronously after the
                  // awaited preset task.  The subsequent focus/capture path
                  // is the authoritative renderer check.
                  return { applied: true };
                }""",
                _MOLSTAR_PRESET_TIMEOUT_MS,
            )
            if not isinstance(result, Mapping) or result.get("applied") is not True:
                raise BatchSceneReplayError("viewer_operation_failed")
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_operation_failed") from exc

    @staticmethod
    def _molstar_measure_distance(page: Any) -> Any:
        """Select two private, unambiguous atoms and use the bundled MCP tool.

        Selector discovery is intentionally browser-private.  Only a digest of
        the native tool response is retained by the caller.
        """

        return page.evaluate(
            """async () => {
              const viewer = window.__sv_viewer;
              const hierarchy = viewer?.plugin?.managers?.structure?.hierarchy;
              const structures = hierarchy?.current?.structures;
              const structure = structures?.[0]?.cell?.obj?.data;
              if (!structure?.units) throw new Error('structure_unavailable');
              const column = (value, index) => {
                const item = value?.value?.(index);
                return item == null || item === '.' || item === '?' ? '' : item;
              };
              const selectors = [];
              const loci = [];
              for (const unit of structure.units) {
                if (unit.kind !== 0 || !unit.model?.atomicHierarchy || !unit.elements) continue;
                const hierarchyData = unit.model.atomicHierarchy;
                const atoms = hierarchyData.atoms || {};
                const residues = hierarchyData.residues || {};
                const chains = hierarchyData.chains || {};
                const residueIndexFor = hierarchyData.residueAtomSegments?.index;
                const chainIndexFor = hierarchyData.chainAtomSegments?.index;
                const modelIndex = Array.isArray(structure.models) ? structure.models.indexOf(unit.model) : 0;
                for (let position = 0; position < unit.elements.length; position += 1) {
                  const rawElement = unit.elements[position];
                  const element = Number(rawElement);
                  if (loci.length < 2) {
                    loci.push({
                      kind: 'element-loci', structure,
                      elements: [{ unit, indices: Int32Array.of(position) }],
                    });
                  }
                  const residueIndex = residueIndexFor ? Number(residueIndexFor[element]) : -1;
                  const chainIndex = chainIndexFor ? Number(chainIndexFor[element]) : -1;
                  const chain = String(column(chains.auth_asym_id, chainIndex) || column(chains.label_asym_id, chainIndex));
                  const authSeq = Number(column(residues.auth_seq_id, residueIndex));
                  const labelSeq = Number(column(residues.label_seq_id, residueIndex));
                  const residue = Number.isFinite(authSeq) ? authSeq : labelSeq;
                  const atom = String(column(atoms.auth_atom_id, element) || column(atoms.label_atom_id, element));
                  if (!chain || !Number.isFinite(residue) || !atom) continue;
                  selectors.push({
                    structure_index: 0,
                    model_index: modelIndex,
                    unit_id: Number(unit.id),
                    chain_id: chain,
                    residue_number: residue,
                    atom_name: atom,
                    altloc: String(column(atoms.label_alt_id, element)),
                  });
                  if (selectors.length >= 2) break;
                }
                if (selectors.length >= 2 && loci.length >= 2) break;
              }
              if (selectors.length >= 2) {
                try {
                  return await window.ScienceVisionViewerMCP.callTool('viewer_measure_distance', {
                    a: selectors[0], b: selectors[1],
                  });
                } catch (_) {
                  // XYZ structures need no synthetic chain identifiers. Fall
                  // back to Mol*'s own measurement manager below.
                }
              }
              const manager = viewer?.plugin?.managers?.structure?.measurement;
              if (loci.length < 2 || !manager?.addDistance) throw new Error('two_atoms_unavailable');
              const annotation = await manager.addDistance(loci[0], loci[1]);
              if (!annotation) throw new Error('distance_annotation_unavailable');
              viewer.plugin.managers.camera.focusLoci(loci, { durationMs: 0 });
              return { annotation_created: true };
            }"""
        )

    @staticmethod
    def _molstar_measure_dihedral(page: Any) -> Any:
        """Use Mol*'s mature measurement manager, never a handwritten formula."""

        return page.evaluate(
            """async () => {
              const viewer = window.__sv_viewer;
              const hierarchy = viewer?.plugin?.managers?.structure?.hierarchy;
              const structures = hierarchy?.current?.structures;
              const structure = structures?.[0]?.cell?.obj?.data;
              const manager = viewer?.plugin?.managers?.structure?.measurement;
              if (!structure?.units || !manager?.addDihedral) throw new Error('dihedral_unavailable');
              const loci = [];
              for (const unit of structure.units) {
                if (unit.kind !== 0 || !unit.elements) continue;
                for (let position = 0; position < unit.elements.length; position += 1) {
                  loci.push({ kind: 'element-loci', structure, elements: [{ unit, indices: Int32Array.of(position) }] });
                  if (loci.length === 4) break;
                }
                if (loci.length === 4) break;
              }
              if (loci.length < 4) throw new Error('four_atoms_unavailable');
              const annotation = await manager.addDihedral(loci[0], loci[1], loci[2], loci[3]);
              if (!annotation) throw new Error('dihedral_annotation_unavailable');
              viewer.plugin.managers.camera.focusLoci(loci, { durationMs: 0 });
              return { annotation_created: true };
            }"""
        )

    @staticmethod
    def _xyz_dihedral_atom_indices(
        selector: Mapping[str, Any],
    ) -> tuple[int, tuple[int, int, int, int]]:
        """Validate the one reviewed host-private XYZ selector shape.

        The public MCP selector grammar intentionally does not expose raw XYZ
        atom indices.  They are resolved from opaque inventory IDs within the
        measurement host, then passed through this small boundary only.  This
        is not a general selector adapter.
        """

        if (
            set(selector) != {"type", "atoms"}
            or selector.get("type") != "atom_quadruplet"
        ):
            raise BatchSceneReplayError("host_contract_invalid")
        atoms = selector.get("atoms")
        if not isinstance(atoms, list) or len(atoms) != 4:
            raise BatchSceneReplayError("host_contract_invalid")
        structure_index: int | None = None
        indices: list[int] = []
        for atom in atoms:
            if not isinstance(atom, Mapping) or set(atom) != {
                "structure_index",
                "atom_index",
                "format",
            }:
                raise BatchSceneReplayError("host_contract_invalid")
            item_structure_index = atom.get("structure_index")
            atom_index = atom.get("atom_index")
            if (
                isinstance(item_structure_index, bool)
                or not isinstance(item_structure_index, int)
                or item_structure_index < 0
                or isinstance(atom_index, bool)
                or not isinstance(atom_index, int)
                or atom_index < 1
                or atom.get("format") != "xyz"
            ):
                raise BatchSceneReplayError("host_contract_invalid")
            if structure_index is None:
                structure_index = item_structure_index
            elif structure_index != item_structure_index:
                raise BatchSceneReplayError("host_contract_invalid")
            indices.append(atom_index)
        if structure_index is None or len(set(indices)) != 4:
            raise BatchSceneReplayError("host_contract_invalid")
        return structure_index, (indices[0], indices[1], indices[2], indices[3])

    @classmethod
    def _molstar_measure_xyz_dihedral(
        cls, page: Any, selector: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Measure one reviewed XYZ atom quartet inside the shipped viewer.

        The host delegates the numerical calculation to the immutable viewer
        package's measurement implementation.  It never reads XYZ bytes,
        coordinates, or molecular metadata itself, and only returns the
        rounded value shown by that measurement surface.
        """

        structure_index, atom_indices = cls._xyz_dihedral_atom_indices(selector)
        try:
            result = page.evaluate(
                """async ({ structureIndex, atomIndices, displayResolutionDeg }) => {
                  const viewer = window.__sv_viewer;
                  const structures = viewer?.plugin?.managers?.structure?.hierarchy?.current?.structures;
                  const manager = viewer?.plugin?.managers?.structure?.measurement;
                  const viewerMeasurement = window.ScienceVisionMolstarMeasurementMath;
                  const entry = Array.isArray(structures) ? structures[structureIndex] : null;
                  const structure = entry?.cell?.obj?.data;
                  if (!structure?.units || !manager?.addDihedral || typeof viewerMeasurement?.dihedral4 !== 'function') {
                    throw new Error('native_dihedral_unavailable');
                  }
                  const loci = [];
                  const points = [];
                  for (const atomIndex of atomIndices) {
                    // Inventory atom indices are one-based; Mol* element IDs
                    // for an XYZ trajectory are zero-based. Resolve this only
                    // in the loaded viewer model, not from an asset file.
                    const expectedElement = atomIndex - 1;
                    let selected = null;
                    for (const unit of structure.units) {
                      if (unit.kind !== 0 || !unit.elements || !unit.conformation?.position) continue;
                      let localIndex = -1;
                      for (let position = 0; position < unit.elements.length; position += 1) {
                        if (Number(unit.elements[position]) === expectedElement) {
                          if (localIndex !== -1) throw new Error('xyz_atom_ambiguous');
                          localIndex = position;
                        }
                      }
                      if (localIndex === -1) continue;
                      if (selected) throw new Error('xyz_atom_ambiguous');
                      selected = { unit, localIndex, element: expectedElement };
                    }
                    if (!selected) throw new Error('xyz_atom_unavailable');
                    const coordinate = [0, 0, 0];
                    selected.unit.conformation.position(selected.element, coordinate);
                    if (!coordinate.every(Number.isFinite)) throw new Error('xyz_atom_coordinate_invalid');
                    loci.push({
                      kind: 'element-loci',
                      structure,
                      elements: [{ unit: selected.unit, indices: Int32Array.of(selected.localIndex) }],
                    });
                    points.push(coordinate);
                  }
                  const annotation = await manager.addDihedral(loci[0], loci[1], loci[2], loci[3]);
                  if (!annotation) throw new Error('dihedral_annotation_unavailable');
                  const rawValue = viewerMeasurement.dihedral4(points[0], points[1], points[2], points[3]);
                  if (!Number.isFinite(rawValue)) throw new Error('native_dihedral_value_unavailable');
                  const value = Number(rawValue.toFixed(1));
                  viewer.plugin.managers.camera.focusLoci(loci, { durationMs: 0 });
                  return {
                    annotation_created: true,
                    value_deg: value,
                    display_resolution_deg: displayResolutionDeg,
                  };
                }""",
                {
                    "structureIndex": structure_index,
                    "atomIndices": list(atom_indices),
                    "displayResolutionDeg": _NATIVE_DIHEDRAL_DISPLAY_RESOLUTION_DEG,
                },
            )
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_operation_failed") from exc
        if not isinstance(result, Mapping) or set(result) != {
            "annotation_created",
            "value_deg",
            "display_resolution_deg",
        }:
            raise BatchSceneReplayError("viewer_operation_failed")
        value = result.get("value_deg")
        resolution = result.get("display_resolution_deg")
        if (
            result.get("annotation_created") is not True
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isinstance(resolution, (int, float))
            or isinstance(resolution, bool)
            or float(resolution) != _NATIVE_DIHEDRAL_DISPLAY_RESOLUTION_DEG
        ):
            raise BatchSceneReplayError("viewer_operation_failed")
        return {
            "value": float(value),
            "unit": "degree",
            "absolute_tolerance": _NATIVE_DIHEDRAL_DISPLAY_RESOLUTION_DEG,
        }

    def native_measure_xyz_dihedral(
        self,
        scene: BatchReplayScene,
        *,
        selector: Mapping[str, Any],
        timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        """Execute one exact native XYZ dihedral measurement in a fresh page.

        This intentionally supports only the conformation pilot's reviewed
        ``atom_quadruplet`` selector.  Callers receive a private result and
        digest-ready session/evidence bindings, never a page, asset bytes, or
        browser object.
        """

        if (
            scene.viewer_family != "molstar"
            or scene.primary_format != "xyz"
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 5 <= float(timeout_seconds) <= 90
        ):
            raise BatchSceneReplayError("host_contract_invalid")
        structure_index, atom_indices = self._xyz_dihedral_atom_indices(selector)
        deadline = monotonic() + float(timeout_seconds)
        try:
            context, token = self._open_scene(
                scene, timeout_seconds=float(timeout_seconds)
            )
        except BatchSceneReplayError:
            raise
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise BatchSceneReplayError("viewer_operation_failed")
            page = context.pages[0]
            page.set_default_timeout(max(1_000, int(remaining * 1_000)))
            result = self._molstar_measure_xyz_dihedral(page, selector)
            self._await_animation_frames(page)
            _, render_digest = self._capture(page)
            runtime = self.health()
            # ``token`` is generated afresh by the loopback asset server for
            # each browser context.  Bind only its digest: the raw token is a
            # live capability and must never reach an artifact or receipt.
            session_nonce_digest = digest({"loopback_scene_token": token})
            session_digest = digest(
                {
                    "host_id": HOST_ID,
                    "anonymous_scene_ref": scene.anonymous_scene_ref,
                    "viewer_family": scene.viewer_family,
                    "asset_set_digest": scene.asset_set_digest,
                    "operation": "measure_dihedral",
                    "selector_digest": digest(
                        {
                            "structure_index": structure_index,
                            "atom_indices": atom_indices,
                        }
                    ),
                    "render_digest": render_digest,
                    "runtime": runtime,
                    "session_nonce_digest": session_nonce_digest,
                }
            )
            executor_identity = {
                "host_id": HOST_ID,
                "implementation": "molstar_shipped_xyz_dihedral_v1",
                "runtime_identity_digest": digest(runtime),
            }
            return {
                "native_operation": "measure_dihedral",
                "operation_args": {
                    "selector": {
                        "type": "atom_quadruplet",
                        "atoms": [
                            {
                                "structure_index": structure_index,
                                "atom_index": atom_index,
                                "format": "xyz",
                            }
                            for atom_index in atom_indices
                        ],
                    }
                },
                "measurement_result": result,
                "viewer_session_digest": session_digest,
                "executor_identity": executor_identity,
                "render_digest": render_digest,
            }
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_operation_failed") from exc
        finally:
            try:
                context.close()
            finally:
                assert self._server is not None
                self._server.unregister(token)

    def _open_scene(
        self, scene: BatchReplayScene, *, timeout_seconds: float
    ) -> tuple[Any, str]:
        self._ensure_runtime()
        assert self._browser is not None and self._server is not None
        token = self._server.register(scene)
        context = self._browser.new_context(
            viewport={"width": 960, "height": 720}, device_scale_factor=1
        )
        origin = self._server.origin

        def route_request(route: Any) -> None:
            target = urlsplit(route.request.url)
            if (
                target.scheme in {"data", "blob"}
                or f"{target.scheme}://{target.netloc}" == origin
            ):
                route.continue_()
            else:
                route.abort("blockedbyclient")

        context.route("**/*", route_request)
        page = context.new_page()
        page.set_default_timeout(max(1_000, int(timeout_seconds * 1_000)))
        if scene.viewer_family == "molstar":
            query = urlencode(
                {
                    "file": f"/__qagen_scene/{token}/primary.{scene.primary_format}",
                    "type": scene.primary_format,
                    "annotate": "1",
                }
            )
            document = "molstar.html"
        else:
            query_values: dict[str, str] = {
                "file": f"/__qagen_scene/{token}/primary.{scene.primary_format}",
                "mode": "expert",
                "view": "multiplanar",
            }
            if scene.overlay_bytes is not None:
                query_values["overlay"] = (
                    f"/__qagen_scene/{token}/mask.{scene.primary_format}"
                )
            if scene.mask_id is not None:
                query_values["mask_id"] = str(scene.mask_id)
            query = urlencode(query_values)
            document = "niivue.html"
        try:
            page.goto(
                f"{origin}/viewer/{document}?{query}", wait_until="domcontentloaded"
            )
            page.wait_for_function(
                "() => window.ScienceVisionViewerMCP?.ready === true"
            )
        except Exception as exc:
            context.close()
            self._server.unregister(token)
            raise BatchSceneReplayError("viewer_page_load_failed") from exc
        try:
            if (
                scene.viewer_family == "molstar"
                and self._molstar_representation_fallback
            ):
                self._molstar_apply_polymer_and_ligand_preset(page)
            if scene.viewer_family == "molstar":
                self._molstar_focus_scene(page)
            self._await_stable_nonblank_render(page)
        except BatchSceneReplayError:
            context.close()
            self._server.unregister(token)
            raise
        return (context, token)

    def replay(
        self,
        scene: BatchReplayScene,
        operations: Iterable[str],
        *,
        timeout_seconds: float = 45.0,
    ) -> BatchReplayResult:
        """Run fixed safe operations and return only digest-bound public evidence."""

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 5 <= timeout_seconds <= 90
        ):
            raise InputError(
                "batch scene replay timeout must be between 5 and 90 seconds"
            )
        requested = tuple(operations)
        allowed = (
            _MOLSTAR_OPERATIONS
            if scene.viewer_family == "molstar"
            else _NIIVUE_OPERATIONS
        )
        if (
            not requested
            or len(set(requested)) != len(requested)
            or any(item not in allowed for item in requested)
        ):
            return BatchReplayResult(
                anonymous_scene_ref=scene.anonymous_scene_ref,
                viewer_family=scene.viewer_family,
                replayed_operations=(),
                evidence=(),
                status="blocked",
                blocker_codes=("viewer_operation_not_allowed",),
            )
        if (
            any(item in {"bbox", "overlay_compare"} for item in requested)
            and scene.overlay_bytes is None
        ):
            return BatchReplayResult(
                anonymous_scene_ref=scene.anonymous_scene_ref,
                viewer_family=scene.viewer_family,
                replayed_operations=(),
                evidence=(),
                status="blocked",
                blocker_codes=("asset_unavailable",),
            )
        deadline = monotonic() + float(timeout_seconds)
        try:
            context, token = self._open_scene(
                scene, timeout_seconds=float(timeout_seconds)
            )
        except BatchSceneReplayError as exc:
            return BatchReplayResult(
                anonymous_scene_ref=scene.anonymous_scene_ref,
                viewer_family=scene.viewer_family,
                replayed_operations=(),
                evidence=(),
                status="blocked",
                blocker_codes=(exc.code,),
            )

        evidence: list[Mapping[str, str]] = []
        replayed: list[str] = []
        try:
            page = context.pages[0]
            for operation in requested:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise BatchSceneReplayError("viewer_operation_failed")
                page.set_default_timeout(max(1_000, int(remaining * 1_000)))
                private_result: Any
                if scene.viewer_family == "molstar":
                    if operation == "inspect":
                        private_result = self._call(page, "viewer_get_state", {})
                    elif operation == "isolate":
                        private_result = self._call(
                            page, "viewer_switch_structure", {"index": 0}
                        )
                    elif operation == "rotate":
                        private_result = self._call(
                            page,
                            "viewer_rotate",
                            {
                                "d_azimuth_deg": 23,
                                "d_elevation_deg": 11,
                                "d_roll_deg": 0,
                            },
                        )
                    elif operation == "zoom":
                        private_result = self._molstar_zoom(page)
                    elif operation == "measure_distance":
                        private_result = self._molstar_measure_distance(page)
                    elif operation == "measure_dihedral":
                        private_result = self._molstar_measure_dihedral(page)
                    else:  # render
                        private_result = {"render": True}
                else:
                    if operation == "inspect":
                        private_result = self._call(page, "viewer_get_state", {})
                    elif operation == "slice":
                        private_result = self._call(
                            page, "viewer_slice", {"axis": "axial", "fraction": 0.5}
                        )
                    elif operation == "window_level":
                        state = self._call(page, "viewer_get_state", {})
                        layers = (
                            state.get("layers") if isinstance(state, Mapping) else None
                        )
                        primary = (
                            layers[0]
                            if isinstance(layers, list)
                            and layers
                            and isinstance(layers[0], Mapping)
                            else None
                        )
                        minimum = (
                            primary.get("cal_min") if primary is not None else None
                        )
                        maximum = (
                            primary.get("cal_max") if primary is not None else None
                        )
                        if (
                            not isinstance(minimum, (int, float))
                            or not isinstance(maximum, (int, float))
                            or maximum <= minimum
                        ):
                            raise BatchSceneReplayError("viewer_operation_failed")
                        private_result = self._call(
                            page,
                            "viewer_window_level",
                            {
                                "layer": 0,
                                "center": (float(minimum) + float(maximum)) / 2,
                                "width": float(maximum) - float(minimum),
                            },
                        )
                    elif operation == "bbox":
                        private_result = self._call(
                            page, "viewer_bbox", {"layer": 1, "center": True}
                        )
                    elif operation == "overlay_compare":
                        hidden = self._call(
                            page, "viewer_visibility", {"layer": 1, "visible": False}
                        )
                        _, hidden_digest = self._capture(page)
                        restored = self._call(
                            page, "viewer_visibility", {"layer": 1, "visible": True}
                        )
                        _, visible_digest = self._capture(page)
                        private_result = {
                            "hidden": hidden,
                            "restored": restored,
                            "frames": [hidden_digest, visible_digest],
                        }
                    elif operation == "rotate":
                        private_result = self._call(
                            page,
                            "viewer_rotate",
                            {
                                "d_azimuth_deg": 23,
                                "d_elevation_deg": 11,
                                "d_roll_deg": 0,
                            },
                        )
                    else:  # render
                        private_result = {"render": True}
                if scene.viewer_family == "molstar" and operation != "zoom":
                    self._molstar_focus_scene(page)
                _, render_digest = self._capture(page)
                evidence.append(
                    {
                        "operation": operation,
                        "result_digest": _safe_result_digest(private_result),
                        "render_digest": render_digest,
                    }
                )
                replayed.append(operation)
        except BatchSceneReplayError as exc:
            return BatchReplayResult(
                anonymous_scene_ref=scene.anonymous_scene_ref,
                viewer_family=scene.viewer_family,
                replayed_operations=tuple(replayed),
                evidence=tuple(evidence),
                status="blocked",
                blocker_codes=(exc.code,),
            )
        except Exception:
            return BatchReplayResult(
                anonymous_scene_ref=scene.anonymous_scene_ref,
                viewer_family=scene.viewer_family,
                replayed_operations=tuple(replayed),
                evidence=tuple(evidence),
                status="blocked",
                blocker_codes=("viewer_operation_failed",),
            )
        finally:
            try:
                context.close()
            finally:
                assert self._server is not None
                self._server.unregister(token)
        return BatchReplayResult(
            anonymous_scene_ref=scene.anonymous_scene_ref,
            viewer_family=scene.viewer_family,
            replayed_operations=tuple(replayed),
            evidence=tuple(evidence),
            status="attested",
            blocker_codes=(),
        )

    def capture_provider_views(
        self,
        scene: BatchReplayScene,
        allowed_operations: Iterable[str],
        *,
        timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        """Capture minimal transient provider views through the real viewer.

        This is deliberately not a replay receipt API.  It returns PNG bytes
        only to the in-process handoff builder, which immediately converts
        them to a provider attachment.  Callers must persist only the returned
        operation/render digests.  The operation sequence is fixed by the
        host; a model cannot supply an arbitrary viewer command or selector.
        """

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 5 <= timeout_seconds <= 90
        ):
            raise InputError(
                "batch provider view timeout must be between 5 and 90 seconds"
            )
        allowed = tuple(sorted(set(allowed_operations)))
        supported = (
            _MOLSTAR_OPERATIONS
            if scene.viewer_family == "molstar"
            else _NIIVUE_OPERATIONS
        )
        if (
            not allowed
            or "render" not in allowed
            or any(operation not in supported for operation in allowed)
        ):
            raise BatchSceneReplayError("viewer_operation_not_allowed")

        # A second angle is useful to visual workers, but it is only taken
        # when it is already part of the approved lock.  A one-view handoff is
        # still valid for a non-rotatable scene; the provider adapter handles
        # that explicitly instead of silently widening the viewer grant.
        operations = ["render"]
        for candidate in (
            "rotate",
            "zoom",
            "slice",
            "overlay_compare",
            "inspect",
            "isolate",
        ):
            if candidate in allowed and candidate != "render":
                operations.append(candidate)
                break

        try:
            context, token = self._open_scene(
                scene, timeout_seconds=float(timeout_seconds)
            )
        except BatchSceneReplayError:
            raise
        deadline = monotonic() + float(timeout_seconds)
        views: list[dict[str, Any]] = []
        try:
            page = context.pages[0]
            for operation in operations:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise BatchSceneReplayError("viewer_operation_failed")
                page.set_default_timeout(max(1_000, int(remaining * 1_000)))
                if operation == "render":
                    private_result: Any = {"render": True}
                elif scene.viewer_family == "molstar":
                    if operation == "rotate":
                        private_result = self._call(
                            page,
                            "viewer_rotate",
                            {
                                "d_azimuth_deg": 23,
                                "d_elevation_deg": 11,
                                "d_roll_deg": 0,
                            },
                        )
                    elif operation == "zoom":
                        private_result = self._molstar_zoom(page)
                    elif operation == "inspect":
                        private_result = self._call(page, "viewer_get_state", {})
                    elif operation == "isolate":
                        private_result = self._call(
                            page, "viewer_switch_structure", {"index": 0}
                        )
                    else:
                        raise BatchSceneReplayError("viewer_operation_not_allowed")
                else:
                    if operation == "rotate":
                        private_result = self._call(
                            page,
                            "viewer_rotate",
                            {
                                "d_azimuth_deg": 23,
                                "d_elevation_deg": 11,
                                "d_roll_deg": 0,
                            },
                        )
                    elif operation == "slice":
                        private_result = self._call(
                            page, "viewer_slice", {"axis": "axial", "fraction": 0.5}
                        )
                    elif operation == "overlay_compare":
                        hidden = self._call(
                            page, "viewer_visibility", {"layer": 1, "visible": False}
                        )
                        restored = self._call(
                            page, "viewer_visibility", {"layer": 1, "visible": True}
                        )
                        private_result = {"hidden": hidden, "restored": restored}
                    elif operation == "inspect":
                        private_result = self._call(page, "viewer_get_state", {})
                    else:
                        raise BatchSceneReplayError("viewer_operation_not_allowed")
                if scene.viewer_family == "molstar" and operation != "zoom":
                    self._molstar_focus_scene(page)
                png, render_digest = self._capture(page)
                views.append(
                    {
                        "operation": operation,
                        "result_digest": _safe_result_digest(private_result),
                        "render_digest": render_digest,
                        # ``png`` must remain transient: the bridge is the
                        # only caller allowed to turn it into a wire attachment.
                        "png": png,
                    }
                )
        except BatchSceneReplayError:
            raise
        except Exception as exc:
            raise BatchSceneReplayError("viewer_operation_failed") from exc
        finally:
            try:
                context.close()
            finally:
                assert self._server is not None
                self._server.unregister(token)
        return {
            "host_id": HOST_ID,
            "viewer_family": scene.viewer_family,
            "anonymous_scene_ref": scene.anonymous_scene_ref,
            "views": views,
        }

    def shutdown(self) -> None:
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
