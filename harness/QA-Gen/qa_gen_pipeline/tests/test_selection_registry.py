from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest

from qagen.cli import main
from qagen.contracts import digest, digest_bytes
from qagen.errors import InputError, QuarantinedError
from qagen.paths import WorkspacePaths
from qagen.selection import compile_selection, import_selection
from qagen.selection import registry as selection_registry
from qagen.storage.atomic import read_json

from conftest import write_json


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _id_digest(question_ids: list[str]) -> str:
    return sha256("\n".join(sorted(question_ids)).encode("utf-8")).hexdigest()


def _asset(path: str, content: bytes, *, format_name: str = "cif") -> dict[str, Any]:
    return {
        "bytes": len(content),
        "format": format_name,
        "path": path,
        "sha256": sha256(content).hexdigest(),
    }


def _row(
    *,
    question_id: str,
    domain: str,
    subdomain: str,
    access_track: str,
    primary: dict[str, Any],
    mask: dict[str, Any] | None = None,
    viewer: str = "fixture_viewer",
) -> dict[str, Any]:
    raw_compute = access_track == "raw_compute"
    return {
        "access_track": access_track,
        "answer_fields": ["answer"],
        "compute_packages": ["fixture==1"] if raw_compute else [],
        "difficulty": "medium",
        "domain": domain,
        "forbidden_strategies": ["fixture_shortcut"],
        "has_compute_contract": raw_compute,
        "mask_asset": mask,
        "mcp_allow_list": ["viewer_rotate", "viewer_render"],
        "primary_asset": primary,
        "question_id": question_id,
        "sub_domain": subdomain,
        "task_id": f"task-{question_id}",
        "viewer": viewer,
        "vision_class_code": "V2",
        "vision_class_group": "fixture",
    }


def _domain_block(row: dict[str, Any]) -> dict[str, Any]:
    question_id = row["question_id"]
    subdomain = row["sub_domain"]
    return {
        "by_sub_domain": {subdomain: [question_id]},
        "count": 1,
        "exposure": {},
        "id_sha256": _id_digest([question_id]),
        "question_ids": [question_id],
        "quota": {subdomain: 1},
        "rows": [row],
        "sub_domain_population": {subdomain: 2},
    }


def _make_selection_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    package_root = tmp_path / "fixture-v32-package"
    raw_bytes = b"raw compute asset\n"
    viewer_bytes = b"viewer measurement asset\n"
    mask_bytes = b"viewer measurement mask\n"
    primary_raw = _asset("assets/biology/raw.cif", raw_bytes)
    primary_viewer = _asset("assets/chemistry/viewer.cif", viewer_bytes)
    mask = {
        **_asset("assets/chemistry/viewer.mask", mask_bytes, format_name="mask"),
        "mask_id": 7,
        "mask_selection": "label_index",
    }
    for asset, content in ((primary_raw, raw_bytes), (primary_viewer, viewer_bytes), (mask, mask_bytes)):
        path = package_root / asset["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    raw_row = _row(
        question_id="biology-raw-001",
        domain="biology",
        subdomain="bio_source_collision",
        access_track="raw_compute",
        primary=primary_raw,
    )
    viewer_row = _row(
        question_id="chemistry-viewer-001",
        domain="chemistry",
        subdomain="chem_source_conformation",
        access_track="viewer_measurement",
        primary=primary_viewer,
        mask=mask,
        viewer="niivue_deployed",
    )
    ids = [raw_row["question_id"], viewer_row["question_id"]]
    selection = {
        "bundle": {"package_id": "fixture-v32-package", "path": str(package_root)},
        "domains": {
            "biology": _domain_block(raw_row),
            "chemistry": _domain_block(viewer_row),
        },
        "generated_by": "test_selection_registry.py",
        "id_sha256": _id_digest(ids),
        "overrides": {},
        "per_domain": 1,
        "schema_version": "sciencevision.eval_v32_selection.v1",
        "seed": "fixture-selection",
        "total": 2,
        "warnings": [],
    }
    selection_path = tmp_path / "selection.json"
    write_json(selection_path, selection)
    source_spec = {
        "schema_version": 1,
        "kind": "qagen_selection_source_spec",
        "id": "fixture_v32_selection",
        "source_mode": "committed_local_selection",
        "selection": {
            "schema_version": "sciencevision.eval_v32_selection.v1",
            "input_filename": selection_path.name,
            "file_sha256": digest_bytes(selection_path.read_bytes()),
            "id_sha256": selection["id_sha256"],
            "total": 2,
            "domain_counts": {"biology": 1, "chemistry": 1},
        },
        "historical_origin": {
            "repository": "fixture/Science-Vision",
            "commit": "a" * 40,
            "path": "docs/harbor/fixture-selection.json",
        },
        "asset_package": {"expected_basename": package_root.name, "package_id": "fixture-v32-package"},
        "local_only": True,
        "production_eligible": False,
        "reference_owners": [
            {"repo_path": "QA-Gen/qa_gen_pipeline/tests/test_selection_registry.py", "purpose": "fixture"}
        ],
    }
    source_spec_path = tmp_path / "selection-source-spec.json"
    write_json(source_spec_path, source_spec)
    return selection_path, package_root, source_spec_path, selection


