from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import threading
from typing import Any

import pytest
import yaml

from qagen.cli import main
from qagen.contracts import digest
from qagen.errors import InvalidStateError, QuarantinedError
from qagen.paths import WorkspacePaths
from qagen.seeds import registry as seed_registry

from conftest import write_json, write_jsonl


SOURCE_ENV = "QAGEN_EV2_SOURCE_ROOT"
PACKAGE_ID = "ev2-test-package-v1.0.2"
REVIEW_DISTRIBUTION = {
    "verified": 3,
    "minor_fix": 2,
    "factual_error": 6,
    "unscorable": 4,
}
FILE_ROLES = {
    "manifest": {"path": "MANIFEST.json", "format": "json", "parser_contract": "ev2_manifest_v1"},
    "version_note": {"path": "README.md", "format": "markdown", "parser_contract": "ev2_version_note_v1"},
    "tasks": {"path": "tasks/tasks.jsonl", "format": "jsonl", "parser_contract": "ev2_tasks_v1"},
    "expert_review": {
        "path": "provenance/expert-review.json",
        "format": "json",
        "parser_contract": "ev2_expert_review_v1",
    },
    "asset_manifest": {
        "path": "provenance/asset-manifest.jsonl",
        "format": "jsonl",
        "parser_contract": "ev2_asset_manifest_v1",
    },
}
TAXONOMY_ALIAS_DOCUMENT = {
    "schema_version": 1,
    "kind": "qagen_subdomain_aliases",
    "id": "ev2_vnext_subdomain_aliases",
    "version": 1,
    "aliases": [
        {
            "source_leaf": "chemistry.chirality",
            "target_leaf": "chemistry.stereochemistry_chirality",
        }
    ],
}


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _inventory_digest(package_root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for path in sorted(package_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.name == ".DS_Store":
            continue
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "type": "file",
                    "bytes": path.stat().st_size,
                    "sha256": f"sha256:{_file_sha256(path)}",
                }
            )
    return digest(entries)


def _make_seed_source(tmp_path: Path) -> tuple[Path, Path]:
    package_root = tmp_path / "ev2-test-package-v1.0.3"
    statuses = [
        *(["verified"] * REVIEW_DISTRIBUTION["verified"]),
        *(["minor_fix"] * REVIEW_DISTRIBUTION["minor_fix"]),
        *(["factual_error"] * REVIEW_DISTRIBUTION["factual_error"]),
        *(["unscorable"] * REVIEW_DISTRIBUTION["unscorable"]),
    ]
    tasks: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    for index, status in enumerate(statuses, start=1):
        asset_path = package_root / "assets" / f"asset-{index:02d}.dat"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(f"fixture asset {index}\n".encode("ascii"))
        asset_digest = _file_sha256(asset_path)
        repo_path = f"crawl_data/fixture/asset-{index:02d}.dat"
        task_id = f"fixture-task-{index:02d}"
        tasks.append(
            {
                "task_id": task_id,
                "domain": "biology",
                "sub_domain": {"slug": "docking-pose-plausibility"},
                "question": f"RAW QUESTION {index}",
                "answer_schema": {"kind": "short_text", "fields": ["answer"]},
                "asset": {
                    "repo_path": repo_path,
                    "sha256": asset_digest,
                    "viewer": "molstar",
                },
                "expert_review": {"verdict_class": status},
                "provenance": {"source_suite": "EV2_Pilot"},
                "reference_answer": {"status": "expert_reference", "value": f"RAW ANSWER {index}"},
                "tool_policy": {"allowed_tools": ["inspect"]},
                "v_class": "V3",
            }
        )
        assets.append(
            {
                "byte_size": asset_path.stat().st_size,
                "package_path": f"assets/asset-{index:02d}.dat",
                "repo_path": repo_path,
                "sha256": asset_digest,
            }
        )
        anchors.append({"task_id": task_id})

    write_jsonl(package_root / FILE_ROLES["tasks"]["path"], tasks)
    write_jsonl(package_root / FILE_ROLES["asset_manifest"]["path"], assets)
    write_json(package_root / FILE_ROLES["expert_review"]["path"], {"reviews": 15, "anchors": anchors})
    write_json(
        package_root / FILE_ROLES["manifest"]["path"],
        {
            "schema_version": "sciencevision.ev2_pilot.sample_cases_by_type.v1",
            "package_id": PACKAGE_ID,
            "version": "1.0.2",
            "task_count": 15,
            "tasks_path": FILE_ROLES["tasks"]["path"],
            "expert_review": {"manifest": FILE_ROLES["expert_review"]["path"]},
            "asset_closure": {"manifest": FILE_ROLES["asset_manifest"]["path"]},
        },
    )
    version_note = package_root / FILE_ROLES["version_note"]["path"]
    version_note.write_text(f"# Fixture EV2 package\n\nPackage: `{PACKAGE_ID}`\n", encoding="utf-8")
    (package_root / ".DS_Store").write_bytes(b"ignored metadata")

    source_spec_path = tmp_path / "configs" / "sources" / "ev2-source-spec.json"
    alias_path = source_spec_path.parent / "aliases" / "ev2-subdomain-aliases.yaml"
    write_json(alias_path, TAXONOMY_ALIAS_DOCUMENT)
    source_spec = {
        "schema_version": 1,
        "kind": "qagen_seed_source_spec",
        "id": "ev2-cli-acceptance",
        "importer": "ev2_local_package_v1",
        "mode": "controlled",
        "root_env": SOURCE_ENV,
        "expected_basename": package_root.name,
        "file_roles": FILE_ROLES,
        "taxonomy_aliases": {
            "path": "aliases/ev2-subdomain-aliases.yaml",
            "version": 1,
            "content_digest": digest(TAXONOMY_ALIAS_DOCUMENT),
        },
        "version_policy": "require_human_resolution_on_conflict",
        "local_only": True,
        "production_eligible": False,
        "expected_inventory_digest": _inventory_digest(package_root),
        "expected_package": {
            "package_id": PACKAGE_ID,
            "version_labels": {
                "directory": "v1.0.3",
                "manifest": "1.0.2",
                "package_id": "v1.0.2",
                "version_note": "v1.0.2",
            },
            "case_count": 15,
            "review_distribution": REVIEW_DISTRIBUTION,
            "provenance": {
                "required_fields": [
                    "task_id",
                    "domain",
                    "sub_domain.slug",
                    "expert_review.verdict_class",
                    "provenance.source_suite",
                    "asset.repo_path",
                    "asset.sha256",
                    "reference_answer.status",
                ],
                "require_asset_sha256": True,
                "require_asset_package_path": True,
                "require_asset_byte_size": True,
                "require_source_suite": True,
            },
        },
    }
    write_json(source_spec_path, source_spec)
    return package_root, source_spec_path


