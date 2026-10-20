"""Tests for Category C inspector tools."""

from __future__ import annotations

import importlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from sci3d_agent.mcp.registry import REGISTRY
from sci3d_agent.mcp.tools import inspect as inspect_module
from sci3d_agent.mcp.tools.inspect import (
    INSPECT_CACHE_DIR,
    TOOLS,
    handle_inspect_mesh,
    handle_inspect_molecule,
    handle_inspect_nifti,
    handle_inspect_nifti_mask_pair,
    handle_inspect_pointcloud,
    handle_inspect_scannet_scene,
    handle_inspect_urdf,
    handle_inspect_volume_npy,
)

REPO_ROOT = Path("/Users/sue/Research/ScienceVision")
M3D_CT = REPO_ROOT / "datasets/M3D-RefSeg/M3D_RefSeg/s0212/ct.nii.gz"
M3D_MASK = REPO_ROOT / "datasets/M3D-RefSeg/M3D_RefSeg/s0212/mask.nii.gz"
SCANNET_PLY = (
    REPO_ROOT
    / "datasets/_shared_3d/scannet/scans_test/scene0707_00/scene0707_00_vh_clean_2.ply"
)
SCANQA_MATCHED = REPO_ROOT / "datasets/_matched/scanqa.json"


_HAS_TRIMESH = importlib.util.find_spec("trimesh") is not None
_HAS_NIBABEL = importlib.util.find_spec("nibabel") is not None
_HAS_LASPY = importlib.util.find_spec("laspy") is not None
_HAS_GEMMI = importlib.util.find_spec("gemmi") is not None
_HAS_YOURDFPY = importlib.util.find_spec("yourdfpy") is not None


@pytest.fixture(autouse=True)
def _isolated_registry():
    snapshot = dict(REGISTRY)
    REGISTRY.clear()
    REGISTRY.update(snapshot)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


@pytest.fixture(autouse=True)
def _clean_inspect_cache(tmp_path, monkeypatch):
    """Redirect inspect cache to tmp so tests cannot stale-read across runs."""
    fresh = tmp_path / "inspect_cache"
    monkeypatch.setattr(inspect_module, "INSPECT_CACHE_DIR", fresh)
    yield


# ----- registration ----------------------------------------------------------


def test_eight_tools_registered():
    assert len(TOOLS) == 8
    names = {t.name for t in TOOLS}
    assert names == {
        "sv_inspect_mesh",
        "sv_inspect_nifti",
        "sv_inspect_nifti_mask_pair",
        "sv_inspect_pointcloud",
        "sv_inspect_molecule",
        "sv_inspect_urdf",
        "sv_inspect_volume_npy",
        "sv_inspect_scannet_scene",
    }
    for t in TOOLS:
        assert t.category == "C"
        assert callable(t.handler)
        assert isinstance(t.input_schema, dict)


# ----- mesh ------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_TRIMESH, reason="trimesh not installed")
def test_mesh_inspector_basic(tmp_path):
    import trimesh

    box = trimesh.creation.box(extents=(2.0, 4.0, 6.0))
    mesh_path = tmp_path / "box.glb"
    box.export(str(mesh_path))
    out = handle_inspect_mesh({"path": str(mesh_path)})
    assert "error" not in out
    assert out["n_vertices"] == 8
    assert out["n_faces"] == 12
    assert out["axis_up_hint"] == "Y"
    bbox = out["bbox"]
    assert len(bbox) == 2 and len(bbox[0]) == 3


@pytest.mark.skipif(not _HAS_TRIMESH, reason="trimesh not installed")
def test_mesh_inspector_ply_axis_z(tmp_path):
    import trimesh

    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    mesh_path = tmp_path / "box.ply"
    box.export(str(mesh_path))
    out = handle_inspect_mesh({"path": str(mesh_path)})
    assert "error" not in out, f"unexpected error: {out}"
    assert out["axis_up_hint"] == "Z"
    assert out["n_faces"] == 12


def test_mesh_inspector_missing_path():
    out = handle_inspect_mesh({"path": "/no/such/file.ply"})
    assert "error" in out


