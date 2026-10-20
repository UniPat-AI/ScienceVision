from __future__ import annotations

from copy import deepcopy
import json
import multiprocessing
from pathlib import Path

import pytest

from qagen.config import build_config, load_compiled_config
from qagen.cli import main
from qagen.contracts import make_envelope
from qagen.errors import InvalidStateError, StaleInputError
from qagen.paths import WorkspacePaths
from qagen.plan import approve_plan, create_plan
from qagen.review import ReviewError, decide_review
from qagen.run import RunError, execute_run, load_run, run_directory, start_run
from qagen.storage.atomic import atomic_write_json
from qagen.viewer import build_viewer_profile

from conftest import PACKAGE_ROOT, profile_path, write_controlled_viewer_evidence


def _execute_revision_in_child(workspace: str, run_id: str, receipt_path: str, queue: multiprocessing.Queue) -> None:
    try:
        execute_run(
            WorkspacePaths.from_value(workspace),
            run_id=run_id,
            revision_receipt_path=receipt_path,
        )
    except Exception as exc:
        queue.put(type(exc).__name__)
    else:
        queue.put("ok")


def _replace_persisted_verification(paths: WorkspacePaths, run_id: str, leaf_id: str, verification: dict) -> None:
    state = load_run(paths, run_id)
    body = deepcopy(state["body"])
    entry = deepcopy(body["executions"][leaf_id])
    verification_digest = atomic_write_json(Path(entry["verification_path"]), verification, private=True)
    stages = deepcopy(entry["stage_digests"])
    stages["verifier"]["output_contract_digest"] = verification_digest
    worker_result_path = Path(entry["worker_result_path"])
    worker_result = json.loads(worker_result_path.read_text(encoding="utf-8"))
    worker_result["stages"] = stages
    worker_result_digest = atomic_write_json(worker_result_path, worker_result, private=True)
    manifest_path = Path(entry["execution_manifest_path"])
    old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_body = deepcopy(old_manifest["body"])
    manifest_body["stages"] = stages
    manifest_body["verification"] = {"path": entry["verification_path"], "digest": verification_digest}
    manifest_body["worker_result"] = {"path": entry["worker_result_path"], "digest": worker_result_digest}
    manifest = make_envelope(
        kind="run_execution_manifest",
        identifier=old_manifest["id"],
        status=old_manifest["status"],
        body=manifest_body,
        producer=old_manifest["producer"],
        created_at=old_manifest["created_at"],
    )
    atomic_write_json(manifest_path, manifest, private=True)
    entry.update(
        {
            "verification_digest": verification_digest,
            "worker_result_digest": worker_result_digest,
            "execution_manifest_digest": manifest["content_digest"],
            "stage_digests": stages,
        }
    )
    body["executions"][leaf_id] = entry
    updated_state = make_envelope(
        kind="run_state",
        identifier=state["id"],
        status=state["status"],
        body=body,
        producer=state["producer"],
        created_at=state["created_at"],
    )
    atomic_write_json(run_directory(paths, run_id) / "state.json", updated_state, private=True)


def _started_fixture_run(tmp_path: Path) -> dict[str, object]:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    compiled_result = build_config(paths, profile_path=profile_path("bio_chem_fixture_compile"))
    compiled = load_compiled_config(paths, compiled_result["config_path"])
    definition_digest = compiled["body"]["dependency_locks"][0]["viewer_definition_digest"]
    evidence_path = tmp_path / "viewer-session-evidence.json"
    write_controlled_viewer_evidence(evidence_path, definition_digest=definition_digest)
    viewer_result = build_viewer_profile(
        paths,
        definition_path=PACKAGE_ROOT / "configs" / "sources" / "viewer_definitions.yaml",
        session_evidence_path=evidence_path,
    )
    plan_result = create_plan(
        paths,
        config_path=compiled_result["config_path"],
        viewer_profile_path=viewer_result["viewer_profile_path"],
    )
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )
    started = start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])
    return {
        "paths": paths,
        "run_id": started["run_id"],
        "viewer_evidence_path": evidence_path,
        "definition_digest": definition_digest,
    }


