from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import stat
from typing import Any

import pytest

from qagen.contracts import digest, make_envelope
from qagen.measurement import (
    MeasurementContractError,
    approve_measurement_verifier,
    build_scene_inventory,
    execute_measurement_request,
    load_measurement_verifier_approval,
    make_measurement_request,
    materialize_v32_scene_inventories,
    selector_backend,
    validate_measurement_request,
    validate_target_spec,
)
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json, read_json
import qagen.measurement.inventory as inventory_module


def _digest(label: str) -> str:
    return digest({"test": label})


def _molecular_catalog() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    catalog = {
        "components": [
            {"component_id": "component-0001", "chain_id": "chain-0001"},
            {"component_id": "component-0002", "chain_id": "chain-0002"},
        ],
        "residues": [
            {
                "residue_id": "residue-000001",
                "component_id": "component-0001",
                "residue_name": "residue-000001",
                "residue_number": 1,
                "insertion_code": "",
            },
            {
                "residue_id": "residue-000002",
                "component_id": "component-0002",
                "residue_name": "residue-000002",
                "residue_number": 2,
                "insertion_code": "",
            },
        ],
        "atoms": [
            {
                "atom_id": f"atom-{index:08d}",
                "residue_id": "residue-000001" if index < 3 else "residue-000002",
                "atom_name": f"atom-{index:08d}",
                "element": element,
                "coordinate_identity": f"coordinate-{index:08d}",
            }
            for index, (_, element) in enumerate((("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O")), start=1)
        ],
        "mask_labels": [],
        "axes": [],
        "volume_shape": [],
        "layers": [],
    }
    backend = {
        "component-0001": {"model_index": 1, "chain_id": "A"},
        "component-0002": {"model_index": 1, "chain_id": "B"},
        "residue-000001": {"model_index": 1, "chain_id": "A", "residue_number": 1, "insertion_code": "", "residue_name": "GLY"},
        "residue-000002": {"model_index": 1, "chain_id": "B", "residue_number": 2, "insertion_code": "", "residue_name": "LIG"},
    }
    backend.update(
        {
            atom["atom_id"]: {
                "model_index": 1,
                "chain_id": "A" if atom["residue_id"] == "residue-000001" else "B",
                "residue_number": 1 if atom["residue_id"] == "residue-000001" else 2,
                "insertion_code": "",
                "atom_name": (("N", "CA", "C", "O")[int(atom["atom_id"].rsplit("-", 1)[1]) - 1]),
                "altloc": "",
            }
            for atom in catalog["atoms"]
        }
    )
    return catalog, backend


def _inventory_body(target_leaf: str) -> dict[str, Any]:
    if target_leaf == "medical.volumetric_target_geometry":
        catalog = {
            "components": [],
            "residues": [],
            "atoms": [],
            "mask_labels": [2, 7],
            "axes": ["x", "y", "z"],
            "volume_shape": [12, 13, 14],
            "layers": ["layer-0001", "layer-0002"],
        }
        backend: dict[str, dict[str, Any]] = {}
        selector_types = ["mask_label_axis"]
        asset_sha256 = {"primary": _digest(f"primary:{target_leaf}"), "mask": _digest(f"mask:{target_leaf}")}
    else:
        catalog, backend = _molecular_catalog()
        selector_types = {
            "biology.docking_pose_plausibility": ["component_pair", "residue", "residue_pair", "atom"],
            "biology.molecular_collision_check": ["atom_pair", "residue_pair"],
            "chemistry.conformation_analysis": ["atom_pair", "atom_quadruplet"],
            "chemistry.steric_accessibility": ["atom", "residue", "atom_pair"],
        }[target_leaf]
        asset_sha256 = {"primary": _digest(f"primary:{target_leaf}")}
    return {
        "inventory_version": 1,
        "source_record_digest": _digest(f"source:{target_leaf}"),
        "target_leaf": target_leaf,
        "scene_registry_digest": _digest(f"registry:{target_leaf}"),
        "anonymous_scene_ref": f"scene://measurement-{target_leaf.replace('.', '-')}",
        "asset_set_digest": _digest(f"assets:{target_leaf}"),
        "asset_sha256": asset_sha256,
        "selector_types": selector_types,
        "selector_catalog": catalog,
        "backend_selector_map": backend,
        "local_only": True,
        "release_eligible": False,
    }


