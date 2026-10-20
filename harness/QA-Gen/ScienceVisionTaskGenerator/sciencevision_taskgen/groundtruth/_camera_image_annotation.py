"""CameraImageAnnotationFiller for ``ground_truth.type == 'camera_plus_image_annotation'``
tasks whose ``answer_schema.kind == 'visual_annotation'``.

Loop:
  1. Renderer.render(task) -> RenderResult.
  2. If skip_reason set -> FillResult(status='awaiting_expert', notes=...).
  3. Build a system prompt that names the task's declared answer_schema.fields
     and their expected types.  Field names are read at runtime from
     task['answer_schema']['fields']; they are never hard-coded here.
  4. VisionChatClient.complete_vision_with_retry(..., image_paths=...).
  5. parse_json_lenient.
  6. Validate that all answer_schema.fields are present in the parsed JSON.
  7. Return FillResult(value=..., status='auto_seed', ...) on success;
     FillResult(status='awaiting_expert', ...) on every failure mode.

NOTE: the fill() loop here is structurally duplicated with the sibling V3
filler (_rendered_visibility.py).  Acceptable at 2 fillers (kept self-contained for
readability).  Extract a shared _vision_filler_loop helper when a third
LLM-as-judge filler lands so all three stay in sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sciencevision_taskgen.llm._parse import parse_json_lenient
from sciencevision_taskgen.llm.client import ChatClientError

from .base import FillResult
from .registry import FILLERS
from .renderer import Renderer, ThumbnailRenderer


_SYSTEM_TEMPLATE = """\
You are seeding ground-truth labels for a ScienceVision V3 visual-annotation task.

Given ONE rendered thumbnail of the 3D asset, output a JSON with EXACTLY these fields
(plus an additional ``reasoning`` field) and no others:

{fields_spec}

Field semantics:
- ``camera``: a JSON object with keys ``azimuth`` (degrees), ``elevation`` (degrees),
  and ``zoom`` (float > 0).  Describe the thumbnail's apparent viewpoint.
- Fields whose name starts with ``annotation_``: a JSON array of normalized [0,1]
  coordinates.  Use [x, y] for a single point; [x1, y1, x2, y2] for a bounding box;
  [x1, y1, x2, y2, ...] for a polygon or mask outline.
- Fields whose name ends with ``_label`` or ``_labels``: a short string (or list of
  strings) naming what was annotated.  Be specific and concise.
- ``reasoning``: one sentence grounding your annotation in the rendered image.
  Cite visible evidence (e.g. colour, position, shape) directly.

Output ONLY the JSON object.  No markdown fences.  No prose around it.  No nulls
omitted — every field listed above must appear in your response.
"""


def _fields_spec(fields: list[str]) -> str:
    """Format the answer_schema.fields list for inclusion in the system prompt."""
    lines = []
    for f in fields:
        lines.append(f"  - {f}")
    return "\n".join(lines)


class CameraImageAnnotationFiller:
    """Filler for camera_plus_image_annotation tasks: seeds structured
    camera + spatial annotation + label from a single rendered thumbnail."""

    def __init__(self, renderer: Renderer, client: Any, model: str) -> None:
        """
        Args:
            renderer: Anything with ``.render(task) -> RenderResult``.
            client: Anything with ``.complete_vision_with_retry(...)`` returning a
                ChatResult-shaped object.
            model: Model id to pass through to the client.
        """
        self._renderer = renderer
        self._client = client
        self._model = model

    def fill(self, task: dict[str, Any]) -> FillResult:
        fields = list(task.get("answer_schema", {}).get("fields", []))
        if not fields:
            return FillResult(
                value=None,
                status="awaiting_expert",
                notes="answer_schema.fields missing — cannot build filler prompt",
            )

        render = self._renderer.render(task)
        if not render.image_paths:
            return FillResult(
                value=None,
                status="awaiting_expert",
                notes=f"renderer skipped: {render.skip_reason}",
            )

        system = _SYSTEM_TEMPLATE.format(fields_spec=_fields_spec(fields))
        instruction = task.get("instruction", "")
        user_text = (
            f"Task instruction:\n{instruction}\n\n"
            f"Asset name: {task.get('asset', {}).get('name', '')}\n"
            f"Required fields: {fields}\n"
            "Return JSON now."
        )

        try:
            result = self._client.complete_vision_with_retry(
                model=self._model,
                system=system,
                user_text=user_text,
                image_paths=list(render.image_paths),
                image_media_type=render.media_type or "image/webp",
                json_mode=True,
            )
        except ChatClientError as exc:
            return FillResult(
                value=None,
                status="awaiting_expert",
                notes=f"LLM call failed: {exc}",
                evidence_paths=tuple(str(p) for p in render.image_paths),
                model=self._model,
            )

        try:
            parsed = parse_json_lenient(result.content)
        except json.JSONDecodeError as exc:
            return FillResult(
                value=None,
                status="awaiting_expert",
                notes=f"LLM response was not valid JSON: {exc}",
                evidence_paths=tuple(str(p) for p in render.image_paths),
                model=self._model,
            )

        if not isinstance(parsed, dict):
            return FillResult(
                value=None,
                status="awaiting_expert",
                notes=f"LLM JSON was not an object, got {type(parsed).__name__}",
                evidence_paths=tuple(str(p) for p in render.image_paths),
                model=self._model,
            )

        missing = [f for f in fields if f not in parsed]
        if missing:
            return FillResult(
                value=None,
                status="awaiting_expert",
                notes=f"LLM response missing required fields: {missing}",
                evidence_paths=tuple(str(p) for p in render.image_paths),
                model=self._model,
            )

        return FillResult(
            value=parsed,
            status="auto_seed",
            notes="Generated by CameraImageAnnotationFiller (LLM-as-judge over thumbnail).",
            evidence_paths=tuple(str(p) for p in render.image_paths),
            model=self._model,
        )


def register_default(
    *,
    renderer: Renderer | None = None,
    client: Any,
    model: str = "claude-sonnet-4-6",
    repo_root: Path | None = None,
) -> None:
    """Register a CameraImageAnnotationFiller under ``camera_plus_image_annotation``.

    Only registers ``camera_plus_image_annotation``.  The sibling V3 types
    (``slice_plus_image_annotation``, ``viewport_plus_image_annotation``,
    ``frame_sequence_annotation``) are NOT registered here; they remain as
    NotImplementedFillers until their own fillers are implemented.

    Separate from class definition so tests can use the class directly without
    a singleton.  CLI / pipeline-stage callers invoke ``register_default()``
    once with their wired client + renderer.
    """
    if renderer is None:
        if repo_root is None:
            raise ValueError("provide either renderer or repo_root")
        renderer = ThumbnailRenderer(repo_root=repo_root)
    FILLERS["camera_plus_image_annotation"] = CameraImageAnnotationFiller(
        renderer=renderer, client=client, model=model
    )