def test_execute_runs_only_locked_fixture_work_items_and_persists_review_inputs(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]

    result = execute_run(paths, run_id=run_id)

    assert result["status"] == "running"
    assert result["execution_status"] == "fixture_execution_complete_pending_human_review"
    assert len(result["executed"]) == 2
    run = load_run(paths, run_id)
    assert set(run["body"]["executions"]) == {entry["leaf_id"] for entry in result["executed"]}

    first = result["executed"][0]
    candidate = json.loads(Path(first["candidate_path"]).read_text(encoding="utf-8"))
    evidence = json.loads(Path(first["evidence_path"]).read_text(encoding="utf-8"))
    verification = json.loads(Path(first["verification_path"]).read_text(encoding="utf-8"))
    worker_result = json.loads(Path(first["worker_result_path"]).read_text(encoding="utf-8"))

    assert candidate["artifact_kind"] == "author_private_candidate"
    assert evidence["candidate_digest"] == first["candidate_digest"]
    assert evidence["viewer_profile_digest"] == run["body"]["viewer_profile_digest"]
    assert evidence["observations"]
    assert verification["candidate_digest"] == first["candidate_digest"]
    assert verification["evidence_digest"] == first["evidence_digest"]
    assert verification["outcome"] == "pass"
    assert all(check["outcome"] == "pass" for check in verification["checks"])
    assert set(verification["rubric_scores"]) == {"authenticity", "correctness", "three_d_relevance"}
    assert set(worker_result["stages"]) == {
        "brief_builder",
        "author",
        "truth_builder",
        "evidence_worker",
        "verifier",
    }
    assert all(stage["fixture_only"] is True for stage in worker_result["stages"].values())

    review = decide_review(
        paths,
        run_id=run_id,
        candidate_path=first["candidate_path"],
        evidence_path=first["evidence_path"],
        reviewer="domain-expert",
        decision="approved",
    )
    assert review["candidate_digest"] == first["candidate_digest"]

    with pytest.raises(InvalidStateError, match="no unexecuted"):
        execute_run(paths, run_id=run_id)


def test_execute_revalidates_live_locks_and_blocks_a_stale_run(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]
    write_controlled_viewer_evidence(
        setup["viewer_evidence_path"],
        definition_digest=setup["definition_digest"],
        allowed_operations=["inspect", "isolate", "rotate", "zoom"],
    )

    with pytest.raises(StaleInputError, match="run locks are stale"):
        execute_run(paths, run_id=run_id)

    assert load_run(paths, run_id)["status"] == "blocked_stale_locks"


def test_review_rejects_a_digest_consistent_failed_verifier_result(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]
    base = execute_run(paths, run_id=run_id)["executed"][0]
    verification_path = Path(base["verification_path"])
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    verification["outcome"] = "fail"
    verification["checks"][0]["outcome"] = "fail"
    _replace_persisted_verification(paths, run_id, base["leaf_id"], verification)

    with pytest.raises(ReviewError, match="outcome must be pass"):
        decide_review(
            paths,
            run_id=run_id,
            candidate_path=base["candidate_path"],
            evidence_path=base["evidence_path"],
            reviewer="domain-expert",
            decision="approved",
        )


def test_run_execute_cli_runs_a_persisted_fixture_run(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]

    exit_code = main(["run", "execute", "--workspace", str(paths.root), "--run-id", run_id])

    assert exit_code == 0
    assert len(load_run(paths, run_id)["body"]["executions"]) == 2


