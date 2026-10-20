"""Coverage for ``run_end2end``'s route-matrix parameter (``--matrix``).

The T0 irreducibility probe has to run under *exactly* the knobs the leaderboard
matrix ran under, or the labels it produces cannot qualify the numbers they exist
to qualify. That is why the matrix is a parameter threaded through the existing
code path rather than a second driver: a parallel implementation is free to
drift on timeouts, task-tree construction, or ``--ae``, and the drift would be
invisible in the artifact.

These tests pin the three properties that make the refactor safe:

  * the six-baseline default is byte-identical to before the parameter existed
  * ``t0-probe`` runs only ``codex-text``, on the text-only image, with no MCP
  * the probe's freshness check is scoped to its own dirs, so the probe can be
    added to a jobs root that already holds a finished six-baseline run

Run:  python -m pytest tests/harbor_adapters/test_end2end_matrix.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.harbor.run_end2end as E  # noqa: E402
from scripts.harbor._mcp_route_runtime import NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT  # noqa: E402

_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    "hAJ/wlseKgAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _explicit_codex_runtime(monkeypatch):
    """CODEX_MODEL is fail-closed by design and never inferred from local config."""
    monkeypatch.setenv("CODEX_MODEL", "model-x")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("CODEX_AUTH_JSON_PATH", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


# ── 1. the matrix registry ───────────────────────────────────────────────── #
def test_probe_matrix_is_the_text_route_only():
    assert E.PROBE_BASELINES == [("codex-text", "codex-text", None)]


def test_six_is_the_default_and_is_the_leaderboard_matrix():
    assert E.MATRICES["six"] is E.BASELINES
    assert E.MATRICES["t0-probe"] is E.PROBE_BASELINES


def test_3d_mcp_matrix_is_the_tools_only_twin_of_ccmcp():
    assert E.MATRICES["3d-mcp"] == [("3d-mcp", "codex-mcp", None)]


def test_omitting_the_matrix_argument_preserves_the_six_baseline_behaviour():
    """Every threaded call site must default to BASELINES, so no existing
    caller changes meaning by not passing the new parameter."""
    jobdirs = {label: f"/jobs/job-{label}" for label, _, _ in E.BASELINES}
    assert E.build_run_specs(jobdirs, "model-x") == E.build_run_specs(
        jobdirs, "model-x", E.BASELINES
    )
    assert len(E.build_run_specs(jobdirs, "model-x")) == 6


def test_build_run_specs_on_the_probe_matrix_emits_only_the_probe_route():
    jobdirs = {"codex-text": "/jobs/job-codex-text", "cc": "/jobs/job-cc"}
    specs = E.build_run_specs(jobdirs, "model-x", E.PROBE_BASELINES)
    assert specs == ["codex-text:model-x:/jobs/job-codex-text"]


# ── 2. freshness is scoped to the matrix being run ───────────────────────── #
def test_probe_may_land_beside_a_finished_six_baseline_run(tmp_path: Path):
    """The file half of T0 reuses the matrix's own job-cc, so the probe is
    normally launched into that same jobs root. Scoping the freshness check to
    the matrix is what makes that possible without a stale-artifact false alarm."""
    (tmp_path / "job-cc").mkdir()
    (tmp_path / "tasks-cc2d-12").mkdir()

    E._require_fresh_jobs_root(tmp_path, E.PROBE_BASELINES)  # must not raise

    with pytest.raises(SystemExit):
        E._require_fresh_jobs_root(tmp_path, E.BASELINES)


def test_3d_mcp_may_land_beside_a_finished_six_baseline_run(tmp_path: Path):
    """The arm reuses the six-run's own job-ccmcp as its control: same task
    trees and worker image, shell disabled as the only difference."""
    (tmp_path / "job-cc").mkdir()
    (tmp_path / "job-ccmcp").mkdir()

    E._require_fresh_jobs_root(tmp_path, E.MATRICES["3d-mcp"])  # must not raise

    (tmp_path / "job-3d-mcp").mkdir()
    with pytest.raises(SystemExit):
        E._require_fresh_jobs_root(tmp_path, E.MATRICES["3d-mcp"])


def test_probe_still_refuses_to_overwrite_its_own_prior_artifacts(tmp_path: Path):
    (tmp_path / "job-codex-text").mkdir()
    with pytest.raises(SystemExit):
        E._require_fresh_jobs_root(tmp_path, E.PROBE_BASELINES)


# ── 3. fan-out honours the matrix ────────────────────────────────────────── #
def test_run_all_on_the_probe_matrix_issues_one_call_without_mcp():
    calls: list[list[str]] = []
    E._run_all(
        {"codex-text": Path("/tmp/tasks-codex-text")},
        {"codex-text": "/jobs/job-codex-text"},
        "model-x", ["CODEX_FORCE_AUTH_JSON=1"],
        max_workers=1, runner=calls.append, baselines=E.PROBE_BASELINES,
    )
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[cmd.index("--agent") + 1] == "codex"
    assert "--mcp-config" not in cmd, "the text probe must not get MCP tools"
    assert cmd[cmd.index("-o") + 1] == "/jobs/job-codex-text"


def test_run_all_on_the_probe_matrix_keeps_the_shared_timeout_guard():
    """The probe inherits the same --harbor-extra allowlist; a baseline-semantic
    override must not become reachable through the new route."""
    with pytest.raises(ValueError):
        E._run_all(
            {"codex-text": Path("/tmp/t")}, {"codex-text": "/jobs/j"},
            "model-x", [], max_workers=1, runner=lambda cmd: None,
            baselines=E.PROBE_BASELINES, harbor_extra=("--agent", "claude-code"),
        )


def test_run_all_on_the_3d_mcp_matrix_keeps_the_worker_and_disables_the_shell():
    calls: list[list[str]] = []
    E._run_all(
        {"3d-mcp": Path("/tmp/tasks-3d-mcp")},
        {"3d-mcp": "/jobs/job-3d-mcp"},
        "model-x", ["CODEX_FORCE_AUTH_JSON=1"],
        max_workers=1, runner=calls.append, baselines=E.MATRICES["3d-mcp"],
    )
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[cmd.index("--agent") + 1] == NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT
    assert cmd[cmd.index("--mcp-config") + 1] == str(E.MCP_CONFIG)
    assert cmd[cmd.index("-o") + 1] == "/jobs/job-3d-mcp"


# ── 4. end to end through main(), on the real task builder ───────────────── #
def _smoke_tasks(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    tasks_jsonl = tmp_path / "tasks.jsonl"
    tasks_jsonl.write_text(json.dumps({
        "task_id": "smoke1",
        "viewer_link": "https://x/viewer/gltf.html?file=assets/m.glb",
        "question": "how many parts?",
        "ground_truth": {"value": "1"},
    }) + "\n")
    return tasks_jsonl


def test_main_matrix_t0_probe_builds_only_the_text_only_tree(tmp_path, monkeypatch, capsys):
    import scripts.harbor.build_harbor_tasks as gen
    monkeypatch.setattr(
        gen, "_lookup_render_handler",
        lambda name: (lambda args: {"image_png_base64": _PNG_B64}),
    )
    monkeypatch.setattr(E, "_run_all", lambda *a, **k: None)
    monkeypatch.setattr(E, "_collect", lambda *a, **k: None)
    monkeypatch.setattr(E, "_plot", lambda *a, **k: None)

    tasks_jsonl = _smoke_tasks(tmp_path)
    jobs_root = tmp_path / "jobs"
    E.main([
        "--tasks", str(tasks_jsonl), "--set-name", "Smoke",
        "--asset-root", str(tmp_path),
        "--out-root", str(tmp_path / "out"), "--jobs-root", str(jobs_root),
        "--matrix", "t0-probe", "--dry-run",
    ])

    built = sorted(p.name for p in jobs_root.iterdir() if p.is_dir())
    assert built == ["tasks-codex-text"], "the probe must not build the 6 matrix trees"

    task_dir = jobs_root / "tasks-codex-text" / "smoke1"
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    assert "COPY asset" not in dockerfile, "the text probe stages no asset"
    assert "COPY views" not in dockerfile, "the text probe stages no renders"

    instruction = (task_dir / "instruction.md").read_text()
    block = re.search(r"```sci3d\s*\n(.*?)```", instruction, re.DOTALL)
    if block is not None:
        payload = json.loads(block.group(1).strip())
        assert not payload.get("views"), "the text probe must carry no views"

    out = capsys.readouterr().out
    assert "codex-text:model-x:" in out
    assert "cc2d-12:" not in out
