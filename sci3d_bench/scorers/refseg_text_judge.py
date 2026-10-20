"""LLM-judge scorer for M3D-RefSeg text track.

Uses a mask-text anchor (canonical region description) so the judge
can score correctness against the unambiguous referent rather than
relying on fuzzy match against the gold sentence."""
from __future__ import annotations

from sci3d_bench.scorers.base import ScoreResult


_JUDGE_TOOL = {
    "name": "_judge",
    "description": "Emit a score in {0.0, 0.5, 1.0} and a one-sentence rationale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {"type": "number", "enum": [0.0, 0.5, 1.0]},
            "rationale": {"type": "string"},
        },
        "required": ["score", "rationale"],
    },
}


_JUDGE_SYSTEM = (
    "You are evaluating a model's free-text answer to a 3D medical referring-"
    "expression segmentation question. The CANONICAL REFERENT is the unambiguous "
    "anatomical structure the model is supposed to identify. Score the AGENT ANSWER "
    "via the _judge tool using exactly one of these values:\n"
    "  1.0  — names the correct referent and (ideally) localizes it. Confident.\n"
    "  0.5  — partial: right organ wrong subtype, or right region but no specific "
    "naming, or hedged/vague language.\n"
    "  0.0  — wrong referent, denial of presence, or non-answer.\n"
    "Ignore the [SEG] token and any markup. Judge only the medical content. "
    "One-sentence rationale."
)


class RefSegLLMJudge:
    """Scorer for M3D-RefSeg text track."""

    name = "refseg_judge"

    def __init__(self, llm, model: str = "claude-sonnet-4-6"):
        self._llm = llm
        self._model = model

    def score(self, task, traj) -> ScoreResult:
        final = traj.final_answer
        candidate = final.get("text") if isinstance(final, dict) else final
        degraded_input = candidate is None or candidate == ""

        gt_value = getattr(task.ground_truth, "value", None) if task.ground_truth else {}
        if not isinstance(gt_value, dict):
            gt_value = {}
        mask_text = gt_value.get("mask_text", "")
        gold_answer = gt_value.get("answer_text", "")

        prompt = (
            f"QUESTION: {task.question}\n"
            f"CANONICAL REFERENT (mask description): {mask_text}\n"
            f"GOLD ANSWER (reference phrasing): {gold_answer}\n"
            f"AGENT ANSWER: {candidate!r}\n"
        )
        resp = self._llm.complete(
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            tools=[_JUDGE_TOOL],
            model=self._model,
        )

        if resp.tool_call is None or resp.tool_call.name != "_judge":
            return ScoreResult(
                value=0.0, metric=self.name,
                raw={"error": "no_tool_call"}, degraded=True,
            )

        score = float(resp.tool_call.args.get("score", 0.0))
        rationale = resp.tool_call.args.get("rationale", "")
        return ScoreResult(
            value=score, metric=self.name,
            raw={
                "rationale": rationale,
                "tokens_in": getattr(resp, "input_tokens", 0),
                "tokens_out": getattr(resp, "output_tokens", 0),
            },
            degraded=degraded_input,
        )
