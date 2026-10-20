from __future__ import annotations

from pathlib import Path
import stat
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from qagen.contracts import digest, make_envelope
from qagen.reviewer_bundle import (
    PendingReviewerBundleError,
    build_pending_reviewer_bundle,
    create_pending_reviewer_bundle_server,
    verify_pending_reviewer_bundle,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    run.mkdir()
    candidate = {
        "artifact_kind": "author_private_candidate",
        "task_id": "pending-run-001",
        "prompt": "Use the controlled viewer to measure the requested dihedral.",
        "anonymous_scene_ref": "scene://private-source-reference",
        "viewer_session_request": {
            "request_id": "pending-run-001-viewer",
            "viewer_profile_digest": digest({"viewer": "molstar"}),
            "allowed_operations": ["rotate", "render", "measure_dihedral"],
        },
        "visual_target": {
            "object_reference": "Four selected atoms around one central bond.",
            "selector_type": "atom_quadruplet",
            "highlighted_atom_ids": ["atom-1", "atom-2", "atom-3", "atom-4"],
        },
        "rubric": {
            "answer_format": "signed_numeric_degree_one_decimal",
            "viewer_necessity": "controlled_viewer_measure_dihedral_required",
        },
    }
    truth = {"artifact_kind": "private_truth", "answer": {"dihedral_angle_deg": 61.2}}
    verifier = {"artifact_kind": "private_verifier", "verdict": "pass", "reference_answer": 61.2}
    stages = {
        role: {
            "status": "completed",
            "worker_kind": "deterministic" if role == "truth_builder" else "model",
            "provider": "control_plane" if role == "truth_builder" else "fixture",
            "model": "truth" if role == "truth_builder" else "fixture-model",
            "output_digest": digest({"stage": role}),
        }
        for role in ("brief_builder", "author", "truth_builder", "verifier")
    }
    stages["author"]["output_digest"] = digest({"author": "draft"})
    state_body = {
        "run_id": "pending-run-001",
        "status": "verification_complete_pending_human_review",
        "staging_enabled": False,
        "release_eligible": False,
        "candidate_digest": digest(candidate),
        "truth_digest": digest(truth),
        "verification_digest": digest(verifier),
        "stages": stages,
        "usage": {"token_in": 42, "token_out": 7, "cost_usd": 0.1},
    }
    _write_json(
        run / "state.json",
        make_envelope(
            kind="qagen_v32_text_only_execution_state",
            identifier="pending-run-001-state",
            status="verification_complete_pending_human_review",
            body=state_body,
        ),
    )
    _write_json(run / "execution-lock.json", {"private": True})
    _write_json(run / "outputs" / "candidate.json", candidate)
    _write_json(run / "outputs" / "truth_builder.json", truth)
    _write_json(run / "outputs" / "verifier.json", verifier)
    _write_json(run / "outputs" / "brief_builder.json", {"brief": "private"})
    _write_json(run / "outputs" / "author.json", {"draft": "private"})
    _write_json(run / "evidence" / "receipt.json", {"evidence": "private"})

    source = tmp_path / "source"
    asset = source / "assets" / "private.xyz"
    asset.parent.mkdir(parents=True)
    asset.write_text("4\nfixture\nC 0 0 0\nC 1 0 0\nC 1 1 0\nC 2 1 1\n", encoding="utf-8")
    viewer = source / "viewer"
    (viewer / "shared").mkdir(parents=True)
    (viewer / "molstar.html").write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
    (viewer / "shared" / "runtime.js").write_text("window.fixture = true;\n", encoding="utf-8")
    registry_body = {
        "local_only": True,
        "release_eligible": False,
        "asset_root": str(source),
        "viewer_bundle": {"root": str(source)},
        "scenes": [
            {
                "anonymous_scene_ref": "scene://private-source-reference",
                "viewer_family": "molstar",
                "asset_format": "xyz",
                "operation_allowlist": ["inspect", "rotate", "render", "measure_dihedral"],
                "backend": {
                    "primary": {
                        "path": "assets/private.xyz",
                        "bytes": asset.stat().st_size,
                        "sha256": __import__("hashlib").sha256(asset.read_bytes()).hexdigest(),
                    }
                },
            }
        ],
    }
    registry = make_envelope(
        kind="qagen_private_batch_scene_registry",
        identifier="fixture-registry",
        status="prepared",
        body=registry_body,
    )
    registry_path = tmp_path / "registry.json"
    _write_json(registry_path, registry)
    return run, registry_path


def test_builds_a_pending_bundle_without_staging_and_keeps_truth_private(tmp_path: Path) -> None:
    run, registry = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    result = build_pending_reviewer_bundle(run_root=run, scene_registry_path=registry, output_dir=bundle)

    assert result["status"] == "built_pending_human_review"
    checked = verify_pending_reviewer_bundle(bundle)
    assert checked["task_id"] == "pending-run-001"
    public = (bundle / "public" / "task.json").read_text(encoding="utf-8")
    assert "reference_answer" not in public
    assert "dihedral_angle_deg" not in public
    assert stat.S_IMODE((bundle / "private").stat().st_mode) == 0o700
    assert stat.S_IMODE((bundle / "private" / "agent-artifacts" / "truth-builder.json").stat().st_mode) == 0o600
    assert "answer" in (bundle / "private" / "agent-artifacts" / "truth-builder.json").read_text(encoding="utf-8")


def test_loopback_server_exposes_no_private_routes(tmp_path: Path) -> None:
    run, registry = _fixture(tmp_path)
    bundle = tmp_path / "bundle"
    build_pending_reviewer_bundle(run_root=run, scene_registry_path=registry, output_dir=bundle)
    server = create_pending_reviewer_bundle_server(bundle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://{server.server_address[0]}:{server.server_address[1]}"
        assert b"ready" in urlopen(origin + "/healthz", timeout=5).read()
        assert b"pending-run-001" in urlopen(origin + "/api/task", timeout=5).read()
        with pytest.raises(HTTPError) as exc:
            urlopen(origin + "/private/agent-artifacts/truth-builder.json", timeout=5).read()
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_rejects_any_staged_or_releaseable_input(tmp_path: Path) -> None:
    run, registry = _fixture(tmp_path)
    state_path = run / "state.json"
    state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    body = dict(state["body"])
    body["staging_enabled"] = True
    _write_json(
        state_path,
        make_envelope(
            kind="qagen_v32_text_only_execution_state",
            identifier=state["id"],
            status="verification_complete_pending_human_review",
            body=body,
            created_at=state["created_at"],
        ),
    )
    with pytest.raises(PendingReviewerBundleError, match="pending-review boundary"):
        build_pending_reviewer_bundle(run_root=run, scene_registry_path=registry, output_dir=tmp_path / "bundle")
