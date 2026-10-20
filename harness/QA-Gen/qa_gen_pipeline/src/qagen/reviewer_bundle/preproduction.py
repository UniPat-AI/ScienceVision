"""Pending-human-review bundle for a verified V32 text-only pilot.

This deliberately does not reuse :mod:`qagen.reviewer_bundle.bundle`.
That builder represents staged release artifacts; a live pilot which stops
before human review must not manufacture a staging manifest merely to obtain
a viewer.  This companion builder has a smaller, explicit contract:

* input is one attested ``verification_complete_pending_human_review`` run;
* the HTTP surface contains only a solver projection, opaque scene reference,
  native viewer runtime, and digest-only stage trace;
* all candidate originals, truth, evidence, and usage remain private;
* the bundle cannot claim staging or release eligibility.
"""

from __future__ import annotations

from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit

from ..contracts import digest, require_digest, verify_envelope
from ..errors import InputError
from .bundle import ReviewerBundleError, _raw_sha256, _read_regular_bytes


class PendingReviewerBundleError(ReviewerBundleError):
    """A pending-review bundle is incomplete, unsafe, or improperly bound."""


_BUNDLE_NAME = "BUNDLE.json"
_CHECKSUM_NAME = "SHA256SUMS"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_SCENE_REF = re.compile(r"^scene_[0-9a-f]{32}$")
_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "answer",
        "answer_key",
        "answer_keys",
        "asset_path",
        "backend",
        "candidate_digest",
        "evidence",
        "ground_truth",
        "private_path",
        "raw_path",
        "reference_answer",
        "truth",
        "usage",
    }
)
_ALLOWED_OPERATIONS = ("rotate", "render", "measure_dihedral")
_SUPPORTED_ANSWER_FORMATS = frozenset(
    {"signed_numeric_degree_one_decimal", "signed_numeric_degree_nearest_whole"}
)
_PENDING_STATE = "verification_complete_pending_human_review"
_RUN_STATE_KIND = "qagen_v32_text_only_execution_state"
_REGISTRY_KIND = "qagen_private_batch_scene_registry"