def test_mesh_inspector_requires_path():
    out = handle_inspect_mesh({})
    assert "error" in out


def test_mesh_inspector_missing_trimesh(monkeypatch, tmp_path):
    p = tmp_path / "x.ply"
    p.write_bytes(b"\x00")
    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: None if name == "trimesh" else importlib.import_module(name),
    )
    out = handle_inspect_mesh({"path": str(p)})
    assert out == {"error": "dependency missing: trimesh"}


def test_mesh_inspector_load_failure(monkeypatch, tmp_path):
    p = tmp_path / "broken.ply"
    p.write_bytes(b"not-a-mesh")

    class _Mod:
        @staticmethod
        def load(_path, force=None):
            raise RuntimeError("decode fail")

    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: _Mod if name == "trimesh" else importlib.import_module(name),
    )
    out = handle_inspect_mesh({"path": str(p)})
    assert "trimesh load failed" in out["error"]


def test_mesh_inspector_non_mesh_object(monkeypatch, tmp_path):
    p = tmp_path / "scene.ply"
    p.write_bytes(b"\x00")

    class _Mod:
        @staticmethod
        def load(_path, force=None):
            return object()  # has no .vertices

    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: _Mod if name == "trimesh" else importlib.import_module(name),
    )
    out = handle_inspect_mesh({"path": str(p)})
    assert out == {"error": "trimesh returned non-mesh object"}


# ----- nifti -----------------------------------------------------------------


def _write_identity_nifti(path: Path, arr: np.ndarray) -> None:
    nib = pytest.importorskip("nibabel")
    img = nib.Nifti1Image(arr, affine=np.eye(4))
    nib.save(img, str(path))


@pytest.mark.skipif(not _HAS_NIBABEL, reason="nibabel not installed")
def test_nifti_orientation_string(tmp_path):
    arr = np.zeros((4, 4, 4), dtype=np.float32)
    path = tmp_path / "vol.nii.gz"
    _write_identity_nifti(path, arr)
    out = handle_inspect_nifti({"path": str(path)})
    assert "error" not in out
    assert out["orientation"] == "RAS"
    assert out["shape"] == [4, 4, 4]
    assert out["affine_4x4"][0][0] == 1.0


@pytest.mark.skipif(not _HAS_NIBABEL, reason="nibabel not installed")
def test_nifti_label_hist_skipped_for_intensity_volume(tmp_path):
    arr = np.zeros((4, 4, 4), dtype=np.float32)
    arr[0, 0, 0] = 5000.0  # large intensity -> label_hist should be skipped/empty
    path = tmp_path / "ct.nii.gz"
    _write_identity_nifti(path, arr)
    out = handle_inspect_nifti({"path": str(path), "include_label_hist": True})
    assert out["n_nonzero_labels"] == 0
    assert out["label_hist"] == []


@pytest.mark.skipif(not _HAS_NIBABEL, reason="nibabel not installed")
def test_nifti_label_hist_top_largest_slice(tmp_path):
    arr = np.zeros((8, 8, 8), dtype=np.int16)
    arr[1:3, 1:3, 5] = 1   # smaller label at axial=5
    arr[2:6, 2:6, 3] = 2   # larger label at axial=3
    path = tmp_path / "labels.nii.gz"
    _write_identity_nifti(path, arr)
    out = handle_inspect_nifti({"path": str(path), "include_label_hist": True})
    assert "error" not in out
    assert out["n_nonzero_labels"] == 2
    # largest label is 2, biggest axial-slice for that label is index 3
    assert out["suggested_axial_slice_for_largest_label"] == 3
    # label_hist sorted by count descending
    assert out["label_hist"][0][0] == 2
    assert out["label_hist"][0][1] == 16


@pytest.mark.skipif(not _HAS_NIBABEL, reason="nibabel not installed")
def test_nifti_missing_file_envelope():
    out = handle_inspect_nifti({"path": "/no/such/file.nii.gz"})
    assert "error" in out


def test_nifti_requires_path():
    assert "error" in handle_inspect_nifti({})


