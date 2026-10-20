"""Sci3dVerifier — a Harbor BaseVerifier that scores a sci3d agent run.

Host-run route (mirror of ``agent.py``): the agent already wrote
``sci3d_answer.json`` + ``sci3d_trajectory.json`` into the trial's agent dir.
``verify()`` reads those, rebuilds the sci3d ``TaskSpec`` from the task's
``[metadata]`` table, computes deterministic binary rubric items and their
weighted ``answer_score``, and reports process signals separately. ``reward``
is exactly ``answer_score``. Optional LLM judges are emitted only as
``shadow_*`` diagnostics.

Register with Harbor without editing Harbor source:

    harbor run \
        --agent-import-path harbor_adapters.sci3d.agent:Sci3dAgent \
        --verifier-import-path harbor_adapters.sci3d.verifier:Sci3dVerifier \
        --model anthropic/claude-haiku-4-5-20251001 \
        ...

rewards keys returned:
    answer_score         deterministic weighted binary-rubric score
    shadow_*             optional non-authoritative judge diagnostics
    process_*            closed-loop signals in {0,1} (opened_scene, rendered,
                         tool_policy, finished, metadata_only)
    process_compliance   deterministic tool-policy diagnostic
    reward               identical to answer_score

An audit file ``verifier/sci3d_scores.json`` records per-item verdicts,
eligibility, parse errors, shadows, and the domain.
"""
from __future__ import annotations

import json
from typing import Any

from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier

from ._score_core import AUDIT_FILE as _AUDIT_FILE
from ._score_core import score_rewards


class Sci3dVerifier(BaseVerifier):
    """Scores a sci3d run into Harbor rewards (host-run route)."""

    def __init__(self, *, judge_model: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._judge_model = judge_model

    async def verify(self) -> VerifierResult:
        meta = self._task_metadata()
        task_id = getattr(self.task, "short_name", None) or getattr(
            self.task, "name", "harbor-task"
        )
        instruction = getattr(self.task, "instruction", "") or ""

        # Single source of truth: the containerized route (tests/score.py)
        # calls this exact same function. See harbor_adapters/sci3d/_score_core.
        rewards, audit = score_rewards(
            meta,
            task_id=str(task_id),
            instruction=instruction,
            agent_dir=self.trial_paths.agent_dir,
            judge_model=self._judge_model,
        )

        self._write_audit(audit)
        return VerifierResult(rewards=rewards)

    # ── internals ──────────────────────────────────────────────────────── #
    def _task_metadata(self) -> dict:
        """Read the task's [metadata] table.

        Harbor places it on ``config.metadata``; we also try
        ``config.task.metadata`` for forward-compat. A non-empty dict wins; an
        empty dict on one location must not shadow a populated one on the other.
        """
        cfg = getattr(self.task, "config", None)
        if cfg is None:
            return {}
        cfg_meta = getattr(cfg, "metadata", None)
        if isinstance(cfg_meta, dict) and cfg_meta:
            return cfg_meta
        pkg = getattr(cfg, "task", None)
        pkg_meta = getattr(pkg, "metadata", None) if pkg else None
        if isinstance(pkg_meta, dict) and pkg_meta:
            return pkg_meta
        return cfg_meta if isinstance(cfg_meta, dict) else {}

    def _write_audit(self, audit: dict) -> None:
        try:
            out = self.trial_paths.verifier_dir
            out.mkdir(parents=True, exist_ok=True)
            (out / _AUDIT_FILE).write_text(
                json.dumps(audit, ensure_ascii=False, indent=2, default=str)
            )
        except Exception:
            # Audit is best-effort; never let it fail the verifier.
            pass
