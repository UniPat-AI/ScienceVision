"""Tests for RenderedVisibilityFiller against fake clients/renderers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sciencevision_taskgen.groundtruth import FILLERS, dispatch
from sciencevision_taskgen.groundtruth._rendered_visibility import (
    RenderedVisibilityFiller,
    register_default,
)
from sciencevision_taskgen.groundtruth.base import FillResult
from sciencevision_taskgen.groundtruth.renderer import RenderResult
from sciencevision_taskgen.llm.client import ChatResult


@dataclass
class _FakeRenderer:
    result: RenderResult

    def render(self, task: dict) -> RenderResult:
        return self.result


class _FakeVisionClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def complete_vision(self, **kw) -> ChatResult:
        self.calls.append(kw)
        return ChatResult(
            content=self.content,
            latency_ms=10.0,
            prompt_tokens=42,
            completion_tokens=13,
            reasoning_tokens=0,
        )

    # Retry wrapper delegates to complete_vision in the fake (no real retry).
    def complete_vision_with_retry(self, **kw) -> ChatResult:
        return self.complete_vision(**kw)


def _make_task(
    *,
    fields: tuple[str, ...] = ("fully_visible", "clipping_side"),
    thumb: str | None = "/viewer/thumbs/abc.webp",
) -> dict[str, Any]:
    return {
        "task_id": "sv-medical-v2-0001",
        "v_class": "V2",
        "domain": "medical",
        "answer_schema": {"kind": "binary_visibility", "fields": list(fields)},
        "ground_truth": {"type": "rendered_visibility_label"},
        "asset": {"asset_id": "a", "name": "hippocampus_001", "thumb": thumb},
        "instruction": "Is the hippocampus fully inside the viewport?",
    }


def test_filler_returns_awaiting_expert_when_renderer_skips(tmp_path: Path):
    renderer = _FakeRenderer(RenderResult(skip_reason="no thumb declared on asset"))
    client = _FakeVisionClient(content='{"fully_visible": true, "clipping_side": null}')
    filler = RenderedVisibilityFiller(renderer=renderer, client=client, model="claude-sonnet-4-6")

    r = filler.fill(_make_task(thumb=None))
    assert r.status == "awaiting_expert"
    assert r.value is None
    assert "no thumb" in r.notes
    assert client.calls == []


def test_filler_returns_auto_seed_with_value(tmp_path: Path):
    img = tmp_path / "abc.webp"
    img.write_bytes(b"\x00fake")
    renderer = _FakeRenderer(
        RenderResult(image_paths=(img,), media_type="image/webp")
    )
    payload = json.dumps({
        "fully_visible": True,
        "clipping_side": None,
        "reasoning": "structure is centred in frame",
    })
    client = _FakeVisionClient(content=payload)
    filler = RenderedVisibilityFiller(renderer=renderer, client=client, model="claude-sonnet-4-6")

    r = filler.fill(_make_task())
    assert r.status == "auto_seed"
    assert r.value == {
        "fully_visible": True,
        "clipping_side": None,
        "reasoning": "structure is centred in frame",
    }
    assert r.evidence_paths == (str(img),)
    assert r.model == "claude-sonnet-4-6"
    sent = client.calls[0]
    assert "fully_visible" in sent["system"]
    assert "clipping_side" in sent["system"]


def test_filler_handles_different_fields(tmp_path: Path):
    img = tmp_path / "kuka.webp"
    img.write_bytes(b"\x00fake")
    renderer = _FakeRenderer(
        RenderResult(image_paths=(img,), media_type="image/webp")
    )
    client = _FakeVisionClient(
        content=json.dumps(
            {"visible": True, "occluding_link": None, "reasoning": "no occluders"}
        )
    )
    filler = RenderedVisibilityFiller(renderer=renderer, client=client, model="m")
    task = _make_task(fields=("visible", "occluding_link"))
    r = filler.fill(task)
    assert r.status == "auto_seed"
    assert r.value["visible"] is True
    assert r.value["occluding_link"] is None
    assert "visible" in client.calls[0]["system"]
    assert "occluding_link" in client.calls[0]["system"]


def test_filler_returns_awaiting_expert_on_llm_error(tmp_path: Path):
    img = tmp_path / "abc.webp"
    img.write_bytes(b"\x00fake")
    renderer = _FakeRenderer(
        RenderResult(image_paths=(img,), media_type="image/webp")
    )

    class _Boom:
        def complete_vision(self, **kw):
            from sciencevision_taskgen.llm.client import ChatClientError
            raise ChatClientError("simulated")

        def complete_vision_with_retry(self, **kw):
            from sciencevision_taskgen.llm.client import ChatClientError
            raise ChatClientError("simulated")

    filler = RenderedVisibilityFiller(renderer=renderer, client=_Boom(), model="m")
    r = filler.fill(_make_task())
    assert r.status == "awaiting_expert"
    assert r.value is None
    assert "simulated" in r.notes


def test_filler_rejects_missing_required_field(tmp_path: Path):
    img = tmp_path / "abc.webp"
    img.write_bytes(b"\x00fake")
    renderer = _FakeRenderer(
        RenderResult(image_paths=(img,), media_type="image/webp")
    )
    client = _FakeVisionClient(content='{"clipping_side": "left"}')
    filler = RenderedVisibilityFiller(renderer=renderer, client=client, model="m")
    r = filler.fill(_make_task())
    assert r.status == "awaiting_expert"
    assert "missing" in r.notes.lower()
    assert "fully_visible" in r.notes

