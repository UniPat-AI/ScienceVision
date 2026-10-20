"""The QC text probe receives question prose and no task assets."""
from __future__ import annotations

from scripts.harbor import build_harbor_tasks as gen


def test_text_probe_strips_asset_plumbing_and_stages_no_files(tmp_path):
    asset = tmp_path / "3672_ibuprofen.mol"
    asset.write_text("molecule data")
    task = {
        "task_id": "chem-text-probe",
        "instruction": (
            "Assign each chiral center.\n\nAsset: /data/3672_ibuprofen.mol\n\n"
            '```sci3d\n{"asset_path": "/data/3672_ibuprofen.mol"}\n```'
        ),
        "asset_path": str(asset),
        "asset_format": "mol",
        "answer_schema": {"kind": "freeform"},
    }

    task_dir = gen.build_one(
        task, tmp_path / "out", containerized=True, agent_env="claude-code-text"
    )

    instruction = (task_dir / "instruction.md").read_text()
    dockerfile = (task_dir / "environment" / "Dockerfile").read_text()
    assert instruction.startswith("Assign each chiral center.\n")
    assert "SCI3D_FINAL_JSON:" in instruction
    assert "3672_ibuprofen" not in instruction
    assert "COPY" not in dockerfile
    assert "sci3d-claude-code-2d:latest" in dockerfile
    assert not (task_dir / "environment" / "asset").exists()
    assert not (task_dir / "environment" / "views").exists()
