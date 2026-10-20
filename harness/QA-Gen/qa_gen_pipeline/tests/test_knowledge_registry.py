from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import pytest
import yaml

from qagen.contracts import digest, digest_bytes, make_envelope
from qagen.cli import main
from qagen.errors import InputError, InvalidStateError
from qagen.knowledge.registry import (
    KnowledgeAuthorizationError,
    KnowledgeRegistry,
    KnowledgeReviewBindingError,
    KnowledgeTransitionError,
    approve_knowledge_snapshot,
    authorize_knowledge_compile,
    compile_knowledge_draft,
    sync_knowledge_sources,
)
from qagen.knowledge import registry as knowledge_registry
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json, atomic_write_yaml, read_json


def _registry(tmp_path: Path) -> KnowledgeRegistry:
    return KnowledgeRegistry(
        private_root=tmp_path / "private",
        knowledge_root=tmp_path / "knowledge",
    )


def _authorization(registry: KnowledgeRegistry, request: dict, *, expires_at: datetime):
    return registry.authorize(
        request,
        owner="knowledge-owner",
        allowed_workers={"knowledge_curator"},
        allowed_models={"curator-test-model"},
        max_budget_usd=1.0,
        expires_at=expires_at,
    )


def _compile(
    registry: KnowledgeRegistry,
    request: dict,
    authorization,
    payload: dict,
    *,
    worker: str = "knowledge_curator",
    model: str = "curator-test-model",
    reserved_budget_usd: float = 0.5,
):
    return registry.compile_draft(
        request,
        authorization=authorization,
        worker=worker,
        model=model,
        reserved_budget_usd=reserved_budget_usd,
        draft_payload=payload,
    )


@pytest.mark.parametrize(
    "authorization_delta,worker,model,reserved_budget_usd",
    [
        (-timedelta(seconds=1), "knowledge_curator", "curator-test-model", 0.5),
        (timedelta(hours=1), "unapproved-worker", "curator-test-model", 0.5),
        (timedelta(hours=1), "knowledge_curator", "unapproved-model", 0.5),
        (timedelta(hours=1), "knowledge_curator", "curator-test-model", 1.01),
    ],
)
def test_compile_fails_closed_for_invalid_authorization(
    tmp_path: Path,
    knowledge_request: dict,
    knowledge_draft_payload: dict,
    authorization_delta: timedelta,
    worker: str,
    model: str,
    reserved_budget_usd: float,
) -> None:
    registry = _registry(tmp_path)
    authorization = _authorization(
        registry,
        knowledge_request,
        expires_at=datetime.now(timezone.utc) + authorization_delta,
    )

    with pytest.raises(KnowledgeAuthorizationError):
        _compile(
            registry,
            knowledge_request,
            authorization,
            knowledge_draft_payload,
            worker=worker,
            model=model,
            reserved_budget_usd=reserved_budget_usd,
        )


