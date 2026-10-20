"""RenderedVisibilityFiller for ``ground_truth.type == 'rendered_visibility_label'``
tasks whose ``answer_schema.kind == 'binary_visibility'``.

Loop:
  1. Renderer.render(task) -> RenderResult.
  2. If skip_reason set -> FillResult(status='awaiting_expert', notes=...).
  3. Build a system prompt that pins the JSON schema to the task's
     declared answer_schema.fields, plus a non-empty 'reasoning' string.
  4. VisionChatClient.complete_vision(..., image_paths=...).
  5. parse_json_lenient.
  6. Validate that all answer_schema.fields are present.
  7. Return FillResult(value=..., status='auto_seed', evidence_paths=
     stringified image paths, model=model).

NOTE: the fill() loop here is structurally duplicated with the sibling V3
filler (_camera_image_annotation.py).  Acceptable at 2 fillers (kept self-contained for
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
You are scoring ground-truth labels for a ScienceVision 3D visibility task.

You will see ONE rendered thumbnail of the scientific 3D asset.
Judge the requested visibility predicate STRICTLY from the rendered image.
Do not invoke prior knowledge about the asset's typical appearance — only
what is visible in this specific render.

Return a single JSON object with EXACTLY these fields (no extras, no nulls
omitted) plus an additional ``reasoning`` field:

{fields_spec}

Field semantics:
- Boolean fields are true/false (not "yes"/"no", not strings).
- Non-boolean fields are short strings or null when not applicable.
- ``reasoning`` is a single sentence justifying the answer from the render.

Output ONLY the JSON object. No markdown fences. No prose around it.
"""


def _fields_spec(fields: list[str]) -> str:
    lines = []
    for f in fields:
        lines.append(f"  - {f}")
    return "\n".join(lines)


class RenderedVisibilityFiller:
    """Filler for rendered_visibility_label tasks: judges structured visibility predicates from a single rendered image."""

    def __init__(self, renderer: Renderer, client: Any, model: str) -> None:
        """
        Args:
            renderer: Anything with ``.render(task) -> RenderResult``.
            client: Anything with ``.complete_vision(...)`` returning a
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
            notes="Generated by RenderedVisibilityFiller (LLM-as-judge over thumbnail).",
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
    """Register a RenderedVisibilityFiller under ``rendered_visibility_label``.

    Separate from class definition so tests can use the class directly without
    a singleton.  CLI / pipeline-stage callers invoke ``register_default()``
    once with their wired client + renderer.
    """
    if renderer is None:
        if repo_root is None:
            raise ValueError("provide either renderer or repo_root")
        renderer = ThumbnailRenderer(repo_root=repo_root)
    FILLERS["rendered_visibility_label"] = RenderedVisibilityFiller(
        renderer=renderer, client=client, model=model
    )
