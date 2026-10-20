from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from tools import build_scale_v2_1_expert_bundle as bundle


ROOT = Path(__file__).resolve().parents[2]
MOLSTAR = ROOT / "viewer" / "molstar.html"
NIIVUE = ROOT / "viewer" / "niivue.html"
TASKS = ROOT / "viewer" / "sciencevision_tasks.html"
SCREENSHOT_API = ROOT / "viewer" / "shared" / "screenshot-api.js"
TASK_SCREENSHOT = ROOT / "viewer" / "shared" / "task-screenshot.js"


def _bundle_molstar_fixture() -> str:
    return textwrap.dedent(
        """\
        <!doctype html><html><head>
        <script src="/viewer/shared/neg-banner.js" defer></script>
        <script src="/viewer/shared/capture/annotate-shell.js" type="module"></script>
        </head><body><div id="viewer"></div><script>
          const params = new URLSearchParams(location.search);
          const file = params.get('file');
          const format = params.get('type') || 'mmcif';
          let files = [];
          const filesJSON = params.get('files');
          if (filesJSON) files = JSON.parse(filesJSON);
          if (!files.length && file) files = [{ file, type: format }];
          if (!files.length) {
            if (!filesJSON) document.getElementById('viewer').innerHTML = '<div class="error-box">No ?file= or ?files= query param.</div>';
          } else {
            const FORMAT_ALIAS = { pdb: 'pdb' };
            molstar.Viewer.create('viewer', {}).then(async viewer => {
              for (const item of files) {
                const normalized = String(item.type || 'mmcif').toLowerCase();
                const molFormat = FORMAT_ALIAS[normalized] || normalized;
                await viewer.loadStructureFromUrl(item.file, molFormat);
              }
            })
              .catch(err => {
                const errorBox = document.createElement('div');
                errorBox.textContent = String(err);
              });
          }
        </script>
        </body></html>
        """
    )


def _bundle_niivue_fixture() -> str:
    return textwrap.dedent(
        """\
        <!doctype html><html><head>
        <script src="/viewer/shared/neg-banner.js" defer></script>
        </head><body><canvas id="gl"></canvas><div id="view-toggle"></div>
        <div id="gesture-hint"></div><script>
          const params = new URLSearchParams(location.search);
          const file = params.get('file');
          const overlay = params.get('overlay');
          if (!file) {
            document.body.insertAdjacentHTML('beforeend', '<div class="error-box">No ?file= query param.</div>');
          } else {
            // niivue 0.45 SLICE_TYPE: AXIAL=0, CORONAL=1, SAGITTAL=2, MULTIPLANAR=3, RENDER=4
            const SLICE = { axial: 0, multiplanar: 3, render: 4 };
            const nv = new niivue.Niivue({ sliceType: SLICE.render });
            nv.attachTo('gl');
            const volumes = [{ url: file, opacity: 1.0 }];
            if (overlay) volumes.push({ url: overlay, opacity: 0.5 });
            nv.loadVolumes(volumes).catch(err => {
              document.body.insertAdjacentHTML('beforeend',
                '<div class="error-box">Failed to load:\\n' + err + '</div>');
            });

            const toggle = document.getElementById('view-toggle');
            const hint = document.getElementById('gesture-hint');
            const HINTS = { render: 'render' };
            toggle.addEventListener('click', e => {
              const btn = e.target.closest('button[data-mode]');
              if (!btn) return;
              const mode = btn.dataset.mode;
              nv.setSliceType(SLICE[mode]);
              hint.textContent = HINTS[mode] || '';
            });
          }
        </script>
        </body></html>
        """
    )


def _launch_browser(playwright):
    from playwright.sync_api import Error

    try:
        return playwright.chromium.launch()
    except Error as exc:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.exists():
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        return playwright.chromium.launch(executable_path=str(chrome))


