from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PACKAGE_ROOT / "src"
if SRC_ROOT.is_dir() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES = [
    "network",
    "asset_file_read",
    "raw_asset_metadata",
    "raw_asset_read",
    "arbitrary_compute",
    "unrestricted_compute",
]


def profile_path(name: str) -> Path:
    return PACKAGE_ROOT / "configs" / "profiles" / f"{name}.yaml"


def write_controlled_viewer_evidence(
    path: Path,
    *,
    definition_digest: str,
    allowed_operations: list[str] | None = None,
) -> None:
    write_json(
        path,
        {
            "viewer_definition_digest": definition_digest,
            "allowed_operations": allowed_operations or ["inspect", "isolate", "rotate", "render"],
            "forbidden_capabilities": CONTROLLED_VIEWER_FORBIDDEN_CAPABILITIES,
            "network_access": False,
            "asset_file_read": False,
        },
    )


@pytest.fixture()
def ev2_source(tmp_path: Path) -> Path:
    """A minimal EV2-shaped source with an intentionally conflicting version label."""
    root = tmp_path / "ev2-pilot"
    write_json(
        root / "MANIFEST.json",
        {"package_version": "v1.0.2", "dataset": "ev2-pilot"},
    )
    (root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("EV2 pilot package v1.0.3\n", encoding="utf-8")
    write_jsonl(
        root / "tasks" / "tasks.jsonl",
        [
            {
                "case_id": "bio-verified-1",
                "domain": "biology",
                "subdomain": "docking_pose_plausibility",
                "question": "RAW QUESTION: identify the hidden ligand contact.",
                "reference_answer": "RAW ANSWER: Tyr42",
                "asset_id": "real-private-asset-id-001",
                "asset_path": "/private/assets/ligand_001.pdb",
                "review_status": "verified",
            },
            {
                "case_id": "bio-minor-fix-1",
                "domain": "biology",
                "subdomain": "docking_pose_plausibility",
                "question": "RAW QUESTION: an incomplete case.",
                "reference_answer": "RAW ANSWER: incorrect draft",
                "asset_id": "real-private-asset-id-002",
                "asset_path": "/private/assets/ligand_002.pdb",
                "review_status": "minor_fix",
            },
            {
                "case_id": "chem-factual-error-1",
                "domain": "chemistry",
                "subdomain": "stereochemistry_chirality",
                "question": "RAW QUESTION: a known bad prompt.",
                "reference_answer": "RAW ANSWER: wrong stereocenter",
                "asset_id": "real-private-asset-id-003",
                "asset_path": "/private/assets/chiral_003.sdf",
                "review_status": "factual_error",
            },
        ],
    )
    write_json(root / "provenance" / "source.json", {"source": "test-fixture"})
    return root


@pytest.fixture()
def knowledge_request() -> dict[str, Any]:
    return {
        "id": "biology.docking_pose_plausibility",
        "expert_source_refs": [
            {"source_id": "expert-biology", "digest": "a" * 64, "section_id": "dock-1"}
        ],
        "visual_taxonomy_ref": {"digest": "b" * 64, "item_ids": ["spatial-contact"]},
        "seed_lesson_ref": {"digest": "c" * 64},
        "schema_digest": "d" * 64,
        "compiler_digest": "e" * 64,
    }


@pytest.fixture()
def knowledge_draft_payload() -> dict[str, Any]:
    return {
        "scientific_scope": {"included": ["pose contact"], "excluded": ["binding affinity"]},
        "visual_target_types": ["local-contact"],
        "scenario_cards": [
            {
                "id": "contact-review",
                "observable_targets": ["ligand-receptor contact"],
                "valid_question_patterns": ["which local interaction is visible"],
                "invalid_question_patterns": ["predict affinity from metadata"],
            }
        ],
        "allowed_inference": ["visual contact plausibility"],
        "forbidden_shortcuts": ["asset_id", "network", "raw_file"],
        "evidence_requirements": {
            "required_viewer_operations": ["orbit"],
            "required_evidence": ["viewer_trace"],
            "allowed_truth_modes": ["expert_visual_judgment"],
        },
        "requirements": {
            "asset_requirements": ["viewer-ready ligand complex"],
            "viewer_requirements": ["orbit"],
            "answer_modes": ["short_text"],
        },
        "common_failure_modes": ["text-only answer"],
        "rubric_extensions": [],
    }
