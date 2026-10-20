"""What the admin gets to read of an expert's key operations.

Reviewers asked for the reasoning behind the operations that moved a
judgement. Recording it in the workspace is only half the job: whoever
reviews the submission has to be able to read it. The results export carries
a count only -- ``validate_export_rows`` pins its field set exactly, so a new
column would change every CSV -- therefore the steps reach the console
through the overview payload instead.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator

import pytest

from tools.offline_task_manager.results_export import validate_export_rows
from tools.offline_task_manager.server import Handler, ManagerServer
from tools.offline_task_manager.store import ManagerStore

from tests.offline_task_manager.test_trajectory_contract import collected, step, submitter

ROTATE_NOTE = "The ligand hid the pocket, so I rotated to the other face."


def submit_two_steps(imported_workflow: dict) -> None:
    submit = submitter(imported_workflow)
    report = submit(collected(step(1), step(2, action="rotate", note=ROTATE_NOTE)))
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}


@contextlib.contextmanager
def served_console(store: ManagerStore) -> Iterator[str]:
    """Serve the manager console from the loopback-only offline server."""
    server = ManagerServer(("127.0.0.1", 0), Handler)
    server.store = store
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_overview_carries_the_steps_the_export_only_counts(
    imported_workflow: dict,
) -> None:
    submit_two_steps(imported_workflow)
    store = imported_workflow["store"]
    row = store.overview()["results"][0]
    assert row["trajectory_step_count"] == 2
    assert [item["index"] for item in row["trajectory_steps"]] == [1, 2]
    assert [item["action"] for item in row["trajectory_steps"]] == ["zoom_in", "rotate"]
    assert row["trajectory_steps"][1]["note"] == ROTATE_NOTE
    export_row = store.result_rows()[0]
    assert "trajectory_steps" not in export_row
    validate_export_rows([export_row])


def test_the_console_shows_each_recorded_step_with_its_reason(
    imported_workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    submit_two_steps(imported_workflow)
    with served_console(imported_workflow["store"]) as url:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url, wait_until="load")
            page.locator('[data-tab="results"]').click()
            page.wait_for_selector("#results-table tr")
            # The count answers "did the expert record anything"; the reasons
            # are one click away rather than crushed into the row.
            summary = page.locator("#results-table .result-steps summary")
            assert "2" in summary.inner_text()
            summary.click()
            steps = page.locator("#results-table .result-steps li")
            assert steps.count() == 2
            first = steps.nth(0).inner_text()
            second = steps.nth(1).inner_text()
            assert "zoom_in" in first
            assert "rotate" in second and ROTATE_NOTE in second
            browser.close()
