"""Generation statistics for ScienceVision task runs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


TOKEN_KEY_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens", "input_token_count", "prompt_token_count"),
    "output_tokens": ("output_tokens", "completion_tokens", "output_token_count", "completion_token_count"),
    "total_tokens": ("total_tokens", "total_token_count"),
}

COST_KEYS = (
    "cost_usd",
    "total_cost_usd",
    "price_usd",
    "estimated_cost_usd",
    "estimated_price_usd",
)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_jsonl_tolerant(path: Path) -> list[dict[str, Any]]:
    """Like load_jsonl but skips malformed lines silently."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
    return rows


def first_existing(base: Path, names: list[str]) -> Path | None:
    for name in names:
        path = base / name
        if path.exists():
            return path
    return None


def collect_usage(value: Any) -> list[dict[str, float]]:
    """Collect token/cost usage records from arbitrary nested run artifacts.

    The current deterministic generator has no LLM usage. This collector is
    intentionally permissive so future LLM-backed builder/challenger/refiner
    stages can add either `usage`, `token_usage`, or direct token/cost fields
    without changing the statistics code.
    """
    found: list[dict[str, float]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            record: dict[str, float] = {}
            for canonical, aliases in TOKEN_KEY_ALIASES.items():
                for key in aliases:
                    raw = node.get(key)
                    if isinstance(raw, (int, float)):
                        record[canonical] = float(raw)
                        break
            for key in COST_KEYS:
                raw = node.get(key)
                if isinstance(raw, (int, float)):
                    record["cost_usd"] = float(raw)
                    break
            if record:
                if "total_tokens" not in record:
                    record["total_tokens"] = record.get("input_tokens", 0.0) + record.get("output_tokens", 0.0)
                found.append(record)

            for key in ("usage", "token_usage", "llm_usage", "billing"):
                child = node.get(key)
                if child is not None:
                    visit(child)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def aggregate_llm_usage(run_path: Path) -> dict[str, Any] | None:
    """Aggregate per-call records from ``<run_dir>/llm_usage.jsonl``.

    Returns ``None`` when the file does not exist (legacy run without JSONL).
    Malformed rows are skipped silently so a partially-written log never
    causes a crash.

    Each row in the JSONL is expected to have (at minimum):
        stage, model, prompt_tokens, completion_tokens, reasoning_tokens,
        latency_ms, cost_usd, fallback
    """
    jsonl_path = run_path / "llm_usage.jsonl"
    records = load_jsonl_tolerant(jsonl_path)
    if not jsonl_path.exists():
        return None

    by_stage: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    total_fallbacks = 0
    total_calls = 0

    for rec in records:
        stage = rec.get("stage", "unknown")
        model = rec.get("model", "unknown")
        fallback = bool(rec.get("fallback", False))
        prompt_tokens = int(rec.get("prompt_tokens", 0) or 0)
        completion_tokens = int(rec.get("completion_tokens", 0) or 0)
        reasoning_tokens = int(rec.get("reasoning_tokens", 0) or 0)
        latency_ms = float(rec.get("latency_ms", 0.0) or 0.0)
        cost_usd = float(rec.get("cost_usd", 0.0) or 0.0)

        # Fallback rows have no real token usage; do not inflate totals.
        effective_prompt = 0 if fallback else prompt_tokens
        effective_completion = 0 if fallback else completion_tokens
        effective_reasoning = 0 if fallback else reasoning_tokens
        effective_cost = 0.0 if fallback else cost_usd

        total_calls += 1
        if fallback:
            total_fallbacks += 1

        # -- by_stage accumulation --
        if stage not in by_stage:
            by_stage[stage] = {
                "calls": 0,
                "fallbacks": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_latency_ms": 0.0,
                "cost_usd": 0.0,
            }
        s = by_stage[stage]
        by_stage[stage] = {
            "calls": s["calls"] + 1,
            "fallbacks": s["fallbacks"] + (1 if fallback else 0),
            "input_tokens": s["input_tokens"] + effective_prompt,
            "output_tokens": s["output_tokens"] + effective_completion,
            "reasoning_tokens": s["reasoning_tokens"] + effective_reasoning,
            "total_latency_ms": s["total_latency_ms"] + latency_ms,
            "cost_usd": s["cost_usd"] + effective_cost,
        }

        # -- by_model accumulation --
        if model not in by_model:
            by_model[model] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "total_latency_ms": 0.0,
            }
        m = by_model[model]
        by_model[model] = {
            "calls": m["calls"] + 1,
            "input_tokens": m["input_tokens"] + effective_prompt,
            "output_tokens": m["output_tokens"] + effective_completion,
            "cost_usd": m["cost_usd"] + effective_cost,
            "total_latency_ms": m["total_latency_ms"] + latency_ms,
        }

    # Add avg_latency_ms to by_model (computed after accumulation)
    for model_key, m in by_model.items():
        calls = m["calls"]
        by_model[model_key] = {
            "calls": m["calls"],
            "input_tokens": m["input_tokens"],
            "output_tokens": m["output_tokens"],
            "cost_usd": m["cost_usd"],
            "avg_latency_ms": (m["total_latency_ms"] / calls) if calls else 0.0,
        }

    fallback_rate = (total_fallbacks / total_calls) if total_calls else 0.0

    return {
        "records_total": len(records),
        "by_stage": by_stage,
        "by_model": by_model,
        "fallback_rate": fallback_rate,
    }


