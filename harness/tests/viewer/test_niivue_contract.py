from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tools.offline_task_manager.contract import object_sha256


ROOT = Path(__file__).resolve().parents[2]
NIIVUE = ROOT / "viewer" / "niivue.html"
TASKS = ROOT / "viewer" / "sciencevision_tasks.html"


def test_niivue_declares_coordinate_readout_crosshair_and_label_filter():
    text = NIIVUE.read_text()
    for token in (
        "location-readout",
        "world RAS mm",
        "frac2vox",
        "frac2mm",
        "crosshairWidth",
        "mask_id",
        "filterOverlayToMaskId",
        "volume.img[index] = Number(volume.img[index]) === targetMaskId ? 1 : 0",
        "maskFilter",
    ):
        assert token in text


def test_model_task_projection_does_not_read_reference_or_qc_fields():
    text = TASKS.read_text()
    start = text.index("function modelTask(raw)")
    end = text.index("function syncRouteModeButtons", start)
    projection = text[start:end]
    for forbidden in (
        "raw.ground_truth",
        "raw.validation_record",
        "raw.acceptance_info",
        "raw.asset_grounding",
        "raw.scorer_config",
        "raw.provenance",
    ):
        assert forbidden not in projection
    assert "parsedRows.map(modelTask)" in text
    assert "state.routeMode === 'expert'" in text


def test_task_viewer_accepts_same_origin_bundle_manifest_without_replacing_set_mode():
    text = TASKS.read_text()
    for token in (
        "p.get('manifest')",
        "const manifestURL = bundleManifestURL(manifestReference)",
        "await verifyBundleManifestDigest(manifest)",
        "manifest.task_file.path",
        "const taskURL = bundleResourceURL(manifest.task_file.path, manifestURL)",
        "await sha256Hex(taskText) !== manifest.task_file.sha256",
        "bindBundleTask(row.task, manifestURL, assetClosure)",
        "bound[pathKey] = bundleResourceURL(record.path, manifestURL)",
        "return {...task, task_id: row.task_id || task.task_id}",
        "else if (state.setName) p.set('set', state.setName)",
        "await loadSetIndex()",
    ):
        assert token in text


