#!/usr/bin/env python3
"""Audit one pending-review bundle without approving, staging, or releasing it.

The public viewer is exercised through a loopback URL, while the exact XYZ
quadruplet measurement runs in a fresh private native-viewer session.  The
receipt intentionally retains only digests of the measurement result; the
numeric result never leaves the private viewer boundary.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit

from qagen.contracts import digest, digest_bytes, make_envelope, require_digest, verify_envelope
from qagen.errors import InputError
from qagen.reviewer_bundle import verify_pending_reviewer_bundle
from qagen.storage.atomic import atomic_write_json
from qagen.viewer.batch_scene_host import BatchReplayScene, ControlledBatchViewerReplayHost


class PendingReviewerAuditError(InputError):
    """The pending-review browser or native viewer audit cannot be attested."""


_RUN_STATE_KIND = "qagen_v32_text_only_execution_state"
_REGISTRY_KIND = "qagen_private_batch_scene_registry"
_PENDING_STATE = "verification_complete_pending_human_review"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _strict_file(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise PendingReviewerAuditError(f"{label} is unsafe")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise PendingReviewerAuditError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or resolved.is_symlink():
        raise PendingReviewerAuditError(f"{label} is unsafe")
    return resolved


def _strict_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise PendingReviewerAuditError(f"{label} is unsafe")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise PendingReviewerAuditError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or resolved.is_symlink():
        raise PendingReviewerAuditError(f"{label} is unsafe")
    return resolved


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_strict_file(path, label=label).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PendingReviewerAuditError(f"{label} is invalid") from exc
    if not isinstance(value, Mapping):
        raise PendingReviewerAuditError(f"{label} is invalid")
    return dict(value)


def _regular_child(root: Path, relative: str, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise PendingReviewerAuditError(f"{label} is invalid")
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise PendingReviewerAuditError(f"{label} is invalid")
    current = root
    try:
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise PendingReviewerAuditError(f"{label} is unsafe")
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PendingReviewerAuditError(f"{label} is unavailable") from exc
    return _strict_file(resolved, label=label)


def _run_workspace(run_root: Path) -> Path:
    for parent in (run_root, *run_root.parents):
        if parent.name == ".qagen":
            return parent.parent
    raise PendingReviewerAuditError("run root is not under a qagen workspace")


def _load_run(run_root: Path) -> dict[str, Any]:
    root = _strict_directory(run_root, label="run root")
    state = _read_json(root / "state.json", label="run state")
    try:
        state = verify_envelope(state, kind=_RUN_STATE_KIND)
    except InputError as exc:
        raise PendingReviewerAuditError("run state is unattested") from exc
    body = state.get("body")
    if not isinstance(body, Mapping) or state.get("status") != _PENDING_STATE or body.get("status") != _PENDING_STATE:
        raise PendingReviewerAuditError("run is not pending human review")
    if body.get("staging_enabled") is not False or body.get("release_eligible") is not False:
        raise PendingReviewerAuditError("run has crossed its pending-review boundary")
    candidate = _read_json(root / "outputs" / "candidate.json", label="candidate")
    try:
        require_digest(body.get("candidate_digest"), "candidate digest")
    except InputError as exc:
        raise PendingReviewerAuditError("candidate digest is invalid") from exc
    if digest(candidate) != body["candidate_digest"]:
        raise PendingReviewerAuditError("candidate no longer matches state")
    if candidate.get("task_id") != body.get("run_id") or candidate.get("artifact_kind") != "author_private_candidate":
        raise PendingReviewerAuditError("candidate does not bind to run")
    target = candidate.get("visual_target")
    if not isinstance(target, Mapping) or not isinstance(target.get("highlighted_atom_ids"), list):
        raise PendingReviewerAuditError("candidate selector is invalid")
    atom_ids = target["highlighted_atom_ids"]
    if len(atom_ids) != 4 or not all(isinstance(value, str) and value for value in atom_ids):
        raise PendingReviewerAuditError("candidate selector is invalid")
    lock = _read_json(root / "execution-lock.json", label="execution lock")
    try:
        lock = verify_envelope(lock, kind="qagen_v32_text_only_execution_lock")
    except InputError as exc:
        raise PendingReviewerAuditError("execution lock is unattested") from exc
    return {"root": root, "state": state, "candidate": candidate, "lock": lock, "atom_ids": atom_ids}


def _load_scene(run: Mapping[str, Any], registry_path: Path) -> tuple[BatchReplayScene, list[dict[str, Any]], str]:
    registry_value = _read_json(registry_path, label="scene registry")
    try:
        registry = verify_envelope(registry_value, kind=_REGISTRY_KIND)
    except InputError as exc:
        raise PendingReviewerAuditError("scene registry is unattested") from exc
    body = registry.get("body")
    if not isinstance(body, Mapping) or body.get("local_only") is not True or body.get("release_eligible") is not False:
        raise PendingReviewerAuditError("scene registry boundary is invalid")
    candidate = run["candidate"]
    source_ref = candidate.get("anonymous_scene_ref")
    scenes = body.get("scenes")
    matches = [scene for scene in scenes if isinstance(scene, Mapping) and scene.get("anonymous_scene_ref") == source_ref] if isinstance(scenes, list) else []
    if len(matches) != 1:
        raise PendingReviewerAuditError("candidate scene is not uniquely registered")
    scene = dict(matches[0])
    if scene.get("viewer_family") != "molstar" or scene.get("asset_format") != "xyz":
        raise PendingReviewerAuditError("only the locked Mol* XYZ pilot is supported")
    operations = scene.get("operation_allowlist")
    if not isinstance(operations, list) or not {"rotate", "render", "measure_dihedral"}.issubset(set(operations)):
        raise PendingReviewerAuditError("scene has insufficient controlled operations")
    asset_root_value = body.get("asset_root")
    viewer_bundle = body.get("viewer_bundle")
    if not isinstance(asset_root_value, str) or not isinstance(viewer_bundle, Mapping) or not isinstance(viewer_bundle.get("root"), str):
        raise PendingReviewerAuditError("scene registry paths are invalid")
    asset_root = _strict_directory(Path(asset_root_value), label="asset root")
    primary = scene.get("backend", {}).get("primary") if isinstance(scene.get("backend"), Mapping) else None
    if not isinstance(primary, Mapping):
        raise PendingReviewerAuditError("scene primary asset is invalid")
    source = _regular_child(asset_root, str(primary.get("path", "")), label="scene primary asset")
    raw_digest = primary.get("sha256")
    if not isinstance(raw_digest, str) or not _RAW_SHA256.fullmatch(raw_digest):
        raise PendingReviewerAuditError("scene primary digest is invalid")
    raw = source.read_bytes()
    if sha256(raw).hexdigest() != raw_digest or len(raw) != primary.get("bytes"):
        raise PendingReviewerAuditError("scene primary binding changed")
    workspace = _run_workspace(run["root"])
    artifacts = run["lock"].get("body", {}).get("artifacts")
    inventory_ref = artifacts.get("inventory") if isinstance(artifacts, Mapping) else None
    if not isinstance(inventory_ref, Mapping) or not isinstance(inventory_ref.get("path"), str):
        raise PendingReviewerAuditError("run inventory reference is invalid")
    inventory_path = _regular_child(workspace, inventory_ref["path"], label="scene inventory")
    inventory = _read_json(inventory_path, label="scene inventory")
    try:
        inventory = verify_envelope(inventory, kind="qagen_scene_inventory")
    except InputError as exc:
        raise PendingReviewerAuditError("scene inventory is unattested") from exc
    selector_map = inventory.get("body", {}).get("backend_selector_map")
    if not isinstance(selector_map, Mapping):
        raise PendingReviewerAuditError("scene inventory selector map is invalid")
    atoms: list[dict[str, Any]] = []
    for atom_id in run["atom_ids"]:
        atom = selector_map.get(atom_id)
        if not isinstance(atom, Mapping) or set(atom) != {"structure_index", "atom_index", "format"}:
            raise PendingReviewerAuditError("scene inventory selector binding is invalid")
        if atom.get("format") != "xyz":
            raise PendingReviewerAuditError("scene inventory selector format is invalid")
        atoms.append(dict(atom))
    replay_scene = BatchReplayScene(
        anonymous_scene_ref=str(source_ref),
        viewer_family="molstar",
        primary_bytes=raw,
        primary_digest=f"sha256:{raw_digest}",
        primary_format="xyz",
    )
    return replay_scene, atoms, str(viewer_bundle["root"])


def _validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None or parsed.path not in {"", "/"}:
        raise PendingReviewerAuditError("viewer URL must be a loopback root URL")
    return f"http://127.0.0.1:{parsed.port}/"


def _browser_audit(
    url: str,
    *,
    screenshot: Path,
    executable: Path,
    selected_atom_ids: list[str],
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PendingReviewerAuditError("playwright is unavailable") from exc
    executable = _strict_file(executable, label="browser executable")
    console_errors: list[str] = []
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(
                executable_path=str(executable),
                headless=True,
                args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
            )
            context = browser.new_context(viewport={"width": 1440, "height": 1024})
            origin = url.removesuffix("/")

            def route_handler(route: Any) -> None:
                request_url = route.request.url
                if request_url.startswith(origin + "/") or request_url == origin:
                    route.continue_()
                else:
                    route.abort()

            context.route("**/*", route_handler)
            page = context.new_page()
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto(url, wait_until="domcontentloaded")
            frame = page.frame_locator("iframe")
            frame.get_by_role("button", name="Dihedral", exact=True).wait_for(state="visible", timeout=20_000)
            frame.locator("canvas").first.wait_for(state="visible", timeout=20_000)
            rotate = frame.locator("html").evaluate(
                """async () => {
                  await window.ScienceVisionViewerMCP.whenReady();
                  const result = await window.ScienceVisionViewerMCP.callTool('viewer_rotate', {
                    d_azimuth_deg: 23, d_elevation_deg: 11, d_roll_deg: 0,
                  });
                  return { camera_changed: Boolean(result?.camera) };
                }"""
            )
            render = frame.locator("html").evaluate(
                """async () => {
                  await window.ScienceVisionViewerScreenshot.whenReady();
                  const blob = await window.ScienceVisionViewerScreenshot.captureScreenshot();
                  const bytes = new Uint8Array(await blob.arrayBuffer());
                  const hash = await crypto.subtle.digest('SHA-256', bytes);
                  return { bytes: bytes.length, sha256: Array.from(new Uint8Array(hash)).map(value => value.toString(16).padStart(2, '0')).join('') };
                }"""
            )
            frame.get_by_role("button", name="Dihedral", exact=True).click()
            mode = frame.locator(".sv-mol-measure__progress").inner_text()
            public_text = page.locator("body").inner_text()
            sidebar_text = page.locator("#selector").inner_text()
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            browser.close()
    except Exception as exc:
        raise PendingReviewerAuditError("public controlled viewer audit failed") from exc
    if not isinstance(rotate, Mapping) or rotate.get("camera_changed") is not True:
        raise PendingReviewerAuditError("viewer rotation was not attested")
    if not isinstance(render, Mapping) or not isinstance(render.get("bytes"), int) or render["bytes"] <= 0 or not isinstance(render.get("sha256"), str) or not _RAW_SHA256.fullmatch(render["sha256"]):
        raise PendingReviewerAuditError("viewer render was not attested")
    if (
        mode != "0/4"
        or "reference_answer" in public_text
        or "ground_truth" in public_text
        or "four highlighted atoms" in public_text.casefold()
        or any(atom_id not in sidebar_text for atom_id in selected_atom_ids)
    ):
        raise PendingReviewerAuditError("public viewer control or leakage check failed")
    if console_errors:
        raise PendingReviewerAuditError("public controlled viewer emitted console errors")
    os.chmod(screenshot, 0o600)
    return {
        "nonblank_canvas": True,
        "dihedral_control_visible": True,
        "dihedral_control_mode": "armed_four_points",
        "browser_exact_measurement": "not_executed_control_armed_only",
        "selector_sidebar_ids_visible": True,
        "rotate": {"status": "attested"},
        "render": {"status": "attested", "render_digest": f"sha256:{render['sha256']}", "bytes": render["bytes"]},
        "public_leakage_check": "passed",
        "screenshot_digest": digest_bytes(screenshot.read_bytes()),
    }


def audit(
    *,
    bundle: Path,
    run_root: Path,
    scene_registry: Path,
    viewer_url: str,
    server_pid: int,
    browser_executable: Path,
    audit_dir: Path,
) -> dict[str, Any]:
    verified = verify_pending_reviewer_bundle(bundle)
    run = _load_run(run_root)
    if verified["task_id"] != run["state"]["body"]["run_id"]:
        raise PendingReviewerAuditError("bundle does not bind to run")
    url = _validate_url(viewer_url)
    if not isinstance(server_pid, int) or isinstance(server_pid, bool) or server_pid <= 0:
        raise PendingReviewerAuditError("server PID is invalid")
    target = Path(audit_dir).expanduser()
    if target.exists() or target.is_symlink():
        raise PendingReviewerAuditError("refusing to overwrite an audit directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        os.chmod(stage, 0o700)
        browser = _browser_audit(
            url,
            screenshot=stage / "browser-review.png",
            executable=browser_executable,
            selected_atom_ids=run["atom_ids"],
        )
        replay_scene, atoms, viewer_root = _load_scene(run, _strict_file(scene_registry, label="scene registry"))
        host = ControlledBatchViewerReplayHost(viewer_root)
        try:
            replay = host.replay(replay_scene, ("rotate", "render"))
            native = host.native_measure_xyz_dihedral(
                replay_scene,
                selector={"type": "atom_quadruplet", "atoms": atoms},
            )
        finally:
            host.shutdown()
        if replay.status != "attested" or tuple(replay.replayed_operations) != ("rotate", "render"):
            raise PendingReviewerAuditError("private rotate/render replay was not attested")
        result = native.get("measurement_result")
        if not isinstance(result, Mapping):
            raise PendingReviewerAuditError("native dihedral measurement was not attested")
        receipt = make_envelope(
            kind="qagen_pending_reviewer_viewer_audit",
            identifier=f"pending-review-audit-{verified['bundle_id']}",
            status="attested_pending_human_review",
            body={
                "audit_version": 1,
                "bundle_id": verified["bundle_id"],
                "task_id": verified["task_id"],
                "scene_ref": verified["scene_ref"],
                "state_digest": run["state"]["content_digest"],
                "viewer_server": {"url": url, "pid": server_pid, "loopback_host": "127.0.0.1"},
                "public_browser": browser,
                "private_native_viewer": {
                    "rotate_render_status": replay.status,
                    "rotate_render_evidence": [dict(item) for item in replay.evidence],
                    "measure_dihedral_status": "attested",
                    "measurement_result_digest": digest(dict(result)),
                    "viewer_session_digest": native.get("viewer_session_digest"),
                    "render_digest": native.get("render_digest"),
                    "executor_identity": native.get("executor_identity"),
                },
                "staging_enabled": False,
                "release_eligible": False,
            },
            producer="qagen/0.1.0:pending_reviewer_viewer_audit",
        )
        atomic_write_json(stage / "viewer-audit.json", receipt, private=True)
        atomic_write_json(stage / "server.json", {"url": url, "pid": server_pid, "health": "ready"}, private=True)
        os.replace(stage, target)
        return {
            "status": "attested_pending_human_review",
            "audit_dir": str(target),
            "receipt_digest": receipt["content_digest"],
            "screenshot_digest": browser["screenshot_digest"],
            "staging_enabled": False,
            "release_eligible": False,
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--scene-registry", type=Path, required=True)
    parser.add_argument("--viewer-url", required=True)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--browser-executable", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = audit(
            bundle=args.bundle,
            run_root=args.run_root,
            scene_registry=args.scene_registry,
            viewer_url=args.viewer_url,
            server_pid=args.server_pid,
            browser_executable=args.browser_executable,
            audit_dir=args.audit_dir,
        )
    except PendingReviewerAuditError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
