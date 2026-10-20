from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.harbor import build_harbor_tasks as gen
from scripts.harbor._codex_runtime import (
    MCP_CONFIG,
    CodexRuntimeError,
    harbor_agent_args,
    record_codex_runtime,
    resolve_codex_runtime,
)
from scripts.harbor._mcp_route_runtime import (
    NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT,
    ROUTE_CONTRACT_CODEX_AGENT,
)
from scripts.harbor.route_contracts import build_route_contract


_ROOT = Path(__file__).resolve().parents[2]


def _write_valid_auth(path: Path, *, oauth: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        {"auth_mode": "chatgpt", "tokens": {"access_token": "test-token"}}
        if oauth
        else {"OPENAI_API_KEY": "test-key"}
    )
    path.write_text(json.dumps(payload))


def test_runtime_requires_explicit_model(tmp_path):
    with pytest.raises(CodexRuntimeError, match="CODEX_MODEL is required"):
        resolve_codex_runtime({"OPENAI_API_KEY": "secret"}, home=tmp_path)


def test_runtime_requires_auth(tmp_path):
    with pytest.raises(CodexRuntimeError, match="Codex auth is required"):
        resolve_codex_runtime({"CODEX_MODEL": "model-x"}, home=tmp_path)


def test_api_key_runtime_record_contains_no_secret(tmp_path):
    runtime = resolve_codex_runtime(
        {
            "CODEX_MODEL": "model-x",
            "OPENAI_API_KEY": "top-secret",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
        },
        home=tmp_path,
    )
    record = record_codex_runtime(tmp_path / "jobs", runtime)
    assert runtime.agent_env == ()
    text = record.read_text()
    assert "top-secret" not in text
    assert "example.invalid" not in text
    assert json.loads(text) == {
        "agent": "codex",
        "auth": "openai_api_key",
        "mcp_baseline": "ccmcp",
        "model": "model-x",
        "openai_base_url": "custom",
    }


def test_default_and_explicit_auth_json_are_supported(tmp_path):
    default = tmp_path / ".codex" / "auth.json"
    _write_valid_auth(default)
    runtime = resolve_codex_runtime({"CODEX_MODEL": "model-x"}, home=tmp_path)
    assert runtime.auth_mode == "auth_json"
    assert runtime.agent_env == ("CODEX_FORCE_AUTH_JSON=1",)

    explicit = tmp_path / "other-auth.json"
    _write_valid_auth(explicit, oauth=False)
    runtime = resolve_codex_runtime(
        {"CODEX_MODEL": "model-x", "CODEX_AUTH_JSON_PATH": str(explicit)},
        home=tmp_path,
    )
    assert runtime.auth_mode == "auth_json"
    assert runtime.agent_env == (f"CODEX_AUTH_JSON_PATH={explicit}",)


def test_explicit_missing_auth_json_fails_closed_even_if_default_exists(tmp_path):
    default = tmp_path / ".codex" / "auth.json"
    _write_valid_auth(default)
    with pytest.raises(CodexRuntimeError, match="CODEX_AUTH_JSON_PATH"):
        resolve_codex_runtime(
            {"CODEX_MODEL": "model-x", "CODEX_AUTH_JSON_PATH": str(tmp_path / "missing")},
            home=tmp_path,
        )


def test_whitespace_api_key_falls_back_to_valid_auth_json(tmp_path):
    default = tmp_path / ".codex" / "auth.json"
    _write_valid_auth(default)
    runtime = resolve_codex_runtime(
        {"CODEX_MODEL": "model-x", "OPENAI_API_KEY": " \t\n"},
        home=tmp_path,
    )
    assert runtime.auth_mode == "auth_json"
    assert runtime.agent_env == ("CODEX_FORCE_AUTH_JSON=1",)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "not json",
        "{}",
        json.dumps([]),
        json.dumps({"auth_mode": "chatgpt"}),
        json.dumps({"tokens": {"access_token": "  "}}),
    ],
)
def test_auth_json_without_recognized_credentials_fails_closed(tmp_path, payload):
    auth = tmp_path / "auth.json"
    auth.write_text(payload)
    with pytest.raises(CodexRuntimeError, match="auth JSON"):
        resolve_codex_runtime(
            {"CODEX_MODEL": "model-x", "CODEX_AUTH_JSON_PATH": str(auth)},
            home=tmp_path,
        )


def test_invalid_default_auth_json_fails_closed(tmp_path):
    default = tmp_path / ".codex" / "auth.json"
    default.parent.mkdir()
    default.write_text("{}")
    with pytest.raises(CodexRuntimeError, match="auth JSON"):
        resolve_codex_runtime({"CODEX_MODEL": "model-x"}, home=tmp_path)


