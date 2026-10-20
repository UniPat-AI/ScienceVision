import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from sci3d_agent.mcp import registry as registry_module
from sci3d_agent.mcp import server as server_module
from sci3d_agent.mcp.registry import REGISTRY, ToolSpec, all_tools, register
from sci3d_agent.mcp.runtime import cache as cache_module
from sci3d_agent.mcp.runtime import paths as paths_module
from sci3d_agent.mcp.runtime.cache import cache_key, get_or_compute
from sci3d_agent.mcp.runtime.errors import SVToolError
from sci3d_agent.mcp.runtime.paths import render_cache_dir, repo_root, viewer_base
from sci3d_agent.mcp.tools import ALL_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolated_registry():
    snapshot = dict(REGISTRY)
    REGISTRY.clear()
    REGISTRY.update(snapshot)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def _noop_handler(args):
    return {"ok": True, "echo": dict(args)}


def _fake_spec(name="sv_fake_tool"):
    return ToolSpec(
        name=name,
        description="fake tool for tests",
        input_schema={"type": "object", "properties": {}},
        handler=_noop_handler,
        category="A",
    )


def test_all_tools_is_list_of_toolspec():
    assert isinstance(ALL_TOOLS, list)
    for spec in ALL_TOOLS:
        assert isinstance(spec, ToolSpec)
        assert spec.name.startswith("sv")


def test_register_adds_to_registry():
    spec = _fake_spec("sv_only_one")
    out = register(spec)
    assert out is spec
    names = [s.name for s in all_tools()]
    assert "sv_only_one" in names


def test_register_rejects_duplicates():
    register(_fake_spec("sv_dup"))
    with pytest.raises(ValueError, match="duplicate tool: sv_dup"):
        register(_fake_spec("sv_dup"))


def test_toolspec_is_frozen():
    spec = _fake_spec("sv_frozen")
    with pytest.raises(Exception):
        spec.name = "mutated"  # type: ignore[misc]


def test_cache_key_changes_on_mtime(tmp_path):
    f = tmp_path / "asset.bin"
    f.write_bytes(b"hello")
    k1 = cache_key(f, "sv_inspect_mesh", {"x": 1})
    new_ns = f.stat().st_mtime_ns + 1_000_000_000
    import os as _os

    _os.utime(f, ns=(new_ns, new_ns))
    k2 = cache_key(f, "sv_inspect_mesh", {"x": 1})
    assert k1 != k2


def test_cache_key_stable_for_same_state(tmp_path):
    f = tmp_path / "asset.bin"
    f.write_bytes(b"hello")
    k1 = cache_key(f, "sv_inspect_mesh", {"x": 1, "y": 2})
    k2 = cache_key(f, "sv_inspect_mesh", {"y": 2, "x": 1})
    assert k1 == k2


def test_cache_key_changes_on_tool_or_args(tmp_path):
    f = tmp_path / "asset.bin"
    f.write_bytes(b"hello")
    base = cache_key(f, "sv_inspect_mesh", {"x": 1})
    diff_tool = cache_key(f, "sv_inspect_nifti", {"x": 1})
    diff_args = cache_key(f, "sv_inspect_mesh", {"x": 2})
    assert base != diff_tool
    assert base != diff_args


def test_get_or_compute_writes_and_reads(tmp_path):
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"value": 42}

    cache_dir = tmp_path / "cache"
    v1 = get_or_compute("k1", compute, cache_dir)
    v2 = get_or_compute("k1", compute, cache_dir)
    assert v1 == {"value": 42}
    assert v2 == {"value": 42}
    assert calls["n"] == 1


def test_get_or_compute_recovers_from_corrupt_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "k2.json").write_text("{ not json")

    def compute():
        return {"value": "recomputed"}

    out = get_or_compute("k2", compute, cache_dir)
    assert out == {"value": "recomputed"}


def test_paths_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("SV_VIEWER_BASE", "http://example.test:8080/")
    monkeypatch.setenv("SV_RENDER_CACHE", str(tmp_path / "renders"))
    assert repo_root() == tmp_path
    assert viewer_base() == "http://example.test:8080"
    assert render_cache_dir() == tmp_path / "renders"


def test_paths_defaults_when_env_missing(monkeypatch):
    monkeypatch.delenv("SV_REPO_ROOT", raising=False)
    monkeypatch.delenv("SV_VIEWER_BASE", raising=False)
    monkeypatch.delenv("SV_RENDER_CACHE", raising=False)
    # repo_root auto-detects from this package's file location, so the
    # default must contain `pyproject.toml` and the `sci3d_agent/` source
    # tree — whichever checkout (main or .claude/worktrees/<branch>/) the
    # code is loaded from.
    detected = repo_root()
    assert (detected / "pyproject.toml").exists()
    assert (detected / "sci3d_agent").is_dir()
    assert viewer_base() == "http://10.0.0.144"
    assert render_cache_dir() == Path("/tmp/sv_renders")


def test_svtoolerror_is_exception():
    err = SVToolError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


def test_safe_handler_catches_svtoolerror():
    def bad(_args):
        raise SVToolError("nope")

    spec = ToolSpec(
        name="sv_bad",
        description="",
        input_schema={"type": "object"},
        handler=bad,
        category="A",
    )
    wrapped = server_module._safe_handler(spec)
    result = wrapped({})
    assert result == {"error": "nope"}


def test_safe_handler_passes_through_success():
    spec = _fake_spec("sv_good")
    wrapped = server_module._safe_handler(spec)
    out = wrapped({"a": 1})
    assert out == {"ok": True, "echo": {"a": 1}}


def test_list_tool_names_shape():
    names = server_module.list_tool_names()
    assert isinstance(names, list)
    for n in names:
        assert isinstance(n, str) and n.startswith("sv")


def test_main_list_tools_in_process(capsys):
    rc = server_module.main(["--list-tools"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out.strip())
    assert isinstance(payload.get("tools"), list)
    for n in payload["tools"]:
        assert isinstance(n, str) and n.startswith("sv")


def test_main_surface_is_a_live_catalog_with_schema_hashes(capsys):
    rc = server_module.main(["--surface", "--viewer-family", "gltf.v1"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    surface = json.loads(captured.out)
    assert surface["server"] == "sciencevision"
    assert surface["viewer_family"] == "gltf.v1"
    assert surface["tool_count"] == len(surface["tool_names"]) == len(surface["tools"])
    assert [row["name"] for row in surface["tools"]] == surface["tool_names"]
    assert all(len(row["schema_sha256"]) == 64 for row in surface["tools"])
    canonical = json.dumps(
        surface["tool_names"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert surface["catalog_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    schemas = json.dumps(
        surface["tools"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert surface["schema_sha256"] == hashlib.sha256(schemas.encode()).hexdigest()
    # Non-live-scene tools remain available; only category F is gated.
    assert "sv_list_assets" in surface["tool_names"]
    assert "sv3d_open_scene" in surface["tool_names"]
    assert "sv3d_set_slice" not in surface["tool_names"]


def test_unknown_viewer_family_fails_closed(capsys):
    rc = server_module.main(["--surface", "--viewer-family", "unknown.v9"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "unknown viewer family" in captured.err


def test_main_list_tools_subprocess_shape():
    proc = subprocess.run(
        [sys.executable, "-m", "sci3d_agent.mcp.server", "--list-tools"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip())
    assert isinstance(payload.get("tools"), list)


def test_main_invalid_http_port(monkeypatch, capsys):
    monkeypatch.setenv("SV_HTTP_PORT", "not-a-number")
    rc = server_module.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "invalid SV_HTTP_PORT" in captured.err
