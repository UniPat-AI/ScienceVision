"""Smoke tests for the fill_ground_truth CLI.

The CLI is mostly glue; we verify:
  - reads input jsonl
  - writes a new jsonl with fillers applied
  - prints the StageReport summary on stderr
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]  # repo root
SCRIPT = ROOT / "QA-Gen" / "ScienceVisionTaskGenerator" / "scripts" / "fill_ground_truth.py"


def _seed_task(gt_type: str = "expert_camera_pose") -> dict:
    return {
        "task_id": "sv-x-0001",
        "v_class": "V1",
        "answer_schema": {"kind": "camera_pose", "fields": ["azimuth"]},
        "asset": {"asset_id": "a", "name": "n", "thumb": None},
        "ground_truth": {
            "type": gt_type,
            "value": None,
            "status": "needs_expert_annotation",
        },
    }


def test_cli_writes_filled_jsonl(tmp_path: Path):
    in_path = tmp_path / "in.jsonl"
    out_path = tmp_path / "out.jsonl"
    in_path.write_text(json.dumps(_seed_task()) + "\n")

    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "QA-Gen" / "ScienceVisionTaskGenerator"), "HOME": os.environ.get("HOME", "")}
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--in", str(in_path), "--out", str(out_path), "--no-llm"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert out_path.exists()
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    out_task = json.loads(lines[0])
    assert out_task["ground_truth"]["status"] == "awaiting_expert"
    assert "Total" in res.stderr or "total" in res.stderr


def test_cli_surfaces_unexpected_wiring_error(tmp_path, monkeypatch):
    """A typo / coding bug in wiring should propagate, not degrade silently."""
    in_path = tmp_path / "in.jsonl"
    out_path = tmp_path / "out.jsonl"
    in_path.write_text(
        '{"task_id": "t1", "ground_truth": {"type": "rendered_visibility_label"}}\n'
    )

    # Force _wire_concrete_fillers to raise AttributeError (simulated typo)
    # by monkeypatching register_default at import-time via PYTHONPATH shim.
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(
        "import sciencevision_taskgen.groundtruth._rendered_visibility as m\n"
        "def _broken(**kw): raise AttributeError('simulated typo')\n"
        "m.register_default = _broken\n"
    )

    repo_root = Path(__file__).resolve().parents[3]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(repo_root / "QA-Gen" / "ScienceVisionTaskGenerator")
                       + os.pathsep + str(shim_dir),
        "HOME": os.environ.get("HOME", ""),
        # Provide creds so wiring tries to proceed (otherwise ValueError swallow path).
        "ANTHROPIC_BASE_URL": "https://example.invalid/v1",
        "ANTHROPIC_API_KEY": "sk-fake",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "QA-Gen" / "ScienceVisionTaskGenerator" / "scripts" / "fill_ground_truth.py"),
            "--in", str(in_path),
            "--out", str(out_path),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    # The typo MUST surface — either a non-zero exit or AttributeError in stderr.
    assert result.returncode != 0 or "AttributeError" in result.stderr, (
        f"Coding bug was silently swallowed.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def _seed_v3_task(gt_type: str = "camera_plus_image_annotation") -> dict:
    return {
        "task_id": "sv-test-v3-0001",
        "v_class": "V3",
        "answer_schema": {
            "kind": "visual_annotation",
            "fields": ["camera", "annotation_bbox_or_point", "region_label"],
        },
        "asset": {"asset_id": "a", "name": "n", "thumb": None},
        "ground_truth": {
            "type": gt_type,
            "value": None,
            "status": "needs_expert_annotation",
        },
    }


def test_cli_v3_camera_annotation_awaiting_expert_no_llm(tmp_path: Path):
    """With --no-llm, a V3 camera_plus_image_annotation task ends as awaiting_expert.

    Without LLM wiring, register_default() is never called and the type is no
    longer in _UNIMPLEMENTED_TYPES (Task 3 removed it), so dispatch() returns
    the 'No filler registered' fallback with awaiting_expert status.
    """
    in_path = tmp_path / "in.jsonl"
    out_path = tmp_path / "out.jsonl"
    in_path.write_text(json.dumps(_seed_v3_task()) + "\n")

    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "QA-Gen" / "ScienceVisionTaskGenerator"),
        "HOME": os.environ.get("HOME", ""),
    }
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--in", str(in_path),
            "--out", str(out_path),
            "--no-llm",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    lines = out_path.read_text().splitlines()
    assert len(lines) == 1
    out_task = json.loads(lines[0])
    assert out_task["ground_truth"]["status"] == "awaiting_expert"


def test_cli_dispatch_no_filler_registered_returns_awaiting_expert(tmp_path: Path):
    """dispatch() on an unregistered type (after removing from _UNIMPLEMENTED_TYPES)
    returns awaiting_expert with 'No filler registered' notes.

    This test validates the registry.dispatch() defensive branch directly,
    independent of the CLI subprocess.
    """
    from sciencevision_taskgen.groundtruth.registry import dispatch, FILLERS

    # Ensure camera_plus_image_annotation is NOT in FILLERS for this test.
    # In --no-llm mode register_default() is never called, so it won't be.
    original = FILLERS.pop("camera_plus_image_annotation", None)
    try:
        task = {
            "task_id": "sv-no-filler",
            "ground_truth": {"type": "camera_plus_image_annotation"},
            "answer_schema": {"fields": ["camera"]},
            "asset": {},
        }
        r = dispatch(task)
        assert r.status == "awaiting_expert"
        assert "No filler registered" in r.notes or "no filler" in r.notes.lower()
    finally:
        # Restore original filler (if any) to not pollute other tests.
        if original is not None:
            FILLERS["camera_plus_image_annotation"] = original
