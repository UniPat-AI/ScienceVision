"""Golden tests for the offline, minimal formal scorer release bundle."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_ROOT = REPO_ROOT / "scorer_bundle"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def staged_bundle(tmp_path: Path) -> Path:
    sys.path.insert(0, str(BUNDLE_ROOT))
    try:
        build_context = _load_module("_scorer_build_context", BUNDLE_ROOT / "build_context.py")
        context = tmp_path / "context"
        build_context.stage(REPO_ROOT, context)
        return context
    finally:
        sys.path.remove(str(BUNDLE_ROOT))


def _structured_task(*, field: str, expected, primary="deterministic_structured_v2_1", **extra):
    task = {
        "task_id": "golden-task",
        "v_class": "V1",
        "asset_path": "asset.obj",
        "asset_format": "obj",
        "evaluation_eligible": True,
        "answer_schema": {"kind": "structured", "fields": [field]},
        "ground_truth": {"value": {field: expected}},
        "scorer": {
            "primary": primary,
            "rubric_version": "sciencevision.deterministic_structured.v2.1",
        },
    }
    task.update(extra)
    return task


def _run_cli(staged_bundle: Path, tmp_path: Path, task: dict, answer) -> tuple[int, dict]:
    task_path = tmp_path / "task.json"
    answer_path = tmp_path / "answer.json"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    answer_path.write_text(json.dumps({"answer": answer}), encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(staged_bundle / "src"))
    completed = subprocess.run(
        [
            sys.executable,
            str(staged_bundle / "scorer_cli.py"),
            "--task",
            str(task_path),
            "--answer",
            str(answer_path),
            "--manifest",
            str(staged_bundle / "scorer_manifest.json"),
        ],
        check=False,
        text=True,
        capture_output=True,
        env=environment,
        cwd=tmp_path,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_golden_integer_equal_preserves_integer_type(staged_bundle: Path, tmp_path: Path):
    task = _structured_task(field="count", expected=3)
    code, result = _run_cli(staged_bundle, tmp_path, task, {"count": 3})
    assert code == 0
    assert result["status"] == "scored"
    assert result["score"]["value"] == 1.0
    assert result["audit"]["raw"]["verdicts"][0]["comparator"] == "integer_equal"

    code, result = _run_cli(staged_bundle, tmp_path, task, {"count": 3.0})
    assert code == 0
    assert result["score"]["value"] == 0.0
    assert result["audit"]["raw"]["verdicts"][0]["reason"] == "comparison_failed"


def test_golden_vector_abs_tolerance_is_positional(staged_bundle: Path, tmp_path: Path):
    task = _structured_task(
        field="point",
        expected=[1.0, 2.0, 3.0],
        scorer={
            "primary": "deterministic_structured_v2_1",
            "rubric_version": "sciencevision.deterministic_structured.v2.1",
            "tolerance": {"point": 0.1},
        },
    )
    code, result = _run_cli(staged_bundle, tmp_path, task, {"point": [1.05, 1.95, 3.05]})
    assert code == 0
    assert result["score"]["value"] == 1.0
    assert result["audit"]["raw"]["verdicts"][0]["comparator"] == "vector_abs_tolerance"

    code, result = _run_cli(staged_bundle, tmp_path, task, {"point": [3.0, 2.0, 1.0]})
    assert code == 0
    assert result["score"]["value"] == 0.0


def _packed_member_set(shape: tuple[int, int, int], voxels) -> str:
    """The wire format the builder writes and the scorer decodes.

    C-order bit-packed occupancy, least significant bit first within each byte,
    then base64(zlib(...)). Hand-packed here with the standard library, so the
    golden is independent of the builder-side packer.
    """
    d0, d1, d2 = shape
    packed = bytearray((d0 * d1 * d2 + 7) // 8)
    for x, y, z in voxels:
        index = (x * d1 + y) * d2 + z
        packed[index >> 3] |= 1 << (index & 7)
    return base64.b64encode(zlib.compress(bytes(packed))).decode("ascii")


def test_membership_golden(staged_bundle: Path, tmp_path: Path):
    """A voxel answer judged by membership in a packed ground-truth region."""
    item = {
        "id": "point_vox",
        "pred_path": "point_vox",
        "gt_path": "point_vox",
        "comparator": "voxel_member_of_set",
        "member_shape": [4, 4, 4],
        "member_set": _packed_member_set((4, 4, 4), [(0, 0, 0), (1, 2, 3)]),
        "weight": 1.0,
        "required": True,
    }
    task = _structured_task(
        field="point_vox",
        expected=[0, 0, 0],
        scorer={
            "primary": "deterministic_structured_v2_1",
            "rubric_version": "sciencevision.deterministic_structured.v2.1",
            "rubric": [item],
        },
    )

    code, result = _run_cli(staged_bundle, tmp_path, task, {"point_vox": [1, 2, 3]})
    assert code == 0
    assert result["status"] == "scored"
    assert result["score"]["value"] == 1.0
    assert result["audit"]["raw"]["verdicts"][0]["comparator"] == "voxel_member_of_set"

    code, result = _run_cli(staged_bundle, tmp_path, task, {"point_vox": [3, 3, 3]})
    assert code == 0
    assert result["score"]["value"] == 0.0
    assert result["audit"]["raw"]["verdicts"][0]["reason"] == "comparison_failed"


def test_extent_voxel_contract_golden(staged_bundle: Path, tmp_path: Path):
    task = {
        "task_id": "med-extent",
        "v_class": "V6",
        "asset_path": "volume.nii.gz",
        "asset_format": "nii.gz",
        "evaluation_eligible": True,
        "answer_schema": {
            "kind": "structured",
            "fields": ["extent_vox"],
            "field_specs": {
                "extent_vox": {
                    "type": "integer_triplet",
                    "unit": "voxel",
                    "length": 3,
                    "axis_order": ["axis-0", "axis-1", "axis-2"],
                }
            },
        },
        "ground_truth": {
            "value": {"extent_vox": [20, 37, 25]},
            "axis_order": ["axis-0", "axis-1", "axis-2"],
        },
        "terminal_answer": {
            "format": "json_object",
            "axis_order": ["axis-0", "axis-1", "axis-2"],
        },
        "scorer": {
            "primary": "extent_voxel_tolerance_v1",
            "checks": ["extent_vox_axis_order", "extent_vox_per_axis_tolerance"],
            "aggregation": "all_required_with_partial_credit",
            "tolerance": {"extent_vox": 3},
            "expected_fields": ["extent_vox"],
            "axis_order": ["axis-0", "axis-1", "axis-2"],
        },
    }
    code, result = _run_cli(staged_bundle, tmp_path, task, {"extent_vox": [17, 40, 28]})
    assert code == 0
    assert result["score"]["metric"] == "extent_voxel_tolerance_v1"
    assert result["score"]["value"] == 1.0


def test_missing_explicit_eligibility_is_blocked(staged_bundle: Path, tmp_path: Path):
    task = _structured_task(field="count", expected=3)
    task.pop("evaluation_eligible")
    code, result = _run_cli(staged_bundle, tmp_path, task, {"count": 3})
    assert code == 0
    assert result["status"] == "blocked"
    assert result["score"] is None
    assert result["audit"]["reason_codes"] == ["missing_explicit_evaluation_eligible"]


def test_staged_context_excludes_agent_and_mcp_runtime(staged_bundle: Path):
    files = {path.relative_to(staged_bundle).as_posix() for path in staged_bundle.rglob("*") if path.is_file()}
    assert "src/sci3d_agent/__init__.py" not in files
    assert not any("/mcp/" in name or name.startswith("mcp") for name in files)
    joined = "\n".join(path.read_text(encoding="utf-8") for path in staged_bundle.rglob("*.py"))
    assert "sci3d_agent" not in joined
    assert "fastmcp" not in joined.casefold()
    assert "USER 10001:10001" in (staged_bundle / "Dockerfile").read_text(encoding="utf-8")


def test_manifest_is_self_verifying_and_exposes_registry(staged_bundle: Path):
    manifest = json.loads((staged_bundle / "scorer_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "sciencevision.scorer_bundle_manifest.v1"
    assert manifest["schema_sha256"]
    assert manifest["comparator_registry_sha256"]
    assert {"integer_equal", "vector_abs_tolerance", "voxel_member_of_set"} <= set(
        manifest["comparator_registry"]
    )
    assert manifest["runtime_files"]["scorer_cli.py"]


def test_coverage_audit_blocks_prose_and_unknown_source(tmp_path: Path):
    coverage = _load_module("_scorer_coverage", BUNDLE_ROOT / "audit_v9_coverage.py")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            {
                "bundle": {"path_name": "ScienceVision-STEM-250-review-v7", "rows": 4},
                "rows": [
                    {
                        "task_id": "ready", "domain": "architecture",
                        "answer_form": "structured_matching_schema",
                        "evaluation_ready": True, "evaluation_eligible": True,
                        "scorer": {"primary": "deterministic_structured_v2_1"},
                    },
                    {
                        "task_id": "structured", "domain": "medical",
                        "answer_form": "structured", "evaluation_ready": False,
                    },
                    {
                        "task_id": "prose", "domain": "biology",
                        "answer_form": "structured_matching_schema",
                        "free_text_fields": ["rationale"],
                    },
                    {"task_id": "broken", "domain": "chemistry"},
                ],
            }
        ),
        encoding="utf-8",
    )
    rows, metadata, source_hash = coverage.load_index(index)
    report = coverage.build_report(
        rows,
        source_path=index,
        source_sha256=source_hash,
        source_metadata=metadata,
        source_version=coverage._source_version(metadata, None),
        expected_total=4,
    )
    assert report["source"]["coverage_status"] == "blocked_non_v9_or_unknown_source"
    assert report["summary"]["coverage_categories"] == {
        "evaluation_ready": 1,
        "malformed_blocked": 1,
        "prose_blocked": 1,
        "structured_blocked": 1,
    }
    assert report["summary"]["formal_contract_eligible"] == 1
