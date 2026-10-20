#!/usr/bin/env python3
"""Build Harbor task trees from exported contracts and a dataset package.

``export_contracts.py`` records the parts of a prepared tree that the dataset
does not carry: asset roles, answer schema, scorer configuration and ground
truth. This script joins those contracts to a dataset root and emits the
``tasks-<route>`` tree that Harbor consumes.

Both dataset layouts used by the release are supported: the ScienceVision
250-task package (``<domain>/<task_id>/``) and any root that resolves the
declared ``repo_path`` directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.harbor.build_harbor_tasks import build_one  # noqa: E402
from scripts.harbor.qa_gen_to_tasks import qa_record_to_task  # noqa: E402

_AGENT_ENV = {
    "codex": {"cc": "codex", "ccmcp": "codex-mcp"},
    "claude-code": {"cc": "claude-code", "ccmcp": "claude-code-mcp"},
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolve_asset(
    asset: dict[str, Any],
    *,
    domain: str,
    task_id: str,
    dataset_root: Path,
) -> dict[str, Any]:
    declared = str(asset.get("repo_path") or "")
    name = PurePosixPath(declared).name if declared else ""
    if declared and (dataset_root / declared).is_file():
        return {**asset, "repo_path": declared}
    if name:
        for subdir in ("assets", "drawings"):
            base = dataset_root / domain / task_id / subdir
            if not base.is_dir():
                continue
            exact = base / name
            if exact.is_file():
                return {**asset, "repo_path": str(exact.relative_to(dataset_root))}
            nested = sorted(base.rglob(name))
            if nested:
                return {**asset, "repo_path": str(nested[0].relative_to(dataset_root))}
    raise FileNotFoundError(f"{task_id}: asset not found: {declared or name}")


def _package_instruction(dataset_root: Path, domain: str, task_id: str) -> str:
    path = dataset_root / domain / task_id / "normalized.json"
    if not path.is_file():
        return ""
    return str(json.loads(path.read_text(encoding="utf-8")).get("question") or "")


def _package_gold(dataset_root: Path, domain: str, task_id: str) -> Any:
    path = dataset_root / domain / task_id / "answer.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"value": payload.get("answer")}


def _package_scorer(dataset_root: Path, task_id: str, scorer: dict[str, Any]) -> dict[str, Any]:
    path = dataset_root / "verifier" / "scoring" / f"{task_id}.json"
    if not path.is_file():
        return scorer
    block = json.loads(path.read_text(encoding="utf-8")).get("scorer")
    return {**scorer, **block} if isinstance(block, dict) else scorer


def _record(
    contract: dict[str, Any],
    *,
    dataset_root: Path,
    instruction_source: str,
    gold_source: str,
) -> dict[str, Any]:
    domain = str(contract.get("domain") or "")
    task_id = str(contract["task_id"])
    assets = [
        _resolve_asset(asset, domain=domain, task_id=task_id, dataset_root=dataset_root)
        for asset in contract.get("assets") or []
        if isinstance(asset, dict)
    ]
    if not assets:
        raise ValueError(f"{task_id}: contract has no assets")

    instruction = str(contract.get("instruction") or "")
    ground_truth = contract.get("ground_truth")
    scorer = contract.get("scorer") or {"primary": "exact_match", "secondary": []}
    if instruction_source == "package":
        instruction = _package_instruction(dataset_root, domain, task_id) or instruction
    if gold_source == "package":
        ground_truth = _package_gold(dataset_root, domain, task_id) or ground_truth
        scorer = _package_scorer(dataset_root, task_id, scorer)

    return {
        **contract,
        "domain": domain,
        "instruction": instruction,
        "ground_truth": ground_truth,
        "scorer": scorer,
        "assets": assets,
        "asset": assets[0],
        "tool_policy": contract.get("tool_policy") or {"allowed_tools": [], "forbidden_tools": []},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--route", choices=["cc", "ccmcp"], required=True)
    parser.add_argument("--harness", choices=sorted(_AGENT_ENV), required=True)
    parser.add_argument("--instruction-source", choices=["contract", "package"], default="contract")
    parser.add_argument("--gold-source", choices=["contract", "package"], default="contract")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    agent_env = _AGENT_ENV[args.harness][args.route]
    out_dir = args.out / f"tasks-{args.route}"
    out_dir.mkdir(parents=True, exist_ok=True)

    contracts = _read_jsonl(args.contracts)
    if args.limit is not None:
        contracts = contracts[: args.limit]

    built: list[str] = []
    for contract in contracts:
        record = _record(
            contract,
            dataset_root=args.dataset_root,
            instruction_source=args.instruction_source,
            gold_source=args.gold_source,
        )
        task = qa_record_to_task(record, asset_root=args.dataset_root)
        build_one(task, out_dir, containerized=True, agent_env=agent_env)
        built.append(task["task_id"])

    print(
        json.dumps(
            {
                "status": "ok",
                "route": args.route,
                "harness": args.harness,
                "agent_env": agent_env,
                "tasks": len(built),
                "dir": str(out_dir),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