def _strict_directory(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise PendingReviewerBundleError(f"{label} cannot be a symlink")
    try:
        resolved = raw.resolve(strict=True)
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise PendingReviewerBundleError(f"cannot resolve {label}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or resolved.is_symlink():
        raise PendingReviewerBundleError(f"{label} must be a regular local directory")
    return resolved


def _strict_file(value: str | Path, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise PendingReviewerBundleError(f"{label} cannot be a symlink")
    try:
        resolved = raw.resolve(strict=True)
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise PendingReviewerBundleError(f"cannot resolve {label}") from exc
    if not stat.S_ISREG(metadata.st_mode) or resolved.is_symlink():
        raise PendingReviewerBundleError(f"{label} must be a regular local file")
    return resolved


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular_bytes(path, label=label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PendingReviewerBundleError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise PendingReviewerBundleError(f"{label} must be a JSON object")
    return dict(value)


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PendingReviewerBundleError(f"{label} must be a POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PendingReviewerBundleError(f"{label} must be a canonical relative path")
    return path.as_posix()


def _safe_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise PendingReviewerBundleError(f"{label} is invalid")
    return value


def _strict_child(root: Path, relative: str, *, label: str) -> Path:
    safe = _safe_relative(relative, label=label)
    candidate = root.joinpath(*PurePosixPath(safe).parts)
    current = root
    try:
        for part in PurePosixPath(safe).parts:
            current = current / part
            if current.is_symlink():
                raise PendingReviewerBundleError(f"{label} cannot traverse a symlink")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PendingReviewerBundleError(f"{label} is unavailable") from exc
    return _strict_file(resolved, label=label)


def _write_json(path: Path, value: Mapping[str, Any], *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600 if private else 0o644)


def _copy_file(source: Path, destination: Path, *, private: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600 if private else 0o644)


def _copy_static_tree(source: Path, destination: Path) -> None:
    """Copy static viewer bytes without retaining source paths or symlinks."""

    for source_path in sorted(source.rglob("*"), key=lambda path: path.as_posix()):
        relative = source_path.relative_to(source)
        target = destination / relative
        if source_path.is_symlink():
            raise PendingReviewerBundleError("viewer runtime contains a symlink")
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, 0o755)
        elif source_path.is_file():
            _copy_file(source_path, target, private=False)
        else:
            raise PendingReviewerBundleError("viewer runtime contains an unsafe file")


def _assert_public_safe(value: Any, *, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise PendingReviewerBundleError(f"{label} has a non-string key")
            if key.lower() in _FORBIDDEN_PUBLIC_KEYS:
                raise PendingReviewerBundleError(f"{label}.{key} is private-only")
            _assert_public_safe(nested, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_public_safe(nested, label=f"{label}[{index}]")


def _load_pending_run(run_root: Path) -> dict[str, Any]:
    state = _read_json(_strict_child(run_root, "state.json", label="run state"), label="run state")
    try:
        state = verify_envelope(state, kind=_RUN_STATE_KIND)
    except InputError as exc:
        raise PendingReviewerBundleError("run state failed attestation") from exc
    if state.get("status") != _PENDING_STATE:
        raise PendingReviewerBundleError("run is not pending human review")
    body = state.get("body")
    if not isinstance(body, Mapping):
        raise PendingReviewerBundleError("run state body is malformed")
    if body.get("status") != _PENDING_STATE or body.get("staging_enabled") is not False or body.get("release_eligible") is not False:
        raise PendingReviewerBundleError("run state does not preserve the pending-review boundary")
    run_id = _safe_id(body.get("run_id"), label="run id")
    candidate = _read_json(_strict_child(run_root, "outputs/candidate.json", label="candidate"), label="candidate")
    truth = _read_json(_strict_child(run_root, "outputs/truth_builder.json", label="truth record"), label="truth record")
    verifier = _read_json(_strict_child(run_root, "outputs/verifier.json", label="verifier output"), label="verifier output")
    for field, artifact in (
        ("candidate_digest", candidate),
        ("truth_digest", truth),
        ("verification_digest", verifier),
    ):
        expected = body.get(field)
        try:
            require_digest(expected, f"run state {field}")
        except InputError as exc:
            raise PendingReviewerBundleError("run state has an invalid artifact digest") from exc
        if digest(artifact) != expected:
            raise PendingReviewerBundleError(f"run state {field} does not bind its artifact")
    if candidate.get("artifact_kind") != "author_private_candidate" or candidate.get("task_id") != run_id:
        raise PendingReviewerBundleError("candidate does not bind to the pending run")
    prompt = candidate.get("prompt")
    reference = candidate.get("anonymous_scene_ref")
    request = candidate.get("viewer_session_request")
    visual_target = candidate.get("visual_target")
    rubric = candidate.get("rubric")
    if not isinstance(prompt, str) or not prompt.strip():
        raise PendingReviewerBundleError("candidate prompt is invalid")
    if not isinstance(reference, str) or not reference.startswith("scene://"):
        raise PendingReviewerBundleError("candidate anonymous scene ref is invalid")
    if not isinstance(request, Mapping) or request.get("allowed_operations") != list(_ALLOWED_OPERATIONS):
        raise PendingReviewerBundleError("candidate viewer operations are not the locked conformation operations")
    if not isinstance(visual_target, Mapping) or not isinstance(rubric, Mapping):
        raise PendingReviewerBundleError("candidate visual target or rubric is invalid")
    atoms = visual_target.get("highlighted_atom_ids")
    if not isinstance(atoms, list) or len(atoms) != 4 or not all(isinstance(item, str) and item for item in atoms):
        raise PendingReviewerBundleError("candidate atom selector is invalid")
    if rubric.get("answer_format") not in _SUPPORTED_ANSWER_FORMATS:
        raise PendingReviewerBundleError("candidate answer format is not the locked format")
    return {
        "state": state,
        "candidate": candidate,
        "truth": truth,
        "verifier": verifier,
        "run_id": run_id,
    }


def _load_scene(scene_registry_path: Path, *, original_ref: str) -> dict[str, Any]:
    registry_value = _read_json(scene_registry_path, label="scene registry")
    try:
        registry = verify_envelope(registry_value, kind=_REGISTRY_KIND)
    except InputError as exc:
        raise PendingReviewerBundleError("scene registry failed attestation") from exc
    body = registry.get("body")
    if not isinstance(body, Mapping) or body.get("local_only") is not True or body.get("release_eligible") is not False:
        raise PendingReviewerBundleError("scene registry is not a local-only registry")
    asset_root_value = body.get("asset_root")
    if not isinstance(asset_root_value, str) or not Path(asset_root_value).is_absolute():
        raise PendingReviewerBundleError("scene registry asset root is invalid")
    asset_root = _strict_directory(asset_root_value, label="scene registry asset root")
    scenes = body.get("scenes")
    if not isinstance(scenes, list):
        raise PendingReviewerBundleError("scene registry scenes are invalid")
    matches = [item for item in scenes if isinstance(item, Mapping) and item.get("anonymous_scene_ref") == original_ref]
    if len(matches) != 1:
        raise PendingReviewerBundleError("candidate scene is not uniquely bound in registry")
    scene = dict(matches[0])
    if scene.get("viewer_family") != "molstar" or scene.get("asset_format") != "xyz":
        raise PendingReviewerBundleError("pending reviewer only supports the locked Mol* XYZ pilot")
    operations = scene.get("operation_allowlist")
    if not isinstance(operations, list) or not set(_ALLOWED_OPERATIONS).issubset(set(operations)):
        raise PendingReviewerBundleError("scene registry lacks locked viewer capabilities")
    backend = scene.get("backend")
    if not isinstance(backend, Mapping) or not isinstance(backend.get("primary"), Mapping):
        raise PendingReviewerBundleError("scene registry primary asset is invalid")
    primary = dict(backend["primary"])
    raw_digest = primary.get("sha256")
    if not isinstance(raw_digest, str) or not _RAW_SHA256.fullmatch(raw_digest):
        raise PendingReviewerBundleError("scene primary checksum is invalid")
    primary_path = _strict_child(asset_root, str(primary.get("path", "")), label="scene primary asset")
    if _raw_sha256(_read_regular_bytes(primary_path, label="scene primary asset")) != raw_digest:
        raise PendingReviewerBundleError("scene primary checksum changed")
    if primary_path.stat().st_size != primary.get("bytes"):
        raise PendingReviewerBundleError("scene primary size changed")
    viewer_bundle = body.get("viewer_bundle")
    if not isinstance(viewer_bundle, Mapping):
        raise PendingReviewerBundleError("scene registry viewer bundle is invalid")
    viewer_root_value = viewer_bundle.get("root")
    if not isinstance(viewer_root_value, str) or not Path(viewer_root_value).is_absolute():
        raise PendingReviewerBundleError("scene registry viewer root is invalid")
    viewer_root = _strict_directory(viewer_root_value, label="scene registry viewer root")
    molstar_html = _strict_child(viewer_root, "viewer/molstar.html", label="Mol* viewer page")
    shared = _strict_directory(viewer_root / "viewer" / "shared", label="Mol* viewer static runtime")
    return {
        "registry": registry,
        "primary": primary_path,
        "viewer_root": viewer_root,
        "molstar_html": molstar_html,
        "shared": shared,
    }


def _public_projection(*, candidate: Mapping[str, Any], public_scene_ref: str) -> dict[str, Any]:
    visual_target = candidate["visual_target"]
    request = candidate["viewer_session_request"]
    projection = {
        "task_id": candidate["task_id"],
        "prompt": candidate["prompt"],
        "anonymous_scene_ref": public_scene_ref,
        "viewer": {
            "family": "molstar",
            "page": "viewer/molstar.html",
            "allowed_operations": list(request["allowed_operations"]),
        },
        "visual_target": {
            "object_reference": visual_target.get("object_reference"),
            "selector_type": visual_target.get("selector_type"),
            "highlighted_atom_ids": list(visual_target["highlighted_atom_ids"]),
        },
        "rubric": {
            "answer_format": candidate["rubric"].get("answer_format"),
            "viewer_necessity": candidate["rubric"].get("viewer_necessity"),
        },
    }
    _assert_public_safe(projection, label="public task projection")
    return projection


def _stage_trace(state: Mapping[str, Any]) -> dict[str, Any]:
    stages = state["body"].get("stages")
    if not isinstance(stages, Mapping):
        raise PendingReviewerBundleError("run stages are malformed")
    trace: list[dict[str, Any]] = []
    for role in ("brief_builder", "author", "truth_builder", "verifier"):
        stage = stages.get(role)
        if not isinstance(stage, Mapping) or stage.get("status") != "completed":
            raise PendingReviewerBundleError("run has an incomplete stage")
        output_digest = stage.get("output_digest")
        try:
            require_digest(output_digest, f"{role} output digest")
        except InputError as exc:
            raise PendingReviewerBundleError("stage trace digest is invalid") from exc
        item = {
            "role": role,
            "worker_kind": stage.get("worker_kind", "model"),
            "provider": stage.get("provider"),
            "model": stage.get("model"),
            "output_digest": output_digest,
            "status": "completed",
        }
        if not all(isinstance(item[key], str) and item[key] for key in ("worker_kind", "provider", "model")):
            raise PendingReviewerBundleError("stage trace identity is invalid")
        trace.append(item)
    result = {
        "kind": "qagen_pending_reviewer_stage_trace",
        "schema_version": 1,
        "approval_boundary": "supplemental review only; no human approval, staging, or release",
        "stages": trace,
    }
    _assert_public_safe(result, label="public stage trace")
    return result


def _write_checksums(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_dir():
            if path.is_symlink():
                raise PendingReviewerBundleError("bundle contains a symlink directory")
            continue
        if path.is_symlink() or not path.is_file():
            raise PendingReviewerBundleError("bundle contains an unsafe artifact")
        relative = path.relative_to(root).as_posix()
        if relative != _CHECKSUM_NAME:
            entries[relative] = _raw_sha256(_read_regular_bytes(path, label="bundle artifact"))
    lines = "".join(f"{checksum}  {relative}\n" for relative, checksum in sorted(entries.items()))
    (root / _CHECKSUM_NAME).write_text(lines, encoding="utf-8")
    os.chmod(root / _CHECKSUM_NAME, 0o644)
    return entries


def _read_checksums(root: Path) -> dict[str, str]:
    content = _read_regular_bytes(root / _CHECKSUM_NAME, label="bundle checksums")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PendingReviewerBundleError("bundle checksums are not UTF-8") from exc
    if not lines:
        raise PendingReviewerBundleError("bundle checksums are empty")
    result: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\n]+)", line)
        if match is None:
            raise PendingReviewerBundleError("bundle checksum line is invalid")
        checksum, relative = match.groups()
        relative = _safe_relative(relative, label="bundle checksum path")
        if relative in result or relative == _CHECKSUM_NAME:
            raise PendingReviewerBundleError("bundle checksums repeat a path")
        result[relative] = checksum
    return result


def _all_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
        if path.name != _CHECKSUM_NAME
    }


def _private_permissions(directory: Path) -> None:
    if stat.S_IMODE(directory.stat(follow_symlinks=False).st_mode) != 0o700:
        raise PendingReviewerBundleError("private reviewer directory permissions must be 0700")
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise PendingReviewerBundleError("private reviewer directory contains a symlink")
        if path.is_dir():
            if stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o700:
                raise PendingReviewerBundleError("private reviewer subdirectory permissions must be 0700")
        elif path.is_file() and stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o600:
            raise PendingReviewerBundleError("private reviewer artifact permissions must be 0600")


def verify_pending_reviewer_bundle(bundle_root: str | Path) -> dict[str, Any]:
    """Verify the pending-review bundle without publishing or approving it."""

    root = _strict_directory(bundle_root, label="pending reviewer bundle root")
    for relative in ("public", "viewer", "scenes", "private", "private/agent-artifacts", "private/evidence", "private/usage"):
        path = root / relative
        if path.is_symlink() or not path.is_dir():
            raise PendingReviewerBundleError("pending reviewer bundle layout is incomplete")
    _private_permissions(root / "private")
    checksums = _read_checksums(root)
    actual = _all_files(root)
    if set(checksums) != actual:
        raise PendingReviewerBundleError("pending reviewer bundle checksum file set differs")
    for relative, expected in checksums.items():
        path = _strict_child(root, relative, label="bundle checksum target")
        if _raw_sha256(_read_regular_bytes(path, label="bundle checksum target")) != expected:
            raise PendingReviewerBundleError("pending reviewer bundle checksum mismatch")
    bundle = _read_json(root / _BUNDLE_NAME, label="pending reviewer manifest")
    expected_bundle_keys = {
        "kind",
        "schema_version",
        "status",
        "bundle_id",
        "source",
        "counts",
        "loopback_host",
        "staging_enabled",
        "release_eligible",
    }
    if set(bundle) != expected_bundle_keys or bundle.get("kind") != "qagen_pending_reviewer_local_bundle" or bundle.get("schema_version") != 1:
        raise PendingReviewerBundleError("pending reviewer manifest contract is invalid")
    if bundle.get("status") != "pending_human_review" or bundle.get("loopback_host") != "127.0.0.1" or bundle.get("staging_enabled") is not False or bundle.get("release_eligible") is not False:
        raise PendingReviewerBundleError("pending reviewer manifest publication boundary is invalid")
    _safe_id(bundle.get("bundle_id"), label="pending reviewer bundle id")
    source = bundle.get("source")
    if not isinstance(source, Mapping) or set(source) != {"run_id", "state_digest", "truth_digest", "verification_digest", "scene_registry_digest"}:
        raise PendingReviewerBundleError("pending reviewer source binding is invalid")
    _safe_id(source.get("run_id"), label="pending reviewer run id")
    for field in ("state_digest", "truth_digest", "verification_digest", "scene_registry_digest"):
        try:
            require_digest(source.get(field), f"pending reviewer {field}")
        except InputError as exc:
            raise PendingReviewerBundleError("pending reviewer source digest is invalid") from exc
    _assert_public_safe(bundle, label="pending reviewer manifest")
    task = _read_json(root / "public" / "task.json", label="public task")
    scene = _read_json(root / "public" / "scene.json", label="public scene")
    trace = _read_json(root / "public" / "agent-trace-index.json", label="public stage trace")
    _assert_public_safe(task, label="public task")
    _assert_public_safe(scene, label="public scene")
    _assert_public_safe(trace, label="public stage trace")
    if task.get("anonymous_scene_ref") != scene.get("scene_ref") or not isinstance(task.get("anonymous_scene_ref"), str) or not _PUBLIC_SCENE_REF.fullmatch(task["anonymous_scene_ref"]):
        raise PendingReviewerBundleError("public task and scene are not bound")
    if scene.get("viewer") != "molstar" or scene.get("asset_format") != "xyz" or scene.get("allowed_operations") != list(_ALLOWED_OPERATIONS):
        raise PendingReviewerBundleError("public scene contract is invalid")
    if task.get("viewer", {}).get("allowed_operations") != list(_ALLOWED_OPERATIONS):
        raise PendingReviewerBundleError("public task viewer contract is invalid")
    asset = root / "scenes" / task["anonymous_scene_ref"] / "primary.xyz"
    if asset.is_symlink() or not asset.is_file():
        raise PendingReviewerBundleError("public scene asset is unavailable")
    if trace.get("approval_boundary") != "supplemental review only; no human approval, staging, or release":
        raise PendingReviewerBundleError("public stage trace approval boundary is invalid")
    return {
        "bundle_root": str(root),
        "bundle_id": bundle["bundle_id"],
        "task_id": task.get("task_id"),
        "scene_ref": task["anonymous_scene_ref"],
        "checksum_count": len(checksums),
    }


def build_pending_reviewer_bundle(
    *,
    run_root: str | Path,
    scene_registry_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build a loopback-only review bundle for one unapproved live run."""

    run = _strict_directory(run_root, label="pending run root")
    loaded = _load_pending_run(run)
    scene_registry = _strict_file(scene_registry_path, label="scene registry")
    scene = _load_scene(scene_registry, original_ref=loaded["candidate"]["anonymous_scene_ref"])
    output = Path(output_dir).expanduser()
    if output.exists() or output.is_symlink():
        raise PendingReviewerBundleError("refusing to overwrite an existing pending reviewer bundle")
    output_parent = output.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    public_scene_ref = "scene_" + sha256(
        f"{loaded['state']['content_digest']}:{loaded['candidate']['anonymous_scene_ref']}".encode("utf-8")
    ).hexdigest()[:32]
    task = _public_projection(candidate=loaded["candidate"], public_scene_ref=public_scene_ref)
    public_scene = {
        "kind": "qagen_pending_reviewer_scene",
        "schema_version": 1,
        "scene_ref": public_scene_ref,
        "viewer": "molstar",
        "asset_format": "xyz",
        "primary_url": f"scene/{public_scene_ref}/primary.xyz",
        "allowed_operations": list(_ALLOWED_OPERATIONS),
    }
    trace = _stage_trace(loaded["state"])
    _assert_public_safe(public_scene, label="public scene")
    bundle_id = f"pending-review-{loaded['run_id']}-{loaded['state']['content_digest'].removeprefix('sha256:')[:12]}"
    manifest = {
        "kind": "qagen_pending_reviewer_local_bundle",
        "schema_version": 1,
        "status": "pending_human_review",
        "bundle_id": bundle_id,
        "source": {
            "run_id": loaded["run_id"],
            "state_digest": loaded["state"]["content_digest"],
            "truth_digest": loaded["state"]["body"]["truth_digest"],
            "verification_digest": loaded["state"]["body"]["verification_digest"],
            "scene_registry_digest": scene["registry"]["content_digest"],
        },
        "counts": {"task_count": 1, "scene_count": 1, "trace_stage_count": len(trace["stages"])},
        "loopback_host": "127.0.0.1",
        "staging_enabled": False,
        "release_eligible": False,
    }
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output_parent))
    try:
        for relative, mode in (("public", 0o755), ("viewer", 0o755), ("scenes", 0o755), ("private", 0o700), ("private/agent-artifacts", 0o700), ("private/evidence", 0o700), ("private/usage", 0o700)):
            directory = stage / relative
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, mode)
        _write_json(stage / _BUNDLE_NAME, manifest, private=False)
        _write_json(stage / "public" / "task.json", task, private=False)
        _write_json(stage / "public" / "scene.json", public_scene, private=False)
        _write_json(stage / "public" / "agent-trace-index.json", trace, private=False)
        _copy_file(scene["primary"], stage / "scenes" / public_scene_ref / "primary.xyz", private=False)
        _copy_file(scene["molstar_html"], stage / "viewer" / "molstar.html", private=False)
        _copy_static_tree(scene["shared"], stage / "viewer" / "shared")
        # The shipped viewer always requests this manifest.  Keep it empty so
        # the local reviewer never imports a broader negative-asset catalog.
        _write_json(
            stage / "viewer" / "negative_assets.json",
            {"note": "empty pending-review viewer manifest", "biology": [], "medical": [], "industrial": []},
            private=False,
        )
        _write_public_shell(stage / "public")
        _copy_private_run_artifacts(run, stage / "private", loaded["state"])
        _write_checksums(stage)
        verification = verify_pending_reviewer_bundle(stage)
        os.replace(stage, output)
        return {
            "status": "built_pending_human_review",
            "bundle_dir": str(output),
            "bundle_id": bundle_id,
            "task_id": loaded["run_id"],
            "scene_ref": public_scene_ref,
            "checksum_count": verification["checksum_count"],
            "loopback_host": "127.0.0.1",
            "staging_enabled": False,
            "release_eligible": False,
        }
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _write_public_shell(public: Path) -> None:
    html = """<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>ScienceVision pending review</title><link rel=\"stylesheet\" href=\"/style.css\"></head>
<body><header><p>LOCAL PENDING REVIEW</p><h1 id=\"task-id\">Loading task</h1><div id=\"operations\"></div></header><main><section class=\"question\"><h2>Question</h2><p id=\"prompt\"></p><dl id=\"selector\"></dl></section><section class=\"viewer\"><iframe id=\"viewer\" title=\"Controlled Mol* viewer\" referrerpolicy=\"no-referrer\"></iframe></section></main><footer id=\"status\">Opening controlled viewer</footer><script src=\"/app.js\"></script></body></html>\n"""
    script = """(() => { 'use strict'; const status=document.getElementById('status'); const get=path=>fetch(path,{cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject(new Error('Local review data unavailable.'))); Promise.all([get('/api/task'),get('/api/scene')]).then(([task,scene])=>{document.getElementById('task-id').textContent=task.task_id;document.getElementById('prompt').textContent=task.prompt;const operations=document.getElementById('operations');for(const operation of task.viewer.allowed_operations){const item=document.createElement('span');item.textContent=operation;operations.append(item);}const selector=document.getElementById('selector');const rows=[['Object reference',task.visual_target.object_reference],['Selected atoms',task.visual_target.highlighted_atom_ids.join(', ')],['Answer format',task.rubric.answer_format]];for(const [label,value] of rows){const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;selector.append(dt,dd);}const page='/' + task.viewer.page + '?file=/' + encodeURIComponent(scene.primary_url) + '&type=' + encodeURIComponent(scene.asset_format) + '&embed=expert';document.getElementById('viewer').src=page;status.textContent='Controlled viewer ready. Review is pending; no answer is displayed.';}).catch(error=>{status.textContent=error.message||String(error);});})();\n"""
    css = """:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#12171c;color:#eef4f1}*{box-sizing:border-box}body{min-height:100vh;margin:0;background:#12171c}header{display:flex;align-items:flex-start;gap:24px;justify-content:space-between;padding:18px 28px;border-bottom:1px solid #35434b;background:#172128}header p{margin:0;color:#9fb6b2;font:700 11px ui-monospace,monospace;letter-spacing:0}h1{margin:3px 0 0;font-size:20px;letter-spacing:0}#operations{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}#operations span{border:1px solid #557667;border-radius:4px;padding:4px 7px;color:#c9ecda;font:11px ui-monospace,monospace}main{display:grid;grid-template-columns:minmax(290px,34%) minmax(0,1fr);min-height:calc(100vh - 104px)}.question{padding:26px;border-right:1px solid #35434b;background:#19242b}.question h2{margin:0 0 12px;font-size:16px}.question p{margin:0;color:#e4ece8;line-height:1.55;white-space:pre-wrap}.question dl{display:grid;grid-template-columns:1fr;gap:5px;margin:24px 0 0}.question dt{color:#a6c0b5;font-size:11px;font-weight:700}.question dd{margin:0 0 13px;color:#dce7e2;font:12px/1.45 ui-monospace,monospace;overflow-wrap:anywhere}.viewer{min-height:620px;background:#0c1115}.viewer iframe{display:block;width:100%;height:100%;min-height:620px;border:0}footer{min-height:42px;padding:12px 28px;border-top:1px solid #35434b;color:#a9bdb5;font-size:12px}@media(max-width:800px){header{display:block;padding:16px}#operations{justify-content:flex-start;margin-top:14px}main{grid-template-columns:1fr}.question{border-right:0;border-bottom:1px solid #35434b}.viewer,.viewer iframe{min-height:560px}footer{padding:12px 16px}}\n"""
    for name, content in (("index.html", html), ("app.js", script), ("style.css", css)):
        path = public / name
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o644)


def _copy_private_run_artifacts(run: Path, private: Path, state: Mapping[str, Any]) -> None:
    artifacts = {
        "agent-artifacts/state.json": "state.json",
        "agent-artifacts/execution-lock.json": "execution-lock.json",
        "agent-artifacts/candidate.json": "outputs/candidate.json",
        "agent-artifacts/brief-builder.json": "outputs/brief_builder.json",
        "agent-artifacts/author.json": "outputs/author.json",
        "agent-artifacts/truth-builder.json": "outputs/truth_builder.json",
        "agent-artifacts/verifier.json": "outputs/verifier.json",
    }
    for destination, source in artifacts.items():
        _copy_file(_strict_child(run, source, label="private run artifact"), private / destination, private=True)
    evidence = run / "evidence"
    if evidence.exists():
        evidence = _strict_directory(evidence, label="run evidence")
        for path in sorted(evidence.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise PendingReviewerBundleError("run evidence contains a symlink")
            if path.is_file():
                _copy_file(path, private / "evidence" / path.relative_to(evidence), private=True)
            elif not path.is_dir():
                raise PendingReviewerBundleError("run evidence contains an unsafe artifact")
    usage = state["body"].get("usage")
    if not isinstance(usage, Mapping):
        raise PendingReviewerBundleError("run usage is invalid")
    _write_json(private / "usage" / "usage.json", {"kind": "qagen_pending_reviewer_private_usage", "usage": dict(usage)}, private=True)
    for path in private.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, 0o700)


def create_pending_reviewer_bundle_server(
    bundle_root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """Create a strict loopback server for a verified pending-review bundle."""

    if host != "127.0.0.1":
        raise PendingReviewerBundleError("pending reviewer server must bind exactly to 127.0.0.1")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise PendingReviewerBundleError("pending reviewer server port is invalid")
    verified = verify_pending_reviewer_bundle(bundle_root)
    root = Path(verified["bundle_root"])
    checksums = _read_checksums(root)
    task = root / "public" / "task.json"
    scene = root / "public" / "scene.json"
    trace = root / "public" / "agent-trace-index.json"
    scene_ref = verified["scene_ref"]
    public_files = {
        "/": root / "public" / "index.html",
        "/index.html": root / "public" / "index.html",
        "/app.js": root / "public" / "app.js",
        "/style.css": root / "public" / "style.css",
        "/api/task": task,
        "/api/scene": scene,
        "/api/stage-trace": trace,
        f"/scene/{scene_ref}/primary.xyz": root / "scenes" / scene_ref / "primary.xyz",
    }
    for relative in checksums:
        if relative.startswith("viewer/"):
            public_files["/" + relative] = root / relative

    class Server(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _headers(self, content_type: str, length: int, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self' data:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
            )
            self.end_headers()

        def _not_found(self) -> None:
            data = b'{"error":"not_found"}\n'
            self._headers("application/json; charset=utf-8", len(data), status=HTTPStatus.NOT_FOUND)
            if self.command != "HEAD":
                self.wfile.write(data)

        def _serve(self, path: Path) -> None:
            try:
                relative = path.relative_to(root).as_posix()
                expected = checksums[relative]
                content = _read_regular_bytes(path, label="pending reviewer HTTP artifact")
            except (KeyError, ValueError, PendingReviewerBundleError):
                self._not_found()
                return
            if _raw_sha256(content) != expected:
                self._not_found()
                return
            if path.name.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            elif path.suffix == ".js" or path.suffix == ".mjs":
                content_type = "application/javascript; charset=utf-8"
            elif path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif path.suffix == ".json":
                content_type = "application/json; charset=utf-8"
            elif path.suffix == ".xyz":
                content_type = "chemical/x-xyz"
            else:
                content_type = "application/octet-stream"
            self._headers(content_type, len(content))
            if self.command != "HEAD":
                self.wfile.write(content)

        def _dispatch(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.query and parsed.path not in {"/", "/index.html", "/viewer/molstar.html"}:
                self._not_found()
                return
            if parsed.path == "/healthz":
                data = b'{"status":"ready","host":"127.0.0.1"}\n'
                self._headers("application/json; charset=utf-8", len(data))
                if self.command != "HEAD":
                    self.wfile.write(data)
                return
            if parsed.path == "/favicon.ico":
                self._headers("image/x-icon", 0, status=HTTPStatus.NO_CONTENT)
                return
            target = public_files.get(parsed.path)
            if target is None:
                self._not_found()
                return
            self._serve(target)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._not_found()

    return Server((host, port), Handler)
