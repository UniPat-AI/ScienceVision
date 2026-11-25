"""Portable NIfTI sequence capability, exercised without external datasets."""
from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from sci3d_agent.mcp.registry import REGISTRY
from sci3d_agent.mcp.tools.vision3d._store import get_store, reset_store_for_tests


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    reset_store_for_tests()
    entries = []
    for name, size, offset, role in (
        ("T1GD", 8, 0, "primary"), ("T2", 12, -2, "sequence"),
        ("R", 8, 0, "mask"),
    ):
        affine = np.eye(4)
        affine[:3, 3] = offset
        image = nib.Nifti1Image(np.full((size,) * 3, 10 if name == "T1GD" else 40, np.int16), affine)
        image.header.set_xyzt_units("mm")
        path = tmp_path / f"{name}.nii.gz"
        nib.save(image, path)
        entries.append({"asset_id": name, "asset_path": str(path), "format": "nifti", "role": role})
    manifest = tmp_path / "task-assets.json"
    manifest.write_text(json.dumps({"schema_version": "sciencevision.mcp_task_assets.v1", "assets": entries}))
    monkeypatch.setenv("SV_MCP_TASK_ASSET_MANIFEST", str(manifest))
    yield entries, manifest
    reset_store_for_tests()


def test_real_nifti_switch_preserves_world_focus_and_repeats(volumes):
    entries, _ = volumes
    opened = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})
    assert "error" not in opened, opened
    sid = opened["session_id"]
    REGISTRY["sv3d_set_slice"].handler({"session_id": sid, "axis": "axial", "index": 3})
    assert "sv3d_switch_sequence" in REGISTRY
    tool = REGISTRY["sv3d_switch_sequence"]
    switched = tool.handler({"session_id": sid, "sequence": "T2"})
    assert "error" not in switched, switched
    state = REGISTRY["sv3d_get_state"].handler({"session_id": sid})
    assert state["sequence"] == "T2"
    assert [s["name"] for s in state["sequences"]] == ["T1GD", "T2"]
    assert Path(get_store().get(sid).asset_path).name == "T2.nii.gz"
    assert state["slice"]["index"] == 5  # world z=3, target starts at -2
    before = get_store().get(sid)
    repeated = tool.handler(switched["replay_input"])
    assert "error" not in repeated
    assert get_store().get(sid) == before
    rejected = tool.handler({"session_id": sid, "sequence": "R"})
    assert "error" in rejected
    assert get_store().get(sid) == before


def test_sequence_missing_file_does_not_mutate_session(volumes):
    entries, _ = volumes
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    before = get_store().get(sid)
    Path(entries[1]["asset_path"]).unlink()
    assert "sv3d_switch_sequence" in REGISTRY
    result = REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": "T2"})
    assert "error" in result
    assert get_store().get(sid) == before


def test_task_surface_has_real_enum_and_single_sequence_hides_tool(volumes):
    from sci3d_agent.mcp.surface import selected_tools

    _, manifest = volumes
    tools = selected_tools("niivue.v1")
    switch = next((t for t in tools if t.name == "sv3d_switch_sequence"), None)
    assert switch is not None
    assert switch.input_schema["properties"]["sequence"]["enum"] == ["T1GD", "T2"]
    payload = json.loads(manifest.read_text())
    payload["assets"] = payload["assets"][:1]
    manifest.write_text(json.dumps(payload))
    assert "sv3d_switch_sequence" not in {t.name for t in selected_tools("niivue.v1")}
    assert "sv3d_switch_sequence" not in {t.name for t in selected_tools("gltf.v1")}


def test_actual_mcp_client_receives_sequence_enum(volumes):
    import asyncio
    from fastmcp import Client
    from sci3d_agent.mcp.server import _build_fastmcp_server

    async def inspect():
        async with Client(_build_fastmcp_server("niivue.v1")) as client:
            tools = (await client.session.list_tools()).tools
            switch = next(t for t in tools if t.name == "sv3d_switch_sequence")
            schema = switch.model_dump(by_alias=True)["inputSchema"]
            assert schema["properties"]["args"]["properties"]["sequence"]["enum"] == ["T1GD", "T2"]
    asyncio.run(inspect())


