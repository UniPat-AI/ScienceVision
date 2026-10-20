"""Digest-bound plan creation and approval for eligible compiled leaves."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Any, Mapping

from ..config import load_compiled_config, revalidate_compiled_config
from ..contracts import canonical_json, digest, make_decision, make_envelope, require_digest, verify_envelope
from ..errors import GatePendingError, InputError, InvalidStateError
from ..paths import WorkspacePaths, require_within
from ..storage.atomic import atomic_write_json, read_json
from ..storage.ledger import append_event
from ..viewer import load_viewer_profile, revalidate_viewer_profile


class PlanError(InputError):
    """A plan or approval does not satisfy its local lock contracts."""


_PLAN_DECISION_THREAD_LOCK = threading.RLock()


def _short(value: str) -> str:
    return value.split(":", 1)[-1][:16]


@contextmanager
def _plan_decision_lock(paths: WorkspacePaths, plan_digest: str):
    """Serialize one plan's terminal decision across threads and processes."""
    require_digest(plan_digest, "plan digest")
    paths.plan_receipts.mkdir(parents=True, exist_ok=True)
    lock_path = paths.plan_receipts / f".plan-{plan_digest.split(':', 1)[1]}.lock"
    with _PLAN_DECISION_THREAD_LOCK:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _create_decision_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    """Create an immutable receipt without replacing an existing decision."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(dict(receipt)) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _dependency_locks(config: Mapping[str, Any], *, leaf_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Read a compiled config's exact, ordered per-leaf dependency locks."""
    body = config["body"]
    raw = body.get("dependency_locks")
    lock_digests = body.get("lock_digests")
    if not isinstance(raw, list) or not isinstance(lock_digests, Mapping):
        raise PlanError("compiled config lacks an explicit dependency lock map")
    expected_digest = lock_digests.get("dependency_lock_map_digest")
    require_digest(expected_digest, "compiled dependency lock map digest")
    if digest(raw) != expected_digest:
        raise PlanError("compiled dependency lock map digest does not match its contents")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise PlanError("compiled dependency lock entry is malformed")
        leaf_id = item.get("leaf_id")
        if not isinstance(leaf_id, str) or not leaf_id or leaf_id in seen:
            raise PlanError("compiled dependency lock map has an invalid or duplicate leaf id")
        seen.add(leaf_id)
        for key in ("seed_digest", "asset_digest", "viewer_definition_digest", "rubric_digest"):
            require_digest(item.get(key), f"compiled dependency {leaf_id}.{key}")
        knowledge = item.get("knowledge_digests")
        if not isinstance(knowledge, list) or not knowledge:
            raise PlanError(f"compiled dependency {leaf_id}.knowledge_digests is malformed")
        for value in knowledge:
            require_digest(value, f"compiled dependency {leaf_id}.knowledge_digest")
        if leaf_ids is None or leaf_id in leaf_ids:
            result.append(dict(item))
    result.sort(key=lambda item: str(item["leaf_id"]))
    if leaf_ids is not None and {item["leaf_id"] for item in result} != leaf_ids:
        raise PlanError("compiled dependency lock map lacks a selected leaf")
    return result


def _plan_locks(
    config: Mapping[str, Any], viewer_profile: Mapping[str, Any], dependency_locks: list[dict[str, Any]]
) -> dict[str, str]:
    viewer_definition_digest = viewer_profile["body"].get("definition_digest")
    require_digest(viewer_definition_digest, "viewer profile definition digest")
    config_lock_digests = config["body"].get("lock_digests")
    if not isinstance(config_lock_digests, Mapping):
        raise PlanError("compiled config lacks control-plane lock digests")
    model_routing_digest = config_lock_digests.get("model_routing_digest")
    require_digest(model_routing_digest, "compiled model routing digest")
    rubric_lock_digest = config_lock_digests.get("rubric_lock_digest")
    require_digest(rubric_lock_digest, "compiled rubric lock digest")
    locks = {
        "config_digest": config["content_digest"],
        "viewer_digest": viewer_profile["content_digest"],
        "viewer_definition_digest": str(viewer_definition_digest),
        "dependency_lock_map_digest": digest(dependency_locks),
        "model_routing_digest": str(model_routing_digest),
        "rubric_lock_digest": str(rubric_lock_digest),
    }
    scene_registry_digest = viewer_profile["body"].get("scene_registry_digest")
    if scene_registry_digest is not None:
        require_digest(scene_registry_digest, "viewer profile scene registry digest")
        locks["scene_registry_digest"] = str(scene_registry_digest)
    if config["body"].get("profile_mode") == "local_fixture_compile":
        fixture_routing_digest = config_lock_digests.get("fixture_routing_digest")
        require_digest(fixture_routing_digest, "compiled fixture routing digest")
        locks["fixture_routing_digest"] = str(fixture_routing_digest)
    if config["body"].get("profile_mode") == "local_provider_pilot":
        provider_runtime_digest = config_lock_digests.get("provider_runtime_digest")
        require_digest(provider_runtime_digest, "compiled provider runtime digest")
        locks["provider_runtime_digest"] = str(provider_runtime_digest)
    for key in ("planning_policy_digest", "viewer_profile_requirement_digest"):
        value = config_lock_digests.get(key)
        if value is not None:
            require_digest(value, f"compiled {key}")
            locks[key] = str(value)
    return locks


