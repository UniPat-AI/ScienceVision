"""Tests for the per-task viewer-family gate on the MCP routes.

``server.py`` narrows its advertised surface when ``SV_MCP_VIEWER_FAMILY`` is
set, but harbor's ``--mcp-config`` is fixed for the whole run, so the value has
to travel in each task's own image. These tests pin both halves: the viewer ->
registry translation (and the guard that keeps it honest against the vendored
snapshot), and the ``ENV`` line that carries it into the generated task.

Run in the harbor venv:
    .venv-harbor/bin/python -m pytest tests/harbor_adapters/test_viewer_family_gate.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sci3d_agent.mcp.parity import allowlist  # noqa: E402
from scripts.harbor import build_harbor_tasks as gen  # noqa: E402


def _write_asset(dirpath: Path, name: str, content: bytes = b"\x00fake") -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / name
    p.write_bytes(content)
    return p


def _task(task_id: str, asset: Path, *, viewer: str | None, extra_viewers: tuple[str, ...] = ()) -> dict:
    """A task whose viewer lives where the corpus actually puts it: on ``asset``
    and, duplicated, on each row of ``assets``."""
    row: dict = {"asset_path": str(asset), "format": "glb"}
    if viewer:
        row["viewer"] = viewer
    task = {
        "task_id": task_id,
        "domain": "architecture",
        "instruction": "q",
        "asset_path": str(asset),
        "asset_format": "glb",
        "answer_schema": {"kind": "freeform"},
        "scorer": {"primary": "llm_judge", "secondary": []},
        "asset": {"asset_path": str(asset), "format": "glb"},
        "assets": [row],
    }
    if viewer:
        task["asset"]["viewer"] = viewer
    task["assets"].extend({"asset_path": str(asset), "format": "glb", "viewer": v} for v in extra_viewers)
    return task


def _envs(task_dir: Path) -> dict[str, str]:
    text = (task_dir / "environment" / "Dockerfile").read_text()
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("ENV ") and "=" in line:
            key, value = line[4:].split("=", 1)
            out[key] = value
    return out


# ── viewer -> registry translation ───────────────────────────────────────── #
def test_live_viewers_map_to_their_registry():
    assert allowlist.live_registry_for_viewer("molstar") == "molstar.v2"
    assert allowlist.live_registry_for_viewer("niivue") == "niivue.v1"
    assert allowlist.live_registry_for_viewer("gltf") == "gltf.v1"
    assert allowlist.live_registry_for_viewer("occt") == "occt.v1"
    assert allowlist.live_registry_for_viewer("bim_coordination") == "bim_coordination.v1"
    assert allowlist.live_registry_for_viewer("architecture_inspection") == "architecture_inspection.v1"


def test_molstar_maps_to_the_live_registry_not_the_fixture():
    """Both molstar registries declare ``viewer: "molstar"``; only v2 is served.

    A regression here would gate a molstar task to a surface no expert was ever
    shown, which reads as a model that declined to use its tools.
    """
    assert allowlist.live_registry_for_viewer("molstar") != "molstar.v1"
    assert "molstar.v1" in allowlist.families()


def test_viewer_without_a_registry_is_not_gated():
    for viewer in ("urdf", "pointcloud", "genesis_view", "bandplot", "citymap"):
        assert allowlist.live_registry_for_viewer(viewer) is None


# ── the guard that keeps the map honest against the snapshot ─────────────── #
def _with_map(monkeypatch, mapping):
    monkeypatch.setattr(allowlist, "LIVE_REGISTRY_BY_VIEWER", mapping)
    allowlist._check_live_map.cache_clear()


def test_map_covering_every_declared_viewer_passes():
    allowlist._check_live_map.cache_clear()
    allowlist._check_live_map()  # does not raise


def test_map_to_an_undeclared_registry_is_rejected(monkeypatch):
    _with_map(monkeypatch, {**allowlist.LIVE_REGISTRY_BY_VIEWER, "molstar": "molstar.v9"})
    with pytest.raises(allowlist.UnknownFamilyError, match="molstar.v9"):
        allowlist._check_live_map()


def test_map_to_a_registry_that_does_not_serve_that_viewer_is_rejected(monkeypatch):
    _with_map(monkeypatch, {**allowlist.LIVE_REGISTRY_BY_VIEWER, "niivue": "gltf.v1"})
    with pytest.raises(ValueError, match="does not serve that viewer"):
        allowlist._check_live_map()


def test_map_missing_a_declared_viewer_is_rejected(monkeypatch):
    """A snapshot refresh that adds a family must stop the build, not silently
    leave that family ungated."""
    incomplete = {k: v for k, v in allowlist.LIVE_REGISTRY_BY_VIEWER.items() if k != "gltf"}
    _with_map(monkeypatch, incomplete)
    with pytest.raises(ValueError, match="does not cover"):
        allowlist._check_live_map()


# ── the ENV line that carries it into the task ───────────────────────────── #
@pytest.mark.parametrize(
    ("viewer", "expected"),
    [("molstar", "molstar.v2"), ("niivue", "niivue.v1"), ("gltf", "gltf.v1")],
)
def test_mcp_route_gates_to_the_tasks_viewer(tmp_path, viewer, expected):
    asset = _write_asset(tmp_path / "src", "a.glb")
    for agent_env in ("claude-code-mcp", "codex-mcp"):
        task_dir = gen.build_one(
            _task(f"sv-{agent_env}", asset, viewer=viewer),
            tmp_path / agent_env,
            containerized=True, agent_env=agent_env,
        )
        assert _envs(task_dir)["SV_MCP_VIEWER_FAMILY"] == expected


def test_ungated_viewer_is_recorded_not_silent(tmp_path, capsys):
    asset = _write_asset(tmp_path / "src", "a.glb")
    task_dir = gen.build_one(
        _task("sv-urdf", asset, viewer="urdf"),
        tmp_path / "out", containerized=True, agent_env="claude-code-mcp",
    )
    assert "SV_MCP_VIEWER_FAMILY" not in _envs(task_dir)
    assert "has no registry for" in capsys.readouterr().err


def test_task_without_a_viewer_runs_ungated_and_warns(tmp_path, capsys):
    asset = _write_asset(tmp_path / "src", "a.glb")
    task_dir = gen.build_one(
        _task("sv-none", asset, viewer=None),
        tmp_path / "out", containerized=True, agent_env="claude-code-mcp",
    )
    assert "SV_MCP_VIEWER_FAMILY" not in _envs(task_dir)
    assert "declares no viewer" in capsys.readouterr().err


def test_two_viewers_is_ambiguous_and_runs_ungated(tmp_path, capsys):
    """Not reachable in today's corpora (all 20k tasks declare 0 or 1 viewer),
    but picking one of two would be a silent wrong answer if it became reachable."""
    asset = _write_asset(tmp_path / "src", "a.glb")
    task = _task("sv-two", asset, viewer="gltf", extra_viewers=("niivue",))
    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env="claude-code-mcp",
    )
    assert "SV_MCP_VIEWER_FAMILY" not in _envs(task_dir)
    assert "declares 2 viewers" in capsys.readouterr().err


def test_non_mcp_routes_are_not_gated(tmp_path):
    """The gate is a property of the MCP routes. A route with no MCP server has
    nothing to narrow, and an ENV line there would be a lie in the Dockerfile."""
    asset = _write_asset(tmp_path / "src", "a.glb")
    task_dir = gen.build_one(
        _task("sv-cc", asset, viewer="molstar"),
        tmp_path / "out", containerized=True, agent_env="claude-code",
    )
    assert "SV_MCP_VIEWER_FAMILY" not in _envs(task_dir)


# ── where the viewer is read from ────────────────────────────────────────── #
def test_viewer_is_read_from_both_the_primary_asset_and_the_asset_rows(tmp_path):
    """``_task_assets`` copies ``role`` off the primary asset but not ``viewer``,
    and the primary is the only carrier for 206 of ~20k corpus tasks."""
    asset = _write_asset(tmp_path / "src", "a.glb")
    from_primary = {"asset": {"viewer": "molstar"}, "assets": []}
    from_rows = {"asset": {}, "assets": [{"viewer": "niivue"}]}
    assert gen._task_viewers(from_primary) == {"molstar"}
    assert gen._task_viewers(from_rows) == {"niivue"}
    assert gen._task_viewers(_task("sv-both", asset, viewer="gltf")) == {"gltf"}
