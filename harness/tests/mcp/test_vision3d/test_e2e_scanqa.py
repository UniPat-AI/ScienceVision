"""End-to-end trajectory for ScanQA (mesh + canonical views sheet)."""

import pytest

from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d._store import reset_store_for_tests
from sci3d_agent.mcp.tools.vision3d.state import _reset_shape_cache


@pytest.fixture(autouse=True)
def _fresh():
    reset_store_for_tests()
    _reset_shape_cache()
    yield
    reset_store_for_tests()
    _reset_shape_cache()


@pytest.fixture
def _registry_snapshot():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def _install_fake(name, fn):
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )
    return orig


def _canned_scanqa_record(*_):
    return {
        "record": {
            "qid": "scanqa-test_wo_obj-test-scene0707-0",
            "asset_kind": "mesh",
            "url_viewer": "/datasets/_shared_3d/scannet/scans_test/scene0707_00/scene0707_00_vh_clean_2.ply",
        }
    }


def _canned_mesh_inspect(*_):
    return {
        "format": "ply",
        "n_vertices": 134360,
        "n_faces": 268720,
        "bbox": [[-5.0, -5.0, 0.0], [5.0, 5.0, 3.0]],
        "extent": [10.0, 10.0, 3.0],
        "has_vertex_colors": True,
        "has_uv": False,
        "axis_up_hint": "Z",
    }


def test_e2e_scanqa_canonical_views(_registry_snapshot):
    _install_fake("sv_get_matched_record", _canned_scanqa_record)
    _install_fake("sv_inspect_mesh", _canned_mesh_inspect)

    # 1. Open
    r = REGISTRY["sv3d_open_scene"].handler(
        {"qid": "scanqa-test_wo_obj-test-scene0707-0"}
    )
    sid = r["session_id"]
    assert r["kind"] == "mesh"
    assert r["axis_up"] == "Z"
    assert r["default_mode"] == "surface"

    # 2. Render canonical views (mocked PNG output if no GL)
    r = REGISTRY["sv3d_render_canonical_views"].handler(
        {
            "session_id": sid,
            "views": ["front", "back", "left", "right", "top", "iso"],
            "tile": True,
        }
    )
    assert "images" in r
    assert len(r["images"]) == 6
    assert all("camera" in img for img in r["images"])
    assert all(
        img["view"] in {"front", "back", "left", "right", "top", "iso"}
        for img in r["images"]
    )

    # 3. Close
    r = REGISTRY["sv3d_close_scene"].handler({"session_id": sid})
    assert r["closed"] is True
