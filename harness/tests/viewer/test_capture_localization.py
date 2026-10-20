"""Contract tests for the localized capture controls.

The capture bar and its crop dialog live in the shared viewer runtime. The
expert workspace loads that runtime next to the language layer, while the
standalone viewer pages and the offline bundles load it alone. Reviewers asked
for a Chinese interface, so the runtime has to follow the layer when it is
there and keep its English wording when it is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CAPTURE_JS = ROOT / "viewer" / "shared" / "task-screenshot.js"
I18N_JS = ROOT / "tools" / "offline_task_manager" / "online_app" / "i18n.js"

CJK = re.compile(r"[一-鿿]")

# Wording the standalone viewer pages and the offline bundles still show,
# because they never load the language layer.
ENGLISH_FALLBACKS = (
    "Screenshot",
    "Capturing...",
    "Capture the complete 3D viewer",
    "Viewer loading...",
    "No viewer selected",
    "Crop screenshot",
    "Reset selection",
    "Copy selection",
    "Download PNG",
)


def _language_table(language: str) -> dict[str, str]:
    source = I18N_JS.read_text(encoding="utf-8")
    opening = "'zh-CN': {" if language == "zh-CN" else "\n    en: {"
    start = source.index(opening) + len(opening)
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                body = source[start:index]
                break
    else:  # pragma: no cover - a malformed table fails the assertions below
        raise AssertionError("unbalanced language table")
    return dict(re.findall(r"'([\w.\- ]+)': '([^']*)'", body))


def test_capture_runtime_resolves_its_text_through_the_language_layer() -> None:
    source = CAPTURE_JS.read_text(encoding="utf-8")
    assert "window.SVI18n" in source, "the capture runtime must consult the layer"
    referenced = set(re.findall(r"['\"](capture\.[\w.]+)['\"]", source))
    assert referenced, "the capture runtime must resolve its text through keys"
    chinese = _language_table("zh-CN")
    unknown = referenced - set(chinese)
    assert not unknown, f"capture runtime resolves unknown keys: {sorted(unknown)}"
    # A language switch has to redraw the injected bar and dialog.
    assert "onChange(" in source


def test_capture_runtime_keeps_its_english_wording_as_a_fallback() -> None:
    source = CAPTURE_JS.read_text(encoding="utf-8")
    for wording in ENGLISH_FALLBACKS:
        assert wording in source, f"{wording!r} must survive as the English fallback"


def _capture_host_page() -> str:
    """A minimal stand-in for the expert workspace shell."""
    # The real workspace declares utf-8, which is what a classic script without
    # its own charset inherits, so the stand-in has to declare it too.
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body>
      <div style="height: 64px"></div><!-- stands in for the workspace topbar,
        so the fixed language toggle does not sit over the capture bar -->
      <div class="sv-task-frame-wrap"><iframe id="sv-task-frame" title="viewer"></iframe></div>
      <script src="/tools/offline_task_manager/online_app/i18n.js"></script>
      <script src="/viewer/shared/task-screenshot.js"></script>
    </body></html>"""


def _capture_child_page() -> str:
    return """<!doctype html><html><body>
      <script src="/viewer/shared/screenshot-api.js"></script>
      <canvas id="view" width="80" height="40"></canvas>
      <script>
        const canvas = document.getElementById('view');
        const context = canvas.getContext('2d');
        context.fillStyle = '#2f9e73';
        context.fillRect(0, 0, 80, 40);
        window.__ScienceVisionScreenshotProvider.ready(() => new Promise((resolve, reject) => {
          canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('encode failed')), 'image/png');
        }));
      </script>
    </body></html>"""


def test_capture_controls_follow_the_language_layer(viewer_server: str):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.route(
            "**/viewer/test-capture-host.html",
            lambda route: route.fulfill(
                content_type="text/html", body=_capture_host_page()
            ),
        )
        page.route(
            "**/viewer/test-capture-child.html",
            lambda route: route.fulfill(
                content_type="text/html", body=_capture_child_page()
            ),
        )
        page.goto(f"{viewer_server}/viewer/test-capture-host.html", wait_until="load")
        page.wait_for_selector("#sv-task-shot", state="attached")

        # Chinese is the default, so the injected bar starts translated.
        assert CJK.search(page.locator("#sv-task-shot").text_content())
        page.evaluate(
            "() => { document.getElementById('sv-task-frame').src ="
            " '/viewer/test-capture-child.html'; }"
        )
        page.wait_for_function("document.getElementById('sv-task-shot').disabled === false")
        assert CJK.search(page.locator("#sv-task-shot").get_attribute("title"))

        page.locator("#sv-task-shot").click()
        page.wait_for_selector("#sv-shot-modal:not([hidden])")
        assert CJK.search(page.locator("#sv-shot-title").text_content())
        assert CJK.search(page.locator("#sv-shot-download").text_content())
        # The measurement line is rebuilt on every selection, so it also has to
        # follow a switch rather than freeze in the language it was drawn in.
        assert CJK.search(page.locator("#sv-shot-meta").text_content())

        # The dialog backdrop sits above the toggle, so the switch is dispatched
        # on the control itself instead of through a real pointer press.
        page.locator(".sv-lang-toggle").dispatch_event("click")
        assert page.locator("#sv-task-shot").text_content() == "Screenshot"
        assert page.locator("#sv-shot-title").text_content() == "Crop screenshot"
        assert page.locator("#sv-shot-download").text_content() == "Download PNG"
        assert "Image 80 x 40 px" in page.locator("#sv-shot-meta").text_content()
        browser.close()
