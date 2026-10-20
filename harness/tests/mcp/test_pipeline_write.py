"""Tests for sci3d_agent.mcp.tools.pipeline_write (Phase 2 / Agent E2).

All tests inject impls / runners so the suite never makes a real LLM call and
never spawns a real subprocess. Set ``SV_E2E=1`` to opt-in to live execution
(not required by this suite).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

from sci3d_agent.mcp.tools import pipeline_write as pw


# ---------------------------------------------------------------------------
# Tool registration / smoke
# ---------------------------------------------------------------------------


def test_tools_count_is_three():
    assert len(pw.TOOLS) == 3


def test_tools_names():
    names = sorted(t.name for t in pw.TOOLS)
    assert names == sorted(
        [
            "sv_classify_v_batch",
            "sv_rebuild_matched_index",
            "sv_run_m3d_pipeline",
        ]
    )


def test_tools_categories_all_E():
    assert all(t.category == "E" for t in pw.TOOLS)


def test_tools_have_object_schemas():
    for spec in pw.TOOLS:
        assert isinstance(spec.input_schema, dict)
        assert spec.input_schema.get("type") == "object"


def test_load_schema_missing_falls_back():
    assert pw._load_schema("nope-not-a-real-schema") == {"type": "object"}


def test_load_schema_existing_returns_dict():
    s = pw._load_schema("sv_classify_v_batch")
    assert s["title"] == "sv_classify_v_batch"


# ---------------------------------------------------------------------------
# Shared fakes for sv_classify_v_batch
# ---------------------------------------------------------------------------


def _canned_records(n: int, prefix: str = "scanqa-x-") -> list[dict]:
    return [
        {
            "qid": f"{prefix}{i}",
            "dataset": "scanqa",
            "question": f"q{i}?",
            "answer": f"a{i}",
        }
        for i in range(n)
    ]


def _client_sentinel() -> object:
    return object()


def _make_canned_classify(dominant: str = "none"):
    def _fake(client, model, rec, max_attempts):
        return {
            "qid": rec["qid"],
            "dataset": rec.get("dataset"),
            "split": rec.get("split"),
            "scene_or_case_id": rec.get("scene_or_case_id"),
            "question": rec.get("question"),
            "answer": rec.get("answer"),
            "status": "ok",
            "v1": 0.0, "v2": 0.0, "v3": 0.0, "v4": 0.0, "v5": 0.0, "v6": 0.0,
            "reasoning": 0.0, "knowledge": 0.0,
            "dominant_v": dominant,
            "rationale": "canned",
            "wall_ms": 1.0,
            "latency_ms": 1.0,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "reasoning_tokens": 0,
        }

    return _fake


# ---------------------------------------------------------------------------
# sv_classify_v_batch
# ---------------------------------------------------------------------------


def test_classify_v_batch_default_mocked(tmp_path, monkeypatch):
    """Batch of 3 records → 3 lines in output; per_class_counts matches mock."""
    monkeypatch.delenv("SV_E2E", raising=False)
    out_path = tmp_path / "out.jsonl"
    records = _canned_records(3)

    result = pw._handle_classify_v_batch(
        {
            "dataset": "scanqa",
            "output_jsonl": str(out_path),
            "limit": 3,
        },
        _load_records=lambda dataset, *, skip_no_answer: records,
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=_client_sentinel,
        _classify_one=_make_canned_classify("V2"),
        _already_done=lambda p: set(),
    )

    assert result["output_path"] == str(out_path)
    assert result["n_items"] == 3
    assert result["n_done"] == 3
    assert result["per_class_counts"]["V2"] == 3
    assert result["per_class_counts"]["none"] == 0
    assert result["total_cost_usd"] > 0.0
    assert isinstance(result["elapsed_s"], float)

    lines = out_path.read_text().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert {p["qid"] for p in parsed} == {r["qid"] for r in records}


def test_classify_v_batch_resumes_via_already_done(tmp_path, monkeypatch):
    """Pre-existing JSONL with 2 qids → only 1 undone qid gets classified."""
    monkeypatch.delenv("SV_E2E", raising=False)
    out_path = tmp_path / "resume.jsonl"
    records = _canned_records(3)

    # Pre-populate output with rows for first 2 qids; cannot use override path
    # because we must trigger overwrite-refusal. Instead use a different
    # output_jsonl in args=None (default) and inject `_already_done` returning
    # the first two qids. We also need the existing rows to be readable so
    # final counts include them. Pre-create the file.
    pre_rows = [
        {"qid": r["qid"], "status": "ok", "dominant_v": "V3", "prompt_tokens": 10,
         "completion_tokens": 5}
        for r in records[:2]
    ]
    # We cannot pre-populate the user-supplied path (overwrite refused). So we
    # take a different route: use default output path and stub already_done.
    # Easier: drop output_jsonl from args, redirect repo_root() to tmp_path so
    # default output is under tmp_path.
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(pw, "_utc_stamp", lambda: "FIXED")
    default_out = tmp_path / "scripts" / "v_classify" / "out" / "scanqa__FIXED.jsonl"
    default_out.parent.mkdir(parents=True, exist_ok=True)
    default_out.write_text("\n".join(json.dumps(r) for r in pre_rows) + "\n")

    classify_calls: list[str] = []

    def _capture(client, model, rec, max_attempts):
        classify_calls.append(rec["qid"])
        return _make_canned_classify("V2")(client, model, rec, max_attempts)

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "limit": 3},
        _load_records=lambda dataset, *, skip_no_answer: records,
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=_client_sentinel,
        _classify_one=_capture,
        _already_done=lambda p: {records[0]["qid"], records[1]["qid"]},
    )

    assert classify_calls == [records[2]["qid"]]
    assert result["n_items"] == 3
    assert result["n_done"] == 3
    # 2 pre-existing V3 rows + 1 new V2
    assert result["per_class_counts"]["V3"] == 2
    assert result["per_class_counts"]["V2"] == 1


def test_classify_v_batch_refuses_to_overwrite(tmp_path, monkeypatch):
    """Existing non-empty file at user-supplied output_jsonl → error envelope; no writes."""
    monkeypatch.delenv("SV_E2E", raising=False)
    out_path = tmp_path / "existing.jsonl"
    out_path.write_text('{"qid": "old"}\n')

    before_mtime = out_path.stat().st_mtime_ns
    before_text = out_path.read_text()

    boom_called = {"n": 0}

    def boom_client():
        boom_called["n"] += 1
        return _client_sentinel()

    result = pw._handle_classify_v_batch(
        {
            "dataset": "scanqa",
            "output_jsonl": str(out_path),
            "limit": 1,
        },
        _load_records=lambda dataset, *, skip_no_answer: _canned_records(1),
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=boom_client,
        _classify_one=_make_canned_classify(),
        _already_done=lambda p: set(),
    )

    assert "error" in result
    assert "refusing to overwrite" in result["error"]
    assert boom_called["n"] == 0  # no client init
    assert out_path.read_text() == before_text
    assert out_path.stat().st_mtime_ns == before_mtime


def test_classify_v_batch_no_real_llm_unless_e2e(tmp_path, monkeypatch):
    """SV_E2E unset → ChatClient is never instantiated.

    We verify by patching the real client module and ensuring it is not touched.
    The handler is invoked with an injected _build_client that records its use.
    """
    monkeypatch.delenv("SV_E2E", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("MINDRA_API", raising=False)
    monkeypatch.delenv("MINDRA_BASE_URL", raising=False)

    import scripts.v_classify.client as client_mod

    def boom(*_a, **_kw):
        raise AssertionError("real ChatClient must not be instantiated in this test")

    monkeypatch.setattr(client_mod, "ChatClient", boom)

    out_path = tmp_path / "out.jsonl"
    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "output_jsonl": str(out_path), "limit": 1},
        _load_records=lambda dataset, *, skip_no_answer: _canned_records(1),
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=_client_sentinel,
        _classify_one=_make_canned_classify(),
        _already_done=lambda p: set(),
    )
    assert "error" not in result
    assert result["n_done"] == 1


def test_classify_v_batch_missing_dataset():
    result = pw._handle_classify_v_batch({})
    assert "error" in result


def test_classify_v_batch_bad_concurrency():
    result = pw._handle_classify_v_batch({"dataset": "scanqa", "max_concurrency": 99})
    assert "error" in result


def test_classify_v_batch_load_records_raises():
    def boom(*_a, **_kw):
        raise RuntimeError("dataset missing")

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "limit": 1},
        _load_records=boom,
    )
    assert "error" in result


def test_classify_v_batch_load_records_systemexit():
    def boom(*_a, **_kw):
        raise SystemExit("missing file")

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "limit": 1},
        _load_records=boom,
    )
    assert "error" in result
    assert "load_records failed" in result["error"]


def test_classify_v_batch_sample_records_raises():
    def boom(*_a, **_kw):
        raise RuntimeError("xx")

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "limit": 1},
        _load_records=lambda dataset, *, skip_no_answer: _canned_records(3),
        _sample_records=boom,
    )
    assert "error" in result


def test_classify_v_batch_zero_resolved_size():
    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "sample": 0},
        _load_records=lambda dataset, *, skip_no_answer: _canned_records(3),
    )
    assert "error" in result
    assert "zero" in result["error"]


def test_classify_v_batch_client_init_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("SV_E2E", raising=False)
    out_path = tmp_path / "out.jsonl"

    def boom():
        raise RuntimeError("creds missing")

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "output_jsonl": str(out_path), "limit": 1},
        _load_records=lambda dataset, *, skip_no_answer: _canned_records(1),
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=boom,
        _classify_one=_make_canned_classify(),
        _already_done=lambda p: set(),
    )
    assert "error" in result
    assert "chat-client init failed" in result["error"]


def test_classify_v_batch_individual_classify_raises(tmp_path, monkeypatch):
    """A single classify_one failure is captured as an error row, not raised."""
    out_path = tmp_path / "out.jsonl"

    def boom(*_a, **_kw):
        raise RuntimeError("model exploded")

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "output_jsonl": str(out_path), "limit": 1},
        _load_records=lambda dataset, *, skip_no_answer: _canned_records(1),
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=_client_sentinel,
        _classify_one=boom,
        _already_done=lambda p: set(),
    )
    assert result["n_done"] == 1
    # All rows are errors → no per-class counts at all
    assert sum(result["per_class_counts"].values()) == 0


def test_classify_v_batch_concurrency_path(tmp_path):
    out_path = tmp_path / "out.jsonl"
    records = _canned_records(5)

    result = pw._handle_classify_v_batch(
        {
            "dataset": "scanqa",
            "output_jsonl": str(out_path),
            "limit": 5,
            "max_concurrency": 4,
        },
        _load_records=lambda dataset, *, skip_no_answer: records,
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=_client_sentinel,
        _classify_one=_make_canned_classify("V3"),
        _already_done=lambda p: set(),
    )

    assert result["n_done"] == 5
    assert result["per_class_counts"]["V3"] == 5


def test_classify_v_batch_sample_fraction(tmp_path):
    out_path = tmp_path / "out.jsonl"
    records = _canned_records(10)

    captured: dict[str, int] = {}

    def _sampler(recs, n, seed):
        captured["n"] = n
        return recs[:n]

    pw._handle_classify_v_batch(
        {
            "dataset": "scanqa",
            "output_jsonl": str(out_path),
            "sample": 0.3,
        },
        _load_records=lambda dataset, *, skip_no_answer: records,
        _sample_records=_sampler,
        _build_client=_client_sentinel,
        _classify_one=_make_canned_classify(),
        _already_done=lambda p: set(),
    )
    assert captured["n"] == 3


def test_classify_v_batch_no_todo_returns_existing_summary(tmp_path, monkeypatch):
    """All sampled qids already done in default-path file → no LLM calls."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(pw, "_utc_stamp", lambda: "FIXED")
    default_out = tmp_path / "scripts" / "v_classify" / "out" / "scanqa__FIXED.jsonl"
    default_out.parent.mkdir(parents=True, exist_ok=True)
    default_out.write_text(json.dumps({
        "qid": "scanqa-x-0", "status": "ok", "dominant_v": "V2",
        "prompt_tokens": 10, "completion_tokens": 5,
    }) + "\n")

    records = _canned_records(1)
    classify_calls = {"n": 0}

    def _track(client, model, rec, max_attempts):
        classify_calls["n"] += 1
        return _make_canned_classify()(client, model, rec, max_attempts)

    result = pw._handle_classify_v_batch(
        {"dataset": "scanqa", "limit": 1},
        _load_records=lambda dataset, *, skip_no_answer: records,
        _sample_records=lambda recs, n, seed: recs[:n],
        _build_client=_client_sentinel,
        _classify_one=_track,
        _already_done=lambda p: {records[0]["qid"]},
    )

    assert classify_calls["n"] == 0
    assert result["elapsed_s"] == 0.0