def test_compile_fails_closed_when_request_digest_differs_from_authorization(
    tmp_path: Path, knowledge_request: dict, knowledge_draft_payload: dict
) -> None:
    registry = _registry(tmp_path)
    authorization = _authorization(
        registry,
        knowledge_request,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    changed_request = {**knowledge_request, "compiler_digest": "f" * 64}

    with pytest.raises(KnowledgeAuthorizationError):
        _compile(registry, changed_request, authorization, knowledge_draft_payload)


def test_approval_binds_draft_input_and_authorization_digests(
    tmp_path: Path, knowledge_request: dict, knowledge_draft_payload: dict
) -> None:
    registry = _registry(tmp_path)
    authorization = _authorization(
        registry,
        knowledge_request,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    draft = _compile(registry, knowledge_request, authorization, knowledge_draft_payload)
    decision = {
        "snapshot_digest": draft.snapshot_digest,
        "input_digest": draft.input_digest,
        "compile_authorization_ref": authorization.digest,
        "reviewer": "biology-domain-expert",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "decision": "approved",
    }

    approved = registry.approve(draft, decision)
    assert approved.status == "approved"

    bad_decision = {**decision, "snapshot_digest": "0" * 64}
    with pytest.raises(KnowledgeReviewBindingError):
        registry.approve(draft, bad_decision)


def test_illegal_knowledge_status_transition_is_rejected(
    tmp_path: Path, knowledge_request: dict, knowledge_draft_payload: dict
) -> None:
    registry = _registry(tmp_path)
    authorization = _authorization(
        registry,
        knowledge_request,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    draft = _compile(registry, knowledge_request, authorization, knowledge_draft_payload)

    with pytest.raises(KnowledgeTransitionError):
        registry.transition(draft, "approved")


def _workspace_knowledge_draft(
    tmp_path: Path,
    *,
    leaf: str = "biology.docking_pose_plausibility",
) -> tuple[WorkspacePaths, dict[str, object]]:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    domain, subdomain = leaf.split(".", 1)
    expert = tmp_path / "expert.md"
    taxonomy = tmp_path / "taxonomy.md"
    expert.write_text("# Expert source\n\nReviewed content.\n", encoding="utf-8")
    taxonomy.write_text("# Visual target\n\nControlled target.\n", encoding="utf-8")
    sync_knowledge_sources(paths, [f"expert:expert-test:{expert}", f"taxonomy:taxonomy-test:{taxonomy}"])
    seed_catalog_digest = digest({"fixture": "seed-catalog"})
    lesson_path = paths.knowledge / "seed-lessons" / domain / f"{subdomain}.yaml"
    seed_index = make_envelope(
        kind="seed_index",
        identifier="seed-index-fixture",
        status="eligible",
        body={
            "mode": "workspace_registry",
            "inventory_digest": digest({"fixture": "inventory"}),
            "eligible": True,
            "seed_catalog_digest": seed_catalog_digest,
            "catalog_path": ".qagen/private/catalog/fixture.catalog.json",
            "cards_digest": digest({"fixture": "cards"}),
            "cards_path": ".qagen/private/cards/fixture.cards.json",
            "package_name": "fixture-package",
            "source_spec_path": "configs/sources/fixture.yaml",
            "source_spec_digest": digest({"fixture": "source-spec"}),
            "local_only": True,
            "production_eligible": False,
            "observed_versions": {},
            "resolution_ref": None,
            "lesson_paths": [str(lesson_path.relative_to(paths.root))],
            "entries": [],
        },
    )
    atomic_write_json(paths.catalog / "current.json", seed_index, private=True)
    lesson = make_envelope(
        kind="seed_lesson_set",
        identifier=leaf,
        status="eligible",
        body={
            "seed_catalog_digest": seed_catalog_digest,
            "positive_lessons": [
                {
                    "visual_skills": ["pose-contact"],
                    "viewer_affordances": ["rotate"],
                    "answer_form": "short_text",
                }
            ],
            "anti_pattern_lessons": [],
        },
    )
    atomic_write_yaml(lesson_path, lesson)
    authorization = authorize_knowledge_compile(
        paths,
        leaf=leaf,
        expert_source_ids=["expert-test"],
        taxonomy_source_ids=["taxonomy-test"],
        owner="knowledge-owner",
        model="deterministic-local",
        max_tokens=0,
        max_cost_usd=0.0,
        ttl_seconds=3600,
    )
    draft = compile_knowledge_draft(paths, authorization_path=authorization["authorization_path"])
    return paths, draft


def _write_structured_source(
    path: Path,
    *,
    kind: str,
    source_id: str,
    leaf_id: str,
    content: dict[str, object],
    production_approval: bool = False,
    fixture_reference: dict[str, object] | None = None,
) -> None:
    source: dict[str, object] = {
        "schema_version": 1,
        "kind": kind,
        "id": source_id,
        "leaf_id": leaf_id,
        "source_status": "draft_reference",
        "production_approval": production_approval,
        "reference_sources": [],
        "content": content,
    }
    if fixture_reference is not None:
        source["fixture_reference"] = fixture_reference
    atomic_write_yaml(path, source)


def _write_feedback_source(
    path: Path,
    *,
    source_id: str,
    leaf_id: str,
    content: dict[str, object],
    source_status: str = "accepted",
    production_approval: bool = False,
    extra: dict[str, object] | None = None,
) -> None:
    source: dict[str, object] = {
        "schema_version": 1,
        "kind": "qagen_expert_feedback_source",
        "id": source_id,
        "leaf_id": leaf_id,
        "source_status": source_status,
        "production_approval": production_approval,
        "reference_sources": [],
        "content": content,
    }
    if extra:
        source.update(extra)
    atomic_write_yaml(path, source)


def _typed_feedback_content() -> dict[str, object]:
    return {
        "claims": [
            {
                "claim_id": "forbid-ui-instructions",
                "rule_id": "forbid_ui_or_operation_instruction",
            },
            {
                "claim_id": "viewer-evidence",
                "rule_id": "require_viewer_grounded_evidence",
            },
        ],
        "question_family_policy": {
            "allowed_families": [
                {"family_id": "local-property", "family_kind": "property"},
                {"family_id": "scene-comparison", "family_kind": "comparison"},
                {"family_id": "viewer-measurement", "family_kind": "measurement"},
            ],
            "quota": {
                "scope": "portfolio",
                "unit": "percentage_points",
                "measurement_max": 25,
                "non_measurement_min": 75,
            },
        },
        "gt_policy": {
            "gt_source_types": ["expert_annotation", "paired_comparison"],
            "expert_approval_required": True,
        },
    }


def test_structured_sources_compile_deidentified_draft_and_review_packet(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    leaf = "chemistry.stereochemistry_chirality"
    expert_path = tmp_path / "expert.yaml"
    taxonomy_path = tmp_path / "taxonomy.yaml"
    _write_structured_source(
        expert_path,
        kind="qagen_expert_knowledge_source",
        source_id="chirality-expert",
        leaf_id=leaf,
        fixture_reference={
            "id": leaf,
            "fixture_digest": digest({"fixture": "chirality"}),
            "use": "structure_only",
            "production_approval": False,
        },
        content={
            "scientific_scope": {"included": ["visible stereocenters"], "excluded": ["identity lookup"]},
            "scenario_cards": [
                {
                    "id": "center-assessment",
                    "observable_targets": ["four substituent paths"],
                    "valid_question_patterns": ["Require viewer-grounded configuration."],
                    "invalid_question_patterns": ["Infer configuration from metadata."],
                }
            ],
            "allowed_inference": ["viewer-visible connectivity"],
            "common_failure_modes": ["rotation confused with reflection"],
            "rubric_extensions": [{"id": "observability", "rule": "Require four visible paths."}],
        },
    )
    _write_structured_source(
        taxonomy_path,
        kind="qagen_visual_taxonomy_source",
        source_id="chirality-taxonomy",
        leaf_id=leaf,
        content={
            "visual_target_types": ["tetrahedral_stereocenter"],
            "forbidden_shortcuts": ["stereochemistry_toolkit"],
            "evidence_requirements": {
                "required_viewer_operations": ["inspect", "rotate"],
                "required_evidence": ["viewer trace"],
                "allowed_truth_modes": ["expert_visual_judgment"],
            },
            "requirements": {
                "asset_requirements": ["anonymous complete geometry"],
                "viewer_requirements": ["controlled rotation"],
                "answer_modes": ["structured center assessment"],
            },
        },
    )
    sync_knowledge_sources(
        paths,
        [f"expert:chirality-expert:{expert_path}", f"taxonomy:chirality-taxonomy:{taxonomy_path}"],
    )
    seed_catalog_digest = digest({"fixture": "chirality-seed-catalog"})
    lesson_path = paths.knowledge / "seed-lessons" / "chemistry" / "stereochemistry_chirality.yaml"
    lesson = make_envelope(
        kind="seed_lesson_set",
        identifier=leaf,
        status="eligible",
        body={
            "seed_catalog_digest": seed_catalog_digest,
            "positive_lessons": [
                {
                    "lesson_id": "seed-lesson-positive",
                    "origin_status": "verified",
                    "visual_skills": ["stereochemical_spatial_reasoning"],
                    "viewer_affordances": ["rotate"],
                    "answer_form": "categorical_with_evidence",
                }
            ],
            "anti_pattern_lessons": [
                {
                    "lesson_id": "seed-lesson-anti",
                    "origin_status": "factual_error",
                    "anti_pattern_classes": ["configuration_not_geometry_bound"],
                    "visual_skills": ["stereochemical_spatial_reasoning"],
                    "viewer_affordances": ["inspect"],
                    "answer_form": "categorical_with_evidence",
                }
            ],
            "taxonomy_alias_lock": {
                "id": "chirality-aliases",
                "version": 1,
                "content_digest": digest({"fixture": "alias"}),
            },
        },
    )
    atomic_write_yaml(lesson_path, lesson)
    seed_index = make_envelope(
        kind="seed_index",
        identifier="seed-index-chirality",
        status="eligible",
        body={
            "mode": "workspace_registry",
            "inventory_digest": digest({"fixture": "inventory"}),
            "eligible": True,
            "seed_catalog_digest": seed_catalog_digest,
            "catalog_path": ".qagen/private/catalog/chirality.catalog.json",
            "cards_digest": digest({"fixture": "cards"}),
            "cards_path": ".qagen/private/cards/chirality.cards.json",
            "package_name": "fixture-package",
            "source_spec_path": "configs/sources/fixture.yaml",
            "source_spec_digest": digest({"fixture": "source-spec"}),
            "local_only": True,
            "production_eligible": False,
            "observed_versions": {},
            "resolution_ref": None,
            "lesson_paths": [str(lesson_path.relative_to(paths.root))],
            "entries": [],
        },
    )
    atomic_write_json(paths.catalog / "current.json", seed_index, private=True)
    authorization = authorize_knowledge_compile(
        paths,
        leaf=leaf,
        expert_source_ids=["chirality-expert"],
        taxonomy_source_ids=["chirality-taxonomy"],
        owner="knowledge-owner",
        model="deterministic-local",
        max_tokens=0,
        max_cost_usd=0.0,
        ttl_seconds=3600,
    )

    result = compile_knowledge_draft(paths, authorization_path=authorization["authorization_path"])

    draft = read_json(Path(result["draft_path"]))
    packet = read_json(Path(result["review_packet_path"]))
    assert draft["body"]["scientific_scope"]["included"] == ["visible stereocenters"]
    assert draft["body"]["visual_target_types"] == ["tetrahedral_stereocenter"]
    assert draft["body"]["provenance"]["fixture_reference"]["use"] == "structure_only"
    assert draft["body"]["provenance"]["fixture_reference"]["production_approval"] is False
    assert {item["disposition"] for item in draft["body"]["provenance"]["seed_lesson_refs"]} == {
        "anti_pattern",
        "positive",
    }
    assert set(draft["body"]["forbidden_shortcuts"]) >= {
        "raw_asset_read",
        "metadata_lookup",
        "network_search",
        "arbitrary_compute",
        "stereochemistry_toolkit",
    }
    assert packet["kind"] == "knowledge_review_packet"
    assert packet["status"] == "pending_human_approval"
    assert packet["body"]["draft_digest"] == result["draft_digest"]
    assert packet["body"]["approval_gate"]["satisfied"] is False
    serialized = str(packet)
    assert "asset_path" not in serialized
    assert "task_id" not in serialized
    assert "ground_truth" not in serialized
    assert not (paths.knowledge / "index.yaml").exists()
    assert list((paths.knowledge / "compiled").glob("**/*")) == []


def test_structured_source_cannot_claim_production_approval(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    expert_path = tmp_path / "expert.yaml"
    _write_structured_source(
        expert_path,
        kind="qagen_expert_knowledge_source",
        source_id="expert",
        leaf_id="chemistry.stereochemistry_chirality",
        production_approval=True,
        content={
            "scientific_scope": {},
            "scenario_cards": [],
            "allowed_inference": [],
            "common_failure_modes": [],
            "rubric_extensions": [],
        },
    )

    with pytest.raises(InputError, match="cannot claim production approval"):
        sync_knowledge_sources(paths, [f"expert:expert:{expert_path}"])


def test_concurrent_conflicting_draft_decisions_commit_one_receipt_and_consistent_index(
    tmp_path: Path,
) -> None:
    paths, draft = _workspace_knowledge_draft(tmp_path)
    barrier = threading.Barrier(3)

    def decide(value: str) -> dict[str, object] | Exception:
        barrier.wait()
        try:
            return approve_knowledge_snapshot(
                paths,
                draft_path=draft["draft_path"],
                reviewer=f"domain-expert-{value}",
                decision=value,
                rationale="Concurrent terminal decision test.",
            )
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, value) for value in ("approved", "rejected")]
        barrier.wait()
        results = [future.result() for future in futures]

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], InvalidStateError)
    winner = successes[0]
    receipts = list((paths.receipts / "knowledge").glob("*.review.json"))
    assert receipts == [Path(winner["review_path"])]
    if winner["decision"] == "approved":
        index = yaml.safe_load((paths.knowledge / "index.yaml").read_text(encoding="utf-8"))
        entry = index["body"]["entries"]["biology.docking_pose_plausibility"]
        assert entry["review_digest"] == winner["review_digest"]
        assert Path(winner["snapshot_path"]).is_file()
    else:
        assert not (paths.knowledge / "index.yaml").exists()


def test_failed_knowledge_index_commit_rolls_back_snapshot_and_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, draft = _workspace_knowledge_draft(tmp_path)

    def fail_index(*args: object, **kwargs: object) -> None:
        raise RuntimeError("index commit failed")

    monkeypatch.setattr(knowledge_registry, "_write_index_entry", fail_index)
    with pytest.raises(RuntimeError, match="index commit failed"):
        approve_knowledge_snapshot(
            paths,
            draft_path=draft["draft_path"],
            reviewer="domain-expert",
            decision="approved",
        )

    assert list((paths.receipts / "knowledge").glob("*.review.json")) == []
    assert not (paths.knowledge / "compiled" / "biology" / "docking_pose_plausibility.yaml").exists()
    assert not (paths.knowledge / "index.yaml").exists()


def test_accepted_typed_feedback_binds_question_and_gt_policies_and_supersedes_on_change(
    tmp_path: Path,
) -> None:
    paths, _ = _workspace_knowledge_draft(tmp_path)
    leaf = "biology.docking_pose_plausibility"
    feedback_path = tmp_path / "expert-feedback.yaml"
    _write_feedback_source(
        feedback_path,
        source_id="feedback-test",
        leaf_id=leaf,
        content=_typed_feedback_content(),
    )
    sync_knowledge_sources(paths, [f"feedback:feedback-test:{feedback_path}"])
    authorization = authorize_knowledge_compile(
        paths,
        leaf=leaf,
        expert_source_ids=["expert-test"],
        taxonomy_source_ids=["taxonomy-test"],
        feedback_source_ids=["feedback-test"],
        owner="knowledge-owner",
        model="deterministic-local",
        max_tokens=0,
        max_cost_usd=0.0,
        ttl_seconds=3600,
    )
    result = compile_knowledge_draft(paths, authorization_path=authorization["authorization_path"])

    draft = read_json(Path(result["draft_path"]))
    packet = read_json(Path(result["review_packet_path"]))
    policy = draft["body"]["feedback_policy"]
    assert policy["question_family_policy"]["quota"] == {
        "scope": "portfolio",
        "unit": "percentage_points",
        "measurement_max": 25,
        "non_measurement_min": 75,
    }
    assert policy["gt_policy"] == {
        "expert_approval_required": True,
        "gt_source_types": ["expert_annotation", "paired_comparison"],
    }
    assert draft["body"]["provenance"]["feedback_policy_digest"] == digest(policy)
    assert draft["body"]["provenance"]["expert_feedback_refs"] == [
        {
            "source_id": "feedback-test",
            "content_digest": digest_bytes(feedback_path.read_bytes()),
        }
    ]
    assert packet["body"]["review_sections"]["feedback_policy"] == policy
    assert packet["body"]["source_boundaries"]["expert_feedback_sources"] == [
        {
            "source_id": "feedback-test",
            "content_digest": digest_bytes(feedback_path.read_bytes()),
            "source_status": "accepted",
            "production_approval": False,
        }
    ]

    approved = approve_knowledge_snapshot(
        paths,
        draft_path=result["draft_path"],
        reviewer="domain-expert",
        decision="approved",
    )
    index = yaml.safe_load((paths.knowledge / "index.yaml").read_text(encoding="utf-8"))
    entry = index["body"]["entries"][leaf]
    assert entry["source_digests"]["feedback:feedback-test"] == digest_bytes(feedback_path.read_bytes())
    assert Path(approved["snapshot_path"]).is_file()

    changed = _typed_feedback_content()
    changed["question_family_policy"] = {
        **changed["question_family_policy"],
        "quota": {
            "scope": "portfolio",
            "unit": "percentage_points",
            "measurement_max": 0,
            "non_measurement_min": 100,
        },
    }
    _write_feedback_source(
        feedback_path,
        source_id="feedback-test",
        leaf_id=leaf,
        content=changed,
    )
    sync_knowledge_sources(paths, [f"feedback:feedback-test:{feedback_path}"])

    superseded = yaml.safe_load((paths.knowledge / "index.yaml").read_text(encoding="utf-8"))
    assert superseded["body"]["entries"][leaf]["state"] == "superseded"
    assert superseded["body"]["entries"][leaf]["superseded_reason"] == "source_changed:feedback:feedback-test"


def test_medical_feedback_policy_is_typed_digest_bound_and_keeps_curated_label_provenance(
    tmp_path: Path,
) -> None:
    leaf = "medical.volumetric_target_geometry"
    paths, _ = _workspace_knowledge_draft(tmp_path, leaf=leaf)
    feedback_path = tmp_path / "medical-feedback.json"
    content = _typed_feedback_content()
    content["gt_policy"] = {
        "gt_source_types": ["curated_source_label", "expert_annotation"],
        "expert_approval_required": True,
        "curated_source_label_provenance": {
            "source_snapshot_digest": digest({"fixture": "verbatim-medical-annotation"}),
            "annotation_id": "finding-label-row-14",
        },
    }
    content["subdomain_semantics"] = "question_type_only"
    content["asset_facets_schema"] = {
        "facets": [
            {"facet_id": "modality", "cardinality": "single", "required": True},
            {"facet_id": "target-structure", "cardinality": "multiple", "required": True},
        ]
    }
    content["medical_visibility_policy"] = {
        "solver_visible_segmentation": False,
        "hidden_reference_mask_allowed": "conditional",
        "label_identity_policy": "preserve",
        "semantic_scoring_required": True,
        "duplicate_coverage_key": "target-geometry",
    }
    _write_feedback_source(
        feedback_path,
        source_id="medical-feedback",
        leaf_id=leaf,
        content=content,
    )
    sync_knowledge_sources(paths, [f"feedback:medical-feedback:{feedback_path}"])
    authorization = authorize_knowledge_compile(
        paths,
        leaf=leaf,
        expert_source_ids=["expert-test"],
        taxonomy_source_ids=["taxonomy-test"],
        feedback_source_ids=["medical-feedback"],
        owner="knowledge-owner",
        model="deterministic-local",
        max_tokens=0,
        max_cost_usd=0.0,
        ttl_seconds=3600,
    )
    result = compile_knowledge_draft(paths, authorization_path=authorization["authorization_path"])
    draft = read_json(Path(result["draft_path"]))
    policy = draft["body"]["feedback_policy"]

    assert policy["subdomain_semantics"] == "question_type_only"
    assert policy["asset_facets_schema"]["facets"] == [
        {"facet_id": "modality", "cardinality": "single", "required": True},
        {"facet_id": "target-structure", "cardinality": "multiple", "required": True},
    ]
    assert policy["medical_visibility_policy"] == {
        "solver_visible_segmentation": False,
        "hidden_reference_mask_allowed": "conditional",
        "label_identity_policy": "preserve",
        "semantic_scoring_required": True,
        "duplicate_coverage_key": "target-geometry",
    }
    assert policy["gt_policy"]["curated_source_label_provenance"] == {
        "source_snapshot_digest": digest({"fixture": "verbatim-medical-annotation"}),
        "annotation_id": "finding-label-row-14",
    }
    assert draft["body"]["provenance"]["feedback_policy_digest"] == digest(policy)


def test_legacy_gt_source_names_are_normalized_before_snapshot_digest(tmp_path: Path) -> None:
    paths, _ = _workspace_knowledge_draft(tmp_path)
    leaf = "biology.docking_pose_plausibility"
    feedback_path = tmp_path / "expert-feedback.yaml"
    content = _typed_feedback_content()
    content["gt_policy"] = {
        "gt_source_types": ["expert_visual_judgment", "viewer_native_measurement"],
        "expert_approval_required": True,
    }
    _write_feedback_source(
        feedback_path,
        source_id="legacy-gt-feedback",
        leaf_id=leaf,
        content=content,
    )
    sync_knowledge_sources(paths, [f"feedback:legacy-gt-feedback:{feedback_path}"])
    authorization = authorize_knowledge_compile(
        paths,
        leaf=leaf,
        expert_source_ids=["expert-test"],
        taxonomy_source_ids=["taxonomy-test"],
        feedback_source_ids=["legacy-gt-feedback"],
        owner="knowledge-owner",
        model="deterministic-local",
        max_tokens=0,
        max_cost_usd=0.0,
        ttl_seconds=3600,
    )
    result = compile_knowledge_draft(paths, authorization_path=authorization["authorization_path"])
    draft = read_json(Path(result["draft_path"]))
    assert draft["body"]["feedback_policy"]["gt_policy"]["gt_source_types"] == [
        "expert_annotation",
        "measurement_receipt",
    ]


@pytest.mark.parametrize(
    ("source_status", "content", "extra", "message"),
    [
        ("draft_reference", _typed_feedback_content(), None, "status is invalid"),
        ("accepted", {"claims": []}, None, "claims must be a non-empty list"),
        ("accepted", {"claims": [{}]}, None, "claim fields are invalid"),
        (
            "accepted",
            {
                "claims": [
                    {
                        "claim_id": "free-text",
                        "rule_id": "forbid_ui_or_operation_instruction",
                        "prompt": "Tell the author how to write the question.",
                    }
                ]
            },
            None,
            "claim fields are invalid",
        ),
        (
            "accepted",
            _typed_feedback_content(),
            {"instructions": "Use this prose as an author prompt."},
            "unknown field",
        ),
    ],
)
def test_expert_feedback_requires_accepted_structured_non_prose_claims(
    tmp_path: Path,
    source_status: str,
    content: dict[str, object],
    extra: dict[str, object] | None,
    message: str,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    feedback_path = tmp_path / "expert-feedback.yaml"
    _write_feedback_source(
        feedback_path,
        source_id="feedback",
        leaf_id="chemistry.stereochemistry_chirality",
        source_status=source_status,
        content=content,
        extra=extra,
    )

    with pytest.raises(InputError, match=message):
        sync_knowledge_sources(paths, [f"feedback:feedback:{feedback_path}"])

    feedback_path.write_text("unstructured review prose", encoding="utf-8")
    with pytest.raises(InputError, match="must be structured YAML"):
        sync_knowledge_sources(paths, [f"feedback:feedback:{feedback_path}"])


@pytest.mark.parametrize(
    ("policy_override", "message"),
    [
        (
            {
                "question_family_policy": {
                    "allowed_families": [
                        {
                            "family_id": "author-hint",
                            "family_kind": "property",
                            "instruction": "Ask exactly this question.",
                        }
                    ],
                    "quota": {
                        "scope": "portfolio",
                        "unit": "percentage_points",
                        "measurement_max": 0,
                        "non_measurement_min": 100,
                    },
                }
            },
            "question family fields are invalid",
        ),
        (
            {
                "gt_policy": {
                    "gt_source_types": ["model_only"],
                    "expert_approval_required": True,
                }
            },
            "gt_source_type is unsupported",
        ),
        (
            {
                "gt_policy": {
                    "gt_source_types": ["visual_annotation"],
                    "expert_approval_required": False,
                }
            },
            "must require expert approval",
        ),
    ],
)
def test_expert_feedback_question_and_gt_policy_are_fail_closed(
    tmp_path: Path,
    policy_override: dict[str, object],
    message: str,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    feedback_path = tmp_path / "expert-feedback.yaml"
    content = _typed_feedback_content()
    content.update(policy_override)
    _write_feedback_source(
        feedback_path,
        source_id="feedback",
        leaf_id="chemistry.stereochemistry_chirality",
        content=content,
    )

    with pytest.raises(InputError, match=message):
        sync_knowledge_sources(paths, [f"feedback:feedback:{feedback_path}"])


def test_knowledge_authorize_cli_binds_accepted_feedback_source(tmp_path: Path) -> None:
    paths, _ = _workspace_knowledge_draft(tmp_path)
    leaf = "biology.docking_pose_plausibility"
    feedback_path = tmp_path / "expert-feedback.yaml"
    _write_feedback_source(
        feedback_path,
        source_id="feedback-cli",
        leaf_id=leaf,
        content=_typed_feedback_content(),
    )
    sync_knowledge_sources(paths, [f"feedback:feedback-cli:{feedback_path}"])

    assert (
        main(
            [
                "knowledge",
                "authorize",
                "--workspace",
                str(paths.root),
                "--leaf",
                leaf,
                "--expert-source-id",
                "expert-test",
                "--taxonomy-source-id",
                "taxonomy-test",
                "--feedback-source-id",
                "feedback-cli",
                "--owner",
                "knowledge-owner",
            ]
        )
        == 0
    )
    authorizations = sorted((paths.receipts / "knowledge").glob("*.authorization.json"))
    newest = next(
        read_json(path)
        for path in authorizations
        if read_json(path)["body"].get("feedback_source_ids") == ["feedback-cli"]
    )
    assert newest["body"]["feedback_source_ids"] == ["feedback-cli"]
    assert newest["body"]["input_digests"]["feedback_sources"] == {
        "feedback-cli": digest_bytes(feedback_path.read_bytes())
    }