def test_mcp_is_added_only_to_ccmcp(tmp_path):
    runtime = resolve_codex_runtime(
        {"CODEX_MODEL": "model-x", "OPENAI_API_KEY": "secret"}, home=tmp_path,
    )
    for baseline in ("cc", "ccmcp", "cc2d-1", "cc2d-3", "cc2d-6", "cc2d-12"):
        args = harbor_agent_args(runtime, baseline)
        expected_agent = ROUTE_CONTRACT_CODEX_AGENT if baseline in {"cc", "ccmcp"} else "codex"
        assert args[:4] == ["--agent", expected_agent, "--model", "model-x"]
        assert all("secret" not in arg for arg in args)
        assert all(not arg.startswith("OPENAI_API_KEY=") for arg in args)
        assert ("--mcp-config" in args) is (baseline == "ccmcp")
        if baseline == "ccmcp":
            assert args[args.index("--mcp-config") + 1] == str(MCP_CONFIG)


def test_mcp_reaches_the_tools_only_3d_mcp_baseline(tmp_path):
    runtime = resolve_codex_runtime(
        {"CODEX_MODEL": "model-x", "OPENAI_API_KEY": "secret"}, home=tmp_path,
    )
    args = harbor_agent_args(runtime, "3d-mcp")
    assert args[:4] == [
        "--agent", NO_SHELL_ROUTE_CONTRACT_CODEX_AGENT, "--model", "model-x",
    ]
    assert args[args.index("--mcp-config") + 1] == str(MCP_CONFIG)

    # The tools-only arm rides the ccmcp route contract; the cc contract conflicts.
    assert "--mcp-config" in harbor_agent_args(
        runtime, "3d-mcp", route_contract=build_route_contract("ccmcp")
    )
    with pytest.raises(CodexRuntimeError, match="conflicts"):
        harbor_agent_args(runtime, "3d-mcp", route_contract=build_route_contract("cc"))


def test_route_contract_cannot_turn_cc_into_a_hidden_mcp_route(tmp_path):
    runtime = resolve_codex_runtime(
        {"CODEX_MODEL": "model-x", "OPENAI_API_KEY": "secret"}, home=tmp_path,
    )
    cc = build_route_contract("cc")
    ccmcp = build_route_contract("ccmcp")
    assert "--mcp-config" not in harbor_agent_args(runtime, "cc", route_contract=cc)
    assert "--mcp-config" in harbor_agent_args(runtime, "ccmcp", route_contract=ccmcp)
    with pytest.raises(CodexRuntimeError, match="conflicts"):
        harbor_agent_args(runtime, "cc", route_contract=ccmcp)


