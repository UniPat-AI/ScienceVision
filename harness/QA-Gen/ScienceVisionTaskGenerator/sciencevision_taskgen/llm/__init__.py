"""Public API for the sciencevision_taskgen.llm package.

Import from here; do not depend on sub-module paths directly.
"""
from ._parse import parse_json_lenient
from ._vision import VisionChatClient
from .client import ChatClient, ChatClientError, ChatResult
from .prompts import build_builder_messages, build_challenger_messages
from .routing import cross_model_for, load_credentials, resolve_model
from .usage import UsageLog, UsageRecord

__all__ = [
    "ChatClient",
    "ChatResult",
    "ChatClientError",
    "VisionChatClient",
    "resolve_model",
    "cross_model_for",
    "load_credentials",
    "UsageLog",
    "UsageRecord",
    "build_builder_messages",
    "build_challenger_messages",
    "parse_json_lenient",
]
