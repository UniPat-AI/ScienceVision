"""Tests for the question-quality metrics scoreboard."""

from __future__ import annotations

from sciencevision_taskgen.metrics import (
    compute_quality_metrics,
    diversity_metrics,
    grounding_metrics,
    shortcut_metrics,
)


def _grounded_task(instr: str, entities: list[str]) -> dict:
    return {
        "instruction": instr,
        "asset_grounding": {"status": "grounded", "verified_entities": entities},
        "answer_schema": {"kind": "categorical", "enums": {"x": ["a", "b"]}},
    }


def _blind_task(instr: str) -> dict:
    return {
        "instruction": instr,
        "asset_grounding": {"status": "unchecked", "verified_entities": []},
        "answer_schema": {"kind": "categorical", "enums": {"x": ["a", "b", "c", "d"]}},
    }


def test_grounding_validity():
    tasks = [
        _grounded_task("Inspect the heme group of haemoglobin.", ["heme group", "haemoglobin"]),
        _grounded_task("Inspect the retinal chromophore of rhodopsin.", ["retinal chromophore"]),
        _blind_task("Inspect the cofactor of the molecule."),
    ]
    g = grounding_metrics(tasks)
    assert g["grounded_count"] == 2
    # 2 of 3 tasks are grounded AND their entities appear in the instruction.
    assert g["entity_backed_count"] == 2
    assert abs(g["grounding_validity_rate"] - 2 / 3) < 1e-9


def test_grounding_validity_zero_for_blind_batch():
    tasks = [_blind_task("Inspect the cofactor of the molecule.") for _ in range(5)]
    g = grounding_metrics(tasks)
    assert g["grounded_rate"] == 0.0
    assert g["grounding_validity_rate"] == 0.0


def test_grounding_flags_status_without_entity_in_text():
    # Grounded status but the declared entity is NOT in the instruction → not entity-backed.
    tasks = [_grounded_task("Inspect the molecule.", ["heme group"])]
    g = grounding_metrics(tasks)
    assert g["grounded_count"] == 1
    assert g["entity_backed_count"] == 0


def test_diversity():
    tasks = [
        _grounded_task("Inspect the heme group of haemoglobin.", ["heme group"]),
        _grounded_task("Inspect the heme group of haemoglobin.", ["heme group"]),  # dup
        _grounded_task("Measure the ring opening angle of Saturn.", ["Saturn"]),
    ]
    d = diversity_metrics(tasks)
    assert d["unique_instruction_count"] == 2
    assert abs(d["unique_instruction_rate"] - 2 / 3) < 1e-9
    assert d["distinct_grounded_entities"] == 2  # {heme group, Saturn}


def test_shortcut_proxy_and_solver():
    tasks = [_grounded_task("q", ["e"]), _blind_task("q2")]
    s = shortcut_metrics(tasks)
    # First: 1/2 chance; second: 1/4 chance → mean 0.375
    assert abs(s["mean_random_guess_accuracy"] - 0.375) < 1e-9
    assert s["text_only_solve_rate"] is None
    assert s["text_only_probe"].startswith("not_run")

    # With a solver that "solves" every task text-only → high (bad) rate.
    s2 = shortcut_metrics(tasks, solver=lambda t: True)
    assert s2["text_only_solve_rate"] == 1.0
    assert s2["text_only_probe"] == "measured"


def test_compute_aggregate_shape():
    qm = compute_quality_metrics([_grounded_task("q", ["e"])])
    assert set(qm) == {"grounding", "diversity", "shortcut"}


def test_empty_batch_is_safe():
    qm = compute_quality_metrics([])
    assert qm["grounding"]["grounding_validity_rate"] == 0.0
    assert qm["diversity"]["unique_instruction_rate"] == 0.0
    assert qm["shortcut"]["mean_random_guess_accuracy"] is None
