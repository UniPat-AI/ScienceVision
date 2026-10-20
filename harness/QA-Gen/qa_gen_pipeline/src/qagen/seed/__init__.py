"""Deprecated compatibility imports for the canonical :mod:`qagen.seeds` API."""

from .registry import (
    LegacySeedAPIError,
    SeedRegistry,
    accept_source_version,
    sync_seed_package,
)

__all__ = [
    "LegacySeedAPIError",
    "SeedRegistry",
    "accept_source_version",
    "sync_seed_package",
]