def _mapping(selection: dict[str, Any], selection_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "kind": "qagen_selection_taxonomy_mapping",
        "id": "fixture_v32_map",
        "selection_id_sha256": selection["id_sha256"],
        "selection_file_sha256": digest_bytes(selection_path.read_bytes()),
        "generation_strategy_by_source_access_track": {
            "raw_compute": "reauthor_on_asset",
            "viewer_measurement": "preserve_intent",
        },
        "review": {
            "decision": "approved",
            "reviewer": "taxonomy-reviewer",
            "reviewed_at": "2026-08-27T00:00:00Z",
            "rationale": "The fixture biology source leaf is intentionally mapped to the existing fixture leaf.",
        },
        "mappings": [
            {
                "source_leaf": "biology.bio_source_collision",
                "target_leaf": "biology.docking_pose_plausibility",
                "rationale": "Only a fixture mapping used to verify the reviewed mapping contract.",
            }
        ],
    }


def _complete_mapping(selection: dict[str, Any], selection_path: Path) -> dict[str, Any]:
    mapping = _mapping(selection, selection_path)
    mapping["mappings"].append(
        {
            "source_leaf": "chemistry.chem_source_conformation",
            "target_leaf": "chemistry.conformation_analysis",
            "rationale": "Only a fixture mapping used to verify a fully compiled mapping contract.",
        }
    )
    return mapping


def _events(paths: WorkspacePaths) -> list[dict[str, Any]]:
    return [json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()]


def _event_body(event: dict[str, Any]) -> dict[str, Any]:
    assert event["kind"] == "event"
    return event["body"]


def _request_digest(operation: str, **input_digests: str | None) -> str:
    return digest(
        {
            "audit_request_version": 1,
            "operation": operation,
            **input_digests,
        }
    )


def _assert_audit_events_are_sanitized(events: list[dict[str, Any]], *sensitive_values: str) -> None:
    serialized = json.dumps(events, sort_keys=True)
    for value in sensitive_values:
        assert value not in serialized
    for forbidden in ("asset_root", "reference_answer", "question", "answer"):
        assert forbidden not in serialized


def test_committed_v32_selection_preserves_its_frozen_identity_but_fails_closed_pending_mask_labels() -> None:
    source_path = PACKAGE_ROOT / "inputs" / "selections" / "V32_BCM522_SIXROUTE_SELECTION.json"
    selection = json.loads(source_path.read_text(encoding="utf-8"))

    # This is a frozen historical input, not a silently repaired manifest.
    # Several Medical rows use a merged non-zero mask and need an explicit
    # human label_index before a successor selection can be re-attested.
    assert any(
        row.get("mask_asset", {}).get("mask_selection") == "complete_nonzero_overlay"
        for row in selection["domains"]["medical"]["rows"]
    )
    with pytest.raises(InputError):
        selection_registry._schema_validate(selection, "selection.schema.json", "committed v32 selection")

    assert sum(len(domain["rows"]) for domain in selection["domains"].values()) == 60
    assert selection["id_sha256"] == "149b29605da6cd2862e427dacb4d1098b106d23eb95b248ffdf6dc0bdf1846d4"
    assert digest_bytes(source_path.read_bytes()) == "sha256:75ae4cbb771d42ff68811b2e06a82ca34cb00749f888673518dac937336463d6"


