"""Trusted local-source inspection primitives."""

from .safe_fs import (
    SafeFilesystemError,
    TrustedFile,
    TrustedFileSetSnapshot,
    TrustedSourceRoot,
    TrustedTreeSnapshot,
    inspect_trusted_file,
    lock_trusted_source_root,
    snapshot_trusted_files,
    snapshot_trusted_tree,
    verify_trusted_file_set_snapshot,
    verify_trusted_tree_snapshot,
)

__all__ = [
    "SafeFilesystemError",
    "TrustedFile",
    "TrustedFileSetSnapshot",
    "TrustedSourceRoot",
    "TrustedTreeSnapshot",
    "inspect_trusted_file",
    "lock_trusted_source_root",
    "snapshot_trusted_files",
    "snapshot_trusted_tree",
    "verify_trusted_file_set_snapshot",
    "verify_trusted_tree_snapshot",
]
