from __future__ import annotations

from pathlib import Path

import pytest

import qagen.seed.registry as legacy_seed_registry
from qagen.seed import LegacySeedAPIError, SeedRegistry
from qagen.seed import accept_source_version as compatibility_accept_source_version
from qagen.seed import sync_seed_package as compatibility_sync_seed_package
from qagen.seeds import accept_source_version, sync_seed_package


def test_legacy_seed_namespace_forwards_only_to_canonical_registry() -> None:
    assert compatibility_sync_seed_package is sync_seed_package
    assert compatibility_accept_source_version is accept_source_version


def test_legacy_seed_registry_constructor_fails_with_migration_guidance(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        LegacySeedAPIError,
        match=r"qagen\.seeds\.sync_seed_package.*qagen\.seeds\.accept_source_version",
    ):
        SeedRegistry(
            private_root=tmp_path / "private",
            knowledge_root=tmp_path / "knowledge",
        )


@pytest.mark.parametrize(
    "removed_name",
    [
        "SeedReceiptMismatchError",
        "SeedSyncResult",
        "SeedVersionReceipt",
        "SeedVersionRequest",
    ],
)
def test_constructible_legacy_receipt_api_is_not_exposed(removed_name: str) -> None:
    assert not hasattr(legacy_seed_registry, removed_name)
