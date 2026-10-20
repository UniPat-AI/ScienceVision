from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import pytest

from tools.offline_task_manager.contract import (
    object_sha256,
    submission_payload_sha256,
    verify_manifest_hash,
)
from tools.offline_task_manager.packager import build_expert_package, build_shared_runtime
from tools.offline_task_manager.store import PreflightError


def _launch_browser(playwright):
    from playwright.sync_api import Error

    try:
        return playwright.chromium.launch()
    except Error as error:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.exists():
            pytest.skip(f"Playwright Chromium unavailable: {error}")
        return playwright.chromium.launch(executable_path=str(chrome))


def test_package_is_redacted_asset_closure_with_complete_offline_viewer(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "expert-a-package"
    manifest = build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )
    assert verify_manifest_hash(manifest) == manifest["manifest_sha256"]
    assert manifest["admission"]["status"] == "passed"
    assert manifest["admission"]["purpose"] == "expert_review_distribution"
    assert manifest["admission"]["receipt_digest"]
    assert manifest["asset_count"] == 1
    assert (output / "assets" / "model.cif").read_bytes() == imported_workflow["asset"].read_bytes()
    assert not (output / "assets" / "unused.bin").exists()
    assert (output / "viewer/shared/vendor/fixture/runtime.bin").is_file()
    assert (output / "viewer/shared/task-screenshot.js").is_file()
    assert (output / "viewer/shared/screenshot-api.js").is_file()
    molstar = (output / "viewer/molstar.html").read_text(encoding="utf-8")
    assert "collapseLeftPanel: params.get('embed') === 'expert'" in molstar
    assert "collapseRightPanel: params.get('embed') === 'expert'" in molstar
    assert "params.set('embed', 'expert')" in (output / "expert-app.js").read_text(
        encoding="utf-8"
    )

    rows = [
        json.loads(line)
        for line in (output / "tasks/tasks.jsonl").read_text().splitlines()
        if line
    ]
    assert len(rows) == 2
    assert {row["expert_id"] for row in rows} == {"expert-a"}
    serialized = json.dumps(rows)
    for forbidden in (
        "GROUND_TRUTH_SECRET",
        "GROUNDING_SECRET",
        "ORACLE_SECRET",
        "SCORER_SECRET",
        "VALIDATION_SECRET",
        "ACCEPTANCE_SECRET",
        "QC_SECRET",
        "QUARANTINE_SECRET",
        "SOURCE_SECRET",
        "ASSET_SECRET",
    ):
        assert forbidden not in serialized
    assert "https://cdn" not in "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.rglob("*")
        if path.is_file() and path.suffix in {".html", ".js", ".css"}
    )
    assert (output / "SHA256SUMS").is_file()
    with pytest.raises(FileExistsError, match="overwrite"):
        build_expert_package(
            store, batch_id="batch-r1", expert_id="expert-a", output=output
        )


def test_package_fails_for_unassigned_batch(imported_workflow: dict) -> None:
    with pytest.raises(PreflightError, match="unassigned"):
        build_expert_package(
            imported_workflow["store"],
            batch_id="batch-r1",
            expert_id="expert-a",
            output=imported_workflow["tmp"] / "must-not-exist",
        )


