from __future__ import annotations

import base64
import json
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from qagen.batch import live
from qagen.contracts import digest
from qagen.measurement import validate_measurement_request
from qagen.paths import WorkspacePaths
from qagen.providers import ProviderAdapterError
import qagen.providers.http as provider_http
from qagen.storage.ledger import UsageBudget
from qagen.workers import WorkerRequest, WorkerResult


def _digest(label: str) -> str:
    return digest({"live-target-spec-test": label})


def _inventory_binding() -> dict[str, Any]:
    source_digest = _digest("source")
    inventory_digest = _digest("inventory")
    return {
        "inventory_digest": inventory_digest,
        "source_record_digest": source_digest,
        "anonymous_scene_ref": "scene://live-target-spec-fixture",
        "target_leaf": "chemistry.stereochemistry_chirality",
        "inventory": {
            "inventory_version": 1,
            "source_record_digest": source_digest,
            "target_leaf": "chemistry.stereochemistry_chirality",
            "scene_registry_digest": _digest("registry"),
            "selection_slot_digest": _digest("slot"),
            "anonymous_scene_ref": "scene://live-target-spec-fixture",
            "asset_set_digest": _digest("assets"),
            "asset_sha256": {"primary": _digest("primary")},
            "selector_types": ["atom", "residue"],
            "selector_catalog": {
                "components": [{"component_id": "component-0001", "chain_id": "chain-0001"}],
                "residues": [
                    {
                        "residue_id": "residue-000001",
                        "component_id": "component-0001",
                        "residue_name": "residue-000001",
                        "residue_number": 1,
                        "insertion_code": "",
                    }
                ],
                "atoms": [
                    {
                        "atom_id": "atom-00000001",
                        "residue_id": "residue-000001",
                        "atom_name": "atom-00000001",
                        "element": "C",
                        "coordinate_identity": "coordinate-00000001",
                    }
                ],
                "mask_labels": [],
                "axes": [],
                "volume_shape": [],
                "layers": [],
            },
            "backend_selector_map": {
                "component-0001": {"model_index": 1, "chain_id": "A"},
                "residue-000001": {"model_index": 1, "chain_id": "A", "residue_number": 1},
                "atom-00000001": {"model_index": 1, "atom_name": "C"},
            },
            "local_only": True,
            "release_eligible": False,
        },
    }


def _ground_truth_record() -> dict[str, Any]:
    return {
        "source_type": "expert_annotation",
        "provenance": {
            "source_id": "fixture-gt-source",
            "source_digest": _digest("gt-source"),
            "record_id": "fixture-gt-record",
            "record_digest": _digest("gt-record"),
            "answer_digest": _digest("gt-answer"),
            "ground_truth_registry_digest": _digest("gt-registry"),
            "anonymous_scene_ref": "scene://live-target-spec-fixture",
            "primary_asset_digest": _digest("primary"),
            "selection_slot_digest": _digest("slot"),
        },
        "allowed_answer_schema": {"shape": "categorical_label", "fields": ["answer"]},
    }


def _root(plan_digest: str, *, with_v32_visual_bridge: bool = False) -> dict[str, Any]:
    root = {
        "artifact_kind": "qagen_compiled_selection_live_root_input",
        "batch_plan_digest": plan_digest,
        "rubric_lock_digest": _digest("rubric"),
        "job": {
            "job_id": "job-001-target-spec",
            "target_leaf": "chemistry.stereochemistry_chirality",
            "generation_strategy": "reauthor_on_asset",
            "lock_digests": {
                "config": _digest("config"),
                "seed": _digest("seed"),
                "knowledge": _digest("knowledge"),
                "viewer": _digest("viewer"),
            },
            "viewer": {
                "anonymous_scene_ref": "scene://live-target-spec-fixture",
                "allowed_operations": ["rotate", "render"],
            },
            "ground_truth_record": _ground_truth_record(),
        },
        "provider_science_bridge": {"work_item_id": "job-001-target-spec"},
    }
    if with_v32_visual_bridge:
        views = []
        for index in range(2):
            png = b"\x89PNG\r\n\x1a\n" + f"author-view-{index}".encode("ascii")
            views.append(
                {
                    "operation": "render" if index == 0 else "rotate",
                    "receipt_digest": _digest(f"view-receipt-{index}"),
                    "render_digest": _digest(f"view-render-{index}"),
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                }
            )
        root["provider_science_bridge"] = {
            "artifact_kind": "approved_local_provider_science_bridge",
            "schema_version": 1,
            "work_item_id": "job-001-target-spec",
            "viewer_evidence": {
                "anonymous_scene_ref": "scene://live-target-spec-fixture",
                "viewer_profile_digest": _digest("viewer"),
                "handoff_digest": _digest("handoff"),
                "receipt_digests": [view["receipt_digest"] for view in views],
                "rendered_views": views,
            },
        }
    return root


