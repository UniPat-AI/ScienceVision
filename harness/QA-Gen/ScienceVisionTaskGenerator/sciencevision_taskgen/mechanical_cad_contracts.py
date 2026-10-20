"""Contract registry and deterministic validation for Mechanical CAD engineering QA."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft7Validator

from sciencevision_taskgen.utils import REPO_ROOT, TASKGEN_ROOT, stable_slug


CONTRACT_ID = "mechanical_cad_engineering_v1"
TASK_ID_PREFIX = "sv-industrial-mechcad-eng-"
GENERATOR_ID = "MechanicalCADEngineeringBuilderStage"
SCORER_ID = "mechanical_structured_v1"
ASSET_CONTRACT_ID = "mechanical_input_closure_v1"
REGISTRY_PATH = TASKGEN_ROOT / "schemas/mechanical_cad_engineering_v1.registry.json"
CASE_SCHEMA_PATH = TASKGEN_ROOT / "schemas/mechanical_cad_case.schema.json"
MAX_TRUSTED_ASSET_BYTES = 8 * 1024**3
MAX_TRUSTED_ARTIFACT_BYTES = 64 * 1024**2
MAX_CASE_MANIFEST_BYTES = 64 * 1024**2
MAX_STRUCTURED_INPUT_BYTES = 16 * 1024**2

ROLE_FORMATS: dict[str, frozenset[str]] = {
    "assembly_brep": frozenset({"brep", "iges", "igs", "step", "stp"}),
    "assembly_native": frozenset({"sldasm"}),
    "bom_semantic": frozenset({"csv", "json", "xlsx"}),
    "calibrated_pointcloud": frozenset({"e57", "las", "laz", "pcd", "ply"}),
    "drawing_semantic": frozenset({"dwg", "dxf", "slddrw"}),
    "drive_schedule": frozenset({"csv", "json"}),
    "fixture_brep": frozenset({"brep", "iges", "igs", "step", "stp"}),
    "joint_model": frozenset({"json", "sdf", "urdf", "xml"}),
    "manufacturing_spec": frozenset({"json", "yaml", "yml"}),
    "measurement_data": frozenset({"csv", "json", "qif", "xml"}),
    "model_brep": frozenset({"brep", "iges", "igs", "step", "stp"}),
    "mold_kinematics": frozenset({"json", "sdf", "urdf", "xml"}),
    "nominal_assembly_brep": frozenset({"brep", "iges", "igs", "step", "stp"}),
    "part_brep": frozenset({"brep", "iges", "igs", "step", "stp"}),
    "registration_spec": frozenset({"json", "yaml", "yml"}),
    "semantic_pmi": frozenset({"qif", "step", "stp", "xml"}),
    "stock_brep": frozenset({"brep", "iges", "igs", "step", "stp"}),
    "tool_assembly": frozenset({"brep", "iges", "igs", "sldasm", "step", "stp"}),
    "toolpath": frozenset({"apt", "cl", "gcode", "nc"}),
}
GEOMETRY_ROLES = frozenset({
    "assembly_brep",
    "assembly_native",
    "calibrated_pointcloud",
    "fixture_brep",
    "model_brep",
    "nominal_assembly_brep",
    "part_brep",
    "stock_brep",
    "tool_assembly",
})
ANSWER_BEARING_KEYS = frozenset({
    "answer",
    "expected_answer",
    "ground_truth",
    "ground_truth_answer",
    "reference_answer",
    "solution",
})
UNSUPPORTED_RELEASE_VIEWERS = frozenset({"fixture", "none", "text"})


class MechanicalTrustError(ValueError):
    """Trusted manifest or case-file validation failed closed."""


def load_contract_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def load_case_schema() -> dict[str, Any]:
    return json.loads(CASE_SCHEMA_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_file_read(
    path: Path,
    *,
    max_bytes: int,
    collect: bool,
    collect_limit: int | None = None,
) -> tuple[str, int, bytes | None]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MechanicalTrustError(f"cannot open trusted file {path}: {exc}") from exc
    chunks: list[bytes] | None = [] if collect else None
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MechanicalTrustError(f"trusted path is not a regular file: {path}")
        if before.st_size > max_bytes:
            raise MechanicalTrustError(
                f"trusted file exceeds size limit ({before.st_size}>{max_bytes}): {path}"
            )
        total = 0
        while True:
            chunk = os.read(descriptor, min(8 * 1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise MechanicalTrustError(f"trusted file exceeds size limit while reading: {path}")
            digest.update(chunk)
            if chunks is not None:
                collected = sum(len(item) for item in chunks)
                remaining = len(chunk) if collect_limit is None else max(0, collect_limit - collected)
                if remaining:
                    chunks.append(chunk[:remaining])
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    snapshot_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    snapshot_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if snapshot_before != snapshot_after or total != after.st_size:
        raise MechanicalTrustError(f"trusted file changed while hashing: {path}")
    try:
        path_after = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise MechanicalTrustError(f"trusted file disappeared after hashing: {path}") from exc
    snapshot_path = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    )
    if snapshot_path != snapshot_after or not stat.S_ISREG(path_after.st_mode):
        raise MechanicalTrustError(f"trusted file path changed while hashing: {path}")
    return digest.hexdigest(), total, b"".join(chunks) if chunks is not None else None


def trusted_relative_path(raw_path: Any) -> PurePosixPath:
    if not isinstance(raw_path, str) or not raw_path or raw_path != raw_path.strip():
        raise MechanicalTrustError("trusted case path must be a non-empty relative POSIX string")
    if "\\" in raw_path:
        raise MechanicalTrustError(f"trusted case path is not POSIX: {raw_path!r}")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise MechanicalTrustError(f"trusted case path escapes manifest root: {raw_path!r}")
    if str(relative) != raw_path or raw_path in {".", ""}:
        raise MechanicalTrustError(f"trusted case path is not canonical: {raw_path!r}")
    return relative


def inspect_trusted_case_file(
    raw_path: Any,
    manifest_path: Path,
    *,
    max_bytes: int = MAX_TRUSTED_ASSET_BYTES,
    collect: bool = False,
    collect_limit: int | None = None,
) -> tuple[Path, str, int, bytes | None]:
    relative = trusted_relative_path(raw_path)
    root = manifest_path.resolve().parent
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            path_stat = current.lstat()
        except OSError as exc:
            raise MechanicalTrustError(f"trusted case file is missing: {raw_path!r}") from exc
        if stat.S_ISLNK(path_stat.st_mode):
            raise MechanicalTrustError(f"trusted case path contains a symlink: {raw_path!r}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MechanicalTrustError(f"trusted case file is missing: {raw_path!r}") from exc
    if not resolved.is_relative_to(root):
        raise MechanicalTrustError(f"trusted case path escapes manifest root: {raw_path!r}")
    digest, size, payload = _stable_file_read(
        resolved,
        max_bytes=max_bytes,
        collect=collect,
        collect_limit=collect_limit,
    )
    if candidate.resolve(strict=True) != resolved:
        raise MechanicalTrustError(f"trusted case path changed while hashing: {raw_path!r}")
    return resolved, digest, size, payload


def load_trusted_case_manifest(
    manifest_path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    if not isinstance(expected_sha256, str) or not re_full_sha256(expected_sha256):
        raise MechanicalTrustError("mechanical case manifest SHA256 must be 64 lowercase hex characters")
    resolved = manifest_path.resolve()
    actual_sha256, _, payload = _stable_file_read(
        resolved, max_bytes=MAX_CASE_MANIFEST_BYTES, collect=True
    )
    if actual_sha256 != expected_sha256:
        raise MechanicalTrustError(
            f"mechanical case manifest SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    cases: dict[str, dict[str, Any]] = {}
    assert payload is not None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MechanicalTrustError("mechanical case manifest is not UTF-8") from exc
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MechanicalTrustError(
                f"mechanical case manifest line {line_number} is invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(value, dict) or not isinstance(value.get("case_id"), str):
            raise MechanicalTrustError(
                f"mechanical case manifest line {line_number} lacks a string case_id"
            )
        case_id = value["case_id"]
        if case_id in cases:
            raise MechanicalTrustError(f"duplicate trusted mechanical case_id: {case_id!r}")
        cases[case_id] = value
    return {
        "manifest_path": resolved,
        "manifest_sha256": actual_sha256,
        "cases": cases,
    }


def re_full_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def engineering_family_signals(task: dict[str, Any]) -> list[str]:
    """Return independent signals that identify the engineering task family."""
    scorer = task.get("scorer") if isinstance(task.get("scorer"), dict) else {}
    provenance = task.get("provenance") if isinstance(task.get("provenance"), dict) else {}
    answer_schema = task.get("answer_schema") if isinstance(task.get("answer_schema"), dict) else {}
    signals: list[str] = []
    if task.get("mechanical_contract") == CONTRACT_ID:
        signals.append("mechanical_contract")
    if str(task.get("task_id", "")).startswith(TASK_ID_PREFIX):
        signals.append("task_id")
    if task.get("asset_contract") == ASSET_CONTRACT_ID:
        signals.append("asset_contract")
    if scorer.get("primary") == SCORER_ID:
        signals.append("scorer")
    if provenance.get("generator") == GENERATOR_ID:
        signals.append("provenance.generator")
    if task.get("task_type") in load_contract_registry().get("task_types", {}):
        signals.append("task_type")
    if answer_schema.get("kind") == "mechanical_structured":
        signals.append("answer_schema")
    receipt = task.get("recompute_receipt") if isinstance(task.get("recompute_receipt"), dict) else {}
    if receipt.get("mechanical_contract") == CONTRACT_ID:
        signals.append("recompute_receipt")
    ground_truth = task.get("ground_truth") if isinstance(task.get("ground_truth"), dict) else {}
    if ground_truth.get("type") == "deterministic_mechanical_recompute":
        signals.append("ground_truth.type")
    if provenance.get("case_id") not in (None, ""):
        signals.append("provenance.case_id")
    if provenance.get("case_sha256") not in (None, ""):
        signals.append("provenance.case_sha256")
    return signals


def task_binding_payload(task: dict[str, Any]) -> dict[str, Any]:
    """Stable task fields covered by the recompute receipt binding."""
    provenance = task.get("provenance") if isinstance(task.get("provenance"), dict) else {}
    ground_truth = task.get("ground_truth") if isinstance(task.get("ground_truth"), dict) else {}
    artifact = ground_truth.get("artifact") if isinstance(ground_truth.get("artifact"), dict) else {}
    return {
        "mechanical_contract": task.get("mechanical_contract"),
        "task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "v_class": task.get("v_class"),
        "title": task.get("title"),
        "instruction": task.get("instruction"),
        "asset_contract": task.get("asset_contract"),
        "asset": task.get("asset"),
        "assets": task.get("assets"),
        "input_closure": task.get("input_closure"),
        "answer_schema": task.get("answer_schema"),
        "ground_truth": {
            "status": ground_truth.get("status"),
            "type": ground_truth.get("type"),
            "value": ground_truth.get("value"),
            "artifact": {"path": artifact.get("path"), "sha256": artifact.get("sha256")},
        },
        "required_evidence": task.get("required_evidence"),
        "viewer_requirements": task.get("viewer_requirements"),
        "challenge_controls": task.get("challenge_controls"),
        "active_vision_evidence": task.get("active_vision_evidence"),
        "tool_policy": task.get("tool_policy"),
        "quality_gates": task.get("quality_gates"),
        "scorer": task.get("scorer"),
        "provenance": {
            "generator": provenance.get("generator"),
            "case_id": provenance.get("case_id"),
            "case_manifest_sha256": provenance.get("case_manifest_sha256"),
            "case_sha256": provenance.get("case_sha256"),
            "solver": provenance.get("solver"),
            "input_sha256": provenance.get("input_sha256"),
        },
    }


def receipt_binding_sha256(receipt: dict[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return canonical_sha256(payload)


def _reason(code: str, detail: str, *, evidence: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "detail": detail}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def _canonical_format(value: Any) -> str:
    return str(value or "").strip().casefold().lstrip(".")


def _format_extensions(asset_format: str) -> frozenset[str]:
    aliases = {
        "gcode": frozenset({"gcode", "nc"}),
        "iges": frozenset({"iges", "igs"}),
        "step": frozenset({"step", "stp"}),
        "xml": frozenset({"qif", "xml"}),
    }
    return aliases.get(asset_format, frozenset({asset_format}))


STEP_SHAPE_ENTITIES = frozenset({
    "ADVANCED_BREP_SHAPE_REPRESENTATION",
    "ADVANCED_FACE",
    "BREP_WITH_VOIDS",
    "GEOMETRIC_CURVE_SET",
    "MANIFOLD_SOLID_BREP",
    "SHAPE_REPRESENTATION",
    "SHELL_BASED_SURFACE_MODEL",
    "TESSELLATED_SHAPE_REPRESENTATION",
    "TRIANGULATED_FACE",
})


def _looks_like_step(payload: bytes, *, complete: bool) -> bool:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return False
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL).strip()
    upper = text.upper()
    if not upper.startswith("ISO-10303-21;"):
        return False
    header = upper.find("HEADER;")
    header_end = upper.find("ENDSEC;", header + 7)
    data = upper.find("DATA;", header_end + 7)
    data_end = upper.find("ENDSEC;", data + 5)
    if not (0 <= header < header_end < data):
        return False
    if complete and not (
        data < data_end
        and upper.rstrip().endswith("END-ISO-10303-21;")
    ):
        return False
    entity_region = upper[data + 5:data_end if data_end >= 0 else len(upper)]
    syntax_only = re.sub(r"'(?:''|[^'])*'", "''", entity_region)
    if any(character in syntax_only for character in "{}[]"):
        return False
    entities = re.findall(r"#(\d+)\s*=\s*([A-Z][A-Z0-9_]*)\s*\(", entity_region)
    if not entities or not any(kind in STEP_SHAPE_ENTITIES for _, kind in entities):
        return False
    defined = {identifier for identifier, _ in entities}
    referenced = set(re.findall(r"#(\d+)", entity_region))
    return referenced <= defined


def _looks_like_iges(payload: bytes, *, complete: bool) -> bool:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return False
    lines = text.splitlines()
    if len(lines) < 4:
        return False
    sections = [line[72:73] for line in lines if len(line) >= 80]
    required = {"S", "G", "D", "P"}
    return required <= set(sections) and (not complete or sections[-1] == "T")


def _looks_like_brep(payload: bytes, *, complete: bool) -> bool:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError:
        return False
    upper = text.upper()
    return (
        upper.lstrip().startswith("DBREP_DRAWABLESHAPE")
        and "TSHAPES" in upper
        and (not complete or "CURVES" in upper)
    )


def _looks_like_cfb(payload: bytes, *, total_size: int) -> bool:
    if len(payload) < 512 or total_size < 512:
        return False
    if not payload.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        return False
    byte_order, sector_shift, mini_sector_shift = struct.unpack_from("<HHH", payload, 28)
    return (
        byte_order == 0xFFFE
        and sector_shift in {9, 12}
        and mini_sector_shift == 6
        and payload[34:40] == b"\x00" * 6
    )


def _looks_like_ply(payload: bytes, *, complete: bool) -> bool:
    lower = payload.lower()
    end = lower.find(b"end_header")
    if not lower.lstrip().startswith(b"ply\n") or end < 0:
        return False
    header = lower[:end].decode("ascii", errors="strict")
    match = re.search(r"(?m)^element\s+(?:vertex|point)\s+(\d+)\s*$", header)
    if match is None or int(match.group(1)) <= 0:
        return False
    if complete and "format ascii" in header:
        body = payload[end + len(b"end_header"):].strip().splitlines()
        return len(body) >= int(match.group(1))
    return True


def _looks_like_pcd(payload: bytes, *, complete: bool) -> bool:
    upper = payload.upper()
    header_end = upper.find(b"DATA")
    if header_end < 0 or b"FIELDS" not in upper[:header_end]:
        return False
    match = re.search(rb"(?mi)^(?:POINTS|WIDTH)\s+(\d+)\s*$", payload[:header_end])
    if match is None or int(match.group(1)) <= 0:
        return False
    return not complete or bool(payload[header_end:].splitlines()[1:])


def _looks_like_geometry(
    asset_format: str,
    payload: bytes,
    *,
    total_size: int,
) -> bool:
    probe = payload[:MAX_STRUCTURED_INPUT_BYTES]
    complete = total_size <= len(payload)
    leak_probe = probe.decode("utf-8", errors="ignore")
    if re.search(
        r"(?i)(?:answer|expected_answer|ground_truth|reference_answer|solution)\s*[\"']?\s*[:=]",
        leak_probe,
    ):
        return False
    if asset_format in {"step", "stp"}:
        return _looks_like_step(probe, complete=complete)
    if asset_format in {"iges", "igs"}:
        return _looks_like_iges(probe, complete=complete)
    if asset_format == "brep":
        return _looks_like_brep(probe, complete=complete)
    if asset_format == "sldasm":
        return _looks_like_cfb(probe, total_size=total_size)
    if asset_format == "ply":
        return _looks_like_ply(probe, complete=complete)
    if asset_format == "pcd":
        return _looks_like_pcd(probe, complete=complete)
    if asset_format == "e57":
        return probe.startswith(b"ASTM-E57")
    if asset_format in {"las", "laz"}:
        return probe.startswith(b"LASF")
    return False


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {
            str(key).casefold()
            for key in value
        } | set().union(*(_nested_keys(child) for child in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_nested_keys(child) for child in value), set())
    return set()


def _normalize_leak_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()


def _answer_value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return _normalize_leak_text(value)
    if isinstance(value, list) and all(
        isinstance(item, (str, int, float, bool)) for item in value
    ):
        return " ".join(_answer_value_text(item) for item in value)
    return ""


def _answer_leak_reasons(case: dict[str, Any]) -> list[dict[str, Any]]:
    ground_truth = case.get("ground_truth") if isinstance(case.get("ground_truth"), dict) else {}
    answer = ground_truth.get("answer") if isinstance(ground_truth.get("answer"), dict) else {}
    sources: list[tuple[str, str, bool]] = [
        ("title", str(case.get("title") or ""), False),
        ("instruction", str(case.get("instruction") or ""), False),
    ]
    closure = case.get("input_closure") if isinstance(case.get("input_closure"), dict) else {}
    for asset in closure.get("assets", []):
        if not isinstance(asset, dict):
            continue
        for key in ("asset_id", "name", "path"):
            if asset.get(key):
                sources.append((f"asset.{key}", Path(str(asset[key])).name, True))

    leaked: list[dict[str, str]] = []
    for field, value in answer.items():
        field_text = _normalize_leak_text(field)
        value_text = _answer_value_text(value)
        if not field_text or not value_text:
            continue
        assignment = f"{field_text} {value_text}"
        for source, raw_text, filename_like in sources:
            normalized = _normalize_leak_text(raw_text)
            categorical_filename_leak = (
                filename_like
                and isinstance(value, (bool, str))
                and value_text in normalized.split()
            )
            if assignment in normalized or categorical_filename_leak:
                leaked.append({
                    "source": source,
                    "field": str(field),
                    "value": str(value),
                })
    return [
        _reason(
            "answer_leakage_detected",
            "Case title, instruction, or asset identity exposes a ground-truth value",
            evidence=leaked,
        )
    ] if leaked else []


def build_solver_evidence(case: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical structured evidence envelope required from a solver."""
    closure = case.get("input_closure") if isinstance(case.get("input_closure"), dict) else {}
    evidence = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
    asset_rows = [row for row in closure.get("assets", []) if isinstance(row, dict)]
    component_ids = list(closure.get("component_ids") or [])
    required = list(evidence.get("required") or [])
    return {
        "required": required,
        "observations": [
            {
                "type": evidence_type,
                "component_ids": component_ids,
                "relation_fields": list(answer),
            }
            for evidence_type in required
        ],
        "operation_trace": [{
            "operation": "deterministic_geometry_recompute",
            "input_asset_ids": [row.get("asset_id") for row in asset_rows],
        }],
        "units": closure.get("units"),
        "numerical_tolerance": closure.get("numerical_tolerance"),
        "input_sha256": {
            str(row.get("asset_id")): str(row.get("sha256", "")).lower()
            for row in asset_rows
        },
        "answer_sha256": canonical_sha256(
            normalize_answer(str(case.get("task_type")), answer, closure)
        ),
    }


