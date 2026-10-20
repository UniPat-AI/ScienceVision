"""The expert workspace records the operations that changed a judgement.

Reviewers asked for the reasoning behind the few semantically key operations --
"one zoom was not enough, so I zoomed in again" -- so the workspace collects an
action plus the reason it mattered, and refuses a step without a reason.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.offline_task_manager.packager import build_expert_package

from tests.offline_task_manager.test_packager import _launch_browser


@contextmanager
def served_package(imported_workflow: dict, name: str):
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    output = imported_workflow["tmp"] / name
    build_expert_package(
        store, batch_id="batch-r1", expert_id="expert-a", output=output
    )
    spec = importlib.util.spec_from_file_location(
        f"{name}_server", output / "serve_package.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def open_workspace(playwright, url: str):
    browser = _launch_browser(playwright)
    # Below the 1000px threshold at which a molstar task opens with the viewer
    # maximised, which hides the pane the response form lives in.
    page = browser.new_page(viewport={"width": 980, "height": 900})
    page.goto(url, wait_until="load")
    page.wait_for_selector("#response-form:not([hidden])")
    return browser, page


def record(page, action: str, note: str) -> None:
    page.select_option("#step-action", action)
    page.fill("#step-note", note)
    page.click("#step-add")


def test_key_operations_are_recorded_and_survive_a_reload(
    imported_workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with served_package(imported_workflow, "step-package") as url:
        with sync_playwright() as playwright:
            browser, page = open_workspace(playwright, url)
            record(page, "zoom_in", "One zoom was not enough to read the ligand.")
            page.wait_for_selector(".step-item")
            assert "One zoom was not enough" in page.locator(".step-item").inner_text()
            assert page.locator("#step-note").input_value() == ""

            # A step without a reason is exactly the mouse-move noise reviewers
            # asked us not to collect, so it is refused rather than stored.
            page.click("#step-add")
            assert page.locator(".step-item").count() == 1
            assert page.locator("#step-status").inner_text().strip()

            page.reload(wait_until="load")
            page.wait_for_selector(".step-item")
            assert page.locator(".step-item").count() == 1
            page.locator(".step-remove").first.click()
            page.wait_for_selector(".step-item", state="detached")
            page.reload(wait_until="load")
            page.wait_for_selector("#response-form:not([hidden])")
            assert page.locator(".step-item").count() == 0
            browser.close()


def test_the_exported_submission_carries_the_recorded_steps(
    imported_workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with served_package(imported_workflow, "export-step-package") as url:
        with sync_playwright() as playwright:
            browser, page = open_workspace(playwright, url)
            first = page.evaluate("decodeURIComponent(location.hash.slice(1))")
            record(page, "zoom_in", "One zoom was not enough to read the ligand.")
            page.wait_for_selector(".step-item")
            record(page, "rotate", "The ligand hid the binding pocket.")
            page.wait_for_selector(".step-item:nth-child(2)")
            page.fill("#validation-feedback", "The task is clear.")
            page.fill("#answer", "Answer text")
            page.fill("#reasoning", "Reasoning text")

            with page.expect_download() as download:
                page.click("#export-results")
            payload = json.loads(
                Path(download.value.path()).read_text(encoding="utf-8")
            )
            browser.close()

    assert [row["assignment_id"] for row in payload["results"]] == [first]
    recorded = payload["results"][0]["trajectory"]
    assert recorded["schema_version"] == "sciencevision.trajectory.v1"
    assert recorded["status"] == "collected"
    assert [step["index"] for step in recorded["steps"]] == [1, 2]
    assert [step["action"] for step in recorded["steps"]] == ["zoom_in", "rotate"]
    assert "read the ligand" in recorded["steps"][0]["note"]
    assert all(step["at"] for step in recorded["steps"])
    report = imported_workflow["store"].import_submission(payload)
    assert report["counts"]["rejected"] == 0
    assert report["counts"]["accepted"] == 1
