from __future__ import annotations

import pytest

from qagen.viewer import (
    ViewerContractError,
    validate_controlled_session_request,
    validate_local_evidence_packet,
)


_DIGEST = "sha256:" + "a" * 64
_CANDIDATE_DIGEST = "sha256:" + "b" * 64
_PROFILE = {
    "content_digest": _DIGEST,
    "body": {
        "allowed_operations": ["inspect", "rotate"],
    },
}


def test_controlled_session_request_rejects_undeclared_fields() -> None:
    request = {
        "request_id": "session-1",
        "viewer_profile_digest": _DIGEST,
        "allowed_operations": ["inspect"],
        "asset_path": "/private/asset.pdb",
    }

    with pytest.raises(ViewerContractError, match="unsupported field"):
        validate_controlled_session_request(
            request,
            viewer_profile=_PROFILE,
            approved_operations={"inspect", "rotate"},
        )


def test_production_evidence_requires_a_bound_trace_for_each_observation() -> None:
    evidence = {
        "candidate_digest": _CANDIDATE_DIGEST,
        "viewer_profile_digest": _DIGEST,
        "viewer_session_request_id": "session-1",
        "viewer_operations": ["inspect"],
        "observations": [{"operation": "inspect", "observation": "visible"}],
        "viewer_trace": [{"operation": "inspect", "network_access": False}],
        "projection": {"camera": "controlled"},
        "probe": {"metadata_access": False},
    }

    result = validate_local_evidence_packet(
        evidence,
        candidate_digest=_CANDIDATE_DIGEST,
        viewer_profile=_PROFILE,
        approved_operations={"inspect", "rotate"},
        require_full_trace=True,
        expected_session_request_id="session-1",
    )

    assert result == evidence


def test_production_evidence_rejects_an_unsafe_trace() -> None:
    evidence = {
        "candidate_digest": _CANDIDATE_DIGEST,
        "viewer_profile_digest": _DIGEST,
        "viewer_session_request_id": "session-1",
        "observations": [{"operation": "inspect"}],
        "viewer_trace": [{"operation": "inspect", "network_access": True}],
        "projection": {"camera": "controlled"},
        "probe": {"metadata_access": False},
    }

    with pytest.raises(ViewerContractError, match="must be explicitly false"):
        validate_local_evidence_packet(
            evidence,
            candidate_digest=_CANDIDATE_DIGEST,
            viewer_profile=_PROFILE,
            approved_operations={"inspect", "rotate"},
            require_full_trace=True,
            expected_session_request_id="session-1",
        )


@pytest.mark.parametrize(
    "leak",
    [
        {"asset_path": "/private/scene.glb"},
        {"details": {"qid": "real-scene-1"}},
        {"raw_metadata": {"bbox": [0, 1]}},
    ],
)
def test_production_evidence_rejects_raw_scene_identifiers_and_metadata(leak: dict) -> None:
    evidence = {
        "candidate_digest": _CANDIDATE_DIGEST,
        "viewer_profile_digest": _DIGEST,
        "viewer_session_request_id": "session-1",
        "observations": [{"operation": "inspect", **leak}],
        "viewer_trace": [{"operation": "inspect", "network_access": False}],
        "projection": {"camera": "controlled"},
        "probe": {"metadata_access": False},
    }

    with pytest.raises(ViewerContractError, match="forbidden solver-visible data"):
        validate_local_evidence_packet(
            evidence,
            candidate_digest=_CANDIDATE_DIGEST,
            viewer_profile=_PROFILE,
            approved_operations={"inspect", "rotate"},
            require_full_trace=True,
            expected_session_request_id="session-1",
        )
