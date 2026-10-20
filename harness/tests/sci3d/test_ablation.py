import pytest
from sci3d_bench.ablation import compute_deltas, AblationCell

@pytest.mark.unit
def test_deltas_simple():
    rows = [
        {"task_id": "t1", "v_class": "V2", "mode": "mode1_no_vision", "score": 0.2, "violation": False},
        {"task_id": "t1", "v_class": "V2", "mode": "mode2_one_image", "score": 0.5, "violation": False},
        {"task_id": "t1", "v_class": "V2", "mode": "mode3_free_visual", "score": 0.9, "violation": False},
    ]
    table = compute_deltas(rows)
    cell = table["V2"]
    assert cell.delta_image == pytest.approx(0.3)
    assert cell.delta_visual == pytest.approx(0.7)
    assert cell.delta_active == pytest.approx(0.4)
    assert cell.n == 1

@pytest.mark.unit
def test_compliant_only_excludes_violator_task_across_all_modes():
    rows = [
        {"task_id": "t1", "v_class": "V2", "mode": "mode1_no_vision", "score": 0.2, "violation": True},
        {"task_id": "t1", "v_class": "V2", "mode": "mode2_one_image", "score": 0.5, "violation": False},
        {"task_id": "t1", "v_class": "V2", "mode": "mode3_free_visual", "score": 0.9, "violation": False},
        {"task_id": "t2", "v_class": "V2", "mode": "mode1_no_vision", "score": 0.4, "violation": False},
        {"task_id": "t2", "v_class": "V2", "mode": "mode2_one_image", "score": 0.6, "violation": False},
        {"task_id": "t2", "v_class": "V2", "mode": "mode3_free_visual", "score": 0.8, "violation": False},
    ]
    table = compute_deltas(rows)
    assert table["V2"].n == 2
    assert table["V2"].n_compliant == 1
    assert table["V2"].mode1_compliant == pytest.approx(0.4)
    assert table["V2"].mode2_compliant == pytest.approx(0.6)
