"""Tests for verifier-side fixes that let a generic harness (Claude Code CLI +
MCP tools) be scored fairly.

Three fixes, each pinned here:
  1. MCP-prefixed tool names (``mcp__sciencevision__sv3d_open_scene``) normalize
     to the bare name so the process predicates match.
  2. A generic harness writes the standard ``trajectory.json`` (not the sci3d
     ``sci3d_trajectory.json``); score_rewards falls back to it.
  3. Generic harness tools (Bash/Read/…) are exempt from the sci3d tool-policy
     allow-list, which only enumerates sci3d/MCP tools.

These import only the pure scoring helpers — no harbor, no Docker.

Run:  .venv-harbor/bin/python -m pytest tests/harbor_adapters/test_generic_harness_scoring.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from harbor_adapters.sci3d._verify_translate import (  # noqa: E402
    _normalize_tool_name,
    compute_process_rewards,
)


# ── 1. MCP prefix normalization ──────────────────────────────────────────── #
def test_normalize_strips_mcp_prefix():
    assert _normalize_tool_name("mcp__sciencevision__sv3d_open_scene") == "sv3d_open_scene"
    assert _normalize_tool_name("sv3d_open_scene") == "sv3d_open_scene"
    assert _normalize_tool_name("Bash") == "Bash"


def test_process_rewards_credit_mcp_prefixed_tools():
    names = [
        "mcp__sciencevision__sv3d_open_scene",
        "mcp__sciencevision__sv3d_render",
    ]
    proc = compute_process_rewards(names, allowed=None, forbidden=None)
    assert proc["process_opened_scene"] == 1.0
    assert proc["process_rendered"] == 1.0


def test_nifti_render_tools_count_as_rendered():
    proc = compute_process_rewards(
        ["mcp__sciencevision__sv_render_nifti_overlay"], None, None
    )
    assert proc["process_rendered"] == 1.0


# ── 3. generic harness tools are policy-exempt ───────────────────────────── #
def test_generic_tools_do_not_trip_policy():
    allowed = ["sv3d_open_scene", "sv_inspect_nifti"]
    names = [
        "Bash", "Read", "Write",                       # generic — must be exempt
        "mcp__sciencevision__sv3d_open_scene",          # in allow-list
        "mcp__sciencevision__sv_inspect_nifti",         # in allow-list
    ]
    proc = compute_process_rewards(names, allowed, forbidden=None)
    assert proc["process_tool_policy"] == 1.0


def test_forbidden_tool_still_trips_policy():
    proc = compute_process_rewards(
        ["Bash", "mcp__sciencevision__scipy_ndimage_label"],
        allowed=["sv_inspect_nifti"],
        forbidden=["scipy_ndimage_label"],
    )
    assert proc["process_tool_policy"] == 0.0


def test_sci3d_tool_outside_allowlist_still_trips_policy():
    # a real sci3d tool not in the allow-list is still a violation (only the
    # generic navigation tools are exempt).
    proc = compute_process_rewards(
        ["mcp__sciencevision__sv3d_segment_3d_volume"],
        allowed=["sv_inspect_nifti"],
        forbidden=None,
    )
    assert proc["process_tool_policy"] == 0.0


# ── 2. trajectory.json fallback ──────────────────────────────────────────── #
def test_score_core_falls_back_to_standard_trajectory(tmp_path):
    # only the generic trajectory.json exists (no sci3d_trajectory.json)
    traj = {
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "message": "inspecting",
                "tool_calls": [
                    {"function_name": "mcp__sciencevision__sv3d_open_scene",
                     "arguments": {}},
                ],
            }
        ]
    }
    (tmp_path / "trajectory.json").write_text(json.dumps(traj))

    # score_rewards needs numpy-backed scorers; import lazily so the prefix
    # tests above still run on a numpy-less interpreter.
    from harbor_adapters.sci3d._score_core import score_rewards

    rewards, audit = score_rewards(
        {
            "domain": "medical",
            "answer_schema": {"kind": "categorical", "fields": ["laterality"]},
            "ground_truth": {"value": {"laterality": "left"}},
            "scorer": {"primary": "exact_match"},
        },
        task_id="t1", instruction="x", agent_dir=tmp_path,
    )
    # the fallback found the trajectory -> tool_names populated, scene opened
    assert "mcp__sciencevision__sv3d_open_scene" in audit["tool_names"]
    assert rewards["process_opened_scene"] == 1.0
