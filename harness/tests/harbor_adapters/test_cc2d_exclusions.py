from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import build_harbor_tasks as gen  # noqa: E402


def test_expected_codes_are_the_enumerated_fail_closed_set():
    for code in ("view_plan_incomplete", "medical_roi_plan_unavailable",
                 "medical_roi_not_visible", "medical_extent_bbox_unavailable",
                 "ocp_unavailable", "step_read_failed",
                 "step_tessellation_failed", "step_tessellation_empty",
                 "step_plan_unroutable", "step_format_unsupported"):
        assert code in gen.CC2D_EXPECTED_EXCLUSION_CODES, code
    # Real faults must abort the batch, not be receipt-ed away.
    for unexpected in ("renderer_exception", "renderer_produced_no_png",
                       "renderer_unavailable", "cc2d_sensitive_path_leakage",
                       "nifti_renderer_identity_invalid"):
        assert unexpected not in gen.CC2D_EXPECTED_EXCLUSION_CODES, unexpected


def test_build_one_writes_exclusion_sidecar_before_removing_the_tree(tmp_path):
    # physics glb: plan_views returns None -> view_plan_incomplete
    task = {"task_id": "t-x", "asset_format": "glb", "domain": "physics",
            "asset_path": str(tmp_path / "s.glb")}
    (tmp_path / "s.glb").write_bytes(b"x")
    out = tmp_path / "tasks"
    with pytest.raises(gen.RenderVisibilityError, match="view_plan_incomplete"):
        gen.build_one(task, out, containerized=True, agent_env="codex-2d")
    sidecar = json.loads((out / "t-x.cc2d_excluded.json").read_text())
    assert sidecar["task_id"] == "t-x"
    assert sidecar["failure_reason"] == "view_plan_incomplete"
    assert sidecar["agent_env"] == "codex-2d"
    assert not (out / "t-x").exists()  # the tree is still fail-closed removed


def test_sidecar_is_not_written_for_non_2d_routes(tmp_path):
    # An eligible task with a *missing* asset fails the codex route with
    # FileNotFoundError; only 2d routes may leave the cc2d sidecar behind.
    task = {"task_id": "t-y", "asset_format": "obj",
            "asset_path": str(tmp_path / "missing.obj"),
            "evaluation_eligible": True}
    out = tmp_path / "tasks"
    with pytest.raises(FileNotFoundError):
        gen.build_one(task, out, containerized=True, agent_env="codex")
    assert not (out / "t-y.cc2d_excluded.json").exists()


def test_cli_records_the_exclusion_and_continues(tmp_path):
    jsonl = tmp_path / "tasks.jsonl"
    # Eligibility is re-derived by normalize_eval_contract from the ground truth
    # and a supported deterministic scorer; a bare top-level True is not enough.
    (tmp_path / "s.glb").write_bytes(b"x")
    jsonl.write_text(json.dumps({
        "task_id": "t-bad", "domain": "physics", "instruction": "x",
        "asset": {"path": str(tmp_path / "s.glb"), "format": "glb"},
        "answer_schema": {"kind": "freeform", "fields": ["label"]},
        "scorer": {"primary": "exact_match"},
        "ground_truth": {"value": {"label": "a"}, "status": "computed"},
        "n_views": 3,
    }) + "\n")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.harbor.qa_gen_to_tasks",
         "--jsonl", str(jsonl), "--out", str(out), "--containerized",
         "--agent-env", "codex-2d", "--n-views", "3"],
        cwd=_ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(_ROOT)},
    )
    assert proc.returncode == 0, proc.stderr
    receipt = [json.loads(line) for line in
               (out / "cc2d_exclusions.jsonl").read_text().splitlines()]
    assert receipt == [{"agent_env": "codex-2d", "failure_reason": "view_plan_incomplete",
                        "n_views": 3, "task_id": "t-bad"}]
    assert "excluded 1 cc2d task" in proc.stdout


def test_cli_reraises_unexpected_codes(tmp_path, monkeypatch):
    # An unexpected code must abort instead of becoming a receipt line.
    from scripts.harbor import qa_gen_to_tasks as q2t

    def fake_build_one(task, out, **kwargs):
        raise gen.RenderVisibilityError("renderer_exception")

    monkeypatch.setattr(q2t, "build_one", fake_build_one)
    jsonl = tmp_path / "tasks.jsonl"
    jsonl.write_text(json.dumps({
        "task_id": "t-fault", "instruction": "x",
        "asset": {"path": str(tmp_path / "s.obj"), "format": "obj"},
        "answer_schema": {"kind": "freeform", "fields": ["label"]},
        "scorer": {"primary": "exact_match"},
        "ground_truth": {"value": {"label": "a"}, "status": "computed"},
        "n_views": 1,
    }) + "\n")
    with pytest.raises(gen.RenderVisibilityError, match="renderer_exception"):
        monkeypatch.setattr(sys, "argv", [
            "qa_gen_to_tasks", "--jsonl", str(jsonl), "--out", str(tmp_path / "out2"),
            "--containerized", "--agent-env", "codex-2d", "--n-views", "1",
        ])
        q2t.main()


def test_tree_audit_accepts_receipt_driven_exclusions(tmp_path):
    from scripts.harbor import build_v21_ev2_trees as bv

    tree = tmp_path / "tasks-codex-cc2d-1"
    # Only "a" has a built tree; "b" is excluded with a receipt line.
    env = tree / "a" / "environment"
    (env / "views").mkdir(parents=True)
    (env / "Dockerfile").write_text("FROM x\n")
    (env / "views" / "view_00.png").write_bytes(b"png")
    (env / "render_manifest.json").write_text("{}")
    (tree / "a" / "instruction.md").write_text("no_asset_access\n")
    (tree / "a" / "task.toml").write_text('docker_image = "sci3d-scorer:tag"\n')
    (tree / "cc2d_exclusions.jsonl").write_text(
        json.dumps({"task_id": "b", "failure_reason": "view_plan_incomplete"}) + "\n")

    report = bv.audit(tree, "cc2d-1", "codex", {"a", "b"}, "sci3d-scorer:tag", "suffix")
    assert report["excluded"] == ["b"]
    assert report["excluded_reasons"] == {"view_plan_incomplete": 1}

    # A missing id with no receipt is still a hard failure.
    with pytest.raises(SystemExit):
        bv.audit(tree, "cc2d-1", "codex", {"a", "b", "c"}, "sci3d-scorer:tag", "suffix")
