"""AgentProtocol — single contract crossing the layer boundary."""
from __future__ import annotations
from typing import Protocol


class AgentProtocol(Protocol):
    def run(self, task, mode: str): ...
    def score(self, task, trajectory): ...