def _solver_evidence_reasons(
    *,
    task_type: Any,
    answer: dict[str, Any],
    closure: dict[str, Any],
    required_evidence: list[Any],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = document.get("solver_evidence")
    if not isinstance(evidence, dict):
        return [_reason(
            "solver_evidence_missing",
            "Ground-truth artifact must include a structured solver_evidence envelope",
        )]
    reasons: list[dict[str, Any]] = []
    required = [str(value) for value in required_evidence]
    if set(evidence.get("required") or []) != set(required):
        reasons.append(_reason(
            "solver_evidence_required_mismatch",
            "Solver evidence does not bind every required evidence type",
        ))
    component_ids = set(closure.get("component_ids") or [])
    answer_fields = set(answer)
    observations = evidence.get("observations")
    observed_types: set[str] = set()
    covered_relations: set[str] = set()
    if not isinstance(observations, list) or not observations:
        reasons.append(_reason(
            "solver_evidence_observations_missing",
            "Solver evidence must include structured geometric observations",
        ))
    else:
        for observation in observations:
            if not isinstance(observation, dict) or not isinstance(observation.get("type"), str):
                reasons.append(_reason(
                    "solver_evidence_observation_invalid",
                    "Every solver observation must declare an evidence type",
                ))
                continue
            observed_types.add(observation["type"])
            observed_components = observation.get("component_ids")
            if (
                not isinstance(observed_components, list)
                or not observed_components
                or not set(observed_components) <= component_ids
            ):
                reasons.append(_reason(
                    "solver_evidence_component_binding_invalid",
                    "Solver observations must bind non-empty trusted component IDs",
                ))
            relations = observation.get("relation_fields")
            if not isinstance(relations, list) or not set(relations) <= answer_fields:
                reasons.append(_reason(
                    "solver_evidence_relation_binding_invalid",
                    "Solver observations must bind declared answer relations",
                ))
            else:
                covered_relations.update(relations)
    if set(required) - observed_types:
        reasons.append(_reason(
            "solver_evidence_type_missing",
            "Structured observations do not cover every required evidence type",
            evidence=sorted(set(required) - observed_types),
        ))
    if answer_fields - covered_relations:
        reasons.append(_reason(
            "solver_evidence_relation_missing",
            "Structured observations do not cover every answer relation",
            evidence=sorted(answer_fields - covered_relations),
        ))

    trace = evidence.get("operation_trace")
    asset_ids = {
        str(row.get("asset_id"))
        for row in closure.get("assets", [])
        if isinstance(row, dict)
    }
    traced_assets: set[str] = set()
    if not isinstance(trace, list) or not trace:
        reasons.append(_reason(
            "solver_operation_trace_missing",
            "Solver evidence must include a non-empty deterministic operation trace",
        ))
    else:
        for step in trace:
            if not isinstance(step, dict) or not isinstance(step.get("operation"), str):
                reasons.append(_reason(
                    "solver_operation_trace_invalid",
                    "Each solver trace step must name an operation",
                ))
                continue
            inputs = step.get("input_asset_ids")
            if not isinstance(inputs, list) or not set(inputs) <= asset_ids:
                reasons.append(_reason(
                    "solver_operation_input_invalid",
                    "Solver trace inputs must reference trusted asset IDs",
                ))
            else:
                traced_assets.update(inputs)
    if traced_assets != asset_ids:
        reasons.append(_reason(
            "solver_operation_input_incomplete",
            "Solver operation trace must cover every trusted input asset",
            evidence=sorted(asset_ids - traced_assets),
        ))

    if evidence.get("units") != closure.get("units"):
        reasons.append(_reason("solver_evidence_units_mismatch", "Solver evidence units differ from input closure"))
    if evidence.get("numerical_tolerance") != closure.get("numerical_tolerance"):
        reasons.append(_reason(
            "solver_evidence_tolerance_mismatch",
            "Solver evidence tolerances differ from input closure",
        ))
    expected_inputs = {
        str(row.get("asset_id")): str(row.get("sha256", "")).lower()
        for row in closure.get("assets", [])
        if isinstance(row, dict)
    }
    if evidence.get("input_sha256") != expected_inputs:
        reasons.append(_reason(
            "solver_evidence_input_digest_mismatch",
            "Solver evidence is not bound to every manifest input digest",
        ))
    try:
        expected_answer_sha = canonical_sha256(normalize_answer(str(task_type), answer, closure))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        expected_answer_sha = None
    if evidence.get("answer_sha256") != expected_answer_sha:
        reasons.append(_reason(
            "solver_evidence_answer_digest_mismatch",
            "Solver evidence is not bound to the normalized answer",
        ))
    return reasons


def _asset_contract_reasons(
    asset: dict[str, Any],
    *,
    size: int,
    payload: bytes | None,
) -> list[dict[str, Any]]:
    role = str(asset.get("role") or "")
    asset_format = _canonical_format(asset.get("format"))
    allowed = ROLE_FORMATS.get(role)
    reasons: list[dict[str, Any]] = []
    if allowed is None:
        return [_reason(
            "input_asset_role_unsupported",
            f"No release format contract is registered for asset role {role!r}",
        )]
    if asset_format not in allowed:
        reasons.append(_reason(
            "input_asset_format_unsupported",
            f"Role {role!r} does not accept format {asset_format!r}",
            evidence={"allowed": sorted(allowed)},
        ))
    suffix = Path(str(asset.get("path") or "")).suffix.casefold().lstrip(".")
    if asset_format and suffix not in _format_extensions(asset_format):
        reasons.append(_reason(
            "input_asset_extension_mismatch",
            f"Asset path suffix {suffix!r} does not match declared format {asset_format!r}",
        ))
    viewer = str(asset.get("viewer") or "").strip().casefold()
    if viewer in UNSUPPORTED_RELEASE_VIEWERS:
        reasons.append(_reason(
            "input_asset_viewer_unsupported",
            f"Release assets cannot use viewer {viewer!r}",
        ))
    try:
        geometry_valid = (
            payload is not None
            and _looks_like_geometry(asset_format, payload, total_size=size)
        )
    except (ArithmeticError, UnicodeError, ValueError, struct.error):
        geometry_valid = False
    if role in GEOMETRY_ROLES and not geometry_valid:
        reasons.append(_reason(
            "geometry_asset_content_invalid",
            f"Role {role!r} is not backed by recognizable {asset_format!r} geometry bytes",
        ))
    if asset_format == "json":
        if size > MAX_STRUCTURED_INPUT_BYTES:
            reasons.append(_reason(
                "structured_input_too_large",
                "Structured JSON input is too large for answer-leakage inspection",
            ))
        elif payload is not None:
            try:
                document = json.loads(payload)
            except (UnicodeError, json.JSONDecodeError) as exc:
                reasons.append(_reason(
                    "structured_input_invalid",
                    f"Structured JSON input is invalid: {exc}",
                ))
            else:
                leaked_keys = sorted(_nested_keys(document) & ANSWER_BEARING_KEYS)
                if leaked_keys:
                    reasons.append(_reason(
                        "answer_bearing_input",
                        "Input JSON contains reserved answer-bearing keys",
                        evidence=leaked_keys,
                    ))
    return reasons


def _resolve_case_path(raw_path: Any, manifest_path: Path | None) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    if manifest_path is not None:
        local = manifest_path.parent / candidate
        if local.exists():
            return local.resolve()
    return (REPO_ROOT / candidate).resolve()


def _schema_reasons(case: dict[str, Any]) -> list[dict[str, Any]]:
    validator = Draft7Validator(load_case_schema())
    reasons: list[dict[str, Any]] = []
    for error in sorted(validator.iter_errors(case), key=lambda row: list(row.path)):
        location = ".".join(str(part) for part in error.path) or "<root>"
        reasons.append(_reason("case_schema_invalid", f"{location}: {error.message}"))
    return reasons


def _validate_task_specific_closure(
    task_type: Any,
    closure: dict[str, Any],
    component_ids: set[str],
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    if task_type == "assembly_bom_relation":
        internal = closure.get("internal_component_ids")
        hidden = closure.get("primary_view_hidden_component_ids")
        views = closure.get("inspection_view_ids")
        internal_ids = set(internal) if isinstance(internal, list) else set()
        hidden_ids = set(hidden) if isinstance(hidden, list) else set()
        if not internal_ids or not internal_ids <= component_ids:
            reasons.append(_reason(
                "internal_component_ids_invalid",
                "assembly_bom_relation requires non-empty internal component IDs from component_ids",
            ))
        if not hidden_ids or not hidden_ids <= internal_ids:
            reasons.append(_reason(
                "hidden_internal_component_ids_invalid",
                "At least one primary-view-hidden component must be an internal component",
            ))
        if (
            not isinstance(views, list)
            or len(views) < 2
            or any(not isinstance(view, str) or not view for view in views)
            or len(views) != len(set(views))
        ):
            reasons.append(_reason(
                "inspection_views_insufficient",
                "At least two unique inspection views are required for hidden-part verification",
            ))
        if closure.get("requires_section_or_xray") is not True:
            reasons.append(_reason(
                "section_or_xray_required",
                "Hidden internal assembly mapping must require section or x-ray inspection",
            ))
    elif task_type == "manufacturability_draft_undercut":
        direction = closure.get("mold_opening_direction")
        if (
            not isinstance(direction, list)
            or len(direction) != 3
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in direction)
            or any(not math.isfinite(value) for value in direction if isinstance(value, (int, float)))
            or sum(float(value) ** 2 for value in direction) == 0
        ):
            reasons.append(_reason(
                "mold_opening_direction_invalid",
                "mold_opening_direction must be a finite non-zero 3D vector",
            ))
    return reasons


def _validate_challenge_controls(
    owner: dict[str, Any],
    component_ids: set[str],
    *,
    manifest_path: Path | None,
    verify_files: bool,
) -> list[dict[str, Any]]:
    requirements = load_contract_registry()["challenge_control_requirements"]
    controls = owner.get("challenge_controls")
    if not isinstance(controls, dict):
        return [_reason(
            "challenge_controls_missing",
            "Opaque labels, matched decoys, and shortcut-probe controls are required",
        )]

    reasons: list[dict[str, Any]] = []
    if controls.get("component_labels") != "opaque":
        reasons.append(_reason(
            "component_labels_not_opaque",
            "Component labels exposed to the solver must be opaque",
        ))
    withheld = controls.get("withheld_metadata")
    withheld_set = set(withheld) if isinstance(withheld, list) else set()
    missing_withheld = sorted(set(requirements["required_forbidden_sources"]) - withheld_set)
    if missing_withheld:
        reasons.append(_reason(
            "shortcut_metadata_not_withheld",
            "Answer-bearing filenames and semantic metadata must be withheld",
            evidence=missing_withheld,
        ))

    candidates = controls.get("candidate_component_ids")
    candidate_ids = set(candidates) if isinstance(candidates, list) else set()
    decoys = controls.get("matched_decoy_component_ids")
    decoy_ids = set(decoys) if isinstance(decoys, list) else set()
    if len(candidate_ids) < requirements["minimum_candidate_count"]:
        reasons.append(_reason(
            "candidate_set_too_small",
            "The candidate set is too small to resist elimination",
        ))
    unknown_candidates = sorted(candidate_ids - component_ids)
    if unknown_candidates:
        reasons.append(_reason(
            "candidate_component_unknown",
            "Candidate IDs must belong to input_closure.component_ids",
            evidence=unknown_candidates,
        ))
    if len(decoy_ids) < requirements["minimum_matched_decoys"] or not decoy_ids <= candidate_ids:
        reasons.append(_reason(
            "matched_decoys_invalid",
            "At least two matched decoys from the candidate set are required",
        ))

    operations = controls.get("required_3d_operations")
    if not isinstance(operations, list) or len(set(operations)) < 2:
        reasons.append(_reason(
            "insufficient_3d_operations",
            "The answer must require at least two distinct 3D inspection operations",
        ))

    audit = controls.get("shortcut_audit") if isinstance(controls.get("shortcut_audit"), dict) else {}
    threshold = audit.get("maximum_success_rate")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold > requirements["maximum_shortcut_success_rate"]
        or threshold < 0
    ):
        reasons.append(_reason(
            "shortcut_threshold_invalid",
            f"Shortcut success threshold must be at most {requirements['maximum_shortcut_success_rate']}",
        ))
        threshold = requirements["maximum_shortcut_success_rate"]
    rates = audit.get("probe_success_rates") if isinstance(audit.get("probe_success_rates"), dict) else {}
    missing_probes = sorted(set(requirements["required_shortcut_probes"]) - set(rates))
    if missing_probes:
        reasons.append(_reason(
            "shortcut_probe_missing",
            "All filename, metadata, bounding-box, and silhouette probes are required",
            evidence=missing_probes,
        ))
    invalid_rates = {
        name: value
        for name, value in rates.items()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or value > 1
            or value > threshold
        )
    }
    if invalid_rates:
        reasons.append(_reason(
            "shortcut_probe_failed",
            "A superficial shortcut probe exceeds the permitted success rate",
            evidence=invalid_rates,
        ))

    artifact = audit.get("artifact") if isinstance(audit.get("artifact"), dict) else {}
    if verify_files:
        try:
            if manifest_path is None:
                artifact_path = _resolve_case_path(artifact.get("path"), None)
                if artifact_path is None or not artifact_path.is_file():
                    raise MechanicalTrustError("shortcut-audit artifact is not materialized")
                digest = sha256_file(artifact_path)
                payload = artifact_path.read_bytes()
            else:
                artifact_path, digest, _, payload = inspect_trusted_case_file(
                    artifact.get("path"),
                    manifest_path,
                    max_bytes=MAX_TRUSTED_ARTIFACT_BYTES,
                    collect=True,
                )
        except (MechanicalTrustError, OSError) as exc:
            reasons.append(_reason(
                "shortcut_audit_artifact_invalid",
                f"Shortcut-audit artifact failed trusted read: {exc}",
            ))
        else:
            if digest != str(artifact.get("sha256", "")).lower():
                reasons.append(_reason(
                    "shortcut_audit_digest_mismatch",
                    "Shortcut-audit artifact SHA256 differs",
                    evidence=str(artifact_path),
                ))
            try:
                document = json.loads(payload or b"")
            except (UnicodeError, json.JSONDecodeError) as exc:
                reasons.append(_reason(
                    "shortcut_audit_artifact_invalid",
                    f"Shortcut-audit artifact is not JSON: {exc}",
                ))
            else:
                if (
                    not isinstance(document, dict)
                    or document.get("probe_success_rates") != rates
                    or document.get("candidate_component_ids") != candidates
                    or document.get("matched_decoy_component_ids") != decoys
                ):
                    reasons.append(_reason(
                        "shortcut_audit_binding_mismatch",
                        "Shortcut-audit artifact does not bind the declared candidates, decoys, and probe rates",
                    ))
    return reasons


