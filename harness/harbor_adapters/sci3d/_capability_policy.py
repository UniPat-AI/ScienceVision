"""Enforce ``forbidden_tools`` against what the agent actually ran (T2).

The pre-existing tool-name check exempted ``Bash``/``Read``/``Write`` wholesale
(a sci3d allow-list enumerates only sci3d tools, so without the exemption every
generic-agent run failed the policy). That exemption is also the hole: a task
declaring ``forbidden_tools = ["nifti_centroid_api"]`` was never checked, because
the capability lives in a ``Bash`` command string, not in a tool name. On the 11
scored ``med_ct_geometry`` tasks the ``cc`` route scored 0.932 with
``process_rendered = 0.00`` -- it computed centroids off the staged ground-truth
mask and collected a clean ``process_tool_policy = 1.0``.

This module scans the arguments of agent-authored, executable calls against
``_capability_signatures``. Three facts come out, and keeping them separate is
the whole design:

``violations``   positive evidence the forbidden capability was used.
``suspects``     a general primitive that *could* be the shortcut, unescalated.
``coverage``     the fraction of declared capabilities that had any signature
                 at all -- disclosed, but never a finding.

Conflating the third with the first would break the gate in one direction or the
other. If "no signature" counted as a violation, ~every task would be flagged
(60 distinct capability names appear in the corpus and 11 of them are
unenforceable by construction) and the gate would be ignored. If it counted as
clean, the gate would be the ``attach_probe_results`` no-op again: a checker
reporting success having checked nothing. So a caller that wants to claim "no
shortcut" must read ``policy_ok`` *and* ``coverage``.

Pure functions, no I/O. ``harbor_adapters`` is the only package installed in the
verifier container (``pyproject.toml``: ``packages = ["sci3d_agent",
"harbor_adapters"]``), so nothing here may import from ``scripts/`` -- the
staged-path predicate is deliberately a small local copy rather than an import
of ``scripts.harbor._leak_probe``, which scans a different axis (network egress)
and is not importable at score time.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from harbor_adapters.sci3d._capability_signatures import (
    CAPABILITY_SIGNATURES,
    SUSPECT,
    UNENFORCEABLE_BY_SIGNATURE,
    VIOLATION,
)
from harbor_adapters.sci3d._tool_names import (
    is_code_authoring_tool,
    is_generic_harness_tool,
    normalize_tool_name,
)

CAPABILITY_POLICY_SCHEMA_VERSION = "sciencevision.capability_policy.v1"

# Findings are evidence for a human reviewer, not a corpus. Cap both the count
# and each excerpt so one pathological run cannot bloat every audit file, and
# disclose the truncation so a reader never mistakes a cap for a total.
MAX_FINDINGS = 12
MAX_EXCERPT_CHARS = 240

# Paths the task builder stages into the container image (``COPY asset/ /data/``,
# ``COPY views/ /data/``). A forbidden primitive applied to one of these is the
# shortcut; the same primitive on scratch data is not.
_STAGED_PATH_RE = re.compile(
    r"(?:^|[\s'\"=(\[,])/?data/[A-Za-z0-9_./-]+",
)

_ALWAYS_OK_TOOLS = frozenset({"finish"})

_pattern_cache: dict[str, re.Pattern[str]] = {}


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile (and memoise) one signature pattern, case-insensitively.

    Signatures are matched against shell commands and script bodies where casing
    varies (``AssignStereochemistry`` vs a lowercased log line), so the whole
    table is case-insensitive by construction rather than per entry.
    """
    cached = _pattern_cache.get(pattern)
    if cached is None:
        cached = re.compile(pattern, re.IGNORECASE)
        _pattern_cache[pattern] = cached
    return cached


def staged_paths(text: str) -> tuple[str, ...]:
    """Staged ``/data/`` paths named in one piece of agent-authored text."""
    found = [m.group(0).lstrip(" '\"=([,\t") for m in _STAGED_PATH_RE.finditer(text or "")]
    return tuple(dict.fromkeys(found))  # de-duplicated, order preserved


