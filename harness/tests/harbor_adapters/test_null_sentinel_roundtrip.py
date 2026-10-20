"""The TOML null round trip: the writer's sentinel, the reader's restore, and
an end-to-end score of a null ground-truth field.

TOML has no null, so the tree builder writes a sentinel string wherever the
metadata carries ``None`` (an empty string would be a silent lie -- it is a
distinct value). The host verifier parses that metadata back and
``task_metadata_to_taskspec`` restores the sentinel to ``None``, so ``exact``
comparison credits an answer that also says null.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from harbor_adapters.sci3d._verify_translate import (
    NULL_SENTINEL,
    task_metadata_to_taskspec,
)
from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer
from scripts.harbor import build_harbor_tasks as gen


def _meta(gt_value: dict) -> dict:
    return {
        "domain": "architecture",
        "v_class": "V1",
        "asset_path": "/x/A.glb",
        "asset_format": "glb",
        "answer_schema": {"kind": "structured", "fields": sorted(gt_value)},
        "ground_truth": {"value": gt_value},
        "scorer": {"primary": "exact_match", "secondary": []},
    }


def test_the_writer_and_the_reader_agree_on_the_sentinel() -> None:
    assert gen._NULL_SENTINEL == NULL_SENTINEL


def test_none_renders_as_the_sentinel_and_an_empty_string_stays_itself() -> None:
    assert gen._toml_scalar(None) == f'"{NULL_SENTINEL}"'
    assert gen._toml_scalar("") == '""'


def test_a_null_ground_truth_field_survives_the_round_trip() -> None:
    rendered = gen._render_task_toml(
        {
            "task_id": "null-round-trip",
            "domain": "architecture",
            "v_class": "V1",
            "asset_path": "/x/A.glb",
            "asset_format": "glb",
            "assets": [],
            "weights": {"answer": 1.0, "process": 0.0},
            "answer_schema": {"kind": "structured", "fields": ["lower_surface"]},
            "ground_truth": {"value": {"lower_surface": None}},
            "scorer": {"primary": "exact_match", "secondary": []},
        },
        containerized=False,
    )
    assert f'lower_surface = "{NULL_SENTINEL}"' in rendered

    # What the host verifier reads back after toml parsing:
    meta = _meta({"lower_surface": NULL_SENTINEL})
    spec = task_metadata_to_taskspec(meta, task_id="null-round-trip", instruction="q")
    assert spec.ground_truth.value == {"lower_surface": None}


def test_an_answer_that_says_null_passes_and_an_empty_string_does_not() -> None:
    meta = _meta(
        {"level_difference_mm": 0, "lower_surface": NULL_SENTINEL, "same_level": True}
    )
    spec = task_metadata_to_taskspec(meta, task_id="null-round-trip", instruction="q")
    scorer = DeterministicAnswerScorer()
    good = scorer.score(
        spec,
        SimpleNamespace(
            final_answer={"level_difference_mm": 0, "lower_surface": None, "same_level": True}
        ),
    )
    assert good.value == pytest.approx(1.0)
    bad = scorer.score(
        spec,
        SimpleNamespace(
            final_answer={"level_difference_mm": 0, "lower_surface": "", "same_level": True}
        ),
    )
    assert bad.value == pytest.approx(2 / 3)


def test_the_restore_walks_nested_lists_and_dicts() -> None:
    meta = _meta({"panels": [{"shots": [NULL_SENTINEL, 1]}]})
    spec = task_metadata_to_taskspec(meta, task_id="null-round-trip", instruction="q")
    assert spec.ground_truth.value == {"panels": [{"shots": [None, 1]}]}


def test_a_string_that_merely_contains_the_sentinel_is_untouched() -> None:
    meta = _meta({"note": f"pre{NULL_SENTINEL}post"})
    spec = task_metadata_to_taskspec(meta, task_id="null-round-trip", instruction="q")
    assert spec.ground_truth.value == {"note": f"pre{NULL_SENTINEL}post"}
