"""Tests for sci3d_agent.mcp.tools.pipeline_read (Phase 1 / Agent E1)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

import pytest

from sci3d_agent.mcp.tools import pipeline_read as pr


# ---------------------------------------------------------------------------
# Tool registration / smoke
# ---------------------------------------------------------------------------


def test_tools_count_is_five():
    assert len(pr.TOOLS) == 5


def test_tools_names():
    names = sorted(t.name for t in pr.TOOLS)
    assert names == sorted([
        "sv_score_astronomy",
        "sv_classify_v",
        "sv_get_v_classify_pilot_summary",
        "sv_validate_matched_index",
        "sv_run_v_validation",
    ])


def test_tools_categories_all_E():
    assert all(t.category == "E" for t in pr.TOOLS)


def test_tools_have_object_schemas():
    for spec in pr.TOOLS:
        assert isinstance(spec.input_schema, dict)
        assert spec.input_schema.get("type") == "object"


# ---------------------------------------------------------------------------
# sv_score_astronomy
# ---------------------------------------------------------------------------


def test_score_astronomy_canned_gt_pass():
    """Patch the in-process impl; verify envelope shape on a passing answer."""

    def fake_impl(question_id: str, predicted: Any) -> dict:
        return {
            "question": {"id": question_id, "v_class": "V6"},
            "truth": {"ground_truth_max_mi_deg": 7.0, "tolerance_deg": 0.5},
            "result": {"q": question_id, "v_class": "V6", "pass": True,
                       "expected": "7.00° ± 0.5°", "got": predicted},
        }

    out = pr._handle_score_astronomy(
        {"question_id": "solar_coplanar_invariant", "predicted_answer": 7.0},
        _impl=fake_impl,
    )
    assert out["pass"] is True
    assert out["score"] == 1.0
    assert out["gt_version"] == "V6"
    assert out["reference_answer"] == 7.0
    assert "breakdown" in out
    assert out["breakdown"]["got"] == 7.0


def test_score_astronomy_canned_gt_fail():
    def fake_impl(question_id: str, predicted: Any) -> dict:
        return {
            "question": {"id": question_id, "v_class": "V2"},
            "truth": {"ground_truth": "edge-on"},
            "result": {"q": question_id, "v_class": "V2", "pass": False,
                       "expected": "edge-on", "got": predicted},
        }

    out = pr._handle_score_astronomy(
        {"question_id": "saturn_ring_occlusion", "predicted_answer": "face-on"},
        _impl=fake_impl,
    )
    assert out["pass"] is False
    assert out["score"] == 0.0
    assert out["reference_answer"] == "edge-on"


def test_score_astronomy_missing_question_id():
    out = pr._handle_score_astronomy({"predicted_answer": 0})
    assert "error" in out


def test_score_astronomy_missing_predicted():
    out = pr._handle_score_astronomy({"question_id": "abc"})
    assert "error" in out


def test_score_astronomy_impl_returns_error():
    def fake_impl(question_id: str, predicted: Any) -> dict:
        return {"error": "synthetic failure"}

    out = pr._handle_score_astronomy(
        {"question_id": "x", "predicted_answer": 1},
        _impl=fake_impl,
    )
    assert "error" in out
    assert out["gt_version"] == "V6"


def test_score_astronomy_v4_reference_answer():
    def fake_impl(question_id: str, predicted: Any) -> dict:
        return {
            "question": {"id": question_id, "v_class": "V4"},
            "truth": {"ground_truth_helio_RG_au": 9.99, "tolerance_rel": 0.005},
            "result": {"q": question_id, "v_class": "V4", "pass": False,
                       "expected": {}, "got": predicted},
        }

    out = pr._handle_score_astronomy(
        {"question_id": "v4", "predicted_answer": {"helio_distance_au": 9.99, "max_rel_err": 0.001}},
        _impl=fake_impl,
    )
    assert out["reference_answer"] == 9.99
    assert out["gt_version"] == "V4"


# ---------------------------------------------------------------------------
# sv_classify_v
# ---------------------------------------------------------------------------


def test_classify_v_mocked(monkeypatch):
    """Default mock mode never hits network; envelope shape is correct."""
    # Make sure SV_E2E is off
    monkeypatch.delenv("SV_E2E", raising=False)

    # Sentinel: confirm no real LLM call by verifying network helpers are not called.
    called = {"live": False}

    def boom(*_args, **_kw):
        called["live"] = True
        raise AssertionError("live impl must not be called in mock mode")

    monkeypatch.setattr(pr, "_classify_v_live_impl", boom)

    record = {
        "qid": "scanqa-fake-1",
        "dataset": "scanqa",
        "question": "what color is the chair?",
        "answer": "white",
    }
    out = pr._handle_classify_v({"record": record})
    assert called["live"] is False
    assert out["V_class"] == "none"
    assert out["model_used"] == "gemini-3.1-pro-preview"
    assert isinstance(out["latency_ms"], int)
    assert out["token_cost"] == {"input": 0, "output": 0, "usd": 0.0}
    assert out["reasoning_class"] == 0.0
    assert out["knowledge_class"] == 0.0


def test_classify_v_custom_model_echoed(monkeypatch):
    monkeypatch.delenv("SV_E2E", raising=False)
    record = {"qid": "x", "dataset": "ds", "question": "q", "answer": "a"}
    out = pr._handle_classify_v({"record": record, "model": "gemini-2.5-flash"})
    assert out["model_used"] == "gemini-2.5-flash"


def test_classify_v_with_injected_impl():
    def fake_impl(rec: Mapping[str, Any], model: str) -> dict:
        return {
            "V_class": "V3",
            "reasoning_class": 1.0,
            "knowledge_class": 1.0,
            "rationale": "fake",
            "model_used": model,
            "token_cost": {"input": 10, "output": 5, "usd": 0.001},
            "latency_ms": 42,
        }

    out = pr._handle_classify_v(
        {"record": {"dataset": "m3d-refseg", "question": "seg the liver", "answer": "[SEG]"}},
        _impl=fake_impl,
    )
    assert out["V_class"] == "V3"
    assert out["reasoning_class"] == 1.0
    assert out["knowledge_class"] == 1.0
    assert out["latency_ms"] == 42
    assert out["token_cost"]["input"] == 10


def test_classify_v_strip_rk_when_not_requested():
    def fake_impl(rec, model):
        return {"V_class": "V1", "rationale": "", "model_used": model,
                "token_cost": {"input": 0, "output": 0, "usd": 0},
                "latency_ms": 0, "reasoning_class": 0.5, "knowledge_class": 0.5}

    out = pr._handle_classify_v(
        {"record": {"question": "q"}, "with_reasoning_knowledge": False},
        _impl=fake_impl,
    )
    assert "reasoning_class" not in out
    assert "knowledge_class" not in out


def test_classify_v_missing_qid_and_record():
    out = pr._handle_classify_v({})
    assert "error" in out


def test_classify_v_qid_not_found(monkeypatch, tmp_path):
    """qid that does not exist anywhere → error envelope."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    (tmp_path / "datasets" / "_matched").mkdir(parents=True)
    (tmp_path / "datasets" / "_matched" / "index.json").write_text(
        json.dumps({"files": []})
    )
    out = pr._handle_classify_v({"qid": "does-not-exist"})
    assert "error" in out