def test_fractional_world_anchor_survives_roundtrip_and_slice_change(volumes):
    entries, _ = volumes
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    tool = REGISTRY["sv3d_switch_sequence"]
    for name in ("T2", "T1GD", "T2", "T1GD"):
        result = tool.handler({"session_id": sid, "sequence": name})
        assert "error" not in result
        assert result["state"]["sequence_focus_world"] == pytest.approx([3.5, 3.5, 3.5])
    REGISTRY["sv3d_set_slice"].handler({"session_id": sid, "axis": "axial", "index": 2})
    result = tool.handler({"session_id": sid, "sequence": "T2"})
    assert result["state"]["sequence_focus_world"] == pytest.approx([3.5, 3.5, 2])


def test_public_mask_preserved_in_world_and_target_window_is_used(volumes, monkeypatch, tmp_path):
    monkeypatch.setenv("SV_RENDER_CACHE", str(tmp_path / "cache"))
    entries, _ = volumes
    opened = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"], "mask_path": entries[2]["asset_path"]})
    sid = opened["session_id"]
    original = get_store().get(sid)
    switched = REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": "T2"})
    assert "error" not in switched, switched
    current = get_store().get(sid)
    assert current.mask_state == original.mask_state
    assert [m.centroid_world for m in current.masks_info] == [m.centroid_world for m in original.masks_info]
    assert nib.load(current.mask_path).shape == (12, 12, 12)
    assert np.allclose(nib.load(current.mask_path).affine, nib.load(current.asset_path).affine)
    assert current.slice.window != original.slice.window
    assert current.bbox != original.bbox


def test_isolated_manifest_rejects_paths_outside_staged_data(volumes, monkeypatch):
    monkeypatch.setenv("SV_MCP_TASK_ISOLATED", "1")
    entries, _ = volumes
    result = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})
    assert "error" in result
    assert get_store().count() == 0


def test_generator_catalog_and_runtime_wire_schema_match(volumes):
    from sci3d_agent.mcp.sequence_catalog import specialize_catalog, wire_schema, digest
    from sci3d_agent.mcp.surface import surface_payload, selected_tools

    entries, _ = volumes
    # No source or /data access is needed when deriving the task receipt.
    catalog = surface_payload("niivue.v1")
    actual = next(t for t in selected_tools("niivue.v1") if t.name == "sv3d_switch_sequence")
    generated = specialize_catalog(catalog, entries)
    entry = next(t for t in generated["tools"] if t["name"] == actual.name)
    assert entry["schema_sha256"] == digest(wire_schema(actual.input_schema))
    assert generated["schema_sha256"] == catalog["schema_sha256"]
    single = specialize_catalog(catalog, entries[:1])
    assert "sv3d_switch_sequence" not in single["tool_names"]


def test_pipeline_opens_declared_primary_and_manifest_is_public(volumes, tmp_path):
    from scripts.harbor.build_harbor_tasks import _stage_assets
    from scripts.harbor._agent_envs import MCP_WORKER_PACKAGING

    entries, _ = volumes
    task = {"domain": "medical", "asset_path": entries[0]["asset_path"],
            "assets": [entries[1], entries[2], entries[0]]}
    staged = _stage_assets(task, tmp_path / "env")
    assert staged["asset_path"] == "/data/T1GD.nii.gz"
    manifest = json.loads(MCP_WORKER_PACKAGING.render_task_asset_manifest(staged))
    assert [r["role"] for r in manifest["assets"]] == ["primary", "sequence"]
    assert not (tmp_path / "env/asset/R.nii.gz").exists()