def test_read_jsonl_rows_skips_bad_lines(tmp_path):
    p = tmp_path / "mixed.jsonl"
    p.write_text('{"qid": "a"}\nnot-json\n\n{"qid": "b"}\n')
    rows = pw._read_jsonl_rows(p)
    assert [r["qid"] for r in rows] == ["a", "b"]


def test_read_jsonl_rows_missing():
    assert pw._read_jsonl_rows(Path("/tmp/__doesnt_exist__.jsonl")) == []


def test_estimate_cost_usd_zero():
    assert pw._estimate_cost_usd([]) == 0.0


def test_per_class_counts_unknown_dominant_bucketed_as_none():
    rows = [{"status": "ok", "dominant_v": "V7"}]
    assert pw._per_class_counts(rows)["none"] == 1


def test_resolve_batch_size_limit_caps_to_total():
    assert pw._resolve_batch_size(5, 100, None) == 5


def test_resolve_batch_size_sample_fraction():
    assert pw._resolve_batch_size(100, None, 0.25) == 25


def test_resolve_batch_size_no_args_returns_total():
    assert pw._resolve_batch_size(7, None, None) == 7


# ---------------------------------------------------------------------------
# sv_rebuild_matched_index
# ---------------------------------------------------------------------------


def _make_grouped(dataset_name: str = "scanqa", records: list[dict] | None = None) -> dict:
    if records is None:
        records = [
            {
                "qid": "scanqa-x-1",
                "dataset": "scanqa",
                "split": "val",
                "scene_or_case_id": "sceneA",
                "question": "q?",
                "answer": "a",
                "asset_kind": "mesh",
                "path_repo": "datasets/foo/sceneA.ply",
                "url_viewer": "/datasets/foo/sceneA.ply",
                "extra": {},
            }
        ]
    return {dataset_name: records}


