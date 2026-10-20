"""Regression: VisionChatClient must not depend on ChatClient's private attrs.

If ChatClient renames _base_url / _api_key / _timeout, the vision subclass
should keep working as long as it goes through _post_chat_request.
"""
from __future__ import annotations

import ast
from pathlib import Path


def test_vision_client_does_not_touch_chatclient_private_attrs():
    src = (
        Path(__file__).parent.parent
        / "sciencevision_taskgen"
        / "llm"
        / "_vision.py"
    ).read_text()
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self" and node.attr in {
                "_base_url",
                "_api_key",
                "_timeout",
            }:
                offenders.append(f"line {node.lineno}: self.{node.attr}")
    assert offenders == [], (
        "VisionChatClient must not reach into ChatClient private attrs; "
        "go through _post_chat_request instead. Offenders: " + str(offenders)
    )
