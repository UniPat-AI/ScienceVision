"""Private structural inventories derived from attested scene bytes.

This module reads only an attested private source record and a controlled
scene registry.  It never opens the historical question or answer fields, and
it intentionally omits coordinates, measured values, and source paths from
its output.  Molecular structures are parsed through Gemmi; volumetric masks
are parsed through NiBabel/NumPy.  Missing parser dependencies are a fixed
fail-closed blocker, not a reason to use a handwritten parser.
"""

from __future__ import annotations

from collections.abc import Mapping
import gzip
from io import BytesIO, StringIO
import os
from pathlib import Path
import stat
from typing import Any

from ..contracts import digest, digest_bytes, make_envelope, require_digest, verify_envelope
from ..errors import InputError
from ..paths import WorkspacePaths, require_within
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from ..viewer.batch_scene_replay import BatchSceneRegistry, BatchSceneReplayError
from .contracts import MeasurementContractError, allowed_selector_types, validate_scene_inventory_body


class SceneInventoryError(MeasurementContractError):
    """A source-bound scene cannot yield a safe selector inventory."""


_INVENTORY_KIND = "qagen_scene_inventory"
# These limits protect parsing from pathological inputs.  Large but ordinary
# molecular structures are represented as complete private selector pages,
# rather than being silently cut at the old inline-catalog limits.
_MAX_COMPONENTS = 65_536
_MAX_RESIDUES = 500_000
_MAX_ATOMS = 500_000
_SELECTOR_PAGE_SIZE = 20_000
_SELECTOR_PAGE_KIND = "qagen_scene_inventory_selector_page"
_MAX_VOLUME_DIMENSIONS = 7
_MAX_VOLUME_AXIS_SIZE = 1_000_000
_MAX_MASK_LABELS = 4_096
_MAX_NIFTI_DECOMPRESSED_BYTES = 512 * 1024 * 1024
_BATCH_INVENTORY_KIND = "qagen_scene_inventory_batch"
_BATCH_INVENTORY_VERSION = 1
_COMPARISON_FIELDS = (
    "source_record_digest",
    "target_leaf",
    "asset_set_digest",
    "asset_sha256",
    "selector_types",
    "selector_catalog",
    "selector_index",
    "backend_selector_map",
)
_FORBIDDEN_INVENTORY_KEYS = frozenset(
    {
        "answer",
        "answer_key",
        "answer_keys",
        "asset_path",
        "compute_contract",
        "question",
        "question_id",
        "raw_path",
        "reference_answer",
        "solving_approach",
        "source_identity",
        "task_id",
        "truth",
    }
)
_ITEM_BLOCKER_CODES = frozenset(
    {
        "asset_capture_limit_exceeded",
        "asset_integrity_mismatch",
        "asset_unavailable",
        "inventory_entity_limit_exceeded",
        "molecular_parser_unavailable",
        "molecular_structure_has_no_selectable_entities",
        "molecular_structure_parse_failed",
        "scene_asset_digest_mismatch",
        "scene_asset_unavailable",
        "scene_format_has_no_reviewed_inventory_parser",
        "scene_has_no_reviewed_selectors_for_leaf",
        "scene_inventory_build_failed",
        "parent_inventory_equivalence_failed",
        "source_record_asset_closure_mismatch",
        "source_record_scene_mapping_unavailable",
        "unsupported_measure_area",
        "unsupported_measure_dihedral",
        "unsupported_measure_distance",
        "unsupported_measure_volume",
        "viewer_host_unavailable",
        "volumetric_mask_label_limit_exceeded",
        "volumetric_mask_parse_failed",
        "volumetric_mask_shape_mismatch",
        "volumetric_parser_unavailable",
        "volumetric_scene_decompression_limit_exceeded",
        "volumetric_scene_parse_failed",
        "xyz_parser_unavailable",
        "xyz_structure_parse_failed",
        "xyz_structure_requires_one_frame",
    }
)


def _short(value: str) -> str:
    return value.removeprefix("sha256:")[:16]


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SceneInventoryError(f"{label}_invalid")
    return dict(value)


def _private_directory(paths: WorkspacePaths, *parts: str) -> Path:
    directory = paths.private.joinpath(*parts)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