def summarize_run(
    run_dir: str | Path,
    input_price_per_mtok: float | None = None,
    output_price_per_mtok: float | None = None,
    total_price_per_mtok: float | None = None,
) -> dict[str, Any]:
    run_path = Path(run_dir)

    candidates_path = first_existing(run_path, ["candidate_tasks.json", "ScienceVision_WalkingSkeleton_candidate_tasks.json"])
    decisions_path = first_existing(run_path, ["decisions.json", "ScienceVision_WalkingSkeleton_decisions.json"])
    critiques_path = first_existing(run_path, ["critiques.json", "validation_critiques.json", "ScienceVision_WalkingSkeleton_validation_critiques.json"])
    seed_plan_path = first_existing(run_path, ["seed_plan.json", "ScienceVision_WalkingSkeleton_seed_plan.json"])
    final_json_path = first_existing(run_path, ["final_tasks.json", "ScienceVision_WalkingSkeleton_final_tasks.json"])
    final_jsonl_path = first_existing(run_path, ["final_tasks.jsonl", "ScienceVision_WalkingSkeleton_final_tasks.jsonl"])

    candidates = load_json(candidates_path, []) if candidates_path else []
    decisions = load_json(decisions_path, []) if decisions_path else []
    critiques = load_json(critiques_path, []) if critiques_path else []
    seed_plan = load_json(seed_plan_path, []) if seed_plan_path else []
    final_tasks = load_json(final_json_path, []) if final_json_path else []
    if not final_tasks:
        final_tasks = load_jsonl(final_jsonl_path) if final_jsonl_path else []

    if candidates:
        generated = len(candidates)
    elif seed_plan:
        generated = len([item for item in seed_plan if isinstance(item, dict) and item.get("status", "ready") == "ready"])
    else:
        generated = len(final_tasks)
    kept = len(final_tasks)
    filtered = max(generated - kept, 0)
    decision_counts = Counter(d.get("decision", "unknown") for d in decisions if isinstance(d, dict))
    critique_counts = Counter(c.get("severity", "unknown") for c in critiques if isinstance(c, dict))

    # --- LLM usage JSONL (authoritative when present) ---
    llm_usage = aggregate_llm_usage(run_path)

    if llm_usage is not None:
        # Derive aggregate token/cost figures from the JSONL.
        all_stage_data = llm_usage["by_stage"].values()
        input_tokens = float(sum(s["input_tokens"] for s in all_stage_data))
        all_stage_data = llm_usage["by_stage"].values()
        output_tokens = float(sum(s["output_tokens"] for s in all_stage_data))
        all_stage_data = llm_usage["by_stage"].values()
        total_tokens = input_tokens + output_tokens
        estimated_cost = float(sum(s["cost_usd"] for s in all_stage_data))
        cost_basis = "llm_usage_jsonl"
        usage_records: list[dict[str, float]] = []  # legacy collector not used
    else:
        # Legacy fallback: permissive collector from artifact files.
        usage_sources = {
            "candidates": candidates,
            "decisions": decisions,
            "critiques": critiques,
            "final_tasks": final_tasks,
        }
        usage_records = []
        for artifact in usage_sources.values():
            usage_records.extend(collect_usage(artifact))

        input_tokens = sum(r.get("input_tokens", 0.0) for r in usage_records)
        output_tokens = sum(r.get("output_tokens", 0.0) for r in usage_records)
        total_tokens = sum(r.get("total_tokens", 0.0) for r in usage_records)
        explicit_cost = sum(r.get("cost_usd", 0.0) for r in usage_records)

        estimated_cost = explicit_cost
        cost_basis = "explicit_usage_cost"
        if explicit_cost == 0 and total_tokens:
            if input_price_per_mtok is not None or output_price_per_mtok is not None:
                estimated_cost = (
                    input_tokens * (input_price_per_mtok or 0.0)
                    + output_tokens * (output_price_per_mtok or 0.0)
                ) / 1_000_000.0
                cost_basis = "input_output_price_per_million_tokens"
            elif total_price_per_mtok is not None:
                estimated_cost = total_tokens * total_price_per_mtok / 1_000_000.0
                cost_basis = "total_price_per_million_tokens"
            else:
                cost_basis = "tokens_found_no_price_config"
        elif explicit_cost == 0:
            cost_basis = "no_llm_usage_found"

    denominator = generated or 1
    result: dict[str, Any] = {
        "run_dir": str(run_path),
        "task_counts": {
            "generated_total": generated,
            "kept_total": kept,
            "filtered_total": filtered,
            "kept_ratio": kept / denominator,
            "filtered_ratio": filtered / denominator,
            "decision_counts": dict(sorted(decision_counts.items())),
            "critique_severity_counts": dict(sorted(critique_counts.items())),
        },
        "token_usage": {
            "usage_records_found": len(usage_records) if llm_usage is None else llm_usage["records_total"],
            "input_tokens_total": input_tokens,
            "output_tokens_total": output_tokens,
            "total_tokens_total": total_tokens,
            "input_tokens_avg_per_generated_task": input_tokens / denominator,
            "output_tokens_avg_per_generated_task": output_tokens / denominator,
            "total_tokens_avg_per_generated_task": total_tokens / denominator,
        },
        "cost": {
            "cost_usd_total": estimated_cost,
            "cost_usd_avg_per_generated_task": estimated_cost / denominator,
            "cost_basis": cost_basis,
            "input_price_per_mtok": input_price_per_mtok,
            "output_price_per_mtok": output_price_per_mtok,
            "total_price_per_mtok": total_price_per_mtok,
        },
    }
    if llm_usage is not None:
        result["llm_usage"] = llm_usage

    # Question-quality metrics over the accepted tasks (deterministic scoreboard).
    from sciencevision_taskgen.metrics import compute_quality_metrics

    result["quality_metrics"] = compute_quality_metrics(final_tasks)
    return result


