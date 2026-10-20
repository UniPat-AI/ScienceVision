from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest

from qagen.contracts import digest, make_envelope
from qagen.errors import QuarantinedError
from qagen.paths import WorkspacePaths
from qagen.selection.private_source import (
    PrivateSourceAdapterError,
    import_v32_private_sources,
    load_v32_sanitized_author_brief,
    materialize_v32_private_truth,
)
from qagen.storage.atomic import read_json


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _id_digest(values: list[str]) -> str:
    return sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def _asset(root: Path, relative: str, content: bytes) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": relative,
        "bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "format": "nii.gz" if relative.endswith(".nii.gz") else "cif",
    }


def _selection_row(
    *,
    question_id: str,
    task_id: str,
    sub_domain: str,
    access_track: str,
    asset: dict[str, Any],
    answer_field: str,
) -> dict[str, Any]:
    return {
        "access_track": access_track,
        "answer_fields": [answer_field],
        "compute_packages": ["gemmi==0.7.5"] if access_track == "raw_compute" else [],
        "difficulty": "medium",
        "domain": "biology",
        "forbidden_strategies": [],
        "has_compute_contract": access_track == "raw_compute",
        "mask_asset": None,
        "mcp_allow_list": ["viewer_rotate", "viewer_measure_distance"],
        "primary_asset": asset,
        "question_id": question_id,
        "sub_domain": sub_domain,
        "task_id": task_id,
        "viewer": "molstar_deployed",
        "vision_class_code": "V2",
        "vision_class_group": "hard_moat",
    }


def _question_row(selection_row: dict[str, Any], *, historic_question: str) -> dict[str, Any]:
    asset = selection_row["primary_asset"]
    result: dict[str, Any] = {
        "schema_version": "sciencevision.question_assets.v3",
        "question_id": selection_row["question_id"],
        "task_id": selection_row["task_id"],
        "domain": selection_row["domain"],
        "sub_domain": selection_row["sub_domain"],
        "viewer": selection_row["viewer"],
        "question": historic_question,
        "answer_shape": {"kind": "structured", "fields": list(selection_row["answer_fields"])},
        "assets": [{"role": "primary", **asset}],
        "mcp_allow_list": list(selection_row["mcp_allow_list"]),
        "provenance": {"access_track": selection_row["access_track"]},
        "compute_contract": None,
    }
    if selection_row["access_track"] == "raw_compute":
        result["compute_contract"] = {
            "schema_version": "sciencevision.qa_gen.compute_contract.v1",
            "deterministic": True,
            "network_access": False,
            "allowed_capabilities": ["asset_file_read", "python_compute"],
            "packages": ["gemmi==0.7.5"],
            "runtime": "python3.12",
            "program": "reviewed_symbolic_program",
            "selection_hints": {"private": "do-not-forward"},
            "input_asset_hashes": [
                {"repo_path": asset["path"], "sha256": asset["sha256"], "byte_size": asset["bytes"]}
            ],
        }
    return result


def _answer_row(question_row: dict[str, Any], *, old_answer: str) -> dict[str, Any]:
    asset = question_row["assets"][0]
    return {
        "schema_version": "sciencevision.answer_keys.v1",
        "question_id": question_row["question_id"],
        "reference_answer": {question_row["answer_shape"]["fields"][0]: old_answer},
        "solving_approach": "HISTORICAL SOLVING APPROACH MUST NOT REACH AN AUTHOR",
        "evidence": {"path": asset["path"], "sha256": asset["sha256"]},
        "truth_mode": "author_compute_exception",
        "truth_status": "auto_verified",
    }