def _seed_sync_args(workspace: Path, package_root: Path, source_spec: Path) -> list[str]:
    return [
        "seed",
        "sync",
        "--workspace",
        str(workspace),
        "--package-root",
        str(package_root),
        "--source-spec",
        str(source_spec),
    ]


def _refresh_inventory_lock(source_spec: Path, package_root: Path) -> None:
    spec = json.loads(source_spec.read_text(encoding="utf-8"))
    spec["expected_inventory_digest"] = _inventory_digest(package_root)
    write_json(source_spec, spec)


def test_seed_cli_requires_bound_resolution_then_emits_only_verified_positive_lessons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setenv(SOURCE_ENV, str(package_root))

    assert main(_seed_sync_args(workspace, package_root, source_spec)) == 3
    pending = json.loads(capsys.readouterr().out)
    assert pending["eligible"] is False
    assert pending["observed_versions"] == {
        "directory": "v1.0.3",
        "manifest": "1.0.2",
        "package_id": "v1.0.2",
        "version_note": "v1.0.2",
    }

    assert main(
        [
            "seed",
            "accept-version",
            "--workspace",
            str(workspace),
            "--request",
            pending["request_path"],
            "--reviewer",
            "source-owner",
            "--resolved-version",
            "1.0.2",
            "--decision",
            "accept",
            "--rationale",
            "The reviewed manifest and version note are authoritative for this immutable fixture inventory.",
        ]
    ) == 0
    capsys.readouterr()

    assert main(_seed_sync_args(workspace, package_root, source_spec)) == 0
    accepted = json.loads(capsys.readouterr().out)
    cards = json.loads(Path(accepted["cards_path"]).read_text(encoding="utf-8"))
    assert len(accepted["lesson_paths"]) == 1
    lesson = yaml.safe_load(Path(accepted["lesson_paths"][0]).read_text(encoding="utf-8"))

    assert accepted["eligible"] is True
    assert accepted["task_count"] == 15
    assert accepted["local_only"] is True
    assert accepted["production_eligible"] is False
    assert sum(card["source_status"] == "verified" for card in cards["body"]["cards"]) == 3
    assert len(lesson["body"]["positive_lessons"]) == 3
    assert len(lesson["body"]["anti_pattern_lessons"]) == 12
    assert "RAW QUESTION" not in json.dumps(cards, sort_keys=True)


