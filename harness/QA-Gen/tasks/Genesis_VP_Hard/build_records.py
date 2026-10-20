#!/usr/bin/env python3
"""Generate Genesis_VP_Hard task records — embodied physics, rollout-grounded.

The Genesis_VP family asks a model to predict the outcome of a physical
interaction (fracture, granular flow) that only a deterministic Genesis
simulation rollout can answer. The model must *act on* the scene, not
just view it (see docs/benchmark/task-families.md → Genesis_VP).

This builder is the BV2-side deterministic recreation of the batch from
its ModelScope source whitelist, mirroring the contract established by
`QA-Gen/tasks/MedicalKAxisPilot/build_records.py`:
  - 0 tokens, 0 USD, no LLM calls;
  - byte-identical output on every run;
  - the rich `final_tasks.jsonl` envelope from
    docs/qa-gen/pipeline-overview.md §5.

Scope (v1): the 2 tasks that exist in the source
`embodied/qa/genesis_vp.jsonl` — MPM fracture (cut_dragon) and MPM
granular cascade (sand_wheel). The two aspirational Genesis_VP tasks in
the docs (force-controlled grasp, FEM cloth) are NOT authored here; they
land when their source questions do.

Ground truth is intentionally unfilled: each `deterministic_simulation`
answer requires running the Genesis solver with the recorded `repro`
block. Records ship `ground_truth.status = "needs_rollout"` (a
genesis-specific extension of the pipeline §6 status enum) with
`value = null` and `release_gate = "blocked"` until the rollout lands.

Run from repo root:
    python3 QA-Gen/tasks/Genesis_VP_Hard/build_records.py

Outputs:
    QA-Gen/tasks/Genesis_VP_Hard/
        ScienceVision_Genesis_VP_Hard_seed_tasks.jsonl
        ScienceVision_Genesis_VP_Hard_final_tasks.jsonl
        generation_stats.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "QA-Gen" / "tasks" / "Genesis_VP_Hard"

# Tools every Genesis rollout task may use: load the scene, apply the
# scripted interaction, roll the solver forward, then measure/render.
COMMON_GENESIS_TOOLS = (
    "sv3d_open_scene",
    "sv3d_get_state",
    "sv3d_render",
    "sv_genesis_load_scene",
    "sv_genesis_apply_force",
    "sv_genesis_actuate",
    "sv_genesis_rollout",
    "sv_genesis_measure",
)


# ---------------------------------------------------------------------------
# Asset anchors


@dataclass(frozen=True)
class Asset:
    asset_id: str          # catalog id, e.g. "embodied/genesis_vp/cut_dragon"
    name: str              # short scene name
    repo_path: str         # path to the glb under the repo
    source_asset: str      # asset ref as written in the source jsonl


ASSETS: dict[str, Asset] = {
    "cut_dragon": Asset(
        asset_id="embodied/genesis_vp/cut_dragon",
        name="cut_dragon",
        repo_path="viewer/datasets/genesis/cut_dragon.glb",
        source_asset="embodied/assets/cut_dragon.glb",
    ),
    "sand_wheel": Asset(
        asset_id="embodied/genesis_vp/sand_wheel",
        name="sand_wheel",
        repo_path="viewer/datasets/genesis/sand_wheel.glb",
        source_asset="embodied/assets/sand_wheel.glb",
    ),
}


def _asset_dict(asset: Asset) -> dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "exists_on_disk": (ROOT / asset.repo_path).is_file(),
        "format": "glb",
        "name": asset.name,
        "path": "/" + asset.repo_path,
        "repo_path": asset.repo_path,
        "thumb": None,
        # Routes to viewer/genesis_view.html (the particle/GLB viewer) via
        # sciencevision_tasks.html's `/viewer/<viewer>.html` convention.
        "viewer": "genesis_view",
    }


# ---------------------------------------------------------------------------
# Per-task physics specs (verbatim from embodied/qa/genesis_vp.jsonl)


@dataclass(frozen=True)
class GenesisTask:
    task_id: str
    source_task_id: str
    physics_class: str          # "mpm_fracture" | "mpm_granular"
    asset_key: str
    title: str
    scientific_action: str
    instruction: str            # the question the agent sees
    answer_kind: str            # "boolean" | "numeric_range"
    answer_fields: tuple[str, ...]
    answer_enums: dict[str, list[str]]
    scorer_primary: str
    scorer_checks: tuple[str, ...]
    extra_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    difficulty: str             # "hard" (P1/P2 in the Genesis_VP_Hard set)
    irreducibility_reason: str
    repro: dict[str, Any]       # solver config that produces the ground truth


TASKS: list[GenesisTask] = [
    GenesisTask(
        task_id="sv-embodied-genesis-0001",
        source_task_id="genesis_vp/q001",
        physics_class="mpm_fracture",
        asset_key="cut_dragon",
        title="MPM fracture under transverse load — connectivity outcome",
        scientific_action=(
            "apply a scripted transverse force at a marked material point, "
            "roll the MPM solver forward, and judge post-rollout connectivity"
        ),
        instruction=(
            "Under a 5 N transverse force applied at the marked vertebra, "
            "will the dragon fracture into two disconnected pieces within "
            "200 simulation steps?"
        ),
        answer_kind="boolean",
        answer_fields=("fractures", "evidence_final_frame"),
        answer_enums={"fractures": ["true", "false"]},
        scorer_primary="genesis_fracture_connectivity",
        scorer_checks=("connected_component_count", "rollout_step_budget"),
        extra_tools=("sv_genesis_mark_point",),
        forbidden_tools=(
            "closed_form_fracture_solver",
            "metadata_lookup",
            "static_frame_guess",
        ),
        difficulty="hard",
        irreducibility_reason=(
            "Whether the mesh separates into two connected components "
            "depends on stress accumulation over the MPM rollout; it "
            "cannot be read off the rest-pose geometry or any single "
            "static frame."
        ),
        repro={
            "solver": "MPM Elastic + CPIC",
            "dt": 0.004,
            "substeps": 10,
            "rollout_steps": 200,
            "grid_density": 64,
            "deterministic": True,
        },
    ),
    GenesisTask(
        task_id="sv-embodied-genesis-0002",
        source_task_id="genesis_vp/q002",
        physics_class="mpm_granular",
        asset_key="sand_wheel",
        title="MPM granular cascade — retained mass fraction after rotation",
        scientific_action=(
            "actuate a 90-degree wheel rotation, roll the granular MPM "
            "solver forward, and measure the sand mass fraction retained "
            "near the rotation axis"
        ),
        instruction=(
            "After rotating the wheel by 90 degrees clockwise, what "
            "fraction of the granular sand pile (initially resting at the "
            "wheel base) remains within a 0.1 m radius of the rotation "
            "axis?"
        ),
        answer_kind="numeric_range",
        answer_fields=("retained_fraction", "evidence_final_frame"),
        answer_enums={},
        scorer_primary="genesis_granular_mass_fraction",
        scorer_checks=("fraction_within_tolerance", "rollout_step_budget"),
        extra_tools=("sv_genesis_measure_region",),
        forbidden_tools=(
            "closed_form_granular_solver",
            "metadata_lookup",
            "static_frame_guess",
        ),
        difficulty="hard",
        irreducibility_reason=(
            "Granular redistribution under rotation is a chaotic, "
            "history-dependent MPM process; the retained fraction is only "
            "recoverable by rolling the solver forward, not by inspecting "
            "the initial pile."
        ),
        repro={
            "solver": "MPM Sand (granular)",
            "dt": 0.003,
            "substeps": 10,
            "rollout_steps": 150,
            "grid_density": 64,
            "deterministic": True,
        },
    ),
]


# ---------------------------------------------------------------------------
# Record envelope


def _envelope(task: GenesisTask) -> dict[str, Any]:
    asset = ASSETS[task.asset_key]
    allowed_tools = list(COMMON_GENESIS_TOOLS) + list(task.extra_tools)
    return {
        "schema_version": "1.0",
        "task_id": task.task_id,
        "domain": "embodied",
        "v_class": None,
        "k_class": None,
        "family": "genesis_vp",
        "physics_class": task.physics_class,
        "scientific_action": task.scientific_action,
        "title": task.title,
        "instruction": task.instruction,
        "difficulty": task.difficulty,
        "asset": _asset_dict(asset),
        "asset_grounding": {
            "missing_or_ambiguous_entities": [],
            "notes": (
                f"Anchored to Genesis_VP scene '{asset.name}' "
                f"({asset.source_asset}); interaction scripted from the "
                f"source question."
            ),
            "status": "unchecked",
            "verified_entities": [],
        },
        "answer_schema": {
            "fields": list(task.answer_fields),
            "kind": task.answer_kind,
            "enums": task.answer_enums,
        },
        "terminal_answer": {
            "fields": list(task.answer_fields),
            "kind": task.answer_kind,
            "must_be_derived_from_3d_interaction": True,
            "not_allowed_as_answer": [
                "metadata_lookup",
                "static_frame_guess",
                "trace_only",
            ],
        },
        "tool_policy": {
            "allowed_tools": allowed_tools,
            "forbidden_tools": list(task.forbidden_tools),
        },
        "scorer": {
            "primary": task.scorer_primary,
            "checks": list(task.scorer_checks),
            "aggregation": "all_required_with_partial_credit",
        },
        "ground_truth": {
            "notes": (
                "needs_rollout: the answer is a deterministic function of "
                "the Genesis solver rollout defined in `repro`. No value is "
                "seeded — running the solver produces it. Release gate stays "
                "blocked until the rollout lands and an expert confirms."
            ),
            "status": "needs_rollout",
            "type": "deterministic_simulation",
            "value": None,
            "repro": task.repro,
        },
        "required_evidence": [
            "operation_trace",
            "rollout_state",
            "final_frame_screenshot",
        ],
        "active_vision_evidence": {
            "optional": ["intermediate_frames", "annotation_overlay"],
            "required": [
                "operation_trace",
                "rollout_state",
                "final_frame_screenshot",
            ],
            "role": "primary_scoring",
        },
        "trace_requirements": {
            "max_steps": 12,
            "must_include_final_state": True,
            "must_save_operation_trace": True,
        },
        "quality_gates": {
            "irreducibility_reason": task.irreducibility_reason,
            "irreducible_3d": True,
            "requires_rendered_evidence": True,
            "requires_viewer": True,
            "requires_simulation": True,
            "shortcut_risk": "low",
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
                    "failures": ["ground_truth_needs_rollout"],
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
        "provenance": {
            "generator": "Genesis_VP_HardBuilderV1",
            "plan_item": {
                "asset_count": 1,
                "domain": "embodied",
                "family": "genesis_vp",
                "physics_class": task.physics_class,
                "replica": 0,
                "status": "ready",
                "v_class": None,
            },
            "source_asset_id": asset.asset_id,
            "source_manifest": "embodied/qa/genesis_vp.jsonl",
            "source_task_id": task.source_task_id,
        },
    }


# ---------------------------------------------------------------------------
# Build + stats


def _records() -> list[dict[str, Any]]:
    return [_envelope(t) for t in TASKS]


def _stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_physics: dict[str, int] = {}
    for r in records:
        key = r.get("physics_class") or "unknown"
        by_physics[key] = by_physics.get(key, 0) + 1
    return {
        "schema_version": "1.0",
        "set_name": "Genesis_VP_Hard",
        "set_version": "v1",
        "task_counts": {
            "generated_total": len(records),
            "kept_total": len(records),
            "filtered_total": 0,
            "kept_ratio": 1.0,
        },
        "token_usage": {"total_tokens_total": 0},
        "cost": {"cost_usd_total": 0.0},
        "by_physics_class": by_physics,
        "ground_truth_status_distribution": {
            "needs_rollout": len(records),
        },
        "scope_note": (
            "v1 ships the 2 tasks present in the source whitelist "
            "(embodied/qa/genesis_vp.jsonl). The 2 aspirational Genesis_VP "
            "tasks (force-controlled grasp, FEM cloth) land when their "
            "source questions do."
        ),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = _records()

    seed_path = OUT_DIR / "ScienceVision_Genesis_VP_Hard_seed_tasks.jsonl"
    final_path = OUT_DIR / "ScienceVision_Genesis_VP_Hard_final_tasks.jsonl"
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
