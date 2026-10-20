"""Molecular structure thumbnail renderer (mmcif/cif/pdb/xyz -> PNG).

Renders a single orbit view of a molecular structure via headless matplotlib
3D (Agg) — no GL, no pymol. Used by the 2D multi-view baseline so biology
(mmcif) and chemistry (cif/xyz) tasks get REAL 2D views derived offline from
the 3D asset; the cc2d agent only ever sees the PNGs.

Representation is ADAPTIVE by atom count:

  * small (<= _BALLSTICK_MAX_ATOMS): element-colored ball-and-stick — spheres
    sized by covalent radius plus distance-inferred bonds, so bond geometry is
    legible for small molecules / ligands.
  * large (proteins, complexes): element-colored CPK spheres only (bonds
    omitted — they clutter at scale), randomly downsampled to
    _CPK_MAX_ATOMS to keep the overall fold readable and the render bounded.

Parsing: .xyz is parsed directly (no dependency); mmcif/cif/pdb/sdf/mol use
gemmi. Missing gemmi for a structured format returns a dependency-missing
envelope rather than raising across the MCP boundary.
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from . import canonical_views as CANON
from . import common as C

# Representation thresholds (atom counts).
_BALLSTICK_MAX_ATOMS = 300
_CPK_MAX_ATOMS = 8000  # downsample cap for large structures

_ELEV_DEFAULT = 20.0

# CPK-ish element colors (RGB 0-1). Unknown elements fall back to _DEFAULT_COLOR.
_ELEMENT_COLORS: dict[str, tuple[float, float, float]] = {
    "H": (0.90, 0.90, 0.90), "C": (0.28, 0.28, 0.28), "N": (0.18, 0.31, 0.97),
    "O": (0.94, 0.15, 0.15), "S": (0.98, 0.82, 0.19), "P": (0.98, 0.55, 0.13),
    "F": (0.34, 0.78, 0.28), "CL": (0.24, 0.78, 0.24), "BR": (0.60, 0.13, 0.06),
    "I": (0.55, 0.10, 0.62), "FE": (0.87, 0.40, 0.20), "CA": (0.24, 0.99, 0.0),
    "NA": (0.44, 0.22, 0.69), "MG": (0.54, 1.0, 0.0), "ZN": (0.49, 0.50, 0.69),
    "K": (0.56, 0.25, 0.83), "MN": (0.61, 0.48, 0.78),
}
_DEFAULT_COLOR = (0.85, 0.42, 0.66)  # pink for unrecognized elements

# Covalent radii (Angstrom) for bond inference + sphere sizing.
_COVALENT_RADII: dict[str, float] = {
    "H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05, "P": 1.07,
    "F": 0.57, "CL": 1.02, "BR": 1.20, "I": 1.39, "FE": 1.32, "CA": 1.76,
    "NA": 1.66, "MG": 1.41, "ZN": 1.22, "K": 2.03, "MN": 1.39,
}
_DEFAULT_RADIUS = 0.75
_BOND_TOLERANCE = 1.30  # bond if dist < (r_i + r_j) * tolerance
_BOND_MAX_ATOMS = 300  # skip O(n^2) bond search above this
_GEMMI_FORMATS = {"mmcif", "cif", "pdb", "sdf", "mol", "mol2"}
_BG = (0.04, 0.05, 0.08)


def _norm_element(sym: str) -> str:
    """Normalize a raw element/atom-name token to an uppercase element symbol."""
    s = "".join(ch for ch in (sym or "") if ch.isalpha()).upper()
    if not s:
        return "X"
    # Two-letter elements we know; otherwise take the first letter.
    if len(s) >= 2 and s[:2] in _COVALENT_RADII:
        return s[:2]
    return s[:1] if s[:1] in _COVALENT_RADII else s[:2]


def _parse_xyz(path: Path) -> tuple[np.ndarray, list[str], list[bool] | None] | None:
    """Parse a plain .xyz file -> (N×3 coords, element symbols, het flags).

    ``het`` is None: a bare xyz has no HETATM concept, so nothing needs
    protecting from the downsample.
    """
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        n = int(lines[0].strip())
    except (ValueError, IndexError):
        n = None
    body = lines[2:] if n is not None else lines
    coords: list[list[float]] = []
    elems: list[str] = []
    for ln in body:
        parts = ln.split()
        if len(parts) < 4:
            continue
        try:
            xyz = [float(parts[1]), float(parts[2]), float(parts[3])]
        except ValueError:
            continue
        coords.append(xyz)
        elems.append(_norm_element(parts[0]))
    if not coords:
        return None
    return np.asarray(coords, dtype=np.float64), elems, None


def _cif_num(s: str | None) -> float | None:
    """Parse a CIF numeric value, stripping the trailing (esd) uncertainty."""
    if s is None:
        return None
    try:
        return float(re.sub(r"\(.*?\)", "", s).strip())
    except ValueError:
        return None


def _parse_cif_document(path: Path, gemmi) -> tuple[np.ndarray, list[str], list[bool] | None] | None:
    """Parse a crystallographic CIF (e.g. COD) via gemmi.cif.

    RCSB mmCIF builds a full Structure through read_structure; crystallographic
    small-molecule CIFs (COD) do not — they carry a bare _atom_site loop with
    FRACTIONAL coords + cell params. Read those directly and orthogonalize.
    """
    try:
        doc = gemmi.cif.read(str(path))
        block = doc.sole_block()
    except Exception:
        return None

    def loop(tag):
        vals = block.find_loop(tag)
        return list(vals) if vals else []

    syms = loop("_atom_site_type_symbol") or loop("_atom_site_label")
    cart = [loop(f"_atom_site_Cartn_{k}") for k in ("x", "y", "z")]
    frac = [loop(f"_atom_site_fract_{k}") for k in ("x", "y", "z")]

    if all(cart) and len(cart[0]) == len(syms) and syms:
        coords = [[_cif_num(cart[0][i]), _cif_num(cart[1][i]), _cif_num(cart[2][i])]
                  for i in range(len(syms))]
    elif all(frac) and len(frac[0]) == len(syms) and syms:
        cell = [_cif_num(block.find_value(f"_cell_length_{k}")) for k in ("a", "b", "c")]
        angs = [_cif_num(block.find_value(f"_cell_angle_{k}")) for k in ("alpha", "beta", "gamma")]
        if None in cell or None in angs:
            return None
        uc = gemmi.UnitCell(cell[0], cell[1], cell[2], angs[0], angs[1], angs[2])
        coords = []
        for i in range(len(syms)):
            fr = gemmi.Fractional(_cif_num(frac[0][i]), _cif_num(frac[1][i]), _cif_num(frac[2][i]))
            pos = uc.orthogonalize(fr)
            coords.append([pos.x, pos.y, pos.z])
    else:
        return None

    clean = [(c, _norm_element(s)) for c, s in zip(coords, syms) if None not in c]
    if not clean:
        return None
    coords_arr = np.asarray([c for c, _ in clean], dtype=np.float64)
    return coords_arr, [e for _, e in clean], None


def _parse_gemmi(path: Path, fmt: str) -> tuple[np.ndarray, list[str], list[bool] | None] | None:
    """Parse mmcif/cif/pdb/sdf via gemmi -> (coords, elements, het flags).

    ``het[i]`` is True when the atom sits in a HETATM residue; the flag is what
    keeps ligands from being sampled away by the CPK downsample. None on
    failure.

    Tries read_structure first (RCSB mmCIF / PDB build a full hierarchy); falls
    back to the raw CIF atom_site loop for crystallographic files (COD) that
    read_structure returns empty for.
    """
    gemmi = C.try_import("gemmi")
    if gemmi is None:
        return None
    coords: list[list[float]] = []
    elems: list[str] = []
    het: list[bool] = []
    try:
        st = gemmi.read_structure(str(path))
    except Exception:
        st = None
    if st is not None and len(st) > 0:
        model = st[0]
        for chain in model:
            for res in chain:
                is_het = getattr(res, "het_flag", "") == "H"
                for atom in res:
                    pos = atom.pos
                    coords.append([pos.x, pos.y, pos.z])
                    name = getattr(getattr(atom, "element", None), "name", "") or atom.name
                    elems.append(_norm_element(name))
                    het.append(is_het)
    if coords:
        return np.asarray(coords, dtype=np.float64), elems, het
    # read_structure came up empty — try the raw CIF loop (fractional coords).
    return _parse_cif_document(path, gemmi)


def _load_structure(path: Path, fmt: str) -> tuple[np.ndarray, list[str], list[bool] | None] | None:
    if fmt == "xyz":
        return _parse_xyz(path)
    if fmt in _GEMMI_FORMATS:
        return _parse_gemmi(path, fmt)
    # Unknown extension: try xyz text first, then gemmi.
    return _parse_xyz(path) or _parse_gemmi(path, fmt)


def _infer_bonds(coords: np.ndarray, elems: list[str]) -> list[tuple[int, int]]:
    """Distance-based bond inference for small structures (O(n^2))."""
    n = coords.shape[0]
    if n < 2 or n > _BOND_MAX_ATOMS:
        return []
    radii = np.array([_COVALENT_RADII.get(e, _DEFAULT_RADIUS) for e in elems])
    bonds: list[tuple[int, int]] = []
    for i in range(n):
        d = np.linalg.norm(coords[i + 1:] - coords[i], axis=1)
        thr = (radii[i] + radii[i + 1:]) * _BOND_TOLERANCE
        for off in np.nonzero((d > 0.4) & (d < thr))[0]:
            bonds.append((i, i + 1 + int(off)))
    return bonds


def _set_equal_3d(ax, coords: np.ndarray) -> None:
    """Center + equalize the 3D axes limits so the molecule isn't distorted."""
    lo = coords.min(axis=0)
    hi = coords.max(axis=0)
    center = (lo + hi) / 2.0
    span = float((hi - lo).max()) or 1.0
    r = span * 0.6
    ax.set_xlim(center[0] - r, center[0] + r)
    ax.set_ylim(center[1] - r, center[1] + r)
    ax.set_zlim(center[2] - r, center[2] + r)