def test_nifti_missing_dep(monkeypatch, tmp_path):
    p = tmp_path / "vol.nii.gz"
    p.write_bytes(b"\x00")
    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: None if name == "nibabel" else importlib.import_module(name),
    )
    out = handle_inspect_nifti({"path": str(p)})
    assert out == {"error": "dependency missing: nibabel"}


def test_nifti_mask_pair_missing_dep(monkeypatch, tmp_path):
    p1 = tmp_path / "ct.nii.gz"
    p2 = tmp_path / "mask.nii.gz"
    p1.write_bytes(b"\x00")
    p2.write_bytes(b"\x00")
    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: None if name == "nibabel" else importlib.import_module(name),
    )
    out = handle_inspect_nifti_mask_pair(
        {"nifti_path": str(p1), "mask_path": str(p2)}
    )
    assert out == {"error": "dependency missing: nibabel"}


# ----- nifti_mask_pair (LOAD-BEARING) ----------------------------------------


@pytest.mark.skipif(not _HAS_NIBABEL, reason="nibabel not installed")
def test_nifti_mask_pair_synthetic(tmp_path):
    """Build a 16x16x16 mask with two labels at known positions and verify
    best_slices + centroid_world.

    Axis convention (this is the locked one for Phase 2 D / Phase 3 F):
      sagittal = axis 0, coronal = axis 1, axial = axis 2.
    """
    shape = (16, 16, 16)
    base = np.zeros(shape, dtype=np.float32)
    mask = np.zeros(shape, dtype=np.int16)
    # label 1: single cube at (2,3,4)
    mask[2, 3, 4] = 1
    # label 2: 3x3x3 cube centred at (10, 10, 12)
    mask[9:12, 9:12, 11:14] = 2

    base_path = tmp_path / "ct.nii.gz"
    mask_path = tmp_path / "mask.nii.gz"
    _write_identity_nifti(base_path, base)
    _write_identity_nifti(mask_path, mask)

    out = handle_inspect_nifti_mask_pair(
        {"nifti_path": str(base_path), "mask_path": str(mask_path)}
    )
    assert "error" not in out
    assert out["shape"] == [16, 16, 16]
    assert len(out["labels"]) == 2

    by_id = {lbl["id"]: lbl for lbl in out["labels"]}
    l1 = by_id[1]
    assert l1["voxel_count"] == 1
    assert l1["centroid_voxel"] == [2.0, 3.0, 4.0]
    # identity affine → world == voxel
    assert l1["centroid_world"] == [2.0, 3.0, 4.0]
    assert l1["best_slices"] == {"sagittal": 2, "coronal": 3, "axial": 4}

    l2 = by_id[2]
    assert l2["voxel_count"] == 27
    assert l2["best_slices"]["sagittal"] in (9, 10, 11)
    assert l2["best_slices"]["coronal"] in (9, 10, 11)
    assert l2["best_slices"]["axial"] in (11, 12, 13)
    # centroid of 9..11, 9..11, 11..13 is (10,10,12)
    assert l2["centroid_voxel"] == [10.0, 10.0, 12.0]


@pytest.mark.skipif(not _HAS_NIBABEL, reason="nibabel not installed")
def test_nifti_mask_pair_shape_mismatch(tmp_path):
    base = np.zeros((4, 4, 4), dtype=np.float32)
    mask = np.zeros((4, 4, 5), dtype=np.int16)
    bp = tmp_path / "ct.nii.gz"
    mp = tmp_path / "mask.nii.gz"
    _write_identity_nifti(bp, base)
    _write_identity_nifti(mp, mask)
    out = handle_inspect_nifti_mask_pair(
        {"nifti_path": str(bp), "mask_path": str(mp)}
    )
    assert "error" in out
    assert "shape mismatch" in out["error"]


def test_nifti_mask_pair_missing_args():
    assert "error" in handle_inspect_nifti_mask_pair({})


def test_nifti_mask_pair_missing_files(tmp_path):
    out = handle_inspect_nifti_mask_pair(
        {"nifti_path": str(tmp_path / "a.nii.gz"), "mask_path": str(tmp_path / "b.nii.gz")}
    )
    assert "error" in out


