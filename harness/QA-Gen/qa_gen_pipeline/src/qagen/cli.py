"""Argparse CLI for the first local QA-Gen registry slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable

from .batch import approve_batch_plan, execute_batch, prepare_batch, resume_batch, start_batch
from .errors import ExitCode, QAGenError
from .config import build_config
from .chirality_pilot import build_chirality_pilot_viewer_profile
from .knowledge import approve_knowledge_snapshot, authorize_knowledge_compile, compile_knowledge_draft, sync_knowledge_sources
from .paths import WorkspacePaths
from .plan import approve_plan, create_plan
from .providers import diagnose_provider_runtime
from .release import export_release, stage_release
from .review import decide_review
from .run import execute_run, resume_run, start_run
from .selection import compile_selection, import_selection
from .seeds import accept_source_version, sync_seed_package
from .viewer import (
    MolstarChiralityHostAdapter,
    build_chirality_scene_registry,
    build_viewer_profile,
    diagnose_chirality_host,
    diagnose_viewer_adapter,
    load_viewer_profile,
    persist_chirality_smoke_receipt,
    revalidate_viewer_profile,
    verify_viewer_evidence_chain,
)


Handler = Callable[[argparse.Namespace], tuple[dict[str, Any], ExitCode]]


def _workspace(args: argparse.Namespace) -> WorkspacePaths:
    return WorkspacePaths.from_value(args.workspace)


def _seed_sync(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = sync_seed_package(_workspace(args), args.package_root, source_spec_path=args.source_spec)
    return result, ExitCode.OK if result["eligible"] else ExitCode.GATE_PENDING


def _seed_accept_version(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = accept_source_version(
        _workspace(args),
        request_path=args.request,
        reviewer=args.reviewer,
        resolved_version=args.resolved_version,
        decision=args.decision,
        rationale=args.rationale,
    )
    return result, ExitCode.OK


def _selection_import(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        import_selection(
            _workspace(args),
            selection_path=args.selection,
            asset_root=args.asset_root,
            source_spec_path=args.source_spec,
        ),
        ExitCode.OK,
    )


def _selection_compile(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = compile_selection(
        _workspace(args),
        selection_import_path=args.selection_import,
        taxonomy_mapping_path=args.taxonomy_mapping,
    )
    return result, ExitCode.OK if result["generation_eligible"] else ExitCode.GATE_PENDING


def _batch_prepare(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        prepare_batch(
            _workspace(args),
            selection_batch_path=args.selection_batch,
            lock_bundle_path=args.lock_bundle,
        ),
        ExitCode.OK,
    )


def _batch_approve(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        approve_batch_plan(
            _workspace(args),
            plan_path=args.plan,
            reviewer=args.reviewer,
            decision=args.decision,
            rationale=args.rationale,
        ),
        ExitCode.OK,
    )


def _batch_start(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        start_batch(_workspace(args), plan_path=args.plan, approval_path=args.approval),
        ExitCode.OK,
    )


def _batch_resume(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return resume_batch(_workspace(args), run_id=args.run_id), ExitCode.OK


def _batch_execute(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    live = bool(args.live)
    result = execute_batch(
        _workspace(args),
        run_id=args.run_id,
        # The CLI deliberately defaults to dry-run.  ``--live`` is the only
        # opt-in path that can construct an HTTP provider adapter.
        dry_run=bool(args.dry_run) or not live,
        live=live,
        runtime_config_path=args.provider_runtime,
        limit=args.limit,
        revision_receipt_path=args.revision_receipt,
    )
    if not live:
        # A dry-run never creates a candidate, including when diagnostic inputs
        # are ready. It is therefore deliberately a pending gate.
        return result, ExitCode.GATE_PENDING
    # A runtime block or a preflight-only result is still a gate. A verifier
    # pass remains review-pending, but the bounded live execution itself ran.
    return (
        result,
        ExitCode.OK if result.get("status") == "live_complete_pending_human_review" else ExitCode.GATE_PENDING,
    )


def _knowledge_sync(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return sync_knowledge_sources(_workspace(args), args.source), ExitCode.OK


def _knowledge_authorize(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = authorize_knowledge_compile(
        _workspace(args),
        leaf=args.leaf,
        expert_source_ids=args.expert_source_id,
        taxonomy_source_ids=args.taxonomy_source_id,
        feedback_source_ids=args.feedback_source_id,
        owner=args.owner,
        model=args.model,
        max_tokens=args.max_tokens,
        max_cost_usd=args.max_cost_usd,
        ttl_seconds=args.ttl_seconds,
    )
    return result, ExitCode.OK


def _knowledge_compile(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = compile_knowledge_draft(
        _workspace(args),
        authorization_path=args.authorization,
        worker=args.worker,
        model=args.model,
    )
    return result, ExitCode.OK


def _knowledge_approve(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = approve_knowledge_snapshot(
        _workspace(args),
        draft_path=args.draft,
        reviewer=args.reviewer,
        decision=args.decision,
        rationale=args.rationale,
    )
    return result, ExitCode.OK


def _viewer_profile(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        build_viewer_profile(
            _workspace(args),
            definition_path=args.definition,
            session_evidence_path=args.session_evidence,
        ),
        ExitCode.OK,
    )


def _viewer_chirality_profile(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        build_chirality_pilot_viewer_profile(_workspace(args), registry_path=args.registry),
        ExitCode.OK,
    )


def _viewer_diagnose(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    profile = load_viewer_profile(_workspace(args), args.viewer_profile) if args.viewer_profile else None
    result = diagnose_viewer_adapter(args.adapter_config, viewer_profile=profile)
    return result, ExitCode.OK if result["production_ready"] else ExitCode.GATE_PENDING


def _viewer_chirality_prepare(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    paths = _workspace(args)
    result = build_chirality_scene_registry(
        paths,
        package_root=args.package_root,
        source_spec_path=args.source_spec,
        seed_task_id=args.seed_task_id,
    )
    if result["runtime_eligible"]:
        profile = build_chirality_pilot_viewer_profile(paths, registry_path=str(result["registry_path"]))
        result = {
            **result,
            "viewer_profile_path": profile["viewer_profile_path"],
            "viewer_profile_digest": profile["viewer_profile_digest"],
            "viewer_profile_status": profile["status"],
        }
    return result, ExitCode.OK if result["runtime_eligible"] else ExitCode.GATE_PENDING


def _viewer_chirality_diagnose(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = diagnose_chirality_host(_workspace(args), args.registry)
    return result, ExitCode.GATE_PENDING


def _observation_payload(observation: Any) -> dict[str, Any]:
    return {
        "operation": observation.operation,
        "sequence": observation.sequence,
        "payload": dict(observation.payload),
        "trace_digest": observation.trace_digest,
    }


def _viewer_chirality_smoke(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    paths = _workspace(args)
    profile_path = Path(args.viewer_profile).expanduser().resolve()
    profile = revalidate_viewer_profile(load_viewer_profile(paths, profile_path))
    if args.viewer_profile_digest is not None and args.viewer_profile_digest != profile["content_digest"]:
        raise QAGenError("--viewer-profile-digest does not match the persisted ViewerProfile")
    adapter = MolstarChiralityHostAdapter.from_profile_path(paths, profile_path)
    if args.registry is not None and Path(args.registry).expanduser().resolve() != adapter.registry.path:
        adapter.shutdown()
        raise QAGenError("--registry does not match the SceneRegistry bound by --viewer-profile")
    session = None
    closed = False
    observations: list[dict[str, Any]] = []
    try:
        session = adapter.open_session(
            anonymous_scene_ref=args.scene_ref,
            request={
                "request_id": "chirality-pilot-smoke",
                "viewer_profile_digest": profile["content_digest"],
                "allowed_operations": ["rotate", "render"],
            },
        )
        observations.append(_observation_payload(adapter.perform(session=session, operation="render", arguments={})))
        observations.append(
            _observation_payload(
                adapter.perform(
                    session=session,
                    operation="rotate",
                    arguments={
                        "d_azimuth_deg": args.azimuth,
                        "d_elevation_deg": args.elevation,
                        "d_roll_deg": args.roll,
                    },
                )
            )
        )
        adapter.close_session(session=session)
        closed = True
        verification = verify_viewer_evidence_chain(
            paths,
            session.session_id,
            viewer_profile_path=profile_path,
        )
        smoke = persist_chirality_smoke_receipt(
            paths,
            viewer_profile_path=profile_path,
            session_id=session.session_id,
        )
        return (
            {
                "artifact_kind": "chirality_host_smoke",
                "status": "pilot_smoke_succeeded_local_only",
                "production_ready": False,
                "session_id": session.session_id,
                "observations": observations,
                "evidence_chain": verification,
                "viewer_profile_path": str(profile_path),
                "viewer_profile_digest": profile["content_digest"],
                "smoke_receipt_path": smoke["smoke_receipt_path"],
                "smoke_receipt_digest": smoke["smoke_receipt_digest"],
                "version_resolution": adapter.registry.resolution.public(),
                "blockers": ["source_spec_local_only"],
            },
            ExitCode.OK,
        )
    finally:
        if session is not None and not closed:
            adapter.close_session(session=session)
        adapter.shutdown()


def _viewer_chirality_verify(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        verify_viewer_evidence_chain(
            _workspace(args),
            args.session_id,
            viewer_profile_path=args.viewer_profile,
        ),
        ExitCode.OK,
    )


def _config_build(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = build_config(_workspace(args), profile_path=args.profile)
    return result, ExitCode.OK if result["status"] != "blocked" else ExitCode.GATE_PENDING


def _plan_create(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        create_plan(
            _workspace(args),
            config_path=args.config,
            viewer_profile_path=args.viewer_profile,
            count=args.count,
        ),
        ExitCode.OK,
    )


def _plan_approve(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        approve_plan(
            _workspace(args),
            plan_path=args.plan,
            reviewer=args.reviewer,
            decision=args.decision,
            rationale=args.rationale,
        ),
        ExitCode.OK,
    )


def _run_start(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return start_run(_workspace(args), plan_path=args.plan, approval_path=args.approval), ExitCode.OK


def _run_resume(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return resume_run(_workspace(args), run_id=args.run_id), ExitCode.OK


def _run_execute(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        execute_run(
            _workspace(args),
            run_id=args.run_id,
            work_item_id=args.work_item,
            revision_receipt_path=args.revision_receipt,
        ),
        ExitCode.OK,
    )


def _review_decide(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return (
        decide_review(
            _workspace(args),
            run_id=args.run_id,
            candidate_path=args.candidate,
            evidence_path=args.evidence,
            reviewer=args.reviewer,
            decision=args.decision,
            rationale=args.rationale,
        ),
        ExitCode.OK,
    )


def _release_stage(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return stage_release(_workspace(args), run_id=args.run_id, candidate_paths=args.candidate), ExitCode.OK


def _release_export(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    return export_release(_workspace(args), run_id=args.run_id, output_dir=args.output_dir), ExitCode.OK


def _provider_diagnose(args: argparse.Namespace) -> tuple[dict[str, Any], ExitCode]:
    result = diagnose_provider_runtime(args.runtime_config, smoke=args.smoke)
    return result, ExitCode.OK if result["execution_eligible"] else ExitCode.GATE_PENDING


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=Path, help="explicit local workspace; runtime state is written below .qagen/")


def _add_seed_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    seed = subparsers.add_parser("seed", help="local EV2 Seed Registry")
    seed_subparsers = seed.add_subparsers(dest="seed_command", required=True)

    sync = seed_subparsers.add_parser("sync", help="import only a local EV2 package matched by an authorized SourceSpec")
    _add_workspace(sync)
    sync.add_argument(
        "--package-root",
        required=True,
        type=Path,
        help="local EV2 package root; must resolve exactly to the SourceSpec root_env value",
    )
    sync.add_argument(
        "--source-spec",
        required=True,
        type=Path,
        help="committed qagen_seed_source_spec that locks root_env, file roles, inventory, review distribution, and provenance",
    )
    sync.set_defaults(handler=_seed_sync)

    accept = seed_subparsers.add_parser("accept-version", help="record the human resolution for a version-conflict request")
    _add_workspace(accept)
    accept.add_argument("--request", required=True, type=Path, help="pending source-version request emitted by seed sync")
    accept.add_argument("--reviewer", required=True, help="human reviewer identity")
    accept.add_argument("--resolved-version", required=True, help="one observed version label to treat as canonical")
    accept.add_argument("--decision", choices=("accept", "reject"), default="accept")
    accept.add_argument("--rationale", default="", help="optional decision rationale")
    accept.set_defaults(handler=_seed_accept_version)


def _add_selection_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    selection = subparsers.add_parser(
        "selection",
        help="attest frozen external selections and compile reviewed vNext batch manifests",
    )
    selection_subparsers = selection.add_subparsers(dest="selection_command", required=True)

    import_parser = selection_subparsers.add_parser(
        "import",
        help="schema-validate a frozen selection and attest every referenced primary/mask asset",
    )
    _add_workspace(import_parser)
    import_parser.add_argument("--selection", required=True, type=Path, help="frozen selection JSON input")
    import_parser.add_argument(
        "--asset-root",
        required=True,
        type=Path,
        help="local bundle root containing selection-relative assets; the whole tree is integrity-snapshotted",
    )
    import_parser.add_argument(
        "--source-spec",
        required=True,
        type=Path,
        help="committed qagen_selection_source_spec locking the selection fingerprint and package basename",
    )
    import_parser.set_defaults(handler=_selection_import)

    compile_parser = selection_subparsers.add_parser(
        "compile",
        help="bind approved taxonomy mappings to attested source slots; never enables generation",
    )
    _add_workspace(compile_parser)
    compile_parser.add_argument(
        "--selection-import",
        required=True,
        type=Path,
        help="private selection_import artifact emitted by selection import",
    )
    compile_parser.add_argument(
        "--taxonomy-mapping",
        required=True,
        type=Path,
        help="human-approved qagen_selection_taxonomy_mapping YAML or JSON",
    )
    compile_parser.set_defaults(handler=_selection_compile)
    _add_batch_commands(selection_subparsers)


def _add_batch_commands(selection_subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    batch = selection_subparsers.add_parser(
        "batch",
        help="prepare digest-bound compiled-selection jobs and run explicit local-pilot execution",
    )
    batch_subparsers = batch.add_subparsers(dest="batch_command", required=True)

    prepare = batch_subparsers.add_parser(
        "prepare",
        help="bind every compiled selection slot to approved mapping, knowledge, seed, viewer, and routing locks",
    )
    _add_workspace(prepare)
    prepare.add_argument(
        "--selection-batch",
        required=True,
        type=Path,
        help="private vnext_selection_batch_manifest emitted by selection compile",
    )
    prepare.add_argument(
        "--lock-bundle",
        required=True,
        type=Path,
        help="human-approved qagen_compiled_selection_batch_locks YAML or JSON",
    )
    prepare.set_defaults(handler=_batch_prepare)

    approve = batch_subparsers.add_parser("approve", help="record immutable human approval for a prepared batch plan")
    _add_workspace(approve)
    approve.add_argument("--plan", required=True, type=Path, help="private compiled-selection batch plan")
    approve.add_argument("--reviewer", required=True, help="human plan owner identity")
    approve.add_argument("--decision", choices=("approved", "revise", "rejected", "quarantined"), required=True)
    approve.add_argument("--rationale", default="", help="required for revise/rejected")
    approve.set_defaults(handler=_batch_approve)

    start = batch_subparsers.add_parser("start", help="create a persistent resumable run from an approved batch plan")
    _add_workspace(start)
    start.add_argument("--plan", required=True, type=Path, help="private compiled-selection batch plan")
    start.add_argument("--approval", required=True, type=Path, help="bound approved batch-plan decision")
    start.set_defaults(handler=_batch_start)

    resume = batch_subparsers.add_parser("resume", help="revalidate immutable locks and resume diagnostic state")
    _add_workspace(resume)
    resume.add_argument("--run-id", required=True, help="existing compiled-selection batch run")
    resume.set_defaults(handler=_batch_resume)

    execute = batch_subparsers.add_parser(
        "execute",
        help="default to no-network diagnostics; --live enables approved local-pilot worker execution",
    )
    _add_workspace(execute)
    execute.add_argument("--run-id", required=True, help="existing compiled-selection batch run")
    mode = execute.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="record a no-network diagnostic (the default)")
    mode.add_argument("--live", action="store_true", help="run only through an approved local HTTPS provider runtime")
    execute.add_argument(
        "--provider-runtime",
        type=Path,
        help="required for --live; optional for a no-network diagnostic",
    )
    execute.add_argument("--limit", type=int, help="optional positive number of queued/resumable jobs to diagnose")
    execute.add_argument(
        "--revision-receipt",
        type=Path,
        help="local human qagen_revision_receipt; valid only with --live and triggers a rewriter retry path",
    )
    execute.set_defaults(handler=_batch_execute)


def _add_knowledge_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    knowledge = subparsers.add_parser("knowledge", help="local Knowledge Registry; never fetches live Feishu content")
    knowledge_subparsers = knowledge.add_subparsers(dest="knowledge_command", required=True)

    sync = knowledge_subparsers.add_parser(
        "sync", help="snapshot local expert, taxonomy, and typed feedback exports into a source manifest"
    )
    _add_workspace(sync)
    sync.add_argument(
        "--source",
        action="append",
        required=True,
        help="repeatable local source spec: expert:<id>:<path>, taxonomy:<id>:<path>, or feedback:<id>:<path>",
    )
    sync.set_defaults(handler=_knowledge_sync)

    authorize = knowledge_subparsers.add_parser("authorize", help="human knowledge-owner authorization for a draft-only compile")
    _add_workspace(authorize)
    authorize.add_argument("--leaf", required=True, help="leaf id, for example biology.docking_pose_plausibility")
    authorize.add_argument("--expert-source-id", action="append", required=True, help="repeatable synchronized expert source id")
    authorize.add_argument("--taxonomy-source-id", action="append", required=True, help="repeatable synchronized taxonomy source id")
    authorize.add_argument(
        "--feedback-source-id",
        action="append",
        default=[],
        help="optional repeatable accepted typed expert-feedback source id",
    )
    authorize.add_argument("--owner", required=True, help="knowledge owner identity")
    authorize.add_argument("--model", default="deterministic-local", help="allowed model id; current adapter is local deterministic")
    authorize.add_argument("--max-tokens", type=int, default=0, help="maximum tokens reserved for the worker")
    authorize.add_argument("--max-cost-usd", type=float, default=0.0, help="maximum USD cost reserved for the worker")
    authorize.add_argument("--ttl-seconds", type=int, default=3600, help="authorization lifetime in seconds")
    authorize.set_defaults(handler=_knowledge_authorize)

    compile_parser = knowledge_subparsers.add_parser("compile", help="run the deterministic knowledge_curator adapter; creates a draft only")
    _add_workspace(compile_parser)
    compile_parser.add_argument("--authorization", required=True, type=Path, help="authorization receipt from knowledge authorize")
    compile_parser.add_argument("--worker", default="knowledge_curator", help="must appear in the authorization worker allowlist")
    compile_parser.add_argument("--model", default="deterministic-local", help="must appear in the authorization model allowlist")
    compile_parser.set_defaults(handler=_knowledge_compile)

    approve = knowledge_subparsers.add_parser("approve", help="record the domain-expert decision; only approved drafts freeze/index")
    _add_workspace(approve)
    approve.add_argument("--draft", required=True, type=Path, help="draft generated by knowledge compile")
    approve.add_argument("--reviewer", required=True, help="domain expert identity")
    approve.add_argument("--decision", choices=("approved", "revise", "rejected", "quarantined"), required=True)
    approve.add_argument("--rationale", default="", help="required operationally for non-approved decisions")
    approve.set_defaults(handler=_knowledge_approve)


def _add_viewer_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    viewer = subparsers.add_parser("viewer", help="build a local digest-bound controlled ViewerProfile")
    viewer_subparsers = viewer.add_subparsers(dest="viewer_command", required=True)
    profile = viewer_subparsers.add_parser("profile", help="consume local definition and session evidence; never opens a browser")
    _add_workspace(profile)
    profile.add_argument("--definition", required=True, type=Path, help="local viewer definition fixture or source config")
    profile.add_argument("--session-evidence", required=True, type=Path, help="local controlled-session evidence JSON/YAML")
    profile.set_defaults(handler=_viewer_profile)
    chirality_profile = viewer_subparsers.add_parser(
        "chirality-profile",
        help="persist the local SceneRegistry-bound ViewerProfile for the Mol* chirality host",
    )
    _add_workspace(chirality_profile)
    chirality_profile.add_argument(
        "--registry",
        required=True,
        type=Path,
        help="private provenance-bound SceneRegistry created by viewer chirality-prepare",
    )
    chirality_profile.set_defaults(handler=_viewer_chirality_profile)
    diagnose = viewer_subparsers.add_parser(
        "diagnose",
        help="inspect an adapter config and backend contract without opening a scene",
    )
    _add_workspace(diagnose)
    diagnose.add_argument("--adapter-config", required=True, type=Path, help="local qagen_viewer_adapter_config YAML")
    diagnose.add_argument(
        "--viewer-profile",
        type=Path,
        help="optional persisted ViewerProfile to include in the compatibility diagnosis",
    )
    diagnose.set_defaults(handler=_viewer_diagnose)
    prepare = viewer_subparsers.add_parser(
        "chirality-prepare",
        help="build one private, provenance-bound Mol* scene mapping for the local pilot",
    )
    _add_workspace(prepare)
    prepare.add_argument("--package-root", required=True, type=Path, help="authorized local EV2 package root")
    prepare.add_argument("--source-spec", required=True, type=Path, help="committed EV2 SourceSpec")
    prepare.add_argument("--seed-task-id", required=True, help="host-only verified chirality seed selector")
    prepare.set_defaults(handler=_viewer_chirality_prepare)
    chirality_diagnose = viewer_subparsers.add_parser(
        "chirality-diagnose",
        help="diagnose the private chirality registry and local browser runtime without opening a scene",
    )
    _add_workspace(chirality_diagnose)
    chirality_diagnose.add_argument("--registry", required=True, type=Path, help="private scene registry")
    chirality_diagnose.set_defaults(handler=_viewer_chirality_diagnose)
    smoke = viewer_subparsers.add_parser(
        "chirality-smoke",
        help="open, render, rotate, and attest the registered scene through the controlled host",
    )
    _add_workspace(smoke)
    smoke.add_argument(
        "--viewer-profile",
        required=True,
        type=Path,
        help="persisted SceneRegistry-bound ViewerProfile created by chirality-prepare",
    )
    smoke.add_argument(
        "--registry",
        type=Path,
        help="optional compatibility check; must equal the SceneRegistry bound by --viewer-profile",
    )
    smoke.add_argument("--scene-ref", required=True, help="anonymous scene reference")
    smoke.add_argument(
        "--viewer-profile-digest",
        help="optional compatibility check; must equal --viewer-profile's digest",
    )
    smoke.add_argument("--azimuth", type=float, default=25.0)
    smoke.add_argument("--elevation", type=float, default=10.0)
    smoke.add_argument("--roll", type=float, default=0.0)
    smoke.set_defaults(handler=_viewer_chirality_smoke)
    verify = viewer_subparsers.add_parser(
        "chirality-verify",
        help="verify a persisted private session, render, and action-receipt chain",
    )
    _add_workspace(verify)
    verify.add_argument("--session-id", required=True, help="opaque viewer session id")
    verify.add_argument(
        "--viewer-profile",
        type=Path,
        help="optional profile-path equality check in addition to the profile path embedded in the session evidence",
    )
    verify.set_defaults(handler=_viewer_chirality_verify)


def _add_config_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = subparsers.add_parser("config", help="strict profile-driven config compiler")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    build = config_subparsers.add_parser("build", help="schema validate declared local inputs and compile eligible/blocked leaves")
    _add_workspace(build)
    build.add_argument("--profile", required=True, type=Path, help="local qagen run profile YAML")
    build.set_defaults(handler=_config_build)
    diagnose = config_subparsers.add_parser(
        "diagnose-providers",
        help="validate env-name references only; never contacts a provider or enables routing",
    )
    diagnose.add_argument(
        "--runtime-config",
        required=True,
        type=Path,
        help="private qagen_provider_runtime YAML with environment-variable names only",
    )
    diagnose.add_argument(
        "--smoke",
        action="store_true",
        help="run fixed no-task-content probes only when the runtime and credential are ready",
    )
    diagnose.set_defaults(handler=_provider_diagnose)


def _add_plan_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plan = subparsers.add_parser("plan", help="create and human-approve digest-bound generation plans")
    plan_subparsers = plan.add_subparsers(dest="plan_command", required=True)
    create = plan_subparsers.add_parser("create", help="select config/session-compatible eligible leaves only")
    _add_workspace(create)
    create.add_argument("--config", required=True, type=Path, help="compiled config artifact")
    create.add_argument("--viewer-profile", required=True, type=Path, help="local ViewerProfile artifact")
    create.add_argument(
        "--count",
        type=int,
        help="work items per eligible leaf; only the chirality local provider pilot accepts 5 through 10",
    )
    create.set_defaults(handler=_plan_create)
    approve = plan_subparsers.add_parser("approve", help="record an immutable PlanApprovalDecision")
    _add_workspace(approve)
    approve.add_argument("--plan", required=True, type=Path, help="generation plan artifact")
    approve.add_argument("--reviewer", required=True, help="human plan owner identity")
    approve.add_argument("--decision", choices=("approved", "revise", "rejected", "quarantined"), required=True)
    approve.add_argument("--rationale", default="", help="required for revise/rejected")
    approve.set_defaults(handler=_plan_approve)


def _add_run_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run = subparsers.add_parser("run", help="persistent run control plane with lock-aware resume")
    run_subparsers = run.add_subparsers(dest="run_command", required=True)
    start = run_subparsers.add_parser("start", help="start only from an approved digest-bound plan")
    _add_workspace(start)
    start.add_argument("--plan", required=True, type=Path, help="approved generation plan")
    start.add_argument("--approval", required=True, type=Path, help="PlanApprovalDecision artifact")
    start.set_defaults(handler=_run_start)
    resume = run_subparsers.add_parser("resume", help="revalidate all locks before resuming a local run")
    _add_workspace(resume)
    resume.add_argument("--run-id", required=True, help="existing local run id")
    resume.set_defaults(handler=_run_resume)
    execute = run_subparsers.add_parser(
        "execute",
        help="execute only persisted, approved fixture-stage work items after lock revalidation",
    )
    _add_workspace(execute)
    execute.add_argument("--run-id", required=True, help="existing approved local run id")
    execute.add_argument("--work-item", help="one approved plan leaf id; omit to execute all unrun approved leaves")
    execute.add_argument(
        "--revision-receipt",
        type=Path,
        help="persisted revision receipt emitted by review decide; invokes only the one allowed rewrite",
    )
    execute.set_defaults(handler=_run_execute)


def _add_review_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    review = subparsers.add_parser("review", help="human domain review of private run candidates")
    review_subparsers = review.add_subparsers(dest="review_command", required=True)
    decide = review_subparsers.add_parser("decide", help="bind candidate/evidence/locks in a DomainReviewDecision")
    _add_workspace(decide)
    decide.add_argument("--run-id", required=True, help="running local run id")
    decide.add_argument("--candidate", required=True, type=Path, help="private candidate under the run")
    decide.add_argument("--evidence", required=True, type=Path, help="private evidence under the run")
    decide.add_argument("--reviewer", required=True, help="human domain expert identity")
    decide.add_argument("--decision", choices=("approved", "revise", "rejected", "quarantined"), required=True)
    decide.add_argument("--rationale", default="", help="required for revise/rejected")
    decide.set_defaults(handler=_review_decide)


def _add_release_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    release = subparsers.add_parser("release", help="stage approved candidates and explicitly export only production-ready bundles")
    release_subparsers = release.add_subparsers(dest="release_command", required=True)
    stage = release_subparsers.add_parser("stage", help="stage internally; fixture profiles never production-export")
    _add_workspace(stage)
    stage.add_argument("--run-id", required=True, help="running local run id")
    stage.add_argument("--candidate", action="append", required=True, type=Path, help="approved private candidate under the run; repeatable")
    stage.set_defaults(handler=_release_stage)
    export = release_subparsers.add_parser("export", help="copy a leakage-checked production solver/scorer bundle to a new explicit directory")
    _add_workspace(export)
    export.add_argument("--run-id", required=True, help="running local run id")
    export.add_argument("--output-dir", required=True, type=Path, help="new empty directory outside .qagen for the stable release bundle")
    export.set_defaults(handler=_release_export)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-gen",
        description=(
            "ScienceVision QA-Gen vNext local control-plane CLI. External network access and legacy generator imports are disabled; browser control is limited to the private local viewer host."
        ),
    )
    parser.add_argument("--version", action="version", version="sciencevision-qa-gen 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_seed_commands(subparsers)
    _add_selection_commands(subparsers)
    _add_knowledge_commands(subparsers)
    _add_viewer_commands(subparsers)
    _add_config_commands(subparsers)
    _add_plan_commands(subparsers)
    _add_run_commands(subparsers)
    _add_review_commands(subparsers)
    _add_release_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handler: Handler = args.handler
        result, exit_code = handler(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return int(exit_code)
    except QAGenError as exc:
        print(f"qa-gen: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except FileNotFoundError as exc:
        print(f"qa-gen: file not found: {exc.filename or exc}", file=sys.stderr)
        return int(ExitCode.INVALID_INPUT)
    except KeyboardInterrupt:
        print("qa-gen: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # Defensive boundary for a CLI that must not silently succeed.
        print(f"qa-gen: unexpected failure: {exc}", file=sys.stderr)
        return int(ExitCode.UNEXPECTED)