def test_classify_v_qid_lookup(monkeypatch, tmp_path):
    """qid found in datasets/_matched is used as the record."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "scanqa", "path_repo": "datasets/_matched/scanqa.json", "count": 1}]
    }))
    (matched / "scanqa.json").write_text(json.dumps({
        "records": [{"qid": "scanqa-x-1", "dataset": "scanqa", "question": "Q?", "answer": "A"}]
    }))

    seen = {}

    def fake_impl(rec, model):
        seen["rec"] = dict(rec)
        return {"V_class": "none", "rationale": "", "model_used": model,
                "token_cost": {"input": 0, "output": 0, "usd": 0},
                "latency_ms": 1, "reasoning_class": 0.0, "knowledge_class": 0.0}

    out = pr._handle_classify_v({"qid": "scanqa-x-1"}, _impl=fake_impl)
    assert out["V_class"] == "none"
    assert seen["rec"]["qid"] == "scanqa-x-1"


def test_classify_v_record_must_be_object():
    out = pr._handle_classify_v({"record": "not-a-dict"})
    assert "error" in out


def test_classify_v_impl_propagates_error():
    def fake_impl(rec, model):
        return {"error": "synthetic"}

    out = pr._handle_classify_v({"record": {"question": "x"}}, _impl=fake_impl)
    assert "error" in out
    assert out["model_used"] == "gemini-3.1-pro-preview"


def test_classify_v_e2e_gated(monkeypatch):
    """When SV_E2E is unset (default), no API key is required."""
    monkeypatch.delenv("SV_E2E", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("MINDRA_API", raising=False)
    monkeypatch.delenv("MINDRA_BASE_URL", raising=False)

    if os.environ.get("SV_E2E") == "1":
        pytest.skip("SV_E2E=1 explicitly set in outer env")

    out = pr._handle_classify_v({"record": {"question": "x"}})
    # Mock impl returns a fine envelope without any creds.
    assert out["V_class"] == "none"
    assert "error" not in out


# ---------------------------------------------------------------------------
# sv_get_v_classify_pilot_summary
# ---------------------------------------------------------------------------


def test_get_v_classify_pilot_summary_all():
    out = pr._handle_pilot_summary({})
    names = sorted(d["name"] for d in out["datasets"])
    assert names == sorted(["scanqa", "sqa3d", "m3d-refseg"])


def test_get_v_classify_pilot_summary_m3d_refseg_v3_100():
    out = pr._handle_pilot_summary({"dataset": "m3d-refseg"})
    assert len(out["datasets"]) == 1
    ds = out["datasets"][0]
    assert ds["name"] == "m3d-refseg"
    assert ds["per_class_pct"]["V3"] == 100.0
    assert ds["per_class_pct"]["none"] == 0.0
    assert ds["n"] == 100


def test_get_v_classify_pilot_summary_scanqa_all_none():
    out = pr._handle_pilot_summary({"dataset": "scanqa"})
    ds = out["datasets"][0]
    assert ds["per_class_pct"]["none"] == 100.0


def test_get_v_classify_pilot_summary_sqa3d_v2_3():
    out = pr._handle_pilot_summary({"dataset": "sqa3d"})
    ds = out["datasets"][0]
    assert ds["per_class_pct"]["V2"] == 3.0
    assert ds["per_class_pct"]["none"] == 97.0


def test_get_v_classify_pilot_summary_unknown_dataset():
    out = pr._handle_pilot_summary({"dataset": "made-up"})
    assert "error" in out


def test_get_v_classify_pilot_summary_immutable():
    """Two consecutive calls return equal-but-not-same-object dicts."""
    a = pr._handle_pilot_summary({})
    b = pr._handle_pilot_summary({})
    assert a == b
    # Mutating the returned datasets must not affect frozen constants.
    a["datasets"][0]["per_class_pct"]["V1"] = 99.9
    c = pr._handle_pilot_summary({})
    assert c["datasets"][0]["per_class_pct"]["V1"] == 0.0


# ---------------------------------------------------------------------------
# sv_validate_matched_index
# ---------------------------------------------------------------------------


def _write_minimal_matched(root: Path, *, asset_present: bool, with_mask: bool = False,
                            mask_present: bool = True) -> None:
    matched = root / "datasets" / "_matched"
    matched.mkdir(parents=True)
    if asset_present:
        asset_dir = root / "datasets" / "fake_ds"
        asset_dir.mkdir(parents=True)
        (asset_dir / "scene.ply").write_text("x")
    if with_mask:
        (root / "datasets" / "fake_ds" / "mask.nii.gz").write_text("m" if mask_present else "")
        if not mask_present:
            (root / "datasets" / "fake_ds" / "mask.nii.gz").unlink()

    rec: dict = {
        "qid": "fake-1",
        "dataset": "scanqa",
        "path_repo": "datasets/fake_ds/scene.ply",
        "url_viewer": "/datasets/fake_ds/scene.ply",
    }
    if with_mask:
        rec["extra"] = {"mask_path_repo": "datasets/fake_ds/mask.nii.gz"}

    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "scanqa", "path_repo": "datasets/_matched/scanqa.json", "count": 1}]
    }))
    (matched / "scanqa.json").write_text(json.dumps({"records": [rec]}))


def test_validate_matched_index_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    _write_minimal_matched(tmp_path, asset_present=True)
    out = pr._handle_validate_matched_index({})
    assert out["ok"] is True
    assert out["missing_assets"] == []
    assert out["orphan_entries"] == []
    assert out["counts_by_dataset"] == {"scanqa": 1}


def test_validate_matched_index_with_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    _write_minimal_matched(tmp_path, asset_present=False)
    out = pr._handle_validate_matched_index({})
    assert out["ok"] is False
    assert len(out["missing_assets"]) == 1
    assert out["missing_assets"][0]["qid"] == "fake-1"


def test_validate_matched_index_missing_mask(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    _write_minimal_matched(tmp_path, asset_present=True, with_mask=True, mask_present=False)
    out = pr._handle_validate_matched_index({})
    assert out["ok"] is False
    assert len(out["missing_assets"]) == 1
    assert "mask" in out["missing_assets"][0]["path"]


def test_validate_matched_index_filter_by_datasets(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    _write_minimal_matched(tmp_path, asset_present=True)
    out = pr._handle_validate_matched_index({"datasets": ["sqa3d"]})
    # No matching datasets → empty counts, still ok.
    assert out["counts_by_dataset"] == {}
    assert out["ok"] is True


def test_validate_matched_index_missing_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out = pr._handle_validate_matched_index({})
    assert "error" in out


def test_validate_matched_index_url_viewer_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    asset_dir = tmp_path / "datasets" / "fake_ds"
    asset_dir.mkdir(parents=True)
    (asset_dir / "scene.ply").write_text("x")

    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "scanqa", "path_repo": "datasets/_matched/scanqa.json", "count": 1}]
    }))
    (matched / "scanqa.json").write_text(json.dumps({"records": [{
        "qid": "fake-1",
        "dataset": "scanqa",
        "path_repo": "datasets/fake_ds/scene.ply",
        "url_viewer": "/wrong/url",
    }]}))

    out = pr._handle_validate_matched_index({})
    assert out["ok"] is False
    assert any("url_viewer" in o.get("reason", "") for o in out["orphan_entries"])


def test_validate_matched_index_corrupt_per_dataset(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "scanqa", "path_repo": "datasets/_matched/scanqa.json", "count": 1}]
    }))
    (matched / "scanqa.json").write_text("{not-json")
    out = pr._handle_validate_matched_index({})
    assert out["ok"] is False
    assert any("decode" in o.get("reason", "") for o in out["orphan_entries"])


def test_validate_matched_index_explicit_root(tmp_path):
    """The _matched_root kwarg lets tests skip env var manipulation."""
    matched_root = tmp_path / "_matched"
    matched_root.mkdir(parents=True)
    (matched_root / "index.json").write_text(json.dumps({"files": []}))
    out = pr._handle_validate_matched_index({}, _matched_root=matched_root)
    assert out["ok"] is True
    assert out["counts_by_dataset"] == {}


# ---------------------------------------------------------------------------
# sv_run_v_validation
# ---------------------------------------------------------------------------


def test_run_v_validation_smoke():
    """Without injection, returns an envelope (real module may be absent)."""
    out = pr._handle_run_v_validation({})
    assert "ok" in out
    assert "failures" in out
    assert "warnings" in out
    assert out["v_levels"] == ["V1", "V2", "V3", "V4", "V5", "V6"]


def test_run_v_validation_injected_pass():
    def fake_impl(spec_path: Path, v_levels: list[str]) -> dict:
        return {"ok": True, "failures": [], "warnings": []}

    out = pr._handle_run_v_validation({}, _impl=fake_impl)
    assert out["ok"] is True


def test_run_v_validation_injected_fail():
    def fake_impl(spec_path: Path, v_levels: list[str]) -> dict:
        return {"ok": False, "failures": ["bad spec"], "warnings": []}

    out = pr._handle_run_v_validation({}, _impl=fake_impl)
    assert out["ok"] is False
    assert out["failures"] == ["bad spec"]


def test_run_v_validation_impl_raises():
    def fake_impl(spec_path: Path, v_levels: list[str]) -> dict:
        raise RuntimeError("blown")

    out = pr._handle_run_v_validation({}, _impl=fake_impl)
    assert out["ok"] is False
    assert "error" in out


def test_run_v_validation_impl_returns_nonsense():
    def fake_impl(spec_path: Path, v_levels: list[str]) -> dict:
        return "not a mapping"  # type: ignore[return-value]

    out = pr._handle_run_v_validation({}, _impl=fake_impl)
    assert out["ok"] is False
    assert "error" in out


def test_run_v_validation_v_levels_passthrough():
    seen = {}

    def fake_impl(spec_path: Path, v_levels: list[str]) -> dict:
        seen["v"] = list(v_levels)
        return {"ok": True, "failures": [], "warnings": []}

    pr._handle_run_v_validation({"v_levels": ["V1", "V6"]}, _impl=fake_impl)
    assert seen["v"] == ["V1", "V6"]


def test_run_v_validation_spec_path_passthrough(tmp_path):
    seen = {}

    def fake_impl(spec_path: Path, v_levels: list[str]) -> dict:
        seen["spec"] = spec_path
        return {"ok": True, "failures": [], "warnings": []}

    pr._handle_run_v_validation({"spec_path": str(tmp_path)}, _impl=fake_impl)
    assert seen["spec"] == tmp_path


# ---------------------------------------------------------------------------
# Schema-loading edge cases
# ---------------------------------------------------------------------------


def test_load_schema_missing_falls_back():
    s = pr._load_schema("definitely-not-a-real-schema")
    assert s == {"type": "object"}


def test_load_schema_existing_returns_dict():
    s = pr._load_schema("sv_score_astronomy")
    assert s["title"] == "sv_score_astronomy"
    assert s["type"] == "object"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_viewer_url_matches_path_exact():
    assert pr._viewer_url_matches_path("/foo/bar.ply", "foo/bar.ply")
    assert pr._viewer_url_matches_path("foo/bar.ply", "foo/bar.ply")


def test_viewer_url_matches_path_mismatch():
    assert not pr._viewer_url_matches_path("/foo/bar.ply", "baz/qux.ply")
    assert not pr._viewer_url_matches_path(None, "foo")  # type: ignore[arg-type]


def test_lookup_record_by_qid_no_index(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    assert pr._lookup_record_by_qid("anything") is None


def test_lookup_record_by_qid_bad_index(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text("{not-json")
    assert pr._lookup_record_by_qid("x") is None


def test_lookup_record_by_qid_skips_corrupt_per_dataset(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "x", "path_repo": "datasets/_matched/x.json", "count": 1}]
    }))
    (matched / "x.json").write_text("{not-json")
    assert pr._lookup_record_by_qid("x-1") is None


def test_lookup_record_by_qid_skips_missing_per_dataset(monkeypatch, tmp_path):
    """An index entry pointing to a missing per-dataset file is skipped, not raised."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "x", "path_repo": "datasets/_matched/x.json", "count": 1}]
    }))
    # x.json deliberately not written
    assert pr._lookup_record_by_qid("anything") is None


