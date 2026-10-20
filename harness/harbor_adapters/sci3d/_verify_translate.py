"""Translate Harbor task metadata + agent artifacts into the shapes the
sci3d scorers consume, and synthesize process / weighted rewards.

Pure functions, no Harbor imports, no side effects beyond reading the two
JSON files the agent already wrote — so the mapping is unit-testable without
an environment or an LLM. This is the *single* place the verifier couples to
the sci3d ``TaskSpec`` schema; mirror of ``_translate.py`` on the agent side.

Data flow consumed here:
    Harbor ``task.config.task.metadata`` (a dict) -> sci3d ``TaskSpec``
    agent_dir/sci3d_answer.json  ({"answer": ...})      -> _TrajView.final_answer
    agent_dir/sci3d_trajectory.json (Harbor ATIF)        -> _TrajView.tool_names
The sci3d scorers (``sci3d_bench.scorers``) read TaskSpec.question /
answer_schema.kind / ground_truth.value / v_class and ``traj.final_answer``
(mask_dice additionally reads ``final_answer["mask_path"]``); _TrajView
exposes exactly those.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any

from harbor_adapters.sci3d._tool_names import (
    GENERIC_HARNESS_TOOLS,
    normalize_tool_name,
)
from harbor_adapters.sci3d._tool_outcomes import (
    merge_call_records,
    parse_calls_from_path,
    parse_codex_session_calls,
)

# ── reward key conventions ─────────────────────────────────────────────── #
# Process-signal rewards (closed-loop usage) all share this prefix so the
# weighted aggregator and downstream metrics can split content vs process.
PROCESS_PREFIX = "process_"
WEIGHTED_KEY = "reward"
DEFAULT_WEIGHTS = {"answer": 1.0, "process": 0.0}

#: The sentinel ``scripts.harbor.build_harbor_tasks`` writes wherever the
#: metadata carries a null (TOML has no null). Kept in sync by
#: ``tests/harbor_adapters/test_null_sentinel_roundtrip.py``.
NULL_SENTINEL = "__sv_null__"


def _restore_nulls(value: Any) -> Any:
    """Map the TOML null sentinel back to ``None``, recursively.

    Anything else passes through untouched, so a genuine string that merely
    contains the sentinel is never mistaken for a null.
    """
    if value == NULL_SENTINEL:
        return None
    if isinstance(value, dict):
        return {key: _restore_nulls(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_restore_nulls(child) for child in value]
    return value


# ── Harbor metadata -> sci3d TaskSpec ──────────────────────────────────── #
def _schema_init(cls: Any, **values: Any) -> Any:
    """Construct a schema dataclass across pinned sci3d schema revisions.

    The Harbor metadata is intentionally richer than the minimal public
    ``TaskSpec`` in older scorer images.  Passing every rich field directly
    made scoring fail before the answer scorer ran whenever the image carried
    the compact ``AssetRef(path, format, sha256)`` shape.  Filter only by the
    installed dataclass's declared fields: required common fields still pass
    through, while richer optional provenance remains in ``raw_task``.
    """
    allowed = {item.name for item in dataclass_fields(cls)}
    return cls(**{key: value for key, value in values.items() if key in allowed})


def task_metadata_to_taskspec(
    meta: dict, *, task_id: str, instruction: str
) -> Any:
    """Build a sci3d ``TaskSpec`` from Harbor task metadata + instruction.

    ``meta`` is the ``[metadata]`` table from task.toml (a plain dict). We
    import the sci3d schema lazily so this module stays importable on the
    Harbor side without pulling sci3d in until a verify actually runs.
    """
    from sci3d_bench.task_schema import (
        AnswerSchema,
        AssetRef,
        GroundTruth,
        Provenance,
        ScorerSpec,
        TaskSpec,
    )

    asset_data = meta.get("asset") if isinstance(meta.get("asset"), dict) else {}
    asset = _schema_init(
        AssetRef,
        path=str(asset_data.get("path") or meta.get("asset_path", "")),
        format=str(asset_data.get("format") or meta.get("asset_format", "obj")),
        sha256=asset_data.get("sha256"),
        asset_id=asset_data.get("asset_id"),
        role=asset_data.get("role"),
        viewer=asset_data.get("viewer"),
        repo_path=asset_data.get("repo_path"),
    )

    ans = meta.get("answer_schema") or {}
    raw_kind = str(ans.get("kind", "freeform"))
    kind = raw_kind if raw_kind == "mechanical_structured" else (
        "freeform" if raw_kind == "freeform" else "structured"
    )
    fields = ans.get("fields")
    answer_schema = _schema_init(
        AnswerSchema,
        kind=kind,
        fields=fields,
        field_specs=ans.get("field_specs") if isinstance(ans.get("field_specs"), dict) else None,
    )

    gt_raw = _restore_nulls(meta.get("ground_truth"))
    ground_truth = None
    if gt_raw is not None and not _is_null_gt(gt_raw):
        if isinstance(gt_raw, dict) and "value" in gt_raw:
            ground_truth = _schema_init(
                GroundTruth,
                value=gt_raw.get("value"),
                notes=gt_raw.get("notes"),
                status=gt_raw.get("status"),
                type=gt_raw.get("type"),
                artifact=gt_raw.get("artifact"),
            )
        else:
            ground_truth = _schema_init(GroundTruth, value=gt_raw)

    sc = _restore_nulls(meta.get("scorer")) or {}
    scorer_config = dict(sc)
    if isinstance(meta.get("scorer_config"), dict):
        scorer_config.update(_restore_nulls(meta["scorer_config"]))
    scorer = _schema_init(
        ScorerSpec,
        primary=str(sc.get("primary", "answer_score")),
        secondary=tuple(sc.get("secondary", []) or []),
        checks=tuple(sc.get("checks", []) or []),
        aggregation=sc.get("aggregation"),
        tolerance=sc.get("tolerance") if isinstance(sc.get("tolerance"), dict) else None,
        expected_fields=tuple(sc.get("expected_fields", []) or []),
        axis_order=tuple(sc.get("axis_order", []) or []),
        config=scorer_config,
    )

    provenance_data = meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {}
    asset_rows = meta.get("assets") if isinstance(meta.get("assets"), list) else []

    def asset_ref(row: dict) -> AssetRef:
        return _schema_init(
            AssetRef,
            path=str(row.get("path") or row.get("asset_path", "")),
            format=str(row.get("format") or row.get("asset_format", "obj")),
            sha256=row.get("sha256"),
            asset_id=row.get("asset_id"),
            role=row.get("role"),
            viewer=row.get("viewer"),
            repo_path=row.get("repo_path"),
        )

    return _schema_init(
        TaskSpec,
        schema_version="1.0",
        task_id=task_id,
        v_class=str(meta.get("v_class", "V1")),
        question=instruction,
        asset=asset,
        prerendered_image=None,
        answer_schema=answer_schema,
        ground_truth=ground_truth,
        scorer=scorer,
        provenance=_schema_init(
            Provenance,
            source_dataset=str(meta.get("source_dataset", "harbor")),
            source_qid=meta.get("source_qid"),
            lifted_at="harbor",
            lifter_version="harbor_adapter",
            generator=provenance_data.get("generator"),
            case_id=provenance_data.get("case_id"),
            case_manifest_sha256=provenance_data.get("case_manifest_sha256"),
            case_sha256=provenance_data.get("case_sha256"),
            solver=provenance_data.get("solver"),
            input_sha256=provenance_data.get("input_sha256"),
        ),
        mechanical_contract=meta.get("mechanical_contract"),
        task_type=meta.get("task_type"),
        asset_contract=meta.get("asset_contract"),
        assets=tuple(asset_ref(row) for row in asset_rows),
        input_closure=meta.get("input_closure"),
        required_evidence=tuple(meta.get("required_evidence", []) or []),
        viewer_requirements=tuple(meta.get("viewer_requirements", []) or []),
        active_vision_evidence=meta.get("active_vision_evidence"),
        trace_requirements=meta.get("trace_requirements"),
        tool_policy={
            "allowed_tools": list(meta.get("allowed_tools", []) or []),
            "forbidden_tools": list(meta.get("forbidden_tools", []) or []),
        },
        recompute_receipt=meta.get("recompute_receipt"),
        challenge_controls=meta.get("challenge_controls"),
        asset_grounding=meta.get("asset_grounding"),
        grounding_receipt=meta.get("grounding_receipt"),
        grounding_receipts=tuple(meta.get("grounding_receipts", []) or []),
        target_selection=meta.get("target_selection"),
        entity_selection=meta.get("entity_selection"),
        render_contract=meta.get("render_contract"),
        mask_id=meta.get("mask_id"),
        asset_hashes=meta.get("asset_hashes"),
        evaluation_eligible=meta.get("evaluation_eligible"),
        evaluation_ineligible_reason=meta.get("evaluation_ineligible_reason"),
        raw_task=meta,
    )


def _is_null_gt(gt: Any) -> bool:
    """``needs_expert_annotation`` style tasks carry no usable ground truth."""
    if isinstance(gt, dict):
        v = gt.get("value", gt.get("status"))
        return v is None or v == "needs_expert_annotation"
    return gt == "needs_expert_annotation"


# ── agent artifacts -> a minimal trajectory view ───────────────────────── #
@dataclass(frozen=True)
class _TrajView:
    """The minimal duck-type the sci3d scorers read off a trajectory.

    Most scorers only touch ``final_answer``; ``tool_names`` is used by
    ``compute_process_rewards``. ``steps`` is an ordered, truncated summary of
    the run (per-step reasoning + tool calls + observations) consumed by the
    trajectory judge to grade *how* the agent reached the answer — there is no
    process ground truth, so this is a soft, rubric-guided signal.
    """

    final_answer: Any
    tool_names: list[str] = field(default_factory=list)
    task_id: str = "harbor-task"
    steps: tuple[dict, ...] = field(default_factory=tuple)
    # Full per-call records ({step, name, arguments_text, outcome, error_text}),
    # untruncated. ``steps`` is shaped for a judge prompt and capped at
    # ``_MAX_STEPS``; the capability scan and the MCP-health signal must see the
    # whole run, so they read ``calls`` instead.
    calls: tuple[dict, ...] = field(default_factory=tuple)


# Truncation budgets so the trajectory fits comfortably in a judge prompt.
_MAX_STEPS = 24
_MAX_MSG_CHARS = 600
_MAX_OBS_CHARS = 800


def answer_json_to_trajview(
    answer_path: Path, traj_path: Path, task_id: str
) -> _TrajView:
    """Read the agent's answer + ATIF trajectory into a ``_TrajView``.

    ``answer_path`` holds ``{"answer": ...}`` (written by Sci3dAgent).
    ``traj_path`` holds a Harbor Trajectory JSON; we extract the ordered
    tool-call names plus a truncated per-step summary.

    Dual-source answer: when ``sci3d_answer.json`` is absent (e.g. a generic
    harness like the Claude Code CLI agent, which writes only the standard
    ``trajectory.json``), fall back to the last agent message in the trajectory
    as the final answer. Both files being absent/corrupt degrades to an empty
    view rather than raising.
    """
    final_answer = None
    try:
        data = json.loads(Path(answer_path).read_text())
        final_answer = data.get("answer") if isinstance(data, dict) else data
    except (OSError, json.JSONDecodeError):
        final_answer = None

    atif_calls = parse_calls_from_path(traj_path)
    # Codex keeps function calls in a session JSONL on some Harbor versions.
    # Read it alongside ATIF rather than choosing one source based on a filename:
    # a partially materialized ATIF must not make a real MCP call disappear.
    calls = merge_call_records(atif_calls, parse_codex_session_calls(traj_path.parent))
    tool_names = [str(call.get("name")) for call in calls if call.get("name")]
    # Preserve the legacy name-only extraction if a malformed observation made
    # the richer ATIF parser return no calls at all.
    if not tool_names:
        tool_names = _parse_atif_tool_names(traj_path)
    steps = _parse_atif_steps(traj_path)
    if final_answer is None:
        # no sci3d answer file -> take the final agent message from the traj
        final_answer = _final_message_from_traj(traj_path)
    return _TrajView(
        final_answer=final_answer,
        tool_names=tool_names,
        task_id=task_id,
        steps=steps,
        calls=calls,
    )


def _parse_atif_steps(traj_path: Path) -> tuple[dict, ...]:
    """Summarize each step as {message, tools, observation} for the judge."""
    try:
        traj = json.loads(Path(traj_path).read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    out: list[dict] = []
    for step in (traj.get("steps", []) or [])[:_MAX_STEPS]:
        tools = [
            c.get("function_name")
            for c in (step.get("tool_calls", []) or [])
            if c.get("function_name")
        ]
        msg = str(step.get("message") or step.get("reasoning_content") or "")
        obs = ""
        observation = step.get("observation")
        if isinstance(observation, dict):
            results = observation.get("results") or []
            obs = " ".join(str(r.get("content", "")) for r in results if isinstance(r, dict))
        out.append(
            {
                "step": step.get("step_id"),
                "message": msg[:_MAX_MSG_CHARS],
                "tools": tools,
                "observation": obs[:_MAX_OBS_CHARS],
            }
        )
    return tuple(out)


def _final_message_from_traj(traj_path: Path) -> str | None:
    """Last agent/assistant message text in a harbor trajectory, or None.

    Used as the answer when no sci3d_answer.json exists (generic harnesses).
    Scans steps in reverse for the last non-empty agent-authored message.
    """
    try:
        traj = json.loads(Path(traj_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for step in reversed(traj.get("steps", []) or []):
        src = str(step.get("source", "")).lower()
        if src and src not in ("agent", "assistant", "model"):
            continue
        msg = step.get("message") or step.get("reasoning_content")
        if msg and str(msg).strip():
            return str(msg).strip()
    return None


def _parse_atif_tool_names(traj_path: Path) -> list[str]:
    try:
        traj = json.loads(Path(traj_path).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    names: list[str] = []
    for step in traj.get("steps", []) or []:
        for call in step.get("tool_calls", []) or []:
            fn = call.get("function_name")
            if fn:
                names.append(fn)
    return names


# ── process rewards (closed-loop signals from the tool sequence) ────────── #
# Tool-name predicates mirror scripts/eval/walkingskeleton/run_verify.py's
# compute_metrics, but depend only on the tool-call sequence (no image disk
# probe) so they run in a pure host-unit context.
_OPEN_TOOLS = {"sv3d_open_scene", "sv_open_scene", "open_scene"}
_RENDER_TOOLS = {
    "sv3d_render", "sv_render", "render",
    # NIfTI/medical inspection tools also produce rendered evidence views.
    "sv_render_nifti_overlay", "sv3d_render_nifti", "sv_snapshot",
}
_METADATA_TOOLS = {
    "sv_inspect_molecule", "sv3d_get_state", "sv_get_metadata", "sv3d_metadata",
}
_FINISH_TOOLS = {"finish"}

# Generic harness tools (Claude Code CLI, Codex, …) used for navigation, not
# scientific inspection. A sci3d ``allowed_tools`` whitelist enumerates only the
# sci3d/MCP tools; it never lists Bash/Read/etc., so without this exemption every
# generic-agent run trips the tool-policy check. These are always allowed (like
# ``finish``); the policy still enforces the sci3d ``forbidden_tools`` and, when
# an allow-list is set, that sci3d tool calls stay inside it.
#
# Exempting them from the *name* check is correct and stays. What it cannot do
# is decide whether the forbidden *capability* was used, because a capability
# such as ``nifti_centroid_api`` lives inside a Bash command string rather than
# in a tool name -- that is ``_capability_policy``'s job, which scans the
# arguments of exactly these tools.
#
# Both names are defined in ``_tool_names`` now and re-bound here under their
# historical private spellings so existing importers keep working.
_GENERIC_HARNESS_TOOLS = GENERIC_HARNESS_TOOLS
_normalize_tool_name = normalize_tool_name


def compute_process_rewards(
    tool_names: list[str],
    allowed: list[str] | None,
    forbidden: list[str] | None,
) -> dict[str, float]:
    """Closed-loop process scores in [0,1], one key per signal.

    Keys (all ``process_``-prefixed): opened_scene, rendered, tool_policy,
    finished, metadata_only. ``metadata_only`` is a *shortcut* probe: 1.0 when
    the agent used only metadata tools and never opened/rendered — i.e. higher
    is *worse*; downstream weighting should treat it as a penalty if desired.
    """
    names = [_normalize_tool_name(n) for n in (tool_names or [])]
    allowed_set = {_normalize_tool_name(n) for n in (allowed or [])}
    forbidden_set = {_normalize_tool_name(n) for n in (forbidden or [])}

    opened = any(n in _OPEN_TOOLS for n in names)
    rendered = any(n in _RENDER_TOOLS for n in names)
    finished = any(n in _FINISH_TOOLS for n in names)

    used_metadata = any(n in _METADATA_TOOLS for n in names)
    metadata_only = bool(used_metadata and not opened and not rendered)

    # policy: no forbidden calls, and (if an allow-list is given) no calls
    # outside it. ``finish`` and generic harness tools (Bash/Read/…) are always
    # allowed — a sci3d allow-list only enumerates sci3d/MCP tools.
    _always_ok = _FINISH_TOOLS | _GENERIC_HARNESS_TOOLS
    out_of_policy = [
        n for n in names
        if n in forbidden_set
        or (allowed_set and n not in allowed_set and n not in _always_ok)
    ]
    policy_ok = len(out_of_policy) == 0

    return {
        f"{PROCESS_PREFIX}opened_scene": 1.0 if opened else 0.0,
        f"{PROCESS_PREFIX}rendered": 1.0 if rendered else 0.0,
        f"{PROCESS_PREFIX}tool_policy": 1.0 if policy_ok else 0.0,
        f"{PROCESS_PREFIX}finished": 1.0 if finished else 0.0,
        f"{PROCESS_PREFIX}metadata_only": 1.0 if metadata_only else 0.0,
    }


# ── weighted total ─────────────────────────────────────────────────────── #
def weighted_total(
    rewards: dict[str, float],
    weights: dict[str, float] | None,
    *,
    process_prefix: str = PROCESS_PREFIX,
) -> float:
    """Return the deterministic answer score used by the leaderboard.

    Process signals and optional judge scores remain diagnostics and never
    change the formal reward, including when legacy metadata carries weights.
    """
    del weights, process_prefix
    answer = float(rewards.get("answer_score", 0.0))
    return max(0.0, min(1.0, answer))
