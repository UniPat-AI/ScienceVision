"""Tests for sciencevision_taskgen.llm.usage."""
from __future__ import annotations

import json

import pytest

from sciencevision_taskgen.llm.usage import UsageLog, UsageRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_pricing() -> dict[str, dict[str, float]]:
    return {
        "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
        "model-a": {"in": 1.0, "out": 2.0},
    }


def _make_log(tmp_path) -> UsageLog:
    return UsageLog.for_run(tmp_path / "run", _sample_pricing())


def _add_record(log: UsageLog, *, task_id: str = "t1", model: str = "model-a") -> UsageLog:
    return log.record(
        "builder",
        task_id,
        "V1",
        model,
        prompt_tokens=100,
        completion_tokens=50,
        reasoning_tokens=0,
        latency_ms=123.4,
    )


# ---------------------------------------------------------------------------
# for_run
# ---------------------------------------------------------------------------


class TestForRun:
    def test_creates_run_dir_if_missing(self, tmp_path) -> None:
        run_dir = tmp_path / "new_run"
        assert not run_dir.exists()
        UsageLog.for_run(run_dir, {})
        assert run_dir.exists()

    def test_existing_run_dir_is_ok(self, tmp_path) -> None:
        run_dir = tmp_path / "existing"
        run_dir.mkdir()
        log = UsageLog.for_run(run_dir, {})
        assert log.all_records() == ()


# ---------------------------------------------------------------------------
# record — immutability & persistence
# ---------------------------------------------------------------------------


class TestRecord:
    def test_returns_new_log_with_one_record(self, tmp_path) -> None:
        log0 = _make_log(tmp_path)
        log1 = _add_record(log0)
        assert len(log1.all_records()) == 1

    def test_original_log_unchanged(self, tmp_path) -> None:
        log0 = _make_log(tmp_path)
        _add_record(log0)
        assert log0.all_records() == ()

    def test_appends_to_disk(self, tmp_path) -> None:
        log0 = _make_log(tmp_path)
        log1 = _add_record(log0)
        log_file = tmp_path / "run" / "llm_usage.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["task_id"] == "t1"

        # Second record
        _add_record(log1, task_id="t2")
        lines2 = log_file.read_text().strip().splitlines()
        assert len(lines2) == 2

    def test_multiple_records_aggregate(self, tmp_path) -> None:
        log = _make_log(tmp_path)
        log = _add_record(log, task_id="t1")
        log = _add_record(log, task_id="t2")
        log = _add_record(log, task_id="t3")
        assert len(log.all_records()) == 3
        ids = [r.task_id for r in log.all_records()]
        assert ids == ["t1", "t2", "t3"]

    def test_all_records_returns_tuple(self, tmp_path) -> None:
        log = _make_log(tmp_path)
        log = _add_record(log)
        assert isinstance(log.all_records(), tuple)


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------


class TestCostCalc:
    def test_known_cost_sonnet(self, tmp_path) -> None:
        """1000 prompt + 500 completion @ in=3.0, out=15.0 → 0.0105 USD."""
        log0 = UsageLog.for_run(tmp_path / "r", _sample_pricing())
        log1 = log0.record(
            "builder",
            "t1",
            "V1",
            "claude-sonnet-4-6",
            prompt_tokens=1000,
            completion_tokens=500,
            reasoning_tokens=0,
            latency_ms=200.0,
        )
        record = log1.all_records()[0]
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000.0
        assert abs(record.cost_usd - expected) < 1e-9

    def test_unknown_model_cost_is_zero(self, tmp_path) -> None:
        log0 = UsageLog.for_run(tmp_path / "r", _sample_pricing())
        log1 = log0.record(
            "challenger",
            "t2",
            "V2",
            "unknown-model",
            prompt_tokens=500,
            completion_tokens=200,
            reasoning_tokens=0,
            latency_ms=50.0,
        )
        assert log1.all_records()[0].cost_usd == 0.0

    def test_cost_precision_six_decimals(self, tmp_path) -> None:
        log0 = UsageLog.for_run(tmp_path / "r", _sample_pricing())
        log1 = log0.record(
            "judge",
            "t3",
            "V3",
            "claude-sonnet-4-6",
            prompt_tokens=1000,
            completion_tokens=500,
            reasoning_tokens=10,
            latency_ms=300.0,
        )
        record = log1.all_records()[0]
        assert round(record.cost_usd, 6) == round(0.0105, 6)


# ---------------------------------------------------------------------------
# Fallback flag
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_record_fields(self, tmp_path) -> None:
        log0 = _make_log(tmp_path)
        log1 = log0.record(
            "builder",
            "t-err",
            "V1",
            "model-a",
            prompt_tokens=0,
            completion_tokens=0,
            reasoning_tokens=0,
            latency_ms=0.0,
            fallback=True,
            error="connection refused",
        )
        rec: UsageRecord = log1.all_records()[0]
        assert rec.fallback is True
        assert rec.error == "connection refused"

    def test_default_fallback_is_false(self, tmp_path) -> None:
        log1 = _add_record(_make_log(tmp_path))
        assert log1.all_records()[0].fallback is False
        assert log1.all_records()[0].error is None


# ---------------------------------------------------------------------------
# Pricing defensive copy
# ---------------------------------------------------------------------------


class TestPricingDefensiveCopy:
    def test_mutating_original_pricing_does_not_affect_cost(self, tmp_path) -> None:
        pricing = {"model-a": {"in": 1.0, "out": 2.0}}
        log = UsageLog.for_run(tmp_path / "r", pricing)
        # Mutate the original pricing after constructing the log
        pricing["model-a"]["in"] = 999.0
        pricing["model-a"]["out"] = 999.0
        log1 = log.record(
            "builder",
            "t1",
            "V1",
            "model-a",
            prompt_tokens=1_000_000,
            completion_tokens=0,
            reasoning_tokens=0,
            latency_ms=10.0,
        )
        # Cost should reflect original in=1.0, not mutated 999.0
        assert abs(log1.all_records()[0].cost_usd - 1.0) < 1e-9
