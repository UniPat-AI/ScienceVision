"""The ``voxel_member_of_set`` comparator: a voxel answer judged against a
packed ground-truth region.

The region rides in the rubric item as bit-packed occupancy
(``base64(zlib(...))``, C-order, least significant bit first) because a
comparator is a pure function over the item — it cannot open a NIfTI. These
tests hand-pack the bytes with the standard library, so the wire format is
pinned independently of the builder-side packer.
"""
from __future__ import annotations

import base64
import zlib
from types import SimpleNamespace

import pytest

from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer


def _pack_voxels(shape: tuple[int, int, int], voxels) -> bytes:
    d0, d1, d2 = shape
    packed = bytearray((d0 * d1 * d2 + 7) // 8)
    for x, y, z in voxels:
        index = (x * d1 + y) * d2 + z
        packed[index >> 3] |= 1 << (index & 7)
    return bytes(packed)


def _member_item(
    shape: tuple[int, int, int] = (4, 4, 4),
    voxels=((0, 0, 0), (1, 2, 3)),
    field: str = "point_vox",
    **overrides,
) -> dict:
    item = {
        "id": field,
        "pred_path": field,
        "gt_path": field,
        "comparator": "voxel_member_of_set",
        "member_shape": list(shape),
        "member_set": base64.b64encode(
            zlib.compress(_pack_voxels(shape, voxels))
        ).decode("ascii"),
        "weight": 1.0,
        "required": True,
    }
    item.update(overrides)
    return item


def _task(item: dict, field: str = "point_vox", point=(0, 0, 0)) -> SimpleNamespace:
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=[field]),
        ground_truth=SimpleNamespace(value={field: list(point)}),
        scorer=SimpleNamespace(
            primary="deterministic_structured_v2_1",
            config={
                "primary": "deterministic_structured_v2_1",
                "rubric_version": "sciencevision.deterministic_structured.v2.1",
                "rubric": [item],
            },
        ),
    )


def _score(item: dict, answer, field: str = "point_vox"):
    return DeterministicAnswerScorer().score(
        _task(item, field=field), SimpleNamespace(final_answer=answer)
    )


def test_a_voxel_on_the_region_scores_one() -> None:
    result = _score(_member_item(), {"point_vox": [1, 2, 3]})
    assert result.value == pytest.approx(1.0)
    assert result.raw["verdicts"][0]["comparator"] == "voxel_member_of_set"
    assert result.raw["verdicts"][0]["passed"] is True


def test_a_voxel_off_the_region_scores_zero() -> None:
    result = _score(_member_item(), {"point_vox": [3, 3, 3]})
    assert result.value == pytest.approx(0.0)
    assert result.raw["verdicts"][0]["reason"] == "comparison_failed"


def test_out_of_bounds_is_a_wrong_answer_not_a_contract_fault() -> None:
    result = _score(_member_item(), {"point_vox": [9, 9, 9]})
    assert result.value == pytest.approx(0.0)
    assert result.raw["eligible"] is True


def test_integral_floats_name_their_voxel_and_others_do_not() -> None:
    assert _score(_member_item(), {"point_vox": [1.0, 2.0, 3.0]}).value == pytest.approx(1.0)
    assert _score(_member_item(), {"point_vox": [1.4, 2.0, 3.0]}).value == pytest.approx(0.0)


def test_booleans_and_malformed_points_are_rejected() -> None:
    assert _score(_member_item(), {"point_vox": [True, 2, 3]}).value == pytest.approx(0.0)
    assert _score(_member_item(), {"point_vox": "1,2,3"}).value == pytest.approx(0.0)
    assert _score(_member_item(), {"point_vox": [1, 2]}).value == pytest.approx(0.0)


def test_a_broken_member_set_is_a_contract_error_not_a_quiet_zero() -> None:
    broken = (
        _member_item(member_set="not base64 !!"),
        _member_item(member_shape=[4, 4]),
        _member_item(member_shape=[4, 4, 5]),  # blob length disagrees with shape
        _member_item(member_set=""),
    )
    for item in broken:
        result = _score(item, {"point_vox": [0, 0, 0]})
        assert result.value == pytest.approx(0.0)
        assert result.raw["eligible"] is False
        assert result.raw["reason"] == "invalid_task_contract"
        assert any(
            str(entry).startswith("invalid_member_set:") for entry in result.raw["errors"]
        )