def test_shell_runner_builds_six_codex_commands_and_records_model(tmp_path):
    script = r'''
source scripts/harbor/codex_runtime.sh
codex_runtime_preflight "$1" >/dev/null
for baseline in cc ccmcp cc2d-1 cc2d-3 cc2d-6 cc2d-12; do
  codex_args_for_baseline "$baseline"
  agent="" model="" mcp=0
  for ((i=0; i<${#CODEX_BASELINE_AGENT_ARGS[@]}; i++)); do
    case "${CODEX_BASELINE_AGENT_ARGS[$i]}" in
      --agent) agent="${CODEX_BASELINE_AGENT_ARGS[$((i + 1))]}" ;;
      --model) model="${CODEX_BASELINE_AGENT_ARGS[$((i + 1))]}" ;;
      --mcp-config) mcp=1 ;;
    esac
  done
  printf '%s agent=%s model=%s mcp=%s\n' "$baseline" "$agent" "$model" "$mcp"
done
'''
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CODEX_MODEL": "  model-x\t",
        "OPENAI_API_KEY": "shell-secret",
    }
    result = subprocess.run(
        ["bash", "-c", script, "bash", str(tmp_path / "jobs")],
        cwd=_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 6
    for line in lines:
        baseline = line.split()[0]
        expected_agent = (
            ROUTE_CONTRACT_CODEX_AGENT if baseline in {"cc", "ccmcp"} else "codex"
        )
        assert f"agent={expected_agent} model=model-x" in line
    assert [line.endswith("mcp=1") for line in lines] == [False, True, False, False, False, False]
    record = (tmp_path / "jobs" / "codex-runtime.json").read_text()
    assert "model-x" in record
    assert "shell-secret" not in record


def test_shell_runner_whitespace_api_key_uses_valid_default_auth(tmp_path):
    _write_valid_auth(tmp_path / ".codex" / "auth.json")
    script = r'''
source scripts/harbor/codex_runtime.sh
codex_runtime_preflight "$1" >/dev/null
printf '%s\n' "${CODEX_COMMON_AGENT_ARGS[@]}"
'''
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CODEX_MODEL": "model-x",
        "OPENAI_API_KEY": " \t ",
    }
    result = subprocess.run(
        ["bash", "-c", script, "bash", str(tmp_path / "jobs")],
        cwd=_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "CODEX_FORCE_AUTH_JSON=1" in result.stdout.splitlines()
    assert json.loads((tmp_path / "jobs" / "codex-runtime.json").read_text())["auth"] == (
        "auth_json"
    )


def test_shell_harbor_resolution_honors_validated_override_and_python_fallback(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    harbor = bin_dir / "harbor"
    harbor.write_text("#!/bin/sh\nexit 0\n")
    harbor.chmod(0o755)
    command = (
        'source scripts/harbor/codex_runtime.sh; '
        'codex_resolve_harbor || exit; printf "%s\\n" "$CODEX_HARBOR_BIN"'
    )

    override = subprocess.run(
        ["bash", "-c", command],
        cwd=_ROOT,
        env={"PATH": os.environ["PATH"], "HARBOR": f"  {harbor}\t"},
        text=True,
        capture_output=True,
    )
    assert override.returncode == 0, override.stderr
    assert override.stdout.strip() == str(harbor)

    invalid = subprocess.run(
        ["bash", "-c", command],
        cwd=_ROOT,
        env={"PATH": os.environ["PATH"], "HARBOR": "harbor"},
        text=True,
        capture_output=True,
    )
    assert invalid.returncode != 0
    assert "absolute executable" in invalid.stderr

    fallback = subprocess.run(
        ["bash", "-c", command],
        cwd=_ROOT,
        env={"PATH": "/usr/bin:/bin", "HARBOR_PYTHON": str(bin_dir / "python")},
        text=True,
        capture_output=True,
    )
    assert fallback.returncode == 0, fallback.stderr
    assert fallback.stdout.strip() == str(harbor)


def test_runtime_shell_helper_verifies_recorded_model(tmp_path: Path):
    record_dir = tmp_path / "jobs"
    record_dir.mkdir()
    (record_dir / "codex-runtime.json").write_text(
        json.dumps({"agent": "codex", "model": "model-x"})
    )
    command = (
        'source scripts/harbor/codex_runtime.sh; '
        'codex_verify_runtime_record "$1" "$2" "$3"'
    )
    matched = subprocess.run(
        ["bash", "-c", command, "bash", str(record_dir), "  model-x\t", sys.executable],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert matched.returncode == 0, matched.stderr

    mismatched = subprocess.run(
        ["bash", "-c", command, "bash", str(record_dir), "model-y", sys.executable],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert mismatched.returncode != 0
    assert "does not match recorded model" in mismatched.stderr


def test_runtime_shell_helper_requires_a_valid_codex_record(tmp_path: Path):
    command = (
        'source scripts/harbor/codex_runtime.sh; '
        'codex_verify_runtime_record "$1" model-x "$2"'
    )
    missing = subprocess.run(
        ["bash", "-c", command, "bash", str(tmp_path / "jobs"), sys.executable],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert missing.returncode != 0
    assert "runtime record is missing or empty" in missing.stderr

    record_dir = tmp_path / "jobs"
    record_dir.mkdir()
    (record_dir / "codex-runtime.json").write_text("not json")
    malformed = subprocess.run(
        ["bash", "-c", command, "bash", str(record_dir), sys.executable],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert malformed.returncode != 0
    assert "invalid Codex runtime record" in malformed.stderr


def test_shell_runner_rejects_stale_claude_task_tree(tmp_path):
    dockerfile = tmp_path / "tasks-cc" / "task-1" / "environment" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM sci3d-codex:latest\n")
    command = (
        "source scripts/harbor/codex_runtime.sh; "
        "codex_validate_task_tree cc \"$1\""
    )
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(tmp_path / "tasks-cc")],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr

    dockerfile.write_text("FROM sci3d-claude-code:latest\n")
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(tmp_path / "tasks-cc")],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "stale non-Codex task image" in result.stderr

    dockerfile.write_text("FROM sci3d-codex:latest\n")
    result = subprocess.run(
        [
            "bash", "-c",
            command.replace('cc "$1"', 'cc "$1" 2'),
            "bash", str(tmp_path / "tasks-cc"),
        ],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "expected 2 staged tasks" in result.stderr


def test_ev2_runners_require_all_generated_trees_before_runtime_preflight():
    for name in ("run_ev2_full.sh", "rerun_ev2_errored.sh"):
        text = (_ROOT / "scripts" / "harbor" / name).read_text()
        preflight = text.index("codex_runtime_preflight")
        for call in (
            'require_ev2_tree cc "$EV2_CC_TASKS"',
            'require_ev2_tree cc2d "$EV2_CC2D_TASKS"',
            'require_ev2_tree ccmcp "$EV2_CCMCP_TASKS"',
        ):
            assert text.index(call) < preflight
        assert "codex_validate_task_tree \"$1\" \"$2\" 15" in text
        assert "CODEX_H20_RUNBOOK.md#ev2-three-way-task-trees" in text


def test_codex_images_preserve_the_three_isolation_contracts():
    plain = (gen._TEMPLATES_DIR / "Dockerfile.codex").read_text()
    mcp = (gen._TEMPLATES_DIR / "Dockerfile.codex-mcp").read_text()
    image_2d = (gen._TEMPLATES_DIR / "Dockerfile.codex-2d").read_text()
    for dockerfile in (plain, image_2d):
        assert "@openai/codex" in dockerfile
        assert "codex --version" in dockerfile
        assert "@anthropic-ai" not in dockerfile

    # CC-MCP is an agent-only wrapper over the prebuilt Codex image.  The
    # trusted worker, not this model-facing image, owns the MCP executable and
    # evaluator source.
    assert "ARG BASE_IMAGE=sci3d-codex:latest" in mcp
    assert "io.sciencevision.image.role=\"agent-only\"" in mcp
    assert "SV_MCP_HTTP_SURFACE=http://127.0.0.1:8765/mcp" in mcp
    assert "npm install -g @openai/codex" not in mcp
    assert "sciencevision-mcp --list-tools" not in mcp
    assert "COPY sci3d_agent/" not in mcp
    assert "COPY harbor_adapters/" not in mcp
    assert "COPY sci3d_bench/" not in mcp

    for library in ("nibabel", "trimesh", "numpy"):
        assert library in plain
        assert library not in image_2d
    assert "pip3 install" not in mcp
    assert "sciencevision-mcp" not in plain
    assert "sciencevision-mcp" not in image_2d
    assert "pip3 install" not in image_2d


def test_current_agent_envs_are_codex_and_keep_visibility_boundaries():
    assert set(("codex", "codex-mcp", "codex-2d")) <= gen.AGENT_ENVS.keys()
    assert gen.AGENT_ENVS["codex"].stager is gen._stage_assets
    assert gen.AGENT_ENVS["codex-mcp"].stager is gen._stage_assets
    assert gen.AGENT_ENVS["codex-2d"].stager is gen._stage_rendered_views
    assert "COPY asset/ /data/" in gen.AGENT_ENVS["codex"].dockerfile
    assert "COPY asset/ /data/" not in gen.AGENT_ENVS["codex-mcp"].dockerfile
    assert "SV_MCP_HTTP_SURFACE=http://127.0.0.1:8765/mcp" in gen.AGENT_ENVS["codex-mcp"].dockerfile
    worker = gen.MCP_WORKER_PACKAGING.render_dockerfile(image="sci3d-mcp-worker:dev")
    assert "COPY asset/ /data/" in worker
    assert "COPY views/ /data/" in gen.AGENT_ENVS["codex-2d"].dockerfile
    assert "COPY asset/" not in gen.AGENT_ENVS["codex-2d"].dockerfile


def test_active_runners_have_no_claude_agent_model_or_auth_dependencies():
    runners = (
        "run_sample50_staged.sh",
        "run_scale_v1_staged.sh",
        "run_end2end.py",
        "run_ev2_full.sh",
        "rerun_ev2_errored.sh",
        "postrun_ev2.sh",
        "launch_scale_v1.sh",
    )
    forbidden = ("--agent claude-code", "claude-opus", "ANTHROPIC_", "@anthropic-ai")
    for name in runners:
        text = (_ROOT / "scripts" / "harbor" / name).read_text()
        for token in forbidden:
            assert token not in text, f"{token!r} remains in {name}"


def test_harbor_print_config_accepts_codex_http_mcp():
    harbor = Path(sys.executable).with_name("harbor")
    harbor_bin = shutil.which("harbor") or (str(harbor) if harbor.exists() else None)
    if harbor_bin is None:
        pytest.skip("Harbor CLI is not installed")
    result = subprocess.run(
        [
            harbor_bin,
            "run",
            "--path",
            "scripts/harbor/tasks/sv-robotics-v6-0001",
            "--agent",
            "codex",
            "--model",
            "model-x",
            "--mcp-config",
            str(MCP_CONFIG),
            "--print-config",
        ],
        cwd=_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    agent = config["agents"][0]
    assert agent["name"] == "codex"
    assert agent["model_name"] == "model-x"
    assert agent["mcp_servers"] == [
        {
            "name": "sciencevision",
            "transport": "streamable-http",
            "url": "http://127.0.0.1:8765/mcp",
        }
    ]