@pytest.mark.skipif(
    not (M3D_CT.exists() and M3D_MASK.exists()),
    reason="M3D-RefSeg sample s0212 not present",
)
def test_nifti_mask_pair_real_m3d_refseg_payload_small():
    out = handle_inspect_nifti_mask_pair(
        {"nifti_path": str(M3D_CT), "mask_path": str(M3D_MASK)}
    )
    assert "error" not in out
    assert len(out["labels"]) >= 1
    payload_bytes = len(json.dumps(out, separators=(",", ":")))
    assert payload_bytes < 2048, f"payload {payload_bytes}B exceeds 2KB"


# ----- envelope on missing file ----------------------------------------------


def test_envelope_on_missing_file():
    out = handle_inspect_mesh({"path": "/this/does/not/exist.ply"})
    assert "error" in out
    assert "not found" in out["error"]


# ----- caching ---------------------------------------------------------------


@pytest.mark.skipif(not _HAS_TRIMESH, reason="trimesh not installed")
def test_cache_invalidates_on_mtime(tmp_path):
    import trimesh

    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    mesh_path = tmp_path / "box.ply"
    box.export(str(mesh_path))

    out1 = handle_inspect_mesh({"path": str(mesh_path)})
    assert out1["n_vertices"] == 8

    # Touch mtime forward by 2s so cache key differs.
    new_ns = mesh_path.stat().st_mtime_ns + 2_000_000_000
    os.utime(mesh_path, ns=(new_ns, new_ns))

    out2 = handle_inspect_mesh({"path": str(mesh_path)})
    assert out2["n_vertices"] == 8


@pytest.mark.skipif(not _HAS_TRIMESH, reason="trimesh not installed")
def test_cache_hit_returns_same_payload(tmp_path):
    import trimesh

    box = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    mesh_path = tmp_path / "box.ply"
    box.export(str(mesh_path))

    out1 = handle_inspect_mesh({"path": str(mesh_path)})
    out2 = handle_inspect_mesh({"path": str(mesh_path)})
    assert out1 == out2


# ----- pointcloud ------------------------------------------------------------


def test_pointcloud_requires_path():
    assert "error" in handle_inspect_pointcloud({})


def test_pointcloud_missing_file(tmp_path):
    assert "error" in handle_inspect_pointcloud({"path": str(tmp_path / "no.las")})


@pytest.mark.skipif(not _HAS_LASPY, reason="laspy not installed")
def test_pointcloud_synthetic(tmp_path):
    import laspy

    las = laspy.LasData(laspy.LasHeader(point_format=3, version="1.2"))
    n = 100
    las.x = np.linspace(0.0, 1.0, n)
    las.y = np.linspace(0.0, 2.0, n)
    las.z = np.linspace(0.0, 3.0, n)
    las.red = np.full(n, 100, dtype=np.uint16)
    las.green = np.full(n, 150, dtype=np.uint16)
    las.blue = np.full(n, 200, dtype=np.uint16)
    las.intensity = np.arange(n, dtype=np.uint16)
    p = tmp_path / "cloud.las"
    las.write(str(p))
    out = handle_inspect_pointcloud({"path": str(p)})
    assert "error" not in out
    assert out["n_points"] == n
    assert out["has_rgb"] is True
    assert out["has_intensity"] is True


def test_pointcloud_missing_dep(monkeypatch, tmp_path):
    p = tmp_path / "cloud.las"
    p.write_bytes(b"\x00")
    monkeypatch.setattr(inspect_module, "_try_import", lambda name: None if name == "laspy" else importlib.import_module(name))
    out = handle_inspect_pointcloud({"path": str(p)})
    assert out == {"error": "dependency missing: laspy"}


class _FakeLasHeader:
    def __init__(self):
        self.mins = (0.0, 1.0, 2.0)
        self.maxs = (10.0, 11.0, 12.0)


class _FakePointFormat:
    dimension_names = ("X", "Y", "Z", "red", "green", "blue", "intensity")


class _FakeLasData:
    def __init__(self):
        self.x = np.zeros(5)
        self.header = _FakeLasHeader()
        self.point_format = _FakePointFormat()
        self.intensity = np.array([0, 0, 1, 2, 3], dtype=np.uint16)


