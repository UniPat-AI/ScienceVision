"""Map QA-Gen task records (QA-Gen/tasks/*/...final_tasks.jsonl) into Harbor
task directories, reusing the existing build_harbor_tasks generator.

QA-Gen records are *structurally close* to the walkingskeleton verify_tasks.json
this repo already lifts into Harbor, but a few fields are nested differently:

    QA-Gen record                       ->  flat task dict (build_harbor_tasks)
    asset.path                          ->  asset_path
    asset.format                        ->  asset_format
    tool_policy.allowed_tools           ->  allowed_tools
    tool_policy.forbidden_tools         ->  forbidden_tools
    ground_truth (with .value)          ->  ground_truth (kept; the verifier's
                                            task_metadata_to_taskspec reads .value)
    v_class (may be null on K-axis)     ->  v_class, else k_class, else "V1"

So this module is a thin field-flattener that feeds build_harbor_tasks.build_one
— scoring, weighting, and the host/containerized split are all unchanged.

Usage:
    python -m scripts.harbor.qa_gen_to_tasks \
        --jsonl QA-Gen/tasks/MedicalKAxisPilot/ScienceVision_MedicalKAxisPilot_final_tasks.jsonl \
        --jsonl QA-Gen/tasks/Genesis_VP_Hard/ScienceVision_Genesis_VP_Hard_final_tasks.jsonl \
        --out scripts/harbor/tasks-qagen [--containerized]
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path, PurePosixPath

from scripts.harbor._membership_rules import apply_membership
from scripts.harbor._tolerance_rules import merge_tolerances
from scripts.harbor.build_harbor_tasks import (
    CC2D_EXPECTED_EXCLUSION_CODES,
    RenderVisibilityError,
    build_one,
)
from scripts.harbor.eval_contracts import _gt_value, normalize_eval_contract

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_asset_path(asset: dict, asset_root: Path | None) -> str:
    """Resolve the asset to a path that exists on *this* machine.

    QA-Gen records carry both ``path`` (a container-absolute path like
    ``/datasets/...`` that does not exist on the host) and ``repo_path`` (the
    path relative to the repo root). When ``asset_root`` is given we prefer
    ``repo_path`` joined onto it, so the host-run agent can actually read the
    file; otherwise we fall back to the record's ``path``.
    """
    repo_path = asset.get("repo_path")
    if asset_root is not None and repo_path:
        return str((asset_root / repo_path).resolve())
    return str(asset.get("path", ""))


def _infer_asset_format(asset: dict) -> str:
    """Resolve an asset's format, falling back to its filename extension.

    Records that omit ``format`` used to default straight to ``obj``, which
    routes a molecular file down the mesh/trimesh branch and makes the task
    unrenderable for the CC2D family. The extension is a better fallback than a
    guess: ``.nii.gz`` keeps both suffixes because the render dispatch matches
    the compound form.
    """
    declared = asset.get("format") or asset.get("asset_format")
    if declared:
        return str(declared)
    name = PurePosixPath(str(asset.get("repo_path") or asset.get("path") or "")).name
    lowered = name.lower()
    if lowered.endswith(".nii.gz"):
        return "nii.gz"
    suffix = PurePosixPath(lowered).suffix.lstrip(".")
    return suffix or "obj"


def _resolve_mask_path(asset: dict, asset_root: Path | None) -> str | None:
    """Resolve the medical segmentation sidecar (mask), if the record has one.

    Same host-resolution rule as the primary asset: prefer ``mask_repo_path``
    joined onto ``asset_root`` so the file exists on this machine; else fall
    back to the container-absolute ``mask_path``. Returns None when absent.
    """
    mask_repo = asset.get("mask_repo_path")
    if asset_root is not None and mask_repo:
        return str((asset_root / mask_repo).resolve())
    return asset.get("mask_path") or None


def _scorer_with_tolerances(rec: dict) -> dict:
    """The record's scorer, with a per-field tolerance derived from its ground truth.

    A tree that declares no tolerance is not graded leniently — a float field
    falls through to ``exact`` and a list field becomes ``invalid_task_contract``,
    reward 0.0 whatever the agent answered. The derivation, and the fields it
    deliberately leaves alone, are documented in ``_tolerance_rules``.
    """
    scorer = dict(rec.get("scorer") or {"primary": "answer_score", "secondary": []})
    merged = merge_tolerances(scorer.get("tolerance"), _gt_value(rec))
    if merged:
        scorer["tolerance"] = merged
    return scorer


def qa_record_to_task(rec: dict, *, asset_root: Path | None = None) -> dict:
    """Flatten one QA-Gen record into the dict build_harbor_tasks expects."""
    # A record that declares a mask-membership contract gets its explicit
    # rubric item (and a ground truth trimmed to the answer schema) before
    # normalization -- the declaration is only intact on the raw record, and
    # the injected rubric is what makes it a rich contract. Every other record
    # passes through untouched.
    rec = apply_membership(rec, asset_root=asset_root)
    rec = normalize_eval_contract(rec)
    asset = rec.get("asset") or {}
    assets = rec.get("assets") if isinstance(rec.get("assets"), list) else []
    if not assets and asset:
        assets = [asset]
    tool_policy = rec.get("tool_policy") or {}
    # v_class is null for K-axis records; fall back to k_class, then V1.
    v_class = rec.get("v_class") or rec.get("k_class") or "V1"
    task = {
        "task_id": rec["task_id"],
        "domain": rec.get("domain", ""),
        "v_class": v_class,
        "instruction": rec.get("instruction") or rec.get("question") or "",
        "asset_path": _resolve_asset_path(asset, asset_root),
        "asset_format": _infer_asset_format(asset),
        "answer_schema": rec.get("answer_schema") or {"kind": "freeform"},
        "scorer": _scorer_with_tolerances(rec),
        "scorer_config": rec.get("scorer_config") or {},
        "allowed_tools": list(tool_policy.get("allowed_tools", []) or []),
        "forbidden_tools": list(tool_policy.get("forbidden_tools", []) or []),
        # Keep the full ground_truth dict; task_metadata_to_taskspec reads .value.
        "ground_truth": rec.get("ground_truth"),
        "evaluation_eligible": bool(rec.get("evaluation_eligible")),
        "evaluation_ineligible_reason": rec.get("evaluation_ineligible_reason", ""),
        "assets": [
            {
                **item,
                "path": _resolve_asset_path(item, asset_root),
                "asset_path": _resolve_asset_path(item, asset_root),
                "asset_format": _infer_asset_format(item),
            }
            for item in assets
            if isinstance(item, dict)
        ],
    }
    for key in (
        "mechanical_contract",
        "task_type",
        "asset_contract",
        "input_closure",
        "required_evidence",
        "viewer_requirements",
        "active_vision_evidence",
        "terminal_answer",
        "trace_requirements",
        "challenge_controls",
        "recompute_receipt",
        "provenance",
        "asset_grounding",
        "grounding_receipt",
        "grounding_receipts",
        "target_selection",
        "entity_selection",
        "render_contract",
        "asset_hashes",
    ):
        if rec.get(key) is not None:
            task[key] = rec[key]
    task["asset"] = {**asset, "path": task["asset_path"]}
    # mask sidecar: only carried when present (medical segmentation tasks). Used
    # by build_one's codex-mcp asset staging; ignored by other routes.
    mask_path = _resolve_mask_path(asset, asset_root)
    if mask_path:
        task["mask_path"] = mask_path
    target_selection = rec.get("target_selection")
    target_selection = target_selection if isinstance(target_selection, dict) else {}
    asset_extra = asset.get("extra") if isinstance(asset.get("extra"), dict) else {}
    mask_id = (
        rec.get("mask_id")
        if rec.get("mask_id") is not None
        else target_selection.get("mask_id")
        if target_selection.get("mask_id") is not None
        else asset.get("mask_id")
        if asset.get("mask_id") is not None
        else asset_extra.get("mask_id")
    )
    if mask_id is not None:
        task["mask_id"] = mask_id
    # 2D view-staging knobs (codex-2d): carry only when the record sets
    # them explicitly, else _stage_rendered_views falls back to its defaults.
    if rec.get("n_views") is not None:
        task["n_views"] = int(rec["n_views"])
    if rec.get("seed_pose") is not None:
        task["seed_pose"] = rec["seed_pose"]
    return task


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--jsonl",
        type=Path,
        action="append",
        required=True,
        help="QA-Gen ...final_tasks.jsonl (repeatable)",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--asset-root",
        type=Path,
        default=_REPO_ROOT,
        help="repo root to resolve asset.repo_path against so the host-run "
        "agent can read the file (default: the repo root). Pass empty to keep "
        "the record's container-absolute asset.path verbatim.",
    )
    ap.add_argument(
        "--containerized",
        action="store_true",
        help="emit the containerized route (separate verifier env + score.py)",
    )
    ap.add_argument(
        "--agent-env",
        choices=[
            "host", "codex", "codex-mcp", "codex-2d", "codex-text",
            "claude-code", "claude-code-mcp", "claude-code-2d", "claude-code-text",
        ],
        default="host",
        help="agent environment image for the containerized route: 'host' "
        "(python-slim, Sci3dAgent in-process), 'codex' (bare CLI, reads "
        "the 3D file directly), 'codex-mcp' (CLI + sciencevision MCP "
        "tools, asset at /data), or 'codex-2d' (baseline ①: vision agent "
        "over pre-rendered PNG views at /data, no raw 3D file, no MCP), or "
        "'codex-text' (QC probe: question text only, no files or images). Note: "
        "codex-2d generation must run in the mcp/GL env (offline render). "
        "The claude-code names are legacy reproduction routes only.",
    )
    ap.add_argument(
        "--n-views",
        type=int,
        choices=[1, 3, 6, 12],
        default=None,
        help="for --agent-env codex-2d: number of rendered views to "
        "stage (overrides per-record n_views)",
    )
    args = ap.parse_args()

    asset_root = args.asset_root if str(args.asset_root) else None

    records: list[dict] = []
    for jp in args.jsonl:
        records.extend(_load_jsonl(jp))

    args.out.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    ineligible: list[str] = []
    cc2d_excluded: list[dict] = []
    for rec in records:
        task = qa_record_to_task(rec, asset_root=asset_root)
        if not task.get("evaluation_eligible"):
            ineligible.append(task["task_id"])
            if Path(task["task_id"]).name != task["task_id"]:
                raise ValueError("task_id must be one path component")
            stale = args.out / task["task_id"]
            if stale.is_dir():
                shutil.rmtree(stale)
            continue
        if args.n_views is not None:
            task["n_views"] = args.n_views
        try:
            built.append(
                build_one(
                    task, args.out,
                    containerized=args.containerized,
                    agent_env=args.agent_env,
                )
            )
        except RenderVisibilityError as exc:
            # Expected cc2d exclusions become receipt lines; real renderer
            # faults (`renderer_exception`, blank output, leaks) still abort.
            if (not args.agent_env.endswith("-2d")
                    or str(exc) not in CC2D_EXPECTED_EXCLUSION_CODES):
                raise
            sidecar = args.out / f"{task['task_id']}.cc2d_excluded.json"
            if sidecar.is_file():
                cc2d_excluded.append(json.loads(sidecar.read_text()))
            else:
                cc2d_excluded.append({
                    "task_id": task["task_id"], "agent_env": args.agent_env,
                    "n_views": task.get("n_views"), "failure_reason": str(exc),
                })

    if cc2d_excluded:
        (args.out / "cc2d_exclusions.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in cc2d_excluded)
        )
        counts: dict[str, int] = {}
        for row in cc2d_excluded:
            counts[row["failure_reason"]] = counts.get(row["failure_reason"], 0) + 1
        print(f"excluded {len(cc2d_excluded)} cc2d task(s): {counts}")

    route = "containerized" if args.containerized else "host-run"
    print(
        f"generated {len(built)} eligible Harbor task(s) [{route}] from QA-Gen "
        f"under {args.out}; excluded {len(ineligible)} without deterministic GT"
    )
    for d in built:
        rel = d.relative_to(_REPO_ROOT) if _REPO_ROOT in d.parents else d
        print(f"  {rel}")


if __name__ == "__main__":
    main()