# ---------------------------------------------------------------------------
# Default in-process impls (exercise more lines without network)
# ---------------------------------------------------------------------------


def test_score_astronomy_default_impl_unknown_qid(monkeypatch):
    """Default impl returns error envelope for unknown question_id."""
    out = pr._score_astronomy_default_impl("definitely-not-a-question", "x")
    # tools.score_astronomy may or may not import here; either path is fine
    # as long as we get an error envelope.
    assert "error" in out


def test_score_astronomy_default_impl_load_failure(monkeypatch):
    """Force load_questions to raise → error envelope."""
    import tools.score_astronomy as score_mod

    def boom():
        raise RuntimeError("synthetic load failure")

    monkeypatch.setattr(score_mod, "load_questions", boom)
    out = pr._score_astronomy_default_impl("x", "y")
    assert "error" in out
    assert "load_questions failed" in out["error"]


def test_score_astronomy_default_impl_compute_truth_error(monkeypatch):
    """compute_truth returning {error: ...} surfaces as an error envelope."""
    import tools.score_astronomy as score_mod

    monkeypatch.setattr(score_mod, "load_questions",
                        lambda: [{"id": "q1", "v_class": "V99"}])

    def truth_err(_q):
        return {"error": "unsupported v_class"}

    monkeypatch.setattr(score_mod, "compute_truth", truth_err)
    out = pr._score_astronomy_default_impl("q1", "any")
    assert "error" in out
    assert "unsupported v_class" in out["error"]


