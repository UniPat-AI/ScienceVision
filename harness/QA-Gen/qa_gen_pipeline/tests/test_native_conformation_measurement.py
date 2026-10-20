from __future__ import annotations

from pathlib import Path
import stat
from typing import Any

import pytest

import qagen.measurement.conformation as conformation_module
from qagen.contracts import digest, digest_bytes, make_envelope
from qagen.measurement import (
    execute_v32_native_conformation_measurement,
    make_measurement_request,
    scan_native_measurement_leaks,
    verify_native_measurement_replay_consistency,
)
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import atomic_write_json, read_json


def _digest(label: str) -> str:
    return digest({"native_conformation_test": label})


def _fixture_inventory(
    *,
    source_record_digest: str,
    registry_digest: str,
    scene_ref: str,
    asset_digest: str,
) -> dict[str, Any]:
    atoms = [
        {
            "atom_id": f"atom-{index:08d}",
            "residue_id": "residue-000001",
            "atom_name": f"atom-{index:08d}",
            "element": "C",
            "coordinate_identity": f"coordinate-{index:08d}",
        }
        for index in range(1, 7)
    ]
    backend: dict[str, dict[str, Any]] = {
        "component-0001": {"structure_index": 0, "component_index": 1, "format": "xyz"},
        "residue-000001": {"structure_index": 0, "residue_index": 1, "format": "xyz"},
    }
    backend.update(
        {
            atom["atom_id"]: {
                "structure_index": 0,
                "atom_index": index,
                "format": "xyz",
            }
            for index, atom in enumerate(atoms, start=1)
        }
    )
    return {
        "inventory_version": 1,
        "source_record_digest": source_record_digest,
        "target_leaf": "chemistry.conformation_analysis",
        "scene_registry_digest": registry_digest,
        "anonymous_scene_ref": scene_ref,
        "asset_set_digest": _digest("asset-set"),
        "asset_sha256": {"primary": asset_digest},
        "selector_types": ["atom_quadruplet"],
        "selector_catalog": {
            "components": [
                {"component_id": "component-0001", "chain_id": "chain-0001"}
            ],
            "residues": [
                {
                    "residue_id": "residue-000001",
                    "component_id": "component-0001",
                    "residue_name": "residue-000001",
                    "residue_number": 1,
                    "insertion_code": "",
                }
            ],
            "atoms": atoms,
            "mask_labels": [],
            "axes": [],
            "volume_shape": [],
            "layers": [],
        },
        "backend_selector_map": backend,
        "local_only": True,
        "release_eligible": False,
    }


def _install_fixture_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    values: tuple[float, ...] = (-61.2,),
) -> tuple[WorkspacePaths, dict[str, Any], dict[str, Any], Path, Path]:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    scene_ref = "scene://native-conformation-fixture"
    source_record_digest = _digest("source-record")
    registry_digest = _digest("registry")
    asset = (
        b"6\nprivate fixture\nC 0 0 0\nC 1 0 0\nC 1 1 0\nC 2 1 1\nC 3 1 1\nC 3 2 1\n"
    )
    asset_digest = digest_bytes(asset)
    inventory_body = _fixture_inventory(
        source_record_digest=source_record_digest,
        registry_digest=registry_digest,
        scene_ref=scene_ref,
        asset_digest=asset_digest,
    )
    inventory = make_envelope(
        kind="qagen_scene_inventory",
        identifier="native-conformation-fixture-inventory",
        status="ready",
        body=inventory_body,
    )
    inventory_path = (
        paths.private
        / "measurement"
        / "inventories"
        / "native-conformation-fixture.json"
    )
    atomic_write_json(inventory_path, inventory, private=True)
    target_spec = {
        "target_spec_version": 1,
        "source_record_digest": source_record_digest,
        "scene_inventory_digest": inventory["content_digest"],
        "anonymous_scene_ref": scene_ref,
        "candidate_plan_digest": _digest("candidate-plan"),
        "target_leaf": "chemistry.conformation_analysis",
        "allowed_native_operations": ["measure_dihedral"],
        "native_operation": "measure_dihedral",
        "selector": {
            "type": "atom_quadruplet",
            "atom_ids": [
                "atom-00000001",
                "atom-00000002",
                "atom-00000004",
                "atom-00000006",
            ],
        },
        "answer_fields": ["dihedral_angle_deg"],
    }
    request = make_measurement_request(
        target_spec,
        candidate_digest=_digest("preauthor-anchor"),
        request_id="measurement-native-conformation-fixture",
        request_mode="preauthor_native_measurement_pilot",
    )

    class FixtureRegistry:
        content_digest = registry_digest

        @classmethod
        def load(cls, _paths: WorkspacePaths, _path: str | Path) -> "FixtureRegistry":
            return cls()

        @property
        def viewer_bundle(self) -> dict[str, str]:
            return {"root": "/private/native-conformation-fixture-viewer"}

        @staticmethod
        def public_scene_descriptor(value: str) -> dict[str, Any]:
            assert value == scene_ref
            return {
                "anonymous_scene_ref": scene_ref,
                "viewer_family": "molstar",
                "asset_format": "xyz",
                "operation_allowlist": ["measure_dihedral"],
            }

        @staticmethod
        def resolve(value: str) -> dict[str, Any]:
            assert value == scene_ref
            return {
                "anonymous_scene_ref": scene_ref,
                "asset_format": "xyz",
                "asset_bytes": asset,
                "asset_content_digest": asset_digest,
            }

    class FixtureHost:
        calls = 0

        def __init__(self, root: str) -> None:
            assert root == "/private/native-conformation-fixture-viewer"

        def native_measure_xyz_dihedral(
            self,
            _scene: object,
            *,
            selector: dict[str, Any],
            timeout_seconds: float,
        ) -> dict[str, Any]:
            assert timeout_seconds == 45.0
            assert selector == {
                "type": "atom_quadruplet",
                "atoms": [
                    {"structure_index": 0, "atom_index": 1, "format": "xyz"},
                    {"structure_index": 0, "atom_index": 2, "format": "xyz"},
                    {"structure_index": 0, "atom_index": 4, "format": "xyz"},
                    {"structure_index": 0, "atom_index": 6, "format": "xyz"},
                ],
            }
            type(self).calls += 1
            session = type(self).calls
            return {
                "native_operation": "measure_dihedral",
                "operation_args": {"selector": selector},
                "measurement_result": {
                    "value": values[(session - 1) % len(values)],
                    "unit": "degree",
                    "absolute_tolerance": 0.1,
                },
                "viewer_session_digest": _digest(f"session-{session}"),
                "executor_identity": {
                    "host_id": "native-conformation-fixture-host",
                    "implementation": "fixture-native-measurement-v1",
                    "runtime_identity_digest": _digest("runtime"),
                },
                "render_digest": _digest(f"render-{session}"),
            }

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(conformation_module, "BatchSceneRegistry", FixtureRegistry)
    monkeypatch.setattr(
        conformation_module, "ControlledBatchViewerReplayHost", FixtureHost
    )
    return (
        paths,
        target_spec,
        request,
        inventory_path,
        paths.private / "viewer" / "fixture-registry.json",
    )


