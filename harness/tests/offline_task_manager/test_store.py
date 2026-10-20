from __future__ import annotations

import hashlib
import json

import pytest

from tools.offline_task_manager.contract import object_sha256
from tools.offline_task_manager.store import PreflightError


def write_jsonl(path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_domain_balancing_and_batch_import_is_non_destructive(imported_workflow: dict) -> None:
    store = imported_workflow["store"]
    first_batch = store.overview()["batches"][0]
    assert first_batch["admission_status"] == "passed"
    assert first_batch["admission_purpose"] == "expert_review_distribution"
    assert first_batch["admission_receipt_digest"]
    report = store.auto_assign(batch_id="batch-r1")
    assert report["assigned"] == 4
    assert sorted(report["counts"].values()) == [2, 2]

    second_path = imported_workflow["root"] / "second.jsonl"
    second_tasks = [imported_workflow["task"]("task-new")]
    write_jsonl(second_path, second_tasks)
    second_receipt = imported_workflow["write_admission_receipt"](
        imported_workflow["root"] / "second-admission.json", second_path, second_tasks
    )
    store.import_batch(
        batch_id="batch-r2",
        tasks_jsonl=second_path,
        asset_root=imported_workflow["root"],
        admission_receipt=second_receipt,
    )
    overview = store.overview()
    assert {row["batch_id"] for row in overview["batches"]} == {"batch-r1", "batch-r2"}
    assert len([row for row in overview["assignments"] if row["batch_id"] == "batch-r1"]) == 4


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("missing", "admission receipt is required"),
        ("rejected", "status is not passed"),
        ("purpose", "purpose is not expected 'expert_review_distribution'"),
        ("tasks_sha256", "tasks.sha256 does not match"),
        ("tasks_count", "tasks.count does not match"),
        ("revision_digest", "task revision digest does not match"),
        ("revision_count", "task revision count does not match"),
        ("self_digest", "receipt digest mismatch"),
    ],
)
def test_qc_admission_failures_are_preinsert_and_fail_closed(
    workflow: dict, failure: str, message: str
) -> None:
    receipt = None
    if failure != "missing":
        def mutate(value: dict) -> None:
            if failure == "rejected":
                value["status"] = "rejected"
            elif failure == "purpose":
                value["purpose"] = "physical_sample_package"
            elif failure == "tasks_sha256":
                value["tasks"]["sha256"] = "0" * 64
            elif failure == "tasks_count":
                value["tasks"]["count"] += 1
            elif failure == "revision_digest":
                value["task_revision_set"]["digest"] = "0" * 64
            elif failure == "revision_count":
                value["task_revision_set"]["count"] += 1

        receipt = workflow["write_admission_receipt"](
            workflow["root"] / f"admission-{failure}.json",
            workflow["tasks_path"],
            workflow["tasks"],
            mutate,
        )
        if failure == "self_digest":
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["receipt_digest"] = "0" * 64
            receipt.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(PreflightError, match=message):
        workflow["store"].import_batch(
            batch_id=f"batch-{failure}",
            tasks_jsonl=workflow["tasks_path"],
            asset_root=workflow["root"],
            admission_receipt=receipt,
        )

    overview = workflow["store"].overview()
    assert overview["batches"] == []
    assert overview["tasks"] == []
    assert overview["assignments"] == []
    assert overview["packages"] == []


def test_nested_physical_wrapper_without_direct_revision_is_rejected_preinsert(
    workflow: dict,
) -> None:
    wrapper = dict(workflow["tasks"][0])
    source_revision = wrapper.pop("source_revision_sha256")
    wrapper["subquestions"] = [
        {
            "source_task_id": wrapper["task_id"],
            "source_lineage": {"source_line_sha256": source_revision},
        }
    ]
    tasks_path = workflow["root"] / "physical-wrapper.jsonl"
    write_jsonl(tasks_path, [wrapper])

    receipt = json.loads(
        workflow["admission_receipt"].read_text(encoding="utf-8")
    )
    receipt["tasks"] = {
        "path": "tasks/physical-wrapper.jsonl",
        "count": 1,
        "sha256": hashlib.sha256(tasks_path.read_bytes()).hexdigest(),
    }
    receipt.pop("receipt_digest")
    receipt["receipt_digest"] = object_sha256(receipt)
    receipt_path = workflow["root"] / "physical-wrapper-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(PreflightError, match="source_revision_sha256 must be"):
        workflow["store"].import_batch(
            batch_id="batch-physical-wrapper",
            tasks_jsonl=tasks_path,
            asset_root=workflow["root"],
            admission_receipt=receipt_path,
        )

    overview = workflow["store"].overview()
    assert overview["batches"] == []
    assert overview["tasks"] == []


