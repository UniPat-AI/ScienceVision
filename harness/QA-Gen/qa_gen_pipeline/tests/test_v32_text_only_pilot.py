from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import pytest

from qagen.batch import text_only_pilot as text_only_pilot
from qagen.batch.text_only_pilot import (
    TextOnlyPilotInputs,
    create_one_slot_provider_runtime,
    execute_text_only_pilot,
    freeze_incomplete_text_only_pilot,
    prepare_text_only_pilot,
    run_no_network_gate,
)
from qagen.contracts import digest, make_envelope
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json, read_json
from qagen.verification import load_ground_truth_registry
from qagen.workers import WorkerRequest, WorkerResult

from conftest import PACKAGE_ROOT


def _digest(label: str) -> str:
    return digest({"v32_text_only_test": label})


def _write(paths: WorkspacePaths, relative: str, envelope: Mapping[str, Any]) -> Path:
    path = paths.private / relative
    atomic_write_json(path, dict(envelope), private=True)
    return path


def _envelope(kind: str, identifier: str, status: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return make_envelope(kind=kind, identifier=identifier, status=status, body=dict(body))


def _inputs(tmp_path: Path) -> tuple[WorkspacePaths, TextOnlyPilotInputs]:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    source = _digest("source")
    scene = "scene://v32-text-only-test"
    atoms = [
        {
            "atom_id": f"atom-{index:08d}",
            "residue_id": "residue-000001",
            "atom_name": f"atom-{index:08d}",
            "element": "C",
            "coordinate_identity": f"coordinate-{index:08d}",
        }
        for index in range(1, 5)
    ]
    inventory_body = {
        "inventory_version": 1,
        "source_record_digest": source,
        "target_leaf": "chemistry.conformation_analysis",
        "scene_registry_digest": _digest("registry"),
        "anonymous_scene_ref": scene,
        "asset_set_digest": _digest("assets"),
        "asset_sha256": {"primary": _digest("primary")},
        "selector_types": ["atom_quadruplet"],
        "selector_catalog": {
            "components": [{"component_id": "component-0001", "chain_id": "chain-0001"}],
            "residues": [{"residue_id": "residue-000001", "component_id": "component-0001", "residue_name": "residue-000001", "residue_number": 1, "insertion_code": ""}],
            "atoms": atoms,
            "mask_labels": [],
            "axes": [],
            "volume_shape": [],
            "layers": [],
        },
        "backend_selector_map": {
            "component-0001": {"component_index": 1},
            "residue-000001": {"residue_index": 1},
            **{atom["atom_id"]: {"atom_index": index} for index, atom in enumerate(atoms, start=1)},
        },
        "local_only": True,
        "release_eligible": False,
    }
    inventory = _envelope("qagen_scene_inventory", "inventory", "ready", inventory_body)
    inventory_path = _write(paths, "measurement/inventory.json", inventory)
    target = {
        "target_spec_version": 1,
        "source_record_digest": source,
        "scene_inventory_digest": inventory["content_digest"],
        "anonymous_scene_ref": scene,
        "candidate_plan_digest": _digest("preauthor-plan"),
        "target_leaf": "chemistry.conformation_analysis",
        "allowed_native_operations": ["measure_dihedral"],
        "native_operation": "measure_dihedral",
        "selector": {"type": "atom_quadruplet", "atom_ids": [atom["atom_id"] for atom in atoms]},
        "answer_fields": ["dihedral_angle_deg"],
    }
    child = _envelope(
        "qagen_v32_native_conformation_child_target",
        "child",
        "preauthor_measurement_only",
        {
            "child_target_spec_digest": digest(target),
            "target_spec": target,
            "historical_truth_equivalence": "not_asserted",
        },
    )
    child_path = _write(paths, "measurement/child.json", child)
    anchor = _envelope(
        "qagen_v32_native_conformation_preauthor_anchor",
        "anchor",
        "preauthor_measurement_only",
        {
            "child_target_artifact_digest": child["content_digest"],
            "child_target_spec_digest": digest(target),
            "historical_truth_equivalence": "not_asserted",
        },
    )
    anchor_path = _write(paths, "measurement/anchor.json", anchor)
    request_digest = _digest("request")
    first_sidecar, first_receipt = _measurement_pair(target, request_digest, "first")
    second_sidecar, second_receipt = _measurement_pair(target, request_digest, "second")
    first_sidecar_path = _write(paths, "measurement/first-sidecar.json", first_sidecar)
    first_receipt["body"]["private_sidecar_digest"] = first_sidecar["content_digest"]
    first_receipt["content_digest"] = digest(first_receipt["body"])
    first_receipt_path = _write(paths, "measurement/first-receipt.json", first_receipt)
    second_sidecar_path = _write(paths, "measurement/second-sidecar.json", second_sidecar)
    second_receipt["body"]["private_sidecar_digest"] = second_sidecar["content_digest"]
    second_receipt["content_digest"] = digest(second_receipt["body"])
    second_receipt_path = _write(paths, "measurement/second-receipt.json", second_receipt)
    first_scan = _envelope("qagen_native_measurement_leak_scan", "first-scan", "passed", {"status": "passed", "measurement_receipt_digest": first_receipt["content_digest"], "private_sidecar_digest": first_sidecar["content_digest"]})
    second_scan = _envelope("qagen_native_measurement_leak_scan", "second-scan", "passed", {"status": "passed", "measurement_receipt_digest": second_receipt["content_digest"], "private_sidecar_digest": second_sidecar["content_digest"]})
    first_scan_path = _write(paths, "measurement/first-scan.json", first_scan)
    second_scan_path = _write(paths, "measurement/second-scan.json", second_scan)
    replay = _envelope("qagen_native_measurement_replay_consistency", "replay", "passed", {"status": "passed", "first_measurement_receipt_digest": first_receipt["content_digest"], "second_measurement_receipt_digest": second_receipt["content_digest"], "first_private_sidecar_digest": first_sidecar["content_digest"], "second_private_sidecar_digest": second_sidecar["content_digest"]})
    replay_path = _write(paths, "measurement/replay.json", replay)
    result = _envelope(
        "qagen_v32_native_conformation_pilot_result",
        "result",
        "attested",
        {
            "status": "attested",
            "generation_eligible": False,
            "release_eligible": False,
            "anchor_digest": anchor["content_digest"],
            "child_target_spec_digest": digest(target),
            "measurements": {
                "first": {"measurement_receipt_digest": first_receipt["content_digest"], "private_sidecar_digest": first_sidecar["content_digest"]},
                "second": {"measurement_receipt_digest": second_receipt["content_digest"], "private_sidecar_digest": second_sidecar["content_digest"]},
            },
        },
    )
    result_path = _write(paths, "measurement/result.json", result)
    manifest = _envelope("vnext_selection_batch_manifest", "manifest", "blocked", {"generation_eligible": False, "total": 1, "rows": []})
    manifest_path = _write(paths, "measurement/manifest.json", manifest)
    return paths, TextOnlyPilotInputs(
        selection_manifest=manifest_path,
        child_target=child_path,
        preauthor_anchor=anchor_path,
        pilot_result=result_path,
        inventory=inventory_path,
        first_receipt=first_receipt_path,
        first_sidecar=first_sidecar_path,
        first_leak_scan=first_scan_path,
        second_receipt=second_receipt_path,
        second_sidecar=second_sidecar_path,
        second_leak_scan=second_scan_path,
        replay_consistency=replay_path,
    )


def _measurement_pair(target: Mapping[str, Any], request_digest: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    viewer_receipt = _digest(f"viewer-{label}")
    sidecar = _envelope(
        "qagen_measurement_private_sidecar",
        f"sidecar-{label}",
        "completed",
        {
            "sidecar_version": 2,
            "measurement_request_digest": request_digest,
            "viewer_receipt_digest": viewer_receipt,
            "evidence_digest": _digest(f"evidence-{label}"),
            "operation_args": {"selector": {"type": "atom_quadruplet"}},
            "measurement_result": {"value": 169.0, "unit": "degree"},
            "measurement_tolerance": {"absolute": 0.1, "unit": "degree"},
            "viewer_session_digest": _digest(f"session-{label}"),
            "executor_identity": {"host_id": "test-host", "implementation": "test", "runtime_identity_digest": _digest("runtime")},
            "truth": {"dihedral_angle_deg": 169.0},
        },
    )
    receipt = _envelope(
        "qagen_measurement_receipt",
        f"receipt-{label}",
        "completed",
        {
            "receipt_version": 2,
            "measurement_request_digest": request_digest,
            "target_spec_digest": digest(target),
            "source_record_digest": target["source_record_digest"],
            "anonymous_scene_ref": target["anonymous_scene_ref"],
            "native_operation": "measure_dihedral",
            "viewer_receipt_digest": viewer_receipt,
            "evidence_digest": sidecar["body"]["evidence_digest"],
            "operation_args_digest": _digest(f"operations-{label}"),
            "measurement_result_digest": _digest("result"),
            "measurement_unit": "degree",
            "measurement_tolerance": {"absolute": 0.1, "unit": "degree"},
            "viewer_session_digest": sidecar["body"]["viewer_session_digest"],
            "executor_identity_digest": _digest("executor"),
            "private_sidecar_digest": _digest(f"placeholder-{label}"),
            "status": "completed",
        },
    )
    return sidecar, receipt


class _Budget:
    pass


class _FakeAdapter:
    fixture_only = False

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.execute_calls = 0

    def prompt_budget(self, _request: WorkerRequest) -> _Budget:
        return _Budget()

    def with_prompt_reservation(self, request: WorkerRequest) -> WorkerRequest:
        return request

    def execute(self, request: WorkerRequest) -> WorkerResult:
        self.execute_calls += 1
        value = dict(request.input_contract)
        if request.role == "brief_builder":
            output = value["public_child_target"]
        elif request.role == "author":
            output = {
                "artifact_kind": "v32_question_draft",
                "scientific_context": "Examine a molecular conformation in the selected structure.",
                "object_reference": "the selected molecular backbone segment",
                "question_wording": "Determine the torsional relationship represented by the selected atom sequence.",
                "reasoning_focus": "Relate the observed conformation to the local molecular geometry.",
                "state_transition": None,
            }
        elif request.role == "truth_builder":
            candidate = value["candidate"]
            sidecar = value["author_private_measurement"]["private_sidecar"]
            output = {
                "artifact_kind": "v32_text_only_truth_receipt",
                "candidate_digest": digest(candidate),
                "measurement_request_digest": sidecar["measurement_request_digest"],
                "truth": sidecar["truth"],
                "unit": "degree",
                "absolute_tolerance": 0.1,
                "work_item_id": value["work_item_id"],
                "state_transition": None,
            }
        else:
            bindings = value["required_verification_bindings"]
            handoff = value["independent_measurement_handoff"]
            output = {
                "artifact_kind": "verification_result",
                "candidate_digest": bindings["candidate_digest"],
                "evidence_digest": bindings["evidence_digest"],
                "outcome": "pass",
                "checks": [{"name": "measurement_replay_text_only", "outcome": "pass", "details": "Verified from independent text-only measurement receipt; no visual inspection claimed."}],
                "rubric_scores": {"authenticity": {"score": 1, "maximum": 1}, "correctness": {"score": 1, "maximum": 1}, "three_d_relevance": {"score": 1, "maximum": 1}},
                "rubric_lock_digest": bindings["rubric_lock_digest"],
                "work_item_id": bindings["work_item_id"],
                "attested_evidence": {"handoff_digest": handoff["handoff_digest"], "receipt_digests": handoff["receipt_digests"]},
                "state_transition": None,
            }
        return WorkerResult(
            role=request.role,
            provider=request.provider,
            model=request.model,
            input_contract_digest=request.input_contract_digest,
            output_contract=output,
            output_contract_digest=digest(output),
            fixture_only=False,
            token_in=10,
            token_out=5,
            actual_cost_usd=0.01,
            cost_accounting="provider_reported",
        )


class _InterruptBeforeAuthorAdapter(_FakeAdapter):
    """Model a process interruption after the prior stage is fully committed."""

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(provider, model)
        self.interrupt_enabled = False

    def arm(self) -> None:
        self.interrupt_enabled = True

    def prompt_budget(self, request: WorkerRequest) -> _Budget:
        if self.interrupt_enabled and request.role == "author":
            raise KeyboardInterrupt("test-only interruption before author transport")
        return super().prompt_budget(request)


class _InterruptBeforeVerifierAdapter(_FakeAdapter):
    """Model an operator interruption before verifier transport starts."""

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(provider, model)
        self.interrupt_enabled = False

    def arm(self) -> None:
        self.interrupt_enabled = True

    def prompt_budget(self, request: WorkerRequest) -> _Budget:
        if self.interrupt_enabled and request.role == "verifier":
            raise KeyboardInterrupt("test-only interruption before verifier transport")
        return super().prompt_budget(request)


def _prepared_runtime(
    tmp_path: Path,
    *,
    run_id: str,
) -> tuple[WorkspacePaths, Path, dict[str, str], dict[str, str]]:
    paths, inputs = _inputs(tmp_path)
    runtime = create_one_slot_provider_runtime(
        paths,
        run_id=run_id,
        source_runtime_path=PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml",
        endpoint="https://provider.test",
    )
    policy = PACKAGE_ROOT / "configs" / "agents" / "model_routing.v32-local-preproduction.yaml"
    prepare_text_only_pilot(
        paths,
        inputs=inputs,
        routing_policy_path=policy,
        runtime_config_path=runtime["runtime_path"],
        endpoint_approval_path=runtime["approval_path"],
        run_id=run_id,
    )
    environ = {
        "QAGEN_UNIV_API_KEY": "test-key",
        "QAGEN_UNIV_BASE_URL": "https://provider.test",
        "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH": runtime["approval_path"],
    }
    return paths, policy, runtime, environ


def test_one_slot_text_only_loop_is_private_no_retry_and_stops_for_human_review(tmp_path: Path) -> None:
    paths, inputs = _inputs(tmp_path)
    run_id = "v32-text-only-test"
    runtime = create_one_slot_provider_runtime(
        paths,
        run_id=run_id,
        source_runtime_path=PACKAGE_ROOT / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml",
        endpoint="https://provider.test",
    )
    policy = PACKAGE_ROOT / "configs" / "agents" / "model_routing.v32-local-preproduction.yaml"
    prepared = prepare_text_only_pilot(
        paths,
        inputs=inputs,
        routing_policy_path=policy,
        runtime_config_path=runtime["runtime_path"],
        endpoint_approval_path=runtime["approval_path"],
        run_id=run_id,
    )
    adapters = {
        ("anthropic", "claude-opus-5"): _FakeAdapter("anthropic", "claude-opus-5"),
        ("deepseek", "deepseek-v4-pro"): _FakeAdapter("deepseek", "deepseek-v4-pro"),
        ("openai", "gpt-5.6-sol"): _FakeAdapter("openai", "gpt-5.6-sol"),
    }
    environ = {
        "QAGEN_UNIV_API_KEY": "test-key",
        "QAGEN_UNIV_BASE_URL": "https://provider.test",
        "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH": runtime["approval_path"],
    }
    gate = run_no_network_gate(paths, run_id=run_id, routing_policy_path=policy, runtime_config_path=runtime["runtime_path"], environ=environ, adapters=adapters)
    assert gate["network_calls"] == 0
    assert sum(adapter.execute_calls for adapter in adapters.values()) == 0
    gate_record = read_json(Path(gate["gate_path"]))
    assert gate_record["body"]["checked_roles"] == ["author", "brief_builder", "verifier"]
    assert gate_record["body"]["checked_deterministic_roles"] == ["truth_builder"]

    result = execute_text_only_pilot(paths, run_id=run_id, routing_policy_path=policy, runtime_config_path=runtime["runtime_path"], environ=environ, adapters=adapters)
    assert result["status"] == "verification_complete_pending_human_review"
    assert result["staging_enabled"] is False
    assert result["release_eligible"] is False
    assert sum(adapter.execute_calls for adapter in adapters.values()) == 3
    lock = read_json(Path(prepared["lock_path"]))
    assert lock["body"]["execution_eligible"] is True
    assert lock["body"]["selection_generation_eligible"] is False
    assert lock["body"]["stage_retry_max"] == 0
    assert lock["body"]["transport_attempt_max"] == 1
    state = read_json(Path(prepared["state_path"]))
    truth_stage = state["body"]["stages"]["truth_builder"]
    assert truth_stage["provider_calls"] == 0
    assert truth_stage["worker_kind"] == "deterministic_locked_measurement_truth_record"
    assert truth_stage["token_in"] == truth_stage["token_out"] == 0
    assert truth_stage["cost_usd"] == 0.0
    assert not any(paths.runs.iterdir())


def test_interrupt_before_author_is_automatically_frozen_without_resuming(tmp_path: Path) -> None:
    run_id = "v32-text-only-restart"
    paths, policy, runtime, environ = _prepared_runtime(tmp_path, run_id=run_id)
    brief = _FakeAdapter("anthropic", "claude-opus-5")
    interrupted_author = _InterruptBeforeAuthorAdapter("deepseek", "deepseek-v4-pro")
    interrupted = {
        ("anthropic", "claude-opus-5"): brief,
        ("deepseek", "deepseek-v4-pro"): interrupted_author,
        ("openai", "gpt-5.6-sol"): _FakeAdapter("openai", "gpt-5.6-sol"),
    }
    run_no_network_gate(
        paths,
        run_id=run_id,
        routing_policy_path=policy,
        runtime_config_path=runtime["runtime_path"],
        environ=environ,
        adapters=interrupted,
    )
    interrupted_author.arm()
    with pytest.raises(KeyboardInterrupt, match="test-only interruption"):
        execute_text_only_pilot(
            paths,
            run_id=run_id,
            routing_policy_path=policy,
            runtime_config_path=runtime["runtime_path"],
            environ=environ,
            adapters=interrupted,
        )
    state_path = paths.private / "text-only-agent-pilots" / run_id / "state.json"
    interrupted_state = read_json(state_path)
    assert interrupted_state["status"] == interrupted_state["body"]["status"] == "incomplete_frozen"
    assert interrupted_state["body"]["stages"]["brief_builder"]["status"] == "committed_completed_frozen"
    assert interrupted_state["body"]["stages"]["brief_builder"]["provider_calls"] == 1
    assert interrupted_state["body"]["stages"]["author"]["status"] == "not_called"
    assert interrupted_state["body"]["resume_allowed"] is False
    assert brief.execute_calls == 1


def test_freeze_historical_uncommitted_brief_run_without_resuming(tmp_path: Path) -> None:
    run_id = "v32-text-only-freeze"
    paths, policy, runtime, environ = _prepared_runtime(tmp_path, run_id=run_id)
    brief = _FakeAdapter("anthropic", "claude-opus-5")
    interrupted = {
        ("anthropic", "claude-opus-5"): brief,
        ("deepseek", "deepseek-v4-pro"): _InterruptBeforeAuthorAdapter("deepseek", "deepseek-v4-pro"),
        ("openai", "gpt-5.6-sol"): _FakeAdapter("openai", "gpt-5.6-sol"),
    }
    run_no_network_gate(
        paths,
        run_id=run_id,
        routing_policy_path=policy,
        runtime_config_path=runtime["runtime_path"],
        environ=environ,
        adapters=interrupted,
    )
    interrupted["deepseek", "deepseek-v4-pro"].arm()
    with pytest.raises(KeyboardInterrupt):
        execute_text_only_pilot(
            paths,
            run_id=run_id,
            routing_policy_path=policy,
            runtime_config_path=runtime["runtime_path"],
            environ=environ,
            adapters=interrupted,
        )

    # Simulate the original defect: private output/ledger exist, but the
    # control plane still claims an uncommitted running brief stage.
    state_path = paths.private / "text-only-agent-pilots" / run_id / "state.json"
    state = read_json(state_path)
    body = dict(state["body"])
    body["status"] = "ready_for_live_execution"
    body["stages"] = {
        "brief_builder": {"status": "running", "attempts": 1},
        "author": {"status": "not_started", "attempts": 0},
        "truth_builder": {"status": "not_started", "attempts": 0},
        "verifier": {"status": "not_started", "attempts": 0},
    }
    atomic_write_json(
        state_path,
        _envelope("qagen_v32_text_only_execution_state", f"{run_id}-state", "live_running", body),
        private=True,
    )

    frozen = freeze_incomplete_text_only_pilot(paths, run_id=run_id)
    assert frozen["status"] == "incomplete_frozen"
    assert frozen["resume_allowed"] is False
    final_state = read_json(state_path)
    summary = read_json(Path(frozen["failure_summary_path"]))
    assert final_state["status"] == final_state["body"]["status"] == "incomplete_frozen"
    assert final_state["body"]["stages"]["author"]["provider_calls"] == 0
    assert summary["body"]["stages"]["brief_builder"]["brief_state_committed"] is False
    assert brief.execute_calls == 1


def test_interrupt_before_verifier_is_automatically_frozen_without_relabeling_calls(tmp_path: Path) -> None:
    run_id = "v32-text-only-committed-freeze"
    paths, policy, runtime, environ = _prepared_runtime(tmp_path, run_id=run_id)
    brief = _FakeAdapter("anthropic", "claude-opus-5")
    author = _FakeAdapter("deepseek", "deepseek-v4-pro")
    interrupted_verifier = _InterruptBeforeVerifierAdapter("openai", "gpt-5.6-sol")
    adapters = {
        ("anthropic", "claude-opus-5"): brief,
        ("deepseek", "deepseek-v4-pro"): author,
        ("openai", "gpt-5.6-sol"): interrupted_verifier,
    }
    run_no_network_gate(
        paths,
        run_id=run_id,
        routing_policy_path=policy,
        runtime_config_path=runtime["runtime_path"],
        environ=environ,
        adapters=adapters,
    )
    interrupted_verifier.arm()
    with pytest.raises(KeyboardInterrupt, match="before verifier"):
        execute_text_only_pilot(
            paths,
            run_id=run_id,
            routing_policy_path=policy,
            runtime_config_path=runtime["runtime_path"],
            environ=environ,
            adapters=adapters,
        )

    state = read_json(paths.private / "text-only-agent-pilots" / run_id / "state.json")
    summary = read_json(paths.private / "text-only-agent-pilots" / run_id / "failure-summary.json")
    assert state["status"] == state["body"]["status"] == "incomplete_frozen"
    assert summary["body"]["stages"]["brief_builder"]["provider_calls"] == 1
    assert summary["body"]["stages"]["author"]["provider_calls"] == 1
    assert summary["body"]["stages"]["truth_builder"]["provider_calls"] == 0
    assert summary["body"]["stages"]["verifier"]["provider_calls"] == 0
    assert brief.execute_calls == 1
    assert author.execute_calls == 1
    assert interrupted_verifier.execute_calls == 0


def test_post_response_pre_commit_abort_is_frozen_with_recoverable_usage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_id = "v32-text-only-post-response-abort"
    paths, policy, runtime, environ = _prepared_runtime(tmp_path, run_id=run_id)
    brief = _FakeAdapter("anthropic", "claude-opus-5")
    author = _FakeAdapter("deepseek", "deepseek-v4-pro")
    verifier = _FakeAdapter("openai", "gpt-5.6-sol")
    adapters = {
        ("anthropic", "claude-opus-5"): brief,
        ("deepseek", "deepseek-v4-pro"): author,
        ("openai", "gpt-5.6-sol"): verifier,
    }
    run_no_network_gate(
        paths,
        run_id=run_id,
        routing_policy_path=policy,
        runtime_config_path=runtime["runtime_path"],
        environ=environ,
        adapters=adapters,
    )

    def abort_after_author_output(role: str, _result: WorkerResult) -> None:
        if role == "author":
            raise SystemExit("test-only post-response pre-commit abort")

    monkeypatch.setattr(text_only_pilot, "_after_worker_output_persisted_before_state_commit", abort_after_author_output)
    with pytest.raises(SystemExit, match="post-response"):
        execute_text_only_pilot(
            paths,
            run_id=run_id,
            routing_policy_path=policy,
            runtime_config_path=runtime["runtime_path"],
            environ=environ,
            adapters=adapters,
        )

    root = paths.private / "text-only-agent-pilots" / run_id
    state = read_json(root / "state.json")
    summary = read_json(root / "failure-summary.json")
    author_stage = summary["body"]["stages"]["author"]
    assert state["status"] == state["body"]["status"] == "incomplete_frozen"
    assert author_stage["status"] == "response_received_uncommitted_frozen"
    assert author_stage["provider_calls"] == 1
    assert author_stage["usage_settlement"]["cost_accounting"] == "provider_reported"
    assert author_stage["output"]["path"].endswith("outputs/author.json")
    assert summary["body"]["usage_unsettled_roles"] == []
    assert brief.execute_calls == author.execute_calls == 1
    assert verifier.execute_calls == 0


def test_cli_subprocess_freezes_post_response_author_abort(tmp_path: Path) -> None:
    """Exercise the real CLI process exit path with a non-network fake route."""

    run_id = "v32-cli-post-response-abort"
    paths, _policy, runtime, _environ = _prepared_runtime(tmp_path, run_id=run_id)
    injection_root = tmp_path / "subprocess-injection"
    injection_root.mkdir()
    injection_root.joinpath("sitecustomize.py").write_text(
        """
from qagen.batch import text_only_pilot as pilot
from qagen.contracts import digest
from qagen.workers import WorkerResult

class _Budget:
    pass

class _Adapter:
    fixture_only = False
    def __init__(self, provider, model):
        self.provider = provider
        self.model = model
    def prompt_budget(self, _request):
        return _Budget()
    def with_prompt_reservation(self, request):
        return request
    def execute(self, request):
        value = dict(request.input_contract)
        if request.role == 'brief_builder':
            output = value['public_child_target']
        elif request.role == 'author':
            output = {
                'artifact_kind': 'v32_question_draft',
                'scientific_context': 'Examine the local molecular conformation.',
                'object_reference': 'the selected molecular backbone segment',
                'question_wording': 'Determine the torsional relationship represented by the selected atom sequence.',
                'reasoning_focus': 'Relate the observed conformation to local molecular geometry.',
                'state_transition': None,
            }
        else:
            bindings = value['required_verification_bindings']
            handoff = value['independent_measurement_handoff']
            output = {
                'artifact_kind': 'verification_result',
                'candidate_digest': bindings['candidate_digest'],
                'evidence_digest': bindings['evidence_digest'],
                'outcome': 'pass',
                'checks': [{'name': 'measurement_replay_text_only', 'outcome': 'pass'}],
                'rubric_scores': {
                    'authenticity': {'score': 1, 'maximum': 1},
                    'correctness': {'score': 1, 'maximum': 1},
                    'three_d_relevance': {'score': 1, 'maximum': 1},
                },
                'rubric_lock_digest': bindings['rubric_lock_digest'],
                'work_item_id': bindings['work_item_id'],
                'attested_evidence': {
                    'handoff_digest': handoff['handoff_digest'],
                    'receipt_digests': handoff['receipt_digests'],
                },
                'state_transition': None,
            }
        return WorkerResult(
            role=request.role,
            provider=request.provider,
            model=request.model,
            input_contract_digest=request.input_contract_digest,
            output_contract=output,
            output_contract_digest=digest(output),
            fixture_only=False,
            token_in=10,
            token_out=5,
            actual_cost_usd=0.01,
            cost_accounting='provider_reported',
        )

def _adapters(*_args, **_kwargs):
    return {
        ('anthropic', 'claude-opus-5'): _Adapter('anthropic', 'claude-opus-5'),
        ('deepseek', 'deepseek-v4-pro'): _Adapter('deepseek', 'deepseek-v4-pro'),
        ('openai', 'gpt-5.6-sol'): _Adapter('openai', 'gpt-5.6-sol'),
    }

def _abort(role, _result):
    if role == 'author':
        raise SystemExit('test-only CLI post-response abort')

pilot.build_provider_adapters = _adapters
pilot._after_worker_output_persisted_before_state_commit = _abort
""".strip(),
        encoding="utf-8",
    )
    script = PACKAGE_ROOT / "scripts" / "run_v32_text_only_agent_loop.py"
    command = [
        sys.executable,
        str(script),
        "--workspace",
        str(paths.root),
        "--run-id",
        run_id,
        "--execute",
    ]
    for name in (
        "selection-manifest",
        "child-target",
        "preauthor-anchor",
        "pilot-result",
        "inventory",
        "first-receipt",
        "first-sidecar",
        "first-leak-scan",
        "second-receipt",
        "second-sidecar",
        "second-leak-scan",
        "replay-consistency",
    ):
        command.extend([f"--{name}", str(tmp_path / f"unused-{name}.json")])
    environ = dict(os.environ)
    prior_python_path = environ.get("PYTHONPATH", "")
    environ["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(injection_root), str(PACKAGE_ROOT / "src"), prior_python_path) if item
    )
    environ["QAGEN_UNIV_API_KEY"] = "test-key"
    environ["QAGEN_UNIV_BASE_URL"] = "https://provider.test"
    result = subprocess.run(command, text=True, capture_output=True, env=environ, check=False)
    assert result.returncode == 1

    root = paths.private / "text-only-agent-pilots" / run_id
    state = read_json(root / "state.json")
    summary = read_json(root / "failure-summary.json")
    assert state["status"] == state["body"]["status"] == "incomplete_frozen"
    assert summary["body"]["failure_context"]["error_class"] == "SystemExit"
    assert summary["body"]["stages"]["author"]["status"] == "response_received_uncommitted_frozen"
    assert summary["body"]["stages"]["author"]["provider_calls"] == 1
    assert summary["body"]["usage_unsettled_roles"] == []


def test_question_draft_is_safely_composed_and_keeps_viewer_actions_out_of_the_prompt(tmp_path: Path) -> None:
    paths, inputs = _inputs(tmp_path)
    loaded = text_only_pilot._load_inputs(paths, inputs)
    target = loaded["target"]
    sidecar = loaded["first_sidecar"][1]["body"]
    draft = {
        "artifact_kind": "v32_question_draft",
        "scientific_context": "Examine the local molecular conformation.",
        "object_reference": "the selected molecular backbone segment",
        "question_wording": "Determine the torsional relationship represented by the selected atom sequence.",
        "reasoning_focus": "Relate the observed conformation to local molecular geometry.",
        "state_transition": None,
    }
    answer = {
        "dihedral_angle_deg": text_only_pilot._nearest_whole_degree(
            sidecar["measurement_result"]["value"]
        )
    }
    receipt = loaded["first_receipt"][1]
    full_sidecar = loaded["first_sidecar"][1]
    record = text_only_pilot._measurement_ground_truth_record(
        work_item_id="draft-composer-work-item",
        target=target,
        receipt=receipt,
        sidecar=full_sidecar,
        answer=answer,
    )
    registry_path = paths.private / "measurement" / "draft-composer-ground-truth-registry.json"
    atomic_write_json(
        registry_path,
        {
            "schema_version": 1,
            "kind": "qagen_ground_truth_registry",
            "id": "draft-composer-ground-truth-registry",
            "source_status": "approved",
            "record_count": 1,
            "approved_snapshot": {"records": [record]},
        },
        private=True,
    )
    registry = load_ground_truth_registry(registry_path)
    provenance = text_only_pilot._ground_truth_provenance(record, registry)
    candidate = text_only_pilot._compose_candidate(
        draft,
        target=target,
        run_id="draft-composer-test",
        work_item_id="draft-composer-work-item",
        plan_digest=_digest("plan"),
        config_digest=_digest("config"),
        knowledge_digest=_digest("knowledge"),
        viewer_profile_digest=_digest("viewer"),
        expected_answer=sidecar["measurement_result"]["value"],
        gt_provenance=provenance,
    )
    text_only_pilot._candidate_gate(
        candidate,
        target=target,
        run_id="draft-composer-test",
        work_item_id="draft-composer-work-item",
        plan_digest=_digest("plan"),
        config_digest=_digest("config"),
        knowledge_digest=_digest("knowledge"),
        viewer_profile_digest=_digest("viewer"),
        expected_answer=sidecar["measurement_result"]["value"],
        tolerance=sidecar["measurement_tolerance"]["absolute"],
        ground_truth_registry=registry,
    )
    assert "controlled 3d viewer" not in candidate["prompt"].casefold()
    assert "measure_dihedral" not in candidate["prompt"]
    assert "sidebar" not in candidate["prompt"].casefold()
    assert "rotate" not in candidate["prompt"].casefold()
    assert "render" not in candidate["prompt"].casefold()
    assert "highlighted" not in candidate["prompt"].casefold()
    assert "nearest whole degree" in candidate["prompt"]
    assert candidate["viewer_session_request"]["allowed_operations"] == ["rotate", "render", "measure_dihedral"]
    assert candidate["question_contract"]["question_family_kind"] == "measurement"
    assert candidate["question_contract"]["gt_answer_contract"]["source_type"] == "measurement_receipt"
    assert candidate["rubric"]["answer_format"] == "signed_numeric_degree_nearest_whole"

    numeric_draft = dict(draft)
    numeric_draft["question_wording"] = "The value is 12 degrees."
    with pytest.raises(text_only_pilot.TextOnlyPilotError, match="numeric_answer"):
        text_only_pilot._question_draft_gate(numeric_draft)

    no_viewer_candidate = dict(candidate)
    no_viewer_candidate["prompt"] = "Describe the selected atom sequence: " + ", ".join(target["selector"]["atom_ids"])
    with pytest.raises(text_only_pilot.TextOnlyPilotError, match="candidate_prompt_does_not_state_the_scientific_measurement_question"):
        text_only_pilot._candidate_gate(
            no_viewer_candidate,
            target=target,
            run_id="draft-composer-test",
            work_item_id="draft-composer-work-item",
            plan_digest=_digest("plan"),
            config_digest=_digest("config"),
            knowledge_digest=_digest("knowledge"),
            viewer_profile_digest=_digest("viewer"),
            expected_answer=sidecar["measurement_result"]["value"],
            tolerance=sidecar["measurement_tolerance"]["absolute"],
            ground_truth_registry=registry,
        )


def test_deterministic_truth_record_rejects_candidate_or_measurement_tampering(tmp_path: Path) -> None:
    paths, inputs = _inputs(tmp_path)
    loaded = text_only_pilot._load_inputs(paths, inputs)
    target = loaded["target"]
    candidate = {"candidate": "private-test-only"}
    first_receipt = loaded["first_receipt"][1]
    first_sidecar = loaded["first_sidecar"][1]
    record = text_only_pilot._materialize_truth_record(
        candidate=candidate,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        work_item_id="deterministic-truth-test",
        purpose="author_truth",
    )
    text_only_pilot._truth_gate(
        record,
        candidate=candidate,
        target=target,
        receipt=first_receipt,
        sidecar=first_sidecar,
        work_item_id="deterministic-truth-test",
    )

    wrong_candidate = {**record, "candidate_digest": _digest("wrong-candidate")}
    with pytest.raises(text_only_pilot.TextOnlyPilotError, match="deterministic_truth_record"):
        text_only_pilot._truth_gate(
            wrong_candidate,
            candidate=candidate,
            target=target,
            receipt=first_receipt,
            sidecar=first_sidecar,
            work_item_id="deterministic-truth-test",
        )

    wrong_measurement = {
        **record,
        "measurement_result_digest": _digest("wrong-measurement"),
    }
    with pytest.raises(text_only_pilot.TextOnlyPilotError, match="deterministic_truth_record"):
        text_only_pilot._truth_gate(
            wrong_measurement,
            candidate=candidate,
            target=target,
            receipt=first_receipt,
            sidecar=first_sidecar,
            work_item_id="deterministic-truth-test",
        )


def test_source_change_blocks_a_prepared_run_before_the_no_network_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_id = "v32-text-only-source-identity"
    paths, policy, runtime, environ = _prepared_runtime(tmp_path, run_id=run_id)
    adapters = {
        ("anthropic", "claude-opus-5"): _FakeAdapter("anthropic", "claude-opus-5"),
        ("deepseek", "deepseek-v4-pro"): _FakeAdapter("deepseek", "deepseek-v4-pro"),
        ("openai", "gpt-5.6-sol"): _FakeAdapter("openai", "gpt-5.6-sol"),
    }
    monkeypatch.setattr(text_only_pilot, "hash_file", lambda _path: _digest("source-changed"))
    with pytest.raises(text_only_pilot.TextOnlyPilotError, match="executor_identity"):
        run_no_network_gate(
            paths,
            run_id=run_id,
            routing_policy_path=policy,
            runtime_config_path=runtime["runtime_path"],
            environ=environ,
            adapters=adapters,
        )
    assert sum(adapter.execute_calls for adapter in adapters.values()) == 0
