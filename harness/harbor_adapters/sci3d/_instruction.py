"""Parse a Harbor task instruction into sci3d's (question, asset_path).

A Harbor task's ``instruction.md`` is free-form prose. sci3d needs two things:
a question and a 3D asset path. We support three ways to carry the asset,
checked in order:

1. A ``Asset:`` line in the instruction (case-insensitive), e.g.::

       Question: How many chambers does this heart model have?
       Asset: /data/heart.glb

2. A fenced ``sci3d`` metadata block holding JSON::

       ```sci3d
       {"asset_path": "/data/heart.glb"}
       ```

3. The ``--ak asset_path=...`` agent kwarg (passed in as ``fallback_asset``).

The question is the instruction with any recognized ``Asset:``/``Question:``
markers stripped, or the explicit ``Question:`` line if present. This keeps
the mapping forgiving: a plain instruction with no markers still works as the
question, with the asset coming from the agent kwarg.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_ASSET_LINE = re.compile(r"^\s*asset\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_QUESTION_LINE = re.compile(
    r"^\s*question\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
_SCI3D_BLOCK = re.compile(r"```sci3d\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ParsedInstruction:
    question: str
    asset_path: str | None


def parse_instruction(
    instruction: str, *, fallback_asset: str | None = None
) -> ParsedInstruction:
    text = instruction or ""

    asset_path: str | None = None

    # 2. fenced sci3d JSON block (most explicit)
    block = _SCI3D_BLOCK.search(text)
    if block:
        try:
            data = json.loads(block.group(1).strip())
            if isinstance(data, dict) and data.get("asset_path"):
                asset_path = str(data["asset_path"])
        except (json.JSONDecodeError, ValueError):
            pass
        # strip the block from the prose either way
        text = _SCI3D_BLOCK.sub("", text)

    # 1. Asset: line
    if asset_path is None:
        m = _ASSET_LINE.search(text)
        if m:
            asset_path = m.group(1).strip()

    # 3. fallback agent kwarg
    if asset_path is None:
        asset_path = fallback_asset

    # question: explicit Question: line, else the cleaned prose
    qm = _QUESTION_LINE.search(text)
    if qm:
        question = qm.group(1).strip()
    else:
        cleaned = _ASSET_LINE.sub("", text)
        question = cleaned.strip()

    return ParsedInstruction(question=question, asset_path=asset_path)