def _require_private_regular(path: Path, label: str) -> None:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise SceneInventoryError(f"{label}_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SceneInventoryError(f"{label}_unavailable")


def _source_record(paths: WorkspacePaths, source_record_digest: str) -> dict[str, Any]:
    require_digest(source_record_digest, "source record digest")
    root = paths.v32_private_sources / "records"
    if not root.is_dir():
        raise SceneInventoryError("source_record_unavailable")
    matches: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            _require_private_regular(path, "source_record")
            raw = read_json(path)
            if not isinstance(raw, Mapping):
                continue
            envelope = verify_envelope(raw, kind="v32_private_source_record")
        except (InputError, OSError):
            continue
        if envelope.get("content_digest") == source_record_digest:
            matches.append(envelope)
    if len(matches) != 1:
        raise SceneInventoryError("source_record_ambiguous" if matches else "source_record_unavailable")
    body = matches[0].get("body")
    if not isinstance(body, Mapping):
        raise SceneInventoryError("source_record_unavailable")
    target_leaf = body.get("target_leaf")
    assets = body.get("assets")
    if not isinstance(target_leaf, str) or not isinstance(assets, list) or not assets:
        raise SceneInventoryError("source_record_unavailable")
    return {"target_leaf": target_leaf, "assets": [dict(item) for item in assets if isinstance(item, Mapping)]}


def _asset_digest(value: Any, label: str) -> str:
    """Normalize source-side raw SHA-256 strings to the envelope form."""

    if not isinstance(value, str):
        raise SceneInventoryError(f"{label}_invalid")
    return require_digest(value if value.startswith("sha256:") else f"sha256:{value}", label)


def _attested_scene_asset_closure(record: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, str]:
    """Bind the record's private asset facts to bytes just resolved by the host registry."""

    assets = record.get("assets")
    if not isinstance(assets, list):
        raise SceneInventoryError("source_record_unavailable")
    expected: dict[str, tuple[int, str]] = {}
    for asset in assets:
        if not isinstance(asset, Mapping) or not isinstance(asset.get("bytes"), int):
            raise SceneInventoryError("source_record_unavailable")
        role = asset.get("role")
        if role not in {"primary", "mask"} or role in expected:
            raise SceneInventoryError("source_record_unavailable")
        expected[str(role)] = (int(asset["bytes"]), _asset_digest(asset.get("sha256"), "source_asset_sha256"))
    primary_bytes = binding.get("asset_bytes")
    if not isinstance(primary_bytes, bytes):
        raise SceneInventoryError("scene_asset_unavailable")
    primary_digest = _asset_digest(binding.get("asset_content_digest"), "scene_primary_asset_sha256")
    if digest_bytes(primary_bytes) != primary_digest:
        raise SceneInventoryError("scene_asset_digest_mismatch")
    observed = {"primary": (len(primary_bytes), primary_digest)}
    if "mask_bytes" in binding and "mask_content_digest" in binding:
        mask_bytes = binding["mask_bytes"]
        if not isinstance(mask_bytes, bytes):
            raise SceneInventoryError("scene_asset_unavailable")
        mask_digest = _asset_digest(binding["mask_content_digest"], "scene_mask_asset_sha256")
        if digest_bytes(mask_bytes) != mask_digest:
            raise SceneInventoryError("scene_asset_digest_mismatch")
        observed["mask"] = (len(mask_bytes), mask_digest)
    if expected != observed:
        raise SceneInventoryError("source_record_asset_closure_mismatch")
    return {role: asset_digest for role, (_, asset_digest) in sorted(observed.items())}


def _gemmi_inventory(asset_bytes: bytes) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    try:
        import gemmi  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SceneInventoryError("molecular_parser_unavailable") from exc
    try:
        document = gemmi.cif.read_string(asset_bytes.decode("utf-8"))
        structure = gemmi.make_structure_from_block(document.sole_block())
    except Exception as exc:
        raise SceneInventoryError("molecular_structure_parse_failed") from exc

    components: list[dict[str, Any]] = []
    residues: list[dict[str, Any]] = []
    atoms: list[dict[str, Any]] = []
    backend: dict[str, dict[str, Any]] = {}
    component_count = residue_count = atom_count = 0
    for model_index, model in enumerate(structure, start=1):
        for chain_index, chain in enumerate(model, start=1):
            component_count += 1
            if component_count > _MAX_COMPONENTS:
                raise SceneInventoryError("inventory_entity_limit_exceeded")
            component_id = f"component-{component_count:04d}"
            anonymous_chain_id = f"chain-{component_count:04d}"
            components.append({"component_id": component_id, "chain_id": anonymous_chain_id})
            backend[component_id] = {"model_index": model_index, "chain_id": str(chain.name)}
            for residue in chain:
                residue_count += 1
                if residue_count > _MAX_RESIDUES:
                    raise SceneInventoryError("inventory_entity_limit_exceeded")
                residue_id = f"residue-{residue_count:06d}"
                insertion_code = str(residue.seqid.icode).strip()
                residue_number = int(residue.seqid.num)
                residues.append(
                    {
                        "residue_id": residue_id,
                        "component_id": component_id,
                        "residue_name": residue_id,
                        "residue_number": residue_count,
                        "insertion_code": "",
                    }
                )
                backend[residue_id] = {
                    "model_index": model_index,
                    "chain_id": str(chain.name),
                    "residue_number": residue_number,
                    "insertion_code": insertion_code,
                    "residue_name": str(residue.name) or "UNK",
                }
                for atom in residue:
                    atom_count += 1
                    if atom_count > _MAX_ATOMS:
                        raise SceneInventoryError("inventory_entity_limit_exceeded")
                    atom_id = f"atom-{atom_count:08d}"
                    atom_name = str(atom.name).strip() or "UNK"
                    altloc = str(atom.altloc).strip()
                    element = str(atom.element.name).strip() or "X"
                    atoms.append(
                        {
                            "atom_id": atom_id,
                            "residue_id": residue_id,
                            "atom_name": atom_id,
                            "element": element,
                            "coordinate_identity": f"coordinate-{atom_count:08d}",
                        }
                    )
                    backend[atom_id] = {
                        "model_index": model_index,
                        "chain_id": str(chain.name),
                        "residue_number": residue_number,
                        "insertion_code": insertion_code,
                        "atom_name": atom_name,
                        "altloc": altloc,
                    }
    if not components or not residues or not atoms:
        raise SceneInventoryError("molecular_structure_has_no_selectable_entities")
    return ({"components": components, "residues": residues, "atoms": atoms}, backend)


def _ase_xyz_inventory(asset_bytes: bytes) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Parse an XYZ scene with ASE without exposing coordinate values."""

    try:
        from ase.io import read as ase_read  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SceneInventoryError("xyz_parser_unavailable") from exc
    try:
        frames = ase_read(StringIO(asset_bytes.decode("utf-8")), format="xyz", index=":")
    except Exception as exc:
        raise SceneInventoryError("xyz_structure_parse_failed") from exc
    if not isinstance(frames, list) or len(frames) != 1:
        raise SceneInventoryError("xyz_structure_requires_one_frame")
    symbols = frames[0].get_chemical_symbols()
    if not symbols:
        raise SceneInventoryError("molecular_structure_has_no_selectable_entities")
    if len(symbols) > _MAX_ATOMS:
        raise SceneInventoryError("inventory_entity_limit_exceeded")
    component_id = "component-0001"
    residue_id = "residue-000001"
    components = [{"component_id": component_id, "chain_id": "chain-0001"}]
    residues = [
        {
            "residue_id": residue_id,
            "component_id": component_id,
            "residue_name": residue_id,
            "residue_number": 1,
            "insertion_code": "",
        }
    ]
    backend: dict[str, dict[str, Any]] = {
        component_id: {"structure_index": 0, "component_index": 1, "format": "xyz"},
        residue_id: {"structure_index": 0, "component_index": 1, "residue_index": 1, "format": "xyz"},
    }
    atoms: list[dict[str, Any]] = []
    for atom_index, symbol in enumerate(symbols, start=1):
        if not isinstance(symbol, str) or not symbol or not symbol[0].isupper():
            raise SceneInventoryError("xyz_structure_parse_failed")
        atom_id = f"atom-{atom_index:08d}"
        atoms.append(
            {
                "atom_id": atom_id,
                "residue_id": residue_id,
                "atom_name": atom_id,
                "element": symbol,
                "coordinate_identity": f"coordinate-{atom_index:08d}",
            }
        )
        backend[atom_id] = {"structure_index": 0, "atom_index": atom_index, "format": "xyz"}
    return ({"components": components, "residues": residues, "atoms": atoms}, backend)


def _nifti_image_from_bytes(nib: Any, asset_bytes: bytes, *, label: str) -> Any:
    """Decode a bounded gzip wrapper, then delegate NIfTI parsing to NiBabel."""

    payload = asset_bytes
    if asset_bytes.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=BytesIO(asset_bytes), mode="rb") as stream:
                payload = stream.read(_MAX_NIFTI_DECOMPRESSED_BYTES + 1)
        except OSError as exc:
            raise SceneInventoryError(f"{label}_parse_failed") from exc
        if len(payload) > _MAX_NIFTI_DECOMPRESSED_BYTES:
            raise SceneInventoryError("volumetric_scene_decompression_limit_exceeded")
    try:
        return nib.Nifti1Image.from_bytes(payload)
    except Exception as exc:
        raise SceneInventoryError(f"{label}_parse_failed") from exc


def _nifti_inventory(
    asset_bytes: bytes,
    mask_bytes: bytes | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        import nibabel as nib  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SceneInventoryError("volumetric_parser_unavailable") from exc
    image = _nifti_image_from_bytes(nib, asset_bytes, label="volumetric_scene")
    volume_shape = [int(item) for item in image.shape]
    if (
        len(volume_shape) < 3
        or len(volume_shape) > _MAX_VOLUME_DIMENSIONS
        or any(item < 1 or item > _MAX_VOLUME_AXIS_SIZE for item in volume_shape)
    ):
        raise SceneInventoryError("volumetric_scene_parse_failed")
    labels: list[int] = []
    layers = ["layer-0001"]
    if mask_bytes is not None:
        try:
            mask = _nifti_image_from_bytes(nib, mask_bytes, label="volumetric_mask")
            values = np.asanyarray(mask.dataobj)
        except SceneInventoryError:
            raise
        except Exception as exc:
            raise SceneInventoryError("volumetric_mask_parse_failed") from exc
        mask_shape = [int(item) for item in mask.shape]
        if len(mask_shape) < 3 or mask_shape[:3] != volume_shape[:3]:
            raise SceneInventoryError("volumetric_mask_shape_mismatch")
        layers.append("layer-0002")
    else:
        values = None
    if values is not None:
        try:
            for raw in np.unique(values):
                numeric = float(raw)
                if numeric > 0 and numeric.is_integer():
                    labels.append(int(numeric))
                    if len(labels) > _MAX_MASK_LABELS:
                        raise SceneInventoryError("volumetric_mask_label_limit_exceeded")
        except SceneInventoryError:
            raise
        except Exception as exc:
            raise SceneInventoryError("volumetric_mask_parse_failed") from exc
    labels = sorted(set(labels))
    return (
        {
            "components": [],
            "residues": [],
            "atoms": [],
            "mask_labels": labels,
            "axes": ["x", "y", "z"],
            "volume_shape": volume_shape,
            "layers": layers,
        },
        {},
    )


def _molecular_selector_types(structural: Mapping[str, list[dict[str, Any]]]) -> set[str]:
    """Advertise only selectors that have enough entities for their arity."""

    component_count = len(structural["components"])
    residue_count = len(structural["residues"])
    atom_count = len(structural["atoms"])
    available: set[str] = set()
    if component_count >= 2:
        available.add("component_pair")
    if residue_count >= 1:
        available.add("residue")
    if residue_count >= 2:
        available.add("residue_pair")
    if atom_count >= 1:
        available.add("atom")
    if atom_count >= 2:
        available.add("atom_pair")
    if atom_count >= 4:
        available.add("atom_quadruplet")
    return available


def _inventory_content(binding: Mapping[str, Any], *, target_leaf: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]], tuple[str, ...]]:
    asset_format = binding.get("asset_format")
    if asset_format == "cif":
        structural, backend = _gemmi_inventory(binding["asset_bytes"])
        catalog = {**structural, "mask_labels": [], "axes": [], "volume_shape": [], "layers": []}
        available = _molecular_selector_types(structural)
    elif asset_format == "xyz":
        structural, backend = _ase_xyz_inventory(binding["asset_bytes"])
        catalog = {**structural, "mask_labels": [], "axes": [], "volume_shape": [], "layers": []}
        available = _molecular_selector_types(structural)
    elif asset_format in {"nii", "nii.gz"}:
        catalog, backend = _nifti_inventory(binding["asset_bytes"], binding.get("mask_bytes"))
        available = {"mask_label_axis"} if catalog["mask_labels"] else set()
    else:
        raise SceneInventoryError("scene_format_has_no_reviewed_inventory_parser")
    reviewed = set(allowed_selector_types(target_leaf))
    selector_types = tuple(sorted(available & reviewed))
    if not selector_types:
        raise SceneInventoryError("scene_has_no_reviewed_selectors_for_leaf")
    return catalog, backend, selector_types


def _inventory_path(paths: WorkspacePaths, content_digest: str) -> Path:
    return _private_directory(paths, "measurement", "inventories") / f"inventory-{_short(content_digest)}.json"


def _selector_page_directory(paths: WorkspacePaths) -> Path:
    return _private_directory(paths, "measurement", "inventory-pages")


def _selector_page_identifier(
    *,
    source_record_digest: str,
    scene_registry_digest: str,
    category: str,
    page_number: int,
    entries: list[dict[str, Any]],
) -> str:
    return (
        f"selector-page-{_short(source_record_digest)}-{_short(scene_registry_digest)}-"
        f"{category}-{page_number:04d}-{_short(digest(entries))}"
    )


def _write_selector_page(
    paths: WorkspacePaths,
    *,
    source_record_digest: str,
    scene_registry_digest: str,
    anonymous_scene_ref: str,
    category: str,
    page_number: int,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    if category not in {"components", "residues", "atoms"} or not entries:
        raise SceneInventoryError("scene_inventory_build_failed")
    identifier_field = {
        "components": "component_id",
        "residues": "residue_id",
        "atoms": "atom_id",
    }[category]
    first_id = entries[0].get(identifier_field)
    last_id = entries[-1].get(identifier_field)
    if not isinstance(first_id, str) or not isinstance(last_id, str):
        raise SceneInventoryError("scene_inventory_build_failed")
    body = {
        "page_version": 1,
        "source_record_digest": source_record_digest,
        "scene_registry_digest": scene_registry_digest,
        "anonymous_scene_ref": anonymous_scene_ref,
        "category": category,
        "page_number": page_number,
        "entries": entries,
    }
    envelope = make_envelope(
        kind=_SELECTOR_PAGE_KIND,
        identifier=_selector_page_identifier(
            source_record_digest=source_record_digest,
            scene_registry_digest=scene_registry_digest,
            category=category,
            page_number=page_number,
            entries=entries,
        ),
        status="ready",
        body=body,
        producer="qagen/0.1.0:scene_inventory",
    )
    path = _selector_page_directory(paths) / f"page-{_short(envelope['content_digest'])}.json"
    atomic_write_json(path, envelope, private=True)
    return {
        "page_number": page_number,
        "entry_count": len(entries),
        "first_id": first_id,
        "last_id": last_id,
        "content_digest": envelope["content_digest"],
    }


def _page_selector_catalog(
    paths: WorkspacePaths,
    *,
    source_record_digest: str,
    scene_registry_digest: str,
    anonymous_scene_ref: str,
    catalog: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Persist complete anonymous entity pages and return a stable index.

    Inline catalogs are retained only when they fit in a single reviewed page.
    A paged catalog has *no* inline partial list, so consumers must either use
    the verified index or load every page; neither path can mistake a prefix
    for a complete scene.
    """

    result = {key: value for key, value in catalog.items()}
    index: dict[str, Any] = {"version": 1}
    for category in ("components", "residues", "atoms"):
        entries = catalog.get(category)
        if not isinstance(entries, list):
            raise SceneInventoryError("scene_inventory_build_failed")
        copied = [dict(item) for item in entries if isinstance(item, Mapping)]
        if len(copied) != len(entries):
            raise SceneInventoryError("scene_inventory_build_failed")
        if len(copied) <= _SELECTOR_PAGE_SIZE:
            result[category] = copied
            index[category] = {"mode": "inline", "count": len(copied), "pages": []}
            continue
        pages: list[dict[str, Any]] = []
        for offset in range(0, len(copied), _SELECTOR_PAGE_SIZE):
            page_number = (offset // _SELECTOR_PAGE_SIZE) + 1
            pages.append(
                _write_selector_page(
                    paths,
                    source_record_digest=source_record_digest,
                    scene_registry_digest=scene_registry_digest,
                    anonymous_scene_ref=anonymous_scene_ref,
                    category=category,
                    page_number=page_number,
                    entries=copied[offset : offset + _SELECTOR_PAGE_SIZE],
                )
            )
        result[category] = []
        index[category] = {"mode": "paged", "count": len(copied), "pages": pages}
    return result, index


def build_scene_inventory(
    paths: WorkspacePaths,
    *,
    source_record_digest: str,
    registry_path: str | Path,
    anonymous_scene_ref: str,
) -> dict[str, Any]:
    """Extract a private selector catalog from one attested controlled scene.

    The source record is used strictly to bind leaf and asset closure.  This
    function never accesses its historical question, answer, or truth input.
    """

    paths.ensure()
    record = _source_record(paths, source_record_digest)
    try:
        registry = BatchSceneRegistry.load(paths, registry_path)
        descriptor = registry.public_scene_descriptor(anonymous_scene_ref)
        binding = registry.resolve(anonymous_scene_ref)
    except BatchSceneReplayError as exc:
        raise SceneInventoryError(str(exc)) from exc
    asset_sha256 = _attested_scene_asset_closure(record, binding)
    catalog, backend, selector_types = _inventory_content(binding, target_leaf=str(record["target_leaf"]))
    paged_catalog, selector_index = _page_selector_catalog(
        paths,
        source_record_digest=source_record_digest,
        scene_registry_digest=registry.content_digest,
        anonymous_scene_ref=descriptor["anonymous_scene_ref"],
        catalog=catalog,
    )
    selection_slot_digest = descriptor.get("slot_digest")
    if selection_slot_digest is not None:
        selection_slot_digest = require_digest(selection_slot_digest, "scene registry slot digest")
    body = {
        "inventory_version": 1,
        "source_record_digest": source_record_digest,
        "target_leaf": record["target_leaf"],
        "scene_registry_digest": registry.content_digest,
        "anonymous_scene_ref": descriptor["anonymous_scene_ref"],
        "asset_set_digest": descriptor["asset_set_digest"],
        "asset_sha256": asset_sha256,
        "selector_types": list(selector_types),
        "selector_catalog": paged_catalog,
        "selector_index": selector_index,
        "backend_selector_map": backend,
        "local_only": True,
        "release_eligible": False,
    }
    if selection_slot_digest is not None:
        body["selection_slot_digest"] = selection_slot_digest
    checked = validate_scene_inventory_body(body)
    envelope = make_envelope(
        kind=_INVENTORY_KIND,
        identifier=f"inventory-{_short(source_record_digest)}-{_short(registry.content_digest)}",
        status="ready",
        body=checked,
        producer="qagen/0.1.0:scene_inventory",
    )
    path = _inventory_path(paths, envelope["content_digest"])
    atomic_write_json(path, envelope, private=True)
    return {
        "inventory_path": str(path),
        "inventory_digest": envelope["content_digest"],
        "source_record_digest": source_record_digest,
        "target_leaf": record["target_leaf"],
        "anonymous_scene_ref": descriptor["anonymous_scene_ref"],
        "selector_types": list(selector_types),
        "generation_eligible": False,
    }


def _asset_set_digest(assets: list[dict[str, Any]]) -> str:
    """Recreate the registry's path-free asset-set closure."""

    facts: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for asset in assets:
        role = asset.get("role")
        byte_count = asset.get("bytes")
        if role not in {"primary", "mask"} or role in seen_roles or not isinstance(byte_count, int) or byte_count < 1:
            raise SceneInventoryError("source_record_unavailable")
        seen_roles.add(str(role))
        facts.append(
            {
                "role": str(role),
                "bytes": byte_count,
                "sha256": _asset_digest(asset.get("sha256"), "source_asset_sha256").removeprefix("sha256:"),
            }
        )
    if "primary" not in seen_roles:
        raise SceneInventoryError("source_record_unavailable")
    facts.sort(key=lambda item: (str(item["role"]), str(item["sha256"])))
    return digest({"algorithm": "qagen.batch_scene_asset_set.v1", "assets": facts})


def _selection_batch_paths(paths: WorkspacePaths) -> list[Path]:
    """Return regular manifests below the private selection-batch root.

    Derived pilots intentionally live below ``derived-pilots/``.  A flat glob
    silently excludes them and encourages callers to bind a registry to an
    unrelated parent batch, so discovery must remain recursive and bounded by
    the private batch directory.
    """

    root = paths.selection_batches
    if not root.is_dir():
        return []
    candidates: list[Path] = []
    for path in root.rglob("*.vnext-batch.json"):
        try:
            require_within(root, path, "selection_batch")
            _require_private_regular(path, "selection_batch")
        except (InputError, SceneInventoryError):
            continue
        candidates.append(path)
    return sorted(candidates, key=lambda item: item.relative_to(root).as_posix())


def _load_selection_batch_for_registry(paths: WorkspacePaths, registry: BatchSceneRegistry) -> dict[str, Any]:
    """Find the one private compiled selection manifest locked by a registry."""

    matches: list[dict[str, Any]] = []
    for path in _selection_batch_paths(paths):
        try:
            raw = read_json(path)
            if not isinstance(raw, Mapping):
                continue
            envelope = verify_envelope(raw, kind="vnext_selection_batch_manifest")
        except (InputError, OSError):
            continue
        if envelope.get("content_digest") == registry.selection_batch_digest:
            matches.append(envelope)
    if len(matches) != 1:
        raise SceneInventoryError("selection_batch_ambiguous" if matches else "selection_batch_unavailable")
    body = matches[0].get("body")
    if not isinstance(body, Mapping) or not isinstance(body.get("rows"), list) or body.get("total") != len(body["rows"]):
        raise SceneInventoryError("selection_batch_unavailable")
    return {"content_digest": matches[0]["content_digest"], "rows": [dict(row) for row in body["rows"] if isinstance(row, Mapping)]}


def _all_v32_source_records(paths: WorkspacePaths) -> list[dict[str, Any]]:
    """Load only source digest, mapped leaf, and asset closure for the formal cohort."""

    root = paths.v32_private_sources / "records"
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name) if root.is_dir() else []:
        try:
            _require_private_regular(path, "source_record")
            raw = read_json(path)
            if not isinstance(raw, Mapping):
                continue
            envelope = verify_envelope(raw, kind="v32_private_source_record")
            digest_value = require_digest(envelope.get("content_digest"), "source record digest")
            record = _source_record(paths, digest_value)
        except (InputError, OSError, SceneInventoryError):
            continue
        records.append(
            {
                "source_record_digest": digest_value,
                "target_leaf": record["target_leaf"],
                "assets": record["assets"],
                "path": path,
            }
        )
    if not records or len({item["source_record_digest"] for item in records}) != len(records):
        raise SceneInventoryError("source_record_unavailable")
    return records


def _batch_rows_by_key(batch: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in batch["rows"]:
        row = dict(raw)
        target_leaf = row.get("target_leaf")
        assets = row.get("assets")
        slot = row.get("slot_ref")
        if not isinstance(target_leaf, str) or not isinstance(assets, list) or not isinstance(slot, Mapping):
            raise SceneInventoryError("selection_batch_unavailable")
        slot_digest = require_digest(slot.get("content_digest"), "selection batch slot digest")
        key = (target_leaf, _asset_set_digest([dict(asset) for asset in assets if isinstance(asset, Mapping)]))
        if key in result:
            raise SceneInventoryError("source_record_scene_mapping_ambiguous")
        result[key] = {"slot_digest": slot_digest, "target_leaf": target_leaf}
    return result


def _registry_scenes_by_slot(registry: BatchSceneRegistry) -> dict[str, dict[str, Any]]:
    scenes: dict[str, dict[str, Any]] = {}
    for scene_ref in registry.anonymous_scene_refs:
        descriptor = registry.public_scene_descriptor(scene_ref)
        slot_digest = require_digest(descriptor.get("slot_digest"), "registry scene slot digest")
        if slot_digest in scenes:
            raise SceneInventoryError("scene_registry_ambiguous")
        scenes[slot_digest] = descriptor
    return scenes


def _selected_source_records(
    source_records: list[dict[str, Any]],
    batch: Mapping[str, Any],
    registry: BatchSceneRegistry,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Join only the selected source rows to this registry's opaque slots.

    The formal V32 source directory contains the whole frozen cohort while a
    derived pilot may select a strict subset.  The join therefore cannot use
    the old cardinality equality.  It is still fail-closed: every selected
    row must map to exactly one attested record and exactly one registry scene.
    """

    rows_by_key = _batch_rows_by_key(batch)
    scenes_by_slot = _registry_scenes_by_slot(registry)
    if len(rows_by_key) != len(batch["rows"]) or len(scenes_by_slot) != len(rows_by_key):
        raise SceneInventoryError("source_record_scene_mapping_unavailable")
    if {row["slot_digest"] for row in rows_by_key.values()} != set(scenes_by_slot):
        raise SceneInventoryError("source_record_scene_mapping_unavailable")

    sources_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for source in source_records:
        key = (str(source["target_leaf"]), _asset_set_digest(list(source["assets"])))
        if key in sources_by_key:
            raise SceneInventoryError("source_record_scene_mapping_ambiguous")
        sources_by_key[key] = source

    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key, row in rows_by_key.items():
        source = sources_by_key.get(key)
        descriptor = scenes_by_slot.get(row["slot_digest"])
        if source is None or descriptor is None or descriptor.get("asset_set_digest") != key[1]:
            raise SceneInventoryError("source_record_scene_mapping_unavailable")
        scene_ref = descriptor.get("anonymous_scene_ref")
        if not isinstance(scene_ref, str) or not scene_ref.startswith("scene://"):
            raise SceneInventoryError("source_record_scene_mapping_unavailable")
        selected.append((source, {**row, "anonymous_scene_ref": scene_ref}))
    return sorted(selected, key=lambda item: str(item[1]["slot_digest"]))


def _normalized_selector_index(body: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Compare selector paging independent of registry-bound page digests."""

    catalog = _require_mapping(body.get("selector_catalog"), "scene_inventory_selector_catalog")
    raw_index = body.get("selector_index")
    result: dict[str, dict[str, Any]] = {}
    if raw_index is None:
        for category in ("components", "residues", "atoms"):
            entries = catalog.get(category)
            if not isinstance(entries, list):
                raise SceneInventoryError("scene_inventory_unavailable")
            result[category] = {"mode": "inline", "count": len(entries), "page_entry_counts": []}
        return result

    index = _require_mapping(raw_index, "scene_inventory_selector_index")
    for category in ("components", "residues", "atoms"):
        page_index = _require_mapping(index.get(category), f"scene_inventory_selector_index_{category}")
        mode = page_index.get("mode")
        count = page_index.get("count")
        pages = page_index.get("pages")
        if mode not in {"inline", "paged"} or not isinstance(count, int) or isinstance(count, bool) or count < 0 or not isinstance(pages, list):
            raise SceneInventoryError("scene_inventory_unavailable")
        page_counts: list[int] = []
        for page in pages:
            metadata = _require_mapping(page, "scene_inventory_selector_page")
            entry_count = metadata.get("entry_count")
            if not isinstance(entry_count, int) or isinstance(entry_count, bool) or entry_count < 1:
                raise SceneInventoryError("scene_inventory_unavailable")
            page_counts.append(entry_count)
        result[category] = {"mode": mode, "count": count, "page_entry_counts": page_counts}
    return result


def _inventory_equivalence(parent: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    """Return the scientific-selector fields that differ across replays."""

    mismatches: list[str] = []
    for field in _COMPARISON_FIELDS:
        if field == "selector_index":
            equal = _normalized_selector_index(parent) == _normalized_selector_index(current)
        elif field == "selector_types":
            parent_types = parent.get(field)
            current_types = current.get(field)
            equal = isinstance(parent_types, list) and isinstance(current_types, list) and sorted(parent_types) == sorted(current_types)
        else:
            equal = parent.get(field) == current.get(field)
        if not equal:
            mismatches.append(field)
    return mismatches


def _validate_inventory_batch_body(value: Any) -> dict[str, Any]:
    """Validate a private inventory-batch receipt before it can become a lock."""

    body = _require_mapping(value, "scene_inventory_batch")
    required = {
        "batch_version",
        "scene_registry_digest",
        "selection_batch_digest",
        "total",
        "ready_count",
        "blocked_count",
        "mapping_complete",
        "results",
        "leak_scan",
        "local_only",
        "release_eligible",
    }
    optional = {"parent_provenance"}
    if set(body) != required and set(body) != required | optional:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    if body.get("batch_version") != _BATCH_INVENTORY_VERSION:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    try:
        require_digest(body.get("scene_registry_digest"), "scene inventory batch registry digest")
        require_digest(body.get("selection_batch_digest"), "scene inventory batch selection digest")
    except InputError as exc:
        raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
    for field in ("total", "ready_count", "blocked_count"):
        item = body.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
    if body["total"] < 1 or body["total"] != body["ready_count"] + body["blocked_count"]:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    if not isinstance(body.get("mapping_complete"), bool) or body.get("local_only") is not True or body.get("release_eligible") is not False:
        raise SceneInventoryError("scene_inventory_batch_unavailable")

    raw_results = body.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != body["total"]:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    results: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_slots: set[str] = set()
    seen_scenes: set[str] = set()
    seen_inventories: set[str] = set()
    for raw in raw_results:
        result = _require_mapping(raw, "scene_inventory_batch_result")
        status = result.get("status")
        optional_slot = {"selection_slot_digest"} if "selection_slot_digest" in result else set()
        if status == "ready":
            expected = {"source_record_digest", "anonymous_scene_ref", "status", "inventory_digest"} | optional_slot
        elif status == "blocked":
            expected = {"source_record_digest", "anonymous_scene_ref", "status", "blocker"} | optional_slot
        else:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        if set(result) != expected:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        try:
            source_digest = require_digest(result.get("source_record_digest"), "scene inventory batch source digest")
        except InputError as exc:
            raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
        scene_ref = result.get("anonymous_scene_ref")
        if not isinstance(scene_ref, str) or not scene_ref.startswith("scene://") or len(scene_ref) <= len("scene://"):
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        if source_digest in seen_sources or scene_ref in seen_scenes:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        seen_sources.add(source_digest)
        seen_scenes.add(scene_ref)
        if "selection_slot_digest" in result:
            try:
                slot_digest = require_digest(result.get("selection_slot_digest"), "scene inventory batch slot digest")
            except InputError as exc:
                raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
            if slot_digest in seen_slots:
                raise SceneInventoryError("scene_inventory_batch_unavailable")
            seen_slots.add(slot_digest)
        if status == "ready":
            try:
                inventory_digest = require_digest(result.get("inventory_digest"), "scene inventory batch inventory digest")
            except InputError as exc:
                raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
            if inventory_digest in seen_inventories:
                raise SceneInventoryError("scene_inventory_batch_unavailable")
            seen_inventories.add(inventory_digest)
        elif not isinstance(result.get("blocker"), str) or result["blocker"] not in _ITEM_BLOCKER_CODES:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        results.append(result)
    if results != sorted(results, key=lambda item: str(item["source_record_digest"])):
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    if sum(result["status"] == "ready" for result in results) != body["ready_count"]:
        raise SceneInventoryError("scene_inventory_batch_unavailable")

    leak_scan = _require_mapping(body.get("leak_scan"), "scene_inventory_batch_leak_scan")
    allowed_leak_fields = {"status", "checked_inventory_count", "violation_count"}
    if set(leak_scan) != allowed_leak_fields and set(leak_scan) != allowed_leak_fields | {"blocker"}:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    if leak_scan.get("status") not in {"passed", "failed"}:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    for field in ("checked_inventory_count", "violation_count"):
        item = leak_scan.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
    if leak_scan["status"] == "passed" and (
        leak_scan["violation_count"] != 0 or leak_scan["checked_inventory_count"] != body["ready_count"]
    ):
        raise SceneInventoryError("scene_inventory_batch_unavailable")

    parent = body.get("parent_provenance")
    if parent is not None:
        _validate_parent_provenance(parent)
    return body


def _validate_parent_provenance(value: Any) -> None:
    provenance = _require_mapping(value, "scene_inventory_batch_parent_provenance")
    required = {
        "inventory_batch_digest",
        "selection_batch_digest",
        "scene_registry_digest",
        "comparison",
        "results",
    }
    if set(provenance) != required:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    try:
        for field in ("inventory_batch_digest", "selection_batch_digest", "scene_registry_digest"):
            require_digest(provenance.get(field), f"scene inventory parent {field}")
    except InputError as exc:
        raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
    comparison = _require_mapping(provenance.get("comparison"), "scene_inventory_batch_parent_comparison")
    expected_comparison = {"status", "checked_count", "mismatch_count", "required_fields"}
    if set(comparison) != expected_comparison or comparison.get("status") not in {"passed", "failed"}:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    if comparison.get("required_fields") != list(_COMPARISON_FIELDS):
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    for field in ("checked_count", "mismatch_count"):
        item = comparison.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
    entries = provenance.get("results")
    if not isinstance(entries, list) or comparison["checked_count"] != len(entries):
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    mismatch_count = 0
    seen_sources: set[str] = set()
    for raw in entries:
        item = _require_mapping(raw, "scene_inventory_batch_parent_result")
        if set(item) != {"source_record_digest", "parent_inventory_digest", "current_inventory_digest", "status", "mismatched_fields"}:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        try:
            source = require_digest(item.get("source_record_digest"), "scene inventory parent source digest")
            require_digest(item.get("parent_inventory_digest"), "scene inventory parent inventory digest")
            require_digest(item.get("current_inventory_digest"), "scene inventory current inventory digest")
        except InputError as exc:
            raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
        if source in seen_sources or item.get("status") not in {"equivalent", "mismatch"}:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        seen_sources.add(source)
        fields = item.get("mismatched_fields")
        if not isinstance(fields, list) or len(fields) != len(set(fields)) or any(field not in _COMPARISON_FIELDS for field in fields):
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        if (item["status"] == "equivalent" and fields) or (item["status"] == "mismatch" and not fields):
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        mismatch_count += item["status"] == "mismatch"
    if mismatch_count != comparison["mismatch_count"] or (
        comparison["status"] == "passed" and mismatch_count != 0
    ):
        raise SceneInventoryError("scene_inventory_batch_unavailable")


def load_v32_scene_inventory_batch(
    paths: WorkspacePaths,
    value: str | Path,
    *,
    expected_content_digest: str | None = None,
) -> dict[str, Any]:
    """Load one explicit inventory-batch receipt without directory selection.

    Callers must provide a receipt path from an approved lock.  This loader
    never searches inventory-batch directories or chooses a recent receipt.
    """

    if expected_content_digest is not None:
        try:
            require_digest(expected_content_digest, "scene inventory batch expected digest")
        except InputError as exc:
            raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
    raw = Path(value).expanduser()
    try:
        # Approved lock bundles persist private-relative references so their
        # digest-bound plans do not embed machine-specific workspace paths.
        # Absolute paths remain supported for local tooling and existing
        # callers, but both forms must resolve below the private root.
        candidate = raw if raw.is_absolute() else paths.private / raw
        path = require_within(paths.private, candidate, "scene inventory batch")
        _require_private_regular(path, "scene_inventory_batch")
        document = read_json(path)
    except (InputError, OSError, SceneInventoryError) as exc:
        raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
    if not isinstance(document, Mapping):
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    try:
        envelope = verify_envelope(document, kind=_BATCH_INVENTORY_KIND)
    except InputError as exc:
        raise SceneInventoryError("scene_inventory_batch_unavailable") from exc
    if expected_content_digest is not None and envelope.get("content_digest") != expected_content_digest:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    body = _validate_inventory_batch_body(envelope.get("body"))
    expected_status = "ready" if body["mapping_complete"] and body["blocked_count"] == 0 and body["leak_scan"]["status"] == "passed" else "blocked"
    if envelope.get("status") != expected_status:
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    return {"path": path, "content_digest": envelope["content_digest"], "status": envelope["status"], "body": body}


def _parent_inventory_results(paths: WorkspacePaths, value: str | Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    parent = load_v32_scene_inventory_batch(paths, value)
    if parent["status"] != "ready":
        raise SceneInventoryError("scene_inventory_batch_unavailable")
    parent_results: dict[str, dict[str, Any]] = {}
    for result in parent["body"]["results"]:
        if result["status"] != "ready":
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        source = str(result["source_record_digest"])
        if source in parent_results:
            raise SceneInventoryError("scene_inventory_batch_unavailable")
        parent_results[source] = result
    return parent, parent_results


def _forbidden_inventory_data(paths: WorkspacePaths, source_records: list[dict[str, Any]]) -> set[str]:
    """Collect internal IDs and paths solely for a private leak scan."""

    forbidden: set[str] = set()
    for source in source_records:
        for asset in source["assets"]:
            value = asset.get("path")
            if isinstance(value, str) and value:
                forbidden.add(value)
        try:
            raw = read_json(source["path"])
            body = raw.get("body") if isinstance(raw, Mapping) else None
            identity = body.get("source_identity") if isinstance(body, Mapping) else None
            if isinstance(identity, Mapping):
                for value in identity.values():
                    if isinstance(value, str) and value:
                        forbidden.add(value)
        except OSError as exc:
            raise SceneInventoryError("source_record_unavailable") from exc
    return forbidden


def _inventory_leak_scan(paths: WorkspacePaths, results: list[dict[str, Any]], source_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Check generated private inventories without recording any secret literal."""

    forbidden_literals = _forbidden_inventory_data(paths, source_records)
    violations = 0

    def visit(value: Any) -> None:
        nonlocal violations
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if isinstance(key, str) and key.casefold() in _FORBIDDEN_INVENTORY_KEYS:
                    violations += 1
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        elif isinstance(value, str) and any(token in value for token in forbidden_literals):
            violations += 1

    checked = 0
    for result in results:
        if result["status"] != "ready":
            continue
        path = result.get("inventory_path")
        if not isinstance(path, Path):
            raise SceneInventoryError("scene_inventory_unavailable")
        loaded = load_scene_inventory(paths, path)
        visit(loaded["body"])
        for page in loaded.get("selector_pages", []):
            visit(page)
        checked += 1
    return {
        "status": "passed" if violations == 0 else "failed",
        "checked_inventory_count": checked,
        "violation_count": violations,
    }


def _item_blocker(error: BaseException) -> str:
    """Return a fixed item-level blocker without preserving exception text."""

    candidate = str(error) if isinstance(error, SceneInventoryError) else ""
    return candidate if candidate in _ITEM_BLOCKER_CODES else "scene_inventory_build_failed"


def _inventory_batch_path(paths: WorkspacePaths, content_digest: str) -> Path:
    return _private_directory(paths, "measurement", "inventory-batches") / f"batch-{_short(content_digest)}.json"


def materialize_v32_scene_inventories(
    paths: WorkspacePaths,
    *,
    registry_path: str | Path,
    parent_inventory_batch_path: str | Path | None = None,
) -> dict[str, Any]:
    """Materialize one selector inventory per selected V32 source record.

    Records are joined to frozen registry scenes only through their reviewed
    target leaf and path-free role/size/SHA-256 asset closure. Historical
    question, answer, compute-contract, and source task fields never enter
    the join or the returned audit artifact.  A derived pilot may point at a
    strict subset of formal source records; when a parent receipt is supplied,
    each rebuilt inventory must prove selector equivalence before it is ready.
    """

    paths.ensure()
    try:
        registry = BatchSceneRegistry.load(paths, registry_path)
        batch = _load_selection_batch_for_registry(paths, registry)
    except BatchSceneReplayError as exc:
        raise SceneInventoryError(str(exc)) from exc
    source_records = _all_v32_source_records(paths)
    selected = _selected_source_records(source_records, batch, registry)
    parent: dict[str, Any] | None = None
    parent_results: dict[str, dict[str, Any]] = {}
    if parent_inventory_batch_path is not None:
        parent, parent_results = _parent_inventory_results(paths, parent_inventory_batch_path)

    results: list[dict[str, Any]] = []
    parent_comparisons: list[dict[str, Any]] = []
    for source, row in selected:
        scene_ref = str(row["anonymous_scene_ref"])
        result: dict[str, Any] = {
            "source_record_digest": source["source_record_digest"],
            "selection_slot_digest": row["slot_digest"],
            "anonymous_scene_ref": scene_ref,
        }
        try:
            built = build_scene_inventory(
                paths,
                source_record_digest=str(source["source_record_digest"]),
                registry_path=registry.path,
                anonymous_scene_ref=scene_ref,
            )
            inventory_path = Path(str(built["inventory_path"]))
            inventory_digest = str(built["inventory_digest"])
            current_inventory = load_scene_inventory(paths, inventory_path)
            if current_inventory["content_digest"] != inventory_digest:
                raise SceneInventoryError("scene_inventory_build_failed")

            if parent is not None:
                parent_result = parent_results.get(str(source["source_record_digest"]))
                if parent_result is None:
                    raise SceneInventoryError("parent_inventory_equivalence_failed")
                parent_inventory = load_scene_inventory_by_digest(paths, str(parent_result["inventory_digest"]))
                mismatched_fields = _inventory_equivalence(parent_inventory["body"], current_inventory["body"])
                comparison = {
                    "source_record_digest": source["source_record_digest"],
                    "parent_inventory_digest": parent_inventory["content_digest"],
                    "current_inventory_digest": current_inventory["content_digest"],
                    "status": "equivalent" if not mismatched_fields else "mismatch",
                    "mismatched_fields": mismatched_fields,
                }
                parent_comparisons.append(comparison)
                if mismatched_fields:
                    raise SceneInventoryError("parent_inventory_equivalence_failed")
            result.update(
                {
                    "status": "ready",
                    "inventory_digest": inventory_digest,
                    "inventory_path": inventory_path,
                }
            )
        except Exception as exc:  # Per-item failures must not suppress the batch receipt.
            result.pop("inventory_digest", None)
            result.pop("inventory_path", None)
            result.update(
                {
                    "status": "blocked",
                    "blocker": _item_blocker(exc),
                }
            )
        results.append(result)
    results.sort(key=lambda item: str(item["source_record_digest"]))
    mapping_complete = len(selected) == len(batch["rows"])
    try:
        leak_scan = _inventory_leak_scan(paths, results, [source for source, _row in selected])
    except Exception:  # The aggregate must still document a failed leak scan.
        leak_scan = {
            "status": "failed",
            "checked_inventory_count": 0,
            "violation_count": 0,
            "blocker": "inventory_leak_scan_failed",
        }
    ready_count = sum(item["status"] == "ready" for item in results)
    blocked_count = len(results) - ready_count
    receipt_results = [
        {
            key: value
            for key, value in item.items()
            if key
            in {
                "source_record_digest",
                "selection_slot_digest",
                "anonymous_scene_ref",
                "status",
                "inventory_digest",
                "blocker",
            }
        }
        for item in results
    ]
    body = {
        "batch_version": 1,
        "scene_registry_digest": registry.content_digest,
        "selection_batch_digest": registry.selection_batch_digest,
        "total": len(results),
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "mapping_complete": mapping_complete,
        "results": receipt_results,
        "leak_scan": leak_scan,
        "local_only": True,
        "release_eligible": False,
    }
    if parent is not None:
        parent_comparisons.sort(key=lambda item: str(item["source_record_digest"]))
        mismatch_count = sum(item["status"] == "mismatch" for item in parent_comparisons)
        body["parent_provenance"] = {
            "inventory_batch_digest": parent["content_digest"],
            "selection_batch_digest": parent["body"]["selection_batch_digest"],
            "scene_registry_digest": parent["body"]["scene_registry_digest"],
            "comparison": {
                "status": "passed" if mismatch_count == 0 and len(parent_comparisons) == len(selected) else "failed",
                "checked_count": len(parent_comparisons),
                "mismatch_count": mismatch_count,
                "required_fields": list(_COMPARISON_FIELDS),
            },
            "results": parent_comparisons,
        }
    status = "ready" if mapping_complete and blocked_count == 0 and leak_scan["status"] == "passed" else "blocked"
    envelope = make_envelope(
        kind=_BATCH_INVENTORY_KIND,
        identifier=f"v32-scene-inventories-{_short(registry.content_digest)}",
        status=status,
        body=body,
        producer="qagen/0.1.0:scene_inventory",
    )
    receipt_path = _inventory_batch_path(paths, envelope["content_digest"])
    atomic_write_json(receipt_path, envelope, private=True)
    append_event(
        paths,
        "v32_scene_inventory_batch_completed",
        {
            "scene_registry_digest": registry.content_digest,
            "selection_batch_digest": registry.selection_batch_digest,
            "total": len(results),
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "leak_scan_status": leak_scan["status"],
            "inventory_batch_digest": envelope["content_digest"],
        },
    )
    response = {
        "status": status,
        "inventory_batch_path": str(receipt_path),
        "inventory_batch_digest": envelope["content_digest"],
        "total": len(results),
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "leak_scan": leak_scan,
        "generation_eligible": False,
    }
    if parent is not None:
        response["parent_comparison"] = body["parent_provenance"]["comparison"]
    return response


def _selector_page_by_digest(paths: WorkspacePaths, content_digest: str) -> dict[str, Any]:
    require_digest(content_digest, "scene inventory selector page digest")
    root = _selector_page_directory(paths)
    matches: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            _require_private_regular(path, "scene_inventory")
            raw = read_json(path)
            if not isinstance(raw, Mapping):
                continue
            envelope = verify_envelope(raw, kind=_SELECTOR_PAGE_KIND)
        except (InputError, OSError):
            continue
        if envelope.get("content_digest") == content_digest:
            matches.append(envelope)
    if len(matches) != 1:
        raise SceneInventoryError("scene_inventory_unavailable")
    return matches[0]


def _validate_selector_page_closure(paths: WorkspacePaths, body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Verify that every declared paged range has a matching private page.

    The main inventory never accepts a page reference merely because its
    metadata looks plausible.  This closes the gap between the stable index
    and the persisted anonymous selector payload.
    """

    raw_index = body.get("selector_index")
    if raw_index is None:
        return []
    index = _require_mapping(raw_index, "scene inventory selector_index")
    selector_catalog = _require_mapping(body.get("selector_catalog"), "scene inventory selector_catalog")
    loaded_pages: list[dict[str, Any]] = []
    identifier_fields = {
        "components": "component_id",
        "residues": "residue_id",
        "atoms": "atom_id",
    }
    for category, identifier_field in identifier_fields.items():
        category_index = _require_mapping(index.get(category), f"scene inventory selector_index.{category}")
        if category_index.get("mode") == "inline":
            continue
        pages = category_index.get("pages")
        if not isinstance(pages, list) or selector_catalog.get(category):
            raise SceneInventoryError("scene_inventory_unavailable")
        for metadata in pages:
            page = _require_mapping(metadata, "scene inventory selector page metadata")
            envelope = _selector_page_by_digest(paths, str(page.get("content_digest")))
            if envelope.get("status") != "ready":
                raise SceneInventoryError("scene_inventory_unavailable")
            page_body = _require_mapping(envelope.get("body"), "scene inventory selector page")
            if (
                page_body.get("page_version") != 1
                or page_body.get("source_record_digest") != body.get("source_record_digest")
                or page_body.get("scene_registry_digest") != body.get("scene_registry_digest")
                or page_body.get("anonymous_scene_ref") != body.get("anonymous_scene_ref")
                or page_body.get("category") != category
                or page_body.get("page_number") != page.get("page_number")
            ):
                raise SceneInventoryError("scene_inventory_unavailable")
            entries = page_body.get("entries")
            if not isinstance(entries, list) or len(entries) != page.get("entry_count") or not entries:
                raise SceneInventoryError("scene_inventory_unavailable")
            identifiers = [item.get(identifier_field) for item in entries if isinstance(item, Mapping)]
            if len(identifiers) != len(entries) or identifiers[0] != page.get("first_id") or identifiers[-1] != page.get("last_id"):
                raise SceneInventoryError("scene_inventory_unavailable")
            loaded_pages.append(page_body)
    return loaded_pages


def load_scene_inventory(paths: WorkspacePaths, value: str | Path) -> dict[str, Any]:
    """Load one 0600 private inventory with its digest and body revalidated."""

    raw = Path(value)
    try:
        path = require_within(paths.private, raw, "scene inventory")
        _require_private_regular(path, "scene_inventory")
        document = read_json(path)
    except (InputError, OSError) as exc:
        raise SceneInventoryError("scene_inventory_unavailable") from exc
    if not isinstance(document, Mapping):
        raise SceneInventoryError("scene_inventory_unavailable")
    try:
        envelope = verify_envelope(document, kind=_INVENTORY_KIND)
    except InputError as exc:
        raise SceneInventoryError("scene_inventory_unavailable") from exc
    if envelope.get("status") != "ready":
        raise SceneInventoryError("scene_inventory_unavailable")
    body = envelope.get("body")
    if not isinstance(body, Mapping):
        raise SceneInventoryError("scene_inventory_unavailable")
    checked = validate_scene_inventory_body(body)
    pages = _validate_selector_page_closure(paths, checked)
    return {"path": path, "content_digest": envelope["content_digest"], "body": checked, "selector_pages": pages}


def load_scene_inventory_by_digest(paths: WorkspacePaths, content_digest: str) -> dict[str, Any]:
    """Find exactly one local inventory by its immutable digest."""

    require_digest(content_digest, "scene inventory digest")
    root = paths.private / "measurement" / "inventories"
    # Inventory paths are content-addressed by the first 16 hexadecimal
    # characters of the envelope digest.  Resolving that path directly keeps
    # a live slot from validating and deep-copying every historical inventory
    # merely to find its already-locked receipt.  The full digest is still
    # verified by ``load_scene_inventory`` before anything is returned.
    candidate = root / f"inventory-{_short(content_digest)}.json"
    try:
        loaded = load_scene_inventory(paths, candidate)
    except SceneInventoryError as exc:
        raise SceneInventoryError("scene_inventory_unavailable") from exc
    if loaded["content_digest"] != content_digest:
        raise SceneInventoryError("scene_inventory_unavailable")
    return loaded


__all__ = [
    "SceneInventoryError",
    "build_scene_inventory",
    "load_scene_inventory",
    "load_scene_inventory_by_digest",
    "load_v32_scene_inventory_batch",
    "materialize_v32_scene_inventories",
]
