#!/usr/bin/env python3
"""Prepare and optionally execute one V32 text-only conformation agent slot.

This command never stages or releases an artifact.  It creates a new,
one-attempt provider runtime from the approved local runtime, runs a
no-network gate, and invokes the four approved workers only when
``--execute`` is present.  Model prompts and outputs are private; stdout
contains only paths, statuses, and content digests.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from qagen.batch.text_only_pilot import (
    TextOnlyPilotInputs,
    create_one_slot_provider_runtime,
    execute_text_only_pilot,
    freeze_incomplete_text_only_pilot,
    prepare_text_only_pilot,
    run_no_network_gate,
)
from qagen.paths import WorkspacePaths


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _inputs(args: argparse.Namespace) -> TextOnlyPilotInputs:
    return TextOnlyPilotInputs(
        selection_manifest=_path(args.selection_manifest),
        child_target=_path(args.child_target),
        preauthor_anchor=_path(args.preauthor_anchor),
        pilot_result=_path(args.pilot_result),
        inventory=_path(args.inventory),
        first_receipt=_path(args.first_receipt),
        first_sidecar=_path(args.first_sidecar),
        first_leak_scan=_path(args.first_leak_scan),
        second_receipt=_path(args.second_receipt),
        second_sidecar=_path(args.second_sidecar),
        second_leak_scan=_path(args.second_leak_scan),
        replay_consistency=_path(args.replay_consistency),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--selection-manifest", required=True)
    parser.add_argument("--child-target", required=True)
    parser.add_argument("--preauthor-anchor", required=True)
    parser.add_argument("--pilot-result", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--first-receipt", required=True)
    parser.add_argument("--first-sidecar", required=True)
    parser.add_argument("--first-leak-scan", required=True)
    parser.add_argument("--second-receipt", required=True)
    parser.add_argument("--second-sidecar", required=True)
    parser.add_argument("--second-leak-scan", required=True)
    parser.add_argument("--replay-consistency", required=True)
    parser.add_argument(
        "--routing-policy",
        default=str(Path(__file__).resolve().parents[1] / "configs" / "agents" / "model_routing.v32-local-preproduction.yaml"),
    )
    parser.add_argument(
        "--source-runtime",
        default=str(Path(__file__).resolve().parents[1] / "configs" / "agents" / "provider_runtime.v32-local-preproduction.yaml"),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--freeze-incomplete",
        action="store_true",
        help="freeze the known interrupted historical run; it is never resumed",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    paths = WorkspacePaths.from_value(args.workspace)
    run_root = paths.private / "text-only-agent-pilots" / args.run_id
    if (run_root / "execution-plan.json").is_file():
        if args.freeze_incomplete:
            if args.execute:
                raise SystemExit("--freeze-incomplete cannot be combined with --execute")
            frozen = freeze_incomplete_text_only_pilot(paths, run_id=args.run_id)
            print(json.dumps({"frozen": frozen}, sort_keys=True))
            return 0
        if not args.execute:
            raise SystemExit("an existing prepared run is immutable; use --execute or --freeze-incomplete")
        if (run_root / "failure-summary.json").is_file():
            raise SystemExit("an incomplete frozen run cannot be resumed or executed")
        runtime_path = run_root / "runtime" / "provider-runtime.one-slot.yaml"
        approval_path = paths.root / "preproduction" / "text-only-agent-pilots" / args.run_id / "provider-endpoint-approval.one-slot.yaml"
        if not runtime_path.is_file() or not approval_path.is_file():
            raise SystemExit("prepared run is missing its locked one-slot runtime or endpoint approval")
        runtime = {"runtime_path": str(runtime_path), "approval_path": str(approval_path)}
        prepared: dict[str, Any] = {
            "run_id": args.run_id,
            "root": str(run_root),
            "reused_prepared_run": True,
        }
    else:
        if args.freeze_incomplete:
            raise SystemExit("--freeze-incomplete requires an existing interrupted run")
        endpoint = os.environ.get("QAGEN_UNIV_BASE_URL", "").strip()
        if not endpoint:
            raise SystemExit("QAGEN_UNIV_BASE_URL must be set before creating the one-slot runtime")
        runtime = create_one_slot_provider_runtime(
            paths,
            run_id=args.run_id,
            source_runtime_path=args.source_runtime,
            endpoint=endpoint,
        )
        prepared = prepare_text_only_pilot(
            paths,
            inputs=_inputs(args),
            routing_policy_path=args.routing_policy,
            runtime_config_path=runtime["runtime_path"],
            endpoint_approval_path=runtime["approval_path"],
            run_id=args.run_id,
        )
    provider_environ: dict[str, str] = dict(os.environ)
    provider_environ["QAGEN_UNIV_ENDPOINT_APPROVAL_PATH"] = runtime["approval_path"]
    gate_path = run_root / "no-network-gate.json"
    if gate_path.is_file():
        gate: dict[str, Any] = {"gate_path": str(gate_path), "reused_completed_gate": True}
    else:
        gate = run_no_network_gate(
            paths,
            run_id=args.run_id,
            routing_policy_path=args.routing_policy,
            runtime_config_path=runtime["runtime_path"],
            environ=provider_environ,
        )
    result: dict[str, Any] = {"prepared": prepared, "no_network_gate": gate}
    if args.execute:
        result["execution"] = execute_text_only_pilot(
            paths,
            run_id=args.run_id,
            routing_policy_path=args.routing_policy,
            runtime_config_path=runtime["runtime_path"],
            environ=provider_environ,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
