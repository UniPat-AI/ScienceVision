"""Tests for optional LLM semantic critique in ChallengerStage (Phase 3).

All tests use FakeClient / FakeUsageLog stubs — no network calls.

Key invariants under test:
- ChallengerStage() no-args is byte-identical to pre-LLM baseline.
- LLM disabled via config → no client call, identical output.
- LLM enabled with builder provenance → cross-model reviewer used.
- Builder fell back or no llm_meta → resolve_model reviewer.
- ChatClientError / bad JSON → fall back silently, record fallback row.
- Partial malformed issue list → only well-formed entries merged.
- Severity bump to critical → recommendation flips to "reject".
- Immutability: task dict is never mutated.
- critique_type field presence/value.
- Usage log rows accumulate across multiple critiques.
"""
from __future__ import annotations

import copy
import json
import types
from typing import Any

import pytest

from sciencevision_taskgen.llm import ChatClientError, ChatResult, UsageLog
from sciencevision_taskgen.pipeline.challenger_stage import ChallengerStage


# ---------------------------------------------------------------------------
# FakeClient — no network
# ---------------------------------------------------------------------------


class FakeClient:
    """Fake chat client that returns pre-canned responses or raises."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete_with_retry(
        self,
        model: str,
        system: str,
        user: str,
        **kw: Any,
    ) -> ChatResult:
        self.calls.append({"model": model, "system": system, "user": user, **kw})
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return ChatResult(
            content=r,
            latency_ms=15.0,
            prompt_tokens=80,
            completion_tokens=40,
            reasoning_tokens=0,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log(tmp_path: Any, models: list[str] | None = None) -> UsageLog:
    pricing: dict = {}
    for m in models or [
        "claude-sonnet-4-6",
        "claude-opus-4-5",
        "gemini-3.1-pro-preview",
        "<unresolved>",
    ]:
        pricing[m] = {"in": 3.0, "out": 15.0}
    return UsageLog.for_run(tmp_path / "run", pricing)


def _complete_task(v_class: str = "V2") -> dict:
    """Return a minimal task dict that passes ALL existing rule-based checks."""
    return {
        "task_id": "test-001",
        "domain": "biology",
        "v_class": v_class,
        "asset": {
            "path": "datasets/biology/4HHB.pdb",
            "viewer": "molstar",
            "format": "pdb",
            "exists_on_disk": True,
        },
        "instruction": "Identify the symmetry axis of the protein.",
        "required_evidence": ["screenshot"],
        "answer_schema": {"kind": "value", "fields": ["symmetry_axis"]},
        "ground_truth": {"status": "expert_verified", "value": "C2"},
        "scorer": {
            "primary": "exact_match",
            "checks": ["exact_match_check"],
        },
        "tool_policy": {
            "allowed_tools": ["molstar_viewer"],
        },
        "quality_gates": {"irreducible_3d": True},
        "terminal_answer": {
            "kind": "value",
            "fields": ["symmetry_axis"],
        },
        "active_vision_evidence": {"screenshot": True},
        "validation_record": {},
        "asset_grounding": {},
        "expert_review_requirements": {},
        "provenance": {},
    }


def _task_with_builder_provenance(
    builder_model: str = "claude-sonnet-4-6",
    fallback: bool = False,
) -> dict:
    """Return a complete task with llm_meta in provenance."""
    task = _complete_task()
    return {
        **task,
        "provenance": {
            "llm_meta": {
                "model": builder_model,
                "fallback": fallback,
            }
        },
    }


def _make_config(
    enabled: bool = True,
    default_model: str = "claude-sonnet-4-6",
    cross_model_map: dict | None = None,
    model_by_v_class: dict | None = None,
    model_pool: list[str] | None = None,
) -> dict:
    llm: dict = {
        "enabled": enabled,
        "default_model": default_model,
    }
    if cross_model_map is not None:
        llm["challenger_cross_model_map"] = cross_model_map
    if model_by_v_class is not None:
        llm["model_by_v_class"] = model_by_v_class
    if model_pool is not None:
        llm["model_pool"] = model_pool
    return {"llm": llm}


def _two_llm_issues_json() -> str:
    return json.dumps([
        {"code": "shortcut_risk", "severity": "medium", "detail": "Trivially guessable."},
        {"code": "terminology_error", "severity": "low", "detail": "Wrong term used."},
    ])


# ---------------------------------------------------------------------------
# 1. Backward-compat: ChallengerStage() no-args → identical to baseline
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_no_args_identical_to_baseline(self) -> None:
        """ChallengerStage() with no args must produce identical output."""
        task = _complete_task()
        challenger = ChallengerStage()
        result = challenger.critique_tasks([task])[0]

        assert result["critique_type"] == "static_sciencevision"
        # No llm_ prefixed issues
        for issue in result["issues"]:
            assert not issue["code"].startswith("llm_")
        # No usage_log to check (it's None)
        assert challenger._usage_log is None

    def test_no_args_accepts_clean_task(self) -> None:
        task = _complete_task()
        result = ChallengerStage().critique_tasks([task])[0]
        assert result["severity"] == "low"
        assert result["recommendation"] == "accept"

    def test_no_args_rejects_broken_task(self) -> None:
        task = _complete_task()
        del task["quality_gates"]
        result = ChallengerStage().critique_tasks([task])[0]
        assert result["recommendation"] == "reject"


# ---------------------------------------------------------------------------
# 2. LLM disabled via config
# ---------------------------------------------------------------------------


class TestLLMDisabledViaConfig:
    def test_disabled_no_client_call(self, tmp_path: Any) -> None:
        config = _make_config(enabled=False)
        log = _make_log(tmp_path)
        client = FakeClient([])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        task = _complete_task()
        result = challenger.critique_tasks([task])[0]

        assert client.calls == []
        assert result["critique_type"] == "static_sciencevision"

    def test_disabled_no_llm_issues(self, tmp_path: Any) -> None:
        config = _make_config(enabled=False)
        log = _make_log(tmp_path)
        client = FakeClient([])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        task = _complete_task()
        result = challenger.critique_tasks([task])[0]

        for issue in result["issues"]:
            assert not issue["code"].startswith("llm_")

    def test_disabled_no_usage_rows(self, tmp_path: Any) -> None:
        config = _make_config(enabled=False)
        log = _make_log(tmp_path)
        client = FakeClient([])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([_complete_task()])
        assert len(challenger._usage_log.all_records()) == 0


# ---------------------------------------------------------------------------
# 3. LLM enabled, builder used sonnet → challenger uses cross-model
# ---------------------------------------------------------------------------


class TestCrossModelRouting:
    def test_cross_model_used_for_reviewer(self, tmp_path: Any) -> None:
        config = _make_config(
            enabled=True,
            default_model="claude-sonnet-4-6",
            cross_model_map={"claude-sonnet-4-6": "claude-opus-4-5"},
        )
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        task = _task_with_builder_provenance(
            builder_model="claude-sonnet-4-6", fallback=False
        )
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        assert len(client.calls) == 1
        assert client.calls[0]["model"] == "claude-opus-4-5"

    def test_cross_model_issues_merged(self, tmp_path: Any) -> None:
        config = _make_config(
            enabled=True,
            default_model="claude-sonnet-4-6",
            cross_model_map={"claude-sonnet-4-6": "claude-opus-4-5"},
        )
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        task = _task_with_builder_provenance(
            builder_model="claude-sonnet-4-6", fallback=False
        )
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        llm_codes = [i["code"] for i in result["issues"] if i["code"].startswith("llm_")]
        assert len(llm_codes) == 2
        assert "llm_shortcut_risk" in llm_codes
        assert "llm_terminology_error" in llm_codes

    def test_cross_model_usage_row_recorded(self, tmp_path: Any) -> None:
        config = _make_config(
            enabled=True,
            default_model="claude-sonnet-4-6",
            cross_model_map={"claude-sonnet-4-6": "claude-opus-4-5"},
        )
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        task = _task_with_builder_provenance(
            builder_model="claude-sonnet-4-6", fallback=False
        )
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        records = challenger._usage_log.all_records()
        assert len(records) == 1
        assert records[0].stage == "challenger"
        assert records[0].fallback is False
        assert records[0].model == "claude-opus-4-5"


# ---------------------------------------------------------------------------
# 4. LLM enabled, builder fell back → reviewer uses resolve_model directly
# ---------------------------------------------------------------------------


class TestBuilderFallbackUsesResolveModel:
    def test_builder_fallback_uses_default_model(self, tmp_path: Any) -> None:
        config = _make_config(
            enabled=True,
            default_model="claude-sonnet-4-6",
            cross_model_map={"claude-sonnet-4-6": "claude-opus-4-5"},
        )
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        # Builder fell back, so reviewer should use resolve_model, not cross_model
        task = _task_with_builder_provenance(
            builder_model="claude-sonnet-4-6", fallback=True
        )
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        assert client.calls[0]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 5. LLM enabled, NO builder llm_meta → reviewer uses resolve_model directly
# ---------------------------------------------------------------------------


class TestNoBuilderLlmMeta:
    def test_no_llm_meta_uses_resolve_model(self, tmp_path: Any) -> None:
        config = _make_config(
            enabled=True,
            default_model="claude-sonnet-4-6",
            cross_model_map={"claude-sonnet-4-6": "claude-opus-4-5"},
        )
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        # No provenance.llm_meta at all
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        assert client.calls[0]["model"] == "claude-sonnet-4-6"

    def test_no_provenance_key_uses_resolve_model(self, tmp_path: Any) -> None:
        config = _make_config(
            enabled=True,
            default_model="claude-sonnet-4-6",
        )
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        # task has no 'provenance' key at all
        task = {k: v for k, v in _complete_task().items() if k != "provenance"}
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        assert client.calls[0]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 6. FakeClient raises ChatClientError → fall back to rule-only
# ---------------------------------------------------------------------------


class TestFallbackOnChatClientError:
    def test_fallback_returns_rule_only_result(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("network fail")])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        # No llm_ issues
        for issue in result["issues"]:
            assert not issue["code"].startswith("llm_")

    def test_fallback_no_exception_propagated(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("boom")])
        task = _complete_task()
        # Must not raise
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])
        assert len(result) == 1

    def test_fallback_usage_row_error_populated(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("err")])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        records = challenger._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True
        assert records[0].error is not None
        assert len(records[0].error) > 0

    def test_fallback_usage_row_zero_tokens(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("fail")])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        rec = challenger._usage_log.all_records()[0]
        assert rec.prompt_tokens == 0
        assert rec.completion_tokens == 0
        assert rec.reasoning_tokens == 0
        assert rec.latency_ms == pytest.approx(0.0)

    def test_fallback_critique_type_still_llm_attempted(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("fail")])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]
        # LLM was attempted (even though it failed), so critique_type updated
        assert result["critique_type"] == "static+llm_sciencevision"


# ---------------------------------------------------------------------------
# 7. FakeClient returns malformed (non-list) JSON → fall back, no exception
# ---------------------------------------------------------------------------


class TestFallbackOnNonListJson:
    def test_non_list_json_falls_back(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient(['{"not": "a list"}'])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        for issue in result["issues"]:
            assert not issue["code"].startswith("llm_")

    def test_non_list_json_records_fallback_row(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient(['{"not": "a list"}'])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])

        records = challenger._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is True

    def test_invalid_json_string_falls_back(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient(["not valid json at all"])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        for issue in result["issues"]:
            assert not issue["code"].startswith("llm_")


# ---------------------------------------------------------------------------
# 8. Mixed valid/invalid entries → only valid entries merged
# ---------------------------------------------------------------------------


class TestMixedValidInvalidIssues:
    def test_malformed_entries_silently_dropped(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        mixed_response = json.dumps([
            {"code": "shortcut_risk", "severity": "medium", "detail": "Valid issue."},
            {"code": 123, "severity": "high", "detail": "Bad code type."},  # malformed
            "just a string",  # not a dict at all
            {"severity": "low", "detail": "Missing code key."},  # missing code
        ])
        client = FakeClient([mixed_response])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        llm_codes = [i["code"] for i in result["issues"] if i["code"].startswith("llm_")]
        assert llm_codes == ["llm_shortcut_risk"]

    def test_all_invalid_entries_no_llm_issues(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        all_bad = json.dumps([
            {"code": 1, "severity": 2, "detail": 3},
            "string entry",
        ])
        client = FakeClient([all_bad])
        task = _complete_task()
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        llm_codes = [i["code"] for i in result["issues"] if i["code"].startswith("llm_")]
        assert llm_codes == []
        # No fallback — we did parse successfully (empty list after filtering)
        records = challenger._usage_log.all_records()
        assert len(records) == 1
        assert records[0].fallback is False


# ---------------------------------------------------------------------------
# 9. LLM issues bump severity to critical → recommendation flips to "reject"
# ---------------------------------------------------------------------------


class TestSeverityBumpToReject:
    def test_critical_issue_from_llm_flips_to_reject(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        # Ensure rule-only result is "accept" first
        task = _complete_task()
        rule_result = ChallengerStage().critique_tasks([task])[0]
        assert rule_result["recommendation"] == "accept"

        # LLM returns a critical issue
        critical_response = json.dumps([
            {"code": "scientific_invalid", "severity": "critical", "detail": "Contradicts domain."},
        ])
        client = FakeClient([critical_response])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        assert result["severity"] == "critical"
        assert result["recommendation"] == "reject"

    def test_high_issue_from_llm_flips_to_revise(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        # Ensure rule-only result is "accept" first
        task = _complete_task()
        rule_result = ChallengerStage().critique_tasks([task])[0]
        assert rule_result["recommendation"] == "accept"

        high_response = json.dumps([
            {"code": "asset_mismatch", "severity": "high", "detail": "Asset doesn't match."},
        ])
        client = FakeClient([high_response])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])[0]

        assert result["recommendation"] == "revise"


# ---------------------------------------------------------------------------
# 10. Immutability: original task dict is never mutated
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_task_dict_not_mutated_no_llm(self) -> None:
        task = _complete_task()
        original = copy.deepcopy(task)
        ChallengerStage().critique_tasks([task])
        assert task == original

    def test_task_dict_not_mutated_with_llm(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        task = _task_with_builder_provenance(
            builder_model="claude-sonnet-4-6", fallback=False
        )
        original = copy.deepcopy(task)
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks([task])
        assert task == original

    def test_mappingproxy_task_not_mutated(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        # Use a plain dict but snapshot it; MappingProxyType would raise on write
        task = _complete_task()
        snapshot = dict(task)
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([task])
        assert len(result) == 1
        assert task == snapshot  # original dict unchanged


# ---------------------------------------------------------------------------
# 11. critique_type field
# ---------------------------------------------------------------------------


class TestCritiqueTypeField:
    def test_critique_type_static_when_llm_disabled(self) -> None:
        result = ChallengerStage().critique_tasks([_complete_task()])[0]
        assert result["critique_type"] == "static_sciencevision"

    def test_critique_type_static_when_config_disabled(self, tmp_path: Any) -> None:
        config = _make_config(enabled=False)
        log = _make_log(tmp_path)
        client = FakeClient([])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([_complete_task()])[0]
        assert result["critique_type"] == "static_sciencevision"

    def test_critique_type_llm_when_llm_enabled_success(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([_two_llm_issues_json()])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([_complete_task()])[0]
        assert result["critique_type"] == "static+llm_sciencevision"

    def test_critique_type_llm_when_llm_fallback(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        client = FakeClient([ChatClientError("fail")])
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        result = challenger.critique_tasks([_complete_task()])[0]
        # LLM was attempted even though it failed
        assert result["critique_type"] == "static+llm_sciencevision"


# ---------------------------------------------------------------------------
# 12. Usage log retention across multiple critiques
# ---------------------------------------------------------------------------


class TestUsageLogRetention:
    def test_all_rows_preserved_across_multiple_critiques(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        responses = [
            _two_llm_issues_json(),
            _two_llm_issues_json(),
            _two_llm_issues_json(),
        ]
        client = FakeClient(responses)
        tasks = [_complete_task() for _ in range(3)]
        for i, t in enumerate(tasks):
            tasks[i] = {**t, "task_id": f"task-{i:03d}"}
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks(tasks)

        records = challenger._usage_log.all_records()
        assert len(records) == 3

    def test_mixed_success_and_fallback_rows_all_kept(self, tmp_path: Any) -> None:
        config = _make_config(enabled=True, default_model="claude-sonnet-4-6")
        log = _make_log(tmp_path)
        responses = [
            _two_llm_issues_json(),
            ChatClientError("network"),
            _two_llm_issues_json(),
        ]
        client = FakeClient(responses)
        tasks = [
            {**_complete_task(), "task_id": f"task-{i:03d}"}
            for i in range(3)
        ]
        challenger = ChallengerStage(config=config, llm_client=client, usage_log=log)
        challenger.critique_tasks(tasks)

        records = challenger._usage_log.all_records()
        assert len(records) == 3
        fallback_flags = [r.fallback for r in records]
        assert fallback_flags == [False, True, False]