def handle_molecule_thumbnail(args: Mapping[str, Any]) -> dict:
    raw = args.get("path")
    if not raw:
        return {"error": "missing required arg: path"}
    # A named view is defined in the VIEWER frame (the table's frame), so it
    # implies angle_frame="viewer_y_up"; the native Z-up default only applies to
    # explicit azimuth/elevation. An explicit native frame next to a `view` is a
    # contradiction, not a request.
    view = args.get("view")
    raw_frame = args.get("angle_frame")
    angle_frame = str(raw_frame or ("viewer_y_up" if view is not None else "native_zup"))
    if angle_frame not in {"native_zup", "viewer_y_up"}:
        return {"error": "angle_frame must be one of: native_zup, viewer_y_up"}
    if view is not None:
        if view not in CANON.VIEWER_ANGLES:
            return {"error": f"view must be one of: {sorted(CANON.VIEWER_ANGLES)}"}
        if angle_frame != "viewer_y_up":
            return {"error": "view requires angle_frame=viewer_y_up"}
        if args.get("azimuth") is not None or args.get("elevation") is not None:
            return {"error": "view and azimuth/elevation are mutually exclusive"}
        azimuth, elevation = CANON.VIEWER_ANGLES[view]
    else:
        try:
            azimuth = float(args.get("azimuth") or 0.0)
            elevation = float(args["elevation"]) if args.get("elevation") is not None else _ELEV_DEFAULT
        except (TypeError, ValueError):
            return {"error": "azimuth/elevation must be numeric"}
    if angle_frame == "viewer_y_up":
        azimuth, elevation = CANON.to_matplotlib_frame(azimuth, elevation)
    size = int(args.get("size") or 512)
    path = C.resolve_path(raw)
    if not path.exists():
        return {"error": f"file not found: {path}"}
    fmt = (args.get("format") or path.suffix.lstrip(".")).lower()
    if fmt == "gz" and path.name.lower().endswith(".cif.gz"):
        fmt = "cif"

    cache_args = {"azimuth": azimuth, "elevation": elevation, "size": size, "format": fmt,
                  "molecule_render_version": 3}
    if view is not None:
        cache_args["view"] = str(view)
    if angle_frame != "native_zup":
        cache_args["angle_frame"] = angle_frame
    sha = C.render_cache_key("sv_render_molecule_thumbnail", cache_args, [path])
    cached = C.cached_envelope_from_disk(sha)
    if cached is not None:
        return cached

    matplotlib = C.try_import("matplotlib")
    if matplotlib is None:
        return C.missing_dep("matplotlib")
    if fmt in _GEMMI_FORMATS and C.try_import("gemmi") is None:
        return C.missing_dep("gemmi")

    parsed = _load_structure(path, fmt)
    if parsed is None:
        return {"error": f"could not parse molecular structure ({fmt}): {path}"}
    coords, elems, het = parsed
    if angle_frame == "viewer_y_up":
        coords = CANON.rotate_yup_to_zup(coords)
    n_total = int(coords.shape[0])

    # Adaptive representation. HETATM atoms (ligands, cofactors) are always
    # kept: they are what a binding-geometry question asks about, and a uniform
    # downsample of a large complex would drop them with the rest of the noise.
    if n_total <= _BALLSTICK_MAX_ATOMS:
        representation = "ball-and-stick"
        sel = np.arange(n_total)
    else:
        representation = "cpk"
        het_idx = (np.nonzero(np.asarray(het, dtype=bool))[0]
                   if het is not None else np.zeros(0, dtype=int))
        budget = max(0, _CPK_MAX_ATOMS - len(het_idx))
        polymer = np.setdiff1d(np.arange(n_total), het_idx)
        if len(polymer) <= budget:
            sel = np.arange(n_total)
        else:
            rng = np.random.default_rng(42)
            sel = np.sort(np.concatenate(
                [het_idx, rng.choice(polymer, size=budget, replace=False)]))
    coords_r = coords[sel]
    elems_r = [elems[i] for i in sel]
    n_rendered = int(coords_r.shape[0])

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt  # type: ignore
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (registers 3d projection)

    colors = np.array([_ELEMENT_COLORS.get(e, _DEFAULT_COLOR) for e in elems_r])
    radii = np.array([_COVALENT_RADII.get(e, _DEFAULT_RADIUS) for e in elems_r])

    dpi = 100
    fig = plt.figure(figsize=(size / dpi, size / dpi), dpi=dpi)
    fig.patch.set_facecolor(_BG)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(_BG)

    if representation == "ball-and-stick":
        # Bonds first so spheres draw on top.
        for i, j in _infer_bonds(coords_r, elems_r):
            ax.plot(
                [coords_r[i, 0], coords_r[j, 0]],
                [coords_r[i, 1], coords_r[j, 1]],
                [coords_r[i, 2], coords_r[j, 2]],
                color=(0.7, 0.7, 0.72), linewidth=1.6, zorder=1,
            )
        s = (radii * 90.0) ** 2 / 40.0
        ax.scatter(
            coords_r[:, 0], coords_r[:, 1], coords_r[:, 2],
            c=colors, s=s, depthshade=True,
            edgecolors=(0.1, 0.1, 0.12), linewidths=0.4, zorder=2,
        )
    else:
        point_area = max(4.0, 1200.0 / math.sqrt(n_rendered))
        ax.scatter(
            coords_r[:, 0], coords_r[:, 1], coords_r[:, 2],
            c=colors, s=point_area, depthshade=True, linewidths=0.0,
        )

    _set_equal_3d(ax, coords_r)
    ax.view_init(elev=elevation, azim=azimuth)
    ax.set_axis_off()
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG, dpi=dpi, bbox_inches="tight", pad_inches=0.0)
    plt.close(fig)
    png = buf.getvalue()

    return C.build_image_envelope(
        png,
        cache_sha=sha,
        extras={
            "format": fmt,
            "azimuth": azimuth,
            "elevation": elevation,
            "representation": representation,
            "n_atoms": n_total,
            "n_atoms_rendered": n_rendered,
            **({"view": str(view)} if view is not None else {}),
            **({"angle_frame": angle_frame} if angle_frame != "native_zup" else {}),
            **({"n_hetatm": int(sum(het)),
                "n_hetatm_rendered": int(sum(het[i] for i in sel))}
               if het is not None else {}),
        },
    )
