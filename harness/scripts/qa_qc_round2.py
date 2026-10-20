"""Fail-closed materialization QC for the reviewed scale-v2 candidate build.

This command is intentionally read-only with respect to source and candidate task
JSONL.  It validates the locked scale-v1 -> scale-v2 build, reruns ordinary QC,
then checks whether every referenced target can actually be staged and rendered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import qa_qc, qa_qc_repair
from scripts._qa_qc_report import render_markdown, write_report


ROOT = Path(__file__).resolve().parents[1]
ROUND2_SCHEMA_VERSION = "sciencevision.qa_qc.round2.v1"
DEFAULT_CANDIDATE_DIR = ROOT / "QA-Gen" / "tasks" / "scale-v2"
DEFAULT_SOURCE_DIR = ROOT / "QA-Gen" / "tasks" / "scale-v1-taxonomy-input-20260805"
DEFAULT_SOURCE_QC_DIR = ROOT / "outputs" / "qa-qc" / "scale-v1-reviewed-20260808"
DEFAULT_SOURCE_LOCK = ROOT / "docs" / "qa-gen" / "SCALE_V1_REVIEWED_20260808_LOCK.json"
DEFAULT_BUILD_LOCK = ROOT / "docs" / "qa-gen" / "SCALE_V2_BUILD_20260808_LOCK.json"
DEFAULT_REGISTRY = ROOT / "datasets" / "_matched" / "m3d-refseg.json"
DEFAULT_PRIOR_QC_DIR = ROOT / "outputs" / "qa-qc" / "scale-v2-repaired-20260808"
DEFAULT_SCHEMA = qa_qc_repair.DEFAULT_SCHEMA
MULTI_ASSET_FAMILY = "bio_multi_structure_compare"
CT_FAMILY = "med_ct_lesion"
MRI_FAMILY = "med_mri_anatomy"
MULTI_ASSET_RECEIPT_SCHEMA_VERSION = (
    "sciencevision.qa_qc.multi_asset_materialization_receipt.v1"
)
MULTI_ASSET_STAGER_CAPABILITY = "harbor_multi_asset_v1"


class Round2Error(ValueError):
    """Raised when the locked build cannot be audited safely."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Round2Error(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Round2Error(f"expected JSON object in {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Round2Error(f"cannot read JSONL {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Round2Error(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise Round2Error(f"expected object at {path}:{line_number}")
        values.append(value)
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _task_id_map(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise Round2Error(f"{label} row has no task_id")
        if task_id in result:
            raise Round2Error(f"duplicate {label} task_id {task_id!r}")
        result[task_id] = row
    return result


def _git_tracked(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return False
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", relative.as_posix()],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0


def _validate_build_pin(
    build_path: Path,
    build_lock_path: Path | None,
    expected_build_sha256: str | None,
) -> dict[str, Any]:
    actual = _sha256(build_path)
    if build_lock_path is None and expected_build_sha256 is None:
        raise Round2Error("an external build manifest SHA256 pin is required")
    pin: dict[str, Any] = {"actual_sha256": actual}
    if build_lock_path is not None:
        lock = _read_json(build_lock_path)
        if lock.get("schema_version") != "sciencevision.qa_qc.build_manifest_lock.v1":
            raise Round2Error("build lock schema is unsupported")
        locked_path = lock.get("build_manifest_path")
        locked_sha = lock.get("build_manifest_sha256")
        if not isinstance(locked_path, str) or not isinstance(locked_sha, str):
            raise Round2Error("build lock is missing manifest path or SHA256")
        resolved_locked_path = (ROOT / locked_path).resolve()
        if resolved_locked_path != build_path.resolve():
            raise Round2Error("build lock points to a different manifest")
        if locked_sha != actual:
            raise Round2Error("build manifest SHA256 does not match build lock")
        tracked = _git_tracked(build_lock_path)
        pin.update(
            {
                "lock_path": str(build_lock_path),
                "lock_sha256": _sha256(build_lock_path),
                "lock_tracked_by_git": tracked,
                "trust_anchor_status": (
                    "version_controlled" if tracked else "pending_git_commit"
                ),
            }
        )
    if expected_build_sha256 is not None:
        if expected_build_sha256 != actual:
            raise Round2Error("build manifest SHA256 does not match CLI pin")
        pin["cli_expected_sha256"] = expected_build_sha256
        pin["trust_anchor_status"] = "cli_expected_hash"
    return pin


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve()
    right_resolved = right.resolve()
    return (
        left_resolved == right_resolved
        or left_resolved in right_resolved.parents
        or right_resolved in left_resolved.parents
    )


def _validate_output_dir(out_dir: Path, protected: list[Path]) -> None:
    for path in protected:
        if _paths_overlap(out_dir, path):
            raise Round2Error(
                f"output directory overlaps protected input path: {out_dir} vs {path}"
            )


def _validate_canonical_baselines(values: list[str]) -> list[str]:
    canonical = list(qa_qc.DEFAULT_EVAL_BASELINES)
    if values != canonical:
        raise Round2Error(
            "round2 requires the exact canonical baseline matrix: "
            + ", ".join(canonical)
        )
    return canonical


def validate_locked_partition(
    candidate_dir: Path,
    source_dir: Path,
    source_qc_dir: Path,
    source_lock_path: Path,
    build_lock_path: Path | None,
    expected_build_sha256: str | None,
    schema_path: Path,
) -> tuple[
    dict[str, Any],
    list[Path],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    """Validate lock, manifest hashes, schema, and candidate/quarantine partition."""
    source_records, source_files = qa_qc_repair._load_tasks(source_dir)
    source_qc, qc_files = qa_qc_repair._load_qc(source_qc_dir)
    source_lock = qa_qc_repair._validate_source_lock(
        source_lock_path, source_files, qc_files
    )
    qa_qc_repair._strict_join(source_records, source_qc)

    build_path = candidate_dir / "scale-v2-build.json"
    build_pin = _validate_build_pin(build_path, build_lock_path, expected_build_sha256)
    build = _read_json(build_path)
    inputs = build.get("inputs")
    if not isinstance(inputs, dict):
        raise Round2Error("scale-v2 build manifest has no inputs object")
    expected_inputs = {
        "task_files": source_files,
        "qc_files": qc_files,
        "source_lock": source_lock,
        "task_schema_sha256": _sha256(schema_path),
    }
    for key, expected in expected_inputs.items():
        if inputs.get(key) != expected:
            raise Round2Error(f"scale-v2 build input drift for {key}")

    outputs = build.get("outputs")
    if not isinstance(outputs, list):
        raise Round2Error("scale-v2 build manifest has no outputs list")
    for output in outputs:
        if not isinstance(output, dict) or not isinstance(output.get("path"), str):
            raise Round2Error("invalid scale-v2 output manifest row")
        path = candidate_dir / output["path"]
        if not path.is_file():
            raise Round2Error(f"manifest output is missing: {path}")
        if _sha256(path) != output.get("sha256"):
            raise Round2Error(f"manifest output hash drift: {path}")
        if len(_read_jsonl(path)) != output.get("task_count"):
            raise Round2Error(f"manifest output count drift: {path}")

    candidate_paths = sorted(
        candidate_dir.glob("ScienceVision_scale-v2-*_candidate_tasks.jsonl")
    )
    quarantine_paths = sorted(
        (candidate_dir / "quarantine").glob(
            "ScienceVision_scale-v2-*_quarantine_tasks.jsonl"
        )
    )
    if not candidate_paths or not quarantine_paths:
        raise Round2Error("candidate or quarantine task files are missing")
    candidate_rows = [row for path in candidate_paths for row in _read_jsonl(path)]
    quarantine_rows = [row for path in quarantine_paths for row in _read_jsonl(path)]
    candidates = _task_id_map(candidate_rows, "candidate")
    quarantined = _task_id_map(quarantine_rows, "quarantine")
    if set(candidates) & set(quarantined):
        raise Round2Error("candidate and quarantine task IDs overlap")
    source_ids = {record.task["task_id"] for record in source_records}
    if set(candidates) | set(quarantined) != source_ids:
        raise Round2Error("candidate/quarantine partition does not cover locked source")
    qa_qc_repair._validate_schema(candidate_rows + quarantine_rows, schema_path)

    decisions = _read_jsonl(candidate_dir / "scale-v2-decisions.jsonl")
    decision_ids: set[str] = set()
    for decision in decisions:
        source = decision.get("source")
        task_id = source.get("task_id") if isinstance(source, dict) else None
        if not isinstance(task_id, str) or not task_id or task_id in decision_ids:
            raise Round2Error("decision ledger has a missing or duplicate task ID")
        decision_ids.add(task_id)
        disposition = decision.get("disposition")
        if disposition == "candidate" and task_id not in candidates:
            raise Round2Error(f"candidate decision/file mismatch for {task_id}")
        if disposition == "quarantine" and task_id not in quarantined:
            raise Round2Error(f"quarantine decision/file mismatch for {task_id}")
        if disposition not in {"candidate", "quarantine"}:
            raise Round2Error(f"invalid disposition for {task_id}")
    if decision_ids != source_ids:
        raise Round2Error("decision ledger does not cover locked source")

    counts = build.get("counts") if isinstance(build.get("counts"), dict) else {}
    if counts.get("candidate") != len(candidates) or counts.get("quarantine") != len(
        quarantined
    ):
        raise Round2Error("build manifest candidate/quarantine counts drifted")

    integrity = {
        "status": "valid",
        "source_lock": source_lock,
        "source_count": len(source_records),
        "source_qc_count": len(source_qc),
        "source_qc_join": "strict_1_to_1",
        "candidate_count": len(candidates),
        "quarantine_count": len(quarantined),
        "decision_count": len(decisions),
        "partition": "disjoint_and_complete",
        "schema_validated_task_count": len(candidate_rows) + len(quarantine_rows),
        "build_manifest": {
            "path": str(build_path),
            "sha256": _sha256(build_path),
            "output_hashes": "valid",
        },
        "build_manifest_pin": build_pin,
    }
    return integrity, candidate_paths, candidates, quarantined, decisions


def _registry_by_qid(path: Path) -> dict[str, dict[str, Any]]:
    registry = _read_json(path)
    records = registry.get("records")
    if not isinstance(records, list):
        raise Round2Error(f"registry {path} has no records list")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        qid = record.get("qid") if isinstance(record, dict) else None
        if not isinstance(qid, str) or not qid or qid in result:
            raise Round2Error(f"registry {path} has a missing or duplicate qid")
        result[qid] = record
    if registry.get("count") != len(result):
        raise Round2Error(f"registry {path} count does not match records")
    return result


def _candidate_paths(
    asset: dict[str, Any], asset_root: Path, repo_key: str, path_key: str
) -> list[Path]:
    candidates: list[Path] = []
    repo_value = asset.get(repo_key)
    if isinstance(repo_value, str) and repo_value:
        repo_path = Path(repo_value)
        candidates.append(
            repo_path if repo_path.is_absolute() else asset_root / repo_path
        )
    raw_value = asset.get(path_key)
    if isinstance(raw_value, str) and raw_value:
        raw_path = Path(raw_value)
        if raw_path.is_absolute():
            candidates.extend([raw_path, asset_root / raw_value.lstrip("/")])
        else:
            candidates.append(asset_root / raw_path)
    return list(dict.fromkeys(path.resolve(strict=False) for path in candidates))


def _resolve_asset_path(
    asset: dict[str, Any],
    asset_root: Path,
    *,
    repo_key: str = "repo_path",
    path_key: str = "path",
) -> tuple[Path | None, list[str]]:
    candidates = _candidate_paths(asset, asset_root, repo_key, path_key)
    return next((path for path in candidates if path.is_file()), None), [
        str(path) for path in candidates
    ]


def _asset_objects(task: dict[str, Any]) -> list[dict[str, Any]]:
    assets = task.get("assets")
    if isinstance(assets, list) and assets:
        return [asset for asset in assets if isinstance(asset, dict)]
    asset = task.get("asset")
    return [asset] if isinstance(asset, dict) else []


def _geometry(mask: Any) -> dict[str, Any]:
    import numpy as np

    count = int(np.count_nonzero(mask))
    if not count:
        return {"voxel_count": 0, "extent_vox": [0] * int(mask.ndim)}
    indices = np.nonzero(mask)
    extent = [int(axis.max() - axis.min() + 1) for axis in indices]
    return {"voxel_count": count, "extent_vox": extent}


class MaskGeometryCache:
    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, Any]] = {}

    def get(self, path: Path) -> dict[str, Any]:
        if path not in self._cache:
            try:
                import nibabel as nib
                import numpy as np
            except ImportError as exc:
                raise Round2Error(
                    "CT materialization audit requires nibabel and numpy"
                ) from exc
            try:
                array = np.asanyarray(nib.load(str(path)).dataobj)
            except Exception as exc:  # noqa: BLE001 - report corrupt/unreadable masks
                raise Round2Error(f"cannot read mask {path}: {exc}") from exc
            labels = sorted({int(value) for value in np.unique(array) if int(value)})
            self._cache[path] = {
                "labels": labels,
                "union": _geometry(array != 0),
                "by_label": {str(label): _geometry(array == label) for label in labels},
            }
        return self._cache[path]


class NiftiPreflightCache:
    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, Any]] = {}

    def get(self, path: Path) -> dict[str, Any]:
        if path in self._cache:
            return self._cache[path]
        try:
            import nibabel as nib
            import numpy as np
        except ImportError as exc:
            result = {
                "status": "not_run",
                "reason": f"dependency unavailable: {exc.name}",
                "valid": False,
            }
            self._cache[path] = result
            return result
        try:
            image = nib.load(str(path))
            shape = [int(value) for value in image.shape]
            affine = np.asarray(image.affine, dtype=float)
            data = np.asanyarray(image.dataobj)
            nonzero = int(np.count_nonzero(data))
            affine_valid = bool(
                affine.shape == (4, 4)
                and np.isfinite(affine).all()
                and abs(float(np.linalg.det(affine[:3, :3]))) > 1e-12
            )
            shape_valid = len(shape) >= 3 and all(value > 0 for value in shape)
            result = {
                "status": "checked",
                "valid": bool(shape_valid and affine_valid and nonzero > 0),
                "shape": shape,
                "shape_valid": shape_valid,
                "affine_valid": affine_valid,
                "nonzero_voxel_count": nonzero,
                "dtype": str(data.dtype),
            }
        except Exception as exc:  # noqa: BLE001 - evidence for invalid files
            result = {
                "status": "checked",
                "valid": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        self._cache[path] = result
        return result


class MolecularPreflightCache:
    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, Any]] = {}

    def get(self, path: Path) -> dict[str, Any]:
        if path in self._cache:
            return self._cache[path]
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                prefix = handle.read(64 * 1024).decode("utf-8", errors="replace")
        except OSError as exc:
            result = {
                "status": "checked",
                "valid": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self._cache[path] = result
            return result
        basic_valid = size > 0 and any(
            line.lstrip().startswith("data_") for line in prefix.splitlines()
        )
        result: dict[str, Any] = {
            "status": "checked",
            "valid": basic_valid,
            "size_bytes": size,
            "mmcif_data_block_header": basic_valid,
            "parser_status": "not_run",
        }
        try:
            import gemmi
        except ImportError:
            result["parser_status"] = "not_run_dependency_unavailable"
        else:
            try:
                structure = gemmi.read_structure(str(path))
                atom_count = sum(
                    1
                    for model in structure
                    for chain in model
                    for residue in chain
                    for _atom in residue
                )
                result.update(
                    {
                        "parser_status": "checked",
                        "parser": "gemmi.read_structure",
                        "model_count": len(structure),
                        "atom_count": atom_count,
                        "valid": bool(basic_valid and len(structure) and atom_count),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - evidence for invalid files
                result.update(
                    {
                        "parser_status": "checked",
                        "valid": False,
                        "parser_error": f"{type(exc).__name__}: {exc}",
                    }
                )
        self._cache[path] = result
        return result


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _task_mask_id(task: dict[str, Any]) -> int | None:
    asset = task.get("asset") if isinstance(task.get("asset"), dict) else {}
    verified = task.get("asset_grounding", {}).get("verified_entities", {})
    for value in (
        asset.get("mask_id"),
        asset.get("mask_label_id"),
        verified.get("mask_id") if isinstance(verified, dict) else None,
        verified.get("target_mask_id") if isinstance(verified, dict) else None,
    ):
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None


def _stored_geometry(task: dict[str, Any]) -> dict[str, Any] | None:
    verified = task.get("asset_grounding", {}).get("verified_entities", {})
    if not isinstance(verified, dict):
        return None
    count = _as_int(verified.get("voxel_count"))
    extent = verified.get("extent_vox")
    if count is None or not isinstance(extent, list):
        return None
    parsed_extent = [_as_int(value) for value in extent]
    if any(value is None for value in parsed_extent):
        return None
    return {"voxel_count": count, "extent_vox": parsed_extent}


def _staged_asset_rows(staged: dict[str, Any]) -> list[dict[str, Any]]:
    rows = staged.get("assets")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    path = staged.get("asset_path")
    return [{"path": path}] if isinstance(path, str) and path else []


def _build_multi_asset_materialization_receipt(
    task: dict[str, Any], asset_root: Path
) -> dict[str, Any]:
    """Dry-run the live Harbor flattening and both raw-asset staging routes."""
    from scripts.harbor import build_harbor_tasks as harbor_builder
    from scripts.harbor.qa_gen_to_tasks import qa_record_to_task

    declared_count = len(_asset_objects(task))
    receipt: dict[str, Any] = {
        "schema_version": MULTI_ASSET_RECEIPT_SCHEMA_VERSION,
        "stager_capability": MULTI_ASSET_STAGER_CAPABILITY,
        "status": "error",
        "declared_asset_count": declared_count,
    }
    try:
        flattened = qa_record_to_task(task, asset_root=asset_root)
        flattened_rows = harbor_builder._declared_asset_rows(flattened)
        with tempfile.TemporaryDirectory(
            prefix="sciencevision-round2-materialization-"
        ) as temp_dir:
            temp_root = Path(temp_dir)
            host = harbor_builder._stage_host_paths(flattened, temp_root / "host")
            container_root = temp_root / "container"
            container = harbor_builder._stage_assets(flattened, container_root)
            host_rows = _staged_asset_rows(host)
            container_rows = _staged_asset_rows(container)
            receipt.update(
                {
                    "status": "supported",
                    "flattened_asset_count": len(flattened_rows),
                    "host_staged_asset_count": len(host_rows),
                    "container_staged_asset_count": len(container_rows),
                    "host_primary_present": bool(host.get("asset_path")),
                    "container_primary_present": bool(container.get("asset_path")),
                    "host_paths_exist": bool(host_rows)
                    and all(Path(str(row.get("path", ""))).is_file() for row in host_rows),
                    "staged_files_exist": bool(container_rows)
                    and all(
                        (
                            container_root
                            / "asset"
                            / Path(str(row.get("path", ""))).name
                        ).is_file()
                        for row in container_rows
                    ),
                }
            )
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    return receipt


def _multi_asset_receipt_supported(receipt: dict[str, Any]) -> bool:
    return bool(
        receipt.get("schema_version") == MULTI_ASSET_RECEIPT_SCHEMA_VERSION
        and receipt.get("stager_capability") == MULTI_ASSET_STAGER_CAPABILITY
        and receipt.get("status") != "unsupported"
    )


def _multi_asset_receipt_complete(
    receipt: dict[str, Any], expected_count: int
) -> bool:
    count_keys = (
        "declared_asset_count",
        "flattened_asset_count",
        "host_staged_asset_count",
        "container_staged_asset_count",
    )
    return bool(
        _multi_asset_receipt_supported(receipt)
        and receipt.get("status") == "supported"
        and expected_count >= 2
        and all(receipt.get(key) == expected_count for key in count_keys)
        and receipt.get("host_primary_present") is True
        and receipt.get("container_primary_present") is True
        and receipt.get("host_paths_exist") is True
        and receipt.get("staged_files_exist") is True
    )


def audit_task_materialization(
    task: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    asset_root: Path,
    mask_cache: MaskGeometryCache,
    nifti_cache: NiftiPreflightCache | None = None,
    molecular_cache: MolecularPreflightCache | None = None,
    detail: dict[str, Any] | None = None,
    materialization_receipt: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    nifti_cache = nifti_cache or NiftiPreflightCache()
    molecular_cache = molecular_cache or MolecularPreflightCache()
    if detail is None:
        detail = {}
    detail.update(
        {
            "materialization_scope": "static/preflight",
            "render_status": "not_run",
        }
    )
    findings: list[dict[str, Any]] = []
    family = task.get("knowledge", {}).get("direction_id")
    assets = _asset_objects(task)
    primary = task.get("asset") if isinstance(task.get("asset"), dict) else {}
    primary_key = primary.get("asset_id") or primary.get("path")
    checks: list[dict[str, Any]] = []
    resolved_assets: list[tuple[dict[str, Any], Path | None]] = []
    for index, asset in enumerate(assets):
        resolved, attempts = _resolve_asset_path(asset, asset_root)
        resolved_assets.append((asset, resolved))
        key = asset.get("asset_id") or asset.get("path") or f"asset-{index}"
        checks.append(
            {
                "asset_id": key,
                "primary": key == primary_key,
                "declared_exists_on_disk": asset.get("exists_on_disk"),
                "resolved_path": str(resolved) if resolved else None,
                "attempted_paths": attempts,
                "exists": resolved is not None,
                "format": asset.get("format"),
            }
        )
    detail["asset_checks"] = checks
    missing = [check for check in checks if not check["exists"]]
    if missing:
        findings.append(
            qa_qc._finding(
                "asset_declared_present_but_missing",
                "error",
                f"{len(missing)} required bound asset(s) cannot be resolved in the workspace.",
                evidence={
                    "assets": checks,
                    "primary_missing": any(check["primary"] for check in missing),
                    "asset_root": str(asset_root.resolve()),
                },
            )
        )

    molecular_checks: list[dict[str, Any]] = []
    for asset, resolved in resolved_assets:
        asset_format = str(asset.get("format") or "").casefold()
        if resolved is None or asset_format not in {"mmcif", "cif"}:
            continue
        check = {
            "asset_id": asset.get("asset_id"),
            "path": str(resolved),
            **molecular_cache.get(resolved),
        }
        molecular_checks.append(check)
        if not check["valid"]:
            findings.append(
                qa_qc._finding(
                    "molecular_file_preflight_invalid",
                    "error",
                    "A staged molecular structure fails nonempty/mmCIF preflight validation.",
                    evidence=check,
                )
            )
    if molecular_checks:
        detail["molecular_file_checks"] = molecular_checks

    if family == MULTI_ASSET_FAMILY:
        receipt = materialization_receipt
        if receipt is None:
            receipt = _build_multi_asset_materialization_receipt(task, asset_root)
        detail["multi_asset_materialization_receipt"] = receipt
        if not _multi_asset_receipt_supported(receipt):
            findings.append(
                qa_qc._finding(
                    "multi_asset_staging_unsupported",
                    "error",
                    "The declared Harbor materialization receipt does not support multi-asset staging.",
                    evidence={"expected_asset_count": len(assets), "receipt": receipt},
                )
            )
        elif len(assets) < 2 or missing or not _multi_asset_receipt_complete(
            receipt, len(assets)
        ):
            findings.append(
                qa_qc._finding(
                    "multi_asset_materialization_incomplete",
                    "error",
                    "The comparison task cannot materialize every bound structure.",
                    evidence={
                        "expected_asset_count": len(assets),
                        "assets": checks,
                        "receipt": receipt,
                    },
                )
            )

    if family == MRI_FAMILY:
        image_path, image_attempts = _resolve_asset_path(primary, asset_root)
        mask_path, mask_attempts = _resolve_asset_path(
            primary,
            asset_root,
            repo_key="mask_repo_path",
            path_key="mask_path",
        )
        if mask_path is None:
            findings.append(
                qa_qc._finding(
                    "asset_declared_present_but_missing",
                    "error",
                    "The MRI task's required segmentation mask cannot be resolved.",
                    evidence={"role": "target_mask", "attempted_paths": mask_attempts},
                )
            )
        nifti_checks = {
            "image": (
                {"path": str(image_path), **nifti_cache.get(image_path)}
                if image_path is not None
                else {
                    "status": "not_run",
                    "valid": False,
                    "attempted_paths": image_attempts,
                }
            ),
            "mask": (
                {"path": str(mask_path), **nifti_cache.get(mask_path)}
                if mask_path is not None
                else {
                    "status": "not_run",
                    "valid": False,
                    "attempted_paths": mask_attempts,
                }
            ),
        }
        image_shape = nifti_checks["image"].get("shape")
        mask_shape = nifti_checks["mask"].get("shape")
        shape_match = bool(image_shape and image_shape == mask_shape)
        nifti_checks["image_mask_shape_match"] = shape_match
        detail["nifti_checks"] = nifti_checks
        if not (
            nifti_checks["image"].get("valid")
            and nifti_checks["mask"].get("valid")
            and shape_match
        ):
            findings.append(
                qa_qc._finding(
                    "nifti_preflight_invalid",
                    "error",
                    "The MRI image/mask pair fails load, nonempty, shape, or affine preflight validation.",
                    evidence=nifti_checks,
                )
            )
        return findings

    if family != CT_FAMILY:
        return findings

    qid = primary.get("qid")
    record = registry.get(qid) if isinstance(qid, str) else None
    if record is None:
        findings.append(
            qa_qc._finding(
                "target_mask_registry_join_missing",
                "error",
                "The CT task cannot be joined to a unique M3D-RefSeg registry record.",
                evidence={"qid": qid},
            )
        )
        return findings
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    registry_mask_id = _as_int(extra.get("mask_id"))
    task_mask_id = _task_mask_id(task)
    mask_evidence = {
        "qid": qid,
        "task_mask_id": task_mask_id,
        "registry_mask_id": registry_mask_id,
        "registry_mask_label_count": _as_int(extra.get("mask_label_count")),
        "renderer_contract": {
            "mask_path_forwarded": True,
            "mask_id_forwarded": False,
            "best_slice_selection": "labels[0]",
        },
    }
    if task_mask_id is None:
        findings.append(
            qa_qc._finding(
                "target_mask_binding_missing",
                "warning",
                "The CT task binds a mask file but not the registry target mask_id.",
                evidence=mask_evidence,
            )
        )
    elif task_mask_id != registry_mask_id:
        findings.append(
            qa_qc._finding(
                "target_mask_binding_mismatch",
                "error",
                "The task mask_id differs from the reviewed registry target label.",
                evidence=mask_evidence,
            )
        )

    mask_path, attempts = _resolve_asset_path(
        primary,
        asset_root,
        repo_key="mask_repo_path",
        path_key="mask_path",
    )
    if mask_path is None:
        findings.append(
            qa_qc._finding(
                "asset_declared_present_but_missing",
                "error",
                "The required CT target mask file cannot be resolved in the workspace.",
                evidence={"role": "target_mask", "attempted_paths": attempts},
            )
        )
        return findings
    if registry_mask_id is None:
        return findings

    geometry = mask_cache.get(mask_path)
    target = geometry["by_label"].get(str(registry_mask_id))
    stored = _stored_geometry(task)
    geometry_evidence = {
        **mask_evidence,
        "mask_path": str(mask_path),
        "observed_labels": geometry["labels"],
        "stored_geometry": stored,
        "union_geometry": geometry["union"],
        "target_geometry": target,
    }
    if target is None or stored is None:
        findings.append(
            qa_qc._finding(
                "target_mask_geometry_mismatch",
                "error",
                "Target-label or stored CT geometry is unavailable for verification.",
                evidence=geometry_evidence,
            )
        )
    elif stored == geometry["union"] and stored != target:
        findings.append(
            qa_qc._finding(
                "target_mask_geometry_uses_union",
                "error",
                "Stored CT grounding geometry matches the nonzero mask union, not the registry target label.",
                evidence=geometry_evidence,
            )
        )
    elif stored != target:
        findings.append(
            qa_qc._finding(
                "target_mask_geometry_mismatch",
                "error",
                "Stored CT grounding geometry does not match the registry target label.",
                evidence=geometry_evidence,
            )
        )
    return findings


def _refresh_summary(report: dict[str, Any]) -> None:
    task_results = report["tasks"]
    suite_findings = report["suite_findings"]
    all_findings = suite_findings + [
        finding for result in task_results for finding in result["findings"]
    ]
    task_codes = Counter(
        finding["code"] for result in task_results for finding in result["findings"]
    )
    suite_codes = Counter(finding["code"] for finding in suite_findings)
    families: dict[str, dict[str, Any]] = {}
    for result in task_results:
        cell = families.setdefault(
            result["family"],
            {
                "task_count": 0,
                "grades": Counter(),
                "labels": Counter(),
                "decisions": Counter(),
            },
        )
        cell["task_count"] += 1
        cell["grades"][result["classification"]["grade"]] += 1
        cell["labels"].update(result["classification"]["labels"])
        cell["decisions"][result["decision"]] += 1
    summary = report["summary"]
    summary.update(
        {
            "decisions": dict(
                sorted(Counter(r["decision"] for r in task_results).items())
            ),
            "grades": dict(
                sorted(
                    Counter(r["classification"]["grade"] for r in task_results).items()
                )
            ),
            "labels": dict(
                sorted(
                    Counter(
                        label
                        for result in task_results
                        for label in result["classification"]["labels"]
                    ).items()
                )
            ),
            "by_family": {
                family: {
                    "task_count": cell["task_count"],
                    "grades": dict(sorted(cell["grades"].items())),
                    "labels": dict(sorted(cell["labels"].items())),
                    "decisions": dict(sorted(cell["decisions"].items())),
                }
                for family, cell in sorted(families.items())
            },
            "findings_by_severity": dict(
                sorted(Counter(f["severity"] for f in all_findings).items())
            ),
            "findings_by_code": {
                "task": dict(sorted(task_codes.items())),
                "suite": dict(sorted(suite_codes.items())),
                "all": dict(sorted((task_codes + suite_codes).items())),
            },
        }
    )


def audit_quarantine(
    quarantined: dict[str, dict[str, Any]], decisions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for decision in decisions:
        if decision.get("disposition") != "quarantine":
            continue
        task_id = decision["source"]["task_id"]
        task = quarantined[task_id]
        qc = decision.get("qc") if isinstance(decision.get("qc"), dict) else {}
        classification = {
            **qc,
            "reasons": [
                {"code": code}
                for code in qc.get("reason_codes", [])
                if isinstance(code, str)
            ],
            "leakage": qc.get("leakage_summary", []),
        }
        recomputed = qa_qc_repair._quarantine_reasons(
            task,
            classification,
            set(decision.get("repaired_qc_findings", [])),
        )
        stored = list(decision.get("disposition_reasons", []))
        obsolete = [reason for reason in stored if reason not in recomputed]
        newly_required = [reason for reason in recomputed if reason not in stored]
        status = "current" if not obsolete and not newly_required else "reason_drift"
        status_counts[status] += 1
        reason_counts.update(recomputed)
        rows.append(
            {
                "schema_version": ROUND2_SCHEMA_VERSION,
                "task_id": task_id,
                "domain": decision.get("domain"),
                "family": decision.get("family"),
                "status": status,
                "stored_reasons": stored,
                "recomputed_reasons": recomputed,
                "obsolete_reasons": obsolete,
                "newly_required_reasons": newly_required,
                "basis": "current repaired task plus locked QC evidence",
            }
        )
    if len(rows) != len(quarantined):
        raise Round2Error("quarantine audit does not cover every quarantined task")
    return rows, {
        "task_count": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "recomputed_reason_counts": dict(sorted(reason_counts.items())),
        "obsolete_reason_task_count": sum(
            bool(row["obsolete_reasons"]) for row in rows
        ),
        "new_reason_task_count": sum(
            bool(row["newly_required_reasons"]) for row in rows
        ),
    }


def build_round2_report(
    *,
    candidate_dir: Path,
    source_dir: Path,
    source_qc_dir: Path,
    source_lock_path: Path,
    build_lock_path: Path | None,
    expected_build_sha256: str | None,
    registry_path: Path,
    schema_path: Path,
    asset_root: Path,
    expected_baselines: list[str],
    jobs_roots: list[Path] | None = None,
    suite_name: str = "scale-v2 round2 materialization QC",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    expected_baselines = _validate_canonical_baselines(expected_baselines)
    integrity, candidate_paths, candidates, quarantined, decisions = (
        validate_locked_partition(
            candidate_dir,
            source_dir,
            source_qc_dir,
            source_lock_path,
            build_lock_path,
            expected_build_sha256,
            schema_path,
        )
    )
    report = qa_qc.build_report(
        candidate_paths,
        jobs_roots=jobs_roots or [],
        expected_baselines=expected_baselines,
        suite_name=suite_name,
    )
    if {result["task_id"] for result in report["tasks"]} != set(candidates):
        raise Round2Error("post-QC task IDs do not match candidate partition")

    registry = _registry_by_qid(registry_path)
    mask_cache = MaskGeometryCache()
    nifti_cache = NiftiPreflightCache()
    molecular_cache = MolecularPreflightCache()
    materialization_counts: Counter[str] = Counter()
    for result in report["tasks"]:
        task = candidates[result["task_id"]]
        materialization: dict[str, Any] = {}
        added = audit_task_materialization(
            task,
            registry,
            asset_root,
            mask_cache,
            nifti_cache,
            molecular_cache,
            materialization,
        )
        result["findings"].extend(added)
        materialization_counts.update(finding["code"] for finding in added)
        qa_qc._finalize_task(result, expected_baselines)
        materialization["preflight_status"] = (
            "preflight_blocked"
            if any(finding["severity"] == "error" for finding in added)
            else "preflight_needs_fix"
            if added
            else "preflight_pass"
        )
        result["materialization"] = materialization
        result["classification"].update(
            {
                "materialization_scope": "static/preflight",
                "materialization_status": materialization["preflight_status"],
                "render_status": "not_run",
            }
        )

    blocked = [result for result in report["tasks"] if result["decision"] == "block"]
    report["suite_findings"].append(
        qa_qc._finding(
            "round2_materialization_blockers",
            "error" if blocked else "info",
            f"Round2 materialization audit blocks {len(blocked)}/{len(report['tasks'])} candidates.",
            evidence={
                "blocked_task_count": len(blocked),
                "finding_counts": dict(sorted(materialization_counts.items())),
            },
            action="Resolve target binding and staging blockers before running external baselines.",
        )
    )
    quarantine_rows, quarantine_summary = audit_quarantine(quarantined, decisions)
    if quarantine_summary["status_counts"].get("reason_drift"):
        report["suite_findings"].append(
            qa_qc._finding(
                "quarantine_reason_drift",
                "error",
                "Stored quarantine reasons differ from recomputation over current tasks and locked QC evidence.",
                evidence=quarantine_summary,
                action="Review the affected quarantine decisions; do not promote them automatically.",
            )
        )

    _refresh_summary(report)
    coverage_complete = all(
        cell["observed"] == cell["expected"]
        for cell in report["summary"]["eval_coverage"].values()
    )
    gate_blockers = {
        "materialization_blocked_tasks": len(blocked),
        "quarantine_reason_drift_tasks": quarantine_summary["status_counts"].get(
            "reason_drift", 0
        ),
        "missing_eval_coverage": not coverage_complete,
        "build_lock_not_version_controlled": integrity["build_manifest_pin"].get(
            "trust_anchor_status"
        )
        == "pending_git_commit",
    }
    report["round2_schema_version"] = ROUND2_SCHEMA_VERSION
    report["round2"] = {
        "integrity": integrity,
        "registry": {
            "path": str(registry_path),
            "sha256": _sha256(registry_path),
            "record_count": len(registry),
        },
        "asset_root": str(asset_root.resolve()),
        "materialization_scope": "static/preflight",
        "render_status": "not_run",
        "materialization_finding_counts": dict(sorted(materialization_counts.items())),
        "quarantine_audit": quarantine_summary,
        "gate": {
            "status": "blocked" if any(gate_blockers.values()) else "pass",
            "blockers": gate_blockers,
            "external_baselines_started": False,
        },
    }
    return report, quarantine_rows, integrity


def _round2_markdown(report: dict[str, Any]) -> str:
    round2 = report["round2"]
    integrity = round2["integrity"]
    quarantine = round2["quarantine_audit"]
    finding_counts = round2["materialization_finding_counts"]
    lines = [
        "",
        "## Round2 materialization gate",
        "",
        f"- Gate: **{round2['gate']['status']}**",
        f"- Locked partition: **{integrity['candidate_count']} candidate / {integrity['quarantine_count']} quarantine**",
        f"- Source/QC join: **{integrity['source_qc_join']}**",
        f"- Materialization scope: **{round2['materialization_scope']}**",
        f"- Render status: **{round2['render_status']}**",
        f"- Build pin trust anchor: **{integrity['build_manifest_pin']['trust_anchor_status']}**",
        f"- Quarantine reason audit: `{json.dumps(quarantine['status_counts'], sort_keys=True)}`",
        "- External baselines started: **no**",
        "",
        "| materialization finding | tasks |",
        "|---|---:|",
    ]
    for code, count in finding_counts.items():
        lines.append(f"| `{code}` | {count} |")
    lines.extend(
        [
            "",
            "### Promotion blockers",
            "",
            f"`{json.dumps(round2['gate']['blockers'], sort_keys=True)}`",
            "",
        ]
    )
    return "\n".join(lines)


def write_round2_outputs(
    report: dict[str, Any],
    quarantine_rows: list[dict[str, Any]],
    integrity: dict[str, Any],
    out_dir: Path,
) -> bool:
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{out_dir.name}.staging-", dir=out_dir.parent)
    )
    try:
        write_report(report, staging)
        (staging / "report.md").write_text(
            render_markdown(report) + _round2_markdown(report), encoding="utf-8"
        )
        with (staging / "quarantine_audit.jsonl").open("w", encoding="utf-8") as handle:
            for row in quarantine_rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        (staging / "integrity.json").write_text(
            json.dumps(integrity, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return qa_qc_repair._publish(staging, out_dir)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--source-qc-dir", type=Path, default=DEFAULT_SOURCE_QC_DIR)
    parser.add_argument("--source-lock", type=Path, default=DEFAULT_SOURCE_LOCK)
    parser.add_argument("--build-lock", type=Path, default=DEFAULT_BUILD_LOCK)
    parser.add_argument("--expected-build-sha256")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--asset-root", type=Path, default=ROOT)
    parser.add_argument("--jobs-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--expected-baselines",
        nargs="*",
        default=list(qa_qc.DEFAULT_EVAL_BASELINES),
    )
    parser.add_argument("--suite-name", default="scale-v2 round2 materialization QC")
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _validate_output_dir(
            args.out_dir,
            [
                args.candidate_dir,
                args.source_dir,
                args.source_qc_dir,
                DEFAULT_PRIOR_QC_DIR,
            ],
        )
        report, quarantine_rows, integrity = build_round2_report(
            candidate_dir=args.candidate_dir,
            source_dir=args.source_dir,
            source_qc_dir=args.source_qc_dir,
            source_lock_path=args.source_lock,
            build_lock_path=args.build_lock,
            expected_build_sha256=args.expected_build_sha256,
            registry_path=args.registry,
            schema_path=args.schema,
            asset_root=args.asset_root,
            expected_baselines=_validate_canonical_baselines(
                qa_qc._parse_baselines(args.expected_baselines)
            ),
            jobs_roots=args.jobs_root,
            suite_name=args.suite_name,
        )
        published = write_round2_outputs(
            report, quarantine_rows, integrity, args.out_dir
        )
    except (OSError, Round2Error, qa_qc_repair.RepairError) as exc:
        print(f"round2 QC failed closed: {exc}", file=sys.stderr)
        return 2
    summary = {
        "out_dir": str(args.out_dir),
        "output_status": "published" if published else "unchanged",
        "task_count": report["summary"]["task_count"],
        "decisions": report["summary"]["decisions"],
        "grades": report["summary"]["grades"],
        "materialization_findings": report["round2"]["materialization_finding_counts"],
        "quarantine_audit": report["round2"]["quarantine_audit"],
        "gate": report["round2"]["gate"],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return int(report["round2"]["gate"]["status"] == "blocked")


if __name__ == "__main__":
    raise SystemExit(main())
