"""Tests for Category A (catalog) tools.

Uses the real repository fixtures rooted at SV_REPO_ROOT (default
/Users/sue/Research/ScienceVision). The matched index totals are LOCKED at
10,551 records across 5 datasets per the tier-1 whitelist contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sci3d_agent.mcp.registry import ToolSpec
from sci3d_agent.mcp.tools import catalog as catalog_module
from sci3d_agent.mcp.tools.catalog import TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]

LIST_TOOLS = {
    "sv_list_matched_datasets",
    "sv_search_matched_qa",
    "sv_list_scenes",
    "sv_list_domains",
    "sv_list_assets",
    "sv_list_vqa_datasets",
    "sv_list_negative_samples",
}

SINGLE_RECORD_TOOLS = {
    "sv_get_matched_record",
    "sv_get_asset",
    "sv_get_vqa_sample",
    "sv_get_astronomy_questions",
}


@pytest.fixture(autouse=True)
def _clear_load_cache():
    catalog_module._load_json_cached.cache_clear()
    yield
    catalog_module._load_json_cached.cache_clear()


@pytest.fixture
def specs() -> dict[str, ToolSpec]:
    return {t.name: t for t in TOOLS}


def _call(specs: dict[str, ToolSpec], name: str, args: dict | None = None) -> dict:
    return dict(specs[name].handler(args or {}))


# ---------------------------------------------------------------------------
# Registration / basic invariants
# ---------------------------------------------------------------------------


def test_eleven_tools_registered():
    assert len(TOOLS) == 11
    names = {t.name for t in TOOLS}
    expected = {
        "sv_list_matched_datasets",
        "sv_search_matched_qa",
        "sv_get_matched_record",
        "sv_list_scenes",
        "sv_list_domains",
        "sv_list_assets",
        "sv_get_asset",
        "sv_list_vqa_datasets",
        "sv_get_vqa_sample",
        "sv_get_astronomy_questions",
        "sv_list_negative_samples",
    }
    assert names == expected


def test_all_tools_are_category_A():
    for t in TOOLS:
        assert t.category == "A", f"{t.name} not category A"


def test_input_schemas_are_object_with_additional_properties_false():
    for t in TOOLS:
        assert t.input_schema["type"] == "object"
        assert t.input_schema.get("additionalProperties") is False
        # description non-empty
        assert t.description and isinstance(t.description, str)


# ---------------------------------------------------------------------------
# Envelope shape & pagination invariants
# ---------------------------------------------------------------------------


def _list_returning_invocations() -> list[tuple[str, dict]]:
    return [
        ("sv_search_matched_qa", {"dataset": "embodiedscan-demo", "limit": 5}),
        ("sv_list_scenes", {"dataset": "m3d-refseg", "limit": 3}),
        ("sv_list_assets", {"domain": "biology", "limit": 3}),
        ("sv_list_negative_samples", {"limit": 3}),
    ]


def test_envelope_shape(specs: dict[str, ToolSpec]) -> None:
    for name, args in _list_returning_invocations():
        result = _call(specs, name, args)
        assert set(result.keys()) == {"records", "total", "offset", "limit"}, (
            f"{name} envelope keys: {set(result.keys())}"
        )
        assert isinstance(result["records"], list)
        assert isinstance(result["total"], int)
        assert isinstance(result["offset"], int)
        assert isinstance(result["limit"], int)


def test_total_counts(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_matched_datasets")
    by_name = {d["dataset"]: d["count"] for d in out["datasets"]}
    # The 5 tier-1 datasets are always present.
    assert set(by_name) == {
        "scanqa",
        "sqa3d",
        "m3d-refseg",
        "m3d-seg-0005",
        "embodiedscan-demo",
    }
    # total == sum of per-dataset counts (internal consistency). Asserted as a
    # relation rather than a frozen number because the ScanNet availability
    # policy (disk vs manifest+blocklist) legitimately rescales scanqa/sqa3d.
    assert out["total"] == sum(by_name.values())
    assert all(c > 0 for c in by_name.values())
    # Medical datasets are filtered by on-disk asset presence, independent of
    # the ScanNet policy, so their counts are stable anchors.
    assert by_name["m3d-refseg"] == 2777
    assert by_name["m3d-seg-0005"] == 20
    assert by_name["embodiedscan-demo"] == 3


def test_pagination(specs: dict[str, ToolSpec]) -> None:
    page1 = _call(specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "limit": 10, "offset": 0})
    page2 = _call(specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "limit": 10, "offset": 10})
    assert page1["total"] == page2["total"]
    assert page1["total"] == 2777
    assert len(page1["records"]) == 10
    assert len(page2["records"]) == 10
    qids1 = {r["qid"] for r in page1["records"]}
    qids2 = {r["qid"] for r in page2["records"]}
    assert qids1.isdisjoint(qids2)


def test_limit_clamped_at_max(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_search_matched_qa", {"limit": 99999})
    assert out["limit"] == 500


def test_negative_or_invalid_offset_clamped(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_search_matched_qa", {"offset": -5, "limit": 1})
    assert out["offset"] == 0


# ---------------------------------------------------------------------------
# sv_search_matched_qa
# ---------------------------------------------------------------------------


def test_search_no_filter_pools_all_datasets(specs: dict[str, ToolSpec]) -> None:
    idx = _call(specs, "sv_list_matched_datasets")
    out = _call(specs, "sv_search_matched_qa", {"limit": 1})
    assert out["total"] == idx["total"]


def test_search_filters_compose_with_AND(specs: dict[str, ToolSpec]) -> None:
    base = _call(specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "limit": 1})
    assert base["total"] == 2777
    only_mask = _call(
        specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "has_mask": True, "limit": 1}
    )
    assert only_mask["total"] == 2777
    only_no_mask = _call(
        specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "has_mask": False, "limit": 1}
    )
    assert only_no_mask["total"] == 0
    combined = _call(
        specs,
        "sv_search_matched_qa",
        {
            "dataset": "m3d-refseg",
            "scene_or_case_id": "s0212",
            "has_mask": True,
            "limit": 5,
        },
    )
    assert combined["total"] > 0
    assert combined["total"] < base["total"]
    for r in combined["records"]:
        assert r["scene_or_case_id"] == "s0212"
        assert r["has_mask"] is True


def test_search_question_contains_case_insensitive(specs: dict[str, ToolSpec]) -> None:
    upper = _call(specs, "sv_search_matched_qa", {"question_contains": "REFRIGERATORS", "limit": 1})
    lower = _call(specs, "sv_search_matched_qa", {"question_contains": "refrigerators", "limit": 1})
    assert upper["total"] == lower["total"]
    assert lower["total"] > 0


def test_search_qid_prefix(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_search_matched_qa", {"qid_prefix": "embodiedscan-demo", "limit": 50})
    assert out["total"] == 3
    for r in out["records"]:
        assert r["qid"].startswith("embodiedscan-demo")


def test_search_split_filter(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "split": "train", "limit": 1})
    assert out["total"] > 0
    out_bogus = _call(
        specs, "sv_search_matched_qa", {"dataset": "m3d-refseg", "split": "no-such-split", "limit": 1}
    )
    assert out_bogus["total"] == 0


def test_search_asset_kind_filter(specs: dict[str, ToolSpec]) -> None:
    nifti_all = _call(specs, "sv_search_matched_qa", {"asset_kind": "nifti", "limit": 1})
    nifti_m3d_refseg = _call(
        specs,
        "sv_search_matched_qa",
        {"asset_kind": "nifti", "dataset": "m3d-refseg", "limit": 1},
    )
    unfiltered = _call(specs, "sv_search_matched_qa", {"limit": 1})
    # m3d-refseg is a stable nifti anchor; nifti-filtered set must contain it
    # and may also include other nifti datasets (e.g. m3d-seg-0005).
    assert nifti_m3d_refseg["total"] == 2777
    assert nifti_all["total"] >= nifti_m3d_refseg["total"]
    # asset_kind must actually narrow results
    assert nifti_all["total"] < unfiltered["total"]


# ---------------------------------------------------------------------------
# sv_get_matched_record
# ---------------------------------------------------------------------------


def test_get_matched_record_finds_across_datasets(specs: dict[str, ToolSpec]) -> None:
    scanqa = _call(specs, "sv_get_matched_record", {"qid": "scanqa-test_wo_obj-test-scene0707-0"})
    assert scanqa.get("record", {}).get("dataset") == "scanqa"
    m3d = _call(specs, "sv_get_matched_record", {"qid": "m3d-refseg-train-s0212-00000"})
    assert m3d.get("record", {}).get("dataset") == "m3d-refseg"


def test_m3d_refseg_record_has_mask_field(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_matched_record", {"qid": "m3d-refseg-train-s0212-00000"})
    record = out["record"]
    assert "mask" in record
    mask = record["mask"]
    assert mask["mask_url_viewer"], "mask_url_viewer must be present"
    assert mask["mask_path_repo"]
    assert mask["mask_id"] == "2"

    # has_mask=True filter returns only mask-bearing records
    filt = _call(specs, "sv_search_matched_qa", {"has_mask": True, "limit": 50})
    for r in filt["records"]:
        assert r["has_mask"] is True


def test_missing_qid_returns_error_envelope(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_matched_record", {"qid": "not-a-real-qid"})
    assert "record" not in out
    assert "error" in out
    assert isinstance(out["error"], str)


def test_get_matched_record_rejects_empty_qid(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_matched_record", {"qid": ""})
    assert "error" in out


def test_get_matched_record_qid_with_unknown_prefix_still_searches(specs: dict[str, ToolSpec]) -> None:
    # qid that does not encode a known dataset prefix should still return error envelope
    out = _call(specs, "sv_get_matched_record", {"qid": "random-prefix-12345"})
    assert "error" in out


# ---------------------------------------------------------------------------
# sv_list_scenes
# ---------------------------------------------------------------------------


def test_list_scenes_groups_by_scene(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_scenes", {"dataset": "m3d-refseg", "limit": 500})
    total_count = sum(s["count"] for s in out["records"])
    assert total_count > 0
    s212 = next((s for s in out["records"] if s["scene_or_case_id"] == "s0212"), None)
    assert s212 is not None
    assert s212["count"] >= 1


def test_list_scenes_unknown_dataset_returns_error(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_scenes", {"dataset": "bogus"})
    assert "error" in out


def test_list_scenes_missing_dataset_returns_error(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_scenes", {})
    assert "error" in out


# ---------------------------------------------------------------------------
# sv_list_domains / sv_list_assets / sv_get_asset
# ---------------------------------------------------------------------------


def test_list_domains_returns_known_domains(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_domains", {})
    names = {d["domain"] for d in out["domains"]}
    assert "biology" in names
    assert "medical" in names
    assert out["total"] == len(out["domains"])


def test_list_assets_filters_by_format(specs: dict[str, ToolSpec]) -> None:
    full = _call(specs, "sv_list_assets", {"domain": "biology", "limit": 500})
    filtered = _call(
        specs, "sv_list_assets", {"domain": "biology", "format": "mmcif", "limit": 500}
    )
    assert 0 < filtered["total"] <= full["total"]
    for r in filtered["records"]:
        assert r["format"] == "mmcif"


def test_list_assets_unknown_domain_returns_error(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_assets", {"domain": "no-such-domain"})
    assert "error" in out


def test_list_assets_requires_domain(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_assets", {})
    assert "error" in out


def test_get_asset_finds_record_and_reports_exists(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_asset", {"path": "/crawl_data/biology/rcsb_pdb_mmcif/1A3N.cif"})
    assert out["record"]["name"] == "1A3N"
    assert out["record"]["exists"] is True


def test_get_asset_with_overlay_emits_siblings(specs: dict[str, ToolSpec]) -> None:
    out = _call(
        specs,
        "sv_get_asset",
        {
            "path": "/crawl_data/medical/decathlon/extracted/Task04_Hippocampus/imagesTr/hippocampus_001.nii.gz",
        },
    )
    assert "siblings" in out["record"]
    assert out["record"]["siblings"][0]["kind"] == "overlay"


def test_get_asset_missing_returns_error(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_asset", {"path": "/does/not/exist.cif"})
    assert "error" in out


def test_get_asset_requires_path(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_asset", {"path": ""})
    assert "error" in out


# ---------------------------------------------------------------------------
# VQA datasets / samples
# ---------------------------------------------------------------------------


def test_list_vqa_datasets_returns_entries(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_vqa_datasets", {})
    assert out["total"] > 0
    names = {d["name"] for d in out["datasets"]}
    assert "cadvqa" in names


def test_get_vqa_sample_by_integer_index(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_vqa_sample", {"dataset": "cadvqa", "sample": 0})
    assert out["record"]["id"].startswith("cadvqa-")
    assert out["record"]["dataset"] == "cadvqa"


def test_get_vqa_sample_by_string_id(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_vqa_sample", {"dataset": "cadvqa", "sample": "cadvqa-test-000000"})
    assert out["record"]["id"] == "cadvqa-test-000000"


def test_get_vqa_sample_unknown_dataset(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_vqa_sample", {"dataset": "no-such-dataset", "sample": 0})
    assert "error" in out


def test_get_vqa_sample_missing_args(specs: dict[str, ToolSpec]) -> None:
    assert "error" in _call(specs, "sv_get_vqa_sample", {"dataset": "cadvqa"})
    assert "error" in _call(specs, "sv_get_vqa_sample", {"sample": 0})


def test_get_vqa_sample_out_of_range_index(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_vqa_sample", {"dataset": "cadvqa", "sample": 10_000_000})
    assert "error" in out


# ---------------------------------------------------------------------------
# Astronomy
# ---------------------------------------------------------------------------


def test_get_astronomy_questions_v2(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_astronomy_questions", {"version": "V2"})
    assert out["version"] == "V2"
    assert out["total"] >= 1
    for q in out["questions"]:
        assert q["v_class"] == "V2"


def test_get_astronomy_questions_v4_and_v6(specs: dict[str, ToolSpec]) -> None:
    for v in ("V4", "V6"):
        out = _call(specs, "sv_get_astronomy_questions", {"version": v})
        assert out["version"] == v
        for q in out["questions"]:
            assert q["v_class"] == v


def test_get_astronomy_questions_invalid_version(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_get_astronomy_questions", {"version": "V99"})
    assert "error" in out


def test_get_astronomy_questions_missing_file_raises(
    specs: dict[str, ToolSpec], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When astronomy/questions.json is missing, SVToolError surfaces (server wraps to envelope)."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    catalog_module._load_json_cached.cache_clear()
    from sci3d_agent.mcp.runtime.errors import SVToolError

    with pytest.raises(SVToolError, match="astronomy spec not found"):
        _call(specs, "sv_get_astronomy_questions", {"version": "V2"})


def test_get_astronomy_questions_bad_shape_raises(
    specs: dict[str, ToolSpec], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    spec_path = tmp_path / "spec" / "astronomy"
    spec_path.mkdir(parents=True)
    (spec_path / "questions.json").write_text(json.dumps({"schema_version": "1.0"}))
    catalog_module._load_json_cached.cache_clear()
    from sci3d_agent.mcp.runtime.errors import SVToolError

    with pytest.raises(SVToolError, match="unexpected shape"):
        _call(specs, "sv_get_astronomy_questions", {"version": "V2"})


# ---------------------------------------------------------------------------
# Negative samples
# ---------------------------------------------------------------------------


def test_list_negative_samples_unions_natural_and_synthetic(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_negative_samples", {"limit": 500})
    kinds = {r["kind"] for r in out["records"]}
    # at minimum natural is present in the repo; synthetic may be absent
    assert "natural" in kinds or "synthetic" in kinds


def test_list_negative_samples_kind_filter(specs: dict[str, ToolSpec]) -> None:
    only_natural = _call(specs, "sv_list_negative_samples", {"kind": "natural", "limit": 500})
    for r in only_natural["records"]:
        assert r["kind"] == "natural"


def test_list_negative_samples_domain_filter(specs: dict[str, ToolSpec]) -> None:
    out = _call(specs, "sv_list_negative_samples", {"domain": "biology", "limit": 500})
    for r in out["records"]:
        assert r["domain"] == "biology"


def test_list_negative_samples_graceful_when_files_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, specs: dict[str, ToolSpec]
) -> None:
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    catalog_module._load_json_cached.cache_clear()
    out = _call(specs, "sv_list_negative_samples", {"limit": 5})
    assert out == {"records": [], "total": 0, "offset": 0, "limit": 5}


# ---------------------------------------------------------------------------
# Schema files mirror the in-code input_schema
# ---------------------------------------------------------------------------


def test_schema_files_mirror_input_schema() -> None:
    schemas_dir = REPO_ROOT / "sci3d_agent" / "mcp" / "schemas"
    for tool in TOOLS:
        schema_path = schemas_dir / f"{tool.name}.json"
        assert schema_path.exists(), f"missing schema file for {tool.name}"
        on_disk = json.loads(schema_path.read_text())
        assert on_disk == tool.input_schema, f"schema drift on {tool.name}"


# ---------------------------------------------------------------------------
# _load_json error paths (covers SVToolError branches)
# ---------------------------------------------------------------------------


def test_load_json_missing_raises(tmp_path: Path) -> None:
    from sci3d_agent.mcp.runtime.errors import SVToolError

    with pytest.raises(SVToolError, match="file not found"):
        catalog_module._load_json(tmp_path / "nope.json")


def test_load_json_invalid_raises(tmp_path: Path) -> None:
    from sci3d_agent.mcp.runtime.errors import SVToolError

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SVToolError, match="invalid JSON"):
        catalog_module._load_json(bad)


def test_load_dataset_records_rejects_unknown_dataset() -> None:
    from sci3d_agent.mcp.runtime.errors import SVToolError

    with pytest.raises(SVToolError, match="unknown matched dataset"):
        catalog_module._load_dataset_records("bogus")


def test_load_matched_index_returns_dict() -> None:
    idx = catalog_module._load_matched_index()
    assert isinstance(idx, dict)
    assert {"files", "total"} <= set(idx)
    assert idx["total"] == sum(f["count"] for f in idx["files"])


# ---------------------------------------------------------------------------
# Immutability: handler does not mutate its argument
# ---------------------------------------------------------------------------


def test_handler_does_not_mutate_input(specs: dict[str, ToolSpec]) -> None:
    args = {"dataset": "m3d-refseg", "limit": 2, "has_mask": True}
    snapshot = dict(args)
    _call(specs, "sv_search_matched_qa", args)
    assert args == snapshot
