"""STEP/STP -> triangles, so the mesh renderer has something it can read.

47 of the 250 tasks are `occt` rows and every one carries a `.step` or `.stp`
asset. A STEP file is a B-rep — analytic faces bounded by trimmed curves — and
``sv_render_mesh_thumbnail`` reads its input with ``trimesh.load``, which has no
B-rep reader. So the stager cannot hand it the asset path: it hands it the path
of a mesh tessellated from that asset, and this module is what tessellates it.

Ported 2026-09-24 from the cc2d preload lineage
(``scripts/cc2d_v6/step_mesh.py`` + ``tools/thumbs/cad.py::shape_to_trimesh``
in ScienceVision). eval-baseline has no ``tools/thumbs`` package, so the B-rep
walk is vendored here rather than imported; the reciprocal ScienceVision port
should collapse the two copies back into one implementation.

### Everything fails closed, and each failure has its own name

There is no degraded arm for a CAD asset nothing could triangulate. Each way
this can fail raises ``StepMeshError`` with a distinct code, and the stager
turns it into a ``RenderVisibilityError`` of that code: ``ocp_unavailable`` is
an environment fact (a mac without OCP is exactly why the 47 tasks are pending
H20), ``step_read_failed`` is about the file, ``step_tessellation_failed`` and
``step_tessellation_empty`` are about the mesh. An operator reading a build
report should not have to guess which.

No OCP exception text is passed through: ``StepMeshError.detail`` is a short
string built here — an exception *type* name at worst — because the stager
writes it into ``render_manifest.json`` and an OCP message can contain the
asset's path.

### The mesh is Z-up ``.ply``, never ``.glb``

``render_mesh_image`` picks its up-axis from the suffix (``.glb``/``.gltf`` →
Y-up, everything else Z-up) and CAD geometry is Z-up; exporting to glTF would
silently roll every view a quarter turn. The suffix is the export format too,
read back off ``STEP_TESSELLATED_NAME``, so it is decided in one place.

### Nothing here mutates its input

``rewrite_plan`` returns a new plan built from new arg dicts; the plan
``plan_views`` produced and the dicts inside it are left exactly as they were.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

STEP_FORMATS = frozenset({"step", "stp"})
STEP_SUFFIXES = frozenset(f".{fmt}" for fmt in STEP_FORMATS)
# B-rep suffixes the *planner* routes onto the mesh branch but this module cannot
# tessellate (no IGES reader here). They fail closed with `step_format_unsupported`
# instead of reaching trimesh.load, whose error for a B-rep is vaguer.
CAD_BREP_SUFFIXES = STEP_SUFFIXES | frozenset({".iges", ".igs"})

STEP_MESH_TOOL = "sv_render_mesh_thumbnail"

# The four BRepMesh_IncrementalMesh arguments, equal to tools/thumbs/cad.py's
# historical defaults in ScienceVision. linear is in model units (mm for this
# corpus), angular in radians.
STEP_MESH_LINEAR_DEFLECTION = 0.5
STEP_MESH_ANGULAR_DEFLECTION = 0.5
STEP_MESH_RELATIVE = False
STEP_MESH_PARALLEL = True

STEP_TESSELLATION_CHAIN = (
    "OCP.STEPControl_Reader -> BRepMesh_IncrementalMesh -> TopExp_Explorer "
    "-> BRep_Tool.Triangulation_s"
)

STEP_OCP_PACKAGE = "OCP"
STEP_OCP_MODULES = tuple(
    f"{STEP_OCP_PACKAGE}.{name}"
    for name in (
        "BRep",
        "BRepMesh",
        "IFSelect",
        "STEPControl",
        "TopAbs",
        "TopExp",
        "TopLoc",
        "TopoDS",
    )
)

# A scratch directory outside the task tree: a cc2d ``environment/`` holds
# ``views/``, the Dockerfile and the manifest and nothing else, and a mesh
# derived from the raw asset is exactly what the arm must not ship.
STEP_SCRATCH_PREFIX = "cc2d-step-mesh-"
# One file per distinct source, never named by the asset's own stem, which is a
# token the cc2d leak scan looks for.
STEP_TESSELLATED_NAME = "tessellated_{index:02d}_{ordinal:02d}.ply"

STEP_FAIL_OCP_UNAVAILABLE = "ocp_unavailable"
STEP_FAIL_READ = "step_read_failed"
STEP_FAIL_TESSELLATION = "step_tessellation_failed"
STEP_FAIL_EMPTY = "step_tessellation_empty"
STEP_FAIL_UNROUTABLE = "step_plan_unroutable"
STEP_FAIL_UNSUPPORTED = "step_format_unsupported"

# One planned render call, as `scripts.harbor._multiview.plan_views` returns it.
RenderCall = tuple[str, dict[str, Any]]


class StepMeshError(RuntimeError):
    """A named, fail-closed reason a STEP file did not become a mesh.

    ``code`` is the ``failure_reason`` the render manifest records and the
    message of the ``RenderVisibilityError`` the stager raises. ``detail`` is a
    short note built by this module and guaranteed free of any filesystem path,
    because it is written into an artifact.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code} ({detail})" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class StepPlan:
    """What `rewrite_plan` decided: the calls to render, and what it did to them.

    ``provenance`` is None when no call named a STEP path — the overwhelming
    majority of tasks — and the manifest is then written exactly as it was
    before this module existed.
    """

    calls: tuple[RenderCall, ...]
    provenance: dict[str, Any] | None