def _make_fake_laspy():
    fake = _FakeLasData()

    class _Mod:
        @staticmethod
        def read(_path):
            return fake

    return _Mod()


def test_pointcloud_with_fake_module(monkeypatch, tmp_path):
    p = tmp_path / "cloud.las"
    p.write_bytes(b"\x00")
    fake = _make_fake_laspy()
    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: fake if name == "laspy" else importlib.import_module(name),
    )
    out = handle_inspect_pointcloud({"path": str(p)})
    assert "error" not in out
    assert out["n_points"] == 5
    assert out["bbox"] == [[0.0, 1.0, 2.0], [10.0, 11.0, 12.0]]
    assert out["has_rgb"] is True
    assert out["has_intensity"] is True


def test_pointcloud_with_fake_module_load_failure(monkeypatch, tmp_path):
    p = tmp_path / "cloud.las"
    p.write_bytes(b"\x00")

    class _Mod:
        @staticmethod
        def read(_path):
            raise RuntimeError("corrupt LAS")

    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: _Mod if name == "laspy" else importlib.import_module(name),
    )
    out = handle_inspect_pointcloud({"path": str(p)})
    assert "laspy load failed" in out.get("error", "")


# ----- molecule --------------------------------------------------------------


def test_molecule_requires_path():
    assert "error" in handle_inspect_molecule({"type": "pdb"})


def test_molecule_invalid_type(tmp_path):
    p = tmp_path / "x.sdf"
    p.write_text("foo")
    out = handle_inspect_molecule({"path": str(p), "type": "xyz"})
    assert "error" in out


def test_molecule_sdf_synthetic(tmp_path):
    sdf = (
        "name\n"
        "  some\n"
        "  comment\n"
        "  3  2  0  0  0  0  0  0  0  0999 V2000\n"
        "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "    1.0000    0.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "    0.0000    1.0000    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "  1  2  1  0  0  0  0\n"
        "  1  3  1  0  0  0  0\n"
        "M  END\n"
        "$$$$\n"
    )
    p = tmp_path / "mol.sdf"
    p.write_text(sdf)
    out = handle_inspect_molecule({"path": str(p), "type": "sdf"})
    assert "error" not in out
    assert out["n_atoms"] == 3
    assert out["n_models"] == 1
    assert out["has_water"] is False


class _FakeAtom:
    pass


class _FakeResidue:
    def __init__(self, name, n_atoms=1):
        self.name = name
        self._atoms = [_FakeAtom()] * n_atoms

    def __iter__(self):
        return iter(self._atoms)


class _FakeChain:
    def __init__(self, name, residues):
        self.name = name
        self._residues = residues

    def __iter__(self):
        return iter(self._residues)


class _FakeModel:
    def __init__(self, chains):
        self._chains = chains

    def __iter__(self):
        return iter(self._chains)


class _FakeStructure:
    def __init__(self, models, resolution=2.0):
        self._models = models
        self.resolution = resolution

    def __iter__(self):
        return iter(self._models)

    def __len__(self):
        return len(self._models)


def _make_fake_gemmi(structure):
    class _Mod:
        @staticmethod
        def read_structure(_path):
            return structure

    return _Mod()


def test_molecule_with_fake_gemmi(monkeypatch, tmp_path):
    res = [
        _FakeResidue("ALA", 5),
        _FakeResidue("HOH", 1),
        _FakeResidue("ATP", 31),  # ligand
    ]
    structure = _FakeStructure(
        models=[_FakeModel(chains=[_FakeChain("A", res)])],
        resolution=1.7,
    )
    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: _make_fake_gemmi(structure) if name == "gemmi" else importlib.import_module(name),
    )
    p = tmp_path / "x.cif"
    p.write_text("data_x")
    out = handle_inspect_molecule({"path": str(p), "type": "mmcif"})
    assert "error" not in out
    assert out["n_residues"] == 3
    assert out["chains"] == ["A"]
    assert out["ligands"] == ["ATP"]
    assert out["has_water"] is True
    assert out["resolution"] == 1.7
    assert out["n_models"] == 1
    assert out["n_atoms"] == 37


