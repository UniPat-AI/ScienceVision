"""Schema + integrity tests for data/assets.index.json and data/qagen_index.json.

These two files are the source of truth for the launcher counts on the
homepage and for the per-domain / per-family lists on assets.html /
qagen.html. The contract:
  - JSON parses cleanly.
  - Each asset / question has every required key.
  - The file count matches viewer/manifest.json's counts block.
  - If an asset's `path` exists locally, it must point at a real file.
    (Asset binaries are not all tracked in git — some live in
    .gitignored partner-data directories or are pulled from ModelScope
    via scripts/sync_data.py. A fresh checkout with no data sync should
    still pass this test.)
  - Every QA item's asset_id resolves to an asset in assets.index.json.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "data" / "assets.index.json"
QAGEN  = REPO_ROOT / "data" / "qagen_index.json"
MANIFEST = REPO_ROOT / "viewer" / "manifest.json"

REQUIRED_ASSET_KEYS = {"id", "domain", "family", "label", "format", "path"}
REQUIRED_QA_KEYS    = {"id", "family", "asset_id", "question",
                       "answer_kind", "ground_truth_kind"}


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_assets_index_is_well_formed():
    data = _load(ASSETS)
    assert data["version"] == 1
    assert isinstance(data["assets"], list)
    assert len(data["assets"]) == 6, "expected exactly 6 demo assets"
    for a in data["assets"]:
        missing = REQUIRED_ASSET_KEYS - a.keys()
        assert not missing, f"asset {a.get('id')} missing keys: {missing}"
        # File existence is checked separately (see
        # test_asset_paths_when_present_are_real_files); a fresh checkout
        # without a ModelScope sync should still pass schema validation.


def test_asset_paths_when_present_are_real_files():
    """If the asset path resolves locally, it must point at a real file.

    Asset binaries may be gitignored (`examples/*/data/`) or live in
    .gitignored directories pending a ModelScope sync. We don't require
    every path to exist (CI clones don't pull binaries by default), but if
    something IS at that path it must be a file, not a directory or stale
    symlink.
    """
    for a in _load(ASSETS)["assets"]:
        target = REPO_ROOT / a["path"]
        if target.exists():
            assert target.is_file(), \
                f"asset path resolves but is not a file: {a['path']}"


def test_asset_domain_and_family_alignment():
    data = _load(ASSETS)
    embodied = [a for a in data["assets"] if a["domain"] == "embodied"]
    medical  = [a for a in data["assets"] if a["domain"] == "medical"]
    assert len(embodied) == 4
    assert len(medical) == 2
    assert all(a["family"] == "genesis_vp"     for a in embodied)
    assert all(a["family"] == "medical_spatial" for a in medical)


def test_manifest_counts_match_index_lengths():
    m = _load(MANIFEST)
    assets = _load(ASSETS)
    qagen  = _load(QAGEN)
    assert m["counts"]["assets"] == len(assets["assets"])
    assert m["counts"]["qagen"]  == len(qagen["questions"])


def test_qagen_is_well_formed():
    data = _load(QAGEN)
    assert data["version"] == 1
    assert isinstance(data["questions"], list)
    assert len(data["questions"]) == 4, "expected exactly 4 demo questions"
    for q in data["questions"]:
        missing = REQUIRED_QA_KEYS - q.keys()
        assert not missing, f"question {q.get('id')} missing keys: {missing}"


def test_every_qa_asset_id_resolves():
    asset_ids = {a["id"] for a in _load(ASSETS)["assets"]}
    for q in _load(QAGEN)["questions"]:
        assert q["asset_id"] in asset_ids, \
            f"QA {q['id']} references unknown asset {q['asset_id']}"


def test_asset_ids_are_unique():
    ids = [a["id"] for a in _load(ASSETS)["assets"]]
    assert len(ids) == len(set(ids)), \
        f"duplicate asset IDs: {[i for i in ids if ids.count(i) > 1]}"


def test_qa_ids_are_unique():
    ids = [q["id"] for q in _load(QAGEN)["questions"]]
    assert len(ids) == len(set(ids)), \
        f"duplicate QA IDs: {[i for i in ids if ids.count(i) > 1]}"