def test_score_astronomy_default_impl_compute_truth_raises(monkeypatch):
    import tools.score_astronomy as score_mod

    monkeypatch.setattr(score_mod, "load_questions",
                        lambda: [{"id": "q1", "v_class": "V2"}])

    def boom(_q):
        raise RuntimeError("xx")

    monkeypatch.setattr(score_mod, "compute_truth", boom)
    out = pr._score_astronomy_default_impl("q1", "any")
    assert "compute_truth failed" in out["error"]


def test_score_astronomy_default_impl_grade_raises(monkeypatch):
    import tools.score_astronomy as score_mod

    monkeypatch.setattr(score_mod, "load_questions",
                        lambda: [{"id": "q1", "v_class": "V2"}])
    monkeypatch.setattr(score_mod, "compute_truth",
                        lambda _q: {"ground_truth": "x"})

    def boom(*_a, **_kw):
        raise RuntimeError("grade boom")

    monkeypatch.setattr(score_mod, "grade", boom)
    out = pr._score_astronomy_default_impl("q1", "any")
    assert "grade failed" in out["error"]


def test_score_astronomy_default_impl_success(monkeypatch):
    """End-to-end: a stub V2 question grades successfully through the default impl."""
    import tools.score_astronomy as score_mod

    monkeypatch.setattr(score_mod, "load_questions",
                        lambda: [{"id": "q1", "v_class": "V2"}])
    monkeypatch.setattr(score_mod, "compute_truth",
                        lambda _q: {"ground_truth": "edge-on"})

    def grader(q, ans, truth):
        return {"q": q["id"], "v_class": q["v_class"], "pass": True,
                "expected": truth["ground_truth"], "got": ans}

    monkeypatch.setattr(score_mod, "grade", grader)
    out = pr._score_astronomy_default_impl("q1", "edge-on")
    assert out["result"]["pass"] is True
    assert out["truth"]["ground_truth"] == "edge-on"


