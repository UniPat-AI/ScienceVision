"""Private V32 source adapter with a hard author/truth boundary.

The BCM522 review bundle is an audit bundle, not an author prompt corpus.  It
contains historic questions, answers, solver hints, and source-side paths.  A
selection importer therefore cannot simply hand those records to generation
workers.  This module admits only the frozen V32 ID set, persists private
provenance/truth records, and emits a deliberately small author projection.

There is intentionally no generic compute execution facility here.  The
external ``compute_contract`` names data and package requirements but does not
ship a reviewed executable program for every historical record.  Running a
string or arbitrary source file from that bundle would violate the boundary.
Until a reviewed local runner is registered, raw-compute truth remains
fail-closed; viewer-measurement truth likewise requires a new private viewer
receipt and evidence packet.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from jsonschema import Draft7Validator
import yaml

from ..contracts import canonical_json, digest, digest_bytes, make_envelope, verify_envelope
from ..errors import InputError, QuarantinedError
from ..paths import WorkspacePaths, contained_path, require_within
from ..resource_paths import PackageResourceError, config_path
from ..sources.safe_fs import (
    SafeFilesystemError,
    lock_trusted_source_root,
    snapshot_trusted_files,
    verify_trusted_file_set_snapshot,
)
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event


class PrivateSourceAdapterError(InputError):
    """A V32 source bundle cannot cross the private author/truth boundary."""


_MAX_SELECTION_BYTES = 4 * 1024 * 1024
_MAX_SPEC_BYTES = 2 * 1024 * 1024
_MAX_MAPPING_BYTES = 2 * 1024 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_JSONL_BYTES = 8 * 1024 * 1024
_MAX_SUMS_BYTES = 2 * 1024 * 1024
_METADATA_FILES = ("MANIFEST.json", "SHA256SUMS", "question_assets.jsonl", "answer_keys.jsonl")
_RAW_COMPUTE = "raw_compute"
_VIEWER_MEASUREMENT = "viewer_measurement"
_REAUTHOR_ON_ASSET = "reauthor_on_asset"
_PRESERVE_INTENT = "preserve_intent"
_FORBIDDEN_AUTHOR_KEYS = frozenset(
    {
        "answer",
        "answer_key",
        "answer_keys",
        "asset_path",
        "compute_contract",
        "compute_packages",
        "question",
        "question_id",
        "raw_path",
        "reference_answer",
        "solving_approach",
        "task_id",
    }
)
_VIEWER_OPERATION_ALIASES = {
    "viewer_bbox": "bbox",
    "viewer_measure_area": "measure_area",
    "viewer_measure_dihedral": "measure_dihedral",
    "viewer_measure_distance": "measure_distance",
    "viewer_measure_volume": "measure_volume",
    "viewer_rotate": "rotate",
    "viewer_slice": "slice",
    "viewer_window_level": "window_level",
    "viewer_zoom": "zoom",
}
_ALLOWED_VIEWER_OPERATIONS = frozenset(
    {
        "bbox",
        "measure_area",
        "measure_dihedral",
        "measure_distance",
        "measure_volume",
        "rotate",
        "slice",
        "window_level",
        "zoom",
    }
)
_PROGRAM_ID = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  ([^\s].*)$")


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _schema(name: str) -> dict[str, Any]:
    try:
        value = read_json(config_path("schemas", name))
    except PackageResourceError as exc:
        raise PrivateSourceAdapterError(f"required V32 source schema is unavailable: {name}") from exc
    if not isinstance(value, Mapping):
        raise PrivateSourceAdapterError(f"V32 source schema must be an object: {name}")
    return dict(value)


def _validate_schema(value: Any, schema_name: str, label: str) -> None:
    errors = sorted(Draft7Validator(_schema(schema_name)).iter_errors(value), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise PrivateSourceAdapterError(f"{label} schema validation failed at {location}: {error.message}")


def _read_local_document(value: str | Path, *, label: str, max_bytes: int) -> tuple[Path, dict[str, Any], str]:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise PrivateSourceAdapterError(f"{label} cannot be a symlink")
    try:
        path = raw.resolve(strict=True)
    except OSError as exc:
        raise PrivateSourceAdapterError(f"cannot resolve {label}") from exc
    if not path.is_file():
        raise PrivateSourceAdapterError(f"{label} is not a regular file")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise PrivateSourceAdapterError(f"cannot read {label}") from exc
    if len(content) > max_bytes:
        raise PrivateSourceAdapterError(f"{label} exceeds {max_bytes} byte limit")
    try:
        parsed = json.loads(content.decode("utf-8")) if path.suffix.lower() == ".json" else yaml.safe_load(content)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise PrivateSourceAdapterError(f"cannot parse {label}") from exc
    if not isinstance(parsed, Mapping):
        raise PrivateSourceAdapterError(f"{label} must be an object")
    return path, dict(parsed), digest_bytes(content)


def _canonical_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PrivateSourceAdapterError(f"{field} must be a non-empty relative path")
    if "\\" in value:
        raise PrivateSourceAdapterError(f"{field} must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PrivateSourceAdapterError(f"{field} must be a canonical relative path")
    return path.as_posix()


def _raw_id_digest(question_ids: list[str]) -> str:
    return sha256("\n".join(sorted(question_ids)).encode("utf-8")).hexdigest()


def _selection_rows(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate the frozen selection's ID/count closure before any bundle join."""

    _validate_schema(selection, "selection.schema.json", "V32 selection")
    domains = selection.get("domains")
    if not isinstance(domains, Mapping):
        raise PrivateSourceAdapterError("V32 selection domains must be an object")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    all_ids: list[str] = []
    for domain, raw_block in sorted(domains.items()):
        if not isinstance(domain, str) or not isinstance(raw_block, Mapping):
            raise PrivateSourceAdapterError("V32 selection domain block is malformed")
        block = dict(raw_block)
        block_rows = block.get("rows")
        if not isinstance(block_rows, list) or block.get("count") != len(block_rows):
            raise PrivateSourceAdapterError(f"V32 selection count mismatch for {domain}")
        identifiers: list[str] = []
        for raw_row in block_rows:
            if not isinstance(raw_row, Mapping):
                raise PrivateSourceAdapterError(f"V32 selection row is malformed for {domain}")
            row = dict(raw_row)
            question_id = row.get("question_id")
            if not isinstance(question_id, str) or not question_id or question_id in seen:
                raise PrivateSourceAdapterError("V32 selection has missing or duplicate question_id")
            if row.get("domain") != domain or row.get("task_id") is None:
                raise PrivateSourceAdapterError(f"V32 selection row identity mismatch for {question_id}")
            if row.get("access_track") not in {_RAW_COMPUTE, _VIEWER_MEASUREMENT}:
                raise PrivateSourceAdapterError(f"V32 selection row has unsupported access track: {question_id}")
            if row.get("access_track") == _RAW_COMPUTE and (
                row.get("has_compute_contract") is not True or not row.get("compute_packages")
            ):
                raise PrivateSourceAdapterError(f"raw_compute selection row lacks its declared contract: {question_id}")
            if row.get("access_track") == _VIEWER_MEASUREMENT and (
                row.get("has_compute_contract") is not False or row.get("compute_packages")
            ):
                raise PrivateSourceAdapterError(f"viewer_measurement selection row carries a compute contract: {question_id}")
            _canonical_relative_path(_require_mapping(row.get("primary_asset"), "primary asset").get("path"), "primary asset path")
            mask = row.get("mask_asset")
            if mask is not None:
                mask_value = _require_mapping(mask, "mask asset")
                _canonical_relative_path(mask_value.get("path"), "mask asset path")
                if mask_value.get("mask_selection") != "label_index":
                    raise PrivateSourceAdapterError(
                        f"selection mask policy must preserve one explicit label_index: {question_id}"
                    )
                mask_id = mask_value.get("mask_id")
                if not isinstance(mask_id, int) or isinstance(mask_id, bool) or mask_id < 0:
                    raise PrivateSourceAdapterError(f"selection mask label_index is invalid: {question_id}")
            seen.add(question_id)
            identifiers.append(question_id)
            all_ids.append(question_id)
            rows.append(row)
        if block.get("question_ids") != sorted(identifiers) or block.get("id_sha256") != _raw_id_digest(identifiers):
            raise PrivateSourceAdapterError(f"V32 selection ID closure mismatch for {domain}")
    if selection.get("total") != len(rows) or selection.get("id_sha256") != _raw_id_digest(all_ids):
        raise PrivateSourceAdapterError("V32 selection global ID closure mismatch")
    return sorted(rows, key=lambda row: str(row["question_id"]))


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PrivateSourceAdapterError(f"{label} must be an object")
    return dict(value)