def test_batch_qc_admission_is_immutable(imported_workflow: dict) -> None:
    def mutate(receipt: dict) -> None:
        receipt["policy"]["path"] = "policy/reissued.json"

    changed_receipt = imported_workflow["write_admission_receipt"](
        imported_workflow["root"] / "reissued-admission.json",
        imported_workflow["tasks_path"],
        imported_workflow["tasks"],
        mutate,
    )
    with pytest.raises(PreflightError, match="immutable QC admission"):
        imported_workflow["store"].import_batch(
            batch_id="batch-r1",
            tasks_jsonl=imported_workflow["tasks_path"],
            asset_root=imported_workflow["root"],
            admission_receipt=changed_receipt,
        )


def test_changed_catalog_requires_new_batch_id(imported_workflow: dict) -> None:
    changed = [dict(row) for row in imported_workflow["tasks"]]
    changed[0] = dict(changed[0], title="revision drift")
    path = imported_workflow["root"] / "changed.jsonl"
    write_jsonl(path, changed)
    receipt = imported_workflow["write_admission_receipt"](
        imported_workflow["root"] / "changed-admission.json", path, changed
    )
    with pytest.raises(PreflightError, match="immutable"):
        imported_workflow["store"].import_batch(
            batch_id="batch-r1",
            tasks_jsonl=path,
            asset_root=imported_workflow["root"],
            admission_receipt=receipt,
        )
    assert len(imported_workflow["store"].overview()["tasks"]) == 4


def test_batch_import_rejects_nonstandard_json_constants_without_inserting(
    workflow: dict,
) -> None:
    path = workflow["root"] / "nonstandard-constants.jsonl"
    valid_row = json.dumps(workflow["task"]("task-valid"))
    path.write_text(
        valid_row
        + '\n{"task_id":"task-nan","value":NaN}'
        + '\n{"task_id":"task-infinity","value":Infinity}'
        + '\n{"task_id":"task-negative-infinity","value":-Infinity}\n',
        encoding="utf-8",
    )
    receipt = workflow["write_admission_receipt"](
        workflow["root"] / "nonstandard-admission.json",
        path,
        [workflow["task"]("task-valid")],
    )

    with pytest.raises(PreflightError) as caught:
        workflow["store"].import_batch(
            batch_id="batch-nonstandard-json",
            tasks_jsonl=path,
            asset_root=workflow["root"],
            admission_receipt=receipt,
        )

    assert caught.value.errors == [
        "line 2: non-standard JSON constant 'NaN' is not allowed",
        "line 3: non-standard JSON constant 'Infinity' is not allowed",
        "line 4: non-standard JSON constant '-Infinity' is not allowed",
    ]
    overview = workflow["store"].overview()
    assert overview["batches"] == []
    assert overview["tasks"] == []


def test_unknown_domain_and_ineligible_explicit_assignment_fail(workflow: dict) -> None:
    path = workflow["root"] / "unknown.jsonl"
    unknown_tasks = [workflow["task"]("task-x", "chemistry")]
    write_jsonl(path, unknown_tasks)
    receipt = workflow["write_admission_receipt"](
        workflow["root"] / "unknown-admission.json", path, unknown_tasks
    )
    with pytest.raises(PreflightError, match="no eligible active expert"):
        workflow["store"].import_batch(
            batch_id="batch-unknown",
            tasks_jsonl=path,
            asset_root=workflow["root"],
            admission_receipt=receipt,
        )

    workflow["store"].import_batch(
        batch_id="batch-r1",
        tasks_jsonl=workflow["tasks_path"],
        asset_root=workflow["root"],
        admission_receipt=workflow["admission_receipt"],
    )
    workflow["store"].import_experts_csv(
        "expert_id,name,domains\nexpert-med,Medical Expert,medical\n"
    )
    with pytest.raises(PreflightError, match="not eligible"):
        workflow["store"].assign_explicit(
            batch_id="batch-r1", task_id="task-1", expert_id="expert-med"
        )


def test_names_are_display_data_not_identity(workflow: dict) -> None:
    store = workflow["store"]
    store.import_experts_csv("expert_id,name,domains\nexpert-a,Renamed Alice,biology\n")
    experts = {row["expert_id"]: row for row in store.overview()["experts"]}
    assert experts["expert-a"]["name"] == "Renamed Alice"
    assert len(experts) == 2
    assert "<b>Alice</b>" not in json.dumps(experts)
    with pytest.raises(PreflightError, match="no data rows"):
        store.import_experts_csv("expert_id,name,domains\n")


