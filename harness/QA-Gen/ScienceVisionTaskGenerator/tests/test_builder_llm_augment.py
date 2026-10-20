"""Tests for optional LLM augmentation in BuilderStage.

All tests use a FakeClient — no network calls.

Fixtures:
- _make_asset()   : minimal asset dict
- _make_plan_item(): minimal plan item dict (status=ready)
- _make_config()  : config dict with llm section enabled
- _make_log()     : UsageLog backed by a tmp dir

Pattern for a builder WITH LLM enabled:
    builder = BuilderStage(config, llm_client=client, usage_log=log)
    tasks = builder.build_tasks(plan, catalog)
"""
from __future__ import annotations

import copy
import json

import pytest

from sciencevision_taskgen.asset_facts import render_instruction
from sciencevision_taskgen.llm import ChatClientError, ChatResult, UsageLog
from sciencevision_taskgen.pipeline.builder_stage import BuilderStage
from sciencevision_taskgen.templates import TEMPLATES


# ---------------------------------------------------------------------------
# FakeClient — no network
# ---------------------------------------------------------------------------


class FakeClient:
    """Fake ChatClient that returns pre-canned responses or raises exceptions."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def complete_with_retry(
        self, model: str, system: str, user: str, **kw
    ) -> ChatResult:
        self.calls.append((model, system, user))
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatResult(
            content=r,
            latency_ms=12.3,
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=0,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asset(asset_id: str = "bio-001") -> dict:
    return {
        "asset_id": asset_id,
        "name": "4HHB",
        "path": "datasets/biology/4HHB.pdb",
        "repo_path": "datasets/biology/4HHB.pdb",
        "viewer": "molstar",
        "format": "pdb",
        "thumb": "thumbs/4HHB.webp",
        "exists_on_disk": True,
    }


def _make_plan_item(domain: str = "biology", v_class: str = "V1") -> dict:
    return {
        "domain": domain,
        "v_class": v_class,
        "replica": 0,
        "status": "ready",
        "asset_count": 1,
    }


def _make_config(
    enabled: bool = True,
    default_model: str = "claude-sonnet-4-6",
    temperature: float = 0.2,
    model_by_v_class: dict | None = None,
) -> dict:
    llm: dict = {
        "enabled": enabled,
        "default_model": default_model,
        "temperature": temperature,
    }
    if model_by_v_class:
        llm["model_by_v_class"] = model_by_v_class
    return {"llm": llm, "validation": {"max_validation_iterations": 4}}


def _make_log(tmp_path, models: list[str] | None = None) -> UsageLog:
    pricing: dict = {}
    for m in (models or ["claude-sonnet-4-6", "gemini-3.1-pro-preview"]):
        pricing[m] = {"in": 3.0, "out": 15.0}
    return UsageLog.for_run(tmp_path / "run", pricing)


def _build_one(
    client: FakeClient,
    log: UsageLog,
    config: dict,
    domain: str = "biology",
    v_class: str = "V1",
) -> dict:
    """Build a single task with LLM enabled and return it."""
    builder = BuilderStage(config, llm_client=client, usage_log=log)
    plan = [_make_plan_item(domain, v_class)]
    catalog = {"domains": {domain: [_make_asset()]}}
    tasks = builder.build_tasks(plan, catalog)
    assert len(tasks) == 1
    return tasks[0]


# ---------------------------------------------------------------------------
# 1. LLM disabled — byte-identical output
# ---------------------------------------------------------------------------


class TestLLMDisabled:
    def test_llm_disabled_produces_unchanged_output(self) -> None:
        """No llm.enabled flag → no provenance.llm_meta, instruction == template."""
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        plan = [_make_plan_item(domain, v_class)]
        catalog = {"domains": {domain: [_make_asset()]}}

        # No LLM: default constructor (Phase 1 backward-compat)
        builder_no_llm = BuilderStage({})
        tasks_no_llm = builder_no_llm.build_tasks(plan, catalog)
        assert len(tasks_no_llm) == 1
        task = tasks_no_llm[0]

        # instruction matches template
        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        assert task["scientific_action"] == template.scientific_action
        # No llm_meta in provenance
        assert "llm_meta" not in task["provenance"]

    def test_llm_disabled_via_config_flag(self) -> None:
        """llm.enabled=False with client+log still produces no augmentation."""
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        plan = [_make_plan_item(domain, v_class)]
        catalog = {"domains": {domain: [_make_asset()]}}
        config = _make_config(enabled=False)

        # Even though client is present, llm.enabled=False disables augmentation
        client = FakeClient([])
        builder = BuilderStage(config, llm_client=client, usage_log=None)
        tasks = builder.build_tasks(plan, catalog)
        assert len(tasks) == 1
        task = tasks[0]
        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        assert "llm_meta" not in task["provenance"]
        assert client.calls == []  # no LLM calls


# ---------------------------------------------------------------------------
# 2. LLM augment success
# ---------------------------------------------------------------------------


class TestLLMAugmentSuccess:
    def test_augment_success_replaces_instruction_and_action(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "Measure the RMSD of 4HHB chain A in Molstar.",
            "scientific_action": "Load 4HHB, align chain A, measure RMSD.",
            "asset_specific_rationale": "4HHB is a classic haemoglobin structure.",
        })
        client = FakeClient([response])
        task = _build_one(client, log, config)

        assert task["instruction"] == "Measure the RMSD of 4HHB chain A in Molstar."
        assert task["scientific_action"] == "Load 4HHB, align chain A, measure RMSD."

    def test_augment_success_llm_meta_fallback_false(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "Identify binding residues.",
            "scientific_action": "Inspect 4HHB binding site.",
        })
        client = FakeClient([response])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is False

    def test_augment_success_llm_meta_model(self, tmp_path) -> None:
        config = _make_config(default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "Check secondary structure.",
            "scientific_action": "Analyse helix content.",
        })
        client = FakeClient([response])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["model"] == "claude-sonnet-4-6"

    def test_augment_success_rationale_stored(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "Measure RMSD.",
            "scientific_action": "Align structures.",
            "asset_specific_rationale": "4HHB has two chains.",
        })
        client = FakeClient([response])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta.get("asset_specific_rationale") == "4HHB has two chains."

    def test_augment_success_usage_row_recorded(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "Some instruction.",
            "scientific_action": "Some action.",
        })
        client = FakeClient([response])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        plan = [_make_plan_item()]
        catalog = {"domains": {"biology": [_make_asset()]}}
        builder.build_tasks(plan, catalog)

        # UsageLog is immutable — record() returns a new instance; retrieve via builder
        records = builder._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is False
        assert records[0].prompt_tokens == 100
        assert records[0].completion_tokens == 50
        assert records[0].latency_ms == pytest.approx(12.3)


# ---------------------------------------------------------------------------
# 3. Fallback on ChatClientError
# ---------------------------------------------------------------------------


class TestFallbackOnChatClientError:
    def test_task_unchanged_on_client_error(self, tmp_path) -> None:
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("network fail")])
        task = _build_one(client, log, config, domain, v_class)

        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        assert task["scientific_action"] == template.scientific_action

    def test_llm_meta_fallback_true_on_client_error(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("network fail")])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_no_exception_propagated_on_client_error(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("boom")])
        # Must not raise
        tasks = BuilderStage(config, llm_client=client, usage_log=log).build_tasks(
            [_make_plan_item()],
            {"domains": {"biology": [_make_asset()]}},
        )
        assert len(tasks) == 1

    def test_usage_row_fallback_true_on_client_error(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("err")])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})
        records = builder._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True


# ---------------------------------------------------------------------------
# 4. Fallback on invalid JSON
# ---------------------------------------------------------------------------


class TestFallbackOnInvalidJson:
    def test_fallback_on_invalid_json(self, tmp_path) -> None:
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["not json"])
        task = _build_one(client, log, config, domain, v_class)

        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_usage_recorded_on_invalid_json(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["not json"])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})
        records = builder._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True
        assert records[0].prompt_tokens == 0


# ---------------------------------------------------------------------------
# 5. Fallback on missing required keys
# ---------------------------------------------------------------------------


class TestFallbackOnMissingKeys:
    def test_fallback_on_missing_instruction_and_action(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(['{"only_rationale": "x"}'])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_fallback_on_missing_scientific_action_only(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(['{"instruction": "some instruction"}'])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True


# ---------------------------------------------------------------------------
# 6. Fallback on empty strings
# ---------------------------------------------------------------------------


class TestFallbackOnEmptyStrings:
    def test_fallback_on_empty_instruction(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(['{"instruction": "", "scientific_action": "something"}'])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_fallback_on_empty_scientific_action(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(['{"instruction": "something", "scientific_action": ""}'])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_fallback_on_both_empty(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(['{"instruction": "", "scientific_action": ""}'])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True


# ---------------------------------------------------------------------------
# 7. Routing by v_class
# ---------------------------------------------------------------------------


class TestRoutesByVClass:
    def test_routes_v1_to_mapped_model(self, tmp_path) -> None:
        config = _make_config(
            model_by_v_class={"V1": "gemini-3.1-pro-preview"},
        )
        log = _make_log(tmp_path, models=["claude-sonnet-4-6", "gemini-3.1-pro-preview"])
        response = json.dumps({
            "instruction": "Measure something.",
            "scientific_action": "Do science.",
        })
        client = FakeClient([response])
        _build_one(client, log, config, "biology", "V1")

        assert len(client.calls) == 1
        assert client.calls[0][0] == "gemini-3.1-pro-preview"

    def test_falls_back_to_default_model_when_v_class_not_mapped(self, tmp_path) -> None:
        config = _make_config(
            default_model="claude-sonnet-4-6",
            model_by_v_class={"V3": "gemini-3.1-pro-preview"},
        )
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "Measure visibility.",
            "scientific_action": "Rate visibility.",
        })
        client = FakeClient([response])
        _build_one(client, log, config, "biology", "V1")  # V1 not in map

        assert client.calls[0][0] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 8. Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_original_plan_and_asset_unchanged_after_augment(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        response = json.dumps({
            "instruction": "New instruction.",
            "scientific_action": "New action.",
        })
        client = FakeClient([response])

        domain, v_class = "biology", "V1"
        asset = _make_asset()
        plan_item = _make_plan_item(domain, v_class)
        original_asset = copy.deepcopy(asset)
        original_plan_item = copy.deepcopy(plan_item)

        plan = [plan_item]
        catalog = {"domains": {domain: [asset]}}
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        tasks = builder.build_tasks(plan, catalog)

        # Mutate returned task deeply
        tasks[0]["instruction"] = "MUTATED"
        tasks[0]["provenance"]["llm_meta"]["fallback"] = "MUTATED"

        # Original inputs unchanged
        assert asset == original_asset
        assert plan_item == original_plan_item

    def test_successive_tasks_have_independent_provenance(self, tmp_path) -> None:
        """Two calls → two independent provenance dicts (no shared reference)."""
        config = _make_config()
        log = _make_log(tmp_path)
        r1 = json.dumps({"instruction": "Instr A.", "scientific_action": "Action A."})
        r2 = json.dumps({"instruction": "Instr B.", "scientific_action": "Action B."})
        client = FakeClient([r1, r2])

        domain, v_class = "biology", "V1"
        plan = [_make_plan_item(domain, v_class), _make_plan_item(domain, v_class)]
        plan[1]["replica"] = 1
        catalog = {"domains": {domain: [_make_asset("bio-001"), _make_asset("bio-002")]}}
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        tasks = builder.build_tasks(plan, catalog)
        assert len(tasks) == 2
        assert tasks[0]["provenance"] is not tasks[1]["provenance"]


# ---------------------------------------------------------------------------
# 9. Usage recorded even on failure
# ---------------------------------------------------------------------------


class TestUsageRecordedOnFailure:
    def test_exactly_one_usage_row_on_exception(self, tmp_path) -> None:
        config = _make_config(default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("boom")])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})

        # UsageLog is immutable — record() returns new instance; retrieve via builder
        records = builder._usage_log.all_records()
        assert len(records) == 1

    def test_usage_row_model_is_routed_model_on_failure(self, tmp_path) -> None:
        config = _make_config(default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("err")])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})

        records = builder._usage_log.all_records()
        assert records[0].model == "claude-sonnet-4-6"

    def test_usage_row_fallback_true_tokens_zero_latency_zero(self, tmp_path) -> None:
        config = _make_config(default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("fail")])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})

        rec = builder._usage_log.all_records()[0]
        assert rec.fallback is True
        assert rec.prompt_tokens == 0
        assert rec.completion_tokens == 0
        assert rec.reasoning_tokens == 0
        assert rec.latency_ms == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 10. Fallback on routing error (resolve_model raises ValueError)
# ---------------------------------------------------------------------------


class TestFallbackOnRoutingError:
    """resolve_model raises ValueError when routing config is missing/malformed."""

    def _make_broken_config(self) -> dict:
        """Config with llm.enabled=True but no default_model, force_model, or v_class map."""
        return {
            "llm": {
                "enabled": True,
                # intentionally no default_model, force_model, or model_by_v_class
            },
            "validation": {"max_validation_iterations": 4},
        }

    def test_fallback_on_routing_error_task_unchanged(self, tmp_path) -> None:
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        config = self._make_broken_config()
        log = _make_log(tmp_path)
        client = FakeClient([])  # should never be called
        task = _build_one(client, log, config, domain, v_class)

        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        assert task["scientific_action"] == template.scientific_action

    def test_fallback_on_routing_error_llm_meta_fallback_true(self, tmp_path) -> None:
        config = self._make_broken_config()
        log = _make_log(tmp_path)
        client = FakeClient([])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_fallback_on_routing_error_reason_prefix(self, tmp_path) -> None:
        config = self._make_broken_config()
        log = _make_log(tmp_path)
        client = FakeClient([])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert "routing_error" in meta["reason"]

    def test_fallback_on_routing_error_usage_row(self, tmp_path) -> None:
        config = self._make_broken_config()
        log = _make_log(tmp_path)
        client = FakeClient([])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})

        records = builder._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True
        assert records[0].prompt_tokens == 0
        assert records[0].latency_ms == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 11. Fallback on non-object JSON response
# ---------------------------------------------------------------------------


class TestFallbackOnNonObjectJson:
    def test_fallback_on_array_json(self, tmp_path) -> None:
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["[1, 2, 3]"])
        task = _build_one(client, log, config, domain, v_class)

        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        assert task["scientific_action"] == template.scientific_action
        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_fallback_on_array_json_reason(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["[1, 2, 3]"])
        task = _build_one(client, log, config)

        meta = task["provenance"]["llm_meta"]
        assert "JSON object" in meta["reason"] or "not a JSON" in meta["reason"]

    def test_fallback_on_array_json_usage_row(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["[1, 2, 3]"])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})

        records = builder._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True


# ---------------------------------------------------------------------------
# 12. Fallback on JSON null response
# ---------------------------------------------------------------------------


class TestFallbackOnJsonNull:
    def test_fallback_on_null_json(self, tmp_path) -> None:
        domain, v_class = "biology", "V1"
        template = TEMPLATES[domain][v_class]
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["null"])
        task = _build_one(client, log, config, domain, v_class)

        assert task["instruction"] == render_instruction(template.instruction, _make_asset())
        assert task["scientific_action"] == template.scientific_action
        meta = task["provenance"]["llm_meta"]
        assert meta["fallback"] is True

    def test_fallback_on_null_json_usage_row(self, tmp_path) -> None:
        config = _make_config()
        log = _make_log(tmp_path)
        client = FakeClient(["null"])
        builder = BuilderStage(config, llm_client=client, usage_log=log)
        builder.build_tasks([_make_plan_item()], {"domains": {"biology": [_make_asset()]}})

        records = builder._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True