def test_molecule_with_fake_gemmi_load_fail(monkeypatch, tmp_path):
    class _Mod:
        @staticmethod
        def read_structure(_path):
            raise ValueError("not a cif")

    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: _Mod if name == "gemmi" else importlib.import_module(name),
    )
    p = tmp_path / "x.cif"
    p.write_text("garbage")
    out = handle_inspect_molecule({"path": str(p), "type": "pdb"})
    assert "gemmi load failed" in out["error"]


def test_molecule_missing_dep_mmcif(monkeypatch, tmp_path):
    p = tmp_path / "x.cif"
    p.write_text("foo")
    monkeypatch.setattr(
        inspect_module, "_try_import", lambda name: None if name == "gemmi" else importlib.import_module(name)
    )
    out = handle_inspect_molecule({"path": str(p), "type": "mmcif"})
    assert out == {"error": "dependency missing: gemmi"}


# ----- urdf ------------------------------------------------------------------


def test_urdf_requires_path():
    assert "error" in handle_inspect_urdf({"format": "urdf"})


def test_urdf_invalid_format(tmp_path):
    p = tmp_path / "x.urdf"
    p.write_text("<robot></robot>")
    out = handle_inspect_urdf({"path": str(p), "format": "xacro"})
    assert "error" in out


def test_urdf_missing_dep(monkeypatch, tmp_path):
    p = tmp_path / "x.urdf"
    p.write_text("<robot/>")
    monkeypatch.setattr(
        inspect_module, "_try_import", lambda name: None if name == "yourdfpy" else importlib.import_module(name)
    )
    out = handle_inspect_urdf({"path": str(p), "format": "urdf"})
    assert out == {"error": "dependency missing: yourdfpy"}


class _FakeURDF:
    def __init__(self, links, joints, base="base"):
        self.link_map = {name: _FakeLink(name, vrs) for name, vrs in links.items()}
        self.joint_map = {jn: _FakeJoint(jt) for jn, jt in joints.items()}
        self.base_link = base


class _FakeLink:
    def __init__(self, name, visuals):
        self.name = name
        self.visuals = visuals


class _FakeVisual:
    def __init__(self, filename):
        self.geometry = _FakeGeometry(filename)


class _FakeGeometry:
    def __init__(self, filename):
        self.mesh = _FakeMesh(filename) if filename else None


class _FakeMesh:
    def __init__(self, filename):
        self.filename = filename


class _FakeJoint:
    def __init__(self, jt):
        self.type = jt


def _make_fake_urdf_module(urdf_instance):
    class _URDFCls:
        @staticmethod
        def load(_path):
            return urdf_instance

    class _Mod:
        URDF = _URDFCls

    return _Mod()


def test_urdf_with_fake_module(monkeypatch, tmp_path):
    mesh_present = tmp_path / "mesh.obj"
    mesh_present.write_text("dummy")
    fake = _FakeURDF(
        links={
            "base": [_FakeVisual(str(mesh_present))],
            "arm": [_FakeVisual("missing.obj")],
            "tip": [_FakeVisual(None)],
        },
        joints={
            "j1": "revolute",
            "j2": "prismatic",
            "j_fix": "fixed",
        },
    )
    fake_mod = _make_fake_urdf_module(fake)
    monkeypatch.setattr(
        inspect_module,
        "_try_import",
        lambda name: fake_mod if name == "yourdfpy" else importlib.import_module(name),
    )
    p = tmp_path / "robot.urdf"
    p.write_text("<robot/>")
    out = handle_inspect_urdf({"path": str(p), "format": "urdf"})
    assert "error" not in out
    assert out["n_links"] == 3
    assert out["n_joints"] == 3
    assert out["dof"] == 2  # j1 + j2; j_fix excluded
    assert out["joint_types_hist"]["fixed"] == 1
    paths = {m["link"]: m for m in out["mesh_refs"]}
    assert paths["base"]["exists"] is True
    assert paths["arm"]["exists"] is False
    assert out["root_link"] == "base"


