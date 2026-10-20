"""Lenient JSON parsing for LLM responses.

LLMs frequently wrap JSON output in markdown code fences (```json ... ```)
or add stray prose around the payload, even when the prompt explicitly
forbids it. This helper strips those wrappers before json.loads so that
otherwise-valid JSON does not trip a JSONDecodeError and force a fallback.
"""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL,
)


def parse_json_lenient(content: str) -> Any:
    """Parse JSON content tolerant of markdown fences and surrounding prose.

    Strategy (in order):
    1. Try ``json.loads`` directly on the input.
    2. Strip a surrounding ``` or ```json fence if present, retry.
    3. Slice from the first ``{`` or ``[`` to the matching final
       ``}`` or ``]`` and retry.

    Args:
        content: Raw text returned by the LLM.

    Returns:
        Parsed JSON value (dict, list, str, number, bool, or None).

    Raises:
        json.JSONDecodeError: if no strategy succeeds.
    """
    if not isinstance(content, str):
        raise json.JSONDecodeError("content is not a string", "", 0)

    text = content.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _FENCE_PATTERN.match(text)
    if fenced:
        inner = fenced.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            pass

    sliced = _slice_to_outer_brackets(text)
    if sliced is not None:
        return json.loads(sliced)

    return json.loads(text)


def _slice_to_outer_brackets(text: str) -> str | None:
    """Return the slice from the first '{' or '[' to its matching closer."""
    starts = [(i, c) for i, c in enumerate(text) if c in "{["]
    if not starts:
        return None
    first_idx, opener = starts[0]
    closer = "}" if opener == "{" else "]"
    last_idx = text.rfind(closer)
    if last_idx <= first_idx:
        return None
    return text[first_idx : last_idx + 1]