def _write_existing_matched(out_dir: Path, name: str, records: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset": name,
                "generated_at": "old",
                "policy": "strict_present_on_disk",
                "count": len(records),
                "records": records,
            },
            indent=2,
        )
    )


def test_rebuild_matched_index_dry_run_returns_diff_empty(tmp_path, monkeypatch):
    """With matching on-disk state, plan reports 0 changed files."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out_dir = tmp_path / "datasets" / "_matched"
    grouped = _make_grouped()
    _write_existing_matched(out_dir, "scanqa", grouped["scanqa"])

    result = pw._handle_rebuild_matched_index(
        {},  # no dry_run key → defaults to True
        _collect=lambda: grouped,
    )

    assert "plan" in result
    assert result["summary"]["n_changed"] == 0
    assert result["summary"]["n_files"] == 1
    assert result["summary"]["total_records_planned"] == 1


def test_rebuild_matched_index_dry_run_reports_changes(tmp_path, monkeypatch):
    """A perturbed builder output → plan reports 1 changed file."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out_dir = tmp_path / "datasets" / "_matched"
    base = _make_grouped()
    _write_existing_matched(out_dir, "scanqa", base["scanqa"])

    perturbed = _make_grouped(
        records=[{**base["scanqa"][0], "answer": "different"}]
    )

    result = pw._handle_rebuild_matched_index(
        {"dry_run": True},
        _collect=lambda: perturbed,
    )
    assert result["summary"]["n_changed"] == 1
    file_plan = result["plan"]["files_to_write"][0]
    assert file_plan["changed"] == 1
    assert file_plan["added"] == 0
    assert file_plan["new_sha"] != file_plan["old_sha"]


