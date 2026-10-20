"""Scientific-input bridge for the bounded local chirality provider pilot.

The provider layer never resolves mutable registries or derives visual facts.
This module reads the exact workspace artifacts locked into an approved plan,
creates host-attested viewer evidence, and exposes only a reviewed structured
subset plus solver-safe render observations to the worker graph.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .contracts import digest, digest_bytes, make_envelope, require_digest, verify_envelope
from .errors import InputError, StaleInputError
from .paths import WorkspacePaths, require_within, safe_component
from .registry_contracts import validate_knowledge_index, validate_seed_index
from .resource_paths import PackageResourceError, config_path
from .storage.atomic import atomic_write_json, hash_file, read_json, read_yaml
from .viewer import SceneRegistry, ViewerAdapterError, build_attested_provider_handoff
from .viewer.batch_scene_host import BatchReplayScene, ControlledBatchViewerReplayHost
from .viewer.batch_scene_replay import (
    BatchSceneRegistry,
    BatchSceneReplayError,
    load_attested_batch_replay_binding,
    validate_viewer_bundle_provenance,
)
from .viewer.capability_mapping import (
    MEASUREMENT_OPERATIONS as CAPABILITY_MEASUREMENT_OPERATIONS,
    ViewerCapabilityMappingError,
    load_compiled_selection_capability_mapping,
    validate_capability_mapping_reference,
)


class ProviderScienceBridgeError(InputError):
    """The pilot cannot safely expose its locked scientific input to a model."""


_KNOWLEDGE_FIELDS = (
    "scientific_scope",
    "visual_target_types",
    "scenario_cards",
    "allowed_inference",
    "forbidden_shortcuts",
    "evidence_requirements",
    "requirements",
    "common_failure_modes",
    "rubric_extensions",
)

# A new leaf/adapter pair must add an equivalent controlled-host handoff
# verifier here. A leaf declaration alone never enables provider execution.
_BATCH_VIEWER_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "biology.docking_pose_plausibility": frozenset({"inspect", "isolate", "rotate", "render"}),
    "biology.molecular_collision_check": frozenset({"inspect", "isolate", "rotate", "render"}),
    "chemistry.conformation_analysis": frozenset(
        {"inspect", "rotate", "zoom", "render", "measure_distance", "measure_dihedral"}
    ),
    "chemistry.steric_accessibility": frozenset({"inspect", "rotate", "render", "measure_distance"}),
    "medical.volumetric_target_geometry": frozenset(
        {"inspect", "slice", "window_level", "bbox", "overlay_compare", "rotate", "render"}
    ),
}

_V32_BATCH_HANDOFF_KIND = "qagen_v32_batch_provider_handoff"
_V32_BATCH_HANDOFF_ARTIFACT = "attested_v32_batch_provider_handoff"
_V32_PREPRODUCTION_SEED_GUIDANCE_KIND = "qagen_local_preproduction_seed_guidance"
_V32_PREPRODUCTION_KNOWLEDGE_BOUNDARY_KIND = "qagen_local_preproduction_knowledge_boundary"
_V32_PREPRODUCTION_LOCK_APPROVAL_KIND = "qagen_compiled_selection_batch_lock_approval"
_DURABLE_HANDOFF_FORBIDDEN_KEYS = frozenset(
    {
        "data_url",
        "png",
        "truth",
        "reference_answer",
        "private_sidecar",
        "private_sidecar_digest",
    }
)


def _assert_durable_handoff_safe(value: Any) -> None:
    """Keep browser bytes and measurement values out of a persisted handoff."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or key.casefold() in _DURABLE_HANDOFF_FORBIDDEN_KEYS:
                raise ProviderScienceBridgeError("durable provider handoff contains transient or truth material")
            _assert_durable_handoff_safe(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_durable_handoff_safe(nested)


def _v32_public_bundle_binding(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Project bundle provenance without exposing its absolute local root."""

    required = {
        "bundle_version",
        "root",
        "package_id",
        "manifest_digest",
        "viewer_provenance_digest",
        "required_files",
    }
    if not isinstance(bundle, Mapping) or set(bundle) != required:
        raise ProviderScienceBridgeError("controlled viewer bundle provenance is malformed")
    result = {
        "bundle_version": bundle.get("bundle_version"),
        "package_id": bundle.get("package_id"),
        "manifest_digest": bundle.get("manifest_digest"),
        "viewer_provenance_digest": bundle.get("viewer_provenance_digest"),
        "required_files": deepcopy(bundle.get("required_files")),
    }
    if result["bundle_version"] != 1 or not isinstance(result["package_id"], str) or not result["package_id"]:
        raise ProviderScienceBridgeError("controlled viewer bundle provenance is invalid")
    try:
        require_digest(result["manifest_digest"], "controlled viewer bundle manifest digest")
        require_digest(result["viewer_provenance_digest"], "controlled viewer bundle provenance digest")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    files = result["required_files"]
    if not isinstance(files, list) or not files:
        raise ProviderScienceBridgeError("controlled viewer bundle file provenance is invalid")
    for item in files:
        if not isinstance(item, Mapping) or set(item) != {"path", "content_digest"}:
            raise ProviderScienceBridgeError("controlled viewer bundle file provenance is invalid")
        if not isinstance(item.get("path"), str) or not item["path"].startswith("viewer/"):
            raise ProviderScienceBridgeError("controlled viewer bundle file path is invalid")
        try:
            require_digest(item.get("content_digest"), "controlled viewer bundle file digest")
        except InputError as exc:
            raise ProviderScienceBridgeError(str(exc)) from exc
    return result


def _batch_host_capability(
    *,
    leaf_id: str,
    capability_mapping: Any,
    allowed_operations: list[str],
) -> dict[str, Any]:
    """Bind a batch viewer lock to both the packaged map and a real host.

    The mapping receipt is revalidated when the private plan is loaded.  This
    second check prevents a live bridge from treating that earlier validation
    as an open-ended authority: the referenced packaged profile must still
    cover the leaf and the actual local host must still implement exactly the
    least-privilege operation set.
    """

    try:
        reference = validate_capability_mapping_reference(capability_mapping)
        loaded = load_compiled_selection_capability_mapping()
    except ViewerCapabilityMappingError as exc:
        raise ProviderScienceBridgeError("batch viewer capability mapping is invalid") from exc
    mapping = loaded["mapping"]
    if reference["mapping_id"] != mapping.get("id") or reference["content_digest"] != loaded["content_digest"]:
        raise ProviderScienceBridgeError("batch viewer capability mapping changed after plan approval")
    profile = loaded["profiles"].get(reference["profile_id"])
    if not isinstance(profile, Mapping) or reference["profile_digest"] != loaded["profile_digests"].get(reference["profile_id"]):
        raise ProviderScienceBridgeError("batch viewer capability profile changed after plan approval")
    if leaf_id not in set(profile.get("leaf_coverage", [])):
        raise ProviderScienceBridgeError("batch viewer capability profile does not cover the target leaf")
    controlled = profile.get("controlled_operations")
    native_measurements = profile.get("native_measurement_operations")
    if not isinstance(controlled, list) or not all(isinstance(operation, str) and operation for operation in controlled):
        raise ProviderScienceBridgeError("batch viewer capability profile operations are malformed")
    if not isinstance(native_measurements, list) or not all(
        isinstance(operation, str) and operation for operation in native_measurements
    ):
        raise ProviderScienceBridgeError("batch viewer capability profile native measurements are malformed")
    required_host_operations = _BATCH_VIEWER_CAPABILITIES.get(leaf_id)
    if required_host_operations is None:
        raise ProviderScienceBridgeError(f"no controlled batch viewer bridge is approved for target leaf {leaf_id}")
    operation_set = set(allowed_operations)
    if len(operation_set) != len(allowed_operations) or not operation_set or "render" not in operation_set:
        raise ProviderScienceBridgeError("batch viewer operations are malformed")
    if not operation_set.issubset(required_host_operations):
        raise ProviderScienceBridgeError("batch viewer operations exceed the implemented target-leaf host capability")
    if not operation_set.issubset(set(controlled)):
        raise ProviderScienceBridgeError("batch viewer capability profile exceeds the implemented controlled host")
    non_native_measurements = sorted((operation_set & set(CAPABILITY_MEASUREMENT_OPERATIONS)) - set(native_measurements))
    if non_native_measurements:
        raise ProviderScienceBridgeError("batch viewer measurement operation is not native to the approved profile")
    return {
        "mapping_id": reference["mapping_id"],
        "mapping_digest": reference["content_digest"],
        "profile_id": reference["profile_id"],
        "profile_digest": reference["profile_digest"],
        "viewer_family": profile.get("viewer_family"),
    }


def _batch_resource_candidates(root: Path, suffixes: set[str]) -> list[Path]:
    """Return regular, non-symlink files under one bounded workspace root."""

    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise ProviderScienceBridgeError(f"batch resource root is unsafe: {root}")
    candidates: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        try:
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ProviderScienceBridgeError("batch resource escapes its approved workspace root") from exc
        candidates.append(path)
    return candidates


def _unique_envelope_by_digest(
    *,
    root: Path,
    kind: str,
    expected_digest: str,
    label: str,
) -> tuple[dict[str, Any], Path]:
    """Resolve one typed artifact only by its approved content digest."""

    require_digest(expected_digest, f"{label} digest")
    matches: list[tuple[dict[str, Any], Path]] = []
    for path in _batch_resource_candidates(root, {".json", ".yaml", ".yml"}):
        try:
            value = _document(path, label=label)
            envelope = verify_envelope(value, kind=kind)
        except (InputError, ProviderScienceBridgeError):
            continue
        if envelope.get("content_digest") == expected_digest:
            matches.append((envelope, path))
    if len(matches) != 1:
        qualifier = "ambiguous" if matches else "missing"
        raise ProviderScienceBridgeError(f"{label} resource is {qualifier} for its approved digest")
    return matches[0]


def _is_preproduction_lock(paths: WorkspacePaths, lock: Any) -> bool:
    """Recognize a persisted V32 local-preproduction approval reference."""

    if not isinstance(lock, Mapping):
        return False
    receipt = lock.get("approval_receipt")
    path_value = receipt.get("path") if isinstance(receipt, Mapping) else None
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        return False
    try:
        Path(path_value).expanduser().resolve(strict=False).relative_to((paths.root / "preproduction").resolve())
    except (OSError, ValueError):
        return False
    return True


def _preproduction_locked_artifact(
    paths: WorkspacePaths,
    *,
    lock: Mapping[str, Any],
    leaf_id: str,
    selection_batch_digest: str,
    expected_digest: str,
    artifact_directory: str,
    artifact_kind: str,
    approval_scope: str,
) -> dict[str, Any]:
    """Load one V32 preproduction artifact through its approved receipt only."""

    require_digest(selection_batch_digest, "batch selection digest")
    require_digest(expected_digest, "batch preproduction lock digest")
    required_lock_fields = {"status", "content_digest", "approval_digest", "approval_receipt"}
    if set(lock) != required_lock_fields or lock.get("status") != "approved":
        raise ProviderScienceBridgeError("batch preproduction lock is malformed or unapproved")
    if lock.get("content_digest") != expected_digest:
        raise ProviderScienceBridgeError("batch preproduction lock differs from its approved digest")
    receipt_ref = lock.get("approval_receipt")
    if not isinstance(receipt_ref, Mapping) or set(receipt_ref) != {"path", "content_digest"}:
        raise ProviderScienceBridgeError("batch preproduction approval receipt reference is malformed")
    receipt_value = receipt_ref.get("path")
    if not isinstance(receipt_value, str) or not Path(receipt_value).is_absolute():
        raise ProviderScienceBridgeError("batch preproduction approval receipt path is invalid")
    receipt_raw_path = Path(receipt_value).expanduser()
    preproduction_root = paths.root / "preproduction"
    if receipt_raw_path.is_symlink() or preproduction_root.is_symlink():
        raise ProviderScienceBridgeError("batch preproduction approval receipt is unsafe")
    try:
        receipt_path = require_within(preproduction_root, receipt_raw_path, "batch preproduction approval receipt")
        receipt = _document(receipt_path, label="batch preproduction approval receipt")
        receipt_digest = digest(receipt)
        approval_digest = require_digest(lock.get("approval_digest"), "batch preproduction approval digest")
        receipt_ref_digest = require_digest(
            receipt_ref.get("content_digest"), "batch preproduction approval receipt digest"
        )
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    receipt_fields = {
        "schema_version",
        "kind",
        "id",
        "decision",
        "scope",
        "subject_digest",
        "reviewer",
        "reviewed_at",
    }
    if (
        set(receipt) != receipt_fields
        or receipt.get("schema_version") != 1
        or receipt.get("kind") != _V32_PREPRODUCTION_LOCK_APPROVAL_KIND
        or receipt.get("decision") != "approved"
        or receipt.get("scope") != approval_scope
        or receipt.get("subject_digest") != expected_digest
        or not isinstance(receipt.get("reviewer"), str)
        or not receipt["reviewer"].strip()
        or not isinstance(receipt.get("reviewed_at"), str)
        or not receipt["reviewed_at"].strip()
        or receipt_digest != approval_digest
        or receipt_digest != receipt_ref_digest
    ):
        raise ProviderScienceBridgeError("batch preproduction approval receipt does not bind the locked artifact")

    if "." not in leaf_id:
        raise ProviderScienceBridgeError("batch preproduction target leaf id is invalid")
    domain, subdomain = leaf_id.split(".", 1)
    if (
        receipt_path.name != f"{subdomain}.json"
        or receipt_path.parent.name != domain
        or receipt_path.parent.parent.name != artifact_directory
        or receipt_path.parent.parent.parent.name != "receipts"
    ):
        raise ProviderScienceBridgeError("batch preproduction approval receipt has an unexpected layout")
    output_root = receipt_path.parents[3]
    try:
        if output_root.parent.resolve(strict=True) != preproduction_root.resolve(strict=True):
            raise ProviderScienceBridgeError("batch preproduction approval receipt escapes its artifact bundle")
    except OSError as exc:
        raise ProviderScienceBridgeError("batch preproduction artifact bundle is unavailable") from exc
    artifact_path = output_root / artifact_directory / domain / f"{subdomain}.json"
    if artifact_path.is_symlink():
        raise ProviderScienceBridgeError("batch preproduction locked artifact is unsafe")
    try:
        artifact_path = require_within(output_root, artifact_path, "batch preproduction locked artifact")
        artifact = verify_envelope(_document(artifact_path, label="batch preproduction locked artifact"), kind=artifact_kind)
    except InputError as exc:
        raise ProviderScienceBridgeError("batch preproduction locked artifact is unavailable or invalid") from exc
    if artifact.get("content_digest") != expected_digest:
        raise ProviderScienceBridgeError("batch preproduction locked artifact differs from its approved digest")
    body = artifact.get("body")
    if not isinstance(body, Mapping) or body.get("scope") != "local_preproduction_only":
        raise ProviderScienceBridgeError("batch preproduction locked artifact has an invalid scope")
    if body.get("selection_batch_digest") != selection_batch_digest or body.get("target_leaf") != leaf_id:
        raise ProviderScienceBridgeError("batch preproduction locked artifact is bound to another selection or leaf")
    return dict(artifact)


def _string_list(value: Any, *, label: str, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value) or not all(isinstance(item, str) and item for item in value):
        raise ProviderScienceBridgeError(f"{label} must be a list of non-empty strings")
    return list(value)


def _batch_preproduction_seed_context(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    selection_batch_digest: str,
    expected_digest: str,
    lock: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _preproduction_locked_artifact(
        paths,
        lock=lock,
        leaf_id=leaf_id,
        selection_batch_digest=selection_batch_digest,
        expected_digest=expected_digest,
        artifact_directory="seed",
        artifact_kind=_V32_PREPRODUCTION_SEED_GUIDANCE_KIND,
        approval_scope="compiled_selection_batch.seed",
    )
    body = artifact["body"]
    expected_fields = {
        "scope",
        "selection_batch_digest",
        "target_leaf",
        "slot_count",
        "derivation",
        "visual_capabilities",
        "response_shapes",
        "positive_rules",
        "anti_patterns",
        "production_eligible",
        "release_eligible",
    }
    if set(body) != expected_fields or body.get("production_eligible") is not False or body.get("release_eligible") is not False:
        raise ProviderScienceBridgeError("batch preproduction seed guidance is malformed")
    if not isinstance(body.get("slot_count"), int) or isinstance(body["slot_count"], bool) or body["slot_count"] < 1:
        raise ProviderScienceBridgeError("batch preproduction seed guidance slot count is invalid")
    if not isinstance(body.get("derivation"), str) or not body["derivation"].strip():
        raise ProviderScienceBridgeError("batch preproduction seed guidance derivation is invalid")
    return {
        "seed_guidance_digest": artifact["content_digest"],
        "guidance": {
            "scope": body["scope"],
            "selection_batch_digest": body["selection_batch_digest"],
            "target_leaf": body["target_leaf"],
            "slot_count": body["slot_count"],
            "derivation": body["derivation"],
            "visual_capabilities": _string_list(body.get("visual_capabilities"), label="batch preproduction seed visual capabilities"),
            "response_shapes": _string_list(body.get("response_shapes"), label="batch preproduction seed response shapes"),
            "positive_rules": _string_list(body.get("positive_rules"), label="batch preproduction seed positive rules"),
            "anti_patterns": _string_list(body.get("anti_patterns"), label="batch preproduction seed anti-patterns"),
        },
    }


def _batch_preproduction_knowledge_context(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    selection_batch_digest: str,
    expected_digest: str,
    lock: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _preproduction_locked_artifact(
        paths,
        lock=lock,
        leaf_id=leaf_id,
        selection_batch_digest=selection_batch_digest,
        expected_digest=expected_digest,
        artifact_directory="knowledge",
        artifact_kind=_V32_PREPRODUCTION_KNOWLEDGE_BOUNDARY_KIND,
        approval_scope="compiled_selection_batch.knowledge",
    )
    body = artifact["body"]
    expected_fields = {
        "scope",
        "selection_batch_digest",
        "target_leaf",
        "knowledge_class",
        "verified_source_digests",
        "permitted_use",
        "prohibited_use",
        "production_eligible",
        "release_eligible",
        "domain_expert_approval",
    }
    if (
        set(body) != expected_fields
        or body.get("production_eligible") is not False
        or body.get("release_eligible") is not False
        or body.get("domain_expert_approval") is not False
        or not isinstance(body.get("knowledge_class"), str)
        or not body["knowledge_class"].strip()
    ):
        raise ProviderScienceBridgeError("batch preproduction knowledge boundary is malformed")
    sources = body.get("verified_source_digests")
    if not isinstance(sources, list) or not sources:
        raise ProviderScienceBridgeError("batch preproduction knowledge sources are malformed")
    safe_sources: list[dict[str, str]] = []
    for source in sources:
        if not isinstance(source, Mapping) or set(source) != {"source_class", "content_digest"}:
            raise ProviderScienceBridgeError("batch preproduction knowledge source is malformed")
        source_class = source.get("source_class")
        if not isinstance(source_class, str) or not source_class:
            raise ProviderScienceBridgeError("batch preproduction knowledge source class is invalid")
        try:
            source_digest = require_digest(source.get("content_digest"), "batch preproduction knowledge source digest")
        except InputError as exc:
            raise ProviderScienceBridgeError(str(exc)) from exc
        safe_sources.append({"source_class": source_class, "content_digest": source_digest})
    return {
        "knowledge_boundary_digest": artifact["content_digest"],
        "knowledge_boundary": {
            "scope": body["scope"],
            "selection_batch_digest": body["selection_batch_digest"],
            "target_leaf": body["target_leaf"],
            "knowledge_class": body["knowledge_class"],
            "verified_source_digests": safe_sources,
            "permitted_use": _string_list(body.get("permitted_use"), label="batch preproduction knowledge permitted use"),
            "prohibited_use": _string_list(body.get("prohibited_use"), label="batch preproduction knowledge prohibited use"),
            "domain_expert_approval": False,
        },
    }


def _batch_seed_context(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    expected_digest: str,
    lock: Mapping[str, Any] | None = None,
    selection_batch_digest: str | None = None,
) -> dict[str, Any]:
    if _is_preproduction_lock(paths, lock):
        if not isinstance(lock, Mapping) or not isinstance(selection_batch_digest, str):
            raise ProviderScienceBridgeError("batch preproduction seed lock is malformed")
        return _batch_preproduction_seed_context(
            paths,
            leaf_id=leaf_id,
            selection_batch_digest=selection_batch_digest,
            expected_digest=expected_digest,
            lock=lock,
        )
    index, index_path = _unique_envelope_by_digest(
        root=paths.catalog,
        kind="seed_index",
        expected_digest=expected_digest,
        label="batch SeedIndex",
    )
    try:
        validated = validate_seed_index(index)
    except InputError as exc:
        raise ProviderScienceBridgeError("batch SeedIndex is invalid") from exc
    body = validated.get("body")
    if validated.get("status") != "eligible" or not isinstance(body, Mapping) or body.get("eligible") is not True:
        raise ProviderScienceBridgeError("batch SeedIndex is not eligible")
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ProviderScienceBridgeError("batch SeedIndex entries are malformed")
    records: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("leaf_id") != leaf_id or entry.get("status") != "verified":
            continue
        tags = entry.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
            raise ProviderScienceBridgeError("batch SeedIndex has invalid verified tags")
        # Original task/answer/asset fields never cross this bridge.
        records.append({"leaf_id": leaf_id, "status": "verified", "tags": list(tags)})
    if not records:
        raise ProviderScienceBridgeError("batch SeedIndex has no verified record for target leaf")
    catalog_digest = body.get("seed_catalog_digest")
    require_digest(catalog_digest, "batch SeedIndex catalog digest")
    return {
        "seed_index_digest": validated["content_digest"],
        "seed_index_path": str(index_path),
        "seed_catalog_digest": str(catalog_digest),
        "verified_records": records,
    }


def _batch_knowledge_context(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    expected_digest: str,
    lock: Mapping[str, Any] | None = None,
    selection_batch_digest: str | None = None,
) -> dict[str, Any]:
    if _is_preproduction_lock(paths, lock):
        if not isinstance(lock, Mapping) or not isinstance(selection_batch_digest, str):
            raise ProviderScienceBridgeError("batch preproduction knowledge lock is malformed")
        return _batch_preproduction_knowledge_context(
            paths,
            leaf_id=leaf_id,
            selection_batch_digest=selection_batch_digest,
            expected_digest=expected_digest,
            lock=lock,
        )
    snapshot, snapshot_path = _unique_envelope_by_digest(
        root=paths.knowledge / "compiled",
        kind="subdomain_knowledge_snapshot",
        expected_digest=expected_digest,
        label="batch SubdomainKnowledgeSnapshot",
    )
    body = snapshot.get("body")
    if snapshot.get("status") != "approved" or not isinstance(body, Mapping):
        raise ProviderScienceBridgeError("batch SubdomainKnowledgeSnapshot is not approved")
    if body.get("id") != leaf_id or body.get("status") != "approved":
        raise ProviderScienceBridgeError("batch SubdomainKnowledgeSnapshot is bound to another leaf")
    review = body.get("review")
    if not isinstance(review, Mapping) or review.get("decision") != "approved":
        raise ProviderScienceBridgeError("batch SubdomainKnowledgeSnapshot has no approved expert review")
    missing = [field for field in _KNOWLEDGE_FIELDS if field not in body]
    if missing:
        raise ProviderScienceBridgeError("batch SubdomainKnowledgeSnapshot lacks provider-safe scientific content")
    context = {field: deepcopy(body[field]) for field in _KNOWLEDGE_FIELDS}
    if not isinstance(context["visual_target_types"], list) or not context["visual_target_types"]:
        raise ProviderScienceBridgeError("batch knowledge visual targets are empty")
    if not isinstance(context["scenario_cards"], list) or not context["scenario_cards"]:
        raise ProviderScienceBridgeError("batch knowledge scenario cards are empty")
    return {
        "knowledge_snapshot_digest": snapshot["content_digest"],
        "knowledge_snapshot_path": str(snapshot_path),
        "knowledge": context,
    }


def _batch_config_context(*, leaf_id: str, expected_digest: str) -> dict[str, Any]:
    if "." not in leaf_id:
        raise ProviderScienceBridgeError("batch target leaf id is invalid")
    domain, subdomain = leaf_id.split(".", 1)
    try:
        path = config_path("subdomains", domain, f"{subdomain}.yaml")
    except PackageResourceError as exc:
        raise ProviderScienceBridgeError("batch target leaf config is unavailable") from exc
    if hash_file(path) != expected_digest:
        raise StaleInputError("batch target leaf config differs from its approved digest lock")
    value = _document(path, label="batch target leaf config")
    if value.get("kind") != "qagen_subdomain_config" or value.get("id") != leaf_id:
        raise ProviderScienceBridgeError("batch target leaf config identity is invalid")
    viewer = value.get("viewer")
    answer = value.get("answer")
    visual_skills = value.get("visual_skills")
    if not isinstance(viewer, Mapping) or not isinstance(answer, Mapping) or not isinstance(visual_skills, list):
        raise ProviderScienceBridgeError("batch target leaf config has no provider-safe policy projection")
    required_operations = viewer.get("required_operations")
    allowed_truth_modes = answer.get("allowed_truth_modes")
    if not isinstance(required_operations, list) or not isinstance(allowed_truth_modes, list):
        raise ProviderScienceBridgeError("batch target leaf config policy is malformed")
    return {
        "config_digest": expected_digest,
        "visual_skills": list(visual_skills),
        "required_operations": list(required_operations),
        "allowed_truth_modes": list(allowed_truth_modes),
    }


def _batch_viewer_context(
    paths: WorkspacePaths,
    *,
    leaf_id: str,
    work_item_id: str,
    slot_digest: str,
    viewer_lock_digest: str,
    selection_batch_digest: str,
    anonymous_scene_ref: str,
    allowed_operations: list[str],
    capability_mapping: Any,
    fresh_replay: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a V32 provider handoff from one fresh external-viewer replay.

    The batch lock must name a current replay lock set, registry, and receipt.
    This function reopens those exact artifacts, then uses only their bundled
    viewer root to capture transient browser PNGs.  Neither the asset path,
    bundle root, source IDs, nor browser/native tool output enter the model
    bridge or its durable handoff artifact.
    """

    capability = _batch_host_capability(
        leaf_id=leaf_id,
        capability_mapping=capability_mapping,
        allowed_operations=allowed_operations,
    )
    # The approved batch job already binds one opaque slot digest.  Passing it
    # directly lets the authoritative replay loader reject a stale selection
    # before this bridge opens any private registry or starts a viewer host.
    if not isinstance(fresh_replay, Mapping):
        raise ProviderScienceBridgeError("batch fresh viewer replay is malformed")
    try:
        require_digest(slot_digest, "batch scene slot digest")
        binding = load_attested_batch_replay_binding(
            paths=paths,
            fresh_replay=fresh_replay,
            selection_batch_digest=selection_batch_digest,
            slot_digest=slot_digest,
            anonymous_scene_ref=anonymous_scene_ref,
            allowed_operations=allowed_operations,
        )
    except (BatchSceneReplayError, InputError) as exc:
        raise ProviderScienceBridgeError("batch fresh viewer replay is unavailable or stale") from exc
    try:
        # The fresh-replay loader already validates this provenance. Reopen it
        # immediately before launching a browser as a second root-of-trust
        # check: a mocked or stale binding may never redirect the host to a
        # different local viewer bundle.
        bundle = validate_viewer_bundle_provenance(binding["viewer_bundle"])
    except (BatchSceneReplayError, InputError) as exc:
        raise ProviderScienceBridgeError("controlled viewer bundle provenance is unavailable or changed") from exc
    public_bundle = _v32_public_bundle_binding(bundle)
    try:
        registry = BatchSceneRegistry.load(paths, binding["registry"]["path"])
        resolved = registry.resolve(anonymous_scene_ref)
        scene = BatchReplayScene(
            anonymous_scene_ref=anonymous_scene_ref,
            viewer_family=str(resolved["viewer_family"]),
            primary_bytes=bytes(resolved["asset_bytes"]),
            primary_digest=str(resolved["asset_content_digest"]),
            primary_format=str(resolved["asset_format"]),
            overlay_bytes=bytes(resolved["mask_bytes"]) if "mask_bytes" in resolved else None,
            overlay_digest=str(resolved["mask_content_digest"]) if "mask_content_digest" in resolved else None,
        )
        host = ControlledBatchViewerReplayHost(str(bundle["root"]))
        try:
            captured = host.capture_provider_views(scene, allowed_operations)
        finally:
            host.shutdown()
    except (BatchSceneReplayError, InputError, KeyError, TypeError) as exc:
        raise ProviderScienceBridgeError("controlled external viewer could not capture the approved scene") from exc

    raw_views = captured.get("views") if isinstance(captured, Mapping) else None
    if not isinstance(raw_views, list) or len(raw_views) < 2:
        raise ProviderScienceBridgeError("controlled external viewer did not produce two approved visual views")
    durable_views: list[dict[str, str]] = []
    transient_views: list[dict[str, str]] = []
    for index, raw_view in enumerate(raw_views):
        if not isinstance(raw_view, Mapping):
            raise ProviderScienceBridgeError("controlled external viewer returned malformed visual evidence")
        operation = raw_view.get("operation")
        result_digest = raw_view.get("result_digest")
        render_digest = raw_view.get("render_digest")
        png = raw_view.get("png")
        if not isinstance(operation, str) or operation not in set(allowed_operations) or not isinstance(png, bytes):
            raise ProviderScienceBridgeError("controlled external viewer visual evidence is not lock-bound")
        try:
            require_digest(result_digest, "controlled viewer operation digest")
            require_digest(render_digest, "controlled viewer render digest")
        except InputError as exc:
            raise ProviderScienceBridgeError(str(exc)) from exc
        if digest_bytes(png) != render_digest:
            raise ProviderScienceBridgeError("controlled external viewer render bytes differ from their attestation digest")
        view_digest = digest(
            {
                "handoff_version": 1,
                "selection_batch_digest": selection_batch_digest,
                "slot_digest": slot_digest,
                "anonymous_scene_ref": anonymous_scene_ref,
                "sequence": index,
                "operation": operation,
                "result_digest": result_digest,
                "render_digest": render_digest,
            }
        )
        durable_views.append(
            {
                "operation": operation,
                "result_digest": result_digest,
                "render_digest": render_digest,
                "receipt_digest": view_digest,
            }
        )
        transient_views.append(
            {
                "operation": operation,
                "receipt_digest": view_digest,
                "render_digest": render_digest,
                "mime_type": "image/png",
                "data_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
            }
        )
    if len({item["receipt_digest"] for item in durable_views}) != len(durable_views) or len(
        {item["render_digest"] for item in durable_views}
    ) != len(durable_views):
        raise ProviderScienceBridgeError("controlled external viewer did not produce distinct visual evidence")

    handoff_body = {
        "handoff_version": 1,
        "artifact_kind": _V32_BATCH_HANDOFF_ARTIFACT,
        "selection_batch_digest": selection_batch_digest,
        "slot_digest": slot_digest,
        "anonymous_scene_ref": anonymous_scene_ref,
        "viewer_lock_digest": viewer_lock_digest,
        "scene_registry_digest": registry.content_digest,
        "allowed_operations": sorted(set(allowed_operations)),
        "viewer_bundle": public_bundle,
        "fresh_replay": {
            "lock_set_digest": binding["lock_set"]["content_digest"],
            "registry_digest": binding["registry"]["content_digest"],
            "receipt_digest": binding["receipt"]["content_digest"],
        },
        "host_id": captured.get("host_id"),
        "views": durable_views,
        "release_eligible": False,
    }
    _assert_durable_handoff_safe(handoff_body)
    handoff = make_envelope(
        kind=_V32_BATCH_HANDOFF_KIND,
        identifier=f"v32-batch-handoff-{safe_component(work_item_id, 'batch work item id')}",
        status="attested",
        body=handoff_body,
        producer="qagen/0.1.0:v32_batch_provider_handoff",
    )
    directory = paths.private / "viewer" / "v32-provider-handoffs"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    handoff_path = directory / f"{safe_component(work_item_id, 'batch work item id')}.{handoff['content_digest'].removeprefix('sha256:')[:16]}.json"
    atomic_write_json(handoff_path, handoff, private=True)
    try:
        persisted_handoff = verify_envelope(read_json(handoff_path), kind=_V32_BATCH_HANDOFF_KIND)
    except (InputError, OSError) as exc:
        raise ProviderScienceBridgeError("controlled external viewer handoff persistence is invalid") from exc
    if persisted_handoff["content_digest"] != handoff["content_digest"]:
        raise ProviderScienceBridgeError("controlled external viewer handoff persistence changed its digest")
    return (
        {
            # Existing candidate/evidence contracts call this a viewer profile
            # digest.  For V32 it is the approved viewer lock digest, which is
            # stronger because it also binds the fresh replay receipt below.
            "viewer_profile_digest": viewer_lock_digest,
            "capability_mapping": capability,
            "scene_registry_digest": registry.content_digest,
            "selection_batch_digest": selection_batch_digest,
            "anonymous_scene_ref": anonymous_scene_ref,
            "handoff_digest": handoff["content_digest"],
            "receipt_digests": [item["receipt_digest"] for item in durable_views],
            "rendered_views": transient_views,
            "fresh_replay_receipt_digest": binding["receipt"]["content_digest"],
            "viewer_bundle": public_bundle,
        },
        {
            "handoff_kind": _V32_BATCH_HANDOFF_KIND,
            "handoff_path": str(handoff_path),
            "handoff_digest": handoff["content_digest"],
            "selection_batch_digest": selection_batch_digest,
            "slot_digest": slot_digest,
            "anonymous_scene_ref": anonymous_scene_ref,
            "viewer_profile_digest": viewer_lock_digest,
            "scene_registry_digest": registry.content_digest,
            "fresh_replay": {
                "lock_set": dict(binding["lock_set"]),
                "registry": dict(binding["registry"]),
                "receipt": dict(binding["receipt"]),
            },
            "receipt_digests": [item["receipt_digest"] for item in durable_views],
            "render_digests": [item["render_digest"] for item in durable_views],
        },
    )


def prepare_compiled_selection_science_bridge(
    paths: WorkspacePaths,
    *,
    batch_plan_digest: str,
    selection_batch_digest: str,
    job: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one provider-safe bridge from a digest-locked batch job.

    The resolver exposes no legacy selection question, answer, task id, raw
    asset path, or backend scene arguments. Every referenced artifact must be
    found exactly once under its typed local workspace registry.
    """

    require_digest(batch_plan_digest, "batch plan digest")
    require_digest(selection_batch_digest, "selection batch digest")
    if not isinstance(job, Mapping):
        raise ProviderScienceBridgeError("batch job is malformed")
    job_id = job.get("job_id")
    leaf_id = job.get("target_leaf")
    slot_digest = job.get("slot_digest")
    locks = job.get("lock_bindings")
    if (
        not isinstance(job_id, str)
        or safe_component(job_id, "batch job id") != job_id
        or not isinstance(leaf_id, str)
        or not isinstance(slot_digest, str)
        or not isinstance(locks, Mapping)
        or set(locks) != {"config", "seed", "knowledge", "viewer"}
    ):
        raise ProviderScienceBridgeError("batch job lacks a complete approved lock set")
    require_digest(slot_digest, "batch job slot digest")
    config_lock = locks["config"]
    seed_lock = locks["seed"]
    knowledge_lock = locks["knowledge"]
    viewer_lock = locks["viewer"]
    if not all(isinstance(value, Mapping) for value in (config_lock, seed_lock, knowledge_lock, viewer_lock)):
        raise ProviderScienceBridgeError("batch job lock set is malformed")
    config_digest = config_lock.get("content_digest")
    seed_digest = seed_lock.get("content_digest")
    knowledge_digest = knowledge_lock.get("content_digest")
    viewer_digest = viewer_lock.get("content_digest")
    for value, label in (
        (config_digest, "batch config lock"),
        (seed_digest, "batch seed lock"),
        (knowledge_digest, "batch knowledge lock"),
        (viewer_digest, "batch viewer lock"),
    ):
        require_digest(value, label)
    scene_ref = viewer_lock.get("anonymous_scene_ref")
    operations = viewer_lock.get("allowed_operations")
    fresh_replay = viewer_lock.get("fresh_replay")
    if not isinstance(scene_ref, str) or not isinstance(operations, list) or not all(
        isinstance(operation, str) and operation for operation in operations
    ) or not isinstance(fresh_replay, Mapping):
        raise ProviderScienceBridgeError("batch viewer lock is malformed")
    config_context = _batch_config_context(leaf_id=leaf_id, expected_digest=str(config_digest))
    required_host_operations = {
        "render" if operation == "inspect" else operation for operation in config_context["required_operations"]
    }
    if not required_host_operations.issubset(set(operations)):
        raise ProviderScienceBridgeError("batch viewer operations differ from target-leaf config")
    seed_context = _batch_seed_context(
        paths,
        leaf_id=leaf_id,
        expected_digest=str(seed_digest),
        lock=seed_lock,
        selection_batch_digest=selection_batch_digest,
    )
    knowledge_context = _batch_knowledge_context(
        paths,
        leaf_id=leaf_id,
        expected_digest=str(knowledge_digest),
        lock=knowledge_lock,
        selection_batch_digest=selection_batch_digest,
    )
    viewer_context, handoff = _batch_viewer_context(
        paths,
        leaf_id=leaf_id,
        work_item_id=job_id,
        slot_digest=slot_digest,
        viewer_lock_digest=str(viewer_digest),
        selection_batch_digest=selection_batch_digest,
        anonymous_scene_ref=scene_ref,
        allowed_operations=list(operations),
        capability_mapping=viewer_lock.get("capability_mapping"),
        fresh_replay=fresh_replay,
    )
    if "seed_index_digest" in seed_context and "seed_catalog_digest" in seed_context:
        seed_bindings = {
            "seed_index_digest": seed_context["seed_index_digest"],
            "seed_catalog_digest": seed_context["seed_catalog_digest"],
        }
        provider_seed_context = {"verified_records": seed_context["verified_records"]}
    elif "seed_guidance_digest" in seed_context and "guidance" in seed_context:
        seed_bindings = {"seed_guidance_digest": seed_context["seed_guidance_digest"]}
        provider_seed_context = {"guidance": seed_context["guidance"]}
    else:
        raise ProviderScienceBridgeError("batch seed context has an unsupported lock form")
    if "knowledge_snapshot_digest" in knowledge_context and "knowledge" in knowledge_context:
        knowledge_bindings = {"knowledge_snapshot_digest": knowledge_context["knowledge_snapshot_digest"]}
        provider_knowledge_context = knowledge_context["knowledge"]
    elif "knowledge_boundary_digest" in knowledge_context and "knowledge_boundary" in knowledge_context:
        knowledge_bindings = {"knowledge_boundary_digest": knowledge_context["knowledge_boundary_digest"]}
        provider_knowledge_context = {"boundary": knowledge_context["knowledge_boundary"]}
    else:
        raise ProviderScienceBridgeError("batch knowledge context has an unsupported lock form")
    bridge = {
        "artifact_kind": "approved_local_provider_science_bridge",
        "schema_version": 1,
        "bridge_scope": "compiled_selection_batch",
        "leaf_id": leaf_id,
        "work_item_id": job_id,
        "lock_bindings": {
            "batch_plan_digest": batch_plan_digest,
            "selection_batch_digest": selection_batch_digest,
            "config_digest": str(config_digest),
            **seed_bindings,
            **knowledge_bindings,
            "viewer_profile_digest": viewer_context["viewer_profile_digest"],
            "scene_registry_digest": viewer_context["scene_registry_digest"],
            "viewer_capability_mapping_digest": viewer_context["capability_mapping"]["mapping_digest"],
            "viewer_capability_profile_digest": viewer_context["capability_mapping"]["profile_digest"],
            "fresh_replay_receipt_digest": viewer_context["fresh_replay_receipt_digest"],
            "viewer_bundle_manifest_digest": viewer_context["viewer_bundle"]["manifest_digest"],
            "viewer_bundle_provenance_digest": viewer_context["viewer_bundle"]["viewer_provenance_digest"],
        },
        "config_context": config_context,
        "seed_context": provider_seed_context,
        "knowledge_context": provider_knowledge_context,
        "viewer_evidence": viewer_context,
    }
    return bridge, handoff


def _document(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProviderScienceBridgeError(f"{label} is unavailable or unsafe")
    try:
        value = read_yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else read_json(path)
    except (InputError, OSError) as exc:
        raise ProviderScienceBridgeError(f"cannot read {label}") from exc
    if not isinstance(value, Mapping):
        raise ProviderScienceBridgeError(f"{label} must be an object")
    return dict(value)


def _input_locks(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    body = config.get("body")
    if not isinstance(body, Mapping):
        raise ProviderScienceBridgeError("compiled config body is malformed")
    raw_locks = body.get("input_locks")
    if not isinstance(raw_locks, list):
        raise ProviderScienceBridgeError("compiled config has no input locks")
    locks: list[dict[str, Any]] = []
    for lock in raw_locks:
        if not isinstance(lock, Mapping):
            raise ProviderScienceBridgeError("compiled config input lock is malformed")
        locks.append(dict(lock))
    return locks


def _locked_artifact(
    paths: WorkspacePaths,
    config: Mapping[str, Any],
    *,
    label: str,
    kind: str,
) -> tuple[dict[str, Any], Path]:
    matches = [lock for lock in _input_locks(config) if lock.get("label") == label]
    if len(matches) != 1:
        raise ProviderScienceBridgeError(f"compiled config lacks one frozen {label} lock")
    lock = matches[0]
    if lock.get("target") != "bytes" or lock.get("artifact_kind") != kind:
        raise ProviderScienceBridgeError(f"compiled config {label} lock is not an immutable {kind} artifact")
    expected_bytes = lock.get("digest")
    expected_artifact = lock.get("artifact_digest")
    try:
        require_digest(expected_bytes, f"{label} byte digest")
        require_digest(expected_artifact, f"{label} artifact digest")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    raw_path = lock.get("path")
    if not isinstance(raw_path, str):
        raise ProviderScienceBridgeError(f"compiled config {label} path is invalid")
    try:
        path = require_within(paths.root, raw_path, f"compiled config {label}")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    if path.is_symlink() or not path.is_file():
        raise ProviderScienceBridgeError(f"compiled config {label} artifact is unavailable or unsafe")
    try:
        actual_bytes = digest_bytes(path.read_bytes())
    except OSError as exc:
        raise ProviderScienceBridgeError(f"cannot read compiled config {label} artifact") from exc
    if actual_bytes != expected_bytes:
        raise StaleInputError(f"compiled config {label} bytes differ from its plan lock")
    value = _document(path, label=f"compiled config {label} artifact")
    try:
        artifact = verify_envelope(value, kind=kind)
    except InputError as exc:
        raise ProviderScienceBridgeError(f"compiled config {label} artifact is not a valid {kind}") from exc
    if artifact["content_digest"] != expected_artifact:
        raise StaleInputError(f"compiled config {label} artifact digest differs from its plan lock")
    return artifact, path


def _workspace_path(paths: WorkspacePaths, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProviderScienceBridgeError(f"{label} path is invalid")
    try:
        path = require_within(paths.root, paths.root / value, label)
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    if path.is_symlink() or not path.is_file():
        raise ProviderScienceBridgeError(f"{label} is unavailable or unsafe")
    return path


def _approved_knowledge_context(
    paths: WorkspacePaths,
    config: Mapping[str, Any],
    *,
    leaf_id: str,
    dependency: Mapping[str, Any],
    seed_index: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    index, index_path = _locked_artifact(paths, config, label="knowledge_index", kind="knowledge_index")
    try:
        index = validate_knowledge_index(index)
    except InputError as exc:
        raise ProviderScienceBridgeError("frozen KnowledgeIndex is invalid") from exc
    if index.get("status") != "ready":
        raise ProviderScienceBridgeError("frozen KnowledgeIndex is not ready")
    index_body = index["body"]
    if index_body.get("seed_index_digest") != seed_index.get("content_digest"):
        raise ProviderScienceBridgeError("KnowledgeIndex is not bound to the frozen SeedIndex")
    if index_body.get("seed_catalog_digest") != dependency.get("seed_digest"):
        raise ProviderScienceBridgeError("KnowledgeIndex seed catalog differs from the approved plan lock")
    entries = index_body.get("entries")
    if not isinstance(entries, Mapping):
        raise ProviderScienceBridgeError("frozen KnowledgeIndex entries are malformed")
    entry = entries.get(leaf_id)
    if not isinstance(entry, Mapping) or entry.get("state") != "approved":
        raise ProviderScienceBridgeError("provider pilot requires an approved KnowledgeIndex entry")
    snapshot_digest = entry.get("snapshot_digest")
    try:
        require_digest(snapshot_digest, "KnowledgeIndex snapshot digest")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    locked_knowledge = dependency.get("knowledge_digests")
    if not isinstance(locked_knowledge, list) or locked_knowledge != [snapshot_digest]:
        raise ProviderScienceBridgeError("KnowledgeIndex snapshot differs from the plan dependency lock")
    snapshot, snapshot_lock_path = _locked_artifact(
        paths,
        config,
        label=f"knowledge_snapshot:{leaf_id}",
        kind="subdomain_knowledge_snapshot",
    )
    if snapshot["content_digest"] != snapshot_digest:
        raise StaleInputError("frozen SubdomainKnowledgeSnapshot differs from the KnowledgeIndex")
    expected_snapshot_path = _workspace_path(paths, entry.get("snapshot_path"), label="KnowledgeIndex snapshot")
    if expected_snapshot_path != snapshot_lock_path:
        raise ProviderScienceBridgeError("KnowledgeIndex snapshot path differs from the compiled lock")
    snapshot_body = snapshot.get("body")
    if (
        snapshot.get("status") != "approved"
        or not isinstance(snapshot_body, Mapping)
        or snapshot_body.get("status") != "approved"
        or snapshot_body.get("id") != leaf_id
    ):
        raise ProviderScienceBridgeError("SubdomainKnowledgeSnapshot is not frozen and approved for this leaf")
    review = snapshot_body.get("review")
    if not isinstance(review, Mapping) or review.get("decision") != "approved" or review.get("decision_digest") != entry.get(
        "review_digest"
    ):
        raise ProviderScienceBridgeError("SubdomainKnowledgeSnapshot review binding is invalid")
    required = set(_KNOWLEDGE_FIELDS)
    if not required.issubset(snapshot_body):
        raise ProviderScienceBridgeError("approved SubdomainKnowledgeSnapshot lacks provider-safe scientific content")
    context = {field: deepcopy(snapshot_body[field]) for field in _KNOWLEDGE_FIELDS}
    if not isinstance(context["visual_target_types"], list) or not context["visual_target_types"]:
        raise ProviderScienceBridgeError("approved knowledge visual targets are empty")
    if not isinstance(context["scenario_cards"], list) or not context["scenario_cards"]:
        raise ProviderScienceBridgeError("approved knowledge scenario cards are empty")
    return (
        {
            "knowledge_index_digest": index["content_digest"],
            "knowledge_index_path": str(index_path),
            "knowledge_snapshot_digest": snapshot["content_digest"],
            "knowledge": context,
        },
        dict(snapshot_body),
    )


def _seed_context(
    paths: WorkspacePaths,
    config: Mapping[str, Any],
    *,
    leaf_id: str,
    dependency: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    index, index_path = _locked_artifact(paths, config, label="seed_index", kind="seed_index")
    try:
        index = validate_seed_index(index)
    except InputError as exc:
        raise ProviderScienceBridgeError("frozen SeedIndex is invalid") from exc
    body = index["body"]
    if index.get("status") != "eligible" or body.get("eligible") is not True:
        raise ProviderScienceBridgeError("frozen SeedIndex is not eligible")
    if body.get("seed_catalog_digest") != dependency.get("seed_digest"):
        raise ProviderScienceBridgeError("frozen SeedIndex differs from the plan seed lock")
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ProviderScienceBridgeError("frozen SeedIndex entries are malformed")
    allowed_records: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("leaf_id") != leaf_id:
            continue
        status = entry.get("status")
        if status != "verified":
            continue
        tags = entry.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag for tag in tags):
            raise ProviderScienceBridgeError("frozen SeedIndex has invalid selected tags")
        allowed_records.append(
            {
                "leaf_id": leaf_id,
                "domain": entry.get("domain"),
                "subdomain": entry.get("subdomain"),
                "status": status,
                "tags": list(tags),
            }
        )
    if not allowed_records:
        raise ProviderScienceBridgeError("frozen SeedIndex has no verified chirality input for the plan leaf")
    return (
        {
            "seed_index_digest": index["content_digest"],
            "seed_index_path": str(index_path),
            "seed_catalog_digest": body["seed_catalog_digest"],
            "verified_records": allowed_records,
        },
        dict(index),
    )


def _viewer_context(
    paths: WorkspacePaths,
    viewer: Mapping[str, Any],
    *,
    work_item_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    viewer_digest = viewer.get("content_digest")
    try:
        require_digest(viewer_digest, "persisted ViewerProfile digest")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    body = viewer.get("body")
    if not isinstance(body, Mapping):
        raise ProviderScienceBridgeError("persisted ViewerProfile body is malformed")
    registry_path = body.get("scene_registry_path")
    registry_digest = body.get("scene_registry_digest")
    refs_digest = body.get("anonymous_scene_ref_set_digest")
    try:
        require_digest(registry_digest, "persisted ViewerProfile SceneRegistry digest")
        require_digest(refs_digest, "persisted ViewerProfile scene ref digest")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    try:
        registry = SceneRegistry.load(paths, str(registry_path))
    except ViewerAdapterError as exc:
        raise ProviderScienceBridgeError(f"private SceneRegistry cannot be loaded: {exc}") from exc
    if (
        not registry.resolution.resolved
        or registry.content_digest != registry_digest
        or digest(sorted(registry.anonymous_scene_refs)) != refs_digest
        or len(registry.anonymous_scene_refs) != 1
    ):
        raise ProviderScienceBridgeError("private SceneRegistry no longer matches the persisted ViewerProfile")
    scene_ref = registry.anonymous_scene_refs[0]
    try:
        handoff = build_attested_provider_handoff(
            paths,
            registry_path=registry.path,
            viewer_profile_digest=str(viewer_digest),
            anonymous_scene_ref=scene_ref,
            request_id=f"provider-handoff-{safe_component(work_item_id, 'provider handoff work item id')}",
        )
    except ViewerAdapterError as exc:
        raise ProviderScienceBridgeError(f"controlled viewer evidence could not be attested: {exc}") from exc
    return (
        {
            "viewer_profile_digest": str(viewer_digest),
            "scene_registry_digest": registry.content_digest,
            "anonymous_scene_ref": scene_ref,
            "session_attestation_digest": handoff["session_attestation_digest"],
            "handoff_digest": handoff["handoff_digest"],
            "receipt_digests": list(handoff["receipt_digests"]),
            "receipt_chain_digest": handoff["receipt_chain_digest"],
            "solver_safe_observations": deepcopy(handoff["solver_safe_observations"]),
            "rendered_views": deepcopy(handoff["rendered_views"]),
        },
        handoff,
    )


def prepare_approved_chirality_science_bridge(
    paths: WorkspacePaths,
    *,
    plan: Mapping[str, Any],
    config: Mapping[str, Any],
    viewer: Mapping[str, Any],
    dependency: Mapping[str, Any],
    work_item: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read locked science inputs and create pre-network host evidence.

    Returns the model-safe bridge and a private handoff descriptor.  The latter
    is retained by run persistence but never placed in a provider prompt.
    """
    plan_digest = plan.get("content_digest")
    config_digest = config.get("content_digest")
    leaf_id = work_item.get("leaf_id")
    work_item_id = work_item.get("work_item_id")
    if not isinstance(leaf_id, str) or leaf_id != "chemistry.stereochemistry_chirality":
        raise ProviderScienceBridgeError("scientific bridge is restricted to the approved chirality leaf")
    if not isinstance(work_item_id, str) or safe_component(work_item_id, "provider bridge work item id") != work_item_id:
        raise ProviderScienceBridgeError("provider bridge work item id is invalid")
    try:
        require_digest(plan_digest, "provider bridge plan digest")
        require_digest(config_digest, "provider bridge config digest")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    if work_item.get("dependency_digest") != digest(dict(dependency)):
        raise ProviderScienceBridgeError("provider bridge work item is not bound to its dependency lock")

    seed_context, seed_index = _seed_context(paths, config, leaf_id=leaf_id, dependency=dependency)
    knowledge_context, _snapshot = _approved_knowledge_context(
        paths,
        config,
        leaf_id=leaf_id,
        dependency=dependency,
        seed_index=seed_index,
    )
    viewer_context, handoff = _viewer_context(paths, viewer, work_item_id=work_item_id)
    bridge = {
        "artifact_kind": "approved_local_chirality_science_bridge",
        "schema_version": 1,
        "leaf_id": leaf_id,
        "work_item_id": work_item_id,
        "lock_bindings": {
            "plan_digest": str(plan_digest),
            "config_digest": str(config_digest),
            "seed_index_digest": seed_context["seed_index_digest"],
            "seed_catalog_digest": seed_context["seed_catalog_digest"],
            "knowledge_index_digest": knowledge_context["knowledge_index_digest"],
            "knowledge_snapshot_digest": knowledge_context["knowledge_snapshot_digest"],
            "viewer_profile_digest": viewer_context["viewer_profile_digest"],
            "scene_registry_digest": viewer_context["scene_registry_digest"],
        },
        "seed_context": {
            "verified_records": seed_context["verified_records"],
        },
        "knowledge_context": knowledge_context["knowledge"],
        "viewer_evidence": viewer_context,
    }
    return bridge, handoff


def _verify_v32_batch_provider_handoff(
    paths: WorkspacePaths,
    handoff: Mapping[str, Any],
    *,
    bridge: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-attest a digest-only V32 handoff before accepting worker output."""

    required_handoff = {
        "handoff_kind",
        "handoff_path",
        "handoff_digest",
        "selection_batch_digest",
        "slot_digest",
        "anonymous_scene_ref",
        "viewer_profile_digest",
        "scene_registry_digest",
        "fresh_replay",
        "receipt_digests",
        "render_digests",
    }
    if set(handoff) != required_handoff or handoff.get("handoff_kind") != _V32_BATCH_HANDOFF_KIND:
        raise ProviderScienceBridgeError("V32 provider handoff is malformed")
    try:
        handoff_digest = require_digest(handoff.get("handoff_digest"), "V32 provider handoff digest")
        selection_digest = require_digest(handoff.get("selection_batch_digest"), "V32 selection batch digest")
        slot_digest = require_digest(handoff.get("slot_digest"), "V32 scene slot digest")
        registry_digest = require_digest(handoff.get("scene_registry_digest"), "V32 scene registry digest")
        viewer_lock_digest = require_digest(handoff.get("viewer_profile_digest"), "V32 viewer lock digest")
        path = require_within(paths.private, str(handoff.get("handoff_path")), "V32 provider handoff")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    value = _document(path, label="V32 provider handoff")
    try:
        envelope = verify_envelope(value, kind=_V32_BATCH_HANDOFF_KIND)
    except InputError as exc:
        raise ProviderScienceBridgeError("V32 provider handoff receipt is invalid") from exc
    if envelope.get("status") != "attested" or envelope["content_digest"] != handoff_digest:
        raise ProviderScienceBridgeError("V32 provider handoff digest changed")
    body = envelope.get("body")
    expected_fields = {
        "handoff_version",
        "artifact_kind",
        "selection_batch_digest",
        "slot_digest",
        "anonymous_scene_ref",
        "viewer_lock_digest",
        "scene_registry_digest",
        "allowed_operations",
        "viewer_bundle",
        "fresh_replay",
        "host_id",
        "views",
        "release_eligible",
    }
    if (
        not isinstance(body, Mapping)
        or set(body) != expected_fields
        or body.get("handoff_version") != 1
        or body.get("artifact_kind") != _V32_BATCH_HANDOFF_ARTIFACT
        or body.get("release_eligible") is not False
        or body.get("selection_batch_digest") != selection_digest
        or body.get("slot_digest") != slot_digest
        or body.get("anonymous_scene_ref") != handoff.get("anonymous_scene_ref")
        or body.get("viewer_lock_digest") != viewer_lock_digest
        or body.get("scene_registry_digest") != registry_digest
        or not isinstance(body.get("host_id"), str)
        or not body["host_id"]
    ):
        raise ProviderScienceBridgeError("V32 provider handoff bindings are malformed")
    allowed_operations = body.get("allowed_operations")
    if (
        not isinstance(allowed_operations, list)
        or not allowed_operations
        or not all(isinstance(operation, str) and operation for operation in allowed_operations)
        or len(set(allowed_operations)) != len(allowed_operations)
        or "render" not in allowed_operations
    ):
        raise ProviderScienceBridgeError("V32 provider handoff allowed operations are malformed")
    fresh_body = body.get("fresh_replay")
    if not isinstance(fresh_body, Mapping) or set(fresh_body) != {"lock_set_digest", "registry_digest", "receipt_digest"}:
        raise ProviderScienceBridgeError("V32 provider handoff fresh replay binding is malformed")
    try:
        for label, value in fresh_body.items():
            require_digest(value, f"V32 fresh replay {label}")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    try:
        binding = load_attested_batch_replay_binding(
            paths=paths,
            fresh_replay=handoff.get("fresh_replay"),
            selection_batch_digest=selection_digest,
            slot_digest=slot_digest,
            anonymous_scene_ref=str(handoff.get("anonymous_scene_ref")),
            allowed_operations=allowed_operations,
        )
    except (BatchSceneReplayError, InputError, ProviderScienceBridgeError) as exc:
        raise ProviderScienceBridgeError("V32 provider handoff fresh replay is unavailable or stale") from exc
    if (
        binding["lock_set"]["content_digest"] != fresh_body["lock_set_digest"]
        or binding["registry"]["content_digest"] != fresh_body["registry_digest"]
        or binding["receipt"]["content_digest"] != fresh_body["receipt_digest"]
        or binding["registry"]["content_digest"] != registry_digest
    ):
        raise ProviderScienceBridgeError("V32 provider handoff fresh replay differs from its receipt")
    public_bundle = _v32_public_bundle_binding(binding["viewer_bundle"])
    if body.get("viewer_bundle") != public_bundle:
        raise ProviderScienceBridgeError("V32 provider handoff viewer bundle provenance changed")

    evidence = bridge.get("viewer_evidence")
    locks = bridge.get("lock_bindings")
    if not isinstance(evidence, Mapping) or not isinstance(locks, Mapping):
        raise ProviderScienceBridgeError("V32 provider bridge is malformed")
    expected_locks = {
        "selection_batch_digest": selection_digest,
        "viewer_profile_digest": viewer_lock_digest,
        "scene_registry_digest": registry_digest,
        "fresh_replay_receipt_digest": fresh_body["receipt_digest"],
        "viewer_bundle_manifest_digest": public_bundle["manifest_digest"],
        "viewer_bundle_provenance_digest": public_bundle["viewer_provenance_digest"],
    }
    if any(locks.get(key) != value for key, value in expected_locks.items()):
        raise ProviderScienceBridgeError("V32 provider handoff does not match the science bridge locks")
    views = body.get("views")
    if not isinstance(views, list) or len(views) < 2:
        raise ProviderScienceBridgeError("V32 provider handoff has insufficient visual evidence")
    receipt_digests: list[str] = []
    render_digests: list[str] = []
    operations: list[str] = []
    for view in views:
        if not isinstance(view, Mapping) or set(view) != {"operation", "result_digest", "render_digest", "receipt_digest"}:
            raise ProviderScienceBridgeError("V32 provider handoff visual evidence is malformed")
        operation = view.get("operation")
        if not isinstance(operation, str):
            raise ProviderScienceBridgeError("V32 provider handoff visual operation is invalid")
        try:
            require_digest(view.get("result_digest"), "V32 provider result digest")
            receipt_digests.append(require_digest(view.get("receipt_digest"), "V32 provider view receipt digest"))
            render_digests.append(require_digest(view.get("render_digest"), "V32 provider render digest"))
        except InputError as exc:
            raise ProviderScienceBridgeError(str(exc)) from exc
        operations.append(operation)
    if len(set(receipt_digests)) != len(receipt_digests) or len(set(render_digests)) != len(render_digests):
        raise ProviderScienceBridgeError("V32 provider handoff visual evidence is not distinct")
    handoff_receipts = handoff.get("receipt_digests")
    handoff_renders = handoff.get("render_digests")
    if (
        not isinstance(handoff_receipts, list)
        or not isinstance(handoff_renders, list)
        or handoff_receipts != receipt_digests
        or handoff_renders != render_digests
    ):
        raise ProviderScienceBridgeError("V32 provider handoff digest bindings differ from its durable evidence")
    if (
        evidence.get("handoff_digest") != handoff_digest
        or evidence.get("receipt_digests") != receipt_digests
        or [item.get("render_digest") for item in evidence.get("rendered_views", []) if isinstance(item, Mapping)] != render_digests
        or evidence.get("anonymous_scene_ref") != handoff.get("anonymous_scene_ref")
        or evidence.get("viewer_profile_digest") != viewer_lock_digest
    ):
        raise ProviderScienceBridgeError("V32 provider bridge evidence differs from its durable handoff")
    return dict(envelope)


def verify_attested_provider_handoff(
    paths: WorkspacePaths,
    handoff: Mapping[str, Any],
    *,
    bridge: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidate a persisted host handoff before accepting model output."""
    if not isinstance(handoff, Mapping) or not isinstance(bridge, Mapping):
        raise ProviderScienceBridgeError("provider handoff or science bridge is malformed")
    if handoff.get("handoff_kind") == _V32_BATCH_HANDOFF_KIND:
        return _verify_v32_batch_provider_handoff(paths, handoff, bridge=bridge)
    path_value = handoff.get("handoff_path")
    handoff_digest = handoff.get("handoff_digest")
    try:
        require_digest(handoff_digest, "provider handoff digest")
        path = require_within(paths.private, str(path_value), "provider handoff")
    except InputError as exc:
        raise ProviderScienceBridgeError(str(exc)) from exc
    value = _document(path, label="provider handoff")
    try:
        envelope = verify_envelope(value, kind="provider_viewer_evidence_handoff")
    except InputError as exc:
        raise ProviderScienceBridgeError("provider handoff receipt is invalid") from exc
    if envelope["content_digest"] != handoff_digest:
        raise ProviderScienceBridgeError("provider handoff digest changed")
    body = envelope.get("body")
    evidence = bridge.get("viewer_evidence")
    locks = bridge.get("lock_bindings")
    if not isinstance(body, Mapping) or not isinstance(evidence, Mapping) or not isinstance(locks, Mapping):
        raise ProviderScienceBridgeError("provider handoff bindings are malformed")
    required_pairs = {
        "viewer_profile_digest": locks.get("viewer_profile_digest"),
        "scene_registry_digest": locks.get("scene_registry_digest"),
        "anonymous_scene_ref": evidence.get("anonymous_scene_ref"),
        "session_attestation_digest": evidence.get("session_attestation_digest"),
        "receipt_chain_digest": evidence.get("receipt_chain_digest"),
    }
    if any(body.get(key) != expected for key, expected in required_pairs.items()):
        raise ProviderScienceBridgeError("provider handoff does not match the scientific bridge")
    receipt_digests = body.get("receipt_digests")
    if not isinstance(receipt_digests, list) or receipt_digests != evidence.get("receipt_digests") or not receipt_digests:
        raise ProviderScienceBridgeError("provider handoff receipt references are invalid")
    for receipt_digest in receipt_digests:
        try:
            require_digest(receipt_digest, "provider handoff receipt digest")
        except InputError as exc:
            raise ProviderScienceBridgeError(str(exc)) from exc
    session_id = body.get("session_id")
    if not isinstance(session_id, str):
        raise ProviderScienceBridgeError("provider handoff session id is invalid")
    # Rechecking the real receipt files rejects a model-created citation or a
    # stale/tampered handoff before the candidate can be persisted.
    from .viewer import verify_viewer_evidence_chain

    try:
        chain = verify_viewer_evidence_chain(paths, session_id)
    except ViewerAdapterError as exc:
        raise ProviderScienceBridgeError(f"provider handoff receipt chain is invalid: {exc}") from exc
    if (
        chain.get("session_attestation_digest") != body.get("session_attestation_digest")
        or chain.get("final_receipt_digest") != receipt_digests[-1]
        or chain.get("action_count") != len(receipt_digests)
    ):
        raise ProviderScienceBridgeError("provider handoff receipt chain no longer matches the handoff")
    return dict(envelope)
