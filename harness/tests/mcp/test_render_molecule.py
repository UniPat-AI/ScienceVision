"""sv_render_molecule_thumbnail: adaptive molecular structure renderer.

Locks (a) .xyz parses with no extra dependency and small molecules render
ball-and-stick, (b) large structures switch to CPK spheres + downsample,
(c) mmcif/cif parse via gemmi, (d) the tool is registered in the MCP registry.
The renderer only needs matplotlib (always present); gemmi tests importorskip.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sci3d_agent.mcp.tools._render import molecule as M  # noqa: E402

_WATER_XYZ = """3
water
O   0.000   0.000   0.000
H   0.758   0.586   0.000
H  -0.758   0.586   0.000
"""


def _write_xyz(tmp_path: Path, n_atoms: int) -> Path:
    """Write an n-atom carbon lattice .xyz so representation tiers can be forced."""
    lines = [str(n_atoms), "grid"]
    side = int(round(n_atoms ** (1 / 3))) + 1
    written = 0
    for x in range(side):
        for y in range(side):
            for z in range(side):
                if written >= n_atoms:
                    break
                lines.append(f"C {x*2.0:.3f} {y*2.0:.3f} {z*2.0:.3f}")
                written += 1
    p = tmp_path / f"grid_{n_atoms}.xyz"
    p.write_text("\n".join(lines) + "\n")
    return p


def _assert_image(env: dict) -> None:
    assert "error" not in env, env
    assert env.get("content_type") == "image/png"
    # Either inlined base64 or an image_url, plus a cache_path on disk.
    assert env.get("image_png_base64") or env.get("image_url")
    assert Path(env["cache_path"]).exists()


@pytest.mark.unit
def test_small_xyz_renders_ball_and_stick(tmp_path):
    p = tmp_path / "water.xyz"
    p.write_text(_WATER_XYZ)
    env = M.handle_molecule_thumbnail({"path": str(p), "azimuth": 30, "size": 256})
    _assert_image(env)
    assert env["representation"] == "ball-and-stick"
    assert env["n_atoms"] == 3
    assert env["n_atoms_rendered"] == 3


@pytest.mark.unit
def test_large_structure_switches_to_cpk(tmp_path):
    p = _write_xyz(tmp_path, 600)
    env = M.handle_molecule_thumbnail({"path": str(p), "size": 256})
    _assert_image(env)
    assert env["representation"] == "cpk"
    assert env["n_atoms"] == 600


@pytest.mark.unit
def test_huge_structure_downsamples(tmp_path):
    p = _write_xyz(tmp_path, M._CPK_MAX_ATOMS + 1500)
    env = M.handle_molecule_thumbnail({"path": str(p), "size": 200})
    _assert_image(env)
    assert env["representation"] == "cpk"
    assert env["n_atoms"] == M._CPK_MAX_ATOMS + 1500
    assert env["n_atoms_rendered"] == M._CPK_MAX_ATOMS


@pytest.mark.unit
def test_missing_file_returns_error():
    env = M.handle_molecule_thumbnail({"path": "/nope/does_not_exist.xyz"})
    assert "error" in env


@pytest.mark.unit
def test_bad_azimuth_returns_error(tmp_path):
    p = tmp_path / "water.xyz"
    p.write_text(_WATER_XYZ)
    env = M.handle_molecule_thumbnail({"path": str(p), "azimuth": "spin"})
    assert "error" in env


@pytest.mark.unit
def test_crystallographic_cif_fractional_coords(tmp_path):
    """COD-style CIF: bare _atom_site loop with FRACTIONAL coords + cell.
    read_structure returns empty for these, so the cif.read fallback must
    orthogonalize the fractional coords."""
    pytest.importorskip("gemmi")
    cif = """data_test
