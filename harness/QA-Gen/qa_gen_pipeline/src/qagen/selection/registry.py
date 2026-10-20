"""Fail-closed import of frozen evaluation selections into QA-Gen vNext.

The historical scale-v3.2 selection is useful as an asset/sample inventory,
but it is not itself a vNext plan.  In particular, its ``raw_compute`` rows
must remain labelled as such until a later, separately reviewed vNext policy
can establish viewer-native answerability.  This module therefore has two
small stages:

* ``import_selection`` attests the exact source file and every referenced
  primary/mask asset, then persists one private immutable source slot per row.
* ``compile_selection`` consumes those slots plus an approved taxonomy map and
  emits a private batch manifest.  It does not create a viewer, call a model,
  or claim that the batch is generation-ready.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft7Validator
import yaml

from ..contracts import digest, digest_bytes, make_envelope, verify_envelope
from ..errors import InputError, QuarantinedError
from ..paths import WorkspacePaths, contained_path, require_within, safe_component
from ..registry_contracts import require_workspace_relative_path
from ..resource_paths import PackageResourceError, config_path
from ..sources.safe_fs import (
    SafeFilesystemError,
    lock_trusted_source_root,
    snapshot_trusted_tree,
    verify_trusted_tree_snapshot,
)
from ..storage.atomic import atomic_write_json, hash_file, read_json
from ..storage.ledger import append_event


_MAX_SELECTION_BYTES = 4 * 1024 * 1024
_MAX_MAPPING_BYTES = 2 * 1024 * 1024
_MAX_SOURCE_SPEC_BYTES = 2 * 1024 * 1024
_RAW_COMPUTE = "raw_compute"
_VIEWER_MEASUREMENT = "viewer_measurement"
_PRESERVE_INTENT = "preserve_intent"
_REAUTHOR_ON_ASSET = "reauthor_on_asset"
_GENERATION_STRATEGY_BY_SOURCE_ACCESS_TRACK = {
    _RAW_COMPUTE: _REAUTHOR_ON_ASSET,
    _VIEWER_MEASUREMENT: _PRESERVE_INTENT,
}


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _schema(name: str) -> dict[str, Any]:
    path = config_path("schemas", name)
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise InputError(f"QA-Gen schema {name} must be an object")
    return dict(value)


def _schema_validate(value: Any, schema_name: str, label: str) -> None:
    errors = sorted(Draft7Validator(_schema(schema_name)).iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise InputError(f"{label} schema validation failed at {location}: {error.message}")


def _read_document(value: str | Path, *, label: str, max_bytes: int) -> tuple[Path, dict[str, Any], str]:
    raw_path = Path(value).expanduser()
    if raw_path.is_symlink():
        raise InputError(f"{label} cannot be a symlink: {raw_path}")
    try:
        path = raw_path.resolve(strict=True)
    except OSError as exc:
        raise InputError(f"cannot resolve {label}: {raw_path}") from exc
    if not path.is_file():
        raise InputError(f"{label} is not a regular file: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read {label}: {path}") from exc
    if len(content) > max_bytes:
        raise InputError(f"{label} exceeds {max_bytes} byte limit: {path}")
    try:
        if path.suffix.lower() == ".json":
            parsed = json.loads(content.decode("utf-8"))
        else:
            parsed = yaml.safe_load(content)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot parse {label}: {path}: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise InputError(f"{label} must be an object: {path}")
    return path, dict(parsed), digest_bytes(content)


def _raw_id_digest(question_ids: list[str]) -> str:
    return sha256("\n".join(sorted(question_ids)).encode("utf-8")).hexdigest()


def _canonical_asset_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputError(f"{field} must be a non-empty relative path")
    if "\\" in value:
        raise InputError(f"{field} must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise InputError(f"{field} must be a canonical relative path")
    return path.as_posix()


def _source_leaf(row: Mapping[str, Any]) -> str:
    return f"{row['domain']}.{row['sub_domain']}"


def _assets_for_row(row: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    primary = row["primary_asset"]
    if not isinstance(primary, Mapping):  # covered by schema; retained for API callers.
        raise InputError(f"{row['question_id']}: primary_asset must be an object")
    assets: list[tuple[str, dict[str, Any]]] = [("primary", dict(primary))]
    mask = row["mask_asset"]
    if mask is not None:
        if not isinstance(mask, Mapping):
            raise InputError(f"{row['question_id']}: mask_asset must be an object or null")
        mask_dict = dict(mask)
        selection = mask_dict.get("mask_selection")
        mask_id = mask_dict.get("mask_id")
        if selection != "label_index":
            raise InputError(
                f"{row['question_id']}: mask_selection must be explicit label_index; merged non-zero overlays are forbidden"
            )
        if not isinstance(mask_id, int) or isinstance(mask_id, bool) or mask_id < 0:
            raise InputError(f"{row['question_id']}: label_index mask requires a non-negative integer mask_id")
        assets.append(("mask", mask_dict))
    return assets


def _validate_track(row: Mapping[str, Any]) -> None:
    question_id = str(row["question_id"])
    track = row["access_track"]
    has_contract = row["has_compute_contract"]
    packages = row["compute_packages"]
    if track == _RAW_COMPUTE:
        if has_contract is not True or not packages:
            raise InputError(f"{question_id}: raw_compute must retain a compute contract and package list")
    elif track == _VIEWER_MEASUREMENT:
        if has_contract is not False or packages:
            raise InputError(f"{question_id}: viewer_measurement cannot carry a compute contract or packages")
    else:  # schema guards this; defensive for callers that bypass it.
        raise InputError(f"{question_id}: unsupported access_track {track!r}")


def _generation_strategy_policy(mapping: Mapping[str, Any]) -> dict[str, str]:
    """Return the approved source-track policy or fail before batch creation.

    Source access tracks describe historical provenance.  They are not solver
    permissions and must not be confused with the route a new vNext task will
    take.  The approved mapping carries the small, explicit policy that turns
    each source track into an executable generation strategy.
    """

    value = mapping.get("generation_strategy_by_source_access_track")
    if not isinstance(value, Mapping):
        raise InputError("selection taxonomy mapping is missing generation_strategy_by_source_access_track")
    policy = {str(track): strategy for track, strategy in value.items()}
    expected_tracks = set(_GENERATION_STRATEGY_BY_SOURCE_ACCESS_TRACK)
    if set(policy) != expected_tracks:
        raise InputError(
            "selection taxonomy mapping generation strategy policy must define exactly "
            "raw_compute and viewer_measurement"
        )
    for source_track, expected_strategy in _GENERATION_STRATEGY_BY_SOURCE_ACCESS_TRACK.items():
        strategy = policy[source_track]
        if strategy != expected_strategy:
            raise InputError(
                f"selection taxonomy mapping requires {source_track} "
                f"strategy={expected_strategy}"
            )
    return {track: str(policy[track]) for track in sorted(policy)}


def _validate_selection_integrity(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    domains = selection["domains"]
    if not isinstance(domains, Mapping):
        raise InputError("selection domains must be an object")
    all_rows: list[dict[str, Any]] = []
    global_ids: list[str] = []
    seen_ids: set[str] = set()
    for domain, raw_block in sorted(domains.items()):
        if not isinstance(raw_block, Mapping):
            raise InputError(f"selection domain {domain} must be an object")
        block = dict(raw_block)
        rows = block["rows"]
        if not isinstance(rows, list):
            raise InputError(f"selection domain {domain} rows must be an array")
        if block["count"] != len(rows):
            raise InputError(f"selection domain {domain} count does not match rows")
        if block["count"] != selection["per_domain"]:
            raise InputError(f"selection domain {domain} count does not match per_domain")

        question_ids: list[str] = []
        by_subdomain: dict[str, list[str]] = {}
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                raise InputError(f"selection domain {domain} has a non-object row")
            row = dict(raw_row)
            if row["domain"] != domain:
                raise InputError(f"{row['question_id']}: row domain does not match selection block {domain}")
            question_id = str(row["question_id"])
            if question_id in seen_ids:
                raise InputError(f"selection has duplicate question_id: {question_id}")
            seen_ids.add(question_id)
            question_ids.append(question_id)
            global_ids.append(question_id)
            by_subdomain.setdefault(str(row["sub_domain"]), []).append(question_id)
            _validate_track(row)
            row_assets = _assets_for_row(row)
            if row["viewer"] == "niivue_deployed" and not any(role == "mask" for role, _ in row_assets):
                raise InputError(f"{question_id}: niivue_deployed selection row requires a typed mask asset")
            for role, asset in row_assets:
                asset_path = _canonical_asset_path(asset.get("path"), f"{question_id} {role} asset path")
                if asset_path != asset["path"]:
                    raise InputError(f"{question_id}: {role} asset path is not canonical")
            all_rows.append(row)

        expected_ids = sorted(question_ids)
        if block["question_ids"] != expected_ids:
            raise InputError(f"selection domain {domain} question_ids do not match rows")
        if block["id_sha256"] != _raw_id_digest(question_ids):
            raise InputError(f"selection domain {domain} id_sha256 does not match question_ids")
        expected_by_subdomain = {key: sorted(value) for key, value in sorted(by_subdomain.items())}
        if block["by_sub_domain"] != expected_by_subdomain:
            raise InputError(f"selection domain {domain} by_sub_domain does not match rows")
        quota = block["quota"]
        if set(quota) != set(expected_by_subdomain) or sum(quota.values()) != len(rows):
            raise InputError(f"selection domain {domain} quota does not match selected sub-domains")
        if set(block["sub_domain_population"]) != set(expected_by_subdomain):
            raise InputError(f"selection domain {domain} sub_domain_population does not match selected sub-domains")

    if selection["total"] != len(all_rows):
        raise InputError("selection total does not match rows")
    if selection["id_sha256"] != _raw_id_digest(global_ids):
        raise InputError("selection global id_sha256 does not match question_ids")
    return sorted(all_rows, key=lambda row: str(row["question_id"]))


def _load_source_spec(value: str | Path) -> tuple[Path, dict[str, Any], str, str]:
    path, source_spec, source_spec_file_digest = _read_document(
        value,
        label="selection source spec",
        max_bytes=_MAX_SOURCE_SPEC_BYTES,
    )
    _schema_validate(source_spec, "source.schema.json", "selection source spec")
    return path, source_spec, digest(source_spec), source_spec_file_digest


def _assert_source_spec(
    *,
    source_spec: Mapping[str, Any],
    selection_path: Path,
    selection_file_digest: str,
    selection: Mapping[str, Any],
    asset_root: Path,
) -> None:
    locked = source_spec["selection"]
    if selection_path.name != locked["input_filename"]:
        raise QuarantinedError("selection filename does not match the authorized SourceSpec")
    if selection_file_digest != locked["file_sha256"]:
        raise QuarantinedError("selection file digest does not match the authorized SourceSpec")
    if selection["schema_version"] != locked["schema_version"]:
        raise QuarantinedError("selection schema version does not match the authorized SourceSpec")
    if selection["id_sha256"] != locked["id_sha256"]:
        raise QuarantinedError("selection id_sha256 does not match the authorized SourceSpec")
    if selection["total"] != locked["total"]:
        raise QuarantinedError("selection total does not match the authorized SourceSpec")
    actual_domain_counts = {domain: block["count"] for domain, block in sorted(selection["domains"].items())}
    if actual_domain_counts != locked["domain_counts"]:
        raise QuarantinedError("selection domain counts do not match the authorized SourceSpec")
    if selection["bundle"]["package_id"] != source_spec["asset_package"]["package_id"]:
        raise QuarantinedError("selection bundle package_id does not match the authorized SourceSpec")
    if asset_root.name != source_spec["asset_package"]["expected_basename"]:
        raise QuarantinedError("asset root basename does not match the authorized SourceSpec")


def _declared_assets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for row in rows:
        for role, asset in _assets_for_row(row):
            asset_path = _canonical_asset_path(asset["path"], f"{row['question_id']} {role} asset path")
            candidate = {
                "bytes": asset["bytes"],
                "sha256": asset["sha256"],
            }
            prior = declared.setdefault(asset_path, candidate)
            if prior != candidate:
                raise InputError(f"selection declares conflicting integrity facts for asset {asset_path}")
    return declared


def _asset_reference_count(rows: list[dict[str, Any]]) -> int:
    return sum(len(_assets_for_row(row)) for row in rows)


def _asset_attestation_digest(
    rows: list[dict[str, Any]],
    attested_assets: Mapping[str, Mapping[str, Any]],
) -> str:
    """Digest observed asset facts without exposing asset or source paths."""

    facts: list[dict[str, Any]] = []
    for row in rows:
        for role, asset in _assets_for_row(row):
            asset_path = _canonical_asset_path(asset["path"], "selection asset path")
            observed = attested_assets[asset_path]
            facts.append(
                {
                    "role": role,
                    "bytes": observed["bytes"],
                    "sha256": observed["sha256"],
                }
            )
    facts.sort(key=lambda item: (item["role"], item["sha256"], item["bytes"]))
    return digest(
        {
            "algorithm": "qagen.selection.asset_attestation_v1",
            "asset_references": facts,
        }
    )


def _audit_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _audit_file_sha256(value: str | Path, *, max_bytes: int) -> str | None:
    """Return a bounded local input digest without retaining its path or body."""

    raw_path = Path(value).expanduser()
    try:
        if raw_path.is_symlink():
            return None
        path = raw_path.resolve(strict=True)
        if not path.is_file():
            return None
        total = 0
        hashed = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(min(1024 * 1024, max_bytes + 1 - total)):
                total += len(chunk)
                if total > max_bytes:
                    return None
                hashed.update(chunk)
        return f"sha256:{hashed.hexdigest()}"
    except OSError:
        return None


def _workspace_audit_file_sha256(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    field: str,
    max_bytes: int,
) -> str | None:
    """Hash a caller input only after confirming that it belongs to its workspace."""

    try:
        path = require_within(paths.root, value, field)
    except (InputError, OSError):
        return None
    return _audit_file_sha256(path, max_bytes=max_bytes)


def _request_digest(operation: str, **input_digests: str | None) -> str:
    """Create a path-free correlation key for one selection operation request."""

    return digest(
        {
            "audit_request_version": 1,
            "operation": operation,
            **input_digests,
        }
    )


def _attest_assets(asset_root: str | Path, declared: Mapping[str, Mapping[str, Any]]) -> tuple[Path, dict[str, dict[str, Any]]]:
    try:
        with lock_trusted_source_root(asset_root) as source_root:
            snapshot = snapshot_trusted_tree(source_root, ignored_names=(".DS_Store",))
            observed: dict[str, dict[str, Any]] = {}
            for relative_path, expected in sorted(declared.items()):
                file = snapshot.file(relative_path)
                if file.size != expected["bytes"]:
                    raise QuarantinedError(
                        f"asset byte size mismatch for {relative_path}: expected {expected['bytes']}, observed {file.size}"
                    )
                if file.sha256 != expected["sha256"]:
                    raise QuarantinedError(f"asset sha256 mismatch for {relative_path}")
                observed[relative_path] = {"bytes": file.size, "sha256": file.sha256}
            verify_trusted_tree_snapshot(source_root, snapshot)
            return snapshot.root, observed
    except SafeFilesystemError as exc:
        raise QuarantinedError(f"asset source could not be attested safely: {exc}") from exc


def _slot_body(
    *,
    row: dict[str, Any],
    selection: Mapping[str, Any],
    selection_file_digest: str,
    source_spec: Mapping[str, Any],
    source_spec_digest: str,
    asset_root: Path,
    attested_assets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    assets = []
    for role, asset in _assets_for_row(row):
        asset_path = _canonical_asset_path(asset["path"], f"{row['question_id']} {role} asset path")
        assets.append(
            {
                "role": role,
                "path": asset_path,
                "declared_bytes": asset["bytes"],
                "declared_sha256": asset["sha256"],
                "observed_bytes": attested_assets[asset_path]["bytes"],
                "observed_sha256": attested_assets[asset_path]["sha256"],
                **(
                    {
                        "mask_selection": asset["mask_selection"],
                        "mask_id": asset["mask_id"],
                    }
                    if role == "mask"
                    else {}
                ),
            }
        )
    track = str(row["access_track"])
    return {
        "source_selection": {
            "schema_version": selection["schema_version"],
            "selection_id_sha256": selection["id_sha256"],
            "selection_file_sha256": selection_file_digest,
            "source_spec_id": source_spec["id"],
            "source_spec_digest": source_spec_digest,
            "historical_origin": dict(source_spec["historical_origin"]),
        },
        "source_slot": {
            "question_id": row["question_id"],
            "task_id": row["task_id"],
            "source_leaf": _source_leaf(row),
            "source_access_track": track,
        },
        "access_track": {
            "source": track,
            "effective": track,
            "conversion": "preserved",
        },
        "asset_root": str(asset_root),
        "asset_attestations": assets,
        "source_row": row,
    }


def import_selection(
    paths: WorkspacePaths,
    *,
    selection_path: str | Path,
    asset_root: str | Path,
    source_spec_path: str | Path,
) -> dict[str, Any]:
    """Attest a frozen selection and persist one private source slot per row.

    The source spec locks the exact selection file, its selection fingerprint,
    expected quotas, and the external package basename.  No outputs are written
    until schema, selection integrity, and every declared asset byte/hash check
    have completed successfully.
    """

    paths.ensure()
    selection_file_sha256 = _audit_file_sha256(selection_path, max_bytes=_MAX_SELECTION_BYTES)
    source_spec_file_sha256 = _audit_file_sha256(source_spec_path, max_bytes=_MAX_SOURCE_SPEC_BYTES)
    audit: dict[str, Any] = {
        "audit_schema_version": 1,
        "phase": "load_source_spec",
        "selection_file_sha256": selection_file_sha256,
        "source_spec_file_sha256": source_spec_file_sha256,
        "request_digest": _request_digest(
            "selection_import",
            selection_file_sha256=selection_file_sha256,
            source_spec_file_sha256=source_spec_file_sha256,
        ),
    }
    started = False
    try:
        source_spec_file, source_spec, source_spec_digest, source_spec_file_sha256 = _load_source_spec(source_spec_path)
        audit["source_spec_file_sha256"] = source_spec_file_sha256
        audit["source_spec_digest"] = source_spec_digest
        audit["phase"] = "load_selection"
        source_file, selection, selection_file_digest = _read_document(
            selection_path,
            label="selection input",
            max_bytes=_MAX_SELECTION_BYTES,
        )
        audit["selection_file_sha256"] = selection_file_digest
        audit["request_digest"] = _request_digest(
            "selection_import",
            selection_file_sha256=selection_file_digest,
            source_spec_file_sha256=source_spec_file_sha256,
            source_spec_digest=source_spec_digest,
        )
        audit["phase"] = "validate_selection"
        _schema_validate(selection, "selection.schema.json", "selection input")
        rows = _validate_selection_integrity(selection)
        audit["selection_id_sha256"] = selection["id_sha256"]
        audit["row_count"] = len(rows)
        audit["asset_reference_count"] = _asset_reference_count(rows)
        audit["phase"] = "preflight_complete"
        append_event(paths, "selection_import_started", _audit_payload(audit))
        started = True
        root = Path(asset_root).expanduser()
        _assert_source_spec(
            source_spec=source_spec,
            selection_path=source_file,
            selection_file_digest=selection_file_digest,
            selection=selection,
            asset_root=root,
        )
        declared = _declared_assets(rows)
        audit["phase"] = "attest_assets"
        attested_root, attested_assets = _attest_assets(root, declared)
        asset_attestation_digest = _asset_attestation_digest(rows, attested_assets)
        audit["asset_attestation_digest"] = asset_attestation_digest
        audit["unique_asset_count"] = len(attested_assets)

        audit["phase"] = "persist_slots"
        selection_key = _short(selection["id_sha256"])
        slots_dir = paths.selection_slots / selection_key
        slot_refs: list[dict[str, Any]] = []
        for row in rows:
            question_id = safe_component(str(row["question_id"]), "selection question_id")
            slot = make_envelope(
                kind="selection_source_slot",
                identifier=f"selection-slot-{selection_key}-{question_id}",
                status="asset_attested",
                body=_slot_body(
                    row=row,
                    selection=selection,
                    selection_file_digest=selection_file_digest,
                    source_spec=source_spec,
                    source_spec_digest=source_spec_digest,
                    asset_root=attested_root,
                    attested_assets=attested_assets,
                ),
            )
            slot_path = slots_dir / f"{question_id}.json"
            atomic_write_json(slot_path, slot, private=True)
            slot_refs.append(
                {
                    "question_id": question_id,
                    "source_leaf": _source_leaf(row),
                    "source_access_track": row["access_track"],
                    "path": slot_path.relative_to(paths.root).as_posix(),
                    "content_digest": slot["content_digest"],
                }
            )

        import_body = {
            "source_selection": {
                "schema_version": selection["schema_version"],
                "selection_id_sha256": selection["id_sha256"],
                "selection_file_sha256": selection_file_digest,
                "source_file_name": source_file.name,
                "source_spec_id": source_spec["id"],
                "source_spec_digest": source_spec_digest,
                "source_spec_path": str(source_spec_file),
                "historical_origin": dict(source_spec["historical_origin"]),
            },
            "asset_attestation": {
                "asset_root": str(attested_root),
                "asset_root_basename": attested_root.name,
                "asset_reference_count": audit["asset_reference_count"],
                "unique_asset_count": len(attested_assets),
                "content_digest": asset_attestation_digest,
                "algorithm": "qagen.safe_fs.trusted_tree_sha256_v1",
            },
            "total": len(slot_refs),
            "slots": slot_refs,
            "local_only": True,
            "production_eligible": False,
        }
        imported = make_envelope(
            kind="selection_import",
            identifier=f"selection-import-{selection_key}",
            status="asset_attested_pending_taxonomy_review",
            body=import_body,
        )
        import_path = paths.selection_imports / f"{selection_key}.{_short(imported['content_digest'])}.selection-import.json"
        atomic_write_json(import_path, imported, private=True)
        append_event(
            paths,
            "selection_import_completed",
            _audit_payload(
                {
                    **audit,
                    "phase": "completed",
                    "selection_import_digest": imported["content_digest"],
                }
            ),
        )
        return {
            "status": imported["status"],
            "selection_id_sha256": selection["id_sha256"],
            "selection_file_sha256": selection_file_digest,
            "source_spec_id": source_spec["id"],
            "source_spec_digest": source_spec_digest,
            "total": len(slot_refs),
            "asset_reference_count": audit["asset_reference_count"],
            "unique_asset_count": len(attested_assets),
            "asset_attestation_digest": asset_attestation_digest,
            "slots_dir": str(slots_dir),
            "selection_import_path": str(import_path),
            "selection_import_digest": imported["content_digest"],
            "generation_eligible": False,
            "blockers": [
                "approved_taxonomy_mapping_required",
                "viewer_profile_not_bound",
                "subdomain_config_knowledge_and_plan_gates_pending",
            ],
        }
    except Exception as exc:
        if not started:
            append_event(paths, "selection_import_started", _audit_payload(audit))
        append_event(
            paths,
            "selection_import_failed",
            _audit_payload({**audit, "error_class": type(exc).__name__}),
        )
        raise


def _load_import(paths: WorkspacePaths, value: str | Path) -> tuple[Path, dict[str, Any]]:
    path = require_within(paths.root, value, "selection import")
    imported = read_json(path)
    if not isinstance(imported, Mapping):
        raise InputError("selection import must be an object")
    envelope = verify_envelope(imported, kind="selection_import")
    body = envelope["body"]
    if not isinstance(body, Mapping):
        raise InputError("selection import body must be an object")
    required = {"source_selection", "asset_attestation", "total", "slots", "local_only", "production_eligible"}
    if set(body) != required:
        raise InputError("selection import body has unexpected fields")
    if body["local_only"] is not True or body["production_eligible"] is not False:
        raise InputError("selection import must remain local-only and non-production")
    if not isinstance(body["source_selection"], Mapping) or not isinstance(body["slots"], list):
        raise InputError("selection import has malformed source_selection or slots")
    if body["total"] != len(body["slots"]):
        raise InputError("selection import total does not match slots")
    return path, envelope


def _load_slot(paths: WorkspacePaths, reference: Mapping[str, Any], import_body: Mapping[str, Any]) -> dict[str, Any]:
    required = {"question_id", "source_leaf", "source_access_track", "path", "content_digest"}
    if set(reference) != required:
        raise InputError("selection source slot reference has unexpected fields")
    relative = require_workspace_relative_path(reference["path"], "selection source slot path")
    slot_path = contained_path(paths.root, relative)
    slot_value = read_json(slot_path)
    if not isinstance(slot_value, Mapping):
        raise InputError(f"selection source slot is not an object: {relative}")
    slot = verify_envelope(slot_value, kind="selection_source_slot")
    if slot["content_digest"] != reference["content_digest"]:
        raise InputError(f"selection source slot digest mismatch: {relative}")
    body = slot["body"]
    if not isinstance(body, Mapping):
        raise InputError(f"selection source slot body is not an object: {relative}")
    source = body.get("source_selection")
    slot_info = body.get("source_slot")
    access_track = body.get("access_track")
    source_row = body.get("source_row")
    attestations = body.get("asset_attestations")
    if not all(isinstance(item, Mapping) for item in (source, slot_info, access_track, source_row)) or not isinstance(attestations, list):
        raise InputError(f"selection source slot is malformed: {relative}")
    import_source = import_body["source_selection"]
    for key in ("selection_id_sha256", "selection_file_sha256", "source_spec_digest"):
        if source.get(key) != import_source.get(key):
            raise InputError(f"selection source slot provenance mismatch for {relative}: {key}")
    if slot_info.get("question_id") != reference["question_id"]:
        raise InputError(f"selection source slot question_id mismatch: {relative}")
    if slot_info.get("source_leaf") != reference["source_leaf"]:
        raise InputError(f"selection source slot leaf mismatch: {relative}")
    if slot_info.get("source_access_track") != reference["source_access_track"]:
        raise InputError(f"selection source slot track mismatch: {relative}")
    if access_track.get("source") != slot_info.get("source_access_track") or access_track.get("effective") != access_track.get("source"):
        raise InputError(f"selection source slot changed access_track: {relative}")
    if access_track.get("conversion") != "preserved":
        raise InputError(f"selection source slot has an unsupported access_track conversion: {relative}")
    _validate_track(source_row)
    if _source_leaf(source_row) != slot_info.get("source_leaf"):
        raise InputError(f"selection source slot source_row leaf mismatch: {relative}")
    if source_row.get("question_id") != slot_info.get("question_id"):
        raise InputError(f"selection source slot source_row question_id mismatch: {relative}")
    if source_row.get("access_track") != slot_info.get("source_access_track"):
        raise InputError(f"selection source slot source_row access_track mismatch: {relative}")
    if source_row.get("viewer") == "niivue_deployed":
        masks = [item for item in attestations if isinstance(item, Mapping) and item.get("role") == "mask"]
        if len(masks) != 1:
            raise InputError(f"selection source slot requires exactly one typed Niivue mask: {relative}")
        mask = masks[0]
        if mask.get("mask_selection") != "label_index":
            raise InputError(f"selection source slot has a forbidden merged Niivue mask selection: {relative}")
        if not isinstance(mask.get("mask_id"), int) or isinstance(mask.get("mask_id"), bool) or mask["mask_id"] < 0:
            raise InputError(f"selection source slot has label_index Niivue mask without non-negative integer mask_id: {relative}")
    return slot


def _load_taxonomy_mapping(
    value: str | Path,
    source_selection: Mapping[str, Any],
    source_leaves: set[str],
    *,
    audit: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    _, mapping, mapping_file_digest = _read_document(
        value,
        label="selection taxonomy mapping",
        max_bytes=_MAX_MAPPING_BYTES,
    )
    if audit is not None:
        audit["taxonomy_mapping_file_sha256"] = mapping_file_digest
    schema_version = mapping.get("schema_version")
    approval = mapping.get("approval")
    is_route_proposal = isinstance(schema_version, str) and "vnext_route_proposal" in schema_version
    is_unapproved = mapping.get("status") == "draft_unapproved" or (
        isinstance(approval, Mapping) and approval.get("human_approved") is False
    )
    if is_route_proposal or is_unapproved:
        raise InputError(
            "selection taxonomy mapping is an unapproved route proposal and cannot be consumed "
            "as an approved taxonomy mapping"
        )
    _schema_validate(mapping, "selection_taxonomy_map.schema.json", "selection taxonomy mapping")
    _generation_strategy_policy(mapping)
    if mapping["selection_id_sha256"] != source_selection["selection_id_sha256"]:
        raise InputError("selection taxonomy mapping is bound to another selection id_sha256")
    if mapping["selection_file_sha256"] != source_selection["selection_file_sha256"]:
        raise InputError("selection taxonomy mapping is bound to another selection file digest")
    aliases: dict[str, str] = {}
    for entry in mapping["mappings"]:
        source_leaf = entry["source_leaf"]
        target_leaf = entry["target_leaf"]
        if source_leaf not in source_leaves:
            raise InputError(f"selection taxonomy mapping names unknown source leaf: {source_leaf}")
        if source_leaf in aliases:
            raise InputError(f"selection taxonomy mapping duplicates source leaf: {source_leaf}")
        source_domain = source_leaf.split(".", 1)[0]
        target_domain = target_leaf.split(".", 1)[0]
        if source_domain != target_domain:
            raise InputError(f"selection taxonomy mapping cannot cross domains: {source_leaf} -> {target_leaf}")
        aliases[source_leaf] = target_leaf
    return mapping, mapping_file_digest


def _target_config(target_leaf: str) -> dict[str, str] | None:
    domain, subdomain = target_leaf.split(".", 1)
    try:
        path = config_path("subdomains", domain, f"{subdomain}.yaml")
    except PackageResourceError:
        return None
    return {
        "path": f"configs/subdomains/{domain}/{subdomain}.yaml",
        "content_sha256": hash_file(path),
    }


def compile_selection(
    paths: WorkspacePaths,
    *,
    selection_import_path: str | Path,
    taxonomy_mapping_path: str | Path,
) -> dict[str, Any]:
    """Compile attested source slots into a private, non-generation-ready batch."""

    paths.ensure()
    selection_import_file_sha256 = _workspace_audit_file_sha256(
        paths,
        selection_import_path,
        field="selection import",
        max_bytes=_MAX_SELECTION_BYTES,
    )
    taxonomy_mapping_file_sha256 = _audit_file_sha256(
        taxonomy_mapping_path,
        max_bytes=_MAX_MAPPING_BYTES,
    )
    audit: dict[str, Any] = {
        "audit_schema_version": 1,
        "phase": "load_selection_import",
        "selection_import_file_sha256": selection_import_file_sha256,
        "taxonomy_mapping_file_sha256": taxonomy_mapping_file_sha256,
        "request_digest": _request_digest(
            "selection_compile",
            selection_import_file_sha256=selection_import_file_sha256,
            taxonomy_mapping_file_sha256=taxonomy_mapping_file_sha256,
        ),
    }
    started = False
    try:
        _, imported = _load_import(paths, selection_import_path)
        import_body = imported["body"]
        source_selection = import_body["source_selection"]
        asset_attestation = import_body["asset_attestation"]
        audit.update(
            {
                "phase": "load_slots",
                "selection_id_sha256": source_selection["selection_id_sha256"],
                "selection_file_sha256": source_selection["selection_file_sha256"],
                "source_spec_digest": source_selection["source_spec_digest"],
                "selection_import_digest": imported["content_digest"],
                "row_count": import_body["total"],
                "asset_reference_count": asset_attestation.get("asset_reference_count"),
                "asset_attestation_digest": asset_attestation.get("content_digest"),
            }
        )
        audit["request_digest"] = _request_digest(
            "selection_compile",
            selection_import_file_sha256=selection_import_file_sha256,
            selection_import_digest=imported["content_digest"],
            selection_file_sha256=source_selection["selection_file_sha256"],
            source_spec_digest=source_selection["source_spec_digest"],
            taxonomy_mapping_file_sha256=taxonomy_mapping_file_sha256,
        )
        append_event(paths, "selection_compile_started", _audit_payload(audit))
        started = True
        slots = [_load_slot(paths, reference, import_body) for reference in import_body["slots"]]
        source_leaves = {slot["body"]["source_slot"]["source_leaf"] for slot in slots}
        audit["phase"] = "load_taxonomy_mapping"
        mapping, mapping_file_digest = _load_taxonomy_mapping(
            taxonomy_mapping_path,
            source_selection,
            source_leaves,
            audit=audit,
        )
        mapping_content_digest = digest(mapping)
        generation_strategy_policy = _generation_strategy_policy(mapping)
        generation_strategy_policy_digest = digest(generation_strategy_policy)
        audit["taxonomy_mapping_digest"] = mapping_content_digest
        audit["generation_strategy_policy_digest"] = generation_strategy_policy_digest
        mappings = {entry["source_leaf"]: entry["target_leaf"] for entry in mapping["mappings"]}
        batch_rows: list[dict[str, Any]] = []
        missing_configs: set[str] = set()
        audit["phase"] = "compile_rows"
        for slot in sorted(slots, key=lambda item: item["body"]["source_slot"]["question_id"]):
            body = slot["body"]
            source_slot = body["source_slot"]
            source_row = body["source_row"]
            source_leaf = source_slot["source_leaf"]
            source_access_track = str(body["access_track"]["source"])
            generation_strategy = generation_strategy_policy[source_access_track]
            target_leaf = mappings.get(source_leaf)
            target_config = _target_config(target_leaf) if target_leaf is not None else None
            if target_leaf is None:
                mapping_status = "unmapped"
                generation_status = "blocked_pending_taxonomy_mapping"
            elif target_config is None:
                mapping_status = "target_config_missing"
                generation_status = "blocked_target_leaf_config_missing"
                missing_configs.add(target_leaf)
            else:
                mapping_status = "mapped"
                generation_status = "blocked_pending_viewer_knowledge_plan_and_review"
            row = {
                "slot_ref": {
                    "id": slot["id"],
                    "content_digest": slot["content_digest"],
                },
                "question_id": source_slot["question_id"],
                "task_id": source_slot["task_id"],
                "source_leaf": source_leaf,
                "target_leaf": target_leaf,
                "mapping_status": mapping_status,
                "access_track": dict(body["access_track"]),
                "generation_strategy": generation_strategy,
                "viewer_hint": {
                    "source_viewer": source_row["viewer"],
                    "allowed_operations": list(source_row["mcp_allow_list"]),
                },
                "assets": [
                    {
                        "role": item["role"],
                        "path": item["path"],
                        "bytes": item["observed_bytes"],
                        "sha256": item["observed_sha256"],
                        **(
                            {
                                "mask_selection": item["mask_selection"],
                                "mask_id": item["mask_id"],
                            }
                            if item["role"] == "mask"
                            else {}
                        ),
                    }
                    for item in body["asset_attestations"]
                ],
                "generation_status": generation_status,
            }
            if target_config is not None:
                row["target_leaf_config"] = target_config
            batch_rows.append(row)

        unmapped = sorted(source_leaves - set(mappings))
        blockers = [
            "viewer_profile_not_bound",
            "subdomain_config_knowledge_and_plan_gates_pending",
            "human_plan_approval_required",
        ]
        if unmapped:
            blockers.append("taxonomy_mapping_incomplete")
        if missing_configs:
            blockers.append("mapped_target_leaf_config_missing")
        batch_body = {
            "source_selection": {
                "selection_id_sha256": source_selection["selection_id_sha256"],
                "selection_file_sha256": source_selection["selection_file_sha256"],
                "import_digest": imported["content_digest"],
            },
            "taxonomy_mapping": {
                "id": mapping["id"],
                "content_digest": mapping_content_digest,
                "generation_strategy_policy_digest": generation_strategy_policy_digest,
                "reviewer": mapping["review"]["reviewer"],
                "reviewed_at": mapping["review"]["reviewed_at"],
            },
            "total": len(batch_rows),
            "mapping_coverage": {
                "source_leaf_count": len(source_leaves),
                "mapped_leaf_count": len(mappings),
                "unmapped_source_leaves": unmapped,
            },
            "generation_eligible": False,
            "generation_blockers": blockers,
            "rows": batch_rows,
        }
        _schema_validate(batch_body, "selection_batch_manifest.schema.json", "vNext batch manifest")
        status = "blocked_taxonomy_mapping" if unmapped else "compiled_pending_downstream_gates"
        batch = make_envelope(
            kind="vnext_selection_batch_manifest",
            identifier=(
                f"vnext-selection-batch-{_short(imported['content_digest'])}-"
                f"{_short(mapping_content_digest)}-{_short(digest(batch_body))}"
            ),
            status=status,
            body=batch_body,
        )
        # Config snapshots are part of every row.  The import and mapping can
        # stay fixed while a later, reviewed config revision changes the
        # compiled manifest.  Include the manifest digest in its filename so
        # recompilation preserves the older immutable artifact instead of
        # silently overwriting it.
        path = paths.selection_batches / (
            f"{_short(imported['content_digest'])}.{_short(mapping_content_digest)}."
            f"{_short(batch['content_digest'])}.vnext-batch.json"
        )
        if path.exists():
            try:
                existing = verify_envelope(read_json(path), kind="vnext_selection_batch_manifest")
            except InputError as exc:
                raise InputError("existing compiled selection manifest is invalid") from exc
            if existing.get("content_digest") != batch["content_digest"]:
                raise InputError("compiled selection manifest filename digest collision")
        atomic_write_json(path, batch, private=True)
        audit.update(
            {
                "phase": "completed" if not unmapped and not missing_configs else "blocked",
                "vnext_batch_manifest_digest": batch["content_digest"],
                "status": status,
                "blockers": blockers,
                "generation_strategy_counts": {
                    strategy: sum(row["generation_strategy"] == strategy for row in batch_rows)
                    for strategy in sorted(set(_GENERATION_STRATEGY_BY_SOURCE_ACCESS_TRACK.values()))
                },
            }
        )
        event_type = "selection_compile_blocked" if unmapped or missing_configs else "selection_compile_completed"
        append_event(paths, event_type, _audit_payload(audit))
        return {
            "status": status,
            "vnext_batch_manifest_path": str(path),
            "vnext_batch_manifest_digest": batch["content_digest"],
            "total": len(batch_rows),
            "mapped_leaf_count": len(mappings),
            "unmapped_source_leaves": unmapped,
            "target_config_missing": sorted(missing_configs),
            "generation_eligible": False,
            "blockers": blockers,
            "taxonomy_mapping_file_sha256": mapping_file_digest,
            "generation_strategy_policy_digest": generation_strategy_policy_digest,
            "generation_strategy_counts": {
                strategy: sum(row["generation_strategy"] == strategy for row in batch_rows)
                for strategy in sorted(set(_GENERATION_STRATEGY_BY_SOURCE_ACCESS_TRACK.values()))
            },
        }
    except Exception as exc:
        if not started:
            append_event(paths, "selection_compile_started", _audit_payload(audit))
        append_event(
            paths,
            "selection_compile_blocked",
            _audit_payload(
                {
                    **audit,
                    "blocker": "invalid_or_untrusted_compile_input",
                    "error_class": type(exc).__name__,
                }
            ),
        )
        raise
