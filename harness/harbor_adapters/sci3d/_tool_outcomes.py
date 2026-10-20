"""Recover per-call outcomes from a trajectory -- "did the MCP call succeed?".

Before this module the eval recorded *that* a tool was called and nothing about
whether it worked. ``_parse_atif_tool_names`` kept only ``function_name`` and
discarded the arguments; ``_parse_atif_steps`` flattened every observation into
one 800-char blob for the judge prompt and truncated at 24 steps. So a ``ccmcp``
run in which every ``sv3d_render`` returned ``{"error": ...}`` was
indistinguishable from one in which they all succeeded -- both scored
``process_rendered = 1.0``, because the reward only asked whether the tool name
appeared.

The repo convention makes this recoverable: MCP tool handlers return
``{"error": "..."}`` rather than raising across the boundary (CLAUDE.md), so the
outcome is in the observation payload. ``_translate.py`` puts it there verbatim
as ``Observation(results=[ObservationResult(source_call_id=..., content=...)])``.

One parse produces one structure serving both consumers: the capability scan
(T2) needs ``arguments_text``, the MCP-health signal (the user's Q2) needs
``outcome``. Two parses of the same file could disagree about which call an
observation belongs to.

Three outcomes, and the third is load-bearing. ``unknown`` exists so an
unparseable or unpaired observation cannot read as success; it is excluded from
the success-rate denominator and counted separately, and a run with no decided
calls reports ``success_rate = None`` rather than ``0.0`` (which would read as
"every call failed") or ``1.0`` (which would let a ``cc`` run using no MCP at
all look like flawless MCP usage).

Pure functions plus one file read. No ``scripts/`` imports -- see
``_capability_policy`` for why.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from harbor_adapters.sci3d._tool_names import is_mcp_call, normalize_tool_name

TOOL_OUTCOMES_SCHEMA_VERSION = "sciencevision.tool_outcomes.v1"

OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_UNKNOWN = "unknown"

# Error text is evidence, not a log. Bound the per-call text and the number of
# samples kept in the run summary, and disclose the truncation.
MAX_ERROR_TEXT_CHARS = 300
MAX_ERROR_SAMPLES = 8
# Arguments are scanned for capability signatures, which can sit deep inside a
# heredoc-authored script, so this budget is generous where the judge-prompt
# window (800 chars) is not.
MAX_ARGS_CHARS = 20000

# Truthy under any of these keys means the call failed. ``error`` is the repo
# envelope; ``isError``/``is_error`` are the MCP wire spellings.
_ERROR_KEYS = ("error", "isError", "is_error")
# A degraded success: the GL-less renderer returns this by design, so the call
# did not raise -- but the agent got no image, which for "did it succeed" is a
# no. The reason is preserved so a reviewer can separate it from a real fault.
_SKIPPED_KEYS = ("renderer_skipped",)

# Plain-text failures, matched as a *prefix* only. Substring matching anywhere
# would make any line mentioning errors ("0 errors, 3 warnings") a failure.
_ERROR_PREFIXES = (
    "error:", "error ", "errno", "traceback (most recent call last)",
    "tool_use_error", "exception:", "fatal:", "usage error",
)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def classify_content(content: Any) -> tuple[str, str | None]:
    """Classify one observation payload as ok / error / unknown.

    Returns ``(outcome, error_text)``; ``error_text`` is None unless the outcome
    is an error.
    """
    if content is None:
        return OUTCOME_UNKNOWN, None
    text = content if isinstance(content, str) else str(content)
    stripped = text.strip()
    if not stripped:
        return OUTCOME_UNKNOWN, None

    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            payload = None
        if isinstance(payload, Mapping):
            for key in _ERROR_KEYS:
                # Truthiness, not presence: ``{"error": null}`` is a handler
                # saying nothing went wrong, and ``{"errors": []}`` is a payload
                # field rather than the envelope.
                if payload.get(key):
                    return OUTCOME_ERROR, _truncate(f"{key}: {payload[key]}", MAX_ERROR_TEXT_CHARS)
            for key in _SKIPPED_KEYS:
                if payload.get(key):
                    return OUTCOME_ERROR, _truncate(
                        f"{key}: {payload[key]}", MAX_ERROR_TEXT_CHARS
                    )
            return OUTCOME_OK, None

    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in _ERROR_PREFIXES):
        return OUTCOME_ERROR, _truncate(stripped, MAX_ERROR_TEXT_CHARS)
    return OUTCOME_OK, None


def _arguments_text(arguments: Any) -> str:
    """Flatten a tool call's arguments to searchable text.

    Serialised rather than walked, so a signature is found wherever it sits --
    a ``command`` string, a ``content`` heredoc, or a nested field a future
    harness invents.
    """
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return _truncate(arguments, MAX_ARGS_CHARS)
    try:
        return _truncate(json.dumps(arguments, ensure_ascii=False), MAX_ARGS_CHARS)
    except (TypeError, ValueError):
        return _truncate(str(arguments), MAX_ARGS_CHARS)


def _pair_results(step: Mapping[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """Split one step's observation results into id-keyed and positional halves.

    Not every harness fills ``source_call_id``. Keeping the unlabelled results
    lets a single call / single result step pair unambiguously; refusing to pair
    them would report such runs as entirely ``unknown``, an artifact
    indistinguishable from a broken parser.
    """
    observation = step.get("observation")
    results = []
    if isinstance(observation, Mapping):
        results = observation.get("results") or []
    by_id: dict[str, Any] = {}
    positional: list[Any] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        call_id = result.get("source_call_id")
        if call_id:
            by_id[str(call_id)] = result.get("content")
        else:
            positional.append(result.get("content"))
    return by_id, positional


def parse_calls(traj: Mapping[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Every tool call in an ATIF trajectory, with arguments and outcome.

    Deliberately unbounded in step count: a shortcut on step 40 is still a
    shortcut, and the judge-prompt truncation in ``_parse_atif_steps`` must not
    become the policy scan's horizon.
    """
    if not isinstance(traj, Mapping):
        return ()
    out: list[dict[str, Any]] = []
    for step in traj.get("steps", []) or []:
        if not isinstance(step, Mapping):
            continue
        by_id, positional = _pair_results(step)
        calls = [c for c in (step.get("tool_calls") or []) if isinstance(c, Mapping)]
        for index, call in enumerate(calls):
            name = call.get("function_name")
            if not name:
                continue
            call_id = call.get("tool_call_id")
            if call_id is not None and str(call_id) in by_id:
                content = by_id[str(call_id)]
            elif len(calls) == 1 and len(positional) == 1:
                content = positional[0]
            elif len(calls) == len(positional):
                content = positional[index]
            else:
                content = None
            outcome, error_text = classify_content(content)
            out.append({
                "step": step.get("step_id"),
                "name": str(name),
                "call_id": str(call_id) if call_id is not None else None,
                "arguments_text": _arguments_text(call.get("arguments")),
                "outcome": outcome,
                "error_text": error_text,
                "source": "atif",
            })
    return tuple(out)


