"""Trajectory replay tests for sv3d tool-use evaluation (Task 8).

These tests do NOT depend on Claude Code. They exercise:

1. **Determinism**: applying a canned tool-call sequence twice from the same
   reset state must produce identical camera readings.
2. **Replay function smoke**: the ``replay`` function in
   ``scripts/eval/sv3d_run_scenarios.py`` accepts a JSONL of Anthropic-style
   tool calls and writes ``trajectory.jsonl`` + ``final_state.json``.
3. **Error envelope surfacing**: when a handler returns ``{"error": ...}``,
   ``replay`` records it without aborting the rest of the trajectory.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sci3d_agent.mcp.registry import REGISTRY

# --- one-time module load (avoid re-import on every test) --------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPLAY_PATH = _REPO_ROOT / "scripts" / "eval" / "sv3d_run_scenarios.py"
_EMIT_PATH = _REPO_ROOT / "scripts" / "eval" / "sv3d_emit_viewer_urls.py"


def _load_script_module(path: Path, cache_key: str) -> ModuleType:
    """Load a ``scripts/eval/<name>.py`` module from disk (no packaging)."""
    cached = sys.modules.get(cache_key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(cache_key, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[cache_key] = module
    return module


def _load_replay_module() -> ModuleType:
    return _load_script_module(_REPLAY_PATH, "_sv3d_run_scenarios_test_cache")


def _load_emit_module() -> ModuleType:
    return _load_script_module(_EMIT_PATH, "_sv3d_emit_viewer_urls_test_cache")


def _state(sid: str) -> dict[str, Any]:
    return REGISTRY["sv3d_get_state"].handler({"session_id": sid})


# --- 1. determinism ---------------------------------------------------------


def test_canned_s2_orbit_zoom_deterministic(mesh_session: str) -> None:
    """S2-style trajectory must yield the same camera state across two runs."""
    sid = mesh_session
    calls: list[tuple[str, dict[str, Any]]] = [
        ("sv3d_camera_set_view", {"session_id": sid, "view": "front"}),
        (
            "sv3d_camera_orbit",
            {"session_id": sid, "d_azimuth_deg": 45.0, "d_elevation_deg": 0.0},
        ),
        ("sv3d_camera_dolly", {"session_id": sid, "factor": 0.5}),
    ]

    for name, args in calls:
        REGISTRY[name].handler(args)
    s1 = _state(sid)

    # Reset and replay exactly the same calls.
    REGISTRY["sv3d_camera_set_view"].handler({"session_id": sid, "view": "reset"})
    for name, args in calls:
        REGISTRY[name].handler(args)
    s2 = _state(sid)

    assert s2["camera"]["position"] == pytest.approx(
        s1["camera"]["position"], abs=1e-9
    )
    assert s2["camera"]["target"] == pytest.approx(
        s1["camera"]["target"], abs=1e-9
    )


# --- 2. replay function smoke -----------------------------------------------


def test_replay_function_runs_tool_calls_jsonl(
    mesh_session: str, tmp_path: Path
) -> None:
    sid = mesh_session
    tool_calls_path = tmp_path / "tool_calls.jsonl"
    lines = [
        json.dumps(
            {
                "name": "sv3d_camera_set_view",
                "arguments": {"session_id": sid, "view": "front"},
            }
        ),
        json.dumps(
            {
                "name": "sv3d_camera_orbit",
                "arguments": {
                    "session_id": sid,
                    "d_azimuth_deg": 30.0,
                    "d_elevation_deg": 10.0,
                },
            }
        ),
    ]
    tool_calls_path.write_text("\n".join(lines) + "\n")

    out_dir = tmp_path / "out"
    module = _load_replay_module()
    summary = module.replay(tool_calls_path, out_dir)

    assert summary["n_steps"] == 2
    assert summary["errors"] == 0
    assert summary["final_state"]["camera"]["position"]
    assert (out_dir / "final_state.json").exists()
    assert (out_dir / "trajectory.jsonl").exists()


# --- 3. error-envelope surfacing --------------------------------------------


def test_replay_records_error_envelope_without_aborting(
    mesh_session: str, tmp_path: Path
) -> None:
    sid = mesh_session
    tool_calls_path = tmp_path / "tool_calls.jsonl"
    lines = [
        # First call: out-of-range factor → returns {"error": ...} per Task 3.
        json.dumps(
            {
                "name": "sv3d_camera_dolly",
                "arguments": {"session_id": sid, "factor": 50.0},
            }
        ),
        # Second call: valid dolly. Must still execute.
        json.dumps(
            {
                "name": "sv3d_camera_dolly",
                "arguments": {"session_id": sid, "factor": 0.5},
            }
        ),
    ]
    tool_calls_path.write_text("\n".join(lines) + "\n")

    out_dir = tmp_path / "out"
    module = _load_replay_module()
    summary = module.replay(tool_calls_path, out_dir)

    assert summary["n_steps"] == 2
    assert summary["errors"] == 1

    trajectory = [
        json.loads(line)
        for line in (out_dir / "trajectory.jsonl").read_text().splitlines()
        if line
    ]
    assert trajectory[0]["result"].get("error")
    assert "error" not in trajectory[1]["result"]


# --- 4. emitter: build_url_params + URL fallback ----------------------------


def test_emitter_packs_mesh_state_into_gltf_url() -> None:
    """Mesh + gltf → URL with cam= and opacity=, no slice= (mesh has no MPR)."""
    state = {
        "kind": "mesh",
        "camera": {
            "position": [1.0, 2.0, 3.0],
            "target": [0.0, 0.0, 0.0],
            "up": [0.0, 0.0, 1.0],
            "fov_deg": 45.0,
        },
        "opacity": {"base": 0.4, "mask": 1.0, "clip_cap": 1.0},
        "slice": {"axis": "axial", "fraction": 0.0, "window": [0.0, 1.0], "level": 0.5},
    }
    emit = _load_emit_module()
    params = emit.build_url_params(state)

    assert "cam" in params and len(params["cam"]) > 10  # base64url is non-trivial
    assert params["opacity"] == "base:0.4,mask:1.0,clip_cap:1.0"
    assert "slice" not in params  # mesh sessions skip slice


def test_emitter_packs_nifti_slice_with_window_width() -> None:
    """NIfTI MPR → slice=<axis>:<fraction>:<width>:<level> matches decodeSlice."""
    state = {
        "kind": "nifti",
        "camera": {
            "position": [10.0, 20.0, 30.0],
            "target": [0.0, 0.0, 0.0],
            "up": [0.0, 0.0, 1.0],
            "fov_deg": 60.0,
        },
        "opacity": {"base": 1.0, "mask": 0.5, "clip_cap": 1.0},
        "slice": {
            "axis": "coronal",
            "fraction": 0.5,
            "window": [-160.0, 240.0],
            "level": 40.0,
        },
    }
    emit = _load_emit_module()
    params = emit.build_url_params(state)

    # width = high - low = 240 - (-160) = 400 (matches the MCP set_slice input).
    assert params["slice"] == "coronal:0.5:400.0:40.0"


def test_emitter_drops_unsupported_params_for_non_gltf_viewer() -> None:
    """When viewer rejects cam/opacity/slice, fallback strips them one by one."""
    emit = _load_emit_module()
    params = {
        "cam": "AAA",
        "opacity": "base:0.4",
        "slice": "axial:0.5:400.0:40.0",
    }
    result = emit._build_url_with_param_fallback(
        "/datasets/foo.nii.gz", "niivue", params
    )
    assert "error" not in result, result
    # Final URL must include file= but not the dropped state params.
    assert "file=" in result["url"]
    assert "cam=" not in result["url"]
    assert "opacity=" not in result["url"]
    assert "slice=" not in result["url"]
