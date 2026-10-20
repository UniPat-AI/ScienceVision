"""The JSON sidecar a downloaded screenshot is saved with.

Reviewers asked for the view state to be written next to the PNG, so a
screenshot can be traced back to the camera it came from.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.viewer.test_screenshot_view_state import (
    DESCRIBING_CHILD,
    _html_route,
    _launch,
)


def test_download_saves_the_view_state_next_to_the_png(viewer_server: str) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page(
            accept_downloads=True, viewport={"width": 1200, "height": 800}
        )
        page.route(
            "**/data/qa_tasks_index.json",
            lambda route: route.fulfill(
                content_type="application/json", body='{"sets": []}'
            ),
        )
        page.route("**/viewer/test-view-describing.html", _html_route(DESCRIBING_CHILD))
        page.goto(
            f"{viewer_server}/viewer/sciencevision_tasks.html?task=demo-task",
            wait_until="load",
        )
        page.wait_for_selector("#sv-task-shot", state="attached")
        page.evaluate(
            "() => { document.getElementById('sv-task-frame').src ="
            " '/viewer/test-view-describing.html'; }"
        )
        page.wait_for_function("document.getElementById('sv-task-shot').disabled === false")
        page.locator("#sv-task-shot").click()
        page.wait_for_selector("#sv-shot-modal:not([hidden])")
        # The dialog says the view travels with the image.
        assert page.locator("#sv-shot-view").inner_text().strip()

        downloads: list = []
        # Playwright stores bookkeeping on the handler, which a builtin method
        # such as list.append cannot carry.
        page.on("download", lambda download: downloads.append(download))
        page.locator("#sv-shot-download").click()
        deadline = time.time() + 15
        while len(downloads) < 2 and time.time() < deadline:
            page.wait_for_timeout(100)
        assert len(downloads) == 2, [item.suggested_filename for item in downloads]

        by_suffix = {
            Path(item.suggested_filename).suffix: item for item in downloads
        }
        assert set(by_suffix) == {".png", ".json"}
        png = by_suffix[".png"]
        sidecar = by_suffix[".json"]
        assert Path(png.suggested_filename).stem == Path(sidecar.suggested_filename).stem
        assert Path(png.path()).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

        payload = json.loads(Path(sidecar.path()).read_text(encoding="utf-8"))
        assert payload["schema_version"] == "sciencevision.screenshot_view.v1"
        assert payload["task"] == "demo-task"
        assert payload["image"] == {
            "file": png.suggested_filename,
            "width": 80,
            "height": 40,
        }
        assert payload["selection"] == {"x": 0, "y": 0, "width": 80, "height": 40}
        assert payload["view"] == {
            "viewer": "stub",
            "mode": "render",
            "angles_degrees": {"azimuth": 110, "elevation": 15},
        }
        assert payload["viewer_url"].endswith("test-view-describing.html")
        assert payload["captured_at"].endswith("Z")
        browser.close()