def _validate_value(
    field: str,
    spec: dict[str, Any],
    value: Any,
    component_ids: set[str],
) -> list[dict[str, Any]]:
    value_type = spec.get("type")
    reasons: list[dict[str, Any]] = []
    if value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            reasons.append(_reason("answer_type_invalid", f"{field} must be a finite number"))
    elif value_type == "boolean":
        if not isinstance(value, bool):
            reasons.append(_reason("answer_type_invalid", f"{field} must be boolean"))
    elif value_type == "categorical":
        if not isinstance(value, str) or not value:
            reasons.append(_reason("answer_type_invalid", f"{field} must be a non-empty category"))
    elif value_type == "mapping":
        if not isinstance(value, dict):
            reasons.append(_reason("answer_type_invalid", f"{field} must be a mapping"))
        elif field in {"visibility_by_component", "deviation_by_component"}:
            unknown = sorted(set(value) - component_ids)
            if unknown:
                reasons.append(_reason("unknown_component_id", f"{field} uses unknown component IDs", evidence=unknown))
    elif value_type in {"component_id_set", "string_set"}:
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            reasons.append(_reason("answer_type_invalid", f"{field} must be a list of unique strings"))
        elif len(value) != len(set(value)):
            reasons.append(_reason("answer_set_not_unique", f"{field} contains duplicate values"))
        elif value_type == "component_id_set":
            unknown = sorted(set(value) - component_ids)
            if unknown:
                reasons.append(_reason("unknown_component_id", f"{field} uses unknown component IDs", evidence=unknown))
    elif value_type == "component_pair_set":
        if not isinstance(value, list):
            reasons.append(_reason("answer_type_invalid", f"{field} must be a list of component pairs"))
        else:
            normalized: list[tuple[str, str]] = []
            for pair in value:
                if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(item, str) for item in pair):
                    reasons.append(_reason("answer_type_invalid", f"{field} contains an invalid component pair"))
                    continue
                unknown = sorted(set(pair) - component_ids)
                if unknown:
                    reasons.append(_reason("unknown_component_id", f"{field} uses unknown component IDs", evidence=unknown))
                normalized.append(tuple(sorted(pair)))
            if len(normalized) != len(set(normalized)):
                reasons.append(_reason("answer_set_not_unique", f"{field} contains duplicate component pairs"))
    elif value_type == "interval":
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
            or any(not math.isfinite(item) for item in value if isinstance(item, (int, float)))
        ):
            reasons.append(_reason("answer_type_invalid", f"{field} must be a finite [lower, upper] interval"))
        elif value[0] > value[1]:
            reasons.append(_reason("answer_interval_invalid", f"{field} lower bound exceeds upper bound"))
    else:
        reasons.append(_reason("answer_spec_invalid", f"{field} has unsupported type {value_type!r}"))
    return reasons