def test_viewers_publish_screenshot_contract_after_rendering():
    api = SCREENSHOT_API.read_text()
    assert "ScienceVisionViewerScreenshot" in api
    assert "captureScreenshot" in api
    assert "asPngBlob" in api

    molstar = MOLSTAR.read_text()
    for token in (
        '/viewer/shared/screenshot-api.js',
        "auto: false",
        "{ x: 0, y: 0, width: 1, height: 1 }",
        "helper.behaviors.relativeCrop.next(previousRelativeCrop)",
        "helper.behaviors.cropParams.next(previousCropParams)",
        "screenshotProvider.ready",
        "_atom_site_fract_[xyz]",
        "loadStructureFromData(text, 'cifCore')",
    ):
        assert token in molstar

    niivue = NIIVUE.read_text()
    assert niivue.index("await nv.attachTo('gl')") < niivue.index(
        "await nv.loadVolumes(volumes)"
    ) < niivue.index("screenshotProvider.ready")
    assert "nv.drawScene()" in niivue
    assert "screenshotProvider.fail(err)" in niivue

    assert '/viewer/shared/task-screenshot.js' in TASKS.read_text()
    task_runtime = TASK_SCREENSHOT.read_text()
    for token in (
        "Capture the complete 3D viewer",
        "Screenshot crop preview",
        "navigator.clipboard.write",
        "Download PNG",
        "previousFocus",
        "MutationObserver",
    ):
        assert token in task_runtime


def test_bundle_task_screenshot_injection_is_idempotent():
    original = "<html><head></head><body><main></main></body></html>"
    injected = bundle.inject_task_screenshot_runtime(original)
    assert injected.count(bundle.TASK_SCREENSHOT_SCRIPT) == 1
    assert bundle.inject_task_screenshot_runtime(injected) == injected

    with pytest.raises(RuntimeError, match="unexpected body"):
        bundle.inject_task_screenshot_runtime("<html><body></body><body></body></html>")

    with pytest.raises(RuntimeError, match="partial screenshot integration"):
        bundle.inject_molstar_screenshot_runtime(bundle.SCREENSHOT_API_SCRIPT)


@pytest.mark.parametrize(
    ("injector", "fixture", "anchor"),
    (
        (
            bundle.inject_molstar_screenshot_runtime,
            _bundle_molstar_fixture,
            '<script src="/viewer/shared/capture/annotate-shell.js" type="module"></script>\n',
        ),
        (
            bundle.inject_niivue_screenshot_runtime,
            _bundle_niivue_fixture,
            '<script src="/viewer/shared/neg-banner.js" defer></script>\n',
        ),
    ),
)
def test_bundle_viewer_screenshot_injectors_fail_closed_on_malformed_anchor(
    injector, fixture, anchor
):
    malformed = fixture().replace(anchor, "")
    with pytest.raises(RuntimeError, match="unexpected runtime structure"):
        injector(malformed)


