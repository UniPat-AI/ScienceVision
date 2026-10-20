from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import threading

import pytest

from qagen.config import build_config, load_compiled_config
from qagen.contracts import digest, make_envelope
from qagen.errors import GatePendingError, InvalidStateError
from qagen.paths import WorkspacePaths
from qagen.plan import approve_plan, create_plan, load_plan
from qagen.release import ReleaseStageError, SolverBundleLeakageError, assert_solver_bundle_safe, export_release, stage_release
import qagen.release.staging as release_staging
from qagen.review import ReviewError, decide_review, load_review_decision
from qagen.run import execute_run, load_run, run_directory, start_run
from qagen.storage.atomic import atomic_write_json
from qagen.viewer import build_viewer_profile

from conftest import PACKAGE_ROOT, profile_path, write_controlled_viewer_evidence, write_json


def _running_fixture_run(tmp_path: Path) -> dict[str, object]:
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
    plan = load_plan(paths, plan_result["plan_path"])
    approval = approve_plan(
        paths,
        plan_path=plan_result["plan_path"],
        reviewer="plan-owner",
        decision="approved",
    )
    started = start_run(paths, plan_path=plan_result["plan_path"], approval_path=approval["approval_path"])
    executed = execute_run(paths, run_id=started["run_id"])["executed"]
    return {
        "paths": paths,
        "plan": plan,
        "run_id": started["run_id"],
        "run": load_run(paths, started["run_id"]),
        "executed": executed,
    }


def _execution_artifacts(setup: dict[str, object], index: int = 0) -> tuple[Path, Path, Path, dict, dict]:
    execution = setup["executed"][index]
    candidate_path = Path(execution["candidate_path"])
    evidence_path = Path(execution["evidence_path"])
    verification_path = Path(execution["verification_path"])
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    return candidate_path, evidence_path, verification_path, candidate, evidence


def _staged_artifact_descriptor(staging_root: Path, path: Path) -> dict[str, object]:
    content = path.read_bytes()
    value = json.loads(content.decode("utf-8"))
    return {
        "relative_path": path.relative_to(staging_root).as_posix(),
        "canonical_sha256": digest(value),
        "file_sha256": f"sha256:{sha256(content).hexdigest()}",
        "size_bytes": len(content),
    }


def _candidate(run: dict, plan: dict, *, task_id: str, variation: str) -> dict:
    knowledge_digest = plan["body"]["eligible_leaves"][0]["locks"]["knowledge_digests"][0]
    return {
        "artifact_kind": "author_private_candidate",
        "task_id": task_id,
        "prompt": f"Use the controlled viewer to inspect the fixture ({variation}).",
        "anonymous_scene_ref": "scene-fixture-biology-001",
        "viewer_session_request": {
            "request_id": f"request-{variation}",
            "viewer_profile_digest": run["body"]["viewer_profile_digest"],
            "allowed_operations": ["inspect", "isolate", "rotate"],
        },
        "reference_answer": {"answer": "fixture answer"},
        "reasoning": "private reasoning",
        "rubric": {"criterion": "visible evidence"},
        "provenance": {
            "plan_digest": run["body"]["plan_digest"],
            "config_digest": run["body"]["config_digest"],
            "knowledge_snapshot_digest": knowledge_digest,
        },
    }


def _write_candidate_and_evidence(
    paths: WorkspacePaths,
    *,
    run_id: str,
    plan: dict,
    task_id: str = "fixture-task-1",
    variation: str = "one",
    evidence_candidate_digest: str | None = None,
) -> tuple[Path, Path, dict]:
    run = load_run(paths, run_id)
    candidate = _candidate(run, plan, task_id=task_id, variation=variation)
    directory = run_directory(paths, run_id)
    candidate_path = directory / "private" / "candidates" / f"candidate-{variation}.json"
    evidence_path = directory / "private" / "evidence" / f"evidence-{variation}.json"
    write_json(candidate_path, candidate)
    write_json(
        evidence_path,
        {
            "candidate_digest": evidence_candidate_digest or digest(candidate),
            "viewer_profile_digest": run["body"]["viewer_profile_digest"],
            "observations": [{"operation": "inspect"}],
        },
    )
    return candidate_path, evidence_path, candidate


