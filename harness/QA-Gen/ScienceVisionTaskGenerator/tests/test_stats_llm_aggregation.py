"""Tests for stats.py LLM usage aggregation (Phase 4).

Covers:
1. Run dir with no llm_usage.jsonl: existing behavior unchanged.
2. Run dir WITH llm_usage.jsonl: correct aggregation.
3. Malformed JSONL lines skipped silently.
4. Fallback rows: counted but tokens not inflated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sciencevision_taskgen.stats import aggregate_llm_usage, summarize_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def make_record(
    *,
    stage: str = "builder",
    model: str = "claude-sonnet-4-6",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    reasoning_tokens: int = 0,
    latency_ms: float = 200.0,
    cost_usd: float = 0.001,
    fallback: bool = False,
    error: str | None = None,
    task_id: str = "t1",
    v_class: str = "V1",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "task_id": task_id,
        "v_class": v_class,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "fallback": fallback,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Test 1 — No llm_usage.jsonl: legacy behavior
# ---------------------------------------------------------------------------

def test_no_jsonl_legacy_cost_basis(tmp_path: Path) -> None:
    """Without llm_usage.jsonl, cost_basis is the legacy 'no_llm_usage_found'."""
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    # Write a minimal final_tasks.jsonl so generated > 0
    (run_dir / "final_tasks.jsonl").write_text(
        json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8"
    )

    stats = summarize_run(run_dir)

    assert stats["cost"]["cost_basis"] == "no_llm_usage_found"
    assert "llm_usage" not in stats
    assert stats["token_usage"]["usage_records_found"] == 0


# ---------------------------------------------------------------------------
# Test 2 — llm_usage.jsonl present: correct aggregation
# ---------------------------------------------------------------------------

def test_jsonl_present_aggregation(tmp_path: Path) -> None:
    """With llm_usage.jsonl, by_stage and by_model aggregates are correct."""
    run_dir = tmp_path / "run_002"
    run_dir.mkdir()

    rows = [
        # builder — sonnet
        make_record(stage="builder", model="claude-sonnet-4-6",
                    prompt_tokens=100, completion_tokens=50, cost_usd=0.001,
                    latency_ms=200.0, task_id="t1"),
        # builder — opus
        make_record(stage="builder", model="claude-opus-4-7",
                    prompt_tokens=200, completion_tokens=80, cost_usd=0.005,
                    latency_ms=400.0, task_id="t2"),
        # challenger — sonnet
        make_record(stage="challenger", model="claude-sonnet-4-6",
                    prompt_tokens=150, completion_tokens=60, cost_usd=0.002,
                    latency_ms=250.0, task_id="t1"),
    ]
    write_jsonl(run_dir / "llm_usage.jsonl", rows)
    (run_dir / "final_tasks.jsonl").write_text(
        json.dumps({"task_id": "t1"}) + "\n", encoding="utf-8"
    )

    stats = summarize_run(run_dir)
    lu = stats["llm_usage"]

    assert lu["records_total"] == 3
    assert lu["fallback_rate"] == pytest.approx(0.0)

    # --- by_stage ---
    builder = lu["by_stage"]["builder"]
    assert builder["calls"] == 2
    assert builder["input_tokens"] == 300   # 100 + 200
    assert builder["output_tokens"] == 130  # 50 + 80
    assert builder["cost_usd"] == pytest.approx(0.006)  # 0.001 + 0.005

    challenger = lu["by_stage"]["challenger"]
    assert challenger["calls"] == 1
    assert challenger["input_tokens"] == 150
    assert challenger["cost_usd"] == pytest.approx(0.002)

    # --- by_model ---
    sonnet = lu["by_model"]["claude-sonnet-4-6"]
    assert sonnet["calls"] == 2  # one builder + one challenger
    assert sonnet["input_tokens"] == 250   # 100 + 150
    assert sonnet["output_tokens"] == 110  # 50 + 60
    assert sonnet["cost_usd"] == pytest.approx(0.003)
    assert sonnet["avg_latency_ms"] == pytest.approx((200.0 + 250.0) / 2)

    opus = lu["by_model"]["claude-opus-4-7"]
    assert opus["calls"] == 1
    assert opus["avg_latency_ms"] == pytest.approx(400.0)

    # --- cost in top-level stats ---
    assert stats["cost"]["cost_basis"] == "llm_usage_jsonl"
    expected_total = 0.001 + 0.005 + 0.002
    assert stats["cost"]["cost_usd_total"] == pytest.approx(expected_total)

    # --- token_usage in top-level stats ---
    assert stats["token_usage"]["usage_records_found"] == 3
    assert stats["token_usage"]["input_tokens_total"] == pytest.approx(450.0)   # 300+150
    assert stats["token_usage"]["output_tokens_total"] == pytest.approx(190.0)  # 130+60


# ---------------------------------------------------------------------------
# Test 3 — Malformed JSONL lines are skipped silently
# ---------------------------------------------------------------------------

def test_malformed_jsonl_skipped(tmp_path: Path) -> None:
    """A bad JSONL line does not crash stats; good rows are still counted."""
    run_dir = tmp_path / "run_003"
    run_dir.mkdir()

    good_row = make_record(stage="builder", model="claude-sonnet-4-6",
                           prompt_tokens=100, completion_tokens=40, cost_usd=0.001)
    jsonl_path = run_dir / "llm_usage.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(good_row) + "\n")
        f.write("THIS IS NOT JSON\n")  # malformed
        f.write("{broken json,\n")     # also malformed
        f.write(json.dumps(make_record(stage="challenger", model="claude-sonnet-4-6",
                                       prompt_tokens=50, completion_tokens=20, cost_usd=0.0005)) + "\n")

    stats = summarize_run(run_dir)
    lu = stats.get("llm_usage")
    assert lu is not None, "llm_usage key must be present when jsonl exists"
    # 2 good rows, 2 bad rows skipped
    assert lu["records_total"] == 2
    assert lu["by_stage"]["builder"]["calls"] == 1
    assert lu["by_stage"]["challenger"]["calls"] == 1


# ---------------------------------------------------------------------------
# Test 4 — Fallback rows counted but tokens/cost zeroed
# ---------------------------------------------------------------------------

def test_fallback_rows_not_inflating_tokens(tmp_path: Path) -> None:
    """Fallback rows count towards calls+fallbacks but not tokens or cost."""
    run_dir = tmp_path / "run_004"
    run_dir.mkdir()

    rows = [
        # Normal call
        make_record(stage="builder", model="claude-sonnet-4-6",
                    prompt_tokens=100, completion_tokens=50, cost_usd=0.001,
                    fallback=False),
        # Fallback call (LLM failed; tokens are irrelevant / 0)
        make_record(stage="builder", model="claude-sonnet-4-6",
                    prompt_tokens=0, completion_tokens=0, cost_usd=0.0,
                    fallback=True, error="timeout"),
        # Another normal
        make_record(stage="challenger", model="claude-opus-4-7",
                    prompt_tokens=200, completion_tokens=80, cost_usd=0.005,
                    fallback=False),
    ]
    write_jsonl(run_dir / "llm_usage.jsonl", rows)

    result = aggregate_llm_usage(run_dir)
    assert result is not None

    assert result["records_total"] == 3
    assert result["fallback_rate"] == pytest.approx(1 / 3)

    builder = result["by_stage"]["builder"]
    assert builder["calls"] == 2
    assert builder["fallbacks"] == 1
    # Fallback row adds 0 tokens
    assert builder["input_tokens"] == 100
    assert builder["output_tokens"] == 50
    assert builder["cost_usd"] == pytest.approx(0.001)

    challenger = result["by_stage"]["challenger"]
    assert challenger["calls"] == 1
    assert challenger["fallbacks"] == 0
    assert challenger["input_tokens"] == 200

    # by_model: fallback row still counted as a call but 0 tokens
    sonnet = result["by_model"]["claude-sonnet-4-6"]
    assert sonnet["calls"] == 2
    assert sonnet["input_tokens"] == 100  # only the non-fallback row