def test_seed_normalizer_consumes_digest_locked_chirality_alias_without_leaking_source_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    tasks_path = package_root / FILE_ROLES["tasks"]["path"]
    tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines()]
    tasks[0]["domain"] = "chemistry"
    tasks[0]["sub_domain"] = {"slug": "chirality"}
    write_jsonl(tasks_path, tasks)
    _refresh_inventory_lock(source_spec, package_root)
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    monkeypatch.setenv(SOURCE_ENV, str(package_root))

    pending = seed_registry.sync_seed_package(paths, package_root, source_spec_path=source_spec)
    seed_registry.accept_source_version(
        paths,
        request_path=pending["request_path"],
        reviewer="source-owner",
        resolved_version="1.0.2",
        decision="accept",
        rationale="Authorized immutable fixture version conflict.",
    )
    accepted = seed_registry.sync_seed_package(paths, package_root, source_spec_path=source_spec)
    cards = json.loads(Path(accepted["cards_path"]).read_text(encoding="utf-8"))
    chirality_cards = [
        card
        for card in cards["body"]["cards"]
        if card["leaf_id"] == "chemistry.stereochemistry_chirality"
    ]
    lesson_path = next(
        Path(path)
        for path in accepted["lesson_paths"]
        if path.endswith("chemistry/stereochemistry_chirality.yaml")
    )
    lesson = yaml.safe_load(lesson_path.read_text(encoding="utf-8"))

    assert len(chirality_cards) == 1
    assert chirality_cards[0]["source_status"] == "verified"
    assert len(lesson["body"]["positive_lessons"]) == 1
    assert lesson["body"]["anti_pattern_lessons"] == []
    assert cards["body"]["taxonomy_alias_lock"]["content_digest"] == digest(TAXONOMY_ALIAS_DOCUMENT)
    assert lesson["body"]["taxonomy_alias_lock"]["content_digest"] == digest(TAXONOMY_ALIAS_DOCUMENT)
    public_payload = json.dumps({"cards": chirality_cards, "lesson": lesson}, sort_keys=True)
    for private_value in (
        "fixture-task-01",
        "RAW QUESTION 1",
        "RAW ANSWER 1",
        "crawl_data/fixture/asset-01.dat",
    ):
        assert private_value not in public_payload


def test_seed_sync_rejects_taxonomy_alias_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    spec = json.loads(source_spec.read_text(encoding="utf-8"))
    spec["taxonomy_aliases"]["content_digest"] = "sha256:" + "0" * 64
    write_json(source_spec, spec)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))

    with pytest.raises(QuarantinedError, match="taxonomy alias document does not match"):
        seed_registry.sync_seed_package(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            package_root,
            source_spec_path=source_spec,
        )


def test_concurrent_conflicting_source_version_decisions_commit_exactly_one_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_value(workspace)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    pending = seed_registry.sync_seed_package(paths, package_root, source_spec_path=source_spec)
    barrier = threading.Barrier(3)

    def decide(value: str) -> dict[str, object] | Exception:
        barrier.wait()
        try:
            return seed_registry.accept_source_version(
                paths,
                request_path=pending["request_path"],
                reviewer=f"source-owner-{value}",
                resolved_version="1.0.2",
                decision=value,
                rationale="Concurrent terminal decision test.",
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, value) for value in ("accept", "reject")]
        barrier.wait()
        results = [future.result() for future in futures]

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], InvalidStateError)
    receipts = list((paths.receipts / "source").glob("*.resolution.json"))
    assert receipts == [Path(successes[0]["receipt_path"])]


def test_failed_source_version_decision_commit_removes_its_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    pending = seed_registry.sync_seed_package(paths, package_root, source_spec_path=source_spec)

    def fail_event(*args: object, **kwargs: object) -> None:
        raise RuntimeError("event commit failed")

    monkeypatch.setattr(seed_registry, "append_event", fail_event)
    with pytest.raises(RuntimeError, match="event commit failed"):
        seed_registry.accept_source_version(
            paths,
            request_path=pending["request_path"],
            reviewer="source-owner",
            resolved_version="1.0.2",
            decision="accept",
            rationale="Authorized fixture resolution.",
        )

    assert list((paths.receipts / "source").glob("*.resolution.json")) == []


def test_seed_cli_rejects_package_root_that_does_not_match_source_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    monkeypatch.setenv(SOURCE_ENV, str(other_root))

    assert main(_seed_sync_args(tmp_path / "workspace", package_root, source_spec)) == 5
    captured = capsys.readouterr()
    assert "does not match the SourceSpec root_env directory" in captured.err
    assert captured.out == ""


def test_seed_cli_quarantines_inventory_drift_before_version_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    (package_root / "unexpected.txt").write_text("not covered by the source lock\n", encoding="utf-8")

    assert main(_seed_sync_args(tmp_path / "workspace", package_root, source_spec)) == 5
    captured = capsys.readouterr()
    assert "inventory digest does not match" in captured.err
    assert captured.out == ""


