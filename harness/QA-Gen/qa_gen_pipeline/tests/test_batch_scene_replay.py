from __future__ import annotations

import json
from pathlib import Path

import pytest

import qagen.viewer.batch_scene_host as host_module
import qagen.viewer.batch_scene_replay as replay_module

from qagen.contracts import digest, digest_bytes
from qagen.paths import WorkspacePaths
from qagen.storage.atomic import read_json
from qagen.viewer.batch_scene_host import (
    BatchReplayScene,
    BatchSceneReplayError as HostReplayError,
    _BatchLoopbackAssetServer,
    ControlledBatchViewerReplayHost,
)
from qagen.viewer.batch_scene_replay import (
    BatchSceneReplayError,
    _replay_result,
    persist_batch_replay_summary,
    persist_batch_scene_viewer_lock_set,
    persist_slot_replay_receipt,
    persist_slot_replay_retry_record,
    seal_batch_replay_summary_after_retries,
)


def _scene(*, primary_format: str = "nii.gz") -> BatchReplayScene:
    primary = b"private-primary-scene"
    overlay = b"private-overlay-scene"
    return BatchReplayScene(
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        viewer_family="niivue",
        primary_bytes=primary,
        primary_digest=digest_bytes(primary),
        primary_format=primary_format,
        overlay_bytes=overlay,
        overlay_digest=digest_bytes(overlay),
    )


