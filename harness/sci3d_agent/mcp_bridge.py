"""MCP bridge: expose `sci3d_agent.mcp.REGISTRY` as agent-callable tools.

Two pure-function entry points:

* :func:`list_mcp_tool_schemas` — produces the Anthropic tool-use shape
  ``{name, description, input_schema}`` for every tool in
  ``sci3d_agent.mcp.tools.ALL_TOOLS``. Suitable as the ``tools=`` argument
  of an LLM client call.
* :func:`dispatch_mcp` — looks up a :class:`ToolCall` by name in the
  per-process tool index, calls its handler with the args mapping, and
  returns the result dict unchanged (handlers already use the
  ``{error: ...}`` envelope on failure, so we don't translate exceptions
  here).

Both functions are read-only; they mutate nothing. ``ALL_TOOLS`` is the
canonical source — some tool modules (E category) build their TOOLS list
without calling ``register()``, so REGISTRY alone undercounts. We index
``ALL_TOOLS`` once at module import so both routes agree on the same tool count.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from sci3d_agent.llm_client import ToolCall
from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.tools import ALL_TOOLS


def _build_index(tools: list[ToolSpec]) -> dict[str, ToolSpec]:
    out: dict[str, ToolSpec] = {}
    for spec in tools:
        if spec.name in out:
            raise ValueError(f"duplicate tool in ALL_TOOLS: {spec.name}")
        out[spec.name] = spec
    return out


_INDEX: dict[str, ToolSpec] = _build_index(ALL_TOOLS)


_PERMISSIVE_TYPES = ["string", "object", "number", "integer", "boolean", "array", "null"]


def _type_array_to_anyof(types: list[str]) -> dict[str, Any]:
    """Convert ``{"type": ["a", "b"]}`` shape into the ``anyOf`` shape
    that Anthropic's strict tool-schema validator accepts.
    """
    return {"anyOf": [{"type": t} for t in types]}


def _relax_schema_for_llm(schema: Any) -> Any:
    """Return a copy of *schema* that strict LLM validators accept.

    The canonical MCP schemas use a few constructs that Anthropic /
    Bedrock strict-reject (the proxy surfaces "JSON schema is invalid.
    It must match JSON Schema draft 2020-12"):

    1. ``oneOf: [{required:[a]}, {required:[b]}]`` at root, alongside
       ``additionalProperties: false`` — the XOR-required-keys pattern
       is rejected. The handler-side error envelope already enforces
       "must provide a or b", so we drop the ``oneOf`` and let the
       handler do the validation. The schema becomes a strict relaxation.

    2. Property definitions lacking any of ``type`` / ``enum`` /
       ``$ref`` / ``oneOf`` / ``anyOf`` (e.g. ``predicted_answer`` whose
       runtime type depends on the V-class). Permissive ``anyOf`` added
       so the LLM can pass any JSON value.

    3. ``"type": ["string", "integer"]`` type-array shorthand. Draft
       2020-12 allows it but Anthropic's validator does not — rewrite
       to ``anyOf: [{"type": "string"}, {"type": "integer"}]``.

    Canonical ``ToolSpec.input_schema`` is left untouched; only the
    LLM-facing copy is relaxed.
    """
    if not isinstance(schema, dict):
        return schema
    out = dict(schema)
    # Type-array shorthand → anyOf.
    if isinstance(out.get("type"), list):
        types = out.pop("type")
        out = {**_type_array_to_anyof(types), **out}
    # Recurse: properties.
    if "properties" in out and isinstance(out["properties"], dict):
        new_props = {}
        for pname, pdef in out["properties"].items():
            if isinstance(pdef, dict):
                fixed = _relax_schema_for_llm(pdef)
                # Property must carry a type marker for strict validators.
                if not any(
                    k in fixed for k in ("type", "enum", "$ref", "oneOf", "anyOf")
                ):
                    fixed = {**fixed, **_type_array_to_anyof(_PERMISSIVE_TYPES)}
                new_props[pname] = fixed
            else:
                new_props[pname] = pdef
        out["properties"] = new_props
    # Recurse: items.
    if "items" in out:
        out["items"] = _relax_schema_for_llm(out["items"])
    # Recurse: combinators.
    for combinator in ("oneOf", "anyOf", "allOf"):
        if combinator in out and isinstance(out[combinator], list):
            out[combinator] = [_relax_schema_for_llm(s) for s in out[combinator]]
    # Drop root/object-level oneOf when its branches are nothing but
    # `required` lists — the XOR-required pattern. Handler enforces.
    if "oneOf" in out and isinstance(out["oneOf"], list):
        branches = out["oneOf"]
        all_required_only = all(
            isinstance(b, dict) and set(b.keys()) <= {"required"} for b in branches
        )
        if all_required_only:
            del out["oneOf"]
    return out


def list_mcp_tool_schemas(
    names: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Return tool schemas in Anthropic tool-use shape.

    With ``names`` omitted (the default), every tool in ``ALL_TOOLS`` is
    returned (mode4_mcp's full toolkit). Pass an iterable of tool names to
    restrict the result to that subset — used by the medical MPR mode to
    advertise only the inspect/MPR/segment tools a human would reach for in
    the viewer. Unknown names raise ``ValueError`` so a typo in a mode's
    curated list fails loudly at profile-build time rather than silently
    dropping a tool.

    Schemas are passed through :func:`_relax_schema_for_llm` so they
    survive strict draft 2020-12 validators behind proxies (Mindra →
    Anthropic / Bedrock / Google). Handlers keep enforcing the
    canonical constraints at call time.
    """
    if names is None:
        selected = list(ALL_TOOLS)
    else:
        wanted = list(names)
        missing = [n for n in wanted if n not in _INDEX]
        if missing:
            raise ValueError(f"unknown MCP tool name(s): {missing}")
        selected = [_INDEX[n] for n in wanted]
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": _relax_schema_for_llm(spec.input_schema),
        }
        for spec in selected
    ]


def is_mcp_tool(name: str) -> bool:
    """True iff *name* is a registered MCP tool."""
    return name in _INDEX


def dispatch_mcp(call: ToolCall) -> Mapping[str, Any]:
    """Dispatch *call* through the registered handler.

    The handler is expected to return either a normal result dict or an
    ``{"error": "..."}`` envelope. Unknown tool names raise ``ValueError``
    so the dispatcher in ``dispatch.py`` can decide whether to fall back
    to the legacy tool routes.
    """
    spec = _INDEX.get(call.name)
    if spec is None:
        raise ValueError(f"MCP tool not registered: {call.name!r}")
    return spec.handler(call.args)
