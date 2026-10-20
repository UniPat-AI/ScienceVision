import json
import subprocess
import sys
import pytest

@pytest.mark.unit
def test_sci3d_agent_cli_help():
    out = subprocess.run([sys.executable, "-m", "sci3d_agent.cli", "--help"], capture_output=True, text=True)
    assert out.returncode == 0
    assert "--mode" in out.stdout
    assert "--task" in out.stdout

@pytest.mark.unit
def test_sci3d_bench_cli_help():
    out = subprocess.run([sys.executable, "-m", "sci3d_bench.cli", "--help"], capture_output=True, text=True)
    assert out.returncode == 0
    assert "--suite" in out.stdout
    assert "--modes" in out.stdout

@pytest.mark.unit
def test_sci3d_bench_cli_runs_seed_with_fake_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("SCI3D_USE_FAKE_LLM", "1")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "seed.json").write_text(json.dumps({
        "schema_version": "1.0",
        "task_id": "cli-seed",
        "v_class": "V6",
        "question": "How many vertices does the cube have?",
        "asset": {"path": "cube.obj", "format": "obj"},
        "prerendered_image": None,
        "answer_schema": {"kind": "structured", "fields": {"answer": "int"}},
        "ground_truth": {"value": 8},
        "scorer": {"primary": "llm_judge"},
        "provenance": {
            "source_dataset": "test",
            "source_qid": None,
            "lifted_at": "2026-05-18",
            "lifter_version": "test",
        },
    }))
    run_dir = tmp_path / "runs"
    out = subprocess.run(
        [sys.executable, "-m", "sci3d_bench.cli",
         "--suite", str(suite),
         "--modes", "mode1_no_vision",
         "--run-dir", str(run_dir),
         "--isolation", "kernel"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    files = list(run_dir.glob("*.json"))
    assert len(files) >= 1
