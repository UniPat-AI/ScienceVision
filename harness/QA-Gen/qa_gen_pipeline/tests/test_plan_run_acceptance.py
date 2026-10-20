from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest
import yaml

from qagen.config import build_config, load_compiled_config
from qagen.contracts import digest, make_decision
from qagen.errors import GatePendingError, InvalidStateError, StaleInputError
from qagen.paths import WorkspacePaths
from qagen.plan import approve_plan, create_plan, load_plan, load_plan_approval
from qagen.plan import registry as plan_registry
from qagen.run import execute_run, load_run, resume_run, start_run
from qagen.viewer import build_viewer_profile

from conftest import PACKAGE_ROOT, profile_path, write_controlled_viewer_evidence, write_json


def _planned_fixture_run(tmp_path: Path) -> dict[str, object]:
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
    return {
        "paths": paths,
        "compiled": compiled,
        "evidence_path": evidence_path,
        "viewer": viewer_result,
        "plan_result": plan_result,
        "plan": load_plan(paths, plan_result["plan_path"]),
    }


def test_approval_receipt_binds_plan_and_every_selected_dependency_lock(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    plan = setup["plan"]

    approval_result = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )
    approval = load_plan_approval(paths, approval_result["approval_path"])

    assert digest(plan["body"]["dependency_locks"]) == plan["body"]["lock_digests"]["dependency_lock_map_digest"]
    assert approval["body"]["bound_digests"] == {
        "plan_digest": plan["content_digest"],
        **plan["body"]["lock_digests"],
    }


def test_plan_carries_the_compiled_model_routing_digest(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    compiled = setup["compiled"]
    plan = setup["plan"]
    routing = yaml.safe_load((PACKAGE_ROOT / "configs" / "agents" / "model_routing.yaml").read_text(encoding="utf-8"))
    expected_digest = digest(routing)

    assert compiled["body"]["lock_digests"]["model_routing_digest"] == expected_digest
    assert plan["body"]["lock_digests"]["model_routing_digest"] == expected_digest


def test_start_run_rejects_a_nonapproved_plan_receipt(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="revise",
        rationale="Needs a scoped correction.",
    )

    with pytest.raises(GatePendingError, match="not approved"):
        start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])


def test_approved_plan_starts_a_bound_running_run(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    plan = setup["plan"]
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )

    result = start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])
    state = load_run(paths, result["run_id"])

    assert result["status"] == "running"
    assert state["body"]["plan_digest"] == plan["content_digest"]
    assert state["body"]["lock_digests"] == plan["body"]["lock_digests"]


def test_concurrent_conflicting_plan_decisions_commit_exactly_one_receipt(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    barrier = threading.Barrier(3)

    def decide(value: str) -> dict[str, object] | Exception:
        barrier.wait()
        try:
            return approve_plan(
                paths,
                plan_path=plan_result["plan_path"],
                reviewer=f"plan-owner-{value}",
                decision=value,
                rationale="Concurrent terminal decision test.",
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, value) for value in ("approved", "rejected")]
        barrier.wait()
        results = [future.result() for future in futures]

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], InvalidStateError)
    receipts = list(paths.plan_receipts.glob("*.decision.json"))
    assert receipts == [Path(successes[0]["approval_path"])]


def test_start_run_rejects_noncanonical_legacy_approval(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    plan = setup["plan"]
    approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="rejected",
        rationale="The canonical terminal decision rejects this plan.",
    )
    locks = plan["body"]["lock_digests"]
    stale = make_decision(
        kind="plan_approval_decision",
        identifier="legacy-plan-decision",
        decision="approved",
        actor_id="legacy-plan-owner",
        actor_role="plan_owner",
        bound_digests={"plan_digest": plan["content_digest"], **locks},
    )
    stale_path = paths.plan_receipts / "legacy-approved.decision.json"
    write_json(stale_path, stale)

    with pytest.raises(InvalidStateError, match="canonical approval decision"):
        start_run(paths, plan_path=plan_result["plan_path"], approval_path=stale_path)