def test_camera_started_before_slow_load_survives_switch(volumes, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sci3d_agent.mcp.tools.vision3d import camera

    entries, _ = volumes
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    old = get_store().get(sid)
    camera_read, compute, loading, finish_load = (Event() for _ in range(4))
    original_orbit, original_load = camera._orbit_camera, nib.load

    def delayed_orbit(*args):
        camera_read.set()
        assert compute.wait(5)
        return original_orbit(*args)

    def delayed_load(path, *args, **kwargs):
        if str(path) == entries[1]["asset_path"]:
            loading.set()
            assert finish_load.wait(5)
        return original_load(path, *args, **kwargs)

    monkeypatch.setattr(camera, "_orbit_camera", delayed_orbit)
    monkeypatch.setattr(nib, "load", delayed_load)
    with ThreadPoolExecutor(2) as pool:
        moving = pool.submit(REGISTRY["sv3d_camera_orbit"].handler, {"session_id": sid, "d_azimuth_deg": 15, "d_elevation_deg": 0})
        assert camera_read.wait(5)  # real handler already holds the old Session
        switching = pool.submit(REGISTRY["sv3d_switch_sequence"].handler, {"session_id": sid, "sequence": "T2"})
        assert loading.wait(5)
        compute.set()
        finish_load.set()
        assert "error" not in switching.result(5)
        assert "error" not in moving.result(5)
    current = get_store().get(sid)
    assert current.sequence == "T2"
    assert current.camera != old.camera


def test_invalid_nifti_does_not_mutate_session(volumes):
    entries, _ = volumes
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    before = get_store().get(sid)
    Path(entries[1]["asset_path"]).write_bytes(b"not a NIfTI")
    assert "error" in REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": "T2"})
    assert get_store().get(sid) == before


def test_rotation_preserves_continuous_world_focus(volumes):
    entries, _ = volumes
    image = nib.load(entries[1]["asset_path"])
    affine = np.array([[0, -1, 0, 10], [1, 0, 0, -2], [0, 0, 1, -2], [0, 0, 0, 1]], dtype=float)
    nib.save(nib.Nifti1Image(np.asanyarray(image.dataobj), affine, image.header), entries[1]["asset_path"])
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    for name in ("T2", "T1GD"):
        result = REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": name})
        assert "error" not in result, result
        assert result["state"]["sequence_focus_world"] == pytest.approx([3.5] * 3)


def test_crosshair_then_multiple_slice_axes_keep_actual_focus(volumes):
    entries, _ = volumes
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    moved = REGISTRY["sv3d_set_crosshair"].handler({"session_id": sid, "voxel": [1, 2, 3]})
    assert "error" not in moved, moved
    result = REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": "T2"})
    assert result["state"]["sequence_focus_world"] == pytest.approx([1, 2, 3])
    REGISTRY["sv3d_set_slice"].handler({"session_id": sid, "axis": "sagittal", "index": 6})
    REGISTRY["sv3d_set_slice"].handler({"session_id": sid, "axis": "coronal", "index": 7})
    result = REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": "T1GD"})
    assert result["state"]["sequence_focus_world"] == pytest.approx([4, 5, 3])


def test_molecule_multi_primary_manifest_keeps_existing_surface(tmp_path, monkeypatch):
    from sci3d_agent.mcp.surface import selected_tools
    manifest = tmp_path / "assets.json"
    manifest.write_text(json.dumps({"schema_version": "sciencevision.mcp_task_assets.v1", "assets": [
        {"asset_id": name, "asset_path": f"/data/{name}.cif", "role": "primary", "viewer": "molstar"}
        for name in ("a", "b")
    ]}))
    monkeypatch.setenv("SV_MCP_TASK_ASSET_MANIFEST", str(manifest))
    names = {tool.name for tool in selected_tools("molstar.v2")}
    assert "sv3d_open_scene" in names
    assert "sv3d_switch_sequence" not in names


def test_build_one_bundled_stdio_has_manifest_and_image_catalog(volumes, tmp_path):
    from scripts.harbor.build_harbor_tasks import build_one
    from sci3d_agent.mcp.surface import surface_payload

    entries, _ = volumes
    task = {"task_id": "medical-sequence-fixture", "domain": "medical", "instruction": "Inspect the supplied MRI.",
            "asset_path": entries[0]["asset_path"], "asset_format": "nifti",
            "asset": {"path": entries[0]["asset_path"], "format": "nifti", "viewer": "niivue", "role": "primary"},
            "assets": entries, "answer_schema": {"kind": "freeform"}, "scorer": {"primary": "exact_match"},
            "ground_truth": {"value": "synthetic test"}, "evaluation_eligible": False}
    catalog = surface_payload("niivue.v1")
    out = build_one(task, tmp_path / "tasks", containerized=True, agent_env="codex-mcp",
                    mcp_catalog=catalog, bundled_mcp_image="example.invalid/sequence-runtime:test")
    env = out / "environment"
    dockerfile = (env / "Dockerfile").read_text()
    assert "FROM example.invalid/sequence-runtime:test" in dockerfile
    assert "COPY asset/ /data/" in dockerfile
    assert "COPY mcp-task-assets.json /etc/sciencevision-mcp/task-assets.json" in dockerfile
    assert "SV_MCP_TASK_ASSET_MANIFEST=/etc/sciencevision-mcp/task-assets.json" in dockerfile
    assert not (env / "Dockerfile.mcp-worker").exists()
    assert not (env / "docker-compose.yaml").exists()
    assert not (env / "asset/R.nii.gz").exists()
    assert json.loads((env / "mcp.stdio.json").read_text())["mcpServers"]["sciencevision"]["command"] == "sciencevision-mcp"
    profile = json.loads((env / "mcp-runtime-profile.json").read_text())
    assert profile["transport"] == "stdio"
    assert "sv3d_switch_sequence" in profile["catalog"]["tool_names"]


def test_exported_catalog_matches_every_actual_wire_input_schema(volumes):
    import asyncio
    from fastmcp import Client
    from sci3d_agent.mcp.server import _build_fastmcp_server
    from sci3d_agent.mcp.sequence_catalog import digest
    from sci3d_agent.mcp.surface import surface_payload

    expected = {entry["name"]: entry["schema_sha256"] for entry in surface_payload("niivue.v1")["tools"]}

    async def inspect():
        async with Client(_build_fastmcp_server("niivue.v1")) as client:
            tools = (await client.session.list_tools()).tools
            observed = {tool.name: digest(tool.model_dump(by_alias=True)["inputSchema"]) for tool in tools}
            assert observed == expected
    asyncio.run(inspect())


@pytest.mark.parametrize(("mode", "dimension", "index"), [("mpr_coronal", 1, 4), ("mpr_sagittal", 0, 3)])
def test_external_render_uses_the_selected_plane_of_world_anchor(volumes, monkeypatch, tmp_path, mode, dimension, index):
    import base64
    import io
    from PIL import Image

    monkeypatch.setenv("SV_RENDER_CACHE", str(tmp_path / "renders"))
    entries, _ = volumes
    image = nib.load(entries[1]["asset_path"])
    ijk = np.indices(image.shape)
    data = (ijk[0] * 10 + ijk[1] * 100 + ijk[2]).astype(np.int16)
    nib.save(nib.Nifti1Image(data, image.affine, image.header), entries[1]["asset_path"])
    sid = REGISTRY["sv3d_open_scene"].handler({"asset_path": entries[0]["asset_path"]})["session_id"]
    REGISTRY["sv3d_set_crosshair"].handler({"session_id": sid, "voxel": [1, 2, 3]})
    REGISTRY["sv3d_switch_sequence"].handler({"session_id": sid, "sequence": "T2"})
    REGISTRY["sv3d_set_render_mode"].handler({"session_id": sid, "mode": mode})
    result = REGISTRY["sv3d_render"].handler({"session_id": sid, "width": 128, "height": 128})
    assert result.get("renderer_used") == "nifti_slice", result
    assert result["axis"] == mode.removeprefix("mpr_")
    assert result["slice_index"] == index
    # Check decoded pixels against the real expected array plane, not the
    # reported locator alone. No synthetic renderer or fake inspector.
    pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(result["image_png_base64"]))))[:, :, :3]
    plane = np.take(data, index, axis=dimension).astype(np.float32)
    lo, hi = get_store().get(sid).slice.window
    gray = ((np.clip(plane, lo, hi) - lo) / (hi - lo) * 255).astype(np.uint8)
    expected = np.repeat(np.flipud(gray.T)[:, :, None], 3, axis=2)
    assert np.max(np.abs(pixels.astype(int) - expected.astype(int))) <= 1