def test_task_viewer_browser_rejects_unsafe_manifest_paths_and_digest_drift(
    viewer_server: str,
):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error, sync_playwright

    task_row = {
        "schema_version": "sciencevision.expert_task.v2",
        "contract_version": "sciencevision.expert_exchange.v2",
        "batch_id": "batch-browser",
        "expert_id": "expert-browser",
        "assignment_id": "asgn_" + "1" * 24,
        "task_id": "bundle-task",
        "task_revision_sha256": "2" * 64,
        "task": {
            "task_id": "bundle-task",
            "title": "Bundle task",
            "domain": "biology",
            "asset": {
                "path": "/assets/model.svg",
                "repo_path": "assets/model.svg",
                "format": "svg",
                "viewer": "svg",
            },
        },
    }
    task_text = json.dumps(
        task_row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'

    def make_manifest(task_path: str = "tasks/tasks.jsonl", task_hash: str | None = None):
        body = {
            "schema_version": "sciencevision.expert_task_bundle.v2",
            "contract_version": "sciencevision.expert_exchange.v2",
            "bundle_id": "bndl_" + "3" * 24,
            "batch_id": "batch-browser",
            "expert_id": "expert-browser",
            "expert": {"name": "Browser Expert", "domains": ["biology"]},
            "admission": {},
            "task_file": {
                "path": task_path,
                "media_type": "application/x-ndjson",
                "count": 1,
                "sha256": task_hash or hashlib.sha256(task_text.encode()).hexdigest(),
            },
            "assignments": [],
            "asset_count": 1,
            "asset_bytes": len(svg),
            "asset_closure_sha256": "4" * 64,
            "assets": [
                {
                    "path": "assets/model.svg",
                    "sha256": hashlib.sha256(svg).hexdigest(),
                    "byte_size": len(svg),
                }
            ],
            "runtime_requirement": {
                "loader_api": "sciencevision.viewer_loader.v1",
                "renderers": [],
                "capabilities": [],
            },
            "redaction": {},
            "submission": {},
        }
        return body | {"bundle_manifest_sha256": object_sha256(body)}

    current_manifest = make_manifest()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Error as exc:
            chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            if not chrome.exists():
                pytest.skip(f"Playwright Chromium unavailable: {exc}")
            browser = pw.chromium.launch(executable_path=str(chrome))
        page = browser.new_page()
        page.route(
            "**/bundles/demo/bundle_manifest.json",
            lambda route: route.fulfill(json=current_manifest),
        )
        page.route(
            "**/bundles/demo/tasks/tasks.jsonl",
            lambda route: route.fulfill(
                body=task_text, content_type="application/x-ndjson"
            ),
        )
        page.route(
            "**/bundles/demo/assets/model.svg",
            lambda route: route.fulfill(body=svg, content_type="image/svg+xml"),
        )
        page.goto(
            f"{viewer_server}/viewer/sciencevision_tasks.html?"
            + urlencode({"manifest": "/bundles/demo/bundle_manifest.json"}),
            wait_until="load",
        )
        page.wait_for_function(
            "document.getElementById('sv-task-status').textContent.startsWith('1 tasks')"
        )
        assert page.evaluate("sha256Fallback('abc')") == (
            "ba7816bf8f01cfea414140de5dae2223"
            "b00361a396177a9cb410ff61f20015ad"
        )
        assert "bndl_" in page.locator("#sv-task-counts").inner_text()

        for unsafe in (
            "../secret.json",
            "..\\secret.json",
            "/bundles/demo/bundle_manifest.json?revision=1",
            "/bundles/demo/bundle_manifest.json#fragment",
            "https://example.test/bundle_manifest.json",
        ):
            page.goto(
                f"{viewer_server}/viewer/sciencevision_tasks.html?"
                + urlencode({"manifest": unsafe}),
                wait_until="load",
            )
            page.wait_for_function(
                "document.getElementById('sv-task-status').textContent === 'failed'"
            )

        current_manifest = make_manifest(task_path="../tasks.jsonl")
        page.goto(
            f"{viewer_server}/viewer/sciencevision_tasks.html?"
            + urlencode({"manifest": "/bundles/demo/bundle_manifest.json"}),
            wait_until="load",
        )
        page.wait_for_function(
            "document.getElementById('sv-task-panel').textContent.includes('safe POSIX path')"
        )

        current_manifest = make_manifest(task_hash="f" * 64)
        page.goto(
            f"{viewer_server}/viewer/sciencevision_tasks.html?"
            + urlencode({"manifest": "/bundles/demo/bundle_manifest.json"}),
            wait_until="load",
        )
        page.wait_for_function(
            "document.getElementById('sv-task-panel').textContent.includes('task JSONL SHA-256')"
        )

        current_manifest = make_manifest() | {"bundle_manifest_sha256": "f" * 64}
        page.goto(
            f"{viewer_server}/viewer/sciencevision_tasks.html?"
            + urlencode({"manifest": "/bundles/demo/bundle_manifest.json"}),
            wait_until="load",
        )
        page.wait_for_function(
            "document.getElementById('sv-task-panel').textContent.includes('manifest SHA-256')"
        )
        browser.close()


def test_niivue_browser_smoke_binarizes_mask_and_updates_readout(viewer_server: str):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error, sync_playwright

    stub = """
      window.niivue = { Niivue: class {
        constructor(options) {
          window.__nvOptions = options;
          window.__nvOrder = [];
          window.__nvInstance = this;
          this.opts = {...options};
          this.scene = {crosshairPos: [0.25, 0.5, 0.75]};
          this.volumes = [];
          this.initialized = true;
          this.screenSlices = [];
        }
        attachTo(id) {
          window.__nvOrder.push('attach');
          this.canvas = document.getElementById(id);
          this.canvas.width = 64;
          this.canvas.height = 32;
        }
        frac2vox() { return [10, 20, 30]; }
        frac2mm() { return [1.25, 2.5, 3.75]; }
        setSliceType(value) { this.opts.sliceType = value; }
        drawScene() { window.__nvOrder.push('draw'); }
        updateGLVolume() { window.__updated = true; }
        async loadVolumes(volumes) {
          window.__nvOrder.push('load');
          this.volumes = [
            {img: new Int16Array([0]), hdr: {}},
            {
              img: new Int16Array([0, 1, 2, 3, 2]),
              hdr: {scl_slope: 1, scl_inter: 0, cal_min: 1, cal_max: 3},
              cal_min: 1,
              cal_max: 3,
            },
          ];
          window.__loadedVolumes = this.volumes;
        }
      }};
    """
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Error as exc:
            chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            if not chrome.exists():
                pytest.skip(f"Playwright Chromium unavailable: {exc}")
            browser = pw.chromium.launch(executable_path=str(chrome))
        page = browser.new_page()
        page.route(
            "**/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js",
            lambda route: route.fulfill(content_type="application/javascript", body=stub),
        )
        query = urlencode({
            "file": "/data/ct.nii.gz",
            "overlay": "/data/mask.nii.gz",
            "mask_id": "2",
            "mode": "model",
        })
        page.goto(f"{viewer_server}/viewer/niivue.html?{query}", wait_until="load")
        page.wait_for_function("window.__updated === true")
        assert page.evaluate("window.__nvOptions.crosshairWidth") == 1
        assert page.evaluate("Array.from(window.__loadedVolumes[1].img)") == [
            0,
            0,
            1,
            0,
            1,
        ]
        assert page.evaluate("window.__loadedVolumes[1].hdr.scl_slope") == 1
        assert page.evaluate("window.__loadedVolumes[1].hdr.scl_inter") == 0
        assert page.evaluate("window.__loadedVolumes[1].cal_min") == 0
        assert page.evaluate("window.__loadedVolumes[1].cal_max") == 1
        assert page.locator("body").get_attribute("data-mask-filter") == "applied"
        assert page.locator("body").get_attribute("data-mask-id") == "2"
        readout = page.locator("#location-readout").inner_text()
        assert "[10, 20, 30]" in readout
        assert "[1.25, 2.50, 3.75]" in readout
        assert page.locator("#viewer-mode").inner_text() == "model"
        page.wait_for_function("window.__svNiiVueMeasurements !== undefined")
        assert page.locator("#sv-niivue-measure-panel").count() == 1
        assert page.evaluate(
            "window.__nvInstance.__svMeasurementController === "
            "window.__svNiiVueMeasurements"
        )
        page.wait_for_function("window.ScienceVisionViewerScreenshot?.ready === true")
        capture = page.evaluate("""async () => {
          const blob = await window.ScienceVisionViewerScreenshot.captureScreenshot();
          return {type: blob.type, size: blob.size};
        }""")
        assert capture["type"] == "image/png"
        assert capture["size"] > 0
        order = page.evaluate("window.__nvOrder")
        assert order[:2] == ["attach", "load"]
        assert order[2:] and set(order[2:]) == {"draw"}
        browser.close()
