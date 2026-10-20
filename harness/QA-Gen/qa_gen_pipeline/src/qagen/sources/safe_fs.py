"""Fail-closed snapshots of files below an explicitly trusted root."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import stat
import threading
from typing import Iterable, Iterator, Union


class SafeFilesystemError(ValueError):
    """Raised when a local-source path cannot be inspected safely."""


@dataclass(frozen=True)
class TrustedFile:
    """Immutable facts observed while inspecting one trusted local file."""

    path: Path
    sha256: str
    size: int


@dataclass(frozen=True)
class TrustedTreeFile:
    """One regular file captured through a no-follow file descriptor."""

    relative_path: str
    sha256: str
    size: int
    device: int
    inode: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    captured_bytes: bytes | None = None

    def inventory_entry(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "type": "file",
            "bytes": self.size,
            "sha256": f"sha256:{self.sha256}",
        }

    def stability_signature(self) -> tuple[object, ...]:
        return (
            self.relative_path,
            self.sha256,
            self.size,
            self.device,
            self.inode,
            self.mode,
            self.mtime_ns,
            self.ctime_ns,
        )


@dataclass(frozen=True)
class TrustedTreeDirectory:
    """Directory identity used to detect additions, removals, or replacement."""

    relative_path: str
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    def stability_signature(self) -> tuple[object, ...]:
        return (
            self.relative_path,
            self.device,
            self.inode,
            self.mode,
            self.size,
            self.mtime_ns,
            self.ctime_ns,
        )


@dataclass(frozen=True)
class TrustedTreeSnapshot:
    """Content and identity snapshot used for both inventory and parsing."""

    root: Path
    files: tuple[TrustedTreeFile, ...]
    directories: tuple[TrustedTreeDirectory, ...]
    ignored_names: tuple[str, ...]

    def inventory_entries(self) -> list[dict[str, object]]:
        return [item.inventory_entry() for item in self.files]

    def file(self, relative_path: Union[str, Path]) -> TrustedTreeFile:
        canonical = "/".join(_relative_parts(relative_path))
        for item in self.files:
            if item.relative_path == canonical:
                return item
        raise SafeFilesystemError(f"trusted source is missing from snapshot: {canonical}")

    def captured(self, relative_path: Union[str, Path]) -> bytes:
        selected = self.file(relative_path)
        if selected.captured_bytes is None:
            raise SafeFilesystemError(f"trusted source was not captured: {selected.relative_path}")
        return selected.captured_bytes

    def stability_signature(self) -> tuple[tuple[object, ...], tuple[object, ...]]:
        return (
            tuple(item.stability_signature() for item in self.files),
            tuple(item.stability_signature() for item in self.directories),
        )


@dataclass(frozen=True)
class TrustedFileSetSnapshot:
    """A no-follow snapshot of an explicit, bounded set of source files.

    ``TrustedTreeSnapshot`` deliberately attests every file below a root.  That
    is appropriate for small seed packages, but it is impractical for a
    multi-gigabyte viewer bundle when the caller needs only a manifest, two
    indexes, and a known asset closure.  This companion type keeps the same
    descriptor/no-follow guarantees for an explicit file set without silently
    treating unrelated bundle files as trusted.
    """

    root: Path
    files: tuple[TrustedTreeFile, ...]

    def file(self, relative_path: Union[str, Path]) -> TrustedTreeFile:
        canonical = "/".join(_relative_parts(relative_path))
        for item in self.files:
            if item.relative_path == canonical:
                return item
        raise SafeFilesystemError(f"trusted source is missing from snapshot: {canonical}")

    def captured(self, relative_path: Union[str, Path]) -> bytes:
        selected = self.file(relative_path)
        if selected.captured_bytes is None:
            raise SafeFilesystemError(f"trusted source was not captured: {selected.relative_path}")
        return selected.captured_bytes

    def stability_signature(self) -> tuple[tuple[object, ...], ...]:
        return tuple(item.stability_signature() for item in self.files)


@dataclass(frozen=True)
class TrustedSourceRoot:
    """An open directory descriptor protected by process and advisory locks."""

    path: Path
    fd: int
    device: int
    inode: int


_PROCESS_LOCKS: dict[tuple[int, int], threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _relative_parts(relative_path: Union[str, Path]) -> tuple[str, ...]:
    if not isinstance(relative_path, (str, Path)):
        raise SafeFilesystemError("source path must be a relative string or Path")
    raw = str(relative_path)
    if not raw or raw.startswith(("/", "\\")) or "\\" in raw:
        raise SafeFilesystemError(f"source path is not canonical and relative: {raw!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SafeFilesystemError(f"source path is not canonical and relative: {raw!r}")
    return tuple(parts)


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _verify_root_identity(root: TrustedSourceRoot) -> None:
    try:
        descriptor_state = os.fstat(root.fd)
        path_state = os.stat(root.path, follow_symlinks=False)
    except OSError as exc:
        raise SafeFilesystemError(f"trusted root changed or became unavailable: {root.path}") from exc
    if not stat.S_ISDIR(descriptor_state.st_mode) or not stat.S_ISDIR(path_state.st_mode):
        raise SafeFilesystemError(f"trusted root is no longer a directory: {root.path}")
    if (descriptor_state.st_dev, descriptor_state.st_ino) != (root.device, root.inode):
        raise SafeFilesystemError(f"trusted root descriptor identity changed: {root.path}")
    if (path_state.st_dev, path_state.st_ino) != (root.device, root.inode):
        raise SafeFilesystemError(f"trusted root path was replaced during inspection: {root.path}")


@contextmanager
def lock_trusted_source_root(root: Union[str, Path]) -> Iterator[TrustedSourceRoot]:
    """Hold an intra-process and advisory cross-process lock for one source root."""
    root_path = Path(root).expanduser()
    if root_path.is_symlink():
        raise SafeFilesystemError(f"trusted root cannot be a symlink: {root_path}")
    try:
        resolved = root_path.resolve(strict=True)
        fd = os.open(resolved, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise SafeFilesystemError(f"cannot open trusted root directory: {root_path}") from exc
    process_lock: threading.Lock | None = None
    flock_acquired = False
    try:
        opened = os.fstat(fd)
        if not stat.S_ISDIR(opened.st_mode):
            raise SafeFilesystemError(f"trusted root is not a directory: {resolved}")
        key = (opened.st_dev, opened.st_ino)
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.Lock())
        process_lock.acquire()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            flock_acquired = True
        except OSError as exc:
            raise SafeFilesystemError(f"cannot lock trusted root directory: {resolved}") from exc
        locked = TrustedSourceRoot(path=resolved, fd=fd, device=opened.st_dev, inode=opened.st_ino)
        _verify_root_identity(locked)
        try:
            yield locked
        except BaseException:
            raise
        else:
            _verify_root_identity(locked)
    finally:
        if flock_acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if process_lock is not None and process_lock.locked():
            process_lock.release()
        os.close(fd)


def _read_regular_file(
    directory_fd: int,
    name: str,
    relative_path: str,
    observed: os.stat_result,
    *,
    capture: bool,
    max_capture_bytes: int,
) -> TrustedTreeFile:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    except OSError as exc:
        raise SafeFilesystemError(f"cannot open trusted source file: {relative_path}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or _stat_signature(before) != _stat_signature(observed):
            raise SafeFilesystemError(f"trusted source changed before it could be read: {relative_path}")
        if capture and before.st_size > max_capture_bytes:
            raise SafeFilesystemError(
                f"trusted source exceeds {max_capture_bytes} byte capture limit: {relative_path}"
            )
        hasher = sha256()
        captured = bytearray() if capture else None
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            hasher.update(chunk)
            if captured is not None:
                captured.extend(chunk)
        after = os.fstat(fd)
        try:
            path_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise SafeFilesystemError(f"trusted source disappeared while being read: {relative_path}") from exc
        if (
            _stat_signature(before) != _stat_signature(after)
            or _stat_signature(after) != _stat_signature(path_after)
            or total != after.st_size
        ):
            raise SafeFilesystemError(f"trusted source changed while being read: {relative_path}")
        return TrustedTreeFile(
            relative_path=relative_path,
            sha256=hasher.hexdigest(),
            size=total,
            device=after.st_dev,
            inode=after.st_ino,
            mode=after.st_mode,
            mtime_ns=after.st_mtime_ns,
            ctime_ns=after.st_ctime_ns,
            captured_bytes=bytes(captured) if captured is not None else None,
        )
    finally:
        os.close(fd)


def _read_explicit_file(
    root: TrustedSourceRoot,
    relative_path: str,
    *,
    capture: bool,
    max_capture_bytes: int,
    max_file_bytes: int,
) -> TrustedTreeFile:
    """Read one named file through no-follow directory descriptors only."""

    parts = _relative_parts(relative_path)
    directory_fd = os.dup(root.fd)
    try:
        for part in parts[:-1]:
            try:
                observed = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise SafeFilesystemError(f"trusted source directory is unavailable: {relative_path}") from exc
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISDIR(observed.st_mode):
                raise SafeFilesystemError(f"trusted source path is not a directory: {relative_path}")
            try:
                child_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                raise SafeFilesystemError(f"cannot open trusted source directory: {relative_path}") from exc
            try:
                opened = os.fstat(child_fd)
                if _stat_signature(opened) != _stat_signature(observed):
                    raise SafeFilesystemError(f"trusted source directory changed before traversal: {relative_path}")
            except BaseException:
                os.close(child_fd)
                raise
            os.close(directory_fd)
            directory_fd = child_fd

        name = parts[-1]
        try:
            observed_file = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise SafeFilesystemError(f"trusted source file is unavailable: {relative_path}") from exc
        if stat.S_ISLNK(observed_file.st_mode) or not stat.S_ISREG(observed_file.st_mode):
            raise SafeFilesystemError(f"trusted source is not a regular file: {relative_path}")
        if observed_file.st_size > max_file_bytes:
            raise SafeFilesystemError(f"trusted source exceeds {max_file_bytes} byte limit: {relative_path}")
        return _read_regular_file(
            directory_fd,
            name,
            relative_path,
            observed_file,
            capture=capture,
            max_capture_bytes=max_capture_bytes,
        )
    finally:
        os.close(directory_fd)


def _scan_directory(
    directory_fd: int,
    relative_parts: tuple[str, ...],
    *,
    capture_paths: set[str],
    ignored_names: set[str],
    max_capture_bytes: int,
    files: list[TrustedTreeFile],
    directories: list[TrustedTreeDirectory],
) -> None:
    before = os.fstat(directory_fd)
    if not stat.S_ISDIR(before.st_mode):
        raise SafeFilesystemError("trusted source tree contains a non-directory traversal root")
    try:
        with os.scandir(directory_fd) as iterator:
            names = sorted((entry.name for entry in iterator))
    except OSError as exc:
        relative = "/".join(relative_parts) or "."
        raise SafeFilesystemError(f"cannot enumerate trusted source directory: {relative}") from exc
    for name in names:
        relative_tuple = (*relative_parts, name)
        relative = "/".join(relative_tuple)
        try:
            observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise SafeFilesystemError(f"trusted source changed during enumeration: {relative}") from exc
        if stat.S_ISLNK(observed.st_mode):
            raise SafeFilesystemError(f"symlink is not allowed in trusted source tree: {relative}")
        if name in ignored_names:
            if not stat.S_ISREG(observed.st_mode):
                raise SafeFilesystemError(f"ignored source entry is not a regular file: {relative}")
            continue
        if stat.S_ISDIR(observed.st_mode):
            try:
                child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                raise SafeFilesystemError(f"cannot open trusted source directory: {relative}") from exc
            try:
                opened = os.fstat(child_fd)
                if _stat_signature(opened) != _stat_signature(observed):
                    raise SafeFilesystemError(f"trusted source directory changed before traversal: {relative}")
                _scan_directory(
                    child_fd,
                    relative_tuple,
                    capture_paths=capture_paths,
                    ignored_names=ignored_names,
                    max_capture_bytes=max_capture_bytes,
                    files=files,
                    directories=directories,
                )
                after = os.fstat(child_fd)
                path_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if _stat_signature(after) != _stat_signature(path_after):
                    raise SafeFilesystemError(f"trusted source directory was replaced during traversal: {relative}")
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(observed.st_mode):
            files.append(
                _read_regular_file(
                    directory_fd,
                    name,
                    relative,
                    observed,
                    capture=relative in capture_paths,
                    max_capture_bytes=max_capture_bytes,
                )
            )
        else:
            raise SafeFilesystemError(f"special file is not allowed in trusted source tree: {relative}")
    after = os.fstat(directory_fd)
    if _stat_signature(before) != _stat_signature(after):
        relative = "/".join(relative_parts) or "."
        raise SafeFilesystemError(f"trusted source directory changed during traversal: {relative}")
    directories.append(
        TrustedTreeDirectory(
            relative_path="/".join(relative_parts) or ".",
            device=after.st_dev,
            inode=after.st_ino,
            mode=after.st_mode,
            size=after.st_size,
            mtime_ns=after.st_mtime_ns,
            ctime_ns=after.st_ctime_ns,
        )
    )


def snapshot_trusted_tree(
    root: TrustedSourceRoot,
    *,
    capture_paths: Iterable[Union[str, Path]] = (),
    ignored_names: Iterable[str] = (".DS_Store",),
    max_capture_bytes: int = 64 * 1024 * 1024,
) -> TrustedTreeSnapshot:
    """Hash one stable tree and retain selected files from those exact reads."""
    if not isinstance(root, TrustedSourceRoot):
        raise SafeFilesystemError("snapshot requires a locked trusted source root")
    if not isinstance(max_capture_bytes, int) or max_capture_bytes < 0:
        raise SafeFilesystemError("max_capture_bytes must be a non-negative integer")
    canonical_captures = {"/".join(_relative_parts(path)) for path in capture_paths}
    ignored = tuple(sorted(set(ignored_names)))
    if any(not isinstance(name, str) or not name or "/" in name or "\\" in name for name in ignored):
        raise SafeFilesystemError("ignored source names must be simple path components")
    _verify_root_identity(root)
    files: list[TrustedTreeFile] = []
    directories: list[TrustedTreeDirectory] = []
    _scan_directory(
        root.fd,
        (),
        capture_paths=canonical_captures,
        ignored_names=set(ignored),
        max_capture_bytes=max_capture_bytes,
        files=files,
        directories=directories,
    )
    files.sort(key=lambda item: item.relative_path)
    directories.sort(key=lambda item: item.relative_path)
    observed_paths = {item.relative_path for item in files}
    missing = sorted(canonical_captures - observed_paths)
    if missing:
        raise SafeFilesystemError(f"captured source file is missing: {missing[0]}")
    _verify_root_identity(root)
    return TrustedTreeSnapshot(
        root=root.path,
        files=tuple(files),
        directories=tuple(directories),
        ignored_names=ignored,
    )


def verify_trusted_tree_snapshot(root: TrustedSourceRoot, snapshot: TrustedTreeSnapshot) -> None:
    """Re-hash the source and fail if content, identity, or tree shape changed."""
    if snapshot.root != root.path:
        raise SafeFilesystemError("trusted tree snapshot belongs to another source root")
    current = snapshot_trusted_tree(root, ignored_names=snapshot.ignored_names)
    if current.stability_signature() != snapshot.stability_signature():
        raise SafeFilesystemError("trusted source tree changed after its immutable snapshot was captured")


def snapshot_trusted_files(
    root: TrustedSourceRoot,
    relative_paths: Iterable[Union[str, Path]],
    *,
    capture_paths: Iterable[Union[str, Path]] = (),
    max_capture_bytes: int = 64 * 1024 * 1024,
    max_file_bytes: int = 128 * 1024 * 1024,
) -> TrustedFileSetSnapshot:
    """Hash an explicit set of files below a locked root without tree-wide I/O.

    The caller must keep ``root`` locked and call
    :func:`verify_trusted_file_set_snapshot` before publishing a result.  Both
    functions are intentionally explicit about the closed set of trusted
    files, so a huge bundle cannot turn an integrity check into an implicit
    full-tree scan.
    """

    if not isinstance(root, TrustedSourceRoot):
        raise SafeFilesystemError("explicit source snapshot requires a locked trusted root")
    if not isinstance(max_capture_bytes, int) or max_capture_bytes < 0:
        raise SafeFilesystemError("max_capture_bytes must be a non-negative integer")
    if not isinstance(max_file_bytes, int) or max_file_bytes < 0:
        raise SafeFilesystemError("max_file_bytes must be a non-negative integer")
    selected = sorted({"/".join(_relative_parts(path)) for path in relative_paths})
    if not selected:
        raise SafeFilesystemError("explicit source snapshot requires at least one path")
    captures = {"/".join(_relative_parts(path)) for path in capture_paths}
    if not captures.issubset(set(selected)):
        raise SafeFilesystemError("captured source path is not part of the explicit snapshot")
    _verify_root_identity(root)
    files = tuple(
        _read_explicit_file(
            root,
            relative_path,
            capture=relative_path in captures,
            max_capture_bytes=max_capture_bytes,
            max_file_bytes=max_file_bytes,
        )
        for relative_path in selected
    )
    _verify_root_identity(root)
    return TrustedFileSetSnapshot(root=root.path, files=files)


def verify_trusted_file_set_snapshot(root: TrustedSourceRoot, snapshot: TrustedFileSetSnapshot) -> None:
    """Re-read an explicit source set and reject any content or identity drift."""

    if not isinstance(snapshot, TrustedFileSetSnapshot) or snapshot.root != root.path:
        raise SafeFilesystemError("explicit trusted source snapshot belongs to another root")
    max_file_bytes = max(item.size for item in snapshot.files)
    current = snapshot_trusted_files(
        root,
        (item.relative_path for item in snapshot.files),
        max_file_bytes=max_file_bytes,
    )
    if current.stability_signature() != snapshot.stability_signature():
        raise SafeFilesystemError("trusted source files changed after their immutable snapshot was captured")


def inspect_trusted_file(root: Union[str, Path], relative_path: Union[str, Path], *, max_bytes: int) -> TrustedFile:
    """Inspect one regular file using the same locked, no-follow snapshot path."""
    if not isinstance(max_bytes, int) or max_bytes < 0:
        raise SafeFilesystemError("max_bytes must be a non-negative integer")
    canonical = "/".join(_relative_parts(relative_path))
    with lock_trusted_source_root(root) as locked:
        snapshot = snapshot_trusted_tree(
            locked,
            capture_paths=(canonical,),
            ignored_names=(),
            max_capture_bytes=max_bytes,
        )
        verify_trusted_tree_snapshot(locked, snapshot)
        selected = next((item for item in snapshot.files if item.relative_path == canonical), None)
        if selected is None:
            raise SafeFilesystemError(f"trusted source is not a regular file: {canonical}")
        if selected.size > max_bytes:
            raise SafeFilesystemError(f"trusted source exceeds {max_bytes} byte limit: {canonical}")
        return TrustedFile(path=(snapshot.root / canonical).resolve(), sha256=selected.sha256, size=selected.size)
