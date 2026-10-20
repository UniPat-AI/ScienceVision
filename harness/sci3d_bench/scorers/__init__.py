"""Scorers for sci3d_bench."""
from sci3d_bench.scorers.base import ScoreResult, Scorer
from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer
from sci3d_bench.scorers.llm_judge import LLMJudgeScorer
from sci3d_bench.scorers.mask_dice import MaskDiceScorer, compute_dice
from sci3d_bench.scorers.refseg_text_judge import RefSegLLMJudge

__all__ = [
    "ScoreResult",
    "Scorer",
    "DeterministicAnswerScorer",
    "LLMJudgeScorer",
    "MaskDiceScorer",
    "compute_dice",
    "RefSegLLMJudge",
]