def _excerpt(text: str, start: int, end: int) -> str:
    """A bounded window around a match, so the audit shows context not a dump."""
    pad = max(0, (MAX_EXCERPT_CHARS - (end - start)) // 2)
    lo = max(0, start - pad)
    return text[lo:lo + MAX_EXCERPT_CHARS]


def _declared(forbidden: Iterable[str] | None) -> list[str]:
    """Declared capability names, de-duplicated, original order preserved."""
    names = [str(n).strip() for n in (forbidden or []) if str(n or "").strip()]
    return list(dict.fromkeys(names))


def scan_text(
    text: str,
    forbidden: Iterable[str] | None,
) -> tuple[dict[str, Any], ...]:
    """Findings for one piece of agent-authored text, one per matching signature.

    A suspect-level match is escalated to ``VIOLATION`` when the same text also
    names a staged ``/data/`` path. Escalation is per-text on purpose: a staged
    path named three calls earlier does not make a later ``np.nonzero`` a
    shortcut, and scoping it this way keeps every finding a self-contained
    excerpt a reviewer can judge without replaying the run.
    """
    body = str(text or "")
    if not body:
        return ()
    paths = staged_paths(body)
    findings: list[dict[str, Any]] = []
    for capability in _declared(forbidden):
        for pattern, severity in CAPABILITY_SIGNATURES.get(capability, ()):
            match = compile_pattern(pattern).search(body)
            if match is None:
                continue
            escalated = severity == SUSPECT and bool(paths)
            findings.append({
                "capability": capability,
                "severity": VIOLATION if (severity == VIOLATION or escalated) else SUSPECT,
                "declared_severity": severity,
                "escalated": escalated,
                "pattern": pattern,
                "match": match.group(0),
                "excerpt": _excerpt(body, match.start(), match.end()),
                "staged_paths": list(paths),
            })
            break  # one finding per capability per text: the first is the evidence
    return tuple(findings)


def audit_capability_policy(
    calls: Sequence[Any] | None,
    forbidden: Iterable[str] | None,
) -> dict[str, Any]:
    """Audit one run's calls against its declared forbidden capabilities.

    ``calls`` are the records produced by ``_tool_outcomes.parse_calls_from_path``
    (``{step, name, arguments_text, ...}``). Only ``arguments_text`` is read:
    observations are never scanned, because an agent must not be penalised for a
    string some tool handed back to it.

    Returns a new dict; neither ``calls`` nor ``forbidden`` is modified.
    """
    declared = _declared(forbidden)
    enforced = [n for n in declared if n in CAPABILITY_SIGNATURES]
    unenforceable = {
        n: UNENFORCEABLE_BY_SIGNATURE[n]
        for n in declared
        if n in UNENFORCEABLE_BY_SIGNATURE
    }
    unmapped = [
        n for n in declared
        if n not in CAPABILITY_SIGNATURES and n not in UNENFORCEABLE_BY_SIGNATURE
    ]

    violations: list[dict[str, Any]] = []
    suspects: list[dict[str, Any]] = []
    scanned = 0
    for call in calls or ():
        if not isinstance(call, Mapping):
            continue
        name = call.get("name")
        if not is_code_authoring_tool(name):
            continue
        scanned += 1
        for finding in scan_text(call.get("arguments_text"), declared):
            row = {**finding, "tool": str(name), "step": call.get("step")}
            (violations if row["severity"] == VIOLATION else suspects).append(row)

    return {
        "schema_version": CAPABILITY_POLICY_SCHEMA_VERSION,
        "declared": declared,
        "enforced": enforced,
        "unenforceable": unenforceable,
        "unmapped": unmapped,
        # 1.0 when nothing is declared: there is no unchecked policy, so the
        # vacuous case must not look like a coverage gap.
        "coverage": 1.0 if not declared else len(enforced) / len(declared),
        "scanned_calls": scanned,
        "violations": violations[:MAX_FINDINGS],
        "violations_truncated": len(violations) > MAX_FINDINGS,
        "suspects": suspects[:MAX_FINDINGS],
        "suspects_truncated": len(suspects) > MAX_FINDINGS,
        # Only positive evidence fails the policy. Coverage is reported beside
        # it; a caller claiming "clean" must check both.
        "policy_ok": not violations,
    }


def split_tool_policy(
    tool_names: Sequence[str] | None,
    allowed: Iterable[str] | None,
    forbidden: Iterable[str] | None,
) -> dict[str, list[str]]:
    """Separate "called a forbidden tool" from "left the allow-list".

    ``compute_process_rewards`` merges both into one ``process_tool_policy``
    bit, which is fine as a diagnostic but wrong as a gate: leaving a narrow
    allow-list is an ergonomics fact about how the allow-list was written, while
    calling a forbidden tool is a breach. Gating on the merged bit would
    re-create the very bias T2 exists to remove -- penalising the route that
    honestly names its tools.
    """
    names = [normalize_tool_name(n) for n in (tool_names or [])]
    allowed_set = {normalize_tool_name(n) for n in (allowed or [])}
    forbidden_set = {normalize_tool_name(n) for n in (forbidden or [])}

    forbidden_called = [n for n in dict.fromkeys(names) if n in forbidden_set]
    outside = [
        n for n in dict.fromkeys(names)
        if allowed_set
        and n not in allowed_set
        and n not in _ALWAYS_OK_TOOLS
        and not is_generic_harness_tool(n)
        and n not in forbidden_set
    ]
    return {"forbidden_called": forbidden_called, "outside_allowlist": outside}
