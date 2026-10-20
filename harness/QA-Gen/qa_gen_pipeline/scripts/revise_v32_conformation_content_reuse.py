#!/usr/bin/env python3
"""Create one audited content-reuse revision for the V32 conformation pilot.

The source run is immutable.  This command writes a new local-only run that
reuses its completed brief and QuestionDraft verbatim, rebuilds the Candidate
with the deterministic composer, rebinds both deterministic TruthRecords to
the new Candidate, and invokes only an independent verifier when explicitly
asked.  It never stages or releases an artifact.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from typing import Any, Mapping

import yaml

from qagen.batch import text_only_pilot as pilot
from qagen.contracts import digest, make_envelope, require_digest, verify_envelope
from qagen.errors import InputError
from qagen.paths import WorkspacePaths, require_within, safe_component
from qagen.providers import build_provider_adapters
from qagen.providers.contracts import MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE
from qagen.reviewer_bundle import verify_pending_reviewer_bundle
from qagen.storage.atomic import atomic_write_json, atomic_write_yaml, read_json
from qagen.workers import WorkerRequest, load_verified_routing_policy


class ContentReuseRevisionError(InputError):
    """The local-only revision cannot prove one of its required boundaries."""


_RUN_STATE_KIND = "qagen_v32_text_only_execution_state"
_LOCK_KIND = "qagen_v32_text_only_execution_lock"
_BUNDLE_KIND = "qagen_v32_text_only_execution_bundle"
_TRUTH_KIND = "v32_deterministic_truth_record"
_SAFE_RUN_SUFFIX = "revision-001"
_DEFECTS = (
    {
        "code": "duplicated_selector_phrase",
        "requirement": "State the selector once, as an ordered Dihedral-tool action.",
    },
    {
        "code": "capitalization_and_sentence_boundary",
        "requirement": "Use one grammatical imperative with no injected capitalized fragment.",
    },
    {
        "code": "false_highlighted_atoms_claim",
        "requirement": "Do not claim atom highlights when the reviewer viewer exposes IDs only in the sidebar.",
    },
)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContentReuseRevisionError(f"{label}_invalid")
    return dict(value)


def _read(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ContentReuseRevisionError(f"{label}_unavailable")
    try:
        return _mapping(read_json(path), label)
    except (InputError, OSError) as exc:
        raise ContentReuseRevisionError(f"{label}_invalid") from exc


def _workspace_for(run_root: Path) -> Path:
    for parent in (run_root, *run_root.parents):
        if parent.name == ".qagen":
            return parent.parent
    raise ContentReuseRevisionError("source_run_is_not_in_a_qagen_workspace")


def _artifact(workspace: Path, reference: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    path_value = reference.get("path")
    expected = reference.get("content_digest")
    if not isinstance(path_value, str):
        raise ContentReuseRevisionError(f"{label}_reference_invalid")
    try:
        require_digest(expected, f"{label} content digest")
        path = require_within(workspace, workspace / path_value, label)
    except InputError as exc:
        raise ContentReuseRevisionError(f"{label}_reference_invalid") from exc
    value = _read(path, label=label)
    try:
        checked = verify_envelope(value)
    except InputError as exc:
        raise ContentReuseRevisionError(f"{label}_unattested") from exc
    if checked["content_digest"] != expected:
        raise ContentReuseRevisionError(f"{label}_digest_changed")
    return checked


def _load_source(source_run: Path, source_bundle: Path) -> dict[str, Any]:
    bundle = verify_pending_reviewer_bundle(source_bundle)
    state = _read(source_run / "state.json", label="source_state")
    try:
        state = verify_envelope(state, kind=_RUN_STATE_KIND)
    except InputError as exc:
        raise ContentReuseRevisionError("source_state_unattested") from exc
    body = _mapping(state.get("body"), "source_state_body")
    if (
        state.get("status") != "verification_complete_pending_human_review"
        or body.get("status") != "verification_complete_pending_human_review"
        or body.get("staging_enabled") is not False
        or body.get("release_eligible") is not False
        or bundle.get("task_id") != body.get("run_id")
    ):
        raise ContentReuseRevisionError("source_run_is_not_pending_human_review")
    candidate = _read(source_run / "outputs" / "candidate.json", label="source_candidate")
    brief = _read(source_run / "outputs" / "brief_builder.json", label="source_brief")
    draft = _read(source_run / "outputs" / "author.json", label="source_question_draft")
    verifier = _read(source_run / "outputs" / "verifier.json", label="source_verifier")
    for name, value in (("candidate", candidate), ("brief", brief), ("draft", draft), ("verifier", verifier)):
        digest_name = {
            "candidate": "candidate_digest",
            "brief": None,
            "draft": None,
            "verifier": "verification_digest",
        }[name]
        if digest_name is not None and body.get(digest_name) != digest(value):
            raise ContentReuseRevisionError(f"source_{name}_digest_changed")
    stages = _mapping(body.get("stages"), "source_stages")
    for role, value in (("brief_builder", brief), ("author", draft)):
        stage = _mapping(stages.get(role), f"source_{role}_stage")
        if stage.get("status") != "completed" or stage.get("output_digest") != digest(value):
            raise ContentReuseRevisionError(f"source_{role}_not_reusable")
    lock = _read(source_run / "execution-lock.json", label="source_lock")
    try:
        lock = verify_envelope(lock, kind=_LOCK_KIND)
    except InputError as exc:
        raise ContentReuseRevisionError("source_lock_unattested") from exc
    execution_bundle = _read(source_run / "execution-bundle.json", label="source_execution_bundle")
    try:
        execution_bundle = verify_envelope(execution_bundle, kind=_BUNDLE_KIND)
    except InputError as exc:
        raise ContentReuseRevisionError("source_execution_bundle_unattested") from exc
    target = _mapping(
        _mapping(execution_bundle.get("body"), "source_execution_bundle_body")
        .get("public_brief_input"),
        "source_public_brief_input",
    ).get("child_target")
    target = _mapping(target, "source_child_target")
    if digest(target) != digest(brief):
        raise ContentReuseRevisionError("source_brief_does_not_match_target")
    lock_body = _mapping(lock.get("body"), "source_lock_body")
    artifacts = _mapping(lock_body.get("artifacts"), "source_lock_artifacts")
    first_receipt = _artifact(_workspace_for(source_run), _mapping(artifacts.get("first_receipt"), "first_receipt"), label="first_receipt")
    first_sidecar = _artifact(_workspace_for(source_run), _mapping(artifacts.get("first_sidecar"), "first_sidecar"), label="first_sidecar")
    second_receipt = _artifact(_workspace_for(source_run), _mapping(artifacts.get("second_receipt"), "second_receipt"), label="second_receipt")
    second_sidecar = _artifact(_workspace_for(source_run), _mapping(artifacts.get("second_sidecar"), "second_sidecar"), label="second_sidecar")
    return {
        "bundle": bundle,
        "state": state,
        "state_body": body,
        "candidate": candidate,
        "brief": brief,
        "draft": draft,
        "verifier": verifier,
        "lock": lock,
        "execution_bundle": execution_bundle,
        "target": target,
        "first_receipt": first_receipt,
        "first_sidecar": first_sidecar,
        "second_receipt": second_receipt,
        "second_sidecar": second_sidecar,
    }


def _one_call_runtime(
    *,
    source_runtime: Path,
    destination: Path,
    approval_destination: Path,
    endpoint: str,
    run_id: str,
) -> tuple[Path, Path, str, str]:
    try:
        raw = yaml.safe_load(source_runtime.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContentReuseRevisionError("source_runtime_unavailable") from exc
    runtime = _mapping(raw, "source_runtime")
    request = _mapping(runtime.get("request"), "source_runtime_request")
    runtime["request"] = {**request, "max_attempts": 1, "initial_backoff_seconds": 0.0}
    runtime_path = destination / "runtime" / "provider-runtime.one-call.yaml"
    runtime_digest = digest(runtime)
    atomic_write_yaml(runtime_path, runtime, private=True)
    approval = {
        "schema_version": 1,
        "kind": "qagen_provider_endpoint_approval",
        "id": f"v32-content-reuse-one-call-{run_id}",
        "status": "approved",
        "decision": "allow_credentialed_https_endpoint",
        "endpoint": endpoint.rstrip("/"),
        "credential_env": "QAGEN_UNIV_API_KEY",
        "runtime_config_digest": runtime_digest,
        "scope": "user-authorized V32 content-reuse revision verifier; one local call; no staging, release, or production",
        "decided_by": "derived_from_user_explicit_generation_authorization",
        "decided_at": "2026-08-27",
        "production_eligible": False,
    }
    approval_path = approval_destination / "provider-endpoint-approval.one-call.yaml"
    atomic_write_yaml(approval_path, approval, private=True)
    return runtime_path, approval_path, runtime_digest, digest(approval)


def _request_receipts(source: Mapping[str, Any], revision_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    body = source["state_body"]
    candidate_digest = digest(source["candidate"])
    verification_digest = digest(source["verifier"])
    request = make_envelope(
        kind="qagen_v32_preproduction_revision_request",
        identifier=f"revision-request-{candidate_digest.removeprefix('sha256:')[:16]}",
        status="revise",
        producer="qagen/0.1.0:automated_preproduction_review",
        body={
            "source_run_id": body["run_id"],
            "source_work_item_id": body["work_item_id"],
            "source_candidate_digest": candidate_digest,
            "source_verification_digest": verification_digest,
            "decision_actor": "automated_preproduction_review",
            "human_approval": False,
            "defects": list(_DEFECTS),
            "required_candidate_contract": {
                "single_measurement_task": True,
                "selector_surface": "sidebar_visible_atom_ids",
                "selector_order": "locked_atom_quadruplet_order",
                "disallowed_phrase": "highlighted atoms",
                "answer_format": "signed_numeric_degree_nearest_whole",
            },
            "staging_enabled": False,
            "release_eligible": False,
        },
    )
    assessment = make_envelope(
        kind="qagen_v32_pending_review_assessment",
        identifier=f"review-assessment-{candidate_digest.removeprefix('sha256:')[:16]}",
        status="revise",
        producer="qagen/0.1.0:automated_preproduction_review",
        body={
            "candidate_digest": candidate_digest,
            "review_decision": "revise",
            "human_approval": False,
            "dimensions": {
                "authenticity": "needs_candidate_revision_before_human_review",
                "answerability": "blocked_by_selector_discoverability_and_ambiguous_wording",
                "viewer_necessity": "supported_by_private_native_measurement_only",
                "selector_discoverability": "failed_no_canvas_highlights_sidebar_ids_only",
                "leakage": "passed_in_existing_public_bundle_audit",
                "wording": "failed_duplicated_selector_phrase_and_capitalization",
            },
            "revision_request_digest": request["content_digest"],
            "staging_enabled": False,
            "release_eligible": False,
        },
    )
    atomic_write_json(revision_dir / "revision-request.json", request, private=True)
    atomic_write_json(revision_dir / "review-assessment.json", assessment, private=True)
    return request, assessment


def _prompt_snapshot(adapter: Any, request: WorkerRequest, destination: Path) -> dict[str, str]:
    materialize = getattr(adapter, "_prompt_material", None)
    if not callable(materialize):
        raise ContentReuseRevisionError("independent_verifier_has_no_prompt_renderer")
    output_schema, visual_inputs, system, user = materialize(request)
    if visual_inputs:
        raise ContentReuseRevisionError("revision_verifier_attempted_visual_transport")
    snapshot = {
        "role": request.role,
        "provider": request.provider,
        "model": request.model,
        "system": system,
        "user": user,
        "output_schema": dict(output_schema),
        "visual_input_count": 0,
    }
    path = destination / "prompts" / "verifier.json"
    atomic_write_json(path, snapshot, private=True)
    return {"path": path.name, "content_digest": digest(snapshot), "exact_rendered_prompt": True}


def _copy_private_json(destination: Path, name: str, value: Mapping[str, Any]) -> None:
    atomic_write_json(destination / "outputs" / name, dict(value), private=True)


def _reused_stage(source_stage: Mapping[str, Any], receipt_digest: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "attempts": 0,
        "provider_calls": 0,
        "worker_kind": "content_reuse",
        "provider": source_stage["provider"],
        "model": source_stage["model"],
        "input_digest": source_stage["input_digest"],
        "output_digest": source_stage["output_digest"],
        "content_reuse_receipt_digest": receipt_digest,
        "agent_reference": "source_stage_reused_without_agent_execution",
        "token_in": 0,
        "token_out": 0,
        "cost_usd": 0.0,
        "cost_accounting": "zero_cost_content_reuse",
    }


def _write_revised_run(
    *,
    workspace: WorkspacePaths,
    source: Mapping[str, Any],
    revision_request: Mapping[str, Any],
    revision_dir: Path,
    run_id: str,
    source_runtime: Path,
    endpoint: str,
    environ: Mapping[str, str],
    routing_policy: Path,
    execute_verifier: bool,
) -> dict[str, Any]:
    destination = workspace.private / "text-only-agent-pilots" / run_id
    if destination.exists() or destination.is_symlink():
        raise ContentReuseRevisionError("revised_run_already_exists")
    stage = Path(tempfile.mkdtemp(prefix=f".{run_id}.staging-", dir=destination.parent))
    try:
        os.chmod(stage, 0o700)
        runtime_path, approval_path, runtime_digest, approval_digest = _one_call_runtime(
            source_runtime=source_runtime,
            destination=stage,
            approval_destination=revision_dir,
            endpoint=endpoint,
            run_id=run_id,
        )
        target = _mapping(source["target"], "source_target")
        lock_body = deepcopy(_mapping(_mapping(source["lock"], "source_lock").get("body"), "source_lock_body"))
        lock_body.update(
            {
                "runtime_config_digest": runtime_digest,
                "endpoint_approval_digest": approval_digest,
                "transport_attempt_max": 1,
                "scope": "V32 content-reuse revision with one independent verifier call; no staging, release, or production",
                "revision_parent": {
                    "source_run_id": source["state_body"]["run_id"],
                    "source_candidate_digest": digest(source["candidate"]),
                    "revision_request_digest": revision_request["content_digest"],
                },
            }
        )
        lock = make_envelope(
            kind=_LOCK_KIND,
            identifier=f"content-reuse-lock-{run_id}",
            status="approved_for_one_slot_local_pilot",
            producer="qagen/0.1.0:content_reuse_revision",
            body=lock_body,
        )
        atomic_write_json(stage / "execution-lock.json", lock, private=True)
        bundle_body = deepcopy(_mapping(_mapping(source["execution_bundle"], "source_bundle").get("body"), "source_bundle_body"))
        bundle_body["execution_lock_digest"] = lock["content_digest"]
        bundle = make_envelope(
            kind=_BUNDLE_KIND,
            identifier=f"content-reuse-bundle-{run_id}",
            status="locked",
            producer="qagen/0.1.0:content_reuse_revision",
            body=bundle_body,
        )
        atomic_write_json(stage / "execution-bundle.json", bundle, private=True)
        source_stages = _mapping(source["state_body"].get("stages"), "source_stages")
        receipt = make_envelope(
            kind="qagen_v32_content_reuse_receipt",
            identifier=f"content-reuse-{run_id}",
            status="reused_without_agent_execution",
            producer="qagen/0.1.0:content_reuse_revision",
            body={
                "revision_request_digest": revision_request["content_digest"],
                "source_run_id": source["state_body"]["run_id"],
                "source_bundle_id": source["bundle"]["bundle_id"],
                "reused_outputs": {
                    "brief_builder": digest(source["brief"]),
                    "author_question_draft": digest(source["draft"]),
                },
                "agent_calls": {"brief_builder": 0, "author": 0},
                "staging_enabled": False,
                "release_eligible": False,
            },
        )
        atomic_write_json(stage / "content-reuse-receipt.json", receipt, private=True)
        candidate = pilot._compose_candidate(
            source["draft"],
            target=target,
            run_id=run_id,
            work_item_id=str(lock_body["work_item_id"]),
            plan_digest=str(source["state_body"]["plan_digest"]),
            config_digest=str(lock_body["config_digest"]),
            knowledge_digest=str(lock_body["knowledge_boundary_digest"]),
            viewer_profile_digest=str(lock_body["viewer_profile_digest"]),
            expected_answer=pilot._numeric_answer(
                _mapping(_mapping(source["first_sidecar"].get("body"), "first_sidecar_body").get("measurement_result"), "first_result").get("value"),
                "first_measurement_value",
            ),
        )
        first_truth = pilot._materialize_truth_record(
            candidate=candidate,
            target=target,
            receipt=source["first_receipt"],
            sidecar=source["first_sidecar"],
            work_item_id=str(lock_body["work_item_id"]),
            purpose="author_truth",
        )
        second_truth = pilot._materialize_truth_record(
            candidate=candidate,
            target=target,
            receipt=source["second_receipt"],
            sidecar=source["second_sidecar"],
            work_item_id=str(lock_body["work_item_id"]),
            purpose="verifier_independent_evidence",
        )
        pilot._candidate_gate(
            candidate,
            target=target,
            run_id=run_id,
            work_item_id=str(lock_body["work_item_id"]),
            plan_digest=str(source["state_body"]["plan_digest"]),
            config_digest=str(lock_body["config_digest"]),
            knowledge_digest=str(lock_body["knowledge_boundary_digest"]),
            viewer_profile_digest=str(lock_body["viewer_profile_digest"]),
            expected_answer=pilot._numeric_answer(
                _mapping(_mapping(source["first_sidecar"].get("body"), "first_sidecar_body").get("measurement_result"), "first_result").get("value"),
                "first_measurement_value",
            ),
            tolerance=pilot._numeric_answer(
                _mapping(_mapping(source["first_sidecar"].get("body"), "first_sidecar_body").get("measurement_tolerance"), "first_tolerance").get("absolute"),
                "first_measurement_tolerance",
            ),
        )
        _copy_private_json(stage, "brief_builder.json", source["brief"])
        _copy_private_json(stage, "author.json", source["draft"])
        _copy_private_json(stage, "candidate.json", candidate)
        _copy_private_json(stage, "truth_builder.json", first_truth)
        atomic_write_json(stage / "evidence" / "independent-second-session-truth-record.json", second_truth, private=True)
        verifier_input = pilot._stage_inputs(
            lock=lock,
            bundle=bundle,
            plan={"content_digest": source["state_body"]["plan_digest"]},
            run_id=run_id,
            target=target,
            first_receipt=source["first_receipt"],
            first_sidecar=source["first_sidecar"],
            second_receipt=source["second_receipt"],
            second_sidecar=source["second_sidecar"],
            brief=source["brief"],
            candidate=candidate,
            independent_truth_record=second_truth,
        )
        atomic_write_json(stage / "inputs" / "verifier.json", verifier_input, private=True)
        verification: dict[str, Any] | None = None
        verifier_stage: dict[str, Any]
        if execute_verifier:
            policy = load_verified_routing_policy(routing_policy)
            route = policy.role("verifier")
            adapters = build_provider_adapters(
                policy,
                runtime_config_path=runtime_path,
                runtime_config_digest=runtime_digest,
                environ={**environ, "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH": str(approval_path)},
                execution_profile=MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE,
            )
            request = WorkerRequest(
                role="verifier",
                provider=str(route["provider"]),
                model=str(route["allowed_models"][0]),
                input_contract=verifier_input,
                max_tokens=int(route["max_tokens"]),
                max_cost_usd=float(route["max_cost_usd"]),
                run_id=run_id,
                work_item_id=str(lock_body["work_item_id"]),
            )
            adapter = adapters[(request.provider, request.model)]
            bounded = adapter.with_prompt_reservation(request)
            prompt = _prompt_snapshot(adapter, bounded, stage)
            started = perf_counter()
            result = adapter.execute(bounded)
            if result.token_in + result.token_out > bounded.max_tokens or result.actual_cost_usd > request.max_cost_usd:
                raise ContentReuseRevisionError("independent_verifier_usage_exceeds_reservation")
            verification = dict(result.output_contract)
            handoff = _mapping(verifier_input.get("independent_measurement_handoff"), "verifier_handoff")
            pilot._verifier_gate(
                verification,
                candidate_digest=digest(candidate),
                evidence_digest=str(_mapping(source["second_sidecar"].get("body"), "second_sidecar_body")["evidence_digest"]),
                rubric_lock_digest=str(lock_body["config_digest"]),
                work_item_id=str(lock_body["work_item_id"]),
                handoff_digest=str(handoff["handoff_digest"]),
                receipt_digests=list(handoff["receipt_digests"]),
            )
            _copy_private_json(stage, "verifier.json", verification)
            verifier_stage = {
                "status": "completed",
                "attempts": 1,
                "provider_calls": 1,
                "worker_kind": "independent_verifier",
                "provider": result.provider,
                "model": result.model,
                "input_digest": result.input_contract_digest,
                "output_digest": result.output_contract_digest,
                "prompt": prompt,
                "token_in": result.token_in,
                "token_out": result.token_out,
                "cost_usd": result.actual_cost_usd,
                "cost_accounting": result.cost_accounting,
                "latency_ms": round((perf_counter() - started) * 1000, 3),
                "agent_reference": "gpt-5.6-sol independent verifier",
            }
        else:
            verifier_stage = {
                "status": "not_started",
                "attempts": 0,
                "provider_calls": 0,
                "worker_kind": "independent_verifier",
                "provider": "openai",
                "model": "gpt-5.6-sol",
            }
        revised_receipt = make_envelope(
            kind="qagen_v32_content_reuse_revision_receipt",
            identifier=f"revised-candidate-{run_id}",
            status="verification_complete_pending_human_review" if verification is not None else "prepared_pending_verification",
            producer="qagen/0.1.0:content_reuse_revision",
            body={
                "revision_request_digest": revision_request["content_digest"],
                "content_reuse_receipt_digest": receipt["content_digest"],
                "source_candidate_digest": digest(source["candidate"]),
                "revised_candidate_digest": digest(candidate),
                "revised_truth_digest": digest(first_truth),
                "revised_independent_truth_digest": digest(second_truth),
                "verifier_called": verification is not None,
                "staging_enabled": False,
                "release_eligible": False,
            },
        )
        atomic_write_json(revision_dir / "revision-receipt.json", revised_receipt, private=True)
        revised_stages = {
            "brief_builder": _reused_stage(_mapping(source_stages["brief_builder"], "source_brief_stage"), receipt["content_digest"]),
            "author": _reused_stage(_mapping(source_stages["author"], "source_author_stage"), receipt["content_digest"]),
            "truth_builder": {
                "status": "completed",
                "attempts": 1,
                "provider_calls": 0,
                "worker_kind": "deterministic_locked_measurement_truth_record",
                "provider": "control_plane",
                "model": "locked_measurement_truth_record_v1",
                "input_digest": digest({"candidate_digest": digest(candidate), "purpose": "author_truth"}),
                "output_digest": digest(first_truth),
                "token_in": 0,
                "token_out": 0,
                "cost_usd": 0.0,
                "cost_accounting": "zero_cost_deterministic",
                "agent_reference": "deterministic_truth_rebound_to_revised_candidate",
            },
            "verifier": verifier_stage,
        }
        usage = {
            "token_in": int(verifier_stage.get("token_in", 0)),
            "token_out": int(verifier_stage.get("token_out", 0)),
            "cost_usd": float(verifier_stage.get("cost_usd", 0.0)),
        }
        state_body = {
            "run_id": run_id,
            "work_item_id": lock_body["work_item_id"],
            "status": "verification_complete_pending_human_review" if verification is not None else "prepared_pending_verification",
            "approval_digest": revised_receipt["content_digest"],
            "execution_lock_digest": lock["content_digest"],
            "executor_receipt_digest": receipt["content_digest"],
            "no_network_gate_digest": None,
            "plan_digest": source["state_body"]["plan_digest"],
            "candidate_digest": digest(candidate),
            "truth_digest": digest(first_truth),
            "independent_truth_record": {
                "path": f".qagen/private/text-only-agent-pilots/{run_id}/evidence/independent-second-session-truth-record.json",
                "content_digest": digest(second_truth),
            },
            "verification_digest": digest(verification) if verification is not None else None,
            "stages": revised_stages,
            "usage": usage,
            "staging_enabled": False,
            "release_eligible": False,
        }
        state = make_envelope(
            kind=_RUN_STATE_KIND,
            identifier=f"content-reuse-state-{run_id}",
            status=state_body["status"],
            producer="qagen/0.1.0:content_reuse_revision",
            body=state_body,
        )
        atomic_write_json(stage / "state.json", state, private=True)
        atomic_write_json(
            stage / "usage" / "revision-verifier-ledger.json",
            {
                "kind": "qagen_content_reuse_revision_usage",
                "source_usage": source["state_body"]["usage"],
                "revised_verifier_usage": usage,
                "settled": verification is not None and verifier_stage.get("cost_accounting") == "provider_reported",
                "reservation_upper_bound": verification is not None
                and verifier_stage.get("cost_accounting") == "reservation_upper_bound",
                "unsettled": False,
            },
            private=True,
        )
        os.replace(stage, destination)
        return {
            "run_root": str(destination),
            "run_id": run_id,
            "state_digest": state["content_digest"],
            "candidate_digest": digest(candidate),
            "truth_digest": digest(first_truth),
            "verification_digest": digest(verification) if verification is not None else None,
            "revision_receipt_digest": revised_receipt["content_digest"],
            "status": state_body["status"],
            "staging_enabled": False,
            "release_eligible": False,
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def revise(
    *,
    workspace: Path,
    source_run: Path,
    source_bundle: Path,
    revision_dir: Path,
    run_id: str | None,
    source_runtime: Path,
    endpoint: str,
    environ: Mapping[str, str],
    routing_policy: Path,
    execute_verifier: bool,
) -> dict[str, Any]:
    paths = WorkspacePaths.from_value(workspace)
    paths.ensure()
    source_root = source_run.resolve()
    if _workspace_for(source_root) != paths.root.resolve():
        raise ContentReuseRevisionError("source_run_workspace_does_not_match_requested_workspace")
    revision_target = revision_dir.resolve()
    if revision_target.exists() or revision_target.is_symlink():
        raise ContentReuseRevisionError("revision_directory_already_exists")
    revision_target.parent.mkdir(parents=True, exist_ok=True)
    revision_target.mkdir(mode=0o700)
    try:
        source = _load_source(source_root, source_bundle.resolve())
        request, assessment = _request_receipts(source, revision_target)
        revised_run_id = safe_component(run_id or f"{source['state_body']['run_id']}-{_SAFE_RUN_SUFFIX}", "revised run id")
        rebuilt = _write_revised_run(
            workspace=paths,
            source=source,
            revision_request=request,
            revision_dir=revision_target,
            run_id=revised_run_id,
            source_runtime=source_runtime.resolve(),
            endpoint=endpoint,
            environ=environ,
            routing_policy=routing_policy.resolve(),
            execute_verifier=execute_verifier,
        )
        return {
            "revision_dir": str(revision_target),
            "revision_request_digest": request["content_digest"],
            "source_review_assessment_digest": assessment["content_digest"],
            **rebuilt,
        }
    except BaseException:
        shutil.rmtree(revision_target, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--revision-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--source-runtime", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--routing-policy", type=Path, required=True)
    parser.add_argument("--execute-verifier", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = revise(
            workspace=args.workspace,
            source_run=args.source_run,
            source_bundle=args.source_bundle,
            revision_dir=args.revision_dir,
            run_id=args.run_id,
            source_runtime=args.source_runtime,
            endpoint=args.endpoint,
            environ=dict(os.environ),
            routing_policy=args.routing_policy,
            execute_verifier=args.execute_verifier,
        )
    except ContentReuseRevisionError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
