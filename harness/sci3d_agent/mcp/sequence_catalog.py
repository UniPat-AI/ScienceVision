"""One pure catalog specialization used by task generation and both runtimes."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def wire_schema(schema):
    return {"type": "object", "properties": {"args": schema},
            "required": ["args"], "additionalProperties": False}


def specialize_catalog(catalog, assets):
    """Derive task receipts without opening container-only /data paths.

    Existing entries retain their exact image-supplied hashes. A pre-extension
    image is never silently upgraded by the generator.
    """
    from .tools.vision3d.sequences import sequence_entries, TOOLS
    names = [name for name, _ in sequence_entries(assets)]
    entries = []
    for raw in catalog.get("tools", ()):
        entry = dict(raw)
        if entry["name"] == "sv3d_switch_sequence":
            if len(names) < 2:
                continue
            schema = {**TOOLS[0].input_schema,
                      "properties": {**TOOLS[0].input_schema["properties"],
                                     "sequence": {"type": "string", "enum": names}}}
            entry["schema_sha256"] = digest(wire_schema(schema))
        entries.append(entry)
    tool_names = [entry["name"] for entry in entries]
    return {**catalog, "tools": entries, "tool_names": tool_names, "tool_count": len(entries),
            "catalog_sha256": digest(tool_names), "schema_sha256": digest(entries)}


def runline_catalog(specs, family, parameters):
    """Fingerprint the unchanged 73-tool runtime's real wire registrations."""
    entries = [{"name": spec.name, "schema_sha256": digest(parameters(spec))} for spec in specs]
    names = [entry["name"] for entry in entries]
    return {"schema_version": "sciencevision.mcp_surface.v1", "server": "sciencevision",
            "viewer_family": family, "gated": bool(family), "tools": entries,
            "tool_names": names, "tool_count": len(names),
            "catalog_sha256": digest(names), "schema_sha256": digest(entries),
            "extension": {"tool": "sv3d_switch_sequence", "platform_pr": "https://github.com/UniPat-AI/BabyVision-v2/pull/132",
                          "semantics": "continuous world focus, nearest displayed slice; only declared primary/sequence NIfTI"}}


def install_runline(namespace: dict[str, Any]) -> None:
    """Add this capability without replacing a run-line server or its schemas.

    The bundled runtime has image blocks and tools absent from the external
    worker. Retain its original register/dispatch functions verbatim.
    """
    from .tools.vision3d.sequences import task_tools, TOOLS
    original_specs = namespace["tool_specs"]
    original_main = namespace["main"]

    def tool_specs(family=None):
        specs = list(original_specs(family))
        if family not in (None, "niivue.v1"):
            return tuple(specs)
        if family in (None, "niivue.v1") and not any(s.name == TOOLS[0].name for s in specs):
            specs.extend(TOOLS)
        return tuple(task_tools(specs))

    def main(argv=None):
        import sys
        args = list(sys.argv[1:] if argv is None else argv)
        if "--surface" not in args:
            return original_main(args)
        try:
            family = namespace["_viewer_family"](args)
            result = runline_catalog(tool_specs(family), family, namespace["_advertised_parameters"])
            result["legacy_surface"] = namespace["surface_for"](family, result["tool_names"]).as_dict()
            print(json.dumps(result, sort_keys=True))
            return 0
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 2

    namespace.update(tool_specs=tool_specs, main=main)
