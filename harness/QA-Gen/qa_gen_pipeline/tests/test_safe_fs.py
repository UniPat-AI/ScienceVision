from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path
import threading

import pytest

from qagen.sources.safe_fs import (
    SafeFilesystemError,
    inspect_trusted_file,
    lock_trusted_source_root,
    snapshot_trusted_tree,
    verify_trusted_tree_snapshot,
)


def test_inspect_trusted_file_returns_digest_size_and_canonical_path(tmp_path: Path) -> None:
    root = tmp_path / "source"
    payload = b"seed manifest contents\n"
    target = root / "nested" / "MANIFEST.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)

    inspected = inspect_trusted_file(root, "nested/MANIFEST.json", max_bytes=1024)

    assert inspected.path == target.resolve()
    assert inspected.sha256 == hashlib.sha256(payload).hexdigest()
    assert inspected.size == len(payload)


@pytest.mark.parametrize("unsafe_path", ["../outside.json", "/etc/passwd", "nested/../../x", "", "./x"])
def test_inspect_trusted_file_rejects_noncanonical_or_escaping_paths(
    tmp_path: Path, unsafe_path: str
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "x").write_text("ok", encoding="utf-8")

    with pytest.raises(SafeFilesystemError):
        inspect_trusted_file(root, unsafe_path, max_bytes=1024)


def test_inspect_trusted_file_rejects_symlink_in_source_tree(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    link = root / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable on this filesystem: {exc}")

    with pytest.raises(SafeFilesystemError):
        inspect_trusted_file(root, "linked.json", max_bytes=1024)


def test_inspect_trusted_file_fails_closed_on_size_limit(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "large.bin").write_bytes(b"x" * 33)

    with pytest.raises(SafeFilesystemError):
        inspect_trusted_file(root, "large.bin", max_bytes=32)


def test_verify_trusted_tree_snapshot_rejects_equal_length_rewrite(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "role.json"
    target.write_bytes(b'{"value":"old"}\n')

    with lock_trusted_source_root(root) as locked:
        snapshot = snapshot_trusted_tree(locked, capture_paths=("role.json",))
        target.write_bytes(b'{"value":"new"}\n')

        assert snapshot.captured("role.json") == b'{"value":"old"}\n'
        with pytest.raises(SafeFilesystemError, match="changed after its immutable snapshot"):
            verify_trusted_tree_snapshot(locked, snapshot)


def test_trusted_source_root_lock_serializes_threads(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    first_acquired = threading.Event()
    release_first = threading.Event()
    second_attempting = threading.Event()
    second_acquired = threading.Event()

    def hold_first_lock() -> None:
        with lock_trusted_source_root(root):
            first_acquired.set()
            assert release_first.wait(timeout=5)

    def acquire_second_lock() -> None:
        second_attempting.set()
        with lock_trusted_source_root(root):
            second_acquired.set()

    first = threading.Thread(target=hold_first_lock)
    second = threading.Thread(target=acquire_second_lock)
    first.start()
    assert first_acquired.wait(timeout=5)
    second.start()
    assert second_attempting.wait(timeout=5)

    assert not second_acquired.wait(timeout=0.1)
    release_first.set()
    assert second_acquired.wait(timeout=5)

    first.join(timeout=5)
    second.join(timeout=5)
    assert not first.is_alive()
    assert not second.is_alive()


def test_trusted_source_root_lock_serializes_processes(tmp_path: Path) -> None:
    try:
        context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - supported local CI platforms provide fork.
        pytest.skip("fork process context is unavailable")
    root = tmp_path / "source"
    root.mkdir()
    first_acquired = context.Event()
    release_first = context.Event()
    second_acquired = context.Event()

    def hold_first_lock() -> None:
        with lock_trusted_source_root(root):
            first_acquired.set()
            if not release_first.wait(timeout=5):
                raise RuntimeError("timed out waiting to release first source lock")

    def acquire_second_lock() -> None:
        with lock_trusted_source_root(root):
            second_acquired.set()

    first = context.Process(target=hold_first_lock)
    first.start()
    assert first_acquired.wait(timeout=5)
    second = context.Process(target=acquire_second_lock)
    second.start()

    assert not second_acquired.wait(timeout=0.1)
    release_first.set()
    assert second_acquired.wait(timeout=5)

    first.join(timeout=5)
    second.join(timeout=5)
    assert first.exitcode == 0
    assert second.exitcode == 0