def test_import_attests_every_asset_and_compile_binds_generation_strategy(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")

    imported = import_selection(
        workspace,
        selection_path=selection_path,
        asset_root=package_root,
        source_spec_path=source_spec,
    )

    assert imported["status"] == "asset_attested_pending_taxonomy_review"
    assert imported["total"] == 2
    assert imported["asset_reference_count"] == 3
    assert imported["unique_asset_count"] == 3
    assert Path(imported["selection_import_path"]).is_file()

    mapping_path = tmp_path / "taxonomy-map.json"
    write_json(mapping_path, _mapping(selection, selection_path))
    compiled = compile_selection(
        workspace,
        selection_import_path=Path(imported["selection_import_path"]),
        taxonomy_mapping_path=mapping_path,
    )

    assert compiled["status"] == "blocked_taxonomy_mapping"
    assert compiled["generation_eligible"] is False
    assert compiled["unmapped_source_leaves"] == ["chemistry.chem_source_conformation"]
    batch = read_json(Path(compiled["vnext_batch_manifest_path"]))
    raw_row = next(row for row in batch["body"]["rows"] if row["question_id"] == "biology-raw-001")
    assert raw_row["access_track"] == {
        "source": "raw_compute",
        "effective": "raw_compute",
        "conversion": "preserved",
    }
    assert raw_row["generation_strategy"] == "reauthor_on_asset"
    assert raw_row["mapping_status"] == "mapped"
    assert raw_row["target_leaf"] == "biology.docking_pose_plausibility"
    niivue_row = next(row for row in batch["body"]["rows"] if row["question_id"] == "chemistry-viewer-001")
    assert niivue_row["generation_strategy"] == "preserve_intent"
    assert next(asset for asset in niivue_row["assets"] if asset["role"] == "mask") == {
        "role": "mask",
        "path": "assets/chemistry/viewer.mask",
        "bytes": len(b"viewer measurement mask\n"),
        "sha256": sha256(b"viewer measurement mask\n").hexdigest(),
        "mask_selection": "label_index",
        "mask_id": 7,
    }
    events = [_event_body(event) for event in _events(workspace)]
    assert [event["event_type"] for event in events] == [
        "selection_import_started",
        "selection_import_completed",
        "selection_compile_started",
        "selection_compile_blocked",
    ]
    import_started = events[0]["payload"]
    expected_import_request_digest = _request_digest(
        "selection_import",
        selection_file_sha256=digest_bytes(selection_path.read_bytes()),
        source_spec_file_sha256=digest_bytes(source_spec.read_bytes()),
        source_spec_digest=imported["source_spec_digest"],
    )
    assert import_started["request_digest"] == expected_import_request_digest
    assert import_started["selection_id_sha256"] == selection["id_sha256"]
    assert import_started["selection_file_sha256"] == digest_bytes(selection_path.read_bytes())
    assert import_started["source_spec_file_sha256"] == digest_bytes(source_spec.read_bytes())
    assert import_started["source_spec_digest"] == imported["source_spec_digest"]
    completed = events[1]["payload"]
    assert completed["request_digest"] == expected_import_request_digest
    assert completed["selection_id_sha256"] == selection["id_sha256"]
    assert completed["selection_file_sha256"] == digest_bytes(selection_path.read_bytes())
    assert completed["source_spec_digest"] == imported["source_spec_digest"]
    assert completed["asset_attestation_digest"] == imported["asset_attestation_digest"]
    assert completed["row_count"] == 2
    assert completed["asset_reference_count"] == 3
    compile_started = events[2]["payload"]
    expected_compile_request_digest = _request_digest(
        "selection_compile",
        selection_import_file_sha256=digest_bytes(Path(imported["selection_import_path"]).read_bytes()),
        selection_import_digest=imported["selection_import_digest"],
        selection_file_sha256=digest_bytes(selection_path.read_bytes()),
        source_spec_digest=imported["source_spec_digest"],
        taxonomy_mapping_file_sha256=digest_bytes(mapping_path.read_bytes()),
    )
    assert compile_started["request_digest"] == expected_compile_request_digest
    assert compile_started["selection_import_digest"] == imported["selection_import_digest"]
    assert compile_started["selection_file_sha256"] == digest_bytes(selection_path.read_bytes())
    assert compile_started["source_spec_digest"] == imported["source_spec_digest"]
    assert compile_started["taxonomy_mapping_file_sha256"] == digest_bytes(mapping_path.read_bytes())
    blocked = events[3]["payload"]
    assert blocked["request_digest"] == expected_compile_request_digest
    assert blocked["selection_import_digest"] == imported["selection_import_digest"]
    assert blocked["taxonomy_mapping_file_sha256"] == digest_bytes(mapping_path.read_bytes())
    assert blocked["vnext_batch_manifest_digest"] == compiled["vnext_batch_manifest_digest"]
    _assert_audit_events_are_sanitized(
        events,
        str(package_root),
        str(selection_path),
        str(source_spec),
        "/private/",
        "raw.cif",
    )


def test_import_fails_before_writing_slots_when_asset_integrity_does_not_match(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, _ = _make_selection_fixture(tmp_path)
    (package_root / "assets" / "biology" / "raw.cif").write_bytes(b"tampered asset\n")
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")

    with pytest.raises(QuarantinedError, match="asset (byte size|sha256) mismatch"):
        import_selection(
            workspace,
            selection_path=selection_path,
            asset_root=package_root,
            source_spec_path=source_spec,
        )

    assert workspace.qagen.is_dir()
    assert not any(workspace.selection_slots.rglob("*.json"))
    assert not any(workspace.selection_imports.rglob("*.json"))
    events = [_event_body(event) for event in _events(workspace)]
    assert [event["event_type"] for event in events] == [
        "selection_import_started",
        "selection_import_failed",
    ]
    started = events[0]["payload"]
    failed = events[-1]["payload"]
    assert started["request_digest"] == failed["request_digest"]
    assert started["selection_file_sha256"] == digest_bytes(selection_path.read_bytes())
    assert started["source_spec_file_sha256"] == digest_bytes(source_spec.read_bytes())
    assert started["source_spec_digest"] == digest(json.loads(source_spec.read_text(encoding="utf-8")))
    assert failed["phase"] == "attest_assets"
    assert failed["error_class"] == "QuarantinedError"
    assert failed["row_count"] == 2
    assert failed["asset_reference_count"] == 3
    assert "asset_attestation_digest" not in failed
    _assert_audit_events_are_sanitized(events, str(package_root), str(selection_path), str(source_spec), "raw.cif")


def test_invalid_selection_preflight_records_sanitized_start_and_failure_events(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, _ = _make_selection_fixture(tmp_path)
    selection_path.write_text('{"reference_answer":"must-not-leak"}', encoding="utf-8")
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")

    with pytest.raises(InputError, match="schema validation failed"):
        import_selection(
            workspace,
            selection_path=selection_path,
            asset_root=package_root,
            source_spec_path=source_spec,
        )

    events = [_event_body(event) for event in _events(workspace)]
    assert [event["event_type"] for event in events] == [
        "selection_import_started",
        "selection_import_failed",
    ]
    started = events[0]["payload"]
    failed = events[1]["payload"]
    assert started["request_digest"] == failed["request_digest"]
    assert started["selection_file_sha256"] == digest_bytes(selection_path.read_bytes())
    assert started["source_spec_file_sha256"] == digest_bytes(source_spec.read_bytes())
    assert started["source_spec_digest"] == digest(json.loads(source_spec.read_text(encoding="utf-8")))
    assert "selection_id_sha256" not in started
    assert failed["error_class"] == "InputError"
    _assert_audit_events_are_sanitized(
        events,
        str(package_root),
        str(selection_path),
        str(source_spec),
        "must-not-leak",
    )


def test_niivue_selection_row_without_a_typed_mask_fails_closed(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    selection["domains"]["chemistry"]["rows"][0]["mask_asset"] = None
    write_json(selection_path, selection)
    spec = json.loads(source_spec.read_text(encoding="utf-8"))
    spec["selection"]["file_sha256"] = digest_bytes(selection_path.read_bytes())
    write_json(source_spec, spec)

    with pytest.raises(InputError, match="niivue_deployed selection row requires a typed mask asset"):
        import_selection(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            selection_path=selection_path,
            asset_root=package_root,
            source_spec_path=source_spec,
        )


def test_selection_cli_import_then_compile_returns_pending_gate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    workspace = tmp_path / "workspace"
    assert (
        main(
            [
                "selection",
                "import",
                "--workspace",
                str(workspace),
                "--selection",
                str(selection_path),
                "--asset-root",
                str(package_root),
                "--source-spec",
                str(source_spec),
            ]
        )
        == 0
    )
    imported = json.loads(capsys.readouterr().out)
    mapping_path = tmp_path / "taxonomy-map.json"
    write_json(mapping_path, _mapping(selection, selection_path))

    assert (
        main(
            [
                "selection",
                "compile",
                "--workspace",
                str(workspace),
                "--selection-import",
                imported["selection_import_path"],
                "--taxonomy-mapping",
                str(mapping_path),
            ]
        )
        == 3
    )
    compiled = json.loads(capsys.readouterr().out)
    assert compiled["status"] == "blocked_taxonomy_mapping"


def test_compile_completed_event_records_input_mapping_and_output_digests(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")
    imported = import_selection(
        workspace,
        selection_path=selection_path,
        asset_root=package_root,
        source_spec_path=source_spec,
    )
    mapping_path = tmp_path / "complete-taxonomy-map.json"
    write_json(mapping_path, _complete_mapping(selection, selection_path))

    compiled = compile_selection(
        workspace,
        selection_import_path=Path(imported["selection_import_path"]),
        taxonomy_mapping_path=mapping_path,
    )

    assert compiled["status"] == "compiled_pending_downstream_gates"
    events = [_event_body(event) for event in _events(workspace)]
    assert [event["event_type"] for event in events][-2:] == [
        "selection_compile_started",
        "selection_compile_completed",
    ]
    started = events[-2]["payload"]
    payload = events[-1]["payload"]
    expected_request_digest = _request_digest(
        "selection_compile",
        selection_import_file_sha256=digest_bytes(Path(imported["selection_import_path"]).read_bytes()),
        selection_import_digest=imported["selection_import_digest"],
        selection_file_sha256=digest_bytes(selection_path.read_bytes()),
        source_spec_digest=imported["source_spec_digest"],
        taxonomy_mapping_file_sha256=digest_bytes(mapping_path.read_bytes()),
    )
    assert started["request_digest"] == expected_request_digest
    assert payload["request_digest"] == expected_request_digest
    assert payload["selection_import_digest"] == imported["selection_import_digest"]
    assert payload["taxonomy_mapping_file_sha256"] == digest_bytes(mapping_path.read_bytes())
    assert payload["taxonomy_mapping_digest"].startswith("sha256:")
    assert payload["generation_strategy_policy_digest"] == digest(
        _complete_mapping(selection, selection_path)["generation_strategy_by_source_access_track"]
    )
    assert payload["generation_strategy_counts"] == {
        "preserve_intent": 1,
        "reauthor_on_asset": 1,
    }
    assert payload["vnext_batch_manifest_digest"] == compiled["vnext_batch_manifest_digest"]
    assert payload["row_count"] == 2
    assert payload["asset_reference_count"] == 3


def test_recompile_preserves_prior_manifest_when_config_snapshot_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")
    imported = import_selection(
        workspace,
        selection_path=selection_path,
        asset_root=package_root,
        source_spec_path=source_spec,
    )
    mapping_path = tmp_path / "complete-taxonomy-map.json"
    write_json(mapping_path, _complete_mapping(selection, selection_path))

    original = compile_selection(
        workspace,
        selection_import_path=Path(imported["selection_import_path"]),
        taxonomy_mapping_path=mapping_path,
    )
    original_path = Path(original["vnext_batch_manifest_path"])

    original_target_config = selection_registry._target_config

    def revised_target_config(target_leaf: str) -> dict[str, str] | None:
        value = original_target_config(target_leaf)
        if target_leaf == "biology.docking_pose_plausibility":
            assert value is not None
            return {**value, "content_sha256": "sha256:" + "a" * 64}
        return value

    monkeypatch.setattr(selection_registry, "_target_config", revised_target_config)
    revised = compile_selection(
        workspace,
        selection_import_path=Path(imported["selection_import_path"]),
        taxonomy_mapping_path=mapping_path,
    )
    revised_path = Path(revised["vnext_batch_manifest_path"])

    assert original_path != revised_path
    assert original_path.is_file()
    assert revised_path.is_file()
    assert original["vnext_batch_manifest_digest"] != revised["vnext_batch_manifest_digest"]
    assert len(list(workspace.selection_batches.glob("*.vnext-batch.json"))) == 2


def test_compile_rejects_missing_generation_strategy_policy(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")
    imported = import_selection(
        workspace,
        selection_path=selection_path,
        asset_root=package_root,
        source_spec_path=source_spec,
    )
    mapping = _complete_mapping(selection, selection_path)
    del mapping["generation_strategy_by_source_access_track"]
    mapping_path = tmp_path / "missing-generation-strategy.json"
    write_json(mapping_path, mapping)

    with pytest.raises(InputError, match="generation_strategy_by_source_access_track"):
        compile_selection(
            workspace,
            selection_import_path=Path(imported["selection_import_path"]),
            taxonomy_mapping_path=mapping_path,
        )


def test_compile_rejects_raw_compute_preserve_intent_strategy(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, selection = _make_selection_fixture(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")
    imported = import_selection(
        workspace,
        selection_path=selection_path,
        asset_root=package_root,
        source_spec_path=source_spec,
    )
    mapping = _complete_mapping(selection, selection_path)
    mapping["generation_strategy_by_source_access_track"]["raw_compute"] = "preserve_intent"
    mapping_path = tmp_path / "raw-compute-preserve-intent.json"
    write_json(mapping_path, mapping)

    with pytest.raises(InputError, match="schema validation failed"):
        compile_selection(
            workspace,
            selection_import_path=Path(imported["selection_import_path"]),
            taxonomy_mapping_path=mapping_path,
        )


def test_unapproved_route_proposal_is_rejected_explicitly_and_audited(tmp_path: Path) -> None:
    selection_path, package_root, source_spec, _ = _make_selection_fixture(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")
    imported = import_selection(
        workspace,
        selection_path=selection_path,
        asset_root=package_root,
        source_spec_path=source_spec,
    )
    proposal_path = tmp_path / "route-proposal.draft.json"
    write_json(
        proposal_path,
        {
            "schema_version": "sciencevision.qagen.vnext_route_proposal.v0.draft",
            "status": "draft_unapproved",
            "approval": {"human_approved": False},
        },
    )

    with pytest.raises(
        InputError,
        match="unapproved route proposal.*cannot be consumed as an approved taxonomy mapping",
    ):
        compile_selection(
            workspace,
            selection_import_path=Path(imported["selection_import_path"]),
            taxonomy_mapping_path=proposal_path,
        )

    events = [_event_body(event) for event in _events(workspace)]
    assert [event["event_type"] for event in events][-2:] == [
        "selection_compile_started",
        "selection_compile_blocked",
    ]
    started = events[-2]["payload"]
    blocked = events[-1]["payload"]
    assert started["request_digest"] == blocked["request_digest"]
    assert started["selection_import_digest"] == imported["selection_import_digest"]
    assert started["taxonomy_mapping_file_sha256"] == digest_bytes(proposal_path.read_bytes())
    assert blocked["phase"] == "load_taxonomy_mapping"
    assert blocked["blocker"] == "invalid_or_untrusted_compile_input"
    assert blocked["error_class"] == "InputError"
    assert blocked["taxonomy_mapping_file_sha256"] == digest_bytes(proposal_path.read_bytes())
    assert "route-proposal.draft.json" not in json.dumps(blocked, sort_keys=True)