def _validate_pilot_viewer_profile_requirement(
    config: Mapping[str, Any], viewer_profile: Mapping[str, Any]
) -> None:
    """Reject generic fixture profiles for the concrete chirality host pilot."""
    requirement = config["body"].get("viewer_profile_requirement")
    if requirement is None:
        return
    if not isinstance(requirement, Mapping):
        raise PlanError("compiled viewer profile requirement is malformed")
    expected_definition = requirement.get("definition_digest")
    expected_operations = requirement.get("required_operations")
    if (
        requirement.get("adapter_id") != "molstar-chirality-host-v1"
        or requirement.get("require_non_fixture") is not True
        or requirement.get("require_scene_registry") is not True
        or not isinstance(expected_operations, list)
        or set(expected_operations) != {"rotate", "render"}
    ):
        raise PlanError("chirality pilot viewer requirement is malformed")
    require_digest(expected_definition, "chirality pilot viewer definition digest")
    body = viewer_profile.get("body")
    if not isinstance(body, Mapping):
        raise PlanError("viewer profile is malformed")
    if body.get("definition_digest") != expected_definition:
        raise PlanError("chirality pilot requires the persistent real ViewerProfile definition")
    if body.get("local_fixture") is not False or viewer_profile.get("status") != "attested_local_only":
        raise PlanError("chirality pilot requires a non-fixture persistent ViewerProfile")
    allowed = body.get("allowed_operations")
    capabilities = body.get("capabilities")
    if not isinstance(allowed, list) or set(allowed) != set(expected_operations):
        raise PlanError("chirality pilot ViewerProfile operations must be exactly rotate and render")
    if not isinstance(capabilities, list) or not set(expected_operations).issubset(set(capabilities)):
        raise PlanError("chirality pilot ViewerProfile capability mismatch")
    for key in ("scene_registry_path", "scene_registry_digest", "anonymous_scene_ref_set_digest"):
        if not isinstance(body.get(key), str) or not body.get(key):
            raise PlanError("chirality pilot ViewerProfile lacks a private SceneRegistry lock")
    require_digest(body.get("scene_registry_digest"), "chirality pilot ViewerProfile scene registry digest")
    require_digest(
        body.get("anonymous_scene_ref_set_digest"),
        "chirality pilot ViewerProfile anonymous scene reference lock",
    )


def _planning_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config["body"].get("planning")
    if raw is None:
        return {
            "default_count": 1,
            "quota": 1,
            "batch": None,
            "per_work_item_budget": {"max_candidates": 1, "max_revisions": 1},
        }
    if not isinstance(raw, Mapping):
        raise PlanError("compiled planning policy is malformed")
    default_count = raw.get("default_count")
    quota = raw.get("quota")
    budget = raw.get("per_work_item_budget")
    if default_count != 1 or isinstance(quota, bool) or not isinstance(quota, int) or quota < 1:
        raise PlanError("compiled planning count policy is malformed")
    if not isinstance(budget, Mapping) or budget.get("max_candidates") != 1 or budget.get("max_revisions") != 1:
        raise PlanError("compiled planning work-item budget is malformed")
    return dict(raw)