def _load_source_spec(value: str | Path) -> tuple[dict[str, Any], str]:
    _, source_spec, file_digest = _read_local_document(value, label="V32 private source spec", max_bytes=_MAX_SPEC_BYTES)
    _validate_schema(source_spec, "v32_private_source_spec.schema.json", "V32 private source spec")
    return source_spec, file_digest


def _assert_selection_spec(selection: Mapping[str, Any], selection_file_digest: str, source_spec: Mapping[str, Any]) -> None:
    locked = _require_mapping(source_spec.get("selection"), "source spec selection")
    actual_counts = {
        str(domain): int(block["count"])
        for domain, block in sorted(_require_mapping(selection.get("domains"), "selection domains").items())
        if isinstance(block, Mapping)
    }
    if (
        selection_file_digest != locked.get("file_sha256")
        or selection.get("schema_version") != locked.get("schema_version")
        or selection.get("id_sha256") != locked.get("id_sha256")
        or selection.get("total") != locked.get("total")
        or actual_counts != locked.get("domain_counts")
    ):
        raise QuarantinedError("selection does not match the pinned V32 private source spec")


def _parse_checksums(content: bytes) -> dict[str, str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QuarantinedError("bundle SHA256SUMS is not UTF-8") from exc
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = _SUM_LINE.fullmatch(line)
        if match is None:
            raise QuarantinedError("bundle SHA256SUMS has an unsupported line")
        raw_path = _canonical_relative_path(match.group(2), "bundle checksum path")
        if raw_path in entries:
            raise QuarantinedError("bundle SHA256SUMS has duplicate paths")
        entries[raw_path] = f"sha256:{match.group(1)}"
    if not entries:
        raise QuarantinedError("bundle SHA256SUMS is empty")
    return entries


def _read_json_bytes(content: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QuarantinedError(f"bundle {label} cannot be parsed") from exc
    return _require_mapping(value, f"bundle {label}")


def _read_jsonl_bytes(content: bytes, *, label: str, expected_schema: str) -> dict[str, dict[str, Any]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QuarantinedError(f"bundle {label} is not UTF-8") from exc
    rows: dict[str, dict[str, Any]] = {}
    if not text.endswith("\n"):
        raise QuarantinedError(f"bundle {label} must end with a newline")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise QuarantinedError(f"bundle {label} has a blank JSONL record")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QuarantinedError(f"bundle {label} has invalid JSONL") from exc
        value = _require_mapping(row, f"bundle {label} row")
        question_id = value.get("question_id")
        if not isinstance(question_id, str) or not question_id or question_id in rows:
            raise QuarantinedError(f"bundle {label} has duplicate or missing question_id")
        if value.get("schema_version") != expected_schema:
            raise QuarantinedError(f"bundle {label} schema version mismatch")
        rows[question_id] = value
    if not rows:
        raise QuarantinedError(f"bundle {label} is empty")
    return rows


def _selection_asset_rows(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    primary = _require_mapping(row.get("primary_asset"), "selection primary asset")
    items = [{"role": "primary", **primary}]
    if row.get("mask_asset") is not None:
        items.append({"role": "mask", **_require_mapping(row.get("mask_asset"), "selection mask asset")})
    return items


def _bundle_assets(row: Mapping[str, Any], question_id: str) -> dict[str, dict[str, Any]]:
    values = row.get("assets")
    if not isinstance(values, list) or not values:
        raise QuarantinedError(f"bundle question assets are missing for {question_id}")
    by_role: dict[str, dict[str, Any]] = {}
    for raw in values:
        asset = _require_mapping(raw, "bundle asset")
        role = asset.get("role")
        if role not in {"primary", "mask"} or role in by_role:
            raise QuarantinedError(f"bundle asset role closure is invalid for {question_id}")
        _canonical_relative_path(asset.get("path"), "bundle asset path")
        if not isinstance(asset.get("bytes"), int) or asset["bytes"] < 1:
            raise QuarantinedError(f"bundle asset bytes are invalid for {question_id}")
        if not isinstance(asset.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
            raise QuarantinedError(f"bundle asset SHA-256 is invalid for {question_id}")
        by_role[str(role)] = asset
    return by_role


def _assert_asset_matches_selection(
    *,
    selection_asset: Mapping[str, Any],
    bundle_asset: Mapping[str, Any],
    role: str,
    question_id: str,
) -> dict[str, Any]:
    expected_path = _canonical_relative_path(selection_asset.get("path"), f"selection {role} asset path")
    if (
        bundle_asset.get("path") != expected_path
        or bundle_asset.get("bytes") != selection_asset.get("bytes")
        or bundle_asset.get("sha256") != selection_asset.get("sha256")
    ):
        raise QuarantinedError(f"bundle {role} asset differs from the frozen selection: {question_id}")
    if role == "mask":
        if (
            bundle_asset.get("mask_selection") != selection_asset.get("mask_selection")
            or bundle_asset.get("mask_id") != selection_asset.get("mask_id")
        ):
            raise QuarantinedError(f"bundle mask selector differs from frozen selection: {question_id}")
    result = {
        "role": role,
        "path": expected_path,
        "bytes": int(selection_asset["bytes"]),
        "sha256": str(selection_asset["sha256"]),
    }
    if role == "mask":
        result.update(
            {
                "mask_selection": selection_asset["mask_selection"],
                "mask_id": selection_asset["mask_id"],
            }
        )
    return result


def _normalized_operations(row: Mapping[str, Any], question_id: str) -> list[str]:
    values = row.get("mcp_allow_list")
    if not isinstance(values, list) or not values or not all(isinstance(item, str) and item for item in values):
        raise QuarantinedError(f"bundle viewer operation list is invalid for {question_id}")
    normalized = {_VIEWER_OPERATION_ALIASES.get(item, item) for item in values}
    if normalized - _ALLOWED_VIEWER_OPERATIONS:
        raise QuarantinedError(f"bundle viewer operation is unsupported for {question_id}")
    return sorted(normalized)


def _validate_question_row(
    selection_row: Mapping[str, Any],
    question_row: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    question_id = str(selection_row["question_id"])
    for field in ("question_id", "task_id", "domain", "sub_domain", "viewer"):
        if question_row.get(field) != selection_row.get(field):
            raise QuarantinedError(f"bundle question identity differs from frozen selection: {question_id}")
    provenance = _require_mapping(question_row.get("provenance"), "bundle question provenance")
    if provenance.get("access_track") != selection_row.get("access_track"):
        raise QuarantinedError(f"bundle access track differs from frozen selection: {question_id}")
    if not isinstance(question_row.get("question"), str) or not question_row["question"]:
        raise QuarantinedError(f"bundle historical question is invalid: {question_id}")
    answer_shape = _require_mapping(question_row.get("answer_shape"), "bundle answer shape")
    fields = answer_shape.get("fields")
    if (
        not isinstance(answer_shape.get("kind"), str)
        or not isinstance(fields, list)
        or not all(isinstance(item, str) and item for item in fields)
        or len(set(fields)) != len(fields)
        or list(fields) != list(selection_row.get("answer_fields", []))
    ):
        raise QuarantinedError(f"bundle answer shape differs from frozen selection: {question_id}")
    bundle_by_role = _bundle_assets(question_row, question_id)
    expected_assets = _selection_asset_rows(selection_row)
    if set(bundle_by_role) != {str(asset["role"]) for asset in expected_assets}:
        raise QuarantinedError(f"bundle asset role set differs from frozen selection: {question_id}")
    assets = [
        _assert_asset_matches_selection(
            selection_asset=asset,
            bundle_asset=bundle_by_role[str(asset["role"])],
            role=str(asset["role"]),
            question_id=question_id,
        )
        for asset in expected_assets
    ]
    return assets, {"kind": answer_shape["kind"], "fields": list(fields)}, _normalized_operations(question_row, question_id)


def _validate_answer_key(
    question_row: Mapping[str, Any],
    answer_key: Mapping[str, Any],
    *,
    question_id: str,
) -> dict[str, Any]:
    if answer_key.get("question_id") != question_id:
        raise QuarantinedError(f"bundle answer-key identity differs from frozen selection: {question_id}")
    if not isinstance(answer_key.get("reference_answer"), Mapping):
        raise QuarantinedError(f"bundle answer key has no structured private reference: {question_id}")
    if not isinstance(answer_key.get("solving_approach"), str):
        raise QuarantinedError(f"bundle answer key lacks a private solving-approach record: {question_id}")
    evidence = _require_mapping(answer_key.get("evidence"), "bundle answer evidence")
    primary = _bundle_assets(question_row, question_id)["primary"]
    if evidence.get("path") != primary.get("path") or evidence.get("sha256") != primary.get("sha256"):
        raise QuarantinedError(f"bundle answer evidence is not bound to the primary asset: {question_id}")
    return {
        "reference_answer": dict(_require_mapping(answer_key["reference_answer"], "legacy reference answer")),
        "reference_answer_digest": digest(dict(answer_key["reference_answer"])),
        "answer_key_digest": digest(dict(answer_key)),
        "truth_mode": answer_key.get("truth_mode"),
        "truth_status": answer_key.get("truth_status"),
    }


def _validate_compute_contract(
    contract: Any,
    *,
    assets: list[dict[str, Any]],
    source_spec: Mapping[str, Any],
    question_id: str,
) -> dict[str, Any]:
    value = _require_mapping(contract, "compute contract")
    policy = _require_mapping(source_spec.get("compute_policy"), "source spec compute policy")
    if value.get("schema_version") != "sciencevision.qa_gen.compute_contract.v1":
        raise QuarantinedError(f"compute contract schema is invalid: {question_id}")
    if value.get("network_access") is not False or value.get("deterministic") is not True:
        raise QuarantinedError(f"compute contract must be deterministic and no-network: {question_id}")
    capabilities = value.get("allowed_capabilities")
    packages = value.get("packages")
    runtime = value.get("runtime")
    if (
        not isinstance(capabilities, list)
        or set(capabilities) != set(policy["allowed_capabilities"])
        or not isinstance(packages, list)
        or not packages
        or not set(packages).issubset(set(policy["allowed_packages"]))
        or runtime not in set(policy["allowed_runtimes"])
    ):
        raise QuarantinedError(f"compute contract exceeds the reviewed allowlist: {question_id}")
    program = value.get("program")
    if program is not None and (not isinstance(program, str) or not _PROGRAM_ID.fullmatch(program)):
        raise QuarantinedError(f"compute contract program is not an approved identifier: {question_id}")
    input_hashes = value.get("input_asset_hashes")
    if not isinstance(input_hashes, list) or not input_hashes:
        raise QuarantinedError(f"compute contract has no asset hash closure: {question_id}")
    expected = {(asset["sha256"], asset["bytes"]) for asset in assets}
    observed: set[tuple[str, int]] = set()
    for raw in input_hashes:
        item = _require_mapping(raw, "compute input asset")
        raw_sha = item.get("sha256")
        byte_size = item.get("byte_size")
        if not isinstance(raw_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_sha) or not isinstance(byte_size, int):
            raise QuarantinedError(f"compute contract input asset is malformed: {question_id}")
        observed.add((raw_sha, byte_size))
    if observed != expected:
        raise QuarantinedError(f"compute contract asset hashes differ from attested assets: {question_id}")
    return value


def _load_taxonomy_mapping(
    value: str | Path,
    *,
    selection: Mapping[str, Any],
    source_leaves: set[str],
) -> tuple[dict[str, Any], str, str, dict[str, str]]:
    _, mapping, file_digest = _read_local_document(value, label="approved V32 taxonomy mapping", max_bytes=_MAX_MAPPING_BYTES)
    _validate_schema(mapping, "selection_taxonomy_map.schema.json", "approved V32 taxonomy mapping")
    if (
        mapping.get("selection_id_sha256") != selection.get("id_sha256")
        or mapping.get("selection_file_sha256") is None
    ):
        raise PrivateSourceAdapterError("taxonomy mapping is not bound to this V32 selection")
    aliases: dict[str, str] = {}
    for raw in mapping["mappings"]:
        entry = _require_mapping(raw, "taxonomy mapping entry")
        source_leaf = entry.get("source_leaf")
        target_leaf = entry.get("target_leaf")
        if not isinstance(source_leaf, str) or not isinstance(target_leaf, str) or source_leaf in aliases:
            raise PrivateSourceAdapterError("taxonomy mapping has invalid or duplicate source leaves")
        if source_leaf not in source_leaves or source_leaf.split(".", 1)[0] != target_leaf.split(".", 1)[0]:
            raise PrivateSourceAdapterError("taxonomy mapping has an invalid source/target domain relation")
        domain, subdomain = target_leaf.split(".", 1)
        try:
            config_path("subdomains", domain, f"{subdomain}.yaml")
        except PackageResourceError as exc:
            raise PrivateSourceAdapterError(f"taxonomy mapping targets an unavailable V32 leaf: {target_leaf}") from exc
        aliases[source_leaf] = target_leaf
    if set(aliases) != source_leaves:
        raise PrivateSourceAdapterError("taxonomy mapping must cover every frozen V32 source leaf exactly once")
    return mapping, digest(mapping), file_digest, aliases


def _scene_kind(assets: list[dict[str, Any]]) -> str:
    formats = {str(asset.get("format", "")).lower() for asset in assets}
    if any(asset["path"].endswith((".nii", ".nii.gz")) for asset in assets):
        return "volumetric_image"
    if formats & {"cif", "mmcif", "mol", "mol2", "pdb", "sdf", "xyz"}:
        return "molecular_structure"
    return "three_dimensional_scene"


def _author_brief_body(
    *,
    source_record_digest: str,
    target_leaf: str,
    assets: list[dict[str, Any]],
    answer_shape: Mapping[str, Any],
    allowed_operations: list[str],
    generation_strategy: str,
) -> dict[str, Any]:
    body = {
        "source_record_digest": source_record_digest,
        "target_leaf": target_leaf,
        "anonymous_target_descriptor": {
            "scene_kind": _scene_kind(assets),
            "asset_roles": sorted(str(asset["role"]) for asset in assets),
        },
        "answer_shape": {"kind": answer_shape["kind"], "fields": list(answer_shape["fields"])},
        "allowed_viewer_operations": allowed_operations,
        "generation_strategy": generation_strategy,
        "constraints": [
            "new_question_only",
            "viewer_required",
            "no_historical_question_or_answer",
            "no_raw_compute_or_source_metadata",
        ],
    }
    _validate_schema(body, "v32_sanitized_author_brief.schema.json", "sanitized V32 author brief")
    return body


def _walk(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, nested in value.items():
            if not isinstance(key, str):
                raise PrivateSourceAdapterError("author brief has a non-string key")
            if key.casefold() in _FORBIDDEN_AUTHOR_KEYS:
                raise PrivateSourceAdapterError(f"author brief leaks forbidden field: {key}")
            result.extend(_walk(nested))
        return result
    if isinstance(value, list):
        return [item for nested in value for item in _walk(nested)]
    if isinstance(value, str):
        return [value]
    return []


def _assert_author_brief_safe(
    body: Mapping[str, Any],
    *,
    historic_question: str,
    historic_answer: Mapping[str, Any],
    source_identifiers: set[str],
    source_paths: set[str],
) -> None:
    values = _walk(body)
    forbidden_values = {
        historic_question,
        canonical_json(dict(historic_answer)).decode("utf-8"),
        *source_identifiers,
        *source_paths,
    }
    for item in values:
        if item in forbidden_values or item.startswith("/") or ":\\" in item:
            raise PrivateSourceAdapterError("sanitized author brief contains historical or absolute source content")


def _audit_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _request_digest(operation: str, **values: str | None) -> str:
    return digest({"audit_request_version": 1, "operation": operation, **values})


def _record_stem(selection_id: str, question_id: str) -> str:
    return f"record-{_short(digest({'selection_id': selection_id, 'question_id': question_id}))}"


def _source_record_body(
    *,
    selection: Mapping[str, Any],
    selection_file_digest: str,
    bundle_root: Path,
    bundle_facts: Mapping[str, str],
    source_spec: Mapping[str, Any],
    source_spec_file_digest: str,
    mapping_digest: str,
    selection_row: Mapping[str, Any],
    question_row: Mapping[str, Any],
    assets: list[dict[str, Any]],
    answer_shape: Mapping[str, Any],
    operations: list[str],
    target_leaf: str,
    legacy: Mapping[str, Any],
) -> dict[str, Any]:
    question_id = str(selection_row["question_id"])
    source_track = str(selection_row["access_track"])
    private_assets = [
        {
            **asset,
            "format": _bundle_assets(question_row, question_id)[asset["role"]].get("format"),
        }
        for asset in assets
    ]
    if source_track == _RAW_COMPUTE:
        truth_input: dict[str, Any] = {
            "mode": "attested_compute_recompute",
            "compute_contract": _validate_compute_contract(
                question_row.get("compute_contract"),
                assets=assets,
                source_spec=source_spec,
                question_id=question_id,
            ),
            "asset_hashes": [{"bytes": asset["bytes"], "sha256": asset["sha256"]} for asset in assets],
            "runner_status": "pending_reviewed_local_runner",
        }
    else:
        if question_row.get("compute_contract") is not None:
            raise QuarantinedError(f"viewer measurement source has a compute contract: {question_id}")
        truth_input = {
            "mode": "viewer_evidence_required",
            "expected_answer_shape": {"kind": answer_shape["kind"], "fields": list(answer_shape["fields"])},
            "required_bindings": ["new_viewer_receipt", "new_evidence_packet"],
        }
    body = {
        "selection": {
            "selection_id_sha256": selection["id_sha256"],
            "selection_file_sha256": selection_file_digest,
            "source_spec_id": source_spec["id"],
            "source_spec_file_sha256": source_spec_file_digest,
            "taxonomy_mapping_digest": mapping_digest,
        },
        "bundle": {
            "root": str(bundle_root),
            "package_id": source_spec["bundle"]["package_id"],
            **dict(bundle_facts),
        },
        "source_identity": {
            "question_id": question_id,
            "task_id": selection_row["task_id"],
            "source_leaf": f"{selection_row['domain']}.{selection_row['sub_domain']}",
            "question_assets_row_digest": digest(dict(question_row)),
        },
        "source_access_track": source_track,
        "target_leaf": target_leaf,
        "assets": private_assets,
        "truth_input": truth_input,
        "legacy_regression": {
            "comparison_policy": "private_regression_only_never_truth_reuse",
            "reference_answer": dict(legacy["reference_answer"]),
            "reference_answer_digest": legacy["reference_answer_digest"],
            "answer_key_digest": legacy["answer_key_digest"],
            "truth_mode": legacy.get("truth_mode"),
            "truth_status": legacy.get("truth_status"),
        },
    }
    _validate_schema(body, "v32_private_source_record.schema.json", "private V32 source record")
    return body


def _bundle_snapshot(
    bundle_root: str | Path,
    *,
    source_spec: Mapping[str, Any],
    selection_assets: list[dict[str, Any]],
) -> tuple[Path, dict[str, bytes], dict[str, Any]]:
    bundle = _require_mapping(source_spec.get("bundle"), "source spec bundle")
    metadata_paths = {
        bundle["manifest_filename"],
        bundle["checksums_filename"],
        bundle["question_assets"]["filename"],
        bundle["answer_keys"]["filename"],
    }
    asset_paths = {_canonical_relative_path(asset["path"], "selection asset path") for asset in selection_assets}
    all_paths = metadata_paths | asset_paths
    max_file_bytes = max(int(bundle["max_asset_bytes"]), _MAX_JSONL_BYTES)
    try:
        with lock_trusted_source_root(bundle_root) as root:
            preliminary = snapshot_trusted_files(
                root,
                metadata_paths,
                capture_paths=metadata_paths,
                max_capture_bytes=_MAX_JSONL_BYTES,
                max_file_bytes=max_file_bytes,
            )
            captured = {path: preliminary.captured(path) for path in metadata_paths}
            checksums = _parse_checksums(captured[bundle["checksums_filename"]])
            for label, path, expected in (
                ("manifest", bundle["manifest_filename"], bundle["manifest_sha256"]),
                ("question_assets", bundle["question_assets"]["filename"], bundle["question_assets"]["sha256"]),
                ("answer_keys", bundle["answer_keys"]["filename"], bundle["answer_keys"]["sha256"]),
            ):
                observed = f"sha256:{preliminary.file(path).sha256}"
                if observed != expected or checksums.get(path) != expected:
                    raise QuarantinedError(f"bundle {label} digest does not match its pinned source spec")
            for asset in selection_assets:
                asset_path = _canonical_relative_path(asset["path"], "selection asset path")
                expected = f"sha256:{asset['sha256']}"
                if checksums.get(asset_path) != expected:
                    raise QuarantinedError("bundle checksum closure does not match a frozen selection asset")

            snapshot = snapshot_trusted_files(
                root,
                all_paths,
                capture_paths=metadata_paths,
                max_capture_bytes=_MAX_JSONL_BYTES,
                max_file_bytes=max_file_bytes,
            )
            for asset in selection_assets:
                asset_path = _canonical_relative_path(asset["path"], "selection asset path")
                observed = snapshot.file(asset_path)
                if observed.size != asset["bytes"] or observed.sha256 != asset["sha256"]:
                    raise QuarantinedError("bundle asset bytes or SHA-256 differs from the frozen selection")
            verify_trusted_file_set_snapshot(root, snapshot)
            return snapshot.root, {path: snapshot.captured(path) for path in metadata_paths}, {
                "manifest_sha256": f"sha256:{snapshot.file(bundle['manifest_filename']).sha256}",
                "checksums_sha256": f"sha256:{snapshot.file(bundle['checksums_filename']).sha256}",
                "question_assets_sha256": f"sha256:{snapshot.file(bundle['question_assets']['filename']).sha256}",
                "answer_keys_sha256": f"sha256:{snapshot.file(bundle['answer_keys']['filename']).sha256}",
                "asset_closure_digest": digest(
                    sorted(
                        [{"bytes": asset["bytes"], "sha256": asset["sha256"]} for asset in selection_assets],
                        key=lambda item: (str(item["sha256"]), int(item["bytes"])),
                    )
                ),
            }
    except SafeFilesystemError as exc:
        raise QuarantinedError("V32 bundle cannot be attested through a no-follow trusted source snapshot") from exc


def _validate_bundle_manifest(manifest: Mapping[str, Any], source_spec: Mapping[str, Any]) -> None:
    bundle = _require_mapping(source_spec.get("bundle"), "source spec bundle")
    if (
        manifest.get("schema_version") != bundle["manifest_schema_version"]
        or manifest.get("package_id") != bundle["package_id"]
        or manifest.get("question_schema") != bundle["question_assets"]["schema_version"]
        or manifest.get("answer_schema") != bundle["answer_keys"]["schema_version"]
    ):
        raise QuarantinedError("bundle MANIFEST identity differs from the pinned V32 source spec")
    counts = _require_mapping(manifest.get("counts"), "bundle MANIFEST counts")
    if not isinstance(counts.get("questions"), int) or counts["questions"] < int(source_spec["selection"]["total"]):
        raise QuarantinedError("bundle MANIFEST does not contain the frozen V32 cohort")


def _private_source_import_path(paths: WorkspacePaths, content_digest: str) -> Path:
    return paths.v32_private_sources / "imports" / f"v32-{_short(content_digest)}.source-import.json"


def _private_source_handoff_path(paths: WorkspacePaths, content_digest: str) -> Path:
    return paths.v32_private_sources / "handoffs" / f"v32-{_short(content_digest)}.agent-handoff.json"


def import_v32_private_sources(
    paths: WorkspacePaths,
    *,
    selection_path: str | Path,
    bundle_root: str | Path,
    source_spec_path: str | Path,
    taxonomy_mapping_path: str | Path,
) -> dict[str, Any]:
    """Attest and import exactly the frozen V32 cohort into private artifacts.

    No historical question, answer, solving approach, source task ID, or asset
    path is placed in an author brief or agent handoff.  All outputs stay below
    ``.qagen/private/v32-private-source``.
    """

    paths.ensure()
    audit: dict[str, Any] = {"audit_schema_version": 1, "phase": "load_source_spec"}
    started = False
    try:
        source_spec, source_spec_file_digest = _load_source_spec(source_spec_path)
        audit["source_spec_file_sha256"] = source_spec_file_digest
        audit["source_spec_digest"] = digest(source_spec)
        audit["phase"] = "load_selection"
        _, selection, selection_file_digest = _read_local_document(
            selection_path,
            label="frozen V32 selection",
            max_bytes=_MAX_SELECTION_BYTES,
        )
        audit["selection_file_sha256"] = selection_file_digest
        audit["request_digest"] = _request_digest(
            "v32_private_source_import",
            selection_file_sha256=selection_file_digest,
            source_spec_file_sha256=source_spec_file_digest,
        )
        rows = _selection_rows(selection)
        _assert_selection_spec(selection, selection_file_digest, source_spec)
        audit.update({"selection_id_sha256": selection["id_sha256"], "row_count": len(rows), "phase": "load_mapping"})
        source_leaves = {f"{row['domain']}.{row['sub_domain']}" for row in rows}
        mapping, mapping_digest, mapping_file_digest, aliases = _load_taxonomy_mapping(
            taxonomy_mapping_path,
            selection=selection,
            source_leaves=source_leaves,
        )
        if mapping.get("selection_file_sha256") != selection_file_digest:
            raise PrivateSourceAdapterError("taxonomy mapping is bound to another frozen selection file")
        audit.update(
            {
                "taxonomy_mapping_digest": mapping_digest,
                "taxonomy_mapping_file_sha256": mapping_file_digest,
                "phase": "preflight_complete",
            }
        )
        append_event(paths, "v32_private_source_import_started", _audit_payload(audit))
        started = True

        all_assets = [asset for row in rows for asset in _selection_asset_rows(row)]
        bundle_path, captured, bundle_facts = _bundle_snapshot(
            bundle_root,
            source_spec=source_spec,
            selection_assets=all_assets,
        )
        audit.update({"phase": "validate_bundle", "asset_closure_digest": bundle_facts["asset_closure_digest"]})
        bundle_spec = _require_mapping(source_spec.get("bundle"), "source spec bundle")
        manifest = _read_json_bytes(captured[bundle_spec["manifest_filename"]], label="MANIFEST")
        _validate_bundle_manifest(manifest, source_spec)
        question_rows = _read_jsonl_bytes(
            captured[bundle_spec["question_assets"]["filename"]],
            label="question_assets",
            expected_schema=bundle_spec["question_assets"]["schema_version"],
        )
        answer_rows = _read_jsonl_bytes(
            captured[bundle_spec["answer_keys"]["filename"]],
            label="answer_keys",
            expected_schema=bundle_spec["answer_keys"]["schema_version"],
        )

        records: list[dict[str, Any]] = []
        raw_compute_count = 0
        viewer_measurement_count = 0
        audit["phase"] = "persist_private_records"
        for selection_row in rows:
            question_id = str(selection_row["question_id"])
            question_row = question_rows.get(question_id)
            answer_row = answer_rows.get(question_id)
            if question_row is None or answer_row is None:
                raise QuarantinedError("bundle join is incomplete for the frozen V32 cohort")
            assets, answer_shape, operations = _validate_question_row(selection_row, question_row)
            legacy = _validate_answer_key(question_row, answer_row, question_id=question_id)
            source_leaf = f"{selection_row['domain']}.{selection_row['sub_domain']}"
            target_leaf = aliases[source_leaf]
            record_body = _source_record_body(
                selection=selection,
                selection_file_digest=selection_file_digest,
                bundle_root=bundle_path,
                bundle_facts=bundle_facts,
                source_spec=source_spec,
                source_spec_file_digest=source_spec_file_digest,
                mapping_digest=mapping_digest,
                selection_row=selection_row,
                question_row=question_row,
                assets=assets,
                answer_shape=answer_shape,
                operations=operations,
                target_leaf=target_leaf,
                legacy=legacy,
            )
            stem = _record_stem(selection["id_sha256"], question_id)
            record = make_envelope(
                kind="v32_private_source_record",
                identifier=stem,
                status=("pending_safe_compute_recompute" if selection_row["access_track"] == _RAW_COMPUTE else "blocked_viewer_evidence_required"),
                body=record_body,
                producer="qagen/0.1.0:v32_private_source_adapter",
            )
            record_path = paths.v32_private_sources / "records" / f"{stem}.json"
            atomic_write_json(record_path, record, private=True)
            strategy = _REAUTHOR_ON_ASSET if selection_row["access_track"] == _RAW_COMPUTE else _PRESERVE_INTENT
            brief_body = _author_brief_body(
                source_record_digest=record["content_digest"],
                target_leaf=target_leaf,
                assets=record_body["assets"],
                answer_shape=answer_shape,
                allowed_operations=operations,
                generation_strategy=strategy,
            )
            _assert_author_brief_safe(
                brief_body,
                historic_question=str(question_row["question"]),
                historic_answer=legacy["reference_answer"],
                source_identifiers={question_id, str(selection_row["task_id"])},
                source_paths={str(asset["path"]) for asset in assets},
            )
            brief = make_envelope(
                kind="v32_sanitized_author_brief",
                identifier=f"brief-{_short(record['content_digest'])}",
                status="author_safe",
                body=brief_body,
                producer="qagen/0.1.0:v32_private_source_adapter",
            )
            brief_path = paths.v32_private_sources / "author-briefs" / f"brief-{_short(record['content_digest'])}.json"
            atomic_write_json(brief_path, brief, private=True)
            truth_mode = record_body["truth_input"]["mode"]
            raw_compute_count += truth_mode == "attested_compute_recompute"
            viewer_measurement_count += truth_mode == "viewer_evidence_required"
            records.append(
                {
                    "record": {
                        "path": record_path.relative_to(paths.root).as_posix(),
                        "content_digest": record["content_digest"],
                    },
                    "author_brief": {
                        "path": brief_path.relative_to(paths.root).as_posix(),
                        "content_digest": brief["content_digest"],
                    },
                    "truth_mode": truth_mode,
                }
            )

        if len(records) != int(source_spec["selection"]["total"]):
            raise PrivateSourceAdapterError("private V32 source adapter did not import the exact frozen cohort")
        import_body = {
            "selection": {
                "selection_id_sha256": selection["id_sha256"],
                "selection_file_sha256": selection_file_digest,
                "total": len(records),
            },
            "bundle": {
                "package_id": bundle_spec["package_id"],
                **bundle_facts,
            },
            "taxonomy_mapping": {"id": mapping["id"], "content_digest": mapping_digest},
            "records": records,
            "truth_gate_counts": {
                "attested_compute_recompute": raw_compute_count,
                "viewer_evidence_required": viewer_measurement_count,
            },
            "local_only": True,
            "production_eligible": False,
        }
        imported = make_envelope(
            kind="v32_private_source_import",
            identifier=f"v32-private-source-{_short(selection_file_digest)}-{_short(mapping_digest)}",
            status="attested_pending_new_truth",
            body=import_body,
            producer="qagen/0.1.0:v32_private_source_adapter",
        )
        import_path = _private_source_import_path(paths, imported["content_digest"])
        atomic_write_json(import_path, imported, private=True)

        handoff_records = [
            {
                "record_digest": item["record"]["content_digest"],
                "author_brief_digest": item["author_brief"]["content_digest"],
                "truth_mode": item["truth_mode"],
                "agent_inputs": {
                    "brief_builder": {"author_brief_digest": item["author_brief"]["content_digest"]},
                    "author": {"author_brief_digest": item["author_brief"]["content_digest"]},
                    "truth_builder": {
                        "private_source_record_digest": item["record"]["content_digest"],
                        "truth_mode": item["truth_mode"],
                    },
                    "evidence_worker": {"private_source_record_digest": item["record"]["content_digest"]},
                    "verifier": {"dependency_scope": "new_candidate_and_new_evidence_only"},
                    "rewriter": {"revision_gate": "human_receipt_required"},
                },
            }
            for item in records
        ]
        handoff_body = {"source_import_digest": imported["content_digest"], "records": handoff_records}
        _validate_schema(handoff_body, "v32_private_source_handoff.schema.json", "V32 agent handoff")
        handoff = make_envelope(
            kind="v32_private_source_agent_handoff",
            identifier=f"v32-handoff-{_short(imported['content_digest'])}",
            status="private_agent_inputs_ready",
            body=handoff_body,
            producer="qagen/0.1.0:v32_private_source_adapter",
        )
        handoff_path = _private_source_handoff_path(paths, handoff["content_digest"])
        atomic_write_json(handoff_path, handoff, private=True)
        audit.update(
            {
                "phase": "completed",
                "source_import_digest": imported["content_digest"],
                "agent_handoff_digest": handoff["content_digest"],
                "raw_compute_count": raw_compute_count,
                "viewer_measurement_count": viewer_measurement_count,
            }
        )
        append_event(paths, "v32_private_source_import_completed", _audit_payload(audit))
        return {
            "status": imported["status"],
            "source_import_path": str(import_path),
            "source_import_digest": imported["content_digest"],
            "agent_handoff_path": str(handoff_path),
            "agent_handoff_digest": handoff["content_digest"],
            "total": len(records),
            "raw_compute_count": raw_compute_count,
            "viewer_measurement_count": viewer_measurement_count,
            "generation_eligible": False,
            "blockers": [
                "new_raw_compute_truth_requires_reviewed_local_runner",
                "viewer_measurement_truth_requires_new_viewer_receipt_and_evidence",
                "downstream_viewer_knowledge_plan_and_human_review_gates_pending",
            ],
        }
    except Exception as exc:
        if not started:
            append_event(paths, "v32_private_source_import_started", _audit_payload(audit))
        append_event(
            paths,
            "v32_private_source_import_failed",
            _audit_payload({**audit, "error_class": type(exc).__name__}),
        )
        raise


def _load_import(paths: WorkspacePaths, value: str | Path) -> tuple[Path, dict[str, Any]]:
    path = require_within(paths.root, value, "V32 private source import")
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise PrivateSourceAdapterError("V32 private source import must be an object")
    try:
        imported = verify_envelope(raw, kind="v32_private_source_import")
    except InputError as exc:
        raise PrivateSourceAdapterError(f"V32 private source import content digest is invalid: {exc}") from exc
    body = _require_mapping(imported.get("body"), "V32 private source import body")
    required = {"selection", "bundle", "taxonomy_mapping", "records", "truth_gate_counts", "local_only", "production_eligible"}
    if set(body) != required or body.get("local_only") is not True or body.get("production_eligible") is not False:
        raise PrivateSourceAdapterError("V32 private source import is malformed")
    if not isinstance(body.get("records"), list) or not body["records"]:
        raise PrivateSourceAdapterError("V32 private source import has no records")
    return path, imported


def _load_record(paths: WorkspacePaths, reference: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    required = {"path", "content_digest"}
    if set(reference) != required:
        raise PrivateSourceAdapterError("V32 private source record reference is malformed")
    path = contained_path(paths.root, str(reference["path"]))
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise PrivateSourceAdapterError("V32 private source record must be an object")
    try:
        record = verify_envelope(raw, kind="v32_private_source_record")
    except InputError as exc:
        raise PrivateSourceAdapterError(f"V32 private source record content digest is invalid: {exc}") from exc
    if record["content_digest"] != reference["content_digest"]:
        raise PrivateSourceAdapterError("V32 private source record digest changed")
    _validate_schema(_require_mapping(record.get("body"), "V32 private source record body"), "v32_private_source_record.schema.json", "V32 private source record")
    return path, record


def _attest_record_assets(record: Mapping[str, Any]) -> None:
    body = _require_mapping(record.get("body"), "V32 private source record body")
    bundle = _require_mapping(body.get("bundle"), "V32 private source bundle")
    root = bundle.get("root")
    assets = body.get("assets")
    if not isinstance(root, str) or not isinstance(assets, list) or not assets:
        raise PrivateSourceAdapterError("V32 private source record has no attested asset closure")
    paths = [_canonical_relative_path(_require_mapping(asset, "private source asset").get("path"), "private source asset path") for asset in assets]
    try:
        with lock_trusted_source_root(root) as locked:
            snapshot = snapshot_trusted_files(
                locked,
                paths,
                max_file_bytes=128 * 1024 * 1024,
            )
            for asset in assets:
                item = _require_mapping(asset, "private source asset")
                observed = snapshot.file(str(item["path"]))
                if observed.size != item.get("bytes") or observed.sha256 != item.get("sha256"):
                    raise QuarantinedError("private source asset no longer matches its attested hash")
            verify_trusted_file_set_snapshot(locked, snapshot)
    except SafeFilesystemError as exc:
        raise QuarantinedError("private source asset closure cannot be safely re-attested") from exc


def _load_viewer_truth_evidence(paths: WorkspacePaths, value: str | Path, *, record_digest: str) -> dict[str, Any]:
    path = require_within(paths.root, value, "V32 viewer truth evidence")
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise PrivateSourceAdapterError("V32 viewer truth evidence must be an object")
    try:
        evidence = verify_envelope(raw, kind="v32_viewer_truth_evidence")
    except InputError as exc:
        raise PrivateSourceAdapterError(f"V32 viewer truth evidence content digest is invalid: {exc}") from exc
    body = _require_mapping(evidence.get("body"), "V32 viewer truth evidence body")
    _validate_schema(body, "v32_viewer_truth_evidence.schema.json", "V32 viewer truth evidence")
    if body.get("source_record_digest") != record_digest:
        raise PrivateSourceAdapterError("V32 viewer truth evidence is bound to another private source record")
    return body


def _truth_result_path(paths: WorkspacePaths, record_digest: str) -> Path:
    return paths.v32_private_sources / "truth" / f"truth-{_short(record_digest)}.json"


def materialize_v32_private_truth(
    paths: WorkspacePaths,
    *,
    source_import_path: str | Path,
    viewer_evidence_paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Create private truth results without executing untrusted source programs.

    ``raw_compute`` rows first re-attest their hash closure and then stop unless
    a reviewed local runner exists.  No command, program text, or package from
    the source bundle is executed.  ``viewer_measurement`` rows require a new
    envelope that binds a controlled viewer receipt, evidence digest, and a
    fresh answer with exactly the approved answer shape.
    """

    paths.ensure()
    _, imported = _load_import(paths, source_import_path)
    body = _require_mapping(imported.get("body"), "V32 private source import body")
    evidence_by_record = dict(viewer_evidence_paths or {})
    audit = {
        "audit_schema_version": 1,
        "source_import_digest": imported["content_digest"],
        "request_digest": _request_digest("v32_private_truth", source_import_digest=imported["content_digest"]),
    }
    append_event(paths, "v32_private_truth_started", audit)
    results: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    try:
        for item in body["records"]:
            entry = _require_mapping(item, "V32 private source import record entry")
            _, record = _load_record(paths, _require_mapping(entry.get("record"), "V32 record reference"))
            record_body = _require_mapping(record.get("body"), "V32 private source record body")
            truth_input = _require_mapping(record_body.get("truth_input"), "V32 truth input")
            truth_mode = truth_input.get("mode")
            result_body: dict[str, Any] = {
                "source_record_digest": record["content_digest"],
                "truth_mode": truth_mode,
                "legacy_regression": {"status": "not_compared"},
            }
            if truth_mode == "attested_compute_recompute":
                try:
                    _attest_record_assets(record)
                    # A contract program is a symbolic identifier, never a
                    # command.  The current package registers no generic V32
                    # runner, so accepting this path is intentionally blocked.
                    result_body.update(
                        {
                            "status": "blocked_reviewed_local_compute_runner_unavailable",
                            "compute_contract_digest": digest(truth_input["compute_contract"]),
                            "network_called": False,
                        }
                    )
                except Exception as exc:
                    result_body.update(
                        {
                            "status": "blocked_asset_attestation",
                            "network_called": False,
                            "blocker": type(exc).__name__,
                        }
                    )
            elif truth_mode == "viewer_evidence_required":
                evidence_path = evidence_by_record.get(record["content_digest"])
                if evidence_path is None:
                    result_body.update(
                        {
                            "status": "blocked_new_viewer_receipt_and_evidence_required",
                            "network_called": False,
                        }
                    )
                else:
                    evidence = _load_viewer_truth_evidence(paths, evidence_path, record_digest=record["content_digest"])
                    answer = _require_mapping(evidence.get("answer"), "new viewer evidence answer")
                    expected = _require_mapping(truth_input.get("expected_answer_shape"), "expected viewer answer shape")
                    expected_fields = expected.get("fields")
                    if not isinstance(expected_fields, list) or set(answer) != set(expected_fields):
                        raise PrivateSourceAdapterError("new viewer evidence answer does not match the attested answer shape")
                    regression = _require_mapping(record_body.get("legacy_regression"), "legacy regression record")
                    result_body.update(
                        {
                            "status": "verified_from_new_viewer_evidence",
                            "truth": dict(answer),
                            "viewer_receipt_digest": evidence["viewer_receipt_digest"],
                            "evidence_digest": evidence["evidence_digest"],
                            "network_called": False,
                            "legacy_regression": {
                                "status": "matched" if digest(dict(answer)) == regression["reference_answer_digest"] else "mismatched",
                                "reference_answer_digest": regression["reference_answer_digest"],
                            },
                        }
                    )
            else:
                raise PrivateSourceAdapterError("V32 private source record has an unsupported truth mode")
            status = str(result_body["status"])
            result = make_envelope(
                kind="v32_private_truth_result",
                identifier=f"truth-{_short(record['content_digest'])}",
                status=status,
                body=result_body,
                producer="qagen/0.1.0:v32_private_source_adapter",
            )
            result_path = _truth_result_path(paths, record["content_digest"])
            atomic_write_json(result_path, result, private=True)
            results.append(
                {
                    "source_record_digest": record["content_digest"],
                    "truth_result_path": str(result_path),
                    "truth_result_digest": result["content_digest"],
                    "status": status,
                }
            )
            status_counts[status] = status_counts.get(status, 0) + 1
        append_event(
            paths,
            "v32_private_truth_completed",
            {
                **audit,
                "result_count": len(results),
                "status_counts": status_counts,
                "result_set_digest": digest(results),
            },
        )
        return {
            "source_import_digest": imported["content_digest"],
            "total": len(results),
            "status_counts": status_counts,
            "results": results,
            "generation_eligible": False,
        }
    except Exception as exc:
        append_event(
            paths,
            "v32_private_truth_failed",
            {**audit, "error_class": type(exc).__name__},
        )
        raise


def load_v32_sanitized_author_brief(paths: WorkspacePaths, value: str | Path) -> dict[str, Any]:
    """Load an author-safe brief from the private registry with digest checks."""

    path = require_within(paths.root, value, "V32 sanitized author brief")
    raw = read_json(path)
    if not isinstance(raw, Mapping):
        raise PrivateSourceAdapterError("V32 sanitized author brief must be an object")
    try:
        brief = verify_envelope(raw, kind="v32_sanitized_author_brief")
    except InputError as exc:
        raise PrivateSourceAdapterError(f"V32 sanitized author brief content digest is invalid: {exc}") from exc
    _validate_schema(_require_mapping(brief.get("body"), "V32 sanitized author brief body"), "v32_sanitized_author_brief.schema.json", "V32 sanitized author brief")
    return brief


__all__ = [
    "PrivateSourceAdapterError",
    "import_v32_private_sources",
    "load_v32_sanitized_author_brief",
    "materialize_v32_private_truth",
]
