"""Regression tests for the fail-closed scale-v2.1 dataset build."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import qa_gen_scale_v2_1 as build
from sci3d_bench.scorers.deterministic_answer import DeterministicAnswerScorer


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "QA-Gen/tasks/scale-v2.1"


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _candidate(domain: str) -> list[dict]:
    return _jsonl(OUTPUT / f"ScienceVision_scale-v2.1-{domain}_candidate_tasks.jsonl")


def _quarantine(domain: str) -> list[dict]:
    return _jsonl(
        OUTPUT
        / "quarantine"
        / f"ScienceVision_scale-v2.1-{domain}_quarantine_tasks.jsonl"
    )


def _by_id(tasks: list[dict]) -> dict[str, dict]:
    return {task["task_id"]: task for task in tasks}


def _mutate_answer_value(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1_000_000
    if isinstance(value, float):
        return value + 1_000_000.0
    if isinstance(value, str):
        return "__definitely_wrong__"
    if isinstance(value, list):
        result = copy.deepcopy(value)
        if result:
            result[0] = _mutate_answer_value(result[0])
        else:
            result.append("__definitely_wrong__")
        return result
    if isinstance(value, dict):
        result = copy.deepcopy(value)
        key = next(
            (name for name in ("atom_id", "atom_name", "chain_id", "label") if name in result),
            next(iter(result)),
        )
        result[key] = _mutate_answer_value(result[key])
        return result
    return "__definitely_wrong__"


def test_checked_in_build_counts_and_asset_closure() -> None:
    manifest = json.loads(
        (OUTPUT / "scale-v2.1-build.json").read_text(encoding="utf-8")
    )
    counts = manifest["counts"]
    assert counts["candidate"] == 823
    assert counts["candidate_by_domain"] == {
        "biology": 113,
        "chemistry": 105,
        "medical": 605,
    }
    assert counts["candidate_by_family"] == {
        "bio_collision_geometry": 97,
        "bio_multi_structure_compare": 16,
        "chem_binding_atom_distance": 23,
        "chem_conformation_explicit_dihedral": 82,
        "med_ct_geometry": 345,
        "med_mri_anatomy": 260,
    }
    assert counts["source_tasks"] == 1567
    assert counts["ledger"] == 1567
    assert counts["unique_assets"] == 1307
    assert counts["asset_bytes"] == 5_581_980_948


def test_manifest_hashes_and_source_lock_are_self_consistent() -> None:
    manifest = json.loads(
        (OUTPUT / "scale-v2.1-build.json").read_text(encoding="utf-8")
    )
    for item in manifest["outputs"]:
        path = OUTPUT / item["path"]
        assert path.stat().st_size == item["byte_size"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    source_lock = OUTPUT / "source-lock.json"
    assert (
        hashlib.sha256(source_lock.read_bytes()).hexdigest()
        == manifest["source_lock_sha256"]
    )


def test_all_candidate_ground_truth_self_scores_and_wrong_answers_lose_credit() -> None:
    scorer = DeterministicAnswerScorer()
    candidates = [
        task
        for domain in ("medical", "biology", "chemistry")
        for task in _candidate(domain)
    ]
    assert len(candidates) == 823
    for task in candidates:
        fields = task["answer_schema"]["fields"]
        ground_truth = task["ground_truth"]["value"]
        config = {**task["scorer"], **(task.get("scorer_config") or {})}
        task_spec = SimpleNamespace(
            answer_schema=SimpleNamespace(fields=fields),
            ground_truth=SimpleNamespace(value=ground_truth),
            scorer=SimpleNamespace(
                primary=task["scorer"]["primary"],
                config=config,
            ),
        )
        correct = scorer.score(
            task_spec, SimpleNamespace(final_answer=copy.deepcopy(ground_truth))
        ).value
        assert correct == 1.0, task["task_id"]

        wrong = copy.deepcopy(ground_truth)
        wrong[fields[0]] = _mutate_answer_value(wrong[fields[0]])
        incorrect = scorer.score(
            task_spec, SimpleNamespace(final_answer=wrong)
        ).value
        assert incorrect < correct, task["task_id"]


def test_ledger_is_a_total_one_to_one_source_partition() -> None:
    source_ids = set()
    source_dir = ROOT / "QA-Gen/tasks/scale-v1-taxonomy-input-20260805"
    ledger = _jsonl(OUTPUT / "task_ledger.jsonl")
    source_paths = [
        source_dir / f"ScienceVision_scale-v1-{domain}_final_tasks.jsonl"
        for domain in ("biology", "chemistry", "medical")
    ]
    if all(path.is_file() for path in source_paths):
        for path in source_paths:
            source_ids.update(task["task_id"] for task in _jsonl(path))
    else:
        # BabyVision mirrors the immutable outputs and cryptographic source
        # lock, not the 5.5 MB scale-v1 input payload.
        source_ids = {item["source_task_id"] for item in ledger}
        lock = json.loads((OUTPUT / "source-lock.json").read_text(encoding="utf-8"))
        locked_paths = {item["path"] for item in lock["inputs"]}
        assert {path.relative_to(ROOT).as_posix() for path in source_paths} <= locked_paths
    assert len(ledger) == len(source_ids) == 1567
    assert {item["source_task_id"] for item in ledger} == source_ids
    assert len({item["source_task_id"] for item in ledger}) == len(ledger)
    receipt_ids = {
        item["receipt_id"] for item in _jsonl(OUTPUT / "oracle_receipts.jsonl")
    }
    assert all(item["selection_receipt_id"] in receipt_ids for item in ledger)


def test_collision_altloc_occupancy_water_and_connection_helpers() -> None:
    def record(altloc: str, occupancy: float) -> dict:
        return {
            "atom_id": {
                "entity_id": "3",
                "chain_id": "A",
                "subchain_id": "C",
                "comp_id": "LIG",
                "seq_id": 1,
                "icode": "",
                "atom_id": "C1",
                "altloc": altloc,
                "element": "C",
            },
            "occupancy": occupancy,
            "xyz": (0.0, 0.0, 0.0),
            "vdw_A": 1.7,
        }

    selected = build._collapse_altloc(
        [record("B", 0.5), record("A", 0.5), record("", 0.5)]
    )
    assert len(selected) == 1
    assert selected[0]["atom_id"]["altloc"] == ""
    assert build._is_occupied_heavy_atom("C", 1.0)
    assert not build._is_occupied_heavy_atom("H", 1.0)
    assert not build._is_occupied_heavy_atom("C", 0.0)
    assert build._collision_role("NonPolymer", "LIG") == "nonpolymer"
    assert build._collision_role("Polymer", "ALA") == "polymer"
    assert build._collision_role("Water", "HOH") is None
    assert build._collision_role("NonPolymer", "WAT") is None
    left = ("A", "LIG", 1, "", "C1")
    right = ("A", "ALA", 2, "", "CA")
    connected = {(left, right)}
    assert build._is_directly_connected(left, right, connected)
    assert not build._is_directly_connected(right, left, connected)


def test_collision_contract_and_old_binary_gt_not_inherited() -> None:
    biology = _by_id(_candidate("biology") + _quarantine("biology"))
    task = biology["sv-biol-bio_collision_check-0000"]
    assert task["ground_truth"]["value"] == {
        "distance_A": 2.450711,
        "has_severe_clash": "yes",
        "nonpolymer_atom_id": task["ground_truth"]["value"]["nonpolymer_atom_id"],
        "polymer_atom_id": task["ground_truth"]["value"]["polymer_atom_id"],
        "vdw_overlap_A": 0.619289,
    }
    old_path = (
        ROOT
        / "QA-Gen/tasks/scale-v1-taxonomy-input-20260805/ScienceVision_scale-v1-biology_final_tasks.jsonl"
    )
    if old_path.is_file():
        old = _by_id(_jsonl(old_path))[task["task_id"]]
        assert old["ground_truth"]["value"] == {"has_severe_clash": "no"}
    else:
        assert len(task["provenance"]["scale_v2_1"]["source_task_sha256"]) == 1
    assert task["ground_truth"]["notes"].endswith("no scale-v1 binary GT was reused.")
    collision_all = [
        item
        for item in biology.values()
        if item["knowledge"]["direction_id"] == "bio_collision_geometry"
    ]
    assert len(collision_all) == 131
    assert sum(item["disposition"] == "candidate" for item in collision_all) == 97
    assert (
        sum(
            item["disposition_reason"] == "vdw_overlap_within_0.1A_of_threshold"
            for item in collision_all
        )
        == 15
    )
    assert (
        sum(
            item["disposition_reason"] == "missing_nonpolymer" for item in collision_all
        )
        == 19
    )


def test_ct_affine_geometry_and_deduplication_receipts() -> None:
    medical = _candidate("medical")
    ct_tasks = [
        task
        for task in medical
        if task["knowledge"]["direction_id"] == "med_ct_geometry"
    ]
    assert len(ct_tasks) == 345
    assert len({task["asset"]["mask_repo_path"] for task in ct_tasks}) == 193
    assert (
        len({(task["asset"]["mask_repo_path"], task["mask_id"]) for task in ct_tasks})
        == 345
    )
    ledger = _jsonl(OUTPUT / "task_ledger.jsonl")
    ct_ledger = [
        item
        for item in ledger
        if item["source_task_id"].startswith("sv-medi-med_ct_lesion-")
    ]
    assert len(ct_ledger) == 579
    assert len({item["output_task_id"] for item in ct_ledger}) == 345
    receipts = {
        item["receipt_id"]: item for item in _jsonl(OUTPUT / "oracle_receipts.jsonl")
    }
    for task in ct_tasks:
        receipt = receipts[task["oracle_receipt_id"]]
        selection = receipt["selection"]
        assert selection["orientation_axcodes"] == ["R", "A", "S"]
        assert selection["lr_array_axis"] == 0
        assert selection["si_array_axis"] == 2
        assert selection["ct_mask_max_affine_delta"] <= 0.001
        mask_id = task["mask_id"]
        assert task["asset"]["mask_id"] == mask_id
        assert task["target_selection"]["mask_id"] == mask_id
        assert task["render_contract"]["mask_id"] == mask_id
        assert task["render_contract"]["overlay_policy"] == "selected target label only"
        assert selection["target_mask_id"] == mask_id
        assert selection["foreground_label_values"] == [mask_id]
        assert mask_id in selection["observed_mask_label_values"]
        assert task["scorer"]["ground_truth_selection"] == {
            "foreground_policy": "mask voxel value equals mask_id",
            "mask_id": mask_id,
            "oracle_version": build.CT_ORACLE,
        }
        assert "Ignore every other nonzero label" in task["instruction"]
        assert task["ground_truth"]["value"]["laterality"] in {"left", "right"}
    sample = _by_id(ct_tasks)["sv-medi-med_ct_geometry-0000"]
    value = sample["ground_truth"]["value"]
    signed = value["signed_lr_distance_mm"]
    assert value["laterality"] == ("right" if signed > 0 else "left")

    source_lock = json.loads((OUTPUT / "source-lock.json").read_text())
    audit = source_lock["ct_target_mapping_audit"]
    assert audit["source_rows"] == 579
    assert audit["source_cases"] == 193
    assert audit["source_rows_per_case"] == 3
    assert audit["unique_target_labels"] == 345
    assert audit["target_group_size_distribution"] == {
        "1": 188,
        "2": 80,
        "3": 77,
    }
    assert audit["targets_per_case_distribution"] == {
        "1": 77,
        "2": 80,
        "3": 36,
    }
    assert audit["observed_nonzero_label_count_distribution"] == {
        "1": 37,
        "2": 30,
        "3": 40,
        "4": 26,
        "5": 20,
        "6": 8,
        "7": 7,
        "8": 8,
        "9": 3,
        "10": 6,
        "11": 3,
        "12": 3,
        "13": 1,
        "14": 1,
    }
    assert audit["selected_target_labels_absent"] == 0
    assert audit["registry_label_count_discrepancies"] == [
        {
            "mask_repo_path": "datasets/M3D-RefSeg/M3D_RefSeg/s0021/mask.nii.gz",
            "registry_mask_label_count": 4,
            "observed_mask_label_values": [2, 3, 4],
        }
    ]
    assert audit["old_laterality_exact_matches"] == 54
    assert audit["old_non_midline_targets"] == 184
    assert audit["old_non_midline_direction_matches"] == 54


def test_ct_geometry_selects_only_bound_target_label(tmp_path: Path) -> None:
    nib = pytest.importorskip("nibabel")
    np = pytest.importorskip("numpy")
    ct_path = tmp_path / "ct.nii.gz"
    mask_path = tmp_path / "mask.nii.gz"
    affine = np.eye(4)
    ct = np.zeros((5, 2, 2), dtype=np.int16)
    mask = np.zeros_like(ct, dtype=np.uint8)
    mask[0, 0, 0] = 1
    mask[4, 1, 1] = 2
    nib.save(nib.Nifti1Image(ct, affine), ct_path)
    nib.save(nib.Nifti1Image(mask, affine), mask_path)

    computed = build._ct_geometry(ct_path, mask_path, 2)

    assert computed["laterality"] == "right"
    assert computed["centroid_vox"] == [4.0, 1.0, 1.0]
    assert computed["bbox_min_vox"] == [4, 1, 1]
    assert computed["bbox_max_vox_inclusive"] == [4, 1, 1]
    assert computed["selection_receipt"]["target_mask_id"] == 2
    assert computed["selection_receipt"]["foreground_label_values"] == [2]
    assert computed["selection_receipt"]["observed_mask_label_values"] == [1, 2]

    with pytest.raises(build.BuildError, match="target CT mask label 3 is absent"):
        build._ct_geometry(ct_path, mask_path, 3)


def test_ct_mask_id_is_forwarded_to_harbor_overlay_plan() -> None:
    from scripts.harbor._multiview import _nifti_views

    task = next(
        item
        for item in _candidate("medical")
        if item["knowledge"]["direction_id"] == "med_ct_geometry"
    )
    flattened = {
        "asset_path": task["asset"]["path"],
        "asset_format": task["asset"]["format"],
        "mask_path": task["asset"]["mask_path"],
        "mask_id": task["mask_id"],
    }
    calls = _nifti_views(flattened, 256, None, 3)
    assert calls
    assert all(tool == "sv_render_nifti_overlay" for tool, _ in calls)
    assert all(args["mask_id"] == task["mask_id"] for _, args in calls)


def test_binding_tasks_have_complete_explicit_selection_and_no_angle() -> None:
    tasks = [
        task
        for task in _candidate("chemistry")
        if task["knowledge"]["direction_id"] == "chem_binding_atom_distance"
    ]
    assert len(tasks) == 23
    required_atom_keys = {
        "entity_id",
        "chain_id",
        "subchain_id",
        "comp_id",
        "seq_id",
        "icode",
        "atom_id",
        "altloc",
        "element",
    }
    for task in tasks:
        value = task["ground_truth"]["value"]
        assert set(value["nonpolymer_atom_id"]) == required_atom_keys
        assert set(value["polymer_atom_id"]) == required_atom_keys
        assert value["contact_distance_A"] > 0
        assert "angle" not in task["instruction"].lower()
        assert all("angle" not in field for field in task["answer_schema"]["fields"])


def test_dihedral_wrap_boundary_and_recomputed_bad_old_gt() -> None:
    assert build._circular_delta_degrees(179.0, -179.0) == pytest.approx(2.0)
    assert build._circular_delta_degrees(-179.0, 179.0) == pytest.approx(2.0)
    assert build._classify_dihedral(30.0) == ("eclipsed", 0.0)
    assert build._classify_dihedral(120.0) == ("anti", 0.0)
    assert build._classify_dihedral(75.0) == ("gauche", 45.0)
    chemistry = _by_id(_candidate("chemistry") + _quarantine("chemistry"))
    task = chemistry["sv-chem-chem_conformation-0117"]
    assert task["ground_truth"]["value"] == {
        "atom_ids": [7, 8, 9, 15],
        "conformer_class": "anti",
        "dihedral_deg": pytest.approx(-179.999382),
    }
    old_path = (
        ROOT
        / "QA-Gen/tasks/scale-v1-taxonomy-input-20260805/ScienceVision_scale-v1-chemistry_final_tasks.jsonl"
    )
    if old_path.is_file():
        old = _by_id(_jsonl(old_path))[task["task_id"]]
        assert old["ground_truth"]["value"] == "eclipsed"
    else:
        assert len(task["provenance"]["scale_v2_1"]["source_task_sha256"]) == 1
    conformation = [
        item
        for item in chemistry.values()
        if item["knowledge"]["direction_id"] == "chem_conformation_explicit_dihedral"
    ]
    assert len(conformation) == 120
    assert sum(item["disposition"] == "candidate" for item in conformation) == 82


def test_asset_hasher_detects_tampering(tmp_path: Path) -> None:
    asset = tmp_path / "assets/example.bin"
    asset.parent.mkdir()
    asset.write_bytes(b"first")
    hasher = build.AssetHasher(tmp_path / "assets")
    record = hasher.record("example.bin")
    assert record["sha256"] == hashlib.sha256(b"first").hexdigest()
    hasher.verify()
    asset.write_bytes(b"second")
    with pytest.raises(build.BuildError, match="asset changed after hashing"):
        hasher.verify()


def test_all_tasks_validate_and_only_use_repo_relative_asset_paths() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (
            ROOT
            / "QA-Gen/ScienceVisionTaskGenerator/schemas/sciencevision_task.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema)
    tasks = []
    for domain in ("biology", "chemistry", "medical"):
        tasks.extend(_candidate(domain))
        tasks.extend(_quarantine(domain))
    assert len(tasks) == 1333
    for task in tasks:
        assert not list(validator.iter_errors(task)), task["task_id"]
        for asset in task["assets"]:
            assert not asset["path"].startswith("/")
            assert "sha256" in asset
            if "mask_path" in asset:
                assert not asset["mask_path"].startswith("/")
                assert "mask_sha256" in asset