def test_seed_sync_quarantines_equal_length_role_rewrite_after_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    workspace = WorkspacePaths.from_value(tmp_path / "workspace")
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    expected_inventory_digest = json.loads(source_spec.read_text(encoding="utf-8"))[
        "expected_inventory_digest"
    ]
    original_load = seed_registry._load_role_inputs

    def load_then_rewrite(snapshot: Any, spec: Any) -> Any:
        loaded = original_load(snapshot, spec)
        tasks_path = package_root / FILE_ROLES["tasks"]["path"]
        before = tasks_path.read_bytes()
        after = before.replace(b"RAW QUESTION 1", b"NEW QUESTION 1", 1)
        assert len(after) == len(before)
        assert after != before
        tasks_path.write_bytes(after)
        return loaded

    monkeypatch.setattr(seed_registry, "_load_role_inputs", load_then_rewrite)

    with pytest.raises(QuarantinedError, match="seed source changed during sync"):
        seed_registry.sync_seed_package(workspace, package_root, source_spec_path=source_spec)

    assert list(workspace.catalog.glob("*.catalog.json")) == []
    assert not (workspace.catalog / "current.json").exists()
    quarantine_paths = list((workspace.snapshots / "seed").glob("*.quarantine.json"))
    assert len(quarantine_paths) == 1
    quarantine = json.loads(quarantine_paths[0].read_text(encoding="utf-8"))
    assert quarantine["body"]["inventory_digest"] == expected_inventory_digest


def test_seed_sync_quarantines_asset_bytes_that_do_not_match_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    asset_path = package_root / "assets" / "asset-01.dat"
    original = asset_path.read_bytes()
    asset_path.write_bytes(b"X" + original[1:])
    _refresh_inventory_lock(source_spec, package_root)

    with pytest.raises(QuarantinedError, match="SHA-256 does not match package asset"):
        seed_registry.sync_seed_package(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            package_root,
            source_spec_path=source_spec,
        )


def test_seed_sync_uses_package_path_not_repo_path_for_asset_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    provenance_only_path = "/unmounted/provenance/asset-01.dat"
    assets_path = package_root / FILE_ROLES["asset_manifest"]["path"]
    tasks_path = package_root / FILE_ROLES["tasks"]["path"]
    assets = [json.loads(line) for line in assets_path.read_text(encoding="utf-8").splitlines()]
    tasks = [json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines()]
    assets[0]["repo_path"] = provenance_only_path
    tasks[0]["asset"]["repo_path"] = provenance_only_path
    write_jsonl(assets_path, assets)
    write_jsonl(tasks_path, tasks)
    _refresh_inventory_lock(source_spec, package_root)

    result = seed_registry.sync_seed_package(
        WorkspacePaths.from_value(tmp_path / "workspace"),
        package_root,
        source_spec_path=source_spec,
    )

    assert result["eligible"] is False  # The fixture's version conflict is unrelated to asset closure.
    assert result["request_path"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("byte_size", 999, "byte_size does not match package asset"),
        ("package_path", "../outside.dat", "package_path is unsafe or missing"),
    ],
)
def test_seed_sync_quarantines_invalid_asset_package_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    manifest_path = package_root / FILE_ROLES["asset_manifest"]["path"]
    assets = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    assets[0][field] = value
    write_jsonl(manifest_path, assets)
    _refresh_inventory_lock(source_spec, package_root)

    with pytest.raises(QuarantinedError, match=message):
        seed_registry.sync_seed_package(
            WorkspacePaths.from_value(tmp_path / "workspace"),
            package_root,
            source_spec_path=source_spec,
        )


def test_seed_sync_serializes_concurrent_access_to_one_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root, source_spec = _make_seed_source(tmp_path)
    monkeypatch.setenv(SOURCE_ENV, str(package_root))
    original_snapshot = seed_registry.snapshot_trusted_tree
    first_snapshot_entered = threading.Event()
    release_first_snapshot = threading.Event()
    second_snapshot_entered = threading.Event()
    failures: list[BaseException] = []

    def blocking_snapshot(*args: Any, **kwargs: Any) -> Any:
        if (
            threading.current_thread().name == "first-seed-sync"
            and not first_snapshot_entered.is_set()
        ):
            first_snapshot_entered.set()
            assert release_first_snapshot.wait(timeout=5)
        if threading.current_thread().name == "second-seed-sync":
            second_snapshot_entered.set()
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(seed_registry, "snapshot_trusted_tree", blocking_snapshot)

    def sync(workspace_name: str) -> None:
        try:
            seed_registry.sync_seed_package(
                WorkspacePaths.from_value(tmp_path / workspace_name),
                package_root,
                source_spec_path=source_spec,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    first = threading.Thread(target=sync, args=("workspace-one",), name="first-seed-sync")
    second = threading.Thread(target=sync, args=("workspace-two",), name="second-seed-sync")
    first.start()
    assert first_snapshot_entered.wait(timeout=5)
    second.start()

    assert not second_snapshot_entered.wait(timeout=0.1)
    release_first_snapshot.set()
    assert second_snapshot_entered.wait(timeout=5)

    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
