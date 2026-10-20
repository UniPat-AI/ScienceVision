#!/usr/bin/env python3
"""Derive a small, fresh-viewer V32 pilot from the frozen 60-slot manifest.

The result is intentionally a new private selection manifest.  It is not a
rebind of any old viewer registry, receipt, or lock set, and it remains
generation-ineligible until a new controlled-viewer replay is attached.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
import stat
from typing import Any

from qagen.contracts import make_envelope, require_digest, verify_envelope
from qagen.errors import InputError
from qagen.paths import WorkspacePaths, require_within
from qagen.storage.atomic import atomic_write_json, read_json


_BATCH_KIND = "vnext_selection_batch_manifest"
_DERIVATION_KIND = "qagen_v32_five_slot_pilot_derivation"
_PILOT_LEAVES = (
    "biology.molecular_collision_check",
    "biology.docking_pose_plausibility",
    "chemistry.conformation_analysis",
    "chemistry.steric_accessibility",
    "medical.volumetric_target_geometry",
)


class DerivationError(InputError):
    """The frozen parent cannot safely produce this bounded pilot."""


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _private_envelope(paths: WorkspacePaths, path_value: str | Path) -> dict[str, Any]:
    raw = Path(path_value)
    if raw.is_symlink():
        raise DerivationError("parent selection batch is not a private regular file")
    try:
        path = require_within(paths.private, raw, "parent selection batch")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise DerivationError("parent selection batch is not a private regular file")
        value = read_json(path)
        if not isinstance(value, Mapping):
            raise DerivationError("parent selection batch is not an envelope")
        return verify_envelope(value, kind=_BATCH_KIND)
    except DerivationError:
        raise
    except (InputError, OSError) as exc:
        raise DerivationError("parent selection batch is invalid") from exc


def _row_asset_bytes(row: Mapping[str, Any]) -> int:
    assets = row.get("assets")
    if not isinstance(assets, list) or not assets:
        raise DerivationError("parent pilot row has no assets")
    total = 0
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise DerivationError("parent pilot asset is invalid")
        value = asset.get("bytes")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise DerivationError("parent pilot asset bytes are invalid")
        total += value
    return total


def _slot_digest(row: Mapping[str, Any]) -> str:
    slot = row.get("slot_ref")
    if not isinstance(slot, Mapping):
        raise DerivationError("parent pilot slot is invalid")
    try:
        return require_digest(slot.get("content_digest"), "parent pilot slot digest")
    except InputError as exc:
        raise DerivationError("parent pilot slot is invalid") from exc


def _selected_rows(parent: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = parent.get("body")
    if not isinstance(body, Mapping) or body.get("total") != 60 or body.get("generation_eligible") is not False:
        raise DerivationError("parent selection batch is not the frozen 60-slot V32 batch")
    rows = body.get("rows")
    if not isinstance(rows, list) or len(rows) != 60:
        raise DerivationError("parent selection batch rows are invalid")
    selected: list[dict[str, Any]] = []
    for leaf in _PILOT_LEAVES:
        candidates: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, Mapping) or raw.get("target_leaf") != leaf:
                continue
            if raw.get("mapping_status") != "mapped" or not isinstance(raw.get("generation_strategy"), str):
                raise DerivationError("parent pilot row is not mapped")
            candidates.append(dict(raw))
        if not candidates:
            raise DerivationError(f"parent selection has no row for {leaf}")
        # This is a deterministic operational selection rule, not a quality
        # judgment: minimize fresh browser loading cost, then break ties on
        # the immutable opaque slot digest.
        selected.append(min(candidates, key=lambda row: (_row_asset_bytes(row), _slot_digest(row))))
    if len({_slot_digest(row) for row in selected}) != len(_PILOT_LEAVES):
        raise DerivationError("pilot leaf selection contains duplicate slots")
    return selected


def derive_five_slot_pilot(paths: WorkspacePaths, *, parent_selection_batch: str | Path) -> dict[str, Any]:
    """Persist a deterministic, private five-leaf pilot manifest and receipt."""

    paths.ensure()
    parent = _private_envelope(paths, parent_selection_batch)
    parent_body = parent["body"]
    assert isinstance(parent_body, Mapping)
    rows = _selected_rows(parent)
    blockers = parent_body.get("generation_blockers")
    if not isinstance(blockers, list) or not all(isinstance(item, str) and item for item in blockers):
        raise DerivationError("parent selection generation blockers are invalid")
    body = {
        "source_selection": dict(parent_body["source_selection"]),
        "taxonomy_mapping": dict(parent_body["taxonomy_mapping"]),
        "total": len(rows),
        "mapping_coverage": {
            "source_leaf_count": len(_PILOT_LEAVES),
            "mapped_leaf_count": len(_PILOT_LEAVES),
            "unmapped_source_leaves": [],
        },
        "generation_eligible": False,
        "generation_blockers": sorted(set((*blockers, "derived_pilot_requires_fresh_viewer_attestation"))),
        "rows": rows,
    }
    manifest = make_envelope(
        kind=_BATCH_KIND,
        identifier=f"v32-five-slot-pilot-{_short(parent['content_digest'])}",
        status="derived_pilot_pending_fresh_viewer_attestation",
        body=body,
    )
    output_dir = paths.selection_batches / "derived-pilots"
    output = output_dir / f"{_short(parent['content_digest'])}.{_short(manifest['content_digest'])}.vnext-batch.json"
    if not output.exists():
        atomic_write_json(output, manifest, private=True)

    derivation = make_envelope(
        kind=_DERIVATION_KIND,
        identifier=f"v32-five-slot-pilot-derivation-{_short(manifest['content_digest'])}",
        status="derived_pending_fresh_viewer_attestation",
        body={
            "parent_selection_batch_digest": parent["content_digest"],
            "derived_selection_batch_digest": manifest["content_digest"],
            "selection_rule": "minimum_declared_asset_bytes_then_opaque_slot_digest_per_target_leaf",
            "target_leaves": list(_PILOT_LEAVES),
            "slot_digests": [_slot_digest(row) for row in rows],
            "fresh_viewer_evidence_required": True,
            "provider_execution_eligible": False,
            "release_eligible": False,
        },
    )
    derivation_dir = paths.private / "selection-derivations"
    derivation_path = derivation_dir / f"{_short(manifest['content_digest'])}.{_short(derivation['content_digest'])}.derivation.json"
    if not derivation_path.exists():
        atomic_write_json(derivation_path, derivation, private=True)
    return {
        "selection_batch_path": str(output),
        "selection_batch_digest": manifest["content_digest"],
        "derivation_path": str(derivation_path),
        "derivation_digest": derivation["content_digest"],
        "slot_count": len(rows),
        "target_leaves": list(_PILOT_LEAVES),
        "provider_execution_eligible": False,
        "release_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--parent-selection-batch", required=True, type=Path)
    args = parser.parse_args()
    result = derive_five_slot_pilot(
        WorkspacePaths.from_value(args.workspace),
        parent_selection_batch=args.parent_selection_batch,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
