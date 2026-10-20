"""Approved local-pilot provider adapters and fail-closed runtime loading."""

from .http import HTTPProviderAdapter, ProviderAdapterError, ProviderPromptBudget
from .runtime import (
    ProviderRuntimeConfigError,
    ProviderRuntimeSettings,
    build_provider_adapters,
    diagnose_provider_runtime,
    load_provider_runtime,
    require_provider_multimodal_capability,
    require_secure_https_transport,
)

__all__ = [
    "HTTPProviderAdapter",
    "ProviderAdapterError",
    "ProviderPromptBudget",
    "ProviderRuntimeConfigError",
    "ProviderRuntimeSettings",
    "build_provider_adapters",
    "diagnose_provider_runtime",
    "load_provider_runtime",
    "require_provider_multimodal_capability",
    "require_secure_https_transport",
]
