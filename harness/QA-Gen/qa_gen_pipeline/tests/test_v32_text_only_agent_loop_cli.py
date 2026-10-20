from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json

from conftest import PACKAGE_ROOT


def _load_cli_module():
    script = PACKAGE_ROOT / "scripts" / "run_v32_text_only_agent_loop.py"
    spec = importlib.util.spec_from_file_location("qagen_v32_text_only_agent_loop_cli_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_prepared_run_dispatches_to_execute_without_prepare_fallthrough(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Exercise the real CLI parser using an explicit zero-network fake executor."""

    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    run_id = "v32-cli-dispatch-test"
    run_root = paths.private / "text-only-agent-pilots" / run_id
    atomic_write_json(run_root / "execution-plan.json", {"prepared": True}, private=True)
    atomic_write_json(run_root / "runtime" / "provider-runtime.one-slot.yaml", {"runtime": "locked"}, private=True)
    atomic_write_json(
        paths.root / "preproduction" / "text-only-agent-pilots" / run_id / "provider-endpoint-approval.one-slot.yaml",
        {"approval": "locked"},
        private=True,
    )
    atomic_write_json(run_root / "no-network-gate.json", {"status": "passed"}, private=True)
    cli = _load_cli_module()
    observed: dict[str, object] = {}

    def fail_prepare(*_args, **_kwargs):
        raise AssertionError("existing prepared run must not fall through to prepare")

    def fake_execute(*_args, **kwargs):
        observed.update(kwargs)
        return {
            "run_id": kwargs["run_id"],
            "status": "fake_provider_execution_complete",
            "staging_enabled": False,
            "release_eligible": False,
        }

    monkeypatch.setattr(cli, "prepare_text_only_pilot", fail_prepare)
    monkeypatch.setattr(cli, "create_one_slot_provider_runtime", fail_prepare)
    monkeypatch.setattr(cli, "execute_text_only_pilot", fake_execute)
    argument_values = [
        "run_v32_text_only_agent_loop.py",
        "--workspace",
        str(paths.root),
        "--run-id",
        run_id,
        "--execute",
    ]
    for name in (
        "selection-manifest",
        "child-target",
        "preauthor-anchor",
        "pilot-result",
        "inventory",
        "first-receipt",
        "first-sidecar",
        "first-leak-scan",
        "second-receipt",
        "second-sidecar",
        "second-leak-scan",
        "replay-consistency",
    ):
        argument_values.extend([f"--{name}", str(tmp_path / f"{name}.json")])
    monkeypatch.setattr(sys, "argv", argument_values)

    assert cli.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert observed["run_id"] == run_id
    assert output["prepared"]["reused_prepared_run"] is True
    assert output["execution"]["status"] == "fake_provider_execution_complete"