def stats_to_markdown(stats: dict[str, Any]) -> str:
    counts = stats["task_counts"]
    tokens = stats["token_usage"]
    cost = stats["cost"]
    lines = [
        "# ScienceVision Generation Stats",
        "",
        f"- Run/task dir: `{stats['run_dir']}`",
        f"- Generated tasks: {counts['generated_total']}",
        f"- Kept tasks: {counts['kept_total']} ({counts['kept_ratio']:.2%})",
        f"- Filtered tasks: {counts['filtered_total']} ({counts['filtered_ratio']:.2%})",
        "",
        "## Token / Cost",
        "",
        f"- Usage records found: {tokens['usage_records_found']}",
        f"- Total tokens: {tokens['total_tokens_total']:.0f}",
        f"- Avg tokens / generated task: {tokens['total_tokens_avg_per_generated_task']:.2f}",
        f"- Total cost USD: {cost['cost_usd_total']:.6f}",
        f"- Avg cost USD / generated task: {cost['cost_usd_avg_per_generated_task']:.6f}",
        f"- Cost basis: `{cost['cost_basis']}`",
        "",
        "## Decision Counts",
        "",
    ]
    if counts["decision_counts"]:
        lines.extend(f"- {key}: {value}" for key, value in counts["decision_counts"].items())
    else:
        lines.append("- No decision artifact found.")
    lines.extend(["", "## Critique Severity Counts", ""])
    if counts["critique_severity_counts"]:
        lines.extend(f"- {key}: {value}" for key, value in counts["critique_severity_counts"].items())
    else:
        lines.append("- No critique artifact found.")

    llm_usage = stats.get("llm_usage")
    if llm_usage:
        lines.extend([
            "",
            "## LLM Usage",
            "",
            f"- Total records: {llm_usage['records_total']}",
            f"- Fallback rate: {llm_usage['fallback_rate']:.2%}",
            "",
            "### By Stage",
            "",
            "| Stage | Calls | Fallbacks | Input Tokens | Output Tokens | Reasoning Tokens | Latency (ms) | Cost USD |",
            "|-------|-------|-----------|-------------|--------------|-----------------|-------------|----------|",
        ])
        for stage, s in sorted(llm_usage["by_stage"].items()):
            lines.append(
                f"| {stage} | {s['calls']} | {s['fallbacks']} | {s['input_tokens']} |"
                f" {s['output_tokens']} | {s['reasoning_tokens']} |"
                f" {s['total_latency_ms']:.1f} | {s['cost_usd']:.6f} |"
            )
        lines.extend([
            "",
            "### By Model",
            "",
            "| Model | Calls | Input Tokens | Output Tokens | Cost USD | Avg Latency (ms) |",
            "|-------|-------|-------------|--------------|----------|-----------------|",
        ])
        for model, m in sorted(llm_usage["by_model"].items()):
            lines.append(
                f"| {model} | {m['calls']} | {m['input_tokens']} | {m['output_tokens']} |"
                f" {m['cost_usd']:.6f} | {m['avg_latency_ms']:.1f} |"
            )

    qm = stats.get("quality_metrics")
    if qm:
        g = qm["grounding"]
        d = qm["diversity"]
        s = qm["shortcut"]

        def _pct(x: float | None) -> str:
            return "n/a" if x is None else f"{x:.2%}"

        lines.extend([
            "",
            "## Question Quality",
            "",
            "### Grounding validity",
            "",
            f"- Grounded tasks: {g['grounded_count']}/{g['n_tasks']} ({_pct(g['grounded_rate'])})",
            f"- **Grounding-validity rate** (entities present in instruction):"
            f" {g['entity_backed_count']}/{g['n_tasks']} ({_pct(g['grounding_validity_rate'])})",
            "",
            "### Diversity",
            "",
            f"- Unique instructions: {d['unique_instruction_count']}/{d['n_tasks']}"
            f" ({_pct(d['unique_instruction_rate'])})",
            f"- Distinct-bigram ratio: {d['distinct_bigram_ratio']:.3f}",
            f"- Distinct grounded entities: {d['distinct_grounded_entities']}",
            "",
            "### Shortcut risk",
            "",
            f"- Categorical tasks: {s['categorical_tasks']}/{s['n_tasks']}",
            f"- Mean random-guess accuracy: {_pct(s['mean_random_guess_accuracy'])}",
            f"- Text-only solve rate: {_pct(s['text_only_solve_rate'])} ({s['text_only_probe']})",
        ])

    return "\n".join(lines) + "\n"
