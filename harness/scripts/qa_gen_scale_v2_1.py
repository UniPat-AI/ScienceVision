"""Build the deterministic ScienceVision scale-v2.1 candidate dataset.

The builder is intentionally fail-closed. It verifies locked inputs, computes
new geometry ground truth from local assets, validates every emitted task, and
publishes only after all partitions and receipts are complete.

Runtime dependencies: gemmi, nibabel, numpy, rdkit, and jsonschema.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


DATASET = "scale-v2.1"
BUILD_SCHEMA = "sciencevision.qa_gen.scale_v2_1.build.v1"
LOCK_SCHEMA = "sciencevision.qa_gen.scale_v2_1.source_lock.v1"
LEDGER_SCHEMA = "sciencevision.qa_gen.scale_v2_1.ledger.v1"
RECEIPT_SCHEMA = "sciencevision.qa_gen.scale_v2_1.oracle_receipt.v1"
RUBRIC_SCHEMA = "sciencevision.generic_deterministic_rubric.v1"
GENERATED_AT_DEFAULT = "2026-08-10T00:00:00Z"

COLLISION_ORACLE = "collision_vdw_overlap.v2.1.0"
CT_ORACLE = "nifti_target_label_ras_geometry.v2.1.1"
BINDING_ORACLE = "explicit_atom_distance.v2.1.0"
CONFORMATION_ORACLE = "ordered_xyz_dihedral.v2.1.0"
REUSE_ORACLE = "locked_scale_v2_reuse.v2.1.0"
QUARANTINE_ORACLE = "quarantine_mapping.v2.1.0"

COLLISION_THRESHOLD_A = 0.4
COLLISION_ROBUST_MARGIN_A = 0.1
CONFORMATION_BOUNDARY_MARGIN_DEG = 10.0
POLAR_ELEMENTS = frozenset({"N", "O", "S"})

SOURCE_DIR = Path("QA-Gen/tasks/scale-v1-taxonomy-input-20260805")
V2_DIR = Path("QA-Gen/tasks/scale-v2")
QC_DIR = Path("outputs/qa-qc/scale-v1-reviewed-20260808")
CHEM_RECEIPT = Path("outputs/qa-qc/scale-v1-chemistry-compute-20260808/results.json")
M3D_MATCHED_INDEX = Path("datasets/_matched/m3d-refseg.json")
V1_LOCK = Path("docs/qa-gen/SCALE_V1_REVIEWED_20260808_LOCK.json")
TASK_SCHEMA = Path(
    "QA-Gen/ScienceVisionTaskGenerator/schemas/sciencevision_task.schema.json"
)


class BuildError(RuntimeError):
    """A condition that must prevent publication."""


def _stable_json(value: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(repo_root: Path, relative_path: Path) -> dict[str, Any]:
    path = repo_root / relative_path
    if not path.is_file():
        raise BuildError(f"required input is missing: {relative_path}")
    return {
        "path": relative_path.as_posix(),
        "sha256": _sha256_file(path),
        "byte_size": path.stat().st_size,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise BuildError(f"{path}:{line_number}: expected JSON object")
        values.append(value)
    return values


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(_stable_json(value) + "\n" for value in values).encode("utf-8")
    path.write_bytes(data)


def _family(task: dict[str, Any]) -> str:
    return str((task.get("knowledge") or {}).get("direction_id") or "")


def _task_hash(task: dict[str, Any]) -> str:
    return _sha256_bytes((_stable_json(task) + "\n").encode("utf-8"))


def _clean_char(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _repo_path(value: Any) -> str:
    path = str(value or "").strip().lstrip("/")
    if not path or path.startswith("../") or "/../" in path or Path(path).is_absolute():
        raise BuildError(f"invalid repo-relative asset path: {value!r}")
    return path


class AssetHasher:
    def __init__(self, asset_root: Path) -> None:
        self.asset_root = asset_root.resolve()
        self._cache: dict[str, dict[str, Any]] = {}

    def record(self, repo_path: Any) -> dict[str, Any]:
        relative = _repo_path(repo_path)
        if relative not in self._cache:
            path = (self.asset_root / relative).resolve()
            try:
                path.relative_to(self.asset_root)
            except ValueError as exc:
                raise BuildError(f"asset escapes root: {relative}") from exc
            if not path.is_file():
                raise BuildError(f"asset is missing: {relative}")
            self._cache[relative] = {
                "repo_path": relative,
                "sha256": _sha256_file(path),
                "byte_size": path.stat().st_size,
            }
        return copy.deepcopy(self._cache[relative])

    @property
    def records(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(self._cache[key]) for key in sorted(self._cache)]

    def verify(self) -> None:
        for relative, expected in sorted(self._cache.items()):
            path = self.asset_root / relative
            actual_size = path.stat().st_size if path.is_file() else None
            actual_hash = _sha256_file(path) if path.is_file() else None
            if (
                actual_size != expected["byte_size"]
                or actual_hash != expected["sha256"]
            ):
                raise BuildError(f"asset changed after hashing: {relative}")


def _normalize_asset(asset: dict[str, Any], hasher: AssetHasher) -> dict[str, Any]:
    result = copy.deepcopy(asset)
    primary = hasher.record(result.get("repo_path") or result.get("path"))
    result["repo_path"] = primary["repo_path"]
    result["path"] = primary["repo_path"]
    result["sha256"] = primary["sha256"]
    result["byte_size"] = primary["byte_size"]
    result["exists_on_disk"] = True
    if result.get("mask_repo_path") or result.get("mask_path"):
        mask = hasher.record(result.get("mask_repo_path") or result.get("mask_path"))
        result["mask_repo_path"] = mask["repo_path"]
        result["mask_path"] = mask["repo_path"]
        result["mask_sha256"] = mask["sha256"]
        result["mask_byte_size"] = mask["byte_size"]
    return result


def _normalize_task_assets(
    task: dict[str, Any], hasher: AssetHasher
) -> list[dict[str, Any]]:
    assets = task.get("assets")
    source_assets = (
        assets if isinstance(assets, list) and assets else [task.get("asset")]
    )
    normalized = [
        _normalize_asset(asset, hasher)
        for asset in source_assets
        if isinstance(asset, dict)
    ]
    if not normalized:
        raise BuildError(f"{task.get('task_id')}: no asset objects")
    task["assets"] = normalized
    task["asset"] = copy.deepcopy(normalized[0])
    task.pop("viewer_url", None)
    hashes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for asset in normalized:
        for prefix in ("", "mask_"):
            key = f"{prefix}repo_path"
            if key not in asset:
                continue
            path = str(asset[key])
            if path in seen:
                continue
            seen.add(path)
            hashes.append(hasher.record(path))
    task["asset_hashes"] = hashes
    return hashes


def _verify_v1_lock(repo_root: Path) -> dict[str, Any]:
    lock_path = repo_root / V1_LOCK
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    for key in ("source_files", "qc_files"):
        for item in lock.get(key, []):
            path_keys = [
                name for name in item if name.endswith("_path") or name == "path"
            ]
            for path_key in path_keys:
                hash_key = (
                    "sha256"
                    if path_key == "path"
                    else path_key.replace("_path", "_sha256")
                )
                relative = Path(item[path_key])
                actual = _file_record(repo_root, relative)
                expected = item.get(hash_key)
                if actual["sha256"] != expected:
                    raise BuildError(
                        f"locked input changed: {relative}: expected {expected}, got {actual['sha256']}"
                    )
                entries.append(actual)
    return {
        "path": V1_LOCK.as_posix(),
        "sha256": _sha256_file(lock_path),
        "verified_files": sorted(entries, key=lambda item: item["path"]),
    }


def _load_sources(
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for domain in ("biology", "chemistry", "medical"):
        path = (
            repo_root
            / SOURCE_DIR
            / f"ScienceVision_scale-v1-{domain}_final_tasks.jsonl"
        )
        tasks = _read_jsonl(path)
        by_domain[domain] = tasks
        for task in tasks:
            task_id = str(task.get("task_id") or "")
            if not task_id or task_id in by_id:
                raise BuildError(f"duplicate or empty source task id: {task_id!r}")
            by_id[task_id] = task
    if len(by_id) != 1567:
        raise BuildError(f"expected 1567 scale-v1 tasks, found {len(by_id)}")
    return by_id, by_domain


def _load_v2(
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    candidate: dict[str, dict[str, Any]] = {}
    quarantine: dict[str, dict[str, Any]] = {}
    for domain in ("biology", "chemistry", "medical"):
        candidate_path = (
            repo_root
            / V2_DIR
            / f"ScienceVision_scale-v2-{domain}_candidate_tasks.jsonl"
        )
        quarantine_path = (
            repo_root
            / V2_DIR
            / "quarantine"
            / f"ScienceVision_scale-v2-{domain}_quarantine_tasks.jsonl"
        )
        for task in _read_jsonl(candidate_path):
            candidate[str(task["task_id"])] = task
        for task in _read_jsonl(quarantine_path):
            quarantine[str(task["task_id"])] = task
    return candidate, quarantine


def _load_ct_target_bindings(
    repo_root: Path, medical_sources: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    document = json.loads((repo_root / M3D_MATCHED_INDEX).read_text(encoding="utf-8"))
    records = document.get("records")
    if not isinstance(records, list) or len(records) != 2777:
        raise BuildError("M3D matched index must contain exactly 2777 records")
    by_qid: dict[str, dict[str, Any]] = {}
    for record in records:
        qid = str(record.get("qid") or "")
        if not qid or qid in by_qid:
            raise BuildError(f"duplicate or empty M3D qid: {qid!r}")
        by_qid[qid] = record

    bindings: dict[str, dict[str, Any]] = {}
    case_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for source in medical_sources:
        if _family(source) != "med_ct_lesion":
            continue
        task_id = str(source["task_id"])
        asset = source.get("asset") or {}
        qid = str(asset.get("qid") or "")
        record = by_qid.get(qid)
        if record is None:
            raise BuildError(f"{task_id}: qid absent from M3D matched index: {qid}")
        extra = record.get("extra") or {}
        try:
            mask_id = int(extra.get("mask_id"))
        except (TypeError, ValueError) as exc:
            raise BuildError(f"{task_id}: invalid M3D mask_id") from exc
        if mask_id <= 0:
            raise BuildError(f"{task_id}: M3D mask_id must be positive")
        mask_repo_path = _repo_path(
            asset.get("mask_repo_path") or asset.get("mask_path")
        )
        expected_mask_path = _repo_path(extra.get("mask_path_repo"))
        if mask_repo_path != expected_mask_path:
            raise BuildError(f"{task_id}: M3D mask path disagrees with matched index")
        if _repo_path(asset.get("repo_path") or asset.get("path")) != _repo_path(
            record.get("path_repo")
        ):
            raise BuildError(f"{task_id}: M3D CT path disagrees with matched index")
        if str(asset.get("scene_or_case_id")) != str(record.get("scene_or_case_id")):
            raise BuildError(f"{task_id}: M3D case id disagrees with matched index")
        if str(asset.get("mask_text")) != str(extra.get("mask_text")):
            raise BuildError(f"{task_id}: M3D mask text disagrees with matched index")
        binding = {
            "qid": qid,
            "mask_id": mask_id,
            "mask_repo_path": mask_repo_path,
            "mask_text": str(extra.get("mask_text")),
            "registry_mask_label_count": int(extra.get("mask_label_count")),
        }
        bindings[task_id] = binding
        case_groups[mask_repo_path].append(source)
        target_groups[(mask_repo_path, mask_id)].append(source)

    if len(bindings) != 579 or len(case_groups) != 193:
        raise BuildError(
            f"expected 579 CT rows over 193 cases, found {len(bindings)}/{len(case_groups)}"
        )
    if any(len(group) != 3 for group in case_groups.values()):
        raise BuildError(
            "each selected M3D case must contribute exactly three source rows"
        )
    for (mask_repo_path, mask_id), group in target_groups.items():
        texts = {str(item["asset"].get("mask_text")) for item in group}
        old_ground_truths = {
            _stable_json((item.get("ground_truth") or {}).get("value"))
            for item in group
        }
        if len(texts) != 1 or len(old_ground_truths) != 1:
            raise BuildError(
                f"inconsistent duplicate CT target: {mask_repo_path} label {mask_id}"
            )

    target_group_sizes = Counter(len(group) for group in target_groups.values())
    targets_per_case = Counter(
        len({bindings[str(item["task_id"])]["mask_id"] for item in group})
        for group in case_groups.values()
    )
    audit = {
        "mapping_source": M3D_MATCHED_INDEX.as_posix(),
        "source_rows": len(bindings),
        "source_cases": len(case_groups),
        "source_rows_per_case": 3,
        "unique_target_labels": len(target_groups),
        "target_group_size_distribution": {
            str(size): count for size, count in sorted(target_group_sizes.items())
        },
        "targets_per_case_distribution": {
            str(size): count for size, count in sorted(targets_per_case.items())
        },
        "qid_join_missing": 0,
        "path_mismatches": 0,
        "mask_text_mismatches": 0,
        "same_target_text_or_gt_conflicts": 0,
    }
    if audit["unique_target_labels"] != 345 or audit[
        "target_group_size_distribution"
    ] != {"1": 188, "2": 80, "3": 77}:
        raise BuildError(f"unexpected M3D target mapping: {audit}")
    return bindings, audit


def _base_task(
    source: dict[str, Any],
    *,
    task_id: str | None,
    source_task_ids: list[str],
    oracle_version: str,
    receipt_id: str,
    disposition: str,
    reason: str,
) -> dict[str, Any]:
    task = copy.deepcopy(source)
    task["task_id"] = task_id or str(source["task_id"])
    task["dataset_version"] = DATASET
    task["source_task_id"] = source_task_ids[0]
    task["supersedes"] = list(source_task_ids)
    task["oracle_receipt_id"] = receipt_id
    task["disposition"] = disposition
    task["disposition_reason"] = reason
    provenance = task.setdefault("provenance", {})
    provenance["scale_v2_1"] = {
        "builder": "scripts/qa_gen_scale_v2_1.py",
        "source_task_ids": list(source_task_ids),
        "source_task_sha256": [_task_hash(source)],
        "oracle_version": oracle_version,
        "receipt_id": receipt_id,
    }
    validation = task.setdefault("validation_record", {})
    validation["release_gate"] = (
        "pending_post_build_qc" if disposition == "candidate" else "quarantined"
    )
    return task


def _exact_field(name: str) -> dict[str, Any]:
    return {"field": name, "comparison": "exact"}


def _numeric_field(
    name: str, tolerance: float, unit: str, *, periodic: float | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "field": name,
        "comparison": "absolute_tolerance",
        "tolerance": tolerance,
        "unit": unit,
    }
    if periodic is not None:
        result["periodic_modulus"] = periodic
    return result


def _structured_scorer(
    fields: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "primary": "structured_deterministic_rubric_v1",
            "checks": [str(field["field"]) for field in fields],
            "aggregation": "all_required_with_partial_credit",
        },
        {
            "schema_version": RUBRIC_SCHEMA,
            "strict_json_object": True,
            "fields": fields,
        },
    )


def _atom_id(chain: Any, residue: Any, atom: Any) -> dict[str, Any]:
    return {
        "entity_id": str(getattr(residue, "entity_id", "") or ""),
        "chain_id": str(chain.name),
        "subchain_id": str(residue.subchain),
        "comp_id": str(residue.name),
        "seq_id": int(residue.seqid.num),
        "icode": _clean_char(residue.seqid.icode),
        "atom_id": str(atom.name),
        "altloc": _clean_char(atom.altloc),
        "element": str(atom.element.name),
    }


def _atom_label(atom_id: dict[str, Any]) -> str:
    insertion = atom_id["icode"] or "-"
    altloc = atom_id["altloc"] or "-"
    return (
        f"entity={atom_id['entity_id']}, chain={atom_id['chain_id']}, "
        f"residue={atom_id['comp_id']}:{atom_id['seq_id']}:{insertion}, "
        f"atom={atom_id['atom_id']}, altloc={altloc}"
    )


def _connection_key(address: Any) -> tuple[str, str, int, str, str]:
    return (
        str(address.chain_name),
        str(address.res_id.name),
        int(address.res_id.seqid.num),
        _clean_char(address.res_id.seqid.icode),
        str(address.atom_name),
    )


def _site_key(record: dict[str, Any]) -> tuple[Any, ...]:
    atom = record["atom_id"]
    return (
        atom["entity_id"],
        atom["chain_id"],
        atom["subchain_id"],
        atom["comp_id"],
        atom["seq_id"],
        atom["icode"],
        atom["atom_id"],
    )


def _collapse_altloc(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[_site_key(record)].append(record)
    selected: list[dict[str, Any]] = []
    for key in sorted(grouped):
        selected.append(
            min(
                grouped[key],
                key=lambda item: (
                    -float(item["occupancy"]),
                    0 if not item["atom_id"]["altloc"] else 1,
                    item["atom_id"]["altloc"],
                ),
            )
        )
    return selected


def _collision_role(entity_type_name: str, residue_name: str) -> str | None:
    if entity_type_name == "NonPolymer" and residue_name.upper() not in {
        "HOH",
        "WAT",
        "DOD",
    }:
        return "nonpolymer"
    if entity_type_name == "Polymer":
        return "polymer"
    return None


def _is_occupied_heavy_atom(element: str, occupancy: float) -> bool:
    return element != "H" and occupancy > 0


def _is_directly_connected(
    left: tuple[str, str, int, str, str],
    right: tuple[str, str, int, str, str],
    connected: set[
        tuple[tuple[str, str, int, str, str], tuple[str, str, int, str, str]]
    ],
) -> bool:
    return (left, right) in connected


def _collision_atoms(
    structure: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not structure:
        raise BuildError("mmCIF structure has no models")
    nonpolymer: list[dict[str, Any]] = []
    polymer: list[dict[str, Any]] = []
    for chain in structure[0]:
        for residue in chain:
            role = _collision_role(residue.entity_type.name, residue.name)
            if role == "nonpolymer":
                target = nonpolymer
            elif role == "polymer":
                target = polymer
            else:
                continue
            for atom in residue:
                if not _is_occupied_heavy_atom(atom.element.name, float(atom.occ)):
                    continue
                target.append(
                    {
                        "atom_id": _atom_id(chain, residue, atom),
                        "xyz": (
                            float(atom.pos.x),
                            float(atom.pos.y),
                            float(atom.pos.z),
                        ),
                        "occupancy": float(atom.occ),
                        "vdw_A": float(atom.element.vdw_r),
                    }
                )
    return _collapse_altloc(nonpolymer), _collapse_altloc(polymer)


def _collision_oracle(asset_path: Path) -> dict[str, Any]:
    import gemmi

    structure = gemmi.read_structure(str(asset_path))
    nonpolymer, polymer = _collision_atoms(structure)
    if not nonpolymer:
        return {
            "status": "missing_nonpolymer",
            "nonpolymer_atom_count": 0,
            "polymer_atom_count": len(polymer),
        }
    if not polymer:
        return {
            "status": "missing_polymer",
            "nonpolymer_atom_count": len(nonpolymer),
            "polymer_atom_count": 0,
        }
    connected: set[
        tuple[tuple[str, str, int, str, str], tuple[str, str, int, str, str]]
    ] = set()
    for connection in structure.connections:
        left = _connection_key(connection.partner1)
        right = _connection_key(connection.partner2)
        connected.add((left, right))
        connected.add((right, left))

    def base(record: dict[str, Any]) -> tuple[str, str, int, str, str]:
        atom = record["atom_id"]
        return (
            atom["chain_id"],
            atom["comp_id"],
            atom["seq_id"],
            atom["icode"],
            atom["atom_id"],
        )

    width = 5.0
    grid: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for index, record in enumerate(polymer):
        grid[tuple(math.floor(value / width) for value in record["xyz"])].append(index)
    best: tuple[float, float, int, int] | None = None
    excluded_connections = 0
    for left_index, left in enumerate(nonpolymer):
        x, y, z = left["xyz"]
        cell = tuple(math.floor(value / width) for value in left["xyz"])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for right_index in grid.get(
                        (cell[0] + dx, cell[1] + dy, cell[2] + dz), []
                    ):
                        right = polymer[right_index]
                        if _is_directly_connected(base(left), base(right), connected):
                            excluded_connections += 1
                            continue
                        distance = math.sqrt(
                            (x - right["xyz"][0]) ** 2
                            + (y - right["xyz"][1]) ** 2
                            + (z - right["xyz"][2]) ** 2
                        )
                        overlap = float(left["vdw_A"] + right["vdw_A"] - distance)
                        candidate = (overlap, -distance, left_index, right_index)
                        if best is None or candidate > best:
                            best = candidate
    if best is None:
        return {
            "status": "no_unconnected_pair",
            "nonpolymer_atom_count": len(nonpolymer),
            "polymer_atom_count": len(polymer),
        }
    overlap, negative_distance, left_index, right_index = best
    distance = -negative_distance
    left = nonpolymer[left_index]
    right = polymer[right_index]
    margin = abs(overlap - COLLISION_THRESHOLD_A)
    return {
        "status": "computed",
        "has_severe_clash": "yes" if overlap >= COLLISION_THRESHOLD_A else "no",
        "nonpolymer_atom_id": left["atom_id"],
        "polymer_atom_id": right["atom_id"],
        "distance_A": round(distance, 6),
        "nonpolymer_vdw_A": round(float(left["vdw_A"]), 6),
        "polymer_vdw_A": round(float(right["vdw_A"]), 6),
        "vdw_overlap_A": round(overlap, 6),
        "threshold_overlap_A": COLLISION_THRESHOLD_A,
        "threshold_margin_A": round(margin, 6),
        "robust": margin + 1e-12 >= COLLISION_ROBUST_MARGIN_A,
        "model_index": 0,
        "nonpolymer_atom_count": len(nonpolymer),
        "polymer_atom_count": len(polymer),
        "excluded_struct_conn_pairs": excluded_connections,
    }


def _make_collision_task(
    source: dict[str, Any], computed: dict[str, Any], disposition: str, reason: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_id = f"oracle:{source['task_id']}:{COLLISION_ORACLE}"
    task = _base_task(
        source,
        task_id=None,
        source_task_ids=[str(source["task_id"])],
        oracle_version=COLLISION_ORACLE,
        receipt_id=receipt_id,
        disposition=disposition,
        reason=reason,
    )
    task["title"] = "Nonpolymer-polymer heavy-atom clash geometry"
    task["knowledge"]["direction_id"] = "bio_collision_geometry"
    task["instruction"] = (
        "In model 0 of the displayed asymmetric unit, consider all occupied, non-water "
        "PDBx NonPolymer heavy atoms and all occupied PDBx Polymer heavy atoms. Collapse "
        "alternate locations by highest occupancy (blank then lexical altloc as tie-break), "
        "exclude directly linked _struct_conn atom pairs, and use Gemmi van der Waals radii. "
        "Report the nonpolymer/polymer atom pair with maximum overlap, its distance and overlap, "
        "and whether overlap is at least 0.4 A."
    )
    fields = [
        "has_severe_clash",
        "nonpolymer_atom_id",
        "polymer_atom_id",
        "distance_A",
        "vdw_overlap_A",
    ]
    task["answer_schema"] = {"kind": "structured", "fields": fields}
    task["terminal_answer"] = {
        "kind": "structured",
        "fields": fields,
        "must_be_derived_from_3d_interaction": True,
    }
    if computed["status"] == "computed":
        value = {field: computed[field] for field in fields}
        task["ground_truth"] = {
            "status": "auto_verified",
            "type": "auto_measured",
            "value": value,
            "notes": f"Recomputed by {COLLISION_ORACLE}; no scale-v1 binary GT was reused.",
        }
        task["asset_grounding"] = {
            "status": "grounded_from_asset",
            "verified_entities": computed,
            "missing_or_ambiguous_entities": [],
            "notes": "Complete deterministic nonpolymer/polymer atom selection receipt.",
        }
    else:
        task["ground_truth"] = {
            "status": "needs_regeneration",
            "type": "unavailable",
            "value": None,
            "notes": computed["status"],
        }
        task["asset_grounding"] = {
            "status": "reject",
            "verified_entities": computed,
            "missing_or_ambiguous_entities": [computed["status"]],
            "notes": "The declared two-sided collision contract cannot be instantiated.",
        }
    scorer, config = _structured_scorer(
        [
            _exact_field("has_severe_clash"),
            _exact_field("nonpolymer_atom_id"),
            _exact_field("polymer_atom_id"),
            _numeric_field("distance_A", 0.15, "A"),
            _numeric_field("vdw_overlap_A", 0.15, "A"),
        ]
    )
    task["scorer"] = scorer
    task["scorer_config"] = config
    task["scientific_action"] = (
        "identify and measure the maximum heavy-atom vdW overlap"
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "task_id": task["task_id"],
        "source_task_ids": [source["task_id"]],
        "oracle_version": COLLISION_ORACLE,
        "disposition": disposition,
        "reason": reason,
        "selection": {
            "model_index": 0,
            "coordinate_scope": "asymmetric_unit_cartesian_no_symmetry",
            "nonpolymer": "occupied non-water PDBx NonPolymer heavy atoms",
            "polymer": "occupied PDBx Polymer heavy atoms",
            "altloc": "highest occupancy; blank then lexical altloc tie-break",
            "excluded_pairs": "direct _struct_conn",
            "radii": "Gemmi Element.vdw_r",
            "severe_overlap_threshold_A": COLLISION_THRESHOLD_A,
        },
        "computed": computed,
    }
    return task, receipt


def _ct_geometry(ct_path: Path, mask_path: Path, target_mask_id: int) -> dict[str, Any]:
    import nibabel as nib
    import numpy as np

    ct = nib.load(str(ct_path))
    mask_image = nib.load(str(mask_path))
    if len(ct.shape) != 3 or len(mask_image.shape) != 3 or ct.shape != mask_image.shape:
        raise BuildError(f"CT/mask shape mismatch: {ct_path} / {mask_path}")
    affine_delta = float(np.max(np.abs(ct.affine - mask_image.affine)))
    if affine_delta > 1e-3:
        raise BuildError(
            f"CT/mask affine mismatch {affine_delta}: {ct_path} / {mask_path}"
        )
    axcodes = tuple(str(value) for value in nib.aff2axcodes(ct.affine))
    if set(axcodes) != {"R", "A", "S"}:
        raise BuildError(f"unsupported CT orientation {axcodes}: {ct_path}")
    raw = np.asanyarray(mask_image.dataobj)
    if not np.issubdtype(raw.dtype, np.integer):
        finite = np.isfinite(raw)
        if not bool(finite.all()) or not bool(np.equal(raw, np.rint(raw)).all()):
            raise BuildError(f"non-integer CT mask values: {mask_path}")
    observed_labels = sorted({int(value) for value in np.unique(raw)})
    if observed_labels and observed_labels[0] < 0:
        raise BuildError(f"negative CT mask label: {mask_path}")
    foreground_labels = [value for value in observed_labels if value != 0]
    if target_mask_id not in foreground_labels:
        raise BuildError(
            f"target CT mask label {target_mask_id} is absent: {mask_path}"
        )
    mask = raw == target_mask_id
    if not bool(mask.any()):
        raise BuildError(f"empty CT mask: {mask_path}")
    del raw
    counts = [
        mask.sum(
            axis=tuple(axis for axis in range(3) if axis != selected), dtype=np.int64
        )
        for selected in range(3)
    ]
    voxel_count = int(counts[0].sum())
    bbox_min = [int(np.flatnonzero(axis_counts)[0]) for axis_counts in counts]
    bbox_max = [int(np.flatnonzero(axis_counts)[-1]) for axis_counts in counts]
    centroid = np.asarray(
        [
            float(np.dot(np.arange(len(axis_counts)), axis_counts)) / voxel_count
            for axis_counts in counts
        ]
    )
    centroid_world = nib.affines.apply_affine(ct.affine, centroid)
    orientation = nib.orientations.io_orientation(ct.affine)
    lr_axis = int(np.where(orientation[:, 0] == 0)[0][0])
    si_axis = int(np.where(orientation[:, 0] == 2)[0][0])
    axial_counts = counts[si_axis]
    maximum_area = int(axial_counts.max())
    tied_slices = [int(value) for value in np.flatnonzero(axial_counts == maximum_area)]
    shape_max = np.asarray(ct.shape) - 1
    corners = np.asarray(
        [
            [i, j, k]
            for i in (0, shape_max[0])
            for j in (0, shape_max[1])
            for k in (0, shape_max[2])
        ]
    )
    world_x = nib.affines.apply_affine(ct.affine, corners)[:, 0]
    midline_x = float((world_x.min() + world_x.max()) / 2.0)
    signed_distance = float(centroid_world[0] - midline_x)
    laterality = (
        "right" if signed_distance > 0 else "left" if signed_distance < 0 else "midline"
    )
    return {
        "laterality": laterality,
        "centroid_vox": [round(float(value), 6) for value in centroid],
        "centroid_world_ras_mm": [round(float(value), 6) for value in centroid_world],
        "bbox_min_vox": bbox_min,
        "bbox_max_vox_inclusive": bbox_max,
        "extent_vox": [bbox_max[index] - bbox_min[index] + 1 for index in range(3)],
        "representative_axial_index": tied_slices[0],
        "signed_lr_distance_mm": round(signed_distance, 6),
        "selection_receipt": {
            "voxel_count": voxel_count,
            "foreground_rule": "mask voxel value equals target_mask_id",
            "target_mask_id": target_mask_id,
            "foreground_label_values": [target_mask_id],
            "observed_mask_label_values": foreground_labels,
            "orientation_axcodes": list(axcodes),
            "lr_array_axis": lr_axis,
            "si_array_axis": si_axis,
            "fov_midline_world_ras_x_mm": round(midline_x, 6),
            "representative_axial_tied_indices": tied_slices,
            "representative_axial_max_mask_voxels": maximum_area,
            "ct_mask_max_affine_delta": round(affine_delta, 9),
            "ct_qform_code": int(ct.header["qform_code"]),
            "ct_sform_code": int(ct.header["sform_code"]),
            "mask_qform_code": int(mask_image.header["qform_code"]),
            "mask_sform_code": int(mask_image.header["sform_code"]),
        },
    }


def _make_ct_task(
    source: dict[str, Any],
    source_ids: list[str],
    task_id: str,
    computed: dict[str, Any],
    binding: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_id = f"oracle:{task_id}:{CT_ORACLE}"
    task = _base_task(
        source,
        task_id=task_id,
        source_task_ids=source_ids,
        oracle_version=CT_ORACLE,
        receipt_id=receipt_id,
        disposition="candidate",
        reason="deduplicated_ct_target_label_geometry_recomputed",
    )
    target_mask_id = int(binding["mask_id"])
    task["title"] = "CT target-annotation localization and geometry"
    task["knowledge"]["direction_id"] = "med_ct_geometry"
    task["instruction"] = (
        f"Use only the bound target annotation: mask voxels whose integer value equals mask_id "
        f"{target_mask_id}. Ignore every other nonzero label in the multi-label mask. Report this "
        "target's centroid in voxel coordinates and RAS+ millimetres, "
        "inclusive voxel bounding box, voxel extent, and representative axial slice (the smallest "
        "array index among slices tied for maximum target area). Also report the signed RAS-x "
        "distance from the CT field-of-view midpoint: positive is patient Right and negative is Left, "
        "and classify laterality by that sign. This is an image field-of-view reference, not an "
        "anatomical body-midline claim."
    )
    fields = [
        "laterality",
        "centroid_vox",
        "centroid_world_ras_mm",
        "bbox_min_vox",
        "bbox_max_vox_inclusive",
        "extent_vox",
        "representative_axial_index",
        "signed_lr_distance_mm",
    ]
    value = {field: computed[field] for field in fields}
    task["answer_schema"] = {"kind": "structured", "fields": fields}
    task["terminal_answer"] = {
        "kind": "structured",
        "fields": fields,
        "must_be_derived_from_3d_interaction": True,
    }
    task["ground_truth"] = {
        "status": "auto_verified",
        "type": "auto_measured",
        "value": value,
        "notes": (
            f"Recomputed from CT affine and mask voxels equal to mask_id "
            f"{target_mask_id} by {CT_ORACLE}."
        ),
    }
    task["asset_grounding"] = {
        "status": "grounded_from_asset",
        "verified_entities": {**value, **computed["selection_receipt"]},
        "missing_or_ambiguous_entities": [],
        "notes": (
            f"RAS+ geometry uses only mask label {target_mask_id} and the CT affine; "
            "signed LR is relative to the CT FOV midpoint."
        ),
    }
    task["mask_id"] = target_mask_id
    task["target_selection"] = {
        "mask_id": target_mask_id,
        "mask_text": binding["mask_text"],
        "foreground_policy": "mask voxel value equals mask_id",
    }
    task["render_contract"] = {
        "mask_id": target_mask_id,
        "foreground_policy": "mask voxel value equals mask_id",
        "overlay_policy": "selected target label only",
        "required_overlay_argument": {"mask_id": target_mask_id},
    }
    for asset in task.get("assets", []):
        asset["mask_id"] = target_mask_id
        asset["registry_mask_label_count"] = binding["registry_mask_label_count"]
    task["asset"]["mask_id"] = target_mask_id
    task["asset"]["registry_mask_label_count"] = binding["registry_mask_label_count"]
    scorer, config = _structured_scorer(
        [
            _exact_field("laterality"),
            _numeric_field("centroid_vox", 3.0, "voxel"),
            _numeric_field("centroid_world_ras_mm", 5.0, "mm"),
            _numeric_field("bbox_min_vox", 3.0, "voxel"),
            _numeric_field("bbox_max_vox_inclusive", 3.0, "voxel"),
            _numeric_field("extent_vox", 3.0, "voxel"),
            _numeric_field("representative_axial_index", 2.0, "voxel_index"),
            _numeric_field("signed_lr_distance_mm", 5.0, "mm"),
        ]
    )
    task["scorer"] = scorer
    task["scorer"]["ground_truth_selection"] = {
        "mask_id": target_mask_id,
        "foreground_policy": "mask voxel value equals mask_id",
        "oracle_version": CT_ORACLE,
    }
    task["scorer_config"] = config
    task["scientific_action"] = (
        "localize and measure a highlighted 3D CT region in RAS+ coordinates"
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "task_id": task_id,
        "source_task_ids": source_ids,
        "oracle_version": CT_ORACLE,
        "disposition": "candidate",
        "reason": "deduplicated_ct_target_label_geometry_recomputed",
        "selection": computed["selection_receipt"],
        "computed": value,
        "registry_binding": binding,
    }
    return task, receipt


def _resolve_structure_atom(
    structure: Any, declared: dict[str, Any]
) -> tuple[dict[str, Any], tuple[float, float, float]]:
    matches: list[tuple[dict[str, Any], tuple[float, float, float]]] = []
    declared_altloc = _clean_char(declared.get("altloc"))
    for chain in structure[0]:
        if chain.name != str(declared["chain"]):
            continue
        for residue in chain:
            if residue.name != str(declared["residue"]) or residue.seqid.num != int(
                declared["seqid"]
            ):
                continue
            for atom in residue:
                if atom.name != str(declared["atom"]):
                    continue
                if declared_altloc and _clean_char(atom.altloc) != declared_altloc:
                    continue
                matches.append(
                    (
                        _atom_id(chain, residue, atom),
                        (float(atom.pos.x), float(atom.pos.y), float(atom.pos.z)),
                    )
                )
    if len(matches) != 1:
        raise BuildError(f"atom selection resolved to {len(matches)} atoms: {declared}")
    return matches[0]


def _make_binding_task(
    source: dict[str, Any], compute: dict[str, Any], asset_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    import gemmi

    receipt_id = f"oracle:{source['task_id']}:{BINDING_ORACLE}"
    structure = gemmi.read_structure(str(asset_path))
    computed = compute["computed_result"]
    nonpolymer_atom, left_xyz = _resolve_structure_atom(
        structure, computed["ligand_atom"]
    )
    polymer_atom, right_xyz = _resolve_structure_atom(
        structure, computed["receptor_atom"]
    )
    distance = math.dist(left_xyz, right_xyz)
    if abs(distance - float(computed["contact_distance_A"])) > 0.005:
        raise BuildError(
            f"{source['task_id']}: local binding distance disagrees with locked receipt"
        )
    residue_id = {
        key: polymer_atom[key]
        for key in (
            "entity_id",
            "chain_id",
            "subchain_id",
            "comp_id",
            "seq_id",
            "icode",
        )
    }
    value = {
        "nonpolymer_atom_id": nonpolymer_atom,
        "polymer_atom_id": polymer_atom,
        "contact_distance_A": round(distance, 6),
        "contact_residue_id": residue_id,
    }
    task = _base_task(
        source,
        task_id=None,
        source_task_ids=[str(source["task_id"])],
        oracle_version=BINDING_ORACLE,
        receipt_id=receipt_id,
        disposition="candidate",
        reason="explicit_atom_selection_confirmed_and_recomputed",
    )
    task["title"] = "Explicit nonpolymer-polymer atom distance"
    task["knowledge"]["direction_id"] = "chem_binding_atom_distance"
    task["instruction"] = (
        "Measure the Cartesian distance in model 0 between the explicitly identified atoms. "
        f"Nonpolymer atom: {_atom_label(nonpolymer_atom)}. "
        f"Polymer atom: {_atom_label(polymer_atom)}. "
        "Return both complete atom IDs, the polymer residue ID, and the distance in A."
    )
    fields = list(value)
    task["answer_schema"] = {"kind": "structured", "fields": fields}
    task["terminal_answer"] = {
        "kind": "structured",
        "fields": fields,
        "must_be_derived_from_3d_interaction": True,
    }
    task["ground_truth"] = {
        "status": "auto_verified",
        "type": "auto_measured",
        "value": value,
        "notes": f"Explicit endpoints resolved and recomputed from local mmCIF by {BINDING_ORACLE}.",
    }
    task["asset_grounding"] = {
        "status": "grounded_from_asset",
        "verified_entities": value,
        "missing_or_ambiguous_entities": [],
        "notes": "Both endpoint atom instances are explicit; no ligand-entity inference remains.",
    }
    scorer, config = _structured_scorer(
        [
            _exact_field("nonpolymer_atom_id"),
            _exact_field("polymer_atom_id"),
            _numeric_field("contact_distance_A", 0.15, "A"),
            _exact_field("contact_residue_id"),
        ]
    )
    task["scorer"] = scorer
    task["scorer_config"] = config
    task["scientific_action"] = (
        "select two declared atom instances and measure their 3D distance"
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "task_id": task["task_id"],
        "source_task_ids": [source["task_id"]],
        "oracle_version": BINDING_ORACLE,
        "disposition": "candidate",
        "reason": "explicit_atom_selection_confirmed_and_recomputed",
        "selection": {
            "model_index": 0,
            "nonpolymer_atom_id": nonpolymer_atom,
            "polymer_atom_id": polymer_atom,
            "locked_compute_receipt_status": compute["receipt"]["route_status"],
        },
        "computed": value,
    }
    return task, receipt


def _branch_atom_indices(molecule: Any, anchor: int, blocked_center: int) -> list[int]:
    seen = {blocked_center}
    pending = [anchor]
    branch: list[int] = []
    while pending:
        atom_index = pending.pop()
        if atom_index in seen:
            continue
        seen.add(atom_index)
        branch.append(atom_index)
        pending.extend(
            neighbor.GetIdx()
            for neighbor in molecule.GetAtomWithIdx(atom_index).GetNeighbors()
            if neighbor.GetIdx() not in seen
        )
    return sorted(branch)


def _outer_atom(
    molecule: Any, center: int, other_center: int
) -> tuple[int, list[dict[str, Any]]]:
    options: list[dict[str, Any]] = []
    for neighbor in molecule.GetAtomWithIdx(center).GetNeighbors():
        anchor = int(neighbor.GetIdx())
        if anchor == other_center:
            continue
        branch = _branch_atom_indices(molecule, anchor, center)
        heavy_count = sum(
            molecule.GetAtomWithIdx(index).GetAtomicNum() > 1 for index in branch
        )
        options.append(
            {
                "anchor_atom_id_zero_based": anchor,
                "connected_heavy_atom_count": heavy_count,
            }
        )
    if not options:
        raise BuildError("rotor center has no outer atom")
    maximum = max(item["connected_heavy_atom_count"] for item in options)
    chosen = min(
        item["anchor_atom_id_zero_based"]
        for item in options
        if item["connected_heavy_atom_count"] == maximum
    )
    return chosen, sorted(options, key=lambda item: item["anchor_atom_id_zero_based"])


def _classify_dihedral(angle: float) -> tuple[str, float]:
    magnitude = abs(angle)
    conformer_class = (
        "eclipsed" if magnitude <= 30 else "anti" if magnitude >= 120 else "gauche"
    )
    margin = min(abs(magnitude - 30), abs(magnitude - 120))
    return conformer_class, margin


def _circular_delta_degrees(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _conformation_oracle(xyz_path: Path) -> dict[str, Any]:
    from rdkit import Chem
    from rdkit.Chem import Lipinski, rdDetermineBonds, rdMolTransforms

    xyz = xyz_path.read_text(encoding="utf-8")
    molecule = Chem.MolFromXYZBlock(xyz)
    if molecule is None:
        return {"status": "xyz_parse_failed"}
    try:
        rdDetermineBonds.DetermineBonds(molecule, charge=0)
        Chem.SanitizeMol(molecule)
    except Exception as exc:
        return {
            "status": "bond_order_uncomputable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    Chem.GetSymmSSSR(molecule)
    rotors = sorted(
        {
            tuple(sorted((int(match[0]), int(match[1]))))
            for match in molecule.GetSubstructMatches(
                Lipinski.RotatableBondSmarts, uniquify=True
            )
            if len(match) >= 2
        }
    )
    if not rotors:
        return {"status": "no_strict_rotatable_bond", "strict_rotor_count": 0}
    center_left, center_right = rotors[0]
    outer_left, left_options = _outer_atom(molecule, center_left, center_right)
    outer_right, right_options = _outer_atom(molecule, center_right, center_left)
    atom_ids_zero = [outer_left, center_left, center_right, outer_right]
    angle = float(
        rdMolTransforms.GetDihedralDeg(molecule.GetConformer(), *atom_ids_zero)
    )
    conformer_class, margin = _classify_dihedral(angle)
    return {
        "status": "computed",
        "strict_rotor_count": len(rotors),
        "strict_rotor_candidates_zero_based": [list(value) for value in rotors],
        "selected_center_bond_zero_based": [center_left, center_right],
        "left_outer_options": left_options,
        "right_outer_options": right_options,
        "atom_ids": [index + 1 for index in atom_ids_zero],
        "atom_ids_zero_based": atom_ids_zero,
        "dihedral_deg": round(angle, 6),
        "conformer_class": conformer_class,
        "class_boundary_margin_deg": round(margin, 6),
        "robust": margin + 1e-12 >= CONFORMATION_BOUNDARY_MARGIN_DEG,
    }


def _make_conformation_task(
    source: dict[str, Any], computed: dict[str, Any], disposition: str, reason: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_id = f"oracle:{source['task_id']}:{CONFORMATION_ORACLE}"
    task = _base_task(
        source,
        task_id=None,
        source_task_ids=[str(source["task_id"])],
        oracle_version=CONFORMATION_ORACLE,
        receipt_id=receipt_id,
        disposition=disposition,
        reason=reason,
    )
    task["title"] = "Ordered four-atom dihedral and conformer class"
    task["knowledge"]["direction_id"] = "chem_conformation_explicit_dihedral"
    fields = ["atom_ids", "dihedral_deg", "conformer_class"]
    task["answer_schema"] = {"kind": "structured", "fields": fields}
    task["terminal_answer"] = {
        "kind": "structured",
        "fields": fields,
        "must_be_derived_from_3d_interaction": True,
    }
    if computed["status"] == "computed":
        value = {field: computed[field] for field in fields}
        task["instruction"] = (
            f"Using XYZ atom line numbers in the ordered sequence {computed['atom_ids']}, report the "
            "signed dihedral in degrees in (-180, 180] using the right-hand atan2 convention, and "
            "classify by absolute angle: eclipsed <=30 degrees, anti >=120 degrees, otherwise gauche. "
            "Return the ordered atom IDs, angle, and class."
        )
        task["ground_truth"] = {
            "status": "auto_verified",
            "type": "auto_measured",
            "value": value,
            "notes": f"Recomputed from ordered XYZ coordinates by {CONFORMATION_ORACLE}; scale-v1 GT was not reused.",
        }
        task["asset_grounding"] = {
            "status": "grounded_from_asset",
            "verified_entities": computed,
            "missing_or_ambiguous_entities": [],
            "notes": "Four ordered atom line IDs are explicit and selected without consulting prior GT.",
        }
    else:
        task["instruction"] = (
            "Replacement conformation task pending a valid strict-rotor atom binding."
        )
        task["ground_truth"] = {
            "status": "needs_regeneration",
            "type": "unavailable",
            "value": None,
            "notes": computed["status"],
        }
        task["asset_grounding"] = {
            "status": "reject",
            "verified_entities": computed,
            "missing_or_ambiguous_entities": [computed["status"]],
            "notes": "No valid explicit strict-rotor contract was produced.",
        }
    scorer, config = _structured_scorer(
        [
            _exact_field("atom_ids"),
            _numeric_field("dihedral_deg", 5.0, "degree", periodic=360.0),
            _exact_field("conformer_class"),
        ]
    )
    task["scorer"] = scorer
    task["scorer_config"] = config
    task["scientific_action"] = "measure an explicitly ordered four-atom dihedral"
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "task_id": task["task_id"],
        "source_task_ids": [source["task_id"]],
        "oracle_version": CONFORMATION_ORACLE,
        "disposition": disposition,
        "reason": reason,
        "selection": {
            "connectivity": "RDKit DetermineBonds neutral charge",
            "rotors": "RDKit Lipinski RotatableBondSmarts",
            "center_bond": "lexicographically first zero-based strict rotor",
            "outer_atom": "largest branch heavy-atom count; lowest atom index tie-break",
            "reported_atom_ids": "one-based XYZ atom line numbers",
            "angle_convention": "ordered right-hand atan2 in (-180,180]",
        },
        "computed": computed,
    }
    return task, receipt


def _quarantine_task(
    source: dict[str, Any],
    *,
    oracle_version: str,
    reason: str,
    clear_ground_truth: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_id = f"oracle:{source['task_id']}:{oracle_version}"
    task = _base_task(
        source,
        task_id=None,
        source_task_ids=[str(source["task_id"])],
        oracle_version=oracle_version,
        receipt_id=receipt_id,
        disposition="quarantine",
        reason=reason,
    )
    if clear_ground_truth:
        task["ground_truth"] = {
            "status": "needs_regeneration",
            "type": "unavailable",
            "value": None,
            "notes": reason,
        }
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "task_id": task["task_id"],
        "source_task_ids": [source["task_id"]],
        "oracle_version": oracle_version,
        "disposition": "quarantine",
        "reason": reason,
        "selection": {"status": "not_selected"},
        "computed": None,
    }
    return task, receipt


def _reuse_task(
    source: dict[str, Any], family_reason: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_id = f"oracle:{source['task_id']}:{REUSE_ORACLE}"
    task = _base_task(
        source,
        task_id=None,
        source_task_ids=[str(source["task_id"])],
        oracle_version=REUSE_ORACLE,
        receipt_id=receipt_id,
        disposition="candidate",
        reason=family_reason,
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "task_id": task["task_id"],
        "source_task_ids": [source["task_id"]],
        "oracle_version": REUSE_ORACLE,
        "disposition": "candidate",
        "reason": family_reason,
        "selection": {"source_task_sha256": _task_hash(source)},
        "computed": copy.deepcopy(task.get("ground_truth", {}).get("value")),
    }
    return task, receipt


def _ledger_record(
    source_task_id: str,
    output_task_id: str,
    task: dict[str, Any],
    receipt: dict[str, Any],
    disposition: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA,
        "source_task_id": source_task_id,
        "supersedes": [source_task_id],
        "output_task_id": output_task_id,
        "domain": task["domain"],
        "output_family": _family(task),
        "disposition": disposition,
        "reason": reason,
        "oracle_version": receipt["oracle_version"],
        "selection_receipt_id": receipt["receipt_id"],
        "asset_hashes": copy.deepcopy(task["asset_hashes"]),
    }


def _rubric_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": RUBRIC_SCHEMA,
        "type": "object",
        "required": ["schema_version", "strict_json_object", "fields"],
        "properties": {
            "schema_version": {"const": RUBRIC_SCHEMA},
            "strict_json_object": {"const": True},
            "fields": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["field", "comparison"],
                    "properties": {
                        "field": {"type": "string"},
                        "comparison": {"enum": ["exact", "absolute_tolerance"]},
                        "tolerance": {"type": "number", "minimum": 0},
                        "unit": {"type": "string"},
                        "periodic_modulus": {"type": "number", "exclusiveMinimum": 0},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }


def _validate_tasks(tasks: list[dict[str, Any]], schema: dict[str, Any]) -> None:
    import jsonschema

    validator = jsonschema.Draft202012Validator(schema)
    for task in tasks:
        errors = sorted(validator.iter_errors(task), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            raise BuildError(
                f"{task.get('task_id')}: task schema error at {list(first.path)}: {first.message}"
            )
        asset_hashes = task.get("asset_hashes")
        if not isinstance(asset_hashes, list) or not asset_hashes:
            raise BuildError(f"{task.get('task_id')}: missing asset hashes")
        for asset in task.get("assets", []):
            if str(asset.get("path", "")).startswith("/") or str(
                asset.get("mask_path", "")
            ).startswith("/"):
                raise BuildError(
                    f"{task.get('task_id')}: emitted an absolute asset path"
                )


def _validate_partition(
    source_ids: set[str],
    candidates: dict[str, list[dict[str, Any]]],
    quarantine: dict[str, list[dict[str, Any]]],
    ledger: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
) -> None:
    candidate_ids = {task["task_id"] for tasks in candidates.values() for task in tasks}
    quarantine_ids = {
        task["task_id"] for tasks in quarantine.values() for task in tasks
    }
    if candidate_ids & quarantine_ids:
        raise BuildError(
            f"candidate/quarantine overlap: {sorted(candidate_ids & quarantine_ids)[:3]}"
        )
    if len(candidate_ids) != sum(len(tasks) for tasks in candidates.values()):
        raise BuildError("duplicate candidate task IDs")
    if len(quarantine_ids) != sum(len(tasks) for tasks in quarantine.values()):
        raise BuildError("duplicate quarantine task IDs")
    ledger_ids = [str(item["source_task_id"]) for item in ledger]
    if set(ledger_ids) != source_ids or len(ledger_ids) != len(source_ids):
        missing = sorted(source_ids - set(ledger_ids))
        extra = sorted(set(ledger_ids) - source_ids)
        raise BuildError(
            f"ledger is not a strict source partition: missing={missing[:3]}, extra={extra[:3]}"
        )
    receipt_ids = [str(item["receipt_id"]) for item in receipts]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise BuildError("duplicate receipt IDs")
    available_receipts = set(receipt_ids)
    for item in ledger:
        if item["selection_receipt_id"] not in available_receipts:
            raise BuildError(
                f"ledger references missing receipt: {item['selection_receipt_id']}"
            )


def _source_lock(
    repo_root: Path, v1_lock: dict[str, Any], ct_mapping_audit: dict[str, Any]
) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    patterns = [
        SOURCE_DIR / "ScienceVision_scale-v1-biology_final_tasks.jsonl",
        SOURCE_DIR / "ScienceVision_scale-v1-chemistry_final_tasks.jsonl",
        SOURCE_DIR / "ScienceVision_scale-v1-medical_final_tasks.jsonl",
        V2_DIR / "ScienceVision_scale-v2-biology_candidate_tasks.jsonl",
        V2_DIR / "ScienceVision_scale-v2-medical_candidate_tasks.jsonl",
        V2_DIR / "quarantine/ScienceVision_scale-v2-biology_quarantine_tasks.jsonl",
        V2_DIR / "quarantine/ScienceVision_scale-v2-chemistry_quarantine_tasks.jsonl",
        V1_LOCK,
        CHEM_RECEIPT,
        M3D_MATCHED_INDEX,
        TASK_SCHEMA,
        Path("scripts/qa_gen_scale_v2_1.py"),
    ]
    for path in patterns:
        inputs.append(_file_record(repo_root, path))
    return {
        "schema_version": LOCK_SCHEMA,
        "dataset": DATASET,
        "inputs": sorted(inputs, key=lambda item: item["path"]),
        "scale_v1_reviewed_lock": v1_lock,
        "ct_target_mapping_audit": ct_mapping_audit,
        "oracle_versions": {
            "collision": COLLISION_ORACLE,
            "ct": CT_ORACLE,
            "binding": BINDING_ORACLE,
            "conformation": CONFORMATION_ORACLE,
            "reuse": REUSE_ORACLE,
        },
        "runtime_versions": {
            "gemmi": "0.7.5",
            "nibabel": "5.3.2",
            "numpy": "2.5.1",
            "rdkit": "2026.3.5",
        },
    }


def _publish(staging: Path, output_dir: Path) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if not output_dir.exists():
        os.replace(staging, output_dir)
        return
    backup = output_dir.with_name(f".{output_dir.name}.backup")
    if backup.exists():
        raise BuildError(f"stale publication backup exists: {backup}")
    os.replace(output_dir, backup)
    try:
        os.replace(staging, output_dir)
    except Exception:
        os.replace(backup, output_dir)
        raise
    shutil.rmtree(backup)


def build_scale_v2_1(
    repo_root: Path,
    asset_root: Path,
    output_dir: Path,
    *,
    generated_at: str = GENERATED_AT_DEFAULT,
    publish: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    asset_root = asset_root.resolve()
    output_dir = output_dir.resolve()
    if not asset_root.is_dir():
        raise BuildError(f"asset root is not a directory: {asset_root}")
    v1_lock = _verify_v1_lock(repo_root)
    sources, by_domain = _load_sources(repo_root)
    ct_bindings, ct_mapping_audit = _load_ct_target_bindings(
        repo_root, by_domain["medical"]
    )
    v2_candidate, v2_quarantine = _load_v2(repo_root)
    compute_data = json.loads((repo_root / CHEM_RECEIPT).read_text(encoding="utf-8"))
    compute_by_id = {str(item["task_id"]): item for item in compute_data["results"]}
    hasher = AssetHasher(asset_root)
    candidates: dict[str, list[dict[str, Any]]] = {
        domain: [] for domain in ("biology", "chemistry", "medical")
    }
    quarantine: dict[str, list[dict[str, Any]]] = {
        domain: [] for domain in ("biology", "chemistry", "medical")
    }
    receipts: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    def add(
        task: dict[str, Any],
        receipt: dict[str, Any],
        source_ids: list[str],
        disposition: str,
        reason: str,
    ) -> None:
        hashes = _normalize_task_assets(task, hasher)
        receipt["asset_hashes"] = copy.deepcopy(hashes)
        target = candidates if disposition == "candidate" else quarantine
        target[str(task["domain"])].append(task)
        receipts.append(receipt)
        for source_id in source_ids:
            ledger_disposition = disposition
            ledger_reason = reason
            if len(source_ids) > 1 and source_id != task["source_task_id"]:
                ledger_disposition = "superseded_deduplicated"
                ledger_reason = (
                    "duplicate_ct_target_contract_superseded_by_unique_geometry_task"
                )
            record = _ledger_record(
                source_id,
                str(task["task_id"]),
                task,
                receipt,
                ledger_disposition,
                ledger_reason,
            )
            record["source_family"] = _family(sources[source_id])
            ledger.append(record)

    # Biology collision: recompute every source task and retain the full partition.
    for source in sorted(by_domain["biology"], key=lambda item: str(item["task_id"])):
        if _family(source) != "bio_collision_check":
            continue
        asset = hasher.record((source.get("asset") or {}).get("repo_path"))
        computed = _collision_oracle(asset_root / asset["repo_path"])
        if computed["status"] != "computed":
            disposition, reason = "quarantine", computed["status"]
        elif computed["robust"]:
            disposition, reason = "candidate", "vdw_overlap_margin_at_least_0.1A"
        else:
            disposition, reason = "quarantine", "vdw_overlap_within_0.1A_of_threshold"
        task, receipt = _make_collision_task(source, computed, disposition, reason)
        add(task, receipt, [str(source["task_id"])], disposition, reason)

    # Reuse only the 16 comparison tasks that survived the locked v2 margin gate.
    comparison_candidate_ids = {
        task_id
        for task_id, task in v2_candidate.items()
        if _family(task) == "bio_multi_structure_compare"
    }
    if len(comparison_candidate_ids) != 16:
        raise BuildError(
            f"expected 16 locked comparison candidates, found {len(comparison_candidate_ids)}"
        )
    for source in sorted(by_domain["biology"], key=lambda item: str(item["task_id"])):
        family = _family(source)
        if family == "bio_multi_structure_compare":
            task_id = str(source["task_id"])
            if task_id in comparison_candidate_ids:
                repaired = v2_candidate[task_id]
                distances = sorted(
                    float(value)
                    for value in repaired["asset_grounding"]["verified_entities"][
                        "contact_distances_A"
                    ]
                )
                margin = distances[1] - distances[0]
                if margin + 1e-12 < 0.5:
                    raise BuildError(f"{task_id}: comparison margin below 0.5 A")
                task, receipt = _reuse_task(
                    repaired, "locked_v2_comparison_margin_at_least_0.5A"
                )
                receipt["selection"]["contact_margin_A"] = round(margin, 6)
                add(
                    task,
                    receipt,
                    [task_id],
                    "candidate",
                    "locked_v2_comparison_margin_at_least_0.5A",
                )
            else:
                base = v2_quarantine.get(task_id, source)
                task, receipt = _quarantine_task(
                    base,
                    oracle_version=QUARANTINE_ORACLE,
                    reason="comparison_margin_below_0.5A",
                    clear_ground_truth=False,
                )
                add(
                    task,
                    receipt,
                    [task_id],
                    "quarantine",
                    "comparison_margin_below_0.5A",
                )
        elif family == "bio_docking_plausibility":
            task_id = str(source["task_id"])
            base = v2_quarantine.get(task_id, source)
            task, receipt = _quarantine_task(
                base,
                oracle_version=QUARANTINE_ORACLE,
                reason="subjective_docking_ground_truth_unverified",
                clear_ground_truth=True,
            )
            add(
                task,
                receipt,
                [task_id],
                "quarantine",
                "subjective_docking_ground_truth_unverified",
            )

    # Medical CT: deduplicate only equivalent target labels, never whole mask assets.
    ct_groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for source in by_domain["medical"]:
        if _family(source) == "med_ct_lesion":
            binding = ct_bindings[str(source["task_id"])]
            ct_groups[(binding["mask_repo_path"], binding["mask_id"])].append(source)
    if len(ct_groups) != 345 or sum(map(len, ct_groups.values())) != 579:
        raise BuildError(
            f"expected 579 CT rows over 345 targets, found {sum(map(len, ct_groups.values()))}/{len(ct_groups)}"
        )
    observed_labels_by_mask: dict[str, tuple[int, ...]] = {}
    old_new_laterality: Counter[tuple[str, str]] = Counter()
    for index, ((mask_repo_path, target_mask_id), group) in enumerate(
        sorted(ct_groups.items())
    ):
        ordered = sorted(group, key=lambda item: str(item["task_id"]))
        source = ordered[0]
        source_ids = [str(item["task_id"]) for item in ordered]
        binding = ct_bindings[source_ids[0]]
        if any(
            ct_bindings[source_id]["mask_id"] != target_mask_id
            for source_id in source_ids
        ):
            raise BuildError(f"mixed target labels in CT group: {source_ids}")
        ct_record = hasher.record(source["asset"]["repo_path"])
        mask_record = hasher.record(mask_repo_path)
        computed = _ct_geometry(
            asset_root / ct_record["repo_path"],
            asset_root / mask_record["repo_path"],
            target_mask_id,
        )
        observed_labels = tuple(
            computed["selection_receipt"]["observed_mask_label_values"]
        )
        previous_labels = observed_labels_by_mask.setdefault(
            mask_repo_path, observed_labels
        )
        if previous_labels != observed_labels:
            raise BuildError(f"inconsistent observed labels for {mask_repo_path}")
        old_laterality = str(source["ground_truth"]["value"]["laterality"])
        old_new_laterality[(old_laterality, computed["laterality"])] += 1
        task_id = f"sv-medi-med_ct_geometry-{index:04d}"
        task, receipt = _make_ct_task(source, source_ids, task_id, computed, binding)
        task["provenance"]["scale_v2_1"]["source_task_sha256"] = [
            _task_hash(item) for item in ordered
        ]
        add(
            task,
            receipt,
            source_ids,
            "candidate",
            "deduplicated_ct_target_label_geometry_recomputed",
        )

    registry_counts_by_mask: dict[str, int] = {}
    for binding in ct_bindings.values():
        mask_repo_path = binding["mask_repo_path"]
        registry_count = binding["registry_mask_label_count"]
        previous_count = registry_counts_by_mask.setdefault(
            mask_repo_path, registry_count
        )
        if previous_count != registry_count:
            raise BuildError(f"inconsistent registry label count for {mask_repo_path}")
    label_count_distribution = Counter(
        len(labels) for labels in observed_labels_by_mask.values()
    )
    registry_count_discrepancies = [
        {
            "mask_repo_path": mask_repo_path,
            "registry_mask_label_count": registry_counts_by_mask[mask_repo_path],
            "observed_mask_label_values": list(labels),
        }
        for mask_repo_path, labels in sorted(observed_labels_by_mask.items())
        if registry_counts_by_mask[mask_repo_path] != len(labels)
    ]
    ct_mapping_audit.update(
        {
            "observed_nonzero_label_count_distribution": {
                str(size): count
                for size, count in sorted(label_count_distribution.items())
            },
            "selected_target_labels_absent": 0,
            "registry_label_count_discrepancies": registry_count_discrepancies,
            "old_to_recomputed_laterality": {
                f"{old}->{new}": count
                for (old, new), count in sorted(old_new_laterality.items())
            },
            "old_laterality_exact_matches": sum(
                count for (old, new), count in old_new_laterality.items() if old == new
            ),
            "old_non_midline_targets": sum(
                count
                for (old, _new), count in old_new_laterality.items()
                if old != "midline"
            ),
            "old_non_midline_direction_matches": sum(
                count
                for (old, new), count in old_new_laterality.items()
                if old != "midline" and old == new
            ),
        }
    )
    expected_registry_discrepancy = {
        "mask_repo_path": "datasets/M3D-RefSeg/M3D_RefSeg/s0021/mask.nii.gz",
        "registry_mask_label_count": 4,
        "observed_mask_label_values": [2, 3, 4],
    }
    if (
        len(observed_labels_by_mask) != 193
        or registry_count_discrepancies != [expected_registry_discrepancy]
        or ct_mapping_audit["old_laterality_exact_matches"] != 54
        or ct_mapping_audit["old_non_midline_targets"] != 184
        or ct_mapping_audit["old_non_midline_direction_matches"] != 54
    ):
        raise BuildError("unexpected M3D observed-label audit")

    # MRI extent tasks are reused from the locked v2 repair output with source provenance.
    mri_tasks = [
        task for task in v2_candidate.values() if _family(task) == "med_mri_anatomy"
    ]
    if len(mri_tasks) != 260:
        raise BuildError(f"expected 260 locked MRI candidates, found {len(mri_tasks)}")
    for source in sorted(mri_tasks, key=lambda item: str(item["task_id"])):
        task, receipt = _reuse_task(source, "locked_v2_mri_extent_contract")
        add(
            task,
            receipt,
            [str(source["task_id"])],
            "candidate",
            "locked_v2_mri_extent_contract",
        )

    # Chemistry binding: only locked answer-confirmed selections are eligible.
    binding_confirmed = {
        task_id
        for task_id, result in compute_by_id.items()
        if result.get("family") == "chem_binding_geometry"
        and (result.get("comparison") or {}).get("answer_confirmed") is True
    }
    if len(binding_confirmed) != 23:
        raise BuildError(
            f"expected 23 confirmed binding tasks, found {len(binding_confirmed)}"
        )
    for source in sorted(by_domain["chemistry"], key=lambda item: str(item["task_id"])):
        if _family(source) != "chem_binding_geometry":
            continue
        task_id = str(source["task_id"])
        if task_id in binding_confirmed:
            asset = hasher.record(source["asset"]["repo_path"])
            task, receipt = _make_binding_task(
                source, compute_by_id[task_id], asset_root / asset["repo_path"]
            )
            add(
                task,
                receipt,
                [task_id],
                "candidate",
                "explicit_atom_selection_confirmed_and_recomputed",
            )
        else:
            task, receipt = _quarantine_task(
                source,
                oracle_version=QUARANTINE_ORACLE,
                reason="binding_entity_or_answer_selection_unconfirmed",
                clear_ground_truth=True,
            )
            add(
                task,
                receipt,
                [task_id],
                "quarantine",
                "binding_entity_or_answer_selection_unconfirmed",
            )

    # Chemistry conformation: recompute a fixed GT-blind explicit four-atom selection.
    for source in sorted(by_domain["chemistry"], key=lambda item: str(item["task_id"])):
        if _family(source) != "chem_conformation":
            continue
        task_id = str(source["task_id"])
        asset = hasher.record(source["asset"]["repo_path"])
        computed = _conformation_oracle(asset_root / asset["repo_path"])
        if computed["status"] != "computed":
            disposition, reason = "quarantine", computed["status"]
        elif computed["robust"]:
            disposition, reason = "candidate", "class_boundary_margin_at_least_10deg"
        else:
            disposition, reason = "quarantine", "class_boundary_margin_below_10deg"
        task, receipt = _make_conformation_task(source, computed, disposition, reason)
        add(task, receipt, [task_id], disposition, reason)

    # Crystallography remains quarantined until a periodic-render receipt exists.
    for source in sorted(by_domain["chemistry"], key=lambda item: str(item["task_id"])):
        if _family(source) != "chem_crystallography":
            continue
        task_id = str(source["task_id"])
        task, receipt = _quarantine_task(
            source,
            oracle_version=QUARANTINE_ORACLE,
            reason="replacement_periodic_geometry_family_pending_render_receipt",
            clear_ground_truth=False,
        )
        add(
            task,
            receipt,
            [task_id],
            "quarantine",
            "replacement_periodic_geometry_family_pending_render_receipt",
        )

    for values in candidates.values():
        values.sort(key=lambda item: str(item["task_id"]))
    for values in quarantine.values():
        values.sort(key=lambda item: str(item["task_id"]))
    receipts.sort(key=lambda item: str(item["receipt_id"]))
    ledger.sort(key=lambda item: str(item["source_task_id"]))
    _validate_partition(set(sources), candidates, quarantine, ledger, receipts)
    all_tasks = [task for values in candidates.values() for task in values] + [
        task for values in quarantine.values() for task in values
    ]
    schema = json.loads((repo_root / TASK_SCHEMA).read_text(encoding="utf-8"))
    _validate_tasks(all_tasks, schema)

    counts = {
        "source_tasks": len(sources),
        "output_tasks": len(all_tasks),
        "candidate": sum(map(len, candidates.values())),
        "candidate_by_domain": {
            domain: len(candidates[domain]) for domain in candidates
        },
        "candidate_by_family": dict(
            sorted(
                Counter(
                    _family(task) for values in candidates.values() for task in values
                ).items()
            )
        ),
        "quarantine": sum(map(len, quarantine.values())),
        "quarantine_by_domain": {
            domain: len(quarantine[domain]) for domain in quarantine
        },
        "quarantine_by_reason": dict(
            sorted(
                Counter(
                    task["disposition_reason"]
                    for values in quarantine.values()
                    for task in values
                ).items()
            )
        ),
        "ledger": len(ledger),
        "oracle_receipts": len(receipts),
        "unique_assets": len(hasher.records),
        "asset_bytes": sum(item["byte_size"] for item in hasher.records),
    }
    expected_candidates = {"biology": 113, "medical": 605, "chemistry": 105}
    if (
        counts["candidate_by_domain"] != expected_candidates
        or counts["candidate"] != 823
    ):
        raise BuildError(
            f"unexpected candidate partition: {counts['candidate_by_domain']}"
        )
    source_lock = _source_lock(repo_root, v1_lock, ct_mapping_audit)
    source_lock["asset_closure"] = {
        "files": len(hasher.records),
        "bytes": counts["asset_bytes"],
        "manifest_sha256": _sha256_bytes(
            (_stable_json(hasher.records) + "\n").encode("utf-8")
        ),
    }
    hasher.verify()
    build_id = _sha256_bytes(
        (_stable_json({"source_lock": source_lock, "counts": counts}) + "\n").encode(
            "utf-8"
        )
    )

    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)
    )
    try:
        for domain in ("biology", "chemistry", "medical"):
            _write_jsonl(
                staging / f"ScienceVision_scale-v2.1-{domain}_candidate_tasks.jsonl",
                candidates[domain],
            )
            _write_jsonl(
                staging
                / "quarantine"
                / f"ScienceVision_scale-v2.1-{domain}_quarantine_tasks.jsonl",
                quarantine[domain],
            )
        _write_jsonl(staging / "oracle_receipts.jsonl", receipts)
        _write_jsonl(staging / "task_ledger.jsonl", ledger)
        (staging / "source-lock.json").write_text(
            _stable_json(source_lock, pretty=True), encoding="utf-8"
        )
        (staging / "generic_deterministic_rubric.schema.json").write_text(
            _stable_json(_rubric_schema(), pretty=True), encoding="utf-8"
        )
        readme = (
            "# ScienceVision scale-v2.1\n\n"
            "Deterministic candidate build from locked scale-v1/v2 inputs and local asset bytes.\n\n"
            f"- Build ID: `{build_id}`\n"
            f"- Candidates: {counts['candidate']} (biology {counts['candidate_by_domain']['biology']}, "
            f"medical {counts['candidate_by_domain']['medical']}, chemistry {counts['candidate_by_domain']['chemistry']})\n"
            f"- Quarantine: {counts['quarantine']}\n"
            f"- Source ledger: {counts['ledger']} records\n"
            f"- Asset closure: {counts['unique_assets']} files, {counts['asset_bytes']} bytes\n\n"
            "Raw-coordinate computability is recorded as an access property, not treated as proof of answer exploitation.\n"
        )
        (staging / "README.md").write_text(readme, encoding="utf-8")
        output_records = []
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                output_records.append(
                    {
                        "path": path.relative_to(staging).as_posix(),
                        "sha256": _sha256_file(path),
                        "byte_size": path.stat().st_size,
                    }
                )
        build = {
            "schema_version": BUILD_SCHEMA,
            "dataset": DATASET,
            "build_id": build_id,
            "generated_at": generated_at,
            "publication_status": "candidate_pending_post_build_qc_and_baselines",
            "generation_command": (
                "uv run --no-project --isolated --python 3.12 --with gemmi==0.7.5 "
                "--with nibabel==5.3.2 --with numpy==2.5.1 --with rdkit==2026.3.5 "
                "--with jsonschema python -m scripts.qa_gen_scale_v2_1 --asset-root $ASSET_ROOT"
            ),
            "counts": counts,
            "source_lock_sha256": _sha256_file(staging / "source-lock.json"),
            "outputs": output_records,
            "promotion_gate": {
                "task_schema": "passed",
                "asset_hash_closure": "passed",
                "ledger_partition": "passed",
                "post_build_static_qc": "required",
                "baseline_evaluation": "required",
            },
        }
        (staging / "scale-v2.1-build.json").write_text(
            _stable_json(build, pretty=True), encoding="utf-8"
        )
        if publish:
            _publish(staging, output_dir)
        else:
            return {**build, "staging_dir": staging.as_posix()}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return build


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generated-at", default=GENERATED_AT_DEFAULT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir or repo_root / "QA-Gen/tasks/scale-v2.1"
    build = build_scale_v2_1(
        repo_root,
        args.asset_root,
        output_dir,
        generated_at=args.generated_at,
    )
    print(
        _stable_json(
            {"build_id": build["build_id"], "counts": build["counts"]}, pretty=True
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