def _write_bundle(root: Path, question_rows: list[dict[str, Any]], answer_rows: list[dict[str, Any]]) -> None:
    _write_json(
        root / "MANIFEST.json",
        {
            "schema_version": "sciencevision.question_assets_bundle_manifest.v1",
            "package_id": "v32-test-bundle",
            "question_schema": "sciencevision.question_assets.v3",
            "answer_schema": "sciencevision.answer_keys.v1",
            "counts": {"questions": len(question_rows)},
        },
    )
    _write_jsonl(root / "question_assets.jsonl", question_rows)
    _write_jsonl(root / "answer_keys.jsonl", answer_rows)
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            rows.append((sha256(path.read_bytes()).hexdigest(), path.relative_to(root).as_posix()))
    (root / "SHA256SUMS").write_text("".join(f"{value}  {relative}\n" for value, relative in rows), encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Any]:
    bundle = tmp_path / "bundle"
    raw_asset = _asset(bundle, "assets/raw.cif", b"raw-scene")
    viewer_asset = _asset(bundle, "assets/viewer.cif", b"viewer-scene")
    raw = _selection_row(
        question_id="official-raw-1",
        task_id="historical-task-raw-1",
        sub_domain="bio_docking_component_clash",
        access_track="raw_compute",
        asset=raw_asset,
        answer_field="observable",
    )
    viewer = _selection_row(
        question_id="official-viewer-1",
        task_id="historical-task-viewer-1",
        sub_domain="bio_docking_component_contacts",
        access_track="viewer_measurement",
        asset=viewer_asset,
        answer_field="visible_metric",
    )
    ids = [raw["question_id"], viewer["question_id"]]
    by_subdomain = {
        raw["sub_domain"]: [raw["question_id"]],
        viewer["sub_domain"]: [viewer["question_id"]],
    }
    selection = {
        "schema_version": "sciencevision.eval_v32_selection.v1",
        "generated_by": "test",
        "seed": "test",
        "per_domain": 2,
        "overrides": {},
        "total": 2,
        "id_sha256": _id_digest(ids),
        "warnings": [],
        "bundle": {"package_id": "test", "path": "test"},
        "domains": {
            "biology": {
                "by_sub_domain": by_subdomain,
                "count": 2,
                "exposure": {},
                "id_sha256": _id_digest(ids),
                "question_ids": sorted(ids),
                "quota": {raw["sub_domain"]: 1, viewer["sub_domain"]: 1},
                "rows": [raw, viewer],
                "sub_domain_population": {raw["sub_domain"]: 1, viewer["sub_domain"]: 1},
            }
        },
    }
    selection_path = tmp_path / "selection.json"
    _write_json(selection_path, selection)
    questions = [
        _question_row(raw, historic_question="HISTORIC RAW QUESTION /private/raw.cif"),
        _question_row(viewer, historic_question="HISTORIC VIEWER QUESTION /private/viewer.cif"),
        {
            **_question_row(raw, historic_question="UNSELECTED SHOULD NEVER IMPORT"),
            "question_id": "unselected-question",
            "task_id": "unselected-task",
        },
    ]
    answers = [
        _answer_row(questions[0], old_answer="OLD-RAW-ANSWER"),
        _answer_row(questions[1], old_answer="OLD-VIEWER-ANSWER"),
        _answer_row(questions[2], old_answer="UNSELECTED-ANSWER"),
    ]
    _write_bundle(bundle, questions, answers)
    source_spec = {
        "schema_version": 1,
        "kind": "qagen_v32_private_source_spec",
        "id": "v32-test-source",
        "selection": {
            "schema_version": selection["schema_version"],
            "file_sha256": _sha(selection_path),
            "id_sha256": selection["id_sha256"],
            "total": 2,
            "domain_counts": {"biology": 2},
        },
        "bundle": {
            "manifest_filename": "MANIFEST.json",
            "manifest_sha256": _sha(bundle / "MANIFEST.json"),
            "manifest_schema_version": "sciencevision.question_assets_bundle_manifest.v1",
            "package_id": "v32-test-bundle",
            "checksums_filename": "SHA256SUMS",
            "question_assets": {
                "filename": "question_assets.jsonl",
                "sha256": _sha(bundle / "question_assets.jsonl"),
                "schema_version": "sciencevision.question_assets.v3",
            },
            "answer_keys": {
                "filename": "answer_keys.jsonl",
                "sha256": _sha(bundle / "answer_keys.jsonl"),
                "schema_version": "sciencevision.answer_keys.v1",
            },
            "max_asset_bytes": 1024,
        },
        "compute_policy": {
            "allowed_capabilities": ["asset_file_read", "python_compute"],
            "allowed_packages": ["gemmi==0.7.5"],
            "allowed_runtimes": ["python3.12"],
        },
    }
    source_spec_path = tmp_path / "source-spec.json"
    _write_json(source_spec_path, source_spec)
    mapping = {
        "schema_version": 2,
        "kind": "qagen_selection_taxonomy_mapping",
        "id": "v32-test-mapping",
        "selection_id_sha256": selection["id_sha256"],
        "selection_file_sha256": _sha(selection_path),
        "generation_strategy_by_source_access_track": {
            "raw_compute": "reauthor_on_asset",
            "viewer_measurement": "preserve_intent",
        },
        "review": {
            "decision": "approved",
            "reviewer": "test-reviewer",
            "reviewed_at": "2026-08-27T00:00:00Z",
            "rationale": "test mapping",
        },
        "mappings": [
            {
                "source_leaf": "biology.bio_docking_component_clash",
                "target_leaf": "biology.docking_pose_plausibility",
                "rationale": "test mapping",
            },
            {
                "source_leaf": "biology.bio_docking_component_contacts",
                "target_leaf": "biology.molecular_collision_check",
                "rationale": "test mapping",
            },
        ],
    }
    mapping_path = tmp_path / "mapping.json"
    _write_json(mapping_path, mapping)
    return {
        "bundle": bundle,
        "selection_path": selection_path,
        "source_spec_path": source_spec_path,
        "mapping_path": mapping_path,
        "workspace": WorkspacePaths.from_value(tmp_path / "workspace"),
    }


