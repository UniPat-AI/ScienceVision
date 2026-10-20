"""Tests for Category B URL builder tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from sci3d_agent.mcp.registry import REGISTRY
from sci3d_agent.mcp.tools import urls as urls_module
from sci3d_agent.mcp.tools.urls import TOOLS


@pytest.fixture(autouse=True)
def _clear_assets_cache():
    urls_module._assets_path_cached.cache_clear()
    yield
    urls_module._assets_path_cached.cache_clear()


def _handler(name: str):
    for spec in TOOLS:
        if spec.name == name:
            return spec.handler
    raise KeyError(name)


def test_tool_count_is_four():
    assert len(TOOLS) == 4


def test_tools_registered_under_category_b():
    names = {
        "sv_build_viewer_url",
        "sv_build_match3d_url",
        "sv_build_dual_pane_url",
        "sv_build_vqa_url",
    }
    for spec in TOOLS:
        assert spec.name in names
        assert spec.category == "B"
        assert spec.name in REGISTRY


# --- sv_build_viewer_url ---------------------------------------------------


def test_auto_resolve_picks_niivue_for_nifti():
    """`sv_build_viewer_url` reads viewer/assets.json and infers viewer=niivue."""
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": (
                "/crawl_data/medical/decathlon/extracted/Task04_Hippocampus/"
                "imagesTr/hippocampus_001.nii.gz"
            )
        }
    )
    assert "error" not in result
    assert result["viewer"] == "niivue"
    assert "/viewer/niivue.html" in result["url"]
    assert result["params_used"]["file"].endswith("hippocampus_001.nii.gz")


def test_auto_resolve_handles_path_without_leading_slash():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": (
                "crawl_data/medical/decathlon/extracted/Task04_Hippocampus/"
                "imagesTr/hippocampus_001.nii.gz"
            )
        }
    )
    assert "error" not in result
    assert result["viewer"] == "niivue"


def test_rejects_overlay_on_gltf():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": "/crawl_data/industrial/khronos_gltf/Avocado.glb",
            "viewer": "gltf",
            "overlay": "/some/overlay.glb",
        }
    )
    assert "error" in result
    assert "overlay" in result["error"]


def test_rejects_type_on_gltf():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": "/x.glb",
            "viewer": "gltf",
            "type": "pdb",
        }
    )
    assert "error" in result


def test_rejects_series_on_niivue():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": "/x.nii.gz",
            "viewer": "niivue",
            "series": "dicom://abc",
        }
    )
    assert "error" in result


def test_molstar_requires_type():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": "/crawl_data/biology/rcsb_pdb_mmcif/1A3N.cif",
            "viewer": "molstar",
        }
    )
    assert "error" in result
    assert "type" in result["error"]


def test_molstar_accepts_type():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {
            "asset_path": "/crawl_data/biology/rcsb_pdb_mmcif/1A3N.cif",
            "viewer": "molstar",
            "type": "mmcif",
        }
    )
    assert "error" not in result
    assert result["params_used"]["type"] == "mmcif"


def test_molstar_invalid_type_rejected():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {"asset_path": "/x.pdb", "viewer": "molstar", "type": "xyz"}
    )
    assert "error" in result


def test_urdf_requires_format():
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/r.urdf", "viewer": "urdf"})
    assert "error" in result
    assert "format" in result["error"]


def test_urdf_invalid_format_rejected():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {"asset_path": "/r.urdf", "viewer": "urdf", "format": "xml"}
    )
    assert "error" in result


def test_urdf_accepts_format():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {"asset_path": "/r.urdf", "viewer": "urdf", "format": "urdf"}
    )
    assert "error" not in result
    assert result["params_used"]["format"] == "urdf"


def test_cornerstone_uses_series_param_key():
    handler = _handler("sv_build_viewer_url")
    result = handler(
        {"asset_path": "wadouri:/dicom/series-1", "viewer": "cornerstone"}
    )
    assert "error" not in result
    assert "series" in result["params_used"]
    assert "/viewer/cornerstone.html" in result["url"]


def test_photonics_uses_path_param_key():
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/photonics/x.json", "viewer": "photonics"})
    assert "error" not in result
    assert "path" in result["params_used"]


def test_unknown_asset_path_errors():
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/this/path/does/not/exist/foo.bar"})
    assert "error" in result
    assert "viewer not registered" in result["error"]


def test_unknown_viewer_value_errors():
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/x.bin", "viewer": "totally_made_up"})
    assert "error" in result


def test_missing_asset_path_errors():
    handler = _handler("sv_build_viewer_url")
    assert "error" in handler({})
    assert "error" in handler({"asset_path": ""})
    assert "error" in handler({"asset_path": 123})  # wrong type


def test_url_encoded_special_chars():
    """Paths with spaces are percent-encoded in the URL but preserved in params_used."""
    handler = _handler("sv_build_viewer_url")
    spacey = "/crawl_data/has space/and+plus.glb"
    result = handler({"asset_path": spacey, "viewer": "gltf"})
    assert "error" not in result
    assert "%20" in result["url"]
    assert "%2B" in result["url"]
    assert result["params_used"]["file"] == spacey


def test_viewer_base_env_override(monkeypatch):
    monkeypatch.setenv("SV_VIEWER_BASE", "https://foo.bar")
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/x.glb", "viewer": "gltf"})
    assert "error" not in result
    assert result["url"].startswith("https://foo.bar/viewer/gltf.html?")


def test_viewer_base_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("SV_VIEWER_BASE", "https://foo.bar/")
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/x.glb", "viewer": "gltf"})
    assert "https://foo.bar//viewer" not in result["url"]
    assert result["url"].startswith("https://foo.bar/viewer/")


def test_assets_json_missing_returns_error(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    urls_module._assets_path_cached.cache_clear()
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/x.glb"})
    assert "error" in result
    assert "viewer not registered" in result["error"]


def test_unsupported_asset_viewer_value_errors(monkeypatch, tmp_path):
    """If assets.json maps a path to viewer=`raw` (not in SPEC enum), error out."""
    fake = tmp_path / "viewer"
    fake.mkdir()
    (fake / "assets.json").write_text(
        json.dumps(
            {"misc": [{"name": "x", "path": "/x.bin", "viewer": "raw", "format": "bin"}]}
        )
    )
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    urls_module._assets_path_cached.cache_clear()
    handler = _handler("sv_build_viewer_url")
    result = handler({"asset_path": "/x.bin"})
    assert "error" in result
    assert "unsupported viewer" in result["error"]


# --- sv_build_match3d_url --------------------------------------------------


def test_match3d_bare_url_no_params():
    handler = _handler("sv_build_match3d_url")
    result = handler({})
    assert "error" not in result
    assert result["viewer"] == "match3d"
    assert result["url"].endswith("/viewer/match3d.html")
    assert "notes" not in result


def test_match3d_dataset_only():
    handler = _handler("sv_build_match3d_url")
    result = handler({"dataset": "scanqa"})
    assert "error" not in result
    assert "dataset=scanqa" in result["url"]
    assert "notes" not in result


def test_match3d_invalid_dataset():
    handler = _handler("sv_build_match3d_url")
    result = handler({"dataset": "not-a-real-dataset"})
    assert "error" in result


def test_match3d_qid_notes():
    handler = _handler("sv_build_match3d_url")
    result = handler({"dataset": "m3d-refseg", "qid": "m3d-refseg-001"})
    assert "error" not in result
    assert "qid=m3d-refseg-001" in result["url"]
    assert "notes" in result
    assert "qid" in result["notes"]


def test_match3d_qid_only_without_dataset():
    handler = _handler("sv_build_match3d_url")
    result = handler({"qid": "abc"})
    assert "error" not in result
    assert "qid=abc" in result["url"]
    assert "notes" in result


def test_match3d_rejects_non_string_qid():
    handler = _handler("sv_build_match3d_url")
    assert "error" in handler({"qid": 42})


def test_match3d_rejects_non_string_dataset():
    handler = _handler("sv_build_match3d_url")
    assert "error" in handler({"dataset": 42})


# --- sv_build_dual_pane_url -----------------------------------------------


def test_dual_pane_happy_path():
    handler = _handler("sv_build_dual_pane_url")
    result = handler({"nifti_path": "/a.nii.gz", "mask_path": "/m.nii.gz"})
    assert "error" not in result
    assert result["viewer"] == "niivue-dual"
    parsed = urlparse(result["url"])
    qs = parse_qs(parsed.query)
    assert qs["file"] == ["/a.nii.gz"]
    assert qs["overlay"] == ["/m.nii.gz"]
    assert qs["layout"] == ["dual"]


def test_dual_pane_accepts_plain_nii():
    handler = _handler("sv_build_dual_pane_url")
    result = handler({"nifti_path": "/a.nii", "mask_path": "/m.nii"})
    assert "error" not in result


def test_dual_pane_rejects_non_nifti():
    handler = _handler("sv_build_dual_pane_url")
    result = handler({"nifti_path": "/a.nii.gz", "mask_path": "/m.ply"})
    assert "error" in result
    assert ".nii" in result["error"]


def test_dual_pane_rejects_non_nifti_base():
    handler = _handler("sv_build_dual_pane_url")
    result = handler({"nifti_path": "/a.dcm", "mask_path": "/m.nii.gz"})
    assert "error" in result


def test_dual_pane_requires_both_paths():
    handler = _handler("sv_build_dual_pane_url")
    assert "error" in handler({"nifti_path": "/a.nii.gz"})
    assert "error" in handler({"mask_path": "/m.nii.gz"})
    assert "error" in handler({"nifti_path": "", "mask_path": "/m.nii.gz"})


# --- sv_build_vqa_url ------------------------------------------------------


def test_vqa_url_encoding():
    """Dataset+sample with special chars survive round-trip via parse_qs."""
    handler = _handler("sv_build_vqa_url")
    dataset = "astro dataset"
    sample = "id with spaces & symbols"
    result = handler({"dataset": dataset, "sample": sample})
    assert "error" not in result
    parsed = urlparse(result["url"])
    qs = parse_qs(parsed.query)
    assert qs["dataset"] == [dataset]
    assert qs["sample"] == [sample]


def test_vqa_requires_dataset_and_sample():
    handler = _handler("sv_build_vqa_url")
    assert "error" in handler({"dataset": "x"})
    assert "error" in handler({"sample": "y"})
    assert "error" in handler({"dataset": "", "sample": "y"})
    assert "error" in handler({})


def test_vqa_viewer_field():
    handler = _handler("sv_build_vqa_url")
    result = handler({"dataset": "astro", "sample": "q1"})
    assert result["viewer"] == "vqa"
    assert "/viewer/vqa.html" in result["url"]


# --- envelope invariants ---------------------------------------------------


def test_all_urls_start_with_viewer_base(monkeypatch):
    monkeypatch.setenv("SV_VIEWER_BASE", "https://test.host")
    cases: list[tuple[str, dict[str, Any]]] = [
        ("sv_build_viewer_url", {"asset_path": "/x.glb", "viewer": "gltf"}),
        ("sv_build_match3d_url", {}),
        (
            "sv_build_dual_pane_url",
            {"nifti_path": "/a.nii.gz", "mask_path": "/m.nii.gz"},
        ),
        ("sv_build_vqa_url", {"dataset": "d", "sample": "s"}),
    ]
    for name, args in cases:
        result = _handler(name)(args)
        assert "error" not in result
        assert result["url"].startswith("https://test.host/viewer/")


def test_handlers_do_not_mutate_input():
    handler = _handler("sv_build_viewer_url")
    args = {"asset_path": "/x.glb", "viewer": "gltf"}
    snapshot = dict(args)
    handler(args)
    assert args == snapshot


def test_build_viewer_url_passes_through_cam_opacity_slice():
    r = REGISTRY["sv_build_viewer_url"].handler({
        "asset_path": "/datasets/foo.glb",
        "viewer": "gltf",
        "cam": "eyJwb3MiOlsxLDIsM119",
        "opacity": "base:0.7",
        "slice": "axial:0.5:400:40",
    })
    assert "error" not in r, r
    assert "cam=eyJwb3MiOlsxLDIsM119" in r["url"]
    assert "opacity=base%3A0.7" in r["url"] or "opacity=base:0.7" in r["url"]
    assert "slice=" in r["url"]


def test_build_viewer_url_rejects_cam_on_non_gltf_viewer():
    """cam/opacity/slice opt-in per viewer; molstar shouldn't accept them yet."""
    r = REGISTRY["sv_build_viewer_url"].handler({
        "asset_path": "/datasets/foo.cif",
        "viewer": "molstar",
        "type": "mmcif",
        "cam": "ignored",
    })
    assert "error" in r and "cam" in r["error"]