def validate_case_static(
    case: dict[str, Any],
    *,
    manifest_path: Path | None = None,
    verify_files: bool = True,
) -> list[dict[str, Any]]:
    """Validate closure and precomputed GT without trusting solver declarations."""
    reasons = _schema_reasons(case)
    registry = load_contract_registry().get("task_types", {})
    task_type = case.get("task_type")
    contract = registry.get(task_type)
    if not isinstance(contract, dict):
        reasons.append(_reason("unknown_task_type", f"No engineering contract for {task_type!r}"))
        return reasons
    if case.get("mechanical_contract") != CONTRACT_ID:
        reasons.append(_reason("mechanical_contract_invalid", f"Expected {CONTRACT_ID}"))
    if case.get("release_eligible") is not True:
        reasons.append(_reason("case_marked_non_release", "Case inventory is explicitly non-release"))

    closure = case.get("input_closure") if isinstance(case.get("input_closure"), dict) else {}
    for field in contract["required_closure_fields"]:
        if closure.get(field) in (None, "", [], {}):
            reasons.append(_reason("input_closure_missing", f"Missing input_closure.{field}"))
    component_rows = closure.get("component_ids")
    component_ids = set(component_rows) if isinstance(component_rows, list) else set()
    if len(component_ids) != len(component_rows or []):
        reasons.append(_reason("component_ids_not_unique", "input_closure.component_ids must be unique"))
    reasons.extend(_validate_task_specific_closure(task_type, closure, component_ids))
    reasons.extend(_validate_challenge_controls(
        case,
        component_ids,
        manifest_path=manifest_path,
        verify_files=verify_files,
    ))
    reasons.extend(_answer_leak_reasons(case))

    assets = closure.get("assets") if isinstance(closure.get("assets"), list) else []
    roles = {row.get("role") for row in assets if isinstance(row, dict)}
    missing_roles = sorted(set(contract["required_input_roles"]) - roles)
    if missing_roles:
        reasons.append(_reason("required_input_role_missing", "Required input roles are absent", evidence=missing_roles))
    seen_asset_ids: set[str] = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("asset_id")
        if asset_id in seen_asset_ids:
            reasons.append(_reason("asset_id_duplicate", f"Duplicate asset_id {asset_id!r}"))
        if isinstance(asset_id, str):
            seen_asset_ids.add(asset_id)
        path = _resolve_case_path(asset.get("path"), manifest_path)
        if not verify_files:
            continue
        try:
            if manifest_path is None:
                if path is None or not path.is_file():
                    raise MechanicalTrustError("input asset is not materialized")
                digest, size, payload = _stable_file_read(
                    path,
                    max_bytes=MAX_TRUSTED_ASSET_BYTES,
                    collect=True,
                    collect_limit=MAX_STRUCTURED_INPUT_BYTES,
                )
            else:
                path, digest, size, payload = inspect_trusted_case_file(
                    asset.get("path"),
                    manifest_path,
                    collect=True,
                    collect_limit=MAX_STRUCTURED_INPUT_BYTES,
                )
        except MechanicalTrustError as exc:
            reasons.append(_reason(
                "input_asset_untrusted_path",
                f"Input asset {index} failed trusted path validation: {exc}",
                evidence=asset.get("path"),
            ))
        else:
            if digest != str(asset.get("sha256", "")).lower():
                reasons.append(_reason(
                    "input_asset_digest_mismatch",
                    f"Input asset {index} SHA256 differs",
                    evidence=str(path),
                ))
            reasons.extend(_asset_contract_reasons(
                asset,
                size=size,
                payload=payload,
            ))

    schema = case.get("answer_schema") if isinstance(case.get("answer_schema"), dict) else {}
    expected_specs = contract["answer_fields"]
    if schema.get("fields") != list(expected_specs):
        reasons.append(_reason("answer_fields_mismatch", "answer_schema.fields differs from registry", evidence=list(expected_specs)))
    if schema.get("field_specs") != expected_specs:
        reasons.append(_reason("answer_specs_mismatch", "answer_schema.field_specs differs from registry"))
    answer = case.get("ground_truth", {}).get("answer") if isinstance(case.get("ground_truth"), dict) else None
    if not isinstance(answer, dict):
        reasons.append(_reason("ground_truth_answer_missing", "ground_truth.answer must be structured"))
        answer = {}
    for field, spec in expected_specs.items():
        if field not in answer:
            reasons.append(_reason("ground_truth_field_missing", f"ground_truth.answer.{field} is missing"))
            continue
        reasons.extend(_validate_value(field, spec, answer[field], component_ids))
        units = closure.get("units") if isinstance(closure.get("units"), dict) else {}
        tolerances = closure.get("numerical_tolerance") if isinstance(closure.get("numerical_tolerance"), dict) else {}
        dimension = spec.get("unit_dimension")
        if dimension and not isinstance(units.get(dimension), str):
            reasons.append(_reason("answer_unit_missing", f"No input_closure.units.{dimension} for {field}"))
        if spec.get("unit") and spec["unit"] not in units.values() and spec["unit"] != "ratio":
            reasons.append(_reason("answer_unit_missing", f"Unit {spec['unit']} is not declared for {field}"))
        tolerance = tolerances.get(field)
        if spec.get("tolerance_required") and (
            isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance <= 0
        ):
            reasons.append(_reason("answer_tolerance_missing", f"Positive numerical_tolerance.{field} is required"))

    evidence = case.get("evidence") if isinstance(case.get("evidence"), dict) else {}
    missing_evidence = sorted(set(contract["required_evidence"]) - set(evidence.get("required", [])))
    missing_viewer = sorted(set(contract["viewer_requirements"]) - set(evidence.get("viewer_requirements", [])))
    if missing_evidence:
        reasons.append(_reason("gt_evidence_missing", "Required GT evidence is absent", evidence=missing_evidence))
    if missing_viewer:
        reasons.append(_reason("viewer_requirement_missing", "Required viewer evidence is absent", evidence=missing_viewer))

    solver = case.get("solver") if isinstance(case.get("solver"), dict) else {}
    for key in ("name", "version", "parameters", "recompute"):
        if solver.get(key) in (None, "", [], {}):
            reasons.append(_reason("solver_provenance_missing", f"solver.{key} is missing"))
    artifact = case.get("ground_truth", {}).get("artifact", {})
    artifact_path = _resolve_case_path(artifact.get("path"), manifest_path) if isinstance(artifact, dict) else None
    if verify_files:
        try:
            if manifest_path is None:
                if artifact_path is None or not artifact_path.is_file():
                    raise MechanicalTrustError("ground-truth artifact is not materialized")
                artifact_digest, _, artifact_payload = _stable_file_read(
                    artifact_path,
                    max_bytes=MAX_TRUSTED_ARTIFACT_BYTES,
                    collect=True,
                )
            else:
                artifact_path, artifact_digest, _, artifact_payload = inspect_trusted_case_file(
                    artifact.get("path"),
                    manifest_path,
                    max_bytes=MAX_TRUSTED_ARTIFACT_BYTES,
                    collect=True,
                )
        except MechanicalTrustError as exc:
            reasons.append(_reason(
                "gt_artifact_untrusted_path",
                f"Precomputed GT artifact failed trusted path validation: {exc}",
                evidence=artifact.get("path") if isinstance(artifact, dict) else None,
            ))
        else:
            if artifact_digest != str(artifact.get("sha256", "")).lower():
                reasons.append(_reason(
                    "gt_artifact_digest_mismatch",
                    "Precomputed GT artifact SHA256 differs",
                    evidence=str(artifact_path),
                ))
            try:
                artifact_document = json.loads(artifact_payload or b"")
            except (UnicodeError, json.JSONDecodeError) as exc:
                reasons.append(_reason(
                    "gt_artifact_invalid",
                    f"Precomputed GT artifact is not canonical JSON: {exc}",
                ))
            else:
                if (
                    not isinstance(artifact_document, dict)
                    or artifact_document.get("task_type") != task_type
                    or not isinstance(artifact_document.get("answer"), dict)
                ):
                    reasons.append(_reason(
                        "gt_artifact_invalid",
                        "Artifact must bind task_type and a structured answer",
                    ))
                else:
                    reasons.extend(_solver_evidence_reasons(
                        task_type=task_type,
                        answer=answer,
                        closure=closure,
                        required_evidence=list(evidence.get("required") or []),
                        document=artifact_document,
                    ))
    return reasons


