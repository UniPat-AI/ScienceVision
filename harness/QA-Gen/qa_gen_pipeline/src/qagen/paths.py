"""Workspace layout and path containment helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from .errors import InputError


def safe_component(value: str, field: str = "path component") -> str:
    if not value or value in {".", ".."} or any(char in value for char in "/\\\x00"):
        raise InputError(f"unsafe {field}: {value!r}")
    return value


def contained_path(root: Path, relative: str | Path) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise InputError(f"path escapes workspace: {relative}") from exc
    return candidate


def require_within(root: Path, candidate: str | Path, field: str = "path") -> Path:
    """Resolve an existing caller-supplied path and require its ownership root."""
    root_resolved = root.resolve()
    resolved = Path(candidate).expanduser().resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise InputError(f"{field} must remain below {root_resolved}") from exc
    return resolved


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @classmethod
    def from_value(cls, value: str | Path) -> "WorkspacePaths":
        root = Path(value).expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise InputError(f"workspace is not a directory: {root}")
        return cls(root=root)

    @property
    def qagen(self) -> Path:
        return self.root / ".qagen"

    @property
    def private(self) -> Path:
        return self.qagen / "private"

    @property
    def snapshots(self) -> Path:
        return self.private / "snapshots"

    @property
    def catalog(self) -> Path:
        return self.private / "catalog"

    @property
    def cards(self) -> Path:
        return self.private / "cards"

    @property
    def receipts(self) -> Path:
        return self.private / "receipts"

    @property
    def knowledge_drafts(self) -> Path:
        return self.private / "knowledge-drafts"

    @property
    def viewer_profiles(self) -> Path:
        return self.private / "viewer-profiles"

    @property
    def compiled_configs(self) -> Path:
        return self.private / "compiled-configs"

    @property
    def plans(self) -> Path:
        return self.private / "plans"

    @property
    def selection_imports(self) -> Path:
        return self.private / "selections" / "imports"

    @property
    def selection_slots(self) -> Path:
        return self.private / "selections" / "slots"

    @property
    def selection_batches(self) -> Path:
        return self.private / "selections" / "batches"

    @property
    def v32_private_sources(self) -> Path:
        """Private, attested source-side records for the frozen V32 cohort."""

        return self.private / "v32-private-source"

    @property
    def plan_receipts(self) -> Path:
        return self.receipts / "plans"

    @property
    def review_receipts(self) -> Path:
        return self.receipts / "reviews"

    @property
    def runs(self) -> Path:
        return self.qagen / "runs"

    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge"

    @property
    def events(self) -> Path:
        return self.private / "events.jsonl"

    @property
    def usage(self) -> Path:
        return self.private / "usage.jsonl"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        _mkdir_private(self.qagen)
        _mkdir_private(self.private)
        for path in (
            self.snapshots / "seed",
            self.snapshots / "knowledge" / "raw",
            self.catalog,
            self.cards,
            self.receipts / "source",
            self.receipts / "knowledge",
            self.plan_receipts,
            self.review_receipts,
            self.knowledge_drafts,
            self.viewer_profiles,
            self.compiled_configs,
            self.plans,
            self.selection_imports,
            self.selection_slots,
            self.selection_batches,
            self.v32_private_sources / "imports",
            self.v32_private_sources / "records",
            self.v32_private_sources / "author-briefs",
            self.v32_private_sources / "truth",
            self.v32_private_sources / "handoffs",
            self.runs,
        ):
            _mkdir_private(path)
        for path in (
            self.knowledge / "sources" / "expert",
            self.knowledge / "sources" / "taxonomy",
            self.knowledge / "sources" / "feedback",
            self.knowledge / "seed-lessons",
            self.knowledge / "compiled",
        ):
            path.mkdir(parents=True, exist_ok=True)
