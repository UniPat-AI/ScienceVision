"""Assemble the three process signals into one citability verdict.

``_score_core`` should read as wiring, so the judgement lives here: given the
parsed calls and the task's declared policy, produce the ``process_*`` rewards,
the audit sections, and the one boolean a reader actually wants -- may this run's
number be cited?

The rules, and why each is drawn where it is:

**Only positive evidence blocks.** A capability violation (the forbidden library
call is in the agent's own command) and a forbidden tool call are breaches.
Leaving ``allowed_tools`` is not: that list enumerates sci3d tools only, so a
generic-harness route trips it merely by existing. Gating on it would re-create
the bias this change exists to remove -- penalising the routes that name their
tools honestly while ``cc`` hides the same work inside a ``Bash`` string.

**Unchecked is disclosed, not passed and not failed.** ~60 distinct capability
names appear across the corpus and 11 are unenforceable by construction (they
describe *reading a staged value*, which is a staging defect, not a code
fingerprint). Treating those as violations would flag nearly every task and the
gate would be ignored; treating them as clean would repeat the
checker-that-checks-nothing bug. They lower ``process_capability_coverage`` and
appear in ``notes``, so ``citable is True`` never silently means "we looked at
nothing".

**Rewards stay numeric.** ``None`` (an undefined MCP success rate) belongs in the
audit; Harbor's reward channel takes floats only. The consequence is that
``process_mcp_success_rate = 0.0`` is ambiguous on its own -- "never tried" and
"tried, all failed" collapse -- so ``process_mcp_attempted`` is emitted beside it
and any consumer must read both.

Pure functions, no I/O.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from harbor_adapters.sci3d._capability_policy import (
    audit_capability_policy,
    split_tool_policy,
)
from harbor_adapters.sci3d._integrity_policy import audit_integrity_policy
from harbor_adapters.sci3d._tool_outcomes import summarize_mcp
from harbor_adapters.sci3d._verify_translate import PROCESS_PREFIX

PROCESS_GATE_SCHEMA_VERSION = "sciencevision.process_gate.v1"

# Positive evidence of a breach. Nothing else may enter this list without the
# argument in the module docstring being revisited.
BLOCKER_CAPABILITY_VIOLATION = "capability_violation"
BLOCKER_FORBIDDEN_TOOL = "forbidden_tool_called"
BLOCKER_PROTECTED_PATH = "protected_path_attempt"
BLOCKER_UNAUTHORIZED_EGRESS = "unauthorized_egress_attempt"


def _notes(
    capability: dict[str, Any],
    tool_policy: dict[str, list[str]],
    mcp: dict[str, Any],
    integrity: dict[str, Any],
) -> list[str]:
    """Everything a reader must know before trusting ``citable``.

    Each note names a way the verdict is weaker than it looks, so "citable" is
    never read as "audited clean".
    """
    notes: list[str] = []
    if capability["unenforceable"]:
        notes.append(
            "no static signature can decide: "
            + ", ".join(sorted(capability["unenforceable"]))
        )
    if capability["unmapped"]:
        notes.append(
            "declared but unmapped (no signature written yet): "
            + ", ".join(capability["unmapped"])
        )
    if capability["suspects"]:
        notes.append(
            f"{len(capability['suspects'])} suspect match(es) not escalated "
            "(a general primitive, no staged path in the same command)"
        )
    if tool_policy["outside_allowlist"]:
        notes.append(
            "outside allowed_tools (reported, not a blocker): "
            + ", ".join(tool_policy["outside_allowlist"])
        )
    if mcp["calls"] and mcp["unknown"]:
        notes.append(
            f"{mcp['unknown']} of {mcp['calls']} MCP call(s) had no readable "
            "outcome and are excluded from the success rate"
        )
    if integrity["protected_path_attempts"]:
        notes.append(
            f"{len(integrity['protected_path_attempts'])} protected evaluator-path "
            "attempt(s)"
        )
    if integrity["unauthorized_egress_attempts"]:
        notes.append(
            f"{len(integrity['unauthorized_egress_attempts'])} unauthorized "
            "egress attempt(s)"
        )
    return notes


def evaluate_process_gate(
    calls: Sequence[Any] | None,
    tool_names: Sequence[str] | None,
    allowed: Iterable[str] | None,
    forbidden: Iterable[str] | None,
) -> dict[str, Any]:
    """Return ``{"rewards": {...}, "audit": {...}}`` for one run.

    ``calls`` are ``_tool_outcomes`` records; ``tool_names`` is the flat name
    sequence the legacy tool-name policy already used. Both are read, neither is
    modified, and the returned dicts are new.
    """
    capability = audit_capability_policy(calls, forbidden)
    tool_policy = split_tool_policy(tool_names, allowed, forbidden)
    mcp = summarize_mcp(calls)
    integrity = audit_integrity_policy(calls)

    blockers: list[str] = []
    if not capability["policy_ok"]:
        blockers.append(BLOCKER_CAPABILITY_VIOLATION)
    if tool_policy["forbidden_called"]:
        blockers.append(BLOCKER_FORBIDDEN_TOOL)
    if integrity["protected_path_attempts"]:
        blockers.append(BLOCKER_PROTECTED_PATH)
    if integrity["unauthorized_egress_attempts"]:
        blockers.append(BLOCKER_UNAUTHORIZED_EGRESS)

    rate = mcp["success_rate"]
    rewards = {
        f"{PROCESS_PREFIX}capability_policy": 1.0 if capability["policy_ok"] else 0.0,
        f"{PROCESS_PREFIX}capability_coverage": float(capability["coverage"]),
        f"{PROCESS_PREFIX}integrity_policy": 1.0 if integrity["policy_ok"] else 0.0,
        f"{PROCESS_PREFIX}mcp_attempted": 1.0 if mcp["calls"] else 0.0,
        # 0.0 when undefined; meaningless without ``mcp_attempted`` beside it.
        f"{PROCESS_PREFIX}mcp_success_rate": float(rate) if rate is not None else 0.0,
        # The verdict itself, on the reward channel, so a reporter that only
        # reads Harbor's ``result.json`` can still refuse to cite the run --
        # the audit alone is a signal nobody consults (see ``_citability``).
        # It is a transport copy, not a second opinion: both come from
        # ``blockers`` below, so they cannot disagree.
        f"{PROCESS_PREFIX}citable": 0.0 if blockers else 1.0,
    }

    return {
        "rewards": rewards,
        "audit": {
            "capability_policy": capability,
            "integrity_policy": integrity,
            "tool_policy": tool_policy,
            "mcp": mcp,
            "process_gate": {
                "schema_version": PROCESS_GATE_SCHEMA_VERSION,
                "citable": not blockers,
                "blockers": blockers,
                "notes": _notes(capability, tool_policy, mcp, integrity),
            },
        },
    }
