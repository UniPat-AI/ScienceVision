"""Tests for the end2end baseline-matrix driver (resolve + orchestration)."""
import base64
import io
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from scripts.harbor._e2e_resolve import resolve_task_list
from scripts.harbor import run_end2end as E
from scripts.harbor._mcp_route_runtime import ROUTE_CONTRACT_CODEX_AGENT

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_require_exact_task_tree_accepts_receipt_exclusions(tmp_path):
    tree = tmp_path / "tasks"
    (tree / "a").mkdir(parents=True)
    # "b" is excluded with a receipt; the gate accepts the gap it explains...
    E._require_exact_task_tree(tree, {"a", "b"}, excluded_ids={"b"})
    # ...but not the same gap with no receipt,
    with pytest.raises(RuntimeError, match="missing"):
        E._require_exact_task_tree(tree, {"a", "b"})
    # ...and not a receipt that contradicts a built tree.
    (tree / "b").mkdir()
    with pytest.raises(RuntimeError, match="excluded task has a built tree"):
        E._require_exact_task_tree(tree, {"a", "b"}, excluded_ids={"b"})

def _test_png_b64():
    from PIL import Image

    image = Image.new("RGB", (16, 16), "black")
    image.putpixel((8, 8), (255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_PNG_B64 = _test_png_b64()


@pytest.fixture(autouse=True)
def _explicit_codex_runtime(monkeypatch):
    monkeypatch.setenv("CODEX_MODEL", "model-x")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("CODEX_AUTH_JSON_PATH", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def _cam_link(cam):
    raw = json.dumps(cam, separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"https://x/viewer/gltf.html?file=assets/m.glb&cam={b}"


# ── resolve_task_list ───────────────────────────────────────────────────── #

def test_resolve_maps_row_to_qagen_record(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    rows = [{"task_id": "t1", "viewer_link": _cam_link({"position": [2, 0, 1], "target": [0, 0, 0]}),
             "question": "how many parts?", "ground_truth": {"value": "3"}}]
    recs = resolve_task_list(rows, asset_root=tmp_path, seed_from_link=True)
    assert recs[0]["asset"]["repo_path"] == "assets/m.glb"
    assert recs[0]["asset"]["format"] == "glb"
    assert recs[0]["instruction"] == "how many parts?"
    assert "seed_pose" in recs[0]  # gltf + cam + seed_from_link


def test_resolve_no_seed_when_canonical(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    rows = [{"task_id": "t1", "viewer_link": _cam_link({"position": [2, 0, 1], "target": [0, 0, 0]}),
             "question": "q", "ground_truth": {"value": "3"}}]
    recs = resolve_task_list(rows, asset_root=tmp_path, seed_from_link=False)
    assert "seed_pose" not in recs[0]


def test_resolve_skips_malformed_row_without_aborting_batch(tmp_path, capsys):
    """One bad row (missing viewer_link, or missing task_id) must not raise
    an unhandled KeyError that kills the whole batch -- it should be skipped
    with a named stderr warning, and the remaining valid rows still resolve."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    cam = _cam_link({"position": [1, 0, 0], "target": [0, 0, 0]})
    good = {"task_id": "good1", "viewer_link": cam, "question": "q", "ground_truth": {"value": "1"}}
    missing_link = {"task_id": "bad_missing_link", "question": "q2"}
    missing_id = {"viewer_link": cam, "question": "q3"}
    rows = [good, missing_link, missing_id]

    recs = resolve_task_list(rows, asset_root=tmp_path, seed_from_link=False)

    assert [r["task_id"] for r in recs] == ["good1"]
    err = capsys.readouterr().err
    assert "bad_missing_link" in err  # named by task_id when available
    assert "<row #2>" in err  # falls back to 0-based index when task_id is also missing


# ── BASELINES / build_run_specs ─────────────────────────────────────────── #

def test_six_baselines_and_run_specs():
    assert [b[0] for b in E.BASELINES] == ["cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12"]
    assert [b[1] for b in E.BASELINES] == [
        "codex", "codex-mcp", "codex-2d", "codex-2d", "codex-2d", "codex-2d",
    ]
    specs = E.build_run_specs({b[0]: f"/jobs/{b[0]}" for b in E.BASELINES}, "model-x")
    assert "cc2d-6:model-x:/jobs/cc2d-6" in specs


def test_harbor_override_requires_an_absolute_executable(tmp_path, monkeypatch):
    harbor = tmp_path / "harbor"
    harbor.write_text("#!/bin/sh\nexit 0\n")
    harbor.chmod(0o755)
    monkeypatch.setenv("HARBOR", str(harbor))
    assert E._resolve_harbor_bin() == str(harbor)

    harbor.chmod(0o644)
    with pytest.raises(RuntimeError, match="absolute executable"):
        E._resolve_harbor_bin()

    monkeypatch.setenv("HARBOR", "harbor")
    with pytest.raises(RuntimeError, match="absolute executable"):
        E._resolve_harbor_bin()


def test_harbor_resolution_falls_back_to_path_then_interpreter_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("HARBOR", raising=False)
    monkeypatch.setattr(E.shutil, "which", lambda name: "/opt/harbor/bin/harbor")
    assert E._resolve_harbor_bin() == "/opt/harbor/bin/harbor"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    harbor = bin_dir / "harbor"
    harbor.write_text("#!/bin/sh\nexit 0\n")
    harbor.chmod(0o755)
    monkeypatch.setattr(E.shutil, "which", lambda name: None)
    monkeypatch.setattr(E.sys, "executable", str(bin_dir / "python"))
    assert E._resolve_harbor_bin() == str(harbor)


# ── collect/plot argv (locked against the real CLIs) ────────────────────── #

def test_build_collect_and_plot_argv_exact_flags():
    run_specs = ["cc:model-x:/jobs/job-cc", "ccmcp:model-x:/jobs/job-ccmcp"]
    out_root = Path("/tmp/qa-gen-tasks")
    assert E._build_collect_argv(run_specs, out_root) == [
        "--tasks-root", str(out_root),
        "--run", "cc:model-x:/jobs/job-cc",
        "--run", "ccmcp:model-x:/jobs/job-ccmcp",
    ]
    assert E._build_plot_argv(out_root) == [
        "--leaderboard", str(out_root / "runs_leaderboard.json"),
        "--out-dir", str(out_root / "charts"),
    ]


def test_collect_argv_accepted_by_real_cli(tmp_path):
    """Actually invoke the real collect_qagen_results CLI with the argv the
    driver builds (against a nonexistent jobdir -- no docker/harbor
    involved) so a future flag rename fails this test instead of silently
    no-op'ing after 6 expensive harbor jobs."""
    out_root = tmp_path / "qa"
    out_root.mkdir()
    run_specs = [f"cc:model-x:{tmp_path / 'nonexistent-job'}"]
    collect_argv = E._build_collect_argv(run_specs, out_root)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.harbor.collect_qagen_results"] + collect_argv,
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (out_root / "runs_leaderboard.json").exists()


def test_plot_argv_accepted_by_real_cli(tmp_path):
    """Actually invoke the real plot_leaderboard CLI with the argv the
    driver builds, against a minimal-but-valid (non-empty domain) synthetic
    leaderboard so it exercises the real chart-building path."""
    out_root = tmp_path / "qa"
    out_root.mkdir()
    board = {
        "n_runs": 1, "n_completed": 1,
        "by_harness_model_mean_reward": {"cc / model-x": 0.5},
        "by_harness_domain_mean_reward": {"cc / general": 0.5},
        "rows": [{
                "task_id": "t1", "set": "smoke", "domain": "general", "v_class": "V1",
                "harness": "cc", "model": "model-x", "status": "ok",
                "evaluation_eligible": True,
                # The chart is drawn from citable rows only, so a row without the
                # verdict draws nothing; this test is about the CLI's argv.
                "citable": True,
                "reward": 0.5, "answer_score": 0.5, "process_compliance": 1.0,
            }],
    }
    (out_root / "runs_leaderboard.json").write_text(json.dumps(board))

    plot_argv = E._build_plot_argv(out_root)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.harbor.plot_leaderboard"] + plot_argv,
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (out_root / "charts" / "chart_data.json").exists()
    assert (out_root / "charts" / "leaderboard_reward_by_domain.png").exists()


def test_plot_leaderboard_zero_domains_skips_charts_cleanly(tmp_path):
    """An all-fail end2end run collects zero 'ok' rows -> zero plottable
    domains. plot_leaderboard must degrade cleanly (exit 0, chart_data.json
    with empty lists, no PNGs) instead of matplotlib's
    'Number of columns must be a positive integer, not 0' crash -- the
    one-command driver must survive an unattended all-fail run through to
    its last step."""
    out_root = tmp_path / "qa"
    out_root.mkdir()
    board = {
        "n_runs": 1, "n_completed": 0,
        "by_harness_model_mean_reward": {}, "by_harness_domain_mean_reward": {},
        "rows": [{
            "task_id": "t1", "set": "smoke", "domain": "general", "v_class": "V1",
            "harness": "cc", "model": "model-x", "status": "error",
            "reward": None, "exact_match": None, "llm_judge": None, "process_judge": None,
        }],
    }
    (out_root / "runs_leaderboard.json").write_text(json.dumps(board))

    plot_argv = E._build_plot_argv(out_root)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.harbor.plot_leaderboard"] + plot_argv,
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "skipping chart generation" in result.stderr
    assert (out_root / "charts" / "chart_data.json").exists()
    assert not (out_root / "charts" / "leaderboard_reward_by_domain.png").exists()
    assert not (out_root / "charts" / "leaderboard_components_by_domain.png").exists()


# ── concurrency / failure signaling ─────────────────────────────────────── #

def test_run_all_fanout_continues_after_one_failure():
    """One baseline's runner raising must not silently drop the other 5:
    every baseline's runner is still invoked (fan-out waits for
    ALL_COMPLETED before inspecting results), and the failure is surfaced
    (re-raised) rather than swallowed. Also locks the exact `harbor run`
    argv shape/flag-names for one baseline -- a flag typo here (e.g.
    --verifier-import-path, --agent, --model, --env, -o, --ae, --path) would
    otherwise only fail against a real harbor binary."""
    calls: list[list[str]] = []
    lock = threading.Lock()
    failing_jobdir = "/jobs/job-cc2d-6"

    def fake_runner(cmd):
        with lock:
            calls.append(cmd)
        if failing_jobdir in cmd:
            raise RuntimeError("boom")

    tasks_dirs = {label: Path(f"/tmp/tasks-{label}") for label, _, _ in E.BASELINES}
    jobdirs = {label: f"/jobs/job-{label}" for label, _, _ in E.BASELINES}
    ae = ["CODEX_FORCE_AUTH_JSON=1"]

    with pytest.raises(RuntimeError, match="boom"):
        E._run_all(tasks_dirs, jobdirs, "model-x", ae, max_workers=2, runner=fake_runner)

    assert len(calls) == 6  # every baseline's runner ran, despite one raising

    cc_cmd = next(c for c in calls if "-o" in c and c[c.index("-o") + 1] == jobdirs["cc"])
    assert cc_cmd == [
        E._HARBOR_BIN, "run",
        "--path", str(tasks_dirs["cc"]),
        "--agent", ROUTE_CONTRACT_CODEX_AGENT,
        "--model", "model-x",
        "--verifier-import-path", E._VERIFIER,
        "--env", "docker",
        "-o", jobdirs["cc"],
        "--ae", "CODEX_FORCE_AUTH_JSON=1",
    ]


def test_all_six_commands_use_the_route_adapter_only_where_needed():
    calls: list[list[str]] = []
    tasks_dirs = {label: Path(f"/tmp/tasks-{label}") for label, _, _ in E.BASELINES}
    jobdirs = {label: f"/jobs/job-{label}" for label, _, _ in E.BASELINES}

    E._run_all(
        tasks_dirs, jobdirs, "model-x", ["CODEX_FORCE_AUTH_JSON=1"],
        max_workers=1, runner=calls.append,
    )

    assert len(calls) == 6
    for cmd in calls:
        label = Path(cmd[cmd.index("-o") + 1]).name.removeprefix("job-")
        expected_agent = ROUTE_CONTRACT_CODEX_AGENT if label in {"cc", "ccmcp"} else "codex"
        assert cmd[cmd.index("--agent") + 1] == expected_agent
        assert cmd[cmd.index("--model") + 1] == "model-x"
        assert ("--mcp-config" in cmd) is (label == "ccmcp")
        if label == "ccmcp":
            assert cmd[cmd.index("--mcp-config") + 1] == str(E.MCP_CONFIG)


def test_run_all_injects_n_concurrent_before_ae():
    """--n-concurrent threads harbor's `-n <N>` (per-baseline trial
    concurrency) into every harbor-run argv, placed after -o and before the
    --ae pairs. Omitting the flag (None) must emit no -n at all so the
    default harbor behaviour is unchanged."""
    calls: list[list[str]] = []
    lock = threading.Lock()

    def fake_runner(cmd):
        with lock:
            calls.append(cmd)

    tasks_dirs = {label: Path(f"/tmp/tasks-{label}") for label, _, _ in E.BASELINES}
    jobdirs = {label: f"/jobs/job-{label}" for label, _, _ in E.BASELINES}
    ae = ["CODEX_FORCE_AUTH_JSON=1"]

    E._run_all(tasks_dirs, jobdirs, "model-x", ae, max_workers=1, runner=fake_runner, n_concurrent=24)

    assert len(calls) == 6
    for cmd in calls:
        assert cmd[-4:] == ["-n", "24", "--ae", "CODEX_FORCE_AUTH_JSON=1"]

    # None -> no -n emitted (default harbor behaviour preserved).
    calls.clear()
    E._run_all(tasks_dirs, jobdirs, "model-x", ae, max_workers=1, runner=fake_runner)
    for cmd in calls:
        assert "-n" not in cmd


def test_run_all_harbor_extra_passthrough_placed_before_n_and_ae():
    """--harbor-extra tokens (e.g. timeout multipliers) are appended verbatim,
    after -o and before -n / --ae, so they compose with both."""
    calls: list[list[str]] = []
    lock = threading.Lock()

    def fake_runner(cmd):
        with lock:
            calls.append(cmd)

    tasks_dirs = {label: Path(f"/tmp/tasks-{label}") for label, _, _ in E.BASELINES}
    jobdirs = {label: f"/jobs/job-{label}" for label, _, _ in E.BASELINES}
    ae = ["CODEX_FORCE_AUTH_JSON=1"]
    extra = ("--agent-setup-timeout-multiplier", "3.0", "--agent-timeout-multiplier", "2.0")

    E._run_all(tasks_dirs, jobdirs, "model-x", ae, max_workers=1, runner=fake_runner,
               n_concurrent=24, harbor_extra=extra)

    assert len(calls) == 6
    for cmd in calls:
        o = cmd.index("-o")
        # order after -o jobdir: <extra...> -n 24 --ae <judge>
        assert cmd[o + 2:] == [
            "--agent-setup-timeout-multiplier", "3.0",
            "--agent-timeout-multiplier", "2.0",
            "-n", "24",
            "--ae", "CODEX_FORCE_AUTH_JSON=1",
        ]


@pytest.mark.parametrize(
    "extra",
    [
        ("--agent", "claude-code"),
        ("--agent=claude-code",),
        ("--model", "other-model"),
        ("--mcp-config", "/tmp/injected.json"),
        ("--env", "host"),
        ("-o", "/tmp/other-jobs"),
        ("--jobs-dir=/tmp/other-jobs",),
        ("--ae", "CODEX_FORCE_AUTH_JSON=0"),
        ("-n", "999"),
        ("--config", "/tmp/override.json"),
        ("--agent-timeout-multiplier", "--mcp-config"),
    ],
)
def test_run_all_rejects_harbor_extra_baseline_override_attacks(extra):
    calls: list[list[str]] = []
    tasks_dirs = {label: Path(f"/tmp/tasks-{label}") for label, _, _ in E.BASELINES}
    jobdirs = {label: f"/jobs/job-{label}" for label, _, _ in E.BASELINES}

    with pytest.raises(ValueError):
        E._run_all(
            tasks_dirs, jobdirs, "model-x", [], max_workers=1,
            runner=calls.append, harbor_extra=extra,
        )
    assert calls == []


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "not-a-number"])
def test_harbor_extra_timeout_values_must_be_positive_and_finite(value):
    with pytest.raises(ValueError):
        E._validate_harbor_extra(("--agent-timeout-multiplier", value))


# ── main() --dry-run: build x6 + run specs, no harbor invoked ───────────── #

def test_main_dry_run_builds_six_task_dirs_and_run_specs(tmp_path, monkeypatch, capsys):
    import scripts.harbor.build_harbor_tasks as gen
    monkeypatch.setattr(
        gen, "_lookup_render_handler",
        lambda name: (lambda args: {"image_png_base64": _PNG_B64}),
    )

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    tasks_jsonl = tmp_path / "tasks.jsonl"
    tasks_jsonl.write_text(json.dumps({
        "task_id": "smoke1",
        "viewer_link": "https://x/viewer/gltf.html?file=assets/m.glb",
        "question": "how many parts?",
        "ground_truth": {"value": "1"},
    }) + "\n")

    seams_called: list[str] = []
    monkeypatch.setattr(E, "_run_all", lambda *a, **k: seams_called.append("_run_all"))
    monkeypatch.setattr(E, "_collect", lambda *a, **k: seams_called.append("_collect"))
    monkeypatch.setattr(E, "_plot", lambda *a, **k: seams_called.append("_plot"))

    jobs_root = tmp_path / "jobs"
    out_root = tmp_path / "out"
    E.main([
        "--tasks", str(tasks_jsonl), "--set-name", "Smoke",
        "--asset-root", str(tmp_path),
        "--out-root", str(out_root), "--jobs-root", str(jobs_root),
        "--harbor-extra=--agent-setup-timeout-multiplier", "--harbor-extra=3.0",
        "--dry-run",
    ])

    # --dry-run must never reach the harbor-run/collect/plot seams.
    assert seams_called == []

    out = capsys.readouterr().out
    model = "model-x"
    runtime_record = json.loads((jobs_root / "codex-runtime.json").read_text())
    assert runtime_record == {
        "agent": "codex",
        "auth": "openai_api_key",
        "mcp_baseline": "ccmcp",
        "model": model,
        "openai_base_url": "default",
    }
    assert "test-key" not in (jobs_root / "codex-runtime.json").read_text()
    for label, _, n_views in E.BASELINES:
        tasks_dir = jobs_root / f"tasks-{label}"
        task_dir = tasks_dir / "smoke1"
        assert task_dir.exists(), f"{label}: task dir not built under --dry-run"
        assert f"{label}: {tasks_dir}" in out
        assert f"{label}:{model}:{jobs_root / f'job-{label}'}" in out
        dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
        expected_image = "sci3d-codex-2d" if n_views is not None else (
            "sci3d-codex-mcp" if label == "ccmcp" else "sci3d-codex"
        )
        assert f"FROM {expected_image}:latest" in dockerfile
        if n_views is not None:
            instr = (task_dir / "instruction.md").read_text()
            block = re.search(r"```sci3d\s*\n(.*?)```", instr, re.DOTALL)
            payload = json.loads(block.group(1).strip())
            assert len(payload["views"]) == n_views, f"{label}: expected {n_views} staged views"


def test_main_refuses_reused_jobs_root_when_limit_shrinks(tmp_path, monkeypatch):
    import scripts.harbor.build_harbor_tasks as gen

    monkeypatch.setattr(
        gen, "_lookup_render_handler",
        lambda name: (lambda args: {"image_png_base64": _PNG_B64}),
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.glb").write_bytes(b"x")
    rows = [
        {
            "task_id": task_id,
            "viewer_link": "https://x/viewer/gltf.html?file=assets/m.glb",
            "question": "how many parts?",
            "ground_truth": {"value": "1"},
        }
        for task_id in ("smoke1", "smoke2")
    ]
    tasks_jsonl = tmp_path / "tasks.jsonl"
    tasks_jsonl.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    jobs_root = tmp_path / "jobs"
    common = [
        "--tasks", str(tasks_jsonl), "--set-name", "Smoke",
        "--asset-root", str(tmp_path), "--out-root", str(tmp_path / "out"),
        "--jobs-root", str(jobs_root), "--dry-run",
    ]

    E.main([*common, "--limit", "2"])
    for label, _, _ in E.BASELINES:
        assert {path.name for path in (jobs_root / f"tasks-{label}").iterdir()} == {
            "smoke1", "smoke2",
        }

    with pytest.raises(SystemExit, match="fresh --jobs-root"):
        E.main([*common, "--limit", "1"])
    for label, _, _ in E.BASELINES:
        assert {path.name for path in (jobs_root / f"tasks-{label}").iterdir()} == {
            "smoke1", "smoke2",
        }


def _prewritten_rec(task_id: str, repo_path: str) -> dict:
    """A scale-v1-shaped, already-resolved QA-Gen record (nested asset, no
    viewer_link) -- the shape --pre-resolved must accept directly."""
    return {
        "task_id": task_id,
        "domain": "biology",
        "instruction": "does it clash?",
        "asset": {"format": "mmcif", "path": f"/data/{task_id}.cif", "repo_path": repo_path},
        "answer_schema": {"kind": "binary", "fields": ["has_severe_clash"]},
        "ground_truth": {"value": {"has_severe_clash": "no"}},
        "scorer": {"primary": "exact_match", "secondary": []},
    }


def test_main_pre_resolved_skips_resolve_and_builds(tmp_path, monkeypatch, capsys):
    """--pre-resolved feeds already-resolved QA-Gen records (nested asset, no
    viewer_link) straight into the build x6 pipeline. Without the flag these
    rows would be skipped by resolve_task_list as malformed (no viewer_link),
    building zero task dirs -- this is the scale-v1 entry path."""
    import scripts.harbor.build_harbor_tasks as gen
    monkeypatch.setattr(
        gen, "_lookup_render_handler",
        lambda name: (lambda args: {"image_png_base64": _PNG_B64}),
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.cif").write_bytes(b"x")
    tasks_jsonl = tmp_path / "scale_slice.jsonl"
    tasks_jsonl.write_text(json.dumps(_prewritten_rec("bio1", "assets/m.cif")) + "\n")

    monkeypatch.setattr(E, "_run_all", lambda *a, **k: None)
    jobs_root = tmp_path / "jobs"
    E.main([
        "--tasks", str(tasks_jsonl), "--pre-resolved", "--set-name", "ScaleSlice",
        "--asset-root", str(tmp_path),
        "--out-root", str(tmp_path / "out"), "--jobs-root", str(jobs_root),
        "--dry-run",
    ])
    for label, _, n_views in E.BASELINES:
        task_dir = jobs_root / f"tasks-{label}" / "bio1"
        assert task_dir.exists(), f"{label}: pre-resolved record not built"
        if n_views is not None:
            instr = (task_dir / "instruction.md").read_text()
            payload = json.loads(re.search(r"```sci3d\s*\n(.*?)```", instr, re.DOTALL).group(1))
            assert len(payload["views"]) == n_views


def test_main_multiple_tasks_concatenate_and_limit(tmp_path, monkeypatch, capsys):
    """Repeatable --tasks concatenates several jsonls; --limit truncates the
    combined record list (smoke-slice validation of the full pipeline)."""
    import scripts.harbor.build_harbor_tasks as gen
    monkeypatch.setattr(
        gen, "_lookup_render_handler",
        lambda name: (lambda args: {"image_png_base64": _PNG_B64}),
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "m.cif").write_bytes(b"x")
    f1 = tmp_path / "a.jsonl"
    f2 = tmp_path / "b.jsonl"
    f1.write_text(json.dumps(_prewritten_rec("bio1", "assets/m.cif")) + "\n")
    f2.write_text(
        json.dumps(_prewritten_rec("bio2", "assets/m.cif")) + "\n"
        + json.dumps(_prewritten_rec("bio3", "assets/m.cif")) + "\n"
    )
    monkeypatch.setattr(E, "_run_all", lambda *a, **k: None)
    jobs_root = tmp_path / "jobs"
    E.main([
        "--tasks", str(f1), "--tasks", str(f2), "--pre-resolved",
        "--limit", "2", "--set-name", "ScaleSlice",
        "--asset-root", str(tmp_path),
        "--out-root", str(tmp_path / "out"), "--jobs-root", str(jobs_root),
        "--dry-run",
    ])
    cc = jobs_root / "tasks-cc"
    built = sorted(p.name for p in cc.iterdir())
    assert built == ["bio1", "bio2"], f"limit=2 over [f1(bio1), f2(bio2,bio3)] -> {built}"