def _target_spec(target_leaf: str, *, selector: dict[str, Any], operation: str, answer_fields: list[str] | None = None) -> dict[str, Any]:
    inventory = _inventory_body(target_leaf)
    return {
        "target_spec_version": 1,
        "source_record_digest": inventory["source_record_digest"],
        "scene_inventory_digest": _digest(f"inventory:{target_leaf}"),
        "anonymous_scene_ref": inventory["anonymous_scene_ref"],
        "candidate_plan_digest": _digest(f"plan:{target_leaf}"),
        "target_leaf": target_leaf,
        "allowed_native_operations": [operation],
        "native_operation": operation,
        "selector": selector,
        "answer_fields": answer_fields or ["answer"],
    }


@pytest.mark.parametrize(
    ("target_leaf", "operation", "selector"),
    [
        ("biology.docking_pose_plausibility", "inspect", {"type": "residue_pair", "residue_ids": ["residue-000001", "residue-000002"]}),
        ("biology.molecular_collision_check", "inspect", {"type": "atom_pair", "atom_ids": ["atom-00000001", "atom-00000002"]}),
        ("chemistry.conformation_analysis", "measure_dihedral", {"type": "atom_quadruplet", "atom_ids": ["atom-00000001", "atom-00000002", "atom-00000003", "atom-00000004"]}),
        ("chemistry.steric_accessibility", "inspect", {"type": "atom", "atom_id": "atom-00000001"}),
        ("medical.volumetric_target_geometry", "bbox", {"type": "mask_label_axis", "mask_label": 7, "axis": "z"}),
    ],
)
def test_five_reviewed_leaf_target_specs_require_an_inventory_selector(
    target_leaf: str,
    operation: str,
    selector: dict[str, Any],
) -> None:
    inventory = _inventory_body(target_leaf)
    target = _target_spec(target_leaf, selector=selector, operation=operation)
    checked = validate_target_spec(
        target,
        inventory=inventory,
        inventory_digest=target["scene_inventory_digest"],
    )
    assert checked["selector"] == selector


def test_target_spec_rejects_empty_default_and_invented_selectors() -> None:
    target_leaf = "chemistry.steric_accessibility"
    inventory = _inventory_body(target_leaf)
    target = _target_spec(target_leaf, selector={"type": "atom", "atom_id": "atom-00000001"}, operation="inspect")
    target["selector"] = {"type": "atom", "atom_id": "default"}
    with pytest.raises(MeasurementContractError):
        validate_target_spec(target, inventory=inventory, inventory_digest=target["scene_inventory_digest"])

    target = _target_spec(target_leaf, selector={"type": "atom", "atom_id": "atom-99999999"}, operation="inspect")
    with pytest.raises(MeasurementContractError, match="absent"):
        validate_target_spec(target, inventory=inventory, inventory_digest=target["scene_inventory_digest"])

    target = _target_spec(target_leaf, selector={"type": "atom", "atom_id": "atom-00000001"}, operation="inspect")
    target["answer_fields"] = []
    with pytest.raises(MeasurementContractError):
        validate_target_spec(target, inventory=inventory, inventory_digest=target["scene_inventory_digest"])


def test_measurement_request_is_an_exact_target_spec_replay() -> None:
    target_leaf = "chemistry.conformation_analysis"
    target = _target_spec(
        target_leaf,
        selector={"type": "atom_quadruplet", "atom_ids": ["atom-00000001", "atom-00000002", "atom-00000003", "atom-00000004"]},
        operation="measure_dihedral",
    )
    request = make_measurement_request(target, candidate_digest=_digest("candidate"), request_id="measurement-conformation-001")
    assert request["target_spec_digest"] == digest(target)
    request["selector"] = {"type": "atom_pair", "atom_ids": ["atom-00000001", "atom-00000002"]}
    with pytest.raises(MeasurementContractError, match="changed TargetSpec field"):
        validate_measurement_request(request, target_spec=target)


