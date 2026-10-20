"""In-process adapter to sci3d_agent.run_agent."""
from __future__ import annotations
from typing import Callable
from sci3d_agent.agent import run_agent
from sci3d_agent.modes import get_profile
from sci3d_bench.scorers.llm_judge import LLMJudgeScorer


class Sci3DAgentAdapter:
    def __init__(
        self,
        llm_factory: Callable,
        kernel_factory: Callable,
        bridge_factory: Callable,
        scorer_llm,
        model: str,
        max_iters: int = 20,
        seed: int = 0,
    ):
        self._llm_factory = llm_factory
        self._kernel_factory = kernel_factory
        self._bridge_factory = bridge_factory
        self._scorer = LLMJudgeScorer(llm=scorer_llm, model=model)
        self._model = model
        self._max_iters = max_iters
        self._seed = seed

    def run(self, task, mode: str):
        profile = get_profile(mode)
        payload = self._build_payload(task, mode)
        return run_agent(
            profile=profile,
            initial_payload=payload,
            llm=self._llm_factory(),
            kernel=self._kernel_factory(),
            bridge=self._bridge_factory(),
            task_id=task.task_id,
            model=self._model,
            seed=self._seed,
            asset_path=task.asset.path,
            max_iters=self._max_iters,
        )

    def _build_payload(self, task, mode: str) -> list:
        msg = [{"role": "user", "content": f"Question: {task.question}\nAsset: {task.asset.path} ({task.asset.format})"}]
        if mode == "mode2_one_image" and task.prerendered_image and task.prerendered_image.path:
            msg.append({"role": "user", "content": f"[prerendered image: {task.prerendered_image.path}]"})
        return msg

    def score(self, task, trajectory):
        return self._scorer.score(task, trajectory)