def test_same_size_asset_tamper_fails_before_batch_writes(workflow: dict) -> None:
    original = workflow["asset"].read_bytes()
    workflow["asset"].write_bytes(b"x" * len(original))

    with pytest.raises(PreflightError, match="asset sha256 mismatch"):
        workflow["store"].import_batch(
            batch_id="batch-tampered-asset",
            tasks_jsonl=workflow["tasks_path"],
            asset_root=workflow["root"],
            admission_receipt=workflow["admission_receipt"],
        )

    overview = workflow["store"].overview()
    assert overview["batches"] == []
    assert overview["tasks"] == []
    assert overview["assignments"] == []
    assert overview["packages"] == []


def test_asset_hash_preflight_runs_once_per_unique_path(
    workflow: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.offline_task_manager import store as store_module

    calls = []
    calculate = store_module.file_sha256

    def tracking_sha256(path):
        calls.append(path)
        return calculate(path)

    monkeypatch.setattr(store_module, "file_sha256", tracking_sha256)
    workflow["store"].import_batch(
        batch_id="batch-deduplicated-hash",
        tasks_jsonl=workflow["tasks_path"],
        asset_root=workflow["root"],
        admission_receipt=workflow["admission_receipt"],
    )

    assert calls == [workflow["asset"]]


def test_conflicting_asset_hashes_across_tasks_fail_preinsert(workflow: dict) -> None:
    first = workflow["task"]("task-first")
    conflicting = workflow["task"]("task-conflicting")
    conflicting["asset"]["sha256"] = "0" * 64
    conflicting["asset_hashes"][0]["sha256"] = "0" * 64
    tasks = [first, conflicting]
    tasks_path = workflow["root"] / "conflicting-asset-hashes.jsonl"
    write_jsonl(tasks_path, tasks)
    receipt = workflow["write_admission_receipt"](
        workflow["root"] / "conflicting-asset-hashes-receipt.json",
        tasks_path,
        tasks,
    )

    with pytest.raises(PreflightError, match="conflicting hashes declared"):
        workflow["store"].import_batch(
            batch_id="batch-conflicting-asset-hashes",
            tasks_jsonl=tasks_path,
            asset_root=workflow["root"],
            admission_receipt=receipt,
        )

    overview = workflow["store"].overview()
    assert overview["batches"] == []
    assert overview["tasks"] == []
    assert overview["assignments"] == []
    assert overview["packages"] == []


def test_symlinked_assets_and_task_lists_are_rejected(workflow: dict) -> None:
    linked_asset = workflow["root"] / "assets" / "linked.cif"
    linked_asset.symlink_to(workflow["asset"])
    task = workflow["task"]("task-linked")
    task["asset"]["path"] = "/assets/linked.cif"
    task["asset"]["repo_path"] = "assets/linked.cif"
    task["asset_hashes"][0]["repo_path"] = "assets/linked.cif"
    path = workflow["root"] / "linked.jsonl"
    write_jsonl(path, [task])
    receipt = workflow["write_admission_receipt"](
        workflow["root"] / "linked-admission.json", path, [task]
    )
    with pytest.raises(PreflightError, match="linked"):
        workflow["store"].import_batch(
            batch_id="batch-linked",
            tasks_jsonl=path,
            asset_root=workflow["root"],
            admission_receipt=receipt,
        )

    linked_tasks = workflow["tmp"] / "tasks-link.jsonl"
    linked_tasks.symlink_to(workflow["tasks_path"])
    with pytest.raises(PreflightError, match="symlink"):
        workflow["store"].import_batch(
            batch_id="batch-list-link",
            tasks_jsonl=linked_tasks,
            asset_root=workflow["root"],
            admission_receipt=workflow["admission_receipt"],
        )


def test_asset_directory_symlink_is_rejected_even_when_it_stays_under_root(
    workflow: dict,
) -> None:
    linked_directory = workflow["root"] / "linked-assets"
    linked_directory.symlink_to(workflow["root"] / "assets", target_is_directory=True)
    task = workflow["task"]("task-linked-directory")
    task["asset"]["path"] = "/linked-assets/model.cif"
    task["asset"]["repo_path"] = "linked-assets/model.cif"
    task["asset_hashes"][0]["repo_path"] = "linked-assets/model.cif"
    path = workflow["root"] / "linked-directory.jsonl"
    write_jsonl(path, [task])
    receipt = workflow["write_admission_receipt"](
        workflow["root"] / "linked-directory-admission.json", path, [task]
    )
    with pytest.raises(PreflightError, match="linked"):
        workflow["store"].import_batch(
            batch_id="batch-linked-directory",
            tasks_jsonl=path,
            asset_root=workflow["root"],
            admission_receipt=receipt,
        )
