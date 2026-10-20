"""Build immutable, redacted, self-contained packages for one expert."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .bundle import build_runtime_manifest
from .contract import (
    CONTRACT_VERSION,
    PACKAGE_SCHEMA_VERSION,
    TASK_SCHEMA_VERSION,
    ContractError,
    canonical_json,
    file_sha256,
    object_sha256,
    redact_task,
    stable_package_id,
)
from .store import ManagerStore, PreflightError, utc_now

MODULE_ROOT = Path(__file__).resolve().parent
EXPERT_APP_ROOT = MODULE_ROOT / "expert_app"
ONLINE_APP_ROOT = MODULE_ROOT / "online_app"
EXPERT_APP_FILES = ("index.html", "expert-app.js", "styles.css")
PACKAGE_FILES_ROOT = MODULE_ROOT / "package_files"
REPO_ROOT = MODULE_ROOT.parents[1]

_REMOTE_EXECUTABLE = re.compile(
    r"<(?:script|link)\b[^>]*(?:src|href)\s*=\s*['\"](?:https?:)?//",
    re.IGNORECASE,
)
_REMOTE_IMPORT = re.compile(
    r"\b(?:import|fetch)\s*\(?(?:[^'\"]*\sfrom\s*)?['\"]https?://",
    re.IGNORECASE,
)
_REMOTE_WORKER = re.compile(
    r"\b(?:importScripts|Worker|SharedWorker)\s*\(\s*['\"](?:https?:)?//",
    re.IGNORECASE,
)
_VIEWER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MOLSTAR_LAYOUT_ANCHOR = "      layoutShowSequence: true,"
_MOLSTAR_LAYOUT_EMBED = """      layoutShowSequence: params.get('embed') !== 'expert',
      collapseLeftPanel: params.get('embed') === 'expert',
      collapseRightPanel: params.get('embed') === 'expert',"""
# Mol* shows the selection-mode toggle by default; only an older snapshot of
# viewer/ turns it off, and the expert guide points straight at that button.
_MOLSTAR_SELECTION_OFF = "viewportShowSelectionMode: false,"
_MOLSTAR_SELECTION_ON = "viewportShowSelectionMode: true,"


def _validate_offline_runtime(
    viewer_root: Path, required_viewers: set[str] | None = None
) -> None:
    required = [
        viewer_root / "molstar.html",
        viewer_root / "niivue.html",
        viewer_root / "shared" / "screenshot-api.js",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PreflightError(["viewer runtime is incomplete: " + ", ".join(missing)])
    for path in viewer_root.rglob("*"):
        if path.is_symlink():
            raise PreflightError([f"viewer runtime contains a symlink: {path}"])
        if not path.is_file() or path.suffix.lower() not in {".html", ".js", ".mjs", ".css"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if (
            _REMOTE_EXECUTABLE.search(text)
            or _REMOTE_IMPORT.search(text)
            or _REMOTE_WORKER.search(text)
        ):
            raise PreflightError([f"viewer runtime has an executable network dependency: {path}"])
    for page_name in ("molstar.html", "niivue.html"):
        page = (viewer_root / page_name).read_text(encoding="utf-8", errors="ignore")
        if "/viewer/shared/screenshot-api.js" not in page:
            raise PreflightError(
                [f"viewer runtime page does not load the screenshot provider: {page_name}"]
            )
    for viewer in sorted(required_viewers or set()):
        if not _VIEWER_NAME.fullmatch(viewer):
            raise PreflightError([f"unsafe viewer name in task: {viewer!r}"])
        page = viewer_root / f"{viewer}.html"
        if page.is_symlink() or not page.is_file():
            raise PreflightError([f"viewer runtime is missing assigned renderer: {viewer}.html"])


def _copy_expert_app(stage: Path) -> None:
    """Stage the expert workspace together with the language layer it loads.

    i18n.js is shared with the online app, so it lives outside expert_app -- but
    the workspace markup loads it as /i18n.js, and a package without it answers
    404 and falls back to English with no way to switch.
    """
    for name in EXPERT_APP_FILES:
        shutil.copy2(EXPERT_APP_ROOT / name, stage / name)
    shutil.copy2(ONLINE_APP_ROOT / "i18n.js", stage / "i18n.js")
    # The browser MCP runtime is loaded from /mcp/ by the same markup, so a
    # package without it loses the Functions panel and trajectory capture.
    mcp_source = EXPERT_APP_ROOT / "mcp"
    if not mcp_source.is_dir():
        raise FileNotFoundError(f"expert app MCP runtime is missing: {mcp_source}")
    shutil.copytree(mcp_source, stage / "mcp")


def _copy_runtime(source: Path, destination: Path, required_viewers: set[str]) -> None:
    _validate_offline_runtime(source, required_viewers)
    shutil.copytree(source, destination, symlinks=False)
    molstar_page = destination / "molstar.html"
    molstar_text = molstar_page.read_text(encoding="utf-8")
    # Both repairs edit the same page, so they compose on one string and land in
    # a single write; writing twice from the original text would undo the first.
    repaired = molstar_text
    if _MOLSTAR_LAYOUT_EMBED not in repaired:
        if repaired.count(_MOLSTAR_LAYOUT_ANCHOR) != 1:
            raise PreflightError(
                ["viewer/molstar.html lacks the expert-layout injection anchor"]
            )
        repaired = repaired.replace(_MOLSTAR_LAYOUT_ANCHOR, _MOLSTAR_LAYOUT_EMBED)
    repaired = repaired.replace(_MOLSTAR_SELECTION_OFF, _MOLSTAR_SELECTION_ON)
    if repaired != molstar_text:
        molstar_page.write_text(repaired, encoding="utf-8")
    # Keep the screenshot workflow from the screenshot PR even when the supplied
    # runtime predates the parent-page toolbar.
    screenshot_source = REPO_ROOT / "viewer" / "shared" / "task-screenshot.js"
    if not screenshot_source.is_file():
        raise FileNotFoundError(screenshot_source)
    shutil.copy2(screenshot_source, destination / "shared" / "task-screenshot.js")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def _copy_asset(asset_root: Path, stage: Path, record: dict[str, Any]) -> dict[str, Any]:
    source = asset_root.joinpath(*record["path"].split("/"))
    resolved = source.resolve()
    current = asset_root
    linked = False
    for part in record["path"].split("/"):
        current = current / part
        if current.is_symlink():
            linked = True
            break
    if not resolved.is_relative_to(asset_root) or linked or not source.is_file():
        raise PreflightError([f"missing, linked, or escaped asset: {record['path']}"])
    actual_hash = file_sha256(source)
    if actual_hash != record["sha256"]:
        raise PreflightError([f"asset hash mismatch: {record['path']}"])
    actual_size = source.stat().st_size
    if record.get("byte_size") is not None and record["byte_size"] != actual_size:
        raise PreflightError([f"asset byte_size mismatch: {record['path']}"])
    destination = stage.joinpath(*record["path"].split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    copied_size = destination.stat().st_size
    copied_hash = file_sha256(destination)
    if copied_hash != record["sha256"]:
        raise PreflightError([f"copied asset hash mismatch: {record['path']}"])
    if record.get("byte_size") is not None and copied_size != record["byte_size"]:
        raise PreflightError([f"copied asset byte_size mismatch: {record['path']}"])
    return {"path": record["path"], "sha256": copied_hash, "byte_size": copied_size}


def _write_checksums(stage: Path) -> None:
    paths = sorted(
        path for path in stage.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [f"{file_sha256(path)}  {path.relative_to(stage).as_posix()}" for path in paths]
    (stage / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_shared_runtime(
    *, viewer_root: Path | str, output: Path | str
) -> dict[str, Any]:
    source = Path(viewer_root).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing runtime: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.staging-", dir=output_path.parent)
    )
    try:
        _copy_runtime(source, stage / "viewer", set())
        _copy_expert_app(stage)
        manifest = build_runtime_manifest(stage)
        _write_json(stage / "runtime_manifest.json", manifest)
        os.replace(stage, output_path)
        return manifest
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _build_thin_package(
    store: ManagerStore,
    *,
    batch_id: str,
    expert_id: str,
    output: Path | str,
) -> dict[str, Any]:
    output_path = Path(output).expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing package: {output_path}")
    prepared = store.prepare_bundle_export(batch_id=batch_id, expert_id=expert_id)
    asset_root = Path(prepared["asset_root"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.staging-", dir=output_path.parent)
    )
    published = False
    try:
        for record in prepared["assets"]:
            _copy_asset(asset_root, stage, record)
        tasks_path = stage / prepared["manifest"]["task_file"]["path"]
        tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tasks_path.write_text(prepared["tasks_jsonl"], encoding="utf-8", newline="\n")
        _write_json(stage / "bundle_manifest.json", prepared["manifest"])
        shutil.copy2(PACKAGE_FILES_ROOT / "serve_bundle.py", stage / "serve_bundle.py")
        shutil.copy2(PACKAGE_FILES_ROOT / "THIN_README.md", stage / "README.md")
        _write_checksums(stage)
        os.replace(stage, output_path)
        published = True
        try:
            store.register_prepared_bundle(prepared)
        except Exception:
            shutil.rmtree(output_path)
            published = False
            raise
        return prepared["manifest"]
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if published and output_path.exists():
            shutil.rmtree(output_path)
        raise


def build_expert_package(
    store: ManagerStore,
    *,
    batch_id: str,
    expert_id: str,
    output: Path | str,
    mode: str = "standalone",
) -> dict[str, Any]:
    if mode == "thin":
        return _build_thin_package(
            store,
            batch_id=batch_id,
            expert_id=expert_id,
            output=output,
        )
    if mode != "standalone":
        raise ContractError("package mode must be standalone or thin")
    output_path = Path(output).expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing package: {output_path}")
    unassigned = store.unassigned_tasks(batch_id)
    if unassigned:
        raise PreflightError(
            [
                f"batch {batch_id!r} still has {len(unassigned)} unassigned task(s): "
                + ", ".join(unassigned[:20])
            ]
        )
    inputs = store.package_inputs(batch_id=batch_id, expert_id=expert_id)
    assignments = inputs["assignments"]
    required_viewers: set[str] = set()
    for assignment in assignments:
        task = assignment["payload"]
        assets: list[dict[str, Any]] = []
        if isinstance(task.get("asset"), dict):
            assets.append(task["asset"])
        if isinstance(task.get("assets"), list):
            assets.extend(item for item in task["assets"] if isinstance(item, dict))
        for asset in assets:
            viewer = asset.get("viewer")
            if viewer == "svg" or (not viewer and asset.get("format") == "svg"):
                continue
            required_viewers.add(str(viewer or "gltf"))
    package_id = stable_package_id(batch_id, expert_id, assignments)
    if store.package_exists(package_id):
        raise FileExistsError(f"package already registered: {package_id}")

    asset_root = Path(inputs["batch"]["asset_root"]).resolve()
    viewer_root = Path(inputs["batch"]["viewer_root"]).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.staging-", dir=output_path.parent)
    )
    published = False
    try:
        _copy_runtime(viewer_root, stage / "viewer", required_viewers)
        _copy_expert_app(stage)
        shutil.copy2(PACKAGE_FILES_ROOT / "serve_package.py", stage / "serve_package.py")
        shutil.copy2(PACKAGE_FILES_ROOT / "README.md", stage / "README.md")

        task_rows: list[dict[str, Any]] = []
        manifest_assignments: list[dict[str, Any]] = []
        closure: dict[str, dict[str, Any]] = {}
        redacted_count = 0
        for assignment in assignments:
            redacted, removed = redact_task(assignment["payload"])
            redacted_count += len(removed)
            row = {
                "schema_version": TASK_SCHEMA_VERSION,
                "contract_version": CONTRACT_VERSION,
                "package_id": package_id,
                "batch_id": batch_id,
                "expert_id": expert_id,
                "assignment_id": assignment["assignment_id"],
                "task_id": assignment["task_id"],
                "task_revision_sha256": assignment["task_revision_sha256"],
                "task": redacted,
            }
            task_rows.append(row)
            manifest_assignments.append(
                {
                    "assignment_id": assignment["assignment_id"],
                    "task_id": assignment["task_id"],
                    "task_revision_sha256": assignment["task_revision_sha256"],
                    "domain": assignment["domain"],
                }
            )
            for record in assignment["assets"]:
                existing = closure.get(record["path"])
                if existing and existing != record:
                    raise ContractError(f"asset closure conflict: {record['path']}")
                closure[record["path"]] = record

        copied_assets = [
            _copy_asset(asset_root, stage, closure[path]) for path in sorted(closure)
        ]
        _write_jsonl(stage / "tasks" / "tasks.jsonl", task_rows)
        manifest_body = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "package_id": package_id,
            "batch_id": batch_id,
            "expert_id": expert_id,
            "expert": {
                "name": inputs["expert"]["name"],
                "domains": inputs["expert"]["domains"],
            },
            "admission": inputs["admission"],
            "created_at": utc_now(),
            "offline": True,
            "task_count": len(task_rows),
            "tasks_path": "tasks/tasks.jsonl",
            "assignments": manifest_assignments,
            "asset_count": len(copied_assets),
            "asset_bytes": sum(record["byte_size"] for record in copied_assets),
            "assets": copied_assets,
            "viewer_path": "/viewer/",
            "redaction": {
                "policy": "expert-visible-allowlist-v1",
                "removed_field_count": redacted_count,
            },
            "submission": {
                "schema_version": "sciencevision.expert_submission.v1",
                "trajectory": {
                    "schema_version": "sciencevision.trajectory_ref.v1",
                    "status": "not_collected",
                    "reason": "collector_not_implemented",
                },
                "evidence_supported": False,
            },
        }
        manifest = manifest_body | {"manifest_sha256": object_sha256(manifest_body)}
        _write_json(stage / "package_manifest.json", manifest)
        _write_checksums(stage)
        os.replace(stage, output_path)
        published = True
        try:
            store.register_package(manifest, output_path)
        except Exception:
            shutil.rmtree(output_path)
            published = False
            raise
        return manifest
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if published and output_path.exists():
            shutil.rmtree(output_path)
        raise
