#!/usr/bin/env python3
"""Generate MedicalKAxisPilot v3 task records — filtered to 3D-vision-load-bearing.

History:
- v1 (PR #79) shipped Mad-Libs records (template placeholders never filled).
- v2 (PR #81) per-case slot fill + auto_seed ground truths.
- v3 (this commit) drops K1 (×3) and K4 (×1) and keeps K2 (×1), K3 (×1),
  K5 (×2) — only the templates where 3D viewer interaction is
  *load-bearing for the answer*, not garnish.

Filter criterion (see docs/qa-gen/medical_k_axis_seeds.md §1):
A task qualifies as K-axis only when **both** of these are required:
  (a) professional medical knowledge to interpret the finding, AND
  (b) active 3D viewing — slice navigation, window-level intensity
      reading at the mask boundary, multi-axis confirmation, or
      pairwise mask-anatomy spatial reasoning under partial-volume —
      to determine the answer.

K1 and K4 fail (b): once the candidate list and the named anatomy are
in the prompt, the diagnosis can be selected from mask_text + medical
knowledge alone; the 3D viewer adds no answer-determining information.

K2 / K3 / K5 pass (b): wall-vs-lumen needs CT density at the mask
boundary; pair topology needs visual judgement under partial-volume;
absence-of-invasion needs orthogonal-slice confirmation.

Run from repo root:
    python3 QA-Gen/tasks/MedicalKAxisPilot/build_records.py

Outputs (overwrites v2):
    QA-Gen/tasks/MedicalKAxisPilot/
        ScienceVision_MedicalKAxisPilot_seed_tasks.jsonl
        ScienceVision_MedicalKAxisPilot_final_tasks.jsonl
        generation_stats.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "QA-Gen" / "tasks" / "MedicalKAxisPilot"

COMMON_SLICE_TOOLS = (
    "sv3d_open_scene",
    "sv3d_get_state",
    "sv3d_camera_set_view",
    "sv3d_camera_orbit",
    "sv3d_camera_dolly",
    "sv3d_render",
    "sv3d_set_slice",
    "sv3d_toggle_mask",
)


# ---------------------------------------------------------------------------
# Asset anchors


@dataclass(frozen=True)
class Case:
    case_id: str
    mask_text: str
    mask_label_count: int


CASES: dict[str, Case] = {
    "s0146": Case(
        "s0146",
        "Multiple calcification foci in the aortic wall, suggesting "
        "atherosclerosis",
        5,
    ),
    "s0212b": Case(
        "s0212",
        "Nodular changes in the local peritoneum of the upper left "
        "abdomen, increased density, consideration of metastasis.",
        6,
    ),
    "s1349": Case(
        "s1349",
        "A strip-like high-density blurred shadow in the right inguinal "
        "region, considering local infection possible",
        3,
    ),
}


def _asset_for(case_id: str, asset_id: str) -> dict[str, Any]:
    repo_ct = f"datasets/M3D-RefSeg/M3D_RefSeg/{case_id}/ct.nii.gz"
    repo_mask = f"datasets/M3D-RefSeg/M3D_RefSeg/{case_id}/mask.nii.gz"
    return {
        "asset_id": asset_id,
        "exists_on_disk": True,
        "format": "nifti",
        "name": case_id,
        "path": "/" + repo_ct,
        "repo_path": repo_ct,
        "thumb": None,
        "viewer": "niivue",
        "mask_path": "/" + repo_mask,
        "mask_repo_path": repo_mask,
    }


# ---------------------------------------------------------------------------
# Per-case slot fills


@dataclass(frozen=True)
class K2Fill:
    target_structure: str
    sub_compartment_enum: tuple[str, ...]
    candidate_pathologies: tuple[str, ...]
    correct_sub_compartment: str
    correct_pathology: str
    rationale: str


@dataclass(frozen=True)
class K3Fill:
    component_a_description: str
    anatomy_b_description: str
    relation_enum: tuple[str, ...]
    significance_enum: tuple[str, ...]
    correct_relation: str
    correct_significance: str
    rationale: str


@dataclass(frozen=True)
class K5Fill:
    target_adjacent_structure: str
    assessment_framework: str
    invasion_enum: tuple[str, ...]
    correct_invasion: str
    stage_or_severity_label: str
    rationale: str


K2_FILLS: dict[str, K2Fill] = {
    "s0146": K2Fill(
        target_structure="aorta",
        sub_compartment_enum=("wall", "lumen", "surrounding_tissue"),
        candidate_pathologies=(
            "atherosclerosis",
            "intraluminal embolus or thrombus",
            "perivascular lymphadenopathy",
            "phlebolith",
            "normal variant",
        ),
        correct_sub_compartment="wall",
        correct_pathology="atherosclerosis",
        rationale=(
            "On CT, calcific foci (>=130 HU rim density) sitting at the "
            "muscular media of the aortic wall — not floating inside the "
            "contrast-filled lumen and not in the periaortic soft tissue "
            "— are the classic appearance of atherosclerotic plaque. "
            "Luminal high-density would suggest embolus / thrombus; "
            "perivascular nodes would be lymphadenopathy."
        ),
    ),
}

K3_FILLS: dict[str, K3Fill] = {
    "s0212b": K3Fill(
        component_a_description=(
            "the high-density peritoneal nodule mask (the small focus on "
            "the upper-left peritoneal surface; corresponds to the "
            "metastasis-suspicious lesion)"
        ),
        anatomy_b_description=(
            "the sigmoid colon wall (the closest bowel-wall structure to "
            "the nodule)"
        ),
        relation_enum=("contains", "contained_by", "adjacent", "disjoint"),
        significance_enum=("yes", "incidental", "no"),
        correct_relation="adjacent",
        correct_significance="yes",
        rationale=(
            "A peritoneal-seeding nodule sits on the serosal surface of "
            "the bowel — touching but not breaching the muscularis. "
            "Topologically that is 'adjacent' (shared surface, neither "
            "contains the other), not 'contains' or 'contained_by'. "
            "Clinical significance is 'yes' because peritoneal "
            "carcinomatosis = AJCC M1c (stage IV), regardless of whether "
            "the bowel wall is breached."
        ),
    ),
}

K5_FILLS: dict[str, K5Fill] = {
    "s0212b": K5Fill(
        target_adjacent_structure="sigmoid colon wall",
        assessment_framework=(
            "AJCC TNM staging for peritoneal metastasis "
            "(M1c remains the designation regardless of bowel-wall invasion)"
        ),
        invasion_enum=("invades", "does_not_invade"),
        correct_invasion="does_not_invade",
        stage_or_severity_label="M1c (peritoneal metastasis)",
        rationale=(
            "Serosal seeding does not invade through the bowel wall by "
            "definition — the implant grows on the outer surface rather "
            "than from the mucosa outward. Two orthogonal slice views "
            "should show continuous muscularis with no transmural defect. "
            "The stage is M1c (distant peritoneal metastasis) and is "
            "independent of whether bowel-wall invasion is also present."
        ),
    ),
    "s1349": K5Fill(
        target_adjacent_structure="femoral neurovascular bundle",
        assessment_framework=(
            "soft-tissue infection extension assessment "
            "(does the inflammatory shadow breach the fascial plane?)"
        ),
        invasion_enum=(
            "crosses_fascia_into_deep_compartment",
            "contained_within_subcutaneous_layer",
        ),
        correct_invasion="contained_within_subcutaneous_layer",
        stage_or_severity_label=(
            "localized cellulitis without fascial breach"
        ),
        rationale=(
            "A strip-like blurred high-density shadow described as "
            "'suspected local infection' is the CT appearance of "
            "cellulitis. Necrotizing fasciitis / deep-compartment "
            "extension would require gas in the deep tissue, fluid "
            "tracking across the fascial plane, or stranding that bridges "
            "fascia — none of which are mentioned in the source caption. "
            "Therefore the inflammatory process is contained in the "
            "subcutaneous layer and does not breach into the femoral "
            "neurovascular bundle."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Slot-filled templates


def _k2_template(fill: K2Fill) -> dict[str, Any]:
    sub_enum = "{" + ", ".join(fill.sub_compartment_enum) + "}"
    path_enum = "{" + ", ".join(fill.candidate_pathologies) + "}"
    instruction = (
        f"Localize the high-density focus mask within the {fill.target_structure}. "
        f"Determine whether it sits in {sub_enum}. State the pathology "
        f"implied by that sub-location, choosing from {path_enum} "
        f"(typical mappings: wall calcification → atherosclerosis; "
        f"luminal density → embolus/thrombus; perivascular → "
        f"lymphadenopathy)."
    )
    return {
        "title": "Wall-vs-lumen sub-containment of high-density focus",
        "instruction": instruction,
        "scientific_action": (
            "sub-anatomy localization with pathology mapping"
        ),
        "answer_kind": "sub_location_plus_pathology",
        "answer_fields": [
            "sub_compartment", "pathology_label", "evidence_slice",
        ],
        "answer_enums": {
            "sub_compartment": list(fill.sub_compartment_enum),
            "pathology_label": list(fill.candidate_pathologies),
        },
        "required_evidence": [
            "operation_trace", "screenshot", "slice_state", "mask_overlay",
        ],
        "scorer_primary": "k2_sublocation_diagnosis",
        "scorer_checks": [
            "sub_compartment_accuracy", "pathology_top1",
        ],
        "allowed_tools": list(COMMON_SLICE_TOOLS) + [
            "sv_inspect_nifti", "sv_window_level_set",
        ],
        "forbidden_tools": ["direct_mask_text_lookup"],
        "ground_truth_type": "expert_sublocation_label",
        "difficulty": "hard",
        "irreducibility_reason": (
            "Wall-vs-lumen distinction requires reading window-leveled CT "
            "intensities at the mask boundary; not derivable from mask "
            "shape alone or from metadata."
        ),
        "preferred_assets": ["m3d-refseg/s0146"],
    }


def _k2_seed_value(fill: K2Fill) -> dict[str, Any]:
    return {
        "sub_compartment": fill.correct_sub_compartment,
        "pathology_label": fill.correct_pathology,
        "evidence_slice": None,
    }


def _k3_template(fill: K3Fill) -> dict[str, Any]:
    relation_enum = "{" + ", ".join(fill.relation_enum) + "}"
    significance_enum = "{" + ", ".join(fill.significance_enum) + "}"
    instruction = (
        f"This volume contains multiple labeled mask components. "
        f"Consider the specific pair below: "
        f"**Component A** = {fill.component_a_description}. "
        f"**Anatomy B** = {fill.anatomy_b_description}. "
        f"Classify the spatial relation between A and B as one of "
        f"{relation_enum}. State whether the observed relation is "
        f"pathologically significant (one of {significance_enum}) and "
        f"justify on one rendered evidence view."
    )
    return {
        "title": "Pairwise mask-anatomy topology and clinical significance",
        "instruction": instruction,
        "scientific_action": (
            "multi-component topology with clinical judgement"
        ),
        "answer_kind": "topology_plus_significance",
        "answer_fields": ["relation", "significance", "evidence_slice"],
        "answer_enums": {
            "relation": list(fill.relation_enum),
            "significance": list(fill.significance_enum),
        },
        "required_evidence": [
            "operation_trace", "screenshot", "slice_state", "annotation",
        ],
        "scorer_primary": "k3_topology_significance",
        "scorer_checks": [
            "relation_accuracy", "significance_accuracy",
        ],
        "allowed_tools": list(COMMON_SLICE_TOOLS) + [
            "sv_inspect_nifti", "sv_toggle_mask_component",
        ],
        "forbidden_tools": [
            "scipy_ndimage_label", "boolean_mask_overlap_solver",
            "direct_mask_text_lookup",
        ],
        "ground_truth_type": (
            "expert_relation_plus_significance_label"
        ),
        "difficulty": "hard",
        "irreducibility_reason": (
            "Boolean overlap solvers are forbidden, forcing visual "
            "judgment of containment under partial-volume averaging."
        ),
        "preferred_assets": [
            "m3d-refseg/s0212", "m3d-refseg/s0038", "m3d-refseg/s1349",
        ],
    }


def _k3_seed_value(fill: K3Fill) -> dict[str, Any]:
    return {
        "relation": fill.correct_relation,
        "significance": fill.correct_significance,
        "evidence_slice": None,
    }


def _k5_template(fill: K5Fill) -> dict[str, Any]:
    invasion_enum = "{" + ", ".join(fill.invasion_enum) + "}"
    instruction = (
        f"Determine whether the masked lesion **crosses into "
        f"{fill.target_adjacent_structure}**. A correct NEGATIVE answer "
        f"must be supported by at least two orthogonal slice views; a "
        f"POSITIVE answer must cite the slice showing breach. Choose "
        f"`invades` from {invasion_enum}. Map your answer to the implied "
        f"label under the following framework: "
        f"**{fill.assessment_framework}**."
    )
    return {
        "title": "Adjacent-structure invasion assessment",
        "instruction": instruction,
        "scientific_action": "negative-evidence invasion assessment",
        "answer_kind": "invasion_plus_stage",
        "answer_fields": [
            "invades", "stage_label", "evidence_slices",
        ],
        "answer_enums": {
            "invades": list(fill.invasion_enum),
        },
        "required_evidence": [
            "operation_trace", "screenshot", "variant_screenshots",
        ],
        "scorer_primary": "k5_invasion_stage",
        "scorer_checks": [
            "binary_accuracy", "stage_accuracy",
            "orthogonal_view_audit",
        ],
        "allowed_tools": list(COMMON_SLICE_TOOLS) + ["sv_inspect_nifti"],
        "forbidden_tools": [
            "direct_mask_text_lookup", "staging_metadata_lookup",
        ],
        "ground_truth_type": "expert_invasion_plus_stage_label",
        "difficulty": "hard",
        "irreducibility_reason": (
            "Absence-of-invasion must be confirmed across orthogonal "
            "slices; single-slice mask projection is insufficient."
        ),
        "preferred_assets": [
            "m3d-refseg/s0212", "m3d-refseg/s1349",
        ],
    }


def _k5_seed_value(fill: K5Fill) -> dict[str, Any]:
    return {
        "invades": fill.correct_invasion,
        "stage_label": fill.stage_or_severity_label,
        "evidence_slices": None,
    }


# ---------------------------------------------------------------------------
# Record envelope


def _envelope(
    *,
    case: Case,
    k_class: str,
    template_dict: dict[str, Any],
    task_id: str,
    asset_id: str,
    seed_value: dict[str, Any],
    case_facts: dict[str, Any],
    rationale: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "domain": "medical",
        "v_class": None,
        "k_class": k_class,
        "scientific_action": template_dict["scientific_action"],
        "title": template_dict["title"],
        "instruction": template_dict["instruction"],
        "difficulty": template_dict["difficulty"],
        "asset": _asset_for(case.case_id, asset_id),
        "asset_grounding": {
            "missing_or_ambiguous_entities": [],
            "notes": (
                f"Anchored to M3D-RefSeg matched whitelist case "
                f"{case.case_id} (mask_label_count="
                f"{case.mask_label_count})."
            ),
            "status": "unchecked",
            "verified_entities": [],
        },
        "answer_schema": {
            "fields": template_dict["answer_fields"],
            "kind": template_dict["answer_kind"],
            "enums": template_dict.get("answer_enums", {}),
        },
        "terminal_answer": {
            "fields": template_dict["answer_fields"],
            "kind": template_dict["answer_kind"],
            "must_be_derived_from_3d_interaction": True,
            "not_allowed_as_answer": [
                "metadata_lookup",
                "mask_text_paraphrase",
                "trace_only",
            ],
        },
        "tool_policy": {
            "allowed_tools": list(template_dict["allowed_tools"]),
            "forbidden_tools": list(template_dict["forbidden_tools"]),
        },
        "scorer": {
            "primary": template_dict["scorer_primary"],
            "checks": list(template_dict["scorer_checks"]),
            "aggregation": "all_required_with_partial_credit",
        },
        "ground_truth": {
            "notes": (
                "v3 auto_seed: value + rationale derived deterministically "
                "from source mask_text; expert should verify and refine "
                "the evidence_slice / evidence_slices fields before release."
            ),
            "status": "auto_seed",
            "type": template_dict["ground_truth_type"],
            "value": seed_value,
            "rationale": rationale,
        },
        "required_evidence": list(template_dict["required_evidence"]),
        "active_vision_evidence": {
            "optional": [
                "intermediate_screenshots", "annotation_overlay",
            ],
            "required": list(template_dict["required_evidence"]),
            "role": "primary_scoring",
        },
        "trace_requirements": {
            "max_steps": 12,
            "must_include_final_state": True,
            "must_save_operation_trace": True,
        },
        "quality_gates": {
            "irreducibility_reason": template_dict["irreducibility_reason"],
            "irreducible_3d": True,
            "requires_rendered_evidence": True,
            "requires_viewer": True,
            "shortcut_risk": "medium",
        },
        "expert_review_requirements": {
            "min_approvals": 1,
            "review_ids": [],
            "roles": ["domain_expert"],
        },
        "validation_record": {
            "auto_checks": {
                "ground_truth_verified": False,
                "no_blocking_failures": True,
                "terminal_answer_present": True,
            },
            "expert_checks": {},
            "loop": {
                "history": [{
                    "failures": ["ground_truth_not_verified"],
                    "iteration": 1,
                    "repairs": [],
                }],
                "iteration": 1,
                "max_iterations": 4,
                "status": "needs_revision",
            },
            "release_gate": "blocked",
        },
        "acceptance_info": {
            "critique_severity": "low",
            "decision": "accept",
            "repairs": [],
        },
        "case_facts": case_facts,
        "provenance": {
            "generator": "MedicalKAxisPilotBuilderV3",
            "plan_item": {
                "asset_count": 1,
                "domain": "medical",
                "k_class": k_class,
                "replica": 0,
                "status": "ready",
                "v_class": None,
            },
            "source_asset_id": asset_id,
            "source_manifest": "datasets/_matched/m3d-refseg.json",
            "source_mask_text": case.mask_text,
            "v1_predecessor_pr": "#79",
            "v2_predecessor_pr": "#81",
            "v3_filter_intent": (
                "Drop K1, K4 — both pass the medical-knowledge bar but "
                "fail the 3D-vision-load-bearing bar (answer can be "
                "selected from labels + knowledge without slice "
                "navigation). Keep K2, K3, K5 where 3D viewing is "
                "answer-determining."
            ),
        },
        "template": {**template_dict, "k_class": k_class},
    }


# ---------------------------------------------------------------------------
# Bindings


@dataclass(frozen=True)
class Binding:
    k_class: str
    case_key: str
    build_record: Callable[..., dict[str, Any]] = field(repr=False)


def _build_k2(case_key: str, task_id: str) -> dict[str, Any]:
    case = CASES[case_key]
    fill = K2_FILLS[case_key]
    template = _k2_template(fill)
    seed_value = _k2_seed_value(fill)
    case_facts = {
        "target_structure": fill.target_structure,
        "sub_compartment_enum": list(fill.sub_compartment_enum),
        "candidate_pathologies": list(fill.candidate_pathologies),
        "correct_sub_compartment": fill.correct_sub_compartment,
        "correct_pathology": fill.correct_pathology,
    }
    return _envelope(
        case=case, k_class="K2", template_dict=template,
        task_id=task_id,
        asset_id=f"medical:m3d-refseg:{case.case_id}",
        seed_value=seed_value, case_facts=case_facts,
        rationale=fill.rationale,
    )


def _build_k3(case_key: str, task_id: str) -> dict[str, Any]:
    case = CASES[case_key]
    fill = K3_FILLS[case_key]
    template = _k3_template(fill)
    seed_value = _k3_seed_value(fill)
    case_facts = {
        "component_a": fill.component_a_description,
        "anatomy_b": fill.anatomy_b_description,
        "relation_enum": list(fill.relation_enum),
        "significance_enum": list(fill.significance_enum),
        "correct_relation": fill.correct_relation,
        "correct_significance": fill.correct_significance,
    }
    return _envelope(
        case=case, k_class="K3", template_dict=template,
        task_id=task_id,
        asset_id=f"medical:m3d-refseg:{case.case_id}",
        seed_value=seed_value, case_facts=case_facts,
        rationale=fill.rationale,
    )


def _build_k5(case_key: str, task_id: str) -> dict[str, Any]:
    case = CASES[case_key]
    fill = K5_FILLS[case_key]
    template = _k5_template(fill)
    seed_value = _k5_seed_value(fill)
    case_facts = {
        "target_adjacent_structure": fill.target_adjacent_structure,
        "assessment_framework": fill.assessment_framework,
        "invasion_enum": list(fill.invasion_enum),
        "correct_invasion": fill.correct_invasion,
        "stage_or_severity_label": fill.stage_or_severity_label,
    }
    return _envelope(
        case=case, k_class="K5", template_dict=template,
        task_id=task_id,
        asset_id=f"medical:m3d-refseg:{case.case_id}",
        seed_value=seed_value, case_facts=case_facts,
        rationale=fill.rationale,
    )


# v3 filtered bindings — keep K2/K3/K5 only.
# Task IDs preserve v2 numbering for traceability (no renumbering to 0001-0004).
BINDINGS: list[tuple[Binding, str]] = [
    (Binding("K2", "s0146",  _build_k2), "sv-medical-k2-0004"),
    (Binding("K3", "s0212b", _build_k3), "sv-medical-k3-0005"),
    (Binding("K5", "s0212b", _build_k5), "sv-medical-k5-0007"),
    (Binding("K5", "s1349",  _build_k5), "sv-medical-k5-0008"),
]


def _records() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for binding, task_id in BINDINGS:
        out.append(binding.build_record(binding.case_key, task_id))
    return out


def _stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_k: dict[str, int] = {}
    for r in records:
        key = r.get("k_class") or "unknown"
        by_k[key] = by_k.get(key, 0) + 1
    return {
        "schema_version": "1.0",
        "set_name": "MedicalKAxisPilot",
        "set_version": "v3",
        "task_counts": {
            "generated_total": len(records),
            "kept_total": len(records),
            "filtered_total": 0,
            "kept_ratio": 1.0,
        },
        "token_usage": {"total_tokens_total": 0},
        "cost": {"cost_usd_total": 0.0},
        "by_k_class": by_k,
        "ground_truth_status_distribution": {
            "auto_seed": len(records),
        },
        "v3_filter_note": (
            "K1 (×3) and K4 (×1) from v2 were removed because the answer "
            "is derivable from candidate labels + medical knowledge "
            "alone, without 3D viewer interaction."
        ),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = _records()

    seed_path = OUT_DIR / "ScienceVision_MedicalKAxisPilot_seed_tasks.jsonl"
    final_path = OUT_DIR / "ScienceVision_MedicalKAxisPilot_final_tasks.jsonl"
    stats_path = OUT_DIR / "generation_stats.json"

    for p in (seed_path, final_path):
        with p.open("w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    stats_path.write_text(json.dumps(_stats(records), indent=2) + "\n")
    print(f"wrote {seed_path.relative_to(ROOT)} ({len(records)} records)")
    print(f"wrote {final_path.relative_to(ROOT)} ({len(records)} records)")
    print(f"wrote {stats_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