def _brief(binding: dict[str, Any], plan_digest: str) -> WorkerResult:
    output = {
        "target_spec_version": 1,
        "source_record_digest": binding["source_record_digest"],
        "scene_inventory_digest": binding["inventory_digest"],
        "anonymous_scene_ref": binding["anonymous_scene_ref"],
        "candidate_plan_digest": plan_digest,
        "target_leaf": binding["target_leaf"],
        "allowed_native_operations": ["inspect"],
        "native_operation": "inspect",
        "selector": {"type": "atom", "atom_id": "atom-00000001"},
        "answer_fields": ["answer"],
    }
    return WorkerResult(
        role="brief_builder",
        provider="anthropic",
        model="claude-opus-5",
        input_contract_digest=_digest("brief-input"),
        output_contract=output,
        output_contract_digest=digest(output),
    )


def _candidate(target_spec_digest: str) -> WorkerResult:
    output = {"target_spec_digest": target_spec_digest}
    return WorkerResult(
        role="author",
        provider="deepseek",
        model="deepseek-v4-pro",
        input_contract_digest=_digest("author-input"),
        output_contract=output,
        output_contract_digest=digest(output),
    )


def test_live_inventory_uses_only_the_locked_batch_receipt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    binding = _inventory_binding()
    batch_digest = _digest("approved-inventory-batch")
    batch_receipt = {
        "content_digest": batch_digest,
        "batch_receipt": {
            "path": "measurement/inventory-batches/batch-fixture.json",
            "content_digest": batch_digest,
        },
    }
    job = {
        "slot_digest": _digest("slot"),
        "target_leaf": binding["target_leaf"],
        "lock_bindings": {"viewer": {"anonymous_scene_ref": binding["anonymous_scene_ref"]}},
    }
    batch_result = {
        "source_record_digest": binding["source_record_digest"],
        "selection_slot_digest": job["slot_digest"],
        "anonymous_scene_ref": binding["anonymous_scene_ref"],
        "status": "ready",
        "inventory_digest": binding["inventory_digest"],
    }
    calls: list[tuple[str, str]] = []

    def load_batch(_paths: WorkspacePaths, receipt_path: str, *, expected_content_digest: str) -> dict[str, Any]:
        calls.append((receipt_path, expected_content_digest))
        return {"body": {"results": [batch_result]}}

    monkeypatch.setattr(live, "load_v32_scene_inventory_batch", load_batch)
    monkeypatch.setattr(
        live,
        "load_scene_inventory_by_digest",
        lambda _paths, content_digest: {"content_digest": content_digest, "body": binding["inventory"]},
    )

    resolved = live._live_scene_inventory(paths, job=job, inventory_batch=batch_receipt)

    assert resolved["inventory_digest"] == binding["inventory_digest"]
    assert calls == [("measurement/inventory-batches/batch-fixture.json", batch_digest)]

    batch_result["anonymous_scene_ref"] = "scene://wrong"
    with pytest.raises(live.LiveBatchError, match="no exact current slot binding"):
        live._live_scene_inventory(paths, job=job, inventory_batch=batch_receipt)


def test_author_input_receives_only_approved_target_spec_not_inventory() -> None:
    binding = _inventory_binding()
    plan_digest = _digest("plan")
    brief = _brief(binding, plan_digest)

    payload = live._stage_input(
        role="author",
        run_id="run-target-spec",
        job_id="job-001-target-spec",
        root_input=_root(plan_digest),
        dependencies={"brief_builder": brief},
        inventory_binding=binding,
    )

    assert payload["artifact_kind"] == "qagen_author_target_spec_input"
    assert payload["target_spec_digest"] == brief.output_contract_digest
    assert payload["target_spec"] == brief.output_contract
    rendered = json.dumps(payload, sort_keys=True)
    assert "selector_catalog" not in rendered
    assert "selector_index" not in rendered
    assert "backend_selector_map" not in rendered


