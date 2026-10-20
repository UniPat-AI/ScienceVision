"""Approved, non-secret provider contracts for the QA-Gen pilot."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


UNIV_CREDENTIAL_ENV = "QAGEN_UNIV_API_KEY"
UNIV_BASE_URL_ENV = "QAGEN_UNIV_BASE_URL"
UNIV_ENDPOINT_APPROVAL_PATH_ENV = "QAGEN_UNIV_ENDPOINT_APPROVAL_PATH"
MEASUREMENT_REPLAY_TEXT_ONLY_PROFILE = "measurement_replay_text_only"
MEASUREMENT_REPLAY_TEXT_ONLY_ROLES = frozenset(
    {"brief_builder", "author", "truth_builder", "verifier"}
)


@dataclass(frozen=True)
class ProviderContract:
    provider: str
    model: str
    path: str
    wire_protocol: str
    thinking: str
    supports_image_input: bool
    supports_data_url: bool


PROVIDER_CONTRACTS: Mapping[str, ProviderContract] = MappingProxyType(
    {
        "anthropic": ProviderContract(
            provider="anthropic",
            model="claude-opus-5",
            path="/v1/messages",
            wire_protocol="anthropic_messages",
            thinking="not_applicable",
            supports_image_input=True,
            supports_data_url=True,
        ),
        "deepseek": ProviderContract(
            provider="deepseek",
            model="deepseek-v4-pro",
            path="/v1/chat/completions",
            wire_protocol="openai_chat_completions",
            thinking="disabled",
            # This is the provider's maximum protocol surface. A pinned
            # runtime may tighten either flag after a compatibility probe.
            supports_image_input=True,
            supports_data_url=True,
        ),
        "openai": ProviderContract(
            provider="openai",
            model="gpt-5.6-sol",
            path="/v1/chat/completions",
            wire_protocol="openai_chat_completions",
            thinking="not_applicable",
            supports_image_input=True,
            supports_data_url=True,
        ),
    }
)


ROLE_PROVIDER_MODELS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "brief_builder": ("anthropic", "claude-opus-5"),
        "author": ("deepseek", "deepseek-v4-pro"),
        "truth_builder": ("deepseek", "deepseek-v4-pro"),
        "evidence_worker": ("deepseek", "deepseek-v4-pro"),
        "rewriter": ("deepseek", "deepseek-v4-pro"),
        "verifier": ("openai", "gpt-5.6-sol"),
    }
)
