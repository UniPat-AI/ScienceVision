from __future__ import annotations

import ast
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import stat
from copy import deepcopy
from types import SimpleNamespace

from PIL import Image
import pytest
import yaml

from qagen.chirality_pilot import build_chirality_pilot_viewer_profile
from qagen.errors import QuarantinedError, StaleInputError
from qagen.contracts import make_envelope
from qagen.paths import WorkspacePaths
from qagen.seeds import registry as seed_registry
from qagen.storage.atomic import atomic_write_json, read_json
from qagen.viewer import (
    AdapterSession,
    HostSceneBinding,
    MolstarChiralityHostAdapter,
    SceneRegistry,
    ViewerAdapterError,
    ViewerProfileError,
    build_chirality_scene_registry,
    diagnose_chirality_host,
    load_viewer_profile,
    persist_chirality_smoke_receipt,
    revalidate_viewer_profile,
    verify_viewer_evidence_chain,
)

from conftest import PACKAGE_ROOT


_DIGEST = "sha256:" + "a" * 64
_SOURCE_ENV = "QAGEN_EV2_SOURCE_ROOT"


_CAMERA = {
    "position": [0.0, 0.0, 10.0],
    "target": [0.0, 0.0, 0.0],
    "up": [0.0, 1.0, 0.0],
    "mode": "perspective",
    "fov_radians": 0.785,
    "distance": 10.0,
}


class _FakeRegistry:
    anonymous_scene_refs = ("scene_fixture",)
    content_digest = _DIGEST

    def __init__(self, root: Path, *, path: Path | None = None) -> None:
        self.root = root
        self.path = path or root / "registry.json"
        self.resolution = SimpleNamespace(resolved=True)

    def resolve(self, anonymous_scene_ref: str) -> HostSceneBinding:
        if anonymous_scene_ref != "scene_fixture":
            raise ViewerAdapterError("anonymous scene reference is not registered")
        return HostSceneBinding(
            anonymous_scene_ref=anonymous_scene_ref,
            backend_arguments={
                "package_root": self.root,
                "package_path": "asset.mol",
                "format": "mol",
                "asset_sha256": _DIGEST,
                "asset_size": 1,
                "asset_bytes": b"x",
                "registry_digest": _DIGEST,
            },
        )


class _FakePage:
    def __init__(self, png: bytes) -> None:
        self.png = png

    def evaluate(self, expression: str, arguments: object | None = None) -> object:
        if arguments is not None:
            return None
        return {
            "png_base64": base64.b64encode(self.png).decode("ascii"),
            "camera": _CAMERA,
        }