def test_author_uses_a_minimal_transient_visual_bridge_without_inventory_or_durable_data_url() -> None:
    binding = _inventory_binding()
    plan_digest = _digest("plan")
    brief = _brief(binding, plan_digest)

    payload = live._stage_input(
        role="author",
        run_id="run-target-spec",
        job_id="job-001-target-spec",
        root_input=_root(plan_digest, with_v32_visual_bridge=True),
        dependencies={"brief_builder": brief},
        inventory_binding=binding,
    )

    assert set(payload) == {
        "artifact_kind",
        "stage",
        "run_id",
        "work_item_id",
        "target_spec_digest",
        "target_spec",
        "author_context",
        "visual_bridge",
    }
    assert set(payload["author_context"]) == {
        "batch_plan_digest",
        "rubric_lock_digest",
        "config_digest",
        "knowledge_snapshot_digest",
        "viewer_lock_digest",
        "anonymous_scene_ref",
        "allowed_operations",
        "ground_truth_record",
    }
    rendered = json.dumps(payload, sort_keys=True)
    for forbidden in ("provider_science_bridge", "selector_catalog", "selector_index", "backend_selector_map", "asset_path"):
        assert forbidden not in rendered

    attachments = provider_http._provider_visual_inputs("author", payload)
    assert len(attachments) == 2
    prompt_input = provider_http._prompt_input("author", payload)
    prompt_rendered = json.dumps(prompt_input, sort_keys=True)
    assert "data_url" not in prompt_rendered
    assert "multimodal_attachment" in prompt_rendered


def test_truth_and_evidence_inputs_bind_the_same_measurement_request() -> None:
    binding = _inventory_binding()
    plan_digest = _digest("plan")
    brief = _brief(binding, plan_digest)
    candidate = _candidate(brief.output_contract_digest)
    root = _root(plan_digest)

    truth_input = live._stage_input(
        role="truth_builder",
        run_id="run-target-spec",
        job_id="job-001-target-spec",
        root_input=root,
        dependencies={"brief_builder": brief, "author": candidate},
        inventory_binding=binding,
    )
    evidence_input = live._stage_input(
        role="evidence_worker",
        run_id="run-target-spec",
        job_id="job-001-target-spec",
        root_input=root,
        dependencies={"brief_builder": brief, "author": candidate},
        inventory_binding=binding,
    )

    assert truth_input["measurement_request"] == evidence_input["measurement_request"]
    assert truth_input["measurement_request_digest"] == evidence_input["measurement_request_digest"]
    assert truth_input["measurement_request"]["candidate_digest"] == candidate.output_contract_digest
    validate_measurement_request(truth_input["measurement_request"], target_spec=brief.output_contract)
    rendered = json.dumps({"truth": truth_input, "evidence": evidence_input}, sort_keys=True)
    assert "selector_catalog" not in rendered
    assert "backend_selector_map" not in rendered