def _measure(
    paths: WorkspacePaths,
    target_spec: dict[str, Any],
    request: dict[str, Any],
    inventory_path: Path,
    registry_path: Path,
) -> dict[str, Any]:
    return execute_v32_native_conformation_measurement(
        paths,
        request=request,
        target_spec=target_spec,
        inventory_path=inventory_path,
        registry_path=registry_path,
    )


def test_native_conformation_measurement_keeps_truth_only_in_private_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, target_spec, request, inventory_path, registry_path = _install_fixture_host(
        monkeypatch, tmp_path
    )
    first = _measure(paths, target_spec, request, inventory_path, registry_path)
    second = _measure(paths, target_spec, request, inventory_path, registry_path)

    receipt_path = Path(first["measurement_receipt_path"])
    sidecar_path = Path(first["private_sidecar_path"])
    receipt_text = receipt_path.read_text(encoding="utf-8")
    receipt = read_json(receipt_path)
    sidecar = read_json(sidecar_path)
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(sidecar_path.stat().st_mode) == 0o600
    assert "dihedral_angle_deg" not in receipt_text
    assert "-61.2" not in receipt_text
    assert "measurement_result" not in receipt["body"]
    assert sidecar["body"]["truth"] == {"dihedral_angle_deg": -61.2}
    assert sidecar["body"]["measurement_result"] == {"value": -61.2, "unit": "degree"}

    assert scan_native_measurement_leaks(paths, measurement=first)["status"] == "passed"
    replay = verify_native_measurement_replay_consistency(
        paths,
        first_measurement=first,
        second_measurement=second,
    )
    assert replay["status"] == "passed"


def test_native_conformation_replay_rejects_inconsistent_fresh_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, target_spec, request, inventory_path, registry_path = _install_fixture_host(
        monkeypatch,
        tmp_path,
        values=(-61.2, 112.7),
    )
    first = _measure(paths, target_spec, request, inventory_path, registry_path)
    second = _measure(paths, target_spec, request, inventory_path, registry_path)

    replay = verify_native_measurement_replay_consistency(
        paths,
        first_measurement=first,
        second_measurement=second,
    )
    assert replay["status"] == "failed"


def test_native_conformation_leak_scan_rejects_a_receipt_with_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, target_spec, request, inventory_path, registry_path = _install_fixture_host(
        monkeypatch, tmp_path
    )
    measurement = _measure(paths, target_spec, request, inventory_path, registry_path)
    original = read_json(Path(measurement["measurement_receipt_path"]))
    leaked_body = dict(original["body"])
    leaked_body["truth"] = {"dihedral_angle_deg": -61.2}
    leaked = make_envelope(
        kind="qagen_measurement_receipt",
        identifier="native-conformation-fixture-leaked-receipt",
        status="completed",
        body=leaked_body,
        producer="qagen-test:native-conformation-leak",
    )
    leaked_path = (
        paths.private
        / "measurement"
        / "receipts"
        / "leaked-native-conformation-fixture.json"
    )
    atomic_write_json(leaked_path, leaked, private=True)
    leaked_measurement = {
        **measurement,
        "measurement_receipt_path": str(leaked_path),
        "measurement_receipt_digest": leaked["content_digest"],
    }

    scan = scan_native_measurement_leaks(paths, measurement=leaked_measurement)
    assert scan["status"] == "failed"
    assert scan["violation_count"] >= 2
    assert "-61.2" not in str(scan)
