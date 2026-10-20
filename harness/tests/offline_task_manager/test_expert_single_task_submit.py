"""An expert can submit one completed task without finishing the rest.

The workspace used to refuse export until every assigned task had validation
feedback, an answer, and reasoning. Reviewers work one question at a time, so
a complete current task must produce a one-result envelope.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.offline_task_manager.test_expert_step_recorder import (
    open_workspace,
    served_package,
)

EXPERT_APP = Path("tools/offline_task_manager/expert_app/expert-app.js")


def _function_source(name: str) -> str:
    text = EXPERT_APP.read_text(encoding="utf-8")
    match = re.search(rf"(async )?function {name}\(", text)
    assert match, f"{name} not found"
    start = match.start()
    depth = 0
    for index, char in enumerate(text[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"{name} was not closed")


def test_export_results_does_not_refuse_when_sibling_tasks_are_incomplete() -> None:
    """A production change that restored the all-task incomplete gate would fail this."""

    source = _function_source("exportResults")
    assert "state.tasks.filter(row => !isComplete(row))" not in source
    assert "state.tasks.map(row =>" not in source
    assert "isComplete(row)" in source or "isComplete(selected)" in source



def _complete_current_task(page) -> None:
    page.wait_for_selector("#response-form:not([hidden])")
    page.fill("#validation-feedback", "The task is clear.")
    page.fill("#answer", "Answer text")
    page.fill("#reasoning", "Reasoning text")


def test_exporting_one_complete_task_does_not_require_the_others(
    imported_workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with served_package(imported_workflow, "single-submit-package") as url:
        with sync_playwright() as playwright:
            browser, page = open_workspace(playwright, url)
            assert page.locator(".task-item").count() >= 2
            first = page.evaluate("decodeURIComponent(location.hash.slice(1))")
            _complete_current_task(page)

            with page.expect_download(timeout=5000) as download:
                page.click("#export-results")
            payload = json.loads(
                Path(download.value.path()).read_text(encoding="utf-8")
            )
            banner = page.locator("#error-banner")
            assert banner.get_attribute("hidden") is not None or banner.inner_text() == ""
            browser.close()

    assert [row["assignment_id"] for row in payload["results"]] == [first]
    assert payload["results"][0]["answer"] == "Answer text"
    assert payload["results"][0]["reasoning"] == "Reasoning text"
    report = imported_workflow["store"].import_submission(payload)
    assert report["counts"]["rejected"] == 0
    assert report["counts"]["accepted"] == 1
