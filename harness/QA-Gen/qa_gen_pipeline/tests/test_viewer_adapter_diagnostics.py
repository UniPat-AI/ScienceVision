from __future__ import annotations

from pathlib import Path

import pytest

from qagen.viewer import (
    AdapterHealth,
    AdapterObservation,
    AdapterProfile,
    AdapterSession,
    ViewerAdapterError,
    diagnose_viewer_adapter,
    validate_adapter_profile,
    validate_measurement_operation,
    validate_session_operation,
)

from conftest import PACKAGE_ROOT


_DIGEST = "sha256:" + "a" * 64
_FORBIDDEN = frozenset(
    {
        "network",
        "asset_file_read",
        "raw_asset_metadata",
        "raw_asset_read",
        "arbitrary_compute",
        "unrestricted_compute",
    }
)


def _profile(**overrides: object) -> AdapterProfile:
    values: dict[str, object] = {
        "adapter_id": "controlled-v1",
        "available_operations": frozenset({"inspect", "rotate"}),
        "native_measurement_operations": frozenset(),
        "forbidden_capabilities": _FORBIDDEN,
        "accepts_anonymous_scene_ref": True,
        "accepts_solver_asset_identifiers": False,
        "exposes_raw_asset_metadata": False,
        "exposes_raw_asset_bytes": False,
        "network_access": False,
        "arbitrary_compute": False,
        "attested_evidence": True,
    }
    values.update(overrides)
    return AdapterProfile(**values)  # type: ignore[arg-type]


def test_sciencevision_mcp_template_reports_real_contract_gaps() -> None:
    result = diagnose_viewer_adapter(
        PACKAGE_ROOT / "configs" / "viewer-adapters" / "sciencevision-mcp-local.template.yaml"
    )

    assert result["status"] == "blocked"
    assert result["production_ready"] is False
    backend = result["health"]["backend_contract"]
    assert backend["session_source_present"] is True
    assert backend["open_schema_present"] is True
    assert backend["accepts_raw_scene_selector"] is True
    assert backend["accepts_anonymous_scene_ref"] is False
    assert "controlled_adapter_implementation_missing" in result["blockers"]
    assert "anonymous_scene_registry_missing" in result["blockers"]
    assert "attested_evidence_missing" in result["blockers"]


def test_diagnostic_reports_profile_operations_and_native_measurement_gap() -> None:
    viewer_profile = {
        "content_digest": _DIGEST,
        "status": "fixture_only",
        "body": {
            "allowed_operations": ["inspect", "measure_dihedral"],
            "production_eligible": False,
        },
    }

    result = diagnose_viewer_adapter(
        PACKAGE_ROOT / "configs" / "viewer-adapters" / "sciencevision-mcp-local.template.yaml",
        viewer_profile=viewer_profile,
    )

    assert result["profile"]["missing_operations"] == ["inspect", "measure_dihedral"]
    assert result["profile"]["non_native_measurements"] == ["measure_dihedral"]
    assert "profile_measurement_not_viewer_native:measure_dihedral" in result["blockers"]
    assert "viewer_profile_not_production_eligible" in result["blockers"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"asset_path": "/private/sample.glb"}, "forbidden solver-visible data"),
        ({"result": {"qid": "real-scene-17"}}, "forbidden solver-visible data"),
        ({"result": "https://example.invalid/sample.ply"}, "file or network URL"),
        ({"result": "sample.nii.gz"}, "raw asset reference"),
    ],
)
def test_adapter_observation_rejects_raw_scene_data(payload: dict, message: str) -> None:
    with pytest.raises(ViewerAdapterError, match=message):
        AdapterObservation(
            operation="inspect",
            sequence=0,
            payload=payload,
            trace_digest=_DIGEST,
        )


def test_adapter_profile_requires_every_measurement_to_be_viewer_native() -> None:
    with pytest.raises(ViewerAdapterError, match="every advertised measurement"):
        _profile(available_operations=frozenset({"inspect", "measure_dihedral"}))


def test_adapter_profile_rejects_non_native_measurement_call() -> None:
    profile = _profile()

    with pytest.raises(ViewerAdapterError, match="not viewer-native"):
        validate_measurement_operation("measure_dihedral", profile)


def test_session_operation_is_scoped_by_request_and_adapter_profile() -> None:
    session = AdapterSession(
        session_id="opaque-session-1",
        request_id="request-1",
        viewer_profile_digest=_DIGEST,
        allowed_operations=frozenset({"inspect"}),
    )
    profile = _profile()

    assert validate_session_operation(session, "inspect", profile) == "inspect"
    with pytest.raises(ViewerAdapterError, match="outside the approved viewer session"):
        validate_session_operation(session, "rotate", profile)


def test_adapter_profile_requires_ready_health_and_full_isolation() -> None:
    profile = _profile()
    assert validate_adapter_profile(
        profile,
        AdapterHealth(status="ready", adapter_id="controlled-v1", detail="local controlled adapter ready"),
    ) is profile

    with pytest.raises(ViewerAdapterError, match="not ready"):
        validate_adapter_profile(
            profile,
            AdapterHealth(status="degraded", adapter_id="controlled-v1", detail="trace sink unavailable"),
        )


def test_adapter_config_rejects_unknown_security_claim(tmp_path: Path) -> None:
    config = tmp_path / "configs" / "viewer-adapters" / "bad.yaml"
    schema_dir = tmp_path / "configs" / "schemas"
    config.parent.mkdir(parents=True)
    schema_dir.mkdir(parents=True)
    schema_dir.joinpath("viewer_adapter.schema.json").write_text(
        (PACKAGE_ROOT / "configs" / "schemas" / "viewer_adapter.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    value = (PACKAGE_ROOT / "configs" / "viewer-adapters" / "sciencevision-mcp-local.template.yaml").read_text(
        encoding="utf-8"
    )
    config.write_text(value.replace("  attested_evidence: false", "  attested_evidence: false\n  shell_access: true"), encoding="utf-8")

    with pytest.raises(ViewerAdapterError, match="schema validation"):
        diagnose_viewer_adapter(config)