def _canonical_generic(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_generic(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_generic(item) for item in value]
    if isinstance(value, float):
        return Decimal(str(value)).normalize().to_eng_string()
    return value


def normalize_answer(task_type: str, answer: dict[str, Any], closure: dict[str, Any]) -> dict[str, Any]:
    contract = load_contract_registry()["task_types"][task_type]
    tolerances = closure.get("numerical_tolerance", {})
    normalized: dict[str, Any] = {}
    for field, spec in contract["answer_fields"].items():
        value = answer[field]
        value_type = spec["type"]
        if value_type == "number":
            tolerance = Decimal(str(tolerances[field]))
            normalized[field] = int((Decimal(str(value)) / tolerance).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
        elif value_type == "interval":
            tolerance = Decimal(str(tolerances[field]))
            normalized[field] = [
                int((Decimal(str(item)) / tolerance).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
                for item in value
            ]
        elif value_type in {"component_id_set", "string_set"}:
            normalized[field] = sorted(value)
        elif value_type == "component_pair_set":
            normalized[field] = sorted([sorted(pair) for pair in value])
        else:
            normalized[field] = _canonical_generic(value)
    return normalized


def _task_file(path_value: Any) -> Path | None:
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else (REPO_ROOT / path.lstrip("/")).resolve()


def _materialized_task_assets(
    task: dict[str, Any],
    closure: dict[str, Any],
    *,
    trusted_case: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    reasons: list[dict[str, Any]] = []
    task_rows = task.get("assets") if isinstance(task.get("assets"), list) else []
    closure_rows = closure.get("assets") if isinstance(closure.get("assets"), list) else []

    def index(rows: list[Any], label: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("asset_id"), str):
                reasons.append(_reason("asset_binding_invalid", f"{label} contains an asset without asset_id"))
                continue
            asset_id = row["asset_id"]
            if asset_id in result:
                reasons.append(_reason("asset_binding_duplicate", f"{label} repeats asset_id {asset_id!r}"))
            result[asset_id] = row
        return result

    task_by_id = index(task_rows, "task.assets")
    closure_by_id = index(closure_rows, "input_closure.assets")
    trusted_rows = (
        trusted_case.get("input_closure", {}).get("assets", [])
        if isinstance(trusted_case, dict)
        else []
    )
    trusted_by_id = index(trusted_rows, "trusted_case.input_closure.assets") if trusted_case is not None else {}
    if set(task_by_id) != set(closure_by_id):
        reasons.append(_reason(
            "asset_binding_set_mismatch",
            "task.assets and input_closure.assets must bind the same asset IDs",
            evidence={"task": sorted(task_by_id), "closure": sorted(closure_by_id)},
        ))
    if trusted_case is not None and (
        set(task_by_id) != set(trusted_by_id) or set(closure_by_id) != set(trusted_by_id)
    ):
        reasons.append(_reason(
            "trusted_asset_set_mismatch",
            "Task and input closure must bind exactly the trusted case asset IDs",
            evidence={
                "task": sorted(task_by_id),
                "closure": sorted(closure_by_id),
                "trusted": sorted(trusted_by_id),
            },
        ))
    binding_fields = ("asset_id", "role", "path", "format", "viewer", "sha256")
    actual_digests: dict[str, str] = {}
    common_ids = set(task_by_id) & set(closure_by_id)
    if trusted_case is not None:
        common_ids &= set(trusted_by_id)
    for asset_id in sorted(common_ids):
        task_asset = task_by_id[asset_id]
        closure_asset = closure_by_id[asset_id]
        mismatched = [field for field in binding_fields if task_asset.get(field) != closure_asset.get(field)]
        if mismatched:
            reasons.append(_reason(
                "asset_binding_content_mismatch",
                f"Asset {asset_id!r} differs between task and input closure",
                evidence=mismatched,
            ))
        if trusted_case is not None:
            trusted_asset = trusted_by_id[asset_id]
            trusted_mismatched = [
                field for field in binding_fields
                if task_asset.get(field) != trusted_asset.get(field)
                or closure_asset.get(field) != trusted_asset.get(field)
            ]
            if trusted_mismatched:
                reasons.append(_reason(
                    "trusted_asset_binding_mismatch",
                    f"Asset {asset_id!r} differs from the trusted case",
                    evidence=sorted(set(trusted_mismatched)),
                ))
            if task_asset.get("repo_path") not in (None, trusted_asset.get("path")):
                reasons.append(_reason(
                    "trusted_asset_repo_path_mismatch",
                    f"Asset {asset_id!r} repo_path must equal its trusted relative path",
                ))
            try:
                if manifest_path is None:
                    raise MechanicalTrustError("trusted manifest path is unavailable")
                _, digest, _, _ = inspect_trusted_case_file(
                    trusted_asset.get("path"), manifest_path
                )
            except MechanicalTrustError as exc:
                reasons.append(_reason(
                    "trusted_input_asset_invalid",
                    f"Trusted asset {asset_id!r} failed secure read: {exc}",
                ))
                continue
            if digest != str(trusted_asset.get("sha256", "")).lower():
                reasons.append(_reason(
                    "trusted_input_asset_digest_mismatch",
                    f"Trusted asset {asset_id!r} digest differs from manifest",
                ))
        else:
            path = _task_file(task_asset.get("repo_path") or task_asset.get("path"))
            if path is None or not path.is_file():
                reasons.append(_reason("input_asset_missing", f"Task asset {asset_id!r} is not materialized"))
                continue
            digest = sha256_file(path)
        actual_digests[asset_id] = digest
        if digest != str(task_asset.get("sha256", "")).lower():
            reasons.append(_reason("input_asset_digest_mismatch", f"Task asset {asset_id!r} SHA256 differs from bytes"))
        if digest != str(closure_asset.get("sha256", "")).lower():
            reasons.append(_reason("input_closure_digest_mismatch", f"Closure asset {asset_id!r} SHA256 differs from bytes"))

    alias = task.get("asset") if isinstance(task.get("asset"), dict) else {}
    alias_id = alias.get("asset_id")
    bound_alias = task_by_id.get(alias_id)
    if bound_alias is None:
        reasons.append(_reason("asset_alias_mismatch", "task.asset does not alias one member of task.assets"))
    else:
        mismatched = [field for field in binding_fields if alias.get(field) != bound_alias.get(field)]
        if mismatched:
            reasons.append(_reason("asset_alias_mismatch", "task.asset differs from its task.assets member", evidence=mismatched))
    return actual_digests, reasons


def validate_engineering_task(
    task: dict[str, Any],
    *,
    trusted_case: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Validate the release receipt embedded in a generated engineering task."""
    signals = engineering_family_signals(task)
    if not signals and not strict:
        return []
    reasons: list[dict[str, Any]] = []
    if strict and trusted_case is None:
        return [_reason(
            "trusted_case_missing",
            "Strict MechanicalCADEngineering validation requires a trusted source case",
        )]
    authoritative = trusted_case if isinstance(trusted_case, dict) else None
    authoritative_task_type = authoritative.get("task_type") if authoritative else task.get("task_type")
    authoritative_case_id = authoritative.get("case_id") if authoritative else None
    authoritative_case_sha = canonical_sha256(authoritative) if authoritative else None
    if authoritative is not None:
        if manifest_path is None:
            reasons.append(_reason(
                "trusted_case_static_invalid",
                "Trusted source case cannot be validated without its anchored manifest path",
            ))
        else:
            for static_reason in validate_case_static(
                authoritative,
                manifest_path=manifest_path,
                verify_files=True,
            ):
                reasons.append(_reason(
                    "trusted_case_static_invalid",
                    f"Trusted source case failed {static_reason['code']}: {static_reason['detail']}",
                    evidence={
                        "source_code": static_reason["code"],
                        "source_evidence": static_reason.get("evidence"),
                    },
                ))
    if task.get("mechanical_contract") != CONTRACT_ID:
        reasons.append(_reason(
            "mechanical_contract_missing_or_invalid",
            f"Engineering family signals require mechanical_contract={CONTRACT_ID}",
            evidence=signals,
        ))
    if task.get("v_class") != "V4":
        reasons.append(_reason("engineering_v_class_invalid", "Mechanical CAD engineering tasks must use v_class V4"))
    if not str(task.get("task_id", "")).startswith(TASK_ID_PREFIX):
        reasons.append(_reason("engineering_task_id_invalid", f"Engineering task_id must start with {TASK_ID_PREFIX}"))
    if authoritative_case_id is not None:
        expected_task_id = f"{TASK_ID_PREFIX}{stable_slug(authoritative_case_id)}"
        if task.get("task_id") != expected_task_id:
            reasons.append(_reason(
                "trusted_task_id_mismatch",
                f"task_id differs from trusted case {authoritative_case_id!r}",
            ))
    if task.get("asset_contract") != ASSET_CONTRACT_ID:
        reasons.append(_reason("engineering_asset_contract_invalid", f"Expected asset_contract={ASSET_CONTRACT_ID}"))
    scorer = task.get("scorer") if isinstance(task.get("scorer"), dict) else {}
    if scorer.get("primary") != SCORER_ID:
        reasons.append(_reason("engineering_scorer_invalid", f"Expected scorer.primary={SCORER_ID}"))
    provenance = task.get("provenance") if isinstance(task.get("provenance"), dict) else {}
    if provenance.get("generator") != GENERATOR_ID:
        reasons.append(_reason("engineering_generator_invalid", f"Expected provenance.generator={GENERATOR_ID}"))
    registry = load_contract_registry().get("task_types", {})
    task_type = task.get("task_type")
    contract = registry.get(authoritative_task_type)
    if not contract:
        reasons.append(_reason("unknown_task_type", f"No engineering contract for {authoritative_task_type!r}"))
        return reasons
    if authoritative is not None and task_type != authoritative_task_type:
        reasons.append(_reason(
            "trusted_task_type_mismatch",
            "Task type differs from trusted source case",
        ))
    closure = task.get("input_closure") if isinstance(task.get("input_closure"), dict) else {}
    if authoritative is not None and closure != authoritative.get("input_closure"):
        reasons.append(_reason(
            "trusted_input_closure_mismatch",
            "input_closure differs from trusted source case",
        ))
    challenge_controls = (
        task.get("challenge_controls")
        if isinstance(task.get("challenge_controls"), dict)
        else {}
    )
    if authoritative is not None and challenge_controls != authoritative.get("challenge_controls"):
        reasons.append(_reason(
            "trusted_challenge_controls_mismatch",
            "challenge_controls differs from trusted source case",
        ))
    roles = {row.get("role") for row in closure.get("assets", []) if isinstance(row, dict)}
    missing_roles = sorted(set(contract["required_input_roles"]) - roles)
    if missing_roles:
        reasons.append(_reason("required_input_role_missing", "Required input roles are absent", evidence=missing_roles))
    for field in contract["required_closure_fields"]:
        if closure.get(field) in (None, "", [], {}):
            reasons.append(_reason("input_closure_missing", f"Missing input_closure.{field}"))
    actual_inputs, asset_reasons = _materialized_task_assets(
        task,
        closure,
        trusted_case=authoritative,
        manifest_path=manifest_path,
    )
    reasons.extend(asset_reasons)
    answer_schema = task.get("answer_schema") if isinstance(task.get("answer_schema"), dict) else {}
    if authoritative is not None and answer_schema != authoritative.get("answer_schema"):
        reasons.append(_reason(
            "trusted_answer_schema_mismatch",
            "answer_schema differs from trusted source case",
        ))
    if answer_schema.get("kind") != "mechanical_structured":
        reasons.append(_reason("answer_kind_mismatch", "answer_schema.kind must be mechanical_structured"))
    if answer_schema.get("fields") != list(contract["answer_fields"]):
        reasons.append(_reason("answer_fields_mismatch", "answer_schema.fields differs from registry"))
    if answer_schema.get("field_specs") != contract["answer_fields"]:
        reasons.append(_reason("answer_specs_mismatch", "answer_schema.field_specs differs from registry"))
    gt = task.get("ground_truth") if isinstance(task.get("ground_truth"), dict) else {}
    if gt.get("status") != "auto_verified" or not isinstance(gt.get("value"), dict):
        reasons.append(_reason("engineering_ground_truth_unverified", "Engineering GT must be auto_verified and structured"))
    answer = gt.get("value") if isinstance(gt.get("value"), dict) else {}
    authoritative_answer = (
        authoritative.get("ground_truth", {}).get("answer")
        if authoritative is not None
        else answer
    )
    if not isinstance(authoritative_answer, dict):
        authoritative_answer = {}
    if authoritative is not None and answer != authoritative_answer:
        reasons.append(_reason(
            "trusted_ground_truth_mismatch",
            "ground_truth.value differs from trusted source case",
        ))
    component_rows = closure.get("component_ids") if isinstance(closure.get("component_ids"), list) else []
    component_ids = set(component_rows)
    reasons.extend(_validate_task_specific_closure(authoritative_task_type, closure, component_ids))
    reasons.extend(_validate_challenge_controls(
        task,
        component_ids,
        manifest_path=None,
        verify_files=False,
    ))
    units = closure.get("units") if isinstance(closure.get("units"), dict) else {}
    tolerances = closure.get("numerical_tolerance") if isinstance(closure.get("numerical_tolerance"), dict) else {}
    for field, spec in contract["answer_fields"].items():
        if field not in answer:
            reasons.append(_reason("ground_truth_field_missing", f"ground_truth.value.{field} is missing"))
            continue
        reasons.extend(_validate_value(field, spec, answer[field], component_ids))
        dimension = spec.get("unit_dimension")
        if dimension and not isinstance(units.get(dimension), str):
            reasons.append(_reason("answer_unit_missing", f"No input_closure.units.{dimension} for {field}"))
        tolerance = tolerances.get(field)
        if spec.get("tolerance_required") and (
            isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance <= 0
        ):
            reasons.append(_reason("answer_tolerance_missing", f"Positive numerical_tolerance.{field} is required"))
    artifact = gt.get("artifact") if isinstance(gt.get("artifact"), dict) else {}
    trusted_artifact = (
        authoritative.get("ground_truth", {}).get("artifact", {})
        if authoritative is not None
        else artifact
    )
    if not isinstance(trusted_artifact, dict):
        trusted_artifact = {}
    if authoritative is not None and artifact != trusted_artifact:
        reasons.append(_reason(
            "trusted_gt_artifact_binding_mismatch",
            "ground_truth.artifact differs from trusted source case",
        ))
    artifact_path = _task_file(artifact.get("path")) if authoritative is None else None
    artifact_digest: str | None = None
    artifact_answer: dict[str, Any] | None = None
    if authoritative is not None:
        try:
            if manifest_path is None:
                raise MechanicalTrustError("trusted manifest path is unavailable")
            artifact_path, artifact_digest, _, artifact_payload = inspect_trusted_case_file(
                trusted_artifact.get("path"),
                manifest_path,
                max_bytes=MAX_TRUSTED_ARTIFACT_BYTES,
                collect=True,
            )
        except MechanicalTrustError as exc:
            reasons.append(_reason(
                "trusted_gt_artifact_invalid",
                f"Trusted ground-truth artifact failed secure read: {exc}",
            ))
            artifact_payload = None
        else:
            if artifact_digest != str(trusted_artifact.get("sha256", "")).lower():
                reasons.append(_reason(
                    "trusted_gt_artifact_digest_mismatch",
                    "Trusted ground-truth artifact digest differs from manifest",
                ))
    elif artifact_path is None or not artifact_path.is_file():
        reasons.append(_reason("gt_artifact_missing", "Ground-truth artifact is not materialized"))
        artifact_payload = None
    else:
        artifact_digest = sha256_file(artifact_path)
        artifact_payload = artifact_path.read_bytes()
        if artifact_digest != str(artifact.get("sha256", "")).lower():
            reasons.append(_reason("gt_artifact_digest_mismatch", "Ground-truth artifact SHA256 differs from bytes"))
    if artifact_payload is not None:
        try:
            document = json.loads(artifact_payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            reasons.append(_reason("gt_artifact_invalid", f"Ground-truth artifact is not canonical JSON: {exc}"))
        else:
            if not isinstance(document, dict) or document.get("task_type") != authoritative_task_type or not isinstance(document.get("answer"), dict):
                reasons.append(_reason("gt_artifact_invalid", "Artifact must bind task_type and a structured answer"))
            else:
                artifact_answer = document["answer"]
                artifact_owner = authoritative if authoritative is not None else {
                    "evidence": {"required": list(contract["required_evidence"])},
                }
                owner_evidence = (
                    artifact_owner.get("evidence")
                    if isinstance(artifact_owner.get("evidence"), dict)
                    else {}
                )
                reasons.extend(_solver_evidence_reasons(
                    task_type=authoritative_task_type,
                    answer=authoritative_answer,
                    closure=(
                        authoritative.get("input_closure", closure)
                        if authoritative is not None
                        else closure
                    ),
                    required_evidence=list(owner_evidence.get("required") or []),
                    document=document,
                ))

    normalized_gt: dict[str, Any] | None = None
    normalized_artifact: dict[str, Any] | None = None
    try:
        normalized_gt = normalize_answer(authoritative_task_type, authoritative_answer, authoritative.get("input_closure", closure) if authoritative else closure)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        reasons.append(_reason("ground_truth_normalization_failed", f"Cannot normalize ground_truth.value: {exc}"))
    if artifact_answer is not None:
        try:
            normalized_artifact = normalize_answer(authoritative_task_type, artifact_answer, authoritative.get("input_closure", closure) if authoritative else closure)
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            reasons.append(_reason("gt_artifact_answer_invalid", f"Cannot normalize artifact answer: {exc}"))
    if normalized_gt is not None and normalized_artifact is not None and normalized_gt != normalized_artifact:
        reasons.append(_reason("gt_artifact_answer_mismatch", "Artifact answer differs from ground_truth.value after normalization"))

    receipt = task.get("recompute_receipt") if isinstance(task.get("recompute_receipt"), dict) else {}
    required_receipt = {
        "status", "mechanical_contract", "task_id", "task_type", "case_id", "case_sha256",
        "plugin", "command", "solver_name", "solver_version", "solver_parameters_sha256",
        "solver_identity_sha256", "solver_recompute_sha256",
        "input_sha256", "expected_answer_sha256", "actual_answer_sha256",
        "expected_artifact_sha256", "actual_artifact_sha256", "task_binding_sha256", "receipt_sha256",
    }
    missing = sorted(key for key in required_receipt if receipt.get(key) in (None, "", [], {}))
    if missing:
        reasons.append(_reason("recompute_receipt_incomplete", "Recompute receipt is incomplete", evidence=missing))
    elif receipt.get("status") != "pass":
        reasons.append(_reason("recompute_receipt_failed", "Recompute receipt did not pass"))
    if receipt:
        identity = {
            "mechanical_contract": task.get("mechanical_contract"),
            "task_id": task.get("task_id"),
            "task_type": authoritative_task_type,
            "case_id": authoritative_case_id if authoritative is not None else provenance.get("case_id"),
            "case_sha256": authoritative_case_sha if authoritative is not None else provenance.get("case_sha256"),
        }
        mismatched_identity = [key for key, value in identity.items() if receipt.get(key) != value]
        if mismatched_identity:
            reasons.append(_reason("recompute_identity_mismatch", "Receipt identity differs from task", evidence=mismatched_identity))
        if receipt.get("input_sha256") != actual_inputs:
            reasons.append(_reason("recompute_input_digest_mismatch", "Receipt input digests differ from materialized task assets"))
        if provenance.get("input_sha256") != actual_inputs:
            reasons.append(_reason("provenance_input_digest_mismatch", "Provenance input digests differ from materialized task assets"))
        authoritative_solver = authoritative.get("solver", {}) if authoritative is not None else provenance.get("solver", {})
        solver = provenance.get("solver") if isinstance(provenance.get("solver"), dict) else {}
        if authoritative is not None:
            if solver != authoritative_solver:
                reasons.append(_reason(
                    "trusted_solver_provenance_mismatch",
                    "Task solver provenance differs from trusted source case",
                ))
            if provenance.get("case_id") != authoritative_case_id:
                reasons.append(_reason(
                    "trusted_case_id_mismatch",
                    "Task provenance case_id differs from trusted source case",
                ))
            if provenance.get("case_sha256") != authoritative_case_sha:
                reasons.append(_reason(
                    "trusted_case_digest_mismatch",
                    "Task provenance case_sha256 differs from trusted source case",
                ))
        if (
            authoritative_solver.get("name") != receipt.get("solver_name")
            or authoritative_solver.get("version") != receipt.get("solver_version")
            or canonical_sha256(authoritative_solver.get("parameters", {})) != receipt.get("solver_parameters_sha256")
        ):
            reasons.append(_reason("solver_provenance_mismatch", "Solver provenance differs from recompute receipt"))
        authoritative_recompute = (
            authoritative_solver.get("recompute")
            if isinstance(authoritative_solver.get("recompute"), dict)
            else {}
        )
        if (
            receipt.get("plugin") != authoritative_recompute.get("plugin")
            or receipt.get("command") != authoritative_recompute.get("command")
        ):
            reasons.append(_reason(
                "solver_recompute_provenance_mismatch",
                "Receipt plugin and command differ from the trusted solver recompute contract",
                evidence={
                    "trusted_plugin": authoritative_recompute.get("plugin"),
                    "receipt_plugin": receipt.get("plugin"),
                    "trusted_command": authoritative_recompute.get("command"),
                    "receipt_command": receipt.get("command"),
                },
            ))
        if receipt.get("solver_identity_sha256") != canonical_sha256(authoritative_solver):
            reasons.append(_reason(
                "solver_identity_digest_mismatch",
                "Receipt is not bound to the complete trusted solver identity",
            ))
        if receipt.get("solver_recompute_sha256") != canonical_sha256(authoritative_recompute):
            reasons.append(_reason(
                "solver_recompute_digest_mismatch",
                "Receipt is not bound to the complete trusted recompute contract",
            ))
        gt_answer_digest = canonical_sha256(normalized_gt) if normalized_gt is not None else None
        artifact_answer_digest = canonical_sha256(normalized_artifact) if normalized_artifact is not None else None
        if receipt.get("expected_answer_sha256") != gt_answer_digest:
            reasons.append(_reason("receipt_expected_answer_forged", "Receipt expected answer digest differs from ground_truth.value"))
        if receipt.get("actual_answer_sha256") != artifact_answer_digest:
            reasons.append(_reason("receipt_actual_answer_forged", "Receipt actual answer digest differs from GT artifact answer"))
        if receipt.get("expected_artifact_sha256") != artifact_digest:
            reasons.append(_reason("receipt_expected_artifact_forged", "Receipt expected artifact digest differs from artifact bytes"))
        if receipt.get("actual_artifact_sha256") != artifact_digest:
            reasons.append(_reason("receipt_actual_artifact_forged", "Receipt actual artifact digest differs from artifact bytes"))
        binding = canonical_sha256(task_binding_payload(task))
        if receipt.get("task_binding_sha256") != binding:
            reasons.append(_reason("task_binding_mismatch", "Receipt does not bind the current task content"))
        if receipt.get("receipt_sha256") != receipt_binding_sha256(receipt):
            reasons.append(_reason("receipt_signature_mismatch", "Receipt binding digest is invalid"))
    evidence_block = task.get("active_vision_evidence") if isinstance(task.get("active_vision_evidence"), dict) else {}
    evidence = evidence_block.get("required") if isinstance(evidence_block.get("required"), list) else []
    viewer = task.get("viewer_requirements") if isinstance(task.get("viewer_requirements"), list) else []
    if not set(contract["required_evidence"]) <= set(evidence):
        reasons.append(_reason("gt_evidence_missing", "Task omits required engineering evidence"))
    if not set(contract["viewer_requirements"]) <= set(viewer):
        reasons.append(_reason("viewer_requirement_missing", "Task omits required viewer evidence"))
    return reasons