def test_revision_receipt_executes_only_one_persisted_rewrite(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]
    initial = execute_run(paths, run_id=run_id)
    base = initial["executed"][0]
    review = decide_review(
        paths,
        run_id=run_id,
        candidate_path=base["candidate_path"],
        evidence_path=base["evidence_path"],
        reviewer="domain-expert",
        decision="revise",
        rationale="Make the visible target more specific.",
    )
    receipt = json.loads(Path(review["revision_receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["body"]["run_id"] == run_id
    assert receipt["body"]["work_item_id"] == base["leaf_id"]
    assert receipt["body"]["candidate_digest"] == base["candidate_digest"]
    assert receipt["body"]["verifier_output_digest"] == base["verification_digest"]
    assert receipt["body"]["reasons"] == ["Make the visible target more specific."]
    with pytest.raises(InvalidStateError, match="already has a persisted execution"):
        execute_run(paths, run_id=run_id, work_item_id=base["leaf_id"])

    revised = execute_run(
        paths,
        run_id=run_id,
        revision_receipt_path=review["revision_receipt_path"],
    )

    assert revised["execution_status"] == "revision_generated_pending_verification_and_human_review"
    revision = revised["revision"]
    assert revision["revision_count"] == 1
    assert revision["candidate_digest"] != base["candidate_digest"]
    assert Path(revision["candidate_path"]).is_file()
    assert Path(revision["worker_result_path"]).is_file()
    assert Path(revision["revision_manifest_path"]).is_file()
    persisted_base = load_run(paths, run_id)["body"]["executions"][base["leaf_id"]]
    assert persisted_base["candidate_digest"] == base["candidate_digest"]
    assert persisted_base["execution_manifest_digest"] == base["execution_manifest_digest"]
    assert persisted_base["revision"]["candidate_digest"] == revision["candidate_digest"]

    with pytest.raises(InvalidStateError, match="one allowed revision"):
        execute_run(
            paths,
            run_id=run_id,
            revision_receipt_path=review["revision_receipt_path"],
        )


def test_run_execute_cli_accepts_persisted_revision_receipt(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]
    base = execute_run(paths, run_id=run_id)["executed"][0]
    review = decide_review(
        paths,
        run_id=run_id,
        candidate_path=base["candidate_path"],
        evidence_path=base["evidence_path"],
        reviewer="domain-expert",
        decision="revise",
        rationale="Clarify the requested inspection.",
    )

    exit_code = main(
        [
            "run",
            "execute",
            "--workspace",
            str(paths.root),
            "--run-id",
            run_id,
            "--revision-receipt",
            review["revision_receipt_path"],
        ]
    )

    assert exit_code == 0
    entry = load_run(paths, run_id)["body"]["executions"][base["leaf_id"]]
    assert entry["revision"]["revision_receipt_digest"] == review["revision_receipt_digest"]


def test_run_execute_rejects_a_tampered_persisted_revision_receipt(tmp_path: Path) -> None:
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]
    base = execute_run(paths, run_id=run_id)["executed"][0]
    review = decide_review(
        paths,
        run_id=run_id,
        candidate_path=base["candidate_path"],
        evidence_path=base["evidence_path"],
        reviewer="domain-expert",
        decision="revise",
        rationale="Clarify the requested inspection.",
    )
    receipt_path = Path(review["revision_receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["body"]["reasons"] = ["changed after review"]
    atomic_write_json(receipt_path, receipt, private=True)

    with pytest.raises(RunError, match="revision receipt"):
        execute_run(paths, run_id=run_id, revision_receipt_path=receipt_path)
    assert "revision" not in load_run(paths, run_id)["body"]["executions"][base["leaf_id"]]


def test_os_lock_allows_only_one_cross_process_revision(tmp_path: Path) -> None:
    try:
        context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - supported local CI platforms provide fork.
        pytest.skip("fork process context is unavailable")
    setup = _started_fixture_run(tmp_path)
    paths = setup["paths"]
    run_id = setup["run_id"]
    base = execute_run(paths, run_id=run_id)["executed"][0]
    review = decide_review(
        paths,
        run_id=run_id,
        candidate_path=base["candidate_path"],
        evidence_path=base["evidence_path"],
        reviewer="domain-expert",
        decision="revise",
        rationale="Apply exactly one rewrite.",
    )
    queue: multiprocessing.Queue = context.Queue()
    workers = [
        context.Process(
            target=_execute_revision_in_child,
            args=(str(paths.root), run_id, review["revision_receipt_path"], queue),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    outcomes = sorted(str(queue.get(timeout=2)) for _ in workers)

    assert outcomes == ["InvalidStateError", "ok"]
    entry = load_run(paths, run_id)["body"]["executions"][base["leaf_id"]]
    assert entry["revision"]["revision_count"] == 1
    revision_manifests = list((run_directory(paths, run_id) / "private" / "audit" / "revisions").glob("*.json"))
    assert len(revision_manifests) == 1
