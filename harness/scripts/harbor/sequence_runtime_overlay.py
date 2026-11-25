"""Stage the sequence extension over the audited 73-tool bundled runtime.

No old renderer, tool schema, dependency, or agent CLI is replaced. The source
fingerprints make an unexpected base a hard failure rather than a partial port.
The caller supplies a source-only snapshot extracted from the chosen image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

_BASE = {
    "sci3d_agent/mcp/tools/vision3d/_types.py": "cd536a365d68f56549a44c985ed0d703eed6374ec492956fb10656183e80a26c",
    "sci3d_agent/mcp/tools/vision3d/session.py": "45419b8458082d0ab4beec602f7b81db1ca92475f6b24d5265ee4d2ff3990c78",
    "sci3d_agent/mcp/tools/vision3d/__init__.py": "e97e5a253f4b7b51d86222b7c4c2180e2aa84d97461ed81d3402192c1401663b",
    "sci3d_agent/mcp/server.py": "0d7a77a78492cb9ca29873fc2add8e0f481495e840badef4590d21e467c1120b",
    "sci3d_agent/mcp/tools/vision3d/_state_view.py": "2e4de16d4182fce0124231dfb1e37d8080ae717e326db415c07494de2ac30c22",
    "sci3d_agent/mcp/tools/vision3d/_store.py": "c8489ffdf770362e94fc5c92355b14266a0982e52340e3d3eb0b8c866e0d7755",
    "sci3d_agent/mcp/tools/vision3d/_crosshair.py": "b286f0063f77c37ee363500c5635007e7857f5f2a8e73d0fba6d7d9c4f957bad",
}
_NEW = ("sci3d_agent/mcp/tools/vision3d/sequences.py", "sci3d_agent/mcp/sequence_catalog.py")


def _replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("runtime hook did not match exactly once")
    return text.replace(before, after)


def build_overlay(snapshot: Path, source: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("overlay output must be a new directory")
    originals = {name: (snapshot / name).read_bytes() for name in _BASE}
    if any(hashlib.sha256(originals[name]).hexdigest() != expected for name, expected in _BASE.items()):
        raise ValueError("bundled runtime source does not match the audited base")
    result = {name: raw.decode() for name, raw in originals.items()}
    types = "sci3d_agent/mcp/tools/vision3d/_types.py"
    # Session is the final class. Append defaults AFTER all existing fields,
    # including run-line molecule, oblique_plane, components and selection.
    extension = (source / types).read_text().split("    sequences: tuple[tuple[str, str], ...] = ()", 1)[1]
    result[types] += "\n    sequences: tuple[tuple[str, str], ...] = ()" + extension
    session = "sci3d_agent/mcp/tools/vision3d/session.py"
    result[session] = _replace_once(result[session], "        try:\n            get_store().add(sess)",
                                   "        from .sequences import initialize_sequences\n        sess = initialize_sequences(sess)\n        try:\n            get_store().add(sess)")
    state = "sci3d_agent/mcp/tools/vision3d/_state_view.py"
    result[state] = _replace_once(result[state], "    ttl_left = max(0, int(s.ttl_seconds - (monotonic_now() - s.created_at)))",
                                 "    from .sequences import sequence_state\n    ttl_left = max(0, int(s.ttl_seconds - (monotonic_now() - s.created_at)))")
    result[state] = _replace_once(result[state], '        "ttl_remaining_s": ttl_left,',
                                 '        "ttl_remaining_s": ttl_left,\n        **sequence_state(s),')
    store = "sci3d_agent/mcp/tools/vision3d/_store.py"
    result[store] = _replace_once(result[store], "    new = replace(s, slice=slice_state)",
                                 "    from .sequences import update_slice_focus\n    new = update_slice_focus(s, slice_state)")
    crosshair = "sci3d_agent/mcp/tools/vision3d/_crosshair.py"
    result[crosshair] = _replace_once(result[crosshair], "        replace_slice(sid, new_slice)",
                                     "        replace_slice(sid, new_slice)\n        from .sequences import remember_crosshair\n        remember_crosshair(sid, planes)")
    result[session] = _replace_once(result[session], "    except SVToolError as exc:\n        return {\"error\": str(exc)}",
                                   "    except (SVToolError, ValueError, OSError) as exc:\n        return {\"error\": str(exc)}")
    init = "sci3d_agent/mcp/tools/vision3d/__init__.py"
    result[init] += "\nfrom sci3d_agent.mcp.tools.vision3d.sequences import TOOLS as _sequences\nTOOLS += _sequences\n"
    server = "sci3d_agent/mcp/server.py"
    result[server] = _replace_once(result[server], 'if __name__ == "__main__":',
                                  'from sci3d_agent.mcp.sequence_catalog import install_runline\ninstall_runline(globals())\n\n\nif __name__ == "__main__":')
    for name in _NEW:
        result[name] = (source / name).read_text()
    report = {"schema_version": "sciencevision.sequence_overlay.v1", "base_files": _BASE, "output_files": {}}
    output.mkdir(parents=True)
    for name, text in result.items():
        compile(text, name, "exec")
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        report["output_files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "sequence-overlay.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_overlay(args.snapshot, args.source, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