def test_rebuild_matched_index_dry_run_writes_zero_bytes(tmp_path, monkeypatch):
    """Snapshot fs mtimes before dry-run; assert nothing changed after."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out_dir = tmp_path / "datasets" / "_matched"
    grouped = _make_grouped()
    _write_existing_matched(out_dir, "scanqa", grouped["scanqa"])

    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}

    pw._handle_rebuild_matched_index(
        {},  # default dry_run
        _collect=lambda: grouped,
    )

    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_rebuild_matched_index_explicit_write(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out_dir = tmp_path / "fresh_matched"
    grouped = _make_grouped()

    result = pw._handle_rebuild_matched_index(
        {"dry_run": False, "output_dir": str(out_dir)},
        _collect=lambda: grouped,
    )
    assert "written" in result
    assert any("scanqa.json" in p for p in result["written"])
    assert (out_dir / "scanqa.json").exists()
    assert (out_dir / "index.json").exists()
    # Original canonical _matched untouched (it never existed in this tmp anyway)
    assert not (tmp_path / "datasets" / "_matched" / "scanqa.json").exists()


def test_rebuild_matched_index_filter_by_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    grouped = {
        "scanqa": _make_grouped()["scanqa"],
        "sqa3d": _make_grouped("sqa3d")["sqa3d"],
    }
    result = pw._handle_rebuild_matched_index(
        {"datasets": ["scanqa"]},
        _collect=lambda: grouped,
    )
    assert result["summary"]["n_files"] == 1
    assert "scanqa.json" in result["plan"]["files_to_write"][0]["path"]


def test_rebuild_matched_index_collect_raises():
    def boom():
        raise RuntimeError("synthetic")

    result = pw._handle_rebuild_matched_index({}, _collect=boom)
    assert "error" in result
    assert "match builder failed" in result["error"]


def test_rebuild_matched_index_collect_module_missing():
    def boom():
        raise ModuleNotFoundError("scripts.match.build_matched_3d_qa")

    result = pw._handle_rebuild_matched_index({}, _collect=boom)
    assert "error" in result
    assert "not importable" in result["error"]


def test_rebuild_matched_index_existing_file_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out_dir = tmp_path / "datasets" / "_matched"
    out_dir.mkdir(parents=True)
    (out_dir / "scanqa.json").write_text("{not-json")

    grouped = _make_grouped()
    result = pw._handle_rebuild_matched_index({}, _collect=lambda: grouped)
    assert result["summary"]["n_changed"] == 1


def test_rebuild_matched_index_write_with_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    out_dir = tmp_path / "fresh_matched"
    grouped = {
        "scanqa": _make_grouped()["scanqa"],
        "sqa3d": _make_grouped("sqa3d")["sqa3d"],
    }
    result = pw._handle_rebuild_matched_index(
        {"dry_run": False, "output_dir": str(out_dir), "datasets": ["scanqa"]},
        _collect=lambda: grouped,
    )
    assert (out_dir / "scanqa.json").exists()
    assert not (out_dir / "sqa3d.json").exists()
    assert result["summary"]["total_records"] == 1


def test_canonical_json_bytes_stable():
    a = pw._canonical_json_bytes({"b": 1, "a": 2})
    b = pw._canonical_json_bytes({"a": 2, "b": 1})
    assert a == b


def test_record_diff_added_removed_changed():
    new = [{"qid": "a", "x": 1}, {"qid": "b", "x": 2}]
    old = [{"qid": "a", "x": 1}, {"qid": "c", "x": 9}]
    added, removed, changed = pw._record_diff(new, old)
    assert added == 1  # b
    assert removed == 1  # c
    assert changed == 0


def test_record_diff_changed_count():
    new = [{"qid": "a", "x": 1}]
    old = [{"qid": "a", "x": 2}]
    _, _, changed = pw._record_diff(new, old)
    assert changed == 1


# ---------------------------------------------------------------------------
# sv_run_m3d_pipeline
# ---------------------------------------------------------------------------


def test_run_m3d_pipeline_dry_run_returns_command(tmp_path, monkeypatch):
    """Dry-run returns commands list; no subprocess.run called."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))

    sentinel = {"called": False}

    def boom(*_a, **_kw):
        sentinel["called"] = True
        raise AssertionError("subprocess.run must not be called in dry-run")

    monkeypatch.setattr(subprocess, "run", boom)

    result = pw._handle_run_m3d_pipeline(
        {"input": "datasets/foo/in.nii.gz"},  # no dry_run key → defaults to True
    )
    assert sentinel["called"] is False
    assert "commands" in result
    assert len(result["commands"]) == 1
    assert result["commands"][0][1].endswith("m3d_pipeline_cli.py")
    assert "outputs_planned" in result
    assert any("in.nii.npy" in p or "in.npy" in p for p in result["outputs_planned"])
    assert "working_dir" in result


