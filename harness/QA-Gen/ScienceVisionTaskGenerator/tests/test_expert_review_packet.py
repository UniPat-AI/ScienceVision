"""Tests for the expert review packet generator (Task 7).

Covers:
  1. build_packet unit tests:
     - Top-level keys match spec.
     - expert_checks has 6 entries, correct ordering, decision=None, comment="".
     - viewer_url correctly built via urllib.parse.quote_plus.
     - Missing/empty viewer → viewer_url absent, viewer_url_note present.
     - required_reviewer_roles + min_approvals from expert_review_requirements.
  2. write_packets integration smoke test:
     - 2 tasks written to <run>/expert_review_packet/<task_id>.json.
     - Content is valid JSON with required keys.
     - Returns count 2.
     - Missing run_dir raises FileNotFoundError.
  3. Hardening tests:
     - build_packet does not mutate input (immutability).
     - duplicate task_id raises ValueError.
     - malformed jsonl raises ValueError with line number.
     - unsafe task_id (path traversal) raises ValueError.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "create_expert_review_packet.py"
)

# Add scripts dir to path so we can import the module
sys.path.insert(0, str(_SCRIPT.parent))

from create_expert_review_packet import build_packet, write_packets  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPERT_CHECK_KEYS_ORDERED = [
    "question_scientifically_valid",
    "asset_matches_question",
    "terminal_answer_correct",
    "gold_evidence_sufficient",
    "scorer_tolerance_reasonable",
    "terminology_precise",
]

_TOP_LEVEL_KEYS = [
    "task_id",
    "title",
    "domain",
    "v_class",
    "difficulty",
    "instruction",
    "asset",
    "terminal_answer",
    "scorer",
    "validation_record",
    "expert_checks",
    "required_reviewer_roles",
    "min_approvals",
    "decision",
    "notes",
]


def _make_task(
    *,
    task_id: str = "task-001",
    viewer: str = "molstar",
    asset_path: str = "/crawl_data/biology/x.cif",
    roles: list[str] | None = None,
    min_approvals: int | None = None,
) -> dict[str, Any]:
    """Build a minimal synthetic task dict for testing."""
    task: dict[str, Any] = {
        "task_id": task_id,
        "title": "Test Task Title",
        "domain": "biology",
        "v_class": "V2",
        "difficulty": "medium",
        "instruction": "Identify the secondary structure elements visible in this protein.",
        "asset": {
            "name": "x.cif",
            "path": asset_path,
            "repo_path": "crawl_data/biology/x.cif",
            "viewer": viewer,
            "format": "cif",
        },
        "terminal_answer": {
            "kind": "classification",
            "fields": ["structure_type"],
        },
        "scorer": {
            "type": "exact_match",
            "tolerance": None,
        },
        "validation_record": {
            "auto_checks": [{"key": "has_instruction", "status": "pass"}],
            "release_gate": "blocked",
        },
    }
    expert_req: dict[str, Any] = {}
    if roles is not None:
        expert_req["roles"] = roles
    if min_approvals is not None:
        expert_req["min_approvals"] = min_approvals
    if expert_req:
        task["expert_review_requirements"] = expert_req
    return task


# ---------------------------------------------------------------------------
# Unit tests: build_packet
# ---------------------------------------------------------------------------


class TestBuildPacketTopLevelKeys:
    def test_all_required_keys_present(self) -> None:
        task = _make_task(roles=["domain_expert", "scorer_expert"])
        packet = build_packet(task)
        for key in _TOP_LEVEL_KEYS:
            assert key in packet, f"Missing key: {key}"

    def test_no_unexpected_extra_keys(self) -> None:
        """Packet must not contain keys beyond the spec list (except viewer_url/note inside asset)."""
        task = _make_task()
        packet = build_packet(task)
        allowed = set(_TOP_LEVEL_KEYS)
        for key in packet:
            assert key in allowed, f"Unexpected top-level key: {key}"

    def test_passthrough_scalars(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["task_id"] == "task-001"
        assert packet["domain"] == "biology"
        assert packet["v_class"] == "V2"
        assert packet["difficulty"] == "medium"
        assert packet["decision"] == "pending"
        assert packet["notes"] == ""

    def test_instruction_copied(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["instruction"] == task["instruction"]

    def test_title_copied(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["title"] == task["title"]

    def test_terminal_answer_copied_verbatim(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["terminal_answer"] == task["terminal_answer"]

    def test_scorer_copied_verbatim(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["scorer"] == task["scorer"]

    def test_validation_record_copied_verbatim(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["validation_record"] == task["validation_record"]


class TestBuildPacketExpertChecks:
    def test_expert_checks_length_is_6(self) -> None:
        packet = build_packet(_make_task())
        assert len(packet["expert_checks"]) == 6

    def test_expert_checks_key_order(self) -> None:
        packet = build_packet(_make_task())
        actual_keys = [c["key"] for c in packet["expert_checks"]]
        assert actual_keys == _EXPERT_CHECK_KEYS_ORDERED

    def test_all_checks_decision_is_none(self) -> None:
        packet = build_packet(_make_task())
        for check in packet["expert_checks"]:
            assert check["decision"] is None, f"Expected decision=None for {check['key']}"

    def test_all_checks_comment_is_empty_string(self) -> None:
        packet = build_packet(_make_task())
        for check in packet["expert_checks"]:
            assert check["comment"] == "", f"Expected comment='' for {check['key']}"

    def test_each_check_has_question_field(self) -> None:
        packet = build_packet(_make_task())
        for check in packet["expert_checks"]:
            assert "question" in check
            assert isinstance(check["question"], str)
            assert len(check["question"]) > 0


class TestBuildPacketViewerUrl:
    def test_viewer_url_built_correctly_with_molstar(self) -> None:
        task = _make_task(viewer="molstar", asset_path="/crawl_data/biology/x.cif")
        packet = build_packet(task)
        asset = packet["asset"]
        assert "viewer_url" in asset
        assert asset["viewer_url"] == "/viewer/molstar.html?asset=%2Fcrawl_data%2Fbiology%2Fx.cif"

    def test_viewer_url_quote_plus_encodes_slashes(self) -> None:
        """quote_plus converts / to %2F and space to +."""
        task = _make_task(viewer="niivue", asset_path="/crawl_data/neuro/scan file.nii.gz")
        packet = build_packet(task)
        asset = packet["asset"]
        assert "viewer_url" in asset
        assert "%2F" in asset["viewer_url"]
        assert "+" in asset["viewer_url"]  # space encoded as +

    def test_viewer_url_absent_when_viewer_empty_string(self) -> None:
        task = _make_task(viewer="")
        packet = build_packet(task)
        asset = packet["asset"]
        assert "viewer_url" not in asset
        assert "viewer_url_note" in asset
        assert "not specified" in asset["viewer_url_note"].lower()

    def test_viewer_url_absent_when_viewer_missing(self) -> None:
        task = _make_task()
        del task["asset"]["viewer"]
        packet = build_packet(task)
        asset = packet["asset"]
        assert "viewer_url" not in asset
        assert "viewer_url_note" in asset

    def test_viewer_url_absent_when_viewer_null(self) -> None:
        """viewer=None (JSON null from upstream) must not generate a viewer_url."""
        task = _make_task()
        task["asset"]["viewer"] = None  # type: ignore[assignment]
        packet = build_packet(task)
        asset = packet["asset"]
        assert "viewer_url" not in asset
        assert "viewer_url_note" in asset

    def test_asset_preserves_other_fields(self) -> None:
        task = _make_task(viewer="gltf", asset_path="/crawl_data/mech/part.glb")
        packet = build_packet(task)
        asset = packet["asset"]
        # name comes from the fixture ("x.cif") — just check it round-trips
        assert asset["name"] == task["asset"]["name"]
        assert asset["format"] == task["asset"]["format"]
        assert asset["path"] == "/crawl_data/mech/part.glb"

    def test_asset_exists_on_disk_false_when_nonexistent_path(self) -> None:
        task = _make_task(asset_path="/nonexistent/path/does_not_exist.cif")
        packet = build_packet(task)
        assert packet["asset"]["exists_on_disk"] is False


class TestBuildPacketReviewerRoles:
    def test_roles_from_expert_review_requirements(self) -> None:
        task = _make_task(roles=["domain_expert", "scorer_expert"])
        packet = build_packet(task)
        assert packet["required_reviewer_roles"] == ["domain_expert", "scorer_expert"]

    def test_min_approvals_from_requirements(self) -> None:
        task = _make_task(roles=["domain_expert"], min_approvals=2)
        packet = build_packet(task)
        assert packet["min_approvals"] == 2

    def test_default_roles_when_absent(self) -> None:
        task = _make_task()  # no expert_review_requirements
        packet = build_packet(task)
        assert packet["required_reviewer_roles"] == ["domain_expert"]

    def test_default_min_approvals_when_absent(self) -> None:
        task = _make_task()
        packet = build_packet(task)
        assert packet["min_approvals"] == 1

    def test_default_min_approvals_when_key_missing_from_req(self) -> None:
        task = _make_task()
        task["expert_review_requirements"] = {"roles": ["domain_expert"]}  # no min_approvals
        packet = build_packet(task)
        assert packet["min_approvals"] == 1


# ---------------------------------------------------------------------------
# Integration smoke tests: write_packets
# ---------------------------------------------------------------------------


class TestWritePackets:
    def _two_tasks(self) -> list[dict[str, Any]]:
        return [
            _make_task(task_id="task-alpha", viewer="molstar"),
            _make_task(
                task_id="task-beta",
                viewer="niivue",
                asset_path="/crawl_data/neuro/scan.nii.gz",
                roles=["domain_expert", "scorer_expert"],
            ),
        ]

    def _write_jsonl(self, tasks: list[dict[str, Any]], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for task in tasks:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")

    def test_returns_correct_count(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        tasks = self._two_tasks()
        self._write_jsonl(tasks, run_dir / "final_tasks.jsonl")
        count = write_packets(run_dir)
        assert count == 2

    def test_files_materialise_at_default_out_dir(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        tasks = self._two_tasks()
        self._write_jsonl(tasks, run_dir / "final_tasks.jsonl")
        write_packets(run_dir)
        out_dir = run_dir / "expert_review_packet"
        assert (out_dir / "task-alpha.json").exists()
        assert (out_dir / "task-beta.json").exists()

    def test_custom_out_dir(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        out_dir = tmp_path / "my_packets"
        tasks = self._two_tasks()
        self._write_jsonl(tasks, run_dir / "final_tasks.jsonl")
        write_packets(run_dir, out_dir)
        assert (out_dir / "task-alpha.json").exists()
        assert (out_dir / "task-beta.json").exists()

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        tasks = self._two_tasks()
        self._write_jsonl(tasks, run_dir / "final_tasks.jsonl")
        write_packets(run_dir)
        out_dir = run_dir / "expert_review_packet"
        for task in tasks:
            packet_path = out_dir / f"{task['task_id']}.json"
            data = json.loads(packet_path.read_text(encoding="utf-8"))
            assert isinstance(data, dict)

    def test_output_has_required_keys(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        tasks = self._two_tasks()
        self._write_jsonl(tasks, run_dir / "final_tasks.jsonl")
        write_packets(run_dir)
        out_dir = run_dir / "expert_review_packet"
        data = json.loads((out_dir / "task-alpha.json").read_text(encoding="utf-8"))
        for key in _TOP_LEVEL_KEYS:
            assert key in data, f"Missing key '{key}' in output packet"

    def test_missing_run_dir_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist"
        with pytest.raises(FileNotFoundError):
            write_packets(missing)

    def test_missing_final_tasks_jsonl_raises(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        # No final_tasks.jsonl written
        with pytest.raises(FileNotFoundError):
            write_packets(run_dir)


# ---------------------------------------------------------------------------
# Hardening tests
# ---------------------------------------------------------------------------


class TestBuildPacketImmutability:
    def test_build_packet_does_not_mutate_input(self) -> None:
        task = _make_task(roles=["domain_expert", "scorer_expert"])
        task_snapshot = copy.deepcopy(task)

        packet = build_packet(task)

        # Identity checks: packet sub-objects must not be the same objects as task's
        assert packet["required_reviewer_roles"] is not task["expert_review_requirements"]["roles"]
        assert packet["terminal_answer"] is not task["terminal_answer"]
        assert packet["scorer"] is not task["scorer"]
        assert packet["validation_record"] is not task["validation_record"]

        # Mutation of packet must not affect original
        packet["required_reviewer_roles"].append("intruder")
        packet["terminal_answer"]["injected"] = True  # type: ignore[index]

        assert task == task_snapshot, "Original task was mutated after modifying the packet"


class TestWritePacketsDuplicate:
    def test_duplicate_task_id_raises(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_dup"
        run_dir.mkdir()
        task_a = _make_task(task_id="dup")
        task_b = _make_task(task_id="dup")
        jsonl_path = run_dir / "final_tasks.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(task_a) + "\n")
            f.write(json.dumps(task_b) + "\n")
        with pytest.raises(ValueError, match="duplicate task_id"):
            write_packets(run_dir)


class TestWritePacketsMalformedJsonl:
    def test_malformed_jsonl_raises_with_line_number(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_bad"
        run_dir.mkdir()
        task = _make_task(task_id="good-task")
        jsonl_path = run_dir / "final_tasks.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(task) + "\n")
            f.write("not_json\n")  # line 2 is invalid
        with pytest.raises(ValueError, match="line 2"):
            write_packets(run_dir)


class TestWritePacketsPathTraversal:
    def test_unsafe_task_id_raises(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_traversal"
        run_dir.mkdir()
        task = _make_task(task_id="../escape")
        jsonl_path = run_dir / "final_tasks.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(task) + "\n")
        with pytest.raises(ValueError, match="unsafe task_id"):
            write_packets(run_dir)

    def test_dotdot_task_id_raises(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run_traversal2"
        run_dir.mkdir()
        task = _make_task(task_id="..")
        jsonl_path = run_dir / "final_tasks.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(task) + "\n")
        with pytest.raises(ValueError, match="unsafe task_id"):
            write_packets(run_dir)