def test_score_astronomy_uses_default_impl_end_to_end(monkeypatch):
    """_handle_score_astronomy with default impl + monkeypatched score_mod."""
    import tools.score_astronomy as score_mod

    monkeypatch.setattr(score_mod, "load_questions",
                        lambda: [{"id": "q1", "v_class": "V2"}])
    monkeypatch.setattr(score_mod, "compute_truth",
                        lambda _q: {"ground_truth": "face-on"})
    monkeypatch.setattr(
        score_mod, "grade",
        lambda q, ans, truth: {
            "q": q["id"], "v_class": q["v_class"],
            "pass": ans == truth["ground_truth"],
            "expected": truth["ground_truth"], "got": ans,
        },
    )
    out = pr._handle_score_astronomy(
        {"question_id": "q1", "predicted_answer": "face-on"}
    )
    assert out["pass"] is True
    assert out["reference_answer"] == "face-on"


def test_classify_v_live_impl_missing_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MINDRA_BASE_URL", raising=False)
    monkeypatch.delenv("MINDRA_API", raising=False)
    out = pr._classify_v_live_impl({"question": "x"}, "model-x")
    assert "error" in out
    assert "missing" in out["error"]


def test_classify_v_live_impl_chat_failure(monkeypatch):
    """When creds present but chat call raises, returns error envelope."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://fake.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    import scripts.v_classify.client as client_mod

    class FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        def complete_with_retry(self, **_kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(client_mod, "ChatClient", FakeClient)
    out = pr._classify_v_live_impl({"question": "x"}, "model-x")
    assert "error" in out
    assert "chat.complete failed" in out["error"]


def test_classify_v_live_impl_parse_failure(monkeypatch):
    """When chat succeeds but returns non-JSON content."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://fake.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    import scripts.v_classify.client as client_mod

    class FakeRes:
        content = "not-json garbage"
        latency_ms = 1.0
        prompt_tokens = 1
        completion_tokens = 1
        reasoning_tokens = 0

    class FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        def complete_with_retry(self, **_kwargs):
            return FakeRes()

    monkeypatch.setattr(client_mod, "ChatClient", FakeClient)
    out = pr._classify_v_live_impl({"question": "x"}, "model-x")
    assert "error" in out
    assert "classifier output invalid" in out["error"]


