"""Tests for terminal_answer and related new fields in sciencevision_task.schema.json.

TDD: these tests are written BEFORE the schema is updated. They will fail (RED)
until the schema changes are applied (GREEN).
"""

import copy
from typing import Any

import pytest
from jsonschema import Draft7Validator


# ---------------------------------------------------------------------------
# Minimal valid base task — only the existing required fields.
# ---------------------------------------------------------------------------

BASE_TASK: dict[str, Any] = {
    "schema_version": "1.0.0",
    "task_id": "test-task-001",
    "title": "Identify the anomaly in the scan",
    "domain": "medical",
    "v_class": "V2",
    "difficulty": "medium",
    "asset": {
        "asset_id": "scan-001",
        "name": "brain_scan",
        "path": "assets/brain_scan.nii",
        "viewer": "nifti_viewer",
        "format": "nifti",
    },
    "instruction": "Inspect the 3D scan and identify the location of the anomaly.",
    "scientific_action": "identify_anomaly",
    "required_evidence": ["screenshot_annotated"],
    "answer_schema": {
        "kind": "bbox",
        "fields": ["x", "y", "z", "w", "h", "d"],
    },
    "ground_truth": {
        "status": "draft",
        "type": "bbox",
    },
    "scorer": {
        "primary": "iou_3d",
        "checks": ["format_check", "coordinate_range_check"],
    },
    "tool_policy": {
        "allowed_tools": ["nifti_viewer", "slice_tool"],
        "forbidden_tools": ["calculator"],
    },
    "trace_requirements": {
        "must_save_operation_trace": True,
        "must_include_final_state": True,
        "max_steps": None,
    },
    "quality_gates": {
        "irreducible_3d": True,
        "requires_viewer": True,
        "shortcut_risk": "low",
    },
    "provenance": {"source": "manual", "author": "test"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_valid(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    """Raise AssertionError with validation message if instance is invalid."""
    errors = list(Draft7Validator(schema).iter_errors(instance))
    assert not errors, "Expected valid, but got errors:\n" + "\n".join(
        str(e) for e in errors
    )


def assert_invalid(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    """Raise AssertionError if instance unexpectedly passes validation."""
    errors = list(Draft7Validator(schema).iter_errors(instance))
    assert errors, "Expected validation failure, but instance passed."


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing tasks that have none of the 5 new fields must still validate."""

    def test_base_task_without_new_fields_passes(self, schema: dict[str, Any]) -> None:
        assert_valid(BASE_TASK, schema)

    def test_chemistry_domain_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["domain"] = "chemistry"
        assert_valid(task, schema)


class TestFullTaskWithAllNewFields:
    """A task with all 5 new fields and a normal kind must pass."""

    def test_full_task_with_all_new_fields_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task.update(
            {
                "terminal_answer": {
                    "kind": "binary",
                    "fields": ["answer"],
                    "unit": None,
                    "format": "yes/no",
                    "must_be_derived_from_3d_interaction": True,
                    "not_allowed_as_answer": ["process_description"],
                },
                "active_vision_evidence": {
                    "required": ["screenshot_annotated"],
                    "optional": ["operation_trace"],
                    "role": "grounding",
                    "storage": {
                        "trace_path": "traces/task-001.json",
                        "screenshot_path": "screenshots/task-001.png",
                    },
                },
                "validation_record": {
                    "auto_checks": {"format": "pass", "range": "pass"},
                    "expert_checks": {"domain_expert": "pending"},
                    "loop": {
                        "iteration": 1,
                        "max_iterations": 3,
                        "status": "in_progress",
                    },
                    "release_gate": "pending",
                },
                "asset_grounding": {
                    "status": "pass",
                    "verified_entities": ["tumor_region"],
                    "missing_or_ambiguous_entities": [],
                    "notes": "All entities confirmed.",
                },
                "expert_review_requirements": {
                    "roles": ["radiologist", "3d_specialist"],
                    "min_approvals": 2,
                    "review_ids": ["rev-001", "rev-002"],
                },
            }
        )
        assert_valid(task, schema)


class TestProcessAnswerConstraint:
    """camera_pose_only / trace_only terminal_answer kinds require v_class V1 or V5."""

    def test_camera_pose_only_with_v2_fails(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["v_class"] = "V2"
        task["terminal_answer"] = {
            "kind": "camera_pose_only",
            "fields": ["camera_matrix"],
        }
        assert_invalid(task, schema)

    def test_camera_pose_only_with_v1_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["v_class"] = "V1"
        task["terminal_answer"] = {
            "kind": "camera_pose_only",
            "fields": ["camera_matrix"],
        }
        assert_valid(task, schema)

    def test_trace_only_with_v3_fails(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["v_class"] = "V3"
        task["terminal_answer"] = {
            "kind": "trace_only",
            "fields": ["operation_trace"],
        }
        assert_invalid(task, schema)

    def test_trace_only_with_v5_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["v_class"] = "V5"
        task["terminal_answer"] = {
            "kind": "trace_only",
            "fields": ["operation_trace"],
        }
        assert_valid(task, schema)

    def test_metadata_lookup_with_v2_fails(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["v_class"] = "V2"
        task["terminal_answer"] = {
            "kind": "metadata_lookup",
            "fields": ["metadata_field"],
        }
        assert_invalid(task, schema)

    def test_metadata_lookup_with_v1_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["v_class"] = "V1"
        task["terminal_answer"] = {
            "kind": "metadata_lookup",
            "fields": ["metadata_field"],
        }
        assert_valid(task, schema)


class TestTerminalAnswerRequired:
    """terminal_answer itself is optional but if present, kind and fields are required."""

    def test_terminal_answer_missing_fields_fails(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        # kind present but fields missing
        task["terminal_answer"] = {
            "kind": "numeric",
            # "fields" deliberately omitted
        }
        assert_invalid(task, schema)

    def test_terminal_answer_missing_kind_fails(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        # fields present but kind missing
        task["terminal_answer"] = {
            "fields": ["value"],
        }
        assert_invalid(task, schema)

    def test_terminal_answer_with_both_required_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["terminal_answer"] = {
            "kind": "numeric",
            "fields": ["value"],
        }
        assert_valid(task, schema)

    def test_structured_terminal_answer_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["answer_schema"] = {
            "kind": "structured",
            "fields": ["spacegroup_hm", "z"],
        }
        task["terminal_answer"] = {
            "kind": "structured",
            "fields": ["spacegroup_hm", "z"],
        }
        assert_valid(task, schema)

    def test_invalid_terminal_answer_kind_fails(self, schema: dict[str, Any]) -> None:
        """An unrecognised kind value must fail validation."""
        task = copy.deepcopy(BASE_TASK)
        task["terminal_answer"] = {
            "kind": "process_description",  # not in the enum
            "fields": ["description"],
        }
        assert_invalid(task, schema)


class TestAssetGrounding:
    """asset_grounding must accept only supported generated and review states."""

    def test_invalid_status_fails(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["asset_grounding"] = {
            "status": "unknown",  # not in enum
            "verified_entities": [],
            "missing_or_ambiguous_entities": [],
        }
        assert_invalid(task, schema)

    def test_valid_status_passes(self, schema: dict[str, Any]) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["asset_grounding"] = {
            "status": "pass",
            "verified_entities": ["entity_a"],
            "missing_or_ambiguous_entities": [],
        }
        assert_valid(task, schema)

    @pytest.mark.parametrize("status", ["grounded_from_asset", "grounded_from_meta"])
    def test_generated_grounding_object_passes(
        self, schema: dict[str, Any], status: str
    ) -> None:
        task = copy.deepcopy(BASE_TASK)
        task["asset_grounding"] = {
            "status": status,
            "verified_entities": {"extent_vox": [20, 37, 25]},
            "missing_or_ambiguous_entities": [],
        }
        assert_valid(task, schema)
