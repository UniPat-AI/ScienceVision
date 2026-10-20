"""Static and empirical shortcut checks for :mod:`scripts.qa_qc`.

The checks identify candidate bypass routes. They do not claim that a package
can answer every scientific judgement merely because it can read the file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


MOLECULAR_FORMATS = {"cif", "mmcif", "mol", "mol2", "pdb", "sdf", "xyz"}
# ``run_end2end --matrix t0-probe`` writes ``job-codex-text``.  Keep that
# concrete launcher label here as well as historical aliases: otherwise a
# successful prose-only shortcut probe is loaded but silently ignored by the
# QC gate, which can make an untested task appear citable.
TEXT_PROBE_BASELINES = {"text-only", "cc-text", "codex-text", "text"}
# The paired T0 file probe is normally the ``codex`` route; retain ``cc`` and
# historical labels for archived collections.
RAW_COMPUTE_BASELINES = {"cc", "codex", "raw-file", "package-compute"}

PACKAGE_RULES = (
    {
        "route_id": "stereochemistry",
        "pattern": r"\b(?:chiral(?:ity| centers?)?|stereocenters?|r/s configuration)\b",
        "property": "stereocenter count or R/S assignment",
        "candidates": [
            {
                "package": "RDKit",
                "apis": ["Chem.AssignStereochemistryFrom3D", "Chem.FindMolChiralCenters"],
            }
        ],
        "control_terms": ("rdkit", "stereo", "chiral"),
    },
    {
        "route_id": "conformation",
        "pattern": r"\b(?:dihedral|torsion|anti|gauche|eclipsed|rotatable bond)\b",
        "property": "dihedral angle or conformer class",
        "candidates": [
            {"package": "RDKit", "apis": ["rdMolTransforms.GetDihedralDeg"]},
            {"package": "NumPy", "apis": ["coordinate-vector dihedral calculation"]},
        ],
        "control_terms": ("rdkit", "dihedral", "torsion", "coordinate"),
    },
    {
        "route_id": "molecular_geometry",
        "pattern": (
            r"\b(?:atom pair|bond length|clash|closest (?:polar )?contact|contact distance|"
            r"hydrogen bond|salt bridge|steric|unoccupied subpocket)\b"
        ),
        "property": "distance, contact, clash, or pocket geometry",
        "candidates": [
            {"package": "Gemmi", "apis": ["NeighborSearch", "UnitCell.find_nearest_image"]},
            {"package": "BioPython", "apis": ["Bio.PDB.NeighborSearch"]},
            {"package": "MDAnalysis", "apis": ["lib.distances.distance_array"]},
            {"package": "NumPy", "apis": ["direct coordinate distance calculation"]},
        ],
        "control_terms": (
            "gemmi", "biopython", "mdanalysis", "numpy", "contact", "distance",
            "clash", "plip", "pocket",
        ),
    },
    {
        "route_id": "crystal_packing",
        "pattern": r"\b(?:crystal packing|packing motif|symmetry-expanded|intermolecular packing)\b",
        "property": "symmetry-expanded contacts or packing connectivity",
        "candidates": [
            {"package": "Gemmi", "apis": ["SpaceGroup.operations", "NeighborSearch"]},
            {"package": "MDAnalysis", "apis": ["lib.distances.distance_array"]},
        ],
        "control_terms": ("gemmi", "symmetry", "packing", "contact", "mdanalysis"),
    },
)

METADATA_RULES = (
    {
        "route_id": "crystallographic_metadata",
        "pattern": r"\b(?:space group|formula units?|unit cell \(z\))\b",
        "property": "space group or formula units per unit cell",
        "formats": {"cif", "mmcif"},
        "locations": ["_space_group_name_H-M_alt", "_symmetry_space_group_name_H-M", "_cell_formula_units_Z"],
        "candidates": [{"package": "Gemmi", "apis": ["cif.read", "Block.find_value"]}],
        "control_terms": ("gemmi", "cif_read", "symmetry", "spacegroup", "metadata", "lookup"),
    },
)

PDB_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{0,3}[A-Za-z])[1-9][A-Za-z0-9]{3}(?![A-Za-z0-9])"
)
COD_ID_RE = re.compile(r"\bcod[_:\s-]*(\d{6,8})\b", re.IGNORECASE)
GENERIC_NAME_PARTS = {
    "asset", "candidate", "chem", "cif", "complex", "conformer", "decoy", "final",
    "input", "ligand", "model", "molecule", "mol", "native", "output", "pose",
    "private", "protein", "qm", "structure",
}


def content_reward(data: dict[str, Any]) -> float | None:
    """Extract answer quality without the active-vision process penalty."""
    rewards = data.get("rewards")
    if not isinstance(rewards, dict):
        rewards = (data.get("verifier_result") or {}).get("rewards") or {}
    if not isinstance(rewards, dict):
        return None
    preferred = rewards.get("llm_judge")
    candidates = [
        value for key, value in rewards.items()
        if key != "reward" and not key.startswith("process") and isinstance(value, (int, float))
    ]
    if isinstance(preferred, (int, float)):
        return float(preferred)
    return max(map(float, candidates), default=None)


def _assets(task: dict[str, Any]) -> list[dict[str, Any]]:
    assets = task.get("assets")
    if isinstance(assets, list) and assets:
        return [value for value in assets if isinstance(value, dict)]
    asset = task.get("asset")
    return [asset] if isinstance(asset, dict) else []


def _asset_formats(task: dict[str, Any]) -> list[str]:
    formats = {str(asset.get("format") or "").casefold() for asset in _assets(task)}
    return sorted(value for value in formats if value)


def _control_state(task: dict[str, Any]) -> dict[str, Any]:
    policy = task.get("tool_policy") if isinstance(task.get("tool_policy"), dict) else {}
    terminal = task.get("terminal_answer") if isinstance(task.get("terminal_answer"), dict) else {}
    active = task.get("active_vision_evidence")
    required_evidence = list(task.get("required_evidence") or [])
    if isinstance(active, dict):
        required_evidence.extend(active.get("required") or [])
    trace = task.get("trace_requirements") if isinstance(task.get("trace_requirements"), dict) else {}
    return {
        "must_be_derived_from_3d_interaction": bool(terminal.get("must_be_derived_from_3d_interaction")),
        "declared_shortcut_risk": (task.get("quality_gates") or {}).get("shortcut_risk"),
        "declared_controls": {
            "forbidden_tools": list(policy.get("forbidden_tools") or []),
            "not_allowed_as_answer": list(terminal.get("not_allowed_as_answer") or []),
            "required_evidence": sorted(set(str(value) for value in required_evidence)),
            "must_save_operation_trace": bool(trace.get("must_save_operation_trace")),
        },
        # A policy declaration is not runtime enforcement. Future receipts may populate this list.
        "enforced_controls": [],
    }


def _matched_declared_controls(controls: dict[str, Any], terms: tuple[str, ...]) -> list[str]:
    declared_state = controls["declared_controls"]
    declared = declared_state["forbidden_tools"] + declared_state["not_allowed_as_answer"]
    return [
        str(value)
        for value in declared
        if any(term in str(value).casefold() for term in terms)
    ]


def _matched_enforced_controls(controls: dict[str, Any], terms: tuple[str, ...]) -> list[str]:
    return [
        str(value)
        for value in controls["enforced_controls"]
        if any(term in str(value).casefold() for term in terms)
    ]


def _identity_evidence(task: dict[str, Any], instruction: str) -> list[dict[str, str]]:
    sources = [("instruction", "instruction", instruction)]
    for asset in _assets(task):
        for field in ("asset_id", "name", "repo_path", "path"):
            value = asset.get(field)
            if value:
                identifier_source = "dataset_metadata" if field == "asset_id" else "raw_metadata"
                sources.append((identifier_source, f"asset.{field}", str(value)))

    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for identifier_source, source_field, value in sources:
        for match in PDB_ID_RE.findall(value):
            item = ("PDB", match.upper(), identifier_source)
            if item not in seen:
                evidence.append(
                    {
                        "kind": item[0],
                        "value": item[1],
                        "identifier_source": identifier_source,
                        "source_field": source_field,
                    }
                )
                seen.add(item)
        for match in COD_ID_RE.findall(value):
            item = ("COD", match, identifier_source)
            if item not in seen:
                evidence.append(
                    {
                        "kind": item[0],
                        "value": item[1],
                        "identifier_source": identifier_source,
                        "source_field": source_field,
                    }
                )
                seen.add(item)

    if set(_asset_formats(task)) & MOLECULAR_FORMATS:
        for asset in _assets(task):
            path = str(asset.get("name") or asset.get("repo_path") or asset.get("path") or "")
            for part in re.findall(r"[A-Za-z]{5,}", Path(path).stem):
                lowered = part.casefold()
                if lowered in GENERIC_NAME_PARTS:
                    continue
                item = ("compound_name", lowered, "raw_metadata")
                if item not in seen:
                    evidence.append(
                        {
                            "kind": item[0],
                            "value": item[1],
                            "identifier_source": "raw_metadata",
                            "source_field": "asset filename",
                        }
                    )
                    seen.add(item)
    return evidence


def _finding(code: str, severity: str, message: str, evidence: Any, action: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": evidence,
        "suggested_action": action,
    }


def audit_shortcuts(task: dict[str, Any], instruction: str) -> dict[str, Any]:
    """Return explainable static shortcut routes and associated findings."""
    controls = _control_state(task)
    formats = _asset_formats(task)
    searchable = " ".join(
        str(value or "")
        for value in (instruction, task.get("scientific_action"), task.get("title"))
    ).casefold()
    package_routes: list[dict[str, Any]] = []
    metadata_routes: list[dict[str, Any]] = []

    if set(formats) & MOLECULAR_FORMATS or str(task.get("domain") or "").casefold() in {"chemistry", "biology"}:
        for rule in PACKAGE_RULES:
            if re.search(rule["pattern"], searchable, re.IGNORECASE):
                package_routes.append(
                    {
                        "route_id": rule["route_id"],
                        "property": rule["property"],
                        "asset_formats": formats,
                        "candidates": rule["candidates"],
                        "matched_declared_controls": _matched_declared_controls(
                            controls, rule["control_terms"]
                        ),
                        "matched_enforced_controls": _matched_enforced_controls(
                            controls, rule["control_terms"]
                        ),
                    }
                )
    for rule in METADATA_RULES:
        if set(formats) & rule["formats"] and re.search(rule["pattern"], searchable, re.IGNORECASE):
            metadata_routes.append(
                {
                    "route_id": rule["route_id"],
                    "property": rule["property"],
                    "asset_formats": formats,
                    "metadata_locations": rule["locations"],
                    "candidates": rule["candidates"],
                    "matched_declared_controls": _matched_declared_controls(
                        controls, rule["control_terms"]
                    ),
                    "matched_enforced_controls": _matched_enforced_controls(
                        controls, rule["control_terms"]
                    ),
                }
            )

    identities = _identity_evidence(task, instruction)
    instruction_identities = [
        item for item in identities if item["identifier_source"] == "instruction"
    ]
    raw_metadata_identities = [
        item for item in identities if item["identifier_source"] == "raw_metadata"
    ]
    findings: list[dict[str, Any]] = []
    if instruction_identities:
        findings.append(
            _finding(
                "text_knowledge_shortcut", "warning",
                "The instruction exposes a public structure or compound identity.",
                {"status": "candidate", "identities": instruction_identities},
                "Hide or randomize public IDs and informative filenames, then run the isolated text-only probe.",
            )
        )
    if raw_metadata_identities:
        findings.append(
            _finding(
                "asset_identifier_shortcut", "warning",
                "Raw asset metadata exposes a public structure or compound identity.",
                {"status": "static_candidate", "identities": raw_metadata_identities},
                "Strip informative raw filenames/metadata or treat public lookup as an allowed route.",
            )
        )
    if metadata_routes:
        findings.append(
            _finding(
                "metadata_shortcut", "warning",
                "A requested answer may be read directly from structure-file metadata.",
                {"status": "static_candidate", "routes": metadata_routes},
                "Do not claim active 3D irreducibility for metadata fields; remove them or test them separately.",
            )
        )
    if package_routes:
        findings.append(
            _finding(
                "package_compute_shortcut", "warning",
                "Raw coordinates can expose a direct Python-package computation route.",
                {"status": "static_candidate", "routes": package_routes},
                "Run the raw-file probe and either accept this as a compute task or enforce and verify a viewer-only route.",
            )
        )

    undeclared = [
        {"kind": kind, **route}
        for kind, routes in (("metadata", metadata_routes), ("package_compute", package_routes))
        for route in routes
        if not route["matched_declared_controls"] and not route["matched_enforced_controls"]
    ]
    declared_but_unenforced = [
        {"kind": kind, **route}
        for kind, routes in (("metadata", metadata_routes), ("package_compute", package_routes))
        for route in routes
        if route["matched_declared_controls"] and not route["matched_enforced_controls"]
    ]
    if controls["must_be_derived_from_3d_interaction"] and undeclared:
        findings.append(
            _finding(
                "shortcut_control_missing", "error",
                "The task requires active 3D derivation but leaves a detected raw-file shortcut uncontrolled.",
                {"uncontrolled_routes": undeclared, "controls": controls},
                "Add an enforceable sandbox/policy control for each route, or remove the active-3D-only claim.",
            )
        )
    if controls["must_be_derived_from_3d_interaction"] and declared_but_unenforced:
        findings.append(
            _finding(
                "shortcut_control_unenforced", "warning",
                "The task declares shortcut restrictions but provides no runtime enforcement receipt.",
                {"routes": declared_but_unenforced, "controls": controls},
                "Attach a runtime enforcement receipt or treat the shortcut capability as exposed.",
            )
        )

    return {
        "controls": controls,
        "routes": {
            "identifiers": identities,
            "text_knowledge": instruction_identities,
            "asset_identifiers": raw_metadata_identities,
            "metadata": metadata_routes,
            "package_compute": package_routes,
        },
        "probe_results": {},
        "findings": findings,
    }


def attach_probe_results(
    result: dict[str, Any], baseline_summary: dict[str, Any], success_threshold: float
) -> None:
    """Attach controlled text/raw-file probe evidence to a task result."""
    shortcut = result.get("shortcuts") or {}
    probes = shortcut.setdefault("probe_results", {})
    for baseline, cell in baseline_summary.items():
        reward = cell.get("effective_reward")
        content = cell.get("effective_content_reward")
        score = content if content is not None else reward
        score_kind = "content_reward" if content is not None else "weighted_reward"
        if baseline in TEXT_PROBE_BASELINES:
            probes[baseline] = {
                "kind": "text_only", "reward": reward, "content_reward": content,
                "source": cell.get("effective_source"),
            }
            if score is not None and score >= success_threshold:
                _upsert_probe_finding(
                    result, "text_knowledge_shortcut", baseline, score, score_kind, success_threshold,
                    error=bool(shortcut.get("controls", {}).get("must_be_derived_from_3d_interaction")),
                    status="empirically_confirmed",
                    message="The no-file, no-image text-only probe answered above the shortcut threshold.",
                )
        elif baseline in RAW_COMPUTE_BASELINES and any(
            shortcut.get("routes", {}).get(kind) for kind in ("package_compute", "metadata")
        ):
            probes[baseline] = {
                "kind": "raw_file_compute", "reward": reward, "content_reward": content,
                "source": cell.get("effective_source"),
            }
            if score is not None and score >= success_threshold:
                for route_kind, code in (
                    ("package_compute", "package_compute_shortcut"),
                    ("metadata", "metadata_shortcut"),
                ):
                    if shortcut["routes"].get(route_kind):
                        _upsert_probe_finding(
                            result, code, baseline, score, score_kind, success_threshold,
                            error=False, status="empirically_corroborated",
                            message="The raw-file baseline scored above the shortcut threshold on a static route.",
                        )


def _upsert_probe_finding(
    result: dict[str, Any], code: str, baseline: str, score: float, score_kind: str, threshold: float,
    *, error: bool, status: str, message: str,
) -> None:
    probe = {"baseline": baseline, "score": score, "score_kind": score_kind, "success_threshold": threshold}
    finding = next((value for value in result["findings"] if value["code"] == code), None)
    if finding is None:
        finding = _finding(
            code, "error" if error else "warning", message,
            {"status": status, "probes": [probe]},
            "Repair the shortcut, then rerun this isolated probe before release.",
        )
        result["findings"].append(finding)
        return
    finding["message"] = message
    finding["severity"] = "error" if error else finding["severity"]
    evidence = finding.setdefault("evidence", {})
    evidence["status"] = status
    evidence.setdefault("probes", []).append(probe)
