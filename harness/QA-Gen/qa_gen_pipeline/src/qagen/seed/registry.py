"""Compatibility boundary for the removed legacy Seed Registry API.

The canonical implementation lives in :mod:`qagen.seeds`.  The former
``SeedRegistry`` object accepted caller-constructed receipts and maintained a
second, weaker source-validation path.  It cannot be adapted safely because
its constructor has no workspace or authorized SourceSpec.
"""

from __future__ import annotations

from typing import NoReturn

from ..seeds import accept_source_version, sync_seed_package


class LegacySeedAPIError(RuntimeError):
    """Raised when a caller tries to use the removed object-style API."""


class SeedRegistry:
    """Removed object API retained only to provide an actionable failure."""

    def __new__(cls, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise LegacySeedAPIError(
            "qagen.seed.SeedRegistry was removed because it bypassed the authorized "
            "SourceSpec and persistent receipt checks; migrate to "
            "qagen.seeds.sync_seed_package and qagen.seeds.accept_source_version"
        )


__all__ = [
    "LegacySeedAPIError",
    "SeedRegistry",
    "accept_source_version",
    "sync_seed_package",
]
