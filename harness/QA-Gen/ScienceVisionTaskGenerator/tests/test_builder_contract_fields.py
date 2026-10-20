"""Tests for the five new contract fields emitted by BuilderStage._build_task.

TDD: written before the implementation. Fails RED until builder_stage.py emits
terminal_answer, active_vision_evidence, asset_grounding, validation_record,
and expert_review_requirements.

Catalog shape mirrors what builder_stage.py expects:
  catalog["domains"][domain] = list of asset dicts
Each asset dict must have: asset_id, name, path, repo_path, viewer, format,
  thumb (optional), exists_on_disk (optional bool).
"""

from __future__ import annotations

import pytest

from sciencevision_taskgen.pipeline.builder_stage import BuilderStage
from sciencevision_taskgen.templates import TEMPLATES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asset(asset_id: str = "bio-001", name: str = "4HHB") -> dict:
    return {
        "asset_id": asset_id,
        "name": name,
        "path": f"datasets/biology/{name}.pdb",
        "repo_path": f"datasets/biology/{name}.pdb",
        "viewer": "molstar",
        "format": "pdb",
        "thumb": f"thumbs/{name}.webp",
        "exists_on_disk": True,
    }


# Biology V2/V3 are grounded to requires=("molecule_name","ligand"); 4HHB (a
# cofactor-only structure) is intentionally excluded from them, so those cells
# must be built with a ligand-bearing asset.  All other biology cells are
# satisfied by 4HHB.
_ASSET_NAME_BY_V_CLASS = {"V2": "6LU7", "V3": "6LU7"}


def _make_plan_item(domain: str, v_class: str, replica: int = 0) -> dict:
    return {
        "domain": domain,
        "v_class": v_class,
        "replica": replica,
        "status": "ready",
        "asset_count": 1,
    }


def _build_single(domain: str, v_class: str, config: dict | None = None) -> dict:
    """Run BuilderStage and return the single produced task."""
    cfg = config if config is not None else {}
    builder = BuilderStage(cfg)
    plan = [_make_plan_item(domain, v_class)]
    asset_name = _ASSET_NAME_BY_V_CLASS.get(v_class, "4HHB") if domain == "biology" else "4HHB"
    catalog = {"domains": {domain: [_make_asset(name=asset_name)]}}
    tasks = builder.build_tasks(plan, catalog)
    assert len(tasks) == 1, f"Expected 1 task, got {len(tasks)}"
    return tasks[0]


# ---------------------------------------------------------------------------
# Backward compatibility: old keys must still be present
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_answer_schema_still_present(self):
        task = _build_single("biology", "V1")
        assert "answer_schema" in task

    def test_required_evidence_still_present(self):
        task = _build_single("biology", "V1")
        assert "required_evidence" in task


# ---------------------------------------------------------------------------
# terminal_answer
# ---------------------------------------------------------------------------


class TestTerminalAnswer:
    def test_key_present(self):
        task = _build_single("biology", "V1")
        assert "terminal_answer" in task

    def test_kind_matches_template(self):
        task = _build_single("biology", "V1")
        template = TEMPLATES["biology"]["V1"]
        assert task["terminal_answer"]["kind"] == template.terminal_answer_kind

    def test_kind_is_numeric_for_biology_v1(self):
        task = _build_single("biology", "V1")
        assert task["terminal_answer"]["kind"] == "numeric"

    def test_fields_match_template(self):
        task = _build_single("biology", "V1")
        template = TEMPLATES["biology"]["V1"]
        assert task["terminal_answer"]["fields"] == list(template.terminal_answer_fields)

    def test_fields_for_biology_v1(self):
        task = _build_single("biology", "V1")
        assert task["terminal_answer"]["fields"] == ["target_visibility_score"]

    def test_must_be_derived_from_3d_interaction_is_true(self):
        task = _build_single("biology", "V1")
        assert task["terminal_answer"]["must_be_derived_from_3d_interaction"] is True

    def test_not_allowed_contains_required_members(self):
        task = _build_single("biology", "V1")
        assert set(task["terminal_answer"]["not_allowed_as_answer"]) >= {
            "camera_pose_only",
            "trace_only",
            "metadata_lookup",
        }

    def test_not_allowed_is_fresh_list(self):
        """Each task must get its own list — not a shared mutable default."""
        t1 = _build_single("biology", "V1")
        t2 = _build_single("biology", "V1")
        assert t1["terminal_answer"]["not_allowed_as_answer"] is not t2["terminal_answer"]["not_allowed_as_answer"]


# ---------------------------------------------------------------------------
# active_vision_evidence
# ---------------------------------------------------------------------------


class TestActiveVisionEvidence:
    def test_key_present(self):
        task = _build_single("biology", "V1")
        assert "active_vision_evidence" in task

    def test_required_matches_template_required_evidence(self):
        task = _build_single("biology", "V1")
        template = TEMPLATES["biology"]["V1"]
        assert task["active_vision_evidence"]["required"] == list(template.required_evidence)

    def test_optional_contains_intermediate_screenshots(self):
        task = _build_single("biology", "V1")
        assert "intermediate_screenshots" in task["active_vision_evidence"]["optional"]

    def test_role_is_audit_and_secondary_scoring(self):
        task = _build_single("biology", "V1")
        assert task["active_vision_evidence"]["role"] == "audit_and_secondary_scoring"

    def test_required_is_fresh_list(self):
        t1 = _build_single("biology", "V1")
        t2 = _build_single("biology", "V1")
        assert t1["active_vision_evidence"]["required"] is not t2["active_vision_evidence"]["required"]


