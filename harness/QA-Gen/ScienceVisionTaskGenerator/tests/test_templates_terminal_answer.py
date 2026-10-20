"""Tests for terminal_answer and active_vision_evidence contracts on all Templates.

TDD: these tests are written BEFORE the dataclass fields and template_to_dict changes
are applied. They will fail (RED) until the implementation is complete (GREEN).
"""

from __future__ import annotations

import pytest

from sciencevision_taskgen.templates import TEMPLATES, template_to_dict

# ---------------------------------------------------------------------------
# Valid terminal_answer_kind enum values (mirrors the JSON schema)
# ---------------------------------------------------------------------------

VALID_TERMINAL_KINDS = {
    "binary",
    "categorical",
    "numeric",
    "bbox",
    "mask",
    "point",
    "relation",
    "ordered_steps",
    "freeform_with_rubric",
    "camera_pose_only",
    "trace_only",
}

# These two are explicitly forbidden as terminal answers for any real template
PROCESS_ONLY_KINDS = {"camera_pose_only", "trace_only"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def all_cells():
    """Yield (domain, v_class, template) for every cell in TEMPLATES."""
    for domain, v_map in TEMPLATES.items():
        for v_class, template in v_map.items():
            yield domain, v_class, template


# ---------------------------------------------------------------------------
# Suite 1 – dataclass field contracts across all 42 cells
# ---------------------------------------------------------------------------


class TestAllCellsHaveTerminalAnswerFields:
    """Every template must declare non-empty terminal_answer_fields."""

    def test_all_cells_have_non_empty_terminal_answer_fields(self):
        missing = []
        for domain, v_class, tmpl in all_cells():
            if not hasattr(tmpl, "terminal_answer_fields") or not tmpl.terminal_answer_fields:
                missing.append(f"{domain}/{v_class}")
        assert not missing, (
            f"Templates with empty or missing terminal_answer_fields: {missing}"
        )

    def test_all_cells_have_valid_terminal_answer_kind(self):
        invalid = []
        for domain, v_class, tmpl in all_cells():
            kind = getattr(tmpl, "terminal_answer_kind", None)
            if kind not in VALID_TERMINAL_KINDS:
                invalid.append(f"{domain}/{v_class}: {kind!r}")
        assert not invalid, (
            f"Templates with invalid terminal_answer_kind: {invalid}"
        )

    def test_no_cell_uses_process_only_kinds(self):
        """camera_pose_only and trace_only must NOT be used as terminal_answer_kind.

        V1 uses 'numeric' (target_visibility_score) and V5 uses 'ordered_steps'.
        """
        violators = []
        for domain, v_class, tmpl in all_cells():
            kind = getattr(tmpl, "terminal_answer_kind", None)
            if kind in PROCESS_ONLY_KINDS:
                violators.append(f"{domain}/{v_class}: {kind!r}")
        assert not violators, (
            f"Templates using forbidden process-only terminal_answer_kind: {violators}"
        )


# ---------------------------------------------------------------------------
# Suite 2 – spot-checks on specific cells
# ---------------------------------------------------------------------------


class TestSpotChecks:
    """Spot-check three specific cells for exact kind+fields values."""

    def test_biology_v1_numeric_target_visibility(self):
        tmpl = TEMPLATES["biology"]["V1"]
        assert tmpl.terminal_answer_kind == "numeric"
        assert tmpl.terminal_answer_fields == ("target_visibility_score",)

    def test_robotics_v3_mask_annotations_joint_axis(self):
        tmpl = TEMPLATES["robotics"]["V3"]
        assert tmpl.terminal_answer_kind == "mask"
        assert tmpl.terminal_answer_fields == ("annotations", "joint_axis_label")

    def test_medical_v6_categorical_component_category(self):
        tmpl = TEMPLATES["medical"]["V6"]
        assert tmpl.terminal_answer_kind == "categorical"
        assert tmpl.terminal_answer_fields == ("component_category",)


# ---------------------------------------------------------------------------
# Suite 3 – template_to_dict output contract
# ---------------------------------------------------------------------------


class TestTemplateToDictOutputContract:
    """template_to_dict must emit terminal_answer and active_vision_evidence."""

    @pytest.fixture(params=list(all_cells()), ids=lambda x: f"{x[0]}/{x[1]}")
    def cell_dict(self, request):
        domain, v_class, tmpl = request.param
        return domain, v_class, template_to_dict(tmpl), tmpl

    def test_terminal_answer_present(self, cell_dict):
        _, _, d, _ = cell_dict
        assert "terminal_answer" in d, "terminal_answer key missing from template_to_dict output"

    def test_terminal_answer_has_kind(self, cell_dict):
        _, _, d, tmpl = cell_dict
        ta = d["terminal_answer"]
        assert ta.get("kind") == tmpl.terminal_answer_kind

    def test_terminal_answer_has_fields(self, cell_dict):
        _, _, d, tmpl = cell_dict
        ta = d["terminal_answer"]
        assert ta.get("fields") == list(tmpl.terminal_answer_fields)

    def test_terminal_answer_has_must_be_derived_from_3d_interaction(self, cell_dict):
        _, _, d, _ = cell_dict
        ta = d["terminal_answer"]
        assert ta.get("must_be_derived_from_3d_interaction") is True

    def test_terminal_answer_has_not_allowed_as_answer(self, cell_dict):
        _, _, d, _ = cell_dict
        ta = d["terminal_answer"]
        not_allowed = ta.get("not_allowed_as_answer", [])
        assert isinstance(not_allowed, list)
        assert "camera_pose_only" in not_allowed
        assert "trace_only" in not_allowed
        assert "metadata_lookup" in not_allowed

    def test_active_vision_evidence_present(self, cell_dict):
        _, _, d, _ = cell_dict
        assert "active_vision_evidence" in d

    def test_active_vision_evidence_required_mirrors_required_evidence(self, cell_dict):
        _, _, d, tmpl = cell_dict
        ave = d["active_vision_evidence"]
        assert ave.get("required") == list(tmpl.required_evidence)

    def test_active_vision_evidence_has_optional(self, cell_dict):
        _, _, d, _ = cell_dict
        ave = d["active_vision_evidence"]
        assert "intermediate_screenshots" in ave.get("optional", [])

    def test_active_vision_evidence_role_set(self, cell_dict):
        _, _, d, _ = cell_dict
        ave = d["active_vision_evidence"]
        assert ave.get("role") == "audit_and_secondary_scoring"