def test_failed_plan_decision_commit_removes_its_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]

    def fail_event(*args: object, **kwargs: object) -> None:
        raise RuntimeError("event commit failed")

    monkeypatch.setattr(plan_registry, "append_event", fail_event)
    with pytest.raises(RuntimeError, match="event commit failed"):
        approve_plan(
            paths,
            plan_path=setup["plan_result"]["plan_path"],
            reviewer="plan-owner",
            decision="approved",
        )

    assert list(paths.plan_receipts.glob("*.decision.json")) == []


def test_execute_run_persists_the_locked_worker_graph_for_each_approved_leaf(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )

    started = start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])
    executed = execute_run(paths, run_id=started["run_id"])
    run_path = Path(started["run_path"])
    worker_results = sorted((run_path / "private" / "worker-results").glob("*.json"))
    candidates = sorted((run_path / "private" / "candidates").glob("*.json"))

    assert len(worker_results) == 2
    assert len(candidates) == 2
    assert executed["execution_status"] == "fixture_execution_complete_pending_human_review"
    assert {item["leaf_id"] for item in executed["executed"]} == {
        "biology.docking_pose_plausibility",
        "chemistry.stereochemistry_chirality",
    }
    for path in worker_results:
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["run_id"] == started["run_id"]
        assert result["status"] == "verification_complete_pending_human_review"
        assert set(result["stages"]) == {
            "brief_builder",
            "author",
            "truth_builder",
            "evidence_worker",
            "verifier",
        }
        assert all(stage["fixture_only"] is True for stage in result["stages"].values())

    state = load_run(paths, started["run_id"])
    assert set(state["body"]["executions"]) == {
        "biology.docking_pose_plausibility",
        "chemistry.stereochemistry_chirality",
    }
    for path in candidates:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        assert candidate["artifact_kind"] == "author_private_candidate"
        assert candidate["viewer_session_request"]["viewer_profile_digest"] == state["body"]["viewer_profile_digest"]

    usage_records = [json.loads(line) for line in paths.usage.read_text(encoding="utf-8").splitlines()]
    assert len(usage_records) == 20
    assert {record["body"]["run_id"] for record in usage_records} == {started["run_id"]}
    assert {record["body"]["worker"] for record in usage_records} == {
        "brief_builder",
        "author",
        "truth_builder",
        "evidence_worker",
        "verifier",
    }
    assert not any(any((run_path / "staging" / area).iterdir()) for area in ("solver", "scorer", "audit"))


def test_execute_run_is_incremental_and_rejects_a_duplicate_work_item(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )
    started = start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])
    selected_leaf = "biology.docking_pose_plausibility"

    first = execute_run(paths, run_id=started["run_id"], work_item_id=selected_leaf)
    assert [item["leaf_id"] for item in first["executed"]] == [selected_leaf]
    assert set(load_run(paths, started["run_id"])["body"]["executions"]) == {selected_leaf}

    with pytest.raises(InvalidStateError, match="already has a persisted execution"):
        execute_run(paths, run_id=started["run_id"], work_item_id=selected_leaf)

    remaining = execute_run(paths, run_id=started["run_id"])
    assert [item["leaf_id"] for item in remaining["executed"]] == ["chemistry.stereochemistry_chirality"]
    assert set(load_run(paths, started["run_id"])["body"]["executions"]) == {
        "biology.docking_pose_plausibility",
        "chemistry.stereochemistry_chirality",
    }


def test_resume_blocks_the_run_when_controlled_viewer_evidence_drifts(tmp_path: Path) -> None:
    setup = _planned_fixture_run(tmp_path)
    paths = setup["paths"]
    plan_result = setup["plan_result"]
    compiled = setup["compiled"]
    evidence_path = setup["evidence_path"]
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )
    started = start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])

    resumed = resume_run(paths, run_id=started["run_id"])
    assert resumed["resume_count"] == 1

    definition_digest = compiled["body"]["dependency_locks"][0]["viewer_definition_digest"]
    write_controlled_viewer_evidence(
        evidence_path,
        definition_digest=definition_digest,
        allowed_operations=["inspect", "isolate", "rotate", "zoom"],
    )
    with pytest.raises(StaleInputError, match="run locks are stale"):
        resume_run(paths, run_id=started["run_id"])

    assert load_run(paths, started["run_id"])["status"] == "blocked_stale_locks"
