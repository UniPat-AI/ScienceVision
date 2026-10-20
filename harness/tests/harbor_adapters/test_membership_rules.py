"""The membership builder rule: recognising a declared mask contract, packing
the region into a rubric item, trimming the ground truth to the answer schema,
and failing loudly on broken records."""
from __future__ import annotations

import base64
import zlib
from types import SimpleNamespace

import pytest

from scripts.harbor import _membership_rules as membership

nib = pytest.importorskip("nibabel")
np = pytest.importorskip("numpy")

from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer  # noqa: E402


def _write_mask(tmp_path, *, shape=(6, 5, 4), label=2, voxels=((1, 2, 3), (4, 0, 1))):
    arr = np.zeros(shape, dtype=np.int16)
    for voxel in voxels:
        arr[voxel] = label
    path = tmp_path / "mask.nii.gz"
    nib.save(nib.Nifti1Image(arr, affine=np.eye(4)), str(path))
    return path


def _record(*, point=(1, 2, 3), with_point=True, mask_id=2, mask_path="mask.nii.gz"):
    value = {"mask_path": mask_path, "mask_id": mask_id, "volume_shape": [6, 5, 4]}
    if with_point:
        value["point_vox"] = list(point)
    return {
        "task_id": "sv-medi-probe-0001",
        "domain": "medical",
        "instruction": "Return one voxel inside the lesion.",
        "answer_schema": {"kind": "structured", "fields": ["point_vox"]},
        "ground_truth": {
            "method": "radiologist_mask_membership",
            "status": "auto_verified",
            "type": "oracle_handle",
            "value": value,
        },
        "asset": {"path": "/x/ct.nii.gz", "format": "nii.gz"},
        "evaluation_eligible": True,
    }


def _as_task(task: dict) -> SimpleNamespace:
    """The duck-type DeterministicAnswerScorer reads, from a flattened task."""
    return SimpleNamespace(
        answer_schema=SimpleNamespace(fields=task["answer_schema"]["fields"]),
        ground_truth=SimpleNamespace(value=task["ground_truth"]["value"]),
        scorer=SimpleNamespace(
            primary=task["scorer"].get("primary", ""), config=task["scorer"]
        ),
    )


def _score(task: dict, point):
    return DeterministicAnswerScorer().score(
        _as_task(task), SimpleNamespace(final_answer={"point_vox": point})
    )


@pytest.fixture()
def qa_gen_to_tasks():
    return pytest.importorskip("scripts.harbor.qa_gen_to_tasks")


def test_declares_membership_recognises_both_spellings() -> None:
    assert membership.declares_membership(
        {"method": "radiologist_mask_membership", "value": {"point_vox": [1, 2, 3]}}
    )
    assert membership.declares_membership(
        {"value": {"mask_path": "m.nii.gz", "mask_id": 1}}
    )
    assert not membership.declares_membership({"value": {"point_vox": [1, 2, 3]}})
    assert not membership.declares_membership(None)


def test_the_packed_bits_match_the_documented_layout(tmp_path) -> None:
    _write_mask(tmp_path)
    blob, shape = membership.pack_member_set(tmp_path / "mask.nii.gz", 2)
    assert shape == [6, 5, 4]
    packed = zlib.decompress(base64.b64decode(blob))
    d1, d2 = 5, 4
    for x, y, z in ((1, 2, 3), (4, 0, 1)):
        index = (x * d1 + y) * d2 + z
        assert (packed[index >> 3] >> (index & 7)) & 1 == 1
    assert packed[0] & 1 == 0  # (0, 0, 0) is background


def test_the_wiring_turns_a_declared_record_into_a_membership_task(
    tmp_path, qa_gen_to_tasks
) -> None:
    _write_mask(tmp_path)
    task = qa_gen_to_tasks.qa_record_to_task(_record(), asset_root=tmp_path)
    rubric = task["scorer"]["rubric"]
    assert len(rubric) == 1
    item = rubric[0]
    assert item["comparator"] == "voxel_member_of_set"
    assert item["pred_path"] == "point_vox" and item["gt_path"] == "point_vox"
    assert item["required"] is True
    # The ground truth is trimmed to exactly the answer schema's fields.
    assert task["ground_truth"]["value"] == {"point_vox": [1, 2, 3]}
    # And the packed region judges the point: on-region 1.0, off-region 0.0.
    assert _score(task, [1, 2, 3]).value == pytest.approx(1.0)
    assert _score(task, [0, 0, 0]).value == pytest.approx(0.0)


def test_a_record_without_the_declaration_is_left_alone() -> None:
    record = {
        "task_id": "sv-medi-probe-0002",
        "answer_schema": {"kind": "structured", "fields": ["point_vox"]},
        "ground_truth": {"value": {"point_vox": [1, 2, 3]}},
        "evaluation_eligible": True,
    }
    assert membership.apply_membership(record) is record


def test_a_declared_point_outside_the_mask_fails_loudly(tmp_path, qa_gen_to_tasks) -> None:
    _write_mask(tmp_path)
    with pytest.raises(ValueError, match="membership_point_outside_mask"):
        qa_gen_to_tasks.qa_record_to_task(_record(point=(0, 0, 0)), asset_root=tmp_path)


def test_a_missing_point_is_derived_from_the_mask(tmp_path, qa_gen_to_tasks) -> None:
    _write_mask(tmp_path)
    task = qa_gen_to_tasks.qa_record_to_task(_record(with_point=False), asset_root=tmp_path)
    # Deterministic: the first in-mask voxel in C-order.
    assert task["ground_truth"]["value"] == {"point_vox": [1, 2, 3]}


def test_an_oversized_member_set_fails_loudly(
    tmp_path, qa_gen_to_tasks, monkeypatch
) -> None:
    _write_mask(tmp_path)
    monkeypatch.setattr(membership, "MEMBER_SET_MAX_BYTES", 8)
    with pytest.raises(ValueError, match="member_set_too_large"):
        qa_gen_to_tasks.qa_record_to_task(_record(), asset_root=tmp_path)


def test_a_missing_mask_file_fails_loudly(tmp_path, qa_gen_to_tasks) -> None:
    with pytest.raises(ValueError, match="membership_mask_unreadable"):
        qa_gen_to_tasks.qa_record_to_task(
            _record(mask_path="nope.nii.gz"), asset_root=tmp_path
        )