@pytest.mark.skipif(not _HAS_YOURDFPY, reason="yourdfpy not installed")
def test_urdf_minimal(tmp_path):
    urdf_text = """<?xml version=\"1.0\"?>
<robot name=\"r\">
  <link name=\"base\"/>
  <link name=\"arm\"/>
  <joint name=\"j\" type=\"revolute\">
    <parent link=\"base\"/>
    <child link=\"arm\"/>
    <origin xyz=\"0 0 0\" rpy=\"0 0 0\"/>
    <axis xyz=\"0 0 1\"/>
    <limit lower=\"-1\" upper=\"1\" effort=\"1\" velocity=\"1\"/>
  </joint>
</robot>
"""
    p = tmp_path / "r.urdf"
    p.write_text(urdf_text)
    out = handle_inspect_urdf({"path": str(p), "format": "urdf"})
    assert "error" not in out
    assert out["n_links"] == 2
    assert out["n_joints"] == 1
    assert out["dof"] == 1


# ----- volume npy ------------------------------------------------------------


def test_volume_npy_basic(tmp_path):
    arr = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    p = tmp_path / "vol.npy"
    np.save(str(p), arr)
    out = handle_inspect_volume_npy({"path": str(p)})
    assert "error" not in out
    assert out["shape"] == [2, 3, 4]
    assert out["dtype"] == "float32"
    assert out["value_range"] == [0.0, 23.0]


def test_volume_npy_with_affine(tmp_path):
    arr = np.zeros((2, 2, 2), dtype=np.float32)
    p = tmp_path / "vol.npy"
    np.save(str(p), arr)
    affine = np.diag([2.0, 3.0, 4.0, 1.0])
    ap = tmp_path / "affine.npy"
    np.save(str(ap), affine)
    out = handle_inspect_volume_npy({"path": str(p), "affine_path": str(ap)})
    assert out["affine_4x4"][0][0] == 2.0
    assert out["spacing"] == [2.0, 3.0, 4.0]


def test_volume_npy_bad_affine_shape(tmp_path):
    arr = np.zeros((2, 2, 2), dtype=np.float32)
    p = tmp_path / "vol.npy"
    np.save(str(p), arr)
    bad = np.zeros((3, 3))
    ap = tmp_path / "affine.npy"
    np.save(str(ap), bad)
    out = handle_inspect_volume_npy({"path": str(p), "affine_path": str(ap)})
    assert "error" in out


def test_volume_npy_missing_file(tmp_path):
    out = handle_inspect_volume_npy({"path": str(tmp_path / "no.npy")})
    assert "error" in out


def test_volume_npy_missing_affine_file(tmp_path):
    arr = np.zeros((2, 2), dtype=np.float32)
    p = tmp_path / "vol.npy"
    np.save(str(p), arr)
    out = handle_inspect_volume_npy(
        {"path": str(p), "affine_path": str(tmp_path / "missing.npy")}
    )
    assert "error" in out


# ----- scannet scene ---------------------------------------------------------


def test_scannet_scene_missing_id():
    assert "error" in handle_inspect_scannet_scene({})


def test_scannet_scene_unknown_scene(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out = handle_inspect_scannet_scene({"scene_id": "scene9999_99"})
    assert "error" in out


@pytest.mark.skipif(
    not (SCANNET_PLY.exists() and SCANQA_MATCHED.exists()),
    reason="ScanNet scene0707_00 / scanqa.json not present",
)
def test_scannet_scene_joiner_finds_qa_refs():
    out = handle_inspect_scannet_scene({"scene_id": "scene0707_00"})
    assert "error" not in out
    assert out["scene_id"] == "scene0707_00"
    assert out["n_vertices"] > 0
    assert out["bbox"] is not None
    qa_refs = out["qa_refs"]
    assert isinstance(qa_refs, list) and len(qa_refs) >= 1
    by_dataset = {r["dataset"]: r for r in qa_refs}
    assert "scanqa" in by_dataset
    assert by_dataset["scanqa"]["count"] >= 1
    assert by_dataset["scanqa"]["sample_ids"]
