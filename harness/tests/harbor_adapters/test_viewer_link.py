from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor._viewer_link import decode_cam, parse_viewer_link, seed_pose_from_cam  # noqa: E402


def _enc(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_decode_cam_roundtrips_encode():
    cam = {"position": [1.0, 0.0, 0.0], "target": [0.0, 0.0, 0.0], "up": [0, 0, 1], "fov_deg": 45}
    assert decode_cam(_enc(cam)) == cam


def test_decode_cam_none_on_empty():
    assert decode_cam(None) is None
    assert decode_cam("") is None


def test_decode_cam_rejects_missing_position_target():
    assert decode_cam(_enc({"position": [1, 0, 0]})) is None
    assert decode_cam(_enc({"target": [0, 0, 0]})) is None


def test_decode_cam_rejects_wrong_length():
    assert decode_cam(_enc({"position": [1, 0], "target": [0, 0, 0]})) is None


def test_parse_gltf_link_extracts_file_and_cam():
    cam = {"position": [2, 0, 0], "target": [0, 0, 0], "up": [0, 0, 1], "fov_deg": 45}
    url = f"https://x/viewer/gltf.html?file=assets/m.glb&cam={_enc(cam)}"
    got = parse_viewer_link(url)
    assert got["viewer_type"] == "gltf"
    assert got["file"] == "assets/m.glb"
    assert got["cam"] == cam


def test_parse_niivue_link_is_asset_only():
    got = parse_viewer_link("https://x/viewer/niivue.html?file=vol.nii.gz")
    assert got["viewer_type"] == "niivue" and got["file"] == "vol.nii.gz"
    assert got["cam"] is None
    assert got["slice"] is None


def test_parse_pointcloud_link_is_asset_only():
    got = parse_viewer_link("https://x/viewer/pointcloud.html?file=c.las")
    assert got["viewer_type"] == "pointcloud" and got["file"] == "c.las"
    assert got["cam"] is None


def test_malformed_cam_is_none_not_raise():
    got = parse_viewer_link("https://x/viewer/gltf.html?file=m.glb&cam=%%%bad")
    assert got["cam"] is None and got["file"] == "m.glb"


def test_gltf_slice_decoded():
    url = "https://x/viewer/gltf.html?file=v.nii.gz&slice=axial:0.5:400:40"
    got = parse_viewer_link(url)
    assert got["slice"] == {"axis": "axial", "fraction": 0.5, "window": 400.0, "level": 40.0}


def test_gltf_missing_query_params_yield_empty_file_and_none_cam():
    got = parse_viewer_link("https://x/viewer/gltf.html")
    assert got["file"] == "" and got["cam"] is None and got["slice"] is None


def test_seed_pose_from_cam_plus_x_is_azimuth0():
    sp = seed_pose_from_cam({"position": [5, 0, 2], "target": [0, 0, 0]})
    assert abs(sp["azimuth"] - 0.0) < 1e-6
    assert sp["elevation"] > 0.0


def test_seed_pose_from_cam_none_when_falsy():
    assert seed_pose_from_cam(None) is None
    assert seed_pose_from_cam({}) is None


def test_seed_pose_from_cam_plus_y_is_azimuth90():
    sp = seed_pose_from_cam({"position": [0, 5, 0], "target": [0, 0, 0]})
    assert abs(sp["azimuth"] - 90.0) < 1e-6
    assert abs(sp["elevation"] - 0.0) < 1e-6