def test_run_m3d_pipeline_missing_input():
    result = pw._handle_run_m3d_pipeline({})
    assert "error" in result


def test_run_m3d_pipeline_write_path_timestamps(tmp_path, monkeypatch):
    """With dry_run=false and fake runner → artifacts list, elapsed_s >= 0."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    output_dir = tmp_path / "outs"

    def _fake_runner(cmd: list[str], cwd: Path):
        # Drop a fake artifact so the rglob picks it up.
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.npy").write_bytes(b"\x00" * 8)

        class _Proc:
            stdout = "all good\n"
            returncode = 0

        return _Proc()

    result = pw._handle_run_m3d_pipeline(
        {
            "input": str(tmp_path / "in.nii.gz"),
            "output_dir": str(output_dir),
            "dry_run": False,
        },
        _runner=_fake_runner,
        _now=lambda: 0.0,
    )

    assert result["exit_code"] == 0
    assert result["elapsed_s"] >= 0.0
    assert any("result.npy" in a for a in result["artifacts"])
    assert "all good" in result["stdout_tail"]


def test_run_m3d_pipeline_subprocess_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))

    def _boom(cmd, cwd):
        raise OSError("permission denied")

    result = pw._handle_run_m3d_pipeline(
        {
            "input": "in.nii.gz",
            "output_dir": str(tmp_path / "x"),
            "dry_run": False,
        },
        _runner=_boom,
    )
    assert "error" in result
    assert "subprocess" in result["error"]


def test_run_m3d_pipeline_nonzero_exit_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    output_dir = tmp_path / "outs"

    class _Proc:
        stdout = "error: file missing\n"
        returncode = 7

    result = pw._handle_run_m3d_pipeline(
        {
            "input": "in.nii.gz",
            "output_dir": str(output_dir),
            "dry_run": False,
        },
        _runner=lambda cmd, cwd: _Proc(),
    )
    assert result["exit_code"] == 7
    assert "file missing" in result["stdout_tail"]


def test_run_m3d_pipeline_derive_subcommand(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    result = pw._handle_run_m3d_pipeline(
        {"input": "v.npy", "config": "derive"},
    )
    assert any(".nii.gz" in p for p in result["outputs_planned"])


def test_run_m3d_pipeline_unknown_subcommand_passes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    result = pw._handle_run_m3d_pipeline(
        {"input": "v.npy", "config": "weird"},
    )
    # Unknown sub → command list still built; outputs_planned defaults to dir
    assert any("weird" in part for part in result["commands"][0])


def test_run_m3d_pipeline_stdout_bytes_decoded(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))

    class _Proc:
        stdout = b"binary-ish output\n"
        returncode = 0

    result = pw._handle_run_m3d_pipeline(
        {"input": "in.npy", "output_dir": str(tmp_path / "out"), "dry_run": False},
        _runner=lambda c, w: _Proc(),
    )
    assert "binary-ish" in result["stdout_tail"]


def test_run_m3d_pipeline_default_output_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(pw, "_utc_stamp", lambda: "FIXED")
    result = pw._handle_run_m3d_pipeline({"input": "x.nii"})
    assert any("out_samples/m3d/FIXED" in p for p in result["outputs_planned"])


def test_build_m3d_command_derive(tmp_path, monkeypatch):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    cmd = pw._build_m3d_command(Path("a.npy"), tmp_path / "o", "derive")
    assert "derive" in cmd
    assert any(".nii.gz" in part for part in cmd)


def test_run_subprocess_default_executes_real(tmp_path, monkeypatch):
    """Sanity-check the real runner against a trivial subprocess (`echo`).

    This does spawn a subprocess (one). It is NOT routed through our handler.
    """
    proc = pw._run_subprocess_default([sys.executable, "-c", "print('hello')"], tmp_path)
    assert proc.returncode == 0
    assert "hello" in proc.stdout


# ---------------------------------------------------------------------------
# Aggregate safety: dry_run defaults
# ---------------------------------------------------------------------------


def test_dry_run_is_default_for_all_write_tools(tmp_path, monkeypatch):
    """Omit dry_run from args → behavior matches dry_run=true (no writes, no exec)."""
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))

    # rebuild_matched_index
    pre_files = set(p for p in tmp_path.rglob("*") if p.is_file())
    grouped = _make_grouped()
    r1 = pw._handle_rebuild_matched_index({}, _collect=lambda: grouped)
    assert "plan" in r1  # dry-run shape

    # run_m3d_pipeline
    sentinel = {"called": False}

    def boom(*a, **k):
        sentinel["called"] = True
        raise AssertionError("subprocess.run must not run on default dry_run")

    monkeypatch.setattr(subprocess, "run", boom)
    r2 = pw._handle_run_m3d_pipeline({"input": "x.nii"})
    assert "commands" in r2
    assert sentinel["called"] is False

    # classify_v_batch doesn't have a dry_run; it never writes if a refusal
    # condition is hit and never auto-runs the LLM without explicit dataset.
    # (It WILL write to a fresh path if you pass a dataset + working impls;
    # that's by design, not a dry-run violation.)

    post_files = set(p for p in tmp_path.rglob("*") if p.is_file())
    assert post_files == pre_files


# ---------------------------------------------------------------------------
# Builder integration smoke (no real LLM, no real subprocess).
# ---------------------------------------------------------------------------


def test_default_classify_one_routes_to_real_module(monkeypatch):
    """The default _classify_one_default_impl uses scripts.v_classify.classify_qa.

    We patch that module's classify_one to a sentinel so the default delegate
    code path is exercised without touching the network.
    """
    import scripts.v_classify.classify_qa as cq

    seen = {}

    def fake(client, model, rec, max_attempts):
        seen["model"] = model
        return {"qid": rec["qid"], "status": "ok", "dominant_v": "none"}

    monkeypatch.setattr(cq, "classify_one", fake)
    out = pw._classify_one_default_impl(_client_sentinel(), "m", {"qid": "x"}, 2)
    assert out["status"] == "ok"
    assert seen["model"] == "m"


def test_default_load_records_routes_to_real_module(monkeypatch):
    import scripts.v_classify.classify_qa as cq

    monkeypatch.setattr(cq, "load_records",
                        lambda dataset, *, skip_no_answer: [{"qid": "a"}])
    rows = pw._load_records_default("scanqa", skip_no_answer=True)
    assert rows == [{"qid": "a"}]


def test_default_sample_records_routes_to_real_module(monkeypatch):
    import scripts.v_classify.classify_qa as cq

    monkeypatch.setattr(cq, "sample_records",
                        lambda records, n, seed: records[:n])
    rows = pw._sample_records_default([{"qid": "a"}, {"qid": "b"}], 1, 0)
    assert rows == [{"qid": "a"}]


def test_default_already_done_routes_to_real_module(monkeypatch, tmp_path):
    import scripts.v_classify.classify_qa as cq

    monkeypatch.setattr(cq, "already_done", lambda p: {"x"})
    assert pw._already_done_default(tmp_path / "x") == {"x"}


def test_build_chat_client_missing_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MINDRA_BASE_URL", raising=False)
    monkeypatch.delenv("MINDRA_API", raising=False)
    with pytest.raises(RuntimeError):
        pw._build_chat_client_default()


def test_build_chat_client_with_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    import scripts.v_classify.client as cm

    class FakeC:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(cm, "ChatClient", FakeC)
    c = pw._build_chat_client_default()
    assert isinstance(c, FakeC)


def test_utc_stamp_format():
    s = pw._utc_stamp()
    assert s.endswith("Z")
    assert len(s) == 16


def test_resolve_path_absolute_passthrough(tmp_path):
    p = tmp_path / "abs.txt"
    assert pw._resolve_path(str(p)) == p


def test_resolve_path_relative_to_repo_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SV_REPO_ROOT", str(tmp_path))
    p = pw._resolve_path("rel/x.txt")
    assert p == tmp_path / "rel" / "x.txt"