def _requested_count(
    config: Mapping[str, Any], *, count: int | None, selected_leaf_ids: set[str], planning: Mapping[str, Any]
) -> int:
    requested = planning["default_count"] if count is None else count
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise PlanError("plan count must be a positive integer")
    if requested == 1:
        return 1
    batch = planning.get("batch")
    quota = planning.get("quota")
    if (
        config["body"].get("profile_mode") != "local_provider_pilot"
        or config["body"].get("profile_id") != "chirality_provider_pilot"
        or selected_leaf_ids != {"chemistry.stereochemistry_chirality"}
        or not isinstance(batch, Mapping)
        or isinstance(quota, bool)
        or not isinstance(quota, int)
    ):
        raise PlanError("batch count is restricted to the chirality local provider pilot")
    minimum = batch.get("min_count")
    maximum = batch.get("max_count")
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
        or not minimum <= requested <= maximum <= 10
        or requested > quota
    ):
        raise PlanError("chirality pilot batch count must be from 5 through its profile quota (maximum 10)")
    return requested


def _work_item_id(leaf_id: str, variation_id: str, ordinal: int, count: int) -> str:
    if count == 1:
        return leaf_id
    return f"{leaf_id}--{ordinal:02d}-{_short(variation_id)}"


def _build_work_items(
    *,
    eligible_leaves: list[dict[str, Any]],
    dependency_locks: list[dict[str, Any]],
    count_per_leaf: int,
    planning: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Expand one approved leaf deterministically without exposing seed content."""
    dependencies = {item["leaf_id"]: item for item in dependency_locks}
    budget = planning["per_work_item_budget"]
    batch = planning.get("batch")
    seed_policy = (
        batch["seed_selection"] if isinstance(batch, Mapping) and isinstance(batch.get("seed_selection"), Mapping) else {
            "policy": "locked_seed_digest",
            "dedupe_policy": "seed_variation_pair",
        }
    )
    variation_policy = (
        batch["variation"] if isinstance(batch, Mapping) and isinstance(batch.get("variation"), Mapping) else {
            "policy": "deterministic_unique_slot"
        }
    )
    work_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_seed_variations: set[str] = set()
    for leaf in eligible_leaves:
        leaf_id = leaf["leaf_id"]
        dependency = dependencies.get(leaf_id)
        if dependency is None:
            raise PlanError(f"approved leaf {leaf_id} lacks a dependency map")
        for ordinal in range(1, count_per_leaf + 1):
            variation_id = digest(
                {
                    "leaf_id": leaf_id,
                    "seed_digest": dependency["seed_digest"],
                    "ordinal": ordinal,
                    "policy": variation_policy["policy"],
                }
            )
            seed_dedupe_key = digest(
                {
                    "seed_digest": dependency["seed_digest"],
                    "variation_id": variation_id,
                    "policy": seed_policy["dedupe_policy"],
                }
            )
            work_item_id = _work_item_id(leaf_id, variation_id, ordinal, count_per_leaf)
            if work_item_id in seen_ids:
                raise PlanError("plan generated a duplicate work item id")
            if seed_dedupe_key in seen_seed_variations:
                raise PlanError("plan generated a duplicate seed selection and variation")
            seen_ids.add(work_item_id)
            seen_seed_variations.add(seed_dedupe_key)
            work_items.append(
                {
                    "work_item_id": work_item_id,
                    "leaf_id": leaf_id,
                    "dependency_digest": digest(dependency),
                    "approved_operations": list(leaf["required_operations"]),
                    "seed_selection": {
                        "policy": seed_policy["policy"],
                        "source_digest": dependency["seed_digest"],
                        "dedupe_policy": seed_policy["dedupe_policy"],
                        "dedupe_key": seed_dedupe_key,
                    },
                    "variation": {
                        "policy": variation_policy["policy"],
                        "ordinal": ordinal,
                        "variation_id": variation_id,
                    },
                    "budget": {
                        "max_candidates": budget["max_candidates"],
                        "max_revisions": budget["max_revisions"],
                    },
                }
            )
    return work_items


def validate_plan_work_items(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Verify the plan's concrete work-item expansion and its budget boundary."""
    body = plan.get("body")
    if not isinstance(body, Mapping):
        raise PlanError("generation plan body is malformed")
    work_items = body.get("work_items")
    locks = body.get("lock_digests")
    dependencies = body.get("dependency_locks")
    leaves = body.get("eligible_leaves")
    if not isinstance(work_items, list) or not work_items:
        raise PlanError("generation plan has no work items")
    if not isinstance(locks, Mapping) or digest(work_items) != locks.get("work_item_map_digest"):
        raise PlanError("generation plan work-item lock is missing or changed")
    if not isinstance(dependencies, list) or not isinstance(leaves, list):
        raise PlanError("generation plan lacks work-item dependencies")
    dependency_by_leaf = {
        item.get("leaf_id"): item for item in dependencies if isinstance(item, Mapping) and isinstance(item.get("leaf_id"), str)
    }
    leaf_by_id = {
        item.get("leaf_id"): item for item in leaves if isinstance(item, Mapping) and isinstance(item.get("leaf_id"), str)
    }
    if not dependency_by_leaf or not leaf_by_id:
        raise PlanError("generation plan work-item references are malformed")
    seen_ids: set[str] = set()
    seen_seed_variations: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in work_items:
        if not isinstance(item, Mapping):
            raise PlanError("generation plan work item is malformed")
        work_item_id = item.get("work_item_id")
        leaf_id = item.get("leaf_id")
        if not isinstance(work_item_id, str) or not work_item_id or work_item_id in seen_ids:
            raise PlanError("generation plan has duplicate or invalid work item ids")
        if not isinstance(leaf_id, str) or leaf_id not in dependency_by_leaf or leaf_id not in leaf_by_id:
            raise PlanError("generation plan work item references an unapproved leaf")
        dependency = dependency_by_leaf[leaf_id]
        approved_operations = item.get("approved_operations")
        if approved_operations != leaf_by_id[leaf_id].get("required_operations"):
            raise PlanError("generation plan work item operations differ from its leaf")
        if item.get("dependency_digest") != digest(dependency):
            raise PlanError("generation plan work item dependency changed")
        seed_selection = item.get("seed_selection")
        variation = item.get("variation")
        budget = item.get("budget")
        if not isinstance(seed_selection, Mapping) or not isinstance(variation, Mapping) or not isinstance(budget, Mapping):
            raise PlanError("generation plan work item scope is malformed")
        if seed_selection.get("source_digest") != dependency.get("seed_digest"):
            raise PlanError("generation plan work item seed lock changed")
        dedupe_key = seed_selection.get("dedupe_key")
        variation_id = variation.get("variation_id")
        require_digest(dedupe_key, "work item seed variation dedupe key")
        require_digest(variation_id, "work item variation id")
        if dedupe_key in seen_seed_variations:
            raise PlanError("generation plan duplicates a seed selection and variation")
        if budget.get("max_candidates") != 1 or budget.get("max_revisions") != 1:
            raise PlanError("generation plan work item exceeds the one-candidate/one-revision budget")
        seen_ids.add(work_item_id)
        seen_seed_variations.add(dedupe_key)
        result.append(dict(item))
    return result


def _prior_plan_decisions(paths: WorkspacePaths, plan_digest: str) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in paths.plan_receipts.glob("*.decision.json"):
        try:
            value = read_json(path)
            if not isinstance(value, Mapping):
                continue
            receipt = verify_envelope(value, kind="plan_approval_decision")
        except (InputError, OSError):
            continue
        bound = receipt["body"].get("bound_digests")
        if isinstance(bound, Mapping) and bound.get("plan_digest") == plan_digest:
            receipts.append(receipt)
    return receipts


def create_plan(
    paths: WorkspacePaths,
    *,
    config_path: str | Path,
    viewer_profile_path: str | Path,
    count: int | None = None,
) -> dict[str, Any]:
    """Create a plan containing only leaf/config/session compatible work."""
    paths.ensure()
    config_file = require_within(paths.compiled_configs, config_path, "compiled config")
    viewer_file = require_within(paths.viewer_profiles, viewer_profile_path, "viewer profile")
    config = load_compiled_config(paths, config_file)
    viewer_profile = load_viewer_profile(paths, viewer_file)
    revalidate_compiled_config(config)
    revalidate_viewer_profile(viewer_profile)
    if config["body"].get("status") == "blocked":
        raise GatePendingError("compiled config is blocked; no plan can be created")
    _validate_pilot_viewer_profile_requirement(config, viewer_profile)
    viewer_body = viewer_profile["body"]
    allowed_operations = set(viewer_body.get("allowed_operations", []))
    eligible_leaves: list[dict[str, Any]] = []
    selected_leaf_ids: set[str] = set()
    config_dependencies = {item["leaf_id"]: item for item in _dependency_locks(config)}
    for leaf in config["body"].get("leaves", []):
        if not isinstance(leaf, Mapping) or leaf.get("status") != "eligible":
            continue
        effective = leaf.get("effective")
        required_operations: set[str] = set()
        if isinstance(effective, Mapping):
            viewer = effective.get("viewer")
            if isinstance(viewer, Mapping):
                raw_operations = viewer.get("required_operations", [])
                if isinstance(raw_operations, list):
                    required_operations = set(raw_operations)
        if not required_operations.issubset(allowed_operations):
            continue
        leaf_locks = leaf.get("locks")
        if not isinstance(leaf_locks, Mapping):
            raise PlanError(f"eligible leaf {leaf.get('leaf_id')} lacks lock data")
        leaf_id = leaf.get("leaf_id")
        if not isinstance(leaf_id, str) or leaf_id not in config_dependencies:
            raise PlanError(f"eligible leaf {leaf_id!r} lacks a dependency lock entry")
        dependency = config_dependencies[leaf_id]
        if dependency.get("viewer_definition_digest") != viewer_body.get("definition_digest"):
            # One controlled viewer profile cannot be used to author a leaf
            # locked to a different viewer definition.
            continue
        if dict(leaf_locks) != {
            "seed_digest": dependency["seed_digest"],
            "asset_digest": dependency["asset_digest"],
            "viewer_definition_digest": dependency["viewer_definition_digest"],
            "knowledge_digests": dependency["knowledge_digests"],
            "rubric_digest": dependency["rubric_digest"],
        }:
            raise PlanError(f"eligible leaf {leaf_id} locks do not match the compiled dependency map")
        selected_leaf_ids.add(leaf_id)
        eligible_leaves.append(
            {
                "leaf_id": leaf_id,
                "required_operations": sorted(required_operations),
                "locks": dict(leaf_locks),
            }
        )
    if not eligible_leaves:
        raise GatePendingError("no compiled leaf is compatible with the controlled viewer session")
    dependency_locks = _dependency_locks(config, leaf_ids=selected_leaf_ids)
    planning = _planning_policy(config)
    count_per_leaf = _requested_count(
        config,
        count=count,
        selected_leaf_ids=selected_leaf_ids,
        planning=planning,
    )
    work_items = _build_work_items(
        eligible_leaves=eligible_leaves,
        dependency_locks=dependency_locks,
        count_per_leaf=count_per_leaf,
        planning=planning,
    )
    locks = _plan_locks(config, viewer_profile, dependency_locks)
    locks["work_item_map_digest"] = digest(work_items)
    provider_pilot = config["body"].get("profile_mode") == "local_provider_pilot"
    body = {
        "config_path": str(config_file),
        "viewer_profile_path": str(viewer_file),
        "profile_id": config["body"].get("profile_id"),
        "profile_mode": config["body"].get("profile_mode"),
        "lock_digests": locks,
        "dependency_locks": dependency_locks,
        "eligible_leaves": eligible_leaves,
        "work_items": work_items,
        "count_per_leaf": count_per_leaf,
        "planning": planning,
        "fixture_only": bool(
            not provider_pilot
            and (viewer_body.get("local_fixture") or config["body"].get("profile_mode") == "local_fixture_compile")
        ),
        "provider_pilot": provider_pilot,
    }
    plan = make_envelope(
        kind="generation_plan",
        identifier=f"plan-{config['body'].get('profile_id', 'unknown')}-{_short(config['content_digest'])}",
        status="pending_approval",
        body=body,
        producer="qagen/0.1.0:plan_control_plane",
    )
    output_path = paths.plans / f"{plan['id']}-{_short(plan['content_digest'])}.json"
    atomic_write_json(output_path, plan, private=True)
    append_event(
        paths,
        "plan_created",
        {
            "plan_digest": plan["content_digest"],
            "config_digest": locks["config_digest"],
            "eligible_leaf_count": len(eligible_leaves),
            "work_item_count": len(work_items),
        },
    )
    return {
        "plan_path": str(output_path),
        "plan_digest": plan["content_digest"],
        "eligible_leaves": [leaf["leaf_id"] for leaf in eligible_leaves],
        "work_item_ids": [item["work_item_id"] for item in work_items],
        "count_per_leaf": count_per_leaf,
        "status": plan["status"],
    }


def load_plan(paths: WorkspacePaths, plan_path: str | Path) -> dict[str, Any]:
    path = require_within(paths.plans, plan_path, "generation plan")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise PlanError("generation plan is not an object")
    return verify_envelope(value, kind="generation_plan")


def approve_plan(
    paths: WorkspacePaths,
    *,
    plan_path: str | Path,
    reviewer: str,
    decision: str,
    rationale: str = "",
) -> dict[str, Any]:
    """Record the sole human gate that permits a plan to start a run."""
    paths.ensure()
    if decision not in {"approved", "revise", "rejected", "quarantined"}:
        raise PlanError("plan decision must be approved, revise, rejected, or quarantined")
    if decision in {"revise", "rejected"} and not rationale.strip():
        raise PlanError(f"plan decision {decision} requires a non-empty rationale")
    initial = load_plan(paths, plan_path)
    plan_digest = initial["content_digest"]
    with _plan_decision_lock(paths, plan_digest):
        plan = load_plan(paths, plan_path)
        if plan["content_digest"] != plan_digest:
            raise InvalidStateError("generation plan changed before its approval decision")
        if plan["status"] != "pending_approval":
            raise InvalidStateError("only a pending generation plan can be decided")
        prior = _prior_plan_decisions(paths, plan_digest)
        if prior:
            raise InvalidStateError("generation plan already has an immutable approval decision")
        locks = plan["body"].get("lock_digests")
        if not isinstance(locks, Mapping):
            raise PlanError("generation plan lacks lock digests")
        required = {
            "config_digest",
            "viewer_digest",
            "viewer_definition_digest",
            "dependency_lock_map_digest",
            "model_routing_digest",
            "work_item_map_digest",
        }
        if "planning_policy_digest" in locks:
            required.add("planning_policy_digest")
        if "viewer_profile_requirement_digest" in locks:
            required.add("viewer_profile_requirement_digest")
        if plan["body"].get("provider_pilot"):
            required.add("scene_registry_digest")
        if plan["body"].get("fixture_only"):
            required.add("fixture_routing_digest")
        if plan["body"].get("provider_pilot"):
            required.add("provider_runtime_digest")
        if not required.issubset(locks):
            raise PlanError("generation plan lacks required approval bindings")
        dependency_locks = plan["body"].get("dependency_locks")
        if not isinstance(dependency_locks, list) or digest(dependency_locks) != locks["dependency_lock_map_digest"]:
            raise PlanError("generation plan dependency lock map is missing or does not match its digest")
        validate_plan_work_items(plan)
        bound = {"plan_digest": plan_digest, **{key: locks[key] for key in sorted(locks)}}
        receipt = make_decision(
            kind="plan_approval_decision",
            identifier=f"plan-decision-{_short(plan_digest)}",
            decision=decision,
            actor_id=reviewer,
            actor_role="plan_owner",
            bound_digests=bound,
            rationale=rationale,
            extra={
                "plan_digest": plan_digest,
                "config_digest": locks["config_digest"],
                "viewer_profile_digest": locks["viewer_digest"],
                "viewer_definition_digest": locks["viewer_definition_digest"],
                "dependency_lock_map_digest": locks["dependency_lock_map_digest"],
            },
        )
        receipt_path = paths.plan_receipts / (
            f"{_short(plan_digest)}.{decision}.{_short(receipt['content_digest'])}.decision.json"
        )
        created = False
        try:
            _create_decision_receipt(receipt_path, receipt)
            created = True
            append_event(
                paths,
                "plan_decided",
                {"plan_digest": plan_digest, "decision": decision, "receipt_digest": receipt["content_digest"]},
            )
        except FileExistsError as exc:
            raise InvalidStateError("generation plan approval receipt already exists") from exc
        except Exception:
            if created:
                receipt_path.unlink(missing_ok=True)
            raise
        return {"approval_path": str(receipt_path), "approval_digest": receipt["content_digest"], "decision": decision}


def load_plan_approval(paths: WorkspacePaths, approval_path: str | Path) -> dict[str, Any]:
    path = require_within(paths.plan_receipts, approval_path, "plan approval")
    value = read_json(path)
    if not isinstance(value, Mapping):
        raise PlanError("plan approval is not an object")
    approval = verify_envelope(value, kind="plan_approval_decision")
    bound = approval["body"].get("bound_digests")
    if not isinstance(bound, Mapping):
        raise PlanError("plan approval lacks bound digests")
    plan_digest = bound.get("plan_digest")
    require_digest(plan_digest, "plan approval plan digest")
    with _plan_decision_lock(paths, plan_digest):
        current_value = read_json(path)
        if not isinstance(current_value, Mapping):
            raise PlanError("plan approval is not an object")
        current = verify_envelope(current_value, kind="plan_approval_decision")
        decisions = _prior_plan_decisions(paths, plan_digest)
        if len(decisions) != 1:
            raise InvalidStateError("generation plan does not have exactly one canonical approval decision")
        if decisions[0]["content_digest"] != current["content_digest"]:
            raise InvalidStateError("plan approval is not the current canonical decision")
        return current
