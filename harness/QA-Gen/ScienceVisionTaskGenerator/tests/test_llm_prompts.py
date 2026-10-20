"""Tests for sciencevision_taskgen.llm.prompts."""
from __future__ import annotations

import pytest

from sciencevision_taskgen.llm.prompts import (
    build_builder_messages,
    build_challenger_messages,
)


# ---------------------------------------------------------------------------
# build_builder_messages
# ---------------------------------------------------------------------------


class TestBuildBuilderMessages:
    def _call(
        self,
        asset_name: str = "mito_sample.nii.gz",
        template: str = "Measure the longest axis of the structure.",
    ) -> tuple[str, str]:
        task = {"domain": "biology", "v_class": "V3"}
        asset = {"name": asset_name, "format": "NIfTI"}
        return build_builder_messages(task, asset, template)

    def test_returns_two_strings(self) -> None:
        system, user = self._call()
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_is_nonempty(self) -> None:
        system, _ = self._call()
        assert len(system) > 0

    def test_user_contains_asset_name(self) -> None:
        _, user = self._call(asset_name="special_asset_xyz.nii")
        assert "special_asset_xyz.nii" in user

    def test_user_contains_template_instruction(self) -> None:
        template = "Count the number of distinct layers visible."
        _, user = self._call(template=template)
        assert template in user

    def test_user_contains_domain(self) -> None:
        task = {"domain": "astronomy", "v_class": "V1"}
        asset = {"name": "galaxy_cube.fits", "format": "FITS"}
        _, user = build_builder_messages(task, asset, "Identify the spiral arms.")
        assert "astronomy" in user

    def test_system_mentions_json(self) -> None:
        system, _ = self._call()
        assert "JSON" in system or "json" in system

    def test_system_mentions_instruction_key(self) -> None:
        system, _ = self._call()
        assert "instruction" in system

    def test_user_nonempty(self) -> None:
        _, user = self._call()
        assert len(user) > 0


# ---------------------------------------------------------------------------
# build_challenger_messages
# ---------------------------------------------------------------------------


class TestBuildChallengerMessages:
    def _sample_task(self) -> dict:
        return {
            "instruction": "Identify the primary crack propagation path.",
            "scientific_action": "crack_path_tracing",
            "asset": "fatigue_sample_A.vtk",
            "v_class": "V4",
            "terminal_answer": "branching crack at 45 degrees",
            "quality_gates": {"min_confidence": 0.8},
        }

    def test_returns_two_strings(self) -> None:
        system, user = build_challenger_messages(self._sample_task())
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_is_nonempty(self) -> None:
        system, _ = build_challenger_messages(self._sample_task())
        assert len(system) > 0

    def test_user_is_nonempty(self) -> None:
        _, user = build_challenger_messages(self._sample_task())
        assert len(user) > 0

    def test_user_contains_instruction(self) -> None:
        task = self._sample_task()
        _, user = build_challenger_messages(task)
        assert task["instruction"] in user

    def test_user_contains_v_class(self) -> None:
        _, user = build_challenger_messages(self._sample_task())
        assert "V4" in user

    def test_user_contains_asset(self) -> None:
        _, user = build_challenger_messages(self._sample_task())
        assert "fatigue_sample_A.vtk" in user

    def test_system_mentions_severity(self) -> None:
        system, _ = build_challenger_messages(self._sample_task())
        assert "severity" in system

    def test_system_mentions_json(self) -> None:
        system, _ = build_challenger_messages(self._sample_task())
        assert "JSON" in system or "json" in system

    def test_empty_task_does_not_crash(self) -> None:
        """Edge case: all fields missing — should still return two strings."""
        system, user = build_challenger_messages({})
        assert isinstance(system, str)
        assert isinstance(user, str)