def _persist_inventory(paths: WorkspacePaths, body: dict[str, Any], *, digest_value: str) -> Path:
    envelope = make_envelope(
        kind="qagen_scene_inventory",
        identifier="inventory-test",
        status="ready",
        body=body,
    )
    assert envelope["content_digest"] == digest_value
    path = paths.private / "measurement" / "inventories" / "inventory-test.json"
    atomic_write_json(path, envelope, private=True)
    return path


def test_inventory_digest_loader_uses_only_its_content_addressed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    body = _inventory_body("chemistry.steric_accessibility")
    envelope = make_envelope(kind="qagen_scene_inventory", identifier="inventory-direct", status="ready", body=body)
    path = paths.private / "measurement" / "inventories" / f"inventory-{envelope['content_digest'][7:23]}.json"
    atomic_write_json(path, envelope, private=True)

    def unexpected_historical_scan(*_args: Any, **_kwargs: Any) -> list[Path]:
        raise AssertionError("digest lookup must not glob historical inventories")

    monkeypatch.setattr(Path, "glob", unexpected_historical_scan)
    loaded = inventory_module.load_scene_inventory_by_digest(paths, envelope["content_digest"])

    assert loaded["path"] == path
    assert loaded["content_digest"] == envelope["content_digest"]


def test_host_places_measurement_values_only_in_a_private_sidecar(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    target_leaf = "chemistry.steric_accessibility"
    inventory = _inventory_body(target_leaf)
    inventory_envelope = make_envelope(kind="qagen_scene_inventory", identifier="inventory-test", status="ready", body=inventory)
    inventory_digest = inventory_envelope["content_digest"]
    target = _target_spec(target_leaf, selector={"type": "atom", "atom_id": "atom-00000001"}, operation="inspect")
    target["scene_inventory_digest"] = inventory_digest
    inventory_path = paths.private / "measurement" / "inventories" / "inventory-test.json"
    atomic_write_json(inventory_path, inventory_envelope, private=True)
    request = make_measurement_request(target, candidate_digest=_digest("candidate"), request_id="measurement-steric-001")
    seen: dict[str, Any] = {}

    def host(operation: str, backend: dict[str, Any], scene_ref: str) -> dict[str, Any]:
        seen.update({"operation": operation, "backend": backend, "scene_ref": scene_ref})
        return {
            "truth": {"answer": "measured-value-7.5"},
            "viewer_receipt_digest": _digest("viewer"),
            "evidence_digest": _digest("evidence"),
            "operation_args": {"selector": {"type": "atom", "atom_id": "opaque-host-only"}},
            "measurement_result": {"value": 7.5, "unit": "angstrom"},
            "measurement_tolerance": {"absolute": 0.1, "unit": "angstrom"},
            "viewer_session_digest": _digest("viewer-session"),
            "executor_identity": {
                "host_id": "measurement-test-host",
                "implementation": "measurement-test-native-v1",
                "runtime_identity_digest": _digest("runtime"),
            },
        }

    result = execute_measurement_request(
        paths,
        request=request,
        target_spec=target,
        inventory_path=inventory_path,
        executor=host,
    )
    assert seen["operation"] == "inspect"
    assert seen["backend"] == {"type": "atom", "atoms": [{"model_index": 1, "chain_id": "A", "residue_number": 1, "insertion_code": "", "atom_name": "N", "altloc": ""}]}
    sidecar_path = Path(result["private_sidecar_path"])
    receipt_path = Path(result["measurement_receipt_path"])
    assert stat.S_IMODE(sidecar_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert "measured-value-7.5" in sidecar_path.read_text(encoding="utf-8")
    assert "measured-value-7.5" not in receipt_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["body"]["private_sidecar_digest"] == result["private_sidecar_digest"]
    assert receipt["body"]["measurement_unit"] == "angstrom"
    assert receipt["body"]["measurement_tolerance"] == {"absolute": 0.1, "unit": "angstrom"}
    assert "measurement_result" not in receipt["body"]
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["body"]["measurement_result"] == {"value": 7.5, "unit": "angstrom"}
    assert sidecar["body"]["executor_identity"]["host_id"] == "measurement-test-host"


def test_independent_verifier_approval_binds_every_digest(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    values = {
        "candidate_digest": _digest("candidate"),
        "target_spec_digest": _digest("target"),
        "measurement_receipt_digest": _digest("receipt"),
        "verifier_output_digest": _digest("verifier"),
    }
    approval = approve_measurement_verifier(paths, rationale="Independent reviewer accepted the receipt binding.", **values)
    loaded = load_measurement_verifier_approval(paths, approval["approval_path"], **values)
    assert loaded["content_digest"] == approval["approval_digest"]
    with pytest.raises(MeasurementContractError, match="binding_mismatch"):
        load_measurement_verifier_approval(paths, approval["approval_path"], **{**values, "candidate_digest": _digest("other")})


def test_inventory_build_uses_only_attested_record_and_scene_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    asset = b"fake-cif-bytes"
    asset_sha = sha256(asset).hexdigest()
    record_body = {
        "target_leaf": "chemistry.conformation_analysis",
        "assets": [{"role": "primary", "bytes": len(asset), "sha256": asset_sha}],
        # The source record deliberately contains fields that inventory code
        # must not read or copy.
        "legacy_regression": {"reference_answer": {"answer": "legacy-secret"}},
        "source_identity": {"question_id": "legacy-question"},
    }
    record = make_envelope(kind="v32_private_source_record", identifier="record-test", status="pending", body=record_body)
    record_path = paths.v32_private_sources / "records" / "record-test.json"
    atomic_write_json(record_path, record, private=True)

    catalog, backend = _molecular_catalog()

    class FakeRegistry:
        content_digest = _digest("registry")

        @classmethod
        def load(cls, _paths: WorkspacePaths, _path: str | Path) -> "FakeRegistry":
            return cls()

        def public_scene_descriptor(self, scene_ref: str) -> dict[str, Any]:
            return {"anonymous_scene_ref": scene_ref, "asset_set_digest": _digest("asset-set")}

        def resolve(self, scene_ref: str) -> dict[str, Any]:
            return {
                "anonymous_scene_ref": scene_ref,
                "asset_format": "cif",
                "asset_bytes": asset,
                "asset_content_digest": f"sha256:{asset_sha}",
            }

    monkeypatch.setattr(inventory_module, "BatchSceneRegistry", FakeRegistry)
    monkeypatch.setattr(inventory_module, "_gemmi_inventory", lambda _asset: ({key: catalog[key] for key in ("components", "residues", "atoms")}, backend))
    result = build_scene_inventory(
        paths,
        source_record_digest=record["content_digest"],
        registry_path=tmp_path / "unused-registry.json",
        anonymous_scene_ref="scene://inventory-test",
    )
    output = Path(result["inventory_path"])
    payload = output.read_text(encoding="utf-8")
    assert "legacy-secret" not in payload
    assert "legacy-question" not in payload
    assert "fake-cif-bytes" not in payload
    assert result["selector_types"] == ["atom_pair", "atom_quadruplet"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


_TINY_CIF = b"""data_tiny
#
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.pdbx_formal_charge
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . GLY A 1 1 ? 0.0 0.0 0.0 1.0 10.0 ? 1 GLY A N 1
ATOM 2 C CA . GLY A 1 1 ? 1.0 0.0 0.0 1.0 10.0 ? 1 GLY A CA 1
ATOM 3 C C . GLY A 1 1 ? 2.0 0.0 0.0 1.0 10.0 ? 1 GLY A C 1
ATOM 4 O O . GLY A 1 1 ? 3.0 0.0 0.0 1.0 10.0 ? 1 GLY A O 1
#
"""


def test_real_cif_and_xyz_extractors_expose_only_anonymous_selector_catalogs() -> None:
    pytest.importorskip("gemmi")
    pytest.importorskip("ase")

    cif_catalog, cif_backend = inventory_module._gemmi_inventory(_TINY_CIF)
    xyz_catalog, xyz_backend = inventory_module._ase_xyz_inventory(
        b"4\nprivate source comment\nC 0.0 0.0 0.0\nH 0.0 0.0 1.0\nH 0.0 1.0 0.0\nH 1.0 0.0 0.0\n"
    )

    for catalog in (cif_catalog, xyz_catalog):
        serialized = json.dumps(catalog, sort_keys=True)
        assert "GLY" not in serialized
        assert "private source comment" not in serialized
        assert "0.0" not in serialized
        assert catalog["components"][0]["chain_id"] == "chain-0001"
        assert catalog["residues"][0]["residue_name"] == "residue-000001"
        assert catalog["atoms"][0]["atom_name"] == "atom-00000001"
        assert catalog["atoms"][0]["coordinate_identity"] == "coordinate-00000001"
    assert cif_catalog["atoms"][0]["element"] == "N"
    assert xyz_catalog["atoms"][0]["element"] == "C"
    assert cif_backend["atom-00000001"]["atom_name"] == "N"
    assert xyz_backend["atom-00000001"] == {"structure_index": 0, "atom_index": 1, "format": "xyz"}


def test_real_nifti_extractor_exposes_shape_axes_layers_and_labels_only() -> None:
    nib = pytest.importorskip("nibabel")
    np = pytest.importorskip("numpy")
    primary = nib.Nifti1Image(np.zeros((2, 3, 4), dtype=np.int16), np.eye(4)).to_bytes()
    mask = nib.Nifti1Image(
        np.array(
            [
                [[0, 2, 0, 7], [0, 0, 0, 0], [2, 0, 0, 0]],
                [[0, 0, 0, 0], [0, 7, 0, 0], [0, 0, 0, 0]],
            ],
            dtype=np.int16,
        ),
        np.eye(4),
    ).to_bytes()

    catalog, backend = inventory_module._nifti_inventory(primary, mask)

    assert catalog == {
        "components": [],
        "residues": [],
        "atoms": [],
        "mask_labels": [2, 7],
        "axes": ["x", "y", "z"],
        "volume_shape": [2, 3, 4],
        "layers": ["layer-0001", "layer-0002"],
    }
    assert backend == {}
    serialized = json.dumps(catalog, sort_keys=True)
    assert "bbox" not in serialized
    assert "measure_volume" not in serialized
    assert "volume_mm" not in serialized
    assert "affine" not in serialized


def test_paged_selector_catalog_keeps_complete_stable_atom_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    monkeypatch.setattr(inventory_module, "_SELECTOR_PAGE_SIZE", 2)
    catalog, backend = _molecular_catalog()
    for index in range(5, 8):
        atom_id = f"atom-{index:08d}"
        catalog["atoms"].append(
            {
                "atom_id": atom_id,
                "residue_id": "residue-000002",
                "atom_name": atom_id,
                "element": "C",
                "coordinate_identity": f"coordinate-{index:08d}",
            }
        )
        backend[atom_id] = {
            "model_index": 1,
            "chain_id": "B",
            "residue_number": 2,
            "insertion_code": "",
            "atom_name": "C",
            "altloc": "",
        }
    source_digest = _digest("paged-source")
    registry_digest = _digest("paged-registry")
    scene_ref = "scene://paged-selector-fixture"
    paged_catalog, selector_index = inventory_module._page_selector_catalog(
        paths,
        source_record_digest=source_digest,
        scene_registry_digest=registry_digest,
        anonymous_scene_ref=scene_ref,
        catalog=catalog,
    )
    assert paged_catalog["atoms"] == []
    assert selector_index["atoms"]["mode"] == "paged"
    assert selector_index["atoms"]["count"] == 7
    assert [page["entry_count"] for page in selector_index["atoms"]["pages"]] == [2, 2, 2, 1]
    body = {
        "inventory_version": 1,
        "source_record_digest": source_digest,
        "target_leaf": "chemistry.steric_accessibility",
        "scene_registry_digest": registry_digest,
        "anonymous_scene_ref": scene_ref,
        "asset_set_digest": _digest("paged-assets"),
        "asset_sha256": {"primary": _digest("paged-primary")},
        "selector_types": ["atom", "atom_pair", "residue"],
        "selector_catalog": paged_catalog,
        "selector_index": selector_index,
        "backend_selector_map": backend,
        "local_only": True,
        "release_eligible": False,
    }
    inventory = make_envelope(kind="qagen_scene_inventory", identifier="paged-inventory", status="ready", body=body)
    inventory_path = paths.private / "measurement" / "inventories" / "paged-inventory.json"
    atomic_write_json(inventory_path, inventory, private=True)

    loaded = inventory_module.load_scene_inventory(paths, inventory_path)
    assert len(loaded["selector_pages"]) == 4
    target = {
        "target_spec_version": 1,
        "source_record_digest": source_digest,
        "scene_inventory_digest": inventory["content_digest"],
        "anonymous_scene_ref": scene_ref,
        "candidate_plan_digest": _digest("paged-plan"),
        "target_leaf": "chemistry.steric_accessibility",
        "allowed_native_operations": ["inspect"],
        "native_operation": "inspect",
        "selector": {"type": "atom", "atom_id": "atom-00000007"},
        "answer_fields": ["answer"],
    }
    validate_target_spec(target, inventory=loaded["body"], inventory_digest=inventory["content_digest"])
    assert selector_backend(loaded["body"], target["selector"])["atoms"][0]["atom_name"] == "C"

    first_page_digest = selector_index["atoms"]["pages"][0]["content_digest"]
    first_page = next(
        path
        for path in (paths.private / "measurement" / "inventory-pages").glob("*.json")
        if read_json(path).get("content_digest") == first_page_digest
    )
    first_page.unlink()
    with pytest.raises(inventory_module.SceneInventoryError, match="scene_inventory_unavailable"):
        inventory_module.load_scene_inventory(paths, inventory_path)


def test_batch_materializer_matches_source_records_by_closed_asset_facts_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    asset = b"fake-cif-bytes"
    asset_sha = sha256(asset).hexdigest()
    source_record = make_envelope(
        kind="v32_private_source_record",
        identifier="source-record",
        status="pending",
        body={
            "target_leaf": "chemistry.conformation_analysis",
            "assets": [{"role": "primary", "bytes": len(asset), "sha256": asset_sha, "path": "private/source.cif"}],
            "source_identity": {"question_id": "legacy-question-id", "task_id": "legacy-task-id"},
        },
    )
    atomic_write_json(paths.v32_private_sources / "records" / "source-record.json", source_record, private=True)
    slot_digest = _digest("batch-slot")
    batch = make_envelope(
        kind="vnext_selection_batch_manifest",
        identifier="batch",
        status="compiled",
        body={
            "total": 1,
            "rows": [
                {
                    "target_leaf": "chemistry.conformation_analysis",
                    "assets": [{"role": "primary", "bytes": len(asset), "sha256": asset_sha}],
                    "slot_ref": {"content_digest": slot_digest},
                }
            ],
        },
    )
    batch_path = paths.selection_batches / "fixture.vnext-batch.json"
    atomic_write_json(batch_path, batch, private=True)
    asset_set_digest = inventory_module._asset_set_digest(
        [{"role": "primary", "bytes": len(asset), "sha256": asset_sha}]
    )

    class FakeRegistry:
        content_digest = _digest("registry")
        selection_batch_digest = batch["content_digest"]
        path = paths.private / "viewer" / "fixture.registry.json"
        anonymous_scene_refs = ("scene://v32-inventory-fixture",)

        @classmethod
        def load(cls, _paths: WorkspacePaths, _path: str | Path) -> "FakeRegistry":
            return cls()

        def public_scene_descriptor(self, scene_ref: str) -> dict[str, Any]:
            assert scene_ref == self.anonymous_scene_refs[0]
            return {
                "anonymous_scene_ref": scene_ref,
                "asset_set_digest": asset_set_digest,
                "slot_digest": slot_digest,
            }

        def resolve(self, scene_ref: str) -> dict[str, Any]:
            assert scene_ref == self.anonymous_scene_refs[0]
            return {
                "anonymous_scene_ref": scene_ref,
                "asset_format": "cif",
                "asset_bytes": asset,
                "asset_content_digest": f"sha256:{asset_sha}",
            }

    catalog, backend = _molecular_catalog()
    monkeypatch.setattr(inventory_module, "BatchSceneRegistry", FakeRegistry)
    monkeypatch.setattr(
        inventory_module,
        "_gemmi_inventory",
        lambda _asset: ({key: catalog[key] for key in ("components", "residues", "atoms")}, backend),
    )

    result = materialize_v32_scene_inventories(paths, registry_path=FakeRegistry.path)

    assert result["status"] == "ready"
    assert result["ready_count"] == 1
    assert result["blocked_count"] == 0
    assert result["leak_scan"] == {"status": "passed", "checked_inventory_count": 1, "violation_count": 0}
    report = Path(result["inventory_batch_path"])
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    report_text = report.read_text(encoding="utf-8")
    assert "legacy-question-id" not in report_text
    assert "legacy-task-id" not in report_text
    assert "private/source.cif" not in report_text

    def _blocked_inventory(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise inventory_module.SceneInventoryError("inventory_entity_limit_exceeded")

    monkeypatch.setattr(inventory_module, "build_scene_inventory", _blocked_inventory)
    blocked = materialize_v32_scene_inventories(paths, registry_path=FakeRegistry.path)

    assert blocked["status"] == "blocked"
    assert blocked["total"] == 1
    assert blocked["ready_count"] == 0
    assert blocked["blocked_count"] == 1
    blocked_report = json.loads(Path(blocked["inventory_batch_path"]).read_text(encoding="utf-8"))
    assert blocked_report["body"]["mapping_complete"] is True
    assert blocked_report["body"]["results"] == [
        {
            "anonymous_scene_ref": FakeRegistry.anonymous_scene_refs[0],
            "blocker": "inventory_entity_limit_exceeded",
            "selection_slot_digest": slot_digest,
            "source_record_digest": source_record["content_digest"],
            "status": "blocked",
        }
    ]
    blocked_text = json.dumps(blocked_report, sort_keys=True)
    assert "legacy-question-id" not in blocked_text
    assert "legacy-task-id" not in blocked_text
    assert "private/source.cif" not in blocked_text


def test_batch_materializer_rebuilds_a_nested_fresh_subset_against_parent_inventory_equivalence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    asset = b"fresh5-fake-cif-bytes"
    asset_sha = sha256(asset).hexdigest()
    source_record = make_envelope(
        kind="v32_private_source_record",
        identifier="source-record",
        status="pending",
        body={
            "target_leaf": "chemistry.conformation_analysis",
            "assets": [{"role": "primary", "bytes": len(asset), "sha256": asset_sha, "path": "private/source.cif"}],
            "source_identity": {"question_id": "legacy-question-id", "task_id": "legacy-task-id"},
        },
    )
    atomic_write_json(paths.v32_private_sources / "records" / "source-record.json", source_record, private=True)
    asset_set_digest = inventory_module._asset_set_digest(
        [{"role": "primary", "bytes": len(asset), "sha256": asset_sha}]
    )

    def _manifest(identifier: str, slot_digest: str, *, nested: bool) -> tuple[Path, dict[str, Any]]:
        manifest = make_envelope(
            kind="vnext_selection_batch_manifest",
            identifier=identifier,
            status="compiled",
            body={
                "total": 1,
                "rows": [
                    {
                        "target_leaf": "chemistry.conformation_analysis",
                        "assets": [{"role": "primary", "bytes": len(asset), "sha256": asset_sha}],
                        "slot_ref": {"content_digest": slot_digest},
                    }
                ],
            },
        )
        directory = paths.selection_batches / "derived-pilots" if nested else paths.selection_batches
        path = directory / f"{identifier}.vnext-batch.json"
        atomic_write_json(path, manifest, private=True)
        return path, manifest

    parent_slot = _digest("parent-slot")
    fresh_slot = _digest("fresh-slot")
    parent_manifest_path, parent_manifest = _manifest("formal-v32", parent_slot, nested=False)
    fresh_manifest_path, fresh_manifest = _manifest("fresh5", fresh_slot, nested=True)

    class FakeRegistry:
        def __init__(self, *, path: Path, content_digest: str, selection_batch_digest: str, slot_digest: str, scene_ref: str) -> None:
            self.path = path
            self.content_digest = content_digest
            self.selection_batch_digest = selection_batch_digest
            self.anonymous_scene_refs = (scene_ref,)
            self._slot_digest = slot_digest

        @classmethod
        def load(cls, _paths: WorkspacePaths, value: str | Path) -> "FakeRegistry":
            return registries[Path(value)]

        def public_scene_descriptor(self, scene_ref: str) -> dict[str, Any]:
            assert scene_ref == self.anonymous_scene_refs[0]
            return {
                "anonymous_scene_ref": scene_ref,
                "asset_set_digest": asset_set_digest,
                "slot_digest": self._slot_digest,
            }

        def resolve(self, scene_ref: str) -> dict[str, Any]:
            assert scene_ref == self.anonymous_scene_refs[0]
            return {
                "anonymous_scene_ref": scene_ref,
                "asset_format": "cif",
                "asset_bytes": asset,
                "asset_content_digest": f"sha256:{asset_sha}",
            }

    parent_registry_path = paths.private / "viewer" / "parent.registry.json"
    fresh_registry_path = paths.private / "viewer" / "fresh.registry.json"
    registries = {
        parent_registry_path: FakeRegistry(
            path=parent_registry_path,
            content_digest=_digest("parent-registry"),
            selection_batch_digest=parent_manifest["content_digest"],
            slot_digest=parent_slot,
            scene_ref="scene://v32-parent",
        ),
        fresh_registry_path: FakeRegistry(
            path=fresh_registry_path,
            content_digest=_digest("fresh-registry"),
            selection_batch_digest=fresh_manifest["content_digest"],
            slot_digest=fresh_slot,
            scene_ref="scene://v32-fresh",
        ),
    }
    catalog, backend = _molecular_catalog()
    monkeypatch.setattr(inventory_module, "BatchSceneRegistry", FakeRegistry)
    monkeypatch.setattr(
        inventory_module,
        "_gemmi_inventory",
        lambda _asset: ({key: catalog[key] for key in ("components", "residues", "atoms")}, backend),
    )

    parent = materialize_v32_scene_inventories(paths, registry_path=parent_registry_path)
    fresh = materialize_v32_scene_inventories(
        paths,
        registry_path=fresh_registry_path,
        parent_inventory_batch_path=parent["inventory_batch_path"],
    )

    assert parent["status"] == "ready"
    assert fresh["status"] == "ready"
    assert fresh["total"] == 1
    assert fresh["parent_comparison"] == {
        "status": "passed",
        "checked_count": 1,
        "mismatch_count": 0,
        "required_fields": list(inventory_module._COMPARISON_FIELDS),
    }
    fresh_receipt = inventory_module.load_v32_scene_inventory_batch(
        paths,
        fresh["inventory_batch_path"],
        expected_content_digest=fresh["inventory_batch_digest"],
    )
    assert fresh_receipt["body"]["selection_batch_digest"] == fresh_manifest["content_digest"]
    assert fresh_receipt["body"]["parent_provenance"]["inventory_batch_digest"] == parent["inventory_batch_digest"]
    entry = fresh_receipt["body"]["results"][0]
    assert entry["selection_slot_digest"] == fresh_slot
    assert entry["anonymous_scene_ref"] == "scene://v32-fresh"

    parent_receipt = inventory_module.load_v32_scene_inventory_batch(paths, parent["inventory_batch_path"])
    parent_entry = parent_receipt["body"]["results"][0]
    parent_inventory = inventory_module.load_scene_inventory_by_digest(paths, parent_entry["inventory_digest"])
    fresh_inventory = inventory_module.load_scene_inventory_by_digest(paths, entry["inventory_digest"])
    assert parent_inventory["body"]["anonymous_scene_ref"] != fresh_inventory["body"]["anonymous_scene_ref"]
    assert parent_inventory["body"]["scene_registry_digest"] != fresh_inventory["body"]["scene_registry_digest"]
    assert inventory_module._inventory_equivalence(parent_inventory["body"], fresh_inventory["body"]) == []
    assert parent_manifest_path.exists()
    assert fresh_manifest_path.exists()
