"""Tests for P2: distractor generation and cross-asset instruction dedup."""

from __future__ import annotations

import json
from typing import Any

from sciencevision_taskgen.distractors import (
    distractors_from_enum,
    make_llm_distractor_generator,
)
from sciencevision_taskgen.llm import ChatResult
from sciencevision_taskgen.pipeline.builder_stage import BuilderStage


# --- deterministic enum distractors ----------------------------------------

_SCHEMA = {
    "kind": "sub_location_plus_pathology",
    "enums": {
        "sub_compartment": ["wall", "lumen", "surrounding_tissue"],
    },
}


def test_distractors_from_enum_excludes_correct():
    d = distractors_from_enum(_SCHEMA, "wall")
    assert set(d) == {"lumen", "surrounding_tissue"}
    assert "wall" not in d


def test_distractors_from_enum_handles_dict_answer():
    d = distractors_from_enum(_SCHEMA, {"sub_compartment": "lumen"})
    assert set(d) == {"wall", "surrounding_tissue"}


def test_distractors_from_enum_respects_cap():
    d = distractors_from_enum(_SCHEMA, "wall", max_distractors=1)
    assert len(d) == 1


def test_distractors_from_enum_empty_without_enums():
    assert distractors_from_enum({"kind": "value"}, "x") == []


# --- LLM distractor generator (opt-in, faked) -------------------------------

class _FakeClient:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete_with_retry(self, *a: Any, **k: Any) -> ChatResult:
        return ChatResult(content=self._content, latency_ms=1.0,
                          prompt_tokens=1, completion_tokens=1, reasoning_tokens=0)


def _task() -> dict:
    return {
        "task_id": "t",
        "instruction": "Where does the focus sit?",
        "answer_schema": _SCHEMA,
        "asset": {"name": "s0146"},
        "ground_truth": {"value": "wall"},
    }


def test_llm_distractors_used_when_present():
    gen = make_llm_distractor_generator(
        _FakeClient(json.dumps({"distractors": ["intramural plaque", "wall"]})), "m"
    )
    out = gen(_task(), n=3)
    # "wall" (the correct answer) is filtered out.
    assert out == ["intramural plaque"]


def test_llm_distractors_fall_back_to_enum_on_failure():
    gen = make_llm_distractor_generator(_FakeClient("garbage"), "m")
    out = gen(_task(), n=3)
    assert set(out) == {"lumen", "surrounding_tissue"}


# --- cross-asset dedup ------------------------------------------------------

def _asset(name: str) -> dict:
    return {
        "asset_id": f"pointcloud:{name}", "name": name,
        "path": f"datasets/pc/{name}.las", "repo_path": f"datasets/pc/{name}.las",
        "viewer": "pointcloud", "format": "las", "thumb": None, "exists_on_disk": True,
    }


def test_dedup_drops_identical_instructions():
    # pointcloud/V1 is an asset-agnostic template (no requires) → both assets
    # render the same instruction; dedup should keep only one.
    plan = [
        {"domain": "pointcloud", "v_class": "V1", "replica": 0, "status": "ready"},
        {"domain": "pointcloud", "v_class": "V1", "replica": 1, "status": "ready"},
    ]
    catalog = {"domains": {"pointcloud": [_asset("a"), _asset("b")]}}

    no_dedup = BuilderStage({}).build_tasks(plan, catalog)
    with_dedup = BuilderStage({"builder": {"dedup_instructions": True}}).build_tasks(plan, catalog)

    instrs = {t["instruction"] for t in no_dedup}
    if len(no_dedup) > 1 and len(instrs) == 1:
        # The template is indeed asset-agnostic → dedup collapses to 1.
        assert len(with_dedup) == 1
    else:
        # Template is grounded/asset-specific → nothing to dedup.
        assert len(with_dedup) == len(no_dedup)
