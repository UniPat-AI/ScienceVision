"""Contract tests for the view state a screenshot is taken from.

A PNG on its own does not say what the expert was looking at, so reviewers
asked for the camera to be saved next to it. Viewers that can describe their
camera do; the ones that cannot stay silent, and the capture runtime has to
work either way.
"""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_API = ROOT / "viewer" / "shared" / "screenshot-api.js"

CANVAS_CHILD_BODY = """
  <canvas id="view" width="80" height="40"></canvas>
  <script>
    const canvas = document.getElementById('view');
    const context = canvas.getContext('2d');
    context.fillStyle = '#2f6fdb';
    context.fillRect(0, 0, 80, 40);
    const provider = () => new Promise((resolve, reject) => {
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('encode failed')), 'image/png');
    });
    window.__register(provider);
  </script>
"""


def _child_page(register: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<script src="/viewer/shared/screenshot-api.js"></script>'
        f"<script>window.__register = {register};</script>"
        f"</head><body>{CANVAS_CHILD_BODY}</body></html>"
    )


DESCRIBING_CHILD = _child_page(
    "provider => window.__ScienceVisionScreenshotProvider.ready(provider, {"
    " describeView: () => ({viewer: 'stub', mode: 'render',"
    " angles_degrees: {azimuth: 110, elevation: 15}}) })"
)
SILENT_CHILD = _child_page(
    "provider => window.__ScienceVisionScreenshotProvider.ready(provider)"
)
THROWING_CHILD = _child_page(
    "provider => window.__ScienceVisionScreenshotProvider.ready(provider, {"
    " describeView: () => { throw new Error('camera is gone'); } })"
)


def _html_route(body: str):
    """A one-argument handler.

    Playwright passes ``(route, request)`` to any handler that accepts two
    parameters, so capturing the body as a default argument would hand the
    request object to ``fulfill``.
    """

    def handler(route):
        route.fulfill(content_type="text/html", body=body)

    return handler


def _launch(playwright):
    from tests.viewer.test_screenshot_contract import _launch_browser

    return _launch_browser(playwright)


def test_the_contract_keeps_the_view_description_optional() -> None:
    """Offline bundles ship this file alone; the optionality has to be visible."""
    source = SCREENSHOT_API.read_text(encoding="utf-8")
    # Adding a method does not break a consumer, so the version stays at 1.
    assert "version: 1," in source
    assert "let viewDescriber = null;" in source
    assert "typeof viewDescriber !== 'function') return null;" in source


def test_view_description_is_optional_and_never_throws(viewer_server: str) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    pages = {
        "describing": DESCRIBING_CHILD,
        "silent": SILENT_CHILD,
        "throwing": THROWING_CHILD,
    }
    expected = {
        "describing": {
            "viewer": "stub",
            "mode": "render",
            "angles_degrees": {"azimuth": 110, "elevation": 15},
        },
        "silent": None,
        "throwing": None,
    }
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page()
        for name, body in pages.items():
            page.route(f"**/viewer/test-view-{name}.html", _html_route(body))
        for name in pages:
            page.goto(f"{viewer_server}/viewer/test-view-{name}.html", wait_until="load")
            page.wait_for_function("window.ScienceVisionViewerScreenshot?.ready === true")
            # The contract stays at version 1: the description is additive.
            assert page.evaluate("window.ScienceVisionViewerScreenshot.version") == 1
            assert page.evaluate(
                "window.ScienceVisionViewerScreenshot.describeView()"
            ) == expected[name], name
        browser.close()


def test_molstar_reports_its_camera_state(viewer_server: str) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    stub = """
      const helper = {
        cropParams: {auto: true, padding: 7},
        relativeCrop: {x: 0, y: 0, width: 1, height: 1},
        behaviors: {
          cropParams: {next(value) { helper.cropParams = {...value}; }},
          relativeCrop: {next(value) { helper.relativeCrop = {...value}; }},
        },
        async getImageDataUri() { return 'data:image/png;base64,iVBORw0KGgo='; },
      };
      window.molstar = {Viewer: {async create() {
        return {
          plugin: {
            helpers: {viewportScreenshot: helper},
            canvas3d: {camera: {state: {
              mode: 'perspective',
              fov: Math.PI / 4,
              position: [12.5, -3.25, 40],
              target: [0.5, 1, 2],
              up: [0, 1, 0],
              radius: 18.75,
            }}},
          },
          async loadStructureFromUrl() {},
          async loadStructureFromData() {},
        };
      }}};
    """
    with sync_playwright() as playwright:
        browser = _launch(playwright)
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
        view = page.evaluate("window.ScienceVisionViewerScreenshot.describeView()")
        assert view == {
            "viewer": "molstar",
            "mode": "perspective",
            "fov_degrees": 45.0,
            "position": {"x": 12.5, "y": -3.25, "z": 40.0},
            "target": {"x": 0.5, "y": 1.0, "z": 2.0},
            "up": {"x": 0.0, "y": 1.0, "z": 0.0},
            "radius": 18.75,
        }
        browser.close()


def test_niivue_reports_its_slice_and_render_angles(viewer_server: str) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    stub = """
      class Niivue {
        constructor(options) {
          this.opts = {...options};
          this.volumes = [];
          this.scene = {crosshairPos: [0.5, 0.25, 0.75], renderAzimuth: 110, renderElevation: 15};
          this.canvas = null;
          this.onLocationChange = null;
        }
        async attachTo(id) { this.canvas = document.getElementById(id); }
        async loadVolumes(volumes) { this.volumes = volumes.map(item => ({...item})); }
        setSliceType(value) { this.opts.sliceType = value; }
        updateGLVolume() {}
        drawScene() {}
        frac2vox(frac) { return frac.map(value => Math.round(value * 100)); }
        frac2mm(frac) { return frac.map(value => value * 10); }
      }
      window.niivue = {Niivue};
    """
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page()
        page.route(
            "**/viewer/shared/vendor/@niivue/niivue/0.45.0/dist/niivue.umd.js",
            lambda route: route.fulfill(content_type="application/javascript", body=stub),
        )
        page.goto(
            f"{viewer_server}/viewer/niivue.html?file=/data/volume.nii.gz",
            wait_until="load",
        )
        page.wait_for_function("window.ScienceVisionViewerScreenshot?.ready === true")
        page.wait_for_function("window.__svNiiVueMeasurements !== undefined")
        view = page.evaluate("window.ScienceVisionViewerScreenshot.describeView()")
        assert view == {
            "viewer": "niivue",
            "mode": "multiplanar",
            "slice_type": 3,
            "crosshair": {
                "voxel": [50, 25, 75],
                "world_ras_mm": [5.0, 2.5, 7.5],
            },
            "angles_degrees": {"azimuth": 110, "elevation": 15},
        }
        browser.close()
