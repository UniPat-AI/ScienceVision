"""What an expert's trajectory of semantically key operations may contain.

Reviewers asked for the reasoning behind the operations that actually moved a
judgement -- "the first zoom was not enough, so I zoomed in again" -- not a log
of every mouse move. So a step without a reason is not a step, and the store
refuses it. The legacy "no collector exists yet" record stays valid: packages
built before the collector shipped must keep importing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.offline_task_manager.contract import object_sha256, submission_payload_sha256
from tools.offline_task_manager.results_export import validate_export_rows

from tests.offline_task_manager.test_results import make_submission, prepared

LEGACY_TRAJECTORY = {
    "schema_version": "sciencevision.trajectory_ref.v1",
    "status": "not_collected",
    "reason": "collector_not_implemented",
}


def step(index: int, **overrides: object) -> dict:
    value = {
        "index": index,
        "at": f"2026-08-19T00:00:{index:02d}Z",
        "action": "zoom_in",
        "note": "The ligand was still unreadable, so I zoomed in once more.",
    }
    value.update(overrides)
    return value


def collected(*steps: dict) -> dict:
    return {
        "schema_version": "sciencevision.trajectory.v1",
        "status": "collected" if steps else "empty",
        "steps": list(steps),
    }


def submitter(imported_workflow: dict):
    """Bind one package per test.

    ``prepared`` writes a package directory and refuses to overwrite it, so a
    test that submits several trajectories has to reuse one assignment.
    """
    manifest, assignment = prepared(imported_workflow)
    store = imported_workflow["store"]

    def submit(trajectory: dict) -> dict:
        return store.import_submission(
            make_submission(manifest, assignment, trajectory=trajectory)
        )

    return submit


def rejection(report: dict) -> str:
    assert report["submission_status"] == "not_imported"
    assert report["counts"]["accepted"] == 0
    return report["rejected"][0]["reason"]


def test_a_collected_trajectory_records_the_key_operations(
    imported_workflow: dict,
) -> None:
    submit = submitter(imported_workflow)
    report = submit(
        collected(step(1), step(2, action="rotate", note="Ligand hid the pocket."))
    )
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    row = imported_workflow["store"].result_rows()[0]
    assert row["trajectory_status"] == "collected"
    assert row["trajectory_step_count"] == 2
    validate_export_rows([row])


def test_an_expert_who_needed_no_key_operation_says_so(
    imported_workflow: dict,
) -> None:
    submit = submitter(imported_workflow)
    report = submit(collected())
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    row = imported_workflow["store"].result_rows()[0]
    assert row["trajectory_status"] == "empty"
    assert row["trajectory_step_count"] == 0


def test_the_legacy_not_collected_record_still_imports(
    imported_workflow: dict,
) -> None:
    submit = submitter(imported_workflow)
    report = submit(dict(LEGACY_TRAJECTORY))
    assert report["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    row = imported_workflow["store"].result_rows()[0]
    assert row["trajectory_status"] == "not_collected"
    assert row["trajectory_step_count"] == 0


def test_the_status_must_agree_with_the_steps(imported_workflow: dict) -> None:
    submit = submitter(imported_workflow)
    assert "status" in rejection(submit(collected(step(1)) | {"status": "empty"}))
    assert "status" in rejection(submit(collected() | {"status": "collected"}))


def test_every_step_must_explain_why_it_mattered(imported_workflow: dict) -> None:
    submit = submitter(imported_workflow)
    without_note = step(1)
    without_note.pop("note")
    assert "note" in rejection(submit(collected(without_note)))
    assert "note" in rejection(submit(collected(step(1, note="  "))))
    assert "note" in rejection(submit(collected(step(1, note="x" * 501))))


def test_steps_are_numbered_in_order_from_one(imported_workflow: dict) -> None:
    submit = submitter(imported_workflow)
    assert "index" in rejection(submit(collected(step(1), step(3))))
    assert "index" in rejection(submit(collected(step(2))))


def test_a_step_names_one_action_at_one_time(imported_workflow: dict) -> None:
    submit = submitter(imported_workflow)
    assert "action" in rejection(submit(collected(step(1, action=""))))
    assert "action" in rejection(submit(collected(step(1, action="a" * 65))))
    assert "at" in rejection(submit(collected(step(1, at=""))))


def test_a_trajectory_cannot_grow_without_bound(imported_workflow: dict) -> None:
    submit = submitter(imported_workflow)
    too_many = collected(*[step(index + 1) for index in range(201)])
    assert "200" in rejection(submit(too_many))


def test_an_unknown_trajectory_shape_is_still_refused(
    imported_workflow: dict,
) -> None:
    submit = submitter(imported_workflow)
    assert "trajectory" in rejection(submit({"status": "collected"}))
    assert "trajectory" in rejection(submit(collected(step(1)) | {"reason": "extra"}))


def test_the_v2_bundle_path_applies_the_same_trajectory_contract(
    imported_workflow: dict,
) -> None:
    store = imported_workflow["store"]
    store.auto_assign(batch_id="batch-r1")
    store.publish_batch(batch_id="batch-r1")
    context = store.active_bundle_context("expert-a")
    task_row = json.loads(context["tasks_jsonl"].splitlines()[0])

    def bundle_submission(trajectory: dict) -> dict:
        result = {
            "schema_version": "sciencevision.expert_bundle_result.v2",
            "bundle_id": context["bundle_id"],
            "batch_id": task_row["batch_id"],
            "expert_id": task_row["expert_id"],
            "assignment_id": task_row["assignment_id"],
            "task_id": task_row["task_id"],
            "task_revision_sha256": task_row["task_revision_sha256"],
            "attempt": 1,
            "task_validation": {"status": "valid", "feedback": "Looks correct."},
            "answer": "Reviewed answer",
            "reasoning": "Reviewed against the model.",
            "trajectory": trajectory,
            "evidence": [],
            "saved_at": "2026-08-15T00:00:00Z",
        }
        body = {
            "schema_version": "sciencevision.expert_bundle_submission.v2",
            "contract_version": "sciencevision.expert_exchange.v2",
            "submission_id": "sub_" + object_sha256(result)[:24],
            "bundle_id": context["bundle_id"],
            "bundle_manifest_sha256": context["manifest"]["bundle_manifest_sha256"],
            "batch_id": result["batch_id"],
            "expert_id": result["expert_id"],
            "results": [result],
        }
        return body | {"payload_sha256": submission_payload_sha256(body)}

    bad = store.import_submission(bundle_submission(collected(step(1, note=""))))
    assert "note" in rejection(bad)
    good = store.import_submission(bundle_submission(collected(step(1), step(2))))
    assert good["counts"] == {"accepted": 1, "duplicates": 0, "rejected": 0}
    row = store.result_rows()[0]
    assert row["trajectory_status"] == "collected"
    assert row["trajectory_step_count"] == 2


def test_the_submission_schema_documents_both_trajectory_forms() -> None:
    import tools.offline_task_manager as package

    schema_path = Path(package.__file__).parent / "schemas" / "submission.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    branches = schema["$defs"]["result"]["properties"]["trajectory"]["oneOf"]
    assert branches[0]["const"] == LEGACY_TRAJECTORY
    collected_branch = branches[1]
    assert collected_branch["properties"]["schema_version"]["const"] == (
        "sciencevision.trajectory.v1"
    )
    assert collected_branch["properties"]["status"]["enum"] == ["collected", "empty"]
    steps = collected_branch["properties"]["steps"]
    assert steps["maxItems"] == 200
    assert steps["items"]["required"] == ["index", "at", "action", "note"]
    assert steps["items"]["properties"]["note"]["maxLength"] == 500


# The browser-MCP drawer records the same kind of trajectory from the other
# side: the viewer executes the action and the drawer captures a screenshot. The
# backend deliberately accepts a step with no reason there, because the client
# already deployed to the platform sends them, so the obligation is enforced by
# the client this branch ships.
TRAJECTORY_JS = Path("tools/offline_task_manager/expert_app/mcp/task-trajectory.js")


def test_the_drawer_names_the_first_saved_step_that_owes_a_reason() -> None:
    """Saving an MCP step is deliberate, so every saved step owes a reason.

    The predicate is exported rather than buried in the submit handler so this
    can exercise the rule itself instead of asserting on source tokens.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from tests.viewer.test_screenshot_contract import _launch_browser

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright)
        page = browser.new_page()
        page.add_script_tag(path=str(TRAJECTORY_JS.resolve()))
        first_gap = page.evaluate(
            "draft => window.ScienceVisionTaskTrajectory.unexplainedStep(draft)?.step ?? null",
            {
                "trajectory": [
                    {"step": 1, "description": "The ligand was unreadable at this zoom."},
                    {"step": 2, "description": "   "},
                    {"step": 3, "description": ""},
                ]
            },
        )
        assert first_gap == 2
        assert (
            page.evaluate(
                "draft => window.ScienceVisionTaskTrajectory.unexplainedStep(draft)",
                {"trajectory": [{"step": 1, "description": "One zoom was not enough."}]},
            )
            is None
        )
        # An empty drawer has nothing to explain, and must not read as a gap.
        for empty in ({"trajectory": []}, {}, None):
            assert (
                page.evaluate(
                    "draft => window.ScienceVisionTaskTrajectory.unexplainedStep(draft)", empty
                )
                is None
            )
        browser.close()


def test_the_drawer_refuses_to_submit_before_the_transport_is_reached() -> None:
    """The gate has to precede the submission, not report after the fact."""
    source = TRAJECTORY_JS.read_text(encoding="utf-8")
    gate = source.index("const unexplained = unexplainedStep(state.draft);")
    assert gate < source.index("await submitDraft(draft, submitTransport", gate)
    assert "'trajectory.status.needNote'" in source