def _png_bytes(
    *,
    size: tuple[int, int] = (64, 64),
    transparent: bool = False,
    varied: bool = True,
    compress_level: int = 6,
) -> bytes:
    color = (255, 255, 255, 0 if transparent else 255)
    image = Image.new("RGBA", size, color)
    if varied and not transparent and size[0] > 1:
        image.paste((16, 80, 160, 255), (0, 0, size[0] // 2, size[1]))
    output = BytesIO()
    image.save(output, format="PNG", compress_level=compress_level)
    return output.getvalue()


def _capture_png(tmp_path: Path, png: bytes) -> bytes:
    package_root = tmp_path / "package"
    package_root.mkdir(exist_ok=True)
    adapter = MolstarChiralityHostAdapter(
        WorkspacePaths.from_value(tmp_path / "workspace"),
        _FakeRegistry(package_root),  # type: ignore[arg-type]
    )
    captured, _ = adapter._capture(SimpleNamespace(page=_FakePage(png)))  # noqa: SLF001
    return captured


def _build_fake_operational_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[WorkspacePaths, dict[str, object], Path]:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    package_root = tmp_path / "package"
    shared_root = package_root / "viewer" / "shared"
    shared_root.mkdir(parents=True)
    (package_root / "viewer" / "molstar.html").write_text("<!doctype html><title>host</title>\n", encoding="utf-8")
    host_script = shared_root / "host.js"
    host_script.write_text("window.__qagen_host = true;\n", encoding="utf-8")
    registry_path = paths.private / "viewer" / "scene-registries" / "registry_fixture.json"
    atomic_write_json(registry_path, {"test_fixture": True}, private=True)
    registry = _FakeRegistry(package_root, path=registry_path)
    monkeypatch.setattr(SceneRegistry, "load", classmethod(lambda _cls, _paths, _path: registry))
    result = build_chirality_pilot_viewer_profile(paths, registry_path=registry_path)
    return paths, result, host_script


def _private_package() -> Path:
    raw = os.environ.get(_SOURCE_ENV)
    if not raw:
        pytest.skip(f"{_SOURCE_ENV} is not configured for the private pilot")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        pytest.skip(f"{_SOURCE_ENV} does not name an available private package")
    return root


def _select_private_seed(package_root: Path, *, verdict: str) -> tuple[str, tuple[str, ...]]:
    """Select a test seed by approved metadata, never by a committed source ID."""
    source_spec = yaml.safe_load(
        (PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(source_spec, dict)
    task_path = package_root / source_spec["file_roles"]["tasks"]["path"]
    candidates: list[dict[str, object]] = []
    for line in task_path.read_text(encoding="utf-8").splitlines():
        task = json.loads(line)
        if not isinstance(task, dict):
            continue
        subdomain = task.get("sub_domain")
        review = task.get("expert_review")
        asset = task.get("asset")
        if (
            task.get("domain") == "chemistry"
            and isinstance(subdomain, dict)
            and subdomain.get("slug") == "chirality"
            and isinstance(review, dict)
            and review.get("verdict_class") == verdict
            and isinstance(asset, dict)
            and asset.get("viewer") == "molstar"
            and asset.get("format") == "mol"
            and isinstance(task.get("task_id"), str)
        ):
            candidates.append(task)
    assert len(candidates) == 1, "private host fixture must expose one metadata-selected chirality seed"
    selected = candidates[0]
    asset = selected["asset"]
    assert isinstance(asset, dict)
    markers = tuple(
        str(value)
        for value in (
            selected["task_id"],
            asset.get("package_path"),
            asset.get("repo_path"),
        )
        if isinstance(value, str)
    )
    return str(selected["task_id"]), markers


def test_host_proxy_private_seed_selection_has_no_committed_private_mapping() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    literals = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert "_select_private_seed" in source
    private_task_prefix = "s" + "v-"
    assert not any(value.startswith(private_task_prefix) for value in literals)
    assert not any(value[:1].isdigit() and value.endswith(".mol") for value in literals)


def _prepare_private_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    verdict: str = "verified",
) -> tuple[WorkspacePaths, dict[str, object]]:
    package_root = _private_package()
    seed_task_id, private_markers = _select_private_seed(package_root, verdict=verdict)
    monkeypatch.setenv(_SOURCE_ENV, str(package_root))
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    pending = seed_registry.sync_seed_package(
        paths,
        package_root,
        source_spec_path=PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml",
    )
    assert pending["eligible"] is False
    seed_registry.accept_source_version(
        paths,
        request_path=pending["request_path"],
        reviewer="source-owner",
        resolved_version="1.0.3",
        decision="accept",
        rationale="The immutable reviewed v1.0.3 package directory and manifest are authoritative for this pilot.",
    )
    accepted = seed_registry.sync_seed_package(
        paths,
        package_root,
        source_spec_path=PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml",
    )
    assert accepted["eligible"] is True
    result = build_chirality_scene_registry(
        paths,
        package_root=package_root,
        source_spec_path=PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml",
        seed_task_id=seed_task_id,
    )
    return paths, {**result, "private_markers": private_markers}


def test_host_profile_exposes_only_rotate_and_render_and_rejects_raw_selectors(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    adapter = MolstarChiralityHostAdapter(WorkspacePaths.from_value(tmp_path / "workspace"), _FakeRegistry(root))  # type: ignore[arg-type]

    profile = adapter.profile()
    assert profile.available_operations == frozenset({"rotate", "render"})
    assert profile.native_measurement_operations == frozenset()
    assert profile.accepts_solver_asset_identifiers is False
    assert profile.exposes_raw_asset_metadata is False
    assert profile.network_access is False

    with pytest.raises(ViewerAdapterError, match="unsupported or missing fields"):
        adapter.open_session(
            anonymous_scene_ref="scene_fixture",
            request={
                "request_id": "request-1",
                "viewer_profile_digest": _DIGEST,
                "allowed_operations": ["render"],
                "qid": "private-task",
            },
        )


def test_capture_accepts_a_valid_nonblank_png(tmp_path: Path) -> None:
    png = _png_bytes()

    assert _capture_png(tmp_path, png) == png


@pytest.mark.parametrize(
    ("png", "message"),
    [
        (b"\x89PNG\r\n\x1a\nnot-an-image", "complete PNG"),
        (_png_bytes() + b"trailing-junk", "complete PNG"),
        (_png_bytes(size=(1, 1), transparent=True), "dimensions"),
        (_png_bytes(transparent=True), "fully transparent"),
        (_png_bytes(varied=False), "pixel variation"),
    ],
    ids=("signature-only", "trailing-junk", "one-pixel", "transparent", "monochrome"),
)
def test_capture_rejects_invalid_or_blank_pngs(tmp_path: Path, png: bytes, message: str) -> None:
    with pytest.raises(ViewerAdapterError, match=message):
        _capture_png(tmp_path, png)


def test_rotate_rejects_an_unchanged_render(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    adapter = MolstarChiralityHostAdapter(paths, _FakeRegistry(package_root))  # type: ignore[arg-type]
    session = AdapterSession(
        session_id="vsess_unchanged_render",
        request_id="request-unchanged-render",
        viewer_profile_digest=_DIGEST,
        allowed_operations=frozenset({"render", "rotate"}),
    )
    directory = paths.private / "viewer" / "sessions" / session.session_id
    directory.mkdir(parents=True)
    host = SimpleNamespace(
        public=session,
        page=_FakePage(_png_bytes()),
        directory=directory,
        attestation_digest=_DIGEST,
        sequence=0,
        previous_receipt_digest=None,
        previous_visible_rgb_digest=None,
    )
    adapter._sessions[session.session_id] = host  # noqa: SLF001

    initial = adapter.perform(session=session, operation="render", arguments={})
    assert initial.sequence == 0
    with pytest.raises(ViewerAdapterError, match="did not produce changed rendered content"):
        adapter.perform(
            session=session,
            operation="rotate",
            arguments={"d_azimuth_deg": 30, "d_elevation_deg": 12},
        )
    assert host.sequence == 1
    assert not (directory / "render-000001.png").exists()


def test_rotate_rejects_reencoded_png_with_unchanged_visible_pixels(tmp_path: Path) -> None:
    initial_png = _png_bytes(compress_level=0)
    reencoded_png = _png_bytes(compress_level=9)
    assert initial_png != reencoded_png

    package_root = tmp_path / "package"
    package_root.mkdir()
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    adapter = MolstarChiralityHostAdapter(paths, _FakeRegistry(package_root))  # type: ignore[arg-type]
    session = AdapterSession(
        session_id="vsess_reencoded_render",
        request_id="request-reencoded-render",
        viewer_profile_digest=_DIGEST,
        allowed_operations=frozenset({"render", "rotate"}),
    )
    directory = paths.private / "viewer" / "sessions" / session.session_id
    directory.mkdir(parents=True)
    page = _FakePage(initial_png)
    host = SimpleNamespace(
        public=session,
        page=page,
        directory=directory,
        attestation_digest=_DIGEST,
        sequence=0,
        previous_receipt_digest=None,
        previous_visible_rgb_digest=None,
    )
    adapter._sessions[session.session_id] = host  # noqa: SLF001

    adapter.perform(session=session, operation="render", arguments={})
    page.png = reencoded_png
    with pytest.raises(ViewerAdapterError, match="did not produce changed rendered content"):
        adapter.perform(
            session=session,
            operation="rotate",
            arguments={"d_azimuth_deg": 30, "d_elevation_deg": 12},
        )
    assert host.sequence == 1
    assert not (directory / "render-000001.png").exists()


def test_operational_viewer_profile_persists_private_host_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, result, _ = _build_fake_operational_profile(tmp_path, monkeypatch)
    profile_path = Path(str(result["viewer_profile_path"]))
    profile = load_viewer_profile(paths, profile_path)
    body = profile["body"]

    assert profile["status"] == "attested_local_only"
    assert body["local_fixture"] is False
    assert body["production_eligible"] is False
    assert body["allowed_operations"] == ["render", "rotate"]
    assert body["scene_registry_digest"] == _DIGEST
    assert revalidate_viewer_profile(profile)["content_digest"] == profile["content_digest"]
    for artifact in (
        profile_path,
        Path(str(result["definition_path"])),
        Path(str(result["session_evidence_path"])),
        Path(str(result["host_build_path"])),
        Path(str(result["session_evidence_schema_path"])),
    ):
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "artifact_key",
    ["host_build_path", "session_evidence_schema_path"],
)
def test_operational_viewer_profile_rejects_tampered_host_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_key: str,
) -> None:
    paths, result, _ = _build_fake_operational_profile(tmp_path, monkeypatch)
    profile = load_viewer_profile(paths, result["viewer_profile_path"])
    artifact_path = Path(str(result[artifact_key]))
    artifact = read_json(artifact_path)
    artifact["status"] = "tampered"
    atomic_write_json(artifact_path, artifact, private=True)

    with pytest.raises(StaleInputError, match="scene-registry lock changed"):
        revalidate_viewer_profile(profile)


def test_operational_viewer_profile_rejects_tampered_profile_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, result, _ = _build_fake_operational_profile(tmp_path, monkeypatch)
    profile_path = Path(str(result["viewer_profile_path"]))
    profile = read_json(profile_path)
    profile["status"] = "fixture_only"
    atomic_write_json(profile_path, profile, private=True)

    with pytest.raises(ViewerProfileError, match="status does not match"):
        load_viewer_profile(paths, profile_path)


def test_operational_viewer_profile_rejects_host_build_manifest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, result, host_script = _build_fake_operational_profile(tmp_path, monkeypatch)
    profile = load_viewer_profile(paths, result["viewer_profile_path"])
    host_script.write_text("window.__qagen_host = false;\n", encoding="utf-8")

    with pytest.raises(StaleInputError, match="host build lock changed"):
        revalidate_viewer_profile(profile)


@pytest.mark.parametrize(
    "artifact_key",
    [
        "viewer_profile_path",
        "definition_path",
        "session_evidence_path",
        "host_build_path",
        "session_evidence_schema_path",
    ],
)
def test_operational_viewer_profile_rejects_broad_artifact_permissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_key: str,
) -> None:
    paths, result, _ = _build_fake_operational_profile(tmp_path, monkeypatch)
    profile_path = Path(str(result["viewer_profile_path"]))
    profile = load_viewer_profile(paths, profile_path)
    artifact_path = Path(str(result[artifact_key]))
    artifact_path.chmod(0o644)

    if artifact_key == "viewer_profile_path":
        with pytest.raises(ViewerProfileError, match="permissions must be 0600"):
            load_viewer_profile(paths, profile_path)
    else:
        with pytest.raises(StaleInputError, match="permissions must be 0600"):
            revalidate_viewer_profile(profile)


def test_bound_host_revalidates_profile_before_opening_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, result, host_script = _build_fake_operational_profile(tmp_path, monkeypatch)
    profile_path = Path(str(result["viewer_profile_path"]))
    profile = load_viewer_profile(paths, profile_path)
    adapter = MolstarChiralityHostAdapter.from_profile_path(paths, profile_path)
    host_script.write_text("window.__qagen_host = false;\n", encoding="utf-8")
    try:
        with pytest.raises(ViewerAdapterError, match="cannot be revalidated"):
            adapter.open_session(
                anonymous_scene_ref="scene_fixture",
                request={
                    "request_id": "request-1",
                    "viewer_profile_digest": profile["content_digest"],
                    "allowed_operations": ["render", "rotate"],
                },
            )
    finally:
        adapter.shutdown()


def test_scene_registry_rejects_tampering_and_broad_permissions(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry_path = paths.private / "viewer" / "scene-registries" / "registry_fixture.json"
    registry = make_envelope(
        kind="qagen_private_scene_registry",
        identifier="registry-fixture",
        status="pilot_local",
        body={"local_only": True, "release_eligible": False},
    )
    registry["body"]["release_eligible"] = True
    atomic_write_json(registry_path, registry, private=True)
    with pytest.raises(ViewerAdapterError, match="failed attestation"):
        SceneRegistry.load(paths, registry_path)

    registry_path.chmod(0o644)
    with pytest.raises(ViewerAdapterError, match="permissions are too broad"):
        SceneRegistry.load(paths, registry_path)


def test_private_registry_rejects_factual_error_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(QuarantinedError, match="verified expert verdict"):
        _prepare_private_registry(tmp_path, monkeypatch, verdict="factual_error")


def test_registry_without_canonical_resolution_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = _private_package()
    monkeypatch.setenv(_SOURCE_ENV, str(package_root))
    seed_task_id, _private_markers = _select_private_seed(package_root, verdict="verified")
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    prepared = build_chirality_scene_registry(
        paths,
        package_root=package_root,
        source_spec_path=PACKAGE_ROOT / "configs" / "sources" / "ev2_seed_source_spec.yaml",
        seed_task_id=seed_task_id,
    )

    assert prepared["status"] == "blocked"
    assert prepared["runtime_eligible"] is False
    assert prepared["version_resolution"]["status"] == "blocked"
    assert prepared["blockers"] == ["source_version_seed_index_missing"]
    diagnostic = diagnose_chirality_host(paths, prepared["registry_path"])
    assert diagnostic["runtime_ready"] is False
    assert diagnostic["blockers"] == ["source_version_seed_index_missing"]


@pytest.mark.timeout(120)
def test_real_chirality_scene_renders_rotates_and_attests_without_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("playwright.sync_api")
    paths, prepared = _prepare_private_registry(tmp_path, monkeypatch)
    registry_path = Path(str(prepared["registry_path"]))
    assert stat.S_IMODE(registry_path.stat().st_mode) == 0o600
    assert prepared["release_eligible"] is False
    assert prepared["runtime_eligible"] is True
    assert prepared["version_resolution"]["status"] == "resolved"
    assert prepared["version_resolution"]["resolved_version"] == "1.0.3"
    assert prepared["blockers"] == ["source_spec_local_only"]

    diagnostic = diagnose_chirality_host(paths, registry_path)
    assert diagnostic["runtime_ready"] is True
    assert diagnostic["production_ready"] is False
    assert diagnostic["available_operations"] == ["render", "rotate"]

    profile_result = build_chirality_pilot_viewer_profile(paths, registry_path=registry_path)
    profile_path = Path(str(profile_result["viewer_profile_path"]))
    profile = load_viewer_profile(paths, profile_path)
    assert profile["status"] == "attested_local_only"
    assert profile["body"]["scene_registry_digest"] == prepared["registry_digest"]
    assert revalidate_viewer_profile(profile)["content_digest"] == profile["content_digest"]
    for artifact_key in (
        "viewer_profile_path",
        "definition_path",
        "session_evidence_path",
        "host_build_path",
        "session_evidence_schema_path",
    ):
        assert stat.S_IMODE(Path(str(profile_result[artifact_key])).stat().st_mode) == 0o600

    scene_ref = str(prepared["anonymous_scene_ref"])
    adapter = MolstarChiralityHostAdapter.from_profile_path(paths, profile_path)
    session = None
    closed = False
    try:
        session = adapter.open_session(
            anonymous_scene_ref=scene_ref,
            request={
                "request_id": "test-pilot-request",
                "viewer_profile_digest": profile["content_digest"],
                "allowed_operations": ["rotate", "render"],
            },
        )
        initial = adapter.perform(session=session, operation="render", arguments={})
        rotated = adapter.perform(
            session=session,
            operation="rotate",
            arguments={"d_azimuth_deg": 30, "d_elevation_deg": 12},
        )
        adapter.close_session(session=session)
        closed = True

        assert initial.sequence == 0
        assert rotated.sequence == 1
        assert set(initial.payload) == {"render_digest", "camera", "action_receipt"}
        assert set(rotated.payload) == {"render_digest", "camera", "action_receipt"}
        assert initial.payload["render_digest"] != rotated.payload["render_digest"]
        assert initial.payload["camera"] != rotated.payload["camera"]
        assert rotated.payload["action_receipt"]["sequence"] == 1
        public = json.dumps(
            {"session": session.__dict__, "initial": initial.payload, "rotated": rotated.payload},
            sort_keys=True,
            default=list,
        )
        package_root = _private_package()
        for forbidden in (
            str(package_root),
            *prepared["private_markers"],
            "asset_path",
            "qid",
            "metadata",
            "structures",
        ):
            assert forbidden not in public

        verified = verify_viewer_evidence_chain(paths, session.session_id, viewer_profile_path=profile_path)
        assert verified["status"] == "verified"
        assert verified["action_count"] == 2
        smoke = persist_chirality_smoke_receipt(
            paths,
            viewer_profile_path=profile_path,
            session_id=session.session_id,
        )
        assert smoke["receipt"]["status"] == "passed_local_only"
        assert stat.S_IMODE(Path(str(smoke["smoke_receipt_path"])).stat().st_mode) == 0o600

        receipt_path = paths.private / "viewer" / "sessions" / session.session_id / "receipt-000001.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["body"]["camera"]["distance"] += 1
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(ViewerAdapterError, match="failed verification"):
            verify_viewer_evidence_chain(paths, session.session_id)
    finally:
        if session is not None and not closed:
            adapter.close_session(session=session)
        adapter.shutdown()


def test_registry_blocks_when_canonical_resolution_receipt_is_tampered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, prepared = _prepare_private_registry(tmp_path, monkeypatch)
    receipt_path = next((paths.receipts / "source").glob("*.resolution.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["body"]["resolved_version"] = "1.0.2"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    registry = SceneRegistry.load(paths, prepared["registry_path"])
    assert registry.resolution.resolved is False
    assert registry.resolution.blockers == ("source_version_resolution_invalid",)
    diagnostic = diagnose_chirality_host(paths, prepared["registry_path"])
    assert diagnostic["runtime_ready"] is False
    assert diagnostic["blockers"] == ["source_version_resolution_invalid"]


def test_registry_blocks_when_current_seed_index_is_not_the_frozen_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, prepared = _prepare_private_registry(tmp_path, monkeypatch)
    current_path = paths.catalog / "current.json"
    current = read_json(current_path)
    changed_body = deepcopy(current["body"])
    changed_body["entries"] = list(reversed(changed_body["entries"]))
    replacement = make_envelope(
        kind="seed_index",
        identifier="seed-index-noncanonical-test",
        status="eligible",
        body=changed_body,
    )
    atomic_write_json(current_path, replacement, private=True)

    registry = SceneRegistry.load(paths, prepared["registry_path"])
    assert registry.resolution.resolved is False
    assert registry.resolution.blockers == ("source_version_seed_index_not_canonical",)


def test_registry_blocks_when_frozen_seed_index_is_tampered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, prepared = _prepare_private_registry(tmp_path, monkeypatch)
    current = read_json(paths.catalog / "current.json")
    frozen_path = paths.catalog / f"{current['content_digest'].split(':', 1)[1][:16]}.seed-index.json"
    frozen = read_json(frozen_path)
    frozen["body"]["eligible"] = False
    atomic_write_json(frozen_path, frozen, private=True)

    registry = SceneRegistry.load(paths, prepared["registry_path"])
    assert registry.resolution.resolved is False
    assert registry.resolution.blockers == ("source_version_seed_index_not_canonical",)


def test_registry_blocks_when_resolution_request_is_tampered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, prepared = _prepare_private_registry(tmp_path, monkeypatch)
    request_path = next((paths.receipts / "source").glob("*.request.json"))
    request = read_json(request_path)
    request["body"]["acceptable_versions"] = ["1.0.2"]
    atomic_write_json(request_path, request, private=True)

    registry = SceneRegistry.load(paths, prepared["registry_path"])
    assert registry.resolution.resolved is False
    assert registry.resolution.blockers == ("source_version_request_invalid",)


def test_registry_blocks_validly_signed_resolution_lock_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, prepared = _prepare_private_registry(tmp_path, monkeypatch)
    original_receipt = read_json(next((paths.receipts / "source").glob("*.resolution.json")))
    original_index = read_json(paths.catalog / "current.json")
    mutations = (
        ("resolved_version", "1.0.2"),
        ("inventory_digest", "sha256:" + "0" * 64),
        ("source_spec_digest", "sha256:" + "1" * 64),
        ("observed_versions", {**original_receipt["body"]["observed_versions"], "directory": "v0.0.0"}),
    )

    for index, (field, value) in enumerate(mutations):
        receipt_body = deepcopy(original_receipt["body"])
        receipt_body[field] = value
        replacement_receipt = make_envelope(
            kind="source_version_resolution_receipt",
            identifier=f"source-resolution-drift-{index}",
            status="accept",
            body=receipt_body,
        )
        atomic_write_json(
            paths.receipts / "source" / f"drift-{index}.resolution.json",
            replacement_receipt,
            private=True,
        )
        index_body = deepcopy(original_index["body"])
        index_body["resolution_ref"] = replacement_receipt["content_digest"]
        replacement_index = make_envelope(
            kind="seed_index",
            identifier=f"seed-index-drift-{index}",
            status="eligible",
            body=index_body,
        )
        frozen_path = paths.catalog / (
            f"{replacement_index['content_digest'].split(':', 1)[1][:16]}.seed-index.json"
        )
        atomic_write_json(frozen_path, replacement_index, private=True)
        atomic_write_json(paths.catalog / "current.json", replacement_index, private=True)

        registry = SceneRegistry.load(paths, prepared["registry_path"])
        assert registry.resolution.resolved is False
        assert registry.resolution.blockers == ("source_version_resolution_lock_mismatch",)