def test_thin_bundle_uses_one_separate_shared_runtime(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    runtime = imported_workflow["tmp"] / "shared-runtime"
    runtime_manifest = build_shared_runtime(
        viewer_root=imported_workflow["viewer"], output=runtime
    )
    assert runtime_manifest["runtime_id"].startswith("vrt_")
    assert (runtime / "index.html").is_file()
    assert (runtime / "expert-app.js").is_file()
    assert (runtime / "viewer/molstar.html").is_file()

    output = imported_workflow["tmp"] / "thin-expert-bundle"
    manifest = build_expert_package(
        store,
        batch_id="batch-r1",
        expert_id="expert-a",
        output=output,
        mode="thin",
    )
    assert manifest["schema_version"] == "sciencevision.expert_task_bundle.v2"
    assert (output / "bundle_manifest.json").is_file()
    assert (output / "tasks/tasks.jsonl").is_file()
    assert (output / "assets/model.cif").is_file()
    assert (output / "serve_bundle.py").is_file()
    assert not (output / "viewer").exists()
    assert not (output / "index.html").exists()
    assert not (output / "expert-app.js").exists()
    registered = store.connection.execute(
        "SELECT bundle_id FROM task_bundles WHERE bundle_id=?",
        (manifest["bundle_id"],),
    ).fetchone()
    assert registered and registered["bundle_id"] == manifest["bundle_id"]
    assert store.connection.execute("SELECT COUNT(*) FROM active_task_bundles").fetchone()[0] == 0

    task = json.loads((output / "tasks/tasks.jsonl").read_text().splitlines()[0])
    result_row = {
        "schema_version": "sciencevision.expert_bundle_result.v2",
        "bundle_id": manifest["bundle_id"],
        "batch_id": task["batch_id"],
        "expert_id": task["expert_id"],
        "assignment_id": task["assignment_id"],
        "task_id": task["task_id"],
        "task_revision_sha256": task["task_revision_sha256"],
        "attempt": 1,
        "task_validation": {"status": "valid", "feedback": "Thin bundle reviewed."},
        "answer": "Thin bundle answer",
        "reasoning": "Reviewed with the shared runtime.",
        "trajectory": {
            "schema_version": "sciencevision.trajectory_ref.v1",
            "status": "not_collected",
            "reason": "collector_not_implemented",
        },
        "evidence": [],
        "saved_at": "2026-08-15T00:00:00Z",
    }
    submission_body = {
        "schema_version": "sciencevision.expert_bundle_submission.v2",
        "contract_version": "sciencevision.expert_exchange.v2",
        "submission_id": "sub_" + object_sha256(result_row)[:24],
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": manifest["bundle_manifest_sha256"],
        "batch_id": task["batch_id"],
        "expert_id": task["expert_id"],
        "results": [result_row],
    }
    submission = submission_body | {
        "payload_sha256": submission_payload_sha256(submission_body)
    }
    assert store.import_submission(submission)["counts"] == {
        "accepted": 1,
        "duplicates": 0,
        "rejected": 0,
    }

    result = subprocess.run(
        [
            sys.executable,
            "serve_bundle.py",
            "--runtime",
            str(runtime),
            "--verify-only",
        ],
        cwd=output,
        check=True,
        capture_output=True,
        text=True,
    )
    assert runtime_manifest["runtime_id"] in result.stdout

    incompatible = json.loads((runtime / "runtime_manifest.json").read_text())
    incompatible["renderers"] = []
    body = {key: value for key, value in incompatible.items() if key != "runtime_id"}
    incompatible["runtime_id"] = "vrt_" + object_sha256(body)[:24]
    (runtime / "runtime_manifest.json").write_text(
        json.dumps(incompatible, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    failed = subprocess.run(
        [sys.executable, "serve_bundle.py", "--runtime", str(runtime), "--verify-only"],
        cwd=output,
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "missing required renderers: molstar" in failed.stderr


def test_package_rechecks_asset_hash_after_import(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    original = imported_workflow["asset"].read_bytes()
    imported_workflow["asset"].write_bytes(b"x" * len(original))
    output = imported_workflow["tmp"] / "post-import-asset-tamper"

    with pytest.raises(PreflightError, match="asset hash mismatch"):
        build_expert_package(
            store,
            batch_id="batch-r1",
            expert_id="expert-a",
            output=output,
        )

    assert not output.exists()


def test_package_rechecks_staged_asset_after_copy(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    source = imported_workflow["asset"]
    original = source.read_bytes()
    malicious = b"x" * len(original)
    output = imported_workflow["tmp"] / "copy-window-asset-tamper"
    real_copy2 = shutil.copy2

    def tamper_then_copy(src, dst, *args, **kwargs):
        if Path(src) == source:
            source.write_bytes(malicious)
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr("tools.offline_task_manager.packager.shutil.copy2", tamper_then_copy)
    with pytest.raises(PreflightError, match="copied asset hash mismatch"):
        build_expert_package(
            store,
            batch_id="batch-r1",
            expert_id="expert-a",
            output=output,
        )

    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.staging-*"))
    assert store.connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0] == 0


def test_generated_launcher_uses_stable_default_port_and_is_location_independent(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "stable-port-package"
    build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )

    launcher = output / "serve_package.py"
    outside = imported_workflow["tmp"] / "outside-package"
    outside.mkdir()
    result = subprocess.run(
        [sys.executable, str(launcher), "--verify-only"],
        cwd=outside,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.startswith("OK:")

    spec = importlib.util.spec_from_file_location("stable_port_package_server", launcher)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_PORT == 4180
    assert module.parse_args([]).port == 4180
    assert module.parse_args(["--port", "0"]).port == 0
    assert module.parse_args(["--port", "4181"]).port == 4181

    readme = (output / "README.md").read_text(encoding="utf-8")
    assert "http://127.0.0.1:4180/" in readme
    assert "changing\nthe port makes drafts" in readme
    assert "python3 serve_package.py --port 4181" in readme
    assert "--port 0" in readme


def test_executable_cdn_dependency_fails_preflight(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    (imported_workflow["viewer"] / "molstar.html").write_text(
        '<script src="https://cdn.example.test/molstar.js"></script>', encoding="utf-8"
    )
    with pytest.raises(PreflightError, match="network dependency"):
        build_expert_package(
            store,
            batch_id="batch-r1",
            expert_id="expert-a",
            output=imported_workflow["tmp"] / "cdn-package",
        )


def test_failed_package_registration_removes_published_directory(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "registration-failure"

    def fail_registration(*_args, **_kwargs):
        raise RuntimeError("database registration failed")

    monkeypatch.setattr(store, "register_package", fail_registration)
    with pytest.raises(RuntimeError, match="registration failed"):
        build_expert_package(
            store, batch_id="batch-r1", expert_id="expert-a", output=output
        )
    assert not output.exists()


def test_failed_thin_bundle_registration_removes_published_directory(
    imported_workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "thin-registration-failure"

    def fail_registration(*_args, **_kwargs):
        raise RuntimeError("bundle registration failed")

    monkeypatch.setattr(store, "register_prepared_bundle", fail_registration)
    with pytest.raises(RuntimeError, match="bundle registration failed"):
        build_expert_package(
            store,
            batch_id="batch-r1",
            expert_id="expert-a",
            output=output,
            mode="thin",
        )
    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.staging-*"))
    assert store.connection.execute("SELECT COUNT(*) FROM task_bundles").fetchone()[0] == 0


def test_thin_launcher_serves_only_verified_runtime_inventory(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    nested_manifest = imported_workflow["viewer"] / "shared/runtime_manifest.json"
    nested_manifest.write_text('{"nested": true}\n', encoding="utf-8")
    runtime = imported_workflow["tmp"] / "inventory-runtime"
    runtime_manifest = build_shared_runtime(
        viewer_root=imported_workflow["viewer"], output=runtime
    )
    assert "runtime_manifest.json" not in {
        record["path"] for record in runtime_manifest["files"]
    }
    assert "viewer/shared/runtime_manifest.json" in {
        record["path"] for record in runtime_manifest["files"]
    }
    output = imported_workflow["tmp"] / "inventory-thin"
    build_expert_package(
        store,
        batch_id="batch-r1",
        expert_id="expert-a",
        output=output,
        mode="thin",
    )

    spec = importlib.util.spec_from_file_location(
        "inventory_thin_server", output / "serve_bundle.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _manifest, bundle_allowed = module.verify_bundle_manifest()
    _runtime, runtime_root, runtime_allowed = module.verify_runtime(runtime)
    (runtime / "viewer/undeclared.js").write_text("window.bad = true;\n")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(
            module.Handler,
            runtime_root=runtime_root,
            runtime_allowed=runtime_allowed,
            bundle_allowed=bundle_allowed,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(
            base + "/viewer/shared/runtime_manifest.json", timeout=3
        ) as response:
            assert json.load(response) == {"nested": True}
        for blocked in ("/runtime_manifest.json", "/viewer/undeclared.js"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + blocked, timeout=3)
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_expert_molstar_focus_has_large_canvas_and_niivue_keeps_responsive_layout(
    workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    workflow["tasks"][2]["asset"]["viewer"] = "niivue"
    workflow["tasks_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in workflow["tasks"]), encoding="utf-8"
    )
    screenshot_api = Path(__file__).resolve().parents[2] / "viewer/shared/screenshot-api.js"
    (workflow["viewer"] / "shared/screenshot-api.js").write_text(
        screenshot_api.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (workflow["viewer"] / "molstar.html").write_text(
        """<!doctype html><html><head>
<script src="/viewer/shared/screenshot-api.js"></script></head><body>
<canvas id="mol-canvas"></canvas><script>
const params = new URLSearchParams(location.search);
const fixtureOptions = {
      layoutShowSequence: true,
};
const canvas = document.getElementById('mol-canvas');
function resizeCanvas() {
  const controlWidth = params.get('embed') === 'expert' ? 32 : 630;
  canvas.width = Math.max(1, window.innerWidth - controlWidth);
  canvas.height = Math.max(1, window.innerHeight - 80);
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);
window.__ScienceVisionScreenshotProvider.ready(() => new Promise((resolve, reject) => {
  canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('encode failed')), 'image/png');
}));
</script></body></html>""",
        encoding="utf-8",
    )
    store = workflow["store"]
    admission_receipt = workflow["write_admission_receipt"](
        workflow["root"] / "layout-admission.json",
        workflow["tasks_path"],
        workflow["tasks"],
    )
    store.import_batch(
        batch_id="batch-layout",
        tasks_jsonl=workflow["tasks_path"],
        asset_root=workflow["root"],
        admission_receipt=admission_receipt,
    )
    store.auto_assign(batch_id="batch-layout")
    output = workflow["tmp"] / "layout-package"
    build_expert_package(
        store, batch_id="batch-layout", expert_id="expert-a", output=output
    )

    spec = importlib.util.spec_from_file_location("layout_package_server", output / "serve_package.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
            page.wait_for_function(
                "document.getElementById('sv-task-frame').contentDocument?.readyState === 'complete'"
            )
            api_state = page.locator("#sv-task-frame").evaluate(
                "frame => ({url: frame.contentWindow.location.href, "
                "api: Boolean(frame.contentWindow.ScienceVisionViewerScreenshot), "
                "ready: frame.contentWindow.ScienceVisionViewerScreenshot?.ready, "
                "provider: Boolean(frame.contentWindow.__ScienceVisionScreenshotProvider)})"
            )
            assert api_state["api"] and api_state["provider"], api_state
            assert api_state["ready"] is True, api_state
            assert page.locator("#workspace").get_attribute("data-viewer-focus") == "true"
            frame_box = page.locator("#sv-task-frame").bounding_box()
            assert frame_box and frame_box["width"] >= 1400
            assert "embed=expert" in page.locator("#sv-task-frame").get_attribute("src")
            page.wait_for_function("document.getElementById('sv-task-shot').disabled === false")
            page.locator("#sv-task-shot").click()
            page.wait_for_selector("#sv-shot-modal:not([hidden])")
            meta = page.locator("#sv-shot-meta").inner_text()
            # The package now ships the language layer, so this line is Chinese
            # by default; the image width is its first number in either language.
            image_width = int(re.search(r"(\d+)", meta).group(1))
            assert image_width >= 1300
            page.locator("#sv-shot-close").click()

            page.locator("#viewer-focus-toggle").click()
            page.locator(".task-item").nth(1).click()
            page.wait_for_function(
                "document.getElementById('sv-task-frame').getAttribute('src').includes('niivue.html')"
            )
            assert page.locator("#workspace").get_attribute("data-viewer-focus") == "false"
            assert page.locator(".question-pane").is_visible()
            niivue_box = page.locator("#sv-task-frame").bounding_box()
            assert niivue_box and niivue_box["width"] >= 600

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator(".task-rail").is_visible()
            assert page.locator(".question-pane").is_visible()
            mobile_box = page.locator("#sv-task-frame").bounding_box()
            assert mobile_box and 360 <= mobile_box["width"] <= 390
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_package_http_blocks_internal_source_and_qc_paths(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "served-package"
    build_expert_package(store, batch_id="batch-r1", expert_id="expert-a", output=output)
    (output / "sources").mkdir()
    (output / "sources/secret.txt").write_text("must not be served", encoding="utf-8")
    (output / "qc").mkdir()
    (output / "qc/secret.txt").write_text("must not be served", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("fixture_package_server", output / "serve_package.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for internal in ("sources/secret.txt", "qc/secret.txt"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/{internal}", timeout=3
                )
            assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_a_packaged_viewer_keeps_the_selection_mode_toggle(imported_workflow: dict) -> None:
    """The expert guide points at a toggle, so a package must not hide it.

    The runtime handed to the packager is a snapshot of viewer/, and older
    snapshots carry the flag that hid Mol*s selection-mode button -- the reason
    a reviewer found no way to measure a distance. The packager already repairs
    a stale layout and a stale screenshot script; the same has to hold here, or
    an offline package ships a guide describing a control it removed.
    """
    page = imported_workflow["viewer"] / "molstar.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "      layoutShowSequence: true,",
            "      layoutShowSequence: true,\n      viewportShowSelectionMode: false,",
        ),
        encoding="utf-8",
    )
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / "selection-mode-package"
    build_expert_package(store, batch_id="batch-r1", expert_id="expert-a", output=output)
    packaged = (output / "viewer/molstar.html").read_text(encoding="utf-8")
    assert "viewportShowSelectionMode: false" not in packaged
    assert "viewportShowSelectionMode: true," in packaged
    # This runtime also needed the layout injection, so the two repairs of the
    # same page have to survive each other.
    assert "collapseLeftPanel: params.get('embed') === 'expert'" in packaged
