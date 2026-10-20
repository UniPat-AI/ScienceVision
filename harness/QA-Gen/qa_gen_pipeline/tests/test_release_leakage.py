from __future__ import annotations

import json
from pathlib import Path

import pytest

from qagen.release.leakage_check import SolverBundleLeakageError, assert_solver_bundle_safe


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _clean_solver_bundle(root: Path) -> Path:
    solver = root / "solver"
    _write_json(
        solver / "task.json",
        {
            "task_id": "qagen-bio-001",
            "instruction": "Use the viewer to identify the visible contact.",
            "answer_schema": {"kind": "short_text"},
        },
    )
    _write_json(solver / "scene-ref.json", {"scene_ref": "scene-f62d8b"})
    _write_json(
        solver / "viewer-session-request.json",
        {
            "session_request_id": "request-4a2c",
            "allowed_operations": ["orbit", "select"],
            "expires_in_seconds": 300,
        },
    )
    _write_json(solver / "capability-profile.json", {"measurement": False})
    return solver


def test_clean_solver_bundle_contains_only_solver_safe_data(tmp_path: Path) -> None:
    solver = _clean_solver_bundle(tmp_path)

    assert_solver_bundle_safe(solver)


@pytest.mark.parametrize(
    "leak_field,leak_value",
    [
        ("reference_answer", "Tyr42"),
        ("ground_truth", {"answer": "Tyr42"}),
        ("asset_id", "real-private-asset-id-001"),
        ("asset_path", "/private/assets/ligand_001.pdb"),
        ("seed_card", "seed-opaque-card-1"),
        ("token", "secret-session-token"),
        ("knowledge", {"locked_ref": "internal-only"}),
    ],
)
def test_solver_bundle_rejects_sensitive_fields(
    tmp_path: Path, leak_field: str, leak_value: object
) -> None:
    solver = _clean_solver_bundle(tmp_path)
    task_path = solver / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task[leak_field] = leak_value
    _write_json(task_path, task)

    with pytest.raises(SolverBundleLeakageError, match=leak_field):
        assert_solver_bundle_safe(solver)


def test_solver_bundle_rejects_private_artifact_by_filename(tmp_path: Path) -> None:
    solver = _clean_solver_bundle(tmp_path)
    _write_json(solver / "author-truth.json", {"receipt": "private"})

    with pytest.raises(SolverBundleLeakageError):
        assert_solver_bundle_safe(solver)