def test_classify_v_live_impl_happy(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://fake.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    import scripts.v_classify.client as client_mod

    payload = {
        "v1": 0.0, "v2": 0.0, "v3": 1.0, "v4": 0.0, "v5": 0.0, "v6": 0.0,
        "reasoning": 1.0, "knowledge": 1.0,
        "dominant_v": "V3", "rationale": "synthetic",
    }

    class FakeRes:
        content = json.dumps(payload)
        latency_ms = 12.5
        prompt_tokens = 100
        completion_tokens = 50
        reasoning_tokens = 25

    class FakeClient:
        def __init__(self, *_a, **_kw):
            pass

        def complete_with_retry(self, **_kwargs):
            return FakeRes()

    monkeypatch.setattr(client_mod, "ChatClient", FakeClient)
    out = pr._classify_v_live_impl({"dataset": "m3d-refseg", "question": "x"}, "model-x")
    assert out["V_class"] == "V3"
    assert out["reasoning_class"] == 1.0
    assert out["knowledge_class"] == 1.0
    assert out["model_used"] == "model-x"
    assert out["token_cost"]["input"] == 100
    assert out["axes"]["v3"] == 1.0


def test_classify_v_live_routed_when_sv_e2e_set(monkeypatch):
    """When SV_E2E=1 the live impl is invoked; monkeypatch it to a sentinel."""
    monkeypatch.setenv("SV_E2E", "1")
    sentinel = {"V_class": "V5", "rationale": "live-stub",
                "model_used": "stub", "token_cost": {"input": 1, "output": 1, "usd": 0.0},
                "latency_ms": 7, "reasoning_class": 0.5, "knowledge_class": 0.5}
    monkeypatch.setattr(pr, "_classify_v_live_impl", lambda rec, m: sentinel)
    out = pr._handle_classify_v({"record": {"question": "?"}})
    assert out["V_class"] == "V5"
    assert out["latency_ms"] == 7


def test_run_v_validation_default_impl_no_entry(monkeypatch):
    """No discoverable entry point → warning envelope, ok=False."""
    # Make every candidate import fail.
    import importlib as _imp
    real_import = _imp.import_module

    def fake_import(name, *a, **kw):
        if name.startswith("tools.v_validation"):
            raise ImportError("no such module in this test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import)
    out = pr._run_v_validation_default_impl(Path("/tmp/spec"), ["V1"])
    assert out["ok"] is False
    assert out["warnings"]


def test_run_v_validation_default_impl_calls_validate(monkeypatch):
    """Inject a fake module with a `validate` callable."""
    class FakeMod:
        @staticmethod
        def validate(spec_path: str, v_levels: list[str]) -> dict:
            return {"ok": True, "failures": [], "warnings": ["one"]}

    real_import = pr.importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "tools.v_validation":
            return FakeMod
        return real_import(name, *a, **kw)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import)
    out = pr._run_v_validation_default_impl(Path("/tmp/spec"), ["V1"])
    assert out["ok"] is True
    assert out["warnings"] == ["one"]


def test_run_v_validation_default_impl_callable_raises(monkeypatch):
    """Entry callable raises → failures captured."""
    class FakeMod:
        @staticmethod
        def validate(spec_path: str, v_levels: list[str]) -> dict:
            raise ValueError("kaboom")

    real_import = pr.importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "tools.v_validation":
            return FakeMod
        return real_import(name, *a, **kw)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import)
    out = pr._run_v_validation_default_impl(Path("/tmp/spec"), ["V1"])
    assert out["ok"] is False
    assert any("kaboom" in f for f in out["failures"])


