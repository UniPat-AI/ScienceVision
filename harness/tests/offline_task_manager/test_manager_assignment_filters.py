"""Narrowing the assignments table down to the work an admin is reviewing.

Reviewers asked to be able to pick out one expert's questions and act on them
-- quality-check, spot-check, return -- instead of scrolling one flat list.
A free-text box cannot express "expert-b, still assigned, chemistry batch", so
the panel offers one select per field the state actually carries, plus a
spot-check sample drawn from whatever the filters left.

The sample is a view, not a record: it changes what the admin is looking at and
nothing in the store, so it is dropped as soon as the slice underneath it
changes.
"""

from __future__ import annotations

import pytest

from tools.offline_task_manager.store import ManagerStore

from tests.offline_task_manager.conftest import write_jsonl
from tests.offline_task_manager.test_manager_result_steps import served_console
from tests.offline_task_manager.test_results import make_submission, prepared

ROWS = "#assignments-table tr:not([hidden])"


def assignments_across_two_batches(imported_workflow: dict) -> ManagerStore:
    """Five assignments spread over two experts, two statuses, two domains.

    ``prepared`` assigns batch-r1's four biology tasks round-robin and packages
    expert-a's; importing one submission moves a single assignment on to
    ``pending_review``. The chemistry batch needs an expert who covers the
    domain, so it brings its own.
    """
    manifest, assignment = prepared(imported_workflow)
    store: ManagerStore = imported_workflow["store"]
    store.import_submission(make_submission(manifest, assignment))
    store.import_experts_csv(
        "schema_version,expert_id,name,domains\n"
        "sciencevision.experts_csv.v1,expert-c,Cara,chemistry\n"
    )
    task = imported_workflow["task"]("task-5", "chemistry")
    tasks_path = imported_workflow["root"] / "chemistry.jsonl"
    write_jsonl(tasks_path, [task])
    receipt = imported_workflow["write_admission_receipt"](
        imported_workflow["root"] / "chemistry_receipt.json", tasks_path, [task]
    )
    store.import_batch(
        batch_id="batch-r2",
        tasks_jsonl=tasks_path,
        asset_root=imported_workflow["root"],
        admission_receipt=receipt,
    )
    store.auto_assign(batch_id="batch-r2")
    return store


def test_the_state_carries_every_field_the_filters_offer(imported_workflow: dict) -> None:
    store = assignments_across_two_batches(imported_workflow)
    rows = store.overview()["assignments"]
    assert len(rows) == 5
    for row in rows:
        assert {"batch_id", "expert_id", "status", "domain"} <= set(row)
    assert {row["expert_id"] for row in rows} == {"expert-a", "expert-b", "expert-c"}
    assert {row["status"] for row in rows} == {"assigned", "pending_review"}
    assert {row["domain"] for row in rows} == {"biology", "chemistry"}
    assert {row["batch_id"] for row in rows} == {"batch-r1", "batch-r2"}


@pytest.fixture
def console(imported_workflow: dict):
    """The offline console, loaded on the distribution view."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    store = assignments_across_two_batches(imported_workflow)
    with served_console(store) as url, sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="load")
        page.locator('[data-tab="distribution"]').click()
        page.wait_for_selector(ROWS)
        try:
            yield page
        finally:
            browser.close()


def test_the_admin_can_narrow_assignments_by_expert_status_batch_and_domain(console) -> None:
    page = console
    assert page.locator(ROWS).count() == 5
    assert page.locator("#assignment-table-count").inner_text() == "5"

    page.select_option("#assignment-filter-expert", "expert-b")
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 2")
    assert page.locator("#assignment-table-count").inner_text() == "2"
    assert "expert-b" in page.locator("#assignments-table").inner_text()
    assert "expert-a" not in page.locator("#assignments-table").inner_text()
    # The header count follows the table, so the operator is told the slice is
    # narrower than the state behind it.
    assert page.locator("#assignment-sample-status").get_attribute("data-mode") == "filter"

    # Filters compose: one expert's remaining work, not everyone's.
    page.select_option("#assignment-filter-expert", "expert-a")
    page.select_option("#assignment-filter-status", "assigned")
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 1")
    assert "task-3" in page.locator("#assignments-table").inner_text()

    page.select_option("#assignment-filter-status", "")
    page.select_option("#assignment-filter-expert", "")
    page.select_option("#assignment-filter-domain", "chemistry")
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 1")
    assert "task-5" in page.locator("#assignments-table").inner_text()

    page.select_option("#assignment-filter-domain", "")
    page.select_option("#assignment-filter-batch", "batch-r1")
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 4")


def test_a_spot_check_sample_narrows_the_view_and_a_new_filter_drops_it(console) -> None:
    page = console
    page.fill("#assignment-sample-size", "3")
    page.locator("#assignment-sample").click()
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 3")
    status = page.locator("#assignment-sample-status")
    assert status.get_attribute("data-mode") == "sample"
    # The sample says what it is: a view over three of the five, drawn now.
    assert "3" in status.inner_text() and "5" in status.inner_text()

    # Narrowing the slice underneath a sample would leave rows drawn from a
    # different population, so the sample goes.
    page.select_option("#assignment-filter-domain", "chemistry")
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 1")
    assert status.get_attribute("data-mode") == "filter"

    page.select_option("#assignment-filter-domain", "")
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 5")
    page.locator("#assignment-sample").click()
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 3")
    page.locator("#assignment-sample-clear").click()
    page.wait_for_function("document.querySelectorAll('#assignments-table tr').length === 5")
    assert status.get_attribute("data-mode") is None
