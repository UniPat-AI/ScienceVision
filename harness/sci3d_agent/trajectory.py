"""TrajectoryRecord + recorder. Persisted JSON is the post-run artifact."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
import json

Mode = Literal[
    "mode1_no_vision",
    "mode1_seg3d_volume",
    "mode1_seg3d_mesh",
    "mode2_one_image",
    "mode3_free_visual",
    "mode3_seg2d",
    "mode4_mcp",
]

@dataclass(frozen=True)
class StepRecord:
    iter: int
    tool: str
    args: dict
    result: dict
    latency_ms: int
    # Reasoning text the model emitted alongside this tool call (response.text).
    # Required by the verify pipeline so a trajectory captures "operation +
    # per-step thinking" and is replayable. Optional + defaulted so existing
    # callers/tests are unaffected.
    reasoning: Optional[str] = None
    # Anthropic tool_use id this step's tool_result is keyed to (multi-turn).
    tool_use_id: Optional[str] = None
    # Relative paths to any images this step's tool returned, after the agent
    # loop persists them to disk (base64 is NOT kept inline in the trajectory).
    images: list = field(default_factory=list)

@dataclass(frozen=True)
class ScoreBundle:
    v_class_score: Optional[float] = None
    v_class_metric: Optional[str] = None
    judge_score: Optional[float] = None
    judge_rationale: Optional[str] = None
    degraded: bool = False

@dataclass(frozen=True)
class MetaBundle:
    model: str
    seed: int
    cost_estimate_usd: Optional[float] = None
    token_counts: dict = field(default_factory=dict)

@dataclass(frozen=True)
class TrajectoryRecord:
    schema_version: str
    task_id: str
    mode: Mode
    timestamp: str
    steps: list
    final_answer: Optional[Any]
    timeout: bool
    error: Optional[str]
    violations: list
    scores: ScoreBundle
    meta: MetaBundle

    def dump_json(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, default=str))


class TrajectoryRecorder:
    def __init__(self, task_id: str, mode: Mode, model: str, seed: int):
        self.task_id = task_id
        self.mode = mode
        self.model = model
        self.seed = seed
        self.steps: list = []
        self.final_answer: Optional[Any] = None
        self.timeout = False
        self.error: Optional[str] = None
        self.violations: list = []
        self.scores = ScoreBundle()
        self.token_counts: dict = {}
        self.cost_estimate_usd: Optional[float] = None

    def record_step(
        self,
        iter_: int,
        tool: str,
        args: dict,
        result: dict,
        latency_ms: int,
        reasoning: Optional[str] = None,
        tool_use_id: Optional[str] = None,
        images: Optional[list] = None,
    ) -> None:
        self.steps.append(
            StepRecord(
                iter=iter_,
                tool=tool,
                args=args,
                result=result,
                latency_ms=latency_ms,
                reasoning=reasoning,
                tool_use_id=tool_use_id,
                images=list(images) if images else [],
            )
        )

    def record_final(self, answer: Any) -> None:
        self.final_answer = answer

    def record_timeout(self) -> None:
        self.timeout = True

    def record_error(self, msg: str) -> None:
        self.error = msg

    def record_violation(self, flag: str) -> None:
        self.violations.append(flag)

    def set_scores(self, scores: ScoreBundle) -> None:
        self.scores = scores

    def build(self) -> TrajectoryRecord:
        return TrajectoryRecord(
            schema_version="1.1",
            task_id=self.task_id,
            mode=self.mode,
            timestamp=datetime.now(timezone.utc).isoformat(),
            steps=list(self.steps),
            final_answer=self.final_answer,
            timeout=self.timeout,
            error=self.error,
            violations=list(self.violations),
            scores=self.scores,
            meta=MetaBundle(
                model=self.model,
                seed=self.seed,
                cost_estimate_usd=self.cost_estimate_usd,
                token_counts=dict(self.token_counts),
            ),
        )
