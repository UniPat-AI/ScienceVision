"""Adapt MCP tool results into Anthropic content blocks + disk-persisted images.

The agent loop must hand a tool's result back to the model as a valid
Anthropic ``tool_result`` content block. Two concerns are handled here so
``agent.py`` stays small:

* **Images become pixels the model can see.** MCP renderers return PNG bytes
  as base64 under a handful of known keys (``image_png_base64``,
  ``image_b64``, ``tiled_image_b64``, or a list under ``images``). We lift
  each into an Anthropic ``image`` block and, separately, persist it to disk
  so the trajectory references a PNG path rather than carrying multi-KB
  base64 inline.
* **The textual part stays compact.** Image base64 is stripped from the JSON
  echoed back as text (replaced with a short ``"<image:rel/path.png>"``
  placeholder), so neither the model's context nor the saved trajectory is
  bloated by pixel data.

Pure functions only; the caller decides where images land on disk.
"""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any

# Result keys whose value is a single base64-encoded PNG string.
_SINGLE_IMAGE_KEYS = ("image_png_base64", "image_b64", "tiled_image_b64", "png_b64")
# Result key whose value is a list of dicts each carrying one of the above.
_IMAGE_LIST_KEY = "images"


def _decode_png(b64: str) -> bytes | None:
    """Decode a base64 PNG, returning None if it is not valid base64."""
    if not isinstance(b64, str) or len(b64) < 16:
        return None
    try:
        return base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None


def extract_images(result: dict) -> list[tuple[str, bytes]]:
    """Return ``[(media_type, png_bytes), ...]`` for every image in *result*.

    Scans the known single-image keys plus any dicts inside ``images``.
    Non-base64 values are skipped silently (a textual field that merely looks
    image-shaped will fail to decode and be ignored).
    """
    out: list[tuple[str, bytes]] = []
    for key in _SINGLE_IMAGE_KEYS:
        png = _decode_png(result.get(key, "")) if isinstance(result, dict) else None
        if png is not None:
            out.append(("image/png", png))
    imgs = result.get(_IMAGE_LIST_KEY) if isinstance(result, dict) else None
    if isinstance(imgs, list):
        for item in imgs:
            if not isinstance(item, dict):
                continue
            for key in _SINGLE_IMAGE_KEYS:
                png = _decode_png(item.get(key, ""))
                if png is not None:
                    out.append(("image/png", png))
                    break
    return out


def _strip_image_b64(obj: Any, placeholder: str) -> Any:
    """Recursively replace base64 image values with a short placeholder."""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k in _SINGLE_IMAGE_KEYS and isinstance(v, str) and _decode_png(v) is not None:
                cleaned[k] = placeholder
            else:
                cleaned[k] = _strip_image_b64(v, placeholder)
        return cleaned
    if isinstance(obj, list):
        return [_strip_image_b64(x, placeholder) for x in obj]
    return obj


def persist_images(
    images: list[tuple[str, bytes]], out_dir: Path, stem: str
) -> list[str]:
    """Write each PNG to ``out_dir/<stem>_<i>.png``; return relative paths.

    Paths are relative to ``out_dir.parent`` so a trajectory stored beside the
    images directory references them as e.g. ``images/step03_0.png``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rels: list[str] = []
    for i, (_mt, png) in enumerate(images):
        fname = f"{stem}_{i}.png"
        (out_dir / fname).write_bytes(png)
        rels.append(str(Path(out_dir.name) / fname))
    return rels


def build_tool_result_block(
    tool_use_id: str,
    result: dict,
    images: list[tuple[str, bytes]],
    image_paths: list[str],
) -> dict:
    """Build an Anthropic ``tool_result`` block for *result*.

    The block's content is a list: a ``text`` part holding the JSON result
    with image base64 stripped to a placeholder, followed by one ``image``
    block per extracted PNG (so the model actually sees the pixels).
    """
    placeholder = "<image stripped — see image block / trajectory images[]>"
    compact = _strip_image_b64(result, placeholder)
    content: list[dict] = [
        {"type": "text", "text": json.dumps(compact, default=str)}
    ]
    for (media_type, png) in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(png).decode("ascii"),
                },
            }
        )
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }


def compact_result_for_trajectory(result: dict, image_paths: list[str]) -> dict:
    """Return *result* with image base64 replaced by the saved PNG path(s).

    Keeps the trajectory JSON small and human-readable while still recording
    exactly which file each step's image landed in.
    """
    placeholder = (
        f"<image saved: {', '.join(image_paths)}>" if image_paths else "<image>"
    )
    return _strip_image_b64(result, placeholder)
