"""Sci3dAgent — a Harbor BaseAgent that drives the sci3d_agent loop.

First-step adapter (host-run route): the sci3d_agent package is a host-side
Python agent (it imports an LLM client + a local kernel/viewer bridge), not a
CLI installed into the task container. So ``run()`` executes the sci3d loop
*in this process* and writes the answer + trajectory to ``logs_dir`` and into
the Harbor ``AgentContext``. The ``environment`` is not used for execution in
this route; a later "installed/containerized" route can subclass and override
``run()`` to call ``environment.exec("sci3d-agent ...")`` instead — the public
contract (name/version/setup/run + context population) stays identical.

Register with Harbor without editing Harbor source:

    harbor run \
        --agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent \
        --model anthropic/claude-haiku-4-5-20251001 \
        --ak mode=mode1_no_vision \
        ...

Agent kwargs (via ``--ak key=value``):
    mode        sci3d ModeProfile name (default: mode1_no_vision)
    max_iters   loop iteration cap (default: 20)
    seed        RNG seed (default: 0)
    asset_path  3D asset path, if not carried in the instruction
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from ._instruction import parse_instruction
from ._translate import record_to_context_fields, record_to_trajectory

_ANSWER_FILE = "sci3d_answer.json"
_TRAJECTORY_FILE = "sci3d_trajectory.json"


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("sciencevision")
    except Exception:
        return "unknown"


class Sci3dAgent(BaseAgent):
    """Runs the sci3d_agent loop as a Harbor agent (host-run route)."""

    SUPPORTS_ATIF: bool = True

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        mode: str = "mode1_no_vision",
        max_iters: int | str = 20,
        seed: int | str = 0,
        asset_path: str | None = None,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._mode = mode
        self._max_iters = int(max_iters)
        self._seed = int(seed)
        self._asset_path = asset_path

    @staticmethod
    def name() -> str:
        return "sci3d"

    def version(self) -> str | None:
        return _package_version()

    async def setup(self, environment: BaseEnvironment) -> None:
        """No container setup in the host-run route.

        The sci3d loop runs in this process. Dependency availability
        (renderer/SAM service) is handled by sci3d_agent's own graceful
        degradation (``renderer_skipped`` envelopes when GL/SAM are absent),
        so there is nothing to install into the environment here.
        """
        return

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # Lazy imports: keep harbor-side import light and avoid importing the
        # sci3d loop (and its heavy deps) until we actually run.
        from sci3d_agent.agent import run_agent
        from sci3d_agent.kernel import FakeKernel
        from sci3d_agent.llm_client import build_client, FakeLLMClient
        from sci3d_agent.modes import get_profile
        from sci3d_agent.viewer_bridge import StubViewerBridge

        import os

        parsed = parse_instruction(instruction, fallback_asset=self._asset_path)
        question, asset_path = parsed.question, parsed.asset_path

        model = self._parsed_model_name or self.model_name or "claude-haiku-4-5-20251001"

        if os.environ.get("SCI3D_USE_FAKE_LLM") == "1":
            llm = FakeLLMClient(
                [{"tool": "finish", "args": {"answer": "fake"},
                  "input_tokens": 1, "output_tokens": 1}]
            )
        else:
            llm = build_client()

        profile = get_profile(self._mode)
        payload = [{"role": "user",
                    "content": f"Question: {question}\nAsset: {asset_path}"}]

        record = run_agent(
            profile=profile,
            initial_payload=payload,
            llm=llm,
            kernel=FakeKernel(),
            bridge=StubViewerBridge(),
            task_id=asset_path or "harbor-task",
            model=model,
            seed=self._seed,
            asset_path=asset_path,
            max_iters=self._max_iters,
            image_out_dir=self.logs_dir / "images",
        )

        self._populate_context(context, record)
        self._write_artifacts(record)

    # ── internals ──────────────────────────────────────────────────────── #

    def _populate_context(self, context: AgentContext, record: Any) -> None:
        fields = record_to_context_fields(record)
        context.n_input_tokens = fields["n_input_tokens"]
        context.n_output_tokens = fields["n_output_tokens"]
        context.cost_usd = fields["cost_usd"]
        meta = dict(context.metadata or {})
        meta["sci3d_final_answer"] = getattr(record, "final_answer", None)
        meta["sci3d_timeout"] = bool(getattr(record, "timeout", False))
        meta["sci3d_error"] = getattr(record, "error", None)
        context.metadata = meta

    def _write_artifacts(self, record: Any) -> None:
        """Write answer + Harbor trajectory to logs_dir for the verifier."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        answer = getattr(record, "final_answer", None)
        (self.logs_dir / _ANSWER_FILE).write_text(
            json.dumps({"answer": answer}, ensure_ascii=False, indent=2)
        )
        traj = record_to_trajectory(
            record,
            session_id=str(getattr(record, "task_id", "sci3d")),
            agent_version=self.version() or "unknown",
        )
        (self.logs_dir / _TRAJECTORY_FILE).write_text(
            traj.model_dump_json(indent=2)
        )