def parse_calls_from_path(traj_path: Path | str) -> tuple[dict[str, Any], ...]:
    """``parse_calls`` over a trajectory file; empty on a missing/bad file."""
    try:
        traj = json.loads(Path(traj_path).read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return ()
    return parse_calls(traj)


# ── Codex session JSONL --------------------------------------------------- #
#
# Harbor's ATIF trajectory is sufficient for the in-repo Sci3dAgent, but the
# Codex adapter can keep its function calls in ``/logs/agent/sessions/*.jsonl``
# instead.  Treating the ATIF artifact as the only source makes a real MCP call
# look like "no call", which in turn lets an agent-noncompliance decision hide a
# missing recorder.  The schema below is deliberately tolerant of the Codex
# variants seen across CLI releases: response-item wrappers, direct function
# call records, and function-call-output records.

_CODEX_CALL_TYPES = frozenset({
    "function_call", "tool_call", "tool_use", "mcp_call", "mcp_tool_call",
})
_CODEX_OUTPUT_TYPE_MARKERS = ("function_call_output", "tool_output", "tool_result", "tool_response")
_CODEX_CALL_ID_KEYS = ("call_id", "tool_call_id", "id")


def _codex_call_id(row: Mapping[str, Any]) -> str | None:
    for key in _CODEX_CALL_ID_KEYS:
        value = row.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _content_to_text(value: Any) -> str:
    """Flatten common OpenAI/Codex output containers without losing JSON text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, Mapping):
        for key in ("text", "output_text", "content", "output", "result"):
            if key in value:
                return _content_to_text(value[key])
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return "\n".join(part for part in (_content_to_text(item) for item in value) if part)
    return str(value)


def _walk_dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    """Yield all mapping nodes in wire order from one JSONL record."""
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            yield from _walk_dicts(child)


def _codex_name(row: Mapping[str, Any]) -> str | None:
    for key in ("name", "function_name", "tool_name"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    function = row.get("function")
    if isinstance(function, Mapping):
        return _codex_name(function)
    return None


def _is_codex_output(row: Mapping[str, Any]) -> bool:
    kind = str(row.get("type") or row.get("item_type") or "").lower()
    return any(marker in kind for marker in _CODEX_OUTPUT_TYPE_MARKERS)


def _is_codex_call(row: Mapping[str, Any]) -> bool:
    if _is_codex_output(row) or not _codex_name(row):
        return False
    kind = str(row.get("type") or row.get("item_type") or "").lower()
    if kind in _CODEX_CALL_TYPES:
        return True
    # Some Codex session versions omit ``type`` inside a `function` wrapper but
    # keep arguments + a call id.  Do not accept a mere named message: that
    # would turn prose/metadata into an invented tool use.
    return _codex_call_id(row) is not None and any(
        key in row for key in ("arguments", "input", "args", "function")
    )


def _codex_arguments(row: Mapping[str, Any]) -> Any:
    for key in ("arguments", "input", "args"):
        if key in row:
            return row[key]
    function = row.get("function")
    if isinstance(function, Mapping):
        return _codex_arguments(function)
    return None


def _codex_output(row: Mapping[str, Any]) -> Any:
    for key in ("output", "content", "result", "response"):
        if key in row:
            return _content_to_text(row[key])
    return None


def _session_paths(agent_dir: Path | str) -> tuple[Path, ...]:
    root = Path(agent_dir)
    if root.is_file():
        return (root,) if root.suffix == ".jsonl" else ()
    sessions = root / "sessions"
    if not sessions.is_dir():
        return ()
    return tuple(sorted(path for path in sessions.rglob("*.jsonl") if path.is_file()))


def parse_codex_session_calls(agent_dir: Path | str) -> tuple[dict[str, Any], ...]:
    """Recover Codex function calls/outcomes from ``agent_dir/sessions`` JSONL.

    Bad lines are ignored rather than making a scorer crash.  They do *not*
    become successes: an unpaired call stays ``unknown`` and cannot meet a
    required MCP evidence group.
    """
    calls: list[dict[str, Any]] = []
    pending: dict[str, list[int]] = {}
    early_outputs: dict[str, list[Any]] = {}

    def bind_output(call_id: str, content: Any) -> bool:
        for index in pending.get(call_id, []):
            if calls[index]["outcome"] == OUTCOME_UNKNOWN:
                outcome, error_text = classify_content(content)
                calls[index]["outcome"] = outcome
                calls[index]["error_text"] = error_text
                return True
        return False

    for path in _session_paths(agent_dir):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, start=1):
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            for row in _walk_dicts(payload):
                if _is_codex_call(row):
                    name = _codex_name(row)
                    assert name is not None
                    call_id = _codex_call_id(row)
                    record = {
                        "step": f"{path.name}:{line_no}",
                        "name": name,
                        "call_id": call_id,
                        "arguments_text": _arguments_text(_codex_arguments(row)),
                        "outcome": OUTCOME_UNKNOWN,
                        "error_text": None,
                        "source": "codex_session",
                    }
                    calls.append(record)
                    if call_id:
                        pending.setdefault(call_id, []).append(len(calls) - 1)
                        queued = early_outputs.pop(call_id, [])
                        for content in queued:
                            bind_output(call_id, content)
                elif _is_codex_output(row):
                    call_id = _codex_call_id(row)
                    content = _codex_output(row)
                    if call_id:
                        if not bind_output(call_id, content):
                            early_outputs.setdefault(call_id, []).append(content)
    return tuple(calls)


# Compatibility name used by early route-contract callers.  It parses all
# Codex function calls (not just MCP ones) so integrity/capability policy can
# inspect Bash/Edit calls in the same session too.
parse_codex_mcp_calls = parse_codex_session_calls


def merge_call_records(*sources: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Merge ATIF and session evidence without double-counting shared call ids."""
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source in sources:
        for raw in source or ():
            if not isinstance(raw, Mapping):
                continue
            record = dict(raw)
            call_id = record.get("call_id")
            if call_id is not None and str(call_id):
                key = str(call_id)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
            out.append(record)
    return tuple(out)


def _empty_counts() -> dict[str, int]:
    return {"ok": 0, "error": 0, "unknown": 0}


def summarize_mcp(calls: Sequence[Any] | None) -> dict[str, Any]:
    """Roll the sci3d/MCP calls up to one run-level health signal.

    ``success_rate`` is over *decided* calls (ok + error). ``None`` when nothing
    was decided -- either no MCP call was made (``cc``, by construction) or every
    observation was unparseable. Callers must therefore qualify the rate with
    ``calls``; a bare rate cannot distinguish "perfect" from "never tried".

    Returns a new dict; ``calls`` is not modified.
    """
    total = 0
    counts = _empty_counts()
    by_tool: dict[str, dict[str, int]] = {}
    errors: list[dict[str, Any]] = []

    for call in calls or ():
        if not isinstance(call, Mapping):
            continue
        name = call.get("name")
        if not is_mcp_call(name):
            continue
        outcome = str(call.get("outcome") or OUTCOME_UNKNOWN)
        if outcome not in counts:
            outcome = OUTCOME_UNKNOWN
        total += 1
        counts[outcome] += 1
        bare = normalize_tool_name(name)
        by_tool.setdefault(bare, _empty_counts())[outcome] += 1
        if outcome == OUTCOME_ERROR:
            errors.append({
                "step": call.get("step"),
                "tool": bare,
                "error_text": call.get("error_text"),
            })

    decided = counts["ok"] + counts["error"]
    return {
        "schema_version": TOOL_OUTCOMES_SCHEMA_VERSION,
        "calls": total,
        "ok": counts["ok"],
        "error": counts["error"],
        "unknown": counts["unknown"],
        "success_rate": (counts["ok"] / decided) if decided else None,
        "by_tool": by_tool,
        "errors": errors[:MAX_ERROR_SAMPLES],
        "errors_truncated": len(errors) > MAX_ERROR_SAMPLES,
    }
