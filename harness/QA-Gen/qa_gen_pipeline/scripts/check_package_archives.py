#!/usr/bin/env python3
"""Verify QA-Gen package archives contain only the intended runtime resources."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile


_REQUIRED_SUFFIXES = (
    "configs/schemas/candidate.schema.json",
    "configs/schemas/compiled_selection_batch_locks.schema.json",
    "configs/schemas/compiled_selection_batch_plan.schema.json",
    "configs/schemas/compiled_selection_batch_run.schema.json",
    "configs/schemas/compiled_selection_viewer_capability_mapping.schema.json",
    "configs/schemas/compiled_selection_viewer_capability_receipt.schema.json",
    "configs/schemas/scene_inventory.schema.json",
    "configs/schemas/target_spec.schema.json",
    "configs/schemas/measurement_request.schema.json",
    "configs/schemas/measurement_receipt.schema.json",
    "configs/schemas/measurement_private_sidecar.schema.json",
    "configs/schemas/measurement_verifier_approval.schema.json",
    "configs/schemas/v32_private_source_spec.schema.json",
    "configs/schemas/v32_sanitized_author_brief.schema.json",
    "configs/viewer-capabilities/compiled_selection_v32.yaml",
    "configs/sources/v32_bcm522_private_source_adapter.yaml",
    "configs/schemas/verification_result.schema.json",
    "configs/profiles/bio_chem_fixture_compile.yaml",
    "configs/profiles/medical_volumetric_geometry_draft.yaml",
    "knowledge/index.yaml",
    "references/QA-Gen/docs/Question-Rubric.md",
)
_FORBIDDEN_PARTS = frozenset({".qagen", ".pytest_cache", ".ruff_cache", "__pycache__", "private", "inputs"})
_FORBIDDEN_FILENAMES = frozenset({".env", "id_rsa", "credentials.json"})
_FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_FORBIDDEN_RESOURCE_SUFFIXES = (
    "configs/profiles/bio_chem_release.yaml",
    "configs/profiles/biology_workspace_registry_compile.yaml",
    "configs/profiles/chirality_workspace_knowledge_pilot.yaml",
    "configs/profiles/ev2_v103_seed_ready.yaml",
    "configs/pilots/chirality/stereochemistry_chirality.workspace.yaml",
    "configs/sources/ev2_seed_source_spec.yaml",
    "configs/sources/knowledge_sources.yaml",
)


def _archive_members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [entry.filename for entry in archive.infolist() if not entry.is_dir()]
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return [entry.name for entry in archive.getmembers() if entry.isfile()]
    raise ValueError(f"unsupported package archive: {path}")


def _resource_member_contents(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [
                (entry.filename, archive.read(entry))
                for entry in archive.infolist()
                if not entry.is_dir() and entry.filename.startswith("qagen/_resources/")
            ]
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [
                entry
                for entry in archive.getmembers()
                if entry.isfile()
                and any(marker in entry.name for marker in ("/configs/", "/knowledge/", "/references/"))
            ]
            contents: list[tuple[str, bytes]] = []
            for entry in members:
                handle = archive.extractfile(entry)
                if handle is not None:
                    contents.append((entry.name, handle.read()))
            return contents
    raise ValueError(f"unsupported package archive: {path}")


def _validate_archive(path: Path) -> list[str]:
    names = _archive_members(path)
    errors: list[str] = []
    for suffix in _REQUIRED_SUFFIXES:
        if not any(name.endswith(suffix) for name in names):
            errors.append(f"missing required packaged resource: {suffix}")
    for name in names:
        item = PurePosixPath(name)
        parts = set(item.parts)
        if parts & _FORBIDDEN_PARTS:
            errors.append(f"forbidden runtime/private path in archive: {name}")
        if item.name in _FORBIDDEN_FILENAMES or item.suffix.lower() in _FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden credential-like file in archive: {name}")
        if item.is_absolute() or ".." in item.parts:
            errors.append(f"unsafe archive member path: {name}")
        if any(name.endswith(suffix) for suffix in _FORBIDDEN_RESOURCE_SUFFIXES):
            errors.append(f"forbidden workspace/EV2 resource in archive: {name}")
    for name, content in _resource_member_contents(path):
        if b".qagen/private/" in content:
            errors.append(f"private workspace mapping in packaged resource: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args(argv)

    failures: list[str] = []
    for archive in args.archives:
        if not archive.is_file():
            failures.append(f"archive does not exist: {archive}")
            continue
        for error in _validate_archive(archive):
            failures.append(f"{archive}: {error}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("verified QA-Gen package archive resources and exclusions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