def _write_pinned_viewer_bundle(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    root.mkdir(parents=True)
    files = {
        "viewer/molstar.html": b"<html>molstar</html>\n",
        "viewer/niivue.html": b"<html>niivue</html>\n",
        "viewer/shared/viewer-mcp-api.js": b"export const api = true;\n",
        "viewer/shared/molstar-mcp-adapter.js": b"export const molstar = true;\n",
        "viewer/shared/niivue-mcp-adapter.js": b"export const niivue = true;\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    viewer_files = {
        "package_id": root.name,
        "files": [
            {
                "relative_path": relative,
                "sha256_shipped": digest_bytes(content).removeprefix("sha256:"),
            }
            for relative, content in sorted(files.items())
        ],
    }
    provenance_path = root / "provenance" / "viewer-files.json"
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps(viewer_files, sort_keys=True), encoding="utf-8"
    )
    manifest_path = root / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps({"package_id": root.name}, sort_keys=True), encoding="utf-8"
    )
    checksums = [
        f"{digest_bytes(content).removeprefix('sha256:')}  {relative}"
        for relative, content in sorted(files.items())
    ]
    (root / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    monkeypatch.setattr(replay_module, "_TRUSTED_V32_VIEWER_BUNDLE_ROOT", root)
    monkeypatch.setattr(
        replay_module, "_TRUSTED_V32_VIEWER_BUNDLE_PACKAGE_ID", root.name
    )
    monkeypatch.setattr(
        replay_module,
        "_TRUSTED_V32_VIEWER_BUNDLE_MANIFEST_DIGEST",
        digest_bytes(manifest_path.read_bytes()),
    )


def test_viewer_bundle_provenance_is_pinned_and_revalidated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "scale-v3.2-bcm522-question-assets-bundle-test-v1.2.1"
    _write_pinned_viewer_bundle(monkeypatch, root)

    binding = replay_module.inspect_viewer_bundle_provenance(root)

    assert binding["root"] == str(root)
    assert binding["package_id"] == root.name
    assert binding["manifest_digest"] == digest_bytes(
        (root / "MANIFEST.json").read_bytes()
    )
    assert [item["path"] for item in binding["required_files"]] == list(
        replay_module._REQUIRED_BUNDLE_VIEWER_FILES
    )
    assert replay_module.validate_viewer_bundle_provenance(binding) == binding

    (root / "viewer/shared/molstar-mcp-adapter.js").write_text(
        "tampered\n", encoding="utf-8"
    )
    with pytest.raises(BatchSceneReplayError, match="asset_integrity_mismatch"):
        replay_module.validate_viewer_bundle_provenance(binding)


def test_viewer_bundle_provenance_rejects_another_absolute_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = tmp_path / "scale-v3.2-bcm522-question-assets-bundle-test-v1.2.1"
    other = tmp_path / "another-bundle"
    _write_pinned_viewer_bundle(monkeypatch, trusted)
    _write_pinned_viewer_bundle(monkeypatch, other)
    monkeypatch.setattr(replay_module, "_TRUSTED_V32_VIEWER_BUNDLE_ROOT", trusted)
    monkeypatch.setattr(
        replay_module, "_TRUSTED_V32_VIEWER_BUNDLE_PACKAGE_ID", trusted.name
    )
    monkeypatch.setattr(
        replay_module,
        "_TRUSTED_V32_VIEWER_BUNDLE_MANIFEST_DIGEST",
        digest_bytes((trusted / "MANIFEST.json").read_bytes()),
    )

    with pytest.raises(BatchSceneReplayError, match="host_contract_invalid"):
        replay_module.inspect_viewer_bundle_provenance(other)


def test_niivue_scene_urls_keep_the_format_suffix(tmp_path: Path) -> None:
    server = _BatchLoopbackAssetServer(tmp_path)
    try:
        scene = _scene()
        token = server.register(scene)

        primary, primary_type = server._scene_file(
            f"/__qagen_scene/{token}/primary.nii.gz"
        )
        mask, mask_type = server._scene_file(f"/__qagen_scene/{token}/mask.nii.gz")

        assert primary == b"private-primary-scene"
        assert mask == b"private-overlay-scene"
        assert primary_type == "application/octet-stream"
        assert mask_type == "application/octet-stream"
        with pytest.raises(HostReplayError, match="asset_unavailable"):
            server._scene_file(f"/__qagen_scene/{token}/primary")
    finally:
        server.close()


def test_stable_capture_waits_two_frames_before_accepting_a_scene(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        script = ""

        def evaluate(self, script: str, *_args: object) -> str:
            self.script = script
            return "painted"

    page = Page()
    captured: list[Page] = []

    def capture(value: Page) -> tuple[bytes, str]:
        captured.append(value)
        return (b"validated-private-frame", digest({"frame": "validated"}))

    monkeypatch.setattr(
        ControlledBatchViewerReplayHost, "_capture", staticmethod(capture)
    )

    ControlledBatchViewerReplayHost._await_stable_nonblank_render(page)

    assert page.script.count("requestAnimationFrame") == 2
    assert captured == [page]


def test_explicit_viewer_executable_fails_closed_outside_trusted_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "untrusted-browser"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv(host_module._VIEWER_EXECUTABLE_ENV, str(executable))

    with pytest.raises(HostReplayError, match="viewer_runtime_unavailable"):
        host_module.explicit_browser_installation_identity()
    host = ControlledBatchViewerReplayHost(tmp_path)
    assert host.health()["status"] == "unavailable"


def test_stable_capture_rejects_a_renderer_that_cannot_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        def evaluate(self, _script: str, *_args: object) -> str:
            return "timed_out"

    monkeypatch.setattr(
        ControlledBatchViewerReplayHost,
        "_capture",
        staticmethod(lambda _value: (b"not-used", digest({"frame": "not-used"}))),
    )

    with pytest.raises(HostReplayError, match="viewer_render_invalid"):
        ControlledBatchViewerReplayHost._await_stable_nonblank_render(Page())


def test_stable_capture_maps_unexpected_capture_failure_to_render_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        def evaluate(self, _script: str, *_args: object) -> str:
            return "painted"

    def capture(_value: Page) -> tuple[bytes, str]:
        raise RuntimeError("private browser failure")

    monkeypatch.setattr(
        ControlledBatchViewerReplayHost, "_capture", staticmethod(capture)
    )

    with pytest.raises(HostReplayError, match="viewer_render_invalid"):
        ControlledBatchViewerReplayHost._await_stable_nonblank_render(Page())


def test_molstar_focus_uses_the_native_camera_manager_before_capture() -> None:
    class Page:
        scripts: list[str] = []

        def evaluate(self, script: str, *_args: object) -> object:
            self.scripts.append(script)
            if "focusSphere" in script:
                return {"focused": True}
            return "painted"

    page = Page()

    ControlledBatchViewerReplayHost._molstar_focus_scene(page)

    assert any("focusSphere" in script for script in page.scripts)
    assert sum(script.count("requestAnimationFrame") for script in page.scripts) == 2


def test_molstar_zoom_uses_a_fixed_bounded_camera_transition() -> None:
    class Page:
        scripts: list[str] = []
        arguments: list[object] = []

        def evaluate(self, script: str, *args: object) -> object:
            self.scripts.append(script)
            self.arguments.extend(args)
            if "camera.setState" in script:
                return {"zoomed": True}
            return "painted"

    page = Page()

    assert ControlledBatchViewerReplayHost._molstar_zoom(page) == {"zoomed": True}
    zoom_script = next(script for script in page.scripts if "camera.setState" in script)
    assert "factor" in zoom_script
    assert "minRadiusMultiplier" in zoom_script
    assert "maxRadiusMultiplier" in zoom_script
    assert "camera.setState" in zoom_script
    assert page.arguments[0] == {
        "factor": 0.82,
        "minRadiusMultiplier": 0.25,
        "maxRadiusMultiplier": 20.0,
    }


def test_molstar_zoom_rejects_an_unbounded_or_unavailable_camera() -> None:
    class Page:
        def evaluate(self, script: str, *_args: object) -> object:
            if "camera.setState" in script:
                return {"zoomed": False}
            return "painted"

    with pytest.raises(HostReplayError, match="viewer_operation_failed"):
        ControlledBatchViewerReplayHost._molstar_zoom(Page())


def test_molstar_contrast_fallback_uses_the_registered_preset() -> None:
    class Page:
        scripts: list[str] = []

        def evaluate(self, script: str, *_args: object) -> object:
            self.scripts.append(script)
            if "polymer-and-ligand" in script:
                return {"applied": True}
            return None

    page = Page()

    ControlledBatchViewerReplayHost._molstar_apply_polymer_and_ligand_preset(page)

    assert any(
        "preset-structure-representation-polymer-and-ligand" in script
        for script in page.scripts
    )
    assert sum(script.count("requestAnimationFrame") for script in page.scripts) == 0


def test_xyz_native_dihedral_accepts_only_the_reviewed_private_selector_shape() -> None:
    selector = {
        "type": "atom_quadruplet",
        "atoms": [
            {"structure_index": 0, "atom_index": 1, "format": "xyz"},
            {"structure_index": 0, "atom_index": 2, "format": "xyz"},
            {"structure_index": 0, "atom_index": 4, "format": "xyz"},
            {"structure_index": 0, "atom_index": 6, "format": "xyz"},
        ],
    }

    assert ControlledBatchViewerReplayHost._xyz_dihedral_atom_indices(selector) == (
        0,
        (1, 2, 4, 6),
    )

    unsupported = {
        "type": "atom_quadruplet",
        "atoms": [
            {
                "structure_index": 0,
                "atom_index": 1,
                "format": "xyz",
                "coordinate": [0, 0, 0],
            },
            *selector["atoms"][1:],
        ],
    }
    with pytest.raises(HostReplayError, match="host_contract_invalid"):
        ControlledBatchViewerReplayHost._xyz_dihedral_atom_indices(unsupported)

    mixed_structure = dict(selector)
    mixed_structure["atoms"] = [dict(item) for item in selector["atoms"]]
    mixed_structure["atoms"][3]["structure_index"] = 1
    with pytest.raises(HostReplayError, match="host_contract_invalid"):
        ControlledBatchViewerReplayHost._xyz_dihedral_atom_indices(mixed_structure)


def test_xyz_native_dihedral_calls_the_shipped_viewer_measurement_surface() -> None:
    selector = {
        "type": "atom_quadruplet",
        "atoms": [
            {"structure_index": 0, "atom_index": 1, "format": "xyz"},
            {"structure_index": 0, "atom_index": 2, "format": "xyz"},
            {"structure_index": 0, "atom_index": 4, "format": "xyz"},
            {"structure_index": 0, "atom_index": 6, "format": "xyz"},
        ],
    }

    class Page:
        script = ""
        arguments: object | None = None

        def evaluate(self, script: str, arguments: object) -> dict[str, object]:
            self.script = script
            self.arguments = arguments
            return {
                "annotation_created": True,
                "value_deg": -61.2,
                "display_resolution_deg": 0.1,
            }

    page = Page()
    result = ControlledBatchViewerReplayHost._molstar_measure_xyz_dihedral(
        page, selector
    )

    assert "ScienceVisionMolstarMeasurementMath" in page.script
    assert "manager.addDihedral" in page.script
    assert "expectedElement = atomIndex - 1" in page.script
    assert page.arguments == {
        "structureIndex": 0,
        "atomIndices": [1, 2, 4, 6],
        "displayResolutionDeg": 0.1,
    }
    assert result == {"value": -61.2, "unit": "degree", "absolute_tolerance": 0.1}


def test_xyz_native_dihedral_session_digest_binds_each_loopback_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector = {
        "type": "atom_quadruplet",
        "atoms": [
            {"structure_index": 0, "atom_index": 1, "format": "xyz"},
            {"structure_index": 0, "atom_index": 2, "format": "xyz"},
            {"structure_index": 0, "atom_index": 4, "format": "xyz"},
            {"structure_index": 0, "atom_index": 6, "format": "xyz"},
        ],
    }
    asset = b"6\nfixture\nC 0 0 0\nC 1 0 0\nC 1 1 0\nC 2 1 1\nC 3 1 1\nC 3 2 1\n"
    scene = BatchReplayScene(
        anonymous_scene_ref="scene://batch-native-dihedral-session",
        viewer_family="molstar",
        primary_bytes=asset,
        primary_digest=digest_bytes(asset),
        primary_format="xyz",
    )

    class Page:
        def set_default_timeout(self, _timeout: int) -> None:
            return None

    class Context:
        pages = [Page()]

        def close(self) -> None:
            return None

    class Server:
        def unregister(self, _token: str) -> None:
            return None

    tokens = iter(("first-loopback-secret", "second-loopback-secret"))
    host = object.__new__(ControlledBatchViewerReplayHost)
    host._server = Server()
    monkeypatch.setattr(
        ControlledBatchViewerReplayHost,
        "_open_scene",
        lambda _self, _scene, *, timeout_seconds: (Context(), next(tokens)),
    )
    monkeypatch.setattr(
        ControlledBatchViewerReplayHost,
        "_molstar_measure_xyz_dihedral",
        staticmethod(
            lambda _page, _selector: {
                "value": -61.2,
                "unit": "degree",
                "absolute_tolerance": 0.1,
            }
        ),
    )
    monkeypatch.setattr(
        ControlledBatchViewerReplayHost,
        "_await_animation_frames",
        staticmethod(lambda _page: None),
    )
    monkeypatch.setattr(
        ControlledBatchViewerReplayHost,
        "_capture",
        staticmethod(lambda _page: (b"fixture-frame", digest_bytes(b"fixture-frame"))),
    )
    monkeypatch.setattr(
        ControlledBatchViewerReplayHost,
        "health",
        lambda _self: {"status": "ready", "runtime": "fixture"},
    )

    first = host.native_measure_xyz_dihedral(scene, selector=selector)
    second = host.native_measure_xyz_dihedral(scene, selector=selector)

    assert first["viewer_session_digest"] != second["viewer_session_digest"]
    assert "first-loopback-secret" not in str(first)
    assert "second-loopback-secret" not in str(second)


def test_isolated_operation_repair_stops_after_the_first_failed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene_ref = "scene://batch-test-anonymous-ref"

    class Registry:
        @staticmethod
        def _scene(_scene_ref: str) -> dict[str, object]:
            assert _scene_ref == scene_ref
            return {"operation_allowlist": ["inspect", "render"]}

    calls: list[tuple[str, ...] | None] = []

    def replay_once(
        *_args: object, operations: tuple[str, ...] | None = None, **_kwargs: object
    ) -> dict[str, object]:
        calls.append(operations)
        return {
            "status": "blocked",
            "host_id": "fixture-host",
            "replayed_operations": [],
            "evidence": [],
            "blockers": ["viewer_operation_failed"],
        }

    monkeypatch.setattr(replay_module, "_replay_scene_with_fresh_browser", replay_once)

    result = replay_module._replay_scene_with_fresh_host_per_operation(
        Registry(),  # type: ignore[arg-type]
        anonymous_scene_ref=scene_ref,
        viewer_package_root="/private/fixture",
        timeout_seconds=5.0,
    )

    assert calls == [("inspect",)]
    assert result["status"] == "blocked"
    assert result["blockers"] == ["viewer_operation_failed"]


class _ReceiptRegistry:
    def __init__(
        self, paths: WorkspacePaths, *, viewer_bundle: dict[str, object] | None = None
    ) -> None:
        self.paths = paths
        self.content_digest = digest({"registry": "fixture"})
        self.selection_batch_digest = digest({"batch": "fixture"})
        self._viewer_bundle = viewer_bundle
        self._scene_value = {
            "slot_digest": digest({"slot": "fixture"}),
            "anonymous_scene_ref": "scene://batch-test-anonymous-ref",
            "asset_set_digest": digest({"asset-set": "fixture"}),
            "viewer_family": "molstar",
            "operation_allowlist": ["render"],
        }
        self.envelope = {"body": {"scenes": [dict(self._scene_value)]}}

    def _scene(self, anonymous_scene_ref: str) -> dict[str, object]:
        assert anonymous_scene_ref == self._scene_value["anonymous_scene_ref"]
        return dict(self._scene_value)

    @property
    def viewer_bundle(self) -> dict[str, object]:
        assert self._viewer_bundle is not None
        return dict(self._viewer_bundle)


def _fixture_viewer_bundle(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Keep receipt tests independent from the machine-local external bundle."""

    binding: dict[str, object] = {
        "bundle_version": 1,
        "root": "/private/qagen-test-viewer-bundle",
        "package_id": "qagen-test-viewer-bundle",
        "manifest_digest": digest({"manifest": "fixture"}),
        "viewer_provenance_digest": digest({"provenance": "fixture"}),
        "required_files": [
            {
                "path": "viewer/molstar.html",
                "content_digest": digest({"file": "molstar"}),
            },
            {
                "path": "viewer/niivue.html",
                "content_digest": digest({"file": "niivue"}),
            },
            {
                "path": "viewer/shared/viewer-mcp-api.js",
                "content_digest": digest({"file": "api"}),
            },
            {
                "path": "viewer/shared/molstar-mcp-adapter.js",
                "content_digest": digest({"file": "molstar-adapter"}),
            },
            {
                "path": "viewer/shared/niivue-mcp-adapter.js",
                "content_digest": digest({"file": "niivue-adapter"}),
            },
        ],
    }

    def validate(value: object) -> dict[str, object]:
        if value != binding:
            raise BatchSceneReplayError("asset_integrity_mismatch")
        return dict(binding)

    monkeypatch.setattr(replay_module, "validate_viewer_bundle_provenance", validate)
    return binding


def test_persisted_receipt_keeps_only_digests_and_fixed_evidence_fields(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry = _ReceiptRegistry(paths)
    replay = {
        "status": "attested",
        "host_id": "fixture-host",
        "replayed_operations": ["render"],
        "evidence": [
            {
                "operation": "render",
                "result_digest": digest({"private": "result"}),
                "render_digest": digest({"private": "frame"}),
            }
        ],
        "blockers": [],
    }

    persisted = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay=replay,
    )
    receipt = read_json(Path(persisted["path"]))
    body = receipt["body"]

    assert set(body["evidence"][0]) == {"operation", "result_digest", "render_digest"}
    serialized = json.dumps(receipt, sort_keys=True)
    for forbidden in (
        "asset_path",
        "primary_asset",
        "mask_asset",
        "task_id",
        "reference_answer",
        "private-primary-scene",
    ):
        assert forbidden not in serialized


def test_replay_result_rejects_private_evidence_fields() -> None:
    scene = {"operation_allowlist": ["render"]}
    replay = {
        "status": "attested",
        "host_id": "fixture-host",
        "replayed_operations": ["render"],
        "evidence": [
            {
                "operation": "render",
                "result_digest": digest({"private": "result"}),
                "render_digest": digest({"private": "frame"}),
                "asset_path": "/private/asset.cif",
            }
        ],
        "blockers": [],
    }

    with pytest.raises(BatchSceneReplayError, match="host_contract_invalid"):
        _replay_result(scene, replay)


def test_stable_capture_retry_is_a_separate_private_record(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry = _ReceiptRegistry(paths)
    old = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay={
            "status": "blocked",
            "host_id": "fixture-host",
            "replayed_operations": [],
            "evidence": [],
            "blockers": ["viewer_render_invalid"],
        },
    )
    replacement = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay={
            "status": "attested",
            "host_id": "fixture-host",
            "replayed_operations": ["render"],
            "evidence": [
                {
                    "operation": "render",
                    "result_digest": digest({"private": "retry-result"}),
                    "render_digest": digest({"private": "retry-frame"}),
                }
            ],
            "blockers": [],
        },
    )

    retry = persist_slot_replay_retry_record(
        paths,
        registry=registry,  # type: ignore[arg-type]
        retry_of=old,
        retry_receipt=replacement,
    )
    record = read_json(Path(retry["path"]))

    assert record["status"] == "repaired"
    assert set(record["body"]) == {
        "record_version",
        "registry_digest",
        "selection_batch_digest",
        "slot_digest",
        "anonymous_scene_ref",
        "retry_of",
        "repair_reason",
        "retry_receipt",
        "release_eligible",
    }
    assert record["body"]["retry_of"] == old
    assert record["body"]["retry_receipt"] == replacement


def test_stable_capture_retry_records_a_real_no_change_attempt(tmp_path: Path) -> None:
    """A repaired capture may truthfully reproduce the original blocker.

    The retry record is still required to show that the stable-capture path
    ran.  It must not force an artificial change to the immutable slot
    receipt just to make its content digest differ.
    """

    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry = _ReceiptRegistry(paths)
    old = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay={
            "status": "blocked",
            "host_id": "fixture-host",
            "replayed_operations": [],
            "evidence": [],
            "blockers": ["viewer_render_invalid"],
        },
    )

    retry = persist_slot_replay_retry_record(
        paths,
        registry=registry,  # type: ignore[arg-type]
        retry_of=old,
        retry_receipt=old,
    )
    record = read_json(Path(retry["path"]))

    assert record["status"] == "retry_blocked"
    assert record["body"]["retry_of"] == old
    assert record["body"]["retry_receipt"] == old

    second_retry = persist_slot_replay_retry_record(
        paths,
        registry=registry,  # type: ignore[arg-type]
        retry_of=old,
        retry_receipt=old,
    )

    assert second_retry["content_digest"] == retry["content_digest"]
    assert second_retry["path"] != retry["path"]
    assert Path(second_retry["path"]).is_file()


def test_retry_summary_seal_replaces_only_the_exact_prior_receipt(
    tmp_path: Path,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    registry = _ReceiptRegistry(paths)
    old = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay={
            "status": "blocked",
            "host_id": "fixture-host",
            "replayed_operations": [],
            "evidence": [],
            "blockers": ["viewer_render_invalid"],
        },
    )
    prior = persist_batch_replay_summary(paths, registry=registry, slot_receipts=[old])  # type: ignore[arg-type]
    retry = persist_slot_replay_retry_record(
        paths,
        registry=registry,  # type: ignore[arg-type]
        retry_of=old,
        retry_receipt=old,
    )

    sealed = seal_batch_replay_summary_after_retries(
        paths,
        registry=registry,  # type: ignore[arg-type]
        prior_summary_path=prior["path"],
        retry_record_paths=[retry["path"]],
    )
    summary = read_json(Path(sealed["summary"]["path"]))

    assert sealed["retry_count"] == 1
    assert summary["body"]["slot_receipts"] == [
        {"slot_digest": old["slot_digest"], "content_digest": old["content_digest"]}
    ]


def test_viewer_lock_set_is_anonymized_and_pending_for_a_blocked_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    viewer_bundle = _fixture_viewer_bundle(monkeypatch)
    registry = _ReceiptRegistry(paths, viewer_bundle=viewer_bundle)
    blocked = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay={
            "status": "blocked",
            "host_id": "fixture-host",
            "replayed_operations": [],
            "evidence": [],
            "blockers": ["viewer_render_invalid"],
        },
    )
    summary = persist_batch_replay_summary(
        paths, registry=registry, slot_receipts=[blocked]
    )  # type: ignore[arg-type]

    lock = persist_batch_scene_viewer_lock_set(
        paths,
        registry=registry,  # type: ignore[arg-type]
        replay_summary_path=summary["path"],
        viewer_bundle=viewer_bundle,
    )
    artifact = read_json(Path(lock["path"]))
    entry = artifact["body"]["slots"][0]

    assert artifact["status"] == "viewer_pending"
    assert artifact["body"]["prepare_eligible"] is False
    assert artifact["body"]["release_eligible"] is False
    assert artifact["body"]["viewer_bundle"] == viewer_bundle
    assert set(entry) == {
        "slot_digest",
        "anonymous_scene_ref",
        "viewer_family",
        "allowed_operations",
        "receipt_digest",
        "receipt_status",
        "host_id",
        "host_code_digest",
        "handoff_capable",
        "blockers",
    }
    assert entry["handoff_capable"] is False
    serialized = json.dumps(artifact, sort_keys=True)
    for forbidden in (
        "asset_path",
        "primary_asset",
        "task_id",
        "reference_answer",
        "private-primary-scene",
    ):
        assert forbidden not in serialized


def test_attested_viewer_lock_slot_is_handoff_capable_but_not_batch_eligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_value(tmp_path / "workspace")
    paths.ensure()
    viewer_bundle = _fixture_viewer_bundle(monkeypatch)
    registry = _ReceiptRegistry(paths, viewer_bundle=viewer_bundle)
    attested = persist_slot_replay_receipt(
        paths,
        registry=registry,  # type: ignore[arg-type]
        anonymous_scene_ref="scene://batch-test-anonymous-ref",
        replay={
            "status": "attested",
            "host_id": "fixture-host",
            "replayed_operations": ["render"],
            "evidence": [
                {
                    "operation": "render",
                    "result_digest": digest({"private": "result"}),
                    "render_digest": digest({"private": "frame"}),
                }
            ],
            "blockers": [],
        },
    )
    summary = persist_batch_replay_summary(
        paths, registry=registry, slot_receipts=[attested]
    )  # type: ignore[arg-type]

    lock = persist_batch_scene_viewer_lock_set(
        paths,
        registry=registry,  # type: ignore[arg-type]
        replay_summary_path=summary["path"],
        viewer_bundle=viewer_bundle,
    )
    artifact = read_json(Path(lock["path"]))

    assert artifact["status"] == "viewer_attested_pending_review"
    assert artifact["body"]["slots"][0]["handoff_capable"] is True
    assert artifact["body"]["prepare_eligible"] is False
    assert artifact["body"]["release_eligible"] is False