def test_run_v_validation_default_impl_returns_nonmapping(monkeypatch):
    """Entry callable returns a non-mapping → wrapped as {ok=True, result=...}."""
    class FakeMod:
        @staticmethod
        def validate(spec_path: str, v_levels: list[str]):
            return "all good"

    real_import = pr.importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "tools.v_validation":
            return FakeMod
        return real_import(name, *a, **kw)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import)
    out = pr._run_v_validation_default_impl(Path("/tmp/spec"), ["V1"])
    assert out["ok"] is True
    assert out["result"] == "all good"


def test_run_v_validation_default_impl_typeerror_fallback(monkeypatch):
    """If kwargs signature TypeErrors, the wrapper retries positional-only."""
    calls = {"n": 0}

    class FakeMod:
        @staticmethod
        def validate(spec_path: str) -> dict:
            calls["n"] += 1
            return {"ok": True, "failures": [], "warnings": []}

    real_import = pr.importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "tools.v_validation":
            return FakeMod
        return real_import(name, *a, **kw)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import)
    out = pr._run_v_validation_default_impl(Path("/tmp/spec"), ["V1"])
    assert out["ok"] is True
    assert calls["n"] == 1


def test_run_v_validation_default_impl_typeerror_then_raises(monkeypatch):
    """If kwargs and positional both fail, returns failures envelope."""
    class FakeMod:
        @staticmethod
        def validate(spec_path):
            raise RuntimeError("posi-fail")

    real_import = pr.importlib.import_module

    def fake_import(name, *a, **kw):
        if name == "tools.v_validation":
            return FakeMod
        return real_import(name, *a, **kw)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import)
    out = pr._run_v_validation_default_impl(Path("/tmp/spec"), ["V1"])
    assert out["ok"] is False
    assert any("posi-fail" in f for f in out["failures"])


