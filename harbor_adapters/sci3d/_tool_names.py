"""How to read a tool name: normalisation and the three classifications on it.

Extracted from ``_verify_translate`` so ``_capability_policy`` and
``_tool_outcomes`` can share one definition rather than each keeping a copy of
the MCP-prefix rule. A drifted copy of ``normalize_tool_name`` would mean the
capability scan and the tool-policy check disagreed about whether
``mcp__sciencevision__sv3d_render`` is ``sv3d_render`` -- and the disagreement
would only surface as a policy that quietly stopped matching.

``_verify_translate`` re-exports ``normalize_tool_name`` under its historical
private name, so nothing that imported it needs to change.
"""
from __future__ import annotations

# Generic harness tools (Claude Code CLI, Codex, ...) used for navigation, not
# scientific inspection. A sci3d ``allowed_tools`` whitelist enumerates only the
# sci3d/MCP tools; it never lists Bash/Read/etc.
GENERIC_HARNESS_TOOLS = frozenset({
    "Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "LS",
    "Task", "TodoWrite", "NotebookEdit", "WebFetch", "WebSearch",
    "BashOutput", "KillShell", "apply_patch", "shell", "container_exec",
})

# Tools whose *arguments* are agent-authored text that becomes executed code.
# Two families, both required:
#   - execution: the command itself carries the forbidden call
#   - authoring: an agent normally writes ``analyze.py`` and then runs
#     ``python analyze.py``, so the forbidden call lives in the write and the
#     command names only the script. Scanning execution alone misses that, and
#     it is ordinary agent behaviour rather than deliberate evasion.
#
# Deliberately excluded: ``Read``/``Grep``/``Glob`` (cannot execute anything --
# reading a staged answer is T1's staging defect, not a capability breach) and
# ``TodoWrite`` (prose intent is not execution).
CODE_AUTHORING_TOOLS = frozenset({
    "bash", "shell", "sh", "zsh", "run_command", "runcommand", "execute_command",
    "python", "run_python", "exec", "container_exec", "local_shell",
    "write", "edit", "multiedit", "notebookedit", "create_file", "apply_patch",
    "str_replace_editor", "task",
})

# Bare sci3d tool-name prefixes. The sci3d agent calls ``sv3d_render``; the
# in-container Claude Code CLI calls the same tool ``mcp__sciencevision__sv3d_render``.
_SCI3D_PREFIXES = ("sv3d_", "sv_")


def normalize_tool_name(name: str | None) -> str:
    """Strip the MCP wrapper prefix so MCP-style names match the bare sets.

    ``mcp__<server>__<tool>`` -> ``<tool>``; anything else is returned unchanged.
    """
    text = str(name or "")
    if text.startswith("mcp__"):
        return text.split("__", 2)[-1]
    return text


def is_mcp_call(name: str | None) -> bool:
    """True for a sciencevision/MCP tool call under either harness.

    Both spellings must land in the same bucket: otherwise the cc-vs-ccmcp
    ablation would compare an ``mcp__``-counted numerator against a bare-name
    denominator.
    """
    raw = str(name or "")
    if raw.startswith("mcp__"):
        return True
    bare = normalize_tool_name(raw)
    return any(bare.startswith(prefix) for prefix in _SCI3D_PREFIXES)


def is_generic_harness_tool(name: str | None) -> bool:
    """True for navigation tools that no sci3d allow-list ever enumerates."""
    return normalize_tool_name(name) in GENERIC_HARNESS_TOOLS


def is_code_authoring_tool(name: str | None) -> bool:
    """True when this tool's arguments are agent-authored, executable text."""
    return normalize_tool_name(name).lower() in CODE_AUTHORING_TOOLS
