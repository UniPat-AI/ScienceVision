"""LLM-judge scorer. Scoring-v0 universal; long-term primary for V1/V5/V6."""
from __future__ import annotations
import json
from sci3d_bench.scorers.base import Scorer, ScoreResult

JUDGE_TOOL = {
    "name": "_judge",
    "description": "Emit a 0..1 score and a one-sentence rationale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
        },
        "required": ["score", "rationale"],
    },
}

JUDGE_SYSTEM = (
    "You are an evaluator. Compare the agent's final answer against the ground truth. "
    "Emit a score in [0,1] using the _judge tool: 1.0 = exactly correct, 0.0 = wrong. "
    "Allow partial credit for partially-correct structured answers. One-sentence rationale."
)


class LLMJudgeScorer:
    name = "llm_judge"

    def __init__(self, llm, model: str = "claude-haiku-4-5-20251001"):
        self._llm = llm
        self._model = model

    def score(self, task, traj) -> ScoreResult:
        degraded = traj.final_answer is None or task.answer_schema.kind == "structured" and not isinstance(traj.final_answer, dict)
        prompt = (
            f"QUESTION: {task.question}\n"
            f"GROUND TRUTH: {json.dumps(getattr(task.ground_truth, 'value', None), default=str)}\n"
            f"AGENT ANSWER: {json.dumps(traj.final_answer, default=str)}\n"
            f"V-CLASS: {task.v_class}\n"
        )
        resp = self._llm.complete(
            messages=[{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}],
            tools=[JUDGE_TOOL],
            model=self._model,
        )
        if resp.tool_call is None or resp.tool_call.name != "_judge":
            return ScoreResult(value=0.0, metric="judge", raw={"error": "no_tool_call"}, degraded=True)
        score = float(resp.tool_call.args.get("score", 0.0))
        rationale = resp.tool_call.args.get("rationale", "")
        return ScoreResult(value=score, metric="judge", raw={"rationale": rationale, "tokens_in": resp.input_tokens, "tokens_out": resp.output_tokens}, degraded=degraded)