def test_stage_success_mutates_the_persisted_job_and_uses_attempt_unique_output(tmp_path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    references = {role: {"role": role, "status": "planned"} for role in live._ALL_STAGES}
    job = {
        "job_id": "job-stage-persistence",
        "stages": {role: live._stage_template(role, reference) for role, reference in references.items()},
        "artifacts": {"candidate": None, "evidence": None, "verification": None, "revision": None},
    }
    stage = job["stages"]["brief_builder"]
    stage["status"] = "running"
    stage["attempts"] = 1
    output = {"target_spec": "fixture"}
    result = WorkerResult(
        role="brief_builder",
        provider="anthropic",
        model="claude-opus-5",
        input_contract_digest=_digest("stage-input"),
        output_contract=output,
        output_contract_digest=digest(output),
        token_in=3,
        token_out=2,
        actual_cost_usd=0.1,
        cost_accounting="provider_reported",
    )

    live._stage_success(paths, run_id="run-stage-persistence", job=job, role="brief_builder", result=result)

    assert job["stages"]["brief_builder"]["status"] == "completed"
    assert job["stages"]["brief_builder"]["attempts"] == 1
    assert job["stages"]["brief_builder"]["output"]["content_digest"] == result.output_contract_digest
    output_path = paths.root / job["stages"]["brief_builder"]["output"]["path"]
    assert output_path.is_file()
    assert output_path.parent.name == "attempts"
    assert live._next_stage(job, revision_receipt=None) == "author"

    first = live._stage_output_path(
        paths, run_id="run-stage-persistence", job_id="job-stage-persistence", role="brief_builder", attempt=2
    )
    second = live._stage_output_path(
        paths, run_id="run-stage-persistence", job_id="job-stage-persistence", role="brief_builder", attempt=2
    )
    assert first != second


def test_stage_failure_persists_only_a_sanitized_provider_error_receipt(tmp_path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    references = {role: {"role": role, "status": "planned"} for role in live._ALL_STAGES}
    job = {
        "job_id": "job-stage-failure",
        "status": "queued",
        "failure": None,
        "stages": {role: live._stage_template(role, reference) for role, reference in references.items()},
        "artifacts": {"candidate": None, "evidence": None, "verification": None, "revision": None},
    }
    job["stages"]["author"]["attempts"] = 1
    error = ProviderAdapterError(
        "provider returned non-retryable HTTP status 400",
        network_called=True,
        error_receipt={
            "http_status": 400,
            "provider_error_type": "invalid_request",
            "provider_error_code": "unsupported_parameter",
            "field": "thinking",
            "message": "unsupported_thinking_option",
        },
    )

    live._stage_failure(
        paths,
        run_id="run-stage-failure",
        job=job,
        role="author",
        error=error,
        max_retries=0,
    )

    expected = error.error_receipt
    assert job["status"] == "failed_final"
    assert job["failure"]["provider_error"] == expected
    event = json.loads(paths.events.read_text(encoding="utf-8").splitlines()[-1])
    assert event["body"]["payload"]["provider_error"] == expected


def test_provider_transport_event_sets_network_called_before_and_after_failure(tmp_path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    body = {
        "runtime": {
            "status": "ready",
            "network_called": False,
            "provider_requests_sent": 0,
        }
    }
    sent = {
        "event": "request_sent",
        "provider": "anthropic",
        "model": "claude-opus-5",
        "role": "brief_builder",
        "run_id": "run-transport-event",
        "work_item_id": "job-transport-event",
        "attempt": 1,
    }
    live._record_provider_transport_event(paths, run_id="run-transport-event", state_body=body, payload=sent)
    assert body["runtime"]["network_called"] is True
    assert body["runtime"]["provider_requests_sent"] == 1

    failed = {**sent, "event": "transport_failed", "error_class": "ConnectTimeout", "latency_ms": 12.0}
    live._record_provider_transport_event(paths, run_id="run-transport-event", state_body=body, payload=failed)
    assert body["runtime"]["network_called"] is True
    records = [json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()]
    assert [record["body"]["payload"]["transport"]["event"] for record in records] == [
        "request_sent",
        "transport_failed",
    ]


def test_brief_inventory_projection_is_bounded_deterministic_and_hides_private_catalog_fields() -> None:
    binding = _inventory_binding()
    catalog = binding["inventory"]["selector_catalog"]
    component_count = 1001
    residue_count = 1003
    atom_count = 1007
    catalog["components"] = [
        {"component_id": f"component-{index:05d}", "chain_id": f"chain-{index:05d}", "asset_path": "/private/no"}
        for index in range(component_count)
    ]
    catalog["residues"] = [
        {
            "residue_id": f"residue-{index:06d}",
            "component_id": f"component-{index % component_count:05d}",
            "residue_number": index,
            "insertion_code": "",
            "coordinate_identity": f"private-{index}",
        }
        for index in range(residue_count)
    ]
    catalog["atoms"] = [
        {
            "atom_id": f"atom-{index:08d}",
            "residue_id": f"residue-{index % residue_count:06d}",
            "element": "C",
            "backend_selector_map": {"private": index},
        }
        for index in range(atom_count)
    ]

    started = perf_counter()
    first = live._brief_inventory_projection(binding)
    second = live._brief_inventory_projection(binding)
    elapsed = perf_counter() - started
    rendered = json.dumps(first, sort_keys=True, separators=(",", ":"))

    assert elapsed < 5.0
    assert first == second
    assert len(rendered.encode("utf-8")) <= live._BRIEF_PROJECTION_MAX_BYTES
    assert first["selector_summary"]["counts"] == {
        "components": component_count,
        "residues": residue_count,
        "atoms": atom_count,
        "mask_labels": 0,
        "axes": 0,
        "volume_shape": 0,
        "layers": 0,
    }
    candidates = first["selector_summary"]["candidates"]
    assert all(len(candidates[name]) == live._BRIEF_SELECTOR_CANDIDATE_LIMIT for name in candidates)
    assert {item["component_id"] for item in candidates["components"]}.issubset(
        {item["component_id"] for item in catalog["components"]}
    )
    assert {item["residue_id"] for item in candidates["residues"]}.issubset(
        {item["residue_id"] for item in catalog["residues"]}
    )
    assert {item["atom_id"] for item in candidates["atoms"]}.issubset(
        {item["atom_id"] for item in catalog["atoms"]}
    )
    for forbidden in (
        "selector_catalog",
        "selector_index",
        "backend_selector_map",
        "coordinate_identity",
        "asset_path",
        "asset_sha256",
    ):
        assert forbidden not in rendered


def test_brief_inventory_projection_fails_closed_when_bounded_sample_exceeds_byte_cap() -> None:
    binding = _inventory_binding()
    catalog = binding["inventory"]["selector_catalog"]
    oversized_id = "x" * live._BRIEF_PROJECTION_MAX_BYTES
    catalog["components"] = [{"component_id": oversized_id, "chain_id": "chain-0001"}]

    with pytest.raises(live.LiveBatchError, match="exceeds its byte cap"):
        live._brief_inventory_projection(binding)


def test_five_slot_runtime_preflight_builds_real_brief_prompts_without_http(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    binding = _inventory_binding()
    calls: list[httpx.Request] = []

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise AssertionError("no-network runtime preflight must not send provider HTTP")

    adapter = provider_http.HTTPProviderAdapter(
        contract=provider_http.PROVIDER_CONTRACTS["anthropic"],
        base_url="https://provider-runtime.test",
        api_key="not-used-by-preflight",
        role_schemas={"brief_builder": {"type": "object"}},
        supports_image_input=True,
        supports_data_url=True,
        timeout_seconds=1.0,
        max_attempts=1,
        initial_backoff_seconds=0.0,
        client=httpx.Client(transport=httpx.MockTransport(fail_if_called)),
    )
    jobs = [{"job_id": f"job-preflight-{index:03d}"} for index in range(5)]
    plan_jobs = {job["job_id"]: dict(job) for job in jobs}
    plan_digest = _digest("five-slot-plan")
    plan = {
        "content_digest": plan_digest,
        "body": {
            "selection_batch": {"content_digest": _digest("selection")},
            "inventory_batch": {},
        },
    }
    route = {
        "provider": "anthropic",
        "allowed_models": ["claude-opus-5"],
        "max_tokens": 9500,
        "max_cost_usd": 0.5,
    }
    policy = SimpleNamespace(role=lambda _role: route)
    budget = UsageBudget.from_value(
        {
            "total_max_tokens": 50_000,
            "total_max_cost_usd": 5.0,
            "roles": {"brief_builder": {"max_tokens": 47_500, "max_cost_usd": 2.5}},
        }
    )

    monkeypatch.setattr(
        live,
        "prepare_compiled_selection_science_bridge",
        lambda _paths, **kwargs: ({"work_item_id": kwargs["job"]["job_id"]}, {"handoff": "fixture"}),
    )
    monkeypatch.setattr(live, "_live_scene_inventory", lambda _paths, **_kwargs: binding)

    def root_input(*, job: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        root = _root(plan_digest)
        root["job"]["job_id"] = job["job_id"]
        root["provider_science_bridge"]["work_item_id"] = job["job_id"]
        return root

    monkeypatch.setattr(live, "_root_input", root_input)

    prepared, receipts = live._prepare_initial_live_jobs(
        paths,
        run_id="run-five-slot-preflight",
        plan=plan,
        plan_jobs=plan_jobs,
        jobs=jobs,
        adapters={("anthropic", "claude-opus-5"): adapter},
        policy=policy,
        budget=budget,
    )

    assert calls == []
    assert set(prepared) == set(plan_jobs)
    assert len(receipts) == 5
    assert all(receipt["role"] == "brief_builder" for receipt in receipts)
    assert all(0 < receipt["dynamic_total_reservation"] <= route["max_tokens"] for receipt in receipts)
    assert all(receipt["output_capacity"] >= 1024 for receipt in receipts)
    assert all(
        isinstance(prepared[job_id]["brief_request"], WorkerRequest)
        and prepared[job_id]["brief_request"].max_tokens == receipt["dynamic_total_reservation"]
        for job_id, receipt in zip(sorted(prepared), receipts, strict=True)
    )
