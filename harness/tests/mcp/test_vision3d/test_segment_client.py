"""Unit tests for the SAM HTTP client wrapper."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from sci3d_agent.mcp.tools.vision3d._segment_client import (
    SamClient,
    SamClientError,
)


@pytest.fixture
def client():
    return SamClient(base_url="http://sam.test")


@respx.mock
def test_sam2d_happy_path(client):
    respx.post("http://sam.test/sam2d").mock(
        return_value=Response(200, json={"masks": [{"mask_b64": "AA", "score": 0.9, "bbox": [0, 0, 1, 1], "area": 1}]})
    )
    out = client.sam2d(image_b64="ZZ", text_prompt="x")
    assert out["masks"][0]["score"] == 0.9


@respx.mock
def test_sam3d_volume_happy_path(client):
    respx.post("http://sam.test/sam3d_volume").mock(
        return_value=Response(200, json={"masks": [{"voxel_mask_b64": "AA", "score": 0.8, "voxel_count": 10, "centroid": [0,0,0], "bbox": [[0,0,0],[1,1,1]]}]})
    )
    out = client.sam3d_volume(volume_path="/v.nii.gz", text_prompt="x")
    assert out["masks"][0]["voxel_count"] == 10


@respx.mock
def test_sam3d_mesh_happy_path(client):
    respx.post("http://sam.test/sam3d_mesh").mock(
        return_value=Response(200, json={"segments": [{"per_point_labels_b64": "AA", "score": 0.7, "point_count": 5, "centroid": [0,0,0], "bbox": [[0,0,0],[1,1,1]]}]})
    )
    out = client.sam3d_mesh(pointcloud_path="/s.ply", text_prompt="x")
    assert out["segments"][0]["point_count"] == 5


@respx.mock
def test_http_500_raises_client_error(client):
    respx.post("http://sam.test/sam2d").mock(return_value=Response(500, text="boom"))
    with pytest.raises(SamClientError, match="500"):
        client.sam2d(image_b64="ZZ", text_prompt="x")


@respx.mock
def test_http_4xx_with_json_detail_raises(client):
    respx.post("http://sam.test/sam2d").mock(return_value=Response(400, json={"detail": "bad image"}))
    with pytest.raises(SamClientError, match="bad image"):
        client.sam2d(image_b64="ZZ", text_prompt="x")


@respx.mock
def test_connection_error_raises(client):
    respx.post("http://sam.test/sam2d").mock(side_effect=Exception("net down"))
    with pytest.raises(SamClientError, match="net down"):
        client.sam2d(image_b64="ZZ", text_prompt="x")


@respx.mock
def test_2xx_invalid_json_raises(client):
    respx.post("http://sam.test/sam2d").mock(
        return_value=Response(200, text="<html>")
    )
    with pytest.raises(SamClientError, match="JSON decode failed"):
        client.sam2d(image_b64="ZZ", text_prompt="x")