def test_review_receipt_binds_exact_candidate_evidence_and_plan_locks(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    execution = setup["executed"][0]
    candidate_path, evidence_path, _, candidate, evidence = _execution_artifacts(setup)

    result = decide_review(
        paths,
        run_id=setup["run_id"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        reviewer="domain-expert",
        decision="approved",
    )
    receipt = load_review_decision(paths, result["review_path"])
    expected = {
        "candidate_digest": digest(candidate),
        "evidence_digest": digest(evidence),
        "plan_digest": setup["run"]["body"]["plan_digest"],
        "viewer_profile_digest": setup["run"]["body"]["viewer_profile_digest"],
        "work_item_dependency_digest": execution["dependency_digest"],
        "execution_manifest_digest": execution["execution_manifest_digest"],
        "worker_result_digest": execution["worker_result_digest"],
        "author_output_digest": execution["candidate_digest"],
        "evidence_output_digest": execution["evidence_digest"],
        "verifier_output_digest": execution["verification_digest"],
        "stage_digests_digest": digest(execution["stage_digests"]),
        **setup["plan"]["body"]["lock_digests"],
    }

    assert receipt["body"]["bound_digests"] == expected
    assert load_run(paths, setup["run_id"])["body"]["candidate_reviews"][digest(candidate)]["decision"] == "approved"


def test_review_rejects_evidence_bound_to_a_different_candidate(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _, _, evidence = _execution_artifacts(setup)
    evidence["candidate_digest"] = digest({"other": "candidate"})
    write_json(evidence_path, evidence)

    with pytest.raises(ReviewError, match="exact candidate digest"):
        decide_review(
            paths,
            run_id=setup["run_id"],
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            reviewer="domain-expert",
            decision="approved",
        )


def test_review_rejects_legacy_fixture_evidence_without_observations(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _, _, evidence = _execution_artifacts(setup)
    evidence["observations"] = []
    write_json(evidence_path, evidence)

    with pytest.raises(ReviewError, match="legacy fixture evidence requires a non-empty observations list"):
        decide_review(
            paths,
            run_id=setup["run_id"],
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            reviewer="domain-expert",
            decision="approved",
        )


def test_candidate_review_decision_is_immutable(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _, _, _ = _execution_artifacts(setup)
    decide_review(
        paths,
        run_id=setup["run_id"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        reviewer="domain-expert",
        decision="revise",
        rationale="Clarify the observation.",
    )

    with pytest.raises(InvalidStateError, match="immutable review decision"):
        decide_review(
            paths,
            run_id=setup["run_id"],
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            reviewer="domain-expert",
            decision="revise",
            rationale="A second revision must not be accepted.",
        )


def test_approved_candidate_stages_only_solver_safe_public_artifacts(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    execution = setup["executed"][0]
    candidate_path, evidence_path, _, candidate, evidence = _execution_artifacts(setup)
    decide_review(
        paths,
        run_id=setup["run_id"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        reviewer="domain-expert",
        decision="approved",
    )

    staged = stage_release(paths, run_id=setup["run_id"], candidate_paths=[candidate_path])
    item = staged["staged"][0]
    solver = json.loads(Path(item["solver_path"]).read_text(encoding="utf-8"))
    scorer = json.loads(Path(item["scorer_path"]).read_text(encoding="utf-8"))
    audit = json.loads(Path(item["audit_path"]).read_text(encoding="utf-8"))

    assert staged["status"] == "fixture_staged_no_production_export"
    assert set(solver) == {"artifact_kind", "task_id", "prompt", "anonymous_scene_ref", "viewer_session_request"}
    assert {"reference_answer", "reasoning", "rubric", "provenance"}.isdisjoint(solver)
    assert scorer["reference_answer"] == candidate["reference_answer"]
    assert scorer["rubric"] == candidate["rubric"]
    assert scorer["execution_manifest_digest"] == execution["execution_manifest_digest"]
    assert scorer["verifier_output_digest"] == execution["verification_digest"]
    assert set(audit) == {
        "artifact_kind",
        "candidate_digest",
        "author_truth_receipt",
        "review_digest",
        "execution",
        "viewer_evidence",
        "rubric_dry_run",
        "lock_digests",
        "work_item_id",
        "work_item_dependency_digest",
    }
    assert audit["viewer_evidence"] == {
        "digest": digest(evidence),
        "trace_required": False,
        "session_request_id": candidate["viewer_session_request"]["request_id"],
    }
    assert audit["execution"]["execution_manifest_digest"] == execution["execution_manifest_digest"]
    assert audit["execution"]["worker_result_digest"] == execution["worker_result_digest"]
    assert audit["execution"]["verifier_output_digest"] == execution["verification_digest"]
    assert audit["rubric_dry_run"] is None
    audit_text = json.dumps(audit, sort_keys=True)
    assert "fixture answer" not in audit_text
    assert "private reasoning" not in audit_text
    assert_solver_bundle_safe(staged["solver_staging"])


def test_review_rejects_execution_evidence_with_an_unapproved_operation(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _, _, evidence = _execution_artifacts(setup)
    evidence["viewer_operations"] = ["network"]
    write_json(evidence_path, evidence)

    with pytest.raises(ReviewError, match="outside the approved plan"):
        decide_review(
            paths,
            run_id=setup["run_id"],
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            reviewer="domain-expert",
            decision="approved",
        )


def test_fixture_staging_can_never_be_explicitly_exported(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _, _, _ = _execution_artifacts(setup)
    decide_review(
        paths,
        run_id=setup["run_id"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        reviewer="domain-expert",
        decision="approved",
    )
    stage_release(paths, run_id=setup["run_id"], candidate_paths=[candidate_path])

    with pytest.raises(GatePendingError, match="fixture"):
        export_release(paths, run_id=setup["run_id"], output_dir=tmp_path / "release")


def test_hand_written_candidate_and_evidence_cannot_be_reviewed_or_staged(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _ = _write_candidate_and_evidence(
        paths,
        run_id=setup["run_id"],
        plan=setup["plan"],
    )

    with pytest.raises(ReviewError, match="persisted run execution"):
        decide_review(
            paths,
            run_id=setup["run_id"],
            candidate_path=candidate_path,
            evidence_path=evidence_path,
            reviewer="domain-expert",
            decision="approved",
        )
    with pytest.raises(ReleaseStageError, match="persisted run execution"):
        stage_release(paths, run_id=setup["run_id"], candidate_paths=[candidate_path])


@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_release_rejects_missing_or_tampered_verifier_output(tmp_path: Path, mutation: str) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, verification_path, _, _ = _execution_artifacts(setup)
    decide_review(
        paths,
        run_id=setup["run_id"],
        candidate_path=candidate_path,
        evidence_path=evidence_path,
        reviewer="domain-expert",
        decision="approved",
    )
    if mutation == "missing":
        verification_path.unlink()
    else:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        verification["verification"] = "tampered"
        write_json(verification_path, verification)

    with pytest.raises(ReleaseStageError, match="persisted run execution"):
        stage_release(paths, run_id=setup["run_id"], candidate_paths=[candidate_path])


def test_production_rubric_scores_must_come_from_the_bound_verifier() -> None:
    candidate_digest = digest({"candidate": 1})
    evidence_digest = digest({"evidence": 1})
    rubric_lock_digest = digest({"rubric": 1})
    verification = {
        "artifact_kind": "verification_result",
        "candidate_digest": candidate_digest,
        "evidence_digest": evidence_digest,
        "outcome": "pass",
        "checks": [{"name": "candidate_evidence_binding", "outcome": "pass"}],
        "rubric_scores": {
            "authenticity": {"score": 2, "maximum": 2},
            "correctness": {"score": 1, "maximum": 1},
            "three_d_relevance": {"score": 1, "maximum": 1},
        },
        "rubric_lock_digest": rubric_lock_digest,
        "work_item_id": "production-leaf",
        "state_transition": None,
    }
    verification_digest = digest(verification)
    rubric = {
        "lock_digest": rubric_lock_digest,
        "release_thresholds": {
            "authenticity": {"required": 2, "maximum": 2},
            "correctness": {"required": 1, "maximum": 1},
            "three_d_relevance": {"required": 1, "maximum": 1},
        },
    }

    result = release_staging._rubric_dry_run(
        verification=verification,
        verification_digest=verification_digest,
        candidate_digest=candidate_digest,
        evidence_digest=evidence_digest,
        rubric=rubric,
    )
    assert result == {"digest": verification_digest, "scores": verification["rubric_scores"]}

    wrong_rubric = dict(verification)
    wrong_rubric["rubric_lock_digest"] = digest({"other-rubric": 1})
    with pytest.raises(ReleaseStageError, match="another rubric lock"):
        release_staging._rubric_dry_run(
            verification=wrong_rubric,
            verification_digest=digest(wrong_rubric),
            candidate_digest=candidate_digest,
            evidence_digest=evidence_digest,
            rubric=rubric,
        )


def test_concurrent_identical_reviews_commit_exactly_one_receipt(tmp_path: Path) -> None:
    setup = _running_fixture_run(tmp_path)
    paths = setup["paths"]
    candidate_path, evidence_path, _, candidate, _ = _execution_artifacts(setup)
    barrier = threading.Barrier(3)

    def attempt_review() -> dict[str, object] | Exception:
        barrier.wait()
        try:
            return decide_review(
                paths,
                run_id=setup["run_id"],
                candidate_path=candidate_path,
                evidence_path=evidence_path,
                reviewer="domain-expert",
                decision="approved",
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt_review) for _ in range(2)]
        barrier.wait()
        results = [future.result() for future in futures]

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], InvalidStateError)
    winner = successes[0]
    assert Path(winner["review_path"]).is_file()
    state_review = load_run(paths, setup["run_id"])["body"]["candidate_reviews"][digest(candidate)]
    assert state_review["decision_digest"] == winner["review_digest"]
    receipts = list(paths.review_receipts.glob("*.decision.json"))
    audits = list((run_directory(paths, setup["run_id"]) / "private" / "audit" / "reviews").glob("*.decision.json"))
    assert receipts == [Path(winner["review_path"])]
    assert len(audits) == 1
    assert json.loads(audits[0].read_text(encoding="utf-8"))["content_digest"] == winner["review_digest"]


@pytest.mark.parametrize(
    "mutation",
    [None, "solver_content", "scorer_content", "added", "missing", "symlink"],
)
def test_production_export_verifies_staged_solver_and_scorer_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str | None,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    run_id = "production-run-1"
    plan_digest = digest({"plan": 1})
    config_digest = digest({"config": 1})
    viewer_digest = digest({"viewer": 1})
    rubric_lock_digest = digest({"rubric": 1})
    candidate_digest = digest({"candidate": 1})
    evidence_digest = digest({"evidence": 1})
    review_digest = digest({"review": 1})
    dry_run_digest = digest({"dry-run": 1})
    worker_result_digest = digest({"worker-result": 1})
    execution_manifest_digest = digest({"execution-manifest": 1})
    verifier_output_digest = digest({"verifier": 1})
    work_item_dependency_digest = digest({"work-item": 1})
    stage_digests = {
        "author": {"output_contract_digest": candidate_digest, "fixture_only": False},
        "evidence_worker": {"output_contract_digest": evidence_digest, "fixture_only": False},
        "verifier": {"output_contract_digest": verifier_output_digest, "fixture_only": False},
    }
    execution_bindings = {
        "execution_manifest_digest": execution_manifest_digest,
        "worker_result_digest": worker_result_digest,
        "author_output_digest": candidate_digest,
        "evidence_output_digest": evidence_digest,
        "verifier_output_digest": verifier_output_digest,
        "stage_digests_digest": digest(stage_digests),
    }
    run = {
        "body": {
            "run_id": run_id,
            "plan_digest": plan_digest,
            "config_digest": config_digest,
            "viewer_profile_digest": viewer_digest,
            "lock_digests": {"rubric_lock_digest": rubric_lock_digest},
            "fixture_only": False,
        }
    }
    config = {
        "body": {
            "profile_mode": "production_release",
            "release_eligible": True,
            "release": {"enabled": True},
            "model_routing": {"execution_eligible": True},
            "fixture_routing": None,
            "lock_digests": {"rubric_lock_digest": rubric_lock_digest},
            "rubric": {
                "lock_digest": rubric_lock_digest,
                "document_digest": digest({"rubric-document": 1}),
                "release_thresholds": {
                    "authenticity": {"required": 2, "maximum": 2},
                    "correctness": {"required": 1, "maximum": 1},
                    "three_d_relevance": {"required": 1, "maximum": 1},
                },
                "fixture_policy": {},
            },
        }
    }
    viewer = {"body": {"local_fixture": False, "production_eligible": True}}
    directory = run_directory(paths, run_id)
    solver_dir = directory / "staging" / "solver"
    scorer_dir = directory / "staging" / "scorer"
    audit_dir = directory / "staging" / "audit"
    solver_filename = "task-production.json"
    scorer_filename = "scorer-production.json"
    audit_filename = "audit-production.json"
    solver_payload = {
        "artifact_kind": "solver_safe_export",
        "task_id": "production-task-1",
        "prompt": "Inspect the controlled scene.",
        "anonymous_scene_ref": "scene-production-001",
        "viewer_session_request": {
            "request_id": "production-request-1",
            "viewer_profile_digest": viewer_digest,
            "allowed_operations": ["inspect"],
        },
    }
    scorer_payload = {
        "artifact_kind": "scorer_private_input",
        "candidate_digest": candidate_digest,
        "reference_answer": {"answer": "private"},
        "rubric": {"criterion": "visible"},
        "review_digest": review_digest,
        "viewer_evidence_digest": evidence_digest,
        "rubric_lock_digest": rubric_lock_digest,
        "work_item_dependency_digest": work_item_dependency_digest,
        **execution_bindings,
    }
    solver_path = solver_dir / solver_filename
    scorer_path = scorer_dir / scorer_filename
    atomic_write_json(solver_path, solver_payload, private=True)
    atomic_write_json(scorer_path, scorer_payload, private=True)
    write_json(audit_dir / audit_filename, {"private": "must-not-export"})
    staging_root = directory / "staging"
    solver_artifact = _staged_artifact_descriptor(staging_root, solver_path)
    scorer_artifact = _staged_artifact_descriptor(staging_root, scorer_path)
    manifest = make_envelope(
        kind="release_staging_manifest",
        identifier="release-staging-production-run-1",
        status="staged_not_exported",
        body={
            "run_id": run_id,
            "plan_digest": plan_digest,
            "config_digest": config_digest,
            "viewer_profile_digest": viewer_digest,
            "lock_digests": run["body"]["lock_digests"],
            "fixture_only": False,
            "staged": [
                {
                    "candidate_digest": candidate_digest,
                    "evidence_digest": evidence_digest,
                    "review_digest": review_digest,
                    **execution_bindings,
                    "rubric_dry_run_digest": dry_run_digest,
                    "solver_file": solver_filename,
                    "scorer_file": scorer_filename,
                    "audit_file": audit_filename,
                    "solver_artifact": solver_artifact,
                    "scorer_artifact": scorer_artifact,
                }
            ],
        },
    )
    write_json(directory / "staging" / "manifest.json", manifest)
    monkeypatch.setattr(release_staging, "load_run", lambda _paths, _run_id: run)
    monkeypatch.setattr(release_staging, "validate_run_locks", lambda _paths, _run: ({}, config, viewer))
    monkeypatch.setattr(
        release_staging,
        "_load_execution_for_candidate",
        lambda *_args, **_kwargs: {
            "entry": {
                "candidate_digest": candidate_digest,
                "evidence_digest": evidence_digest,
                "verification_digest": verifier_output_digest,
                "worker_result_digest": worker_result_digest,
                "execution_manifest_digest": execution_manifest_digest,
                "stage_digests": stage_digests,
            },
            "candidate": {
                "artifact_kind": "author_private_candidate",
                "task_id": solver_payload["task_id"],
                "prompt": solver_payload["prompt"],
                "anonymous_scene_ref": solver_payload["anonymous_scene_ref"],
                "viewer_session_request": solver_payload["viewer_session_request"],
            },
        },
    )
    monkeypatch.setattr(
        release_staging,
        "_candidate_work_item",
        lambda *_args, **_kwargs: {
            "leaf_id": "production-leaf",
            "dependency_digest": work_item_dependency_digest,
            "approved_operations": ["inspect"],
        },
    )
    monkeypatch.setattr(
        release_staging,
        "_approved_review",
        lambda *_args, **_kwargs: {"content_digest": review_digest},
    )

    if mutation == "solver_content":
        mutated = dict(solver_payload)
        mutated["prompt"] = "Inspect a different controlled scene."
        atomic_write_json(solver_path, mutated, private=True)
    elif mutation == "scorer_content":
        mutated = dict(scorer_payload)
        mutated["reference_answer"] = {"answer": "changed after staging"}
        atomic_write_json(scorer_path, mutated, private=True)
    elif mutation == "added":
        added = dict(solver_payload)
        added["task_id"] = "production-task-added"
        atomic_write_json(solver_dir / "task-added.json", added, private=True)
    elif mutation == "missing":
        scorer_path.unlink()
    elif mutation == "symlink":
        symlink_target = tmp_path / "replacement-solver.json"
        atomic_write_json(symlink_target, solver_payload, private=True)
        solver_path.unlink()
        solver_path.symlink_to(symlink_target)

    if mutation is not None:
        with pytest.raises((ReleaseStageError, SolverBundleLeakageError)):
            export_release(paths, run_id=run_id, output_dir=tmp_path / "public-release")
        assert not (tmp_path / "public-release").exists()
        return

    result = export_release(paths, run_id=run_id, output_dir=tmp_path / "public-release")

    assert result["status"] == "exported"
    assert (Path(result["solver_export"]) / solver_filename).is_file()
    assert (Path(result["scorer_export"]) / scorer_filename).is_file()
    assert not (tmp_path / "public-release" / "audit").exists()
    assert_solver_bundle_safe(result["solver_export"])
    export_manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert export_manifest["body"]["solver_artifacts"] == [solver_artifact]
    assert export_manifest["body"]["scorer_artifacts"] == [scorer_artifact]


def test_solver_validator_rejects_empty_and_private_solver_artifacts(tmp_path: Path) -> None:
    empty_solver = tmp_path / "empty-solver"
    empty_solver.mkdir()
    with pytest.raises(SolverBundleLeakageError, match="at least one"):
        assert_solver_bundle_safe(empty_solver)

    solver = tmp_path / "solver"
    write_json(
        solver / "task-public.json",
        {
            "artifact_kind": "solver_safe_export",
            "task_id": "fixture-task-1",
            "prompt": "Inspect the controlled scene.",
            "anonymous_scene_ref": "scene-fixture-biology-001",
            "viewer_session_request": {
                "request_id": "request-1",
                "viewer_profile_digest": "sha256:" + "a" * 64,
                "allowed_operations": ["inspect"],
            },
            "reference_answer": {"answer": "private"},
        },
    )
    with pytest.raises(SolverBundleLeakageError, match="reference_answer"):
        assert_solver_bundle_safe(solver)