def is_step(path: str | Path) -> bool:
    """Whether this path names one of the two B-rep formats the corpus carries."""
    return Path(str(path)).suffix.lower() in STEP_SUFFIXES


def _is_cad_brep(path: str | Path) -> bool:
    return Path(str(path)).suffix.lower() in CAD_BREP_SUFFIXES


def ocp_missing() -> str:
    """The first `STEP_OCP_MODULES` entry that will not import, or ``""``.

    Asks about the submodules the chain actually uses rather than about the
    top-level package: a partial OCP install imports ``OCP`` and then fails on
    ``OCP.BRepMesh``, and the operator needs to be told which one.
    """
    for name in STEP_OCP_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - any import failure means "not usable"
            return name
    return ""


@contextlib.contextmanager
def scratch_dir() -> Iterator[Path]:
    """A directory for tessellated meshes, removed however the caller returns.

    Deliberately outside the task tree and deleted once the renderer has read
    it. A random per-run path means the render cache cannot be reused across
    builds for STEP tasks; that is the documented cost of never leaving derived
    geometry in a task tree.
    """
    with tempfile.TemporaryDirectory(prefix=STEP_SCRATCH_PREFIX) as path:
        yield Path(path)


def _import_failure_code(error: ImportError) -> str:
    """``ocp_unavailable`` when `error` is about the kernel, else tessellation."""
    module = str(getattr(error, "name", "") or "")
    package = module.split(".", 1)[0]
    return (
        STEP_FAIL_OCP_UNAVAILABLE
        if package == STEP_OCP_PACKAGE
        else STEP_FAIL_TESSELLATION
    )


def read_shape(source: Path) -> Any:
    """The OCP shape one STEP file carries, or a `StepMeshError`."""
    try:
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
    except Exception as error:  # noqa: BLE001 - ImportError, or a broken build
        raise StepMeshError(STEP_FAIL_OCP_UNAVAILABLE, type(error).__name__) from error
    try:
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(source))
    except Exception as error:  # noqa: BLE001 - OCP raises its own types
        raise StepMeshError(STEP_FAIL_READ, type(error).__name__) from error
    if status != IFSelect_RetDone:
        raise StepMeshError(STEP_FAIL_READ, "reader_status_not_done")
    try:
        reader.TransferRoots()
        return reader.OneShape()
    except Exception as error:  # noqa: BLE001 - OCP raises its own types
        raise StepMeshError(STEP_FAIL_READ, type(error).__name__) from error


def shape_to_mesh(shape: Any) -> Any:
    """Tessellate an OCP shape into a ``trimesh.Trimesh``.

    Vendored from ScienceVision ``tools/thumbs/cad.py::shape_to_trimesh``; keep
    the two in step until the reciprocal port collapses them.
    """
    import numpy as np
    import trimesh
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS  # static downcast helpers

    BRepMesh_IncrementalMesh(
        shape,
        STEP_MESH_LINEAR_DEFLECTION,
        STEP_MESH_RELATIVE,
        STEP_MESH_ANGULAR_DEFLECTION,
        STEP_MESH_PARALLEL,
    )

    all_v: list[Any] = []
    all_f: list[Any] = []
    offset = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trans = loc.Transformation()
            n_nodes = tri.NbNodes()
            verts = np.empty((n_nodes, 3), dtype=np.float64)
            for i in range(1, n_nodes + 1):
                p = tri.Node(i).Transformed(trans)
                verts[i - 1] = (p.X(), p.Y(), p.Z())
            n_tri = tri.NbTriangles()
            faces = np.empty((n_tri, 3), dtype=np.int64)
            for i in range(1, n_tri + 1):
                a, b, c = tri.Triangle(i).Get()
                faces[i - 1] = (a - 1 + offset, b - 1 + offset, c - 1 + offset)
            all_v.append(verts)
            all_f.append(faces)
            offset += n_nodes
        exp.Next()

    if not all_v:
        raise StepMeshError(STEP_FAIL_EMPTY, "no_triangulated_face")
    return trimesh.Trimesh(
        vertices=np.vstack(all_v), faces=np.vstack(all_f), process=False
    )


