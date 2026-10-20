"""Instantiate sci3d scorers by name, with per-domain rubric injection.

The verifier names a *primary* scorer (and optional *secondary* ones) in task
metadata; this module maps those names to instances from
``sci3d_bench.scorers``. The only behavioral addition over the raw scorers is
``DomainJudge``: a thin LLM-judge variant whose system prompt carries a
domain-specific rubric, so the same V-class judging gets domain context
without touching the scorer package.

LLM clients:
- ``SCI3D_USE_FAKE_LLM=1`` -> ``FakeLLMClient`` (tests / offline). The caller
  supplies the canned responses; here we only build it when no llm is passed.
- otherwise the real client via ``sci3d_agent.llm_client.build_client``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_RUBRICS_DIR = Path(__file__).parent / "rubrics"


def load_rubric(domain: str | None) -> str:
    """Return the rubric text for ``domain``, falling back to ``_default``."""
    name = (domain or "").strip().lower()
    candidate = _RUBRICS_DIR / f"{name}.md" if name else None
    if candidate and candidate.exists():
        return candidate.read_text().strip()
    return (_RUBRICS_DIR / "_default.md").read_text().strip()


class DomainJudge:
    """LLM-judge that injects a per-domain rubric into the system prompt.

    Mirrors ``sci3d_bench.scorers.llm_judge.LLMJudgeScorer.score`` but swaps in
    a domain-aware system prompt. Keeps ``name = "llm_judge"`` so its reward
    lands on the same key as the plain judge.
    """

    name = "llm_judge"

    def __init__(self, llm, *, model: str, domain: str, rubric_text: str):
        self._llm = llm
        self._model = model
        self._domain = domain
        self._rubric = rubric_text

    def _system_prompt(self) -> str:
        from sci3d_bench.scorers.llm_judge import JUDGE_SYSTEM

        return (
            f"{JUDGE_SYSTEM}\n\n"
            f"DOMAIN RUBRIC (domain={self._domain or 'general'}):\n{self._rubric}"
        )

    def score(self, task: Any, traj: Any):
        from sci3d_bench.scorers.base import ScoreResult
        from sci3d_bench.scorers.llm_judge import JUDGE_TOOL

        degraded = traj.final_answer is None
        prompt = (
            f"QUESTION: {task.question}\n"
            f"GROUND TRUTH: {json.dumps(getattr(task.ground_truth, 'value', None), default=str)}\n"
            f"AGENT ANSWER: {json.dumps(traj.final_answer, default=str)}\n"
            f"V-CLASS: {task.v_class}\n"
        )
        resp = self._llm.complete(
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            tools=[JUDGE_TOOL],
            model=self._model,
        )
        if resp.tool_call is None or resp.tool_call.name != "_judge":
            return ScoreResult(
                value=0.0, metric="judge", raw={"error": "no_tool_call"}, degraded=True
            )
        score = float(resp.tool_call.args.get("score", 0.0))
        rationale = resp.tool_call.args.get("rationale", "")
        return ScoreResult(
            value=score,
            metric="judge",
            raw={
                "rationale": rationale,
                "domain": self._domain,
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            },
            degraded=degraded,
        )


_TRAJ_JUDGE_SYSTEM = """You are grading the PROCESS of a scientific 3D-analysis \
agent — the trajectory of steps it took, not just its final answer. There is no \
ground-truth trajectory; judge whether the path is scientifically sound for the \
task and domain.

Reward:
- Inspecting the actual asset (loading the volume/mesh, reading real values) \
rather than guessing from the prompt.
- A coherent sequence: the agent's actions build toward the answer and react to \
what it observed.
- Respecting the tool policy (no forbidden tools; using the asset, not a \
metadata/text shortcut where the task forbids it).
- Committing to an answer (the run should converge, not stall).

