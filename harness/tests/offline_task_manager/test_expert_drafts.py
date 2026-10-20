"""Server-side drafts do not submit an assignment.

An expert can park validation, answer, and reasoning on one assignment and
reload them later. Saving a draft must not move ``assignments.status``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.offline_task_manager.contract import ContractError


def _first_assignment(store):
    store.auto_assign(batch_id="batch-r1")
    row = store.connection.execute(
        "SELECT assignment_id, expert_id, status FROM assignments ORDER BY task_id LIMIT 1"
    ).fetchone()
    assert row is not None
    return row


def test_save_draft_does_not_advance_assignment_status(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    row = _first_assignment(store)
    saved = store.save_draft(
        expert_id=row["expert_id"],
        assignment_id=row["assignment_id"],
        payload={
            "validation_status": "uncertain",
            "validation_feedback": "Need another look.",
            "answer": "Draft answer",
            "reasoning": "Draft reasoning",
            "attempt": 1,
            "steps": [],
        },
    )
    after = store.connection.execute(
        "SELECT status FROM assignments WHERE assignment_id=?",
        (row["assignment_id"],),
    ).fetchone()
    assert after["status"] == row["status"] == "assigned"
    drafts = store.load_drafts(expert_id=row["expert_id"])
    assert drafts[row["assignment_id"]]["answer"] == "Draft answer"
    assert drafts[row["assignment_id"]]["reasoning"] == "Draft reasoning"
    assert saved["saved_at"]


def test_save_draft_rejects_another_experts_assignment(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    row = _first_assignment(store)
    with pytest.raises(ContractError, match="not assigned"):
        store.save_draft(
            expert_id="expert-b",
            assignment_id=row["assignment_id"],
            payload={"answer": "stolen", "reasoning": "no", "validation_feedback": "x"},
        )


def test_save_draft_button_sits_between_focus_and_submit() -> None:
    html = Path("tools/offline_task_manager/expert_app/index.html").read_text(encoding="utf-8")
    focus = html.index('id="viewer-focus-toggle"')
    save = html.index('id="save-draft"')
    export = html.index('id="export-results"')
    assert focus < save < export
    assert 'data-i18n="expert.saveDraft"' in html