def tessellate(path: str | Path) -> Any:
    """The ``trimesh.Trimesh`` one ``.step``/``.stp`` file tessellates to.

    Raises `StepMeshError` and nothing else. An empty result is a failure, not
    an empty mesh handed on: a mesh with no triangles renders as a blank frame,
    and a blank frame is a thing a later gate would have to catch by inspecting
    pixels, having already claimed the task was built.
    """
    source = Path(str(path))
    if not is_step(source):
        raise StepMeshError(
            STEP_FAIL_UNSUPPORTED, source.suffix.lower().lstrip(".") or "none"
        )
    if not source.is_file():
        raise StepMeshError(STEP_FAIL_READ, "source_absent")

    shape = read_shape(source)
    try:
        mesh = shape_to_mesh(shape)
    except StepMeshError:
        raise
    except ImportError as error:
        # Six of the eight OCP submodules import inside the chain, so a partial
        # kernel surfaces here rather than in `read_shape`. It is still an
        # environment fact and gets the environment's code.
        raise StepMeshError(_import_failure_code(error), type(error).__name__) from error
    except Exception as error:  # noqa: BLE001 - OCP / trimesh raise their own
        raise StepMeshError(STEP_FAIL_TESSELLATION, type(error).__name__) from error
    if len(mesh.faces) == 0 or len(mesh.vertices) == 0:
        raise StepMeshError(STEP_FAIL_EMPTY, "empty_mesh")
    return mesh


def tessellate_to(source: str | Path, dest: Path) -> dict[str, Any]:
    """Tessellate `source`, write it to `dest`, and describe what was written.

    The export format is `dest`'s own suffix, so ``STEP_TESSELLATED_NAME`` is
    the single place it is decided. The returned row is provenance: the triangle
    counts a reviewer can compare against a rendered silhouette, and the hash
    that identifies the exact mesh the views came off. No paths: this row is
    written into the manifest artifact.
    """
    mesh = tessellate(source)
    file_type = dest.suffix.lower().lstrip(".")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = mesh.export(file_type=file_type)
        dest.write_bytes(payload if isinstance(payload, bytes) else payload.encode())
    except Exception as error:  # noqa: BLE001 - trimesh / OS raise their own
        raise StepMeshError(STEP_FAIL_TESSELLATION, type(error).__name__) from error
    return {
        "source_format": Path(str(source)).suffix.lower().lstrip("."),
        "mesh_format": file_type,
        "vertex_count": int(len(mesh.vertices)),
        "face_count": int(len(mesh.faces)),
        "mesh_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
    }


def _provenance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The disclosure block the manifest records, built in exactly one place."""
    return {
        "kernel": "OCP",
        "chain": STEP_TESSELLATION_CHAIN,
        "linear_deflection": STEP_MESH_LINEAR_DEFLECTION,
        "angular_deflection": STEP_MESH_ANGULAR_DEFLECTION,
        "relative": STEP_MESH_RELATIVE,
        "parallel": STEP_MESH_PARALLEL,
        "meshes": rows,
    }


def _destination(scratch: Path, index: int, ordinal: int) -> Path:
    return Path(scratch) / STEP_TESSELLATED_NAME.format(index=index, ordinal=ordinal)


def rewrite_plan(
    calls: Sequence[RenderCall], *, scratch: Path, index: int = 0
) -> StepPlan:
    """The plan to render, with every STEP path replaced by a tessellated mesh.

    Each *distinct* source is tessellated once and reused across that source's
    views — an orbit is 1, 3, 6 or 12 calls on one asset. Keyed by the source
    path, so two different B-reps in one plan cannot silently share one mesh.

    A CAD path planned onto anything but ``STEP_MESH_TOOL`` fails closed
    (``step_plan_unroutable``); an IGES path fails closed
    (``step_format_unsupported``) because no reader for it exists here, and
    letting it through would hand a raw B-rep to ``trimesh.load``.
    """
    if not any(_is_cad_brep(args.get("path") or "") for _, args in calls):
        return StepPlan(calls=tuple(calls), provenance=None)

    meshes: dict[str, Path] = {}
    rows: list[dict[str, Any]] = []
    rewritten: list[RenderCall] = []
    for tool_name, args in calls:
        source = str(args.get("path") or "")
        if not _is_cad_brep(source):
            rewritten.append((tool_name, args))
            continue
        if tool_name != STEP_MESH_TOOL:
            raise StepMeshError(STEP_FAIL_UNROUTABLE, tool_name)
        if not is_step(source):
            raise StepMeshError(
                STEP_FAIL_UNSUPPORTED, Path(source).suffix.lower().lstrip(".") or "none"
            )
        dest = meshes.get(source)
        if dest is None:
            dest = _destination(Path(scratch), index, len(meshes))
            rows.append(tessellate_to(source, dest))
            meshes[source] = dest
        rewritten.append((tool_name, {**args, "path": str(dest)}))

    return StepPlan(calls=tuple(rewritten), provenance=_provenance(rows))