Penalize: hand-waving with no real inspection, ignoring observations, looping \
without progress, or never committing. Call _judge with score in [0,1] and a \
one-sentence rationale."""


class TrajectoryJudge:
    """LLM-judge over the trajectory steps (the process), not the answer.

    There is no process ground truth; this is a soft, rubric-guided signal that
    grades whether the agent's path was scientifically sound. Lands on the
    ``process_judge`` reward key so it sits beside the binary ``process_*``
    closed-loop signals but carries a continuous score.
    """

    name = "process_judge"

    def __init__(self, llm, *, model: str, domain: str, rubric_text: str):
        self._llm = llm
        self._model = model
        self._domain = domain
        self._rubric = rubric_text

    def _system_prompt(self) -> str:
        return (
            f"{_TRAJ_JUDGE_SYSTEM}\n\n"
            f"DOMAIN RUBRIC (domain={self._domain or 'general'}):\n{self._rubric}"
        )

    def _format_steps(self, steps) -> str:
        lines = []
        for s in steps or ():
            tools = ",".join(s.get("tools") or []) or "-"
            msg = (s.get("message") or "").strip()
            obs = (s.get("observation") or "").strip()
            lines.append(
                f"[step {s.get('step')}] tools={tools}\n"
                f"  reason: {msg}\n"
                f"  result: {obs}"
            )
        return "\n".join(lines) if lines else "(no steps recorded)"

    def score(self, task: Any, traj: Any):
        from sci3d_bench.scorers.base import ScoreResult
        from sci3d_bench.scorers.llm_judge import JUDGE_TOOL

        steps = getattr(traj, "steps", ()) or ()
        degraded = not steps
        prompt = (
            f"QUESTION: {task.question}\n"
            f"V-CLASS: {task.v_class}\n"
            f"FINAL ANSWER: {json.dumps(getattr(traj, 'final_answer', None), default=str)}\n\n"
            f"TRAJECTORY (ordered):\n{self._format_steps(steps)}\n"
        )
        resp = self._llm.complete(
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            tools=[JUDGE_TOOL],
            model=self._model,
        )
        if resp.tool_call is None or resp.tool_call.name != "_judge":
            return ScoreResult(
                value=0.0, metric="process_judge",
                raw={"error": "no_tool_call"}, degraded=True,
            )
        return ScoreResult(
            value=float(resp.tool_call.args.get("score", 0.0)),
            metric="process_judge",
            raw={
                "rationale": resp.tool_call.args.get("rationale", ""),
                "domain": self._domain,
                "n_steps": len(steps),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            },
            degraded=degraded,
        )


# Scorer names that map to the LLM-judge family (get domain rubric injection).
_JUDGE_NAMES = {"llm_judge", "judge"}


class UnknownVersionedScorerError(ValueError):
    """A declared evaluation contract has no runtime implementation."""


def looks_versioned_scorer(name: str) -> bool:
    """Return whether ``name`` declares a strict deterministic contract."""
    normalized = str(name or "").casefold()
    if normalized.startswith(("v1_", "v2_", "v3_", "v4_", "v5_", "v6_")):
        return False
    return bool(
        "deterministic" in normalized
        or "rubric" in normalized
        or re.search(r"_v\d+(?:_\d+)+$", normalized)
    )


def build_scorers(
    primary: str,
    secondary: tuple | list,
    *,
    llm: Any | None = None,
    judge_model: str | None = None,
    domain: str = "",
    rubric_text: str | None = None,
    fail_on_unknown: bool = False,
) -> list:
    """Instantiate only the scorers explicitly declared by the task.

    ``exact_match`` / ``tolerance_match`` / ``expert_rubric`` all route through
    the deterministic binary-rubric scorer.  LLM judges are optional shadow
    diagnostics; unlike the old registry, they are never appended implicitly.
    """
    from sci3d_bench.scorers.deterministic_answer import (
        DeterministicAnswerScorer,
        SCORER_ALIASES,
    )
    from sci3d_bench.scorers.mask_dice import MaskDiceScorer
    from sci3d_bench.scorers.refseg_text_judge import RefSegLLMJudge

    # This model is resolved only when a task explicitly requests a shadow
    # judge. The formal deterministic path never constructs an LLM client.
    model = judge_model or os.environ.get("SCI3D_JUDGE_MODEL") or "claude-haiku-4-5-20251001"
    rubric = rubric_text if rubric_text is not None else load_rubric(domain)

    wanted: list[str] = [primary, *list(secondary or [])]

    built: dict[str, Any] = {}
    unknown: list[str] = []
    for n in wanted:
        if n in built:
            continue
        normalized = n.removeprefix("shadow_")
        if normalized in _JUDGE_NAMES:
            client = llm if llm is not None else _build_llm(model)
            built["llm_judge"] = DomainJudge(
                client, model=model, domain=domain, rubric_text=rubric
            )
        elif normalized == "process_judge":
            client = llm if llm is not None else _build_llm(model)
            built["process_judge"] = TrajectoryJudge(
                client, model=model, domain=domain, rubric_text=rubric
            )
        elif normalized in {
            *SCORER_ALIASES,
            # Historical task JSON used these descriptive aliases before the
            # v2.1 scorer identifier was introduced. They remain deterministic
            # answer contracts, not optional unknown secondary metrics.
            "exact_match",
            "tolerance_match",
            "expert_rubric",
        }:
            built["answer_score"] = DeterministicAnswerScorer()
        elif n == "mask_dice":
            built["mask_dice"] = MaskDiceScorer()
        elif normalized == "extent_voxel_tolerance_v1":
            # This scorer is an optional medical extension in some pinned
            # scorer images.  Importing it unconditionally made every
            # unrelated exact-match task fail before its answer scorer could
            # be constructed when an older image lacked that extension.
            # Requesting the named contract still fails loudly if the image is
            # incomplete; only unrelated contracts are insulated.
            from sci3d_bench.scorers.extent_voxel_tolerance import (
                ExtentVoxelToleranceScorer,
            )

            built[ExtentVoxelToleranceScorer.name] = ExtentVoxelToleranceScorer()
        elif n == "refseg_judge":
            # refseg keeps its own fixed claude judge → Anthropic route.
            client = llm if llm is not None else _build_llm("claude-sonnet-4-6")
            built["refseg_judge"] = RefSegLLMJudge(client, model="claude-sonnet-4-6")
        else:
            unknown.append(str(n))

    strict_unknown = [
        name for name in unknown if fail_on_unknown or looks_versioned_scorer(name)
    ]
    if strict_unknown:
        raise UnknownVersionedScorerError(
            "unsupported scorer contract(s): " + ", ".join(strict_unknown)
        )

    return list(built.values())


def _build_llm(model: str | None = None) -> Any:
    """Build an LLM client for judging `model`: fake when SCI3D_USE_FAKE_LLM=1,
    else the route matching the model id (OpenAI for gpt-family, Anthropic for
    claude). Routing by model is what lets gpt-5.5 judge Claude agents without
    the Anthropic gateway rejecting the gpt id."""
    if os.environ.get("SCI3D_USE_FAKE_LLM") == "1":
        from sci3d_agent.llm_client import FakeLLMClient

        # A judge-shaped canned response so offline runs produce a real score.
        return FakeLLMClient(
            [{"tool": "_judge", "args": {"score": 0.0, "rationale": "fake"},
              "input_tokens": 1, "output_tokens": 1}]
        )
    from sci3d_agent.llm_client import build_judge_client

    return build_judge_client(model or "")
