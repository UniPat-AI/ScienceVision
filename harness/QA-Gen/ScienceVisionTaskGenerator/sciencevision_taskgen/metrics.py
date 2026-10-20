"""Question-quality metrics for a generated ScienceVision task batch.

These are the before/after scoreboard for generation improvements.  All three
are computed deterministically from a list of final task dicts (the objects in
``final_tasks.jsonl``); none require an LLM by default.

1. Grounding validity — is the question actually bound to the asset's real
   entities?  ``grounding_validity_rate`` is the fraction of tasks that are
   marked grounded AND whose declared verified entities literally appear in the
   instruction text.  The old asset-blind templates score ~0 here (a generic
   "heme cofactor" instruction names no per-asset entity).

2. Instruction diversity — distinct-instruction ratio and distinct-bigram
   ratio across the batch, plus the count of distinct grounded entities.  A
   batch of one verbatim template per domain scores near 0.

3. Shortcut risk — deterministic proxy: mean random-guess accuracy over
   enumerated categorical answers (higher = easier to guess without 3D).  The
   authoritative text-only-solver probe is opt-in and LLM-backed; when a
   ``solver`` callable is supplied it is measured too, otherwise reported as
   ``not_run``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

_WORD = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _instruction(task: dict[str, Any]) -> str:
    return str(task.get("instruction", ""))


# ---------------------------------------------------------------------------
# 1. Grounding validity
# ---------------------------------------------------------------------------

def grounding_metrics(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(tasks)
    grounded = 0
    entity_backed = 0  # grounded AND entities literally present in instruction
    for task in tasks:
        ag = task.get("asset_grounding", {}) or {}
        entities = [str(e) for e in (ag.get("verified_entities") or [])]
        is_grounded = ag.get("status") == "grounded" and bool(entities)
        if is_grounded:
            grounded += 1
            instr = _instruction(task).lower()
            if entities and all(e.lower() in instr for e in entities):
                entity_backed += 1
    denom = n or 1
    return {
        "n_tasks": n,
        "grounded_count": grounded,
        "grounded_rate": grounded / denom,
        "entity_backed_count": entity_backed,
        # Headline: fraction of the whole batch provably bound to real entities.
        "grounding_validity_rate": entity_backed / denom,
    }


# ---------------------------------------------------------------------------
# 2. Instruction diversity
# ---------------------------------------------------------------------------

def diversity_metrics(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(tasks)
    instructions = [_instruction(t) for t in tasks]
    unique_instructions = len(set(instructions))

    bigrams: set[tuple[str, str]] = set()
    total_bigrams = 0
    for instr in instructions:
        toks = _tokens(instr)
        for i in range(len(toks) - 1):
            bigrams.add((toks[i], toks[i + 1]))
            total_bigrams += 1

    entities: set[str] = set()
    for task in tasks:
        for ent in (task.get("asset_grounding", {}) or {}).get("verified_entities", []) or []:
            entities.add(str(ent))

    denom = n or 1
    return {
        "n_tasks": n,
        "unique_instruction_count": unique_instructions,
        "unique_instruction_rate": unique_instructions / denom,
        "distinct_bigram_ratio": (len(bigrams) / total_bigrams) if total_bigrams else 0.0,
        "distinct_grounded_entities": len(entities),
    }


# ---------------------------------------------------------------------------
# 3. Shortcut risk
# ---------------------------------------------------------------------------

def _enum_options(task: dict[str, Any]) -> list[int]:
    """Sizes of every enumerated answer field, if the answer is categorical."""
    schema = task.get("answer_schema", {}) or {}
    enums = schema.get("enums") or {}
    return [len(v) for v in enums.values() if isinstance(v, list) and v]


def shortcut_metrics(
    tasks: list[dict[str, Any]],
    solver: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Shortcut-solvability signals.

    ``solver`` (opt-in): given a task, returns True if a *text-only* attempt
    (no 3D interaction) produced the correct answer.  A good 3D-grounded
    question should be hard to solve this way, so a LOW rate is better.  When
    omitted, only the deterministic random-guess proxy is reported.
    """
    n = len(tasks)
    # Deterministic proxy: mean chance accuracy over categorical answers.
    guessables: list[float] = []
    for task in tasks:
        sizes = _enum_options(task)
        if sizes:
            # Independent fields → product of per-field chance.
            chance = 1.0
            for s in sizes:
                chance *= 1.0 / s
            guessables.append(chance)
    result: dict[str, Any] = {
        "n_tasks": n,
        "categorical_tasks": len(guessables),
        "mean_random_guess_accuracy": (sum(guessables) / len(guessables)) if guessables else None,
    }
    if solver is None:
        result["text_only_solve_rate"] = None
        result["text_only_probe"] = "not_run (supply an LLM solver to measure)"
    else:
        solved = sum(1 for t in tasks if solver(t))
        result["text_only_solve_rate"] = solved / (n or 1)
        result["text_only_solved_count"] = solved
        result["text_only_probe"] = "measured"
    return result


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def compute_quality_metrics(
    tasks: list[dict[str, Any]],
    solver: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    return {
        "grounding": grounding_metrics(tasks),
        "diversity": diversity_metrics(tasks),
        "shortcut": shortcut_metrics(tasks, solver=solver),
    }
