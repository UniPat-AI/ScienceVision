"""Skip tests/mcp/* when the raw ScienceVision data layer is missing.

These tests target the MCP server's catalog / inspect / pipeline-read /
sv3d-tool-use tools, all of which read real on-disk fixtures (NIfTI volumes,
ScanNet PLYs, mmCIF molecules, Decathlon Task04, ...) that live under
``crawl_data/`` and ``datasets/_shared_3d/`` etc. Those directories are
deliberately not mirrored into this repo (see ``.gitignore`` and
``docs/platform/quickstart.md`` for the partner-pull instructions); a fresh
partner clone will not have them.

Without this conftest, ``pytest tests/`` on such a clone reports 17 failures
that look like regressions but are really unsatisfied data dependencies.
Detecting the absence and converting the failures into clean skips keeps the
signal in CI / partner runs honest.

Issue: https://github.com/UniPat-AI/BabyVision-v2/issues/10
"""
from __future__ import annotations

from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[1]

# Sentinel files — picking specific files rather than directories so an empty
# stub dir does not fool the check. Each sentinel is referenced by at least
# one test in this package; together they cover the data dependencies of all
# 17 known-failing tests on a partner-clone.
_REQUIRED_SENTINELS = (
    REPO_ROOT / "datasets" / "_shared_3d" / "scannet" / "scans_test"
              / "scene0707_00" / "scene0707_00_vh_clean_2.ply",
    REPO_ROOT / "datasets" / "M3D-RefSeg" / "M3D_RefSeg" / "s0212" / "ct.nii.gz",
    REPO_ROOT / "crawl_data" / "biology" / "rcsb_pdb_mmcif" / "1A3N.cif",
    REPO_ROOT / "crawl_data" / "industrial" / "khronos_gltf" / "Avocado.glb",
    REPO_ROOT / "crawl_data" / "medical" / "decathlon" / "extracted"
              / "Task04_Hippocampus" / "imagesTr" / "hippocampus_001.nii.gz",
)


def _missing_sentinels() -> list[Path]:
    return [p for p in _REQUIRED_SENTINELS if not p.exists()]


def pytest_collection_modifyitems(config, items):
    missing = _missing_sentinels()
    if not missing:
        return
    pretty = ", ".join(p.relative_to(REPO_ROOT).as_posix() for p in missing)
    reason = (
        "tests/mcp/* require raw ScienceVision data layers not present in this "
        "clone (missing: " + pretty + "). Pull them per "
        "docs/platform/quickstart.md, or skip this package."
    )
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if _HERE in Path(item.path).parents or Path(item.path).parent == _HERE:
            item.add_marker(skip)
