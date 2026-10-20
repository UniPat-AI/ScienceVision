import pytest
from sci3d_bench.scorers.base import Scorer, ScoreResult
from sci3d_bench.scorers.llm_judge import LLMJudgeScorer
from sci3d_bench.task_schema import TaskSpec, AssetRef, GroundTruth, AnswerSchema, ScorerSpec, Provenance
from sci3d_agent.trajectory import TrajectoryRecord, ScoreBundle, MetaBundle
from sci3d_agent.llm_client import FakeLLMClient

def _build_task(gt_value=42):
    return TaskSpec(
        schema_version="1.0", task_id="t1", v_class="V6", question="how many?",
        asset=AssetRef(path="x.obj", format="obj"), prerendered_image=None,
        answer_schema=AnswerSchema(kind="structured", fields={"count": "int"}),
        ground_truth=GroundTruth(value=gt_value),
        scorer=ScorerSpec(primary="llm_judge"),
        provenance=Provenance(source_dataset="seed", source_qid=None, lifted_at="2026-05-18", lifter_version="manual"),
    )

def _build_traj(answer):
    return TrajectoryRecord(
        schema_version="1.0", task_id="t1", mode="mode1_no_vision",
        timestamp="2026-05-18T00:00:00+00:00", steps=[], final_answer=answer,
        timeout=False, error=None, violations=[],
        scores=ScoreBundle(), meta=MetaBundle(model="m", seed=0),
    )

@pytest.mark.unit
def test_llm_judge_returns_high_score_for_correct_answer():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 1.0, "rationale": "correct"}, "input_tokens": 50, "output_tokens": 5},
    ])
    scorer = LLMJudgeScorer(llm=fake, model="claude-haiku-4-5")
    result = scorer.score(_build_task(42), _build_traj(42))
    assert result.value == 1.0
    assert result.metric == "judge"
    assert "correct" in result.raw["rationale"]

@pytest.mark.unit
def test_llm_judge_returns_low_score_for_wrong_answer():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 0.0, "rationale": "wrong"}},
    ])
    scorer = LLMJudgeScorer(llm=fake, model="claude-haiku-4-5")
    result = scorer.score(_build_task(42), _build_traj(7))
    assert result.value == 0.0

@pytest.mark.unit
def test_llm_judge_marks_degraded_when_answer_missing():
    fake = FakeLLMClient(responses=[
        {"tool": "_judge", "args": {"score": 0.0, "rationale": "no answer"}},
    ])
    scorer = LLMJudgeScorer(llm=fake, model="claude-haiku-4-5")
    result = scorer.score(_build_task(42), _build_traj(None))
    assert result.degraded is True