def _import(fixture: dict[str, Any]) -> dict[str, Any]:
    return import_v32_private_sources(
        fixture["workspace"],
        selection_path=fixture["selection_path"],
        bundle_root=fixture["bundle"],
        source_spec_path=fixture["source_spec_path"],
        taxonomy_mapping_path=fixture["mapping_path"],
    )


def test_imports_only_official_ids_and_emits_sanitized_author_briefs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _import(fixture)

    assert result["total"] == 2
    assert result["raw_compute_count"] == 1
    assert result["viewer_measurement_count"] == 1
    imported = read_json(Path(result["source_import_path"]))
    assert imported["body"]["truth_gate_counts"] == {
        "attested_compute_recompute": 1,
        "viewer_evidence_required": 1,
    }
    assert len(imported["body"]["records"]) == 2
    all_private_text = Path(result["agent_handoff_path"]).read_text(encoding="utf-8")
    assert "official-raw-1" not in all_private_text
    assert "historical-task-raw-1" not in all_private_text
    assert "OLD-RAW-ANSWER" not in all_private_text

    for item in imported["body"]["records"]:
        record = read_json(fixture["workspace"].root / item["record"]["path"])
        brief = load_v32_sanitized_author_brief(fixture["workspace"], fixture["workspace"].root / item["author_brief"]["path"])
        text = json.dumps(brief, sort_keys=True)
        assert "HISTORIC" not in text
        assert "OLD-" not in text
        assert "SOLVING" not in text
        assert "/private/" not in text
        assert "question_id" not in text
        assert record["body"]["legacy_regression"]["comparison_policy"] == "private_regression_only_never_truth_reuse"
        assert "reference_answer" in record["body"]["legacy_regression"]


