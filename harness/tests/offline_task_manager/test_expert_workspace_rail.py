"""The task rail stays clickable while an expert is typing.

Every keystroke saves a draft, and the rail used to be rebuilt on each save.
Leaving a textarea also fires ``change``, so the rebuild landed between the
mousedown and the mouseup of the very click that caused it and the browser
never raised ``click`` -- the expert's first attempt to open another task did
nothing.
"""

from __future__ import annotations

import pytest

from tests.offline_task_manager.test_expert_step_recorder import (
    open_workspace,
    served_package,
)


def test_a_task_can_be_opened_with_one_click_after_typing(
    imported_workflow: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with served_package(imported_workflow, "rail-package") as url:
        with sync_playwright() as playwright:
            browser, page = open_workspace(playwright, url)
            assert page.locator(".task-item").count() >= 2
            opened = page.locator("#task-id").inner_text()

            page.fill("#answer", "Answer text")
            other = next(
                index
                for index in range(page.locator(".task-item").count())
                if opened not in page.locator(".task-item").nth(index).inner_text()
            )
            page.locator(".task-item").nth(other).click()
            page.wait_for_function(
                "expected => document.getElementById('task-id').textContent !== expected",
                arg=opened,
            )
            assert page.locator("#task-id").inner_text() != opened
            assert page.locator("#answer").input_value() == ""
            browser.close()