def test_bundle_runtime_overlay_preserves_reference_assets(tmp_path, monkeypatch):
    reference = tmp_path / "reference"
    stage = tmp_path / "stage"
    viewer = reference / "viewer"
    shared = viewer / "shared"
    preserved = {
        "vendor/molstar/4.5.0/build/viewer/molstar.js": "molstar-vendor-sentinel",
        "vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js": "niivue-vendor-sentinel",
        "capture/annotate-shell.js": "capture-sentinel",
        "capture/viewer-adapters.js": "adapter-sentinel",
        "molstar-query.mjs": "query-sentinel",
    }
    for relative, content in preserved.items():
        path = shared / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    viewer.mkdir(parents=True, exist_ok=True)
    (viewer / "molstar.html").write_text(_bundle_molstar_fixture())
    (viewer / "niivue.html").write_text(_bundle_niivue_fixture())
    (viewer / "sciencevision_tasks.html").write_text(
        textwrap.dedent(
            """\
            <!doctype html><html><head><style>
            </style>
            </head><body><script>
              if (viewer === 'niivue' && a.mask_path) params.set('overlay', a.mask_path);
            </script>
            </body></html>
            """
        )
    )

    def copy_fake_runtime(destination: Path, source: Path) -> None:
        assert source == reference
        shutil.copytree(source, destination)

    monkeypatch.setattr(bundle.base, "copy_runtime", copy_fake_runtime)
    monkeypatch.setattr(bundle.base, "sanitize_runtime_protocol_literals", lambda _: None)
    bundle.copy_runtime(stage, reference)

    for relative, content in preserved.items():
        assert (stage / "viewer/shared" / relative).read_text() == content
    for name in bundle.SCREENSHOT_SHARED_FILES:
        assert (stage / "viewer/shared" / name).read_bytes() == (
            ROOT / "viewer/shared" / name
        ).read_bytes()

    molstar = (stage / "viewer/molstar.html").read_text()
    niivue = (stage / "viewer/niivue.html").read_text()
    tasks = (stage / "viewer/sciencevision_tasks.html").read_text()
    assert molstar.count(bundle.SCREENSHOT_API_SCRIPT) == 1
    assert niivue.count(bundle.SCREENSHOT_API_SCRIPT) == 1
    assert "params.get('files')" in molstar
    assert "screenshotProvider.ready" in molstar
    assert "screenshotProvider.ready" in niivue
    assert "await nv.attachTo('gl')" in niivue
    assert "await nv.loadVolumes(volumes)" in niivue
    assert "Failed to load:\\n' + err" in niivue
    assert "Failed to load:\n' + err" not in niivue
    assert bundle.TASK_SCREENSHOT_SCRIPT in tasks


def test_molstar_browser_smoke_captures_full_viewport_and_restores_crop(viewer_server: str):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    stub = """
      window.__shotEvents = [];
      const helper = {
        cropParams: {auto: true, padding: 7},
        relativeCrop: {x: 0.2, y: 0.1, width: 0.5, height: 0.6},
        behaviors: {
          cropParams: {next(value) {
            window.__shotEvents.push(['crop', {...value}]);
            helper.cropParams = {...value};
          }},
          relativeCrop: {next(value) {
            window.__shotEvents.push(['relative', {...value}]);
            helper.relativeCrop = {...value};
          }},
        },
        async getImageDataUri() {
          window.__shotEvents.push(['capture']);
          return 'data:image/png;base64,iVBORw0KGgo=';
        },
      };
      window.molstar = {Viewer: {async create() {
        return {
          plugin: {helpers: {viewportScreenshot: helper}},
          async loadStructureFromUrl() { window.__structureLoaded = true; },
          async loadStructureFromData() { window.__structureLoaded = true; },
        };
      }}};
    """
    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = browser.new_page()
        page.route(
            "**/viewer/shared/vendor/molstar/4.5.0/build/viewer/molstar.js",
            lambda route: route.fulfill(content_type="application/javascript", body=stub),
        )
        page.goto(
            f"{viewer_server}/viewer/molstar.html?file=/data/model.pdb&type=pdb",
            wait_until="load",
        )
        page.wait_for_function("window.ScienceVisionViewerScreenshot?.ready === true")
        capture = page.evaluate("""async () => {
          const blob = await window.ScienceVisionViewerScreenshot.captureScreenshot();
          return {type: blob.type, size: blob.size};
        }""")
        assert capture == {"type": "image/png", "size": 8}
        assert page.evaluate("window.__shotEvents") == [
            ["crop", {"auto": False, "padding": 7}],
            ["relative", {"x": 0, "y": 0, "width": 1, "height": 1}],
            ["capture"],
            ["relative", {"x": 0.2, "y": 0.1, "width": 0.5, "height": 0.6}],
            ["crop", {"auto": True, "padding": 7}],
        ]
        browser.close()