def test_validate_matched_index_missing_path_repo(monkeypatch, tmp_path):
    """Record without path_repo → orphan entry."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "scanqa", "path_repo": "datasets/_matched/scanqa.json", "count": 1}]
    }))
    (matched / "scanqa.json").write_text(json.dumps({"records": [{"qid": "no-path"}]}))
    out = pr._handle_validate_matched_index({})
    assert out["ok"] is False
    assert any(o.get("reason") == "missing path_repo" for o in out["orphan_entries"])


def test_validate_matched_index_missing_per_dataset_file(monkeypatch, tmp_path):
    """Index references a per-dataset JSON that does not exist."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text(json.dumps({
        "files": [{"dataset": "scanqa", "path_repo": "datasets/_matched/scanqa.json", "count": 1}]
    }))
    out = pr._handle_validate_matched_index({})
    assert out["ok"] is False
    assert any("missing per-dataset" in o.get("reason", "") for o in out["orphan_entries"])


def test_validate_matched_index_bad_index_json(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    matched = tmp_path / "datasets" / "_matched"
    matched.mkdir(parents=True)
    (matched / "index.json").write_text("not-json {")
    out = pr._handle_validate_matched_index({})
    assert "error" in out


def test_classify_v_qid_falsy_record_falsy():
    """Empty record dict still counts as 'record provided'."""
    def fake_impl(rec, model):
        return {"V_class": "none", "rationale": "", "model_used": model,
                "token_cost": {"input": 0, "output": 0, "usd": 0},
                "latency_ms": 0, "reasoning_class": 0.0, "knowledge_class": 0.0}

    out = pr._handle_classify_v({"record": {}}, _impl=fake_impl)
    assert out["V_class"] == "none"


def test_classify_v_default_mock_no_creds_required(monkeypatch):
    """Mock impl needs no creds; live impl never invoked when SV_E2E not set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SV_E2E", raising=False)

    # Confirm mock is the one used.
    out = pr._classify_v_mock_impl({"question": "x"}, "model-z")
    assert out["V_class"] == "none"
    assert out["model_used"] == "model-z"
    assert out["latency_ms"] == 0