def test_manifest_or_checksum_tamper_fails_closed_and_events_do_not_leak_payloads(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    sums = fixture["bundle"] / "SHA256SUMS"
    sums.write_text(sums.read_text(encoding="utf-8").replace("question_assets.jsonl", "question_assets_tampered.jsonl", 1), encoding="utf-8")

    with pytest.raises(QuarantinedError):
        _import(fixture)

    events = fixture["workspace"].events.read_text(encoding="utf-8")
    assert "HISTORIC" not in events
    assert "OLD-" not in events
    assert str(fixture["bundle"]) not in events


def test_raw_compute_never_executes_symbolic_source_program_and_viewer_truth_needs_new_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _import(fixture)
    truth = materialize_v32_private_truth(fixture["workspace"], source_import_path=result["source_import_path"])

    assert truth["status_counts"] == {
        "blocked_new_viewer_receipt_and_evidence_required": 1,
        "blocked_reviewed_local_compute_runner_unavailable": 1,
    }
    rendered = json.dumps(truth, sort_keys=True)
    assert "OLD-RAW-ANSWER" not in rendered
    assert "reviewed_symbolic_program" not in rendered


def test_viewer_truth_uses_fresh_evidence_and_only_compares_legacy_reference_privately(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    imported_result = _import(fixture)
    imported = read_json(Path(imported_result["source_import_path"]))
    viewer_entry = next(item for item in imported["body"]["records"] if item["truth_mode"] == "viewer_evidence_required")
    record_digest = viewer_entry["record"]["content_digest"]
    evidence = make_envelope(
        kind="v32_viewer_truth_evidence",
        identifier="new-controlled-viewer-evidence",
        status="observed",
        body={
            "source_record_digest": record_digest,
            "viewer_receipt_digest": digest({"receipt": "new-controlled-viewer"}),
            "evidence_digest": digest({"evidence": "new-screenshot-and-trace"}),
            "answer": {"visible_metric": "FRESH-VIEWER-OBSERVATION"},
        },
    )
    evidence_path = fixture["workspace"].private / "new-viewer-evidence.json"
    _write_json(evidence_path, evidence)

    truth = materialize_v32_private_truth(
        fixture["workspace"],
        source_import_path=imported_result["source_import_path"],
        viewer_evidence_paths={record_digest: evidence_path},
    )
    result_ref = next(item for item in truth["results"] if item["source_record_digest"] == record_digest)
    truth_record = read_json(Path(result_ref["truth_result_path"]))
    assert truth_record["status"] == "verified_from_new_viewer_evidence"
    assert truth_record["body"]["truth"] == {"visible_metric": "FRESH-VIEWER-OBSERVATION"}
    assert truth_record["body"]["legacy_regression"] == {
        "status": "mismatched",
        "reference_answer_digest": digest({"visible_metric": "OLD-VIEWER-ANSWER"}),
    }
    assert "OLD-VIEWER-ANSWER" not in json.dumps(truth_record, sort_keys=True)


def test_compute_contract_program_must_be_a_symbolic_identifier_not_source_code(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    questions_path = fixture["bundle"] / "question_assets.jsonl"
    rows = [json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["compute_contract"]["program"] = "__import__('os').system('touch pwned')"
    _write_jsonl(questions_path, rows)
    _write_bundle(fixture["bundle"], rows, [json.loads(line) for line in (fixture["bundle"] / "answer_keys.jsonl").read_text(encoding="utf-8").splitlines()])
    # Re-pin the test-only source spec so rejection reaches the symbolic-program
    # validator rather than stopping at the earlier JSONL checksum gate.
    source_spec = read_json(fixture["source_spec_path"])
    source_spec["bundle"]["question_assets"]["sha256"] = _sha(questions_path)
    _write_json(fixture["source_spec_path"], source_spec)
    with pytest.raises(QuarantinedError):
        _import(fixture)
    assert not (fixture["bundle"] / "pwned").exists()


def test_tampered_private_record_digest_is_rejected_before_truth_materialization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _import(fixture)
    imported = read_json(Path(result["source_import_path"]))
    record_ref = imported["body"]["records"][0]["record"]
    record_path = fixture["workspace"].root / record_ref["path"]
    record = read_json(record_path)
    record["body"]["target_leaf"] = "biology.molecular_collision_check"
    _write_json(record_path, record)

    with pytest.raises(PrivateSourceAdapterError, match="content digest"):
        materialize_v32_private_truth(fixture["workspace"], source_import_path=result["source_import_path"])
