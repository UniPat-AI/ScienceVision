"""Locate immutable configuration resources in source and installed packages.

The QA-Gen control plane needs checked-in schemas and profiles at runtime.
Source checkouts keep them beside ``src/`` for authoring, while wheels carry
the same reviewed tree below ``qagen/_resources``.  Callers should use this
module instead of deriving paths from their own ``__file__`` location.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path, PurePosixPath


class PackageResourceError(RuntimeError):
    """A required immutable resource is unavailable from this installation."""


_RESOURCE_ROOT = "_resources"
_ALLOWED_TREES = frozenset({"configs", "knowledge", "references"})


def _source_checkout_root() -> Path | None:
    """Return the project root only when this module is in its source layout."""
    module_file = Path(__file__).resolve()
    try:
        candidate = module_file.parents[2]
    except IndexError:  # pragma: no cover - normal modules always have enough parents
        return None
    if not (candidate / "pyproject.toml").is_file():
        return None
    if module_file != (candidate / "src" / "qagen" / "resource_paths.py").resolve():
        return None
    return candidate


@lru_cache(maxsize=None)
def resource_tree(name: str) -> Path:
    """Return one immutable resource directory.

    A source checkout deliberately remains the preferred location so config
    authors can run the pipeline before building a wheel.  A regular wheel
    installation has no adjacent ``configs`` directory and resolves through
    ``importlib.resources`` instead.  The latter is intentionally fail-closed
    for non-filesystem import loaders because the compiler requires stable
    paths for digest locks and relative-reference containment checks.
    """
    if name not in _ALLOWED_TREES:
        raise PackageResourceError(f"unknown QA-Gen resource tree: {name}")

    source_root = _source_checkout_root()
    if source_root is not None:
        source_path = source_root / name
        if source_path.is_dir():
            return source_path.resolve()

    traversable = resources.files("qagen").joinpath(_RESOURCE_ROOT, name)
    if not traversable.is_dir():
        raise PackageResourceError(f"required packaged QA-Gen resource tree is unavailable: {name}")
    try:
        path = Path(traversable)
    except TypeError as exc:  # pragma: no cover - wheels install as filesystem packages
        raise PackageResourceError(
            "QA-Gen resources require a filesystem-backed wheel installation"
        ) from exc
    if not path.is_dir():
        raise PackageResourceError(f"required packaged QA-Gen resource tree is unavailable: {name}")
    return path.resolve()


def _resource_file(tree: str, *parts: str) -> Path:
    if not parts:
        raise PackageResourceError("resource file path must not be empty")
    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise PackageResourceError(f"unsafe QA-Gen resource path: {relative}")
    root = resource_tree(tree)
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PackageResourceError(f"QA-Gen resource path escapes {tree}: {relative}") from exc
    if not path.is_file():
        raise PackageResourceError(f"required QA-Gen resource file is unavailable: {tree}/{relative}")
    return path


def config_root() -> Path:
    """Return the root containing bundled QA-Gen profiles and schemas."""
    return resource_tree("configs")


def config_path(*parts: str) -> Path:
    """Return one checked-in configuration file from the active resource tree."""
    return _resource_file("configs", *parts)


def knowledge_root() -> Path:
    """Return the root containing bundled local-only knowledge fixtures."""
    return resource_tree("knowledge")


def knowledge_path(*parts: str) -> Path:
    """Return one checked-in local-only knowledge fixture."""
    return _resource_file("knowledge", *parts)


def repository_reference_path(repo_relative: str) -> Path:
    """Resolve one explicitly mirrored, digest-locked repository reference.

    Only references copied into the package's reviewed ``references`` tree are
    available here.  This is intentionally not a generic repository fallback:
    unmirrored paths remain unavailable in an installed wheel.
    """
    relative = PurePosixPath(repo_relative)
    if not repo_relative or relative.is_absolute() or ".." in relative.parts:
        raise PackageResourceError(f"unsafe QA-Gen repository reference: {repo_relative!r}")
    return _resource_file("references", *relative.parts)
