"""Shared fixtures: fresh store + mock assets installed into REGISTRY."""

import pytest
from sci3d_agent.mcp import server  # registers all tools  # noqa: F401
from sci3d_agent.mcp.registry import REGISTRY, ToolSpec
from sci3d_agent.mcp.tools.vision3d._store import reset_store_for_tests
from sci3d_agent.mcp.tools.vision3d.state import _reset_shape_cache


@pytest.fixture(autouse=True)
def fresh():
    reset_store_for_tests()
    _reset_shape_cache()
    yield
    reset_store_for_tests()
    _reset_shape_cache()


@pytest.fixture
def registry_snapshot():
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


def install_fake(name: str, fn) -> None:
    orig = REGISTRY[name]
    REGISTRY[name] = ToolSpec(
        name=orig.name,
        description=orig.description,
        input_schema=orig.input_schema,
        handler=fn,
        category=orig.category,
    )


@pytest.fixture
def mesh_session(registry_snapshot):
    """Open a mesh session with canned record + inspect, return session_id."""
    install_fake(
        "sv_get_matched_record",
        lambda _: {
            "record": {
                "qid": "fixture-mesh-0",
                "asset_kind": "mesh",
                "url_viewer": (
                    "/datasets/_shared_3d/scannet/scans_test/"
                    "scene0707_00/scene0707_00_vh_clean_2.ply"
                ),
            }
        },
    )
    install_fake(
        "sv_inspect_mesh",
        lambda _: {
            "format": "ply",
            "n_vertices": 1000,
            "n_faces": 2000,
            "bbox": [[-5.0, -5.0, 0.0], [5.0, 5.0, 3.0]],
            "extent": [10.0, 10.0, 3.0],
            "has_vertex_colors": True,
            "has_uv": False,
            "axis_up_hint": "Z",
        },
    )
    r = REGISTRY["sv3d_open_scene"].handler({"qid": "fixture-mesh-0"})
    return r["session_id"]


@pytest.fixture
def nifti_session(registry_snapshot):
    """Open a nifti session with canned record + inspect, return session_id."""
    install_fake(
        "sv_get_matched_record",
        lambda _: {
            "record": {
                "qid": "fixture-nifti-0",
                "asset_kind": "nifti",
                "url_viewer": "/datasets/_shared_3d/medical/sample.nii.gz",
                # mask block: _resolve_paths_from_qid reads r.get("mask") as a dict
                # with key "mask_url_viewer" — a top-level "mask_path" is ignored.
                "mask": {
                    "mask_url_viewer": "/datasets/_shared_3d/medical/sample_mask.nii.gz",
                },
            }
        },
    )
    # sv_inspect_nifti is called by _slice_math._get_nifti_shape during set_slice.
    # Must return shape so the shape cache can be populated.
    install_fake(
        "sv_inspect_nifti",
        lambda _: {
            "shape": [256, 256, 180],
            "spacing": [1.0, 1.0, 1.0],
            "affine_4x4": [
                [1, 0, 0, -128],
                [0, 1, 0, -128],
                [0, 0, 1, -90],
                [0, 0, 0, 1],
            ],
            "axis_up_hint": "RAS",
            "intensity": {"p1": 0.0, "p99": 1000.0},
        },
    )
    # When mask_path is present, _inspect_for_open calls sv_inspect_nifti_mask_pair
    # instead of sv_inspect_nifti. This combined envelope must include shape, spacing,
    # affine_4x4, intensity (for _build_session_from_inspector) AND labels
    # (for _build_mask_info). Note: _build_mask_info uses lbl["id"], not lbl["mask_id"].
    install_fake(
        "sv_inspect_nifti_mask_pair",
        lambda _: {
            "shape": [256, 256, 180],
            "spacing": [1.0, 1.0, 1.0],
            "affine_4x4": [
                [1, 0, 0, -128],
                [0, 1, 0, -128],
                [0, 0, 1, -90],
                [0, 0, 0, 1],
            ],
            "axis_up_hint": "RAS",
            "intensity": {"p1": 0.0, "p99": 1000.0},
            "labels": [
                {
                    "id": "1",
                    "voxel_count": 5000,
                    "centroid_world": [0.0, 0.0, 0.0],
                    "bbox_world": [[-10.0, -10.0, -10.0], [10.0, 10.0, 10.0]],
                    "best_slices": {"axial": 90, "coronal": 128, "sagittal": 128},
                }
            ],
        },
    )
    r = REGISTRY["sv3d_open_scene"].handler({"qid": "fixture-nifti-0"})
    assert "session_id" in r, f"open_scene failed: {r}"
    return r["session_id"]