# ---------------------------------------------------------------------------
# asset_grounding
# ---------------------------------------------------------------------------


class TestAssetGrounding:
    def test_key_present(self):
        task = _build_single("biology", "V1")
        assert "asset_grounding" in task

    def test_status_grounded_for_annotated_template(self):
        # biology/V1 declares requires=(molecule_name, cofactor); asset 4HHB
        # supplies both, so the task is grounded to verified entities.
        task = _build_single("biology", "V1")
        assert task["asset_grounding"]["status"] == "grounded"

    def test_verified_entities_populated_when_grounded(self):
        task = _build_single("biology", "V1")
        assert task["asset_grounding"]["verified_entities"] == [
            "human deoxyhaemoglobin",
            "heme group",
        ]

    def test_missing_or_ambiguous_entities_is_empty_list(self):
        task = _build_single("biology", "V1")
        assert task["asset_grounding"]["missing_or_ambiguous_entities"] == []

    def test_notes_present(self):
        task = _build_single("biology", "V1")
        assert "notes" in task["asset_grounding"]
        assert task["asset_grounding"]["notes"]  # non-empty string

    def test_verified_entities_is_fresh_list(self):
        t1 = _build_single("biology", "V1")
        t2 = _build_single("biology", "V1")
        assert t1["asset_grounding"]["verified_entities"] is not t2["asset_grounding"]["verified_entities"]


# ---------------------------------------------------------------------------
# validation_record
# ---------------------------------------------------------------------------


class TestValidationRecord:
    def test_key_present(self):
        task = _build_single("biology", "V1")
        assert "validation_record" in task

    def test_auto_checks_is_empty_dict(self):
        task = _build_single("biology", "V1")
        assert task["validation_record"]["auto_checks"] == {}

    def test_expert_checks_is_empty_dict(self):
        task = _build_single("biology", "V1")
        assert task["validation_record"]["expert_checks"] == {}

    def test_loop_iteration_is_zero(self):
        task = _build_single("biology", "V1")
        assert task["validation_record"]["loop"]["iteration"] == 0

    def test_loop_max_iterations_defaults_to_4(self):
        """Empty config → max_validation_iterations defaults to 4."""
        task = _build_single("biology", "V1", config={})
        assert task["validation_record"]["loop"]["max_iterations"] == 4

    def test_loop_max_iterations_reads_from_config(self):
        task = _build_single("biology", "V1", config={"validation": {"max_validation_iterations": 6}})
        assert task["validation_record"]["loop"]["max_iterations"] == 6

    def test_loop_status_is_pending(self):
        task = _build_single("biology", "V1")
        assert task["validation_record"]["loop"]["status"] == "pending"

    def test_loop_is_exact_dict(self):
        task = _build_single("biology", "V1")
        assert task["validation_record"]["loop"] == {
            "iteration": 0,
            "max_iterations": 4,
            "status": "pending",
        }

    def test_release_gate_is_pending(self):
        task = _build_single("biology", "V1")
        assert task["validation_record"]["release_gate"] == "pending"

    def test_auto_checks_is_fresh_dict(self):
        t1 = _build_single("biology", "V1")
        t2 = _build_single("biology", "V1")
        assert t1["validation_record"]["auto_checks"] is not t2["validation_record"]["auto_checks"]


# ---------------------------------------------------------------------------
# expert_review_requirements
# ---------------------------------------------------------------------------


class TestExpertReviewRequirements:
    def test_key_present(self):
        task = _build_single("biology", "V1")
        assert "expert_review_requirements" in task

    def test_v1_roles_is_domain_expert_only(self):
        """V1 is not in {V3, V4, V6}, so only domain_expert is required."""
        task = _build_single("biology", "V1")
        assert task["expert_review_requirements"]["roles"] == ["domain_expert"]

    def test_v2_roles_is_domain_expert_only(self):
        task = _build_single("biology", "V2")
        assert task["expert_review_requirements"]["roles"] == ["domain_expert"]

    def test_v3_roles_includes_scorer_expert(self):
        task = _build_single("biology", "V3")
        roles = task["expert_review_requirements"]["roles"]
        assert "domain_expert" in roles
        assert "scorer_expert" in roles

    def test_v4_roles_includes_scorer_expert(self):
        task = _build_single("biology", "V4")
        roles = task["expert_review_requirements"]["roles"]
        assert "domain_expert" in roles
        assert "scorer_expert" in roles

    def test_v5_roles_is_domain_expert_only(self):
        task = _build_single("biology", "V5")
        assert task["expert_review_requirements"]["roles"] == ["domain_expert"]

    def test_v6_roles_includes_scorer_expert(self):
        task = _build_single("biology", "V6")
        roles = task["expert_review_requirements"]["roles"]
        assert "domain_expert" in roles
        assert "scorer_expert" in roles

    def test_min_approvals_is_1(self):
        task = _build_single("biology", "V1")
        assert task["expert_review_requirements"]["min_approvals"] == 1

    def test_review_ids_is_empty_list(self):
        task = _build_single("biology", "V1")
        assert task["expert_review_requirements"]["review_ids"] == []

    def test_review_ids_is_fresh_list(self):
        t1 = _build_single("biology", "V1")
        t2 = _build_single("biology", "V1")
        assert t1["expert_review_requirements"]["review_ids"] is not t2["expert_review_requirements"]["review_ids"]

    def test_v3_roles_exact_order(self):
        """Roles must be exactly ["domain_expert", "scorer_expert"]."""
        task = _build_single("biology", "V3")
        assert task["expert_review_requirements"]["roles"] == ["domain_expert", "scorer_expert"]
