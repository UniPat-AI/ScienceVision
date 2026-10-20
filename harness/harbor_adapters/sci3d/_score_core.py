"""Single-source scoring core shared by the host-run and containerized routes.

``Sci3dVerifier.verify()`` (host route) and ``tests/score.py`` (containerized
route) both call :func:`score_rewards`. There is exactly one implementation of
"answer + trajectory + metadata -> rewards + audit"; neither route reimplements
the scoring logic. The only difference between routes is *where the inputs come
from*:

    host route        Harbor injects ``self.task`` / ``self.trial_paths``; the
                      verifier hands their fields to ``score_rewards``.
    containerized     ``tests/score.py`` reads the same fields from the mounted
                      ``/logs/agent`` files + the ``[metadata]`` table (passed
                      in as a dict via the SCI3D_METADATA env / task.toml), then
                      hands them to ``score_rewards``.

``score_rewards`` is therefore free of any Harbor object — it takes plain data
(a metadata dict, a task id, an instruction string, and the agent dir Path) and
returns ``(rewards, audit)``. This keeps the container image light (no Harbor
runtime needed just to score) and guarantees the two routes can never drift.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._process_gate import evaluate_process_gate
from ._route_contracts import evaluate_route_eligibility, load_health_receipt
from ._scorer_registry import build_scorers, load_rubric, looks_versioned_scorer
from sci3d_bench.scorers.deterministic_answer import SCORER_ALIASES
from ._verify_translate import (
    WEIGHTED_KEY,
    answer_json_to_trajview,
    compute_process_rewards,
    task_metadata_to_taskspec,
    weighted_total,
)

ANSWER_FILE = "sci3d_answer.json"
TRAJECTORY_FILE = "sci3d_trajectory.json"
AUDIT_FILE = "sci3d_scores.json"


def score_rewards(
    meta: dict[str, Any],
    *,
    task_id: str,
    instruction: str,
    agent_dir: Path,
    judge_model: str | None = None,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Score one sci3d run into Harbor rewards + an audit dict.

    Mirrors exactly what ``Sci3dVerifier.verify`` used to do inline; both routes
    now delegate here so the logic lives in one place.

    Returns ``(rewards, audit)``:
        rewards  flat ``dict[str, float]`` — deterministic ``answer_score``,
                 independent ``process_*`` diagnostics, optional shadows, and
                 ``reward`` (which equals ``answer_score``).
        audit    per-item verdicts + eligibility + tool_names + domain;
                 callers persist it next to the rewards (host: verifier_dir,
                 container: /logs/verifier/sci3d_scores.json).
    """
    agent_dir = Path(agent_dir)
    # Trajectory file: Sci3dAgent writes sci3d_trajectory.json; a generic harness
    # (Claude Code CLI, Codex, …) writes the standard harbor trajectory.json.
    # Prefer the sci3d-specific name, fall back to the standard one so process
    # signals + the dual-source answer work for generic agents too.
    traj_path = agent_dir / TRAJECTORY_FILE
    if not traj_path.exists():
        std_traj = agent_dir / "trajectory.json"
        if std_traj.exists():
            traj_path = std_traj
    view = answer_json_to_trajview(
        agent_dir / ANSWER_FILE, traj_path, str(task_id)
    )

    rewards: dict[str, float] = {}
    audit: dict[str, Any] = {"task_id": str(task_id), "domain": meta.get("domain", "")}
    primary = str((meta.get("scorer") or {}).get("primary", ""))
    strict_contract = primary in {
        *SCORER_ALIASES,
        "exact_match",
        "tolerance_match",
        "expert_rubric",
    } or looks_versioned_scorer(primary)

    # ── deterministic answer + explicitly requested shadow scores ────────── #
    task_marked_ineligible = meta.get("evaluation_eligible") is False
    if task_marked_ineligible:
        audit["eligibility_error"] = str(
            meta.get("evaluation_ineligible_reason") or "task_marked_ineligible"
        )
    else:
        try:
            taskspec = task_metadata_to_taskspec(
                meta, task_id=str(task_id), instruction=instruction
            )
            domain = str(meta.get("domain", ""))
            scorers = build_scorers(
                taskspec.scorer.primary,
                taskspec.scorer.secondary,
                llm=None,  # registry builds fake/real per SCI3D_USE_FAKE_LLM
                judge_model=judge_model,
                domain=domain,
                rubric_text=load_rubric(domain),
                fail_on_unknown=strict_contract,
            )
            for sc in scorers:
                try:
                    r = sc.score(taskspec, view)
                    key = (
                        f"shadow_{sc.name}"
                        if sc.name in {"llm_judge", "process_judge"}
                        else sc.name
                    )
                    rewards[key] = float(r.value)
                    audit[key] = {
                        "value": float(r.value),
                        "metric": r.metric,
                        "degraded": bool(r.degraded),
                        "raw": r.raw,
                    }
                except Exception as exc:  # one bad scorer must not sink the run
                    key = (
                        f"shadow_{sc.name}"
                        if sc.name in {"llm_judge", "process_judge"}
                        else sc.name
                    )
                    rewards[key] = 0.0
                    audit[key] = {
                        "value": 0.0,
                        "error": repr(exc),
                        "degraded": True,
                    }
        except Exception as exc:
            audit["taskspec_error"] = repr(exc)
            if strict_contract:
                rewards["answer_score"] = 0.0

    # ── process scores (closed-loop, {0,1}) ──────────────────────────────── #
    process = compute_process_rewards(
        view.tool_names,
        meta.get("allowed_tools"),
        meta.get("forbidden_tools"),
    )
    rewards.update(process)
    rewards["process_compliance"] = process["process_tool_policy"]
    audit["tool_names"] = list(view.tool_names)

    # ── process gate (capability policy + MCP health) ────────────────────── #
    # ``compute_process_rewards`` above can only see tool *names*, and generic
    # harness tools are exempt from that check by necessity. So it cannot notice
    # a forbidden capability executed inside a Bash string, nor that every MCP
    # call returned an error envelope. The gate reads the full call records for
    # both. It never changes the formal reward -- see ``weights`` below.
    gate = evaluate_process_gate(
        view.calls,
        view.tool_names,
        meta.get("allowed_tools"),
        meta.get("forbidden_tools"),
    )
    rewards.update(gate["rewards"])
    audit.update(gate["audit"])

    # ── route contract (is this a comparable CC / CCMCP trial?) ────────── #
    # A task with no route contract predates the isolation experiment and keeps
    # its historical scoring behavior.  Once a contract is present, absence of
    # its health receipt is intentionally infrastructure-invalid, not a silent
    # pass.  This is evaluated after the normal answer scorer so the diagnostic
    # answer score remains inspectable even when the formal row is excluded.
    route_verdict: dict[str, Any] | None = None
    route_contract = meta.get("route_contract")
    if route_contract is not None:
        if not isinstance(route_contract, dict):
            route_verdict = {
                "status": "infra_invalid",
                "reason_codes": ["malformed_route_contract"],
            }
        else:
            try:
                route_verdict = evaluate_route_eligibility(
                    route_contract,
                    load_health_receipt(agent_dir),
                    view.calls,
                )
            except Exception as exc:  # malformed external receipt must not crash scoring
                route_verdict = {
                    "status": "infra_invalid",
                    "reason_codes": ["route_contract_evaluation_error"],
                    "detail": repr(exc),
                }
        assert route_verdict is not None
        status = str(route_verdict.get("status") or "infra_invalid")
        rewards["process_route_eligible"] = 1.0 if status == "eligible" else 0.0
        rewards["process_route_infra_healthy"] = 0.0 if status == "infra_invalid" else 1.0
        audit["route_eligibility"] = route_verdict

    # ── formal reward (answer only) ──────────────────────────────────────── #
    weights = meta.get("weights") if isinstance(meta.get("weights"), dict) else {}
    answer_audit = audit.get("answer_score") if isinstance(audit.get("answer_score"), dict) else {}
    raw = answer_audit.get("raw") if isinstance(answer_audit.get("raw"), dict) else {}
    audit["evaluation_eligible"] = bool(raw.get("eligible", False))
    audit["weights"] = {"answer": 1.0, "process": 0.0}
    audit["formal_reward_source"] = "answer_score"
    audit["process_rewards_diagnostic_only"] = True
    audit["aggregate_excluded"] = False

    def exclude_formal_row(*, source: str, reason: str) -> None:
        """Remove an incomparable answer rather than relabel it a model zero."""
        diagnostic = audit.pop("answer_score", None)
        if diagnostic is not None:
            audit["diagnostic_answer_score"] = diagnostic
        rewards.pop("answer_score", None)
        audit["formal_reward_valid"] = False
        audit["formal_reward_source"] = source
        audit["evaluation_eligible"] = False
        audit["aggregate_excluded"] = True
        audit["aggregate_exclusion_reason"] = reason

    if task_marked_ineligible:
        exclude_formal_row(
            source="task_marked_ineligible_not_scorable",
            reason=str(audit["eligibility_error"]),
        )

    if route_verdict is not None:
        status = str(route_verdict.get("status") or "infra_invalid")
        audit["route_evaluation_status"] = status
        # A bad health/catalog receipt means the route itself is incomparable,
        # not that the model missed the answer. Conversely, a healthy route
        # with an agent that declined/failed the required tool call is an
        # ordinary comparable zero.
        if task_marked_ineligible:
            # The task-level exclusion is stronger than any later route
            # diagnostic. In particular, a missing required private input can
            # never be converted into a comparable agent-noncompliance zero.
            pass
        elif status == "infra_invalid":
            exclude_formal_row(
                source="infra_invalid_not_scorable",
                reason="route_infrastructure_invalid",
            )
        elif status == "agent_noncompliant":
            diagnostic = audit.get("answer_score")
            if diagnostic is not None:
                audit["diagnostic_answer_score"] = diagnostic
            rewards["answer_score"] = 0.0
            if isinstance(audit.get("answer_score"), dict):
                audit["answer_score"] = {
                    **audit["answer_score"],
                    "value": 0.0,
                    "route_contract_noncompliant": True,
                }
            else:
                audit["answer_score"] = {
                    "value": 0.0,
                    "metric": "answer_score",
                    "degraded": True,
                    "raw": {"reason": "route_contract_noncompliance"},
                    "route_contract_noncompliant": True,
                }
            audit["formal_reward_valid"] = True
            audit["formal_reward_source"] = "route_contract_noncompliance_zero"
            # Health/canonical catalog are valid; this remains a row in the
            # formal denominator and is deliberately a zero.
            audit["evaluation_eligible"] = True
            audit["aggregate_excluded"] = False
        else:
            audit["formal_reward_valid"] = True
    elif task_marked_ineligible:
        # Explicit task ineligibility is not a route verdict, but has the same
        # non-comparable aggregate behavior as an infra-invalid route.
        audit["formal_reward_valid"] = False

    rewards[WEIGHTED_KEY] = weighted_total(rewards, weights)
    audit["reward"] = rewards[WEIGHTED_KEY]

    return rewards, audit
