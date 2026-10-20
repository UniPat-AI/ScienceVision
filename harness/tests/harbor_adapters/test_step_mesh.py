from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.harbor import step_mesh  # noqa: E402


def test_is_step_matches_the_corpus_suffixes():
    assert step_mesh.is_step("a.step") and step_mesh.is_step("b.STP")
    assert not step_mesh.is_step("c.iges") and not step_mesh.is_step("d.obj")


def test_tessellation_knobs_are_the_mirrored_values():
    # The four BRepMesh_IncrementalMesh arguments, equal to tools/thumbs/cad.py's
    # defaults in ScienceVision; changing one is a decision, not a refactor.
    assert step_mesh.STEP_MESH_LINEAR_DEFLECTION == 0.5
    assert step_mesh.STEP_MESH_ANGULAR_DEFLECTION == 0.5
    assert step_mesh.STEP_MESH_RELATIVE is False
    assert step_mesh.STEP_MESH_PARALLEL is True
    assert step_mesh.STEP_MESH_TOOL == "sv_render_mesh_thumbnail"
    assert step_mesh.STEP_TESSELLATED_NAME.endswith(".ply")  # Z-up: never .glb


def test_scratch_dir_lives_outside_the_task_tree(tmp_path):
    with step_mesh.scratch_dir() as scratch:
        assert scratch.is_dir()
        assert tmp_path not in scratch.parents
        created = scratch
    assert not created.exists()


def test_rewrite_plan_passthrough_when_nothing_is_cad():
    plan = step_mesh.rewrite_plan(
        [("sv_render_nifti_slice", {"path": "/h/a.nii.gz", "axis": "axial"})],
        scratch=Path("."),
    )
    assert plan.provenance is None
    assert plan.calls[0][1]["path"] == "/h/a.nii.gz"


def test_iges_fails_unsupported_on_the_mesh_tool(tmp_path):
    with pytest.raises(step_mesh.StepMeshError) as exc:
        step_mesh.rewrite_plan(
            [("sv_render_mesh_thumbnail", {"path": "/h/a.iges", "view": "iso"})],
            scratch=tmp_path,
        )
    assert exc.value.code == "step_format_unsupported"


def test_step_on_a_foreign_tool_fails_unroutable(tmp_path):
    with pytest.raises(step_mesh.StepMeshError) as exc:
        step_mesh.rewrite_plan(
            [("sv_render_molecule_thumbnail", {"path": "/h/a.step"})],
            scratch=tmp_path,
        )
    assert exc.value.code == "step_plan_unroutable"


@pytest.mark.skipif(not step_mesh.ocp_missing(), reason="OCP present: real tessellation owns this")
def test_step_without_ocp_fails_closed_with_its_own_code(tmp_path):
    src = tmp_path / "part.step"
    src.write_text("ISO-10303-21;")
    with pytest.raises(step_mesh.StepMeshError) as exc:
        step_mesh.rewrite_plan(
            [("sv_render_mesh_thumbnail", {"path": str(src), "view": "iso"})],
            scratch=tmp_path,
        )
    assert exc.value.code == "ocp_unavailable"


class _FakeMesh:
    def __init__(self) -> None:
        import numpy as np

        self.vertices = np.zeros((3, 3))
        self.faces = np.zeros((1, 3), dtype=int)

    def export(self, file_type: str) -> bytes:
        assert file_type == "ply"
        return b"ply\nfake mesh\n"


def test_tessellate_to_writes_the_mesh_and_describes_it(tmp_path, monkeypatch):
    src = tmp_path / "part.step"
    src.write_text("ISO-10303-21;")
    monkeypatch.setattr(step_mesh, "read_shape", lambda path: object())
    monkeypatch.setattr(step_mesh, "shape_to_mesh", lambda shape: _FakeMesh())

    dest = tmp_path / "tessellated_00_00.ply"
    row = step_mesh.tessellate_to(str(src), dest)
    assert dest.read_bytes() == b"ply\nfake mesh\n"
    assert row["source_format"] == "step"
    assert row["mesh_format"] == "ply"
    assert row["vertex_count"] == 3
    assert row["face_count"] == 1
    assert len(row["mesh_sha256"]) == 64
    assert "path" not in row and "source" not in row  # no paths in the manifest


def test_rewrite_plan_tessellates_each_distinct_source_once(tmp_path, monkeypatch):
    src = tmp_path / "part.step"
    src.write_text("ISO-10303-21;")
    calls_seen: list[str] = []

    def fake_tessellate_to(source, dest):
        calls_seen.append(str(source))
        Path(dest).write_bytes(b"ply\nx\n")
        return {"source_format": "step", "mesh_format": "ply", "vertex_count": 3,
                "face_count": 1, "mesh_sha256": "0" * 64}

    monkeypatch.setattr(step_mesh, "tessellate_to", fake_tessellate_to)
    plan = step_mesh.rewrite_plan(
        [("sv_render_mesh_thumbnail", {"path": str(src), "view": "iso"}),
         ("sv_render_mesh_thumbnail", {"path": str(src), "view": "front"})],
        scratch=tmp_path,
    )
    assert calls_seen == [str(src)]
    assert [a["path"] for _, a in plan.calls] == [plan.calls[0][1]["path"]] * 2
    assert plan.calls[0][1]["path"].endswith("tessellated_00_00.ply")
    assert plan.calls[0][1]["view"] == "iso" and plan.calls[1][1]["view"] == "front"
    assert plan.provenance["kernel"] == "OCP"
    assert len(plan.provenance["meshes"]) == 1
    assert plan.provenance["linear_deflection"] == 0.5