_cell_length_a 10.0
_cell_length_b 10.0
_cell_length_c 10.0
_cell_angle_alpha 90.0
_cell_angle_beta 90.0
_cell_angle_gamma 90.0
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
C1 C 0.100(2) 0.100 0.100
O1 O 0.200 0.100 0.100
N1 N 0.300 0.150 0.100
"""
    p = tmp_path / "cod.cif"
    p.write_text(cif)
    env = M.handle_molecule_thumbnail({"path": str(p), "size": 256})
    _assert_image(env)
    assert env["n_atoms"] == 3
    assert env["format"] == "cif"


@pytest.mark.unit
def test_real_rcsb_mmcif_if_present():
    """Smoke against a real RCSB mmCIF when the crawl_data links exist."""
    pytest.importorskip("gemmi")
    asset = _ROOT / "crawl_data/biology/rcsb_pdb_mmcif/10BT.cif"
    if not asset.exists():
        pytest.skip("crawl_data not linked in this worktree")
    env = M.handle_molecule_thumbnail({"path": str(asset), "format": "mmcif", "size": 256})
    _assert_image(env)
    # A cache hit (from a prior run) returns a bare envelope without extras;
    # only assert the adaptive fields on a fresh render.
    if "representation" in env:
        assert env["representation"] == "cpk"  # 14k atoms -> CPK + downsample
        assert env["n_atoms_rendered"] <= M._CPK_MAX_ATOMS


@pytest.mark.unit
def test_tool_registered_in_registry():
    import sci3d_agent.mcp.tools  # noqa: F401  (ensures tools import + register)
    from sci3d_agent.mcp.registry import REGISTRY

    assert "sv_render_molecule_thumbnail" in REGISTRY


# ---------------------------------------------------------------------------
# Viewer-frame named views (cc2d-views/2 protocol)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_render_cache(tmp_path, monkeypatch):
    """Every test gets its own render cache.

    ``cached_envelope_from_disk`` replays a hit as a *bare* envelope (no
    representation/n_atoms/view extras), and a stale PNG from an earlier
    renderer would silently satisfy a pixel assertion. Point SV_RENDER_CACHE at
    this test's tmp_path so every call renders for real.
    """
    monkeypatch.setenv("SV_RENDER_CACHE", str(tmp_path / "render-cache"))


# An asymmetric 8-atom structure: a cubic carbon lattice would be invariant
# under the axis-aligned Y-up -> Z-up rotation, so a frame bug could hide.
_ASYM_XYZ = """8
asym
C  0.000  0.000  0.000
N  1.240  0.310  0.115
O -0.410  1.730  0.930
S  0.520 -1.110  2.260
C  2.030  0.640 -0.710
N -1.330 -0.820 -0.940
C  0.870  1.990 -1.480
O -1.010  0.370  1.610
"""


def _asym_pair(tmp_path: Path):
    """(viewer-frame file, the same structure pre-rotated into the renderer frame).

    The rotation applied here must match ``canonical_views.rotate_yup_to_zup``:
    ``(x, y, z) -> (x, -z, y)``.
    """
    viewer = tmp_path / "asym.xyz"
    viewer.write_text(_ASYM_XYZ)
    body = []
    for line in _ASYM_XYZ.splitlines()[2:]:
        elem, x, y, z = line.split()
        x, y, z = float(x), float(y), float(z)
        body.append(f"{elem} {x:.6f} {-z:.6f} {y:.6f}")
    rotated = tmp_path / "asym_rot.xyz"
    rotated.write_text("\n".join(["8", "asym rotated"] + body) + "\n")
    return viewer, rotated


def _pixels(env: dict) -> np.ndarray:
    from PIL import Image

    raw = env.get("image_png_base64")
    if raw is None:
        raw = base64.b64encode(Path(env["cache_path"]).read_bytes()).decode("ascii")
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB"))


@pytest.mark.unit
def test_named_view_equals_explicit_viewer_angles(tmp_path):
    p = tmp_path / "asym.xyz"
    p.write_text(_ASYM_XYZ)
    by_view = M.handle_molecule_thumbnail(
        {"path": str(p), "view": "front", "angle_frame": "viewer_y_up", "size": 128})
    by_angle = M.handle_molecule_thumbnail(
        {"path": str(p), "azimuth": 0.0, "elevation": 0.0,
         "angle_frame": "viewer_y_up", "size": 128})
    assert "error" not in by_view and "error" not in by_angle
    assert np.array_equal(_pixels(by_view), _pixels(by_angle))
    assert by_view["view"] == "front"
    assert by_view["angle_frame"] == "viewer_y_up"


@pytest.mark.unit
def test_viewer_frame_equals_native_on_pre_rotated_coords(tmp_path):
    # The frame conversion is BOTH a -90 azimuth shift and the proper
    # (x, -z, y) coordinate rotation. Viewer-frame azimuth 90 (right) must
    # therefore reproduce the native render of the pre-rotated structure at
    # matplotlib azim 0 -- and must NOT reproduce the native render of the
    # unrotated structure (the negative control that makes the rotation
    # load-bearing in this test).
    viewer, rotated = _asym_pair(tmp_path)
    framed = M.handle_molecule_thumbnail(
        {"path": str(viewer), "azimuth": 90.0, "elevation": 0.0,
         "angle_frame": "viewer_y_up", "size": 128})
    native_rotated = M.handle_molecule_thumbnail(
        {"path": str(rotated), "azimuth": 0.0, "elevation": 0.0, "size": 128})
    native_unrotated = M.handle_molecule_thumbnail(
        {"path": str(viewer), "azimuth": 0.0, "elevation": 0.0, "size": 128})
    assert "error" not in framed and "error" not in native_rotated
    assert np.array_equal(_pixels(framed), _pixels(native_rotated))
    assert not np.array_equal(_pixels(framed), _pixels(native_unrotated))


@pytest.mark.unit
def test_native_frame_default_is_unchanged(tmp_path):
    p = tmp_path / "asym.xyz"
    p.write_text(_ASYM_XYZ)
    env = M.handle_molecule_thumbnail({"path": str(p), "azimuth": 30.0, "elevation": 20.0})
    assert "error" not in env
    assert "angle_frame" not in env and "view" not in env


@pytest.mark.unit
def test_declared_surface_advertises_view_and_angle_frame():
    import sci3d_agent.mcp.tools  # noqa: F401  (registers the tools)
    from sci3d_agent.mcp.registry import REGISTRY
    from sci3d_agent.mcp.tools._render.canonical_views import VIEWER_ANGLES

    schema = REGISTRY["sv_render_molecule_thumbnail"].input_schema
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["view"]["enum"]) == set(VIEWER_ANGLES)
    assert schema["properties"]["angle_frame"]["enum"] == ["native_zup", "viewer_y_up"]


@pytest.mark.unit
def test_view_and_azimuth_conflict_returns_error(tmp_path):
    p = tmp_path / "asym.xyz"
    p.write_text(_ASYM_XYZ)
    env = M.handle_molecule_thumbnail(
        {"path": str(p), "view": "front", "azimuth": 10.0})
    assert "error" in env


@pytest.mark.unit
def test_unknown_view_returns_error(tmp_path):
    p = tmp_path / "asym.xyz"
    p.write_text(_ASYM_XYZ)
    env = M.handle_molecule_thumbnail({"path": str(p), "view": "diagonal"})
    assert "error" in env


@pytest.mark.unit
def test_yup_to_zup_is_a_proper_rotation():
    from sci3d_agent.mcp.tools._render import canonical_views as CANON

    assert np.isclose(np.linalg.det(CANON.YUP_TO_ZUP), 1.0)
    pts = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    rot = CANON.rotate_yup_to_zup(pts)
    # orientation (signed volume of the origin + the three points) is preserved
    assert np.isclose(np.linalg.det(pts), np.linalg.det(rot))
    assert np.allclose(np.linalg.norm(rot, axis=1), np.linalg.norm(pts, axis=1))


# ---------------------------------------------------------------------------
# HETATM ligand retention under CPK downsampling
# ---------------------------------------------------------------------------

def _write_pdb_with_ligand(tmp_path: Path, polymer_atoms: int, ligand_atoms: int) -> Path:
    """A column-correct PDB: N ATOM CA atoms plus a small HETATM HEM ligand."""

    def _atom(record: str, serial: int, name: str, res: str, seq: int,
              x: float, y: float, z: float, element: str) -> str:
        return (
            f"{record:<6}{serial:>5} {name:>4} {res:>3} A{seq:>4}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2}"
        )

    lines = [
        _atom("ATOM", i + 1, "CA", "ALA", i // 4 + 1, i * 0.5, 0.0, 0.0, "C")
        for i in range(polymer_atoms)
    ]
    lines += [
        _atom("HETATM", polymer_atoms + j + 1, "FE", "HEM", 901, j * 0.5, 5.0, 5.0, "FE")
        for j in range(ligand_atoms)
    ]
    p = tmp_path / "complex.pdb"
    p.write_text("\n".join(lines) + "\nEND\n")
    return p


@pytest.mark.unit
def test_hetatm_survives_cpk_downsampling(tmp_path):
    pytest.importorskip("gemmi")
    p = _write_pdb_with_ligand(tmp_path, polymer_atoms=9000, ligand_atoms=4)
    env = M.handle_molecule_thumbnail({"path": str(p), "size": 64})
    assert "error" not in env, env
    assert env["representation"] == "cpk"
    assert env["n_atoms"] == 9004
    assert env["n_hetatm"] == 4
    assert env["n_hetatm_rendered"] == 4
    assert env["n_atoms_rendered"] == 8000  # cap still bounds the render


@pytest.mark.unit
def test_no_hetatm_key_for_plain_xyz(tmp_path):
    p = _write_xyz(tmp_path, 12)
    env = M.handle_molecule_thumbnail({"path": str(p)})
    assert "error" not in env
    assert "n_hetatm" not in env
