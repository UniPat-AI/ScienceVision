from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ScoreResult:
    value: float
    metric: str
    raw: dict = field(default_factory=dict)
    degraded: bool = False


class Scorer(Protocol):
    name: str
    def score(self, task, traj) -> ScoreResult: ...