def test_task_page_captures_crops_and_downloads_png(viewer_server: str):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    child = """<!doctype html><html><body>
      <script src="/viewer/shared/screenshot-api.js"></script>
      <canvas id="view" width="80" height="40"></canvas>
      <script>
        const canvas = document.getElementById('view');
        const context = canvas.getContext('2d');
        context.fillStyle = '#ef4444';
        context.fillRect(0, 0, 40, 40);
        context.fillStyle = '#22c55e';
        context.fillRect(40, 0, 40, 40);
        setTimeout(() => {
          window.__ScienceVisionScreenshotProvider.ready(() => new Promise((resolve, reject) => {
            canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('encode failed')), 'image/png');
          }));
        }, 250);
      </script>
    </body></html>"""

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = browser.new_page(accept_downloads=True, viewport={"width": 1200, "height": 800})
        page.route(
            "**/data/qa_tasks_index.json",
            lambda route: route.fulfill(
                content_type="application/json", body='{"sets": []}'
            ),
        )
        page.route(
            "**/viewer/test-screenshot-child.html",
            lambda route: route.fulfill(content_type="text/html", body=child),
        )
        page.route(
            "**/viewer/test-unsupported-child.html",
            lambda route: route.fulfill(
                content_type="text/html", body="<!doctype html><p>unsupported viewer</p>"
            ),
        )
        page.goto(
            f"{viewer_server}/viewer/sciencevision_tasks.html?task=demo-task",
            wait_until="load",
        )
        page.wait_for_selector("#sv-task-shot", state="attached")
        assert page.locator(".sv-task-viewer-bar").is_hidden()
        assert not page.locator(".sv-task-frame-wrap").evaluate(
            "node => node.classList.contains('sv-task-screenshot-enabled')"
        )
        page.evaluate("""() => {
          document.getElementById('sv-task-frame').src = '/viewer/test-screenshot-child.html';
        }""")
        page.wait_for_function("document.querySelector('.sv-task-viewer-bar').hidden === false")
        assert page.locator("#sv-task-shot").is_disabled()
        assert page.locator("#sv-task-shot-status").inner_text() == "Viewer loading..."
        page.wait_for_function("document.getElementById('sv-task-shot').disabled === false")
        assert page.locator(".sv-task-viewer-bar").is_visible()
        assert page.locator(".sv-task-frame-wrap").evaluate(
            "node => node.classList.contains('sv-task-screenshot-enabled')"
        )
        page.locator("#sv-task-shot").click()
        page.wait_for_selector("#sv-shot-modal:not([hidden])")
        assert "Image 80 x 40 px" in page.locator("#sv-shot-meta").inner_text()

        box = page.locator("#sv-shot-canvas").bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + 8, box["y"] + 6)
        page.mouse.down()
        page.mouse.move(box["x"] + 48, box["y"] + 30)
        page.mouse.up()
        meta = page.locator("#sv-shot-meta").inner_text()
        assert "Selection 40 x 24 px at 8, 6" in meta

        with page.expect_download() as download_info:
            page.locator("#sv-shot-download").click()
        download = download_info.value
        assert download.suggested_filename.startswith("demo-task-crop-")
        assert download.suggested_filename.endswith(".png")
        payload = Path(download.path()).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")

        page.locator("#sv-shot-close").click()
        assert page.evaluate("document.activeElement.id") == "sv-task-shot"

        page.locator("#sv-task-shot").click()
        page.wait_for_selector("#sv-shot-modal:not([hidden])")
        page.evaluate("""() => {
          document.getElementById('sv-task-frame').src = '/viewer/test-unsupported-child.html';
        }""")
        page.wait_for_function("document.querySelector('.sv-task-viewer-bar').hidden === true")
        assert page.locator("#sv-shot-modal").is_hidden()
        assert page.locator("#sv-shot-canvas").evaluate("canvas => [canvas.width, canvas.height]") == [1, 1]
        assert not page.locator(".sv-task-frame-wrap").evaluate(
            "node => node.classList.contains('sv-task-screenshot-enabled')"
        )

        page.evaluate("""() => {
          document.getElementById('sv-task-frame').src = '/viewer/test-screenshot-child.html';
        }""")
        page.wait_for_function("document.getElementById('sv-task-shot').disabled === false")
        assert page.locator(".sv-task-viewer-bar").is_visible()
        assert page.locator(".sv-task-frame-wrap").evaluate(
            "node => node.classList.contains('sv-task-screenshot-enabled')"
        )
        browser.close()
