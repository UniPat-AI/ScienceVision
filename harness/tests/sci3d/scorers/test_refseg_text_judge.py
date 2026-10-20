"""Unit tests for RefSegLLMJudge — Claude judge with mask-text anchor."""
from __future__ import annotations

import pytest

from sci3d_agent.llm_client import FakeLLMClient
from sci3d_agent.trajectory import MetaBundle, ScoreBundle, TrajectoryRecord
from sci3d_bench.scorers.refseg_text_judge import RefSegLLMJudge
from sci3d_bench.task_schema import (
    AnswerSchema, AssetRef, GroundTruth, Provenance, ScorerSpec, TaskSpec,
)


def _task():
    return TaskSpec(
        schema_version="1.0", task_id="m3d-refseg-test-002",
        v_class="V3",
        question="Please identify and segment any postoperative stoma that may be present.",
        asset=AssetRef(path="ct.nii.gz", format="nii.gz"),
        prerendered_image=None,
        answer_schema=AnswerSchema(kind="freeform"),
        ground_truth=GroundTruth(value={
            "mask_path": "ignored.nii.gz", "mask_id": 2,
            "mask_text": "postoperative stoma in upper abdomen",
            "answer_text": "Sure, a postoperative stoma can be seen in the [SEG].",
        }),
        scorer=ScorerSpec(primary="refseg_text_judge"),
        provenance=Provenance(
            source_dataset="m3d-refseg", source_qid=None,
            lifted_at="2026-05-21", lifter_version="manual",
        ),
    )


def _traj(text):
    return TrajectoryRecord(
        schema_version="1.0", task_id="m3d-refseg-test-002",
        mode="mode3_free_visual", timestamp="2026-05-21T00:00:00+00:00",
        steps=[], final_answer={"text": text}, timeout=False, error=None,
        violations=[], scores=ScoreBundle(),
        meta=MetaBundle(model="claude-sonnet-4-6", seed=0),
    )


@pytest.mark.unit
def test_refseg_judge_scores_correct_referent_high():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 1.0, "rationale": "names stoma"},
         "input_tokens": 60, "output_tokens": 8},
    ])
    scorer = RefSegLLMJudge(llm=fake)
    res = scorer.score(_task(), _traj("Yes, I see a postoperative stoma in the upper abdomen."))
    assert res.value == 1.0
    assert res.metric == "refseg_judge"
    assert "stoma" in res.raw["rationale"]
    assert res.degraded is False


@pytest.mark.unit
def test_refseg_judge_scores_partial_for_vague_answer():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 0.5, "rationale": "vague localization"}},
    ])
    scorer = RefSegLLMJudge(llm=fake)
    res = scorer.score(_task(), _traj("There seems to be something in the upper abdomen."))
    assert res.value == 0.5


@pytest.mark.unit
def test_refseg_judge_scores_zero_for_wrong_referent():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 0.0, "rationale": "names lung instead"}},
    ])
    scorer = RefSegLLMJudge(llm=fake)
    res = scorer.score(_task(), _traj("I see a lung nodule."))
    assert res.value == 0.0


@pytest.mark.unit
def test_refseg_judge_degraded_when_no_tool_call():
    fake = FakeLLMClient(responses=[
        {"tool": None, "args": {}},  # malformed; no _judge tool call
    ])
    scorer = RefSegLLMJudge(llm=fake)
    res = scorer.score(_task(), _traj("anything"))
    assert res.degraded is True
    assert res.value == 0.0


@pytest.mark.unit
def test_refseg_judge_degraded_when_final_answer_missing():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 0.0, "rationale": "no answer"}},
    ])
    scorer = RefSegLLMJudge(llm=fake)
    traj = TrajectoryRecord(
        schema_version="1.0", task_id="t", mode="mode1_no_vision",
        timestamp="2026-05-21T00:00:00+00:00", steps=[],
        final_answer=None, timeout=False, error=None, violations=[],
        scores=ScoreBundle(), meta=MetaBundle(model="m", seed=0),
    )
    res = scorer.score(_task(), traj)
    assert res.degraded is True
