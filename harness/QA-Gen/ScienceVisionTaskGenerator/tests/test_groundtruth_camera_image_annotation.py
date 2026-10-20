"""Tests for CameraImageAnnotationFiller (V3 camera_plus_image_annotation)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sciencevision_taskgen.groundtruth._camera_image_annotation import (
    CameraImageAnnotationFiller,
)
from sciencevision_taskgen.groundtruth.base import FillResult
from sciencevision_taskgen.groundtruth.renderer import RenderResult
from sciencevision_taskgen.llm.client import ChatClientError, ChatResult


@dataclass
class _FakeRenderer:
    result: RenderResult

    def render(self, task: dict) -> RenderResult:
        return self.result


class _FakeVisionClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def complete_vision_with_retry(self, **kw) -> ChatResult:
        self.calls.append(kw)
        return ChatResult(
            content=self.content,
            latency_ms=10.0,
            prompt_tokens=60,
            completion_tokens=20,
            reasoning_tokens=0,
        )


class _BoomClient:
    def complete_vision_with_retry(self, **kw):
        raise ChatClientError("simulated LLM failure")


def _make_pointcloud_task(
    thumb: str | None = "/viewer/thumbs/c3c9b3d10b9dc84a.webp",
) -> dict[str, Any]:
    return {
        "task_id": "sv-pointcloud-v3-0001",
        "v_class": "V3",
        "domain": "pointcloud",
        "answer_schema": {
            "kind": "visual_annotation",
            "fields": ["camera", "annotation_bbox_or_point", "region_label"],
        },
        "ground_truth": {"type": "camera_plus_image_annotation"},
        "asset": {
            "asset_id": "pointcloud:0000",
            "name": "laspy_simple",
            "thumb": thumb,
        },
        "instruction": (
            "Load the laspy_simple.las point cloud and render it; "
            "annotate the visible extremal point."
        ),
    }


def _make_biology_task(
    thumb: str | None = "/viewer/thumbs/bio001.webp",
) -> dict[str, Any]:
    return {
        "task_id": "sv-biology-v3-0001",
        "v_class": "V3",
        "domain": "biology",
        "answer_schema": {
            "kind": "visual_annotation",
            "fields": ["camera", "annotation_bbox_or_mask", "target_labels"],
        },
        "ground_truth": {"type": "camera_plus_image_annotation"},
        "asset": {
            "asset_id": "biology:0000",
            "name": "cell_sample",
            "thumb": thumb,
        },
        "instruction": "Annotate the nucleus in the cell render.",
    }


# ---- awaiting_expert when renderer skips ----

def test_filler_returns_awaiting_expert_when_renderer_skips():
    renderer = _FakeRenderer(RenderResult(skip_reason="no thumb declared on asset"))
    client = _FakeVisionClient(content="{}")
    filler = CameraImageAnnotationFiller(
        renderer=renderer, client=client, model="claude-sonnet-4-6"
    )
    r = filler.fill(_make_pointcloud_task(thumb=None))
    assert r.status == "awaiting_expert"
    assert r.value is None
    assert "renderer skipped" in r.notes.lower() or "no thumb" in r.notes.lower()
    assert client.calls == []


# ---- auto_seed when LLM returns valid pointcloud JSON ----

def test_filler_returns_auto_seed_for_pointcloud_task(tmp_path: Path):
    img = tmp_path / "c3c9b3d10b9dc84a.webp"
    img.write_bytes(b"\x00fake")
    renderer = _FakeRenderer(RenderResult(image_paths=(img,), media_type="image/webp"))
    payload = json.dumps({
        "camera": {"azimuth": 45.0, "elevation": 30.0, "zoom": 1.0},
        "annotation_bbox_or_point": [0.5, 0.6],
        "region_label": "highest elevation point",
        "reasoning": "The topmost point in the render corresponds to max Z.",
    })
    client = _FakeVisionClient(content=payload)
    filler = CameraImageAnnotationFiller(
        renderer=renderer, client=client, model="claude-sonnet-4-6"
    )

    r = filler.fill(_make_pointcloud_task())
    assert r.status == "auto_seed"
    assert isinstance(r.value, dict)
    assert "camera" in r.value
    assert "annotation_bbox_or_point" in r.value
    assert "region_label" in r.value
    assert r.evidence_paths == (str(img),)
    assert r.model == "claude-sonnet-4-6"
    # System prompt must name the fields from answer_schema.
    sent = client.calls[0]
    assert "annotation_bbox_or_point" in sent["system"]
    assert "region_label" in sent["system"]


# ---- auto_seed when LLM returns valid biology JSON (different field names) ----

def test_filler_returns_auto_seed_for_biology_task(tmp_path: Path):
    img = tmp_path / "bio001.webp"
    img.write_bytes(b"\x00fake")
    renderer = _FakeRenderer(RenderResult(image_paths=(img,), media_type="image/webp"))
    payload = json.dumps({
        "camera": {"azimuth": 0.0, "elevation": 90.0, "zoom": 2.0},
        "annotation_bbox_or_mask": [0.1, 0.2, 0.4, 0.5],
        "target_labels": "nucleus",
        "reasoning": "The round dark region in the centre is the nucleus.",
    })
    client = _FakeVisionClient(content=payload)
    filler = CameraImageAnnotationFiller(
        renderer=renderer, client=client, model="claude-sonnet-4-6"
    )

    r = filler.fill(_make_biology_task())
    assert r.status == "auto_seed"
    assert "annotation_bbox_or_mask" in r.value
    assert "target_labels" in r.value
    # Confirm system prompt references biology field names.
    sent = client.calls[0]
    assert "annotation_bbox_or_mask" in sent["system"]
    assert "target_labels" in sent["system"]


# ---- awaiting_expert when LLM omits a required field ----

def test_filler_rejects_missing_field(tmp_path: Path):
    img = tmp_path / "t.webp"
    img.write_bytes(b"\x00")
    renderer = _FakeRenderer(RenderResult(image_paths=(img,), media_type="image/webp"))
    # Missing "region_label"
    payload = json.dumps({
        "camera": {"azimuth": 10.0, "elevation": 20.0, "zoom": 1.0},
        "annotation_bbox_or_point": [0.3, 0.4],
        "reasoning": "visible at top",
    })
    client = _FakeVisionClient(content=payload)
    filler = CameraImageAnnotationFiller(
        renderer=renderer, client=client, model="m"
    )

    r = filler.fill(_make_pointcloud_task())
    assert r.status == "awaiting_expert"
    assert r.value is None
    assert "missing" in r.notes.lower()
    assert "region_label" in r.notes


# ---- awaiting_expert on malformed JSON ----

def test_filler_rejects_malformed_json(tmp_path: Path):
    img = tmp_path / "t.webp"
    img.write_bytes(b"\x00")
    renderer = _FakeRenderer(RenderResult(image_paths=(img,), media_type="image/webp"))
    client = _FakeVisionClient(content="not-valid-json{{{")
    filler = CameraImageAnnotationFiller(
        renderer=renderer, client=client, model="m"
    )

    r = filler.fill(_make_pointcloud_task())
    assert r.status == "awaiting_expert"
    assert r.value is None
    assert "json" in r.notes.lower()


# ---- awaiting_expert when LLM call raises ChatClientError ----

def test_filler_handles_llm_error(tmp_path: Path):
    img = tmp_path / "t.webp"
    img.write_bytes(b"\x00")
    renderer = _FakeRenderer(RenderResult(image_paths=(img,), media_type="image/webp"))
    filler = CameraImageAnnotationFiller(
        renderer=renderer, client=_BoomClient(), model="m"
    )

    r = filler.fill(_make_pointcloud_task())
    assert r.status == "awaiting_expert"
    assert r.value is None
    assert "simulated" in r.notes.lower() or "llm" in r.notes.lower()
